"""Carte de combat officielle : blocs, trous et cases de placement de chaque carte du jeu (24/10).

Référence locale, non versionnée (data/references/combat_<région>.json, tools/reference_combat.py : cartes du client
déchiffrées avec les clés relevées sur les serveurs officiels ; placements de l'émulateur StarLoco). Contrôlée le 24/10
(tools/controle_combat.py, docs/rapports/veracite_reference_combat.md) : sur 1388 captures de combat, accord marche
98 % et ligne de vue 99 % avec ce que lit l'écran, aucun bloc officiel lu comme du sol (les écarts sont des sols lus
blocs à tort à l'écran) ; sur 7 captures de placement, toutes les cases lues sont des placements de la carte reconnue.

La carte est reconnue d'après ce que montre l'écran, sans les coordonnées : le terrain lu en mode tactique, ou les
cases de placement. Sans référence (fichiers absents), rien n'est reconnu et le combat lit tout à l'écran, comme avant.

31/10 : source prioritaire, la carte du client déchiffrée avec la clé entendue du serveur (brain/mapdata.py,
fight_arena) : la carte où l'on est, sans reconnaissance, et la version du jeu installé (mises à jour comprises). Les
références restent en secours (clé inconnue) ; celles des cartes changées depuis leur construction (changed_maps) ne sont
plus reconnues (blocs d'une autre version).
"""

import contextlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from bot.perception.grid import Cell
from bot.perception.sight import Terrain

log = logging.getLogger(__name__)

ARENA_FILES = ("combat_monde_des_douze.json", "combat_incarnam.json")
ARENA_DIR = Path(__file__).resolve().parents[2] / "data" / "references"
COLUMNS, ROWS = 29, 40  # cases (i, j) de notre grille, cartes de 15 de large
FLOOR, BLOCK, HOLE = 0, 1, 2
# Terrain : accord minimal sur les cases lues, avance sur la carte suivante (24/10 : 1388 captures sur 1581 reconnues
# avec 3 points d'avance ; la carte reconnue colle à 98-99 %), et cases lues au moins. Ou avance plus courte, si la
# suivante fait au moins TERRAIN_ERROR_RATIO fois plus d'erreurs (24/10, Prairie des Blops -4,-27 : 99,2 % contre
# 96,3 %, pas reconnue avec 3 points ; les plaines sans relief se ressemblent).
TERRAIN_MATCH, TERRAIN_MARGIN, TERRAIN_CELLS = 0.9, 0.03, 50
TERRAIN_MIN_MARGIN, TERRAIN_ERROR_RATIO = 0.015, 3.0
PLACEMENT_CELLS = 6  # placement : cases lues au moins, toutes parmi celles d'une seule carte


@dataclass(frozen=True)
class Arena:
    ident: str
    coords: str  # « x,y »
    blocks: frozenset[Cell]  # ni marche ni ligne de vue
    holes: frozenset[Cell]  # pas de marche, ligne de vue (fosse, vide autour de la carte)
    placements: tuple[frozenset[Cell], ...] = ()  # cases de placement des deux camps (couleur selon le combat)
    # 31/10 : cases praticables sans ligne de vue (carte du client seulement : 21 sur 60 cartes déchiffrées) ; les
    # références les comptent comme du sol.
    sight_only: frozenset[Cell] = frozenset()

    @property
    def unwalkable(self) -> frozenset[Cell]:
        return self.blocks | self.holes


class Arenas:
    """Toutes les cartes de combat de la référence ; chargées une fois (Arenas.load)."""

    _loaded: "Arenas | None" = None

    def __init__(self, entries: dict[str, dict], stale: frozenset[str] = frozenset()) -> None:
        every = [Arena(ident, e.get("carte", "?"), frozenset(map(tuple, e.get("blocs", ()))),
                       frozenset(map(tuple, e.get("trous", ()))),
                       tuple(frozenset(map(tuple, camp)) for camp in e.get("placements") or ()))
                 for ident, e in entries.items()]
        self.idents = {arena.ident: arena for arena in every}  # toutes, périmées comprises (coordonnées, placements)
        self.arenas = [arena for arena in every if arena.ident not in stale]  # celles qu'on peut reconnaître
        self._matrix: np.ndarray | None = None
        self._placed: list[tuple[frozenset[Cell], Arena]] | None = None

    @classmethod
    def load(cls, directory: Path | None = None) -> "Arenas":
        if directory is None and cls._loaded is not None:
            return cls._loaded
        entries: dict[str, dict] = {}
        for name in ARENA_FILES:
            path = (directory or ARENA_DIR) / name
            if path.exists():
                entries.update(json.loads(path.read_text(encoding="utf-8")))
        # 31/10 : une carte changée par une mise à jour du jeu n'a plus les blocs de la référence : pas reconnue.
        stale = frozenset(str(ident) for ident in changed_maps()) & set(entries) if entries else frozenset()
        if stale:
            log.info("Références de combat : %d carte(s) changée(s) depuis leur construction écartée(s) de la "
                     "reconnaissance (%s) ; carte du client si la clé est connue", len(stale), ", ".join(sorted(stale)))
        arenas = cls(entries, stale)
        if directory is None:
            cls._loaded = arenas
        return arenas

    def __len__(self) -> int:
        return len(self.arenas)

    @property
    def matrix(self) -> np.ndarray:
        """Cartes x cases : FLOOR / BLOCK / HOLE (cases hors de notre grille ignorées)."""
        if self._matrix is None:
            matrix = np.zeros((len(self.arenas), COLUMNS, ROWS), dtype=np.int8)
            for n, arena in enumerate(self.arenas):
                for cells, nature in ((arena.blocks, BLOCK), (arena.holes, HOLE)):
                    for i, j in cells:
                        if 0 <= i < COLUMNS and 0 <= j < ROWS:
                            matrix[n, i, j] = nature
            self._matrix = matrix.reshape(len(self.arenas), -1)
        return self._matrix

    def by_terrain(self, terrain: Terrain) -> Arena | None:
        """La carte dont les cases collent au terrain lu (mode tactique), si une seule s'en détache ; sinon None."""
        cells, natures = [], []
        for group, nature in ((terrain.floor, FLOOR), (terrain.blocks, BLOCK), (terrain.holes, HOLE)):
            for i, j in group:
                if 0 <= i < COLUMNS and 0 <= j < ROWS:
                    cells.append(i * ROWS + j)
                    natures.append(nature)
        if len(cells) < TERRAIN_CELLS or len(self.arenas) < 2:
            return None
        agreement = (self.matrix[:, cells] == np.array(natures, dtype=np.int8)).mean(axis=1)
        best, second = np.argsort(-agreement)[:2]
        lead = agreement[best] - agreement[second]
        clear = lead >= TERRAIN_MARGIN or (lead >= TERRAIN_MIN_MARGIN and
                                           1 - agreement[second] >= TERRAIN_ERROR_RATIO * (1 - agreement[best]))
        if agreement[best] < TERRAIN_MATCH or not clear:
            return None
        log.debug("Carte de combat reconnue au terrain : %s (%s), accord %.0f %% (suivante %.0f %%)",
                  self.arenas[best].coords, self.arenas[best].ident, 100 * agreement[best], 100 * agreement[second])
        return self.arenas[best]

    def by_ident(self, ident: int | str) -> Arena | None:
        """La référence de la carte `ident` (numéro du client), périmée ou non."""
        return self.idents.get(str(ident))

    def by_coords(self, coords: tuple[int, int]) -> list[Arena]:
        key = f"{coords[0]},{coords[1]}"
        return [a for a in self.arenas if a.coords == key]

    def by_placement(self, cells: set[Cell]) -> Arena | None:
        """La seule carte dont les cases de placement contiennent toutes celles lues (au moins PLACEMENT_CELLS)."""
        if len(cells) < PLACEMENT_CELLS:
            return None
        if self._placed is None:
            self._placed = [(frozenset().union(*a.placements), a) for a in self.arenas if a.placements]
        found = [arena for places, arena in self._placed if cells <= places]
        return found[0] if len(found) == 1 else None


CLIENT_MAPS = Path(os.environ.get("LOCALAPPDATA", "")) / "Ankama" / "Retro" / "resources" / "app" / "retroclient" / "data" \
    / "maps"  # cartes du jeu installé
BUILT_MAPS = ARENA_DIR.parent / "client_retro" / "maps"  # copie qui a servi à construire les références
MAP_FILE = re.compile(r"^(\d+)_(\d+)X?\.swf$")


def map_versions(directory: Path) -> dict[int, str]:
    """Identifiant de carte -> date de sa version, d'après les noms de fichiers (« 10000_0706131721X.swf »)."""
    versions = {}
    with contextlib.suppress(OSError):
        for name in os.listdir(directory):
            found = MAP_FILE.match(name)
            if found:
                versions[int(found.group(1))] = found.group(2)
    return versions


def changed_maps(installed: Path = CLIENT_MAPS, built: Path = BUILT_MAPS) -> list[int]:
    """Cartes dont le jeu installé a une autre version que celle des références (mise à jour du jeu) : les références
    (data/references) sont à reconstruire (tools/reference_complete.py, tools/reference_combat.py, après une nouvelle
    copie du client et des clés). [] si l'un des dossiers manque."""
    now, then = map_versions(installed), map_versions(built)
    if not now or not then:
        return []
    return sorted(ident for ident, date in now.items() if ident in then and then[ident] != date)


MAP_CELLS = 479  # cases d'une carte de 15 de large (lignes de 15 puis 14 en quinconce)
SECOND_AREA = 20  # cases : une 2e zone aussi grande, et la zone de départ ne se devine pas
STEPS = ((1, 1), (1, -1), (-1, 1), (-1, -1), (2, 0), (-2, 0), (0, 2), (0, -2))  # 8 directions hors combat


def map_cell(case: int, width: int = 15) -> Cell:
    """Numéro de case du client -> case (i, j) de notre grille (comme tools/cartes_client.case_vers_grille)."""
    row, rest = divmod(case, 2 * width - 1)
    return (2 * rest, 2 * row) if rest < width else (2 * (rest - width) + 1, 2 * row + 1)


ALL_CELLS = frozenset(map_cell(n) for n in range(MAP_CELLS))


def walk_area(arena: Arena, start: Cell | None) -> set[Cell] | None:
    """Cases joignables à pied (hors combat) sur la carte officielle : depuis `start` ; sans `start`, la seule grande
    zone d'un seul tenant s'il n'y en a qu'une (24/10, Taïnéla 5,-30 : deux zones de 65 et 52 cases, celle où l'on est
    dépend du côté d'arrivée), sinon None (on ne sait pas)."""
    free = set(ALL_CELLS) - arena.unwalkable
    areas, seen = [], set()
    for first in sorted(free):
        if first in seen:
            continue
        area, stack = set(), [first]
        while stack:
            cell = stack.pop()
            if cell in area:
                continue
            area.add(cell)
            stack.extend(n for n in ((cell[0] + di, cell[1] + dj) for di, dj in STEPS) if n in free and n not in area)
        seen |= area
        areas.append(area)
    if start is not None:
        near = [start] + [(start[0] + di, start[1] + dj) for di, dj in STEPS]
        for area in areas:
            if any(c in area for c in near):
                return area
        return None
    areas.sort(key=len, reverse=True)
    if not areas or (len(areas) > 1 and len(areas[1]) >= SECOND_AREA):
        return None
    return areas[0]


def client_arena(map_id: int | None, key: str | None = None, net=None, date: str | None = None) -> Arena | None:
    """Carte de combat d'après la carte du client (31/10, brain/mapdata.py) : blocs et trous de la version du jeu,
    coordonnées et placements de la référence s'il y en a une (placements gardés s'ils sont tous praticables : une mise
    à jour a pu les déplacer). `key` : celle du GDM, sinon net.map_key ou data/cles_cartes.json. None sans clé ni
    fichier de la carte, ou carte d'une autre largeur que notre grille."""
    from bot.brain.mapdata import known_key, load_map  # import local : mapdata.py importe ce module

    found = load_map(map_id, key or known_key(map_id, net), date)
    if found is None or found.width != 15:
        return None
    reference = Arenas.load().by_ident(map_id)
    placements = reference.placements if reference is not None else ()
    if placements and not all(camp <= found.walkable for camp in placements):
        log.info("Carte %d : placements de la référence hors des cases praticables de la carte du client : ignorés",
                 map_id)
        placements = ()
    return Arena(str(map_id), reference.coords if reference is not None else "?", found.blocks, found.holes,
                 placements, found.sight_blockers & found.walkable)


def fight_arena(net) -> Arena | None:
    """Carte de combat de la carte où l'on est (net.map_id du dernier GDM), d'après la carte du client ; None si la
    carte ou sa clé ne se savent pas (reconnaissance par les références alors, Arenas.by_terrain / by_placement)."""
    if net is None or getattr(net, "map_id", None) is None:
        return None
    return client_arena(net.map_id, net=net, date=getattr(net, "map_date", None))
