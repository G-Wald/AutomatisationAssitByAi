"""Reprise après erreur (21/10), d'après les journaux du 20/10 : ordre des fenêtres, fenêtre de résultat reconnue de
justesse, fenêtre qui revient sans cesse, fenêtre sourde aux touches (Z, sorts, F1, A), erreurs et dépassement de la
durée de session, surveillance du progrès (bot/brain/recovery.py, bot/team/recovery.py)."""

import logging
import time
from contextlib import contextmanager

import numpy as np
import pytest

from bot.actions.stop import BotStopped, StopControl
from bot.brain.combat import CombatAI, CombatSettings
from bot.brain.engage import DEAF_CYCLES, Engager, EngageSettings
from bot.brain.game import Game
from bot.brain.popups import REPEAT_DIAGNOSE, REPEAT_GIVE_UP, PopupCloser
from bot.brain.recovery import EPISODE, capture_once, is_deaf, keyboard_answers, mark_deaf, unlock_click
from bot.brain.travel import Traveler, TravelSettings, World
from bot.config import load_settings
from bot.perception.colors import RED
from bot.perception.frame import Frame
from bot.perception.groups import find_group_tooltips
from bot.perception.maplabel import read_map_label
from bot.perception.popups import (PopupSpec, find_generic_close, find_known_popup, flash_close_point,
                                   load_popup_specs)
from bot.perception.templates import match_template
from bot.perception.ui import spell_slot_point
from bot.state.scene import read_scene
from bot.team.recovery import FRUITLESS_LIMIT, STALL_DIAGNOSE, STALL_LAST_RESORT, RecoverySettings
from conftest import load_frame
from tests.test_team import FakeMemberGame, Screen, events, member, team_bot
from tests.test_travel import with_a_outline

LEVEL_UP_OVER_RESULT = ["niveau_14_par_dessus_resultat_champs", "niveau_14_par_dessus_resultat_calanques"]
RESULT_1057 = "resultat_combat_reconnu_de_justesse"  # 20/10, journal 201321, capture 0021 (fenêtre de 1057 px)
RESULT = "résultat du combat"


def near(point, expected, tolerance=5) -> bool:
    return point is not None and abs(point[0] - expected[0]) <= tolerance and abs(point[1] - expected[1]) <= tolerance


def episodes(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(EPISODE)]


# --- 1. Ordre des fenêtres ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", LEVEL_UP_OVER_RESULT)
def test_level_up_window_over_the_result_is_closed_first(name):
    # 20/10 (S11 : journal 205623, capture 0247 ; S15 : journal 211515, capture 0125) : « Informations — Tu passes niveau
    # 14 » ouverte par-dessus le résultat du combat. La plus grande surface beige (le résultat) passait d'abord :
    # « Fermer » cliqué sous la fenêtre du niveau, sans effet.
    match = find_known_popup(load_frame(name), load_popup_specs())
    assert match.spec.name == "informations (niveau supérieur)"
    assert near(match.close_point, (1216, 502))  # « Ok »


def test_popup_closer_closes_the_level_up_then_the_result():
    screen = Screen()
    level_up, result = load_frame(LEVEL_UP_OVER_RESULT[0]), load_frame(RESULT_1057)
    m = member("A", screen, level_up)
    assert m.popups.handle(level_up, 2)
    assert m.popups.handle(result, 2)
    ok, fermer = (e[2] for e in events(screen, "click"))
    assert near(ok, (1216, 502)) and near(fermer, (1663, 715))


# --- 2. Fenêtre de résultat reconnue de justesse -----------------------------------------------------------------------

def test_result_window_of_the_201321_capture_recognised_by_its_second_model():
    frame = load_frame(RESULT_1057)
    match = find_known_popup(frame, load_popup_specs())
    assert match.spec.name == RESULT and near(match.close_point, (1663, 715))
    # Premier modèle : 0,874 seulement ; le second, découpé dans cette capture, ne laisse aucun doute.
    assert match_template(frame, "fin_de_combat_titre_2.png", threshold=0.9) is not None


def test_title_or_button_enough_when_the_other_is_close():
    frame = load_frame(RESULT_1057)  # titre 0,875, bouton 0,89 avec les premiers modèles
    strict = PopupSpec("résultat", "fin_de_combat_titre.png", close="fin_de_combat_fermer.png", threshold=0.88)
    paired = PopupSpec("résultat", "fin_de_combat_titre.png", close="fin_de_combat_fermer.png", threshold=0.88,
                       pair_threshold=0.8)
    assert find_known_popup(frame, [strict]) is None  # titre sous le seuil
    assert find_known_popup(frame, [paired]) is not None  # le bouton au seuil, le titre au-dessus de 0,80
    both_low = PopupSpec("résultat", "fin_de_combat_titre.png", close="fin_de_combat_fermer.png", threshold=0.9,
                         pair_threshold=0.8)
    assert find_known_popup(frame, [both_low]) is None  # ni l'un ni l'autre sûr : rien


@pytest.mark.parametrize("name", ["fenetre_options", "combats_en_cours", "challenge_en_cours", "mort_perte_energie",
                                  "hors_combat", "click_lancer_le_combat", "menu_flash_clic_droit"])
def test_no_result_window_seen_on_other_windows(name):
    # « Fermer » à 0,93 sur Options et Combats en cours : le titre du résultat n'y dépasse pas 0,55.
    match = find_known_popup(load_frame(name), load_popup_specs())
    assert match is None or match.spec.name != RESULT


def test_leader_windows_looked_at_again_after_a_fight_end_without_result_window():
    # 20/10 (journal 201321) : fin du combat vue sans fenêtre de résultat, rien fermé au tour des fenêtres ; le résultat
    # est resté ouvert, l'équipe a cherché des groupes derrière lui. Nouveau regard chez le principal 2 s plus tard.
    screen = Screen()
    a = member("A", screen, load_frame("hors_combat"))
    bot = team_bot(a, engage=False)
    bot._round_due, bot._result_unseen = True, True
    bot.step()
    assert bot._result_check_at is not None and events(screen, "click") == []
    a.game.frames = [load_frame(RESULT_1057), load_frame("hors_combat")]
    bot._result_check_at = time.monotonic() - 0.1
    bot.step()
    assert [e[2] for e in events(screen, "click")] and near(events(screen, "click")[0][2], (1663, 715))
    assert bot._result_check_at is None


def test_no_second_look_when_the_round_closed_the_result():
    screen = Screen()
    a = member("A", screen, load_frame("fin_de_combat"), load_frame("hors_combat"))
    bot = team_bot(a, engage=False)
    bot._round_due, bot._result_unseen = True, True
    bot.step()
    assert len(events(screen, "click")) == 1 and bot._result_check_at is None


# --- 3. Fenêtre qui revient sans cesse ---------------------------------------------------------------------------------

def test_window_that_keeps_coming_back_gets_another_button_then_is_left_until_the_next_round(monkeypatch, caplog):
    clock = [1000.0]
    monkeypatch.setattr("bot.brain.popups.time.monotonic", lambda: clock[0])
    screen = Screen()
    result = load_frame("fin_de_combat")
    m = member("A", screen, result)
    done = []
    with caplog.at_level(logging.INFO):
        for _ in range(REPEAT_GIVE_UP + 2):
            clock[0] += 1.0  # au-delà de RECLICK_AFTER : un nouvel essai à chaque fois
            done.append(m.popups.handle(result, 0))
    points = [e[2] for e in events(screen, "click")]
    assert len(points) == REPEAT_GIVE_UP
    assert all(near(p, (1675, 590)) for p in points[:REPEAT_DIAGNOSE])  # « Fermer »
    assert all(near(p, (1872, 237)) for p in points[REPEAT_DIAGNOSE:])  # puis la croix orange du titre
    assert done[-2:] == [False, False] and m.popups.blocked == RESULT  # notée bloquée : on n'insiste plus
    assert m.game.recorder.saved.count("fenetre_qui_ne_se_ferme_pas") == 1
    assert any("revenue 6 fois" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert m.popups.describe(result) is not None  # le tour des fenêtres la saute
    m.popups.new_round()  # tour des fenêtres suivant : nouvel essai
    clock[0] += 1.0
    assert m.popups.handle(result, 0) and len(events(screen, "click")) == REPEAT_GIVE_UP + 1
    clock[0] += 30.0  # revue bien plus tard : une autre fois, plus la même série
    m.popups._closed = (RESULT, clock[0] - 20.0)
    assert m.popups.handle(result, 0) and near(events(screen, "click")[-1][2], (1675, 590))


def test_generic_close_buttons_of_the_windows_of_the_20_10():
    found = find_generic_close(load_frame(LEVEL_UP_OVER_RESULT[1]))
    assert found is not None and found[1] == "informations_ok.png" and near(found[0], (1216, 502))
    assert find_generic_close(load_frame("hors_combat")) is None


# --- 4. Fenêtre sourde aux touches -------------------------------------------------------------------------------------


class MenuGame:
    """Menu de personnage ouvert (`menu`) ; un clic sur la carte hors du menu le ferme (`after`)."""

    def __init__(self, menu, after) -> None:
        self.frame, self.after = menu, after
        self.clicks: list = []

    def look(self, park=None):
        return self.frame

    def sleep(self, seconds):
        pass

    def press(self, key):
        raise AssertionError("pas d'Échap : il ne ferme pas ce menu")

    def click(self, point=None, spread=(0, 0), button="left"):
        self.clicks.append(point)
        self.frame = self.after
        return True


def test_own_character_menu_on_the_sun_closed_by_a_click_beside_it():
    # 21/10, Calanques 11,-27 : chacun avait cliqué sur lui-même, debout sur le soleil ; Échap ne ferme pas ce menu.
    from bot.brain.popups import EDGE_KEEP, close_context_menu
    from bot.perception.popups import find_context_menu

    menu = load_frame("menu_personnage_sur_soleil")
    game = MenuGame(menu, load_frame("hors_combat"))
    assert close_context_menu(game, menu)
    ((x, y),) = game.clicks
    x0, y0, x1, y1 = find_context_menu(menu)
    assert not (x0 - 10 <= x <= x1 + 10 and y0 - 50 <= y <= y1 + 10)  # pas sur le menu (ni sa barre de titre)
    left, top, right, bottom = menu.layout.map_box
    keep_x, keep_y = EDGE_KEEP * 2 * menu.grid.half_w, EDGE_KEEP * 2 * menu.grid.half_h
    assert left + keep_x - 30 <= x <= right - keep_x + 30 and top + keep_y - 30 <= y <= bottom - keep_y + 30


def test_popup_closer_closes_a_character_menu_by_a_click_beside_it():
    menu = load_frame("menu_personnage_sur_soleil")
    game = MenuGame(menu, load_frame("hors_combat"))
    game.recorder = FakeRecorder()
    assert PopupCloser(game, load_popup_specs(), "escape", 5.0).handle(menu, None)
    assert len(game.clicks) == 1


@pytest.mark.parametrize("name", ["menu_flash_clic_droit", "menu_principal", "hors_combat"])
def test_no_click_beside_the_flash_menu_the_main_menu_or_nothing(name):
    from bot.brain.popups import close_context_menu

    frame = load_frame(name)
    game = MenuGame(frame, frame)
    assert not close_context_menu(game, frame) and game.clicks == []

def test_unlock_click_on_the_safe_beige_background_only():
    screen = Screen()
    frame = load_frame("hors_combat")
    m = member("A", screen, frame)
    assert unlock_click(m.game, frame)
    ((_, _, point),) = events(screen, "click")
    assert near(point, flash_close_point(frame), 4) and not frame.layout.on_map(point)
    dark = Frame.of(np.zeros_like(frame.image))  # une fenêtre assombrit l'écran : aucun clic
    assert not unlock_click(m.game, dark) and len(events(screen, "click")) == 1


def test_deaf_state_kept_per_character_window():
    screen = Screen()
    a, b = member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("hors_combat"))
    mark_deaf(a.game, "test")
    assert is_deaf(a.game) and not is_deaf(b.game)


class FakeControl:
    def __init__(self, game) -> None:
        self.game = game

    def sleep(self, seconds):
        self.game.clock += seconds


class KeyboardGame:
    """Faux jeu dont la fenêtre peut être sourde aux touches (20/10) : Z tenue montre `z_frame`, A montre `exits_frame`,
    sauf si `deaf` ; un clic la débloque si `unlocks`."""

    wait_until = Game.wait_until

    def __init__(self, base, z_frame, exits_frame, deaf=False, unlocks=True) -> None:
        self.base, self.z_frame, self.exits_frame = base, z_frame, exits_frame
        self.deaf, self.unlocks = deaf, unlocks
        self.actions: list[tuple] = []
        self.clock = 0.0
        self.control = FakeControl(self)
        self.recorder = FakeRecorder()
        self.z_held = self.exits_shown = False

    def look(self, park=None):
        if self.z_held and not self.deaf:
            return self.z_frame
        return self.exits_frame if self.exits_shown else self.base

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds

    def press(self, key):
        self.actions.append(("press", key))
        if key == "a" and not self.deaf:
            self.exits_shown = True

    @contextmanager
    def holding(self, key):
        self.actions.append(("hold", key))
        self.z_held = True
        try:
            yield
        finally:
            self.z_held = False

    def move(self, point, spread=(0, 0)):
        return round(point[0]), round(point[1])

    def click(self, point=None, spread=(0, 0), button="left"):
        self.actions.append(("click", point))
        if self.unlocks:
            self.deaf = False
        return True


class FakeRecorder:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def keeps(self, lossless=False):
        return True

    def save(self, image, label, lossless=False, quality=80):
        self.saved.append(label)
        return f"<{label}>"


def kinds(game, kind):
    return [a[1] for a in game.actions if a[0] == kind]


def test_empty_z_twice_then_keys_checked_with_a_and_the_map_is_really_empty():
    base = load_frame("hors_combat")
    assert find_group_tooltips(base) == []
    game = KeyboardGame(base, base, with_a_outline())
    engager = Engager(game, EngageSettings())
    assert not engager.engage() and "a" not in kinds(game, "press")  # 1re recherche vide : pas encore de sonde
    assert not engager.engage() and kinds(game, "press") == ["a"]  # 2e : sonde par A, sorties vues
    assert engager.keyboard_ok is True and engager.none_found and kinds(game, "click") == []


def test_deaf_window_unlocked_by_a_click_then_z_again_shows_the_groups(caplog):
    base, groups = load_frame("hors_combat"), load_frame("groupes_z_haut_mixte")
    game = KeyboardGame(base, groups, with_a_outline(), deaf=True)
    engager = Engager(game, EngageSettings())
    with caplog.at_level(logging.INFO):
        frame, seen = engager._z_unheard(base)
    assert seen and engager.keyboard_ok is True and game.deaf_until == 0.0
    (point,) = kinds(game, "click")
    assert near(point, flash_close_point(base), 4)  # le fond beige sûr, pas la carte
    (line,) = episodes(caplog)
    assert "touche Z" in line and "débloqué : oui" in line


def test_window_still_deaf_after_three_unlock_clicks_is_not_an_empty_map(caplog):
    base = load_frame("hors_combat")
    game = KeyboardGame(base, base, with_a_outline(), deaf=True, unlocks=False)
    engager = Engager(game, EngageSettings())
    engager.probe_first = True  # mode zone : dès la 1re recherche vide, avant de changer de carte
    with caplog.at_level(logging.INFO):
        assert not engager.engage()
    assert engager.keyboard_ok is False and len(kinds(game, "click")) == DEAF_CYCLES
    assert kinds(game, "press") == ["a"] * DEAF_CYCLES
    (line,) = episodes(caplog)
    assert "débloqué : non" in line and "clavier_sourd" in game.recorder.saved and is_deaf(game)


def test_team_does_not_change_map_on_a_deaf_keyboard():
    class Engaged:
        none_found, missed, keyboard_ok = True, False, False
        settings = EngageSettings()

        def engage(self):
            return False

        def new_map(self):
            pass

    class Roamer:
        steps = 0

        def step(self):
            self.steps += 1

    screen = Screen()
    a = member("A", screen, load_frame("hors_combat"))
    bot = team_bot(a)
    bot.engager, bot.roamer = Engaged(), Roamer()
    bot._together_checked, bot._last_round, bot._next_engage_at = True, time.monotonic(), 0.0
    bot._out_of_combat(a, load_frame("hors_combat"))
    assert bot.roamer.steps == 0 and bot._empty_searches == 0 and bot._next_engage_at > time.monotonic()
    bot.engager.keyboard_ok, bot._next_engage_at = True, 0.0  # clavier vérifié : la carte est vide, on change
    bot._out_of_combat(a, load_frame("hors_combat"))
    assert bot.roamer.steps == 1


def test_dungeon_end_room_never_concluded_on_a_deaf_keyboard():
    from bot.team.dungeon import DungeonFarm
    from tests.test_dungeon import unknown_rooms

    class Engaged:
        groups_seen, keyboard_ok = 0, False

        def new_map(self):
            pass

    class FakeBot:
        def __init__(self, team):
            self.members, self.leader, self.engager = team, team[0], Engaged()
            self.finished, self._round_due, self._stop_reason = False, False, ""
            self.fights, self._empty_searches, self.steps = 0, 0, 0

        def step(self):
            self.steps += 1
            self._empty_searches += 1
            self.engager.keyboard_ok = None if self.steps < 4 else (False if self.steps < 6 else True)

    screen = Screen()
    bot = FakeBot([member("A", screen, load_frame("donjon_incarnam_salle_fin"))])
    assert DungeonFarm(bot, unknown_rooms(), 1)._fight_rooms()
    assert bot.steps == 6  # sourd puis pas vérifié : pas de conclusion ; vérifié par A : salle de fin


def spell_game(*, unlocks: bool) -> tuple["SpellGame", Frame, Frame]:
    my_turn, shown = load_frame("combat_mon_tour"), load_frame("combat_portee_sort")
    return SpellGame(my_turn, shown, unlocks), my_turn, shown


class SpellGame(KeyboardGame):
    """En combat : la touche du sort est ignorée (fenêtre sourde) ; un clic sur sa case l'affiche, ou un clic de
    déblocage rend les touches (`unlocks`)."""

    def __init__(self, my_turn, shown, unlocks) -> None:
        super().__init__(my_turn, my_turn, my_turn, deaf=True, unlocks=unlocks)
        self.shown_frame, self.selected = shown, False
        self.slot = spell_slot_point(my_turn.layout, 3)

    def look(self, park=None):
        return self.shown_frame if self.selected else self.base

    def press(self, key):
        self.actions.append(("press", key))
        if not self.deaf and key.isdigit():
            self.selected = True

    def click(self, point=None, spread=(0, 0), button="left"):
        self.actions.append(("click", point))
        if point is not None and abs(point[0] - self.slot[0]) <= 6 and abs(point[1] - self.slot[1]) <= 6:
            self.selected = True
        elif self.unlocks:
            self.deaf = False
        return True


def spell_ai(game) -> CombatAI:
    ai = CombatAI(game, CombatSettings())
    ai._use.pa = 6
    ai._bar_turn = ai._use.turn  # barre lue ce tour : le sort n'est pas grisé
    return ai


def test_spell_key_ignored_then_a_click_on_its_slot_selects_it_and_the_window_is_deaf(caplog):
    game, my_turn, _ = spell_game(unlocks=False)
    ai = spell_ai(game)
    spell = ai.spells[0]
    with caplog.at_level(logging.INFO):
        assert ai._select_spell(my_turn, spell) is not None
    assert kinds(game, "press") == ["3", "3"]  # la touche, puis de nouveau après le clic de déblocage
    clicks = kinds(game, "click")
    assert near(clicks[-1], game.slot, 6) and len(clicks) == 2  # clic de déblocage, puis la case du sort
    assert ai._keys_deaf and is_deaf(game)
    (line,) = episodes(caplog)
    assert "débloqué : non" in line
    game.selected = False
    assert ai._select_spell(my_turn, spell) is not None
    assert kinds(game, "press") == ["3", "3"] and len(kinds(game, "click")) == 3  # reste du tour : clic direct


def test_spell_key_heard_again_after_the_unlock_click(caplog):
    game, my_turn, _ = spell_game(unlocks=True)
    ai = spell_ai(game)
    with caplog.at_level(logging.INFO):
        assert ai._select_spell(my_turn, ai.spells[0]) is not None
    assert kinds(game, "press") == ["3", "3"] and len(kinds(game, "click")) == 1 and not ai._keys_deaf
    (line,) = episodes(caplog)
    assert "débloqué : oui" in line



def test_range_slow_to_show_is_not_a_deaf_window(caplog):
    game, my_turn, _ = spell_game(unlocks=False)
    ai = spell_ai(game)
    looks = []
    game.look = lambda park=None: looks.append(1) or (game.shown_frame if len(looks) >= 2 else game.base)
    with caplog.at_level(logging.INFO):
        assert ai._select_spell(my_turn, ai.spells[0]) is not None
    assert kinds(game, "press") == ["3"] and kinds(game, "click") == [] and not ai._keys_deaf
    assert episodes(caplog) == [] and not is_deaf(game)

def test_spell_key_checked_once_per_turn_only_and_never_without_a_fresh_bar():
    game, my_turn, _ = spell_game(unlocks=False)
    ai = spell_ai(game)
    ai._bar_turn = -1  # sorts grisés pas lus ce tour : une touche sans portée n'est pas suspecte
    assert ai._select_spell(my_turn, ai.spells[0]) is None and kinds(game, "click") == []
    ai._bar_turn, ai._deaf_checked = ai._use.turn, True  # déjà vérifiée ce tour
    assert ai._select_spell(my_turn, ai.spells[0]) is None and kinds(game, "click") == []


def test_end_turn_button_clicked_directly_when_the_window_is_deaf():
    enemy_turn = load_frame("combat_tour_adversaire")
    my_turn = load_frame("combat_mon_tour")

    class TurnGame(KeyboardGame):
        def look(self, park=None):
            return enemy_turn if kinds(self, "click") else my_turn

    game = TurnGame(my_turn, my_turn, my_turn)
    ai = CombatAI(game, CombatSettings(end_turn_key="f1"))
    mark_deaf(game, "test")
    ai._pass_turn(RED)
    assert kinds(game, "press") == []  # pas de F1 perdu
    assert kinds(game, "click") == [read_scene(my_turn).end_turn_button]
    assert ai._end_turn_key  # la touche reste pour les tours suivants


# --- 6. Touche A « aucune sortie » ------------------------------------------------------------------------------------

class ExitsGame(FakeMemberGame):
    """A ignorée tant qu'aucun clic n'a débloqué la fenêtre (si `unlocks`)."""

    def __init__(self, screen, base, lit, unlocks) -> None:
        super().__init__("A", screen, base)
        self.lit, self.unlocks, self.deaf = lit, unlocks, True

    def press(self, key):
        super().press(key)
        if key == "a" and not self.deaf:
            self.frames = [self.lit]

    def click(self, point=None, spread=(0, 0), button="left"):
        super().click(point, spread, button)
        self.deaf = not self.unlocks
        return True


def no_suns(monkeypatch):
    monkeypatch.setattr("bot.brain.travel.find_suns", lambda frame, every=False: [])


def test_a_without_any_exit_is_read_again_then_unlocked(monkeypatch, caplog):
    no_suns(monkeypatch)
    screen = Screen()
    game = ExitsGame(screen, load_frame("hors_combat"), with_a_outline(), unlocks=True)
    traveler = Traveler(game, TravelSettings(mode="test", settle=0, hide_players_key=""), World(None))
    with caplog.at_level(logging.INFO):
        found = traveler._screen_exits()
    assert "haut" in found and not traveler.a_unheard
    assert [e[2] for e in events(screen, "press")] == ["a", "a"] and len(events(screen, "click")) == 1
    (line,) = episodes(caplog)
    assert "touche A" in line and "débloqué : oui" in line


def test_empty_a_reading_never_blocks_an_exit_everywhere(monkeypatch, caplog):
    no_suns(monkeypatch)
    screen = Screen()
    base = load_frame("hors_combat")
    game = ExitsGame(screen, base, with_a_outline(), unlocks=False)
    traveler = Traveler(game, TravelSettings(mode="test", settle=0, hide_players_key=""), World(None))
    assert traveler._screen_exits() == {} and traveler.a_unheard
    noted = []
    monkeypatch.setattr(traveler, "_note_blocked", lambda *args, **kwargs: noted.append(args))
    monkeypatch.setattr(traveler, "exits", lambda frame, wanted=None, complete=False: {})
    label = read_map_label(base)
    assert traveler.go(base, label, "haut") is None
    assert noted == [] and (traveler.key(label), "haut") in traveler.blocked_here  # ce trajet seulement


# --- 5. Erreurs et OVERTIME --------------------------------------------------------------------------------------------

def test_errors_out_of_combat_stop_the_bot_cleanly_at_the_third(caplog):
    screen = Screen()
    a = member("A", screen, load_frame("hors_combat"))
    bot = team_bot(a)

    def boom():
        raise RuntimeError("panne")

    bot.step = boom
    with caplog.at_level(logging.ERROR):
        bot.safe_step()
        bot.safe_step()
        assert not bot.finished
        bot.safe_step()
    assert bot.finished and "erreurs hors combat" in bot._stop_reason
    assert a.game.recorder.saved.count("erreur") == 1  # au plus une capture par cause et par fenêtre en 5 min


def test_errors_during_a_fight_never_stop_the_bot():
    screen = Screen()
    a = member("A", screen, load_frame("combat_mon_tour"))
    bot = team_bot(a)

    def boom():
        bot._in_fight = True
        raise RuntimeError("panne en combat")

    bot.step = boom
    for _ in range(7):
        bot.safe_step()
    assert not bot.finished and bot._round_due  # combat repris au tour des fenêtres
    assert "erreurs pendant un combat" in bot._stop_reason  # fin de session après ce combat


def test_stop_request_is_not_swallowed():
    screen = Screen()
    bot = team_bot(member("A", screen, load_frame("hors_combat")))

    def stop():
        raise BotStopped("touche F12")

    bot.step = stop
    with pytest.raises(BotStopped):
        bot.safe_step()


# --- 7. Surveillance du progrès ----------------------------------------------------------------------------------------


def test_verified_empty_map_in_map_mode_is_waiting_for_respawn_not_a_stall():
    class Engaged:
        none_found, missed, keyboard_ok = True, False, True
        settings = EngageSettings()

        def engage(self):
            return False

    screen = Screen()
    a = member("A", screen, load_frame("hors_combat"))
    bot = team_bot(a)
    bot.engager, bot.roamer = Engaged(), None
    bot._together_checked, bot._last_round, bot._next_engage_at = True, time.monotonic(), 0.0
    stalled(bot, STALL_LAST_RESORT + 10)
    bot._out_of_combat(a, load_frame("hors_combat"))
    assert time.monotonic() - bot._progress_at < 5 and not bot.watchdog.check()
    bot.engager.keyboard_ok, bot._next_engage_at = False, 0.0  # clavier sourd : rien ne prouve la carte vide
    stalled(bot, STALL_DIAGNOSE + 10)
    bot._out_of_combat(a, load_frame("hors_combat"))
    assert time.monotonic() - bot._progress_at > STALL_DIAGNOSE

def stalled(bot, seconds):
    bot._progress_at = time.monotonic() - seconds


def test_no_diagnostic_while_the_team_makes_progress():
    screen = Screen()
    bot = team_bot(member("A", screen, with_a_outline()))
    assert bot.watchdog.enabled and not bot.watchdog.check()
    stalled(bot, STALL_DIAGNOSE + 10)
    bot._crossed("droite", (27, 9), None, None)  # changement de carte : un progrès
    assert not bot.watchdog.check()


def test_diagnostic_closes_a_known_window_of_a_follower():
    screen = Screen()
    a = member("A", screen, with_a_outline())
    # Un regard pour vérifier que personne n'est en combat, un pour le diagnostic, puis la fenêtre fermée.
    b = member("B", screen, load_frame("fin_de_combat"), load_frame("fin_de_combat"), load_frame("hors_combat"))
    bot = team_bot(a, b)
    stalled(bot, STALL_DIAGNOSE + 1)
    assert bot.watchdog.check()
    (click,) = [e for e in events(screen, "click")]
    assert click[0] == "B" and near(click[2], (1675, 590)) and bot.watchdog.fruitless == 0


def test_two_fruitless_diagnostics_then_the_last_resort_stops_without_a_pause_control():
    screen = Screen()
    a, b = member("A", screen, with_a_outline()), member("B", screen, with_a_outline())
    bot = team_bot(a, b)
    stalled(bot, STALL_DIAGNOSE + 1)
    assert bot.watchdog.check() and bot.watchdog.fruitless == 1
    assert not bot.watchdog.check()  # diagnostic récent : la boucle travaille
    bot.watchdog._diagnosed_at -= STALL_DIAGNOSE
    assert bot.watchdog.check() and bot.watchdog.fruitless == FRUITLESS_LIMIT
    assert bot.watchdog.check()  # dernier recours
    assert bot.finished and bot._stop_reason == "aucun progrès"
    assert a.game.recorder.saved.count("sans_progres") == 1 and b.game.recorder.saved.count("sans_progres") == 1


def test_deaf_keyboard_found_by_the_diagnostic(caplog):
    screen = Screen()
    a = member("A", screen, load_frame("hors_combat"))  # A n'affiche aucune sortie
    bot = team_bot(a)
    stalled(bot, STALL_DIAGNOSE + 1)
    with caplog.at_level(logging.INFO):
        assert bot.watchdog.check()
    assert [e[2] for e in events(screen, "press")] == ["a", "a"] and len(events(screen, "click")) == 1
    (line,) = episodes(caplog)
    assert "diagnostic" in line and "débloqué : non" in line


def test_never_a_diagnostic_during_a_fight():
    screen = Screen()
    a = member("A", screen, with_a_outline())
    b = member("B", screen, load_frame("combat_mon_tour"))
    bot = team_bot(a, b)
    stalled(bot, 10 * STALL_DIAGNOSE)
    assert bot.watchdog.check() and bot._round_due and not bot.finished
    assert events(screen, "click") == [] and events(screen, "press") == []


class PauseControl:
    pause_key_name = "f9"

    def __init__(self) -> None:
        self.paused_total = 0.0
        self.reasons: list[str] = []
        self.waits = 0

    def pause(self, reason):
        self.reasons.append(reason)

    def resume(self, reason):
        self.reasons.append(f"reprise : {reason}")

    def wait_paused(self, seconds):
        self.waits += 1
        return False  # l'utilisateur a levé la pause (F9)

    @contextmanager
    def lifted(self):
        yield

    def sleep(self, seconds):
        pass


def test_last_resort_pauses_like_f9_then_resumes():
    screen = Screen()
    a = member("A", screen, with_a_outline())
    bot = team_bot(a)
    control = bot.watchdog.control = PauseControl()
    stalled(bot, 301)
    assert bot.watchdog.check()
    assert control.reasons == ["aucun progrès"] and not bot.finished and bot._round_due
    assert time.monotonic() - bot._progress_at < 5  # reprise : la surveillance repart de zéro


def test_time_paused_by_the_user_is_not_lost_time():
    screen = Screen()
    bot = team_bot(member("A", screen, with_a_outline()))
    control = bot.watchdog.control = PauseControl()
    bot.watchdog._paused_seen = 0.0
    stalled(bot, STALL_DIAGNOSE + 30)
    control.paused_total = 60.0  # F9 pendant une minute
    assert not bot.watchdog.check()


def test_no_watchdog_when_the_bot_does_nothing_out_of_combat():
    screen = Screen()
    bot = team_bot(member("A", screen, load_frame("hors_combat")), engage=False)  # --combat-only
    assert not bot.watchdog.enabled
    stalled(bot, 1000)
    assert not bot.watchdog.check()


def test_recovery_settings():
    assert RecoverySettings.from_settings(load_settings()).last_resort == "pause"
    assert RecoverySettings.from_settings({"recovery": {"last_resort": "stop"}}).last_resort == "stop"
    with pytest.raises(ValueError):
        RecoverySettings.from_settings({"recovery": {"last_resort": "attendre"}})


def test_capture_once_per_cause_and_window_every_five_minutes(monkeypatch):
    clock = [5000.0]
    monkeypatch.setattr("bot.brain.recovery.time.monotonic", lambda: clock[0])
    game = KeyboardGame(None, None, None)
    image = np.zeros((4, 4, 3), np.uint8)
    assert capture_once(game, image, "erreur") and capture_once(game, image, "autre")
    assert capture_once(game, image, "erreur") is None
    clock[0] += 301
    assert capture_once(game, image, "erreur")
    assert game.recorder.saved == ["erreur", "autre", "erreur"]


def test_keyboard_probe_by_the_exits_key():
    base = load_frame("hors_combat")
    assert keyboard_answers(KeyboardGame(base, base, with_a_outline()), "a")
    assert not keyboard_answers(KeyboardGame(base, base, with_a_outline(), deaf=True), "a")


def test_stop_control_pause_by_the_bot_and_lifted_for_a_look():
    control = StopControl("f12", "f9", corner=False)
    control.pause("aucun progrès")
    assert control.paused
    with control.lifted():
        control.check()  # ne bloque pas : le bot regarde
    assert control.paused and control.wait_paused(0.05)
    control.resume("test")
    assert not control.paused and not control.wait_paused(1.0)


@pytest.mark.skipif(not __import__("bot.perception.chat", fromlist=["x"]).ocr_available(), reason="OCR de Windows absent")
def test_level_read_in_the_level_up_window_before_closing_it():
    # 23/10 : engage.max_level « auto » : level_ratio x niveau cumulé de l'équipe, niveaux retenus à chaque passage.
    from bot.brain.popups import LEVEL_UP, read_level_up

    frame = load_frame("niveau_14_par_dessus_resultat_calanques")
    match = find_known_popup(frame, load_popup_specs())
    assert match.spec.name == LEVEL_UP and read_level_up(frame, match.box) == 14


def test_level_limit_follows_the_team_cumulated_level():
    from bot.brain.engage import Engager, EngageSettings

    settings = EngageSettings.from_settings({"engage": {"max_level": "auto", "level_ratio": 0.5}})
    engager = Engager.__new__(Engager)
    engager.settings = settings
    engager.team_levels = [14, 13, 15, None]
    assert engager.level_limit() == 28  # un niveau manque (le principal, absent de sa liste d'amis) : la moyenne (25/10)
    engager.team_levels = [14, None, None, None]
    assert engager.level_limit() == 70  # trop peu de niveaux connus : le plafond fixe
    engager.team_levels = [14, 13, 15, 14]
    assert engager.level_limit() == 28
