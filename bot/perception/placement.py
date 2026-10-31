"""Cases de placement (rouges et bleues) en début de combat, libres ou occupées par un personnage.

Mode tactique requis : en affichage normal, l'herbe dessinée par-dessus les cases brouille leur couleur.
Une case libre est entièrement colorée ; un personnage debout dessus en cache une partie (mesuré :
1,0 libre contre 0,62 à 0,85 occupée ; un petit monstre en cache peu). Le seuil est relatif aux autres
cases de la même couleur (une capture compressée en JPEG donne 0,96 aux cases libres).
Au début du placement, le bandeau « Tu es entré en combat ! » assombrit une bande de la carte (le 01/10, la
rangée du haut de mes cases rouges : le bot s'est cru bleu, sur la case du monstre) : attendre qu'il parte.
Une case coupée par le bord de la carte (barre d'interface en bas) paraîtrait occupée : elle est ignorée.
"""

from dataclasses import dataclass

import numpy as np

from bot.perception.colors import TEAMS, team_mask
from bot.perception.frame import Frame
from bot.perception.grid import Cell
from bot.perception.templates import match_template

MIN_COVERAGE = 0.3  # en dessous : pas une case de placement
OCCUPIED_DROP = 0.035  # couverture plus basse que celle des cases libres de cette couleur : quelqu'un dessus
# Couverture d'une case libre, au moins (0,94 à 1,0 sur les captures, JPEG compris ; occupées : 0,35 à 0,94). Le
# 3e quartile des cases d'une couleur n'est celle d'une case libre que si la plupart le sont : avec 8 personnages sur
# les 10 cases de l'équipe, il tomberait sur une case occupée (vers 0,8) et des cases occupées passeraient pour libres.
FREE_COVERAGE = 0.95
ENTRY_BANNER = "bandeau_entree_combat.png"  # texte blanc du bandeau, au milieu de la carte
BANNER_ZONE = (900, 300, 1600, 480)


@dataclass(frozen=True)
class Placement:
    free: dict[str, set[Cell]]
    occupied: dict[str, set[Cell]]


def entry_banner(frame: Frame) -> bool:
    """Bandeau « Tu es entré en combat ! » affiché (score 0,94 à 1 avec, 0,52 au plus sans : journaux du 01/10)."""
    return match_template(frame, ENTRY_BANNER, box=frame.layout.box(*BANNER_ZONE), threshold=0.75) is not None


def read_placement(frame: Frame) -> Placement:
    grid = frame.grid
    height, width = frame.image.shape[:2]
    masks = {team: team_mask(frame.day_image, team) for team in TEAMS}  # couleurs du jour (nuit : daylight.py)
    free: dict[str, set[Cell]] = {team: set() for team in TEAMS}
    occupied: dict[str, set[Cell]] = {team: set() for team in TEAMS}
    # Losange intérieur d'une case, en décalages de pixels autour de son centre.
    hw, hh = grid.half_w, grid.half_h
    ys, xs = np.mgrid[-int(hh) : int(hh) + 1, -int(hw) : int(hw) + 1]
    inside = np.abs(xs) / hw + np.abs(ys) / hh <= 0.9
    dys, dxs = ys[inside], xs[inside]
    coverages: dict[str, dict[Cell, float]] = {team: {} for team in TEAMS}
    left, top, right, bottom = frame.layout.map_box
    for cell in grid.cells():
        cx, cy = grid.center(cell)
        if cx - hw < left or cx + hw > right or cy - hh < top or cy + hh > bottom:
            continue
        px, py = np.rint(cx + dxs).astype(int), np.rint(cy + dys).astype(int)
        if px.min() < 0 or py.min() < 0 or px.max() >= width or py.max() >= height:
            continue
        for team, mask in masks.items():
            coverage = float(mask[py, px].mean())
            if coverage >= MIN_COVERAGE:
                coverages[team][cell] = coverage
    for team, cells in coverages.items():
        free[team], occupied[team] = split_cells(cells)
    return Placement(free, occupied)


def split_cells(coverages: dict[Cell, float]) -> tuple[set[Cell], set[Cell]]:
    """Cases d'une couleur (couverture de chacune par la couleur) : (libres, occupées)."""
    if not coverages:
        return set(), set()
    clear = max(float(np.percentile(list(coverages.values()), 75)), FREE_COVERAGE)  # repère : une case libre
    free = {cell for cell, coverage in coverages.items() if coverage >= clear - OCCUPIED_DROP}
    return free, set(coverages) - free


def standing_cells(cells: set[Cell]) -> set[Cell]:
    """Cases où se tiennent les combattants : un personnage grand couvre aussi la case juste au-dessus de la sienne
    (même colonne, deux rangées plus haut), qui paraît occupée elle aussi. On garde la plus basse."""
    return {c for c in cells if (c[0], c[1] + 2) not in cells}
