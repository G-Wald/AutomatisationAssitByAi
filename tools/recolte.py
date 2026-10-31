"""Essai de récolte (27/10) : un personnage récolte les ressources de sa carte que son métier permet (bot/brain/harvest.py).
L'outil du métier doit être équipé. Arrière-plan ; F12 arrête.

    .venv\\Scripts\\python -m tools.recolte --personnage Elseiso --metier Bûcheron --niveau 23 --carte -1,-16
    .venv\\Scripts\\python -m tools.recolte --personnage Enu-six --metier Paysan --niveau 1 --carte 2,-16 --outil Faux --arme Marteau

--outil : type de l'outil à équiper d'abord (Faux, Hache, Pioche...) ; --arme : type de l'arme de combat remise après.
--carte : coordonnées de la carte où il se trouve (son numéro vient de la référence ; la clé, de data/cles_cartes.json,
apprise d'un passage précédent). Écoute du serveur obligatoire (Npcap) : état des ressources et fin des récoltes.
"""

import argparse
import json
import logging
import sys
import time

import cv2

from bot.actions.input import Human
from bot.actions.stop import StopControl
from bot.brain.equipment import equip
from bot.brain.game import Game
from bot.brain.harvest import Harvester
from bot.brain.popups import PopupCloser
from bot.capture.window import enable_dpi_awareness, find_windows
from bot.config import ROOT
from bot.connection.network import shared_listener
from bot.perception.popups import load_popup_specs
from tools.arriere_plan import confirm


def map_id_of(coords: str) -> int | None:
    reference = json.loads((ROOT / "data" / "references" / "monde_des_douze.json").read_text(encoding="utf-8"))
    ids = (reference.get(coords.replace(" ", "")) or {}).get("ids") or []
    return ids[0] if ids else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--personnage", required=True)
    parser.add_argument("--metier", required=True, help="Bûcheron, Paysan, Mineur, Alchimiste, Pêcheur")
    parser.add_argument("--niveau", type=int, required=True, help="niveau du métier : ressources de ce niveau au plus")
    parser.add_argument("--carte", required=True, help="x,y de la carte où se trouve le personnage")
    parser.add_argument("--max", type=int, default=10)
    parser.add_argument("--outil", help="type de l'outil à équiper avant (Faux, Hache, Pioche...)")
    parser.add_argument("--arme", help="type de l'arme de combat à remettre après (Marteau, Dague...)")
    parser.add_argument("--debug", action="store_true", help="journal détaillé (messages du serveur)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if "--debug" in sys.argv else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    enable_dpi_awareness()
    map_id = map_id_of(args.carte)
    found = [w for w in find_windows("- Dofus Retro") if w.title.lower().startswith(args.personnage.lower())]
    if map_id is None or not found:
        print(f"Carte {args.carte} inconnue de la référence, ou pas de fenêtre pour {args.personnage}", file=sys.stderr)
        return 1
    listener = shared_listener()
    if listener is None:
        print("Écoute du serveur impossible (Npcap) : pas de récolte", file=sys.stderr)
        return 1
    if not confirm(f"{args.personnage} récolte (métier {args.metier} {args.niveau}) en {args.carte} (carte {map_id}), "
                   f"{args.max} ressources au plus."):
        return 1
    control = StopControl("f12", "", corner=False).start()

    folder = ROOT / "logs" / f"recolte_{time.strftime('%Y%m%d_%H%M%S')}"

    class Recorder:
        dir = folder
        count = 0

        def save(self, image, label, lossless=False, quality=80):
            folder.mkdir(parents=True, exist_ok=True)
            Recorder.count += 1
            path = folder / f"{Recorder.count:03d}_{label}.png"
            cv2.imencode(".png", image)[1].tofile(str(path))
            return path

        def keeps(self, lossless=False):
            return True

    game = Game(found[0], control, Recorder(), Human(), act=True, background=True)
    game.net = listener.follow(found[0].hwnd, args.personnage)
    if args.outil and not equip(game, args.outil):
        return 1
    popups = PopupCloser(game, load_popup_specs(), unknown_key="", unknown_delay=20.0)
    harvester = Harvester(game, lambda r: r.job == args.metier and r.level <= args.niveau, popups=popups)
    try:
        done = harvester.run(map_id, args.max)
    finally:
        if args.arme:
            equip(game, args.arme)
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
