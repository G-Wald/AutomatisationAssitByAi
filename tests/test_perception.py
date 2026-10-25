import cv2
import numpy as np
import pytest

from bot.perception.colors import BLUE, RED, UI_BEIGE
from bot.perception.fighters import Fighter, clear_point, find_fighters
from bot.perception.frame import Frame
from bot.perception.grid import distance
from bot.perception.groups import find_group_tooltips
from bot.perception.layout import Layout
from bot.perception.popups import (find_flash_menu, find_npc_dialog, flash_close_point, find_known_popup, find_unknown_window,
                                   load_popup_specs)
from bot.perception.placement import entry_banner, read_placement
from bot.perception.spellbook import identify, locate_in_bar, read_spell_window, slot_greyed
from bot.perception.spellsheet import read_sheet
from bot.perception.sight import (in_line_only, range_center, line_of_sight, read_blocks, read_range, read_terrain, sight_agreement,
                                  timeline_portraits)
from bot.perception.templates import match_template
from bot.perception.timeline import challenge_icons
from bot.perception.ui import (
    PA_BUBBLE,
    TACTICAL_OFF,
    TOOLBAR_ZONE,
    forbidden_cursor_near,
    hp_ratio,
    movement_zone,
    read_pa,
    read_pm,
    region_changed,
    spell_range_visible,
    spell_slot_point,
    tactical_mode,
)
from conftest import FIXTURES, FLASH_MENU_CLICK, SCREENS, load_frame, with_flash_menu, without_flash_menu


def positions(name):
    return sorted((f.team, round(f.x), round(f.y)) for f in find_fighters(load_frame(name)))


def assert_near(found, expected, tolerance=4):
    assert len(found) == len(expected), found
    for (team, x, y), (team_e, x_e, y_e) in zip(found, expected):
        assert team == team_e and abs(x - x_e) <= tolerance and abs(y - y_e) <= tolerance, found


@pytest.mark.parametrize(
    "name, expected",
    [
        ("combat_mon_tour", [(BLUE, 1822, 342), (RED, 1004, 514)]),
        ("combat_tour_adversaire", [(BLUE, 1744, 384), (RED, 1004, 515)]),
        ("combat_portee_sort", [(BLUE, 1534, 442), (RED, 1148, 589)]),
        ("hors_combat", []),
        ("preparation", []),  # les cases de placement ne sont pas des cercles
        # 01/10 : deux Bouftous sur des cases voisines en diagonale, cercles bleus collés en une seule tache.
        ("combat_cercles_colles", [(BLUE, 1487, 417), (BLUE, 1535, 442), (RED, 1246, 491)]),
        ("combat_cercles_colles_contact", [(BLUE, 1294, 466), (BLUE, 1342, 491), (RED, 1244, 491)]),
        # 03/10 : Bouftou juste derrière l'Enutrof, son cercle coupé en deux arcs ; vu nulle part, tours passés en boucle.
        ("combat_cercle_cache", [(BLUE, 1342, 393), (RED, 1246, 491), (RED, 1294, 417), (RED, 1342, 442)]),
    ],
)
def test_find_fighters(name, expected):
    assert_near(positions(name), expected)


def test_team_of_8_placed_in_a_diagonal_line_is_seen():
    """18/10, forêt d'Incarnam 1,6 : les 8 personnages placés en diagonale, leurs cercles collés en une seule tache de
    424 x 215 px, trop grande pour des cercles collés ; aucun ne se voyait (deux combats de 7 et 4 min, tours passés)."""
    frame = load_frame("combat_equipe_8_en_diagonale")
    fighters = find_fighters(frame)
    assert sorted(f.cell for f in fighters if f.team == RED) == [(18, 14), (19, 13), (20, 12), (21, 11), (22, 10),
                                                                  (23, 9), (24, 8), (25, 7)]
    assert [f.cell for f in fighters if f.team == BLUE] == [(8, 2)]  # sous « Coordonnées », en haut à gauche
    for fighter in fighters:
        x, y = frame.grid.center(fighter.cell)
        assert abs(x - fighter.x) < 4 and abs(y - fighter.y) < 4


def test_fighters_stand_on_cell_centers():
    frame = load_frame("combat_portee_sort")
    for fighter in find_fighters(frame):
        x, y = frame.grid.center(fighter.cell)
        assert abs(x - fighter.x) < 4 and abs(y - fighter.y) < 4


def under_long_timeline(*patches: str) -> Frame:
    """15/10, équipe de 8 : le seul ennemi (Arakne Immature) caché sous la timeline de 9 portraits, personne ne le
    voyait. Les captures (logs/20261015_204355, 0079 à 0113) ont été effacées par la rotation des journaux ; il en
    reste le bas droit (exact, 1250-1920 x 600-850, puis 1580-1730 x 700-830 pour l'ennemi au-dessus), recollé sur la
    capture du même combat combat_equipe_8_timeline_longue (dont l'Arakne visible en (12, 28))."""
    image = load_frame("combat_equipe_8_timeline_longue").image.copy()
    for name, (x, y) in zip(patches, [(1250, 600), (1580, 700)]):
        patch = cv2.imread(str(FIXTURES / f"{name}.png"))
        image[y : y + patch.shape[0], x : x + patch.shape[1]] = patch
    return Frame.of(image)


@pytest.mark.parametrize(
    "patches, cell",
    [
        (["timeline_longue_ennemi_dessous"], (23, 31)),  # seul le bas du cercle dépasse, sous les portraits
        (["timeline_longue_ennemi_dessous", "timeline_longue_ennemi_dessus"], (23, 29)),  # le haut, au-dessus
    ],
)
def test_enemy_ring_hidden_under_the_timeline_portraits(patches, cell):
    frame = under_long_timeline(*patches)
    assert {f.cell for f in find_fighters(frame) if f.team == BLUE} == {(12, 28), cell}


@pytest.mark.parametrize("cell", [(23, 31), (23, 29)])
def test_click_on_a_cell_almost_hidden_by_the_timeline_misses_the_portraits(cell):
    frame = under_long_timeline("timeline_longue_ennemi_dessous")
    portraits = timeline_portraits(frame)
    (x, y), (sx, sy) = clear_point(frame.grid, cell, [], portraits)
    cx, cy = frame.grid.center(cell)
    assert abs(x - cx) / frame.grid.half_w + abs(y - cy) / frame.grid.half_h <= 0.9  # dans le losange
    assert not any(x0 - sx <= x <= x1 + sx and y0 - sy <= y <= y1 + sy for x0, y0, x1, y1 in portraits)
    assert frame.layout.on_map((x, y + sy))


def test_no_ring_guessed_under_the_timeline_without_its_arc():
    frame = under_long_timeline("timeline_longue_ennemi_dessous")
    x0, y0, x1, y1 = 1600, 760, 1720, 830  # l'arc bleu sous le 6e portrait, effacé (gris du sol)
    image = frame.image.copy()
    region = image[y0:y1, x0:x1]
    region[(region[:, :, 0] > 200) & (region[:, :, 2] < 60)] = (77, 74, 72)
    assert {f.cell for f in find_fighters(Frame.of(image)) if f.team == BLUE} == {(12, 28)}


@pytest.mark.parametrize("name", SCREENS)
def test_spell_range_only_when_spell_selected(name):
    assert spell_range_visible(load_frame(name)) is (name == "combat_portee_sort")


def test_forbidden_cursor():
    frame = load_frame("combat_portee_sort")
    assert all(forbidden_cursor_near(frame, (1625 + dx, 554 + dy)) for dx in (-17, 0, 17) for dy in (-8, 0, 8))
    assert not forbidden_cursor_near(frame, (1534, 442))  # l'ennemi, loin du curseur
    assert not forbidden_cursor_near(load_frame("combat_mon_tour"), (1004, 514))  # mon propre cercle rouge


def test_my_circle_is_not_a_forbidden_cursor_when_target_is_adjacent():
    # Bug du 27/09 : cible au contact refusée, mon cercle rouge coupé par la zone de recherche.
    frame = load_frame("combat_mon_tour")
    me = frame.grid.cell_at(1004, 514)
    for di, dj in [(1, 1), (-1, 1), (1, -1), (-1, -1), (2, 0), (-2, 0), (0, 2), (0, -2)]:
        x, y = frame.grid.center((me[0] + di, me[1] + dj))
        for dx in range(-20, 21, 5):
            for dy in range(-10, 11, 5):
                assert not forbidden_cursor_near(frame, (round(x + dx), round(y + dy)))


def test_spell_range_and_obstacles_from_colored_cells():
    frame = load_frame("combat_portee_sort")
    fighters = find_fighters(frame)
    me = next(f for f in fighters if f.team == RED)
    view = read_range(frame, me, fighters)
    assert view.spell_range == (1, 7)  # Flèche Magique : pas de portée minimale
    assert view.no_sight == {(19, 17)}  # derrière l'angle du bloc, en haut à droite
    # Blocs en haut à droite et en bas ; pas la case cachée par mon personnage. 18/10 : un bloc est sur la case du coin
    # bas de son dessus ; (12, 30) et (14, 30), à droite des deux piliers du bas, sont des cases cachées par leur
    # relief, plus des blocs (la case (15, 31) était prédite sans ligne de vue : 103 cases justes sur 103, 102 avant).
    assert view.obstacles == {(15, 17), (16, 18), (17, 17), (18, 18), (11, 31), (13, 31)}
    blockers = view.obstacles | ({f.cell for f in fighters} - {me.cell})
    assert sight_agreement(view, me.cell, blockers) == 1.0  # ligne de vue prédite comme le jeu l'affiche
    assert read_range(load_frame("combat_mon_tour"), me, fighters).spell_range is None


def test_no_obstacles_inside_the_minimum_range():
    # 11/10 : Tempête de Puissance (3 à 4) ; les cases à 1 ou 2 de Brawli ne sont pas colorées, ce ne sont pas des blocs.
    image = np.zeros((1009, 1920, 3), np.uint8)  # extrait (lignes 200-560, colonnes 850-1500) d'une capture 1920 x 1009
    image[200:560, 850:1500] = cv2.imread(str(FIXTURES / "portee_tempete_3_a_4.png"))
    frame = Frame.of(image)
    fighters = find_fighters(frame)
    me = next(f for f in fighters if f.cell == (11, 15))
    view = read_range(frame, me, fighters)
    assert view.spell_range == (3, 4)
    # Les deux blocs, en bas à gauche et à droite ; 18/10 : le pilier de droite est en (15, 17), coin bas de son dessus,
    # (14, 16) n'est que cachée en partie par lui (le jeu la colore).
    assert view.obstacles == {(10, 18), (15, 17)}
    assert (14, 16) in view.castable
    blockers = view.obstacles | ({f.cell for f in fighters} - {me.cell})
    assert sight_agreement(view, me.cell, blockers) >= 0.9  # pas une « carte à relief »


def test_in_line_spell_obstacles_only_on_its_lines():
    # 11/10 : Épée Céleste (1 à 3, en ligne) ; le jeu ne colore que les diagonales de l'écran.
    image = np.zeros((1009, 1920, 3), np.uint8)  # extrait (lignes 150-650, colonnes 800-1500) d'une capture 1920 x 1009
    image[150:650, 800:1500] = cv2.imread(str(FIXTURES / "portee_epee_celeste_en_ligne.png"))
    frame = Frame.of(image)
    fighters = find_fighters(frame)
    me = next(f for f in fighters if f.cell == (11, 13))
    view = read_range(frame, me, fighters)
    assert view.spell_range == (1, 3)
    kept = in_line_only(view, me.cell).obstacles
    assert len(view.obstacles) >= 10 and kept < view.obstacles
    assert all(abs(c[0] - 11) == abs(c[1] - 13) for c in kept)


def test_main_menu_is_a_known_window_closed_with_escape():
    # 11/10 : un Échap sans menu à fermer a ouvert le « Menu principal » (Changer de perso, Déconnecter, Quitter).
    image = np.zeros((1009, 1920, 3), np.uint8)  # extrait (lignes 180-620, colonnes 980-1520) d'une capture
    image[180:620, 980:1520] = cv2.imread(str(FIXTURES / "menu_principal.png"))
    found = find_known_popup(Frame.of(image), load_popup_specs())
    assert found is not None and found.spec.name == "menu principal" and found.spec.key == "escape"
    assert found.close_point is None  # jamais de clic dans ce menu
    assert find_known_popup(load_frame("hors_combat"), load_popup_specs()) is None


def test_pods_gauge_around_the_minimap():
    # 11/10 : hors combat, l'anneau de la minimap est la jauge des pods (orange depuis le haut, sens horaire).
    from bot.perception.ui import pods_fill

    image = np.zeros((1009, 1920, 3), np.uint8)  # extrait (lignes 760-1009, colonnes 1180-1480) d'une capture de Brawli
    image[760:1009, 1180:1480] = cv2.imread(str(FIXTURES / "jauge_pods_brawli.png"))
    assert 0.35 <= pods_fill(Frame.of(image)) <= 0.45
    assert pods_fill(Frame.of(np.zeros((1009, 1920, 3), np.uint8))) == 0.0  # anneau vide


def test_blocks_read_on_the_clean_tactical_map():
    # Mêmes blocs que ceux révélés par la portée, sans sélectionner le sort.
    with_range = load_frame("combat_portee_sort")
    fighters = find_fighters(with_range)
    me = next(f for f in fighters if f.team == RED)
    view = read_range(with_range, me, fighters)
    clean = load_frame("combat_mon_tour")  # même carte, aucun sort sélectionné
    blocks = read_blocks(clean, find_fighters(clean))
    assert {(15, 17), (16, 18), (17, 17), (18, 18)} <= blocks
    assert sight_agreement(view, me.cell, blocks | ({f.cell for f in fighters} - {me.cell})) >= 0.98


def test_terrain_of_astrub_with_raised_blocks_and_sunken_holes():
    # 14/10, -1,-19 (Cité d'Astrub), fenêtre de 1920 x 1057 : le dessus des blocs, remonté d'une demi-case, et leurs
    # côtés sombres recouvraient la droite et le bas des cases de sol voisines (lues blocs) ; les trous y sont des fosses
    # gris foncé, pas du vide noir. Natures prises dans les cases du client (tools/comparer_carte).
    frame = load_frame("combat_astrub_moins_1_moins_19")
    terrain = read_terrain(frame, find_fighters(frame))
    assert {(6, 4), (10, 4), (6, 6), (3, 7), (11, 3), (14, 20), (18, 26)} <= terrain.floor  # au bord d'un bloc
    assert {(8, 4), (7, 5), (9, 7), (19, 27), (21, 25)} <= terrain.blocks
    assert {(7, 3), (9, 3), (16, 6), (17, 27), (21, 5), (23, 25)} <= terrain.holes  # fosses
    unread = set(frame.grid.cells()) - terrain.seen
    assert {(2, 2), (4, 2)} <= unread  # sous le nom de la carte
    assert {(16, 32), (20, 32), (22, 32)} <= unread  # bas de la case sous l'interface
    assert {(24, 28), (26, 28), (27, 29)} <= unread  # sous les icônes au-dessus de la timeline


def test_terrain_under_the_fight_start_banner_is_not_read():
    # Bandeau « Le combat commence ! » (14/10) : une bande de toute la largeur, assombrie à 60 % (sol 135 -> 82).
    frame = load_frame("combat_astrub_moins_1_moins_19")
    image = frame.image.copy()
    image[354:472] = (image[354:472] * 0.6).astype(np.uint8)
    banner = Frame.of(image)
    clear, dimmed = read_terrain(frame, find_fighters(frame)), read_terrain(banner, find_fighters(banner))
    grid = frame.grid
    under = {c for c in grid.cells() if 354 - grid.half_h - 2 <= grid.center(c)[1] <= 472 + grid.half_h + 2}
    assert dimmed.seen.isdisjoint(under) and len(clear.seen & under) > 50
    for nature in ("floor", "blocks", "holes"):
        assert getattr(dimmed, nature) == getattr(clear, nature) - under


def test_placement_on_compressed_capture():
    # Capture JPEG d'un vrai placement : cases libres à 96 % seulement, le seuil relatif s'en accommode.
    placement = read_placement(load_frame("placement_moi_et_monstre"))
    assert len(placement.occupied[RED]) == 1 and len(placement.occupied[BLUE]) == 1


def test_entry_banner_seen_only_while_it_darkens_the_cells():
    for name in ("placement_bandeau", "placement_moi_et_monstre"):  # « Tu es entré en combat ! »
        assert entry_banner(load_frame(name)), name
    for name in ("placement_grand_personnage", "placement_foret", "preparation", "combat_mon_tour", "hors_combat"):
        assert not entry_banner(load_frame(name)), name


def spell_window_frame(name="combat_mon_tour", window="fenetre_sorts"):
    image = load_frame(name).image.copy()
    window = cv2.imread(str(FIXTURES / f"{window}.png"))
    image[40 : 40 + window.shape[0], 640 : 640 + window.shape[1]] = window
    return Frame.of(image)


def test_spell_window_reading():
    entries = read_spell_window(spell_window_frame())
    read = [(e.pa, e.spell_range, e.learned) for e in entries]
    assert read == [
        (4, (1, 3), True), (4, (2, 5), True), (4, (1, 10), True), (3, (1, 5), True), (4, (1, 3), True),  # appris
        (2, (0, 0), False), (4, (8, 10), False), (3, (1, 5), False), (2, (1, 1), False), (2, (1, 5), False),
    ]


def test_spells_located_in_the_bar():
    entries = read_spell_window(spell_window_frame())
    slots = locate_in_bar(load_frame("combat_mon_tour"), entries)
    assert {slot: e.row for slot, e in slots.items()} == {1: 0, 2: 1, 3: 2, 4: 3}  # Flèche Magique en case 3


def test_placement_ignores_cells_cut_by_the_map_edge():
    # Carte de la forêt : une case rouge coupée par la barre du bas paraissait occupée (deux « moi »).
    placement = read_placement(load_frame("placement_foret"))
    assert placement.occupied == {RED: {(16, 4)}, BLUE: {(6, 12)}}


@pytest.mark.parametrize("name, pm", [("combat_mon_tour", 3), ("combat_tour_adversaire", 3),
                                      ("combat_portee_sort", 0), ("combat_infobulle", 3),
                                      ("combat_5pa", 3), ("combat_3pa_sorts_grises", 3),  # JPEG : vert plus sombre
                                      ("combat_cra_10pa_4pm", 4), ("combat_cra_8pa_4pm", 4),  # 04/10 : 4 PM
                                      ("combat_iop_7pa_3pm", 3)])
def test_pm_read_in_the_bubble(name, pm):
    assert read_pm(load_frame(name)) == pm


@pytest.mark.parametrize("name, pa", [("combat_mon_tour", 6), ("combat_portee_sort", 6),
                                      ("combat_5pa", 5),  # un monstre m'a retiré 1 PA
                                      ("combat_3pa_sorts_grises", 3),  # puis Tir Éloigné (2 PA)
                                      # 04/10, personnages plus haut niveau : 10 PA, puis 8 (lu « 0 », le Crâ avait passé
                                      # son tour après Tir Éloigné) et 7 (illisible).
                                      ("combat_cra_10pa_4pm", 10), ("combat_cra_8pa_4pm", 8), ("combat_iop_7pa_3pm", 7),
                                      # 15/10, fenêtre de 1057 px de haut : le 6 à 0,58 de son modèle (seuil 0,6).
                                      ("bulle_pa_6_1057", 6)])
def test_pa_read_in_the_bubble(name, pa):
    assert read_pa(load_frame(name)) == pa


def test_no_pa_out_of_combat():
    assert read_pa(load_frame("hors_combat")) is None


def test_greyed_spells_in_the_bar():
    entries = read_spell_window(spell_window_frame("hors_combat", "fenetre_sorts_28_09"))  # 6 sorts appris
    slots = locate_in_bar(load_frame("combat_5pa"), entries)
    assert sorted(slots) == [1, 2, 3, 4, 5, 6]
    assert not any(slot_greyed(load_frame("combat_5pa"), s, e) for s, e in slots.items())  # 5 PA : tout lançable
    # 3 PA : seule Flèche Glacée (3 PA, case 4) reste en couleur ; Tir Éloigné (case 6) est en relance.
    greyed = {s for s, e in slots.items() if slot_greyed(load_frame("combat_3pa_sorts_grises"), s, e)}
    assert greyed == {1, 2, 3, 5, 6}


@pytest.mark.parametrize("name, low, high", [("coeur_43pv", 0.35, 0.55), ("hors_combat", 0.9, 1.0),
                                             ("placement_foret", 0.85, 1.0)])
def test_hp_read_from_the_heart_fill(name, low, high):
    assert low <= hp_ratio(load_frame(name)) <= high


def test_no_pm_out_of_combat():
    assert read_pm(load_frame("hors_combat")) is None


@pytest.mark.parametrize(
    "name, en_ligne, portee_modifiable, counts",
    [
        ("fiche_fleche_de_recul", True, False, {"lancers_par_tour": None, "lancers_par_cible": 1, "relance": None}),
        ("fiche_fleche_magique", False, True, {"lancers_par_tour": None, "lancers_par_cible": 2, "relance": None}),
        ("fiche_tir_eloigne", True, False, {"lancers_par_tour": None, "lancers_par_cible": None, "relance": 5}),
    ],
)
def test_spell_sheet_reading(name, en_ligne, portee_modifiable, counts):
    sheet = read_sheet(load_frame(name))
    assert sheet.checks["lancer_en_ligne"] is en_ligne
    assert sheet.checks["portee_modifiable"] is portee_modifiable
    assert sheet.checks["ligne_de_vue"] is True and sheet.checks["cellules_libres"] is False
    assert sheet.counts == counts


def test_spells_identified_by_their_icon():
    entries = read_spell_window(load_frame("fiche_fleche_de_recul"))  # liste déplacée à droite par la fiche
    assert [identify(e) for e in entries[:6]] == [
        "fleche_de_recul", "fleche_empoisonnee", "fleche_magique", "fleche_glacee", "fleche_enflammee", "tir_eloigne"]
    assert all(e.learned for e in entries[:6]) and not any(e.learned for e in entries[6:])


def test_placement_cells():
    placement = read_placement(load_frame("preparation"))
    assert placement.occupied == {RED: {(9, 21)}, BLUE: {(20, 20)}}  # moi et le monstre
    assert len(placement.free[RED]) == 10 and len(placement.free[BLUE]) == 10


def test_line_of_sight():
    assert line_of_sight((0, 0), (6, 0), set())
    assert not line_of_sight((0, 0), (6, 0), {(3, 1), (3, -1), (2, 0), (4, 0)})
    assert line_of_sight((0, 0), (6, 0), {(3, 3)})  # obstacle à côté de la ligne
    assert line_of_sight((0, 0), (1, 1), {(5, 5)})


def test_a_cell_touched_only_by_a_corner_does_not_block():
    # 17/10, Incarnam : 236 lignes touchant un combattant ou un bloc par un seul coin (20 entre deux combattants qui se
    # touchent par un coin), toutes montrées dégagées par le jeu ; la case du dessous d'une ligne horizontale bloquait.
    assert line_of_sight((0, 0), (4, 0), {(1, 1), (1, -1), (3, 1)})  # ligne horizontale à l'écran
    assert line_of_sight((0, 0), (0, 4), {(1, 1), (-1, 1)})  # verticale
    assert line_of_sight((11, 19), (7, 17), {(9, 19)})  # en biais, par le coin (9, 18)
    assert not line_of_sight((0, 0), (4, 0), {(2, 0)})  # case traversée
    assert not line_of_sight((11, 19), (7, 17), {(10, 18)})


def test_tactical_mode_button_found_when_off():
    frame = Frame.of(cv2.imread(str(FIXTURES / "placement_mode_normal.png")))
    assert not tactical_mode(frame)
    (x, y), _ = match_template(frame, TACTICAL_OFF, box=frame.layout.box(*TOOLBAR_ZONE))
    assert abs(x - 1746) <= 3 and abs(y - 687) <= 3
    for name in SCREENS:  # bouton coché (mode actif) ou absent : rien à cliquer
        other = load_frame(name)
        assert match_template(other, TACTICAL_OFF, box=other.layout.box(*TOOLBAR_ZONE)) is None


def test_tactical_mode():
    assert tactical_mode(load_frame("combat_mon_tour")) and tactical_mode(load_frame("preparation"))
    assert not tactical_mode(load_frame("hors_combat"))
    # 16/10 : petite carte entourée de vide noir (Incarnam 6,1) : 28 % de gris sur toute la zone de la carte.
    assert tactical_mode(load_frame("tactique_petite_carte_vide_noir"))


def test_pa_bubble_change_detection():
    frame = load_frame("combat_mon_tour")
    assert not region_changed(frame, frame, PA_BUBBLE)
    image = frame.image.copy()
    x0, y0, x1, y1 = frame.layout.box(*PA_BUBBLE)
    image[y0 + 8 : y1 - 8, x0 + 8 : x1 - 8] = 255
    assert region_changed(frame, Frame.of(image), PA_BUBBLE)


def test_movement_zone_of_hovered_monster():
    frame = load_frame("combat_infobulle")
    piou = frame.grid.cell_at(1100, 465)  # monstre survolé, 4 PM
    zone = movement_zone(frame)
    assert len(zone) >= 20
    assert all(distance(cell, piou) <= 4 for cell in zone)


def test_no_movement_zone_without_hover():
    assert movement_zone(load_frame("combat_mon_tour")) == set()


@pytest.mark.parametrize("name", SCREENS)
def test_no_unknown_window_on_known_screens(name):
    assert find_unknown_window(load_frame(name)) is None


def test_spell_slots():
    layout = Layout(1918, 1008)
    assert spell_slot_point(layout, 1) == (1533, 904)
    assert spell_slot_point(layout, 5) == (1737, 904)
    assert spell_slot_point(layout, 8) == (1533, 956)
    assert spell_slot_point(layout, 0) == (1476, 904)
    with pytest.raises(ValueError):
        spell_slot_point(layout, 15)


@pytest.mark.parametrize(
    "name, popup",
    [(name, None) for name in SCREENS + ["hors_combat_detection_enemi"]]
    + [("fin_de_combat", "résultat du combat"), ("fin_de_combat_level_up", "informations (niveau supérieur)"),
       ("combats_en_cours", "combats en cours"),  # 16/10 : liste des combats à regarder, ouverte par erreur
       ("challenge_en_cours", "challenge en cours"),  # 16/10 : en combat, cachait la carte
       ("fenetre_options", "options")],  # 18/10 : « Options » du menu de Flash, cliquée par le donjon
)
def test_known_windows(name, popup):
    match = find_known_popup(load_frame(name), load_popup_specs())
    assert (match.spec.name if match else None) == popup


def test_real_group_tooltip():
    frame = load_frame("hors_combat_detection_enemi")  # souris sur un groupe de 2 monstres
    (tooltip,) = find_group_tooltips(frame)
    assert all(abs(a - b) <= 4 for a, b in zip(tooltip.box, (1031, 273, 1363, 400)))
    assert tooltip.monsters == 2 and not tooltip.clipped


def test_overlapping_tooltips_with_z():
    # Touche Z : 5 groupes, « Niveau 9 » et « Niveau 3 » se chevauchent, « Niveau 9 » est collée au bord gauche.
    tooltips = sorted(find_group_tooltips(load_frame("groupes_z_chevauchement")), key=lambda t: t.x)
    assert [(round(t.x, -1), t.monsters, t.clipped) for t in tooltips] == [
        (720, 1, True), (810, 2, False), (960, 1, False), (1290, 2, False), (1480, 1, False)]
    niveau_9, _, niveau_3, _, _ = tooltips
    assert niveau_9.overlaps(niveau_3)


def test_tooltip_lines_stop_at_the_tooltip_below():
    # « Niveau 7 » recouvre le bas de « Niveau 4 » : ses lignes ne sont pas comptées pour « Niveau 4 ».
    tooltips = find_group_tooltips(load_frame("groupes_z_chevauchement_2"))
    assert len(tooltips) == 5 and all(t.monsters <= 2 for t in tooltips)


@pytest.mark.parametrize("name", SCREENS + ["fin_de_combat", "fin_de_combat_level_up"])
def test_no_group_tooltip_on_screens_without_one(name):
    assert find_group_tooltips(load_frame(name)) == []


def test_screen_sizes_that_crashed_the_bot():
    # 03/10 (analyse des tailles d'écran) : souris écartée hors de la fenêtre en 16:9 et 16:10 (le bot s'arrêtait),
    # infobulles de groupes à 1008 px de haut pile (bande de 0 ligne), fenêtre des sorts sur un écran plus petit.
    import cv2

    from bot.perception.frame import Frame
    from bot.perception.layout import Layout
    from bot.perception.spellbook import read_spell_window
    from bot.perception.ui import park_point

    for width, height in ((1920, 1080), (1920, 1129), (1280, 729)):
        layout = Layout(width, height)
        assert all(0 <= x < width and 0 <= y < height for x, y in (park_point(layout) for _ in range(300)))
    z = load_frame("groupes_z_bord_droit").image
    for height in (1008, 600):
        width = round(z.shape[1] * height / z.shape[0])
        assert len(find_group_tooltips(Frame.of(cv2.resize(z, (width, height), interpolation=cv2.INTER_AREA)))) == 5
    window = spell_window_frame("hors_combat", "fenetre_sorts_28_09").image
    small = cv2.resize(window, (round(window.shape[1] * 841 / window.shape[0]), 841), interpolation=cv2.INTER_AREA)
    read = [(e.level, e.pa, e.spell_range) for e in read_spell_window(Frame.of(small)) if e.learned]
    assert read == [(2, 4, (1, 4)), (1, 4, (2, 5)), (4, 4, (1, 10)), (1, 3, (1, 5)), (2, 4, (1, 4)), (2, 2, (0, 0))]


def test_end_turn_button_not_confused_with_the_timer_ring():
    from bot.perception.ui import find_end_turn_button

    # Audit du 03/10 : au placement, le morceau de l'anneau du timer dans la zone du bouton fait 648 px pour un seuil
    # de 700 ; s'il passait, le placement serait lu comme un combat. La largeur du bouton les sépare.
    assert find_end_turn_button(load_frame("placement_mode_normal")) is None
    assert find_end_turn_button(load_frame("combat_mon_tour")) is not None


def test_click_spot_not_on_a_fighter_standing_in_front_of_the_cell():
    from bot.perception.fighters import Fighter, clear_point

    grid = load_frame("combat_mon_tour").grid
    target = (10, 10)
    x, y = grid.center(target)
    assert clear_point(grid, target, [])[0] == (x, y)  # rien devant : le centre
    front = Fighter("red", *grid.center((10, 12)), (10, 12))  # juste devant : son corps couvre le centre de la case
    (px, py), _ = clear_point(grid, target, [front])
    assert grid.cell_at(px, py) == target and abs(px - front.x) >= 0.55 * grid.half_w  # un coin dégagé de la case


def test_tall_result_window_of_a_big_fight_is_recognised():
    # 03/10 : combat à 4 contre 5, fenêtre de résultat plus haute, ses lignes sombres ne laissent que 28 % de beige
    # dans son rectangle (seuil 30 % avant) : jamais reconnue, restée ouverte 7 minutes, la carte cachée.
    frame = load_frame("resultat_combat_4_contre_5")
    match = find_known_popup(frame, load_popup_specs())
    assert match is not None and match.spec.name == "résultat du combat"
    assert abs(match.close_point[0] - 1675) <= 5 and abs(match.close_point[1] - 717) <= 5  # « Fermer », plus bas


# --- Menu de Flash (clic droit à côté d'un monstre, 14/10) ------------------------------------------------------------

def test_flash_menu_found_on_its_capture():
    frame = load_frame("menu_flash_clic_droit")
    for found in (find_flash_menu(frame), find_flash_menu(frame, around=FLASH_MENU_CLICK)):
        assert found is not None and abs(found[0] - 1570) <= 3 and abs(found[1] - 135) <= 3  # « DOFUS RETRO Client »
    assert find_unknown_window(frame) is not None  # le 14/10, il passait pour une fenêtre inconnue


@pytest.mark.parametrize("name", SCREENS + [
    "fin_de_combat", "fin_de_combat_level_up", "resultat_combat_4_contre_5", "hors_combat_menu_personnage",
    "epee_pnj_menu_parler", "amis_menu_inviter", "groupes_z_toit_de_paille", "groupes_z_sable_calanques",
    "hors_combat_detection_enemi", "connexion_choix_personnage",
])
def test_no_flash_menu_on_other_captures(name):
    frame = load_frame(name)
    assert find_flash_menu(frame) is None
    assert find_flash_menu(frame, around=(frame.layout.width // 2, frame.layout.height // 3)) is None


def test_no_flash_menu_once_closed():
    frame = without_flash_menu()
    assert find_flash_menu(frame) is None and find_flash_menu(frame, around=FLASH_MENU_CLICK) is None


def test_flash_menu_found_whether_or_not_it_follows_the_window_size():
    # Fenêtre de référence (1008 px de haut) : menu à la taille de sa capture (1057 px de haut)...
    frame = with_flash_menu(load_frame("hors_combat"), (1100, 300))
    assert find_flash_menu(frame) is not None and find_flash_menu(frame, around=(1100, 300)) is not None
    # ... ou réduit avec toute la fenêtre.
    image = load_frame("menu_flash_clic_droit").image
    scale = 1008 / image.shape[0]
    small = Frame.of(cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
    assert find_flash_menu(small) is not None


def test_flash_menu_only_searched_near_the_click():
    frame = load_frame("menu_flash_clic_droit")
    assert find_flash_menu(frame, around=(800, 600)) is None


@pytest.mark.parametrize("name", ["menu_flash_clic_droit", "hors_combat", "hors_combat_prairie",
                                  "groupes_z_bord_droit"])
def test_flash_close_point_is_plain_beige_under_the_spell_bar(name):
    # 15/10 : le clic dans le chat ne fermait pas le menu de Flash ; ailleurs dans la fenêtre, si (utilisateur).
    frame = load_frame(name)
    x, y = flash_close_point(frame)
    assert not frame.layout.on_map((x, y)) and x > frame.layout.width * 0.7 and y > frame.layout.height * 0.9
    r = round(frame.layout.length(6))
    patch = frame.image[y - r : y + r + 1, x - r : x + r + 1].astype(int)
    assert (np.abs(patch - np.array(UI_BEIGE)).max(axis=2) <= 10).all()


@pytest.mark.parametrize("name", ["combat_cra_10pa_4pm", "combat_mon_tour", "combat_menu_flash_sur_timeline"])
def test_flash_close_point_in_combat_is_right_of_the_end_turn_button(name):
    # En combat, le bouton de fin de tour est sous le point d'avant : le fond beige sous la 2e rangée de sorts.
    frame = load_frame(name)
    x, y = flash_close_point(frame)
    end_turn_x, _ = frame.layout.point(1465, 991)
    assert not frame.layout.on_map((x, y)) and x > end_turn_x + frame.layout.length(150)


def test_flash_menu_left_on_the_timeline_found_anywhere():
    # 17/10 : ouvert par le clic droit qui a fait entrer Vertdepomme dans le combat, par-dessus la timeline : loin de
    # toute surface beige de la carte, pas trouvé sans le clic ; tour illisible à chacun de ses tours.
    frame = load_frame("combat_menu_flash_sur_timeline")
    assert find_flash_menu(frame) is None
    x, y = find_flash_menu(frame, anywhere=True)
    assert abs(x - 1469) <= 3 and abs(y - 701) <= 3


def test_cells_under_a_long_timeline_are_not_obstacles():
    # 15/10, équipe de 8 : la timeline de 9 portraits couvre le bas de la portée de Lancer de Pièces (1 à 8) ; ses cases
    # non colorées étaient prises pour des blocs (ligne de vue prédite juste sur 55 % des cases, carte crue à relief).
    frame = load_frame("combat_equipe_8_timeline_longue")
    portraits = timeline_portraits(frame)
    assert len(portraits) == 9
    fighters = find_fighters(frame)
    me = Fighter(RED, *frame.grid.center((18, 30)), (18, 30))  # Dinarpepe, caché sous la timeline
    view = read_range(frame, me, fighters)
    left, top = min(b[0] for b in portraits), min(b[1] for b in portraits)
    assert not any(frame.grid.center(c)[0] >= left and frame.grid.center(c)[1] >= top for c in view.obstacles)
    assert view.castable  # la portée reste lue


def test_void_around_the_map_inside_a_range_is_not_an_obstacle():
    # 16/10, Incarnam 6,1 : la portée déborde sur le vide autour de la plateforme, teinté bleu nuit ; ces cases étaient
    # prises pour des blocs (plus de cent), qui coupaient toutes les lignes de vue. Le vide est un trou.
    frame = load_frame("portee_sur_le_vide_incarnam_6_1")
    fighters = find_fighters(frame)
    me = Fighter(RED, *frame.grid.center((12, 14)), (12, 14))
    view = read_range(frame, me, fighters)
    assert view.castable
    for cell in view.obstacles:
        x, y = (round(v) for v in frame.grid.center(cell))
        b, g, r = (int(v) for v in frame.image[y, x])
        assert not (abs(b - 45) <= 14 and abs(g - 30) <= 14 and abs(r - 10) <= 14), cell  # pas le vide bleu nuit
    assert len(view.obstacles) < 30


def relief_frame(name: str, top: int, left: int) -> Frame:
    """Extrait d'une capture 1920 x 1057 du 17/10 (Incarnam, 8 personnages), recollé à sa place sur fond noir."""
    image = np.zeros((1057, 1920, 3), np.uint8)
    crop = cv2.imread(str(FIXTURES / f"{name}.png"))
    image[top:top + crop.shape[0], left:left + crop.shape[1]] = crop
    return Frame.of(image)


def relief_view(frame: Frame, me_cell):
    fighters = find_fighters(frame)
    me = Fighter(RED, *frame.grid.center(me_cell), me_cell)
    view = read_range(frame, me, fighters)
    return view, view.obstacles | ({f.cell for f in fighters} - {me_cell})


def test_walls_of_a_plateau_and_a_pit_are_not_obstacles():
    # 17/10, Incarnam 2,0 : les côtés du plateau et les parois de la fosse, teintés par la portée de Flèche Magique,
    # étaient lus comme des blocs ; ligne de vue prédite juste sur 71 % des cases, carte crue à relief (le jeu montrait
    # à portée le plateau à gauche et le fond de la fosse à droite).
    frame = relief_frame("portee_relief_plateau_et_fosse_incarnam_2_0", 330, 940)
    view, blockers = relief_view(frame, (17, 27))  # Pombro
    assert view.spell_range == (1, 7)
    assert len(view.walls) >= 5 and not view.obstacles
    assert {(11, 27), (13, 29), (24, 22), (22, 28)} <= view.castable  # plateau, fond de la fosse
    assert view.no_sight  # les cases cachées par les combattants restent sans ligne de vue
    assert sight_agreement(view, (17, 27), blockers) == 1.0


def test_cell_under_the_forbidden_cursor_is_not_an_obstacle():
    # 17/10 : le curseur « interdit », laissé par le survol d'une cible, couvrait la case (16, 24) à moitié colorée ;
    # lue bloc, elle coupait 12 lignes de vue vers la gauche (71 % de cases justes avec les parois de la fosse).
    frame = relief_frame("portee_relief_curseur_interdit", 380, 1000)
    view, blockers = relief_view(frame, (18, 24))  # Leproduitlatte
    assert (16, 24) not in view.obstacles and (16, 24) not in view.castable
    assert view.walls and not view.obstacles
    assert sight_agreement(view, (18, 24), blockers) == 1.0


def test_forbidden_cursor_is_not_a_fighter():
    # 17/10 : le curseur « interdit » (rond rouge barré de 43 px) passait pour un morceau de cercle rouge, un allié
    # inventé en (13, 21) coupait 11 lignes de vue sur 69 (86 %).
    frame = relief_frame("portee_relief_curseur_pris_pour_un_cercle", 300, 700)
    fighters = find_fighters(frame)
    assert (13, 21) not in {f.cell for f in fighters}
    assert {(9, 19), (11, 19), (11, 21)} <= {f.cell for f in fighters}  # les vrais cercles autour restent
    view, blockers = relief_view(frame, (11, 19))  # Leproduitlatte
    assert (7, 17) in view.castable  # la ligne ne touche (9, 19) que par un coin
    assert sight_agreement(view, (11, 19), blockers) == 1.0


def test_raised_pillars_of_the_forest_block_only_their_own_cell():
    # 18/10, forêt d'Incarnam 2,4 : piliers et plateaux à dessus blanc, remontés d'une demi-case, jamais teintés par la
    # portée. Leur relief cachait le bas des cases juste au-dessus d'eux, lues blocs ; un pilier caché par le corps de
    # l'ennemi posé devant était oublié. Ligne de vue prédite juste sur 90 % des cases (88 % sur 13 captures).
    frame = relief_frame("portee_foret_piliers_incarnam_2_4", 400, 740)
    view, blockers = relief_view(frame, (14, 26))  # Vertdepomme, Flèche Magique
    assert view.spell_range == (1, 9)
    assert {(11, 23), (14, 22), (17, 23)} <= view.obstacles  # piliers, (17, 23) sous le corps de l'ennemi en (17, 25)
    assert not {(13, 21), (16, 22)} & view.obstacles  # cases derrière un pilier
    assert sight_agreement(view, (14, 26), blockers) == 1.0


def test_caster_found_on_a_cell_counted_under_the_timeline():
    # 18/10, forêt d'Incarnam 3,4 : Vertdepomme juste au-dessus de la timeline, sa case comptée sous les portraits ;
    # le centre trouvé une case à côté désignait un autre cercle, toutes les lignes de vue prédites de là. La case sous
    # les boutons du combat (au-dessus de la fin de la timeline) était lue bloc.
    frame = relief_frame("portee_foret_plateau_lanceur_sur_la_timeline_incarnam_3_4", 470, 990)
    assert range_center(frame, (1, 9)) == (19, 29)
    view, blockers = relief_view(frame, (19, 29))
    assert (26, 28) not in view.obstacles and (26, 28) in view.hidden
    assert {(20, 20), (21, 21), (22, 20)} <= view.obstacles  # bord du plateau
    assert sight_agreement(view, (19, 29), blockers) == 1.0


def test_spectator_list_closed_with_its_fermer_button():
    match = find_known_popup(load_frame("combats_en_cours"), load_popup_specs())
    assert match.spec.name == "combats en cours"
    assert abs(match.close_point[0] - 1406) <= 6 and abs(match.close_point[1] - 729) <= 6  # « Fermer », pas « Rejoindre »


def test_caster_found_when_the_range_spills_over_the_void_and_under_the_timeline():
    # 16/10, Incarnam 6,1 : portée de Lancer de Pièces (0 à 8) sur le vide (bleu nuit) et sous la timeline ; le losange
    # coupé plaçait Pesopepe en (19, 21), au-dessus du vide : 7 tirs refusés, 43 s perdues sur deux tours.
    assert range_center(load_frame("reperage_portee_sur_le_vide"), (0, 8)) == (19, 25)


def test_npc_dialog_found_by_its_cross():
    # 17/10 : clic de sonde sur Matu Vuh ; sa bulle cachait le décor et bloquait la marche (session arrêtée).
    x, y = find_npc_dialog(load_frame("dialogue_pnj_matu_vuh"))
    assert abs(x - 1149) <= 3 and abs(y - 276) <= 3


@pytest.mark.parametrize("name", ["hors_combat", "hors_combat_prairie", "fin_de_combat", "hors_combat_menu_personnage",
                                  "epee_pnj_menu_parler", "menu_flash_clic_droit", "combat_mon_tour", "preparation"])
def test_no_npc_dialog_elsewhere(name):
    assert find_npc_dialog(load_frame(name)) is None


def test_mouse_moved_away_inside_the_flash_scene_never_in_the_chat():
    # 17/10 : écartée dans le chat (hors de la scène Flash), la souris restait pour le jeu sur le monstre visé, et le
    # même sort sur lui était ignoré. Toujours à droite du chat, sous la carte, dans la fenêtre.
    from bot.perception.layout import Layout
    from bot.perception.ui import scene_park_point

    for width, height in ((1920, 1057), (1918, 1008), (1280, 729)):
        layout = Layout(width, height)
        for x, y in (scene_park_point(layout) for _ in range(300)):
            assert layout.map_box[0] <= x < width and layout.map_box[3] <= y < height


@pytest.mark.parametrize("name, count", [("challenge_icone_dinarpepe", 1), ("challenge_deux_icones", 2),
                                         ("combat_cercles_colles", 1), ("combat_mon_tour", 0), ("combat_portee_sort", 0)])
def test_challenge_icons_over_the_map(name, count):
    # 20/10 : icônes des challenges (carrés orange, pourcentage dessous) en haut à gauche de la carte, par-dessus des
    # cases : jamais cliquées pour viser une case.
    icons = challenge_icons(load_frame(name))
    assert len(icons) == count
    assert all(x1 - x0 < 90 and y1 - y0 < 110 for x0, y0, x1, y1 in icons)


def test_click_point_avoids_a_challenge_icon():
    from bot.perception.fighters import clear_point

    frame = load_frame("challenge_icone_dinarpepe")
    icons = challenge_icons(frame)
    (px, py), _ = clear_point(frame.grid, (2, 8), [], icons)
    assert not any(x0 <= px <= x1 and y0 <= py <= y1 for x0, y0, x1, y1 in icons)


@pytest.mark.skipif(not __import__("bot.perception.chat", fromlist=["x"]).ocr_available(), reason="OCR de Windows absent")
def test_group_level_read_in_its_tooltip_title():
    # 23/10 : engage.max_level écarte les groupes trop forts pour l'équipe (Porkass jusqu'au niveau 30 par monstre).
    from bot.perception.groups import find_group_tooltips, read_group_level

    frame = load_frame("groupe_niveau_7")
    (tooltip,) = find_group_tooltips(frame)
    assert read_group_level(frame, tooltip) == 7


def test_trade_offered_by_another_player_is_refused_with_escape():
    # 25/10, Pombro : échange proposé par un autre joueur, ouvert à la connexion ; la carte et « /p » illisibles
    # dessous, le bot arrêté. Fenêtre connue, refermée par Échap (refus) : jamais « Accepter ».
    found = find_known_popup(load_frame("echange_propose_par_un_joueur"), load_popup_specs())
    assert found is not None and found.spec.name == "échange" and found.spec.key == "escape"


def test_fighters_found_on_cells_one_ground_level_lower():
    # 25/10, Taïnéla 5,-30 : combat dans le coin bas-droit, une marche plus bas (niveau de sol 6, carte officielle) ;
    # cercles dessinés ~14 px plus haut que la grille plate : aucun trouvé, tous les tours passés. 7 rouges et 5 bleus
    # visibles, une fois chacun.
    fighters = find_fighters(load_frame("combat_coin_une_marche_plus_bas"))
    teams = [f.team for f in fighters]
    assert teams.count("red") == 7 and teams.count("blue") == 5
    assert len({(f.team, f.cell) for f in fighters}) == len(fighters)


def test_flash_close_point_stays_off_the_fights_eye():
    # 25/10 : l'œil « combats en cours » (un autre combat sur la carte) juste au-dessus de l'ancien point de fermeture
    # du menu de Flash : le clic l'a ouvert. Le point est descendu dans la bande beige, beige uni au-dessus de lui.
    frame = load_frame("oeil_combats_en_cours_sous_la_carte")
    x, y = flash_close_point(frame)
    up, r = round(frame.layout.length(16)), round(frame.layout.length(6))
    patch = frame.image[y - up : y + r + 1, x - r : x + r + 1].astype(int)
    assert (np.abs(patch - np.array(UI_BEIGE)).max(axis=2) <= 10).all()
    eye_y = frame.layout.point(1465, 991)[1] - round(frame.layout.length(20))  # l'œil, ~20 px au-dessus de l'ancien point
    assert y - eye_y >= frame.layout.length(25)
