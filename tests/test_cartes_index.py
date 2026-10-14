import json
import struct
import zlib
from pathlib import Path

import pytest

from tools.cartes_client import DOSSIER, case_vers_grille
from tools.cartes_index import (
    CARTES,
    IndexCartes,
    case_bot,
    catalogue_objets_interactifs,
    construire,
    ecrire,
    placements,
    portes_de_maisons,
    soleils_conditionnels,
)
from tools.langue_client import LangueClient


# --- Fonctions pures -------------------------------------------------------------------------------------------------

def test_case_bot_seulement_pour_les_cartes_de_15_de_large():
    assert case_bot(173, 15) == list(case_vers_grille(173))
    assert case_bot(173, 11) is None


def test_placements_en_cases_du_bot():
    # p1 « ezeN » = cases 281 et 295 (2 caractères par case dans l'alphabet du client).
    resultat = placements({"p1": "ezeN"}, 15)

    assert resultat["p1"]["cases"] == [281, 295]
    assert resultat["p1"]["cases_bot"] == [list(case_vers_grille(281)), list(case_vers_grille(295))]
    assert placements({"p1": "ezeN"}, 11)["p1"]["cases_bot"] == [None, None]
    assert placements({}, 15) == {}


def test_soleils_conditionnels():
    carte = {"p": {"0": {"0": {"0": 1029, "1": 1030}, "1": 318, "2": "BE>2"}}}

    soleils = soleils_conditionnels(carte, 15)

    assert soleils[0]["case"] == 318 and soleils[0]["gfx"] == [1029, 1030]
    assert soleils[0]["soleil"] and soleils[0]["condition"] == "BE>2"
    assert soleils[0]["case_bot"] == list(case_vers_grille(318))
    assert soleils_conditionnels({}, 15) == []


def test_portes_de_maisons(tmp_path: Path):
    ecrire_langue(tmp_path, h={"h": {"700": {"n": "Petite maison"}}, "d": {"7331": {"c124": 700, "c300": None}}})

    portes = portes_de_maisons(LangueClient(tmp_path))

    assert portes[7331] == [{"case": 124, "maison": 700, "nom": "Petite maison"},
                            {"case": 300, "maison": None, "nom": None}]


def test_catalogue_objets_interactifs(tmp_path: Path):
    ecrire_langue(tmp_path, io={"g": {"7500": 1, "7003": 2, "7002": 3},
                                "d": {"1": {"n": "Frêne", "t": 1, "sk": {"0": 6}}, "2": {"n": "Scie", "t": 2},
                                      "3": {"n": "Zaap", "t": 3}}})

    catalogue = catalogue_objets_interactifs(LangueClient(tmp_path))

    assert catalogue[7500] == {"type": 1, "nom": "Frêne", "nature": "récolte", "competences": [6]}
    assert catalogue[7003]["nature"] == "atelier"
    assert catalogue[7002]["nature"] == "zaap"


# --- Construction et lecture -----------------------------------------------------------------------------------------

def dossier_synthetique(racine: Path) -> Path:
    """Copie minimale : deux SWF (dont un intérieur à la même coordonnée) et les tables de langue utiles."""
    (racine / "maps").mkdir()
    (racine / "maps" / "7331_0905131019X.swf").write_bytes(swf_compresse(
        {"id": 7331, "width": 15, "height": 17, "bOutdoor": True, "mapData": "0a" * 10 * 479,
         "backgroundNum": 71, "ambianceId": 1, "musicId": 115, "capabilities": 2,
         "canAggro": True, "canUseInventory": True, "canUseObject": False, "canChangeCharac": True}))
    (racine / "maps" / "7694_0905131019X.swf").write_bytes(swf_compresse(
        {"id": 7694, "width": 9, "height": 12, "bOutdoor": False, "mapData": "0a" * 10 * 200, "capabilities": 231}))
    ma = {"m": {"7331": {"x": -1, "y": -19, "sa": 95, "o": True, "p1": "ezeN", "p2": "cacb",
                         "p": {"0": {"0": {"0": 1030}, "1": 318, "2": "BE>2"}}},
                "7694": {"x": -1, "y": -19, "sa": 95}},
          "sa": {"95": {"n": "Cité d'Astrub", "a": 18, "tt": "ville"}},
          "a": {"18": {"n": "Astrub", "sua": 0}}, "sua": {"0": "Continent Amaknien"}}
    ecrire_langue(racine, ma=ma, h={"h": {"700": {"n": "Petite maison"}}, "d": {"7331": {"c124": 700}}},
                  io={"g": {"7500": 1}, "d": {"1": {"n": "Frêne", "t": 1}}})
    return racine


def test_construire(tmp_path: Path):
    index = construire(dossier_synthetique(tmp_path))

    carte = index["cartes"][7331]
    assert (carte.x, carte.y, carte.exterieur, carte.largeur, carte.cases) == (-1, -19, True, 15, 479)
    assert carte.nom_sous_zone == "Cité d'Astrub" and carte.nom_zone == "Astrub" and carte.super_zone == 0
    assert carte.musique == 115 and carte.capacites == 2 and carte.canUseObject is False
    assert index["coordonnees"]["-1,-19"] == [7331, 7694]  # plusieurs cartes à une coordonnée, id croissant
    assert 7331 in index["placements"] and index["objets_langue"][7331]["portes_de_maisons"][0]["nom"] == "Petite maison"
    assert index["objets_langue"][7331]["soleils_conditionnels"][0]["case"] == 318


def test_ecrire_et_lecture(tmp_path: Path):
    ecrire(dossier_synthetique(tmp_path))
    index = IndexCartes(tmp_path)

    carte = index.carte(7331)
    assert carte["x"] == -1 and carte["nom_sous_zone"] == "Cité d'Astrub"
    assert carte["placements"]["p1"]["cases"] == [281, 295]
    assert carte["objets_langue"]["portes_de_maisons"][0]["case"] == 124
    assert index.cartes_en(-1, -19) == [7331, 7694]
    assert index.carte_principale(-1, -19)["id"] == 7331
    assert index.carte(99999) is None
    fichiers = {p.name for p in (tmp_path / "cartes").glob("*.json")}
    assert fichiers == {"index.json", "coordonnees.json", "placements.json", "objets_langue.json",
                        "catalogue_objets_interactifs.json", "zones.json"}


# --- Sur la copie réelle (sautés si absente) -------------------------------------------------------------------------

@pytest.mark.skipif(not (DOSSIER / "lang" / "ANKXTRASO_0_MA.sol").exists(), reason="langue du client absente")
def test_index_de_la_copie_reelle():
    index = construire()

    assert len(index["cartes"]) > 9000
    carte = index["cartes"][7331]
    assert (carte.x, carte.y, carte.largeur) == (-1, -19, 15)
    # Les cases de placement restent dans la grille du bot (0..28, 0..32) sur les cartes de 15 de large.
    for contenu in index["placements"][7331].values():
        for ij in contenu["cases_bot"]:
            assert ij is None or (0 <= ij[0] <= 28 and 0 <= ij[1] <= 32)


@pytest.mark.skipif(not (CARTES / "index.json").exists(), reason="index non construit")
def test_lecture_de_la_copie_reelle():
    index = IndexCartes()

    assert index.carte(7414)["nom_sous_zone"] == "Cité d'Astrub"
    assert 7414 in index.cartes_en(4, -16)  # banque : plusieurs cartes à cette coordonnée
    assert len(index.cartes_en(4, -16)) > 1


# --- Fabriques de fichiers synthétiques (mêmes formats que test_cartes_client) ---------------------------------------

def doaction(variables: dict) -> bytes:
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

    for nom, valeur in variables.items():
        if isinstance(valeur, bool):
            element = b"\x05" + bytes([valeur])
        elif isinstance(valeur, int):
            element = b"\x07" + struct.pack("<i", valeur)
        else:
            element = constante(valeur)
        code += push(constante(nom), element) + b"\x1d"
    return code + b"\x00"


def swf_compresse(variables: dict) -> bytes:
    rectangle = bytes([0x08, 0x00])
    corps_action = doaction(variables)
    tag = struct.pack("<HI", (12 << 6) | 0x3F, len(corps_action)) + corps_action
    corps = rectangle + b"\x00\x0c\x01\x00" + tag + struct.pack("<H", 0)
    return b"CWS\x06" + struct.pack("<I", 8 + len(corps)) + zlib.compress(corps)


def _amf(valeur) -> bytes:
    if isinstance(valeur, bool):
        return b"\x01" + bytes([valeur])
    if isinstance(valeur, (int, float)):
        return b"\x00" + struct.pack(">d", valeur)
    if isinstance(valeur, str):
        octets = valeur.encode("utf-8")
        return b"\x02" + struct.pack(">H", len(octets)) + octets
    if valeur is None:
        return b"\x05"
    contenu = b""
    for cle, v in valeur.items():
        octets = str(cle).encode("utf-8")
        contenu += struct.pack(">H", len(octets)) + octets + _amf(v)
    return b"\x03" + contenu + b"\x00\x00\x09"


def sol(nom: str, donnees: dict) -> bytes:
    data = struct.pack(">H", len("_Data")) + b"_Data" + _amf(donnees)
    corps = b"TCSO\x00\x04\x00\x00\x00\x00" + struct.pack(">H", len(nom)) + nom.encode() + b"\x00\x00\x00\x00" + data
    return b"\x00\xbf" + struct.pack(">I", len(corps)) + corps


def ecrire_langue(racine: Path, ma: dict | None = None, h: dict | None = None, io: dict | None = None) -> None:
    (racine / "lang").mkdir(exist_ok=True)
    (racine / "lang" / "ANKXTRASO_0_MA.sol").write_bytes(sol("ANKXTRASO_0_MA", {"MA": ma or {
        "m": {}, "sa": {}, "a": {}, "sua": {}}}))
    (racine / "lang" / "ANKXTRASO_0_H.sol").write_bytes(sol("ANKXTRASO_0_H", {"H": h or {"h": {}, "d": {}}}))
    (racine / "lang" / "ANKXTRASO_0_IO.sol").write_bytes(sol("ANKXTRASO_0_IO", {"IO": io or {"g": {}, "d": {}}}))
