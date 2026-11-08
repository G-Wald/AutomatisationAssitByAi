"""Changer de carte par les soleils du bord, et savoir où l'on est.

Repérage : les coordonnées écrites en haut à gauche (perception/maplabel.py). La région n'est pas lue : c'est un
réglage (travel.region : Incarnam, ou plus tard Monde des Douze), car le bot reste dans l'une ou dans l'autre et
Incarnam a ses propres coordonnées (Incarnam 0,0 n'est pas la carte 0,0 du Monde des Douze).
Quand un chiffre n'est pas encore appris, les coordonnées sont déduites de la carte précédente et du côté
emprunté (droite : x + 1, bas : y + 1...), et la capture est gardée pour apprendre ce chiffre.

Sortie : d'abord les soleils visibles sans rien faire ; si celui du côté voulu n'est pas vu, un appui sur A les
entoure de blanc (perception/exits.py). Un clic sur le soleil fait marcher le personnage jusqu'à la carte
voisine ; on attend que le nom de la carte change.

Depuis le 25/10, sur les cartes que la référence complétée décrit (data/references) : itinéraire complet calculé avant
de partir (brain/itinerary.py), soleil prévu cliqué sans recherche à l'écran, passage confirmé par le décor, position à
l'estime ; la lecture du nom ne fait que la contrôler (compatible, même tronquée : confirmée ; franchement contraire :
« /p %pos% »). Zaap quand aucun chemin à pied n'existe (brain/zaap.py).

Mémoire (data/carte.json) : pour chaque carte, sa zone, son dernier passage, la case de ses soleils et ce que
donne chaque sortie (carte voisine, « bloquée »), d'une session à l'autre ; selon le côté d'arrivée, les zones de
groupes hors d'atteinte (brain/engage.py). Références (config/monde, voir brain/atlas.py) : cases des soleils, carte
où mène chaque côté et zone de chaque carte, avant même d'y être passé.
Ordre de confiance : ce que le bot a vu, puis les références, puis la recherche à l'écran.

Cartes et zones interdites (travel.avoid_maps, travel.avoid_zones) : jamais prises, demi-tour si on y arrive
(monstres agressifs par exemple : le bot ne gère pas les agressions).

Quatre façons de circuler (settings.yaml, travel.mode, ou options de la ligne de commande) :
- test : s'éloigner de 2 cartes puis revenir sur la carte de départ, sans combat ;
- explore : visiter toutes les cartes de la région de départ, sans combat ;
- zone : combats, et quand la carte n'a plus de groupe attaquable, carte voisine de la même zone (Forêt...) ;
- aller : jusqu'à la carte d'arrivée (--aller x,y, ou travel.destination) par le plus court chemin, sans combat.
"""

import contextlib
import json
import logging
import math
import re
import time
import heapq
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np

from bot.brain.atlas import Atlas, MapInfo
from bot.brain.game import Game
from bot.brain.harvest import remember_key
from bot.brain.itinerary import Leg, Planner, complete, reading_fits
from bot.brain.popups import close_any_menu, close_context_menu
from bot.brain.position import PositionAsker
from bot.brain.recovery import mark_deaf, mark_heard, report_episode, unlock_click
from bot.debug.draw import annotate
from bot.perception.exits import DELTA, OPPOSITE, SAME_SUN, SIDES, Exit, find_marked_exits, find_suns
from bot.perception.grid import distance
from bot.perception.frame import Frame
from bot.perception.maplabel import MapLabel, learn_digits, read_map_label, unreadable_label
from bot.perception.popups import find_context_menu, find_known_popup, find_unknown_window, load_popup_specs
from bot.state.scene import Phase, read_scene

log = logging.getLogger(__name__)

BLOCKED = "bloquée"
MENU = "menu"  # le clic a ouvert le menu d'un personnage ou d'un monstre posé sur le soleil
START = "départ"  # côté d'arrivée inconnu : carte du début du trajet (Traveler.place)
# Toujours sur la carte de départ, décor inchangé : le soleil est recliqué toutes les RECLICK_EVERY s (un personnage passait
# dessus, le clic l'a visé lui : 03/10, 12 s perdues). En marche, recliquer la même case ne change rien.
CLICK_TAKEN_WAIT = 1.2  # s : un clic sur un soleil sans déplacement ni carte annoncés par le jeu est perdu (31/10)
RECLICK_EVERY = 3.0
A_REREAD = 0.4  # touche A sans aucun contour : relue après ce délai, avant le clic de déblocage (20/10, S14)
# Écart moyen (niveaux de gris, 0-255) entre deux vues réduites de la carte. Mesuré le 28/09 : 0,1 à 0,3 sur une
# même carte pendant que le personnage marche, 28 à 37 entre deux cartes voisines.
SCENE_CHANGED, SCENE_STABLE = 12.0, 3.0
# Soleil inatteignable vu en quelques secondes (11/10, l'utilisateur : 60 s perdues à chaque mur des remparts) : le
# personnage ne bouge plus et n'a pas changé de carte. Mouvement : vue réduite de la carte, autres joueurs cachés
# (touche E tenue) ; une case qui change de plus de MOTION_LEVEL niveaux de gris entre deux captures (0,4 s) bouge.
STILL = "immobile"
# Autre décor, stable, sans fenêtre par-dessus, mais nom de carte lu comme au départ : arrivé au bout de ce nombre de
# regards (0,4 s chacun) ; le décor fait foi (25/10).
DECOR_OVER_NAME = 3
# Écoute réseau : nouvelle carte annoncée (GDM, GDK) ; le décor de la capture suit d'un instant, attendu au plus
# NET_SCENE_WAIT s avant de garder la capture d'arrivée (26/10 : GDM 0,5 s après le clic, carte chargée aussitôt).
NET_SCENE_WAIT = 1.5
MOTION_SIZE, MOTION_LEVEL = (160, 90), 12.0
# Nom de carte illisible (20 % des arrivées dans la cité, bilan du 12/10) : la position déduite du chemin reste sûre
# SURE_UNREAD cartes au plus après une carte lue, sans saut. Avant, rien n'était appris sur ces cartes : la sortie du
# haut de 4,-22 (aucun soleil) était redécouverte à chaque trajet.
SURE_UNREAD = 2
WALKED_POLLS = 3  # captures (0,4 s) où il bougeait : il a marché (pas seulement l'affichage des autres joueurs)
CANDIDATE_TRIES = 2  # clics sur un même soleil (le second au bas de la case) avant de passer au soleil suivant
MENU_RETRIES = 3  # menus de personnage fermés en s'écartant, sans compter d'essai sur le soleil
# Endroits de la case du soleil visés après chaque menu (_click_spot) : le centre (souvent soi-même sur la case,
# écarté par la fermeture), puis les pointes gauche et droite. 07/11 (Pesopepe, 12,-27 et 12,-28) : un coéquipier déjà
# parti restait affiché sur le soleil dans sa fenêtre ; centre et bas de la case ouvraient son menu, la pointe est passée.
MENU_SPOTS = (0, 2, 3)
MAX_CANDIDATES = 3  # soleils essayés d'un même côté (carte coupée par un rempart : un soleil de chaque côté du mur)
MODES = ("", "test", "explore", "zone", "aller")
# Mode aller (03/10) : changements de carte ratés d'affilée (sortie introuvable, clic sans effet, agression) avant
# d'abandonner, comme l'exploration ; et itinéraires recalculés au plus (chacun apprend une liaison ou une sortie
# bloquée : au-delà, les liaisons lues se contredisent, mieux vaut s'arrêter que tourner en rond).
JOURNEY_FAILURES = 5
JOURNEY_REROUTES = 30
WORLDS = {"incarnam": "Incarnam", "douze": "Monde des Douze"}  # choix au lancement (--monde)
PROJECT_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TravelSettings:
    mode: str = ""  # "" : rester sur la carte ; test, explore, zone, aller (voir en tête du module)
    region: str = "Incarnam"  # où le bot circule (références config/monde/<région>.json)
    show_exits_key: str = "a"
    # Touche tenue pendant le clic sur un soleil : les autres joueurs disparaissent (découverte de l'utilisateur, 11/10 ;
    # dans la cité d'Astrub, des joueurs debout sur les soleils prenaient le clic). "" : pas de touche.
    hide_players_key: str = "e"
    memory: str = "data/carte.json"
    walk_timeout: float = 15.0  # clic sur le soleil -> nouvelle carte affichée
    # Soleil inatteignable (STILL) : personnage immobile pendant still_for s, après still_after s laissées au départ du
    # clic ; 0 : attente complète (walk_timeout). motion_cells : cases de la vue réduite qui changent s'il marche.
    still_after: float = 1.0
    still_for: float = 2.0
    motion_cells: int = 6
    settle: float = 1.0  # après l'arrivée, avant de lire la carte
    empty_searches: int = 2  # mode zone : recherches sans groupe attaquable avant de changer de carte
    test_steps: int = 2
    start: tuple[int, int] | None = None  # coordonnées de la carte de départ, si ses chiffres ne sont pas encore appris
    # Cartes et zones où ne jamais aller (monstres agressifs...), avec leur région (None : dans toutes les régions).
    avoid_maps: tuple[tuple[str | None, tuple[int, int]], ...] = ()  # « Incarnam 3,6 » ou « 3,6 »
    avoid_zones: tuple[tuple[str | None, str], ...] = ()  # « Incarnam (Cimetière) » ou « Cimetière »
    destination: tuple[int, int] | None = None  # mode aller : coordonnées de la carte d'arrivée
    waypoints: tuple[tuple[int, int], ...] = ()  # mode aller : cartes à rejoindre l'une après l'autre (--etapes)
    # Zaap quand aucun chemin à pied n'existe (25/10 : le Berceau de Taïnéla n'a pas de sortie à pied) ; coûte des
    # kamas à chaque personnage. False : le trajet s'arrête avec un message à la place.
    zaap: bool = True

    @classmethod
    def from_settings(cls, settings: dict, mode: str | None = None, start: tuple[int, int] | None = None,
                      region: str | None = None) -> "TravelSettings":
        travel = settings.get("travel") or {}
        chosen = (travel.get("mode") or "") if mode is None else mode
        if chosen not in MODES:
            raise ValueError(f"travel.mode doit valoir test, explore, zone, aller ou rester vide (pas {chosen!r})")
        destination = _parse_coords(travel.get("destination"))
        if chosen == "aller" and destination is None:
            raise ValueError("travel.mode aller : carte d'arrivée manquante (--aller x,y, ou travel.destination)")
        return cls(
            mode=chosen,
            region=region or travel.get("region") or cls.region,
            show_exits_key=travel.get("show_exits_key") or cls.show_exits_key,
            hide_players_key=str(travel.get("hide_players_key", cls.hide_players_key) or ""),
            memory=travel.get("memory") or cls.memory,
            walk_timeout=float(travel.get("walk_timeout", cls.walk_timeout)),
            still_after=float(travel.get("still_after", cls.still_after)),
            still_for=float(travel.get("still_for", cls.still_for)),
            motion_cells=int(travel.get("motion_cells", cls.motion_cells)),
            settle=float(travel.get("settle", cls.settle)),
            empty_searches=int(travel.get("empty_searches", cls.empty_searches)),
            test_steps=int(travel.get("test_steps", cls.test_steps)),
            start=start,
            avoid_maps=tuple(_parse_map(text) for text in travel.get("avoid_maps") or ()),
            avoid_zones=tuple(_parse_zone(text) for text in travel.get("avoid_zones") or ()),
            destination=destination,
            waypoints=tuple(tuple(w) for w in travel.get("etapes") or ()),
            zaap=bool(travel.get("zaap", cls.zaap)),
        )


def _parse_coords(value) -> tuple[int, int] | None:
    """« 5,-18 », [5, -18] ou (5, -18) -> (5, -18) ; vide -> None (travel.destination, ou --aller)."""
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    found = re.fullmatch(r"\s*(-?\d+)\s*,\s*(-?\d+)\s*", str(value))
    if not found:
        raise ValueError(f"travel.destination : « x,y » attendu (pas {value!r})")
    return int(found[1]), int(found[2])


def _parse_map(text) -> tuple[str | None, tuple[int, int]]:
    """« Incarnam 3,6 » -> ("Incarnam", (3, 6)) ; « 3,6 » -> (None, (3, 6))."""
    found = re.fullmatch(r"\s*(?:(.*\S)\s+)?(-?\d+)\s*,\s*(-?\d+)\s*", str(text))
    if not found:
        raise ValueError(f"travel.avoid_maps : « Région x,y » ou « x,y » attendu (pas {text!r})")
    return found[1], (int(found[2]), int(found[3]))


def _parse_zone(text) -> tuple[str | None, str]:
    """« Incarnam (Cimetière) » -> ("Incarnam", "Cimetière") ; « Cimetière » -> (None, "Cimetière")."""
    found = re.fullmatch(r"\s*(.*\S)\s*\((.+)\)\s*", str(text))
    return (found[1], found[2].strip()) if found else (None, str(text).strip())


class World:
    """Cartes connues, repérées par « région x,y », enregistrées dans un fichier JSON (None : en mémoire seulement)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.maps: dict[str, dict] = {}
        self._jumps_seen: set[tuple[str, str, str]] = set()  # sauts entre cartes pas voisines vus une fois (link)
        if path is not None and path.is_file():
            try:
                self.maps = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                log.warning("Mémoire des cartes illisible (%s) : je repars de zéro", error)

    @staticmethod
    def key(region: str | None, coords: tuple[int, int] | None) -> str | None:
        if coords is None:
            return None
        return f"{region or 'région inconnue'} {coords[0]},{coords[1]}"

    def visit(self, key: str, label: MapLabel, exits: list[Exit]) -> None:
        entry = self.maps.setdefault(key, {"sorties": {}})
        if label.zone_name or "zone" not in entry:
            entry["zone"] = label.zone_name
        entry["vue"] = round(time.time())
        for exit_ in exits:
            entry["sorties"].setdefault(exit_.side, None)  # destination inconnue tant qu'on ne l'a pas prise
        self.remember_suns(key, exits)

    def suns(self, key: str | None) -> dict[str, tuple[int, int]]:
        """Case du soleil de chaque côté, notée lors d'un passage précédent."""
        return {side: tuple(cell) for side, cell in (self.maps.get(key) or {}).get("soleils", {}).items()} if key else {}

    def remember_suns(self, key: str | None, exits: list[Exit]) -> None:
        if not key:
            return
        suns = self.maps.setdefault(key, {"sorties": {}}).setdefault("soleils", {})
        for exit_ in exits:
            if not exit_.remembered:
                suns[exit_.side] = list(exit_.cell)
        self.save()

    def confirm_sun(self, key: str | None, side: str, cell: tuple[int, int], entry: str | None = None) -> None:
        """Ce soleil a mené à la carte voisine : on le garde comme vu ; `entry` : aussi pour qui arrive par ce côté."""
        if key:
            info = self.maps.setdefault(key, {"sorties": {}})
            info.setdefault("soleils", {})[side] = list(cell)
            if entry:
                info.setdefault("soleils_depuis", {}).setdefault(entry, {})[side] = list(cell)
                blocked = (info.get("bloquees_depuis") or {}).get(entry) or []
                if side in blocked:
                    blocked.remove(side)
            self.save()

    def suns_from(self, key: str | None, entry: str | None) -> dict[str, tuple[int, int]]:
        """Soleil de chaque côté qui a mené à la carte voisine, pour qui est arrivé sur la carte par le côté `entry`."""
        if not key or not entry:
            return {}
        found = ((self.maps.get(key) or {}).get("soleils_depuis") or {}).get(entry) or {}
        return {side: tuple(cell) for side, cell in found.items()}

    def several(self, key: str | None, side: str) -> bool:
        """Plusieurs soleils vus de ce côté (carte coupée par un rempart) : la case gardée sans le côté d'arrivée ne dit
        pas lequel est de notre côté du mur."""
        return bool(key) and side in ((self.maps.get(key) or {}).get("plusieurs") or [])

    def note_several(self, key: str | None, sides: list[str]) -> None:
        if not key or not sides:
            return
        info = self.maps.setdefault(key, {"sorties": {}})
        known = set(info.get("plusieurs") or [])
        if not set(sides) <= known:
            info["plusieurs"] = sorted(known | set(sides))
            self.save()

    def blocked_from(self, key: str | None, entry: str | None) -> set[str]:
        """Sorties inatteignables pour qui arrive sur la carte par le côté `entry` (de l'autre côté d'un rempart)."""
        if not key or not entry:
            return set()
        return set(((self.maps.get(key) or {}).get("bloquees_depuis") or {}).get(entry) or [])

    def block_from(self, key: str | None, entry: str, side: str) -> None:
        if not key:
            return
        sides = self.maps.setdefault(key, {"sorties": {}}).setdefault("bloquees_depuis", {}).setdefault(entry, [])
        if side not in sides:
            sides.append(side)
            sides.sort()
            self.save()

    def unblock(self, key: str | None, side: str) -> bool:
        """Ce soleil marche (un suiveur l'a pris) : plus noté bloqué, ni pour tous ni selon le côté d'arrivée."""
        info = self.maps.get(key) if key else None
        if not info:
            return False
        changed = False
        if (info.get("sorties") or {}).get(side) == BLOCKED:
            del info["sorties"][side]
            changed = True
        for sides in (info.get("bloquees_depuis") or {}).values():
            if side in sides:
                sides.remove(side)
                changed = True
        if changed:
            self.save()
        return changed

    def unreachable_from(self, key: str | None, entry: str | None) -> list[dict]:
        """Zones de groupes hors d'atteinte (brain/engage.py) pour qui arrive sur la carte par le côté `entry` (START :
        début du trajet), en px de référence."""
        if not key or not entry:
            return []
        zones = ((self.maps.get(key) or {}).get("hors_atteinte_depuis") or {}).get(entry) or []
        return [dict(zone) for zone in zones if isinstance(zone, dict)]

    def set_unreachable_from(self, key: str | None, entry: str | None, zones: list[dict]) -> None:
        if not key or not entry:
            return
        info = self.maps.get(key)
        if not zones and not (info or {}).get("hors_atteinte_depuis", {}).get(entry):
            return  # rien de noté, rien à retirer : la carte n'est pas créée pour rien
        by_entry = self.maps.setdefault(key, {"sorties": {}}).setdefault("hors_atteinte_depuis", {})
        if zones:
            by_entry[entry] = zones
        else:
            del by_entry[entry]
            if not by_entry:
                del self.maps[key]["hors_atteinte_depuis"]
        self.save()

    def forget_sun(self, key: str | None, side: str) -> None:
        if key and side in (self.maps.get(key) or {}).get("soleils", {}):
            del self.maps[key]["soleils"][side]
            self.save()

    def destination(self, key: str | None, side: str) -> str | None:
        return (self.maps.get(key) or {}).get("sorties", {}).get(side) if key else None

    def link(self, key: str | None, side: str, destination: str) -> None:
        if not key:
            return
        start, end = _coords_of(key), _coords_of(destination) if destination != BLOCKED else None
        if start is not None and end is not None and side in DELTA                 and (start[0] + DELTA[side][0], start[1] + DELTA[side][1]) != end                 and key.rsplit(" ", 1)[0] == destination.rsplit(" ", 1)[0]:
            # Cartes pas voisines : lecture fausse (11/10 : « 1, -23 » lu « -1,-23 », liaison « -1,-23 droite -> 2,-23 »)
            # ou vrai saut du jeu (11/10 : en -2,-18, monter mène en -2,-20, lu deux fois de suite ; refusé, le bot a fait
            # des allers-retours). Enregistrée seulement quand le même saut est vu une seconde fois.
            jump = (key, side, destination)
            if jump not in self._jumps_seen:
                self._jumps_seen.add(jump)
                log.warning("Liaison %s côté %s -> %s pas encore enregistrée : cartes pas voisines (lecture fausse ou "
                            "saut du jeu ?), il faudra la revoir", key, side, destination)
                return
            log.info("Saut du jeu confirmé : %s côté %s -> %s", key, side, destination)
        self.maps.setdefault(key, {"sorties": {}})["sorties"][side] = destination
        self.save()

    def last_seen(self, key: str | None) -> float:
        return float((self.maps.get(key) or {}).get("vue", 0)) if key else 0.0

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.maps, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        except OSError as error:
            log.warning("Mémoire des cartes non enregistrée : %s", error)


class Traveler:
    """Lit la carte, trouve ses sorties, passe sur la carte voisine."""

    def __init__(self, game: Game, settings: TravelSettings, world: World, atlas: Atlas | None = None) -> None:
        self.game = game
        self.settings = settings
        self.world = world
        self.atlas = atlas or Atlas()
        self.coords: tuple[int, int] | None = settings.start  # lues, sinon déduites (départ donné, chemin parcouru)
        self.asker = PositionAsker(game, _character(game))  # « /p %pos% » quand la position reste inconnue
        self._keys_noted: set[int] = set()  # cartes dont la clé est retenue (told_coords)
        self.region = settings.region  # réglage : le nom de la région n'est pas lu à l'écran
        self.jumped = False  # coordonnées lues loin de celles attendues : mort, téléportation...
        # Mode équipe (team/loop.py) : appelée après chaque passage réussi avec (côté, case du soleil, carte de départ,
        # carte d'arrivée), pour que les autres personnages prennent le même soleil.
        self.on_crossed = None
        # Mode équipe : appelée juste après le clic du principal sur un soleil avec (côté, case, capture de départ, endroit
        # de la case) : les autres cliquent aussitôt le même, toute l'équipe marche en même temps.
        self.on_click = None
        self._unknown_saved: set[str] = set()
        self._a_saved = 0
        self.a_unheard = False  # dernière lecture des sorties : aucun contour après A, même après déblocage (20/10)
        # Faux pour le trajet d'un suiveur qui rejoint le principal (team/loop.py) : rien d'écrit dans la mémoire
        # partagée (sa position est souvent déduite du chemin, pas lue).
        self.learns = True
        self._last_label: MapLabel | None = None
        # Sorties bloquées notées pour ce trajet seulement (carte pas sûre : pas écrites dans data/carte.json), pour
        # que l'itinéraire soit quand même recalculé (Journey._links).
        self.blocked_here: set[tuple[str | None, str]] = set()
        self.aim_center: tuple[int, int] | None = None  # losange du soleil visé, vu avec la touche A (équipe : _dispatch)
        # Côté par lequel on est arrivé sur cette carte, et case d'arrivée estimée (en face du soleil pris) ; None au
        # début du trajet. Sur une carte coupée par un rempart, il dit de quel côté du mur on est.
        self.entry: str | None = None
        self.entry_cell: tuple[int, int] | None = None
        self.arrival_cell: tuple[int, int] | None = None  # case d'arrivée d'après la référence complétée (_arrived)
        self.next_side: str | None = None  # côté à prendre sur la carte suivante (Journey : choix du soleil)
        self.candidates: dict[str, list[Exit]] = {}  # soleils vus de chaque côté à la dernière recherche à l'écran
        self._hiding = False  # touche E déjà tenue : un clic sur un soleil ne la réappuie pas
        self._walked = False  # au dernier STILL : il avait marché avant de s'arrêter
        self._unread_steps = 0  # cartes d'affilée au nom illisible depuis la dernière carte lue (ou la position donnée)
        # Position à l'estime (25/10) : chaque passage est confirmé par le décor ; la lecture du nom de la carte ne fait
        # que la contrôler. `doubt` : une lecture contraire n'a pas pu être vérifiée par « /p » (position pas sûre).
        self.doubt = False
        self._checked: dict[tuple, tuple[int, int] | None] = {}  # (carte attendue, lecture) -> position retenue
        self.interrupted = False  # dernier passage arrêté par un combat (ou une autre fenêtre), pas raté
        # Soleils prévus par l'itinéraire pour le prochain passage (Journey) : cliqués dans l'ordre, sans recherche à
        # l'écran ni touche A ; None : le Traveler choisit (mémoire, référence, écran).
        self.plan_cells: list[tuple[int, int]] | None = None
        self.teleported: tuple[tuple[int, int], tuple[int, int]] | None = None  # dernier zaap pris : (départ, arrivée)
        # Écoute du trafic du jeu (connection/network.py, 26/10) : carte et case annoncées par le serveur, préférées à la
        # lecture du nom de la carte et à « /p ». None sans Npcap : l'écran seul, comme avant.
        self.net = getattr(game, "net", None)
        self._sun_clicked_at = 0.0  # time.monotonic() du dernier clic sur un soleil (arrivée annoncée après lui)
        self._told: tuple[int, int] | None = None  # dernière carte annoncée par le jeu, pour ne le dire qu'une fois

    def look(self) -> tuple[Frame, MapLabel]:
        """Capture et nom de la carte actuelle (celle dont les coordonnées sont `self.coords`).

        La souris reste où elle est : elle n'est écartée que pour chercher des soleils à l'écran."""
        frame = self.game.look(park=False)
        label = read_map_label(frame) or unreadable_label()
        told = self.told_coords()
        if told is not None:
            # Carte annoncée par le serveur (GDM) : elle fait foi, ni lecture du nom ni « /p ».
            if told != self._told:
                self._told = told
                if self.coords is not None and told != self.coords:
                    log.info("Carte annoncée par le jeu : %s,%s (je me croyais en %s,%s)", *told, *self.coords)
                elif self.coords is None:
                    log.info("Carte annoncée par le jeu : %s,%s", *told)
            self.coords, self.doubt, self._unread_steps = told, False, 0
            self._last_label = label
            return frame, label
        if self._last_label is None:
            self._start(frame, label)
        else:
            self._locate(frame, label, self.coords)
            if self.doubt and hasattr(self, "asker"):
                # Lecture contraire restée sans réponse du jeu : nouvel essai (FORCED_EVERY s au plus souvent).
                asked = self.asker.ask(force=True)
                if asked is not None:
                    log.info("Position donnée par le jeu : %s,%s (je me croyais en %s)", *asked,
                             f"{self.coords[0]},{self.coords[1]}" if self.coords else "?")
                    self.coords, self.doubt, self._unread_steps = asked, False, 0
            if self.coords is None:
                # 16/10 : coordonnées illisibles au départ (Incarnam 0,3 et 6,1) : trajet arrêté, mode zone bloqué 11 min.
                asked = self.asker.ask()
                if asked is not None:
                    self._locate(frame, label, asked)  # comme une position donnée : chiffres inconnus appris
                    self._unread_steps = 0
        self._last_label = label
        return frame, label

    def told_coords(self) -> tuple[int, int] | None:
        """Coordonnées de la carte annoncée par le serveur (écoute réseau) ; None sans écoute, avant le premier
        changement de carte (le jeu ne la répète pas), ou pour une carte hors de la référence de la région."""
        if self.net is None or not self.net.alive():
            return None  # pas d'écoute, ou flux périmé (fenêtre reconnectée, écoute arrêtée) : l'écran
        map_id = self.net.snapshot()[0]
        key, noted = getattr(self.net, "map_key", None), self.__dict__.setdefault("_keys_noted", set())
        if map_id is not None and key and map_id not in noted:
            # Clé de la carte (la même à chaque passage) retenue pour la récolte : ressources de la carte même quand on
            # y est déjà au lancement (brain/harvest.py, 27/10).
            noted.add(map_id)
            remember_key(map_id, key)
        return self.passages.coords_of(map_id) if map_id is not None else None

    def _start(self, frame: Frame, label: MapLabel) -> None:
        """Premier regard : où l'on est. Lecture complète et cohérente (carte de la référence, zone lue d'accord avec
        elle) : elle l'emporte sur la position donnée au lancement (21/10 : --combat-zone 11,-28, l'équipe restée en
        10,-25). Sinon le jeu est interrogé (« /p %pos% ») : 25/10 (journal 201109), nom illisible à Taïnéla, la position
        de --combat-zone (-3,-25) gardée, Elseiso envoyé seul aux Blops. La position donnée ne sert qu'en dernier recours
        (pas de réponse du jeu), si la lecture ne la contredit pas."""
        hint = self.coords
        if label.coords is not None and complete(label.text) and self._coherent(label):
            if hint is not None and label.coords != hint and _truncated(label.coords, hint):
                log.info("Départ lu en %s,%s : %s,%s donné, dernier chiffre perdu à la lecture ; je pars de %s,%s",
                         *label.coords, *hint, *hint)
                return
            if hint is not None and label.coords != hint:
                log.info("Départ lu en %s,%s (position donnée : %s,%s) : je pars d'ici", *label.coords, *hint)
            self.coords = label.coords
            return
        asked = self.asker.ask(force=True)
        if asked is not None:
            if label.readable and reading_fits(label.text, asked):
                learned = learn_digits(label, asked) if label.coords is None else []
                if learned:
                    log.info("Chiffre(s) %s appris sur la carte %s,%s (lu « %s »)", ", ".join(learned), *asked, label.text)
            self.coords, self._unread_steps = asked, 0
            return
        if hint is not None and reading_fits(label.text if label.readable else None, hint):
            log.warning("Départ : %s, pas de réponse à « /p » : je pars de la position donnée %s,%s (supposée)",
                        f"coordonnées lues « {label.text} »" if label.readable else "nom de la carte illisible", *hint)
            self._locate(frame, label, hint)
            return
        if label.coords is not None and self._coherent(label):
            self.coords = label.coords
            return
        log.warning("Départ : coordonnées lues « %s »%s, pas de réponse à « /p » : position inconnue",
                    label.text, f", position donnée {hint[0]},{hint[1]} contredite" if hint else "")
        self.coords = None
        self._locate(frame, label, None)

    def _coherent(self, label: MapLabel) -> bool:
        """Lecture plausible : carte connue de la référence (ou de config/monde), et zone lue (si son nom est reconnu)
        celle que les références donnent à ces coordonnées."""
        if label.coords is None:
            return False
        described = bool(self.passages.reference) or any(m.region == self.region for m in self.atlas.maps.values())
        if described and not self.passages.describes(label.coords) \
                and self.atlas.get(self.region, label.coords) is None:
            return False  # région décrite, carte inconnue : lecture fausse (sans description, rien ne se vérifie)
        zone = self.atlas.zone(self.region, label.coords)
        return label.zone_name is None or zone is None or zone == label.zone_name

    def _locate(self, frame: Frame, label: MapLabel, expected: tuple[int, int] | None) -> None:
        """Coordonnées de la carte : lues, sinon `expected` ; un chiffre inconnu est alors appris.

        25/10 (estime) : une lecture qui va avec `expected`, même partielle ou tronquée (reading_fits), le confirme ;
        une lecture franchement contraire est vérifiée par « /p %pos% » (_contradicted) avant d'y croire."""
        if label.readable and expected is not None and not reading_fits(label.text, expected):
            self._contradicted(frame, label, expected)
            return
        if not label.readable:
            self.coords = expected
            if ("illisible", expected) not in self._unknown_saved:
                self._unknown_saved.add(("illisible", expected))
                path = self.game.recorder.save(frame.image, "nom_de_carte_illisible", lossless=True)
                log.info("Nom de la carte illisible (décor clair derrière le texte ?)%s (capture : %s)",
                         f" : carte {expected[0]},{expected[1]} d'après le chemin" if expected else "", path)
            return
        if label.coords is not None:
            if expected is not None and label.coords != expected:
                # 25/10 (journal 201109) : « 0,-2 » lu en 0,-29, « 0,-3 » en 0,-30 (dernier chiffre perdu sur le décor) :
                # cru téléporté, itinéraire de 32 cartes recalculé, aller-retour sans fin entre 0,-30 et 0,-29.
                log.info("Coordonnées lues %s,%s : %s,%s attendu, dernier chiffre perdu à la lecture ; je suis en %s,%s",
                         *label.coords, *expected, *expected)
                self.coords = expected
                return
            self.coords = label.coords
            self.doubt = False
            return
        if expected is not None:
            if not merge_reading(label.text, expected)[1]:
                # Lecture qui va avec l'attendu sans en avoir la forme exacte (« -2,-32? » : tache du décor après le
                # nombre) : rien d'appris, la position attendue est gardée.
                self.coords = expected
                return
            learned = learn_digits(label, expected)
            if learned:
                log.info("Chiffre(s) %s appris sur la carte %s,%s (lu « %s »)", ", ".join(learned), *expected, label.text)
            self.coords = expected
        if label.text not in self._unknown_saved:
            self._unknown_saved.add(label.text)
            path = self.game.recorder.save(frame.image, "coordonnees_a_apprendre", lossless=True)
            log.info("Coordonnées lues « %s »%s (capture : %s)", label.text,
                     f", carte {expected[0]},{expected[1]} d'après le chemin" if expected else " : position inconnue", path)

    def _contradicted(self, frame: Frame, label: MapLabel, expected: tuple[int, int]) -> None:
        """Lecture franchement contraire à la carte attendue (chiffre lu différent, signe en trop) : vérifiée par
        « /p %pos% » avant d'y croire. 11/10, cité d'Astrub : « -1,-23 » lu pour 1,-23 (tache du décor prise pour un
        tiret), Brawli s'est cru deux cartes plus loin ; 25/10 (journal 201109) : 7 itinéraires recalculés sur 12 à
        cause d'une lecture fausse. Le jeu tranche ; sans réponse, la position à l'estime est gardée (`doubt`).
        Une même lecture sur la même carte n'est vérifiée qu'une fois."""
        key = (expected, label.text)
        if key in self._checked:
            self.coords = self._checked[key]
            return
        asked = self.asker.ask(force=True) if hasattr(self, "asker") else None
        if asked is None:
            log.warning("Coordonnées lues « %s » : pas la carte %s,%s attendue, et pas de réponse à « /p » : je garde "
                        "%s,%s (estime, à vérifier)", label.text, *expected, *expected)
            self.coords, self.doubt = expected, True
            return
        self.doubt = False
        if asked == expected:
            log.info("Coordonnées lues « %s » : le jeu confirme %s,%s (lecture fausse)", label.text, *expected)
        else:
            far = max(abs(asked[0] - expected[0]), abs(asked[1] - expected[1])) > 1
            self.jumped = self.jumped or far
            log.warning("Coordonnées lues « %s » : le jeu me donne en %s,%s au lieu de %s,%s%s", label.text, *asked,
                        *expected, " (personnage déplacé ailleurs : mort, téléportation ?)" if far else "")
            if reading_fits(label.text, asked) and label.coords is None:
                learn_digits(label, asked)
        self.coords = self._checked[key] = asked
        self._unread_steps = 0

    @property
    def passages(self) -> "Passages":
        """Référence des passages de la région (vus en jeu, puis référence complétée), chargée une fois."""
        if getattr(self, "_passages", None) is None or self._passages_region != self.region:
            self._passages, self._passages_region = Passages.load(self.region), self.region
        return self._passages

    def key(self, label: MapLabel | None = None) -> str | None:
        """« Région x,y » de la carte actuelle (région : réglage travel.region)."""
        return World.key(self.region, self.coords)

    def here(self) -> MapInfo | None:
        """Références de la carte actuelle (None : carte ou région inconnues de config/monde)."""
        return self.atlas.get(self.region, self.coords)

    def neighbour(self, side: str) -> tuple[int, int] | None:
        if self.coords is None:
            return None
        info = self.here()
        if info is not None and info.leads_to.get(side):
            return info.leads_to[side]
        dx, dy = DELTA[side]
        return self.coords[0] + dx, self.coords[1] + dy

    def forbidden(self, coords: tuple[int, int] | None, zone: str | None, region: str | None = None) -> str | None:
        """Pourquoi la carte est interdite (réglages travel.avoid_maps / avoid_zones), None si elle ne l'est pas."""
        region = region or self.region
        zone = zone or self.atlas.zone(region, coords)  # zone connue des références, même nom illisible
        for where, avoided in self.settings.avoid_maps:
            if coords == avoided and where in (None, region):
                return f"carte {coords[0]},{coords[1]} interdite"
        for where, avoided in self.settings.avoid_zones:
            if zone == avoided and where in (None, region):
                return f"zone {zone} interdite"
        return None

    def forbidden_side(self, side: str) -> str | None:
        """La carte derrière ce côté est interdite (d'après ses coordonnées, ou sa zone si on y est déjà passé)."""
        coords = self.neighbour(side)
        known = self.world.maps.get(World.key(self.region, coords)) if coords else None
        return self.forbidden(coords, (known or {}).get("zone"))

    def exits(self, frame: Frame, wanted: str | None = None, complete: bool = False) -> dict[str, Exit]:
        """Sorties de la carte ; la touche A seulement si le soleil voulu (ou aucun, sans préférence) n'est pas vu.

        `complete` : il faut toutes les sorties (exploration d'une carte nouvelle) : A dès qu'un côté manque."""
        self.a_unheard = False
        key = self.key()
        known = self.world.suns(key)
        info = self.here()
        reference = {side: cells[0] for side, cells in info.suns.items()} if info else {}
        for side, cells in (info.client_suns.items() if info else ()):
            reference.setdefault(side, cells[0])  # case donnée par le client (soleil « conditionnel »)
        chosen = {}
        if self.coords is not None and self._map_sure():
            for side in DELTA:
                cell = self.passages.sun_cell(self.coords, side, self.entry, self.arrival_cell,
                                              self.next_side if side == wanted else None)
                if cell is not None:
                    chosen[side] = cell
        merged = {**reference, **known}  # ce que le bot a vu prime sur les références
        for side in [s for s in merged if self.world.several(key, s)]:
            del merged[side]  # plusieurs soleils de ce côté (rempart) : la case gardée ne dit pas lequel est le nôtre
        merged.update(self.world.suns_from(key, self.entry))  # celui qui a marché en arrivant par le même côté
        # Référence complétée (data/references) : le soleil joignable depuis la case d'arrivée (et qui mène vers la
        # suite de l'itinéraire) prime sur une case retenue, qui peut être celle de l'autre côté d'une muraille.
        merged.update(chosen)
        described = info is not None and bool(info.suns)  # références avec soleils (Incarnam ; pas le Monde des Douze)
        if merged and (wanted in merged or (wanted is None and (described or not complete))):
            # Carte connue (déjà vue, ou décrite dans config/monde) : la case du soleil est connue, rien à chercher.
            self.candidates = {}
            return {side: Exit(side, cell, _point(frame, cell), 1.0, remembered=True) for side, cell in merged.items()}
        # Les autres joueurs cachés pendant la recherche (touche E tenue, idée de l'utilisateur du 11/10 : dans la
        # cité d'Astrub, les personnages qui passent gênaient la détection des soleils et des contours de la touche A).
        # Toujours la touche A d'abord (11/10, l'utilisateur : dans la cité d'Astrub, des taches orange du décor prises
        # pour des soleils, cliquées, puis un demi-tour) : les contours de A sont les vraies sorties ; les soleils vus
        # sans A ne servent que s'il n'y a aucun contour de ce côté.
        candidates = self._screen_exits(save=True)
        found = {side: exits[0] for side, exits in candidates.items()}
        log.info("Sorties avec la touche A : %s", ", ".join(sorted(found)) or "aucune")
        several = sorted(side for side, exits in candidates.items() if len(exits) > 1)
        if several:
            log.info("Plusieurs soleils %s (carte coupée par un rempart ?) : %s", "/".join(several), " ; ".join(
                f"{side} {', '.join(str(e.cell) for e in candidates[side])}" for side in several))
            if self._sure_here():
                self.world.note_several(key, several)
        self._remember_suns(list(found.values()))
        return found

    def _screen_exits(self, save: bool = False) -> dict[str, list[Exit]]:
        """Tous les soleils de chaque côté, cherchés à l'écran (autres joueurs cachés, touche A si besoin) et rangés
        (_ordered) ; gardés dans `self.candidates`. Les contours de A sont les vraies sorties : les soleils vus sans A
        ne comptent que pour un côté sans aucun contour."""
        with self._players_hidden():
            frame = self.game.look(park=True)  # soleils à chercher : la souris ne doit pas en cacher un
            marked = find_marked_exits(frame, every=True)
            if not marked:  # contours pas encore affichés : un appui sur A
                self.game.press(self.settings.show_exits_key)
                self.game.sleep(0.4)
                frame = self.game.look(park=True)
                marked = find_marked_exits(frame, every=True)
                suns = find_suns(frame, every=True)
                if not marked and not suns:  # « aucune » sortie : impossible (fenêtre sourde à A ?)
                    frame, marked = self._exits_unheard()
                if save and self._a_saved < 5:  # quelques captures pour vérifier la détection des contours
                    self._a_saved += 1
                    self.game.recorder.save(frame.image, "sorties_touche_a", lossless=True)
        found: dict[str, list[Exit]] = {}
        for exit_ in marked:
            found.setdefault(exit_.side, []).append(exit_)
        for exit_ in find_suns(frame, every=True):
            if not any(e.marked for e in found.get(exit_.side, [])):
                found.setdefault(exit_.side, []).append(exit_)
        self.candidates = {side: self._ordered(exits) for side, exits in found.items()}
        self.a_unheard = not found  # aucune sortie, même après déblocage : lecture impossible (go : rien de noté
        # bloqué partout)
        return self.candidates

    def _exits_unheard(self) -> tuple[Frame, list[Exit]]:
        """Ni contour après A ni soleil : impossible, toute carte a au moins une sortie (20/10, S14 : « Sorties avec la
        touche A : aucune », la sortie voulue notée bloquée partout). Relue A_REREAD s plus tard (contours lents à venir),
        puis clic de déblocage sur le fond beige (fenêtre sourde aux touches, brain/recovery.py) et A de nouveau."""
        self.game.sleep(A_REREAD)
        frame = self.game.look(park=True)
        marked = find_marked_exits(frame, every=True)
        if marked or find_suns(frame, every=True):
            log.info("Sorties vues à la 2e lecture, %.1f s plus tard", A_REREAD)
            return frame, marked
        unlocked = unlock_click(self.game, frame)
        self.game.press(self.settings.show_exits_key)
        self.game.sleep(0.4)
        frame = self.game.look(park=True)
        marked = find_marked_exits(frame, every=True)
        if marked:
            mark_heard(self.game)
        else:
            mark_deaf(self.game, f"aucune sortie après {self.settings.show_exits_key.upper()}, deux fois")
        report_episode(f"touche {self.settings.show_exits_key.upper()}", bool(marked),
                       "" if unlocked else "clic impossible, fond beige pas libre")
        return frame, marked

    def _ordered(self, exits: list[Exit]) -> list[Exit]:
        """Le soleil le plus proche de l'endroit où l'on est arrivé d'abord (carte coupée par un rempart : le plus
        souvent celui de notre côté du mur) ; sans point d'arrivée connu, le plus net."""
        if self.entry_cell is None or len(exits) < 2:
            return sorted(exits, key=lambda e: -e.score)
        return sorted(exits, key=lambda e: _screen_gap(e.cell, self.entry_cell))

    @contextlib.contextmanager
    def _players_hidden(self):
        key = self.settings.hide_players_key
        if not key or self._hiding:
            yield
            return
        with self.game.holding(key):
            self._hiding = True
            try:
                yield
            finally:
                self._hiding = False

    def _sure_here(self, label: MapLabel | None = None) -> bool:
        """La carte où l'on est est sûre pour ÉCRIRE dans la mémoire partagée : coordonnées lues à l'écran ; nom
        illisible, position déduite du chemin à SURE_UNREAD cartes au plus d'une carte lue, sans saut ; et ce Traveler
        écrit (pas le trajet d'un suiveur qui rejoint le principal)."""
        if not self.learns or self.coords is None or self.doubt:
            return False
        label = label if label is not None else self._last_label
        if label is not None and label.readable and label.coords is not None:
            return label.coords == self.coords
        return not self.jumped and self._unread_steps <= SURE_UNREAD

    def _map_sure(self, label: MapLabel | None = None) -> bool:
        """La carte où l'on est est sûre pour LIRE la référence complétée : position à l'estime (chaque passage confirmé
        par le décor), qu'aucune lecture ne contredit sans avoir été vérifiée (25/10 : la référence est juste, la
        lecture du nom se trompe ; « 0,-2 » lu en 0,-29 n'enlève plus la référence). Avant : SURE_UNREAD cartes au nom
        illisible au plus, puis les soleils cherchés à l'écran (journal 180222 : de l'autre côté des murets de 1,-33 et
        0,-33, 10 min sans rejoindre le principal)."""
        label = label if label is not None else self._last_label
        if self.coords is None or self.jumped or self.doubt:
            return False
        return label is None or not label.readable or reading_fits(label.text, self.coords)

    def place(self) -> tuple[str, str] | None:
        """(carte, côté par lequel on y est arrivé, START au début du trajet) si la carte est sûre (_sure_here), None
        sinon : ce qui est noté selon le côté d'arrivée (groupes hors d'atteinte, brain/engage.py) n'est lu et écrit
        que là. Sur une carte coupée en deux, ce côté dit quelle moitié est accessible."""
        if not self._sure_here():
            return None
        return self.key(), self.entry or START

    def _remember_suns(self, exits: list[Exit]) -> None:
        if self._sure_here():
            self.world.remember_suns(self.key(), exits)

    def _note_blocked(self, key: str | None, side: str, label: MapLabel, everywhere: bool = False,
                      tried: list[tuple[int, int]] | None = None) -> bool:
        """Sortie notée bloquée dans data/carte.json, seulement si c'est sûr (_sure_here). 11/10 : des suiveurs, pas
        sur la carte qu'ils croyaient, ont noté bloqués les 4 côtés de 4,-20 ; le trajet suivant était impossible.

        Soleil vu mais inatteignable : bloqué seulement pour qui arrive par le même côté (de l'autre côté d'un rempart,
        il peut marcher) ; côté d'arrivée inconnu (début du trajet) : pour ce trajet seulement. `everywhere` : aucun
        soleil de ce côté, même avec la touche A : bloqué pour tous.

        Passage de la référence complétée (contrôlée en jeu, 24/10) : rien d'appris, c'est une anomalie du bot
        (_anomaly) ; True dans ce cas. `tried` : cases des soleils essayés."""
        # Toujours pour ce trajet aussi : la référence complétée l'emporte sur un « bloquée » appris (Passages), un échec
        # vu à l'instant ne doit pas être retenté en boucle (24/10, -1,-21 bas : 25 essais en 4 min).
        self.blocked_here.add((key, side))
        if self.coords is not None and self._map_sure(label) and self.passages.referenced(self.coords).get(side):
            self._anomaly(side, label, tried or [])
            return True
        if self._sure_here(label) and everywhere:
            self.world.link(key, side, BLOCKED)
        elif self._sure_here(label) and self.entry:
            self.world.block_from(key, self.entry, side)
            log.info("Sortie du %s notée inatteignable pour qui arrive par le %s", side, self.entry)
        elif self._sure_here(label):
            self.blocked_here.add((key, side))
            log.info("Sortie du %s bloquée pour ce trajet seulement (côté d'arrivée inconnu)", side)
        else:
            self.blocked_here.add((key, side))
            log.info("Sortie du %s bloquée pour ce trajet seulement : carte pas sûre (coordonnées pas lues), rien "
                     "d'écrit dans la mémoire", side)
        return False

    def _anomaly(self, side: str, label: MapLabel, tried: list[tuple[int, int]]) -> None:
        """Passage officiel raté (24/10, utilisateur : la référence est juste, contrôlée en jeu ; une erreur vient du
        bot) : pas de « bloquée » appris, seulement évité pour ce trajet. Noté avec sa capture et la cause probable dans
        data/anomalies_passages.jsonl, pour corriger le bot (perception, soleil choisi, joueur dessus, latence)."""
        coords = self.coords
        reachable = self.passages.reachable(coords, side, self.arrival_cell)
        expected = [tuple(c) for c in reachable] if reachable is not None else \
            [c for c in [self.passages.sun_cell(coords, side, self.entry, self.arrival_cell)] if c is not None]
        if not tried:
            cause = "aucun soleil vu à l'écran, même avec la touche A (perception)"
        elif expected and not any(distance(c, e) <= SAME_SUN for c in tried for e in expected):
            cause = f"soleil(s) cliqué(s) {tried} hors de ceux de la référence {expected}"
        elif self._walked:
            cause = "le personnage a marché puis s'est arrêté (case d'arrivée supposée fausse ? obstacle mobile ?)"
        else:
            cause = "personnage immobile après le clic (clic perdu, menu, joueur sur le soleil, latence ?)"
        path = self.game.recorder.save(self.game.look().image, "anomalie_passage", lossless=True)
        log.warning("Anomalie : passage officiel du %s raté en %s,%s (%s) ; pas noté bloqué, évité pour ce trajet "
                    "(capture : %s)", side, *coords, cause, path)
        entry = {"quand": time.strftime("%Y-%m-%d %H:%M:%S"), "carte": f"{coords[0]},{coords[1]}", "cote": side,
                 "vers": "{},{}".format(*self.passages.referenced(coords)[side]), "arrivee_par": self.entry,
                 "case_arrivee": self.arrival_cell, "soleils_essayes": tried, "soleils_reference": expected,
                 "cause": cause, "capture": str(path) if path else None}
        try:
            ANOMALIES.parent.mkdir(parents=True, exist_ok=True)
            with ANOMALIES.open("a", encoding="utf-8") as out:
                out.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as error:
            log.debug("Anomalie non écrite (%s)", error)

    def go(self, frame: Frame, label: MapLabel, side: str, exits: dict[str, Exit] | None = None) -> MapLabel | None:
        """Passe sur la carte voisine par le côté `side` ; None si c'est impossible (ou si un combat commence :
        `interrupted`).

        `plan_cells` (itinéraire de la référence, Journey) : ces soleils seulement, dans l'ordre, sans recherche à
        l'écran ni touche A (25/10, journal 201109 : 6 recherches à la touche A sur des cartes que la référence décrit,
        dont 4 sans voir le bon soleil). Tous ratés : anomalie notée, l'itinéraire décide de la suite."""
        self.interrupted = False
        planned = list(self.plan_cells or ())
        if planned:
            return self._cross(frame, label, side, [Exit(side, c, _point(frame, c), 1.0, remembered=True)
                                                    for c in planned], searched=True)
        exits = exits if exits is not None and side in exits else self.exits(frame, side)
        exit_ = exits.get(side)
        if self.coords is not None and self.passages.reachable(self.coords, side, self.arrival_cell) == []:
            # Référence complétée : aucun soleil de ce côté n'est joignable depuis la case où l'on est arrivé
            # (muraille, falaise : 24/10, 0,-14 gauche en venant de 1,-14 par le bas du rempart) : pas de clic perdu.
            log.info("Sortie du %s inatteignable depuis ma case d'arrivée %s d'après la carte officielle : je ne "
                     "l'essaie pas", side, self.arrival_cell)
            self.blocked_here.add((self.key(label), side))
            return None
        reference_cell = self.passages.sun_cell(self.coords, side, self.entry, self.arrival_cell, self.next_side) \
            if exit_ is None and self.coords is not None else None
        if reference_cell is not None:
            # 24/10, 9,-20 haut : contours de A pas vus, sortie notée bloquée, alors qu'elle est dans la référence
            # complétée (et déjà prise en jeu) : sa case de soleil d'abord, avant de conclure qu'il n'y a pas de sortie.
            log.info("Pas de soleil vu vers le %s, mais la référence en donne un en %s : je l'essaie", side, reference_cell)
            exit_ = Exit(side, reference_cell, _point(frame, reference_cell), 1.0, remembered=True)
        if exit_ is None and self.a_unheard:
            # 20/10 (S14) : A sans aucun contour, la fenêtre n'avait pas reçu la touche ; la sortie notée bloquée pour
            # tous les passages. Jamais sur une lecture vide : bloquée pour ce trajet seulement.
            log.warning("Pas de sortie vers le %s, mais la touche A n'a montré aucune sortie (lecture impossible) : je "
                        "ne la note pas bloquée, seulement évitée pour ce trajet", side)
            self.blocked_here.add((self.key(label), side))
            return None
        if exit_ is None:
            if not self._note_blocked(self.key(label), side, label, everywhere=True):
                log.info("Pas de sortie vers le %s sur cette carte, même avec la touche A : je la note bloquée", side)
            return None
        queue = [exit_] + [e for e in self.candidates.get(side, []) if distance(e.cell, exit_.cell) > SAME_SUN]
        return self._cross(frame, label, side, queue, searched=not exit_.remembered)

    def _cross(self, frame: Frame, label: MapLabel, side: str, queue: list[Exit], searched: bool) -> MapLabel | None:
        """Clic sur les soleils de `queue` l'un après l'autre jusqu'au passage ; `searched` : ceux de ce côté ont déjà
        été cherchés à l'écran (sinon, le dernier raté, on les y cherche)."""
        before_key, before_coords, entry = self.key(label), self.coords, self.entry
        self.game.recorder.save(annotate(frame, marks=[(e.point, f"sortie {side}") for e in queue]), "changement_de_carte")
        tried: list[Exit] = []
        fast = self.settings.still_for > 0
        while queue and len(tried) < MAX_CANDIDATES:
            exit_ = queue.pop(0)
            tried.append(exit_)
            if len(tried) > 1:
                log.info("Autre soleil du %s : %s (le précédent n'est pas atteignable d'ici)", side, exit_.cell)
            spot = 0  # endroit de la case où cliquer : d'abord le centre, puis là où un personnage ne se tient pas
            attempt, menus = 0, 0
            while attempt < CANDIDATE_TRIES:
                attempt += 1
                # Soleil entouré par la touche A : son losange tel qu'il est vu (grille décalée sur certaines cartes).
                self.aim_center = exit_.point if exit_.marked else None
                point, spread = _click_spot(frame, exit_.cell, spot, self.aim_center)
                log.info("Vers la carte du %s : clic sur le soleil en %s%s", side, exit_.cell,
                         " (contour de la touche A)" if exit_.marked and not spot else " (bas de la case)" if spot else "")
                with self._players_hidden():  # tenue pendant la marche : le mouvement se voit sans les autres joueurs
                    self.click_sun(point, spread)
                    if self.on_click is not None:
                        # À chaque essai, avec l'endroit visé (20/10, soleil (27,5) de 5,5 : le principal a recliqué
                        # le bas de la case et il est passé ; ses 5 suiveurs immobiles, recliqués seulement après son
                        # arrivée, ~7 s perdues). L'équipe ne reclique que ceux qui n'ont pas quitté le départ.
                        self.on_click(side, exit_.cell, frame, spot)
                    reclick = None if fast else (lambda point=point, spread=spread: self.click_sun(point, spread))
                    arrived = self._wait_arrival(label, frame, before_coords, reclick=reclick)
                if arrived is None:
                    self.interrupted = True
                    return None  # combat (agression) ou autre fenêtre : la boucle principale s'en charge
                if arrived == MENU:
                    # Quelqu'un sur le soleil, souvent soi-même (arrivé par ce soleil) : son menu au lieu du déplacement.
                    # Fermé d'un clic à côté, qui écarte le personnage de la case (close_context_menu), puis le soleil
                    # de nouveau, au centre, puis aux pointes (MENU_SPOTS) ; MENU_RETRIES fois sans compter d'essai.
                    log.info("Un menu de personnage s'est ouvert au lieu de la marche (quelqu'un sur le soleil, "
                             "peut-être moi) : je le ferme en m'écartant, puis je reclique le soleil")
                    if not close_context_menu(self.game, self.game.look(park=False)):
                        close_any_menu(self.game, "Soleil")  # 31/10 : plus d'Échap (Menu principal derrière un menu)
                    if menus < MENU_RETRIES:
                        spot = MENU_SPOTS[min(menus, len(MENU_SPOTS) - 1)]
                        menus += 1
                        attempt -= 1
                    else:
                        spot += 1
                    continue
                if isinstance(arrived, tuple):
                    new_frame, new_label = arrived
                    if self.learns:
                        self.world.confirm_sun(before_key, side, exit_.cell, entry if self._sure_here(label) else None)
                    self._arrived(new_frame, new_label, before_key, before_coords, side, exit_.cell)
                    if self.on_crossed is not None:
                        # Coordonnées complétées par celles déduites du chemin (20/10, 11,-23 : « 11,-2? » lu par le
                        # principal, « 11,-23 » par ses suiveurs ; les noms comparés ne collaient pas, 6 suiveurs crus
                        # perdus et le bot arrêté).
                        self.on_crossed(side, exit_.cell, _completed(label, before_coords),
                                        _completed(new_label, self.coords))
                    return new_label
                if arrived == STILL and self._walked:
                    log.info("Le personnage a marché vers le soleil en %s puis s'est arrêté sans changer de carte : pas "
                             "atteignable d'ici", exit_.cell)
                    break  # pas de second clic : il est déjà allé au plus près
                if arrived == STILL:
                    log.info("Le personnage ne bouge plus et n'a pas changé de carte : soleil en %s pas atteignable "
                             "d'ici ? (essai %d/%d)", exit_.cell, attempt, CANDIDATE_TRIES)
                else:
                    log.warning("Toujours sur la même carte après %.0f s (soleil en %s, essai %d/%d)",
                                self.settings.walk_timeout, exit_.cell, attempt, CANDIDATE_TRIES)
                spot += 1
            if exit_.remembered and self.learns and not self.plan_cells:
                self.world.forget_sun(before_key, side)
            if not queue and not searched:
                # Soleil de la mémoire raté : là seulement, on cherche à l'écran (souris écartée) ceux de ce côté.
                searched = True
                found = self._screen_exits().get(side, [])
                if len(found) > 1 and self.learns and self._sure_here(label):
                    self.world.note_several(before_key, [side])
                queue = [e for e in found if all(distance(e.cell, t.cell) > SAME_SUN for t in tried)]
                if queue:
                    log.info("Soleil(s) du %s retrouvé(s) à l'écran : %s", side, ", ".join(str(e.cell) for e in queue))
                frame = self.game.look(park=False)
        if not self._note_blocked(before_key, side, label, tried=[e.cell for e in tried]):
            path = self.game.recorder.save(self.game.look().image, "sortie_bloquee", lossless=True)
            log.warning("Sortie du %s inutilisable, je la note bloquée (capture : %s)", side, path)
        return None

    def teleport(self, leg: Leg) -> MapLabel | None:
        """Zaap de la carte (case `leg.cell`) vers le zaap `leg.zaap` (carte `leg.dest`), brain/zaap.py ; None si c'est
        impossible (fenêtre refermée). Arrivée contrôlée comme après un soleil : lecture compatible, sinon « /p »."""
        from bot.brain.zaap import use_zaap  # évite un import circulaire (zaap -> itinerary)

        self.interrupted = False
        if not use_zaap(self.game, leg.cell, leg.zaap, leg.dest, self.settings.hide_players_key):
            return None
        frame = self.game.look(park=False)
        label = read_map_label(frame) or unreadable_label()
        # Le personnage apparaît au pied du zaap d'arrivée : sa case sert de case d'arrivée (cases joignables d'après
        # la carte officielle, Planner).
        self.entry, self.entry_cell = None, None
        self.arrival_cell = Planner(self.passages.reference).zaap_cell(leg.dest)
        self._unread_steps = 0
        self.teleported = (leg.coords, leg.dest)
        self._locate(frame, label, leg.dest)
        self._last_label = label
        log.info("Nouvelle carte (zaap) : %s%s", label.describe(),
                 f" (coordonnées {self.coords[0]},{self.coords[1]})" if self.coords else "")
        return label

    def click_sun(self, point: tuple[float, float], spread: tuple[float, float], game: Game | None = None) -> bool:
        """Clic sur un soleil, la touche qui cache les autres joueurs tenue (travel.hide_players_key) ; `game` : la
        fenêtre d'un suiveur (même réglage). False si le clic a été annulé."""
        game = game or self.game
        if game is self.game:
            self._sun_clicked_at = time.monotonic()
        key = self.settings.hide_players_key
        if not key or (game is self.game and self._hiding):
            return game.click(point, spread) is not False
        with game.holding(key):
            return game.click(point, spread) is not False

    def _click_lost(self, clicked: float) -> bool:
        """Clic sur un soleil perdu d'après le jeu : CLICK_TAKEN_WAIT s passées, mon personnage connu, et ni déplacement
        annoncé (GA0;1) ni nouvelle carte depuis le clic. False sans écoute vivante (l'écran décide, comme avant)."""
        net = self.net
        if net is None or not net.alive() or net.my_id is None or time.monotonic() - clicked < CLICK_TAKEN_WAIT:
            return False
        return net.moved_at < clicked and net.new_map_since(clicked) is None

    def _wait_arrival(self, before: MapLabel, before_frame: Frame, before_coords: tuple[int, int] | None,
                      reclick: Callable[[], object] | None = None) -> tuple[Frame, MapLabel] | str | bool | None:
        """Nouvelle carte (lue une fois l'affichage stable), MENU si un menu s'est ouvert, False si on n'a pas changé
        de carte, None si un combat commence.

        Arrivée : tout le décor a changé, et les coordonnées lues ne sont plus celles de départ (ou ne se lisent
        pas et le décor ne bouge plus). Un nom qui devient lisible un instant pendant la marche ne suffit pas.
        `reclick` : nouveau clic sur le soleil, toutes les RECLICK_EVERY s tant que le décor n'a pas changé."""
        deadline = time.monotonic() + self.settings.walk_timeout
        departure, previous, clicked = _scene(before_frame), None, time.monotonic()
        motion, last_motion, counts = None, clicked, []
        same_name = 0  # regards d'affilée : autre décor stable, nom lu comme au départ
        announced = None  # time.monotonic() où la nouvelle carte a été annoncée par le jeu (écoute réseau)
        taken_checked = False  # « clic pris » vérifié au réseau (CLICK_TAKEN_WAIT)
        while time.monotonic() < deadline:
            self.game.sleep(0.4)
            if not taken_checked and reclick is not None and self._click_lost(clicked):
                # 31/10, revue : rien d'annoncé par le jeu (ni déplacement de mon personnage, ni carte) : clic perdu ;
                # recliqué tout de suite, au lieu d'attendre l'immobilité à l'écran (plusieurs secondes).
                taken_checked = True
                log.info("Clic sur le soleil pas pris d'après le jeu (aucun déplacement annoncé en %.1f s) : je reclique",
                         CLICK_TAKEN_WAIT)
                reclick()
                clicked = last_motion = time.monotonic()
            frame = self.game.look(park=False)
            if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                log.info("Combat pendant le trajet")
                return None
            if find_context_menu(frame) is not None:
                return MENU
            scene = _scene(frame)
            changed = _gap(scene, departure) > SCENE_CHANGED
            if self.net is not None and self.net.new_map_since(self._sun_clicked_at) is not None:
                # Nouvelle carte annoncée et chargée (GDM puis GDK) : arrivé. Le décor suit d'un instant ; attendu
                # NET_SCENE_WAIT s au plus pour que la capture d'arrivée montre la nouvelle carte.
                announced = announced or time.monotonic()
                if changed or time.monotonic() - announced >= NET_SCENE_WAIT:
                    log.debug("Nouvelle carte annoncée par le jeu, %.1f s après le clic", announced - clicked)
                    new_frame, new_label = self._settled(frame, read_map_label(frame) or unreadable_label())
                    if new_label.coords is not None and new_label.coords == before_coords:
                        new_label = unreadable_label()  # nom de la carte de départ encore affiché : il ne dit rien
                    return new_frame, new_label
                continue
            stable = previous is not None and _gap(scene, previous) < SCENE_STABLE
            previous = scene
            view = _motion_view(frame)
            if motion is not None:
                moved_cells = _motion(view, motion)
                counts.append(moved_cells)
                if moved_cells >= self.settings.motion_cells:
                    last_motion = time.monotonic()
            motion = view
            if not changed and self.settings.still_for > 0:
                now = time.monotonic()
                if now - clicked >= self.settings.still_after + self.settings.still_for \
                        and now - last_motion >= self.settings.still_for:
                    label = read_map_label(frame)
                    if label is not None and label.coords and before_coords and label.coords != before_coords:
                        # Décor presque pareil d'une carte à l'autre (24/10, plaines des Porkass -3,-37 → -3,-38 : sous
                        # SCENE_CHANGED, trois sorties notées bloquées alors que l'équipe était passée) : les
                        # coordonnées lues tranchent.
                        log.debug("Arrivé, décor presque pareil (coordonnées %s,%s lues)", *label.coords)
                        return self._settled(frame, label)
                    log.debug("Immobile %.1f s après le clic (cases qui bougent : %s)", now - clicked, counts)
                    # Il a marché puis s'est arrêté : il est allé au plus près, le soleil est de l'autre côté d'un mur.
                    self._walked = sum(c >= self.settings.motion_cells for c in counts) >= WALKED_POLLS
                    return STILL
            if not changed:
                if reclick is not None and time.monotonic() - clicked >= RECLICK_EVERY:
                    log.debug("Toujours sur la carte de départ : je reclique sur le soleil")
                    reclick()
                    clicked = time.monotonic()
                continue  # en chemin
            label = read_map_label(frame)
            if label is None:
                if stable:
                    log.debug("Arrivé (cases qui bougeaient : %s)", counts)
                    return self._settled(frame, unreadable_label())  # autre décor, stable : arrivé, nom illisible
                continue  # chargement de la carte
            moved = label.coords != before_coords if label.coords and before_coords else not label.same_map(before)
            if moved:
                log.debug("Arrivé (cases qui bougeaient : %s)", counts)
                return self._settled(frame, label)
            if stable and find_unknown_window(frame) is None:
                # 25/10 : le décor fait foi, pas la lecture. Tout le décor a changé et ne bouge plus, aucune fenêtre
                # par-dessus, mais le nom se lit encore comme au départ (lecture fausse sur le nouveau décor) : avant,
                # attente jusqu'à walk_timeout puis passage cru raté alors qu'il avait eu lieu.
                same_name += 1
                if same_name >= DECOR_OVER_NAME:
                    log.info("Décor changé et stable, nom lu comme au départ (« %s ») : je me fie au décor, nouvelle "
                             "carte", label.text)
                    return self._settled(frame, label)
        label = read_map_label(self.game.look(park=False))
        if label is not None and label.coords and before_coords and label.coords != before_coords:
            log.debug("Arrivé, décor presque pareil (coordonnées %s,%s lues)", *label.coords)
            return self._settled(self.game.look(park=False), label)
        log.debug("Pas arrivé en %.0f s (cases qui bougeaient : %s)", self.settings.walk_timeout, counts)
        return False

    def _settled(self, frame: Frame, label: MapLabel) -> tuple[Frame, MapLabel]:
        self.game.sleep(self.settings.settle)
        settled_frame = self.game.look(park=False)
        settled = read_map_label(settled_frame)
        return (settled_frame, settled) if settled is not None else (frame, label)

    def _arrived(self, frame: Frame, label: MapLabel, before_key: str | None, before_coords: tuple[int, int] | None,
                 side: str, cell: tuple[int, int] | None = None) -> None:
        self.entry = OPPOSITE[side]
        # Case d'arrivée d'après la référence complétée (le soleil pris) : choisit le soleil suivant (Passages.sun_cell)
        self.arrival_cell = self.passages.arrival(before_coords, side, cell) if before_coords is not None else None
        self.entry_cell = _facing(frame, cell, side) if cell is not None else None
        self._unread_steps = 0 if label.readable and label.coords is not None else self._unread_steps + 1
        expected = None
        if before_coords is not None:
            info = self.atlas.get(self.region, before_coords)
            dx, dy = DELTA[side]
            # Carte où mène le côté d'après la référence complétée (sauts du jeu compris), sinon config/monde, sinon la
            # voisine d'à côté : la position à l'estime, que la lecture ne fait que contrôler (_locate).
            expected = self.passages.referenced(before_coords).get(side) \
                or (info.leads_to.get(side) if info else None) or (before_coords[0] + dx, before_coords[1] + dy)
        told = self.told_coords()
        if told is not None:
            # Carte et case d'arrivée annoncées par le serveur (GDM, GM) : exactes, ni lecture ni « /p ».
            self.coords, self.doubt, self._unread_steps, self._told = told, False, 0, told
            cell = self.net.my_grid_cell()
            if cell is not None:
                self.arrival_cell = cell
            self._last_label = label
            log.info("Nouvelle carte : %s,%s, case d'arrivée %s (annoncées par le jeu)%s", *told,
                     cell if cell is not None else "inconnue",
                     f" ; attendue {expected[0]},{expected[1]}" if expected is not None and expected != told else "")
        else:
            self._locate(frame, label, expected)
            self._last_label = label  # carte sûre ou non (place) dès l'arrivée, avant le look suivant
            log.info("Nouvelle carte : %s%s", label.describe(),
                     f" (coordonnées {self.coords[0]},{self.coords[1]} d'après le chemin)"
                     if self.coords and label.coords != self.coords else "")
        key = self.key(label)
        # 11/10 : le regroupement d'un suiveur effaçait les sorties bloquées du principal ; 25/10 : lecture contraire
        # pas vérifiée (`doubt`) : rien d'appris sur une position incertaine.
        if key and self.learns and not self.doubt:
            self.world.link(before_key, side, key)
            if before_key and self.world.destination(key, OPPOSITE[side]) != BLOCKED:
                # Le soleil d'en face ramène d'où l'on vient, sauf s'il a été vu inutilisable : une carte coupée par un
                # rempart n'atteint parfois ce soleil que d'un côté (cité d'Astrub).
                self.world.link(key, OPPOSITE[side], before_key)


def _completed(label: MapLabel, coords: tuple[int, int] | None) -> MapLabel:
    """Nom de carte lisible aux coordonnées incomplètes (« 11,-2? ») : complétées par `coords` (carte déduite du
    chemin). Nom illisible, ou coordonnées déjà lues : tel quel."""
    if label.coords is not None or coords is None or not label.readable:
        return label
    return replace(label, coords=coords)


# Échap sans menu à fermer : le Menu principal s'ouvre, parfois plus d'un instant après (20/10, Vertdepomme : pas vu au
# regard de 0,3 s, six reclics tombés dedans, ~25 s perdues). Cherché ESCAPE_LOOKS fois, toutes les ESCAPE_EVERY s.
ESCAPE_LOOKS, ESCAPE_EVERY = 3, 0.3


def escape_menu(game: Game) -> None:
    """Échap pour fermer un menu ouvert par erreur (quelqu'un sur le soleil). S'il n'y avait rien à fermer, Échap ouvre
    le Menu principal (Changer de perso / Déconnecter / Quitter Dofus) : refermé aussitôt par Échap, sans aucun clic.
    11/10 : le Menu principal pris pour un menu de joueur, un clic « ailleurs » est tombé sur « Déconnecter »."""
    game.press("escape")
    specs = load_popup_specs()
    for _ in range(ESCAPE_LOOKS):
        game.sleep(ESCAPE_EVERY)
        known = find_known_popup(game.look(park=False), specs)
        if known is not None and known.spec.name == "menu principal":
            log.info("Échap a ouvert le Menu principal : je le referme (Échap)")
            game.press("escape")
            game.sleep(0.3)
            return


def _click_spot(frame: Frame, cell: tuple[int, int], spot: int,
                center: tuple[float, float] | None = None) -> tuple[tuple[float, float], tuple[float, float]]:
    """Où cliquer dans la case du soleil, et avec quelle dispersion. 0 : autour du centre ; ensuite, le bas du
    losange, puis ses pointes gauche et droite : un personnage debout sur la case en cache le centre et le haut.
    `center` : centre du losange vu (contour de la touche A), à la place de celui de la grille."""
    cx, cy = center or frame.grid.center(cell)
    hw, hh = frame.grid.half_w, frame.grid.half_h
    spots = [(cx, cy), (cx, cy + 0.55 * hh), (cx - 0.55 * hw, cy + 0.1 * hh), (cx + 0.55 * hw, cy + 0.1 * hh)]
    # Case du bord de la carte (21/10, Astrub : les 9 clics sans effet sur 42 changements de carte étaient tous sur des
    # soleils de la rangée du haut, du bas ou de la colonne de gauche, cliqués autour du centre ; les 9 reclics, sur le
    # bas du losange, sont tous partis) : le bas du losange d'emblée, à dispersion serrée.
    left, top, right, bottom = frame.layout.map_box
    edge = min(cx - left, right - cx) < 1.2 * hw or min(cy - top, bottom - cy) < 1.2 * hh
    if edge:
        spots = [spots[1], spots[0], spots[2], spots[3]]  # le bas, puis le centre, puis les pointes
    x, y = spots[min(spot, len(spots) - 1)]
    spread = frame.grid.click_spread() if spot == 0 and not edge else (hw * 0.12, hh * 0.1)
    return (x, y), spread


def _point(frame: Frame, cell: tuple[int, int]) -> tuple[int, int]:
    x, y = frame.grid.center(cell)
    return round(x), round(y)


def _scene(frame: Frame) -> np.ndarray:
    """Vue très réduite de la carte, en niveaux de gris : assez pour voir qu'on a changé de carte."""
    gray = cv2.cvtColor(frame.crop(frame.layout.map_box), cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)


def _gap(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a - b).mean())


def _motion_view(frame: Frame) -> np.ndarray:
    gray = cv2.cvtColor(frame.crop(frame.layout.map_box), cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, MOTION_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)


def _motion(a: np.ndarray, b: np.ndarray) -> int:
    """Cases de la vue réduite qui ont changé : un personnage qui marche en fait changer quelques-unes."""
    return int((np.abs(a - b) > MOTION_LEVEL).sum())


def _screen_gap(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Distance à l'écran entre deux cases (une case est deux fois plus large que haute)."""
    return math.hypot(2 * (a[0] - b[0]), a[1] - b[1])


def _facing(frame: Frame, cell: tuple[int, int], side: str) -> tuple[int, int]:
    """Case d'arrivée estimée sur la nouvelle carte : au bord opposé, en face du soleil pris (même rangée ou colonne)."""
    cells = frame.grid.cells()
    i, j = cell
    if side == "gauche":
        return max(c[0] for c in cells), j
    if side == "droite":
        return min(c[0] for c in cells), j
    if side == "haut":
        return i, max(c[1] for c in cells)
    return i, min(c[1] for c in cells)


# Deux fenêtres sur la même carte montrent le même décor, aux personnages près : 0,8 d'écart entre trois personnages en
# 4,-20, 41 avec celui en 3,-20 (11/10, cité d'Astrub, noms de carte illisibles sur les murs clairs).
SAME_SCENE = 6.0


def same_map(a: Frame, b: Frame) -> bool:
    """Les deux captures (deux fenêtres) montrent la même carte, d'après le décor (sans lire le nom de la carte)."""
    return _gap(_scene(a), _scene(b)) < SAME_SCENE


def _coords_of(key: str) -> tuple[int, int] | None:
    """« Incarnam 3,6 » -> (3, 6)."""
    try:
        x, y = key.rsplit(" ", 1)[1].split(",")
        return int(x), int(y)
    except (IndexError, ValueError):
        return None


def merge_reading(text: str, expected: tuple[int, int]) -> tuple[tuple[int, int], bool]:
    """Coordonnées lues en partie (« ?,-18 », « 5,-1? ») et carte attendue d'après le chemin : chaque coordonnée lue en
    entier l'emporte, les autres sont celles attendues. False si la lecture contredit l'attendu (un chiffre lu
    différent, ou pas le même nombre de chiffres)."""
    parts = text.split(",")
    if len(parts) != 2:
        return expected, True
    coords, consistent = [], True
    for part, value in zip(parts, expected):
        want = str(value)
        if "?" not in part:
            try:
                read = int(part)
            except ValueError:
                return expected, True
            coords.append(read)
            consistent = consistent and read == value
        else:
            coords.append(value)
            consistent = consistent and len(part) == len(want) and all(c in ("?", w) for c, w in zip(part, want))
    return (coords[0], coords[1]), consistent


def _character(game: Game) -> str | None:
    """Nom du personnage de la fenêtre (« Koalaloupour - Dofus Retro v1.49.7 » -> « Koalaloupour »)."""
    title = getattr(getattr(game, "window", None), "title", "") or ""
    name = title.split(" - ")[0].strip()
    return name if name and "Dofus" not in name else None


def shortest_path(start: tuple[int, int], goals, links, allowed) -> list[tuple[str, tuple[int, int]]] | None:
    """Plus court chemin (en nombre de cartes, en largeur d'abord) de `start` jusqu'à la plus proche des cartes
    `goals` : [(côté pris, carte atteinte), ...], [] si on y est déjà, None si aucune n'est accessible.

    `links(carte)` : côté -> carte voisine ; `allowed(carte)` : on peut y passer (la carte de départ l'est toujours)."""
    came: dict = {start: None}  # carte -> (carte précédente, côté pris)
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current in goals:
            path = []
            while came[current] is not None:
                previous, side = came[current]
                path.append((side, current))
                current = previous
            return path[::-1]
        for side, dest in links(current).items():
            if dest in came or not allowed(dest):
                continue
            came[dest] = (current, side)
            queue.append(dest)
    return None


def shortest_route(start: tuple[int, int], entry: str | None, goals, links, allowed, cost=None) -> list | None:
    """Comme shortest_path, sur les états (carte, côté d'arrivée) : sur une carte coupée par un rempart, une sortie peut
    n'être inatteignable que pour qui arrive par un certain côté. `links(carte, côté d'arrivée)`. `cost(carte, côté,
    carte voisine)` : coût du passage (défaut 1 : le moins de cartes) ; chemin le moins coûteux."""
    first = (start, entry)
    came: dict = {first: None}
    best = {first: 0.0}
    queue = [(0.0, 0, first)]
    order = 1  # départage des égalités : ordre d'arrivée (comme la largeur d'abord)
    while queue:
        spent, _, state = heapq.heappop(queue)
        if spent > best.get(state, float("inf")):
            continue
        if state[0] in goals:
            path = []
            while came[state] is not None:
                previous, side = came[state]
                path.append((side, state[0]))
                state = previous
            return path[::-1]
        for side, dest in links(*state).items():
            following = (dest, OPPOSITE[side])
            if not allowed(dest):
                continue
            total = spent + (cost(state[0], side, dest) if cost is not None else 1.0)
            if total >= best.get(following, float("inf")):
                continue
            best[following], came[following] = total, (state, side)
            heapq.heappush(queue, (total, order, following))
            order += 1
    return None


class RoundTrip:
    """Test : s'éloigner de `steps` cartes (sans revenir en arrière), puis refaire le chemin jusqu'au départ."""

    def __init__(self, traveler: Traveler, steps: int) -> None:
        self.traveler = traveler
        self.steps = steps
        self.start: MapLabel | None = None
        self.path: list[str] = []  # côtés empruntés à l'aller
        self.returning = False
        self.done = False
        self.failures = 0

    def step(self) -> None:
        frame, label = self.traveler.look()
        if self.start is None:
            if not label.readable:
                log.info("Nom de la carte de départ illisible : j'attends")
                return
            self.start = label
            log.info("Test de déplacement : départ de %s, %d cartes puis retour", label.describe(), self.steps)
        if not self.returning and len(self.path) < self.steps:
            exits = self.traveler.exits(frame)
            back = OPPOSITE[self.path[-1]] if self.path else None
            choices = [s for s in SIDES if s in exits and s != back and not self.traveler.forbidden_side(s)]
            if not choices:
                self._fail("aucune sortie autorisée pour continuer")
                return
            side = self.path[-1] if self.path and self.path[-1] in choices else choices[0]  # tout droit si possible
            if self.traveler.go(frame, label, side, exits) is not None:
                self.path.append(side)
            else:
                self._fail(f"passage vers le {side} raté")
            return
        self.returning = True
        side = OPPOSITE[self.path[-1]]
        arrived = self.traveler.go(frame, label, side)
        if arrived is None:
            self._fail(f"retour vers le {side} raté")
            return
        self.path.pop()
        if not self.path:
            self.done = True
            if arrived.same_map(self.start):
                log.info("Test réussi : revenu au départ (%s)", arrived.describe())
            else:
                log.warning("Test fini sur %s au lieu du départ %s", arrived.describe(), self.start.describe())

    def _fail(self, why: str) -> None:
        self.failures += 1
        log.warning("Test de déplacement : %s (%d/3)", why, self.failures)
        if self.failures >= 3:
            log.warning("Test de déplacement abandonné")
            self.done = True


class Explorer:
    """Visite toutes les cartes de la région de départ (Incarnam).

    Région décrite dans config/monde : tournée des cartes des références, en allant chaque fois vers la carte pas
    encore vue la plus proche (plus court chemin par les soleils connus). Sinon : découverte, chaque sortie essayée
    une fois. Jamais dans les cartes et zones interdites (travel.avoid_maps / avoid_zones) : demi-tour si on y
    arrive. Arrêt si le personnage se retrouve ailleurs sans être passé par un soleil (mort, téléportation)."""

    def __init__(self, traveler: Traveler, zone_only: bool = False) -> None:
        self.traveler = traveler
        self.world = traveler.world
        self.zone_only = zone_only  # Monde des Douze : la zone de départ seulement (le monde entier est bien trop grand)
        self.zone: str | None = None
        self.start: MapLabel | None = None
        self.came_from: str | None = None  # côté par lequel on est arrivé sur la carte actuelle
        self.done = False
        self.failures = 0  # changements de carte ratés d'affilée
        self.visited: set[tuple[int, int]] = set()  # cartes vues pendant cette exploration

    def step(self) -> None:
        frame, label = self.traveler.look()
        if self.start is None:
            self.start = label
            log.info("Exploration de %s depuis %s", self.traveler.region, label.describe())
        if self.traveler.jumped:
            log.warning("Exploration arrêtée : le personnage a été déplacé ailleurs (mort au combat, téléportation ?). "
                        "Ramène-le dans la région, puis relance avec --position x,y")
            self.done = True
            return
        key = self.traveler.key(label)
        if key is None:
            log.warning("Exploration impossible : coordonnées de la carte inconnues (chiffre pas encore appris). "
                        "Relance avec --position x,y (les coordonnées affichées en haut à gauche)")
            self.done = True
            return
        forbidden = self.traveler.forbidden(self.traveler.coords, label.zone_name)
        if forbidden:
            self.world.visit(key, label, [])  # sa zone est retenue : on ne la reprendra plus
            self._go(frame, label, self.came_from, f"{forbidden} : demi-tour")
            return
        exits = self.traveler.exits(frame, complete="vue" not in self.world.maps.get(key, {}))  # carte jamais visitée
        self.world.visit(key, label, list(exits.values()))
        if self._tour(frame, label, key.rsplit(" ", 1)[0], exits):
            return
        unexplored = [s for s in self._open_sides(key) if s in exits]
        preferred = [s for s in unexplored if s != self.came_from]  # pas de demi-tour tant qu'il y a autre chose
        if unexplored:
            self._go(frame, label, (preferred or unexplored)[0], "sortie jamais prise", exits)
            return
        side = self._toward_unexplored(key)
        if side is None:
            log.info("Exploration terminée : %d cartes connues (mémoire : %s)", len(self.world.maps), self.world.path)
            self.done = True
            return
        self._go(frame, label, side, "vers une carte qui a encore des sorties à essayer", exits)

    def _tour(self, frame: Frame, label: MapLabel, region: str, exits: dict) -> bool:
        """Tournée des cartes des références ; False si la région n'y est pas décrite (découverte à la place)."""
        maps = {m.coords: m for m in self.traveler.atlas.maps.values() if m.region == region}
        if not maps:
            return False
        here = self.traveler.coords
        if self.zone_only:
            if self.zone is None and here in maps:
                self.zone = maps[here].zone
                log.info("Exploration de la zone %s (%d cartes)", self.zone,
                         len(self.traveler.atlas.zone_maps(region, self.zone)))
            maps = {c: m for c, m in maps.items() if m.zone == self.zone}
        if here not in maps:
            self._go(frame, label, self.came_from, "carte absente des références (intérieur ?) : demi-tour", exits)
            return True
        self.visited.add(here)
        allowed = {c for c, m in maps.items() if not self.traveler.forbidden(c, m.zone, region)}
        todo = allowed - self.visited
        if not todo:
            avoided = sorted(set(maps) - allowed)
            log.info("Exploration terminée : %d cartes vues sur %d autorisées%s", len(self.visited & allowed), len(allowed),
                     f" (évitées : {' '.join(f'{x},{y}' for x, y in avoided)})" if avoided else "")
            self.done = True
            return True
        route = self._route(maps, allowed, here, todo, region)
        if route is None:
            log.warning("Exploration terminée : cartes inaccessibles d'ici : %s",
                        " ".join(f"{x},{y}" for x, y in sorted(todo)))
            self.done = True
            return True
        side, target = route
        self._go(frame, label, side, f"vers {target[0]},{target[1]}, encore {len(todo)} carte(s) à voir", exits)
        return True

    def _route(self, maps: dict, allowed: set, here: tuple[int, int], todo: set,
               region: str) -> tuple[str, tuple[int, int]] | None:
        """Premier pas et but du plus court chemin vers la carte pas encore vue la plus proche."""

        def links(current: tuple[int, int]) -> dict[str, tuple[int, int]]:
            return {side: dest for side, dest in self.traveler.atlas.links(region, current).items()
                    if self.world.destination(World.key(region, current), side) != BLOCKED}

        path = shortest_path(here, todo, links, lambda dest: dest in allowed and dest in maps)
        return (path[0][0], path[-1][1]) if path else None

    def _open_sides(self, key: str) -> list[str]:
        """Sorties jamais prises de cette carte, vers une carte qui n'est pas interdite."""
        exits = self.world.maps.get(key, {}).get("sorties", {})
        coords = _coords_of(key)
        sides = []
        for side in SIDES:
            if side not in exits or exits[side] is not None:
                continue
            neighbour = (coords[0] + DELTA[side][0], coords[1] + DELTA[side][1]) if coords else None
            region = key.rsplit(" ", 1)[0]
            known = self.world.maps.get(World.key(region, neighbour)) if neighbour else None
            if not self.traveler.forbidden(neighbour, (known or {}).get("zone"), region):
                sides.append(side)
        return sides

    def _go(self, frame: Frame, label: MapLabel, side: str | None, why: str, exits: dict | None = None) -> None:
        if side is None:
            self.done = True
            log.warning("Exploration : %s, mais je ne sais pas par où revenir", why)
            return
        log.info("Exploration : %s (%s)", side, why)
        if self.traveler.go(frame, label, side, exits) is not None:
            self.came_from = OPPOSITE[side]
            self.failures = 0
            return
        self.failures += 1
        if self.failures >= 5:
            log.warning("Exploration arrêtée : %d changements de carte ratés d'affilée", self.failures)
            self.done = True

    def _toward_unexplored(self, key: str) -> str | None:
        """Premier pas du plus court chemin (sorties connues, cartes autorisées) vers une carte de la région qui a
        encore une sortie autorisée jamais prise."""
        region = key.rsplit(" ", 1)[0]
        seen, queue = {key}, deque([(key, None)])
        while queue:
            current, first = queue.popleft()
            if current != key and self._open_sides(current):
                return first
            for side, dest in self.world.maps.get(current, {}).get("sorties", {}).items():
                if not dest or dest == BLOCKED or dest in seen or dest.rsplit(" ", 1)[0] != region:
                    continue
                if self.traveler.forbidden(_coords_of(dest), (self.world.maps.get(dest) or {}).get("zone"), region):
                    continue
                seen.add(dest)
                queue.append((dest, first or side))
        return None


class ZoneRoamer:
    """Mode zone (--zone) : combats dans la zone où se trouve le personnage au lancement ; quand la carte n'a plus
    de groupe attaquable, carte voisine de la même zone, jamais dans les cartes et zones interdites.

    La zone de chaque carte vient des références (config/monde, d'après ses coordonnées) : seules les sorties vers
    une carte de la zone sont prises, et le bot fait demi-tour s'il en sort quand même. Sans références pour la
    région, le nom de zone lu à l'écran sert à défaut. Parmi les cartes voisines de la zone : celle jamais vue,
    sinon celle vue il y a le plus longtemps, et pas de demi-tour s'il y a le choix."""

    def __init__(self, traveler: Traveler) -> None:
        self.traveler = traveler
        self.world = traveler.world
        self.zone_name: str | None = None
        self.leaving = "Plus de groupe ici"  # pourquoi on change de carte (journal) ; récolte : brain/loop.py
        self._failed_sides: dict[tuple[str | None, str], float] = {}  # (carte, côté) raté -> écarté jusqu'à
        self.came_from: str | None = None
        self.done = False
        self._warned = False
        self._searched: set[str] = set()  # cartes dont tous les soleils ont été cherchés à l'écran
        self._back: Journey | None = None  # trajet jusqu'à la zone quand on en est loin (_back_to_zone)
        # Hors de la zone (None : pas encore regardé) : pas de combat en chemin (24/10, retour d'Astrub vers les
        # Porkass : un groupe attaqué en 4,-20, cité d'Astrub, équipe transformée en citrouilles) ; voir locate.
        self._away: bool | None = None
        # Carte de la zone donnée au lancement (--combat-zone) : rendez-vous de l'équipe quand elle en est loin (26/10 :
        # principal ressuscité au Berceau, les 7 autres à Astrub ; ils prenaient le zaap vers lui, puis tous le zaap du
        # retour : 15 zaaps). Le principal y va par son trajet, chacun des autres par le sien (team/loop.py, _regroup).
        self.meeting: tuple[int, int] | None = None
        # Course hors de la zone (banque quand les pods sont pleins, team/loop.py) : trajet jusqu'à cette carte sans
        # combat, `errand_reached` à l'arrivée ; effacée ensuite (end_errand), retour dans la zone.
        self.errand: tuple[int, int] | None = None
        self.errand_reached = False
        self._errand_trip: Journey | None = None
        self._saved_meeting: tuple[int, int] | None = None
        info = traveler.here()  # coordonnées données au lancement : la sous-zone se lit dans les références
        if info is not None and info.zone:
            self.zone_name = info.zone
            self.meeting = info.coords
            maps = sorted(traveler.atlas.zone_maps(traveler.region, info.zone))
            log.info("Combat en zone : carte %s,%s de %s -> région %s, sous-zone %s (%d cartes : %s)",
                     *info.coords, traveler.region, info.area or traveler.region, info.zone, len(maps),
                     " ".join(f"{x},{y}" for x, y in maps))
        elif traveler.coords is not None:
            log.warning("Carte %s,%s absente des références de %s : sous-zone d'après le nom lu à l'écran",
                        *traveler.coords, traveler.region)

    @property
    def away(self) -> bool | None:
        """Hors de la zone ; aussi, en équipe, juste après un zaap du principal tant que ses suiveurs n'ont pas été
        envoyés le rejoindre (team/loop.py, _regroup, efface `teleported`) : pas de combat seul au zaap d'arrivée, même
        dans la zone. En équipe : `on_crossed` est posé par team/loop.py."""
        if getattr(self.traveler, "teleported", None) and getattr(self.traveler, "on_crossed", None) is not None:
            return True
        if self.errand is not None:
            return True  # en course (banque) : pas de combat en chemin
        return self._away

    @away.setter
    def away(self, value: bool | None) -> None:
        self._away = value

    def locate(self) -> bool:
        """Regarde la carte : True si elle est hors de la zone (ou interdite) ; met `away` à jour. Coordonnées
        illisibles : la position est demandée au jeu (24/10, 4,-20 au nom illisible : le bot s'est cru en -3,-30, la
        position donnée au lancement, dans la zone, et a attaqué en pleine cité d'Astrub)."""
        frame, label = self.traveler.look()
        if label.coords is None:
            asked = self.traveler.asker.ask() if hasattr(self.traveler, "asker") else None
            if asked is not None:
                self.traveler._locate(frame, label, asked)
                self.traveler._unread_steps = 0
        here_zone = self._zone_here(label)
        zone = self.zone_name or here_zone
        self.away = here_zone != zone or bool(self.traveler.forbidden(self.traveler.coords, here_zone))
        return self.away

    def start_errand(self, goal: tuple[int, int], why: str) -> None:
        """Course jusqu'à `goal` (banque) : pas de combat en chemin ; les suiveurs suivent le principal, pas le rendez-vous
        de la zone (gardé pour le retour)."""
        if self.errand is not None:
            return
        if self.zone_name is None:
            # 01/11 (journal 162522) : --zone sans position, pods pleins dès le premier tour, zone pas encore retenue ;
            # au retour, celle de la banque (cité d'Astrub) est devenue la zone de combat. Retenue ici d'abord.
            _, label = self.traveler.look()
            self.zone_name = self._zone_here(label)
            info = self.traveler.here()
            if self.meeting is None and info is not None and info.zone == self.zone_name:
                self.meeting = info.coords
            log.info("Zone de combat retenue avant la course : %s", self.zone_name or "inconnue")
        log.info("%s : trajet jusqu'à %s,%s, sans combat en chemin", why, *goal)
        self.errand, self.errand_reached, self._errand_trip = goal, False, None
        self._saved_meeting, self.meeting = self.meeting, None

    def end_errand(self) -> None:
        """Course finie : retour dans la zone (trajet jusqu'au rendez-vous, comme après une mort)."""
        log.info("Course finie : je retourne dans la zone %s", self.zone_name)
        self.errand, self.errand_reached, self._errand_trip = None, False, None
        self.meeting, self._saved_meeting = self._saved_meeting, None
        self._away = None
        self.came_from = None

    def _errand_step(self) -> None:
        if self.traveler.coords == self.errand:
            self.errand_reached = True
            return
        if self._errand_trip is None:
            self._errand_trip = Journey(self.traveler, self.errand)
        self._errand_trip.step()
        if self._errand_trip.done:
            if self.traveler.coords == self.errand:
                self.errand_reached = True
            else:
                log.warning("Pas de trajet jusqu'à %s,%s : course abandonnée", *self.errand)
                self.end_errand()
            self._errand_trip = None

    def _back_to_zone(self) -> None:
        """Hors de la zone sans côté par où revenir (lancé ailleurs, mort, téléporté) : trajet jusqu'à la carte de la
        zone la plus proche (Journey), une carte par appel. 24/10 : --combat-zone donné depuis Astrub, le bot est resté
        30 min sur place à répéter « je reviens »."""
        if self._back is None:
            here = self.traveler.coords
            maps = self.traveler.atlas.zone_maps(self.traveler.region, self.zone_name)
            if here is None or not maps:
                log.warning("Hors de la zone %s, et pas de trajet possible (position ou cartes de la zone inconnues) : "
                            "je m'arrête", self.zone_name)
                self.done = True
                return
            goal = self.meeting if self.meeting in maps else min(
                maps, key=lambda c: (abs(c[0] - here[0]) + abs(c[1] - here[1]), c))
            log.info("Hors de la zone %s : trajet jusqu'à %s,%s%s", self.zone_name, *goal,
                     " (rendez-vous de l'équipe)" if goal == self.meeting else ", sa carte la plus proche")
            self._back = Journey(self.traveler, goal)
        self._back.step()
        if self._back.done:
            if self.traveler.coords != self._back.goal:
                log.warning("Pas de trajet jusqu'à la zone %s : je m'arrête", self.zone_name)
                self.done = True
            self._back = None

    def _zone_here(self, label: MapLabel) -> str | None:
        info = self.traveler.here()
        return (info.zone if info else None) or label.zone_name

    def step(self) -> None:
        if self.errand is not None:
            self._errand_step()
            return
        frame, label = self.traveler.look()
        if self.traveler.jumped:
            if not self.done:
                log.warning("Personnage déplacé ailleurs (mort, téléportation ?) : je ne change plus de carte")
            self.done = True
            return
        here_zone = self._zone_here(label)
        if self.zone_name is None:
            if here_zone is None:
                if not self._warned:
                    self._warned = True
                    log.warning("Zone de la carte inconnue (coordonnées illisibles ?) : relance avec --position x,y")
                return
            self.zone_name = here_zone
            size = len(self.traveler.atlas.zone_maps(self.traveler.region, here_zone))
            log.info("Combats dans la zone %s%s (départ : %s)", here_zone, f", {size} cartes" if size else "",
                     label.describe())
        key = self.traveler.key(label)
        forbidden = self.traveler.forbidden(self.traveler.coords, here_zone)
        self.away = here_zone != self.zone_name or bool(forbidden)
        if self._away:
            if key:
                self.world.visit(key, label, [])
            if not self.came_from:
                self._back_to_zone()
                return
            log.info("Carte %s : je reviens", forbidden or f"hors de la zone {self.zone_name} ({here_zone or 'inconnue'})")
            if self.traveler.go(frame, label, self.came_from) is not None:
                self.came_from = None
            return
        self._back = None
        exits = self.traveler.exits(frame)
        if key:
            self.world.visit(key, label, list(exits.values()))
        described = bool(self.traveler.atlas.zone_maps(self.traveler.region, self.zone_name))

        def rank(side: str) -> tuple:
            dest = self.traveler.neighbour(side)
            dest_key = World.key(self.traveler.region, dest)
            dest_zone = self.traveler.atlas.zone(self.traveler.region, dest) or (self.world.maps.get(dest_key) or {}).get("zone")
            # Zone décrite dans les références : une carte voisine inconnue d'elles n'est pas dans la zone.
            out_of_zone = dest_zone != self.zone_name if described else (dest_zone is not None and dest_zone != self.zone_name)
            excluded = (self.world.destination(key, side) == BLOCKED or out_of_zone
                        or bool(self.traveler.forbidden_side(side))
                        or time.monotonic() < self._failed_sides.get((key, side), 0.0))
            return (excluded, side == self.came_from, self.world.last_seen(dest_key))

        choices = sorted((s for s in exits if not rank(s)[0]), key=rank)
        if not choices and key not in self._searched:
            # Seuls des soleils connus hors de la zone (03/10, Champs d'Astrub 9,-25 : seul le soleil du bas, vers
            # 9,-24, était retenu ; 7 minutes sur une carte vide) : tous les soleils cherchés à l'écran, touche A
            # comprise, une fois par carte.
            self._searched.add(key)
            log.info("Pas de sortie connue vers la zone %s : je cherche les autres soleils à l'écran", self.zone_name)
            exits = self.traveler.exits(frame, complete=True)
            if key:
                self.world.visit(key, label, list(exits.values()))
            choices = sorted((s for s in exits if not rank(s)[0]), key=rank)
        if not choices:
            log.info("Aucune sortie vers une autre carte de la zone %s : je reste", self.zone_name)
            return
        side = choices[0]
        log.info("%s : carte du %s (%s,%s, zone %s)", self.leaving, side, *self.traveler.neighbour(side), self.zone_name)
        if self.traveler.go(frame, label, side, exits) is not None:
            self.came_from = OPPOSITE[side]
        else:
            # 01/11 (journal 181347) : soleil du côté droit de -2,-28 caché sous un Frêne (le clic ouvre son menu),
            # passage officiel donc jamais noté bloqué : repris 40 fois, l'équipe bloquée 2 min deux fois. Côté écarté
            # FAILED_SIDE_FOR s : une autre sortie de la zone d'abord.
            self._failed_sides[(key, side)] = time.monotonic() + FAILED_SIDE_FOR
            log.info("Sortie du %s ratée : je prends une autre sortie de la zone pendant %.0f min", side,
                     FAILED_SIDE_FOR / 60)


FAILED_SIDE_FOR = 600.0  # s : côté d'une carte écarté du mode zone après un passage raté (ZoneRoamer.step)
# Itinéraire (Journey._cost) : passage jamais pris, et entrée dans une autre zone que celles du départ et de l'arrivée.
UNCONFIRMED_COST, ZONE_COST = 2.0, 2.0
# Référence complétée (data/references, tools/reference_complete.py : sorties StarLoco contrôlées sur les cartes
# officielles, 482 passages sur 482 d'accord avec ce que le bot a vu, 24/10) : passage de la référence, et côté sans
# aucune sortie connue d'une carte que la référence décrit.
REFERENCE_COST, NO_EXIT_COST = 1.2, 6.0
PASSAGES_DIR = PROJECT_DIR / "config" / "monde" / "passages"
REFERENCES_DIR = PROJECT_DIR / "data" / "references"
ANOMALIES = PROJECT_DIR / "data" / "anomalies_passages.jsonl"  # passages officiels ratés (Traveler._anomaly)


_REFERENCES: dict[str, tuple[tuple[int, int], dict]] = {}  # chemin -> (date et taille du fichier, contenu)


def _reference(path: Path) -> dict:
    """Référence complétée d'une région (13 Mo pour le Monde des Douze), lue une fois tant que le fichier ne change pas :
    chaque trajet de suiveur (team/loop.py, _regroup) la relisait (Journey), ~0,5 s à chaque fois. Lecture seule."""
    try:
        stat = path.stat()
    except OSError:
        return {}
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _REFERENCES.get(str(path))
    if cached is None or cached[0] != stamp:
        cached = _REFERENCES[str(path)] = (stamp, json.loads(path.read_text(encoding="utf-8")))
    return cached[1]


def _arena_area(reference: dict) -> Callable[[tuple[int, int], tuple[int, int] | None], set | None]:
    """Cases joignables à pied depuis une case d'une carte, d'après la carte officielle (brain/arena.walk_area, carte
    de combat de même identifiant que la carte extérieure de la référence) ; None si elle n'est pas connue. Sert quand
    la case d'arrivée n'est pas une de celles de `depuis_arrivee` (début de trajet, pied du zaap)."""
    from bot.brain.arena import Arenas, walk_area  # chargées seulement si un itinéraire en a besoin

    by_id: dict[str, object] = {}

    def area(coords: tuple[int, int], cell: tuple[int, int] | None) -> set | None:
        if not by_id:
            by_id.update({arena.ident: arena for arena in Arenas.load().arenas} or {"": None})
        ids = (reference.get(f"{coords[0]},{coords[1]}") or {}).get("ids") or ()
        found = [by_id[str(i)] for i in ids if by_id.get(str(i)) is not None]
        return walk_area(found[0], cell) if len(found) == 1 else None

    return area


def _truncated(read: tuple[int, int], expected: tuple[int, int]) -> bool:
    """`read` est `expected` amputé d'un dernier chiffre sur une coordonnée (l'autre identique) : « 0,-2 » pour 0,-29."""
    pairs = list(zip(read, expected))
    cut = [str(e)[:-1] == str(r) and abs(e) >= 10 for r, e in pairs]
    return any(cut) and all(c or r == e for c, (r, e) in zip(cut, pairs))


class Passages:
    """Référence des passages d'une région (config/monde/passages/<région>.json), construite à partir de ce que le bot a
    vu en jeu (tools/passages.py, depuis data/carte.json) et relue à la main : les cases des cartes du client sont
    chiffrées (docs/rapports/cles_cartes.md), on ne sait par où l'on passe qu'en y étant allé. Gardée à part de la
    mémoire, qu'une lecture ratée peut fausser (23/10 : 5,-30 droite notée « bloquée » à tort).

    Format : {"x,y": {"sorties": {côté: "x,y" | "bloquée"}, "bloquees_depuis": {côté d'arrivée: [côtés]}}}.

    `reference` : la référence complétée (data/references/<région>.json, locale, non versionnée ; sorties d'un
    émulateur contrôlées sur les cartes officielles : tools/reference_complete.py), au format {"x,y": {"sorties":
    {côté: {"vers": "x,y", "cases": [[i, j], ...]}}, "bloquees_depuis": {...}}} ; ce qui a été vu en jeu prime."""

    def __init__(self, maps: dict | None = None, reference: dict | None = None, region: str | None = None) -> None:
        self.maps = maps or {}
        self.reference = reference or {}
        self.region = region  # pour les coordonnées tirées de la langue du client (client_coords)

    @staticmethod
    def path(region: str | None) -> Path:
        slug = (region or "region").lower().replace(" ", "_")
        return PASSAGES_DIR / f"{slug}.json"

    @classmethod
    def load(cls, region: str | None) -> "Passages":
        path = cls.path(region)
        maps = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls(maps, _reference(REFERENCES_DIR / path.name), region)

    def coords_of(self, map_id: int) -> tuple[int, int] | None:
        """Coordonnées de la carte de numéro `map_id` (numéro du client, annoncé par le serveur : GDM), d'après le champ
        « ids » de la référence complétée, sinon d'après la langue du client (client_coords) ; None si elle n'est pas
        une carte extérieure de cette région."""
        index = getattr(self, "_ids", None)
        if index is None:
            index = self._ids = {}
            for key, info in self.reference.items():
                x, _, y = key.partition(",")
                if x.lstrip("-").isdigit() and y.lstrip("-").isdigit():
                    for ident in info.get("ids") or ():
                        index[int(ident)] = (int(x), int(y))
        found = index.get(map_id)
        return found if found is not None else client_coords(map_id, getattr(self, "region", None))


    def seen(self, coords: tuple[int, int]) -> dict[str, tuple[int, int] | str]:
        """Sorties vues en jeu (référence des passages)."""
        out = {}
        for side, dest in ((self.maps.get(f"{coords[0]},{coords[1]}") or {}).get("sorties") or {}).items():
            if dest == BLOCKED:
                out[side] = BLOCKED
            elif dest:
                where = _coords_of(f"x {dest}")
                if where is not None:
                    out[side] = where
        return out

    def referenced(self, coords: tuple[int, int]) -> dict[str, tuple[int, int]]:
        """Sorties de la référence complétée ; {} si elle ne décrit pas la carte."""
        out = {}
        for side, exit_ in ((self.reference.get(f"{coords[0]},{coords[1]}") or {}).get("sorties") or {}).items():
            where = _coords_of(f"x {exit_.get('vers', '')}")
            if where is not None:
                out[side] = where
        return out

    def describes(self, coords: tuple[int, int]) -> bool:
        return f"{coords[0]},{coords[1]}" in self.reference

    def exits(self, coords: tuple[int, int]) -> dict[str, tuple[int, int] | str]:
        """Sorties : celles vues en jeu, et celles de la référence complétée. Sur une carte qu'elle décrit, une sortie
        de la référence l'emporte sur un « bloquée » appris (24/10, utilisateur : l'apprentissage peut se tromper ; 5
        sorties notées bloquées que StarLoco et la carte officielle donnent praticables)."""
        out = dict(self.seen(coords))
        for side, dest in self.referenced(coords).items():
            if out.get(side) in (None, BLOCKED):
                out[side] = dest
        return out

    def arrival(self, coords: tuple[int, int], side: str, cell: tuple[int, int] | None) -> tuple[int, int] | None:
        """Case d'arrivée sur la carte voisine en prenant le soleil `cell` du côté `side` de la carte `coords`."""
        exit_ = ((self.reference.get(f"{coords[0]},{coords[1]}") or {}).get("sorties") or {}).get(side) or {}
        found = (exit_.get("arrivees") or {}).get(f"{cell[0]},{cell[1]}") if cell else None
        return tuple(found) if found else None

    def reachable(self, coords: tuple[int, int], side: str, arrival: tuple[int, int] | None) -> list | None:
        """Soleils du côté `side` joignables à pied depuis la case d'arrivée `arrival` ([] : aucun, côté inatteignable
        d'ici) ; None si la référence ne le sait pas."""
        if arrival is None:
            return None
        by_cell = (self.reference.get(f"{coords[0]},{coords[1]}") or {}).get("depuis_arrivee") or {}
        cells = (by_cell.get(f"{arrival[0]},{arrival[1]}") or {}).get(side)
        return None if cells is None else [tuple(c) for c in cells]

    def sun_cell(self, coords: tuple[int, int], side: str, entry: str | None = None,
                 arrival: tuple[int, int] | None = None, then: str | None = None) -> tuple[int, int] | None:
        """Case du soleil à cliquer d'un côté d'après la référence complétée : celle qu'on peut joindre à pied depuis
        la case d'arrivée (`arrival`, soleil pris sur la carte d'avant), sinon depuis le côté d'arrivée (`entry`)
        (calculé sur les cases praticables officielles : jamais un soleil de l'autre côté d'une muraille, 24/10) ;
        sinon le seul soleil du côté ; sinon None (la recherche à l'écran choisit).

        `then` : côté à prendre sur la carte voisine (itinéraire). Entre plusieurs soleils joignables, celui qui y
        amène sur une case d'où ce côté se joint (24/10 : en 1,-14, le soleil gauche du bas mène en 0,-14 sous le
        rempart, d'où la sortie gauche est hors d'atteinte ; celui du haut, non)."""
        exit_ = ((self.reference.get(f"{coords[0]},{coords[1]}") or {}).get("sorties") or {}).get(side) or {}
        cells = self.reachable(coords, side, arrival)             or [tuple(c) for c in ((exit_.get("cases_depuis") or {}).get(entry) or [] if entry else [])]
        if not cells:
            cells = [tuple(c) for c in exit_.get("cases") or []][:1] if len(exit_.get("cases") or []) == 1 else []
        dest = _coords_of(f"x {exit_.get('vers', '')}")
        if then and dest is not None and len(cells) > 1:
            onward = [c for c in cells if self.reachable(dest, then, self.arrival(coords, side, c))]
            if onward:
                return onward[0]
        return cells[0] if cells else None

    def blocked_from(self, coords: tuple[int, int], entry: str | None) -> set[str]:
        if not entry:
            return set()
        key = f"{coords[0]},{coords[1]}"
        computed = ((self.reference.get(key) or {}).get("bloquees_depuis") or {}).get(entry) or []
        if self.describes(coords):
            return set(computed)  # calculé sur les cases praticables officielles : prime sur l'appris
        return set(((self.maps.get(key) or {}).get("bloquees_depuis") or {}).get(entry) or [])


# Continent de chaque région (« sua » de la table MA de la langue du client).
SUPER_AREAS = {WORLDS["douze"]: 0, WORLDS["incarnam"]: 3}


def client_coords(map_id: int, region: str | None, maps: dict | None = None) -> tuple[int, int] | None:
    """Coordonnées d'une carte d'après la langue du client (table MA : x, y, sous-zone, extérieur), pour les cartes
    absentes de la référence (31/10 : 416 cartes extérieures du continent, aucun désaccord sur les 4861 communes).
    Seulement une carte extérieure du continent de `region` : une salle de donjon ou un intérieur porte les mêmes
    coordonnées que la carte du dehors (salles du donjon Bouftou en 2,-34 comme l'entrée) ; None sinon, ou sans la
    langue du client."""
    if region not in SUPER_AREAS:
        return None
    if maps is None:
        maps = _client_maps()
    table = maps.get("m", {}).get(str(map_id))
    if not table or not table.get("o") or table.get("d"):
        return None
    subarea = maps.get("sa", {}).get(str(table.get("sa")), {})
    area = maps.get("a", {}).get(str(subarea.get("a")), {})
    if area.get("sua") != SUPER_AREAS[region]:
        return None
    return int(table["x"]), int(table["y"])


def _client_maps() -> dict:
    try:
        from tools.langue_client import LangueClient  # import local : langue du client (data/client_retro)

        return LangueClient().table("MA")
    except (OSError, KeyError, ValueError):
        log.debug("Langue du client illisible : cartes hors de la référence inconnues", exc_info=True)
        return {}


class Journey:
    """Mode aller (--aller x,y) : jusqu'à la carte d'arrivée par le plus court chemin, sans combat.

    Itinéraire (en nombre de cartes) sur les liaisons connues : ce que le bot a vu (data/carte.json : carte où mène
    chaque sortie déjà prise, sorties bloquées) prime sur les références (config/monde : liaisons des soleils
    d'Incarnam ; Monde des Douze, décrit sans soleils : les cartes voisines qui existent, passage à confirmer en
    jeu). Jamais par une carte ou une zone interdite (travel.avoid_maps / avoid_zones). Une carte à la fois, par le
    Traveler (soleil de la mémoire ou des références, sinon cherché à l'écran, touche A si besoin). Nouvel itinéraire
    quand le personnage arrive ailleurs que prévu (liaison différente des références, mort, téléportation) ou qu'une
    sortie est introuvable (le Traveler la note bloquée). Arrêt à l'arrivée, ou avec un message si aucun chemin.

    25/10 : sur une carte que la référence complétée décrit, itinéraire COMPLET calculé sur elle seule avant de partir
    (brain/itinerary.py : état (carte, case d'arrivée), soleil à cliquer à chaque étape, zaap si aucun chemin à pied),
    suivi à l'estime (_walk). Passage raté : les autres soleils du même côté, puis « /p » et nouvel itinéraire
    (_missed)."""

    def __init__(self, traveler: Traveler, goal: tuple[int, int]) -> None:
        self.traveler = traveler
        self.world = traveler.world
        self.goal = goal
        self.done = False
        self.path: list[tuple[str, tuple[int, int]]] | None = None  # reste de l'itinéraire : (côté, carte atteinte)
        self.at: tuple[int, int] | None = None  # carte d'où part `path` (None : pas encore d'itinéraire)
        self.crossed = 0  # changements de carte réussis
        self.failures = 0  # changements de carte ratés d'affilée
        self.reroutes = 0  # itinéraires recalculés
        self.passages = Passages.load(traveler.region)  # passages vus en jeu, gardés (config/monde/passages)
        self._zones_ok: set[str | None] = set()  # zones du départ et de l'arrivée (_cost)
        # Itinéraire complet de la référence (25/10) : étapes (carte, soleil, carte suivante, case d'arrivée) ; None :
        # pas d'itinéraire de la référence (carte qu'elle ne décrit pas : liaisons apprises, `path`).
        self.legs: list[Leg] | None = None
        self.avoid: set[tuple[tuple[int, int], str | None]] = set()  # (carte, côté) ratés pendant ce trajet
        self.refused: set[tuple[int, int]] = set()  # zaaps d'arrivée impossibles (pas connus du personnage)
        self._planner: Planner | None = None

    @property
    def planner(self) -> Planner:
        """Itinéraires sur la référence complétée (brain/itinerary.py), cartes interdites exclues."""
        if self._planner is None:
            region = self.traveler.region
            zaaps = {}
            for place in getattr(self.traveler.atlas, "places", ()):
                coords = _coords_of(f"x {place.get('carte', '')}")
                name = place.get("nom") or ""
                # Nom à taper dans la liste des zaaps : un nom générique (« Zaap », Incarnam) ne désigne aucune ligne.
                if place.get("type") == "zaap" and place.get("region") == region and coords is not None \
                        and name and name.lower() != "zaap":
                    zaaps[coords] = name
            reference = self.passages.reference
            self._planner = Planner(reference, self._allowed, _arena_area(reference), zaaps)
        return self._planner

    def _leave_building(self) -> bool:
        """Dans un bâtiment d'après le serveur : en sortir (True s'il a fallu sortir et que c'est fait)."""
        from bot.brain.building import is_building, leave_building

        game = self.traveler.game
        net = getattr(game, "net", None)
        if net is None or not net.alive() or not is_building(getattr(net, "map_id", None)):
            return False
        hide = getattr(self.traveler.settings, "hide_players_key", "") or ""
        name = getattr(getattr(game, "window", None), "title", "").split(" - ")[0]
        return leave_building(game, net, hide, name)

    def step(self) -> None:
        frame, label = self.traveler.look()
        here, region = self.traveler.coords, self.traveler.region
        if here is None:
            self._stop("position de départ inconnue (coordonnées illisibles, chiffre pas encore appris) : relance avec "
                       "--position x,y (les coordonnées affichées en haut à gauche du jeu)")
            return
        if self.at is None:
            log.info("Trajet vers %s,%s (%s), sans combat ; départ : %s,%s (%s)", *self.goal, region, *here,
                     label.describe())
            # 08/11 : parti d'un bâtiment (taverne, maison...) d'après le serveur : sorti d'abord par ses soleils
            # (brain/building.py) ; les soleils de la carte de dehors n'existent pas dedans (07/11, « aucun chemin »).
            if self._leave_building():
                frame, label = self.traveler.look()
                here = self.traveler.coords
            if not any(m.suns for m in self.traveler.atlas.maps.values() if m.region == region) \
                    and not self.passages.reference:  # référence complétée : cases des soleils connues
                log.info("%s : références sans soleils : chaque sortie est cherchée à l'écran (touche %s si besoin) ; "
                         "une sortie introuvable est notée bloquée et l'itinéraire recalculé", region,
                         self.traveler.settings.show_exits_key.upper())
        key = self.traveler.key(label)
        if key:
            self.world.visit(key, label, [])  # zone (cartes interdites) et dernier passage, comme les autres modes
        if self.legs is not None and self.legs and here != self.legs[0].coords:
            log.warning("Carte %s,%s au lieu de %s,%s%s : nouvel itinéraire", *here, *self.legs[0].coords,
                        " (personnage déplacé ailleurs : mort, téléportation ?)" if self.traveler.jumped else "")
            self.legs = None
        elif self.path is not None and here != self.at:
            log.warning("Carte %s,%s au lieu de %s,%s%s : nouvel itinéraire", *here, *self.at,
                        " (personnage déplacé ailleurs : mort, téléportation ?)" if self.traveler.jumped else "")
            self.path = None
        self.traveler.jumped = False  # le trajet repart de là où l'on est
        if here == self.goal:
            log.info("Arrivé en %s,%s%s", *here, f" ({self.crossed} changement(s) de carte)" if self.crossed
                     else " : déjà sur la carte d'arrivée")
            self.done = True
            return
        if not self.legs and self.path is None and not self._plan(here):
            return
        if self.legs:
            self._walk(frame, label)
            return
        side, dest = self.path[0]
        log.info("Trajet : côté %s, vers %s,%s (encore %d carte(s) jusqu'à %s,%s)", side, *dest, len(self.path),
                 *self.goal)
        self.traveler.next_side = self.path[1][0] if len(self.path) > 1 else None
        arrived = self.traveler.go(frame, label, side)
        self.traveler.next_side = None
        if arrived is None:
            # Sortie introuvable ou clic sans effet (le Traveler la note bloquée), ou combat : nouvel itinéraire.
            self.path = None
            self.failures += 1
            if self.failures >= JOURNEY_FAILURES:
                self._stop(f"{self.failures} changements de carte ratés d'affilée")
            return
        self.failures = 0
        self.crossed += 1
        self.path.pop(0)
        self.at = dest  # vérifié au pas suivant, sur les coordonnées lues

    def _walk(self, frame: Frame, label: MapLabel) -> None:
        """Étape suivante de l'itinéraire de la référence, à l'estime : clic sur la case prévue (pas de recherche à
        l'écran, pas de touche A), passage confirmé par le décor, carte d'arrivée seulement contrôlée (Traveler)."""
        leg = self.legs[0]
        traveler = self.traveler
        if leg.zaap:
            arrived = traveler.teleport(leg) if traveler.settings.zaap else None
        else:
            # « Trajet : côté X, vers x,y » : comme avant (tools/bilan_sessions.py, temps d'un changement de carte).
            log.info("Trajet : côté %s, vers %s,%s : soleil %s%s%s (encore %d carte(s) jusqu'à %s,%s)", leg.side, *leg.dest,
                     leg.cell, f", arrivée {leg.arrival}" if leg.arrival is not None else "",
                     "" if leg.sure else ", à confirmer", len(self.legs), *self.goal)
            traveler.next_side = self.legs[1].side if len(self.legs) > 1 else None
            traveler.plan_cells = [leg.cell, *leg.others]
            try:
                arrived = traveler.go(frame, label, leg.side)
            finally:
                traveler.plan_cells, traveler.next_side = None, None
        if arrived is None:
            if getattr(traveler, "interrupted", False):
                self.legs = None  # combat en chemin : itinéraire recalculé après, d'où l'on sera
                return
            self.failures += 1
            if self.failures >= JOURNEY_FAILURES:
                self._stop(f"{self.failures} changements de carte ratés d'affilée")
                return
            self._missed(leg)
            return
        self.failures = 0
        self.crossed += 1
        here = traveler.coords
        if here != leg.dest:
            self.legs = None  # vérifié par « /p » (Traveler._contradicted) : ailleurs que prévu, nouvel itinéraire
            return
        self.legs.pop(0)
        self.at = here
        arrival = getattr(traveler, "arrival_cell", None)
        if self.legs and arrival != leg.arrival:
            # Autre soleil pris que celui prévu (secours du même côté) : autre case d'arrivée, peut-être de l'autre
            # côté d'un muret ; la suite est recalculée depuis elle.
            log.info("Arrivé en %s,%s sur la case %s (prévue : %s) : itinéraire recalculé depuis elle", *here, arrival,
                     leg.arrival)
            self.legs = None

    def _missed(self, leg: Leg) -> None:
        """Passage (ou zaap) raté, autres soleils du même côté compris : la position est vérifiée par « /p » ; ailleurs,
        nouvel itinéraire depuis là ; sinon ce passage est évité pour ce trajet. Jamais d'errance."""
        traveler = self.traveler
        self.legs = None
        if leg.zaap:
            # 31/10 : Pesopepe et Walitcoin, cru sur la carte du zaap (1,-32) alors qu'ils étaient ailleurs : zaap
            # « raté », puis les zaaps des Craqueleurs, de Madrestam, de Cania essayés depuis la même mauvaise carte.
            # La carte d'après le serveur d'abord (sinon « /p ») : ailleurs, nouvel itinéraire depuis là ; bien sur la
            # carte du zaap, trajet arrêté (tous les zaaps partent du même : en essayer un autre n'y changerait rien).
            told = traveler.told_coords() if hasattr(traveler, "told_coords") else None
            asker = getattr(traveler, "asker", None)
            here = told or (asker.ask(force=True) if asker is not None else None)
            if here is not None and here != traveler.coords:
                log.warning("Zaap vers %s raté : le jeu me donne en %s,%s, pas en %s,%s ; nouvel itinéraire depuis là",
                            leg.zaap, *here, *traveler.coords)
                traveler.coords, traveler.doubt = here, False
                traveler.arrival_cell = traveler.entry = None
                return
            self.refused.add(leg.dest)
            self._stop(f"zaap vers {leg.zaap} raté depuis {leg.coords[0]},{leg.coords[1]} (les autres zaaps partent "
                       f"du même : pas d'autre essai)")
            return
        asker = getattr(traveler, "asker", None)
        asked = asker.ask(force=True) if asker is not None else None
        if asked is not None and asked != traveler.coords:
            log.warning("Passage du %s raté : le jeu me donne en %s,%s, pas en %s,%s ; nouvel itinéraire depuis là",
                        leg.side, *asked, *traveler.coords)
            traveler.coords, traveler.doubt = asked, False
            traveler.arrival_cell = traveler.entry = None
            return
        log.warning("Passage du %s de %s,%s raté (soleils %s)%s : évité pour ce trajet, nouvel itinéraire", leg.side,
                    *leg.coords, ", ".join(str(c) for c in (leg.cell, *leg.others)),
                    " ; position confirmée par le jeu" if asked is not None else "")
        self.avoid.add((leg.coords, leg.side))
        traveler.arrival_cell = None  # il a pu marcher vers le soleil : sa case n'est plus celle d'arrivée

    def _plan(self, here: tuple[int, int]) -> bool:
        """Calcule l'itinéraire depuis `here` et le journalise ; False (et arrêt) s'il n'y en a pas.

        Carte décrite par la référence complétée : itinéraire complet sur elle seule (soleil par soleil, case d'arrivée
        comprise), zaap seulement si aucun chemin à pied n'existe. Sinon : liaisons apprises et références (avant)."""
        x, y = self.goal
        why = self._forbidden(self.goal)
        if why:
            self._stop(f"la carte d'arrivée {x},{y} est interdite ({why} : réglages travel.avoid_maps / avoid_zones)")
            return False
        if not self._known(self.goal):
            self._stop(f"carte {x},{y} inconnue de {self.traveler.region} (absente de config/monde et jamais vue) : "
                       "vérifie les coordonnées et --monde")
            return False
        if self.at is not None:
            self.reroutes += 1
            if self.reroutes > JOURNEY_REROUTES:
                self._stop(f"plus de {JOURNEY_REROUTES} itinéraires recalculés (liaisons qui se contredisent ?)")
                return False
        if self.passages.reference and self.planner.describes(here):
            return self._plan_reference(here)
        zone = self.traveler.atlas.zone
        self._zones_ok = {zone(self.traveler.region, here), zone(self.traveler.region, self.goal)}
        path = shortest_route(here, getattr(self.traveler, "entry", None), {self.goal},
                              lambda coords, entry: self._links(coords, entry=entry), self._allowed, self._cost)
        if path is None:
            self._stop(f"aucun chemin de {here[0]},{here[1]} à {x},{y} : {self._no_path(here)}")
            return False
        log.info("%s de %s,%s à %s,%s : %d carte(s) : %s", "Itinéraire" if self.at is None else "Nouvel itinéraire",
                 *here, x, y, len(path), " ".join(f"{cx},{cy}" for _, (cx, cy) in path))
        self.path, self.at = path, here
        return True

    def _plan_reference(self, here: tuple[int, int]) -> bool:
        x, y = self.goal
        cell = getattr(self.traveler, "arrival_cell", None)
        legs = self.planner.route(here, cell, {self.goal}, self.avoid)
        zaap = False
        if legs is None and self.traveler.settings.zaap:
            legs = self.planner.route(here, cell, {self.goal}, self.avoid, zaap=True, refused=self.refused)
            zaap = legs is not None
        if legs is None:
            avoided = f" (passages ratés évités : {', '.join(f'{c[0]},{c[1]} {s}' for c, s in sorted(self.avoid))})" \
                if self.avoid else ""
            self._stop(f"aucun chemin de {here[0]},{here[1]} à {x},{y} d'après la référence{avoided} : "
                       f"{self._no_path(here)}" + ("" if self.traveler.settings.zaap else " (zaap désactivé : "
                                                    "travel.zaap)"))
            return False
        log.info("%s de %s,%s à %s,%s : %d carte(s)%s%s : %s", "Itinéraire" if self.at is None else "Nouvel itinéraire",
                 *here, x, y, len(legs), f" depuis la case {cell}" if cell is not None else "",
                 " par zaap (aucun chemin à pied)" if zaap else "", " ; ".join(leg.describe() for leg in legs))
        self.legs, self.at, self.path = legs, here, None
        return True

    def _links(self, coords: tuple[int, int], blocked: bool = True,
               entry: str | None = None) -> dict[str, tuple[int, int]]:
        """Côté -> carte voisine : ce que le bot a vu (data/carte.json) prime sur les références. `blocked` : sans
        les sorties notées bloquées (pour tous, ou pour qui arrive sur la carte par le côté `entry`)."""
        region, atlas = self.traveler.region, self.traveler.atlas
        if atlas.get(region, coords) is not None:
            links = atlas.links(region, coords)
        else:  # carte absente des références (intérieur, oubli) : ses voisines qui y sont, à confirmer en jeu
            links = {side: (coords[0] + dx, coords[1] + dy) for side, (dx, dy) in DELTA.items()
                     if atlas.get(region, (coords[0] + dx, coords[1] + dy)) is not None}
        for side, dest in self.passages.exits(coords).items():  # référence des passages, puis la mémoire
            if dest == BLOCKED:
                if blocked:
                    links.pop(side, None)
            else:
                links[side] = dest
        referenced = self.passages.referenced(coords)
        if self.passages.describes(coords):
            # 25/10 (journal 201109) : la référence fait foi. Un côté sans sortie dans la référence (vérifiée sur les
            # cartes officielles) n'est pas un passage « à confirmer » : en bas de 1,-29, à gauche de -2,-30 et -2,-31,
            # l'itinéraire passait par des sorties qui n'existent pas, cherchées puis notées bloquées l'une après
            # l'autre (11 min sans atteindre les Blops). Restent ses sorties et celles déjà prises en jeu.
            seen = {s for s, d in self.passages.seen(coords).items() if d != BLOCKED}
            taken = {s for s, d in ((self.world.maps.get(World.key(region, coords)) or {}).get("sorties") or {}).items()
                     if d and d != BLOCKED}
            for side in [s for s in links if s not in referenced and s not in seen and s not in taken]:
                links.pop(side)
        for side, dest in (self.world.maps.get(World.key(region, coords)) or {}).get("sorties", {}).items():
            if dest == BLOCKED:
                if blocked and side not in referenced:  # la référence complétée prime sur un « bloquée » appris
                    links.pop(side, None)
            elif dest:
                where = _coords_of(dest)
                if where is not None and dest.rsplit(" ", 1)[0] == region:
                    links[side] = where
                else:
                    links.pop(side, None)  # mène hors de la région
        if blocked:
            # Après la mémoire : 23/10, en 5,-30, la sortie de droite bloquée pour ce trajet revenait par le lien
            # appris en arrivant (6,-30 gauche -> 5,-30) ; 3 essais d'affilée sans un clic, trajet arrêté.
            for key, side in getattr(self.traveler, "blocked_here", ()):
                if key == World.key(region, coords):
                    links.pop(side, None)  # bloquée pour ce trajet seulement (carte pas sûre)
            # Inatteignables selon le côté d'arrivée (référence : calculé sur la carte officielle), après la mémoire qui
            # remettait le lien appris (24/10, -1,-21 : bas inatteignable en venant du haut, pris quand même).
            for side in self.passages.blocked_from(coords, entry):
                links.pop(side, None)
            if not self.passages.describes(coords):  # sinon : calculé sur la carte officielle (blocked_from)
                for side in self.world.blocked_from(World.key(region, coords), entry):
                    links.pop(side, None)  # inatteignable quand on arrive par ce côté (rempart)
        return links

    def _cost(self, coords: tuple[int, int], side: str, dest: tuple[int, int]) -> float:
        """Coût d'un passage pour l'itinéraire : 1 s'il est confirmé (déjà pris : mémoire ou référence des passages) ;
        sinon UNCONFIRMED_COST, et ZONE_COST de plus pour entrer dans une zone qui n'est ni celle du départ ni celle
        de l'arrivée. 23/10 : en ligne droite de 11,-28 vers -3,-29, le bot est entré dans Le berceau (plateau fermé, 5,-30
        sans issue vers l'ouest) par des passages jamais vus, plutôt que par les Champs déjà parcourus."""
        region = self.traveler.region
        dest_key = World.key(region, dest)
        if self.world.destination(World.key(region, coords), side) == dest_key \
                or self.passages.seen(coords).get(side) == dest:
            return 1.0
        if self.passages.referenced(coords).get(side) == dest:
            return REFERENCE_COST
        zone = self.traveler.atlas.zone
        here_zone, dest_zone = zone(region, coords), zone(region, dest)
        unknown = NO_EXIT_COST if self.passages.describes(coords) else UNCONFIRMED_COST
        return unknown + (ZONE_COST if dest_zone != here_zone and dest_zone not in self._zones_ok else 0.0)

    def _known(self, coords: tuple[int, int]) -> bool:
        """Carte des références, ou déjà vue."""
        region = self.traveler.region
        return self.traveler.atlas.get(region, coords) is not None or World.key(region, coords) in self.world.maps

    def _forbidden(self, coords: tuple[int, int]) -> str | None:
        """Carte interdite d'après sa zone dans les références, ou celle lue lors d'un passage."""
        region = self.traveler.region
        seen = (self.world.maps.get(World.key(region, coords)) or {}).get("zone")
        return (self.traveler.forbidden(coords, None, region)
                or (self.traveler.forbidden(coords, seen, region) if seen else None))

    def _allowed(self, coords: tuple[int, int]) -> bool:
        return self._known(coords) and not self._forbidden(coords)

    def _no_path(self, here: tuple[int, int]) -> str:
        """Pourquoi aucun chemin : sorties notées bloquées, cartes interdites, ou aucune liaison connue."""
        region = self.traveler.region
        path = shortest_path(here, {self.goal}, lambda c: self._links(c, blocked=False), self._allowed)
        if path is not None:
            blocked, at = [], here
            for side, dest in path:
                if self.world.destination(World.key(region, at), side) == BLOCKED:
                    blocked.append(f"{at[0]},{at[1]} côté {side}")
                at = dest
            if not blocked:
                # 25/10 : le Berceau de Taïnéla, plateau fermé ; ses sorties ne se joignent pas depuis l'intérieur
                # (murets, falaises de la carte officielle : référence complétée). On n'en sort que par le zaap.
                return ("aucune sortie à pied ne se joint d'ici d'après la carte officielle (zone fermée par des murets "
                        "ou des falaises) : il faut un zaap ou une potion de rappel")
            return (f"il passe par des sorties notées « {BLOCKED} » ({', '.join(blocked)}) dans "
                    f"{self.world.path or 'la mémoire des cartes'} : remplace-les par null si le passage existe")
        if shortest_path(here, {self.goal}, self._links, self._known) is not None:
            return "il passe par des cartes ou zones interdites (travel.avoid_maps / avoid_zones)"
        return "aucune liaison connue entre ces deux cartes (île, autre région ? vérifie --monde)"

    def _stop(self, why: str) -> None:
        log.warning("Trajet vers %s,%s arrêté : %s", *self.goal, why)
        self.done = True


class Waypoints:
    """Mode aller par étapes (--etapes) : chaque carte rejointe à son tour (Journey). Une étape ratée est dite et
    sautée : la suite continue (24/10 : vérification en jeu de la référence complétée, une paire de cartes voisines
    par passage à vérifier)."""

    def __init__(self, traveler: Traveler, goals: list[tuple[int, int]]) -> None:
        self.traveler = traveler
        self.goals = list(goals)
        self.reached: list[tuple[int, int]] = []
        self.missed: list[tuple[int, int]] = []
        self.done = False
        self.journey: Journey | None = None

    def step(self) -> None:
        if self.journey is None:
            if not self.goals:
                log.info("Étapes : %d rejointes, %d ratées%s", len(self.reached), len(self.missed),
                         f" ({' '.join(f'{x},{y}' for x, y in self.missed)})" if self.missed else "")
                self.done = True
                return
            goal = self.goals.pop(0)
            log.info("Étape suivante : %s,%s (encore %d après)", *goal, len(self.goals))
            self.journey = Journey(self.traveler, goal)
        self.journey.step()
        if self.journey.done:
            goal = self.journey.goal
            (self.reached if self.traveler.coords == goal else self.missed).append(goal)
            if self.traveler.coords != goal:
                log.warning("Étape %s,%s ratée : je passe à la suivante", *goal)
            self.journey = None


def make_roamer(game: Game, settings: TravelSettings) -> RoundTrip | Explorer | ZoneRoamer | Journey | None:
    if not settings.mode:
        return None
    path = Path(settings.memory)
    world = World(path if path.is_absolute() else PROJECT_DIR / path)
    traveler = Traveler(game, settings, world, Atlas.load())
    if settings.mode == "test":
        return RoundTrip(traveler, settings.test_steps)
    if settings.mode == "explore":
        return Explorer(traveler, zone_only=settings.region == WORLDS["douze"])
    if settings.mode == "aller" and settings.waypoints:
        return Waypoints(traveler, list(settings.waypoints))
    if settings.mode == "aller":
        return Journey(traveler, settings.destination)
    return ZoneRoamer(traveler)
