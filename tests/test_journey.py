"""Mode aller (--aller x,y) : plus court chemin d'une carte à une autre, sans combat (brain/travel.py, Journey)."""

import argparse
import sys

import pytest

import bot.__main__ as launcher
from bot.brain.atlas import Atlas, MapInfo
from bot.brain.loop import Bot
from bot.brain.travel import (
    BLOCKED,
    WORLDS,
    Journey,
    Traveler,
    TravelSettings,
    World,
    ZoneRoamer,
    _parse_coords,
    shortest_path,
)
from bot.config import load_settings
from bot.perception.exits import DELTA, OPPOSITE
from bot.team.loop import TeamBot
from bot.team.roster import TeamSettings
from conftest import load_frame
from test_brain import FakeGame, clicks
from test_team import Screen, member
from test_travel import SimTraveler

CEMETERY = (("Incarnam", "Cimetière"),)


def references(maps: dict, links: dict | None = None) -> Atlas:
    """Références simulées : `maps` coordonnées -> zone. Sans `links`, comme le Monde des Douze : pas de soleils,
    les voisines d'à côté qui existent ; `links` : coordonnées -> {côté: voisine}, liaisons des soleils (Incarnam)."""
    return Atlas({f"Incarnam {x},{y}": MapInfo("Incarnam", (x, y), zone, leads_to=(links or {}).get((x, y), {}))
                  for (x, y), zone in maps.items()})


class GameSim(SimTraveler):
    """Le jeu simulé derrière le Traveler : `real` (carte, côté) -> carte où mène vraiment le soleil (sinon la
    voisine d'à côté) ; `walls` : (carte, côté) sans passage en jeu (falaise, eau), que le Traveler note bloqué."""

    on_crossed = None

    def __init__(self, maps, start, atlas=None, real=None, walls=(), **avoid):
        super().__init__(maps, start, **avoid)
        self.atlas = atlas if atlas is not None else references(maps)
        self.real, self.walls = real or {}, set(walls)

    def go(self, frame, label, side, exits=None):
        before = self.key(label)
        dx, dy = DELTA[side]
        new = self.real.get((self.pos, side), (self.pos[0] + dx, self.pos[1] + dy))
        if (self.pos, side) in self.walls or new not in self.maps:
            self.world.link(before, side, BLOCKED)  # comme le vrai Traveler : sortie introuvable, notée bloquée
            return None
        self.pos = new
        self.moves.append(side)
        arrived = self.label(new)
        self.world.link(before, side, self.key(arrived))
        self.world.link(self.key(arrived), OPPOSITE[side], before)
        if self.on_crossed is not None:
            self.on_crossed(side, (0, 0), label, arrived)
        return arrived


def travel(journey: Journey, limit: int = 100) -> int:
    """Pas du trajet jusqu'à son arrêt (comme la boucle du bot) ; renvoie le nombre de pas."""
    for count in range(1, limit + 1):
        journey.step()
        if journey.done:
            return count
    raise AssertionError("trajet jamais terminé")


def grid(width: int, height: int, zone: str = "Forêt") -> dict:
    return {(x, y): zone for x in range(width) for y in range(height)}


def test_shortest_route_logged_then_stop_on_arrival(caplog):
    caplog.set_level("INFO")
    sim = GameSim(grid(5, 4), (0, 0))
    journey = Journey(sim, (3, 2))
    steps = travel(journey)
    assert sim.pos == (3, 2) and len(sim.moves) == 5  # le plus court : 3 à droite, 2 vers le bas
    assert steps == 6  # 5 changements de carte, puis l'arrivée constatée : plus aucun clic
    assert "Itinéraire de 0,0 à 3,2 : 5 carte(s) : " in caplog.text
    assert "Arrivé en 3,2 (5 changement(s) de carte)" in caplog.text


def test_already_on_the_destination():
    sim = GameSim(grid(2, 2), (1, 1))
    journey = Journey(sim, (1, 1))
    assert travel(journey) == 1 and sim.moves == []


def test_forbidden_zone_is_walked_around():
    maps = {**grid(5, 2), (2, 0): "Cimetière"}
    sim = GameSim(maps, (0, 0), avoid_zones=CEMETERY)
    journey = Journey(sim, (4, 0))
    seen = [sim.pos]
    for _ in range(20):
        journey.step()
        seen.append(sim.pos)
        if journey.done:
            break
    assert journey.done and sim.pos == (4, 0)
    assert (2, 0) not in seen and len(sim.moves) == 6  # tout droit (4 cartes) traverserait le Cimetière


def test_forbidden_map_is_walked_around():
    sim = GameSim(grid(3, 2), (0, 0), avoid_maps=(("Incarnam", (1, 0)),))
    journey = Journey(sim, (2, 0))
    travel(journey)
    assert sim.pos == (2, 0) and sim.moves == ["bas", "droite", "droite", "haut"]


def test_forbidden_destination_refused(caplog):
    maps = {**grid(3, 1), (2, 0): "Cimetière"}
    sim = GameSim(maps, (0, 0), avoid_zones=CEMETERY)
    journey = Journey(sim, (2, 0))
    assert travel(journey) == 1 and sim.moves == []
    assert "la carte d'arrivée 2,0 est interdite (zone Cimetière interdite" in caplog.text


def test_new_route_when_the_sun_leads_elsewhere(caplog):
    caplog.set_level("INFO")
    links = {(0, 0): {"droite": (1, 0), "bas": (0, 1)}}  # références : le soleil de droite mène à 1,0...
    sim = GameSim(grid(3, 2), (0, 0), atlas=references(grid(3, 2), links), real={((0, 0), "droite"): (1, 1)})
    journey = Journey(sim, (2, 0))
    travel(journey)
    assert sim.pos == (2, 0) and journey.reroutes == 1
    assert "Carte 1,1 au lieu de 1,0 : nouvel itinéraire" in caplog.text  # ... il mène en fait à 1,1
    assert "Nouvel itinéraire de 1,1 à 2,0 : 2 carte(s)" in caplog.text
    # Ce que le bot a vu prime ensuite sur les références : de 0,0, la droite mène à 1,1.
    sim.pos = (0, 0)
    again = Journey(sim, (1, 1))
    moves = len(sim.moves)
    travel(again)
    assert sim.moves[moves:] == ["droite"]


def test_new_route_around_an_exit_that_does_not_exist():
    # Falaise entre 1,0 et 2,0, pourtant voisines d'après les références (Monde des Douze : pas de soleils décrits).
    sim = GameSim(grid(3, 2), (0, 0), walls=[((1, 0), "droite")])
    journey = Journey(sim, (2, 0))
    travel(journey)
    assert sim.pos == (2, 0) and sim.moves == ["droite", "bas", "droite", "haut"]
    assert sim.world.destination("Incarnam 1,0", "droite") == BLOCKED
    assert journey.failures == 0 and journey.reroutes == 1


def test_gives_up_after_repeated_failures(caplog):
    sim = GameSim(grid(2, 1), (0, 0))
    sim.go = lambda frame, label, side, exits=None: None  # agressions à répétition : jamais de changement de carte
    journey = Journey(sim, (1, 0))
    assert travel(journey) == 5 and sim.moves == []
    assert "5 changements de carte ratés d'affilée" in caplog.text


@pytest.mark.parametrize("maps, memory, goal, why", [
    ({(0, 0): "Forêt", (1, 0): "Forêt", (5, 5): "Forêt"}, {}, (5, 5), "aucune liaison connue"),  # île
    (grid(2, 1), {}, (9, 9), "carte 9,9 inconnue de Incarnam"),  # absente des références
    ({(0, 0): "Forêt", (1, 0): "Cimetière", (2, 0): "Forêt"}, {}, (2, 0), "cartes ou zones interdites"),
    (grid(2, 1), {"Incarnam 0,0": {"sorties": {"droite": BLOCKED}}}, (1, 0), "sorties notées « bloquée »"),
])
def test_impossible_route_stops_with_a_clear_message(caplog, maps, memory, goal, why):
    sim = GameSim(maps, (0, 0), avoid_zones=CEMETERY)
    sim.world.maps.update(memory)
    journey = Journey(sim, goal)
    assert travel(journey) == 1 and sim.moves == []
    assert "Trajet vers" in caplog.text and why in caplog.text


def test_blocked_exit_named_in_the_message(caplog):
    sim = GameSim(grid(2, 1), (0, 0))
    sim.world.link("Incarnam 0,0", "droite", BLOCKED)
    travel(Journey(sim, (1, 0)))
    assert "aucun chemin de 0,0 à 1,0 : il passe par des sorties notées « bloquée » (0,0 côté droite)" in caplog.text


def test_unknown_start_position_asks_for_it(caplog):
    pale = load_frame("hors_combat_nom_illisible")  # nom illisible, pas de --position
    t = Traveler(FakeGame(pale), TravelSettings(mode="aller", destination=(1, 0)), World(None), Atlas.load())
    journey = Journey(t, (1, 0))
    assert travel(journey) == 1 and clicks(t.game) == []
    assert "relance avec --position x,y" in caplog.text


def test_real_traveler_clicks_the_reference_sun_then_reroutes(caplog):
    caplog.set_level("INFO")
    pale = load_frame("hors_combat_nom_illisible")  # Incarnam 1,1 : le personnage ne bouge jamais
    settings = TravelSettings(mode="aller", start=(1, 1), settle=0, walk_timeout=0.05, destination=(1, 0))
    t = Traveler(FakeGame(pale), settings, World(None), Atlas.load())
    journey = Journey(t, (1, 0))
    journey.step()
    assert "Itinéraire de 1,1 à 1,0 : 1 carte(s) : 1,0" in caplog.text
    assert pale.grid.cell_at(*clicks(t.game)[0]) == (21, 1)  # soleil du haut de 1,1, d'après config/monde
    # Nom de la carte illisible (position d'après le chemin) : bloquée pour ce trajet seulement, rien d'écrit (11/10).
    assert ("Incarnam 1,1", "haut") in t.blocked_here and t.world.destination("Incarnam 1,1", "haut") is None
    assert journey.failures == 1
    journey.step()
    assert "Nouvel itinéraire de 1,1 à 1,0 : 3 carte(s)" in caplog.text  # par 2,1 et 2,0


def test_incarnam_route_never_crosses_the_cemetery():
    atlas = Atlas.load()
    maps = {m.coords: m.zone for m in atlas.maps.values() if m.region == "Incarnam"}
    sim = GameSim(maps, (2, 6), atlas=atlas, avoid_zones=CEMETERY)
    journey = Journey(sim, (6, 5))
    seen = {sim.pos}
    for _ in range(40):
        journey.step()
        seen.add(sim.pos)
        if journey.done:
            break
    assert journey.done and sim.pos == (6, 5)
    assert not seen & {(3, 6), (4, 6), (5, 6)}


def test_world_of_twelve_route_from_the_reference_maps(caplog):
    caplog.set_level("INFO")
    atlas = Atlas.load()
    maps = {m.coords: m.zone for m in atlas.maps.values() if m.region == "Monde des Douze"}
    sim = GameSim(maps, (4, -11), atlas=atlas)  # Le coin des Tofus
    sim.region = "Monde des Douze"
    expected = shortest_path((4, -11), {(5, -18)}, lambda c: sim.atlas.links("Monde des Douze", c),
                             lambda c: sim.atlas.get("Monde des Douze", c) is not None)
    journey = Journey(sim, (5, -18))  # Cité d'Astrub
    travel(journey)
    assert sim.pos == (5, -18) and len(sim.moves) == len(expected) >= 8
    assert "références sans soleils : chaque sortie est cherchée à l'écran" in caplog.text


def test_followers_follow_every_crossing_in_team_mode(tmp_path):
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json"),
                          "destination": "2,0"}
    screen = Screen()
    a, b = member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("hors_combat"))
    bot = TeamBot([a, b], settings, TeamSettings(enabled=True), engage=False, travel="aller")
    assert bot.engager is None and isinstance(bot.roamer, Journey)
    assert bot.roamer.traveler.on_crossed == bot._crossed  # passages du principal refaits par les suiveurs
    sim = GameSim(grid(3, 1), (0, 0))
    sim.on_crossed = bot._crossed
    bot.roamer = Journey(sim, (2, 0))
    followed = []
    bot.follow = followed.append
    for _ in range(5):
        bot._roam()
        if bot.finished:
            break
    assert bot.finished and [c.side for c in followed] == ["droite", "droite"]


def test_destination_settings():
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "destination": "5, -18"}
    assert TravelSettings.from_settings(settings, "aller").destination == (5, -18)
    settings["travel"]["destination"] = ""
    with pytest.raises(ValueError, match="carte d'arrivée manquante"):
        TravelSettings.from_settings(settings, "aller")
    assert _parse_coords([3, 4]) == (3, 4) and _parse_coords(None) is None
    with pytest.raises(ValueError):
        _parse_coords("ici")


def test_bot_in_goto_mode_launches_no_fight(tmp_path):
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json")}
    args = argparse.Namespace(aller=(4, 3), travel=None)
    assert launcher._goto(args, settings) and args.travel == "aller"
    bot = Bot(FakeGame(load_frame("hors_combat")), settings, engage=False, travel=args.travel)
    assert bot.engager is None and isinstance(bot.roamer, Journey) and bot.roamer.goal == (4, 3)
    assert not launcher._goto(argparse.Namespace(aller=None, travel="zone"), load_settings())


def parsed(monkeypatch, *argv: str) -> argparse.Namespace:
    """Options de la ligne de commande telles que bot/__main__.py les lit (il s'arrête juste après : « un autre bot
    tourne déjà »), sans rien lancer."""
    seen = []
    parse = argparse.ArgumentParser.parse_args

    def keep(self, *args, **kwargs):
        seen.append(parse(self, *args, **kwargs))
        return seen[-1]

    def never(*args, **kwargs):
        raise AssertionError("le bot ne doit pas démarrer pendant les tests")

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", keep)
    monkeypatch.setattr(launcher, "single_instance", lambda: None)
    for name in ("load_settings", "find_windows", "run_team"):  # au cas où : jamais de fenêtre du jeu touchée
        monkeypatch.setattr(launcher, name, never)
    monkeypatch.setattr(sys, "argv", ["bot", *argv])
    assert launcher.main() == 1
    return seen[0]


def test_goto_option_on_the_command_line(monkeypatch):
    args = parsed(monkeypatch, "--monde", "douze", "--position", "12,-28", "--aller", "5,-18", "--equipe",
                  "--principal", "Jesuisleking", "--verbose")
    assert args.aller == (5, -18) and args.position == (12, -28) and args.monde == "douze"
    assert args.equipe and args.principal == "Jesuisleking" and args.verbose
    settings = load_settings()
    assert launcher._goto(args, settings)
    travel_settings = TravelSettings.from_settings(settings, args.travel, args.position, WORLDS[args.monde])
    assert travel_settings.mode == "aller" and travel_settings.destination == (5, -18)
    assert travel_settings.start == (12, -28) and travel_settings.region == "Monde des Douze"
    assert parsed(monkeypatch, "--aller=-1,-20").aller == (-1, -20)  # x négatif : avec « = »
    with pytest.raises(SystemExit):
        parsed(monkeypatch, "--aller", "1,1", "--explore")  # un seul mode de déplacement à la fois


def test_zone_mode_started_away_from_the_zone_travels_to_it(caplog):
    # 24/10 : --combat-zone -3,-30 donné depuis Astrub ; le bot a répété « hors de la zone : je reviens » 30 min sans
    # bouger (aucun côté par où revenir). Il fait maintenant le trajet jusqu'à la carte la plus proche de la zone.
    caplog.set_level("INFO")
    atlas = Atlas.load()
    maps = {m.coords: m.zone for m in atlas.maps.values() if m.region == "Monde des Douze"}
    sim = GameSim(maps, (4, -11), atlas=atlas)  # Le coin des Tofus
    sim.region = "Monde des Douze"
    roamer = ZoneRoamer(sim)
    sim.pos = (5, -18)  # Cité d'Astrub
    for _ in range(40):
        roamer.step()
        if atlas.zone("Monde des Douze", sim.pos) == "Le coin des Tofus":
            break
    assert atlas.zone("Monde des Douze", sim.pos) == "Le coin des Tofus" and not roamer.done
    assert "Hors de la zone Le coin des Tofus : trajet jusqu'à" in caplog.text


def test_route_only_through_the_reference_exits_of_a_described_map():
    # 25/10 (journal 201109) : Taïnéla vers les Blops, l'itinéraire passait par des sorties absentes de la référence (en
    # bas de 1,-29, à gauche de -2,-30), cherchées puis notées bloquées l'une après l'autre. Sur une carte que la
    # référence décrit, seules ses sorties (et celles déjà prises en jeu) sont des passages.
    from bot.brain.travel import Passages

    sim = GameSim(grid(3, 3), (0, 0))
    journey = Journey(sim, (2, 0))
    journey.passages = Passages({}, {"1,0": {"sorties": {"gauche": {"vers": "0,0"}}},  # long détour par la 3e rangée
                                     "1,1": {"sorties": {"gauche": {"vers": "0,1"}}}})
    journey.step()
    route = [journey.at] + [coords for _, coords in journey.path or []]
    steps = list(zip(route, route[1:]))
    assert ((1, 0), (2, 0)) not in steps and ((1, 1), (2, 1)) not in steps and route[-1] == (2, 0)


def test_last_digit_lost_in_the_reading_is_not_a_teleport():
    # 25/10 (journal 201109) : « 0,-2 » lu en 0,-29, « 0,-3 » en 0,-30 : cru téléporté, aller-retour sans fin.
    from bot.brain.travel import _truncated

    assert _truncated((0, -2), (0, -29)) and _truncated((0, -3), (0, -30)) and _truncated((1, -3), (12, -3))
    assert not _truncated((0, -2), (0, -3)) and not _truncated((0, -2), (1, -29)) and not _truncated((0, -29), (0, -29))
