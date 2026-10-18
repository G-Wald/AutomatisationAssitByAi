"""Entrée du donjon d'Incarnam (18/10, captures de l'utilisateur) : bot/team/dungeon.py."""

from bot.team.dungeon import DungeonEntry, load_dungeons
from conftest import load_frame
from tests.test_team import Screen, events, member

OUTSIDE = "donjon_incarnam_entree_6_5"  # grotte en 6,5 (Divers)
PLATES = "donjon_incarnam_salle_dalles"  # salle d'entrée : dalles, soleil libéré
NPC_ROOM = "donjon_incarnam_salle_master_donge"  # salle de Ma'ster Donge
BUBBLE = "donjon_incarnam_bulle_master_donge"  # sa bulle : « Voici la clef de la porte. »


def incarnam():
    return load_dungeons()["incarnam"]


def test_incarnam_dungeon_described_cell_by_cell():
    spec = incarnam()
    assert spec.outside == (6, 5) and spec.entrance == (16, 14) and spec.plate == (18, 20) and spec.sun == (23, 5)
    assert spec.npc == (20, 14) and "clef" in spec.reply


def test_whole_team_takes_the_cave_sun_and_each_decor_changes():
    screen = Screen()
    outside, inside = load_frame(OUTSIDE), load_frame(PLATES)
    team = [member("A", screen, outside, inside), member("B", screen, outside, inside)]
    assert DungeonEntry(team, incarnam())._all_take((16, 14), "l'entrée")
    clicks = [e[2] for e in events(screen, "click")]
    x, y = outside.grid.center((16, 14))
    assert len(clicks) == 2 and all(abs(cx - x) <= 25 and abs(cy - y) <= 15 for cx, cy in clicks)


def test_member_still_outside_clicks_the_sun_again_then_gives_up():
    screen = Screen()
    outside = load_frame(OUTSIDE)
    stuck = member("A", screen, outside)  # le décor ne change jamais
    assert not DungeonEntry([stuck], incarnam())._all_take((16, 14), "l'entrée")
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
