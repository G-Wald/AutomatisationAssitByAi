"""Clés des cartes du client Dofus Retro : le client les garde-t-il sur le disque, et comment déchiffrent-elles mapData ?

    .venv/Scripts/python -m tools.cles_cartes --inventaire   # %APPDATA%/Dofus Retro en lecture seule (noms, tailles, formats)
    .venv/Scripts/python -m tools.cles_cartes --copier       # copie les fichiers utiles vers data/client_retro/stockage/
    .venv/Scripts/python -m tools.cles_cartes --chercher     # cherche des clés de carte dans les copies
    .venv/Scripts/python -m tools.cles_cartes --code         # retrouve le déchiffrement dans loader.swf (copie)
    .venv/Scripts/python -m tools.cles_cartes --dechiffrer 7331 --cle <hexa du paquet GDM>

Constats (détails : docs/rapports/cles_cartes.md) :
- aucune clé de carte sur le disque : SharedObjects (langue, affichage, astuces...), cache HTTP (langues seulement),
  stockages Electron (vides) ;
- dans le bytecode de loader.swf, la clé reçue (paquet GDM) passe par prepareKey puis va dans un tableau en mémoire du
  gestionnaire de cartes ; aucune classe qui touche ce tableau n'appelle SharedObject.getLocal ;
- l'algorithme est celui de la 1.29, vérifié dans le bytecode : preparer_cle, checksum, dechiffrer ci-dessous ; le
  format des cases (10 caractères, masques de bits) aussi : decoder_case.

Fichiers d'identification (cookies, options du compte, raccourcis nommés d'après le compte) : jamais copiés ; on ne lit
que l'en-tête des SharedObjects, où est leur nom. Les dossiers du lanceur (zaap, Ankama Launcher) ne sont pas parcourus.
Rien n'est écrit hors de data/client_retro/stockage/. Seule la bibliothèque standard est utilisée.
"""

import argparse
import json
import os
import re
import shutil
import struct
import sys
import zlib
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tools.cartes_client import (
    ALPHABET,
    DOSSIER,
    INSTALLATION,
    case_vers_grille,
    etat_map_data,
    fichiers_cartes,
    lire_carte_swf,
    lire_sol,
    nom_shared_object,
    swf_tags,
)

APPLI = Path(os.environ.get("APPDATA", "")) / "Dofus Retro"
STOCKAGE = DOSSIER / "stockage"
CARTES_CONNUES = {7331: "-1,-19", 7329: "-1,-20", 7314: "-2,-20", 7414: "4,-16"}  # visitées les 11 et 12/10
GFX_SOLEILS = (1029, 1030)  # gfx du soleil en 1.29, et de la langue (champ p) : supposé, non vérifié en jeu
CHIFFRES_HEXA = "0123456789ABCDEF"


# --- Algorithme du client (vérifié dans le bytecode de loader.swf, identique à la 1.29) ------------------------------

def unescape(texte: str) -> str:
    """unescape d'ActionScript : %uXXXX, et suites de %XX lues comme de l'UTF-8 (un caractère par octet si ce n'en est
    pas). Supposé pour les octets > 0x7F (Flash 6+ échappe en UTF-8) ; sans effet sur l'alphabet des cartes."""
    def remplacer(m: re.Match) -> str:
        if m.group(0).startswith("%u"):
            return chr(int(m.group(0)[2:], 16))
        octets = bytes(int(h, 16) for h in re.findall(r"%([0-9a-fA-F]{2})", m.group(0)))
        try:
            return octets.decode("utf-8")
        except UnicodeDecodeError:
            return octets.decode("latin-1")

    return re.sub(r"%u[0-9a-fA-F]{4}|(?:%[0-9a-fA-F]{2})+", remplacer, texte)


def preparer_cle(cle_hexa: str) -> str:
    """prepareKey : la clé du paquet GDM, lue par paires hexadécimales (un caractère chacune), puis unescape."""
    return unescape("".join(chr(int(cle_hexa[i:i + 2], 16)) for i in range(0, len(cle_hexa), 2)))


def checksum(cle: str) -> str:
    """checksum : somme des (code % 16) des caractères de la clé préparée, modulo 16, en chiffre hexadécimal."""
    return CHIFFRES_HEXA[sum(ord(c) % 16 for c in cle) % 16]


def decalage(cle: str) -> int:
    """Décalage passé à decypherData par le gestionnaire de cartes : parseInt(checksum(clé), 16) * 2."""
    return int(checksum(cle), 16) * 2


def dechiffrer(donnees: str, cle: str, decal: int | None = None) -> str:
    """decypherData : paire hexadécimale n XOR caractère (n + décalage) % len(clé) de la clé préparée, puis unescape."""
    decal = decalage(cle) if decal is None else decal
    texte = "".join(chr(int(donnees[2 * n:2 * n + 2], 16) ^ ord(cle[(n + decal) % len(cle)]))
                    for n in range(len(donnees) // 2))
    return unescape(texte)


def pre_echapper(texte: str) -> str:
    """Échappement du client avant chiffrement : caractères < 32 ou > 127, « % » et « + »."""
    return "".join(c if 32 <= ord(c) <= 127 and c not in "%+" else "".join(f"%{o:02X}" for o in c.encode("utf-8"))
                   for c in texte)


def chiffrer(texte: str, cle: str, decal: int | None = None) -> str:
    """cypherData (sens inverse, pour les tests) : échappement, puis d2h(code XOR clé), en majuscules comme d2h."""
    decal = decalage(cle) if decal is None else decal
    texte = pre_echapper(texte)
    return "".join("%02X" % min(ord(c) ^ ord(cle[(n + decal) % len(cle)]), 255) for n, c in enumerate(texte))


def decoder_case(texte: str) -> dict:
    """Une case de mapData en clair (10 caractères de l'alphabet).

    Masques et décalages vérifiés dans le bytecode (uncompressCell de loader.swf, identiques à la 1.29) ; les noms
    viennent de la 1.29 (ceux du client sont obscurcis). deplacement 0 = non praticable (1.29, supposé)."""
    v = [ALPHABET.index(c) for c in texte]
    return {
        "active": bool(v[0] & 32),
        "ligne_de_vue": bool(v[0] & 1),
        "rotation_sol": (v[1] & 48) >> 4,
        "niveau_sol": v[1] & 15,
        "deplacement": (v[2] & 56) >> 3,
        "sol": ((v[0] & 24) << 6) + ((v[2] & 7) << 6) + v[3],
        "pente": (v[4] & 60) >> 2,
        "sol_retourne": bool(v[4] & 2),
        "objet1": ((v[0] & 4) << 11) + ((v[4] & 1) << 12) + (v[5] << 6) + v[6],
        "rotation_objet1": (v[7] & 48) >> 4,
        "objet1_retourne": bool(v[7] & 8),
        "objet2_retourne": bool(v[7] & 4),
        "objet2_interactif": bool(v[7] & 2),
        "objet2": ((v[0] & 2) << 12) + ((v[7] & 1) << 12) + (v[8] << 6) + v[9],
    }


def decoder_carte(map_data_clair: str) -> list[dict]:
    return [decoder_case(map_data_clair[i:i + 10]) for i in range(0, len(map_data_clair) - 9, 10)]


# --- Inventaire de %APPDATA%/Dofus Retro (lecture seule) ----------------------------------------------------------

SENSIBLES = ("cookies", "login data", "web data", "token", "password", "zaap", "ankama launcher")
SO_OPTIONS_COMPTE = "ANKOPTIONSSO"
SUFFIXE_RACCOURCIS = "_SHARED_OBJECT_SHORTCUTS"


@dataclass
class Entree:
    chemin: str  # relatif au dossier inventorié
    taille: int
    format: str
    copie: str | None  # chemin relatif dans data/client_retro/stockage/, None si non copié
    note: str = ""


def _debut(chemin: Path, n: int = 300) -> bytes:
    with open(chemin, "rb") as f:
        return f.read(n)


def est_sensible(relatif: str) -> bool:
    """Fichier d'identification d'après son seul nom : on ne l'ouvre pas."""
    return any(mot in relatif.lower() for mot in SENSIBLES)


def nom_so_affichable(nom: str) -> str:
    """Les raccourcis portent le nom du compte : on le masque."""
    return "<compte>_<n>" + SUFFIXE_RACCOURCIS if nom.endswith(SUFFIXE_RACCOURCIS) else nom


def decrire(relatif: str, chemin: Path, taille: int) -> Entree:
    parties = relatif.split("/")
    nom = parties[-1]
    if est_sensible(relatif):
        return Entree(relatif, taille, "SQLite Chromium" if nom.lower().startswith("cookies") else "?", None,
                      "identification : ni ouvert, ni copié")
    if nom.endswith(".sol") or nom.endswith(".sxx"):
        so = nom_shared_object(_debut(chemin)) if taille else None
        if so is None:
            return Entree(relatif, taille, "réglages Flash", None, "vide ou sans en-tête SharedObject")
        if so == SO_OPTIONS_COMPTE:
            return Entree(relatif, taille, f"SharedObject AMF0 « {so} »", None,
                          "options du client, dont le compte mémorisé (1.29) : en-tête lu, non copié")
        if so.endswith(SUFFIXE_RACCOURCIS):
            return Entree(relatif, taille, f"SharedObject AMF0 « {nom_so_affichable(so)} »", None,
                          "nommé d'après le compte : en-tête lu, non copié")
        if so.startswith(("ANKXTRASO", "ANKLANGSO")):
            return Entree(relatif, taille, f"SharedObject AMF0 « {so} »", None,
                          "langue : copiée par tools.cartes_client dans data/client_retro/lang")
        suffixe = "" if chemin.stem == "00000001" else "_" + chemin.stem
        return Entree(relatif, taille, f"SharedObject AMF0 « {so} »",
                      f"sharedobjects/{so.replace('/', '_')}{suffixe}.sol")
    if parties[0] == "Cache":
        if nom.startswith("f_"):
            genre = "SWF" if _debut(chemin, 3) in (b"CWS", b"FWS") else "autre"
            return Entree(relatif, taille, f"réponse HTTP en cache ({genre})", f"cache_http/{nom}")
        genre = "index" if nom == "index" else "fichier de blocs : URL, en-têtes, petites réponses"
        return Entree(relatif, taille, f"cache HTTP Chromium ({genre})", f"cache_http/{nom}")
    if parties[0] in ("Session Storage", "Local Storage"):
        if nom == "LOCK" or nom.startswith("LOG"):
            return Entree(relatif, taille, "LevelDB (verrou ou journal technique)", None, "sans contenu utile")
        return Entree(relatif, taille, "LevelDB (stockage web d'Electron)", "electron/" + relatif)
    if relatif == "config.json":
        return Entree(relatif, taille, "JSON (réglages de l'application)", "electron/config.json")
    if parties[0] == "GPUCache":
        return Entree(relatif, taille, "cache GPU Chromium (shaders)", None, "sans rapport avec les cartes")
    if parties[0] == "Code Cache":
        return Entree(relatif, taille, "cache de code JavaScript", None, "index vides")
    if nom in ("Preferences", "Network Persistent State"):
        return Entree(relatif, taille, "JSON Chromium", None, "réglages du navigateur intégré")
    if nom.endswith(".cfg"):
        return Entree(relatif, taille, "configuration Flash (texte)", None, "autorisations du lecteur Flash")
    return Entree(relatif, taille, "?", None)


def inventaire(appli: Path = APPLI) -> list[Entree]:
    entrees = []
    for dossier, sous_dossiers, fichiers in os.walk(appli):
        sous_dossiers.sort()
        for nom in sorted(fichiers):
            chemin = Path(dossier) / nom
            relatif = chemin.relative_to(appli).as_posix()
            entrees.append(decrire(relatif, chemin, chemin.stat().st_size))
    return entrees


# Code du client (installation, lecture seule) copié pour lire le bytecode.
CODE_CLIENT = ["loader.swf", "preloader.swf", "modules/core.swf", "modules/soma.swf", "config.xml", "D1Chat.html",
               "D1Console.html", "D1ElectronLauncher.html", "js/D1Chat.js", "js/D1Console.js",
               "js/D1ElectronLauncher.js", "../main.jsc"]


def copier(appli: Path = APPLI, installation: Path = INSTALLATION, stockage: Path = STOCKAGE) -> list[Entree]:
    """Copie vers `stockage` les entrées copiables de l'inventaire et le code du client. N'écrit jamais dans la source."""
    cible = stockage.resolve()
    for source in (appli.resolve(), installation.resolve()):
        if cible == source or source in cible.parents or cible in source.parents:
            sys.exit(f"Refus : {cible} et {source} se chevauchent")
    entrees = inventaire(appli)
    for entree in entrees:
        if entree.copie and not est_sensible(entree.chemin):
            (stockage / entree.copie).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(appli / entree.chemin, stockage / entree.copie)
    for relatif in CODE_CLIENT:
        source = installation / relatif
        if source.exists():
            destination = stockage / "code" / (Path(relatif).name if relatif.startswith("..") else relatif)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    (stockage / "inventaire.json").write_text(
        json.dumps([asdict(e) for e in entrees], ensure_ascii=False, indent=1), encoding="utf-8")
    return entrees


# --- Recherche de clés dans les copies ------------------------------------------------------------------------------

HEXA_LONG = re.compile(rb"[0-9a-fA-F]{32,}")
HEXA_LONG_UTF16 = re.compile(rb"(?:[0-9a-fA-F]\x00){32,}")
GDM = (b"GDM", "GDM".encode("utf-16-le"))


@dataclass
class Trouvaille:
    fichier: str
    genre: str
    detail: str
    explication: str | None = None  # None : candidat sérieux à examiner


def _explication_hexa(contexte: bytes) -> str | None:
    if b"etag:" in contexte:
        return "ETag HTTP (empreinte de fichier du CDN)"
    if b"cloudfront" in contexte or b"via:" in contexte:
        return "identifiant de relais CloudFront"
    return None


def _swf_inclus(octets: bytes) -> list[bytes]:
    """SWF compressés contenus dans un fichier (réponses en cache), décompressés."""
    corps = []
    for m in re.finditer(rb"CWS[\x05-\x0f]", octets):
        try:
            corps.append(zlib.decompressobj().decompress(octets[m.start() + 8:]))
        except zlib.error:
            pass
    return corps


def _parcourir(valeur, chemin: str, fichier: str, sortie: list[Trouvaille]) -> None:
    """Parcourt un SharedObject : chaînes hexadécimales longues, « GDM », entrées rangées sous l'id d'une carte connue."""
    if isinstance(valeur, dict):
        for cle, v in valeur.items():
            if str(cle) in map(str, CARTES_CONNUES):
                texte = json.dumps(v, ensure_ascii=False)
                table = chemin.split("/")[2] if chemin.count("/") >= 2 else chemin  # /_Data/<table>/...
                explication = None if HEXA_LONG.search(texte.encode()) else f"table « {table} » sans hexadécimal"
                sortie.append(Trouvaille(fichier, "entrée à l'id d'une carte connue", f"{chemin}/{cle} = {texte[:120]}",
                                         explication))
            _parcourir(v, f"{chemin}/{cle}", fichier, sortie)
    elif isinstance(valeur, list):
        for i, v in enumerate(valeur):
            _parcourir(v, f"{chemin}[{i}]", fichier, sortie)
    elif isinstance(valeur, str):
        if len(valeur) >= 32 and re.fullmatch(r"[0-9a-fA-F]+", valeur):
            sortie.append(Trouvaille(fichier, "chaîne hexadécimale (SharedObject)", f"{chemin} ({len(valeur)} car.)"))
        if "GDM" in valeur:
            sortie.append(Trouvaille(fichier, "GDM (SharedObject)", chemin))


def chercher(dossier: Path = DOSSIER) -> tuple[int, list[Trouvaille]]:
    """Cherche dans data/client_retro/lang et stockage/ (hors code) : hexadécimal long, « GDM », ids de cartes connues."""
    resultats = {dossier / "stockage" / n for n in ("inventaire.json", "recherche_cles.json")}  # sorties de l'outil
    fichiers = sorted((dossier / "lang").glob("*.sol"))
    fichiers += sorted(p for p in (dossier / "stockage").rglob("*") if p.is_file() and "code" not in p.parts
                       and p not in resultats)
    trouvailles: list[Trouvaille] = []
    for chemin in fichiers:
        nom = chemin.relative_to(dossier).as_posix()
        octets = chemin.read_bytes()
        swfs = _swf_inclus(octets)
        brut = b"" if octets[:3] == b"CWS" else octets  # un SWF compressé ne se lit qu'une fois décompressé
        for m in HEXA_LONG.finditer(brut):
            trouvailles.append(Trouvaille(nom, "hexadécimal ASCII", f"{len(m.group())} car. à l'octet {m.start()}",
                                          _explication_hexa(brut[max(0, m.start() - 40):m.start()].lower())))
        for m in HEXA_LONG_UTF16.finditer(brut):
            trouvailles.append(Trouvaille(nom, "hexadécimal UTF-16", f"{len(m.group()) // 2} car. à l'octet {m.start()}"))
        for motif in GDM:
            for m in re.finditer(re.escape(motif), brut):
                trouvailles.append(Trouvaille(nom, "GDM", f"octet {m.start()}"))
        for corps in swfs:
            for m in HEXA_LONG.finditer(corps):
                trouvailles.append(Trouvaille(nom, "hexadécimal dans un SWF en cache", f"{len(m.group())} car."))
            if b"GDM" in corps:
                trouvailles.append(Trouvaille(nom, "GDM dans un SWF en cache", ""))
        if chemin.suffix == ".sol":
            try:
                _parcourir(lire_sol(chemin), "", nom, trouvailles)
            except (ValueError, IndexError, struct.error) as erreur:
                trouvailles.append(Trouvaille(nom, "SharedObject illisible", str(erreur)))
    return len(fichiers), trouvailles


# --- Bytecode AVM1 (copie de loader.swf) : code obscurci, on suit le flot d'exécution -------------------------------

NOMS_OPERATIONS = {
    0x00: "End", 0x0A: "Add", 0x0B: "Subtract", 0x0C: "Multiply", 0x0D: "Divide", 0x0E: "Equals", 0x0F: "Less",
    0x10: "And", 0x11: "Or", 0x12: "Not", 0x13: "StringEquals", 0x14: "StringLength", 0x15: "StringExtract",
    0x17: "Pop", 0x18: "ToInteger", 0x1C: "GetVariable", 0x1D: "SetVariable", 0x20: "SetTarget2", 0x21: "StringAdd",
    0x22: "GetProperty", 0x23: "SetProperty", 0x24: "CloneSprite", 0x25: "RemoveSprite", 0x26: "Trace",
    0x27: "StartDrag", 0x28: "EndDrag", 0x29: "StringLess", 0x2A: "Throw", 0x2B: "CastOp", 0x2C: "ImplementsOp",
    0x30: "RandomNumber", 0x32: "CharToAscii", 0x33: "AsciiToChar", 0x34: "GetTime", 0x3A: "Delete", 0x3B: "Delete2",
    0x3C: "DefineLocal", 0x3D: "CallFunction", 0x3E: "Return", 0x3F: "Modulo", 0x40: "NewObject", 0x41: "DefineLocal2",
    0x42: "InitArray", 0x43: "InitObject", 0x44: "TypeOf", 0x45: "TargetPath", 0x46: "Enumerate", 0x47: "Add2",
    0x48: "Less2", 0x49: "Equals2", 0x4A: "ToNumber", 0x4B: "ToString", 0x4C: "PushDuplicate", 0x4D: "StackSwap",
    0x4E: "GetMember", 0x4F: "SetMember", 0x50: "Increment", 0x51: "Decrement", 0x52: "CallMethod", 0x53: "NewMethod",
    0x54: "InstanceOf", 0x55: "Enumerate2", 0x60: "BitAnd", 0x61: "BitOr", 0x62: "BitXor", 0x63: "BitLShift",
    0x64: "BitRShift", 0x65: "BitURShift", 0x66: "StrictEquals", 0x67: "Greater", 0x68: "StringGreater",
    0x69: "Extends", 0x87: "StoreRegister", 0x88: "ConstantPool", 0x8E: "DefineFunction2", 0x8F: "Try", 0x94: "With",
    0x96: "Push", 0x99: "Jump", 0x9A: "GetURL2", 0x9B: "DefineFunction", 0x9D: "If",
}
FIN_DE_FLOT = (0x00, 0x2A, 0x3E)  # End, Throw, Return


@dataclass
class Instruction:
    adresse: int
    op: int
    donnees: bytes
    suivante: int
    cible: int | None = None  # Jump, If
    toujours: bool | None = None  # If : True toujours pris, False jamais, None vrai test
    bruit: bool = False  # valeur poussée pour un prédicat opaque

    @property
    def nom(self) -> str:
        return NOMS_OPERATIONS.get(self.op, f"op{self.op:02x}")


def lire_instruction(code: bytes, pc: int) -> Instruction:
    op = code[pc]
    if op < 0x80:
        return Instruction(pc, op, b"", pc + 1)
    longueur = struct.unpack_from("<H", code, pc + 1)[0]
    ins = Instruction(pc, op, code[pc + 3:pc + 3 + longueur], pc + 3 + longueur)
    if op in (0x99, 0x9D):
        ins.cible = ins.suivante + struct.unpack_from("<h", ins.donnees, 0)[0]
    return ins


def lire_pool(donnees: bytes) -> list[str]:
    nombre = struct.unpack_from("<H", donnees, 0)[0]
    return [p.decode("latin-1") for p in donnees[2:].split(b"\x00")[:nombre]]


def valeurs_push(donnees: bytes, pool: list[str] = ()) -> list:
    """Valeurs d'un Push : str (chaîne ou constante du pool), int/float, bool, None ; registre -> ("r", n)."""
    valeurs, q = [], 0
    while q < len(donnees):
        genre = donnees[q]
        q += 1
        if genre == 0:
            fin = donnees.index(b"\x00", q)
            valeurs.append(donnees[q:fin].decode("latin-1"))
            q = fin + 1
        elif genre == 1:
            valeurs.append(struct.unpack_from("<f", donnees, q)[0])
            q += 4
        elif genre in (2, 3):
            valeurs.append(None)
        elif genre == 4:
            valeurs.append(("r", donnees[q]))
            q += 1
        elif genre == 5:
            valeurs.append(bool(donnees[q]))
            q += 1
        elif genre == 6:
            valeurs.append(struct.unpack("<d", donnees[q + 4:q + 8] + donnees[q:q + 4])[0])
            q += 8
        elif genre == 7:
            valeurs.append(struct.unpack_from("<i", donnees, q)[0])
            q += 4
        elif genre in (8, 9):
            index = donnees[q] if genre == 8 else struct.unpack_from("<H", donnees, q)[0]
            valeurs.append(pool[index] if index < len(pool) else ("c", index))
            q += 1 if genre == 8 else 2
        else:
            break
    return valeurs


def predicat_opaque(precedentes: list[Instruction]) -> tuple[bool | None, list[Instruction]]:
    """Valeur testée par un If quand elle est constante : GetTime (> 0), GetTime + 1, Push d'une constante,
    Push booléen puis Not, Push d'un caractère puis CharToAscii (code > 0). Renvoie (valeur, instructions de bruit)."""
    if not precedentes:
        return None, []
    der = precedentes[-1]
    avant = precedentes[-2] if len(precedentes) > 1 else None
    if der.op == 0x34:
        return True, [der]
    if der.op == 0x50 and avant and avant.op == 0x34:
        return True, [avant, der]
    if der.op == 0x96:
        valeurs = valeurs_push(der.donnees)
        if len(valeurs) == 1 and (valeurs[0] is None or isinstance(valeurs[0], (bool, int, float))):
            return bool(valeurs[0]), [der]
    if avant and avant.op == 0x96:
        valeurs = valeurs_push(avant.donnees)
        if der.op == 0x12 and len(valeurs) == 1 and isinstance(valeurs[0], bool):
            return not valeurs[0], [avant, der]
        if der.op == 0x32 and len(valeurs) == 1 and isinstance(valeurs[0], str) and valeurs[0] and ord(valeurs[0][0]):
            return True, [avant, der]
    return None, []


def tracer(code: bytes) -> dict[int, Instruction]:
    """Instructions atteignables en suivant le flot : les sauts déguisés (If toujours pris) sont suivis, les octets
    parasites qu'ils enjambent ne sont pas décodés ; les corps de fonctions sont tracés à part."""
    instructions: dict[int, Instruction] = {}
    a_faire = [(0, len(code))]
    while a_faire:
        pc, fin = a_faire.pop()
        precedentes: list[Instruction] = []
        while pc < fin and pc not in instructions:
            ins = lire_instruction(code, pc)
            instructions[pc] = ins
            if ins.op == 0x99:
                pc, precedentes = ins.cible, []
                continue
            if ins.op == 0x9D:
                ins.toujours, bruit = predicat_opaque(precedentes)
                for autre in bruit:
                    autre.bruit = True
                if ins.toujours is None:
                    a_faire.append((ins.cible, fin))
                pc, precedentes = (ins.cible if ins.toujours else ins.suivante), []
                continue
            if ins.op in (0x8E, 0x9B):
                taille = struct.unpack_from("<H", ins.donnees, len(ins.donnees) - 2)[0]
                a_faire.append((ins.suivante, ins.suivante + taille))
                pc, precedentes = ins.suivante + taille, []
                continue
            if ins.op in FIN_DE_FLOT:
                break
            precedentes = (precedentes + [ins])[-2:]
            pc = ins.suivante
    return instructions


def _texte(ins: Instruction, pool: list[str]) -> str:
    if ins.op == 0x96:
        return "Push " + ", ".join(f"r{v[1]}" if isinstance(v, tuple) and v[0] == "r" else repr(v)
                                   for v in valeurs_push(ins.donnees, pool))
    if ins.op == 0x87:
        return f"StoreRegister r{ins.donnees[0]}"
    if ins.op == 0x88:
        return f"ConstantPool ({len(lire_pool(ins.donnees))} constantes)"
    if ins.op == 0x8E:
        d = ins.donnees
        fin = d.index(b"\x00")
        nb_params, nb_registres, _ = struct.unpack_from("<HBH", d, fin + 1)
        q, params = fin + 6, []
        for _ in range(nb_params):
            registre = d[q]
            fin_nom = d.index(b"\x00", q + 1)
            nom = d[q + 1:fin_nom].decode("latin-1")
            params.append(f"r{registre}={nom!r}" if registre else repr(nom))
            q = fin_nom + 1
        return f"DefineFunction2({', '.join(params)}) registres={nb_registres}"
    return ins.nom


@dataclass
class Fonction:
    bloc: int
    nom: str | None  # dernière chaîne poussée avant la définition : nom (souvent obscurci) du membre qui la reçoit
    lignes: list[str]
    chaines: set = field(default_factory=set)
    operations: set = field(default_factory=set)
    nombres: set = field(default_factory=set)
    suite: list = field(default_factory=list)  # (opération, valeurs poussées) dans l'ordre d'exécution


def _destination(instructions: dict[int, Instruction], adresse: int) -> int:
    """Première instruction réellement exécutée à partir d'une adresse (sauts déguisés et bruit enjambés)."""
    vues = set()
    while adresse in instructions and adresse not in vues:
        vues.add(adresse)
        ins = instructions[adresse]
        if ins.op == 0x99 or (ins.op == 0x9D and ins.toujours):
            adresse = ins.cible
        elif ins.bruit or (ins.op == 0x9D and ins.toujours is False):
            adresse = ins.suivante
        else:
            break
    return adresse


def lineariser(instructions: dict[int, Instruction], debut: int, fin: int, pool: list[str], bloc: int,
               fonctions: list[Fonction], chaines: set, fonction: Fonction | None = None) -> list[str]:
    """Lignes d'une région [debut, fin) dans l'ordre d'exécution, sans sauts déguisés ni prédicats opaques."""
    vus: set[int] = set()
    lignes: list[tuple[int | None, str]] = []  # (adresse, texte) ; étiquettes ajoutées à la fin
    references: set[int] = set()
    a_faire = [debut]
    dernier_nom = None
    while a_faire:
        pc = _destination(instructions, a_faire.pop(0))
        if pc in vus or not debut <= pc < fin:
            continue
        while debut <= pc < fin and pc in instructions and pc not in vus:
            vus.add(pc)
            ins = instructions[pc]
            if ins.op == 0x88:
                pool[:] = lire_pool(ins.donnees)
            if ins.op == 0x99 or (ins.op == 0x9D and ins.toujours):
                cible = _destination(instructions, ins.cible)
                if cible in vus:
                    references.add(cible)
                    lignes.append((None, f"   goto L{cible}"))
                    break
                pc = cible
                continue
            if ins.bruit or (ins.op == 0x9D and ins.toujours is False):
                pc = ins.suivante
                continue
            if ins.op == 0x9D:
                cible = _destination(instructions, ins.cible)
                references.add(cible)
                lignes.append((pc, f"   If -> L{cible}"))
                a_faire.append(cible)
                pc = ins.suivante
                continue
            if ins.op in (0x8E, 0x9B):
                taille = struct.unpack_from("<H", ins.donnees, len(ins.donnees) - 2)[0]
                sous = Fonction(bloc, dernier_nom, [])
                sous.lignes = lineariser(instructions, ins.suivante, ins.suivante + taille, pool, bloc, fonctions,
                                         chaines, sous)
                fonctions.append(sous)
                lignes.append((pc, f"   {_texte(ins, pool)} {{"))
                lignes.extend((None, "      " + ligne) for ligne in sous.lignes)
                lignes.append((None, "   }"))
                pc = ins.suivante + taille
                continue
            valeurs = valeurs_push(ins.donnees, pool) if ins.op == 0x96 else []
            textes = [v for v in valeurs if isinstance(v, str)]
            chaines.update(textes)
            if valeurs and isinstance(valeurs[-1], str):
                dernier_nom = valeurs[-1]
            if fonction is not None:
                fonction.chaines.update(textes)
                fonction.operations.add(ins.nom)
                fonction.nombres.update(v for v in valeurs if isinstance(v, (int, float)) and not isinstance(v, bool))
                fonction.suite.append((ins.nom, valeurs))
            lignes.append((pc, f"   {_texte(ins, pool)}"))
            if ins.op in FIN_DE_FLOT:
                break
            pc = ins.suivante
        else:
            if pc in vus:  # le flot rejoint du code déjà écrit
                cible = _destination(instructions, pc)
                references.add(cible)
                lignes.append((None, f"   goto L{cible}"))
    sortie = []
    for adresse, texte in lignes:
        if adresse in references:
            sortie.append(f"L{adresse}:")
            references.discard(adresse)
        sortie.append(texte)
    return sortie


@dataclass
class Bloc:
    numero: int
    sprite: int
    chaines: set
    fonctions: list[Fonction]


def blocs_du_code(chemin: Path) -> list[Bloc]:
    """Blocs DoInitAction (classes ActionScript 2) d'un SWF, linéarisés."""
    blocs = []
    for code_tag, donnees in swf_tags(chemin.read_bytes()):
        if code_tag != 59:
            continue
        code = donnees[2:]
        instructions = tracer(code)
        fonctions: list[Fonction] = []
        chaines: set = set()
        lineariser(instructions, 0, len(code), [], len(blocs), fonctions, chaines)
        blocs.append(Bloc(len(blocs), struct.unpack_from("<H", donnees, 0)[0], chaines, fonctions))
    return blocs


SIGNATURES = {
    "decypherData": lambda f: {"BitXor", "Modulo"} <= f.operations
    and {"substr", "parseInt", "charCodeAt", "fromCharCode", "unescape"} <= f.chaines,
    "cypherData": lambda f: "BitXor" in f.operations and "d2h" in f.chaines,
    "prepareKey": lambda f: "BitXor" not in f.operations and {"substr", "parseInt", "fromCharCode", "unescape"} <= f.chaines,
    "checksum": lambda f: "Modulo" in f.operations and "charCodeAt" in f.chaines and 16 in f.nombres
    and not {"BitXor", "CallFunction"} & f.operations and not {"parseInt", "substr", "fromCharCode"} & f.chaines,
}


def nom_du_tableau_de_cles(fonction: Fonction, nom_prepare: str) -> str | None:
    """Dans loadMap : this[<tableau>][Number(sID)] = prepareKey(sKey). Le tableau est le dernier membre de this
    poussé avant l'appel à prepareKey."""
    for k, (operation, valeurs) in enumerate(fonction.suite):
        if operation == "Push" and nom_prepare in valeurs:
            for _, avant in reversed(fonction.suite[:k]):
                if len(avant) == 2 and avant[0] == ("r", 1) and isinstance(avant[1], str):
                    return avant[1]
    return None


def analyser_code(loader: Path) -> dict:
    """Retrouve dans loader.swf les fonctions de déchiffrement, le gestionnaire de cartes et les usages de SharedObject."""
    blocs = blocs_du_code(loader)
    trouvees: dict[str, Fonction] = {}
    # decypherData d'abord (signature la plus précise), puis les autres dans la même classe (réseau, comme en 1.29).
    reseau = next((b for b in blocs for f in b.fonctions if SIGNATURES["decypherData"](f)), None)
    for fonction in reseau.fonctions if reseau else []:
        for nom, signature in SIGNATURES.items():
            if signature(fonction) and nom not in trouvees:
                trouvees[nom] = fonction
    resultat: dict = {"blocs": len(blocs), "fonctions": {}, "gestionnaire_de_cartes": None, "tableau_de_cles": None}
    for nom, fonction in trouvees.items():
        resultat["fonctions"][nom] = {"bloc": fonction.bloc, "nom_obscurci": fonction.nom, "lignes": fonction.lignes}
    noms = {nom: f.nom for nom, f in trouvees.items()}
    so = sorted(b.numero for b in blocs if "getLocal" in b.chaines)
    resultat["blocs_sharedobject"] = so
    cartes = [b for b in blocs if "mapData" in b.chaines and noms.get("decypherData") in b.chaines]
    if cartes and noms.get("prepareKey"):
        bloc = cartes[0]
        resultat["gestionnaire_de_cartes"] = bloc.numero
        charge = next((f for f in bloc.fonctions if noms["prepareKey"] in f.chaines and "X" in f.chaines), None)
        if charge:
            tableau = nom_du_tableau_de_cles(charge, noms["prepareKey"])
            resultat["tableau_de_cles"] = tableau
            resultat["chargement_de_carte"] = charge.lignes
            resultat["blocs_tableau_de_cles"] = sorted(b.numero for b in blocs if tableau in b.chaines)
    suivis = [n for n in (resultat["tableau_de_cles"], *noms.values()) if n]
    resultat["sharedobject_et_cles"] = sorted(b.numero for b in blocs if b.numero in so and set(suivis) & b.chaines)
    return resultat


# --- Déchiffrement d'une carte (si on a sa clé) ---------------------------------------------------------------------

def dechiffrer_carte(id_carte: int, cle_hexa: str, dossier: Path = DOSSIER) -> tuple:
    """(carte SWF, mapData en clair, cases décodées). ValueError si le résultat n'a pas la forme d'un mapData clair."""
    carte = lire_carte_swf(fichiers_cartes(dossier)[id_carte])
    clair = dechiffrer(carte.map_data, preparer_cle(cle_hexa))
    if etat_map_data(clair, carte.cases) != "clair":
        raise ValueError(f"carte {id_carte} : le résultat n'est pas un mapData clair (10 caractères de l'alphabet "
                         f"par case) : clé incorrecte ?")
    return carte, clair, decoder_carte(clair)


def resume_cases(carte, cases: list[dict]) -> list[str]:
    def nommer(numero: int) -> str:
        return f"{numero}{case_vers_grille(numero)}" if carte.largeur == 15 else str(numero)

    actives = [n for n, c in enumerate(cases) if c["active"]]
    praticables = [n for n in actives if cases[n]["deplacement"] > 0]
    soleils = [n for n in actives if cases[n]["objet2"] in GFX_SOLEILS]
    return [f"carte {carte.id} : {len(cases)} cases, {len(actives)} actives, {len(praticables)} praticables "
            f"(déplacement > 0), {len(actives) - len(praticables)} bloquées",
            "soleils supposés (objet2 gfx 1029/1030) : " + (", ".join(nommer(n) for n in soleils) or "aucun")]


# --- Commandes ------------------------------------------------------------------------------------------------------

def _afficher_inventaire(entrees: list[Entree]) -> None:
    for e in entrees:
        copie = f" -> stockage/{e.copie}" if e.copie else ""
        print(f"{e.taille:>10}  {e.chemin}  [{e.format}]{copie}{'  (' + e.note + ')' if e.note else ''}")
    print(f"{len(entrees)} fichiers, {sum(e.taille for e in entrees) / 1e6:.1f} Mo ; à copier : "
          f"{sum(1 for e in entrees if e.copie)} ; identification ou compte (non copiés) : "
          f"{sum(1 for e in entrees if 'compte' in e.note or 'identification' in e.note)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clés des cartes du client Dofus Retro (lecture seule, sur copie).")
    parser.add_argument("--inventaire", action="store_true", help="liste %%APPDATA%%/Dofus Retro (lecture seule)")
    parser.add_argument("--copier", action="store_true", help="copie les fichiers utiles vers data/client_retro/stockage/")
    parser.add_argument("--chercher", action="store_true", help="cherche des clés de carte dans les copies")
    parser.add_argument("--code", action="store_true", help="retrouve le déchiffrement dans loader.swf (copie)")
    parser.add_argument("--dechiffrer", type=int, metavar="ID", help="déchiffre une carte (avec --cle)")
    parser.add_argument("--cle", help="clé hexadécimale de la carte (3e champ du paquet GDM)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.inventaire:
        _afficher_inventaire(inventaire())
    if args.copier:
        entrees = copier()
        print(f"{sum(1 for e in entrees if e.copie)} fichiers de %APPDATA%/Dofus Retro et le code du client copiés "
              f"vers {STOCKAGE}")
    if args.chercher:
        nombre, trouvailles = chercher()
        candidats = [t for t in trouvailles if t.explication is None]
        print(f"{nombre} fichiers examinés ; {len(trouvailles)} correspondances, dont {len(candidats)} sans explication")
        for (genre, explication), n in Counter((t.genre, t.explication) for t in trouvailles).most_common():
            print(f"  {n:>5}  {genre} : {explication or 'À EXAMINER'}")
        for t in candidats:
            print(f"  candidat : {t.fichier} : {t.genre} {t.detail}")
        sortie = STOCKAGE / "recherche_cles.json"
        sortie.write_text(json.dumps([asdict(t) for t in trouvailles], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Détail : {sortie}")
    if args.code:
        loader = STOCKAGE / "code" / "loader.swf"
        if not loader.exists():
            sys.exit("Copie de loader.swf absente : lancer d'abord --copier")
        resultat = analyser_code(loader)
        print(f"{resultat['blocs']} classes (DoInitAction) dans loader.swf")
        for nom, f in resultat["fonctions"].items():
            print(f"\n== {nom} : bloc {f['bloc']}, rangée sous {f['nom_obscurci']!r}")
            print("\n".join(f["lignes"]))
        if resultat.get("chargement_de_carte"):
            print(f"\n== chargement d'une carte (bloc {resultat['gestionnaire_de_cartes']}), clés rangées dans "
                  f"this[{resultat['tableau_de_cles']!r}]")
            print("\n".join(resultat["chargement_de_carte"]))
        print(f"\nblocs qui utilisent ce tableau : {resultat.get('blocs_tableau_de_cles')}")
        print(f"blocs qui appellent SharedObject.getLocal : {resultat['blocs_sharedobject']}")
        print(f"blocs qui font les deux (clés et SharedObject) : {resultat['sharedobject_et_cles'] or 'aucun'}")
    if args.dechiffrer is not None:
        if not args.cle:
            sys.exit("--dechiffrer demande --cle")
        carte, _, cases = dechiffrer_carte(args.dechiffrer, args.cle)
        print("\n".join(resume_cases(carte, cases)))
    if not (args.inventaire or args.copier or args.chercher or args.code or args.dechiffrer is not None):
        parser.print_help()


if __name__ == "__main__":
    main()
