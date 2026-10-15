"""Nom et coordonnées de la carte, écrits en blanc ombré de noir en haut à gauche de la zone de jeu.

    Incarnam (Forêt) - Neutre
    Coordonnées : 2, 4

Chaque caractère est une tache blanche séparée des autres ; une tache n'est gardée que si son ombre (en bas à
droite) est sombre, ce qui écarte les pierres claires du décor. Les chiffres sont reconnus par comparaison
avec des modèles découpés dans les captures (assets/carte/chiffres) : un chiffre sans modèle rend les
coordonnées inconnues (None) plutôt que fausses.

La zone entre parenthèses (Forêt, Champs...) et la région avant elles (Incarnam) sont comparées en image :
deux cartes sont de la même zone quand ce mot est dessiné pareil, même s'il n'a pas encore de nom connu
(les noms connus sont les modèles de assets/carte/zones et assets/carte/regions).

Chiffres appris en route : quand le bot sait où il est (carte de départ donnée, puis chemin parcouru), un
chiffre inconnu est enregistré dans data/chiffres avec la valeur attendue, si le reste de la lecture concorde.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from bot.perception.frame import Frame
from bot.perception.templates import ASSETS_DIR

LEARNED_DIR = Path(__file__).resolve().parents[2] / "data" / "chiffres"  # chiffres appris en route

LABEL_BOX = (578, 16, 1000, 92)  # repère de référence : les deux lignes du nom et des coordonnées
LINE_SPLIT = 55  # référence : au-dessus, ligne du nom ; en dessous, ligne des coordonnées
WHITE_MIN = 170  # texte : les trois canaux au moins à ce niveau
SHADOW_MAX = 110  # ombre du texte : canal le plus clair au plus à ce niveau
# Bord à demi blanc d'un caractère (lissage) : ni le caractère ni son ombre. 11/10, « Coordonnées : 2, -25 » devant
# un arbre : le « - » n'avait qu'une ligne blanche, la suivante à 134-140 comptée comme ombre pas assez noire ; tiret
# écarté, coordonnées illisibles, plus de changement de carte.
HALF_WHITE = 120
DIGIT_MIN, DIGIT_MARGIN = 0.8, 0.08  # ressemblance minimale d'un chiffre à son modèle, et avance sur le suivant
SAME_WORD = 0.7  # ressemblance de deux mots dessinés pareil (zone, région) ; une pierre claire derrière le texte
# soude parfois deux lettres (0,78 mesuré), deux mots différents de même largeur restent bien en dessous
WORD_GAP = 18  # référence : au-delà de cet écart après le dernier caractère, c'est du décor


@dataclass(frozen=True, eq=False)
class Glyph:
    x: int
    y: int
    w: int
    h: int
    mask: np.ndarray  # tache du caractère, 1.0 sur le blanc

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


@dataclass(frozen=True, eq=False)
class MapLabel:
    coords: tuple[int, int] | None  # None : un chiffre pas encore appris (voir `text`)
    text: str  # coordonnées lues, « ? » pour un chiffre inconnu (« 2,4 », « -1,? »)
    zone: np.ndarray | None  # mot entre parenthèses, en image
    zone_name: str | None
    region: np.ndarray | None  # mot avant les parenthèses (Incarnam), en image
    region_name: str | None
    signature: np.ndarray  # ligne des coordonnées, en image : change avec la carte même sans chiffres connus
    glyphs: tuple = ()  # (caractère lu, masque) de chaque caractère des coordonnées, pour apprendre les chiffres

    @property
    def readable(self) -> bool:
        return self.signature.size > 1

    def same_zone(self, other: "MapLabel") -> bool:
        return same_word(self.zone, other.zone)

    def same_region(self, other: "MapLabel") -> bool:
        return same_word(self.region, other.region)

    def same_map(self, other: "MapLabel") -> bool:
        if self.region_name is not None and other.region_name is not None:
            if self.region_name != other.region_name:
                return False  # mêmes coordonnées, autre région (Incarnam 0,0 n'est pas Amakna 0,0)
        elif self.coords is None or other.coords is None or self.coords != other.coords:
            if self.region is not None and other.region is not None and not self.same_region(other):
                return False
        # 11/10 : « Astrub » souvent mal découpé sur les murs clairs de la cité ; deux noms de région reconnus
        # départagent, sinon les coordonnées lues suffisent (trois suiveurs crus perdus en 5,-21 avec le principal).
        if self.coords is not None and other.coords is not None:
            return self.coords == other.coords
        return same_word(self.signature, other.signature, threshold=0.95)

    def describe(self) -> str:
        if not self.readable:
            return "carte au nom illisible"
        zone = self.zone_name or "zone inconnue"
        region = self.region_name or "région inconnue"
        return f"{region} ({zone}) {self.text}"


def likeness(a: np.ndarray, b: np.ndarray) -> float:
    """Ressemblance de deux masques de texte, ramenés à la même taille : un peu floutés et décalables de 2 px,
    car le seuil du blanc rogne ou épaissit les traits d'un pixel selon le fond et la compression."""
    b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    if a.std() == 0 or b.std() == 0:
        return float(np.array_equal(a > 0.5, b > 0.5))
    a = cv2.GaussianBlur(cv2.copyMakeBorder(a, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=0), (3, 3), 0)
    b = cv2.GaussianBlur(b, (3, 3), 0)
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED).max())


def unreadable_label() -> MapLabel:
    """Carte dont le nom ne se lit pas (texte blanc sur un décor clair, fenêtre par-dessus) : rien de connu."""
    return MapLabel(coords=None, text="illisible", zone=None, zone_name=None, region=None, region_name=None,
                    signature=np.zeros((1, 1), np.float32))


def same_word(a: np.ndarray | None, b: np.ndarray | None, threshold: float = SAME_WORD) -> bool:
    """Deux mots (masques de texte) dessinés pareil : même taille à 2 px près, et même forme."""
    if a is None or b is None:
        return False
    if abs(a.shape[0] - b.shape[0]) > 2 or abs(a.shape[1] - b.shape[1]) > 2:
        return False
    return likeness(a, b) >= threshold


def text_glyphs(frame: Frame) -> list[Glyph]:
    """Taches de texte blanc ombré dans la zone du nom de la carte (coordonnées de la capture)."""
    x0, y0, x1, y1 = frame.layout.box(*LABEL_BOX)
    region = frame.crop((x0, y0, x1, y1)).astype(np.int16)
    white = region.min(axis=2) >= WHITE_MIN
    half_white = region.min(axis=2) >= HALF_WHITE
    dark = region.max(axis=2) <= SHADOW_MAX
    count, labels, stats, _ = cv2.connectedComponentsWithStats(white.astype(np.uint8), connectivity=8)
    glyphs = []
    for k in range(1, count):
        x, y, w, h, area = stats[k]
        if w > frame.layout.length(40) or h > frame.layout.length(30):
            continue  # trop grand pour un caractère : décor clair
        mask = labels == k
        shadow = np.zeros_like(mask)
        shadow[1:, 1:] |= mask[:-1, :-1]
        shadow[2:, 2:] |= mask[:-2, :-2]
        shadow &= ~half_white
        if not shadow.any() or dark[shadow].mean() < 0.6:
            continue  # pas d'ombre noire : pas du texte
        glyphs.append(Glyph(x0 + x, y0 + y, w, h, mask[y : y + h, x : x + w].astype(np.float32)))
    return sorted(glyphs, key=lambda g: g.x)


def _word_mask(glyphs: list[Glyph]) -> np.ndarray | None:
    """Les taches côte à côte, recollées en une image (sans le fond)."""
    if not glyphs:
        return None
    left, top = min(g.x for g in glyphs), min(g.y for g in glyphs)
    right, bottom = max(g.right for g in glyphs), max(g.bottom for g in glyphs)
    mask = np.zeros((bottom - top, right - left), np.float32)
    for g in glyphs:
        mask[g.y - top : g.bottom - top, g.x - left : g.right - left] = np.maximum(
            mask[g.y - top : g.bottom - top, g.x - left : g.right - left], g.mask)
    return mask


@lru_cache
def _masks(folder: str) -> list[tuple[str, np.ndarray]]:
    """(nom, masque) des modèles d'un dossier ; « chiffre_4b.png » -> « 4 » pour les chiffres."""
    found = []
    paths = sorted((ASSETS_DIR / "carte" / folder).glob("*.png"))
    if folder == "chiffres":
        paths += sorted(LEARNED_DIR.glob("*.png"))
    for path in paths:
        image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
        name = path.stem.split("_")[1][0] if folder == "chiffres" else path.stem
        found.append((name, image.astype(np.float32) / 255))
    return found


def _name(word: np.ndarray | None, folder: str, scale: float = 1.0) -> str | None:
    """Nom connu du mot. Modèles découpés à l'échelle 1 : mis à l'échelle de la fenêtre avant la comparaison, sinon
    same_word les écarte sur la taille (15/10, fenêtre de 1057 de haut : « Astrub » 66 px de large, modèle 64)."""
    if word is None:
        return None
    for name, mask in _masks(folder):
        if abs(scale - 1) > 0.02:
            mask = cv2.resize(mask, (round(mask.shape[1] * scale), round(mask.shape[0] * scale)),
                              interpolation=cv2.INTER_AREA)
        if same_word(word, mask):
            return name
    return None


def read_digit(glyph: Glyph) -> str | None:
    best: dict[str, float] = {}
    for digit, template in _masks("chiffres"):
        best[digit] = max(best.get(digit, -1.0), likeness(template, glyph.mask))
    ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < DIGIT_MIN:
        return None
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < DIGIT_MARGIN:
        return None
    return ranked[0][0]


def _merge_overlapping(glyphs: list[Glyph]) -> list[Glyph]:
    """Recolle les morceaux d'un même caractère (un trait fin coupé par le seuil du blanc) : ils se chevauchent
    en largeur, alors que deux chiffres voisins sont séparés par une colonne vide."""
    merged: list[list[Glyph]] = []
    for g in sorted(glyphs, key=lambda g: g.x):
        if merged and g.x < max(p.right for p in merged[-1]):
            merged[-1].append(g)
        else:
            merged.append([g])
    out = []
    for parts in merged:
        if len(parts) == 1:
            out.append(parts[0])
            continue
        x, y = min(p.x for p in parts), min(p.y for p in parts)
        mask = _word_mask(parts)
        out.append(Glyph(x, y, mask.shape[1], mask.shape[0], mask))
    return out


def _coordinates(glyphs: list[Glyph], frame: Frame) -> tuple[str, list[tuple[str, Glyph]]] | None:
    """Texte des coordonnées (« 2,4 », « -1,? ») et ses taches : ce qui suit les deux-points de « Coordonnées : »."""
    small = [g for g in glyphs if g.h <= frame.layout.length(5) and g.w <= frame.layout.length(5)]
    colon = next((a for a in small for b in small if a is not b and abs(a.x - b.x) <= 2 and b.y > a.y + 3), None)
    if colon is None:
        return None
    tall = max((g.h for g in glyphs), default=0)
    after = []
    for g in sorted((g for g in glyphs if g.x > colon.right), key=lambda g: g.x):
        if g.x - max([colon.right] + [a.right for a in after]) > frame.layout.length(WORD_GAP):
            break  # fin du texte : ce qui suit est du décor clair
        after.append(g)
    after = _merge_overlapping(after)
    digits = [g for g in after if g.h >= 0.75 * tall]
    top = min((g.y for g in digits), default=0)
    height = max((g.bottom for g in digits), default=0) - top
    read = []
    for g in after:
        if g.h >= 0.75 * tall:
            read.append((read_digit(g) or "?", g))
        elif g.w > g.h and g.h <= frame.layout.length(4):
            # Un tiret est à mi-hauteur des chiffres. 11/10 : un éclat clair du décor sous la ligne (« 1, -23 » devant
            # des fleurs) lu comme un tiret : « -1,-23 », Brawli s'est cru deux cartes plus loin.
            middle = g.y + g.h / 2
            if height and not top + 0.3 * height <= middle <= top + 0.8 * height:
                continue
            read.append(("-", g))
        elif g.h >= frame.layout.length(3):
            read.append((",", g))
    text = "".join(char for char, _ in read)
    return (text, read) if text else None


def _between_parentheses(glyphs: list[Glyph], frame: Frame) -> tuple[list[Glyph], list[Glyph]] | None:
    """(région, zone) : les taches avant la parenthèse ouvrante, et celles entre les deux parenthèses.

    Les parenthèses sont les taches fines qui descendent sous la ligne d'écriture."""
    if not glyphs:
        return None
    baseline = int(np.median([g.bottom for g in glyphs]))
    parentheses = [g for g in glyphs if g.bottom > baseline + frame.layout.length(2) and g.w <= frame.layout.length(6)
                   and g.h >= frame.layout.length(14)]
    if len(parentheses) < 2:
        return None
    opening, closing = parentheses[0], parentheses[1]
    region = [g for g in glyphs if g.right <= opening.x]
    zone = [g for g in glyphs if opening.right <= g.x and g.right <= closing.x]
    return region, zone


def read_map_label(frame: Frame) -> MapLabel | None:
    """Nom et coordonnées de la carte ; None si le texte n'est pas visible (chargement, fenêtre par-dessus)."""
    glyphs = text_glyphs(frame)
    split = frame.layout.y(LINE_SPLIT)
    name_line = [g for g in glyphs if g.y + g.h / 2 < split]
    coords_line = [g for g in glyphs if g.y + g.h / 2 >= split]
    found = _coordinates(coords_line, frame)
    if found is None:
        return None
    text, coords_glyphs = found
    if not re.fullmatch(r"-?[\d?]+,-?[\d?]+", text):
        return None  # texte assombri par une fenêtre par-dessus, ou masqué en partie
    parsed = re.fullmatch(r"(-?\d+),(-?\d+)", text)
    parts = _between_parentheses(name_line, frame)
    region, zone = (_word_mask(parts[0]), _word_mask(parts[1])) if parts else (None, None)
    return MapLabel(
        coords=(int(parsed[1]), int(parsed[2])) if parsed else None,
        text=text,
        zone=zone,
        zone_name=_name(zone, "zones", frame.layout.scale),
        region=region,
        region_name=_name(region, "regions", frame.layout.scale),
        signature=_word_mask([g for _, g in coords_glyphs]),
        glyphs=tuple((char, g.mask) for char, g in coords_glyphs),
    )


def learn_digits(label: MapLabel, coords: tuple[int, int]) -> list[str]:
    """Enregistre les chiffres inconnus de `label`, sachant que la carte est en `coords` ; renvoie les chiffres appris.

    Rien n'est appris si la lecture ne colle pas à ces coordonnées (nombre de caractères, chiffres déjà connus) :
    mieux vaut un chiffre inconnu de plus qu'un chiffre appris de travers."""
    expected = f"{coords[0]},{coords[1]}"
    chars = [char for char, _ in label.glyphs]
    if len(chars) != len(expected) or any(c != "?" and c != e for c, e in zip(chars, expected)):
        return []
    learned = []
    for index, ((char, mask), digit) in enumerate(zip(label.glyphs, expected)):
        if char == "?" and digit.isdigit():
            LEARNED_DIR.mkdir(parents=True, exist_ok=True)
            path = LEARNED_DIR / f"chiffre_{digit}_appris_{coords[0]}_{coords[1]}_{index}.png"
            cv2.imencode(".png", (mask * 255).astype(np.uint8))[1].tofile(str(path))
            learned.append(digit)
    if learned:
        _masks.cache_clear()
    return learned
