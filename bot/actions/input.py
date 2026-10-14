"""Souris et clavier, en coordonnées de la zone de jeu (les pixels des captures), avec un rythme humain.

- La souris glisse le long d'une courbe, accélère puis ralentit, au lieu de se téléporter.
- Chaque clic tombe sur un point tiré au hasard dans la cible (`spread`), jamais sur le même pixel,
  en restant assez près du centre pour ne pas manquer la cible.
- Temps de réaction, durée d'appui et hésitations occasionnelles sont aléatoires.

Rien n'est envoyé tant que la fenêtre du jeu n'est pas au premier plan : si tu passes sur une autre
fenêtre, le bot attend au lieu de cliquer dedans.
"""

import logging
import math
import random
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import win32api
import win32con
import win32gui
import win32process

from bot.actions import keys
from bot.actions.stop import StopControl
from bot.capture.window import GameWindow
from bot.perception.layout import Point

log = logging.getLogger(__name__)

Spread = tuple[float, float]  # demi-étendue (x, y) du tirage aléatoire autour de la cible
NO_SPREAD: Spread = (0.0, 0.0)

MOUSE_BUTTONS = {
    "left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
    "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
}
UNDER_CURSOR_WAIT = 0.5  # fenêtre du jeu pas encore sous la souris (autre fenêtre devant) : attente avant d'annuler
WHEEL_NOTCH = 120  # un cran de molette (WHEEL_DELTA de Windows)
WHEEL_GAP = (0.03, 0.08)  # pause entre deux crans
# Ce qui changerait le sens d'un clic s'il restait enfoncé : vérifié (et relâché) avant chaque clic ou touche.
WATCHED = {win32con.VK_LBUTTON: ("bouton gauche de la souris", win32con.MOUSEEVENTF_LEFTUP),
           win32con.VK_RBUTTON: ("bouton droit de la souris", win32con.MOUSEEVENTF_RIGHTUP),
           win32con.VK_SHIFT: ("Maj", None), win32con.VK_CONTROL: ("Ctrl", None), win32con.VK_MENU: ("Alt", None)}


@dataclass(frozen=True)
class Human:
    """Rythme des gestes (secondes). Réglages : section `human` de config/settings.yaml."""

    move_time: tuple[float, float] = (0.08, 0.22)  # trajet de souris sur ~800 px
    reaction: tuple[float, float] = (0.07, 0.22)  # pause avant un clic ou une touche
    hold: tuple[float, float] = (0.04, 0.09)  # durée d'appui
    hesitation_chance: float = 0.06  # probabilité d'une pause plus longue avant une action
    hesitation: tuple[float, float] = (0.3, 0.8)
    slowdown: tuple[float, float] = (0.85, 1.15)  # facteur appliqué aux attentes du bot

    @classmethod
    def from_settings(cls, settings: dict) -> "Human":
        human = settings.get("human") or {}
        fields = {name: getattr(cls, name) for name in cls.__dataclass_fields__}
        values = {}
        for name, default in fields.items():
            value = human.get(name, default)
            values[name] = tuple(float(v) for v in value) if isinstance(default, tuple) else float(value)
        return cls(**values)


def scatter(point: tuple[float, float], spread: Spread) -> Point:
    """Point aléatoire dans le losange de demi-diagonales `spread`, plus souvent près du centre."""
    x, y = point
    rx, ry = spread
    while True:
        u = random.random() - random.random()
        v = random.random() - random.random()
        if abs(u) + abs(v) <= 1:
            return round(x + u * rx), round(y + v * ry)


class GameInput:
    def __init__(self, window: GameWindow, control: StopControl, human: Human = Human(), enabled: bool = True) -> None:
        self.window = window
        self.control = control
        self.human = human
        self.enabled = enabled  # False : mode observation, aucune entrée n'est envoyée

    def wait_foreground(self) -> None:
        if self.window.is_foreground():
            return
        log.info("Le jeu n'est plus au premier plan : j'attends qu'il revienne devant.")
        while not self.window.is_foreground():
            self.control.sleep(0.3)
        log.info("Jeu au premier plan : je reprends.")
        self.control.sleep(0.3)  # laisse le jeu se redessiner

    def _to_screen(self, point: Point) -> tuple[int, int]:
        rect = self.window.client_rect()
        x, y = round(point[0]), round(point[1])
        if not (0 <= x < rect.width and 0 <= y < rect.height):
            raise ValueError(f"Point {point} hors de la zone de jeu ({rect.width}x{rect.height})")
        return rect.left + x, rect.top + y

    def _inside(self, point: Point) -> bool:
        rect = self.window.client_rect()
        return 0 <= round(point[0]) < rect.width and 0 <= round(point[1]) < rect.height

    def _within(self, point: Point) -> Point:
        """Ramené dans la zone de jeu : la dispersion d'un point visé près du bord peut l'en faire sortir de quelques
        pixels (03/10 : épée tout en haut de la carte, clic en y = -4, le bot s'était arrêté)."""
        rect = self.window.client_rect()
        return min(max(point[0], 0), rect.width - 1), min(max(point[1], 0), rect.height - 1)

    def cursor(self) -> Point | None:
        """Position de la souris dans la zone de jeu, None si elle est ailleurs."""
        rect = self.window.client_rect()
        x, y = win32api.GetCursorPos()
        x, y = x - rect.left, y - rect.top
        return (x, y) if 0 <= x < rect.width and 0 <= y < rect.height else None

    def move(self, point: tuple[float, float], spread: Spread = NO_SPREAD) -> Point:
        """Amène la souris près de `point` ; renvoie le point visé (pour cliquer ensuite sur place)."""
        self.control.check()
        target = scatter(point, spread)
        if self.enabled:
            self.wait_foreground()
            target = self._within(target)
            self._glide(self._to_screen(target))
        return target

    def _glide(self, end: tuple[int, int]) -> None:
        x0, y0 = win32api.GetCursorPos()
        x1, y1 = end
        length = math.hypot(x1 - x0, y1 - y0)
        if length > 3:
            duration = random.uniform(*self.human.move_time) * min(1.2, max(0.35, length / 800))
            steps = max(6, round(duration / 0.012))
            # Courbe de Bézier : point de contrôle écarté de la ligne droite, d'un côté ou de l'autre.
            bend = random.uniform(-0.15, 0.15) * length
            cx = (x0 + x1) / 2 - (y1 - y0) / length * bend
            cy = (y0 + y1) / 2 + (x1 - x0) / length * bend
            for step in range(1, steps):
                t = step / steps
                t = t * t * (3 - 2 * t)  # accélère puis ralentit
                x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t**2 * x1
                y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t**2 * y1
                win32api.SetCursorPos((round(x), round(y)))
                time.sleep(duration / steps)
                self.control.check()
        win32api.SetCursorPos(end)

    def _react(self) -> None:
        pause = random.uniform(*self.human.reaction)
        if random.random() < self.human.hesitation_chance:
            pause += random.uniform(*self.human.hesitation)
        self.control.sleep(pause)

    def release_keys(self, names: list[str]) -> None:
        """Premier plan : les touches sont celles du vrai clavier (relâchées par release_stuck s'il le faut)."""

    def release_stuck(self) -> list[str]:
        """Bouton de souris ou touche Maj / Ctrl / Alt enfoncé alors que le bot n'appuie sur rien : relâché et signalé
        (un bouton resté enfoncé ferait ignorer les clics suivants)."""
        stuck = []
        for vk, (name, mouse_up) in WATCHED.items():
            if not win32api.GetAsyncKeyState(vk) & 0x8000:
                continue
            stuck.append(name)
            if mouse_up is not None:
                win32api.mouse_event(mouse_up, 0, 0)
            else:
                keys.key_up(keys.Key(vk, win32api.MapVirtualKey(vk, 0)))
        if stuck:
            log.warning("%s encore enfoncé(s) avant mon action : je relâche", ", ".join(stuck))
        return stuck

    def _game_under(self, screen: tuple[int, int]) -> bool:
        """La fenêtre du jeu est-elle celle qui est sous ce point de l'écran ? Un clic va à la fenêtre sous la souris,
        pas à celle qui a le focus : juste après un changement de fenêtre (mode équipe), l'autre fenêtre du jeu, au
        même endroit, peut encore être devant (01/10 : « Fermer » cliqué deux fois sans effet chez un suiveur)."""
        hwnd = win32gui.WindowFromPoint(screen)
        return bool(hwnd) and win32gui.GetAncestor(hwnd, win32con.GA_ROOT) == self.window.hwnd

    def click(self, point: tuple[float, float] | None = None, spread: Spread = NO_SPREAD, button: str = "left") -> bool:
        """Clic sur `point` (à `spread` près), ou là où est la souris si `point` vaut None. False s'il est annulé :
        une autre fenêtre est restée sous la souris, ou `point` est hors de la fenêtre du jeu."""
        if point is not None and self.enabled and not self._inside(point):
            log.warning("Clic en (%d, %d) annulé : hors de la fenêtre du jeu", round(point[0]), round(point[1]))
            return False
        target = self.move(point, spread) if point is not None else None
        if not self.enabled:
            return True
        if not self._ready(target, "Clic"):
            return False
        log.debug("Clic %s en %s", button, target or self.cursor())
        down, up = MOUSE_BUTTONS[button]
        win32api.mouse_event(down, 0, 0)
        try:
            time.sleep(random.uniform(*self.human.hold))  # pas control.sleep : un arrêt ici laisserait le bouton enfoncé
        finally:
            win32api.mouse_event(up, 0, 0)
        return True

    def _ready(self, target: Point | None, what: str) -> bool:
        """Juste avant un geste de souris en `target` (None : là où elle est) : pause humaine, fenêtre au premier plan,
        rien de resté enfoncé, souris sur la cible et la fenêtre du jeu sous elle. False (avec un avertissement) si
        une autre fenêtre reste sous la souris."""
        self._react()
        self.wait_foreground()
        self.release_stuck()
        if target is not None:
            screen = self._to_screen(target)
            if win32api.GetCursorPos() != screen:  # la souris a bougé pendant la pause
                win32api.SetCursorPos(screen)
        else:
            screen = win32api.GetCursorPos()
        deadline = time.monotonic() + UNDER_CURSOR_WAIT
        while not self._game_under(screen):
            if time.monotonic() >= deadline:
                log.warning("%s annulé en %s : une autre fenêtre que « %s » est sous la souris", what, target or screen,
                            self.window.title)
                return False
            time.sleep(0.02)
        return True

    def wheel(self, point: tuple[float, float], notches: int, spread: Spread = NO_SPREAD) -> bool:
        """Molette, souris sur `point` : `notches` crans vers le bas (négatif : vers le haut), un par un. False si
        c'est annulé (point hors de la fenêtre du jeu, autre fenêtre sous la souris)."""
        if self.enabled and not self._inside(point):
            log.warning("Molette en (%d, %d) annulée : hors de la fenêtre du jeu", round(point[0]), round(point[1]))
            return False
        target = self.move(point, spread)
        if not self.enabled:
            return True
        if not self._ready(target, "Molette"):
            return False
        log.debug("Molette : %d cran(s) en %s", notches, target)
        for _ in range(abs(notches)):
            win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, -WHEEL_NOTCH if notches > 0 else WHEEL_NOTCH)
            time.sleep(random.uniform(*WHEEL_GAP))
        return True

    def _keyboard_layout(self) -> int:
        thread, _ = win32process.GetWindowThreadProcessId(self.window.hwnd)
        return win32api.GetKeyboardLayout(thread)

    def press(self, name: str) -> None:
        with self.holding(name):
            time.sleep(random.uniform(*self.human.hold))

    @contextmanager
    def holding(self, name: str) -> Iterator[None]:
        """Garde la touche (ou la combinaison) enfoncée pendant le bloc `with`."""
        self.control.check()
        if not self.enabled:
            yield
            return
        pressed = keys.resolve(name, self._keyboard_layout())
        self._react()
        self.wait_foreground()
        self.release_stuck()
        log.debug("Touche %s", name)
        down = []
        try:
            for key in pressed:
                keys.key_down(key)
                down.append(key)
                time.sleep(random.uniform(0.02, 0.05))
            yield
        finally:
            for key in reversed(down):
                keys.key_up(key)
