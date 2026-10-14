"""Écrans de l'ordinateur, et placement des fenêtres du jeu sur l'un d'eux, agrandies.

Le 11/10, lancées par le launcher, les fenêtres du jeu se sont ouvertes petites sur l'écran principal de
l'utilisateur, un 4K à 175 %, où il ne veut pas que le bot joue. Seule la configuration « fenêtre agrandie sur un
écran 1080p à 100 % » est validée (docs/rapports/ecrans.md) : son second écran (1920 x 1080, 100 %). L'écran est
choisi dans l'interface (une case par écran détecté) ou par window.ecran / --ecran ; au lancement du bot, chaque
fenêtre du jeu y est déplacée puis agrandie (comme le bouton à côté de la croix), toutes sur le même écran : elles se
recouvrent, ce qui ne gêne ni l'arrière-plan (capture de chaque fenêtre même recouverte) ni le premier plan (chaque
fenêtre passée devant à son tour). Agrandir une fenêtre la passe au premier plan, une fois, au placement.
"""

import ctypes
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import win32api
import win32con
import win32gui

from bot.capture.window import GameWindow, Rect

log = logging.getLogger(__name__)

VALIDATED_HEIGHT = 1080  # hauteur (pixels réels) de l'écran des captures de référence
MONITOR_DEFAULTTONEAREST = 2
PLACE_SETTLE = 1.5  # fenêtre déplacée ou agrandie : le jeu se redessine à sa nouvelle taille
NOWHERE = "aucun"  # fenêtres laissées où elles sont
CHOICES = ("auto", "principal", "secondaire", NOWHERE)  # en plus des numéros d'écran


@dataclass(frozen=True)
class ScreenSettings:
    choice: str = "auto"  # écran des fenêtres du jeu (pick_monitor) ; « aucun » : pas de placement
    maximize: bool = True  # agrandies sur cet écran

    @classmethod
    def from_settings(cls, settings: dict, choice: str | None = None) -> "ScreenSettings":
        """window.ecran, window.plein_ecran de config/settings.yaml ; `choice` : option --ecran (None : réglage)."""
        window = settings.get("window") or {}
        raw = choice if choice is not None else window.get("ecran", cls.choice)
        return cls(str(raw if raw is not None else NOWHERE).strip().lower() or NOWHERE,
                   bool(window.get("plein_ecran", cls.maximize)))


def valid_choice(choice: str) -> bool:
    choice = str(choice).strip().lower()
    return choice in CHOICES or (choice.isdigit() and int(choice) >= 1)


@dataclass(frozen=True)
class Monitor:
    handle: int
    rect: Rect  # tout l'écran, en pixels réels
    work: Rect  # sans la barre des tâches
    primary: bool
    dpi: int = 96

    @property
    def scale(self) -> int:
        """Mise à l'échelle de Windows, en % (100, 175...)."""
        return round(self.dpi * 100 / 96)

    def describe(self) -> str:
        return (f"{self.rect.width}x{self.rect.height} à {self.scale} %"
                f"{' (principal)' if self.primary else ''}")


def _rect(left: int, top: int, right: int, bottom: int) -> Rect:
    return Rect(left, top, right - left, bottom - top)


def _dpi(handle: int) -> int:
    x, y = ctypes.c_uint(), ctypes.c_uint()
    try:
        ctypes.windll.shcore.GetDpiForMonitor(handle, 0, ctypes.byref(x), ctypes.byref(y))  # MDT_EFFECTIVE_DPI
    except (AttributeError, OSError):
        return 96
    return x.value or 96


def monitors() -> list[Monitor]:
    """Écrans, de gauche à droite."""
    found = []
    for handle, _, _ in win32api.EnumDisplayMonitors():
        info = win32api.GetMonitorInfo(handle)
        found.append(Monitor(int(handle), _rect(*info["Monitor"]), _rect(*info["Work"]), bool(info["Flags"] & 1),
                             _dpi(int(handle))))
    return sorted(found, key=lambda m: (m.rect.left, m.rect.top))


def pick_monitor(choice: str, screens: list[Monitor]) -> Monitor | None:
    """Écran choisi (réglage connexion.ecran) :
    - « auto » : un écran de 1080 pixels de haut (celui des captures de référence), le principal s'il l'est, sinon
      l'écran principal ;
    - « principal », « secondaire » (le premier écran qui n'est pas le principal) ;
    - un numéro : 1 pour l'écran le plus à gauche.
    None si le choix ne correspond à aucun écran."""
    if not screens:
        return None
    primary = next((m for m in screens if m.primary), screens[0])
    choice = str(choice).strip().lower()
    if choice in ("", "auto"):
        fitting = [m for m in screens if m.rect.height == VALIDATED_HEIGHT]
        return next((m for m in fitting if m.primary), fitting[0] if fitting else primary)
    if choice == "principal":
        return primary
    if choice == "secondaire":
        return next((m for m in screens if not m.primary), None)
    if choice.isdigit() and 1 <= int(choice) <= len(screens):
        return screens[int(choice) - 1]
    return None


def is_maximized(hwnd: int) -> bool:
    """Fenêtre agrandie (pywin32 n'a pas IsZoomed : appel direct à Windows)."""
    return bool(ctypes.windll.user32.IsZoomed(hwnd))


def monitor_of(hwnd: int) -> int:
    """Écran où est la fenêtre ; 0 si elle n'existe plus."""
    handle = win32api.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    return int(handle) if handle else 0


def screen_labels(screens: list[Monitor]) -> list[tuple[str, str]]:
    """(valeur de window.ecran, libellé) de chaque écran, pour l'interface : « 1 », « Écran 1 : 1920x1080 à 100 % »."""
    return [(str(i), f"Écran {i} : {m.describe()}") for i, m in enumerate(screens, 1)]


def place_window(hwnd: int, monitor: Monitor, maximize: bool = True) -> bool:
    """Fenêtre déplacée sur `monitor` puis agrandie ; False si elle y était déjà (agrandie si demandé), ou fermée."""
    if not win32gui.IsWindow(hwnd):
        return False
    here = monitor_of(hwnd) == monitor.handle
    zoomed = is_maximized(hwnd)
    if here and (zoomed or not maximize):
        return False
    if win32gui.IsIconic(hwnd) or (zoomed and not here):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)  # une fenêtre agrandie ne se déplace pas
    if not here:
        win32gui.SetWindowPos(hwnd, 0, monitor.work.left + 40, monitor.work.top + 40, 0, 0,
                              win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
    if maximize:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    return True


def place_windows(windows: Iterable[GameWindow], settings: ScreenSettings, wait: Callable[[float], None]
                  ) -> Monitor | None:
    """Fenêtres du jeu sur l'écran choisi, agrandies si demandé ; renvoie cet écran (None : pas de placement)."""
    if settings.choice == NOWHERE:
        return None
    screens = monitors()
    monitor = pick_monitor(settings.choice, screens)
    if monitor is None:
        log.warning("Écran « %s » introuvable (écrans : %s) : fenêtres du jeu laissées où elles sont", settings.choice,
                    ", ".join(label for _, label in screen_labels(screens)))
        return None
    moved = [w for w in windows if place_window(w.hwnd, monitor, settings.maximize)]
    if moved:
        log.info("%d fenêtre(s) du jeu placée(s) sur l'écran %s%s", len(moved), monitor.describe(),
                 ", agrandies" if settings.maximize else "")
        wait(PLACE_SETTLE)
    if monitor.rect.height != VALIDATED_HEIGHT or monitor.scale != 100:
        log.warning("Écran %s : seul un écran 1080p à 100 %% est validé, la lecture de l'écran peut se tromper",
                    monitor.describe())
    return monitor
