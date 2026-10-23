"""Fermeture des fenêtres qui s'ouvrent par-dessus la carte.

- Menu de Flash (clic droit tombé à côté d'un monstre) : fermé d'un clic sur le fond beige sous la barre de sorts, jamais par Échap.
- Fenêtre connue (assets/popups.yaml) : fermée à tout moment hors combat.
- Fenêtre inconnue : fermée avec la touche de secours seulement juste après un combat (résultat, niveau
  supérieur...) ; plus tard, c'est sans doute une fenêtre que tu as ouverte toi-même, le bot n'y touche pas.
  Chaque nouvelle fenêtre inconnue est enregistrée en PNG : envoie-la-moi pour que je l'ajoute aux connues.
"""

import logging
import time
from collections.abc import Callable

from bot.brain.game import Game
from bot.brain.recovery import capture_once
from bot.perception.frame import Frame
from bot.perception.layout import Point
from bot.perception.popups import (FLASH_CLOSE_CLEARANCE, PopupMatch, PopupSpec, find_context_menu, find_flash_menu,
                                   find_generic_close, flash_close_point, find_known_popup, find_npc_dialog,
                                   find_quit_dialog_no, find_unknown_window)
from bot.state.scene import Phase, read_scene

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
AFTER_CLOSE = 0.4  # clic sur « Fermer » -> capture suivante (01/10 : 0,8, trop long en fin de combat)
RECLICK_AFTER = 0.8  # la même fenêtre revue avant ce délai : elle se ferme peut-être encore, pas de second clic tout
# de suite (il tomberait sur la carte) ; ensuite, nouvel essai (01/10 : un clic de fin de combat n'avait pas pris)
FLASH_CLICKS = 2  # clics sous la barre de sorts pour fermer le menu de Flash, avant d'abandonner
FLASH_CLOSE_WAIT = 1.0  # clic -> menu de Flash disparu
# Même fenêtre revue moins de REPEAT_WINDOW s après sa fermeture : la fermeture n'a pas pris (20/10, S11 et S15 :
# « Fermer » du résultat cliqué sous « Informations », sans effet, encore et encore). À REPEAT_DIAGNOSE fois, bouton
# cherché autrement (perception.popups.find_generic_close) ; à REPEAT_GIVE_UP, la fenêtre est notée bloquée jusqu'au
# tour des fenêtres suivant (new_round), avec un avertissement et une capture.
REPEAT_WINDOW = 15.0
REPEAT_DIAGNOSE = 3
REPEAT_GIVE_UP = 6


# Menu d'un personnage (« Baffer, Organiser mon magasin... ») : barre de titre sombre de MENU_TITLE px de référence
# au-dessus des entrées beiges ; le clic qui le ferme, à MENU_STEP cases à côté de lui, jamais à moins de EDGE_KEEP
# cases du bord de la carte (un soleil y ferait changer de carte).
MENU_TITLE = 38
MENU_STEP = 1.5
EDGE_KEEP = 2
MENU_CLOSE_WAIT = 0.6  # clic -> menu disparu


def close_context_menu(game: Game, frame: Frame) -> bool:
    """Menu d'un personnage ouvert par un clic, hors combat : fermé d'un clic sur la carte à côté de lui, qui déplace
    aussi le personnage. 21/10, Calanques 11,-27 : l'équipe arrivée sur la case du soleil par lequel elle repartait,
    chacun a cliqué sur lui-même (la touche E cache les autres joueurs, pas soi) ; Échap ne ferme pas ce menu (il ouvre
    le Menu principal derrière), 8 fenêtres bloquées jusqu'à la fin de la session. Utilisateur : cliquer ailleurs pour
    déplacer le personnage, puis le soleil. Jamais le menu de Flash (close_flash_menu). False sans menu à fermer."""
    if find_flash_menu(frame) is not None or read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
        return False
    box = find_context_menu(frame)
    if box is None:
        return False
    layout, grid = frame.layout, frame.grid
    x0, y0, x1, y1 = box
    title = y0 - layout.length(MENU_TITLE)
    step_x, step_y = MENU_STEP * 2 * grid.half_w, MENU_STEP * 2 * grid.half_h
    left, top, right, bottom = layout.map_box
    keep_x, keep_y = EDGE_KEEP * 2 * grid.half_w, EDGE_KEEP * 2 * grid.half_h
    candidates = [(x0 - step_x, title), (x1 + step_x, title), (x0, title - step_y), (x0, y1 + step_y),
                  (x1 + step_x, y1), (x0 - step_x, y1)]
    points = [grid.center(grid.cell_at(x, y)) for x, y in candidates
              if left + keep_x <= x <= right - keep_x and top + keep_y <= y <= bottom - keep_y]
    spread = (grid.half_w * 0.15, grid.half_h * 0.15)
    for point in points[:2]:
        log.info("Menu d'un personnage ouvert (Échap ne le ferme pas) : je clique sur la carte à côté, en (%d, %d), "
                 "ce qui le déplace hors de sa case", *point)
        game.click(point, spread)
        game.sleep(MENU_CLOSE_WAIT)
        if find_context_menu(game.look(park=False)) is None:
            return True
    log.warning("Menu d'un personnage toujours ouvert après %d clic(s) à côté", min(2, len(points)))
    return True


def close_flash_menu(game: Game, frame: Frame, around: Point | None = None) -> bool:
    """Menu de Flash ouvert (perception.popups.find_flash_menu ; `around` : où chercher, le clic qui l'a ouvert) :
    fermé d'un clic gauche sur le fond beige en bas à gauche de la barre de sorts (15/10 : un clic dans le chat ne le
    ferme pas), jamais sur la carte (le personnage y marcherait) ni par Échap (14/10 : il reste ouvert et le Menu
    principal s'ouvre derrière). True s'il a disparu."""
    for attempt in range(1, FLASH_CLICKS + 1):
        point = flash_close_point(frame)
        if point is None or frame.layout.on_map(point):
            log.warning("Menu de Flash ouvert, mais le fond sous la barre de sorts n'est pas libre : ferme-le toi-même "
                        "(clic à côté du menu)")
            return False
        reach = frame.layout.length(FLASH_CLOSE_CLEARANCE / 2)
        log.info("Menu de Flash ouvert : je clique sous la barre de sorts en (%d, %d) pour le fermer, sans Échap "
                 "(essai %d/%d)", *point, attempt, FLASH_CLICKS)
        game.click(point, (reach, reach))
        after = game.wait_until(lambda f: find_flash_menu(f, around) is None, FLASH_CLOSE_WAIT, park=False)
        if after is not None:
            log.info("Menu de Flash fermé")
            return True
        frame = game.look(park=False)
    log.warning("Le menu de Flash reste ouvert : ferme-le toi-même (clic à côté du menu)")
    return False


def close_npc_dialog(game: Game, frame: Frame) -> bool:
    """Bulle de dialogue d'un PNJ (perception.popups.find_npc_dialog) : fermée par sa croix. True si elle était là."""
    cross = find_npc_dialog(frame)
    if cross is None:
        return False
    log.info("Bulle de dialogue d'un PNJ ouverte (clic sur lui ?) : je la ferme par sa croix en (%d, %d)", *cross)
    game.click(cross, (2, 2))
    if game.wait_until(lambda f: find_npc_dialog(f) is None, FLASH_CLOSE_WAIT, park=False) is None:
        log.warning("La bulle du PNJ est encore là après le clic sur sa croix")
    return True


def close_small_menu(game: Game, frame: Frame, still_open: Callable[[Frame], bool]) -> None:
    """Petit menu du jeu ouvert par un clic droit (ressource « Lin / Cueillir / Faucher », personnage, PNJ) : fermé
    d'un clic sur le même fond beige que le menu de Flash. 17/10 (journaux 112944, 125352, 161256) : Échap ne le
    fermait pas et ouvrait le Menu principal derrière, à chaque fois. Échap seulement si ce fond n'est pas libre."""
    point = flash_close_point(frame)
    if point is None or frame.layout.on_map(point):
        game.press("escape")
        return
    reach = frame.layout.length(FLASH_CLOSE_CLEARANCE / 2)
    game.click(point, (reach, reach))
    if game.wait_until(lambda f: not still_open(f), FLASH_CLOSE_WAIT, park=False) is None:
        log.info("Le menu est encore là après le clic sous la barre de sorts")


LEVEL_UP = "informations (niveau supérieur)"


def read_level_up(frame: Frame, box) -> int | None:
    """Niveau de « Tu passes niveau N » (fenêtre Informations), lu par l'OCR de Windows ; None s'il ne se lit pas."""
    import re

    import cv2

    from bot.perception.chat import ocr_available, ocr_lines

    if not ocr_available() or box is None:
        return None
    x0, y0, x1, y1 = (max(0, round(v)) for v in box)
    crop = frame.image[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    for line in ocr_lines(cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)):
        match = re.search(r"niveau\s*(\d+)", line, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


class PopupCloser:
    def __init__(self, game: Game, specs: list[PopupSpec], unknown_key: str, unknown_delay: float,
                 on_level: Callable[[int], None] | None = None) -> None:
        self.game = game
        self.on_level = on_level  # niveau lu dans « Tu passes niveau N » avant de fermer la fenêtre (engage.max_level)
        self.specs = specs
        self.unknown_key = unknown_key
        self.unknown_delay = unknown_delay
        self._attempts = 0
        self._flash_failures = 0  # fermetures du menu de Flash ratées d'affilée
        self._seen_unknown = False
        self._closed: tuple[str, float] | None = None  # dernière fenêtre fermée, et quand
        self._repeats = 0  # fois de suite où la dernière fenêtre fermée est revenue (REPEAT_WINDOW)
        self.blocked: str | None = None  # fenêtre qu'on n'arrive pas à fermer : laissée jusqu'au tour suivant

    def new_round(self) -> None:
        """Nouveau tour des fenêtres (team/loop.py) : une fenêtre notée bloquée est retentée."""
        self.blocked = None
        if self._repeats >= REPEAT_GIVE_UP:
            self._repeats = REPEAT_DIAGNOSE  # retentée directement par le diagnostic, sans tout recompter

    def closed_elsewhere(self, name: str) -> None:
        """La fenêtre `name` vient d'être fermée sans passer par ici (clic refait dans chaque fenêtre de l'équipe) :
        pas de second clic si elle est encore à l'écran au prochain regard."""
        self._closed = (name, time.monotonic())

    def describe(self, frame: Frame) -> str | None:
        known = find_known_popup(frame, self.specs)
        if known:
            return f"fenêtre « {known.spec.name} »"
        if find_npc_dialog(frame) is not None:
            return "bulle de dialogue d'un PNJ"
        if find_unknown_window(frame) is None:
            return None
        return "menu de Flash" if find_flash_menu(frame) is not None else "fenêtre inconnue"

    def handle(self, frame: Frame, seconds_since_combat: float | None) -> bool:
        """True si une fenêtre a été fermée (ou tentée) : il faut reprendre une capture."""
        no = find_quit_dialog_no(frame)
        if no is not None:
            log.warning("Fenêtre « Cette action va quitter ta partie en cours » : je réponds « Non »")
            self.game.click(no, (6, 3))
            self.game.sleep(AFTER_CLOSE)
            return True
        if find_flash_menu(frame) is not None:  # par-dessus tout le reste ; jamais d'Échap ni de touche de secours
            if self._flash_failures >= MAX_ATTEMPTS:
                return False  # déjà signalé : la fenêtre reste au joueur
            self._flash_failures = 0 if close_flash_menu(self.game, frame) else self._flash_failures + 1
            return True
        self._flash_failures = 0
        if close_npc_dialog(self.game, frame):
            return True
        known = find_known_popup(frame, self.specs)
        if known:
            return self._close_known(frame, known)
        if close_context_menu(self.game, frame):
            return True

        if find_unknown_window(frame) is None:
            self._attempts, self._seen_unknown = 0, False
            return False
        if not self._seen_unknown:
            self._seen_unknown = True
            path = self.game.recorder.save(frame.image, "fenetre_inconnue", lossless=True)
            log.info("Fenêtre inconnue sur la carte (capture : %s)", path)
        after_combat = seconds_since_combat is not None and seconds_since_combat <= self.unknown_delay
        if not (after_combat and self.unknown_key):
            return False
        if self._attempts >= MAX_ATTEMPTS:
            if self._attempts == MAX_ATTEMPTS:
                self._attempts += 1
                log.warning("Je n'arrive pas à fermer cette fenêtre : ferme-la toi-même et envoie-moi la capture.")
            return False
        self._attempts += 1
        log.info("J'essaie de la fermer avec %s (essai %d/%d)", self.unknown_key.upper(), self._attempts, MAX_ATTEMPTS)
        self.game.press(self.unknown_key)
        self.game.sleep(0.8)
        return True

    def _close_known(self, frame: Frame, known: PopupMatch) -> bool:
        name = known.spec.name
        if name == LEVEL_UP and self.on_level is not None and self._closed is None or \
                name == LEVEL_UP and self.on_level is not None and self._closed[0] != name:
            level = read_level_up(frame, known.box)
            if level is not None:
                self.on_level(level)
        if self.blocked == name:
            return False  # déjà signalée : pas d'autre essai avant le tour des fenêtres suivant
        now = time.monotonic()
        again = self._closed is not None and self._closed[0] == name
        if again and (left := RECLICK_AFTER - (now - self._closed[1])) > 0:
            self.game.sleep(left)  # encore à l'écran : soit elle se ferme, soit le clic n'a pas pris (nouvel essai)
            return True
        self._repeats = self._repeats + 1 if again and now - self._closed[1] < REPEAT_WINDOW else 0
        if self._repeats >= REPEAT_GIVE_UP:
            self.blocked = name
            path = capture_once(self.game, frame.image, "fenetre_qui_ne_se_ferme_pas")
            log.warning("Fenêtre « %s » revenue %d fois de suite après sa fermeture : je la laisse jusqu'au prochain "
                        "tour des fenêtres. Ferme-la toi-même si elle reste%s", name, self._repeats,
                        f", et envoie-moi la capture : {path}" if path else "")
            return False
        if self._repeats >= REPEAT_DIAGNOSE:
            generic = find_generic_close(frame, known.box)
            if generic is not None:
                point, what = generic
                log.info("Fenêtre « %s » revenue %d fois après sa fermeture : j'essaie un autre bouton (%s) en "
                         "(%d, %d)", name, self._repeats, what, *point)
                self.game.click(point, frame.layout.spread(4, 3))
                self._closed = (name, time.monotonic())
                self.game.sleep(AFTER_CLOSE)
                return True
            log.info("Fenêtre « %s » revenue %d fois après sa fermeture, et aucun autre bouton sûr : je la referme "
                     "comme d'habitude", name, self._repeats)
        log.info("Fenêtre « %s » : je la ferme", name)
        if known.close_point:
            self.game.click(known.close_point, known.close_spread)
        else:
            self.game.press(known.spec.key)
        self._closed = (name, time.monotonic())
        self.game.sleep(AFTER_CLOSE)
        return True
