import pytest

from bot.perception.blobs import find_blobs
from bot.perception.colors import TEAMS, team_mask
from bot.perception.grid import Grid, distance
from bot.perception.layout import Layout
from conftest import load_frame

GRID = Grid.for_layout(Layout(1918, 1008))


@pytest.mark.parametrize("cell", [(9, 13), (10, 20), (0, 0), (27, 1), (14, 30)])
def test_center_and_cell_at_round_trip(cell):
    x, y = GRID.center(cell)
    assert GRID.cell_at(x, y) == cell
    # Un point dans le losange, près d'un coin, reste dans la même case.
    assert GRID.cell_at(x + GRID.half_w * 0.9, y) == cell
    assert GRID.cell_at(x, y - GRID.half_h * 0.9) == cell


def test_distance():
    assert distance((9, 13), (10, 14)) == 1  # voisine
    assert distance((9, 13), (11, 13)) == 2  # à côté à l'écran = 2 pas
    assert distance((9, 13), (9, 21)) == 8


def test_placement_cells_fall_on_grid():
    frame = load_frame("preparation")
    box = frame.layout.map_box
    cells = [b for team in TEAMS for b in find_blobs(team_mask(frame.crop(box), team), box[:2]) if b.area > 2000]
    assert len(cells) >= 20
    for blob in cells:
        cx, cy = blob.center
        gx, gy = frame.grid.center(frame.grid.cell_at(cx, cy))
        assert abs(gx - cx) < 2 and abs(gy - cy) < 2


def test_cells_cover_map():
    cells = GRID.cells()
    assert 400 < len(cells) < 500
    assert all((i + j) % 2 == 0 for i, j in cells)
