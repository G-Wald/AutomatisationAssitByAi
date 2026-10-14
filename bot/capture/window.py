"""Fenêtres Windows : trouver la fenêtre du jeu, sa zone de jeu, la passer au premier plan."""

import ctypes
from dataclasses import dataclass

import win32api
import win32con
import win32gui
import win32process


def enable_dpi_awareness() -> None:
    """Travaille en pixels physiques (captures et clics alignés malgré la mise à l'échelle Windows)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except OSError:
        ctypes.windll.user32.SetProcessDPIAware()


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    title: str

    def client_rect(self) -> Rect:
        """Zone de jeu (sans bordure ni barre de titre), en coordonnées écran."""
        _, _, width, height = win32gui.GetClientRect(self.hwnd)
        left, top = win32gui.ClientToScreen(self.hwnd, (0, 0))
        return Rect(left, top, width, height)

    def is_foreground(self) -> bool:
        return win32gui.GetForegroundWindow() == self.hwnd

    def bring_to_front(self) -> None:
        if win32gui.IsIconic(self.hwnd):
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        # Windows refuse SetForegroundWindow à un process qui n'a pas le focus :
        # on attache temporairement notre file d'entrée à celle de la fenêtre active.
        current = win32api.GetCurrentThreadId()
        foreground, _ = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())
        attached = foreground not in (0, current)
        if attached:
            win32process.AttachThreadInput(current, foreground, True)
        try:
            win32gui.BringWindowToTop(self.hwnd)
            win32gui.SetForegroundWindow(self.hwnd)
        finally:
            if attached:
                win32process.AttachThreadInput(current, foreground, False)
        if not self.is_foreground():
            raise RuntimeError(f"Impossible de passer « {self.title} » au premier plan")


def list_windows() -> list[GameWindow]:
    """Fenêtres visibles ayant un titre."""
    windows: list[GameWindow] = []

    def collect(hwnd: int, _) -> None:
        if win32gui.IsWindowVisible(hwnd) and (title := win32gui.GetWindowText(hwnd)):
            windows.append(GameWindow(hwnd, title))

    win32gui.EnumWindows(collect, None)
    return windows


def find_windows(title_part: str) -> list[GameWindow]:
    needle = title_part.lower()
    return [w for w in list_windows() if needle in w.title.lower()]


def character_name(title: str, title_part: str) -> str:
    """Nom du personnage d'après le titre de sa fenêtre : ce qui précède le titre du jeu.

    « Jesuisleking - Dofus Retro v1.49.5 » -> « Jesuisleking » (que `title_part` soit « - Dofus Retro » ou
    « Dofus Retro »). "" si le titre commence par celui du jeu (écran de connexion, pas encore de personnage)."""
    at = title.lower().find(title_part.lower())
    return title[:at].strip(" -") if at > 0 else ""
