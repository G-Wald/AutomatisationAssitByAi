import random

import pytest

from bot.actions import keys
from bot.actions.input import Human, scatter
from bot.config import load_settings

AZERTY = 0x040C040C  # disposition Français (France)


def test_digit_keys_are_the_top_row_whatever_the_layout():
    # Raccourcis des cases de sorts : rangée du haut, sans Maj (& é " ' ... en AZERTY).
    (three,) = keys.resolve("3", AZERTY)
    assert three.vk == 0x33 and three.scan == 0x04


def test_letters_follow_the_keyboard_layout():
    (z,) = keys.resolve("z", AZERTY)
    assert z.vk == 0x5A and z.scan == 0x11  # le Z d'un AZERTY est à la place du W d'un QWERTY


def test_combinations_and_named_keys():
    ctrl, one = keys.resolve("ctrl+1", AZERTY)
    assert ctrl.vk == 0x11 and one.vk == 0x31
    assert keys.resolve("escape")[0].vk == 0x1B
    numpad = keys.resolve("num7", AZERTY)[0]
    assert numpad.vk == 0x67 and numpad.scan == 0x47  # 7 du pavé numérique
    with pytest.raises(ValueError):
        keys.resolve("echap")
    with pytest.raises(ValueError):
        keys.resolve("ctrl+")


def test_scatter_stays_in_target_and_varies():
    random.seed(1)
    points = [scatter((100, 50), (20, 10)) for _ in range(500)]
    assert all(abs(x - 100) / 20 + abs(y - 50) / 10 <= 1.1 for x, y in points)
    assert len(set(points)) > 50  # jamais toujours le même pixel
    assert scatter((100, 50), (0, 0)) == (100, 50)


def test_human_settings_from_config():
    human = Human.from_settings(load_settings())
    assert human.move_time[0] < human.move_time[1]
    assert 0 <= human.hesitation_chance <= 1
    assert Human.from_settings({}) == Human()
