"""Recherche d'un modèle (petite image découpée dans une capture de référence) dans une capture."""

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from bot.perception.frame import Frame
from bot.perception.layout import Box, Point

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


@lru_cache
def load_template(name: str) -> np.ndarray:
    path = ASSETS_DIR / name
    # imdecode plutôt qu'imread : imread ne sait pas ouvrir un chemin avec accents sous Windows.
    image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR) if path.is_file() else None
    if image is None:
        raise FileNotFoundError(f"Modèle introuvable : {path}")
    return image


def match_template(frame: Frame, name: str, box: Box | None = None, threshold: float = 0.85) -> tuple[Point, float] | None:
    """Centre et score de la meilleure correspondance, ou None sous le seuil."""
    template = load_template(name)
    scale = frame.layout.scale
    if abs(scale - 1) > 0.02:
        template = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    box = box or (0, 0, frame.layout.width, frame.layout.height)
    region = frame.crop(box)
    th, tw = template.shape[:2]
    if region.shape[0] < th or region.shape[1] < tw:
        return None
    scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(scores)
    if score < threshold:
        return None
    return (box[0] + x + tw // 2, box[1] + y + th // 2), float(score)


def template_spread(frame: Frame, name: str, part: float = 0.3) -> tuple[float, float]:
    """Demi-étendue d'un clic aléatoire dans le bouton du modèle `name` : une part de sa demi-taille."""
    height, width = load_template(name).shape[:2]
    return frame.layout.spread(width * part, height * part)
