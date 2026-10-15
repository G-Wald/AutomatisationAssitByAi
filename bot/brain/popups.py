"""Fermeture des fenêtres qui s'ouvrent par-dessus la carte.

- Menu de Flash (clic droit tombé à côté d'un monstre) : fermé d'un clic sur le fond beige sous la barre de sorts, jamais par Échap.
- Fenêtre connue (assets/popups.yaml) : fermée à tout moment hors combat.
- Fenêtre inconnue : fermée avec la touche de secours seulement juste après un combat (résultat, niveau
  supérieur...) ; plus tard, c'est sans doute une fenêtre que tu as ouverte toi-même, le bot n'y touche pas.
  Chaque nouvelle fenêtre inconnue est enregistrée en PNG : envoie-la-moi pour que je l'ajoute aux connues.
"""

import logging
import time

from bot.brain.game import Game
from bot.perception.frame import Frame
from bot.perception.layout import Point
from bot.perception.popups import (FLASH_CLOSE_CLEARANCE, PopupSpec, find_flash_menu, flash_close_point, find_known_popup, find_quit_dialog_no,
                                   find_unknown_window)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
AFTER_CLOSE = 0.4  # clic sur « Fermer » -> capture suivante (01/10 : 0,8, trop long en fin de combat)
RECLICK_AFTER = 0.8  # la même fenêtre revue avant ce délai : elle se ferme peut-être encore, pas de second clic tout
# de suite (il tomberait sur la carte) ; ensuite, nouvel essai (01/10 : un clic de fin de combat n'avait pas pris)
FLASH_CLICKS = 2  # clics sous la barre de sorts pour fermer le menu de Flash, avant d'abandonner
FLASH_CLOSE_WAIT = 1.0  # clic -> menu de Flash disparu


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


class PopupCloser:
    def __init__(self, game: Game, specs: list[PopupSpec], unknown_key: str, unknown_delay: float) -> None:
        self.game = game
        self.specs = specs
        self.unknown_key = unknown_key
        self.unknown_delay = unknown_delay
        self._attempts = 0
        self._flash_failures = 0  # fermetures du menu de Flash ratées d'affilée
        self._seen_unknown = False
        self._closed: tuple[str, float] | None = None  # dernière fenêtre fermée, et quand

    def closed_elsewhere(self, name: str) -> None:
        """La fenêtre `name` vient d'être fermée sans passer par ici (clic refait dans chaque fenêtre de l'équipe) :
        pas de second clic si elle est encore à l'écran au prochain regard."""
        self._closed = (name, time.monotonic())

    def describe(self, frame: Frame) -> str | None:
        known = find_known_popup(frame, self.specs)
        if known:
            return f"fenêtre « {known.spec.name} »"
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
        known = find_known_popup(frame, self.specs)
        if known:
            if self._closed is not None and self._closed[0] == known.spec.name \
                    and (left := RECLICK_AFTER - (time.monotonic() - self._closed[1])) > 0:
                self.game.sleep(left)  # encore à l'écran : soit elle se ferme, soit le clic n'a pas pris (nouvel essai)
                return True
            log.info("Fenêtre « %s » : je la ferme", known.spec.name)
            if known.close_point:
                self.game.click(known.close_point, known.close_spread)
            else:
                self.game.press(known.spec.key)
            self._closed = (known.spec.name, time.monotonic())
            self.game.sleep(AFTER_CLOSE)
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
