"""Repérage (nom et coordonnées de la carte), sorties (soleils) et changements de carte."""

import shutil
import time

import cv2
import numpy as np
import pytest

import bot.perception.maplabel as maplabel
from bot.__main__ import _coords
from bot.brain.atlas import Atlas
from bot.brain.loop import Bot
from bot.brain.travel import (
    BLOCKED,
    Explorer,
    START,
    SURE_UNREAD,
    RoundTrip,
    Traveler,
    TravelSettings,
    World,
    ZoneRoamer,
    _click_spot,
    _parse_map,
    _parse_zone,
)
from bot.config import load_settings
from bot.perception.exits import DELTA, OPPOSITE, Exit, find_marked_exits, find_suns
from bot.perception.frame import Frame
from bot.perception.maplabel import learn_digits, read_map_label, unreadable_label
from bot.perception.popups import find_context_menu
from conftest import FIXTURES, load_frame
from test_brain import FakeGame, clicks, presses


def test_map_name_and_coordinates():
    champs = read_map_label(load_frame("hors_combat"))
    assert champs.coords == (4, 3) and champs.zone_name == "Champs" and champs.region_name == "Incarnam"
    foret = read_map_label(load_frame("hors_combat_foret"))
    assert foret.coords == (2, 4) and foret.zone_name == "Forêt" and foret.region_name == "Incarnam"
    assert foret.same_region(champs) and not foret.same_zone(champs) and not foret.same_map(champs)


@pytest.fixture
def without_digit_3(monkeypatch, tmp_path):
    """Modèles de la carte sans le chiffre 3, et chiffres appris dans un dossier temporaire."""
    assets = tmp_path / "assets"
    shutil.copytree(maplabel.ASSETS_DIR / "carte", assets / "carte")
    for path in (assets / "carte" / "chiffres").glob("chiffre_3*.png"):
        path.unlink()
    monkeypatch.setattr(maplabel, "ASSETS_DIR", assets)
    monkeypatch.setattr(maplabel, "LEARNED_DIR", tmp_path / "appris")
    maplabel._masks.cache_clear()
    yield tmp_path / "appris"
    maplabel._masks.cache_clear()


def test_escape_that_opens_the_main_menu_closes_it_without_any_click():
    # 11/10 : Échap sans menu à fermer a ouvert le Menu principal ; un clic « ailleurs » est tombé sur « Déconnecter ».
    from bot.brain.travel import escape_menu

    image = np.zeros((1009, 1920, 3), np.uint8)  # extrait (lignes 180-620, colonnes 980-1520) d'une capture
    image[180:620, 980:1520] = cv2.imread(str(FIXTURES / "menu_principal.png"))
    game = FakeGame(Frame.of(image), load_frame("hors_combat"))
    escape_menu(game)
    assert presses(game) == ["escape", "escape"] and clicks(game) == []
    game = FakeGame(load_frame("hors_combat"))  # menu bien fermé par le premier Échap
    escape_menu(game)
    assert presses(game) == ["escape"] and clicks(game) == []


def test_main_menu_opened_a_little_late_by_escape_is_still_closed():
    # 20/10, Vertdepomme : le Menu principal, ouvert par Échap, pas encore affiché au seul regard de 0,3 s ; six reclics
    # sont tombés dedans (~25 s). Cherché pendant ~1 s : 3 regards espacés de 0,3 s.
    from bot.brain.travel import escape_menu

    image = np.zeros((1009, 1920, 3), np.uint8)
    image[180:620, 980:1520] = cv2.imread(str(FIXTURES / "menu_principal.png"))
    champs = load_frame("hors_combat")
    game = FakeGame(champs, champs, Frame.of(image), champs)
    escape_menu(game)
    assert presses(game) == ["escape", "escape"] and clicks(game) == [] and len(game.parks) == 3
    game = FakeGame(champs)
    escape_menu(game)
    assert presses(game) == ["escape"] and len(game.parks) == 3 and sum(game.slept) == pytest.approx(0.9)


def test_team_told_of_every_attempt_of_the_leader_with_the_spot_aimed_at():
    # 20/10, soleil (27,5) de 5,5 : le principal a recliqué le bas de la case et il est passé ; on_click n'était appelé
    # qu'au premier essai, ses suiveurs immobiles n'étaient recliqués qu'après son arrivée (~7 s perdues).
    t = Traveler(FakeGame(load_frame("hors_combat_foret")), fast(), World(None))
    calls = []
    t.on_click = lambda side, cell, frame, spot: calls.append((side, cell, spot))
    frame, label = t.look()
    assert t.go(frame, label, "droite") is None
    assert calls == [("droite", (27, 9), 0), ("droite", (27, 9), 1)]



def test_start_read_elsewhere_than_the_given_position_is_not_a_teleport():
    # 21/10 : --combat-zone=11,-28, l'équipe restée en 10,-25 : crue téléportée, plus aucun changement de carte.
    foret = load_frame("hors_combat_foret")
    t = Traveler(FakeGame(foret, foret), TravelSettings(mode="test", settle=0, start=(11, -28)), World(None))
    t.look()
    assert t.coords == (2, 4) and not t.jumped  # au départ, la lecture l'emporte sur la position donnée
    t.coords = (9, 9)
    t.look()
    assert t.jumped  # en route, toujours vu comme un déplacement ailleurs

def test_crossing_reported_with_the_coordinates_completed_by_the_path(without_digit_3):
    # 20/10, 11,-23 : le principal lisait « 11,-2? » et passait ce nom tel quel à son équipe ; ses suiveurs lisaient
    # « 11,-23 » : noms différents, 6 suiveurs crus perdus. Coordonnées complétées par le chemin (11,-23).
    foret, champs = load_frame("hors_combat_foret"), load_frame("hors_combat")
    t = Traveler(FakeGame(foret, foret, foret, champs, champs), TravelSettings(mode="test", settle=0), World(None))
    crossings = []
    t.on_crossed = lambda side, cell, before, after: crossings.append((before, after))
    frame, label = t.look()
    t.coords = (3, 3)  # à droite : 4,3 d'après le chemin
    assert t.go(frame, label, "droite").text == "4,?"
    ((before, after),) = crossings
    assert after.text == "4,?" and after.coords == (4, 3) and after.readable
    assert before.coords == (2, 4)  # lu en entier : tel quel


def test_exit_noted_blocked_only_on_a_map_whose_coordinates_were_read(tmp_path):
    # 11/10 : des suiveurs, pas sur la carte qu'ils croyaient, ont noté bloqués les 4 côtés de 4,-20.
    world = World(tmp_path / "carte.json")
    traveler = Traveler(FakeGame(load_frame("hors_combat")), TravelSettings(mode="test", settle=0), world)
    frame, label = traveler.look()
    assert label.readable and label.coords == traveler.coords
    key = traveler.key(label)
    traveler._note_blocked(key, "gauche", label, everywhere=True)  # aucun soleil de ce côté
    assert world.destination(key, "gauche") == BLOCKED  # carte lue : notée pour tous
    traveler._note_blocked(key, "droite", label)  # soleil inatteignable, côté d'arrivée inconnu (début du trajet)
    assert world.destination(key, "droite") is None and (key, "droite") in traveler.blocked_here
    traveler.entry = "bas"  # arrivé par le bas : de ce côté du rempart, le soleil de droite ne s'atteint pas
    traveler._note_blocked(key, "droite", label)
    assert world.destination(key, "droite") is None and world.blocked_from(key, "bas") == {"droite"}
    assert world.blocked_from(key, "haut") == set()
    traveler.learns = False  # trajet d'un suiveur qui rejoint le principal
    traveler._note_blocked(key, "haut", label, everywhere=True)
    assert world.destination(key, "haut") is None
    traveler.learns = True
    # Nom illisible : sûr par le chemin à SURE_UNREAD cartes au plus d'une carte lue (12/10 : sur 4,-22, la sortie du
    # haut était redécouverte à chaque trajet) ; au-delà, ou après un saut, pour ce trajet seulement.
    traveler._unread_steps = SURE_UNREAD
    traveler._note_blocked(key, "bas", maplabel.unreadable_label(), everywhere=True)
    assert world.destination(key, "bas") == BLOCKED
    traveler._unread_steps = SURE_UNREAD + 1
    traveler._note_blocked(key, "haut", maplabel.unreadable_label(), everywhere=True)
    assert world.destination(key, "haut") is None and (key, "haut") in traveler.blocked_here


def test_link_between_maps_that_do_not_touch_is_not_saved(tmp_path):
    # 11/10 : positions mal lues -> « -1,-23 droite -> 2,-23 » enregistré ; une voisine est toujours à un pas.
    world = World(tmp_path / "carte.json")
    world.link("Monde des Douze -1,-23", "droite", "Monde des Douze 2,-23")
    assert world.destination("Monde des Douze -1,-23", "droite") is None
    world.link("Monde des Douze -1,-23", "droite", "Monde des Douze 0,-23")
    assert world.destination("Monde des Douze -1,-23", "droite") == "Monde des Douze 0,-23"
    world.link("Monde des Douze -1,-23", "haut", BLOCKED)
    assert world.destination("Monde des Douze -1,-23", "haut") == BLOCKED


def test_sun_clicked_with_other_players_hidden():
    # Découverte de l'utilisateur (11/10) : E tenu cache les autres joueurs ; dans la cité, ils prenaient le clic.
    game = FakeGame(load_frame("hors_combat"))
    traveler = Traveler(game, TravelSettings(mode="test", settle=0), World(None))
    traveler.click_sun((100, 100), (0, 0))
    assert [a[0] for a in game.actions] == ["hold", "move", "click"] and game.actions[0][1] == "e"
    game = FakeGame(load_frame("hors_combat"))
    Traveler(game, TravelSettings(mode="test", settle=0, hide_players_key=""), World(None)).click_sun((100, 100), (0, 0))
    assert [a[0] for a in game.actions] == ["move", "click"]


def test_partial_reading_beats_the_expected_map():
    from bot.brain.travel import merge_reading

    # 11/10, cité d'Astrub : « ?,-18 » lu alors que le chemin disait 5,-17 : on est toujours en 5,-18.
    assert merge_reading("?,-18", (5, -17)) == ((5, -18), False)
    assert merge_reading("?,-1?", (5, -17)) == ((5, -17), True)  # rien qui contredise : le chemin
    assert merge_reading("?,-2?", (5, -17)) == ((5, -17), False)  # contredit, sans mieux à proposer
    assert merge_reading("5,-?", (5, -17)) == ((5, -17), False)  # un chiffre de moins
    assert merge_reading("1,-2?", (1, -27)) == ((1, -27), True)


def test_thin_minus_sign_in_front_of_a_tree():
    # 11/10 : « Coordonnées : 2, -25 », le tiret sur une seule ligne blanche devant un arbre.
    image = np.zeros((1009, 1920, 3), np.uint8)  # extrait (lignes 0-120, colonnes 560-1100) d'une capture
    image[0:120, 560:1100] = cv2.imread(str(FIXTURES / "nom_de_carte_moins_fin.png"))
    label = read_map_label(Frame.of(image))
    assert label is not None and label.coords == (2, -25)


def test_light_speck_under_the_line_is_not_a_minus_sign():
    # 11/10 : « Coordonnées : 1, -23 » devant des fleurs, un éclat sous la ligne lu comme un tiret : « -1,-23 ».
    image = np.zeros((1009, 1920, 3), np.uint8)  # extrait (lignes 0-120, colonnes 560-1100) d'une capture
    image[0:120, 560:1100] = cv2.imread(str(FIXTURES / "nom_de_carte_un_moins_23.png"))
    label = read_map_label(Frame.of(image))
    assert label is not None and label.coords == (1, -23)


@pytest.mark.parametrize("name, coords", [("nom_de_carte_moins_19_rempart", (-1, -19)),
                                          ("nom_de_carte_moins_2_moins_19", (-2, -19))])
def test_nine_is_not_read_as_eight(name, coords):
    # 11/10 : pas de modèle du 9, lu « 8 » (ressemblance 0,83) : toute la rangée -19 de la cité d'Astrub prise pour la
    # rangée -18, un faux « saut du jeu » -2,-18 <-> -2,-20 retenu et le rempart de -1,-19 noté sur -1,-18.
    image = np.zeros((1009, 1920, 3), np.uint8)
    image[0:120, 560:1100] = cv2.imread(str(FIXTURES / f"{name}.png"))
    label = read_map_label(Frame.of(image))
    assert label is not None and label.coords == coords


def test_unknown_digit_gives_no_coordinates(without_digit_3):
    label = read_map_label(load_frame("hors_combat"))  # « 4, 3 » sans modèle du 3
    assert label.coords is None and label.text == "4,?"
    assert not label.same_map(read_map_label(load_frame("hors_combat_foret")))  # la ligne des coordonnées diffère


def test_digit_learned_from_the_known_position(without_digit_3):
    label = read_map_label(load_frame("hors_combat"))
    assert learn_digits(label, (5, 3)) == []  # le 4 lu ne colle pas : rien n'est appris
    assert learn_digits(label, (4, 3)) == ["3"]
    assert [p.name for p in without_digit_3.glob("*.png")] == ["chiffre_3_appris_4_3_2.png"]
    assert read_map_label(load_frame("hors_combat")).coords == (4, 3)  # lu désormais


def test_start_position_given_on_the_command_line(without_digit_3):
    frame = load_frame("hors_combat")
    t = Traveler(FakeGame(frame), TravelSettings(mode="explore", start=(4, 3)), World(None))
    _, label = t.look()
    assert label.coords is None and t.coords == (4, 3)  # position donnée, et le 3 est appris au passage
    assert read_map_label(frame).coords == (4, 3)
    assert _coords("1,6") == (1, 6) and _coords("-1, 3") == (-1, 3)


def test_label_unreadable_under_a_window():
    assert read_map_label(load_frame("fin_de_combat")) is None  # fenêtre de fin de combat : texte assombri


def test_suns_found_without_the_a_key():
    suns = {e.side: e.cell for e in find_suns(load_frame("hors_combat_foret"))}
    assert suns == {"haut": (17, 1), "bas": (21, 31), "gauche": (1, 21), "droite": (27, 9)}  # gauche : sous des feuilles
    suns = {e.side: e.cell for e in find_suns(load_frame("hors_combat"))}
    assert suns == {"haut": (9, 1), "bas": (7, 31), "gauche": (1, 25), "droite": (27, 27)}


def with_a_outline(name: str = "hors_combat") -> Frame:
    """Carte Champs avec le contour blanc de la touche A (capture de l'utilisateur) sur le soleil du haut."""
    image = load_frame(name).image.copy()
    frame = Frame.of(image)
    cx, cy = frame.grid.center((9, 1))
    crop = cv2.imread(str(FIXTURES / "soleil_touche_a.png"))[4:]  # sans la bande claire de l'interface
    x0, y0 = round(cx) - 51, max(0, round(cy) - 26)
    image[y0 : y0 + crop.shape[0], x0 : x0 + crop.shape[1]] = crop[: image.shape[0] - y0]
    return Frame.of(image)


def without_top_sun() -> Frame:
    image = load_frame("hors_combat").image.copy()
    image[8:42, 975:1032] = image[108:142, 975:1032]  # herbe prise plus bas
    return Frame.of(image)


def test_outline_of_the_a_key():
    assert find_marked_exits(load_frame("hors_combat")) == []  # sans A : aucun contour (pierres, grille)
    assert find_marked_exits(load_frame("hors_combat_foret")) == []
    (marked,) = find_marked_exits(with_a_outline())
    assert marked.side == "haut" and marked.cell == (9, 1) and marked.marked


def traveler(*frames: Frame) -> Traveler:
    return Traveler(FakeGame(*frames), TravelSettings(mode="test", settle=0), World(None))


def test_a_key_always_pressed_to_find_exits():
    # 11/10, l'utilisateur : dans la cité, des taches orange prises pour des soleils ; toujours la touche A d'abord.
    t = traveler(load_frame("hors_combat"))
    assert set(t.exits(load_frame("hors_combat"), "haut")) == {"haut", "bas", "gauche", "droite"}
    assert presses(t.game) == ["a"]  # soleils vus quand même : la touche A d'abord
    t = traveler(without_top_sun(), with_a_outline())  # captures : avant, puis après l'appui sur A
    exits = t.exits(without_top_sun(), "haut")
    assert presses(t.game) == ["a"] and exits["haut"].marked


def test_changing_map_through_the_right_sun():
    foret, champs = load_frame("hors_combat_foret"), load_frame("hors_combat")
    t = traveler(foret, foret, foret, champs, champs)
    frame, label = t.look()
    arrived = t.go(frame, label, "droite")
    assert arrived.coords == (4, 3)  # (le test recolle deux cartes qui ne sont pas voisines)
    (click,) = clicks(t.game)
    assert frame.grid.cell_at(*click) == (27, 9)  # soleil de droite
    # Ces deux cartes ne sont pas voisines : la liaison n'est pas enregistrée (11/10, positions mal lues).
    assert t.world.destination("Incarnam 2,4", "droite") is None
    assert t.world.destination("Incarnam 4,3", "gauche") is None  # pas voisines (voir plus haut)


def test_a_key_on_a_new_map_with_a_missing_side():
    t = traveler(without_top_sun(), with_a_outline())
    exits = t.exits(without_top_sun(), complete=True)  # 3 soleils vus : l'exploration veut savoir s'il en manque
    assert presses(t.game) == ["a"] and set(exits) == {"haut", "bas", "gauche", "droite"}
    t = traveler(without_top_sun(), with_a_outline())
    assert len(t.exits(without_top_sun())) == 4 and presses(t.game) == ["a"]  # toujours A (11/10)


def test_sun_cell_known_from_the_client_is_clicked_without_searching():
    # Langue du client (tools/cartes_index.py) : la case de certains soleils (« conditionnels », 48 cartes autour
    # d'Astrub, vus en jeu à la case annoncée) ; cliquée d'emblée, comme une case gardée en mémoire.
    from bot.brain.atlas import Atlas, MapInfo

    atlas = Atlas({"Incarnam 2,4": MapInfo("Incarnam", (2, 4), None, client_suns={"droite": ((27, 9),)})})
    foret = load_frame("hors_combat_foret")
    t = Traveler(FakeGame(foret), TravelSettings(mode="test", settle=0), World(None), atlas)
    frame, label = t.look()
    exits = t.exits(frame, "droite")
    assert exits["droite"].cell == (27, 9) and exits["droite"].remembered and presses(t.game) == []


def test_arrival_keeps_a_blocked_exit_and_a_follower_writes_nothing():
    # 11/10, 1,-13 : le regroupement d'un suiveur (2,-13 -> 1,-13) a réécrit « 1,-13 droite -> 2,-13 » sur la sortie
    # que le principal venait de noter bloquée ; le principal l'a reprise cinq fois.
    foret, pale = load_frame("hors_combat_foret"), load_frame("hors_combat_nom_illisible")
    t = traveler(foret, foret, pale, pale, pale, pale)
    t.world.link("Incarnam 2,3", "bas", BLOCKED)
    frame, label = t.look()
    t.go(frame, label, "haut")
    assert t.world.destination("Incarnam 2,4", "haut") == "Incarnam 2,3"
    assert t.world.destination("Incarnam 2,3", "bas") == BLOCKED  # vue inutilisable : pas réécrite par déduction
    t = traveler(foret, foret, pale, pale, pale, pale)
    t.learns = False  # trajet d'un suiveur qui rejoint le principal
    frame, label = t.look()
    t.go(frame, label, "haut")
    assert t.world.destination("Incarnam 2,4", "haut") is None and t.world.destination("Incarnam 2,3", "bas") is None


def test_arrival_seen_when_the_new_map_name_is_unreadable():
    foret, pale = load_frame("hors_combat_foret"), load_frame("hors_combat_nom_illisible")
    assert read_map_label(pale) is None  # 1,1 : texte blanc sur une falaise claire
    t = traveler(foret, foret, pale, pale, pale, pale)
    frame, label = t.look()
    arrived = t.go(frame, label, "haut")
    assert not arrived.readable and t.coords == (2, 3)  # tout le décor a changé : arrivé, coordonnées d'après le chemin
    assert t.world.destination("Incarnam 2,4", "haut") == "Incarnam 2,3"  # région de la carte précédente


def test_unreadable_start_map_is_in_the_configured_region():
    pale = load_frame("hors_combat_nom_illisible")
    t = Traveler(FakeGame(pale), TravelSettings(mode="explore", start=(1, 1)), World(None))
    _, label = t.look()
    assert t.key(label) == "Incarnam 1,1"


def test_exit_noted_blocked_when_the_map_does_not_change():
    foret = load_frame("hors_combat_foret")
    t = Traveler(FakeGame(foret), TravelSettings(mode="test", settle=0, walk_timeout=0.05), World(None))
    frame, label = t.look()
    assert t.go(frame, label, "droite") is None
    first, second = clicks(t.game)  # deux clics sur le soleil, le second ailleurs sur la case
    assert second != first
    cx, cy = frame.grid.center((27, 9))  # case du bord (colonne de droite) : le bas du losange d'abord (21/10)
    assert abs(first[0] - cx) <= 8 and first[1] - cy >= 9
    assert ("Incarnam 2,4", "droite") in t.blocked_here  # côté d'arrivée inconnu (début du trajet) : ce trajet seulement


def fast(**changes) -> TravelSettings:
    """Réglages de test : soleil inatteignable jugé en 0,1 s d'immobilité."""
    return TravelSettings(mode="test", settle=0, walk_timeout=30, still_after=0.05, still_for=0.05, **changes)


def test_unreachable_sun_given_up_in_seconds_when_the_character_does_not_move():
    # 11/10, l'utilisateur : 60 s perdues à chaque mur des remparts d'Astrub (3 essais de 20 s). Le personnage ne bouge
    # plus et n'a pas changé de carte : soleil inatteignable d'ici, vu en quelques secondes.
    foret = load_frame("hors_combat_foret")
    t = Traveler(FakeGame(foret), fast(), World(None))
    frame, label = t.look()
    start = time.monotonic()
    assert t.go(frame, label, "droite") is None
    assert time.monotonic() - start < 5 and len(clicks(t.game)) == 2


@pytest.mark.parametrize("arrival, first, then", [((19, 1), (17, 31), (3, 31)), ((3, 1), (3, 31), (17, 31))])
def test_two_suns_on_one_side_the_one_facing_the_arrival_first(arrival, first, then):
    # 11/10, -1,-19 (cité d'Astrub) : deux soleils en bas, un de chaque côté du rempart. Le plus proche de l'arrivée
    # d'abord (du même côté du mur, le plus souvent), l'autre s'il ne mène nulle part ; puis le bas est noté
    # inatteignable pour qui arrive par le haut seulement (de l'autre côté du mur, il marche peut-être).
    t = Traveler(FakeGame(load_frame("deux_soleils_en_bas")), fast(), World(None))
    t.entry, t.entry_cell = "haut", arrival
    frame, label = t.look()
    assert label.coords == (-1, -19)
    assert t.go(frame, label, "bas") is None
    cells = [frame.grid.cell_at(*point) for point in clicks(t.game)]
    assert len(cells) == 4 and cells[0] == first and cells[2] == then
    key = t.key(label)
    assert t.world.blocked_from(key, "haut") == {"bas"} and t.world.destination(key, "bas") is None
    assert t.world.several(key, "bas")  # retenu : la case gardée ne suffira pas sans le côté d'arrivée


class SimLabel:
    """Nom de carte simulé : coordonnées et zone."""

    region = None
    region_name = "Incarnam"
    readable = True

    def __init__(self, coords, zone):
        self.coords, self.zone_name, self.zone = coords, zone, zone
        self.text = f"{coords[0]},{coords[1]}"

    def same_map(self, other):
        return self.coords == other.coords

    def same_zone(self, other):
        return self.zone_name == other.zone_name

    def same_region(self, other):
        return True

    def describe(self):
        return f"Incarnam ({self.zone_name}) {self.text}"


class SimTraveler:
    """Monde simulé : une carte par coordonnées, une sortie vers chaque carte voisine existante.
    Les règles d'interdiction sont celles du vrai Traveler."""

    forbidden = Traveler.forbidden
    forbidden_side = Traveler.forbidden_side
    neighbour = Traveler.neighbour
    here = Traveler.here
    region = "Incarnam"
    jumped = False
    atlas = Atlas()  # pas de références : tout se découvre en route

    def __init__(self, maps, start, **avoid):
        self.maps, self.pos, self.world, self.moves = maps, start, World(None), []
        self.settings = TravelSettings(**avoid)

    @property
    def coords(self):
        return self.pos

    def label(self, coords):
        label = SimLabel(coords, self.maps[coords])
        label.region_name = self.region
        return label

    def look(self):
        return None, self.label(self.pos)

    def key(self, label):
        return World.key(label.region_name, label.coords)

    def exits(self, frame, wanted=None, complete=False):
        return {side: Exit(side, (0, 0), (0, 0), 1.0) for side, (dx, dy) in DELTA.items()
                if (self.pos[0] + dx, self.pos[1] + dy) in self.maps}

    def go(self, frame, label, side, exits=None):
        dx, dy = DELTA[side]
        new = (self.pos[0] + dx, self.pos[1] + dy)
        if new not in self.maps:
            return None
        before = self.key(label)
        self.pos = new
        self.moves.append(side)
        arrived = self.label(new)
        self.world.link(before, side, self.key(arrived))
        self.world.link(self.key(arrived), OPPOSITE[side], before)
        return arrived


def test_round_trip_goes_two_maps_away_and_comes_back():
    sim = SimTraveler({(x, y): "Forêt" for x in range(0, 4) for y in range(0, 4)}, (1, 1))
    trip = RoundTrip(sim, steps=2)
    while not trip.done:
        trip.step()
    assert len(sim.moves) == 4 and sim.pos == (1, 1)
    assert sim.moves[2:] == [OPPOSITE[s] for s in reversed(sim.moves[:2])]  # retour par le même chemin
    assert sim.moves[1] != OPPOSITE[sim.moves[0]]  # jamais de demi-tour à l'aller


def test_exploration_visits_every_map_then_stops():
    maps = {(x, y): "Forêt" for x in range(0, 3) for y in range(0, 3)}
    sim = SimTraveler(maps, (0, 0))
    explorer = Explorer(sim)
    for _ in range(60):
        if explorer.done:
            break
        explorer.step()
    assert explorer.done
    assert {key for key in sim.world.maps} == {f"Incarnam {x},{y}" for x, y in maps}


def test_exploration_needs_a_known_position():
    sim = SimTraveler({(0, 0): "Forêt"}, (0, 0))
    sim.key = lambda label: None  # coordonnées illisibles, pas de --position
    explorer = Explorer(sim)
    explorer.step()
    assert explorer.done and sim.moves == []


def test_zone_mode_stays_in_the_zone():
    maps = {(0, 0): "Forêt", (1, 0): "Forêt", (2, 0): "Champs", (0, 1): "Forêt"}
    sim = SimTraveler(maps, (1, 0))
    sim.world.maps["Incarnam 2,0"] = {"zone": "Champs", "sorties": {}}
    sim.world.link("Incarnam 1,0", "droite", "Incarnam 2,0")  # déjà vue : hors de la zone
    roamer = ZoneRoamer(sim)
    for _ in range(6):
        roamer.step()
        assert maps[sim.pos] == "Forêt"
    assert "bas" in sim.moves  # la carte jamais vue de la zone (0, 1) a été visitée


def test_exploration_never_crosses_a_forbidden_zone():
    maps = {(0, 0): "Forêt", (1, 0): "Forêt", (2, 0): "Cimetière", (3, 0): "Forêt", (1, 1): "Forêt"}
    sim = SimTraveler(maps, (0, 0), avoid_zones=(("Incarnam", "Cimetière"),))
    explorer = Explorer(sim)
    visited = []
    for _ in range(40):
        if explorer.done:
            break
        explorer.step()
        visited.append(sim.pos)
    assert explorer.done
    assert (3, 0) not in visited  # derrière le Cimetière : jamais atteinte
    assert visited.count((2, 0)) <= 1 and maps[sim.pos] != "Cimetière"  # entré une fois (zone inconnue), demi-tour


def test_forbidden_map_is_never_entered():
    maps = {(0, 0): "Forêt", (1, 0): "Forêt", (2, 0): "Forêt"}
    sim = SimTraveler(maps, (0, 0), avoid_maps=(("Incarnam", (1, 0)),))
    explorer = Explorer(sim)
    for _ in range(10):
        explorer.step()
    assert explorer.done and sim.moves == []  # seule sortie : vers la carte interdite


def test_forbidden_zone_only_in_its_region():
    t = Traveler(FakeGame(load_frame("hors_combat")), TravelSettings(avoid_zones=(_parse_zone("Incarnam (Cimetière)"),)),
                 World(None))
    assert t.forbidden((3, 6), "Cimetière", "Incarnam") == "zone Cimetière interdite"
    assert t.forbidden((3, 6), "Cimetière", "Astrub") is None  # Astrub n'est pas concerné
    assert _parse_map("Incarnam 3,6") == ("Incarnam", (3, 6)) and _parse_map("-1, -20") == (None, (-1, -20))
    assert _parse_zone("Cimetière") == (None, "Cimetière")


def test_exploration_stops_when_the_character_is_moved_elsewhere():
    sim = SimTraveler({(0, 0): "Forêt", (1, 0): "Forêt"}, (0, 0))
    sim.jumped = True  # mort au combat : renvoyé ailleurs
    explorer = Explorer(sim)
    explorer.step()
    assert explorer.done and sim.moves == []


def test_incarnam_reference_data():
    atlas = Atlas.load()
    start = atlas.get("Incarnam", (1, 6))
    assert start.zone == "Forêt" and start.suns == {"haut": ((9, 1),), "droite": ((27, 11),)}
    assert start.leads_to == {"haut": (1, 5), "droite": (2, 6)}
    assert atlas.get("Incarnam", (0, 2)).others == ((21, 31),)  # soleil sans direction : pas une carte voisine
    assert {atlas.zone("Incarnam", (x, 6)) for x in (3, 4, 5)} == {"Cimetière"}
    assert len([m for m in atlas.maps.values() if m.region == "Incarnam"]) == 46
    assert {p["carte"] for p in atlas.places if p["type"] == "zaap" and p["region"] == "Incarnam"} == {"1,3", "5,0", "6,4", "2,6"}


def test_reference_suns_are_clicked_without_searching():
    frame = load_frame("hors_combat_nom_illisible")  # 1,1 : nom illisible, soleils jamais vus par le bot
    t = Traveler(FakeGame(frame), TravelSettings(mode="explore", start=(1, 1)), World(None), Atlas.load())
    t.region = "Incarnam"
    exits = t.exits(frame, "haut")
    assert exits["haut"].cell == (21, 1) and exits["haut"].remembered  # case du soleil d'après config/monde
    assert presses(t.game) == []  # ni recherche, ni touche A


def test_cemetery_forbidden_from_the_reference_zone():
    t = Traveler(FakeGame(load_frame("hors_combat")), TravelSettings(avoid_zones=(_parse_zone("Incarnam (Cimetière)"),)),
                 World(None), Atlas.load())
    t.region, t.coords = "Incarnam", (2, 6)
    assert t.forbidden_side("droite") == "zone Cimetière interdite"  # 3,6, jamais vue : zone connue des références
    assert t.forbidden_side("haut") is None


def test_tour_of_incarnam_from_0_2_with_the_reference_maps():
    atlas = Atlas.load()
    maps = {m.coords: m.zone for m in atlas.maps.values() if m.region == "Incarnam"}
    sim = SimTraveler(maps, (0, 2), avoid_zones=(("Incarnam", "Cimetière"),))
    sim.atlas = atlas
    explorer = Explorer(sim)
    path = [sim.pos]
    for _ in range(200):
        if explorer.done:
            break
        explorer.step()
        path.append(sim.pos)
    cemetery = {(3, 6), (4, 6), (5, 6)}
    assert explorer.done
    assert set(path) == set(maps) - cemetery  # les 43 cartes autorisées, et elles seules
    assert len(sim.moves) < 80  # chaque fois vers la plus proche : pas d'allers-retours inutiles


def test_partly_unreadable_region_does_not_matter():
    frame = load_frame("hors_combat_region_partielle")  # 2,1 : « Inca » sur un décor clair, seul « rnam » est lu
    label = read_map_label(frame)
    assert label.coords == (2, 1) and label.region_name is None and label.zone_name == "Prairie"
    t = Traveler(FakeGame(frame), TravelSettings(mode="explore"), World(None), Atlas.load())
    t.region, t.coords = "Incarnam", (3, 1)  # arrivé de 3,1 par le soleil de gauche
    t._locate(frame, label, (2, 1))
    assert t.key(label) == "Incarnam 2,1"


def test_someone_on_the_sun_menu_closed_then_bottom_of_the_cell_clicked():
    foret, menu, champs = load_frame("hors_combat_foret"), load_frame("hors_combat_menu_personnage"), load_frame("hors_combat")
    # 21/10 : souvent soi-même, arrivé par ce soleil ; Échap ne ferme pas ce menu. Un clic à côté le ferme et écarte le
    # personnage, puis le soleil de nouveau, sans compter d'essai.
    t = traveler(foret, foret, foret, menu, menu, foret, foret, champs, champs)  # carte, recherche avec A, menu...
    frame, label = t.look()
    assert t.go(frame, label, "droite").coords == (4, 3)
    assert "escape" not in presses(t.game)
    first, step, again = clicks(t.game)
    cx, cy = frame.grid.center((27, 9))
    # Case du bord : le bas du losange, sous les pieds du personnage, dès le premier clic (21/10).
    assert abs(first[0] - cx) <= 8 and first[1] - cy >= 9 and abs(again[0] - cx) <= 8 and again[1] - cy >= 9
    x0, y0, x1, y1 = find_context_menu(menu)
    assert not (x0 - 10 <= step[0] <= x1 + 10 and y0 - 50 <= step[1] <= y1 + 10)


def test_readable_name_of_the_departure_map_is_not_an_arrival():
    foret = load_frame("hors_combat_foret")  # 2,4 : le nom se lit maintenant, mais c'est toujours la carte de départ
    t = Traveler(FakeGame(foret), TravelSettings(mode="explore", settle=0, walk_timeout=0.05), World(None))
    assert t._wait_arrival(unreadable_label(), foret, (2, 4)) is False


def test_mouse_left_alone_when_the_sun_is_known():
    frame = load_frame("hors_combat_nom_illisible")
    t = Traveler(FakeGame(frame), TravelSettings(mode="explore", start=(1, 1)), World(None), Atlas.load())
    t.look()
    t.exits(frame, "haut")
    assert t.game.parks == [False]  # ni souris écartée, ni recherche : la case du soleil est connue


def test_world_of_twelve_reference_data():
    atlas = Atlas.load()
    astrub = atlas.get("Monde des Douze", (2, -20))
    assert astrub.zone == "Cité d'Astrub" and astrub.suns == {}  # lu en jeu le 28/09 ; pas de soleils hors Incarnam
    forest = atlas.get("Monde des Douze", (3, -26))
    assert forest.zone == "Forêt d'Astrub" and any(o["nom"] == "Frêne" for o in forest.objects)
    assert atlas.get("Incarnam", (1, 6)).zone == "Forêt"  # Incarnam et le Monde des Douze ne se mélangent pas
    places = {(p["region"], p["type"]) for p in atlas.places}
    assert ("Monde des Douze", "zaap") in places and ("Monde des Douze", "donjon") in places


def world_sim(start):
    atlas = Atlas.load()
    maps = {m.coords: m.zone for m in atlas.maps.values() if m.region == "Monde des Douze"}
    sim = SimTraveler(maps, start)
    sim.atlas, sim.region = atlas, "Monde des Douze"
    return sim


def test_zone_mode_fights_and_moves_only_inside_the_current_zone():
    sim = world_sim((4, -11))  # Le coin des Tofus (Astrub)
    zone = sim.atlas.zone("Monde des Douze", (4, -11))
    roamer = ZoneRoamer(sim)
    seen = set()
    for _ in range(40):  # 40 fois « plus de groupe sur cette carte »
        roamer.step()
        seen.add(sim.pos)
        assert sim.atlas.zone("Monde des Douze", sim.pos) == zone  # jamais hors de la zone
    assert zone == "Le coin des Tofus" and len(seen) >= 10  # et il en fait le tour plutôt que de faire des allers-retours


def test_world_exploration_covers_only_the_starting_zone():
    sim = world_sim((4, -11))
    explorer = Explorer(sim, zone_only=True)
    visited = {sim.pos}
    for _ in range(200):
        if explorer.done:
            break
        explorer.step()
        visited.add(sim.pos)
    zone_maps = sim.atlas.zone_maps("Monde des Douze", "Le coin des Tofus")
    reachable, todo = set(), [(4, -11)]  # cartes de la zone reliées entre elles (de proche en proche)
    while todo:
        c = todo.pop()
        if c in reachable or c not in zone_maps:
            continue
        reachable.add(c)
        todo += [(c[0] + dx, c[1] + dy) for dx, dy in DELTA.values()]
    assert explorer.done and visited == reachable


def test_combat_zone_found_from_the_given_coordinates(caplog):
    caplog.set_level("INFO")
    t = Traveler(FakeGame(load_frame("hors_combat")), TravelSettings(mode="zone", start=(1, 1)), World(None), Atlas.load())
    roamer = ZoneRoamer(t)
    assert roamer.zone_name == "Prairie"  # Incarnam 1,1 d'après les références, dès le lancement
    assert "sous-zone Prairie (10 cartes" in caplog.text
    atlas = t.atlas
    assert atlas.get("Monde des Douze", (4, -11)).area == "Astrub"  # région de la sous-zone, dans le Monde des Douze


def test_combat_zone_in_incarnam_prairie_stays_in_the_prairie():
    atlas = Atlas.load()
    maps = {m.coords: m.zone for m in atlas.maps.values() if m.region == "Incarnam"}
    sim = SimTraveler(maps, (1, 1), avoid_zones=(("Incarnam", "Cimetière"),))
    sim.atlas = atlas
    roamer = ZoneRoamer(sim)
    seen = set()
    for _ in range(30):
        roamer.step()
        seen.add(sim.pos)
        assert maps[sim.pos] == "Prairie"
    assert len(seen) >= 8  # presque toute la Prairie (10 cartes)


def test_travel_test_mode_launches_no_fight():
    bot = Bot(FakeGame(load_frame("hors_combat")), load_settings(), travel="test")
    assert bot.engager is None and isinstance(bot.roamer, RoundTrip)
    assert Bot(FakeGame(load_frame("hors_combat")), load_settings()).roamer is None  # par défaut : on reste


def test_zone_mode_keeps_unreachable_groups_in_the_map_memory(tmp_path):
    # Mode zone (seul à changer de carte en combattant) : l'attaque des groupes note ses zones hors d'atteinte dans
    # la mémoire des cartes, pour la carte sûre et le côté d'arrivée du trajet. Sans déplacement : rien de gardé.
    settings = load_settings()
    settings["travel"] = {**(settings.get("travel") or {}), "memory": str(tmp_path / "carte.json")}
    bot = Bot(FakeGame(load_frame("hors_combat")), settings, travel="zone")
    assert bot.engager._memory is bot.roamer.traveler.world
    assert bot.engager._where == bot.roamer.traveler.place
    assert Bot(FakeGame(load_frame("hors_combat")), settings).engager._memory is None


def test_place_is_the_sure_map_and_the_arrival_side():
    foret, champs = load_frame("hors_combat_foret"), load_frame("hors_combat")
    t = traveler(foret, foret, foret, champs, champs)
    assert t.place() is None  # position pas encore lue
    frame, label = t.look()
    assert t.place() == ("Incarnam 2,4", START)
    t.go(frame, label, "droite")
    assert t.place() == ("Incarnam 4,3", "gauche")
    t.learns = False  # trajet d'un suiveur qui rejoint : rien n'est noté
    assert t.place() is None


def test_sun_clicked_again_while_still_on_the_departure_map(monkeypatch):
    # 03/10 : clic qui a visé un personnage de passage sur le soleil : rien ne bouge. Recliqué toutes les
    # RECLICK_EVERY s tant que le décor n'a pas changé (en marche, recliquer la même case ne change rien).
    monkeypatch.setattr("bot.brain.travel.RECLICK_EVERY", 0)
    foret, champs = load_frame("hors_combat_foret"), load_frame("hors_combat")
    t = Traveler(FakeGame(foret, foret, foret, foret, champs, champs), TravelSettings(mode="test", settle=0, still_for=0),
                 World(None))  # sans la détection d'immobilité (still_for 0) : l'ancien reclic
    frame, label = t.look()
    assert t.go(frame, label, "droite").coords == (4, 3)
    sun = clicks(t.game)
    assert len(sun) >= 2 and len(set(sun)) == 1  # même point, recliqué avant l'arrivée


def test_zone_mode_searches_all_suns_when_the_known_ones_leave_the_zone():
    # 03/10, Champs d'Astrub 9,-25 : seul le soleil du bas (vers 9,-24, hors de la zone) était retenu ; le bot est
    # resté 7 minutes sur une carte vide. Tous les soleils sont alors cherchés à l'écran, une fois par carte.
    sim = world_sim((9, -25))
    calls = []
    every_exit = SimTraveler.exits

    def exits(frame, wanted=None, complete=False):
        calls.append(complete)
        found = every_exit(sim, frame, wanted, complete)
        return found if complete else {"bas": found["bas"]}  # mémoire : le seul soleil vu jusqu'ici

    sim.exits = exits
    roamer = ZoneRoamer(sim)
    assert roamer.zone_name == "Champs d'Astrub"
    roamer.step()
    assert calls == [False, True] and sim.pos == (8, -25)  # la carte de gauche, dans les Champs


def test_label_read_in_a_taller_window():
    # 15/10 : barre des tâches masquée, fenêtre de 1920x1057 (échelle 1,049) : le « 1 » de « -19 » à 0,74 de son modèle,
    # le « 9 » à égalité avec le « 8 », et « Astrub » / « Cité d'Astrub » écartés pour 2 px de largeur de trop.
    image = np.zeros((1057, 1920, 3), np.uint8)
    image[0:120, 500:1100] = cv2.imread(str(FIXTURES / "nom_de_carte_echelle_1057.png"))
    label = read_map_label(Frame.of(image))
    assert label is not None and label.coords == (-1, -19)
    assert label.region_name == "Astrub" and label.zone_name == "Cité d'Astrub"


def test_sun_on_an_edge_cell_clicked_on_the_bottom_of_its_diamond_first():
    # 21/10, Astrub aller-retour : les 9 clics sans effet sur 42 changements de carte, tous sur des soleils de la rangée
    # du haut, du bas ou de la colonne de gauche, autour du centre ; les reclics, sur le bas du losange, tous partis.
    from bot.brain.travel import _click_spot

    frame = load_frame("hors_combat_foret")
    for cell in ((13, 1), (1, 17), (27, 9)):
        (x, y), spread = _click_spot(frame, cell, 0)
        cx, cy = frame.grid.center(cell)
        assert abs(x - cx) < 1 and y - cy >= 0.5 * frame.grid.half_h and spread[1] < 0.15 * frame.grid.half_h
    (x, y), _ = _click_spot(frame, (13, 15), 0)  # au milieu de la carte : le centre, comme avant
    assert (round(x), round(y)) == tuple(round(v) for v in frame.grid.center((13, 15)))


def test_sun_outlined_on_pale_grass_with_the_grid_off_by_half_a_cell():
    # 23/10, Tainéla 5,-30 : herbe vert pâle prise pour du contour, et losange du soleil de droite 14 px au-dessus du
    # centre de la grille ; seule la sortie de gauche (en contrebas, inaccessible) était vue, le trajet arrêté.
    frame = load_frame("soleil_droite_grille_decalee_herbe_claire")
    exits = {e.side: e for e in find_marked_exits(frame)}
    assert set(exits) == {"gauche", "droite"} and exits["droite"].cell == (27, 25)
    x, y = exits["droite"].point
    assert abs(x - 1868) <= 4 and abs(y - 630) <= 4  # le losange vu, pas la case de la grille (y 643)
    (cx, cy), _ = _click_spot(frame, (27, 25), 1, exits["droite"].point)
    assert abs(cy - 630) < 0.6 * frame.grid.half_h + 2  # même le bas du losange reste dans la case vue


def test_route_prefers_passages_already_taken_over_a_shorter_unknown_way():
    # 23/10 : en ligne droite de 11,-28 vers -3,-29, le bot est entré dans Le berceau (plateau fermé) par des passages
    # jamais vus. Passage confirmé : 1 ; jamais pris : 2 ; entrée dans une autre zone (ni départ ni arrivée) : 2 de plus.
    from bot.brain.travel import shortest_route

    grid = {(0, 0): {"droite": (1, 0), "bas": (0, 1)}, (1, 0): {"droite": (2, 0)}, (2, 0): {},
            (0, 1): {"droite": (1, 1)}, (1, 1): {"haut": (1, 0), "droite": (2, 1)}, (2, 1): {"haut": (2, 0)}}
    confirmed = {((0, 0), "bas"), ((0, 1), "droite"), ((1, 1), "droite"), ((2, 1), "haut")}
    cost = lambda here, side, dest: 1.0 if (here, side) in confirmed else 3.0
    links = lambda coords, entry: grid[coords]
    assert [d for _, d in shortest_route((0, 0), None, {(2, 0)}, links, lambda c: True)] == [(1, 0), (2, 0)]
    assert [d for _, d in shortest_route((0, 0), None, {(2, 0)}, links, lambda c: True, cost)] == \
        [(0, 1), (1, 1), (2, 1), (2, 0)]  # 4 passages connus plutôt que 2 jamais pris


def test_passages_reference_exported_from_the_memory_without_losing_a_known_passage():
    from bot.brain.travel import Passages
    from tools.passages import exporter

    memory = {"Monde des Douze 5,-30": {"sorties": {"droite": "Monde des Douze 6,-30", "bas": "bloquée"},
                                         "bloquees_depuis": {"droite": ["gauche"]}},
              "Monde des Douze 6,-29": {"sorties": {"gauche": "bloquée"}},
              "Incarnam 1,1": {"sorties": {"bas": "Incarnam 1,2"}}}
    reference = {"6,-29": {"sorties": {"gauche": "5,-29"}}}  # déjà pris : une sortie « bloquée » ne l'efface pas
    out = exporter(memory, "Monde des Douze", reference)
    assert set(out) == {"5,-30", "6,-29"}
    assert out["5,-30"]["sorties"] == {"droite": "6,-30", "bas": "bloquée"} and out["6,-29"]["sorties"]["gauche"] == "5,-29"
    passages = Passages(out)
    assert passages.exits((5, -30)) == {"droite": (6, -30), "bas": "bloquée"}
    assert passages.blocked_from((5, -30), "droite") == {"gauche"} and passages.blocked_from((5, -30), None) == set()


def test_completed_reference_used_after_what_was_seen_in_game():
    # 24/10 : référence complétée (data/references, StarLoco contrôlé sur les cartes officielles) : passages et cases des
    # soleils ; ce qui a été vu en jeu prime ; blocages selon le côté d'arrivée calculés sur les cases praticables.
    from bot.brain.travel import Passages

    seen = {"5,-30": {"sorties": {"bas": "bloquée"}, "bloquees_depuis": {"haut": ["bas"]}}}
    reference = {"5,-30": {"sorties": {"droite": {"vers": "6,-30", "cases": [[27, 25]]},
                                       "gauche": {"vers": "4,-30", "cases": [[1, 29], [1, 7]]},
                                       "bas": {"vers": "5,-29", "cases": [[13, 31]]}},
                           "bloquees_depuis": {"droite": ["gauche"]}}}
    passages = Passages(seen, reference)
    assert passages.exits((5, -30)) == {"droite": (6, -30), "gauche": (4, -30), "bas": "bloquée"}
    assert passages.sun_cell((5, -30), "droite") == (27, 25)
    assert passages.sun_cell((5, -30), "gauche") is None  # deux soleils de ce côté : la recherche à l'écran choisit
    assert passages.blocked_from((5, -30), "droite") == {"gauche"} and passages.blocked_from((5, -30), "haut") == {"bas"}
    assert passages.describes((5, -30)) and not passages.describes((0, 0))
