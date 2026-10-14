"""Personnages à connecter : cochés dans l'interface (ou connexion.personnages de config/settings.yaml) ; en ligne de
commande, --personnages « Gela-[A13],Brawli-[Gal] ». Proposés dans l'interface : ceux que le bot a déjà vus.
"""

from pathlib import Path

from bot.config import ROOT

MEMORY_DIR = ROOT / "data" / "personnages"  # un fichier par personnage déjà lu par le bot (brain/memory.py)


def parse_names(text: str) -> tuple[str, ...]:
    """« Gela-[A13], Brawli-[Gal] » -> ("Gela-[A13]", "Brawli-[Gal]")."""
    return tuple(name.strip() for name in str(text or "").replace(";", ",").split(",") if name.strip())


def known_characters(memory_dir: Path = MEMORY_DIR) -> list[str]:
    """Personnages déjà vus par le bot (data/personnages), pour les proposer dans l'interface."""
    names = {p.stem for p in memory_dir.glob("*.json")} if memory_dir.is_dir() else set()
    return sorted(names, key=str.lower)
