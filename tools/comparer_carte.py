"""Compare ce que le bot lit en jeu sur -1,-19 (Cité d'Astrub, id 7331) aux cases du client, connues pour cette
seule carte (déchiffrée le 12/10 avec l'accord de l'utilisateur, aucune autre carte ne l'est).

L'outil ne fait que regarder : captures PrintWindow de la fenêtre, aucun clic, aucune touche envoyée au jeu.

Depuis la racine du projet :
    .venv\\Scripts\\python -m tools.comparer_carte --preparer CLAIR.txt     référence (data/, ignoré par git)
    .venv\\Scripts\\python -m tools.comparer_carte --soleils                 hors combat : soleils vus à l'écran
    .venv\\Scripts\\python -m tools.comparer_carte --combat 300              300 s : lit le terrain pendant un combat
    .venv\\Scripts\\python -m tools.comparer_carte --image capture.png       même analyse sur une capture enregistrée

--perso NOM : la fenêtre dont le titre contient NOM (défaut : la seule fenêtre du jeu, sinon la liste est affichée).

Référence (une entrée par case (i, j) du bot) : praticable = déplacement > 0 ; bloc = ni marche ni ligne de vue ;
trou = pas de marche mais ligne de vue ; soleil = objet 1 de dessin 1030 (les 5 soleils vérifiés en jeu le 12/10).
"""

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from bot.config import ROOT, load_settings
from bot.perception.exits import find_marked_exits, find_suns
from bot.perception.fighters import find_fighters
from bot.perception.frame import Frame
from bot.perception.grid import Cell, distance
from bot.perception.sight import Terrain, read_terrain
from bot.perception.ui import movement_zone, spell_range_visible, tactical_mode
from tools.cartes_client import case_vers_grille
from tools.cles_cartes import decoder_carte

ID_CARTE = 7331
REFERENCE = ROOT / "data" / "client_retro" / "reference_moins_1_moins_19.json"
SOLEIL_GFX = (1029, 1030)
PRES = 1  # cases d'écart tolérées entre un soleil de la référence et celui vu à l'écran


def reference_depuis_clair(clair: str) -> dict:
    """Cases utiles de la carte, sans la clé ni le mapData : praticables, blocs, trous, soleils, en cases (i, j)."""
    cases = decoder_carte(clair)
    praticables, blocs, trous, soleils = [], [], [], []
    for n, case in enumerate(cases):
        if not case["active"]:
            continue
        ij = list(case_vers_grille(n))
        if case["deplacement"] > 0:
            praticables.append(ij)
        elif case["ligne_de_vue"]:
            trous.append(ij)
        else:
            blocs.append(ij)
        if case["objet1"] in SOLEIL_GFX or case["objet2"] in SOLEIL_GFX:
            soleils.append(ij)
    return {"carte": ID_CARTE, "coordonnees": [-1, -19], "cases": len(cases), "praticables": praticables,
            "blocs": blocs, "trous": trous, "soleils": soleils}


def charger_reference(chemin: Path = REFERENCE) -> dict:
    data = json.loads(chemin.read_text(encoding="utf-8"))
    for cle in ("praticables", "blocs", "trous", "soleils"):
        data[cle] = {tuple(c) for c in data[cle]}
    return data


def comparer_soleils(reference: dict, vus: list[Cell]) -> dict:
    """Chaque soleil de la référence : retrouvé à PRES case(s) près ou non ; soleils vus qui n'en sont pas."""
    attendus = sorted(reference["soleils"])
    trouves = {s: min(vus, key=lambda v: distance(s, v)) for s in attendus
               if vus and min(distance(s, v) for v in vus) <= PRES}
    faux = sorted(v for v in vus if all(distance(s, v) > PRES for s in attendus))
    return {"attendus": attendus, "trouves": trouves, "manques": [s for s in attendus if s not in trouves],
            "faux": faux}


def nature_reference(reference: dict, cell: Cell) -> str | None:
    if cell in reference["praticables"]:
        return "sol"
    if cell in reference["blocs"]:
        return "bloc"
    if cell in reference["trous"]:
        return "trou"
    return None  # hors de la carte du client


def nature_lue(terrain: Terrain, cell: Cell) -> str | None:
    if cell in terrain.floor:
        return "sol"
    if cell in terrain.blocks:
        return "bloc"
    if cell in terrain.holes:
        return "trou"
    return None


def comparer_terrain(reference: dict, lectures: dict[Cell, str]) -> dict:
    """Tableau (référence, lu) -> nombre de cases, et la liste des cases en désaccord. Le bot ne distingue que
    « on peut marcher » et « ça coupe la ligne de vue » : ces deux accords sont comptés à part."""
    tableau: Counter = Counter()
    desaccords = []
    marche = vue = total = 0
    for cell, lu in sorted(lectures.items()):
        ref = nature_reference(reference, cell)
        tableau[(ref or "hors carte", lu)] += 1
        if ref is None:
            continue
        total += 1
        marche += (ref == "sol") == (lu == "sol")
        vue += (ref == "bloc") == (lu == "bloc")
        if ref != lu:
            desaccords.append({"case": list(cell), "reference": ref, "lu": lu})
    return {"cases_lues": total, "accord_marche": marche / total if total else None,
            "accord_ligne_de_vue": vue / total if total else None,
            "tableau": {f"{r} -> {l}": n for (r, l), n in sorted(tableau.items())}, "desaccords": desaccords}


def carte_propre(frame: Frame) -> bool:
    """Carte tactique sans portée ni zone de déplacement affichée : celle que lit le combat (combat._learn_terrain)."""
    return tactical_mode(frame) and not spell_range_visible(frame) and not movement_zone(frame)


def voter(lectures: list[dict[Cell, str]]) -> dict[Cell, str]:
    """Nature la plus vue de chaque case sur plusieurs captures (un sprite ou une animation n'en gâche qu'une)."""
    votes: dict[Cell, Counter] = {}
    for lecture in lectures:
        for cell, nature in lecture.items():
            votes.setdefault(cell, Counter())[nature] += 1
    return {cell: c.most_common(1)[0][0] for cell, c in votes.items()}


def lire_terrain(frame: Frame) -> dict[Cell, str]:
    terrain = read_terrain(frame, find_fighters(frame))
    return {cell: nature for cell in terrain.seen if (nature := nature_lue(terrain, cell))}


# --- Fenêtre du jeu (lecture seule) -------------------------------------------------------------------------------

def fenetre(perso: str | None):
    from bot.capture.window import enable_dpi_awareness, find_windows

    enable_dpi_awareness()
    fenetres = find_windows(load_settings()["window"]["title"])
    if perso:
        fenetres = [f for f in fenetres if perso.lower() in f.title.lower()]
    if len(fenetres) != 1:
        print(f"{len(fenetres)} fenêtre(s) du jeu correspondent" + (f" à « {perso} »" if perso else "") + " :")
        for f in fenetres:
            print(f"  {f.title}")
        print("Précise --perso NOM.")
        return None
    return fenetres[0]


def capturer(window) -> Frame:
    from bot.capture.printwindow import WindowCapture

    return Frame.of(WindowCapture(window).grab())


def dossier_sortie() -> Path:
    dossier = ROOT / "logs" / f"comparer_carte_{datetime.now():%Y%m%d_%H%M%S}"
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier


def afficher_taille(frame: Frame) -> None:
    layout = frame.layout
    print(f"Fenêtre : zone de jeu {layout.width}x{layout.height} (référence 1918x1008, échelle {layout.scale:.3f})"
          + ("" if layout.matches_reference() else " : PROPORTIONS DIFFÉRENTES, la disposition peut ne pas coller"))


def afficher_soleils(titre: str, resultat: dict) -> None:
    print(f"{titre} : {len(resultat['trouves'])}/{len(resultat['attendus'])} soleils retrouvés")
    for s in resultat["attendus"]:
        vu = resultat["trouves"].get(s)
        print(f"  {s} : " + (f"vu en {vu}" if vu else "PAS VU"))
    if resultat["faux"]:
        print(f"  vus mais absents de la référence : {resultat['faux']}")


def analyser_soleils(reference: dict, frame: Frame) -> dict:
    sans_a = comparer_soleils(reference, [e.cell for e in find_suns(frame, every=True)])
    avec_a = comparer_soleils(reference, [e.cell for e in find_marked_exits(frame, every=True)])
    afficher_soleils("Soleils dessinés", sans_a)
    afficher_soleils("Contours de la touche A", avec_a)
    return {"sans_a": sans_a, "avec_a": avec_a}


def afficher_terrain(resultat: dict) -> None:
    if not resultat["cases_lues"]:
        print("Aucune case lue (pas de carte tactique propre).")
        return
    print(f"Terrain : {resultat['cases_lues']} cases lues ; accord marche {resultat['accord_marche']:.1%}, "
          f"accord ligne de vue {resultat['accord_ligne_de_vue']:.1%}")
    for ligne, n in resultat["tableau"].items():
        print(f"  {ligne:<20} {n}")
    for d in resultat["desaccords"][:40]:
        print(f"  {tuple(d['case'])} : référence {d['reference']}, lu {d['lu']}")


def _json(objet):
    if isinstance(objet, dict):
        return {(", ".join(map(str, k)) if isinstance(k, tuple) else k): _json(v) for k, v in objet.items()}
    if isinstance(objet, (list, tuple, set)):
        return [_json(v) for v in objet]
    return objet


def surveiller_combat(reference: dict, window, secondes: float, dossier: Path) -> dict:
    """Capture la fenêtre toutes les secondes ; chaque carte tactique propre est lue, puis les lectures votées."""
    lectures, fin, n = [], time.monotonic() + secondes, 0
    print(f"Surveillance du combat pendant {secondes:.0f} s (Ctrl+C pour arrêter plus tôt)...")
    try:
        while time.monotonic() < fin:
            try:
                frame = capturer(window)
            except RuntimeError:  # 15/10 : fenêtre réduite un instant, 47 lectures perdues avec le rapport
                time.sleep(1)
                continue
            if carte_propre(frame):
                lectures.append(lire_terrain(frame))
                if n < 5 or len(lectures) % 10 == 0:
                    cv2.imwrite(str(dossier / f"carte_propre_{len(lectures):03d}.png"), frame.image)
                    n += 1
                print(f"\r  cartes propres lues : {len(lectures)}", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    print()
    resultat = comparer_terrain(reference, voter(lectures)) if lectures else comparer_terrain(reference, {})
    resultat["captures_lues"] = len(lectures)
    afficher_terrain(resultat)
    return resultat


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--preparer", type=Path, metavar="CLAIR", help="écrit la référence depuis le mapData en clair")
    actions.add_argument("--soleils", action="store_true", help="capture hors combat et compare les soleils")
    actions.add_argument("--combat", type=float, metavar="SECONDES", help="lit le terrain pendant un combat")
    actions.add_argument("--image", type=Path, help="analyse une capture enregistrée (soleils et terrain)")
    parser.add_argument("--perso", help="partie du titre de la fenêtre du personnage")
    args = parser.parse_args()

    if args.preparer:
        reference = reference_depuis_clair(args.preparer.read_text(encoding="latin-1").strip())
        REFERENCE.parent.mkdir(parents=True, exist_ok=True)
        REFERENCE.write_text(json.dumps(reference, ensure_ascii=False), encoding="utf-8")
        print(f"{REFERENCE.relative_to(ROOT)} : {len(reference['praticables'])} praticables, {len(reference['blocs'])} "
              f"blocs, {len(reference['trous'])} trous, soleils {reference['soleils']}")
        return 0
    if not REFERENCE.exists():
        print(f"Pas de référence ({REFERENCE.relative_to(ROOT)}) : lance d'abord --preparer.")
        return 1
    reference = charger_reference()

    if args.image:
        frame = Frame.of(cv2.imdecode(np.fromfile(args.image, np.uint8), cv2.IMREAD_COLOR))
        afficher_taille(frame)
        analyser_soleils(reference, frame)
        if carte_propre(frame):
            afficher_terrain(comparer_terrain(reference, lire_terrain(frame)))
        return 0

    window = fenetre(args.perso)
    if window is None:
        return 1
    dossier = dossier_sortie()
    frame = capturer(window)
    afficher_taille(frame)
    cv2.imwrite(str(dossier / "depart.png"), frame.image)
    rapport = {"fenetre": window.title, "taille": [frame.layout.width, frame.layout.height]}
    if args.soleils:
        rapport["soleils"] = analyser_soleils(reference, frame)
    else:
        rapport["terrain"] = surveiller_combat(reference, window, args.combat, dossier)
    (dossier / "rapport.json").write_text(json.dumps(_json(rapport), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Captures et rapport : {dossier.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
