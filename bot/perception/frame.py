"""Une capture de la zone de jeu avec son repère et sa grille."""

from dataclasses import dataclass, field
from functools import cached_property

import numpy as np

from bot.perception.daylight import Tint, floor_tint, to_daylight
from bot.perception.grid import Grid
from bot.perception.layout import Box, Layout


@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # BGR
    layout: Layout
    grid: Grid
    # Écoute du serveur de la fenêtre capturée (connection/network.py ClientFeed), posée par Game.look : la phase du
    # jeu en vient d'abord (state/scene.py, 31/10). None : capture sans écoute (tests, outils), l'écran seul.
    net: object = field(default=None, compare=False, repr=False)

    @classmethod
    def of(cls, image: np.ndarray) -> "Frame":
        layout = Layout.of(image)
        return cls(image, layout, Grid.for_layout(layout))

    def crop(self, box: Box) -> np.ndarray:
        x0, y0, x1, y1 = box
        return self.image[y0:y1, x0:x1]

    @cached_property
    def tint(self) -> Tint | None:
        """Teinte jour/nuit de la carte (perception/daylight.py), lue une fois par capture ; None de jour."""
        if self.image.ndim != 3 or self.image.shape[2] != 3:
            return None
        return floor_tint(self.crop(self.layout.map_box))

    @cached_property
    def day_image(self) -> np.ndarray:
        """L'image, la carte ramenée aux couleurs du jour (une fois par capture) : pour les couleurs de la carte
        (mode tactique, portée, zone de déplacement, cases, cercles). L'image elle-même de jour, sans copie.
        L'interface hors de la carte est intacte ; celle posée sur la carte (timeline, fenêtres) est corrigée avec
        elle : les lectures de l'interface gardent `image`."""
        tint = self.tint
        return self.image if tint is None else to_daylight(self.image, self.layout.map_box, tint)

    def day_crop(self, box: Box) -> np.ndarray:
        x0, y0, x1, y1 = box
        return self.day_image[y0:y1, x0:x1]
