"""Capture d'une fenêtre telle qu'elle se dessine, même recouverte par d'autres (PrintWindow), pour le mode arrière-plan.

Tests du 08/10 (tools/arriere_plan*.py) : la fenêtre du jeu (Electron) continue de se dessiner sous une autre fenêtre,
y compris une autre fenêtre du jeu, et PrintWindow donne la même image que l'écran ; 18 ms par capture (médiane de
736). Une fenêtre réduite dans la barre des tâches, elle, ne se dessine plus : à ne jamais faire en arrière-plan.
"""

import ctypes

import numpy as np
import win32gui
import win32ui

from bot.capture.window import GameWindow, Rect

PW_RENDERFULLCONTENT = 2  # contenu rendu par le GPU compris (Chromium, DirectX)


def print_window(hwnd: int) -> np.ndarray | None:
    """Zone de jeu (BGR) de la fenêtre ; None si la capture échoue (fenêtre réduite, fermée...)."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    window_dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(window_dc)
    memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, width, height)
    memory.SelectObject(bitmap)
    try:
        if not ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), PW_RENDERFULLCONTENT):
            return None
        image = np.frombuffer(bitmap.GetBitmapBits(True), np.uint8).reshape(height, width, 4)[:, :, :3].copy()
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)
    cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
    _, _, cw, ch = win32gui.GetClientRect(hwnd)
    client = image[cy - top : cy - top + ch, cx - left : cx - left + cw]
    return np.ascontiguousarray(client) if client.size else None


class WindowCapture:
    """Même usage que ScreenCapture (grab), mais la fenêtre du jeu est lue directement : peu importe ce qui la
    recouvre. Un échec (fenêtre réduite) est réessayé une fois, puis signalé."""

    def __init__(self, window: GameWindow) -> None:
        self.window = window

    def grab(self, rect: Rect | None = None) -> np.ndarray:
        for _ in range(2):
            image = print_window(self.window.hwnd)
            if image is not None:
                return image
        raise RuntimeError(f"Impossible de capturer « {self.window.title} » (fenêtre réduite ? remets-la à l'écran, "
                           "sans la réduire)")
