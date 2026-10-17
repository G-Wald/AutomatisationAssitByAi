"""Placement de toute l'équipe (team/placement.py) : ordre de jeu, choix des cases, passes de déplacements ; sur des
placements simulés et sur les captures de placement de tests/fixtures."""

import random

import pytest

from bot.perception.fighters import Fighter
from bot.perception.grid import distance
from bot.perception.placement import read_placement, split_cells, standing_cells
from bot.perception.sight import line_of_sight, read_blocks
from bot.perception.ui import tactical_mode
from bot.team.placement import Seat, _targets, by_elimination, merge_order, next_moves, plan_seats
from bot.team.turns import TurnRouter
from conftest import load_frame

# L'équipe du 16/10, dans l'ordre de jeu vu aux 18 combats : Crâs (Flèche Magique 1 à 7), Enutrofs (Lancer de Pièces
# 0 à 8), le Sadida en dernier (Ronce 1 à 6).
TEAM = [Seat("Vertdepomme", (1, 7)), Seat("Dinarpepe", (0, 8)), Seat("Leproduitlatte", (1, 7)),
        Seat("Pesopepe", (0, 8)), Seat("Pombro", (1, 7)), Seat("Vieenjaune", (1, 7)), Seat("Lestunes", (0, 8)),
        Seat("Koalaloupour", (1, 6))]
ORDER = [s.name for s in TEAM]
FIXTURES = ["placement_foret", "preparation", "placement_moi_et_monstre", "placement_grand_personnage"]


def nearest(cell, enemies):
    return min(distance(cell, e) for e in enemies)


def fires(seat, cell, cells, enemies, blockers=None):
    """`seat` en `cell` a un ennemi à portée et en vue, les autres personnages en `cells`."""
    return bool(_targets(seat, cell, enemies, (blockers or set()) | (set(cells) - {cell}) | enemies))


def fixture_maps():
    """(capture, cases de mon équipe, ennemis, blocs) : chaque couleur d'une capture tour à tour pour mon équipe, l'un
    des combattants de l'autre couleur pour ennemi."""
    for name in FIXTURES:
        frame = load_frame(name)
        placement = read_placement(frame)
        standing = [Fighter(t, *frame.grid.center(c), c) for t, cells in placement.occupied.items() for c in cells]
        blockers = read_blocks(frame, standing) if tactical_mode(frame) else None
        for mine, other in (("red", "blue"), ("blue", "red")):
            cells = placement.free[mine] | standing_cells(placement.occupied[mine])
            enemies = standing_cells(placement.occupied[other])
            if len(cells) >= len(TEAM) and enemies:
                yield f"{name}:{mine}", cells, enemies, blockers


# Ordre de jeu


def test_order_seen_in_a_short_fight_keeps_the_others_where_they_were():
    before = ["V", "D", "L", "P", "K"]
    assert merge_order(["V", "D"], before) == before  # combat fini après 2 tours : rien n'est perdu
    assert merge_order(["D", "V", "L"], before) == ["D", "V", "L", "P", "K"]  # vu autrement : ce qui a été vu gagne
    assert merge_order(["L", "K"], before) == ["V", "D", "L", "P", "K"]  # V, D avant L, P juste après L
    assert merge_order(["A"], []) == ["A"]


def test_play_order_learned_from_the_previous_fights_or_the_team_order_at_first():
    router = TurnRouter(["K", "V", "D"])
    assert router.play_order(["K", "V", "D"]) == (["K", "V", "D"], False)  # premier combat : ordre de l'équipe
    router.learn("V", None)
    router.learn("D", None)
    router.learn("K", None)
    router.new_fight()
    assert router.play_order(["K", "V", "D"]) == (["V", "D", "K"], True)
    router.learn("D", None)  # combat suivant, court : seul D a joué ; rien ne dit que V ne joue plus avant lui
    router.new_fight()
    assert router.play_order(["K", "V", "D"]) == (["V", "D", "K"], True)
    router.learn("D", None)  # vu jouer avant V
    router.learn("V", None)
    router.new_fight()
    assert router.play_order(["K", "V", "D"]) == (["D", "V", "K"], True)
    assert router.play_order(["K", "D"]) == (["D", "K"], True)  # V n'est pas de ce combat


# Choix des cases (simulé)


def test_first_players_in_range_last_ones_further_away():
    enemies = {(16, 0)}
    near = {(10, 4), (10, -4), (9, -7)}  # à 6, 6 et 7 cases, aucune sur la ligne de vue d'une autre
    far = {(0, 0), (2, 0), (4, 0), (0, 4), (2, 4)}  # à 12 cases et plus
    plan = plan_seats([Seat(f"p{k}", (1, 7)) for k in range(8)], near | far, enemies)
    assert len(set(plan.values())) == 8  # jamais deux sur la même case
    assert {plan[f"p{k}"] for k in range(3)} == near  # les 3 premiers à jouer : à portée dès leur premier tour
    distances = [nearest(plan[f"p{k}"], enemies) for k in range(3, 8)]
    assert distances == sorted(distances)  # les suivants de plus en plus loin


def test_no_cell_on_the_line_of_an_earlier_player_while_another_one_is_left():
    enemies = {(16, 0)}
    plan = plan_seats([Seat("a", (1, 6)), Seat("b", (1, 7))], {(10, 0), (14, 0), (4, 4)}, enemies)
    assert plan == {"a": (10, 0), "b": (4, 4)}  # (14, 0) couperait la ligne de a : b va plus loin


def test_no_cell_in_contact_with_an_enemy_if_another_one_is_left():
    plan = plan_seats([Seat("a", (1, 1))], {(1, 1), (6, 6)}, {(2, 2)})
    assert plan == {"a": (6, 6)}  # seul le contact donnerait la portée : pas au contact


def test_a_later_player_never_stands_in_the_line_of_an_earlier_one():
    rng = random.Random(3)
    for _ in range(40):
        cells = {(i, j) for i in range(0, 12) for j in range(0, 12) if (i + j) % 2 == 0 and rng.random() < 0.3}
        enemies = {(rng.randrange(14, 20, 2), rng.randrange(0, 12, 2))}
        if len(cells) < 8:
            continue
        plan = plan_seats(TEAM, cells, enemies)
        placed = list(plan.items())
        for k, (name, cell) in enumerate(placed):
            seat = TEAM[k]
            before = {c for _, c in placed[:k]}
            aims = _targets(seat, cell, enemies, before | enemies)
            if not aims:
                continue
            later = {c for _, c in placed[k + 1:]}
            cut = [c for c in later if not line_of_sight(cell, aims[0], {c})]
            free_lines = any(line_of_sight(cell, aims[0], {c}) for c in cells - set(plan.values()))
            assert not cut or not free_lines, (name, cell, aims[0], cut)


def test_blocks_count_for_the_line_of_sight():
    enemies = {(10, 0)}
    cells = {(4, 0), (2, 2)}
    assert plan_seats([Seat("a", (1, 8))], cells, enemies)["a"] == (2, 2)  # à 8 cases, la plus loin à portée
    assert plan_seats([Seat("a", (1, 8))], cells, enemies, blockers={(5, 1)})["a"] == (4, 0)


def test_two_blocks_touching_by_a_corner_leave_the_line_open():
    # 17/10, Incarnam : 20 lignes passant juste entre deux combattants qui se touchent par un coin, toutes montrées
    # dégagées par le jeu (sight._crossed).
    seats = plan_seats([Seat("a", (1, 8))], {(4, 0), (2, 0)}, {(10, 0)}, blockers={(3, 1), (3, -1)})
    assert seats["a"] == (2, 0)


def test_free_cell_preferred_to_an_equally_good_one_taken_by_someone_unknown():
    enemies = {(10, 0)}
    cells = {(4, 0), (4, 2), (2, 0)}
    seat = Seat("a", (1, 6))
    assert plan_seats([seat], cells, enemies, free={(4, 2), (2, 0)})["a"] == (4, 2)  # (4, 0) à 6 aussi, mais pris
    assert plan_seats([seat], cells, enemies, free={(2, 0)})["a"] in {(4, 0), (4, 2)}  # à portée avant tout
    assert plan_seats([seat], cells, enemies, free={(2, 0)}, at={"a": (4, 0)})["a"] == (4, 0)  # c'est la sienne


def test_characters_already_in_place_keep_their_cell():
    enemies = {(10, 0)}
    cells = {(4, 0), (2, 0), (0, 0)}
    plan = plan_seats([Seat("a", (1, 6)), Seat("b", (1, 6))], cells, enemies, fixed={"b": (4, 0)})
    assert plan == {"a": (2, 0), "b": (4, 0)}


# Passes de déplacements


def test_moves_follow_the_cells_freed_by_the_previous_pass():
    plan = {"a": (0, 0), "b": (2, 0), "c": (4, 0)}
    # a sur la case de b, b sur celle de c, c sur une case en trop ; (0, 0) libre.
    assert next_moves(plan, {}, {(0, 0)}, ["a", "b", "c"]) == [("a", (0, 0))]
    assert next_moves(plan, {"a": (0, 0)}, {(2, 0)}, ["a", "b", "c"]) == [("b", (2, 0))]
    assert next_moves(plan, {"a": (0, 0), "b": (2, 0)}, {(4, 0)}, ["a", "b", "c"]) == [("c", (4, 0))]


def test_detour_when_nobody_can_move_then_the_last_one_by_elimination():
    plan = {"a": (0, 0), "b": (2, 0)}
    # a et b chacun sur la case de l'autre (ou déjà sur la leur : on ne sait pas), une seule case libre en trop.
    assert next_moves(plan, {}, {(8, 0)}, ["a", "b"], detour=False) == []
    assert next_moves(plan, {}, {(8, 0)}, ["a", "b"]) == [("a", (8, 0))]  # le premier à jouer fait le détour
    assert by_elimination({"a": (8, 0)}, ["a", "b"], {(8, 0), (2, 0)}) == ("b", (2, 0))
    assert by_elimination({}, ["a", "b"], {(8, 0), (2, 0)}) is None


def simulate(cells, enemies, blockers, rng, passes=3):
    """Une partie simulée : personnages placés au hasard par le jeu, passes comme TeamBot._seat_team. Renvoie (cases
    réelles, cases reconnues, nombre de passes)."""
    at = dict(zip(ORDER, rng.sample(sorted(cells), len(ORDER))))
    known, fixed, done = {}, {}, 0
    while done < passes:
        free = cells - set(at.values())
        plan = plan_seats(TEAM, cells, enemies, blockers, fixed, free, known)
        fixed.update({n: c for n, c in known.items() if plan.get(n) == c})
        moves = [(n, c) for n, c in next_moves(plan, known, free, ORDER, done < passes - 1) if n not in fixed]
        if not moves:
            break
        done += 1
        for name, cell in moves:
            at[name] = known[name] = cell
            if plan[name] == cell:
                fixed[name] = cell
        guess = by_elimination(known, ORDER, set(at.values()))
        if guess:
            known[guess[0]] = guess[1]
    return at, known, done


@pytest.mark.parametrize("seed", range(3))
def test_simulated_placements_on_the_fixture_maps(seed):
    """Sur les cases de placement réelles des captures : au plus 3 passes, ceux qui sont reconnus le sont sur leur
    vraie case, et les 4 premiers à jouer ont plus souvent un ennemi à portée qu'en ne bougeant pas."""
    rng = random.Random(seed)
    firing_before = firing_after = 0
    for _, cells, enemies, blockers in fixture_maps():
        for _ in range(4):
            start = dict(zip(ORDER, rng.sample(sorted(cells), len(ORDER))))
            firing_before += sum(fires(s, start[s.name], start.values(), enemies, blockers) for s in TEAM[:4])
            at, known, done = simulate(cells, enemies, blockers, rng)
            assert done <= 3
            assert all(at[name] == cell for name, cell in known.items())
            assert len(set(at.values())) == len(ORDER)
            firing_after += sum(fires(s, at[s.name], at.values(), enemies, blockers) for s in TEAM[:4])
    assert firing_after > firing_before


# Sur les captures de placement


@pytest.mark.parametrize("name,cells,enemies,blockers", list(fixture_maps()), ids=lambda v: v if isinstance(v, str)
                         else "")
def test_plan_on_real_placement_cells(name, cells, enemies, blockers):
    plan = plan_seats(TEAM, cells, enemies, blockers)
    assert set(plan) == set(ORDER) and len(set(plan.values())) == len(ORDER)
    assert set(plan.values()) <= cells
    first, last = TEAM[0], TEAM[-1]
    if any(fires(first, c, set(), enemies, blockers) for c in cells):
        assert fires(first, plan[first.name], [plan[n] for n in ORDER[:1]], enemies, blockers)
    assert nearest(plan[first.name], enemies) <= nearest(plan[last.name], enemies)
    assert all(nearest(c, enemies) > 1 for c in plan.values()) or all(nearest(c, enemies) <= 1 for c in cells)


def test_crowded_team_cells_still_read_as_occupied():
    # 8 personnages sur les 10 cases de l'équipe : la plupart des cases sont occupées (couvertures mesurées : 0,35 à
    # 0,94 occupées, 0,94 à 1,0 libres). Le 3e quartile serait celui d'une case occupée.
    coverages = {(i, 0): c for i, c in enumerate([0.62, 0.7, 0.85, 0.78, 0.66, 0.81, 0.73, 0.84, 0.97, 0.99])}
    free, occupied = split_cells(coverages)
    assert free == {(8, 0), (9, 0)} and len(occupied) == 8
    free, occupied = split_cells({(0, 0): 0.6, (1, 0): 0.8})  # toutes occupées
    assert free == set()
