"""Position demandée au jeu par le chat : « /p %pos% » (perception/chat.py), quand le nom de la carte ne la donne pas.

16/10 : sur Incarnam 0,3 et 6,1 (texte blanc sur décor clair, fenêtre de 1057 px), coordonnées illisibles au départ :
trajet arrêté, mode zone resté 11 minutes sur une carte vide faute de connaître sa zone. Rarement (pas à chaque carte,
demande de l'utilisateur) : au plus une fois par ASK_EVERY secondes.

27/10 (« pourquoi ça a spammé le %pos% ? ») : la vérification du départ et le trajet ont chacun leur PositionAsker, et
redemandaient à 6 s d'écart sans avoir bougé. La réponse est gardée sur la fenêtre (game.told_position) et resservie
tant que le serveur n'annonce pas de changement de carte (écoute : connection/network.py).
"""

import logging
import time

from bot.brain.game import Game
from bot.perception.chat import POSITION_COMMAND, chat_input_point, ocr_available, read_position
from bot.perception.popups import flash_close_point

log = logging.getLogger(__name__)

ASK_EVERY = 60.0
# Demande forcée (25/10, brain/travel.py) : lecture du nom de carte contraire à la position à l'estime, ou passage raté ;
# le jeu tranche avant tout nouvel itinéraire. Au plus une fois par FORCED_EVERY secondes (chat du groupe).
FORCED_EVERY = 10.0
ANSWER_WAIT = 3.0  # Entrée -> ligne affichée dans le chat
CLEAR_KEYS = 25  # Retour arrière avant de taper : reste éventuel dans la zone de saisie
POLL = 0.3


class PositionAsker:
    def __init__(self, game: Game, name: str | None = None) -> None:
        self.game = game
        self.name = name
        self._asked_at: float | None = None

    def ask(self, force: bool = False) -> tuple[int, int] | None:
        """Coordonnées lues dans le chat après « /p %pos% » ; None (rien envoyé) trop tôt après la dernière demande
        (ASK_EVERY s, FORCED_EVERY s si `force`), sans OCR, ou si la saisie de texte n'est pas possible (premier plan)."""
        known = self._known()
        if known is not None:
            log.debug("Position déjà donnée par le jeu, pas de changement de carte depuis : %s,%s", *known)
            return known
        now = time.monotonic()
        if self._asked_at is not None and now - self._asked_at < (FORCED_EVERY if force else ASK_EVERY):
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
        sent, sent_at = time.localtime(), time.monotonic()  # l'heure des lignes du chat est celle de ce PC
        self.game.press("enter")
        try:
            return self._answer((sent.tm_hour, sent.tm_min), sent_at)
        finally:
            self._give_back(frame)

    def _known(self) -> tuple[int, int] | None:
        """Dernière réponse à « /p %pos% » dans cette fenêtre, si le serveur n'a annoncé aucune carte depuis ; None sans
        écoute (le personnage a pu bouger sans qu'on le sache)."""
        told = getattr(self.game, "told_position", None)
        net = getattr(self.game, "net", None)
        if told is None or net is None or not net.alive():
            return None
        coords, at = told
        return coords if net.snapshot()[1] <= at else None

    def _give_back(self, frame) -> None:
        """La main rendue au jeu : la zone de saisie garde sinon le focus après Entrée, et les raccourcis suivants (Z, E,
        sorts) s'écriraient dans le chat. Clic sur le fond beige sous la barre de sorts (rien n'y est cliquable)."""
        # Sur la capture fraîche seulement (25/10) : le point de la capture d'avant la demande, cliqué à l'aveugle quand
        # le fond n'est plus libre, tomberait sur ce qui s'y est posé depuis (fenêtre, menu, œil « combats en cours »).
        point = flash_close_point(self.game.look(park=False))
        if point is None:
            self.game.sleep(0.3)
            point = flash_close_point(self.game.look(park=False))
        if point is not None:
            self.game.click(point)
        else:
            log.info("Fond beige sous la barre de sorts pas libre : la zone de saisie du chat garde peut-être la main")

    def _answer(self, since: tuple[int, int] | None = None, sent_at: float | None = None) -> tuple[int, int] | None:
        """Réponse à la demande : d'abord le message renvoyé par le serveur (31/10, utilisateur : le réseau plutôt que
        l'écran ; « cMK$|id|nom|[x, y]| »), sinon une ligne du chat de la minute de l'envoi ou après (pas une ancienne
        réponse restée dans le chat, 31/10)."""
        end = time.monotonic() + ANSWER_WAIT
        net = getattr(self.game, "net", None)
        while time.monotonic() < end:
            self.game.sleep(POLL)
            told = None
            if sent_at is not None and hasattr(net, "position_told_since"):
                told = net.position_told_since(sent_at)
            if told is not None:
                log.info("Position donnée par le jeu (« %s », dite par le serveur) : %s,%s", POSITION_COMMAND, *told)
                self.game.told_position = (told, time.monotonic())
                return told
            coords = read_position(self.game.look(park=False), self.name, since)
            if coords is not None:
                log.info("Position donnée par le jeu (« %s » dans le chat) : %s,%s", POSITION_COMMAND, *coords)
                self.game.told_position = (coords, time.monotonic())
                return coords
        path = self.game.recorder.save(self.game.look(park=False).image, "position_chat_illisible", lossless=True)
        log.info("« %s » envoyé, mais pas de position lue dans le chat (capture : %s)", POSITION_COMMAND, path)
        return None
