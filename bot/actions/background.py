"""Souris et clavier en arrière-plan : gestes envoyés à la fenêtre du jeu par messages Windows, sans toucher à la vraie
souris ni au clavier, sans passer la fenêtre au premier plan. L'utilisateur se sert de son PC pendant ce temps.

Même usage que GameInput (actions/input.py), d'après les tests du 08/10 (tools/arriere_plan*.py) :
- les messages vont à la fenêtre interne qui reçoit les entrées du jeu (Chrome_RenderWidgetHostHWND du client
  Electron), en coordonnées de cette fenêtre ;
- un clic part d'un bloc : « souris ici », bouton enfoncé, bouton relâché, déposés à la suite. Envoyé lentement, il
  rate : en traitant « souris ici », le jeu demande à Windows d'être prévenu quand la souris s'en va, et Windows
  répond aussitôt « partie » (la vraie souris est ailleurs), avant le bouton ;
- jamais deux « souris ici » au même pixel de suite : le jeu ignore le second, et ne voit plus la souris revenir
  (5 ratés sur 5 ; 32 clics réussis sur 32 en changeant de pixel) ;
- pas de survol : `move` ne fait que noter où est la souris « virtuelle » (un message de survol ne montrerait rien, et
  rendrait le clic suivant au même endroit inutile) ;
- touches : enfoncée puis relâchée (100 % : Échap, Z tenu montre les groupes). Pas de combinaison (Ctrl+3) : l'état
  de Ctrl est celui du vrai clavier, qu'un message ne change pas ; ces cases de sorts se cliquent ;
- jamais de mise en pause d'un fil du jeu (essayé le 08/10 : les clics ne passaient plus jusqu'au redémarrage du jeu).
Le rythme reste humain : temps de trajet de la souris et réaction avant chaque geste, comme au premier plan.
"""

import logging
import math
import random
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import win32api
import win32con
import win32gui
import win32process

from bot.actions import keys
from bot.actions.input import NO_SPREAD, Human, Spread, scatter
from bot.actions.stop import StopControl
from bot.capture.window import GameWindow
from bot.perception.layout import Point

log = logging.getLogger(__name__)

INPUT_CLASS = "Chrome_RenderWidgetHostHWND"  # fenêtre interne du client Electron qui reçoit souris et clavier
WHEEL_NOTCH = 120
KEY_REPEAT = 0.1  # touche tenue : « encore enfoncée » toutes les 0,1 s, comme la répétition du clavier
BUTTONS = {"left": (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON),
           "right": (win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON)}


def pack(x: int, y: int) -> int:
    """Coordonnées dans un lParam (négatives comprises : écran à gauche du principal)."""
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


class BackgroundInput:
    def __init__(self, window: GameWindow, control: StopControl, human: Human = Human(), enabled: bool = True) -> None:
        self.window = window
        self.control = control
        self.human = human
        self.enabled = enabled  # False : mode observation, aucune entrée n'est envoyée
        self._target = 0
        self._cursor: Point | None = None  # souris « virtuelle » : là où le dernier geste a été envoyé
        self._last_move: Point | None = None  # pixel du dernier « souris ici » envoyé

    # --- Fenêtre cible ---

    def target(self) -> int:
        """Fenêtre interne qui reçoit les entrées (retrouvée si le jeu l'a recréée), sinon la fenêtre du jeu."""
        if self._target and win32gui.IsWindow(self._target):
            return self._target
        found: list[int] = []

        def collect(child: int, _) -> None:
            if win32gui.GetClassName(child) == INPUT_CLASS:
                found.append(child)

        win32gui.EnumChildWindows(self.window.hwnd, collect, None)
        self._target = found[0] if found else self.window.hwnd
        return self._target

    def _client(self, point: Point) -> int:
        """Point de la zone de jeu dans les coordonnées de la fenêtre cible."""
        screen = win32gui.ClientToScreen(self.window.hwnd, (round(point[0]), round(point[1])))
        x, y = win32gui.ScreenToClient(self.target(), screen)
        return pack(x, y)

    def _inside(self, point: Point) -> bool:
        _, _, width, height = win32gui.GetClientRect(self.window.hwnd)
        return 0 <= round(point[0]) < width and 0 <= round(point[1]) < height

    def _post(self, message: int, wparam: int, lparam: int) -> None:
        win32api.PostMessage(self.target(), message, wparam, lparam)

    # --- Rythme humain ---

    def _travel(self, end: Point) -> None:
        """Temps qu'aurait mis la main pour amener la souris (pas de souris à bouger : rien n'est envoyé)."""
        start = self._cursor
        length = math.dist(start, end) if start is not None else 400.0
        if length > 3:
            self.control.sleep(random.uniform(*self.human.move_time) * min(1.2, max(0.35, length / 800)))

    def _react(self) -> None:
        pause = random.uniform(*self.human.reaction)
        if random.random() < self.human.hesitation_chance:
            pause += random.uniform(*self.human.hesitation)
        self.control.sleep(pause)

    # --- Même usage que GameInput ---

    def wait_foreground(self) -> None:
        """Rien à attendre : la fenêtre du jeu n'a pas besoin d'être devant."""

    def release_stuck(self) -> list[str]:
        """Jamais : les touches et boutons vraiment enfoncés sont ceux de l'utilisateur, dans son application."""
        return []

    def release_keys(self, names: list[str]) -> None:
        """« Touche relâchée » envoyé pour chacune : si le bot a été arrêté pendant qu'il tenait une touche (E pendant
        la marche, Z pour les groupes), le jeu la croirait encore enfoncée (11/10 : principal bloqué, à vérifier)."""
        if not self.enabled:
            return
        for name in names:
            try:
                self._key_up(self._key(name))
            except ValueError:
                continue

    def cursor(self) -> Point | None:
        return self._cursor

    def move(self, point: tuple[float, float], spread: Spread = NO_SPREAD) -> Point:
        """Note où va la souris virtuelle (pas de message : le survol ne marche pas en arrière-plan)."""
        self.control.check()
        target = scatter(point, spread)
        if self.enabled:
            self._travel(target)
        self._cursor = target
        return target

    def hover(self, point: tuple[float, float], spread: Spread = NO_SPREAD) -> None:
        """« Souris ici » envoyé au jeu, sans clic : pour que le jeu cesse de croire la souris sur un combattant (11/10 :
        après un sort, sa fiche restait affichée à la place de la barre de sorts, et le sort suivant ne partait pas)."""
        if not self.enabled or not self._inside(point):
            return
        target = self.move(point, spread)
        if target == self._last_move:
            target = (target[0] + random.choice((-1, 1)), target[1])
            self._cursor = target
        self._post(win32con.WM_MOUSEMOVE, 0, self._client(target))
        self._last_move = target

    def click(self, point: tuple[float, float] | None = None, spread: Spread = NO_SPREAD, button: str = "left") -> bool:
        """Clic sur `point` (à `spread` près), ou là où est la souris virtuelle si `point` vaut None. False s'il est
        annulé (point hors de la fenêtre du jeu)."""
        if point is not None and not self._inside(point):
            log.warning("Clic en (%d, %d) annulé : hors de la fenêtre du jeu", round(point[0]), round(point[1]))
            return False
        target = self.move(point, spread) if point is not None else self._cursor
        if target is None:
            log.warning("Clic annulé : aucun point visé (souris virtuelle pas encore placée)")
            return False
        if not self.enabled:
            return True
        self._react()
        if target == self._last_move:  # jamais deux « souris ici » au même pixel : le jeu ignorerait le second
            target = (target[0] + random.choice((-1, 1)), target[1])
            self._cursor = target
        down, up, flag = BUTTONS[button]
        where = self._client(target)
        log.debug("Clic %s en %s (arrière-plan)", button, target)
        # D'un bloc, sans pause : le « souris partie » de Windows ne doit pas se glisser entre ces messages.
        self._post(win32con.WM_MOUSEMOVE, 0, where)
        self._post(down, flag, where)
        self._post(up, 0, where)
        self._last_move = target
        return True

    def wheel(self, point: tuple[float, float], notches: int, spread: Spread = NO_SPREAD) -> bool:
        """Molette sur `point` : `notches` crans vers le bas (négatif : vers le haut). Coordonnées d'écran, comme le
        veut ce message. Pas encore essayé en arrière-plan : la liste des sorts a la flèche du bas en secours."""
        if not self._inside(point):
            log.warning("Molette en (%d, %d) annulée : hors de la fenêtre du jeu", round(point[0]), round(point[1]))
            return False
        target = self.move(point, spread)
        if not self.enabled:
            return True
        self._react()
        screen = win32gui.ClientToScreen(self.window.hwnd, target)
        delta = -WHEEL_NOTCH if notches > 0 else WHEEL_NOTCH
        log.debug("Molette : %d cran(s) en %s (arrière-plan)", notches, target)
        for _ in range(abs(notches)):
            self._post(win32con.WM_MOUSEWHEEL, (delta & 0xFFFF) << 16, pack(*screen))
            time.sleep(random.uniform(0.03, 0.08))
        return True

    def _key(self, name: str) -> keys.Key:
        thread, _ = win32process.GetWindowThreadProcessId(self.window.hwnd)
        pressed = keys.resolve(name, win32api.GetKeyboardLayout(thread))
        if len(pressed) > 1:
            raise ValueError(f"Combinaison {name!r} impossible en arrière-plan (l'état de Ctrl/Maj est celui du vrai "
                             "clavier) : cliquer plutôt")
        return pressed[0]

    def _key_down(self, key: keys.Key, repeat: bool = False) -> None:
        self._post(win32con.WM_KEYDOWN, key.vk, 1 | (key.scan << 16) | ((1 << 30) if repeat else 0))

    def _key_up(self, key: keys.Key) -> None:
        self._post(win32con.WM_KEYUP, key.vk, 1 | (key.scan << 16) | (1 << 30) | (1 << 31))

    def type_text(self, text: str) -> bool:
        """Texte tapé dans le champ qui a le focus du jeu (zone de saisie du chat cliquée avant) : un message
        « caractère » par lettre (16/10 : « /p %pos% » passe ainsi en arrière-plan)."""
        if not self.enabled:
            return False
        self._react()
        for char in text:
            self.control.check()
            self._post(win32con.WM_CHAR, ord(char), 1)
            time.sleep(random.uniform(0.03, 0.08))
        return True

    def press(self, name: str) -> None:
        with self.holding(name):
            time.sleep(random.uniform(*self.human.hold))

    @contextmanager
    def holding(self, name: str) -> Iterator[None]:
        """Touche enfoncée pendant le bloc `with`, « encore enfoncée » répété comme le fait le clavier."""
        self.control.check()
        if not self.enabled:
            yield
            return
        key = self._key(name)
        self._react()
        log.debug("Touche %s (arrière-plan)", name)
        self._key_down(key)
        stop = threading.Event()

        def repeat() -> None:
            while not stop.wait(KEY_REPEAT):
                self._key_down(key, repeat=True)

        repeater = threading.Thread(target=repeat, daemon=True)
        repeater.start()
        try:
            yield
        finally:
            stop.set()
            repeater.join()
            self._key_up(key)
