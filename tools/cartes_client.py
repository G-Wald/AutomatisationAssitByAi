"""Lecture des fichiers de cartes du client Dofus Retro 1.49 (sur une copie, jamais dans l'installation).

    .venv/Scripts/python -m tools.cartes_client --copier          # copie l'installation vers data/client_retro/
    .venv/Scripts/python -m tools.cartes_client --stats           # bilan : combien de cartes ont un mapData chiffré
    .venv/Scripts/python -m tools.cartes_client --carte -1,-19    # résumé des cartes qui portent ces coordonnées
    .venv/Scripts/python -m tools.cartes_client --id 7331         # résumé d'une carte par son identifiant
    .venv/Scripts/python -m tools.cartes_client --comparer        # langue contre config/monde (lecture seule) :
                                                                   # écrit data/client_retro/comparaison.json

Ce qu'on a trouvé (détails et validation : docs/rapports/cartes_client.md) :
- cartes : retroclient/data/maps/<id>_<date>X.swf, SWF compressé (« CWS », zlib après 8 octets) dont l'unique
  DoAction affecte id, width, height, backgroundNum, ambianceId, musicId, bOutdoor, capabilities, mapData, canAggro...
- mapData est CHIFFRÉ sur 100 % des cartes : texte hexadécimal, 2 caractères par octet, 10 octets par case. Le
  client le déchiffre avec une clé que le serveur envoie à l'entrée sur la carte (le « X » du nom de fichier signale
  cette clé). Elle n'est pas dans le client : cases, praticabilité, calques et soleils ne sont pas lisibles ici.
- langue « maps » : absente de l'installation. Le client la télécharge et la garde dans ses SharedObjects Flash
  (AMF0) sous %APPDATA%/Dofus Retro/Pepper Data/... ; ANKXTRASO_0_MA.sol donne, en clair, pour chaque carte :
  x, y, sous-zone (sa), extérieur (o), cases de placement de combat (p1, p2) et quelques soleils conditionnels (p).

Rien n'est écrit hors de data/client_retro/. Seule la bibliothèque standard est utilisée.
"""

import argparse
import json
import os
import re
import shutil
import struct
import sys
import unicodedata
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
DOSSIER = RACINE / "data" / "client_retro"
INSTALLATION = Path(os.environ.get("LOCALAPPDATA", "")) / "Ankama" / "Retro" / "resources" / "app" / "retroclient"
SHARED_OBJECTS = (Path(os.environ.get("APPDATA", "")) / "Dofus Retro" / "Pepper Data" / "Shockwave Flash"
                  / "WritableRoot" / "#SharedObjects")

# Alphabet « base 64 » du client 1.29 : mapData en clair, cases de placement p1/p2 (2 caractères par case).
ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
OCTETS_PAR_CASE = 10
NOM_FICHIER_CARTE = re.compile(r"^(\d+)_(\d+)(X?)\.swf$")
HEXA = re.compile(r"^[0-9a-f]*$")


# --- SWF -----------------------------------------------------------------------------------------------------------

def swf_tags(contenu: bytes) -> list[tuple[int, bytes]]:
    """Tags (code, données) d'un SWF, compressé (CWS) ou non (FWS)."""
    signature = contenu[:3]
    if signature == b"CWS":
        corps = zlib.decompress(contenu[8:])
    elif signature == b"FWS":
        corps = contenu[8:]
    else:
        raise ValueError(f"signature SWF inconnue : {signature!r}")
    nbits = corps[0] >> 3
    pos = (5 + 4 * nbits + 7) // 8 + 4  # rectangle de la scène, puis cadence et nombre d'images
    tags = []
    while pos + 2 <= len(corps):
        entete = struct.unpack_from("<H", corps, pos)[0]
        pos += 2
        code, longueur = entete >> 6, entete & 0x3F
        if longueur == 0x3F:
            longueur = struct.unpack_from("<I", corps, pos)[0]
            pos += 4
        tags.append((code, corps[pos:pos + longueur]))
        pos += longueur
        if code == 0:
            break
    return tags


def _valeurs_push(donnees: bytes, pool: list[str]) -> list:
    valeurs, q = [], 0
    while q < len(donnees):
        type_ = donnees[q]
        q += 1
        if type_ == 0:  # chaîne
            fin = donnees.index(b"\x00", q)
            valeurs.append(donnees[q:fin].decode("latin-1"))
            q = fin + 1
        elif type_ == 1:  # flottant 32 bits
            valeurs.append(struct.unpack_from("<f", donnees, q)[0])
            q += 4
        elif type_ in (2, 3):  # null, undefined
            valeurs.append(None)
        elif type_ == 4:  # registre
            valeurs.append(None)
            q += 1
        elif type_ == 5:  # booléen
            valeurs.append(bool(donnees[q]))
            q += 1
        elif type_ == 6:  # double, moitiés de 32 bits inversées
            valeurs.append(struct.unpack("<d", donnees[q + 4:q + 8] + donnees[q:q + 4])[0])
            q += 8
        elif type_ == 7:  # entier 32 bits
            valeurs.append(struct.unpack_from("<i", donnees, q)[0])
            q += 4
        elif type_ == 8:  # constante (index 8 bits)
            valeurs.append(pool[donnees[q]])
            q += 1
        elif type_ == 9:  # constante (index 16 bits)
            valeurs.append(pool[struct.unpack_from("<H", donnees, q)[0]])
            q += 2
        else:
            raise ValueError(f"type de Push inconnu : {type_}")
    return valeurs


def variables_doaction(code: bytes) -> dict:
    """Variables affectées à la racine par un DoAction (mini-interpréteur AVM1).

    Les cartes ne font que : ConstantPool, Push, SetVariable, et un appel System.security.allowDomain(_parent._url)."""
    pool: list[str] = []
    pile: list = []
    variables: dict = {}
    pos = 0
    while pos < len(code):
        op = code[pos]
        pos += 1
        if op == 0:
            break
        donnees = b""
        if op >= 0x80:
            longueur = struct.unpack_from("<H", code, pos)[0]
            donnees = code[pos + 2:pos + 2 + longueur]
            pos += 2 + longueur
        if op == 0x88:  # ConstantPool
            nombre = struct.unpack_from("<H", donnees, 0)[0]
            pool = [p.decode("latin-1") for p in donnees[2:].split(b"\x00")[:nombre]]
        elif op == 0x96:  # Push
            pile.extend(_valeurs_push(donnees, pool))
        elif op == 0x1D:  # SetVariable
            valeur, nom = pile.pop(), pile.pop()
            variables[nom] = valeur
        elif op == 0x17:  # Pop
            if pile:
                pile.pop()
        elif op == 0x1C:  # GetVariable
            pile.pop()
            pile.append(None)
        elif op == 0x4E:  # GetMember
            pile.pop()
            pile.pop()
            pile.append(None)
        elif op == 0x52:  # CallMethod
            pile.pop()
            pile.pop()
            nb_args = pile.pop()
            for _ in range(int(nb_args or 0)):
                pile.pop()
            pile.append(None)
    return variables


# --- Cartes --------------------------------------------------------------------------------------------------------

def nombre_de_cases(largeur: int, hauteur: int) -> int:
    """Lignes de `largeur` cases en quinconce avec des lignes de `largeur - 1` : 479 cases pour 15 x 17."""
    return largeur * hauteur + (largeur - 1) * (hauteur - 1)


def etat_map_data(map_data: str, cases: int) -> str:
    """« clair » (10 caractères de l'alphabet par case), « chiffré » (hexadécimal, 20 caractères par case),
    « chiffré, longueur atypique » ou « inconnu »."""
    if len(map_data) == OCTETS_PAR_CASE * cases and all(c in ALPHABET for c in map_data):
        return "clair"
    if HEXA.match(map_data) and len(map_data) % 2 == 0:
        return "chiffré" if len(map_data) == 2 * OCTETS_PAR_CASE * cases else "chiffré, longueur atypique"
    return "inconnu"


@dataclass
class CarteSwf:
    id: int
    date: str
    cle_attendue: bool  # « X » à la fin du nom de fichier
    largeur: int
    hauteur: int
    exterieur: bool | None
    map_data: str
    variables: dict = field(repr=False)

    @property
    def cases(self) -> int:
        return nombre_de_cases(self.largeur, self.hauteur)

    @property
    def etat(self) -> str:
        return etat_map_data(self.map_data, self.cases)


def lire_carte_swf(chemin: Path) -> CarteSwf:
    nom = NOM_FICHIER_CARTE.match(chemin.name)
    variables: dict = {}
    for code, donnees in swf_tags(chemin.read_bytes()):
        if code == 12:  # DoAction
            variables.update(variables_doaction(donnees))
    return CarteSwf(
        id=int(variables.get("id", nom.group(1) if nom else -1)),
        date=nom.group(2) if nom else "",
        cle_attendue=bool(nom and nom.group(3)),
        largeur=int(variables.get("width", 0)),
        hauteur=int(variables.get("height", 0)),
        exterieur=variables.get("bOutdoor"),
        map_data=str(variables.get("mapData", "")),
        variables=variables,
    )


def fichiers_cartes(dossier: Path = DOSSIER) -> dict[int, Path]:
    fichiers = {}
    for chemin in sorted((dossier / "maps").glob("*.swf")):
        nom = NOM_FICHIER_CARTE.match(chemin.name)
        if nom:
            fichiers[int(nom.group(1))] = chemin
    return fichiers


# --- SharedObjects (AMF0) -------------------------------------------------------------------------------------------

class _LecteurAmf0:
    def __init__(self, octets: bytes, pos: int):
        self.octets, self.pos, self.references = octets, pos, []

    def _u16(self) -> int:
        valeur = struct.unpack_from(">H", self.octets, self.pos)[0]
        self.pos += 2
        return valeur

    def _u32(self) -> int:
        valeur = struct.unpack_from(">I", self.octets, self.pos)[0]
        self.pos += 4
        return valeur

    def chaine(self) -> str:
        longueur = self._u16()
        valeur = self.octets[self.pos:self.pos + longueur].decode("utf-8", "replace")
        self.pos += longueur
        return valeur

    def _proprietes(self, objet: dict) -> dict:
        while True:
            cle = self.chaine()
            if cle == "" and self.octets[self.pos] == 9:  # fin d'objet
                self.pos += 1
                return objet
            objet[cle] = self.valeur()

    def valeur(self):
        type_ = self.octets[self.pos]
        self.pos += 1
        if type_ == 0:
            nombre = struct.unpack_from(">d", self.octets, self.pos)[0]
            self.pos += 8
            return int(nombre) if nombre.is_integer() else nombre
        if type_ == 1:
            self.pos += 1
            return bool(self.octets[self.pos - 1])
        if type_ == 2:
            return self.chaine()
        if type_ in (3, 8, 16):  # objet, tableau associatif, objet typé
            if type_ == 8:
                self._u32()
            elif type_ == 16:
                self.chaine()
            objet: dict = {}
            self.references.append(objet)
            return self._proprietes(objet)
        if type_ in (5, 6):  # null, undefined
            return None
        if type_ == 7:
            return self.references[self._u16()]
        if type_ == 10:  # tableau strict
            liste: list = []
            self.references.append(liste)
            for _ in range(self._u32()):
                liste.append(self.valeur())
            return liste
        if type_ == 11:  # date
            self.pos += 10
            return None
        if type_ == 12:  # chaîne longue
            longueur = self._u32()
            valeur = self.octets[self.pos:self.pos + longueur].decode("utf-8", "replace")
            self.pos += longueur
            return valeur
        raise ValueError(f"type AMF0 inconnu {type_} à l'octet {self.pos - 1}")


def nom_shared_object(octets: bytes) -> str | None:
    if octets[6:10] != b"TCSO":
        return None
    longueur = struct.unpack_from(">H", octets, 16)[0]
    return octets[18:18 + longueur].decode("latin-1")


def lire_sol(chemin: Path) -> dict:
    """Première entrée (« _Data ») d'un SharedObject Flash AMF0 : {"_Data": {...}}.

    Pepper Flash ajoute après l'objet un chemin binaire qu'on ignore."""
    octets = chemin.read_bytes()
    nom = nom_shared_object(octets)
    if nom is None:
        raise ValueError(f"{chemin} n'est pas un SharedObject")
    lecteur = _LecteurAmf0(octets, 18 + len(nom.encode("latin-1")) + 4)
    cle = lecteur.chaine()
    return {cle: lecteur.valeur()}


@dataclass
class Langue:
    cartes: dict[int, dict]
    sous_zones: dict[int, dict]
    zones: dict[int, dict]
    super_zones: dict[int, str]
    version: int | None

    @classmethod
    def charger(cls, dossier: Path = DOSSIER) -> "Langue":
        ma = lire_sol(dossier / "lang" / "ANKXTRASO_0_MA.sol")["_Data"]["MA"]
        version = None
        index = dossier / "lang" / "ANKXTRASO_0.sol"
        if index.exists():
            version = lire_sol(index)["_Data"].get("VERSIONS", {}).get("maps")
        return cls(
            cartes={int(k): v for k, v in ma.get("m", {}).items()},
            sous_zones={int(k): v for k, v in ma.get("sa", {}).items()},
            zones={int(k): v for k, v in ma.get("a", {}).items()},
            super_zones={int(k): v for k, v in ma.get("sua", {}).items()},
            version=version,
        )

    def cartes_en(self, x: int, y: int) -> list[int]:
        return sorted(i for i, c in self.cartes.items() if c.get("x") == x and c.get("y") == y)

    def sous_zone(self, id_carte: int) -> str:
        sa = self.cartes[id_carte].get("sa")
        sous_zone = self.sous_zones.get(sa, {})
        zone = self.zones.get(sous_zone.get("a"), {})
        return f"{sous_zone.get('n', '?')} ({zone.get('n', '?')}, sous-zone {sa})"

    def nom_sous_zone(self, id_carte: int) -> str:
        return self.sous_zones.get(self.cartes[id_carte].get("sa"), {}).get("n", "?")

    def super_zone(self, id_carte: int) -> int | None:
        """sua de la carte : 0 « Continent Amaknien » (Monde des Douze), 3 « Zone de départ » (Incarnam)."""
        sous_zone = self.sous_zones.get(self.cartes[id_carte].get("sa"), {})
        return self.zones.get(sous_zone.get("a"), {}).get("sua")

    def par_coordonnees(self, super_zone: int) -> tuple[dict, dict]:
        """Cartes d'une super-zone : (extérieures, intérieures), chacune (x, y) -> [(id, sous-zone), ...] par id croissant.

        Extérieure = champ o (égal au bOutdoor du SWF sur toutes les cartes). Les intérieurs (maisons, banque, mines,
        donjons...) portent les coordonnées de la carte du dehors."""
        exterieures: dict = {}
        interieures: dict = {}
        for id_carte in sorted(self.cartes):
            if self.super_zone(id_carte) != super_zone:
                continue
            carte = self.cartes[id_carte]
            cible = exterieures if carte.get("o") else interieures
            cible.setdefault((carte["x"], carte["y"]), []).append((id_carte, self.nom_sous_zone(id_carte)))
        return exterieures, interieures


def decoder_cases(texte: str) -> list[int]:
    """Cases codées sur 2 caractères de l'alphabet (champs p1/p2 de la langue)."""
    return [ALPHABET.index(texte[k]) * 64 + ALPHABET.index(texte[k + 1]) for k in range(0, len(texte) - 1, 2)]


def case_vers_grille(case: int, largeur: int = 15) -> tuple[int, int]:
    """Numéro de case du client -> case (i, j) de bot/perception/grid.py (lignes de 15 puis 14 cases en quinconce).

    Même formule que tools/import_monde.cell_to_grid, vérifiée en jeu sur des soleils cliqués (cartes 15 x 17)."""
    ligne, reste = divmod(case, 2 * largeur - 1)
    if reste < largeur:
        return 2 * reste, 2 * ligne
    return 2 * (reste - largeur) + 1, 2 * ligne + 1


# --- Copie depuis l'installation (lecture seule de la source) ------------------------------------------------------

def copier(installation: Path = INSTALLATION, shared_objects: Path = SHARED_OBJECTS, dossier: Path = DOSSIER) -> None:
    """Copie les SWF de cartes et les SharedObjects de langue (ANK*) vers `dossier`. N'écrit jamais dans la source."""
    source_cartes = installation / "data" / "maps"
    if not source_cartes.is_dir():
        sys.exit(f"Dossier des cartes introuvable : {source_cartes}")
    cible = dossier.resolve()
    for source in (installation.resolve(), shared_objects.resolve()):
        if cible == source or source in cible.parents or cible in source.parents:
            sys.exit(f"Refus : {cible} et {source} se chevauchent")
    (dossier / "maps").mkdir(parents=True, exist_ok=True)
    (dossier / "lang").mkdir(parents=True, exist_ok=True)
    nb_cartes = taille = 0
    for chemin in sorted(source_cartes.glob("*.swf")):
        shutil.copy2(chemin, dossier / "maps" / chemin.name)
        nb_cartes += 1
        taille += chemin.stat().st_size
    nb_langues = 0
    for chemin in sorted(shared_objects.rglob("*.sol")):
        nom = nom_shared_object(chemin.read_bytes()[:300])
        if not nom or not nom.startswith("ANK") or nom.startswith("ANKOPTIONS"):
            continue
        suffixe = "" if chemin.stem == "00000001" else "_" + chemin.stem
        shutil.copy2(chemin, dossier / "lang" / f"{nom}{suffixe}.sol")
        nb_langues += 1
        taille += chemin.stat().st_size
    print(f"{nb_cartes} cartes et {nb_langues} fichiers de langue copiés ({taille / 1e6:.1f} Mo) vers {dossier}")


# --- Résumés -------------------------------------------------------------------------------------------------------

def statistiques(dossier: Path = DOSSIER) -> None:
    fichiers = fichiers_cartes(dossier)
    etats: Counter = Counter()
    tailles: Counter = Counter()
    sans_cle = 0
    for chemin in fichiers.values():
        carte = lire_carte_swf(chemin)
        etats[carte.etat] += 1
        tailles[(carte.largeur, carte.hauteur)] += 1
        sans_cle += not carte.cle_attendue
    print(f"{len(fichiers)} fichiers de cartes dans {dossier / 'maps'}")
    for etat, nombre in etats.most_common():
        print(f"  mapData {etat} : {nombre} ({100 * nombre / len(fichiers):.1f} %)")
    print(f"  noms de fichier sans « X » (sans clé) : {sans_cle}")
    print("  tailles (largeur x hauteur) les plus fréquentes : "
          + ", ".join(f"{l} x {h} : {n}" for (l, h), n in tailles.most_common(5)))
    langue_presente = (dossier / "lang" / "ANKXTRASO_0_MA.sol").exists()
    if langue_presente:
        langue = Langue.charger(dossier)
        connues = set(langue.cartes)
        print(f"Langue maps v{langue.version} : {len(connues)} cartes, {len(langue.sous_zones)} sous-zones, "
              f"{len(langue.zones)} zones ; {len(connues & set(fichiers))} ont un fichier, "
              f"{len(set(fichiers) - connues)} fichiers absents de la langue")


def resume_carte(id_carte: int, langue: Langue | None, fichiers: dict[int, Path]) -> list[str]:
    lignes = []
    infos = langue.cartes.get(id_carte) if langue else None
    entete = f"Carte {id_carte}"
    if infos:
        lieu = "extérieure" if infos.get("o") else "intérieure"
        entete += f" [{infos.get('x')},{infos.get('y')}] {lieu}, {langue.sous_zone(id_carte)}"
        if infos.get("n"):
            entete += f", « {infos['n']} »"
    lignes.append(entete)
    chemin = fichiers.get(id_carte)
    largeur = None
    if chemin is None:
        lignes.append("  pas de fichier SWF dans la copie")
    else:
        carte = lire_carte_swf(chemin)
        largeur = carte.largeur
        v = carte.variables
        lignes.append(f"  fichier {chemin.name} : {carte.largeur} x {carte.hauteur} ({carte.cases} cases), "
                      f"fond {v.get('backgroundNum')}, musique {v.get('musicId')}, ambiance {v.get('ambianceId')}, "
                      f"capacités {v.get('capabilities')}")
        lignes.append(f"  mapData : {len(carte.map_data)} caractères, {carte.etat}")
        if carte.etat != "clair":
            lignes.append("  cases, praticabilité, soleils, composantes connexes : NON DISPONIBLES (mapData chiffré, "
                          "clé envoyée par le serveur à l'entrée sur la carte)")
    if infos:
        def nommer(case: int) -> str:
            # Le repère (i, j) du bot n'est établi que pour les cartes de 15 cases de large.
            return f"{case}{case_vers_grille(case)}" if largeur == 15 else str(case)

        for equipe in ("p1", "p2"):
            if infos.get(equipe):
                texte = ", ".join(nommer(c) for c in decoder_cases(infos[equipe]))
                lignes.append(f"  placement de combat {equipe} (langue, en clair ; case(i,j)) : {texte}")
        for objet in (infos.get("p") or {}).values():
            gfx = sorted((objet.get("0") or {}).values())
            case = objet.get("1")
            lieu = nommer(case) if isinstance(case, int) else case
            lignes.append(f"  objet conditionnel de la langue (gfx {gfx}) case {lieu}, condition {objet.get('2')}")
    return lignes


# --- Comparaison avec la référence du bot (config/monde/*.json, lecture seule) -------------------------------------

REFERENCE = RACINE / "config" / "monde"
OBSERVATIONS = RACINE / "data" / "carte.json"  # cartes vues par le bot en jeu (lecture seule)
COMPARAISON = DOSSIER / "comparaison.json"
# Région de la référence -> (fichier, super-zone « sua » de la langue).
REGIONS_REFERENCE = {"Monde des Douze": ("monde_des_douze.json", 0), "Incarnam": ("incarnam.json", 3)}
COTES = {"haut": (0, -1), "bas": (0, 1), "gauche": (-1, 0), "droite": (1, 0)}
OPPOSES = {"haut": "bas", "bas": "haut", "gauche": "droite", "droite": "gauche"}
VERDICTS = ("identique", "graphie", "carte secondaire", "discorde", "absente")
CITE, CONTOUR = "Cité d'Astrub", "Contour d'Astrub"
CADRE_ASTRUB = ((-5, 12), (-30, -8))  # (x min, x max), (y min, y max)
# Faits relevés en jeu par l'utilisateur, pour contrôler la comparaison.
VU_CITE = ["4,-14"]
VU_DEHORS = ["4,-13", "-1,-13", "0,-13", "1,-13", "2,-13", "3,-13"]
PORTES_CITE = [("-1,-20", "-2,-20"), ("5,-22", "5,-23"), ("7,-19", "8,-19"), ("4,-14", "4,-13"), ("-1,-14", "-1,-13")]


def normaliser(nom: str | None) -> str:
    """Nom comparable : sans accents ni majuscules, apostrophe droite, espaces simples."""
    if not nom:
        return ""
    texte = unicodedata.normalize("NFKD", nom.replace("’", "'"))
    return " ".join("".join(c for c in texte if not unicodedata.combining(c)).casefold().split())


def lire_coords(texte: str) -> tuple[int, int]:
    x, y = texte.split(",")
    return int(x), int(y)


def ecrire_coords(coords: tuple[int, int]) -> str:
    return f"{coords[0]},{coords[1]}"


def dans_cadre(coords: tuple[int, int], cadre: tuple | None) -> bool:
    return cadre is None or (cadre[0][0] <= coords[0] <= cadre[0][1] and cadre[1][0] <= coords[1] <= cadre[1][1])


def cote_du_bord(i: int, j: int) -> str | None:
    """Bord d'une carte de 15 x 17 (repère (i, j) du bot) où se trouve la case : là où sont les soleils de sortie."""
    if j <= 1:
        return "haut"
    if j >= 31:
        return "bas"
    if i <= 1:
        return "gauche"
    if i >= 27:
        return "droite"
    return None


def verdict_zone(nom: str | None, cartes: list[tuple[int, str]] | None) -> str:
    """Nom de zone de la référence contre les cartes extérieures de la langue à ces coordonnées (principale d'abord).

    « identique » ; « graphie » (mêmes lettres aux accents, majuscules ou apostrophes près) ; « carte secondaire »
    (nom d'une autre carte extérieure de la même coordonnée) ; « discorde » ; « absente » (aucune carte extérieure) ;
    « sans zone » (la référence n'en donne pas)."""
    if not cartes:
        return "absente"
    if not nom:
        return "sans zone"
    noms = [sous_zone for _, sous_zone in cartes]
    if nom == noms[0]:
        return "identique"
    if normaliser(nom) == normaliser(noms[0]):
        return "graphie"
    if normaliser(nom) in {normaliser(n) for n in noms[1:]}:
        return "carte secondaire"
    return "discorde"


def comparer_reference(reference: dict, exterieures: dict, interieures: dict) -> dict:
    """Compare une référence de config/monde (cartes par coordonnées, zones) aux cartes de la langue.

    `exterieures`, `interieures` : sortie de Langue.par_coordonnees. Carte principale d'une coordonnée : l'extérieure
    de plus petit id (la carte du monde d'origine) ; les autres extérieures sont des couches ajoutées plus tard
    (canopée d'Otomaï, village des Brigandins, prismes, variantes de temples...)."""
    cartes_ref = {lire_coords(k): v for k, v in reference.get("cartes", {}).items()}

    reference_seulement = []
    for coords in sorted(set(cartes_ref) - set(exterieures)):
        dedans = interieures.get(coords, [])
        reference_seulement.append({
            "carte": ecrire_coords(coords), "zone_reference": cartes_ref[coords].get("zone"),
            "objets": [o.get("nom") for o in cartes_ref[coords].get("objets", [])],
            "langue": "intérieurs seulement" if dedans else "aucune carte",
            "interieurs": [{"id": i, "sous_zone": s} for i, s in dedans]})
    langue_seulement = [{"carte": ecrire_coords(c), "ids": [i for i, _ in exterieures[c]], "sous_zone": exterieures[c][0][1]}
                        for c in sorted(set(exterieures) - set(cartes_ref))]

    # Zone que le bot retient pour chaque carte (cartes[].zone, lue par bot/brain/atlas.py).
    par_carte = Counter(verdict_zone(e.get("zone"), exterieures.get(c)) for c, e in cartes_ref.items())
    sans_zone = [{"carte": ecrire_coords(c), "sous_zone_langue": exterieures[c][0][1],
                  "objets": [o.get("nom") for o in e.get("objets", [])]}
                 for c, e in sorted(cartes_ref.items()) if not e.get("zone") and c in exterieures]

    # Appartenances zones[].cartes (une carte peut être dans deux zones : champ « aussi » de cartes[]).
    zones, ecarts = [], []
    zones_de: dict = defaultdict(list)
    for zone in reference.get("zones", []):
        bilan, sous_zones = Counter(), Counter()
        for texte in zone.get("cartes", []):
            coords = lire_coords(texte)
            zones_de[coords].append(zone["nom"])
            cartes = exterieures.get(coords)
            verdict = verdict_zone(zone["nom"], cartes)
            bilan[verdict] += 1
            if cartes:
                sous_zones[cartes[0][1]] += 1
            if verdict != "identique":
                ecarts.append({"carte": texte, "zone_reference": zone["nom"], "region_reference": zone.get("region"),
                               "verdict": verdict, "sous_zones_langue": [s for _, s in cartes or []],
                               "ids": [i for i, _ in cartes or []]})
        zones.append({"zone": zone["nom"], "id": zone.get("id"), "region": zone.get("region"),
                      "cartes": len(zone.get("cartes", [])), **{v: bilan[v] for v in VERDICTS},
                      "sous_zones_langue": dict(sous_zones.most_common())})

    # Sens inverse : chaque sous-zone de la langue (carte principale), et les zones de la référence qui la portent.
    sous_zones_langue: dict = {}
    for coords, cartes in sorted(exterieures.items()):
        nom = cartes[0][1]
        bloc = sous_zones_langue.setdefault(nom, {"cartes": 0, "meme_nom": 0, "autres_zones": Counter(),
                                                  "hors_reference": 0, "sans_zone": 0})
        bloc["cartes"] += 1
        noms_ref = zones_de.get(coords, [])
        if any(normaliser(n) == normaliser(nom) for n in noms_ref):
            bloc["meme_nom"] += 1
        elif noms_ref:
            bloc["autres_zones"][noms_ref[0]] += 1
        elif coords in cartes_ref:
            bloc["sans_zone"] += 1
        else:
            bloc["hors_reference"] += 1
    for bloc in sous_zones_langue.values():
        bloc["autres_zones"] = dict(bloc["autres_zones"].most_common())

    return {
        "chiffres": {
            "cartes_reference": len(cartes_ref),
            "coordonnees_exterieures_langue": len(exterieures),
            "cartes_exterieures_langue": sum(len(v) for v in exterieures.values()),
            "communes": len(set(cartes_ref) & set(exterieures)),
            "reference_seulement": len(reference_seulement),
            "langue_seulement": len(langue_seulement),
            "coordonnees_a_plusieurs_exterieures": sum(len(v) > 1 for v in exterieures.values()),
            "dont_sous_zones_differentes": sum(len({s for _, s in v}) > 1 for v in exterieures.values()),
            "coordonnees_interieures_seulement": len(set(interieures) - set(exterieures)),
            "zones_reference": len(zones),
            "appartenances_zone": sum(z["cartes"] for z in zones),
            "verdicts_appartenances": {v: sum(z[v] for z in zones) for v in VERDICTS},
            "verdicts_zone_par_carte": dict(par_carte.most_common()),
        },
        "reference_seulement": reference_seulement,
        "langue_seulement": langue_seulement,
        "sans_zone": sans_zone,
        "zones": zones,
        "ecarts": ecarts,
        "sous_zones_langue": sous_zones_langue,
    }


def comparer_par_identifiant(reference: dict, langue: Langue, super_zone: int) -> dict:
    """Incarnam : la référence garde l'id de chaque carte (« cartes » : extérieures avec soleils, puis « interieurs »)."""
    entrees = [(k, v, "cartes") for k, v in reference.get("cartes", {}).items()]
    entrees += [(v["carte"], v, "interieurs") for v in reference.get("interieurs", [])]
    ids_langue = {i for i in langue.cartes if langue.super_zone(i) == super_zone}
    ecarts, vus, types = [], set(), Counter()
    for texte, entree, rubrique in entrees:
        id_carte = entree.get("id")
        vus.add(id_carte)
        infos = langue.cartes.get(id_carte)
        if infos is None:
            ecarts.append({"id": id_carte, "carte": texte, "rubrique": rubrique, "ecarts": ["absente de la langue"]})
            types["absente de la langue"] += 1
            continue
        problemes = []
        if (infos["x"], infos["y"]) != lire_coords(texte):
            problemes.append(f"coordonnées {infos['x']},{infos['y']} dans la langue")
        if entree.get("zone") != langue.nom_sous_zone(id_carte):
            problemes.append(f"sous-zone « {langue.nom_sous_zone(id_carte)} » dans la langue")
        if bool(infos.get("o")) != (rubrique == "cartes"):
            problemes.append("extérieure dans la langue" if infos.get("o") else "intérieure dans la langue")
        if problemes:
            types.update(p.split(" « ")[0] if p.startswith("sous-zone") else p for p in problemes)
            ecarts.append({"id": id_carte, "carte": texte, "rubrique": rubrique, "zone_reference": entree.get("zone"),
                           "ecarts": problemes})
    langue_seulement = [{"id": i, "carte": f"{langue.cartes[i]['x']},{langue.cartes[i]['y']}",
                         "sous_zone": langue.nom_sous_zone(i), "exterieure": bool(langue.cartes[i].get("o"))}
                        for i in sorted(ids_langue - vus)]
    noms = [{"id": z.get("id"), "reference": z["nom"], "langue": langue.sous_zones.get(z.get("id"), {}).get("n")}
            for z in reference.get("zones", [])]
    return {
        "ids_reference": len(vus), "ids_langue": len(ids_langue), "communs": len(vus & ids_langue),
        "types_d_ecarts": dict(types.most_common()), "ecarts": ecarts, "langue_seulement": langue_seulement,
        "noms_de_zone_differents": [n for n in noms if n["reference"] != n["langue"]],
    }


def lettres_des_zones(noms: list[str]) -> dict[str, str]:
    """Une lettre par sous-zone pour la grille : C = Cité d'Astrub, c = Contour d'Astrub, puis les initiales."""
    lettres = {CITE: "C", CONTOUR: "c"}
    prises = set(lettres.values()) | {".", "!", "+", "-"}
    for nom in noms:
        if nom in lettres:
            continue
        mots = [m for m in re.split(r"[\s'’-]+", nom) if m and m[0].isalpha() and m.lower() not in
                {"le", "la", "les", "de", "des", "du", "d", "l"}]
        candidats = [m[0].upper() for m in mots] + [m[0].lower() for m in mots] + list("ABDEGHIJKLMNOPQRSTUVWXYZ")
        lettre = next((l for l in candidats if l not in prises), "?")
        lettres[nom] = lettre
        prises.add(lettre)
    return lettres


def grille_zones(exterieures: dict, cartes_ref: dict, cadre: tuple) -> tuple[list[str], dict[str, str]]:
    """Grille texte d'un cadre : lettre de la sous-zone de la langue (carte principale) quand la référence concorde ;
    « ! » discorde, « + » carte de la langue absente de la référence, « - » carte de la référence seule, « . » rien."""
    dedans = [c for c in exterieures if dans_cadre(c, cadre)]
    lettres = lettres_des_zones([n for n, _ in Counter(exterieures[c][0][1] for c in dedans).most_common()])
    (x_min, x_max), (y_min, y_max) = cadre
    lignes = ["     " + "".join(f"{x:>4}" for x in range(x_min, x_max + 1))]
    utilisees = set()
    for y in range(y_min, y_max + 1):
        cases = []
        for x in range(x_min, x_max + 1):
            cartes, ref = exterieures.get((x, y)), cartes_ref.get((x, y))
            if cartes and ref is None:
                symbole = "+"
            elif cartes:
                verdict = verdict_zone(ref.get("zone"), cartes)
                symbole = lettres[cartes[0][1]] if verdict in ("identique", "graphie") else "!"
            else:
                symbole = "-" if ref is not None else "."
            utilisees.add(symbole)
            cases.append(f"{symbole:>4}")
        lignes.append(f"{y:>5}" + "".join(cases))
    return lignes, {l: n for n, l in lettres.items() if l in utilisees}


def detail_astrub(reference: dict, exterieures: dict, observations: dict) -> dict:
    """Cité d'Astrub contre Contour d'Astrub, carte par carte ; faits vus en jeu ; portes et remparts de la cité."""
    region = reference["region"]
    cartes_ref = {lire_coords(k): v for k, v in reference.get("cartes", {}).items()}

    def zone_langue(c):
        return exterieures[c][0][1] if c in exterieures else None

    def zone_ref(c):
        return cartes_ref[c].get("zone") if c in cartes_ref else None

    def vue(c):
        return observations.get(f"{region} {ecrire_coords(c)}") or {}

    def passage(a, b) -> str:
        """Ce que le bot a noté en jeu (data/carte.json) entre deux cartes voisines a et b, dans les deux sens."""
        cote = next(s for s, (dx, dy) in COTES.items() if (a[0] + dx, a[1] + dy) == b)
        etats = set()
        for depart, sens, arrivee in ((a, cote, b), (b, OPPOSES[cote], a)):
            sortie = (vue(depart).get("sorties") or {}).get(sens)
            if sortie == f"{region} {ecrire_coords(arrivee)}":
                etats.add("passage")
            elif sortie == "bloquée":
                etats.add("bloquée")
            elif sortie:
                etats.add("autre carte")
        if {"passage", "bloquée"} <= etats:
            return "contradictoire"
        if "passage" in etats:
            return "passage vu"
        if "bloquée" in etats:
            return "bloquée vue"
        return "mène ailleurs" if etats else "non vu"

    cartes = []
    for c in sorted(set(cartes_ref) | set(exterieures), key=lambda c: (c[1], c[0])):
        zl, zr = zone_langue(c), zone_ref(c)
        if {zl, zr} & {CITE, CONTOUR}:
            cartes.append({"carte": ecrire_coords(c), "ids_exterieurs": [i for i, _ in exterieures.get(c, [])],
                           "langue": zl, "reference": zr, "aussi": cartes_ref.get(c, {}).get("aussi", []),
                           "concorde": zl == zr, "zone_lue_en_jeu": vue(c).get("zone")})

    faits = [{"carte": t, "vu_en_jeu": CITE, "langue": zone_langue(lire_coords(t)), "reference": zone_ref(lire_coords(t)),
              "langue_d_accord": zone_langue(lire_coords(t)) == CITE, "reference_d_accord": zone_ref(lire_coords(t)) == CITE}
             for t in VU_CITE]
    faits += [{"carte": t, "vu_en_jeu": "hors de la cité", "langue": zone_langue(lire_coords(t)),
               "reference": zone_ref(lire_coords(t)), "langue_d_accord": zone_langue(lire_coords(t)) not in (None, CITE),
               "reference_d_accord": zone_ref(lire_coords(t)) not in (None, CITE)} for t in VU_DEHORS]

    portes = []
    for a, b in PORTES_CITE:
        ca, cb = lire_coords(a), lire_coords(b)
        portes.append({"cite": a, "dehors": b, "langue": [zone_langue(ca), zone_langue(cb)],
                       "reference": [zone_ref(ca), zone_ref(cb)], "en_jeu": passage(ca, cb)})

    # Toutes les limites entre une carte de la cité et une carte extérieure voisine qui n'en est pas (langue).
    connues = {(lire_coords(a), lire_coords(b)) for a, b in PORTES_CITE}
    remparts = []
    for c in sorted((c for c in exterieures if zone_langue(c) == CITE), key=lambda c: (c[1], c[0])):
        for cote, (dx, dy) in COTES.items():
            n = (c[0] + dx, c[1] + dy)
            if n in exterieures and zone_langue(n) != CITE:
                remparts.append({"cite": ecrire_coords(c), "cote": cote, "voisine": ecrire_coords(n),
                                 "zone_voisine": zone_langue(n), "porte_observee": (c, n) in connues,
                                 "en_jeu": passage(c, n), "reference": [zone_ref(c), zone_ref(n)]})
    grille, legende = grille_zones(exterieures, cartes_ref, CADRE_ASTRUB)
    return {
        "chiffres": {
            "cartes": len(cartes), "concordent": sum(c["concorde"] for c in cartes),
            "cite_langue": sum(c["langue"] == CITE for c in cartes), "cite_reference": sum(c["reference"] == CITE for c in cartes),
            "contour_langue": sum(c["langue"] == CONTOUR for c in cartes),
            "contour_reference": sum(c["reference"] == CONTOUR for c in cartes),
            "zones_lues_en_jeu": sum(bool(c["zone_lue_en_jeu"]) for c in cartes),
            "zones_lues_egales_a_la_langue": sum(c["zone_lue_en_jeu"] == c["langue"] for c in cartes if c["zone_lue_en_jeu"]),
            "limites_de_la_cite": len(remparts),
            "limites_par_voisine": dict(Counter(r["zone_voisine"] for r in remparts).most_common()),
            "limites_vues_en_jeu": dict(Counter(r["en_jeu"] for r in remparts).most_common()),
        },
        "cartes": cartes, "faits_observes": faits, "portes": portes, "limites_de_la_cite": remparts,
        "grille": grille, "legende": legende,
    }


def soleils_conditionnels(langue: Langue, fichiers: dict[int, Path], super_zone: int, exterieures: dict,
                          observations: dict, region: str, cadre: tuple | None = None) -> dict:
    """Champ p de la langue : objets gfx 1029/1030 (soleil en 1.29) sur une case, avec une condition « BE>n ».

    Sur le bord d'une carte extérieure de 15 cases de large, le côté donne la voisine visée ; on compare n au champ ep
    de cette voisine, et la case au soleil que le bot a vu de ce côté (data/carte.json)."""
    bilan, relation, controle = Counter(), Counter(), Counter()
    details = []
    for id_carte in sorted(langue.cartes):
        infos = langue.cartes[id_carte]
        if "p" not in infos or langue.super_zone(id_carte) != super_zone:
            continue
        coords = (infos["x"], infos["y"])
        largeur = lire_carte_swf(fichiers[id_carte]).largeur if id_carte in fichiers else None
        for objet in (infos.get("p") or {}).values():
            bilan["objets"] += 1
            case, condition = objet.get("1"), objet.get("2")
            trouve = re.fullmatch(r"BE>(\d+)", condition or "")
            seuil = int(trouve.group(1)) if trouve else None
            grille = case_vers_grille(case) if largeur == 15 and isinstance(case, int) else None
            cote = cote_du_bord(*grille) if grille and infos.get("o") else None
            bilan["sur un bord" if cote else "intérieur ou hors 15 x 17" if infos.get("o") else "carte intérieure"] += 1
            voisine = ep_voisine = None
            if cote:
                dx, dy = COTES[cote]
                voisine = (coords[0] + dx, coords[1] + dy)
                if voisine in exterieures:
                    ep_voisine = langue.cartes[exterieures[voisine][0][0]].get("ep")
                if seuil is not None and ep_voisine is not None:
                    relation["n = ep de la voisine - 1" if seuil == ep_voisine - 1 else "autre"] += 1
            vue = observations.get(f"{region} {ecrire_coords(coords)}") if infos.get("o") else None
            if vue is None:
                verdict = "carte non vue par le bot"
            elif not cote:
                verdict = "pas sur un bord"
            else:
                vu = (vue.get("soleils") or {}).get(cote)
                verdict = ("même case que le soleil vu" if vu is not None and tuple(vu) == grille
                           else "autre case vue de ce côté" if vu is not None else "aucun soleil vu de ce côté")
            controle[verdict] += 1
            if dans_cadre(coords, cadre):
                details.append({"id": id_carte, "carte": ecrire_coords(coords), "exterieure": bool(infos.get("o")),
                                "sous_zone": langue.nom_sous_zone(id_carte), "ep": infos.get("ep"),
                                "gfx": sorted((objet.get("0") or {}).values()), "case": case,
                                "case_bot": list(grille) if grille else None, "cote": cote, "condition": condition,
                                "voisine": ecrire_coords(voisine) if voisine else None, "ep_voisine": ep_voisine,
                                "en_jeu": verdict})
    return {"cartes": sum(1 for i, c in langue.cartes.items() if "p" in c and langue.super_zone(i) == super_zone),
            "bilan": dict(bilan), "condition_et_ep": dict(relation), "controle_en_jeu": dict(controle),
            "cadre": cadre, "details": details}


def voisines_sous_zones(langue: Langue, exterieures: dict, cadre: tuple | None = None) -> dict:
    """Champ v des sous-zones contre les sous-zones qui se touchent sur la carte (cartes extérieures principales)."""
    principale = {c: langue.cartes[v[0][0]]["sa"] for c, v in exterieures.items()}
    limites: dict = defaultdict(list)
    for c, sa in principale.items():
        for cote in ("droite", "bas"):
            dx, dy = COTES[cote]
            n = (c[0] + dx, c[1] + dy)
            if n in principale and principale[n] != sa:
                limites[tuple(sorted((sa, principale[n])))].append(f"{ecrire_coords(c)} | {ecrire_coords(n)}")

    def v(sa):
        return set((langue.sous_zones.get(sa, {}).get("v") or {}).values())

    def nom(sa):
        return langue.sous_zones.get(sa, {}).get("n", "?")

    presentes = set(principale.values())
    dans_v, hors_v = 0, []
    for (a, b), liste in sorted(limites.items()):
        if b in v(a) or a in v(b):
            dans_v += 1
        else:
            proches = [l for l in liste if any(dans_cadre(lire_coords(p), cadre) for p in l.split(" | "))]
            hors_v.append({"sous_zones": [nom(a), nom(b)], "limites": len(liste), "dans_le_cadre": proches})
    v_sans_limite = sorted({tuple(sorted((a, b))) for a in presentes for b in v(a) if b in presentes}
                           - set(limites))
    symetriques = sum(1 for a in langue.sous_zones for b in v(a) if a in v(b))
    return {
        "paires_contigues": len(limites), "paires_contigues_dans_v": dans_v, "paires_contigues_hors_v": len(hors_v),
        "paires_v_sans_limite_commune": len(v_sans_limite), "liens_v": sum(len(v(a)) for a in langue.sous_zones),
        "liens_v_reciproques": symetriques,
        "contigues_hors_v": hors_v,
        "v_sans_limite_commune": [[nom(a), nom(b)] for a, b in v_sans_limite],
        "astrub": {nom(sa): sorted(nom(b) for b in v(sa)) for sa in sorted(presentes)
                   if langue.sous_zones.get(sa, {}).get("a") == 18},
    }


def comparer(dossier: Path = DOSSIER, reference: Path = REFERENCE, observations: Path = OBSERVATIONS) -> dict:
    """Langue des cartes du client contre config/monde/*.json : rien n'est modifié, résultat en dictionnaire."""
    langue = Langue.charger(dossier)
    fichiers = fichiers_cartes(dossier)
    vues = json.loads(observations.read_text(encoding="utf-8")) if observations.exists() else {}
    resultat: dict = {"langue": {"version_maps": langue.version, "cartes": len(langue.cartes),
                                 "super_zones": {str(k): v for k, v in langue.super_zones.items()}},
                      "regions": {}}
    for region, (fichier, super_zone) in REGIONS_REFERENCE.items():
        ref = json.loads((reference / fichier).read_text(encoding="utf-8"))
        exterieures, interieures = langue.par_coordonnees(super_zone)
        bloc = comparer_reference(ref, exterieures, interieures)
        bloc["super_zone"] = {"sua": super_zone, "nom": langue.super_zones.get(super_zone),
                              "cartes_langue": sum(1 for i in langue.cartes if langue.super_zone(i) == super_zone)}
        cartes_ref = {lire_coords(k) for k in ref.get("cartes", {})}
        par_zone: dict = {}
        for coords, cartes in exterieures.items():
            zone = langue.zones.get(langue.sous_zones.get(langue.cartes[cartes[0][0]]["sa"], {}).get("a"), {}).get("n", "?")
            ligne = par_zone.setdefault(zone, {"coordonnees": 0, "cartes_exterieures": 0, "dans_reference": 0})
            ligne["coordonnees"] += 1
            ligne["cartes_exterieures"] += len(cartes)
            ligne["dans_reference"] += coords in cartes_ref
        bloc["zones_langue"] = dict(sorted(par_zone.items(), key=lambda item: -item[1]["coordonnees"]))
        bloc["multiples_sous_zones_differentes"] = [
            {"carte": ecrire_coords(c), "cartes": [{"id": i, "sous_zone": s} for i, s in v]}
            for c, v in sorted(exterieures.items()) if len({s for _, s in v}) > 1]
        if region == "Incarnam":
            bloc["par_identifiant"] = comparer_par_identifiant(ref, langue, super_zone)
        else:
            bloc["astrub"] = detail_astrub(ref, exterieures, vues)
            bloc["soleils_conditionnels"] = soleils_conditionnels(langue, fichiers, super_zone, exterieures, vues,
                                                                  region, CADRE_ASTRUB)
            bloc["voisines_sous_zones"] = voisines_sous_zones(langue, exterieures, CADRE_ASTRUB)
        resultat["regions"][region] = bloc
    return resultat


def afficher_comparaison(resultat: dict) -> None:
    print(f"Langue maps v{resultat['langue']['version_maps']} : {resultat['langue']['cartes']} cartes")
    for region, bloc in resultat["regions"].items():
        ch = bloc["chiffres"]
        print(f"\n== {region} (sua {bloc['super_zone']['sua']}, {bloc['super_zone']['nom']}) ==")
        print(f"  référence : {ch['cartes_reference']} cartes ; langue : {ch['coordonnees_exterieures_langue']} "
              f"coordonnées extérieures ({ch['cartes_exterieures_langue']} cartes extérieures)")
        print(f"  communes {ch['communes']}, référence seule {ch['reference_seulement']}, langue seule "
              f"{ch['langue_seulement']} ; coordonnées à plusieurs extérieures {ch['coordonnees_a_plusieurs_exterieures']}"
              f" (sous-zones différentes : {ch['dont_sous_zones_differentes']})")
        print(f"  appartenances zones[].cartes : {ch['appartenances_zone']} -> {ch['verdicts_appartenances']}")
        print(f"  zone par carte (cartes[].zone, utilisée par le bot) : {ch['verdicts_zone_par_carte']}")
        for zone in bloc["zones"]:
            if zone["identique"] != zone["cartes"]:
                autres = {k: v for k, v in zone["sous_zones_langue"].items() if k != zone["zone"]}
                print(f"    {zone['region'] or region} / {zone['zone']} : {zone['identique']}/{zone['cartes']} identiques, "
                      f"discordes {zone['discorde']}, absentes {zone['absente']}, secondaires {zone['carte secondaire']}"
                      f" ; langue : {autres}")
        if "astrub" in bloc:
            print(f"  Astrub : {bloc['astrub']['chiffres']}")
            print("\n".join("    " + ligne for ligne in bloc["astrub"]["grille"]))
            print(f"    légende : {bloc['astrub']['legende']}")
            print(f"  soleils conditionnels : {({k: v for k, v in bloc['soleils_conditionnels'].items() if k != 'details'})}")
            vs = bloc["voisines_sous_zones"]
            print(f"  champ v : {vs['paires_contigues']} paires contiguës, dont {vs['paires_contigues_dans_v']} dans v ; "
                  f"{vs['paires_v_sans_limite_commune']} paires de v sans limite commune")
        if "par_identifiant" in bloc:
            pi = bloc["par_identifiant"]
            print(f"  par id : {pi['communs']}/{pi['ids_reference']} ids communs, langue {pi['ids_langue']} ; "
                  f"écarts {pi['types_d_ecarts']} ; noms {pi['noms_de_zone_differents']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lecture des fichiers de cartes du client Dofus Retro (copie).")
    parser.add_argument("--copier", action="store_true", help="copie l'installation vers data/client_retro/")
    parser.add_argument("--stats", action="store_true", help="bilan du chiffrement des cartes")
    parser.add_argument("--carte", help="coordonnées x,y (ex. -1,-19)")
    parser.add_argument("--id", type=int, help="identifiant de carte")
    parser.add_argument("--comparer", action="store_true",
                        help="compare la langue à config/monde (lecture seule) et écrit data/client_retro/comparaison.json")
    parser.add_argument("--dossier", type=Path, default=DOSSIER, help="copie à lire (défaut : data/client_retro)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.copier:
        copier(dossier=args.dossier)
    if args.stats:
        statistiques(args.dossier)
    if args.comparer:
        resultat = comparer(args.dossier)
        sortie = args.dossier / COMPARAISON.name
        sortie.write_text(json.dumps(resultat, ensure_ascii=False, indent=1), encoding="utf-8")
        afficher_comparaison(resultat)
        print(f"\nDétail : {sortie}")
    if args.carte or args.id is not None:
        fichiers = fichiers_cartes(args.dossier)
        langue = Langue.charger(args.dossier) if (args.dossier / "lang" / "ANKXTRASO_0_MA.sol").exists() else None
        if args.carte:
            if langue is None:
                sys.exit("Langue absente : lancer d'abord --copier")
            x, y = (int(v) for v in args.carte.split(","))
            ids = langue.cartes_en(x, y)
            print(f"[{x},{y}] : {len(ids)} carte(s) dans la langue maps v{langue.version}")
        else:
            ids = [args.id]
        for id_carte in ids:
            print("\n".join(resume_carte(id_carte, langue, fichiers)))
    if not (args.copier or args.stats or args.comparer or args.carte or args.id is not None):
        parser.print_help()


if __name__ == "__main__":
    main()
