"""Couleurs de l'interface et masques associés. Les images sont en BGR (convention OpenCV)."""

import numpy as np

RED, BLUE = "red", "blue"
TEAMS = (RED, BLUE)

RANGE_BLUE = (171, 109, 47)  # case à portée du sort sélectionné (carte en mode tactique)
UI_BEIGE = (170, 207, 213)  # fond des fenêtres, menus et panneaux


def _channels(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    image = image.astype(np.int16)
    return image[:, :, 0], image[:, :, 1], image[:, :, 2]


def near(image: np.ndarray, bgr: tuple[int, int, int], tolerance: int) -> np.ndarray:
    return np.abs(image.astype(np.int16) - np.array(bgr, np.int16)).max(axis=2) <= tolerance


def team_mask(image: np.ndarray, team: str) -> np.ndarray:
    """Rouge ou bleu pur : cercles au sol des combattants, cases de placement, barres de la timeline."""
    b, g, r = _channels(image)
    if team == RED:
        return (r >= 215) & (g <= 40) & (b <= 40)
    return (b >= 215) & (g <= 40) & (r <= 40)


def orange_mask(image: np.ndarray) -> np.ndarray:
    """Orange des boutons (Prêt, fin de tour), du cadre de la timeline et du timer de tour."""
    b, g, r = _channels(image)
    return (r >= 230) & (g >= 80) & (g <= 130) & (b <= 40)


def green_overlay_mask(image: np.ndarray) -> np.ndarray:
    """Vert de la zone de déplacement affichée au survol d'un combattant (carte en mode tactique)."""
    b, g, r = _channels(image)
    return (g >= r + 30) & (g >= b + 25) & (g >= 90)
