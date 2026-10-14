"""Ankama Launcher : le lancer s'il est fermé, lancer tous les comptes disponibles, attendre les NOUVELLES fenêtres du
jeu (celles déjà ouvertes avant ne comptent pas : 11/10, une fenêtre déjà ouverte devant le launcher ne doit pas
empêcher d'en ouvrir d'autres).

Lancer les comptes (l'utilisateur, 11/10) : sur la page de Dofus Retro, Ctrl+8 lance tous les comptes disponibles
(pas déjà lancés), 8 au plus. Chaque fenêtre ouverte est ensuite connectée avec un des personnages voulus, ou
refermée (connection/manager.py). Pas de mot de passe : les comptes sont enregistrés dans le launcher.

Le launcher est toujours manipulé au premier plan (connection/manager.py, make_launcher) : en arrière-plan (capture
de la fenêtre recouverte, clics par messages), il s'était figé jusqu'à ce que l'utilisateur redimensionne sa fenêtre.
Ctrl ne passe pas par message (l'état de Ctrl est celui du vrai clavier) : Ctrl+8 est tapé au clavier, launcher devant.
La souris ne sert que si la page de Dofus Retro n'est pas affichée (clic sur son icône).
"""

import ctypes
import logging
import os
import subprocess
import time

import win32con
import win32gui

from bot.actions.stop import StopControl
from bot.brain.game import Game
from bot.capture.window import GameWindow, list_windows
from bot.connection.clients import client_windows
from bot.perception.launcher import find_play, find_retro_icon

log = logging.getLogger(__name__)

LAUNCHER_TITLE = "Ankama Launcher"
LAUNCHER_WAIT = 30.0  # fenêtre du launcher apparue après son lancement
PAGE_WAIT = 20.0  # page de Dofus Retro prête (bouton « JOUER »), launcher qui démarre ou se met à jour
LAUNCH_ALL = "ctrl+8"  # tous les comptes disponibles, 8 au plus
AFTER_LAUNCH = 2.5  # Ctrl+8 : capture du launcher si rien ne s'est ouvert après ce délai
SETTLE = 6.0  # une fenêtre du jeu est apparue : on attend encore les suivantes (les comptes s'ouvrent un à un)


def launcher_window() -> GameWindow | None:
    return next((w for w in list_windows() if w.title == LAUNCHER_TITLE), None)


def start_launcher(path: str) -> bool:
    """Lance l'Ankama Launcher s'il n'a pas de fenêtre ; False si l'exécutable est introuvable."""
    if launcher_window() is not None:
        return True
    if not os.path.isfile(path):
        log.error("Ankama Launcher introuvable : %s (réglage connexion.launcher)", path)
        return False
    log.info("Je lance l'Ankama Launcher")
    subprocess.Popen([path], close_fds=True)  # déjà lancé (icône près de l'horloge) : sa fenêtre réapparaît
    return True


def wait_launcher(control: StopControl, timeout: float = LAUNCHER_WAIT) -> GameWindow | None:
    """Fenêtre du launcher, remise à l'écran si elle est réduite."""
    end = time.monotonic() + timeout
    while (window := launcher_window()) is None:
        if time.monotonic() >= end:
            return None
        control.sleep(0.5)
    if win32gui.IsIconic(window.hwnd):
        win32gui.ShowWindow(window.hwnd, win32con.SW_RESTORE)  # réduite, elle ne se dessine plus
        control.sleep(1.0)
    return window


def window_scale(window: GameWindow) -> float:
    """Mise à l'échelle de Windows pour cette fenêtre (1,0 à 100 %, 1,75 à 175 %)."""
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(window.hwnd)
    except (AttributeError, OSError, ctypes.ArgumentError):
        dpi = 0
    return dpi / 96 if dpi else 1.0


def new_windows(known: set[int]) -> list[GameWindow]:
    """Fenêtres du jeu ouvertes depuis que `known` (leurs identifiants) a été relevé."""
    return [w for w in client_windows() if w.hwnd not in known]


def launch_all(game: Game, control: StopControl, known: set[int] = frozenset()) -> bool:
    """Page de Dofus Retro du launcher (icône à gauche s'il faut), puis Ctrl+8. False sans la page (capture).
    `known` : fenêtres du jeu déjà ouvertes avant."""
    scale = window_scale(game.window)
    end = time.monotonic() + PAGE_WAIT
    icon_clicked = False
    while find_play((frame := game.look(park=False)).image, scale) is None:
        icon = None if icon_clicked else find_retro_icon(frame.image, scale)
        if icon is not None:
            log.info("Launcher : page de Dofus Retro")
            game.click(icon, (6 * scale, 6 * scale))
            icon_clicked = True
        elif time.monotonic() >= end:
            path = game.recorder.save(frame.image, "launcher_sans_jouer", lossless=True)
            log.warning("Launcher : page de Dofus Retro introuvable (capture : %s)", path)
            return False
        control.sleep(1.0)
    log.info("Launcher : Ctrl+8, tous les comptes disponibles")
    game.press(LAUNCH_ALL)
    control.sleep(AFTER_LAUNCH)
    if not new_windows(known):
        path = game.recorder.save(game.look(park=False).image, "launcher_apres_ctrl_8", lossless=True)
        log.info("Launcher après Ctrl+8 (capture : %s) : rien ne s'est encore ouvert", path)
    return True


def wait_for_clients(control: StopControl, timeout: float, ask: bool = True, known: set[int] = frozenset()
                     ) -> list[GameWindow]:
    """Attend qu'au moins une nouvelle fenêtre du jeu s'ouvre (pas dans `known`), puis les suivantes, tant qu'il en
    apparaît ; renvoie les nouvelles. `ask` : te demander de lancer les comptes toi-même (le bot n'a pas pu)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        windows = new_windows(known)
        if windows:
            count = len(windows)
            control.sleep(SETTLE)
            while len(windows := new_windows(known)) > count:
                count = len(windows)
                control.sleep(SETTLE)
            return windows
        if ask:
            ask = False
            log.info("Lance toi-même les comptes dans l'Ankama Launcher : je prends la suite dès que le jeu s'ouvre")
        control.sleep(1.0)
    return []


def launch_clients(path: str, timeout: float, make_game, control: StopControl) -> list[GameWindow]:
    """Launcher lancé (s'il ne l'est pas), Ctrl+8, puis les fenêtres du jeu qui s'ouvrent (les nouvelles)."""
    known = {w.hwnd for w in client_windows()}
    if not start_launcher(path):
        return []
    launcher = wait_launcher(control)
    launched = False
    if launcher is None:
        log.warning("Fenêtre de l'Ankama Launcher introuvable au bout de %.0f s", LAUNCHER_WAIT)
    else:
        launched = launch_all(make_game(launcher), control, known)
    return wait_for_clients(control, timeout, ask=not launched, known=known)
