import time

import cv2
import pytest

from bot.perception.frame import Frame
from bot.perception.toolbar import find_creature_button, find_tactical_button
from conftest import load_frame

# Boutons dans le repère de référence (x0, y0, x1, y1), mesurés le 04/10 sur les captures des journaux. Cube : en
# combat, au placement (cadenas et visage en plus : un bouton plus à gauche), au placement en groupe (« seulement le
# groupe » en plus : deux boutons plus à gauche, 36 px chacun). Boule : en combat seulement.
CUBE_COMBAT = (1762, 673, 1798, 699)
CUBE_PLACEMENT = (1726, 673, 1762, 699)
CUBE_GROUP = (1690, 673, 1726, 699)
BALL = (1803, 674, 1838, 699)


def assert_on_button(frame, button, box_ref):
    """Le point de clic tombe sur le bouton, et pas sur la flèche ▽ du tour (contour orange, intérieur blanc)."""
    x0, y0, x1, y1 = frame.layout.box(*box_ref)
    x, y = button.point
    assert x0 <= x < x1 and y0 <= y < y1, button
    b, g, r = (int(v) for v in frame.image[y, x])
    assert not (r >= 190 and 60 <= g <= 160 and b <= 100), button  # orange de la flèche
    assert min(b, g, r) < 235, button  # blanc de la flèche
    assert button.room >= 2, button


@pytest.mark.parametrize(
    "name, cube_box, cube_on, ball_on",
    [
        # 04/10 : flèche ▽ du tour posée sur le cube non coché ; le modèle entier ne le reconnaissait plus, le mode
        # tactique n'a jamais été activé (« bouton du mode tactique introuvable »).
        ("combat_fleche_sur_cube", CUBE_COMBAT, False, False),
        ("combat_tour_adversaire", CUBE_COMBAT, True, False),  # flèche ▽ sur le cube coché : le haut de la coche sort
        ("combat_cercle_cache", CUBE_COMBAT, True, False),
        ("combat_cube_non_coche", CUBE_COMBAT, False, False),  # JPEG, sur l'herbe
        ("combat_mode_creature", CUBE_COMBAT, True, True),  # JPEG, mode créature actif (03/10)
        ("combat_fleche_sur_boule", CUBE_COMBAT, True, True),  # flèche ▽ sur la moitié droite de la boule cochée
        ("combat_mon_tour", CUBE_COMBAT, True, False),  # flèche ▽ sur l'œil
        ("combat_trou_cache", CUBE_COMBAT, True, True),
        ("combat_sadida_derriere_allies_tour1", CUBE_COMBAT, True, True),
        ("combat_infobulle", CUBE_COMBAT, True, False),
        ("preparation", CUBE_PLACEMENT, True, None),  # placement : pas de boule, le visage est à sa place
        ("placement_mode_normal", CUBE_PLACEMENT, False, None),
        ("placement_case_ambigue", CUBE_GROUP, True, None),
        ("placement_groupe_cube_non_coche", CUBE_GROUP, False, None),
    ],
)
def test_toolbar_buttons(name, cube_box, cube_on, ball_on):
    frame = load_frame(name)
    cube = find_tactical_button(frame)
    assert cube is not None and cube.active is cube_on, cube
    assert_on_button(frame, cube, cube_box)
    ball = find_creature_button(frame)
    if ball_on is None:
        assert ball is None, ball
    else:
        assert ball is not None and ball.active is ball_on, ball
        assert_on_button(frame, ball, BALL)


def test_turn_marker_hides_most_of_the_cube():
    cube = find_tactical_button(load_frame("combat_fleche_sur_cube"))
    assert cube.hidden >= 0.5  # 0,68 : la flèche en cache plus de la moitié, le reste suffit
    assert cube.point[0] < 1772  # clic sur la partie gauche, visible, du cube (la flèche commence à x = 1772)


@pytest.mark.parametrize("name", ["hors_combat", "hors_combat_prairie", "hors_combat_foret", "groupes_z_bord_droit",
                                  "fin_de_combat", "rejoindre_epee"])
def test_no_toolbar_out_of_combat(name):
    frame = load_frame(name)
    assert find_tactical_button(frame) is None and find_creature_button(frame) is None


@pytest.mark.parametrize("scale", [0.8, 1.15])
def test_toolbar_buttons_in_a_resized_window(scale):
    # Capture réduite ou agrandie (autre taille de fenêtre) : modèles mis à l'échelle.
    image = load_frame("combat_mode_creature").image
    frame = Frame.of(cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
    cube, ball = find_tactical_button(frame), find_creature_button(frame)
    assert cube is not None and cube.active and ball is not None and ball.active
    assert_on_button(frame, cube, CUBE_COMBAT)
    assert_on_button(frame, ball, BALL)


def test_toolbar_search_is_fast():
    frame = load_frame("combat_fleche_sur_cube")
    find_tactical_button(frame), find_creature_button(frame)  # modèles chargés
    started = time.perf_counter()
    for _ in range(10):
        find_tactical_button(frame), find_creature_button(frame)
    # Mesuré le 04/10 : 1,5 ms pour les deux boutons (médiane sur 778 captures).
    assert (time.perf_counter() - started) / 10 < 0.03
