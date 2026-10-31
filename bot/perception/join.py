"""Combat en cours sur la carte : l'épée plantée là où il a commencé, et son menu « Combat / Rejoindre ».

Quand un combat commence, les autres joueurs de la carte voient une épée à l'endroit où il a commencé, avec les
fanions de l'équipe (flèches, hache...) et un cercle au sol (violet, cyan...). Un clic sur l'épée ouvre un petit menu :
« Combat » (titre, brun foncé) puis « Rejoindre » (beige). Son infobulle donne le chef de l'équipe (« Jesuisleking(12) »).

L'épée se reconnaît PAR PARTIES QUI VOTENT, jamais par ce qui l'entoure : ni les fanions (selon l'équipe), ni le cercle
au sol (violet ou cyan selon le combat), ni le décor. Les parties, chacune comparée sur ses pixels intérieurs seulement
(masque réduit d'un pixel : les bords se mêlent au fond) :
- le pommeau (anneau orange à cœur sombre), lignes 1 à 14 de assets/epee_combat.png (découpé dans
  tests/fixtures/rejoindre_epee.png, même échelle que les captures de référence) ;
- la garde (orange, pierre sombre), lignes 38 à 54 du même modèle ;
- la lame (21/10), assets/epee_lame.png, découpée dans tests/fixtures/epee_cercle_cyan_fleur.png (lignes 50 à 92,
  colonnes -2 à 16 du coin du modèle de l'épée) : le corps de la lame (peu saturé et clair, ou orange au talon), sa
  plus grande composante, et son contour sombre d'un pixel ; le fond (herbe, cercle cyan) n'en fait pas partie ;
- l'icône des 3 personnages beiges (assets/epee_icone_groupe.png, sans doute l'option « groupe » du combat) qui
  accompagne l'épée : son haut 38 px au-dessus du haut du pommeau, centrée sur la poignée ; ou, quand elle ne tient pas
  au-dessus (épée tout en haut de la carte, 04/10 : icône en y = 97), 108 px sous lui (sous la pointe de la lame) ;
- la poignée orange, entre le pommeau et la garde : une colonne orange étroite (2 à 3 px, bordée de sombre) sur au
  moins 15 lignes sur 24 (couleur, donc quel que soit le fond).
Les cartes de scores sont calculées une fois par partie sur la zone cherchée. Chaque pic d'une partie au-dessus de son
seuil propose une origine (le coin haut-gauche du modèle de l'épée) ; pour chaque origine, on relit le score des autres
parties à leur place, à 3 px près. Une partie au-dessus de son seuil est un vote (0,70 pour le pommeau, la garde et la
lame ; 0,74 pour l'icône), la poignée orange aussi. L'épée est prise avec AU MOINS 2 VOTES : une partie seule ne suffit
jamais (la lame atteint 0,74 sans épée, sur des pierres claires).

Pourquoi (20/10, journaux 203456, 205623, 211515) : sur 25 captures où l'épée se voyait, l'ancienne règle (pommeau ou
garde à 0,75 ET poignée orange, ou icône à 0,80 ET poignée) n'en trouvait que 4 ; les 7 suiveurs restaient dehors :
- épée devant un monstre orange (203456, 0011 à 0020) : la poignée se mêle à l'orange du monstre (pas de poignée
  étroite), alors que pommeau 0,79, garde 0,91, lame 0,96 et icône 0,75 sont là ;
- épée tout en haut (211515, 0114 à 0122) : pommeau hors de l'écran, garde sous le nom de la carte (0,20), mais lame
  0,76, icône (sous l'épée) 0,78 et poignée ;
- épée sous la fenêtre « Résultat du combat » du combat précédent (205623, 0248) : seules le bas de la garde (0,72) et
  la lame (0,97) dépassent.
Les votes en trouvent 23 sur 25, sans aucune épée prise à tort sur 251 captures sans combat (journaux et
tests/fixtures) ni sur 12 captures « epee_introuvable » où l'épée était partie. Les 2 manquées sont cachées : la
fenêtre « Résultat du combat » sur l'icône, l'épée éclaircie par la souris (211515, 0118) ; le menu de Flash ouvert
par-dessus (205623, 0247). Le suiveur ferme ses fenêtres et écarte la souris avant de chercher de nouveau
(team/join.py).
Ancien cas couvert : poignée cachée par un monstre debout devant (11/10, un Champ Champ) : icône (0,95) et pommeau
(0,78) font 2 votes.

Refus voulus : l'épée de l'équipe des monstres (en bois, avec un cadenas) n'a pas ce dessin, on ne la rejoint pas ;
l'épée d'un combat qu'on ne peut pas rejoindre (« équipe fermée ou limitée au groupe du joueur principal », le 04/10)
est beige, sans orange : son icône seule (1,0) ne fait qu'un vote.
Seule exception aux 2 votes (25/10) : l'épée coupée par le haut de l'écran, pommeau et garde au-dessus du bord ; il
n'en reste que le bas de la lame et l'icône, dessous. L'icône seule (0,70) suffit alors, dans cette bande étroite en
haut de la carte, et le clic va sur le bout de lame visible (_cut_swords).

Survolée par la souris, l'épée s'éclaircit (0,55) : on écarte la souris si elle ne se voit pas.

Les points de clic sont SEULEMENT sur les parties qui ont voté (21/10 : on ne clique pas sur une partie qu'on ne voit
pas, cachée par un monstre ou par un personnage), dans l'ordre : pommeau (au-dessus des têtes : le 01/10, le clic sur
la garde avait touché un PNJ debout sur l'épée, menu « Parler »), poignée, milieu de la lame, garde en dernier. Ils
doivent tomber sur l'épée visible : dans la carte (pas au-dessus du bord de l'écran), pas sur le texte du nom de la
carte (blanc ombré de noir, perception/maplabel.py ; 04/10, épée sous « Astrub (Champs d'Astrub) ») ; les lignes de la
poignée cachées par ce texte ne comptent pas dans sa vérification.

Le menu ne se reconnaît pas à sa surface beige (une seule entrée beige, trop petite pour find_context_menu) : on y
cherche directement le mot « Rejoindre » (assets/menu_rejoindre.png : 0,99 sur une capture collée et passée en JPEG,
0,54 au plus ailleurs, 0,47 dans le menu « Monstres / Attaquer »).
"""

import math
from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np

from bot.perception.frame import Frame
from bot.perception.layout import Box, Point
from bot.perception.maplabel import LABEL_BOX, text_glyphs
from bot.perception.templates import ASSETS_DIR, match_template

SWORD, BLADE, ICON = "epee_combat.png", "epee_lame.png", "epee_icone_groupe.png"
# Parties du modèle de l'épée (lignes) : pommeau, poignée, garde ; colonne de la poignée.
POMMEL_ROWS, HANDLE_ROWS, GUARD_ROWS, HANDLE_X = (1, 14), (13, 38), (38, 54), 7
BLADE_AT = (-2, 50)  # du coin du modèle de l'épée au coin du modèle de la lame
# Du coin haut-gauche de l'icône à celui du modèle de l'épée (mesuré à 1 px près sur 3 épées) : icône au-dessus du
# pommeau (cas normal) ; icône sous la lame quand l'épée est tout en haut de la carte (04/10 : icône en y = 97), ce
# second cas n'étant envisagé que pour une icône assez haute.
ICON_ABOVE, ICON_BELOW, ICON_BELOW_TOP = (12, 38), (12, -108), 170
# Parties qui votent, comparées à un modèle : où est le coin de leur modèle, du coin de celui de l'épée (l'icône a deux
# places possibles), et leur seuil. 20/10, sur 25 épées et 253 captures sans combat : la lame atteint 0,74 sans épée
# (pierres claires), l'icône 0,72 ; aucune origine n'a 2 parties au-dessus de ces seuils sans épée.
PARTS = ("pommel", "guard", "blade", "icon")
PART_AT = {"pommel": ((0, POMMEL_ROWS[0]),), "guard": ((0, GUARD_ROWS[0]),), "blade": (BLADE_AT,),
           "icon": ((-ICON_ABOVE[0], -ICON_ABOVE[1]), (-ICON_BELOW[0], -ICON_BELOW[1]))}
THRESHOLDS = {"pommel": 0.70, "guard": 0.70, "blade": 0.70, "icon": 0.74}
PART_NAMES = {"pommel": "pommeau", "handle": "poignée", "blade": "lame", "guard": "garde", "icon": "icône"}
VOTES = 2  # parties vues au même endroit (la poignée orange en est une) : jamais une seule
# Sauf l'épée coupée par le haut de l'écran (pommeau et garde au-dessus) : son icône seule, sous la lame, à ce seuil
# (0,72 le 25/10) ; et au moins CUT_BLADE_SEEN px de référence de lame visibles, où l'on clique.
ICON_CUT, CUT_BLADE_SEEN, CUT_BLADE_SPREAD = 0.70, 10, (2, 3)
# Le bout de lame est blanc : autour du clic (demi-étendue en px de référence), une part au moins de pixels presque
# blancs (0,26 sur l'épée du 25/10 ; aucun sur les cases grises du mode tactique, icône à 0,70 par hasard au-dessus).
CUT_WHITE_REACH, CUT_WHITE_MIN, CUT_WHITE_V, CUT_WHITE_S = (8, 12), 0.12, 235, 40
AGREE = 3  # px de référence : écart toléré entre la place d'une partie et son pic
PEAKS = 30  # pics de chaque partie essayés comme origine
ZONE_REACH = (40, 150)  # autour de la zone cherchée (centres des épées) : où sont leurs parties (icône, lame)
HANDLE_MIN = 0.6  # part des lignes de la poignée où l'on voit de l'orange (0,81 à 0,85 sur les épées du 01/10)
# 04/10 : lignes de la poignée visibles au moins (dans la carte, hors du texte du nom de la carte) pour en juger, sur
# 25 ; les 25 se voyaient sur l'épée du 04/10 en haut de l'écran (pommeau coupé, poignée juste au-dessus du texte).
HANDLE_SEEN = 10
# 04/10 : une ligne de poignée a au plus 3 px orange sur 13 autour de sa colonne (1 à 3 sur les épées réelles, en
# JPEG, et dans une fenêtre réduite à 0,8) : sans cela, sous une icône collée au hasard, les boutons orange du haut à
# droite, une fenêtre aux barres orange (7 sur 1 200 essais), puis le bord orange (4 px) d'un portrait de la
# timeline (1 sur 2 000) passaient pour des poignées.
HANDLE_WIDE, HANDLE_THIN = 6, 5.0
SWORD_CLICK = (-1.5, 16)  # du centre du modèle au milieu de la garde (repère de référence)
# Où cliquer, du centre du modèle, dans l'ordre : (partie, décalage, demi-étendue du clic). Pommeau (au-dessus des
# têtes), poignée, milieu de la lame (04/10 : seule partie visible avec la poignée quand l'épée est sous le nom de la
# carte ; 20/10, 205623 : la seule cliquable derrière des personnages), la garde en dernier (cachée par qui est dessus).
SWORD_POINTS = (("pommel", (-3, -20), (3, 3)), ("handle", (-2.5, -5), (1, 4)), ("blade", (-3, 40), (2, 3)),
                ("guard", SWORD_CLICK, (6, 3)))
JOIN_ENTRY, JOIN_SPREAD = "menu_rejoindre.png", (22, 5)
MAX_SWORDS = 5  # plusieurs combats sur la carte (d'autres joueurs) : on garde les meilleurs
TEXT_MARGIN = 2  # autour des lettres du nom de la carte : leur ombre noire


@dataclass(frozen=True)
class Sword:
    center: Point  # centre du modèle (du pommeau à la garde)
    click: Point  # milieu de la garde
    score: float  # la partie vue la plus ressemblante
    points: tuple[tuple[Point, tuple[float, float]], ...] = ()  # (point, demi-étendue) à cliquer, dans l'ordre
    parts: tuple[str, ...] = ()  # parties vues (qui ont voté), dont « handle » pour la poignée orange
    scores: dict[str, float] = field(default_factory=dict, hash=False)  # score de chaque partie à sa place
    point_parts: tuple[str, ...] = ()  # la partie de chaque point de `points`


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


def _template(part: str, scale: float) -> tuple[np.ndarray, np.ndarray]:
    if part == "pommel":
        return _part(SWORD, POMMEL_ROWS, scale)
    if part == "guard":
        return _part(SWORD, GUARD_ROWS, scale)
    name = BLADE if part == "blade" else ICON
    return _part(name, (0, _asset(name).shape[0]), scale)


def sword_orange(image: np.ndarray) -> np.ndarray:
    """Orange du pommeau, de la poignée et de la garde (pas le jaune des toits de chaume : vert plus fort)."""
    b, g, r = (image[:, :, i].astype(np.int16) for i in range(3))
    return (r >= 190) & (g >= 90) & (g <= 175) & (b <= 90) & (r - g >= 50)


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


class _Maps:
    """Cartes de scores de chaque partie sur `zone` (une fois par partie), et score d'une partie pour une épée dont
    le modèle aurait son coin haut-gauche en (x, y)."""

    def __init__(self, frame: Frame, zone: Box) -> None:
        self.frame, self.zone = frame, zone
        layout = frame.layout
        scale = round(layout.scale, 2)
        region = frame.crop(zone)
        self.maps: dict[str, np.ndarray | None] = {}
        self.sizes: dict[str, tuple[int, int]] = {}
        for part in PARTS:
            template, mask = _template(part, scale)
            self.sizes[part] = template.shape[:2]
            if region.shape[0] < template.shape[0] or region.shape[1] < template.shape[1]:
                self.maps[part] = None
                continue
            scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED, mask=mask)
            self.maps[part] = np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0)  # zone unie : division par 0
        self.offsets = {part: [(round(layout.length(dx)), round(layout.length(dy))) for dx, dy in PART_AT[part]]
                        for part in PARTS}
        self.below_top = layout.map_box[1] + layout.length(ICON_BELOW_TOP)
        self.tolerance = max(1, round(layout.length(AGREE)))
        self.text = _LabelText(frame)

    def _allowed(self, part: str, index: int, part_y: int) -> bool:
        """L'icône n'est sous l'épée que si elle ne tient pas au-dessus : épée tout en haut de la carte."""
        return part != "icon" or index == 0 or part_y < self.below_top

    def score(self, part: str, x: int, y: int) -> float:
        """Meilleur score de la partie à sa place pour une épée en (x, y), à quelques pixels près ; -1 hors de la zone."""
        scores = self.maps[part]
        best = -1.0
        if scores is None:
            return best
        t = self.tolerance
        for index, (dx, dy) in enumerate(self.offsets[part]):
            px, py = x + dx - self.zone[0], y + dy - self.zone[1]
            if not self._allowed(part, index, y + dy):
                continue
            x0, y0 = max(0, px - t), max(0, py - t)
            x1, y1 = min(scores.shape[1], px + t + 1), min(scores.shape[0], py + t + 1)
            if x0 < x1 and y0 < y1:
                best = max(best, float(scores[y0:y1, x0:x1].max()))
        return best

    def peaks(self, part: str, threshold: float | None = None) -> list[tuple[float, int, int]]:
        """(score, x, y) : coins du modèle de l'épée proposés par les pics de la partie au-dessus de son seuil (ou de
        `threshold`), du meilleur au moins bon ; un pic par endroit (à la taille du modèle de la partie près)."""
        threshold = THRESHOLDS[part] if threshold is None else threshold
        scores = self.maps[part]
        if scores is None:
            return []
        scores = scores.copy()
        th, tw = self.sizes[part]
        found = []
        for _ in range(PEAKS):
            _, score, _, (px, py) = cv2.minMaxLoc(scores)
            if score < threshold:
                break
            x, y = px + self.zone[0], py + self.zone[1]
            found += [(float(score), x - dx, y - dy) for index, (dx, dy) in enumerate(self.offsets[part])
                      if self._allowed(part, index, y)]
            scores[max(0, py - th) : py + th, max(0, px - tw) : px + tw] = -1.0
        return found

    def best(self, part: str) -> float:
        """Meilleur score de la partie dans la zone (journal : pourquoi l'épée n'est pas vue)."""
        scores = self.maps[part]
        return -1.0 if scores is None or not scores.size else float(scores.max())

    def handle(self, x: int, y: int) -> bool:
        return _handle_seen(self.frame, x, y, self.text)


def _zone(frame: Frame, box: Box) -> Box:
    """Où chercher les parties des épées dont le centre est dans `box` : la carte autour (icône, pointe de la lame)."""
    left, top, right, bottom = frame.layout.map_box
    rx, ry = (round(frame.layout.length(v)) for v in ZONE_REACH)
    return max(left, box[0] - rx), max(top, box[1] - ry), min(right, box[2] + rx), min(bottom, box[3] + ry)


def _visible(frame: Frame, point: Point, spread: tuple[float, float], text: _LabelText | None) -> bool:
    """Le clic (point et demi-étendue) tombe dans la carte, et pas sur le nom de la carte (`text`)."""
    left, top, right, bottom = frame.layout.map_box
    box = (math.floor(point[0] - spread[0]), math.floor(point[1] - spread[1]), math.ceil(point[0] + spread[0]) + 1,
           math.ceil(point[1] + spread[1]) + 1)
    inside = left <= box[0] and top <= box[1] and box[2] <= right and box[3] <= bottom
    return inside and (text is None or not text.covers(box))


def _white_blade(frame: Frame, point: Point) -> bool:
    """Du blanc de lame autour de `point` (épée coupée par le haut de l'écran)."""
    rx, ry = (round(frame.layout.length(v)) for v in CUT_WHITE_REACH)
    x, y = point
    patch = frame.image[max(0, y - ry) : y + ry + 1, max(0, x - rx) : x + rx + 1]
    if not patch.size:
        return False
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    return float(((hsv[..., 2] > CUT_WHITE_V) & (hsv[..., 1] < CUT_WHITE_S)).mean()) >= CUT_WHITE_MIN


def find_fight_swords(frame: Frame, box: Box | None = None) -> list[Sword]:
    """Épées de combat dont le centre est dans `box` (toute la carte par défaut) : au moins 2 parties vues au même
    endroit (pommeau, garde, lame, icône des 3 personnages, poignée orange), en partant des pics les plus
    ressemblants. Chacune avec ses points de clic, sur les parties vues seulement."""
    layout = frame.layout
    box = box or layout.map_box
    maps = _Maps(frame, _zone(frame, box))
    width, height = (round(layout.length(v)) for v in _asset(SWORD).shape[1::-1])
    origins = sorted((o for part in PARTS for o in maps.peaks(part)), reverse=True)
    # 21/10, Calanques 8,-27 : épée tout en haut, pommeau hors de l'écran, garde et icône (dessous) vues ; son centre
    # au-dessus du bord : écartée, les 7 suiveurs restés dehors. Au bord haut de la carte, centre jusqu'à une demi-épée
    # au-dessus.
    top = box[1] - height / 2 if box[1] <= layout.map_box[1] else box[1]
    swords: list[Sword] = []
    for _, x, y in origins:
        cx, cy = x + width / 2, y + height / 2
        if not (box[0] <= cx < box[2] and top <= cy < box[3]):
            continue
        if any(abs(cx - s.center[0]) + abs(cy - s.center[1]) < height for s in swords):
            continue
        scores = {part: maps.score(part, x, y) for part in PARTS}
        seen = [part for part in PARTS if scores[part] >= THRESHOLDS[part]]
        if maps.handle(x, y):
            seen.append("handle")
        if len(seen) < VOTES:
            continue

        def at(offset: tuple[float, float]) -> Point:
            return round(cx + layout.length(offset[0])), round(cy + layout.length(offset[1]))

        points = [(part, at(offset), layout.spread(*spread)) for part, offset, spread in SWORD_POINTS if part in seen]
        # Les points visibles ; à défaut, ceux de la carte même sous le texte, jamais au-delà du bord de l'écran.
        visible = [p for p in points if _visible(frame, p[1], p[2], maps.text)]
        visible = visible or [p for p in points if _visible(frame, p[1], p[2], None)]
        swords.append(Sword((round(cx), round(cy)), at(SWORD_CLICK), max(scores[p] for p in seen if p in scores),
                            tuple((p, s) for _, p, s in visible), tuple(seen), scores,
                            tuple(part for part, _, _ in visible)))
        if len(swords) == MAX_SWORDS:
            break
    return swords + _cut_swords(frame, maps, box, top, width, height, swords)[: MAX_SWORDS - len(swords)]


def _cut_swords(frame: Frame, maps: "_Maps", box: Box, top: float, width: int, height: int,
                found: list[Sword]) -> list[Sword]:
    """Épées coupées par le haut de l'écran : pommeau et garde au-dessus, seuls le bas de la lame et l'icône (dessous)
    se voient (25/10, journal 171736, Taïnéla 1,-32 : deux combats sur un groupe tout en haut, icône à 0,72, aucune
    autre partie ; les 7 suiveurs restés dehors, Fourboun mort seul). L'icône seule y suffit, dans cette bande étroite
    seulement, et le clic va sur le bout de lame visible."""
    layout = frame.layout
    map_top = layout.map_box[1]
    swords = []
    for score, x, y in maps.peaks("icon", ICON_CUT):  # coins du modèle de l'épée ; icône au-dessus : garde visible
        cx, cy = x + width / 2, y + height / 2
        blade_top = max(map_top, y + layout.length(BLADE_AT[1]))
        blade_bottom = y + layout.length(BLADE_AT[1] + _asset(BLADE).shape[0])
        if y + layout.length(GUARD_ROWS[1]) > map_top or blade_bottom - blade_top < layout.length(CUT_BLADE_SEEN):
            continue  # garde visible (règle des votes) ou presque rien de la lame
        if not (box[0] <= cx < box[2] and top - height <= cy < box[3]):
            continue
        if any(abs(cx - s.center[0]) + abs(cy - s.center[1]) < height for s in found + swords):
            continue
        point = round(x + layout.length(HANDLE_X)), round((blade_top + blade_bottom) / 2)
        spread = layout.spread(*CUT_BLADE_SPREAD)
        if not _visible(frame, point, spread, None) or not _white_blade(frame, point):
            continue
        swords.append(Sword((round(cx), round(cy)), point, score, ((point, spread),), ("icon",), {"icon": score},
                            ("blade",)))
    return swords


def sword_part_scores(frame: Frame, box: Box | None = None) -> dict[str, float]:
    """Meilleur score de chaque partie de l'épée (pic dans la zone de `box`), pour le journal quand elle n'est pas vue."""
    maps = _Maps(frame, _zone(frame, box or frame.layout.map_box))
    return {part: maps.best(part) for part in PARTS}


def find_join_entry(frame: Frame) -> Point | None:
    """Entrée « Rejoindre » du menu ouvert par un clic sur l'épée."""
    found = match_template(frame, JOIN_ENTRY, box=frame.layout.map_box)
    return found[0] if found else None
