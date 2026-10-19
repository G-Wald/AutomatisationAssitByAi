"""Logique de décision, avec un faux jeu qui rejoue des captures et note les actions."""

import logging
import time
from contextlib import contextmanager
from dataclasses import replace

import cv2
import numpy as np

import bot.brain.combat as combat_module
from bot.brain.combat import (
    Cast,
    CombatAI,
    CombatSettings,
    choose_placement,
)
from bot.brain.engine import CAST, POSITION, reachable_cells
from bot.brain.engage import HOVER_WAIT, Z_LINGER, Engager, EngageSettings, hover_points
from bot.brain.loop import Bot
from bot.brain.popups import PopupCloser
from bot.brain.rest import Rester, RestSettings
from bot.brain.game import Game, _hover_disturbs
from bot.brain.spellbook import SpellBook, choose_level, guess_class, load_reference
from bot.config import load_settings
from bot.perception.colors import BLUE, RED, UI_BEIGE
from bot.perception.fighters import Fighter, find_fighters
from bot.perception.frame import Frame
from bot.perception.groups import find_group_tooltips
from bot.perception.placement import Placement, read_placement
from bot.perception.spellbook import SpellEntry, read_spell_window
from bot.perception.grid import distance
from bot.brain.tactics import CombatSpell
from bot.perception.sight import Terrain, locate_caster, range_center
from bot.perception.toolbar import find_creature_button, find_tactical_button
from bot.perception.popups import load_popup_specs
from bot.perception.ui import PA_BUBBLE, default_slot_key, pa_changed, read_pa, region_changed, spell_slot_point
from bot.state.scene import Phase, read_scene
from conftest import FIXTURES, load_frame, whole_list, without_flash_menu


class FakeRecorder:
    def keeps(self, lossless=False):
        return True

    def save(self, image, label, lossless=False, quality=80):
        return f"<{label}>"


class FakeControl:
    """StopControl du faux jeu : les pauses entre deux captures de wait_until avancent son horloge simulée."""

    def __init__(self, game: "FakeGame") -> None:
        self.game = game

    def sleep(self, seconds):
        self.game.polled.append(seconds)
        self.game.clock += seconds


class FakeGame:
    """Rejoue les captures dans l'ordre (la dernière se répète) ; aucune attente réelle, clics sans hasard.

    Horloge simulée `clock`, avancée par les attentes seulement : `slept`, attentes fixes du bot (game.sleep) ;
    `polled`, pauses entre deux captures quand il guette l'écran (wait_until, celui du vrai jeu)."""

    wait_until = Game.wait_until

    def __init__(self, *frames: Frame) -> None:
        self.frames = list(frames)
        self.actions: list[tuple] = []
        self.recorder = FakeRecorder()
        self.cursor = None
        self.parks: list = []  # valeur de `park` à chaque capture
        self.clock = 0.0
        self.slept: list[float] = []
        self.polled: list[float] = []
        self.control = FakeControl(self)

    def look(self, park=True):
        self.parks.append(park)
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.clock += seconds

    def now(self):
        return self.clock

    def move(self, point, spread=(0, 0)):
        self.cursor = (round(point[0]), round(point[1]))
        self.actions.append(("move", self.cursor))
        return self.cursor

    def click(self, point=None, spread=(0, 0), button="left"):
        if point is not None:
            self.move(point, spread)
        self.actions.append(("click" if button == "left" else f"click_{button}", self.cursor))

    def press(self, key):
        self.actions.append(("press", key))

    def wheel(self, point, notches, spread=(0, 0)):
        self.move(point, spread)
        self.actions.append(("wheel", notches))
        return True

    @contextmanager
    def holding(self, key):
        self.actions.append(("hold", key))
        yield


def clicks(game):
    return [a[1] for a in game.actions if a[0] == "click"]


def presses(game):
    return [a[1] for a in game.actions if a[0] == "press"]


def test_refused_spell_ends_turn_without_clicking_its_slot(monkeypatch):
    monkeypatch.setattr("bot.brain.combat.read_terrain", lambda frame, fighters: Terrain(set(), set(), set()))  # rien entre nous
    my_turn = load_frame("combat_mon_tour")
    enemy_turn = load_frame("combat_tour_adversaire")
    # Ennemi supposé à portée ; le raccourci ne fait pas apparaître la portée (le jeu refuse le sort)
    # -> fin de tour, puis la main passe.
    game = FakeGame(*[my_turn] * 8, enemy_turn)
    CombatAI(game, CombatSettings(spell_range=(1, 20))).play_turn(RED)
    assert presses(game) == ["3"]
    assert clicks(game) == [read_scene(my_turn).end_turn_button]  # jamais de clic sur la case du sort


def spent_pa(frame: Frame) -> Frame:
    """Même capture, bulle des PA changée (le sort est parti)."""
    image = frame.image.copy()
    x0, y0, x1, y1 = frame.layout.box(*PA_BUBBLE)
    image[y0 + 8 : y1 - 8, x0 + 8 : x1 - 8] = 255
    return Frame.of(image)


def me_and_enemy(frame: Frame) -> tuple[Fighter, Fighter]:
    fighters = find_fighters(frame)
    return next(f for f in fighters if f.team == RED), next(f for f in fighters if f.team == BLUE)


def enemy_in_range(frame: Frame) -> Fighter:
    """Ennemi fictif à 4 cases de moi (portée 1 à 7 sur combat_portee_sort), loin du curseur interdit."""
    x, y = frame.grid.center((16, 22))
    return Fighter(BLUE, x, y, (16, 22))


def with_forbidden_cursor_on(frame: Frame, target: Fighter) -> Frame:
    """Colle le curseur « interdit » de la capture sur la cible."""
    image = frame.image.copy()
    icon = frame.image[532:576, 1603:1647].copy()
    x, y = round(target.x), round(target.y)
    image[y - 22 : y + 22, x - 22 : x + 22] = icon
    return Frame.of(image)


def test_spell_shortcut_used_and_cast_confirmed():
    frame = load_frame("combat_portee_sort")  # portée affichée
    me, _ = me_and_enemy(frame)
    target = enemy_in_range(frame)
    game = FakeGame(frame, frame, spent_pa(frame))
    ai = CombatAI(game, CombatSettings())
    assert ai._cast(frame, me, target, [me, target], RED) is Cast.DONE
    assert presses(game) == ["3"]
    assert clicks(game) == [(round(target.x), round(target.y))]  # pas de clic sur la case du sort
    assert ai._use.measured["sort_configure"] == (1, 7)  # mesurée sur les cases bleues


def test_click_that_did_not_go_through_is_retried():
    frame = load_frame("combat_portee_sort")
    me, _ = me_and_enemy(frame)
    target = enemy_in_range(frame)
    # 1er clic sans effet : PA inchangés pendant toute la surveillance (3 captures), sort toujours sélectionné.
    # 2e clic confirmé.
    settings = CombatSettings(cast_confirm=0.45)  # 4 coups d'œil (toutes les 0,15 s)
    game = FakeGame(frame, frame, frame, frame, frame, frame, spent_pa(frame))
    assert CombatAI(game, settings)._cast(frame, me, target, [me, target], RED) is Cast.DONE
    assert clicks(game) == [(round(target.x), round(target.y))] * 2


def with_pa_bubble(frame: Frame, pa: int) -> Frame:
    """Même capture, bulle des PA de Brawli-[Gal] du 04/10 montrant `pa` (9, 6 ou 3), collée à sa place."""
    image = frame.image.copy()
    bubble = cv2.imread(str(FIXTURES / f"bulle_pa_{pa}.png"))
    x0, y0, _, _ = frame.layout.box(*PA_BUBBLE)
    image[y0 - 8 : y0 - 8 + bubble.shape[0], x0 - 8 : x0 - 8 + bubble.shape[1]] = bubble
    return Frame.of(image)


def test_pa_spent_seen_on_the_digits(monkeypatch):
    # 04/10 : la bulle entière ressemblait encore à 0,95 de 9 à 6 PA (le chiffre n'en est qu'une petite part) : Tempêtes
    # de Puissance crues pas parties et relancées. Le nombre lu, ou la forme des chiffres, tranche.
    frame = load_frame("combat_mon_tour")
    nine, six, three = (with_pa_bubble(frame, pa) for pa in (9, 6, 3))
    assert [read_pa(f) for f in (nine, six, three)] == [9, 6, 3]
    assert not region_changed(nine, six, PA_BUBBLE)  # l'ancienne comparaison ne voyait rien
    assert pa_changed(nine, six) and pa_changed(six, three) and pa_changed(nine, nine) is False
    monkeypatch.setattr("bot.perception.ui.read_pa", lambda frame: None)  # chiffres illisibles : leur forme suffit
    assert pa_changed(nine, six) and pa_changed(six, three) and pa_changed(six, six) is False
    assert pa_changed(frame, spent_pa(frame)) is None  # bulle sans chiffres : on ne sait pas


def test_no_second_click_on_the_cell_of_an_enemy_that_died(monkeypatch):
    # 04/10 : PA inchangés aux yeux du bot, mais l'ennemi n'est plus sur sa case : le sort est parti et l'a tué. Un
    # nouvel essai frapperait une case vide.
    frame = load_frame("combat_portee_sort")
    me, _ = me_and_enemy(frame)
    target = enemy_in_range(frame)
    monkeypatch.setattr("bot.brain.combat.find_fighters", lambda f: [me, target] if f is frame else [me])
    after = Frame.of(frame.image.copy())  # PA inchangés, cible partie
    game = FakeGame(frame, after)
    assert CombatAI(game, CombatSettings(cast_confirm=0.15))._cast(frame, me, target, [me, target], RED) is Cast.DONE
    assert clicks(game) == [(round(target.x), round(target.y))]


def test_background_cast_without_effect_is_a_refusal_not_three_tries(monkeypatch):
    # 11/10 (arrière-plan, Jesuisleking : 33 s sur 2 tours) : pas de curseur « interdit » à voir ; PA inchangés après le
    # clic = refusé d'ici. Un seul clic, pas de mise à l'écart du sort pour la session.
    frame = load_frame("combat_portee_sort")
    me, _ = me_and_enemy(frame)
    target = enemy_in_range(frame)
    monkeypatch.setattr("bot.brain.combat.find_fighters", lambda f: [me, target])
    unselected = load_frame("combat_mon_tour")  # sans portée affichée

    class Refusing(FakeGame):
        def look(self, park=True):  # PA inchangés, sort toujours sélectionné jusqu'à Échap
            return unselected if ("press", "escape") in self.actions else frame

    game = Refusing(frame)
    game.background = True
    ai = CombatAI(game, CombatSettings(cast_confirm=0.15))
    assert ai._cast(frame, me, target, [me, target], RED) is Cast.OUT_OF_REACH
    assert len(clicks(game)) == 1 and not ai._banned


def test_spell_confirmed_only_once_the_animation_is_over():
    frame = load_frame("combat_portee_sort")
    me, _ = me_and_enemy(frame)
    target = enemy_in_range(frame)
    # Les PA ne baissent qu'à la fin de l'animation : 2 captures inchangées, puis PA dépensés -> un seul clic.
    game = FakeGame(frame, frame, frame, frame, spent_pa(frame))
    assert CombatAI(game, CombatSettings())._cast(frame, me, target, [me, target], RED) is Cast.DONE
    assert clicks(game) == [(round(target.x), round(target.y))]


def test_no_move_when_the_pm_bubble_shows_zero():
    frame = load_frame("combat_portee_sort")  # bulle des PM : 0 ; ennemi à 8 cases, portée 1 à 7
    me, target = me_and_enemy(frame)
    game = FakeGame(frame)
    ai = CombatAI(game, CombatSettings())
    pm = ai._pm_left(frame, 3)
    assert pm == 0 and ai._plan(frame, me, [target], [], pm).steps == ()  # ni approche, ni placement
    assert game.actions == []  # ni clic, ni survol de mon personnage


def test_far_enemy_approached_with_all_pm_without_hovering_me():
    # Ennemi à 17 cases, portée 1 à 7 : aucun sort ne touche ce tour. Un seul style (03/10) : placement avec tous ses PM
    # vers une case d'où taper, dès le premier tour (avant : style agressif, ou prudent après 3 tours sans toucher).
    frame = load_frame("combat_mon_tour")  # 3 PM
    game = FakeGame(*[frame] * 14, load_frame("combat_tour_adversaire"))
    CombatAI(game, CombatSettings()).play_turn(RED)
    assert "3" not in presses(game)  # déplacement sans sélectionner le sort
    destination, again, *_ = clicks(game)  # capture figée : « pas bougé », recliqué une fois (03/10)
    me, _ = me_and_enemy(frame)
    assert all(a[1] != (round(me.x), round(me.y - 30)) for a in game.actions if a[0] == "move")  # pas de survol
    assert distance(frame.grid.cell_at(*destination), me.cell) == 3
    assert frame.grid.cell_at(*again) == frame.grid.cell_at(*destination)  # la même case


def test_reachable_cells_go_around_obstacles():
    me = (0, 0)
    zone = reachable_cells(me, 2, {(i, j) for i in range(-5, 6) for j in range(-5, 6) if (i + j) % 2 == 0},
                           blocked={(1, 1), (1, -1)})
    assert (2, 0) not in zone  # derrière le mur : à 2 pas seulement en passant à travers
    assert zone == {(-1, 1), (-1, -1), (0, 2), (-2, 2), (-2, 0), (0, -2), (-2, -2)}


def test_spell_is_never_selected_by_clicking_its_slot():
    no_range = load_frame("combat_mon_tour")
    ai = CombatAI(FakeGame(no_range), CombatSettings())
    assert ai._select_spell(no_range, ai.spells[0]) is None  # la touche ne sélectionne rien : sort refusé
    assert presses(ai.game) == ["3"] and clicks(ai.game) == []


def book_of_the_28_09() -> SpellBook:
    """Sorts lus dans la fenêtre du 28/09 (6 sorts appris) ; la barre est reliée au premier tour."""
    window = with_spell_window("hors_combat", "fenetre_sorts_28_09")
    book = SpellBook(FakeGame(window))
    book.learn(read_spell_window(window))
    return book


def fighters_of_the_28_09(frame: Frame) -> tuple[Fighter, Fighter]:
    """Moi en (22, 22), l'ennemi en (15, 15) : à 7 cases (positions du journal ; les captures « tour » sont annotées)."""
    return (Fighter(RED, *frame.grid.center((22, 22)), (22, 22)), Fighter(BLUE, *frame.grid.center((15, 15)), (15, 15)))


def test_turn_starts_with_the_pa_shown_in_the_bubble():
    frame = load_frame("combat_5pa")  # un monstre m'a retiré 1 PA : 5 au lieu de 6
    ai = CombatAI(FakeGame(frame), CombatSettings(), book_of_the_28_09())
    ai._use_spellbook(frame)
    ai._use.new_turn(ai._turn_pa(frame))
    ai._read_bar(frame)
    assert ai._use.pa == 5 and ai._use.greyed == set()
    me, enemy = fighters_of_the_28_09(frame)
    plan = ai._plan(frame, me, [enemy], [], pm=3)
    # Tir Éloigné (2 PA) ne laisserait que 3 PA : plus de sort à 4 PA ensuite, donc pas de bonus. Le sort à 4 PA :
    # Flèche Magique (13) d'ici : ni Flèche Enflammée sur la case vide à 4 cases (l'ennemi 3 cases derrière, à 70 % :
    # 10,5), ni 3 PM pour s'aligner sur lui et le toucher en plein (15 - 3 PM = 12 : déplacement qui ne vaut pas).
    assert [(s.spell.spell_id, s.cell, s.hits) for s in plan.steps] == [("fleche_magique", (15, 15), ((15, 15),))]


def test_my_turn_coming_back_right_after_its_end_is_the_same_turn():
    # 11/10 : Gela, F1 ignoré pendant l'animation de son dernier sort ; « mon tour » 2,2 s après, 4 PA au lieu de 10 :
    # la suite du même tour, ses lancers gardés (pas une Maîtrise de l'Arc de plus).
    from bot.brain.combat import SAME_TURN_WINDOW

    frame = load_frame("combat_5pa")  # 5 PA au lieu de 6
    ai = CombatAI(FakeGame(frame), CombatSettings(), book_of_the_28_09())
    ai._use.new_turn(6)
    ai._use.casts_this_turn["fleche_magique"] = 1
    turn = ai._use.turn
    ai._turn_ended_at = time.monotonic()
    ai.play_turn(RED, is_mine=lambda f: False)
    assert ai._use.turn == turn and ai._use.casts_this_turn["fleche_magique"] == 1 and ai._use.pa == 5
    ai._turn_ended_at = time.monotonic() - SAME_TURN_WINDOW  # bien plus tard : un vrai nouveau tour
    ai.play_turn(RED, is_mine=lambda f: False)
    assert ai._use.turn == turn + 1 and not ai._use.casts_this_turn


def test_turn_cut_by_the_end_of_the_fight_is_in_the_report():
    # 11/10 : « 0 tour(s) ; 3 sort(s) » au bilan quand le combat finit pendant le tour.
    ai = CombatAI(FakeGame(load_frame("combat_5pa")), CombatSettings(), book_of_the_28_09())
    ai._turn_stats().casts += 1  # un sort lancé, puis le combat finit (pas de F1)
    ai.play_turn(RED, is_mine=lambda f: False)
    assert ai.fight_report()["tours"] == 1 and ai._stats is None


def test_pa_left_and_greyed_spells_read_before_choosing():
    ai = CombatAI(FakeGame(load_frame("combat_5pa")), CombatSettings(), book_of_the_28_09())
    ai._use_spellbook(load_frame("combat_5pa"))  # premier tour : sorts reliés à la barre
    frame = load_frame("combat_3pa_sorts_grises")  # 3 PA après Tir Éloigné : seule Flèche Glacée est en couleur
    ai._use.new_turn(6)  # PA supposés : la bulle corrige
    ai._read_bar(frame)
    assert ai._use.pa == 3
    assert ai._use.greyed == {"fleche_de_recul", "fleche_empoisonnee", "fleche_magique", "fleche_enflammee", "tir_eloigne"}
    me, _ = fighters_of_the_28_09(frame)
    enemy = Fighter(BLUE, *frame.grid.center((18, 18)), (18, 18))  # à 4 cases : à portée de Flèche Glacée (1 à 5)
    assert ai._plan(frame, me, [enemy], [], pm=3).first.spell.spell_id == "fleche_glacee"


def test_enemies_with_touching_rings_are_attacked_not_skipped():
    frame = load_frame("combat_cercles_colles")  # 01/10 : tour passé, « je ne vois pas d'ennemi »
    game = FakeGame(*[frame] * 30, load_frame("combat_tour_adversaire"))
    CombatAI(game, CombatSettings()).play_turn(RED)
    assert presses(game)[0] == "3"  # Flèche Magique sur le Bouftou en (19, 17), à 5 cases (avant : F1 aussitôt)


def plan_on(name: str, pm: int):
    """Plan du premier coup d'œil d'un tour, sur une capture, avec les sorts du 28/09."""
    frame = load_frame(name)
    ai = CombatAI(FakeGame(frame), CombatSettings(), book_of_the_28_09())
    ai._use_spellbook(frame)
    ai._use.new_turn(ai._turn_pa(frame))
    fighters = find_fighters(frame)
    ai._learn_blocks(frame, fighters)
    ai._read_bar(frame)
    me, *enemies = sorted(fighters, key=lambda f: f.team != RED)
    return ai._plan(frame, me, enemies, [], pm)


def test_out_of_contact_to_line_up_both_bouftous():
    # 01/10 : Bouftou au contact en (15, 19), l'autre en (16, 20) ; 5 PA, 3 PM.
    turn = plan_on("combat_cercles_colles_contact", pm=3)
    move, cast = turn.steps
    assert move.cell == (13, 17) and move.pm == 3  # hors contact, les deux Bouftous alignés devant moi
    assert cast.spell.spell_id == "fleche_enflammee" and set(cast.hits) == {(15, 19), (16, 20)}
    # Sans PM (taclé, par exemple) : Flèche de Recul sur celui du contact (danger écarté, plus de contact).
    (push,) = plan_on("combat_cercles_colles_contact", pm=0).steps
    assert push.kind == CAST and push.spell.spell_id == "fleche_de_recul" and push.cell == (15, 19)


def test_team_hook_says_whose_turn_it_is_and_who_i_am():
    frame = load_frame("combat_cercles_colles")  # timeline : au tour des rouges ; deux bleus collés
    game = FakeGame(frame)
    CombatAI(game, CombatSettings()).play_turn(BLUE)
    assert game.actions == []  # sans la fonction du mode équipe : pas mon tour, d'après la timeline
    end = load_frame("combat_tour_adversaire")
    game = FakeGame(*[frame] * 12, end)
    ai = CombatAI(game, CombatSettings(cast_confirm=0))
    plans = []
    planner = ai._plan
    ai._plan = lambda *args, **kwargs: plans.append(planner(*args, **kwargs)) or plans[-1]
    # Mode équipe : je suis le bleu en (19, 17), le bleu en (20, 18) est un allié, le rouge l'ennemi.
    ai.play_turn(BLUE, is_mine=lambda f: f is not end, me_cell=(19, 17))
    assert presses(game)[0] == "3" and plans[0].first.cell == (14, 20)  # le rouge, jamais l'allié
    # Capture figée : le sort ne part pas. Reste du tour (03/10, jeu d'équipe) : ne plus rester collé à l'allié.
    (step,) = plans[1].steps
    assert step.kind == POSITION and distance(step.cell, (20, 18)) > 1
    assert "je ne reste pas collé à l'allié en (20, 18)" in step.reason
    assert [frame.grid.cell_at(*c) for c in clicks(game)] == [step.cell]
    assert game.frames == [end]  # la main passe quand la fonction dit « plus mon tour »


def test_my_own_team_mate_is_not_me_when_told_where_i_am():
    frame = load_frame("combat_cercles_colles")
    ai = CombatAI(FakeGame(frame), CombatSettings())
    ai._me_cell = (20, 18)
    ai._me_sure = True  # case sûre (play_turn(me_cell=...))
    me, allies, enemies = ai._sides(frame, find_fighters(frame), BLUE)
    assert me.cell == (20, 18) and [a.cell for a in allies] == [(19, 17)] and [e.cell for e in enemies] == [(14, 20)]


def test_range_boost_cast_on_myself_with_a_right_click():
    frame = load_frame("combat_mon_tour")
    me, _ = me_and_enemy(frame)
    boost = CombatSpell(6, "tir_eloigne", "Tir Éloigné", 2, (0, 0), range_bonus=1, bonus_turns=3, role="boost_portee")
    game = FakeGame(frame, spent_pa(frame))
    assert CombatAI(game, CombatSettings())._cast(frame, me, me, [me], RED, boost) is Cast.DONE
    assert [a for a in game.actions if a[0].startswith("click")] == [("click_right", spell_slot_point(frame.layout, 6))]
    assert presses(game) == []  # ni raccourci, ni clic sur mon personnage


def test_range_boost_falls_back_on_its_key_and_a_click_on_me_and_keeps_it():
    frame = load_frame("combat_mon_tour")
    me, _ = me_and_enemy(frame)
    boost = CombatSpell(6, "tir_eloigne", "Tir Éloigné", 2, (0, 0), range_bonus=1, bonus_turns=3, role="boost_portee")
    # Clic droit ignoré (01/10) : rien ne change ; le raccourci puis le clic sur moi le lancent.
    game = FakeGame(frame, frame, spent_pa(frame))
    ai = CombatAI(game, CombatSettings(cast_confirm=0))
    assert ai._cast(frame, me, me, [me], RED, boost) is Cast.DONE
    assert [a[0] for a in game.actions if a[0].startswith("click")] == ["click_right", "click"]
    assert presses(game) == ["6"] and clicks(game) == [(round(me.x), round(me.y))]
    # Combat suivant : directement par le raccourci.
    game.actions.clear()
    game.frames = [spent_pa(frame)]
    ai.new_combat()
    assert ai._cast(frame, me, me, [me], RED, boost) is Cast.DONE
    assert presses(game) == ["6"] and [a[0] for a in game.actions if a[0].startswith("click")] == ["click"]


def test_forbidden_target_cancels_the_spell():
    frame = load_frame("combat_portee_sort")
    me, _ = me_and_enemy(frame)
    target = enemy_in_range(frame)  # à portée, mais le jeu affiche « interdit » (pas de ligne de vue)
    # Après Échap, la portée a disparu.
    game = FakeGame(frame, with_forbidden_cursor_on(frame, target), load_frame("combat_mon_tour"))
    assert CombatAI(game, CombatSettings())._cast(frame, me, target, [me, target], RED) is Cast.OUT_OF_REACH
    assert presses(game) == ["3", "escape"]
    assert clicks(game) == []


def test_spell_still_selected_after_cancel_stops_actions():
    frame = load_frame("combat_portee_sort")
    me, _ = me_and_enemy(frame)
    target = enemy_in_range(frame)
    game = FakeGame(frame, with_forbidden_cursor_on(frame, target), frame)  # la portée reste affichée malgré Échap
    assert CombatAI(game, CombatSettings())._cast(frame, me, target, [me, target], RED) is Cast.STUCK


def test_target_beyond_measured_range_skips_the_hover():
    frame = load_frame("combat_portee_sort")  # portée 1 à 7, ennemi à 8 cases
    me, target = me_and_enemy(frame)
    game = FakeGame(frame, load_frame("combat_mon_tour"))  # après Échap, la portée a disparu
    assert CombatAI(game, CombatSettings())._cast(frame, me, target, find_fighters(frame), RED) is Cast.OUT_OF_REACH
    assert [a for a in game.actions if a[0] == "move"] == []  # pas de survol inutile de l'ennemi
    assert presses(game) == ["3", "escape"]


def test_tactical_mode_switched_on_at_placement():
    normal = Frame.of(cv2.imread(str(FIXTURES / "placement_mode_normal.png")))
    game = FakeGame(normal, load_frame("preparation"))
    bot = Bot(game, load_settings())
    bot._phase_changed(Phase.OUT_OF_COMBAT, Phase.PLACEMENT)
    bot._phase_since -= 10
    bot._step(normal, read_scene(normal))
    bot._step(normal, read_scene(normal))
    tactical_click, _placement_click, ready_click = clicks(game)
    assert abs(tactical_click[0] - 1746) <= 3 and abs(tactical_click[1] - 687) <= 3
    assert ready_click == read_scene(normal).ready_button


def test_short_wait_after_a_fight_longer_after_ready():
    bot = Bot(FakeGame(load_frame("hors_combat")), load_settings())
    bot._phase_changed(Phase.COMBAT, Phase.OUT_OF_COMBAT)
    assert bot._settle == 2  # fin de combat : 2 s suffisent
    assert 1.5 <= bot._next_engage_at - time.monotonic() <= 2.5  # pause avant le groupe suivant (config)
    bot._phase_changed(Phase.PLACEMENT, Phase.OUT_OF_COMBAT)
    assert bot._settle == 5  # après Prêt, l'écran ressemble un moment à du hors combat


def test_old_combat_styles_are_ignored(caplog):
    # 03/10 : un seul style, polyvalent. Un vieux config/settings.yaml (style, approach_below, flee) ne plante pas :
    # avertissement, une fois par lancement.
    combat_module._warned_obsolete.clear()
    caplog.set_level(logging.WARNING, logger="bot.brain.combat")
    old = {"combat": {"style": "prudent", "approach_below": 10, "flee": True, "contact_penalty": 12}}
    assert CombatSettings.from_settings(old).weights.contact == 12
    CombatSettings.from_settings(old)
    warnings = [r.getMessage() for r in caplog.records]
    assert len(warnings) == 3 and all("ignoré : un seul style de combat, polyvalent" in w for w in warnings)
    assert not {"style", "approach_below", "flee"} & set(load_settings()["combat"])  # livré sans


def test_range_boost_counts_from_the_turn_it_is_cast():
    frame = load_frame("combat_portee_sort")
    me, _ = me_and_enemy(frame)
    x, y = frame.grid.center((me.cell[0] - 8, me.cell[1]))  # à 8 cases : hors de la portée 1 à 7 sans bonus
    target = Fighter(BLUE, x, y, (me.cell[0] - 8, me.cell[1]))
    ai = CombatAI(FakeGame(frame, frame, spent_pa(frame)), CombatSettings())
    ai._use.record(CombatSpell(6, "tir_eloigne", "Tir Éloigné", 0, (0, 0), range_bonus=1, bonus_turns=3), me.cell)
    ai._use.measured.clear()
    ai._learn_range = lambda *args: None  # portée affichée ignorée : seule compte celle calculée
    assert ai._cast(frame, me, target, [me, target], RED) is Cast.DONE  # 7 + 1 : à portée, pas d'annulation
    assert "escape" not in presses(ai.game)


def test_placement_choice_on_real_screen():
    frame = load_frame("preparation")
    placement = read_placement(frame)
    (me,) = placement.occupied[RED]
    enemies = placement.occupied[BLUE]
    free = placement.free[RED]
    assert choose_placement(me, free, enemies, (1, 7)) == (13, 13)  # la seule à portée
    # Portée 9 : plusieurs cases à portée, on prend la plus loin du monstre.
    best = choose_placement(me, free, enemies, (1, 9))
    assert min(distance(best, e) for e in enemies) == 9


def test_no_placement_move_when_already_best():
    assert choose_placement((0, 0), {(1, 1)}, {(5, 0)}, (1, 7)) is None  # déjà à portée et plus loin


def test_no_warning_when_combat_ends_on_my_last_spell():
    game = FakeGame(load_frame("hors_combat"))
    CombatAI(game, CombatSettings())._end_turn(RED)
    assert game.actions == []


def test_default_slot_keys():
    assert [default_slot_key(s) for s in (0, 1, 3, 7, 8, 14)] == ["", "1", "3", "7", "ctrl+1", "ctrl+7"]


def test_placement_then_ready_once():
    frame = load_frame("preparation")
    game = FakeGame(frame)
    bot = Bot(game, load_settings())
    bot._phase_since -= 10  # bandeau de début de combat passé
    for _ in range(3):
        bot._step(frame, read_scene(frame))
    placement_click, ready_click = clicks(game)
    assert frame.grid.cell_at(*placement_click) == (13, 13)  # seule case rouge libre à portée du monstre
    assert ready_click == read_scene(frame).ready_button


def test_tall_character_covers_the_cell_above_it():
    frame = load_frame("placement_grand_personnage")  # Incarnam 1,1 : le sprite déborde sur la case du dessus
    game = FakeGame(frame)
    team = CombatAI(game, CombatSettings()).place(frame, RED)  # un contre un : la couleur du combat précédent
    assert team == RED  # une seule case à moi : la plus basse des deux cases « occupées »
    assert len(clicks(game)) == 1  # et il change de case


def test_placement_retried_while_cells_are_unreadable():
    frame = load_frame("hors_combat")  # pas de cases de placement : bandeau, animation d'entrée...
    ai = CombatAI(FakeGame(frame), CombatSettings())
    assert ai.place(frame, RED, last_try=False) is None  # nouvel essai au tour de boucle suivant
    assert ai.place(frame, RED, last_try=True) == ""  # abandon : Prêt sans bouger


def test_team_known_from_placement_so_my_turn_starts_at_once():
    frame = load_frame("preparation")
    bot = Bot(FakeGame(frame), load_settings())
    bot._phase_since -= 10
    bot._step(frame, read_scene(frame))
    assert bot.my_team == RED  # avant même le premier tour : pas besoin d'attendre le timer


def test_observe_mode_never_acts():
    frame = load_frame("preparation")
    game = FakeGame(frame)
    bot = Bot(game, load_settings(), act=False)
    bot._report(frame, read_scene(frame))
    assert game.actions == []


def test_my_team_learned_from_timer_for_each_combat():
    frame = load_frame("combat_tour_adversaire")
    bot = Bot(FakeGame(frame), load_settings())
    bot._step(frame, replace(read_scene(frame), timer_visible=True, current_team=RED))
    assert bot.my_team == RED
    bot._step(frame, read_scene(frame))  # tour adverse : on garde rouge
    assert bot.my_team == RED
    bot._phase_changed(Phase.COMBAT, Phase.OUT_OF_COMBAT)
    assert bot.my_team is None


def test_level_up_window_closed_first_with_ok():
    frame = load_frame("fin_de_combat_level_up")  # « Informations » posée sur le résultat du combat
    game = FakeGame(frame)
    assert PopupCloser(game, load_popup_specs(), "escape", 20).handle(frame, seconds_since_combat=2)
    ((x, y),) = clicks(game)
    assert abs(x - 1247) <= 3 and abs(y - 478) <= 3  # « Ok », pas « Fermer » (bloqué dessous)


def test_combat_result_is_closed_with_its_button():
    frame = load_frame("fin_de_combat")
    game = FakeGame(frame)
    assert PopupCloser(game, load_popup_specs(), "escape", 20).handle(frame, seconds_since_combat=None)
    ((x, y),) = clicks(game)
    assert abs(x - 1675) <= 3 and abs(y - 590) <= 3  # « Fermer », même longtemps après le combat
    assert presses(game) == []


def test_npc_dialog_closed_by_its_cross():
    frame = load_frame("dialogue_pnj_matu_vuh")
    game = FakeGame(frame, load_frame("hors_combat"))
    closer = PopupCloser(game, load_popup_specs(), "escape", 20)
    assert closer.describe(frame) == "bulle de dialogue d'un PNJ"
    assert closer.handle(frame, seconds_since_combat=None)
    ((x, y),) = clicks(game)
    assert abs(x - 1149) <= 4 and abs(y - 276) <= 4 and presses(game) == []


def test_no_engage_while_a_window_covers_the_map():
    frame = load_frame("fin_de_combat")
    game = FakeGame(frame)
    bot = Bot(game, {**load_settings(), "popups": {"unknown_window_key": ""}})
    bot.popups.specs = []  # fenêtre devenue inconnue : le bot ne sait pas la fermer
    bot._step(frame, read_scene(frame))
    assert game.actions == []


def with_window(name: str) -> Frame:
    """Capture de référence avec une fausse fenêtre beige posée sur la carte."""
    image = load_frame(name).image.copy()
    cv2.rectangle(image, (900, 200), (1400, 550), UI_BEIGE, -1)
    return Frame.of(image)


def test_unknown_window_closed_only_after_combat():
    frame = with_window("hors_combat")
    game = FakeGame(frame)
    closer = PopupCloser(game, [], unknown_key="escape", unknown_delay=20)
    assert not closer.handle(frame, seconds_since_combat=None)  # pas de combat récent : fenêtre ouverte par le joueur
    assert closer.handle(frame, seconds_since_combat=3)
    assert closer.handle(frame, seconds_since_combat=4)
    assert not closer.handle(frame, seconds_since_combat=5)  # deux essais, puis on abandonne
    assert game.actions == [("press", "escape"), ("press", "escape")]


def test_flash_menu_closed_by_a_click_under_the_spell_bar_never_escape():
    # 14/10 : clic droit à côté d'un Piou, menu de Flash ouvert ; Échap ne le fermait pas (Menu principal ouvert
    # derrière). Juste après un combat, la touche de secours des fenêtres inconnues ne doit pas partir non plus.
    frame = load_frame("menu_flash_clic_droit")
    game = FakeGame(without_flash_menu())
    closer = PopupCloser(game, load_popup_specs(), unknown_key="escape", unknown_delay=20)
    assert closer.describe(frame) == "menu de Flash"
    assert closer.handle(frame, seconds_since_combat=2)
    ((x, y),) = clicks(game)  # un seul clic, gauche
    assert not frame.layout.on_map((x, y)) and x > frame.layout.width * 0.7  # sous la barre de sorts, pas la carte
    assert presses(game) == []


def test_flash_menu_staying_open_is_left_to_the_player_without_escape(caplog):
    caplog.set_level("INFO")
    frame = load_frame("menu_flash_clic_droit")
    game = FakeGame(frame)  # le clic ne le ferme pas
    closer = PopupCloser(game, load_popup_specs(), unknown_key="escape", unknown_delay=20)
    assert closer.handle(frame, seconds_since_combat=2)
    assert closer.handle(frame, seconds_since_combat=3)
    assert not closer.handle(frame, seconds_since_combat=4)  # abandon : à toi de le fermer
    assert not closer.handle(frame, seconds_since_combat=5)
    assert len(clicks(game)) == 4 and not any(frame.layout.on_map(c) for c in clicks(game))
    assert presses(game) == []
    assert "ferme-le toi-même" in caplog.text
    assert not closer.handle(without_flash_menu(), seconds_since_combat=None)  # fermé par le joueur
    assert closer.handle(frame, seconds_since_combat=None)  # rouvert plus tard : nouveaux essais
    assert len(clicks(game)) == 6


def with_attack_menu(name: str, at=(1170, 460)) -> Frame:
    """Capture avec le menu « Monstres / Attaquer / Bestiaire » ouvert."""
    image = load_frame(name).image.copy()
    menu = cv2.imread(str(FIXTURES / "click_lancer_le_combat.png"))
    mx, my = at
    image[my : my + menu.shape[0], mx : mx + menu.shape[1]] = menu
    return Frame.of(image)


def missed_hover_looks() -> int:
    """Captures prises pendant un survol qui ne montre rien : guet de HOVER_WAIT s, une capture toutes les 0,1 s."""
    game = FakeGame(load_frame("hors_combat"))
    game.wait_until(lambda frame: False, HOVER_WAIT, park=False)
    return len(game.parks)


def first_hover_looks() -> int:
    """Captures du 1er survol jusqu'à la première qui compte : les infobulles de Z peuvent encore être là avant."""
    game = FakeGame(load_frame("hors_combat"))
    game.wait_until(lambda frame: game.now() >= Z_LINGER, HOVER_WAIT, park=False)
    return len(game.parks)


def test_clean_groups_are_tried_first():
    frame = load_frame("groupes_z_chevauchement")
    game = FakeGame(load_frame("hors_combat"), frame, load_frame("hors_combat"))  # aucun survol ne répond
    assert not Engager(game, EngageSettings()).engage()
    first_hover = next(a[1] for a in game.actions if a[0] == "move")
    assert abs(first_hover[0] - 811) <= 3  # « Niveau 10 » : le seul ni collé au bord ni collé à une autre infobulle


def test_clipped_tooltip_hover_points_move_toward_the_edge():
    frame = load_frame("groupes_z_chevauchement")
    niveau_9 = min(find_group_tooltips(frame), key=lambda t: t.x)
    xs = {round(x) for x, _ in hover_points(niveau_9, frame)}
    assert min(xs) <= niveau_9.x - 80  # le groupe peut être plus près du bord gauche


def test_group_that_left_is_given_up_quickly():
    groups = load_frame("hors_combat_detection_enemi")
    empty = load_frame("hors_combat")
    # Survol trouvé du 1er coup, menu, clic Attaquer ; puis le personnage marche mais le groupe a disparu (Z : rien).
    game = FakeGame(empty, groups, *[groups] * first_hover_looks(), with_attack_menu("hors_combat"), empty)
    engager = Engager(game, EngageSettings())
    assert not engager.engage()
    assert engager.missed  # la boucle relancera une recherche presque aussitôt
    assert [a for a in game.actions if a[0] == "hold"].count(("hold", "z")) >= 2  # Z revérifié pendant la marche


def test_engage_gives_up_without_groups():
    game = FakeGame(load_frame("hors_combat"))
    assert not Engager(game, EngageSettings()).engage()
    assert clicks(game) == []


def test_engage_aborts_when_a_combat_starts():
    game = FakeGame(load_frame("hors_combat"), load_frame("combat_tour_adversaire"))
    assert not Engager(game, EngageSettings()).engage()
    assert clicks(game) == []


def test_no_engage_right_after_ready_click():
    # Entre Prêt et l'affichage de la timeline, l'écran ressemble à du hors combat pendant quelques secondes.
    frame = load_frame("hors_combat")
    game = FakeGame(frame)
    bot = Bot(game, load_settings())
    bot._phase_changed(Phase.PLACEMENT, Phase.OUT_OF_COMBAT)
    bot._step(frame, read_scene(frame))
    assert game.actions == []


def test_rest_on_the_stool_when_hp_is_low():
    low = load_frame("coeur_43pv")  # 43 PV sur 95 : cœur à moitié jaune
    full = load_frame("hors_combat")  # 65/65
    game = FakeGame(low, low, full)
    Rester(game, RestSettings()).rest()
    ((x, y),) = clicks(game)
    assert abs(x - 663) <= 3 and abs(y - 795) <= 3  # tabouret, cliqué une seule fois ; repart à vie pleine


def test_low_hp_triggers_rest_after_the_result_window():
    low = load_frame("coeur_43pv")  # fenêtre « Résultat du combat » encore ouverte
    game = FakeGame(low)
    bot = Bot(game, load_settings())
    bot.spellbook.read_done = True
    bot._phase_changed(Phase.COMBAT, Phase.OUT_OF_COMBAT)
    bot._phase_since -= 10
    bot._step(low, read_scene(low))
    ((x, y),) = clicks(game)
    assert abs(x - 1675) <= 5  # d'abord « Fermer » ; le repos viendra au tour suivant
    assert bot._rest_pending


def test_no_rest_when_hp_is_high():
    frame = load_frame("hors_combat")
    game = FakeGame(frame)
    bot = Bot(game, {**load_settings(), "engage": {"enabled": True}})
    bot.spellbook.read_done = True
    bot.engager = None  # on ne regarde que le repos
    bot._phase_changed(Phase.COMBAT, Phase.OUT_OF_COMBAT)
    bot._phase_since -= 10
    bot.rester.rest()
    assert clicks(game) == []


def test_no_placement_reading_during_the_start_banner():
    frame = load_frame("preparation")
    game = FakeGame(frame)
    bot = Bot(game, load_settings())
    bot._phase_changed(Phase.OUT_OF_COMBAT, Phase.PLACEMENT)  # placement qui vient de commencer
    bot._step(frame, read_scene(frame))
    assert game.actions == []


def test_placement_waits_for_the_entry_banner_to_go():
    frame = load_frame("placement_bandeau")  # 01/10 : le bandeau assombrit la rangée du haut de mes cases rouges
    game = FakeGame(frame)
    bot = Bot(game, load_settings())
    bot._phase_changed(Phase.OUT_OF_COMBAT, Phase.PLACEMENT)
    bot._phase_since -= 2  # bien après le premier essai, mais bandeau toujours là
    scene = read_scene(load_frame("preparation"))  # capture du journal annotée : son bouton Prêt est barré
    bot._step(frame, scene)
    assert game.actions == [] and bot.my_team is None


def with_spell_window(name: str, window: str = "fenetre_sorts") -> Frame:
    image = load_frame(name).image.copy()
    window = whole_list(cv2.imread(str(FIXTURES / f"{window}.png")))  # rien à faire défiler
    image[40 : 40 + window.shape[0], 640 : 640 + window.shape[1]] = window
    return Frame.of(image)


def test_spellbook_reads_the_levels_without_opening_the_sheets():
    closed = load_frame("hors_combat")
    window = with_spell_window("hors_combat")
    game = FakeGame(closed, window, window, closed)
    book = SpellBook(game)
    assert book.read()
    button, close = clicks(game)  # aucun clic sur les noms des sorts
    assert abs(button[0] - 1512) <= 3 and abs(button[1] - 815) <= 3  # bouton baguette
    assert abs(close[0] - 1191) <= 3 and abs(close[1] - 65) <= 3  # croix de la fenêtre
    assert len(book.entries) == 10 and len(book.spells) == 5 and book.class_key == "cra"
    assert [(s.name, s.level) for s in book.spells.values()] == [
        ("Flèche de Recul", 1), ("Flèche Empoisonnée", 1), ("Flèche Magique", 4), ("Flèche Glacée", 1),
        ("Flèche Enflammée", 1)]
    assert all(not s.remark for s in book.spells.values())  # PA et portée lus : ceux du niveau lu


def test_spells_of_the_bar_known_to_the_tactics():
    book = SpellBook(FakeGame(with_spell_window("hors_combat")))
    book.learn(read_spell_window(with_spell_window("hors_combat")))
    ai = CombatAI(FakeGame(load_frame("combat_mon_tour")), CombatSettings(), book)
    ai._use_spellbook(load_frame("combat_mon_tour"))  # barre : Recul, Empoisonnée, Magique, Glacée
    by_slot = {s.slot: s for s in ai.spells}
    assert [by_slot[k].name for k in (1, 2, 3, 4)] == ["Flèche de Recul", "Flèche Empoisonnée", "Flèche Magique", "Flèche Glacée"]
    assert by_slot[3].spell_range == (1, 10) and not by_slot[3].in_line and by_slot[3].per_target == 2
    assert by_slot[1].in_line and by_slot[1].push == 3 and by_slot[1].role == "repousser"


def test_group_searched_only_below_its_tooltip_away_from_the_top():
    frame = load_frame("hors_combat_detection_enemi")  # infobulle en milieu de carte
    (tooltip,) = find_group_tooltips(frame)
    assert all(y > tooltip.bottom for _, y in hover_points(tooltip, frame))


def test_mouse_moved_away_only_when_it_hides_something():
    assert _hover_disturbs(load_frame("combat_infobulle"))  # monstre survolé : sa fiche cache la barre de sorts
    assert not _hover_disturbs(load_frame("combat_mon_tour"))
    assert not _hover_disturbs(load_frame("hors_combat"))


def test_spells_of_the_28_09_taken_from_solomonk_at_the_level_read():
    book = book_of_the_28_09()
    book.slots = {spell.entry.row + 1: spell.entry for spell in book.spells.values()}  # barre : cases 1 à 6
    spells = {s.spell_id: s for s in book.combat_spells()}
    recul, enflammee, boost = spells["fleche_de_recul"], spells["fleche_enflammee"], spells["tir_eloigne"]
    assert recul.spell_range == (1, 4) and recul.damage == 13.5 and recul.push == 3  # niveau 2 : 12 à 15
    assert enflammee.zone == ("ligne", 4) and enflammee.per_turn == 2 and enflammee.role == "degats_zone"
    assert spells["fleche_magique"].zone == ("point", 0) and boost.zone == ("point", 0)
    assert boost.range_bonus == 2 and boost.bonus_turns == 3 and boost.cooldown == 5  # niveau 2 : +2 PO
    assert spells["fleche_empoisonnee"].damage == 5.0  # 2 à 3 pendant 2 tours


def test_misread_level_corrected_by_pa_and_range():
    recul = load_reference()["sorts"]["169"]  # 4 PA ; portée 1 à 3 au niveau 1, 1 à 4 au niveau 2
    icon = np.zeros((46, 46, 3), np.uint8)
    level, remark = choose_level(recul, SpellEntry(0, 4, (1, 4), True, icon, level=1))
    assert level["niveau"] == 2 and "niveau 2" in remark
    level, remark = choose_level(recul, SpellEntry(0, 4, (1, 3), True, icon, level=None))
    assert level["niveau"] == 1 and "illisible" in remark
    assert choose_level(recul, SpellEntry(0, 4, (1, 4), True, icon, level=2)) == (recul["niveaux"][1], "")


def test_class_found_from_pa_and_range_without_known_icons():
    entries = read_spell_window(with_spell_window("hors_combat", "fenetre_sorts_28_09"))
    assert guess_class(entries, load_reference(), keys={}) == "cra"


def test_combat_only_mode_does_not_rest_nor_engage():
    bot = Bot(FakeGame(load_frame("hors_combat")), load_settings(), engage=False)
    assert bot.engager is None and bot.rester is None


def test_mouse_steps_aside_instead_of_going_to_the_edge():
    from bot.brain.game import Game

    hovered, clear = load_frame("combat_infobulle"), load_frame("combat_mon_tour")  # fiche d'un monstre survolé

    class Input:
        enabled = True
        position = (1300, 400)
        moves: list = []

        def wait_foreground(self):
            pass

        def cursor(self):
            return self.position

        def move(self, point, spread=(0, 0)):
            self.moves.append(point)
            self.position = point
            return point

    class Screen:
        frames = [hovered, clear]

        def grab(self, rect):
            return (self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]).image

    game = Game.__new__(Game)
    game.input, game._screen, game._layout = Input(), Screen(), hovered.layout
    game.window = type("Window", (), {"client_rect": lambda self: None})()
    game.control = type("Control", (), {"sleep": lambda self, seconds: None})()
    assert game.look() is not None
    ((x, y),) = game.input.moves  # un seul petit pas, pas d'aller-retour jusqu'au bord
    assert abs(x - 1300) + abs(y - 400) <= 150


def test_unfindable_group_is_skipped_at_the_next_search():
    groups, empty = load_frame("hors_combat_detection_enemi"), load_frame("hors_combat")
    (tooltip,) = find_group_tooltips(groups)
    hovers = len(hover_points(tooltip, groups))
    # 1re recherche : aucun survol ne fait apparaître l'infobulle (groupe caché derrière une maison, par exemple).
    game = FakeGame(empty, groups, *[empty] * (hovers * missed_hover_looks()), empty, groups, empty)
    engager = Engager(game, EngageSettings())
    assert not engager.engage()
    assert sum(a[0] == "move" for a in game.actions) == hovers
    game.actions.clear()
    assert not engager.engage()  # 2e recherche : même infobulle au même endroit, sautée
    assert not any(a[0] == "move" for a in game.actions)


def test_impossible_range_read_on_a_relief_map_is_ignored(monkeypatch):
    from bot.perception.sight import RangeView

    # 03/10 : sur une carte à relief, la portée de Flèche Magique (1 à 8) a été lue « 1 à 12 » : le bot est allé
    # chercher l'ennemi à 12 cases. Une portée lue au-delà de celle du sort (Solomonk + bonus) est ignorée.
    frame = load_frame("combat_portee_sort")
    magique = CombatSpell(3, "fleche_magique", "Flèche Magique", 4, (1, 8), range_modifiable=True, damage=11)
    me = Fighter(RED, *frame.grid.center((10, 20)), (10, 20))
    ai = CombatAI(FakeGame(frame), CombatSettings())
    monkeypatch.setattr("bot.brain.combat.read_range", lambda *a: RangeView({(11, 21)}, set(), set(), (1, 12)))
    monkeypatch.setattr("bot.brain.combat.sight_agreement", lambda *a: 1.0)
    ai._learn_range(frame, me, [me], magique)
    assert "fleche_magique" not in ai._use.measured
    monkeypatch.setattr("bot.brain.combat.read_range", lambda *a: RangeView({(11, 21)}, set(), set(), (1, 8)))
    ai._learn_range(frame, me, [me], magique)
    assert ai._use.measured["fleche_magique"] == (1, 8)


def test_impossible_range_teaches_no_obstacle_and_no_relief(monkeypatch):
    from bot.perception.sight import RangeView

    # 17/10 : Pelle Fantomatique (0 à 6) lue 1 à 8, losange pas centré sur ma case ; ses cases non colorées apprises
    # comme blocs, ligne de vue prédite juste sur 22 % des cases, carte crue à relief.
    frame = load_frame("combat_portee_sort")
    pelle = CombatSpell(2, "pelle_fantomatique", "Pelle Fantomatique", 3, (0, 6), range_modifiable=False, damage=8)
    me = Fighter(RED, *frame.grid.center((10, 20)), (10, 20))
    ai = CombatAI(FakeGame(frame), CombatSettings())
    ai.map.obstacles.add((12, 22))
    shifted = RangeView({(12, 22), (13, 23)}, {(9, 27)}, {(14, 20), (10, 24)}, (1, 8))
    monkeypatch.setattr("bot.brain.combat.read_range", lambda *a: shifted)
    monkeypatch.setattr("bot.brain.combat.sight_agreement", lambda *a: 0.22)
    ai._learn_range(frame, me, [me], pelle)
    assert ai.map.obstacles == set()  # (12, 22) colorée : pas un bloc ; rien d'appris d'autre
    assert ai._map_reliable and "pelle_fantomatique" not in ai._use.measured


def test_range_still_fading_in_is_not_read(monkeypatch):
    from bot.perception.sight import RangeView

    # 11/10 : portée lue en plein fondu, toutes ses cases bleu clair (« sans ligne de vue ») : carte crue à relief.
    frame = load_frame("combat_portee_sort")
    magique = CombatSpell(3, "fleche_magique", "Flèche Magique", 4, (1, 8), range_modifiable=True, damage=11)
    me = Fighter(RED, *frame.grid.center((10, 20)), (10, 20))
    ai = CombatAI(FakeGame(frame), CombatSettings())
    faded = RangeView(set(), {(11, 21), (12, 22), (9, 19)}, set(), (1, 2))
    monkeypatch.setattr("bot.brain.combat.read_range", lambda *a: faded)
    ai._learn_range(frame, me, [me], magique)
    assert "fleche_magique" not in ai._use.measured and ai._map_reliable


def test_pm_bubble_not_yet_updated_after_a_move_is_not_trusted():
    # 01/10 : après un déplacement, la bulle montrait encore 3 PM ; un 2e déplacement a été planifié avec 0 PM.
    frame = load_frame("combat_mon_tour")  # bulle : 3 PM
    ai = CombatAI(FakeGame(frame), CombatSettings())
    assert ai._pm_left(frame, 3) == 3 and ai._pm_left(frame, 1) == 1


def test_ready_clicked_again_when_the_click_was_cancelled():
    frame = load_frame("preparation")
    game = FakeGame(frame)
    game.click = lambda point=None, spread=(0, 0), button="left": False  # autre fenêtre sous la souris
    bot = Bot(game, load_settings())
    bot._phase_since -= 10
    bot._placed = True
    bot._step(frame, read_scene(frame))
    assert not bot._ready_clicked  # sera refait au tour de boucle suivant


def test_spell_window_read_again_after_a_failure():
    book = SpellBook(FakeGame(load_frame("combat_mon_tour")))  # ni fenêtre des sorts, ni bouton pour l'ouvrir
    results = [book.read() for _ in range(3)]
    assert results == [False, False, False]
    assert book._tries == 3 and book.read_done  # abandon après 3 essais seulement


def test_who_am_i_from_the_spell_range_when_nothing_else_says():
    # 03/10 : combat repris sans connaître les cases (faux « Combat terminé » juste après Prêt) : Walitcoin s'est pris
    # pour Imthebest. La portée d'un sort s'affiche autour de MA case : le cercle au centre, c'est moi.
    shown = load_frame("combat_portee_sort")  # portée 1 à 7 autour de moi
    me, _ = me_and_enemy(shown)
    decoy_cell = (me.cell[0] - 4, me.cell[1] - 2)
    decoy = Fighter(RED, *shown.grid.center(decoy_cell), decoy_cell)  # un allié, 4 cases plus loin
    game = FakeGame(shown, load_frame("combat_mon_tour"))  # sort sélectionné, puis Échap : plus de portée
    ai = CombatAI(game, CombatSettings())
    assert ai._locate_by_range(shown, [decoy, me], [decoy, me]) is me
    assert presses(game) == ["3", "escape"]


def test_who_am_i_when_my_circle_is_hidden():
    # 03/10 : Jesuisleking sous la timeline, son cercle invisible : la portée affichée dit quand même où il est.
    # 18/10, forêt d'Incarnam 3,4 : Vertdepomme juste au-dessus de la timeline, sa case comptée sous les portraits.
    from test_perception import relief_frame

    shown = relief_frame("portee_foret_plateau_lanceur_sur_la_timeline_incarnam_3_4", 470, 990)
    fighters = [f for f in find_fighters(shown) if f.cell != (19, 29)]  # son cercle, sous les portraits : pas vu
    found = locate_caster(shown, (1, 9), {f.cell for f in fighters if f.team == RED}, fighters)
    assert found is not None and found.cell == (19, 29) and found.hidden


def test_an_empty_cell_at_the_range_center_is_no_proof():
    # 18/10 : Pelle Fantomatique attendue 0 à 6, affichée plus loin (bonus d'un allié) : centre sur une case vide, rien
    # pour y cacher un cercle, pris pour « mon cercle est caché ». Ce n'est pas une preuve.
    shown = load_frame("combat_portee_sort")
    me, _ = me_and_enemy(shown)
    ally_cell = (me.cell[0] - 4, me.cell[1] - 2)
    allies = [Fighter(RED, *shown.grid.center(ally_cell), ally_cell)]  # mon cercle, bien visible, oublié exprès
    game = FakeGame(shown, load_frame("combat_mon_tour"))
    assert CombatAI(game, CombatSettings())._locate_by_range(shown, allies, allies) is None


def test_range_center_on_the_real_overlay():
    shown = load_frame("combat_portee_sort")  # portée 1 à 7
    me, _ = me_and_enemy(shown)
    assert range_center(shown, (1, 7)) == me.cell
    assert range_center(load_frame("combat_mon_tour"), (1, 7)) is None  # pas de portée affichée


def test_range_center_of_a_spell_cast_in_line():
    # 19/10, donjon d'Incarnam : la portée de Flèche de Recul (1 à 5, en ligne) n'est qu'une croix ; cherchée comme un
    # losange, aucun centre (117 repérages ratés) : le Crâ a pris un autre cercle pour le sien, 44 « n'a pas bougé ».
    for name, cell in (("reperage_fleche_de_recul_en_ligne", (8, 20)), ("reperage_fleche_de_recul_au_bord", (2, 26))):
        shown = load_frame(name)
        assert range_center(shown, (1, 5)) is None
        assert range_center(shown, (1, 5), in_line=True) == cell
        assert any(f.cell == cell and f.team == RED for f in find_fighters(shown))  # un cercle de mon équipe


def test_who_am_i_prefers_a_spell_whose_range_is_a_diamond():
    # 19/10 : Flèche de Recul passée de 1-3 à 1-5, plus près de LOCATE_RANGE que Flèche Empoisonnée : choisie pour se
    # repérer, sa croix ne disait rien. Un sort en ligne passe après les autres.
    shown = load_frame("combat_portee_sort")  # portée 1 à 7 autour de moi
    me, _ = me_and_enemy(shown)
    decoy_cell = (me.cell[0] - 4, me.cell[1] - 2)
    decoy = Fighter(RED, *shown.grid.center(decoy_cell), decoy_cell)
    game = FakeGame(shown, load_frame("combat_mon_tour"))
    ai = CombatAI(game, CombatSettings())
    ai._spells = [CombatSpell(1, "fleche_de_recul", "Flèche de Recul", 4, (1, 6), in_line=True, damage=18),
                  CombatSpell(3, "fleche_magique", "Flèche Magique", 4, (1, 7), damage=16)]
    assert ai._locate_by_range(shown, [decoy, me], [decoy, me]) is me
    assert presses(game) == ["3", "escape"]


def test_ambiguous_placement_cell_goes_to_a_free_cell(monkeypatch):
    # 03/10 : le Crâ en bas de la carte, son corps et son arc débordent sur la case voisine : 2 cases possibles. Avant,
    # « je ne bouge pas » : ni sa case ni l'équipe connues, chacun s'est pris pour un autre ensuite.
    frame = load_frame("placement_case_ambigue")
    real = read_placement(frame)
    game = FakeGame(frame)

    def after_click(seen):  # le jeu me déplace sur la case cliquée si elle est de ma couleur (rouge)
        if not clicks(game):
            return real
        cell = seen.grid.cell_at(*clicks(game)[-1])
        moved = cell in real.free[RED]
        return Placement(real.free, {RED: real.occupied[RED] | ({cell} if moved else set()), BLUE: real.occupied[BLUE]})

    monkeypatch.setattr("bot.brain.combat.read_placement", after_click)
    ai = CombatAI(game, CombatSettings())
    assert ai.place(frame, RED) == RED
    (click,) = clicks(game)
    assert ai.placed_cell == frame.grid.cell_at(*click) and ai.placed_cell in real.free[RED]
    # Couleur du combat précédent (bleu), fausse : la case bleue cliquée ne me déplace pas, la rouge si.
    game.actions.clear()
    ai = CombatAI(game, CombatSettings())
    assert ai.place(frame, BLUE) == RED and ai.placed_cell in real.free[RED]
    assert [frame.grid.cell_at(*c) in real.free[BLUE] for c in clicks(game)] == [True, False]


def test_ambiguous_placement_tries_the_other_colour_when_the_click_did_not_move_me(monkeypatch):
    # 2 contre 2 (03/10, Walitcoin au bord d'un groupe de cases, 2 Pichons) : la couleur supposée peut être fausse ;
    # le jeu ne me déplace que sur une case de mon équipe : clic sans effet, l'autre couleur.
    frame = load_frame("placement_case_ambigue")
    real = read_placement(frame)
    two_blue = set(sorted(real.occupied[BLUE])[:2])
    game = FakeGame(frame)

    def after_click(seen):
        occupied = {RED: set(real.occupied[RED]), BLUE: set(two_blue)}
        if clicks(game):
            cell = seen.grid.cell_at(*clicks(game)[-1])
            if cell in real.free[RED]:
                occupied[RED].add(cell)
        return Placement(real.free, occupied)

    monkeypatch.setattr("bot.brain.combat.read_placement", after_click)
    monkeypatch.setattr("bot.brain.combat.standing_cells", lambda cells: set(cells) if len(cells) <= 2 else cells)
    ai = CombatAI(game, CombatSettings())
    assert ai.place(frame, BLUE) == RED  # bleu supposé (combat précédent) : faux
    assert [frame.grid.cell_at(*c) in real.free[BLUE] for c in clicks(game)] == [True, False]


def test_spell_that_never_goes_off_on_enemies_is_dropped_for_the_session():
    # 03/10 : Sacrifice Poupesque (Sadida, ne vise que ses invocations) cliqué sur un monstre à chaque tour, jamais
    # parti, ~5 s perdues à chaque fois. Deux fois de suite : écarté pour la session.
    frame = load_frame("combat_portee_sort")
    me, _ = me_and_enemy(frame)
    target = enemy_in_range(frame)
    settings = CombatSettings(cast_confirm=0.15)
    ai = CombatAI(FakeGame(frame), settings)
    spell = ai.spells[0]
    assert ai._cast(frame, me, target, [me, target], RED, spell) is Cast.NO_SPELL
    assert spell.spell_id not in ai._banned  # une fois : peut-être un clic perdu
    assert ai._cast(frame, me, target, [me, target], RED, spell) is Cast.NO_SPELL
    assert spell.spell_id in ai._banned
    ai.new_combat()
    assert spell.spell_id in ai._banned  # pour toute la session


def test_spells_only_for_invocations_are_not_attacks():
    from bot.brain.spellbook import load_knowledge
    roles = load_knowledge()
    assert roles["sacrifice_poupesque"]["role"] == "autre"


def test_ally_with_a_hidden_circle_is_still_an_ally():
    # 03/10 : Jesuisleking sous la timeline, son cercle invisible : les autres l'auraient visé dans une zone ou auraient
    # marché sur sa case. Sa dernière case connue (mode équipe) le remplace.
    frame = load_frame("combat_mon_tour")
    fighters = find_fighters(frame)
    me = next(f for f in fighters if f.team == RED)
    hidden = (me.cell[0] + 2, me.cell[1] + 2)
    ai = CombatAI(FakeGame(frame), CombatSettings())
    ai._team_cells = lambda: {hidden}
    ai._me_cell = me.cell
    ai._me_sure = True  # case sûre (play_turn(me_cell=...))
    found, allies, _ = ai._sides(frame, fighters, RED)
    assert found == me and [a.cell for a in allies] == [hidden]


def test_stale_tracked_cell_is_checked_once_per_turn_with_the_spell_range():
    # 03/10 : case suivie périmée (poussé par un monstre), « je m'y crois toujours » : 3 déplacements ratés de suite.
    # Pas de cercle sur ma case suivie : repérage par la portée d'un sort, une seule fois dans le tour.
    shown = load_frame("combat_portee_sort")
    me, _ = me_and_enemy(shown)
    ally_cell = (me.cell[0] - 4, me.cell[1] - 2)
    mine = [me, Fighter(RED, *shown.grid.center(ally_cell), ally_cell)]
    game = FakeGame(shown, load_frame("combat_mon_tour"))
    ai = CombatAI(game, CombatSettings())
    ai._me_cell, ai._find_me, ai._located = (me.cell[0] + 2, me.cell[1] + 2), (lambda frame, circles: None), False
    found, _, _ = ai._sides(shown, mine, RED)
    assert found.cell == me.cell and presses(game) == ["3", "escape"]
    ai._me_cell = (me.cell[0] + 2, me.cell[1] + 2)  # encore faux : pas de second repérage ce tour
    found, _, _ = ai._sides(shown, mine, RED)
    assert found.cell == ai._me_cell and presses(game) == ["3", "escape"]


def test_last_spell_of_the_turn_is_not_waited_for():
    # Demande du 04/10 : peu de clics de sort ratés ; dernier sort du tour : clic puis fin de tour, sans attendre son
    # animation (les PA qui baissent).
    frame = load_frame("combat_portee_sort")
    me, _ = me_and_enemy(frame)
    target = enemy_in_range(frame)
    game = FakeGame(frame, frame)
    ai = CombatAI(game, CombatSettings(fast_end=True))
    assert ai._cast(frame, me, target, [me, target], RED, confirm=False) is Cast.DONE
    assert clicks(game) == [(round(target.x), round(target.y))]
    looks_after_click = [a for a in game.actions[game.actions.index(next(a for a in game.actions if a[0] == "click")):]
                         if a[0] == "look"]
    assert looks_after_click == []  # aucune surveillance des PA après le clic


def test_fast_burst_presses_the_key_again_until_the_game_takes_it(monkeypatch):
    # Rafale rapide : touche du sort suivant pressée sans attendre la fin de l'animation, puis de nouveau toutes les
    # BURST_REPRESS s tant que la portée ne s'affiche pas.
    shown, busy = load_frame("combat_portee_sort"), load_frame("combat_mon_tour")
    game = FakeGame(busy, busy, busy, busy, busy, shown)
    ai = CombatAI(game, CombatSettings(fast_end=True))
    assert ai._burst_ready("3") is shown
    assert presses(game) == ["3", "3"]  # une seconde pression : l'animation du lancer précédent durait encore
    game = FakeGame(busy)
    assert CombatAI(game, CombatSettings(fast_end=True))._burst_ready("3") is None  # jamais prise : rafale arrêtée


def test_creature_mode_switched_on_only_when_off():
    # Demande du 04/10 : mode créature en combat (boule à côté du cube du mode tactique), jamais recliqué s'il est
    # déjà coché (le clic le désactiverait).
    off, on = load_frame("combat_cube_non_coche"), load_frame("combat_mode_creature")
    game = FakeGame(off, on)
    ai = CombatAI(game, CombatSettings(creature_mode=True))
    ai.ensure_creature_mode(off)
    (click,) = clicks(game)
    assert abs(click[0] - find_creature_button(off).point[0]) <= 6
    game = FakeGame(on)
    ai = CombatAI(game, CombatSettings(creature_mode=True))
    ai.ensure_creature_mode(on)
    assert clicks(game) == []


def test_tactical_button_clicked_even_under_the_turn_arrow():
    # 04/10 : la flèche ▽ du tour posée sur le cube : « bouton du mode tactique introuvable », carte jamais tactique.
    frame = load_frame("combat_fleche_sur_cube")
    game = FakeGame(frame, frame)
    CombatAI(game, CombatSettings())._ensure_tactical(frame)
    (click,) = clicks(game)
    button = find_tactical_button(frame)
    assert not button.active and abs(click[0] - button.point[0]) <= button.room and abs(click[1] - button.point[1]) <= button.room


def test_end_turn_button_ignored_while_walking_is_clicked_again():
    # 17/10 (journal 161256, Vieenjaune ×3) : clic 1 s après un déplacement, ignoré ; la main passée au second clic, pas
    # au tour suivant (4 s plus tard).
    mine, theirs = load_frame("combat_mon_tour"), load_frame("combat_tour_adversaire")
    game = FakeGame(*[mine] * 8, theirs)
    ai = CombatAI(game, CombatSettings())
    ai._end_turn_key = ""
    ai._pass_turn(RED)
    assert len(clicks(game)) == 2 and ai.turns_not_passed == 0


def test_no_more_moves_once_the_character_cannot_move(caplog):
    # 04/10 : Fourbocinq taclé au contact d'un Prespic (« Le jet d'esquive a échoué ») : 4 déplacements ratés d'affilée
    # dans le tour, ~5 s chacun. Au 2e déplacement sans bouger (ou au 1er au contact d'un ennemi) : plus de déplacement.
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    frame, end = load_frame("combat_mon_tour"), load_frame("combat_tour_adversaire")  # capture figée : rien ne bouge
    game = FakeGame(*[frame] * 80, end)
    CombatAI(game, CombatSettings()).play_turn(RED)
    moves = [m for m in caplog.messages if m.startswith(("Déplacement de", "Placement pour le prochain tour"))]
    assert len(moves) == 2 and any("plus de déplacement ce tour" in m for m in caplog.messages)


def test_one_wrong_cell_of_an_in_line_spell_is_not_a_relief_map(monkeypatch):
    from bot.perception.sight import RangeView

    # 15/10, -1,-19 et 1,-18 : Épée Céleste en ligne, 7 cases colorées ; une seule mal prédite (86 %) faisait passer
    # tout le combat en « carte à relief ». Deux cases fausses sur peu de cases, oui.
    frame = load_frame("combat_portee_sort")
    magique = CombatSpell(3, "fleche_magique", "Flèche Magique", 4, (1, 8), range_modifiable=True, damage=11)
    me = Fighter(RED, *frame.grid.center((10, 20)), (10, 20))
    seven = {(10 + k, 20 + k) for k in range(1, 8)}
    monkeypatch.setattr("bot.brain.combat.read_range", lambda *a: RangeView(seven, set(), set(), (1, 7)))
    ai = CombatAI(FakeGame(frame), CombatSettings())
    monkeypatch.setattr("bot.brain.combat.sight_agreement", lambda *a: 6 / 7)
    ai._learn_range(frame, me, [me], magique)
    assert ai._map_reliable
    monkeypatch.setattr("bot.brain.combat.sight_agreement", lambda *a: 5 / 7)
    ai._learn_range(frame, me, [me], magique)
    assert not ai._map_reliable
