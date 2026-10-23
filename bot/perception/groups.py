"""Groupes de monstres hors combat, repérés par leur infobulle.

Une infobulle est un rectangle sombre semi-transparent : « Niveau 14 », une ligne d'étoiles, puis un monstre
par ligne. Elle s'affiche au survol d'un groupe, ou pour tous les groupes tant que la touche Z est enfoncée.
Elle se place au-dessus du groupe, ou en dessous s'il n'y a pas la place ; collée à un bord de la carte,
elle est décalée vers l'intérieur et le groupe n'est plus sous son centre.

Avec Z, les infobulles de groupes proches se chevauchent : on repère donc chacune par son mot « Niveau »
(texte blanc, un par infobulle) plutôt que par son rectangle sombre, puis on compte ses lignes de texte.
Mesures (px de référence) prises sur les captures de tests/fixtures et des journaux du 27/09.

Le groupe se tient au centre d'une case (réseau de grid.py) et son infobulle se place par rapport à cette case, au
pixel près (03/10 : 94 infobulles de 26 captures Z) : on en déduit de quel côté de son infobulle est le groupe
(under_its_group) et si un bord l'a repoussée (elle n'est plus centrée sur une colonne de cases).
"""

import re
from dataclasses import dataclass

import cv2
import numpy as np

from bot.perception.frame import Frame
from bot.perception.layout import SCENE_LEFT, Box, Layout
from bot.perception.templates import load_template

NIVEAU = "niveau.png"  # mot « Niveau » découpé dans une infobulle
MAP_TITLE_ZONE = (SCENE_LEFT, 0, 900, 95)  # nom de la carte et coordonnées, écrits en blanc
TEXT_ABOVE = 23  # du centre du mot « Niveau » au haut de l'infobulle
FIRST_MONSTER, LINE_HEIGHT, TEXT_BELOW = 55, 22, 26  # lignes de monstres, puis marge du bas
LINE_HALF_WIDTH = 60  # bande autour du centre où chercher le texte d'une ligne
# Une ligne de monstre est du texte SUR le fond sombre de l'infobulle. Le 03/10, sur le sable des Calanques d'Astrub
# (jaune clair, pris pour du texte), 16 infobulles sur 17 étaient lues à 6 ou 8 monstres au lieu de 1 à 3 : leur bas
# 100 à 200 px trop bas, survols et clics loin des groupes (et 2 sur 22 en Incarnam : toit de paille, autre infobulle).
# Mesuré sur 90 infobulles de 44 captures Z (03/10) : une vraie ligne a 286 à 546 px de texte, sur 8 à 10 des 10
# rangées de la bande, et 36 à 67 % de fond sombre ; le sable sous l'infobulle, 0 à 20 % de sombre ; la marge du bas
# (bord de l'infobulle dans la bande), 134 px de texte au plus, sur 5 rangées au plus.
LINE_TEXT, LINE_ROWS, LINE_DARK = 150, 0.6, 0.3  # px de texte ; part des rangées avec du texte ; part de fond sombre
EDGE_MARGIN = 20  # infobulle à moins de 20 px d'un bord de la carte : repoussée par le jeu, le groupe est plus au bord
# Infobulle et case de son groupe, mesuré le 03/10 (94 infobulles, écarts de 1 px au plus) : son bas est 63,8 px
# au-dessus du centre de la case ; en haut de la carte, quand elle passe sous le groupe, son haut est 19,7 px en
# dessous. Le jeu ne la met au-dessus que si son haut reste à 54 px au moins du haut de la carte (vu : 55 accepté,
# 52,5 refusé) ; 6 px de marge.
BOTTOM_TO_CELL, CELL_TO_TOP = 63.8, 19.7
TOP_ROOM = 54 + 6
ROW_FIT = 4  # case du groupe à moins de 4 px d'une rangée : placement plausible (vu : 1 px ; l'autre : 7 px ou plus)
# Titre lu 2 px à gauche de la colonne de cases du groupe (écart de 1 px au plus, 03/10) ; à plus de 5 px d'une
# colonne, l'infobulle a été repoussée par un bord (vu : 11 à 20 px).
TITLE_SHIFT, OFF_COLUMN = -2, 5
# Bout d'infobulle caché près d'un bord : portraits de l'équipe le long du bord droit (55 px de large, 03/10). Le
# 03/10, deux infobulles repoussées par ce bord s'y arrêtaient à 53 px de lui : pas vues comme collées au bord, leur
# groupe cherché sous leur centre (trouvé au 5e survol pour l'une, jamais pour l'autre).
HIDDEN_EDGE = 70


@dataclass(frozen=True)
class GroupTooltip:
    x: float  # centre horizontal du titre « Niveau N » (= de l'infobulle)
    title_y: float  # centre vertical du titre
    top: float
    bottom: float
    left: float
    right: float
    monsters: int
    clipped: bool  # collée au bord gauche ou droit de la carte (ou décentrée, bout caché) : décalée par le jeu

    @property
    def box(self) -> Box:
        return round(self.left), round(self.top), round(self.right), round(self.bottom)

    def overlaps(self, other: "GroupTooltip", margin: float = 10) -> bool:
        """Se chevauchent ou se touchent presque : leurs groupes sont trop proches pour survoler sans erreur."""
        return (self.left - margin < other.right and other.left - margin < self.right
                and self.top - margin < other.bottom and other.top - margin < self.bottom)


def _white(image: np.ndarray) -> np.ndarray:
    return (image.min(axis=2) >= 200).astype(np.float32)


def _bright_text(image: np.ndarray) -> np.ndarray:
    """Texte blanc ou jaune clair (titre, noms des monstres)."""
    return (image[:, :, 2] >= 200) & (image[:, :, 1] >= 200)


def _dark(image: np.ndarray) -> np.ndarray:
    return image.max(axis=2) <= 90


def _title_words(frame: Frame) -> list[tuple[int, int, int, int]]:
    """Mots « Niveau » trouvés (x, y, largeur, hauteur), du plus net au moins net."""
    layout = frame.layout
    template = _white(load_template(NIVEAU))
    if abs(layout.scale - 1) > 0.02:
        template = cv2.resize(template, None, fx=layout.scale, fy=layout.scale, interpolation=cv2.INTER_AREA)
    box = layout.map_box
    region = frame.crop(box).copy()
    tx0, ty0, tx1, ty1 = layout.box(*MAP_TITLE_ZONE)
    region[ty0 - box[1] : ty1 - box[1], tx0 - box[0] : tx1 - box[0]] = 0  # pas une infobulle
    th, tw = template.shape
    scores = cv2.matchTemplate(_white(region), template, cv2.TM_CCOEFF_NORMED)
    words = []
    while True:
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        if score < 0.65:
            return words
        words.append((box[0] + x, box[1] + y, tw, th))
        scores[max(0, y - th) : y + th, max(0, x - tw) : x + tw] = -1


def _tooltip_from_word(frame: Frame, word: tuple[int, int, int, int], stop_y: float) -> GroupTooltip:
    """`stop_y` : titre de l'infobulle suivante en dessous (chevauchement) ; ses lignes ne sont pas les nôtres."""
    layout = frame.layout
    s = layout.scale
    wx, wy, ww, wh = word
    title_y = wy + wh / 2
    # Fin du titre « Niveau N » : texte blanc à droite du mot, jusqu'à un blanc de plus de 12 px.
    row = _white(frame.crop((wx, wy, min(layout.width, wx + ww + round(80 * s)), wy + wh))).sum(axis=0) > 0
    right_end, gap = ww, 0
    for i in range(ww, len(row)):
        gap = 0 if row[i] else gap + 1
        if row[i]:
            right_end = i
        if gap > 12 * s:
            break
    x = wx + (right_end + 1) / 2
    # Une ligne par monstre, tant qu'il y a du texte à l'endroit attendu.
    monsters = 0
    while monsters < 8:
        y = round(title_y + (FIRST_MONSTER + LINE_HEIGHT * monsters) * s)
        if y + 5 * s >= stop_y or not _monster_line(frame, x, y):
            break
        monsters += 1
    top = title_y - TEXT_ABOVE * s
    bottom = title_y + (FIRST_MONSTER - LINE_HEIGHT + LINE_HEIGHT * max(monsters, 1) + TEXT_BELOW) * s
    # Bords : fond sombre de part et d'autre du titre, sur une ligne sans texte (juste sous le haut).
    map_left, _, map_right, _ = layout.map_box
    row_y = round(top + 5 * s)  # une ligne, quelle que soit l'échelle (à 1008 px de haut, l'arrondi en donnait 0)
    dark_row = _dark(frame.crop((map_left, row_y, map_right, row_y + 1)))[0]
    left = right = min(len(dark_row) - 1, max(0, round(x) - map_left))
    while left > 0 and dark_row[left - 1]:
        left -= 1
    while right < len(dark_row) - 1 and dark_row[right + 1]:
        right += 1
    left, right = map_left + left, map_left + right
    # Le jeu garde un peu de marge : le 01/10, une infobulle repoussée par le bord droit s'arrêtait à 16 px de lui.
    clipped = left <= map_left + EDGE_MARGIN * s or right >= map_right - EDGE_MARGIN * s
    if not clipped and abs(_column_offset(x, frame)) > OFF_COLUMN * s:  # décentrée : repoussée, bout caché ?
        clipped = left <= map_left + HIDDEN_EDGE * s or right >= map_right - HIDDEN_EDGE * s
    return GroupTooltip(x, title_y, top, bottom, left, right, monsters, clipped)


def _monster_line(frame: Frame, x: float, y: int) -> bool:
    """Ligne de texte centrée en (x, y) dans le fond sombre d'une infobulle (pas du sable clair sous elle)."""
    s = frame.layout.scale
    half_h, half_w = round(5 * s), round(LINE_HALF_WIDTH * s)
    band = frame.crop((max(0, round(x) - half_w), max(0, y - half_h), round(x) + half_w, y + half_h))
    if band.size == 0:
        return False
    text = _bright_text(band)
    rows = (text.sum(axis=1) >= 2).mean()
    return text.sum() >= LINE_TEXT * s * s and rows >= LINE_ROWS and _dark(band).mean() >= LINE_DARK


def _column_offset(x: float, frame: Frame) -> float:
    grid = frame.grid
    column = (x - frame.layout.length(TITLE_SHIFT) - grid.x0) / grid.half_w
    return (column - round(column)) * grid.half_w


def column_offset(tooltip: GroupTooltip, frame: Frame) -> float:
    """Écart (px, signé) entre le centre de l'infobulle et la colonne de cases la plus proche : presque nul pour une
    infobulle centrée sur son groupe ; au-delà de OFF_COLUMN, le jeu l'a repoussée d'un bord (ou le groupe marchait)."""
    return _column_offset(tooltip.x, frame)


def can_be_under_its_group(tooltip: GroupTooltip, layout: Layout) -> bool:
    """Le jeu a pu mettre l'infobulle sous son groupe : au-dessus de lui, son haut aurait été trop près du haut de la
    carte. Le haut de la carte seulement : rien de tel n'a été vu aux autres bords (03/10)."""
    s = layout.scale
    top_if_above = tooltip.top - (CELL_TO_TOP + BOTTOM_TO_CELL) * s - (tooltip.bottom - tooltip.top)
    return top_if_above < TOP_ROOM * s


def under_its_group(tooltip: GroupTooltip, frame: Frame) -> bool | None:
    """Infobulle sous son groupe (True) ou au-dessus de lui (False), d'après la case où tomberait le groupe dans
    chaque cas : une seule tombe sur une rangée du réseau. None si aucune ou les deux (relief de la carte, groupe
    en marche, lignes de monstres mal comptées sous une autre infobulle) : à vérifier en survolant."""
    grid = frame.grid
    s = frame.layout.scale

    def off_row(y: float) -> float:
        row = (y - grid.y0) / grid.half_h
        return abs(row - round(row)) * grid.half_h / s

    if_above = off_row(tooltip.bottom + BOTTOM_TO_CELL * s)
    if_under = off_row(tooltip.top - CELL_TO_TOP * s)
    if (if_above <= ROW_FIT) == (if_under <= ROW_FIT):
        return None
    return if_under <= ROW_FIT


LEVEL_ZOOM = 3  # titre « Niveau N » agrandi pour l'OCR de Windows (lu « Niveau 7 » sur les captures du 23/10)


def read_group_level(frame: Frame, tooltip: GroupTooltip) -> int | None:
    """Niveau total du groupe, lu dans le titre « Niveau N » de son infobulle ; None si l'OCR ne le lit pas."""
    from bot.perception.chat import ocr_available, ocr_lines  # OCR de Windows (chat.py)

    if not ocr_available():
        return None
    x0, x1 = round(tooltip.left), round(tooltip.right)
    y0, y1 = round(tooltip.top), round(2 * tooltip.title_y - tooltip.top + 4)
    crop = frame.image[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0:
        return None
    for line in ocr_lines(cv2.resize(crop, None, fx=LEVEL_ZOOM, fy=LEVEL_ZOOM, interpolation=cv2.INTER_CUBIC)):
        match = re.search(r"Niveau\s*(\d+)", line, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def find_group_tooltips(frame: Frame) -> list[GroupTooltip]:
    words = _title_words(frame)
    tooltips = []
    for word in words:
        x, y, w, h = word
        # Titre d'une autre infobulle plus bas, dans notre bande de texte : nos lignes s'arrêtent avant.
        below = [oy + oh / 2 - frame.layout.length(12) for ox, oy, ow, oh in words
                 if oy > y and abs((ox + ow / 2) - (x + w / 2)) < frame.layout.length(2 * LINE_HALF_WIDTH)]
        tooltips.append(_tooltip_from_word(frame, word, min(below, default=float("inf"))))
    return tooltips
