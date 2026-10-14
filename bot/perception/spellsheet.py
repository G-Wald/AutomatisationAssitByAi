"""Fiche d'un sort (clic sur un sort dans la fenêtre « Tes sorts » : elle s'ouvre à côté de la liste).

Lu automatiquement, dans « Autres caractéristiques » :
- les cases cochées (coche verte / croix rouge) : portée modifiable, ligne de vue, lancer en ligne,
  cellules libres, échec critique qui finit le tour ;
- les nombres : lancers par tour, lancers par tour par joueur (= par cible), tours entre deux lancers
  (« - » : illimité / sans délai). Chiffres reconnus : 1, 2 et 5 (modèles des fiches du 27/09) ; un autre
  chiffre est signalé illisible.

Positions en pixels de référence depuis le coin haut-gauche du titre « Autres caractéristiques »,
mesurées sur captures/sorts_20260927_220837.
"""

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from bot.perception.frame import Frame
from bot.perception.layout import Point
from bot.perception.templates import ASSETS_DIR, match_template

TITLE = "sorts/fiche_titre.png"
TITLE_SIZE = (239, 20)
CHECKS = {  # décalage vertical de chaque case à cocher (colonne à +652 px du titre)
    "portee_modifiable": 49, "ligne_de_vue": 79, "lancer_en_ligne": 108, "cellules_libres": 137, "ec_fini_le_tour": 165,
}
CHECK_X = 652
COUNTS = {"lancers_par_tour": 122, "lancers_par_cible": 158, "relance": 194}  # colonne des nombres : +414 à +446
COUNT_X = (414, 446)
UNREADABLE = -1  # nombre présent mais chiffre inconnu


@dataclass(frozen=True)
class SpellSheet:
    checks: dict[str, bool | None]  # None : ni coche ni croix trouvée
    counts: dict[str, int | None]  # None : « - » (illimité) ; UNREADABLE : chiffre inconnu


@lru_cache
def _count_digits() -> list[tuple[int, np.ndarray]]:
    digits = []
    for path in sorted((ASSETS_DIR / "sorts").glob("fiche_[0-9].png")):
        image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255
        digits.append((int(path.stem.split("_")[1]), image))
    return digits


def find_sheet(frame: Frame) -> Point | None:
    """Coin haut-gauche du titre « Autres caractéristiques » si une fiche est ouverte."""
    found = match_template(frame, TITLE)
    if found is None:
        return None
    (x, y), _ = found
    s = frame.layout.scale
    return round(x - TITLE_SIZE[0] * s / 2), round(y - TITLE_SIZE[1] * s / 2)


def _check(frame: Frame, anchor: Point, dy: int) -> bool | None:
    s = frame.layout.scale
    x, y = anchor[0] + CHECK_X * s, anchor[1] + dy * s
    icon = frame.crop((round(x - 14 * s), round(y - 10 * s), round(x + 14 * s), round(y + 10 * s))).astype(np.int16)
    b, g, r = icon[:, :, 0], icon[:, :, 1], icon[:, :, 2]
    green = int(((g > 120) & (g > r + 50) & (g > b + 50)).sum())
    red = int(((r > 150) & (r > g + 80) & (r > b + 80)).sum())
    if max(green, red) < 30 * s * s:
        return None
    return green > red


def _count(frame: Frame, anchor: Point, dy: int) -> int | None:
    s = frame.layout.scale
    y = anchor[1] + dy * s
    region = frame.crop((round(anchor[0] + COUNT_X[0] * s), round(y - 10 * s), round(anchor[0] + COUNT_X[1] * s), round(y + 10 * s)))
    ink = region.max(axis=2) < 130
    ys, xs = np.nonzero(ink)
    if len(ys) == 0:
        return UNREADABLE
    glyph = ink[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1].astype(np.float32)
    if glyph.shape[0] <= 5 * s:
        return None  # « - »
    best, best_score = UNREADABLE, 0.6
    for digit, template in _count_digits():
        resized = cv2.resize(glyph, (template.shape[1], template.shape[0]), interpolation=cv2.INTER_AREA)
        score = float(cv2.matchTemplate(resized, template, cv2.TM_CCOEFF_NORMED)[0, 0])
        if score > best_score:
            best, best_score = digit, score
    return best


def read_sheet(frame: Frame) -> SpellSheet | None:
    anchor = find_sheet(frame)
    if anchor is None:
        return None
    return SpellSheet(
        checks={name: _check(frame, anchor, dy) for name, dy in CHECKS.items()},
        counts={name: _count(frame, anchor, dy) for name, dy in COUNTS.items()},
    )
