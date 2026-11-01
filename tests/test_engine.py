"""Moteur de tour (brain/engine.py) : déplacement + sorts + repli, pour toute classe, sans écran."""

import logging
import random
import time
from dataclasses import replace

import numpy as np

from bot.brain.combat import CombatAI, CombatSettings, MapMemory
from bot.brain.engine import (CAST, MOVE, POSITION, RETREAT, Board, Weights, contact_cells, crossed, in_zone,
                              plan_turn, walk_steps)
from bot.brain.spellbook import Spell, load_reference
from bot.brain.tactics import CombatSpell, SpellUse, refusals, refused_beyond
from bot.perception.colors import RED
from bot.perception.fighters import find_fighters
from bot.perception.grid import distance
from bot.perception.sight import line_of_sight, read_terrain
from bot.perception.spellbook import SpellEntry
from conftest import load_frame
from test_brain import FakeGame, book_of_the_28_09, clicks, presses

# Les sorts du Crâ du 28/09 (niveaux lus dans la fenêtre, valeurs Solomonk).
TIR = CombatSpell(6, "tir_eloigne", "Tir Éloigné", 2, (0, 0), in_line=True, range_modifiable=False, cooldown=5,
                  range_bonus=1, bonus_turns=3, role="boost_portee")
MAGIQUE = CombatSpell(3, "fleche_magique", "Flèche Magique", 4, (1, 10), per_target=2, damage=13)
RECUL = CombatSpell(1, "fleche_de_recul", "Flèche de Recul", 4, (1, 4), in_line=True, range_modifiable=False,
                    per_target=1, damage=13.5, push=3, role="repousser")
ENFLAMMEE = CombatSpell(5, "fleche_enflammee", "Flèche Enflammée", 4, (1, 4), in_line=True, per_turn=2, damage=15,
                        push=1, zone=("ligne", 4), role="degats_zone")
CELLS = frozenset((i, j) for i in range(-20, 21) for j in range(-20, 21) if (i + j) % 2 == 0)
OPEN = Board(CELLS)  # carte connue, sans obstacle : seuls les combattants coupent la ligne de vue


def new_use(pa=6):
    use = SpellUse()
    use.new_turn(pa)
    return use


def plan(me, pm, enemies, spells, pa=6, allies=(), board=OPEN, weights=Weights(), use=None):
    return plan_turn(me, pm, list(enemies), list(allies), spells, use or new_use(pa), board, weights)


def kinds(turn):
    return [s.kind for s in turn.steps]


def solomonk_spell(spell_id: str, slot: int = 1, level: int = 1) -> CombatSpell:
    """Sort de la référence Solomonk, à ce niveau, PA et portée pris chez Solomonk (fenêtre illisible)."""
    spell = load_reference()["sorts"][spell_id]
    entry = SpellEntry(0, None, None, True, np.zeros((46, 46, 3), np.uint8), level=level)
    return Spell(entry, spell["cle"], spell["nom"], spell["niveaux"][level - 1], "degats").combat_spell(slot)


def test_one_step_to_line_up_two_enemies_beats_flèche_magique_from_here():
    # D'ici, ni l'un ni l'autre n'est aligné : Flèche Magique (13). Un pas en (-1, 1) les aligne tous les deux
    # dans l'axe de la Flèche Enflammée : 15 + 13,5 (zone dégressive, 1 case derrière) (demande de l'utilisateur, 01/10).
    enemies = [(3, 5), (4, 6)]
    assert plan((0, 0), 0, enemies, [MAGIQUE, ENFLAMMEE], pa=4).first.spell is MAGIQUE
    turn = plan((0, 0), 3, enemies, [MAGIQUE, ENFLAMMEE], pa=4)
    assert kinds(turn) == [MOVE, CAST]
    move, cast = turn.steps
    assert move.cell == (-1, 1) and move.pm == 1
    assert cast.spell is ENFLAMMEE and set(cast.hits) == {(3, 5), (4, 6)} and turn.damage == 28.5
    assert turn.describe().startswith("Plan : aller en (-1, 1) (1 PM), Flèche Enflammée sur (3, 5) (2 ennemis touchés)")


def test_always_hit_but_leave_contact_when_possible():
    # 03/10 : un seul style, polyvalent (« il faut quand même taper un ennemi ») : un petit coup au corps à corps se
    # tente, avec le PM restant pour sortir du contact ; un coup minuscule ne vaut pas de finir son tour au contact.
    small = CombatSpell(1, "coup", "Coup", 4, (1, 1), damage=6)
    turn = plan((0, 0), 3, [(3, 1)], [small])
    assert kinds(turn) == [MOVE, CAST, RETREAT] and turn.steps[0].pm == 2
    assert distance(turn.steps[2].cell, (3, 1)) >= 2 and turn.steps[2].reason.startswith("hors du contact")
    tiny = CombatSpell(1, "pichenette", "Pichenette", 4, (1, 1), damage=2)
    assert not plan((0, 0), 2, [(3, 1)], [tiny]).casts  # 2 PM : il faudrait finir au contact pour 2 dégâts


def test_after_casting_in_contact_step_out_and_stay_in_range():
    turn = plan((0, 0), 3, [(1, 1)], [MAGIQUE], pa=4)
    assert kinds(turn) == [CAST, RETREAT]  # lancer d'abord (d'ici, c'est sûr), puis s'écarter
    retreat = turn.steps[1]
    assert distance(retreat.cell, (1, 1)) >= 2 and retreat.pm == 1  # hors contact, sans dépenser plus de PM
    assert kinds(plan((0, 0), 0, [(1, 1)], [MAGIQUE], pa=4)) == [CAST]  # sans PM : on reste


def test_no_flight_after_hitting_out_of_contact():
    # Plus de style prudent (fuite après avoir touché) : à 2 cases, on tape et on reste, à portée pour le tour suivant.
    assert kinds(plan((0, 0), 3, [(2, 0)], [MAGIQUE], pa=4)) == [CAST]


def test_casts_per_target_are_respected():
    turn = plan((0, 0), 0, [(8, 0)], [MAGIQUE], pa=12)
    assert [s.cell for s in turn.casts] == [(8, 0), (8, 0)]  # 2 lancers par cible, il reste 4 PA
    turn = plan((0, 0), 0, [(8, 0), (6, 2)], [MAGIQUE], pa=12)
    assert sorted(s.cell for s in turn.casts) == [(6, 2), (6, 2), (8, 0)] or \
        sorted(s.cell for s in turn.casts) == [(6, 2), (8, 0), (8, 0)]
    use = new_use(8)
    use.record(MAGIQUE, (8, 0))  # déjà lancé ce tour sur lui
    assert [s.cell for s in plan((0, 0), 0, [(8, 0)], [MAGIQUE], use=use).casts] == [(8, 0)]


def test_range_bonus_first_then_the_attack():
    turn = plan((0, 0), 0, [(11, 1)], [TIR, MAGIQUE])  # à 11 cases : 10 + 1 avec le bonus
    assert [(s.spell, s.cell) for s in turn.steps] == [(TIR, (0, 0)), (MAGIQUE, (11, 1))]
    # Rien ne touche, même avec le bonus (25/10, l'utilisateur) : le bonus (3 tours) est lancé quand même, ses PA ne
    # servaient à rien ; au tour suivant, ils iront aux attaques.
    assert [s.spell for s in plan((0, 0), 0, [(14, 0)], [TIR, MAGIQUE]).casts] == [TIR]
    turn = plan((0, 0), 3, [(14, 0)], [TIR, MAGIQUE])  # 3 pas vers lui : 11 cases, bonus puis Flèche Magique
    assert kinds(turn) == [MOVE, CAST, CAST] and turn.steps[1].spell is TIR and turn.steps[2].spell is MAGIQUE
    assert distance(turn.steps[0].cell, (14, 0)) == 11


def test_push_is_simulated_for_the_next_spells():
    turn = plan((0, 0), 0, [(2, 2)], [RECUL, MAGIQUE], pa=8)
    assert [(s.spell, s.cell) for s in turn.casts] == [(RECUL, (2, 2)), (MAGIQUE, (5, 5))]  # repoussé de 3 cases
    assert "danger" in turn.steps[0].reason
    walled = Board(CELLS, unwalkable=frozenset({(4, 4)}), obstacles=frozenset({(4, 4)}))
    turn = plan((0, 0), 0, [(2, 2)], [RECUL, MAGIQUE], pa=8, board=walled)
    assert [s.cell for s in turn.casts] == [(2, 2), (3, 3)]  # arrêté par le bloc après 1 case
    assert turn.steps[0].value == 13.5 + 2 * 2.0  # dégâts de poussée : 2 cases de recul empêchées sur 3
    relief = Board(CELLS, unwalkable=frozenset({(4, 4)}), obstacles=frozenset({(4, 4)}), reliable=False)
    turn = plan((0, 0), 0, [(2, 2)], [RECUL, MAGIQUE], pa=8, board=relief)
    assert turn.steps[0].value == 13.5  # carte à relief : obstacle mal connu, pas de dégâts de poussée supposés


def test_moving_must_be_worth_it():
    # 01/10 : 3 PM pour une Flèche Enflammée à 15 au lieu de Flèche Magique à 13 d'ici. 1 point par PM : 15 - 3 < 13.
    turn = plan((0, 0), 3, [(7, 7)], [MAGIQUE, ENFLAMMEE], pa=4)
    assert kinds(turn) == [CAST] and turn.casts[0].spell is MAGIQUE
    # Sur une carte à relief, bouger coûte le double : les prévisions faites pour une autre case y sont un pari.
    relief = Board(CELLS, reliable=False)
    assert kinds(plan((0, 0), 3, [(5, 5)], [MAGIQUE, ENFLAMMEE], pa=4, board=relief)) == [CAST]


def test_pushing_an_enemy_i_walked_up_to_is_no_danger_removed():
    # Ennemi à 6 cases au début du tour : s'en approcher à 3 cases pour le repousser n'écarte aucun danger.
    # Le moteur va juste à portée (4 cases, 2 PM) au lieu de 3 PM (danger compté depuis ma case de lancer).
    turn = plan((0, 0), 3, [(6, 6)], [RECUL], pa=4)
    assert kinds(turn) == [MOVE, CAST] and turn.steps[0].pm == 2 and distance(turn.steps[0].cell, (6, 6)) == 4


def test_zone_refused_on_an_empty_cell_tries_no_other_empty_cell_from_here():
    # 18/10, Champs 4,3 : Flèche Enflammée refusée sur 3 cases vides alignées vers un ennemi caché par un bloc, ~20 s.
    poussiere = solomonk_spell("96")  # cercle de 2 : vise une case vide entre les ennemis
    enemies = [(3, 1), (5, 1), (4, 4)]
    first = plan((0, 0), 0, enemies, [poussiere]).first
    assert first.cell not in enemies
    use = new_use(6)
    use.empty_refused.add(poussiere.spell_id)
    again = plan((0, 0), 0, enemies, [poussiere], use=use).first
    assert again is None or again.cell in enemies  # plus de case vide d'ici ; un ennemi visé reste possible


def test_zone_refused_twice_on_empty_cells_aims_only_at_enemies_for_the_fight():
    # 18/10, Chafers 3,6 : la même case vide refusée depuis 3 cases de suite (un pas entre chaque efface les refus d'ici).
    from bot.brain.tactics import EMPTY_REFUSALS_LIMIT

    poussiere = solomonk_spell("96")
    enemies = [(3, 1), (5, 1), (4, 4)]
    use = new_use(6)
    use.empty_refusals[poussiere.spell_id] = EMPTY_REFUSALS_LIMIT  # empty_refused vide : un pas vient d'être fait
    again = plan((0, 0), 0, enemies, [poussiere], use=use).first
    assert again is None or again.cell in enemies


def test_ally_is_never_targeted_nor_caught_in_a_zone():
    # Allié juste derrière l'ennemi : la ligne de la Flèche Enflammée le prendrait -> Flèche Magique.
    turn = plan((0, 0), 0, [(2, 2)], [MAGIQUE, ENFLAMMEE], pa=4, allies=[(3, 3)])
    assert turn.first.spell is MAGIQUE and turn.first.cell == (2, 2)
    assert plan((0, 0), 0, [], [MAGIQUE, ENFLAMMEE], allies=[(2, 2)]).steps == ()  # un allié n'est jamais une cible
    # Allié entre l'ennemi et moi : il coupe la ligne de vue (aussi depuis les 4 cases à 1 PM), 2 PM la dégagent.
    turn = plan((0, 0), 3, [(4, 0)], [MAGIQUE], pa=4, allies=[(2, 0)])
    assert not line_of_sight((0, 0), (4, 0), {(2, 0)})
    assert kinds(turn) == [MOVE, CAST] and turn.steps[0].pm == 2
    assert line_of_sight(turn.steps[0].cell, (4, 0), {(2, 0)})


def test_circle_zone_of_another_class_aims_at_an_empty_cell():
    poussiere = solomonk_spell("96")  # Xélor, Poussière Temporelle niveau 1 : cercle de 2, portée 0 à 5, 6 PA
    assert poussiere.zone == ("cercle", 2) and poussiere.spell_range == (0, 5) and poussiere.damage == 5.5
    enemies = [(3, 1), (5, 1), (4, 4)]  # aucun ennemi n'a les deux autres à 2 cases ou moins
    first = plan((0, 0), 0, enemies, [poussiere]).first
    assert set(first.hits) == set(enemies) and first.cell not in enemies  # une case vide entre les trois
    assert first.describe() == f"Poussière Temporelle sur la case {first.cell} (3 ennemis touchés)"
    with_ally = plan((0, 0), 0, enemies, [poussiere], allies=[(3, 3)]).first
    assert len(with_ally.hits) == 2 and not in_zone("cercle", 2, (0, 0), with_ally.cell, (3, 3))


def test_self_centred_zone_spell():
    epee = solomonk_spell("145")  # Iop, Épée Divine : croix de 1 autour de moi, sans ligne de vue
    assert epee.on_self and epee.zone == ("croix", 1)
    turn = plan((0, 0), 0, [(1, 1), (-1, 1), (3, 3)], [epee])
    assert turn.first.spell is epee and turn.first.cell == (0, 0) and set(turn.first.hits) == {(1, 1), (-1, 1)}


def test_zone_shapes():
    me = (0, 0)
    assert in_zone("ligne", 2, me, (2, 2), (4, 4)) and not in_zone("ligne", 2, me, (2, 2), (5, 5))
    assert not in_zone("ligne", 2, me, (2, 2), (1, 1))  # la ligne part de la cible, à l'opposé de moi
    assert not in_zone("ligne", 2, me, (2, 0), (3, 1))  # tir pas aligné : la cible seule
    assert in_zone("croix", 2, me, (5, 5), (3, 7)) and not in_zone("croix", 2, me, (5, 5), (5, 7))
    assert in_zone("cercle", 1, me, (5, 5), (5, 7)) is False and in_zone("cercle", 2, me, (5, 5), (5, 7))
    assert in_zone("anneau", 1, me, (5, 5), (6, 6)) and not in_zone("anneau", 1, me, (5, 5), (5, 5))
    across = "ligne_perpendiculaire"
    assert in_zone(across, 1, me, (2, 2), (3, 1)) and in_zone(across, 1, me, (2, 2), (1, 3))
    assert not in_zone(across, 1, me, (2, 2), (3, 3))
    assert in_zone("point", 0, me, (5, 5), (5, 5)) and not in_zone("point", 0, me, (5, 5), (6, 6))


def test_walk_does_not_go_through_contact_cells():
    cells = {(i, j) for i in range(-6, 7) for j in range(-6, 7) if (i + j) % 2 == 0}
    enemy = (2, 0)
    stops = {(enemy[0] + di, enemy[1] + dj) for di, dj in ((1, 1), (1, -1), (-1, 1), (-1, -1))}
    steps = walk_steps((0, 0), 2, cells, {enemy}, stops)
    assert steps[(1, 1)] == 1  # on peut y entrer...
    assert (2, 2) not in steps  # ... mais pas en repartir dans le même déplacement
    assert steps[(0, 2)] == 2


def test_a_refusal_also_rules_out_the_spells_whose_range_fits_in_it():
    # 03/10, carte à relief : Flèche Magique refusée sur (9, 21), puis Flèche Glacée sur le même ennemi, de la même
    # case : la ligne de vue ne dépend pas du sort. 4 refus dans le tour, ~4 s perdues.
    glacee = CombatSpell(4, "fleche_glacee", "Flèche Glacée", 3, (1, 7), damage=10)
    sans_vue = CombatSpell(7, "sans_vue", "Sans vue", 3, (1, 5), needs_sight=False, damage=10)
    loin = CombatSpell(8, "loin", "Loin", 3, (1, 14), damage=10)
    spells = [MAGIQUE, ENFLAMMEE, glacee, sans_vue, loin, TIR]
    use = new_use()
    assert refusals(MAGIQUE, (9, 21), spells, use) == {(s.spell_id, (9, 21)) for s in (MAGIQUE, ENFLAMMEE, glacee)}
    # Refus d'un sort en ligne : peut-être la ligne ; les sorts qui ne la demandent pas restent permis.
    assert refusals(ENFLAMMEE, (9, 21), spells, use) == {("fleche_enflammee", (9, 21))}
    use.refused |= refusals(MAGIQUE, (2, 2), spells, use)
    assert plan((0, 0), 0, [(2, 2)], [MAGIQUE, glacee, sans_vue], use=use).first.spell is sans_vue


def test_relief_margin_targets_count_for_half():
    # 03/10 : Flèche Enflammée (15, portée 1 à 4 ici) essayée à 6 cases grâce à la marge d'une carte à relief, avant
    # Flèche Magique (13, à portée) : refusée.
    relief = Board(CELLS, reliable=False, margin=4)
    assert plan((0, 0), 0, [(6, 6)], [ENFLAMMEE, MAGIQUE], pa=4, board=relief).first.spell is MAGIQUE
    assert plan((0, 0), 0, [(6, 6)], [ENFLAMMEE], pa=4, board=relief).first.spell is ENFLAMMEE  # seule chance : tentée


def test_refused_beyond_its_range_no_farther_target_from_here():
    # 11/10 : Gela, carte à relief, 4 refus de suite à 10 cases (portée 1 à 9), une autre case à chaque fois.
    relief = Board(CELLS, reliable=False, margin=4)
    loin = CombatSpell(8, "loin", "Loin", 3, (1, 14), damage=10)
    use = new_use()
    spells = [MAGIQUE, ENFLAMMEE, loin, TIR]
    assert refused_beyond(MAGIQUE, 9, spells, use) == {}  # à portée : refus propre à la case (ligne de vue)
    use.beyond.update(refused_beyond(MAGIQUE, 12, spells, use))
    assert use.beyond == {"fleche_magique": 12, "fleche_enflammee": 12}  # pas « Loin », qui porte plus loin
    far = plan((0, 0), 0, [(12, 2)], [MAGIQUE], use=use, board=relief)
    assert CAST not in kinds(far)
    nearer = plan((0, 0), 0, [(11, 1)], [MAGIQUE], use=use, board=relief)
    assert nearer.first.spell is MAGIQUE and nearer.first.cell == (11, 1)  # moins loin : encore une chance
    use.new_turn(6)
    assert use.beyond == {}


def test_unseen_cells_are_neither_destinations_nor_passages():
    # Portée 1 à 5, ennemi à 7 cases : la seule case d'où le toucher avec 2 PM est (2, 2), par (1, 1).
    coup = CombatSpell(3, "coup", "Coup", 4, (1, 5), damage=13)
    move = plan((0, 0), 2, [(7, 7)], [coup], pa=4).first
    assert move.kind == MOVE and move.cell == (2, 2)
    for hidden in ((1, 1), (2, 2)):  # cachée par un corps depuis le début du combat : peut-être un trou
        turn = plan((0, 0), 2, [(7, 7)], [coup], pa=4, board=Board(CELLS, unseen=frozenset({hidden})))
        assert all(s.cell != (2, 2) for s in turn.steps)


def test_hole_hidden_behind_an_ally_is_not_walked_on():
    # 03/10 : (20, 10), un trou, était caché par le corps de Jesuisleking en (20, 12) ; l'Enutrof en (22, 12) a voulu
    # y aller (pas bougé), puis en (19, 11), par ce trou (arrêté en (21, 11), 2 PM perdus).
    frame = load_frame("combat_trou_cache")
    fighters = find_fighters(frame)
    assert {(f.team, f.cell) for f in fighters} >= {("red", (22, 12)), ("red", (20, 12)), ("red", (20, 14))}
    memory = MapMemory()
    memory.learn_terrain(read_terrain(frame, fighters), fighters)
    cells = set(frame.grid.cells())
    assert (20, 10) in memory.unseen(cells) and (20, 10) not in memory.unwalkable
    assert (22, 12) not in memory.unseen(cells)  # case d'un combattant : du sol
    walk = walk_steps((22, 12), 3, cells, memory.unwalkable | memory.unseen(cells) | {f.cell for f in fighters})
    assert walk[(21, 11)] == 1 and (20, 10) not in walk and (19, 11) not in walk
    # Vue dans une autre fenêtre (carte partagée par l'équipe), le trou est connu.
    memory.unwalkable.add((20, 10))
    memory.seen.add((20, 10))
    assert (20, 10) not in memory.unseen(cells)
    assert MapMemory().unseen(cells) == set()  # terrain pas encore lu : rien n'est bloqué d'office


def test_a_wall_tinted_by_a_range_is_no_longer_an_obstacle():
    # 17/10, Incarnam : côté d'un plateau lu bloc plus tôt, puis vu teinté par la portée d'un sort : pas un bloc.
    from bot.perception.sight import RangeView

    memory = MapMemory()
    memory.obstacles |= {(19, 17), (5, 5)}
    memory.learn_range(RangeView({(20, 16)}, set(), {(6, 6)}, (1, 7), walls=frozenset({(19, 17)})))
    assert memory.obstacles == {(5, 5), (6, 6)}


def test_cached_sight_lines_match_line_of_sight():
    rng = random.Random(1)
    cells = [(i, j) for i in range(0, 30) for j in range(0, 30) if (i + j) % 2 == 0]
    blockers = set(rng.sample(cells, 60))
    for _ in range(400):
        a, b = rng.sample(cells, 2)
        assert crossed(a, b).isdisjoint(blockers - {a, b}) == line_of_sight(a, b, blockers - {a, b}), (a, b)


def test_planning_is_fast_with_the_real_spells_and_map():
    book = book_of_the_28_09()
    book.slots = {spell.entry.row + 1: spell.entry for spell in book.spells.values()}
    spells = book.combat_spells()  # les 6 sorts du Crâ
    frame = load_frame("combat_mon_tour")
    terrain = read_terrain(frame, find_fighters(frame))
    board = Board(frozenset(frame.grid.cells()), frozenset(terrain.blocks | terrain.holes), frozenset(terrain.blocks))
    crossed.cache_clear()
    started = time.perf_counter()
    turn = plan((9, 21), 3, [(14, 15), (15, 16), (12, 12), (16, 22)], spells, pa=12, board=board)
    elapsed = time.perf_counter() - started
    # Mesuré le 01/10 : 11 ms (3 PM, 12 PA, 4 ennemis), 55 ms avec 6 PM ; le 03/10, avec le jeu d'équipe et la case de fin
    # de tour jugée pour chaque suite : 39 ms (14 ms cache des lignes de vue plein), 135 ms avec 6 PM.
    assert turn.casts and elapsed < 0.5


def test_plan_kept_once_moved_to_cast():
    # Arrivé sur la case choisie pour lancer (move_first faux) : on lance d'ici, sans nouveau détour, même si une autre
    # case semble un peu meilleure ; le repli hors du contact reste permis.
    enemies = [(3, 5), (4, 6)]
    free = plan_turn((0, 0), 3, enemies, [], [MAGIQUE, ENFLAMMEE], new_use(4), OPEN, Weights())
    assert kinds(free) == [MOVE, CAST]  # un pas pour aligner les deux ennemis (voir plus haut)
    kept = plan_turn((0, 0), 3, enemies, [], [MAGIQUE, ENFLAMMEE], new_use(4), OPEN, Weights(), move_first=False)
    assert kinds(kept) == [CAST] and kept.casts[0].spell is MAGIQUE
    small = CombatSpell(1, "coup", "Coup", 4, (1, 1), damage=6)
    in_contact = plan_turn((0, 0), 3, [(1, 1)], [], [small], new_use(4), OPEN, Weights(), move_first=False)
    assert kinds(in_contact) == [CAST, RETREAT]


# --- Jeu d'équipe (03/10 : 4 personnages, bientôt 8) ---------------------------------------------------------------

WIDE = Board(frozenset((i, j) for i in range(-5, 40) for j in range(-5, 40) if (i + j) % 2 == 0))
RONCE = CombatSpell(1, "ronce", "Ronce", 4, (1, 8), per_target=2, damage=13.5)
# Tours 1 et 2 de Fourbocinq (Sadida) le 03/10 : le Pichon Bleu en (13, 21), Imthebest à son contact, Jesuisleking et
# Walitcoin entre eux et moi.
TOUR_1 = {(10, 24): "Jesuisleking", (12, 22): "Imthebest", (12, 26): "Walitcoin"}
TOUR_2 = {(11, 23): "Jesuisleking", (12, 22): "Imthebest", (12, 26): "Walitcoin"}


def hit_next_turn(cell, pm, enemy, allies, board, spell_range=(1, 8)) -> bool:
    """Au tour suivant, avec `pm` PM : une case d'où l'ennemi est à portée, ligne de vue dégagée (obstacles et alliés),
    si personne ne bouge d'ici là."""
    low, high = spell_range
    walls = (board.obstacles or frozenset()) | set(allies)
    walk = walk_steps(cell, pm, board.cells, board.unwalkable | board.unseen | set(allies) | {enemy},
                      contact_cells([enemy]))
    return any(low <= distance(c, enemy) <= high and crossed(c, enemy).isdisjoint(walls) for c in walk)


def test_no_queue_behind_allies_when_nothing_hits():
    # Imthebest au contact de l'ennemi coupe toute ligne de vue venant de « derrière » lui : aucune case à 3 PM ne
    # touche. Avant : 1 PM tout droit, collé derrière Jesuisleking (et de là, toujours rien au tour suivant). Maintenant :
    # tous les PM, de côté, vers une case d'où taper au prochain tour, sans se coller à personne.
    me, enemy = (8, 26), (13, 21)
    turn = plan_turn(me, 3, [enemy], list(TOUR_1), [RONCE], new_use(6), WIDE, Weights(), names=TOUR_1)
    (step,) = turn.steps
    assert step.kind == POSITION and step.pm == 3 and not turn.can_hit
    assert all(distance(step.cell, ally) > 1 for ally in TOUR_1)
    assert hit_next_turn(step.cell, 3, enemy, TOUR_1, WIDE) and not hit_next_turn((9, 25), 3, enemy, TOUR_1, WIDE)
    assert step.reason.startswith("aucun sort ne touche ce tour")
    assert "je ne me mets pas en (9, 25), tout droit : collé à Jesuisleking" in step.reason
    assert turn.describe() == f"Plan : me placer en {step.cell} pour le prochain tour (3 PM)"


def test_side_step_that_clears_my_line_of_sight_is_explained():
    # File indienne : Imthebest entre l'ennemi et moi (voir plus haut), 2 PM pour un pas de côté ; le journal dit
    # pourquoi.
    turn = plan_turn((0, 0), 3, [(4, 0)], [(2, 0)], [MAGIQUE], new_use(4), OPEN, Weights(), names={(2, 0): "Imthebest"})
    move, cast = turn.steps
    assert move.kind == MOVE and cast.cell == (4, 0)
    assert move.reason == "pour lancer d'ici, je me décale pour dégager ma ligne de vue (Imthebest la coupait)"


def test_i_do_not_stay_in_an_ally_line_of_sight():
    # Ma case coupe la ligne de vue de Walitcoin vers l'ennemi : je tape d'ici, puis un pas de côté la lui rend
    # (2 points par ligne coupée, plus qu'1 PM et un déplacement).
    turn = plan_turn((3, 3), 1, [(8, 8)], [(0, 0)], [MAGIQUE], new_use(4), OPEN, Weights(), names={(0, 0): "Walitcoin"})
    cast, retreat = turn.steps
    assert cast.kind == CAST and retreat.kind == RETREAT and retreat.pm == 1
    assert retreat.cell not in crossed((0, 0), (8, 8))
    assert retreat.reason == "je ne coupe plus la ligne de vue de Walitcoin"
    # Sans PM : je reste, et je tape quand même.
    assert kinds(plan_turn((3, 3), 0, [(8, 8)], [(0, 0)], [MAGIQUE], new_use(4), OPEN, Weights())) == [CAST]


def test_among_cells_to_cast_from_the_one_that_blocks_no_ally():
    # Ennemi à 6 cases, portée 1 à 5 : deux cases à 1 PM d'où taper. Seul, la première venue ; avec Jesuisleking dont
    # la ligne de vue vers l'ennemi passe par elle, l'autre.
    glacee = CombatSpell(4, "fleche_glacee", "Flèche Glacée", 3, (1, 5), damage=10)
    me, enemy, ally = (0, 0), (6, 0), (-2, -2)
    alone = plan_turn(me, 1, [enemy], [], [glacee], new_use(3), OPEN, Weights())
    assert kinds(alone) == [MOVE, CAST] and alone.first.cell in crossed(ally, enemy)
    turn = plan_turn(me, 1, [enemy], [ally], [glacee], new_use(3), OPEN, Weights())
    move, cast = turn.steps
    assert move.kind == MOVE and move.pm == 1 and cast.cell == enemy and move.cell not in crossed(ally, enemy)


def test_weakened_enemy_finished_and_team_target_preferred():
    # Vie lue au survol (MapMemory.life, par moi ou un allié) : achever l'ennemi à 5 PV (il ne jouera plus) passe devant
    # 13 dégâts sur l'autre ; vies connues plus hautes : le plus entamé reste la cible de l'équipe.
    enemies = [(3, 1), (1, 7)]
    assert plan_turn((0, 0), 0, enemies, [], [MAGIQUE], new_use(4), WIDE, Weights()).first.cell == (3, 1)  # inconnues
    finished = plan_turn((0, 0), 0, enemies, [], [MAGIQUE], new_use(4), WIDE, Weights(), life={(1, 7): 5})
    assert finished.first.cell == (1, 7)
    focus = plan_turn((0, 0), 0, enemies, [], [MAGIQUE], new_use(4), WIDE, Weights(), life={(1, 7): 60, (3, 1): 80})
    assert focus.first.cell == (1, 7)
    # Dégâts au-delà de la vie : perdus. 8 PA, 5 PV : un seul lancer sur lui, le second sur l'autre.
    two = plan_turn((0, 0), 0, enemies, [], [MAGIQUE], new_use(8), WIDE, Weights(), life={(1, 7): 5})
    assert sorted(s.cell for s in two.casts) == [(1, 7), (3, 1)]


def test_refused_from_here_but_not_from_the_next_cell():
    # Curseur interdit sur l'ennemi depuis ma case (ligne de vue que je ne prévoyais pas) : un pas de côté pour lancer,
    # plutôt que rien (refusé d'ici seulement ; combat.py oublie les refus après un déplacement).
    use = new_use(4)
    use.refused |= refusals(MAGIQUE, (4, 0), [MAGIQUE], use)
    turn = plan((0, 0), 3, [(4, 0)], [MAGIQUE], use=use)
    assert kinds(turn) == [MOVE, CAST] and turn.steps[0].pm == 1 and turn.steps[1].cell == (4, 0)


def test_far_enemy_approached_with_all_pm_at_once():
    # Hors de portée même après 3 PM : tous les PM vers lui (avant : choose_destination, en style agressif).
    turn = plan((0, 0), 3, [(20, 0)], [MAGIQUE], pa=4, board=WIDE)
    (step,) = turn.steps
    assert step.kind == POSITION and step.pm == 3 and distance(step.cell, (20, 0)) == 17
    # À 10 cases, portée 1 à 7 : juste ce qu'il faut pour l'avoir à portée au tour suivant, sans aller plus près.
    fleche = CombatSpell(3, "fleche", "Flèche", 4, (1, 7), damage=13)
    near = plan((0, 0), 3, [(10, 0)], [fleche], pa=0, board=WIDE)
    assert distance(near.first.cell, (10, 0)) == 7 and "un ennemi à portée et en vue d'ici" in near.first.reason


def test_no_step_back_to_look_for_a_line_of_sight():
    # 03/10 : à 11 cases sans ligne de vue, le bot est parti à 12 cases. Une case à 1 PM d'où taper plus près : pas de
    # recul au bout de la portée pour la même chose (au-delà de 5 cases, la distance ne compte plus).
    me, enemy = (20, 14), (9, 15)
    wall = sorted(crossed(me, enemy))[0]
    board = Board(WIDE.cells, frozenset({wall}), frozenset({wall}))
    magique = CombatSpell(3, "fleche_magique", "Flèche Magique", 4, (1, 12), damage=13)
    turn = plan(me, 1, [enemy], [magique], pa=4, board=board)
    assert kinds(turn) == [MOVE, CAST] and distance(turn.first.cell, enemy) < distance(me, enemy)


def test_nothing_to_do_without_a_better_cell():
    walled = Board(OPEN.cells, frozenset({(1, 1), (1, -1), (-1, 1), (-1, -1)}))
    assert plan((0, 0), 3, [(2, 0)], [MAGIQUE], pa=0, board=walled).steps == ()


def test_eight_characters_plan_quickly():
    # Bientôt 8 personnages : 7 alliés et 8 ennemis sur la vraie carte de combat_mon_tour, 6 sorts du Crâ, 12 PA.
    book = book_of_the_28_09()
    book.slots = {spell.entry.row + 1: spell.entry for spell in book.spells.values()}
    spells = book.combat_spells()
    frame = load_frame("combat_mon_tour")
    terrain = read_terrain(frame, find_fighters(frame))
    board = Board(frozenset(frame.grid.cells()), frozenset(terrain.blocks | terrain.holes), frozenset(terrain.blocks))
    rng = random.Random(8)
    me, *rest = rng.sample([c for c in sorted(terrain.floor) if 6 <= c[0] <= 26 and 8 <= c[1] <= 26], 16)
    allies, enemies = rest[:7], rest[7:]
    crossed.cache_clear()
    started = time.perf_counter()
    turn = plan_turn(me, 3, enemies, allies, spells, new_use(12), board, Weights(), life={enemies[0]: 20})
    elapsed = time.perf_counter() - started
    assert turn.casts and elapsed < 0.5  # mesuré le 03/10 : ~30 ms, cache des lignes de vue vide


# --- Cas réel du 03/10 : Fourbocinq (Sadida) derrière ses alliés (logs/20261003_193222, lignes 138 à 166, 258 à 269) -

SADIDA = [solomonk_spell("183", 1, level=5), solomonk_spell("181", 3, level=4), solomonk_spell("195", 9, level=1)]


def real_board(name: str) -> tuple[list, Board]:
    """Combattants et terrain lus sur la capture, comme en combat (MapMemory)."""
    frame = load_frame(name)
    fighters = find_fighters(frame)
    memory = MapMemory()
    memory.learn_terrain(read_terrain(frame, fighters), fighters)
    cells = set(frame.grid.cells())
    return fighters, Board(frozenset(cells), frozenset(memory.unwalkable), frozenset(memory.obstacles),
                           unseen=frozenset(memory.unseen(cells)))


def test_real_case_sadida_steps_aside_instead_of_queueing():
    # Tour 1 : « rien d'utile à faire d'ici », puis 1 PM vers (9, 25), collé derrière Jesuisleking ; tour 2 : 1 PM vers
    # (10, 24), en file derrière Jesuisleking et Imthebest. Les deux fois, 2 PM perdus et rien à taper au tour suivant.
    # Ronce (portée 1 à 8, ligne de vue) : de toutes les cases à 3 PM, la ligne passe par Imthebest, au contact du Pichon.
    assert [s.spell_id for s in SADIDA] == ["ronce", "tremblement", "larme"] and SADIDA[0].spell_range == (1, 8)
    for name, me, team, old in (("combat_sadida_derriere_allies_tour1", (8, 26), TOUR_1, (9, 25)),
                                ("combat_sadida_derriere_allies_tour2", (9, 25), TOUR_2, (10, 24))):
        fighters, board = real_board(name)
        assert {f.cell for f in fighters} == {me, (13, 21), *team}
        crossed.cache_clear()
        started = time.perf_counter()
        turn = plan_turn(me, 3, [(13, 21)], list(team), SADIDA, new_use(6), board, Weights(), names=team)
        elapsed = time.perf_counter() - started
        (step,) = turn.steps
        assert step.kind == POSITION and step.pm == 3 and all(distance(step.cell, a) > 1 for a in team), name
        assert hit_next_turn(step.cell, 3, (13, 21), team, board) and not hit_next_turn(old, 3, (13, 21), team, board)
        assert f"je ne me mets pas en {old}, tout droit : collé à Jesuisleking" in step.reason
        assert elapsed < 0.1  # mesuré le 03/10 : ~5 ms, cache des lignes de vue vide


def test_real_case_sadida_turn_uses_all_its_pm_and_says_why(caplog):
    # Le même tour 1, joué par CombatAI en mode équipe (cases et noms des alliés donnés par team/loop.py) : un seul
    # placement de 3 PM, sans sélectionner de sort, et le journal dit pourquoi, avec les noms.
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    frame, end = load_frame("combat_sadida_derriere_allies_tour1"), load_frame("combat_tour_adversaire")
    game = FakeGame(*[frame] * 30, end)
    ai = CombatAI(game, CombatSettings())
    ai._spells = SADIDA
    ai.play_turn(RED, is_mine=lambda f: f is not end, me_cell=(8, 26), team_cells=lambda: dict(TOUR_1))
    # Aucun sort sélectionné avant le placement. Après, la fausse partie ne montre pas le personnage arrivé : déplacement
    # raté depuis une case héritée, le bot revérifie où il est par la portée d'un sort (19/10).
    click = next(i for i, action in enumerate(game.actions) if action[0] == "click")
    assert [a for a in game.actions[:click] if a[0] == "press"] == []
    first = frame.grid.cell_at(*clicks(game)[0])
    assert distance(first, (8, 26)) == 3 and all(distance(first, ally) > 1 for ally in TOUR_1)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Placement pour le prochain tour"))
    assert line.startswith(f"Placement pour le prochain tour de (8, 26) vers {first} (3 PM) : aucun sort ne touche")
    assert "je ne me mets pas en (9, 25), tout droit : collé à Jesuisleking" in line


def test_relief_margin_does_not_bring_a_long_minimum_range_spell_too_close():
    # 04/10 : Flèche d'Expiation (portée 8 à 12) choisie contre un ennemi à 4 cases sur une carte à relief (marge de
    # 4 cases des deux côtés) : refusée par le jeu. La marge ne vaut que pour la portée maximale.
    expiation = CombatSpell(7, "fleche_d_expiation", "Flèche d'Expiation", 4, (8, 12), cooldown=3, damage=24)
    relief = Board(CELLS, reliable=False, margin=4)
    turn = plan((0, 0), 0, [(4, 4)], [expiation, MAGIQUE], pa=4, board=relief)
    assert turn.first.spell is MAGIQUE
    assert plan((0, 0), 0, [(7, 7)], [expiation], pa=4, board=relief).first.spell is expiation  # 1 case de marge


def test_zone_whose_shape_is_not_known_for_sure_never_risks_an_ally():
    # 04/10 (sorts de zone des personnages plus haut niveau) : une ligne tirée hors des axes part quand même dans une
    # direction qu'in_zone ne prévoit pas, une forme inconnue (« rectangle ») non plus : pour ne toucher ni un allié ni
    # moi, elles comptent comme un cercle de leur taille ; pour les ennemis touchés, la cible seule.
    griffe = CombatSpell(2, "griffe", "Griffe", 4, (1, 4), damage=20, zone=("ligne", 3))  # pas lancée « en ligne »
    enemy = (3, 1)  # pas dans un axe depuis (0, 0)
    alone = plan((0, 0), 0, [enemy], [griffe]).first
    assert alone.spell is griffe and alone.hits == (enemy,)
    assert not plan((0, 0), 0, [enemy], [griffe], allies=[(5, 3)]).casts  # 2 cases derrière la cible
    rectangle = CombatSpell(2, "rect", "Rectangle", 4, (1, 6), damage=20, zone=("rectangle", 2))
    assert plan((0, 0), 0, [(4, 0)], [rectangle]).first.spell is rectangle
    assert not plan((0, 0), 0, [(4, 0)], [rectangle], allies=[(5, 1)]).casts


def test_dominated_zone_targets_are_dropped_without_changing_the_plan(monkeypatch):
    # Une zone vise aussi les cases vides autour des ennemis ; celles qui ne font rien de mieux qu'une autre cible du
    # même sort sont écartées (04/10 : plans 2 à 3 fois plus rapides avec des zones) : même meilleur plan.
    import bot.brain.engine as engine

    explosive = solomonk_spell("179", 7, 3)  # Flèche Explosive : cercle de 2
    rng = random.Random(4)
    for _ in range(5):
        cells = rng.sample([c for c in CELLS if max(abs(c[0]), abs(c[1])) <= 6], 9)
        me, enemies, allies = cells[0], cells[1:6], cells[6:]
        pruned = plan(me, 2, enemies, [MAGIQUE, explosive, ENFLAMMEE], pa=8, allies=allies)
        with monkeypatch.context() as patched:
            patched.setattr(engine, "_undominated", lambda options: options)
            full = plan(me, 2, enemies, [MAGIQUE, explosive, ENFLAMMEE], pa=8, allies=allies)
        assert abs(pruned.score - full.score) < 1e-6 and pruned.sequences <= full.sequences


def test_sight_lines_traced_once_per_offset():
    # Le tracé ne dépend que de l'écart entre les deux cases (i + j pair partout) : même tracé décalé, où que soit la
    # ligne sur la carte ; line_of_sight trace de la même façon (un point à égale distance de deux cases tombait
    # d'un côté ou de l'autre selon la position sur la carte).
    rng = random.Random(2)
    cells = [(i, j) for i in range(-30, 31) for j in range(-30, 31) if (i + j) % 2 == 0]
    for _ in range(300):
        a, b = rng.sample(cells, 2)
        shifted = crossed((0, 0), (b[0] - a[0], b[1] - a[1]))
        assert crossed(a, b) == {(i + a[0], j + a[1]) for i, j in shifted}
        blockers = set(rng.sample(cells, 400))
        assert crossed(a, b).isdisjoint(blockers - {a, b}) == line_of_sight(a, b, blockers - {a, b}), (a, b)


def test_planning_with_zones_and_many_pa_stays_fast():
    # 04/10 : 6 sorts dont 2 zones, 10 PA, 5 PM, 7 ennemis, 4 alliés : 0,9 s par plan avant, refait après chaque sort ;
    # ~0,45 s maintenant (cibles dominées écartées, tracés par écart), et un seul plan complet par tour.
    from bot.brain.engine import _crossed_from_origin

    explosive, glacee = solomonk_spell("179", 7, 3), solomonk_spell("163", 4, 3)
    enemies = [(5, -1), (-1, 3), (7, 7), (2, 8), (-7, -7), (6, -4), (-5, -1)]
    crossed.cache_clear()
    _crossed_from_origin.cache_clear()
    started = time.perf_counter()
    turn = plan((-4, 0), 5, enemies, [MAGIQUE, ENFLAMMEE, RECUL, TIR, explosive, glacee], pa=10,
                allies=[(3, 5), (4, 8), (-3, -7), (3, 1)])
    assert turn.casts and time.perf_counter() - started < 2.0


def test_zone_on_an_empty_cell_is_aimed_clear_of_an_ally_body(monkeypatch):
    # Un clic sur un personnage le vise lui, pas la case : une zone visée sur la case vide juste derrière un allié
    # (son corps monte sur elle à l'écran) l'aurait pris pour cible. Le clic va sur un côté libre de la case.
    from bot.brain.combat import Cast
    from bot.perception.fighters import SPRITE_HALF_W, Fighter

    frame = load_frame("combat_mon_tour")
    grid = frame.grid
    game = FakeGame(frame)
    ai = CombatAI(game, CombatSettings())
    for name, stub in (("_select_spell", lambda before, spell: before), ("_learn_range", lambda *a: None),
                       ("_forbidden_target", lambda aim: False), ("_confirm_cast", lambda *a: None)):
        monkeypatch.setattr(ai, name, stub)
    explosive = CombatSpell(7, "fleche_explosive", "Flèche Explosive", 4, (1, 8), damage=20, zone=("cercle", 2))
    me, ally = Fighter(RED, *grid.center((8, 22)), (8, 22)), Fighter(RED, *grid.center((12, 18)), (12, 18))
    cell = (12, 16)  # juste au-dessus de l'allié à l'écran : le haut de son corps la couvre
    assert ai._cast(frame, me, Fighter("", *grid.center(cell), cell), [me, ally], RED, explosive) is Cast.DONE
    (x, y), = [a[1] for a in game.actions if a[0] == "move"]
    assert abs(x - ally.x) >= SPRITE_HALF_W * grid.half_w and grid.cell_at(x, y) == cell


# --- Bonus de dégâts (04/10) ---------------------------------------------------------------------------------------

def reference_spell(spell_id: str, slot: int, level: int, who: str | None = None) -> CombatSpell:
    """Sort de la référence Solomonk à ce niveau, rôle déduit de ses effets, avec les caractéristiques lues le 04/10
    pour `who` : Gela-[A13] (Crâ : Intelligence 86, +6) ou Brawli-[Gal] (Iop : Intelligence 211, +10 %)."""
    from bot.brain.spellbook import deduce_role
    from bot.brain.stats import CharacterStats

    stats = {"gela": CharacterStats(force=11, intelligence=86, agilite=29, dommages=6),
             "brawli": CharacterStats(force=28, intelligence=211, agilite=-16, dommages_pourcent=10)}.get(who)
    data = load_reference()["sorts"][spell_id]
    level_data = data["niveaux"][level - 1]
    entry = SpellEntry(0, None, None, True, np.zeros((46, 46, 3), np.uint8), level=level)
    return Spell(entry, data["cle"], data["nom"], level_data, deduce_role(level_data)).combat_spell(slot, stats)


def test_damage_boosts_known_from_the_reference():
    maitrise, tir = reference_spell("180", 11, 5), reference_spell("166", 13, 4)  # Maîtrise de l'Arc, Tir Puissant
    assert maitrise.role == "boost_degats" and maitrise.on_self and maitrise.spell_range == (0, 5)
    assert (maitrise.boost_flat, maitrise.boost_pct, maitrise.boost_turns) == (40, 0, 2)
    assert tir.boost_pct == 140 and tir.on_self and tir.damage_boost
    for spell_id in ("147", "149"):  # Guide de Bravoure (toute la carte), Mutilation (blesse le lanceur)
        assert reference_spell(spell_id, 1, 5).role == "autre"
    magique = reference_spell("161", 3, 6, "gela")  # 20 à 22 de base, une ligne de dégâts
    assert magique.damage_base == 21 and magique.damage_lines == 1 and round(magique.damage) == 45
    assert magique.boosted(40) == magique.damage + 40 and abs(magique.boosted(0, 140) - magique.damage - 29.4) < 1e-6


def test_cra_boosts_its_damage_first_when_an_enemy_is_in_range():
    # 04/10 (Gela-[A13], 10 PA, Maîtrise de l'Arc et Tir Puissant jamais lancés) : « si on a la portée pour taper,
    # autant booster les dégâts ». Maîtrise (+40, 2 PA) puis 2 Flèches Magiques : 2 x 85 au lieu de 2 x 45.
    magique = reference_spell("161", 3, 6, "gela")
    maitrise, tir = reference_spell("180", 11, 5), reference_spell("166", 13, 4)
    turn = plan((0, 0), 0, [(6, 2)], [magique, maitrise, tir], pa=10)
    assert [s.spell for s in turn.casts] == [maitrise, magique, magique]
    assert turn.first.reason == "bonus de dégâts (+40 par coup) avant de taper"
    assert turn.describe().startswith("Plan : Maîtrise de l'Arc sur moi, Flèche Magique sur (6, 2)")
    # Rien à portée : les bonus qui durent encore au tour suivant (Maîtrise, Tir Puissant : 2 tours), pour taper fort
    # au prochain
    # (25/10, l'utilisateur : des personnages passaient leur tour).
    assert {s.spell for s in plan((0, 0), 3, [(20, 20)], [magique, maitrise, tir], pa=10).casts} == {maitrise, tir}
    attacked = new_use(10)
    attacked.record(magique, (6, 2))  # déjà tapé ce tour : les bonus se lancent avant
    assert maitrise not in [s.spell for s in plan((0, 0), 0, [(6, 2)], [magique, maitrise], use=attacked).casts]


def test_damage_boost_lasts_its_turns():
    magique, maitrise = reference_spell("161", 3, 6, "gela"), reference_spell("180", 11, 5)
    use = new_use(10)
    use.record(maitrise, (0, 0))
    assert use.damage_bonus == (40, 0) and use.gain(magique) == 40
    use.new_turn(10)
    assert use.damage_bonus == (40, 0)  # 2 tours : celui du lancer et le suivant
    turn = plan((0, 0), 0, [(6, 2)], [magique, maitrise], pa=10, use=use)  # Maîtrise en relance, son bonus compte
    assert turn.damage == 2 * (magique.damage + 40)
    use.new_turn(10)
    assert use.damage_bonus == (0, 0)


def test_iop_keeps_three_tempests_and_adds_amplification():
    # Brawli-[Gal] (10 PA) : 3 Tempêtes de Puissance (122 chacune) valent plus que Puissance (+70 %, 5 PA) ; le dernier
    # PA prend Amplification (+7,5 par coup), lancée avant.
    tempete = reference_spell("156", 1, 5, "brawli")
    amplification, puissance = reference_spell("148", 12, 5), reference_spell("153", 9, 5)
    turn = plan((0, 0), 0, [(4, 0)], [tempete, amplification, puissance], pa=10)
    assert [s.spell for s in turn.casts] == [amplification, tempete, tempete, tempete]


def test_self_cast_once_per_turn_when_limited_per_target():
    # 11/10 : Amplification (1 lancer par cible) prévue 4 fois sur moi, le 2e lancer refusé par le jeu.
    amplification = reference_spell("148", 12, 5)
    assert amplification.on_self and amplification.per_target == 1
    turn = plan((0, 0), 0, [(1, 1)], [amplification, MAGIQUE], pa=10)
    assert [s.spell for s in turn.casts].count(amplification) == 1
    use = new_use(10)
    use.record(amplification, (0, 0))
    assert not use.available(amplification)  # déjà lancée sur moi ce tour


def test_cra_prefers_its_damage_boost_to_a_useless_range_boost():
    # 11/10 (l'utilisateur) : à portée, Maîtrise de l'Arc (+40 par coup) plutôt que Tir Éloigné.
    magique = reference_spell("161", 3, 6, "gela")
    maitrise = reference_spell("180", 11, 5)
    turn = plan((0, 0), 0, [(6, 2)], [TIR, magique, maitrise], pa=10)
    assert [s.spell for s in turn.casts] == [maitrise, magique, magique]
    without_boost = plan((0, 0), 0, [(6, 2)], [TIR, magique], pa=10)
    assert TIR not in [s.spell for s in without_boost.casts]  # 2 PA de reste : pas de bonus de portée inutile


def test_cra_explosive_arrow_aimed_where_it_hits_the_most():
    # 11/10 (l'utilisateur) : « faire le plus de dégâts, des fois il ne faudra pas lancer le sort directement sur un
    # ennemi ». Flèche Explosive (cercle de 2) : la case qui prend les 3 ennemis, quitte à viser une case vide.
    explosive = reference_spell("179", 7, 5, "gela")
    magique = reference_spell("161", 3, 6, "gela")
    assert explosive.zone == ("cercle", 2)
    enemies = [(5, 1), (7, 1), (6, 4)]
    turn = plan((0, 0), 0, enemies, [explosive, magique], pa=4)
    assert turn.first.spell is explosive and set(turn.first.hits) == set(enemies)
    # Un allié au milieu : jamais dans la zone, même pour un ennemi de plus.
    with_ally = plan((0, 0), 0, enemies, [explosive, magique], pa=4, allies=[(6, 2)]).first
    assert with_ally is None or not in_zone("cercle", 2, (0, 0), with_ally.cell, (6, 2)) or         with_ally.spell is not explosive


def test_pa_drop_read_in_the_bubble_must_agree_with_the_bar(monkeypatch):
    # 04/10 : 8 PA lus « 0 » après Tir Éloigné, le Crâ a passé son tour. Un sort plus cher que les PA lus serait grisé
    # par le jeu : s'il ne l'est pas, la lecture est fausse et les PA suivis sont gardés.
    from types import SimpleNamespace

    frame = load_frame("combat_mon_tour")
    ai = CombatAI(FakeGame(frame), CombatSettings())
    ai.spellbook = SimpleNamespace(in_slot=lambda slot: object())
    ai._spells = [MAGIQUE]
    ai._use = new_use(8)
    monkeypatch.setattr("bot.brain.combat.read_pa", lambda frame: 0)
    monkeypatch.setattr("bot.brain.combat.slot_greyed", lambda frame, slot, entry: False)
    ai._read_bar(frame)
    assert ai._use.pa == 8
    monkeypatch.setattr("bot.brain.combat.slot_greyed", lambda frame, slot, entry: True)  # la barre est d'accord
    ai._read_bar(frame)
    assert ai._use.pa == 0
    monkeypatch.setattr("bot.brain.combat.read_pa", lambda frame: 16)  # « 10 » mal lu (JPEG)
    assert ai._turn_pa(frame) == ai._max_pa


def test_spell_refused_right_after_a_cast_is_tried_again(monkeypatch):
    # 04/10 : 3e Tempête de Puissance demandée 0,4 s après le clic de la 2e (animation) : « non sélectionnable », le
    # Iop a lancé Épée Céleste à la place. Juste après un lancer, la touche est essayée une fois de plus.
    from bot.brain.combat import Cast
    from bot.perception.fighters import Fighter

    frame = load_frame("combat_mon_tour")
    game = FakeGame(frame)
    ai = CombatAI(game, CombatSettings())
    answers = [None, frame]
    monkeypatch.setattr(ai, "_select_spell", lambda before, spell: answers.pop(0))
    monkeypatch.setattr(ai, "_range_frame", lambda watch=False: None)
    for name, stub in (("_learn_range", lambda *a: None), ("_forbidden_target", lambda aim: False),
                       ("_confirm_cast", lambda *a: None), ("_read_life", lambda *a: None)):
        monkeypatch.setattr(ai, name, stub)
    me, enemy = Fighter(RED, *frame.grid.center((8, 22)), (8, 22)), Fighter("blue", *frame.grid.center((12, 18)), (12, 18))
    ai._cast_at = time.monotonic()  # un lancer vient de partir
    assert ai._cast(frame, me, enemy, [me, enemy], RED, MAGIQUE) is Cast.DONE and not answers
    answers[:] = [None]
    ai._cast_at = float("-inf")  # pas de lancer récent : refusé pour de bon
    assert ai._cast(frame, me, enemy, [me, enemy], RED, MAGIQUE) is Cast.NO_SPELL


def test_spell_refused_after_a_move_this_turn_is_tried_again(monkeypatch):
    # 19/10 : touche de Pelle Fantomatique pressée 0,5 s après le clic d'un pas, pendant la marche : « non
    # sélectionnable » avec 4 PA, la Pelle écartée du tour. Après un déplacement ce tour, la touche est retentée.
    from bot.brain.combat import Cast
    from bot.perception.fighters import Fighter

    frame = load_frame("combat_mon_tour")
    game = FakeGame(frame)
    ai = CombatAI(game, CombatSettings())
    answers = [None, frame]
    monkeypatch.setattr(ai, "_select_spell", lambda before, spell: answers.pop(0))
    monkeypatch.setattr(ai, "_range_frame", lambda watch=False: None)
    for name, stub in (("_learn_range", lambda *a: None), ("_forbidden_target", lambda aim: False),
                       ("_confirm_cast", lambda *a: None), ("_read_life", lambda *a: None)):
        monkeypatch.setattr(ai, name, stub)
    me, enemy = Fighter(RED, *frame.grid.center((8, 22)), (8, 22)), Fighter("blue", *frame.grid.center((12, 18)), (12, 18))
    ai._moved_at = time.monotonic()  # un pas vient de finir, aucun lancer récent (25/10 : plus « un pas ce tour »)
    assert ai._cast(frame, me, enemy, [me, enemy], RED, MAGIQUE) is Cast.DONE and not answers


def test_team_boost_counts_for_every_ally():
    # 25/10, l'utilisateur : Cupidité (Enutrof, +50 force et +50 intelligence à toute l'équipe, 5 tours) booste beaucoup
    # les alliés : elle passe avant les attaques quand l'équipe est là, plus qu'un bonus pour soi seul.
    pieces = CombatSpell(1, "pieces", "Lancer de Pièces", 2, (1, 10), damage=20.0)
    cupidite = CombatSpell(2, "cupidite", "Cupidité", 4, (0, 0), cooldown=6, boost_pct=50.0, boost_turns=5,
                           role="boost_degats", team_boost=True)
    allies = [(2, 2), (3, 3), (4, 4), (5, 5)]
    turn = plan((0, 0), 0, [(6, 2)], [pieces, cupidite], allies=allies, pa=6)
    assert turn.casts[0].spell is cupidite
    alone = CombatSpell(2, "cupidite", "Cupidité", 4, (0, 0), cooldown=6, boost_pct=50.0, boost_turns=5,
                        role="boost_degats")
    assert plan((0, 0), 0, [(6, 2)], [pieces, alone], pa=6).casts[0].spell is pieces  # pour moi seul : 3 Pièces valent mieux


# Tir Éloigné tel qu'en combat (niveau 4) : +PO dans un cercle de rayon 2 autour de moi, alliés compris.
TIR_ZONE = replace(TIR, boost_zone=("cercle", 2))
BEHIND_ME = {(-1, 1): "Eldieso", (-1, -1): "Fourboun", (-2, 0): "Pesopepe"}  # dans le cercle de ma case (0, 0)


def test_zone_of_a_boost_known_from_the_reference():
    # 27/10 : « Tir Eloigné : ... ; zone : cercle de rayon 2 autour de la cible (13 cases) ». La zone des dégâts
    # (`zone`) reste un point : un allié dans celle du bonus n'empêche pas de le lancer.
    tir = reference_spell("172", 6, 4)
    assert tir.role == "boost_portee" and tir.boost_zone == ("cercle", 2) and tir.zone == ("point", 0)
    assert reference_spell("172", 6, 6).boost_zone == ("cercle", 3)
    assert reference_spell("180", 11, 5).boost_zone == ("point", 0)  # Maîtrise de l'Arc : moi seul
    assert reference_spell("161", 3, 6).boost_zone == ("point", 0)  # une attaque


def test_zone_boost_cast_before_moving_when_it_reaches_more_allies():
    # 27/10, l'utilisateur : « des alliés auraient pu profiter » de Tir Éloigné. Trois alliés autour de moi, l'ennemi
    # à 14 cases : même déplacement, mêmes dégâts, mais le bonus part de ma case, avant de m'éloigner d'eux.
    enemies, allies = [(14, 0)], list(BEHIND_ME)
    before = plan_turn((0, 0), 3, enemies, allies, [TIR, MAGIQUE], new_use(6), OPEN, Weights(), names=BEHIND_ME)
    assert kinds(before) == [MOVE, CAST, CAST] and before.steps[1].spell is TIR  # sans zone connue : après le pas
    turn = plan_turn((0, 0), 3, enemies, allies, [TIR_ZONE, MAGIQUE], new_use(6), OPEN, Weights(), names=BEHIND_ME)
    assert kinds(turn) == [CAST, MOVE, CAST]
    boost, move, attack = turn.steps
    assert boost.spell is TIR_ZONE and boost.cell == (0, 0) and set(boost.allies) == set(BEHIND_ME)
    assert move.cell == before.steps[0].cell and move.pm == before.steps[0].pm  # pas un pas de plus pour eux
    assert attack.spell is MAGIQUE and turn.damage == before.damage
    assert boost.reason == ("bonus de portée en début de tour, 3 alliés dans la zone (Pesopepe, Fourboun, Eldieso), "
                            f"lancé avant d'aller en {move.cell} (de là-bas : 0)")
    assert turn.describe().startswith("Plan : Tir Éloigné sur moi (3 alliés dans la zone), aller en ")


def test_zone_boost_never_worth_a_step_by_itself():
    # Sans en faire une priorité : jamais un pas, un PA ou un point de dégâts pour mettre des alliés dans la zone.
    for enemies, allies, pm in (([(11, 1)], [(3, 1), (4, 2), (3, 3)], 3),  # alliés à 1 pas de la zone
                                ([(6, 2)], [(-2, 0), (-1, 1)], 0)):  # tout à portée : pas de bonus de portée pour eux
        alone = plan(((0, 0)), pm, enemies, [TIR, MAGIQUE], allies=allies, pa=6)
        shared = plan(((0, 0)), pm, enemies, [TIR_ZONE, MAGIQUE], allies=allies, pa=6)
        assert [(s.kind, s.cell, s.spell and s.spell.spell_id) for s in shared.steps] ==             [(s.kind, s.cell, s.spell and s.spell.spell_id) for s in alone.steps]
        assert shared.damage == alone.damage
    assert TIR not in [s.spell for s in alone.casts]


def test_zone_boost_breaks_a_tie_between_two_cells():
    # Deux cases à 1 PM, symétriques, d'où Flèche Magique (avec le bonus) touche l'ennemi à 11 cases : la première
    # venue ((1, -1)), sauf si de l'autre le cercle du bonus prend un allié.
    ally = {(2, 3): "Eldieso"}
    alone = plan_turn((0, 0), 1, [(12, 0)], list(ally), [TIR, MAGIQUE], new_use(6), OPEN, Weights(), names=ally)
    assert alone.steps[0].kind == MOVE and alone.steps[0].cell == (1, -1)
    turn = plan_turn((0, 0), 1, [(12, 0)], list(ally), [TIR_ZONE, MAGIQUE], new_use(6), OPEN, Weights(), names=ally)
    move, boost, attack = turn.steps
    assert move.cell == (1, 1) and boost.allies == ((2, 3),) and attack.spell is MAGIQUE
    assert boost.reason == "bonus de portée en début de tour, 1 allié dans la zone (Eldieso)"
    assert abs(turn.score - alone.score - Weights().boost_ally) < 1e-6  # un petit plus, rien de plus


def test_team_boost_says_nothing_of_a_zone():
    # Cupidité (toute la carte) : déjà comptée pour chaque allié (TEAM_BOOST), pas de petit plus en double.
    pieces = CombatSpell(1, "pieces", "Lancer de Pièces", 2, (1, 10), damage=20.0)
    cupidite = CombatSpell(2, "cupidite", "Cupidité", 4, (0, 0), cooldown=6, boost_pct=50.0, boost_turns=5,
                           role="boost_degats", team_boost=True, boost_zone=("cercle", 63))
    turn = plan((0, 0), 0, [(6, 2)], [pieces, cupidite], allies=[(2, 2), (3, 3), (4, 4), (5, 5)], pa=6)
    assert turn.casts[0].spell is cupidite and turn.casts[0].allies == () and "dans la zone" not in turn.describe()


def test_after_a_boost_cast_before_the_move_the_next_plan_may_move(monkeypatch):
    # combat.py : après un sort, le plan suivant part de ma case (« committed ») ; pas après un bonus lancé avant le
    # déplacement prévu (le plan suivant irait sinon taper d'ici, moins bien).
    from bot.brain.combat import Cast
    from bot.brain.engine import Step, TurnPlan

    frame, end = load_frame("combat_sadida_derriere_allies_tour1"), load_frame("combat_tour_adversaire")
    game = FakeGame(*[frame] * 30, end)
    ai = CombatAI(game, CombatSettings())
    ai._spells = [TIR_ZONE, MAGIQUE]
    boost = Step(CAST, (8, 26), TIR_ZONE, reason="bonus de portée en début de tour")
    move, attack = Step(MOVE, (9, 27), pm=1, reason="pour lancer d'ici"), Step(CAST, (13, 21), MAGIQUE, damage=13)
    plans = [TurnPlan((boost, move, attack)), TurnPlan((move, attack)), TurnPlan((attack,)), TurnPlan()]
    asked = []

    def scripted(frame, me, enemies, allies, pm, avoid=frozenset(), missed=frozenset(), can_move=True,
                 move_first=True):
        asked.append(move_first)
        return plans.pop(0) if plans else TurnPlan()

    monkeypatch.setattr(ai, "_plan", scripted)
    monkeypatch.setattr(ai, "_cast", lambda *args, **kwargs: Cast.DONE)
    monkeypatch.setattr(ai, "_walk", lambda frame, me, best, fighters, zone, label, steps=None, retry=True: steps)
    ai.play_turn(RED, is_mine=lambda f: f is not end, me_cell=(8, 26), team_cells=lambda: dict(TOUR_1))
    assert asked[:3] == [True, True, False]  # après le bonus : libre d'aller ; après le pas : on lance d'ici


DESINVOCATION = CombatSpell(7, "desinvocation", "Désinvocation", 6, (1, 6), per_target=1, damage=40, role="invocations")


def test_desinvocation_only_hits_a_summon():
    # 01/11 (l'utilisateur) : Désinvocation ne frappe que les invocations.
    monster, summon = (4, 0), (0, 4)
    assert not plan_turn((0, 0), 0, [monster], [], [DESINVOCATION], new_use(6), OPEN, Weights()).casts
    plan = plan_turn((0, 0), 0, [monster, summon], [], [DESINVOCATION], new_use(6), OPEN, Weights(),
                     summons=frozenset({summon}))
    assert [(s.spell.spell_id, s.cell) for s in plan.casts] == [("desinvocation", summon)]


def test_desinvocation_is_kept_for_when_no_other_spell_hits(monkeypatch):
    # 01/11 (l'utilisateur) : tuer l'invocateur tue ses invocations ; Désinvocation seulement si rien d'autre ne touche.
    from bot.perception.fighters import Fighter

    ai = CombatAI(FakeGame(load_frame("combat_mon_tour")), CombatSettings())
    frame = ai.game.look()
    me = Fighter(RED, *frame.grid.center((10, 20)), (10, 20))
    monster, summon = (14, 20), (10, 24)
    enemies = [Fighter("blue", *frame.grid.center(c), c) for c in (monster, summon)]
    monkeypatch.setattr(ai, "_summon_cells", lambda: frozenset({summon}))
    ai._use.new_turn(10)
    ai._spells = [MAGIQUE, DESINVOCATION]
    plan = ai._plan(frame, me, enemies, [], 0)
    assert [s.spell.spell_id for s in plan.casts] == ["fleche_magique"] * 2  # le monstre, pas l'invocation
    ai._spells = [replace(MAGIQUE, spell_range=(1, 1)), DESINVOCATION]  # plus rien d'autre ne touche
    plan = ai._plan(frame, me, enemies, [], 0)
    assert [(s.spell.spell_id, s.cell) for s in plan.casts] == [("desinvocation", summon)]
