"""Contrôle en jeu de la case d'un zaap de la référence complétée (24/10, demande de l'utilisateur).

    .venv/Scripts/python -m tools.verifier_zaap --perso Koalaloupour

Le personnage doit être sur la carte du zaap. L'outil lit la carte (nom de carte), prend la case du zaap dans la
référence (data/references, objets « zaap »), clique sur cette case et garde les captures d'avant et d'après : si la
case est la bonne, le personnage marche jusqu'au zaap et la fenêtre des destinations s'ouvre. Aucune destination
n'est choisie : la fenêtre est refermée par Échap. Arrière-plan : ni souris ni clavier de l'utilisateur.
"""

import argparse
import json
import sys
import time

from bot.actions.input import Human
from bot.actions.stop import StopControl
from bot.config import load_settings
from bot.connection.clients import client_windows, logged_in
from bot.debug.recorder import Recorder
from bot.perception.maplabel import read_map_label
from bot.team.member import Focus, MemberGame, TaggedRecorder
from tools.reference_complete import SORTIE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--perso", required=True)
    parser.add_argument("--attente", type=float, default=6.0, help="secondes de marche avant la capture")
    args = parser.parse_args()
    settings = load_settings()
    window = next((w for w in client_windows() if (logged_in(w) or "").lower() == args.perso.lower()), None)
    if window is None:
        print(f"Pas de fenêtre en jeu pour {args.perso}", file=sys.stderr)
        return 1
    control = StopControl("f12", "", corner=False).start()
    recorder = Recorder(max_images=20, images="toutes")
    game = MemberGame(window, control, TaggedRecorder(recorder, args.perso), Human.from_settings(settings), True,
                      Focus(control, 0.1, passive=True), True)
    try:
        before = game.look(park=False)
        label = read_map_label(before)
        if label is None or label.coords is None:
            print("Carte illisible", file=sys.stderr)
            return 1
        key = f"{label.coords[0]},{label.coords[1]}"
        reference = json.loads((SORTIE / "monde_des_douze.json").read_text(encoding="utf-8"))
        zaaps = [o for o in (reference.get(key) or {}).get("objets", []) if o.get("nature") == "zaap"]
        if not zaaps:
            print(f"Pas de zaap dans la référence en {key}", file=sys.stderr)
            return 1
        cell = tuple(zaaps[0]["case"])
        point = before.grid.center(cell)
        print(f"{key} : zaap de la référence en {cell}, point {point[0]:.0f},{point[1]:.0f}")
        print("avant :", recorder.save(before.image, "zaap_avant", lossless=True))
        game.click(point, (2, 2))
        time.sleep(args.attente)
        after = game.look(park=False)
        print("après :", recorder.save(after.image, "zaap_apres", lossless=True))
        return 0
    finally:
        control.close()


if __name__ == "__main__":
    sys.exit(main())
