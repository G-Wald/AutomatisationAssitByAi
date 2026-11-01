"""Qui est moi parmi les cercles de ma couleur (mode équipe) : toujours sur une preuve, jamais au hasard (19/10)."""

import pytest

from bot.brain.combat import HOVER_MISSES, CombatAI, CombatSettings
from bot.brain.tactics import CombatSpell
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
    # Arrière-plan (20/10) : souris restée où le clic de déplacement l'a posée, Pesopepe arrivé dessous.
    assert same_name(read_hovered_name(load_frame("fiche_pesopepe_arriere_plan")), "Pesopepe")


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


def walk_setup(monkeypatch, before, after):
    """_walk sur combat_mon_tour : cercles vus avant le clic (`before`) et après (`after`), arrivée jamais vue."""
    frame = load_frame("combat_mon_tour")
    later = load_frame("combat_mon_tour")
    me = next(f for f in find_fighters(frame) if f.team == RED)
    cells = lambda cs: [Fighter(RED, *frame.grid.center(c), c) for c in cs]
    monkeypatch.setattr("bot.brain.combat.find_fighters", lambda f: cells(before if f is frame else after))
    ai = CombatAI(FakeGame(later), CombatSettings(), name="Walitcoin")
    monkeypatch.setattr(ai, "_wait_arrival", lambda me, best, steps, quick=False: (False, later))
    ai._my_turn = lambda frame, team: False  # pas de reclic
    return ai, frame, me, cells(before)


def test_arrival_seen_on_the_target_cell_proves_my_cell(monkeypatch):
    frame = load_frame("combat_mon_tour")
    fighters = find_fighters(frame)
    me = next(f for f in fighters if f.team == RED)
    target = (me.cell[0] + 1, me.cell[1])
    ai = CombatAI(FakeGame(frame), CombatSettings(), name="Walitcoin")
    ai._me_cell = me.cell  # suivie, pas prouvée
    monkeypatch.setattr(ai, "_wait_arrival", lambda me, best, steps, quick=False: (True, frame))
    assert ai._walk(frame, me, target, fighters, {target}, "destination") == 1
    assert ai._me_cell == target and ai.me_sure


def test_the_only_new_circle_after_my_move_is_me(monkeypatch):
    # 20/10 : 4 fins de tour « sans preuve » (arrivée pas vue, plus de PA pour se repérer par la portée). Personne
    # d'autre ne bouge pendant mon tour : le seul cercle de ma couleur apparu, à ma portée, c'est moi.
    ally = (3, 30)
    ai, frame, me, fighters = walk_setup(monkeypatch, before=[(10, 20), ally], after=[(11, 21), ally])
    me = fighters[0]
    ai._me_cell = me.cell
    assert ai._walk(frame, me, (12, 22), fighters, set(), "destination", steps=2) == 1  # arrêté en route
    assert ai._me_cell == (11, 21) and ai.me_sure and not ai._doubt
    # Arrivé, mais vu seulement après l'attente : arrivé quand même.
    ai, frame, me, fighters = walk_setup(monkeypatch, before=[(10, 20), ally], after=[(12, 22), ally])
    ai._me_cell = (10, 20)
    assert ai._walk(frame, fighters[0], (12, 22), fighters, set(), "destination", steps=2) == 2 and ai.me_sure


def test_no_circle_appeared_and_mine_gone_means_check_where_i_am(monkeypatch):
    ally = (3, 30)
    ai, frame, me, fighters = walk_setup(monkeypatch, before=[(10, 20), ally], after=[ally])  # le mien caché
    ai._me_cell, ai._me_sure = (10, 20), True
    ai._walk(frame, fighters[0], (12, 22), fighters, set(), "destination", steps=2)
    assert not ai.me_sure and ai._doubt


def test_nobody_moved_keeps_my_cell(monkeypatch):
    # 20/10, Vertdepomme taclé : « n'a pas bougé », case revérifiée sans PA pour le faire, tour fini « sans preuve ».
    # Mon cercle toujours là et aucun autre apparu : personne n'a bougé, ma case reste sûre.
    ally = (3, 30)
    ai, frame, me, fighters = walk_setup(monkeypatch, before=[(10, 20), ally], after=[(10, 20), ally])
    ai._me_cell, ai._me_sure = (10, 20), True
    assert ai._walk(frame, fighters[0], (12, 22), fighters, set(), "destination", steps=2) == 0
    assert ai.me_sure and not ai._doubt and ai._me_cell == (10, 20)


def test_an_invocation_appearing_while_i_did_not_move_is_not_me(monkeypatch):
    # Demande du 20/10 : le Sadida a des invocations de sa couleur (La Bloqueuse, La Folle). Mon cercle toujours sur ma
    # case : je n'ai pas bougé ; le cercle apparu à côté est une invocation, pas moi.
    ally = (3, 30)
    ai, frame, me, fighters = walk_setup(monkeypatch, before=[(10, 20), ally], after=[(10, 20), (11, 21), ally])
    ai._me_cell, ai._me_sure = (10, 20), True
    assert ai._walk(frame, fighters[0], (12, 22), fighters, set(), "destination", steps=2) == 0
    assert ai._me_cell == (10, 20) and ai.me_sure


def test_two_new_circles_after_my_move_prove_nothing(monkeypatch):
    # Parti, et deux cercles de ma couleur apparus (le mien et une invocation) : rien de sûr, case revérifiée.
    ally = (3, 30)
    ai, frame, me, fighters = walk_setup(monkeypatch, before=[(10, 20), ally], after=[(11, 21), (12, 20), ally])
    ai._me_cell, ai._me_sure = (10, 20), True
    ai._walk(frame, fighters[0], (12, 22), fighters, set(), "destination", steps=2)
    assert not ai.me_sure and ai._doubt


def test_a_circle_appearing_out_of_my_reach_is_not_me(monkeypatch):
    # Le cercle d'un allié, caché avant mon clic (texte de dégâts, timeline) et revu après : trop loin pour être moi.
    ai, frame, me, fighters = walk_setup(monkeypatch, before=[(10, 20)], after=[(3, 30)])
    ai._me_cell, ai._me_sure = (10, 20), True
    ai._walk(frame, fighters[0], (12, 22), fighters, set(), "destination", steps=2)
    assert not ai.me_sure and ai._doubt


def test_a_cell_proven_this_turn_holds_when_its_circle_is_hidden_for_a_moment():
    # 20/10 : arrivée vue en (10, 24), puis le cercle masqué par la souris restée dessus : 7 s de repérage (deux sorts
    # sélectionnés, deux survols) pour rien. Une case prouvée pendant ce tour vaut jusqu'à mon prochain geste.
    frame = load_frame("combat_mon_tour")
    me, other = two_circles(frame)
    game = FakeGame(frame)
    ai = CombatAI(game, CombatSettings(), name="Pombro")
    ai._find_me = lambda frame, circles: None
    hidden = (me.cell[0] + 1, me.cell[1] + 1)
    ai._me_cell, ai._me_sure, ai._proven_now = hidden, True, True
    found, _, _ = ai._sides(frame, [me, other], RED)
    assert found.cell == hidden and presses(game) == []


def test_no_hover_in_background():
    # 20/10 : en arrière-plan, deux survols en combat, aucune fiche (comme le 08/10) : plus essayé.
    frame = load_frame("combat_mon_tour")
    me, other = two_circles(frame)
    game = HoverGame(frame)
    game.background = True
    assert CombatAI(game, CombatSettings(), name="Pombro")._locate_by_hover([me, other]) is None
    assert hovers(game) == []


def test_spells_the_pa_left_cannot_pay_are_not_selected_to_locate_me():
    # 20/10, Vertdepomme : 2 PA après Flèche de Recul, tous les sorts grisés ; deux touches pour rien.
    frame = load_frame("combat_mon_tour")
    me, other = two_circles(frame)
    game = FakeGame(frame)
    ai = CombatAI(game, CombatSettings())
    ai._spells = [CombatSpell(2, "fleche_empoisonnee", "Flèche Empoisonnée", 4, (2, 5), damage=6)]
    ai._use.pa = 2
    assert ai._locate_by_range(frame, [me, other], [me, other]) is None and presses(game) == []


def test_range_center_on_no_known_circle_proves_nothing_and_does_not_crash(monkeypatch):
    # Demande de l'utilisateur (20/10) : le cercle au centre de la portée cherché sans valeur par défaut aurait levé
    # une erreur si ce centre n'était sur aucun cercle connu. Jamais une erreur : la portée ne prouve rien.
    from types import SimpleNamespace

    shown = load_frame("combat_portee_sort")
    me, other = two_circles(shown)
    game = FakeGame(shown)
    ai = CombatAI(game, CombatSettings())
    nowhere = (me.cell[0] + 7, me.cell[1] + 7)
    monkeypatch.setattr("bot.brain.combat.locate_caster", lambda *a, **k: SimpleNamespace(
        cell=nowhere, hidden=False, spell_range=(1, 8)))
    selected = []
    monkeypatch.setattr(ai, "_select_spell", lambda frame, spell: selected.append(spell) or shown)
    monkeypatch.setattr("bot.brain.combat.find_fighters", lambda frame: [me, other])
    assert ai._locate_by_range(shown, [me, other], [me, other]) is None
    assert selected  # la portée a bien été lue (et son centre écarté)
