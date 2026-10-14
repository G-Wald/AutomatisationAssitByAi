"""Barre de boutons au-dessus de la timeline (en bas à droite de la carte) : mode tactique et mode créature.

De droite à gauche, en combat : la flèche rouge (calée sur le bord droit), l'œil barré, la boule sombre cerclée de
rouge du mode créature, le cube du mode tactique. Au placement, la barre a d'autres boutons (cadenas, visage, et
en groupe les petits personnages « seulement le groupe ») : le cube y est plus à gauche (36 px par bouton en plus) et la
boule n'y est pas (le visage occupe sa place). Une coche verte posée sur le bouton : mode activé.

La flèche orange ▽ qui marque, dans la timeline, le combattant dont c'est le tour se pose parfois sur un bouton
(04/10 : sur le cube, le modèle entier n'était plus reconnu, mode tactique jamais activé). Chaque bouton est donc
cherché avec un modèle détouré (canal alpha : le fond de la carte change d'un combat à l'autre), état coché et non
coché, en ne comparant que les pixels que la flèche ne cache pas. Modèles tirés des captures sans perte des journaux
du 03 et du 04/10 (le dessin des boutons y est identique au pixel près, à fenêtre égale).

La comparaison se fait en niveaux de gris : le JPEG des journaux étale les couleurs (le fin cercle rouge de la boule
devenait rose et large, écart doublé). L'état se lit ensuite à la couleur : part de verts parmi les pixels visibles
de la coche qui tombent sur le corps du bouton (là où, sans coche, le bouton est gris ; ce qui dépasse du bouton
peut tomber sur de l'herbe).

Mesuré le 04/10 sur 778 captures (journaux du 03 et du 04/10, tests/fixtures ; 1,5 ms pour les deux boutons) :
- cube : trouvé, état juste, sur les 555 captures de combat et de placement où il se voit (89 avec la flèche ▽
  dessus, dont 18 non coché) ; rien sur les 222 captures hors combat ; caché par un grand bouton orange : None ;
- boule : trouvée, état juste, sur les 367 captures de combat sans annotation dessus (78 non cochée, 1 sous la
  flèche) ; rien au placement ni hors combat. Les 174 autres portent, sur la boule, un point vert du débogage (cases
  annotées par le journal, jamais sur une vraie capture) : 124 trouvées cochées (juste), 50 non trouvées.
"""

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from bot.perception.frame import Frame
from bot.perception.layout import Layout, Point
from bot.perception.templates import ASSETS_DIR

# Bande où chercher les boutons (repère de référence) : du cube de la barre la plus longue (placement en groupe, 6
# boutons) à la boule, sans la flèche rouge ni les portraits de la timeline en dessous.
TOOLBAR_BAND = (1640, 664, 1880, 708)
TACTICAL_TEMPLATES = ("barre/mode_tactique_actif.png", "barre/mode_tactique_inactif.png")  # coché, non coché
CREATURE_TEMPLATES = ("barre/mode_creature_actif.png", "barre/mode_creature_inactif.png")
# Écart quadratique moyen au modèle (gris de 0 à 255, sur les pixels visibles du bouton) au-delà duquel ce n'est pas
# le bouton. Mesuré le 04/10 sur les captures des journaux du 03 et du 04/10 et de tests/fixtures (PNG et JPEG) :
# 0 à 9 pour le bon bouton en combat, 13 à 14 pour le cube au placement (dessiné plus à gauche, l'anticrénelage
# diffère un peu), jusqu'à 17 (cube) et 28 (boule) sur des captures réduites ou agrandies de 10 à 20 % ; ailleurs, la
# meilleure place est à 34 au moins hors combat, à 41 pour la boule au placement (le visage est à sa place).
MAX_GAP = 25.0
MIN_SHOWN = 0.25  # part du bouton qui doit rester visible (la flèche ▽ posée sur le cube en cache 62 à 68 %)
OCCLUDER_MIN_HEIGHT = 8  # tache orange plus haute : flèche ▽ (ou grand bouton orange) ; plus plate : bord d'un portrait
# Pixels de la coche, visibles et sur le corps du bouton, en dessous desquels l'état se lit aux modèles (gris) plutôt
# qu'à la couleur. La flèche ▽ sur le cube n'en laisse que 8 (04/10). Les deux lectures concordent sur les 920
# boutons trouvés dans les captures du 04/10.
MIN_CHECK_PIXELS = 6
CHECKED = 0.5  # part de verts parmi ces pixels au-delà de laquelle la coche est là (mesuré : 0,88 à 1 ; sinon 0,02)


@dataclass(frozen=True)
class ToolbarButton:
    point: Point  # où cliquer : le pixel visible du bouton le plus loin de ses bords et de la flèche ▽ (capture)
    room: float  # distance de ce point au bord de la partie visible (pixels) : borne l'écart d'un clic aléatoire
    active: bool  # coche verte dessus : mode activé
    hidden: float  # part du bouton cachée (flèche du tour posée dessus...), 0 à 1
    gap: float  # écart quadratique moyen au modèle retenu (gris de 0 à 255)


@dataclass(frozen=True)
class _Templates:
    greys: tuple[np.ndarray, np.ndarray]  # coché, non coché : gris (float32, centrés sur 0)
    alphas: tuple[np.ndarray, np.ndarray]  # masques des pixels du bouton (float32, 0 ou 1)
    check: np.ndarray  # pixels verts du modèle coché tombant sur le corps du bouton non coché (bool)


def _green(image: np.ndarray) -> np.ndarray:
    """Vert de la coche (0, 150, 0 en PNG), tolérant au JPEG."""
    b, g, r = (image[:, :, k].astype(np.int16) for k in range(3))
    return (g >= 70) & (g >= r + 25) & (g >= b + 25)


@lru_cache
def _templates(names: tuple[str, str], scale: float) -> _Templates:
    """Les deux modèles détourés (BGRA, même cadre) d'un bouton, à l'échelle de la capture."""
    greys, alphas, colors = [], [], []
    for name in names:
        path = ASSETS_DIR / name
        image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED) if path.is_file() else None
        if image is None or image.ndim != 3 or image.shape[2] != 4:
            raise FileNotFoundError(f"Modèle détouré (BGRA) introuvable : {path}")
        bgr, alpha = image[:, :, :3], image[:, :, 3].astype(np.float32) / 255
        if abs(scale - 1) > 0.02:
            bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            alpha = cv2.resize(alpha, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        greys.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) - 128)
        alphas.append((alpha >= 0.5).astype(np.float32))
        colors.append(bgr)
    check = _green(colors[0]) & (alphas[0] > 0) & (alphas[1] > 0)
    return _Templates(tuple(greys), tuple(alphas), check)


def _occluders(region: np.ndarray, layout: Layout) -> np.ndarray:
    """Pixels cachés par la flèche ▽ du tour (contour orange fermé, intérieur blanc) : enveloppe convexe de chaque
    tache orange assez haute, élargie de 2 px (le rectangle englobant cachait aussi les coins du bas, à côté de la
    pointe : 70 % du cube passait pour caché). Le cadre orange d'un portrait, en bas de la bande, est plat : ignoré."""
    b, g, r = (region[:, :, k].astype(np.int16) for k in range(3))
    orange = (r >= 190) & (g >= 60) & (g <= 160) & (b <= 100) & (r - g >= 80)  # tolérant au JPEG
    count, labels, stats, _ = cv2.connectedComponentsWithStats(orange.astype(np.uint8), connectivity=8)
    hidden = np.zeros(region.shape[:2], np.uint8)
    for k in range(1, count):
        height, area = stats[k, cv2.CC_STAT_HEIGHT], stats[k, cv2.CC_STAT_AREA]
        if height >= layout.length(OCCLUDER_MIN_HEIGHT) and area >= layout.area(30):
            ys, xs = np.nonzero(labels == k)
            cv2.fillConvexPoly(hidden, cv2.convexHull(np.column_stack([xs, ys]).astype(np.int32)), 1)
    pad = max(1, round(layout.length(2)))
    return cv2.dilate(hidden, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8)) > 0


def _gap_map(grey: np.ndarray, shown: np.ndarray, template: np.ndarray,
             alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pour chaque position du modèle dans la zone : écart quadratique moyen sur les pixels du bouton non cachés, et
    part du bouton non cachée. Somme des carrés développée en trois corrélations (rapide, et le masque des pixels
    cachés porte sur la capture, ce que le masque de cv2.matchTemplate ne sait pas faire)."""
    count = cv2.matchTemplate(shown, alpha, cv2.TM_CCORR)
    image_sq = cv2.matchTemplate(shown * grey * grey, alpha, cv2.TM_CCORR)
    cross = cv2.matchTemplate(shown * grey, alpha * template, cv2.TM_CCORR)
    template_sq = cv2.matchTemplate(shown, alpha * template * template, cv2.TM_CCORR)
    squares = np.maximum(image_sq - 2 * cross + template_sq, 0)
    return np.sqrt(squares / np.maximum(count, 1)), count / float(alpha.sum())


def _find_button(frame: Frame, names: tuple[str, str]) -> ToolbarButton | None:
    layout = frame.layout
    box = layout.box(*TOOLBAR_BAND)
    region = frame.crop(box)
    templates = _templates(names, round(layout.scale, 2))
    h, w = templates.check.shape
    if region.shape[0] < h or region.shape[1] < w:
        return None
    grey = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).astype(np.float32) - 128
    shown = (~_occluders(region, layout)).astype(np.float32)
    maps = []
    for template, alpha in zip(templates.greys, templates.alphas):
        gap, part = _gap_map(grey, shown, template, alpha)
        maps.append((np.where(part >= MIN_SHOWN, gap, np.inf), part))
    best = min((0, 1), key=lambda k: float(maps[k][0].min()))
    gaps, parts = maps[best]
    y, x = (int(v) for v in np.unravel_index(int(np.argmin(gaps)), gaps.shape))
    if gaps[y, x] > MAX_GAP:
        return None
    visible = shown[y : y + h, x : x + w] > 0
    check = templates.check & visible
    if check.sum() >= MIN_CHECK_PIXELS:
        active = float(_green(region[y : y + h, x : x + w])[check].mean()) >= CHECKED
    else:  # coche presque toute cachée : le modèle le plus proche (à un pixel près), en gris
        near = [float(m[0][max(0, y - 1) : y + 2, max(0, x - 1) : x + 2].min()) for m in maps]
        active = near[0] <= near[1]
    (px, py), room = _click_point((templates.alphas[best] > 0) & visible)
    return ToolbarButton((box[0] + x + px, box[1] + y + py), room, active, round(1 - float(parts[y, x]), 2),
                         round(float(gaps[y, x]), 1))


def _click_point(inside: np.ndarray) -> tuple[Point, float]:
    """Pixel de `inside` le plus loin de son bord (le plus proche du centre en cas d'égalité), et cette distance.
    Le centre de la partie visible tombait sur la flèche ▽ quand elle coupe le cube en deux."""
    padded = cv2.copyMakeBorder(inside.astype(np.uint8), 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    distance = cv2.distanceTransform(padded, cv2.DIST_L2, 3)[1:-1, 1:-1]
    ys, xs = np.nonzero(inside)
    cy, cx = ys.mean(), xs.mean()
    far_y, far_x = np.nonzero(distance >= distance.max() - 0.5)
    k = int(np.argmin((far_y - cy) ** 2 + (far_x - cx) ** 2))
    return (int(far_x[k]), int(far_y[k])), round(float(distance.max()), 1)


def find_tactical_button(frame: Frame) -> ToolbarButton | None:
    """Cube du mode tactique (combat et placement) ; actif : coche verte dessus. None : barre absente (hors combat)
    ou bouton trop caché. Quand la flèche ▽ est posée dessus, l'état se lit sur le haut de la coche, qui dépasse ;
    ui.tactical_mode (aspect de la carte) reste la référence pour savoir si le mode est actif."""
    return _find_button(frame, TACTICAL_TEMPLATES)


def find_creature_button(frame: Frame) -> ToolbarButton | None:
    """Boule du mode créature (combat seulement : pas de bouton au placement) ; actif : coche verte dessus."""
    return _find_button(frame, CREATURE_TEMPLATES)
