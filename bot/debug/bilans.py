"""Bilan chiffré de chaque lancement, gardé dans data/bilans/<dossier>.json (quelques Ko, jamais effacé).

Les journaux et les captures de logs/ tournent (logs.keep_sessions, logs.max_total_mb) : le 11/10, les 36 combats de
l'après-midi ont été effacés par les trajets du soir, et plus rien ne permettait de comparer les sessions. Les chiffres
(tools/bilan_sessions.py) restent, eux (accord de l'utilisateur, 12/10). Un bilan qui ne s'écrit pas ne gêne jamais le
bot : un avertissement au journal, rien de plus.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

BILANS_DIR = Path(__file__).resolve().parents[2] / "data" / "bilans"


def archive(dossier: Path, out: Path = BILANS_DIR) -> Path | None:
    """Écrit out/<nom du dossier>.json : le bilan d'un lancement du bot (logs/<date_heure>/) ou le rapport d'un outil
    (logs/<outil>_<date_heure>/). None s'il n'y a rien à résumer ou en cas d'erreur."""
    try:
        from tools.bilan_sessions import analyser_dossier, en_json, lire_rapport_outil  # import local : outil de lecture

        session = analyser_dossier(dossier)
        if session is not None:
            data = en_json([session], [], None)
        else:
            outil = lire_rapport_outil(dossier)
            if outil is None:
                return None
            data = en_json([], [outil], None)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{dossier.name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return path
    except Exception as error:  # le bilan ne doit jamais arrêter le bot
        log.warning("Bilan chiffré de %s pas écrit : %s", dossier.name, error)
        return None


def archive_missing(root: Path, current: Path | None = None, out: Path = BILANS_DIR) -> list[Path]:
    """Bilan de chaque dossier de logs/ qui n'en a pas encore (au démarrage, avant que la rotation n'en efface) ;
    `current` : le lancement qui commence, résumé à sa fin."""
    if not root.is_dir():
        return []
    written = []
    for dossier in sorted(d for d in root.iterdir() if d.is_dir() and d != current):
        if not (out / f"{dossier.name}.json").exists():
            path = archive(dossier, out)
            if path is not None:
                written.append(path)
    return written
