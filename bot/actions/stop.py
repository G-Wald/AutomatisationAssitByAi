"""Arrêt d'urgence et pause.

La touche d'arrêt est écoutée globalement (même quand le jeu a le focus). Toutes les attentes du bot
passent par `StopControl.sleep`, qui vérifie l'arrêt toutes les 50 ms : le bot s'arrête presque aussitôt.
Filet de sécurité supplémentaire : amener la souris dans le coin haut-gauche de l'écran principal.
"""

import logging
import threading
import time

import win32api
from pynput import keyboard

log = logging.getLogger(__name__)


class BotStopped(Exception):
    """Arrêt demandé : touche d'arrêt ou souris dans le coin de l'écran."""


def parse_key(name: str) -> keyboard.Key | keyboard.KeyCode:
    name = name.strip().lower()
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    try:
        return keyboard.Key[name]
    except KeyError:
        raise ValueError(f"Touche inconnue : {name!r} (exemples : f12, pause, x)") from None


def _same_key(pressed, expected) -> bool:
    if isinstance(expected, keyboard.KeyCode):
        return isinstance(pressed, keyboard.KeyCode) and (pressed.char or "").lower() == expected.char
    return pressed == expected


class StopControl:
    def __init__(self, stop_key: str, pause_key: str = "", corner: bool = True) -> None:
        self.corner = corner  # arrêt par la souris dans le coin haut-gauche (pas en arrière-plan : souris de l'utilisateur)
        self.stop_key_name = stop_key
        self.pause_key_name = pause_key
        self._stop_key = parse_key(stop_key)
        self._pause_key = parse_key(pause_key) if pause_key else None
        self._stopped = threading.Event()
        self._paused = threading.Event()
        self.reason = ""
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.daemon = True

    def start(self) -> "StopControl":
        self._listener.start()
        return self

    def close(self) -> None:
        self._listener.stop()

    def _on_press(self, key) -> None:
        if _same_key(key, self._stop_key):
            self.stop(f"touche {self.stop_key_name.upper()}")
        elif self._pause_key is not None and _same_key(key, self._pause_key):
            if self._paused.is_set():
                self._paused.clear()
                log.info("Reprise")
            else:
                self._paused.set()
                log.info("Pause (%s pour reprendre)", self.pause_key_name.upper())

    def stop(self, reason: str) -> None:
        if not self._stopped.is_set():
            self.reason = reason
            self._stopped.set()
            log.info("Arrêt demandé : %s", reason)

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def check(self) -> None:
        """Lève BotStopped si l'arrêt est demandé ; bloque tant que le bot est en pause."""
        while True:
            if self.corner and win32api.GetCursorPos() == (0, 0):
                self.stop("souris dans le coin haut-gauche de l'écran")
            if self._stopped.is_set():
                raise BotStopped(self.reason)
            if not self._paused.is_set():
                return
            time.sleep(0.05)

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            self.check()
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))
