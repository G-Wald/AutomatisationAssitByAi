from dataclasses import replace

import pytest

from bot.perception.colors import BLUE, RED
from bot.state.scene import Phase, is_my_turn, read_scene
from conftest import load_frame


@pytest.mark.parametrize(
    "name, phase, team, timer",
    [
        ("hors_combat", Phase.OUT_OF_COMBAT, None, False),
        ("preparation", Phase.PLACEMENT, None, False),
        ("combat_mon_tour", Phase.COMBAT, RED, True),
        ("combat_tour_adversaire", Phase.COMBAT, BLUE, False),
        ("combat_portee_sort", Phase.COMBAT, RED, True),
        ("combat_infobulle", Phase.COMBAT, RED, True),  # fin du décompte : petit arc à droite du cœur
    ],
)
def test_read_scene(name, phase, team, timer):
    scene = read_scene(load_frame(name))
    assert scene.phase is phase
    assert scene.current_team == team
    assert scene.timer_visible is timer


def test_ready_button():
    x, y = read_scene(load_frame("preparation")).ready_button
    assert abs(x - 1821) <= 5 and abs(y - 737) <= 5


def test_end_turn_button():
    x, y = read_scene(load_frame("combat_mon_tour")).end_turn_button
    assert 1420 <= x <= 1490 and 945 <= y <= 990


def test_is_my_turn():
    assert is_my_turn(read_scene(load_frame("combat_mon_tour")), None)  # timer visible
    assert not is_my_turn(read_scene(load_frame("combat_tour_adversaire")), RED)
    assert not is_my_turn(read_scene(load_frame("preparation")), RED)


def test_my_turn_from_timeline_without_timer():
    scene = replace(read_scene(load_frame("combat_mon_tour")), timer_visible=False)
    assert is_my_turn(scene, RED)
    assert not is_my_turn(scene, BLUE)
    assert not is_my_turn(scene, None)
