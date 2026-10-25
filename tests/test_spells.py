"""Sorts lus dans la fenêtre « Tes sorts » : classe du personnage, sort de chaque ligne, rôle tactique."""

import logging

import cv2
import numpy as np
import pytest

from bot.brain.spellbook import (
    DAMAGE_EFFECTS,
    RANGE_BONUS,
    SpellBook,
    best_assignment,
    boost_of,
    stat_boost_of,
    deduce_role,
    guess_class,
    load_reference,
    match_class,
    mean_damage,
    rank_classes,
)
from bot.perception.frame import Frame
from bot.perception.spellbook import SpellEntry, read_spell_window
from conftest import FIXTURES, load_frame

ENUTROF = "fenetre_sorts_enutrof_03_10"  # Walitcoin, 03/10 : pris pour un Osamodas
CRA_WINDOWS = ["fenetre_sorts", "fenetre_sorts_28_09", "fenetre_sorts_cra_03_10"]
ENUTROF_ROWS = ["retraite_anticipee", "lancer_de_pieces", "sac_anime", "lancer_de_pelle", "pelle_fantomatique",
                "chance", "boite_de_pandore", "remblai", "cle_reductrice", "force_de_l_age"]
ATTACKS = {"degats", "degats_zone", "repousser", "retrait_pa", "poison"}
ICON = np.zeros((46, 46, 3), np.uint8)


class Window:
    title = "Walitcoin - Dofus Retro v1.49.5"


class TitledGame:
    """Seul le titre de la fenêtre sert à SpellBook.learn (nom du personnage, pour la classe imposée)."""

    window = Window()


def window_entries(name: str) -> list[SpellEntry]:
    """Fenêtre des sorts (découpée à 574 x 729) posée sur une capture hors combat, comme dans le jeu."""
    image = load_frame("hors_combat").image.copy()
    window = cv2.imread(str(FIXTURES / f"{name}.png"))
    image[40 : 40 + window.shape[0], 640 : 640 + window.shape[1]] = window
    return read_spell_window(Frame.of(image))


def book(name: str = ENUTROF, classes: dict | None = None) -> SpellBook:
    spells = SpellBook(TitledGame(), classes=classes or {})
    spells.learn(window_entries(name))
    return spells


def level_of(key: str, level: int = 1) -> dict:
    spell = next(s for s in load_reference()["sorts"].values() if s["cle"] == key)
    return spell["niveaux"][level - 1]


def test_enutrof_window_read_with_its_fives_and_ranges_from_zero():
    # « Niv. 5 » (Lancer de Pièces) et « 5 PA » (Pelle Fantomatique) : illisibles avant les modèles du 03/10 ;
    # « 12 PO », « 4 PO », « 1 PO » : 0 à 12, 0 à 4, 0 à 1 (lus 12 à 12... avant).
    read = [(e.level, e.pa, e.reach, e.learned) for e in window_entries(ENUTROF)]
    assert read == [(1, 4, (0, 0), True), (5, 2, (0, 12), True), (1, 3, (1, 1), True), (1, 4, (1, 4), True),
                    (1, 5, (0, 4), True), (1, 4, (0, 1), True), (1, 3, (0, 1), True), (1, 5, (2, 2), True),
                    (1, 1, (1, 5), True), (1, 5, (1, 4), True)]


def test_single_number_range_starts_at_zero():
    entry = SpellEntry(0, 2, (12, 12), True, ICON, level=5, low_written=False)
    assert entry.reach == (0, 12) and "portée 0 à 12" in entry.describe()
    assert SpellEntry(0, 3, (1, 1), True, ICON).reach == (1, 1)  # « 1-1 PO »


def test_enutrof_found_from_pa_range_and_level_alone():
    entries = window_entries(ENUTROF)
    reference = load_reference()
    assert guess_class(entries, reference, keys={}) == "enutrof"  # sans les icônes
    ranking = rank_classes(entries, reference, keys={})
    assert ranking[0].explained == 10 and not ranking[0].contradictions
    assert ranking[0].score - ranking[1].score >= 10  # nette avance
    osamodas = next(m for m in ranking if m.class_key == "osamodas")
    assert osamodas.score < ranking[0].score / 2
    # « Retraite anticipée » en tête (ordre de Solomonk décalé) ; Sac Animé et Pelle Animée (3 PA, 1 à 1) ou
    # Retraite anticipée et Cupidité (4 PA, 0 à 0) départagés par la place dans la liste.
    match = match_class(entries, reference, "enutrof", keys={})
    assert [match.spells[row]["cle"] for row in range(10)] == ENUTROF_ROWS


def test_enutrof_spells_known_with_their_roles():
    spells = book()
    assert spells.class_key == "enutrof"
    by_key = {s.key: s for s in spells.spells.values()}
    assert [s.key for s in spells.spells.values()] == ENUTROF_ROWS
    assert by_key["lancer_de_pieces"].level == 5 and by_key["lancer_de_pieces"].combat_spell(1).spell_range == (0, 12)
    assert all(not s.remark for s in spells.spells.values())  # PA et portée lus : ceux du niveau lu
    assert by_key["sac_anime"].role == "autre"  # invocation
    assert by_key["cle_reductrice"].role == "autre"  # -1 à la portée, sans dégâts
    spells.slots = {s.entry.row + 1: s.entry for s in spells.spells.values()}
    attacks = {s.spell_id for s in spells.combat_spells()}
    # Chance (+11 à 20 chance sur soi) : un bonus de dégâts (bonus de caractéristique, 25/10).
    assert attacks == {"lancer_de_pieces", "lancer_de_pelle", "pelle_fantomatique", "remblai", "force_de_l_age",
                       "chance"}


@pytest.mark.parametrize("name", CRA_WINDOWS)
def test_cra_windows_stay_cra(name):
    entries = window_entries(name)
    assert guess_class(entries, load_reference(), keys={}) == "cra"
    spells = book(name)
    assert spells.class_key == "cra"
    assert [s.key for s in spells.spells.values()][:3] == ["fleche_de_recul", "fleche_empoisonnee", "fleche_magique"]


def test_forced_class_has_priority(caplog):
    assert SpellBook(TitledGame(), classes={}).name == "Walitcoin"  # d'après le titre de la fenêtre
    with caplog.at_level(logging.INFO):
        forced = book(ENUTROF, classes={"walitcoin": "Crâ"})  # nom et classe : majuscules et accents indifférents
    assert forced.class_key == "cra"  # imposée, même contre la fenêtre
    assert "imposée" in caplog.text and "plutôt la classe Enutrof" in caplog.text
    assert book(ENUTROF, classes={"Walitcoin": "enutrof"}).class_key == "enutrof"
    assert book(ENUTROF, classes={"Autreperso": "cra"}).class_key == "enutrof"  # pas pour ce personnage
    assert book(ENUTROF, classes={"Walitcoin": "boulanger"}).class_key == "enutrof"  # classe inconnue : ignorée


def test_uncertain_class_left_unknown(caplog):
    reference = load_reference()
    one_row = [SpellEntry(0, 4, (1, 3), True, ICON, level=1)]  # Flèche de Recul... ou bien d'autres sorts
    assert guess_class(one_row, reference, keys={}) is None
    assert guess_class([], reference, keys={}) is None
    spells = SpellBook(TitledGame(), classes={})
    with caplog.at_level(logging.WARNING):
        spells.learn(one_row)
    assert spells.class_key is None and not spells.spells[0].data  # sort inconnu : la tactique ne s'en sert pas
    assert "Classe incertaine" in caplog.text and "classes: {Walitcoin:" in caplog.text


def test_no_attack_without_damage_in_any_class():
    """Sur les 12 classes, à tous les niveaux : un sort sans dégâts (invocation, soin, bonus, malus) n'est jamais une
    attaque ; un bonus de portée lancé sur soi est « boost_portee », un simple bonus de dégâts « boost_degats »."""
    for spell in load_reference()["sorts"].values():
        for level in spell["niveaux"]:
            role = deduce_role(level)
            ids = {e["id"] for e in level["effets"]}
            if role in ATTACKS:
                assert ids & DAMAGE_EFFECTS and mean_damage(level["effets"]) > 0, spell["cle"]
            elif not ids & DAMAGE_EFFECTS:
                expected = "boost_portee" if RANGE_BONUS in ids and tuple(level["portee"]) == (0, 0) else                     "boost_degats" if boost_of(level) is not None or stat_boost_of(level) is not None else "autre"
                assert role == expected, spell["cle"]


def test_malus_spells_with_little_damage_are_not_attacks():
    assert all(deduce_role(level_of("cle_reductrice", n)) == "autre" for n in range(1, 7))
    assert deduce_role(level_of("sac_anime")) == "autre"
    assert deduce_role(level_of("retraite_anticipee")) == "autre"
    assert deduce_role(level_of("aveuglement")) == "autre"  # 1 dégât et -1 à -2 PA, pour 3 PA
    assert deduce_role(level_of("aveuglement", 5)) == "retrait_pa"  # 3 à 7 dégâts et -2 PA
    assert deduce_role(level_of("intimidation")) == "autre"  # 1 à 2 dégâts et 1 case de recul, pour 4 PA
    assert deduce_role(level_of("force_de_l_age")) == "degats"  # 7 à 11 dégâts et vol d'1 PM
    assert deduce_role(level_of("lancer_de_pieces")) == "degats"  # peu de dégâts, mais rien d'autre
    assert deduce_role(level_of("fleche_glacee")) == "retrait_pa"


def test_best_assignment_gives_each_row_a_different_column():
    assert best_assignment([[5, 4, 0], [5, 0, 0]]) == [1, 0]  # la 2e ligne n'a que la 1re colonne
    assert best_assignment([[1, 2], [3, 1]]) == [1, 0]
    assert best_assignment([]) == []
