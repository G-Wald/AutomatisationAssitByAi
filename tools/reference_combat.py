"""Référence de combat par carte : blocs, trous et cases de placement (24/10, demande de l'utilisateur : fiabiliser les
combats avec les cases bloquées du jeu).

    .venv/Scripts/python -m tools.reference_combat                 # construit data/references/combat_*.json
    .venv/Scripts/python -m tools.reference_combat --controle      # compare aux captures de combat des journaux

Sources (data/externes/, jamais versionnées ni redistribuées : aucune licence ; voir tools/reference_complete.py) :
- cartes officielles du client installé, déchiffrées avec les clés MapKeys-DR : pour chaque case, praticable ou non,
  ligne de vue ou non. Mêmes définitions que tools/comparer_carte.py (accord 99 % marche, 100 % ligne de vue en -1,-19
  le 14/10) : bloc = ni marche ni ligne de vue ; trou = pas de marche, ligne de vue (fosse, vide autour de la carte) ;
- StarLoco-Game (`map.positions`) : cases de placement des deux camps (émulateur : contrôlées contre l'écran).

Référence produite (data/references/combat_<région>.json, locale) : par identifiant de carte, ses coordonnées, ses
blocs et trous sur notre grille (bot/perception/grid.py), ses placements et le nombre et la taille des groupes de
monstres d'après StarLoco.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from tools.cartes_client import case_vers_grille
from tools.cles_cartes import dechiffrer_carte
from tools.reference_complete import INDEX, MAPDEF, REGIONS, SORTIE, lire_cles

RACINE = Path(__file__).resolve().parents[1]
EXTERNES = RACINE / "data" / "externes"
HASH = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"  # cases encodées sur 2 caractères (Dofus)
POSITIONS = re.compile(r'map\.positions\s*=\s*"([^"]*)"')
GROUPES = re.compile(r"map\.mobGroupsCount\s*=\s*(\d+)")
TAILLE = re.compile(r"map\.mobGroupsMaxSize\s*=\s*(\d+)")


def fichier(region: str) -> Path:
    return SORTIE / f"combat_{region.lower().replace(' ', '_')}.json"


def decoder_positions(texte: str) -> list[list[int]]:
    """« cMcO...|dbd6... » -> [[cases du camp 1], [cases du camp 2]] (numéros de case du client)."""
    camps = []
    for part in texte.split("|")[:2]:
        camps.append([HASH.index(part[k]) * 64 + HASH.index(part[k + 1]) for k in range(0, len(part) - 1, 2)
                      if part[k] in HASH and part[k + 1] in HASH])
    return camps


def lire_starloco() -> dict[int, dict]:
    cartes = {}
    for chemin in (EXTERNES / "starloco" / "scripts" / "data" / "maps").rglob("*.lua"):
        texte = chemin.read_text(encoding="utf-8", errors="replace")
        entete = MAPDEF.search(texte)
        if not entete:
            continue
        positions, groupes, taille = POSITIONS.search(texte), GROUPES.search(texte), TAILLE.search(texte)
        cartes[int(entete.group(1))] = {
            "date": entete.group(2),
            "placements": decoder_positions(positions.group(1)) if positions and positions.group(1) else [],
            "groupes": int(groupes.group(1)) if groupes else None,
            "taille_max": int(taille.group(1)) if taille else None,
        }
    return cartes


def construire() -> tuple[dict[str, dict], dict[str, Counter]]:
    index = {int(k): v for k, v in json.loads(INDEX.read_text(encoding="utf-8")).items()}
    cles, starloco = lire_cles(), lire_starloco()
    references, stats = {region: {} for region in REGIONS.values()}, {region: Counter() for region in REGIONS.values()}
    for ident, info in sorted(index.items()):
        region = REGIONS.get(info.get("super_zone"))
        if region is None or info.get("largeur", 15) != 15:
            continue
        s = stats[region]
        s["cartes"] += 1
        date_cle = cles.get(ident)
        if not date_cle or date_cle[0] != str(info.get("date") or date_cle[0]):
            s["sans_cle_de_cette_version"] += 1
            continue
        try:
            cases = dechiffrer_carte(ident, date_cle[1])[2]
        except Exception:  # fichier absent, clé d'une autre version
            s["non_dechiffrees"] += 1
            continue
        blocs, trous = [], []
        for n, case in enumerate(cases):
            if case["active"] and case["deplacement"] > 0:
                continue
            cellule = list(case_vers_grille(n))
            (trous if not case["active"] or case["ligne_de_vue"] else blocs).append(cellule)
        entree = {"carte": f"{info['x']},{info['y']}", "blocs": blocs, "trous": trous}
        loco = starloco.get(ident)
        if loco and loco["placements"]:
            same = loco["date"] == str(info.get("date") or loco["date"])
            entree["placements"] = [[list(case_vers_grille(n)) for n in camp] for camp in loco["placements"]]
            entree["placements_meme_version"] = same
            s["avec_placements"] += 1
            s["placements_meme_version"] += same
        if loco and loco["groupes"] is not None:
            entree["groupes"] = {"nombre": loco["groupes"], "taille_max": loco["taille_max"]}
        references[region][str(ident)] = entree
        s["dechiffrees"] += 1
    return references, stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--controle", action="store_true", help="compare aux captures de combat des journaux")
    args = parser.parse_args()
    if args.controle:
        from tools.controle_combat import main as controle
        controle()
        return
    references, stats = construire()
    SORTIE.mkdir(parents=True, exist_ok=True)
    for region, ref in references.items():
        fichier(region).write_text(json.dumps(ref, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"{fichier(region)} : {len(ref)} cartes ; {dict(stats[region])}")


if __name__ == "__main__":
    main()
