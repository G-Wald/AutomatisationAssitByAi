"""Ce que le bot voit (captures de la zone de jeu) et ce qu'il fait (souris, clavier).

Deux façons de jouer (réglage window.arriere_plan, option --arriere-plan) :
- au premier plan (défaut) : la vraie souris et le vrai clavier, la fenêtre du jeu devant, capture de l'écran ;
- en arrière-plan (08/10) : gestes envoyés à la fenêtre du jeu par messages (actions/background.py), capture de la
  fenêtre même recouverte (capture/printwindow.py) ; l'utilisateur garde sa souris et son clavier. Pas de survol : rien
  à écarter de la carte avant une capture.
"""

import logging
import random
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from bot.actions.background import BackgroundInput
from bot.actions.input import NO_SPREAD, GameInput, Human, Spread
from bot.actions.stop import StopControl
from bot.capture.printwindow import WindowCapture
from bot.capture.screen import ScreenCapture
from bot.capture.window import GameWindow
from bot.debug.recorder import Recorder
from bot.perception.frame import Frame
from bot.perception.layout import Layout, Point
from bot.perception.ui import find_end_turn_button, movement_zone, park_point, tactical_mode
from bot.state.scene import Phase, read_scene

log = logging.getLogger(__name__)


STEP_ASIDE = ((0, 70), (100, 35), (-100, 35), (0, -130))  # px de référence : hors du sprite survolé, 1 à 2 cases
WATCH_EVERY = 0.1  # secondes entre deux captures quand on guette un changement à l'écran (wait_until)
WATCH_SLACK = 0.001  # échéance de wait_until atteinte à 1 ms près : pas de capture en plus pour un reste d'arrondi


def _hover_disturbs(frame: Frame) -> bool:
    """En combat, la souris survole un combattant : sa fiche remplace la barre de sorts, ou sa zone verte s'affiche."""
    if read_scene(frame).phase is not Phase.COMBAT:
        return False
    return find_end_turn_button(frame) is None or (tactical_mode(frame) and len(movement_zone(frame)) > 3)


class Game:
    background = False  # premier plan, sauf Game(..., background=True)

    def __init__(self, window: GameWindow, control: StopControl, recorder: Recorder,
                 human: Human = Human(), act: bool = True, background: bool = False) -> None:
        self.window = window
        self.control = control
        self.recorder = recorder
        self.human = human
        self.background = background  # arrière-plan : messages à la fenêtre, pas de survol, capture de la fenêtre
        self.input = (BackgroundInput if background else GameInput)(window, control, human, enabled=act)
        self._screen = WindowCapture(window) if background else ScreenCapture()
        self._layout: Layout | None = None

    def look(self, park: bool | None = None) -> Frame:
        """Capture de la zone de jeu.

        Un combattant survolé affiche sa fiche à la place de la barre de sorts et sa zone de déplacement en vert,
        qui masquent le reste : il faut alors écarter la souris. `park` : True l'écarte toujours si elle est sur
        la carte, False jamais, None (défaut) seulement si la capture montre qu'elle gêne."""
        self.input.wait_foreground()
        if park and self._cursor_on_map():
            self._park()
        frame = self._grab()
        if park is None and self._cursor_on_map() and _hover_disturbs(frame):
            frame = self._step_aside()
            if frame is None:
                self._park()
                frame = self._grab()
        return frame

    def _step_aside(self) -> Frame | None:
        """La souris survole un combattant (souvent la cible du sort qui vient de partir) : un petit pas de côté, d'une
        ou deux cases, plutôt qu'un aller-retour jusqu'au bord de l'écran (01/10 : « des mouvements pour rien »).
        None si la vue est encore gênée après chaque pas : on écarte alors la souris au bord."""
        cursor = self.input.cursor()
        if cursor is None:
            return None
        for dx, dy in STEP_ASIDE:
            point = (cursor[0] + self._layout.length(dx), cursor[1] + self._layout.length(dy))
            if not self._layout.on_map(point):
                continue
            self.input.move(point)
            self.control.sleep(0.08)
            frame = self._grab()
            if not _hover_disturbs(frame):
                return frame
        return None

    @property
    def layout(self) -> Layout | None:
        """Disposition de la dernière capture (taille de la fenêtre) ; None avant la première."""
        return self._layout

    def _grab(self) -> Frame:
        # Arrière-plan : la fenêtre est lue directement, sa place à l'écran ne compte pas.
        frame = Frame.of(self._screen.grab(None if self.background else self.window.client_rect()))
        self._layout = frame.layout
        return frame

    def _cursor_on_map(self) -> bool:
        if not self.input.enabled or self._layout is None or self.background:
            return False  # arrière-plan : pas de survol, la souris virtuelle ne cache jamais rien
        cursor = self.input.cursor()
        return cursor is None or self._layout.on_map(cursor)

    def _park(self) -> None:
        self.input.move(park_point(self._layout))
        self.control.sleep(0.08)

    def mouse_away(self) -> None:
        """Arrière-plan : la souris « virtuelle » écartée de la carte (message « souris ici » dans le chat), pour que le jeu
        n'affiche plus la fiche du combattant visé à la place de la barre de sorts. Au premier plan : rien (la vraie
        souris est écartée quand une capture en a besoin)."""
        if self.background and self._layout is not None:
            self.input.hover(park_point(self._layout))

    def sleep(self, seconds: float) -> None:
        """Attente allongée au hasard (réglage human.slowdown) pour ne jamais garder le même rythme."""
        self.control.sleep(seconds * random.uniform(*self.human.slowdown))

    def now(self) -> float:
        """Horloge des attentes, en secondes (les tests la simulent)."""
        return time.monotonic()

    def wait_until(self, predicate: Callable[[Frame], bool], timeout: float, poll: float = WATCH_EVERY,
                   park: bool | None = None) -> Frame | None:
        """Guette l'écran : capture après capture, jusqu'à ce que `predicate` soit vrai ; renvoie cette capture, None
        au bout de `timeout` secondes. Première capture aussitôt, dernière à l'échéance (une seule si le délai est
        nul). Entre deux captures, `poll` s sans allongement au hasard (on regarde, on n'agit pas), par
        StopControl.sleep : l'arrêt (F12) et la pause restent écoutés. `park` : comme look."""
        end = self.now() + timeout
        while True:
            frame = self.look(park)
            if predicate(frame):
                return frame
            left = end - self.now()
            if left <= WATCH_SLACK:
                return None
            self.control.sleep(min(poll, left))

    def move(self, point: tuple[float, float], spread: Spread = NO_SPREAD) -> Point:
        return self.input.move(point, spread)

    def click(self, point: tuple[float, float] | None = None, spread: Spread = NO_SPREAD, button: str = "left") -> bool:
        """False si le clic a été annulé (une autre fenêtre sous la souris)."""
        return self.input.click(point, spread, button) is not False

    def wheel(self, point: tuple[float, float], notches: int, spread: Spread = NO_SPREAD) -> bool:
        """Molette sur `point` : `notches` crans vers le bas (négatif : vers le haut). False si c'est annulé."""
        return self.input.wheel(point, notches, spread) is not False

    def release_keys(self, names: list[str]) -> None:
        release = getattr(self.input, "release_keys", None)
        if release is not None:
            release(names)

    def press(self, key: str) -> None:
        self.input.press(key)
        if key.lower() in ("escape", "esc"):
            self._after_escape(key)

    def _after_escape(self, key: str) -> None:
        """Échap sans rien à fermer ouvre le Menu principal du jeu (Changer de perso / Déconnecter / Quitter Dofus) :
        refermé aussitôt par un second Échap, jamais de clic dedans. 11/10 : un clic tombé ensuite sur « Déconnecter »
        a ouvert « Cette action va quitter ta partie en cours » chez trois personnages."""
        from bot.perception.popups import find_known_popup, load_popup_specs  # évite un import circulaire

        self.sleep(0.25)
        match = find_known_popup(self.look(park=False), load_popup_specs())
        if match is not None and match.spec.name == "menu principal":
            log.warning("Échap a ouvert le Menu principal (rien d'autre à fermer) : je le referme")
            self.input.press(key)
            self.sleep(0.25)

    @contextmanager
    def holding(self, key: str) -> Iterator[None]:
        with self.input.holding(key):
            yield
