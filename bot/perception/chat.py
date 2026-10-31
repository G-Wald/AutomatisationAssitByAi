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
POSITION_LINE = re.compile(r"\(Groupe\)\s*(?P<name>[^:;]+?)\s*[:;]\s*\[\s*(?P<x>-?\d+)\s*[,.]\s*(?P<y>-?\d+)\s*(?P<close>\])?")
LINE_TIME = re.compile(r"\[\s*(?P<h>\d{1,2})\s*[:.;]?\s*(?P<m>\d{2})\s*\]")  # heure de la ligne : « [19:16] »
# 23/10 : « [11,-27] » lu « [11,-271 » aux deux agrandissements, crochet fermant pris pour un 1 ; Pombro cru introuvable,
# le bot arrêté. Sans crochet, un dernier 1 n'est retiré que si le nombre entier est impossible (au-delà de MAX_COORD) :
# « [6,21 » reste refusé (6,21 existe).
MAX_COORD = 120
INPUT_FROM_BOTTOM = 23  # px de référence : milieu de la zone de saisie du chat, au-dessus du bas de la fenêtre
LINES_HEIGHT = 130  # px de référence : bas du chat lu (les dernières lignes, au-dessus de la zone de saisie)
LOWER_PANE_TOP = 392  # px de référence : haut du chat du dessous (sous le séparateur des deux zones du chat)
READ_ZOOMS = (3, 4)  # agrandissements lus : 2 fois, l'OCR prenait parfois le crochet « ] » pour un 1


def chat_input_point(frame: Frame) -> Point:
    """Milieu de la zone de saisie du chat, en bas à gauche (le chat prend la largeur à gauche de la scène)."""
    layout = frame.layout
    return round(layout.map_box[0] / 2), round(layout.height - layout.length(INPUT_FROM_BOTTOM))


def last_lines_image(frame: Frame, zoom: float = 3, whole: bool = False) -> np.ndarray:
    """Bas du chat du dessous (sans la zone de saisie), agrandi `zoom` fois pour l'OCR ; `whole` : tout le chat du
    dessous, depuis son séparateur (juste après la connexion, presque vide, ses lignes sont en haut)."""
    layout = frame.layout
    bottom = round(layout.height - layout.length(2 * INPUT_FROM_BOTTOM))
    top = round(layout.length(LOWER_PANE_TOP)) if whole else max(0, round(bottom - layout.length(LINES_HEIGHT)))
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


def ocr_boxes(image: np.ndarray) -> list[tuple[str, tuple[float, float, float, float]]] | None:
    """Lignes lues par l'OCR de Windows avec leur cadre (x0, y0, x1, y1) en pixels de `image` ; None sans OCR. Pour
    cliquer sur un texte (entrée « Parler » d'un menu, réponse dans la bulle d'un PNJ)."""
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
    except ImportError:
        return None

    async def recognize():
        bgra = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
        writer = DataWriter()
        writer.write_bytes(bgra.tobytes())
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                        bgra.shape[1], bgra.shape[0])
        engine = OcrEngine.try_create_from_language(Language("fr")) or OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            return None
        result = await engine.recognize_async(bitmap)
        lines = []
        for line in result.lines:
            rects = [word.bounding_rect for word in line.words]
            if not rects:
                continue
            x0, y0 = min(r.x for r in rects), min(r.y for r in rects)
            x1, y1 = max(r.x + r.width for r in rects), max(r.y + r.height for r in rects)
            lines.append((line.text, (x0, y0, x1, y1)))
        return lines

    return asyncio.run(recognize())


def find_text(frame: Frame, box: tuple[int, int, int, int], wanted: str, zoom: float = 2,
              exact: bool = False) -> Point | None:
    """Centre (pixels de la fenêtre) de la première ligne lue dans `box` qui contient `wanted` (accents, majuscules
    indifférents) ; `exact` : la ligne entière, sans puce ni point final (une réponse dans la bulle d'un PNJ : sa
    question peut contenir les mêmes mots, « je vous téléporte à la sortie ou à l'auberge ») ; None si elle n'y est
    pas ou sans OCR."""
    x0, y0, x1, y1 = (max(0, round(v)) for v in box)
    crop = frame.image[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    lines = ocr_boxes(cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)) or []
    target = _plain(wanted).strip(" .•")
    for text, (a, b, c, d) in lines:
        line = _plain(text).strip(" .•")
        if line == target if exact else target in line:
            return round(x0 + (a + c) / 2 / zoom), round(y0 + (b + d) / 2 / zoom)
    return None


def _older(line: str, since: tuple[int, int]) -> bool:
    """La ligne est d'avant la minute `since` (heure, minute), d'après son heure « [19:16] » ; vrai aussi si l'heure est
    illisible (rien ne prouve qu'elle est récente). Passage de minuit compris."""
    match = LINE_TIME.search(line)
    if match is None:
        return True
    hour, minute = int(match["h"]), int(match["m"])
    if hour > 23 or minute > 59:
        return True
    late = (since[0] * 60 + since[1] - hour * 60 - minute) % (24 * 60)
    return 0 < late < 12 * 60


def parse_position(lines: list[str], name: str | None = None,
                   since: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """Coordonnées de la dernière ligne « (Groupe) Nom : [x,y] » (de `name` s'il est donné) ; `since` (heure, minute) :
    seulement une ligne de cette minute ou après (31/10 : réponse pas encore affichée, Pesopepe pris en 0,-32 d'après
    sa ligne de 18:42 restée plus haut dans le chat ; il était en 1,-32, trajet impossible et bot arrêté)."""
    # 23/10 : l'OCR coupe parfois la ligne après le nom (« ...Koalaloupour : » puis « [10,-28] ») : chaque ligne, puis
    # recollée à la suivante, de la dernière à la première.
    candidates = []
    for index, line in enumerate(lines):
        candidates.append(line)
        if index + 1 < len(lines):
            candidates.append(f"{line} {lines[index + 1]}")
    for line in reversed(candidates):
        match = POSITION_LINE.search(line)
        if not match or (name is not None and match["name"].strip().lower() != name.lower()):
            continue
        if since is not None and _older(line[: match.start()], since):
            continue
        x, y = match["x"], match["y"]
        if match["close"] is None:
            if not (y.endswith("1") and abs(int(y)) > MAX_COORD and y.strip("-")[:-1]):
                continue
            y = y[:-1]
        return int(x), int(y)
    return None


def read_position(frame: Frame, name: str | None = None,
                  since: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """Position lue deux fois (agrandie 3 et 4 fois), gardée seulement si les deux lectures concordent : agrandie 2 fois,
    « [6,2] » était parfois lu « [6,21 » (crochet pris pour un 1, 16/10), une autre carte qui existe. Rien dans les
    dernières lignes : tout le chat du dessous (21/10, juste après la connexion : « (Groupe) Koalaloupour : [11,-28] »
    sous le message de bienvenue, en haut du chat presque vide ; trajet arrêté faute de position de départ). Une
    lecture vide ne contredit pas l'autre (23/10 : agrandie 3 fois, « [18:45] (Groupe) Koalaloupour : [10,-28] » coupée
    en trois lignes ; trajet arrêté) ; deux positions différentes, rien."""
    for whole in (False, True):
        found = set()
        for zoom in READ_ZOOMS:
            lines = ocr_lines(last_lines_image(frame, zoom, whole))
            found.add(parse_position(lines, name, since) if lines else None)
        found.discard(None)
        if len(found) == 1:
            return found.pop()
    return None


# 23/10 : « Impossible de rejoindre le combat car l'équipe est fermée (ou limitée au groupe du joueur principal). » :
# le suiveur n'est pas dans le groupe du principal (resté dans l'ancien groupe après une déconnexion du principal).
JOIN_REFUSED = "equipe est fermee"


def join_refused(frame: Frame) -> bool:
    """Le chat du dessous dit que l'équipe du combat est fermée au personnage (pas dans le groupe du principal)."""
    if not ocr_available():
        return False
    lines = ocr_lines(last_lines_image(frame, 3, whole=True))
    return JOIN_REFUSED in _plain(" ".join(lines[-6:]))  # la phrase tient sur deux lignes


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


def died(lines: list[str]) -> bool:
    """Le personnage de cette fenêtre vient de mourir : « Tu as perdu N points d'énergie. » (17/10 : écrit seulement chez
    lui, avec « Nom est mort ! ») dans les dernières lignes du chat de combat."""
    return any("perdu" in _plain(line) and "energie" in _plain(line) for line in lines)
