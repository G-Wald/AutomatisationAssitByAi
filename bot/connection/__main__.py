"""Connexion seule : les fenêtres du jeu menées en jeu, le groupe reformé et replié, puis le programme s'arrête.

Depuis la racine du projet :
    .venv\\Scripts\\python -m bot.connection                     serveur et personnages de config/settings.yaml
    .venv\\Scripts\\python -m bot.connection --serveur Allisteria --principal Gela-[A13]
    .venv\\Scripts\\python -m bot.connection --arriere-plan      gestes envoyés aux fenêtres, ta souris reste libre
    .venv\\Scripts\\python -m bot.connection --sans-groupe       connexion seulement

Les fenêtres du jeu déjà ouvertes sont connectées d'abord ; s'il manque encore un des personnages voulus (ou s'il n'y
a aucune fenêtre), l'Ankama Launcher est lancé (s'il ne l'est pas) et lance tous les comptes disponibles (Ctrl+8) ;
chaque nouvelle fenêtre est connectée avec un personnage voulu, ou refermée (compte non abonné, aucun personnage
voulu, tous déjà en jeu).
F12 arrête. Journal et captures : logs/<date_heure>/.
"""

import argparse
import logging
import sys

from bot.__main__ import _names, _screen, connection_settings, in_background, single_instance, stop_control
from bot.actions.input import Human
from bot.actions.stop import BotStopped
from bot.capture.monitors import ScreenSettings
from bot.capture.window import GameWindow
from bot.connection.clients import client_windows, logged_in
from bot.connection.group import Player, form_group
from bot.connection.manager import connect_at_start
from bot.config import load_settings
from bot.debug.recorder import Recorder
from bot.team.member import Focus, MemberGame, TaggedRecorder, tag_logs
from bot.team.roster import TeamSettings

log = logging.getLogger("bot")


def team_order(names: list[str], team: TeamSettings, preferred: tuple[str, ...] = ()) -> list[str]:
    """Personnages en jeu dans l'ordre de l'équipe : le principal d'abord (--principal, team.leader, sinon le premier
    de team.members ou de connexion.personnages, sinon le premier trouvé), puis les autres de cette liste (sans
    liste : tous les autres)."""
    by_lower = {n.lower(): n for n in names}
    wanted = team.members or preferred
    members = [by_lower[m.lower()] for m in wanted if m.lower() in by_lower] if wanted else list(names)
    leader = team.leader or (members[0] if members else "")
    first = by_lower.get(leader.lower())
    return ([first] if first else []) + [n for n in members if n != first]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serveur", metavar="NOM", help="serveur (défaut : connexion.serveur)")
    parser.add_argument("--principal", metavar="NOM", help="personnage qui invite les autres dans son groupe "
                                                           "(défaut : team.leader, sinon le premier de team.members)")
    parser.add_argument("--personnages", type=_names, metavar="A,B",
                        help="personnages à connecter, ex. Gela-[A13],Brawli-[Gal] (défaut : connexion.personnages)")
    parser.add_argument("--sans-groupe", action="store_true", help="ne reforme pas le groupe")
    parser.add_argument("--ecran", type=_screen, metavar="N",
                        help="écran où placer et agrandir les fenêtres du jeu : numéro (1 : le plus à gauche), auto, "
                             "principal, secondaire, aucun (défaut : window.ecran)")
    parser.add_argument("--arriere-plan", action="store_true", help="gestes envoyés aux fenêtres du jeu, sans passer "
                                                                    "devant (défaut : window.arriere_plan)")
    parser.add_argument("--verbose", action="store_true", help="affiche aussi le détail (clics)")
    args = parser.parse_args()

    lock = single_instance()  # gardé jusqu'à la fin du programme
    if lock is None:
        print("Un bot tourne déjà : arrête-le (F12) avant de lancer la connexion.", file=sys.stderr)
        return 1
    settings = load_settings()
    logs = settings.get("logs") or {}
    recorder = Recorder(max_images=int(logs.get("max_images", 300)), images=str(logs.get("images", "toutes")))
    recorder.setup_logging(args.verbose)
    connection = connection_settings(args, settings, enabled=True)
    team = TeamSettings.from_settings(settings, leader=args.principal)
    background = in_background(args, settings)
    control = stop_control(settings, background)
    human = Human.from_settings(settings)
    try:
        log.info("Journal et captures : %s", recorder.dir)
        names = connect_at_start(connection, connection.wanted(team.members), control, recorder, human, background,
                                 team.switch_delay, ScreenSettings.from_settings(settings, args.ecran))
        if not names:
            log.error("Aucun personnage en jeu")
            return 1
        log.info("En jeu : %s", ", ".join(names))
        order = team_order(names, team, connection.characters)
        if args.sans_groupe or not connection.group or len(order) < 2:
            return 0
        windows: dict[str, GameWindow] = {logged_in(w): w for w in client_windows() if logged_in(w)}
        focus = Focus(control, team.switch_delay, passive=background)
        tag_logs(lambda: focus.current_name)
        players = []
        for name in order:
            focus.names[windows[name].hwnd] = name
            game = MemberGame(windows[name], control, TaggedRecorder(recorder, name), human, True, focus, background)
            players.append(Player(name, game))
        log.info("Groupe : %s invite %s", order[0], ", ".join(order[1:]))
        missing = form_group(players[0], players[1:], connection.collapse_group)
        return 1 if missing else 0
    except BotStopped as stop:
        log.info("Arrêté (%s)", stop)
    except KeyboardInterrupt:
        log.info("Arrêté (Ctrl+C)")
    except Exception:
        log.exception("Erreur inattendue : arrêt")
        return 1
    finally:
        control.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
