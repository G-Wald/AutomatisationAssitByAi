"""Connexion au lancement du bot, et reconnexion pendant le jeu (personnage déconnecté pour inactivité).

Au lancement : les fenêtres du jeu déjà ouvertes, puis les fenêtres restées sur un écran de connexion menées en jeu
en cascade (chacune avance pendant que les autres chargent) ; celle d'un compte non abonné est fermée. S'il manque encore un des personnages voulus (ou s'il
n'y avait aucune fenêtre), le launcher lance tous les comptes disponibles (Ctrl+8) ; chaque nouvelle fenêtre est menée
en jeu avec un personnage voulu, ou refermée (compte non abonné, aucun personnage voulu, tous déjà en jeu). En mode
équipe, le groupe est ensuite reformé (connection/group.py).
Pendant le jeu : le titre de chaque fenêtre est relu (sans capture) au plus toutes les connexion.verifier_toutes
secondes ; un personnage dont le titre a perdu son nom est reconnecté (le même personnage), puis réinvité dans le
groupe une fois hors combat.
"""

import logging
import time
from collections.abc import Callable

from bot.actions.stop import StopControl
from bot.brain.game import Game
from bot.capture.monitors import ScreenSettings, place_windows
from bot.capture.window import GameWindow
from bot.connection.clients import client_windows, close_window, is_open, logged_in
from bot.connection.group import Player, form_group
from bot.connection.launcher import launch_clients, new_windows
from bot.connection.login import Login, LoginFailed, NotSubscribed, NotWanted
from bot.connection.settings import ConnectionSettings
from bot.state.scene import Phase, read_scene
from bot.team.member import Focus, MemberGame, TaggedRecorder

log = logging.getLogger(__name__)

CASCADE_POLL = 0.1  # en cascade : aucune fenêtre n'a rien à faire, regard suivant dans 0,1 s au plus


def missing_characters(wanted: list[str], names: list[str]) -> list[str]:
    """Personnages voulus qui ne sont dans aucune fenêtre du jeu."""
    found = {name.lower() for name in names}
    return [name for name in wanted if name.lower() not in found]


def connect_windows(settings: ConnectionSettings, wanted: list[str], make_game: Callable[[GameWindow], Game],
                    control: StopControl, screens: ScreenSettings = ScreenSettings(),
                    make_launcher: Callable[[GameWindow], Game] | None = None) -> list[str]:
    """Les fenêtres du jeu déjà ouvertes menées en jeu ; s'il manque un des personnages voulus (ou s'il n'y a aucune
    fenêtre), tous les comptes disponibles lancés par le launcher (Ctrl+8), chaque nouvelle fenêtre menée en jeu avec
    un personnage voulu ou refermée. Chaque fenêtre est d'abord placée sur l'écran choisi (window.ecran).
    `make_launcher` : le jeu du launcher (11/10 : au premier plan ; par défaut `make_game`). Renvoie les personnages en
    jeu."""
    windows = client_windows()
    names = _connect(windows, settings, wanted, make_game, control, screens) if windows else []
    if names and not missing_characters(wanted, names):
        return names
    log.info("%s : je lance les comptes disponibles avec le launcher",
             f"Sans fenêtre : {', '.join(missing_characters(wanted, names))}" if names else "Aucun personnage en jeu")
    known = {w.hwnd for w in client_windows()}
    new = launch_clients(settings.launcher_path, settings.clients_timeout, make_launcher or make_game, control)
    if not new:
        log.error("Aucune nouvelle fenêtre du jeu au bout de %.0f s", settings.clients_timeout)
    while new:
        names += _connect(new, settings, wanted, make_game, control, screens, launched=names)
        known |= {w.hwnd for w in new}
        new = new_windows(known)  # ouvertes pendant les connexions (les comptes s'ouvrent un à un)
    if missing := missing_characters(wanted, names):
        log.warning("Personnage(s) toujours sans fenêtre : %s", ", ".join(missing))
    return names


def _connect(windows: list[GameWindow], settings: ConnectionSettings, wanted: list[str],
             make_game: Callable[[GameWindow], Game], control: StopControl, screens: ScreenSettings,
             launched: list[str] | None = None) -> list[str]:
    """Ces fenêtres placées sur l'écran choisi puis menées en jeu en cascade : à tour de rôle, chaque fenêtre qui a
    quelque chose à faire avance d'un geste (les autres chargent pendant ce temps). Renvoie leurs personnages.
    `launched` : fenêtres ouvertes par le bot (Ctrl+8), les personnages déjà en jeu avant elles ; chacune est menée en
    jeu avec un personnage voulu qui manque encore (ni en jeu, ni choisi dans une autre fenêtre), sinon refermée."""
    place_windows(windows, screens, control.sleep)
    names = [name for window in windows if (name := logged_in(window))]
    waiting = [window for window in windows if not logged_in(window)]
    if not waiting:
        return names
    log.info("Connexion de %d fenêtre(s) du jeu (serveur %s%s)", len(waiting), settings.server,
             f", personnages : {', '.join(wanted)}" if wanted else "")
    logins: dict[int, tuple[GameWindow, Login]] = {}

    def targets() -> list[str]:
        if launched is None:
            return wanted
        chosen = [login.chosen for _, login in logins.values() if login.chosen]
        return missing_characters(wanted, launched + names + chosen)

    only_wanted = launched is not None and bool(wanted)
    for window in waiting:
        logins[window.hwnd] = (window, Login(make_game(window), settings, targets, only_wanted))
    while logins:
        for hwnd, (window, login) in list(logins.items()):
            try:
                name = login.step()
            except NotSubscribed:
                log.info("Compte non abonné (« Mode découverte ») : je ferme sa fenêtre")
                _close(window, control)
            except NotWanted as error:
                log.info("Fenêtre inutile (%s) : je la ferme", error)
                _close(window, control)
            except LoginFailed as error:
                log.error("Connexion impossible : %s", error)
            else:
                if name is None:
                    continue
                names.append(name)
            del logins[hwnd]
        if logins:
            now = min(login.game.now() for _, login in logins.values())
            next_look = min(login.ready_at for _, login in logins.values())
            control.sleep(min(max(next_look - now, 0.0), CASCADE_POLL) or 0.02)
    return names


def _close(window: GameWindow, control: StopControl) -> None:
    if not close_window(window, control.sleep):
        log.warning("La fenêtre « %s » ne se ferme pas : ferme-la toi-même", window.title)


def connect_at_start(settings: ConnectionSettings, wanted: list[str], control: StopControl, recorder, human,
                     background: bool, switch_delay: float = 0.25, screens: ScreenSettings = ScreenSettings()
                     ) -> list[str]:
    """Connexion au lancement du bot : une fenêtre à la fois, gestes comme pendant le jeu (premier plan : chaque
    fenêtre passée devant à son tour ; arrière-plan : messages, rien n'est passé devant)."""
    focus = Focus(control, switch_delay, passive=background)
    tagged = TaggedRecorder(recorder, "connexion")

    def make_game(window: GameWindow) -> Game:
        # 30/10 : fenêtre écoutée dès sa connexion (avant le choix du personnage) : le sac arrive à ce moment (ASK) et
        # sert à la répartition des clés de donjon (team/keys.py) ; son flux revient au personnage ensuite (follow).
        from bot.connection.network import current_listener

        listener = current_listener()
        if listener is not None:
            listener.follow(window.hwnd, "")
        return MemberGame(window, control, tagged, human, True, focus, background)

    def make_launcher(window: GameWindow) -> Game:
        # 11/10 : en arrière-plan (capture de la fenêtre recouverte, clics par messages), le launcher se figeait
        # jusqu'à ce que l'utilisateur redimensionne sa fenêtre. Il est donc toujours passé devant et capturé à l'écran ;
        # Ctrl+8 est tapé au vrai clavier (la souris ne sert que pour la page de Dofus Retro, si elle n'est pas affichée).
        if background:
            log.info("Launcher : je le passe devant quelques secondes (Ctrl+8)")
        return MemberGame(window, control, TaggedRecorder(recorder, "launcher"), human, True,
                          Focus(control, switch_delay), False)

    return connect_windows(settings, wanted, make_game, control, screens, make_launcher)


class Watcher:
    """Reconnexion pendant le jeu. `players` : le principal d'abord ; groupe reformé s'il y en a plusieurs."""

    def __init__(self, settings: ConnectionSettings, players: list[Player]) -> None:
        self.settings = settings
        self.players = players
        self._next_check = 0.0
        self._regroup = False  # reconnexion faite en combat : groupe à reformer une fois hors combat
        self._other: set[str] = set()  # fenêtres passées sur un autre personnage (signalé une fois)

    def check(self) -> bool:
        """À chaque tour de boucle : True si une reconnexion (ou le groupe) vient d'être faite, l'écran a changé."""
        now = time.monotonic()
        if now < self._next_check:
            return False
        self._next_check = now + self.settings.check_every
        lost = [p for p in self.players if self._lost(p)]
        for player in lost:
            log.warning("%s est déconnecté : je le reconnecte", player.name)
            try:
                name = Login(player.game, self.settings, [player.name]).run()
                if name and name.lower() != player.name.lower():
                    # Personnage le plus à gauche pris faute de trouver le sien : signalé, pas joué à sa place.
                    log.error("Reconnexion de %s : c'est %s qui est en jeu dans sa fenêtre. Ferme-la et relance le bot "
                              "avec --connexion --personnages <les noms de l'équipe>", player.name, name)
            except NotSubscribed:
                log.error("Reconnexion de %s impossible : son compte n'est plus abonné (« Mode découverte »)",
                          player.name)
            except LoginFailed as error:
                log.error("Reconnexion de %s impossible : %s", player.name, error)
        if lost and len(self.players) > 1 and self.settings.group:
            if self.players[0] in lost and len(self.players) > 2:
                log.warning("Le principal a été déconnecté : si les autres sont restés groupés entre eux, ils ne "
                            "pourront pas être réinvités (quitte leur groupe à la main)")
            self._regroup = True
        if self._regroup and self._out_of_combat():
            self._regroup = False
            form_group(self.players[0], self.players[1:], self.settings.collapse_group)
            return True
        return bool(lost)

    def _lost(self, player: Player) -> bool:
        window = player.game.window
        if not is_open(window):
            if player.name not in self._other:
                self._other.add(player.name)
                log.error("La fenêtre de %s est fermée : relance le jeu (le launcher n'est pas encore géré)", player.name)
            return False
        name = logged_in(window)
        if not name:
            return True
        if name.lower() != player.name.lower() and player.name not in self._other:
            self._other.add(player.name)
            log.warning("La fenêtre de %s est maintenant celle de %s : je n'y touche pas", player.name, name)
        return False

    def _out_of_combat(self) -> bool:
        return all(read_scene(p.game.look(park=False)).phase is Phase.OUT_OF_COMBAT for p in self.players)
