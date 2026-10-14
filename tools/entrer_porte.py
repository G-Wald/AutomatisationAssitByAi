"""Entrer dans un bâtiment par le soleil de sa porte (test du 11/10 : banque d'Astrub, carte 4,-16, case 23,9).

Pour chaque personnage nommé, en arrière-plan : clic sur la case du soleil, puis attente de l'intérieur (fond noir
autour de la pièce). Le nom de la carte ne change pas (mêmes coordonnées dedans et dehors) : c'est le décor qui dit si
on est entré. Rapport et captures dans logs/entrer_porte_<date>/.

    .venv\\Scripts\\python -m tools.entrer_porte --case 23,9 --personnages Gela-[A13],Brawli-[Gal]
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

ARRIVAL_TIMEOUT = 10.0  # secondes pour voir l'intérieur après le clic
INDOOR_DARK = 0.25  # part de noir dans la zone de jeu : intérieur d'un bâtiment (dehors : presque rien)
OUTDOOR_DARK = 0.05  # --sortir : dehors (0,2 % de noir mesuré devant la banque d'Astrub)


def dark_share(frame) -> float:
    left, top, right, bottom = frame.layout.map_box
    region = frame.image[top:bottom, left:right]
    return float((region.max(axis=2) <= 12).mean())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", required=True, help="case du soleil de la porte, i,j (ex. 23,9)")
    parser.add_argument("--personnages", required=True, help="noms séparés par des virgules")
    parser.add_argument("--sortir", action="store_true", help="sortir du bâtiment (soleil de la sortie, à l'intérieur)")
    args = parser.parse_args()
    i, j = (int(v) for v in args.case.split(","))
    names = [n.strip() for n in args.personnages.split(",") if n.strip()]
    enable_dpi_awareness()
    windows = {}
    for name in names:
        found = [w for w in find_windows("- Dofus Retro") if w.title.lower().startswith(name.lower())]
        if not found:
            print(f"Aucune fenêtre du jeu pour {name}", file=sys.stderr)
            return 1
        windows[name] = found[0]
    if not confirm(f"Clic sur le soleil de la case {i},{j} dans les fenêtres de {', '.join(names)} (arrière-plan)."):
        return 1
    folder = ROOT / "logs" / f"entrer_porte_{datetime.now():%Y%m%d_%H%M%S}"
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
    try:
        for name, window in windows.items():
            game = Game(window, control, Recorder(), Human(), act=True, background=True)
            before = game.look(park=False)
            start = dark_share(before)
            x, y = before.grid.center((i, j))
            game.click((x, y), before.grid.click_spread())
            deadline, inside, after = time.monotonic() + ARRIVAL_TIMEOUT, False, before
            while time.monotonic() < deadline:
                time.sleep(0.5)
                after = game.look(park=False)
                done = dark_share(after) < OUTDOOR_DARK if args.sortir else dark_share(after) >= INDOOR_DARK > start
                if done:
                    inside = True
                    break
            what = ("sorti", "pas sorti") if args.sortir else ("entré", "pas entré")
            Recorder().save(after.image, f"{name}_{what[0] if inside else what[1]}".replace(" ", "_"))
            lines.append(f"{name} : {what[0] if inside else what[1]} (noir {start:.0%} -> {dark_share(after):.0%})")
            print(lines[-1])
    finally:
        control.close()
        (folder / "rapport.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Rapport : {folder / 'rapport.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
