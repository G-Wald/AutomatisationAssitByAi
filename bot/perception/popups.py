"""Fenêtres ouvertes par-dessus la carte (résultat du combat, niveau supérieur...).

Deux niveaux de reconnaissance :
- fenêtres connues : décrites dans assets/popups.yaml, avec un modèle qui les reconnaît
  et le bouton (ou la touche) qui les ferme ;
- fenêtres inconnues : toute grande surface beige posée sur la carte.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

import cv2
import numpy as np

from bot.perception.blobs import find_blobs
from bot.perception.colors import UI_BEIGE, near
from bot.perception.frame import Frame
from bot.perception.layout import REF_HEIGHT, Box, Point
from bot.perception.templates import ASSETS_DIR, load_template, match_template

POPUPS_FILE = ASSETS_DIR / "popups.yaml"
# Fenêtre à tableaux : ses lignes sombres découpent le fond beige. Résultat d'un combat à 4 contre 5 : 28 % de beige
# dans son rectangle (03/10, fenêtre restée ouverte 7 minutes, la carte cachée) ; 0,3 avant.
WINDOW_FILL = 0.2


@dataclass(frozen=True)
class PopupSpec:
    name: str
    detect: str  # modèle qui reconnaît la fenêtre
    close: str = ""  # modèle du bouton qui la ferme...
    key: str = ""  # ... ou touche qui la ferme
    threshold: float = 0.85


@dataclass(frozen=True)
class PopupMatch:
    spec: PopupSpec
    close_point: Point | None  # None : fermer avec la touche
    close_spread: tuple[float, float] = (0.0, 0.0)  # zone de clic aléatoire dans le bouton


def load_popup_specs(path: Path = POPUPS_FILE) -> list[PopupSpec]:
    with path.open(encoding="utf-8") as f:
        entries = (yaml.safe_load(f) or {}).get("popups") or []
    return [PopupSpec(**entry) for entry in entries]


QUIT_TEXT = "Cette action va quitter ta partie en cours"
QUIT_TEXT_SIZE, QUIT_BUTTON_SIZE = 22, 18  # Verdana, repère de référence (captures du 11/10)


def find_quit_dialog_no(frame: Frame) -> Point | None:
    """Fenêtre « Attention : Cette action va quitter ta partie en cours, veux-tu continuer ? Oui / Non » : centre du
    bouton « Non » (à droite de « Oui », sur la même ligne), None si la fenêtre n'est pas là ou si « Non » n'est pas sûr.

    11/10 : un Échap sans menu à fermer a ouvert le Menu principal, puis un clic « ailleurs » est tombé sur
    « Déconnecter » : cette fenêtre s'est ouverte chez trois personnages. Jamais « Oui »."""
    from bot.perception.text import find_text  # import local : text.py est lourd (rendu des polices)

    box = frame.layout.map_box
    if find_text(frame, QUIT_TEXT, box, QUIT_TEXT_SIZE, dark=True, threshold=0.75) is None:
        return None
    no = find_text(frame, "Non", box, QUIT_BUTTON_SIZE, threshold=0.8)
    yes = find_text(frame, "Oui", box, QUIT_BUTTON_SIZE, threshold=0.8)
    if no is None or yes is None:
        return None
    (nx, ny), (yx, yy) = no[0], yes[0]
    if nx <= yx + frame.layout.length(100) or abs(ny - yy) > frame.layout.length(20):
        return None
    return no[0]


def find_known_popup(frame: Frame, specs: list[PopupSpec]) -> PopupMatch | None:
    """Fenêtre connue ouverte. On ne cherche ses modèles qu'autour d'une grande surface beige (une fenêtre) :
    les chercher sur tout l'écran prenait 0,2 s, à chaque coup d'œil. Le titre n'est cherché que dans le haut de la
    surface (son bandeau est juste au-dessus du fond beige) : une étendue de sable ne coûte presque rien."""
    margin = round(frame.layout.length(120))
    for x0, y0, x1, y1 in _beige_windows(frame, min_fill=WINDOW_FILL):
        title = (max(0, x0 - margin), max(0, y0 - margin), min(frame.layout.width, x1 + margin),
                 min(frame.layout.height, y0 + margin))
        box = (max(0, x0 - margin), max(0, y0 - margin), min(frame.layout.width, x1 + margin),
               min(frame.layout.height, y1 + 2 * margin))  # le bouton « Fermer » est sous la fenêtre
        for spec in specs:
            if match_template(frame, spec.detect, box=title, threshold=spec.threshold) is None:
                continue
            if spec.key:
                return PopupMatch(spec, None)
            button = match_template(frame, spec.close, box=box, threshold=spec.threshold)
            if button is not None:
                height, width = load_template(spec.close).shape[:2]
                return PopupMatch(spec, button[0], frame.layout.spread(width * 0.3, height * 0.25))
    return None


def find_context_menu(frame: Frame) -> Box | None:
    """Menu ouvert par un clic sur un personnage ou un monstre (« Baffer... », « Monstres / Attaquer... ») :
    rectangle beige plein. Les nuages, pierres et chemins beiges ne remplissent pas leur rectangle (0,4 au plus,
    contre 0,94 pour un menu, mesuré le 28/09)."""
    layout = frame.layout
    box = layout.map_box
    for blob in find_blobs(near(frame.crop(box), UI_BEIGE, 10), offset=box[:2], close=3):
        if layout.length(110) <= blob.w <= layout.length(450) and layout.length(45) <= blob.h <= layout.length(400) \
                and blob.fill >= 0.8:
            return blob.x, blob.y, blob.x + blob.w, blob.y + blob.h
    return None


def find_small_menu(frame: Frame, around: Point | None = None, radius: float = 250) -> Box | None:
    """Petit menu ouvert par un clic (une seule entrée beige, comme « Pipelette Nhin / Parler » sur un PNJ, ou
    « Combat / Rejoindre ») : trop bas pour find_context_menu. Entrée beige pleine d'au moins 18 px de haut (33 px pour
    « Parler », le 01/10), près de `around` (le clic) s'il est donné."""
    layout = frame.layout
    box = layout.map_box
    for blob in find_blobs(near(frame.crop(box), UI_BEIGE, 10), offset=box[:2], close=3):
        if not (layout.length(90) <= blob.w <= layout.length(450) and layout.length(18) <= blob.h <= layout.length(400)
                and blob.fill >= 0.8):
            continue
        x, y = blob.center
        if around is None or abs(x - around[0]) + abs(y - around[1]) <= layout.length(radius):
            return blob.x, blob.y, blob.x + blob.w, blob.y + blob.h
    return None


FLASH_MENU = "menu_flash.png"  # « DOFUS RETRO Client », 1re ligne du menu de Flash (sans le numéro de version)
FLASH_MENU_SCALE = 1057 / REF_HEIGHT  # découpé tel quel dans une fenêtre de 1057 px de haut (14/10)
FLASH_MENU_THRESHOLD = 0.8  # 1,0 sur sa capture ; 0,5 au plus sur les autres captures entières (14/10)
FLASH_MENU_REACH = (350, 300)  # px de référence autour du clic : le menu s'ouvre au clic (à gauche ou au-dessus près
# d'un bord), d'environ 300 x 220
FLASH_CLOSE_REF = (1465, 991)  # repère de référence : fond beige en bas à gauche de la barre de sorts (15/10)
FLASH_CLOSE_CLEARANCE = 6  # px de référence autour de ce point : beige uni exigé


def find_flash_menu(frame: Frame, around: Point | None = None) -> Point | None:
    """Menu contextuel de Flash (« DOFUS RETRO Client », « Flash player WIN », « Qualité Flash », « Options »...) :
    ouvert par un clic droit tombé à côté d'un monstre (14/10, un Piou, clic de sonde en arrière-plan). Échap ne le
    ferme pas (il ouvre le Menu principal derrière) : il se ferme d'un clic ailleurs (flash_close_point). Centre de sa
    1re ligne, None s'il n'est pas là. Cherché autour de `around` (le clic) s'il est donné, sinon au-dessus des
    grandes surfaces beiges de la carte (ses autres lignes), pas sur tout l'écran (0,16 s)."""
    layout = frame.layout
    if around is not None:
        rx, ry = layout.spread(*FLASH_MENU_REACH)
        boxes = [(round(around[0] - rx), round(around[1] - ry), round(around[0] + rx), round(around[1] + ry))]
    else:
        up, side = layout.length(120), layout.length(20)
        boxes = [(round(x0 - side), round(y0 - up), round(x1 + side), round(y0 + side))
                 for x0, y0, x1, y1 in _beige_windows(frame, min_fill=0.5)]
    template = load_template(FLASH_MENU)
    factors = {1.0, round(layout.scale / FLASH_MENU_SCALE, 3)}  # sa taille suit-elle la fenêtre ? les deux essayées
    for x0, y0, x1, y1 in boxes:
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(layout.width, x1), min(layout.height, y1)
        region = frame.crop((x0, y0, x1, y1))
        for factor in factors:
            sized = template if factor == 1.0 else cv2.resize(template, None, fx=factor, fy=factor,
                                                                 interpolation=cv2.INTER_AREA)
            th, tw = sized.shape[:2]
            if region.shape[0] < th or region.shape[1] < tw:
                continue
            _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(region, sized, cv2.TM_CCOEFF_NORMED))
            if score >= FLASH_MENU_THRESHOLD:
                return x0 + x + tw // 2, y0 + y + th // 2
    return None


def flash_close_point(frame: Frame) -> Point | None:
    """Où cliquer pour fermer le menu de Flash sans rien déclencher : le fond beige en bas à gauche de la barre de
    sorts, sous aucun raccourci. Un clic dans le chat ne le ferme pas (15/10, deux fois en jeu : le chat n'est pas
    dans la scène Flash). None si ce fond n'est pas beige uni à cet endroit : en combat, le bouton de fin de tour y
    est (le menu de Flash ne s'ouvre qu'en cherchant un groupe, hors combat), ou une fenêtre assombrit l'écran."""
    x, y = frame.layout.point(*FLASH_CLOSE_REF)
    r = round(frame.layout.length(FLASH_CLOSE_CLEARANCE))
    patch = frame.image[max(0, y - r) : y + r + 1, max(0, x - r) : x + r + 1]
    if patch.size == 0 or not near(patch, UI_BEIGE, 10).all():
        return None
    return x, y


def find_unknown_window(frame: Frame) -> Box | None:
    """Grande surface beige rectangulaire sur la carte (les chemins de pierre et les menus sont plus petits)."""
    return _beige_window(frame, min_fill=0.5)


def _beige_window(frame: Frame, min_fill: float) -> Box | None:
    windows = _beige_windows(frame, min_fill)
    return windows[0] if windows else None


def _beige_windows(frame: Frame, min_fill: float) -> list[Box]:
    """Grandes surfaces beiges assez pleines, la plus grande d'abord (3 au plus)."""
    layout = frame.layout
    box = layout.map_box
    small = cv2.resize(frame.crop(box), None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)  # 16 fois moins de pixels
    found = []
    for blob in find_blobs(near(small, UI_BEIGE, 10), close=2):
        if 4 * blob.w >= layout.length(250) and 4 * blob.h >= layout.length(120) and blob.fill >= min_fill:
            x, y = box[0] + 4 * blob.x, box[1] + 4 * blob.y
            found.append((blob.w * blob.h, (x, y, x + 4 * blob.w, y + 4 * blob.h)))
    return [window for _, window in sorted(found, reverse=True)[:3]]
