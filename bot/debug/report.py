"""Bilan de chaque combat (11/10, sessions de tests enchaînées) : une ligne par personnage au journal, et une ligne
JSON par combat dans logs/<date_heure>/combats.jsonl pour comparer les tests.

Pour chaque personnage : tours joués, temps passé à jouer, sorts lancés (par sort), sorts tentés mais refusés, clics
sur une cible sans effet, déplacements (et ceux sans bouger).
"""

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

REPORT_NAME = "combats.jsonl"


def describe(report: dict) -> str:
    """« 3 tours (11,2 s) ; 7 sorts (Flèche Magique x4, Maîtrise de l'Arc x1...) ; 1 refus ; 2 déplacements »."""
    spells = ", ".join(f"{name} x{count}" for name, count in sorted(report["sorts"].items(), key=lambda kv: -kv[1]))
    casts = sum(report["sorts"].values())
    parts = [f"{report['tours']} tour(s) ({report['duree_tours']:.1f} s)".replace(".", ","),
             f"{casts} sort(s)" + (f" ({spells})" if spells else ""), f"{report['refus']} refus"]
    if report["clics_sans_effet"]:
        parts.append(f"{report['clics_sans_effet']} clic(s) sans effet")
    parts.append(f"{report['deplacements']} déplacement(s)")
    if report["deplacements_sans_bouger"]:
        parts.append(f"dont {report['deplacements_sans_bouger']} sans bouger")
    return " ; ".join(parts)


def write_fight_report(folder: Path | None, number: int, reports: list[tuple[str, dict]]) -> None:
    """Bilan du combat n° `number` au journal, et ajouté à `folder`/combats.jsonl (None : au journal seulement)."""
    for name, report in reports:
        log.info("Bilan du combat %d, %s : %s", number, name, describe(report))
    if folder is None:
        return
    line = {"combat": number, "fin": datetime.now().isoformat(timespec="seconds"),
            "personnages": {name: report for name, report in reports}}
    try:
        with (Path(folder) / REPORT_NAME).open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as error:
        log.warning("Bilan du combat non enregistré (%s)", error)
