"""Importe la carte d'Incarnam ou du Monde des Douze (Dofus Rétro 1.49) depuis la carte interactive de xixou.io.

    .venv\\Scripts\\python -m tools.import_monde incarnam
    .venv\\Scripts\\python -m tools.import_monde douze

Écrit config/monde/<region>.json (données de référence, versionnées avec le projet).

Incarnam, sources toutes Rétro 1.49 :
- cartes, sorties (case de chaque soleil) et ressources de chaque carte (nombre, pas la case) :
  tiles-incarnam/data.json (tiré du client Dofus Rétro : data/maps, lang interactiveobjects) ;
- sous-zones et lieux (zaaps, donjon...) : xm-incarnam.js (lang maps_fr_1251 et hints_fr_1254 du client) ;
- monstres : bestiaire du site (race « D'Incarnam »), pour toute l'île : le client ne publie pas leur
  répartition par sous-zone.
Monde des Douze (carte « complète » du site, xmap-data-*.js) :
- zones (nom, région, cartes, monstres), niveau des monstres, donjons (boss, niveau, carte), zaaps ;
- ressources de chaque carte (nombre, pas la case) : relevées par solomonk.fr, reprises par le site ;
- pas de soleils : le site ne les donne que pour Incarnam (le bot les trouve à l'écran puis les retient).
Rien d'autre n'est gardé (ni quêtes, ni PNJ, ni hôtels de vente, ateliers, temples, maisons, enclos). Ce que le bot
voit lui-même en jeu va dans data/carte.json et prime sur ces données.
"""

import argparse
import base64
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

SITE = "https://xixou.io"
PAGE = SITE + "/map-interactive/?carte=incarnam"
WORLD_PAGE = SITE + "/map-interactive/"
MAPS = SITE + "/wp-content/plugins/xixou-map/tiles-incarnam/data.json"
SCRIPT = SITE + "/wp-content/plugins/xixou-map/js/xm-incarnam.js"
OUT_DIR = Path(__file__).resolve().parents[1] / "config" / "monde"

SIDES = {"N": "haut", "S": "bas", "E": "droite", "O": "gauche"}
DELTA = {"haut": (0, -1), "bas": (0, 1), "gauche": (-1, 0), "droite": (1, 0)}
OBJECT_KINDS = {1: "ressource", 2: "atelier", 3: "zaap", 4: "autre"}  # type d'objet interactif du client
JOBS = {"bucheron": "Bûcheron", "mineur": "Mineur", "alchimiste": "Alchimiste", "paysan": "Paysan",
        "pecheur": "Pêcheur"}
PLACES = {"Zaap": "zaap", "Place Marchande": "place_marchande", "Taverne": "taverne", "Milice": "milice",
          "Transport vers Astrub": "transport", "Donjon d'Incarnam": "donjon", "Banque": "banque"}


def get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (autobot, import de carte)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def cell_to_grid(cell: int) -> list[int]:
    """Numéro de case du client (lignes de 15 puis 14 cases en quinconce) -> case (i, j) de bot/perception/grid.py.

    Vérifié sur les soleils cliqués par le bot le 28/09 (1,6 : case 19 -> (9, 1), case 173 -> (27, 11)...)."""
    row, rest = divmod(cell, 29)
    return [2 * rest, 2 * row] if rest < 15 else [2 * (rest - 15) + 1, 2 * row + 1]


def page_config(html: str) -> dict:
    """XixouMapConfig, inclus dans la page sous forme de script base64."""
    for encoded in re.findall(r'src="data:text/javascript;base64,([A-Za-z0-9+/=]+)"', html):
        script = base64.b64decode(encoded).decode("utf-8")
        if script.startswith("var XixouMapConfig="):
            text = re.sub(r":!([01])(?=[,}\]])", lambda m: ":false" if m[1] == "1" else ":true",
                          script[len("var XixouMapConfig="):])
            return json.JSONDecoder().raw_decode(text)[0]  # JavaScript minifié : !0 = true, !1 = false
    raise RuntimeError("XixouMapConfig introuvable dans la page")


def sub_zones(script: str) -> dict[int, dict]:
    zones = {}
    for found in re.finditer(r"\{\s*id:\s*(\d+),\s*n:\s*(['\"])(.*?)\2,\s*c:\s*'[^']*',\s*cells:\s*(\[\[.*?\]\])", script):
        zones[int(found[1])] = {"nom": found[3], "cartes": [f"{x},{y}" for x, y in json.loads(found[4])]}
    if not zones:
        raise RuntimeError("sous-zones introuvables dans xm-incarnam.js")
    return zones


def places(script: str) -> list[dict]:
    found = []
    for hint in re.finditer(r"\{\s*n:\s*(['\"])(.*?)\1,\s*g:\s*\d+,\s*x:\s*(-?\d+),\s*y:\s*(-?\d+)", script):
        name = hint[2]
        found.append({"type": PLACES.get(name, "autre"), "nom": name, "carte": f"{hint[3]},{hint[4]}"})
    return found


def build_incarnam() -> dict:
    maps = json.loads(get(MAPS))
    script = get(SCRIPT)
    config = page_config(get(PAGE))
    zones = sub_zones(script)
    links = {((a, b), (c, d)) for a, b, c, d in maps["liens"]} | {((c, d), (a, b)) for a, b, c, d in maps["liens"]}

    outdoor, inside = {}, []
    for map_id, info in sorted(maps["maps"].items(), key=lambda item: int(item[0])):
        coords = (info["x"], info["y"])
        key = f"{coords[0]},{coords[1]}"
        zone = zones.get(info["sa"], {}).get("nom")
        exits, others = {}, []
        for cell, direction, kind, *_ in info["ex"]:
            side = SIDES.get(direction)
            if kind == "soleil" and side:
                dest = (coords[0] + DELTA[side][0], coords[1] + DELTA[side][1])
                exit_ = exits.setdefault(side, {"cases": [], "vers": f"{dest[0]},{dest[1]}" if (coords, dest) in links else None})
                exit_["cases"].append(cell_to_grid(cell))
            else:
                others.append({"type": kind, "case": cell_to_grid(cell), **({"direction": side} if side else {})})
        objects = [{"nom": maps["io"][obj]["n"], "type": OBJECT_KINDS.get(maps["io"][obj]["t"], "autre"), "nombre": count,
                    **({"metiers": maps["io"][obj]["j"]} if maps["io"][obj]["j"] else {})}
                   for obj, count in sorted(info["r"].items(), key=lambda item: -item[1]) if obj in maps["io"]]
        entry = {"id": int(map_id), "zone": zone, "sorties": exits, **({"autres": others} if others else {}),
                 **({"objets": objects} if objects else {})}
        if info["o"] == 1 and exits and key not in outdoor:
            outdoor[key] = entry
        else:
            inside.append({"carte": key, **entry})

    return {
        "region": "Incarnam",
        "source": {
            "site": PAGE,
            "version": "Dofus Rétro 1.49",
            "origine": "client Dofus Rétro (data/maps, lang maps_fr_1251, hints_fr_1254) ; monstres : bestiaire du site",
            "importe_le": date.today().isoformat(),
        },
        "zones": [{"id": zone_id, **zone} for zone_id, zone in sorted(zones.items())],
        "monstres": {
            "repartition": "toute l'île : le client ne publie pas la répartition par sous-zone",
            "liste": [{"nom": m["n"], "niveau": m["lv"]} for m in config["incarnam"]["monsters"]],
        },
        "lieux": places(script),
        "cartes": outdoor,
        "interieurs": inside,
    }


def world_data() -> dict:
    """window.XixouMapData, dans le script xmap-data-<empreinte>.js de la carte complète (l'empreinte change)."""
    html = get(WORLD_PAGE)
    found = re.search(r"https://xixou\.io/wp-content/uploads/xixou-cache/xmap-data-[0-9a-f]+\.js", html)
    if not found:
        raise RuntimeError("script des données du Monde des Douze introuvable dans la page")
    script = get(found[0])
    text = re.sub(r":!([01])(?=[,}\]])", lambda m: ":false" if m[1] == "1" else ":true",
                  script[script.index("=") + 1:])
    return json.JSONDecoder().raw_decode(text)[0]


def build_douze() -> dict:
    data = world_data()
    world = data["mapData"]
    regions = {r["id"]: r["name"] for r in world["regions"]}
    zones = [z for z in world["zones"] if z.get("region") != "incarnam"]  # Incarnam : autre fichier
    levels = {name: meta.get("lv") for name, meta in data.get("monsterMeta", {}).items()}

    maps: dict[str, dict] = {}
    for zone in zones:
        for x, y in zone.get("cells") or []:
            entry = maps.setdefault(f"{x},{y}", {"sorties": {}})
            entry.setdefault("zones", []).append(zone["name"])
    for job, resources in world["resources"].items():
        for resource in resources:
            for x, y, *rest in resource.get("positions", []):
                count = rest[0] if rest else None  # nombre parfois absent : la ressource est là, sans quantité
                entry = maps.setdefault(f"{x},{y}", {"sorties": {}})
                entry.setdefault("objets", []).append({"nom": resource["name"], "type": "ressource", "nombre": count,
                                                       "metiers": [JOBS.get(job, job)], "niveau": resource.get("level")})
    for entry in maps.values():
        names = entry.pop("zones", [])
        entry["zone"] = names[0] if names else None
        if len(names) > 1:
            entry["aussi"] = names[1:]  # carte partagée par deux zones du site

    monsters = sorted({m for z in zones for m in z.get("monsters") or []})
    places = [{"type": "zaap", "nom": z["name"], "carte": f"{z['position'][0]},{z['position'][1]}"} for z in world["zaaps"]]
    places += [{"type": "donjon", "nom": d["name"], "boss": d.get("boss"), "niveau": int(d["level"]) if str(d.get("level", "")).isdigit() else d.get("level"),
                "carte": f"{d['position'][0]},{d['position'][1]}", "zone": d.get("zone")} for d in world["dungeons"]]
    return {
        "region": "Monde des Douze",
        "source": {
            "site": WORLD_PAGE,
            "version": "Dofus Rétro 1.49",
            "origine": "carte interactive du site (zones, monstres, donjons, zaaps) ; ressources relevées par solomonk.fr "
                       f"({world.get('_comment', '')})",
            "importe_le": date.today().isoformat(),
            "soleils": "non fournis par le site hors Incarnam : trouvés à l'écran par le bot",
        },
        "regions": sorted(set(regions[z["region"]] for z in zones if z.get("region") in regions)),
        "zones": [{"id": z["id"], "nom": z["name"], "region": regions.get(z.get("region"), z.get("region")),
                   "cartes": [f"{x},{y}" for x, y in z.get("cells") or []], "monstres": z.get("monsters") or []}
                  for z in zones],
        "monstres": {"repartition": "par zone (voir zones)",
                     "liste": [{"nom": name, "niveau": levels.get(name)} for name in monsters]},
        "lieux": places,
        "cartes": dict(sorted(maps.items(), key=lambda item: tuple(int(v) for v in item[0].split(",")))),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("region", choices=["incarnam", "douze"], help="incarnam, ou douze (Monde des Douze)")
    args = parser.parse_args()
    data = build_incarnam() if args.region == "incarnam" else build_douze()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / ("incarnam.json" if args.region == "incarnam" else "monde_des_douze.json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    objects = sum(len(m.get("objets", [])) for m in [*data["cartes"].values(), *data.get("interieurs", [])])
    print(f"{path} : {len(data['cartes'])} cartes, {len(data['zones'])} zones, {len(data['lieux'])} lieux, "
          f"{len(data['monstres']['liste'])} monstres, {objects} ressources ou objets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
