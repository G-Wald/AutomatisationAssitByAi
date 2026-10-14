"""Échange ouvert, objets posés (tools/echange.py, tools/echange_pile.py) : « Accepter » chez le suiveur puis chez le
principal, chacun dès que son bouton n'est plus grisé (orange vif) ; puis la fenêtre doit se fermer des deux côtés
(test du 11/10).

    .venv\\Scripts\\python -m tools.echange_valider --principal Gela-[A13] --suiveur Brawli-[Gal]
"""

import argparse
import sys
import time
from datetime import datetime

import cv2

from bot.actions.input import Human
from bot.actions.stop import StopControl
from bot.brain.game import Game
from bot.capture.window import enable_dpi_awareness, find_windows
from bot.config import ROOT
from bot.perception.colors import orange_mask
from tools.arriere_plan import confirm
from tools.banquier import best_text

ACTIVE = 0.15  # part d'orange vif autour du texte « Accepter » : actif (0,29 mesuré), grisé (0)
WAIT_ACTIVE = 8.0


def window(name):
    found = [w for w in find_windows("- Dofus Retro") if w.title.lower().startswith(name.lower())]
    return found[0] if found else None


def accept_button(frame):
    score, point, _ = best_text(frame, "Accepter", frame.layout.map_box, dark=False)
    if score < 0.7:
        return None, 0.0
    x, y = point
    box = frame.crop((x - 90, y - 14, x + 90, y + 14))
    return point, float(orange_mask(box).mean())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--suiveur", required=True)
    args = parser.parse_args()
    enable_dpi_awareness()
    windows = [(args.suiveur, window(args.suiveur)), (args.principal, window(args.principal))]
    if any(w is None for _, w in windows):
        print("Fenêtre du principal ou du suiveur introuvable", file=sys.stderr)
        return 1
    if not confirm(f"« Accepter » chez {args.suiveur} puis chez {args.principal}."):
        return 1
    folder = ROOT / "logs" / f"echange_valider_{datetime.now():%Y%m%d_%H%M%S}"
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

    games = [(name, Game(w, control, Recorder(), Human(), act=True, background=True)) for name, w in windows]
    try:
        for name, game in games:
            deadline = time.monotonic() + WAIT_ACTIVE
            point, share = None, 0.0
            while time.monotonic() < deadline:
                point, share = accept_button(game.look(park=False))
                if point is not None and share >= ACTIVE:
                    break
                time.sleep(0.4)
            note(f"{name} : « Accepter » {'actif' if share >= ACTIVE else 'grisé ou absent'} ({share:.2f}) en {point}")
            if point is None or share < ACTIVE:
                note("Arrêt : bouton pas actif")
                return 1
            game.click(point, (25, 4))
            time.sleep(1.0)
        time.sleep(1.0)
        for name, game in games:
            frame = game.look(park=False)
            Recorder().save(frame.image, f"apres_{name[:6]}")
            point, _ = accept_button(frame)
            note(f"{name} : fenêtre d'échange {'encore ouverte' if point else 'fermée'}")
    finally:
        control.close()
        (folder / "rapport.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
