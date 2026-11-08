"""Sortir d'un bâtiment (08/11, l'utilisateur : « avec les données du serveur et nos références, le bot devrait pouvoir
sortir de n'importe quel bâtiment »).

07/11 : Waldhary connecté dans une taverne de 5,-17 (cartes 7572 à 7574) ; l'écran sombre l'a fait prendre pour la
banque (sortie de la banque cliquée pour rien), puis le trajet a cliqué les soleils de bord de la carte de dehors, qui
n'existent pas dedans : bot arrêté (« aucun chemin »), sorti à la main par le soleil au sol.

Ce qui est sûr :
- la carte où l'on est : annoncée par le serveur (GDM), avec sa clé ;
- intérieur ou dehors : index des cartes du client (data/client_retro/cartes/index.json, « exterieur ») ;
- un BÂTIMENT (à quitter) : une carte intérieure dont les coordonnées ont aussi une carte de dehors de la même
  sous-zone (taverne 7572 et rue 7429 : « Cité d'Astrub »). Une salle de donjon n'en est pas un (salles de l'Ensablé :
  sous-zone « Donjon Ensablé », dehors : « Calanques d'Astrub ») : jamais quittée par ici ;
- ses sorties : les soleils de la carte du client, déchiffrée avec la clé du serveur (brain/mapdata.py).

Sortie : le soleil le plus proche, puis la carte d'arrivée annoncée par le serveur ; une autre pièce du bâtiment :
on recommence (un soleil déjà pris dans une pièce n'est pas repris), jusqu'à une carte de dehors.
"""

import json
import logging
import time
from functools import lru_cache

from bot.brain.mapdata import current_map
from bot.config import ROOT
from bot.perception.grid import distance

log = logging.getLogger(__name__)

INDEX_FILE = ROOT / "data" / "client_retro" / "cartes" / "index.json"
MAX_MAPS = 5  # pièces traversées au plus
SUN_TRIES = 3  # soleils essayés par pièce
CROSS_WAIT = 12.0  # s : nouvelle carte annoncée après le clic sur un soleil (marche comprise)


@lru_cache(maxsize=1)
def _index() -> dict:
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except OSError:
        log.debug("Index des cartes du client illisible", exc_info=True)
        return {}


@lru_cache(maxsize=1)
def _outdoor_zones() -> dict[tuple[int, int], set]:
    zones: dict[tuple[int, int], set] = {}
    for info in _index().values():
        if info.get("exterieur") and info.get("x") is not None:
            zones.setdefault((info["x"], info["y"]), set()).add(info.get("sous_zone"))
    return zones


def is_building(map_id: int | None) -> bool:
    """Carte intérieure qui a une carte de dehors de la même sous-zone aux mêmes coordonnées (maison, taverne,
    banque...) ; False pour une carte de dehors, une salle de donjon ou une carte inconnue."""
    info = _index().get(str(map_id)) if map_id is not None else None
    if not info or info.get("exterieur", True) or info.get("x") is None:
        return False
    return info.get("sous_zone") in _outdoor_zones().get((info["x"], info["y"]), set())


def indoors(game, net) -> bool:
    """Dans un bâtiment : d'après le serveur (carte annoncée) ; carte pas encore annoncée (08/11 : personnage entré
    avant que le bot écoute, le serveur ne redit la carte qu'au changement suivant) : fond noir autour de la pièce."""
    map_id = getattr(net, "map_id", None) if net is not None else None
    if map_id is not None:
        return is_building(map_id)
    from bot.team.bank import INDOOR_DARK, dark_share

    return dark_share(game.look(park=False)) >= INDOOR_DARK


def _screen_suns(game) -> list:
    from bot.perception.exits import find_floor_suns

    return [exit_.cell for exit_ in find_floor_suns(game.look(park=False))]


def leave_building(game, net, hide_key: str = "e", name: str = "") -> bool:
    """Le personnage de `game` (écoute `net`) sort du bâtiment où il est. True s'il est dehors (ou n'était pas dans un
    bâtiment) ; False si une pièce n'a ni soleil connu ni soleil vu à l'écran, ou si aucun soleil n'a mené ailleurs.
    Carte pas encore annoncée : soleils cherchés à l'écran ; la carte d'arrivée, elle, est annoncée."""
    taken: set[tuple[int | None, tuple[int, int]]] = set()
    for _ in range(MAX_MAPS):
        map_id = getattr(net, "map_id", None)
        if not indoors(game, net):
            return True
        data = current_map(net) if map_id is not None else None
        cells = set(data.suns) if data is not None and data.suns else set(_screen_suns(game))
        if not cells:
            log.warning("%s : dans un bâtiment (carte %s), aucune sortie connue ni vue à l'écran", name,
                        map_id if map_id is not None else "pas encore annoncée")
            return False
        me = net.my_grid_cell() if hasattr(net, "my_grid_cell") else None
        suns = sorted((s for s in cells if (map_id, s) not in taken),
                      key=lambda s: (distance(me, s) if me else 0, s))
        moved = False
        for sun in suns[:SUN_TRIES]:
            taken.add((map_id, sun))
            frame = game.look(park=False)
            clicked = time.monotonic()
            log.info("%s : dans un bâtiment (carte %s), vers la sortie en %s", name, map_id, sun)
            hold = game.holding(hide_key) if hide_key and hasattr(game, "holding") else _Nothing()
            with hold:
                game.click(frame.grid.center(sun), (2, 2))
            if net.wait_new_map(clicked, CROSS_WAIT) is not None:
                moved = True
                break
        if not moved:
            log.warning("%s : aucun soleil de la carte %s n'a mené ailleurs", name, map_id)
            return False
    outside = not indoors(game, net)
    log.info("%s : %s", name, "sorti du bâtiment" if outside else f"toujours dedans après {MAX_MAPS} pièces")
    return outside


class _Nothing:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def outdoors(map_id: int | None) -> bool | None:
    """Carte de dehors d'après l'index des cartes du client ; None si la carte est inconnue."""
    info = _index().get(str(map_id)) if map_id is not None else None
    return None if not info else bool(info.get("exterieur", True))


def leave_room(game, net, hide_key: str = "e", name: str = "") -> bool:
    """Sortir d'une salle intérieure qui n'est pas un bâtiment (08/11 : salle d'entrée du donjon des Blops, 11878, où
    « Sortir du donjon » ramène ; sa sortie est un soleil au sol en (4, 26), vu à l'écran, absent des soleils de la
    carte du client) : le soleil au sol vu à l'écran le plus proche, puis la carte annoncée par le serveur. True si le
    personnage est dehors (ou l'était)."""
    map_id = getattr(net, "map_id", None)
    if outdoors(map_id) is not False:
        return True
    suns = _screen_suns(game)
    if not suns:
        log.warning("%s : salle %s : aucun soleil vu au sol", name, map_id)
        return False
    me = net.my_grid_cell() if hasattr(net, "my_grid_cell") else None
    for sun in sorted(suns, key=lambda s: (distance(me, s) if me else 0, s))[:SUN_TRIES]:
        frame = game.look(park=False)
        clicked = time.monotonic()
        log.info("%s : salle %s, vers la sortie en %s", name, map_id, sun)
        hold = game.holding(hide_key) if hide_key and hasattr(game, "holding") else _Nothing()
        with hold:
            game.click(frame.grid.center(sun), (2, 2))
        if net.wait_new_map(clicked, CROSS_WAIT) is not None:
            return outdoors(net.map_id) is not False
    return False
