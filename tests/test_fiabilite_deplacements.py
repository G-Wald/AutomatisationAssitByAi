"""Fiabilité des déplacements (25/10) : itinéraire complet sur la référence, suivi à l'estime, lecture du nom de carte
seulement contrôlée, « /p %pos% » en cas de vrai doute, zaap quand aucun chemin à pied n'existe.

Journaux à l'origine : 20261025_201109 (Taïnéla vers les Blops : 12 itinéraires recalculés en 4 min, dont 7 sur une
fausse lecture « 0,-2 » / « 0,-3 », 6 recherches à la touche A, sorties inexistantes), 20261025_180222 et 175311 (7
suiveurs à la touche A en 0,-33 : 50 clics sans effet, aucun n'a rejoint), 20261025_181212 (trajets réussis)."""

import json
from pathlib import Path

import numpy as np
import pytest

from bot.brain.atlas import Atlas, MapInfo
from bot.brain.itinerary import Leg, Planner, complete, reading_fits, zaap_search_word
from bot.brain.travel import Journey, Passages, Traveler, TravelSettings, World, ZoneRoamer, _reference
from bot.config import load_settings
from bot.perception.chat import ocr_available
from bot.perception.exits import OPPOSITE
from bot.perception.maplabel import MapLabel, unreadable_label
from bot.team.loop import TeamBot
from bot.team.roster import TeamSettings
from conftest import load_frame
from test_brain import FakeGame, clicks, presses
from test_team import Screen, member

DOUZE = "Monde des Douze"
REAL_REFERENCE = Path(__file__).resolve().parents[1] / "data" / "references" / "monde_des_douze.json"


# Référence simulée

def exit_to(dest: str, *suns: tuple[tuple[int, int], tuple[int, int]]) -> dict:
    """Sortie vers `dest` : soleils (case, case d'arrivée sur `dest`)."""
    return {"vers": dest, "cases": [list(c) for c, _ in suns], "arrivees": {f"{c[0]},{c[1]}": list(a) for c, a in suns}}


def a_map(exits: dict, depuis: dict | None = None, objets: list | None = None) -> dict:
    entry = {"sorties": exits}
    if depuis is not None:
        entry["depuis_arrivee"] = {f"{c[0]},{c[1]}": {s: [list(x) for x in cells] for s, cells in by_side.items()}
                                   for c, by_side in depuis.items()}
    if objets:
        entry["objets"] = objets
    return entry


def muret_reference() -> dict:
    """Comme 1,-14 (24/10) : en 1,0, deux soleils à gauche ; celui du bas (1,29) mène en 0,0 sous un muret (case (26,30)),
    d'où la sortie gauche de 0,0 ne se joint pas ; celui du haut (1,9) mène en (26,8), d'où elle se joint."""
    return {
        "2,0": a_map({"gauche": exit_to("1,0", ((1, 15), (26, 14)))}),
        "1,0": a_map({"gauche": exit_to("0,0", ((1, 29), (26, 30)), ((1, 9), (26, 8))),
                      "droite": exit_to("2,0", ((27, 15), (2, 16)))},
                     {(26, 14): {"gauche": [(1, 29), (1, 9)], "droite": [(27, 15)]}}),
        "0,0": a_map({"gauche": exit_to("-1,0", ((1, 9), (26, 10))), "droite": exit_to("1,0", ((27, 9), (2, 8)))},
                     {(26, 30): {"gauche": [], "droite": []}, (26, 8): {"gauche": [(1, 9)], "droite": [(27, 9)]}}),
        "-1,0": a_map({"droite": exit_to("0,0", ((27, 11), (2, 10)))}),
    }


def test_route_takes_the_sun_whose_arrival_cell_keeps_the_way_open():
    # 24/10, 1,-14 : le soleil gauche du bas menait sous le rempart de 0,-14, d'où la sortie gauche est hors d'atteinte.
    # L'état de l'itinéraire est (carte, case d'arrivée) : le soleil du haut est choisi d'emblée.
    legs = Planner(muret_reference()).route((2, 0), None, {(-1, 0)})
    assert [(leg.coords, leg.side, leg.cell, leg.arrival) for leg in legs] == [
        ((2, 0), "gauche", (1, 15), (26, 14)), ((1, 0), "gauche", (1, 9), (26, 8)), ((0, 0), "gauche", (1, 9), (26, 10))]
    assert legs[1].others == ((1, 29),)  # secours du même côté
    assert legs[1].describe() == "1,0 gauche soleil (1, 9) -> 0,0 arrivée (26, 8)"


def test_route_avoids_a_passage_missed_during_the_trip():
    reference = muret_reference()
    reference["1,0"]["sorties"]["haut"] = exit_to("1,-1", ((15, 1), (14, 30)))
    reference["1,0"]["depuis_arrivee"]["26,14"]["haut"] = [[15, 1]]
    reference["1,-1"] = a_map({"gauche": exit_to("0,-1", ((1, 15), (26, 16)))})
    reference["0,-1"] = a_map({"bas": exit_to("0,0", ((13, 31), (12, 2)))})
    reference["0,0"]["depuis_arrivee"]["12,2"] = {"gauche": [[1, 9]], "droite": [[27, 9]]}
    planner = Planner(reference)
    assert [leg.side for leg in planner.route((1, 0), (26, 14), {(-1, 0)})] == ["gauche", "gauche"]
    detour = planner.route((1, 0), (26, 14), {(-1, 0)}, avoid={((1, 0), "gauche")})
    assert [leg.dest for leg in detour] == [(1, -1), (0, -1), (0, 0), (-1, 0)]


def berceau_reference() -> dict:
    """Comme le Berceau de Taïnéla : 1,0 et 2,0 fermés à pied (seulement entre eux), zaap en 1,0 ; zaap en 5,0, relié
    au but 7,0 à pied."""
    zaap = [{"nom": "Zaap", "nature": "zaap", "case": [13, 17]}]
    return {
        "1,0": a_map({"droite": exit_to("2,0", ((27, 13), (2, 12)))}, objets=zaap),
        "2,0": a_map({"gauche": exit_to("1,0", ((1, 17), (26, 18)))}),
        "5,0": a_map({"droite": exit_to("6,0", ((27, 13), (2, 12)))}, objets=[dict(zaap[0], case=[14, 20])]),
        "6,0": a_map({"droite": exit_to("7,0", ((27, 13), (2, 12))), "gauche": exit_to("5,0", ((1, 13), (26, 12)))}),
        "7,0": a_map({"gauche": exit_to("6,0", ((1, 13), (26, 12)))}),
        "9,0": a_map({}, objets=[dict(zaap[0], case=[10, 10])]),
    }


def test_closed_area_is_left_by_the_zaap_only_when_no_way_on_foot():
    planner = Planner(berceau_reference(), zaaps={(1, 0): "Le Berceau", (5, 0): "Cité d'Astrub", (9, 0): "Loin"})
    assert planner.route((2, 0), None, {(7, 0)}) is None  # à pied : aucun chemin
    legs = planner.route((2, 0), None, {(7, 0)}, zaap=True)
    assert [leg.describe() for leg in legs] == [
        "2,0 gauche soleil (1, 17) -> 1,0 arrivée (26, 18)", "1,0 zaap (13, 17) -> Cité d'Astrub 5,0",
        "5,0 droite soleil (27, 13) -> 6,0 arrivée (2, 12)", "6,0 droite soleil (27, 13) -> 7,0 arrivée (2, 12)"]
    assert planner.route((2, 0), None, {(7, 0)}, zaap=True, refused={(5, 0)}) is None  # zaap pas connu du personnage


@pytest.mark.skipif(not REAL_REFERENCE.exists(), reason="référence complétée absente (data/references)")
def test_real_reference_berceau_closed_on_foot_and_left_by_the_astrub_zaap():
    # 25/10 (journal 201109) : Elseiso en 1,-32 vers les Blops (-3,-25) ; 4 min de boucles. Le Berceau (26 cartes autour
    # du zaap 1,-32) est fermé à pied d'après la carte officielle : on n'en sort que par le zaap.
    reference = json.loads(REAL_REFERENCE.read_text(encoding="utf-8"))
    atlas = Atlas.load()
    zaaps = {tuple(map(int, p["carte"].split(","))): p["nom"] for p in atlas.places
             if p.get("type") == "zaap" and p["region"] == DOUZE}
    planner = Planner(reference, lambda c: atlas.get(DOUZE, c) is not None, zaaps=zaaps)
    for start in [(1, -32), (1, -29), (0, -29), (-2, -30)]:
        assert planner.route(start, None, {(-3, -25)}) is None
    legs = planner.route((1, -32), None, {(-3, -25)}, zaap=True)
    assert legs[0].zaap == "Cité d'Astrub" and legs[0].cell == (13, 17) and legs[0].dest == (4, -19)
    assert legs[-1].dest == (-3, -25) and all(leg.zaap is None for leg in legs[1:])
    # 25/10 (journaux 175311, 180222) : suiveurs en 0,-33, touche A, soleils vus de l'autre côté du muret : (26,28),
    # (26,16), (27,15)... jamais (27,23), celui de la référence. L'itinéraire ne prend que des soleils de la référence.
    legs = planner.route((0, -33), None, {(1, -32)})
    assert len(legs) == 2 and all(list(leg.cell) in reference[f"{leg.coords[0]},{leg.coords[1]}"]["sorties"][leg.side]
                                  ["cases"] for leg in legs)


# Lecture du nom de la carte : seulement un contrôle

def test_reading_that_fits_the_expected_map():
    # 25/10 (journal 201109) : « 0,-2 » lu en 0,-29 (7 fois), « -2,-32? » en -2,-32 (3 fois) : compatibles.
    assert reading_fits("0,-2", (0, -29)) and reading_fits("0,-3", (0, -30)) and reading_fits("-2,-32?", (-2, -32))
    assert reading_fits("?,-18", (5, -18)) and reading_fits("5,-1?", (5, -18)) and reading_fits("illisible", (1, 1))
    assert reading_fits("0,-29", (0, -29)) and reading_fits(None, (0, -29))
    # Contraires : un chiffre lu différent, un tiret en trop (11/10 : « -1,-23 » pour 1,-23), deux cartes à côté.
    assert not reading_fits("-1,-23", (1, -23)) and not reading_fits("0,-3", (0, -29)) and not reading_fits("4,3", (3, 4))
    assert not reading_fits("0,-28", (0, -29)) and not reading_fits("-,-29", (0, -29))
    assert complete("0,-29") and not complete("0,-2?") and not complete("illisible")


def label(text: str) -> MapLabel:
    """Nom de carte lu « text » (coordonnées complètes si aucun « ? »)."""
    coords = tuple(int(v) for v in text.split(",")) if complete(text) else None
    return MapLabel(coords=coords, text=text, zone=None, zone_name=None, region=None, region_name=None,
                    signature=np.ones((2, 2), np.float32))


class Asker:
    """« /p %pos% » simulé : réponses données dans l'ordre (None : pas de réponse), demandes notées."""

    def __init__(self, *answers):
        self.answers, self.forced = list(answers), []

    def ask(self, force=False):
        self.forced.append(force)
        return self.answers.pop(0) if self.answers else None


def traveler_at(coords, *answers, frame=None, **settings) -> Traveler:
    frame = frame or load_frame("hors_combat")
    t = Traveler(FakeGame(frame), TravelSettings(mode="aller", destination=(0, 0), settle=0, **settings), World(None))
    t.region, t.coords, t._last_label = DOUZE, coords, unreadable_label()
    t.asker = Asker(*answers)
    return t


def test_truncated_reading_after_a_crossing_keeps_the_dead_reckoning_without_asking():
    t = traveler_at((1, -29))
    t._locate(load_frame("hors_combat"), label("0,-2"), (0, -29))
    assert t.coords == (0, -29) and not t.jumped and not t.doubt and t.asker.forced == []
    t._locate(load_frame("hors_combat"), label("-2,-32?"), (-2, -32))
    assert t.coords == (-2, -32) and t.asker.forced == [] and t._map_sure(label("-2,-32?"))


def test_contradicting_reading_is_checked_once_by_the_game(caplog):
    caplog.set_level("INFO")
    t = traveler_at((1, -24), (1, -23))
    t._locate(load_frame("hors_combat"), label("-1,-23"), (1, -23))
    assert t.coords == (1, -23) and t.asker.forced == [True] and not t.doubt
    assert "le jeu confirme 1,-23 (lecture fausse)" in caplog.text
    t._locate(load_frame("hors_combat"), label("-1,-23"), (1, -23))  # même lecture, même carte : pas redemandé
    assert t.asker.forced == [True]
    t = traveler_at((0, -30), (5, -12))  # ailleurs pour de bon (mort, téléportation) : le jeu l'emporte
    t._locate(load_frame("hors_combat"), label("5,-12"), (0, -29))
    assert t.coords == (5, -12) and t.jumped


def test_contradicting_reading_without_answer_keeps_the_dead_reckoning_then_asks_again():
    t = traveler_at((0, -30))
    t._locate(load_frame("hors_combat"), label("0,-28"), (0, -29))
    assert t.coords == (0, -29) and t.doubt and not t._map_sure()
    t.asker.answers = [(0, -29)]
    t.look()  # au regard suivant (nom illisible), le jeu est de nouveau interrogé
    assert t.coords == (0, -29) and not t.doubt and t.asker.forced == [True, True]


def test_start_from_a_complete_and_coherent_reading_without_asking(monkeypatch):
    # Au départ : lecture complète et cohérente, ou « /p » ; jamais la position supposée (25/10, journal 201109 :
    # --combat-zone -3,-25 gardé alors que le principal était à Taïnéla).
    monkeypatch.setattr("bot.brain.travel.read_map_label", lambda frame: label("1,-32"))
    t = traveler_at((-3, -25))
    t._last_label = None
    t._passages, t._passages_region = Passages({}, {"1,-32": {"sorties": {}}, "-3,-25": {"sorties": {}}}), DOUZE
    t.look()
    assert t.coords == (1, -32) and t.asker.forced == []


def test_start_with_unreadable_name_asks_the_game_rather_than_trusting_the_given_position(monkeypatch):
    monkeypatch.setattr("bot.brain.travel.read_map_label", lambda frame: None)
    t = traveler_at((-3, -25), (1, -32))
    t._last_label = None
    t.look()
    assert t.coords == (1, -32) and t.asker.forced == [True]
    # Lecture incomplète qui contredit la position donnée, sans réponse : position inconnue (pas de trajet à l'aveugle).
    monkeypatch.setattr("bot.brain.travel.read_map_label", lambda frame: label("1,-3?"))
    t = traveler_at((-3, -25))
    t._last_label = None
    t.look()
    assert t.coords is None


def test_start_reading_with_a_lost_last_digit_keeps_the_given_position(monkeypatch):
    monkeypatch.setattr("bot.brain.travel.read_map_label", lambda frame: label("0,-2"))
    t = traveler_at((0, -29))
    t._last_label = None
    t._passages, t._passages_region = Passages({}, {"0,-2": {"sorties": {}}, "0,-29": {"sorties": {}}}), DOUZE
    t.look()
    assert t.coords == (0, -29) and t.asker.forced == []


# Trajet à l'estime

class SimLabel:
    region_name = DOUZE
    readable = True
    zone = zone_name = None

    def __init__(self, coords):
        self.coords, self.text = coords, f"{coords[0]},{coords[1]}"

    def describe(self):
        return self.text


class RefSim:
    """Personnage simulé d'après une référence : le soleil cliqué mène où elle le dit, sur la case d'arrivée qu'elle
    donne ; `walls` : (carte, soleil) qui ne passent pas en jeu ; `known` : zaaps connus du personnage."""

    region = DOUZE
    jumped = doubt = interrupted = False
    teleported = None
    on_crossed = None
    forbidden = Traveler.forbidden
    here = Traveler.here
    learns = True

    def __init__(self, reference, start, walls=(), known=None, answers=()):
        self.reference, self.pos, self.walls, self.known = reference, start, set(walls), known
        self.world = World(None)
        self.atlas = Atlas({f"{DOUZE} {k}": MapInfo(DOUZE, tuple(map(int, k.split(","))), "Zone") for k in reference})
        self.settings = TravelSettings(mode="aller", destination=(0, 0))
        self.arrival_cell = self.entry = self.next_side = self.plan_cells = None
        self.clicked, self.teleports, self.exits_called = [], [], 0
        self.asker = Asker(*answers)

    @property
    def coords(self):
        return self.pos

    @coords.setter
    def coords(self, value):
        self.pos = value

    def look(self):
        return None, SimLabel(self.pos)

    def key(self, label=None):
        return World.key(self.region, self.pos)

    def exits(self, *args, **kwargs):
        self.exits_called += 1  # recherche à l'écran (touche A) : jamais sur une carte décrite
        return {}

    def go(self, frame, label, side, exits=None):
        assert self.plan_cells, "itinéraire de la référence : soleils prévus"
        for cell in self.plan_cells:
            self.clicked.append((self.pos, cell))
            if (self.pos, cell) in self.walls:
                continue
            exit_ = self.reference[f"{self.pos[0]},{self.pos[1]}"]["sorties"][side]
            self.pos = tuple(map(int, exit_["vers"].split(",")))
            self.arrival_cell = tuple(exit_["arrivees"][f"{cell[0]},{cell[1]}"])
            self.entry = OPPOSITE[side]
            return SimLabel(self.pos)
        return None

    def teleport(self, leg):
        self.teleports.append(leg.zaap)
        if self.known is not None and leg.dest not in self.known:
            return None
        # Au pied du zaap d'arrivée, comme Traveler.teleport.
        self.pos, self.arrival_cell, self.teleported = leg.dest, Planner(self.reference).zaap_cell(leg.dest),             (leg.coords, leg.dest)
        return SimLabel(self.pos)


def journey(sim: RefSim, goal) -> Journey:
    trip = Journey(sim, goal)
    trip.passages = Passages({}, sim.reference)
    zaaps = {(1, 0): "Le Berceau", (5, 0): "Cité d'Astrub", (9, 0): "Loin"}
    sim.atlas.places = [{"region": DOUZE, "type": "zaap", "nom": n, "carte": f"{c[0]},{c[1]}"} for c, n in zaaps.items()]
    return trip


def run(trip: Journey, limit: int = 30) -> None:
    for _ in range(limit):
        trip.step()
        if trip.done:
            return
    raise AssertionError("trajet jamais terminé")


def test_journey_plans_the_whole_route_then_clicks_the_planned_suns_only(caplog):
    caplog.set_level("INFO")
    sim = RefSim(muret_reference(), (2, 0))
    trip = journey(sim, (-1, 0))
    run(trip)
    assert sim.pos == (-1, 0) and trip.crossed == 3 and trip.reroutes == 0
    assert sim.clicked == [((2, 0), (1, 15)), ((1, 0), (1, 9)), ((0, 0), (1, 9))] and sim.exits_called == 0
    assert ("Itinéraire de 2,0 à -1,0 : 3 carte(s) : 2,0 gauche soleil (1, 15) -> 1,0 arrivée (26, 14) ; "
            "1,0 gauche soleil (1, 9) -> 0,0 arrivée (26, 8) ; 0,0 gauche soleil (1, 9) -> -1,0 arrivée (26, 10)") \
        in caplog.text
    assert sim.asker.forced == []  # aucun doute : jamais de « /p »
    # Lignes lues par tools/bilan_sessions.py (itinéraire, étape) : même début qu'avant.
    assert "Trajet : côté gauche, vers 1,0 : soleil (1, 15), arrivée (26, 14) (encore 3 carte(s) jusqu'à -1,0)"         in caplog.text


def test_missed_passage_tries_the_other_sun_then_asks_the_position_and_avoids_it(caplog):
    caplog.set_level("INFO")
    reference = muret_reference()
    sim = RefSim(reference, (1, 0), walls={((1, 0), (1, 9))})
    sim.arrival_cell = (26, 14)
    trip = journey(sim, (-1, 0))
    trip.step()  # (1,9) ne passe pas : le secours (1,29) passe, mais arrive sous le muret
    assert sim.clicked == [((1, 0), (1, 9)), ((1, 0), (1, 29))] and sim.pos == (0, 0) and sim.arrival_cell == (26, 30)
    assert trip.legs is None  # autre case d'arrivée que prévue : itinéraire recalculé depuis elle
    trip.step()  # depuis (26,30), plus rien ne se joint : aucun chemin, arrêt clair (pas d'errance)
    assert trip.done and "aucun chemin de 0,0 à -1,0 d'après la référence" in caplog.text
    # Tous les soleils du côté ratés : « /p » d'abord ; le jeu confirme la carte, le côté est évité pour ce trajet.
    sim = RefSim(reference, (1, 0), walls={((1, 0), (1, 9)), ((1, 0), (1, 29))}, answers=[(1, 0)])
    sim.arrival_cell = (26, 14)
    trip = journey(sim, (-1, 0))
    trip.step()
    assert sim.asker.forced == [True] and trip.avoid == {((1, 0), "gauche")} and trip.failures == 1
    assert "Passage du gauche de 1,0 raté" in caplog.text


def test_missed_passage_elsewhere_than_believed_reroutes_from_the_game_position(caplog):
    sim = RefSim(muret_reference(), (1, 0), walls={((1, 0), (1, 9)), ((1, 0), (1, 29))}, answers=[(0, 0)])
    sim.arrival_cell = (26, 14)
    trip = journey(sim, (-1, 0))
    trip.step()
    assert sim.pos == (0, 0) and trip.avoid == set() and trip.legs is None
    sim.walls.clear()
    run(trip)
    assert sim.pos == (-1, 0)


def test_journey_leaves_a_closed_area_by_the_zaap_and_skips_an_unknown_one(caplog):
    caplog.set_level("INFO")
    sim = RefSim(berceau_reference(), (2, 0))
    trip = journey(sim, (7, 0))
    run(trip)
    assert sim.pos == (7, 0) and sim.teleports == ["Cité d'Astrub"] and sim.teleported == ((1, 0), (5, 0))
    assert trip.reroutes == 0  # case d'arrivée du zaap prévue : pas de nouvel itinéraire après la téléportation
    assert "par zaap (aucun chemin à pied)" in caplog.text
    # Zaap d'arrivée pas connu du personnage (ligne absente de la liste) : pas repris, aucun autre : arrêt.
    sim = RefSim(berceau_reference(), (1, 0), known=set())
    trip = journey(sim, (7, 0))
    run(trip)
    assert sim.teleports == ["Cité d'Astrub"] and trip.refused == {(5, 0)} and sim.pos == (1, 0)
    # Zaap désactivé (travel.zaap: false) : arrêt avec un message, sans clic.
    sim = RefSim(berceau_reference(), (1, 0))
    sim.settings = TravelSettings(mode="aller", destination=(7, 0), zaap=False)
    trip = journey(sim, (7, 0))
    run(trip)
    assert sim.teleports == [] and "zaap désactivé" in caplog.text


def test_real_traveler_clicks_the_planned_cells_without_the_a_key():
    # 25/10 (journal 201109) : 6 recherches à la touche A sur des cartes décrites par la référence. Avec l'itinéraire,
    # seulement les cases prévues, dans l'ordre ; tout raté : anomalie notée, rien d'appris « bloqué ».
    foret = load_frame("hors_combat_foret")
    t = Traveler(FakeGame(foret), TravelSettings(mode="aller", destination=(0, 0), settle=0, walk_timeout=30,
                                                 still_after=0.05, still_for=0.05), World(None))
    frame, label_ = t.look()
    t.plan_cells = [(27, 9), (27, 13)]
    assert t.go(frame, label_, "droite") is None and not t.interrupted
    cells = [frame.grid.cell_at(*c) for c in clicks(t.game)]
    assert set(cells) == {(27, 9), (27, 13)} and cells[0] == (27, 9)
    assert "a" not in presses(t.game)
    assert t.world.destination("Incarnam 2,4", "droite") is None


def test_new_decor_wins_over_a_name_still_read_as_the_departure(monkeypatch, caplog):
    # Le décor fait foi pour dire que la carte a changé : nom lu comme au départ sur le nouveau décor (lecture fausse),
    # avant attente jusqu'à walk_timeout puis passage cru raté alors qu'il avait eu lieu.
    caplog.set_level("INFO")
    foret, champs = load_frame("hors_combat_foret"), load_frame("hors_combat")
    monkeypatch.setattr("bot.brain.travel.read_map_label", lambda frame: label("2,4"))
    t = Traveler(FakeGame(foret, champs), TravelSettings(mode="aller", destination=(0, 0), settle=0, walk_timeout=30,
                                                         still_after=0.05, still_for=0.05), World(None))
    frame, label_ = t.look()
    t.asker = Asker()
    t.plan_cells = [(27, 9)]
    assert t.go(frame, label_, "droite") is not None
    assert "je me fie au décor, nouvelle carte" in caplog.text and len(clicks(t.game)) == 1
    assert t.coords == (3, 4) and t.doubt  # « 2,4 » contraire à l'estime, « /p » sans réponse : estime gardée


# Équipe : suiveurs par le même zaap

def test_followers_sent_through_the_zaap_after_the_leader(tmp_path, monkeypatch):
    trips = []

    class FakeJourney:
        def __init__(self, traveler, goal):
            self.traveler, self.goal, self.done = traveler, goal, False
            trips.append((traveler.game.name, traveler.settings.start, goal, traveler.settings.zaap))

        def step(self):
            self.done, self.traveler.coords = True, self.goal

    monkeypatch.setattr("bot.team.loop.Journey", FakeJourney)
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json")}
    screen = Screen()
    a, b = member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("hors_combat"))
    bot = TeamBot([a, b], settings, TeamSettings(enabled=True), travel="test")
    traveler = bot.roamer.traveler
    traveler.coords, traveler._last_label = (4, -19), unreadable_label()
    bot._together_checked = True
    traveler.teleported = ((1, -32), (4, -19))  # le principal vient de prendre le zaap du Berceau
    bot._regroup()  # B part le rejoindre, depuis le zaap de départ, zaap permis
    assert trips == [("B", (1, -32), (4, -19), True)] and traveler.teleported is None


def test_zone_roamer_does_not_fight_alone_right_after_a_team_zaap():
    sim = RefSim(berceau_reference(), (5, 0))
    roamer = ZoneRoamer(sim)
    roamer.away = False
    sim.teleported = ((1, 0), (5, 0))
    assert roamer.away is False  # seul (pas d'équipe) : rien à attendre
    sim.on_crossed = lambda *args: None  # équipe (team/loop.py)
    assert roamer.away is True
    sim.teleported = None  # suiveurs envoyés (team/loop.py, _regroup)
    assert roamer.away is False


# Zaap : menus et fenêtre (captures du 25/10, Elseiso, Astrub 4,-19 vers Le Berceau)

needs_ocr = pytest.mark.skipif(not ocr_available(), reason="OCR de Windows absent (requirements-winrt.txt)")


@needs_ocr
def test_zaap_menu_window_row_and_button_are_seen():
    from bot.brain.zaap import find_button, find_row, find_use_entry, find_zaap_window

    menu = load_frame("zaap_menu_utiliser")
    assert find_use_entry(menu, (1215, 516)) is not None and find_zaap_window(menu) is None
    entry = find_use_entry(menu, (1215, 516))
    assert 1220 <= entry[0] <= 1305 and 556 <= entry[1] <= 590  # « Utiliser », sous l'en-tête « Zaap »
    whole, searched, chosen = load_frame("zaap_liste"), load_frame("zaap_liste_recherche"), load_frame("zaap_ligne_choisie")
    window = find_zaap_window(whole)
    assert window is not None and find_use_entry(whole, (1215, 516)) is None
    assert find_row(whole, window, "strub") is not None and find_row(whole, window, "erceau") is None
    assert find_row(searched, window, "erceau") is not None
    assert find_button(searched, window, "teleporter") is None  # sombre tant qu'aucune ligne n'est choisie
    assert find_button(chosen, window, "teleporter") is not None and find_button(chosen, window, "fermer") is not None


@needs_ocr
def test_use_zaap_sees_each_step_before_clicking():
    from bot.brain.zaap import use_zaap

    menu = load_frame("zaap_menu_utiliser")
    game = FakeGame(menu, menu, load_frame("zaap_liste"), load_frame("zaap_liste_recherche"),
                    load_frame("zaap_ligne_choisie"), load_frame("hors_combat_foret"))
    assert use_zaap(game, (14, 20), "Le Berceau", (1, -32))
    first, entry, field, row, button = clicks(game)
    assert menu.grid.cell_at(*first) == (14, 20)
    assert 1220 <= entry[0] <= 1305 and 556 <= entry[1] <= 590
    assert 1311 <= field[0] <= 1643 and 618 <= field[1] <= 646
    assert 220 <= row[1] <= 270  # la ligne « Le berceau »
    assert 1260 <= button[0] <= 1441 and 667 <= button[1] <= 695  # « Se téléporter »
    assert "".join(presses(game)) == "erceau"  # touche par touche (pas de saisie de texte ici), sans la 1re lettre


@needs_ocr
def test_use_zaap_closes_the_window_when_the_destination_is_not_listed():
    from bot.brain.zaap import use_zaap

    menu, listed = load_frame("zaap_menu_utiliser"), load_frame("zaap_liste")
    game = FakeGame(menu, menu, listed)
    assert not use_zaap(game, (14, 20), "Bonta", (-32, -58))
    fermer = clicks(game)[-1]
    assert 1458 <= fermer[0] <= 1640 and 667 <= fermer[1] <= 695  # « Fermer », jamais « Se téléporter »


def test_zaap_search_word():
    assert zaap_search_word("Le Berceau") == "berceau" and zaap_search_word("Cité d'Astrub") == "astrub"
    assert zaap_search_word("Village d'Akwadala") == "akwadala" and zaap_search_word("Brâkmar") == "brakmar"


def test_reference_read_once_while_unchanged(tmp_path):
    # Chaque trajet de suiveur relisait les 13 Mo de la référence.
    path = tmp_path / "ref.json"
    path.write_text(json.dumps({"1,1": {"sorties": {}}}), encoding="utf-8")
    assert _reference(path) is _reference(path)
    assert _reference(tmp_path / "absente.json") == {}


def test_leg_description_for_a_zaap():
    leg = Leg((1, -32), None, (13, 17), (4, -19), None, zaap="Cité d'Astrub")
    assert leg.describe() == "1,-32 zaap (13, 17) -> Cité d'Astrub 4,-19"


def test_scattered_team_meets_at_the_zone_map_instead_of_joining_the_leader(tmp_path, monkeypatch):
    # 26/10 (journal 172442) : principal ressuscité au Berceau, les 7 autres à Astrub ; ils prenaient le zaap vers lui,
    # puis tous auraient repris le zaap du retour (15 zaaps). Principal hors de la zone : chacun va à la carte de la zone
    # donnée au lancement, le principal ne les attend pas ; arrivé, un suiveur y attend sans arrêter le bot.
    from bot.config import load_settings
    from bot.team.loop import TeamBot
    from bot.team.roster import TeamSettings
    from test_team import Screen, member

    trips = []

    class FakeJourney:
        def __init__(self, traveler, goal):
            self.traveler, self.goal, self.done = traveler, goal, False
            trips.append(goal)

        def step(self):
            self.done = True
            self.traveler.coords = self.goal

    monkeypatch.setattr("bot.team.loop.Journey", FakeJourney)
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json")}
    screen = Screen()
    a, b = member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("hors_combat"))
    bot = TeamBot([a, b], settings, TeamSettings(enabled=True), travel="test")
    roamer = bot.roamer
    roamer.traveler.coords, roamer.traveler._last_label = (1, -32), unreadable_label()
    monkeypatch.setattr(type(roamer), "away", property(lambda self: True), raising=False)  # principal hors de la zone
    roamer.meeting = (-3, -25)
    bot._lost, bot._seen_at, bot._together_checked = {"B"}, {"B": (4, -19)}, True
    assert bot._regroup()  # le principal n'attend pas
    assert trips == [(-3, -25)] and "B" in bot._at_meeting and not bot.finished
    assert bot._regroup() and trips == [(-3, -25)]  # il attend au rendez-vous : pas de nouveau trajet


def test_meeting_decided_after_looking_where_the_leader_is(tmp_path, monkeypatch):
    # 26/10 (journal 173634) : au départ, « hors de la zone » pas encore regardé (None) pris pour « dans la zone » : 5
    # suiveurs ont pris le zaap vers le principal, au Berceau. Le principal est situé d'abord ; hors de la zone, ils vont
    # au rendez-vous.
    from bot.config import load_settings
    from bot.team import loop
    from bot.team.loop import TeamBot
    from bot.team.roster import TeamSettings
    from test_team import Screen, member

    trips = []

    class FakeJourney:
        def __init__(self, traveler, goal):
            self.traveler, self.goal, self.done = traveler, goal, False
            trips.append(goal)

        def step(self):
            pass

    monkeypatch.setattr("bot.team.loop.Journey", FakeJourney)
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json")}
    screen = Screen()
    a, b = member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("hors_combat"))
    bot = TeamBot([a, b], settings, TeamSettings(enabled=True), travel="test")
    roamer = bot.roamer
    kind = type(roamer)
    monkeypatch.setattr(loop, "ZoneRoamer", kind)
    monkeypatch.setattr(kind, "away", property(lambda self: getattr(self, "_seen_away", None)), raising=False)
    monkeypatch.setattr(kind, "locate", lambda self: setattr(self, "_seen_away", True) or True, raising=False)
    roamer.traveler.coords, roamer.traveler._last_label = (1, -32), unreadable_label()
    roamer.meeting = (-3, -25)
    bot._lost, bot._seen_at, bot._together_checked = {"B"}, {"B": (4, -19)}, True
    assert bot._regroup() and trips == [(-3, -25)]


def test_followers_left_behind_a_zaap_join_the_leader_who_waits_for_them(tmp_path, monkeypatch):
    # 26/10 (journal 173905) : après le zaap du principal, ses suiveurs partaient seuls vers le rendez-vous de la zone et
    # marchaient l'un après l'autre (~40 s par carte pour 7). Restés derrière son zaap : ils le rejoignent à l'arrivée du
    # zaap, et il les attend ; l'équipe marche ensuite groupée.
    from bot.config import load_settings
    from bot.team.loop import TeamBot
    from bot.team.roster import TeamSettings
    from test_team import Screen, member

    trips = []

    class FakeJourney:
        def __init__(self, traveler, goal):
            self.traveler, self.goal, self.done = traveler, goal, False
            trips.append(goal)

        def step(self):
            pass

    monkeypatch.setattr("bot.team.loop.Journey", FakeJourney)
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json")}
    screen = Screen()
    a, b = member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("hors_combat"))
    bot = TeamBot([a, b], settings, TeamSettings(enabled=True), travel="test")
    roamer = bot.roamer
    monkeypatch.setattr(type(roamer), "away", property(lambda self: True), raising=False)
    roamer.meeting = (-3, -25)
    roamer.traveler.coords, roamer.traveler._last_label = (4, -19), unreadable_label()
    roamer.traveler.teleported = ((1, -32), (4, -19))  # le principal vient de prendre le zaap du Berceau
    bot._together_checked = True
    assert not bot._regroup()  # le principal attend B
    assert trips == [(4, -19)] and "B" in bot._behind_zaap


def test_follower_heading_to_the_meeting_follows_the_leader_once_on_his_map(tmp_path, monkeypatch):
    # 26/10 (journal 173905) : chacun allait seul au rendez-vous, un pas après l'autre. Sur la carte du principal (même
    # décor), le suiveur le suit désormais : plus de trajet à lui.
    from bot.config import load_settings
    from bot.team.loop import TeamBot
    from bot.team.roster import TeamSettings
    from test_team import Screen, member

    trips = []
    monkeypatch.setattr("bot.team.loop.Journey", lambda traveler, goal: trips.append(goal))
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json")}
    screen = Screen()
    a, b = member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("hors_combat"))
    bot = TeamBot([a, b], settings, TeamSettings(enabled=True), travel="test")
    roamer = bot.roamer
    monkeypatch.setattr(type(roamer), "away", property(lambda self: True), raising=False)
    roamer.meeting = (-3, -25)
    roamer.traveler.coords, roamer.traveler._last_label = (4, -19), unreadable_label()
    bot._lost, bot._seen_at, bot._together_checked = {"B"}, {"B": (4, -19)}, True
    assert bot._regroup() and not bot._lost and trips == []


def test_position_asked_in_the_group_chat_is_read_for_the_right_character(tmp_path):
    # 26/10 (journal 173905) : « /p » répond dans le canal du groupe ; le nom venait du titre de la fenêtre, qui ne le
    # porte plus : la position d'un autre membre lue (-4,-20 pour -1,-19), principal cru téléporté, trajet arrêté.
    from bot.config import load_settings
    from bot.perception.chat import parse_position
    from bot.team.loop import TeamBot
    from bot.team.roster import TeamSettings
    from test_team import Screen, member

    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json")}
    screen = Screen()
    a, b = member("Fourboun", screen, load_frame("hors_combat")), member("Waldarch", screen, load_frame("hors_combat"))
    bot = TeamBot([a, b], settings, TeamSettings(enabled=True), travel="test")
    assert bot.roamer.traveler.asker.name == "Fourboun"
    chat = ["[17:48] (Groupe) Fourboun : [-1,-19]", "[17:48] (Groupe) Waldarch : [-4,-20]"]
    assert parse_position(chat, bot.roamer.traveler.asker.name) == (-1, -19)
