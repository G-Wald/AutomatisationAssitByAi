"""Carte complète d'après la carte du client (bot/brain/mapdata.py, source « carte du client » de bot/brain/arena.py).

Les cartes du client (data/client_retro, jeu installé), les clés entendues (data/cles_cartes.json) et les références
(data/references) sont locales : les tests qui en ont besoin sont sautés sans elles.
"""

import json
import shutil
from pathlib import Path

import pytest

from bot.brain import mapdata
from bot.brain.arena import BUILT_MAPS, Arenas, client_arena, fight_arena, map_cell
from bot.brain.harvest import load_keys
from bot.brain.mapdata import current_map, load_map

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "data" / "references"  # vraie référence, en lecture seule (conftest en écarte arena.ARENA_DIR)
KEYS = load_keys()
KEY_7334 = KEYS.get(7334)
FILE_7334 = BUILT_MAPS / "7334_0905131019X.swf"
needs_7334 = pytest.mark.skipif(KEY_7334 is None or not FILE_7334.exists(), reason="clé ou carte du client absente")


class Net:
    def __init__(self, map_id=None, map_key=None):
        self.map_id, self.map_key = map_id, map_key


def references() -> dict[str, dict]:
    entries = {}
    for name in ("combat_monde_des_douze.json", "combat_incarnam.json"):
        path = REFERENCES / name
        if path.exists():
            entries.update(json.loads(path.read_text(encoding="utf-8")))
    return entries


@needs_7334
def test_map_minus_1_minus_16_deciphered_with_the_key_heard_from_the_server():
    # 31/10 : -1,-16 (7334, carte de la récolte du 27/10) ; ses arbres sont les objets interactifs de la carte.
    found = load_map(7334, KEY_7334)
    assert found is not None and found.map_id == 7334 and found.date == "0905131019" and found.width == 15
    by_number = {found.numbers[cell]: gfx for cell, gfx in found.interactive.items()}
    assert {75, 168, 206, 207, 321, 366, 438} <= set(by_number)  # Noyer, Frênes, Châtaigniers (tests/test_recolte.py)
    assert by_number[168] == by_number[206] == by_number[321] == by_number[366]  # mêmes arbres, même graphisme
    assert map_cell(168) in found.interactive and found.suns and found.suns <= found.walkable | found.sight_blockers
    assert found.blocks == found.sight_blockers - found.walkable and not found.blocks & found.holes
    assert found.walkable | found.blocks | found.holes == found.cells and len(found.cells) == 479
    assert load_map(7334, KEY_7334) is found  # gardée en mémoire


@pytest.mark.skipif(not KEYS or not references() or not BUILT_MAPS.exists(), reason="clés, références ou client absents")
def test_blocks_and_holes_agree_with_the_combat_reference():
    # 31/10 : mêmes définitions que tools/reference_combat.py ; mesuré : 60 cartes sur 60, blocs et trous identiques.
    entries = references()
    compared = 0
    for map_id, key in KEYS.items():
        found, entry = load_map(map_id, key), entries.get(str(map_id))
        if found is None or entry is None:
            continue
        compared += 1
        assert found.blocks == {tuple(c) for c in entry["blocs"]}, map_id
        assert found.holes == {tuple(c) for c in entry["trous"]}, map_id
    assert compared


def test_no_map_without_its_key_or_its_file(monkeypatch, tmp_path):
    monkeypatch.setattr("bot.brain.harvest.KEYS", tmp_path / "cles_cartes.json")  # aucune clé retenue
    assert load_map(7334, None) is None and load_map(None, "abcd") is None
    assert current_map(Net()) is None and current_map(Net(7334)) is None
    assert fight_arena(Net(7334)) is None and fight_arena(None) is None
    assert load_map(7334, "00ff", directories=[tmp_path]) is None  # fichier absent


@needs_7334
def test_wrong_key_and_key_older_than_the_installed_version(tmp_path):
    # 31/10 : une clé ne déchiffre qu'une version (1906 et 1911 : l'ancienne clé ne va pas à la nouvelle version). Une
    # clé qui ne déchiffre qu'une version plus ancienne que celle du jeu est périmée : carte refusée.
    assert load_map(7334, KEYS.get(7335) or "41" * 40, directories=[BUILT_MAPS]) is None  # clé d'une autre carte
    shutil.copy(FILE_7334, tmp_path / FILE_7334.name)
    shutil.copy(BUILT_MAPS / "7335_0905131019X.swf", tmp_path / "7334_2610300000X.swf")  # « nouvelle version »
    assert load_map(7334, KEY_7334, directories=[tmp_path]) is None
    assert load_map(7334, KEY_7334, date="0905131019", directories=[tmp_path]).date == "0905131019"  # version du GDM


@needs_7334
def test_fight_arena_from_the_client_map_with_the_reference_coords_and_placements(monkeypatch, tmp_path):
    # 31/10 : la carte où l'on est (GDM), sans reconnaissance ; coordonnées et placements de la référence.
    found = load_map(7334, KEY_7334)
    arena = fight_arena(Net(7334, KEY_7334))
    assert arena.ident == "7334" and arena.coords == "?" and arena.placements == ()  # pas de référence (conftest)
    assert arena.blocks == found.blocks and arena.holes == found.holes
    assert arena.sight_only == found.sight_blockers & found.walkable
    walkable = sorted(found.walkable)
    folder = tmp_path / "references"
    folder.mkdir()
    entry = {"carte": "-1,-16", "blocs": [], "trous": [], "placements": [[list(walkable[0])], [list(walkable[1])]]}
    (folder / "combat_monde_des_douze.json").write_text(json.dumps({"7334": entry}), encoding="utf-8")
    monkeypatch.setattr("bot.brain.arena.Arenas._loaded", None)
    arena = client_arena(7334, KEY_7334)
    assert arena.coords == "-1,-16" and arena.placements == (frozenset({walkable[0]}), frozenset({walkable[1]}))
    entry["placements"] = [[list(sorted(found.blocks)[0])], [list(walkable[1])]]  # placement sur un bloc : périmés
    (folder / "combat_monde_des_douze.json").write_text(json.dumps({"7334": entry}), encoding="utf-8")
    monkeypatch.setattr("bot.brain.arena.Arenas._loaded", None)
    assert client_arena(7334, KEY_7334).placements == ()


def test_reference_of_a_map_changed_by_a_game_update_is_not_recognized(monkeypatch, tmp_path):
    # 31/10 : 1906 et 1911 ont changé depuis la construction des références : leurs blocs ne sont plus ceux du jeu.
    from test_arena import BLOCKS, HOLES, entries, terrain_of

    (tmp_path / "combat_incarnam.json").write_text(json.dumps(entries()), encoding="utf-8")
    monkeypatch.setattr("bot.brain.arena.changed_maps", lambda: [1, 1906])
    arenas = Arenas.load(tmp_path)
    assert len(arenas) == 1 and arenas.by_terrain(terrain_of(BLOCKS, HOLES)) is None
    assert arenas.by_ident(1).coords == "4,3"  # coordonnées et placements toujours donnés


def test_cache_does_not_keep_maps_read_from_other_folders(tmp_path):
    assert load_map(7334, "00ff", directories=[tmp_path]) is None
    assert not any(memo[1] == "00ff" for memo in mapdata._cache)
