"""Référence des donjons (30/10, demande de l'utilisateur : « scanner la référence pour voir si on a l'info des
donjons, nombre de salles... et se faire une référence dédiée »), tirée de la langue du client seulement (hors ligne,
lecture seule de data/client_retro) :

- table DU : 54 donjons (nom, niveau, sous-zone) ; pour 7 d'entre eux, la liste des salles (carte, nom, place sur le
  plan du donjon, icône : 900 entrée, 901 sortie / boss, 905...) ;
- table MA.m : chaque carte avec sa sous-zone (sa), ses coordonnées, ses cases de placement des deux équipes (p1, p2)
  et un drapeau donjon (d) ; les salles des autres donjons sont les cartes de leur sous-zone ;
- table I : la clé du donjon (type « Clefs », nom du donjon dans le nom ou la description de la clé :
  brain/inventory.py dungeon_key).

Écrit data/donjons.json (local, jamais distribué) et docs/rapports/donjons_reference.md (tableau).

    .venv\\Scripts\\python -m tools.donjons_reference           # écrit les deux fichiers
    .venv\\Scripts\\python -m tools.donjons_reference --donjon bouftou   # détail d'un donjon
"""

import argparse
import json
import sys
import unicodedata
from pathlib import Path

from bot.config import ROOT
from tools.cartes_client import lire_sol

LANGUE = ROOT / "data" / "client_retro" / "lang"
SORTIE = ROOT / "data" / "donjons.json"
RAPPORT = ROOT / "docs" / "rapports" / "donjons_reference.md"
ICONES = {900: "entrée", 901: "sortie", 904: "boss", 905: "salle", 906: "salle"}  # à vérifier en jeu (DU.m[].i)


def _table(nom: str) -> dict:
    return lire_sol(LANGUE / f"ANKXTRASO_0_{nom}.sol")["_Data"][nom]


def _plain(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte.lower()) if unicodedata.category(c) != "Mn")


VIDES = {"donjon", "des", "du", "de", "la", "le", "les", "d", "l"}


def _mots(texte: str) -> set[str]:
    """Mots significatifs, sans accents ni pluriel en s (« Donjon Bouftou » et « Clef du Donjon des Bouftous »)."""
    mots = "".join(c if c.isalnum() else " " for c in _plain(texte)).split()
    return {m.rstrip("s") for m in mots if m not in VIDES and len(m) > 2}


def _cle_par_mots(nom: str, noms_cles: dict[int, str]) -> int | None:
    """Clé dont le nom contient tous les mots significatifs du nom du donjon ; None si aucune ou plusieurs."""
    voulus = _mots(nom)
    trouvees = [k for k, n in noms_cles.items() if voulus and voulus <= _mots(n)]
    return trouvees[0] if len(trouvees) == 1 else None


def _cases(codes: str) -> int:
    return len(codes or "") // 2  # deux caractères de l'alphabet du client par case


def construire() -> list[dict]:
    donjons, cartes = _table("DU"), _table("MA")["m"]
    try:
        from bot.brain.inventory import dungeon_key, key_names

        noms_cles = key_names()
    except Exception:  # langue des objets absente
        dungeon_key, noms_cles = (lambda nom: None), {}
    par_sous_zone: dict[int, list[str]] = {}
    for numero, carte in cartes.items():
        par_sous_zone.setdefault(carte.get("sa"), []).append(numero)
    sortie = []
    for numero, donjon in sorted(donjons.items(), key=lambda kv: int(kv[0])):
        salles_du = donjon.get("m") or {}
        source = "liste du donjon" if salles_du else "sous-zone"
        ids = list(salles_du) if salles_du else sorted(par_sous_zone.get(donjon.get("sba"), []), key=int)
        salles = []
        for carte_id in ids:
            carte, salle = cartes.get(str(carte_id), {}), salles_du.get(str(carte_id), {})
            salles.append({
                "carte": int(carte_id), "nom": salle.get("n") or "", "plan": [salle.get("x"), salle.get("y")]
                if salle else None, "icone": ICONES.get(salle.get("i"), salle.get("i")) if salle.get("i") else None,
                "coords": [carte.get("x"), carte.get("y")] if carte else None, "donjon": bool(carte.get("d")),
                "placement": [_cases(carte.get("p1")), _cases(carte.get("p2"))] if carte else None,
            })
        cle = (dungeon_key(donjon.get("n", "")) if donjon.get("n") else None) or _cle_par_mots(donjon.get("n", ""),
                                                                                             noms_cles)
        sortie.append({
            "id": int(numero), "nom": donjon.get("n", ""), "niveau": donjon.get("lvl"), "sous_zone": donjon.get("sba"),
            "salles_source": source, "nb_salles": len(salles), "cle": cle, "cle_nom": noms_cles.get(cle),
            "salles": salles,
        })
    return sortie


def rapport(donjons: list[dict]) -> str:
    lignes = ["# Référence des donjons (langue du client, 30/10)", "",
              "Tirée de la langue du client (tables DU, MA, I) par `tools/donjons_reference.py` ; détail par salle dans "
              "`data/donjons.json` (carte, nom, place sur le plan, coordonnées, cases de placement des deux équipes).", "",
              "Pas dans la langue du client (le serveur les décide) : les monstres de chaque salle, le nombre de "
              "personnages par combat. Les cases de placement donnent un majorant : nombre de cases de l'équipe.", "",
              "| Donjon | Niveau | Salles | Source | Clé | Places (équipe 1 / 2) |", "|---|---|---|---|---|---|"]
    for d in donjons:
        places = sorted({tuple(s["placement"]) for s in d["salles"] if s["placement"]})
        lignes.append(f"| {d['nom']} | {d['niveau']} | {d['nb_salles']} | {d['salles_source']} | "
                      f"{d['cle_nom'] or '—'} | {', '.join(f'{a}/{b}' for a, b in places[:3]) or '—'} |")
    return "\n".join(lignes) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--donjon", help="nom (ou morceau du nom) d'un donjon à détailler")
    args = parser.parse_args()
    donjons = construire()
    if args.donjon:
        for d in donjons:
            if _plain(args.donjon) in _plain(d["nom"]):
                print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    SORTIE.write_text(json.dumps(donjons, ensure_ascii=False, indent=1), encoding="utf-8")
    RAPPORT.write_text(rapport(donjons), encoding="utf-8")
    print(f"{len(donjons)} donjons : {SORTIE}, {RAPPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
