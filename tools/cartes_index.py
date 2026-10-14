"""Index des cartes du client Dofus Retro à partir des données EN CLAIR (jamais du mapData chiffré).

    .venv/Scripts/python -m tools.cartes_index --construire   # écrit data/client_retro/cartes/*.json
    .venv/Scripts/python -m tools.cartes_index --carte 7331   # résumé d'une carte (id)
    .venv/Scripts/python -m tools.cartes_index --coord -1,-19 # cartes à ces coordonnées
    .venv/Scripts/python -m tools.cartes_index --inventaire   # ce que contiennent les cartes (chiffres, exemples)

Deux sources, toutes deux lisibles sans clé :
- la langue des cartes (lang/ANKXTRASO_0_MA.sol, par tools/cartes_client) : pour chaque carte, coordonnées, sous-zone,
  zone, intérieur/extérieur (o), cases de placement de combat (p1, p2), soleils conditionnels (p) ;
- l'en-tête de chaque SWF (data/maps/<id>X.swf) : largeur, hauteur, fond, ambiance, musique, capacités (bits),
  bOutdoor et les quatre droits canAggro/canUseInventory/canUseObject/canChangeCharac. Le mapData (cases) reste
  chiffré : praticabilité, murs, soleils réels et objets interactifs posés NE sont PAS ici (voir data/carte.json,
  observé en jeu).

Positions d'objets données en clair par la langue : portes de maisons (table H, par carte et par case) et soleils
conditionnels (MA.p). Les objets interactifs (table IO) ne sont qu'un catalogue gfx -> métier ; leur position est
dans le mapData chiffré.

Écrit seulement dans data/client_retro/cartes/. Lecture seule partout ailleurs. Bibliothèque standard uniquement.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tools.cartes_client import (
    DOSSIER,
    Langue,
    case_vers_grille,
    decoder_cases,
    fichiers_cartes,
    lire_carte_swf,
    nombre_de_cases,
)
from tools.langue_client import LangueClient, liste

CARTES = DOSSIER / "cartes"
GFX_SOLEIL = (1029, 1030)  # dessins du soleil en 1.29
# Droits booléens lus en clair dans le DoAction de chaque carte.
DROITS = ("canAggro", "canUseInventory", "canUseObject", "canChangeCharac")


def case_bot(numero: int, largeur: int) -> list[int] | None:
    """Case (i, j) du repère du bot pour une carte de 15 cases de large (repère vérifié en jeu) ; None sinon."""
    return list(case_vers_grille(numero)) if largeur == 15 else None


# --- Catalogue des objets interactifs (table IO : gfx -> métier, sans position) ------------------------------------

# IO.d[type].t : nature de l'objet interactif (déduite des noms de la table ; Frêne/Fer -> récolte, Scie/Four ->
# atelier, Zaap/Zaapi -> déplacement, Porte, Coffre, Enclos, Levier, Statue de classe...).
METIERS_IO = {1: "récolte", 2: "atelier", 3: "zaap", 4: "source", 5: "porte", 6: "coffre", 7: "marmite",
              10: "zaapi", 12: "liste des artisans", 13: "enclos", 14: "levier", 15: "statue"}


def catalogue_objets_interactifs(langue: LangueClient) -> dict:
    """gfx d'objet interactif -> {type, nom, nature (récolte/atelier/déplacement), compétences}. Sans position."""
    io = langue.table("IO")
    types = io.get("d", {})
    catalogue = {}
    for gfx, type_ in io.get("g", {}).items():
        infos = types.get(str(type_), {})
        catalogue[int(gfx)] = {
            "type": int(type_),
            "nom": infos.get("n"),
            "nature": METIERS_IO.get(infos.get("t"), infos.get("t")),
            "competences": [int(s) for s in liste(infos.get("sk"))],
        }
    return catalogue


# --- Positions données par la langue -------------------------------------------------------------------------------

def portes_de_maisons(langue: LangueClient) -> dict[int, list[dict]]:
    """Portes de maisons : table H.d[carte] = {cNNN: id de maison}. (case NNN, nom et description de la maison)."""
    maisons = langue.table("H").get("h", {})
    portes: dict[int, list[dict]] = {}
    for id_carte, cases in langue.table("H").get("d", {}).items():
        liste_portes = []
        for cle, id_maison in cases.items():
            numero = int(str(cle).lstrip("c"))
            if id_maison is None:  # 2 cartes ont une porte sans id de maison (c300 de la carte 709)
                liste_portes.append({"case": numero, "maison": None, "nom": None})
                continue
            maison = maisons.get(str(id_maison), {})
            liste_portes.append({"case": numero, "maison": int(id_maison), "nom": maison.get("n")})
        portes[int(id_carte)] = sorted(liste_portes, key=lambda p: p["case"])
    return portes


def soleils_conditionnels(carte_langue: dict, largeur: int) -> list[dict]:
    """Champ p d'une carte : soleils (gfx 1029/1030) qui n'apparaissent que sous condition « BE>n »."""
    soleils = []
    for objet in liste(carte_langue.get("p")):
        gfx = sorted(int(g) for g in liste(objet.get("0")))
        case = objet.get("1")
        soleils.append({"case": case, "case_bot": case_bot(case, largeur) if isinstance(case, int) else None,
                        "gfx": gfx, "soleil": any(g in GFX_SOLEIL for g in gfx), "condition": objet.get("2")})
    return soleils


def placements(carte_langue: dict, largeur: int) -> dict:
    """Cases de placement de combat des deux équipes (champs p1, p2), en cases (i, j) du bot si la carte fait 15 de large."""
    resultat = {}
    for equipe in ("p1", "p2"):
        if carte_langue.get(equipe):
            cases = decoder_cases(carte_langue[equipe])
            resultat[equipe] = {"cases": cases, "cases_bot": [case_bot(c, largeur) for c in cases]}
    return resultat


# --- Index ---------------------------------------------------------------------------------------------------------

CHAMPS_INDEX = ["id", "x", "y", "sous_zone", "nom_sous_zone", "zone", "nom_zone", "super_zone", "exterieur",
                "largeur", "hauteur", "cases", "fond", "ambiance", "musique", "capacites", *DROITS]


@dataclass
class CarteIndex:
    id: int
    x: int | None
    y: int | None
    sous_zone: int | None
    nom_sous_zone: str | None
    zone: int | None
    nom_zone: str | None
    super_zone: int | None  # 0 Continent Amaknien, 3 Zone de départ (Incarnam)
    exterieur: bool | None  # champ o de la langue (= bOutdoor du SWF)
    largeur: int | None
    hauteur: int | None
    cases: int | None
    fond: int | None
    ambiance: int | None
    musique: int | None
    capacites: int | None  # champ capabilities du SWF : bits de droits sur la carte
    canAggro: bool | None = None
    canUseInventory: bool | None = None
    canUseObject: bool | None = None
    canChangeCharac: bool | None = None


def construire(dossier: Path = DOSSIER) -> dict:
    """Index complet : {cartes: {id: CarteIndex}, coordonnees: {"x,y": [ids]}, placements, objets_langue, catalogue}.

    Lit la langue et l'en-tête de chaque SWF. Ne déchiffre rien."""
    langue_ma = Langue.charger(dossier)
    langue = LangueClient(dossier)
    fichiers = fichiers_cartes(dossier)
    portes = portes_de_maisons(langue)

    cartes: dict[int, CarteIndex] = {}
    coordonnees: dict[str, list[int]] = {}
    plac: dict[int, dict] = {}
    objets: dict[int, dict] = {}

    ids = sorted(set(fichiers) | set(langue_ma.cartes))
    for id_carte in ids:
        infos = langue_ma.cartes.get(id_carte, {})
        sa = infos.get("sa")
        sous_zone = langue_ma.sous_zones.get(sa, {})
        zone_id = sous_zone.get("a")
        zone = langue_ma.zones.get(zone_id, {})
        variables = {}
        largeur = hauteur = None
        if id_carte in fichiers:
            carte_swf = lire_carte_swf(fichiers[id_carte])
            variables = carte_swf.variables
            largeur, hauteur = carte_swf.largeur, carte_swf.hauteur
        entree = CarteIndex(
            id=id_carte,
            x=infos.get("x"), y=infos.get("y"),
            sous_zone=sa, nom_sous_zone=sous_zone.get("n"),
            zone=zone_id, nom_zone=zone.get("n"),
            super_zone=zone.get("sua"),
            exterieur=bool(infos["o"]) if "o" in infos else variables.get("bOutdoor"),
            largeur=largeur or None, hauteur=hauteur or None,
            cases=nombre_de_cases(largeur, hauteur) if largeur and hauteur else None,
            fond=variables.get("backgroundNum"), ambiance=variables.get("ambianceId"),
            musique=variables.get("musicId"), capacites=variables.get("capabilities"),
            **{droit: variables.get(droit) for droit in DROITS},
        )
        cartes[id_carte] = entree
        if entree.x is not None and entree.y is not None:
            coordonnees.setdefault(f"{entree.x},{entree.y}", []).append(id_carte)

        p = placements(infos, largeur or 0)
        if p:
            plac[id_carte] = p
        soleils = soleils_conditionnels(infos, largeur or 0)
        maisons = portes.get(id_carte, [])
        if soleils or maisons:
            objets[id_carte] = {"soleils_conditionnels": soleils, "portes_de_maisons": maisons}

    return {
        "cartes": cartes,
        "coordonnees": {c: sorted(ids) for c, ids in sorted(coordonnees.items())},
        "placements": plac,
        "objets_langue": objets,
        "catalogue_objets_interactifs": catalogue_objets_interactifs(langue),
        "zones": {str(i): {"nom": z.get("n"), "super_zone": z.get("sua")} for i, z in langue_ma.zones.items()},
        "sous_zones": {str(i): {"nom": s.get("n"), "zone": s.get("a"), "type": s.get("tt")}
                       for i, s in langue_ma.sous_zones.items()},
        "super_zones": {str(i): n for i, n in langue_ma.super_zones.items()},
    }


def ecrire(dossier: Path = DOSSIER) -> dict:
    """Écrit l'index dans data/client_retro/cartes/. Renvoie quelques compteurs."""
    index = construire(dossier)
    cartes = CARTES if dossier == DOSSIER else dossier / "cartes"
    cartes.mkdir(parents=True, exist_ok=True)

    def ecrire_json(nom: str, contenu) -> None:
        (cartes / nom).write_text(json.dumps(contenu, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    ecrire_json("index.json", {str(i): asdict(c) for i, c in index["cartes"].items()})
    ecrire_json("coordonnees.json", index["coordonnees"])
    ecrire_json("placements.json", {str(i): p for i, p in index["placements"].items()})
    ecrire_json("objets_langue.json", {str(i): o for i, o in index["objets_langue"].items()})
    ecrire_json("catalogue_objets_interactifs.json", index["catalogue_objets_interactifs"])
    ecrire_json("zones.json", {"zones": index["zones"], "sous_zones": index["sous_zones"],
                               "super_zones": index["super_zones"]})
    return {
        "cartes": len(index["cartes"]),
        "coordonnees": len(index["coordonnees"]),
        "placements": len(index["placements"]),
        "cartes_avec_objets_langue": len(index["objets_langue"]),
        "objets_interactifs_catalogue": len(index["catalogue_objets_interactifs"]),
    }


# --- Lecture (ce que le bot utilisera) -----------------------------------------------------------------------------

class IndexCartes:
    """Lecture rapide de l'index écrit dans data/client_retro/cartes/ (chargé à la demande)."""

    def __init__(self, dossier: Path = DOSSIER) -> None:
        self.cartes_dossier = CARTES if dossier == DOSSIER else Path(dossier) / "cartes"
        self._cache: dict[str, dict] = {}

    def _fichier(self, nom: str) -> dict:
        if nom not in self._cache:
            chemin = self.cartes_dossier / f"{nom}.json"
            self._cache[nom] = json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else {}
        return self._cache[nom]

    def carte(self, id_carte: int) -> dict | None:
        """Tout ce qu'on a en clair sur une carte : index, placements, objets de la langue."""
        base = self._fichier("index").get(str(id_carte))
        if base is None:
            return None
        resultat = dict(base)
        resultat["placements"] = self._fichier("placements").get(str(id_carte), {})
        resultat["objets_langue"] = self._fichier("objets_langue").get(str(id_carte), {})
        return resultat

    def cartes_en(self, x: int, y: int) -> list[int]:
        return self._fichier("coordonnees").get(f"{x},{y}", [])

    def carte_principale(self, x: int, y: int) -> dict | None:
        """La carte de plus petit id à ces coordonnées (carte du monde d'origine), avec ses détails."""
        ids = self.cartes_en(x, y)
        return self.carte(ids[0]) if ids else None


# --- Inventaire (chiffres et exemples de ce que contiennent les cartes) --------------------------------------------

def inventaire(dossier: Path = DOSSIER) -> list[str]:
    import collections

    index = construire(dossier)
    cartes = index["cartes"]
    lignes = [f"{len(cartes)} cartes (langue + SWF)"]
    avec_swf = [c for c in cartes.values() if c.largeur]
    tailles = collections.Counter((c.largeur, c.hauteur) for c in avec_swf)
    lignes.append(f"  SWF lus : {len(avec_swf)} ; tailles les plus fréquentes : "
                  + ", ".join(f"{l}x{h} : {n}" for (l, h), n in tailles.most_common(5)))
    exterieures = sum(1 for c in cartes.values() if c.exterieur)
    lignes.append(f"  extérieures {exterieures}, intérieures {len(cartes) - exterieures} ; "
                  f"coordonnées distinctes {len(index['coordonnees'])} ; "
                  f"coordonnées à plusieurs cartes {sum(len(v) > 1 for v in index['coordonnees'].values())}")
    caps = collections.Counter(c.capacites for c in avec_swf)
    lignes.append(f"  capacités (bits) les plus fréquentes : "
                  + ", ".join(f"{v} : {n}" for v, n in caps.most_common(6)))
    for droit in DROITS:
        vrais = sum(1 for c in avec_swf if getattr(c, droit))
        lignes.append(f"    {droit} : {vrais}/{len(avec_swf)}")
    musiques = collections.Counter(c.musique for c in avec_swf)
    fonds = collections.Counter(c.fond for c in avec_swf)
    lignes.append(f"  musiques distinctes {len(musiques)}, fonds distincts {len(fonds)}, "
                  f"ambiances distinctes {len({c.ambiance for c in avec_swf})}")
    lignes.append(f"  cartes avec placement de combat (p1/p2) : {len(index['placements'])}")
    objets = index["objets_langue"]
    soleils = sum(len(o["soleils_conditionnels"]) for o in objets.values())
    portes = sum(len(o["portes_de_maisons"]) for o in objets.values())
    lignes.append(f"  soleils conditionnels (langue) : {soleils} sur "
                  f"{sum(1 for o in objets.values() if o['soleils_conditionnels'])} cartes")
    lignes.append(f"  portes de maisons (langue) : {portes} sur "
                  f"{sum(1 for o in objets.values() if o['portes_de_maisons'])} cartes")
    catalogue = index["catalogue_objets_interactifs"]
    natures = collections.Counter(o["nature"] for o in catalogue.values())
    lignes.append(f"  catalogue d'objets interactifs (gfx, SANS position) : {len(catalogue)} ; natures {dict(natures)}")
    exemples = [f"{g} « {o['nom']} » ({o['nature']})" for g, o in list(catalogue.items())[:6]]
    lignes.append("    exemples : " + " ; ".join(exemples))
    return lignes


# --- Affichage -----------------------------------------------------------------------------------------------------

def resume_carte(id_carte: int, dossier: Path = DOSSIER) -> list[str]:
    index = IndexCartes(dossier)
    carte = index.carte(id_carte)
    if carte is None:
        return [f"carte {id_carte} absente de l'index"]
    lignes = [f"Carte {id_carte} [{carte['x']},{carte['y']}] "
              f"{'extérieure' if carte['exterieur'] else 'intérieure'} | "
              f"{carte['nom_sous_zone']} / {carte['nom_zone']} (sous-zone {carte['sous_zone']}) | "
              f"{carte['largeur']}x{carte['hauteur']} ({carte['cases']} cases)",
              f"  fond {carte['fond']}, ambiance {carte['ambiance']}, musique {carte['musique']}, "
              f"capacités {carte['capacites']} ; "
              + ", ".join(f"{d} {carte[d]}" for d in DROITS)]
    for equipe, contenu in carte["placements"].items():
        lignes.append(f"  placement {equipe} : " + ", ".join(
            f"{c}{tuple(ij) if ij else ''}" for c, ij in zip(contenu["cases"], contenu["cases_bot"])))
    objets = carte["objets_langue"]
    for soleil in objets.get("soleils_conditionnels", []):
        lignes.append(f"  soleil conditionnel case {soleil['case']}{tuple(soleil['case_bot']) if soleil['case_bot'] else ''}"
                      f" gfx {soleil['gfx']} condition {soleil['condition']}")
    for porte in objets.get("portes_de_maisons", []):
        lignes.append(f"  porte de maison case {porte['case']} : {porte['nom']} (maison {porte['maison']})")
    return lignes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dossier", type=Path, default=DOSSIER, help="copie du client (contient lang/ et maps/)")
    parser.add_argument("--construire", action="store_true", help="écrit data/client_retro/cartes/*.json")
    parser.add_argument("--carte", type=int, help="résumé d'une carte par son id")
    parser.add_argument("--coord", help="cartes à des coordonnées x,y")
    parser.add_argument("--inventaire", action="store_true", help="chiffres et exemples du contenu des cartes")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not (args.dossier / "lang" / "ANKXTRASO_0_MA.sol").exists():
        print(f"Langue absente de {args.dossier} (voir tools/cartes_client.py --copier)", file=sys.stderr)
        return 1
    fait = False
    if args.construire:
        compteurs = ecrire(args.dossier)
        print(f"Index écrit dans {CARTES if args.dossier == DOSSIER else args.dossier / 'cartes'} : {compteurs}")
        fait = True
    if args.inventaire:
        print("\n".join(inventaire(args.dossier)))
        fait = True
    if args.carte is not None:
        print("\n".join(resume_carte(args.carte, args.dossier)))
        fait = True
    if args.coord:
        x, y = (int(v) for v in args.coord.split(","))
        ids = IndexCartes(args.dossier).cartes_en(x, y)
        print(f"[{x},{y}] : {ids}")
        for id_carte in ids:
            print("\n".join("  " + l for l in resume_carte(id_carte, args.dossier)))
        fait = True
    if not fait:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
