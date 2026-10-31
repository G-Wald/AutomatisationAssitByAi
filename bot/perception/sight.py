"""Portée et ligne de vue, lues sur les cases colorées quand un sort est sélectionné (mode tactique).

- bleu foncé : à portée, avec ligne de vue (le sort part) ;
- bleu clair : à portée, sans ligne de vue ;
- rien dans le losange de portée : obstacle (bloc, trou, bord de carte), sauf si un sprite cache la case.

Les obstacles vus servent à prédire la ligne de vue depuis d'autres cases, pour choisir où se déplacer.
Vérifié sur tests/fixtures/combat_portee_sort.png : 103 cases sur 103 prédites comme le jeu les affiche (102 avant le
18/10) ; le 17/10, sur les 50 captures « ligne_de_vue_a_revoir » d'Incarnam (plateaux et fosses) : 3421 cases sur 3421
(3030 avant de ne plus lire les parois teintées comme des blocs ni compter les coins de case) ; le 18/10, sur les 13 de
la forêt d'Incarnam (piliers et plateaux en relief) : 1396 sur 1396 (1235 sur 1363 avant de lire les blocs à leur dessus).
"""

import functools
import math
from dataclasses import dataclass

import numpy as np

from bot.perception.colors import RANGE_BLUE, near
from bot.perception.fighters import Fighter
from bot.perception.frame import Frame
from bot.perception.grid import CORNER_TOLERANCE, Cell, distance
from bot.perception.layout import MAP_BOTTOM
from bot.perception.timeline import timeline_portraits

NO_SIGHT_BLUE = (137, 117, 95)  # BGR, bleu clair : à portée mais ligne de vue bloquée
VOID_IN_RANGE = (45, 30, 10)  # BGR, bleu nuit : le vide autour de la carte, teinté par la portée (16/10, Incarnam 6,1)
# Boutons du combat au-dessus de la fin de la timeline (mode tactique, cercles…), en repère de référence :
# 18/10, forêt d'Incarnam, la case dessous lue bloc coupait une ligne de vue sur deux captures.
FIGHT_BUTTONS = (1756, 664, 1918, 707)
HIDDEN_CENTER_MARGIN = 0.1  # range_center : avance (indice de Jaccard) qu'il faut à une case cachée sur une case vue
RANGE_SCAN = (2, 6)  # locate_caster : portées essayées sous et au-dessus de l'attendue (malus, bonus d'un allié)
# locate_caster : écart d'indice de Jaccard sous lequel deux centres sur deux de mes cercles sont aussi plausibles l'un
# que l'autre (26/10 : 0,925 contre 0,925 et 0,916 sur un losange coupé ; sur les losanges entiers, le bon centre
# devance le suivant de 0,06 au moins).
AMBIGUOUS_FIT = 0.02
# locate_caster, portée modifiable : plateau de la portée retenue, les portées voisines qui collent à PEAK_FIT près
# (losange coupé : 0,925 puis 0,916 ; losange entier : 0,05 à 0,3 de moins dès la portée voisine), jusqu'à PLATEAU_MAX.
PEAK_FIT = 0.03
PLATEAU_MAX = 20


@dataclass(frozen=True)
class RangeView:
    castable: set[Cell]
    no_sight: set[Cell]
    obstacles: set[Cell]
    spell_range: tuple[int, int] | None  # distances min et max des cases à portée
    void: frozenset[Cell] = frozenset()  # vide autour de la carte teinté bleu nuit : dans la portée, mais pas une case
    hidden: frozenset[Cell] = frozenset()  # sous la timeline ou l'interface du bas : on ne sait pas
    walls: frozenset[Cell] = frozenset()  # paroi d'un plateau ou d'une fosse, teintée par la portée : pas un obstacle


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
                 exclude: set[Cell] | frozenset[Cell] = frozenset(), in_line: bool = False) -> Cell | None:
    """Case du lanceur d'après la portée affichée : celle dont le losange de portée (`spell_range`) recouvre le mieux
    les cases colorées (indice de Jaccard), même si son cercle est caché (03/10 : sous la timeline). `exclude` : cases
    jamais colorées (trous, blocs, vide autour de la carte), retirées du losange attendu : sinon un losange coupé par
    un bord décale le centre. `in_line` : sort « en ligne », seules les deux diagonales de l'écran sont colorées (une
    croix, pas un losange : 19/10, Flèche de Recul des Crâs, 117 repérages ratés). None si aucune ne colle à
    `min_match` près."""
    best, score = range_fit(frame, spell_range, exclude, in_line)
    return best if score >= min_match else None


def range_fit(frame: Frame, spell_range: tuple[int, int], exclude: set[Cell] | frozenset[Cell] = frozenset(),
              in_line: bool = False, view: RangeView | None = None) -> tuple[Cell | None, float]:
    """range_center sans seuil : (meilleur centre, son indice de Jaccard) ; (None, 0) sans portée affichée. Pour
    comparer plusieurs portées possibles (bonus de portée d'un allié, inconnu du lanceur : 18/10, voir combat.py).
    `view` : la portée déjà lue sur cette capture (plusieurs portées comparées sans la relire)."""
    view = view or read_range(frame, Fighter("", -1e4, -1e4, (-10_000, -10_000)), [])
    if not (view.castable | view.no_sight):
        return None, 0.0
    # 16/10, Incarnam 6,1 : la portée de Lancer de Pièces débordait sur le vide (bleu nuit) et sous la timeline ; le
    # losange coupé de deux côtés plaçait le lanceur une case trop haut, au-dessus du vide : 7 tirs refusés, 43 s. Le
    # vide teinté fait partie du losange ; ce qui est caché en est retiré.
    colored = view.castable | view.no_sight | view.void
    exclude = set(exclude) | view.hidden | view.walls
    cells = [c for c in frame.grid.cells() if c not in exclude or c in colored]
    grid = np.array(cells)
    is_colored = np.array([c in colored for c in cells])
    low, high = spell_range
    mean = grid[is_colored].mean(axis=0)

    def best_of(candidates: np.ndarray) -> tuple[Cell | None, float]:
        best, score = None, 0.0
        for center in candidates[np.abs(candidates - mean).max(axis=1) <= high]:  # losange coupé : centre décalé
            delta = np.abs(grid - center)
            gap = delta.max(axis=1)
            diamond = (gap >= low) & (gap <= high)
            if in_line:
                diamond &= delta[:, 0] == delta[:, 1]  # comme in_line_only
            match = (diamond & is_colored).sum() / (diamond | is_colored).sum()
            if match > score:
                best, score = (int(center[0]), int(center[1])), float(match)
        return best, score

    best, score = best_of(grid)
    # Le centre peut être une case cachée : 18/10, forêt d'Incarnam, lanceur juste au-dessus de la timeline, sa case
    # comptée sous les portraits ; le centre trouvé une case à côté désignait un autre cercle (3 captures « ligne de vue
    # à revoir » sur 13). Seulement s'il colle nettement mieux : le losange d'une case cachée, à moitié retiré, est
    # moins pénalisé quand la portée attendue est trop grande (0,63 contre 0,57 pour la bonne case sur une capture,
    # 0,91 à 0,93 contre 0,73 à 0,77 pour les vrais lanceurs cachés).
    if view.hidden:
        hidden_best, hidden_score = best_of(np.array(sorted(view.hidden)))
        if hidden_score >= score + HIDDEN_CENTER_MARGIN:
            best, score = hidden_best, hidden_score
    return best, score


@dataclass(frozen=True)
class Located:
    """Lanceur trouvé par sa portée affichée (locate_caster)."""

    cell: Cell
    spell_range: tuple[int, int]  # portée qui colle (bonus d'un allié compris)
    hidden: bool  # aucun de mes cercles dessus : cercle caché (timeline, corps d'un combattant devant)


def locate_caster(frame: Frame, spell_range: tuple[int, int], mine: set[Cell], fighters: list[Fighter],
                  exclude: set[Cell] | frozenset[Cell] = frozenset(), in_line: bool = False,
                  modifiable: bool = False, min_match: float = 0.5) -> Located | None:
    """Ma case d'après la portée affichée de mon sort sélectionné, seulement si c'est une preuve (19/10) :
    1. le centre pour la portée attendue est sur un de mes cercles (`mine`). Pas à une case près : 18/10, 5 centres
       sur une case vide voisine du cercle d'un allié ; les 46 bons centres de 51 captures étaient pile sur le cercle ;
    2. sinon, portée modifiable : un bonus ou un malus que le lanceur ignore (18/10 : Pelle Fantomatique attendue 0 à 6,
       affichée 0 à 7 ou 8 ; 5 centres sur une case vide, sur 19 captures). Portées voisines (RANGE_SCAN), gardée
       celle dont le centre est sur un de mes cercles et qui colle le mieux ;
    3. sinon, le centre de la portée attendue, si un cercle peut s'y cacher : sous la timeline ou l'interface du bas
       (03/10, 18/10), derrière le corps d'un combattant debout devant.
    None sinon : un centre sur une case vide, où rien ne cache de cercle, n'est pas le lanceur.

    Jamais quand une autre portée voisine place le centre sur un AUTRE de mes cercles, aussi bien ou mieux
    (AMBIGUOUS_FIT) : 26/10 (journal 190101), losange coupé par deux bords de la carte, portées 11 à 15 toutes à 0,92,
    chacune centrée sur le cercle d'un allié différent (équipe serrée en diagonale) ; Elseiso s'est pris pour Pesopepe,
    2 déplacements ratés, et 3 personnages se sont crus sur la même case dans un autre combat."""
    low, high = spell_range
    view = read_range(frame, Fighter("", -1e4, -1e4, (-10_000, -10_000)), [])
    fits = {high: range_fit(frame, spell_range, exclude, in_line, view)}
    if modifiable:
        # Portée modifiable (bonus ou malus inconnus) : les portées voisines disent aussi si le losange, coupé par un
        # bord, irait aussi bien à un autre cercle. Une portée fixe est connue : son centre fait foi.
        for reach in range(max(low, high - RANGE_SCAN[0]), high + RANGE_SCAN[1] + 1):
            if reach != high:
                fits[reach] = range_fit(frame, (low, reach), exclude, in_line, view)

    def rivals(cell: Cell, fit: float) -> list[Cell]:
        """Autres de mes cercles aussi plausibles : centre d'une portée essayée qui colle aussi bien (AMBIGUOUS_FIT), ou
        sur le plateau de la portée retenue : losange coupé par un bord, les portées plus longues (ou plus courtes)
        collent presque aussi bien (PEAK_FIT), leur centre glissant d'une case à chaque fois, au-delà de RANGE_SCAN au
        besoin (26/10 : 0,925 à la portée 11 sur Pesopepe, 0,916 à 12 et 13 sur des cases vides, 0,925 à 14 et 15 sur
        deux autres alliés). Un plateau qui ne passe que sur des cases vides ne dit rien contre la portée retenue."""
        found = {c for c, f in fits.values() if c in mine and c != cell and f >= fit - AMBIGUOUS_FIT}
        if modifiable:
            reach = next(r for r, (c, f) in fits.items() if c == cell and f == fit)
            for step in (-1, 1):
                other = reach + step
                while max(low, 1) <= other <= PLATEAU_MAX:
                    if other not in fits:
                        fits[other] = range_fit(frame, (low, other), exclude, in_line, view)
                    c, f = fits[other]
                    if c is None or f < fit - PEAK_FIT:
                        break
                    if c in mine and c != cell:
                        found.add(c)
                    other += step
        return sorted(found)

    center, score = fits[high]
    if score < min_match:
        center = None
    if center is not None and center in mine:
        if not rivals(center, score):
            return Located(center, spell_range, False)
        return None
    if modifiable:
        tries = [(fit, cell, reach) for reach, (cell, fit) in fits.items()
                 if reach != high and fit >= min_match and cell in mine]
        if tries:
            fit, cell, reach = max(tries)
            if rivals(cell, fit):
                return None
            return Located(cell, (low, reach), False)
    if center is not None:
        if center in view.hidden or center in _hidden_by_sprites(frame, fighters):
            return Located(center, spell_range, True)
    return None


def _tinted_wall(bgr: np.ndarray) -> bool:
    """Paroi (côté gris sombre d'un plateau ou d'une fosse) teintée par la portée : bleu sombre, le bleu 22 à 50 au-dessus
    du rouge, le vert 8 à 30. 17/10, Incarnam : (59, 44, 24) et (82, 66, 50) sur les deux faces d'un plateau, (92, 75,
    60) et (103, 88, 66) dans une fosse. Ni le bleu nuit du vide (45, 30, 10), ni le bleu clair « sans ligne de vue »
    (137, 117, 95), ni le sol gris."""
    b, g, r = (int(v) for v in bgr)
    return 22 <= b - r <= 50 and 8 <= g - r <= 30 and 55 < b <= 125


def _foreign(bgr: np.ndarray) -> bool:
    """Ni terrain nu (sol, dessus ou côté gris d'un bloc) ni paroi teintée : texte, icône, interface posés sur la case
    (le rouge pur du curseur « interdit » compris, que _shade prend pour une case de placement)."""
    return not _tinted_wall(bgr) and (_shade(bgr) == OTHER or int(bgr.max()) - int(bgr.min()) > 30)


def _raised_top(image: np.ndarray, grid, cell: Cell, hidden) -> bool:
    """Bloc en relief sur `cell` : son dessus clair, jamais teinté par la portée, remonté d'une demi-hauteur de case
    (losange centré une demi-hauteur au-dessus du centre de la case). Trois points sur quatre de ce dessus suffisent
    (le quatrième peut tomber sous un texte de dégâts)."""
    x, y = grid.center(cell)
    height, width = image.shape[:2]
    points = [(round(x + dx * grid.half_w), round(y + dy * grid.half_h)) for dx, dy in ((0, -0.6), (0, -0.3), (-0.5, -1), (0.5, -1))]
    if not all(0 <= px < width and 0 <= py < height for px, py in points) or any(hidden(px, py) for px, py in points):
        return False
    return sum(_shade(image[py, px]) == TOP for px, py in points) >= 3


def _in_relief(grid, raised: set[Cell], px: float, py: float) -> bool:
    """Point de l'écran caché par le relief d'un bloc de `raised` : son dessus (remonté d'une demi-hauteur) et ses
    côtés, de sommets (0, -2), (±1, -1), (±1, 0) et (0, 1) en demi-cases autour du centre de sa case."""
    for cell in raised:
        x, y = grid.center(cell)
        dx, dy = abs(px - x) / grid.half_w, (py - y) / grid.half_h
        if dx <= 1 and dx - 2 <= dy <= 1 - dx:
            return True
    return False


def read_range(frame: Frame, me: Fighter, fighters: list[Fighter]) -> RangeView:
    grid = frame.grid
    image = frame.day_image  # couleurs du jour : 26/10, la nuit sortait le bleu de la portée de sa tolérance
    dark = near(image, RANGE_BLUE, 25)
    light = near(image, NO_SIGHT_BLUE, 22)
    # Vide autour de la carte (noir, ou bleu nuit dans la portée) : un trou, on tire par-dessus ; pas un obstacle.
    # 16/10, Incarnam 6,1 : plus de cent cases du vide prises pour des blocs, lignes de vue justes sur 57 à 70 %.
    tinted = near(image, VOID_IN_RANGE, 14)
    void = tinted | (image.max(axis=2) <= 35)
    height, width = dark.shape
    castable, no_sight, uncolored, void_cells, hidden_cells, wall_cells = set(), set(), set(), set(), set(), set()
    portraits = timeline_portraits(frame)
    # La rangée des portraits, jusqu'au bord droit (flèches de défilement), et le bas de la carte sous l'interface.
    row = (min(b[0] for b in portraits), min(b[1] for b in portraits), max(b[3] for b in portraits)) if portraits else None
    map_bottom = frame.layout.y(MAP_BOTTOM) - frame.layout.length(2)
    buttons = frame.layout.box(*FIGHT_BUTTONS)

    def hidden(px: int, py: int) -> bool:
        return (py >= map_bottom or (row is not None and px >= row[0] and row[1] <= py <= row[2])
                or (buttons[0] <= px < buttons[2] and buttons[1] <= py < buttons[3]))

    raised = {cell for cell in grid.cells() if cell != me.cell and _raised_top(image, grid, cell, hidden)}
    for cell in grid.cells():
        if cell == me.cell or cell in raised:
            continue
        x, y = grid.center(cell)
        points = [(round(x + dx * grid.half_w), round(y + dy * grid.half_h)) for dx, dy in ((-0.6, 0), (0.6, 0), (0, -0.6), (0, 0.6))]
        points = [(px, py) for px, py in points if 0 <= px < width and 0 <= py < height]
        # 18/10, forêt d'Incarnam : le relief d'un bloc (dessus et côtés) cache le bas des cases juste au-dessus de lui
        # à l'écran ; leurs points cachés ne disent rien de leur couleur. Une telle case n'est jamais un obstacle.
        seen = [(px, py) for px, py in points if not _in_relief(grid, raised, px, py)]
        behind = len(seen) < len(points)
        points = seen
        if len(points) >= 2 and sum(dark[py, px] for px, py in points) >= min(3, len(points)):
            castable.add(cell)
        elif sum(light[py, px] for px, py in points) >= 2:
            no_sight.add(cell)
        elif sum(hidden(px, py) for px, py in points) >= 2:
            hidden_cells.add(cell)
        elif behind:
            continue
        elif sum(void[py, px] for px, py in points) >= 2:
            if sum(tinted[py, px] for px, py in points) >= 2:
                void_cells.add(cell)
        elif sum(_tinted_wall(image[py, px]) for px, py in points) >= 3:
            wall_cells.add(cell)
        elif sum(not dark[py, px] and not light[py, px] and _foreign(image[py, px]) for px, py in points) < 2:
            uncolored.add(cell)
        # Cachée sous un portrait de la timeline ou sous l'interface du bas : ni colorée ni obstacle, inconnue. 15/10,
        # équipe de 8 : la timeline de 9 portraits couvrait le bas de la portée de Lancer de Pièces, ses cases prises
        # pour des blocs, ligne de vue prédite juste sur 55 % des cases et toute la carte crue à relief.
        # 17/10, Incarnam (cartes à plateaux et fosses, 50 captures « ligne_de_vue_a_revoir ») : 416 des 431 obstacles
        # lus étaient des parois, côtés d'une case plus haute dessinés sur la case de devant et teintés comme elle par
        # la portée (bleu sombre) ; ils coupaient les lignes de vue vers les plateaux et le fond des fosses, que le jeu
        # montrait à portée. Un obstacle, lui, n'est jamais teinté : un bloc montre son dessus clair et ses côtés
        # sombres (parfois deux points bleus de la case voisine, au bord de la portée). Deux points ou plus d'une
        # autre couleur (texte vert des dégâts, curseur « interdit », flèche orange de la timeline, sur 6 captures) :
        # case couverte, ni colorée ni obstacle (laissée dans le losange attendu par range_center, comme avant : l'en
        # retirer décalait le lanceur trouvé).
    distances = [distance(c, me.cell) for c in castable | no_sight]
    if not distances:
        return RangeView(castable, no_sight, set(), None, frozenset(void_cells), frozenset(hidden_cells),
                         frozenset(wall_cells))
    low, reach = min(distances), max(distances)
    ignored = {f.cell for f in fighters} | _hidden_by_sprites(frame, fighters)
    # Cases plus proches que la portée minimale : jamais colorées, ce ne sont pas des obstacles. 11/10, Tempête de
    # Puissance (3 à 4) : les 11 cases à 1 ou 2 de Brawli prises pour des blocs, ligne de vue prédite juste sur 8 % des
    # cases au lieu de 96 %, carte crue à relief (4 cases de marge) : tirs prévus à 6 cases, refusés par le jeu.
    obstacles = {c for c in uncolored if low <= distance(c, me.cell) <= reach} - ignored
    # Bloc reconnu à son dessus, même sous le corps d'un combattant posé derrière (18/10 : un pilier caché ainsi par
    # l'ennemi de la case du dessous, oublié, laissait voir 4 cases que le jeu montrait sans ligne de vue).
    obstacles |= {c for c in raised if distance(c, me.cell) <= reach} - {f.cell for f in fighters}
    return RangeView(castable, no_sight, obstacles, (low, reach), frozenset(void_cells), frozenset(hidden_cells),
                     frozenset(wall_cells))


def in_line_only(view: RangeView, me: Cell) -> RangeView:
    """Portée d'un sort « en ligne » : le jeu ne colore que les deux diagonales de l'écran ; les autres cases ne sont pas
    des obstacles. 11/10, Épée Céleste (1 à 3, en ligne) : 10 des 12 « obstacles » lus étaient hors de la ligne."""
    def aligned(cell: Cell) -> bool:
        return abs(cell[0] - me[0]) == abs(cell[1] - me[1])

    return RangeView(view.castable, view.no_sight, {c for c in view.obstacles if aligned(c)}, view.spell_range,
                     view.void, view.hidden, view.walls)


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
    image = frame.day_image  # couleurs du jour (sol, dessus et côtés des blocs) : la nuit teinte la carte (26/10)
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

    Tracé pris depuis `a` (puis décalé de `a`), le même où que soit la ligne sur la carte (même tracé que
    brain/engine.crossed, calculé une fois par écart). Une case que le segment ne fait que toucher par un coin ne
    bloque pas (voir _crossed)."""
    (i0, j0), (i1, j1) = a, b
    return not any((i0 + di, j0 + dj) in blockers for di, dj in _crossed(i1 - i0, j1 - j0))


@functools.lru_cache(maxsize=4096)
def _crossed(di: int, dj: int) -> tuple[Cell, ...]:
    """Cases traversées par le segment de (0, 0) à (di, dj), extrémités exclues, dans l'ordre (placement de toute
    l'équipe, 17/10 : des centaines de lignes par plan, chacune calculée une fois par écart).

    Un point du segment à égale distance de deux cases est sur leur bord : il ne compte pour aucune. Une case
    traversée a toujours d'autres points à elle ; une case touchée par un seul coin n'en a pas et ne bloque pas.
    17/10, Incarnam : une ligne horizontale à l'écran passe par les coins des cases au-dessus et au-dessous d'elle ;
    la case du dessous était comptée (égalité tranchée d'un seul côté), et un combattant posé là coupait 70 lignes de
    vue sur 50 captures que le jeu montrait dégagées (aucune ligne bloquée par un coin dans ces captures)."""
    samples = max(abs(di), abs(dj)) * 24
    cells: list[Cell] = []
    for t in np.linspace(0, 1, samples + 1)[1:-1]:
        a, b = di * t, dj * t
        i, j = math.floor(a), math.floor(b)
        near_cells = sorted((abs(a - ci) + abs(b - cj), (ci, cj)) for ci in (i, i + 1) for cj in (j, j + 1)
                            if (ci + cj) % 2 == 0)
        if near_cells[1][0] - near_cells[0][0] <= CORNER_TOLERANCE:
            continue
        cell = near_cells[0][1]
        if cell != (0, 0) and cell != (di, dj) and cell not in cells:
            cells.append(cell)
    return tuple(cells)


def sight_agreement(view: RangeView, me: Cell, blockers: set[Cell]) -> float:
    """Part des cases à portée dont la ligne de vue prédite correspond à l'affichage du jeu (auto-contrôle)."""
    cells = view.castable | view.no_sight
    if not cells:
        return math.nan
    return sum(line_of_sight(me, c, blockers) == (c in view.castable) for c in cells) / len(cells)
