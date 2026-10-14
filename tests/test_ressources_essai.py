import json
import os
from datetime import datetime

import cv2
import numpy as np
import pytest

from bot.perception.frame import Frame
from tools import ressources_essai as re_
from tools.ressources_essai import (
    Detection,
    Gabarit,
    carte_de_capture,
    comparer,
    construire_gabarit,
    decouper,
    detecter,
    evenements_de_carte,
    indexer,
    planche,
    scores_par_case,
    totaliser,
)

LARGEUR, HAUTEUR = 1920, 1009  # captures réelles du jeu


def sprite(h: int = 60, w: int = 40) -> tuple[np.ndarray, np.ndarray]:
    """Petit « arbre » asymétrique (pour que le miroir diffère) et son masque."""
    image = np.zeros((h, w, 3), np.uint8)
    masque = np.zeros((h, w), np.uint8)
    cv2.rectangle(image, (16, 30), (23, 59), (60, 120, 180), -1)  # tronc
    cv2.rectangle(masque, (16, 30), (23, 59), 255, -1)
    cv2.rectangle(image, (18, 38), (19, 59), (30, 60, 90), -1)  # écorce : le pied seul n'est pas uni
    cv2.circle(image, (14, 18), 13, (40, 160, 60), -1)  # feuillage décalé à gauche
    cv2.circle(masque, (14, 18), 13, 255, -1)
    cv2.circle(image, (26, 10), 6, (20, 90, 200), -1)  # fruit
    cv2.circle(masque, (26, 10), 6, 255, -1)
    return image, masque


def fond(graine: int = 0) -> np.ndarray:
    rng = np.random.default_rng(graine)
    image = rng.integers(60, 140, (HAUTEUR, LARGEUR, 3), dtype=np.uint8)
    return cv2.GaussianBlur(image, (5, 5), 0)


def coller(image: np.ndarray, spr: np.ndarray, masque: np.ndarray, point: tuple[float, float], ancre: tuple[int, int],
           miroir: bool = False) -> None:
    if miroir:
        spr, masque = cv2.flip(spr, 1), cv2.flip(masque, 1)
        ancre = (spr.shape[1] - 1 - ancre[0], ancre[1])
    x, y = round(point[0]) - ancre[0], round(point[1]) - ancre[1]
    h, w = spr.shape[:2]
    y0, x0 = max(0, y), max(0, x)
    zone = image[y0:y + h, x0:x + w]
    s, m = spr[y0 - y:y0 - y + zone.shape[0], x0 - x:x0 - x + zone.shape[1]], masque[y0 - y:y0 - y + zone.shape[0], x0 - x:x0 - x + zone.shape[1]]
    zone[m > 0] = s[m > 0]


def gabarit_test() -> Gabarit:
    spr, masque = sprite()
    return Gabarit("arbre", "Frêne", "debout", spr, masque, (20, 55))


# --- Index des captures ----------------------------------------------------------------------------------------------

JOURNAL = """\
22:03:40.202 INFO    [Gela-[A13]] Pods de Gela-[A13] : 35 %
22:03:40.425 INFO    [Gela-[A13]] Nom de la carte illisible (décor clair derrière le texte ?) : carte 4,-16 d'après le chemin (capture : C:\\x\\logs\\s\\0002_Gela-_A13__nom_de_carte_illisible.png)
22:03:40.425 INFO    [Gela-[A13]] Trajet vers 1,-27 (Monde des Douze), sans combat ; départ : 4,-16 (carte au nom illisible)
22:03:52.077 INFO    [Gela-[A13]] Nouvelle carte : Astrub (Cité d'Astrub) 4,-18
22:03:52.148 INFO    [Brawli-[Gal]] Brawli-[Gal] a suivi le principal (Astrub (Cité d'Astrub) 4,-18)
22:03:57.307 INFO    [Gela-[A13]] Nouvelle carte : région inconnue (zone inconnue) 4,-19
"""


def test_evenements_de_carte_lit_arrivees_et_captures_illisibles():
    evenements, captures = evenements_de_carte(JOURNAL)
    assert captures == {"0002_Gela-_A13__nom_de_carte_illisible.png": "4,-16"}
    assert [e[2] for e in evenements] == ["4,-16", "4,-16", "4,-18", "4,-18", "4,-19"]
    assert evenements[3][1] == "Brawli-[Gal]"


def test_carte_de_capture_par_heure_et_par_personnage():
    evenements, captures = evenements_de_carte(JOURNAL)
    assert carte_de_capture("0002_Gela-_A13__nom_de_carte_illisible.png", "22:03:40.400", evenements, captures) == "4,-16"
    assert carte_de_capture("0003_Gela-_A13__changement_de_carte.jpg", "22:03:52.500", evenements, captures) == "4,-18"
    assert carte_de_capture("0004_Gela-_A13__changement_de_carte.jpg", "22:03:58.000", evenements, captures) == "4,-19"
    # Brawli n'a suivi que jusqu'en 4,-18 : sa capture reste sur sa dernière carte connue.
    assert carte_de_capture("0005_Brawli-_Gal__changement_de_carte.jpg", "22:03:58.000", evenements, captures) == "4,-18"
    assert carte_de_capture("0001_Gela-_A13__out_of_combat.jpg", "22:00:00.000", evenements, captures) is None


def test_indexer_associe_capture_carte_et_ressources_attendues(tmp_path, monkeypatch):
    monkeypatch.setattr(re_, "RACINE", tmp_path)
    session = tmp_path / "logs" / "20261011_220339"
    session.mkdir(parents=True)
    (session / "bot.log").write_text(JOURNAL, encoding="utf-8")
    image = session / "0003_Gela-_A13__changement_de_carte.jpg"
    image.write_bytes(b"x")
    quand = datetime.now().replace(hour=22, minute=3, second=53, microsecond=0).timestamp()
    os.utime(image, (quand, quand))
    monde = tmp_path / "monde.json"
    monde.write_text(json.dumps({"cartes": {"4,-18": {"zone": "Cité d'Astrub", "objets": [
        {"nom": "Frêne", "nombre": 2}, {"nom": "Frêne", "nombre": 1}, {"nom": "Blé", "nombre": 3}]}}}), encoding="utf-8")
    lignes = indexer(tmp_path / "logs", monde)
    assert lignes == [{"fichier": "logs/20261011_220339/0003_Gela-_A13__changement_de_carte.jpg", "carte": "4,-18",
                       "zone": "Cité d'Astrub", "attendu": {"Frêne": 3, "Blé": 3}}]


# --- Gabarits --------------------------------------------------------------------------------------------------------

def test_construire_gabarit_retrouve_le_sprite_malgre_les_fonds_et_un_intrus():
    spr, masque = sprite()
    r = 4
    exemplaires = []
    for k in range(5):
        rng = np.random.default_rng(k)
        ex = rng.integers(0, 255, (60 + 2 * r, 40 + 2 * r, 3), dtype=np.uint8)
        dx, dy = [(4, 4), (3, 5), (5, 4), (4, 2), (6, 6)][k]  # petits décalages à rattraper
        zone = ex[dy:dy + 60, dx:dx + 40]
        zone[masque > 0] = spr[masque > 0]
        if k == 1:
            ex[dy + 35:dy + 45, dx + 15:dx + 25] = (255, 0, 255)  # quelque chose passe devant le tronc
        exemplaires.append(ex)
    mediane, masque_calcule, scores = construire_gabarit(exemplaires, r)
    assert mediane.shape == (60, 40, 3)
    assert scores[0] == pytest.approx(1.0)
    # Le premier exemplaire est la référence (décalage 4, 4) : le sprite médian y est à sa place.
    ecart = np.abs(mediane.astype(int) - spr.astype(int)).max(axis=2)
    assert (ecart[masque > 0] <= 2).mean() > 0.97  # l'intrus a disparu
    assert (masque_calcule[masque > 0] > 0).mean() > 0.9  # le sprite est stable
    assert (masque_calcule[masque == 0] > 0).mean() < 0.1  # le fond aléatoire ne l'est pas


def test_decouper_refuse_une_boite_hors_image():
    image = np.zeros((100, 100, 3), np.uint8)
    assert decouper(image, (50, 50), (10, 20, 10, 5)).shape == (25, 20, 3)
    assert decouper(image, (5, 50), (10, 20, 10, 5)) is None


def test_retourne_garde_l_ancre_sur_la_case():
    gab = gabarit_test()
    miroir = gab.retourne()
    assert miroir.ancre == (19, 55)
    assert np.array_equal(miroir.image, cv2.flip(gab.image, 1))
    assert np.array_equal(miroir.retourne().image, gab.image)


# --- Repérage --------------------------------------------------------------------------------------------------------

def test_detecter_trouve_les_cases_y_compris_en_miroir_et_au_bord_haut():
    image = fond()
    frame = Frame.of(image)
    gab = gabarit_test()
    spr, masque = sprite()
    cases = [(8, 12), (15, 21), (22, 16)]
    for c in cases:
        coller(image, spr, masque, frame.grid.center(c), gab.ancre)
    coller(image, spr, masque, frame.grid.center((12, 26)), gab.ancre, miroir=True)
    coller(image, spr, masque, frame.grid.center((18, 2)), gab.ancre)  # coupé par le haut de la carte (y = 49)
    detections = detecter(Frame.of(image), [gab], seuil=0.8)
    trouvees = {d.case: d for d in detections}
    assert set(trouvees) == set(cases) | {(12, 26), (18, 2)}
    assert trouvees[(12, 26)].miroir and not trouvees[(8, 12)].miroir
    assert all(d.ressource == "Frêne" and d.etat == "debout" for d in detections)
    assert trouvees[(8, 12)].point == tuple(round(v) for v in frame.grid.center((8, 12)))


def test_scores_par_case_tolere_un_petit_decalage():
    image = fond(1)
    frame = Frame.of(image)
    gab = gabarit_test()
    spr, masque = sprite()
    x, y = frame.grid.center((10, 20))
    coller(image, spr, masque, (x + 2, y - 2), gab.ancre)
    scores = scores_par_case(Frame.of(image), gab)
    assert scores[(10, 20)][0] > 0.95
    assert max(s for c, (s, _) in scores.items() if c != (10, 20)) < 0.6


def test_detecter_garde_le_meilleur_gabarit_d_une_case():
    image = fond(2)
    frame = Frame.of(image)
    spr, masque = sprite()
    gab = gabarit_test()
    coller(image, spr, masque, frame.grid.center((10, 20)), gab.ancre)
    # Une « souche » : le bas de l'arbre seul, même ancre relative ; elle répond aussi sur la case de l'arbre.
    souche = Gabarit("souche", "Frêne", "coupe", spr[20:60], masque[20:60], (20, 35))
    assert scores_par_case(Frame.of(image), souche)[(10, 20)][0] > 0.9
    detections = detecter(Frame.of(image), [souche, gab], seuil=0.5)
    sur_la_case = [d for d in detections if d.case == (10, 20)]
    assert len(sur_la_case) == 1


def test_detecter_sans_rien_a_trouver():
    assert detecter(Frame.of(fond(3)), [gabarit_test()], seuil=0.8) == []


# --- Bilan -----------------------------------------------------------------------------------------------------------

def det(ressource: str, etat: str = "debout", score: float = 0.8, case=(1, 1)) -> Detection:
    return Detection(ressource, etat, "g", case, (0, 0), score, False)


def test_comparer_compte_debout_coupees_et_ecarts():
    detections = [det("Frêne"), det("Frêne", "coupe"), det("Frêne"), det("Châtaignier")]
    bilan = comparer(detections, {"Frêne": 2, "Noyer": 1}, ["Frêne", "Châtaignier"])
    assert bilan["Frêne"] == {"debout": 2, "coupees": 1, "trouvees": 3, "attendues": 2, "dans_l_attendu": 2,
                              "en_trop": 1, "manquantes": 0}
    assert bilan["Châtaignier"]["en_trop"] == 1 and bilan["Châtaignier"]["attendues"] == 0


def test_totaliser_applique_le_seuil():
    captures = [
        {"attendu": {"Frêne": 2}, "detections": [
            {"ressource": "Frêne", "etat": "debout", "case": [1, 1], "point": [0, 0], "score": 0.9, "miroir": False},
            {"ressource": "Frêne", "etat": "coupe", "case": [3, 3], "point": [90, 0], "score": 0.55, "miroir": False}]},
        {"attendu": {}, "detections": [
            {"ressource": "Frêne", "etat": "debout", "case": [1, 1], "point": [0, 0], "score": 0.58, "miroir": False}]},
    ]
    bas = totaliser(captures, ["Frêne"], 0.5)["Frêne"]
    haut = totaliser(captures, ["Frêne"], 0.6)["Frêne"]
    assert (bas["trouvees_dans_l_attendu"], bas["fausses_cartes_sans"], bas["dont_coupees"]) == (2, 1, 1)
    assert (haut["trouvees_dans_l_attendu"], haut["fausses_cartes_sans"], haut["captures_sans_avec_fausse"]) == (1, 0, 0)


def test_totaliser_seuil_plus_haut_pour_les_souches():
    captures = [{"attendu": {"Frêne": 2}, "detections": [
        {"ressource": "Frêne", "etat": "debout", "case": [1, 1], "point": [0, 0], "score": 0.62, "miroir": False},
        {"ressource": "Frêne", "etat": "coupe", "case": [3, 3], "point": [90, 0], "score": 0.65, "miroir": False}]}]
    assert totaliser(captures, ["Frêne"], 0.6)["Frêne"]["trouvees"] == 2
    bilan = totaliser(captures, ["Frêne"], 0.6, seuil_souche=0.7)["Frêne"]
    assert (bilan["trouvees"], bilan["dont_coupees"]) == (1, 0)


def test_partie_basse_garde_l_ancre_et_retrouve_un_arbre_coupe_par_le_haut():
    gab = gabarit_test()
    bas = re_.partie_basse(gab, hauteur=30)
    assert bas.image.shape[:2] == (35, 40) and bas.ancre == (20, 30)
    assert np.array_equal(bas.image, gab.image[25:])
    image = fond(5)
    frame = Frame.of(image)
    spr, masque = sprite()
    coller(image, spr, masque, frame.grid.center((11, 1)), gab.ancre)  # y = 24 : plus de la moitié de l'arbre hors carte
    assert (11, 1) not in {d.case for d in detecter(Frame.of(image), [gab], seuil=0.8)}
    assert (11, 1) in {d.case for d in detecter(Frame.of(image), [bas], seuil=0.8)}


def test_une_capture_par_carte():
    index = [{"fichier": "a/0001_x_changement_de_carte.jpg", "carte": "1,1"},
             {"fichier": "a/0002_x_sorties_touche_a.png", "carte": "2,2"},
             {"fichier": "a/0003_x_changement_de_carte.jpg", "carte": "1,1"},
             {"fichier": "a/0004_x_changement_de_carte.jpg", "carte": "2,2"},
             {"fichier": "a/0005_x_out_of_combat.jpg", "carte": None}]
    assert [l["fichier"][2:6] for l in re_.une_capture_par_carte(index)] == ["0001", "0004"]


def test_planche_une_vignette_par_detection():
    image = fond(4)
    dets = [Detection("Frêne", "debout", "g", (8, 12), (900, 300), 0.8, False),
            Detection("Châtaignier", "coupe", "g", (2, 2), (600, 30), 0.7, True)]
    p = planche(image, dets, par_ligne=12)
    assert p.shape[0] == round(240 * 0.75) + 18
    assert p.shape[1] == 12 * (round(120 * 0.75) + 2)
    assert planche(image, []) is None


# --- Langue du client ------------------------------------------------------------------------------------------------

TABLES = {
    "J": {"2": {"n": "Bûcheron"}, "28": {"n": "Paysan"}, "11": {"n": "Forgeur d'Epées"}},
    "SK": {"6": {"d": "Couper", "j": 2, "io": 1, "i": 303}, "45": {"d": "Faucher", "j": 28, "io": 38, "i": 289},
           "101": {"d": "Scier", "j": 2, "io": 2, "cl": {"0": 459}}, "9": {"d": "Forger", "j": 11, "io": 5, "i": 1}},
    "IO": {"g": {"7500": 1, "7511": 38}, "d": {"1": {"n": "Frêne", "t": 1}, "38": {"n": "Blé", "t": 1}, "2": {"n": "Scie", "t": 2}}},
    "I": {"t": {"15": {"n": "Ressource", "t": 9}, "19": {"n": "Hache", "t": 2}, "22": {"n": "Faux", "t": 2}},
          "u": {"303": {"n": "Bois de Frêne", "l": 1, "w": 5, "t": 38}, "289": {"n": "Blé", "l": 1, "w": 2, "t": 34},
                "454": {"n": "Hache de Bûcheron", "l": 1, "w": 10, "t": 19, "p": 150},
                "771": {"n": "Mangeuse de Frêne", "l": 8, "w": 10, "t": 19, "c": "CS>10"}}},
    "M": {"711": {"n": "Frêne Ensorcelé", "b": 64, "g1": {"l": 10, "lp": 30, "ap": 6, "mp": 2}},
          "31": {"n": "Larve Bleue", "b": 11, "g1": {"l": 1, "lp": 10, "ap": 4, "mp": 2}}},
    "MR": {"64": {"n": "Protecteurs des Arbres"}, "11": {"n": "Larves"}},
}


@pytest.fixture
def langue(monkeypatch):
    monkeypatch.setattr(re_, "_table", lambda dossier, nom: TABLES[nom])


def test_recoltes_garde_les_competences_de_recolte(langue):
    lignes = re_.recoltes()
    assert [(l["metier"], l["action"], l["ressource"], l["objet"], l["pods"], l["gfx"]) for l in lignes] == [
        ("Bûcheron", "Couper", "Frêne", "Bois de Frêne", 5, [7500]), ("Paysan", "Faucher", "Blé", "Blé", 2, [7511])]


def test_outils_de_niveau_1_sans_condition(langue):
    assert [(o["objet"], o["type"], o["super_type"]) for o in re_.outils()] == [("Hache de Bûcheron", "Hache", 2)]


def test_protecteurs_par_race(langue):
    assert re_.protecteurs() == [{"race": "Protecteurs des Arbres", "nom": "Frêne Ensorcelé", "id": 711,
                                  "grades": [(10, 30, 6, 2)]}]
