"""Référence des passages entre cartes (config/monde/passages/<région>.json), à partir de ce que le bot a vu en jeu.

    .venv/Scripts/python -m tools.passages --exporter                  # data/carte.json -> référence (Monde des Douze)
    .venv/Scripts/python -m tools.passages --exporter --region Incarnam
    .venv/Scripts/python -m tools.passages --carte 5,-30                # ce que la référence et la mémoire en disent
    .venv/Scripts/python -m tools.passages --retirer 5,-30 droite       # retire une sortie fausse de la référence

Les cases des cartes du client sont chiffrées (docs/rapports/cles_cartes.md) : par où l'on passe ne se sait qu'en jeu.
La mémoire (data/carte.json) l'apprend à chaque trajet, mais une lecture ratée peut la fausser ; la référence garde ce
qui a été vu, relu à la main, et l'itinéraire préfère ces passages confirmés (brain/travel.py, Journey._cost).

Fusion : un passage pris (carte d'arrivée) l'emporte toujours sur « bloquée » ; une sortie « bloquée » de la mémoire
n'efface jamais un passage déjà dans la référence.
"""

import argparse
import json
from pathlib import Path

from bot.brain.travel import BLOCKED, PASSAGES_DIR, Passages

RACINE = Path(__file__).resolve().parents[1]
MEMOIRE = RACINE / "data" / "carte.json"


def exporter(memoire: dict, region: str, reference: dict | None = None) -> dict:
    """Passages de `region` vus dans la mémoire, fusionnés dans `reference` (copie) ; clés « x,y »."""
    out = json.loads(json.dumps(reference or {}))
    prefixe = f"{region} "
    for cle, carte in memoire.items():
        if not cle.startswith(prefixe):
            continue
        ici = cle[len(prefixe):]
        entree = out.setdefault(ici, {"sorties": {}})
        sorties = entree.setdefault("sorties", {})
        for cote, dest in (carte.get("sorties") or {}).items():
            if dest == BLOCKED:
                if sorties.get(cote) in (None, BLOCKED):
                    sorties[cote] = BLOCKED
            elif dest and dest.startswith(prefixe):
                sorties[cote] = dest[len(prefixe):]
        for arrivee, cotes in (carte.get("bloquees_depuis") or {}).items():
            deja = entree.setdefault("bloquees_depuis", {}).setdefault(arrivee, [])
            for cote in cotes:
                if cote not in deja:
                    deja.append(cote)
            deja.sort()
        if not sorties and not entree.get("bloquees_depuis"):
            out.pop(ici)
    return dict(sorted(out.items(), key=lambda kv: tuple(int(v) for v in kv[0].split(","))))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default="Monde des Douze")
    parser.add_argument("--exporter", action="store_true")
    parser.add_argument("--carte", metavar="X,Y")
    parser.add_argument("--retirer", nargs=2, metavar=("X,Y", "COTE"))
    args = parser.parse_args()
    chemin = Passages.path(args.region)
    reference = json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else {}
    if args.exporter:
        memoire = json.loads(MEMOIRE.read_text(encoding="utf-8"))
        nouvelle = exporter(memoire, args.region, reference)
        PASSAGES_DIR.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(nouvelle, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        passages = sum(1 for c in nouvelle.values() for d in c.get("sorties", {}).values() if d != BLOCKED)
        bloquees = sum(1 for c in nouvelle.values() for d in c.get("sorties", {}).values() if d == BLOCKED)
        print(f"{chemin} : {len(nouvelle)} cartes, {passages} passages, {bloquees} sorties bloquées")
    if args.retirer:
        ici, cote = args.retirer
        retire = (reference.get(ici) or {}).get("sorties", {}).pop(cote, None)
        chemin.write_text(json.dumps(reference, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{ici} {cote} : {retire!r} retiré" if retire is not None else f"{ici} {cote} : rien à retirer")
    if args.carte:
        memoire = json.loads(MEMOIRE.read_text(encoding="utf-8")) if MEMOIRE.exists() else {}
        print("référence :", json.dumps(reference.get(args.carte), ensure_ascii=False))
        print("mémoire   :", json.dumps((memoire.get(f"{args.region} {args.carte}") or {}).get("sorties"),
                                        ensure_ascii=False))


if __name__ == "__main__":
    main()
