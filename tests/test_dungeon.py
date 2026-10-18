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
    assert abs(talk[0] - 1555) <= 12 and abs(talk[1] - 381) <= 8  # « Parler »


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
    assert len(events(screen, "click_right")) == 1 and not events(screen, "click")


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
