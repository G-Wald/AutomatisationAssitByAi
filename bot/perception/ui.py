"""Repères de l'interface : boutons, timeline, timer de tour, portée du sort, zone de déplacement.

Les zones sont en coordonnées de référence (voir layout.py), mesurées sur tests/fixtures.
"""

import math
import random
from functools import lru_cache

import cv2
import numpy as np

from bot.perception.blobs import find_blobs
from bot.perception.colors import RANGE_BLUE, RED, TEAMS, green_overlay_mask, near, orange_mask, team_mask
from bot.perception.frame import Frame
from bot.perception.grid import Cell
from bot.perception.layout import MAP_BOTTOM, SCENE_LEFT, Layout, Point

READY_ZONE, READY_SPREAD = (1700, 690, 1918, MAP_BOTTOM), (35, 8)  # bouton « Prêt » du placement
END_TURN_ZONE, END_TURN_SPREAD = (1400, 930, 1505, 1008), (12, 7)  # bouton de fin de tour (flèche sous la minimap)
TIMELINE_ZONE = (SCENE_LEFT, 640, 1888, MAP_BOTTOM)  # portraits des combattants (sans les flèches de défilement)
RING_CENTER, RING_RADII, RING_SPLIT_Y = (1328, 903), (60, 118), 935  # anneau autour de la minimap
SPELL_SLOTS_X, SPELL_SLOTS_STEP, SPELL_ROWS_Y = 1533, 51, (904.5, 956.5)
SPELL_EXTRA_SLOT = (1476.5, 904.5)  # bouton à gauche de la barre de sorts, visible en combat
SPELL_SLOT_SPREAD = (9, 9)
PA_BUBBLE = (1231, 801, 1276, 846)  # bulle des PA (chiffre bleu), à gauche du cœur
PM_BUBBLE = (1380, 806, 1422, 846)  # bulle des PM (chiffre vert), à droite du cœur
HEART_ZONE = (1290, 776, 1368, 848)  # cœur de la vie, au-dessus de la minimap
TOOLBAR_ZONE = (1690, 650, 1918, 715)  # boutons au-dessus de la timeline (placement et combat)
TACTICAL_OFF = "mode_tactique_off.png"  # cube sans coche verte : mode tactique désactivé
# Où écarter la souris de la carte sans rien survoler d'utile (ni la barre de sorts, ni les PA / PM) :
# bas-gauche de l'interface (fiche d'effets en combat, carte du monde hors combat) et bas du chat.
# Un point tiré au hasard à chaque fois, dans l'une ou l'autre zone.
PARK_ZONES = ((640, 830, 1190, 1000), (20, 560, 540, 950))


def _center(blob) -> Point:
    x, y = blob.center
    return round(x), round(y)


def find_ready_button(frame: Frame) -> Point | None:
    box = frame.layout.box(*READY_ZONE)
    for blob in find_blobs(orange_mask(frame.crop(box)), offset=box[:2]):
        if blob.w >= frame.layout.length(110) and blob.h >= frame.layout.length(28):
            return _center(blob)
    return None


def find_end_turn_button(frame: Frame) -> Point | None:
    """Masqué quand la souris survole un combattant (sa fiche remplace la barre de sorts). Le bouton fait environ
    70 x 40 px ; le morceau de l'anneau du timer qui entre dans la zone en fait 25 x 41 à 44, pour 648 à 672 px de
    surface, à 4 à 8 % du seuil de surface (audit du 03/10) : la largeur les sépare nettement."""
    box = frame.layout.box(*END_TURN_ZONE)
    blobs = find_blobs(orange_mask(frame.crop(box)), offset=box[:2])
    big = [b for b in blobs if b.area >= frame.layout.area(700) and b.w >= frame.layout.length(50)]
    return _center(max(big, key=lambda b: b.area)) if big else None


def current_fighter_team(frame: Frame) -> str | None:
    """Équipe du combattant qui joue : son portrait est encadré d'orange, sa barre de vie a la couleur de l'équipe.

    None hors combat (pas de timeline).
    """
    layout = frame.layout
    box = layout.box(*TIMELINE_ZONE)
    for blob in find_blobs(orange_mask(frame.crop(box)), offset=box[:2]):
        if layout.length(50) <= blob.w <= layout.length(72) and layout.length(58) <= blob.h <= layout.length(82):
            portrait = frame.crop((blob.x, blob.y, blob.x + blob.w, blob.y + blob.h))
            counts = {team: int(team_mask(portrait, team).sum()) for team in TEAMS}
            team = max(counts, key=counts.get)
            if counts[team] >= layout.area(40):
                return team
    return None


def turn_timer_visible(frame: Frame) -> bool:
    """Timer orange sur la moitié haute de l'anneau de la minimap.

    Visible pendant le tour du personnage de cette fenêtre (et pendant le placement), jamais pendant
    le tour des autres. Il peut manquer en fin de décompte : ne pas s'y fier seul.
    """
    layout = frame.layout
    cx, cy = RING_CENTER
    r_in, r_out = RING_RADII
    box = layout.box(cx - r_out, cy - r_out, cx + r_out, RING_SPLIT_Y)
    region = frame.crop(box)
    ys, xs = np.mgrid[box[1] : box[3], box[0] : box[2]]
    radius = np.hypot(xs - layout.x(cx), ys - layout.y(cy))
    ring = (radius >= layout.length(r_in)) & (radius < layout.length(r_out))
    return int((orange_mask(region) & ring).sum()) >= layout.area(150)


POD_STEP = 6  # degrés entre deux mesures autour de l'anneau
POD_START = 30  # le cœur de la vie cache le haut de l'anneau : la jauge y est supposée pleine dès qu'elle commence
POD_GAP = 2  # mesures non orange tolérées dans la jauge (séparations entre ses 8 segments)


def pods_fill(frame: Frame) -> float:
    """Hors combat : remplissage de la jauge des pods, de 0 à 1. C'est l'anneau autour de la minimap (en combat, le
    timer du tour) : orange depuis le haut, dans le sens des aiguilles d'une montre (captures de l'utilisateur du
    11/10 : à moitié, puis presque plein). Mesuré angle par angle, au milieu de l'épaisseur de l'anneau."""
    layout = frame.layout
    cx, cy = layout.x(RING_CENTER[0]), layout.y(RING_CENTER[1])
    r_in, r_out = layout.length(RING_RADII[0]), layout.length(RING_RADII[1])
    mask = orange_mask(frame.image)
    height, width = mask.shape
    filled = []
    for angle in range(0, 360, POD_STEP):
        t = math.radians(angle)
        hits = total = 0
        for k in (0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85):
            r = r_in + k * (r_out - r_in)
            x, y = round(cx + r * math.sin(t)), round(cy - r * math.cos(t))
            if 0 <= x < width and 0 <= y < height:
                total += 1
                hits += bool(mask[y, x])
        filled.append(total > 0 and hits / total > 0.5)
    end, gap = None, 0
    for index in range(POD_START // POD_STEP, len(filled)):
        if filled[index]:
            end, gap = index, 0
        elif end is not None or index > (POD_START // POD_STEP) + POD_GAP:
            gap += 1
            if gap > POD_GAP:
                break
    if end is None:
        return 0.0
    reached = (end + 1) * POD_STEP
    return 1.0 if reached >= 360 - POD_START else reached / 360  # jusque sous le cœur, de l'autre côté : pleine


def spell_range_visible(frame: Frame) -> bool:
    """Cases bleues de portée affichées : un sort est sélectionné et lançable (assez de PA)."""
    return int(near(frame.crop(frame.layout.map_box), RANGE_BLUE, 25).sum()) >= frame.layout.area(3000)


def forbidden_cursor_near(frame: Frame, point: Point) -> bool:
    """Curseur « interdit » (rond rouge barré d'environ 40 px) sur la case survolée : pas une cible valide.

    On cherche large et on ignore les taches coupées par le bord de la zone : sinon mon propre cercle rouge,
    coupé en deux quand la cible est au contact, ressemble à un rond de 40 px.
    """
    layout = frame.layout
    x, y = point
    half, near_cursor = layout.length(120), layout.length(60)
    box = (max(0, round(x - half)), max(0, round(y - half)), min(layout.width, round(x + half)), min(layout.height, round(y + half)))
    for blob in find_blobs(team_mask(frame.crop(box), RED), offset=box[:2]):
        cut = blob.x <= box[0] or blob.y <= box[1] or blob.x + blob.w >= box[2] or blob.y + blob.h >= box[3]
        bx, by = blob.center
        if (not cut and abs(bx - x) <= near_cursor and abs(by - y) <= near_cursor
                and layout.length(30) <= blob.w <= layout.length(50) and 0.75 <= blob.w / blob.h <= 1.33):
            return True
    return False


def movement_zone(frame: Frame) -> set[Cell]:
    """Cases vertes : zone de déplacement du combattant survolé par la souris."""
    grid = frame.grid
    cells = grid.cells()
    if not cells:
        return set()
    mask = green_overlay_mask(frame.image)
    centers = np.array([grid.center(c) for c in cells])
    # Quatre points par case, vers ses coins : le centre est souvent caché par un cercle ou un sprite.
    offsets = np.array([(-0.6, 0), (0.6, 0), (0, -0.6), (0, 0.6)]) * (grid.half_w, grid.half_h)
    points = np.rint(centers[:, None, :] + offsets[None, :, :]).astype(int)
    xs = points[..., 0].clip(0, frame.layout.width - 1)
    ys = points[..., 1].clip(0, frame.layout.height - 1)
    green = mask[ys, xs].sum(axis=1)
    return {cell for cell, count in zip(cells, green) if count >= 3}


def spell_slot_point(layout: Layout, slot: int) -> Point:
    """0 : bouton à gauche de la barre ; 1 à 7 : première ligne ; 8 à 14 : deuxième ligne."""
    if slot == 0:
        return layout.point(*SPELL_EXTRA_SLOT)
    if not 1 <= slot <= 14:
        raise ValueError(f"Emplacement de sort invalide : {slot} (0 à 14)")
    row, column = divmod(slot - 1, 7)
    return layout.point(SPELL_SLOTS_X + column * SPELL_SLOTS_STEP, SPELL_ROWS_Y[row])


def park_point(layout: Layout) -> Point:
    """Où écarter la souris. La zone du chat n'est gardée que si elle tient dans la fenêtre : calée à droite comme la
    scène, elle sort de toute fenêtre moins large que 1,88:1 (plein écran 16:9, 16:10...), et un point hors de la
    fenêtre arrêtait le bot."""
    zones = [z for z in PARK_ZONES if layout.x(z[0]) >= 0] or PARK_ZONES[:1]
    areas = [(x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in zones]
    x0, y0, x1, y1 = random.choices(zones, weights=areas)[0]
    return layout.point(random.uniform(x0, x1), random.uniform(y0, y1))


def tactical_mode(frame: Frame) -> bool:
    """Carte en mode tactique (cases grises unies) : couleurs de portée et de déplacement fiables."""
    region = frame.crop(frame.layout.map_box)[::4, ::4].astype(np.int16)  # une proportion : un pixel sur 16 suffit
    value = region.max(axis=2)
    grey = (value - region.min(axis=2) <= 14) & (value >= 90) & (value <= 215)
    return float(grey.mean()) >= 0.3


def region_changed(before: Frame, after: Frame, box_ref: tuple[float, float, float, float]) -> bool:
    """La zone a changé d'aspect entre deux captures (à quelques pixels de décalage près)."""
    x0, y0, x1, y1 = before.layout.box(*box_ref)
    margin = max(2, round(before.layout.length(4)))
    old = cv2.cvtColor(before.crop((x0, y0, x1, y1)), cv2.COLOR_BGR2GRAY)
    new = cv2.cvtColor(after.crop((max(0, x0 - margin), max(0, y0 - margin), x1 + margin, y1 + margin)), cv2.COLOR_BGR2GRAY)
    if new.shape[0] < old.shape[0] or new.shape[1] < old.shape[1]:
        return True
    if old.std() < 1 and new.std() < 1:
        return abs(float(old.mean()) - float(new.mean())) > 10
    return float(cv2.matchTemplate(new, old, cv2.TM_CCOEFF_NORMED).max()) < 0.9


@lru_cache
def _digit_templates(folder: str) -> list[tuple[int, np.ndarray]]:
    from bot.perception.templates import ASSETS_DIR  # import local : templates importe frame, comme ce module

    digits = []
    for path in sorted((ASSETS_DIR / folder).glob("chiffre_*.png")):
        mask = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255
        digits.append((int(path.stem.split("_")[1]), mask))
    return digits


def _read_digits(frame: Frame, ink: np.ndarray, folder: str) -> int | None:
    """Nombre écrit dans une bulle. `ink` : pixels de la couleur des chiffres ; seules les taches assez hautes
    sont des chiffres (la petite icône dans le coin de la bulle ne l'est pas)."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    marks = sorted((k for k in range(1, count) if stats[k][3] >= frame.layout.length(15)), key=lambda k: stats[k][0])
    if not marks:
        return None
    value = ""
    for k in marks:
        x, y, w, h, _ = stats[k]
        glyph = (labels[y : y + h, x : x + w] == k).astype(np.float32)
        best, best_score = None, 0.6
        for digit, template in _digit_templates(folder):
            resized = cv2.resize(glyph, (template.shape[1], template.shape[0]), interpolation=cv2.INTER_AREA)
            score = float(cv2.matchTemplate(resized, template, cv2.TM_CCOEFF_NORMED)[0, 0])
            if score > best_score:
                best, best_score = digit, score
        if best is None:
            return None
        value += str(best)
    return int(value)


def read_pm(frame: Frame) -> int | None:
    """PM restants, lus dans la bulle verte (modèles des chiffres 0 à 3 : au-delà, None).

    Le vert des chiffres varie d'une capture à l'autre (plus sombre en JPEG) : plusieurs modèles par chiffre."""
    region = frame.crop(frame.layout.box(*PM_BUBBLE)).astype(np.int16)
    b, g, r = region[:, :, 0], region[:, :, 1], region[:, :, 2]
    green = (g > 60) & (g > r + 40) & (g > b + 30)
    return _read_digits(frame, green, "pm")


def _pa_ink(frame: Frame) -> np.ndarray:
    """Pixels bleus des chiffres de la bulle des PA."""
    region = frame.crop(frame.layout.box(*PA_BUBBLE)).astype(np.int16)
    b, g, r = region[:, :, 0], region[:, :, 1], region[:, :, 2]
    return (b > 120) & (b > r + 60) & (b > g + 30)


def read_pa(frame: Frame) -> int | None:
    """PA restants, lus dans la bulle bleue (modèles des chiffres 0 à 6 pris dans les journaux du 27 et du 28/09 ;
    7, 8 et 9 le 04/10, personnages à 10 PA).

    Ils peuvent être plus bas que le maximum dès le début du tour : certains monstres retirent des PA."""
    return _read_digits(frame, _pa_ink(frame), "pa")


# Recouvrement des chiffres (pixels communs / pixels de l'un ou l'autre, au meilleur décalage de 2 px au plus) sous
# lequel le nombre a changé : 1,0 pour un même nombre, 0,90 au plus pour deux nombres différents (04/10, 27 bulles).
PA_SAME_INK = 0.95


def pa_changed(before: Frame, after: Frame) -> bool | None:
    """Le nombre de PA écrit dans la bulle a changé (sort parti) : chiffres lus différents, sinon forme des chiffres
    (encre bleue) différente (PA_SAME_INK). None si les chiffres ne se voient pas sur l'une des captures.

    Pas la ressemblance de toute la bulle (region_changed) : le chiffre n'en est qu'une petite part, et de 9 à 6,
    de 6 à 3 ou de 3 à 0 elle restait au-dessus de 0,9 (04/10 : Tempêtes de Puissance crues pas parties, relancées
    sur la case de l'ennemi déjà mort)."""
    a, b = _pa_ink(before), _pa_ink(after)
    least = before.layout.area(20)
    if a.shape != b.shape or a.sum() < least or b.sum() < least:
        return None
    pa, pb = read_pa(before), read_pa(after)
    if pa is not None and pb is not None:
        return pa != pb
    overlap = 0.0
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            moved = np.roll(b, (dy, dx), axis=(0, 1))
            overlap = max(overlap, float((a & moved).sum()) / max(1, int((a | moved).sum())))
    return overlap < PA_SAME_INK


def hp_ratio(frame: Frame) -> float | None:
    """Part de vie restante (0 à 1), lue au remplissage du cœur : il se vide par le haut, la partie vidée est jaune.

    Vaut quel que soit l'affichage choisi dans le cœur (vie, vie/max, pourcentage). Précision : ±7 % environ
    (mesuré sur 14 captures de journaux : 43 PV -> 0,45, 77 PV -> 0,81, pour 95 PV max)."""
    heart = frame.crop(frame.layout.box(*HEART_ZONE)).astype(np.int16)
    b, g, r = heart[:, :, 0], heart[:, :, 1], heart[:, :, 2]
    red = ((r >= 140) & (g <= 90) & (b <= 90)).sum(axis=1)
    yellow = ((r >= 200) & (g >= 180) & (b <= 190) & (r - b >= 40)).sum(axis=1)
    rows = np.nonzero(red + yellow >= 4)[0]
    if len(rows) < frame.layout.length(20):
        return None  # cœur caché (fenêtre par-dessus...)
    top, bottom = rows[0], rows[-1]
    full = [y for y in rows if red[y] > yellow[y]]
    if not full:
        return 0.0
    return float((bottom - full[0] + 1) / (bottom - top + 1))


def spell_slot_box(slot: int) -> tuple[float, float, float, float]:
    """Icône d'une case de sort (repère de référence)."""
    if slot == 0:
        x, y = SPELL_EXTRA_SLOT
    else:
        row, column = divmod(slot - 1, 7)
        x, y = SPELL_SLOTS_X + column * SPELL_SLOTS_STEP, SPELL_ROWS_Y[row]
    return x - 20, y - 20, x + 20, y + 20


def default_slot_key(slot: int) -> str:
    """Raccourci par défaut d'une case : touches 1 à 7 de la rangée du haut (& é " ' ( - è en AZERTY),
    avec Ctrl pour la deuxième ligne. Le bouton à gauche de la barre (0) n'en a pas."""
    if slot == 0:
        return ""
    if not 1 <= slot <= 14:
        raise ValueError(f"Emplacement de sort invalide : {slot} (0 à 14)")
    row, column = divmod(slot - 1, 7)
    return f"{'ctrl+' if row else ''}{column + 1}"
