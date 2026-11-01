"""Fiabilité de nuit (26/10) : la carte teintée par le cycle jour/nuit, ramenée au jour (perception/daylight.py).

26/10 (journal 001614, Prairie des Blops -3,-28) : passé minuit, sol du mode tactique en (116, 97, 114) au lieu de
(131, 125, 121). Mode tactique cru inactif, portée des sorts et zone de déplacement plus reconnues, terrain illisible
(5 à 8 cases lues au lieu de ~365) : « 9 cercles de ma couleur et rien ne prouve lequel est moi », 38 fois en
12 minutes (14 de plus dans le journal 234008 après minuit), les 8 personnages ont passé tous leurs tours.

Captures de nuit (tests/fixtures/nuit_*) : journaux 20261026_001614 et 20261025_234008 (après minuit).
"""

import logging
import time

import numpy as np
import pytest

from bot.brain.combat import CombatAI, CombatSettings
from bot.brain.tactics import CombatSpell
from bot.perception import daylight
from bot.perception.colors import BLUE, RANGE_BLUE, RED
from bot.perception.daylight import DAY_FLOOR, floor_tint
from bot.perception.fighters import find_fighters
from bot.perception.frame import Frame
from bot.perception.placement import read_placement
from bot.perception.sight import locate_caster, read_range, read_terrain, sight_agreement
from bot.perception.ui import movement_zone, spell_range_visible, tactical_mode
from conftest import SCREENS, load_frame
from test_brain import FakeGame, clicks, presses

NIGHT = ["nuit_combat_portee_eldieso", "nuit_combat_zone_de_deplacement", "nuit_placement_fourboun",
         "nuit_pave_numerique_portee_affichee", "combat_timeline_en_haut_sans_mode_tactique"]
# Captures de jour en mode tactique (cartes, portées, placements, JPEG compris) : jamais retouchées.
DAY = [*SCREENS, "combat_cercles_colles", "combat_equipe_8_en_diagonale", "placement_foret", "portee_relief_porkass",
       "portee_sur_le_vide_incarnam_6_1", "tactique_petite_carte_vide_noir", "reperage_cercle_absent_du_tour",
       "combat_coin_une_marche_plus_bas", "placement_mode_normal"]

# Carte officielle -3,-28 (data/references, 8 blocs) ; (28, 32) est sous l'interface du bas, jamais lu.
OFFICIAL_BLOCKS = {(4, 24), (12, 8), (12, 20), (18, 14), (19, 25), (21, 3), (26, 2)}
# Les 8 personnages sur nuit_combat_portee_eldieso (Eldieso, au centre de la portée de Flèche Empoisonnée : (11, 23)).
TEAM_CELLS = {(11, 23), (13, 23), (10, 24), (12, 24), (14, 24), (11, 25), (10, 26), (11, 27)}


@pytest.mark.parametrize("name", NIGHT)
def test_night_tint_read_on_the_tactical_floor(name):
    tint = load_frame(name).tint
    assert tint is not None and 0.9 <= tint.strength <= 1.1
    dark, light = tint.floor
    assert np.abs(np.array(dark) - (116, 97, 114)).max() <= 2 and np.abs(np.array(light) - (125, 105, 123)).max() <= 2


@pytest.mark.parametrize("name", DAY)
def test_day_captures_are_left_untouched(name):
    # 710 captures de jour (tests et journaux du 25/10), 503 en mode tactique : force 0,00 à 0,03, image jamais retouchée.
    frame = load_frame(name)
    assert frame.tint is None
    assert frame.day_image is frame.image


def test_night_out_of_combat_is_left_untouched():
    # Hors combat, pas de sol tactique pour lire la teinte : l'image reste telle quelle (groupes et épée se lisaient
    # déjà de nuit, journal 001614).
    frame = load_frame("nuit_hors_combat")
    assert frame.tint is None and frame.day_image is frame.image and not tactical_mode(frame)


def test_night_map_brought_back_to_day_colors_and_interface_untouched():
    frame = load_frame("nuit_combat_portee_eldieso")
    day = frame.day_image
    x0, y0, x1, y1 = frame.layout.map_box
    found, _ = daylight._shades(day[y0:y1, x0:x1])
    colors = [c for c, _ in found]
    for floor in DAY_FLOOR:  # le sol revient au gris du jour, à 2 niveaux près
        assert min(np.abs(c - floor).max() for c in colors) <= 2
    assert min(np.abs(c - RANGE_BLUE).max() for c in colors) <= 4  # portée : (153, 80, 44) -> bleu du jour
    assert floor_tint(day[y0:y1, x0:x1]) is None  # plus rien à corriger
    assert (day[y1:] == frame.image[y1:]).all() and (day[:, :x0] == frame.image[:, :x0]).all()  # barre, chat


def test_night_tint_is_cheap():
    # Une fois par capture : lecture de la teinte (~3 ms) et correction de la carte (~2 ms), mesurées le 26/10.
    image = load_frame("nuit_combat_portee_eldieso").image
    took = []
    for _ in range(5):
        start = time.perf_counter()
        frame = Frame.of(image)
        assert frame.day_image is not frame.image
        took.append(time.perf_counter() - start)
    assert sorted(took)[2] < 0.025


@pytest.mark.parametrize("name", NIGHT)
def test_tactical_mode_recognized_at_night(name):
    # 26/10 : faux sur les 139 captures de combat de nuit, vrai sur toutes depuis.
    assert tactical_mode(load_frame(name))


def test_tactical_button_not_searched_at_night(caplog):
    # 26/10 (journal 234008, 00:00 à 00:08) : 15 captures « bouton_tactique_introuvable » (timeline en haut, bouton
    # caché dessous) alors que le mode tactique était actif ; timeline redescendue pour rien à chaque combat.
    frame = load_frame("combat_timeline_en_haut_sans_mode_tactique")
    game = FakeGame(frame)
    with caplog.at_level(logging.INFO, logger="bot.brain.combat"):
        CombatAI(game, CombatSettings())._ensure_tactical(frame)
    assert clicks(game) == [] and "tactique" not in caplog.text


def test_unknown_floor_with_the_tactical_button_checked_is_captured_once(monkeypatch, caplog):
    # Une teinte d'une autre forme (aube ?) laisserait le sol non reconnu, bouton coché : une capture par session pour
    # la mesurer, sans clic (le bouton coché se décocherait).
    monkeypatch.setattr("bot.brain.combat.tactical_mode", lambda frame: False)
    frame = load_frame("nuit_combat_zone_de_deplacement")
    game = FakeGame(frame)
    ai = CombatAI(game, CombatSettings())
    with caplog.at_level(logging.INFO, logger="bot.brain.combat"):
        for _ in range(2):
            ai._ensure_tactical(frame)
            ai.new_combat()
    assert clicks(game) == [] and caplog.text.count("sol_tactique_inconnu") == 1


def test_spell_range_seen_at_night():
    # 26/10 (journal 001614) : « Pavé numérique : Flèche Empoisonnée pas sélectionné par num5 », la portée pourtant
    # affichée (bleu (153, 80, 44), hors tolérance) ; le pas de repérage cliquait alors avec le sort sélectionné.
    assert spell_range_visible(load_frame("nuit_pave_numerique_portee_affichee"))
    assert spell_range_visible(load_frame("nuit_combat_portee_eldieso"))
    assert not spell_range_visible(load_frame("nuit_combat_zone_de_deplacement"))
    assert not spell_range_visible(load_frame("nuit_placement_fourboun"))


def test_circles_of_the_team_and_enemies_found_at_night():
    fighters = find_fighters(load_frame("nuit_combat_portee_eldieso"))
    red = {f.cell for f in fighters if f.team == RED}
    blue = {f.cell for f in fighters if f.team == BLUE}
    assert TEAM_CELLS <= red
    assert {(5, 3), (3, 5), (5, 5)} <= blue  # les trois Blops en haut à gauche


def test_who_am_i_from_the_spell_range_at_night():
    # Ce qui manquait le 26/10 : le repérage par la portée, jamais tenté (mode tactique cru inactif).
    shown = load_frame("nuit_combat_portee_eldieso")
    fighters = find_fighters(shown)
    mine = [f for f in fighters if f.team == RED]
    found = locate_caster(shown, (2, 5), {f.cell for f in mine}, fighters)
    assert found is not None and found.cell == (11, 23) and not found.hidden
    game = FakeGame(shown, load_frame("nuit_combat_zone_de_deplacement"))  # sort sélectionné, puis Échap
    ai = CombatAI(game, CombatSettings())
    ai._spells = [CombatSpell(2, "fleche_empoisonnee", "Flèche Empoisonnée", 4, (2, 5), damage=6)]
    me = ai._locate_by_range(shown, mine, fighters)
    assert me is not None and me.cell == (11, 23)
    assert presses(game)[-1] == "escape"


def test_line_of_sight_read_at_night_matches_the_game():
    shown = load_frame("nuit_combat_portee_eldieso")
    fighters = find_fighters(shown)
    me = next(f for f in fighters if f.cell == (11, 23))
    others = [f for f in fighters if f is not me]
    view = read_range(shown, me, others)
    assert len(view.castable) >= 15 and len(view.no_sight) >= 20 and view.spell_range == (2, 5)
    blockers = read_terrain(shown, fighters).blocks | {f.cell for f in others}
    assert sight_agreement(view, me.cell, blockers) == 1.0


@pytest.mark.parametrize("name", ["nuit_combat_portee_eldieso", "nuit_combat_zone_de_deplacement",
                                  "nuit_placement_fourboun"])
def test_terrain_read_at_night_matches_the_official_map(name):
    # 26/10 : 4 à 8 cases lues de nuit (accord 50 à 71 % avec la carte officielle), ~365 depuis, 99 à 100 % d'accord
    # sur 51 captures de combat de nuit, carte reconnue sur toutes (de jour sur -3,-27 : 99 à 100 % aussi).
    frame = load_frame(name)
    terrain = read_terrain(frame, find_fighters(frame))
    assert len(terrain.seen) >= 290
    banner = {(18, 14)} if name == "nuit_placement_fourboun" else set()  # sous « Tu es entré en combat ! » : non lue
    assert OFFICIAL_BLOCKS - banner <= terrain.blocks
    assert not terrain.floor & OFFICIAL_BLOCKS
    assert len(terrain.blocks - OFFICIAL_BLOCKS) <= 2  # l'icône du challenge en haut à gauche, posée sur (4, 2)


def test_movement_zone_seen_at_night():
    # Fourbodeux survolé, entouré de ses alliés : les 4 cases libres autour de lui (0 avant la correction).
    assert movement_zone(load_frame("nuit_combat_zone_de_deplacement")) == {(10, 24), (10, 26), (12, 24), (12, 26)}


def test_placement_read_at_night():
    # Avant la correction, une case libre de l'équipe était lue occupée (JPEG et teinte de nuit).
    placement = read_placement(load_frame("nuit_placement_fourboun"))
    assert placement.occupied[RED] == {(12, 24)}
    assert placement.free[RED] == {(10, 24), (10, 26), (11, 23), (11, 25), (11, 27), (13, 23), (14, 24)}
    assert placement.occupied[BLUE] == {(3, 5), (5, 3), (5, 5)}
    assert placement.free[BLUE] == {(3, 7), (5, 7), (7, 3), (7, 5), (7, 7)}


def test_night_tint_logged_once_per_fight(caplog):
    frame = load_frame("nuit_combat_portee_eldieso")
    ai = CombatAI(FakeGame(frame), CombatSettings())
    with caplog.at_level(logging.INFO, logger="bot.brain.combat"):
        ai._note_tint(frame)
        ai._note_tint(frame)
        ai._note_tint(load_frame("combat_mon_tour"))
    lines = [r.getMessage() for r in caplog.records if "teintée par la nuit" in r.getMessage()]
    assert len(lines) == 1 and "(116, 97, 114)" in lines[0]
    ai.new_combat()
    with caplog.at_level(logging.INFO, logger="bot.brain.combat"):
        ai._note_tint(frame)
    assert len([r for r in caplog.records if "teintée par la nuit" in r.getMessage()]) == 2


def test_other_two_tone_floors_are_not_taken_for_a_tint():
    # Deux couleurs voisines qui ne sont pas le sol tactique (bleu « sans ligne de vue », fenêtres beiges : écart de 26
    # à 130 au modèle) : pas de teinte. Ici un sol uni à deux tons beiges.
    image = np.zeros((200, 200, 3), np.uint8)
    image[:, :100] = (141, 172, 180)
    image[:, 100:] = (152, 185, 190)
    assert floor_tint(image) is None
    # Le sol tactique teinté à moitié (aube, crépuscule ?) : force lue à mi-chemin.
    gain, offset = np.array(daylight.NIGHT_GAIN), np.array(daylight.NIGHT_OFFSET)
    half = [np.array(c) + 0.5 * (np.array(c) * (gain - 1) + offset) for c in DAY_FLOOR]
    image[:, :100], image[:, 100:] = np.rint(half[0]), np.rint(half[1])
    tint = floor_tint(image)
    assert tint is not None and abs(tint.strength - 0.5) <= 0.05


def test_group_tooltips_read_at_night():
    # 26/10 (journal 011501) : de nuit, les infobulles sont teintées comme la carte (blanc du titre lu (235, 212, 235)) :
    # aucune trouvée, la carte quittée avec 3 groupes dessus (niveaux 107, 16 et 108), 8 fois de suite.
    from bot.perception.groups import find_group_tooltips

    tooltips = find_group_tooltips(load_frame("nuit_groupes_z_trois_groupes"))
    assert sorted(t.monsters for t in tooltips) == [1, 5, 5]


def test_group_tooltips_by_day_read_with_the_day_threshold_only():
    # De jour, le seuil de nuit n'est jamais essayé quand celui du jour trouve des infobulles (0 changement sur 939
    # captures de jour du 25/10).
    from bot.perception import groups

    frame = load_frame("groupes_z_chevauchement")
    assert groups.find_group_tooltips(frame) == groups._find_tooltips(frame)


def test_session_stops_after_a_fight_where_the_night_tint_was_not_recognised():
    # 26/10 (journal 012810) : nuit plus profonde qu'à minuit, teinte pas encore apprise : les 8 personnages ont passé
    # tous leurs tours. Plusieurs personnages au sol tactique pas reconnu : arrêt après le combat. Un seul (fiche
    # passagère sur la carte) : pas d'arrêt.
    from test_team import Screen, member, team_bot

    # 01/11 : combattants d'après le serveur, la teinte n'empêche pas de jouer : pas d'arrêt.
    for blind, stops, told in ((3, True, False), (1, False, False), (4, False, True)):
        screen = Screen()
        members = [member(name, screen, load_frame("hors_combat")) for name in "ABCD"]
        for m in members[:blind]:
            m.ai.unknown_tint, m.ai.told_fight = True, told
        bot = team_bot(*members)
        bot._fight_over()
        assert bool(bot._stop_reason) == stops


def test_no_fight_launched_at_night():
    # 26/10 (journal 012810) : combat lancé de nuit, teinte pas reconnue, aucun personnage repéré : les 8 morts. Les
    # infobulles lues avec le seuil de nuit disent qu'il fait nuit : aucun combat lancé, aucun clic sur un groupe.
    from bot.brain.engage import Engager, EngageSettings
    from test_brain import FakeGame, clicks

    frame = load_frame("nuit_groupes_z_trois_groupes")
    game = FakeGame(frame)
    engager = Engager(game, EngageSettings())
    assert not engager.engage() and engager.night and clicks(game) == []
    day = Engager(FakeGame(load_frame("groupes_z_chevauchement")), EngageSettings())
    day.engage()
    assert not day.night


def test_night_does_not_stop_fights_driven_by_the_server():
    # 01/11, utilisateur : avec l'écoute du serveur, la nuit n'empêche plus de lancer un combat ; plus de touche Z par
    # carte pour lire la teinte des infobulles.
    import time

    from bot.brain.engage import Engager, EngageSettings
    from bot.connection.network import ClientFeed
    from test_brain import FakeGame, presses

    game = FakeGame(load_frame("nuit_groupes_z_trois_groupes"))
    net = ClientFeed("Fourboun")
    net.heard_at, net.map_id, net.map_at = time.monotonic(), 7354, 1.0
    engager = Engager(game, EngageSettings())
    assert not engager._engage_by_net(net)  # aucun groupe annoncé
    assert not engager.night and presses(game) == [] and engager.none_found
