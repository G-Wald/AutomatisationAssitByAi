"""Itinéraire complet sur la référence complétée (data/references), calculé avant de partir, puis suivi à l'estime.

25/10, l'utilisateur : la référence est sûre (482 passages sur 482 d'accord avec le jeu) ; quand un trajet échoue, c'est
la lecture du nom de la carte qui se trompe (décor clair, chiffre perdu), pas la référence. Journal 201109 (Taïnéla vers
les Blops, 4 min) : 12 itinéraires recalculés, dont 7 sur une fausse lecture (« 0,-2 » pour 0,-29, « 0,-3 » pour 0,-30),
6 recherches à la touche A, 10 clics sans effet, aucune carte gagnée.

Donc :
- l'état d'un trajet est (carte, case d'arrivée) : sur une carte coupée par un muret ou une falaise, la case où l'on
  arrive dit quels soleils se joignent (`depuis_arrivee` de la référence ; à défaut, les cases praticables de la carte
  officielle, brain/arena.walk_area) ;
- l'itinéraire est une suite d'étapes (carte, case du soleil à cliquer, carte suivante, case d'arrivée attendue),
  calculée une fois sur la référence seule (plus court en cartes) ;
- le Traveler clique la case prévue (pas de recherche à l'écran, pas de touche A), vérifie que le décor a changé, et
  ne fait que contrôler la carte d'arrivée : une lecture compatible (même partielle ou tronquée) confirme ; une lecture
  franchement contraire est vérifiée par « /p %pos% » avant tout nouvel itinéraire ;
- zaap seulement quand aucun chemin à pied n'existe (25/10 : le Berceau de Taïnéla, 26 cartes autour du zaap 1,-32,
  est fermé à pied d'après la carte officielle).
"""

import heapq
import logging
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from bot.perception.grid import Cell

log = logging.getLogger(__name__)

Coords = tuple[int, int]
# Soleil choisi sans savoir s'il se joint d'où l'on est (début de trajet sans case d'arrivée connue, carte que la carte
# officielle ne permet pas de trancher) : un peu plus cher qu'un passage sûr, pour préférer les sûrs à longueur égale.
UNSURE_COST = 0.3
# Zaap : seulement quand aucun chemin à pied n'existe (l'itinéraire à pied est cherché d'abord) ; entre deux zaaps,
# coût d'une téléportation en cartes (menus, kamas), pour choisir le zaap d'arrivée le plus proche du but à pied.
ZAAP_COST = 6.0
ZAAP = "zaap"


@dataclass(frozen=True)
class Leg:
    """Une étape : depuis `coords`, clic sur `cell` (soleil du côté `side`, ou zaap si `zaap` est le nom du zaap
    d'arrivée), carte suivante `dest`, case d'arrivée attendue `arrival` (None : pas connue). `others` : autres soleils
    du même côté, joignables d'ici et menant à `dest` (secours si le premier ne passe pas)."""

    coords: Coords
    side: str | None
    cell: Cell
    dest: Coords
    arrival: Cell | None
    others: tuple[Cell, ...] = ()
    zaap: str | None = None
    sure: bool = True  # soleil joignable d'après la référence (False : pas tranché, essayé quand même)

    def describe(self) -> str:
        if self.zaap:
            return f"{_xy(self.coords)} zaap {self.cell} -> {self.zaap} {_xy(self.dest)}"
        arrival = f" arrivée {self.arrival}" if self.arrival is not None else ""
        return f"{_xy(self.coords)} {self.side} soleil {self.cell}{'' if self.sure else ' (à confirmer)'} -> " \
               f"{_xy(self.dest)}{arrival}"


def _xy(coords: Coords) -> str:
    return f"{coords[0]},{coords[1]}"


def _key(coords: Coords | None) -> str | None:
    return None if coords is None else f"{coords[0]},{coords[1]}"


def _parse(text: str | None) -> Coords | None:
    try:
        x, y = str(text).split(",")
        return int(x), int(y)
    except (TypeError, ValueError):
        return None


class Planner:
    """Itinéraires sur la référence complétée d'une région : {"x,y": {"sorties": {côté: {"vers", "cases", "arrivees",
    ...}}, "depuis_arrivee": {"i,j": {côté: [[i, j], ...]}}, "objets": [{"nature": "zaap", "case": [i, j]}]}}.

    `allowed(carte)` : la carte peut être traversée (zones et cartes interdites). `area(carte, case)` : cases joignables
    à pied depuis cette case de la carte (None : pas connu) ; case None : la seule grande zone de la carte, s'il n'y en a
    qu'une (brain/arena.walk_area). `zaaps` : coordonnées -> nom des zaaps (config/monde, lieux)."""

    def __init__(self, reference: dict, allowed: Callable[[Coords], bool] | None = None,
                 area: Callable[[Coords, Cell | None], set | None] | None = None,
                 zaaps: dict[Coords, str] | None = None) -> None:
        self.reference = reference or {}
        self.allowed = allowed or (lambda coords: True)
        self.area = area
        self.zaaps = dict(zaaps or {})
        self._areas: dict[tuple[Coords, Cell | None], set | None] = {}

    def describes(self, coords: Coords | None) -> bool:
        return coords is not None and _key(coords) in self.reference

    def zaap_cell(self, coords: Coords | None) -> Cell | None:
        """Case du zaap de la carte (objets de la référence), None s'il n'y en a pas."""
        for thing in (self.reference.get(_key(coords)) or {}).get("objets") or ():
            if thing.get("nature") == ZAAP and thing.get("case"):
                return tuple(thing["case"])
        return None

    def _walkable(self, coords: Coords, cell: Cell | None) -> set | None:
        if self.area is None:
            return None
        if (coords, cell) not in self._areas:
            try:
                self._areas[(coords, cell)] = self.area(coords, cell)
            except Exception as error:  # noqa: BLE001 - une carte officielle illisible ne doit pas arrêter le trajet
                log.debug("Cases praticables de %s inconnues (%s)", _xy(coords), error)
                self._areas[(coords, cell)] = None
        return self._areas[(coords, cell)]

    def suns(self, coords: Coords, cell: Cell | None, side: str) -> tuple[list[Cell], bool]:
        """Soleils du côté `side` joignables depuis la case `cell` de la carte, et True si c'est sûr (référence ou carte
        officielle) ; sinon tous ceux du côté, à confirmer."""
        info = self.reference.get(_key(coords)) or {}
        exit_ = (info.get("sorties") or {}).get(side) or {}
        every = [tuple(c) for c in exit_.get("cases") or ()]
        if cell is not None:
            known = (info.get("depuis_arrivee") or {}).get(_key(cell))
            if known is not None:
                return [tuple(c) for c in known.get(side) or ()], True
        walkable = self._walkable(coords, cell)
        if walkable is not None:
            return [c for c in every if c in walkable or any(_near(c, w) for w in walkable)], True
        # Rien pour trancher : un seul soleil de ce côté sur une carte sans muret connu (ni `bloquees_depuis`, ni cases
        # d'arrivée décrites) est pris pour sûr ; sinon à confirmer en jeu.
        return every, len(every) <= 1 and not info.get("bloquees_depuis")             and (cell is None or not info.get("depuis_arrivee"))

    def arrival(self, coords: Coords, side: str, cell: Cell) -> Cell | None:
        exit_ = ((self.reference.get(_key(coords)) or {}).get("sorties") or {}).get(side) or {}
        found = (exit_.get("arrivees") or {}).get(_key(cell))
        return tuple(found) if found else None

    def moves(self, coords: Coords, cell: Cell | None) -> Iterable[Leg]:
        """Étapes possibles depuis la case `cell` de la carte (une par soleil joignable)."""
        info = self.reference.get(_key(coords)) or {}
        for side, exit_ in sorted((info.get("sorties") or {}).items()):
            dest = _parse(exit_.get("vers"))
            if dest is None:
                continue
            cells, sure = self.suns(coords, cell, side)
            for sun in cells:
                others = tuple(c for c in cells if c != sun)
                yield Leg(coords, side, sun, dest, self.arrival(coords, side, sun), others, sure=sure)

    def route(self, start: Coords, cell: Cell | None, goals: set[Coords], avoid: Iterable = (),
              zaap: bool = False, refused: Iterable[Coords] = ()) -> list[Leg] | None:
        """Plus court itinéraire (en cartes) de la case `cell` de `start` jusqu'à l'une des cartes `goals` : [] si on y
        est, None s'il n'y en a pas. `avoid` : (carte, côté) ratés pendant ce trajet ; `zaap` : téléportations
        permises entre zaaps (sauf vers les cartes de `refused`, zaaps pas connus du personnage)."""
        avoid, refused = set(avoid), set(refused)
        if start in goals:
            return []
        first = (start, cell)
        best = {first: 0.0}
        came: dict = {first: None}
        queue = [(0.0, 0, first)]
        order = 1
        while queue:
            spent, _, state = heapq.heappop(queue)
            if spent > best.get(state, float("inf")):
                continue
            coords, here = state
            if coords in goals:
                legs = []
                while came[state] is not None:
                    state, leg = came[state]
                    legs.append(leg)
                return legs[::-1]
            steps = list(self.moves(coords, here))
            if zaap and self.zaap_cell(coords) is not None and coords in self.zaaps:
                # Arrivée au pied du zaap d'arrivée : sa case sert de case d'arrivée (cases joignables d'après la carte
                # officielle).
                steps += [Leg(coords, None, self.zaap_cell(coords), dest, self.zaap_cell(dest), zaap=name)
                          for dest, name in sorted(self.zaaps.items()) if dest != coords and dest not in refused
                          and self.describes(dest)]
            for leg in steps:
                if (leg.coords, leg.side) in avoid or not self.allowed(leg.dest):
                    continue
                cost = ZAAP_COST if leg.zaap else 1.0 + (0.0 if leg.sure else UNSURE_COST)
                following = (leg.dest, leg.arrival)
                total = spent + cost
                if total >= best.get(following, float("inf")):
                    continue
                best[following], came[following] = total, (state, leg)
                heapq.heappush(queue, (total, order, following))
                order += 1
        return None


def _near(a: Cell, b: Cell) -> bool:
    """Cases voisines (8 directions hors combat, brain/arena.STEPS) : un soleil posé au bord d'une zone praticable."""
    di, dj = abs(a[0] - b[0]), abs(a[1] - b[1])
    return (di, dj) in ((1, 1), (2, 0), (0, 2))


def reading_fits(text: str | None, expected: Coords | None) -> bool:
    """La lecture des coordonnées (« 0,-2 », « ?,-18 », « -2,-32? ») va avec la carte attendue : chaque caractère lu est
    celui attendu ou un « ? » ; un chiffre final perdu (décor clair sous le dernier chiffre, journal 201109 : « 0,-2 »
    pour 0,-29) ou un « ? » de trop après le nombre (tache claire du décor lue comme un caractère : « -2,-32? ») ne
    contredisent pas. Lecture vide ou illisible : rien ne contredit."""
    if expected is None or not text or "," not in text:
        return True
    parts = text.split(",")
    if len(parts) != 2:
        return True
    for part, value in zip(parts, expected):
        want = str(value)
        while len(part) > len(want) and part.endswith("?"):
            part = part[:-1]
        if not part or len(part) > len(want):
            return not part
        if any(c != "?" and c != w for c, w in zip(part, want)):
            return False
        if len(part) < len(want) and not any(c.isdigit() for c in part):
            return False  # signe seul : pas une lecture
    return True


def complete(text: str | None) -> bool:
    """Coordonnées lues en entier (aucun chiffre inconnu)."""
    return bool(text) and "?" not in text and "," in text


def zaap_search_word(name: str) -> str:
    """Mot tapé dans la recherche de la liste des zaaps : le plus long mot du nom sans accent (on ne sait pas si la
    recherche les ignore), en minuscules (« Cité d'Astrub » -> « astrub », « Le Berceau » -> « berceau ») ; le 25/10,
    « berceau » a trouvé la seule ligne « Le berceau ». Que des mots accentués : le plus long, accents ôtés."""
    words = [w for w in name.lower().replace("'", " ").replace("-", " ").split() if w.isalpha()]
    plain = [w for w in words if w.isascii()]
    if plain:
        return max(plain, key=len)
    stripped = [unicodedata.normalize("NFKD", w).encode("ascii", "ignore").decode() for w in words]
    return max(stripped, key=len) if stripped else name.lower()
