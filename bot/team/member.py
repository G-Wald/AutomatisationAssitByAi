"""Un personnage de l'équipe : sa fenêtre, son jeu, ses sorts, son IA de combat.

Une seule fenêtre peut être au premier plan, et il le faut pour la capturer (elle doit être visible) comme pour y
cliquer ou y appuyer sur une touche. Chaque regard ou geste d'un personnage passe donc d'abord sa fenêtre devant
(`Focus`), sans rien faire si elle y est déjà : le code des actions (attaque, combat, changement de carte) reste celui
du mode solo. Après un changement de fenêtre, une courte attente (team.switch_delay) laisse le jeu se redessiner,
mais seulement avant une CAPTURE : un clic ou une touche part aussitôt (le jeu les reçoit même avant de s'être
redessiné). Avec 8 personnages, les actions refaites dans chaque fenêtre (Prêt, soleil...) y gagnent autant de fois.

Le journal et les captures disent de quel personnage il s'agit : « [Jesuisleking] Mon tour », et
« 0042_Jesuisleking_tour.jpg ».
"""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pywintypes

from bot.actions.input import NO_SPREAD, Human, Spread
from bot.actions.stop import StopControl
from bot.brain.combat import CombatAI, CombatSettings
from bot.brain.game import Game
from bot.brain.memory import SessionSettings
from bot.brain.popups import PopupCloser
from bot.brain.spellbook import SpellBook
from bot.capture.window import GameWindow
from bot.debug.recorder import Recorder
from bot.perception.layout import Layout, Point
from bot.perception.popups import load_popup_specs

log = logging.getLogger(__name__)

FOCUS_TRIES = 3


def to_reference(layout: Layout, point: tuple[float, float]) -> tuple[float, float]:
    """Point d'une capture -> repère de référence (inverse de Layout.point), pour le refaire dans une autre fenêtre."""
    return layout.to_reference(point)


class Focus:
    """Fenêtre au premier plan, celle du personnage qui regarde ou agit.

    `passive` : ne change jamais de fenêtre ; mode observation (le bot ne regarde que celle que tu as devant toi) ou
    arrière-plan (08/10 : gestes et captures vont directement à chaque fenêtre, rien n'est passé devant)."""

    def __init__(self, control: StopControl, delay: float = 0.35, passive: bool = False) -> None:
        self.control = control
        self.delay = delay
        self.passive = passive
        self.names: dict[int, str] = {}  # fenêtre -> personnage, pour le journal
        self.current: GameWindow | None = None
        self._switched_at = 0.0  # dernier changement de fenêtre (le redessin n'est attendu qu'avant une capture)
        self._settled = True  # fenêtre devant redessinée depuis le dernier changement

    @property
    def current_name(self) -> str:
        return self.names.get(self.current.hwnd, "") if self.current is not None else ""

    def to(self, window: GameWindow, settle: bool = True) -> None:
        """Fenêtre au premier plan ; `settle` : attendre qu'elle soit redessinée (avant une capture)."""
        if self.passive:
            self.current = window
            return
        if not window.is_foreground():
            for attempt in range(1, FOCUS_TRIES + 1):
                try:
                    window.bring_to_front()
                    break
                except (RuntimeError, pywintypes.error) as error:
                    if attempt == FOCUS_TRIES:
                        # GameInput attendra qu'elle passe devant (tu peux cliquer dessus) plutôt que d'agir ailleurs.
                        log.warning("Impossible de passer « %s » au premier plan (%s)", window.title, error)
                        return
                    self.control.sleep(0.3)
            self._switched_at, self._settled = time.monotonic(), False
        self.current = window
        if settle and not self._settled:
            wait = self.delay - (time.monotonic() - self._switched_at)  # moins si un clic a déjà pris du temps
            if wait > 0:
                self.control.sleep(wait)
            self._settled = True


class MemberGame(Game):
    """Le jeu d'un personnage de l'équipe : sa fenêtre passe au premier plan avant chaque regard ou geste."""

    def __init__(self, window: GameWindow, control: StopControl, recorder, human: Human, act: bool, focus: Focus,
                 background: bool = False) -> None:
        super().__init__(window, control, recorder, human, act, background)
        self.focus = focus
        self.last_map_click: tuple[tuple[float, float], float] | None = None  # (point de référence, heure) : épée

    def look(self, park: bool | None = None):
        self.focus.to(self.window)
        return super().look(park)

    def move(self, point: tuple[float, float], spread: Spread = NO_SPREAD) -> Point:
        self.focus.to(self.window, settle=False)
        return super().move(point, spread)

    def hover(self, point: tuple[float, float], spread: Spread = NO_SPREAD) -> None:
        self.focus.to(self.window, settle=False)
        super().hover(point, spread)

    def click(self, point: tuple[float, float] | None = None, spread: Spread = NO_SPREAD, button: str = "left") -> bool:
        self.focus.to(self.window, settle=False)
        done = super().click(point, spread, button) is not False
        # Clic « sur place » (point None) : là où est la souris virtuelle. 17/10 : l'attaque d'un groupe se fait ainsi ;
        # le dernier clic retenu était celui sur la barre de défilement des sorts, l'épée cherchée au mauvais endroit.
        where = point if point is not None else self.input.cursor()
        if done and where is not None and self._layout is not None and self._layout.on_map(where):
            self.last_map_click = (to_reference(self._layout, where), time.monotonic())
        return done

    def wheel(self, point: tuple[float, float], notches: int, spread: Spread = NO_SPREAD) -> bool:
        self.focus.to(self.window, settle=False)
        return super().wheel(point, notches, spread)

    def press(self, key: str) -> None:
        self.focus.to(self.window, settle=False)
        super().press(key)

    def type_text(self, text: str) -> bool:
        self.focus.to(self.window, settle=False)
        return super().type_text(text)

    @contextmanager
    def holding(self, key: str) -> Iterator[None]:
        self.focus.to(self.window, settle=False)
        with super().holding(key):
            yield


class TaggedRecorder:
    """Captures du personnage, préfixées de son nom (même dossier de session pour toute l'équipe)."""

    def __init__(self, recorder: Recorder, name: str) -> None:
        self.recorder = recorder
        self.name = name

    @property
    def dir(self):
        return self.recorder.dir

    def keeps(self, lossless: bool = False) -> bool:
        return self.recorder.keeps(lossless)

    def save(self, image, label: str, lossless: bool = False, quality: int = 80):
        return self.recorder.save(image, f"{self.name}_{label}", lossless=lossless, quality=quality)


@dataclass(eq=False)
class Member:
    name: str
    game: Game
    spellbook: SpellBook
    ai: CombatAI
    popups: PopupCloser

    @property
    def window(self) -> GameWindow:
        return self.game.window

    def in_front(self) -> bool:
        """Sa fenêtre est devant ; en arrière-plan (aucune devant) : c'est celle du dernier personnage qui a agi."""
        if getattr(self.game, "background", False):
            current = self.game.focus.current
            return current is not None and current.hwnd == self.window.hwnd
        return self.game.window.is_foreground()


def make_member(name: str, window: GameWindow, settings: dict, focus: Focus, control: StopControl, recorder: Recorder,
                human: Human, act: bool = True, background: bool = False) -> Member:
    """Mêmes briques que le mode solo (brain/loop.py), une par personnage : ses sorts et ses caractéristiques (lus
    une fois, ou repris de la dernière session : session.mode), sa mémoire de combat (façon de lancer un sort sur soi,
    raccourcis...)."""
    focus.names[window.hwnd] = name
    game = MemberGame(window, control, TaggedRecorder(recorder, name), human, act, focus, background)
    spellbook = SpellBook(game, session=SessionSettings.from_settings(settings))  # sorts et caractéristiques
    popups = settings.get("popups") or {}
    return Member(
        name=name,
        game=game,
        spellbook=spellbook,
        ai=CombatAI(game, CombatSettings.from_settings(settings), spellbook, name=name),  # nom : lu au survol
        popups=PopupCloser(game, load_popup_specs(), unknown_key=popups.get("unknown_window_key") or "",
                           unknown_delay=float(popups.get("unknown_window_delay", 20))),
    )


class MemberTag(logging.Filter):
    """Ajoute aux lignes du journal le nom du personnage dont la fenêtre est devant : « [Jesuisleking] Mon tour »."""

    def __init__(self, current: Callable[[], str]) -> None:
        super().__init__()
        self.current = current

    def filter(self, record: logging.LogRecord) -> bool:
        name = self.current()
        record.member = f"[{name}] " if name else ""
        return True


def tag_logs(current: Callable[[], str]) -> None:
    """À appeler après Recorder.setup_logging : même format, avec le nom du personnage."""
    formatter = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s %(member)s%(message)s", "%H:%M:%S")
    tag = MemberTag(current)
    for handler in logging.getLogger().handlers:
        handler.addFilter(tag)
        handler.setFormatter(formatter)
