"""Recherche du groupe à attaquer (bot/brain/engage.py, bot/perception/groups.py), sur des captures Z réelles du 03/10.

Ce jour-là :
- 13 des 23 groupes cherchés en haut de la carte étaient AU-DESSUS de leur infobulle (passée sous eux faute de place) :
  le bot les cherchait d'abord partout en dessous, 8 survols (4,4 s) ; un autre n'a jamais été trouvé ;
- sur le sable des Calanques d'Astrub, les infobulles étaient lues à 6 ou 8 monstres au lieu de 1 à 3 (le sable clair
  pris pour du texte) : survols 100 à 200 px sous les groupes ; et 4 groupes « trouvés » sur une infobulle restée
  affichée après le passage de la souris sur le groupe pendant son trajet : clic à côté, pas de menu « Attaquer »,
  puis les 2 groupes de la carte sautés en boucle (journaux 184624, 191636, 193222) : plus aucun combat.

Faux jeu propre à ce fichier (copie de celui de test_brain.py, plus le survol des groupes) : ces tests ne dépendent pas
de ceux du combat. 08/10 : en arrière-plan, le survol ne montre rien ; le groupe est cherché par des clics directs
(BackgroundGame).
"""

import math
from contextlib import contextmanager

import cv2
import pytest

from bot.brain.engage import (BELOW_OFFSETS, DIRECT_ATTACK_WAIT, EDGE_SHIFTS, HOVER_WAIT, NEIGHBOURS, NO_MENU_LIMIT,
                              PROBES, RETRY_SKIPPED, SHOWN_ON_ARRIVAL, SKIP_FOR, UNREACHABLE_HITS, UNREACHABLE_RECHECK,
                              Z_LINGER, Engager, EngageSettings, hover_points)
from bot.brain.game import Game
from bot.brain.travel import World
from bot.config import load_settings
from bot.perception.frame import Frame
from bot.perception.groups import column_offset, find_group_tooltips, under_its_group
from bot.perception.layout import Layout
from conftest import FIXTURES, load_frame, with_flash_menu


class FakeRecorder:
    def keeps(self, lossless=False):
        return True

    def save(self, image, label, lossless=False, quality=80):
        return f"<{label}>"


class FakeControl:
    """Pauses entre deux captures de wait_until : elles avancent l'horloge simulée du faux jeu."""

    def __init__(self, game: "FakeGame") -> None:
        self.game = game

    def sleep(self, seconds):
        self.game.clock += seconds


class FakeGame:
    """Rejoue les captures dans l'ordre (la dernière se répète) ; aucune attente réelle, clics sans hasard. Horloge
    simulée `clock`, avancée par les attentes (game.sleep, et les pauses de wait_until)."""

    wait_until = Game.wait_until

    def __init__(self, *frames: Frame) -> None:
        self.frames = list(frames)
        self.actions: list[tuple] = []
        self.recorder = FakeRecorder()
        self.cursor = None
        self.parks: list = []
        self.clock = 0.0
        self.control = FakeControl(self)

    def look(self, park=True):
        self.parks.append(park)
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]

    def sleep(self, seconds):
        self.clock += seconds

    def now(self):
        return self.clock

    def move(self, point, spread=(0, 0)):
        self.cursor = (round(point[0]), round(point[1]))
        self.actions.append(("move", self.cursor))
        return self.cursor

    def click(self, point=None, spread=(0, 0), button="left"):
        if point is not None:
            self.move(point, spread)
        self.actions.append(("click", self.cursor))
        return True

    def press(self, key):
        self.actions.append(("press", key))

    @contextmanager
    def holding(self, key):
        self.actions.append(("hold", key))
        yield


def clicks(game):
    return [a[1] for a in game.actions if a[0] == "click"]


def moves(game):
    return [a[1] for a in game.actions if a[0] == "move"]


def inside(point, box) -> bool:
    x0, y0, x1, y1 = box
    return point is not None and x0 <= point[0] <= x1 and y0 <= point[1] <= y1


MENU = cv2.imread(str(FIXTURES / "click_lancer_le_combat.png"))  # menu « Monstres / Attaquer / Bestiaire »


def with_menu(frame: Frame, at) -> tuple[Frame, tuple[int, int, int, int]]:
    """Même capture, menu « Attaquer » ouvert en `at` (son coin haut gauche) ; et la place du menu."""
    image = frame.image.copy()
    x, y = at
    h, w = MENU.shape[:2]
    image[y : y + h, x : x + w] = MENU
    return Frame.of(image), (x, y, x + w, y + h)


def only_tooltip(z: Frame, empty: Frame, tooltip) -> Frame:
    """Capture de la carte sans Z où seule l'infobulle de ce groupe est affichée (celle de son survol)."""
    image = empty.image.copy()
    x0, y0, x1, y1 = tooltip.box
    image[y0 : y1 + 1, x0 : x1 + 1] = z.image[y0 : y1 + 1, x0 : x1 + 1]
    return Frame.of(image)


class HoverGame(FakeGame):
    """Faux jeu où survoler un groupe montre son infobulle : capture `z` tant que Z est enfoncée ; `groups[sprite]`
    tant que la souris est sur le rectangle `sprite` d'un groupe ; `empty` ailleurs.

    `ghosts` {n: (capture, s)} : à l'arrivée du n-ième déplacement de la souris, l'infobulle d'un groupe est encore
    affichée pendant s secondes (la souris est passée sur lui en chemin, comme le 03/10). Clic sur un groupe : son
    menu « Attaquer » s'ouvre (si `menu`) ; clic dans ce menu : `fight` (le placement)."""

    def __init__(self, z, empty, groups, menu=True, fight=None, ghosts=None) -> None:
        super().__init__(empty)
        self.z, self.empty, self.groups = z, empty, groups
        self.menu, self.fight = menu, fight if fight is not None else load_frame("preparation")
        self.ghosts = ghosts or {}
        self.z_held = False
        self.moved = 0
        self.ghost = None  # (capture, jusqu'à quand)
        self.state, self.menu_frame, self.menu_box = "carte", None, None
        self.buttons: list[str] = []  # bouton de chaque clic

    @contextmanager
    def holding(self, key):
        self.actions.append(("hold", key))
        self.z_held = True
        try:
            yield
        finally:
            self.z_held = False

    def move(self, point, spread=(0, 0)):
        aim = super().move(point, spread)
        self.moved += 1
        if self.moved in self.ghosts:
            frame, seconds = self.ghosts[self.moved]
            self.ghost = (frame, self.clock + seconds)
        return aim

    def hovered(self):
        return next((frame for sprite, frame in self.groups.items() if inside(self.cursor, sprite)), None)

    def look(self, park=True):
        self.parks.append(park)
        if self.state == "combat":
            return self.fight
        if self.state == "menu":
            return self.menu_frame
        if self.z_held:
            return self.z
        shown = self.hovered()
        if shown is None and self.ghost is not None and self.clock < self.ghost[1]:
            shown = self.ghost[0]
        return shown if shown is not None else self.empty

    def click(self, point=None, spread=(0, 0), button="left"):
        super().click(point, spread, button)
        self.buttons.append(button)
        if self.state == "menu":
            self.state = "combat" if inside(self.cursor, self.menu_box) else "carte"
        elif self.menu and self.hovered() is not None:
            self.open_menu()
        return True

    def open_menu(self):
        x, y = self.cursor
        self.menu_frame, self.menu_box = with_menu(self.empty, (min(x + 10, 1700), min(y + 10, 600)))
        self.state = "menu"


class BackgroundGame(HoverGame):
    """Arrière-plan (08/10) : aucun survol ne montre d'infobulle (la vraie souris est ailleurs, le jeu reçoit aussitôt
    « souris partie »). Clic sur un des rectangles `sprites` d'un groupe : son menu « Attaquer » pour un bouton de
    `opens`, le combat aussitôt pour un bouton de `direct`. Clic gauche à côté : le personnage marche jusque-là
    (`walks`) ; clic droit à côté : rien. `click_times` : l'horloge simulée à chaque clic."""

    background = True

    def __init__(self, z, empty, sprites, opens=("left",), direct=(), **kwargs) -> None:
        super().__init__(z, empty, {}, **kwargs)  # aucun groupe ne se montre au survol
        self.sprites, self.opens, self.direct = sprites, opens, direct
        self.walks: list = []
        self.click_times: list[float] = []

    def click(self, point=None, spread=(0, 0), button="left"):
        FakeGame.click(self, point, spread, button)
        self.buttons.append(button)
        self.click_times.append(self.clock)
        on_group = any(inside(self.cursor, sprite) for sprite in self.sprites)
        if self.state == "menu":
            self.state = "combat" if inside(self.cursor, self.menu_box) else "carte"
        elif on_group and button in self.direct:
            self.state = "combat"
        elif on_group and button in self.opens:
            self.open_menu()
        elif button == "left":
            self.walks.append(self.cursor)
        return True


def tooltip_at(frame, x, y):
    """Infobulle dont le titre « Niveau N » est en (x, y), à 3 px près."""
    return next(t for t in find_group_tooltips(frame) if abs(t.x - x) <= 3 and abs(t.title_y - y) <= 3)


def on_group(point, group) -> bool:
    """Survol qui touche le groupe : à 14 x 10 px près du point qui l'a touché le 03/10."""
    return abs(point[0] - group[0]) <= 14 and abs(point[1] - group[1]) <= 10


# --- Lecture des infobulles -------------------------------------------------------------------------------------------

# (capture Z, titre de l'infobulle, monstres qu'elle liste)
MONSTER_COUNTS = [
    # Sable des Calanques d'Astrub (03/10) : lues 6 et 8 avant la correction.
    ("groupes_z_sable_calanques", (1148, 352), 3),  # Pichon Bleu, Pichon Blanc, Crabe
    ("groupes_z_sable_calanques", (1148, 548), 3),  # 3 Pichons Orange
    ("groupes_z_sable_un_monstre", (1533, 100), 1),  # Pichon Blanc : lue 8
    ("groupes_z_sable_un_monstre", (762, 198), 1),  # Pichon Bleu : lue 8
    # Toit de paille jaune sous la ligne : lue 4.
    ("groupes_z_toit_de_paille", (1052, 100), 1),
    ("groupes_z_toit_de_paille", (811, 179), 3),
    # Déjà justes avant : inchangées.
    ("hors_combat_detection_enemi", (1196, 298), 2),
    ("groupes_z_haut_mixte", (1629, 78), 2),
    ("groupes_z_bord_droit", (1484, 179), 3),
    ("groupes_z_chevauchement", (811, 495), 2),
]


@pytest.mark.parametrize("name, title, count", MONSTER_COUNTS)
def test_monster_lines_are_text_on_the_dark_tooltip(name, title, count):
    frame = load_frame(name)
    assert tooltip_at(frame, *title).monsters == count


def test_side_of_the_group_read_from_its_cell():
    above = load_frame("groupes_z_haut_groupes_dessus")
    assert under_its_group(tooltip_at(above, 1532, 140), above) is True
    assert under_its_group(tooltip_at(above, 1099, 264), above) is True
    mixed = load_frame("groupes_z_haut_mixte")
    assert under_its_group(tooltip_at(mixed, 1629, 78), mixed) is False
    assert under_its_group(tooltip_at(mixed, 1292, 166), mixed) is True
    assert under_its_group(tooltip_at(mixed, 1051, 238), mixed) is True
    # Sur le sable, le bas de l'infobulle enfin juste : la case du groupe est sous elle (avant : « on ne sait pas »).
    sand = load_frame("groupes_z_sable_calanques")
    assert under_its_group(tooltip_at(sand, 1148, 352), sand) is False
    assert under_its_group(tooltip_at(sand, 1148, 548), sand) is False


# --- Où survoler ------------------------------------------------------------------------------------------------------

# (capture Z, titre de l'infobulle, point de survol qui a touché le groupe le 03/10 ; survols qu'il avait fallu)
TOP_OF_THE_MAP = [
    ("groupes_z_haut_groupes_dessus", (1532, 140), (1531, 82)),  # 8e survol : groupe au-dessus de son infobulle
    ("groupes_z_haut_groupes_dessus", (1099, 264), (1100, 205)),  # jamais trouvé (Bouftou visible au-dessus)
    ("groupes_z_haut_mixte", (1629, 78), (1629, 233)),  # 1er survol : groupe dessous, infobulle tout en haut
    ("groupes_z_bord_droit_portraits", (1788, 128), (1834, 286)),  # 5e survol : repoussée, bout sous les portraits
]


@pytest.mark.parametrize("name, title, group", TOP_OF_THE_MAP)
def test_group_at_the_top_of_the_map_hovered_first(name, title, group):
    frame = load_frame(name)
    assert on_group(hover_points(tooltip_at(frame, *title), frame)[0], group)


# Monstres sur le sable (03/10), rectangle de leur sprite mesuré sur les captures sans Z prises juste après : jamais
# survolés avant la correction (survols 100 à 200 px plus bas, ou de l'autre côté de l'infobulle).
SAND_GROUPS = [
    ("groupes_z_sable_calanques", (1148, 352), (1120, 497, 1183, 547)),  # Pichon Blanc
    ("groupes_z_sable_calanques", (1148, 548), (1120, 690, 1187, 740)),  # Pichon Orange
    ("groupes_z_sable_un_monstre", (1533, 100), (1510, 202, 1565, 247)),  # Pichon Blanc
    ("groupes_z_sable_un_monstre", (762, 198), (740, 297, 802, 347)),  # Pichon Bleu
]


@pytest.mark.parametrize("name, title, sprite", SAND_GROUPS)
def test_group_on_sand_hovered_first_on_the_monster(name, title, sprite):
    frame = load_frame(name)
    assert inside(hover_points(tooltip_at(frame, *title), frame)[0], sprite)


def test_other_side_tried_at_the_second_hover_near_the_top():
    # Groupe sous son infobulle, en haut de la carte : la place au-dessus vient juste après (si la case trompait).
    frame = load_frame("groupes_z_haut_mixte")
    tooltip = tooltip_at(frame, 1629, 78)
    first, second = hover_points(tooltip, frame)[:2]
    assert first[1] > tooltip.bottom and second[1] < tooltip.top


def test_away_from_the_top_and_the_edges_below_then_the_neighbour_cells():
    # Sous l'infobulle (50, 62, 38 px), puis les 4 cases voisines en diagonale au niveau du corps ; jamais au-dessus,
    # jamais plus loin qu'une case (avant le 03/10 : ±45 et ±90 px).
    for name, title in [("hors_combat_detection_enemi", (1196, 298)), ("groupes_z_bord_droit_portraits", (955, 394)),
                        ("groupes_z_bord_droit", (1196, 420)), ("groupes_z_sable_calanques", (1148, 352))]:
        frame = load_frame(name)
        tooltip = tooltip_at(frame, *title)
        length, grid = frame.layout.length, frame.grid
        body = tooltip.bottom + length(BELOW_OFFSETS[0])
        expected = [(tooltip.x, tooltip.bottom + length(d)) for d in BELOW_OFFSETS]
        expected += [(tooltip.x + c * grid.half_w, body + r * grid.half_h) for c, r in NEIGHBOURS]
        assert hover_points(tooltip, frame) == expected
        assert all(abs(x - tooltip.x) <= grid.half_w + 1 for x, _ in expected)


def test_group_searched_below_first_at_the_top_of_the_map_when_its_cell_says_so():
    # Déplacé de test_brain.py (03/10) : le balayage sur les côtés n'est plus de ±90 px mais d'une case.
    frame = load_frame("groupes_z_bord_droit")  # 01/10 : « Niveau 1 » en haut à droite, Bouftou en (1773, 355)
    tooltip = max(find_group_tooltips(frame), key=lambda t: t.x)
    assert tooltip.clipped  # repoussée par le bord droit (16 px de marge)
    points = hover_points(tooltip, frame)
    assert abs(points[0][0] - 1773) <= 10 and abs(points[0][1] - 355) <= 10  # le Bouftou, du premier coup
    first_above = next(i for i, (_, y) in enumerate(points) if y < tooltip.top)
    assert all(y > tooltip.bottom for _, y in points[:first_above])
    # Haut de la carte : au-dessus dès la 2e rangée (après la 1re et ses décalages vers le bord), plus en dernier.
    assert first_above == 3
    xs = {round(x - tooltip.x) for x, _ in points}
    assert {round(frame.layout.length(d)) for d in EDGE_SHIFTS} <= xs  # vers le bord de la carte, collée à lui
    assert min(xs) < 0  # et la case voisine de l'autre côté


def test_tooltip_pushed_by_the_edge_under_the_team_portraits():
    frame = load_frame("groupes_z_bord_droit_portraits")
    pushed = tooltip_at(frame, 1788, 128)
    assert pushed.right < frame.layout.map_box[2] - 50  # son bout est caché par les portraits de l'équipe
    assert pushed.clipped and column_offset(pushed, frame) > 10  # plus centrée sur une colonne de cases
    assert not tooltip_at(frame, 955, 394).clipped  # au milieu de la carte, centrée sur sa colonne


# --- Survol : quand le groupe est-il trouvé ? -------------------------------------------------------------------------

def above_group_game(**kwargs) -> HoverGame:
    """« Niveau 1 » en haut de la carte, son Bouftou au-dessus d'elle en (1531, 82) (03/10)."""
    return HoverGame(load_frame("groupes_z_haut_groupes_dessus"), load_frame("hors_combat"),
                     {(1517, 72, 1545, 92): load_frame("groupes_z_haut_groupes_dessus")}, **kwargs)


def test_group_above_its_tooltip_found_at_once(caplog):
    caplog.set_level("INFO")
    game = above_group_game()
    engager = Engager(game, EngageSettings())
    frame = engager._show_groups()
    released = game.clock
    tooltip = tooltip_at(frame, 1532, 140)
    aim = engager._find_group(frame, tooltip)
    assert aim is not None and on_group(aim, (1531, 82))
    assert len(moves(game)) == 1
    # Infobulle là dès l'arrivée (le Bouftou survolé juste après Z) : prise au bout de SHOWN_ON_ARRIVAL, pas avant le
    # délai de Z. 0,3 s au lieu de 8 survols (4,4 s).
    assert game.clock - released <= max(Z_LINGER, SHOWN_ON_ARRIVAL) + 0.1
    assert f"survol 1/{len(hover_points(tooltip, frame))}, au-dessus de son infobulle" in caplog.text


def test_tooltips_of_z_still_shown_are_not_taken_for_a_hover():
    # Juste après Z, ses infobulles peuvent rester affichées un instant : elles ne prouvent pas un survol réussi.
    game = FakeGame(load_frame("hors_combat"), load_frame("groupes_z_haut_groupes_dessus"))  # Z, puis encore là
    engager = Engager(game, EngageSettings())
    frame = engager._show_groups()
    released = game.clock
    assert engager._find_group(frame, tooltip_at(frame, 1532, 140)) is not None
    assert game.clock - released >= Z_LINGER


def test_tooltip_left_by_the_mouse_crossing_the_group_is_not_a_find():
    # 03/10 (193222) : « Groupe trouvé au survol 3/10 ; infobulle vue 62 ms après l'arrivée de la souris » ; la souris
    # était passée sur le Pichon Blanc en allant du 2e survol (au-dessus de l'infobulle) au 3e (en dessous) ; clic à
    # côté, pas de menu « Attaquer ». Ici le groupe n'est sous aucun point de survol : l'infobulle vue à l'arrivée du
    # 3e survol, puis disparue la souris immobile, n'est pas le groupe.
    z = load_frame("groupes_z_sable_calanques")
    empty = load_frame("hors_combat_sable_calanques")
    tooltip = tooltip_at(z, 1148, 352)
    shown = only_tooltip(z, empty, tooltip)
    game = HoverGame(z, empty, {}, ghosts={3: (shown, 0.15)})
    engager = Engager(game, EngageSettings())
    frame = engager._show_groups()
    assert engager._find_group(frame, tooltip) is None
    assert clicks(game) == []


def test_tooltip_already_there_and_staying_is_the_group():
    # La souris est passée sur le groupe et s'y est arrêtée : l'infobulle ne disparaît pas, le groupe est trouvé.
    z = load_frame("groupes_z_sable_calanques")
    empty = load_frame("hors_combat_sable_calanques")
    tooltip = tooltip_at(z, 1148, 352)
    shown = only_tooltip(z, empty, tooltip)
    game = HoverGame(z, empty, {(1120, 497, 1183, 547): shown}, ghosts={1: (shown, 0.15)})
    engager = Engager(game, EngageSettings())
    frame = engager._show_groups()
    game.clock += 1  # loin de Z
    posed = game.clock
    assert engager._find_group(frame, tooltip) is not None
    assert SHOWN_ON_ARRIVAL <= game.clock - posed <= SHOWN_ON_ARRIVAL + 0.1


def test_tooltip_appearing_under_the_still_mouse_is_found_at_the_second_capture():
    # Cas des 5 attaques réussies du 03/10 : pas d'infobulle à l'arrivée, puis elle apparaît (vue à 0,2 s). Trouvé dès
    # qu'elle est là sur deux captures de suite, sans attendre SHOWN_ON_ARRIVAL.
    z = load_frame("groupes_z_sable_calanques")
    empty = load_frame("hors_combat_sable_calanques")
    tooltip = tooltip_at(z, 1148, 352)
    shown = only_tooltip(z, empty, tooltip)

    class SlowTooltip(HoverGame):
        def look(self, park=True):
            frame = super().look(park)
            return self.empty if frame is shown and self.clock - self.arrived < 0.15 else frame

        def move(self, point, spread=(0, 0)):
            self.arrived = self.clock
            return super().move(point, spread)

    game = SlowTooltip(z, empty, {(1120, 497, 1183, 547): shown})
    engager = Engager(game, EngageSettings())
    frame = engager._show_groups()
    game.clock += 1
    posed = game.clock
    assert engager._find_group(frame, tooltip) is not None
    assert game.clock - posed == pytest.approx(0.3)  # vue à 0,2 s, confirmée à 0,3 s
    assert game.clock - posed < HOVER_WAIT


# --- Attaque, échecs et reprises --------------------------------------------------------------------------------------

def sand_game(menu=True) -> HoverGame:
    """Calanques d'Astrub (03/10, 193222) : 2 groupes de 3 monstres, chacun montre sa seule infobulle au survol."""
    z = load_frame("groupes_z_sable_calanques")
    empty = load_frame("hors_combat_sable_calanques")
    groups = {sprite: only_tooltip(z, empty, tooltip_at(z, *title))
              for name, title, sprite in SAND_GROUPS if name == "groupes_z_sable_calanques"}
    return HoverGame(z, empty, groups, menu=menu)


def test_group_on_sand_attacked_at_the_first_hover():
    # 03/10 (193222) : le bot n'y lançait plus aucun combat. Ici : 1er survol sur le monstre, clic sur lui, Attaquer.
    game = sand_game()
    assert Engager(game, EngageSettings()).engage()
    group_click, attack_click = clicks(game)
    assert any(inside(group_click, sprite) for _, _, sprite in SAND_GROUPS[:2])
    assert len(moves(game)) == 2  # un survol, puis le clic sur « Attaquer »
    assert inside(attack_click, game.menu_box)


def test_engage_finds_group_below_its_tooltip_and_attacks():
    # Déplacé de test_brain.py (03/10), avec le survol simulé : 1er survol (57 px sous l'infobulle) : rien ; 2e (47 px)
    # sur le groupe ; clic -> menu ; clic Attaquer -> placement.
    groups = load_frame("hors_combat_detection_enemi")  # une infobulle (x 1196, bas 400)
    game = HoverGame(groups, load_frame("hors_combat"), {(1186, 441, 1206, 452): groups})
    assert Engager(game, EngageSettings()).engage()
    assert ("hold", "z") in game.actions
    group_click, attack_click = clicks(game)
    assert abs(group_click[0] - 1196) <= 2 and abs(group_click[1] - 447) <= 2
    assert inside(attack_click, game.menu_box)  # entrée « Attaquer »


def test_click_without_menu_retried_once_then_the_group_is_skipped(caplog):
    # Groupe « trouvé » mais clic sans menu « Attaquer » : retenté à la recherche suivante, puis sauté (le 03/10 : en
    # boucle). Seuls sur la carte, les groupes sautés sont retentés après RETRY_SKIPPED s, sans Z toutes les 1,7 s.
    caplog.set_level("INFO")
    game = sand_game(menu=False)
    engager = Engager(game, EngageSettings())
    assert not engager.engage()
    assert len(clicks(game)) == 2  # les deux groupes, chacun une fois
    assert engager.missed and not engager.none_found  # nouvelle recherche aussitôt
    for _ in range(NO_MENU_LIMIT - 1):
        assert not engager.engage()
    assert len(clicks(game)) == 2 * NO_MENU_LIMIT
    assert "je passe aux autres" in caplog.text
    assert engager.none_found and not engager.missed  # tous ratés : la boucle attend (ou change de carte)
    game.actions.clear()
    assert not engager.engage()
    assert moves(game) == []  # sautés : pas de survol
    game.clock += RETRY_SKIPPED
    assert not engager.engage()
    assert len(clicks(game)) == 2  # retentés
    assert "je retente 2 groupe(s)" in caplog.text


def test_group_with_a_click_without_menu_comes_after_the_others():
    game = sand_game()
    engager = Engager(game, EngageSettings())
    engager._no_menu.append((tooltip_at(game.z, 1148, 352), game.now()))  # Pichon Blanc : clic sans menu juste avant
    assert engager.engage()
    assert inside(clicks(game)[0], SAND_GROUPS[1][2])  # les Pichons Orange d'abord


def test_unfindable_group_alone_on_the_map_retried_after_a_delay():
    groups, empty = load_frame("hors_combat_detection_enemi"), load_frame("hors_combat")
    (tooltip,) = find_group_tooltips(groups)
    hovers = len(hover_points(tooltip, groups))
    game = HoverGame(groups, empty, {})  # groupe caché (derrière une maison) : aucun survol ne le montre
    engager = Engager(game, EngageSettings())
    assert not engager.engage()
    assert len(moves(game)) == hovers and engager.none_found
    game.actions.clear()
    assert not engager.engage()  # recherche suivante : sauté, sans survol
    assert moves(game) == [] and engager.none_found
    game.clock += RETRY_SKIPPED
    assert not engager.engage()
    assert len(moves(game)) == hovers  # retenté


# --- Arrière-plan : clics directs, sans survol (08/10) ----------------------------------------------------------------

def sand_background_game(**kwargs) -> BackgroundGame:
    """Calanques d'Astrub (03/10) en arrière-plan : 2 groupes, le 1er point de chacun sur le monstre."""
    return BackgroundGame(load_frame("groupes_z_sable_calanques"), load_frame("hors_combat_sable_calanques"),
                          [sprite for name, _, sprite in SAND_GROUPS if name == "groupes_z_sable_calanques"], **kwargs)


def beside_background_game(**kwargs) -> BackgroundGame:
    """Une infobulle (x 1196, bas 400) : le 1er point (57 px sous elle) tombe à côté du groupe, le 2e (47 px) sur lui."""
    return BackgroundGame(load_frame("hors_combat_detection_enemi"), load_frame("hors_combat"),
                          [(1186, 441, 1206, 452)], **kwargs)


def test_background_group_clicked_at_once_without_hovering(caplog):
    # Le survol ne montre rien : le 1er point (sur le monstre) est cliqué directement, menu, « Attaquer », placement.
    # Un seul Z ; au premier plan, ce groupe serait resté introuvable (aucune infobulle au survol).
    caplog.set_level("INFO")
    game = sand_background_game()
    assert Engager(game, EngageSettings(probe_click="gauche")).engage()
    group_click, attack_click = clicks(game)
    assert any(inside(group_click, sprite) for sprite in game.sprites)
    assert inside(attack_click, game.menu_box)
    assert game.buttons == ["left", "left"] and game.walks == []
    assert game.actions.count(("hold", "z")) == 1
    assert f"Groupe visé sans survol (arrière-plan), clic gauche 1/{PROBES} en " in caplog.text
    assert "Groupe trouvé au survol" not in caplog.text


def test_background_click_beside_the_group_then_the_next_point(caplog):
    # 1er clic à côté : pas de menu, le personnage y marche. Combat guetté le temps de sa marche, Z de nouveau (les
    # groupes ont pu bouger), puis le point suivant pas encore cliqué (47 px) : menu, « Attaquer ».
    caplog.set_level("INFO")
    game = beside_background_game()
    assert Engager(game, EngageSettings(probe_click="gauche")).engage()
    beside, group_click, attack_click = clicks(game)
    assert game.walks == [beside] and abs(beside[0] - 1196) <= 2 and abs(beside[1] - 457) <= 2
    assert abs(group_click[0] - 1196) <= 2 and abs(group_click[1] - 447) <= 2
    assert inside(attack_click, game.menu_box)
    assert game.actions.count(("hold", "z")) == 2  # Z refait après le clic à côté
    assert game.click_times[1] - game.click_times[0] >= DIRECT_ATTACK_WAIT  # sa marche attendue avant le 2e clic
    assert f"clic gauche 2/{PROBES}" in caplog.text and "le personnage a pu y marcher" in caplog.text


def test_background_right_click_probe():
    # engage.clic_sonde: droit (défaut, 08/10 : vérifié par l'utilisateur) : sur le groupe, le personnage y marche et le
    # combat se lance, sans menu. (Clic droit à côté : menu de Flash, voir FlashMenuGame.)
    game = sand_background_game(opens=(), direct=("right",))
    assert Engager(game, EngageSettings()).engage()
    assert game.buttons == ["right"]


def test_background_groups_out_of_reach_left_until_the_next_map(caplog):
    # 17/10 (Incarnam 3,0) : clic droit sur le groupe, ni menu de Flash ni combat (le personnage marche, le groupe est de
    # l'autre côté d'un obstacle). Pas de 2e clic dessus ; touché UNREACHABLE_HITS fois au même endroit, il est laissé
    # bien après SKIP_FOR, la carte compte comme vide (mode zone : carte suivante), jusqu'au changement de carte.
    caplog.set_level("INFO")
    game = sand_background_game(opens=(), direct=())
    engager = Engager(game, EngageSettings())
    assert not engager.engage()
    assert game.buttons == ["right"] and "hors d'atteinte ?" in caplog.text
    for _ in range(2 * UNREACHABLE_HITS - 1):
        assert not engager.engage()
    assert len(clicks(game)) == 2 * UNREACHABLE_HITS
    game.clock += SKIP_FOR + RETRY_SKIPPED
    game.actions.clear()
    assert not engager.engage()
    assert clicks(game) == [] and engager.none_found and not engager.missed
    assert "Plus que des groupes hors d'atteinte sur la carte" in caplog.text
    engager.new_map()
    assert not engager.engage()
    assert len(clicks(game)) == 1


MAP_3_0 = "Incarnam 3,0"


def unreachable_visit(world, entry="gauche", **kwargs) -> tuple[BackgroundGame, Engager]:
    """Visite d'Incarnam 3,0 (arrivée par `entry`), sable des Calanques en arrière-plan : clic droit sur un groupe, ni
    menu ni combat (hors d'atteinte), sauf `direct`."""
    game = sand_background_game(opens=(), **{"direct": (), **kwargs})
    engager = Engager(game, EngageSettings())
    engager.remember_in(world, lambda: (MAP_3_0, entry))
    return game, engager


def learn_unreachable(world, entry="gauche") -> BackgroundGame:
    """Les 2 groupes touchés UNREACHABLE_HITS fois chacun sans combat : leurs endroits sont notés."""
    game, engager = unreachable_visit(world, entry)
    for _ in range(2 * UNREACHABLE_HITS):
        assert not engager.engage()
    assert len(clicks(game)) == 2 * UNREACHABLE_HITS
    return game


def test_unreachable_zones_remembered_for_the_next_visit(tmp_path, caplog):
    # 17/10, Incarnam 3,0 : ~1 min de clics à chaque passage pour réapprendre les groupes hors d'atteinte. Notés dans
    # data/carte.json (par carte, par côté d'arrivée), ils sont laissés d'emblée au passage suivant, même après
    # relance (mémoire relue du fichier) : aucun clic, la carte compte comme vide (mode zone : carte suivante).
    caplog.set_level("INFO")
    path = tmp_path / "carte.json"
    learn_unreachable(World(path))
    assert "Endroit hors d'atteinte noté pour les prochains passages" in caplog.text
    zones = World(path).unreachable_from(MAP_3_0, "gauche")
    assert len(zones) == 2 and all(zone["laissee"] == 0 for zone in zones)
    game, engager = unreachable_visit(World(path))
    assert not engager.engage()
    assert clicks(game) == [] and engager.none_found and not engager.missed
    assert "2 zone(s) hors d'atteinte notée(s) à un passage précédent" in caplog.text
    assert [zone["laissee"] for zone in World(path).unreachable_from(MAP_3_0, "gauche")] == [1, 1]


def test_unreachable_zones_kept_in_reference_pixels():
    # Les endroits notés ne dépendent pas de la taille de la fenêtre : repère des captures de référence.
    world = World(None)
    game = learn_unreachable(world)
    layout = game.z.layout
    for zone in world.unreachable_from(MAP_3_0, "gauche"):
        assert any(math.dist((zone["x"], zone["y"]), layout.to_reference(click)) <= 1 for click in clicks(game))
    small = Layout(959, 504)
    assert small.to_reference(small.point(1148.0, 402.0)) == pytest.approx((1148, 402), abs=1)


def test_unreachable_zones_depend_on_the_arrival_side():
    # Carte coupée en deux : arrivé par l'autre côté, c'est l'autre moitié qui est accessible ; rien n'est laissé.
    world = World(None)
    learn_unreachable(world, entry="gauche")
    game, engager = unreachable_visit(world, entry="droite")
    assert not engager.engage()
    assert len(clicks(game)) == 1


def test_map_never_noted_unchanged(tmp_path):
    # Carte sans zone notée (ou position pas sûre) : comme avant, rien d'écrit tant que rien n'est confirmé.
    path = tmp_path / "carte.json"
    game, engager = unreachable_visit(World(path))
    assert not engager.engage()
    assert len(clicks(game)) == 1 and not path.exists()
    world = World(None)
    learn_unreachable(world)
    game = sand_background_game(opens=(), direct=())
    engager = Engager(game, EngageSettings())
    engager.remember_in(world, lambda: None)  # carte pas sûre : la mémoire n'est ni lue ni écrite
    assert not engager.engage()
    assert len(clicks(game)) == 1


def test_noted_zone_rechecked_with_one_click_then_confirmed(caplog):
    # Toutes les UNREACHABLE_RECHECK visites où elle a été laissée, une zone est revérifiée : un seul clic, qui la
    # confirme s'il touche sans combat (compteur remis à zéro), et ses groupes sont de nouveau laissés.
    caplog.set_level("INFO")
    world = World(None)
    learn_unreachable(world)
    world.set_unreachable_from(MAP_3_0, "gauche", [{**zone, "laissee": UNREACHABLE_RECHECK}
                                                   for zone in world.unreachable_from(MAP_3_0, "gauche")])
    game, engager = unreachable_visit(world)
    assert not engager.engage()
    assert len(clicks(game)) == 1 and "(2 à revérifier d'un clic)" in caplog.text
    assert "Zone hors d'atteinte confirmée" in caplog.text
    assert sorted(zone["laissee"] for zone in world.unreachable_from(MAP_3_0, "gauche")) == [0, UNREACHABLE_RECHECK]
    assert not engager.engage()
    assert len(clicks(game)) == 2  # l'autre zone revérifiée à son tour
    game.actions.clear()
    assert not engager.engage()
    assert clicks(game) == [] and engager.none_found


def test_combat_in_a_noted_zone_forgets_it(caplog):
    # Pas de faux positif éternel : à sa revérification, le groupe de la zone lance le combat ; elle est retirée.
    caplog.set_level("INFO")
    world = World(None)
    learn_unreachable(world)
    world.set_unreachable_from(MAP_3_0, "gauche", [{**zone, "laissee": UNREACHABLE_RECHECK}
                                                   for zone in world.unreachable_from(MAP_3_0, "gauche")])
    game, engager = unreachable_visit(world, direct=("right",))
    assert engager.engage()
    assert "je l'oublie" in caplog.text
    assert len(world.unreachable_from(MAP_3_0, "gauche")) == 1
    world.set_unreachable_from(MAP_3_0, "gauche", [])
    assert "hors_atteinte_depuis" not in world.maps[MAP_3_0]


def test_background_clicks_limited_then_the_group_is_skipped(caplog):
    # Groupes jamais touchés (cachés derrière une maison) : PROBES clics au plus par recherche, pour un seul groupe (le
    # personnage a marché, les autres ont pu bouger : nouvelle recherche, avec Z). Puis comptés comme des clics sans
    # menu au premier plan : chaque groupe sauté au bout de NO_MENU_LIMIT recherches.
    caplog.set_level("INFO")
    game = BackgroundGame(load_frame("groupes_z_sable_calanques"), load_frame("hors_combat_sable_calanques"), [])
    engager = Engager(game, EngageSettings(probe_click="gauche"))
    assert not engager.engage()
    assert len(clicks(game)) == len(game.walks) == PROBES
    assert engager.missed and not engager.none_found  # nouvelle recherche aussitôt
    for _ in range(2 * NO_MENU_LIMIT - 1):
        assert not engager.engage()
    assert len(clicks(game)) == 2 * NO_MENU_LIMIT * PROBES
    assert "je passe aux autres" in caplog.text
    assert engager.none_found and not engager.missed


def test_background_group_gone_after_a_click_beside(caplog):
    # Après le clic à côté, Z ne montre plus son infobulle près de là : le groupe est parti, pas de 2e clic au hasard.
    caplog.set_level("INFO")

    class Leaving(BackgroundGame):
        def click(self, point=None, spread=(0, 0), button="left"):
            self.z = self.empty  # le groupe quitte la carte
            return super().click(point, spread, button)

    game = Leaving(load_frame("hors_combat_detection_enemi"), load_frame("hors_combat"), [])
    engager = Engager(game, EngageSettings(probe_click="gauche"))
    assert not engager.engage()
    assert len(clicks(game)) == 1
    assert "le groupe est parti" in caplog.text


class FlashMenuGame(BackgroundGame):
    """Clic droit à côté du groupe : le menu de Flash s'ouvre au clic (14/10, un Piou). Échap ne le ferme pas ; un clic
    gauche hors du menu si (`closes` : le clic qui le ferme ; un clic gauche sur la carte ferait aussi marcher le
    personnage, compté dans `walks`)."""

    def __init__(self, *args, closes=True, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.closes = closes
        self.flash = None

    def look(self, park=True):
        if self.flash is not None:
            self.parks.append(park)
            return self.flash
        return super().look(park)

    def click(self, point=None, spread=(0, 0), button="left"):
        if self.flash is None:
            super().click(point, spread, button)
            if button == "right" and self.state == "carte":
                self.flash = with_flash_menu(self.empty, self.cursor)
            return True
        FakeGame.click(self, point, spread, button)
        self.buttons.append(button)
        self.click_times.append(self.clock)
        if self.empty.layout.on_map(self.cursor):
            self.walks.append(self.cursor)
        if self.closes:
            self.flash = None
        return True


def test_background_right_click_beside_closes_the_flash_menu_under_the_spell_bar(caplog):
    # Le 1er clic droit tombe à côté du groupe : menu de Flash. Fermé aussitôt d'un clic gauche sous la barre de sorts (pas sur
    # la carte, pas d'Échap, pas d'attente du combat), puis Z et le point suivant, sur le groupe : combat.
    caplog.set_level("INFO")
    game = FlashMenuGame(load_frame("hors_combat_detection_enemi"), load_frame("hors_combat"), [(1186, 441, 1206, 452)],
                         opens=(), direct=("right",))
    assert Engager(game, EngageSettings()).engage()
    assert game.buttons == ["right", "left", "right"]
    beside, close, group = clicks(game)
    assert not game.empty.layout.on_map(close) and close[0] > game.empty.layout.width * 0.7 and game.walks == []
    assert ("press", "escape") not in game.actions
    assert game.click_times[1] - game.click_times[0] < 1  # vu dès la 1re capture, pas RIGHT_CLICK_WAIT d'attente
    assert "menu de Flash" in caplog.text and "Menu de Flash fermé" in caplog.text


def test_background_flash_menu_that_stays_open_is_never_escaped(caplog):
    caplog.set_level("INFO")
    game = FlashMenuGame(load_frame("hors_combat_detection_enemi"), load_frame("hors_combat"), [(1186, 441, 1206, 452)],
                         opens=(), direct=("right",), closes=False)
    assert not Engager(game, EngageSettings()).engage()
    assert ("press", "escape") not in game.actions and game.walks == []
    assert game.buttons[0] == "right" and set(game.buttons[1:]) <= {"left", "right"}
    assert not any(game.empty.layout.on_map(c) for c, b in zip(clicks(game), game.buttons) if b == "left")
    assert "ferme-le toi-même" in caplog.text


@pytest.mark.parametrize("background", [None, False])
def test_foreground_still_hovers_whatever_the_probe_click(background, caplog):
    # Premier plan (attribut background absent ou faux) : survol puis clic gauche sur place, comme avant ;
    # engage.clic_sonde ne sert qu'en arrière-plan.
    caplog.set_level("INFO")
    game = sand_game()
    if background is not None:
        game.background = background
    assert Engager(game, EngageSettings(probe_click="droit")).engage()
    assert game.buttons == ["left", "left"] and len(moves(game)) == 2
    assert "Groupe trouvé au survol 1/" in caplog.text and "sans survol" not in caplog.text


def test_probe_click_setting():
    assert EngageSettings.from_settings({}).probe_click == "droit"
    assert EngageSettings.from_settings(load_settings()).probe_click == "droit"
    assert EngageSettings.from_settings({"engage": {"clic_sonde": "gauche"}}).probe_click == "gauche"
    assert EngageSettings.from_settings({"engage": {"clic_sonde": "Droit"}}).probe_click == "droit"
    with pytest.raises(ValueError, match="clic_sonde"):
        EngageSettings.from_settings({"engage": {"clic_sonde": "milieu"}})
