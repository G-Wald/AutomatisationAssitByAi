"""Lecture des fichiers de langue du client Dofus Retro 1.49 copiés dans data/client_retro/lang (lecture seule) :
objets (clés), recettes, monstres, donjons et leurs salles, indices de la carte du monde, PNJ et dialogues.

    .venv/Scripts/python -m tools.langue_client --donjons             # tous les donjons : niveau, entrée, salles
    .venv/Scripts/python -m tools.langue_client --donjon 2            # salles d'un donjon (identifiant de la table DU)
    .venv/Scripts/python -m tools.langue_client --cles                # objets de type « Clefs » : recette, butin
    .venv/Scripts/python -m tools.langue_client --objet 8143          # un objet : type, recette, butin, fichier du dessin
    .venv/Scripts/python -m tools.langue_client --monstre "Tournesol" # niveaux et vie des monstres par grade
    .venv/Scripts/python -m tools.langue_client --dialogues "clef"    # textes de dialogue (questions et réponses)
    .venv/Scripts/python -m tools.langue_client --pnj "gardien"       # PNJ par nom

Tables lues (fichier lang/ANKXTRASO_0_<T>.sol, objet _Data.<T> ; format AMF0, lu par tools/cartes_client.lire_sol) :
- I : objets. I.u[id] = {n nom, t type, l niveau, w poids, d description, drop : monstres qui le laissent tomber} ;
  I.t[type] = {n nom du type, t super-type}. Type 84 « Clefs », de super-type 9 comme les ressources.
- CR : recettes. CR[id objet] = [[id ingrédient, quantité], ...].
- M : monstres. M[id] = {n, g1..g5 : {l niveau, lp vie, ap, mp}}.
- DU : donjons. DU[id] = {n, lvl, sba sous-zone, m : {id carte : {n nom de salle, x, y, z place sur le plan, i icône}}}.
  Le plan des salles n'est rempli que pour quelques donjons ; sinon, on prend les cartes de la sous-zone (MA).
- HI : indices de la carte du monde. HI[id] = {n, c catégorie, m carte} ; catégorie 6 « Donjons » : n = ids DU.
- MA : cartes (tools/cartes_client.py) : MA.m[id] = {x, y, sa sous-zone, d donjon, n nom de salle...}.
- N : PNJ. N.d[id] = {n nom, a actions} ; N.a[action] = « Parler »...
- D : dialogues. D.q[id] = texte du PNJ, D.a[id] = réponse du joueur. Quel PNJ dit quoi vient du serveur : ce lien
  n'est pas dans la langue.

Les tableaux AMF0 arrivent en objets {"0": ..., "1": ...} : `liste` les remet dans l'ordre. Seule la bibliothèque
standard est utilisée ; rien n'est écrit.
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from tools.cartes_client import DOSSIER, INSTALLATION, lire_sol

TYPE_CLEFS = 84  # I.t["84"] = {"n": "Clefs", "t": 9}
# Dessin d'un objet dans l'installation : clips/items/<type>/<gfx>.swf (gfx = champ « g » de l'objet). Ce sont des
# SWF vectoriels (formes DefineShape) ; 542 des 6 888 contiennent aussi des bitmaps, aucun pour les clés vues.
DOSSIER_ICONES = INSTALLATION / "clips" / "items"
CATEGORIE_DONJONS = 6  # HIC["6"] = {"n": "Donjons"}
GRADES = ("g1", "g2", "g3", "g4", "g5")


def liste(valeur) -> list:
    """Tableau AMF0 (liste, ou objet aux clés « 0 », « 1 »...) remis dans l'ordre ; [] pour None."""
    if valeur is None:
        return []
    if isinstance(valeur, list):
        return valeur
    if isinstance(valeur, dict):
        return [valeur[k] for k in sorted(valeur, key=lambda k: int(k) if str(k).lstrip("-").isdigit() else 0)]
    return [valeur]


@lru_cache(maxsize=None)
def _charger(dossier: str, nom: str) -> dict:
    chemin = Path(dossier) / "lang" / f"ANKXTRASO_0_{nom}.sol"
    return lire_sol(chemin)["_Data"][nom]


@dataclass
class Salle:
    carte: int
    nom: str
    plan: tuple[int, int, int] | None  # (x, y, z) sur le plan du donjon (table DU), None si la salle vient de MA
    icone: int | None
    coordonnees: tuple[int, int] | None  # coordonnées affichées en jeu : celles de l'entrée pour toutes les salles


@dataclass
class Donjon:
    id: int
    nom: str
    niveau: int | None
    sous_zone: int | None
    nom_sous_zone: str | None
    entree: int | None  # carte de l'indice « Donjons » de la carte du monde
    entree_coordonnees: tuple[int, int] | None
    salles: list[Salle] = field(default_factory=list)
    salles_source: str = "aucune"  # « plan » (table DU), « sous-zone » (cartes de MA), « aucune »


@dataclass
class Cle:
    id: int
    nom: str
    niveau: int | None
    poids: int | None
    description: str
    recette: list[tuple[int, str, int]]  # (id, nom, quantité)
    butin: list[tuple[int, str, tuple[int, int] | None]]  # (id du monstre, nom, niveaux)

    @property
    def detruite(self) -> bool:
        return "détruite après utilisation" in self.description


class LangueClient:
    """Accès aux tables de langue d'une copie du client (data/client_retro par défaut), chargées à la demande."""

    def __init__(self, dossier: Path = DOSSIER) -> None:
        self.dossier = Path(dossier)

    def table(self, nom: str) -> dict:
        return _charger(str(self.dossier.resolve()), nom)

    # --- Objets, recettes, monstres ------------------------------------------------------------------------------

    def objet(self, id_objet: int) -> dict | None:
        return self.table("I")["u"].get(str(id_objet))

    def nom_objet(self, id_objet: int) -> str:
        objet = self.objet(id_objet)
        return objet.get("n", f"objet {id_objet}") if objet else f"objet {id_objet} inconnu"

    def type_objet(self, id_objet: int) -> tuple[int | None, str | None, int | None]:
        """(type, nom du type, super-type)."""
        objet = self.objet(id_objet) or {}
        type_ = objet.get("t")
        info = self.table("I")["t"].get(str(type_), {}) if type_ is not None else {}
        return type_, info.get("n"), info.get("t")

    def icone(self, id_objet: int, installation_icones: Path = DOSSIER_ICONES) -> Path | None:
        """Fichier SWF du dessin de l'objet dans l'installation (lecture seule), d'après son type et son gfx."""
        objet = self.objet(id_objet)
        if not objet or objet.get("t") is None or objet.get("g") is None:
            return None
        return Path(installation_icones) / str(objet["t"]) / f"{objet['g']}.swf"

    def recette(self, id_objet: int) -> list[tuple[int, str, int]]:
        ingredients = []
        for element in liste(self.table("CR").get(str(id_objet))):
            ingredient, quantite = liste(element)[:2]
            ingredients.append((int(ingredient), self.nom_objet(int(ingredient)), int(quantite)))
        return ingredients

    def niveaux_monstre(self, id_monstre: int) -> tuple[int, int] | None:
        monstre = self.table("M").get(str(id_monstre)) or {}
        niveaux = [monstre[g]["l"] for g in GRADES if isinstance(monstre.get(g), dict) and "l" in monstre[g]]
        return (min(niveaux), max(niveaux)) if niveaux else None

    def nom_monstre(self, id_monstre: int) -> str:
        return (self.table("M").get(str(id_monstre)) or {}).get("n", f"monstre {id_monstre} inconnu")

    def butin(self, id_objet: int) -> list[tuple[int, str, tuple[int, int] | None]]:
        """Monstres qui laissent tomber l'objet (champ « drop » de l'objet), sans le taux (non fourni)."""
        return [(int(m), self.nom_monstre(int(m)), self.niveaux_monstre(int(m)))
                for m in liste((self.objet(id_objet) or {}).get("drop"))]

    def monstres(self, motif: str) -> list[tuple[int, str, list[tuple[int, int | None]]]]:
        """(id, nom, [(niveau, vie) par grade]) des monstres dont le nom contient le motif (expression régulière)."""
        expression = re.compile(motif, re.IGNORECASE)
        trouves = []
        for id_monstre, monstre in self.table("M").items():
            if expression.search(monstre.get("n", "")):
                grades = [(monstre[g].get("l"), monstre[g].get("lp")) for g in GRADES if isinstance(monstre.get(g), dict)]
                trouves.append((int(id_monstre), monstre["n"], grades))
        return sorted(trouves)

    def cles(self) -> list[Cle]:
        """Objets du type « Clefs » (clés de donjon et morceaux de clés)."""
        cles = []
        for id_objet, objet in self.table("I")["u"].items():
            if objet.get("t") == TYPE_CLEFS:
                cles.append(Cle(int(id_objet), objet.get("n", ""), objet.get("l"), objet.get("w"),
                                objet.get("d", ""), self.recette(int(id_objet)), self.butin(int(id_objet))))
        return sorted(cles, key=lambda c: c.id)

    # --- Cartes et donjons ---------------------------------------------------------------------------------------

    def _cartes(self) -> dict:
        return self.table("MA")["m"]

    def coordonnees(self, id_carte: int) -> tuple[int, int] | None:
        carte = self._cartes().get(str(id_carte))
        return (int(carte["x"]), int(carte["y"])) if carte and "x" in carte and "y" in carte else None

    def nom_sous_zone(self, id_sous_zone: int | None) -> str | None:
        if id_sous_zone is None:
            return None
        info = self.table("MA").get("sa", {}).get(str(id_sous_zone))
        return info.get("n") if isinstance(info, dict) else None

    def entrees_donjons(self) -> dict[int, int]:
        """Identifiant DU -> carte de l'indice « Donjons » (une carte peut servir à plusieurs donjons : « 10,45 »)."""
        entrees = {}
        for indice in self.table("HI").values():
            if indice.get("c") != CATEGORIE_DONJONS:
                continue
            for morceau in str(indice.get("n", "")).split(","):
                if morceau.strip().isdigit():
                    entrees[int(morceau)] = int(indice["m"])
        return entrees

    def donjon(self, id_donjon: int) -> Donjon | None:
        info = self.table("DU").get(str(id_donjon))
        if info is None:
            return None
        entree = self.entrees_donjons().get(id_donjon)
        sous_zone = info.get("sba")
        sous_zone = int(sous_zone) if sous_zone is not None and int(sous_zone) >= 0 else None
        donjon = Donjon(int(id_donjon), info.get("n", ""), info.get("lvl"), sous_zone, self.nom_sous_zone(sous_zone),
                        entree, self.coordonnees(entree) if entree is not None else None)
        plan = info.get("m") or {}
        if plan:
            donjon.salles_source = "plan"
            for id_carte, salle in plan.items():
                donjon.salles.append(Salle(int(id_carte), salle.get("n", ""),
                                           (salle.get("x", 0), salle.get("y", 0), salle.get("z", 0)),
                                           salle.get("i"), self.coordonnees(int(id_carte))))
        elif sous_zone is not None:
            for id_carte, carte in self._cartes().items():
                if carte.get("sa") == sous_zone:
                    donjon.salles.append(Salle(int(id_carte), carte.get("n", ""), None, None,
                                               self.coordonnees(int(id_carte))))
            donjon.salles_source = "sous-zone" if donjon.salles else "aucune"
        donjon.salles.sort(key=lambda s: s.carte)
        return donjon

    def donjons(self) -> list[Donjon]:
        return [self.donjon(int(i)) for i in sorted(self.table("DU"), key=int)]

    # --- PNJ et dialogues ----------------------------------------------------------------------------------------

    def pnj(self, motif: str) -> list[tuple[int, str, list[str]]]:
        """(id, nom, actions) des PNJ dont le nom contient le motif."""
        expression = re.compile(motif, re.IGNORECASE)
        actions = self.table("N").get("a", {})
        trouves = []
        for id_pnj, info in self.table("N").get("d", {}).items():
            if expression.search(info.get("n", "")):
                trouves.append((int(id_pnj), info["n"], [actions.get(str(a), str(a)) for a in liste(info.get("a"))]))
        return sorted(trouves)

    def dialogues(self, motif: str) -> list[tuple[str, int, str]]:
        """(« question » ou « réponse », id, texte) des dialogues qui contiennent le motif."""
        expression = re.compile(motif, re.IGNORECASE)
        trouves = []
        for genre, cle in (("question", "q"), ("réponse", "a")):
            for id_texte, texte in self.table("D").get(cle, {}).items():
                if isinstance(texte, str) and expression.search(texte):
                    trouves.append((genre, int(id_texte), texte))
        return sorted(trouves, key=lambda t: (t[0] != "question", t[1]))


# --- Affichage ---------------------------------------------------------------------------------------------------

def _niveaux(niveaux: tuple[int, int] | None) -> str:
    return "?" if niveaux is None else f"{niveaux[0]}-{niveaux[1]}"


def _coord(coordonnees: tuple[int, int] | None) -> str:
    return "?" if coordonnees is None else f"{coordonnees[0]},{coordonnees[1]}"


def afficher_donjon(donjon: Donjon, detail: bool) -> list[str]:
    lignes = [f"{donjon.id:>3} {donjon.nom} | niveau {donjon.niveau} | entrée {_coord(donjon.entree_coordonnees)} "
              f"(carte {donjon.entree}) | sous-zone {donjon.nom_sous_zone} ({donjon.sous_zone}) | "
              f"{len(donjon.salles)} salles ({donjon.salles_source})"]
    if detail:
        for salle in donjon.salles:
            plan = "" if salle.plan is None else f" plan {salle.plan[0]},{salle.plan[1]}"
            icone = "" if salle.icone is None else f" icône {salle.icone}"
            lignes.append(f"      carte {salle.carte} « {salle.nom} » coordonnées {_coord(salle.coordonnees)}{plan}{icone}")
    return lignes


def afficher_objet(langue: LangueClient, id_objet: int) -> list[str]:
    objet = langue.objet(id_objet)
    if objet is None:
        return [f"objet {id_objet} absent de la langue"]
    type_, nom_type, super_type = langue.type_objet(id_objet)
    lignes = [f"{id_objet} {objet.get('n')} | type {type_} {nom_type} (super-type {super_type}) | "
              f"niveau {objet.get('l')} | poids {objet.get('w')} | gfx {objet.get('g')}", f"   {objet.get('d', '')}"]
    icone = langue.icone(id_objet)
    if icone is not None:
        etat = f"{icone.stat().st_size} octets" if icone.is_file() else "absent de cette installation"
        lignes.append(f"   dessin : {icone} ({etat})")
    recette = langue.recette(id_objet)
    lignes.append("   recette : " + (", ".join(f"{q} x {n} ({i})" for i, n, q in recette) if recette else "aucune"))
    butin = langue.butin(id_objet)
    lignes.append("   butin : " + ("; ".join(f"{n} ({_niveaux(l)})" for _, n, l in butin) if butin else "aucun"))
    return lignes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dossier", type=Path, default=DOSSIER, help="copie du client (contient lang/)")
    parser.add_argument("--donjons", action="store_true")
    parser.add_argument("--donjon", type=int)
    parser.add_argument("--cles", action="store_true")
    parser.add_argument("--objet", type=int)
    parser.add_argument("--monstre")
    parser.add_argument("--dialogues")
    parser.add_argument("--pnj")
    args = parser.parse_args(argv)
    if not (args.dossier / "lang").is_dir():
        print(f"Pas de langue copiée dans {args.dossier / 'lang'} (voir tools/cartes_client.py --copier)",
              file=sys.stderr)
        return 1
    langue = LangueClient(args.dossier)
    lignes: list[str] = []
    if args.donjons:
        for donjon in langue.donjons():
            lignes += afficher_donjon(donjon, detail=False)
    if args.donjon is not None:
        donjon = langue.donjon(args.donjon)
        lignes += afficher_donjon(donjon, detail=True) if donjon else [f"donjon {args.donjon} absent"]
    if args.cles:
        for cle in langue.cles():
            lignes += afficher_objet(langue, cle.id)
    if args.objet is not None:
        lignes += afficher_objet(langue, args.objet)
    if args.monstre:
        for id_monstre, nom, grades in langue.monstres(args.monstre):
            lignes.append(f"{id_monstre} {nom} | " + " ".join(f"niv {l} / {v} PV" for l, v in grades))
    if args.pnj:
        for id_pnj, nom, actions in langue.pnj(args.pnj):
            lignes.append(f"{id_pnj} {nom} | {', '.join(actions) or 'aucune action'}")
    if args.dialogues:
        for genre, id_texte, texte in langue.dialogues(args.dialogues):
            lignes.append(f"{genre} {id_texte} : {' '.join(texte.split())}")
    if not lignes:
        parser.print_help()
        return 1
    print("\n".join(lignes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
