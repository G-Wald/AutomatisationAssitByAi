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
