"""Qui est moi parmi les cercles de ma couleur (mode équipe) : toujours sur une preuve, jamais au hasard (19/10)."""

import pytest

from bot.brain.combat import HOVER_MISSES, CombatAI, CombatSettings
from bot.perception.chat import ocr_available
from bot.perception.fighters import Fighter, find_fighters
from bot.perception.hp import read_hovered_name, same_name
from conftest import load_frame
from test_brain import RED, FakeGame, me_and_enemy, presses

needs_ocr = pytest.mark.skipif(not ocr_available(), reason="OCR de Windows absent (requirements-winrt.txt)")


class HoverGame(FakeGame):
    """Faux jeu qui sait survoler sans clic (Game.hover) et note où."""

    def hover(self, point, spread=(0, 0)):
        self.actions.append(("hover", (round(point[0]), round(point[1]))))


def two_circles(frame):
    me, _ = me_and_enemy(frame)
    other = (me.cell[0] - 4, me.cell[1] - 2)
    return me, Fighter(RED, *frame.grid.center(other), other)


def hovers(game):
    return [a[1] for a in game.actions if a[0] == "hover"]


@needs_ocr
def test_hovered_name_is_read_on_the_sheet():
    assert same_name(read_hovered_name(load_frame("combat_fiche_walitcoin_212pv")), "Walitcoin")
    assert same_name(read_hovered_name(load_frame("combat_infobulle")), "Piou Rose")
    assert read_hovered_name(load_frame("combat_mon_tour")) is None  # barre de sorts : pas de fiche


def test_same_name_tolerates_one_misread_letter_only():
    assert same_name("Leproduitiatte", "Leproduitlatte")  # l lu i
    assert same_name("vieenjaune", "Vieenjaune")
    assert not same_name("Pesopepe", "Dinarpepe")
    assert not same_name("Vertdepomme", "Vertdepommes2000")
    assert not same_name(None, "Pombro")


def test_never_a_random_circle_in_team_mode(caplog):
    # 19/10 : « 8 cercles de ma couleur et rien ne dit lequel est moi : je prends celui en (2, 20) » ; Pombro et
    # Leproduitlatte s'y sont crus tous les deux. Sans preuve : personne, le tour ne se joue pas sur une supposition.
    frame = load_frame("combat_mon_tour")  # pas de portée affichée : le repérage par la portée ne dit rien
    me, other = two_circles(frame)
    game = FakeGame(frame)
    ai = CombatAI(game, CombatSettings(), name="Pombro")
    ai._find_me = lambda frame, circles: None
    ai._locate_by_range = lambda *a: None
    found, allies, _ = ai._sides(frame, [me, other], RED)
    assert found is None and not ai.me_sure
    assert "rien ne prouve lequel est moi" in caplog.text and "<qui_est_moi>" in caplog.text


@needs_ocr
def test_my_name_on_the_hovered_sheet_says_which_circle_is_me():
    frame = load_frame("combat_mon_tour")
    me, other = two_circles(frame)
    # 1er survol (le cercle le plus proche de ma dernière case) : fiche de Piou Rose ; 2e : la mienne.
    game = HoverGame(load_frame("combat_infobulle"), load_frame("combat_fiche_walitcoin_212pv"))
    ai = CombatAI(game, CombatSettings(), name="Walitcoin")
    ai._find_me = lambda frame, circles: None
    ai._locate_by_range = lambda *a: None
    ai._me_cell = other.cell  # dernière case connue, pas sûre : survolée en premier
    found, _, _ = ai._sides(frame, [me, other], RED)
    assert found is me and ai.me_sure and ai._me_cell == me.cell
    assert hovers(game) == [(round(other.x), round(other.y)), (round(me.x), round(me.y))]


def test_hover_given_up_for_the_session_when_no_sheet_ever_shows():
    # 08/10 (tools/arriere_plan.py) : en arrière-plan, aucun survol envoyé n'a montré d'infobulle. Pas 8 survols à
    # chaque tour pour rien : abandonné après HOVER_MISSES essais sans fiche.
    frame = load_frame("combat_mon_tour")
    me, other = two_circles(frame)
    game = HoverGame(frame)
    ai = CombatAI(game, CombatSettings(), name="Pombro")
    for _ in range(HOVER_MISSES + 2):
        assert ai._locate_by_hover([me, other]) is None
    assert len(hovers(game)) == HOVER_MISSES  # un seul cercle survolé par essai, plus rien ensuite


def test_spell_range_proves_who_i_am_and_the_cell_is_kept_as_sure():
    shown = load_frame("combat_portee_sort")
    me, other = two_circles(shown)
    game = FakeGame(shown, load_frame("combat_mon_tour"))
    ai = CombatAI(game, CombatSettings(), name="Walitcoin")
    ai._find_me = lambda frame, circles: None
    found, _, _ = ai._sides(shown, [other, me], RED)
    assert found is me and ai.me_sure and presses(game) == ["3", "escape"]


def test_failed_move_from_an_inherited_cell_is_checked_again():
    # 19/10 : case suivie fausse héritée du tour d'avant, deux déplacements « sans bouger », puis « rien d'utile à faire
    # d'ici » calculé depuis la mauvaise case : tour perdu. Après un déplacement raté, ma case se revérifie (portée).
    shown = load_frame("combat_portee_sort")
    me, other = two_circles(shown)
    game = FakeGame(shown, load_frame("combat_mon_tour"))
    ai = CombatAI(game, CombatSettings(), name="Walitcoin")
    ai._find_me = lambda frame, circles: None
    ai._me_cell, ai._me_sure = other.cell, True  # case « sûre » du tour d'avant… c'était celle d'un allié
    found, _, _ = ai._sides(shown, [other, me], RED)
    assert found is other and presses(game) == []  # un cercle dessus : rien ne permet encore d'en douter
    ai._me_sure, ai._doubt = False, True  # ce que fait _walk après « Mon personnage n'a pas bougé »
    found, _, _ = ai._sides(shown, [other, me], RED)
    assert found is me and ai.me_sure and presses(game) == ["3", "escape"]


def test_arrival_seen_on_the_target_cell_proves_my_cell(monkeypatch):
    frame = load_frame("combat_mon_tour")
    fighters = find_fighters(frame)
    me = next(f for f in fighters if f.team == RED)
    target = (me.cell[0] + 1, me.cell[1])
    there = Fighter(RED, *frame.grid.center(target), target)
    ai = CombatAI(FakeGame(frame), CombatSettings(), name="Walitcoin")
    ai._me_cell = me.cell  # suivie, pas prouvée
    monkeypatch.setattr(ai, "_wait_arrival", lambda me, best, steps: (True, frame))
    assert ai._walk(frame, me, target, fighters, {target}, "destination") == 1
    assert ai._me_cell == target and ai.me_sure
    # Arrêté ailleurs : case supposée, à revérifier, et pas sûre pour l'équipe.
    ai._me_sure = True
    monkeypatch.setattr(ai, "_wait_arrival", lambda me, best, steps: (False, frame))
    monkeypatch.setattr("bot.brain.combat.find_fighters", lambda f: [there] + [x for x in fighters if x != me])
    ai._walk(frame, me, (me.cell[0] + 3, me.cell[1]), fighters, set(), "destination", steps=3)
    assert not ai.me_sure and ai._doubt
