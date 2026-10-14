"""Scénario de trajets du plan de tests (docs/plan_de_tests.md) : toujours les mêmes étapes, dans le même ordre.

Chaque étape lance le bot en mode équipe vers une carte (`--aller`), en partant de la carte d'arrivée de l'étape
précédente. Le résultat de chaque étape est lu dans son journal (tools/bilan_sessions.py) : arrivé ou non, durée,
changements de carte réussis et ratés. Une étape ratée arrête le scénario, sauf avec --continuer. Une étape peut
d'abord faire oublier des cartes à la mémoire (test D2 « mémoire vide », copie de sauvegarde faite avant).

    .venv\\Scripts\\python -m tools.scenario_trajets config/scenarios/session1_deplacements.yaml --a-sec
    .venv\\Scripts\\python -m tools.scenario_trajets config/scenarios/session1_deplacements.yaml
    .venv\\Scripts\\python -m tools.scenario_trajets config/scenarios/session1_deplacements.yaml --depuis 12

Agit sur le jeu (par le bot) : seulement pendant une session de test ouverte par l'utilisateur. Entrée demandée avant
de commencer ; F12 arrête le bot comme d'habitude.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from tools import memoire_cartes
from tools.arriere_plan import confirm
from tools.bilan_sessions import LOGS_DIR, analyser_dossier

RACINE = Path(__file__).resolve().parents[1]
RESULTATS = RACINE / "data" / "bilans"


def charger(chemin: Path) -> dict:
    scenario = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    depart = scenario["depart"]
    for numero, etape in enumerate(scenario["etapes"], 1):
        etape["numero"] = numero
        etape["de"] = depart
        depart = etape["vers"]
    return scenario


def commande(scenario: dict, etape: dict) -> list[str]:
    equipe = scenario.get("equipe") or {}
    return [sys.executable, "-m", "bot", "--monde", equipe.get("monde", "douze"), "--equipe",
            "--principal", equipe["principal"], f"--aller={etape['vers']}", f"--position={etape['de']}",
            "--minutes", str(etape.get("minutes", 5)), "--lecture", "memoire", "--verbose"]


def resultat(dossier: Path | None, but: str) -> dict:
    """Trajet du principal lu dans le journal du lancement."""
    session = analyser_dossier(dossier) if dossier is not None else None
    x, y = (int(v) for v in but.split(","))
    trajets = [t for t in (session.trajets if session else []) if t.principal and t.but == (x, y)]
    if not trajets:
        return {"issue": "sans journal", "duree": None, "passages": 0, "rates": 0, "dossier": str(dossier)}
    t = trajets[-1]
    return {"issue": t.issue, "raison": t.raison, "duree": t.duree, "passages": t.passages, "rates": t.rates,
            "dossier": str(dossier)}


def nouveau_dossier(avant: set[str]) -> Path | None:
    dossiers = [d for d in LOGS_DIR.iterdir() if d.is_dir() and d.name not in avant] if LOGS_DIR.is_dir() else []
    dossiers = [d for d in dossiers if (d / "bot.log").is_file()]
    return max(dossiers, key=lambda d: d.name) if dossiers else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--a-sec", action="store_true", help="affiche les étapes et les commandes, sans rien lancer")
    parser.add_argument("--depuis", type=int, default=1, help="numéro de la première étape à jouer")
    parser.add_argument("--jusqua", type=int, default=None, help="numéro de la dernière étape à jouer")
    parser.add_argument("--continuer", action="store_true", help="ne pas s'arrêter à une étape ratée")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    scenario = charger(args.scenario)
    etapes = [e for e in scenario["etapes"] if e["numero"] >= args.depuis and (args.jusqua is None or e["numero"] <= args.jusqua)]
    for e in etapes:
        oubli = f" (oublier d'abord : {', '.join(e['oublier'])})" if e.get("oublier") else ""
        print(f"{e['numero']:>2}. {e.get('test', ''):<4} {e['de']:>7} -> {e['vers']:<7} {e.get('note', '')}{oubli}")
    if args.a_sec:
        print("\nCommande de la 1re étape :", " ".join(commande(scenario, etapes[0])) if etapes else "aucune")
        return 0
    if not etapes or not confirm(f"Scénario « {scenario.get('nom', args.scenario.stem)} » : {len(etapes)} trajets "
                                 "en équipe, en arrière-plan (F12 arrête le bot)."):
        return 1
    lignes, debut = [], datetime.now()
    for e in etapes:
        if e.get("oublier"):
            print(f"Sauvegarde de la mémoire : {memoire_cartes.sauvegarder()}")
            cartes = memoire_cartes.lire()
            oubliees = memoire_cartes.oublier(cartes, [memoire_cartes.cle("Monde des Douze", c) for c in e["oublier"]])
            memoire_cartes.ecrire(cartes)
            print(f"Oubliées : {', '.join(oubliees) or 'aucune'}")
        avant = {d.name for d in LOGS_DIR.iterdir()} if LOGS_DIR.is_dir() else set()
        lancement = time.monotonic()
        subprocess.run(commande(scenario, e), input="\n", text=True, cwd=RACINE,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        r = resultat(nouveau_dossier(avant), e["vers"])
        r.update(numero=e["numero"], test=e.get("test"), de=e["de"], vers=e["vers"],
                 total=round(time.monotonic() - lancement, 1))
        lignes.append(r)
        duree = f"{r['duree']:.0f} s" if r.get("duree") else "-"
        print(f"{e['numero']:>2}. {e.get('test', ''):<4} {e['de']} -> {e['vers']} : {r['issue']} en {duree}, "
              f"{r['passages']} changements de carte, {r['rates']} ratés{' (' + r['raison'] + ')' if r.get('raison') else ''}")
        if r["issue"] != "arrivé" and not args.continuer:
            print(f"Étape {e['numero']} ratée : scénario arrêté (reprendre avec --depuis {e['numero']})")
            break
    RESULTATS.mkdir(parents=True, exist_ok=True)
    sortie = RESULTATS / f"scenario_{args.scenario.stem}_{debut:%Y%m%d_%H%M%S}.json"
    sortie.write_text(json.dumps({"scenario": str(args.scenario), "debut": debut.isoformat(), "etapes": lignes},
                                 ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    arrives = sum(r["issue"] == "arrivé" for r in lignes)
    print(f"\n{arrives}/{len(lignes)} étapes arrivées ; résultats : {sortie}")
    print(f"Mémoire : {len(memoire_cartes.controle(memoire_cartes.lire()))} point(s) à revoir "
          "(python -m tools.memoire_cartes --controle)")
    return 0 if arrives == len(lignes) else 1


if __name__ == "__main__":
    sys.exit(main())
