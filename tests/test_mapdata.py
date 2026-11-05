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
    assert found.blocks == found.sight_blockers and not found.blocks & found.holes  # arbres compris (01/11)
    assert found.walkable | found.blocks | found.holes == found.cells and len(found.cells) == 479
    assert load_map(7334, KEY_7334) is found  # gardée en mémoire


# Cases sans ligne de vue et pas interactives (pas des arbres), que la référence relevée à l'écran ne compte pas comme
# des blocs : écarts connus, à vérifier en jeu (traversables en combat ?).
UNSURE_BLOCKS = {7453: {(8, 8)}}


@pytest.mark.skipif(not KEYS or not references() or not BUILT_MAPS.exists(), reason="clés, références ou client absents")
def test_blocks_and_holes_agree_with_the_combat_reference():
    # 31/10 : mêmes définitions que tools/reference_combat.py ; mesuré : 60 cartes sur 60, blocs et trous identiques.
    # 01/11 : les arbres (objets interactifs sans ligne de vue) sont des blocs ; la référence les compte comme du sol.
    entries = references()
    compared = 0
    for map_id, key in KEYS.items():
        found, entry = load_map(map_id, key), entries.get(str(map_id))
        if found is None or entry is None:
            continue
        compared += 1
        blocks = {tuple(c) for c in entry["blocs"]}
        # 05/11 : 7,-25 (clé apprise ce jour) : case (8,8), déplacement 1, sans ligne de vue, objet 286 PAS interactif
        # (pas un arbre) ; la référence relevée à l'écran n'y voit pas de bloc. Écart connu, à vérifier en jeu.
        known = UNSURE_BLOCKS.get(map_id, set())
        assert blocks <= found.blocks and found.blocks - blocks - known <= set(found.interactive), map_id
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
    assert not found.walkable & found.sight_blockers  # arbres : des blocs (01/11)
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


def test_a_tree_without_line_of_sight_is_a_block():
    # 01/11 (journal 071237) : arbres (objet interactif, déplacement 1, pas de ligne de vue) pris pour des cases
    # praticables ; 5 clics de déplacement sur 5 vers l'un d'eux, ou par l'un d'eux, sans effet.
    from bot.brain.mapdata import _build

    floor = {"active": True, "ligne_de_vue": True, "deplacement": 4, "objet2": 0, "objet2_interactif": False}
    tree = dict(floor, ligne_de_vue=False, deplacement=1, objet2=7500, objet2_interactif=True)
    found = _build(1, "0", 15, [floor, tree])
    assert found.walkable == {map_cell(0)} and found.blocks == {map_cell(1)} and map_cell(1) in found.interactive


@needs_7334
def test_first_fight_recognized_by_the_reference_takes_the_client_map(monkeypatch, tmp_path):
    # 01/11 : 1er combat de la session, pas de GDM entendu ; la carte reconnue par la référence (qui compte les arbres
    # comme du sol) est remplacée par sa carte du client quand sa clé est retenue.
    from bot.brain.combat import CombatAI, CombatSettings
    from conftest import load_frame
    from test_brain import FakeGame

    found = load_map(7334, KEY_7334)
    walkable = sorted(found.walkable)
    placements = [[list(c) for c in walkable[:6]], [list(c) for c in walkable[-6:]]]
    entry = {"carte": "-1,-16", "blocs": [], "trous": [], "placements": placements}
    folder = tmp_path / "references"
    folder.mkdir()
    (folder / "combat_monde_des_douze.json").write_text(json.dumps({"7334": entry}), encoding="utf-8")
    monkeypatch.setattr("bot.brain.arena.Arenas._loaded", None)
    ai = CombatAI(FakeGame(load_frame("combat_mon_tour")), CombatSettings())
    ai._recognize_arena(placed={tuple(c) for c in placements[0] + placements[1]})
    assert ai.map.arena.from_client and ai.map.arena.blocks == found.blocks


def test_relief_of_the_client_map():
    # 01/11 : niveau du sol et pente de chaque case (carte 0,-23 : sommets, pentes) ; à plat au niveau 7.
    from bot.brain.mapdata import _build

    flat = {"active": True, "ligne_de_vue": True, "deplacement": 4, "objet2": 0, "objet2_interactif": False,
            "niveau_sol": 7, "pente": 1}
    found = _build(1, "0", 15, [flat, dict(flat, niveau_sol=8, pente=9), dict(flat, niveau_sol=9, deplacement=0)])
    assert found.lift == {map_cell(1): 1.5, map_cell(2): 2.0}
