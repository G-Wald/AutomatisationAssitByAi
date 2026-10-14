"""Début de session : contrôle à l'écran ou reprise de la dernière session, et ce qui est retenu de chaque personnage.

Demande de l'utilisateur (04/10) : un mode qui contrôle (sorts et caractéristiques lus à l'écran, comme avant) et un
mode qui reprend ce qui a été retenu à la dernière session, au choix dans l'interface ; retenu par nom de personnage,
une seule entrée chacun (pas d'historique).

- Réglage session.mode (config/settings.yaml ; --lecture en ligne de commande) : « controle » lit la fenêtre des
  sorts et les caractéristiques, puis les retient ; « memoire » n'ouvre aucune fenêtre et reprend ce qui est retenu
  pour ce personnage ; s'il manque quelque chose (personnage jamais lu, caractéristiques jamais lues), seul ce qui
  manque est lu à l'écran, et le journal le dit.
- Fichier data/personnages/<Nom>.json, un par personnage : la dernière lecture réussie de chaque partie (sorts,
  caractéristiques) remplace la précédente, avec sa date. Sorts : les lignes de la fenêtre « Tes sorts » telles que
  lues (PA, portée, niveau, appris ou non, icône en PNG) — de quoi refaire exactement ce que fait la lecture (classe,
  niveau chez Solomonk, rôle, recalculés avec config/sorts.yaml du moment) — plus, pour le lecteur, la classe, le
  sort de chaque ligne, ses dégâts, et les cases de la barre où chaque sort a été vu.
- Barre de sorts : les icônes retenues sont comparées aux cases de la barre au premier tour, comme après une lecture à
  l'écran (SpellBook.locate) : un sort déplacé dans la barre depuis la dernière session est retrouvé à sa nouvelle
  case. Les cases retenues ne servent qu'au journal (changements depuis la dernière session).
"""

import base64
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from bot.config import ROOT
from bot.perception.spellbook import SpellEntry

log = logging.getLogger(__name__)

CONTROL, MEMORY = "controle", "memoire"
READING_MODES = (CONTROL, MEMORY)
MEMORY_DIR = ROOT / "data" / "personnages"
UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # caractères interdits dans un nom de fichier Windows


def reading_mode(text: str) -> str:
    """« Mémoire », « controle »... -> « memoire » / « controle » (accents et majuscules indifférents) ; ValueError
    sinon."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c)).strip().lower()
    if plain not in READING_MODES:
        raise ValueError(f"session.mode doit valoir {' ou '.join(READING_MODES)} (pas {text!r})")
    return plain


@dataclass(frozen=True)
class SessionSettings:
    mode: str = CONTROL  # controle : lire à l'écran puis retenir ; memoire : reprendre la dernière session
    stats: bool = True  # lire les caractéristiques et les appliquer aux dégâts des sorts
    memory: Path | None = None  # dossier des fichiers des personnages ; None : rien n'est retenu

    @classmethod
    def from_settings(cls, settings: dict) -> "SessionSettings":
        session = settings.get("session") or {}
        folder = session.get("memory", "data/personnages")
        path = Path(folder) if folder else None
        return cls(
            mode=reading_mode(session.get("mode", CONTROL) or CONTROL),
            stats=bool(session.get("stats", cls.stats)),
            memory=(path if path is None or path.is_absolute() else ROOT / path),
        )


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def when(text: str | None) -> str:
    """« 2026-10-04T15:01:12 » -> « le 04/10 à 15:01 » ; « à une date inconnue » si illisible."""
    try:
        moment = datetime.fromisoformat(text or "")
    except ValueError:
        return "à une date inconnue"
    return f"le {moment:%d/%m} à {moment:%H:%M}"


def icon_text(icon: np.ndarray) -> str:
    ok, data = cv2.imencode(".png", icon)
    return base64.b64encode(data.tobytes()).decode("ascii") if ok else ""


def icon_of(text: str) -> np.ndarray | None:
    try:
        data = np.frombuffer(base64.b64decode(text, validate=True), np.uint8)
    except (ValueError, TypeError):
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def entry_record(entry: SpellEntry) -> dict:
    """Une ligne de la fenêtre des sorts, telle que lue."""
    return {"ligne": entry.row, "pa": entry.pa, "portee": list(entry.spell_range) if entry.spell_range else None,
            "portee_min_ecrite": entry.low_written, "appris": entry.learned, "niveau": entry.level,
            "icone": icon_text(entry.icon)}


def entry_of(record: dict) -> SpellEntry | None:
    """La ligne retenue ; None si elle est abîmée."""
    icon = icon_of(record.get("icone") or "")
    reach = record.get("portee")
    if icon is None or not isinstance(record.get("ligne"), int):
        return None
    return SpellEntry(row=record["ligne"], pa=record.get("pa"), spell_range=tuple(reach) if reach else None,
                      learned=bool(record.get("appris")), icon=icon, level=record.get("niveau"),
                      low_written=bool(record.get("portee_min_ecrite", True)))


class CharacterMemory:
    """Un fichier JSON par personnage dans `folder` : une seule entrée par nom, réécrite à chaque lecture réussie."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def path(self, name: str) -> Path:
        return self.folder / f"{UNSAFE.sub('_', name.strip())}.json"

    def load(self, name: str) -> dict | None:
        """Ce qui est retenu pour ce personnage ; None si rien (ou fichier abîmé : signalé)."""
        path = self.path(name)
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warning("Mémoire de %s illisible (%s) : %s", name, path, error)
            return None
        if not isinstance(record, dict):
            log.warning("Mémoire de %s illisible (%s)", name, path)
            return None
        return record

    def save(self, name: str, record: dict) -> Path | None:
        """Remplace ce qui était retenu pour ce personnage (écrit à côté puis renommé : jamais de fichier à moitié
        écrit) ; None si l'écriture échoue (signalé, le bot continue)."""
        path = self.path(name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"personnage": name, **record}, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
            temporary.replace(path)
        except OSError as error:
            log.warning("Mémoire de %s pas enregistrée (%s) : %s", name, path, error)
            return None
        return path
