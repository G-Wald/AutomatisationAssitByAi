"""Moteur de tour : où aller, quels sorts lancer sur quoi et dans quel ordre, puis où finir le tour.

Logique pure (pas d'écran, testable), valable pour toute classe : elle ne lit que les champs de CombatSpell, tirés
de la référence Solomonk (PA, portée, lancer en ligne, ligne de vue, portée modifiable, lancers par tour et par cible,
relance, dégâts, zone, poussée, bonus de portée).

Un seul style de combat, « polyvalent » (03/10, demande de l'utilisateur : plus d'agressif ni de prudent) : finir le
combat vite en tapant toujours un ennemi quand c'est possible, en restant assez à l'abri, et en équipe : ne pas se
coller aux alliés, ne pas s'aligner derrière eux (« file indienne »), ne pas couper leurs lignes de vue.

Pour chaque case atteignable avec les PM (rester sur place compris) :
1. la meilleure suite de sorts lancés depuis cette case, dans les PA : lancers par tour et par cible, relance,
   portée (+ bonus de portée pour les sorts à portée modifiable, valable dès le tour où il est lancé), lancer en
   ligne, ligne de vue prédite ; les sorts de bonus (portée, dégâts) avant toute attaque du tour, et suivis d'une
   attaque : un bonus de dégâts augmente les dégâts des sorts lancés après lui (« si on a la portée pour taper,
   autant booster les dégâts », 04/10), et vaut un peu pour les tours suivants qu'il dure ;
   zones (point, cercle, croix, ligne, ligne perpendiculaire, anneau, damier) : une zone peut viser une case vide
   (juste devant deux ennemis alignés, entre trois ennemis) ; un sort qui toucherait un allié ou moi n'est pas
   lancé ; recul simulé : l'ennemi poussé change de case pour les sorts suivants, poussée contre un obstacle ou un
   combattant = dégâts de poussée, ennemi proche repoussé = danger écarté ; vie des ennemis connue (lue au survol,
   par moi ou un allié) : dégâts au-delà de sa vie perdus, ennemi achevé = bonus, l'ennemi le plus entamé (la cible
   de l'équipe) vaut un peu plus ;
2. avec les PM restants, un repli éventuel : hors du contact d'un ennemi, hors des lignes de vue des alliés, pas
   collé à eux, d'où un ennemi reste à portée au tour suivant.
Score = dégâts attendus + bonus − pénalités, en points de dégâts ; la case de fin de tour est jugée par _state
(contact, pas à faire au tour suivant pour toucher, jeu d'équipe, distance). Plan retenu : [déplacement] + sorts +
[repli].

Si aucun sort ne touche ce tour (ou que toucher coûterait plus que ça ne rapporte) : placement pour le tour suivant
(_position), avec tous les PM utiles d'un coup : la case d'où un ennemi sera le plus vite à portée et en vue,
lignes de vue dégagées des alliés (pas de côté plutôt que d'avancer derrière eux), hors du contact, et à égalité la
plus loin des ennemis.

combat.py n'exécute que la première étape, relit l'écran et replanifie (« horizon glissant ») : PA, morts, cibles
refusées, portées mesurées changent en cours de tour.

Déplacement : un pas = une case voisine (1 PM). On peut entrer dans une case au contact d'un ennemi mais pas en
repartir dans le même déplacement (en Rétro, quitter le contact expose au tacle) : seule la case de départ fait
exception, avec la pénalité de tacle.
"""

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from bot.brain.tactics import (
    DANGER_DISTANCE,
    PUSH_BLOCKED_BONUS,
    CombatSpell,
    SpellUse,
    aligned,
    reach_range,
    spell_range,
    unit,
)
from bot.perception.grid import Cell, distance

NEIGHBOURS = ((1, 1), (1, -1), (-1, 1), (-1, -1))  # un pas = une case voisine (1 PM)

# Poids du score, en points de dégâts (01/10, demande de l'utilisateur : comparer dégâts et déplacements).
CONTACT_PENALTY = 10.0  # finir son tour au contact d'un ennemi : y aller seulement pour bien plus de dégâts
TACKLE_PENALTY = 5.0  # quitter une case au contact d'un ennemi : tacle possible (PM perdus, on reste au contact)
MOVE_COST = 1.0  # par PM dépensé : ne bouger que pour un vrai gain (01/10 : 0,5, trop de déplacements inutiles)
# Par déplacement, en plus de ses PM : un clic, puis ~1 s d'attente de l'arrivée. Un pas de plus après les sorts doit
# rapporter plus que le temps qu'il prend : ne plus être collé à un allié ou ne plus couper sa ligne de vue, oui ; se
# rapprocher d'1 case d'un ennemi qu'on vient de repousser, non (03/10, essai à 0,5 : le Crâ revenait vers lui).
STEP_COST = 0.75
# Toucher un ennemi vaut toujours mieux que ne rien faire (03/10, « il faut quand même taper un ennemi ») : un coup
# de 6 dégâts au corps à corps se tente, en gardant 1 PM pour sortir du contact s'il en reste (tacle 5 + 1 point de
# déplacement, moins que le contact 10) ; un coup de 2 ou 3 dégâts ne vaut pas de finir le tour au contact (3 + 5 < 10).
HIT_BONUS = 5.0
# Ennemi achevé (sa vie lue au survol, la mienne ou celle d'un allié, MapMemory.life) : il ne jouera plus, ni contre
# nous ni pour retarder la fin du combat. Autant qu'un sort moyen (Flèche Magique 13, Ronce 13,5) : un sort plus faible
# qui tue passe devant un plus fort qui ne tue pas (8 + 10 > 13).
KILL_BONUS = 10.0
# Cible de l'équipe : l'ennemi à la vie connue la plus basse. Ses dégâts comptent 25 % de plus : à dégâts proches, toute
# l'équipe tape le même (Ronce 13,5 sur lui = 16,9 > 15 ailleurs), sans préférer un coup bien plus faible.
FOCUS_SHARE = 0.25
# Tour suivant : par pas qu'il faudra faire avant d'avoir un ennemi à portée et en vue (alliés compris). Remplace
# l'ancien bonus « à portée au tour suivant » (1 point) : même écart entre « à portée » et « à 1 pas », mais il compte
# aussi les pas suivants, ce qui guide le placement quand rien ne touche (03/10 : le Sadida a avancé d'1 PM derrière
# deux alliés au lieu de se décaler).
NEXT_TURN = 1.0
NEXT_TURN_CAP = 8  # pas comptés au plus (deux tours de marche ; au-delà, la distance à l'ennemi départage : calcul
# limité aux cases proches, 03/10 : 12 pas coûtaient 60 ms de plus à 6 PM)
# PM qui ne servent plus à lancer ce tour (repli après les sorts, placement pour le tour suivant quand rien ne touche) :
# ils ne serviraient à rien d'autre ; un petit coût par PM départage seulement les cases équivalentes (moins de PM).
# Inférieur à NEXT_TURN : chaque pas vers une case d'où taper vaut d'être fait (03/10 : 1 PM sur 3 utilisé, rien au tour
# suivant) ; avec STEP_COST, un pas de côté pour ne plus être collé à un allié se fait (1,5 > 0,25 + 0,75).
SPARE_PM_COST = 0.25
# Jeu d'équipe (03/10 : 4 personnages, bientôt 8 ; le Sadida s'est collé aux autres, en file derrière eux).
# Par allié collé à ma case : une zone ennemie nous prend à deux, on lui bouche un passage, et souvent on s'aligne avec
# lui. Assez pour valoir un pas de côté à la fin du tour (1,5 > 0,25 + 0,75), pas pour renoncer à un coup.
ALLY_ADJACENT = 1.5
BEHIND_ALLY = 1.0  # par ennemi à ma portée dont la ligne de vue passe par un allié (file indienne), BEHIND_CAP au plus
BEHIND_CAP = 2
# Par ligne de vue d'un allié vers un ennemi que ma case coupe : il ne pourra peut-être pas taper à son tour. Plus qu'un
# allié collé, mais toujours moins qu'un coup (dégâts + HIT_BONUS) : on coupe une ligne plutôt que de ne pas taper.
CUT_ALLY = 2.0
ALLY_REACH = 10  # portée supposée des sorts d'un allié (on ne les connaît pas) : lignes plus longues ignorées
# À l'abri : par case entre moi et l'ennemi le plus proche, jusqu'à SAFE_CAP cases (ou la portée de mon sort qui porte
# le plus loin, si elle est plus courte) : au-delà, un monstre de 3 ou 4 PM n'arrive pas au contact en un tour, et finir
# au bout de sa portée n'apporte rien (03/10 : sur une carte à relief, la portée lue au bout était fausse). Ne fait que
# départager des cases équivalentes (moins qu'un PM : jamais de recul pour lui seul).
SAFE_DISTANCE = 0.2
SAFE_CAP = 5
# Carte à relief : les prévisions (portée, ligne de vue, obstacles) ne valent pas depuis une autre case ; s'y rendre
# pour un tir prévu y est un pari (01/10 : 3 déplacements dans un tour, plan changé à chaque case).
UNRELIABLE_MOVE_FACTOR = 2.0
# Dégâts de poussée : par case de recul empêchée par un obstacle ou un combattant (Recul bloqué net : 3 x 2 = le bonus
# validé le 27/09) ; comptés seulement si les obstacles sont bien connus (carte plate) : le 01/10, sur une carte à
# relief, ce bonus a fait préférer Flèche Enflammée (9 prévus) à Flèche Magique (13).
PUSH_PER_CELL = PUSH_BLOCKED_BONUS / 3
# Carte à relief, depuis ma case : une cible hors de la portée du sort mais dans la marge (Board.margin) n'est qu'une
# chance (mes distances y sont fausses de quelques cases) : sa valeur compte pour moitié. Le 03/10, Flèche Enflammée
# (15, portée 6) à 10 cases essayée avant Flèche Magique (13, portée 12) : refusée, une seconde perdue.
MARGIN_ODDS = 0.5
LOW_MARGIN = 1  # carte à relief : marge sur la portée MINIMALE, bien plus petite (le jeu l'applique au plus juste)
# Zone dégressive : -10 % de dégâts par case d'éloignement de la case visée (100 % sur elle). Règle publiée pour
# Dofus 2 (aucune source Rétro trouvée), validée par l'utilisateur le 01/10 ; le chat du 01/10 va dans ce sens : 13 PDV
# pour un Bouftou 1 case derrière la case visée par Flèche Enflammée (14 à 16 de base). Réglage : combat.zone_falloff.
ZONE_FALLOFF = 0.10
PUSH_AWAY = 3.0  # par case gagnée en repoussant un ennemi à `danger` cases ou moins : Recul prioritaire (27/09)
RANGE_BONUS_VALUE = 1.0  # bonus de portée suivi d'une attaque : il vaut encore pour les tours suivants
# Bonus de dégâts qui dure plus d'un tour : par tour de plus (2 au plus), cette part de ce qu'il ajoute à un lancer de
# ma meilleure attaque (on n'est pas sûr de taper au tour suivant). Le tour du lancer, il vaut ce qu'il ajoute aux
# attaques qui le suivent : Maîtrise de l'Arc (+40) avant deux Flèches Magiques, c'est +80 tout de suite.
BOOST_CARRY = 0.5
MAX_CASTS = 6  # sorts au plus dans un plan (12 PA en sorts à 2 PA)
ZONE_SEARCH = 4  # cases vides visées par une zone : à 4 cases au plus d'un ennemi (au-delà, viser l'ennemi suffit)
EPSILON = 1e-6

MOVE, CAST, RETREAT, POSITION = "deplacement", "sort", "repli", "placement"


@dataclass(frozen=True)
class Weights:
    contact: float = CONTACT_PENALTY
    tackle: float = TACKLE_PENALTY
    move: float = MOVE_COST
    step: float = STEP_COST
    danger: int = DANGER_DISTANCE
    push_away: float = PUSH_AWAY
    next_turn: float = NEXT_TURN
    range_bonus: float = RANGE_BONUS_VALUE
    boost_carry: float = BOOST_CARRY
    zone_falloff: float = ZONE_FALLOFF  # part des dégâts perdue par case entre la case visée et l'ennemi touché
    hit: float = HIT_BONUS
    kill: float = KILL_BONUS
    focus: float = FOCUS_SHARE
    spare_pm: float = SPARE_PM_COST
    ally_adjacent: float = ALLY_ADJACENT
    behind_ally: float = BEHIND_ALLY
    cut_ally: float = CUT_ALLY
    safe: float = SAFE_DISTANCE


@dataclass(frozen=True)
class Board:
    """Ce qu'on sait de la carte du combat."""

    cells: frozenset[Cell]
    unwalkable: frozenset[Cell] = frozenset()  # blocs et trous : pas de marche, une poussée s'y arrête
    obstacles: frozenset[Cell] | None = frozenset()  # blocs qui coupent la ligne de vue ; None : inconnus (pas prédite)
    # Carte à relief (reliable faux) : depuis ma case, `margin` cases de marge sur la portée et pas de ligne de vue
    # prédite, le jeu tranche (comme avant le moteur) ; depuis une autre case, prévisions sans marge.
    reliable: bool = True
    margin: int = 0
    # Jamais vues (cachées par le corps d'un combattant), peut-être des trous : pas de marche ; une poussée vers elles
    # n'est pas comptée comme empêchée (on n'en sait rien).
    unseen: frozenset[Cell] = frozenset()


@dataclass(frozen=True)
class Step:
    kind: str  # MOVE, CAST, RETREAT ou POSITION
    cell: Cell  # destination, ou case visée (ma case pour un sort sur moi)
    spell: CombatSpell | None = None
    pm: int = 0  # PM du déplacement
    hits: tuple[Cell, ...] = ()  # cases des ennemis touchés (avant le sort)
    damage: float = 0.0
    value: float = 0.0  # dégâts + dégâts de poussée (le danger écarté compte pour la suite entière)
    reason: str = ""

    def describe(self) -> str:
        if self.kind == MOVE:
            return f"aller en {self.cell} ({self.pm} PM)"
        if self.kind == RETREAT:
            return f"repli en {self.cell} ({self.pm} PM)"
        if self.kind == POSITION:
            return f"me placer en {self.cell} pour le prochain tour ({self.pm} PM)"
        if self.spell.on_self:
            return f"{self.spell.name} sur moi"
        on = f"sur {self.cell}" if self.cell in self.hits else f"sur la case {self.cell}"
        return f"{self.spell.name} {on}" + (f" ({len(self.hits)} ennemis touchés)" if len(self.hits) > 1 else "")


@dataclass(frozen=True)
class TurnPlan:
    steps: tuple[Step, ...] = ()
    score: float = 0.0
    can_hit: bool = False  # un sort de dégâts touche depuis au moins une case atteignable (même s'il n'est pas retenu)
    sequences: int = 0  # suites de sorts évaluées (journal)

    @property
    def first(self) -> Step | None:
        return self.steps[0] if self.steps else None

    @property
    def casts(self) -> list[Step]:
        return [s for s in self.steps if s.kind == CAST]

    @property
    def damage(self) -> float:
        return sum(s.damage for s in self.steps)

    def rest(self) -> "TurnPlan":
        """Ce qu'il restera à faire après la première étape."""
        return TurnPlan(self.steps[1:], self.score, self.can_hit)

    def describe(self) -> str:
        if not self.steps:
            return "Plan : rien d'utile à faire d'ici"
        text = "Plan : " + ", ".join(s.describe() for s in self.steps)
        return text + (f" ; dégâts prévus {self.damage:.0f}" if self.casts else "")


def walk_steps(start: Cell, pm: int, cells: frozenset[Cell] | set[Cell], blocked: set[Cell] | frozenset[Cell],
               stops: set[Cell] | frozenset[Cell] = frozenset()) -> dict[Cell, int]:
    """PM pour aller sur chaque case atteignable en au plus `pm` pas (départ compris, à 0), sans traverser de case
    bloquée ; on entre dans une case de `stops` (au contact d'un ennemi) sans en repartir, sauf du départ."""
    steps = {start: 0}
    frontier = [start]
    for n in range(1, pm + 1):
        reached = []
        for cell in frontier:
            if cell in stops and cell != start:
                continue
            for di, dj in NEIGHBOURS:
                nxt = (cell[0] + di, cell[1] + dj)
                if nxt in cells and nxt not in blocked and nxt not in steps:
                    steps[nxt] = n
                    reached.append(nxt)
        frontier = reached
    return steps


def reachable_cells(me: Cell, pm: int, cells: set[Cell], blocked: set[Cell]) -> set[Cell]:
    """Cases atteignables en au plus `pm` pas, sans traverser de case bloquée (obstacle, trou, combattant)."""
    return set(walk_steps(me, pm, cells, blocked)) - {me}


def contact_cells(enemies) -> set[Cell]:
    """Cases voisines d'un ennemi (au contact)."""
    return {(e[0] + di, e[1] + dj) for e in enemies for di, dj in NEIGHBOURS}


@lru_cache(maxsize=1 << 16)
def crossed(a: Cell, b: Cell) -> frozenset[Cell]:
    """Cases traversées par le segment entre les centres de `a` et `b`, extrémités exclues.

    Mêmes échantillons et même arrondi que perception/sight.line_of_sight (vérifié par test), mais calculé une fois
    par paire de cases : la planification teste des milliers de lignes de vue. Le tracé ne dépend que de l'écart
    entre les deux cases (i + j pair partout : arrondi et parité se décalent avec elles) : calculé une fois par
    écart (_crossed_from_origin), puis décalé (04/10 : chaque nouvelle position des combattants le recalculait)."""
    i0, j0 = a
    return frozenset((i0 + di, j0 + dj) for di, dj in _crossed_from_origin(b[0] - i0, b[1] - j0))


@lru_cache(maxsize=None)
def _crossed_from_origin(di: int, dj: int) -> tuple[Cell, ...]:
    """crossed((0, 0), (di, dj)) ; quelques centaines d'écarts possibles sur une carte."""
    samples = max(abs(di), abs(dj)) * 24
    if samples < 2:
        return ()
    t = np.linspace(0, 1, samples + 1)[1:-1]
    x, y = di * t, dj * t
    fx, fy = np.floor(x), np.floor(y)
    even = (fx + fy) % 2 == 0
    # Les deux coins pairs du carré (fx, fy)-(fx+1, fy+1), dans l'ordre où lattice_cell les essaie.
    first_x, first_y = fx, np.where(even, fy, fy + 1)
    second_x, second_y = fx + 1, np.where(even, fy + 1, fy)
    keep_first = np.abs(x - first_x) + np.abs(y - first_y) <= np.abs(x - second_x) + np.abs(y - second_y)
    ci, cj = np.where(keep_first, first_x, second_x), np.where(keep_first, first_y, second_y)
    return tuple(sorted(set(zip(ci.astype(int).tolist(), cj.astype(int).tolist())) - {(0, 0), (di, dj)}))


def in_zone(shape: str, size: int, origin: Cell, target: Cell, cell: Cell) -> bool:
    """La case est-elle dans la zone d'un sort lancé de `origin` sur `target` ?

    Taille codée par le client (comme chez Solomonk) : cercle n = cases à n ou moins de la cible ; croix n = la cible
    et n cases dans chacune des 4 directions ; ligne n = la cible et n cases au-delà, dans l'axe du tir (une ligne ne
    part dans un axe que si le tir est aligné) ; ligne perpendiculaire n = n cases de part et d'autre, en travers du
    tir ; anneau n = cases à exactement n ; damier n = une case sur deux du cercle n. Forme inconnue : la cible.
    Vérifié le 01/10 sur l'aperçu de zone des pages de sorts de Solomonk : ligne « L » = cases j = 0 à n au-delà de
    la cible (Flèche Enflammée « Le », taille 4 : 5 cases), cercle « C » = distance <= n, croix « X » = n par branche."""
    di, dj = cell[0] - target[0], cell[1] - target[1]
    if di == 0 and dj == 0:
        return shape != "anneau" or size == 0
    reach = max(abs(di), abs(dj))
    if size <= 0 or reach > size:
        return False
    if shape == "cercle":
        return True
    if shape == "anneau":
        return reach == size
    if shape == "damier":
        return reach % 2 == 0
    if shape == "croix":
        return abs(di) == abs(dj)
    if shape in ("ligne", "ligne_perpendiculaire") and aligned(origin, target):
        ui, uj = unit(origin, target)
        if shape == "ligne_perpendiculaire":
            uj = -uj
        k = di * ui  # di = k * ui puisque ui vaut ±1
        return dj == k * uj and (k > 0 or shape == "ligne_perpendiculaire")
    return False


SHAPES = ("point", "cercle", "croix", "ligne", "ligne_perpendiculaire", "anneau", "damier")  # celles qu'in_zone connaît


def zone_hits(shape: str, size: int, origin: Cell, target: Cell, cells: tuple[Cell, ...],
              safe: bool = False) -> list[int]:
    """Indices des cases de `cells` dans la zone (in_zone), en raccourci pour la cible seule et le cercle (la
    planification en teste des millions).

    `safe` : pour ne toucher ni un allié ni moi, la zone est prise au plus large quand on ne connaît pas son tracé : une
    forme inconnue compte comme un cercle de sa taille ; une ligne tirée hors des axes (le jeu l'oriente quand même,
    dans une direction qu'in_zone ne prévoit pas) comme les cases de ce cercle pas plus près de moi que la cible (une
    ligne part au-delà de la cible). Pour les ennemis touchés, au plus juste (la cible seule)."""
    t0, t1 = target
    if size <= 0 or shape == "point":
        return [i for i, c in enumerate(cells) if c == target] if shape != "anneau" or size == 0 else []
    if shape == "cercle" or safe and shape not in SHAPES:
        return [i for i, c in enumerate(cells) if abs(c[0] - t0) <= size and abs(c[1] - t1) <= size]
    if safe and shape.startswith("ligne") and not aligned(origin, target):
        gap = distance(origin, target)
        return [i for i, c in enumerate(cells)
                if abs(c[0] - t0) <= size and abs(c[1] - t1) <= size and distance(origin, c) >= gap]
    return [i for i, c in enumerate(cells) if in_zone(shape, size, origin, target, c)]


@dataclass(frozen=True)
class _Option:
    spell: CombatSpell
    target: Cell
    hits: tuple[int, ...]  # indices des ennemis touchés
    target_enemy: int | None  # ennemi sur la case visée (lancers par cible)
    damage: float
    value: float
    dealt: tuple[tuple[int, float], ...]  # (ennemi, dégâts attendus), marge d'une carte à relief comprise
    positions: tuple[Cell, ...] | None  # cases des ennemis après le sort (None : personne n'a bougé)
    reason: str
    stretch: bool = False  # attaque possible seulement grâce à la portée en plus (bonus de portée de la suite)


@dataclass(frozen=True)
class _Finish:
    value: float
    cell: Cell | None = None  # case de repli (None : rester)
    pm: int = 0


@dataclass
class _Best:
    total: float
    damage: float
    casts: tuple[_Option, ...]
    finish: _Finish
    alive: tuple[Cell, ...]  # ennemis encore debout à la fin de la suite (cases après les reculs)


@dataclass(frozen=True)
class _Team:
    """Ce que ma case fait à l'équipe : alliés collés, ennemis vus seulement à travers un allié, lignes coupées."""

    adjacent: tuple[Cell, ...] = ()
    behind: tuple[tuple[Cell, Cell], ...] = ()  # (ennemi, allié devant moi)
    cuts: tuple[tuple[Cell, Cell], ...] = ()  # (allié, ennemi) dont je coupe la ligne de vue
    value: float = 0.0


class _Planner:
    def __init__(self, me: Cell, pm: int, enemies: list[Cell], allies: list[Cell], spells: list[CombatSpell],
                 use: SpellUse, board: Board, weights: Weights, avoid: set[Cell], missed: set[Cell],
                 can_move: bool, move_first: bool = True, life: dict[Cell, int] | None = None,
                 names: dict[Cell, str] | None = None) -> None:
        self.me, self.pm = me, max(pm, 0)
        self.enemies = tuple(enemies)
        self.allies = frozenset(allies)
        self.ally_cells = tuple(sorted(self.allies))
        self.spells = [s for s in spells if s.damage > 0 or (s.on_self and (s.range_bonus or s.damage_boost))]
        self.use, self.board, self.w = use, board, weights
        self.active = use.damage_bonus  # bonus de dégâts déjà en cours ce tour (lancés avant)
        # Une attaque est déjà partie ce tour : plus de sort de bonus (ils se lancent avant de taper).
        self.attacked = any(use.casts_this_turn[s.spell_id] for s in spells if s.damage > 0)
        self.avoid, self.missed, self.can_move, self.move_first = avoid, missed, can_move, move_first
        factor = 1.0 if board.reliable else UNRELIABLE_MOVE_FACTOR
        self.move_cost, self.spare_pm_cost = self.w.move * factor, self.w.spare_pm * factor
        reach = reach_range(spells, use) or max((spell_range(s, use) for s in spells if s.damage > 0),
                                                key=lambda r: r[1], default=None)
        self.reach = reach
        # Sorts qui visent une case (pas ceux lancés sur moi) : d'où un ennemi est touchable au tour suivant.
        self.attacks = [(*spell_range(s, use), s.in_line, s.needs_sight) for s in self.spells
                        if s.damage > 0 and not s.on_self]
        self.safe_cap = min(reach[1], SAFE_CAP) if reach else 1
        self.walls = (board.obstacles or frozenset()) | self.allies  # coupent la vue, connus même sans le terrain
        self.free = board.cells - board.unwalkable - board.unseen - self.allies  # où l'on peut se tenir (sans ennemis)
        life = life or {}
        self.hp = {i: life[e] for i, e in enumerate(self.enemies) if e in life}  # vie connue, par ennemi
        self.focus = min(self.hp, key=lambda i: (self.hp[i], i)) if self.hp else None  # la cible de l'équipe
        self.names = names or {}
        self.sequences = 0
        self.can_hit = False
        self._options_cache: dict = {}
        self._finish_cache: dict = {}
        self._state_cache: dict = {}
        self._steps_cache: dict = {}
        self._cuts_cache: dict = {}
        self._team_cache: dict = {}

    # --- Ligne de vue, contact ---

    def _sight(self, origin: Cell, target: Cell, positions: tuple[Cell, ...]) -> bool:
        """Ligne de vue d'un sort lancé ce tour (obstacles inconnus : le jeu tranchera)."""
        obstacles = self.board.obstacles
        if obstacles is None:
            return True
        line = crossed(origin, target)
        return line.isdisjoint(obstacles) and line.isdisjoint(self.allies) and \
            all(p == target or p not in line for p in positions)

    def _clear(self, origin: Cell, target: Cell, positions: tuple[Cell, ...]) -> bool:
        """Ligne de vue pour le placement : obstacles connus, alliés et autres ennemis."""
        line = crossed(origin, target)
        return line.isdisjoint(self.walls) and all(p == target or p not in line for p in positions)

    @staticmethod
    def _nearest(cell: Cell, positions: tuple[Cell, ...]) -> int:
        return min((distance(cell, p) for p in positions), default=99)

    def _name(self, ally: Cell) -> str:
        return self.names.get(ally) or f"l'allié en {ally}"

    # --- Sorts ---

    def _castable(self, spell: CombatSpell, pa: int, local: Counter) -> bool:
        use, key = self.use, spell.spell_id
        if key in use.unavailable or key in use.greyed or spell.pa > pa:
            return False
        if local[key] and spell.cooldown:
            return False
        # Sort sur soi : « n lancers par cible » = n lancers par tour, je suis la seule cible (11/10 : Amplification
        # prévue 4 fois, le 2e lancer refusé par le jeu).
        if spell.on_self and spell.per_target and use.casts_this_turn[key] + local[key] >= spell.per_target:
            return False
        last = use.last_cast_turn.get(key)
        if spell.cooldown and last is not None and use.turn - last < spell.cooldown:
            return False
        return not (spell.per_turn and use.casts_this_turn[key] + local[key] >= spell.per_turn)

    def _targets(self, spell: CombatSpell, origin: Cell, positions: tuple[Cell, ...]) -> list[Cell]:
        """Cases à viser : les ennemis, puis pour une zone les cases vides d'où elle prend un ennemi, les plus
        proches de lui d'abord (à effet égal, c'est celle-là qui est gardée : elle touche encore si la zone est
        un peu plus petite que prévu)."""
        if spell.on_self:
            return [origin]
        shape, size = spell.zone
        targets = list(dict.fromkeys(positions))
        if size <= 0 or shape == "point":
            return targets
        if shape == "ligne":  # cases alignées entre moi et un ennemi aligné, à `size` cases au plus devant lui
            for p in positions:
                if aligned(origin, p):
                    ui, uj = unit(origin, p)
                    far = distance(origin, p)
                    targets += [cell for k in range(far - 1, max(0, far - size - 1), -1)
                                if (cell := (origin[0] + k * ui, origin[1] + k * uj)) in self.board.cells]
            return list(dict.fromkeys(targets))
        if shape in ("cercle", "croix", "anneau", "damier"):  # zones symétriques : t prend p <=> p dans la zone de t
            radius = min(size, ZONE_SEARCH)
            around = sorted(((di, dj) for di in range(-radius, radius + 1) for dj in range(-radius, radius + 1)
                             if (di + dj) % 2 == 0), key=lambda d: max(abs(d[0]), abs(d[1])))
            for p in positions:
                targets += [cell for di, dj in around if (cell := (p[0] + di, p[1] + dj)) in self.board.cells
                            and in_zone(shape, size, origin, cell, p)]
            return list(dict.fromkeys(targets))
        return targets

    def _options(self, origin: Cell, positions: tuple[Cell, ...], extra: int,
                 boost: tuple[float, float] = (0.0, 0.0)) -> list[_Option]:
        """Sorts lançables depuis `origin` sur les ennemis en `positions` (sans compter PA ni lancers), dans un
        ordre fixe : il sert à ne pas essayer toutes les permutations de sorts qui ne déplacent personne. `extra` :
        portée en plus, `boost` : bonus de dégâts en plus (dommages, %), donnés par les sorts de bonus de la suite."""
        key = (origin, positions, extra, boost)
        if key in self._options_cache:
            return self._options_cache[key]
        flat, pct = self.active[0] + boost[0], self.active[1] + boost[1]
        options = []
        at_start = origin == self.me
        loose = at_start and not self.board.reliable
        margin = self.board.margin if loose else 0
        for spell in self.spells:
            low, high = spell_range(spell, self.use)
            if spell.range_modifiable:
                high += extra
            shape, size = spell.zone
            seen = set()
            for target in self._targets(spell, origin, positions):
                gap = distance(origin, target)
                # Marge du relief sur la portée maximale ; sur la minimale, une case au plus : 04/10, Flèche
                # d'Expiation (portée 8 à 12) tentée à 4 cases, refusée par le jeu.
                if not low - min(margin, LOW_MARGIN) <= gap <= high + margin:
                    continue
                if not spell.on_self:
                    if target in self.allies or target == origin:
                        continue
                    if spell.in_line and not aligned(origin, target):
                        continue
                    if spell.needs_sight and not loose and not self._sight(origin, target, positions):
                        continue
                if at_start and (spell.spell_id, target) in self.use.refused:
                    continue  # refusé d'ici (combat.py les oublie après un déplacement) : d'une autre case, on verra
                if at_start and gap >= self.use.beyond.get(spell.spell_id, gap + 1):
                    continue  # refusé d'ici à cette distance, dans la marge du relief : plus loin, pas mieux
                if zone_hits(shape, size, origin, target, self.ally_cells, safe=True):
                    continue  # jamais un allié dans la zone
                if not spell.on_self and zone_hits(shape, size, origin, target, (origin,), safe=True):
                    continue  # ni moi
                hits = tuple(zone_hits(shape, size, origin, target, positions))
                if spell.damage <= 0:
                    if spell.on_self and spell.range_bonus:
                        options.append(_Option(spell, target, (), None, 0.0, self.w.range_bonus, (), None,
                                               "bonus de portée en début de tour"))
                    elif spell.on_self and spell.damage_boost:
                        options.append(_Option(spell, target, (), None, 0.0, self._carry(spell), (), None,
                                               f"bonus de dégâts ({_boost_text(spell)}) avant de taper"))
                    break
                if not hits:
                    continue
                target_enemy = next((i for i, p in enumerate(positions) if p == target), None)
                moved, blocked = self._push(spell, origin, target, positions, hits)
                outcome = (hits, target_enemy, moved)
                if outcome in seen:
                    continue  # même effet qu'une case déjà retenue
                seen.add(outcome)
                # Zone dégressive : moins de dégâts à mesure qu'on s'éloigne de la case visée.
                strength = spell.boosted(flat, pct)
                per_enemy = tuple((i, strength * max(0.0, 1 - self.w.zone_falloff * distance(target, positions[i])))
                                  for i in hits)
                damage = sum(d for _, d in per_enemy)
                if not self.board.reliable:
                    blocked = 0  # obstacles mal connus : poussée contre un mur supposée, pas comptée
                value = damage + PUSH_PER_CELL * blocked  # danger écarté : compté en fin de suite (_relief)
                odds = 1.0 if low <= gap <= high else MARGIN_ODDS  # dans la marge d'une carte à relief : une chance
                value *= odds
                stretch = spell.range_modifiable and extra > 0 and gap > high - extra + margin
                close = [] if moved is None else [  # ennemis proches repoussés
                    d for i in hits
                    if (d := distance(origin, positions[i])) <= self.w.danger and distance(origin, moved[i]) > d]
                if close:
                    reason = f"danger : ennemi à {min(close)} case(s), je le repousse"
                elif len(hits) > 1:
                    reason = f"{len(hits)} ennemis dans la zone"
                elif blocked:
                    reason = "ennemi poussé contre un obstacle"
                else:
                    reason = "le plus de dégâts"
                dealt = tuple((i, d * odds) for i, d in per_enemy)
                options.append(_Option(spell, target, hits, target_enemy, damage, value, dealt, moved, reason,
                                       stretch))
        options = _undominated(options)
        self._options_cache[key] = options
        return options

    def _carry(self, boost: CombatSpell) -> float:
        """Ce que vaut un bonus de dégâts pour les tours suivants qu'il dure : BOOST_CARRY de ce qu'il ajoute à un
        lancer de ma meilleure attaque, par tour de plus (2 au plus). Le tour même, sa valeur vient des attaques qui
        le suivent dans la suite (_options avec `boost`)."""
        turns = min(max(boost.boost_turns, 1) - 1, 2)
        gain = max((s.boosted(boost.boost_flat, boost.boost_pct) - s.damage for s in self.spells
                    if s.damage > 0 and not s.on_self), default=0.0)
        return self.w.boost_carry * turns * gain

    def _push(self, spell: CombatSpell, origin: Cell, center: Cell, positions: tuple[Cell, ...],
              hits: tuple[int, ...]) -> tuple[tuple[Cell, ...] | None, int]:
        """Recul simulé : nouvelles cases des ennemis (None : personne n'a bougé), et ennemis arrêtés par un
        obstacle, un combattant ou le bord de la carte (dégâts de poussée).

        Direction : de moi vers la cible pour l'ennemi visé, du centre de la zone vers lui pour les autres ; le plus
        loin est poussé d'abord (il libère la place). Hors des axes de la grille, la poussée n'est pas simulée."""
        if not spell.push:
            return None, 0
        new = list(positions)
        blocked = 0
        for i in sorted(hits, key=lambda i: -distance(origin, positions[i])):
            cell = new[i]
            start = center if cell != center else origin
            if not aligned(start, cell):
                continue
            di, dj = unit(start, cell)
            occupied = (set(new) - {cell}) | self.allies | {origin}
            for step in range(spell.push):
                nxt = (cell[0] + di, cell[1] + dj)
                if nxt not in self.board.cells or nxt in self.board.unwalkable or nxt in occupied:
                    blocked += spell.push - step  # cases de recul empêchées
                    break
                cell = nxt
            new[i] = cell
        moved = tuple(new)
        return (None if moved == positions else moved), blocked

    def _relief(self, origin: Cell, positions: tuple[Cell, ...]) -> int:
        """Danger écarté par les reculs : pour chaque ennemi poussé, cases gagnées (jusqu'à `danger` + 1) entre sa
        distance à ma case de départ en début de tour et sa distance à ma case de lancer à la fin de la suite.
        Aller au contact d'un ennemi pour le repousser n'écarte donc aucun danger."""
        if not self.w.danger or positions is self.enemies:
            return 0
        cap = self.w.danger + 1
        return sum(max(0, min(distance(origin, now), cap) - min(distance(self.me, before), cap))
                   for before, now in zip(self.enemies, positions) if now != before)

    def _lives(self, dealt: list[float]) -> tuple[float, tuple[int, ...]]:
        """Vie connue des ennemis touchés : (correction de la valeur de la suite, ennemis achevés).

        Dégâts au-delà de sa vie : perdus ; ennemi achevé : KILL_BONUS ; cible de l'équipe : FOCUS_SHARE de ses dégâts
        en plus."""
        adjust, killed = 0.0, []
        for i, hp in self.hp.items():
            done = dealt[i]
            if done <= EPSILON:
                continue
            if done >= hp - EPSILON:
                adjust += self.w.kill - (done - hp)
                killed.append(i)
            if i == self.focus:
                adjust += self.w.focus * min(done, hp)
        return adjust, tuple(killed)

    # --- Fin de tour : la case où je reste jusqu'au tour suivant ---

    def _hit_steps(self, positions: tuple[Cell, ...]) -> dict[Cell, int]:
        """Pas à faire, depuis chaque case proche, pour atteindre une case d'où un de mes sorts touche un ennemi en
        `positions` (portée, lancer en ligne, ligne de vue : obstacles connus, alliés et autres ennemis compris).

        Calculé une fois par position des ennemis : cases de départ autour de chaque ennemi, puis parcours en largeur
        sur les cases libres, jusqu'à NEXT_TURN_CAP pas, près de moi seulement (là où ce tour peut finir)."""
        if positions in self._steps_cache:
            return self._steps_cache[positions]
        limit = self.pm + NEXT_TURN_CAP
        free = self.free - set(positions)
        top = max((high for _, high, _, _ in self.attacks), default=0)
        sources = set()
        for e in positions:
            for di in range(-top, top + 1):
                for dj in range(-top, top + 1):
                    cell = (e[0] + di, e[1] + dj)
                    if (di + dj) % 2 or cell in sources or cell not in free or distance(self.me, cell) > limit:
                        continue
                    gap = max(abs(di), abs(dj))
                    clear = None  # ligne de vue de cette case sur l'ennemi : calculée au plus une fois, si un sort en a besoin
                    for low, high, line, sight in self.attacks:
                        if not low <= gap <= high or line and not aligned(cell, e):
                            continue
                        if sight and clear is None:
                            clear = self._clear(cell, e, positions)
                        if not sight or clear:
                            sources.add(cell)
                            break
        steps = dict.fromkeys(sources, 0)
        frontier = list(sources)
        for n in range(1, NEXT_TURN_CAP + 1):
            reached = []
            for cell in frontier:
                for di, dj in NEIGHBOURS:
                    nxt = (cell[0] + di, cell[1] + dj)
                    if nxt in free and nxt not in steps and distance(self.me, nxt) <= limit:
                        steps[nxt] = n
                        reached.append(nxt)
            frontier = reached
        self._steps_cache[positions] = steps
        return steps

    def _steps_to_hit(self, cell: Cell, positions: tuple[Cell, ...]) -> int:
        if not positions or not self.attacks:
            return 0
        steps = self._hit_steps(positions).get(cell)
        if steps is not None:
            return steps
        top = max(high for _, high, _, _ in self.attacks)
        return NEXT_TURN_CAP + max(0, self._nearest(cell, positions) - top)  # rien en vue près d'ici : se rapprocher

    def _cuts(self, positions: tuple[Cell, ...]) -> dict[Cell, list[tuple[Cell, Cell]]]:
        """Lignes de vue dégagées des alliés vers les ennemis (à ALLY_REACH cases au plus), par case qu'elles
        traversent : s'y tenir les couperait."""
        if positions in self._cuts_cache:
            return self._cuts_cache[positions]
        obstacles = self.board.obstacles or frozenset()
        cuts: dict[Cell, list[tuple[Cell, Cell]]] = {}
        for ally in self.allies:
            others = self.allies - {ally}
            for enemy in positions:
                if distance(ally, enemy) > ALLY_REACH:
                    continue
                line = crossed(ally, enemy)
                if line.isdisjoint(obstacles) and line.isdisjoint(others) and \
                        all(p == enemy or p not in line for p in positions):
                    for cell in line:
                        cuts.setdefault(cell, []).append((ally, enemy))
        self._cuts_cache[positions] = cuts
        return cuts

    def _team(self, cell: Cell, positions: tuple[Cell, ...]) -> _Team:
        key = (cell, positions)
        if key in self._team_cache:
            return self._team_cache[key]
        adjacent = tuple(sorted(a for a in self.allies if distance(a, cell) == 1))
        behind = []
        if self.reach and self.allies:
            low, high = self.reach
            obstacles = self.board.obstacles or frozenset()
            for enemy in positions:
                if not low <= distance(cell, enemy) <= high:
                    continue
                line = crossed(cell, enemy)
                front = line & self.allies
                if front and line.isdisjoint(obstacles) and all(p == enemy or p not in line for p in positions):
                    behind.append((enemy, min(front, key=lambda a: distance(a, cell))))
        cuts = tuple(self._cuts(positions).get(cell, ()))
        value = -(self.w.ally_adjacent * len(adjacent) + self.w.behind_ally * min(len(behind), BEHIND_CAP)
                  + self.w.cut_ally * len(cuts))
        team = self._team_cache[key] = _Team(adjacent, tuple(behind), cuts, value)
        return team

    def _state(self, cell: Cell, positions: tuple[Cell, ...]) -> float:
        """Valeur de la case où je finis mon tour, `positions` : ennemis encore debout (cases après les reculs).

        Au contact d'un ennemi : -contact ; par pas à faire au tour suivant pour toucher : -next_turn ; jeu d'équipe
        (_team) ; à l'abri : un peu par case d'écart avec l'ennemi le plus proche, d'après leurs cases du début du
        tour (un ennemi repoussé compte déjà dans le danger écarté, _relief)."""
        key = (cell, positions)
        if key not in self._state_cache:
            value = -self.w.contact if self._nearest(cell, positions) <= 1 else 0.0
            value -= self.w.next_turn * self._steps_to_hit(cell, positions)
            value += self.w.safe * min(self._nearest(cell, self.enemies), self.safe_cap)
            self._state_cache[key] = value + self._team(cell, positions).value
        return self._state_cache[key]

    def _finish(self, origin: Cell, positions: tuple[Cell, ...], pm_left: int) -> _Finish:
        """Rester, ou se replier avec les PM restants : hors du contact, hors des lignes de vue des alliés, à portée
        pour le tour suivant ; seulement si ça rapporte plus que le déplacement (PM restants : SPARE_PM_COST)."""
        key = (origin, positions, pm_left)
        if key in self._finish_cache:
            return self._finish_cache[key]
        best = _Finish(self._state(origin, positions))
        if self.can_move and pm_left > 0:
            in_contact = self._nearest(origin, positions) <= 1
            blocked = self.board.unwalkable | self.board.unseen | self.allies | set(positions)
            walk = walk_steps(origin, pm_left, self.board.cells, blocked, contact_cells(positions))
            for cell, n in sorted(walk.items(), key=lambda kv: (kv[1], kv[0])):
                if cell == origin or cell in self.missed:
                    continue
                value = self._state(cell, positions) - self.spare_pm_cost * n - self.w.step - \
                    (self.w.tackle if in_contact else 0.0)
                if value > best.value + EPSILON:
                    best = _Finish(value, cell, n)
        self._finish_cache[key] = best
        return best

    # --- Recherche ---

    def _casts_from(self, origin: Cell, pm_left: int) -> _Best:
        """Meilleure suite de sorts lancés depuis `origin`, puis la fin de tour (repli éventuel)."""
        best = _Best(float("-inf"), 0.0, (), _Finish(0.0), self.enemies)
        local: Counter = Counter()  # lancers de ce plan, par sort
        per_target: Counter = Counter()  # (sort, ennemi)
        dealt = [0.0] * len(self.enemies)  # dégâts attendus par ennemi
        sequence: list[_Option] = []

        def leaf(positions, value, damage, bonus):
            nonlocal best
            self.sequences += 1
            if damage > 0:
                self.can_hit = True
            elif bonus:
                return  # bonus sans attaque derrière : gardé pour quand il servira
            if any(o.spell.range_bonus and o.damage <= 0 for o in sequence) and not any(o.stretch for o in sequence):
                return  # 11/10 : bonus de portée alors que tout est déjà à portée : des PA pour rien
            adjust, killed = self._lives(dealt)
            alive = tuple(p for i, p in enumerate(positions) if i not in killed) if killed else positions
            finish = self._finish(origin, alive, pm_left)
            total = value + adjust + self.w.push_away * self._relief(origin, positions) + finish.value + \
                (self.w.hit if damage > 0 else 0.0)
            if _better((total, damage, -len(sequence)), (best.total, best.damage, -len(best.casts))):
                best = _Best(total, damage, tuple(sequence), finish, alive)

        def search(pa, positions, extra, boost, min_index, value, damage, bonus):
            leaf(positions, value, damage, bonus)
            if len(sequence) >= MAX_CASTS:
                return
            for index, option in enumerate(self._options(origin, positions, extra, boost)):
                spell = option.spell
                is_bonus = option.damage <= 0
                changes = is_bonus or option.positions is not None  # change la suite : l'ordre compte
                if index < min_index and not changes:
                    continue
                if not self._castable(spell, pa, local):
                    continue
                if is_bonus and (damage > 0 or self.attacked):
                    continue  # sorts de bonus : avant toute attaque du tour
                enemy = option.target_enemy
                if enemy is not None and spell.per_target and per_target[(spell.spell_id, enemy)] + \
                        self.use.casts_on_target[(spell.spell_id, self.enemies[enemy])] >= spell.per_target:
                    continue
                local[spell.spell_id] += 1
                per_target[(spell.spell_id, enemy)] += 1
                for i, d in option.dealt:
                    dealt[i] += d
                sequence.append(option)
                more = (boost[0] + spell.boost_flat, boost[1] + spell.boost_pct) if spell.damage_boost else boost
                search(pa - spell.pa, option.positions or positions, extra + (spell.range_bonus if is_bonus else 0),
                       more, 0 if changes else index, value + option.value, damage + option.damage, bonus or is_bonus)
                sequence.pop()
                for i, d in option.dealt:
                    dealt[i] -= d
                per_target[(spell.spell_id, enemy)] -= 1
                local[spell.spell_id] -= 1

        search(self.use.pa, self.enemies, 0, (0.0, 0.0), 0, 0.0, 0.0, False)
        return best

    def plan(self) -> TurnPlan:
        start_contact = self._nearest(self.me, self.enemies) <= 1
        if self.can_move and self.move_first and self.pm > 0:
            blocked = self.board.unwalkable | self.board.unseen | self.allies | set(self.enemies)
            moves = walk_steps(self.me, self.pm, self.board.cells, blocked, contact_cells(self.enemies))
        else:
            moves = {self.me: 0}
        chosen, chosen_cell, chosen_pm, chosen_key = None, self.me, 0, (float("-inf"), 0.0)
        for cell, n in sorted(moves.items(), key=lambda kv: (kv[1], kv[0])):  # à score égal : le moins de PM
            if cell != self.me and (cell in self.avoid or cell in self.missed):
                continue
            result = self._casts_from(cell, self.pm - n)
            total = result.total - (self.move_cost * n + self.w.step if cell != self.me else 0.0) - \
                (self.w.tackle if start_contact and cell != self.me else 0.0)
            if _better((total, result.damage), chosen_key):
                chosen, chosen_cell, chosen_pm, chosen_key = result, cell, n, (total, result.damage)
        if not chosen.casts:
            return self._position()  # rien ne touche ce tour, ou pas à ce prix : se placer pour le suivant
        steps = []
        if chosen_cell != self.me:
            steps.append(Step(MOVE, chosen_cell, pm=chosen_pm, reason=self._why_move(chosen_cell, chosen.casts[0])))
        positions = self.enemies
        for option in chosen.casts:
            steps.append(Step(CAST, option.target, option.spell, hits=tuple(positions[i] for i in option.hits),
                              damage=option.damage, value=option.value, reason=option.reason))
            positions = option.positions or positions
        if chosen.finish.cell is not None:
            reason = self._why_end(chosen_cell, chosen.finish.cell, chosen.alive)
            steps.append(Step(RETREAT, chosen.finish.cell, pm=chosen.finish.pm, reason=reason))
        return TurnPlan(tuple(steps), chosen_key[0], self.can_hit, self.sequences)

    def _position(self) -> TurnPlan:
        """Placement pour le tour suivant, quand aucun sort ne touche ce tour (ou pas à ce prix) : la case de meilleur
        état (_state : prochaine case d'où taper, alliés, contact, distance), avec tous les PM utiles d'un coup ; les
        PM ne coûtent presque rien (SPARE_PM_COST), un déplacement un peu (STEP_COST)."""
        stay = self._state(self.me, self.enemies)
        best_cell, best_pm, best_value = self.me, 0, stay
        walk = {}
        if self.can_move and self.pm > 0:
            in_contact = self._nearest(self.me, self.enemies) <= 1
            blocked = self.board.unwalkable | self.board.unseen | self.allies | set(self.enemies)
            walk = walk_steps(self.me, self.pm, self.board.cells, blocked, contact_cells(self.enemies))
            for cell, n in sorted(walk.items(), key=lambda kv: (kv[1], kv[0])):
                if cell == self.me or cell in self.avoid or cell in self.missed:
                    continue
                value = self._state(cell, self.enemies) - self.spare_pm_cost * n - self.w.step - \
                    (self.w.tackle if in_contact else 0.0)
                if value > best_value + EPSILON:
                    best_cell, best_pm, best_value = cell, n, value
        if best_cell == self.me:
            return TurnPlan((), stay, self.can_hit, self.sequences)
        reason = self._why_position(best_cell, walk)
        return TurnPlan((Step(POSITION, best_cell, pm=best_pm, reason=reason),), best_value, self.can_hit,
                        self.sequences)

    # --- Raisons des déplacements (journal) ---

    def _team_changes(self, start: Cell, end: Cell, positions: tuple[Cell, ...]) -> list[str]:
        """Ce que le déplacement de `start` à `end` change pour l'équipe, en clair."""
        before, after = self._team(start, positions), self._team(end, positions)
        notes = []
        cleared = {e for e, _ in before.behind} - {e for e, _ in after.behind}  # plus aucun allié devant moi
        if cleared:
            names = ", ".join(self._name(a) for a in sorted({a for e, a in before.behind if e in cleared}))
            notes.append(f"je me décale pour dégager ma ligne de vue ({names} devant moi)")
        cut = {a for a, _ in before.cuts} - {a for a, _ in after.cuts}
        if cut:
            notes.append("je ne coupe plus la ligne de vue de " + ", ".join(self._name(a) for a in sorted(cut)))
        unstuck = set(before.adjacent) - set(after.adjacent)
        if unstuck and not after.adjacent:
            notes.append("je ne reste pas collé à " + ", ".join(self._name(a) for a in sorted(unstuck)))
        return notes

    def _why_move(self, cell: Cell, first: _Option) -> str:
        """Déplacement avant de lancer : pour lancer d'ici, et pourquoi pas de ma case."""
        notes = ["pour lancer d'ici"]
        low, high = spell_range(first.spell, self.use)
        ahead = sorted(crossed(self.me, first.target) & self.allies)
        cleared = not first.spell.on_self and low <= distance(self.me, first.target) <= high and bool(ahead)
        if cleared:
            notes.append(f"je me décale pour dégager ma ligne de vue ({', '.join(self._name(a) for a in ahead)} "
                         "la coupait)")
        notes += [n for n in self._team_changes(self.me, cell, self.enemies)
                  if not (cleared and n.startswith("je me décale"))]
        return ", ".join(notes)

    def _why_end(self, start: Cell, end: Cell, positions: tuple[Cell, ...]) -> str:
        """Repli après les sorts."""
        notes = []
        if self._nearest(start, positions) <= 1:
            notes.append("hors du contact")
        notes += self._team_changes(start, end, positions)
        if not notes:
            notes.append("pour taper au prochain tour" if self._steps_to_hit(end, positions) <
                         self._steps_to_hit(start, positions) else "plus à l'abri")
        return ", ".join(notes)

    def _why_position(self, cell: Cell, walk: dict[Cell, int]) -> str:
        """Placement pour le tour suivant : où en sera la prochaine case d'où taper, ce qui change pour l'équipe, et
        la case « tout droit vers l'ennemi » écartée si elle gênait un allié."""
        positions = self.enemies
        notes = ["aucun sort ne touche ce tour"]
        if self._nearest(self.me, positions) <= 1 and self._nearest(cell, positions) > 1:
            notes.append("hors du contact")
        notes += self._team_changes(self.me, cell, positions)
        steps = self._steps_to_hit(cell, positions)
        notes.append("un ennemi à portée et en vue d'ici au prochain tour" if steps == 0 else
                     f"une case d'où taper à {steps} pas d'ici" if steps < NEXT_TURN_CAP else "je me rapproche")
        straight = min((c for c in walk if c != self.me and c not in self.missed and c not in self.avoid),
                       key=lambda c: (self._nearest(c, positions), walk[c], c), default=None)
        if straight is not None and straight != cell:
            # La case qui rapproche le plus de l'ennemi (l'ancien choix) : pourquoi pas elle.
            there, here = self._team(straight, positions), self._team(cell, positions)
            why = []
            for word, avoided, chosen in (("devant", {a for a, _ in there.cuts}, {a for a, _ in here.cuts}),
                                          ("derrière", {a for _, a in there.behind}, {a for _, a in here.behind}),
                                          ("collé à", set(there.adjacent), set(here.adjacent))):
                if len(avoided) > len(chosen):
                    why.append(f"{word} {', '.join(self._name(a) for a in sorted(avoided - chosen))}")
            far = self._steps_to_hit(straight, positions)
            if far > steps:
                why.append(f"à {far} pas d'une case d'où taper" if far < NEXT_TURN_CAP else "sans ennemi en vue")
            if why:
                notes.append(f"je ne me mets pas en {straight}, tout droit : {', '.join(why)}")
        return ", ".join(notes)


def _boost_text(spell: CombatSpell) -> str:
    """« +40 par coup », « +140 % », « +7 par coup et +20 % »."""
    parts = ([f"+{spell.boost_flat:g} par coup"] if spell.boost_flat else []) + \
        ([f"+{spell.boost_pct:g} %"] if spell.boost_pct else [])
    return " et ".join(parts)


def _dominates(a: _Option, b: _Option) -> bool:
    """`a` fait au moins autant que `b` à chaque ennemi, vaut au moins autant, et n'est pas plus limitée par les
    lancers par cible (case vide visée, ou le même ennemi que `b`). Même sort, aucun ennemi déplacé."""
    if a.target_enemy is not None and a.target_enemy != b.target_enemy or a.value < b.value - EPSILON:
        return False
    done = dict(a.dealt)
    return all(done.get(i, 0.0) >= d - EPSILON for i, d in b.dealt)


def _undominated(options: list[_Option]) -> list[_Option]:
    """Options d'un même sort qui ne font rien de mieux qu'une autre : écartées, toute suite qui s'en sert est au
    moins aussi bonne avec l'autre à sa place (plus de dégâts n'est jamais pire : au-delà de la vie d'un ennemi,
    ils ne comptent plus, _lives). À égalité, la première gardée : l'ennemi visé avant une case vide.

    04/10 : une zone vise aussi les cases vides autour des ennemis (cercle de rayon 3 : jusqu'à 25 cases par ennemi),
    presque toutes moins bonnes que viser l'ennemi lui-même ; avec 10 PA, 5 PM et 7 ennemis, un plan prenait 0,3 à
    0,9 s, refait après chaque action. Les sorts qui déplacent un ennemi sont tous gardés (l'ordre des sorts compte)."""
    kept: dict[str, list[_Option]] = {}
    order = {id(option): n for n, option in enumerate(options)}
    survivors = [o for o in options if o.positions is not None or o.damage <= 0]
    for option in sorted((o for o in options if o.positions is None and o.damage > 0), key=lambda o: -o.value):
        group = kept.setdefault(option.spell.spell_id, [])
        if not any(_dominates(other, option) for other in group):
            group.append(option)
    survivors += [o for group in kept.values() for o in group]
    return sorted(survivors, key=lambda o: order[id(o)])


def _better(a: tuple, b: tuple) -> bool:
    """Clés (score, dégâts, ...) : `a` meilleure que `b`, aux erreurs d'arrondi près ; à égalité, la première gagne."""
    for x, y in zip(a, b):
        if x > y + EPSILON:
            return True
        if x < y - EPSILON:
            return False
    return False


def plan_turn(me: Cell, pm: int, enemies: list[Cell], allies: list[Cell], spells: list[CombatSpell], use: SpellUse,
              board: Board, weights: Weights = Weights(), avoid: set[Cell] = frozenset(),
              missed: set[Cell] = frozenset(), can_move: bool = True, move_first: bool = True,
              life: dict[Cell, int] | None = None, names: dict[Cell, str] | None = None) -> TurnPlan:
    """Meilleur plan du tour depuis l'état actuel (voir le début du module).

    `pm` : PM restants ; `use` : PA restants et compteurs du tour (lancers, cibles refusées, portées mesurées,
    bonus) ; `allies` : combattants de mon équipe autres que moi ; `avoid` : cases où ne pas aller avant de lancer
    (déjà occupées ce tour ; le repli peut y revenir) ; `missed` : destinations pas atteintes ce tour, plus
    essayées ; `can_move` : faux quand le tour a déjà compté assez de déplacements (ni approche, ni repli) ;
    `move_first` : faux pour lancer d'ici sans aller ailleurs d'abord (le repli reste possible) ; `life` : vie connue
    des ennemis, par case (lue au survol) ; `names` : noms des alliés, par case (journal)."""
    return _Planner(me, pm, enemies, allies, spells, use, board, weights, set(avoid), set(missed), can_move,
                    move_first, life, names).plan()


def cast_blocker(spell: CombatSpell, me: Cell, target: Cell, enemies: list[Cell], allies: list[Cell], use: SpellUse,
                 board: Board) -> str:
    """Pourquoi `spell` ne se lancerait pas de `me` sur `target` d'après ce qui est connu ("" : rien ne l'empêche). Pour
    le journal, quand un sort prévu disparaît du plan suivant (11/10 : Tempête de Puissance abandonnée juste après
    Amplification, alors que le moteur, rejoué à part, la garde)."""
    planner = _Planner(me, 0, enemies, allies, [spell], use, board, Weights(), set(), set(), False, False, None, None)
    if spell.spell_id in use.unavailable:
        return "non sélectionnable ce tour"
    if spell.spell_id in use.greyed:
        return "grisé dans la barre"
    if spell.pa > use.pa:
        return f"{spell.pa} PA, il en reste {use.pa}"
    if not planner._castable(spell, use.pa, Counter()):
        return "relance ou lancers du tour épuisés"
    if (spell.spell_id, target) in use.refused:
        return "refusé d'ici par le jeu"
    low, high = spell_range(spell, use)
    gap = distance(me, target)
    if gap >= use.beyond.get(spell.spell_id, gap + 1):
        return f"refusé d'ici dès {use.beyond[spell.spell_id]} cases par le jeu"
    if not low <= gap <= high:
        return f"hors de portée ({gap} cases, portée {low} à {high})"
    if spell.in_line and not aligned(me, target):
        return "pas en ligne"
    if spell.needs_sight and not planner._sight(me, target, tuple(enemies)):
        blockers = crossed(me, target) & ((board.obstacles or frozenset()) | frozenset(allies) | frozenset(enemies))
        return f"pas de ligne de vue (cases en travers : {', '.join(map(str, sorted(blockers - {target})))})"
    if zone_hits(*spell.zone, me, target, tuple(sorted(allies)), safe=True):
        return "un allié dans la zone"
    return ""
