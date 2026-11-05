"""Observation du glisser d'une pile du coffre vers le sac (05/11 : « la pile de clés n'a pas quitté le coffre d'après
le serveur » chez 7 personnages sur 7). Le personnage, déjà en 4,-16 devant la banque, y entre, ouvre son coffre
(127 kamas), puis refait le geste de team/bank.py (take_from_chest) étape par étape, une capture à chaque étape, les
messages du serveur au journal ; à la fin, le coffre refermé (sans Échap s'il y a mieux) et sortie de la banque.

Une seule pile reprise au plus (`--nombre 1`). Arrière-plan ; ta souris hors des fenêtres du jeu (elle casse les
glisser par messages, 30/10). F12 arrête.

    .venv\\Scripts\\python -m tools.observer_coffre --personnage Walitcoin --modele 8143
"""

import argparse
import logging
import sys
import time
from types import SimpleNamespace

import cv2

from bot.actions.input import Human
from bot.actions.stop import StopControl
from bot.brain.game import Game
from bot.capture.window import enable_dpi_awareness, find_windows
from bot.config import ROOT
from bot.connection.network import shared_listener
from bot.debug.draw import annotate
from bot.team import bank

log = logging.getLogger("observer_coffre")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--personnage", required=True)
    parser.add_argument("--modele", type=int, default=8143, help="modèle de l'objet à reprendre (Clef des Champs)")
    parser.add_argument("--nombre", type=int, default=1)
    parser.add_argument("--double-clic", action="store_true",
                        help="double-clic sur la pile au lieu du glisser (05/11 : un seul objet, comme aux échanges)")
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
    folder = ROOT / "logs" / f"observation_coffre_{time.strftime('%Y%m%d_%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)
    count = [0]

    class Recorder:
        dir = folder

        def save(self, image, label, lossless=False, quality=80):
            count[0] += 1
            path = folder / f"{count[0]:02d}_{label}.png"
            cv2.imencode(".png", image)[1].tofile(str(path))
            return path

        def keeps(self, lossless=False):
            return True

    recorder = Recorder()
    game = Game(found[0], control, recorder, Human(), act=True, background=True)
    net = game.net = listener.follow(found[0].hwnd, args.personnage)
    listener.keep_journal(folder)
    member = SimpleNamespace(name=args.personnage, game=game)
    visit = bank.BankVisit([member])

    def shot(label, marks=()):
        frame = game.look(park=False)
        path = recorder.save(annotate(frame, marks=list(marks)) if marks else frame.image, label)
        log.info("Capture %s : %s", label, path)
        return frame

    time.sleep(3.0)
    shot("dehors")
    if args.personnage not in visit._doors([member], bank.DOOR, inside=True):
        log.warning("Pas entré dans la banque")
        return 1
    shot("dedans")
    if not visit.open_chest(member):
        shot("coffre_pas_ouvert")
        log.warning("Coffre pas ouvert : %s", visit.reasons.get(args.personnage))
        return 1
    layout = shot("coffre_ouvert").layout
    exchange, _ = net.trade()
    stacks = bank.chest_stacks(net, args.modele)
    from bot.brain.inventory import key_names

    keys = set(key_names()) or {args.modele}
    same_type = [u for u, (m, q) in exchange.storage.items() if m in keys and q > 0]
    log.info("Coffre d'après le serveur : %d piles ; modèle %d : %s ; piles de clés (ordre d'arrivée) : %s",
             len(exchange.storage), args.modele, stacks, [(u, exchange.storage[u]) for u in same_type])
    try:
        game.click(layout.point(*bank.CHEST_RESOURCES_TAB), (5, 5))
        game.sleep(0.8)
        shot("onglet_ressources", [(layout.point(*bank.CHEST_RESOURCES_TAB), "onglet")])
        typed = bank.choose_type(game, layout, bank.KEYS_TYPE, bank.CHEST_FILTER, bank.CHEST_LIST)
        frame = shot("type_clefs" if typed else "type_clefs_pas_trouve",
                     [(layout.point(*bank.CHEST_FILTER), "filtre")])
        if not typed or not stacks:
            return 1
        uid, stored = stacks[0]
        rank = same_type.index(uid) if uid in same_type else 0
        start = layout.point(*bank.grid_point(bank.CHEST_FIRST_STACK, rank, bank.CHEST_STEP, bank.CHEST_COLUMNS))
        drop = layout.point(*bank.BAG_DROP)
        shot("avant_glisser", [(start, f"pile rang {rank}"), (drop, "lacher")])
        since = time.monotonic()
        if args.double_clic:
            game.double_click(start, (3, 3))
            game.sleep(1.0)
            shot("apres_double_clic")
            moved = bank._chest_moved(net, uid, stored, since, bank.CHEST_WAIT)
            log.info("Double-clic : pile du coffre %s ; serveur : %s ; dans le sac : %s",
                     "diminuée" if moved else "PAS diminuée", " | ".join(net.heard_since(since)) or "rien",
                     net.held(args.modele))
            return 0
        bank.slow_drag(game, start, drop)
        game.sleep(0.8)
        shot("apres_glisser")
        log.info("Serveur depuis le glisser : %s", " | ".join(net.heard_since(since)) or "rien")
        if not bank._chest_moved(net, uid, stored, since, 0.6):
            _, ok, _ = bank.quantity_points(layout, drop)
            shot("fenetre_quantite_attendue", [(ok, "coche")])
            for _ in range(4):
                game.press("backspace")
            game.type_text(str(min(args.nombre, stored)))
            game.sleep(0.3)
            game.click(ok, (3, 3))
            game.sleep(1.0)
            shot("apres_coche")
        moved = bank._chest_moved(net, uid, stored, since, bank.CHEST_WAIT)
        log.info("Pile du coffre %s ; serveur : %s", "diminuée" if moved else "PAS diminuée",
                 " | ".join(net.heard_since(since)) or "rien")
    finally:
        bank.choose_type(game, layout, bank.ALL_TYPES, bank.CHEST_FILTER, bank.CHEST_LIST)
        bank.close_unexpected(game, f"Observation, {args.personnage}")
        shot("coffre_ferme")
        visit._doors([member], bank.EXIT, inside=False)
        shot("sorti")
        log.info("Journal et captures : %s", folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
