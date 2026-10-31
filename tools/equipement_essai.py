"""Essai HORS JEU (30/10) : quel équipement conviendrait à chaque personnage, d'après la langue du client et les
personnages connus (data/personnages). Ne touche pas au jeu, n'écrit rien. Voir docs/rapports/equipement.md.

    .venv/Scripts/python -m tools.equipement_essai --valeurs            # ce que vaut 1 PA, 10 de chaque carac., 1 dommage
    .venv/Scripts/python -m tools.equipement_essai --objets Eldieso     # meilleurs objets par emplacement (catalogue)
    .venv/Scripts/python -m tools.equipement_essai --panoplies Eldieso  # panoplies accessibles, avec leurs bonus
    .venv/Scripts/python -m tools.equipement_essai --pods 43            # objets de pods jusqu'à ce niveau

Données (lecture seule) :
- langue I : I.u[modèle] = {n nom, t type, l niveau, w poids, c conditions, s panoplie, e arme : [bonus CC, PA,
  portée min, portée max, CC 1/x, EC 1/x, ligne, vue], tw : deux mains} ; I.t[type].t = super-type (emplacement) ;
- langue ISTA : ISTA[modèle] = « effet#min#max#...,... » en hexadécimal (effets possibles, jets min à max) ;
- langue IS : IS[panoplie] = {n, i : modèles, b : {"e<nombre d'objets>": effets}} ;
- data/personnages/*.json (caractéristiques du serveur, sorts lus) et config/sorts/solomonk.json (effets des sorts).
Modèle de dégâts : celui du bot (bot/brain/stats.py, CharacterStats.boost ; bot/brain/spellbook.py, mean_damage).
Les jets des objets sont pris à leur moyenne ; les objets réellement possédés ont leurs vrais jets (serveur), à
utiliser dans la vraie version.
"""

import argparse
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

from bot.brain.spellbook import mean_damage
from bot.brain.stats import CharacterStats

RACINE = Path(__file__).resolve().parents[1]
LANGUE = RACINE / "data" / "client_retro" / "lang"
PERSONNAGES = RACINE / "data" / "personnages"
SORTS = RACINE / "config" / "sorts" / "solomonk.json"

EMPLACEMENTS = {1: "amulette", 2: "arme", 3: "anneau", 4: "ceinture", 5: "bottes", 10: "coiffe", 11: "cape",
                12: "familier", 13: "dofus", 7: "bouclier"}
CARACS = {118: "force", 126: "intelligence", 123: "chance", 119: "agilite", 157: "-force", 155: "-intelligence",
          152: "-chance", 154: "-agilite", 112: "dommages", 138: "dommages_pourcent", 111: "pa", 128: "pm",
          168: "-pa", 169: "-pm", 125: "vitalite", 153: "-vitalite", 124: "sagesse", 156: "-sagesse",
          117: "portee", 116: "-portee", 158: "pods", 182: "invocations", 176: "prospection", 174: "initiative"}
ELEMENT_ARME = {100: "neutre", 97: "terre", 98: "air", 99: "feu", 96: "eau",
                95: "neutre", 92: "terre", 93: "air", 94: "feu", 91: "eau"}
CONDITION_CARAC = {"CS": "force", "CI": "intelligence", "CC": "chance", "CA": "agilite", "CV": "vitalite",
                   "CW": "sagesse"}
CLASSES = {"feca": 1, "osamodas": 2, "enutrof": 3, "sram": 4, "xelor": 5, "ecaflip": 6, "eniripsa": 7, "iop": 8,
           "cra": 9, "sadida": 10, "sacrieur": 11, "pandawa": 12}
LIE_AU_COMPTE = 983  # objets d'événement ou de boutique : écartés par défaut
POIDS_DEFAUT = {"vitalite": 0.0, "sagesse": 0.0, "pods": 0.0}  # ce qui ne fait pas de dégâts : à choisir


# --- Langue du client ----------------------------------------------------------------------------------------------

@lru_cache(maxsize=None)
def table(nom: str) -> dict:
    """Table de langue, accents compris (certaines chaînes sont en latin-1, que cartes_client lit en utf-8)."""
    import tools.cartes_client as cc

    def chaine(self):
        longueur = self._u16()
        brut = self.octets[self.pos:self.pos + longueur]
        self.pos += longueur
        try:
            return brut.decode("utf-8")
        except UnicodeDecodeError:
            return brut.decode("latin-1")

    ancienne, cc._LecteurAmf0.chaine = cc._LecteurAmf0.chaine, chaine
    try:
        return cc.lire_sol(LANGUE / f"ANKXTRASO_0_{nom}.sol")["_Data"][nom]
    finally:
        cc._LecteurAmf0.chaine = ancienne


def effets(texte: str) -> list[tuple[int, float, float]]:
    """« 76#1#28#... » -> [(118, 1, 40)] : effet, jet min, jet max (max = min quand il est vide)."""
    sortie = []
    for morceau in (texte or "").split(","):
        champs = morceau.split("#")
        if len(champs) < 3 or not champs[0]:
            continue
        effet, bas = int(champs[0], 16), int(champs[1], 16) if champs[1] else 0
        haut = int(champs[2], 16) if champs[2] else bas
        sortie.append((effet, bas, max(bas, haut)))
    return sortie


def caracs_objet(modele: int) -> dict[str, float]:
    """Caractéristiques moyennes d'un objet (jets à mi-chemin) : {"force": 20.5, "pa": 1, ...}."""
    totaux: dict[str, float] = {}
    for effet, bas, haut in effets(table("ISTA").get(str(modele), "")):
        nom = CARACS.get(effet)
        if nom:
            signe = -1 if nom.startswith("-") else 1
            nom = nom.lstrip("-")
            totaux[nom] = totaux.get(nom, 0) + signe * (bas + haut) / 2
    return totaux


def ordinaire(modele: int) -> bool:
    """Objet qu'on peut vraiment avoir : ni lié au compte (effet 983 : événement, boutique), ni objet spécial au prix
    de 1 kama (Annobusé de Maître Jarbo, Amulette de Jiva : objets de maître de jeu ou nominatifs), ni objet non
    échangeable sans description (Épée Clipse : « --- »)."""
    objet = table("I")["u"].get(str(modele), {})
    lie = any(e == LIE_AU_COMPTE for e, _, _ in effets(table("ISTA").get(str(modele), "")))
    return not lie and objet.get("p", 0) > 1 and (objet.get("es") or objet.get("d") != "---")


@lru_cache(maxsize=1)
def objets_equipables() -> dict[int, dict]:
    u, types = table("I")["u"], table("I")["t"]
    sortie = {}
    for cle, objet in u.items():
        emplacement = EMPLACEMENTS.get(types.get(str(objet["t"]), {}).get("t"))
        if emplacement and ordinaire(int(cle)):
            sortie[int(cle)] = dict(objet, emplacement=emplacement)
    return sortie


def condition_remplie(condition: str, caracs: dict, classe: str) -> bool | None:
    """Conditions de la langue (« CS>50&CV>50 », « PG=9 », « | » pour ou) ; None : code inconnu ici (à vérifier)."""
    if not condition:
        return True
    if "(" in condition:
        return None
    resultats = []
    for alternative in condition.split("|"):
        tout = True
        for terme in alternative.split("&"):
            m = re.fullmatch(r"([A-Za-z]{2})([<>=!~])(.+)", terme.strip())
            if not m:
                return None
            code, op, valeur = m.groups()
            if code in CONDITION_CARAC and valeur.lstrip("-").isdigit():
                gauche = caracs.get(CONDITION_CARAC[code], 0)
            elif code == "PG" and valeur.isdigit():
                gauche = CLASSES.get(classe, 0)
            else:
                return None
            droite = int(valeur)
            tout &= {"<": gauche < droite, ">": gauche > droite, "=": gauche == droite,
                     "!": gauche != droite}.get(op, False)
        resultats.append(tout)
    return any(resultats)


# --- Personnages et dégâts ------------------------------------------------------------------------------------------

def personnage(nom: str) -> dict:
    return json.loads((PERSONNAGES / f"{nom}.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def reference() -> dict:
    sorts = json.loads(SORTS.read_text(encoding="utf-8"))["sorts"]
    return {s["cle"]: s for s in sorts.values()}


def sorts_offensifs(perso: dict) -> list[dict]:
    """Sorts appris qui font des dégâts : {nom, pa, effets, max par tour, relance}."""
    sortie = []
    for ligne in perso["sorts"]["lignes"]:
        sort = reference().get(ligne.get("cle"))
        if not ligne.get("appris") or not sort or not sort["niveaux"]:
            continue
        niveaux = sort["niveaux"]
        niveau = niveaux[min(len(niveaux), max(1, ligne.get("niveau") or 1)) - 1]
        if mean_damage(niveau["effets"]) <= 0:
            continue
        par_tour = niveau.get("lancers_par_tour") or niveau.get("lancers_par_cible") or 9
        relance = niveau.get("relance") or 0
        sortie.append({"nom": sort["nom"], "pa": niveau["pa"], "effets": niveau["effets"],
                       "max": 1 if relance > 1 else par_tour, "facteur": 1 / relance if relance > 1 else 1})
    return sortie


def degats_par_tour(sorts: list[dict], stats: CharacterStats, pa: int) -> tuple[float, list[str]]:
    """Meilleure suite de sorts d'un tour (sac à dos borné sur les PA, une cible, sans déplacement)."""
    meilleur = {0: (0.0, [])}
    for sort in sorts:
        valeur = mean_damage(sort["effets"], stats) * sort["facteur"]
        suivant = dict(meilleur)
        for budget, (degats, suite) in meilleur.items():
            for n in range(1, sort["max"] + 1):
                cout = budget + n * sort["pa"]
                if cout > pa:
                    break
                candidat = (degats + n * valeur, suite + [sort["nom"]] * n)
                if candidat[0] > suivant.get(cout, (-1, []))[0]:
                    suivant[cout] = candidat
        meilleur = suivant
    return max(meilleur.values(), key=lambda x: x[0])


def stats_de(perso: dict, ajout: dict | None = None) -> CharacterStats:
    base = {k: v for k, v in perso["caracteristiques"].items() if k in CharacterStats.__dataclass_fields__}
    for cle, valeur in (ajout or {}).items():
        if cle in base and isinstance(base[cle], (int, float)):
            base[cle] = base[cle] + valeur
    return CharacterStats(**{k: v for k, v in base.items() if k != "source"})


def valeurs(perso: dict) -> dict[str, float]:
    """Dégâts par tour gagnés pour +1 PA (et +2 PA), +10 de chaque caractéristique, +1 dommage, +10 % de dommages."""
    sorts = sorts_offensifs(perso)
    pa = perso["caracteristiques"].get("pa") or 6
    ref, _ = degats_par_tour(sorts, stats_de(perso), pa)
    sortie = {"pa": degats_par_tour(sorts, stats_de(perso), pa + 1)[0] - ref,
              "_pa2": degats_par_tour(sorts, stats_de(perso), pa + 2)[0] - ref,
              "pm": 0.0}  # un PM ne change pas les dégâts d'un tour sans déplacement : poids à choisir
    for cle, pas in (("force", 10), ("intelligence", 10), ("chance", 10), ("agilite", 10), ("dommages", 1),
                     ("dommages_pourcent", 10)):
        sortie[cle] = (degats_par_tour(sorts, stats_de(perso, {cle: pas}), pa)[0] - ref) / pas
    sortie["_reference"] = ref
    return sortie


def note(caracs: dict, poids: dict) -> float:
    return sum(valeur * poids.get(cle, 0) for cle, valeur in caracs.items())


# --- Sorties -------------------------------------------------------------------------------------------------------

def afficher_valeurs() -> None:
    print("| Personnage | Classe | Niv. | Dégâts/tour | +1 PA | +2 PA | +10 Fo | +10 In | +10 Ch | +10 Ag | +1 Do | +10 %Do | Suite |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for chemin in sorted(PERSONNAGES.glob("*.json")):
        perso = json.loads(chemin.read_text(encoding="utf-8"))
        v = valeurs(perso)
        sorts = sorts_offensifs(perso)
        _, suite = degats_par_tour(sorts, stats_de(perso), perso["caracteristiques"].get("pa") or 6)
        print(f"| {perso['personnage']} | {perso['sorts']['classe']} | {perso.get('niveau')} | {v['_reference']:.0f} | "
              f"{v['pa']:.0f} | {v['_pa2']:.0f} | " + " | ".join(f"{v[c] * 10:.1f}" for c in ("force", "intelligence", "chance", "agilite"))
              + f" | {v['dommages']:.1f} | {v['dommages_pourcent'] * 10:.1f} | {' + '.join(suite)} |")


def candidats(perso: dict, poids: dict):
    niveau, classe = perso.get("niveau") or 1, perso["sorts"]["classe"]
    caracs = perso["caracteristiques"]
    for modele, objet in objets_equipables().items():
        if objet["l"] > niveau or objet["emplacement"] in ("familier", "dofus", "bouclier"):
            continue
        ok = condition_remplie(objet.get("c", ""), caracs, classe)
        if not ok:  # condition non remplie, ou inconnue ici (alignement, nom, quête...) : écarté
            continue
        stats = caracs_objet(modele)
        yield modele, objet, stats, note(stats, poids), ok


def poids_de(perso: dict) -> dict:
    v = valeurs(perso)
    poids = dict(POIDS_DEFAUT, **{k: x for k, x in v.items() if not k.startswith("_")})
    return poids


def afficher_objets(nom: str, par_emplacement: int = 3) -> None:
    perso = personnage(nom)
    poids = poids_de(perso)
    print(f"{nom} : poids (dégâts/tour par point) " + ", ".join(f"{k} {v:.2f}" for k, v in poids.items() if v))
    groupes: dict[str, list] = {}
    for modele, objet, stats, valeur, ok in candidats(perso, poids):
        if objet["emplacement"] != "arme":
            groupes.setdefault(objet["emplacement"], []).append((valeur, modele, objet, stats, ok))
    for emplacement, liste in sorted(groupes.items()):
        for valeur, modele, objet, stats, ok in sorted(liste, key=lambda x: -x[0])[:par_emplacement]:
            texte = ", ".join(f"{k} {v:+.0f}" for k, v in stats.items())
            condition = f" [condition {objet['c']}]" if objet.get("c") else ""
            print(f"  {emplacement:9} {valeur:6.1f}  {objet['n']} (niv. {objet['l']}, {modele}) : {texte}{condition}")


def afficher_panoplies(nom: str) -> None:
    perso = personnage(nom)
    poids, niveau = poids_de(perso), perso.get("niveau") or 1
    u = table("I")["u"]
    lignes = []
    for cle, panoplie in table("IS").items():
        modeles = [int(m) for m in panoplie["i"].values() if int(m) in objets_equipables()]
        accessibles = [m for m in modeles if u[str(m)]["l"] <= niveau
                       and objets_equipables()[m]["emplacement"] not in ("familier", "arme", "bouclier")]
        if len(accessibles) < 2:
            continue
        objets = sum(note(caracs_objet(m), poids) for m in accessibles)
        bonus = {}
        for e, bas, haut in effets(panoplie["b"].get(f"e{len(accessibles)}", "")):
            if CARACS.get(e):
                bonus[CARACS[e].lstrip("-")] = bonus.get(CARACS[e].lstrip("-"), 0) + (-1 if CARACS[e][0] == "-" else 1) * bas
        lignes.append((objets + note(bonus, poids), panoplie["n"], len(accessibles), objets, bonus))
    for total, nom_pano, nombre, objets, bonus in sorted(lignes, key=lambda x: -x[0])[:8]:
        print(f"  {total:6.1f}  {nom_pano} ({nombre} objets : {objets:.1f}, bonus {bonus})")


def afficher_pods(niveau: int) -> None:
    lignes = []
    for modele, objet in objets_equipables().items():
        stats = caracs_objet(modele)
        if objet["l"] <= niveau and (stats.get("pods") or stats.get("force", 0) > 0):
            pods = stats.get("pods", 0) + 5 * stats.get("force", 0)
            if pods >= 100:
                lignes.append((pods, objet["emplacement"], objet["n"], objet["l"], objet.get("c", "")))
    vus: dict[str, int] = {}
    for pods, emplacement, nom, niv, condition in sorted(lignes, key=lambda x: (x[1], -x[0])):
        vus[emplacement] = vus.get(emplacement, 0) + 1
        if vus[emplacement] > 5:
            continue
        print(f"  {emplacement:9} {pods:6.0f}  {nom} (niv. {niv}){'  condition ' + condition if condition else ''}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--valeurs", action="store_true")
    parser.add_argument("--objets")
    parser.add_argument("--panoplies")
    parser.add_argument("--pods", type=int)
    args = parser.parse_args()
    if args.valeurs:
        afficher_valeurs()
    if args.objets:
        afficher_objets(args.objets)
    if args.panoplies:
        afficher_panoplies(args.panoplies)
    if args.pods:
        afficher_pods(args.pods)


if __name__ == "__main__":
    main()
