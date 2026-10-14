"""Rafale (combat.burst) : le même sort relancé sur la même cible sans capture complète ni nouveau plan entre deux
lancers, pour tout sort (Lancer de Pièces 2 PA x3, Flèche Magique 4 PA x2 avec 8 PA) ; vie de la cible lue sur sa
fiche au survol (perception/hp.py) et lancers qui devraient la tuer (HitLog). Faux jeu à horloge simulée (test_waits) :
la touche du sort affiche la portée, le clic change les PA 0,8 s plus tard ; combattants, PA et vie lus fixés par test.
"""

import logging

import cv2
import numpy as np
import pytest

from bot.brain.combat import CombatAI, CombatSettings, MapMemory, TargetLife, burst_length
from bot.brain.engine import CAST, Board, Step, TurnPlan, plan_turn
from bot.brain.tactics import CombatSpell, HitLog, SpellUse
from bot.config import load_settings
from bot.perception.colors import BLUE
from bot.perception.fighters import Fighter
from bot.perception.frame import Frame
from bot.perception.hp import read_hovered_hp, sheet_visible
from bot.perception.colors import RED
from bot.perception.sight import Terrain
from bot.perception.templates import ASSETS_DIR
from bot.perception.ui import PA_BUBBLE
from conftest import SCREENS, load_frame
from test_brain import clicks, enemy_in_range, me_and_enemy, presses, with_forbidden_cursor_on
from test_waits import HIDDEN, SHOWN, ScriptedGame, summaries

# Sorts de l'Enutrof et du Crâ (valeurs Solomonk des journaux du 03/10, dégâts de base moyens).
PIECES = CombatSpell(1, "lancer_de_pieces", "Lancer de Pièces", 2, (0, 12), per_target=3, damage=3.5)
MAGIQUE = CombatSpell(3, "fleche_magique", "Flèche Magique", 4, (1, 10), per_target=2, damage=13)
RECUL = CombatSpell(2, "fleche_de_recul", "Flèche de Recul", 4, (1, 4), in_line=True, per_target=2, damage=13.5,
                    push=3, role="repousser")
OTHER = (15, 23)  # deuxième ennemi fictif, à 3 cases de moi
PA_LEVELS = (None, 255, 160, 0, 255)  # bulle des PA après chaque lancer : chaque état diffère du précédent
END = load_frame("combat_tour_adversaire")


# --- Vie lue sur la fiche du combattant survolé --------------------------------------------------------------------


@pytest.mark.parametrize("name, hp", [("combat_infobulle", 7), ("combat_fiche_walitcoin_212pv", 212)])
def test_hp_read_on_the_sheet_of_the_hovered_fighter(name, hp):
    # 27/09 : Piou Rose survolé, 7 PV ; 03/10 : Walitcoin (allié), 212 PV, capture JPEG d'un journal.
    frame = load_frame(name)
    assert sheet_visible(frame) and read_hovered_hp(frame) == hp


@pytest.mark.parametrize("name", [s for s in SCREENS if s != "combat_infobulle"])
def test_no_hp_without_a_hovered_fighter(name):
    assert not sheet_visible(load_frame(name)) and read_hovered_hp(load_frame(name)) is None  # barre de sorts


def sheet_showing(hp: int) -> Frame:
    """La fiche de Walitcoin, sa vie remplacée par `hp` dessiné en rouge avec les modèles de chiffres (Verdana)."""
    frame = load_frame("combat_fiche_walitcoin_212pv")
    image = frame.image.copy()
    image[856:890, 1500:1600] = image[872, 1580]  # fond de la fiche
    x = 1504
    for char in str(hp):
        mask = cv2.imdecode(np.fromfile(ASSETS_DIR / "vie" / f"chiffre_{char}.png", np.uint8), cv2.IMREAD_GRAYSCALE)
        h, w = mask.shape
        zone = image[865 : 865 + h, x : x + w]
        zone[mask > 127] = (0, 0, 255)
        x += w + 3
    return Frame.of(image)


@pytest.mark.parametrize("hp", [3058, 46, 9, 170, 1])
def test_every_digit_read_in_order(hp):
    assert read_hovered_hp(sheet_showing(hp)) == hp


# --- Dégâts mesurés et lancers pour tuer ---------------------------------------------------------------------------


def test_casts_to_kill_from_base_then_measured_damage():
    hits = HitLog()
    assert hits.expected(PIECES) == 3.5 and hits.casts_to_kill(PIECES, 7) == 2 and hits.casts_to_kill(PIECES, 8) == 3
    hits.learn(PIECES, 11)  # coup critique : la moyenne ne double pas
    assert hits.expected(PIECES) == pytest.approx(7.25)
    hits.learn(PIECES, 5)
    assert hits.expected(PIECES) == pytest.approx(6.5) and hits.casts_to_kill(PIECES, 13) == 2
    assert hits.casts_to_kill(PIECES, 0) == 0
    assert HitLog().expected(CombatSpell(1, "x", "x", 2, (1, 5))) == 1  # sort sans dégâts connus : 1 au moins


def test_burst_length_follows_the_engine_plan():
    cells = frozenset((i, j) for i in range(-20, 21) for j in range(-20, 21) if (i + j) % 2 == 0)

    def first_plan(spells, pa):
        use = SpellUse()
        use.new_turn(pa)
        return plan_turn((0, 0), 0, [(4, 4)], [], spells, use, Board(cells))

    assert burst_length(first_plan([PIECES], 6)) == 2  # 3 lancers de 2 PA : 2 après le premier
    assert burst_length(first_plan([MAGIQUE], 8)) == 1  # 8 PA : Flèche Magique deux fois
    assert burst_length(first_plan([MAGIQUE], 6)) == 0
    other = Step(CAST, (2, 2), PIECES, hits=((2, 2),))
    same = Step(CAST, (4, 4), PIECES, hits=((4, 4),))
    assert burst_length(TurnPlan((same, same, other, same))) == 1  # autre case : nouvelle cible, pas de rafale


def test_burst_setting():
    assert CombatSettings().burst is False  # sans réglage : un plan après chaque sort, comme avant
    assert CombatSettings.from_settings({"combat": {"burst": True}}).burst is True
    assert CombatSettings.from_settings(load_settings()).burst is True  # livré activé, à l'essai


# --- Rafale dans le tour -------------------------------------------------------------------------------------------


def pa_shown(frame: Frame, level: int | None) -> Frame:
    """Même capture, bulle des PA changée (`level` : gris de son centre ; None : inchangée)."""
    if level is None:
        return frame
    image = frame.image.copy()
    x0, y0, x1, y1 = frame.layout.box(*PA_BUBBLE)
    image[y0 + 8 : y1 - 8, x0 + 8 : x1 - 8] = level
    return Frame.of(image)


class Arena:
    """Mon tour (rouge, sur combat_portee_sort) : la cible, ennemi fictif à 4 cases, et un autre à 3 cases.

    Lancer k (0, 1...) : sa touche affiche la portée (`aimed[k]`), son clic change les PA 0,8 s plus tard (`calm[k+1]`).
    `hp[k]` : vie lue au survol du lancer k ; `dead_after` : la cible n'a plus de cercle à partir de `calm[dead_after]` ;
    `pa_read` : PA lus dans la bulle de `calm[k]` (sinon les PA de départ moins les lancers)."""

    def __init__(self, monkeypatch, spell: CombatSpell, pa: int, casts: int, burst: bool = True, hp=(),
                 dead_after: int | None = None, pa_read: dict | None = None, **settings) -> None:
        self.spell, self.pa = spell, pa
        self.me, _ = me_and_enemy(SHOWN)
        self.target = enemy_in_range(SHOWN)
        self.other = Fighter(BLUE, *SHOWN.grid.center(OTHER), OTHER)
        self.calm = [pa_shown(HIDDEN, level) for level in PA_LEVELS]
        self.aimed = [pa_shown(SHOWN, level) for level in PA_LEVELS]
        index = {id(f): k for k, f in enumerate(self.calm)} | {id(f): k for k, f in enumerate(self.aimed)}
        pa_read = pa_read or {}
        hp_of = {id(self.aimed[k]): value for k, value in enumerate(hp)}

        def read_pa(frame):
            k = index.get(id(frame))
            return None if k is None else pa_read.get(k, max(pa - k * spell.pa, 0))

        def fighters(frame):
            k = index.get(id(frame), 0)
            alive = dead_after is None or k < dead_after
            return [self.me, self.target, self.other] if alive else [self.me, self.other]

        monkeypatch.setattr("bot.brain.combat.read_pa", read_pa)
        monkeypatch.setattr("bot.brain.combat.find_fighters", fighters)
        monkeypatch.setattr("bot.brain.combat.read_hovered_hp", lambda frame: hp_of.get(id(frame)))
        monkeypatch.setattr("bot.brain.combat.read_terrain", lambda frame, fighters: Terrain(set(), set(), set()))
        self.game = ScriptedGame(self.calm[0])
        key = str(spell.slot)
        for k in range(casts):
            self.game.on(f"press {key}", (0.05, self.aimed[k])).on("click", (0.8, self.calm[k + 1]))
        self.game.on("press f1", (0.3, END))
        self.ai = CombatAI(self.game, CombatSettings(burst=burst, end_turn_key="f1", **settings))
        self.ai._spells = [spell]
        self.plans: list[TurnPlan] = []
        self.ai._plan = self._plan

    def _plan(self, frame, me, enemies, allies, pm, *args, **kwargs) -> TurnPlan:
        """Comme le moteur : le sort sur la cible tant que PA et lancers par cible le permettent, puis sur l'autre."""
        use, spell = self.ai._use, self.spell
        for enemy in enemies:
            left = spell.per_target - use.casts_on_target[(spell.spell_id, enemy.cell)]
            count = max(0, min(left, use.pa // spell.pa))
            if count:
                step = Step(CAST, enemy.cell, spell, hits=(enemy.cell,), damage=spell.damage, reason="test")
                self.plans.append(TurnPlan((step,) * count))
                return self.plans[-1]
        self.plans.append(TurnPlan())
        return self.plans[-1]

    def play(self) -> "Arena":
        self.ai.play_turn(RED)
        return self

    def on(self, fighter: Fighter) -> tuple[int, int]:
        return round(fighter.x), round(fighter.y)

    # Après un premier lancer parti (pour appeler _burst seul) : PA comptés, capture du sort parti, vie lue.
    def after_first_cast(self, hp: int | None = None) -> Step:
        use = self.ai._use
        use.new_turn(self.pa)
        step = Step(CAST, self.target.cell, self.spell, hits=(self.target.cell,), damage=self.spell.damage)
        use.record(self.spell, step.cell)
        self.ai._keys[self.spell.slot] = str(self.spell.slot)
        self.ai._sent = self.calm[1]
        if hp is not None:
            self.ai._life = TargetLife(use.turn, self.target.cell, hp, [(self.spell.spell_id, self.target.cell)])
        self.game.shown = [(0.0, self.calm[1])]
        self.game.reactions.clear()
        return step


@pytest.mark.parametrize("spell, pa, casts", [(PIECES, 6, 3), (MAGIQUE, 8, 2)],
                         ids=["Lancer de Pièces 2 PA x3", "Flèche Magique 4 PA x2 avec 8 PA"])
def test_burst_chains_the_planned_casts_without_new_plans(monkeypatch, caplog, spell, pa, casts):
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    arena = Arena(monkeypatch, spell, pa, casts).play()
    assert presses(arena.game) == [str(spell.slot)] * casts + ["f1"]
    assert clicks(arena.game) == [arena.on(arena.target)] * casts  # jamais ailleurs que sur la cible vérifiée
    assert len(arena.plans) == 2  # un plan avant le premier lancer, un après la rafale (plus de PA : fin du tour)
    assert arena.ai._use.casts_on_target[(spell.spell_id, arena.target.cell)] == casts
    assert arena.ai._use.pa == pa - casts * spell.pa
    (line,) = summaries(caplog)
    assert f"{casts} sorts (dont {casts - 1} en rafale), 0 déplacement, 0 refus" in line


@pytest.mark.parametrize("spell, pa, casts", [(PIECES, 6, 3), (MAGIQUE, 8, 2)],
                         ids=["Lancer de Pièces 2 PA x3", "Flèche Magique 4 PA x2 avec 8 PA"])
def test_same_gestures_with_or_without_burst_minus_the_checks(monkeypatch, spell, pa, casts):
    plain = Arena(monkeypatch, spell, pa, casts, burst=False).play()
    burst = Arena(monkeypatch, spell, pa, casts).play()
    assert presses(plain.game) == presses(burst.game) and clicks(plain.game) == clicks(burst.game)
    assert len(plain.plans) == casts + 1 and len(burst.plans) == 2
    # Par relance : ni curseur interdit à revérifier (0,25 s), ni capture complète ni plan (temps réel en plus).
    assert plain.game.clock - burst.game.clock == pytest.approx(0.25 * (casts - 1))
    assert plain.game.looks > burst.game.looks


def test_same_burst_in_both_wait_modes(monkeypatch):
    fixed = Arena(monkeypatch, PIECES, 6, 3, hp=(20, 16, 12)).play()
    watched = Arena(monkeypatch, PIECES, 6, 3, hp=(20, 16, 12), wait_mode="condition").play()
    assert presses(fixed.game) == presses(watched.game) == ["1", "1", "1", "f1"]
    assert clicks(fixed.game) == clicks(watched.game) and len(fixed.plans) == len(watched.plans) == 2
    assert fixed.ai._hits.seen == watched.ai._hits.seen == {"lancer_de_pieces": [4, 4]}
    assert watched.game.clock < fixed.game.clock


def test_only_the_casts_that_should_kill_then_a_new_target(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    # Plan : 3 Lancers de Pièces sur la cible. Vie lue au survol : 5 PV (3,5 de dégâts de base : 2 lancers), puis 2 PV
    # (3 de dégâts mesurés : encore 1 lancer). Morte après le 2e : nouveau plan, le 3e part sur l'autre ennemi.
    arena = Arena(monkeypatch, PIECES, 6, 3, hp=(5, 2), dead_after=2).play()
    assert presses(arena.game) == ["1", "1", "1", "f1"]
    assert clicks(arena.game) == [arena.on(arena.target)] * 2 + [arena.on(arena.other)]
    assert [len(p.steps) for p in arena.plans] == [3, 1, 0]
    assert arena.ai._hits.seen == {"lancer_de_pieces": [3]}
    messages = [r.getMessage() for r in caplog.records]
    assert any("avait 5 PV" in m and "2 lancers pour le tuer, 1 de plus" in m for m in messages)


def test_no_burst_when_the_first_cast_should_kill(monkeypatch):
    arena = Arena(monkeypatch, PIECES, 6, 3, hp=(3,), dead_after=1).play()  # 3 PV : un lancer de 3,5 en moyenne
    assert clicks(arena.game) == [arena.on(arena.target), arena.on(arena.other), arena.on(arena.other)]
    assert len(arena.plans) == 3  # nouveau plan aussitôt (la cible est morte) : l'autre ennemi, en rafale


def test_burst_stops_when_the_target_has_no_ring_anymore(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    arena = Arena(monkeypatch, PIECES, 6, 3, dead_after=1)
    step = arena.after_first_cast()
    assert arena.ai._burst(arena.target, step, 2, RED, 4) == (0, False)
    assert arena.game.actions == []  # ni touche, ni clic sur une case vide
    assert any("plus de cercle en (16, 22)" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("spell, pa, pa_read, repeats, expected", [
    (MAGIQUE, 6, {}, 1, 0),  # 2 PA après le premier : plus de Flèche Magique (4 PA)
    (PIECES, 6, {1: 1}, 2, 0),  # bulle : 1 PA (un monstre en a retiré), et non les 4 comptés
    (PIECES, 10, {}, 4, 2),  # 3 lancers par cible : 2 de plus au plus, malgré 10 PA
], ids=["PA du sort", "PA lus dans la bulle", "lancers par cible"])
def test_burst_stays_within_pa_and_casts_per_target(monkeypatch, spell, pa, pa_read, repeats, expected):
    arena = Arena(monkeypatch, spell, pa, 4, pa_read=pa_read)
    step = arena.after_first_cast()
    for k in range(1, 4):
        arena.game.on(f"press {spell.slot}", (0.05, arena.aimed[k])).on("click", (0.8, arena.calm[k + 1]))
    assert arena.ai._burst(arena.target, step, repeats, RED, 4) == (expected, False)
    assert clicks(arena.game) == [arena.on(arena.target)] * expected


def test_burst_stops_at_the_turn_guard(monkeypatch):
    arena = Arena(monkeypatch, PIECES, 10, 4)
    step = arena.after_first_cast()
    for k in range(1, 4):
        arena.game.on("press 1", (0.05, arena.aimed[k])).on("click", (0.8, arena.calm[k + 1]))
    assert arena.ai._burst(arena.target, step, 2, RED, room=1) == (1, False)  # MAX_CASTS_PER_TURN presque atteint


def test_burst_stops_when_it_is_no_longer_my_turn(monkeypatch):
    arena = Arena(monkeypatch, PIECES, 6, 3)
    step = arena.after_first_cast()
    arena.ai._is_mine = lambda frame: False  # mode équipe : la main est passée (timer écoulé, échec critique)
    assert arena.ai._burst(arena.target, step, 2, RED, 4) == (0, False) and arena.game.actions == []


def test_forbidden_cursor_during_a_burst_cancels_the_spell(monkeypatch):
    arena = Arena(monkeypatch, PIECES, 6, 3)
    step = arena.after_first_cast()
    refused = with_forbidden_cursor_on(arena.aimed[1], arena.target)
    arena.game.on("press 1", (0.05, refused)).on("press escape", (0.05, arena.calm[1]))
    assert arena.ai._burst(arena.target, step, 2, RED, 4) == (0, False)
    assert presses(arena.game) == ["1", "escape"] and clicks(arena.game) == []


def test_spell_left_selected_after_a_burst_ends_the_turn(monkeypatch):
    # Curseur interdit au 2e lancer, et Échap sans effet : le sort reste sélectionné ; un clic de plus le lancerait
    # n'importe où (comme pour _cast : Cast.STUCK) -> plus d'action, fin du tour.
    arena = Arena(monkeypatch, PIECES, 6, 1)
    arena.game.on("press 1", (0.05, with_forbidden_cursor_on(arena.aimed[1], arena.target)))
    arena.play()
    assert presses(arena.game) == ["1", "1", "escape", "f1"]
    assert clicks(arena.game) == [arena.on(arena.target)] and len(arena.plans) == 1


def test_burst_stops_when_the_target_sheet_disappears(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    arena = Arena(monkeypatch, PIECES, 6, 3)  # vie lue au premier survol, plus rien au survol suivant (morte ?)
    step = arena.after_first_cast(hp=30)
    arena.game.on("press 1", (0.05, arena.aimed[1])).on("press escape", (0.05, arena.calm[1]))
    assert arena.ai._burst(arena.target, step, 2, RED, 4) == (0, False)
    assert presses(arena.game) == ["1", "escape"] and clicks(arena.game) == []
    assert any("plus de vie lue au survol" in r.getMessage() for r in caplog.records)


def test_no_burst_for_a_spell_that_pushes_its_target(monkeypatch):
    arena = Arena(monkeypatch, RECUL, 8, 2)
    step = arena.after_first_cast()
    assert arena.ai._burst(arena.target, step, 1, RED, 4) == (0, False) and arena.game.actions == []  # la cible a reculé


# --- Vie gardée pour l'équipe (MapMemory.life, 03/10) --------------------------------------------------------------


def test_life_read_on_hover_is_kept_for_the_whole_team():
    # Lue au survol par un personnage, la vie sert au plan des autres (achever, cible commune), diminuée des dégâts
    # attendus de chaque sort parti, et oubliée quand l'ennemi quitte sa case.
    frame = load_frame("combat_infobulle")  # Piou Rose survolé : 7 PV
    reader, other = CombatAI(ScriptedGame(frame), CombatSettings()), CombatAI(ScriptedGame(frame), CombatSettings())
    reader.map = other.map = MapMemory()  # partagée par l'équipe (team/loop.py)
    target = Fighter(BLUE, 0, 0, (14, 20))
    assert reader._read_life(frame, target) == 7 and other.map.life == {(14, 20): 7}
    reader._wound(PIECES, Step(CAST, (14, 20), PIECES, hits=((14, 20),)))
    assert other.map.life == {(14, 20): 4}  # 7 - 3,5 (dégâts de base de Lancer de Pièces)
    reader._wound(MAGIQUE, Step(CAST, (14, 20), MAGIQUE, hits=((14, 20),)))
    assert other.map.life == {(14, 20): 1}  # toujours debout tant que son cercle se voit
    other.map.keep_life({(3, 3)})  # plus personne en (14, 20)
    assert reader.map.life == {}


def test_life_read_during_a_burst_is_kept(monkeypatch):
    arena = Arena(monkeypatch, PIECES, 6, 3, hp=(20, 16, 12)).play()
    assert 0 < arena.ai.map.life[arena.target.cell] < 12  # dernière vie lue, moins le dernier lancer


def test_burst_counts_the_damage_of_the_cast_still_in_flight(monkeypatch, caplog):
    # 04/10 : au survol du lancer suivant (~0,4 s après le clic), la vie n'a pas encore baissé : les dégâts du lancer
    # précédent s'affichent à la fin de son animation. Compter sur cette vie faisait un lancer de trop, parti sur la case
    # de l'ennemi mort. 6 PV, 3,5 par lancer : 2 lancers, pas 3 ; le 3e part sur l'autre ennemi.
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    arena = Arena(monkeypatch, PIECES, 6, 3, hp=(6, 6), dead_after=2).play()
    assert clicks(arena.game) == [arena.on(arena.target)] * 2 + [arena.on(arena.other)]


def test_burst_stops_when_the_cast_in_flight_should_kill(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    arena = Arena(monkeypatch, PIECES, 6, 3, dead_after=2)
    step = arena.after_first_cast(hp=7)  # 7 PV avant le 1er lancer : 2 lancers prévus
    arena.ai._hits.learn(PIECES, 7)  # mesuré : 7 par lancer en moyenne avec la valeur de départ (3,5 -> 5,25)
    arena.ai._hits.learn(PIECES, 9)
    arena.game.on("press 1", (0.05, arena.aimed[1]))
    monkeypatch.setattr("bot.brain.combat.read_hovered_hp", lambda frame: 7)  # vie pas encore baissée
    assert arena.ai._burst(arena.target, step, 2, RED, 4)[0] == 0
    assert clicks(arena.game) == []
    assert any("devrait mourir du lancer précédent" in r.getMessage() for r in caplog.records)
