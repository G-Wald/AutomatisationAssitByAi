"""Changer de carte par les soleils du bord, et savoir où l'on est.

Repérage : les coordonnées écrites en haut à gauche (perception/maplabel.py). La région n'est pas lue : c'est un
réglage (travel.region : Incarnam, ou plus tard Monde des Douze), car le bot reste dans l'une ou dans l'autre et
Incarnam a ses propres coordonnées (Incarnam 0,0 n'est pas la carte 0,0 du Monde des Douze).
Quand un chiffre n'est pas encore appris, les coordonnées sont déduites de la carte précédente et du côté
emprunté (droite : x + 1, bas : y + 1...), et la capture est gardée pour apprendre ce chiffre.

Sortie : d'abord les soleils visibles sans rien faire ; si celui du côté voulu n'est pas vu, un appui sur A les
entoure de blanc (perception/exits.py). Un clic sur le soleil fait marcher le personnage jusqu'à la carte
voisine ; on attend que le nom de la carte change.

Mémoire (data/carte.json) : pour chaque carte, sa zone, son dernier passage, la case de ses soleils et ce que
donne chaque sortie (carte voisine, « bloquée »), d'une session à l'autre. Références (config/monde, voir
brain/atlas.py) : cases des soleils, carte où mène chaque côté et zone de chaque carte, avant même d'y être passé.
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
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from bot.brain.atlas import Atlas, MapInfo
from bot.brain.game import Game
from bot.debug.draw import annotate
from bot.perception.exits import DELTA, OPPOSITE, SAME_SUN, SIDES, Exit, find_marked_exits, find_suns
from bot.perception.grid import distance
from bot.perception.frame import Frame
from bot.perception.maplabel import MapLabel, learn_digits, read_map_label, unreadable_label
from bot.perception.popups import find_context_menu, find_known_popup, load_popup_specs
from bot.state.scene import Phase, read_scene

log = logging.getLogger(__name__)

BLOCKED = "bloquée"
MENU = "menu"  # le clic a ouvert le menu d'un personnage ou d'un monstre posé sur le soleil
# Toujours sur la carte de départ, décor inchangé : le soleil est recliqué toutes les RECLICK_EVERY s (un personnage passait
# dessus, le clic l'a visé lui : 03/10, 12 s perdues). En marche, recliquer la même case ne change rien.
RECLICK_EVERY = 3.0
# Écart moyen (niveaux de gris, 0-255) entre deux vues réduites de la carte. Mesuré le 28/09 : 0,1 à 0,3 sur une
# même carte pendant que le personnage marche, 28 à 37 entre deux cartes voisines.
SCENE_CHANGED, SCENE_STABLE = 12.0, 3.0
# Soleil inatteignable vu en quelques secondes (11/10, l'utilisateur : 60 s perdues à chaque mur des remparts) : le
# personnage ne bouge plus et n'a pas changé de carte. Mouvement : vue réduite de la carte, autres joueurs cachés
# (touche E tenue) ; une case qui change de plus de MOTION_LEVEL niveaux de gris entre deux captures (0,4 s) bouge.
STILL = "immobile"
MOTION_SIZE, MOTION_LEVEL = (160, 90), 12.0
# Nom de carte illisible (20 % des arrivées dans la cité, bilan du 12/10) : la position déduite du chemin reste sûre
# SURE_UNREAD cartes au plus après une carte lue, sans saut. Avant, rien n'était appris sur ces cartes : la sortie du
# haut de 4,-22 (aucun soleil) était redécouverte à chaque trajet.
SURE_UNREAD = 2
WALKED_POLLS = 3  # captures (0,4 s) où il bougeait : il a marché (pas seulement l'affichage des autres joueurs)
CANDIDATE_TRIES = 2  # clics sur un même soleil (le second au bas de la case) avant de passer au soleil suivant
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
        # Faux pour le trajet d'un suiveur qui rejoint le principal (team/loop.py) : rien d'écrit dans la mémoire
        # partagée (sa position est souvent déduite du chemin, pas lue).
        self.learns = True
        self._last_label: MapLabel | None = None
        # Sorties bloquées notées pour ce trajet seulement (carte pas sûre : pas écrites dans data/carte.json), pour
        # que l'itinéraire soit quand même recalculé (Journey._links).
        self.blocked_here: set[tuple[str | None, str]] = set()
        # Côté par lequel on est arrivé sur cette carte, et case d'arrivée estimée (en face du soleil pris) ; None au
        # début du trajet. Sur une carte coupée par un rempart, il dit de quel côté du mur on est.
        self.entry: str | None = None
        self.entry_cell: tuple[int, int] | None = None
        self.candidates: dict[str, list[Exit]] = {}  # soleils vus de chaque côté à la dernière recherche à l'écran
        self._hiding = False  # touche E déjà tenue : un clic sur un soleil ne la réappuie pas
        self._walked = False  # au dernier STILL : il avait marché avant de s'arrêter
        self._unread_steps = 0  # cartes d'affilée au nom illisible depuis la dernière carte lue (ou la position donnée)

    def look(self) -> tuple[Frame, MapLabel]:
        """Capture et nom de la carte actuelle (celle dont les coordonnées sont `self.coords`).

        La souris reste où elle est : elle n'est écartée que pour chercher des soleils à l'écran."""
        frame = self.game.look(park=False)
        label = read_map_label(frame) or unreadable_label()
        self._locate(frame, label, self.coords)
        self._last_label = label
        return frame, label

    def _locate(self, frame: Frame, label: MapLabel, expected: tuple[int, int] | None) -> None:
        """Coordonnées de la carte : lues, sinon `expected` ; un chiffre inconnu est alors appris."""
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
                far = max(abs(label.coords[0] - expected[0]), abs(label.coords[1] - expected[1])) > 1
                self.jumped = self.jumped or far
                log.warning("Coordonnées lues %s,%s : j'attendais %s,%s%s", *label.coords, *expected,
                            " (personnage déplacé ailleurs : mort, téléportation ?)" if far else "")
            self.coords = label.coords
            return
        if expected is not None:
            merged, consistent = merge_reading(label.text, expected)
            if not consistent:
                # 11/10, cité d'Astrub : « ?,-18 » lu, 5,-17 attendu ; les joueurs qui passent ont fait croire à un
                # changement de carte. Le chemin cru plutôt que la lecture : position fausse, chiffres appris faux
                # (le 5 de « -15 » appris comme un 7), le principal a tourné en rond.
                far = max(abs(merged[0] - expected[0]), abs(merged[1] - expected[1])) > 1
                self.jumped = self.jumped or far
                log.warning("Coordonnées lues « %s » : pas la carte %s,%s attendue ; je me crois en %s,%s (rien "
                            "d'appris)", label.text, *expected, *merged)
                self.coords = merged
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
        key = self.key()
        known = self.world.suns(key)
        info = self.here()
        reference = {side: cells[0] for side, cells in info.suns.items()} if info else {}
        for side, cells in (info.client_suns.items() if info else ()):
            reference.setdefault(side, cells[0])  # case donnée par le client (soleil « conditionnel »)
        merged = {**reference, **known}  # ce que le bot a vu prime sur les références
        for side in [s for s in merged if self.world.several(key, s)]:
            del merged[side]  # plusieurs soleils de ce côté (rempart) : la case gardée ne dit pas lequel est le nôtre
        merged.update(self.world.suns_from(key, self.entry))  # celui qui a marché en arrivant par le même côté
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
        return self.candidates

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
        """La carte où l'on est est sûre, et ce Traveler écrit dans la mémoire partagée (pas le trajet d'un suiveur qui
        rejoint le principal) : coordonnées lues à l'écran ; nom illisible, position déduite du chemin à SURE_UNREAD
        cartes au plus d'une carte lue, sans saut."""
        label = label if label is not None else self._last_label
        if not self.learns or self.coords is None:
            return False
        if label is not None and label.readable and label.coords is not None:
            return label.coords == self.coords
        return not self.jumped and self._unread_steps <= SURE_UNREAD

    def _remember_suns(self, exits: list[Exit]) -> None:
        if self._sure_here():
            self.world.remember_suns(self.key(), exits)

    def _note_blocked(self, key: str | None, side: str, label: MapLabel, everywhere: bool = False) -> None:
        """Sortie notée bloquée dans data/carte.json, seulement si c'est sûr (_sure_here). 11/10 : des suiveurs, pas
        sur la carte qu'ils croyaient, ont noté bloqués les 4 côtés de 4,-20 ; le trajet suivant était impossible.

        Soleil vu mais inatteignable : bloqué seulement pour qui arrive par le même côté (de l'autre côté d'un rempart,
        il peut marcher) ; côté d'arrivée inconnu (début du trajet) : pour ce trajet seulement. `everywhere` : aucun
        soleil de ce côté, même avec la touche A : bloqué pour tous."""
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

    def go(self, frame: Frame, label: MapLabel, side: str, exits: dict[str, Exit] | None = None) -> MapLabel | None:
        """Passe sur la carte voisine par le côté `side` ; None si c'est impossible (ou si un combat commence)."""
        exits = exits if exits is not None and side in exits else self.exits(frame, side)
        exit_ = exits.get(side)
        if exit_ is None:
            log.info("Pas de sortie vers le %s sur cette carte, même avec la touche A : je la note bloquée", side)
            self._note_blocked(self.key(label), side, label, everywhere=True)
            return None
        before_key, before_coords, entry = self.key(label), self.coords, self.entry
        queue = [exit_] + [e for e in self.candidates.get(side, []) if distance(e.cell, exit_.cell) > SAME_SUN]
        self.game.recorder.save(annotate(frame, marks=[(e.point, f"sortie {side}") for e in queue]), "changement_de_carte")
        tried: list[Exit] = []
        searched = not exit_.remembered  # soleils de ce côté déjà cherchés à l'écran
        fast = self.settings.still_for > 0
        while queue and len(tried) < MAX_CANDIDATES:
            exit_ = queue.pop(0)
            tried.append(exit_)
            if len(tried) > 1:
                log.info("Autre soleil du %s : %s (le précédent n'est pas atteignable d'ici)", side, exit_.cell)
            spot = 0  # endroit de la case où cliquer : d'abord le centre, puis là où un personnage ne se tient pas
            for attempt in range(1, CANDIDATE_TRIES + 1):
                point, spread = _click_spot(frame, exit_.cell, spot)
                log.info("Vers la carte du %s : clic sur le soleil en %s%s", side, exit_.cell,
                         " (contour de la touche A)" if exit_.marked and not spot else " (bas de la case)" if spot else "")
                with self._players_hidden():  # tenue pendant la marche : le mouvement se voit sans les autres joueurs
                    self.click_sun(point, spread)
                    if attempt == 1 and self.on_click is not None:
                        self.on_click(side, exit_.cell, frame, spot)
                    reclick = None if fast else (lambda point=point, spread=spread: self.click_sun(point, spread))
                    arrived = self._wait_arrival(label, frame, before_coords, reclick=reclick)
                if arrived is None:
                    return None  # combat (agression) ou autre fenêtre : la boucle principale s'en charge
                if arrived == MENU:
                    # Un personnage ou un monstre est sur le soleil : son menu s'est ouvert au lieu du déplacement.
                    log.info("Quelqu'un se tient sur le soleil : je ferme son menu et je clique ailleurs sur la case")
                    self._close_menu(frame, exit_.cell)
                    spot += 1
                    continue
                if isinstance(arrived, tuple):
                    new_frame, new_label = arrived
                    if self.learns:
                        self.world.confirm_sun(before_key, side, exit_.cell, entry if self._sure_here(label) else None)
                    self._arrived(new_frame, new_label, before_key, before_coords, side, exit_.cell)
                    if self.on_crossed is not None:
                        self.on_crossed(side, exit_.cell, label, new_label)
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
            if exit_.remembered and self.learns:
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
        self._note_blocked(before_key, side, label)
        path = self.game.recorder.save(self.game.look().image, "sortie_bloquee", lossless=True)
        log.warning("Sortie du %s inutilisable, je la note bloquée (capture : %s)", side, path)
        return None

    def click_sun(self, point: tuple[float, float], spread: tuple[float, float], game: Game | None = None) -> bool:
        """Clic sur un soleil, la touche qui cache les autres joueurs tenue (travel.hide_players_key) ; `game` : la
        fenêtre d'un suiveur (même réglage). False si le clic a été annulé."""
        game = game or self.game
        key = self.settings.hide_players_key
        if not key or (game is self.game and self._hiding):
            return game.click(point, spread) is not False
        with game.holding(key):
            return game.click(point, spread) is not False

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
        while time.monotonic() < deadline:
            self.game.sleep(0.4)
            frame = self.game.look(park=False)
            if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                log.info("Combat pendant le trajet")
                return None
            if find_context_menu(frame) is not None:
                return MENU
            scene = _scene(frame)
            changed = _gap(scene, departure) > SCENE_CHANGED
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
        log.debug("Pas arrivé en %.0f s (cases qui bougeaient : %s)", self.settings.walk_timeout, counts)
        return False

    def _close_menu(self, frame: Frame, target: tuple[int, int]) -> None:
        """Ferme le menu ouvert par erreur : Échap seulement (escape_menu), jamais de clic « ailleurs »."""
        escape_menu(self.game)

    def _settled(self, frame: Frame, label: MapLabel) -> tuple[Frame, MapLabel]:
        self.game.sleep(self.settings.settle)
        settled_frame = self.game.look(park=False)
        settled = read_map_label(settled_frame)
        return (settled_frame, settled) if settled is not None else (frame, label)

    def _arrived(self, frame: Frame, label: MapLabel, before_key: str | None, before_coords: tuple[int, int] | None,
                 side: str, cell: tuple[int, int] | None = None) -> None:
        self.entry = OPPOSITE[side]
        self.entry_cell = _facing(frame, cell, side) if cell is not None else None
        self._unread_steps = 0 if label.readable and label.coords is not None else self._unread_steps + 1
        expected = None
        if before_coords is not None:
            info = self.atlas.get(self.region, before_coords)
            dx, dy = DELTA[side]
            expected = (info.leads_to.get(side) if info else None) or (before_coords[0] + dx, before_coords[1] + dy)
        self._locate(frame, label, expected)
        log.info("Nouvelle carte : %s%s", label.describe(),
                 f" (coordonnées {self.coords[0]},{self.coords[1]} d'après le chemin)"
                 if label.coords is None and self.coords else "")
        key = self.key(label)
        if key and self.learns:  # 11/10 : le regroupement d'un suiveur effaçait les sorties bloquées du principal
            self.world.link(before_key, side, key)
            if before_key and self.world.destination(key, OPPOSITE[side]) != BLOCKED:
                # Le soleil d'en face ramène d'où l'on vient, sauf s'il a été vu inutilisable : une carte coupée par un
                # rempart n'atteint parfois ce soleil que d'un côté (cité d'Astrub).
                self.world.link(key, OPPOSITE[side], before_key)


def escape_menu(game: Game) -> None:
    """Échap pour fermer un menu ouvert par erreur (quelqu'un sur le soleil). S'il n'y avait rien à fermer, Échap ouvre
    le Menu principal (Changer de perso / Déconnecter / Quitter Dofus) : refermé aussitôt par Échap, sans aucun clic.
    11/10 : le Menu principal pris pour un menu de joueur, un clic « ailleurs » est tombé sur « Déconnecter »."""
    game.press("escape")
    game.sleep(0.3)
    now = game.look(park=False)
    known = find_known_popup(now, load_popup_specs())
    if known is not None and known.spec.name == "menu principal":
        log.info("Échap a ouvert le Menu principal : je le referme (Échap)")
        game.press("escape")
        game.sleep(0.3)


def _click_spot(frame: Frame, cell: tuple[int, int], spot: int) -> tuple[tuple[float, float], tuple[float, float]]:
    """Où cliquer dans la case du soleil, et avec quelle dispersion. 0 : autour du centre ; ensuite, le bas du
    losange, puis ses pointes gauche et droite : un personnage debout sur la case en cache le centre et le haut."""
    cx, cy = frame.grid.center(cell)
    hw, hh = frame.grid.half_w, frame.grid.half_h
    spots = [(cx, cy), (cx, cy + 0.55 * hh), (cx - 0.55 * hw, cy + 0.1 * hh), (cx + 0.55 * hw, cy + 0.1 * hh)]
    x, y = spots[min(spot, len(spots) - 1)]
    spread = frame.grid.click_spread() if spot == 0 else (hw * 0.12, hh * 0.1)
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


def shortest_route(start: tuple[int, int], entry: str | None, goals, links, allowed) -> list | None:
    """Comme shortest_path, sur les états (carte, côté d'arrivée) : sur une carte coupée par un rempart, une sortie peut
    n'être inatteignable que pour qui arrive par un certain côté. `links(carte, côté d'arrivée)`."""
    first = (start, entry)
    came: dict = {first: None}
    queue = deque([first])
    while queue:
        state = queue.popleft()
        if state[0] in goals:
            path = []
            while came[state] is not None:
                previous, side = came[state]
                path.append((side, state[0]))
                state = previous
            return path[::-1]
        for side, dest in links(*state).items():
            following = (dest, OPPOSITE[side])
            if following in came or not allowed(dest):
                continue
            came[following] = (state, side)
            queue.append(following)
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
        self.came_from: str | None = None
        self.done = False
        self._warned = False
        self._searched: set[str] = set()  # cartes dont tous les soleils ont été cherchés à l'écran
        info = traveler.here()  # coordonnées données au lancement : la sous-zone se lit dans les références
        if info is not None and info.zone:
            self.zone_name = info.zone
            maps = sorted(traveler.atlas.zone_maps(traveler.region, info.zone))
            log.info("Combat en zone : carte %s,%s de %s -> région %s, sous-zone %s (%d cartes : %s)",
                     *info.coords, traveler.region, info.area or traveler.region, info.zone, len(maps),
                     " ".join(f"{x},{y}" for x, y in maps))
        elif traveler.coords is not None:
            log.warning("Carte %s,%s absente des références de %s : sous-zone d'après le nom lu à l'écran",
                        *traveler.coords, traveler.region)

    def _zone_here(self, label: MapLabel) -> str | None:
        info = self.traveler.here()
        return (info.zone if info else None) or label.zone_name

    def step(self) -> None:
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
        if here_zone != self.zone_name or forbidden:
            if key:
                self.world.visit(key, label, [])
            log.info("Carte %s : je reviens", forbidden or f"hors de la zone {self.zone_name} ({here_zone or 'inconnue'})")
            if self.came_from and self.traveler.go(frame, label, self.came_from) is not None:
                self.came_from = None
            return
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
                        or bool(self.traveler.forbidden_side(side)))
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
        log.info("Plus de groupe ici : carte du %s (%s,%s, zone %s)", side, *self.traveler.neighbour(side), self.zone_name)
        if self.traveler.go(frame, label, side, exits) is not None:
            self.came_from = OPPOSITE[side]


class Journey:
    """Mode aller (--aller x,y) : jusqu'à la carte d'arrivée par le plus court chemin, sans combat.

    Itinéraire (en nombre de cartes) sur les liaisons connues : ce que le bot a vu (data/carte.json : carte où mène
    chaque sortie déjà prise, sorties bloquées) prime sur les références (config/monde : liaisons des soleils
    d'Incarnam ; Monde des Douze, décrit sans soleils : les cartes voisines qui existent, passage à confirmer en
    jeu). Jamais par une carte ou une zone interdite (travel.avoid_maps / avoid_zones). Une carte à la fois, par le
    Traveler (soleil de la mémoire ou des références, sinon cherché à l'écran, touche A si besoin). Nouvel itinéraire
    quand le personnage arrive ailleurs que prévu (liaison différente des références, mort, téléportation) ou qu'une
    sortie est introuvable (le Traveler la note bloquée). Arrêt à l'arrivée, ou avec un message si aucun chemin."""

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
            if not any(m.suns for m in self.traveler.atlas.maps.values() if m.region == region):
                log.info("%s : références sans soleils : chaque sortie est cherchée à l'écran (touche %s si besoin) ; "
                         "une sortie introuvable est notée bloquée et l'itinéraire recalculé", region,
                         self.traveler.settings.show_exits_key.upper())
        key = self.traveler.key(label)
        if key:
            self.world.visit(key, label, [])  # zone (cartes interdites) et dernier passage, comme les autres modes
        if self.path is not None and here != self.at:
            log.warning("Carte %s,%s au lieu de %s,%s%s : nouvel itinéraire", *here, *self.at,
                        " (personnage déplacé ailleurs : mort, téléportation ?)" if self.traveler.jumped else "")
            self.path = None
        self.traveler.jumped = False  # le trajet repart de là où l'on est
        if here == self.goal:
            log.info("Arrivé en %s,%s%s", *here, f" ({self.crossed} changement(s) de carte)" if self.crossed
                     else " : déjà sur la carte d'arrivée")
            self.done = True
            return
        if self.path is None and not self._plan(here):
            return
        side, dest = self.path[0]
        log.info("Trajet : côté %s, vers %s,%s (encore %d carte(s) jusqu'à %s,%s)", side, *dest, len(self.path),
                 *self.goal)
        if self.traveler.go(frame, label, side) is None:
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

    def _plan(self, here: tuple[int, int]) -> bool:
        """Calcule l'itinéraire depuis `here` et le journalise ; False (et arrêt) s'il n'y en a pas."""
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
        path = shortest_route(here, getattr(self.traveler, "entry", None), {self.goal},
                              lambda coords, entry: self._links(coords, entry=entry), self._allowed)
        if path is None:
            self._stop(f"aucun chemin de {here[0]},{here[1]} à {x},{y} : {self._no_path(here)}")
            return False
        log.info("%s de %s,%s à %s,%s : %d carte(s) : %s", "Itinéraire" if self.at is None else "Nouvel itinéraire",
                 *here, x, y, len(path), " ".join(f"{cx},{cy}" for _, (cx, cy) in path))
        self.path, self.at = path, here
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
        if blocked:
            for key, side in getattr(self.traveler, "blocked_here", ()):
                if key == World.key(region, coords):
                    links.pop(side, None)  # bloquée pour ce trajet seulement (carte pas sûre)
            for side in self.world.blocked_from(World.key(region, coords), entry):
                links.pop(side, None)  # inatteignable quand on arrive par ce côté (rempart)
        for side, dest in (self.world.maps.get(World.key(region, coords)) or {}).get("sorties", {}).items():
            if dest == BLOCKED:
                if blocked:
                    links.pop(side, None)
            elif dest:
                where = _coords_of(dest)
                if where is not None and dest.rsplit(" ", 1)[0] == region:
                    links[side] = where
                else:
                    links.pop(side, None)  # mène hors de la région
        return links

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
            return (f"il passe par des sorties notées « {BLOCKED} » ({', '.join(blocked)}) dans "
                    f"{self.world.path or 'la mémoire des cartes'} : remplace-les par null si le passage existe")
        if shortest_path(here, {self.goal}, self._links, self._known) is not None:
            return "il passe par des cartes ou zones interdites (travel.avoid_maps / avoid_zones)"
        return "aucune liaison connue entre ces deux cartes (île, autre région ? vérifie --monde)"

    def _stop(self, why: str) -> None:
        log.warning("Trajet vers %s,%s arrêté : %s", *self.goal, why)
        self.done = True


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
    if settings.mode == "aller":
        return Journey(traveler, settings.destination)
    return ZoneRoamer(traveler)
