"""Lance le bot, le jeu ouvert.

Depuis la racine du projet :
    .venv\\Scripts\\python -m bot --observe       regarde et journalise, sans toucher à la souris ni au clavier
    .venv\\Scripts\\python -m bot --combat-only   joue les combats que tu lances toi-même
    .venv\\Scripts\\python -m bot                 enchaîne : attaque un groupe, combat, ferme le résultat, recommence
    .venv\\Scripts\\python -m bot --zone          pareil, et change de carte (même zone) quand il n'y a plus de groupe
    .venv\\Scripts\\python -m bot --monde incarnam --combat-zone 1,1
                                               combats dans la sous-zone de la carte 1,1 (Prairie), sans en sortir
    .venv\\Scripts\\python -m bot --travel-test   sans combat : s'éloigne de 2 cartes puis revient au départ
    .venv\\Scripts\\python -m bot --explore --position 1,6   sans combat : visite toutes les cartes de la région
    .venv\\Scripts\\python -m bot --monde douze --position 12,-28 --aller 5,-18
                                               sans combat : de la carte 12,-28 à la carte 5,-18 par le plus court
                                               chemin (sans --position : coordonnées lues à l'écran ; x négatif :
                                               --aller=-1,-20, --position=-1,-20)
    .venv\\Scripts\\python -m bot --equipe --principal Jesuisleking
                                               mode équipe : une fenêtre du jeu par personnage ; le principal attaque,
                                               les autres le rejoignent et le suivent (se combine avec les options
                                               ci-dessus : --combat-only, --zone, --observe...)
    .venv\\Scripts\\python -m bot --equipe --notifications
                                               mode équipe, et qui joue d'après les notifications de Windows du jeu
                                               (bot/team/signals.py) : sa fenêtre tout de suite, vérifiée au timer
    .venv\\Scripts\\python -m bot --lecture memoire
                                               début de session : sorts et caractéristiques de chaque personnage repris
                                               de la dernière session, sans ouvrir de fenêtre (défaut : session.mode de
                                               config/settings.yaml ; controle : lus à l'écran puis retenus)
    .venv\\Scripts\\python -m bot --equipe --connexion
                                               connecte d'abord les fenêtres du jeu restées à l'écran de connexion
                                               (serveur, personnage), reforme le groupe, puis joue ; reconnecte un
                                               personnage déconnecté (inactivité). Connexion seule :
                                               .venv\\Scripts\\python -m bot.connection

F12 arrête le bot, F9 le met en pause (réglables dans config/settings.yaml). Amener la souris dans le coin
haut-gauche de l'écran l'arrête aussi. Journal et captures : logs/<date_heure>/.
"""

import argparse
import logging
import sys
import time
from dataclasses import replace

import win32api
import win32event
import winerror

from bot.actions.input import Human
from bot.actions.stop import BotStopped, StopControl
from bot.brain.arena import changed_maps
from bot.brain.game import Game
from bot.brain.loop import Bot
from bot.brain.memory import CONTROL, READING_MODES, reading_mode
from bot.brain.recovery import capture_once
from bot.brain.travel import WORLDS
from bot.capture.monitors import ScreenSettings, place_windows, valid_choice
from bot.capture.window import character_name, find_windows
from bot.config import apply_overrides, load_settings, parse_override
from bot.connection.group import Player, form_group
from bot.team.bank import BANK_MAP, BankVisit
from bot.team.bank import inside as bank_inside
from bot.connection.characters import parse_names
from bot.connection.clients import client_windows
from bot.connection.manager import Watcher, connect_at_start
from bot.connection.settings import ConnectionSettings
from bot.debug.bilans import archive, archive_missing
from bot.debug.recorder import Recorder, prune_sessions
from bot.team.dungeon import DungeonFarm, load_dungeons
from bot.team.loop import TeamBot
from bot.team.member import Focus, make_member, tag_logs
from bot.team.roster import TeamSettings, pick_team
from bot.team.signals import NOTIFICATIONS, RECORD_NAME, TurnSignals, turn_signal, turn_words

log = logging.getLogger("bot")


SINGLE_INSTANCE = "autobot-dofus-retro"  # mutex nommé : un seul bot à la fois


def single_instance(name: str = SINGLE_INSTANCE):
    """Verrou tenu tant que le bot tourne ; None si un autre bot le tient déjà. Le 01/10, un bot lancé en --observe
    tournait encore : les commandes suivantes attendaient derrière lui, et deux bots se disputeraient les fenêtres."""
    handle = win32event.CreateMutex(None, False, name)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        win32api.CloseHandle(handle)
        return None
    return handle


def _coords(text: str) -> tuple[int, int]:
    try:
        x, y = (int(v) for v in text.replace(" ", "").split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"coordonnées attendues sous la forme x,y (pas {text!r})") from None
    return x, y


def _goto(args: argparse.Namespace, settings: dict) -> bool:
    """--aller X,Y : mode aller, carte d'arrivée dans travel.destination. True pour le mode aller (en ligne de
    commande ou dans config/settings.yaml) : aucun combat lancé par le bot pendant le trajet."""
    if getattr(args, "etapes", None):
        args.aller = args.etapes[-1]
        settings["travel"] = {**(settings.get("travel") or {}), "etapes": args.etapes}
    if args.aller is not None:
        args.travel = "aller"
        settings["travel"] = {**(settings.get("travel") or {}), "destination": args.aller}
    return (args.travel or (settings.get("travel") or {}).get("mode")) == "aller"


def _reading(text: str) -> str:
    try:
        return reading_mode(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"controle ou memoire attendu (pas {text!r})") from None


SESSION_MODES = {"controle": "sorts et caractéristiques lus à l'écran (contrôle), puis retenus",
                 "memoire": "sorts et caractéristiques repris de la dernière session (lus à l'écran s'il n'y a rien)"}


def _session(args: argparse.Namespace, settings: dict) -> str:
    """--lecture : mode du début de session (session.mode) ; renvoie le mode retenu, pour le journal (ValueError si
    session.mode est inconnu)."""
    if args.lecture is not None:
        settings["session"] = {**(settings.get("session") or {}), "mode": args.lecture}
    return reading_mode((settings.get("session") or {}).get("mode") or CONTROL)


def _override(text: str):
    try:
        return parse_override(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


BACKGROUND_NOTE = ("Arrière-plan : je ne touche ni à ta souris ni à ton clavier, et je ne passe aucune fenêtre du jeu "
                   "devant ; elles peuvent être recouvertes (vidéo, autre application), mais jamais réduites dans la "
                   "barre des tâches")


def in_background(args: argparse.Namespace, settings: dict) -> bool:
    """Jouer en arrière-plan : --arriere-plan, ou window.arriere_plan de config/settings.yaml."""
    return bool(args.arriere_plan or (settings.get("window") or {}).get("arriere_plan", False))


def limit_session(bot, args: argparse.Namespace) -> None:
    """--combats, --minutes : le bot s'arrête de lui-même (sessions de tests enchaînées)."""
    if args.combats:
        bot.max_fights = args.combats
    if args.minutes:
        bot.deadline = time.monotonic() + args.minutes * 60
    if args.combats or args.minutes:
        log.info("Session limitée : %s", " ou ".join(part for part in (
            f"{args.combats} combat(s)" if args.combats else "", f"{args.minutes:g} minute(s)" if args.minutes else "")
            if part))


def stop_control(settings: dict, background: bool) -> StopControl:
    """Touches d'arrêt et de pause. En arrière-plan, pas d'arrêt par la souris dans le coin de l'écran : c'est la
    souris de l'utilisateur, qui s'en sert pendant ce temps."""
    safety = settings.get("safety") or {}
    return StopControl(safety.get("stop_key") or "f12", safety.get("pause_key") or "", corner=not background).start()


def _names(text: str) -> tuple[str, ...]:
    names = parse_names(text)
    if not names:
        raise argparse.ArgumentTypeError("au moins un personnage attendu, ex. Gela-[A13],Brawli-[Gal]")
    return names


def connection_settings(args: argparse.Namespace, settings: dict, enabled: bool | None = None) -> ConnectionSettings:
    """Réglages de connexion, avec les options --serveur et --personnages (remplies par l'interface).
    --personnages fixe aussi l'équipe (team.members), si config/settings.yaml n'en donne pas."""
    if args.personnages and not (settings.get("team") or {}).get("members"):
        settings["team"] = {**(settings.get("team") or {}), "members": list(args.personnages)}
    return ConnectionSettings.from_settings(settings, enabled=enabled, server=args.serveur,
                                            characters=args.personnages)


def _screen(text: str) -> str:
    if not valid_choice(text):
        raise argparse.ArgumentTypeError(f"auto, principal, secondaire, aucun ou un numéro d'écran attendu (pas {text!r})")
    return text.strip().lower()


def place(args: argparse.Namespace, settings: dict) -> None:
    """Fenêtres du jeu ouvertes déplacées sur l'écran choisi (--ecran, window.ecran) et agrandies."""
    control = stop_control(settings, in_background(args, settings))
    try:
        place_windows(client_windows(), ScreenSettings.from_settings(settings, args.ecran), control.sleep)
    finally:
        control.close()


def connect(args: argparse.Namespace, settings: dict, connection: ConnectionSettings, recorder: Recorder) -> int | None:
    """--connexion : les fenêtres du jeu menées en jeu avant de jouer. None pour continuer, sinon le code de sortie."""
    background = in_background(args, settings)
    control = stop_control(settings, background)
    team = TeamSettings.from_settings(settings, leader=args.principal)
    try:
        names = connect_at_start(connection, connection.wanted(team.members), control, recorder,
                                 Human.from_settings(settings), background, team.switch_delay,
                                 ScreenSettings.from_settings(settings, args.ecran))
    except BotStopped as stop:
        log.info("Bot arrêté (%s)", stop)
        return 0
    except KeyboardInterrupt:
        log.info("Bot arrêté (Ctrl+C)")
        return 0
    finally:
        control.close()
    if not names:
        log.error("Aucun personnage en jeu")
        return 1
    log.info("En jeu : %s", ", ".join(names))
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--observe", action="store_true", help="observe seulement : aucun clic, aucune touche")
    parser.add_argument("--combat-only", action="store_true", help="n'attaque pas les groupes : tu lances les combats")
    moves = parser.add_mutually_exclusive_group()
    moves.add_argument("--combat-zone", type=_coords, metavar="X,Y",
                       help="combats en restant dans la sous-zone de la carte X,Y (d'après les références) ; "
                            "le personnage doit être sur cette carte")
    moves.add_argument("--zone", dest="travel", action="store_const", const="zone",
                       help="combats, et carte voisine de la même zone quand il n'y a plus de groupe")
    moves.add_argument("--travel-test", dest="travel", action="store_const", const="test",
                       help="sans combat : s'éloigne de 2 cartes puis revient sur la carte de départ")
    moves.add_argument("--explore", dest="travel", action="store_const", const="explore",
                       help="sans combat : visite toutes les cartes de la région de départ")
    moves.add_argument("--aller", type=_coords, metavar="X,Y",
                       help="sans combat : va jusqu'à la carte X,Y par le plus court chemin (départ : --position, "
                            "sinon les coordonnées lues à l'écran ; x négatif : --aller=-1,-20)")
    # Un seul style de combat, polyvalent, depuis le 03/10 : --style reste accepté (anciennes commandes), ignoré.
    parser.add_argument("--style", help=argparse.SUPPRESS)
    parser.add_argument("--monde", choices=sorted(WORLDS),
                        help="où le personnage se trouve : incarnam ou douze (Monde des Douze) ; défaut : config/settings.yaml")
    parser.add_argument("--position", type=_coords, metavar="X,Y",
                        help="coordonnées de la carte de départ (en haut à gauche du jeu), pour les modes qui changent de carte")
    parser.add_argument("--title", help="partie du titre de la fenêtre (défaut : config/settings.yaml)")
    parser.add_argument("--donjon", metavar="NOM",
                        help="mode équipe : faire le donjon NOM (config/donjons_entree.yaml), l'équipe devant l'entrée ; "
                             "NOM:ETAPE pour reprendre à une étape (entree, dalle, soleil, pnj, salles), NOM:ETAPE:NOMS "
                             "pour ces seuls personnages à cette étape (les autres l'ont faite), NOM:salles:N en salle N")
    parser.add_argument("--etapes", type=lambda t: [_coords(c) for c in t.split(";") if c.strip()], metavar="X,Y;X,Y",
                        help="sans combat : rejoint ces cartes l'une après l'autre (une étape ratée est sautée)")
    parser.add_argument("--banque", action="store_true",
                        help="mode équipe : à la banque d'Astrub (4,-16), chaque personnage vide l'onglet ressources de "
                             "son inventaire dans son coffre, puis le bot s'arrête")
    parser.add_argument("--passages", type=int, default=1, metavar="N",
                        help="avec --donjon : N passages complets (entrée, combats de salle en salle, sortie)")
    parser.add_argument("--equipe", action="store_true",
                        help="mode équipe : toutes les fenêtres du jeu (ou celles de team.members), un personnage chacune")
    parser.add_argument("--principal", metavar="NOM",
                        help="mode équipe : personnage principal, tel qu'écrit dans le titre de sa fenêtre "
                             "(implique --equipe ; défaut : team.leader de config/settings.yaml)")
    parser.add_argument("--notifications", action="store_true",
                        help="mode équipe : qui joue, d'après les notifications de Windows envoyées par le jeu "
                             "au début de chaque tour (vérifié au timer du tour) ; défaut : team.turn_signal de "
                             "config/settings.yaml (timeline seule)")
    parser.add_argument("--lecture", type=_reading, metavar="|".join(READING_MODES),
                        help="début de session, pour chaque personnage : controle = sorts et caractéristiques lus à "
                             "l'écran puis retenus ; memoire = repris de la dernière session sans ouvrir de fenêtre "
                             "(défaut : session.mode de config/settings.yaml)")
    parser.add_argument("--arriere-plan", action="store_true",
                        help="joue en arrière-plan : gestes envoyés aux fenêtres du jeu, ta souris et ton clavier "
                             "restent libres (défaut : window.arriere_plan de config/settings.yaml)")
    parser.add_argument("--connexion", action="store_true",
                        help="connecte d'abord les fenêtres du jeu restées à l'écran de connexion (lance le launcher "
                             "s'il n'y en a aucune), reforme le groupe ; pendant le jeu, reconnecte un personnage "
                             "déconnecté (défaut : connexion.auto de config/settings.yaml)")
    parser.add_argument("--serveur", metavar="NOM", help="serveur choisi à la connexion (défaut : connexion.serveur)")
    parser.add_argument("--personnages", type=_names, metavar="A,B",
                        help="personnages à connecter (et à jouer en équipe), ex. Gela-[A13],Brawli-[Gal] "
                             "(défaut : connexion.personnages)")
    parser.add_argument("--ecran", type=_screen, metavar="N",
                        help="écran où placer et agrandir les fenêtres du jeu au lancement : numéro (1 : le plus à "
                             "gauche), auto (un écran 1080p), principal, secondaire, aucun (défaut : window.ecran)")
    parser.add_argument("--combats", type=int, metavar="N",
                        help="s'arrête après N combats (sessions de tests), une fois les fenêtres de résultat fermées")
    parser.add_argument("--minutes", type=float, metavar="M",
                        help="s'arrête après M minutes, hors combat (un combat commencé est fini)")
    parser.add_argument("--verbose", action="store_true", help="affiche aussi le détail (clics, combattants)")
    parser.add_argument("--set", action="append", default=[], type=_override, metavar="SECTION.CLE=VALEUR",
                        help="remplace un réglage de config/settings.yaml pour ce lancement (répétable), ex. "
                             "--set combat.burst=false --set timing.wait_mode=condition (interface : bot.gui)")
    args = parser.parse_args()

    lock = single_instance()  # gardé jusqu'à la fin du programme
    if lock is None:
        print("Un autre bot tourne déjà : arrête-le (F12, ou Ctrl+C dans son terminal) avant d'en lancer un nouveau.",
              file=sys.stderr)
        return 1
    settings = apply_overrides(load_settings(), args.set)
    try:
        session_mode = _session(args, settings)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    logs = settings.get("logs") or {}
    recorder = Recorder(max_images=int(logs.get("max_images", 300)), images=str(logs.get("images", "toutes")))
    recorder.setup_logging(args.verbose)
    if args.style is not None:
        log.warning("--style %s ignoré : un seul style de combat, polyvalent (taper vite, à l'abri, sans gêner les "
                    "alliés)", args.style)
    archived = archive_missing(recorder.dir.parent, current=recorder.dir)  # chiffres gardés avant la rotation
    if archived:
        log.info("Bilans chiffrés gardés dans %s : %d session(s)", archived[0].parent, len(archived))
    removed = prune_sessions(recorder.dir.parent, int(logs.get("keep_sessions", 5)),
                             float(logs.get("max_total_mb", 300)), current=recorder.dir)
    if removed:
        log.info("Anciennes sessions supprimées : %s", ", ".join(d.name for d in removed))

    title = args.title or (settings.get("window") or {}).get("title")
    if not title:
        log.error("Titre de fenêtre inconnu : utilise --title ou renseigne window.title dans config/settings.yaml")
        return 1
    connection = connection_settings(args, settings, enabled=True if args.connexion else None)
    if connection.enabled and args.observe:
        log.warning("Connexion automatique ignorée en observation (aucun clic)")
        connection = replace(connection, enabled=False)
    if connection.enabled:
        if (code := connect(args, settings, connection, recorder)) is not None:
            return code
    elif not args.observe:
        place(args, settings)  # observation : rien n'est touché, pas même les fenêtres
    forced = True if args.equipe or args.principal else None
    if forced is None and str((settings.get("team") or {}).get("enabled", "")).strip().lower() == "auto":
        # team.enabled: auto : mode équipe dès que plusieurs fenêtres de personnages sont ouvertes, solo sinon.
        names = {character_name(w.title, title) for w in find_windows(title)} - {""}
        forced = len(names) >= 2
        log.info("Mode %s : %d fenêtre(s) de personnage ouverte(s)", "équipe" if forced else "solo", len(names))
    team = TeamSettings.from_settings(settings, enabled=forced, leader=args.principal)
    if team.enabled:
        return run_team(args, settings, team, title, recorder, connection)
    if args.notifications:
        log.warning("--notifications : seulement en mode équipe (--equipe), ignoré")
    matches = find_windows(title)
    if not matches:
        log.error("Aucune fenêtre ne contient « %s ». Le jeu est-il lancé ? (python -m tools.capture --list)", title)
        return 1
    if len(matches) > 1:
        log.warning("%d fenêtres contiennent « %s », j'utilise « %s » (précise avec --title)",
                    len(matches), title, matches[0].title)
    window = matches[0]

    background = in_background(args, settings)
    control = stop_control(settings, background)
    try:
        game = Game(window, control, recorder, Human.from_settings(settings), act=not args.observe,
                    background=background)
        if args.combat_zone is not None:
            args.travel, args.position = "zone", args.combat_zone
        goto = _goto(args, settings)
        bot = Bot(game, settings, act=not args.observe, engage=False if args.combat_only or goto else None,
                  travel=args.travel, start=args.position, region=WORLDS.get(args.monde))
        log.info("Fenêtre : %s", window.title)
        log.info("Début de session : %s", SESSION_MODES[session_mode])
        if args.observe:
            log.info("Mode : observation (aucune action)")
        else:
            moves = {"test": "test de déplacement (2 cartes puis retour)", "explore": "exploration de la région",
                     "zone": "changement de carte dans la zone quand elle est vide",
                     "aller": "trajet jusqu'à une carte"}.get(bot.travel_settings.mode)
            if moves:
                log.info("Monde : %s", bot.travel_settings.region)
            if bot.engager is None and moves:
                log.info("Mode : %s, sans lancer de combat", moves)
            else:
                log.info("Mode : %s%s", "combats + attaque des groupes" if bot.engager else "combats seulement",
                         f" ; {moves}" if moves else "")
        log.info("Arrêt : %s%s", control.stop_key_name.upper(),
                 f" — pause : {control.pause_key_name.upper()}" if control.pause_key_name else "")
        log.info("Journal et captures : %s", recorder.dir)
        limit_session(bot, args)
        if connection.enabled:
            log.info("Reconnexion automatique : serveur %s", connection.server)
            bot.connection = Watcher(connection, [Player(character_name(window.title, title) or window.title, game)])
        if background:
            log.info(BACKGROUND_NOTE)
        else:
            window.bring_to_front()
        control.sleep(0.5)
        frame = game.look(park=False)
        if not frame.layout.matches_reference():
            log.warning("Fenêtre de %dx%d : proportions différentes des captures de référence (1918x1008), "
                        "la disposition de l'interface peut ne pas correspondre.", frame.layout.width, frame.layout.height)
        bot.run()
    except BotStopped as stop:
        log.info("Bot arrêté (%s)", stop)
    except KeyboardInterrupt:
        log.info("Bot arrêté (Ctrl+C)")
    except Exception:
        log.exception("Erreur inattendue : le bot s'arrête")
        return 1
    finally:
        control.close()
        _keep_summary(recorder)
    return 0


def run_team(args: argparse.Namespace, settings: dict, team: TeamSettings, title: str, recorder: Recorder,
             connection: ConnectionSettings = ConnectionSettings()) -> int:
    """Mode équipe (bot/team) : mêmes options que le mode solo, pour toute l'équipe."""
    try:
        named = pick_team(find_windows(title), title, team)
    except ValueError as error:
        log.error("%s", error)
        return 1
    try:
        signal_mode = turn_signal(settings, forced=args.notifications)
        words = turn_words(settings)
    except ValueError as error:
        log.error("%s", error)
        return 1
    signals = None  # écouteur des notifications de Windows (team.turn_signal: notifications)
    background = in_background(args, settings)
    control = stop_control(settings, background)
    try:
        # Arrière-plan : aucune fenêtre n'est passée devant, on retient seulement celle du personnage qui agit.
        focus = Focus(control, team.switch_delay, passive=args.observe or background)
        human = Human.from_settings(settings)
        members = [make_member(name, window, settings, focus, control, recorder, human, act=not args.observe,
                               background=background)
                   for name, window in named]
        tag_logs(lambda: focus.current_name)
        for m in members:
            m.game.release_keys(_held_keys(settings))  # une touche tenue lors d'un arrêt brutal précédent
        if args.combat_zone is not None:
            args.travel, args.position = "zone", args.combat_zone
        if args.banque and args.aller is None:
            args.aller = BANK_MAP  # trajet jusqu'à la banque d'Astrub d'abord (team/bank.py)
        goto = _goto(args, settings)
        bot = TeamBot(members, settings, team, act=not args.observe, engage=False if args.combat_only or goto else None,
                      travel=args.travel, start=args.position, region=WORLDS.get(args.monde))
        log.info("Équipe : %s ; principal : %s", ", ".join(m.name for m in members), members[0].name)
        changed = changed_maps()
        if changed:
            # 24/10 : la référence fait foi (passages, soleils, cases de combat) ; une mise à jour du jeu la périme.
            log.warning("%d carte(s) du jeu ont changé depuis la construction des références (mise à jour du jeu ?), ex. "
                        "%s : à reconstruire (tools.reference_complete, tools.reference_combat)", len(changed),
                        ", ".join(map(str, changed[:5])))
        log.info("Début de session : %s", SESSION_MODES[_session(args, settings)])
        if args.observe:
            log.info("Mode : observation (aucune action, aucun changement de fenêtre) : passe toi-même d'une fenêtre à "
                     "l'autre, le journal dit ce que le bot y lit")
        else:
            moves = {"test": "test de déplacement (2 cartes puis retour)", "explore": "exploration de la région",
                     "zone": "changement de carte dans la zone quand elle est vide",
                     "aller": "trajet jusqu'à une carte"}.get(bot.travel_settings.mode)
            if moves:
                log.info("Monde : %s", bot.travel_settings.region)
            if bot.engager is None and moves:
                log.info("Mode : %s, toute l'équipe suit le principal, sans lancer de combat", moves)
            else:
                log.info("Mode : %s%s", "le principal attaque les groupes, les autres le rejoignent" if bot.engager
                         else "combats lancés par toi, les autres personnages rejoignent", f" ; {moves}" if moves else "")
            if signal_mode == NOTIFICATIONS:
                # Désactivé avec un message (paquets absents, accès refusé) : la timeline seule, comme d'habitude.
                # Toutes les notifications de Dofus enregistrées à côté de bot.log, pour connaître leurs textes.
                signals = TurnSignals([m.name for m in members], words=words, record=recorder.dir / RECORD_NAME)
                if signals.start():
                    bot.signals = signals
                else:
                    signals = None
            if background:
                log.info(BACKGROUND_NOTE)
            else:
                log.info("Le bot passe lui-même d'une fenêtre à l'autre : F9 (pause) pour reprendre la main. %s",
                         "Notifications de Dofus : bannières désactivées dans Windows (elles couvriraient la barre de "
                         "sorts)." if signal_mode == NOTIFICATIONS else
                         "Désactive les notifications Windows de Dofus : elles s'affichent sur la barre de sorts.")
        log.info("Arrêt : %s%s", control.stop_key_name.upper(),
                 f" — pause : {control.pause_key_name.upper()}" if control.pause_key_name else "")
        log.info("Journal et captures : %s", recorder.dir)
        if not args.observe and not background:
            log.info("Je prends la main : fenêtre de %s au premier plan, puis celles des autres à tour de rôle",
                     members[0].name)
            focus.to(members[0].window)
        limit_session(bot, args)
        if connection.enabled:
            players = [Player(m.name, m.game) for m in members]
            if connection.group and len(players) > 1:
                form_group(players[0], players[1:], connection.collapse_group)
            log.info("Reconnexion automatique : serveur %s", connection.server)
            bot.connection = Watcher(connection, players)
        if args.donjon:
            name, _, rest = args.donjon.partition(":")
            step, _, who = rest.partition(":")
            room = int(who) if step == "salles" and who.isdigit() else 1  # « salles:2 » : reprise en salle 2
            only = None if step == "salles" else {n.strip().lower() for n in who.split(",") if n.strip()} or None
            spec = load_dungeons().get(name)
            if spec is None:
                log.error("Donjon inconnu : %s (config/donjons_entree.yaml)", args.donjon)
                return 1
            return 0 if DungeonFarm(bot, spec, args.passages).run(step or None, only, room) == args.passages else 1
        if args.banque and bank_inside(members[0]):
            log.info("Banque : le principal est déjà dans la banque, pas de trajet")
            return 0 if len(BankVisit(members).run()) == len(members) else 1
        bot.run()  # erreurs d'un tour de boucle rattrapées là (TeamBot.safe_step) : jamais d'arrêt en plein combat
        if args.banque:
            arrived = bot.roamer is not None and bot.roamer.traveler.coords == BANK_MAP and not bot._lost
            if not arrived:
                log.warning("Banque : l'équipe n'est pas toute sur la carte de la banque (%s,%s) : pas de passage au coffre",
                            *BANK_MAP)
                return 1
            return 0 if len(BankVisit(members).run()) == len(members) else 1
    except BotStopped as stop:
        log.info("Bot arrêté (%s)", stop)
    except KeyboardInterrupt:
        log.info("Bot arrêté (Ctrl+C)")
    except Exception:
        # Erreur hors de la boucle (démarrage, donjon hors des combats) : au journal avec une capture de chaque fenêtre.
        log.exception("Erreur inattendue : le bot s'arrête")
        _capture_windows(locals().get("members") or [], "erreur_fatale")
        return 1
    finally:
        for m in members if "members" in locals() else []:
            try:
                m.game.release_keys(_held_keys(settings))
            except Exception:  # fenêtre fermée : rien à relâcher
                pass
        if signals is not None:
            signals.stop()
        control.close()
        _keep_summary(recorder)
    return 0


def _capture_windows(members: list, label: str) -> None:
    """Une capture de chaque fenêtre de l'équipe (au plus une par cause et par fenêtre toutes les 5 min)."""
    for member in members:
        try:
            path = capture_once(member.game, member.game.look(park=False).image, label)
            if path:
                log.info("Capture de %s : %s", member.name, path)
        except Exception:  # noqa: BLE001 : fenêtre fermée, capture impossible
            pass


def _keep_summary(recorder: Recorder) -> None:
    """Fin du lancement : son bilan chiffré dans data/bilans/ (bot/debug/bilans.py)."""
    path = archive(recorder.dir)
    if path is not None:
        log.info("Bilan chiffré de la session : %s", path)


def _held_keys(settings: dict) -> list[str]:
    """Touches que le bot tient enfoncées un moment : contours des sorties (A), joueurs cachés (E), groupes (Z)."""
    travel, engage = settings.get("travel") or {}, settings.get("engage") or {}
    held = [travel.get("show_exits_key") or "a", str(travel.get("hide_players_key", "e") or ""),
            engage.get("show_groups_key") or "z"]
    return [key for key in dict.fromkeys(held) if key]


if __name__ == "__main__":
    sys.exit(main())
