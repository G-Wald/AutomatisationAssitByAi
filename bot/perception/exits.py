"""Sorties de la carte : les petits soleils posés sur les cases du bord.

Un soleil est dessiné au centre d'une case : un « O » orange entouré de 4 rayons jaunes en croix (et de rayons
verts, qui se confondent avec l'herbe). On le cherche sur le masque des pixels orange, case par case, sur les
rangées du bord seulement : un soleil au milieu de la carte mène dans une maison ou une grotte, pas sur la
carte voisine.

Mesuré sur les cartes Forêt 2,4 et Champs 4,3 : les 8 soleils ont un score de 0,58 à 1,0 (0,58 : à moitié caché
par des feuilles), les autres cases 0,42 au plus.

Touche A (un appui) : le jeu entoure de blanc les cases des soleils, avec une flèche rouge au-dessus (cachée pour
ceux du haut). Le contour est un trait clair épais (5 px) sur le bord de la case, qu'on distingue des fines
lignes de la grille (2 px, à peine plus claires que l'herbe) et des pierres (claires aussi à l'intérieur).
"""

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from bot.perception.frame import Frame
from bot.perception.grid import Cell, distance
from bot.perception.layout import Point
from bot.perception.templates import ASSETS_DIR

TOP, BOTTOM, LEFT, RIGHT = "haut", "bas", "gauche", "droite"
SIDES = (TOP, BOTTOM, LEFT, RIGHT)
OPPOSITE = {TOP: BOTTOM, BOTTOM: TOP, LEFT: RIGHT, RIGHT: LEFT}
DELTA = {TOP: (0, -1), BOTTOM: (0, 1), LEFT: (-1, 0), RIGHT: (1, 0)}  # coordonnées de la carte voisine

EDGE_ROWS = 3  # un soleil de changement de carte est sur l'une des 3 rangées de cases du bord
SUN_MIN = 0.5
OUTLINE_MIN = 0.55
OUTLINE_BRIGHT = 205  # contour : rouge et vert au moins à ce niveau (le bleu est un peu plus bas : blanc crème)
OUTLINE_THICK = 3  # px de trait clair d'affilée, en hauteur (grille : 2 px, et bien moins clairs)
SAME_SUN = 3  # cases au plus entre deux détections d'un même soleil (au-delà : un autre soleil du même côté)


@dataclass(frozen=True)
class Exit:
    side: str
    cell: Cell
    point: Point  # centre de la case du soleil
    score: float
    marked: bool = False  # trouvé grâce au contour de la touche A
    remembered: bool = False  # position gardée d'un passage précédent (data/carte.json), pas cherchée à l'écran


def sun_mask(image: np.ndarray) -> np.ndarray:
    """Pixels orange-jaune du soleil (l'herbe, jaune-vert, a autant de vert que de rouge)."""
    c = image.astype(np.int16)
    b, g, r = c[:, :, 0], c[:, :, 1], c[:, :, 2]
    return ((r - g >= 18) & (r >= 160) & (b <= 90) & (r - b >= 120)).astype(np.float32)


@lru_cache
def _sun_template() -> np.ndarray:
    image = cv2.imdecode(np.fromfile(ASSETS_DIR / "carte" / "soleil.png", np.uint8), cv2.IMREAD_GRAYSCALE)
    return cv2.GaussianBlur(image.astype(np.float32) / 255, (3, 3), 0)


def side_of(frame: Frame, cell: Cell) -> str | None:
    """Bord de la carte près duquel est la case (None : au milieu). Dans un coin, le bord le plus proche."""
    cells = frame.grid.cells()
    i_min, i_max = min(c[0] for c in cells), max(c[0] for c in cells)
    j_min, j_max = min(c[1] for c in cells), max(c[1] for c in cells)
    i, j = cell
    gaps = {TOP: j - j_min, BOTTOM: j_max - j, LEFT: (i - i_min) * 2, RIGHT: (i_max - i) * 2}  # 2 rangées j = 1 colonne i
    side = min(gaps, key=gaps.get)
    limit = EDGE_ROWS if side in (TOP, BOTTOM) else EDGE_ROWS * 2
    return side if gaps[side] < limit else None


def _edge_cells(frame: Frame) -> list[tuple[Cell, str]]:
    return [(cell, side) for cell in frame.grid.cells() if (side := side_of(frame, cell)) is not None]


def _best_per_side(found: list[Exit]) -> list[Exit]:
    """Un soleil par côté (le plus net) ; plusieurs soleils voisins d'un même côté mènent au même endroit."""
    best: dict[str, Exit] = {}
    for exit_ in found:
        if exit_.side not in best or exit_.score > best[exit_.side].score:
            best[exit_.side] = exit_
    return sorted(best.values(), key=lambda e: SIDES.index(e.side))


def _clusters_per_side(found: list[Exit]) -> list[Exit]:
    """Un soleil par groupe de cases voisines, mais tous les soleils distincts d'un même côté : sur une carte coupée par
    un rempart, deux soleils à gauche, un de chaque côté du mur (11/10, cité d'Astrub)."""
    kept: list[Exit] = []
    for exit_ in sorted(found, key=lambda e: e.score, reverse=True):
        if not any(k.side == exit_.side and distance(k.cell, exit_.cell) <= SAME_SUN for k in kept):
            kept.append(exit_)
    return sorted(kept, key=lambda e: (SIDES.index(e.side), -e.score))


def find_suns(frame: Frame, every: bool = False) -> list[Exit]:
    """Soleils visibles sans la touche A, au plus un par côté (`every` : tous les soleils distincts de chaque côté)."""
    mask = cv2.GaussianBlur(sun_mask(frame.image), (3, 3), 0)
    template = _sun_template()
    s = frame.layout.scale
    if abs(s - 1) > 0.02:
        template = cv2.resize(template, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    th, tw = template.shape
    found = []
    for cell, side in _edge_cells(frame):
        cx, cy = frame.grid.center(cell)
        x0, y0 = round(cx - tw / 2 - 6 * s), round(cy - th / 2 - 6 * s)
        window = mask[max(0, y0) : y0 + th + round(12 * s), max(0, x0) : x0 + tw + round(12 * s)]
        if window.shape[0] < th or window.shape[1] < tw or window.max() == 0:
            continue
        score = float(cv2.matchTemplate(window, template, cv2.TM_CCOEFF_NORMED).max())
        if score >= SUN_MIN:
            found.append(Exit(side, cell, (round(cx), round(cy)), score))
    return _clusters_per_side(found) if every else _best_per_side(found)


def _outline_score(bright: np.ndarray, frame: Frame, cell: Cell) -> float:
    """Contour clair épais sur chacun des 4 côtés de la case (score : le côté le moins bien tracé), à condition que
    ni l'intérieur ni l'extérieur tout proche ne soient clairs eux aussi (pierres, chemin)."""
    cx, cy = frame.grid.center(cell)
    hw, hh = frame.grid.half_w, frame.grid.half_h
    corners = [(cx - hw, cy), (cx, cy - hh), (cx + hw, cy), (cx, cy + hh)]
    height, width = bright.shape
    reach = max(2, round(frame.layout.length(2)))
    sides, around, around_bright = [], 0, 0
    for k in range(4):
        (xa, ya), (xb, yb) = corners[k], corners[(k + 1) % 4]
        hits = seen = 0
        for t in np.linspace(0.15, 0.85, 10):
            x, y = xa + t * (xb - xa), ya + t * (yb - ya)
            xi, yi = round(x), round(y)
            if not (0 <= xi < width and reach <= yi < height - reach):
                continue
            seen += 1
            hits += bool(bright[yi - reach : yi + reach + 1, xi].any())
            for f in (0.3, -0.25):  # un peu vers le centre, un peu vers l'extérieur
                ax, ay = round(x + f * (cx - x)), round(y + f * (cy - y))
                if 0 <= ax < width and 0 <= ay < height:
                    around += 1
                    around_bright += bool(bright[ay, ax])
        if seen >= 4:
            sides.append(hits / seen)
    if len(sides) < 3 or (around and around_bright / around > 0.25):
        return 0.0  # case coupée par le bord de l'écran, ou dans une zone claire
    return min(sides)


def find_marked_exits(frame: Frame, every: bool = False) -> list[Exit]:
    """Sorties entourées de blanc après un appui sur A, au plus une par côté (`every` : toutes celles qui sont
    distinctes)."""
    c = frame.image.astype(np.int16)
    light = (np.minimum(c[:, :, 1], c[:, :, 2]) >= OUTLINE_BRIGHT) & (c.max(axis=2) - c.min(axis=2) <= 70)
    # Trait épais : OUTLINE_THICK pixels clairs d'affilée en hauteur (les lignes de la grille n'y arrivent pas).
    kernel = np.ones((OUTLINE_THICK, 1), np.uint8)
    bright = cv2.erode(light.astype(np.uint8), kernel).astype(bool)
    found = []
    for cell, side in _edge_cells(frame):
        score = _outline_score(bright, frame, cell)
        if score >= OUTLINE_MIN:
            cx, cy = frame.grid.center(cell)
            found.append(Exit(side, cell, (round(cx), round(cy)), score, marked=True))
    return _clusters_per_side(found) if every else _best_per_side(found)
