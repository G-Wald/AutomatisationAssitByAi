import pytest
from pynput import keyboard

from bot.actions.stop import BotStopped, StopControl, parse_key


def test_parse_key():
    assert parse_key("F12") == keyboard.Key.f12
    assert parse_key("x") == keyboard.KeyCode.from_char("x")
    with pytest.raises(ValueError):
        parse_key("f99")


def test_stop_key_interrupts_waits():
    control = StopControl("f12", "f9")
    control.sleep(0.01)
    control._on_press(keyboard.Key.f12)
    with pytest.raises(BotStopped):
        control.sleep(10)
    assert control.stopped and "F12" in control.reason


def test_pause_key_toggles():
    control = StopControl("f12", "f9")
    control._on_press(keyboard.Key.f9)
    assert control._paused.is_set()
    control._on_press(keyboard.Key.f9)
    assert not control._paused.is_set()
    control.check()  # ne bloque plus


def test_stop_while_paused():
    control = StopControl("f12", "f9")
    control._on_press(keyboard.Key.f9)
    control._on_press(keyboard.Key.f12)
    with pytest.raises(BotStopped):
        control.check()
