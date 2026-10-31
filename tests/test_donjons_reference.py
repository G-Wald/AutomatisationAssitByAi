"""Référence des donjons (tools/donjons_reference.py, 30/10) : clé retrouvée malgré le pluriel et les accents."""

from tools.donjons_reference import _cle_par_mots

CLES = {1568: "Clef du Donjon des Bouftous", 8143: "Clef des Champs", 8545: "Clef du Donjon d'Incarnam"}


def test_the_key_is_found_from_the_dungeon_name():
    assert _cle_par_mots("Donjon Bouftou", CLES) == 1568  # « Bouftou » / « Bouftous »
    assert _cle_par_mots("Donjon d'Incarnam", CLES) == 8545
    assert _cle_par_mots("Donjon des Tofus", CLES) is None  # pas de clé dans la liste : rien plutôt qu'une mauvaise
