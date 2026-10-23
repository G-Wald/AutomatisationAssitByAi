"""Reprise après erreur : fenêtre du jeu sourde aux touches, captures limitées.

Fenêtre sourde (20/10, S3, S4, S6, S8) : la fenêtre de Koalaloupour ignorait Z, F1 et les chiffres des sorts, alors
que les clics passaient ; un vrai clic dans la fenêtre l'a débloquée une fois. Remède : un clic gauche sur le fond
beige sûr de l'interface (perception.popups.flash_close_point : en bas à gauche de la barre de sorts, ou sous la 2e
rangée de sorts en combat), jamais sur la carte, puis la touche de nouveau.

- unlock_click(game, frame) : ce clic ;
- is_deaf / mark_deaf / mark_heard : l'état « sourde jusqu'à » de la fenêtre (Game.deaf_until, une par personnage) :
  pendant ce temps, ce qui a un bouton se clique plutôt qu'au clavier (sorts, fin de tour) ;
- keyboard_answers(game, key) : sonde par la touche A (sorties marquées) : toute carte a au moins une sortie, donc
  aucun contour après A veut dire que la touche n'a pas été reçue ;
- report_episode : UNE ligne au journal par épisode, qui dit si le clic a débloqué (oui ou non) : à compter dans les
  journaux (« Reprise, clavier sourd »), pour mesurer si ce remède marche ;
- capture_once : au plus une capture par cause et par fenêtre toutes les CAPTURE_EVERY s.
"""

import logging
import time

from bot.perception.exits import find_marked_exits
from bot.perception.frame import Frame
from bot.perception.popups import FLASH_CLOSE_CLEARANCE, flash_close_point

log = logging.getLogger(__name__)

DEAF_HOLD = 60.0  # s pendant lesquels une fenêtre vue sourde le reste (un tour de combat et sa fin de tour)
UNLOCK_SETTLE = 0.2  # clic de déblocage -> touche suivante
PROBE_WAIT = 1.0  # touche A -> contours des sorties : attendus jusqu'à ce délai
CAPTURE_EVERY = 300.0  # au plus une capture par cause et par fenêtre toutes les 5 minutes
EPISODE = "Reprise, clavier sourd"  # début de la ligne de chaque épisode (à chercher dans les journaux)


def is_deaf(game) -> bool:
    """La fenêtre de ce jeu a ignoré une touche il y a peu (mark_deaf) : boutons cliqués plutôt que touches."""
    return getattr(game, "deaf_until", 0.0) > time.monotonic()


def mark_deaf(game, why: str) -> None:
    if not is_deaf(game):
        log.warning("Fenêtre sourde aux touches (%s) : je clique les boutons plutôt que d'appuyer sur leurs touches "
                    "pendant %.0f s", why, DEAF_HOLD)
    game.deaf_until = time.monotonic() + DEAF_HOLD


def mark_heard(game) -> None:
    """Une touche a de nouveau été reçue : la fenêtre n'est plus sourde."""
    if getattr(game, "deaf_until", 0.0):
        game.deaf_until = 0.0


def unlock_click(game, frame: Frame | None = None) -> bool:
    """Clic gauche sur le fond beige sûr (flash_close_point), jamais sur la carte. False si ce fond n'est pas libre
    (une fenêtre assombrit l'écran) : aucun clic."""
    frame = frame if frame is not None else game.look(park=False)
    point = flash_close_point(frame)
    if point is None or frame.layout.on_map(point):
        log.info("Clic de déblocage impossible : le fond beige sous la barre de sorts n'est pas libre")
        return False
    reach = frame.layout.length(FLASH_CLOSE_CLEARANCE / 2)
    log.info("Clic de déblocage sur le fond beige en (%d, %d)", *point)
    game.click(point, (reach, reach))
    game.sleep(UNLOCK_SETTLE)
    return True


def report_episode(what: str, unlocked: bool, detail: str = "") -> None:
    """La ligne de mesure de l'épisode : quelle touche était ignorée, et si le clic de déblocage a suffi."""
    log.log(logging.INFO if unlocked else logging.WARNING, "%s (%s) : clic de déblocage, débloqué : %s%s", EPISODE,
            what, "oui" if unlocked else "non", f" ({detail})" if detail else "")


def keyboard_answers(game, key: str = "a", wait: float = PROBE_WAIT) -> bool:
    """Sonde du clavier : `key` (touche A) entoure les sorties de la carte ; True si des contours se voient avant
    `wait` s (ou déjà avant l'appui : seule A les affiche), False sinon (la touche n'a pas été reçue)."""
    if find_marked_exits(game.look(park=True)):
        return True
    game.press(key)
    return game.wait_until(lambda frame: bool(find_marked_exits(frame)), wait, park=True) is not None


def capture_once(game, image, cause: str, lossless: bool = True) -> str | None:
    """Capture `cause` de cette fenêtre, sauf si elle a déjà été prise il y a moins de CAPTURE_EVERY s (None)."""
    now = time.monotonic()
    taken = getattr(game, "_captured_at", None)
    if taken is None:
        taken = {}
        try:
            game._captured_at = taken
        except AttributeError:
            pass
    if now - taken.get(cause, float("-inf")) < CAPTURE_EVERY:
        return None
    taken[cause] = now
    return game.recorder.save(image, cause, lossless=lossless)
