"""Capture d'écran Windows (mss) limitée au rectangle de la zone de jeu.

La fenêtre doit être visible (pas recouverte) au moment de la capture :
on la passe au premier plan avant, ce qui est de toute façon nécessaire pour agir dessus.
"""

import mss
import numpy as np

from bot.capture.window import Rect


class ScreenCapture:
    def __init__(self) -> None:
        self._sct = mss.mss()

    def grab(self, rect: Rect) -> np.ndarray:
        """Image au format OpenCV (BGR)."""
        shot = self._sct.grab({"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height})
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3])
