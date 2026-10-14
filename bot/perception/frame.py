"""Une capture de la zone de jeu avec son repère et sa grille."""

from dataclasses import dataclass

import numpy as np

from bot.perception.grid import Grid
from bot.perception.layout import Box, Layout


@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # BGR
    layout: Layout
    grid: Grid

    @classmethod
    def of(cls, image: np.ndarray) -> "Frame":
        layout = Layout.of(image)
        return cls(image, layout, Grid.for_layout(layout))

    def crop(self, box: Box) -> np.ndarray:
        x0, y0, x1, y1 = box
        return self.image[y0:y1, x0:x1]
