"""Données de référence des régions : config/monde/<région>.json (importées par tools/import_monde.py, Dofus Rétro 1.49).

Pour chaque carte extérieure : sa zone, les cases de ses soleils par côté, la carte où mène chaque côté, et les
autres passages (soleils sans direction, portes) qui mènent ailleurs (maison, temple, donjon) : le bot ne les
prend pas pour changer de carte. Ce que le bot a vu lui-même (data/carte.json) prime sur ces données.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from bot.perception.exits import DELTA
from bot.perception.grid import Cell

log = logging.getLogger(__name__)

WORLD_DIR = Path(__file__).resolve().parents[2] / "config" / "monde"


@dataclass(frozen=True)
class MapInfo:
    region: str
    coords: tuple[int, int]
    zone: str | None  # sous-zone (Prairie, Forêt d'Astrub...)
    area: str | None = None  # région de la sous-zone (Incarnam, Astrub, Amakna...)
    suns: dict[str, tuple[Cell, ...]] = field(default_factory=dict)  # côté -> cases des soleils de ce côté
    leads_to: dict[str, tuple[int, int] | None] = field(default_factory=dict)  # côté -> coordonnées de la voisine
    others: tuple[Cell, ...] = ()  # soleils sans direction, portes : autre chose qu'une carte voisine
    objects: tuple[dict, ...] = ()  # ressources, ateliers, zaap... : {"nom", "type", "nombre"} (pas leur case)
    # Données en clair du client (tools/completer_references.py, 12/10) :
    # soleils dont la langue donne la case, côté -> cases : quelques côtés seulement, pas une description complète de
    # la carte comme `suns` (Incarnam) ; vus en jeu à la case annoncée.
    client_suns: dict[str, tuple[Cell, ...]] = field(default_factory=dict)
    map_id: int | None = None  # carte du jeu (extérieure)
    other_ids: tuple[int, ...] = ()  # autres cartes aux mêmes coordonnées : intérieurs, couches, salles de donjon
    doors: tuple[tuple[Cell, str | None], ...] = ()  # portes de maisons : (case, maison)
    placements: dict[str, tuple[Cell, ...]] = field(default_factory=dict)  # combat : "p1"/"p2" -> cases de départ


def _coords(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    x, y = text.split(",")
    return int(x), int(y)


class Atlas:
    def __init__(self, maps: dict[str, MapInfo] | None = None, places: list[dict] | None = None) -> None:
        self.maps = maps or {}  # « Incarnam 1,6 » -> MapInfo
        self.places = places or []  # zaaps, donjons, banques... : {"region", "type", "nom", "carte"}

    @classmethod
    def load(cls, folder: Path = WORLD_DIR) -> "Atlas":
        maps, places = {}, []
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                log.warning("Carte de référence illisible (%s) : %s", path.name, error)
                continue
            region = data["region"]
            areas = {zone["nom"]: zone.get("region", region) for zone in data.get("zones", [])}
            for key, entry in data.get("cartes", {}).items():
                maps[f"{region} {key}"] = MapInfo(
                    region=region,
                    coords=_coords(key),
                    zone=entry.get("zone"),
                    area=areas.get(entry.get("zone"), region),
                    suns={side: tuple(tuple(c) for c in exit_["cases"]) for side, exit_ in entry.get("sorties", {}).items()},
                    leads_to={side: _coords(exit_.get("vers")) for side, exit_ in entry.get("sorties", {}).items()},
                    others=tuple(tuple(o["case"]) for o in entry.get("autres", [])),
                    objects=tuple(entry.get("objets", [])),
                    client_suns={side: tuple(tuple(c) for c in cells)
                                 for side, cells in (entry.get("soleils_client") or {}).items()},
                    map_id=entry.get("id"),
                    other_ids=tuple(entry.get("autres_ids") or ()),
                    doors=tuple((tuple(d["case"]), d.get("maison")) for d in entry.get("portes") or ()),
                    placements={team: tuple(tuple(c) for c in cells or () if c)
                                for team, cells in (entry.get("placements") or {}).items()},
                )
            places += [{"region": region, **place} for place in data.get("lieux", [])]
        if maps:
            known = sum(bool(m.client_suns) for m in maps.values())
            log.info("Cartes de référence : %d cartes (%s)%s", len(maps), ", ".join(sorted({m.region for m in maps.values()})),
                     f" ; soleils connus du client sur {known} cartes" if known else "")
        return cls(maps, places)

    def get(self, region: str | None, coords: tuple[int, int] | None) -> MapInfo | None:
        if region is None or coords is None:
            return None
        return self.maps.get(f"{region} {coords[0]},{coords[1]}")

    def zone(self, region: str | None, coords: tuple[int, int] | None) -> str | None:
        info = self.get(region, coords)
        return info.zone if info else None

    def zone_maps(self, region: str | None, zone: str | None) -> set[tuple[int, int]]:
        return {m.coords for m in self.maps.values() if m.region == region and m.zone == zone} if zone else set()

    def links(self, region: str, coords: tuple[int, int]) -> dict[str, tuple[int, int]]:
        """Côté -> carte voisine. Références avec soleils (Incarnam) : les liaisons du jeu ; sinon (Monde des Douze),
        les cartes connues juste à côté, à confirmer en jeu (une falaise ou de l'eau peut les séparer)."""
        info = self.get(region, coords)
        if info is None:
            return {}
        if info.leads_to:
            return {side: dest for side, dest in info.leads_to.items() if dest}
        near = {side: (coords[0] + dx, coords[1] + dy) for side, (dx, dy) in DELTA.items()}
        return {side: dest for side, dest in near.items() if self.get(region, dest) is not None}
