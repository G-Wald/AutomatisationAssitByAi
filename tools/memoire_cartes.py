"""Mémoire des cartes apprise en jeu (data/carte.json) : sauvegarde, oubli de cartes choisies, contrôle de cohérence.

Pour le plan de tests (docs/plan_de_tests.md) : D2 rejoue des cartes « mémoire vide », D8 contrôle la mémoire après
chaque session. Toute modification est précédée d'une copie dans data/carte_sauvegardes/<date_heure>.json.

    .venv\\Scripts\\python -m tools.memoire_cartes --sauvegarder
    .venv\\Scripts\\python -m tools.memoire_cartes --oublier -1,-19 2,-14       (région : Monde des Douze par défaut)
    .venv\\Scripts\\python -m tools.memoire_cartes --controle
    .venv\\Scripts\\python -m tools.memoire_cartes --restaurer data/carte_sauvegardes/20261012_221500.json
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
MEMOIRE = RACINE / "data" / "carte.json"
SAUVEGARDES = RACINE / "data" / "carte_sauvegardes"
BLOQUEE = "bloquée"
DELTA = {"haut": (0, -1), "bas": (0, 1), "gauche": (-1, 0), "droite": (1, 0)}
OPPOSE = {"haut": "bas", "bas": "haut", "gauche": "droite", "droite": "gauche"}


def lire(chemin: Path = MEMOIRE) -> dict:
    return json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else {}


def ecrire(cartes: dict, chemin: Path = MEMOIRE) -> None:
    """Même format que le bot (bot/brain/travel.py, World.save)."""
    chemin.write_text(json.dumps(cartes, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def sauvegarder(chemin: Path = MEMOIRE, dossier: Path = SAUVEGARDES) -> Path | None:
    if not chemin.exists():
        return None
    dossier.mkdir(parents=True, exist_ok=True)
    copie = dossier / f"{datetime.now():%Y%m%d_%H%M%S}.json"
    shutil.copyfile(chemin, copie)
    return copie


def cle(region: str, coords: str) -> str:
    return f"{region} {coords.strip()}"


def oublier(cartes: dict, cles: list[str]) -> list[str]:
    """Retire ces cartes, et les liaisons des autres cartes vers elles ; renvoie celles qui étaient connues."""
    retirees = [k for k in cles if cartes.pop(k, None) is not None]
    for info in cartes.values():
        sorties = info.get("sorties") or {}
        for cote in [c for c, dest in sorties.items() if dest in cles]:
            del sorties[cote]
    return retirees


def _coords(texte: str) -> tuple[str, tuple[int, int]] | None:
    region, _, xy = texte.rpartition(" ")
    try:
        x, y = xy.split(",")
        return region, (int(x), int(y))
    except ValueError:
        return None


def controle(cartes: dict) -> list[str]:
    """Incohérences à revoir (rien n'est corrigé) :
    - liaison vers une carte qui n'est pas la voisine de ce côté (lecture fausse, sauf vrai saut du jeu) ;
    - sortie notée bloquée alors que la voisine y mène par le côté opposé (possible sur une carte coupée par un
      rempart : à vérifier selon le côté d'arrivée) ;
    - sortie inatteignable selon le côté d'arrivée, mais un soleil retenu pour ce même côté d'arrivée."""
    problemes = []
    for k, info in sorted(cartes.items()):
        ici = _coords(k)
        if ici is None:
            continue
        region, (x, y) = ici
        for cote, dest in (info.get("sorties") or {}).items():
            if not dest or dest == BLOQUEE or cote not in DELTA:
                continue
            la = _coords(dest)
            if la and la[0] == region and la[1] != (x + DELTA[cote][0], y + DELTA[cote][1]):
                problemes.append(f"{k} {cote} -> {dest} : pas la voisine de ce côté")
        for cote, dest in (info.get("sorties") or {}).items():
            if dest != BLOQUEE or cote not in DELTA:
                continue
            voisine = f"{region} {x + DELTA[cote][0]},{y + DELTA[cote][1]}"
            retour = ((cartes.get(voisine) or {}).get("sorties") or {}).get(OPPOSE[cote])
            if retour == k:
                problemes.append(f"{k} {cote} bloquée, mais {voisine} {OPPOSE[cote]} y mène (rempart ? à vérifier)")
        depuis = info.get("soleils_depuis") or {}
        for entree, cotes in (info.get("bloquees_depuis") or {}).items():
            for cote in cotes:
                if cote in (depuis.get(entree) or {}):
                    problemes.append(f"{k} {cote} inatteignable en arrivant par le {entree}, mais un soleil y est retenu")
    return problemes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sauvegarder", action="store_true", help="copie de data/carte.json dans data/carte_sauvegardes/")
    parser.add_argument("--oublier", nargs="+", metavar="X,Y", help="cartes à oublier (copie faite avant)")
    parser.add_argument("--region", default="Monde des Douze", help="région des cartes à oublier")
    parser.add_argument("--controle", action="store_true", help="incohérences de la mémoire (rien n'est modifié)")
    parser.add_argument("--restaurer", type=Path, help="remet une sauvegarde (copie de l'état actuel faite avant)")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if args.sauvegarder:
        print(f"Sauvegarde : {sauvegarder()}")
    if args.restaurer:
        print(f"État actuel gardé : {sauvegarder()}")
        shutil.copyfile(args.restaurer, MEMOIRE)
        print(f"Restauré : {args.restaurer}")
    if args.oublier:
        print(f"Sauvegarde : {sauvegarder()}")
        cartes = lire()
        retirees = oublier(cartes, [cle(args.region, c) for c in args.oublier])
        ecrire(cartes)
        print(f"Oubliées ({len(retirees)}) : {', '.join(retirees) or 'aucune (pas en mémoire)'}")
    if args.controle:
        cartes = lire()
        problemes = controle(cartes)
        print(f"{len(cartes)} cartes en mémoire ; {len(problemes)} point(s) à revoir")
        for p in problemes:
            print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
