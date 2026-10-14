"""Coffre ouvert (tools/banquier.py) : onglet ressources de l'inventaire, bouton trier / transférer, « Transférer tous
les objets » (seul le contenu de l'onglet part, d'après l'utilisateur, 11/10). Le clic sur l'entrée du menu n'a lieu
que si son texte est reconnu (modèle découpé dans la capture de l'utilisateur) ; sinon arrêt sans autre geste.
Positions : fenêtre de 1920 x 1009 (captures du 11/10). Rapport et captures dans logs/coffre_transfert_<date>/.

    .venv\\Scripts\\python -m tools.coffre_transfert --personnage Gela-[A13]
"""

import argparse
import sys
import time
from datetime import datetime

import cv2
import numpy as np

from bot.actions.input import Human
from bot.actions.stop import StopControl
from bot.brain.game import Game
from bot.capture.window import enable_dpi_awareness, find_windows
from bot.config import ROOT
from tools.arriere_plan import confirm
from tools.banquier import best_text

RESOURCES_TAB = (1670, 220)  # 3e icône (bûche) du panneau de l'inventaire, à côté du coffre
SORT_BUTTON = (1875, 220)  # dernière icône (flèches) : trier / transférer
MENU_ZONE = (1450, 200, 1919, 700)
TRANSFER_ALL = ROOT / "bot" / "perception" / "assets" / "banque" / "transferer_tous.png"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--personnage", required=True)
    args = parser.parse_args()
    enable_dpi_awareness()
    found = [w for w in find_windows("- Dofus Retro") if w.title.lower().startswith(args.personnage.lower())]
    if not found:
        print(f"Aucune fenêtre du jeu pour {args.personnage}", file=sys.stderr)
        return 1
    if not confirm(f"Coffre de {args.personnage} : onglet ressources, trier / transférer, « Transférer tous les "
                   "objets »."):
        return 1
    folder = ROOT / "logs" / f"coffre_transfert_{datetime.now():%Y%m%d_%H%M%S}"
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
    try:
        frame = game.look(park=False)
        left, top, right, bottom = frame.layout.map_box
        title = best_text(frame, "Coffre", (left, top, right, bottom), dark=False, bold=True)
        note(f"Fenêtre « Coffre » : {title[0]:.2f}")
        if title[0] < 0.75:
            note("Coffre pas ouvert : arrêt (rien envoyé)")
            return 1
        game.click(RESOURCES_TAB, (5, 5))
        time.sleep(0.8)
        Recorder().save(game.look(park=False).image, "1_onglet_ressources")
        game.click(SORT_BUTTON, (5, 5))
        time.sleep(0.8)
        frame = game.look(park=False)
        Recorder().save(frame.image, "2_menu")
        template = cv2.imdecode(np.fromfile(str(TRANSFER_ALL), np.uint8), cv2.IMREAD_COLOR)
        x0, y0, x1, y1 = MENU_ZONE
        _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(frame.image[y0:y1, x0:x1], template,
                                                              cv2.TM_CCOEFF_NORMED))
        point = (x0 + x + template.shape[1] // 2, y0 + y + template.shape[0] // 2)
        note(f"« Transférer tous les objets » : {score:.2f} en {point}")
        if score < 0.85:
            note("Entrée pas reconnue : arrêt (le menu est peut-être resté ouvert)")
            return 1
        game.click(point, (20, 3))
        time.sleep(1.5)
        Recorder().save(game.look(park=False).image, "3_apres_transfert")
        note("Transfert demandé (voir la capture 3_apres_transfert)")
    finally:
        control.close()
        (folder / "rapport.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
