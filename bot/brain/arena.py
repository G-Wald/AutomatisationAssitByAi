"""Carte de combat officielle : blocs, trous et cases de placement de chaque carte du jeu (24/10).

Référence locale, non versionnée (data/references/combat_<région>.json, tools/reference_combat.py : cartes du client
déchiffrées avec les clés relevées sur les serveurs officiels ; placements de l'émulateur StarLoco). Contrôlée le 24/10
(tools/controle_combat.py, docs/rapports/veracite_reference_combat.md) : sur 1388 captures de combat, accord marche
98 % et ligne de vue 99 % avec ce que lit l'écran, aucun bloc officiel lu comme du sol (les écarts sont des sols lus
blocs à tort à l'écran) ; sur 7 captures de placement, toutes les cases lues sont des placements de la carte reconnue.

La carte est reconnue d'après ce que montre l'écran, sans les coordonnées : le terrain lu en mode tactique, ou les
cases de placement. Sans référence (fichiers absents), rien n'est reconnu et le combat lit tout à l'écran, comme avant.
"""

import json
import logging
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
# avec 3 points d'avance ; la carte reconnue colle à 98-99 %), et cases lues au moins.
TERRAIN_MATCH, TERRAIN_MARGIN, TERRAIN_CELLS = 0.9, 0.03, 50
PLACEMENT_CELLS = 6  # placement : cases lues au moins, toutes parmi celles d'une seule carte


@dataclass(frozen=True)
class Arena:
    ident: str
    coords: str  # « x,y »
    blocks: frozenset[Cell]  # ni marche ni ligne de vue
    holes: frozenset[Cell]  # pas de marche, ligne de vue (fosse, vide autour de la carte)
    placements: tuple[frozenset[Cell], ...] = ()  # cases de placement des deux camps (couleur selon le combat)

    @property
    def unwalkable(self) -> frozenset[Cell]:
        return self.blocks | self.holes


class Arenas:
    """Toutes les cartes de combat de la référence ; chargées une fois (Arenas.load)."""

    _loaded: "Arenas | None" = None

    def __init__(self, entries: dict[str, dict]) -> None:
        self.arenas = [Arena(ident, e.get("carte", "?"), frozenset(map(tuple, e.get("blocs", ()))),
                             frozenset(map(tuple, e.get("trous", ()))),
                             tuple(frozenset(map(tuple, camp)) for camp in e.get("placements") or ()))
                       for ident, e in entries.items()]
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
        arenas = cls(entries)
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
        if agreement[best] < TERRAIN_MATCH or agreement[best] - agreement[second] < TERRAIN_MARGIN:
            return None
        log.debug("Carte de combat reconnue au terrain : %s (%s), accord %.0f %% (suivante %.0f %%)",
                  self.arenas[best].coords, self.arenas[best].ident, 100 * agreement[best], 100 * agreement[second])
        return self.arenas[best]

    def by_placement(self, cells: set[Cell]) -> Arena | None:
        """La seule carte dont les cases de placement contiennent toutes celles lues (au moins PLACEMENT_CELLS)."""
        if len(cells) < PLACEMENT_CELLS:
            return None
        if self._placed is None:
            self._placed = [(frozenset().union(*a.placements), a) for a in self.arenas if a.placements]
        found = [arena for places, arena in self._placed if cells <= places]
        return found[0] if len(found) == 1 else None
