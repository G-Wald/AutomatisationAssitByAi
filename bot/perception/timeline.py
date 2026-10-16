"""Portrait du combattant qui joue, dans la timeline (en bas à droite de la carte) : celui encadré d'orange.

Mode équipe : ui.current_fighter_team dit de quelle équipe est le combattant qui joue ; son portrait dit QUI il est.
Chaque personnage y est dessiné en pied, avec son équipement : comparé au portrait retenu pour chaque personnage de
l'équipe, il donne directement la fenêtre où jouer. Le portrait se compare réduit à 12 x 18 (écart moyen des
couleurs, 0 à 255), sans son cadre ni la barre de vie à sa droite (elle baisse avec la vie). Mesuré sur les captures
de combat de tests/fixtures : 0 à 3 pour le même personnage (même dans un autre combat, à une autre place de la
timeline), 20 avec un autre chapeau, plus de 60 pour un Bouftou.

Le cadre orange peut être trouvé un pixel à côté du dessin : 11/10, un ennemi tué par Brawli sort de la timeline, son
portrait glisse d'une place, écart 12 (pas le même personnage) au lieu de 3 ; le bot a cru son tour fini en plein
milieu et l'a recommencé avec ses compteurs à zéro (Tempête de Puissance et Amplification relancées, refusées). Le
dessin est donc aussi relevé décalé d'un pixel dans chaque sens, et l'écart retenu est le plus petit (13 au moins entre
deux personnages différents des captures).
"""

from dataclasses import dataclass

import cv2
import numpy as np

from bot.perception.blobs import find_blobs
from bot.perception.colors import TEAMS, orange_mask, team_mask
from bot.perception.frame import Frame
from bot.perception.layout import Box
from bot.perception.ui import TIMELINE_ZONE

PORTRAIT_INNER = (7, 8, 45, 62)  # dessin dans le cadre orange (gauche, haut, droite, bas ; référence), sans la barre de vie
SIGNATURE_SIZE = (12, 18)
SAME_PORTRAIT = 10.0  # écart moyen au-delà duquel ce n'est pas le même personnage
SHIFT = 1  # décalage toléré entre le cadre et le dessin, en pixels de la référence


@dataclass(frozen=True, eq=False)
class Portrait:
    box: Box  # cadre orange, dans la capture
    team: str | None  # couleur de sa barre de vie
    image: np.ndarray  # dessin réduit (float32, BGR)
    shifted: tuple[np.ndarray, ...] = ()  # le même, relevé décalé d'un pixel dans chaque sens (8 voisins)


def current_portrait(frame: Frame) -> Portrait | None:
    """Portrait encadré d'orange (mêmes critères que ui.current_fighter_team) ; None hors combat."""
    layout = frame.layout
    box = layout.box(*TIMELINE_ZONE)
    for blob in find_blobs(orange_mask(frame.crop(box)), offset=box[:2]):
        if not (layout.length(50) <= blob.w <= layout.length(72) and layout.length(58) <= blob.h <= layout.length(82)):
            continue
        frame_box = (blob.x, blob.y, blob.x + blob.w, blob.y + blob.h)
        counts = {team: int(team_mask(frame.crop(frame_box), team).sum()) for team in TEAMS}
        team = max(counts, key=counts.get)
        left, top, right, bottom = (round(layout.length(v)) for v in PORTRAIT_INNER)
        image = _signature(frame, (blob.x + left, blob.y + top, blob.x + right, blob.y + bottom))
        if image is None:
            continue
        step = max(1, round(layout.length(SHIFT)))
        shifted = tuple(s for dx in (-step, 0, step) for dy in (-step, 0, step) if dx or dy
                        for s in [_signature(frame, (blob.x + left + dx, blob.y + top + dy, blob.x + right + dx,
                                                     blob.y + bottom + dy))] if s is not None)
        return Portrait(frame_box, team if counts[team] >= layout.area(40) else None, image, shifted)
    return None


def _signature(frame: Frame, box: Box) -> np.ndarray | None:
    x0, y0, x1, y1 = box
    height, width = frame.image.shape[:2]
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x1 <= x0 or y1 <= y0:
        return None
    return cv2.resize(frame.crop(box), SIGNATURE_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)


def portrait_gap(a: Portrait, b: Portrait) -> float:
    """Écart entre deux portraits, le plus petit à un pixel de décalage près (voir plus haut)."""
    gaps = [np.abs(a.image - b.image).mean()]
    gaps += [np.abs(a.image - s).mean() for s in b.shifted] + [np.abs(s - b.image).mean() for s in a.shifted]
    return float(min(gaps))


def same_portrait(a: Portrait | None, b: Portrait | None) -> bool:
    return a is not None and b is not None and portrait_gap(a, b) <= SAME_PORTRAIT


PORTRAIT_SIZE = ((15, 48), (48, 66))  # fond blanc d'un portrait de la timeline, en px de référence (largeur, hauteur) ;
# 15 de large : le dernier, à moitié sous la flèche de défilement
PORTRAIT_MARGIN = (6, 6, 16, 8)  # autour de ce fond : cadre, et barre de vie à droite (gauche, haut, droite, bas)


def timeline_portraits(frame: Frame) -> list[Box]:
    """Portraits des combattants dans la timeline (fond blanc, 38 x 56 de référence), avec leur cadre et leur barre
    de vie : ce qu'ils cachent de la carte. 2 portraits en duel ; 9 en équipe de 8, sur 600 px de large (15/10)."""
    layout = frame.layout
    x0, y0, x1, y1 = layout.box(*TIMELINE_ZONE)
    white = (frame.image[y0:y1, x0:x1].min(axis=2) >= 225).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    (w_min, w_max), (h_min, h_max) = PORTRAIT_SIZE
    left, top, right, bottom = (layout.length(v) for v in PORTRAIT_MARGIN)
    return [(round(x0 + x - left), round(y0 + y - top), round(x0 + x + w + right), round(y0 + y + h + bottom))
            for x, y, w, h, _ in stats[1:count]
            if layout.length(w_min) <= w <= layout.length(w_max) and layout.length(h_min) <= h <= layout.length(h_max)]
