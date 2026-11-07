"""Entrée du donjon d'Incarnam (18/10, captures de l'utilisateur) : bot/team/dungeon.py."""

from bot.perception.popups import find_npc_dialog
from bot.team.dungeon import DungeonEntry, load_dungeons
from conftest import load_frame
from tests.test_team import Screen, events, member

OUTSIDE = "donjon_incarnam_entree_6_5"  # grotte en 6,5 (Divers)
PLATES = "donjon_incarnam_salle_dalles"  # salle d'entrée : dalles, soleil libéré
NPC_ROOM = "donjon_incarnam_salle_master_donge"  # salle de Ma'ster Donge
BUBBLE = "donjon_incarnam_bulle_master_donge"  # sa bulle : « Voici la clef de la porte. »


def incarnam():
    return load_dungeons()["incarnam"]


def unknown_rooms():
    from dataclasses import replace

    return replace(incarnam(), rooms=None)


def test_incarnam_dungeon_described_cell_by_cell():
    spec = incarnam()
    assert spec.outside == (6, 5) and spec.entrance == (16, 14) and spec.plate == (18, 20) and spec.sun == (23, 5)
    assert spec.npc == (20, 14) and spec.reply == "Voici la clef de la porte"
    assert spec.exit_npc == (21, 11) and spec.exit_reply == "A la sortie" and spec.exit_click == "right"
    assert spec.rooms == 4


def test_whole_team_takes_the_cave_sun_and_each_decor_changes():
    screen = Screen()
    outside, inside = load_frame(OUTSIDE), load_frame(PLATES)
    team = [member("A", screen, outside, inside), member("B", screen, outside, inside)]
    assert DungeonEntry(team, incarnam())._all_take(team, (16, 14), "l'entrée")
    clicks = [e[2] for e in events(screen, "click")]
    x, y = outside.grid.center((16, 14))
    assert len(clicks) == 2 and all(abs(cx - x) <= 25 and abs(cy - y) <= 15 for cx, cy in clicks)
    # Les autres joueurs cachés (touche E tenue) pendant chaque clic : 19/10, un marchand sous le soleil.
    assert [e[2] for e in events(screen, "hold")] == ["e", "e"]


def test_member_still_outside_clicks_the_sun_again_then_gives_up():
    screen = Screen()
    outside = load_frame(OUTSIDE)
    stuck = member("A", screen, outside)  # le décor ne change jamais
    assert not DungeonEntry([stuck], incarnam())._all_take([stuck], (16, 14), "l'entrée")
    assert len(events(screen, "click")) == 2  # un second clic, à un autre endroit du soleil, puis abandon


def test_key_given_through_the_npc_bubble_then_the_door_is_passed():
    screen = Screen()
    room, bubble, beyond = load_frame(NPC_ROOM), load_frame(BUBBLE), load_frame(PLATES)
    hero = member("A", screen, room, bubble, bubble, beyond)
    assert DungeonEntry([hero], incarnam())._give_key(hero)
    npc_click, reply_click = (e[2] for e in events(screen, "click"))
    nx, ny = room.grid.center((20, 14))
    assert abs(npc_click[0] - nx) <= 10 and npc_click[1] < ny  # sur le corps du PNJ, au-dessus de sa case
    assert abs(reply_click[1] - 456) <= 8  # la ligne « Voici la clef de la porte. »


def test_no_key_reply_is_a_failure_without_clicking_elsewhere():
    screen = Screen()
    room = load_frame(NPC_ROOM)
    hero = member("A", screen, room)  # ni menu ni bulle : rien ne s'ouvre
    assert not DungeonEntry([hero], incarnam())._give_key(hero)
    assert len(events(screen, "click")) == 1


def test_npc_menu_parler_found_then_the_bubble_reply():
    # 18/10 : clic sur Ma'ster Donge -> menu « Ma'ster Donge / Parler » (pas lu dans un cadre serré, Vertdepomme).
    screen = Screen()
    room, menu, bubble, beyond = (load_frame(n) for n in (NPC_ROOM, "donjon_incarnam_menu_parler", BUBBLE, PLATES))
    hero = member("A", screen, room, menu, menu, bubble, bubble, beyond)
    assert DungeonEntry([hero], incarnam())._give_key(hero)
    npc, talk, reply = (e[2] for e in events(screen, "click"))
    assert 1521 <= talk[0] <= 1680 and 363 <= talk[1] <= 399  # dans l'entrée « Parler » du petit menu (30/10)


def test_team_leaves_through_the_exit_reply_with_a_right_click():
    # 18/10 : clic droit sur Ma'ster Donge (salle de fin) ; « A la sortie. » (pas la question, qui dit aussi « à la
    # sortie ») ; dehors en 6,5.
    screen = Screen()
    room, bubble, outside = (load_frame(n) for n in ("donjon_incarnam_salle_fin", "donjon_incarnam_bulle_sortie",
                                                     OUTSIDE))
    hero = member("A", screen, room, bubble, bubble, outside)
    assert DungeonEntry([hero], incarnam()).leave()
    (npc,), (reply,) = [e[2] for e in events(screen, "click_right")], [e[2] for e in events(screen, "click")]
    assert abs(reply[1] - 434) <= 8  # « A la sortie. », sous la question


def test_rooms_fought_until_two_empty_searches_out_of_combat():
    from bot.team.dungeon import DungeonFarm

    class FakeBot:
        def __init__(self, team):
            self.members, self.leader, self.engager = team, team[0], None
            self.finished, self._round_due, self._stop_reason = False, False, ""
            self.fights, self._empty_searches, self.steps = 0, 0, 0

        def step(self):
            self.steps += 1
            if self.steps <= 3:
                self.fights += 1  # trois salles
            else:
                self._empty_searches += 1

    screen = Screen()
    room = load_frame("donjon_incarnam_salle_fin")
    bot = FakeBot([member("A", screen, room)])
    assert DungeonFarm(bot, unknown_rooms(), 1)._fight_rooms()  # nombre de salles inconnu
    assert bot.fights == 3 and bot._empty_searches == 2


def test_reply_read_again_while_the_bubble_text_is_written():
    # 18/10 : bulle vue avant son texte ; Dinarpepe et Vieenjaune, une clef en poche, restés devant la porte.
    from bot.perception.frame import Frame

    screen = Screen()
    room, bubble, beyond = load_frame(NPC_ROOM), load_frame(BUBBLE), load_frame(PLATES)
    blank = bubble.image.copy()
    x, y = find_npc_dialog(bubble)
    blank[y + 30:y + 300, 540:x - 30] = blank[y + 30, x - 40]  # le jaune de la bulle, sans ses lignes
    empty = Frame.of(blank)
    hero = member("A", screen, room, empty, empty, bubble, bubble, beyond)
    assert DungeonEntry([hero], incarnam())._give_key(hero)
    assert abs(events(screen, "click")[-1][2][1] - 456) <= 8


def test_room_with_a_missed_group_is_not_the_end_room():
    # 18/10 : deux recherches sans groupe attaquable, mais un groupe raté encore là (salle 2) : pas la salle de fin.
    from bot.team.dungeon import DungeonFarm

    class Engager:
        groups_seen = 1

        def new_map(self):
            pass

    class FakeBot:
        def __init__(self, team):
            self.members, self.leader, self.engager = team, team[0], Engager()
            self._round_due, self._stop_reason = False, ""
            self.fights, self._empty_searches, self.steps = 0, 0, 0

        @property
        def finished(self):
            return self.steps >= 6

        def step(self):
            self.steps += 1
            self._empty_searches += 1
            if self.steps == 4:
                self.engager.groups_seen = 0  # le groupe battu ; la salle suivante est vide

    screen = Screen()
    bot = FakeBot([member("A", screen, load_frame("donjon_incarnam_salle_fin"))])
    assert DungeonFarm(bot, unknown_rooms(), 1)._fight_rooms()
    assert bot.steps == 4


def test_exit_right_click_opening_a_menu_clicks_nothing_in_it():
    # 18/10 : clic droit sans PNJ dessous -> menu de Flash ; son milieu cliqué ouvrait les Options du jeu.
    screen = Screen()
    room, menu = load_frame("donjon_incarnam_salle_fin"), load_frame("donjon_incarnam_menu_parler")
    hero = member("A", screen, room, menu)
    assert not DungeonEntry([hero], incarnam()).leave()
    # Deux clics droits : le second essai de la sortie (30/10) ; jamais de clic dans le menu : refermé d'un clic sur le
    # fond beige sous la barre de sorts (31/10, close_any_menu), hors de la carte.
    from bot.perception.popups import find_small_menu

    x0, y0, x1, y1 = find_small_menu(menu)
    assert len(events(screen, "click_right")) == 2
    assert all(not (x0 <= e[2][0] <= x1 and y0 <= e[2][1] <= y1) and not menu.layout.on_map(e[2])
               for e in events(screen, "click"))


def test_options_window_closed_before_the_run():
    # 18/10 : « Options » restée ouverte chez Koalaloupour (clic dans le menu de Flash) : fermée par son « Fermer ».
    from bot.team.dungeon import DungeonFarm

    class FakeBot:
        def __init__(self, team):
            self.members = team

    screen = Screen()
    options, room = load_frame("fenetre_options"), load_frame("donjon_incarnam_salle_fin")
    bot = FakeBot([member("A", screen, options, room)])
    DungeonFarm(bot, incarnam(), 1)._close_windows()
    (close,) = [e[2] for e in events(screen, "click")]
    assert abs(close[0] - 1467) <= 30 and abs(close[1] - 784) <= 12


def test_end_room_only_after_the_last_room_fight():
    # Utilisateur, 18/10 : retenir le nombre de salles. Reprise en salle 2 sur 4 : trois combats, puis la salle de fin ;
    # des recherches sans groupe attaquable entre deux (groupe raté) ne la font pas croire atteinte.
    from bot.team.dungeon import DungeonFarm

    class FakeBot:
        def __init__(self, team):
            self.members, self.leader, self.engager = team, team[0], None
            self.finished, self._round_due, self._stop_reason = False, False, ""
            self.fights, self._empty_searches, self.steps = 0, 0, 0

        def step(self):
            self.steps += 1
            if self.steps in (3, 6, 7):
                self.fights += 1
            else:
                self._empty_searches += 1  # groupe raté, ou recherche pendant un affichage

    screen = Screen()
    bot = FakeBot([member("A", screen, load_frame("donjon_incarnam_salle_fin"))])
    assert DungeonFarm(bot, incarnam(), 1)._fight_rooms(first=2)
    assert bot.steps == 7 and bot.fights == 3


def test_late_member_takes_the_entrance_then_the_whole_team_goes_on(monkeypatch):
    # 19/10 : Pesopepe resté dehors, les 7 autres dans la salle des dalles : « incarnam:entree:Pesopepe ».
    class Named(str):
        name = property(lambda self: str(self))

    entry = DungeonEntry([Named("Koalaloupour"), Named("Pesopepe")], incarnam())
    taken, plates, keys = [], [], []
    monkeypatch.setattr(entry, "_all_take", lambda members, cell, what: taken.append((list(members), cell)) or True)
    monkeypatch.setattr(entry, "_step_on_plate", plates.append)
    monkeypatch.setattr(entry, "_through_npc", lambda members: keys.extend(members) or True)
    assert entry.enter("entree", {"pesopepe"})
    assert taken == [(["Pesopepe"], (16, 14)), (entry.members, (23, 5))]
    assert plates == ["Koalaloupour"] and keys == entry.members


def test_trophy_rewards_card_closed_by_its_cross():
    # 19/10 : carte « récompenses de trophées » posée sur la carte de Pesopepe à la connexion ; fermée par sa croix.
    from bot.perception.popups import find_trophy_cross
    from bot.team.dungeon import DungeonFarm

    class FakeBot:
        def __init__(self, team):
            self.members = team

    screen = Screen()
    card, outside = load_frame("recompenses_trophees"), load_frame(OUTSIDE)
    assert find_trophy_cross(outside) is None
    DungeonFarm(FakeBot([member("A", screen, card, outside)]), incarnam(), 1)._close_windows()
    (close,) = [e[2] for e in events(screen, "click")]
    assert abs(close[0] - 1323) <= 4 and abs(close[1] - 709) <= 4


def test_each_member_located_by_its_decor():
    screen = Screen()
    outside, plates, npc_room, end = (load_frame(n) for n in (OUTSIDE, PLATES, NPC_ROOM, "donjon_incarnam_salle_fin"))
    entry = DungeonEntry([], incarnam())
    assert entry.locate(member("A", screen, outside)) == "entree"
    assert entry.locate(member("B", screen, load_frame("recompenses_trophees"))) == "entree"  # marchands, trophées
    assert entry.locate(member("C", screen, plates)) == "dalle"
    assert entry.locate(member("D", screen, npc_room)) == "pnj"
    assert entry.locate(member("E", screen, end)) is None  # déjà dans les salles


def test_team_split_is_brought_together(monkeypatch):
    # Utilisateur, 19/10 : le bot ramène tout le monde au même endroit. Pesopepe dehors, Koalaloupour dans la salle des
    # dalles : Pesopepe seul prend la grotte, puis la dalle (le principal), le soleil et le PNJ pour les deux.
    screen = Screen()
    leader = member("Koalaloupour", screen, load_frame(PLATES))
    late = member("Pesopepe", screen, load_frame(OUTSIDE))
    entry = DungeonEntry([leader, late], incarnam())
    taken, plates, keys = [], [], []
    monkeypatch.setattr(entry, "_all_take", lambda members, cell, what: taken.append(([m.name for m in members], cell))
                        or True)
    monkeypatch.setattr(entry, "_step_on_plate", lambda m: plates.append(m.name))
    monkeypatch.setattr(entry, "_through_npc", lambda members: keys.extend(m.name for m in members) or True)
    assert entry.enter()
    assert taken == [(["Pesopepe"], (16, 14)), (["Koalaloupour", "Pesopepe"], (23, 5))]
    assert plates == ["Koalaloupour"] and keys == ["Koalaloupour", "Pesopepe"]


def test_member_elsewhere_stops_the_entry():
    screen = Screen()
    team = [member("A", screen, load_frame(OUTSIDE)), member("B", screen, load_frame("donjon_incarnam_salle_fin"))]
    assert not DungeonEntry(team, incarnam()).enter()
    assert not events(screen, "click")


def test_every_member_left_outside_is_retried_and_named(caplog):
    # 19/10 : Pesopepe et Pombro restés dehors ; seul Pesopepe signalé, le bot arrêté sans retenter Pombro.
    screen = Screen()
    outside = load_frame(OUTSIDE)
    team = [member("Pesopepe", screen, outside), member("Pombro", screen, outside)]
    with caplog.at_level("WARNING"):
        assert not DungeonEntry(team, incarnam())._all_take(team, (16, 14), "l'entrée")
    assert len(events(screen, "click")) == 4  # deux clics chacun
    assert "Pesopepe, Pombro pas passé(s) par l'entrée" in caplog.text


def test_bouftou_npc_is_outside_and_members_are_located_by_the_server():
    # 30/10 : Altaïr dehors en 2,-34 (carte 1856), sans grotte ni dalle ; chacun situé par la carte du serveur.
    from bot.connection.network import ClientFeed

    spec = load_dungeons()["bouftou"]
    assert spec.outside == (2, -34) and spec.entrance is None and spec.plate is None and spec.sun is None
    assert spec.npc == (18, 14) and spec.reply == "Donner la clé des bouftous" and spec.npc_map == 1856
    screen = Screen()
    entry = DungeonEntry([], spec)
    there, inside = member("A", screen, load_frame(OUTSIDE)), member("B", screen, load_frame(OUTSIDE))
    there.game.net, inside.game.net = ClientFeed("A"), ClientFeed("B")
    there.game.net.feed("GDM|1856|0901081440|00")
    inside.game.net.feed("GDM|2073|0706131721|00")
    assert entry.locate(there) == "pnj"
    assert entry.locate(inside) is None  # déjà dans les salles


def _bouftou_entry(monkeypatch, answers):
    """Entrée Bouftou de 3 personnages, bulles « ouvertes » sans écran : réponses proposées par le serveur à chacun."""
    from types import SimpleNamespace

    from bot.connection.network import ClientFeed

    team = []
    for name in answers:
        net = ClientFeed(name)
        team.append(SimpleNamespace(name=name, game=SimpleNamespace(net=net)))
    entry = DungeonEntry(team, load_dungeons()["bouftou"])
    opened, replied, closed = [], [], []

    def open_(member, npc, button):
        entry._opened_at[member.name] = 0.0
        member.game.net.feed("DQ660|" + ";".join(str(a) for a in answers[member.name]))
        opened.append(member.name)
        return ("salle", "bulle")

    monkeypatch.setattr(entry, "_open", open_)
    monkeypatch.setattr(entry, "_reply", lambda m, before, shown, answer, gone: replied.append((m.name, answer)) or True)
    monkeypatch.setattr(entry, "_close_bubble", lambda m: closed.append(m.name))
    return entry, team, opened, replied, closed


def test_nobody_enters_when_one_member_cannot(monkeypatch):
    # 31/10, utilisateur : « si tout le groupe n'a pas de clé il ne faut pas continuer et faire rentrer solo » (ce matin,
    # Fourboun entré seul). Le 3e n'a ni clé ni trousseau : bulles refermées, personne ne répond.
    entry, team, opened, replied, closed = _bouftou_entry(
        monkeypatch, {"Fourboun": (564, 6602), "Eldieso": (564, 6602), "Walitcoin": ()})
    assert not entry._through_npc(team)
    assert opened == ["Fourboun", "Eldieso", "Walitcoin"] and replied == []
    assert closed == ["Fourboun", "Eldieso", "Walitcoin"]  # toutes les bulles refermées


def test_entry_answer_chosen_from_the_server_key_then_keyring_only_if_allowed(monkeypatch, caplog):
    # « Donner la clé » (564) proposée seulement à qui a la clé. Le trousseau (6602) ne sert qu'une fois par semaine
    # (31/10, utilisateur) : jamais choisi tout seul ; sans lui, Eldieso sans clé bloque l'entrée, et un message le dit.
    answers = {"Fourboun": (564, 6602), "Eldieso": (6602,)}
    entry, team, opened, replied, closed = _bouftou_entry(monkeypatch, answers)
    assert not entry._through_npc(team) and replied == []
    assert "À l'utilisateur : Donjon Bouftou : Eldieso sans clé" in caplog.text
    entry, team, opened, replied, closed = _bouftou_entry(monkeypatch, answers)
    entry.keyring = True  # donjon.trousseau : oui
    assert entry._through_npc(team)
    assert replied == [("Fourboun", "Donner la clé des bouftous"), ("Eldieso", "Utiliser le trousseau de clefs")]


def test_a_member_left_in_another_room_is_found_by_the_server():
    # 31/10 : Walitcoin resté dans la salle 8 (combat pas rejoint) ; les 7 autres téléportés dans la suivante.
    from types import SimpleNamespace

    from bot.connection.network import ClientFeed
    from bot.team.dungeon import DungeonFarm

    team = []
    for name, room in (("Fourboun", 2082), ("Eldieso", 2082), ("Walitcoin", 2081)):
        net = ClientFeed(name)
        net.feed(f"GDM|{room}|0706131721|aa")
        team.append(SimpleNamespace(name=name, game=SimpleNamespace(net=net)))
    bot = SimpleNamespace(members=team, leader=team[0], _settings={})
    farm = DungeonFarm(bot, load_dungeons()["bouftou"], 1)
    assert farm._left_behind(wait=0) == ["Walitcoin"]
    team[2].game.net.feed("GDM|2082|0706131721|aa")
    assert farm._left_behind(wait=0) == []


def test_champs_dungeon_entry_as_observed_and_exit_by_a_cell():
    # 05/11 (observé au réseau avec Fourboun) : Mawy dehors en 7,-25, case 187 ; réponses 2802 et 6615. Sortie : une
    # case de la salle de fin, sans PNJ.
    spec = load_dungeons()["champs"]
    assert spec.outside == (7, -25) and spec.npc == (26, 12) and spec.npc_map == 7453
    assert spec.reply_id == 2802 and spec.keyring_id == 6615 and spec.rooms == 7 and spec.exit_cell == (27, 17)
    screen = Screen()
    room, outside = load_frame(PLATES), load_frame(OUTSIDE)
    team = [member("A", screen, room, outside), member("B", screen, room, outside)]
    assert DungeonEntry(team, spec).leave()
    assert len(events(screen, "click")) == 2  # chacun sur la case de sortie, pas de bulle


def test_room_places_and_waves_strongest_in_the_smallest():
    # 05/11, utilisateur : à 8, le bot passe les salles à peu de places ; « les plus haut level sont plus forts donc
    # peuvent être moins par salle ».
    from bot.team.dungeon import plan_waves, room_places

    assert [room_places("Donjon des champs", m) for m in (9768, 9770, 9772)] == [9, 3, 4]
    assert room_places("Donjon des champs", 1) is None and room_places("Inconnu", 9770) is None
    levels = {"A": 50, "B": 48, "C": 45, "D": 39, "E": 36, "F": 31, "G": 30, "H": 30}
    assert plan_waves(levels, 3) == [["A", "B"], ["C", "D", "E"], ["F", "G", "H"]]
    assert plan_waves(levels, 4) == [["A", "B", "C", "D"], ["E", "F", "G", "H"]]
    assert plan_waves(levels, 9) == [list("ABCDEFGH")]


def test_only_the_wave_joins_the_fight(monkeypatch):
    # Combat en vagues : seuls les membres de la vague rejoignent (TeamBot.fighting_only).
    from tests.test_team import team_bot

    screen = Screen()
    frame = load_frame("preparation")
    a, b, c = (member(n, screen, frame) for n in "ABC")
    bot = team_bot(a, b, c)
    asked = []
    bot.joiner.ask = lambda m, hint: asked.append(m.name) or True
    bot.joiner.wait_placement = lambda m, deadline: True
    bot._wait_combat = lambda host: True
    bot.fighting_only = {"A", "B"}
    assert bot._placement(a) == [a, b] and asked == ["B"]


def test_waves_fight_one_after_the_other_each_launched_by_its_strongest(monkeypatch):
    from types import SimpleNamespace

    from bot.team import dungeon
    from bot.team.dungeon import DungeonFarm

    screen = Screen()
    placement = load_frame("preparation")
    members = [member(n, screen, placement) for n in ("A", "B", "C", "D", "E")]
    levels = {"A": 50, "B": 48, "C": 45, "D": 39, "E": 36}

    class FakeEngager:
        def __init__(self, game, settings):
            self.game = game

        def engage(self):
            return True

    monkeypatch.setattr("bot.brain.engage.Engager", FakeEngager)
    fought = []
    bot = SimpleNamespace(members=members, engager=SimpleNamespace(settings=None), _round_due=False, step=lambda: None,
                          fighting_only=None)
    bot._fight = lambda host: fought.append((host.name, sorted(bot.fighting_only)))
    farm = DungeonFarm.__new__(DungeonFarm)
    farm.bot, farm.spec = bot, SimpleNamespace(name="Donjon des champs")
    monkeypatch.setattr(farm, "_levels", lambda: levels)
    assert farm._fight_in_waves(3) == 2
    assert fought == [("A", ["A", "B"]), ("C", ["C", "D", "E"])] and bot.fighting_only is None


def test_member_in_front_of_an_outside_npc_located_by_the_map_label_without_the_server_map(monkeypatch):
    # 05/11 : fenêtres déjà connectées, pas de changement de carte : carte du serveur inconnue, les 8 crus ailleurs.
    from types import SimpleNamespace

    from bot.connection.network import ClientFeed
    from bot.perception import maplabel

    spec = load_dungeons()["champs"]
    screen = Screen()
    hero = member("A", screen, load_frame(OUTSIDE))
    hero.game.net = ClientFeed("A")
    hero.game.net.heard_at = __import__("time").monotonic()
    monkeypatch.setattr(maplabel, "read_map_label", lambda frame: SimpleNamespace(coords=(7, -25)))
    assert DungeonEntry([hero], spec).locate(hero) == "pnj"
    monkeypatch.setattr(maplabel, "read_map_label", lambda frame: SimpleNamespace(coords=(4, -16)))
    assert DungeonEntry([hero], spec).locate(hero) is None


def test_members_left_in_the_previous_room_are_found_and_catch_up(monkeypatch):
    # 05/11 : bot arrêté (F12) pendant la vague 1 de la salle 5, relancé : la vague 2 encore en salle 5 (9772), les
    # autres en salle 6 (9773). Ceux d'avant font leur salle (en vagues de 4 au plus), puis rejoignent.
    from types import SimpleNamespace

    from bot.team.dungeon import DungeonFarm

    screen = Screen()
    out = load_frame("hors_combat")
    members = [member(n, screen, out) for n in "ABCDEFGH"]
    for m in members:
        m.game.net = SimpleNamespace(map_id=9773 if m.name in "ABCD" else 9772)
    bot = SimpleNamespace(members=members, leader=members[0], _round_due=False)
    farm = DungeonFarm.__new__(DungeonFarm)
    farm.bot, farm.spec = bot, SimpleNamespace(name="Donjon des champs")
    room, behind = farm._split_team()
    assert room == 9772 and [m.name for m in behind] == list("EFGH")
    fought = []
    monkeypatch.setattr(farm, "_levels", lambda: {n: 50 - i for i, n in enumerate("ABCDEFGH")})
    monkeypatch.setattr(farm, "_wave_fight", lambda wave, i, n: fought.append([m.name for m in wave]) or True)
    assert farm._catch_up(behind, 4) == 1 and fought == [list("EFGH")]
    for m in members:
        m.game.net.map_id = 9773
    assert farm._split_team() is None


def test_members_without_an_announced_map_after_a_small_room_are_left_behind(monkeypatch):
    # 05/11 : relancé, la vague 2 sans carte annoncée (pas de changement de carte depuis), le principal en salle 6.
    from types import SimpleNamespace

    from bot.team.dungeon import DungeonFarm

    screen = Screen()
    out = load_frame("hors_combat")
    members = [member(n, screen, out) for n in "ABCDEFGH"]
    for m in members:
        m.game.net = SimpleNamespace(map_id=9773 if m.name in "ABCD" else None)
    bot = SimpleNamespace(members=members, leader=members[0], _round_due=False)
    farm = DungeonFarm.__new__(DungeonFarm)
    farm.bot, farm.spec = bot, SimpleNamespace(name="Donjon des champs")
    room, behind = farm._split_team()
    assert room == 9772 and [m.name for m in behind] == list("EFGH")


def test_team_away_from_the_entrance_walks_back_before_each_pass(monkeypatch):
    # 05/11 (journal 212413) : sortie des Champs par la case (27,17), équipe déposée en 7,-26 (7452) ; passage suivant
    # pas fait (« ni devant l'entrée ni dans les premières salles »). Ramenée devant Mawy avant chaque passage, le
    # premier compris ; jamais depuis une salle du donjon ni quand elle est devant le PNJ.
    import time
    from types import SimpleNamespace

    from bot.connection.network import ClientFeed
    from bot.team import pods
    from bot.team.dungeon import DungeonFarm

    trips = []
    monkeypatch.setattr(pods, "team_trip", lambda bot, goal, why: trips.append(goal) or True)

    def team(*maps):
        members = []
        for index, room in enumerate(maps):
            net = ClientFeed(f"P{index}")
            net.feed(f"GDM|{room}|0706131721|aa")
            net.heard_at = time.monotonic()
            members.append(SimpleNamespace(name=f"P{index}", game=SimpleNamespace(net=net)))
        return members

    for maps, expected in (((7452, 7452), [(7, -25)] * 3), ((7453, 7453), []), ((7452, 7453), []),
                           ((7452, 9770), [])):
        trips.clear()
        members = team(*maps)
        farm = DungeonFarm(SimpleNamespace(members=members, leader=members[0], _settings={}),
                           load_dungeons()["champs"], 3)
        monkeypatch.setattr(farm, "_close_windows", lambda: None)
        monkeypatch.setattr(farm, "_pods_ready", lambda: True)
        monkeypatch.setattr(farm, "_fight_rooms", lambda first=1: True)
        monkeypatch.setattr(farm.entry, "enter", lambda *args: True)
        monkeypatch.setattr(farm.entry, "leave", lambda: True)
        assert farm.run() == 3 and trips == expected, maps
    assert not DungeonFarm(SimpleNamespace(members=team(7452), leader=None, _settings={}),
                           load_dungeons()["incarnam"], 1).entry.away(team(7452)[0])  # pas de carte du PNJ : pas sûr


def test_session_time_limit_never_cuts_a_pass(monkeypatch):
    # 06/11 : --minutes arrêtait l'équipe au milieu du donjon (clé donnée, salles à moitié faites). Un passage n'est
    # commencé que s'il a le temps de finir ; pendant le passage, la limite de la session est suspendue.
    import time
    from types import SimpleNamespace

    from bot.team.dungeon import PASS_ESTIMATE, DungeonFarm

    def farm_with(deadline, start=None):
        bot = SimpleNamespace(members=[], leader=None, _settings={}, deadline=deadline)
        farm = DungeonFarm(bot, load_dungeons()["champs"], 3)
        seen = []
        monkeypatch.setattr(farm, "_one_pass", lambda run, *args: seen.append(bot.deadline) or True)
        return farm, bot, seen

    farm, bot, seen = farm_with(time.monotonic() + PASS_ESTIMATE / 2)
    assert farm.run() == 0 and seen == []  # pas le temps d'un passage
    farm, bot, seen = farm_with(time.monotonic() + PASS_ESTIMATE / 2)
    assert farm.run("salles") == 3 and seen == [None] * 3  # reprise : finie, puis passages bien plus courts qu'estimé
    deadline = time.monotonic() + 3 * PASS_ESTIMATE
    farm, bot, seen = farm_with(deadline)
    assert farm.run() == 3 and seen == [None] * 3 and bot.deadline == deadline  # limite rendue après chaque passage


def test_missing_keys_farmed_in_the_key_zone_then_the_passes_go_on(monkeypatch):
    # 06/11, utilisateur : clés qui manquent (coffre compris) : « farm du butin » (donjon.cles_manquantes=farm), zone de
    # la clé dans config/donjons_entree.yaml (Champs : 7,-25) ; arrêt quand le butin ne donne plus une clé chacun.
    from types import SimpleNamespace

    from bot.team import pods
    from bot.team.dungeon import DungeonFarm

    members = [SimpleNamespace(name=n, game=None) for n in ("A", "B")]
    bot = SimpleNamespace(members=members, leader=members[0], finished=False,
                          _settings={"donjon": {"cles": "sac", "cles_manquantes": "farm"}})
    farm = DungeonFarm(bot, load_dungeons()["champs"], 3)
    assert farm.spec.key_zone == (7, -25) and farm.keys.refill
    held = {"A": 1, "B": 1}
    loot = [{"A": 2, "B": 0}, {"A": 0, "B": 1}]  # 1er farm : 2 clés (chez A) ; 2e : 1 seule, pas assez pour deux
    farms = []

    def zone_farm(bot, zone, enough, seconds, why):
        farms.append(zone)
        for name, count in loot.pop(0).items():
            held[name] += count
        return enough()

    def prepare(dungeon, wanted, template=None):
        each = min(sum(held.values()) // len(held), wanted)
        held.update({name: each for name in held})  # répartis par échanges
        return each

    monkeypatch.setattr(pods, "team_zone_farm", zone_farm)
    monkeypatch.setattr(farm.keys, "prepare", prepare)
    monkeypatch.setattr(farm.keys, "count", lambda template: dict(held))

    def one_pass(run, *args):
        held.update({name: count - 1 for name, count in held.items()})  # une clé donnée chacun
        return True

    monkeypatch.setattr(farm, "_one_pass", one_pass)
    assert farm.run() == 2 and farms == [(7, -25)] * 2 and farm.runs == 3
    farm = DungeonFarm(SimpleNamespace(members=members, leader=None, finished=False,
                                       _settings={"donjon": {"cles": "sac", "cles_manquantes": "fabrication"}}),
                       load_dungeons()["champs"], 3)
    assert not farm._can_refill() and not farm.keys.refill  # pas encore faite : passages ramenés aux clés


def test_team_zone_farm_until_enough_then_the_previous_mode_comes_back(monkeypatch):
    from types import SimpleNamespace

    from bot.brain import travel
    from bot.team.pods import team_zone_farm

    roamer = SimpleNamespace(traveler=SimpleNamespace(coords=(7, -26)))
    monkeypatch.setattr(travel, "make_roamer", lambda game, settings: roamer if settings.mode == "zone" else None)
    steps = []
    bot = SimpleNamespace(engager=object(), roamer=None, travel_settings=travel.TravelSettings(), finished=False,
                          _round_due=False, leader=SimpleNamespace(game=None), joiner=SimpleNamespace(fight_coords=None))
    bot.step = lambda: steps.append(bot.roamer is roamer and bot.travel_settings.start == (7, -25))
    assert team_zone_farm(bot, (7, -25), lambda: len(steps) >= 3, 60, "Clés")
    assert steps == [True] * 3 and bot.roamer is None and bot.travel_settings.mode != "zone" and not bot.finished
    assert bot.joiner.fight_coords is None
    assert not team_zone_farm(bot, (7, -25), lambda: False, 0, "Clés")  # durée écoulée
    bot.engager = None
    assert not team_zone_farm(bot, (7, -25), lambda: True, 60, "Clés")  # combats désactivés : pas de farm


def test_entry_and_exit_count_as_progress_for_the_watchdog(monkeypatch):
    # 06/11 (journal 200051) : 5 min d'échanges de clés sans progrès noté, pause « BLOQUÉ » de 2 min juste après l'entrée.
    from types import SimpleNamespace

    from bot.team.dungeon import DungeonFarm

    notes = []
    bot = SimpleNamespace(members=[], leader=None, _settings={}, _progress=notes.append)
    farm = DungeonFarm(bot, load_dungeons()["champs"], 1)
    monkeypatch.setattr(farm, "_close_windows", lambda: None)
    monkeypatch.setattr(farm, "_pods_ready", lambda: True)
    monkeypatch.setattr(farm, "_fight_rooms", lambda first=1: True)
    monkeypatch.setattr(farm.entry, "enter", lambda *args: True)
    monkeypatch.setattr(farm.entry, "leave", lambda: True)
    assert farm.run() == 1 and notes == ["équipe entrée dans le donjon", "équipe sortie du donjon"]


def test_zone_farm_never_stops_with_a_fight_announced(monkeypatch):
    # 06/11 (journal 194826) : attaque lancée au dernier tour, farm arrêté 2 s après (durée écoulée), Fourboun seul en
    # combat sans personne pour jouer ni rejoindre. Le farm attend la fin du combat.
    from types import SimpleNamespace

    from bot.brain import travel
    from bot.team.pods import team_zone_farm

    roamer = SimpleNamespace(traveler=SimpleNamespace(coords=(7, -26)))
    monkeypatch.setattr(travel, "make_roamer", lambda game, settings: roamer)
    fights = [True, True, False]  # combat annoncé aux deux premiers tours
    bot = SimpleNamespace(engager=object(), roamer=None, travel_settings=travel.TravelSettings(), finished=False,
                          _round_due=False, leader=SimpleNamespace(game=None), joiner=None)
    steps = []
    bot.step = lambda: steps.append(1)
    bot.fight_pending = lambda: fights.pop(0)
    assert not team_zone_farm(bot, (7, -25), lambda: False, 0, "Clés") and len(steps) == 3


def test_a_member_the_server_sees_still_is_clicked_again_at_once(monkeypatch):
    # 06/11 (journal 210755) : 3 sur 8 pas passés par la sortie, recliqués seulement 12 s après chacun (46 s).
    import time
    from types import SimpleNamespace

    monkeypatch.setattr("bot.team.dungeon.NET_SILENT", 0.0)  # le jeu factice n'attend pas pour de vrai

    def net(moved):
        return SimpleNamespace(alive=lambda: True, my_id=1, moved_at=moved, map_at=0.0,
                               wait_new_map=lambda since, wait: 1)

    spec = load_dungeons()["champs"]
    screen = Screen()
    room, outside = load_frame(PLATES), load_frame(OUTSIDE)
    team = [member("A", screen, room, outside), member("B", screen, room, outside)]
    team[0].game.net = net(time.monotonic() + 3600)  # déplacement annoncé : clic pris
    team[1].game.net = net(0.0)  # rien d'annoncé : reclic tout de suite
    assert DungeonEntry(team, spec).leave()
    assert len(events(screen, "click")) == 3


def test_entry_bubble_in_several_steps_goes_on_until_the_key(monkeypatch):
    # 07/11, Ensablé (langue du client) : Igloute raconte son histoire, 8 « Continuer la discussion » (2936 à 2943)
    # avant la question qui propose « Donner la clef et rentrer » (2944). Chaque « Continuer » attend la question
    # suivante du serveur ; rien n'est donné avant.
    from types import SimpleNamespace

    from bot.connection.network import ClientFeed

    spec = load_dungeons()["ensable"]
    assert spec.chain == "Continuer la discussion" and spec.reply_id == 2944 and spec.rooms == 9
    net = ClientFeed("Fourboun")
    clicks = []
    bubble = SimpleNamespace(layout=SimpleNamespace(spread=lambda *a: (0, 0)), image=None)
    game = SimpleNamespace(net=net, sleep=lambda s: None, look=lambda park=False: bubble,
                           click=lambda point, spread=None: clicks.append(point) or advance())
    hero = SimpleNamespace(name="Fourboun", game=game)
    entry = DungeonEntry([hero], spec)
    story = [(3281 + i, (2936 + i,)) for i in range(8)] + [(3289, (2944,))]

    def advance():
        question, answers = story.pop(0)
        net.feed(f"DQ{question}|" + ";".join(map(str, answers)))

    advance()  # première bulle, à l'ouverture
    entry._opened_at["Fourboun"] = 0.0
    monkeypatch.setattr(entry, "_read_reply", lambda game, frame, answer: (1, 1) if answer == spec.chain else None)
    assert entry._through_chain(hero, bubble) is bubble
    assert len(clicks) == 8 and net.question[0] == 3289 and entry._entry_answer(hero, bubble) == spec.reply


def test_entry_bubble_chain_that_never_reaches_the_key_is_closed(monkeypatch):
    from types import SimpleNamespace

    spec = load_dungeons()["ensable"]
    closed = []
    bubble = SimpleNamespace(layout=SimpleNamespace(spread=lambda *a: (0, 0)), image=None)
    game = SimpleNamespace(net=None, sleep=lambda s: None, look=lambda park=False: bubble,
                           click=lambda point, spread=None: None, recorder=SimpleNamespace(save=lambda *a, **k: "x"))
    hero = SimpleNamespace(name="Fourboun", game=game)
    entry = DungeonEntry([hero], spec)
    monkeypatch.setattr(entry, "_read_reply", lambda game, frame, answer: (1, 1) if answer == spec.chain else None)
    monkeypatch.setattr(entry, "_close_bubble", lambda m: closed.append(m.name))
    assert entry._through_chain(hero, bubble) is None and closed == ["Fourboun"]


def test_a_member_who_did_the_dungeon_skips_the_story(monkeypatch):
    # 07/11, utilisateur : qui a déjà fait l'Ensablé se voit proposer de passer tout le dialogue ; même texte « Donner
    # la clef et rentrer », numéro 2947 (langue du client). Aucun « Continuer » cliqué.
    from types import SimpleNamespace

    from bot.connection.network import ClientFeed

    spec = load_dungeons()["ensable"]
    assert spec.reply_ids == {2944, 2947}
    net = ClientFeed("Fourboun")
    net.feed("DQ3281|2936;2947")
    clicks = []
    bubble = SimpleNamespace(layout=SimpleNamespace(spread=lambda *a: (0, 0)), image=None)
    game = SimpleNamespace(net=net, sleep=lambda s: None, look=lambda park=False: bubble,
                           click=lambda point, spread=None: clicks.append(point))
    hero = SimpleNamespace(name="Fourboun", game=game)
    entry = DungeonEntry([hero], spec)
    entry._opened_at["Fourboun"] = 0.0
    monkeypatch.setattr(entry, "_read_reply", lambda game, frame, answer: (1, 1))
    assert entry._through_chain(hero, bubble) is bubble and clicks == []
    assert entry._entry_answer(hero, bubble) == spec.reply
