"""Fenêtres ouvertes par-dessus la carte (résultat du combat, niveau supérieur...).

Deux niveaux de reconnaissance :
- fenêtres connues : décrites dans assets/popups.yaml, avec un modèle qui les reconnaît
  et le bouton (ou la touche) qui les ferme ;
- fenêtres inconnues : toute grande surface beige posée sur la carte.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

import cv2
import numpy as np

from bot.perception.blobs import find_blobs
from bot.perception.colors import UI_BEIGE, UI_BEIGE_DARK, near, orange_mask
from bot.perception.frame import Frame
from bot.perception.layout import REF_HEIGHT, Box, Point
from bot.perception.templates import ASSETS_DIR, load_template, match_template

POPUPS_FILE = ASSETS_DIR / "popups.yaml"
# Fenêtre à tableaux : ses lignes sombres découpent le fond beige. Résultat d'un combat à 4 contre 5 : 28 % de beige
# dans son rectangle (03/10, fenêtre restée ouverte 7 minutes, la carte cachée) ; 0,3 avant.
WINDOW_FILL = 0.2


@dataclass(frozen=True)
class PopupSpec:
    name: str
    detect: str  # modèle qui reconnaît la fenêtre
    close: str = ""  # modèle du bouton qui la ferme...
    key: str = ""  # ... ou touche qui la ferme
    threshold: float = 0.85
    title_above: float = 120  # px de référence au-dessus de la surface beige où chercher le titre (`detect`)
    detect_also: tuple[str, ...] = ()  # autres modèles du même titre (autre qualité graphique du jeu)
    # Titre OU bouton au seuil suffit si l'autre atteint au moins pair_threshold (0 : les deux au seuil). 20/10
    # (journal 201321) : résultat du combat à 0,874 (titre) et 0,88 (bouton) sur la capture JPEG, pas reconnu sur la
    # capture du jeu : resté ouvert, l'équipe a cherché des groupes derrière lui.
    pair_threshold: float = 0.0


@dataclass(frozen=True)
class PopupMatch:
    spec: PopupSpec
    close_point: Point | None  # None : fermer avec la touche
    close_spread: tuple[float, float] = (0.0, 0.0)  # zone de clic aléatoire dans le bouton
    box: Box | None = None  # surface beige de la fenêtre


def load_popup_specs(path: Path = POPUPS_FILE) -> list[PopupSpec]:
    with path.open(encoding="utf-8") as f:
        entries = (yaml.safe_load(f) or {}).get("popups") or []
    return [PopupSpec(**{**entry, "detect_also": tuple(entry.get("detect_also") or ())}) for entry in entries]


QUIT_TEXT = "Cette action va quitter ta partie en cours"
QUIT_TEXT_SIZE, QUIT_BUTTON_SIZE = 22, 18  # Verdana, repère de référence (captures du 11/10)


def find_quit_dialog_no(frame: Frame) -> Point | None:
    """Fenêtre « Attention : Cette action va quitter ta partie en cours, veux-tu continuer ? Oui / Non » : centre du
    bouton « Non » (à droite de « Oui », sur la même ligne), None si la fenêtre n'est pas là ou si « Non » n'est pas sûr.

    11/10 : un Échap sans menu à fermer a ouvert le Menu principal, puis un clic « ailleurs » est tombé sur
    « Déconnecter » : cette fenêtre s'est ouverte chez trois personnages. Jamais « Oui »."""
    from bot.perception.text import find_text  # import local : text.py est lourd (rendu des polices)

    box = frame.layout.map_box
    if find_text(frame, QUIT_TEXT, box, QUIT_TEXT_SIZE, dark=True, threshold=0.75) is None:
        return None
    no = find_text(frame, "Non", box, QUIT_BUTTON_SIZE, threshold=0.8)
    yes = find_text(frame, "Oui", box, QUIT_BUTTON_SIZE, threshold=0.8)
    if no is None or yes is None:
        return None
    (nx, ny), (yx, yy) = no[0], yes[0]
    if nx <= yx + frame.layout.length(100) or abs(ny - yy) > frame.layout.length(20):
        return None
    return no[0]


def find_known_popup(frame: Frame, specs: list[PopupSpec]) -> PopupMatch | None:
    """Fenêtre connue ouverte. On ne cherche ses modèles qu'autour d'une grande surface beige (une fenêtre) :
    les chercher sur tout l'écran prenait 0,2 s, à chaque coup d'œil. Le titre n'est cherché que dans le haut de la
    surface (son bandeau est juste au-dessus du fond beige) : une étendue de sable ne coûte presque rien.

    Types de fenêtre dans l'ordre de popups.yaml (celle du dessus d'abord), surfaces beiges ensuite : 20/10 (journaux
    205623 et 211515), « Informations — Tu passes niveau 14 » ouverte par-dessus le résultat du combat ; la plus grande
    surface (le résultat) passait d'abord, « Fermer » était cliqué sous la fenêtre du niveau, sans effet, et l'équipe
    restait bloquée derrière les deux fenêtres."""
    margin = round(frame.layout.length(120))
    windows = _beige_windows(frame, min_fill=WINDOW_FILL)
    for spec in specs:
        for window in windows:
            found = _match_popup(frame, spec, window, margin)
            if found is not None:
                return found
    return None


def _match_popup(frame: Frame, spec: PopupSpec, window: Box, margin: int) -> PopupMatch | None:
    x0, y0, x1, y1 = window
    box = (max(0, x0 - margin), max(0, y0 - margin), min(frame.layout.width, x1 + margin),
           min(frame.layout.height, y1 + 2 * margin))  # le bouton « Fermer » est sous la fenêtre
    # 16/10, « Combats en cours » : seul le bas de la fenêtre est du beige des fenêtres, le titre est 340 px plus
    # haut (title_above de la fenêtre dans popups.yaml).
    above = round(frame.layout.length(spec.title_above))
    title = (max(0, x0 - margin), max(0, y0 - above), min(frame.layout.width, x1 + margin),
             min(frame.layout.height, y0 + margin))
    low = min(spec.pair_threshold or spec.threshold, spec.threshold)
    title_score = max((found[1] for name in (spec.detect, *spec.detect_also)
                       if (found := match_template(frame, name, box=title, threshold=low)) is not None), default=None)
    if title_score is None:
        return None
    if spec.key:
        return PopupMatch(spec, None, box=window) if title_score >= spec.threshold else None
    button = match_template(frame, spec.close, box=box, threshold=low)
    if button is None or max(title_score, button[1]) < spec.threshold:
        return None  # l'un des deux au moins doit être sûr
    height, width = load_template(spec.close).shape[:2]
    return PopupMatch(spec, button[0], frame.layout.spread(width * 0.3, height * 0.25), window)


# Diagnostic d'une fenêtre qui revient sans cesse, ou d'une surface beige inconnue (20/10) : boutons de fermeture
# connus cherchés plus largement, puis la croix orange du bandeau de titre. « Ok » d'abord : la fenêtre du niveau
# supérieur s'ouvre par-dessus le résultat du combat, dont le « Fermer » ne répond pas tant qu'elle est là.
GENERIC_BUTTONS = ("informations_ok.png", "challenge_fermer_x.png", "fin_de_combat_fermer.png")
GENERIC_THRESHOLD = 0.75
TITLE_CROSS_SIZE = (16, 34)  # px de référence : côté de la croix orange (26 px sur le résultat du combat, 20/10)
TITLE_CROSS_BAND = (60, 12)  # px de référence au-dessus et au-dessous du haut de la surface beige : bandeau du titre


def find_generic_close(frame: Frame, window: Box | None = None) -> tuple[Point, str] | None:
    """Bouton qui ferme une fenêtre qu'on ne sait pas (ou plus) fermer : « Fermer », « Ok » ou la croix de « Challenge
    en cours » au seuil GENERIC_THRESHOLD, cherchés autour de la surface beige `window` (sinon de chacune des grandes
    surfaces beiges) ; à défaut, la croix orange en haut à droite du bandeau de titre. (point, ce que c'est), None sans
    rien de sûr. Jamais rien sur la carte elle-même : seulement des boutons orange de l'interface."""
    layout = frame.layout
    margin = round(layout.length(120))
    windows = [window] if window is not None else _beige_windows(frame, min_fill=WINDOW_FILL)
    for x0, y0, x1, y1 in windows:
        box = (max(0, x0 - margin), max(0, y0 - margin), min(layout.width, x1 + margin),
               min(layout.height, y1 + 2 * margin))
        for name in GENERIC_BUTTONS:
            found = match_template(frame, name, box=box, threshold=GENERIC_THRESHOLD)
            if found is not None:
                return found[0], name
        cross = find_title_cross(frame, (x0, y0, x1, y1))
        if cross is not None:
            return cross, "croix orange du titre"
    return None


def find_title_cross(frame: Frame, window: Box) -> Point | None:
    """Croix orange en haut à droite du bandeau de titre d'une fenêtre (à droite du « _ » orange qui la réduit) :
    la tache orange carrée la plus à droite du bandeau, juste au-dessus de la surface beige."""
    layout = frame.layout
    x0, y0, x1, y1 = window
    up, down = (round(layout.length(v)) for v in TITLE_CROSS_BAND)
    side = round(layout.length(40))
    band = (max(0, x0 - side), max(0, y0 - up), min(layout.width, x1 + side), min(layout.height, y0 + down))
    low, high = (layout.length(v) for v in TITLE_CROSS_SIZE)
    # Dans le quart droit du bandeau seulement : les jauges d'XP orange du résultat du combat dépassent à gauche de
    # « Informations » posée dessus (20/10), pas de croix là.
    right = x1 - (x1 - x0) / 4
    squares = [b for b in find_blobs(orange_mask(frame.crop(band)), offset=band[:2], close=3)
               if low <= b.w <= high and low <= b.h <= high and abs(b.w - b.h) <= layout.length(6) and b.fill >= 0.5
               and b.center[0] >= right]
    if not squares:
        return None
    cross = max(squares, key=lambda b: b.x)
    return round(cross.center[0]), round(cross.center[1])


def find_context_menu(frame: Frame) -> Box | None:
    """Menu ouvert par un clic sur un personnage ou un monstre (« Baffer... », « Monstres / Attaquer... ») :
    rectangle beige plein. Les nuages, pierres et chemins beiges ne remplissent pas leur rectangle (0,4 au plus,
    contre 0,94 pour un menu, mesuré le 28/09)."""
    layout = frame.layout
    box = layout.map_box
    for blob in find_blobs(near(frame.crop(box), UI_BEIGE, 10), offset=box[:2], close=3):
        if layout.length(110) <= blob.w <= layout.length(450) and layout.length(45) <= blob.h <= layout.length(400) \
                and blob.fill >= 0.8:
            return blob.x, blob.y, blob.x + blob.w, blob.y + blob.h
    return None


def find_small_menu(frame: Frame, around: Point | None = None, radius: float = 250) -> Box | None:
    """Petit menu ouvert par un clic (une seule entrée beige, comme « Pipelette Nhin / Parler » sur un PNJ, ou
    « Combat / Rejoindre ») : trop bas pour find_context_menu. Entrée beige pleine d'au moins 18 px de haut (33 px pour
    « Parler », le 01/10), près de `around` (le clic) s'il est donné."""
    layout = frame.layout
    box = layout.map_box
    for blob in find_blobs(near(frame.crop(box), UI_BEIGE, 10), offset=box[:2], close=3):
        if not (layout.length(90) <= blob.w <= layout.length(450) and layout.length(18) <= blob.h <= layout.length(400)
                and blob.fill >= 0.8):
            continue
        x, y = blob.center
        if around is None or abs(x - around[0]) + abs(y - around[1]) <= layout.length(radius):
            return blob.x, blob.y, blob.x + blob.w, blob.y + blob.h
    return None


FLASH_MENU = "menu_flash.png"  # « DOFUS RETRO Client », 1re ligne du menu de Flash (sans le numéro de version)
FLASH_MENU_SCALE = 1057 / REF_HEIGHT  # découpé tel quel dans une fenêtre de 1057 px de haut (14/10)
FLASH_MENU_THRESHOLD = 0.8  # 1,0 sur sa capture ; 0,5 au plus sur les autres captures entières (14/10)
FLASH_MENU_REACH = (350, 300)  # px de référence autour du clic : le menu s'ouvre au clic (à gauche ou au-dessus près
# d'un bord), d'environ 300 x 220
FLASH_CLOSE_REF = (1465, 991)  # repère de référence : fond beige en bas à gauche de la barre de sorts (15/10)
FLASH_CLOSE_IN_COMBAT_REF = (1700, 1000)  # sous la 2e rangée de sorts : beige en combat (bouton de fin de tour à gauche)
FLASH_CLOSE_CLEARANCE = 6  # px de référence autour de ce point : beige uni exigé


def find_flash_menu(frame: Frame, around: Point | None = None, anywhere: bool = False) -> Point | None:
    """Menu contextuel de Flash (« DOFUS RETRO Client », « Flash player WIN », « Qualité Flash », « Options »...) :
    ouvert par un clic droit tombé à côté d'un monstre (14/10, un Piou, clic de sonde en arrière-plan). Échap ne le
    ferme pas (il ouvre le Menu principal derrière) : il se ferme d'un clic ailleurs (flash_close_point). Centre de sa
    1re ligne, None s'il n'est pas là. Cherché autour de `around` (le clic) s'il est donné, sinon au-dessus des
    grandes surfaces beiges de la carte (ses autres lignes), pas sur tout l'écran (0,16 s) ; `anywhere` : sur tout
    l'écran quand même (17/10 : ouvert sur la timeline, loin de toute surface beige, le clic qui l'a ouvert oublié)."""
    layout = frame.layout
    if anywhere:
        boxes = [(0, 0, layout.width, layout.height)]
    elif around is not None:
        rx, ry = layout.spread(*FLASH_MENU_REACH)
        boxes = [(round(around[0] - rx), round(around[1] - ry), round(around[0] + rx), round(around[1] + ry))]
    else:
        up, side = layout.length(120), layout.length(20)
        boxes = [(round(x0 - side), round(y0 - up), round(x1 + side), round(y0 + side))
                 for x0, y0, x1, y1 in _beige_windows(frame, min_fill=0.5)]
    template = load_template(FLASH_MENU)
    factors = {1.0, round(layout.scale / FLASH_MENU_SCALE, 3)}  # sa taille suit-elle la fenêtre ? les deux essayées
    for x0, y0, x1, y1 in boxes:
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(layout.width, x1), min(layout.height, y1)
        region = frame.crop((x0, y0, x1, y1))
        for factor in factors:
            sized = template if factor == 1.0 else cv2.resize(template, None, fx=factor, fy=factor,
                                                                 interpolation=cv2.INTER_AREA)
            th, tw = sized.shape[:2]
            if region.shape[0] < th or region.shape[1] < tw:
                continue
            _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(region, sized, cv2.TM_CCOEFF_NORMED))
            if score >= FLASH_MENU_THRESHOLD:
                return x0 + x + tw // 2, y0 + y + th // 2
    return None


def flash_close_point(frame: Frame) -> Point | None:
    """Où cliquer pour fermer le menu de Flash sans rien déclencher : le fond beige en bas à gauche de la barre de
    sorts, sous aucun raccourci. Un clic dans le chat ne le ferme pas (15/10, deux fois en jeu : le chat n'est pas
    dans la scène Flash). En combat, le bouton de fin de tour y est : le fond beige sous la 2e rangée de sorts, à
    droite de ce bouton (17/10 : menu de Flash ouvert en rejoignant un combat). None si aucun des deux n'est beige uni
    (une fenêtre assombrit l'écran)."""
    r = round(frame.layout.length(FLASH_CLOSE_CLEARANCE))
    for ref in (FLASH_CLOSE_REF, FLASH_CLOSE_IN_COMBAT_REF):
        x, y = frame.layout.point(*ref)
        patch = frame.image[max(0, y - r) : y + r + 1, max(0, x - r) : x + r + 1]
        if patch.size and near(patch, UI_BEIGE, 10).all():
            return x, y
    return None


NPC_BUBBLE = (206, 255, 255)  # fond jaune pâle uni (BGR) de la bulle de dialogue d'un PNJ
NPC_BUBBLE_MIN = (300, 120)  # px de référence : bulle de Matu Vuh 610 x 210 en 1057 de haut
NPC_CROSS_ZONE = 50  # px de référence : coin haut droit de la bulle où chercher la croix noire


TROPHY_CROSS = (21.5, -30.5)  # px de référence : croix de la carte des récompenses de trophées, depuis son icône


def find_trophy_cross(frame: Frame) -> Point | None:
    """Carte « récompenses de trophées » (coffre orange sur fond blanc, croix en haut à droite), posée sur la carte à
    la connexion (19/10, Pesopepe ; elle se ferme par sa croix) : centre de sa croix, None sans elle."""
    found = match_template(frame, "trophees_icone.png", box=frame.layout.map_box, threshold=0.85)
    if found is None:
        return None
    (x, y), _ = found
    return round(x + frame.layout.length(TROPHY_CROSS[0])), round(y + frame.layout.length(TROPHY_CROSS[1]))


def find_npc_dialog(frame: Frame) -> Point | None:
    """Bulle de dialogue d'un PNJ (nom, texte, croix « × » en haut à droite), ouverte par un clic droit sur lui. 17/10
    (journal 175051) : clic de sonde sur Matu Vuh, debout près d'un groupe ; la bulle cachait le décor (les 7 suiveurs
    crus sur une autre carte) et le personnage ne marchait plus (sortie crue bloquée), session arrêtée. Ni beige de
    l'interface ni menu : jaune pâle uni. Centre de sa croix, None sans bulle."""
    layout = frame.layout
    box = layout.map_box
    small = cv2.resize(frame.crop(box), None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    min_w, min_h = (layout.length(v) / 4 for v in NPC_BUBBLE_MIN)
    for blob in find_blobs(near(small, NPC_BUBBLE, 8), close=2):
        if blob.w < min_w or blob.h < min_h or blob.fill < 0.7:
            continue
        right, top = box[0] + 4 * (blob.x + blob.w), box[1] + 4 * blob.y
        zone = round(layout.length(NPC_CROSS_ZONE))
        x0, y0 = max(0, right - zone), max(0, top - zone // 5)
        corner = frame.image[y0 : top + zone, x0 : right + zone // 5]
        # La croix : tache noire de 10 à 30 px, à part du trait noir qui borde la bulle (long, fin).
        for mark in find_blobs(corner.max(axis=2) < 60, offset=(x0, y0)):
            if layout.length(8) <= mark.w <= layout.length(30) and layout.length(8) <= mark.h <= layout.length(30):
                return round(mark.center[0]), round(mark.center[1])
    return None


def find_unknown_window(frame: Frame) -> Box | None:
    """Grande surface beige rectangulaire sur la carte (les chemins de pierre et les menus sont plus petits)."""
    return _beige_window(frame, min_fill=0.5)


def _beige_window(frame: Frame, min_fill: float) -> Box | None:
    windows = _beige_windows(frame, min_fill)
    return windows[0] if windows else None


def _beige_windows(frame: Frame, min_fill: float) -> list[Box]:
    """Grandes surfaces beiges assez pleines, la plus grande d'abord (3 au plus)."""
    layout = frame.layout
    box = layout.map_box
    small = cv2.resize(frame.crop(box), None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)  # 16 fois moins de pixels
    found = []
    beige = near(small, UI_BEIGE, 10) | near(small, UI_BEIGE_DARK, 10)  # « Options » : le fond plus sombre
    for blob in find_blobs(beige, close=2):
        if 4 * blob.w >= layout.length(250) and 4 * blob.h >= layout.length(120) and blob.fill >= min_fill:
            x, y = box[0] + 4 * blob.x, box[1] + 4 * blob.y
            found.append((blob.w * blob.h, (x, y, x + 4 * blob.w, y + 4 * blob.h)))
    return [window for _, window in sorted(found, reverse=True)[:3]]
