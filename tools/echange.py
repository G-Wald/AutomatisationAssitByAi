"""Ouvrir un échange entre le principal et un suiveur (test du 11/10, banque d'Astrub) : chez le principal, clic gauche
sur le portrait du suiveur dans le cadre du groupe, « Echange », fenêtre « En attente de la réponse de <suiveur> » ;
chez le suiveur, « <principal> te propose de faire un échange. Acceptes-tu ? » puis « Oui » ; fenêtre d'échange
ouverte des deux côtés (laissée ouverte). Chaque étape vérifiée à l'écran ; au moindre doute, arrêt.

    .venv\\Scripts\\python -m tools.echange --principal Gela-[A13] --suiveur Brawli-[Gal] --portrait 1890,169
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

ECHANGE = ROOT / "bot" / "perception" / "assets" / "banque" / "menu_echange.png"  # capture de l'utilisateur, 11/10
MENU_ZONE = (1300, 60, 1919, 1000)
FOUND = 0.75


def window(name):
    found = [w for w in find_windows("- Dofus Retro") if w.title.lower().startswith(name.lower())]
    return found[0] if found else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--suiveur", required=True)
    parser.add_argument("--portrait", required=True, help="x,y du portrait du suiveur dans la fenêtre du principal")
    args = parser.parse_args()
    px, py = (int(v) for v in args.portrait.split(","))
    enable_dpi_awareness()
    lead_window, follow_window = window(args.principal), window(args.suiveur)
    if lead_window is None or follow_window is None:
        print("Fenêtre du principal ou du suiveur introuvable", file=sys.stderr)
        return 1
    if not confirm(f"Échange {args.principal} <- {args.suiveur} : clic gauche sur le portrait en {px},{py}, « Echange », "
                   "« Oui » chez le suiveur."):
        return 1
    folder = ROOT / "logs" / f"echange_{datetime.now():%Y%m%d_%H%M%S}"
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

    lead = Game(lead_window, control, Recorder(), Human(), act=True, background=True)
    follow = Game(follow_window, control, Recorder(), Human(), act=True, background=True)
    try:
        lead.click((px, py), (3, 3))  # clic GAUCHE : un clic droit envoyé ouvre le menu de Flash (11/10)
        time.sleep(0.8)
        frame = lead.look(park=False)
        Recorder().save(frame.image, "1_menu_portrait")
        template = cv2.imdecode(np.fromfile(str(ECHANGE), np.uint8), cv2.IMREAD_COLOR)
        x0, y0, x1, y1 = MENU_ZONE
        _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(frame.image[y0:y1, x0:x1], template,
                                                              cv2.TM_CCOEFF_NORMED))
        point = (x0 + x + template.shape[1] // 2, y0 + y + template.shape[0] // 2)
        note(f"Menu du portrait : « Echange » {score:.2f} en {point}")
        if score < 0.85:
            note("« Echange » pas trouvé : arrêt (le menu est peut-être resté ouvert)")
            return 1
        lead.click(point, (10, 3))
        time.sleep(1.2)
        frame = lead.look(park=False)
        Recorder().save(frame.image, "2_attente")
        box = frame.layout.map_box
        waiting = best_text(frame, "En attente de la réponse de", box, dark=True)
        name = best_text(frame, args.suiveur, box, dark=True)
        note(f"Chez le principal : « En attente de la réponse de » {waiting[0]:.2f}, « {args.suiveur} » {name[0]:.2f}")
        if waiting[0] < FOUND or name[0] < FOUND:
            cancel = best_text(frame, "Annuler", box, dark=False)
            note(f"Pas le bon personnage ou pas de demande : « Annuler » {cancel[0]:.2f}")
            if cancel[0] >= FOUND:
                lead.click(cancel[1], (10, 3))
            return 1
        frame = follow.look(park=False)
        Recorder().save(frame.image, "3_proposition")
        box = frame.layout.map_box
        offer = best_text(frame, "te propose de faire un échange", box, dark=True)
        who = best_text(frame, args.principal, box, dark=True)
        yes = best_text(frame, "Oui", box, dark=False)
        note(f"Chez le suiveur : proposition {offer[0]:.2f}, « {args.principal} » {who[0]:.2f}, « Oui » {yes[0]:.2f} "
             f"en {yes[1]}")
        if offer[0] < FOUND or who[0] < FOUND or yes[0] < FOUND:
            note("Proposition pas reconnue chez le suiveur : arrêt (demande laissée en attente)")
            return 1
        follow.click(yes[1], (8, 3))
        time.sleep(1.5)
        for game, label in ((follow, "suiveur"), (lead, "principal")):
            frame = game.look(park=False)
            Recorder().save(frame.image, f"4_echange_{label}")
            accept = best_text(frame, "Accepter", frame.layout.map_box, dark=False)
            note(f"Fenêtre d'échange chez le {label} : « Accepter » {accept[0]:.2f} en {accept[1]}")
    finally:
        control.close()
        (folder / "rapport.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
