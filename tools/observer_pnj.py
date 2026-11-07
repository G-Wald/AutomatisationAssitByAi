"""Observation d'une entrée de donjon (05/11, donjon des Champs) : un personnage passe une porte, puis ouvre la bulle d'un
PNJ et la referme SANS répondre. Tout vient du serveur : la carte d'arrivée (GDM), les acteurs de la carte (GM, dont le
PNJ par son modèle), la question et les réponses proposées (DQ), traduites par la langue du client (table D).

Rien n'est consommé (ni clé ni trousseau) : la bulle est fermée par sa croix. Arrière-plan ; F12 arrête.

Bulle à plusieurs étapes (07/11, donjon Ensablé : Igloute, 8 « Continuer la discussion » avant la clé) : `--suivre`
clique cette ligne, et seulement elle, tant qu'elle est proposée, chaque question et ses réponses au journal ; la bulle
est refermée par sa croix ensuite, rien donné (--pnj-modele 798 --suivre "Continuer la discussion").

    .venv\\Scripts\\python -m tools.observer_pnj --personnage Fourboun --porte 18,12 --pnj-modele 780
"""

import argparse
import logging
import sys
import time

import cv2

from bot.actions.input import Human
from bot.actions.stop import StopControl
from bot.brain.arena import map_cell
from bot.brain.game import Game
from bot.capture.window import enable_dpi_awareness, find_windows
from bot.config import ROOT
from bot.connection.network import shared_listener
from bot.perception.popups import find_npc_dialog, find_small_menu

log = logging.getLogger("observer_pnj")

NPC_BODY_UP = 35  # px de référence : le corps du PNJ au-dessus de sa case (comme team/dungeon.py)


def answers_text(ids) -> dict[int, str]:
    from pathlib import Path

    from tools.cartes_client import lire_sol

    table = lire_sol(Path(ROOT / "data" / "client_retro" / "lang" / "ANKXTRASO_0_D.sol"))["_Data"]["D"]
    return {i: str(table["a"].get(str(i), "?")) for i in ids}


def question_text(qid: int) -> str:
    from pathlib import Path

    from tools.cartes_client import lire_sol

    table = lire_sol(Path(ROOT / "data" / "client_retro" / "lang" / "ANKXTRASO_0_D.sol"))["_Data"]["D"]
    return str(table["q"].get(str(qid), "?"))[:120]


def log_question(question) -> None:
    if question is None:
        return
    qid, answers = question
    log.info("Question %d : %s", qid, question_text(qid))
    for aid, text in answers_text(answers).items():
        log.info("  réponse %d : %s", aid, text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--personnage", required=True)
    parser.add_argument("--porte", help="x,y de la case de la porte à passer d'abord (grille de combat)")
    parser.add_argument("--pnj-modele", type=int, required=True, help="modèle du PNJ (GM, 5e champ) : 780 pour Mawy")
    parser.add_argument("--pnj-case", help="x,y du PNJ si la carte n'a pas changé (acteurs pas encore annoncés)")
    parser.add_argument("--suivre", help="ligne de la bulle à cliquer tant qu'elle est proposée (« Continuer la "
                                         "discussion ») ; aucune autre réponse n'est jamais cliquée")
    parser.add_argument("--etapes", type=int, default=12, help="avec --suivre : clics au plus")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    enable_dpi_awareness()
    found = [w for w in find_windows("- Dofus Retro") if w.title.lower().startswith(args.personnage.lower())]
    listener = shared_listener()
    if not found or listener is None:
        print("Fenêtre introuvable ou écoute du serveur impossible", file=sys.stderr)
        return 1
    control = StopControl("f12", "", corner=False).start()
    folder = ROOT / "logs" / f"observation_pnj_{time.strftime('%Y%m%d_%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)

    class Recorder:
        dir = folder

        def save(self, image, label, lossless=False, quality=80):
            path = folder / f"{label}.png"
            cv2.imencode(".png", image)[1].tofile(str(path))
            return path

        def keeps(self, lossless=False):
            return True

    game = Game(found[0], control, Recorder(), Human(), act=True, background=True)
    net = game.net = listener.follow(found[0].hwnd, args.personnage)
    listener.keep_journal(folder)
    time.sleep(3.0)  # premiers messages (BN toutes les ~30 s ; la carte arrive au premier GDM)
    log.info("Carte d'après le serveur : %s ; acteurs connus : %d", net.map_id, len(net.actors))
    if args.porte:
        cell = tuple(int(v) for v in args.porte.split(","))
        frame = game.look(park=False)
        clicked = time.monotonic()
        with game.holding("e"):  # autres joueurs cachés, comme pour les soleils
            game.click(frame.grid.center(cell), (2, 2))
        new = net.wait_new_map(clicked, 12.0)
        log.info("Porte (%d,%d) : %s", *cell, f"nouvelle carte {new}" if new is not None else "pas de changement de carte")
        if new is None:
            Recorder().save(game.look(park=False).image, "porte_ratee")
            return 1
        time.sleep(1.5)
    actors = sorted(dict(net.actors).values(), key=lambda a: a.id)
    for actor in actors:
        log.info("Acteur %d en case %d (%s), modèle/nom « %s »", actor.id, actor.cell, map_cell(actor.cell), actor.name)
    npc = next((a for a in actors if a.id < 0 and a.name == str(args.pnj_modele)), None)
    frame = game.look(park=False)
    Recorder().save(frame.image, "salle")
    if npc is None and not args.pnj_case:
        log.warning("PNJ de modèle %d pas annoncé sur cette carte", args.pnj_modele)
        return 1
    cell = map_cell(npc.cell) if npc is not None else tuple(int(v) for v in args.pnj_case.split(","))
    x, y = frame.grid.center(cell)
    point = (x, y - frame.layout.length(NPC_BODY_UP))
    log.info("PNJ en %s : clic gauche en (%d, %d)", cell, *point)
    asked = time.monotonic()
    with game.holding("e"):
        game.click(point, frame.layout.spread(3, 3))
    shown = game.wait_until(lambda f: find_npc_dialog(f) is not None or find_small_menu(f, around=point) is not None,
                            4.0, park=False)
    if shown is not None and find_npc_dialog(shown) is None:
        # Menu du PNJ (son nom, puis « Parler », 05/11 : Mawy comme Altaïr) : sa seule entrée beige.
        x0, y0, x1, y1 = find_small_menu(shown, around=point)
        log.info("Menu du PNJ : clic sur « Parler »")
        game.click(((x0 + x1) / 2, (y0 + y1) / 2), shown.layout.spread(8, 3))
        shown = game.wait_until(lambda f: find_npc_dialog(f) is not None, 4.0, park=False)
    question = net.question if net.question_at >= asked else None
    Recorder().save((shown or game.look(park=False)).image, "bulle")
    if question is None:
        log.warning("Pas de question annoncée par le serveur (DQ) ; bulle %s", "vue" if shown is not None else "pas vue")
    else:
        log_question(question)
    for step in range(args.etapes if args.suivre and shown is not None else 0):
        from bot.perception.text import find_text

        line = find_text(shown, shown.layout.map_box, args.suivre, exact=True)
        if line is None:
            log.info("« %s » plus proposé après %d clic(s)", args.suivre, step)
            break
        clicked = time.monotonic()
        game.click(line, shown.layout.spread(10, 3))
        if net.wait_for("question_at", clicked, 3.0) is None:
            log.warning("Clic %d sur « %s » : pas de nouvelle question annoncée", step + 1, args.suivre)
        game.sleep(0.5)
        shown = game.look(park=False)
        Recorder().save(shown.image, f"bulle_{step + 1:02d}")
        log.info("Clic %d sur « %s »", step + 1, args.suivre)
        log_question(net.question)
    if shown is not None:
        cross = find_npc_dialog(shown)
        game.click(cross, shown.layout.spread(2, 2))  # refermée sans répondre : rien n'est consommé
        game.sleep(0.8)
        log.info("Bulle refermée sans répondre : %s", "oui" if find_npc_dialog(game.look(park=False)) is None else "non")
    log.info("Journal et captures : %s", folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
