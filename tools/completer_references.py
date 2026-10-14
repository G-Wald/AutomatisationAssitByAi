"""Complète les références (config/monde/*.json) avec ce que les fichiers du client donnent EN CLAIR.

Source : l'index des cartes (data/client_retro/cartes/, `python -m tools.cartes_index --construire`) ; aucune donnée
chiffrée. Demande de l'utilisateur, 12/10 : « compléter les références ». Champs ajoutés à chaque carte :

- `id` : identifiant de la carte du jeu. C'est la carte extérieure ; s'il y en a plusieurs (couches), celle de la zone
  de la référence. Incarnam a déjà ses ids : gardés.
- `autres_ids` : les autres cartes aux mêmes coordonnées (intérieurs, couches, salles de donjon). Le nom de carte
  affiché à l'écran ne les distingue pas.
- `soleils_client` : côté -> cases (repère (i, j) du bot) des soleils dont la langue donne la case (champ p,
  soleils « conditionnels », vus en jeu à la case annoncée).
- `portes` : portes de maisons, [{case, maison}].
- `placements` : cases de départ des deux équipes en combat, {"p1": [...], "p2": [...]}.

Seuls ces champs sont écrits (remplacés à chaque passage) ; le reste de la référence ne change pas. Copie de chaque
fichier avant la première écriture (data/client_retro/<fichier>_avant_completion.json). À relancer après
tools/corriger_monde.py, qui repart de l'original et n'a pas ces champs.

    .venv\\Scripts\\python -m tools.completer_references            (bilan seulement)
    .venv\\Scripts\\python -m tools.completer_references --ecrire
"""

import argparse
import json
import sys
from pathlib import Path

from tools.cartes_client import DOSSIER, normaliser
from tools.cartes_index import case_bot

RACINE = Path(__file__).resolve().parents[1]
MONDE = RACINE / "config" / "monde"
INDEX = DOSSIER / "cartes"
REFERENCES = {"monde_des_douze.json": 0, "incarnam.json": 3}  # fichier -> super-zone de la langue
CHAMPS = ("id", "autres_ids", "soleils_client", "portes", "placements")


def _cote(case: list[int] | None) -> str | None:
    """Bord d'une carte de 15 x 17 (repère (i, j) du bot) : là où sont les soleils de sortie."""
    if not case:
        return None
    i, j = case
    if j <= 1:
        return "haut"
    if j >= 31:
        return "bas"
    if i <= 1:
        return "gauche"
    if i >= 27:
        return "droite"
    return None


def completer(reference: dict, super_zone: int, index: dict, coordonnees: dict, objets: dict,
              placements: dict) -> tuple[dict, dict]:
    """(référence complétée, bilan). Ne modifie pas `reference`."""
    cartes = {}
    bilan = {"cartes": 0, "id": 0, "autres_ids": 0, "soleils_client": 0, "portes": 0, "placements": 0, "sans_id": []}
    for cle, entree in reference.get("cartes", {}).items():
        entree = {k: v for k, v in entree.items() if k not in CHAMPS[1:]}  # champs de ce script : recalculés
        bilan["cartes"] += 1
        ids = [int(i) for i in coordonnees.get(cle, []) if index.get(str(i), {}).get("super_zone") == super_zone]
        principal = entree.get("id")
        if principal is None:
            exterieurs = [i for i in ids if index[str(i)].get("exterieur")]
            voulu = normaliser(entree.get("zone"))
            meme_zone = [i for i in exterieurs if normaliser(index[str(i)].get("nom_sous_zone")) == voulu]
            principal = (meme_zone or exterieurs or [None])[0]
        if principal is None:
            bilan["sans_id"].append(cle)
            cartes[cle] = entree
            continue
        entree["id"] = principal
        bilan["id"] += 1
        autres = [i for i in ids if i != principal]
        if autres:
            entree["autres_ids"] = autres
            bilan["autres_ids"] += 1
        largeur = index.get(str(principal), {}).get("largeur", 15)
        trouve = objets.get(str(principal)) or {}
        soleils: dict[str, list[list[int]]] = {}
        for soleil in trouve.get("soleils_conditionnels", []):
            cote = _cote(soleil.get("case_bot"))
            if cote:
                soleils.setdefault(cote, []).append(soleil["case_bot"])
        if soleils:
            entree["soleils_client"] = soleils
            bilan["soleils_client"] += 1
        portes = [{"case": case_bot(p["case"], largeur), "maison": p.get("nom")} for p in trouve.get("portes_de_maisons", [])]
        portes = [p for p in portes if p["case"]]
        if portes:
            entree["portes"] = portes
            bilan["portes"] += 1
        place = placements.get(str(principal)) or {}
        # Cases sans position dans le repère du bot (carte qui n'a pas 15 cases de large) : écartées.
        equipes = {e: [c for c in (place.get(e) or {}).get("cases_bot") or [] if c] for e in ("p1", "p2")}
        if all(equipes.values()):
            entree["placements"] = equipes
            bilan["placements"] += 1
        cartes[cle] = entree
    complete = dict(reference)
    complete["cartes"] = cartes
    source = dict(reference.get("source") or {})
    source["complete_le"] = "2026-10-12"
    source["completion"] = ("id, autres_ids, soleils_client, portes, placements : données en clair du client Dofus "
                            "Retro 1.49.6 (tools/completer_references.py) ; rien de chiffré")
    complete["source"] = source
    return complete, bilan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ecrire", action="store_true", help="écrit les références complétées (copie avant)")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    def lire(nom: str):
        return json.loads((INDEX / nom).read_text(encoding="utf-8"))

    index, coordonnees = lire("index.json"), lire("coordonnees.json")
    objets, placements = lire("objets_langue.json"), lire("placements.json")
    for fichier, super_zone in REFERENCES.items():
        chemin = MONDE / fichier
        texte = chemin.read_text(encoding="utf-8")
        complete, bilan = completer(json.loads(texte), super_zone, index, coordonnees, objets, placements)
        sans = bilan.pop("sans_id")
        print(f"{fichier} : " + ", ".join(f"{k} {v}" for k, v in bilan.items())
              + (f" ; sans carte extérieure dans le client : {', '.join(sans[:10])}{' …' if len(sans) > 10 else ''}"
                 if sans else ""))
        if args.ecrire:
            copie = DOSSIER / f"{chemin.stem}_avant_completion.json"
            if not copie.exists():
                copie.write_text(texte, encoding="utf-8")
            chemin.write_text(json.dumps(complete, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  écrit (original : {copie})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
