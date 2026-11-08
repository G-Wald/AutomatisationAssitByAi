"""Sortie d'un bâtiment (brain/building.py, 08/11) : bâtiment reconnu d'après la carte annoncée par le serveur et
l'index des cartes du client, sorties d'après la carte du client (soleils au sol)."""

from contextlib import contextmanager
from types import SimpleNamespace

import bot.brain.building as building
from conftest import load_frame


def test_buildings_are_indoor_maps_of_an_outdoor_sub_zone():
    # Taverne de 5,-17 (07/11, Waldhary connecté dedans) et banque d'Astrub : des bâtiments ; la rue, les salles de
    # l'Ensablé et du donjon des Bouftous : non (une salle de donjon n'est jamais quittée par ici).
    assert all(building.is_building(m) for m in (7572, 7573, 7574, 7549))
    assert not any(building.is_building(m) for m in (7429, 7414, 10156, 10165, 2074, None, 999999))


class Net:
    def __init__(self, rooms):
        self.map_id, self.rooms, self.clicked = 7573, rooms, []

    def my_grid_cell(self):
        return (12, 12)

    def wait_new_map(self, after, timeout):
        new = self.rooms.get((self.map_id, self.clicked[-1]))
        if new is not None:
            self.map_id = new
        return new


class Game:
    def __init__(self, net):
        self.frame, self.net, self.held = load_frame("hors_combat"), net, []

    def look(self, park=True):
        return self.frame

    def click(self, point, spread=(0, 0)):
        self.net.clicked.append(self.frame.grid.cell_at(*point))

    @contextmanager
    def holding(self, key):
        self.held.append(key)
        yield


def test_leave_through_the_nearest_sun_then_the_next_room(monkeypatch):
    # Pièce 7573 : le soleil le plus proche ne mène nulle part (pas de nouvelle carte), le suivant dans une autre pièce
    # (7572), dont un soleil mène dehors (7429).
    suns = {7573: {(9, 13), (8, 24)}, 7572: {(14, 10)}}
    monkeypatch.setattr(building, "current_map", lambda net: SimpleNamespace(suns=frozenset(suns[net.map_id])))
    net = Net({(7573, (8, 24)): 7572, (7572, (14, 10)): 7429})
    game = Game(net)
    assert building.leave_building(game, net, "e", "Waldhary-[Gal]")
    assert net.map_id == 7429 and net.clicked == [(9, 13), (8, 24), (14, 10)] and game.held == ["e"] * 3


def test_room_without_known_exit_is_a_failure(monkeypatch):
    monkeypatch.setattr(building, "current_map", lambda net: None)  # clé pas encore entendue
    monkeypatch.setattr(building, "_screen_suns", lambda game: [])  # et aucun soleil vu à l'écran
    net = Net({})
    assert not building.leave_building(Game(net), net)
    assert net.clicked == []


def test_map_not_yet_announced_dark_room_left_by_a_sun_seen_on_screen(monkeypatch):
    # 08/11 : Waldhary entré dans la taverne avant que le bot écoute ; le serveur ne redit la carte qu'au changement
    # suivant. Pièce sombre à l'écran : bâtiment ; sortie par un soleil vu au sol, la carte d'arrivée est annoncée.
    import bot.team.bank as bank

    monkeypatch.setattr(bank, "dark_share", lambda frame: 0.5)
    monkeypatch.setattr(building, "_screen_suns", lambda game: [(8, 24)])
    net = Net({(None, (8, 24)): 7429})
    net.map_id = None
    assert building.leave_building(Game(net), net, "e", "Waldhary-[Gal]")
    assert net.map_id == 7429 and net.clicked == [(8, 24)]


def test_bank_exit_is_a_ground_sun_of_the_client_map():
    # 08/11 : dans les bâtiments, le soleil est au sol (objet1 1030), pas dans objet2 comme au bord des cartes de dehors.
    import pytest

    from bot.brain.mapdata import known_key, load_map

    key = known_key(7549)
    data = load_map(7549, key) if key else None
    if data is None:
        pytest.skip("carte du client ou clé de la banque absente")
    assert data.suns == frozenset({(8, 26)})


def test_floor_suns_of_the_tavern_seen_on_screen():
    # Capture du 07/11 (Waldhary dans la taverne de 5,-17, carte 7573) : sorties au sol vues à l'écran, parmi celles de la
    # carte du client ((8, 24), (9, 25), (21, 23), (22, 22), (6, 10)).
    from bot.perception.exits import find_floor_suns

    cells = {exit_.cell for exit_ in find_floor_suns(load_frame("batiment_taverne_5_-17"))}
    assert cells and cells <= {(8, 24), (9, 25), (21, 23), (22, 22), (6, 10)}
