"""Chat du jeu : la position donnée par « /p %pos% », lue avec l'OCR de Windows.

16/10 : « /p %pos% » tapé dans la zone de saisie du chat (en arrière-plan : caractères envoyés à la fenêtre) affiche,
en bas du chat du dessous, « [19:48] (Groupe) Koalaloupour : [6,2] ». Le chat Commerce défile vite : la ligne est
cherchée aussitôt, en bas. Les chiffres du chat ne sont pas ceux du nom de la carte (police, gras) : lus par l'OCR de
Windows (Windows.Media.Ocr, paquets de requirements-winrt.txt), 41 ms sur la capture du 16/10. Sans ces paquets, rien
n'est lu (None) et le bot fait comme avant.

Utilisé rarement (utilisateur, 11/10 : pas à chaque carte) : quand la position est inconnue (brain/position.py).
"""

import asyncio
import logging
import re

import cv2
import numpy as np

from bot.perception.frame import Frame
from bot.perception.layout import Point

log = logging.getLogger(__name__)

POSITION_COMMAND = "/p %pos%"
# « (Groupe) Nom : [x,y] » ; l'OCR lit parfois « ; » pour « : » ou ajoute des espaces dans les crochets.
POSITION_LINE = re.compile(r"\(Groupe\)\s*(?P<name>[^:;]+?)\s*[:;]\s*\[\s*(?P<x>-?\d+)\s*[,.]\s*(?P<y>-?\d+)\s*\]")
INPUT_FROM_BOTTOM = 23  # px de référence : milieu de la zone de saisie du chat, au-dessus du bas de la fenêtre
LINES_HEIGHT = 130  # px de référence : bas du chat lu (les dernières lignes, au-dessus de la zone de saisie)
READ_ZOOMS = (3, 4)  # agrandissements lus : 2 fois, l'OCR prenait parfois le crochet « ] » pour un 1


def chat_input_point(frame: Frame) -> Point:
    """Milieu de la zone de saisie du chat, en bas à gauche (le chat prend la largeur à gauche de la scène)."""
    layout = frame.layout
    return round(layout.map_box[0] / 2), round(layout.height - layout.length(INPUT_FROM_BOTTOM))


def last_lines_image(frame: Frame, zoom: float = 3) -> np.ndarray:
    """Bas du chat du dessous (sans la zone de saisie), agrandi `zoom` fois pour l'OCR."""
    layout = frame.layout
    bottom = round(layout.height - layout.length(2 * INPUT_FROM_BOTTOM))
    top = max(0, round(bottom - layout.length(LINES_HEIGHT)))
    crop = frame.image[top:bottom, 0 : layout.map_box[0]]
    return cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)


def ocr_available() -> bool:
    try:
        import winrt.windows.media.ocr  # noqa: F401
    except ImportError:
        return False
    return True


def ocr_lines(image: np.ndarray) -> list[str] | None:
    """Lignes de texte de l'image (BGR) lues par l'OCR de Windows ; None sans les paquets ou sans moteur."""
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
    except ImportError:
        return None

    async def recognize() -> list[str] | None:
        bgra = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
        writer = DataWriter()
        writer.write_bytes(bgra.tobytes())
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                        bgra.shape[1], bgra.shape[0])
        engine = OcrEngine.try_create_from_language(Language("fr")) or OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            return None
        result = await engine.recognize_async(bitmap)
        return [line.text for line in result.lines]

    return asyncio.run(recognize())


def parse_position(lines: list[str], name: str | None = None) -> tuple[int, int] | None:
    """Coordonnées de la dernière ligne « (Groupe) Nom : [x,y] » (de `name` s'il est donné)."""
    for line in reversed(lines):
        match = POSITION_LINE.search(line)
        if match and (name is None or match["name"].strip().lower() == name.lower()):
            return int(match["x"]), int(match["y"])
    return None


def read_position(frame: Frame, name: str | None = None) -> tuple[int, int] | None:
    """Position lue deux fois (agrandie 3 et 4 fois), gardée seulement si les deux lectures concordent : agrandie 2 fois,
    « [6,2] » était parfois lu « [6,21 » (crochet pris pour un 1, 16/10), une autre carte qui existe."""
    found = set()
    for zoom in READ_ZOOMS:
        lines = ocr_lines(last_lines_image(frame, zoom))
        found.add(parse_position(lines, name) if lines else None)
    return found.pop() if len(found) == 1 else None


# --- Chat de combat (zone du haut) : « Nom lance Sort. » ---------------------------------------------------------
# 16/10 : chaque sort parti écrit aussitôt « Dinarpepe lance Lancer de Pièces. » en vert en bas de la zone du haut du
# chat (canal de combat activé) ; un sort refusé (hors de portée, pas de ligne de vue) n'écrit rien. Lu par l'OCR de
# Windows : les noms et sorts sont bien lus, les chiffres verts des dégâts non (inutiles ici).
COMBAT_PANE = (36, 388)  # px de référence : haut et bas de la zone du haut du chat (au-dessus du séparateur)
COMBAT_LINES_HEIGHT = 120  # px de référence : ses dernières lignes, les seules lues


def _plain(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", text)
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).lower().split())


def combat_lines_image(frame: Frame, zoom: float = 3) -> np.ndarray:
    layout = frame.layout
    bottom = round(layout.length(COMBAT_PANE[1]))
    top = max(round(layout.length(COMBAT_PANE[0])), round(bottom - layout.length(COMBAT_LINES_HEIGHT)))
    crop = frame.image[top:bottom, 0 : layout.map_box[0]]
    return cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)


def combat_lines(frame: Frame) -> list[str] | None:
    """Dernières lignes du chat de combat ; None sans OCR."""
    return ocr_lines(combat_lines_image(frame))


def cast_count(lines: list[str], name: str, spell: str) -> int:
    """Lignes « Nom lance Sort. » parmi `lines` (accents, majuscules et espaces indifférents)."""
    wanted = _plain(f"{name} lance {spell}")
    return sum(1 for line in lines if _plain(line).startswith(wanted))


def cast_announced(before: list[str], after: list[str], name: str, spell: str) -> bool:
    """Le sort vient d'être annoncé : une ligne « Nom lance Sort. » de plus qu'avant le clic. Zone pleine et même sort
    relancé, la plus ancienne annonce peut défiler en même temps : rien n'est conclu (les PA tranchent, comme avant)."""
    return cast_count(after, name, spell) > cast_count(before, name, spell)
