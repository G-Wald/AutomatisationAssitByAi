"""Portée et ligne de vue, lues sur les cases colorées quand un sort est sélectionné (mode tactique).

- bleu foncé : à portée, avec ligne de vue (le sort part) ;
- bleu clair : à portée, sans ligne de vue ;
- rien dans le losange de portée : obstacle (bloc, trou, bord de carte), sauf si un sprite cache la case.

Les obstacles vus servent à prédire la ligne de vue depuis d'autres cases, pour choisir où se déplacer.
Vérifié sur tests/fixtures/combat_portee_sort.png : 102 cases sur 103 prédites comme le jeu les affiche.
"""

import functools
import math
from dataclasses import dataclass

import numpy as np

from bot.perception.colors import RANGE_BLUE, near
from bot.perception.fighters import Fighter
from bot.perception.frame import Frame
from bot.perception.grid import Cell, distance, lattice_cell
from bot.perception.layout import MAP_BOTTOM
from bot.perception.timeline import timeline_portraits

NO_SIGHT_BLUE = (137, 117, 95)  # BGR, bleu clair : à portée mais ligne de vue bloquée
VOID_IN_RANGE = (45, 30, 10)  # BGR, bleu nuit : le vide autour de la carte, teinté par la portée (16/10, Incarnam 6,1)


@dataclass(frozen=True)
class RangeView:
    castable: set[Cell]
    no_sight: set[Cell]
    obstacles: set[Cell]
    spell_range: tuple[int, int] | None  # distances min et max des cases à portée
    void: frozenset[Cell] = frozenset()  # vide autour de la carte teinté bleu nuit : dans la portée, mais pas une case
    hidden: frozenset[Cell] = frozenset()  # sous la timeline ou l'interface du bas : on ne sait pas


def _hidden_by_sprites(frame: Frame, fighters: list[Fighter]) -> set[Cell]:
    """Cases masquées par le corps d'un personnage (dessiné au-dessus de sa case)."""
    grid = frame.grid
    hidden = set()
    for cell in grid.cells():
        x, y = grid.center(cell)
        if any(abs(x - f.x) < 0.6 * grid.half_w and f.y - 3 * grid.half_h < y < f.y for f in fighters):
            hidden.add(cell)
    return hidden


def range_center(frame: Frame, spell_range: tuple[int, int], min_match: float = 0.5,
                 exclude: set[Cell] | frozenset[Cell] = frozenset()) -> Cell | None:
    """Case du lanceur d'après la portée affichée : celle dont le losange de portée (`spell_range`) recouvre le mieux
    les cases colorées (indice de Jaccard), même si son cercle est caché (03/10 : sous la timeline). `exclude` : cases
    jamais colorées (trous, blocs, vide autour de la carte), retirées du losange attendu : sinon un losange coupé par
    un bord décale le centre. None si aucune ne colle à `min_match` près."""
    view = read_range(frame, Fighter("", -1e4, -1e4, (-10_000, -10_000)), [])
    if not (view.castable | view.no_sight):
        return None
    # 16/10, Incarnam 6,1 : la portée de Lancer de Pièces débordait sur le vide (bleu nuit) et sous la timeline ; le
    # losange coupé de deux côtés plaçait le lanceur une case trop haut, au-dessus du vide : 7 tirs refusés, 43 s. Le
    # vide teinté fait partie du losange ; ce qui est caché en est retiré.
    colored = view.castable | view.no_sight | view.void
    exclude = set(exclude) | view.hidden
    cells = [c for c in frame.grid.cells() if c not in exclude or c in colored]
    grid = np.array(cells)
    is_colored = np.array([c in colored for c in cells])
    low, high = spell_range
    mean = grid[is_colored].mean(axis=0)
    best, score = None, 0.0
    for center in grid[np.abs(grid - mean).max(axis=1) <= high]:  # le losange peut être coupé : centre décalé
        gap = np.abs(grid - center).max(axis=1)
        diamond = (gap >= low) & (gap <= high)
        match = (diamond & is_colored).sum() / (diamond | is_colored).sum()
        if match > score:
            best, score = (int(center[0]), int(center[1])), float(match)
    return best if score >= min_match else None


def read_range(frame: Frame, me: Fighter, fighters: list[Fighter]) -> RangeView:
    grid = frame.grid
    dark = near(frame.image, RANGE_BLUE, 25)
    light = near(frame.image, NO_SIGHT_BLUE, 22)
    # Vide autour de la carte (noir, ou bleu nuit dans la portée) : un trou, on tire par-dessus ; pas un obstacle.
    # 16/10, Incarnam 6,1 : plus de cent cases du vide prises pour des blocs, lignes de vue justes sur 57 à 70 %.
    void = near(frame.image, VOID_IN_RANGE, 14) | (frame.image.max(axis=2) <= 35)
    height, width = dark.shape
    castable, no_sight, uncolored, void_cells, hidden_cells = set(), set(), set(), set(), set()
    tinted = near(frame.image, VOID_IN_RANGE, 14)
    portraits = timeline_portraits(frame)
    # La rangée des portraits, jusqu'au bord droit (flèches de défilement), et le bas de la carte sous l'interface.
    row = (min(b[0] for b in portraits), min(b[1] for b in portraits), max(b[3] for b in portraits)) if portraits else None
    map_bottom = frame.layout.y(MAP_BOTTOM) - frame.layout.length(2)

    def hidden(px: int, py: int) -> bool:
        return py >= map_bottom or (row is not None and px >= row[0] and row[1] <= py <= row[2])

    for cell in grid.cells():
        if cell == me.cell:
            continue
        x, y = grid.center(cell)
        points = [(round(x + dx * grid.half_w), round(y + dy * grid.half_h)) for dx, dy in ((-0.6, 0), (0.6, 0), (0, -0.6), (0, 0.6))]
        points = [(px, py) for px, py in points if 0 <= px < width and 0 <= py < height]
        if sum(dark[py, px] for px, py in points) >= 3:
            castable.add(cell)
        elif sum(light[py, px] for px, py in points) >= 2:
            no_sight.add(cell)
        elif sum(hidden(px, py) for px, py in points) >= 2:
            hidden_cells.add(cell)
        elif sum(void[py, px] for px, py in points) >= 2:
            if sum(tinted[py, px] for px, py in points) >= 2:
                void_cells.add(cell)
        else:
            uncolored.add(cell)
        # Sinon la case est cachée sous un portrait de la timeline ou sous l'interface du bas : ni colorée ni obstacle,
        # inconnue. 15/10, équipe de 8 :
        # la timeline de 9 portraits couvrait le bas de la portée de Lancer de Pièces, ses cases prises pour des blocs,
        # ligne de vue prédite juste sur 55 % des cases et toute la carte crue à relief.
    distances = [distance(c, me.cell) for c in castable | no_sight]
    if not distances:
        return RangeView(castable, no_sight, set(), None, frozenset(void_cells), frozenset(hidden_cells))
    low, reach = min(distances), max(distances)
    ignored = {f.cell for f in fighters} | _hidden_by_sprites(frame, fighters)
    # Cases plus proches que la portée minimale : jamais colorées, ce ne sont pas des obstacles. 11/10, Tempête de
    # Puissance (3 à 4) : les 11 cases à 1 ou 2 de Brawli prises pour des blocs, ligne de vue prédite juste sur 8 % des
    # cases au lieu de 96 %, carte crue à relief (4 cases de marge) : tirs prévus à 6 cases, refusés par le jeu.
    obstacles = {c for c in uncolored if low <= distance(c, me.cell) <= reach} - ignored
    return RangeView(castable, no_sight, obstacles, (low, reach), frozenset(void_cells), frozenset(hidden_cells))


def in_line_only(view: RangeView, me: Cell) -> RangeView:
    """Portée d'un sort « en ligne » : le jeu ne colore que les deux diagonales de l'écran ; les autres cases ne sont pas
    des obstacles. 11/10, Épée Céleste (1 à 3, en ligne) : 10 des 12 « obstacles » lus étaient hors de la ligne."""
    def aligned(cell: Cell) -> bool:
        return abs(cell[0] - me[0]) == abs(cell[1] - me[1])

    return RangeView(view.castable, view.no_sight, {c for c in view.obstacles if aligned(c)}, view.spell_range,
                     view.void, view.hidden)


def _is_floor(bgr: np.ndarray) -> bool:
    """Sol praticable en mode tactique : gris uni, ou case de placement rouge / bleue."""
    b, g, r = (int(v) for v in bgr)
    grey = max(b, g, r) - min(b, g, r) <= 14 and 105 <= max(b, g, r) <= 150
    return grey or (r >= 215 and g <= 40 and b <= 40) or (b >= 215 and g <= 40 and r <= 40)


FLOOR, TOP, DARK, OTHER = "sol", "dessus", "sombre", "autre"


def _shade(bgr: np.ndarray) -> str:
    """Nature d'un pixel de la carte tactique : sol, dessus clair d'un bloc, sombre (côté d'un bloc, paroi d'un trou,
    vide autour de la carte), ou autre chose (texte, interface, sprite)."""
    if _is_floor(bgr):
        return FLOOR
    high, spread = int(bgr.max()), int(bgr.max()) - int(bgr.min())
    if high <= 35 or (high <= 100 and spread <= 30):
        return DARK
    if 160 <= high <= 235 and spread <= 20:
        return TOP
    return OTHER


def _banner_rows(image: np.ndarray, left: int, right: int, min_rows: int) -> tuple[int, int] | None:
    """Lignes du bandeau sombre « Le combat commence ! » : toute la largeur de la carte assombrie d'un coup (bord
    haut), puis éclaircie d'autant au moins `min_rows` lignes plus bas (bord bas). 14/10 sur -1,-19 : 118 lignes, le
    sol passe de 135 à 82 de gris dessous."""
    grey = image[:, left:right:4].mean(axis=2) + 1
    ratio = grey[2:] / grey[:-2]  # ligne y + 2 comparée à la ligne y
    darker = np.flatnonzero((ratio < 0.75).mean(axis=1) > 0.8)
    if not len(darker):
        return None
    top = int(darker[0]) + 2
    lighter = np.flatnonzero((ratio[top + min_rows :] > 1.33).mean(axis=1) > 0.8)
    return (top, top + min_rows + int(lighter[0]) + 1) if len(lighter) else None


@dataclass(frozen=True)
class Terrain:
    floor: set[Cell]  # on peut y marcher
    blocks: set[Cell]  # ni marcher ni voir à travers
    holes: set[Cell]  # trous et vide autour de la carte : pas de marche, mais on tire par-dessus

    @property
    def seen(self) -> set[Cell]:
        return self.floor | self.blocks | self.holes


def read_blocks(frame: Frame, fighters: list[Fighter]) -> set[Cell]:
    return read_terrain(frame, fighters).blocks


def read_terrain(frame: Frame, fighters: list[Fighter]) -> Terrain:
    """Sol, blocs et trous, lus sur la carte tactique sans sort sélectionné.

    Sur la carte « propre » (souris écartée, pas de portée ni de zone de déplacement affichée), le sol est gris uni.
    Un bloc est dessiné en relief : son dessus clair est remonté d'une demi-hauteur de case (ou plus), ses côtés
    sombres descendent jusqu'à sa case. Ce relief recouvre la gauche, la droite et le bas des cases de sol juste
    au-dessus de lui à l'écran, jamais leur haut : seuls deux points du haut de la case (à 0,6 et 0,3 demi-hauteur au-
    dessus du centre) sont lus. Les trous (vide noir, ou fosse aux parois gris foncé) empêchent de marcher mais pas de
    tirer : une case sombre est un trou, sauf si le dessus clair d'un bloc haut commence juste au-dessus (son côté).
    Cases sous un texte, une icône, un sprite, l'interface ou le bandeau de début de combat : non lues.
    Vérifié sur tests/fixtures/combat_mon_tour.png (100 % des lignes de vue de la portée colorée, 99 % avec l'ancienne
    lecture à 4 points autour du centre) et, le 14/10, sur -1,-19 comparée aux cases du client (tools/comparer_carte) :
    accord marche 99 % et ligne de vue 100 % (400 cases lues), contre 90 % et 84 % avec les 4 points (blocs à tort).
    """
    grid = frame.grid
    image = frame.image
    height, width = image.shape[:2]
    left, _, right, bottom = grid.bounds
    banner = _banner_rows(image, max(0, round(left)), min(width, round(right)), round(2 * grid.half_h))
    ignored = {f.cell for f in fighters} | _hidden_by_sprites(frame, fighters)
    floor, blocks, holes = set(), set(), set()
    for cell in grid.cells():
        if cell in ignored:
            continue
        x, y = grid.center(cell)
        px = round(x)
        rows = [round(y - k * grid.half_h) for k in (0.6, 0.3)]
        if not 0 <= px < width or not all(0 <= py < height for py in rows):
            continue
        if banner and banner[0] - 2 <= y + grid.half_h and y - grid.half_h <= banner[1] + 2:
            continue
        shades = {_shade(image[py, px]) for py in rows}
        if shades == {FLOOR}:
            floor.add(cell)
        elif y + 0.6 * grid.half_h > bottom:
            continue  # bas de la case sous l'interface : seul le sol se lit sans doute
        elif shades == {TOP}:
            blocks.add(cell)
        elif shades == {DARK}:
            # Côté d'un bloc haut (son dessus clair commence moins de 2 demi-cases au-dessus du centre), ou paroi d'un
            # trou (au-dessus : le sol, le vide, ou le côté puis le dessus du bloc de la case derrière).
            for k in np.arange(1.2, 4.01, 0.4):
                py = round(y - k * grid.half_h)
                shade = _shade(image[py, px]) if py >= 0 else FLOOR
                if shade != DARK:
                    break
            if shade == OTHER:
                continue
            (blocks if shade == TOP and k < 1.9 else holes).add(cell)
        # Mélange (bord d'un texte, d'une icône, case derrière un bloc haut) : non lue.
    return Terrain(floor, blocks, holes)


def line_of_sight(a: Cell, b: Cell, blockers: set[Cell]) -> bool:
    """Aucune case bloquante sur le segment entre les centres de `a` et `b` (extrémités exclues).

    Points du segment pris depuis `a` (puis décalés de `a`) : un point à égale distance de deux cases tombe toujours
    du même côté, où que soit la ligne sur la carte (même tracé que brain/engine.crossed, calculé une fois par écart)."""
    (i0, j0), (i1, j1) = a, b
    return not any((i0 + di, j0 + dj) in blockers for di, dj in _crossed(i1 - i0, j1 - j0))


@functools.lru_cache(maxsize=4096)
def _crossed(di: int, dj: int) -> tuple[Cell, ...]:
    """Cases traversées par le segment de (0, 0) à (di, dj), extrémités exclues, dans l'ordre (placement de toute
    l'équipe, 17/10 : des centaines de lignes par plan, chacune calculée une fois par écart)."""
    samples = max(abs(di), abs(dj)) * 24
    cells: list[Cell] = []
    for t in np.linspace(0, 1, samples + 1)[1:-1]:
        cell = lattice_cell(di * t, dj * t)
        if cell != (0, 0) and cell != (di, dj) and cell not in cells:
            cells.append(cell)
    return tuple(cells)


def sight_agreement(view: RangeView, me: Cell, blockers: set[Cell]) -> float:
    """Part des cases à portée dont la ligne de vue prédite correspond à l'affichage du jeu (auto-contrôle)."""
    cells = view.castable | view.no_sight
    if not cells:
        return math.nan
    return sum(line_of_sight(me, c, blockers) == (c in view.castable) for c in cells) / len(cells)
