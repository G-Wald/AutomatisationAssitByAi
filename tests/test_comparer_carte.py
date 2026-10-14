import pytest

from conftest import load_frame
from tools.cartes_client import ALPHABET, case_vers_grille
from tools.comparer_carte import (REFERENCE, charger_reference, comparer_soleils, comparer_terrain, lire_terrain,
                                  reference_depuis_clair, voter)


def case(active=True, vue=True, deplacement=4, objet1=0) -> str:
    """Une case en clair (10 caractères), codée comme decoder_case la lit."""
    v = [0] * 10
    v[0] = (32 if active else 0) | (1 if vue else 0)
    v[2] = deplacement << 3
    v[4] = (objet1 >> 12) & 1
    v[5], v[6] = (objet1 >> 6) & 63, objet1 & 63
    return "".join(ALPHABET[x] for x in v)


def test_reference_classe_les_cases():
    clair = case() + case(deplacement=0, vue=False) + case(deplacement=0) + case(active=False) + case(deplacement=2, objet1=1030)
    ref = reference_depuis_clair(clair)
    g = [list(case_vers_grille(n)) for n in range(5)]
    assert ref["praticables"] == [g[0], g[4]]
    assert ref["blocs"] == [g[1]]
    assert ref["trous"] == [g[2]]
    assert ref["soleils"] == [g[4]]


def _ref(**ensembles):
    base = {"praticables": set(), "blocs": set(), "trous": set(), "soleils": set()}
    base.update(ensembles)
    return base


def test_soleils_a_une_case_pres_manques_et_faux():
    ref = _ref(soleils={(19, 1), (3, 31)})
    r = comparer_soleils(ref, [(18, 2), (10, 10)])
    assert r["trouves"] == {(19, 1): (18, 2)}
    assert r["manques"] == [(3, 31)]
    assert r["faux"] == [(10, 10)]


def test_terrain_accords_et_desaccords():
    ref = _ref(praticables={(0, 0), (1, 1)}, blocs={(2, 0)}, trous={(3, 1)})
    lu = {(0, 0): "sol", (1, 1): "bloc", (2, 0): "bloc", (3, 1): "bloc", (9, 9): "sol"}
    r = comparer_terrain(ref, lu)
    assert r["cases_lues"] == 4
    assert r["accord_marche"] == 3 / 4  # (1, 1) lue bloquée alors qu'on y marche
    assert r["accord_ligne_de_vue"] == 2 / 4  # (1, 1) et le trou (3, 1) lus comme coupant la vue
    assert r["tableau"]["hors carte -> sol"] == 1
    assert {tuple(d["case"]) for d in r["desaccords"]} == {(1, 1), (3, 1)}


def test_vote_majoritaire():
    assert voter([{(0, 0): "sol"}, {(0, 0): "bloc"}, {(0, 0): "sol", (1, 1): "trou"}]) == {(0, 0): "sol", (1, 1): "trou"}


@pytest.mark.skipif(not REFERENCE.exists(), reason="référence de -1,-19 absente (tools.comparer_carte --preparer)")
def test_terrain_lu_comme_les_cases_du_client():
    # 14/10 : 90 % d'accord sur la marche, 84 % sur la ligne de vue (432 cases), toutes les erreurs des cases lues
    # « bloc » à tort ; depuis la lecture du haut des cases : 99 % et 100 %, 400 cases lues (le reste sous l'interface).
    r = comparer_terrain(charger_reference(), lire_terrain(load_frame("combat_astrub_moins_1_moins_19")))
    assert r["cases_lues"] >= 390
    assert r["accord_marche"] >= 0.98 and r["accord_ligne_de_vue"] >= 0.98
