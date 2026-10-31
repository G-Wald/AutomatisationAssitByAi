"""Mode arrière-plan (08/10) : gestes envoyés à la fenêtre du jeu par messages, capture de la fenêtre recouverte.

Les fonctions Windows sont remplacées par des fausses : aucune fenêtre réelle n'est touchée. Ce qui est vérifié vient
des tests de l'utilisateur sur le vrai client (tools/arriere_plan*.py) : clic d'un bloc (souris ici, bouton enfoncé,
relâché), jamais deux fois au même pixel, pas de message de survol, touches enfoncées puis relâchées, pas de
combinaison avec Ctrl."""

import argparse

import numpy as np
import pytest
import win32con

import bot.actions.background as background
from bot.actions.background import BackgroundInput, pack
from bot.actions.input import Human
from bot.brain.combat import CombatAI, CombatSettings
from bot.brain.tactics import CombatSpell
from bot.brain.game import Game
from bot.capture.window import GameWindow
from bot.team.member import Focus
from conftest import load_frame
from test_brain import FakeRecorder

GAME, RENDER = 1000, 1001  # fenêtre du jeu, sa fenêtre interne qui reçoit les entrées
QUICK = Human(move_time=(0, 0), reaction=(0, 0), hold=(0, 0), hesitation_chance=0, slowdown=(1, 1))


class Control:
    def check(self) -> None:
        pass

    def sleep(self, seconds: float) -> None:
        pass


@pytest.fixture
def posted(monkeypatch) -> list[tuple[int, int, int, int]]:
    """Messages envoyés : (fenêtre, message, wParam, lParam). Fenêtre de 1918 x 1008 en (100, 50) à l'écran ; sa
    fenêtre interne la recouvre exactement."""
    sent: list[tuple[int, int, int, int]] = []
    monkeypatch.setattr(background.win32api, "PostMessage", lambda *message: sent.append(message))
    monkeypatch.setattr(background.win32api, "GetKeyboardLayout", lambda thread: 0x040C040C)  # AZERTY
    monkeypatch.setattr(background.win32process, "GetWindowThreadProcessId", lambda hwnd: (7, 8))
    monkeypatch.setattr(background.win32gui, "IsWindow", lambda hwnd: True)
    monkeypatch.setattr(background.win32gui, "GetClassName",
                        lambda hwnd: "Chrome_RenderWidgetHostHWND" if hwnd == RENDER else "Intermediate D3D Window")
    monkeypatch.setattr(background.win32gui, "EnumChildWindows",
                        lambda hwnd, callback, extra: [callback(child, extra) for child in (1002, RENDER)])
    monkeypatch.setattr(background.win32gui, "GetClientRect", lambda hwnd: (0, 0, 1918, 1008))
    monkeypatch.setattr(background.win32gui, "ClientToScreen", lambda hwnd, point: (point[0] + 100, point[1] + 50))
    monkeypatch.setattr(background.win32gui, "ScreenToClient", lambda hwnd, point: (point[0] - 100, point[1] - 50))
    return sent


def make_input(enabled: bool = True) -> BackgroundInput:
    return BackgroundInput(GameWindow(GAME, "Gela-[A13] - Dofus Retro v1.49.6"), Control(), QUICK, enabled)


def kinds(sent) -> list[int]:
    return [message for _, message, _, _ in sent]


def test_click_sent_in_one_block_to_the_input_window(posted):
    gestures = make_input()
    assert gestures.click((500, 300))
    assert kinds(posted) == [win32con.WM_MOUSEMOVE, win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP]
    assert {hwnd for hwnd, *_ in posted} == {RENDER}
    assert posted[0][3] == posted[1][3] == posted[2][3] == pack(500, 300) and posted[1][2] == win32con.MK_LBUTTON
    gestures.click((800, 600), button="right")
    assert kinds(posted)[3:] == [win32con.WM_MOUSEMOVE, win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP]


def test_never_twice_the_same_pixel(posted):
    # 08/10 : un « souris ici » au pixel du précédent est ignoré par le jeu, et le clic rate (5 sur 5).
    gestures = make_input()
    gestures.click((500, 300))
    gestures.click((500, 300))
    first, second = posted[0][3], posted[3][3]
    assert first != second and posted[4][3] == posted[5][3] == second


def test_move_sends_nothing_and_click_goes_where_the_mouse_was_put(posted):
    # Pas de survol en arrière-plan ; un message de survol rendrait le clic suivant au même endroit inutile.
    gestures = make_input()
    aim = gestures.move((640, 420))
    assert posted == [] and gestures.cursor() == aim
    gestures.click()
    assert kinds(posted) == [win32con.WM_MOUSEMOVE, win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP]
    assert posted[0][3] == pack(*aim)


def test_click_outside_the_window_is_cancelled(posted):
    assert make_input().click((2500, 300)) is False and posted == []


def test_keys_pressed_then_released_and_no_ctrl_combination(posted):
    gestures = make_input()
    gestures.press("escape")
    assert kinds(posted) == [win32con.WM_KEYDOWN, win32con.WM_KEYUP] and posted[0][2] == 0x1B
    assert posted[1][3] & (1 << 31)  # relâchée
    with pytest.raises(ValueError, match="arrière-plan"):
        gestures.press("ctrl+3")


def test_held_key_repeats_until_released(posted, monkeypatch):
    monkeypatch.setattr(background, "KEY_REPEAT", 0.01)
    gestures = make_input()
    with gestures.holding("z"):
        import time
        time.sleep(0.06)
    assert kinds(posted)[0] == win32con.WM_KEYDOWN and kinds(posted)[-1] == win32con.WM_KEYUP
    assert kinds(posted).count(win32con.WM_KEYDOWN) >= 3  # « encore enfoncée », comme le clavier


def test_wheel_uses_screen_coordinates(posted):
    make_input().wheel((300, 450), 2)
    assert kinds(posted) == [win32con.WM_MOUSEWHEEL] * 2
    _, _, wparam, lparam = posted[0]
    assert wparam >> 16 == (-120 & 0xFFFF) and lparam == pack(400, 500)


def test_observation_mode_sends_nothing(posted):
    gestures = make_input(enabled=False)
    gestures.click((500, 300))
    gestures.press("escape")
    assert posted == []


def test_negative_screen_coordinates_packed():
    # Écran de jeu à gauche du principal (08/10 : fenêtre en (-1920, 1097)).
    assert pack(-1920, 1097) == (1097 << 16) | (-1920 & 0xFFFF)


# Le reste du bot


def test_game_in_background_captures_the_window_and_never_parks(monkeypatch):
    frame = load_frame("combat_mon_tour")
    monkeypatch.setattr("bot.capture.printwindow.print_window", lambda hwnd: frame.image)
    window = GameWindow(GAME, "Gela-[A13] - Dofus Retro v1.49.6")
    game = Game(window, Control(), FakeRecorder(), QUICK, background=True)
    assert isinstance(game.input, BackgroundInput)
    looked = game.look()  # combat : au premier plan, la souris sur la carte serait écartée
    assert looked.image is frame.image and game.input.cursor() is None


def test_combat_in_background_uses_the_numpad_for_the_second_row_and_skips_hover_checks():
    class BackgroundGame:
        background = True

    ai = CombatAI(BackgroundGame(), CombatSettings())
    assert ai._slot_key(3) == "3" and ai._slot_key(9) == "num2"  # 2e rangée : Ctrl+2 -> « 2 » du pavé numérique
    assert ai._forbidden_target((500, 300)) is False and ai._hovered is None
    ai._numpad_misses = 2  # sans effet en jeu : plus de touche (les attaques de la 2e rangée sont écartées)
    assert ai._slot_key(9) == ""


def test_numpad_checked_once_on_a_second_row_attack():
    # 11/10 : Expiation et Punitive (2e rangée de Gela) servent rarement ; leur touche est essayée au premier tour.
    from test_brain import FakeGame, presses

    expiation = CombatSpell(9, "fleche_d_expiation", "Flèche d'Expiation", 4, (8, 10), damage=38)
    turn, shown = load_frame("combat_mon_tour"), load_frame("combat_portee_sort")

    def ai_with(*frames):
        game = FakeGame(*frames)
        game.background = True
        ai = CombatAI(game, CombatSettings())
        ai._spells = [expiation]
        ai._use.new_turn(6)
        return ai, game

    ai, game = ai_with(shown, turn)  # la portée juste après la touche, puis plus rien après Échap
    ai._check_numpad(turn)
    assert presses(game) == ["num2", ai.settings.cancel_key] and ai._numpad_misses == 0 and ai.spells == [expiation]
    ai._check_numpad(turn)  # une fois par session
    assert presses(game) == ["num2", ai.settings.cancel_key]
    ai._numpad_missed(expiation)  # pas sélectionné en combat (fin du combat...) : pas la faute du pavé, vérifié
    assert ai._numpad_misses == 0
    ai, game = ai_with(turn)  # pas de portée : la touche ne marche pas (deux tours de suite), sort écarté
    ai._check_numpad(turn)
    assert ai._numpad_misses == 1 and expiation in ai._spells and ai._slot_key(9) == "num2"  # au tour suivant
    ai._check_numpad(turn)
    assert ai._numpad_misses == 2 and expiation not in ai._spells and ai._slot_key(9) == ""
    ai._check_numpad(turn)
    assert presses(game) == ["num2", "num2"]  # plus d'essai


def test_escape_that_opens_the_main_menu_is_followed_by_a_second_escape(monkeypatch):
    # 11/10 : Échap sans rien à fermer ouvre le Menu principal ; refermé aussitôt, sans clic.
    from conftest import FIXTURES
    from bot.perception.frame import Frame
    import cv2

    window = GameWindow(GAME, "Gela-[A13] - Dofus Retro v1.49.6")
    game = Game(window, Control(), FakeRecorder(), QUICK, background=True)
    image = np.zeros((1009, 1920, 3), np.uint8)
    image[180:620, 980:1520] = cv2.imread(str(FIXTURES / "menu_principal.png"))
    frames = [Frame.of(image)]
    pressed = []
    monkeypatch.setattr(game.input, "press", lambda key: pressed.append(key))
    monkeypatch.setattr(game, "look", lambda park=None: frames.pop(0) if frames else load_frame("hors_combat"))
    monkeypatch.setattr(game, "sleep", lambda seconds: None)
    game.press("escape")
    assert pressed == ["escape", "escape"]
    pressed.clear()
    game.press("escape")  # rien d'ouvert ensuite : un seul Échap
    assert pressed == ["escape"]


def test_background_option_and_stop_control():
    from bot.__main__ import in_background, stop_control

    args = argparse.Namespace(arriere_plan=False)
    assert not in_background(args, {"window": {}}) and in_background(args, {"window": {"arriere_plan": True}})
    assert in_background(argparse.Namespace(arriere_plan=True), {})
    control = stop_control({}, background=True)
    try:
        assert control.corner is False  # la souris dans le coin est celle de l'utilisateur : pas un arrêt
    finally:
        control.close()


def test_team_focus_never_brings_a_window_forward_in_background():
    focus = Focus(Control(), passive=True)
    window = GameWindow(GAME, "Gela-[A13] - Dofus Retro v1.49.6")
    focus.to(window)  # passive : retenue pour le journal, jamais passée devant
    assert focus.current is window


def test_numpad_not_tried_again_when_it_worked_in_a_previous_session():
    # 27/10 : sort sélectionné puis Échap à chaque session, pris pour un repérage par l'utilisateur.
    from test_brain import FakeGame, presses

    class Book:
        remembered = False

        def numpad_known(self):
            return True

    expiation = CombatSpell(9, "fleche_d_expiation", "Flèche d'Expiation", 4, (8, 10), damage=38)
    turn = load_frame("combat_mon_tour")
    game = FakeGame(turn)
    game.background = True
    ai = CombatAI(game, CombatSettings(), Book())
    ai._spells = [expiation]
    ai._use.new_turn(6)
    ai._check_numpad(turn)
    assert presses(game) == [] and ai._numpad_checked and not ai._numpad_ok  # une touche morte reste vue en combat
