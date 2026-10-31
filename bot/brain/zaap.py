"""Zaap : d'une carte à zaap vers un autre zaap connu du personnage, quand aucun chemin à pied n'existe (Journey).

Déroulé validé à la main le 25/10 (Elseiso, Astrub 4,-19 vers Le Berceau 1,-32, captures e4 à e8) :
1. clic sur la case du zaap (référence complétée, objets de nature « zaap ») : menu « Zaap / Utiliser » au clic ;
2. clic sur « Utiliser » : le personnage marche jusqu'au zaap, puis la fenêtre « Liste des zaaps connus » s'ouvre ;
3. le nom tapé dans le champ de recherche (blanc, en bas de la fenêtre) ne laisse que les lignes qui le contiennent ;
4. clic sur la ligne : le bouton « Se téléporter » passe de l'orange sombre à l'orange vif ;
5. clic sur « Se téléporter » (coûte des kamas : 130 pour Le Berceau depuis Astrub) : nouvelle carte.

Jamais de clic à l'aveugle : chaque menu ou fenêtre est vu chargé avant d'y cliquer (OCR Windows pour les textes,
couleurs pour le champ blanc et les boutons orange) ; au moindre écart, la fenêtre est fermée et le trajet le sait.
Mesures (fenêtre de 1920 x 1057, échelle 1,0486) : entrée « Utiliser » beige 89 x 36 sous l'en-tête « Zaap » ; champ
de recherche blanc pur 332 x 28 ; boutons orange 28 px de haut, 21 px sous le champ.
"""

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from bot.alerts import tell_user
from bot.brain.arena import map_cell
from bot.brain.itinerary import zaap_search_word
from bot.brain.popups import close_any_menu, menu_open
from bot.perception.blobs import find_blobs
from bot.perception.chat import _plain, ocr_available, ocr_boxes
from bot.perception.colors import UI_BEIGE, near, orange_mask
from bot.perception.frame import Frame
from bot.perception.layout import Box, Point

log = logging.getLogger(__name__)

MENU_WAIT = 2.0  # clic sur le zaap -> menu « Zaap / Utiliser »
WINDOW_WAIT = 15.0  # « Utiliser » -> le personnage marche jusqu'au zaap, la liste s'ouvre
LIST_WAIT = 2.0  # nom tapé -> lignes filtrées ; ligne cliquée -> « Se téléporter » vif
TELEPORT_WAIT = 10.0  # « Se téléporter » -> nouvelle carte
POLL = 0.3
ZAAP_UP = 1.5  # demi-hauteurs de case au-dessus du centre : l'arche du zaap, au 2e clic (31/10)
TRIES = 2  # essais du clic sur le zaap et du choix de la ligne (30/10 : deux ratés passagers, passés au second)
# Px de référence (mesures de la fenêtre de 1057 px divisées par 1,0486).
ENTRY_SIZE = ((50, 250), (20, 55))  # entrée beige d'un menu d'objet (largeur, hauteur)
ENTRY_REACH = 220  # le menu s'ouvre au clic
FIELD_MIN_W, FIELD_H = 220, (18, 40)  # champ de recherche blanc
TITLE_BAND = (-560, -480, -150, -410)  # titre « Liste des zaaps connus » : x0, y0 (haut du champ), x1, y1
ROWS_BAND = (-560, -385, -120, -12)  # lignes de la liste : depuis le coin haut gauche du champ
BUTTON_BAND = (-580, 4, 30, 75)  # boutons : x depuis la gauche du champ et sa droite, y depuis le bas du champ
BUTTON_MIN = (120, 18)
SCENE_CHANGED = 12.0  # comme brain/travel.py : écart moyen de deux vues réduites de deux cartes différentes


@dataclass(frozen=True)
class ZaapWindow:
    field: Box  # champ de recherche (blanc)

    def band(self, frame: Frame, dx0: float, dy0: float, dx1: float, dy1: float, bottom: bool = False) -> Box:
        x0, y0, x1, y1 = self.field
        length = frame.layout.length
        top = y1 if bottom else y0
        right = x1 if bottom else x0
        return (max(0, round(x0 + length(dx0))), max(0, round(top + length(dy0))),
                min(frame.layout.width, round(right + length(dx1))), min(frame.layout.height, round(top + length(dy1))))


def _read(frame: Frame, box: Box, zoom: float = 2) -> list[tuple[str, Point]]:
    """Lignes lues par l'OCR dans `box` : (texte simplifié, centre en pixels de la fenêtre)."""
    x0, y0, x1, y1 = box
    crop = frame.image[y0:y1, x0:x1]
    if crop.size == 0:
        return []
    lines = ocr_boxes(cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)) or []
    return [(_plain(text), (round(x0 + (a + c) / 2 / zoom), round(y0 + (b + d) / 2 / zoom)))
            for text, (a, b, c, d) in lines]


def find_use_entry(frame: Frame, around: Point) -> Point | None:
    """Entrée « Utiliser » du menu ouvert par le clic sur le zaap (en-tête « Zaap ») : centre, None si le menu n'est pas
    là (ou si c'est le menu d'autre chose : un joueur debout sur le zaap)."""
    layout = frame.layout
    x0, y0, x1, y1 = layout.map_box
    (wmin, wmax), (hmin, hmax) = ENTRY_SIZE
    for blob in find_blobs(near(frame.crop(layout.map_box), UI_BEIGE, 10), offset=(x0, y0), close=3):
        cx, cy = blob.center
        if not (layout.length(wmin) <= blob.w <= layout.length(wmax) and layout.length(hmin) <= blob.h
                <= layout.length(hmax) and blob.fill >= 0.75):
            continue
        if abs(cx - around[0]) + abs(cy - around[1]) > layout.length(ENTRY_REACH):
            continue
        pad = round(layout.length(8))
        box = (max(x0, blob.x - pad), max(y0, round(blob.y - blob.h * 1.3)), min(x1, blob.x + blob.w + pad),
               min(y1, blob.y + blob.h + pad))
        texts = [text for text, _ in _read(frame, box)]
        if any("utiliser" in t for t in texts) and any("zaap" in t for t in texts):
            return round(cx), round(cy)
    return None


def find_zaap_window(frame: Frame) -> ZaapWindow | None:
    """Fenêtre « Liste des zaaps connus » : son champ de recherche blanc, et le titre lu au-dessus."""
    layout = frame.layout
    x0, y0, _, _ = layout.map_box
    white = frame.crop(layout.map_box).min(axis=2) >= 245
    for blob in find_blobs(white, offset=(x0, y0)):
        if blob.w < layout.length(FIELD_MIN_W) or not layout.length(FIELD_H[0]) <= blob.h <= layout.length(FIELD_H[1]) \
                or blob.fill < 0.9:
            continue
        window = ZaapWindow((blob.x, blob.y, blob.x + blob.w, blob.y + blob.h))
        if any("zaaps" in text for text, _ in _read(frame, window.band(frame, *TITLE_BAND))):
            return window
    return None


def _buttons(frame: Frame, window: ZaapWindow) -> list[tuple[float, float, float, float]]:
    """Boutons orange vif sous la liste (x0, y0, x1, y1), de gauche à droite : « Sauvegarder ce zaap », « Se téléporter »
    (vif seulement quand une ligne est choisie), « Fermer »."""
    box = window.band(frame, BUTTON_BAND[0], BUTTON_BAND[1], BUTTON_BAND[2], BUTTON_BAND[3], bottom=True)
    found = []
    for blob in find_blobs(orange_mask(frame.crop(box)), offset=box[:2], close=2):
        if blob.w >= frame.layout.length(BUTTON_MIN[0]) and blob.h >= frame.layout.length(BUTTON_MIN[1]):
            found.append((blob.x, blob.y, blob.x + blob.w, blob.y + blob.h))
    return sorted(found)


def find_button(frame: Frame, window: ZaapWindow, wanted: str) -> Point | None:
    """Bouton orange vif dont le texte contient `wanted` (« teleporter », « fermer ») : centre, None s'il n'est pas vif."""
    for x0, y0, x1, y1 in _buttons(frame, window):
        box = (round(x0), round(y0), round(x1), round(y1))
        if any(wanted in text for text, _ in _read(frame, box, zoom=3)):
            return round((x0 + x1) / 2), round((y0 + y1) / 2)
    return None


def find_row(frame: Frame, window: ZaapWindow, word: str) -> Point | None:
    """Ligne de la liste qui contient `word` (OCR) : centre de son texte."""
    for text, point in _read(frame, window.band(frame, *ROWS_BAND)):
        if word in text:
            return point
    return None


def _scene(frame: Frame) -> np.ndarray:
    gray = cv2.cvtColor(frame.crop(frame.layout.map_box), cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)


def close_zaap_window(game, window: ZaapWindow | None = None) -> None:
    """Fenêtre des zaaps fermée par son bouton « Fermer » s'il est vu, sinon par Échap (Game.press referme le Menu
    principal si Échap l'a ouvert)."""
    frame = game.look(park=False)
    window = find_zaap_window(frame) or window
    point = find_button(frame, window, "fermer") if window is not None else None
    if point is not None:
        game.click(point, frame.layout.spread(10, 4))
    else:
        game.press("escape")
    game.sleep(0.4)


def _type(game, text: str) -> None:
    """Texte tapé dans le champ (arrière-plan : messages de caractères ; sinon touche par touche, comme le 25/10)."""
    type_text = getattr(game, "type_text", None)
    if type_text is None or not type_text(text):
        for char in text:
            game.press(char)
            game.sleep(0.06)


def _who(game) -> str:
    """Nom du personnage de cette fenêtre (écoute du serveur, sinon captures préfixées de son nom), pour l'utilisateur."""
    return (getattr(getattr(game, "net", None), "name", "") or getattr(getattr(game, "recorder", None), "name", "")
            or "le personnage")


def _give_up(game, name: str, why: str) -> bool:
    """Abandon du zaap, dit à l'utilisateur (31/10, revue : « pas de message à l'utilisateur » sur un zaap raté)."""
    tell_user(f"Zaap vers {name} abandonné pour {_who(game)} : {why}")
    return False


def player_on(game, cell) -> bool:
    """Un autre joueur (acteur GM d'id positif, pas moi) debout sur la case `cell`, d'après le serveur ; False sans
    écoute vivante."""
    net = getattr(game, "net", None)
    if net is None or not net.alive():
        return False
    mine, wanted = net.my_id, tuple(cell)
    return any(actor.id > 0 and actor.id != mine and map_cell(actor.cell) == wanted
               for actor in list(dict(net.actors).values()))


def use_zaap(game, cell, name: str, coords: tuple[int, int], hide_key: str = "") -> bool:
    """Téléportation par le zaap de la case `cell` vers le zaap `name` (carte `coords`). False (fenêtre refermée) si
    une étape n'est pas vue : menu, fenêtre, ligne (zaap pas connu du personnage), bouton, nouvelle carte ; chaque
    abandon est dit à l'utilisateur (tell_user)."""
    if not ocr_available():
        log.warning("Zaap impossible : pas d'OCR pour lire ses menus")
        return _give_up(game, name, "pas d'OCR pour lire ses menus")
    frame = game.look(park=False)
    departure = _scene(frame)
    point = frame.grid.center(cell)
    log.info("Zaap vers %s (%s,%s) : clic sur le zaap en %s", name, *coords, cell)
    # 30/10 : menu pas vu (Eldieso) ou bouton pas activé (Elseiso) au premier essai, les deux passés au second : chaque
    # étape est essayée TRIES fois.
    hh = frame.grid.half_h
    # 31/10, utilisateur : « utilisation de la touche E et clic sur d'autres endroits du zaap si le menu ne s'ouvre
    # pas » : le centre de sa case, puis plus haut sur l'arche (dessinée au-dessus de la case), puis le bas de la case.
    arch = (point[0], point[1] - ZAAP_UP * hh)
    spots = [point, arch, (point[0], point[1] + 0.55 * hh)]
    if player_on(game, cell):
        # 31/10 : un joueur debout sur la case du zaap (d'après le serveur) en cache le centre, et son menu s'ouvre à la
        # place de « Zaap / Utiliser » : l'arche d'emblée, puis le bas de la case, le centre en dernier.
        log.info("Zaap : un joueur est sur la case du zaap (d'après le serveur) : je vise l'arche d'abord")
        spots = [arch, spots[2], point]
    for attempt, spot in enumerate(spots, 1):
        # 31/10 : un autre joueur (Astrasile) debout sur le zaap d'Astrub : son menu au lieu de « Zaap / Utiliser ».
        # Les joueurs cachés (touche tenue) pendant le clic, comme pour les soleils.
        if hide_key:
            with game.holding(hide_key):
                game.click(spot, frame.layout.spread(3, 3))
        else:
            game.click(spot, frame.layout.spread(3, 3))
        seen = game.wait_until(lambda f: find_use_entry(f, point) is not None, MENU_WAIT, POLL, park=False)
        if seen is not None:
            break
        # 31/10, revue : ce qui s'est ouvert à la place est reconnu (menu d'un joueur, de Flash, petit menu, bulle d'un
        # PNJ) et refermé sans risque (close_any_menu : ni Échap, qui ouvre le Menu principal derrière un menu, ni clic
        # dedans) ; rien d'ouvert : clic perdu, l'endroit suivant.
        kind = menu_open(game.look(park=False))
        log.warning("Zaap : pas de menu « Zaap / Utiliser » après le clic (%s) (essai %d/%d)",
                    f"{kind} ouvert à la place" if kind else "rien d'ouvert : clic perdu ?", attempt, len(spots))
        if kind is not None and not close_any_menu(game, "Zaap"):
            return _give_up(game, name, f"un {kind} s'est ouvert à la place du menu du zaap et ne se ferme pas sans "
                                        "risque (ferme-le toi-même)")
    else:
        return _give_up(game, name, f"pas de menu « Zaap / Utiliser » après {len(spots)} clics sur le zaap "
                                    "(quelqu'un dessus ? clic perdu ?)")
    game.click(find_use_entry(seen, point), seen.layout.spread(12, 5))
    seen = game.wait_until(lambda f: find_zaap_window(f) is not None, WINDOW_WAIT, 0.5, park=False)
    if seen is None:
        log.warning("Zaap : la liste des zaaps ne s'est pas ouverte")
        close_any_menu(game, "Zaap")  # menu resté ouvert (« Utiliser » pas pris) : refermé sans Échap
        return _give_up(game, name, "la liste des zaaps ne s'est pas ouverte après « Utiliser »")
    window = find_zaap_window(seen)
    x0, y0, x1, y1 = window.field
    game.click(((x0 + x1) / 2, (y0 + y1) / 2), seen.layout.spread(20, 3))
    game.sleep(0.3)
    word = zaap_search_word(name)
    # Sans sa première lettre : la majuscule du nom dans le jeu (« Le berceau », « Cité d'Astrub ») ne compte plus.
    typed = word[1:] if len(word) > 4 else word
    _type(game, typed)
    seen = game.wait_until(lambda f: find_row(f, window, typed) is not None, LIST_WAIT, POLL, park=False)
    if seen is None:
        log.warning("Zaap : « %s » absent de la liste des zaaps connus (pas encore visité par ce personnage ?)", name)
        close_zaap_window(game, window)
        return _give_up(game, name, "absent de la liste des zaaps connus (pas encore visité par ce personnage ?)")
    row = find_row(seen, window, typed)
    for attempt in range(1, TRIES + 1):
        game.click(row, seen.layout.spread(30, 4))
        after = game.wait_until(lambda f: find_button(f, window, "teleporter") is not None, LIST_WAIT, POLL, park=False)
        if after is not None:
            seen = after
            break
        log.warning("Zaap : bouton « Se téléporter » pas activé après le choix de %s (essai %d/%d)", name, attempt,
                    TRIES)
    else:
        close_zaap_window(game, window)
        return _give_up(game, name, f"bouton « Se téléporter » pas activé après {TRIES} choix de la ligne")
    arrived = None
    for attempt in range(1, TRIES + 1):
        # 31/10 : Walitcoin, « Se téléporter » cliqué sans effet une fois (fenêtre toujours ouverte), passé au second.
        button = find_button(seen, window, "teleporter")
        if button is None:
            break
        game.click(button, seen.layout.spread(25, 5))
        arrived = game.wait_until(lambda f: find_zaap_window(f) is None and float(np.abs(_scene(f) - departure).mean())
                                  > SCENE_CHANGED, TELEPORT_WAIT, 0.5, park=False)
        if arrived is not None:
            break
        seen = game.look(park=False)
        if find_zaap_window(seen) is None:
            break  # fenêtre refermée sans nouvelle carte : pas de second clic
        log.warning("Zaap : pas de nouvelle carte après « Se téléporter » (essai %d/%d)", attempt, TRIES)
    if arrived is None:
        log.warning("Zaap : pas de nouvelle carte après « Se téléporter » (kamas insuffisants ?)")
        if find_zaap_window(game.look(park=False)) is not None:
            close_zaap_window(game, window)
        return _give_up(game, name, "pas de nouvelle carte après « Se téléporter » (kamas insuffisants ?)")
    game.sleep(1.0)  # carte affichée, personnages posés
    log.info("Zaap : téléporté vers %s", name)
    return True
