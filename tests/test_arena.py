import json

from bot.brain.arena import Arenas
from bot.brain.combat import MapMemory
from bot.perception.placement import read_placement
from bot.perception.sight import Terrain
from conftest import load_frame

BLOCKS = [[4, 10], [5, 11], [6, 12], [10, 4], [12, 20]]
HOLES = [[0, 0], [1, 1], [2, 0]]


def entries(**more) -> dict:
    """Deux cartes : 1 (BLOCKS, HOLES) et 2 (d'autres blocs), plus `more`."""
    return {"1": {"carte": "4,3", "blocs": BLOCKS, "trous": HOLES, "placements": [[[8, 8], [9, 9]], [[14, 14]]]},
            "2": {"carte": "5,3", "blocs": [[20, 20], [21, 21]], "trous": []}, **more}


def terrain_of(blocks, holes, floor_cells=80) -> Terrain:
    taken = {tuple(c) for c in blocks} | {tuple(c) for c in holes}
    floor = {(i, j) for i in range(2, 28) for j in range(2, 30) if (i + j) % 2 == 0 and (i, j) not in taken}
    return Terrain(set(sorted(floor)[:floor_cells]), {tuple(c) for c in blocks}, {tuple(c) for c in holes})


def test_arena_recognized_from_the_terrain_read_in_tactical_mode():
    # 24/10 : la carte officielle (blocs, trous) reconnue d'après le terrain lu, sans les coordonnées.
    arenas = Arenas(entries())
    found = arenas.by_terrain(terrain_of(BLOCKS, HOLES))
    assert found is not None and found.coords == "4,3" and (4, 10) in found.blocks and (0, 0) in found.holes
    assert arenas.by_terrain(terrain_of(BLOCKS, HOLES, floor_cells=10)) is None  # trop peu de cases lues
    twins = Arenas(entries(**{"3": {"carte": "6,3", "blocs": BLOCKS, "trous": HOLES}}))
    assert twins.by_terrain(terrain_of(BLOCKS, HOLES)) is None  # deux cartes pareilles : pas de choix au hasard


def test_arena_recognized_from_the_placement_cells():
    # 24/10 : sur les 7 captures de placement des tests, toutes les cases lues étaient des placements de la carte
    # reconnue (StarLoco) ; la seule carte qui les contient toutes est retenue.
    placement = read_placement(load_frame("placement_foret"))
    red, blue = placement.free["red"] | placement.occupied["red"], placement.free["blue"] | placement.occupied["blue"]
    camps = [[list(c) for c in red | {(2, 2)}], [list(c) for c in blue]]  # une case cachée en plus
    arenas = Arenas(entries(**{"10343": {"carte": "2,4", "blocs": [], "trous": [], "placements": camps}}))
    assert arenas.by_placement(red | blue).coords == "2,4"
    assert arenas.by_placement(set(list(red)[:3])) is None  # trop peu de cases
    both = Arenas(entries(**{"a": {"carte": "2,4", "placements": camps}, "b": {"carte": "9,9", "placements": camps}}))
    assert both.by_placement(red | blue) is None


def test_official_arena_wins_over_what_the_screen_reads():
    # Contrôle du 24/10 : aucun bloc officiel lu comme du sol, 1513 sols officiels lus comme des blocs à l'écran
    # (chiffres, animations) : la carte officielle reconnue fait foi pour le combat.
    memory = MapMemory()
    memory.adopt(Arenas(entries()).arenas[0])
    memory.learn_terrain(Terrain({(4, 10)}, {(20, 20)}, set()), [])  # bloc officiel lu sol, sol lu bloc
    assert (4, 10) in memory.obstacles and (20, 20) not in memory.obstacles
    assert (0, 0) in memory.unwalkable and (0, 0) not in memory.obstacles  # trou : on tire par-dessus
    assert memory.known and memory.unseen({(7, 7)}) == set()


def test_arenas_loaded_from_the_local_reference(tmp_path):
    (tmp_path / "combat_incarnam.json").write_text(json.dumps(entries()), encoding="utf-8")
    assert len(Arenas.load(tmp_path)) == 2
    assert len(Arenas.load()) == 0  # tests : sans la référence locale (conftest), rien n'est reconnu
    assert Arenas.load().by_terrain(terrain_of(BLOCKS, HOLES)) is None


def test_maps_changed_by_a_game_update_are_reported(tmp_path):
    # 24/10 : la référence fait foi ; une mise à jour du jeu (nouvelle version d'une carte) la périme : signalé au
    # lancement, d'après les dates dans les noms des fichiers de cartes.
    from bot.brain.arena import changed_maps

    installed, built = tmp_path / "jeu", tmp_path / "copie"
    installed.mkdir()
    built.mkdir()
    for name in ("10000_0706131721X.swf", "10001_0801011200X.swf", "10002_0706131721X.swf"):
        (installed / name).write_bytes(b"")
    for name in ("10000_0706131721X.swf", "10001_0706131721X.swf"):
        (built / name).write_bytes(b"")
    assert changed_maps(installed, built) == [10001]  # 10002 absente de la copie : pas dans les références
    assert changed_maps(tmp_path / "absent", built) == []
