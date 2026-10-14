"""Chargement de config/settings.yaml, et réglages remplacés au lancement (option --set section.cle=valeur)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "config" / "settings.yaml"


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_override(text: str) -> tuple[tuple[str, ...], object]:
    """« combat.burst=true » -> (("combat", "burst"), True) : un réglage de config/settings.yaml remplacé au lancement.

    La valeur est lue comme dans settings.yaml (true, 0.5, condition, [a, b]) ; rien après « = » : texte vide.
    ValueError si le texte n'a pas la forme section.cle=valeur."""
    key, equal, value = text.partition("=")
    path = tuple(part.strip() for part in key.split("."))
    if not equal or not all(path):
        raise ValueError(f"réglage attendu sous la forme section.cle=valeur, ex. combat.burst=false (pas {text!r})")
    if not value.strip():
        return path, ""
    try:
        return path, yaml.safe_load(value)
    except yaml.YAMLError:
        raise ValueError(f"valeur illisible dans {text!r}") from None


def apply_overrides(settings: dict, overrides) -> dict:
    """Remplace dans `settings` les réglages lus par parse_override (sections créées au besoin) ; renvoie `settings`."""
    for path, value in overrides:
        node = settings
        for part in path[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = node[part] = {}
            node = child
        node[path[-1]] = value
    return settings
