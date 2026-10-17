"""Caractéristiques du personnage : fenêtres « Tes caractéristiques » et « Caracteristiques avancées ».

Ouverture (04/10, captures de l'utilisateur, tests/fixtures/caracteristiques_*.png, échelle 1:1 du jeu en 1920 x 1009) :
le 1er bouton orange de la barre d'icônes (ADN, à gauche de la baguette des sorts, vers (1457, 821)) ouvre « Tes
caractéristiques » ; l'épée, à droite de la barre « Caractéristiques » de cette fenêtre, ouvre « Caracteristiques
avancées » (sans accent sur le « e » dans le jeu). Échap ferme les deux d'un coup.

« Caracteristiques avancées » : un tableau qui défile, une ligne par caractéristique (icône + libellé à gauche),
colonnes Base, Additionnel, Équipement, Dons, Boost, Total ; chiffres verts, « - » gris pour 0. Seule la colonne
Total est lue. Chaque ligne est retrouvée à son libellé (modèle découpé dans la capture de l'utilisateur, icône
comprise et jusqu'au bord de la colonne : « Bonus aux dégâts » ne ressemble donc pas à « Bonus aux dégâts (%) » ni à
« Bonus aux dégâts physiques »), où qu'elle soit dans la liste. Les lignes plus bas (soins, critiques...) ne sont pas
visibles sans faire défiler la liste : pas lues.

« Tes caractéristiques » (secours, si l'épée ou la fenêtre avancée font défaut) : « Force 0 (+80) » = base, puis entre
parenthèses ce que donne l'équipement : total = la somme. Disposition fixe, lue aux places mesurées sur la capture.
Elle ne donne ni les bonus aux dommages, ni le pourcentage.

Chiffres : comparés à des modèles découpés dans les deux captures (assets/stats/chiffres) : 0 à 8 et « + », vus dans
les deux fenêtres (3 et 4 seulement dans la fenêtre avancée, et un 3 gras). Le 9 n'y figure pas : son modèle est le 6
retourné (Verdana : le 9 est un 6 tourné d'un demi-tour), à vérifier sur une vraie capture. Parenthèses et tiret se
reconnaissent à leur forme : parenthèses plus hautes que les chiffres, tiret plat (« - » seul : 0 ; devant un nombre :
signe moins).
"""

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from bot.perception.frame import Frame
from bot.perception.layout import Point
from bot.perception.templates import ASSETS_DIR, load_template, match_template

BUTTON = "stats/bouton.png"  # icône ADN orange de la barre d'icônes
TITLE = "stats/titre.png"  # « Tes caractéristiques »
SWORD = "stats/epee.png"  # bouton du détail, dans « Tes caractéristiques »
ADVANCED_TITLE = "stats/titre_avancees.png"  # « Caracteristiques avancées »
LABELS_DIR, DIGITS_DIR = "stats/lignes", "stats/chiffres"
# Barre d'icônes au-dessus de la barre de sorts : le bouton (59 x 62) y est vers (1457, 821) (journaux du 04/10).
BUTTON_ZONE = (1380, 785, 1918, 855)
BUTTON_THRESHOLD = 0.85  # 0,99 ; trouvé sur les 26 captures hors combat des journaux essayées (04/10)
TITLE_THRESHOLD = 0.85

# « Tes caractéristiques », repère : coin haut-gauche de la capture de l'utilisateur (432 x 735).
SIMPLE_SIZE = (432, 735)
SIMPLE_TITLE_AT = (8, 18)  # coin haut-gauche du modèle du titre
SWORD_ZONE = (330, 450, 432, 520)  # l'épée (48 x 36) en (374, 466), à droite de la barre « Caractéristiques »
SWORD_THRESHOLD = 0.8  # la découpe de l'épée faite à part par l'utilisateur (survolée ?) : 0,93
# Le clic vise le milieu de l'épée dessinée (x 22 à 42, y 8 à 29 dans le modèle), pas le milieu du modèle (24, 18) :
# l'épée n'en occupe que la droite, et le jeu ne réagit que sur l'épée elle-même. 17/10, deux sessions en fenêtres de
# 1920 x 1057, visé au milieu du modèle : chaque clic à gauche de x 1862 (fond, ou bord du pommeau) n'ouvrait rien
# (10 sur 10), chaque clic à partir de 1862 ouvrait la fenêtre avancée (14 sur 14) ; le milieu de l'épée est vers 1870.
SWORD_CENTER = (32, 19)  # dans le modèle
SWORD_SPREAD = (3, 3)  # l'épée est en diagonale : un carré de ±3 autour de son milieu reste dessus
# Lignes : (haut du texte des chiffres, colonnes où lire la valeur), mesurées sur la capture.
SIMPLE_ROWS = {
    "pa": (280, (320, 396)), "pm": (313, (320, 396)), "portee": (411, (320, 396)), "invocations": (444, (320, 396)),
    "vitalite": (512, (225, 362)), "sagesse": (544, (225, 362)), "force": (577, (225, 362)),
    "intelligence": (609, (225, 362)), "chance": (642, (225, 362)), "agilite": (674, (225, 362)),
}

# « Caracteristiques avancées », repère : coin haut-gauche de la capture de l'utilisateur (702 x 728, coupée sous la
# ligne « Bonus aux pièges (%) » ; la fenêtre continue plus bas).
ADVANCED_SIZE = (702, 760)  # hauteur supposée (lue jusqu'au bord de l'écran si elle dépasse)
ADVANCED_TITLE_AT = (7, 14)
LIST_TOP = 100  # haut de la première ligne du tableau (115), moins une marge
LABEL_X = (4, 292)  # colonne des libellés (icône comprise), jusqu'au trait de la colonne Base (296)
LABEL_TOP = 3  # haut du modèle d'un libellé sous le haut de sa ligne (lignes de 36,4 px)
TOTAL_X = (618, 681)  # intérieur de la colonne Total (traits en 614-615 et 682)
TEXT_BELOW_ROW = 12  # haut des chiffres sous le haut de la ligne (14 px de haut)
LABEL_THRESHOLD = 0.85
# Lignes lues (clé -> modèle de son libellé). Seules force, intelligence, chance, agilité et les deux bonus aux
# dommages sont indispensables (REQUIRED) ; les autres sont lues pour le journal et le combat (PA, PM, portée).
ADVANCED_ROWS = ("vitalite", "sagesse", "force", "intelligence", "chance", "agilite", "pa", "pm", "portee",
                 "invocations", "dommages", "dommages_physiques", "maitrise_arme", "dommages_pourcent")
REQUIRED = ("force", "intelligence", "chance", "agilite", "dommages", "dommages_pourcent")
SIMPLE_REQUIRED = ("force", "intelligence", "chance", "agilite")

# Lecture des valeurs : bande autour du texte, caractères séparés par des colonnes vides, jetons par les espaces.
BAND_ABOVE, BAND_BELOW = 5, 20  # bande lue autour du haut du texte (chiffres de 14 à 16 px, parenthèses de 20)
INK = 0.35  # encre : au moins 35 % du contraste de la bande
MIN_CONTRAST = 25
SPACE = 6  # colonnes vides entre deux jetons (« 0 (+80) » : 10 ; entre deux chiffres : 2 à 4)
DIGIT_SCORE = 0.75
PAREN_HEIGHT = 18  # une parenthèse dépasse les chiffres en haut et en bas (20 px contre 14 à 16)
DASH_HEIGHT = 4  # tiret : 2 px de haut


@dataclass(frozen=True)
class StatsReading:
    """Valeurs lues dans une fenêtre : clé -> nombre, None si illisible (« - » : 0)."""

    values: dict[str, int | None]
    source: str  # « avancées » ou « simple »

    def missing(self, required: tuple[str, ...]) -> list[str]:
        return [key for key in required if self.values.get(key) is None]


# Recherche des fenêtres et des boutons


def find_stats_button(frame: Frame) -> Point | None:
    found = match_template(frame, BUTTON, box=frame.layout.box(*BUTTON_ZONE), threshold=BUTTON_THRESHOLD)
    return found[0] if found else None


def _corner(frame: Frame, title: str, title_at: tuple[int, int]) -> Point | None:
    """Coin haut-gauche (repère de la capture de l'utilisateur) d'une fenêtre trouvée à son titre, cherché dans tout
    l'écran (la place des fenêtres n'est pas connue), en niveaux de gris : ~30 ms au lieu de ~120 en couleurs.
    Meilleure place sans la fenêtre, sur 164 captures des journaux et de tests/fixtures : 0,56 au plus (seuil 0,85)."""
    s = frame.layout.scale
    template = _gray_template(title, round(s, 2))
    gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
    if gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1]:
        return None
    _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED))
    if score < TITLE_THRESHOLD:
        return None
    return round(x - title_at[0] * s), round(y - title_at[1] * s)


@lru_cache
def _gray_template(name: str, scale: float) -> np.ndarray:
    path = ASSETS_DIR / name
    image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"Modèle introuvable : {path}")
    if abs(scale - 1) > 0.02:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return image


def find_stats_window(frame: Frame) -> Point | None:
    """Coin haut-gauche de « Tes caractéristiques » si elle est ouverte."""
    return _corner(frame, TITLE, SIMPLE_TITLE_AT)


def find_advanced_window(frame: Frame) -> Point | None:
    """Coin haut-gauche de « Caracteristiques avancées » si elle est ouverte."""
    return _corner(frame, ADVANCED_TITLE, ADVANCED_TITLE_AT)


def find_sword_button(frame: Frame, corner: Point | None = None) -> Point | None:
    """Milieu de l'épée de « Tes caractéristiques », où cliquer (fenêtre cherchée si `corner` n'est pas donné)."""
    corner = corner or find_stats_window(frame)
    if corner is None:
        return None
    s = frame.layout.scale
    x0, y0, x1, y1 = SWORD_ZONE
    box = (max(0, corner[0] + round(x0 * s)), max(0, corner[1] + round(y0 * s)),
           min(frame.layout.width, corner[0] + round(x1 * s)), min(frame.layout.height, corner[1] + round(y1 * s)))
    found = match_template(frame, SWORD, box=box, threshold=SWORD_THRESHOLD)
    if found is None:
        return None
    (x, y), _ = found  # milieu du modèle
    height, width = load_template(SWORD).shape[:2]
    return round(x + (SWORD_CENTER[0] - width / 2) * s), round(y + (SWORD_CENTER[1] - height / 2) * s)


def _window(frame: Frame, corner: Point, size: tuple[int, int]) -> np.ndarray | None:
    """La fenêtre en niveaux de gris, ramenée à la géométrie des captures de l'utilisateur (coupée en bas si elle sort
    de l'écran) ; None si son haut ou son côté sortent de l'écran."""
    scale = frame.layout.scale
    near_reference = abs(scale - 1) <= 0.02
    width, height = size if near_reference else (round(v * scale) for v in size)
    x0, y0 = corner
    if x0 < 0 or y0 < 0 or x0 + width > frame.layout.width:
        return None
    window = frame.image[y0 : y0 + height, x0 : x0 + width]
    if not near_reference:
        shown = round(window.shape[0] / scale)
        window = cv2.resize(window, (size[0], shown), interpolation=cv2.INTER_AREA if scale > 1 else cv2.INTER_CUBIC)
    return cv2.cvtColor(window, cv2.COLOR_BGR2GRAY)


# Lecture des chiffres


@lru_cache
def _digit_templates() -> list[tuple[str, np.ndarray]]:
    """(caractère, modèle d'encre 0..1) : chiffres (chiffre_<c>_*.png) et « + » (plus_*.png) ; plusieurs variantes
    par caractère (fenêtre, graisse, position). Le « + » a son modèle : à sa forme seule, un 3 de 13 px de haut
    (fenêtre avancée) passait pour un « + »."""
    templates = []
    for path in sorted((ASSETS_DIR / DIGITS_DIR).glob("*.png")):
        image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
        char = "+" if path.stem.startswith("plus") else path.stem.split("_")[1]
        templates.append((char, image.astype(np.float32) / 255))
    return templates


def ink_of(band: np.ndarray) -> np.ndarray | None:
    """Encre d'une bande en niveaux de gris (0 : fond, 1 : le plus foncé) ; None si la bande est vide."""
    band = band.astype(np.float32)
    background = float(np.median(band))
    contrast = background - float(band.min())
    if contrast < MIN_CONTRAST:
        return None
    return np.clip((background - band) / contrast, 0, 1)


def glyph_columns(ink: np.ndarray) -> list[list[tuple[int, int]]]:
    """Caractères (colonnes [a, b) d'encre), groupés en jetons séparés par au moins SPACE colonnes vides."""
    columns = (ink > INK).any(axis=0)
    segments, start = [], None
    for x, on in enumerate(list(columns) + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            segments.append((start, x))
            start = None
    tokens: list[list[tuple[int, int]]] = []
    for i, segment in enumerate(segments):
        if i and segment[0] - segments[i - 1][1] < SPACE:
            tokens[-1].append(segment)
        else:
            tokens.append([segment])
    return tokens


def classify(glyph: np.ndarray) -> str:
    """Un caractère (colonnes d'encre sur toute la hauteur de la bande) : chiffre, « - », « + », « ( », « ) », ou
    « ? »."""
    rows = np.nonzero((glyph > INK).any(axis=1))[0]
    height, width = int(rows[-1] - rows[0] + 1), glyph.shape[1]
    if height <= DASH_HEIGHT:
        return "-" if width >= 3 else "?"
    if height >= PAREN_HEIGHT:
        if width > 7:
            return "?"  # « / » de « 217 / 217 »
        middle = glyph[rows[0] + height // 2]
        center = float((middle * np.arange(width)).sum() / max(middle.sum(), 1e-6))
        return "(" if center < (width - 1) / 2 else ")"
    best, best_score = "?", DIGIT_SCORE
    for char, template in _digit_templates():
        if abs(width - template.shape[1]) > 2 or glyph.shape[0] < template.shape[0]:
            continue
        resized = cv2.resize(glyph.astype(np.float32), (template.shape[1], glyph.shape[0]), interpolation=cv2.INTER_AREA)
        score = float(cv2.matchTemplate(resized, template, cv2.TM_CCOEFF_NORMED).max())
        if score > best_score:
            best, best_score = char, score
    return best


def read_tokens(band: np.ndarray) -> list[str]:
    """Jetons d'une bande en niveaux de gris : « 0 (+80) » -> ["0", "(+80)"] ; [] si elle est vide."""
    ink = ink_of(band)
    if ink is None:
        return []
    return ["".join(classify(ink[:, a:b]) for a, b in token) for token in glyph_columns(ink)]


def _number(text: str) -> int | None:
    """« 80 » -> 80, « -12 » -> -12, « - » -> 0 (case vide du tableau) ; None sinon."""
    if text == "-":
        return 0
    sign, digits = (-1, text[1:]) if text.startswith("-") else (1, text.lstrip("+"))
    return sign * int(digits) if digits.isdigit() else None


def parse_value(tokens: list[str]) -> int | None:
    """Valeur d'une case : « 52 », « - » (0), « 0 (+80) » (base + équipement : 80), « 10 (-5) » ; None si illisible."""
    if not tokens:
        return None
    base = _number(tokens[0])
    if base is None or len(tokens) > 2:
        return None
    if len(tokens) == 1:
        return base
    extra = tokens[1]
    if not (extra.startswith("(") and extra.endswith(")")):
        return None
    bonus = _number(extra[1:-1])
    return None if bonus is None else base + bonus


def _value_at(gray: np.ndarray, text_top: int, x: tuple[int, int]) -> int | None:
    if text_top - BAND_ABOVE < 0 or text_top + BAND_BELOW > gray.shape[0]:
        return None
    return parse_value(read_tokens(gray[text_top - BAND_ABOVE : text_top + BAND_BELOW, x[0] : x[1]]))


# Fenêtres


@lru_cache
def _label_templates() -> dict[str, np.ndarray]:
    templates = {}
    for key in ADVANCED_ROWS:
        path = ASSETS_DIR / LABELS_DIR / f"{key}.png"
        templates[key] = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
    return templates


def find_rows(gray: np.ndarray) -> dict[str, int]:
    """Haut de la ligne de chaque caractéristique dans la fenêtre avancée (géométrie de référence), trouvée à son
    libellé ; celles qui ne se voient pas (liste défilée, fenêtre coupée) manquent."""
    column = gray[LIST_TOP:, LABEL_X[0] - 2 : LABEL_X[1] + 2]
    rows = {}
    for key, template in _label_templates().items():
        if column.shape[0] < template.shape[0]:
            continue
        scores = cv2.matchTemplate(column, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, (_, y) = cv2.minMaxLoc(scores)
        if score >= LABEL_THRESHOLD:
            rows[key] = LIST_TOP + y - LABEL_TOP
    return rows


def read_advanced_window(frame: Frame, corner: Point | None = None) -> StatsReading | None:
    """Colonne Total de « Caracteristiques avancées » (cherchée si `corner` n'est pas donné) ; None si la fenêtre
    n'est pas ouverte."""
    corner = corner or find_advanced_window(frame)
    if corner is None:
        return None
    gray = _window(frame, corner, ADVANCED_SIZE)
    if gray is None:
        return StatsReading({key: None for key in ADVANCED_ROWS}, "avancées")
    rows = find_rows(gray)
    values = {key: _value_at(gray, rows[key] + TEXT_BELOW_ROW, TOTAL_X) if key in rows else None
              for key in ADVANCED_ROWS}
    return StatsReading(values, "avancées")


def read_stats_window(frame: Frame, corner: Point | None = None) -> StatsReading | None:
    """« Tes caractéristiques » (cherchée si `corner` n'est pas donné) : base + équipement de chaque
    caractéristique, PA, PM, portée ; None si la fenêtre n'est pas ouverte."""
    corner = corner or find_stats_window(frame)
    if corner is None:
        return None
    gray = _window(frame, corner, SIMPLE_SIZE)
    if gray is None or gray.shape[0] < SIMPLE_SIZE[1]:
        return StatsReading({key: None for key in SIMPLE_ROWS}, "simple")
    return StatsReading({key: _value_at(gray, top, x) for key, (top, x) in SIMPLE_ROWS.items()}, "simple")
