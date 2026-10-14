"""Combattants sur la carte, repérés par le cercle rouge ou bleu dessiné au sol sous chacun d'eux.

La souris doit être hors de la carte : le survol d'un combattant affiche sa zone de déplacement
en vert par-dessus les cercles, et son infobulle peut en masquer d'autres.

Deux combattants d'une même équipe sur des cases voisines en diagonale ont des cercles qui se touchent : une
seule tache, trop grande pour un cercle (le 01/10, deux Bouftous collés sont restés invisibles, tour passé).
Une telle tache est découpée en cherchant, sur chaque case qu'elle couvre, une ellipse de cercle centrée sur la case
et vide à l'intérieur (les cases de placement, colorées en entier, forment aussi de grandes taches).
"""

from dataclasses import dataclass

import cv2
import numpy as np

from bot.perception.blobs import Blob, find_blobs
from bot.perception.colors import TEAMS, team_mask
from bot.perception.frame import Frame
from bot.perception.grid import Cell, Grid
from bot.perception.layout import Layout

RING_AXES = (33, 16.5)  # demi-axes de l'ellipse d'un cercle au sol
RING_MIN_SCORE = 0.6  # part de l'ellipse colorée : 0,79 à 0,98 sur un cercle (sprite dessus), 0,1 à côté (01/10)
HOLLOW_MAX = 0.3  # ellipse intérieure (0,7 fois plus petite) colorée : 0 dans un cercle, 0,77 à 0,92 sur une case pleine
ARC_MIN_SCORE = 0.45  # cercle à moitié caché : 0,52 à 0,69 sur sa case, 0,10 au plus à côté, 0,31 hors cercles (03/10)
_ANGLES = np.linspace(0, 2 * np.pi, 48, endpoint=False)


@dataclass(frozen=True)
class Fighter:
    team: str
    x: float  # centre du cercle = centre de la case
    y: float
    cell: Cell


def _is_ring(blob: Blob, layout: Layout) -> bool:
    """Ellipse creuse d'environ 67 x 34 px (les cases de placement, elles, sont pleines et plus grandes)."""
    return (
        layout.length(52) <= blob.w <= layout.length(85)
        and layout.length(26) <= blob.h <= layout.length(44)
        and 1.5 <= blob.w / blob.h <= 2.7
        and layout.area(200) <= blob.area <= layout.area(900)
        and blob.fill <= 0.45
    )


def _touching_rings(blob: Blob, layout: Layout) -> bool:
    """Tache creuse plus grande qu'un cercle : plusieurs cercles collés (115 x 55 px pour deux, le 01/10)."""
    return (
        layout.length(52) <= blob.w <= layout.length(400)
        and layout.length(26) <= blob.h <= layout.length(200)
        and blob.area >= layout.area(300)
        and blob.fill <= 0.45
    )


def ring_score(mask: np.ndarray, x: float, y: float, layout: Layout, size: float = 1.0) -> float:
    """Part de l'ellipse d'un cercle centré en (x, y) (`size` fois sa taille) qui tombe sur le masque."""
    a, b = (layout.length(v) * size for v in RING_AXES)
    xs = np.rint(x + a * np.cos(_ANGLES)).astype(int)
    ys = np.rint(y + b * np.sin(_ANGLES)).astype(int)
    inside = (xs >= 0) & (ys >= 0) & (xs < mask.shape[1]) & (ys < mask.shape[0])
    return float(mask[ys[inside], xs[inside]].mean()) if inside.any() else 0.0


# Corps d'un combattant à l'écran, autour du centre de sa case, en demi-cases : demi-largeur, hauteur au-dessus,
# débord sous le centre (un personnage fait environ 50 x 95 px pour des cases de 96 x 49).
SPRITE_HALF_W, SPRITE_UP, SPRITE_DOWN = 0.55, 3.8, 0.35
# Où cliquer dans le losange d'une case, du centre aux bords, en demi-cases (tous à l'intérieur du losange).
CELL_SPOTS = ((0, 0), (0, 0.5), (-0.7, 0), (0.7, 0), (0, -0.5))  # coins à 0,7 : hors du corps d'un voisin de devant


def clear_point(grid: Grid, cell: Cell, fighters: list[Fighter]) -> tuple[tuple[float, float], tuple[float, float]]:
    """Point (et demi-étendue du clic) pour viser la case `cell` sans toucher le corps d'un combattant : un clic sur
    un personnage le vise lui, pas la case (03/10 : cases en diagonale derrière un personnage difficiles à atteindre).
    Le centre de la case s'il est dégagé, sinon le bas, la gauche, la droite ou le haut du losange."""
    x, y = grid.center(cell)
    hw, hh = grid.half_w, grid.half_h

    def covered(px: float, py: float) -> bool:
        return any(f.cell != cell and abs(px - f.x) < SPRITE_HALF_W * hw
                   and f.y - SPRITE_UP * hh <= py <= f.y + SPRITE_DOWN * hh for f in fighters)

    for dx, dy in CELL_SPOTS:
        point = (x + dx * hw, y + dy * hh)
        if not covered(*point):
            return point, (grid.click_spread() if (dx, dy) == (0, 0) else (hw * 0.12, hh * 0.12))
    return (x, y), grid.click_spread()


def _is_arc(blob: Blob, layout: Layout) -> bool:
    """Morceau de cercle : le reste est caché par le corps d'un personnage debout devant (03/10 : un Bouftou juste
    derrière l'Enutrof, cercle coupé en deux arcs de 23 x 29 et 20 x 15 px ; aucun ennemi vu, tour passé en boucle)."""
    return blob.fill <= 0.45 and blob.area >= layout.area(40) and max(blob.w, blob.h) <= layout.length(85)


def find_fighters(frame: Frame) -> list[Fighter]:
    box = frame.layout.map_box
    region = frame.crop(box)
    fighters: list[Fighter] = []
    arcs: list[tuple[str, Blob, np.ndarray, np.ndarray]] = []
    for team in TEAMS:
        mask = team_mask(region, team)
        wide = cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8))
        # `close` recolle un cercle coupé par le sprite qui se tient dessus.
        for blob in find_blobs(mask, offset=box[:2], close=3):
            if _is_ring(blob, frame.layout):
                x, y = blob.center
                fighters.append(Fighter(team, x, y, frame.grid.cell_at(x, y)))
            elif _touching_rings(blob, frame.layout):
                fighters += _rings_in(frame, blob, team, mask, wide, RING_MIN_SCORE)
            elif _is_arc(blob, frame.layout):
                arcs.append((team, blob, mask, wide))
    # Morceaux de cercle, une fois les cercles entiers connus : une ellipse centrée sur une case, en partie visible.
    for team, blob, mask, wide in arcs:
        known = {f.cell for f in fighters}
        margin = frame.layout.length(RING_AXES[0])
        grown = Blob(round(blob.x - margin), round(blob.y - margin / 2), round(blob.w + 2 * margin),
                     round(blob.h + margin), blob.area)
        fighters += [f for f in _rings_in(frame, grown, team, mask, wide, ARC_MIN_SCORE) if f.cell not in known]
    return fighters


def _rings_in(frame: Frame, blob: Blob, team: str, mask: np.ndarray, wide: np.ndarray, min_score: float) -> list[Fighter]:
    """Cercles centrés sur les cases que couvre `blob` : ellipse colorée au moins à `min_score`, vide à l'intérieur."""
    box = frame.layout.map_box
    found = []
    for cell in frame.grid.cells():
        x, y = frame.grid.center(cell)
        rx, ry = x - box[0], y - box[1]
        if blob.x <= x <= blob.x + blob.w and blob.y <= y <= blob.y + blob.h \
                and ring_score(wide, rx, ry, frame.layout) >= min_score \
                and ring_score(mask, rx, ry, frame.layout, 0.7) <= HOLLOW_MAX:  # creux : pas une case pleine
            found.append(Fighter(team, x, y, cell))
    return found
