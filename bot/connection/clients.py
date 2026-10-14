"""Fenêtres du client Dofus Retro, connectées ou non, d'après leur titre (lu à chaque fois : il change à la connexion).

Titres (08/10, v1.49.6) : « Gela-[A13] - Dofus Retro v1.49.6 » en jeu ; « Dofus Retro v1.49.6 » avant le choix du
personnage (écrans de connexion) et, sans doute, après une déconnexion. Le réglage window.title (« - Dofus Retro »)
ne trouve que les fenêtres en jeu : ici on cherche le titre du jeu seul.
"""

import re
import time

import win32con
import win32gui

from bot.capture.window import GameWindow, list_windows

# Titre entier d'une fenêtre du client, avec ou sans personnage (pas un onglet de navigateur qui en parlerait).
CLIENT_TITLE = re.compile(r"^(?:(?P<name>.+?) - )?Dofus Retro v[\d.]+$")


def live_title(window: GameWindow) -> str:
    """Titre actuel de la fenêtre ; "" si elle n'existe plus (jeu fermé)."""
    if not win32gui.IsWindow(window.hwnd):
        return ""
    return win32gui.GetWindowText(window.hwnd)


def logged_in(window: GameWindow) -> str:
    """Nom du personnage en jeu dans cette fenêtre ; "" sur un écran de connexion (ou fenêtre fermée)."""
    match = CLIENT_TITLE.match(live_title(window))
    return (match.group("name") or "") if match else ""


def is_open(window: GameWindow) -> bool:
    return bool(CLIENT_TITLE.match(live_title(window)))


def client_windows() -> list[GameWindow]:
    """Fenêtres du client, connectées ou non, dans l'ordre de Windows."""
    return [w for w in list_windows() if CLIENT_TITLE.match(w.title)]


def close_window(window: GameWindow, wait, timeout: float = 10.0) -> bool:
    """Ferme la fenêtre du jeu (comme sa croix) ; True si elle a disparu. `wait(secondes)` : attente (StopControl)."""
    win32gui.PostMessage(window.hwnd, win32con.WM_CLOSE, 0, 0)
    end = time.monotonic() + timeout
    while win32gui.IsWindow(window.hwnd):
        if time.monotonic() >= end:
            return False
        wait(0.3)
    return True
