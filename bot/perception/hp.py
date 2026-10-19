"""Vie du combattant survolé par la souris, lue sur sa fiche (en combat).

Survoler un combattant remplace la barre de sorts par sa fiche, en bas à droite : nom et niveau, puis un cœur rouge
suivi de sa vie actuelle en chiffres rouges, ses PA (bleu) et ses PM (vert). Vu sur les captures : Piou Rose, 7 PV
(tests/fixtures/combat_infobulle.png, 27/09) et Walitcoin, 212 PV (combat_fiche_walitcoin_212pv.jpg, journaux du
03/10 : souris restée sur lui au début du combat). La même vie est écrite en blanc dans l'infobulle au-dessus du
combattant, sur une barre rouge dont la partie claire donne la part de vie restante (pas lue : sa place dépend du
sprite).

Chiffres : Verdana gras, 14 à 15 px de haut. Modèles (assets/vie) : les dix chiffres dessinés en Verdana gras 19 px
par Windows (GDI), et les vrais 1, 2 et 7 rouges découpés dans ces captures (« _fiche »). Les chiffres dessinés
reconnaissent les vrais chiffres rouges et bleus de la fiche à 0,82-0,96, un autre chiffre à 0,57 au plus (03/10) ;
0, 3, 4, 5, 6, 8 et 9 n'ont pas encore été vus en rouge sur une fiche.
"""

from functools import lru_cache

import cv2
import numpy as np

from bot.perception.frame import Frame
from bot.perception.layout import Layout

SHEET_HP_ZONE = (1462, 852, 1600, 892)  # cœur et vie de la fiche (repère de référence), avant les PA et PM
SHEET_NAME_ZONE = (1420, 785, 1800, 830)  # nom du combattant, en haut de la fiche (sans « Niveau 22 » dessous)
NAME_ZOOM = 2  # agrandissement avant l'OCR
NAME_MATCH = 0.85  # ressemblance minimale du nom lu (difflib) : une lettre fausse sur 8 à 12
SHEET_LIGHT = 0.6  # part de fond clair dans la zone : 0,75 à 0,80 sur une fiche, 0,2 sur la barre de sorts (03/10)
DIGIT_MIN = 0.7  # ressemblance minimale d'un chiffre à son modèle
DIGIT_MARGIN = 0.1  # avance sur le meilleur autre chiffre
MAX_DIGITS = 4


@lru_cache
def _templates() -> dict[int, list[np.ndarray]]:
    from bot.perception.templates import ASSETS_DIR  # import local, comme dans ui.py

    digits: dict[int, list[np.ndarray]] = {}
    for path in sorted((ASSETS_DIR / "vie").glob("chiffre_*.png")):
        mask = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255
        digits.setdefault(int(path.stem.split("_")[1]), []).append(mask)
    return digits


def _red_ink(region: np.ndarray) -> np.ndarray:
    """Rouge vif du cœur et des chiffres (le fond de la fiche est blanc cassé, l'orange de l'anneau est écarté)."""
    region = region.astype(np.int16)
    b, g, r = region[:, :, 0], region[:, :, 1], region[:, :, 2]
    return (r > 140) & (r - np.maximum(g, b) > 70)


def _is_heart(stat: np.ndarray, layout: Layout) -> bool:
    """Le cœur de la fiche : environ 20 x 17 px, à gauche des chiffres."""
    _, _, w, h, _ = stat
    return layout.length(15) <= w <= layout.length(26) and layout.length(13) <= h <= layout.length(22)


def _digit(glyph: np.ndarray) -> int | None:
    """Chiffre le plus ressemblant, s'il ressemble assez à son modèle et nettement plus qu'à tout autre chiffre."""
    scores = {}
    for digit, masks in _templates().items():
        best = 0.0
        for mask in masks:
            resized = cv2.resize(glyph, (mask.shape[1], mask.shape[0]), interpolation=cv2.INTER_AREA)
            best = max(best, float(cv2.matchTemplate(resized, mask, cv2.TM_CCOEFF_NORMED)[0, 0]))
        scores[digit] = best
    ranked = sorted(scores, key=scores.get, reverse=True)
    first, second = ranked[0], ranked[1]
    if scores[first] < DIGIT_MIN or scores[first] - scores[second] < DIGIT_MARGIN:
        return None
    return first


def sheet_visible(frame: Frame) -> bool:
    """La fiche d'un combattant survolé remplace la barre de sorts (fond clair à la place des cases de sorts)."""
    region = frame.crop(frame.layout.box(*SHEET_HP_ZONE))
    return region.size > 0 and float((region.min(axis=2) > 200).mean()) >= SHEET_LIGHT


def read_hovered_hp(frame: Frame) -> int | None:
    """Vie actuelle du combattant survolé, lue sur sa fiche ; None sans fiche (souris sur aucun combattant) ou si un
    chiffre est illisible."""
    if not sheet_visible(frame):
        return None
    layout = frame.layout
    region = frame.crop(layout.box(*SHEET_HP_ZONE))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(_red_ink(region).astype(np.uint8), connectivity=8)
    marks = sorted((k for k in range(1, count) if stats[k][3] >= layout.length(10)), key=lambda k: stats[k][0])
    if not marks or not _is_heart(stats[marks[0]], layout) or not 1 <= len(marks) - 1 <= MAX_DIGITS:
        return None
    value = ""
    for k in marks[1:]:
        x, y, w, h, _ = stats[k]
        if w > layout.length(16):
            return None  # deux chiffres collés
        digit = _digit((labels[y : y + h, x : x + w] == k).astype(np.float32))
        if digit is None:
            return None
        value += str(digit)
    return int(value)


def read_hovered_name(frame: Frame) -> str | None:
    """Nom du combattant survolé, écrit en haut de sa fiche (au-dessus de « Niveau 22 »), lu par l'OCR de Windows ;
    None sans fiche, sans OCR ou sans texte. Lu « Walitcoin » et « Piou Rose » sur les captures du 03/10 et du 27/09
    (combat_fiche_walitcoin_212pv.jpg, combat_infobulle.png)."""
    from bot.perception.chat import ocr_lines  # import local : winrt n'est chargé que si on lit

    if not sheet_visible(frame):
        return None
    region = frame.crop(frame.layout.box(*SHEET_NAME_ZONE))
    if region.size == 0:
        return None
    lines = ocr_lines(cv2.resize(region, None, fx=NAME_ZOOM, fy=NAME_ZOOM, interpolation=cv2.INTER_CUBIC))
    text = " ".join(line.strip() for line in lines or [] if line.strip())
    return text or None


def same_name(read: str | None, name: str) -> bool:
    """Le nom lu sur la fiche est `name` : accents, majuscules et espaces indifférents, une lettre mal lue tolérée
    (l / I, o / 0 de l'OCR) ; jamais un nom seulement contenu dans l'autre."""
    import difflib

    from bot.perception.chat import _plain

    if not read or not name:
        return False
    a, b = _plain(read).replace(" ", ""), _plain(name).replace(" ", "")
    return difflib.SequenceMatcher(None, a, b).ratio() >= NAME_MATCH
