"""Personnages à connecter : cochés dans l'interface (ou connexion.personnages de config/settings.yaml) ; en ligne de
commande, --personnages « Gela-[A13],Brawli-[Gal] ». Proposés dans l'interface : ceux que le bot a déjà vus, qu'elle
peut aussi oublier (fichier mis de côté dans data/personnages/supprimes, jamais effacé).
"""

import time
from pathlib import Path

from bot.brain.memory import CharacterMemory, SessionSettings
from bot.config import ROOT

MEMORY_DIR = ROOT / "data" / "personnages"  # un fichier par personnage déjà lu par le bot (brain/memory.py)
FORGOTTEN = "supprimes"  # sous-dossier des fichiers des personnages supprimés de l'interface


def parse_names(text: str) -> tuple[str, ...]:
    """« Gela-[A13], Brawli-[Gal] » -> ("Gela-[A13]", "Brawli-[Gal]")."""
    return tuple(name.strip() for name in str(text or "").replace(";", ",").split(",") if name.strip())


def memory_dir(settings: dict) -> Path | None:
    """Dossier des fichiers des personnages (session.memory de config/settings.yaml, défaut data/personnages) ; None
    si le bot ne retient rien (session.memory vide)."""
    return SessionSettings.from_settings(settings).memory


def known_characters(memory_dir: Path | None = MEMORY_DIR) -> list[str]:
    """Personnages déjà vus par le bot (un fichier <Nom>.json dans `memory_dir`, celui de session.memory), pour les
    proposer dans l'interface ; ceux de supprimes/ n'en font plus partie."""
    names = {p.stem for p in memory_dir.glob("*.json")} if memory_dir is not None and memory_dir.is_dir() else set()
    return sorted(names, key=str.lower)


def forget_character(name: str, memory_dir: Path = MEMORY_DIR) -> Path | None:
    """Personnage oublié : son fichier (même nom que CharacterMemory.path, bot/brain/memory.py) DÉPLACÉ dans
    supprimes/<Nom>_<AAAAMMJJ_HHMMSS>.json, pour pouvoir le remettre ; renvoie sa nouvelle place, None s'il n'a pas de
    fichier."""
    path = CharacterMemory(memory_dir).path(name)
    if not path.is_file():
        return None
    target = memory_dir / FORGOTTEN / f"{path.stem}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    return path.replace(target)
