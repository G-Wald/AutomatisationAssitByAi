"""Références complétées avec les données en clair du client (tools/completer_references.py)."""

from tools.completer_references import completer


def test_maps_get_their_ids_known_suns_doors_and_placements():
    index = {"10": {"super_zone": 0, "exterieur": True, "largeur": 15, "nom_sous_zone": "Forêt"},
             "11": {"super_zone": 0, "exterieur": True, "largeur": 15, "nom_sous_zone": "Village des Zoths"},
             "12": {"super_zone": 0, "exterieur": False, "largeur": 15, "nom_sous_zone": "Forêt"},
             "99": {"super_zone": 3, "exterieur": True, "largeur": 15, "nom_sous_zone": "Prairie"}}
    coordonnees = {"1,0": [10, 11, 12, 99]}
    objets = {"11": {"soleils_conditionnels": [{"case_bot": [1, 15]}, {"case_bot": [13, 15]}],
                     "portes_de_maisons": [{"case": 0, "nom": "Petite maison"}]}}
    placements = {"11": {"p1": {"cases_bot": [[13, 21], None]}, "p2": {"cases_bot": [[19, 7]]}}}
    reference = {"source": {}, "cartes": {"1,0": {"sorties": {}, "zone": "Village des Zoths", "soleils_client": {"x": 1}}}}
    complete, bilan = completer(reference, 0, index, coordonnees, objets, placements)
    carte = complete["cartes"]["1,0"]
    assert carte["id"] == 11  # extérieure de la zone de la référence (couche)
    assert carte["autres_ids"] == [10, 12]  # l'autre super-zone (99) n'y est pas
    assert carte["soleils_client"] == {"gauche": [[1, 15]]}  # case au milieu de la carte : pas un soleil de sortie
    assert carte["portes"] and carte["portes"][0]["maison"] == "Petite maison"
    assert carte["placements"] == {"p1": [[13, 21]], "p2": [[19, 7]]}  # case sans position écartée
    assert reference["cartes"]["1,0"]["soleils_client"] == {"x": 1}  # la référence d'origine n'est pas modifiée
    assert bilan["id"] == 1 and bilan["sans_id"] == []
