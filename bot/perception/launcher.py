"""Ankama Launcher (captures de l'utilisateur des 08/10 et 11/10, écran 1080p à 100 %, launcher plein écran).

Page Dofus Retro : sélecteur de version « Retro » en haut du panneau du jeu, bouton blanc « JOUER » ; dans la barre de
gauche, l'icône de Dofus Retro (œuf jaune), en surbrillance quand sa page est affichée. Le bouton « JOUER » existe
aussi sur la page des autres jeux : on ne lance rien si le sélecteur « Retro » n'est pas là.
Le launcher n'est pas le jeu : sa page se met à l'échelle de Windows (DPI de sa fenêtre), pas de la hauteur de la
fenêtre ; les modèles sont donc agrandis selon le DPI. Ce DPI peut être celui d'un autre écran que celui où le launcher
est affiché (16/10 : modèles agrandis pour un écran à 175 %, launcher sur l'écran à 100 %, « JOUER » introuvable) :
l'échelle 1 est toujours essayée aussi.
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


def _scales(scale: float) -> list[float]:
    return list(dict.fromkeys((scale, 1.0)))


def find_play(image: np.ndarray, scale: float = 1.0) -> Point | None:
    """Bouton « JOUER » de la page Dofus Retro ; None ailleurs (page d'un autre jeu, launcher pas encore prêt). La
    page est reconnue au sélecteur « Retro », ou (21/10) à l'icône de Dofus Retro en surbrillance dans la barre de
    gauche : page défilée vers le haut, sélecteur coupé (0,59), « JOUER » pourtant là (0,996) ; le bot s'était arrêté."""
    for factor in _scales(scale):
        if scaled_match(image, RETRO_VERSION, factor) is not None or _retro_selected(image, factor):
            found = scaled_match(image, PLAY, factor)
            if found:
                return found[0]
    return None


SELECTED_SLOT = (105, 95, 51)  # BGR, fond de la case du jeu affiché dans la barre de gauche (les autres : 67, 55, 0)
SLOT_EDGE_X = 8  # px (échelle 1) : bord gauche de la barre, hors de l'icône


def _retro_selected(image: np.ndarray, scale: float) -> bool:
    """L'icône de Dofus Retro est celle du jeu affiché : sa case de la barre de gauche est éclaircie."""
    found = scaled_match(image, RETRO_ICON, scale, threshold=0.75)
    if found is None:
        return False
    x, y = round(SLOT_EDGE_X * scale), found[0][1]
    pixel = image[y, x].astype(int)
    return int(np.abs(pixel - np.array(SELECTED_SLOT)).max()) <= 20


def find_retro_icon(image: np.ndarray, scale: float = 1.0) -> Point | None:
    """Icône de Dofus Retro dans la barre des jeux, à gauche."""
    for factor in _scales(scale):
        found = scaled_match(image, RETRO_ICON, factor, threshold=0.75)
        if found:
            return found[0]
    return None
