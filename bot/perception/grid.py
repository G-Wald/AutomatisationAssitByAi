"""Grille isométrique des cases.

Les centres des cases forment un réseau (x0 + i * demi-largeur, y0 + j * demi-hauteur) avec i + j pair,
identique sur toutes les cartes : une case fait 53 x 27 unités du jeu, soit 96,3 x 49,1 px en référence.
Deux cases voisines diffèrent de 1 en i et de 1 en j ; la distance du jeu (PM, portée) vaut max(|di|, |dj|).
"""

import math
from dataclasses import dataclass

from bot.perception.layout import MAP_BOTTOM, REF_WIDTH, SCENE_LEFT, Layout

Cell = tuple[int, int]

# Mesuré sur les cases de placement de tests/fixtures/preparation.png (écart max 0,15 px).
X0_REF, Y0_REF = 570.56, -0.35
HALF_W_REF, HALF_H_REF = 48.143, 24.528


def distance(a: Cell, b: Cell) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def lattice_cell(a: float, b: float) -> Cell:
    """Case (losange) qui contient le point de coordonnées fractionnaires (a, b) du réseau."""
    i0, j0 = math.floor(a), math.floor(b)
    candidates = [(i, j) for i in (i0, i0 + 1) for j in (j0, j0 + 1) if (i + j) % 2 == 0]
    return min(candidates, key=lambda c: abs(a - c[0]) + abs(b - c[1]))


@dataclass(frozen=True)
class Grid:
    x0: float
    y0: float
    half_w: float
    half_h: float
    bounds: tuple[float, float, float, float]  # zone de la carte : x0, y0, x1, y1

    @classmethod
    def for_layout(cls, layout: Layout) -> "Grid":
        return cls(
            x0=layout.x(X0_REF),
            y0=layout.y(Y0_REF),
            half_w=layout.length(HALF_W_REF),
            half_h=layout.length(HALF_H_REF),
            bounds=(layout.x(SCENE_LEFT), 0.0, layout.x(REF_WIDTH), layout.y(MAP_BOTTOM)),
        )

    def click_spread(self) -> tuple[float, float]:
        """Demi-étendue d'un clic aléatoire sur une case : reste bien à l'intérieur du losange."""
        return self.half_w * 0.35, self.half_h * 0.35

    def center(self, cell: Cell) -> tuple[float, float]:
        i, j = cell
        return self.x0 + i * self.half_w, self.y0 + j * self.half_h

    def cell_at(self, x: float, y: float) -> Cell:
        """Case (losange) qui contient le point."""
        return lattice_cell((x - self.x0) / self.half_w, (y - self.y0) / self.half_h)

    def cells(self) -> list[Cell]:
        """Cases dont le centre est sur la carte."""
        left, top, right, bottom = self.bounds
        i_range = range(math.ceil((left - self.x0) / self.half_w), math.floor((right - self.x0) / self.half_w) + 1)
        j_range = range(math.ceil((top - self.y0) / self.half_h), math.floor((bottom - self.y0) / self.half_h) + 1)
        return [(i, j) for j in j_range for i in i_range if (i + j) % 2 == 0]
