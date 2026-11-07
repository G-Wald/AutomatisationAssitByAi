"""Capture d'une fenêtre telle qu'elle se dessine, même recouverte par d'autres (PrintWindow), pour le mode arrière-plan.

Tests du 08/10 (tools/arriere_plan*.py) : la fenêtre du jeu (Electron) continue de se dessiner sous une autre fenêtre,
y compris une autre fenêtre du jeu, et PrintWindow donne la même image que l'écran ; 18 ms par capture (médiane de
736). Une fenêtre réduite dans la barre des tâches, elle, ne se dessine plus : à ne jamais faire en arrière-plan.
"""

import ctypes
import time

import numpy as np
import win32gui
import win32ui

from bot.capture.window import GameWindow, Rect

PW_RENDERFULLCONTENT = 2  # contenu rendu par le GPU compris (Chromium, DirectX)
CAPTURE_TRIES = 3  # captures essayées avant de déclarer la fenêtre illisible
RETRY_PAUSE = 0.1  # s entre deux essais


def print_window(hwnd: int) -> np.ndarray | None:
    """Zone de jeu (BGR) de la fenêtre ; None si la capture échoue (fenêtre réduite, fermée...)."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    # 07/11 : « CreateCompatibleDC failed » deux fois en fin de combat (8 fenêtres capturées en même temps), remonté
    # comme erreur du combat, et le DC de la fenêtre jamais rendu (l'échec arrivait avant le try). Tout ce qui a été
    # créé est rendu, et l'échec est un échec de capture (None), réessayé par WindowCapture.
    window_dc = win32gui.GetWindowDC(hwnd)
    source = memory = bitmap = None
    try:
        source = win32ui.CreateDCFromHandle(window_dc)
        memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source, width, height)
        memory.SelectObject(bitmap)
        if not ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), PW_RENDERFULLCONTENT):
            return None
        image = np.frombuffer(bitmap.GetBitmapBits(True), np.uint8).reshape(height, width, 4)[:, :, :3].copy()
    except win32ui.error:
        return None
    finally:
        _release(hwnd, window_dc, source, memory, bitmap)
    cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
    _, _, cw, ch = win32gui.GetClientRect(hwnd)
    client = image[cy - top : cy - top + ch, cx - left : cx - left + cw]
    return np.ascontiguousarray(client) if client.size else None


def _release(hwnd: int, window_dc, source, memory, bitmap) -> None:
    """Rend les objets GDI d'une capture, même à moitié créée ; un objet pas encore créé (None) est sauté."""
    for close in (lambda: win32gui.DeleteObject(bitmap.GetHandle()) if bitmap is not None else None,
                  lambda: memory.DeleteDC() if memory is not None else None,
                  lambda: source.DeleteDC() if source is not None else None,
                  lambda: win32gui.ReleaseDC(hwnd, window_dc)):
        try:
            close()
        except (win32ui.error, win32gui.error):  # bitmap pas encore allouée : rien à rendre
            pass


class WindowCapture:
    """Même usage que ScreenCapture (grab), mais la fenêtre du jeu est lue directement : peu importe ce qui la
    recouvre. Un échec (fenêtre réduite) est réessayé une fois, puis signalé."""

    def __init__(self, window: GameWindow) -> None:
        self.window = window

    def grab(self, rect: Rect | None = None) -> np.ndarray:
        for attempt in range(CAPTURE_TRIES):
            if attempt:
                time.sleep(RETRY_PAUSE)  # ressources GDI prises par les autres captures du moment
            image = print_window(self.window.hwnd)
            if image is not None:
                return image
        raise RuntimeError(f"Impossible de capturer « {self.window.title} » (fenêtre réduite ? remets-la à l'écran, "
                           "sans la réduire)")
