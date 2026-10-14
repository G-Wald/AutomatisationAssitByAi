"""Annotation des captures : ce que le bot a reconnu, dessiné par-dessus l'image."""

import unicodedata
from collections.abc import Iterable

import cv2
import numpy as np

from bot.perception.colors import RED
from bot.perception.fighters import Fighter
from bot.perception.frame import Frame
from bot.perception.grid import Cell
from bot.perception.layout import Box, Point

MAGENTA, YELLOW, GREEN, CYAN = (255, 0, 255), (0, 255, 255), (0, 200, 0), (255, 255, 0)


def _ascii(text: str) -> str:
    """Les polices d'OpenCV n'ont pas les accents."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def annotate(
    frame: Frame,
    *,
    fighters: Iterable[Fighter] = (),
    cells: Iterable[Cell] = (),
    marks: Iterable[tuple[Point, str]] = (),
    boxes: Iterable[Box] = (),
    text: str = "",
) -> np.ndarray:
    image = frame.image.copy()
    for cell in cells:
        x, y = frame.grid.center(cell)
        cv2.circle(image, (round(x), round(y)), 5, GREEN, -1)
    for fighter in fighters:
        center = (round(fighter.x), round(fighter.y))
        color = (0, 0, 255) if fighter.team == RED else (255, 0, 0)
        cv2.ellipse(image, center, (40, 22), 0, 0, 360, YELLOW, 2)
        cv2.putText(image, f"{fighter.team} {fighter.cell}", (center[0] - 45, center[1] + 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    for (x, y), label in marks:
        cv2.drawMarker(image, (round(x), round(y)), MAGENTA, cv2.MARKER_CROSS, 30, 3)
        cv2.putText(image, _ascii(label), (round(x) + 12, round(y) - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, MAGENTA, 2)
    for x0, y0, x1, y1 in boxes:
        cv2.rectangle(image, (x0, y0), (x1, y1), CYAN, 3)
    if text:
        text = _ascii(text)
        cv2.putText(image, text, (frame.layout.width // 3, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
        cv2.putText(image, text, (frame.layout.width // 3, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, YELLOW, 2)
    return image
