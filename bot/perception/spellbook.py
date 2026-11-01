"""Fenêtre « Tes sorts » (bouton baguette de la barre d'icônes) : niveau, coût en PA, portée et icône de chaque sort.

Lecture des lignes « 4 PA   1-10 PO » : les caractères sont séparés par des colonnes vides et regroupés par
les espaces en jetons [PA] [« PA »] [portée] [« PO »] ; seuls les chiffres et le tiret sont reconnus, par
comparaison avec des modèles découpés dans tests/fixtures/fenetre_sorts.png (chiffre_5_2 et chiffre_5_niveau :
dans la fenêtre de l'Enutrof du 03/10, tests/fixtures/fenetre_sorts_enutrof_03_10.png). Les chiffres 6, 7 et 9
n'y figurent pas encore : un sort qui en contient est signalé illisible (sa portée sera mesurée en combat).

La portée minimale n'est écrite que si elle n'est pas 0 : « 4 PO » = 0 à 4 (Pelle Fantomatique, Tir Critique, Tir
Éloigné « 0 PO »), « 1-1 PO » = 1 à 1 (Sac Animé) ; vérifié sur les 03/10 avec la référence Solomonk.

Le niveau « Niv. 2 », à droite de la ligne du nom, est lu de la même façon (dernier jeton de la ligne) : il suffit,
avec la référence Solomonk (config/sorts/solomonk.json), à connaître tout le sort sans ouvrir sa fiche.

Les icônes de la fenêtre sont les mêmes dessins que celles de la barre de sorts : c'est ce qui relie un sort
de la liste à sa case dans la barre.

La liste montre 10 sorts ; une classe en a 22 (04/10 : personnages plus haut niveau, sorts au-delà de la 10e
ligne) : elle défile (molette), et sa barre de défilement dit où on en est. La poignée (sombre, x 540 à 557)
commence à y = 193 en haut de la liste et couvre ~45 % du rail (fenêtres du 28/09 et du 03/10) ; flèche du bas en
y ~704. Les pages sont recalées entre elles par les icônes des lignes (page_shift) : on suppose que la liste défile
ligne par ligne, chaque ligne à sa place habituelle (à vérifier sur les captures « fenetre_sorts_page_N »).
"""

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from bot.perception.frame import Frame
from bot.perception.layout import Point
from bot.perception.templates import ASSETS_DIR, match_template
from bot.perception.ui import spell_slot_box

TITLE, CLOSE, BUTTON = "sorts/titre.png", "sorts/fermer.png", "sorts/bouton_sorts.png"
BUTTON_ZONE = (1430, 790, 1918, 850)  # barre d'icônes au-dessus de la barre de sorts
# Géométrie de la fenêtre en pixels de la capture de référence, depuis son coin haut-gauche.
WINDOW_SIZE = (574, 729)
TITLE_AT = (8, 14)  # coin haut-gauche du modèle du titre dans la fenêtre
FIRST_LINE_Y, ROW_PITCH, ROWS = 204, 54.5, 10  # haut du texte « PA / PO » du 1er sort, puis un sort par ligne
BAND_ABOVE, BAND_BELOW = 5, 17  # bande lue autour de la ligne (les modèles font 18 px : marge de ±2 px)
TEXT_X = (70, 320)
LEVEL_X, LEVEL_ABOVE_LINE = (460, 536), 24  # « Niv. 2 » : à droite, haut du texte 24 px au-dessus de la ligne PA / PO
ICON_X, FIRST_ICON_Y, ICON_SIZE = 20, 176, 38
ICON_MARGIN = 4  # l'icône est découpée avec une marge : elle peut être décalée d'un ou deux pixels
GLYPHS = {"0": "chiffre_0", "1": "chiffre_1", "2": "chiffre_2", "3": "chiffre_3", "4": "chiffre_4", "6": "chiffre_6", "7": "chiffre_7", "9": "chiffre_9",
          "5": "chiffre_5", "8": "chiffre_8", "-": "tiret"}


# Défilement de la liste, en pixels de la fenêtre de référence.
SCROLL_COLUMNS = (544, 548, 552)  # colonnes lues dans la barre de défilement (poignée sombre sur rail clair)
SCROLL_SPAN = (185, 700)  # rail entre les flèches du haut et du bas
THUMB_TOP, THUMB_BOTTOM = 193, 694  # course de la poignée, mesurée (04/10 : bas de liste du Crâ et du Iop)
THUMB_DARK = 130  # niveau de gris de la poignée : 74 (reflets 101), rail 183
ARROW_DOWN = (548, 704)  # flèche du bas de la barre de défilement (le rail : même colonne)
LIST_POINT = (300, 450)  # au milieu de la liste : la molette la fait défiler
AWAY_POINT = (290, 22)  # barre de titre : la souris n'y survole aucune ligne de la liste
SAME_ICON = 0.9  # deux icônes de ligne identiques (même sort) : corrélation au moins
ICON_SHIFT = 2  # deux pages : une même icône peut tomber un ou deux pixels plus haut ou plus bas


@dataclass(frozen=True)
class ScrollBar:
    """Poignée de la barre de défilement de la liste (haut et bas, en pixels de la fenêtre de référence)."""

    top: int
    bottom: int

    @property
    def scrolls(self) -> bool:
        """La liste a plus de lignes qu'elle n'en montre (poignée plus courte que sa course)."""
        return self.bottom - self.top < THUMB_BOTTOM - THUMB_TOP - 12

    @property
    def at_top(self) -> bool:
        return self.top <= THUMB_TOP + 3

    @property
    def at_end(self) -> bool:
        return self.bottom >= THUMB_BOTTOM - 3

    @property
    def rows(self) -> int | None:
        """Lignes de toute la liste, d'après la longueur de la poignée (elle couvre la part visible de la liste : 239 px
        sur 501 pour 10 lignes sur 21, 18/10) ; None si la liste ne défile pas."""
        if not self.scrolls:
            return None
        return max(ROWS + 1, round(ROWS * (THUMB_BOTTOM - THUMB_TOP) / max(1, self.bottom - self.top)))

    @property
    def first_row(self) -> int:
        """Ligne de la liste montrée en haut (0 : la première), d'après la place de la poignée sur sa course. 18/10 :
        liste de Lestunes restée défilée en bas à l'ouverture, poignée en y 431 à 670 : lignes 11 à 20 visibles."""
        rows = self.rows
        if rows is None or self.at_top:
            return 0
        free = max(1, THUMB_BOTTOM - THUMB_TOP - (self.bottom - self.top))
        return min(rows - ROWS, max(0, round((self.top - THUMB_TOP) / free * (rows - ROWS))))


@dataclass(frozen=True)
class SpellEntry:
    row: int  # position dans la liste de la fenêtre (0 = premier), liste défilée comprise
    pa: int | None
    spell_range: tuple[int, int] | None  # portée telle qu'écrite : « 1-5 PO » -> (1, 5), « 4 PO » -> (4, 4) ; voir reach
    learned: bool  # sort appris (les autres sont grisés, avec les PA et la portée de leur niveau 1)
    # Icône BGR avec ICON_MARGIN px de marge autour, pour la reconnaître et la retrouver dans la barre ; None : sort
    # annoncé par le serveur sans ligne lue dans la fenêtre (appris depuis la dernière lecture, 01/11).
    icon: np.ndarray | None
    level: int | None = None  # niveau du sort (« Niv. 2 ») ; None : pas appris ou illisible
    low_written: bool = True  # False : un seul nombre écrit (« 4 PO »), la portée minimale 0 n'est pas écrite

    @property
    def icon_core(self) -> np.ndarray | None:
        if self.icon is None:
            return None
        return self.icon[ICON_MARGIN:-ICON_MARGIN, ICON_MARGIN:-ICON_MARGIN]

    @property
    def reach(self) -> tuple[int, int] | None:
        """Portée min et max en cases : « 4 PO » -> (0, 4)."""
        if self.spell_range is None or self.low_written:
            return self.spell_range
        return 0, self.spell_range[1]

    def describe(self) -> str:
        pa = f"{self.pa} PA" if self.pa is not None else "PA illisibles"
        po = f"portée {self.reach[0]} à {self.reach[1]}" if self.reach else "portée illisible"
        level = (f"niv. {self.level}, " if self.level else "niveau illisible, ") if self.learned else ""
        return f"{level}{pa}, {po}{'' if self.learned else ' (pas appris)'}"


@lru_cache
def _glyph_templates() -> list[tuple[str, np.ndarray]]:
    """(caractère, modèle) ; plusieurs variantes par caractère (rendu légèrement différent selon la position)."""
    templates = []
    for char, name in GLYPHS.items():
        for path in sorted((ASSETS_DIR / "sorts").glob(f"{name}*.png")):
            image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
            templates.append((char, image.astype(np.float32) / 255))
    return templates


def _ink(band: np.ndarray) -> tuple[np.ndarray, float]:
    """Intensité de l'encre (0 fond, 1 le plus foncé) et contraste de la ligne."""
    background = float(np.median(band))
    contrast = background - float(band.min())
    return np.clip((background - band) / max(1.0, contrast), 0, 1), contrast


def _classify(glyph: np.ndarray) -> str:
    """`glyph` : colonne d'encre un peu plus haute que les modèles ; on cherche le meilleur calage vertical
    (l'écart de 54,5 px entre lignes les décale d'un pixel d'une ligne à l'autre)."""
    best, best_score = "?", 0.75
    for char, template in _glyph_templates():
        if abs(glyph.shape[1] - template.shape[1]) > 2:
            continue
        resized = cv2.resize(glyph.astype(np.float32), (template.shape[1], glyph.shape[0]), interpolation=cv2.INTER_AREA)
        score = float(cv2.matchTemplate(resized, template, cv2.TM_CCOEFF_NORMED).max())
        if score > best_score:
            best, best_score = char, score
    return best


def read_tokens(band: np.ndarray) -> list[str]:
    """Jetons d'une ligne « 4 PA   1-10 PO » en niveaux de gris (BAND_ABOVE px au-dessus du haut du texte,
    BAND_BELOW en dessous) : ["4", "??", "1-10", "??"]."""
    ink, contrast = _ink(band.astype(np.float32))
    if contrast < 25:
        return []
    columns = (ink > 0.35).any(axis=0)
    segments, start = [], None
    for x, on in enumerate(list(columns) + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            segments.append((start, x))
            start = None
    tokens: list[list[tuple[int, int]]] = []
    for i, segment in enumerate(segments):
        gap = segment[0] - segments[i - 1][1] if i else None
        if gap is not None and gap > 40:
            break  # « Coût du niveau suivant » plus à droite
        if gap is None or gap >= 5:
            tokens.append([segment])
        else:
            tokens[-1].append(segment)
    return ["".join(_classify(ink[:, a:b]) for a, b in token) for token in tokens]


def read_level(gray: np.ndarray, line_y: int) -> int | None:
    """Niveau « Niv. 2 » d'une ligne de la fenêtre (en niveaux de gris, géométrie de référence)."""
    top = line_y - LEVEL_ABOVE_LINE
    tokens = read_tokens(gray[top - BAND_ABOVE : top + BAND_BELOW, LEVEL_X[0] : LEVEL_X[1]])
    return int(tokens[-1]) if len(tokens) >= 2 and tokens[-1].isdigit() else None


def _parse_range(token: str) -> tuple[int, int] | None:
    """Portée telle qu'écrite : « 1-5 » -> (1, 5), « 4 » -> (4, 4) (SpellEntry.reach : 0 à 4)."""
    parts = token.split("-")
    if not all(p.isdigit() for p in parts) or len(parts) > 2:
        return None
    low, high = int(parts[0]), int(parts[-1])
    return (low, high) if low <= high else None


def find_spell_window(frame: Frame) -> Point | None:
    """Coin haut-gauche de la fenêtre « Tes sorts » si elle est ouverte."""
    found = match_template(frame, TITLE)
    if found is None:
        return None
    (x, y), _ = found
    layout = frame.layout
    template_w, template_h = 106 * layout.scale, 23 * layout.scale  # taille du modèle du titre
    return round(x - template_w / 2 - TITLE_AT[0] * layout.scale), round(y - template_h / 2 - TITLE_AT[1] * layout.scale)


_last_window: tuple[Frame | None, np.ndarray | None] = (None, None)


def _window(frame: Frame) -> np.ndarray | None:
    """La fenêtre « Tes sorts » (BGR), ramenée à l'échelle de la capture de référence pour garder une géométrie
    fixe ; None si elle n'est pas ouverte ou sort de la capture. Gardée pour la dernière capture : lecture, icônes
    et barre de défilement d'une même page la cherchaient chacune sur tout l'écran (16/10 : 160 ms à chaque fois).
    """
    global _last_window
    if _last_window[0] is frame:
        return _last_window[1]
    window = _find_window(frame)
    _last_window = (frame, window)
    return window


def _find_window(frame: Frame) -> np.ndarray | None:
    corner = find_spell_window(frame)
    if corner is None:
        return None
    x0, y0 = corner
    scale = frame.layout.scale
    near_reference = abs(scale - 1) <= 0.02  # en deçà, le flou du redimensionnement gênerait la lecture
    # La fenêtre telle qu'elle est à l'écran (avant : jamais plus petite que la référence, donc fausse sur toute
    # fenêtre du jeu plus petite que 1918 x 1008).
    width, height = WINDOW_SIZE if near_reference else (round(v * scale) for v in WINDOW_SIZE)
    window = frame.image[max(0, y0) : y0 + height, max(0, x0) : x0 + width]
    if window.shape[0] < height or window.shape[1] < width:
        return None
    if not near_reference:
        window = cv2.resize(window, WINDOW_SIZE, interpolation=cv2.INTER_AREA if scale > 1 else cv2.INTER_CUBIC)
    return window


def read_spell_window(frame: Frame, first_row: int = 0) -> list[SpellEntry]:
    """Sorts lisibles des lignes visibles ; `first_row` : ligne de la liste montrée en haut (liste défilée)."""
    window = _window(frame)
    if window is None:
        return []
    gray = cv2.cvtColor(window, cv2.COLOR_BGR2GRAY)
    entries = []
    for row in range(ROWS):
        line_y = round(FIRST_LINE_Y + row * ROW_PITCH)
        band = gray[line_y - BAND_ABOVE : line_y + BAND_BELOW, TEXT_X[0] : TEXT_X[1]]
        tokens = read_tokens(band)
        if len(tokens) < 3:
            continue
        pa = int(tokens[0]) if tokens[0].isdigit() else None
        _, contrast = _ink(band.astype(np.float32))
        icon_y = round(FIRST_ICON_Y + row * ROW_PITCH)
        m = ICON_MARGIN
        icon = window[icon_y - m : icon_y + ICON_SIZE + m, ICON_X - m : ICON_X + ICON_SIZE + m].copy()
        learned = contrast > 60
        entries.append(SpellEntry(first_row + row, pa, _parse_range(tokens[2]), learned, icon,
                                  read_level(gray, line_y) if learned else None, low_written="-" in tokens[2]))
    return entries


def scroll_bar(frame: Frame) -> ScrollBar | None:
    """Poignée de la barre de défilement de la liste ; None sans fenêtre ou sans poignée visible."""
    window = _window(frame)
    if window is None:
        return None
    gray = cv2.cvtColor(window, cv2.COLOR_BGR2GRAY)
    runs = []
    for x in SCROLL_COLUMNS:
        dark = list(gray[SCROLL_SPAN[0] : SCROLL_SPAN[1], x] < THUMB_DARK) + [False]
        best, start = (0, 0), None
        for y, on in enumerate(dark):
            if on and start is None:
                start = y
            elif not on and start is not None:
                best = max(best, (y - start, start))
                start = None
        if best[0] >= 20:
            runs.append((best[1] + SCROLL_SPAN[0], best[1] + best[0] + SCROLL_SPAN[0]))
    if len(runs) < 2:
        return None
    top, bottom = sorted(runs)[len(runs) // 2]  # colonne médiane
    return ScrollBar(top, bottom)


def row_icons(frame: Frame) -> list[np.ndarray | None]:
    """Icône de chaque ligne visible de la liste (BGR, avec ICON_SHIFT px de marge autour) ; None pour une ligne
    vide (fond uni)."""
    window = _window(frame)
    if window is None:
        return []
    icons, m = [], ICON_SHIFT
    for row in range(ROWS):
        y = round(FIRST_ICON_Y + row * ROW_PITCH)
        icon = window[y - m : y + ICON_SIZE + m, ICON_X - m : ICON_X + ICON_SIZE + m]
        icons.append(icon.copy() if float(icon[m:-m, m:-m].std()) > 12 else None)
    return icons


def _same_icon(a: np.ndarray, b: np.ndarray) -> bool:
    """Même icône, à ICON_SHIFT px près : le centre de `a` cherché dans `b`, les deux un peu floutés. Les lignes sont
    espacées de 54,5 px : après un défilement d'un nombre impair de lignes, une icône est dessinée un demi-pixel plus
    loin (04/10, liste du Crâ défilée d'une ligne : même sort 0,84 à 0,90 sans flou, 0,94 au moins avec ; deux sorts
    différents 0,85 au plus)."""
    m = ICON_SHIFT
    a, b = cv2.GaussianBlur(a, (3, 3), 0), cv2.GaussianBlur(b, (3, 3), 0)
    return float(cv2.matchTemplate(b, a[m:-m, m:-m], cv2.TM_CCOEFF_NORMED).max()) >= SAME_ICON


def page_shift(before: list[np.ndarray | None], after: list[np.ndarray | None]) -> int | None:
    """De combien de lignes la liste a défilé entre deux captures, d'après les icônes des lignes : la ligne r
    d'après montre le sort de la ligne r + décalage d'avant. 0 : rien n'a bougé ; None : aucun décalage n'explique
    les deux pages (défilé de plus d'une page, ou lignes pas à leur place).

    Au moins 2 lignes en commun pour conclure à un défilement (une seule icône pareille peut être un hasard)."""
    for shift in range(len(before)):
        pairs = [(before[r + shift], after[r]) for r in range(len(after))
                 if r + shift < len(before) and before[r + shift] is not None and after[r] is not None]
        if len(pairs) < (1 if shift == 0 else 2):
            continue
        if all(_same_icon(a, b) for a, b in pairs):
            return shift
    return None


NAME_X, NAME_ABOVE_LINE = 250, 16  # nom d'un sort dans la liste : au-dessus de sa ligne « PA / PO »
ICONS_DIR = ASSETS_DIR / "sorts" / "icones"  # une icône par sort connu (config/sorts.yaml)


def window_point(frame: Frame, corner: Point, at: tuple[float, float]) -> tuple[float, float]:
    """Point de la fenêtre (coordonnées de la fenêtre de référence, depuis son coin `corner`) dans la capture."""
    s = frame.layout.scale
    return corner[0] + at[0] * s, corner[1] + at[1] * s


def name_point(frame: Frame, corner: Point, row: int) -> tuple[float, float]:
    """Où cliquer pour ouvrir la fiche du sort de la ligne `row`."""
    s = frame.layout.scale
    return corner[0] + NAME_X * s, corner[1] + (FIRST_LINE_Y + row * ROW_PITCH - NAME_ABOVE_LINE) * s


@lru_cache
def _known_icons() -> dict[str, np.ndarray]:
    return {path.stem: cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR) for path in sorted(ICONS_DIR.glob("*.png"))}


def identify(entry: SpellEntry, threshold: float = 0.8) -> str | None:
    """Identifiant du sort (nom de son icône dans assets/sorts/icones), None s'il est inconnu."""
    best, best_score = None, threshold
    for spell_id, icon in _known_icons().items():
        if entry.icon.shape[0] < icon.shape[0] or entry.icon.shape[1] < icon.shape[1]:
            continue
        score = float(cv2.matchTemplate(entry.icon, icon, cv2.TM_CCOEFF_NORMED).max())  # meilleur calage
        if score > best_score:
            best, best_score = spell_id, score
    return best


ICON_SIZES_IN_BAR = (34, 36, 38)  # l'icône fait 36 px dans la barre (38 dans la fenêtre)


def slot_scores(frame: Frame, entry: SpellEntry, slots: range = range(0, 15)) -> dict[int, float]:
    """Ressemblance de l'icône du sort avec chaque case de la barre (cherchée à plusieurs tailles et positions)."""
    scores = {}
    for slot in slots:
        x0, y0, x1, y1 = frame.layout.box(*spell_slot_box(slot))
        margin = round(6 * frame.layout.scale)
        region = frame.crop((max(0, x0 - margin), max(0, y0 - margin), x1 + margin, y1 + margin))
        best = -1.0
        for size in ICON_SIZES_IN_BAR:
            side = round(size * frame.layout.scale)
            if region.shape[0] < side or region.shape[1] < side:
                continue
            template = cv2.resize(entry.icon_core, (side, side), interpolation=cv2.INTER_AREA)
            best = max(best, float(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED).max()))
        scores[slot] = best
    return scores


def locate_in_bar(frame: Frame, entries: list[SpellEntry], threshold: float = 0.65) -> dict[int, SpellEntry]:
    """Case de la barre -> sort de la fenêtre, pour les sorts appris présents dans la barre."""
    candidates = sorted(((score, slot, entry.row) for entry in entries if entry.learned
                         for slot, score in slot_scores(frame, entry).items() if score >= threshold), reverse=True)
    by_row = {entry.row: entry for entry in entries}
    found: dict[int, SpellEntry] = {}
    placed_rows = set()
    for _, slot, row in candidates:  # meilleures correspondances d'abord, une case et un sort chacun
        if slot not in found and row not in placed_rows:
            found[slot] = by_row[row]
            placed_rows.add(row)
    return found


GREYED_RATIO = 0.7  # icône de la barre plus sombre que ça (par rapport à celle de la fenêtre) : sort grisé


def icon_brightness(image: np.ndarray) -> float:
    """Luminosité moyenne du centre d'une icône (le cadre de la case ne compte pas)."""
    h, w = image.shape[:2]
    my, mx = round(h * 0.15), round(w * 0.15)
    return float(cv2.cvtColor(image[my : h - my, mx : w - mx], cv2.COLOR_BGR2HSV)[:, :, 2].mean())


def slot_greyed(frame: Frame, slot: int, entry: SpellEntry) -> bool:
    """Le jeu a grisé ce sort dans la barre : plus assez de PA, relance en cours... (barre visible à vérifier avant).

    Mesuré sur les journaux du 28/09 : une icône lançable garde 0,96 à 0,99 de la luminosité de son icône dans
    la fenêtre des sorts, une icône grisée 0,49 à 0,50 (le chiffre de relance posé dessus compris)."""
    if entry.icon is None:
        return False  # pas d'icône de référence : le serveur dira s'il refuse le sort
    slot_image = frame.crop(frame.layout.box(*spell_slot_box(slot)))
    return icon_brightness(slot_image) < GREYED_RATIO * icon_brightness(entry.icon_core)


def find_spells_button(frame: Frame) -> Point | None:
    found = match_template(frame, BUTTON, box=frame.layout.box(*BUTTON_ZONE))
    return found[0] if found else None


def find_close_button(frame: Frame) -> Point | None:
    corner = find_spell_window(frame)
    if corner is None:
        return None
    x0, y0 = corner
    s = frame.layout.scale
    found = match_template(frame, CLOSE, box=(x0 + round(500 * s), y0, x0 + round(574 * s), y0 + round(60 * s)))
    return found[0] if found else None
