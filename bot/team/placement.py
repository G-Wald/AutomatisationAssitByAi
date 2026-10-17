"""Placement de toute l'équipe en début de combat (team.place : true) : qui va sur quelle case (logique pure, testable).

Demande de l'utilisateur (17/10) : ceux qui jouent en premier près des ennemis, à portée de leur sort dès leur premier
tour ; ceux qui jouent en dernier plus loin (les ennemis viendront à eux). Le 16/10 (team.place : false, 16 combats
à 8 sans anomalie), 68 tours sur 162 n'ont servi qu'à marcher ou à rien (« aucun sort ne touche ce tour »,
« rien d'utile à faire d'ici »), 65 d'entre eux avant le premier sort du personnage dans le combat.

Ordre de jeu : l'initiative. Ces personnages bas niveau, sans équipement ni caractéristiques, l'ont sans doute égale
et la règle d'égalité du jeu est inconnue : elle n'est pas devinée. L'ordre est celui vu dans la timeline au combat
précédent (TurnRouter.order, fusionné d'un combat à l'autre : merge_order). Le 16/10, il a été le même aux 18 combats
(Vertdepomme, Dinarpepe, Leproduitlatte, Pesopepe, Pombro, Vieenjaune, Lestunes, Koalaloupour, les monstres
intercalés). Au premier combat de la session, rien n'a encore été vu : ordre par défaut, celui de l'équipe du bot
(le principal d'abord, puis les suiveurs comme dans team.members) ; arbitraire, il est remplacé dès le combat suivant.

Choix des cases (plan_seats) : dans l'ordre de jeu, chacun prend la meilleure case qui reste parmi celles de mon
équipe (libres ou occupées par l'un des miens) :
1. pas au contact d'un ennemi ; pas sur la ligne de vue d'un allié qui joue avant lui (vers l'ennemi qu'il visera) ;
2. d'abord une case d'où un ennemi est à portée de son sort qui porte le plus loin, en ligne de vue (blocs connus,
   combattants), la plus loin des ennemis (comme en solo : brain/combat.choose_placement) ; sinon une case d'où il
   l'aura en marchant (portée + PM), puis les autres, chaque fois la plus proche des ennemis.
Ceux qui jouent tôt prennent donc les cases à portée, ou les plus proches ; les derniers, ce qui reste, plus loin.

Déplacements (next_moves) : on ne sait pas qui est sur quelle case (les suiveurs rejoignent tous ensemble, sans
attendre d'être reconnus) ; le jeu ne déplace un personnage que sur une case LIBRE de son équipe. À chaque passe,
tous ceux dont la case prévue est libre y vont en même temps (un clic dans chaque fenêtre, sans attendre) ; une seule
lecture des cases, dans une fenêtre, vérifie ensuite toute la passe : celui dont la case prévue est maintenant
occupée y est (personne d'autre n'y allait). Les cases qu'ils ont quittées se libèrent pour la passe suivante.
Quand plus personne ne peut avancer alors que certains ne sont pas reconnus (ils sont peut-être déjà sur leur case,
ou chacun sur celle d'un autre), un détour : ils vont sur une case libre (reconnus), puis sur la leur à la passe
suivante si elle s'est libérée. Le dernier non reconnu l'est par élimination (la seule case des miens qui reste).
"""

from dataclasses import dataclass

from bot.perception.grid import Cell, distance
from bot.perception.sight import line_of_sight


@dataclass(frozen=True)
class Seat:
    """Un personnage à placer : son nom, la portée de son sort qui porte le plus loin, ses PM."""

    name: str
    spell_range: tuple[int, int]
    pm: int = 3


def merge_order(seen: list[str], before: list[str]) -> list[str]:
    """Ordre de jeu appris : celui vu dans ce combat (`seen`, peut-être partiel : combat court, personnage mort avant
    de jouer), complété par l'ordre connu avant (`before`), chacun des absents gardé après tous ceux qui le
    précédaient."""
    order = list(seen)
    for i, name in enumerate(before):
        if name in order:
            continue
        after = [order.index(n) for n in before[:i] if n in order]
        order.insert(max(after) + 1 if after else 0, name)
    return order


def _nearest(cell: Cell, enemies: set[Cell]) -> int:
    return min(distance(cell, e) for e in enemies)


def _targets(seat: Seat, cell: Cell, enemies: set[Cell], obstacles: set[Cell]) -> list[Cell]:
    """Ennemis à portée du sort et en ligne de vue depuis `cell`, du plus proche au plus loin."""
    low, high = seat.spell_range
    return sorted((e for e in enemies if low <= distance(cell, e) <= high
                   and line_of_sight(cell, e, obstacles - {e, cell})), key=lambda e: (distance(cell, e), e))


def plan_seats(seats: list[Seat], cells: set[Cell], enemies: set[Cell], blockers: set[Cell] | None = None,
               fixed: dict[str, Cell] | None = None, free: set[Cell] | None = None,
               at: dict[str, Cell] | None = None) -> dict[str, Cell]:
    """Case de chacun (`seats` dans l'ordre de jeu), parmi `cells` (cases de mon équipe), deux personnages jamais sur
    la même. `blockers` : cases qui coupent la ligne de vue (blocs lus en mode tactique), None si inconnues.

    Refait à chaque passe : `fixed`, ceux qui sont déjà à leur place (leur case ne change plus) ; `free`, les cases
    libres maintenant ; `at`, la case de ceux qui sont reconnus. À rang égal (contact, ligne de vue, portée), une case
    où l'on peut aller tout de suite (libre, ou la sienne) passe avant une case occupée par un inconnu, qui ne se
    libérera peut-être qu'à la passe suivante, ou jamais (personnage déjà sur sa case)."""
    fixed, at = fixed or {}, at or {}
    plan: dict[str, Cell] = {}
    if not enemies:
        return plan
    lines: list[tuple[Cell, Cell]] = []  # (case, ennemi visé) des alliés qui jouent avant
    reserved = set(fixed.values())

    for seat in seats:
        left = cells - set(plan.values()) - (reserved - {fixed.get(seat.name)})
        if not left:
            break
        obstacles = (blockers or set()) | set(plan.values()) | reserved | enemies

        def cost(cell: Cell, seat=seat, obstacles=obstacles) -> tuple:
            near = _nearest(cell, enemies)
            cuts = sum(1 for start, aim in lines if not line_of_sight(start, aim, {cell}))
            if _targets(seat, cell, enemies, obstacles):
                tier, key = 0, -near
            elif near - seat.spell_range[1] <= seat.pm:
                tier, key = 1, near
            else:
                tier, key = 2, near
            later = free is not None and cell not in free and cell != at.get(seat.name)
            return near <= 1, cuts, tier, later, key, cell

        best = fixed[seat.name] if seat.name in fixed else min(left, key=cost)
        plan[seat.name] = best
        aims = _targets(seat, best, enemies, obstacles)
        if aims:
            lines.append((best, aims[0]))
    return plan


def next_moves(plan: dict[str, Cell], known: dict[str, Cell], free: set[Cell], order: list[str],
               detour: bool = True) -> list[tuple[str, Cell]]:
    """Passe suivante : (personnage, case où cliquer), dans l'ordre de jeu. `known` : case de ceux qui sont reconnus ;
    `free` : cases libres de mon équipe. Chacun dont la case prévue est libre y va ; si personne ne peut, détour
    (`detour`) des premiers non reconnus vers les cases libres les plus proches de leur case prévue."""
    moves = [(name, plan[name]) for name in order
             if name in plan and known.get(name) != plan[name] and plan[name] in free]
    if moves or not detour:
        return moves
    room = set(free)
    for name in order:
        if name not in plan or name in known or not room:
            continue
        cell = min(room, key=lambda c: (distance(c, plan[name]), c))
        room.discard(cell)
        moves.append((name, cell))
    return moves


def by_elimination(known: dict[str, Cell], names: list[str], cells: set[Cell]) -> tuple[str, Cell] | None:
    """Le seul personnage pas reconnu, s'il ne reste qu'une case des miens qui n'est à personne de connu."""
    unknown = [n for n in names if n not in known]
    rest = cells - set(known.values())
    if len(unknown) == 1 and len(rest) == 1:
        return unknown[0], next(iter(rest))
    return None
