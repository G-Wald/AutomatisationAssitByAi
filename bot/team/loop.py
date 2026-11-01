"""Mode équipe : plusieurs personnages, une fenêtre du jeu chacun, menés ensemble (vue d'ensemble : bot/team/__init__.py).

Le principal cherche les groupes, attaque et choisit le chemin, avec les briques du mode solo (brain/engage.py,
brain/travel.py) ; les autres le suivent. Chaque personnage a son jeu, ses sorts et son IA de combat (team/member.py).

- Hors combat : tour des fenêtres (suiveurs d'abord, le principal reste devant) au démarrage, après chaque combat et
  au plus toutes les team.round_every secondes : fenêtres de résultat et de niveau supérieur fermées, fenêtre des
  sorts et caractéristiques lues une fois par personnage (ou reprises de la dernière session), vie notée (repos de
  tous ceux qui en ont besoin, assis en même temps).
- Combat lancé par l'un d'eux (le principal qui attaque, ou un suiveur agressé) : les autres le rejoignent par l'épée
  sans s'attendre (team/join.py) ; avec team.place, toute l'équipe se place ensuite selon l'ordre de jeu
  (team/placement.py : quelques passes de clics dans toutes les fenêtres, une lecture des cases par passe, et la case
  de chacun de ceux qui ont bougé est connue) ; puis Prêt dans chaque fenêtre (F1), la sienne en dernier : le combat
  ne commence pas avant.
- Tours : la timeline dit qui joue (team/turns.py). Tour d'un monstre : on regarde dans la dernière fenêtre active,
  sans changer de fenêtre. Tour d'un personnage : sa fenêtre, son IA (play_turn) avec `is_mine` (c'est bien SON tour)
  et sa case (`me_cell`, suivie d'un tour à l'autre ; à défaut `find_me` : le seul cercle de l'équipe qui n'est pas
  celui d'un autre personnage), puis F1.
- Changement de carte : le principal prend le soleil ; ensuite chaque suiveur clique sur la même case dans sa fenêtre
  (même carte, donc même case, quelle que soit la taille de la fenêtre), puis on vérifie l'arrivée de chacun.
"""

import inspect
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

import numpy as np

from bot.actions import keys
from bot.alerts import tell_user
from bot.actions.stop import BotStopped
from bot.brain.arena import Arenas, walk_area
from bot.brain.combat import MapMemory
from bot.brain.engage import Engager, EngageSettings
from bot.brain.loop import PLACEMENT_FIRST_TRY, PLACEMENT_GIVE_UP, SETTLE_AFTER_READY, SETTLE_SECONDS
from bot.brain.popups import close_any_menu, close_context_menu, close_flash_menu
from bot.brain.position import PositionAsker
from bot.brain.recovery import capture_once
from bot.brain.rest import CHECK_EVERY, STOOL, STOOL_SPREAD, STOOL_ZONE, RestSettings
from bot.brain.travel import (RECLICK_EVERY, SCENE_CHANGED, SCENE_STABLE, Journey, TravelSettings, Traveler,
                              ZoneRoamer, make_roamer, merge_reading, same_map)
from bot.brain.travel import _click_spot, _gap, _motion, _motion_view, _scene  # mêmes repères que le principal
from bot.brain import possessions
from bot.team.bank import BANK_MAP, BankVisit
from bot.team.keys import kept_key
from bot.team.trade import CentralBank
from bot.debug.draw import annotate
from bot.debug.report import write_fight_report
from bot.perception.colors import BLUE, RED
from bot.perception.exits import DELTA, OPPOSITE
from bot.perception.fighters import Fighter, clear_point, find_fighters
from bot.perception.frame import Frame
from bot.perception.grid import Cell, distance
from bot.perception.maplabel import MapLabel, read_map_label
from bot.perception.placement import Placement, entry_banner, read_placement, standing_cells
from bot.perception.sight import read_blocks
from bot.perception.popups import find_context_menu, find_flash_menu, find_known_popup
from bot.perception.templates import match_template
from bot.perception.timeline import Portrait, current_portrait, same_portrait
from bot.perception.ui import READY_SPREAD, hp_ratio, pods_fill, tactical_mode
from bot.state.scene import Phase, Scene, read_scene
from bot.perception.chat import combat_lines, died
from bot.team.join import Joiner, recent_click
from bot.connection.group import Player, friend_levels
from bot.team.member import load_level, save_level, Member, to_reference
from bot.team.placement import Seat, by_elimination, next_moves, plan_seats
from bot.team.recovery import RecoverySettings, Watchdog
from bot.team.roster import TeamSettings
from bot.team.turns import TurnRouter

log = logging.getLogger(__name__)

FIGHT_END_CONFIRM = 1.0  # hors combat vu aussi longtemps pendant le combat : il est fini (pas un instant de transition)
FIGHT_HEARD_BEFORE = 120.0  # s : début du combat (GJK) entendu au plus ce délai avant les tours (placement compris)
# (aussitôt si une fenêtre est ouverte sur la carte : le résultat du combat ; le 01/10, 8 s entre le dernier coup et
# la recherche du groupe suivant, dont 2 s de confirmation et 2 s d'attente fixe)
# Début du tour d'un allié au portrait pas encore reconnu : son timer se remplit peu à peu (visible ~1 s après le
# début du tour). On l'attend dans la fenêtre regardée avant d'aller voir les autres (01/10 : allers-retours inutiles
# entre les deux fenêtres, 4 s avant le premier tour de Jesuisleking).
TIMER_FILL = 1.2
COMBAT_START_WAIT = 15.0  # Prêt partout -> timeline ; au-delà, quelqu'un n'est sans doute pas prêt
SEARCH_RETRY = 0.8  # tour d'un allié sans timer dans aucune fenêtre : nouveau tour des fenêtres après ce délai
# Avant de conclure à une invocation : le timer se remplit peu à peu (visible ~1 s après le début du tour) ; le 01/10,
# au bout de 0,8 s, le premier tour de Jesuisleking a été pris pour celui d'une invocation (corrigé à 1,6 s).
OTHER_AFTER = 2.5
# Notification du tour d'un personnage (team.turn_signal: notifications) : son timer est attendu dans SA fenêtre, sans
# aller voir les autres (03/10 : parti chercher ailleurs après TIMER_FILL, le tour d'Imthebest vu 3,9 s après sa
# notification au lieu de ~0,8 s). Au-delà, la recherche habituelle reprend (notification trompeuse).
HINT_PATIENCE = 3.0
# Notification de son tour et timeline de mon équipe dans sa fenêtre : il joue sans attendre que son timer se remplisse
# (~1 s ; idée de l'utilisateur, 03/10). Pendant au plus EARLY_START s, ou jusqu'à ce que son timer s'affiche, c'est son
# tour pour la vérification « est-ce encore mon tour ? » (is_mine) ; ensuite la règle habituelle (portrait, timer).
EARLY_START = 1.5
# Entre Prêt et la timeline, l'écran ressemble quelques secondes à du hors combat (et le bouton de fin de tour peut
# s'afficher un instant avant : « combat, tour de l'équipe ? ») : tant qu'aucun tour n'a été lu dans la timeline, la
# fin du combat sans fenêtre de résultat n'est conclue qu'au bout de ce délai (03/10 : « Combat terminé » 1 s après
# Prêt, 2 combats sur 9, puis combat repris sans savoir qui est où : déplacements et sorts faux).
START_CONFIRM = 2 * SETTLE_AFTER_READY
# Juste après la fin du tour d'un personnage, la timeline encadre encore son portrait un instant : pas de nouveau tour
# pour lui avant ce délai (03/10 : Walitcoin a rejoué 0,2 s après son F1, sorts refusés, déplacement pendant un autre tour).
REPLAY_GUARD = 1.5
STUCK_TURNS = 3  # tours d'affilée où la main ne passe pas : ce personnage n'est plus cru en train de jouer...
STUCK_PAUSE = 30.0  # ... pendant ce temps (16/10 : 79 « La main ne semble pas être passée » de Dinarpepe, 14 min)
# s ajoutées au hasard après chaque Prêt (en plus de son geste). 20/10 (utilisateur) : Prêt est un geste très rapide en
# multicompte (changer de fenêtre, F1) ; 0,05-0,6 s donnait 8 Prêt en 4,2 s.
READY_GAP = (0.03, 0.2)
# s au-delà de la durée de la session (16/10 : 30 min prévues, 43 min) : fin de session après ce combat, annoncée une
# fois. Jamais d'arrêt en plein combat (17/10 : un test arrêté en plein combat a laissé Pesopepe mourir) : avant le
# 21/10, le bot s'arrêtait ici et laissait l'équipe sans lui.
OVERTIME = 300.0
# Erreurs rattrapées (TeamBot.safe_step, 21/10) : en combat, on continue (fin de session après le combat à la
# MAX_FIGHT_ERRORS-ième) ; hors combat, arrêt propre à la MAX_ERRORS-ième en ERRORS_WINDOW s.
MAX_FIGHT_ERRORS = 5
MAX_ERRORS = 3
ERRORS_WINDOW = 600.0
# Combat fini sans fenêtre de résultat vue, et rien fermé chez le principal au tour des fenêtres : ses fenêtres
# regardées de nouveau ce délai plus tard (20/10, journal 201321 : résultat reconnu de justesse, resté ouvert).
RESULT_RECHECK = 2.0
# Pendant le tour d'un personnage, ni son timer ni son portrait encadré : revu sur une 2e capture avant de conclure
# que son tour est fini (11/10, idée validée par l'utilisateur).
TURN_MARKS_RECHECK = 0.2
POD_FULL = 0.9  # pods : à partir de là, direction la banque (demande de l'utilisateur, 11/10)
# Agression d'un personnage sur une autre carte (26/10, idée de l'utilisateur, écoute du serveur) : ceux qui en sont à
# HELP_MAX_MAPS cartes au plus y vont pendant le placement (~30 s : « GJK...|30000 »), puis rejoignent son combat.
HELP_MAX_MAPS = 2
HELP_PLACEMENT = 24.0  # s après le début du placement : les aides doivent être arrivés (rejoindre prend ~5 s)
HELP_WALK = 12.0  # s au plus pour un changement de carte des aides
POD_LOG_STEP = 0.05  # pods notés au journal quand ils ont changé d'au moins 5 %
# Pods en sortant de la banque : l'équipement et les objets hors ressources restent (27/10 : Pesopepe 42 -> 30 %,
# Waldarch 38 -> 25 %). Pas de nouveau passage avant BANK_FLOOR_MARGIN de plus : sinon, aller-retour sans fin.
BANK_FLOOR_MARGIN = 0.05
# Le principal ne passe pas un soleil que ses suiveurs passent : personnage bloqué par le jeu (11/10, 1,-13 : rare,
# débloqué par l'utilisateur avec un défi lancé à un autre personnage puis quitté). Au bout de LEADER_STUCK fois de
# suite, le bot s'arrête et le dit.
LEADER_STUCK = 2
# s d'immobilité vue avant de recliquer le soleil d'un suiveur (en marche, jamais), comptées à partir du premier regard
# sur lui : 20/10, 22 reclics sur 29 partaient dès ce premier regard (immobilité comptée depuis le clic), dont 15 sur
# des suiveurs qui arrivaient déjà (Pombro, Vertdepomme, Vieenjaune, les derniers cliqués).
FOLLOWER_STILL = 1.5
# Clic d'un suiveur sur le soleil sans déplacement annoncé par le serveur dans ce délai : pas pris (01/11 : le serveur
# répond à un clic de déplacement en ~0,2 s ; hors combat, GA0;1 arrive aussi pour chaque suiveur).
NET_SUN_SILENT = 1.0
# Reclics d'un suiveur resté immobile sur la carte de départ ; au suivant (~9 s après son clic), il fait son propre
# trajet au lieu d'attendre travel.walk_timeout (20 s).
FOLLOWER_RECLICKS = 2
MAX_POPUPS = 6  # regards d'affilée dans une même fenêtre du jeu : fermer résultat, niveau supérieur, et retenter
RESULT = "résultat du combat"  # même fenêtre, même bouton « Fermer » dans chaque fenêtre du combat
RESULT_LAG = 0.2  # elle s'ouvre partout en même temps, à un instant près
# Fin du combat d'après le serveur (GE) : la carte est rechargée (GDK) ~3,5 s plus tard dans chaque fenêtre, et le
# résultat ne s'ouvre qu'après (01/11, journal 113029 : « fini sans fenêtre de résultat » conclu 1 s après GE, 24
# combats sur 31 ; fenêtres fermées ensuite une à une, le principal déjà reparti au combat suivant).
END_LOAD_WAIT = 8.0  # s au plus après GE pour la carte rechargée chez tous les combattants
RESULT_OPEN_WAIT = 4.0  # s au plus, carte rechargée, pour voir chaque fenêtre de résultat s'ouvrir et la fermer
UNKNOWN_TINT_STOP = 3  # personnages au sol tactique pas reconnu dans un combat : arrêt (pas une fiche passagère)
# Placement de toute l'équipe (team.place, team/placement.py). Une passe : un clic dans la fenêtre de chacun de ceux
# qui bougent, puis une lecture des cases (PLACE_SETTLE après le premier clic, encore PLACE_RECHECK plus tard si un
# déplacement ne se voit pas). Au plus PLACE_PASSES passes et PLACE_BUDGET s : avec 8 personnages sur les 8 à 12 cases
# d'une équipe à Incarnam (client du jeu : 34 cartes à 8 cases, 46 à 9, 45 à 10), 0 à 4 cases libres seulement, et
# chacune ne sert qu'à un personnage par passe.
PLACE_PASSES = 3
PLACE_BUDGET = 5.0
PLACE_SETTLE = 0.35
PLACE_RECHECK = 0.3
PLACE_COLOUR_MARGIN = 2  # écart de cases occupées (rapporté à l'équipe) pour reconnaître ma couleur sans bouger


@dataclass(frozen=True)
class Crossing:
    """Passage du principal sur une carte voisine, à refaire par les suiveurs."""

    side: str
    cell: Cell  # case du soleil
    before: MapLabel  # carte quittée
    after: MapLabel  # carte d'arrivée


# Attente des suiveurs (_wait_followers) prolongée tant que l'un bouge encore (immobile depuis moins de MOVING_RECENTLY s),
# jusqu'à WALK_EXTENSION fois travel.walk_timeout.
MOVING_RECENTLY, WALK_EXTENSION = 2.0, 2.0
# Suiveur perdu au nom de carte illisible : carte relue LOOK_AGAIN fois, LOOK_AGAIN_PAUSE s d'écart (_look_again).
LOOK_AGAIN, LOOK_AGAIN_PAUSE = 3, 1.5


@dataclass
class _Walk:
    """Suiveur en route vers le soleil, regardé à tour de rôle (_wait_followers)."""

    member: Member
    departure: np.ndarray  # décor de la carte de départ (_scene)
    clicked: float  # heure du dernier clic sur le soleil
    spot: int = 0  # endroit de la case visé au dernier clic
    previous: np.ndarray | None = None  # décor au regard précédent
    motion: np.ndarray | None = None  # vue du mouvement au regard précédent
    still_since: float | None = None  # immobile depuis (vu à partir du premier regard ; None : pas encore regardé)
    reclicks: int = 0  # reclics faute de bouger (pas ceux d'un menu ouvert sur le soleil)
    menu_saved: bool = False  # capture d'un menu ouvert sur le soleil déjà gardée


def _reads_as(label: MapLabel | None, coords: tuple[int, int] | None, known: MapLabel | None) -> bool | None:
    """Le nom de carte lu dans une fenêtre désigne-t-il la carte `coords` (celle que croit le principal) ? True :
    coordonnées lues égales, ou lues en partie sans contradiction (merge_reading : « 11,-?3 » pour 11,-23, au moins un
    chiffre lu) ; False :
    une autre carte ; None : on ne peut pas le dire au nom (illisible, lecture partielle qui ne colle pas). Sans
    `coords`, comparaison au nom `known` lu par le principal.

    20/10, 11,-23 : le principal lisait « 11,-2? », ses suiveurs « 11,-23 », « 11,-?3 » ou « 11,-? » ; comparés nom
    contre nom, 6 suiveurs crus « pas sur la carte du principal », le bot arrêté."""
    if label is None or not label.readable:
        return None
    if coords is not None:
        if label.coords is not None:
            return label.coords == coords
        digits = any(c.isdigit() for c in label.text)  # « ?,? » ne contredit rien, mais ne dit rien non plus
        return True if digits and merge_reading(label.text, coords)[1] else None
    if known is None or not known.readable:
        return None
    return label.same_map(known)


def _accepts(function, name: str) -> bool:
    try:
        return name in inspect.signature(function).parameters
    except (TypeError, ValueError):
        return False


def _standing(frame: Frame, placement: Placement) -> list[Fighter]:
    """Combattants debout sur les cases de placement (un clic sur l'un d'eux le viserait, lui)."""
    return [Fighter(team, *frame.grid.center(c), c) for team, cells in placement.occupied.items() for c in cells]


def _same_turn(a: Portrait | None, b: Portrait | None) -> bool:
    return (a is None and b is None) or same_portrait(a, b)


def _live_net(member: Member):
    """L'écoute du serveur de ce personnage, si elle est vivante ; None sinon."""
    net = getattr(member.game, "net", None)
    return net if net is not None and net.alive() else None


def _popup_views(members: list[Member]) -> list[tuple[Member, Frame, object]]:
    """Capture de chaque fenêtre et fenêtre connue vue dessus (find_known_popup), dans l'ordre de `members`. En
    arrière-plan, toutes en même temps : ~0,15 s de recherche par capture où une fenêtre est ouverte, 8 fenêtres
    regardées l'une après l'autre prenaient ~3,5 s (01/11). Au premier plan, chacune doit passer devant : l'une après
    l'autre."""

    def view(member: Member) -> tuple[Member, Frame, object]:
        frame = member.game.look(park=False)
        return member, frame, find_known_popup(frame, member.popups.specs)

    if len(members) < 2 or not all(getattr(m.game, "background", False) for m in members):
        return [view(m) for m in members]
    with ThreadPoolExecutor(max_workers=len(members)) as pool:
        return list(pool.map(view, members))


def _net_moved(net) -> float:
    """Dernier déplacement ou changement de carte de ce personnage annoncé par le serveur (time.monotonic())."""
    return max(getattr(net, "moved_at", 0.0), getattr(net, "map_at", 0.0))


def _same_button(seen, known, tolerance: float) -> bool:
    """Le bouton « Fermer » du résultat au même endroit sur deux captures : fenêtre ouverte, immobile."""
    return (seen is not None and seen.spec.name == RESULT and seen.close_point is not None
            and max(abs(a - b) for a, b in zip(seen.close_point, known.close_point)) <= tolerance)


def _name_asker(roamer, name: str) -> None:
    """« /p %pos% » répond dans le canal du groupe : la ligne lue doit être celle de `name`. 26/10 (journal 173905) : le
    nom venait du titre de la fenêtre, qui ne le porte plus (« Dofus Retro v1.49.8 ») ; la position d'un autre membre
    lue (-4,-20 pour -1,-19), le principal cru téléporté, plus aucun changement de carte."""
    asker = getattr(getattr(roamer, "traveler", None), "asker", None)
    if asker is not None:
        asker.name = name


def _short_route(passages, start: tuple[int, int], goal: tuple[int, int],
                 most: int) -> list[tuple[str, tuple[int, int]]] | None:
    """Plus court chemin de `start` à `goal` en `most` cartes au plus, par les sorties de la référence complétée :
    [(côté, carte d'arrivée), ...] ; None au-delà ou sans chemin connu."""
    frontier = [(start, [])]
    seen = {start}
    for _ in range(most):
        nxt = []
        for here, route in frontier:
            for side, dest in passages.referenced(here).items():
                if dest in seen:
                    continue
                if dest == goal:
                    return route + [(side, dest)]
                seen.add(dest)
                nxt.append((dest, route + [(side, dest)]))
        frontier = nxt
    return None


def bank_visit(members, hide_key: str, mode: str, keep: int | None = None):
    """Passage à la banque selon team.banque_mode : « principal » (team/trade.py) ou « chacun » (team/bank.py).
    `keep` : clé de donjon reprise du coffre après le dépôt (donjon.garder_cle, team/keys.py ; 30/10)."""
    return CentralBank(members, hide_key, keep) if mode == "principal" else BankVisit(members, hide_key, keep)


def _told(member: Member, what: str) -> float | None:
    """Vie ou pods de ce personnage d'après le serveur (écoute : connection/network.py, « As », « Ow ») ; None sans
    écoute ou pas encore annoncés : l'écran (cœur, jauge des pods)."""
    net = getattr(member.game, "net", None)
    return getattr(net, what)() if net is not None and net.alive() else None


def _circle_colour(member: Member) -> str | None:
    """Couleur du cercle sur la case de ce personnage annoncée par le jeu (écoute réseau) ; None sans elle, ou si
    aucun cercle ne s'y voit."""
    net = getattr(member.game, "net", None)
    cell = net.my_grid_cell() if net is not None else None
    if cell is None:
        return None
    circle = next((f for f in find_fighters(member.game.look(park=False)) if f.cell == cell), None)
    return circle.team if circle is not None and circle.team else None


def _died(member: Member) -> bool:
    """Fin de combat : ce personnage est mort (perte d'énergie annoncée dans SON chat de combat)."""
    lines = combat_lines(member.game.look(park=False))
    return bool(lines) and died(lines)


class TeamBot:
    # Notifications de Windows (team/signals.py, TurnSignals), branchées par bot/__main__.py quand team.turn_signal
    # vaut notifications : indice de qui joue, signaux seulement pendant l'attente d'un tour (_turns, _player) ; en
    # dehors, les notifications sont seulement enregistrées.
    signals = None
    # Reconnexion (connection/manager.py, Watcher), branchée par bot/__main__.py avec --connexion : titres des fenêtres
    # relus à chaque tour de boucle ; personnage déconnecté reconnecté, puis réinvité dans le groupe hors combat.
    connection = None
    # Sessions de tests (11/10, --combats, --minutes) : arrêt après tant de combats, ou passé cette heure (horloge
    # time.monotonic), une fois les fenêtres de résultat fermées (tour des fenêtres). 0 / None : sans limite.
    max_fights = 0
    deadline: float | None = None

    def __init__(self, members: list[Member], settings: dict, team: TeamSettings | None = None, act: bool = True,
                 engage: bool | None = None, travel: str | None = None, start: tuple[int, int] | None = None,
                 region: str | None = None) -> None:
        """`members` : le principal d'abord (roster.pick_team). Autres options : celles du mode solo (brain/loop.py)."""
        if not members:
            raise ValueError("Équipe vide")
        combat = settings.get("combat") or {}
        timing = settings.get("timing") or {}
        self.members = members
        self._settings = settings  # réglages bruts : niveaux retenus des personnages (learn_levels)
        self.leader, self.followers = members[0], members[1:]
        self.by_name = {m.name: m for m in members}
        self.team = team or TeamSettings.from_settings(settings)
        self.act = act
        engage_settings = EngageSettings.from_settings(settings)
        self.travel_settings = TravelSettings.from_settings(settings, travel, start, region)
        # Pods pleins (POD_FULL) en mode zone : aller-retour à la banque d'Astrub (team.banque_auto, case de l'interface).
        self.bank_auto = bool((settings.get("team") or {}).get("banque_auto", True))
        # Seuil réglable (team.pods_banque) : un test de la banque sans attendre des pods pleins (27/10).
        self.pods_full = float((settings.get("team") or {}).get("pods_banque", POD_FULL))
        # chacun : chacun dans son coffre ; principal : tout au coffre du principal, par lots (team/trade.py, 30/10).
        self.bank_mode = str((settings.get("team") or {}).get("banque_mode", "chacun")).strip().lower()
        # Clé de donjon reprise du coffre après le dépôt (donjon.garder_cle, team/keys.py, 30/10) ; None : rien.
        self.keep_key = kept_key(settings)
        if self.travel_settings.mode in ("test", "explore", "aller"):
            engage = False  # déplacements (test, exploration, trajet) : pas de combat lancé par le bot
        enabled = engage_settings.enabled if engage is None else engage
        self.engager = Engager(self.leader.game, engage_settings) if enabled else None
        if self.engager is not None and engage_settings.max_level_auto:
            self.engager.team_levels = [load_level(settings, m.name) for m in members]
            missing = [m.name for m, level in zip(members, self.engager.team_levels) if level is None]
            known = [level for level in self.engager.team_levels if level is not None]
            log.info("Niveau maximal des groupes : %s", f"{self.engager.level_limit()} ({engage_settings.level_ratio:g} x "
                     f"niveau cumulé {sum(known)}" + (f", {', '.join(missing)} à la moyenne des autres" if missing else "")
                     + ")" if self.engager.level_limit() != engage_settings.max_level or not missing else
                     f"{engage_settings.max_level} (niveau pas encore connu : {', '.join(missing)} ; retenu au prochain "
                     "« Tu passes niveau N »)")
        self.roamer = make_roamer(self.leader.game, self.travel_settings)
        _name_asker(self.roamer, self.leader.name)
        self._crossings: list[Crossing] = []
        # Suiveurs partis vers le soleil en même temps que le principal (_dispatch) : capture de départ, heure du clic.
        self._dispatched: dict[str, tuple[Frame, float]] = {}
        self._pods: dict[str, float] = {}  # remplissage de la jauge des pods de chacun (0 à 1), au dernier tour des fenêtres
        self._bank_floor: dict[str, float] = {}  # pods de chacun en sortant de la banque (BANK_FLOOR_MARGIN)
        self._banked = False  # passé à la banque pendant cette session : premiers pods lus ensuite = _bank_floor
        self._lost: set[str] = set()  # suiveurs qui n'ont pas suivi : pas de clic à l'aveugle tant qu'ils n'ont pas rejoint
        self._seen_at: dict[str, tuple[int, int]] = {}  # dernière carte connue d'un suiveur perdu
        self._rejoin: dict[str, Journey] = {}  # trajet d'un suiveur perdu jusqu'à la carte du principal (_regroup)
        self._departure: tuple[int, int] | None = None  # carte que le principal vient de quitter
        self._together_checked = False  # équipe vue sur la carte du principal avant le premier trajet
        self._flash_checked: set[str] = set()  # combat en cours : fenêtres où un menu de Flash oublié a été cherché
        self._dispatch_to: tuple[int, int] | None = None  # carte où mène le soleil cliqué par toute l'équipe
        self._dispatch_side: str | None = None  # côté de ce soleil
        self._leader_stuck = 0  # fois de suite où les suiveurs sont passés sans le principal
        if self.roamer is not None:
            self.roamer.traveler.on_crossed = self._crossed
            self.roamer.traveler.on_click = self._dispatch
        if self.engager is not None and self.roamer is not None:  # zones hors d'atteinte gardées (brain/engage.py)
            self.engager.remember_in(self.roamer.traveler.world, self.roamer.traveler.place)
            self.engager.walk_area = self._walk_area  # groupes injoignables d'après la carte officielle
        if self.engager is not None:
            # Z sans infobulle : clavier sondé par la touche A (fenêtre sourde, 20/10), dès la 1re recherche vide en
            # mode zone (avant de changer de carte), après deux sinon.
            self.engager.probe_key = self.travel_settings.show_exits_key
            self.engager.probe_first = self.roamer is not None
        self.rest_settings = RestSettings.from_settings(settings)
        # Repos seulement quand le bot circule lui-même (combats enchaînés, déplacements), comme en solo.
        self.rest_enabled = (self.engager is not None or self.roamer is not None) and self.rest_settings.enabled
        for key in (self.team.ready_key, engage_settings.show_groups_key, self.travel_settings.show_exits_key):
            if key:
                keys.resolve(key)  # erreur de config signalée au démarrage
        my_team = str(combat.get("my_team", "auto")).lower()
        if my_team not in ("auto", RED, BLUE):
            raise ValueError(f"combat.my_team doit valoir auto, red ou blue (pas {my_team!r})")
        self.learn_team = my_team == "auto"
        self.my_team = None if self.learn_team else my_team  # couleur de l'équipe : la même pour tous les personnages
        # Équipe 0 du serveur (GP) = cases rouges : supposition contrôlée à l'écran à chaque combat (31/10). True :
        # accord vu à ce lancement, le serveur tranche quand l'écran ne peut pas ; False : un désaccord, plus jamais.
        self._told_colour_ok: bool | None = None
        self.auto_ready = bool(combat.get("auto_ready", True))
        self.poll = float(timing.get("poll", 0.15))
        self.router = TurnRouter([m.name for m in members])
        self.joiner = Joiner(self.team.join_timeout)
        self.joiner.hide_key = self.travel_settings.hide_players_key  # joueurs cachés pendant le clic sur l'épée
        if self.roamer is not None:  # carte du combat quand son nom ne se lit pas : suiveur ailleurs (team/join.py)
            self.joiner.fight_coords = lambda: self.roamer.traveler.coords
        self.finished = False
        self.fights = 0  # combats finis depuis le lancement
        self._stop_reason = ""  # limite de la session atteinte : arrêt après le prochain tour des fenêtres
        self._at_meeting: set[str] = set()  # suiveurs arrivés au rendez-vous de la zone, qui attendent le principal
        self._behind_zaap: set[str] = set()  # suiveurs restés au zaap de départ du principal : il les attend à l'arrivée
        self._last_team = RED  # couleur du dernier combat : sert au placement, avant de savoir celle de celui-ci
        self._combat_ended_at: float | None = None
        self._next_engage_at = 0.0
        self._round_due = True  # tour des fenêtres : au démarrage, puis après chaque combat
        self._last_round = 0.0
        self._tired: list[Member] = []  # vie basse, vue au dernier tour des fenêtres
        self._empty_searches = 0
        self._summaries: dict[str, str] = {}
        self._checked_layout: set[str] = set()
        # Tour d'allié introuvable : (portrait, essais, heure du dernier essai, heure du premier).
        self._search: tuple[Portrait | None, int, float, float] | None = None
        self._cells: dict[str, Cell] = {}  # case de chaque personnage dans le combat en cours (placement, puis fin de tour)
        self._warned_is_mine = False
        self._turn_seen: tuple[Portrait | None, float] | None = None  # portrait du tour en cours, vu pour la 1re fois
        self._hinted: tuple[str, float] | None = None  # personnage signalé par notification, heure du signal
        self._early: tuple[str, float] | None = None  # joue sur notification, sans son timer : (nom, jusqu'à quand)
        self._just_played: tuple[str, float] | None = None  # dernier personnage à avoir joué, heure de fin de son tour
        self._stuck: dict[str, float] = {}  # personnage -> fin de sa mise à l'écart (_unless_stuck)
        # Reprise après erreur (21/10, team/recovery.py). Progrès : combat lancé ou fini, carte changée, suiveur arrivé,
        # tour joué ; la surveillance (hors combat) cherche ce qui bloque quand il n'y en a plus. Progrès « faible »
        # (_progress(soft=True) : nouvel essai d'un suiveur en route, sans résultat encore) : _progress_at seulement,
        # pas _real_progress_at (team/recovery.py, SOFT_PROGRESS_LIMIT).
        self._progress_at = time.monotonic()
        self._real_progress_at = self._progress_at
        self._rejoin_seen: tuple = ()  # essais des trajets des suiveurs perdus au tour de boucle précédent
        self._hp: dict[str, float] = {}  # vie de chacun (0 à 1) au dernier tour des fenêtres
        self._hp_report_due = False  # vie de l'équipe au journal au premier tour des fenêtres après un combat
        self._seated_after: dict[str, float | None] = {}  # assis pendant le repos d'un autre : après quelle fin de combat
        self._lost_seen = 0  # suiveurs perdus au dernier tour de boucle (un de moins : il est arrivé)
        self._in_fight = False  # dans _fight : une erreur y est rattrapée sans arrêter le bot
        self._fight_errors = 0  # erreurs rattrapées pendant le combat en cours
        self._errors: list[float] = []  # heures des erreurs rattrapées hors combat
        self._overtime_warned = False
        self._result_unseen = False  # dernier combat fini sans fenêtre de résultat vue
        self._told_end = False  # dernier combat fini d'après le serveur (_close_told_results)
        self._departure_map: int | None = None  # carte quittée par le principal d'après le serveur (_dispatch)
        self._result_check_at: float | None = None  # ... et rien fermé chez le principal : nouveau regard à cette heure
        self._leader_closed = False  # le dernier tour des fenêtres a fermé une fenêtre chez le principal
        control = getattr(self.leader.game, "control", None)
        self.watchdog = Watchdog(self, RecoverySettings.from_settings(settings), control,
                                 enabled=act and (self.engager is not None or self.roamer is not None))

    def run(self) -> None:
        # 25/10 (journal 175311) : progrès compté depuis la création du TeamBot, avant le groupe et la reconnexion
        # (bot/__main__.py : 29 s) ; « aucun progrès depuis 247 s » quand la boucle tournait depuis 3 min 38.
        self._progress("début de la boucle")
        while not self.finished:
            self.safe_step()

    def safe_step(self) -> None:
        """Un tour de boucle (step), ses erreurs rattrapées (21/10) : en combat, l'erreur et une capture au journal,
        puis on continue (jamais d'arrêt en plein combat : à MAX_FIGHT_ERRORS, fin de session après ce combat) ; hors
        combat, arrêt propre à la MAX_ERRORS-ième erreur en ERRORS_WINDOW s."""
        try:
            self.step()
        except (BotStopped, KeyboardInterrupt):
            raise
        except Exception as error:  # noqa: BLE001 : toute erreur du bot, pas celles de l'arrêt demandé
            self._step_failed(error)

    def _step_failed(self, error: Exception) -> None:
        in_fight, self._in_fight = self._in_fight, False
        log.exception("Erreur %s : %s", "pendant le combat" if in_fight else "hors combat", error)
        try:
            game = self._front_member().game
            path = capture_once(game, game.look(park=False).image, "erreur")
            if path:
                log.info("Capture de l'erreur : %s", path)
        except Exception:  # noqa: BLE001 : fenêtre fermée, capture impossible : l'erreur est déjà au journal
            pass
        if in_fight:
            self._fight_errors += 1
            if self._fight_errors >= MAX_FIGHT_ERRORS and not self._stop_reason:
                log.warning("%d erreurs pendant ce combat : je le finis, puis je m'arrête", self._fight_errors)
                self._stop_reason = f"{self._fight_errors} erreurs pendant un combat"
            self._round_due = True  # combat repris au tour des fenêtres (ou, fini, ses fenêtres fermées)
            self.leader.game.sleep(1.0)
            return
        now = time.monotonic()
        self._errors = [t for t in self._errors if now - t < ERRORS_WINDOW] + [now]
        if len(self._errors) < MAX_ERRORS:
            self._round_due = True
            return
        log.error("%d erreurs hors combat en %.0f min : je m'arrête proprement", len(self._errors), ERRORS_WINDOW / 60)
        self._stop_reason = f"{len(self._errors)} erreurs hors combat"
        try:
            fighting = any(read_scene(m.game.look(park=False)).phase is not Phase.OUT_OF_COMBAT for m in self.members)
        except Exception:  # noqa: BLE001
            fighting = False
        if fighting:
            self._round_due = True  # un combat vient de commencer : fini d'abord, arrêt ensuite (_round)
        else:
            self.finished = True

    def _front_member(self) -> Member:
        return next((m for m in self.members if m.in_front()), self.leader)

    def _progress(self, what: str, soft: bool = False) -> None:
        """Progrès de l'équipe (surveillance : team/recovery.py). `soft` : le bot agit, sans résultat encore (essai
        d'un suiveur en route) ; il ne repousse la surveillance que jusqu'à SOFT_PROGRESS_LIMIT s après le dernier
        vrai progrès."""
        self._progress_at = time.monotonic()
        if not soft:
            self._real_progress_at = self._progress_at
        self.watchdog.progress()
        log.debug("Progrès : %s", what)

    def _rejoin_attempts(self) -> tuple:
        """Où en sont les trajets des suiveurs perdus (_regroup) : par suiveur, cartes passées, échecs d'affilée,
        itinéraires recalculés (brain/travel.Journey)."""
        return tuple(sorted((name, getattr(j, "crossed", 0), getattr(j, "failures", 0), getattr(j, "reroutes", 0))
                            for name, j in self._rejoin.items()))

    def _note_rejoin_attempts(self) -> None:
        """Un nouvel essai d'un suiveur en route (sortie essayée sans succès, nouvel itinéraire) : progrès faible.

        25/10 (journal 180222) : 7 suiveurs perdus en 0,-33, chacun essayant 2 à 4 soleils de la sortie de droite l'un
        après l'autre (~20 s chacun), aucune carte changée : « aucun progrès » à 147 s, pause du bot à 280 s alors
        qu'il travaillait ; journal 175311, même chose depuis 1,-33, premier changement de carte d'un suiveur 3 min 58
        après le début du regroupement (« aucun progrès » à 247 s, pause à 332 s). Les trajets sont bornés
        (brain/travel.py : JOURNEY_FAILURES échecs d'affilée, JOURNEY_REROUTES itinéraires, puis arrêt du bot « ne peut
        pas rejoindre ») ; la surveillance reprend de toute façon SOFT_PROGRESS_LIMIT s après le dernier vrai progrès."""
        attempts = self._rejoin_attempts()
        if attempts != self._rejoin_seen:
            if attempts:
                self._progress("suiveur en route : nouvel essai", soft=True)
            self._rejoin_seen = attempts

    def step(self) -> None:
        if not self.act:
            self._observe()
            return
        if self.connection is not None and self.connection.check():
            self._round_due = True  # personnage reconnecté (ou groupe reformé) : tour des fenêtres
            return
        if not self._stop_reason and self.deadline is not None and time.monotonic() >= self.deadline:
            self._stop_reason = "durée de la session écoulée"
            self._round_due = True  # fenêtres de résultat fermées avant de s'arrêter
        if len(self._lost) < self._lost_seen:
            self._progress("suiveur arrivé")
        self._lost_seen = len(self._lost)
        self._note_rejoin_attempts()
        if not self._round_due and self.watchdog.check():
            return  # diagnostic (hors combat), ou dernier recours
        if self._result_check_at is not None and time.monotonic() >= self._result_check_at and not self._round_due:
            self._result_check_at = None
            self._recheck_results()
            return
        if self._round_due:
            unseen, self._result_unseen = self._result_unseen, False
            host = self._round()
            if host is not None:
                self._fight(host)
            elif self._stop_reason:
                log.info("Fin de la session : %s", self._stop_reason)
                self.finished = True
            elif unseen and not self._leader_closed:
                # Tour des fenêtres d'après un combat fini sans résultat vu, rien fermé chez le principal : on y
                # regarde de nouveau RESULT_RECHECK s plus tard (_recheck_results).
                self._result_check_at = time.monotonic() + RESULT_RECHECK
            return
        member = self._watched()
        if member is None:
            self.leader.game.sleep(0.5)  # aucune fenêtre de l'équipe devant toi : rien à regarder
            return
        frame = member.game.look(park=False)
        scene = read_scene(frame)
        self._report(member, frame, scene)
        if scene.phase is not Phase.OUT_OF_COMBAT:
            self._fight(member)
            return
        self._out_of_combat(member, frame)
        member.game.sleep(self.poll)

    def _watched(self) -> Member | None:
        """Fenêtre à regarder hors combat : celle du principal quand le bot le mène ; en --combat-only (tu lances les
        combats toi-même), celle de l'équipe que tu as devant toi, sans t'en enlever."""
        if self.engager is not None or self.roamer is not None:
            return self.leader
        return next((m for m in self.members if m.in_front()), None)

    # Hors combat

    def _round(self) -> Member | None:
        """Tour des fenêtres, suiveurs d'abord (le principal reste devant à la fin) : fenêtres fermées (résultat du
        combat, niveau supérieur), sorts lus une fois, vie notée. Renvoie le personnage qui est en combat (agression,
        combat lancé à la main), s'il y en a un."""
        self._round_due = False
        self._last_round = time.monotonic()
        self._tired = []
        for member in self.members:  # possessions gardées pour la session suivante (brain/possessions.py, 30/10)
            possessions.save(member.name, getattr(member.game, "net", None))
        self._hp = {}
        since = None if self._combat_ended_at is None else time.monotonic() - self._combat_ended_at
        self._leader_closed = False
        for member in self.followers + [self.leader]:
            frame = member.game.look(park=False)
            self._check_layout(member, frame)
            new_round = getattr(member.popups, "new_round", None)
            if new_round is not None:
                new_round()  # fenêtre notée bloquée au tour précédent : nouvel essai
            for _ in range(MAX_POPUPS):
                if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                    return member
                if not member.popups.handle(frame, since):
                    break
                self._leader_closed = self._leader_closed or member is self.leader
                frame = member.game.look(park=False)
            if member.popups.describe(frame) is not None:
                continue  # fenêtre inconnue (ou qui ne se ferme pas) : on n'y touche pas
            if not member.spellbook.read_done:
                member.spellbook.read()  # une fois par personnage : ses sorts et caractéristiques, pour son IA de combat
                frame = member.game.look(park=False)
            ratio = _told(member, "life_ratio")
            ratio = hp_ratio(frame) if ratio is None else ratio
            if ratio is not None:
                self._hp[member.name] = ratio
            if ratio is not None and ratio < self.rest_settings.below:
                self._tired.append(member)
            self._note_pods(member, frame)
        if len(self._hp) >= 2 and not any(self._hp.values()):
            # 26/10 (journal 181017, combat 6) : résultat du combat pas reconnu, resté ouvert par-dessus le cœur ; vie
            # lue à 0 % chez les 8 (personne n'est mort), repos de 180 s recommencé sans fin. Vivant hors combat,
            # personne n'est à 0 : le cœur est caché, pas de repos sur cette lecture ; le tour suivant relit.
            log.warning("Vie lue à 0 %% chez %d personnages : leur cœur est caché (fenêtre par-dessus ?), pas de repos "
                        "sur cette lecture", len(self._hp))
            self._tired = []
            self._hp = {}
        if self._hp_report_due and self._hp:
            # Vie de chacun après le combat : mesure des repos (25/10, journal 181212 : 3 repos, 8,1 min sur 53, la vie
            # des autres n'était écrite nulle part).
            self._hp_report_due = False
            log.info("Vie après le combat : %s", ", ".join(f"{name} {100 * ratio:.0f} %" for name, ratio in
                                                         sorted(self._hp.items(), key=lambda item: item[1])))
            self.learn_levels(friends=False)  # passage de niveau annoncé par le serveur (PM) : limite recalculée
        return None

    def _recheck_results(self) -> None:
        """Combat fini sans fenêtre de résultat vue, rien fermé chez le principal au tour des fenêtres : ses fenêtres
        regardées une fois de plus, RESULT_RECHECK s après (20/10, journal 201321 : le résultat, pas reconnu, est resté
        ouvert ; l'équipe a cherché des groupes derrière lui)."""
        member = self.leader
        since = None if self._combat_ended_at is None else time.monotonic() - self._combat_ended_at
        frame = member.game.look(park=False)
        for _ in range(MAX_POPUPS):
            if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                self._round_due = True  # combat (agression) : pris en charge au tour des fenêtres
                return
            if not member.popups.handle(frame, since):
                break
            log.info("Fenêtre vue chez le principal %.0f s après la fin du combat", RESULT_RECHECK)
            frame = member.game.look(park=False)

    def _note_pods(self, member: Member, frame: Frame) -> None:
        """Pods de chacun (jauge autour de la minimap, hors combat) : au journal quand ils changent ; vers 90 %, à la
        banque (allers-retours à venir : docs/rapports/plan_banque.md)."""
        fill = _told(member, "pods_ratio")
        fill = pods_fill(frame) if fill is None else fill
        before = self._pods.get(member.name)
        self._pods[member.name] = fill
        if before is not None and abs(fill - before) < POD_LOG_STEP:
            return
        # Premiers pods lus après la banque, du serveur ou de la jauge (27/10 : « Ow » pas encore reçu dans la session,
        # Pesopepe relu à l'écran, plancher manqué, second passage aussitôt).
        floor = self._bank_floor.setdefault(member.name, fill) if self._banked else None
        if fill >= self.pods_full and floor is not None and fill < floor + BANK_FLOOR_MARGIN:
            log.info("Pods de %s : %.0f %%, comme en sortant de la banque (équipement, objets hors ressources) : pas de "
                     "nouveau passage", member.name, 100 * fill)
        elif fill >= self.pods_full:
            log.warning("Pods de %s : %.0f %% : il faut passer à la banque", member.name, 100 * fill)
            if self.bank_auto and isinstance(self.roamer, ZoneRoamer) and self.roamer.errand is None:
                self.roamer.start_errand(BANK_MAP, f"Pods de {member.name} pleins")
        else:
            log.info("Pods de %s : %.0f %%", member.name, 100 * fill)

    def _check_layout(self, member: Member, frame: Frame) -> None:
        if member.name in self._checked_layout:
            return
        self._checked_layout.add(member.name)
        if not frame.layout.matches_reference():
            log.warning("Fenêtre de %dx%d : proportions différentes des captures de référence (1918x1008), "
                        "la disposition de l'interface peut ne pas correspondre.", frame.layout.width, frame.layout.height)

    def _out_of_combat(self, member: Member, frame: Frame) -> None:
        if self.engager is None and self.roamer is None:
            return  # --combat-only : le bot ne touche à rien hors combat
        if time.monotonic() - self._last_round >= self.team.round_every:
            self._round_due = True  # un suiveur a pu être agressé, ou une fenêtre s'ouvrir
            return
        if member.popups.describe(frame) is not None:
            return  # une fenêtre couvre la carte du principal : ses clics tomberaient dessus
        if self._tired and self.rest_enabled:
            self._rest()
            self._progress("repos")  # jusqu'à rest.max_seconds (180 s) sans bouger : pas un blocage
            return
        if self.engager is None:
            self._roam()
            return
        if not self._together_before_fighting():
            return
        if isinstance(self.roamer, ZoneRoamer) and (self.roamer.away or (self.roamer.away is None and
                                                                          self.roamer.locate())):
            # Hors de la zone : trajet jusqu'à elle d'abord, sans combat en chemin (ZoneRoamer.away).
            if self._regroup():
                self.roamer.step()
                self.engager.new_map()
                self._follow()
                self._progress("trajet vers la zone")
                if self.roamer.errand_reached:
                    self._bank_visit()
            return
        if time.monotonic() < self._next_engage_at or self.engager.engage():
            return  # combat lancé : le placement est vu au tour de boucle suivant
        if getattr(self.engager, "night", False) and not self.engager.settings.fight_at_night:
            # 26/10 (journal 012810) : combat de nuit perdu, les 8 morts (teinte pas reconnue). Pas de combat la nuit.
            self._stop_reason = "il fait nuit dans le jeu : pas de combat la nuit (engage.combats_de_nuit)"
            self._round_due = True
            return
        # Comme en solo : groupe parti avant l'arrivée, on en cherche un autre aussitôt ; aucun groupe, on attend
        # ou (mode zone) toute l'équipe passe sur une carte voisine.
        if self.engager.keyboard_ok is False:
            # Z et A sans effet malgré les clics de déblocage (brain/engage.py, _z_unheard) : rien ne dit que la carte
            # est vide, on n'en change pas ; nouvel essai plus tard, la surveillance du progrès prend le relais.
            self._empty_searches = 0
            self._next_engage_at = time.monotonic() + self.engager.settings.retry_delay
            return
        self._empty_searches = self._empty_searches + 1 if self.engager.none_found else 0
        if self.roamer is None and self.engager.none_found:
            # Mode carte : recherche Z entendue et sans groupe attaquable, l'équipe attend la repousse ; pas un blocage
            # (relecture du 21/10 : carte vide 5 min = pause BLOQUÉ jamais levée par la repousse).
            self._progress("carte vide, j'attends la repousse")
        if self.roamer is not None and self._empty_searches >= self.travel_settings.empty_searches:
            if not self._regroup():
                return
            self._empty_searches = 0
            self.roamer.step()
            self.engager.new_map()
            self._follow()
            self._next_engage_at = time.monotonic()
            return
        delay = 1.0 if self.engager.missed else self.engager.settings.retry_delay
        self._next_engage_at = time.monotonic() + delay

    def _bank_visit(self) -> None:
        """Équipe arrivée à la banque (course du ZoneRoamer, pods pleins) : chacun vide l'onglet ressources dans son
        coffre (team/bank.py, comme --banque), puis retour dans la zone de combat. Seulement toute l'équipe là."""
        if self._lost:
            log.info("Banque : %s pas encore là, j'attends l'équipe", ", ".join(sorted(self._lost)))
            return
        log.info("Banque : toute l'équipe est là, passage au coffre")
        done = bank_visit(self.members, self.travel_settings.hide_players_key, self.bank_mode, self.keep_key).run()
        failed = [m.name for m in self.members if m.name not in done]
        if failed:
            log.warning("Banque : coffre pas vidé pour %s (captures gardées)", ", ".join(failed))
        self._pods = {}
        self._bank_floor, self._banked = {}, True
        self.roamer.end_errand()
        self._round_due = True

    def _roam(self) -> None:
        """Test de déplacement, exploration : une carte à la fois, suivie par toute l'équipe."""
        if not self._regroup():
            return
        self.roamer.step()
        self._follow()
        if self.roamer.done:
            self.finished = True

    def _rest(self) -> None:
        """Repos de ceux dont la vie est basse, assis en même temps : tabouret dans chaque fenêtre, puis tour des
        fenêtres toutes les CHECK_EVERY secondes jusqu'à ce que chacun soit remonté (ou rest.max_seconds).

        Ceux qui attendent sous rest.until s'assoient aussi (_sit_while_waiting), sans allonger le repos : il finit
        quand ceux qui en avaient besoin sont remontés."""
        tired, self._tired = self._tired, []
        sitting = []
        for member in tired:
            frame = member.game.look()
            # Vie annoncée par le serveur (As) d'abord. Assis, il n'annonce que le rythme de récupération (ILS, 01/11 :
            # « ILS2000 », la vie gagnée « ILF » au lever) : la remontée se suit au cœur, plus bas.
            ratio = _told(member, "life_ratio")
            ratio = hp_ratio(frame) if ratio is None else ratio
            if ratio is None or ratio >= self.rest_settings.below:
                continue
            stool = match_template(frame, STOOL, box=frame.layout.box(*STOOL_ZONE))
            if stool is None:
                log.info("Vie à %.0f %% : tabouret introuvable, je me repose debout", 100 * ratio)
            else:
                log.info("Vie à %.0f %% : je m'assois jusqu'à %.0f %%", 100 * ratio, 100 * self.rest_settings.until)
                member.game.click(stool[0], frame.layout.spread(*STOOL_SPREAD))
            sitting.append(member)
        if sitting:
            self._sit_while_waiting(sitting)
        deadline = time.monotonic() + self.rest_settings.max_seconds
        while sitting and time.monotonic() < deadline:
            self.leader.game.sleep(CHECK_EVERY)
            for member in list(sitting):
                frame = member.game.look(park=False)
                if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                    log.info("Combat pendant le repos")
                    self._round_due = True
                    return
                ratio = hp_ratio(frame)
                if ratio is not None and ratio >= self.rest_settings.until:
                    log.info("Vie à %.0f %% : je repars", 100 * ratio)
                    sitting.remove(member)
        if sitting:
            log.info("Fin du repos (durée maximale)")

    def _sit_while_waiting(self, resting: list[Member]) -> None:
        """Pendant le repos de `resting`, les autres sous rest.until (vie lue au tour des fenêtres, relue ici)
        s'assoient aussi : l'équipe attend de toute façon, et assis la vie remonte plus vite. 25/10 (journal 181212) :
        3 repos d'un seul personnage (Eldieso 39 %, Fourboun 43 %, Eldieso 39 %), 153 à 179 s chacun, 8,1 min sur
        53 ; les 7 autres attendaient debout. Le tabouret est cliqué seulement s'il est vu ; le clic suivant sur la
        carte (soleil, épée) les relève, comme ceux qui se reposent. Une fois par fin de combat : déjà assis, un second
        clic sur le tabouret pourrait le relever."""
        until = self.rest_settings.until
        for member in self.members:
            if member in resting or self._hp.get(member.name, 1.0) >= until \
                    or self._seated_after.get(member.name, -1.0) == self._combat_ended_at:
                continue
            frame = member.game.look()
            ratio = hp_ratio(frame)
            if ratio is None or ratio >= until or read_scene(frame).phase is not Phase.OUT_OF_COMBAT \
                    or member.popups.describe(frame) is not None:
                continue
            stool = match_template(frame, STOOL, box=frame.layout.box(*STOOL_ZONE))
            if stool is None:
                continue
            log.info("Vie à %.0f %% : je m'assois aussi pendant le repos des autres", 100 * ratio)
            member.game.click(stool[0], frame.layout.spread(*STOOL_SPREAD))
            self._seated_after[member.name] = self._combat_ended_at

    # Changement de carte

    def _crossed(self, side: str, cell: Cell, before: MapLabel, after: MapLabel) -> None:
        """Appelée par le Traveler du principal (brain/travel.py) après chaque passage réussi."""
        self._crossings.append(Crossing(side, cell, before, after))
        self._progress("changement de carte")

    def _dispatch(self, side: str, cell: Cell, frame: Frame, spot: int) -> None:
        """Le principal vient de cliquer sur le soleil : chaque suiveur clique aussitôt le même (même case, à l'échelle
        de sa fenêtre) ; toute l'équipe marche en même temps (03/10 : les suiveurs ne partaient qu'une fois le
        principal arrivé, ~4 s perdues à chaque carte). L'arrivée de chacun est vérifiée ensuite (follow).

        Capture de départ : celle d'avant le clic (même décor, le personnage n'a pas encore bougé), plus de seconde
        capture après. Ordre des suiveurs tiré au hasard à chaque carte (20/10 : toujours les mêmes derniers cliqués,
        Pombro, Vertdepomme, Vieenjaune). Nouvel essai du principal (bas de la case, autre soleil) : recliqués aussi,
        mais seulement ceux dont le décor est encore celui du départ (20/10, soleil (27,5) de 5,5 : les 5 suiveurs
        restés immobiles n'étaient recliqués qu'après son arrivée, ~7 s perdues)."""
        center = getattr(self.roamer.traveler, "aim_center", None) if self.roamer is not None else None
        point, spread = _click_spot(frame, cell, spot, center)  # le losange vu par le principal (touche A)
        reference = to_reference(frame.layout, point)
        self._departure = self.roamer.traveler.coords if self.roamer is not None else None  # pas encore arrivé
        delta = DELTA.get(side)
        self._dispatch_side = side
        # Carte où mène ce soleil (suiveurs passés seuls, lectures incomplètes).
        self._dispatch_to = (self._departure[0] + delta[0], self._departure[1] + delta[1]) \
            if self._departure is not None and delta else None
        names, again = [], []
        # Carte de départ du principal d'après le serveur, relevée au premier clic de ce passage, avant que la suivante
        # soit annoncée (01/11, journal 113029 : arrivé pendant ce tour des suiveurs, 3 d'entre eux comparés à sa
        # nouvelle carte, crus ailleurs, rejoints à pied). Chaque suiveur est comparé à ce numéro de carte.
        if not self._dispatched:
            leader_net = _live_net(self.leader)
            self._departure_map = leader_net.map_id if leader_net is not None else None
        leader_map = self._departure_map
        for member in random.sample(self.followers, len(self.followers)):
            if member.name in self._lost:
                continue  # pas sur la carte du principal : il ne le suit pas à l'aveugle
            now = member.game.look(park=False)
            sent = self._dispatched.get(member.name)
            if sent is not None:
                net = _live_net(member)
                if net is not None and getattr(net, "my_id", None) is not None:
                    if _net_moved(net) >= sent[1]:
                        continue  # en marche ou arrivé d'après le serveur : pas de nouveau clic
                elif _gap(_scene(now), _scene(sent[0])) > SCENE_CHANGED:
                    continue  # parti (ou en train de changer de carte) : pas de nouveau clic
                if not self._clear_for_click(member, now):
                    continue  # fenêtre fermée : il sera recliqué pendant l'attente des suiveurs
            elif not self._on_leader_map(member, now, frame, leader_map):
                # 11/10 : noms de carte illisibles dans la cité, un suiveur ailleurs a cliqué le même soleil sur sa
                # carte à lui et s'est éloigné. Pas le même décor que le principal : il le rejoindra par son trajet.
                log.warning("%s n'est pas sur la carte du principal (décor différent) : il ne le suit pas à l'aveugle",
                            member.name)
                self._mark_lost(member, now)
                continue
            elif member.popups.describe(now) is not None:
                # 01/11 (journaux 113029, 122800) : « résultat du combat » ouvert après le passage de fermeture ; le clic
                # sur le soleil tombait dessus, le suiveur restait sur place et rejoignait le principal à pied.
                member.popups.handle(now, self._since_combat())
            layout = getattr(member.game, "layout", None) or frame.layout
            target = layout.point(*reference)
            # Autres joueurs cachés (touche E) pendant le clic.
            clicked = self.roamer.traveler.click_sun(target, spread, member.game) if self.roamer is not None \
                else member.game.click(target, spread)
            if clicked:
                self._dispatched[member.name] = (sent[0] if sent is not None else now, time.monotonic())
                (again if sent is not None else names).append(member.name)
        if names:
            log.info("Toute l'équipe vers le %s : %s cliquent aussi sur le soleil en %s", side, ", ".join(names), cell)
        if again:
            log.info("Nouvel essai du principal (soleil en %s%s) : %s, encore sur la carte de départ, recliquent aussi",
                     cell, ", bas de la case" if spot else "", ", ".join(again))

    def _clear_for_click(self, member: Member, frame: Frame) -> bool:
        """Avant de recliquer le soleil d'un suiveur : menu d'un personnage ouvert (fermé d'un clic à côté qui l'écarte
        de la case : close_context_menu ; Échap ne le ferme pas), ou fenêtre connue par-dessus la carte (fermée). False
        si une fenêtre vient d'être fermée : pas de clic sur cette capture."""
        if find_context_menu(frame) is not None:
            if not close_context_menu(member.game, frame):
                close_any_menu(member.game, member.name)  # 31/10 : plus d'Échap (Menu principal derrière un menu)
            return False  # il s'est écarté : le soleil au prochain essai, sur une capture fraîche
        if member.popups.describe(frame) is not None and member.popups.handle(frame, self._since_combat()):
            return False
        return True

    def _since_combat(self) -> float | None:
        return None if self._combat_ended_at is None else time.monotonic() - self._combat_ended_at

    def _follow(self) -> None:
        crossings, self._crossings = self._crossings, []
        if crossings:
            self._leader_stuck = 0
        for crossing in crossings:
            self.follow(crossing)
        if not crossings and self._dispatched:
            self._left_without_leader()
        self._dispatched = {}

    def _left_without_leader(self) -> None:
        """Le principal est resté sur sa carte (son clic n'a pas pris, il repart par un autre côté), mais les suiveurs
        partis avec lui (_dispatch) ont pu passer : 11/10, deux suiveurs en -1,-23, le principal reparti vers 1,-23,
        l'équipe séparée. Chacun est vérifié au décor ; ailleurs, il est perdu, sur la carte où menait ce soleil."""
        here = self.leader.game.look(park=False)
        passed = []
        for name in self._dispatched:
            member = self.by_name[name]
            frame = member.game.look(park=False)
            if self._on_leader_map(member, frame, here):
                continue
            passed.append(name)
            log.warning("%s est passé sans le principal (resté sur sa carte) : il le rejoindra par son trajet", name)
            self._lost.add(name)
            told = self._told_at(member)
            if told is not None:
                self._seen_at[name] = told  # carte annoncée par le serveur
            else:
                self._note_seen(name, read_map_label(frame), self._dispatch_to)
        if passed and self.roamer is not None and self._dispatch_side is not None:
            self._leader_left_behind(passed)

    def _note_seen(self, name: str, label: MapLabel | None, expected: tuple[int, int] | None) -> None:
        """Carte d'un suiveur perdu, pour son trajet (_regroup) : coordonnées lues ; lues en partie, complétées par
        `expected` (merge_reading) ; nom illisible : `expected` (carte où mène le soleil pris). 20/10 : Pesopepe,
        « 11,-?3 », sans carte notée : le bot s'est arrêté faute de savoir où il était."""
        seen = expected
        if label is not None and label.readable and label.coords is not None:
            seen = label.coords
        elif label is not None and label.readable and expected is not None:
            seen = merge_reading(label.text, expected)[0]
        if seen is not None:
            self._seen_at[name] = seen

    def _leader_left_behind(self, passed: list[str]) -> None:
        """Des suiveurs ont pris le soleil, pas le principal : ce soleil marche (pas de mur), il n'est plus noté bloqué.
        Plusieurs fois de suite : le principal est sans doute bloqué par le jeu, le bot s'arrête et le dit."""
        traveler = self.roamer.traveler
        key, side = traveler.key(), self._dispatch_side
        traveler.blocked_here.discard((key, side))
        if traveler.world.unblock(key, side):
            log.info("Sortie du %s pas bloquée : %s l'ont prise (je l'enlève des sorties bloquées)", side,
                     ", ".join(passed))
        self._leader_stuck += 1
        if self._leader_stuck >= LEADER_STUCK:
            log.warning("Le principal ne passe pas des soleils que ses suiveurs passent (%d fois de suite) : il est "
                        "sans doute bloqué par le jeu. Je m'arrête : un défi lancé à un autre personnage, puis quitté, "
                        "le débloque", self._leader_stuck)
            self.finished = True

    def follow(self, crossing: Crossing) -> None:
        """Les suiveurs prennent le soleil que le principal vient de prendre : déjà partis avec lui (_dispatch, dans
        l'ordre de leurs clics), ou clic dans chaque fenêtre maintenant ; puis vérification de l'arrivée de chacun,
        fenêtre après fenêtre."""
        dispatched, self._dispatched = self._dispatched, {}
        walking = [(self.by_name[name], *sent) for name, sent in dispatched.items()]
        before = crossing.before.coords or self._departure
        for member in self.followers:
            if member.name in dispatched or member.name in self._lost:
                # Perdu : il rejoint par son propre trajet (_regroup). 23/10 : 6 suiveurs restés en 11,-27, noms de carte
                # illisibles des deux côtés, ont cliqué le soleil du principal sur leur carte à eux (vers 10,-27).
                continue
            frame = member.game.look(park=False)
            if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                self._round_due = True  # combat (agression) : pris en charge au tour des fenêtres
                continue
            told = self._net_same_map(member)
            if told is True:
                continue  # déjà sur la carte d'arrivée (numéro de carte annoncé par le serveur dans les deux fenêtres)
            at = self._told_at(member)
            if told is False and at is not None and before is not None and at != before:
                log.warning("%s n'est pas sur la carte que quitte le principal (il est en %s,%s d'après le jeu) : il "
                            "ne le suit pas", member.name, *at)
                self._seen_at[member.name] = at
                continue
            label = read_map_label(frame) if told is None else None
            if told is None and _reads_as(label, self._arrival(crossing), crossing.after):
                continue  # déjà sur la carte d'arrivée
            if told is None and _reads_as(label, before, crossing.before) is False:
                log.warning("%s n'est pas sur la carte que quitte le principal (il est sur %s) : il ne le suit pas",
                            member.name, label.describe())
                if label.coords is not None:
                    self._seen_at[member.name] = label.coords
                continue
            self._click_sun(member, frame, crossing, 0)
            walking.append((member, frame, time.monotonic()))
        self._wait_followers(walking, crossing)

    def _arrival(self, crossing: Crossing) -> tuple[int, int] | None:
        """Coordonnées de la carte d'arrivée du principal : lues (ou complétées par son chemin, brain/travel.py), à
        défaut celles où il se croit (roamer.traveler.coords)."""
        if crossing.after.coords is not None:
            return crossing.after.coords
        return self.roamer.traveler.coords if self.roamer is not None else None

    def _click_sun(self, member: Member, frame: Frame, crossing: Crossing, spot: int) -> None:
        point, spread = _click_spot(frame, crossing.cell, spot % 4)  # centre, bas, pointes gauche et droite, encore
        log.info("Je suis le principal vers le %s : clic sur le soleil en %s", crossing.side, crossing.cell)
        if self.roamer is not None:
            self.roamer.traveler.click_sun(point, spread, member.game)  # autres joueurs cachés (touche E)
        else:
            member.game.click(point, spread)

    def _reclick(self, walk: _Walk, frame: Frame, crossing: Crossing) -> None:
        walk.spot += 1
        self._click_sun(walk.member, frame, crossing, walk.spot)
        walk.clicked = time.monotonic()
        walk.still_since = None  # immobilité à revoir à partir du regard suivant

    def _wait_followers(self, walking: list[tuple[Member, Frame, float]], crossing: Crossing) -> None:
        """Arrivée des suiveurs, regardés à tour de rôle (ils marchent en même temps). Comme pour le principal
        (brain/travel.py) : arrivé quand tout le décor a changé et que la carte lue est celle du principal (_reads_as) ;
        sinon, une fois le décor stable, quand c'est celui du principal (captures fraîches). Encore sur la carte de
        départ : nouveau clic sur le soleil, à un autre endroit de la case (un personnage passait dessus : 03/10,
        recliqué 12 s plus tard seulement), au plus tôt RECLICK_EVERY s après le clic et après FOLLOWER_STILL s
        d'immobilité vue, décor revu sur une capture fraîche juste avant ; après FOLLOWER_RECLICKS reclics, son propre
        trajet. Un menu ouvert à la place (quelqu'un sur le soleil) est fermé et la case recliquée ailleurs ; une
        fenêtre connue (Menu principal ouvert par un Échap) est fermée avant tout reclic."""
        pending = {member.name: _Walk(member, _scene(before), clicked) for member, before, clicked in walking}
        sent = {member.name: clicked for member, _, clicked in walking}  # premier clic de chacun (écoute réseau)
        target = self._arrival(crossing)
        leader_view: Frame | None = None  # carte où est arrivé le principal, pour juger au décor
        start = time.monotonic()
        deadline = start + self.travel_settings.walk_timeout
        while pending:
            now = time.monotonic()
            if now >= deadline:
                # 23/10, Plaine des Porkass (grandes cartes, rochers) : suiveurs encore en marche déclarés perdus à 20 s,
                # rattrapages un par un. Tant que l'un d'eux bouge, l'attente continue (jusqu'à WALK_EXTENSION fois).
                walking_now = [w for w in pending.values() if w.still_since is None or now - w.still_since < MOVING_RECENTLY]
                if not walking_now or now - start >= WALK_EXTENSION * self.travel_settings.walk_timeout:
                    break
                deadline = now + MOVING_RECENTLY
            for name, walk in list(pending.items()):
                member = walk.member
                frame = member.game.look(park=False)
                if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                    log.info("Combat pendant le trajet de %s", name)
                    self._round_due = True
                    del pending[name]
                    continue
                if find_context_menu(frame) is not None:
                    if not walk.menu_saved:
                        walk.menu_saved = True
                        path = member.game.recorder.save(frame.image, "suiveur_menu", lossless=True)
                        log.info("%s : menu ouvert au lieu de la marche (quelqu'un sur le soleil ?) (capture : %s)",
                                 name, path)
                    # Souvent lui-même, arrivé par ce soleil (21/10) : un clic à côté ferme le menu et l'écarte de la
                    # case (Échap ne le ferme pas), puis le soleil de nouveau, au centre.
                    if not close_context_menu(member.game, frame):
                        close_any_menu(member.game, name)  # 31/10 : plus d'Échap derrière un menu
                    walk.spot = -1  # _reclick : le centre de la case
                    self._reclick(walk, member.game.look(park=False), crossing)
                    continue
                told = self._told_arrival(member, sent[name])
                if told is not None:
                    # Nouvelle carte annoncée par le jeu dans SA fenêtre (connection/network.py, 26/10) : exacte.
                    if target is None or told == target:
                        log.info("%s a suivi le principal (carte %s,%s annoncée par le jeu)", name, *told)
                        self._lost.discard(name)
                    else:
                        log.warning("%s est arrivé en %s,%s d'après le jeu, pas sur la carte du principal (%s,%s)",
                                    name, *told, *target)
                        self._lost.add(name)
                    del pending[name]
                    continue
                popup = member.popups.describe(frame)
                if popup is not None and member.popups.handle(frame, self._since_combat()):
                    # 20/10, Vertdepomme : un Échap a ouvert le Menu principal, six reclics sont tombés dedans (~25 s).
                    log.info("%s : %s pendant l'attente des suiveurs, fermée avant tout reclic", name, popup)
                    walk.previous = walk.motion = walk.still_since = None  # décor caché : regards à refaire
                    continue
                net = _live_net(member)
                if net is not None and getattr(net, "my_id", None) is not None and \
                        time.monotonic() - walk.clicked >= NET_SUN_SILENT and _net_moved(net) < walk.clicked:
                    # Aucun déplacement annoncé par le serveur depuis le clic sur le soleil : pas pris, recliqué sans
                    # attendre de le voir immobile (les déplacements hors combat sont annoncés, GA0;1).
                    self._still_follower(walk, crossing, pending)
                    continue
                view = _scene(frame)
                changed = _gap(view, walk.departure) > SCENE_CHANGED
                stable = walk.previous is not None and _gap(view, walk.previous) < SCENE_STABLE
                walk.previous = view
                motion = _motion_view(frame)
                now = time.monotonic()
                if walk.still_since is None or (walk.motion is not None and
                                                _motion(motion, walk.motion) >= self.travel_settings.motion_cells):
                    walk.still_since = now  # premier regard, ou il bouge
                walk.motion = motion
                if not changed:
                    # Reclic seulement s'il ne bouge pas : 11/10, Imthebest, plus lent, arrivait en 5,-16 quand le
                    # reclic est parti ; tombé sur la nouvelle carte, il l'a fait descendre en 5,-15.
                    if now - walk.clicked >= RECLICK_EVERY and now - walk.still_since >= FOLLOWER_STILL:
                        self._still_follower(walk, crossing, pending)
                    continue  # en chemin
                label = read_map_label(frame)
                if _reads_as(label, target, crossing.after):
                    log.info("%s a suivi le principal (%s)", name, label.describe())
                    self._lost.discard(name)
                    del pending[name]
                    continue
                if not stable:
                    continue  # chargement de la carte
                # Nom illisible (27 % des arrivées de suiveurs n'étaient pas vérifiées, bilan du 12/10), lu en partie ou
                # autrement que chez le principal (20/10, 11,-23) : même décor que le principal, revu une fois sur des
                # captures fraîches avant de le déclarer perdu.
                if leader_view is None:
                    leader_view = self.leader.game.look(park=False)
                read = label is not None and label.readable
                if same_map(frame, leader_view) or same_map(member.game.look(park=False),
                                                              self.leader.game.look(park=False)):
                    log.info("%s a suivi le principal (%s, même décor)", name,
                             f"lu « {label.text} »" if read else "nom de carte illisible")
                    self._lost.discard(name)
                elif read:
                    log.warning("%s est arrivé sur %s, pas sur la carte du principal (autre décor)", name,
                                label.describe())
                    self._lost.add(name)
                    self._note_seen(name, label, target or self._dispatch_to)
                else:
                    log.warning("%s a changé de carte, mais pas le même décor que le principal : il le rejoindra "
                                "par son trajet", name)
                    self._lost.add(name)  # position inconnue : pas devinée (le regroupement revoit le décor)
                del pending[name]
            if pending:
                self.leader.game.sleep(0.2)
        for walk in pending.values():
            self._left_behind(walk.member, crossing,
                              f"toujours pas arrivé après {time.monotonic() - start:.0f} s")

    def _told_arrival(self, member: Member, since: float) -> tuple[int, int] | None:
        """Coordonnées de la carte annoncée par le jeu dans la fenêtre de `member` depuis `since` (time.monotonic()),
        une fois chargée ; None sans écoute réseau, sans changement de carte, ou carte hors de la référence."""
        net = getattr(member.game, "net", None)
        map_id = net.new_map_since(since) if net is not None and net.alive() else None
        if map_id is None or self.roamer is None:
            return None
        return self.roamer.traveler.passages.coords_of(map_id)

    def _still_follower(self, walk: _Walk, crossing: Crossing, pending: dict[str, _Walk]) -> None:
        """Suiveur immobile sur la carte de départ (_wait_followers) : décor revu sur une capture fraîche, puis reclic ;
        après FOLLOWER_RECLICKS reclics, son propre trajet (rattrapage) plutôt que d'attendre travel.walk_timeout."""
        member = walk.member
        fresh = member.game.look(park=False)
        if _gap(_scene(fresh), walk.departure) > SCENE_CHANGED:
            return  # il arrive : pas de reclic (jugé au regard suivant)
        if find_context_menu(fresh) is not None or member.popups.describe(fresh) is not None:
            return  # menu ou fenêtre : fermés au regard suivant, avant tout clic
        # Écoute réseau : une arrivée est annoncée (_told_arrival), le nom de la carte n'a rien à trancher.
        label = read_map_label(fresh) if self._net_same_map(member) is None else None
        if label is not None and label.coords is not None and label.coords == self._arrival(crossing):
            # Décor presque pareil d'une carte à l'autre (24/10, plaines des Porkass : suiveurs crus sur la carte de
            # départ, recliqués sur la nouvelle carte, partis une ou deux cartes trop loin) : la carte lue tranche.
            log.info("%s a suivi le principal (%s, décor presque pareil)", member.name, label.describe())
            self._lost.discard(member.name)
            del pending[member.name]
            return
        if walk.reclicks >= FOLLOWER_RECLICKS:
            del pending[member.name]
            self._left_behind(member, crossing, f"immobile sur la carte de départ après {walk.reclicks} reclics", fresh)
            return
        if walk.reclicks == 0:
            path = member.game.recorder.save(fresh.image, "suiveur_reclic", lossless=True)
            log.info("%s ne bouge pas, toujours sur la carte de départ : je reclique le soleil (capture : %s)",
                     member.name, path)
        walk.reclicks += 1
        self._reclick(walk, fresh, crossing)

    def _left_behind(self, member: Member, crossing: Crossing, why: str, frame: Frame | None = None) -> None:
        """Suiveur qui n'a pas suivi : perdu, resté sur la carte de départ ; il rejoindra par son propre trajet."""
        image = (frame if frame is not None else member.game.look()).image
        path = member.game.recorder.save(image, "suiveur_bloque", lossless=True)
        log.warning("%s n'a pas suivi le principal (%s ; capture : %s) : il le rejoindra par son propre trajet",
                    member.name, why, path)
        self._lost.add(member.name)
        start = crossing.before.coords if crossing.before.readable else None
        if start or self._departure:
            self._seen_at[member.name] = start or self._departure  # resté sur la carte de départ

    def _mark_lost(self, member: Member, frame: Frame | None = None) -> None:
        """Suiveur pas sur la carte du principal : perdu ; sa carte est notée si elle se lit."""
        self._lost.add(member.name)
        told = self._told_at(member)
        if told is not None:
            self._seen_at[member.name] = told  # carte annoncée par le serveur
            return
        label = read_map_label(frame if frame is not None else member.game.look(park=False))
        if label is not None and label.readable and label.coords is not None:
            self._seen_at[member.name] = label.coords

    def _together_before_fighting(self) -> bool:
        """Avant la première attaque : toute l'équipe sur la carte du principal. 17/10 : Koalaloupour, mort la veille,
        réapparu à Astrub, les 7 autres à Incarnam : le principal s'est battu seul, aucun suiveur ne voyait l'épée. Les
        perdus rejoignent d'abord (mode zone : _regroup) ; sans trajet possible, le bot s'arrête."""
        if self._together_checked:
            return not self._lost or self.roamer is None or self._regroup()
        self._check_together()
        if not self._lost:
            return True
        if self.roamer is None:
            log.warning("%s pas sur la carte du principal, et pas de trajet possible dans ce mode : je m'arrête "
                        "(réunis l'équipe sur une même carte, puis relance)", ", ".join(sorted(self._lost)))
            self.finished = True
            return False
        return self._regroup()

    def _check_together(self) -> None:
        """Avant le premier trajet : chaque suiveur doit voir le même décor que le principal (même carte)."""
        self._together_checked = True
        here = self.leader.game.look(park=False)
        for member in self.followers:
            frame = member.game.look(park=False)
            if not self._on_leader_map(member, frame, here):
                log.warning("%s n'est pas sur la carte du principal au départ : il le rejoindra d'abord", member.name)
                self._mark_lost(member, frame)

    def _told_at(self, member: Member) -> tuple[int, int] | None:
        """Coordonnées de la carte de ce personnage d'après le serveur (numéro du dernier GDM, référence complétée) ;
        None sans écoute vivante, avant le premier changement de carte, ou hors de la référence de la région."""
        net = getattr(member.game, "net", None)
        passages = getattr(getattr(self.roamer, "traveler", None), "passages", None)
        if net is None or not net.alive() or net.map_id is None or passages is None:
            return None
        return passages.coords_of(net.map_id)

    def _net_same_map(self, member: Member, leader_map: int | None = None) -> bool | None:
        """Même carte que le principal d'après le serveur (numéro du dernier GDM dans les deux fenêtres) ; None sans
        écoute vivante dans l'une ou l'autre. `leader_map` : la carte du principal à comparer, si elle a pu changer
        depuis (_dispatch : sa carte de départ)."""
        nets = [getattr(m.game, "net", None) for m in (member, self.leader)]
        told = [net is not None and net.alive() and net.map_id is not None for net in nets]
        if told[0] and leader_map is not None:
            return nets[0].map_id == leader_map
        if all(told):
            return nets[0].map_id == nets[1].map_id
        if told[0]:
            # 01/11 : Pesopepe reconnecté (carte annoncée à sa connexion, 1,-28), le principal sans changement de carte
            # depuis le début de l'écoute ; décor jugé autre juste après la connexion, le bot arrêté. Coordonnées de
            # la carte annoncée contre celles où le principal se sait.
            mine = self._told_at(member)
            traveler = getattr(getattr(self, "roamer", None), "traveler", None)
            theirs = getattr(traveler, "coords", None) if not getattr(traveler, "doubt", True) else None
            if mine is not None and theirs is not None:
                return mine == theirs
        return None

    def _on_leader_map(self, member: Member, frame: Frame, leader_frame: Frame, leader_map: int | None = None) -> bool:
        """Même carte que le principal : d'après le serveur (01/11), sinon au décor des deux captures."""
        told = self._net_same_map(member, leader_map)
        return told if told is not None else same_map(frame, leader_frame)

    def _same_place(self, member: Member) -> bool:
        """Le suiveur est sur la carte du principal : même décor, ou mêmes coordonnées lues dans les deux fenêtres (24/10,
        plaines des Porkass : Pesopepe arrivé en -1,-40, décor jugé autre, le bot arrêté)."""
        # 31/10, utilisateur : le réseau d'abord ; numéro de carte annoncé par le serveur aux deux fenêtres.
        told = self._net_same_map(member)
        if told is not None:
            return told
        mine, theirs = member.game.look(park=False), self.leader.game.look(park=False)
        if same_map(mine, theirs):
            return True
        a, b = read_map_label(mine), read_map_label(theirs)
        return a is not None and b is not None and a.coords is not None and a.coords == b.coords

    def _regroup(self) -> bool:
        """Suiveurs perdus (pas sur la carte du principal) : chacun fait son propre trajet jusqu'à elle (mode aller,
        une carte par appel) ; le principal ne repart pas avant. True quand toute l'équipe est réunie.

        11/10, cité d'Astrub : le principal continuait sans eux, l'équipe s'est dispersée sur quatre cartes. Un
        suiveur qui ne peut pas rejoindre (position inconnue, aucun chemin) arrête le bot : mieux vaut s'arrêter que
        de continuer dispersés."""
        if self.roamer is None:
            return True
        traveler = getattr(self.roamer, "traveler", None)
        teleported = getattr(traveler, "teleported", None)
        if teleported:
            # 25/10 : le principal a pris un zaap (aucun chemin à pied, brain/travel.py) ; ses suiveurs, restés au zaap
            # de départ, le rejoignent chacun par son trajet : même itinéraire de la référence, même zaap.
            traveler.teleported = None
            behind = [m.name for m in self.followers if m.name not in self._lost]
            for name in behind:
                self._lost.add(name)
                self._seen_at[name] = teleported[0]
            # 26/10 (journal 173905) : partis seuls vers le rendez-vous de la zone, ils marchaient l'un après l'autre (une
            # carte en ~40 s pour 7) ; le principal les attend à l'arrivée du zaap, puis l'équipe marche ensemble.
            self._behind_zaap.update(behind)
            if behind:
                log.info("Le principal a pris le zaap de %s,%s vers %s,%s : %s le rejoignent par le zaap", *teleported[0],
                         *teleported[1], ", ".join(behind))
        if not self._together_checked:
            self._check_together()
        if not self._lost:
            return True
        if getattr(self.roamer.traveler, "_last_label", True) is None:
            # 25/10 (journal 201109) : pas encore regardé, sa position était celle de --combat-zone (-3,-25) alors qu'il
            # était à Taïnéla ; Elseiso envoyé seul aux Blops. Sa carte d'abord (lue, sinon demandée au jeu).
            self.roamer.traveler.look()
        target = self.roamer.traveler.coords
        # Principal hors de la zone : rendez-vous à la carte de la zone donnée au lancement, où il va lui aussi ; les
        # suiveurs égarés y vont chacun de leur côté, le principal ne les attend pas (voir ZoneRoamer.meeting).
        if isinstance(self.roamer, ZoneRoamer) and self.roamer.away is None:
            # 26/10 (journal 173634) : pas encore regardé (None) pris pour « dans la zone » ; 5 suiveurs ont pris le zaap
            # vers le principal au Berceau, hors de la zone. Où il est d'abord.
            self.roamer.locate()
        meeting = getattr(self.roamer, "meeting", None) if getattr(self.roamer, "away", False) else None
        if meeting is not None:
            target = meeting
        else:
            self._at_meeting.clear()  # le principal est dans la zone : ceux qui l'attendaient le rejoignent
        if target is None:
            # 23/10 : au départ, nom de carte du principal illisible ; parti aussitôt sans les 6 suiveurs restés sur la
            # carte voisine. Sa position d'abord (lue, sinon demandée au jeu : « /p %pos% »).
            self.roamer.traveler.look()
            target = self.roamer.traveler.coords
        if target is None:
            return True  # carte du principal inconnue : rien pour guider les autres
        leader_at = self.roamer.traveler.coords
        for name in sorted(self._lost - self._at_meeting):
            member = self.by_name[name]
            target = leader_at if name in self._behind_zaap or meeting is None else meeting
            if target is None:
                continue  # carte du principal pas encore lue : au tour suivant
            journey = self._rejoin.get(name)
            here = journey.traveler.coords if journey is not None else self._seen_at.get(name)
            if meeting is not None and here is not None and here == leader_at and self._same_place(member):
                # 26/10 (journal 173905) : en route vers le rendez-vous chacun de son côté, un pas après l'autre (~80 s
                # par carte pour le principal et 7 suiveurs) ; sur la carte du principal, il le suit comme les autres
                # (tous les soleils cliqués ensemble).
                log.info("%s est sur la carte du principal (%s,%s) : il le suit désormais", name, *leader_at)
                self._lost.discard(name)
                self._rejoin.pop(name, None)
                self._seen_at.pop(name, None)
                continue
            if journey is None or journey.goal != target:
                # 31/10 : la carte annoncée par le serveur d'abord (Pesopepe resté en 0,-32, cru au zaap 1,-32 avec le
                # principal : trajet parti de la mauvaise carte, zaap cliqué là où il n'y en a pas).
                start = self._told_at(member) or self._seen_at.get(name)
                if start is None:
                    start = self._look_again(member)
                if start is None and same_map(member.game.look(park=False), self.leader.game.look(park=False)):
                    # 20/10, 11,-23 : Pesopepe cru perdu (« 11,-?3 » lu), carte inconnue, le bot arrêté ; il était sur
                    # la carte du principal. Son décor est revu avant de s'arrêter faute de savoir où il est.
                    log.info("%s est en fait sur la carte du principal (même décor) : il n'a pas besoin de trajet", name)
                    self._lost.discard(name)
                    continue
                if start is None:
                    # 23/10 : Pombro, nom de carte illisible au départ, le bot arrêté ; sa position demandée au jeu.
                    start = PositionAsker(member.game, name).ask()
                    if start is not None:
                        log.info("%s : position donnée par le jeu (« /p %%pos%% ») : %s,%s", name, *start)
                        self._seen_at[name] = start
                if start is None:
                    log.warning("%s n'est pas sur la carte du principal et je ne sais pas où il est (nom de carte "
                                "illisible) : je m'arrête (amène-le sur la carte du principal, puis relance)", name)
                    self.finished = True
                    return False
                settings = replace(self.travel_settings, mode="aller", destination=target, start=start)
                traveler = Traveler(member.game, settings, self.roamer.traveler.world, self.roamer.traveler.atlas)
                traveler.asker.name = name  # sa ligne « /p », pas celle d'un autre du groupe (_name_asker)
                traveler.learns = False  # 11/10 : ses sorties « bloquées » (pas sur la carte crue) ont tout faussé
                journey = self._rejoin[name] = Journey(traveler, target)
                log.info("%s rejoint %s en %s,%s (depuis %s)", name,
                         "le rendez-vous de la zone" if target == meeting else "le principal", *target,
                         f"{start[0]},{start[1]}" if start else "une carte inconnue")
            before = journey.traveler.coords
            journey.step()
            if journey.traveler.coords != before:
                # 25/10 (journal 175311) : 7 suiveurs en route depuis 1,-33 (sortie du bas injoignable, détour par
                # 0,-33), 5 min sans « progrès » : pause du bot alors que chacun changeait de carte.
                self._progress("suiveur en route")
            if not journey.done:
                continue
            del self._rejoin[name]
            arrived = journey.traveler.coords == target
            if arrived and meeting is not None and target == meeting:
                log.info("%s attend le principal au rendez-vous de la zone, en %s,%s", name, *target)
                self._at_meeting.add(name)
                self._seen_at[name] = target
                continue
            if arrived and not self._same_place(member):
                # 01/11 : Pesopepe reconnecté en 1,-28, décor plus clair que celui du principal (teinte jour/nuit de
                # chaque fenêtre), nom de carte illisible : le bot arrêté alors que « /p » avait répondu 1,-28 par le
                # serveur. Cette réponse tranche.
                asker = getattr(journey.traveler, "asker", None)
                told = asker.ask(force=True) if asker is not None else None
                if told == target:
                    log.info("%s : décor autre que celui du principal, mais en %s,%s d'après le jeu (« /p »)", name,
                             *target)
                else:
                    log.warning("%s se croit en %s,%s mais ne voit pas le même décor que le principal%s", name,
                                *target, f" (« /p » : {told[0]},{told[1]})" if told else "")
                    arrived = False
            if arrived:
                log.info("%s a rejoint le principal en %s,%s", name, *target)
                self._lost.discard(name)
                self._behind_zaap.discard(name)
                self._seen_at.pop(name, None)
            else:
                log.warning("%s ne peut pas rejoindre le principal en %s,%s : je m'arrête (amène-le sur sa carte, "
                            "puis relance)", name, *target)
                tell_user(f"{name} ne peut pas rejoindre le principal en {target[0]},{target[1]} : bot arrêté ; "
                          f"amène-le sur sa carte, puis relance.")
                self.finished = True
                return False
        # En route vers le rendez-vous : le principal ne les attend pas ; restés derrière son zaap : il les attend.
        return not self._lost or (meeting is not None and not (self._lost & self._behind_zaap))

    # Combat

    def _fight(self, host: Member) -> None:
        """Un combat de l'équipe, du placement jusqu'à la fin ; les fenêtres de résultat sont fermées au tour des
        fenêtres qui suit."""
        self._in_fight = True  # erreur d'ici : rattrapée sans arrêter le bot (safe_step)
        self._progress("combat lancé")
        shared = MapMemory()  # même carte pour tous : ce qu'un personnage en voit sert aux autres (trou caché, 03/10)
        for member in self.members:
            member.ai.new_combat()  # nouvelle carte de combat : obstacles et mode tactique à revoir
            member.ai.map = shared
        self.router.new_fight()
        self.joiner.new_fight()
        self._search = None
        self._turn_seen = None
        self._hinted = None
        self._early = None
        self._cells = {}
        self._flash_checked = set()
        if read_scene(host.game.look(park=False)).phase is Phase.PLACEMENT:
            fighters = self._placement(host)
            if fighters == [host] and len(self.members) > 1 and not self._stop_reason:
                # 25/10 (journal 171736) : épée vue par aucun suiveur, 2 combats sur 3 sans personne d'autre que le
                # principal ; au second, Fourboun est mort seul. Ce combat est joué jusqu'au bout, puis fin de session.
                log.warning("Personne n'a rejoint le combat de %s : il se bat seul. Je finis ce combat, puis je "
                            "m'arrête (relance quand l'équipe est réunie)", host.name)
                tell_user(f"Personne n'a rejoint le combat de {host.name} : il se bat seul, le bot s'arrête après.")
                self._stop_reason = f"personne n'a rejoint le combat de {host.name}"
        else:
            fighters = [host] + [m for m in self.members if m is not host
                                 and read_scene(m.game.look(park=False)).phase is not Phase.OUT_OF_COMBAT]
            log.info("Combat déjà commencé : %s", ", ".join(m.name for m in fighters))
        self._told_end = False
        end = self._turns(fighters) if fighters else None
        dead = [m.name for m in fighters if _died(m)]
        if dead:
            # 17/10 : Koalaloupour mort seul à Incarnam, réapparu à Astrub, le bot a continué comme si de rien n'était.
            log.warning("%s mort(s) pendant ce combat (« Tu as perdu ... points d'énergie » dans le chat) : je "
                        "m'arrête après ce combat", ", ".join(dead))
            tell_user(f"{', '.join(dead)} mort(s) au combat : le bot s'arrête après ce combat.")
            self._stop_reason = f"{', '.join(dead)} mort(s) au combat"
        self._fight_over()
        self.fights += 1
        write_fight_report(getattr(self.leader.game.recorder, "dir", None), self.fights,
                           [(m.name, m.ai.fight_report()) for m in fighters])
        if self.max_fights and self.fights >= self.max_fights:
            self._stop_reason = f"{self.fights} combat(s), le maximum demandé"
        self._result_unseen = end is None and bool(fighters) and not self._told_end
        if self._told_end:
            self._close_told_results(fighters)
        elif end is not None:
            self._close_results(*end, fighters)
        self._in_fight = False

    def _placement(self, host: Member) -> list[Member]:
        """Les autres rejoignent le combat de celui qui l'a lancé, puis Prêt dans chaque fenêtre, la sienne en dernier.
        Renvoie les personnages du combat. Sans placement (team.place, défaut) : personne ne bouge, chacun est repéré à
        son premier tour (portée d'un sort : CombatAI._locate_by_range). Avec : toute l'équipe se place d'un coup,
        selon l'ordre de jeu (_seat_team, team/placement.py), une fois tout le monde arrivé."""
        log.info("Placement : combat de %s", host.name)
        hint = recent_click(host)  # près du groupe attaqué : l'épée est là
        self.joiner.host_id = getattr(getattr(host.game, "net", None), "my_id", None)  # son épée, d'après le serveur
        # 21/10 : épées déjà là avant son clic d'attaque (sa capture d'avant ce clic) : d'autres combats, écartés.
        self.joiner.ignore_swords_before(getattr(host.game, "map_click_frame", None), hint)
        others = [m for m in self.members if m is not host]
        self._send_help(host, others)
        # Tous rejoignent d'abord, l'un après l'autre sans s'attendre (cases tirées au hasard par le jeu).
        asked = [m for m in others if self.joiner.ask(m, hint)]
        deadline = time.monotonic() + self.joiner.timeout
        joined = []
        for member in others:
            if (member in asked and self.joiner.wait_placement(member, deadline)) or self.joiner.join(member, hint):
                joined.append(member)
        ready: set[str] = set()
        if self.team.place:
            ready = self._seat_team(host, [host] + joined)
        else:
            self._note_placement(host)
        if self.auto_ready:
            # 18/10 (utilisateur : un peu d'aléatoire, mais rapide) : le 17/10, les 8 Prêt tombaient toutes les 0,31 s,
            # toujours dans le même ordre. Ordre mélangé, écart tiré au hasard ; le lanceur du combat toujours en dernier.
            for member in random.sample(joined, len(joined)) + [host]:
                if member.name not in ready:
                    self._ready(member)
                    member.game.sleep(random.uniform(*READY_GAP))
        return [host] + joined if self._wait_combat(host) else []

    def _send_help(self, host: Member, others: list[Member]) -> None:
        """Personnages pas sur la carte du combat de `host` (agressé en route, resté en arrière), d'après le serveur :
        s'ils en sont à HELP_MAX_MAPS cartes au plus (sorties de la référence), ils y vont ensemble, soleil après
        soleil, pendant le placement, pour rejoindre son combat ensuite (26/10, demande de l'utilisateur). Sans écoute,
        ou carte inconnue : rien (comme avant)."""
        net = getattr(host.game, "net", None)
        if net is None or not net.alive() or self.roamer is None:
            return
        passages = self.roamer.traveler.passages
        target = passages.coords_of(net.snapshot()[0]) if net.snapshot()[0] is not None else None
        if target is None:
            return
        away: dict[tuple[int, int], list[Member]] = {}
        for member in others:
            feed = getattr(member.game, "net", None)
            if feed is None or not feed.alive() or feed.in_fight() or feed.snapshot()[0] is None:
                continue
            here = passages.coords_of(feed.snapshot()[0])
            if here is not None and here != target:
                away.setdefault(here, []).append(member)
        deadline = time.monotonic() + HELP_PLACEMENT
        for start, members in away.items():
            route = _short_route(passages, start, target, HELP_MAX_MAPS)
            names = ", ".join(m.name for m in members)
            if route is None:
                log.info("%s : combat de %s en %s,%s, trop loin de %s,%s pour l'aider à temps", names, host.name,
                         *target, *start)
                continue
            log.info("%s : %s est agressé en %s,%s, j'y vais (%d carte(s)) pour rejoindre son combat", names, host.name,
                     *target, len(route))
            here, entry = start, None
            for side, dest in route:
                cell = passages.sun_cell(here, side, entry)
                if cell is None or time.monotonic() >= deadline:
                    log.info("%s : pas de soleil connu vers %s,%s, ou plus le temps : je n'y vais pas", names, *dest)
                    break
                clicked = time.monotonic()
                for member in members:
                    frame = member.game.look(park=False)
                    point, spread = _click_spot(frame, cell, 0)
                    self.roamer.traveler.click_sun(point, spread, member.game)
                end = min(deadline, clicked + HELP_WALK)
                while time.monotonic() < end and any(
                        passages.coords_of(m.game.net.new_map_since(clicked) or -1) != dest for m in members):
                    host.game.sleep(0.3)
                arrived = [m for m in members if passages.coords_of(m.game.net.new_map_since(clicked) or -1) == dest]
                if len(arrived) < len(members):
                    log.info("%s : pas arrivé(s) en %s,%s à temps", ", ".join(m.name for m in members
                                                                          if m not in arrived), *dest)
                members, here, entry = arrived, dest, OPPOSITE[side]
                if not members:
                    break
            if here == target and members:
                log.info("%s : arrivé(s) sur la carte du combat de %s, je le(s) fais rejoindre", names, host.name)
                for member in members:
                    self._lost.discard(member.name)

    def _look_again(self, member: Member) -> tuple[int, int] | None:
        """Carte d'un suiveur perdu, relue LOOK_AGAIN fois à quelques instants d'intervalle avant de demander « /p » ou
        de s'arrêter (25/10 : Walitcoin reconnecté à la main au démarrage, carte pas encore affichée ; nom illisible,
        « /p » sans réponse, le bot arrêté alors qu'il était avec le principal). Ses coordonnées, ou None."""
        for _ in range(LOOK_AGAIN):
            member.game.sleep(LOOK_AGAIN_PAUSE)
            label = read_map_label(member.game.look(park=False))
            if label is not None and label.readable and label.coords is not None:
                return label.coords
        return None

    def learn_levels(self, friends: bool = True) -> None:
        """Niveaux jamais lus (nouveaux personnages, 25/10 : groupes de niveau 75 laissés, limite par défaut 70, pour
        une équipe qui en cumule ~270) : lus dans la liste des amis du principal, retenus, la limite recalculée. Le
        principal n'est pas dans sa propre liste : la moyenne des autres (Engager.level_limit). Au lancement, après le
        groupe (bot/__main__.py)."""
        if self.engager is None or not getattr(self.engager, "team_levels", None):
            return
        # 01/11 : niveaux annoncés par le serveur avec le groupe (PM, principal compris) : ni liste des amis ni moyenne,
        # et un passage de niveau retenu.
        told = {}
        for member in self.members:
            net = getattr(member.game, "net", None)
            told.update(getattr(net, "group_levels", None) or {})
        changed = {m.name: told[m.name] for m, level in zip(self.members, self.engager.team_levels)
                   if told.get(m.name) is not None and told[m.name] != level}
        for name, level in changed.items():
            save_level(self._settings, name, level)
        if changed:
            self.engager.team_levels = [load_level(self._settings, m.name) for m in self.members]
            log.info("Niveaux d'après le serveur (%s) : niveau maximal des groupes %d",
                     ", ".join(f"{name} {level}" for name, level in changed.items()), self.engager.level_limit())
        missing = [m.name for m, level in zip(self.members, self.engager.team_levels)
                   if level is None and m is not self.leader]
        if not missing or not friends:
            return
        for name, level in friend_levels(Player(self.leader.name, self.leader.game), missing).items():
            save_level(self._settings, name, level)
        self.engager.team_levels = [load_level(self._settings, m.name) for m in self.members]
        log.info("Niveaux lus dans la liste des amis : niveau maximal des groupes %d", self.engager.level_limit())

    def _walk_area(self) -> set | None:
        """Cases joignables à pied d'où est le principal (carte officielle, brain/arena.walk_area) : depuis sa case
        d'arrivée si le soleil pris est dans la référence ; None si la carte ou la case ne se savent pas."""
        traveler = self.roamer.traveler
        # 31/10 : la carte du client d'abord (numéro du GDM et sa clé, brain/mapdata.py : 60 cartes sur 60 d'accord
        # avec les références) ; les références par coordonnées lues en secours.
        from bot.brain.arena import fight_arena

        arena = fight_arena(getattr(self.leader.game, "net", None))
        if arena is not None:
            return walk_area(arena, traveler.arrival_cell)
        arenas = Arenas.load().by_coords(traveler.coords) if traveler.coords is not None else []
        return walk_area(arenas[0], traveler.arrival_cell) if len(arenas) == 1 else None

    def _note_placement(self, host: Member) -> None:
        """Sans placement de l'équipe : les cases de placement lues une fois, pour reconnaître la carte officielle et
        contrôler ses placements (24/10, utilisateur : vérifier la référence en jeu ; brain/arena.py)."""
        if getattr(host.ai, "map", None) is None:
            return
        told = self._told_placement(host)
        if told is not None:
            # 31/10 : cases de placement d'après le serveur (GP), exactes ; plus de lecture des couleurs des cases.
            placed = set(told[0] | told[1])
            host.ai.map.placed = (placed, host.game.look(park=False))
            host.ai._recognize_arena(placed=placed)
            return
        frame = self._placement_frame(host)
        if frame is None:
            return
        placement = read_placement(frame)
        placed = set().union(*placement.free.values(), *placement.occupied.values())
        if placed:
            host.ai.map.placed = (placed, frame)
            host.ai._recognize_arena(placed=placed)

    def _placement_frame(self, host: Member) -> Frame | None:
        """Capture du placement lisible dans la fenêtre de `host` : bandeau d'entrée parti, mode tactique (l'herbe
        brouille la couleur des cases). None si le placement est fini."""
        start = time.monotonic()
        while True:
            frame = host.game.look(park=False)
            if read_scene(frame).phase is not Phase.PLACEMENT:
                return None
            elapsed = time.monotonic() - start
            if elapsed < PLACEMENT_FIRST_TRY or (entry_banner(frame) and elapsed < PLACEMENT_GIVE_UP):
                host.game.sleep(self.poll)
                continue
            host.ai.ensure_tactical_mode(frame)
            return host.game.look(park=True)

    @staticmethod
    def _told_placement(host: Member) -> tuple[frozenset, frozenset, int | None] | None:
        """Cases de placement des deux équipes et numéro de la mienne d'après le serveur (GP) ; None sans écoute."""
        net = getattr(host.game, "net", None)
        if net is None or not hasattr(net, "placement_told") or not net.alive():
            return None
        return net.placement_told()

    def _colour_told(self, host: Member, seen: str | None) -> str | None:
        """Couleur de mon équipe : celle vue à l'écran, comparée à celle du serveur (équipe 0 supposée rouge) ; le
        serveur ne tranche que si l'écran ne peut pas et qu'un accord a déjà été vu à ce lancement (31/10 : la
        couleur sert à reconnaître les alliés, une erreur ferait tirer sur l'équipe, incident du 26/10)."""
        told = self._told_placement(host)
        index = told[2] if told is not None else None
        if index not in (0, 1):
            return seen
        guess = RED if index == 0 else BLUE
        if seen is not None:
            agree = seen == guess
            if self._told_colour_ok is not False:
                self._told_colour_ok = agree
            log.info("Équipe %d d'après le serveur (%s supposé) ; %s à l'écran : %s", index, guess, seen,
                     "accord" if agree else "DÉSACCORD, l'écran fait foi et le serveur ne tranchera plus")
            return seen
        if self._told_colour_ok:
            log.info("Couleur de mon équipe d'après le serveur : %s (équipe %d ; accord vu à un combat précédent)",
                     guess, index)
            return guess
        return None

    def _team_colour(self, placement: Placement, count: int) -> str | None:
        """Couleur de mon équipe : celle dont les cases occupées sont au nombre de mes personnages (8 contre 1 à 3
        monstres) ; None si on ne peut pas trancher (autant de monstres que de personnages). Cases occupées comptées
        entre les deux : un grand personnage paraît aussi sur la case au-dessus de la sienne, et deux des miens dans la
        même colonne, à deux rangées l'un de l'autre, n'en font qu'un pour standing_cells."""
        if self.my_team is not None:
            return self.my_team

        def gap(team: str) -> int:
            low, high = len(standing_cells(placement.occupied[team])), len(placement.occupied[team])
            return 0 if low <= count <= high else min(abs(low - count), abs(high - count))

        gaps = {team: gap(team) for team in (RED, BLUE)}
        mine, other = sorted(gaps, key=gaps.get)
        return mine if gaps[other] - gaps[mine] >= PLACE_COLOUR_MARGIN else None

    def _seat_team(self, host: Member, fighters: list[Member]) -> set[str]:
        """Toute l'équipe sur ses cases (team/placement.py) : à chaque passe, chacun dont la case prévue est libre y
        va, un clic dans sa fenêtre sans attendre ; une lecture des cases dans la fenêtre de `host` vérifie la passe.
        Ceux qui sont à leur place sont Prêts pendant l'attente de la passe suivante. Retient la case de chacun de ceux
        qui sont reconnus (self._cells). Renvoie les noms de ceux qui sont déjà Prêts."""
        started = time.monotonic()
        frame = self._placement_frame(host)
        if frame is None:
            return set()
        placement = read_placement(frame)
        if host.game.recorder.keeps():  # aucune capture de placement à 8 dans tests/fixtures (17/10)
            host.game.recorder.save(frame.image, "placement_equipe_avant")
        team = self._colour_told(host, self._team_colour(placement, len(fighters)))
        known: dict[str, Cell] = {}
        if team is None:
            # Autant de monstres que de personnages : la couleur se vérifie en déplaçant `host` (comme en solo).
            self._place(host)
            team = self.my_team
            if getattr(host.ai, "placed_cell", None) is not None:
                known[host.name] = host.ai.placed_cell
            frame = host.game.look(park=True)
            placement = read_placement(frame)
        elif self.learn_team and self.my_team is None:
            self.my_team = team
            log.info("Mon équipe pour ce combat : %s (%d personnages au placement)", team, len(fighters))
        if not team:
            log.info("Placement : couleur de mon équipe inconnue, personne ne bouge")
            return set()
        other = BLUE if team == RED else RED
        enemies = standing_cells(placement.occupied[other])
        if not enemies:
            log.info("Placement : je ne vois aucun ennemi, personne ne bouge")
            return set()
        by_name = {m.name: m for m in fighters}
        order, seen = self.router.play_order([m.name for m in fighters])
        log.info("Placement : ordre de jeu %s (%s)", ", ".join(order),
                 "vu aux combats précédents" if seen else "par défaut, aucun combat encore : celui de l'équipe")
        seats = [Seat(name, by_name[name].ai.wanted_range(), getattr(by_name[name].ai, "move_points", 3))
                 for name in order]
        standing = _standing(frame, placement)
        blockers = read_blocks(frame, standing) if tactical_mode(frame) else None
        fixed: dict[str, Cell] = {}
        refused: set[Cell] = set()  # cases libres où le clic n'a mené personne
        ready: set[str] = set()
        passes, plan, budget = 0, {}, time.monotonic() + PLACE_BUDGET
        while passes < PLACE_PASSES and time.monotonic() < budget:
            free = placement.free[team] - refused
            cells = free | standing_cells(placement.occupied[team])
            plan = plan_seats(seats, cells, enemies, blockers, fixed, free, known)
            fixed.update({name: cell for name, cell in known.items() if plan.get(name) == cell})
            moves = [(name, cell) for name, cell in next_moves(plan, known, free, order, passes < PLACE_PASSES - 1)
                     if name not in fixed]
            if not moves:
                break
            passes += 1
            log.info("Placement, passe %d : %s (%d case(s) libre(s))", passes,
                     ", ".join(f"{n} -> {c}" + ("" if plan[n] == c else " (détour)") for n, c in moves), len(free))
            sent = time.monotonic()
            for name, cell in moves:
                point, spread = clear_point(frame.grid, cell, standing)  # pas sur un personnage debout devant
                game = by_name[name].game
                layout = getattr(game, "layout", None) or frame.layout
                game.click(layout.point(*to_reference(frame.layout, point)), spread)
            if self.auto_ready:  # pendant que le jeu les déplace : Prêt de ceux qui ne bougeront plus
                for name in order:
                    if name in fixed and name not in ready and by_name[name] is not host:
                        self._ready(by_name[name])
                        ready.add(name)
            host.game.sleep(max(0.0, PLACE_SETTLE - (time.monotonic() - sent)))
            for attempt in range(2):
                frame = host.game.look(park=True)
                if read_scene(frame).phase is not Phase.PLACEMENT:
                    self._cells.update(known)
                    return ready
                placement = read_placement(frame)
                occupied = placement.occupied[team]
                if attempt or all(cell in occupied for _, cell in moves):
                    break
                host.game.sleep(PLACE_RECHECK)  # un déplacement pas encore affiché
            standing = _standing(frame, placement)
            for name, cell in moves:
                if cell in occupied:
                    known[name] = cell
                    if plan[name] == cell:
                        fixed[name] = cell
                else:
                    refused.add(cell)
                    known.pop(name, None)  # clic sans effet, ou tombé sur une autre case : on ne sait plus
                    log.info("Placement : %s n'est pas allé en %s", name, cell)
            guess = by_elimination(known, order, standing_cells(occupied))
            if guess is not None:
                known[guess[0]] = guess[1]
                log.info("Placement : %s en %s (la seule case des miens qui reste)", *guess)
        self._cells.update(known)
        self._placement_report(host, frame, order, plan, known, enemies, started, passes)
        return ready

    def _placement_report(self, host: Member, frame: Frame, order: list[str], plan: dict[str, Cell],
                          known: dict[str, Cell], enemies: set[Cell], started: float, passes: int) -> None:
        placed = [n for n in order if n in known and plan.get(n) == known[n]]
        unknown = [n for n in order if n not in known]
        log.info("Placement fait en %.1f s, %d passe(s) : %d personnage(s) sur leur case prévue, %d reconnu(s) "
                 "sur %d%s", time.monotonic() - started, passes, len(placed), len(known), len(order),
                 f" ; à repérer à leur premier tour : {', '.join(unknown)}" if unknown else "")
        if host.game.recorder.keeps():
            marks = [(frame.grid.center(c), f"{order.index(n) + 1} {n}") for n, c in known.items()]
            host.game.recorder.save(annotate(frame, cells=set(enemies), marks=marks), "placement_equipe")

    def _ready(self, member: Member) -> None:
        """Un seul appui : un second annulerait le premier.

        TODO (20/10, fenêtre sourde aux touches, S3 à S8) : F1 « Prêt » peut être ignoré comme F1 de fin de tour. Le
        remède serait un clic sur le bouton Prêt quand la fenêtre est sourde (brain/recovery.is_deaf), mais il faut
        d'abord savoir ce qui change à l'écran après Prêt (bouton qui change d'aspect ? encadré du portrait ?) : rien
        n'est encore validé sur des captures, et un second Prêt annulerait le premier. Pas de clic ici en attendant :
        captures de placement avant et après Prêt à relever (une fenêtre qui a reçu F1, une qui l'a ignoré)."""
        frame = member.game.look(park=False)
        scene = read_scene(frame)
        if scene.phase is not Phase.PLACEMENT:
            return
        if self.team.ready_key:
            log.info("Placement : Prêt (%s)", self.team.ready_key.upper())
            member.game.press(self.team.ready_key)
        elif scene.ready_button is not None:
            log.info("Placement : je clique sur Prêt")
            member.game.click(scene.ready_button, frame.layout.spread(*READY_SPREAD))
        member.game.sleep(0.03)

    def _wait_combat(self, host: Member) -> bool:
        """Jusqu'au début du combat (timeline affichée). Entre Prêt et la timeline, l'écran ressemble quelques secondes
        à du hors combat : seul un hors combat qui dure veut dire que le combat n'a pas eu lieu."""
        start, out_since, warned, combat_since = time.monotonic(), None, False, None
        while True:
            scene = read_scene(host.game.look(park=False))
            phase = scene.phase
            now = time.monotonic()
            if phase is Phase.COMBAT:
                # La timeline lue : le combat a commencé. Le bouton de fin de tour seul s'affiche parfois un instant juste
                # après Prêt (03/10) : il doit durer.
                if scene.current_team is not None:
                    return True
                combat_since = combat_since or now
                if now - combat_since >= SETTLE_SECONDS:
                    return True
            else:
                combat_since = None
            if phase is Phase.OUT_OF_COMBAT:
                if out_since is None:
                    out_since = now
                elif now - out_since >= 2 * SETTLE_AFTER_READY:
                    log.warning("Ni placement ni combat : le combat n'a pas eu lieu")
                    return False
            else:
                out_since = None
                if self.auto_ready and not warned and now - start >= COMBAT_START_WAIT:
                    warned = True
                    log.warning("Le combat ne commence pas : un personnage n'est sans doute pas prêt "
                                "(clique sur Prêt dans sa fenêtre)")
            host.game.sleep(0.3)

    def _turns(self, fighters: list[Member]) -> tuple[Member, Frame] | None:
        """Jusqu'à la fin du combat. Les tours des monstres sont regardés dans la dernière fenêtre active. Renvoie la
        fenêtre et la capture où la fenêtre de fin de combat a été vue (None : fin vue sans elle)."""
        watcher = next((m for m in fighters if m.in_front()), fighters[0])
        out_since = None
        began = time.monotonic()
        started = False  # un tour lu dans la timeline : avant, un hors combat peut être l'écran d'après Prêt
        signals = self.signals
        if signals is not None:
            signals.new_fight()  # notifications d'avant les tours (hors combat : invitations, échanges) ignorées
        while True:
            if self.deadline is not None and time.monotonic() >= self.deadline + OVERTIME and not self._overtime_warned:
                # Jamais d'arrêt en plein combat (21/10 : avant, le bot s'arrêtait ici, l'équipe restait sans lui).
                self._overtime_warned = True
                log.warning("Durée de la session dépassée de %.0f min en plein combat : je finis ce combat, puis la "
                            "session s'arrête", OVERTIME / 60)
                self._stop_reason = self._stop_reason or "durée de la session dépassée"
            frame = watcher.game.look(park=False)
            scene = read_scene(frame)
            self._report(watcher, frame, scene)
            if signals is not None:
                # Signaux : en attente d'un tour seulement, et pas avec l'écoute du serveur (GTS dit qui joue).
                signals.enabled_for_polling = scene.phase is Phase.COMBAT and getattr(watcher.game, "net", None) is None
            if scene.phase is Phase.OUT_OF_COMBAT:
                told = self._fight_told(fighters, began)
                if told is not None:
                    # 31/10, utilisateur : la fin du combat d'après le serveur seulement (GE), plus fiable que l'écran
                    # (boss du donjon Bouftou : Eldieso K.O., sa fenêtre hors combat prise pour la fin).
                    if told is False:  # en cours : K.O., écran de transition ; un autre en combat est regardé
                        still = next((m for m in fighters if m is not watcher
                                      and read_scene(m.game.look(park=False)).phase is not Phase.OUT_OF_COMBAT), None)
                        if still is not None:
                            log.info("%s hors combat (K.O. ?), combat en cours d'après le jeu : je regarde la fenêtre "
                                     "de %s", watcher.name, still.name)
                            watcher = still
                        out_since = None
                        watcher.game.sleep(self.poll)
                        continue
                    # Fini d'après le jeu : les fenêtres de résultat s'ouvrent une fois la carte rechargée (GDK) dans
                    # chaque fenêtre ; elles sont fermées d'après le serveur (_close_told_results).
                    if out_since is None:
                        out_since = time.monotonic()
                    if self._end_loaded(fighters) or time.monotonic() - out_since >= END_LOAD_WAIT:
                        self._told_end = True
                        return None
                    watcher.game.sleep(self.poll)
                    continue
                # Sans écoute de ce combat (bot relancé en plein combat) : l'écran décide.
                still = next((m for m in fighters if m is not watcher
                              and read_scene(m.game.look(park=False)).phase is not Phase.OUT_OF_COMBAT), None)
                if still is not None:
                    # 31/10 (boss du donjon Bouftou) : Eldieso K.O. à son tour, sa fenêtre hors combat prise pour la fin
                    # du combat, salle comptée deux fois. Un autre est encore en combat : sa fenêtre est regardée.
                    log.info("%s hors combat (K.O. ?), %s encore en combat : je regarde sa fenêtre", watcher.name,
                             still.name)
                    watcher, out_since = still, None
                    continue
                if watcher.popups.describe(frame) is not None:
                    return watcher, frame  # fenêtre de résultat (ou de niveau supérieur) : le combat est fini
                if out_since is None:
                    out_since = time.monotonic()
                elif time.monotonic() - out_since >= (FIGHT_END_CONFIRM if started else START_CONFIRM):
                    return None
            else:
                out_since = None
                started = started or scene.current_team is not None
                if scene.phase is Phase.COMBAT:
                    player = self._player(watcher, frame, scene, fighters)
                    if player is not None:
                        if signals is not None:
                            signals.playing(player.name)  # relevé coupé pendant son tour (enregistrement seulement)
                        self._play(player, scene)
                        if signals is not None:
                            signals.played(player.name)  # sa notification lue en retard ne comptera pas
                        watcher = player
                        continue
                    watcher = next((m for m in fighters if m.in_front()), watcher)  # pas de retour inutile
            watcher.game.sleep(self.poll)

    @staticmethod
    def _fight_told(fighters: list[Member], began: float) -> bool | None:
        """Le combat d'après le serveur : False en cours (GJK entendu, pas encore GE), True fini (GE, GCK, carte
        rechargée), None si aucune écoute n'a vu ce combat commencer (début entendu au plus FIGHT_HEARD_BEFORE s avant
        `began`, le début des tours : le placement le précède)."""
        told = None
        for member in fighters:
            net = getattr(member.game, "net", None)
            fight = getattr(net, "fight", None) if net is not None else None
            if fight is None or not net.alive():
                continue
            if net.in_fight():
                return False
            if fight.started_at >= began - FIGHT_HEARD_BEFORE:
                told = True
        return told

    def _player(self, watcher: Member, frame: Frame, scene: Scene, fighters: list[Member]) -> Member | None:
        """Le personnage dont c'est le tour ; None pendant le tour d'un monstre ou d'une invocation alliée."""
        told = self._told_player(watcher, scene, fighters)
        if told is not False:
            return told
        if scene.current_team is None or (self.my_team is not None and scene.current_team != self.my_team):
            return None  # timeline illisible un instant, ou tour d'un monstre
        portrait = current_portrait(frame)
        names = [m.name for m in fighters]
        owner = self.router.owner(portrait, names)
        just = self._just_played if self._just_played is not None \
            and time.monotonic() - self._just_played[1] < REPLAY_GUARD else None
        if owner is not None and just is not None and owner == just[0]:
            return None  # timeline pas encore à jour juste après son tour (03/10 : Walitcoin a rejoué après F1)
        if owner is not None:
            if self.my_team is None and self.learn_team:
                # Portrait d'un de mes personnages encadré : l'équipe qui joue est la mienne (sans placement choisi,
                # rien d'autre ne la donne en début de combat).
                self.my_team = scene.current_team
                log.info("Mon équipe pour ce combat : %s (portrait de %s dans la timeline)", self.my_team, owner)
            return self._unless_stuck(self.by_name[owner])  # portrait reconnu : directement sa fenêtre
        if scene.timer_visible and just is not None and watcher.name == just[0]:
            # Son timer encore affiché juste après son F1 (fin de tour sans attendre l'animation) : ce n'est pas un
            # nouveau tour. 04/10 : pris pour le tour de Fourbocinq pendant celui d'un monstre, portrait du monstre
            # retenu comme le sien et équipe bleue au lieu de rouge : 25 minutes de tours perdus.
            return None
        if scene.timer_visible:
            return self._unless_stuck(self._found(watcher, scene, portrait))  # le timer de son tour, fenêtre regardée
        if self.router.is_other(portrait):
            return None
        now = time.monotonic()
        if self._turn_seen is None or not _same_turn(self._turn_seen[0], portrait):
            self._turn_seen = (portrait, now)
        # Notification de Windows (team.turn_signal: notifications) : SA fenêtre tout de suite, sans attendre ici. Ce
        # n'est qu'un indice : on n'y joue que si le timer de son tour y est affiché (un portrait qui ressemble ne
        # suffit pas). Pas encore de timer (il se remplit en ~1 s) : sa fenêtre est devant, _turns y regarde ensuite.
        hinted = self.signals.take(names) if self.signals is not None else None
        if hinted is not None:
            self._hinted = (hinted, now)
        if hinted is not None and hinted != watcher.name:
            member = self.by_name[hinted]
            seen = member.game.look(park=False)
            found = read_scene(seen)
            if found.phase is Phase.COMBAT and found.timer_visible:
                log.info("Notification : tour de %s (timer vu dans sa fenêtre)", hinted)
                return self._found(member, found, current_portrait(seen) or portrait)
            if self._early_start(member, found, seen):
                return member
            log.info("Notification : tour de %s ? Pas encore de timer dans sa fenêtre : j'y attends", hinted)
            return None
        if self._hinted is not None and self._hinted[0] == watcher.name and now - self._hinted[1] < HINT_PATIENCE:
            if self._early_start(watcher, scene, frame):
                return watcher
            return None  # sa notification : son timer va s'afficher ici, pas d'aller-retour dans les autres fenêtres
        if now - self._turn_seen[1] < TIMER_FILL:
            return None  # son timer va peut-être s'afficher ici : on attend avant de changer de fenêtre
        if not self._search_due(portrait):
            return None
        # Portrait pas encore connu : sa fenêtre est celle qui affiche le timer de son tour.
        for name in self.router.candidates(portrait, [n for n in names if n != watcher.name]):
            member = self.by_name[name]
            seen = member.game.look(park=False)
            found = read_scene(seen)
            if found.phase is Phase.COMBAT and found.timer_visible:
                return self._found(member, found, current_portrait(seen) or portrait)
        self._search_failed(portrait)
        return None

    def _told_player(self, watcher: Member, scene: Scene, fighters: list[Member]) -> Member | None | bool:
        """Qui joue d'après le jeu (« GTS<id> », « GTF<id> », connection/network.py, 26/10) : le personnage, None
        (tour d'un monstre, ou tour fini, ou tour déjà joué), False si l'écoute ne le dit pas (l'écran décide)."""
        net = getattr(watcher.game, "net", None) or getattr(self.leader.game, "net", None)
        if net is None or not net.alive() or not net.in_fight():
            return False
        turn, over, turn_at = net.fight_turn()
        if turn is None:
            return False
        if over:
            return None
        member = next((m for m in fighters if getattr(getattr(m.game, "net", None), "my_id", None) == turn), None)
        if member is None:
            return False if turn > 0 else None  # un personnage pas encore reconnu : l'écran ; un monstre : attendre
        if self._just_played is not None and self._just_played[0] == member.name and turn_at <= self._just_played[1]:
            return None  # ce tour-là est déjà joué (GTF pas encore arrivé)
        if self.my_team is None and self.learn_team:
            # Jamais la couleur de la timeline ici : le tour est annoncé par le jeu avant qu'elle ne se mette à jour
            # (26/10, journal 205705 : équipe des Blops prise pour la mienne, mes personnages se sont tapé dessus).
            # La couleur du cercle sur sa case annoncée par le jeu.
            # Contrôle de l'équipe annoncée par le serveur (GP) ; elle ne sert que si le cercle n'est pas vu et qu'un
            # accord a déjà été constaté à ce lancement.
            colour = self._colour_told(member, _circle_colour(member))
            if colour is None:
                return False  # pas encore vue : l'écran décide, comme avant
            self.my_team = colour
            log.info("Mon équipe pour ce combat : %s (cercle de %s sur sa case annoncée par le jeu)", colour, member.name)
        return self._unless_stuck(member)

    def _early_start(self, member: Member, scene: Scene, frame: Frame) -> bool:
        """Notification de son tour, et la timeline de sa fenêtre montre le tour de mon équipe : il joue tout de suite,
        sans attendre que son timer se remplisse (EARLY_START). Pas si la timeline encadre le portrait connu d'un autre
        de mes personnages. Son portrait n'est pas retenu ici : la timeline peut encore encadrer l'allié d'avant ; il
        l'est quand son timer s'affiche (_shows_turn)."""
        if scene.phase is not Phase.COMBAT or self.my_team is None or scene.current_team != self.my_team:
            return False
        owner = self.router.owner(current_portrait(frame))
        if owner is not None and owner != member.name:
            return False
        log.info("Notification : tour de %s (timeline de mon équipe) : je joue sans attendre son timer", member.name)
        self.router.learn(member.name, None)
        self._search = None
        self._hinted = None
        self._early = (member.name, time.monotonic() + EARLY_START)
        return True

    def _unless_stuck(self, member: Member) -> Member | None:
        """None si ce personnage vient de finir STUCK_TURNS tours d'affilée sans que la main passe : ce n'était sans
        doute pas son tour (16/10 : timer d'une invocation dans sa fenêtre, carte cachée par « Challenge en cours »)."""
        until = self._stuck.get(member.name)
        if until is not None and time.monotonic() < until:
            return None
        return member

    def _found(self, member: Member, scene: Scene, portrait: Portrait | None) -> Member:
        self.router.learn(member.name, portrait)
        self._search = None
        self._hinted = None
        if self.learn_team and scene.current_team and scene.current_team != self.my_team:
            if self.my_team is not None:
                log.warning("Mon équipe est %s, pas %s (timer du tour de %s)", scene.current_team, self.my_team, member.name)
            else:
                log.info("Mon équipe pour ce combat : %s (timer du tour de %s)", scene.current_team, member.name)
            self.my_team = scene.current_team
        return member

    def _search_due(self, portrait: Portrait | None) -> bool:
        """Un seul tour des fenêtres par tour de jeu, plus un second un peu plus tard (timer pas encore affiché)."""
        if self._search is None or not _same_turn(self._search[0], portrait):
            return True
        wait = SEARCH_RETRY if self.my_team is not None else 3 * SEARCH_RETRY  # équipe inconnue : peut-être un monstre
        return time.monotonic() - self._search[2] >= wait

    def _search_failed(self, portrait: Portrait | None) -> None:
        now = time.monotonic()
        same = self._search is not None and _same_turn(self._search[0], portrait)
        tries, first = (self._search[1] + 1, self._search[3]) if same else (1, now)
        self._search = (portrait, tries, now, first)
        # Couleur de l'équipe connue : un allié qui n'est aucun des personnages (invocation). Inconnue, on ne conclut
        # rien : ce pourrait être un personnage dont le timer tarde, il ne faut pas sauter son tour.
        if tries >= 2 and now - first >= OTHER_AFTER and portrait is not None and self.my_team is not None:
            self.router.learn_other(portrait)
            self._search = None

    def _play(self, member: Member, scene: Scene) -> None:
        """Le tour du personnage, par son IA (brain/combat.py, play_turn) : options du mode équipe, si elle les connaît."""
        self.router.learn(member.name, None)  # ordre de jeu
        focus = getattr(member.game, "focus", None)
        if focus is not None:
            focus.to(member.window)  # sa fenêtre devant dès maintenant : le journal porte son nom
        # Fenêtre connue par-dessus la carte (16/10 : « Challenge en cours » chez Dinarpepe, ni ennemi ni personnage
        # vus) : fermée avant de jouer. Pas de touche de secours pour une fenêtre inconnue en plein combat.
        frame = member.game.look(park=False)
        if find_known_popup(frame, member.popups.specs) is not None:
            member.popups.handle(frame, None)
        # 17/10 (journal 155034) : menu de Flash ouvert par le clic droit qui a fait entrer Vertdepomme dans le combat,
        # par-dessus la timeline : tour illisible (« équipe ? »), 2 min 30 perdues à chacun de ses tours. Cherché dans
        # toute la fenêtre (0,16 s) au premier tour de chacun dans le combat.
        if member.name not in self._flash_checked:
            self._flash_checked.add(member.name)
            if find_flash_menu(frame, anywhere=True) is not None:
                log.info("Menu de Flash resté ouvert dans la fenêtre de %s : je le ferme avant de jouer", member.name)
                close_flash_menu(member.game, frame)
        options = {}
        if _accepts(member.ai.play_turn, "is_mine"):
            options["is_mine"] = lambda frame: self._shows_turn(member, frame)
        elif len(self.members) > 1 and not self._warned_is_mine:
            self._warned_is_mine = True
            log.warning("L'IA de combat ne sait pas encore vérifier que c'est le tour de CE personnage (is_mine) : "
                        "avec plusieurs alliés, la fin de son tour peut être mal vue")
        cell = self._cells.get(member.name)
        if cell is not None and _accepts(member.ai.play_turn, "me_cell"):
            options["me_cell"] = cell  # l'IA suit ensuite ma case après chacun de mes déplacements
        if _accepts(member.ai.play_turn, "find_me"):
            options["find_me"] = lambda frame, mine: self._find_me(member, mine, frame)
        if _accepts(member.ai.play_turn, "team_cells"):  # alliés au cercle caché (sous la timeline, 03/10)
            # Avec leur nom : le journal du combat dit qui gêne (« je ne me mets pas devant Imthebest »).
            options["team_cells"] = lambda: {cell: name for name, cell in self._cells.items() if name != member.name}
        member.ai.play_turn(self.my_team or scene.current_team, **options)
        self._progress("tour joué")
        self._early = None
        self._just_played = (member.name, time.monotonic())
        if getattr(member.ai, "turns_not_passed", 0) >= STUCK_TURNS:
            member.ai.turns_not_passed = 0
            self._stuck[member.name] = time.monotonic() + STUCK_PAUSE
            path = member.game.recorder.save(member.game.look(park=False).image, "tour_qui_ne_passe_pas", lossless=True)
            log.warning("%s : %d tours d'affilée sans que la main passe, ce n'est sans doute pas son tour : je ne le fais "
                        "plus jouer pendant %.0f s (capture : %s)", member.name, STUCK_TURNS, STUCK_PAUSE, path)
        tracked = getattr(member.ai, "_me_cell", None)  # case où l'IA m'a vu en dernier (fin du tour)
        if tracked is not None and getattr(member.ai, "me_sure", True):
            self._cells[member.name] = tracked
        elif getattr(member.ai, "me_sure", True) is False:
            # Case pas prouvée (19/10 : un Crâ sur le cercle d'un autre, gardé d'un tour à l'autre, 8 déplacements ratés
            # de plus) : oubliée plutôt que transmise ; elle n'écarte pas non plus ce cercle chez les autres (_find_me).
            self._cells.pop(member.name, None)

    def _find_me(self, member: Member, mine: list[Fighter], frame: Frame | None = None) -> Fighter | None:
        """Moi parmi les cercles de ma couleur. Jamais un cercle posé sur la case d'un autre personnage. Le seul qui
        reste, ou le plus proche de ma case suivie ; aucun : mon cercle est caché par le corps d'un allié debout devant
        moi (03/10 : Imthebest a pris le cercle de Jesuisleking pour le sien, portée et déplacements faux), je suis
        sur ma case suivie. None si ce n'est pas sûr."""
        others = {cell for name, cell in self._cells.items() if name != member.name}
        left = [f for f in mine if f.cell not in others]
        tracked = getattr(member.ai, "_me_cell", None) or self._cells.get(member.name)
        exact = next((f for f in left if f.cell == tracked), None)
        if exact is not None:
            return exact  # un cercle sur ma case suivie : moi
        if tracked is None:
            # Premier tour du combat (pas de placement choisi, team.place) : repéré par la portée d'un sort
            # (CombatAI._locate_by_range), même s'il ne reste qu'un cercle : le mien peut être caché.
            return None
        known_all = all(m.name in self._cells for m in self.members if m.name != member.name)
        if len(left) == 1 and distance(left[0].cell, tracked) <= 2 and known_all:
            # Le seul cercle qui reste, quand la case de chacun des autres est connue (sûre) : moi, par élimination.
            # Un autre à la case oubliée (pas prouvée, 19/10) pourrait être ce cercle-là : la portée tranchera.
            return left[0]
        # Pas de cercle sur ma case suivie : périmée (poussé par un monstre) ou cachée. L'IA de combat me repère par la
        # portée d'un sort (une fois par tour), puis se fie à ma case suivie.
        return None

    def _shows_turn(self, member: Member, frame: Frame) -> bool:
        """C'est encore le tour de ce personnage (is_mine de son IA) : le timer de son tour dans sa fenêtre OU son
        portrait encadré dans la timeline. Ni l'un ni l'autre : revu sur une 2e capture avant de dire non.

        11/10 : le portrait seul ne suffit pas. Un ennemi tué sort de la timeline, le portrait de Brawli glisse d'une
        place et n'a plus été reconnu : tour coupé en plein milieu, recommencé avec ses compteurs à zéro (Tempête de
        Puissance et Amplification relancées, refusées). Le timer, lui, peut manquer en fin de décompte : les deux ne
        se trompent pas au même moment."""
        mine = self._turn_marks(member, frame)
        if mine is not None:
            return mine
        member.game.sleep(TURN_MARKS_RECHECK)
        return bool(self._turn_marks(member, member.game.look(park=False)))

    def _turn_marks(self, member: Member, frame: Frame) -> bool | None:
        """True : son timer ou son portrait ; False : plus en combat ; None : ni l'un ni l'autre."""
        scene = read_scene(frame)
        if scene.phase is not Phase.COMBAT:
            return False
        net = getattr(member.game, "net", None)
        if net is not None and net.my_id is not None and net.alive() and net.in_fight():
            # Son tour d'après le jeu (GTS, GTF) : sans attendre timer ni portrait (connection/network.py, 26/10).
            turn, over, _ = net.fight_turn()
            if turn is not None:
                return turn == net.my_id and not over
        early = self._early is not None and self._early[0] == member.name
        if scene.timer_visible:
            if early:  # parti sur notification : son timer, enfin ; le portrait encadré maintenant est le sien
                self._early = None
                self.router.learn(member.name, current_portrait(frame))
            return True
        known = self.router.portraits.get(member.name)
        if known is not None and same_portrait(current_portrait(frame), known):
            return True
        if early and time.monotonic() < self._early[1] and scene.current_team == self.my_team:
            return True
        return None

    def _close_results(self, watcher: Member, frame: Frame, fighters: list[Member]) -> None:
        """Fenêtre de résultat fermée dans chaque fenêtre du combat, vue une fois chez `watcher`. Les suiveurs d'abord :
        le principal reste devant. Le tour des fenêtres qui suit ferme ce qui reste (niveau supérieur, qui n'arrive pas
        à tout le monde).

        Plus de clic à l'aveugle (25/10, journal 181212 : « Fermer », vu chez Elseiso, cliqué au même endroit dans les 8
        fenêtres ; dans la sienne, rien ne couvrait plus la barre de raccourcis à cet endroit, il a bu une potion de
        Brâkmar). Avec 8 personnages, le bouton est juste au-dessus des objets de la barre : chaque fenêtre est
        regardée, et cliquée seulement si son bouton y est vu, immobile (fenêtre ouverte)."""
        from bot.brain.popups import still_open  # ici : la ligne d'import du module est partagée avec d'autres travaux

        known = find_known_popup(frame, watcher.popups.specs)
        if known is None or known.close_point is None:
            # Vue en train de s'ouvrir, sans son bouton encore (03/10 : 2 combats sur 14) : un second regard.
            watcher.game.sleep(RESULT_LAG)
            frame = watcher.game.look(park=False)
            known = find_known_popup(frame, watcher.popups.specs)
        if known is None or known.spec.name != RESULT or known.close_point is None:
            return  # niveau supérieur par-dessus, chez celui-ci : le tour des fenêtres s'en charge
        watcher.game.sleep(RESULT_LAG)
        again = find_known_popup(watcher.game.look(park=False), watcher.popups.specs)
        if not _same_button(again, known, frame.layout.length(3)):
            log.info("Bouton « Fermer » du résultat pas encore immobile : fermé fenêtre par fenêtre")
            return
        log.info("Fenêtre « %s » : je la ferme dans les %d fenêtres du combat", RESULT, len(fighters))
        # Même combat, même fenêtre de résultat : son bouton, immobile chez `watcher`, est à la même place (repère de
        # référence) dans chaque fenêtre une fois ouverte. Ailleurs, la fenêtre de ce personnage s'ouvre encore : revue
        # une fois (still_open) avant tout clic.
        place = frame.layout.to_reference(again.close_point)
        tolerance = 3  # px de référence
        for member in sorted(fighters, key=lambda m: m is self.leader):
            shot = None if member is watcher else member.game.look(park=False)
            seen = again if shot is None else find_known_popup(shot, member.popups.specs)
            if seen is None or seen.spec.name != RESULT or seen.close_point is None:
                log.info("%s : pas de bouton « Fermer » du résultat vu dans sa fenêtre, pas de clic", member.name)
                continue
            if shot is not None and max(abs(a - b) for a, b in
                                        zip(shot.layout.to_reference(seen.close_point), place)) > tolerance:
                seen = still_open(member.game, seen, member.popups.specs, shot, RESULT_LAG)
                if seen is None or max(abs(a - b) for a, b in
                                       zip(shot.layout.to_reference(seen.close_point), place)) > tolerance:
                    log.info("%s : bouton « Fermer » pas encore à sa place (fenêtre en train de s'ouvrir), pas de clic : "
                             "le tour des fenêtres la fermera", member.name)
                    continue
            member.game.click(seen.close_point, seen.close_spread)
            member.popups.closed_elsewhere(RESULT)

    @staticmethod
    def _end_loaded(fighters: list[Member]) -> bool:
        """Carte rechargée (GDK) après la fin du combat dans chaque fenêtre qui écoute le serveur : les fenêtres de
        résultat s'ouvrent maintenant."""
        for member in fighters:
            net = getattr(member.game, "net", None)
            fight = getattr(net, "fight", None) if net is not None else None
            if net is None or not net.alive() or fight is None:
                continue
            if not fight.ended_at or net.loaded_at < fight.ended_at:
                return False
        return True

    def _close_told_results(self, fighters: list[Member]) -> None:
        """Fin du combat d'après le serveur, carte rechargée : une fenêtre de résultat s'ouvre dans chaque fenêtre du
        combat. Toutes regardées à tour de rôle (suiveurs d'abord, le principal reste devant), chacune cliquée dès que
        son bouton « Fermer » est à sa place : la première revue RESULT_LAG s plus tard (bouton immobile, fenêtre
        ouverte), les autres d'un seul regard quand leur bouton est à la même place (repère de référence), sinon
        revues comme la première. Pas vue en RESULT_OPEN_WAIT s : le tour des fenêtres qui suit s'en charge ; il
        regarde aussi chaque fenêtre et referme celle restée ouverte (le serveur ne dit rien de la fermeture,
        fenêtre du jeu seulement : seul l'écran la confirme).

        01/11 après-midi : deux regards exigés par fenêtre, ~0,3 s chacun ; la boucle finissait à 4 s avant d'avoir
        revu les fenêtres cliquées (« fermée chez 0/8 », fermées en fait)."""
        from bot.brain.popups import still_open  # ici : la ligne d'import du module est partagée avec d'autres travaux

        left = sorted(fighters, key=lambda m: m is self.leader)
        place: tuple[float, float] | None = None  # bouton « Fermer » vu immobile, en repère de référence
        why: dict[str, str] = {}  # pourquoi pas encore cliquée, pour le journal
        tolerance = 3  # px de référence
        start = time.monotonic()
        deadline = start + RESULT_OPEN_WAIT
        while left and time.monotonic() < deadline:
            for member, frame, seen in _popup_views(left):
                if seen is None:
                    continue  # pas encore ouverte
                why[member.name] = seen.spec.name
                if seen.spec.name != RESULT:
                    # « Informations » (niveau supérieur) par-dessus : le « Fermer » du résultat ne répond pas avant.
                    member.popups.handle(frame, self._since_combat())
                    continue
                if seen.close_point is None:
                    why[member.name] = "bouton « Fermer » pas encore vu"
                    continue  # en train de s'ouvrir
                spot = frame.layout.to_reference(seen.close_point)
                if place is None or max(abs(a - b) for a, b in zip(spot, place)) > tolerance:
                    seen = still_open(member.game, seen, member.popups.specs, frame, RESULT_LAG)
                    if seen is None or seen.spec.name != RESULT or seen.close_point is None:
                        why[member.name] = f"bouton « Fermer » qui bouge (à {spot}, référence {place})"
                        continue  # bouton qui bouge : fenêtre en train de s'ouvrir, revue au tour suivant
                    place = place or frame.layout.to_reference(seen.close_point)
                member.game.click(seen.close_point, seen.close_spread)
                member.popups.closed_elsewhere(RESULT)
                left.remove(member)
            if left:
                self.leader.game.sleep(0.05)
        log.info("Fenêtre « %s » : « Fermer » cliqué chez %d/%d en %.1f s (fin d'après le jeu)", RESULT,
                 len(fighters) - len(left), len(fighters), time.monotonic() - start)
        if left:
            log.info("Pas cliquée chez %s : le tour des fenêtres s'en charge",
                     ", ".join(f"{m.name} ({why.get(m.name, 'rien de vu')})" for m in left))

    def _fight_over(self) -> None:
        log.info("Combat terminé")
        self._progress("combat fini")
        # 01/11 : combattants d'après le serveur, la teinte ne compte plus (CombatAI.blind_fight).
        blind = [m.name for m in self.members if getattr(m.ai, "blind_fight", False)]
        tinted = [m.name for m in self.members if getattr(m.ai, "unknown_tint", False) and m.name not in blind]
        if tinted:
            log.info("Sol tactique pas reconnu (teinte jour/nuit) chez %s, sans conséquence : combattants d'après le "
                     "serveur", ", ".join(tinted))
        if len(blind) >= UNKNOWN_TINT_STOP and not self._stop_reason:
            # 26/10 (journal 012810) : nuit plus profonde qu'à minuit, teinte d'une autre forme pas encore apprise
            # (perception/daylight.py) ; les 8 personnages, sans se repérer, ont passé tous leurs tours. Mieux vaut
            # s'arrêter que d'enchaîner des combats à l'aveugle ; les captures « sol_tactique_inconnu » servent à
            # apprendre cette teinte.
            self._stop_reason = (f"teinte jour/nuit pas reconnue par {len(blind)} personnages ce combat (nuit pas "
                                 "encore apprise) : arrêt pour ne pas combattre à l'aveugle")
            log.warning("Teinte jour/nuit pas reconnue (%s) : je m'arrête après ce combat", ", ".join(blind))
        self._fight_errors = 0
        self._combat_ended_at = time.monotonic()
        self._last_team = self.my_team or self._last_team
        if self.learn_team:
            self.my_team = None  # la couleur de l'équipe peut changer d'un combat à l'autre
        self._round_due = True  # fenêtres de résultat, de niveau supérieur et vie : dans chaque fenêtre
        self._hp_report_due = True
        self._cells = {}
        if self.engager is not None:
            self._next_engage_at = time.monotonic() + random.uniform(*self.engager.settings.pause_between_fights)

    # Journal

    def _report(self, member: Member, frame: Frame, scene: Scene) -> None:
        """Journalise chaque changement d'état d'une fenêtre, avec une capture annotée."""
        summary = scene.summary()
        if self._summaries.get(member.name) == summary:
            return
        self._summaries[member.name] = summary
        log.info("État : %s", summary)
        if member.game.recorder.keeps():
            buttons = [(p, name) for p, name in ((scene.ready_button, "pret"), (scene.end_turn_button, "fin de tour")) if p]
            member.game.recorder.save(annotate(frame, marks=buttons, text=f"{member.name} : {summary}"),
                                      scene.phase.name.lower())

    def _observe(self) -> None:
        """Mode observation : journal de la fenêtre de l'équipe que tu as devant toi, sans changer de fenêtre ni rien
        toucher. En combat, il dit aussi à qui est le tour d'après la timeline : en passant toi-même d'une fenêtre à
        l'autre, tu vois si le timer ne s'affiche bien que dans la fenêtre du personnage qui joue."""
        member = next((m for m in self.members if m.in_front()), None)
        if member is None:
            self.leader.game.sleep(0.5)
            return
        frame = member.game.look(park=False)
        scene = read_scene(frame)
        portrait = current_portrait(frame) if scene.phase is Phase.COMBAT else None
        if portrait is not None and scene.timer_visible and scene.current_team is not None:
            # Couleur de l'équipe lue : pas pendant l'animation de fin de combat (le 01/10, portrait faussé appris là).
            self.router.learn(member.name, portrait)
        owner = self.router.owner(portrait) if portrait is not None else None
        summary = scene.summary() + (f", portrait de {owner}" if owner else ", portrait inconnu" if portrait else "")
        if self._summaries.get(member.name) != summary:
            self._summaries[member.name] = summary
            log.info("État : %s", summary)
            member.game.recorder.save(frame.image, f"observation_{scene.phase.name.lower()}")
        member.game.sleep(self.poll)
