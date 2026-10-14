import struct
import zlib
from pathlib import Path

import pytest

from tools.cartes_client import (
    CITE,
    CONTOUR,
    DOSSIER,
    Langue,
    case_vers_grille,
    comparer,
    comparer_reference,
    cote_du_bord,
    decoder_cases,
    detail_astrub,
    etat_map_data,
    fichiers_cartes,
    lire_carte_swf,
    lire_sol,
    nombre_de_cases,
    normaliser,
    variables_doaction,
    verdict_zone,
)


def doaction_de_carte(variables: dict) -> bytes:
    """DoAction construit comme ceux des cartes : pool de constantes, allowDomain, puis une affectation par variable."""
    noms = ["System", "security", "_parent", "_url", "allowDomain"] + list(variables)
    chaines = [v for v in variables.values() if isinstance(v, str)]
    pool = noms + chaines
    contenu = struct.pack("<H", len(pool)) + b"".join(p.encode("latin-1") + b"\x00" for p in pool)
    code = b"\x88" + struct.pack("<H", len(contenu)) + contenu

    def push(*elements: bytes) -> bytes:
        corps = b"".join(elements)
        return b"\x96" + struct.pack("<H", len(corps)) + corps

    def constante(texte: str) -> bytes:
        return b"\x08" + bytes([pool.index(texte)])

    # System.security.allowDomain(_parent._url)
    code += push(constante("_parent")) + b"\x1c" + push(constante("_url")) + b"\x4e"
    code += push(b"\x07" + struct.pack("<i", 1), constante("System")) + b"\x1c" + push(constante("security")) + b"\x4e"
    code += push(constante("allowDomain")) + b"\x52\x17"
    for nom, valeur in variables.items():
        if isinstance(valeur, bool):
            element = b"\x05" + bytes([valeur])
        elif isinstance(valeur, int):
            element = b"\x07" + struct.pack("<i", valeur)
        else:
            element = constante(valeur)
        code += push(constante(nom), element) + b"\x1d"
    return code + b"\x00"


def swf_compresse(doaction: bytes) -> bytes:
    rectangle = bytes([0x08, 0x00])  # nbits = 1 : rectangle tenant sur 2 octets
    tag = struct.pack("<HI", (12 << 6) | 0x3F, len(doaction)) + doaction
    corps = rectangle + b"\x00\x0c\x01\x00" + tag + struct.pack("<H", 0)
    return b"CWS\x06" + struct.pack("<I", 8 + len(corps)) + zlib.compress(corps)


def test_variables_d_une_carte(tmp_path: Path):
    map_data = "0a1b" * 10
    chemin = tmp_path / "7331_0905131019X.swf"
    chemin.write_bytes(swf_compresse(doaction_de_carte(
        {"id": 7331, "width": 15, "height": 17, "bOutdoor": True, "mapData": map_data})))

    carte = lire_carte_swf(chemin)

    assert (carte.id, carte.largeur, carte.hauteur, carte.exterieur) == (7331, 15, 17, True)
    assert carte.map_data == map_data
    assert carte.date == "0905131019" and carte.cle_attendue


def test_doaction_ignore_l_appel_allow_domain():
    variables = variables_doaction(doaction_de_carte({"id": 4, "canAggro": False}))

    assert variables == {"id": 4, "canAggro": False}


def test_etat_du_map_data():
    cases = nombre_de_cases(15, 17)

    assert cases == 479
    assert etat_map_data("Hhaaeaaaaa" * cases, cases) == "clair"
    assert etat_map_data("1f" * 10 * cases, cases) == "chiffré"
    assert etat_map_data("1f" * 10 * (cases + 7), cases) == "chiffré, longueur atypique"


def sol(nom: str, donnees: bytes) -> bytes:
    corps = b"TCSO\x00\x04\x00\x00\x00\x00" + struct.pack(">H", len(nom)) + nom.encode() + b"\x00\x00\x00\x00" + donnees
    return b"\x00\xbf" + struct.pack(">I", len(corps)) + corps


def amf_chaine(texte: str) -> bytes:
    octets = texte.encode("utf-8")
    return struct.pack(">H", len(octets)) + octets


def amf_objet(proprietes: dict) -> bytes:
    contenu = b""
    for cle, valeur in proprietes.items():
        contenu += amf_chaine(cle)
        if isinstance(valeur, dict):
            contenu += amf_objet(valeur)
        elif isinstance(valeur, bool):
            contenu += b"\x01" + bytes([valeur])
        elif isinstance(valeur, (int, float)):
            contenu += b"\x00" + struct.pack(">d", valeur)
        else:
            contenu += b"\x02" + amf_chaine(valeur)
    return b"\x03" + contenu + b"\x00\x00\x09"


def test_langue_des_cartes(tmp_path: Path):
    ma = {"m": {"7331": {"x": -1, "y": -19, "sa": 95, "o": True, "p1": "ezeN"}},
          "sa": {"95": {"n": "Cité d'Astrub", "a": 18}}, "a": {"18": {"n": "Astrub", "sua": 0}},
          "sua": {"0": "Continent Amaknien"}}
    (tmp_path / "lang").mkdir()
    # Pepper Flash ajoute un chemin binaire après l'objet : il doit être ignoré.
    queue = b"\x00\x00\xff\x00\x00\x00\x10/localhost/Users"
    (tmp_path / "lang" / "ANKXTRASO_0_MA.sol").write_bytes(
        sol("ANKXTRASO_0_MA", amf_chaine("_Data") + amf_objet({"MA": ma}) + queue))

    langue = Langue.charger(tmp_path)

    assert langue.cartes_en(-1, -19) == [7331]
    assert langue.sous_zone(7331) == "Cité d'Astrub (Astrub, sous-zone 95)"
    assert decoder_cases(langue.cartes[7331]["p1"]) == [4 * 64 + 25, 4 * 64 + 39]
    assert lire_sol(tmp_path / "lang" / "ANKXTRASO_0_MA.sol")["_Data"]["MA"]["m"]["7331"]["o"] is True


def test_case_vers_grille_du_bot():
    # Soleils cliqués par le bot (tools/import_monde.cell_to_grid) et soleils relevés en jeu.
    assert case_vers_grille(19) == (9, 1)
    assert case_vers_grille(173) == (27, 11)
    assert case_vers_grille(0) == (0, 0)
    assert case_vers_grille(478) == (28, 32)


@pytest.mark.skipif(not (DOSSIER / "maps").is_dir(), reason="copie du client absente (data/client_retro)")
def test_cartes_du_client_chiffrees():
    fichiers = fichiers_cartes()
    carte = lire_carte_swf(fichiers[7331])

    assert (carte.largeur, carte.hauteur, carte.exterieur) == (15, 17, True)
    assert carte.etat == "chiffré"


def test_noms_de_zone_compares():
    assert normaliser("Fôret de Kaliptus") == normaliser("Forêt de Kaliptus")
    assert normaliser("Le Château d’Amakna") == normaliser("Le château d'Amakna")
    cartes = [(3017, "Massif de Cania"), (6845, "Village Brigandins")]  # plus petit id d'abord : carte principale
    assert verdict_zone("Massif de Cania", cartes) == "identique"
    assert verdict_zone("Massif  de cania", cartes) == "graphie"
    assert verdict_zone("Village Brigandins", cartes) == "carte secondaire"
    assert verdict_zone("Village des Brigandins", cartes) == "discorde"
    assert verdict_zone("Cité d'Astrub", None) == "absente"
    assert verdict_zone(None, cartes) == "sans zone"


def test_cote_du_bord():
    # Soleils vus en jeu (data/carte.json) : -1,-13 bas (15, 31), -3,-18 gauche (1, 15)...
    assert [cote_du_bord(*c) for c in [(13, 1), (15, 31), (1, 15), (27, 15), (14, 16)]] == [
        "haut", "bas", "gauche", "droite", None]


def test_comparer_reference():
    reference = {
        "region": "Monde des Douze",
        "zones": [{"nom": CITE, "cartes": ["4,-14"]}, {"nom": "Burdure de Brâkmar", "cartes": ["-31,41"]}],
        "cartes": {"4,-14": {"zone": CITE}, "-31,41": {"zone": "Burdure de Brâkmar"}, "-33,41": {"zone": "Façade"},
                   "-27,8": {"zone": None}},
    }
    exterieures = {(4, -14): [(7414, CITE)], (-31, 41): [(1, "Bordure de Brâkmar")], (-27, 8): [(2, "Lande")],
                   (5, -14): [(3, CONTOUR)]}
    interieures = {(-33, 41): [(12141, "Bordure de Brâkmar")]}

    resultat = comparer_reference(reference, exterieures, interieures)

    assert resultat["chiffres"]["communes"] == 3
    assert resultat["reference_seulement"][0]["carte"] == "-33,41"
    assert resultat["reference_seulement"][0]["langue"] == "intérieurs seulement"
    assert [c["carte"] for c in resultat["langue_seulement"]] == ["5,-14"]
    assert resultat["sans_zone"][0]["carte"] == "-27,8"
    assert resultat["chiffres"]["verdicts_appartenances"]["identique"] == 1
    assert resultat["ecarts"][0]["sous_zones_langue"] == ["Bordure de Brâkmar"]
    assert resultat["sous_zones_langue"]["Bordure de Brâkmar"]["autres_zones"] == {"Burdure de Brâkmar": 1}


def test_passages_de_la_cite_notes_en_jeu():
    reference = {"region": "Monde des Douze", "cartes": {"0,-14": {"zone": CITE}, "0,-13": {"zone": CONTOUR},
                                                         "-1,-14": {"zone": CITE}, "-1,-13": {"zone": CONTOUR}}}
    exterieures = {(0, -14): [(1, CITE)], (0, -13): [(2, CONTOUR)], (-1, -14): [(3, CITE)], (-1, -13): [(4, CONTOUR)]}
    vues = {"Monde des Douze 0,-14": {"sorties": {"bas": "bloquée"}},
            "Monde des Douze 0,-13": {"sorties": {"haut": "Monde des Douze 0,-14"}},
            "Monde des Douze -1,-14": {"sorties": {"bas": "Monde des Douze -1,-13"}}}

    detail = detail_astrub(reference, exterieures, vues)

    limites = {(l["cite"], l["voisine"]): l for l in detail["limites_de_la_cite"]}
    assert limites[("0,-14", "0,-13")]["en_jeu"] == "contradictoire"
    assert limites[("-1,-14", "-1,-13")]["en_jeu"] == "passage vu"
    assert limites[("-1,-14", "-1,-13")]["porte_observee"]
    assert detail["chiffres"]["concordent"] == 4


@pytest.mark.skipif(not (DOSSIER / "lang" / "ANKXTRASO_0_MA.sol").exists(), reason="langue du client absente")
def test_reference_contre_la_langue_du_client():
    resultat = comparer()
    astrub = resultat["regions"]["Monde des Douze"]["astrub"]

    # Faits vus en jeu : 4,-14 est dans la cité, la rangée -13 dehors ; chaque porte relie la cité au contour.
    assert all(f["langue_d_accord"] and f["reference_d_accord"] for f in astrub["faits_observes"])
    assert all(p["langue"] == [CITE, CONTOUR] for p in astrub["portes"])
    assert astrub["chiffres"]["concordent"] == astrub["chiffres"]["cartes"]
    assert resultat["regions"]["Incarnam"]["par_identifiant"]["communs"] == 85
