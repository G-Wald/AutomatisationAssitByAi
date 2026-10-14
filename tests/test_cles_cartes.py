import hashlib
import json
import random
import struct
from pathlib import Path

import pytest

import tools.cles_cartes as cles
from tools.cartes_client import ALPHABET, DOSSIER, etat_map_data
from tools.cles_cartes import (
    Fonction,
    Instruction,
    analyser_code,
    checksum,
    chercher,
    chiffrer,
    copier,
    decalage,
    dechiffrer,
    decoder_case,
    inventaire,
    lineariser,
    nom_du_tableau_de_cles,
    predicat_opaque,
    preparer_cle,
    tracer,
    unescape,
)


# --- Algorithme ------------------------------------------------------------------------------------------------------

def test_preparer_cle():
    assert preparer_cle("4b6579") == "Key"
    assert preparer_cle("4B6579") == "Key"
    assert preparer_cle("253431" + "62") == "Ab"  # « %41b » : unescape après la lecture hexadécimale


def test_checksum_et_decalage():
    # K = 75, e = 101, y = 121 : 75 % 16 + 101 % 16 + 121 % 16 = 11 + 5 + 9 = 25 ; 25 % 16 = 9
    assert checksum("Key") == "9"
    assert decalage("Key") == 18


def test_dechiffrer_vecteur_a_la_main():
    # n = 0, décalage 18 : caractère (0 + 18) % 3 = 0 de la clé, « K » = 0x4B ; 0x4B XOR « a » (0x61) = 0x2A
    assert chiffrer("a", "Key") == "2A"
    assert dechiffrer("2a", "Key") == "a"
    assert dechiffrer("2A", "Key", decal=18) == "a"


def test_aller_retour_sur_un_map_data():
    hasard = random.Random(7)
    texte = "".join(hasard.choice(ALPHABET) for _ in range(10 * 479))
    cle = preparer_cle("".join(f"{hasard.randrange(33, 127):02x}" for _ in range(137)))

    donnees = chiffrer(texte, cle).lower()  # les cartes portent de l'hexadécimal en minuscules

    assert len(donnees) == 20 * 479 and etat_map_data(donnees, 479) == "chiffré"
    assert dechiffrer(donnees, cle) == texte
    assert etat_map_data(dechiffrer(donnees, cle), 479) == "clair"
    assert etat_map_data(dechiffrer(donnees, cle[1:] + cle[0]), 479) != "clair"  # mauvaise clé


def test_echappement_avant_chiffrement():
    assert unescape("%41%u00e9b") == "Aéb"
    assert dechiffrer(chiffrer("50%+é", "Key"), "Key") == "50%+é"


def test_decoder_case():
    # H = 33 : active (32) et ligne de vue (1) ; G = 32 : déplacement 4 ; q, g = 16, 6 : objet2 16 * 64 + 6 = 1030
    case = decoder_case("HaGaaaaaqg")

    assert case["active"] and case["ligne_de_vue"]
    assert case["deplacement"] == 4
    assert case["objet2"] == 1030 and case["objet1"] == 0 and case["sol"] == 0
    assert not decoder_case("aaaaaaaaaa")["active"]


# --- Bytecode AVM1 ---------------------------------------------------------------------------------------------------

def push(*valeurs) -> bytes:
    corps = b""
    for v in valeurs:
        if isinstance(v, bool):
            corps += b"\x05" + bytes([v])
        elif isinstance(v, int):
            corps += b"\x07" + struct.pack("<i", v)
        else:
            corps += b"\x00" + v.encode("latin-1") + b"\x00"
    return b"\x96" + struct.pack("<H", len(corps)) + corps


def test_saut_deguise_et_octets_parasites():
    # Push True ; If (toujours pris) par-dessus 3 octets parasites (faux ConstantPool) ; Push 'ok' ; Return
    code = push(True) + b"\x9d\x02\x00" + struct.pack("<h", 3) + b"\x88\xff\xff" + push("ok") + b"\x3e"

    instructions = tracer(code)

    assert 10 not in instructions  # les octets parasites ne sont pas décodés
    assert instructions[0].bruit and instructions[5].toujours is True
    assert lineariser(instructions, 0, len(code), [], 0, [], set()) == ["   Push 'ok'", "   Return"]


def test_predicats_opaques():
    def ins(op, donnees=b""):
        return Instruction(0, op, donnees, 0)

    assert predicat_opaque([ins(0x34)])[0] is True  # GetTime
    assert predicat_opaque([ins(0x34), ins(0x50)])[0] is True  # GetTime + 1
    assert predicat_opaque([ins(0x96, push(False)[3:]), ins(0x12)])[0] is True  # Push False ; Not
    assert predicat_opaque([ins(0x96, push("\x07")[3:]), ins(0x32)])[0] is True  # CharToAscii('\x07') = 7
    assert predicat_opaque([ins(0x96, push(0)[3:])])[0] is False
    assert predicat_opaque([ins(0x96, push("x", 1)[3:])])[0] is None  # deux valeurs : vrai test


def test_nom_du_tableau_de_cles():
    charge = Fonction(0, "charger", [], suite=[
        ("Push", [("r", 1), "\x1b\x1d\r"]), ("GetMember", []), ("Push", [("r", 2)]), ("ToNumber", []),
        ("Push", [("r", 4), 1, "dofus"]), ("GetVariable", []), ("Push", ["\x1a\x07\x01"]), ("CallMethod", []),
        ("SetMember", [])])

    assert nom_du_tableau_de_cles(charge, "\x1a\x07\x01") == "\x1b\x1d\r"
    assert nom_du_tableau_de_cles(charge, "autre") is None


# --- Inventaire, copie, recherche ------------------------------------------------------------------------------------

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
        elif isinstance(valeur, (int, float)):
            contenu += b"\x00" + struct.pack(">d", valeur)
        else:
            contenu += b"\x02" + amf_chaine(valeur)
    return b"\x03" + contenu + b"\x00\x00\x09"


def so(nom: str, donnees: dict) -> bytes:
    return sol(nom, amf_chaine("_Data") + amf_objet(donnees))


def faux_dossier_client(racine: Path) -> Path:
    appli = racine / "Dofus Retro"
    objets = appli / "Pepper Data" / "Shockwave Flash" / "WritableRoot" / "#SharedObjects" / "X" / "localhost"
    for dossier, nom, donnees in [("##1", "ANKOPTIONSSO", {"compte": "secret"}),
                                  ("##2", "moncompte_12_SHARED_OBJECT_SHORTCUTS", {"a": 1}),
                                  ("##3", "SHARED_OBJECT_DISPLAYSGela", {"display19": 3}),
                                  ("##4", "ANKXTRASO_0_MA", {"MA": {}})]:
        (objets / dossier).mkdir(parents=True)
        (objets / dossier / "00000001.sol").write_bytes(so(nom, donnees))
    (appli / "Cache").mkdir()
    (appli / "Cache" / "data_1").write_bytes(b'etag:"' + b"0123456789abcdef" * 2 + b'"')
    (appli / "GPUCache").mkdir()
    (appli / "GPUCache" / "data_0").write_bytes(b"\x00" * 8)
    (appli / "Cookies").write_bytes(b"secret")
    (appli / "config.json").write_text('{"sCurrentLanguage": "fr"}', encoding="utf-8")
    return appli


def empreinte(dossier: Path) -> dict:
    return {p.relative_to(dossier).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(dossier.rglob("*")) if p.is_file()}


def test_inventaire_ne_lit_pas_les_fichiers_d_identification(tmp_path: Path, monkeypatch):
    appli = faux_dossier_client(tmp_path)
    lus = []
    debut = cles._debut
    monkeypatch.setattr(cles, "_debut", lambda chemin, n=300: lus.append(chemin.name) or debut(chemin, n))

    entrees = {e.chemin.split("/")[-2] if e.chemin.endswith(".sol") else e.chemin: e for e in inventaire(appli)}

    assert "Cookies" not in lus and entrees["Cookies"].copie is None
    assert entrees["##1"].copie is None and "compte" in entrees["##1"].note
    assert entrees["##2"].copie is None and "moncompte" not in entrees["##2"].format + entrees["##2"].note
    assert entrees["##3"].copie == "sharedobjects/SHARED_OBJECT_DISPLAYSGela.sol"
    assert entrees["##4"].copie is None  # langue : copiée par tools.cartes_client
    assert entrees["Cache/data_1"].copie == "cache_http/data_1"
    assert entrees["GPUCache/data_0"].copie is None


def test_copie_sans_toucher_la_source(tmp_path: Path):
    appli = faux_dossier_client(tmp_path)
    installation = tmp_path / "retroclient"
    (installation / "modules").mkdir(parents=True)
    (installation / "loader.swf").write_bytes(b"CWS\x08")
    avant = empreinte(appli)

    copier(appli, installation, tmp_path / "stockage")

    assert empreinte(appli) == avant
    copies = empreinte(tmp_path / "stockage")
    assert "code/loader.swf" in copies and "electron/config.json" in copies
    assert not any("Cookies" in c or "OPTIONS" in c or "SHORTCUTS" in c for c in copies)
    assert "moncompte" not in (tmp_path / "stockage" / "inventaire.json").read_text(encoding="utf-8")


def test_recherche_de_cles(tmp_path: Path):
    (tmp_path / "lang").mkdir()
    (tmp_path / "lang" / "ANKXTRASO_0_MA.sol").write_bytes(so("ANKXTRASO_0_MA", {"MA": {"m": {"7331": {"x": -1}}}}))
    (tmp_path / "lang" / "ANKXTRASO_0_K.sol").write_bytes(so("ANKXTRASO_0_K", {"K": {"7414": {"k": "ab" * 20}}}))
    (tmp_path / "stockage" / "cache_http").mkdir(parents=True)
    (tmp_path / "stockage" / "cache_http" / "data_1").write_bytes(b'etag:"' + b"0123456789abcdef" * 2 + b'"')
    (tmp_path / "stockage" / "electron").mkdir()
    (tmp_path / "stockage" / "electron" / "journal").write_bytes(b"GDM|7331|0905131019|" + b"4b" * 20)

    nombre, trouvailles = chercher(tmp_path)

    assert nombre == 4
    candidats = {(t.fichier, t.genre) for t in trouvailles if t.explication is None}
    assert ("lang/ANKXTRASO_0_K.sol", "entrée à l'id d'une carte connue") in candidats
    assert ("lang/ANKXTRASO_0_K.sol", "chaîne hexadécimale (SharedObject)") in candidats
    assert ("stockage/electron/journal", "GDM") in candidats
    assert ("stockage/electron/journal", "hexadécimal ASCII") in candidats
    expliquees = {t.fichier: t.explication for t in trouvailles if t.explication}
    assert expliquees["stockage/cache_http/data_1"].startswith("ETag")
    assert expliquees["lang/ANKXTRASO_0_MA.sol"] == "table « MA » sans hexadécimal"


# --- Copies réelles (sautés si absentes) -----------------------------------------------------------------------------

LOADER = DOSSIER / "stockage" / "code" / "loader.swf"


@pytest.mark.skipif(not LOADER.exists(), reason="copie de loader.swf absente (tools.cles_cartes --copier)")
def test_dechiffrement_dans_le_bytecode_du_client():
    resultat = analyser_code(LOADER)

    assert set(resultat["fonctions"]) == {"prepareKey", "checksum", "cypherData", "decypherData"}
    assert len({f["bloc"] for f in resultat["fonctions"].values()}) == 1  # classe réseau, comme en 1.29
    assert resultat["gestionnaire_de_cartes"] is not None and resultat["tableau_de_cles"]
    assert "   Push 'X'" in resultat["chargement_de_carte"]
    assert resultat["blocs_sharedobject"] and resultat["sharedobject_et_cles"] == []


@pytest.mark.skipif(not (DOSSIER / "stockage" / "inventaire.json").exists(), reason="copie du stockage absente")
def test_aucune_cle_dans_le_stockage_copie():
    _, trouvailles = chercher()

    assert [t for t in trouvailles if t.explication is None] == []
    inventaire_copie = json.loads((DOSSIER / "stockage" / "inventaire.json").read_text(encoding="utf-8"))
    assert all("Cookies" not in (e["copie"] or "") for e in inventaire_copie)
