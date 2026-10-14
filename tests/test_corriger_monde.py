"""Correction de la référence du Monde des Douze d'après la langue des cartes du client (tools/corriger_monde.py)."""

from tools.cartes_client import Langue
from tools.corriger_monde import corriger


def petit_monde() -> tuple[dict, Langue]:
    langue = Langue(
        cartes={100: {"x": 0, "y": 0, "sa": 1, "o": 1}, 101: {"x": 5, "y": 5, "sa": 2, "o": 1},
                102: {"x": 1, "y": 0, "sa": 3, "o": 1}, 103: {"x": 1, "y": 0, "sa": 4, "o": 1},
                104: {"x": 9, "y": 9, "sa": 3, "o": 0}, 105: {"x": 2, "y": 0, "sa": 3, "o": 1}},
        sous_zones={1: {"n": "Centre-ville", "a": 10}, 2: {"n": "Centre-ville", "a": 11}, 3: {"n": "Forêt", "a": 12},
                    4: {"n": "Village des Zoths", "a": 12}},
        zones={10: {"n": "Bonta", "sua": 0}, 11: {"n": "Brâkmar", "sua": 0}, 12: {"n": "Amakna", "sua": 0}},
        super_zones={0: "Continent Amaknien"},
        version=1384,
    )
    reference = {
        "region": "Monde des Douze", "source": {"site": "test"}, "regions": ["Amakna", "Bonta", "Village des Zoths"],
        "zones": [
            {"id": "z1", "nom": "Bonta", "region": "Bonta", "cartes": ["0,0"], "monstres": ["Bouftou"]},
            {"id": "z2", "nom": "Fôret", "region": "Amakna", "cartes": ["1,0", "2,0", "9,9"], "monstres": ["Tofu"]},
            {"id": "z3", "nom": "Village des Zoths", "region": "Village des Zoths", "cartes": ["1,0"],
             "monstres": ["Zoth"]},
        ],
        "monstres": {"liste": []},
        "lieux": [{"type": "donjon", "nom": "D", "carte": "2,0", "zone": "Fôret"}],
        "cartes": {"0,0": {"sorties": {}, "zone": "Bonta"},
                   "1,0": {"sorties": {}, "zone": "Fôret", "aussi": ["Village des Zoths"]},
                   "2,0": {"sorties": {}, "objets": [{"nom": "Frêne"}], "zone": "Fôret"},
                   "9,9": {"sorties": {}, "zone": "Fôret"}},
    }
    return reference, langue


def test_maps_become_the_outdoor_maps_of_the_game():
    reference, langue = petit_monde()
    corrigee, bilan = corriger(reference, langue)
    assert set(corrigee["cartes"]) == {"0,0", "1,0", "2,0", "5,5"}  # 9,9 : intérieur seulement ; 5,5 ajoutée
    assert [r["carte"] for r in bilan["retirees"]] == ["9,9"] and bilan["ajoutees"] == ["5,5 (Centre-ville (Brâkmar))"]
    assert corrigee["cartes"]["2,0"]["objets"] == [{"nom": "Frêne"}]  # ressources gardées
    assert reference["cartes"]["2,0"]["zone"] == "Fôret"  # la référence d'origine n'est pas modifiée


def test_zone_names_of_the_game_and_cities_told_apart():
    reference, langue = petit_monde()
    corrigee, bilan = corriger(reference, langue)
    assert corrigee["cartes"]["0,0"]["zone"] == "Centre-ville (Bonta)"
    assert corrigee["cartes"]["5,5"]["zone"] == "Centre-ville (Brâkmar)"
    assert corrigee["cartes"]["2,0"]["zone"] == "Forêt"  # « Fôret » : nom exact du jeu
    assert corrigee["lieux"][0]["zone"] == "Forêt"  # la zone d'un lieu suit sa carte


def test_layer_zone_keeps_its_own_monsters():
    # Village des Zoths : une couche sur la carte 1,0 de la forêt ; ni ses monstres dans la forêt, ni l'inverse.
    reference, langue = petit_monde()
    corrigee, _ = corriger(reference, langue)
    zones = {z["nom"]: z for z in corrigee["zones"]}
    assert zones["Village des Zoths"]["monstres"] == ["Zoth"] and zones["Village des Zoths"]["cartes"] == ["1,0"]
    assert zones["Forêt"]["monstres"] == ["Tofu"] and zones["Forêt"]["cartes"] == ["1,0", "2,0"]
    assert corrigee["cartes"]["1,0"] == {"sorties": {}, "zone": "Forêt", "aussi": ["Village des Zoths"]}
