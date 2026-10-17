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
from dataclasses import dataclass, replace

from bot.actions import keys
from bot.brain.combat import MapMemory
from bot.brain.engage import Engager, EngageSettings
from bot.brain.loop import PLACEMENT_FIRST_TRY, PLACEMENT_GIVE_UP, SETTLE_AFTER_READY, SETTLE_SECONDS
from bot.brain.rest import CHECK_EVERY, STOOL, STOOL_SPREAD, STOOL_ZONE, RestSettings
from bot.brain.travel import (RECLICK_EVERY, SCENE_CHANGED, SCENE_STABLE, Journey, TravelSettings, Traveler,
                              escape_menu, make_roamer, same_map)
from bot.brain.travel import _click_spot, _gap, _motion, _motion_view, _scene  # mêmes repères que le principal
from bot.debug.draw import annotate
from bot.debug.report import write_fight_report
from bot.perception.colors import BLUE, RED
from bot.perception.exits import DELTA
from bot.perception.fighters import Fighter, clear_point
from bot.perception.frame import Frame
from bot.perception.grid import Cell, distance
from bot.perception.maplabel import MapLabel, read_map_label
from bot.perception.placement import Placement, entry_banner, read_placement, standing_cells
from bot.perception.sight import read_blocks
from bot.perception.popups import find_context_menu, find_known_popup
from bot.perception.templates import match_template
from bot.perception.timeline import Portrait, current_portrait, same_portrait
from bot.perception.ui import READY_SPREAD, hp_ratio, pods_fill, tactical_mode
from bot.state.scene import Phase, Scene, read_scene
from bot.perception.chat import combat_lines, died
from bot.team.join import Joiner, recent_click
from bot.team.member import Member, to_reference
from bot.team.placement import Seat, by_elimination, next_moves, plan_seats
from bot.team.roster import TeamSettings
from bot.team.turns import TurnRouter

log = logging.getLogger(__name__)

FIGHT_END_CONFIRM = 1.0  # hors combat vu aussi longtemps pendant le combat : il est fini (pas un instant de transition)
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
OVERTIME = 300.0  # s au-delà de la durée de la session : arrêt même en plein combat (16/10 : 30 min prévues, 43 min)
# Pendant le tour d'un personnage, ni son timer ni son portrait encadré : revu sur une 2e capture avant de conclure
# que son tour est fini (11/10, idée validée par l'utilisateur).
TURN_MARKS_RECHECK = 0.2
POD_FULL = 0.9  # pods : à partir de là, direction la banque (demande de l'utilisateur, 11/10)
POD_LOG_STEP = 0.05  # pods notés au journal quand ils ont changé d'au moins 5 %
# Le principal ne passe pas un soleil que ses suiveurs passent : personnage bloqué par le jeu (11/10, 1,-13 : rare,
# débloqué par l'utilisateur avec un défi lancé à un autre personnage puis quitté). Au bout de LEADER_STUCK fois de
# suite, le bot s'arrête et le dit.
LEADER_STUCK = 2
FOLLOWER_STILL = 1.5  # s sans bouger avant de recliquer le soleil d'un suiveur (en marche, jamais)
MAX_POPUPS = 6  # regards d'affilée dans une même fenêtre du jeu : fermer résultat, niveau supérieur, et retenter
RESULT = "résultat du combat"  # même fenêtre, même bouton « Fermer » dans chaque fenêtre du combat
RESULT_LAG = 0.2  # elle s'ouvre partout en même temps, à un instant près
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
        self.leader, self.followers = members[0], members[1:]
        self.by_name = {m.name: m for m in members}
        self.team = team or TeamSettings.from_settings(settings)
        self.act = act
        engage_settings = EngageSettings.from_settings(settings)
        self.travel_settings = TravelSettings.from_settings(settings, travel, start, region)
        if self.travel_settings.mode in ("test", "explore", "aller"):
            engage = False  # déplacements (test, exploration, trajet) : pas de combat lancé par le bot
        enabled = engage_settings.enabled if engage is None else engage
        self.engager = Engager(self.leader.game, engage_settings) if enabled else None
        self.roamer = make_roamer(self.leader.game, self.travel_settings)
        self._crossings: list[Crossing] = []
        # Suiveurs partis vers le soleil en même temps que le principal (_dispatch) : capture de départ, heure du clic.
        self._dispatched: dict[str, tuple[Frame, float]] = {}
        self._pods: dict[str, float] = {}  # remplissage de la jauge des pods de chacun (0 à 1), au dernier tour des fenêtres
        self._lost: set[str] = set()  # suiveurs qui n'ont pas suivi : pas de clic à l'aveugle tant qu'ils n'ont pas rejoint
        self._seen_at: dict[str, tuple[int, int]] = {}  # dernière carte connue d'un suiveur perdu
        self._rejoin: dict[str, Journey] = {}  # trajet d'un suiveur perdu jusqu'à la carte du principal (_regroup)
        self._departure: tuple[int, int] | None = None  # carte que le principal vient de quitter
        self._together_checked = False  # équipe vue sur la carte du principal avant le premier trajet
        self._dispatch_to: tuple[int, int] | None = None  # carte où mène le soleil cliqué par toute l'équipe
        self._dispatch_side: str | None = None  # côté de ce soleil
        self._leader_stuck = 0  # fois de suite où les suiveurs sont passés sans le principal
        if self.roamer is not None:
            self.roamer.traveler.on_crossed = self._crossed
            self.roamer.traveler.on_click = self._dispatch
        if self.engager is not None and self.roamer is not None:  # zones hors d'atteinte gardées (brain/engage.py)
            self.engager.remember_in(self.roamer.traveler.world, self.roamer.traveler.place)
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
        self.auto_ready = bool(combat.get("auto_ready", True))
        self.poll = float(timing.get("poll", 0.15))
        self.router = TurnRouter([m.name for m in members])
        self.joiner = Joiner(self.team.join_timeout)
        self.finished = False
        self.fights = 0  # combats finis depuis le lancement
        self._stop_reason = ""  # limite de la session atteinte : arrêt après le prochain tour des fenêtres
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

    def run(self) -> None:
        while not self.finished:
            self.step()

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
        if self._round_due:
            host = self._round()
            if host is not None:
                self._fight(host)
            elif self._stop_reason:
                log.info("Fin de la session : %s", self._stop_reason)
                self.finished = True
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
        since = None if self._combat_ended_at is None else time.monotonic() - self._combat_ended_at
        for member in self.followers + [self.leader]:
            frame = member.game.look(park=False)
            self._check_layout(member, frame)
            for _ in range(MAX_POPUPS):
                if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                    return member
                if not member.popups.handle(frame, since):
                    break
                frame = member.game.look(park=False)
            if member.popups.describe(frame) is not None:
                continue  # fenêtre inconnue (ou qui ne se ferme pas) : on n'y touche pas
            if not member.spellbook.read_done:
                member.spellbook.read()  # une fois par personnage : ses sorts et caractéristiques, pour son IA de combat
                frame = member.game.look(park=False)
            ratio = hp_ratio(frame)
            if ratio is not None and ratio < self.rest_settings.below:
                self._tired.append(member)
            self._note_pods(member, frame)
        return None

    def _note_pods(self, member: Member, frame: Frame) -> None:
        """Pods de chacun (jauge autour de la minimap, hors combat) : au journal quand ils changent ; vers 90 %, à la
        banque (allers-retours à venir : docs/rapports/plan_banque.md)."""
        fill = pods_fill(frame)
        before = self._pods.get(member.name)
        self._pods[member.name] = fill
        if before is not None and abs(fill - before) < POD_LOG_STEP:
            return
        if fill >= POD_FULL:
            log.warning("Pods de %s : %.0f %% : il faut passer à la banque", member.name, 100 * fill)
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
            return
        if self.engager is None:
            self._roam()
            return
        if not self._together_before_fighting():
            return
        if time.monotonic() < self._next_engage_at or self.engager.engage():
            return  # combat lancé : le placement est vu au tour de boucle suivant
        # Comme en solo : groupe parti avant l'arrivée, on en cherche un autre aussitôt ; aucun groupe, on attend
        # ou (mode zone) toute l'équipe passe sur une carte voisine.
        self._empty_searches = self._empty_searches + 1 if self.engager.none_found else 0
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
        fenêtres toutes les CHECK_EVERY secondes jusqu'à ce que chacun soit remonté (ou rest.max_seconds)."""
        tired, self._tired = self._tired, []
        sitting = []
        for member in tired:
            frame = member.game.look()
            ratio = hp_ratio(frame)
            if ratio is None or ratio >= self.rest_settings.below:
                continue
            stool = match_template(frame, STOOL, box=frame.layout.box(*STOOL_ZONE))
            if stool is None:
                log.info("Vie à %.0f %% : tabouret introuvable, je me repose debout", 100 * ratio)
            else:
                log.info("Vie à %.0f %% : je m'assois jusqu'à %.0f %%", 100 * ratio, 100 * self.rest_settings.until)
                member.game.click(stool[0], frame.layout.spread(*STOOL_SPREAD))
            sitting.append(member)
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

    # Changement de carte

    def _crossed(self, side: str, cell: Cell, before: MapLabel, after: MapLabel) -> None:
        """Appelée par le Traveler du principal (brain/travel.py) après chaque passage réussi."""
        self._crossings.append(Crossing(side, cell, before, after))

    def _dispatch(self, side: str, cell: Cell, frame: Frame, spot: int) -> None:
        """Le principal vient de cliquer sur le soleil : chaque suiveur clique aussitôt le même (même case, à l'échelle
        de sa fenêtre), puis une capture de départ ; toute l'équipe marche en même temps (03/10 : les suiveurs ne
        partaient qu'une fois le principal arrivé, ~4 s perdues à chaque carte). L'arrivée de chacun est vérifiée
        ensuite (follow)."""
        point, spread = _click_spot(frame, cell, spot)
        reference = to_reference(frame.layout, point)
        self._departure = self.roamer.traveler.coords if self.roamer is not None else None  # pas encore arrivé
        delta = DELTA.get(side)
        self._dispatch_side = side
        self._dispatch_to = (self._departure[0] + delta[0], self._departure[1] + delta[1])             if self._departure is not None and delta else None  # carte où mène ce soleil (suiveurs passés seuls)
        names = []
        for member in self.followers:
            if member.name in self._lost:
                continue  # pas sur la carte du principal : il ne le suit pas à l'aveugle
            if not same_map(member.game.look(park=False), frame):
                # 11/10 : noms de carte illisibles dans la cité, un suiveur ailleurs a cliqué le même soleil sur sa
                # carte à lui et s'est éloigné. Pas le même décor que le principal : il le rejoindra par son trajet.
                log.warning("%s n'est pas sur la carte du principal (décor différent) : il ne le suit pas à l'aveugle",
                            member.name)
                self._mark_lost(member)
                continue
            layout = getattr(member.game, "layout", None) or frame.layout
            target = layout.point(*reference)
            clicked = self.roamer.traveler.click_sun(target, spread, member.game) if self.roamer is not None                 else member.game.click(target, spread)  # autres joueurs cachés (touche E) pendant le clic
            if clicked:
                self._dispatched[member.name] = (member.game.look(park=False), time.monotonic())
                names.append(member.name)
        if names:
            log.info("Toute l'équipe vers le %s : %s cliquent aussi sur le soleil en %s", side, ", ".join(names), cell)

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
            if same_map(frame, here):
                continue
            passed.append(name)
            log.warning("%s est passé sans le principal (resté sur sa carte) : il le rejoindra par son trajet", name)
            self._lost.add(name)
            label = read_map_label(frame)
            if label is not None and label.readable and label.coords is not None:
                self._seen_at[name] = label.coords
            elif self._dispatch_to is not None:
                self._seen_at[name] = self._dispatch_to
        if passed and self.roamer is not None and self._dispatch_side is not None:
            self._leader_left_behind(passed)

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
        """Les suiveurs prennent le soleil que le principal vient de prendre : déjà partis avec lui (_dispatch), ou
        clic dans chaque fenêtre maintenant ; puis vérification de l'arrivée de chacun, fenêtre après fenêtre."""
        dispatched, self._dispatched = self._dispatched, {}
        walking = []
        for member in self.followers:
            if member.name in dispatched:
                walking.append((member, *dispatched[member.name]))
                continue
            frame = member.game.look(park=False)
            if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                self._round_due = True  # combat (agression) : pris en charge au tour des fenêtres
                continue
            label = read_map_label(frame)
            if label is not None and label.readable:
                if crossing.after.readable and label.same_map(crossing.after):
                    continue  # déjà sur la carte d'arrivée
                if crossing.before.readable and not label.same_map(crossing.before):
                    log.warning("%s n'est pas sur la carte que quitte le principal (il est sur %s) : il ne le suit pas",
                                member.name, label.describe())
                    if label.coords is not None:
                        self._seen_at[member.name] = label.coords
                    continue
            self._click_sun(member, frame, crossing, 0)
            walking.append((member, frame, time.monotonic()))
        self._wait_followers(walking, crossing)

    def _click_sun(self, member: Member, frame: Frame, crossing: Crossing, spot: int) -> None:
        point, spread = _click_spot(frame, crossing.cell, spot % 4)  # centre, bas, pointes gauche et droite, encore
        log.info("Je suis le principal vers le %s : clic sur le soleil en %s", crossing.side, crossing.cell)
        if self.roamer is not None:
            self.roamer.traveler.click_sun(point, spread, member.game)  # autres joueurs cachés (touche E)
        else:
            member.game.click(point, spread)

    def _wait_followers(self, walking: list[tuple[Member, Frame, float]], crossing: Crossing) -> None:
        """Arrivée des suiveurs, regardés à tour de rôle (ils marchent en même temps). Comme pour le principal
        (brain/travel.py) : arrivé quand tout le décor a changé et que la carte lue est celle du principal (ou, nom
        illisible, que le décor ne bouge plus). Encore sur la carte de départ au bout de RECLICK_EVERY s : nouveau clic
        sur le soleil, à un autre endroit de la case (un personnage passait dessus : 03/10, recliqué 12 s plus tard
        seulement). Un menu ouvert à la place (quelqu'un sur le soleil) est fermé et la case recliquée ailleurs."""
        # [membre, décor de départ, heure du clic, endroit de la case, décor précédent, vue du mouvement, dernier mouvement]
        pending = {member.name: [member, _scene(before), clicked, 0, None, None, clicked]
                   for member, before, clicked in walking}
        leader_view: Frame | None = None  # carte où est arrivé le principal, pour juger les noms illisibles au décor
        deadline = time.monotonic() + self.travel_settings.walk_timeout
        while pending and time.monotonic() < deadline:
            for name, state in list(pending.items()):
                member, departure, clicked, spot, previous = state[:5]
                frame = member.game.look(park=False)
                if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                    log.info("Combat pendant le trajet de %s", name)
                    self._round_due = True
                    del pending[name]
                    continue
                if find_context_menu(frame) is not None:
                    escape_menu(member.game)  # Échap seulement ; Menu principal ouvert par erreur : refermé
                    state[3] = spot = spot + 1
                    self._click_sun(member, frame, crossing, spot)
                    state[2], state[4] = time.monotonic(), None
                    continue
                view = _scene(frame)
                changed = _gap(view, departure) > SCENE_CHANGED
                stable = previous is not None and _gap(view, previous) < SCENE_STABLE
                state[4] = view
                motion = _motion_view(frame)
                if state[5] is not None and _motion(motion, state[5]) >= self.travel_settings.motion_cells:
                    state[6] = time.monotonic()
                state[5] = motion
                if not changed:
                    # Reclic seulement s'il ne bouge pas : 11/10, Imthebest, plus lent, arrivait en 5,-16 quand le
                    # reclic est parti ; tombé sur la nouvelle carte, il l'a fait descendre en 5,-15.
                    now = time.monotonic()
                    if now - clicked >= RECLICK_EVERY and now - state[6] >= FOLLOWER_STILL:
                        state[3] = spot = spot + 1
                        self._click_sun(member, frame, crossing, spot)
                        state[2] = time.monotonic()
                    continue  # en chemin
                label = read_map_label(frame)
                if label is not None and label.readable and crossing.after.readable:
                    if label.same_map(crossing.after):
                        log.info("%s a suivi le principal (%s)", name, label.describe())
                        self._lost.discard(name)
                        del pending[name]
                    elif stable:
                        log.warning("%s est arrivé sur %s, pas sur la carte du principal", name, label.describe())
                        self._lost.add(name)
                        if label.coords is not None:
                            self._seen_at[name] = label.coords
                        del pending[name]
                elif stable:
                    # Nom illisible (27 % des arrivées de suiveurs n'étaient pas vérifiées, bilan du 12/10) : même décor
                    # que le principal, revu une fois sur des captures fraîches avant de le déclarer perdu.
                    if leader_view is None:
                        leader_view = self.leader.game.look(park=False)
                    if same_map(frame, leader_view) or same_map(member.game.look(park=False),
                                                                  self.leader.game.look(park=False)):
                        log.info("%s a suivi le principal (nom de carte illisible, même décor)", name)
                        self._lost.discard(name)
                    else:
                        log.warning("%s a changé de carte, mais pas le même décor que le principal : il le rejoindra "
                                    "par son trajet", name)
                        self._lost.add(name)  # position inconnue : pas devinée (le regroupement le dira)
                    del pending[name]
            if pending:
                self.leader.game.sleep(0.2)
        for name, (member, *_rest) in pending.items():
            path = member.game.recorder.save(member.game.look().image, "suiveur_bloque", lossless=True)
            log.warning("%s n'a pas suivi le principal (capture : %s) : il le rejoindra par son propre trajet", name,
                        path)
            self._lost.add(name)
            start = crossing.before.coords if crossing.before.readable else None
            if start or self._departure:
                self._seen_at[name] = start or self._departure  # resté sur la carte de départ

    def _mark_lost(self, member: Member, frame: Frame | None = None) -> None:
        """Suiveur pas sur la carte du principal : perdu ; sa carte est notée si elle se lit."""
        self._lost.add(member.name)
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
            if not same_map(frame, here):
                log.warning("%s n'est pas sur la carte du principal au départ : il le rejoindra d'abord", member.name)
                self._mark_lost(member, frame)

    def _regroup(self) -> bool:
        """Suiveurs perdus (pas sur la carte du principal) : chacun fait son propre trajet jusqu'à elle (mode aller,
        une carte par appel) ; le principal ne repart pas avant. True quand toute l'équipe est réunie.

        11/10, cité d'Astrub : le principal continuait sans eux, l'équipe s'est dispersée sur quatre cartes. Un
        suiveur qui ne peut pas rejoindre (position inconnue, aucun chemin) arrête le bot : mieux vaut s'arrêter que
        de continuer dispersés."""
        if self.roamer is None:
            return True
        if not self._together_checked:
            self._check_together()
        if not self._lost:
            return True
        target = self.roamer.traveler.coords
        if target is None:
            return True  # carte du principal inconnue : rien pour guider les autres
        for name in sorted(self._lost):
            member = self.by_name[name]
            journey = self._rejoin.get(name)
            if journey is None or journey.goal != target:
                start = self._seen_at.get(name)
                if start is None:
                    log.warning("%s n'est pas sur la carte du principal et je ne sais pas où il est (nom de carte "
                                "illisible) : je m'arrête (amène-le sur la carte du principal, puis relance)", name)
                    self.finished = True
                    return False
                settings = replace(self.travel_settings, mode="aller", destination=target, start=start)
                traveler = Traveler(member.game, settings, self.roamer.traveler.world, self.roamer.traveler.atlas)
                traveler.learns = False  # 11/10 : ses sorties « bloquées » (pas sur la carte crue) ont tout faussé
                journey = self._rejoin[name] = Journey(traveler, target)
                log.info("%s rejoint le principal en %s,%s (depuis %s)", name, *target,
                         f"{start[0]},{start[1]}" if start else "une carte inconnue")
            journey.step()
            if not journey.done:
                continue
            del self._rejoin[name]
            arrived = journey.traveler.coords == target
            if arrived and not same_map(member.game.look(park=False), self.leader.game.look(park=False)):
                log.warning("%s se croit en %s,%s mais ne voit pas le même décor que le principal", name, *target)
                arrived = False
            if arrived:
                log.info("%s a rejoint le principal en %s,%s", name, *target)
                self._lost.discard(name)
                self._seen_at.pop(name, None)
            else:
                log.warning("%s ne peut pas rejoindre le principal en %s,%s : je m'arrête (amène-le sur sa carte, "
                            "puis relance)", name, *target)
                self.finished = True
                return False
        return not self._lost

    # Combat

    def _fight(self, host: Member) -> None:
        """Un combat de l'équipe, du placement jusqu'à la fin ; les fenêtres de résultat sont fermées au tour des
        fenêtres qui suit."""
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
        if read_scene(host.game.look(park=False)).phase is Phase.PLACEMENT:
            fighters = self._placement(host)
        else:
            fighters = [host] + [m for m in self.members if m is not host
                                 and read_scene(m.game.look(park=False)).phase is not Phase.OUT_OF_COMBAT]
            log.info("Combat déjà commencé : %s", ", ".join(m.name for m in fighters))
        end = self._turns(fighters) if fighters else None
        dead = [m.name for m in fighters if _died(m)]
        if dead:
            # 17/10 : Koalaloupour mort seul à Incarnam, réapparu à Astrub, le bot a continué comme si de rien n'était.
            log.warning("%s mort(s) pendant ce combat (« Tu as perdu ... points d'énergie » dans le chat) : je "
                        "m'arrête après ce combat", ", ".join(dead))
            self._stop_reason = f"{', '.join(dead)} mort(s) au combat"
        self._fight_over()
        self.fights += 1
        write_fight_report(getattr(self.leader.game.recorder, "dir", None), self.fights,
                           [(m.name, m.ai.fight_report()) for m in fighters])
        if self.max_fights and self.fights >= self.max_fights:
            self._stop_reason = f"{self.fights} combat(s), le maximum demandé"
        if end is not None:
            self._close_results(*end, fighters)

    def _placement(self, host: Member) -> list[Member]:
        """Les autres rejoignent le combat de celui qui l'a lancé, puis Prêt dans chaque fenêtre, la sienne en dernier.
        Renvoie les personnages du combat. Sans placement (team.place, défaut) : personne ne bouge, chacun est repéré à
        son premier tour (portée d'un sort : CombatAI._locate_by_range). Avec : toute l'équipe se place d'un coup,
        selon l'ordre de jeu (_seat_team, team/placement.py), une fois tout le monde arrivé."""
        log.info("Placement : combat de %s", host.name)
        hint = recent_click(host)  # près du groupe attaqué : l'épée est là
        others = [m for m in self.members if m is not host]
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
        if self.auto_ready:
            for member in joined + [host]:
                if member.name not in ready:
                    self._ready(member)
        return [host] + joined if self._wait_combat(host) else []

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
        team = self._team_colour(placement, len(fighters))
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
        """Un seul appui : un second annulerait le premier."""
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
        member.game.sleep(0.1)

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
        started = False  # un tour lu dans la timeline : avant, un hors combat peut être l'écran d'après Prêt
        signals = self.signals
        if signals is not None:
            signals.new_fight()  # notifications d'avant les tours (hors combat : invitations, échanges) ignorées
        while True:
            if self.deadline is not None and time.monotonic() >= self.deadline + OVERTIME:
                log.warning("Durée de la session dépassée de %.0f min en plein combat : j'arrête le bot, le combat "
                            "continue sans lui", OVERTIME / 60)
                self._stop_reason = "durée de la session dépassée en plein combat"
                self.finished = True
                return None
            frame = watcher.game.look(park=False)
            scene = read_scene(frame)
            self._report(watcher, frame, scene)
            if signals is not None:
                signals.enabled_for_polling = scene.phase is Phase.COMBAT  # signaux : en attente d'un tour seulement
            if scene.phase is Phase.OUT_OF_COMBAT:
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

    def _player(self, watcher: Member, frame: Frame, scene: Scene, fighters: list[Member]) -> Member | None:
        """Le personnage dont c'est le tour ; None pendant le tour d'un monstre ou d'une invocation alliée."""
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
        self._early = None
        self._just_played = (member.name, time.monotonic())
        if getattr(member.ai, "turns_not_passed", 0) >= STUCK_TURNS:
            member.ai.turns_not_passed = 0
            self._stuck[member.name] = time.monotonic() + STUCK_PAUSE
            path = member.game.recorder.save(member.game.look(park=False).image, "tour_qui_ne_passe_pas", lossless=True)
            log.warning("%s : %d tours d'affilée sans que la main passe, ce n'est sans doute pas son tour : je ne le fais "
                        "plus jouer pendant %.0f s (capture : %s)", member.name, STUCK_TURNS, STUCK_PAUSE, path)
        tracked = getattr(member.ai, "_me_cell", None)  # case où l'IA m'a vu en dernier (fin du tour)
        if tracked is not None:
            self._cells[member.name] = tracked

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
        if len(left) == 1 and distance(left[0].cell, tracked) <= 2:
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
        """Action refaite dans chaque fenêtre : la fenêtre de résultat a son bouton « Fermer » au même endroit partout
        (même disposition) ; vu une fois chez `watcher`, il est cliqué dans chaque fenêtre du combat sans capture
        (0,3 s par fenêtre au lieu de 1,2). Le tour des fenêtres qui suit vérifie, et ferme ce qui reste (niveau
        supérieur, qui n'arrive pas à tout le monde). Les suiveurs d'abord : le principal reste devant."""
        known = find_known_popup(frame, watcher.popups.specs)
        if known is None or known.close_point is None:
            # Vue en train de s'ouvrir, sans son bouton encore (03/10 : 2 combats sur 14, fermée ensuite fenêtre par
            # fenêtre, ~1,5 s de plus) : un second regard.
            watcher.game.sleep(RESULT_LAG)
            frame = watcher.game.look(park=False)
            known = find_known_popup(frame, watcher.popups.specs)
        else:
            watcher.game.sleep(RESULT_LAG)
        if known is None or known.spec.name != RESULT or known.close_point is None:
            return  # niveau supérieur par-dessus, chez celui-ci : le tour des fenêtres s'en charge
        point = to_reference(frame.layout, known.close_point)
        log.info("Fenêtre « %s » : je la ferme dans les %d fenêtres du combat", RESULT, len(fighters))
        for member in sorted(fighters, key=lambda m: m is self.leader):
            layout = getattr(member.game, "layout", None) or frame.layout
            member.game.click(layout.point(*point), known.close_spread)
            member.popups.closed_elsewhere(RESULT)

    def _fight_over(self) -> None:
        log.info("Combat terminé")
        self._combat_ended_at = time.monotonic()
        self._last_team = self.my_team or self._last_team
        if self.learn_team:
            self.my_team = None  # la couleur de l'équipe peut changer d'un combat à l'autre
        self._round_due = True  # fenêtres de résultat, de niveau supérieur et vie : dans chaque fenêtre
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
