"""Enregistre une capture de la zone de jeu, exactement ce que le bot verra.

Depuis la racine du projet :
    .venv\\Scripts\\python -m tools.capture --list
    .venv\\Scripts\\python -m tools.capture --name combat
    .venv\\Scripts\\python -m tools.capture --name combat --fixture
    .venv\\Scripts\\python -m tools.capture --name groupes_z --delay 5    5 s pour tenir Z ou survoler un groupe
"""

import argparse
import sys
import time
from datetime import datetime

import cv2

from bot.capture.screen import ScreenCapture
from bot.capture.window import find_windows, list_windows
from bot.config import ROOT, load_settings

CAPTURES_DIR = ROOT / "captures"
FIXTURES_DIR = ROOT / "tests" / "fixtures"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="liste les fenêtres visibles")
    parser.add_argument("--title", help="partie du titre de la fenêtre (défaut : config/settings.yaml)")
    parser.add_argument("--name", default="capture", help="nom du fichier")
    parser.add_argument("--fixture", action="store_true", help="enregistre dans tests/fixtures/ (capture de référence)")
    parser.add_argument("--no-focus", action="store_true", help="ne pas passer la fenêtre au premier plan")
    parser.add_argument("--delay", type=int, default=0, help="secondes d'attente avant la capture, pour préparer l'écran")
    args = parser.parse_args()

    if args.list:
        for w in list_windows():
            r = w.client_rect()
            print(f"{w.hwnd:>10}  {r.width:>5}x{r.height:<5}  {w.title}")
        return 0

    title = args.title or load_settings()["window"]["title"]
    if not title:
        print("Titre de fenêtre inconnu : utilise --title ou renseigne window.title dans config/settings.yaml")
        return 1
    matches = find_windows(title)
    if not matches:
        print(f"Aucune fenêtre ne contient « {title} ». Lance --list pour voir les titres.")
        return 1
    if len(matches) > 1:
        print(f"{len(matches)} fenêtres correspondent, j'utilise la première :")
        for w in matches:
            print(f"  {w.hwnd}  {w.title}")
    window = matches[0]

    if not args.no_focus:
        window.bring_to_front()
        time.sleep(0.3)  # laisse Windows redessiner la fenêtre
    for remaining in range(args.delay, 0, -1):
        print(f"Capture dans {remaining} s...")
        time.sleep(1)

    rect = window.client_rect()
    if rect.width == 0 or rect.height == 0:
        print("La fenêtre est réduite : impossible de la capturer.")
        return 1

    image = ScreenCapture().grab(rect)
    if args.fixture:
        path = FIXTURES_DIR / f"{args.name}.png"
    else:
        path = CAPTURES_DIR / f"{args.name}_{datetime.now():%Y%m%d_%H%M%S}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)
    print(f"{path.relative_to(ROOT)}  ({image.shape[1]}x{image.shape[0]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
