"""Trouver un texte connu à l'écran (nom de serveur, de personnage, d'ami), sans OCR ni capture de chaque nom.

Le jeu écrit ces textes en Verdana (gras pour les noms de serveur et de personnage). On dessine le texte cherché avec
la police de Windows, 4 fois plus grand puis réduit (lissage proche de celui du jeu), et on le cherche comme un modèle.
Mesures du 08/10 sur les captures de l'utilisateur (écran de 1918 x 1008, échelle 1) :
- nom de serveur (gras, 18 px) : « Allisteria » 0,96, les 11 autres serveurs essayés 0,61 au plus ;
- nom de personnage sur l'écran de choix (gras, 21,75 px) : 0,89 à 0,93 sur sa plaque, 0,72 au plus sur une autre
  (un nom court comme « Elnano » ressemble au début d'un plus long) ;
- nom d'ami dans la fenêtre « Amis » (normal, 18 px, texte foncé) : 0,85, les autres noms 0,54 au plus.
La taille compte au demi-pixel près (0,96 à 18 px, 0,77 à 17,5) : on essaie plusieurs tailles autour de la taille
attendue, mise à l'échelle de la fenêtre.
"""

from functools import lru_cache

import cv2
import numpy as np
import win32con
import win32gui
import win32ui

from bot.perception.frame import Frame
from bot.perception.layout import Box, Point

FONT = "Verdana"
SUPERSAMPLE = 4
SIZE_STEPS = (-0.5, -0.25, 0.0, 0.25, 0.5)  # tailles essayées autour de la taille attendue (px)
INK = 40  # sur 255 : pixels du texte dessiné (le reste est le fond)


@lru_cache(maxsize=256)
def render_text(text: str, size: float, bold: bool = False) -> np.ndarray:
    """Texte blanc sur noir (niveaux de gris), recadré au plus près ; `size` : hauteur des caractères en pixels."""
    screen_dc = win32gui.GetDC(0)
    screen = win32ui.CreateDCFromHandle(screen_dc)
    dc = screen.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        font = win32ui.CreateFont({"name": FONT, "height": -round(size * SUPERSAMPLE), "weight": 700 if bold else 400,
                                   "quality": win32con.NONANTIALIASED_QUALITY})  # lissé par la réduction, pas par Windows
        dc.SelectObject(font)
        width, height = dc.GetTextExtent(text)
        margin = 2 * SUPERSAMPLE
        bitmap.CreateCompatibleBitmap(screen, width + 2 * margin, height + 2 * margin)
        dc.SelectObject(bitmap)
        dc.FillSolidRect((0, 0, width + 2 * margin, height + 2 * margin), 0)
        dc.SetTextColor(0xFFFFFF)
        dc.SetBkMode(win32con.TRANSPARENT)
        dc.TextOut(margin, margin, text)
        pixels = np.frombuffer(bitmap.GetBitmapBits(True), np.uint8)
        big = pixels.reshape(height + 2 * margin, width + 2 * margin, 4)[:, :, 0].copy()
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        dc.DeleteDC()
        screen.DeleteDC()
        win32gui.ReleaseDC(0, screen_dc)
    small = cv2.resize(big, None, fx=1 / SUPERSAMPLE, fy=1 / SUPERSAMPLE, interpolation=cv2.INTER_AREA)
    ys, xs = np.nonzero(small > INK)
    if not len(xs):
        raise ValueError(f"texte vide : {text!r}")
    return small[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def text_score(image: np.ndarray, text: str, size: float, bold: bool = False, dark: bool = False) -> tuple[float, Point]:
    """Meilleure ressemblance du texte dans `image` (BGR ou gris) et centre de l'endroit, en essayant plusieurs tailles
    autour de `size`. `dark` : texte foncé sur fond clair (fenêtre « Amis »)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    if dark:
        gray = 255 - gray
    best, where = 0.0, (0, 0)
    for step in SIZE_STEPS:
        template = render_text(text, round((size + step) * 4) / 4, bold)
        th, tw = template.shape
        if gray.shape[0] < th or gray.shape[1] < tw:
            continue
        scores = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        if score > best:
            best, where = float(score), (x + tw // 2, y + th // 2)
    return best, where


def find_text(frame: Frame, text: str, box: Box, size: float, bold: bool = False, dark: bool = False,
              threshold: float = 0.8) -> tuple[Point, float] | None:
    """Centre et ressemblance du texte dans `box` (repère de la capture) ; `size` au repère de référence (mise à
    l'échelle de la fenêtre). None sous le seuil."""
    region = frame.crop(box)
    if not region.size:
        return None
    score, (x, y) = text_score(region, text, frame.layout.length(size), bold, dark)
    if score < threshold:
        return None
    return (box[0] + x, box[1] + y), score
