"""Ouvrir son coffre à la banque (test du 11/10, banque d'Astrub) : clic droit sur le banquier, « Parler »,
« Consulter son coffre personnel » (402 kamas), fenêtre « Coffre ». Chaque étape est vérifiée à l'écran avant la
suivante ; au moindre doute, arrêt (Échap seulement si un menu est bien ouvert). Rapport et captures dans
logs/banquier_<date>/.

    .venv\\Scripts\\python -m tools.banquier --personnage Gela-[A13] --banquier 1386,333
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
from bot.perception.text import text_score
from tools.arriere_plan import confirm

SIZES = (14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24)
FOUND = 0.75


def best_text(frame, text, box, dark, bold=False):
    region = frame.crop(box)
    best = (0.0, (0, 0), 0)
    for size in SIZES:
        score, (x, y) = text_score(region, text, frame.layout.length(size), bold, dark)
        if score > best[0]:
            best = (score, (box[0] + x, box[1] + y), size)
    return best


PARLER = ROOT / "bot" / "perception" / "assets" / "banque" / "menu_parler.png"  # capture de l'utilisateur, 11/10
BANKER = ROOT / "bot" / "perception" / "assets" / "banque" / "banquier.png"  # le banquier se déplace derrière le comptoir
COUNTER = (1150, 150, 1700, 450)  # où le chercher (fenêtre de 1920 x 1009, capture du 11/10)


def find_bankers(frame) -> list[tuple[int, int]]:
    """Banquiers (hiboux dorés) derrière le comptoir, du plus visible au moins visible : les plus grandes taches de leur
    couleur (1 780 à 2 300 px, ~55 x 70, sur 4 captures du 11/10 ; les morceaux de coffre : 320 au plus)."""
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = ((h >= 12) & (h <= 24) & (s >= 90) & (s <= 200) & (v >= 110) & (v <= 215)).astype(np.uint8)
    x0, y0, x1, y1 = COUNTER
    zone = cv2.morphologyEx(mask[y0:y1, x0:x1], cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, _, stats, centers = cv2.connectedComponentsWithStats(zone)
    found = [(int(stats[k, 4]), (int(centers[k, 0]) + x0, int(centers[k, 1]) + y0)) for k in range(1, count)
             if stats[k, 4] >= 1000 and stats[k, 3] >= 45]
    return [center for _, center in sorted(found, reverse=True)]


def find_parler(frame, box):
    """(ressemblance, centre, 0) de l'entrée « Parler » du menu gris d'un PNJ (police du menu, pas Verdana)."""
    template = cv2.imdecode(np.fromfile(str(PARLER), np.uint8), cv2.IMREAD_COLOR)
    x0, y0, x1, y1 = box
    region = frame.image[y0:y1, x0:x1]
    if region.shape[0] < template.shape[0] or region.shape[1] < template.shape[1]:
        return 0.0, (0, 0), 0
    _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))
    return float(score), (x0 + x + template.shape[1] // 2, y0 + y + template.shape[0] // 2), 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--personnage", required=True)
    parser.add_argument("--banquier", help="x,y du banquier dans la fenêtre, si on le connaît (sinon cherché)")
    args = parser.parse_args()
    enable_dpi_awareness()
    found = [w for w in find_windows("- Dofus Retro") if w.title.lower().startswith(args.personnage.lower())]
    if not found:
        print(f"Aucune fenêtre du jeu pour {args.personnage}", file=sys.stderr)
        return 1
    if not confirm(f"Banquier de {args.personnage} : clic gauche sur le banquier, « Parler », « Consulter son coffre "
                   "personnel » (402 kamas)."):
        return 1
    folder = ROOT / "logs" / f"banquier_{datetime.now():%Y%m%d_%H%M%S}"
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
        bankers = [tuple(int(v) for v in args.banquier.split(","))] if args.banquier else find_bankers(frame)
        note(f"Banquiers vus : {bankers}")
        speak = (0.0, (0, 0), 0)
        for bx, by in bankers[:2]:
            game.click((bx, by - 8), (3, 3))  # clic GAUCHE sur son corps (le clic droit ouvre le menu de Flash)
            time.sleep(0.8)
            frame = game.look(park=False)
            Recorder().save(frame.image, "1_menu")
            # Menu gris du PNJ (pas le beige des joueurs) : « Parler » cherché autour du banquier.
            box = (max(0, bx - 300), max(0, by - 200), min(frame.layout.width, bx + 400),
                   min(frame.layout.height, by + 250))
            speak = find_parler(frame, box)
            note(f"Banquier en {bx},{by} : « Parler » {speak[0]:.2f} en {speak[1]}")
            if speak[0] >= FOUND:
                break
        if speak[0] < FOUND:
            note("« Parler » pas trouvé : arrêt (rien d'autre envoyé)")
            return 1
        game.click(speak[1], (6, 3))
        time.sleep(1.5)
        frame = game.look(park=False)
        Recorder().save(frame.image, "2_dialogue")
        left, top, right, bottom = frame.layout.map_box
        chest = best_text(frame, "Consulter son coffre personnel", (left, top, right, bottom), dark=True)
        note(f"Dialogue : « Consulter son coffre personnel » {chest[0]:.2f} (taille {chest[2]}) en {chest[1]}")
        if chest[0] < FOUND:
            note("Option du coffre pas trouvée : arrêt (dialogue laissé ouvert)")
            return 1
        game.click(chest[1], (20, 3))
        time.sleep(2.0)
        frame = game.look(park=False)
        Recorder().save(frame.image, "3_coffre")
        title = best_text(frame, "Coffre", (left, top, right, bottom), dark=False, bold=True)
        note(f"Fenêtre « Coffre » : {title[0]:.2f} (taille {title[2]}) en {title[1]}")
        note("Coffre ouvert" if title[0] >= FOUND else "Coffre pas vu : voir la capture 3_coffre")
    finally:
        control.close()
        (folder / "rapport.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
