"""Outil de la mémoire des cartes (tools/memoire_cartes.py) : oubli de cartes et contrôle de cohérence."""

from tools.memoire_cartes import controle, oublier

M = "Monde des Douze"


def test_forgetting_a_map_removes_the_links_to_it():
    cartes = {f"{M} 2,-14": {"sorties": {"gauche": f"{M} 1,-14"}},
              f"{M} 1,-14": {"sorties": {"droite": f"{M} 2,-14", "haut": "bloquée"}}}
    assert oublier(cartes, [f"{M} 1,-14", f"{M} 9,9"]) == [f"{M} 1,-14"]
    assert cartes == {f"{M} 2,-14": {"sorties": {}}}


def test_check_lists_what_to_review():
    cartes = {
        f"{M} 0,0": {"sorties": {"droite": f"{M} 2,0", "bas": "bloquée"}},
        f"{M} 0,1": {"sorties": {"haut": f"{M} 0,0"}},
        f"{M} 5,5": {"sorties": {}, "bloquees_depuis": {"haut": ["gauche"]},
                     "soleils_depuis": {"haut": {"gauche": [1, 29]}}},
    }
    problemes = controle(cartes)
    assert any("0,0 droite" in p and "pas la voisine" in p for p in problemes)
    assert any("0,0 bas bloquée" in p for p in problemes)
    assert any("5,5 gauche inatteignable" in p for p in problemes)
    assert len(problemes) == 3
