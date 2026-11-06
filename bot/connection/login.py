"""Une fenêtre du jeu, de l'écran de connexion jusqu'en jeu (déroulé décrit par l'utilisateur le 08/10).

Accueil (« Se connecter »), choix du serveur (clic sur sa carte, puis « Sélectionner »), choix du personnage (clic sur
lui, puis « Jouer ») ; en jeu quand le titre de la fenêtre porte le nom du personnage et que la barre d'icônes est là.
Personnage choisi : le premier des personnages voulus présent sur le compte, sinon le plus à gauche, le dernier joué.

Compte non abonné (« Mode découverte » sur le choix du serveur ou du personnage) : NotSubscribed, rien n'est cliqué.
Fenêtre ouverte par le bot (Ctrl+8 lance tous les comptes) sur un compte sans aucun des personnages voulus, ou quand
ils sont tous déjà en jeu : NotWanted, elle sera refermée.

Après une déconnexion pour inactivité : fenêtre « Connexion » (« Tu es resté trop longtemps inactif. »), « Ok »,
puis l'accueil. Sur un écran inconnu, Entrée (le « Ok » d'une information) puis Échap sont essayés, et une capture
est enregistrée ; un bouton « Jouer » est cliqué où qu'il soit.

En cascade (11/10, l'utilisateur : une fenêtre après l'autre, c'était long, chaque écran a son chargement) : `step`
regarde la fenêtre et fait au plus un geste, puis rend la main ; connection/manager.py fait avancer ainsi toutes les
fenêtres à tour de rôle, chacune pendant que les autres chargent. `run` mène une seule fenêtre jusqu'au bout.
"""

import logging
from collections.abc import Callable

from bot.brain.game import Game
from bot.connection.clients import is_open, logged_in
from bot.connection.settings import ConnectionSettings
from bot.perception.frame import Frame
from bot.perception.login import (BODY_SPREAD, CARD_SPREAD, MESSAGE_OK, PLAY_BUTTON, SELECT_BUTTON, WELCOME_BUTTON,
                                  LoginStep, LoginView, find_character, find_server, read_login, slot_point)
from bot.perception.party import find_friends_icon
from bot.perception.templates import match_template, template_spread

log = logging.getLogger(__name__)

AFTER_CLICK = 0.6  # clic sur un bouton, une carte de serveur ou un personnage -> regard suivant sur cette fenêtre
POLL = 0.3  # écran en chargement : regard suivant sur cette fenêtre
NEXT_SCREEN = 15.0  # secondes au plus pour quitter l'écran après « Sélectionner », « Jouer », « Ok »...
IN_GAME = 45.0  # secondes au plus, une fois le titre au nom du personnage, pour voir la barre d'icônes du jeu
UNKNOWN_SAVE = 4.0  # écran inconnu depuis 4 s : capture enregistrée (une par connexion)
UNKNOWN_KEYS = ((4.0, "enter"), (12.0, "escape"))  # écran inconnu depuis tant de secondes : touche essayée, une fois
UNKNOWN_POLL = 1.0


class LoginFailed(Exception):
    pass


class NotSubscribed(LoginFailed):
    """Compte non abonné (« Mode découverte »)."""


class NotWanted(LoginFailed):
    """Aucun des personnages voulus sur ce compte, ou tous déjà en jeu (fenêtre ouverte par le bot : à refermer)."""


class Login:
    def __init__(self, game: Game, settings: ConnectionSettings, wanted: list[str] | Callable[[], list[str]],
                 only_wanted: bool = False) -> None:
        """`wanted` : personnages à choisir, ou fonction qui les donne au moment du choix (en cascade : ceux qui
        manquent encore). `only_wanted` : un compte sans aucun d'eux est refusé (NotWanted) ; sinon, le personnage le
        plus à gauche (le dernier joué)."""
        self.game = game
        self.settings = settings
        self._wanted = wanted
        self.only_wanted = only_wanted
        self.chosen: str | None = None  # personnage cliqué sur l'écran des personnages (en route vers le jeu)
        self._end: float | None = None  # échéance de la connexion
        self._ready_at = 0.0  # pas de regard avant (attente après un geste, ou chargement)
        self._leaving: tuple[LoginStep, float] | None = None  # écran qu'on attend de voir partir, jusqu'à quand
        self._confirm: tuple[LoginStep, str, str] | None = None  # bouton de l'écran, après un clic sur carte/personnage
        self._server_error = False  # fenêtre « Erreur » vue : carte du serveur cliquée deux fois
        self._in_game: float | None = None  # titre au nom du personnage : échéance de la barre d'icônes
        self._unknown_since: float | None = None
        self._tried: set[str] = set()
        self._saved = False

    @property
    def wanted(self) -> list[str]:
        return list(self._wanted() if callable(self._wanted) else self._wanted)

    @property
    def ready_at(self) -> float:
        """Heure (horloge du jeu) du prochain regard utile sur cette fenêtre."""
        return self._ready_at

    def run(self) -> str:
        """Mène la fenêtre jusqu'en jeu ; renvoie le nom du personnage. LoginFailed au-delà du délai."""
        while (name := self.step()) is None:
            self.game.sleep(max(self._ready_at - self.game.now(), 0.05))
        return name

    def step(self) -> str | None:
        """Un regard, au plus un geste : le nom du personnage une fois en jeu, None sinon (rien à faire avant
        `ready_at`). LoginFailed (NotSubscribed, NotWanted) si la fenêtre n'arrivera pas en jeu."""
        now = self.game.now()
        if now < self._ready_at:
            return None
        if self._end is None:
            self._end = now + self.settings.login_timeout
        name = logged_in(self.game.window)
        if name:
            return self._interface(name, now)
        if not is_open(self.game.window):
            raise LoginFailed("la fenêtre du jeu a été fermée")
        if self.only_wanted and self.chosen is None and not self.wanted:
            raise NotWanted("tous les personnages voulus sont déjà en jeu")
        frame = self.game.look(park=False)
        if now >= self._end:
            path = self.game.recorder.save(frame.image, "connexion_echec", lossless=True)
            raise LoginFailed(f"toujours pas en jeu au bout de {self.settings.login_timeout:.0f} s (capture : {path})")
        view = read_login(frame)
        if view.discovery:
            raise NotSubscribed("compte non abonné (« Mode découverte »)")
        if self._leaving is not None:
            leaving, until = self._leaving
            if view.step is leaving and now < until:
                return self._wait(POLL)  # l'écran suivant charge
            self._leaving = None
        if self._confirm is not None:
            confirm, button, label = self._confirm
            self._confirm = None
            if view.step is confirm and view.button is not None:
                log.info("« %s »", label)
                return self._click(view.button, template_spread(frame, button), confirm)
        if view.step is not LoginStep.OTHER:
            self._unknown_since = None
        if view.step is LoginStep.MESSAGE and view.button is not None and view.error:
            log.info("Fenêtre « Erreur » : « Ok » (la carte du serveur sera cliquée deux fois)")
            self._server_error = True
            return self._click(view.button, template_spread(frame, MESSAGE_OK))
        if view.step is LoginStep.MESSAGE and view.button is not None:
            log.info("Fenêtre « Connexion »%s : « Ok »", " (déconnecté pour inactivité)" if view.inactive else "")
            return self._click(view.button, template_spread(frame, MESSAGE_OK), LoginStep.MESSAGE)
        if view.step is LoginStep.WELCOME:
            log.info("« Se connecter »")
            return self._click(view.button, template_spread(frame, WELCOME_BUTTON), LoginStep.WELCOME)
        if view.step is LoginStep.SERVERS:
            return self._server(frame, view)
        if view.step is LoginStep.CHARACTERS:
            return self._character(frame, view)
        play = match_template(frame, PLAY_BUTTON, threshold=0.85)
        if play is not None:
            log.info("Bouton « Jouer »")
            return self._click(play[0], template_spread(frame, PLAY_BUTTON))
        return self._unknown(frame, now)

    def _wait(self, seconds: float) -> None:
        self._ready_at = self.game.now() + seconds
        return None

    def _click(self, point, spread, leaving: LoginStep | None = None) -> None:
        """Clic, puis (si `leaving`) attente que cet écran s'en aille."""
        self.game.click(point, spread)
        if leaving is not None:
            self._leaving = (leaving, self.game.now() + NEXT_SCREEN)
        return self._wait(AFTER_CLICK)

    def _server(self, frame: Frame, view: LoginView) -> None:
        point = find_server(frame, view, self.settings.server)
        if point is None:
            path = self.game.recorder.save(frame.image, "connexion_serveur_introuvable", lossless=True)
            raise LoginFailed(f"serveur « {self.settings.server} » introuvable sur l'écran de choix du serveur "
                              f"(capture : {path})")
        log.info("Serveur %s", self.settings.server)
        self._confirm = (LoginStep.SERVERS, SELECT_BUTTON, "Sélectionner")
        if self._server_error:
            # 17/10, compte à deux serveurs : clic sur la carte pas pris, « Choisis un serveur » à chaque « Sélectionner ».
            self.game.click(point, frame.layout.spread(*CARD_SPREAD))
            self.game.sleep(0.3)
        return self._click(point, frame.layout.spread(*CARD_SPREAD))

    def _character(self, frame: Frame, view: LoginView) -> None:
        wanted = self.wanted
        if self.chosen and self.chosen not in wanted:
            # 06/11 (journal 194826) : « Jouer » pas pris, écran des personnages toujours là ; en cascade, la liste ne
            # donne plus que ceux qui manquent hors de cette fenêtre (son choix compté) : vide, fenêtre de Waldarch
            # crue inutile et fermée. Son propre choix reste le sien.
            wanted = [self.chosen] + wanted
        found = find_character(frame, view, wanted) if wanted else None
        if found is not None:
            slot, self.chosen = found
            log.info("Personnage %s (emplacement %d)", self.chosen, slot + 1)
        elif self.only_wanted:
            path = self.game.recorder.save(frame.image, "connexion_compte_sans_personnage_voulu", lossless=True)
            raise NotWanted(f"aucun de {', '.join(wanted)} sur ce compte (capture : {path})" if wanted else
                            f"plus aucun personnage voulu à connecter (capture : {path})")
        else:
            slot = 0
            log.info("%sle personnage le plus à gauche (le dernier joué)",
                     f"Aucun de {', '.join(wanted)} sur ce compte : " if wanted else "")
        self._confirm = (LoginStep.CHARACTERS, PLAY_BUTTON, "Jouer")
        return self._click(slot_point(frame, view, slot), frame.layout.spread(*BODY_SPREAD))

    def _unknown(self, frame: Frame, now: float) -> None:
        """Écran inconnu (chargement le plus souvent) : capture et touches essayées s'il dure."""
        self._unknown_since = now if self._unknown_since is None else self._unknown_since
        if not self._saved and now - self._unknown_since >= UNKNOWN_SAVE:
            self._saved = True
            path = self.game.recorder.save(frame.image, "connexion_ecran_inconnu", lossless=True)
            log.info("Écran de connexion inconnu (capture : %s) : envoie-la-moi pour que je l'apprenne", path)
        for delay, key in UNKNOWN_KEYS:
            if key not in self._tried and now - self._unknown_since >= delay:
                self._tried.add(key)
                log.info("Écran inconnu depuis %.0f s : j'essaie %s", now - self._unknown_since, key.upper())
                self.game.press(key)
        return self._wait(UNKNOWN_POLL)

    def _interface(self, name: str, now: float) -> str | None:
        """Titre de la fenêtre au nom du personnage : on attend encore que le jeu affiche sa barre d'icônes."""
        if self._in_game is None:
            self._in_game = now + IN_GAME
        if find_friends_icon(self.game.look(park=False)) is not None:
            log.info("%s est en jeu", name)
            self._check_chosen(name)
            return name
        if now >= self._in_game:
            log.warning("%s : en jeu d'après le titre de la fenêtre, mais je ne vois pas encore la barre d'icônes", name)
            self._check_chosen(name)
            return name
        return self._wait(POLL)

    def _check_chosen(self, name: str) -> None:
        """Le personnage en jeu est-il celui qui a été cliqué ? 25/10 : Fourboquatre connecté pour Fourboun (même début
        de nom) ; l'erreur n'était vue qu'en jeu, à la main. Signalé aussitôt, avec le remède de l'utilisateur."""
        if self.chosen and name.lower() != self.chosen.lower():
            log.error("Mauvais personnage en jeu : %s, alors que %s avait été choisi. Ferme cette fenêtre et relance le "
                      "bot avec --connexion --personnages <les noms de l'équipe>", name, self.chosen)
