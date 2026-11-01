"""Carte complète d'après la carte du client (31/10) : cases praticables, lignes de vue, soleils, objets interactifs.

Le serveur annonce chaque carte « GDM|<id>|<date>|<clé> » (connection/network.py : map_id, map_key) ; la clé déchiffre le
mapData du fichier de la carte du client (tools/cles_cartes.py, algorithme vérifié dans le bytecode, outil validé le
20/10). Les fichiers viennent du jeu installé (version du moment) ou de sa copie locale data/client_retro/maps (celle des
références) : jamais distribués.

Une clé ne déchiffre qu'une version d'une carte (31/10 : la clé de 1906 et 1911 d'avant la mise à jour ne déchiffre pas
leur nouvelle version). Une clé qui ne déchiffre qu'une version plus ancienne que celle du jeu installé est périmée
(entendue avant une mise à jour) : carte refusée, plutôt qu'une carte qui n'est plus celle du jeu.

Définitions des cases (celles de tools/reference_combat.py, contrôlées le 24/10 : accord 98 % marche, 99 % ligne de vue
avec l'écran) : praticable = active et déplacement > 0 ; bloc = ni marche ni ligne de vue ; trou = pas de marche, ligne
de vue (fosse, vide autour de la carte). Coordonnées : celles de notre grille (bot/brain/arena.map_cell).
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from bot.brain.arena import BUILT_MAPS, CLIENT_MAPS, map_cell
from bot.perception.grid import Cell

log = logging.getLogger(__name__)

MAP_DIRS = (CLIENT_MAPS, BUILT_MAPS)  # jeu installé d'abord (version du moment), puis la copie des références
BASE_LEVEL = 7  # niveau du sol des cartes plates (toutes les cases de 1,-28 et 0,-28)
SUN_GFX = (1029, 1030)  # objet2 des soleils (tools/cles_cartes.GFX_SOLEILS : vérifié sur 97 % des sorties)
VERSION = re.compile(r"^(\d+)_(\d+)X?\.swf$")


@dataclass(frozen=True)
class MapData:
    map_id: int
    date: str  # version du fichier de la carte (« 0706131721 », AAMMJJhhmm)
    width: int
    walkable: frozenset[Cell]  # actives et praticables (déplacement > 0)
    sight_blockers: frozenset[Cell]  # actives sans ligne de vue
    inactive: frozenset[Cell]  # hors de la carte : pas de marche, ligne de vue
    suns: frozenset[Cell]  # soleils (sorties de la carte)
    interactive: dict[Cell, int] = field(default_factory=dict)  # objets interactifs : case -> gfx (ressources...)
    numbers: dict[Cell, int] = field(default_factory=dict, repr=False)  # case de la grille -> numéro de case du client
    # Relief (01/11, carte 0,-23) : hauteur dessinée de chaque case active hors du sol de base (niveau 7), en niveaux ;
    # une pente compte un demi-niveau de plus (hauteur moyenne de ses coins). Absente : à plat.
    lift: dict[Cell, float] = field(default_factory=dict, repr=False)

    @property
    def cells(self) -> frozenset[Cell]:
        return frozenset(self.numbers)

    @property
    def blocks(self) -> frozenset[Cell]:
        """Ni marche ni ligne de vue (définition des références de combat)."""
        return self.sight_blockers - self.walkable

    @property
    def holes(self) -> frozenset[Cell]:
        """Pas de marche, ligne de vue : cases hors de la carte, fosses."""
        return self.cells - self.walkable - self.blocks


_cache: dict[tuple[int, str, str | None], MapData | None] = {}


def _versions(map_id: int, directories=None) -> list[tuple[str, Path]]:
    """(date, fichier) des versions de la carte, la plus récente d'abord (une seule par date)."""
    found: dict[str, Path] = {}
    for directory in directories or MAP_DIRS:
        try:
            paths = list(Path(directory).glob(f"{map_id}_*.swf"))
        except OSError:
            continue
        for path in paths:
            match = VERSION.match(path.name)
            if match and int(match.group(1)) == map_id:
                found.setdefault(match.group(2), path)
    return sorted(found.items(), reverse=True)


def load_map(map_id: int | None, key: str | None, date: str | None = None, directories=None) -> MapData | None:
    """La carte `map_id` déchiffrée avec `key` (hexadécimal du GDM), version `date` si elle est connue ; None sans
    fichier, sans clé, si la clé ne la déchiffre pas ou si elle ne déchiffre qu'une version périmée. Gardée en mémoire."""
    if map_id is None or not key:
        return None
    memo = (map_id, key, date) if directories is None else None
    if memo is not None and memo in _cache:
        return _cache[memo]
    found = _decipher(map_id, key, date, directories)
    if memo is not None:
        _cache[memo] = found
    return found


def _decipher(map_id: int, key: str, date: str | None, directories) -> MapData | None:
    try:  # import local : déchiffrement du client (outils validés, bibliothèque standard seulement)
        from tools.cartes_client import etat_map_data, lire_carte_swf
        from tools.cles_cartes import decoder_carte, dechiffrer, preparer_cle
    except ImportError as error:
        log.info("Carte %d : déchiffrement indisponible (%s)", map_id, error)
        return None
    versions = _versions(map_id, directories)
    if date is not None:
        versions = [(d, p) for d, p in versions if d == date]
    if not versions:
        log.info("Carte %d : fichier du client absent%s", map_id, f" (version {date})" if date else "")
        return None
    try:
        prepared = preparer_cle(key)
    except ValueError:
        log.info("Carte %d : clé illisible", map_id)
        return None
    for version, path in versions:
        try:
            swf = lire_carte_swf(path)
            clear = dechiffrer(swf.map_data, prepared)
        except (OSError, ValueError, IndexError) as error:
            log.info("Carte %d : fichier %s illisible (%s)", map_id, path.name, error)
            continue
        if etat_map_data(clear, swf.cases) != "clair":
            continue
        if date is None and version != versions[0][0]:
            log.warning("Carte %d : la clé connue ne déchiffre que la version %s, le jeu a la %s (clé d'avant une mise "
                        "à jour) : carte du client ignorée", map_id, version, versions[0][0])
            return None
        return _build(map_id, version, swf.largeur, decoder_carte(clear))
    log.info("Carte %d : la clé ne déchiffre aucune version (%s)", map_id, ", ".join(d for d, _ in versions))
    return None


def _build(map_id: int, date: str, width: int, cases: list[dict]) -> MapData:
    walkable, blockers, inactive, suns, interactive, numbers, lift = set(), set(), set(), set(), {}, {}, {}
    for number, case in enumerate(cases):
        cell = map_cell(number, width)
        numbers[cell] = number
        if not case["active"]:
            inactive.add(cell)
            continue
        level, slope = case.get("niveau_sol", BASE_LEVEL), case.get("pente", 1)
        if (height := level - BASE_LEVEL + (0.5 if slope != 1 else 0.0)) != 0:
            lift[cell] = height
        # Sans ligne de vue : jamais praticable. 01/11 (journal 071237) : les seules cases « déplacement > 0 » sans ligne
        # de vue (169 sur 105 cartes) sont des arbres (objet interactif, déplacement 1), dessinés en blocs en mode
        # tactique ; 5 clics de déplacement sur 5 vers l'un d'eux, ou par l'un d'eux, sans effet.
        if case["deplacement"] > 0 and case["ligne_de_vue"]:
            walkable.add(cell)
        if not case["ligne_de_vue"]:
            blockers.add(cell)
        if case["objet2"] in SUN_GFX:
            suns.add(cell)
        if case["objet2_interactif"] and case["objet2"]:
            interactive[cell] = case["objet2"]
    return MapData(map_id, date, width, frozenset(walkable), frozenset(blockers), frozenset(inactive),
                   frozenset(suns), interactive, numbers, lift)


def known_key(map_id: int | None, net=None) -> str | None:
    """Clé de la carte : celle du dernier GDM si c'est cette carte, sinon celle retenue (data/cles_cartes.json)."""
    if map_id is None:
        return None
    if net is not None and getattr(net, "map_key", None) and getattr(net, "map_id", None) == map_id:
        return net.map_key
    from bot.brain.harvest import load_keys  # import local : harvest.py importe arena.py, qui importe ce module

    return load_keys().get(map_id)


def current_map(net, map_id: int | None = None) -> MapData | None:
    """La carte où l'on est d'après le serveur (net.map_id, ou `map_id`), avec sa clé connue ; None sinon."""
    map_id = getattr(net, "map_id", None) if map_id is None else map_id
    date = getattr(net, "map_date", None) if map_id == getattr(net, "map_id", None) else None
    return load_map(map_id, known_key(map_id, net), date)
