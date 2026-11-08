"""Résistances des monstres (08/11, l'utilisateur : « tu prends en compte les résistances des mobs en face ? »).

Langue du client, table M : chaque grade (g1 à g5) d'un monstre a ses résistances « r », en % : 0 neutre, 1 terre,
2 feu, 3 eau, 4 air (puis esquive PA, esquive PM). Ordre déduit le 08/11 : le serveur numérote les éléments des coups
de la même façon (« GA;100;lanceur;cible,-dégâts,élément » : Flèche Explosive, feu, en 2 ; Flèche d'Expiation, eau, en
3), et les Blops suivent les couleurs, par paires opposées : Griotte 50 en 2 et -62 en 3, Indigo l'inverse ; Coco 50
en 4 et -62 en 1, Reinette l'inverse.

En combat, le serveur donne le modèle et le grade de chaque monstre (GM|+ : modèle au 5e champ, grade au 8e).
"""

import logging
from functools import lru_cache

log = logging.getLogger(__name__)

ELEMENTS = {"neutre": 0, "terre": 1, "feu": 2, "eau": 3, "air": 4}


@lru_cache(maxsize=1)
def _monsters() -> dict:
    try:
        from tools.langue_client import LangueClient  # import local : lecture des fichiers de langue du client

        return LangueClient().table("M")
    except (OSError, KeyError):
        log.debug("Langue du client illisible : résistances des monstres inconnues", exc_info=True)
        return {}


@lru_cache(maxsize=4096)
def resistances(template: int, grade: int | None) -> dict[str, int]:
    """Résistances (élément -> %) du monstre `template` à son `grade` (1 à 5 ; inconnu : le 1er) ; {} s'il est inconnu."""
    monster = _monsters().get(str(template)) or {}
    level = monster.get(f"g{grade}") if grade else None
    level = level or monster.get("g1") or {}
    values = level.get("r") or {}
    out = {}
    for name, index in ELEMENTS.items():
        value = values.get(str(index)) if isinstance(values, dict) else None
        if isinstance(value, (int, float)):
            out[name] = int(value)
    return out


def factor(resist: dict[str, int] | None, element: str | None) -> float:
    """Part des dégâts d'un élément qui passe la résistance (-50 % : 1,5 ; 50 % : 0,5 ; jamais moins que 0)."""
    if not resist or not element:
        return 1.0
    return max(0.0, 1 - resist.get(element, 0) / 100)
