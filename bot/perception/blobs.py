"""Taches de couleur (composantes connexes d'un masque)."""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Blob:
    x: int
    y: int
    w: int
    h: int
    area: int

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    @property
    def fill(self) -> float:
        return self.area / (self.w * self.h)


def find_blobs(mask: np.ndarray, offset: tuple[int, int] = (0, 0), close: int = 0) -> list[Blob]:
    """`offset` : position du masque dans l'image ; `close` : bouche les trous de moins de `close` pixels."""
    mask = mask.astype(np.uint8)
    if close:
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((close, close), np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    ox, oy = offset
    return [Blob(int(x) + ox, int(y) + oy, int(w), int(h), int(a)) for x, y, w, h, a in stats[1:]]
