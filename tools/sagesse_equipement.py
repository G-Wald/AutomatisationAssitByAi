"""Sagesse des soutiens : ce que chacun porte, et les objets de nos sacs et de nos coffres qui en donneraient plus
(08/11, l'utilisateur : « un mécanisme pour optimiser les soutiens : analyser le stuff sur chaque perso et dans chaque
banque, voir si certains permettent de donner plus de sagesse ; on ne touche pas au stuff des DPS, ils ont besoin de
faire des dégâts »). Les retraits de PA et de PM esquivables (Flèche Glacée...) dépendent de la sagesse du lanceur.

Lecture seule, d'après les journaux réseau (logs/*/reseau_*.txt) :
- `ASK|id|nom|niveau|classe|...|objets` : le sac de chaque personnage à sa connexion, objets portés compris (position
  0 à 15), avec leurs jets ;
- `EL...` : le contenu d'un coffre ouvert (le dernier vu pour chaque personnage) ;
objet : `uid~modèle~quantité~position~effets`, nombres en hexadécimal ; effet : `id#min#max#...` (124 : sagesse,
156 : sagesse en moins). Niveau et type des objets : langue du client (table I). Les panoplies ne sont pas comptées.

    .venv\\Scripts\\python -m tools.sagesse_equipement --dps Walarbalete
"""

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
WISDOM, WISDOM_LESS = 124, 156
# Positions des objets portés -> emplacement ; types de la langue du client (I.t) -> emplacement
SLOTS = {0: "amulette", 1: "arme", 2: "anneau", 3: "ceinture", 4: "anneau", 5: "bottes", 6: "chapeau", 7: "cape",
         8: "familier", 15: "bouclier"}
TYPE_SLOT = {1: "amulette", 9: "anneau", 10: "ceinture", 11: "bottes", 16: "chapeau", 17: "cape", 18: "familier",
             82: "bouclier", **{t: "arme" for t in (2, 3, 4, 5, 6, 7, 8, 19, 20, 21, 22)}}
LINE = re.compile(r"^(\d\d:\d\d:\d\d)\.\d+ (.*)$")


@dataclass(eq=False)
class Objet:
    modele: int
    position: int
    effets: dict[int, int]
    ou: str  # « porté par X », « sac de X », « coffre de X »
    porteur: str

    @property
    def sagesse(self) -> int:
        return self.effets.get(WISDOM, 0) - self.effets.get(WISDOM_LESS, 0)


@dataclass
class Perso:
    nom: str
    niveau: int = 0
    vu: str = ""
    portes: dict[int, Objet] = field(default_factory=dict)


def effets(texte: str) -> dict[int, int]:
    out: dict[int, int] = {}
    for part in filter(None, texte.split(",")):
        champs = part.split("#")
        try:
            ident = int(champs[0], 16)
            valeur = int(champs[1], 16) if len(champs) > 1 and champs[1] else 0
        except ValueError:
            continue
        out[ident] = out.get(ident, 0) + valeur
    return out


def objets(texte: str, ou: str, porteur: str) -> list[Objet]:
    out = []
    for bloc in filter(None, re.split(r"[;]", texte)):
        bloc = bloc[1:] if bloc.startswith("O") else bloc
        champs = bloc.split("~")
        if len(champs) < 4:
            continue
        try:
            modele, position = int(champs[1], 16), int(champs[3], 16) if champs[3] else -1
        except ValueError:
            continue
        out.append(Objet(modele, position, effets(champs[4] if len(champs) > 4 else ""), ou, porteur))
    return out


def lire(logs: Path = LOGS) -> tuple[dict[str, Perso], list[Objet]]:
    persos: dict[str, Perso] = {}
    sacs: dict[str, list[Objet]] = {}
    coffres: dict[str, tuple[str, list[Objet]]] = {}
    for journal in sorted(logs.glob("*/reseau_*.txt")):
        session = journal.parent.name
        nom_journal = journal.stem.removeprefix("reseau_")
        for ligne in journal.read_text(encoding="utf-8", errors="replace").splitlines():
            m = LINE.match(ligne)
            if not m:
                continue
            texte = m.group(2)
            if texte.startswith("ASK|"):
                champs = texte.split("|")
                if len(champs) < 11:
                    continue
                nom = champs[2]
                p = Perso(nom, int(champs[3]), f"{session} {m.group(1)}")
                items = objets(champs[10], "", nom)
                for o in items:
                    o.ou = f"porté par {nom}" if o.position in SLOTS else f"sac de {nom}"
                p.portes = {o.position: o for o in items if o.position in SLOTS}
                persos[nom] = p
                sacs[nom] = [o for o in items if o.position not in SLOTS]
            elif texte.startswith("EL") and not texte.startswith("ELE"):
                nom = nom_journal.replace("_Gal_", "[Gal]").replace("-[Gal]", "-[Gal]")
                coffres[nom] = (f"{session} {m.group(1)}", objets(texte[2:], f"coffre de {nom}", nom))
    tout = [o for items in sacs.values() for o in items] + [o for _, items in coffres.values() for o in items]
    return persos, tout


def rapport(dps: set[str], logs: Path = LOGS) -> list[str]:
    from tools.langue_client import LangueClient

    items = LangueClient().table("I")["u"]
    persos, reserve = lire(logs)
    nom = lambda m: items.get(str(m), {}).get("n", str(m))  # noqa: E731
    niveau = lambda m: int(items.get(str(m), {}).get("l", 0) or 0)  # noqa: E731
    emplacement = lambda m: TYPE_SLOT.get(int(items.get(str(m), {}).get("t", 0) or 0))  # noqa: E731
    # Sac d'un personnage hors de l'équipe : pas joignable (son coffre, celui du compte, l'est)
    sages = [o for o in reserve if o.sagesse > 0 and emplacement(o.modele)
             and not (o.ou.startswith("sac") and o.porteur in EXCLUS)]
    lignes = ["# Sagesse des soutiens (lecture des journaux réseau)", ""]
    for p in sorted(persos.values(), key=lambda p: p.nom):
        if p.nom in dps:
            lignes.append(f"## {p.nom} (DPS, niveau {p.niveau}) : pas touché")
            lignes.append("")
            continue
        total = sum(o.sagesse for o in p.portes.values())
        lignes.append(f"## {p.nom} (niveau {p.niveau}, sac lu {p.vu}) : +{total} sagesse portée")
        for slot in ("amulette", "anneau", "ceinture", "bottes", "chapeau", "cape", "familier", "bouclier", "arme"):
            ici = [o for pos, o in p.portes.items() if SLOTS[pos] == slot]
            actuel = max((o.sagesse for o in ici), default=0) if slot != "anneau" else min(
                (o.sagesse for o in ici), default=0) if len(ici) == 2 else 0
            mieux = sorted((o for o in sages if emplacement(o.modele) == slot and niveau(o.modele) <= p.niveau
                            and o.sagesse > actuel and not (o.porteur in dps and o.ou.startswith("porté"))),
                           key=lambda o: -o.sagesse)
            porte = ", ".join(f"{nom(o.modele)} ({o.sagesse:+d})" for o in ici) or "rien"
            lignes.append(f"- {slot} : {porte}" + (
                "" if not mieux else " ; mieux : " + "; ".join(
                    f"{nom(o.modele)} niv. {niveau(o.modele)} ({o.sagesse:+d}, {o.ou})" for o in mieux[:3])))
        lignes.append("")
    lignes += repartition(persos, sages, dps, nom, niveau, emplacement)
    return lignes


def repartition(persos, sages, dps, nom, niveau, emplacement) -> list[str]:
    """Chaque objet proposé une seule fois : le plus gros gain de sagesse d'abord, parmi tous les soutiens et leurs
    emplacements (deux anneaux)."""
    places = []  # (soutien, emplacement, sagesse actuelle, objet porté)
    for p in persos.values():
        if p.nom in dps or p.nom not in TEAM_OK(persos, dps):
            continue
        for slot in ("amulette", "ceinture", "bottes", "chapeau", "cape", "familier", "bouclier", "arme"):
            ici = [o for pos, o in p.portes.items() if SLOTS[pos] == slot]
            places.append((p, slot, max((o.sagesse for o in ici), default=0), ici[0] if ici else None))
        anneaux = [o for pos, o in p.portes.items() if SLOTS[pos] == "anneau"]
        for k in range(2):
            o = anneaux[k] if k < len(anneaux) else None
            places.append((p, "anneau", o.sagesse if o else 0, o))
    pris, choix = set(), []
    while True:
        meilleur = None
        for index, (p, slot, actuel, porte) in enumerate(places):
            if index in {c[0] for c in choix}:
                continue
            for o in sages:
                if id(o) in pris or emplacement(o.modele) != slot or niveau(o.modele) > p.niveau or o.sagesse <= actuel:
                    continue
                if o.porteur in dps and o.ou.startswith("porté"):
                    continue
                gain = o.sagesse - actuel
                if meilleur is None or gain > meilleur[0]:
                    meilleur = (gain, index, o)
        if meilleur is None:
            break
        gain, index, o = meilleur
        pris.add(id(o))
        choix.append((index, o, gain))
    lignes = ["## Proposition (chaque objet une fois, plus gros gain d'abord)", ""]
    par_perso: dict[str, list[str]] = {}
    for index, o, gain in choix:
        p, slot, actuel, porte = places[index]
        par_perso.setdefault(p.nom, []).append(
            f"  - {slot} : {nom(o.modele)} ({o.sagesse:+d}, {o.ou}) à la place de "
            f"{nom(porte.modele) + f' ({porte.sagesse:+d})' if porte else 'rien'} : +{gain}")
    for perso in sorted(par_perso):
        total = sum(g for i, _, g in choix if places[i][0].nom == perso)
        lignes.append(f"- **{perso}** : +{total} sagesse")
        lignes += par_perso[perso]
    return lignes


def TEAM_OK(persos, dps):  # noqa: N802 - soutiens de l'équipe du jour (Fourbodeux et Waldhary n'y sont pas)
    return {n for n in persos if n not in dps} - EXCLUS


EXCLUS = {"Fourbodeux", "Waldhary-[Gal]"}  # pas dans l'équipe du 08/11 (remplacés par Walarbalete et Fourboun)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dps", default="Walarbalete", help="DPS, séparés par des virgules (pas touchés)")
    parser.add_argument("--sortie", help="fichier Markdown où écrire le rapport")
    args = parser.parse_args(argv)
    lignes = rapport({n.strip() for n in args.dps.split(",") if n.strip()})
    texte = "\n".join(lignes)
    if args.sortie:
        Path(args.sortie).write_text(texte + "\n", encoding="utf-8")
    print(texte)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
