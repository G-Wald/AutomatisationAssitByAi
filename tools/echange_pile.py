"""Échange ouvert (tools/echange.py), chez le suiveur : onglet ressources, une pile glissée dans son offre, fenêtre de
quantité « max » puis la coche (test du 11/10). Glisser par messages (bouton enfoncé, déplacements, relâché), comme la
poignée de la liste des sorts. Les boutons de la fenêtre de quantité ne sont cliqués que s'ils sont reconnus (modèles
découpés dans la capture de l'utilisateur). Positions : fenêtre de 1920 x 1009.

    .venv\\Scripts\\python -m tools.echange_pile --suiveur Brawli-[Gal]
"""

import argparse
import sys
import time
from datetime import datetime

import cv2
import numpy as np
import win32api
import win32con
import win32gui

from bot.actions.background import BackgroundInput
from bot.actions.input import Human
from bot.actions.stop import StopControl
from bot.brain.game import Game
from bot.capture.window import enable_dpi_awareness, find_windows
from bot.config import ROOT
from tools.arriere_plan import confirm

ASSETS = ROOT / "bot" / "perception" / "assets" / "banque"
RESOURCES_TAB = (1424, 184)  # 3e icône (bûche) de l'inventaire, dans la fenêtre d'échange
FIRST_CELL = (1357, 288)  # 1re case de l'inventaire
OFFER = (1593, 620)  # milieu de mon offre (en bas à droite)
OFFER_BOX = (1340, 540, 1880, 700)


def pack(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def find(frame, name: str) -> tuple[float, tuple[int, int]]:
    template = cv2.imdecode(np.fromfile(str(ASSETS / name), np.uint8), cv2.IMREAD_COLOR)
    _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(frame.image, template, cv2.TM_CCOEFF_NORMED))
    return float(score), (x + template.shape[1] // 2, y + template.shape[0] // 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suiveur", required=True)
    parser.add_argument("--premier-plan", action="store_true",
                        help="fenêtre passée devant pendant le glisser (la souris reste à l'utilisateur)")
    args = parser.parse_args()
    enable_dpi_awareness()
    found = [w for w in find_windows("- Dofus Retro") if w.title.lower().startswith(args.suiveur.lower())]
    if not found:
        print(f"Aucune fenêtre du jeu pour {args.suiveur}", file=sys.stderr)
        return 1
    if not confirm(f"Échange chez {args.suiveur} : onglet ressources, 1re pile glissée dans l'offre, « max », coche."):
        return 1
    folder = ROOT / "logs" / f"echange_pile_{datetime.now():%Y%m%d_%H%M%S}"
    folder.mkdir(parents=True, exist_ok=True)
    control = StopControl("f12", "", corner=False).start()

    class Recorder:
        dir = folder

        def save(self, image, label, lossless=False, quality=80):
            path = folder / f"{label}.png"
            cv2.imencode(".png", image)[1].tofile(str(path))
            return path

        def keeps(self, lossless=False):
            return True

    lines = []

    def note(text):
        lines.append(text)
        print(text)

    game = Game(found[0], control, Recorder(), Human(), act=True, background=True)
    inner = BackgroundInput(game.window, control).target()

    def client(point) -> int:
        screen = win32gui.ClientToScreen(game.window.hwnd, (round(point[0]), round(point[1])))
        return pack(*win32gui.ScreenToClient(inner, screen))

    def drag(start, end) -> None:
        win32api.PostMessage(inner, win32con.WM_MOUSEMOVE, 0, client(start))
        time.sleep(0.05)
        win32api.PostMessage(inner, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, client(start))
        for k in range(1, 13):
            time.sleep(0.04)
            point = (start[0] + (end[0] - start[0]) * k / 12, start[1] + (end[1] - start[1]) * k / 12)
            win32api.PostMessage(inner, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, client(point))
        time.sleep(0.08)
        win32api.PostMessage(inner, win32con.WM_LBUTTONUP, 0, client(end))

    try:
        game.click(RESOURCES_TAB, (4, 4))
        time.sleep(0.8)
        before = game.look(park=False)
        Recorder().save(before.image, "1_onglet_ressources")
        x0, y0, x1, y1 = OFFER_BOX
        if args.premier_plan:
            game.window.bring_to_front()  # 11/10 : en arrière-plan, le relâchement n'est pas pris
            time.sleep(0.4)
        drag(FIRST_CELL, OFFER)
        time.sleep(1.0)
        frame = game.look(park=False)
        Recorder().save(frame.image, "2_quantite")
        score, point = find(frame, "quantite_max.png")
        note(f"Fenêtre de quantité : « max » {score:.2f} en {point}")
        if score < 0.8:
            note("« max » pas trouvé : arrêt")
            return 1
        game.click(point, (4, 3))
        time.sleep(0.5)
        frame = game.look(park=False)
        score, point = find(frame, "quantite_valider.png")
        note(f"Coche : {score:.2f} en {point}")
        if score < 0.8:
            note("Coche pas trouvée : arrêt")
            return 1
        game.click(point, (3, 3))
        time.sleep(1.2)
        after = game.look(park=False)
        Recorder().save(after.image, "3_offre")
        change = float(np.abs(after.image[y0:y1, x0:x1].astype(int) - before.image[y0:y1, x0:x1].astype(int)).mean())
        note(f"Offre : changement {change:.1f} (0 : rien de posé)")
    finally:
        control.close()
        (folder / "rapport.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
