"""Choix du sort depuis ma case (moteur sans déplacement) : les priorités validées le 27/09."""

from bot.brain.engine import Board, Weights, plan_turn
from bot.brain.tactics import CombatSpell, SpellUse, aligned, reach_range

TIR = CombatSpell(6, "tir_eloigne", "Tir Éloigné", 2, (0, 0), in_line=True, range_modifiable=False, cooldown=5,
                  range_bonus=1, bonus_turns=3, role="boost_portee")
MAGIQUE = CombatSpell(3, "fleche_magique", "Flèche Magique", 4, (1, 10), per_target=2, damage=13)
RECUL = CombatSpell(1, "fleche_de_recul", "Flèche de Recul", 4, (1, 3), in_line=True, range_modifiable=False,
                    per_target=1, damage=12.5, push=3, role="repousser")
ENFLAMMEE = CombatSpell(5, "fleche_enflammee", "Flèche Enflammée", 4, (1, 4), in_line=True, per_turn=2, damage=15,
                        push=1, zone=("ligne", 4), role="degats_zone")
GLACEE = CombatSpell(4, "fleche_glacee", "Flèche Glacée", 3, (1, 5), per_target=2, damage=5, role="retrait_pa")
ALL = [TIR, MAGIQUE, RECUL, ENFLAMMEE, GLACEE]
CELLS = frozenset((i, j) for i in range(-20, 21) for j in range(-20, 21) if (i + j) % 2 == 0)


def plan(me, enemies, use, spells=ALL, walls=frozenset(), danger=3):
    """Premier sort du plan, sans PM (obstacles inconnus : pas de ligne de vue prédite) ; None : rien d'utile."""
    board = Board(CELLS, unwalkable=frozenset(walls), obstacles=None)
    turn = plan_turn(me, 0, enemies, [], spells, use, board, Weights(danger=danger))
    return turn.first


def new_use(pa=6):
    use = SpellUse()
    use.new_turn(pa)
    return use


def test_range_boost_first_when_it_brings_the_enemy_in_range():
    use = new_use()
    first = plan((0, 0), [(11, 1)], use)  # 11 cases : Flèche Magique (10) n'y arrive qu'avec Tir Éloigné (+1)
    assert first.spell is TIR and first.cell == (0, 0)
    use.record(first.spell, first.cell)
    second = plan((0, 0), [(11, 1)], use)
    assert second.spell is MAGIQUE  # 4 PA restants : 2 + 4 = 6
    use.record(second.spell, second.cell)
    assert plan((0, 0), [(11, 1)], use) is None  # plus que 0 PA


def test_no_range_boost_when_the_enemy_is_already_in_range():
    """11/10 (l'utilisateur) : « il se boost en portée alors qu'il a déjà la portée » : des PA pour rien."""
    use = new_use()
    first = plan((0, 0), [(8, 0)], use)
    assert first.spell is MAGIQUE


def test_no_range_boost_when_the_planned_attack_would_no_longer_fit():
    use = new_use(5)  # un monstre m'a retiré 1 PA
    assert plan((0, 0), [(8, 0)], use).spell is MAGIQUE  # 5 - 2 = 3 PA : plus de Flèche Magique après le bonus


def test_no_range_boost_while_enemies_are_out_of_reach_even_with_it():
    use = new_use()
    assert plan((0, 0), [(14, 0)], use) is None  # 14 cases : bonus gardé pour quand il servira


def test_greyed_spells_are_skipped():
    use = new_use()
    use.greyed = {"tir_eloigne", "fleche_magique"}  # grisés dans la barre
    assert plan((0, 0), [(4, 0)], use).spell is GLACEE


def test_range_boost_waits_for_its_cooldown():
    use = new_use()
    use.record(TIR, (0, 0))
    use.new_turn(6)
    assert plan((0, 0), [(8, 0)], use).spell is MAGIQUE  # relance tous les 5 tours


def test_range_boost_extends_modifiable_ranges():
    use = new_use()
    use.record(TIR, (0, 0))
    assert plan((0, 0), [(11, 1)], use).spell is MAGIQUE  # 11 cases : portée 10 + 1
    assert plan((0, 0), [(12, 0)], use) is None


def test_danger_pushes_the_closest_enemy_away():
    use = new_use(4)
    chosen = plan((0, 0), [(2, 2), (7, 1)], use)
    assert chosen.spell is RECUL and chosen.cell == (2, 2) and "danger" in chosen.reason


def test_no_push_when_the_close_enemy_is_not_aligned():
    use = new_use(4)
    assert plan((0, 0), [(3, 1)], use).spell is MAGIQUE  # Recul et Enflammée se lancent en ligne


def test_most_damage_when_no_danger():
    use = new_use(4)
    assert plan((0, 0), [(4, 4)], use).spell is ENFLAMMEE  # alignée à 4 cases : 15 > 13
    # À 5 cases, hors de portée de l'Enflammée (1 à 4)... mais sa zone en ligne part de la case visée : sur la case
    # vide à 4 cases, la ligne prend l'ennemi 1 case plus loin, à 90 % (zone dégressive) : 13,5 > 13.
    sniped = plan((0, 0), [(5, 5)], use)
    assert sniped.spell is ENFLAMMEE and sniped.cell == (4, 4) and sniped.hits == ((5, 5),)
    # À 6 cases, 2 cases derrière la case visée : 80 %, 12 < 13 : Flèche Magique.
    assert plan((0, 0), [(6, 6)], use).spell is MAGIQUE
    assert plan((0, 0), [(9, 9)], use).spell is MAGIQUE  # hors de portée de l'Enflammée, zone comprise


def test_zone_hits_aligned_enemies():
    use = new_use(4)
    chosen = plan((0, 0), [(2, 2), (3, 3)], use, danger=0)
    # Zone dégressive : 15 sur la case visée, 13,5 sur l'ennemi 1 case derrière.
    assert chosen.spell is ENFLAMMEE and chosen.value == 28.5 and "2 ennemis" in chosen.reason


def test_push_against_a_wall_deals_more_damage():
    use = new_use(4)
    free = plan((0, 0), [(2, 2)], use, spells=[MAGIQUE, RECUL], danger=0)
    blocked = plan((0, 0), [(2, 2)], use, spells=[MAGIQUE, RECUL], walls={(3, 3)}, danger=0)
    assert free.spell is MAGIQUE and blocked.spell is RECUL


def test_casts_per_target_are_respected():
    use = new_use(12)
    use.record(TIR, (0, 0))
    use.record(MAGIQUE, (8, 0))
    use.record(MAGIQUE, (8, 0))
    assert plan((0, 0), [(8, 0)], use) is None  # 2 lancers par cible atteints


def test_refused_target_is_not_retried():
    use = new_use(4)
    use.refused.add(("fleche_magique", (8, 0)))
    assert plan((0, 0), [(8, 0)], use, spells=[MAGIQUE]) is None


def test_three_pa_left_goes_to_the_cheaper_spell():
    use = new_use(3)
    assert plan((0, 0), [(4, 0)], use).spell is GLACEE


def test_aligned_and_reach_range():
    assert aligned((0, 0), (3, -3)) and aligned((0, 0), (-2, 2)) and not aligned((0, 0), (2, 0))
    assert reach_range(ALL, new_use()) == (1, 10)
