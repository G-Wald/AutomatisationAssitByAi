"""Repère des captures de référence et passage à la taille réelle de la fenêtre.

Toutes les positions et tailles du code sont exprimées dans le repère des captures de référence
(zone client 1918 x 1008, chat à gauche). Le jeu dessine sa scène (carte + interface du bas) à
proportions fixes, mise à l'échelle sur la hauteur de la fenêtre et calée à droite ; le chat prend
la largeur restante. Une autre taille de fenêtre garde donc les mêmes repères, à l'échelle près.
"""

from dataclasses import dataclass

import numpy as np

REF_WIDTH, REF_HEIGHT = 1918, 1008
SCENE_LEFT = 572  # bord gauche de la scène (à gauche : le chat)
MAP_BOTTOM = 785  # bas de la carte (en dessous : l'interface)

Point = tuple[int, int]
Box = tuple[int, int, int, int]  # x0, y0, x1, y1 (x1 et y1 exclus)


@dataclass(frozen=True)
class Layout:
    width: int
    height: int

    @classmethod
    def of(cls, image: np.ndarray) -> "Layout":
        return cls(image.shape[1], image.shape[0])

    @property
    def scale(self) -> float:
        return self.height / REF_HEIGHT

    def x(self, x_ref: float) -> float:
        return self.width - (REF_WIDTH - x_ref) * self.scale

    def y(self, y_ref: float) -> float:
        return y_ref * self.scale

    def point(self, x_ref: float, y_ref: float) -> Point:
        return round(self.x(x_ref)), round(self.y(y_ref))

    def length(self, length_ref: float) -> float:
        return length_ref * self.scale

    def area(self, area_ref: float) -> float:
        return area_ref * self.scale**2

    def spread(self, rx_ref: float, ry_ref: float) -> tuple[float, float]:
        """Demi-étendue d'un clic aléatoire sur une cible (voir actions/input.py)."""
        return rx_ref * self.scale, ry_ref * self.scale

    def box(self, x0: float, y0: float, x1: float, y1: float) -> Box:
        return (
            max(0, round(self.x(x0))),
            max(0, round(self.y(y0))),
            min(self.width, round(self.x(x1))),
            min(self.height, round(self.y(y1))),
        )

    @property
    def map_box(self) -> Box:
        return self.box(SCENE_LEFT, 0, REF_WIDTH, MAP_BOTTOM)

    def on_map(self, point: tuple[float, float]) -> bool:
        x0, y0, x1, y1 = self.map_box
        return x0 <= point[0] < x1 and y0 <= point[1] < y1

    def matches_reference(self) -> bool:
        """Faux si la fenêtre n'a pas les proportions des captures de référence (disposition sans doute différente)."""
        return abs(self.width / self.height - REF_WIDTH / REF_HEIGHT) < 0.03
