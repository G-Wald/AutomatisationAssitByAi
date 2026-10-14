import struct
from pathlib import Path

import pytest

from tools.cartes_client import DOSSIER
from tools.langue_client import LangueClient, liste, main


def sol(nom: str, donnees: bytes) -> bytes:
    corps = b"TCSO\x00\x04\x00\x00\x00\x00" + struct.pack(">H", len(nom)) + nom.encode() + b"\x00\x00\x00\x00" + donnees
    return b"\x00\xbf" + struct.pack(">I", len(corps)) + corps


def amf_chaine(texte: str) -> bytes:
    octets = texte.encode("utf-8")
    return struct.pack(">H", len(octets)) + octets


def amf_valeur(valeur) -> bytes:
    if isinstance(valeur, dict):
        contenu = b"".join(amf_chaine(str(cle)) + amf_valeur(v) for cle, v in valeur.items())
        return b"\x03" + contenu + b"\x00\x00\x09"
    if isinstance(valeur, list):  # tableau strict
        return b"\x0a" + struct.pack(">I", len(valeur)) + b"".join(amf_valeur(v) for v in valeur)
    if isinstance(valeur, bool):
        return b"\x01" + bytes([valeur])
    if isinstance(valeur, (int, float)):
        return b"\x00" + struct.pack(">d", valeur)
    if valeur is None:
        return b"\x05"
    return b"\x02" + amf_chaine(valeur)


def ecrire_table(dossier: Path, nom: str, table: dict) -> None:
    (dossier / "lang").mkdir(exist_ok=True)
    queue = b"\x00\x00\xff\x00\x00\x00\x10/localhost/Users"  # chemin binaire ajouté par Pepper Flash
    (dossier / "lang" / f"ANKXTRASO_0_{nom}.sol").write_bytes(
        sol(f"ANKXTRASO_0_{nom}", amf_chaine("_Data") + amf_valeur({nom: table}) + queue))


@pytest.fixture
def langue(tmp_path: Path) -> LangueClient:
    """Petite langue construite comme celle du client : deux donjons, une clé, ses ingrédients et ses monstres."""
    ecrire_table(tmp_path, "I", {
        "t": {"84": {"n": "Clefs", "t": 9}, "35": {"n": "Fleur", "t": 9}, "12": {"n": "Potion", "t": 6}},
        "u": {
            "8143": {"n": "Clef des Champs", "t": 84, "l": 10, "w": 1, "g": 13, "d": "Clef du Donjon des Champs.",
                     "drop": {"0": 48, "1": 79}},
            "1568": {"n": "Clef du Donjon des Bouftous", "t": 84, "l": 10, "w": 2,
                     "d": "Attention, elle sera détruite après utilisation."},
            "2659": {"n": "Pétale de Tournesol Sauvage", "t": 35, "l": 12, "w": 1, "drop": [48]},
            "306": {"n": "Fleur de Pissenlit Diabolique", "t": 35, "l": 5, "w": 1},
            "7": {"n": "Potion de Rappel", "t": 12, "l": 1, "w": 1},
        }})
    ecrire_table(tmp_path, "CR", {"8143": {"0": {"0": 2659, "1": 3}, "1": {"0": 306, "1": 3}}})
    ecrire_table(tmp_path, "M", {
        "48": {"n": "Tournesol Sauvage", "g1": {"l": 8, "lp": 80}, "g5": {"l": 15, "lp": 140}},
        "79": {"n": "Pissenlit Diabolique", "g1": {"l": 2, "lp": 25}, "g2": {"l": 9, "lp": 55}},
    })
    ecrire_table(tmp_path, "DU", {
        "2": {"n": "Donjon des champs", "lvl": 20, "sba": 306,
              "m": {"9768": {"n": "Salle 1", "x": 0, "y": 0, "z": 0, "i": 900},
                    "9769": {"n": "Salle 2", "x": 10, "y": 0, "z": 0}}},
        "9": {"n": "Donjon Ensablé", "lvl": 20, "sba": 336, "m": {}},
        "38": {"n": "Portail de Rushu", "lvl": 0, "sba": -1, "m": {}},
    })
    ecrire_table(tmp_path, "HI", {
        "338": {"n": "2", "c": 6, "m": 7453},
        "353": {"n": "9,38", "c": 6, "m": 10155},
        "5": {"n": "Temple Crâ", "c": 1, "m": 540},
    })
    ecrire_table(tmp_path, "MA", {
        "m": {"7453": {"x": 7, "y": -25, "sa": 98}, "9768": {"x": 7, "y": -25, "sa": 306, "d": 2, "n": "Salle 1"},
              "9769": {"x": 7, "y": -25, "sa": 306, "d": 2, "n": "Salle 2"},
              "10155": {"x": 13, "y": -28, "sa": 335}, "10157": {"x": 13, "y": -28, "sa": 336},
              "10156": {"x": 13, "y": -28, "sa": 336}},
        "sa": {"306": {"n": "Donjon des Champs", "a": 18}, "336": {"n": "Donjon Ensablé", "a": 18}},
        "a": {"18": {"n": "Astrub", "sua": 0}}, "sua": {"0": "Continent Amaknien"},
    })
    ecrire_table(tmp_path, "N", {
        "d": {"799": {"n": "Gardien du donjon des rats de Bonta", "a": {"0": 3}}, "63": {"n": "Abely Bobeule"}},
        "a": {"3": "Parler"},
    })
    ecrire_table(tmp_path, "D", {
        "q": {"3351": "Mais il te faut une clef voyons pour rentrer dans ce donjon !", "10": "Bonjour."},
        "a": {"2781": "Donner la clef et rentrer", "2929": "Sortir du donjon"},
    })
    return LangueClient(tmp_path)


def test_liste_remet_les_tableaux_amf_dans_l_ordre():
    assert liste({"1": "b", "0": "a", "10": "k", "2": "c"}) == ["a", "b", "c", "k"]
    assert liste([1, 2]) == [1, 2]
    assert liste(None) == []


def test_cle_recette_et_butin(langue: LangueClient):
    cles = {c.id: c for c in langue.cles()}

    assert set(cles) == {8143, 1568}  # la potion n'est pas une clé
    champs = cles[8143]
    assert champs.recette == [(2659, "Pétale de Tournesol Sauvage", 3), (306, "Fleur de Pissenlit Diabolique", 3)]
    assert champs.butin == [(48, "Tournesol Sauvage", (8, 15)), (79, "Pissenlit Diabolique", (2, 9))]
    assert (champs.poids, champs.niveau, champs.detruite) == (1, 10, False)
    assert cles[1568].detruite and cles[1568].recette == []
    assert langue.type_objet(8143) == (84, "Clefs", 9)  # même super-type que les ressources
    assert langue.butin(2659) == [(48, "Tournesol Sauvage", (8, 15))]  # « drop » en tableau strict


def test_fichier_du_dessin_d_un_objet(langue: LangueClient, tmp_path: Path):
    assert langue.icone(8143, tmp_path / "items") == tmp_path / "items" / "84" / "13.swf"  # clips/items/<type>/<gfx>
    assert langue.icone(7, tmp_path / "items") is None  # pas de gfx dans la langue
    assert langue.icone(424242) is None


def test_donjon_avec_plan_des_salles(langue: LangueClient):
    donjon = langue.donjon(2)

    assert (donjon.nom, donjon.niveau, donjon.nom_sous_zone) == ("Donjon des champs", 20, "Donjon des Champs")
    assert (donjon.entree, donjon.entree_coordonnees) == (7453, (7, -25))
    assert donjon.salles_source == "plan"
    assert [(s.carte, s.nom, s.plan, s.icone) for s in donjon.salles] == [
        (9768, "Salle 1", (0, 0, 0), 900), (9769, "Salle 2", (10, 0, 0), None)]
    assert {s.coordonnees for s in donjon.salles} == {(7, -25)}  # toutes aux coordonnées de l'entrée


def test_donjon_sans_plan_pris_dans_la_sous_zone(langue: LangueClient):
    ensable = langue.donjon(9)

    assert ensable.entree_coordonnees == (13, -28)  # indice « 9,38 » : deux donjons sur la même carte
    assert ensable.salles_source == "sous-zone"
    assert [s.carte for s in ensable.salles] == [10156, 10157]
    rushu = langue.donjon(38)
    assert (rushu.sous_zone, rushu.salles, rushu.salles_source, rushu.entree) == (None, [], "aucune", 10155)
    assert langue.donjon(999) is None
    assert [d.id for d in langue.donjons()] == [2, 9, 38]


def test_pnj_et_dialogues(langue: LangueClient):
    assert langue.pnj("gardien") == [(799, "Gardien du donjon des rats de Bonta", ["Parler"])]
    assert langue.pnj("abely") == [(63, "Abely Bobeule", [])]
    assert langue.dialogues("clef") == [
        ("question", 3351, "Mais il te faut une clef voyons pour rentrer dans ce donjon !"),
        ("réponse", 2781, "Donner la clef et rentrer")]
    assert langue.monstres("^tournesol") == [(48, "Tournesol Sauvage", [(8, 80), (15, 140)])]


def test_ligne_de_commande(langue: LangueClient, capsys):
    assert main(["--dossier", str(langue.dossier), "--donjon", "2", "--objet", "8143"]) == 0
    sortie = capsys.readouterr().out
    assert "entrée 7,-25 (carte 7453)" in sortie and "carte 9769 « Salle 2 »" in sortie
    assert "3 x Pétale de Tournesol Sauvage (2659)" in sortie
    assert main(["--dossier", str(langue.dossier / "absent"), "--cles"]) == 1


@pytest.mark.skipif(not (DOSSIER / "lang" / "ANKXTRASO_0_DU.sol").exists(),
                    reason="copie de la langue du client absente (data/client_retro/lang)")
def test_donnees_du_client_pour_les_donjons_d_astrub():
    langue = LangueClient()

    champs, bouftous, ensable = langue.donjon(2), langue.donjon(1), langue.donjon(9)
    assert (champs.entree_coordonnees, len(champs.salles)) == ((7, -25), 8)
    assert (bouftous.entree_coordonnees, len(bouftous.salles)) == ((2, -34), 11)
    assert (ensable.entree_coordonnees, len(ensable.salles)) == ((13, -28), 10)
    assert [(i, q) for i, _, q in langue.recette(8143)] == [(2659, 3), (306, 3)]  # Clef des Champs
    assert [(i, q) for i, _, q in langue.recette(8437)] == [(1782, 5), (8680, 3)]  # Clef du Donjon Ensablé
    assert [(i, q) for i, _, q in langue.recette(1568)] == [(384, 10), (2453, 1), (2451, 1)]  # Clef des Bouftous
    assert langue.type_objet(8143)[2] == langue.type_objet(384)[2] == 9  # clés et ressources : même onglet
    assert langue.icone(8143).parts[-2:] == ("84", "13.swf")  # tournesol
    assert langue.icone(1568).parts[-2:] == ("84", "226.swf")
