"""Ankama Launcher (captures de l'utilisateur des 08/10 et 11/10, écran 1080p à 100 %, launcher plein écran).

Page Dofus Retro : sélecteur de version « Retro » en haut du panneau du jeu, bouton blanc « JOUER » ; dans la barre de
gauche, l'icône de Dofus Retro (œuf jaune), en surbrillance quand sa page est affichée. Le bouton « JOUER » existe
aussi sur la page des autres jeux : on ne lance rien si le sélecteur « Retro » n'est pas là.
Le launcher n'est pas le jeu : sa page se met à l'échelle de Windows (DPI de sa fenêtre), pas de la hauteur de la
fenêtre ; les modèles sont donc agrandis selon le DPI.
"""

import cv2
import numpy as np

from bot.perception.layout import Point
from bot.perception.templates import load_template

PLAY, RETRO_VERSION, RETRO_ICON = "launcher/jouer.png", "launcher/retro.png", "launcher/icone_retro.png"
THRESHOLD = 0.8


def scaled_match(image: np.ndarray, name: str, scale: float = 1.0, threshold: float = THRESHOLD
                 ) -> tuple[Point, float] | None:
    """Centre et ressemblance du modèle `name`, agrandi de `scale` (DPI / 96), dans toute l'image."""
    template = load_template(name)
    if abs(scale - 1) > 0.02:
        template = cv2.resize(template, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    th, tw = template.shape[:2]
    if image.shape[0] < th or image.shape[1] < tw:
        return None
    scores = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(scores)
    if score < threshold:
        return None
    return (x + tw // 2, y + th // 2), float(score)


def find_play(image: np.ndarray, scale: float = 1.0) -> Point | None:
    """Bouton « JOUER » de la page Dofus Retro ; None ailleurs (page d'un autre jeu, launcher pas encore prêt)."""
    if scaled_match(image, RETRO_VERSION, scale) is None:
        return None
    found = scaled_match(image, PLAY, scale)
    return found[0] if found else None


def find_retro_icon(image: np.ndarray, scale: float = 1.0) -> Point | None:
    """Icône de Dofus Retro dans la barre des jeux, à gauche."""
    found = scaled_match(image, RETRO_ICON, scale, threshold=0.75)
    return found[0] if found else None
