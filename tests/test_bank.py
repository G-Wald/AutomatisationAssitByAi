"""Banque d'Astrub (11/10) : ce que le bot reconnaît à l'écran (outils de test tools/banquier.py...)."""

import cv2
import numpy as np

from bot.perception.frame import Frame
from conftest import FIXTURES
from tools.banquier import find_bankers


def counter(name: str) -> Frame:
    image = np.zeros((1009, 1920, 3), np.uint8)  # extrait (lignes 150-480, colonnes 1150-1750) d'une capture
    image[150:480, 1150:1750] = cv2.imread(str(FIXTURES / f"{name}.png"))
    return Frame.of(image)


def test_moving_bankers_found_behind_the_counter():
    # Les banquiers se déplacent (l'utilisateur, 11/10) : les deux hiboux, où qu'ils soient ; jamais les coffres.
    def near(found, expected):
        return all(any(abs(x - ex) <= 15 and abs(y - ey) <= 15 for x, y in found) for ex, ey in expected) \
            and len(found) == len(expected)

    assert near(find_bankers(counter("banque_comptoir_1")), [(1389, 292), (1576, 437)])
    assert near(find_bankers(counter("banque_comptoir_2")), [(1384, 338), (1480, 388)])
