"""Position demandée au jeu par le chat : « /p %pos% » (perception/chat.py), quand le nom de la carte ne la donne pas.

16/10 : sur Incarnam 0,3 et 6,1 (texte blanc sur décor clair, fenêtre de 1057 px), coordonnées illisibles au départ :
trajet arrêté, mode zone resté 11 minutes sur une carte vide faute de connaître sa zone. Rarement (pas à chaque carte,
demande de l'utilisateur) : au plus une fois par ASK_EVERY secondes.
"""

import logging
import time

from bot.brain.game import Game
from bot.perception.chat import POSITION_COMMAND, chat_input_point, ocr_available, read_position
from bot.perception.popups import flash_close_point

log = logging.getLogger(__name__)

ASK_EVERY = 60.0
ANSWER_WAIT = 3.0  # Entrée -> ligne affichée dans le chat
CLEAR_KEYS = 25  # Retour arrière avant de taper : reste éventuel dans la zone de saisie
POLL = 0.3


class PositionAsker:
    def __init__(self, game: Game, name: str | None = None) -> None:
        self.game = game
        self.name = name
        self._asked_at: float | None = None

    def ask(self) -> tuple[int, int] | None:
        """Coordonnées lues dans le chat après « /p %pos% » ; None (rien envoyé) trop tôt après la dernière demande,
        sans OCR, ou si la saisie de texte n'est pas possible (premier plan)."""
        now = time.monotonic()
        if self._asked_at is not None and now - self._asked_at < ASK_EVERY:
            return None
        type_text = getattr(self.game, "type_text", None)
        if type_text is None or not ocr_available():
            return None
        self._asked_at = now
        frame = self.game.look(park=False)
        self.game.click(chat_input_point(frame))
        self.game.sleep(0.3)
        # 16/10 : « ee » (touche E des soleils, tapée dans le chat resté actif) devant la commande : « ee/p [6,1] »
        # parti en clair dans le canal général. La zone de saisie est vidée d'abord.
        for _ in range(CLEAR_KEYS):
            self.game.press("backspace")
        if not type_text(POSITION_COMMAND):
            self._give_back(frame)
            return None
        self.game.press("enter")
        try:
            return self._answer()
        finally:
            self._give_back(frame)

    def _give_back(self, frame) -> None:
        """La main rendue au jeu : la zone de saisie garde sinon le focus après Entrée, et les raccourcis suivants (Z, E,
        sorts) s'écriraient dans le chat. Clic sur le fond beige sous la barre de sorts (rien n'y est cliquable)."""
        point = flash_close_point(self.game.look(park=False)) or flash_close_point(frame)
        if point is not None:
            self.game.click(point)

    def _answer(self) -> tuple[int, int] | None:
        end = time.monotonic() + ANSWER_WAIT
        while time.monotonic() < end:
            self.game.sleep(POLL)
            coords = read_position(self.game.look(park=False), self.name)
            if coords is not None:
                log.info("Position donnée par le jeu (« %s » dans le chat) : %s,%s", POSITION_COMMAND, *coords)
                return coords
        path = self.game.recorder.save(self.game.look(park=False).image, "position_chat_illisible", lossless=True)
        log.info("« %s » envoyé, mais pas de position lue dans le chat (capture : %s)", POSITION_COMMAND, path)
        return None
