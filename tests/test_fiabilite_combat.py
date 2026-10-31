"""Fiabilité du combat (25/10) : corrections tirées des journaux du 25/10 (153121, 155334, 171736, 181212).

1. Zone visée sur une case vide : jamais un bloc ni un trou (8 refus sur 8 dans les journaux).
2. « Carte à relief » : pas pour un sort sans ligne de vue, ni sur une carte officielle reconnue (40 % de refus après).
3. Cible montrée sans ligne de vue par le jeu : aucun sort à ligne de vue sur elle d'ici ; de 2 cases, plus du tour.
4. Fenêtre « sourde » : pas de clic de déblocage ni de clic sur la barre quand plus aucun ennemi n'est debout.
"""

import logging

import pytest

from bot.brain.arena import Arena
from bot.brain.combat import Cast, CombatAI, CombatSettings
from bot.brain.engine import CAST, Board, cast_blocker, plan_turn
from bot.brain.tactics import SIGHT_DOUBT_CELLS, CombatSpell, SpellUse, sight_refusals
from bot.perception.colors import BLUE, RED
from bot.perception.fighters import Fighter, find_fighters
from bot.perception.sight import RangeView
from conftest import load_frame
from test_brain import FakeGame, clicks, presses
from test_recovery import kinds, spell_ai, spell_game

CELLS = frozenset((i, j) for i in range(0, 29) for j in range(0, 40) if (i + j) % 2 == 0)

# Sorts du 25/10 (valeurs Solomonk au niveau lu dans les journaux).
OEIL = CombatSpell(5, "oeil_de_taupe", "Oeil de Taupe", 3, (1, 11), damage=15, zone=("cercle", 2))
RONCES = CombatSpell(4, "ronces_multiples", "Ronces Multiples", 5, (0, 5), in_line=True, range_modifiable=False,
                     per_turn=1, damage=8, zone=("cercle", 3))
MAGIQUE = CombatSpell(1, "fleche_magique", "Flèche Magique", 3, (1, 14), damage=27)
EXPIATION = CombatSpell(7, "fleche_d_expiation", "Flèche d'Expiation", 4, (8, 14), damage=33)
HARCELANTE = CombatSpell(8, "fleche_harcelante", "Flèche Harcelante", 2, (1, 11), needs_sight=False, damage=12)
ROULAGE = CombatSpell(3, "roulage_de_pelle", "Roulage de Pelle", 3, (1, 5), needs_sight=False, damage=18)


def new_use(pa=6):
    use = SpellUse()
    use.new_turn(pa)
    return use


# --- 1. Zone visée : du sol seulement ---------------------------------------------------------------------------------

def test_zone_never_aimed_at_a_block_or_a_hole():
    # 25/10 (journal 181212, Eldieso 18:47:35 et 18:47:41) : Oeil de Taupe sur les cases vides (7, 11) puis (10, 12),
    # blocs de la carte officielle 1,-31 : refusé deux fois. D'ici (10, 4), l'ennemi en (10, 14) est hors de portée de
    # sa case ; les seules cases vides à portée dont la zone le prend sont (8, 12), (10, 12) (blocs) et (12, 12) (trou).
    me, enemy = (10, 4), (10, 14)
    board = Board(CELLS, unwalkable=frozenset({(8, 12), (10, 12), (12, 12)}), obstacles=frozenset({(8, 12), (10, 12)}))
    spell = CombatSpell(5, "oeil_de_taupe", "Oeil de Taupe", 3, (1, 8), range_modifiable=False, damage=15,
                        zone=("cercle", 2))
    turn = plan_turn(me, 0, [enemy], [], [spell], new_use(), board, can_move=False)
    assert all(step.kind != CAST for step in turn.steps)
    assert cast_blocker(spell, me, (10, 12), [enemy], [], new_use(), board) == "case visée hors du sol (bloc ou trou)"
    # Même carte, ces cases redevenues du sol : la zone s'y lance (rien d'autre n'a changé).
    floor = Board(CELLS)
    turn = plan_turn(me, 0, [enemy], [], [spell], new_use(), floor, can_move=False)
    assert turn.first is not None and turn.first.kind == CAST and turn.first.cell in {(8, 12), (10, 12), (12, 12)}


def test_sadida_does_not_walk_forward_to_aim_ronces_multiples_at_a_block():
    # 25/10 (journal 171736) : Fourboun, seul en combat, « aller en (19, 19) (3 PM), Ronces Multiples sur la case
    # (13, 13) » puis « aller en (16, 20), Ronces Multiples sur la case (10, 14) » : deux blocs, deux refus, 6 PM vers
    # l'avant ; mort à 17:27. Ici, la case vide qui prend les 3 ennemis dans la zone, (13, 13), est un bloc : avant, le
    # plan y allait quand même (« aller en (18, 18), Ronces Multiples sur la case (13, 13) »).
    blocks = frozenset({(13, 13)})
    enemies = [(10, 10), (16, 10), (13, 7)]
    turn = plan_turn((19, 19), 3, enemies, [], [RONCES], new_use(6), Board(CELLS, unwalkable=blocks, obstacles=blocks))
    casts = [step for step in turn.steps if step.kind == CAST]
    assert casts and all(step.cell not in blocks for step in casts)


# --- 2. Carte à relief ------------------------------------------------------------------------------------------------

def _range_ai(monkeypatch, agreement: float) -> tuple[CombatAI, Fighter, object]:
    frame = load_frame("combat_portee_sort")
    me = Fighter(RED, *frame.grid.center((10, 20)), (10, 20))
    cells = {(10 + k, 20 + k) for k in range(1, 5)} | {(10 - k, 20 + k) for k in range(1, 5)} | \
        {(10 + k, 20 - k) for k in range(1, 5)} | {(10 - k, 20 - k) for k in range(1, 5)}
    monkeypatch.setattr("bot.brain.combat.read_range", lambda *a: RangeView(cells, set(), set(), (1, 4)))
    monkeypatch.setattr("bot.brain.combat.sight_agreement", lambda *a: agreement)
    return CombatAI(FakeGame(frame), CombatSettings()), me, frame


def test_spell_without_line_of_sight_never_makes_a_relief_map(monkeypatch, caplog):
    # 25/10 : 21 des 27 passages en « carte à relief » après la portée de Roulage de Pelle ou de Flèche Harcelante, sorts
    # sans ligne de vue (toutes leurs cases à portée en bleu foncé, même derrière un bloc) ; 41 clics refusés sur 103
    # ensuite, contre 21 sur 579 avant.
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    ai, me, frame = _range_ai(monkeypatch, 0.5)
    for spell in (ROULAGE, HARCELANTE):
        ai._learn_range(frame, me, [me], spell)
    assert ai._map_reliable and not any("relief" in m for m in caplog.messages)
    assert ai._use.measured["roulage_de_pelle"] == (1, 4)  # la portée lue sert toujours
    ai._learn_range(frame, me, [me], MAGIQUE)  # sort à ligne de vue, carte crue : comme avant
    assert not ai._map_reliable


def test_official_map_is_never_turned_into_a_relief_map(monkeypatch, caplog):
    # 25/10 : les 27 passages en « carte à relief » sur une carte officielle reconnue (1,-31, 1,-33...) ; ses cases font
    # foi, les écarts venaient de l'écran (animation de Flèche de Recul sur la portée de Flèche d'Immobilisation,
    # Eldieso 18:23:46, capture 0263 : 72 %).
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    ai, me, frame = _range_ai(monkeypatch, 0.6)
    ai.map.adopt(Arena("6959", "1,-31", frozenset({(7, 11)}), frozenset()))
    ai._learn_range(frame, me, [me], MAGIQUE)
    assert ai._map_reliable and ai._trusted_map
    assert any("carte officielle reconnue : je garde mes prévisions" in m for m in caplog.messages)


def test_official_map_recognised_after_a_relief_switch_is_trusted_again(monkeypatch):
    # La carte est partagée par l'équipe : reconnue par un autre personnage après mon passage en « carte à relief »,
    # mes distances valent de nouveau (portée vérifiée avant le clic, pas de marge de 4 cases).
    ai, me, frame = _range_ai(monkeypatch, 0.6)
    ai._learn_range(frame, me, [me], MAGIQUE)
    assert not ai._trusted_map
    ai.map.adopt(Arena("6959", "1,-31", frozenset(), frozenset()))
    assert ai._trusted_map


# --- 3. Cible montrée sans ligne de vue -------------------------------------------------------------------------------

def test_no_sight_shown_refuses_every_sight_spell_on_that_target_from_here():
    # 25/10 (journal 181212, Eldieso 18:47:27 puis 18:47:33) : Flèche d'Expiation (8 à 14) montrée sans ligne de vue sur
    # (9, 13), puis Flèche Magique (1 à 14) essayée sur lui de la même case : même réponse. 18 fois sur 62.
    spells = [MAGIQUE, EXPIATION, HARCELANTE, OEIL]
    refused = sight_refusals((9, 13), spells)
    assert refused == {("fleche_magique", (9, 13)), ("fleche_d_expiation", (9, 13)), ("oeil_de_taupe", (9, 13))}
    use = new_use(6)
    use.refused |= refused
    turn = plan_turn((5, 5), 0, [(9, 13)], [], spells, use, Board(CELLS), can_move=False)
    assert turn.first is not None and turn.first.spell is HARCELANTE  # sans ligne de vue : il reste permis


def test_target_shown_without_sight_from_two_cells_is_dropped_for_the_turn(caplog):
    # 25/10 (journal 181212, Eldieso 18:49:00 à 18:50:04) : (21, 11) montré sans ligne de vue de 3 cases, un pas de 1 PM
    # entre chaque, aucun sort parti, 17 s de tour. Vu ainsi de 2 cases : plus visé ce tour (sorts à ligne de vue).
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    ai = CombatAI(FakeGame(load_frame("combat_mon_tour")), CombatSettings())
    ai._spells = [MAGIQUE, EXPIATION, HARCELANTE]
    ai._use.new_turn(6)
    target = (21, 11)
    ai._note_no_sight((7, 5), target)
    assert target not in ai._use.blind_targets
    ai._use.refused.clear()  # un pas : les refus d'ici sont oubliés (combat._play_turn)
    ai._note_no_sight((8, 6), target)
    assert SIGHT_DOUBT_CELLS == 2 and target in ai._use.blind_targets
    assert sum("je ne la vise plus ce tour" in m for m in caplog.messages) == 1
    ai._use.refused.clear()
    turn = plan_turn((7, 7), 3, [target], [], ai._spells, ai._use, Board(CELLS))
    assert all(step.spell is HARCELANTE for step in turn.steps if step.kind == CAST)
    ai._use.new_turn(6)  # tour suivant : on réessaie
    assert target not in ai._use.blind_targets


def test_cast_reports_a_target_shown_without_sight(monkeypatch):
    # La portée affichée par le jeu montre la cible sans ligne de vue : pas de clic, et _play_turn le sait (refus de tous
    # les sorts à ligne de vue sur elle d'ici, pas seulement de ceux de portée incluse).
    shown, plain = load_frame("portee_relief_porkass"), load_frame("combat_mon_tour")
    fighters = find_fighters(shown)
    me = next(f for f in fighters if f.cell == (6, 8))
    enemy = next(f for f in fighters if f.cell == (10, 14))
    ronce = CombatSpell(3, "ronce", "Ronce", 4, (1, 8), range_modifiable=True, damage=15)
    game = FakeGame(shown, plain)
    ai = CombatAI(game, CombatSettings())
    ai._spells = [ronce]
    ai._use.new_turn(6)
    assert ai._cast(shown, me, enemy, fighters, RED, ronce) is Cast.OUT_OF_REACH
    assert ai._sight_refused and clicks(game) == [] and presses(game) == ["3", "escape"]


def test_spell_retried_only_right_after_a_move(monkeypatch):
    # 25/10 (journaux 153121 à 181212) : « pas sélectionné juste après un déplacement : nouvel essai » 18 fois, touche
    # pressée 1,7 à 11 s après l'ordre de marche, 18 « non sélectionnable » ensuite. Un pas fait plus tôt dans le tour
    # ne vaut plus un nouvel essai ; un pas qui vient de finir, oui (19/10 : touche pressée pendant la marche).
    import time

    frame = load_frame("combat_mon_tour")
    me = Fighter(RED, *frame.grid.center((8, 22)), (8, 22))
    enemy = Fighter(BLUE, *frame.grid.center((12, 18)), (12, 18))
    for moved_ago, tries in ((5.0, 1), (0.2, 2)):
        ai = CombatAI(FakeGame(frame), CombatSettings())
        ai._use.new_turn(6)
        asked = []
        monkeypatch.setattr(ai, "_select_spell", lambda before, spell: asked.append(spell) and None)
        monkeypatch.setattr(ai, "_range_frame", lambda watch=False: None)
        ai._turn_stats().moves = 1  # un déplacement ce tour
        ai._moved_at = time.monotonic() - moved_ago
        assert ai._cast(frame, me, enemy, [me, enemy], RED, MAGIQUE) is Cast.NO_SPELL
        assert len(asked) == tries


# --- 4. Pas de clic de déblocage quand le combat finit ----------------------------------------------------------------

def test_no_unlock_click_nor_bar_click_when_no_enemy_stands(monkeypatch, caplog):
    # 25/10 : 29 clics de déblocage, 25 suivis d'un clic sur la case du sort qui n'a rien sélectionné ; 12 juste avant
    # « hors combat » (Pesopepe 18:54:21 : son 2e Lancer de Pièces avait tué le dernier ennemi).
    game, my_turn, _ = spell_game(unlocks=False)
    ai = spell_ai(game)
    ai._team = RED
    monkeypatch.setattr("bot.brain.combat.find_fighters", lambda frame: [Fighter(RED, 900, 400, (10, 10))])
    with caplog.at_level(logging.INFO, logger="bot.brain.combat"):
        assert ai._select_spell(my_turn, ai.spells[0]) is None
    assert kinds(game, "click") == [] and kinds(game, "press") == ["3"]
    assert any("plus aucun ennemi debout" in m for m in caplog.messages)


def test_unlock_click_kept_while_an_enemy_stands(monkeypatch):
    # Un ennemi debout : la vérification de la fenêtre sourde reste la même (clic de déblocage, touche, case du sort).
    game, my_turn, _ = spell_game(unlocks=False)
    ai = spell_ai(game)
    ai._team = RED
    monkeypatch.setattr("bot.brain.combat.find_fighters",
                        lambda frame: [Fighter(RED, 900, 400, (10, 10)), Fighter(BLUE, 1100, 400, (14, 10))])
    assert ai._select_spell(my_turn, ai.spells[0]) is not None
    assert len(kinds(game, "click")) == 2 and kinds(game, "press") == ["3", "3"]


def test_no_spell_key_when_the_bar_shows_the_objects_tab():
    # 25/10 (journal 231114, et 19:01 journal 181212) : 2e appui sur num5 pour Flèche d'Immobilisation alors que le sort
    # précédent venait de tuer le dernier Blop ; le jeu avait remis la barre sur « Obj. » et la touche a bu la potion de
    # Brâkmar de la case 12 (Elseiso téléporté deux fois). Barre sur « Obj. » : ni touche ni clic, même en combat.
    from bot.perception.ui import OBJECTS_TAB, SPELLS_TAB, spell_bar_tab

    fight, objects = load_frame("combat_mon_tour"), load_frame("hors_combat")
    image = fight.image.copy()
    for box in (SPELLS_TAB, OBJECTS_TAB):  # libellés des onglets d'une barre sur « Obj. », collés sur le combat
        (x0, y0), (x1, y1) = fight.layout.point(box[0], box[1]), objects.layout.point(box[2], box[3])
        x0, y0, x1, y1 = (round(v) for v in (x0, y0, x1, y1))
        image[y0:y1, x0:x1] = objects.image[y0:y1, x0:x1]
    from bot.perception.frame import Frame
    tabbed = Frame.of(image)
    assert spell_bar_tab(fight) == "sorts" and spell_bar_tab(tabbed) == "objets"
    game = FakeGame(tabbed)
    ai = CombatAI(game, CombatSettings())
    immobilisation = CombatSpell(12, "fleche_immobilisation", "Flèche d'Immobilisation", 2, (1, 5), damage=4)
    assert ai._select_spell(tabbed, immobilisation) is None and presses(game) == []
    assert not ai._press_spell_key("num5", immobilisation) and presses(game) == []


@pytest.mark.parametrize("foes, tries", [(True, 2), (False, 1)])
def test_no_second_try_of_a_spell_without_any_foe_standing(monkeypatch, foes, tries):
    # 25/10 (journal 231114) : le « nouvel essai juste après un lancer » pressait la touche alors que plus aucun ennemi
    # n'était debout (combat fini, barre sur « Obj. ») : nouvel essai seulement avec un ennemi debout.
    import time

    frame = load_frame("combat_mon_tour")
    ai = CombatAI(FakeGame(frame), CombatSettings())
    tried = []
    monkeypatch.setattr(ai, "_foes_standing", lambda f: foes)
    monkeypatch.setattr(ai, "_select_spell", lambda f, spell: tried.append(spell) and None)
    monkeypatch.setattr(ai, "_range_frame", lambda **kwargs: None)
    ai._cast_at = time.monotonic()  # juste après un lancer : un nouvel essai est permis
    spell = CombatSpell(12, "fleche_immobilisation", "Flèche d'Immobilisation", 2, (1, 5), damage=4)
    me = Fighter(RED, *frame.grid.center((10, 10)), (10, 10))
    target = Fighter(BLUE, *frame.grid.center((12, 10)), (12, 10))
    ai._use.pa = 6
    assert ai._cast(frame, me, target, [me, target], RED, spell) is Cast.NO_SPELL
    assert len(tried) == tries


def test_timeline_put_back_down_when_it_hides_the_tactical_button(monkeypatch):
    # 26/10 (journal 234008) : timeline mise en haut un combat plus tôt ; les boutons du mode tactique et du mode
    # créature ne se voient plus, mode tactique perdu dans les 8 fenêtres, la moitié de l'équipe a passé ses tours.
    # La capture (00:00:56) était en fait en mode tactique, sol teinté par la nuit : reconnu depuis (test_fiabilite_nuit) ;
    # le mode tactique est donc dit inactif ici pour garder le cas « bouton caché par la timeline en haut ».
    from bot.perception.ui import TIMELINE_DOWN, timeline_at_top

    monkeypatch.setattr("bot.brain.combat.tactical_mode", lambda frame: False)
    frame = load_frame("combat_timeline_en_haut_sans_mode_tactique")
    assert timeline_at_top(frame)
    game = FakeGame(frame)
    ai = CombatAI(game, CombatSettings())
    ai._ensure_tactical(frame)
    (point,) = clicks(game)[:1]
    x, y = frame.layout.point(*TIMELINE_DOWN)
    assert abs(point[0] - x) <= 4 and abs(point[1] - y) <= 4  # ▼ de l'onglet de la timeline
