"""Contrôle de la référence de combat (tools/reference_combat.py) contre les captures de combat des journaux (24/10).

    .venv/Scripts/python -m tools.controle_combat [--sessions logs/2026102*]

Pour chaque capture de combat en mode tactique sans portée affichée : sol, blocs et trous lus à l'écran comme le bot
les lit (bot/perception/sight.read_terrain), comparés à chaque carte de la référence. La carte qui colle le mieux est
retenue ; l'accord sur elle mesure la véracité de la référence (et de la lecture). Sur les captures de placement, les
cases de placement lues (bot/perception/placement) sont comparées à celles de StarLoco. Rapport :
docs/rapports/veracite_reference_combat.md.
"""

import argparse
import glob
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from bot.perception.fighters import find_fighters
from bot.perception.frame import Frame
from bot.perception.placement import entry_banner, read_placement
from bot.perception.sight import read_terrain
from bot.perception.ui import spell_range_visible, tactical_mode
from tools.reference_combat import REGIONS, fichier

RACINE = Path(__file__).resolve().parents[1]
RAPPORT = RACINE / "docs" / "rapports" / "veracite_reference_combat.md"
SOL, BLOC, TROU = 0, 1, 2
TYPES = ("combat", "tour", "deplacement", "reperage_portee", "ligne_de_vue_a_revoir", "qui_est_moi")
UNIQUE_MARGIN = 0.03  # avance d'accord qu'il faut à la meilleure carte sur la suivante pour la dire reconnue


def charger() -> tuple[list[str], list[str], np.ndarray, list[dict]]:
    """(identifiants, coordonnées, matrice cartes x cases : SOL / BLOC / TROU, entrées) de toutes les régions."""
    idents, coords, lignes, entrees = [], [], [], []
    for region in REGIONS.values():
        if not fichier(region).exists():
            continue
        for ident, entree in json.loads(fichier(region).read_text(encoding="utf-8")).items():
            ligne = np.zeros((29, 40), dtype=np.int8)
            for i, j in (c for c in entree["blocs"] if c[0] < 29 and c[1] < 40):
                ligne[i, j] = BLOC
            for i, j in (c for c in entree["trous"] if c[0] < 29 and c[1] < 40):
                ligne[i, j] = TROU
            idents.append(ident)
            coords.append(f"{region} {entree['carte']}")
            lignes.append(ligne.reshape(-1))
            entrees.append(entree)
    return idents, coords, np.stack(lignes), entrees


def lu(frame: Frame) -> tuple[np.ndarray, np.ndarray] | None:
    """Cases lues à l'écran (indices dans la matrice) et leur nature, ou None si la capture ne s'y prête pas."""
    if not tactical_mode(frame) or spell_range_visible(frame):
        return None
    terrain = read_terrain(frame, find_fighters(frame))
    cases, natures = [], []
    for ensemble, nature in ((terrain.floor, SOL), (terrain.blocks, BLOC), (terrain.holes, TROU)):
        for i, j in ensemble:
            if 0 <= i < 29 and 0 <= j < 40:
                cases.append(i * 40 + j)
                natures.append(nature)
    return (np.array(cases), np.array(natures)) if len(cases) >= 50 else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions", default=str(RACINE / "logs" / "2026*"))
    args = parser.parse_args()
    idents, coords, matrice, entrees = charger()
    marche = matrice != SOL  # vrai : on ne peut pas y marcher
    vue = matrice == BLOC  # vrai : coupe la ligne de vue
    total, reconnues, cartes = Counter(), 0, Counter()
    placements = Counter()
    lignes_rapport = []
    captures = sorted(c for t in TYPES for c in glob.glob(str(Path(args.sessions) / f"*_{t}.*")))
    vus_placement: list[tuple[str, set]] = []  # (capture, cases de placement lues) en attente de la carte reconnue
    for chemin in captures:
        image = cv2.imread(chemin)
        if image is None:
            continue
        frame = Frame.of(image)
        placement = read_placement(frame)
        cases_placement = set().union(*placement.free.values(), *placement.occupied.values())
        if len(cases_placement) >= 4:
            vus_placement.append((chemin, cases_placement))
            continue
        lecture = lu(frame)
        if lecture is None:
            continue
        cases, natures = lecture
        accord = (matrice[:, cases] == natures).mean(axis=1)
        ordre = np.argsort(-accord)
        meilleure, seconde = ordre[0], ordre[1]
        total["captures"] += 1
        unique = accord[meilleure] - accord[seconde] >= UNIQUE_MARGIN
        if not unique:
            continue
        reconnues += 1
        cartes[coords[meilleure]] += 1
        # Placements vus juste avant dans la même session : même combat, même carte
        officiels = {tuple(c) for camp in entrees[meilleure].get("placements") or [] for c in camp}
        for vu_chemin, vues in vus_placement:
            if Path(vu_chemin).parent == Path(chemin).parent and officiels:
                placements["captures"] += 1
                placements["cases_lues"] += len(vues)
                placements["dans_starloco"] += len(vues & officiels)
                placements["starloco"] += len(officiels)
        vus_placement.clear()
        # Accord marche et ligne de vue sur la carte reconnue
        ecran_marche, ecran_vue = natures != SOL, natures == BLOC
        total["cases"] += len(cases)
        total["marche_ok"] += int((marche[meilleure, cases] == ecran_marche).sum())
        total["vue_ok"] += int((vue[meilleure, cases] == ecran_vue).sum())
        total["bloc_officiel_lu_sol"] += int(((matrice[meilleure, cases] == BLOC) & (natures == SOL)).sum())
        total["sol_officiel_lu_bloc"] += int(((matrice[meilleure, cases] == SOL) & (natures == BLOC)).sum())
        lignes_rapport.append(f"| {Path(chemin).parent.name}/{Path(chemin).name} | {coords[meilleure]} "
                              f"({idents[meilleure]}) | {accord[meilleure]:.1%} | {accord[seconde]:.1%} |")
    print(f"{total['captures']} captures lisibles, {reconnues} cartes reconnues sans ambiguïté")
    if total["cases"]:
        print(f"marche : {total['marche_ok'] / total['cases']:.1%}, ligne de vue : {total['vue_ok'] / total['cases']:.1%} "
              f"sur {total['cases']} cases ; blocs officiels lus sol : {total['bloc_officiel_lu_sol']}, "
              f"sols officiels lus blocs : {total['sol_officiel_lu_bloc']}")
    print("cartes :", dict(cartes.most_common(20)))
    print("placements :", dict(placements))
    RAPPORT.write_text("\n".join([
        "# Véracité de la référence de combat (cartes officielles déchiffrées)",
        "",
        "Produit par `tools/controle_combat.py` : terrain lu à l'écran (mode tactique) comparé à chaque carte de la",
        "référence ; la carte qui colle le mieux (avance d'au moins 3 points sur la suivante) est retenue.",
        "",
        f"- Captures lisibles : {total['captures']} ; cartes reconnues sans ambiguïté : {reconnues}",
        f"- Accord marche : {total['marche_ok'] / max(1, total['cases']):.1%} ; ligne de vue : "
        f"{total['vue_ok'] / max(1, total['cases']):.1%} ({total['cases']} cases)",
        f"- Blocs officiels lus comme du sol : {total['bloc_officiel_lu_sol']} ; sols officiels lus comme des blocs : "
        f"{total['sol_officiel_lu_bloc']}",
        f"- Cartes : " + ", ".join(f"{k} x{v}" for k, v in cartes.most_common()),
        f"- Placements (StarLoco) : {placements['captures']} captures de placement ; {placements['dans_starloco']} cases "
        f"lues sur {placements['cases_lues']} sont des placements StarLoco de la carte reconnue",
        "",
        "| Capture | Carte reconnue | Accord | Seconde carte |",
        "|---|---|---|---|",
        *lignes_rapport,
        "",
    ]), encoding="utf-8")


if __name__ == "__main__":
    main()
