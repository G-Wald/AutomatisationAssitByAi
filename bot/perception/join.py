"""Combat en cours sur la carte : l'épée plantée là où il a commencé, et son menu « Combat / Rejoindre ».

Quand un combat commence, les autres joueurs de la carte voient une épée à l'endroit où il a commencé, avec les
fanions de l'équipe (flèches, hache...) et un cercle au sol (violet, cyan...). Un clic sur l'épée ouvre un petit menu :
« Combat » (titre, brun foncé) puis « Rejoindre » (beige). Son infobulle donne le chef de l'équipe (« Jesuisleking(12) »).

L'épée se reconnaît PAR PARTIES (assets/epee_combat.png, découpé dans tests/fixtures/rejoindre_epee.png, même échelle
que les captures de référence), jamais par ce qui l'entoure : ni les fanions (selon l'équipe), ni le cercle au sol (violet
ou cyan selon le combat), ni le décor. Chaque partie est comparée sur ses pixels intérieurs seulement (masque réduit
d'un pixel : les bords se mêlent au fond) :
- le pommeau (anneau orange à cœur sombre) : 0,85 à 1,0 sur les épées réelles du 01/10 (PNJ debout dessus, fleur
  blanche derrière), 0,60 au plus sur 150 captures sans épée ;
- la garde (orange, pierre sombre) : 0,93 à 1,0, quand le pommeau est caché ;
et entre les deux, la poignée : une colonne orange d'au moins 15 lignes sur 24 (couleur, donc quel que soit le fond),
présente sur toutes les épées, absente sous tous les faux pommeaux. Le 01/10, le modèle entier (pommeau + poignée + garde
+ début des fanions) ne donnait plus que 0,57 sur une épée à cercle cyan devant une fleur.
Survolée par la souris, l'épée s'éclaircit (0,55) : on écarte la souris si elle ne se voit pas.
Quelqu'un debout sur l'épée (le 01/10, un PNJ) en cache la garde : on clique d'abord sur le pommeau, au-dessus des
têtes (le clic sur la garde avait touché le PNJ : menu « Parler »). L'épée de l'équipe des monstres (en bois, avec un
cadenas) n'a pas ce dessin : elle n'est pas reconnue, et c'est voulu, on ne la rejoint pas. L'épée d'un combat qu'on
ne peut pas rejoindre (« équipe fermée ou limitée au groupe du joueur principal », le 04/10) est beige, sans orange :
pas reconnue non plus.

Épée tout en haut de la carte (04/10, groupe attaqué en y = 57) : pommeau coupé par le bord de l'écran, garde sous le
texte blanc du nom de la carte (pommeau 0,54, garde 0,41) ; aucun des 3 suiveurs n'avait rejoint. Repère de secours :
la petite icône des 3 personnages beiges (assets/epee_icone_groupe.png, sans doute l'option « groupe » du combat)
qui accompagne l'épée : son haut 38 px au-dessus du haut du pommeau, centrée sur la poignée ; ou, quand elle ne tient
pas au-dessus, 108 px sous lui (sous la pointe de la lame). Elle donne 0,87 à 1,0 sur les 18 captures où elle est
(épées orange et beiges), 0,72 au plus sur 544 captures sans elle. Une épée n'y est prise que si sa poignée orange
(étroite) est là où l'icône la place : aucune épée prise à tort sur 854 captures sans épée orange (journaux du 04/10,
dont les 10 à épée beige et icône), ni sur 4 000 icônes collées au hasard sur 50 d'entre elles.
Poignée cachée par un monstre debout devant l'épée (11/10 : un Champ Champ ; les 3 suiveurs ne l'ont pas trouvée) :
l'icône (0,95) et le pommeau (0,78) au même endroit suffisent, sans la poignée. Deux repères indépendants qui tombent
au même pixel près : jamais vu sans épée (pommeau 0,60 et icône 0,72 au plus sans elle).
Les points de clic doivent tomber sur l'épée visible : dans la carte (pas au-dessus du bord de l'écran), pas sur le
texte du nom de la carte (blanc ombré de noir, perception/maplabel.py) ; dans l'ordre pommeau, poignée, garde, puis
le milieu de la lame (entre les deux lignes du nom de la carte quand l'épée est tout en haut). Les lignes de la
poignée cachées par ce texte ne comptent pas dans sa vérification.

Le menu ne se reconnaît pas à sa surface beige (une seule entrée beige, trop petite pour find_context_menu) : on y
cherche directement le mot « Rejoindre » (assets/menu_rejoindre.png : 0,99 sur une capture collée et passée en JPEG,
0,54 au plus ailleurs, 0,47 dans le menu « Monstres / Attaquer »).
"""

import math
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from bot.perception.frame import Frame
from bot.perception.layout import Box, Point
from bot.perception.maplabel import LABEL_BOX, text_glyphs
from bot.perception.templates import ASSETS_DIR, match_template

SWORD = "epee_combat.png"
# Parties du modèle (lignes) : pommeau, poignée, garde ; colonne de la poignée.
POMMEL_ROWS, HANDLE_ROWS, GUARD_ROWS, HANDLE_X = (1, 14), (13, 38), (38, 54), 7
PART_THRESHOLD = 0.75
HANDLE_MIN = 0.6  # part des lignes de la poignée où l'on voit de l'orange (0,81 à 0,85 sur les épées du 01/10)
# 04/10 : lignes de la poignée visibles au moins (dans la carte, hors du texte du nom de la carte) pour en juger, sur
# 25 ; les 25 se voyaient sur l'épée du 04/10 en haut de l'écran (pommeau coupé, poignée juste au-dessus du texte).
HANDLE_SEEN = 10
# 04/10 : une ligne de poignée a au plus 3 px orange sur 13 autour de sa colonne (1 à 3 sur les épées réelles, en
# JPEG, et dans une fenêtre réduite à 0,8) : sans cela, sous une icône collée au hasard, les boutons orange du haut à
# droite, une fenêtre aux barres orange (7 sur 1 200 essais), puis le bord orange (4 px) d'un portrait de la
# timeline (1 sur 2 000) passaient pour des poignées.
HANDLE_WIDE, HANDLE_THIN = 6, 3.5
SWORD_CLICK = (-1.5, 16)  # du centre du modèle au milieu de la garde (repère de référence)
# Où cliquer, dans l'ordre, du centre du modèle : pommeau (au-dessus des têtes), poignée, garde, puis (04/10) le milieu
# de la lame, sous la garde : seule partie visible avec la poignée quand l'épée est sous le nom de la carte (pommeau
# hors de l'écran, garde sous le texte) ; demi-étendue du clic.
SWORD_POINTS = (((-3, -20), (3, 3)), ((-2.5, -5), (1, 4)), (SWORD_CLICK, (6, 3)), ((-3, 40), (2, 3)))
SWORD_SPREAD = (6, 3)  # la garde fait une dizaine de pixels de haut
JOIN_ENTRY, JOIN_SPREAD = "menu_rejoindre.png", (22, 5)
MAX_SWORDS = 5  # plusieurs combats sur la carte (d'autres joueurs) : on garde les meilleurs
# 04/10 : icône des 3 personnages qui accompagne l'épée (repère de secours, voir plus haut). Seuil : 0,87 à 1,0 sur
# les 18 captures où elle est, 0,72 au plus sur 544 captures sans elle (journaux du 04/10, tests/fixtures, captures).
ICON, ICON_THRESHOLD = "epee_icone_groupe.png", 0.8
# Du coin haut-gauche de l'icône à celui du modèle de l'épée (mesuré à 1 px près sur 3 épées) : icône au-dessus du
# pommeau (cas normal) ; icône sous la lame quand l'épée est tout en haut de la carte (04/10 : icône en y = 97), ce
# second cas n'étant envisagé que pour une icône assez haute.
ICON_ABOVE, ICON_BELOW, ICON_BELOW_TOP = (12, 38), (12, -108), 170
ICON_REACH = (30, 130)  # autour de la zone de recherche de l'épée : où chercher son icône (au-dessus ou en dessous)
TEXT_MARGIN = 2  # autour des lettres du nom de la carte : leur ombre noire
AGREE = 3  # icône et pommeau (ou garde) au même endroit : écart toléré sur le coin du modèle (repère de référence)


@dataclass(frozen=True)
class Sword:
    center: Point  # centre du modèle (du pommeau à la garde)
    click: Point  # milieu de la garde
    score: float
    points: tuple[tuple[Point, tuple[float, float]], ...] = ()  # (point, demi-étendue) à cliquer, dans l'ordre


@lru_cache
def _asset(name: str) -> np.ndarray:
    path = ASSETS_DIR / name
    image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED) if path.is_file() else None
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        raise FileNotFoundError(f"Modèle avec transparence introuvable : {path}")
    return image


@lru_cache
def _part(name: str, rows: tuple[int, int], scale: float) -> tuple[np.ndarray, np.ndarray]:
    """Lignes `rows` du modèle (BGR) et leur masque : pixels opaques, sauf les bords (mêlés au fond), à l'échelle."""
    image = _asset(name)[rows[0] : rows[1]]
    template = np.ascontiguousarray(image[:, :, :3])
    mask = cv2.erode((image[:, :, 3] > 0).astype(np.uint8), np.ones((2, 2), np.uint8)) * 255
    if abs(scale - 1) > 0.02:
        template = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        mask = cv2.resize(mask, (template.shape[1], template.shape[0]), interpolation=cv2.INTER_NEAREST)
    return template, mask


def sword_orange(image: np.ndarray) -> np.ndarray:
    """Orange du pommeau, de la poignée et de la garde (pas le jaune des toits de chaume : vert plus fort)."""
    b, g, r = (image[:, :, i].astype(np.int16) for i in range(3))
    return (r >= 190) & (g >= 90) & (g <= 175) & (b <= 90) & (r - g >= 50)


def _matches(frame: Frame, box: Box, template: np.ndarray, mask: np.ndarray, threshold: float,
             limit: int) -> list[tuple[float, int, int]]:
    """(score, x, y) du coin haut-gauche du modèle, pour chaque endroit de `box` où il ressemble, du meilleur au moins
    bon ; un seul par endroit (à la taille du modèle près)."""
    region = frame.crop(box)
    th, tw = template.shape[:2]
    if region.shape[0] < th or region.shape[1] < tw:
        return []
    scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED, mask=mask)
    scores = np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0)  # zone unie : division par zéro
    found = []
    while len(found) < limit:
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        if score < threshold:
            break
        found.append((float(score), box[0] + x, box[1] + y))
        scores[max(0, y - th) : y + th, max(0, x - tw) : x + tw] = -1.0  # la même chose, à quelques pixels près
    return found


def _part_matches(frame: Frame, box: Box, rows: tuple[int, int]) -> list[tuple[float, int, int]]:
    """(score, x, y) du coin haut-gauche du MODÈLE ENTIER, pour chaque endroit où la partie `rows` ressemble."""
    layout = frame.layout
    template, mask = _part(SWORD, rows, round(layout.scale, 2))
    top = round(layout.length(rows[0]))
    return [(score, x, y - top) for score, x, y in _matches(frame, box, template, mask, PART_THRESHOLD, 3 * MAX_SWORDS)]


def _icon_matches(frame: Frame, box: Box) -> list[tuple[float, int, int]]:
    """(score, x, y) du coin haut-gauche de l'icône des 3 personnages, près de `box` (elle est au-dessus de l'épée,
    ou en dessous)."""
    layout = frame.layout
    template, mask = _part(ICON, (0, _asset(ICON).shape[0]), round(layout.scale, 2))
    left, top, right, bottom = layout.map_box
    rx, ry = (round(layout.length(v)) for v in ICON_REACH)
    near = (max(left, box[0] - rx), max(top, box[1] - ry), min(right, box[2] + rx), min(bottom, box[3] + ry))
    return _matches(frame, near, template, mask, ICON_THRESHOLD, 2 * MAX_SWORDS)


def _icon_candidates(frame: Frame, box: Box) -> list[tuple[float, int, int]]:
    """(score de l'icône, x, y) du coin haut-gauche du modèle de l'épée, là où chaque icône la place."""
    layout = frame.layout
    found = []
    for score, x, y in _icon_matches(frame, box):
        offsets = [ICON_ABOVE]
        if y - layout.map_box[1] < layout.length(ICON_BELOW_TOP):
            offsets.append(ICON_BELOW)  # épée trop haute pour avoir son icône au-dessus d'elle
        found += [(score, x + round(layout.length(dx)), y + round(layout.length(dy))) for dx, dy in offsets]
    return found


class _LabelText:
    """Lettres du nom de la carte (blanc ombré de noir, en haut à gauche de la carte), élargies de leur ombre : elles
    cachent ce qui est derrière, on ne vise pas dessus."""

    def __init__(self, frame: Frame) -> None:
        self.box = frame.layout.box(*LABEL_BOX)
        x0, y0, x1, y1 = self.box
        mask = np.zeros((max(0, y1 - y0), max(0, x1 - x0)), np.uint8)
        for g in text_glyphs(frame):
            mask[g.y - y0 : g.bottom - y0, g.x - x0 : g.right - x0] = 1
        if mask.size:
            grow = max(1, round(frame.layout.length(TEXT_MARGIN)))
            mask = cv2.dilate(mask, np.ones((2 * grow + 1, 2 * grow + 1), np.uint8))
        self.mask = mask > 0

    def rows(self, top: int, bottom: int, left: int, right: int) -> np.ndarray:
        """Pour chaque ligne de `top` à `bottom` (exclu) : du texte entre `left` et `right` (exclu) ?"""
        hidden = np.zeros(max(0, bottom - top), bool)
        x0, y0, x1, y1 = self.box
        t, b, l, r = max(top, y0), min(bottom, y1), max(left, x0), min(right, x1)
        if t < b and l < r:
            hidden[t - top : b - top] = self.mask[t - y0 : b - y0, l - x0 : r - x0].any(axis=1)
        return hidden

    def covers(self, box: Box) -> bool:
        return bool(self.rows(box[1], box[3], box[0], box[2]).any())


def _handle_seen(frame: Frame, x: int, y: int, text: _LabelText) -> bool:
    """Poignée orange sous le pommeau, au-dessus de la garde, pour une épée dont le modèle commence en (x, y) : jugée
    sur les lignes visibles (dans la carte, pas cachées par le nom de la carte). Une ligne de poignée : de l'orange
    près de la colonne de la poignée, mais étroit (2 à 3 px, bordé de sombre), pas une plage orange."""
    layout = frame.layout
    x0, y0, x1, y1 = layout.map_box
    cx = x + round(layout.length(HANDLE_X))
    half, wide = max(2, round(layout.length(3))), max(4, round(layout.length(HANDLE_WIDE)))
    top, bottom = (min(y1, max(y0, y + round(layout.length(v)))) for v in HANDLE_ROWS)
    left, right = max(x0, cx - wide), min(x1, cx + wide + 1)
    if bottom <= top or right <= left:
        return False
    orange = sword_orange(frame.image[top:bottom, left:right])
    near = orange[:, max(0, cx - half - left) : max(0, cx + half + 1 - left)].any(axis=1)
    handle = near & (orange.sum(axis=1) <= layout.length(HANDLE_THIN))
    seen = handle | ~text.rows(top, bottom, cx - half, cx + half + 1)
    return seen.sum() >= layout.length(HANDLE_SEEN) and handle.sum() >= HANDLE_MIN * seen.sum()


def _visible(frame: Frame, point: Point, spread: tuple[float, float], text: _LabelText | None) -> bool:
    """Le clic (point et demi-étendue) tombe dans la carte, et pas sur le nom de la carte (`text`)."""
    left, top, right, bottom = frame.layout.map_box
    box = (math.floor(point[0] - spread[0]), math.floor(point[1] - spread[1]), math.ceil(point[0] + spread[0]) + 1,
           math.ceil(point[1] + spread[1]) + 1)
    inside = left <= box[0] and top <= box[1] and box[2] <= right and box[3] <= bottom
    return inside and (text is None or not text.covers(box))


def find_fight_swords(frame: Frame, box: Box | None = None) -> list[Sword]:
    """Épées de combat visibles sur la carte : un pommeau ou une garde (de la plus ressemblante à la moins
    ressemblante), sinon (04/10) l'icône des 3 personnages qui les accompagne ; et la poignée orange là où il faut."""
    layout = frame.layout
    box = box or layout.map_box
    width, height = (round(layout.length(v)) for v in _asset(SWORD).shape[1::-1])
    text = _LabelText(frame)
    parts = sorted(_part_matches(frame, box, POMMEL_ROWS) + _part_matches(frame, box, GUARD_ROWS), reverse=True)
    icons = sorted(_icon_candidates(frame, box), reverse=True)
    tolerance = layout.length(AGREE)

    def agree(x: int, y: int, found: list[tuple[float, int, int]]) -> bool:
        return any(abs(x - fx) <= tolerance and abs(y - fy) <= tolerance for _, fx, fy in found)

    swords: list[Sword] = []
    for score, x, y in parts + icons:
        cx, cy = x + width / 2, y + height / 2
        if any(abs(cx - s.center[0]) + abs(cy - s.center[1]) < height for s in swords):
            continue
        if not _handle_seen(frame, x, y, text) and not (agree(x, y, parts) and agree(x, y, icons)):
            continue

        def at(offset: tuple[float, float]) -> Point:
            return round(cx + layout.length(offset[0])), round(cy + layout.length(offset[1]))

        points = [(at(offset), layout.spread(*spread)) for offset, spread in SWORD_POINTS]
        # Les points visibles ; à défaut, ceux de la carte même sous le texte, jamais au-delà du bord de l'écran.
        visible = [(p, s) for p, s in points if _visible(frame, p, s, text)]
        visible = visible or [(p, s) for p, s in points if _visible(frame, p, s, None)]
        swords.append(Sword((round(cx), round(cy)), at(SWORD_CLICK), score, tuple(visible)))
        if len(swords) == MAX_SWORDS:
            break
    return swords


def find_join_entry(frame: Frame) -> Point | None:
    """Entrée « Rejoindre » du menu ouvert par un clic sur l'épée."""
    found = match_template(frame, JOIN_ENTRY, box=frame.layout.map_box)
    return found[0] if found else None
