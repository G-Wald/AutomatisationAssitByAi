"""Mode équipe : plusieurs fausses fenêtres (une par personnage) qui rejouent des captures et notent leurs actions
dans un journal commun, pour vérifier l'ordre des actions d'une fenêtre à l'autre."""

import logging
import math
import time
from contextlib import contextmanager

import cv2
import numpy as np
import pytest

from bot.brain.popups import PopupCloser
from bot.brain.travel import BLOCKED, Traveler, TravelSettings, World
from bot.capture.window import GameWindow, character_name
from bot.config import load_settings
from bot.perception.colors import RED, orange_mask
from bot.perception.fighters import Fighter
from bot.perception.frame import Frame
from bot.perception.join import find_fight_swords, find_join_entry, sword_orange
from bot.perception.layout import Layout
from bot.perception.maplabel import read_map_label, text_glyphs, unreadable_label
from bot.perception.popups import load_popup_specs
from bot.perception.timeline import current_portrait, same_portrait
from bot.perception.ui import RING_CENTER, RING_RADII, RING_SPLIT_Y
from bot.state.scene import Phase, read_scene
from bot.team.join import Joiner
from bot.team.loop import REPLAY_GUARD, Crossing, TeamBot
from bot.actions.input import Human
from bot.team.member import Focus, Member, MemberTag, TaggedRecorder, make_member, to_reference
from bot.team.roster import TeamSettings, pick_team
from bot.team.turns import TurnRouter
from conftest import FIXTURES, load_frame, with_flash_menu

SWORD_CROP = cv2.imread(str(FIXTURES / "rejoindre_epee.png"))
MENU_CROP = cv2.imread(str(FIXTURES / "rejoindre_menu.png"))


# Faux objets


class Screen:
    """Fenêtre au premier plan (partagée par les fausses fenêtres) et journal des actions de toute l'équipe."""

    def __init__(self) -> None:
        self.front = None
        self.events: list[tuple] = []


class FakeWindow:
    def __init__(self, screen: Screen, name: str) -> None:
        self.screen = screen
        self.title = f"{name} - Dofus Retro v1.49.5"
        self.hwnd = id(self)

    def is_foreground(self) -> bool:
        return self.screen.front is self

    def bring_to_front(self) -> None:
        self.screen.front = self
        self.screen.events.append((self.title.split(" - ")[0], "devant"))


class FakeRecorder:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def keeps(self, lossless=False):
        return True

    def save(self, image, label, lossless=False, quality=80):
        self.saved.append(label)
        return f"<{label}>"


class FakeMemberGame:
    """Comme FakeGame (test_brain.py) : rejoue les captures dans l'ordre, la dernière se répète. Comme MemberGame :
    passe sa fenêtre devant avant chaque regard ou geste. `after_press` : captures qui suivent l'appui d'une touche."""

    def __init__(self, name: str, screen: Screen, *frames: Frame) -> None:
        self.name = name
        self.screen = screen
        self.window = FakeWindow(screen, name)
        self.frames = list(frames)
        self.recorder = FakeRecorder()
        self.after_press: dict[str, list[Frame]] = {}
        self.last_map_click = None

    def _front(self) -> None:
        if not self.window.is_foreground():
            self.window.bring_to_front()

    def look(self, park=None):
        self._front()
        self.screen.events.append((self.name, "look"))
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]

    def sleep(self, seconds):
        pass

    def wait_until(self, predicate, timeout, poll=0.1, park=None):
        for _ in range(3):  # quelques regards, comme Game.wait_until le temps de `timeout`
            frame = self.look(park)
            if predicate(frame):
                return frame
        return None

    def move(self, point, spread=(0, 0)):
        self._front()
        return round(point[0]), round(point[1])

    def click(self, point=None, spread=(0, 0), button="left"):
        self._front()
        kind = "click" if button == "left" else f"click_{button}"
        self.screen.events.append((self.name, kind, None if point is None else (round(point[0]), round(point[1]))))
        return True

    def press(self, key):
        self._front()
        self.screen.events.append((self.name, "press", key))
        if key in self.after_press:
            self.frames = self.after_press.pop(key)

    @contextmanager
    def holding(self, key):
        self._front()
        self.screen.events.append((self.name, "hold", key))
        yield


class FakeAI:
    """IA de combat : note ses tours ; `on_turn` peut changer ce que montrent les fenêtres après le tour."""

    def __init__(self, game: FakeMemberGame, place_team=RED) -> None:
        self.game = game
        self.place_team = place_team
        self.turns: list[dict] = []
        self.on_turn = None
        self._me_cell = None

    def new_combat(self):
        pass

    def ensure_tactical_mode(self, frame):
        pass

    def wanted_range(self):
        return (1, 10)

    def place(self, frame, my_team, last_try=True):
        self.game.screen.events.append((self.game.name, "placement"))
        return self.place_team

    def play_turn(self, my_team, is_mine=None, me_cell=None, find_me=None):
        self.game.screen.events.append((self.game.name, "tour"))
        self.turns.append({"team": my_team, "is_mine": is_mine, "me_cell": me_cell, "find_me": find_me})
        if self.on_turn:
            self.on_turn()


class OldAI(FakeAI):
    """IA d'avant le mode équipe : play_turn(my_team) seulement."""

    def play_turn(self, my_team):
        self.turns.append({"team": my_team})


class DoneBook:
    read_done = True


def member(name: str, screen: Screen, *frames: Frame, ai=FakeAI) -> Member:
    game = FakeMemberGame(name, screen, *frames)
    return Member(name, game, DoneBook(), ai(game), PopupCloser(game, load_popup_specs(), "escape", 20))


def team_bot(*members: Member, **options) -> TeamBot:
    bot = TeamBot(list(members), load_settings(), TeamSettings(enabled=True), **options)
    return bot


def events(screen: Screen, kind: str) -> list[tuple]:
    return [e for e in screen.events if e[1] == kind]


def with_sword(name: str, at=(900, 300)) -> Frame:
    """Capture avec l'épée de combat collée en `at` : seulement les pixels de l'épée (l'herbe de la découpe est
    laissée de côté), pour qu'elle soit vue sur le décor de la capture."""
    image = load_frame(name).image.copy()
    crop = SWORD_CROP.astype(np.int16)
    keep = ~((crop[..., 1] >= crop[..., 2] - 10) & (crop[..., 1] > crop[..., 0] + 50))
    x, y = at
    image[y : y + SWORD_CROP.shape[0], x : x + SWORD_CROP.shape[1]][keep] = SWORD_CROP[keep]
    return Frame.of(image)


def with_join_menu(frame: Frame, at=(950, 330)) -> Frame:
    """Capture avec le menu « Combat / Rejoindre » (découpe de l'utilisateur, infobulle comprise) collé en `at`."""
    image = frame.image.copy()
    x, y = at
    image[y : y + MENU_CROP.shape[0], x : x + MENU_CROP.shape[1]] = MENU_CROP
    return Frame.of(image)


def without_timer(frame: Frame) -> Frame:
    """Même capture, sans le timer de tour autour de la minimap : le tour d'un allié, vu d'une autre fenêtre."""
    image = frame.image.copy()
    (cx, cy), r = RING_CENTER, RING_RADII[1]
    x0, y0, x1, y1 = frame.layout.box(cx - r, cy - r, cx + r, RING_SPLIT_Y)
    ring = image[y0:y1, x0:x1]
    ring[orange_mask(ring)] = (90, 90, 90)
    return Frame.of(image)


def scaled(frame: Frame, factor: float) -> Frame:
    """La même capture dans une fenêtre plus petite (ou plus grande)."""
    return Frame.of(cv2.resize(frame.image, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA))


# Fenêtres et réglages


def test_character_name_from_the_window_title():
    title = "Jesuisleking - Dofus Retro v1.49.5"
    assert character_name(title, "- Dofus Retro") == "Jesuisleking"
    assert character_name(title, "Dofus Retro") == "Jesuisleking"
    assert character_name("Dofus Retro v1.49.5", "Dofus Retro") == ""  # écran de connexion


def test_team_chosen_among_the_game_windows():
    windows = [GameWindow(1, "Zeta - Dofus Retro v1.49.5"), GameWindow(2, "Jesuisleking - Dofus Retro v1.49.5"),
               GameWindow(3, "Dofus Retro v1.49.5"), GameWindow(4, "Alpha - Dofus Retro v1.49.5")]
    names = lambda team: [name for name, _ in team]  # noqa: E731
    assert names(pick_team(windows, "- Dofus Retro", TeamSettings())) == ["Alpha", "Jesuisleking", "Zeta"]
    assert names(pick_team(windows, "- Dofus Retro", TeamSettings(leader="jesuisleking"))) == ["Jesuisleking", "Alpha", "Zeta"]
    chosen = pick_team(windows, "- Dofus Retro", TeamSettings(members=("Zeta", "Jesuisleking", "Absent")))
    assert names(chosen) == ["Zeta", "Jesuisleking"] and chosen[0][1].hwnd == 1  # ordre de team.members
    with pytest.raises(ValueError, match="Inconnu"):
        pick_team(windows, "- Dofus Retro", TeamSettings(leader="Inconnu"))
    with pytest.raises(ValueError):
        pick_team([GameWindow(3, "Dofus Retro v1.49.5")], "- Dofus Retro", TeamSettings())


def test_team_settings_from_config_and_command_line():
    settings = load_settings()
    assert "team" in settings
    team = TeamSettings.from_settings(settings)
    assert not team.enabled and team.ready_key == "f1" and team.members == ()
    team = TeamSettings.from_settings({"team": {"members": "Solo"}}, enabled=True, leader=" Jesuisleking ")
    assert team.enabled and team.leader == "Jesuisleking" and team.members == ("Solo",)


class FakeControl:
    def __init__(self) -> None:
        self.slept: list[float] = []

    def sleep(self, seconds):
        self.slept.append(seconds)


def test_window_switched_only_when_needed_then_redrawn():
    screen, control = Screen(), FakeControl()
    a, b = FakeWindow(screen, "A"), FakeWindow(screen, "B")
    focus = Focus(control, delay=0.35)
    focus.names = {a.hwnd: "A", b.hwnd: "B"}
    focus.to(a)
    focus.to(a)  # déjà devant : rien
    focus.to(b)
    assert screen.events == [("A", "devant"), ("B", "devant")]
    assert control.slept == [0.35, 0.35]  # le temps que le jeu se redessine, après chaque changement
    assert focus.current_name == "B"
    Focus(control, passive=True).to(a)  # observation : jamais de changement de fenêtre
    assert screen.front is b


def test_same_reference_point_in_windows_of_any_size():
    for layout in (Layout(1918, 1008), Layout(1600, 900), Layout(2560, 1400)):
        point = layout.point(1240.0, 534.0)
        x, y = to_reference(layout, point)
        assert abs(x - 1240) <= 1 and abs(y - 534) <= 1


def test_member_window_brought_forward_before_acting(monkeypatch):
    monkeypatch.setattr("bot.brain.game.ScreenCapture", lambda: None)  # aucune capture d'écran dans les tests
    done = []
    monkeypatch.setattr("bot.brain.game.Game.click", lambda self, point=None, spread=(0, 0), button="left":
                        done.append(("click", point)))
    monkeypatch.setattr("bot.brain.game.Game.press", lambda self, key: done.append(("press", key)))
    screen, control = Screen(), FakeControl()
    window = FakeWindow(screen, "Second")
    focus = Focus(control)
    second = make_member("Second", window, load_settings(), focus, control, FakeRecorder(), Human(), act=True)
    second.game._layout = Layout(1600, 900)  # dernière capture : une fenêtre plus petite
    second.game.click((900, 400))
    second.game.press("f1")
    assert screen.events == [("Second", "devant")]  # une fois : ensuite elle est déjà devant
    assert done == [("click", (900, 400)), ("press", "f1")]
    (x, y), _ = second.game.last_map_click  # pour l'épée : en repère de référence
    assert Layout(1600, 900).point(x, y) == (900, 400)
    assert second.game.recorder.name == "Second" and focus.current_name == "Second"
    assert second.ai.spellbook is second.spellbook and second.popups.game is second.game


def test_logs_and_captures_tagged_with_the_character():
    recorder = FakeRecorder()
    TaggedRecorder(recorder, "Jesuisleking").save(None, "tour")
    assert recorder.saved == ["Jesuisleking_tour"]
    record = logging.LogRecord("bot", logging.INFO, __file__, 1, "Mon tour", None, None)
    MemberTag(lambda: "Jesuisleking").filter(record)
    assert record.member == "[Jesuisleking] "
    MemberTag(lambda: "").filter(record)
    assert record.member == ""


# Rejoindre un combat


@pytest.mark.parametrize("name", ["hors_combat", "hors_combat_foret", "hors_combat_prairie", "groupes_z_chevauchement"])
def test_fight_sword_found_on_any_background(name):
    (sword,) = find_fight_swords(with_sword(name, at=(900, 300)))
    # Garde de l'épée : (82, 80) dans la découpe de l'utilisateur.
    assert abs(sword.click[0] - 982) <= 2 and abs(sword.click[1] - 380) <= 2
    assert sword.score >= 0.95


def test_fight_sword_found_in_jpeg_and_smaller_windows():
    frame = with_sword("hors_combat", at=(900, 300))
    ok, data = cv2.imencode(".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    (sword,) = find_fight_swords(Frame.of(cv2.imdecode(data, cv2.IMREAD_COLOR)))
    assert abs(sword.click[0] - 982) <= 2 and abs(sword.click[1] - 380) <= 2
    (sword,) = find_fight_swords(scaled(frame, 0.8))
    assert abs(sword.click[0] - 982 * 0.8) <= 3 and abs(sword.click[1] - 380 * 0.8) <= 3


@pytest.mark.parametrize("name", ["hors_combat", "hors_combat_foret", "hors_combat_prairie_pierres", "preparation",
                                  "hors_combat_detection_enemi", "groupes_z_chevauchement_2", "fin_de_combat",
                                  "hors_combat_menu_personnage", "combat_mon_tour"])
def test_no_fight_sword_without_a_fight(name):
    assert find_fight_swords(load_frame(name)) == []


def test_join_entry_found_only_in_the_join_menu():
    frame = with_join_menu(load_frame("hors_combat"), at=(950, 330))
    x, y = find_join_entry(frame)
    assert abs(x - (950 + 162)) <= 2 and abs(y - (330 + 139)) <= 2  # « Rejoindre » : (162, 139) dans la découpe
    attack_menu = load_frame("hors_combat").image.copy()
    menu = cv2.imread(str(FIXTURES / "click_lancer_le_combat.png"))
    attack_menu[460 : 460 + menu.shape[0], 1170 : 1170 + menu.shape[1]] = menu
    assert find_join_entry(Frame.of(attack_menu)) is None  # menu « Monstres / Attaquer »
    assert find_join_entry(load_frame("hors_combat_menu_personnage")) is None


def test_follower_joins_through_the_sword_and_its_menu():
    screen = Screen()
    sword = with_sword("hors_combat", at=(900, 300))
    follower = member("Second", screen, sword, with_join_menu(sword, at=(950, 330)), load_frame("preparation"))
    assert Joiner(timeout=5).join(follower, hint=None)
    (sword_click,), (join_click,) = [e[2] for e in events(screen, "click_right")], [e[2] for e in events(screen, "click")]
    # Pommeau de l'épée (au-dessus des têtes : 01/10, un PNJ debout sur l'épée cachait sa garde).
    assert abs(sword_click[0] - 980) <= 2 and abs(sword_click[1] - 344) <= 2
    assert abs(join_click[0] - 1112) <= 2 and abs(join_click[1] - 469) <= 2  # « Rejoindre »


def test_next_followers_click_the_same_sword_at_once_and_are_checked_after(monkeypatch):
    monkeypatch.setattr("bot.team.join.QUICK_SAME_SPOT", 1.0)  # le point qui a marché (sinon : autre partie haute)
    # 15/10, équipe de 8 : 0,7 s par suiveur entre deux clics sur l'épée (clic, attente, regard). Le premier la trouve ;
    # les suivants cliquent au même endroit sans attendre, leur entrée est vérifiée ensuite.
    screen = Screen()
    sword = with_sword("hors_combat", at=(900, 300))
    first = member("Second", screen, sword, load_frame("preparation"))
    second = member("Troisieme", screen, sword, load_frame("preparation"))
    joiner = Joiner(timeout=5)
    assert joiner.ask(first, None) and joiner.ask(second, None)
    assert joiner.wait_placement(first, time.monotonic() + 5) and joiner.wait_placement(second, time.monotonic() + 5)
    mine = [e for e in screen.events if e[0] == "Troisieme" and e[1] != "devant"]
    assert [e[1] for e in mine] == ["look", "click_right", "look"]  # clic aussitôt, puis la vérification
    a, b = (e[2] for e in events(screen, "click_right"))
    assert abs(a[0] - b[0]) <= 4 and abs(a[1] - b[1]) <= 4  # même endroit de l'épée


def test_follower_not_in_after_the_quick_click_joins_the_long_way(monkeypatch):
    monkeypatch.setattr("bot.team.join.QUICK_JOIN_WAIT", 0.0)
    screen = Screen()
    sword = with_sword("hors_combat", at=(900, 300))
    first = member("Second", screen, sword, load_frame("preparation"))
    second = member("Troisieme", screen, sword, sword, sword, load_frame("preparation"))
    joiner = Joiner(timeout=5)
    assert joiner.ask(first, None) and joiner.ask(second, None)
    assert not joiner.wait_placement(second, time.monotonic() + 5)  # pas entré dans le délai du clic rapide
    assert joiner.join(second, None)  # refait tout : clic, attente et regard
    assert len([e for e in events(screen, "click_right") if e[0] == "Troisieme"]) == 2


def test_sword_nearest_to_the_leader_click_tried_first():
    screen = Screen()
    image = with_sword("hors_combat", at=(700, 200)).image
    two = with_sword("hors_combat", at=(1300, 400)).image.copy()
    two[200 : 200 + SWORD_CROP.shape[0], 700 : 700 + SWORD_CROP.shape[1]] = image[200 : 200 + SWORD_CROP.shape[0],
                                                                                  700 : 700 + SWORD_CROP.shape[1]]
    frame = Frame.of(two)
    assert len(find_fight_swords(frame)) == 2
    follower = member("Second", screen, frame, with_join_menu(frame, at=(1350, 430)), load_frame("preparation"))
    hint = to_reference(frame.layout, (1390, 520))  # le principal a cliqué près du groupe, en bas à droite
    assert Joiner(timeout=5).join(follower, hint)
    first_click = events(screen, "click_right")[0][2]
    assert abs(first_click[0] - 1380) <= 2 and abs(first_click[1] - 444) <= 2  # pommeau de l'épée la plus proche


def test_sword_with_its_handle_hidden_by_a_monster():
    # 11/10 : un monstre debout devant l'épée en cache la poignée ; icône et pommeau au même endroit suffisent.
    image = np.zeros((1009, 1920, 3), np.uint8)  # extrait (lignes 150-450, colonnes 1350-1750) d'une capture
    image[150:450, 1350:1750] = cv2.imread(str(FIXTURES / "epee_poignee_cachee.png"))
    (sword,) = find_fight_swords(Frame.of(image))
    assert abs(sword.center[0] - 1540) < 20 and 260 < sword.center[1] < 330


def test_sword_not_seen_follower_clicks_where_the_leader_attacked(monkeypatch):
    # 16/10, Incarnam 6,2 : groupe tout en haut, épée coupée par le bord et sous le nom de la carte ; aucun des 7 suiveurs
    # ne la voyait. L'épée remplace le groupe : clic droit là où le principal a cliqué.
    monkeypatch.setattr("bot.team.join.SWORD_WAIT", 0)
    screen = Screen()
    hors_combat = load_frame("hors_combat")
    follower = member("Second", screen, hors_combat, hors_combat, load_frame("preparation"))
    hint = to_reference(hors_combat.layout, (1011, 84))
    assert Joiner(timeout=5).join(follower, hint)
    ((x, y),) = [e[2] for e in events(screen, "click_right")]
    assert abs(x - 1011) <= 10 and abs(y - 84) <= 10


def test_once_a_follower_cannot_see_the_sword_the_next_ones_do_not_wait_for_it(monkeypatch):
    # 16/10 : 7 suiveurs x 2 recherches de 4 s, le principal 1 min 30 au placement avant son premier tour.
    monkeypatch.setattr("bot.team.join.SWORD_WAIT", 0)
    screen = Screen()
    joiner = Joiner(timeout=0)
    first = member("Second", screen, load_frame("hors_combat"))
    assert not joiner.join(first)
    monkeypatch.setattr("bot.team.join.SWORD_WAIT", 5.0)
    second = member("Troisieme", screen, load_frame("hors_combat"))
    start = time.monotonic()
    assert not joiner.join(second)
    assert time.monotonic() - start < 2.0  # une recherche, sans attendre l'épée 5 s ni refaire un essai
    assert second.game.recorder.saved.count("epee_introuvable") == 1


def test_join_given_up_without_a_sword(monkeypatch):
    monkeypatch.setattr("bot.team.join.SWORD_WAIT", 0)
    screen = Screen()
    follower = member("Second", screen, load_frame("hors_combat"))
    assert not Joiner(timeout=0).join(follower)
    assert events(screen, "click") == []
    assert "epee_introuvable" in follower.game.recorder.saved


# Tours de jeu


def test_portrait_tells_who_is_playing():
    cra = current_portrait(load_frame("combat_mon_tour"))
    assert cra.team == RED
    assert same_portrait(cra, current_portrait(load_frame("combat_portee_sort")))
    assert same_portrait(cra, current_portrait(load_frame("combat_infobulle")))  # autre combat, autre place
    assert not same_portrait(cra, current_portrait(load_frame("combat_cercles_colles")))  # avec un chapeau
    assert not same_portrait(cra, current_portrait(load_frame("combat_tour_adversaire")))  # Bouftou
    assert current_portrait(load_frame("hors_combat")) is None


def test_portrait_still_mine_after_the_timeline_slides():
    """11/10 : Brawli tue un ennemi, la timeline glisse d'une place, son cadre est trouvé un pixel à côté du dessin :
    pris pour un autre portrait, son tour a été recommencé en plein milieu."""
    def timeline(name):  # bande de la timeline (lignes 690-790, colonnes 1450-1920) dans une capture de 1920 x 1009
        image = np.zeros((1009, 1920, 3), np.uint8)
        image[690:790, 1450:1920] = cv2.imread(str(FIXTURES / f"{name}.png"))
        return Frame.of(image)

    before = current_portrait(timeline("timeline_avant_ennemi_tue"))
    after = current_portrait(timeline("timeline_apres_ennemi_tue"))
    assert after.box[0] > before.box[0]  # une place plus à droite
    assert same_portrait(before, after)
    assert not same_portrait(before, current_portrait(load_frame("combat_mon_tour")))


def test_turn_router_learns_portraits_and_order():
    cra = current_portrait(load_frame("combat_mon_tour"))
    hat = current_portrait(load_frame("combat_cercles_colles"))
    router = TurnRouter(["A", "B", "C"])
    assert router.candidates(cra, ["A", "B", "C"]) == ["A", "B", "C"]
    router.learn("B", cra)
    router.learn("C", hat)
    assert router.owner(cra) == "B" and router.owner(hat) == "C"
    assert router.candidates(None, ["A", "B", "C"]) == ["B", "A", "C"]  # après C : B (ordre appris), puis l'inconnu A
    router.learn_other(cra)
    assert router.is_other(cra)
    router.new_fight()
    assert not router.is_other(cra) and router.owner(cra) == "B"  # portraits gardés d'un combat à l'autre


def test_ally_turn_found_by_its_timer_then_directly_by_its_portrait(monkeypatch):
    monkeypatch.setattr("bot.team.loop.TIMER_FILL", 0)
    screen = Screen()
    seen_from_a = without_timer(load_frame("combat_mon_tour"))  # tour de B vu dans la fenêtre de A
    a = member("A", screen, seen_from_a)
    b = member("B", screen, load_frame("combat_mon_tour"))  # la fenêtre de B : timer de son tour
    c = member("C", screen, without_timer(load_frame("combat_mon_tour")))
    bot = team_bot(a, b, c)
    bot.my_team = RED
    scene = read_scene(seen_from_a)
    assert scene.current_team == RED and not scene.timer_visible
    bot.router.order = ["A", "B"]
    bot.router.last = "A"  # B est le suivant dans l'ordre appris : sa fenêtre regardée en premier
    assert bot._player(a, seen_from_a, scene, [a, b, c]) is b
    assert [e[0] for e in events(screen, "look")] == ["B"]
    screen.events.clear()
    assert bot._player(a, seen_from_a, scene, [a, b, c]) is b  # portrait retenu : plus besoin de chercher
    assert screen.events == []


def test_monster_turn_watched_without_switching_windows():
    screen = Screen()
    enemy = load_frame("combat_tour_adversaire")
    a, b = member("A", screen, enemy), member("B", screen, enemy)
    bot = team_bot(a, b)
    bot.my_team = RED
    assert bot._player(a, enemy, read_scene(enemy), [a, b]) is None
    assert screen.events == []


def test_ally_that_is_none_of_my_characters_is_let_through(monkeypatch):
    monkeypatch.setattr("bot.team.loop.TIMER_FILL", 0)
    screen = Screen()
    ally = without_timer(load_frame("combat_mon_tour"))
    a, b = member("A", screen, ally), member("B", screen, ally)  # aucune fenêtre n'affiche le timer
    bot = team_bot(a, b)
    bot.my_team = RED
    scene = read_scene(ally)
    assert bot._player(a, ally, scene, [a, b]) is None
    assert bot._player(a, ally, scene, [a, b]) is None  # trop tôt pour chercher de nouveau
    portrait, tries, last, first = bot._search
    bot._search = (portrait, tries, last - 0.9, first - 0.9)  # 0,9 s plus tard : le timer peut encore venir
    assert bot._player(a, ally, scene, [a, b]) is None
    assert not bot.router.is_other(current_portrait(ally))  # pas encore conclu (le timer se remplit en ~1 s)
    portrait, tries, last, first = bot._search
    bot._search = (portrait, tries, last - 5, first - 5)
    assert bot._player(a, ally, scene, [a, b]) is None  # toujours rien, 5 s après : invocation alliée
    assert bot.router.is_other(current_portrait(ally))
    screen.events.clear()
    assert bot._player(a, ally, scene, [a, b]) is None and screen.events == []


def test_fight_played_in_the_right_windows_until_its_end(monkeypatch):
    monkeypatch.setattr("bot.team.loop.TIMER_FILL", 0)
    monkeypatch.setattr("bot.team.loop.FIGHT_END_CONFIRM", 0)
    screen = Screen()
    my_turn, enemy, end = load_frame("combat_mon_tour"), load_frame("combat_tour_adversaire"), load_frame("hors_combat")
    a = member("A", screen, without_timer(my_turn))  # A regarde : c'est au tour de B
    b = member("B", screen, my_turn)
    bot = team_bot(a, b)
    bot.my_team = RED

    def b_played():  # après le tour de B : tour du monstre, puis fin du combat, dans les deux fenêtres
        b.game.frames = [enemy, end]
        a.game.frames = [end]

    b.ai.on_turn = b_played
    bot._turns([a, b])
    assert [e[0] for e in events(screen, "tour")] == ["B"]
    (turn,) = b.ai.turns
    assert turn["team"] == RED
    assert turn["is_mine"](my_turn) and turn["is_mine"](without_timer(my_turn))  # son portrait, timer ou pas
    assert not turn["is_mine"](enemy)
    assert not a.ai.turns


def test_still_my_turn_with_my_timer_or_my_portrait():
    # 11/10 : portrait de Brawli plus reconnu (timeline qui glisse), tour coupé en plein milieu. Son timer suffit.
    my_turn, enemy = load_frame("combat_mon_tour"), load_frame("combat_tour_adversaire")
    screen = Screen()
    b = member("B", screen, my_turn)
    bot = team_bot(b)
    bot.my_team = RED
    bot.router.learn("B", current_portrait(load_frame("combat_cercles_colles")))  # portrait retenu : pas celui-ci
    assert bot._shows_turn(b, my_turn)  # portrait pas reconnu, mais son timer dans sa fenêtre
    bot.router.learn("B", current_portrait(my_turn))
    assert bot._shows_turn(b, without_timer(my_turn))  # son portrait, timer pas vu (fin de décompte)
    b.game.frames = [enemy]
    assert not bot._shows_turn(b, enemy)  # ni l'un ni l'autre, deux captures de suite : son tour est fini
    b.game.frames = [my_turn]
    assert bot._shows_turn(b, enemy)  # ni l'un ni l'autre un instant, son timer sur la capture suivante
    assert not bot._shows_turn(b, load_frame("hors_combat"))  # combat fini : pas de seconde capture


def test_turn_options_for_the_combat_engine():
    screen = Screen()
    frame = load_frame("combat_mon_tour")
    a, b, c = member("A", screen, frame), member("B", screen, frame), member("C", screen, frame)
    old = member("Ancien", screen, frame, ai=OldAI)
    bot = team_bot(a, b, c, old)
    bot._cells = {"A": (10, 10), "B": (14, 14)}
    b.ai.on_turn = lambda: setattr(b.ai, "_me_cell", (16, 14))  # B s'est déplacé pendant son tour
    scene = read_scene(frame)
    bot._play(a, scene)
    assert a.ai.turns[0]["me_cell"] == (10, 10)
    find_a = a.ai.turns[0]["find_me"]
    # 03/10 : le corps de B, debout devant A, cache le cercle de A : jamais celui de B pris pour le sien.
    # Pas de cercle sur sa case suivie : TeamBot ne devine pas ; l'IA le repère par la portée d'un sort (une fois par
    # tour), puis se fie à sa case suivie (cercle caché).
    assert find_a(frame, [Fighter(RED, 0, 0, (14, 14))]) is None
    bot._play(b, scene)
    assert bot._cells["B"] == (16, 14)  # suivie d'un tour à l'autre
    bot._play(c, scene)  # case inconnue (premier tour, pas de placement choisi)
    find_me = c.ai.turns[0]["find_me"]
    mine = [Fighter(RED, 0, 0, (10, 10)), Fighter(RED, 0, 0, (16, 14)), Fighter(RED, 0, 0, (20, 20))]
    # Même un seul cercle restant n'est pas sûr (le mien peut être caché) : la portée d'un sort tranchera (03/10).
    assert find_me(frame, mine) is None
    bot._play(old, scene)  # IA sans les options du mode équipe : appelée comme en solo
    assert old.ai.turns == [{"team": RED}]


# Placement


class Board:
    """Le jeu simulé au placement, lu par read_placement : cases de mon équipe (rouges), ennemis (bleus), case de
    chaque personnage. Un clic dans la fenêtre d'un personnage l'y déplace si la case est libre (comme le jeu), sauf
    les cases de `refuse`."""

    def __init__(self, screen: Screen, frame: Frame, cells: set, at: dict, enemies: set, refuse=()) -> None:
        self.screen, self.frame, self.cells, self.at, self.enemies = screen, frame, cells, at, enemies
        self.refuse = set(refuse)
        self.seen = 0

    def read(self, _frame):
        from bot.perception.colors import BLUE
        from bot.perception.placement import Placement

        for event in self.screen.events[self.seen:]:
            if event[1] == "click":
                cell = self.frame.grid.cell_at(*event[2])
                if cell in self.cells - set(self.at.values()) - self.refuse:
                    self.at[event[0]] = cell
        self.seen = len(self.screen.events)
        occupied = set(self.at.values())
        return Placement({RED: self.cells - occupied, BLUE: set()}, {RED: occupied, BLUE: set(self.enemies)})


# Les 11 cases rouges de la capture « preparation », un ennemi sur une case bleue ; l'équipe du 16/10 dans l'ordre de
# jeu vu aux 18 combats (Crâs : Flèche Magique 1 à 7 ; Enutrofs : Lancer de Pièces 0 à 8 ; Sadida : Ronce 1 à 6).
RED_CELLS = {(5, 13), (7, 11), (7, 13), (7, 15), (8, 8), (8, 18), (9, 5), (9, 13), (9, 21), (11, 13), (13, 13)}
TEAM_8 = [("Vertdepomme", (1, 7)), ("Dinarpepe", (0, 8)), ("Leproduitlatte", (1, 7)), ("Pesopepe", (0, 8)),
          ("Pombro", (1, 7)), ("Vieenjaune", (1, 7)), ("Lestunes", (0, 8)), ("Koalaloupour", (1, 6))]


def team_of_8(monkeypatch, seed=0, refuse=()):
    import random

    monkeypatch.setattr("bot.team.loop.PLACEMENT_FIRST_TRY", 0)
    monkeypatch.setattr("bot.team.loop.entry_banner", lambda f: False)
    screen = Screen()
    frame = load_frame("preparation")
    members = {}
    for name, spell_range in TEAM_8:
        members[name] = member(name, screen, frame)
        members[name].ai.wanted_range = lambda r=spell_range: r
    host = members["Koalaloupour"]
    others = [m for n, m in members.items() if m is not host]
    bot = team_bot(host, *others)
    bot.team = TeamSettings(enabled=True, place=True)
    at = dict(zip(members, random.Random(seed).sample(sorted(RED_CELLS), len(members))))
    board = Board(screen, frame, set(RED_CELLS), at, {(20, 20)}, refuse)
    monkeypatch.setattr("bot.team.loop.read_placement", board.read)
    return screen, bot, host, members, board


@pytest.mark.parametrize("seed", range(4))
def test_whole_team_placed_in_a_few_passes_each_known_on_its_true_cell(monkeypatch, caplog, seed):
    screen, bot, host, members, board = team_of_8(monkeypatch, seed)
    bot.router.known_order = [name for name, _ in TEAM_8]  # vu aux combats précédents
    start = dict(board.at)
    with caplog.at_level(logging.INFO):
        ready = bot._seat_team(host, list(members.values()))
    assert bot.my_team == RED  # 8 cases occupées contre 1 : sans bouger personne
    passes = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Placement, passe")]
    assert 1 <= len(passes) <= 3
    assert "ordre de jeu Vertdepomme, Dinarpepe" in caplog.text and "vu aux combats précédents" in caplog.text
    moved = {n for n in members if board.at[n] != start[n]}
    assert moved and moved <= set(bot._cells)  # celui qui a bougé sait où il est
    assert all(board.at[name] == cell for name, cell in bot._cells.items())  # et c'est vrai
    assert len(set(board.at.values())) == 8
    assert len(events(screen, "click")) >= len(moved)  # un détour peut faire bouger quelqu'un deux fois
    assert "Koalaloupour" not in ready and ready <= set(bot._cells)  # Prêt seulement ceux qui ne bougeront plus
    assert sorted(e[0] for e in events(screen, "press")) == sorted(ready)  # une fois chacun


def test_ready_pressed_once_in_every_window_the_host_last_after_the_placement(monkeypatch):
    screen, bot, host, members, board = team_of_8(monkeypatch, seed=1)
    host.game.after_press["f1"] = [load_frame("combat_tour_adversaire")]  # le dernier prêt : le combat commence
    joined = []
    bot.joiner.ask = lambda m, hint: joined.append(m.name) or True  # tous rejoignent d'abord, sans s'attendre
    bot.joiner.wait_placement = lambda m, deadline: True
    assert bot._placement(host) == [host] + [m for m in members.values() if m is not host]
    assert len(joined) == 7
    presses = [e[0] for e in events(screen, "press")]
    assert sorted(presses) == sorted(members) and presses[-1] == "Koalaloupour"
    first_click = next(i for i, e in enumerate(screen.events) if e[1] == "click")
    assert screen.events.index(("Koalaloupour", "press", "f1")) > first_click


def test_cell_where_nobody_went_is_not_taken_for_a_character_cell(monkeypatch):
    screen, bot, host, members, board = team_of_8(monkeypatch, seed=2, refuse=RED_CELLS)  # le jeu ne bouge personne
    bot._seat_team(host, list(members.values()))
    assert bot._cells == {}
    clicked = [members[e[0]].game.frames[0].grid.cell_at(*e[2]) for e in events(screen, "click")]
    assert len(clicked) == len(set(clicked))  # une case refusée n'est pas réessayée


def test_team_colour_from_the_number_of_characters_on_the_placement():
    from bot.perception.colors import BLUE
    from bot.perception.placement import Placement

    bot = team_bot(member("A", Screen(), load_frame("preparation")))
    eight = Placement({RED: {(0, 20)}, BLUE: {(20, 0)}},
                      {RED: {(0, 2 * k) for k in range(8)}, BLUE: {(20, 2), (20, 4)}})
    assert bot._team_colour(eight, 8) == RED
    two = Placement({RED: set(), BLUE: set()}, {RED: {(0, 0), (0, 4)}, BLUE: {(20, 2), (20, 6)}})
    assert bot._team_colour(two, 2) is None  # autant de monstres : il faudra bouger pour savoir


# Hors combat


def test_result_windows_closed_in_every_window_followers_first():
    screen = Screen()
    a = member("A", screen, load_frame("fin_de_combat_level_up"), load_frame("fin_de_combat"), load_frame("hors_combat"))
    b = member("B", screen, load_frame("fin_de_combat"), load_frame("hors_combat"))
    bot = team_bot(a, b, engage=False)
    assert bot._round() is None
    clicks = [(e[0], e[2]) for e in events(screen, "click")]
    assert [name for name, _ in clicks] == ["B", "A", "A"]
    (_, fermer), (_, ok), _ = clicks
    assert abs(fermer[0] - 1675) <= 3 and abs(ok[0] - 1247) <= 3  # « Fermer » chez B ; « Ok » (niveau) puis « Fermer » chez A
    assert screen.front is a.window  # le principal devant à la fin
    assert bot._tired == []


def test_round_reports_a_character_already_in_combat():
    screen = Screen()
    a = member("A", screen, load_frame("hors_combat"))
    b = member("B", screen, load_frame("preparation"))  # agressé, ou combat lancé à la main
    assert team_bot(a, b, engage=False)._round() is b


def test_followers_click_the_same_sun_cell_in_their_own_window():
    screen = Screen()
    before, after = load_frame("hors_combat"), load_frame("hors_combat_foret")
    a = member("A", screen, after)
    b = member("B", screen, before, after)
    c = member("C", screen, scaled(before, 0.8), scaled(after, 0.8))  # fenêtre plus petite
    bot = team_bot(a, b, c, engage=False)
    cell = (24, 12)
    bot.follow(Crossing("droite", cell, read_map_label(before), read_map_label(after)))
    order = [(e[0], e[1]) for e in screen.events if e[1] in ("click", "look")]
    assert order[:4] == [("B", "look"), ("B", "click"), ("C", "look"), ("C", "click")]  # tous cliquent, puis on attend
    (b_click,), (c_click,) = ([e[2] for e in events(screen, "click") if e[0] == n] for n in ("B", "C"))
    assert before.grid.cell_at(*b_click) == cell and scaled(before, 0.8).grid.cell_at(*c_click) == cell
    assert b_click != c_click  # pas les mêmes pixels : la même case
    assert all(e[0] != "A" for e in screen.events)
    assert len(events(screen, "click")) == 2  # arrivés du premier coup : pas de second clic
    assert "suiveur_bloque" not in b.game.recorder.saved + c.game.recorder.saved


def test_leader_crossing_replayed_by_the_followers():
    screen = Screen()
    foret, champs = load_frame("hors_combat_foret"), load_frame("hors_combat")
    a = member("A", screen, foret, foret, foret, champs, champs)
    b = member("B", screen, foret, champs)
    bot = team_bot(a, b, engage=False)
    traveler = Traveler(a.game, TravelSettings(mode="test", settle=0), World(None))
    traveler.on_crossed = bot._crossed  # ce que fait TeamBot avec le Traveler de son roamer
    frame, label = traveler.look()
    assert traveler.go(frame, label, "droite").coords == (4, 3)
    (crossing,) = bot._crossings
    assert crossing.side == "droite" and crossing.cell == (27, 9)
    bot._follow()
    (b_click,) = [e[2] for e in events(screen, "click") if e[0] == "B"]
    assert foret.grid.cell_at(*b_click) == (27, 9)  # le soleil de droite, comme le principal
    assert bot._crossings == []


def test_roamer_of_the_leader_reports_its_crossings(tmp_path):
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json")}
    screen = Screen()
    a, b = member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("hors_combat"))
    bot = TeamBot([a, b], settings, TeamSettings(enabled=True), travel="test")
    assert bot.engager is None and bot.roamer.traveler.on_crossed == bot._crossed


def test_leader_keeps_unreachable_groups_in_the_map_memory_in_zone_mode(tmp_path):
    # Mode zone : zones hors d'atteinte du principal notées dans la mémoire des cartes, selon son côté d'arrivée.
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json")}
    screen = Screen()
    a, b = member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("hors_combat"))
    bot = TeamBot([a, b], settings, TeamSettings(enabled=True), travel="zone")
    assert bot.engager._memory is bot.roamer.traveler.world and bot.engager._where == bot.roamer.traveler.place


def test_lost_follower_rejoins_the_leader_before_it_moves_on(tmp_path, monkeypatch):
    # 11/10, cité d'Astrub : le principal continuait sans ses suiveurs, l'équipe s'est dispersée sur quatre cartes.
    trips = []

    class FakeJourney:
        def __init__(self, traveler, goal, maps=2, reachable=True):
            self.traveler, self.goal, self.done, self.left, self.reachable = traveler, goal, False, maps, reachable
            trips.append((traveler.game.name, traveler.settings.start, goal))

        def step(self):
            self.left -= 1
            if self.left == 0:
                self.done = True
                self.traveler.coords = self.goal if self.reachable else None

    monkeypatch.setattr("bot.team.loop.Journey", FakeJourney)
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json")}
    screen = Screen()
    a, b = member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("hors_combat"))
    bot = TeamBot([a, b], settings, TeamSettings(enabled=True), travel="test")
    leader_steps = []
    monkeypatch.setattr(bot.roamer, "step", lambda: leader_steps.append(1))
    bot.roamer.traveler.coords = (4, -16)
    bot._lost, bot._seen_at = {"B"}, {"B": (5, -17)}
    bot._roam()
    assert leader_steps == [] and trips == [("B", (5, -17), (4, -16))]  # le principal attend, B fait son trajet
    bot._roam()
    assert leader_steps == [1] and not bot._lost  # B arrivé (2e carte) : le principal repart aussitôt
    # Un suiveur qui ne peut pas rejoindre : le bot s'arrête plutôt que de continuer sans lui.
    monkeypatch.setattr("bot.team.loop.Journey", lambda traveler, goal: FakeJourney(traveler, goal, 1, False))
    bot._lost = {"B"}
    bot._roam()
    assert bot.finished and leader_steps == [1]


def test_follower_with_another_decor_does_not_click_the_leaders_sun():
    # 11/10, cité d'Astrub : noms de carte illisibles ; un suiveur sur une autre carte cliquait le même soleil sur la
    # sienne et s'éloignait. Pas le même décor que le principal : perdu, pas de clic.
    screen = Screen()
    foret, champs = load_frame("hors_combat_foret"), load_frame("hors_combat")
    a, b = member("A", screen, foret), member("B", screen, champs)
    bot = team_bot(a, b, engage=False)
    bot._dispatch("droite", (27, 9), foret, 0)
    assert events(screen, "click") == [] and bot._lost == {"B"}
    bot._lost.clear()
    bot._check_together()  # au départ : même vérification
    assert bot._lost == {"B"}


def test_follower_gone_while_the_leader_stayed_is_lost():
    # 11/10 : le clic du principal n'a pas pris, ses suiveurs sont passés seuls ; il est reparti par un autre côté.
    screen = Screen()
    foret, champs = load_frame("hors_combat_foret"), load_frame("hors_combat")
    a, b = member("A", screen, foret), member("B", screen, champs)
    bot = team_bot(a, b, engage=False)
    bot._dispatched = {"B": (foret, 0.0)}  # parti avec le principal...
    bot._dispatch_to = (4, 3)  # ... vers la carte où menait le soleil
    bot._follow()  # aucun passage du principal
    assert bot._lost == {"B"} and bot._seen_at["B"] == (4, 3) and bot._dispatched == {}


def test_sun_taken_by_the_followers_but_not_the_leader_is_not_blocked(tmp_path):
    # 11/10, 1,-13 : le principal, bloqué par le jeu, n'a pas passé le soleil de droite cinq fois ; ses suiveurs, si,
    # à chaque fois. Ce soleil marche : plus noté bloqué ; deux fois de suite, le bot s'arrête (principal bloqué).
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json")}
    screen = Screen()
    foret, champs = load_frame("hors_combat_foret"), load_frame("hors_combat")
    a, b = member("A", screen, foret), member("B", screen, champs)
    bot = TeamBot([a, b], settings, TeamSettings(enabled=True), travel="test")
    traveler = bot.roamer.traveler
    traveler.coords = (1, -13)
    key = traveler.key()
    traveler.world.link(key, "droite", BLOCKED)
    for round_ in (1, 2):
        bot._dispatched, bot._dispatch_side, bot._dispatch_to = {"B": (foret, 0.0)}, "droite", (2, -13)
        bot._follow()  # aucun passage du principal, B est passé
        assert traveler.world.destination(key, "droite") is None
        assert bot.finished == (round_ == 2)
        traveler.world.link(key, "droite", BLOCKED)


@pytest.mark.parametrize("same_decor", [True, False])
def test_follower_on_an_unreadable_map_is_checked_by_decor(same_decor):
    # Bilan du 12/10 : 27 % des arrivées de suiveurs n'étaient pas vérifiées (nom de carte illisible). Même décor que
    # le principal : il a suivi ; autre décor : perdu, il le rejoindra par son trajet.
    screen = Screen()
    foret, pale = load_frame("hors_combat_foret"), load_frame("hors_combat_nom_illisible")
    autre = pale.image.copy()
    left, top, right, bottom = pale.layout.map_box
    autre[top + 150:bottom, left:right] = 255 - autre[top + 150:bottom, left:right]  # décor changé, nom intact
    ailleurs = Frame.of(autre)
    arrivee = pale if same_decor else ailleurs
    a = member("A", screen, pale)
    b = member("B", screen, foret, foret, arrivee, arrivee, arrivee)
    bot = team_bot(a, b, engage=False)
    bot.follow(Crossing("droite", (27, 9), read_map_label(foret), read_map_label(pale) or unreadable_label()))
    assert ("B" in bot._lost) is not same_decor


def test_follower_on_another_map_does_not_follow():
    screen = Screen()
    a = member("A", screen, load_frame("hors_combat_foret"))
    b = member("B", screen, load_frame("hors_combat_prairie"))  # ni la carte quittée ni celle d'arrivée
    bot = team_bot(a, b, engage=False)
    before, after = read_map_label(load_frame("hors_combat")), read_map_label(load_frame("hors_combat_foret"))
    bot.follow(Crossing("droite", (24, 12), before, after))
    assert events(screen, "click") == []


def test_observe_mode_never_switches_windows():
    screen = Screen()
    a, b = member("A", screen, load_frame("combat_mon_tour")), member("B", screen, load_frame("hors_combat"))
    screen.front = b.window  # la fenêtre que tu as devant toi
    bot = team_bot(a, b, act=False)
    bot.step()
    assert screen.events == [("B", "look")]
    screen.front = None  # autre fenêtre (la console) : rien à regarder
    bot.step()
    assert screen.events == [("B", "look")]


def test_combat_only_watches_the_team_window_in_front():
    screen = Screen()
    a, b = member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("hors_combat"))
    bot = team_bot(a, b, engage=False)
    assert bot._watched() is None  # la console devant : le bot ne prend pas la main
    screen.front = b.window
    assert bot._watched() is b
    assert team_bot(a, b)._watched() is a  # le bot attaque : il mène le principal


def test_a_second_bot_refuses_to_start_while_one_runs():
    import win32api

    from bot.__main__ import single_instance

    name = "autobot-test-verrou"  # pas celui du vrai bot : le test ne gêne pas un bot lancé
    first = single_instance(name)
    assert first is not None
    assert single_instance(name) is None  # un bot tourne déjà
    win32api.CloseHandle(first)
    again = single_instance(name)
    assert again is not None  # le premier s'est arrêté : on peut relancer
    win32api.CloseHandle(again)


def test_sword_with_someone_on_it_is_clicked_on_its_pommel_and_a_wrong_menu_is_seen():
    from bot.perception.popups import find_small_menu

    frame = load_frame("epee_pnj_menu_parler")  # 01/10 : PNJ sur l'épée, son menu « Parler » ouvert par le clic
    (sword,) = find_fight_swords(frame)  # le haut seul : la garde est cachée (l'épée des monstres n'est pas prise)
    pommel = sword.points[0][0]
    assert abs(pommel[0] - 1436) <= 3 and pommel[1] <= 320  # au-dessus de la tête du PNJ (clic raté en y = 350)
    assert find_small_menu(frame, around=(1438, 350)) is not None  # « Parler » : à fermer avant de viser ailleurs
    assert find_small_menu(load_frame("hors_combat")) is None


def test_small_menu_closed_by_a_click_under_the_spell_bar_not_escape():
    # 17/10 (journaux 112944, 125352, 161256) : menu « Parler », « Lin / Cueillir / Faucher »... Échap ne le fermait pas
    # et ouvrait le Menu principal derrière. Clic sur le fond beige sous la barre de sorts, comme le menu de Flash.
    from bot.brain.popups import close_small_menu
    from bot.perception.popups import find_small_menu

    screen = Screen()
    menu, closed = load_frame("epee_pnj_menu_parler"), load_frame("hors_combat")
    game = member("Second", screen, menu, closed).game
    close_small_menu(game, menu, lambda f: find_small_menu(f, around=(1438, 350)) is not None)
    (close,) = [e[2] for e in events(screen, "click")]
    assert not menu.layout.on_map(close) and not events(screen, "press")


def test_sword_found_by_its_parts_whatever_its_ground_circle_and_background():
    # 01/10 : cercle cyan (et non violet), fleur blanche derrière le pommeau : le modèle entier ne donnait que 0,57.
    (sword,) = find_fight_swords(load_frame("epee_cercle_cyan_fleur"))
    assert abs(sword.center[0] - 1536) <= 3 and abs(sword.center[1] - 531) <= 3 and sword.score >= 0.85
    pommel = sword.points[0][0]
    assert pommel[1] < sword.center[1] - 15  # premier clic sur le pommeau


def _click_boxes(sword):
    return [(math.floor(x - rx), math.floor(y - ry), math.ceil(x + rx) + 1, math.ceil(y + ry) + 1)
            for (x, y), (rx, ry) in sword.points]


def test_sword_at_the_top_under_the_map_name_found_by_its_icon():
    # 04/10 : groupe attaqué en (715, 57) ; pommeau coupé par le haut de l'écran, garde sous « Astrub (Champs
    # d'Astrub) » (pommeau 0,54, garde 0,41) ; l'icône des 3 personnages est SOUS l'épée, en (793, 97).
    frame = load_frame("epee_haut_ecran_sous_le_nom_de_carte")
    (sword,) = find_fight_swords(frame)
    assert abs(sword.center[0] - 814) <= 3 and abs(sword.center[1] - 16) <= 3
    letters = [(g.x - 2, g.y - 2, g.right + 2, g.bottom + 2) for g in text_glyphs(frame)]
    for x0, y0, x1, y1 in _click_boxes(sword):
        assert y0 >= 0  # pas au-dessus du bord de l'écran (le pommeau)
        assert not any(x0 < r and l < x1 and y0 < b and t < y1 for l, t, r, b in letters)  # pas sur le nom de la carte
    (hx, hy), (bx, by) = (p for p, _ in sword.points)  # la poignée, puis le milieu de la lame
    assert sword_orange(frame.image[hy - 1 : hy + 2, hx - 1 : hx + 2]).any() and 0 < hy < 25
    blade = frame.image[by - 1 : by + 2, bx - 1 : bx + 2].min(axis=2) >= 150
    assert blade.mean() >= 0.5 and 46 < by < 64  # lame claire, entre les deux lignes du nom de la carte
    # Aussi trouvée par la recherche près du groupe attaqué, la première faite.
    assert Joiner()._find(frame, to_reference(frame.layout, (715, 57)))[0].center == sword.center


def test_follower_joins_the_sword_at_the_top_by_its_handle():
    screen = Screen()
    follower = member("Second", screen, load_frame("epee_haut_ecran_sous_le_nom_de_carte"), load_frame("preparation"))
    assert Joiner(timeout=5).join(follower, hint=to_reference(follower.game.frames[0].layout, (715, 57)))
    (x, y), = [e[2] for e in events(screen, "click_right")]
    assert abs(x - 812) <= 2 and abs(y - 11) <= 2  # la poignée, au-dessus du nom de la carte


@pytest.mark.parametrize("at", [(1853, 85), (1694, 747), (900, 300)])
def test_icon_alone_is_not_a_sword_even_over_orange_buttons(at):
    # 04/10 : icône collée au hasard sur 1 200 captures sans épée : sous les boutons orange du haut à droite ou
    # au-dessus de la barre des sorts, une plage orange passait pour la poignée.
    from bot.perception.join import ICON, _asset, _icon_matches

    icon = _asset(ICON)
    image = load_frame("hors_combat").image.copy()
    x, y = at
    alpha = icon[..., 3:4] / 255.0
    patch = image[y : y + icon.shape[0], x : x + icon.shape[1]]
    image[y : y + icon.shape[0], x : x + icon.shape[1]] = (icon[..., :3] * alpha + patch * (1 - alpha)).astype(np.uint8)
    frame = Frame.of(image)
    assert any(abs(ix - x) <= 1 and abs(iy - y) <= 1 for _, ix, iy in _icon_matches(frame, frame.layout.map_box))
    assert find_fight_swords(frame) == []


def test_sword_of_a_fight_we_cannot_join_is_not_taken_despite_its_icon():
    # 04/10 : « Impossible de rejoindre le combat car l'équipe est fermée (ou limitée au groupe du joueur principal) » :
    # épée beige, sans orange, avec l'icône des 3 personnages au-dessus (1,0).
    assert find_fight_swords(load_frame("epee_beige_equipe_limitee_au_groupe")) == []


def test_ally_turn_waits_for_its_timer_in_this_window_before_looking_at_the_others():
    # 01/10 : le timer d'un tour se remplit en ~1 s ; aller voir aussitôt les autres fenêtres faisait des allers-retours.
    screen = Screen()
    seen_from_a = without_timer(load_frame("combat_mon_tour"))
    a, b = member("A", screen, seen_from_a), member("B", screen, load_frame("combat_mon_tour"))
    bot = team_bot(a, b)
    bot.my_team = RED
    scene = read_scene(seen_from_a)
    assert bot._player(a, seen_from_a, scene, [a, b]) is None  # tout début du tour : on attend ici
    assert events(screen, "look") == []
    portrait, seen = bot._turn_seen
    bot._turn_seen = (portrait, seen - 2)  # 2 s plus tard, toujours pas de timer ici : on va voir chez B
    assert bot._player(a, seen_from_a, scene, [a, b]) is b


def test_click_right_after_a_window_switch_does_not_wait_for_the_redraw():
    screen, control = Screen(), FakeControl()
    a, b = FakeWindow(screen, "A"), FakeWindow(screen, "B")
    focus = Focus(control, delay=0.35)
    focus.to(a)
    control.slept.clear()
    focus.to(b, settle=False)  # clic, touche : le jeu les reçoit même avant de s'être redessiné
    assert control.slept == []
    focus.to(b)  # capture : là, on attend le redessin, une fois
    focus.to(b)
    assert control.slept == [0.35]


def test_next_follower_clicks_the_sword_where_it_worked_for_the_first(monkeypatch):
    screen = Screen()
    sword = with_sword("hors_combat", at=(900, 300))
    first = member("Second", screen, sword, with_join_menu(sword, at=(950, 330)), load_frame("preparation"))
    joiner = Joiner(timeout=5)
    assert joiner.join(first, hint=None) and joiner.known is not None
    worked = events(screen, "click_right")[0][2]
    monkeypatch.setattr("bot.team.join.find_fight_swords", lambda *a, **k: pytest.fail("épée recherchée de nouveau"))
    screen.events.clear()
    third = member("Third", screen, sword, with_join_menu(sword, at=(950, 330)), load_frame("preparation"))
    assert joiner.join(third, hint=None)
    again = events(screen, "click_right")[0][2]
    assert abs(again[0] - worked[0]) <= 4 and abs(again[1] - worked[1]) <= 4
    joiner.new_fight()
    assert joiner.known is None


def test_closing_window_is_not_clicked_twice():
    screen = Screen()
    result = load_frame("coeur_43pv")  # fenêtre « Résultat du combat »
    m = member("A", screen, result)
    assert m.popups.handle(result, 0) and m.popups.handle(result, 0)  # revue aussitôt : encore en train de se fermer
    assert len(events(screen, "click")) == 1


def test_fight_end_seen_at_once_on_the_result_window(monkeypatch):
    monkeypatch.setattr("bot.team.loop.FIGHT_END_CONFIRM", 99)  # sans la fenêtre de résultat, on attendrait
    screen = Screen()
    a = member("A", screen, load_frame("coeur_43pv"))
    bot = team_bot(a)
    bot.my_team = RED
    bot._turns([a])  # rend la main aussitôt


def test_result_window_closed_in_every_fight_window_with_one_look():
    screen = Screen()
    result = load_frame("coeur_43pv")  # « Résultat du combat », même fenêtre au même endroit pour tous
    leader, follower = member("A", screen, result), member("B", screen, result)
    bot = team_bot(leader, follower)
    bot._close_results(leader, result, [leader, follower])
    clicks = events(screen, "click")
    assert [e[0] for e in clicks] == ["B", "A"]  # le principal en dernier : il reste devant
    assert clicks[0][2] == clicks[1][2]  # même bouton « Fermer »
    assert events(screen, "look") == []  # aucune capture dans la fenêtre du suiveur
    screen.events.clear()
    assert follower.popups.handle(result, 0) and events(screen, "click") == []  # vérification : pas de second clic


def test_click_cancelled_when_another_window_is_under_the_mouse(monkeypatch):
    from bot.actions import input as game_input

    pressed = []
    monkeypatch.setattr(game_input.win32gui, "WindowFromPoint", lambda point: 111)
    monkeypatch.setattr(game_input.win32gui, "GetAncestor", lambda hwnd, flag: 222)  # l'autre fenêtre du jeu, devant
    monkeypatch.setattr(game_input.win32api, "mouse_event", lambda *args: pressed.append(args))
    monkeypatch.setattr(game_input.win32api, "GetCursorPos", lambda: (10, 10))
    monkeypatch.setattr(game_input, "UNDER_CURSOR_WAIT", 0)

    class Window:
        hwnd, title = 333, "B - Dofus Retro v1.49.5"

        def is_foreground(self):
            return True

        def client_rect(self):
            from bot.capture.window import Rect
            return Rect(0, 0, 1918, 1008)

    class Control:
        def check(self):
            pass

        def sleep(self, seconds):
            pass

    clicker = game_input.GameInput(Window(), Control(), game_input.Human(reaction=(0, 0), hold=(0, 0),
                                                                        hesitation_chance=0))
    monkeypatch.setattr(clicker, "release_stuck", lambda: [])
    assert clicker.click() is False and pressed == []  # annulé : il serait parti dans l'autre fenêtre
    monkeypatch.setattr(game_input.win32gui, "GetAncestor", lambda hwnd, flag: 333)
    assert clicker.click() is True and len(pressed) == 2  # appui et relâchement


def test_click_outside_the_game_window_is_cancelled_not_a_crash(monkeypatch):
    # 03/10 : épée tout en haut de la carte, clic visé en y = -4 : le bot s'était arrêté (ValueError).
    from bot.actions import input as game_input

    pressed, cursor = [], [(10, 10)]
    monkeypatch.setattr(game_input.win32gui, "WindowFromPoint", lambda point: 333)
    monkeypatch.setattr(game_input.win32gui, "GetAncestor", lambda hwnd, flag: 333)
    monkeypatch.setattr(game_input.win32api, "mouse_event", lambda *args: pressed.append(args))
    monkeypatch.setattr(game_input.win32api, "GetCursorPos", lambda: cursor[0])
    monkeypatch.setattr(game_input.win32api, "SetCursorPos", lambda point: cursor.__setitem__(0, point))

    class Window:
        hwnd, title = 333, "B - Dofus Retro v1.49.5"

        def is_foreground(self):
            return True

        def client_rect(self):
            from bot.capture.window import Rect
            return Rect(0, 0, 1918, 1008)

    class Control:
        def check(self):
            pass

        def sleep(self, seconds):
            pass

    clicker = game_input.GameInput(Window(), Control(), game_input.Human(reaction=(0, 0), hold=(0, 0),
                                                                        hesitation_chance=0, move_time=(0, 0)))
    monkeypatch.setattr(clicker, "release_stuck", lambda: [])
    assert clicker.click((1197, -4)) is False and pressed == []  # hors de la fenêtre : annulé, sans planter
    for _ in range(20):  # tout près du bord : la dispersion ne fait jamais sortir de la fenêtre
        assert clicker.click((1197, 1), (8, 8)) is True and 0 <= cursor[0][1] < 1008


def test_right_click_on_the_sword_joins_without_any_menu():
    # Indication de l'utilisateur (03/10) : clic droit sur l'épée = on rejoint directement, pas de menu « Rejoindre ».
    screen = Screen()
    sword = with_sword("hors_combat", at=(900, 300))
    follower = member("Second", screen, sword, load_frame("preparation"))
    assert Joiner(timeout=5).join(follower, hint=None)
    assert len(events(screen, "click_right")) == 1 and events(screen, "click") == []  # un seul geste


def test_flash_menu_opened_by_a_sword_click_is_closed_without_escape():
    # 17/10 : clic droit au point connu de l'épée, pas encore dessinée : menu de Flash, pris pour le menu de quelqu'un ;
    # Échap ouvrait le Menu principal. Il est fermé sous la barre de sorts, puis l'épée recliquée.
    screen = Screen()
    sword = with_sword("hors_combat", at=(900, 300))
    flash = with_flash_menu(sword, at=(982, 346))
    follower = member("Second", screen, sword, flash, sword, sword, sword, load_frame("preparation"))
    assert Joiner(timeout=5).join(follower, hint=None)
    assert not [e for e in events(screen, "press") if e[2] == "escape"]
    close = [e[2] for e in events(screen, "click")]
    assert close and not sword.layout.on_map(close[0])
    assert len(events(screen, "click_right")) >= 2


def test_flash_menu_opened_by_the_click_that_joins_is_closed_at_once():
    # 17/10 (journal 155034) : le clic droit fait entrer Vertdepomme ET ouvre le menu de Flash, laissé sur la timeline.
    screen = Screen()
    sword = with_sword("hors_combat", at=(900, 300))
    joined = with_flash_menu(load_frame("preparation"), at=(982, 346))
    follower = member("Second", screen, sword, joined, load_frame("preparation"))
    assert Joiner(timeout=5).join(follower, hint=None)
    (close,) = [e[2] for e in events(screen, "click")]
    assert not sword.layout.on_map(close)
    assert not [e for e in events(screen, "press") if e[2] == "escape"]


def test_flash_menu_left_open_is_closed_at_the_first_turn_of_the_fight_only():
    # 17/10 (journal 155034) : menu de Flash oublié sur la timeline de Vertdepomme, 2 min 30 perdues à chacun de ses
    # tours. Cherché dans toute la fenêtre à son premier tour du combat (0,16 s), pas aux suivants.
    screen = Screen()
    stuck = load_frame("combat_menu_flash_sur_timeline")
    fighter = member("Vertdepomme", screen, stuck, stuck, load_frame("combat_mon_tour"))
    bot = team_bot(fighter)
    bot._play(fighter, read_scene(stuck))
    (close,) = [e[2] for e in events(screen, "click")]
    assert not stuck.layout.on_map(close) and [e[1] for e in screen.events].count("tour") == 1
    bot._play(fighter, read_scene(stuck))
    assert len(events(screen, "click")) == 1


def test_out_of_combat_screen_right_after_ready_is_not_the_fight_end(monkeypatch):
    # 03/10 : « Combat terminé » 1 s après Prêt (l'écran d'entre Prêt et la timeline ressemble à du hors combat), puis
    # combat repris sans connaître les cases : déplacements et sorts faux. Tant qu'aucun tour n'a été lu dans la
    # timeline, il faut START_CONFIRM s de hors combat.
    clock = [0.0]

    def monotonic():
        clock[0] += 0.4
        return clock[0]

    monkeypatch.setattr("bot.team.loop.time.monotonic", monotonic)
    screen = Screen()
    out, enemy = load_frame("hors_combat"), load_frame("combat_tour_adversaire")
    a = member("A", screen, out, out, out, out, out, enemy, out)  # 2 s de « hors combat », puis la timeline
    bot = team_bot(a)
    bot.my_team = RED
    assert bot._turns([a]) is None
    assert len(a.game.frames) == 1  # la timeline a été vue : pas de fin conclue avant


def test_whole_team_clicks_the_sun_right_after_the_leader():
    # 03/10 : les suiveurs ne partaient qu'une fois le principal arrivé (~4 s perdues à chaque carte). Ils cliquent
    # maintenant le même soleil aussitôt après lui ; leur arrivée est vérifiée ensuite, sans nouveau clic.
    screen = Screen()
    foret, champs = load_frame("hors_combat_foret"), load_frame("hors_combat")
    a = member("A", screen, foret, foret, foret, champs, champs)
    b = member("B", screen, foret, foret, champs)  # même décor que le principal avant de cliquer, puis celle du départ
    bot = team_bot(a, b, engage=False)
    traveler = Traveler(a.game, TravelSettings(mode="test", settle=0), World(None))
    traveler.on_crossed, traveler.on_click = bot._crossed, bot._dispatch  # comme TeamBot avec son roamer
    frame, label = traveler.look()
    assert traveler.go(frame, label, "droite").coords == (4, 3)
    clicks = [e[0] for e in screen.events if e[1] == "click"]
    assert clicks == ["A", "B"]  # B part aussitôt, avant que le principal ne soit arrivé
    a_click = next(i for i, e in enumerate(screen.events) if e[:2] == ("A", "click"))
    first_a_look_after = next(i for i, e in enumerate(screen.events) if e == ("A", "look") and i > a_click)
    assert screen.events.index(next(e for e in screen.events if e[:2] == ("B", "click"))) < first_a_look_after
    bot._follow()
    assert [e[0] for e in screen.events if e[1] == "click"] == ["A", "B"]  # arrivé : pas de second clic
    (b_click,) = [e[2] for e in events(screen, "click") if e[0] == "B"]
    assert foret.grid.cell_at(*b_click) == (27, 9)


def test_follower_still_on_the_map_clicks_the_sun_again(monkeypatch):
    # 03/10 : un personnage passait sur le soleil, le clic de Imthebest l'a visé lui : recliqué 12 s plus tard
    # seulement. Toujours sur la carte de départ après RECLICK_EVERY s : nouveau clic, ailleurs sur la case.
    monkeypatch.setattr("bot.team.loop.RECLICK_EVERY", 0)
    monkeypatch.setattr("bot.team.loop.FOLLOWER_STILL", 0)  # immobile (même capture) : pas d'attente en test
    screen = Screen()
    before, after = load_frame("hors_combat"), load_frame("hors_combat_foret")
    a = member("A", screen, after)
    b = member("B", screen, before, before, before, after, after)
    bot = team_bot(a, b, engage=False)
    bot.follow(Crossing("droite", (24, 12), read_map_label(before), read_map_label(after)))
    b_clicks = [e[2] for e in events(screen, "click") if e[0] == "B"]
    assert len(b_clicks) >= 2 and len(set(b_clicks)) >= 2  # recliqué, à un autre endroit
    assert all(before.grid.cell_at(*c) == (24, 12) for c in b_clicks)  # toujours la case du soleil
    assert "suiveur_bloque" not in b.game.recorder.saved


def test_without_placement_everybody_joins_and_is_ready_at_once(monkeypatch):
    # Demande du 03/10 : à 8, se placer prend trop de temps. Personne ne bouge, Prêt partout ; chacun est repéré à son
    # premier tour par la portée d'un sort.
    monkeypatch.setattr("bot.team.loop.PLACEMENT_FIRST_TRY", 0)
    screen = Screen()
    placement = load_frame("preparation")
    a, b, c = (member(n, screen, placement) for n in ("A", "B", "C"))
    a.game.after_press["f1"] = [load_frame("combat_tour_adversaire")]
    bot = team_bot(a, b, c)
    assert not bot.team.place  # défaut
    bot.joiner.ask = lambda m, hint: True
    bot.joiner.wait_placement = lambda m, deadline: True
    bot._claim_cell = lambda m, taken: pytest.fail("aucun suiveur ne choisit sa case")
    assert bot._placement(a) == [a, b, c]
    assert events(screen, "click") == [] and ("A", "placement") not in screen.events  # aucun déplacement
    presses = [(e[0], e[2]) for e in events(screen, "press")]
    # 18/10 : ordre des suiveurs mélangé, le lanceur du combat toujours en dernier.
    assert sorted(presses[:2]) == [("B", "f1"), ("C", "f1")] and presses[2] == ("A", "f1")
    assert bot._cells == {}  # cases inconnues : repérées au premier tour


def test_no_new_turn_for_the_character_who_just_played(monkeypatch):
    # 03/10 : 0,2 s après le F1 de Walitcoin, la timeline encadrait encore son portrait : il a « rejoué ».
    screen = Screen()
    frame = without_timer(load_frame("combat_mon_tour"))
    a, b = member("A", screen, frame), member("B", screen, frame)
    bot = team_bot(a, b)
    bot.my_team = RED
    bot.router.learn("A", current_portrait(frame))
    bot._just_played = ("A", time.monotonic())
    assert bot._player(a, frame, read_scene(frame), [a, b]) is None
    bot._just_played = ("A", time.monotonic() - REPLAY_GUARD)
    assert bot._player(a, frame, read_scene(frame), [a, b]) is a


def test_timer_still_shown_after_the_end_of_my_turn_is_not_a_new_turn():
    # 04/10 : 0,4 s après le F1 de Fourbocinq (fin de tour sans attendre l'animation), son timer encore affiché pendant
    # le tour d'un monstre : pris pour un nouveau tour, portrait du monstre retenu comme le sien, équipe bleue au lieu
    # de rouge : 25 minutes de tours perdus.
    screen = Screen()
    frame = load_frame("combat_mon_tour")  # timer visible
    a, b = member("A", screen, frame), member("B", screen, frame)
    bot = team_bot(a, b)
    bot._just_played = ("A", time.monotonic())
    assert bot._player(a, frame, read_scene(frame), [a, b]) is None
    assert bot.my_team is None and bot.router.portraits.get("A") is None  # rien d'appris de travers


def test_team_colour_learned_from_a_known_portrait():
    screen = Screen()
    frame = without_timer(load_frame("combat_mon_tour"))
    a, b = member("A", screen, frame), member("B", screen, frame)
    bot = team_bot(a, b)
    bot.router.learn("A", current_portrait(frame))
    assert bot._player(b, frame, read_scene(frame), [a, b]) is a
    assert bot.my_team == read_scene(frame).current_team  # sans placement choisi : appris ici


def test_fight_running_past_the_session_time_stops_the_bot():
    # 16/10 : farm de 30 min, combat bloqué à partir de la 28e minute ; le bot tournait encore à la 43e.
    screen = Screen()
    one = member("Premier", screen, load_frame("combat_mon_tour"))
    bot = team_bot(one)
    bot.deadline = time.monotonic() - 301
    assert bot._turns([one]) is None
    assert bot.finished and "dépassée" in bot._stop_reason


def test_member_whose_turn_never_passes_is_left_alone_for_a_while():
    screen = Screen()
    one = member("Premier", screen, load_frame("combat_mon_tour"))
    bot = team_bot(one)
    assert bot._unless_stuck(one) is one
    bot._stuck["Premier"] = time.monotonic() + 30
    assert bot._unless_stuck(one) is None


def test_team_split_on_two_maps_stops_before_the_first_attack():
    # 17/10 : Koalaloupour (principal) réapparu à Astrub, les 7 autres à Incarnam : il s'est battu seul.
    screen = Screen()
    leader = member("Premier", screen, load_frame("hors_combat"))
    other = member("Second", screen, load_frame("hors_combat_prairie"))
    bot = team_bot(leader, other)
    bot.roamer = None
    assert not bot._together_before_fighting()
    assert bot.finished and "Second" in bot._lost


def test_team_together_fights_normally():
    screen = Screen()
    leader = member("Premier", screen, load_frame("hors_combat"))
    other = member("Second", screen, load_frame("hors_combat"))
    bot = team_bot(leader, other)
    assert bot._together_before_fighting() and not bot.finished


def test_sword_found_with_a_wider_handle_in_a_taller_window():
    # 17/10, Incarnam 6,2, fenêtre 1920x1057 : poignée de 4 à 5 px de large (règle : 3,5 au plus), épée refusée ; les
    # 7 suiveurs ne l'ont pas vue et Koalaloupour, seul, est mort.
    (sword,) = find_fight_swords(load_frame("epee_champs_1057"))
    assert abs(sword.center[0] - 1064) <= 6 and abs(sword.center[1] - 274) <= 6


def test_followers_click_other_upper_parts_of_the_sword_never_the_guard(monkeypatch):
    # 18/10 : les suiveurs suivants ne cliquent plus tous au même pixel ; une fois sur deux le point qui a marché,
    # sinon l'autre partie haute de la même épée (pommeau ou poignée), jamais la garde (cachée par qui est dessus).
    from bot.team.join import SWORD_PARTS

    joiner = Joiner()
    joiner.known = (900.0, 300.0)
    joiner.spots = [((901.0, 316.0), (1.0, 4.0))]
    monkeypatch.setattr("bot.team.join.random.random", lambda: 0.9)
    assert joiner._spot() == ((901.0, 316.0), (1.0, 4.0))
    monkeypatch.setattr("bot.team.join.random.random", lambda: 0.1)
    assert joiner._spot() == ((900.0, 300.0), (3, 3))
    joiner.spots = []
    monkeypatch.setattr("bot.team.join.random.random", lambda: 0.9)
    assert joiner._spot() == ((900.0, 300.0), (3, 3))
    assert SWORD_PARTS == 2  # pommeau et poignée seulement


def test_sword_spots_remembered_after_the_first_join():
    screen = Screen()
    sword = with_sword("hors_combat", at=(900, 300))
    follower = member("Second", screen, sword, load_frame("preparation"))
    joiner = Joiner(timeout=5)
    assert joiner.join(follower, hint=None)
    assert joiner.known is not None and len(joiner.spots) <= 1
    assert all(spot != joiner.known for spot, _ in joiner.spots)
