"""Boucle principale : observe l'écran, reconnaît la phase de jeu et agit en conséquence.

Hors combat : ferme les fenêtres (niveau supérieur, résultat du combat), s'assoit pour reprendre de la
vie, puis attaque un groupe de monstres. Avec engage.enabled à false (ou --combat-only), le bot ne touche
à rien hors combat et tu lances les combats toi-même. Dès le placement, il prend la main : Prêt, puis
chacun de ses tours.
"""

import logging
import random
import time

from bot.actions import keys
from bot.brain.combat import CombatAI, CombatSettings
from bot.brain.engage import Engager, EngageSettings
from bot.brain.game import Game
from bot.brain.memory import SessionSettings
from bot.brain.popups import PopupCloser
from bot.brain.rest import Rester, RestSettings
from bot.brain.spellbook import SpellBook
from bot.brain.travel import TravelSettings, make_roamer
from bot.debug.draw import annotate
from bot.debug.report import write_fight_report
from bot.perception.colors import BLUE, RED
from bot.perception.fighters import find_fighters
from bot.perception.frame import Frame
from bot.perception.placement import entry_banner
from bot.perception.popups import load_popup_specs
from bot.perception.ui import READY_SPREAD
from bot.state.scene import Phase, Scene, is_my_turn, read_scene

log = logging.getLogger(__name__)

# « Hors combat » doit durer ce temps avant d'agir (fenêtres de fin de combat fermées, carte revenue).
SETTLE_SECONDS = 2
# Juste après le placement, plus longtemps : entre le clic sur Prêt et l'apparition de la timeline, l'écran
# ressemble quelques secondes à du hors combat.
SETTLE_AFTER_READY = 5
AFTER_READY_SECONDS = 15
PLACEMENT_FIRST_TRY = 0.3  # placement : premier essai de lecture des cases, une fois le bandeau d'entrée parti
PLACEMENT_GIVE_UP = 4.0  # au-delà, Prêt sans changer de case


class Bot:
    connection = None  # reconnexion (connection/manager.py, Watcher), branchée par bot/__main__.py avec --connexion
    # Sessions de tests (11/10, --combats, --minutes) : arrêt après tant de combats, ou passé cette heure (horloge
    # time.monotonic), une fois hors combat et les fenêtres de résultat fermées. 0 / None : sans limite.
    max_fights = 0
    deadline: float | None = None

    def __init__(self, game: Game, settings: dict, act: bool = True, engage: bool | None = None,
                 travel: str | None = None, start: tuple[int, int] | None = None, region: str | None = None) -> None:
        """`engage` : force l'attaque des groupes (True) ou la désactive (False) ; None = config.
        `travel` : façon de changer de carte (voir brain/travel.py) ; None = config.
        `start` : coordonnées de la carte de départ, si le bot ne sait pas encore lire tous ses chiffres.
        `region` : Incarnam ou Monde des Douze (None : config)."""
        combat = settings.get("combat") or {}
        popups = settings.get("popups") or {}
        timing = settings.get("timing") or {}
        self.game = game
        self.act = act
        combat_settings = CombatSettings.from_settings(settings)
        self.spellbook = SpellBook(game, session=SessionSettings.from_settings(settings))  # sorts et caractéristiques
        self.ai = CombatAI(game, combat_settings, self.spellbook)
        self.popups = PopupCloser(
            game,
            load_popup_specs(),
            unknown_key=popups.get("unknown_window_key") or "",
            unknown_delay=float(popups.get("unknown_window_delay", 20)),
        )
        engage_settings = EngageSettings.from_settings(settings)
        self.travel_settings = TravelSettings.from_settings(settings, travel, start, region)
        if self.travel_settings.mode in ("test", "explore", "aller"):
            engage = False  # déplacements (test, exploration, trajet) : pas de combat lancé par le bot
        self.engager = Engager(game, engage_settings) if (engage_settings.enabled if engage is None else engage) else None
        self.roamer = make_roamer(game, self.travel_settings)
        self.finished = False  # test de déplacement ou exploration terminés
        self._empty_searches = 0
        rest_settings = RestSettings.from_settings(settings)
        # Repos seulement quand le bot enchaîne lui-même les combats : sinon il ne touche à rien hors combat.
        # Repos seulement quand le bot circule lui-même (combats enchaînés, déplacements) : sinon il ne touche à rien.
        self.rester = Rester(game, rest_settings) if (self.engager or self.roamer) and rest_settings.enabled else None
        for key in (combat_settings.resolved_spell_key, combat_settings.cancel_key, combat_settings.end_turn_key,
                    self.popups.unknown_key, engage_settings.show_groups_key, self.travel_settings.show_exits_key):
            if key:
                keys.resolve(key)  # erreur de config signalée au démarrage plutôt qu'en plein combat
        team = str(combat.get("my_team", "auto")).lower()
        if team not in ("auto", RED, BLUE):
            raise ValueError(f"combat.my_team doit valoir auto, red ou blue (pas {team!r})")
        self.learn_team = team == "auto"
        self.my_team = None if self.learn_team else team
        self.auto_ready = bool(combat.get("auto_ready", True))
        self.poll = float(timing.get("poll", 0.15))
        self._last_summary: str | None = None
        self._phase: Phase | None = None
        self._combat_ended_at: float | None = None
        self._ready_clicked = False
        self._placed = False
        self._last_team = RED  # couleur du dernier combat : sert au placement, avant de savoir la couleur de celui-ci
        self._next_engage_at = 0.0
        self._phase_since = time.monotonic()
        self._settle = SETTLE_SECONDS
        self._rest_pending = False
        self.fights = 0  # combats finis depuis le lancement
        self._stop_reason = ""  # limite de la session atteinte : arrêt dès que le calme revient

    def run(self) -> None:
        while not self.finished:
            if self.connection is not None and self.connection.check():
                continue  # personnage reconnecté : nouvelle capture
            frame = self.game.look(park=False)
            scene = read_scene(frame)
            if scene.phase is not self._phase:
                self._phase_changed(self._phase, scene.phase)
            self._report(frame, scene)
            if self.act:
                self._step(frame, scene)
            if self._session_over(frame, scene):
                break
            self.game.sleep(self.poll)

    def _session_over(self, frame: Frame, scene: Scene) -> bool:
        """Limite de la session (combats, durée) atteinte : True une fois hors combat depuis un moment, sans fenêtre de
        résultat (elles sont fermées par _step)."""
        if not self._stop_reason and self.deadline is not None and time.monotonic() >= self.deadline:
            self._stop_reason = "durée de la session écoulée"
        if not self._stop_reason or scene.phase is not Phase.OUT_OF_COMBAT:
            return False
        if time.monotonic() - self._phase_since < SETTLE_SECONDS + 1 or self.popups.describe(frame) is not None:
            return False
        log.info("Fin de la session : %s", self._stop_reason)
        self.finished = True
        return True

    def _phase_changed(self, old: Phase | None, new: Phase) -> None:
        self._phase = new
        self._phase_since = time.monotonic()
        self._settle = SETTLE_AFTER_READY if old is Phase.PLACEMENT else SETTLE_SECONDS
        self._ready_clicked = False
        self._placed = False
        if new is Phase.PLACEMENT or (new is Phase.COMBAT and old is not Phase.PLACEMENT):
            self.ai.new_combat()  # nouvelle carte de combat : obstacles et mode tactique à revoir
        if old is Phase.PLACEMENT:
            self._next_engage_at = max(self._next_engage_at, time.monotonic() + AFTER_READY_SECONDS)
        if old is Phase.COMBAT:
            self._rest_pending = self.rester is not None
            log.info("Combat terminé")
            self.fights += 1
            write_fight_report(getattr(self.game.recorder, "dir", None), self.fights,
                               [(self.spellbook.name or "personnage", self.ai.fight_report())])
            if self.max_fights and self.fights >= self.max_fights:
                self._stop_reason = f"{self.fights} combat(s), le maximum demandé"
            self._combat_ended_at = time.monotonic()
            if self.engager is not None:
                self._next_engage_at = time.monotonic() + random.uniform(*self.engager.settings.pause_between_fights)
            self._last_team = self.my_team or self._last_team
            if self.learn_team:
                self.my_team = None  # la couleur de mon équipe peut changer d'un combat à l'autre

    def _report(self, frame: Frame, scene: Scene) -> None:
        """Journalise chaque changement d'état, avec une capture annotée."""
        popup = self.popups.describe(frame) if scene.phase is not Phase.COMBAT else None  # pas de fenêtre en combat
        summary = scene.summary() + (f" + {popup}" if popup else "")
        if summary == self._last_summary:
            return
        self._last_summary = summary
        log.info("État : %s", summary)
        if not self.game.recorder.keeps() and not log.isEnabledFor(logging.DEBUG):
            return
        fighters = find_fighters(frame) if scene.phase is Phase.COMBAT else []
        for fighter in fighters:
            log.debug("  combattant %s en %s (%.0f, %.0f)", fighter.team, fighter.cell, fighter.x, fighter.y)
        buttons = [(p, name) for p, name in ((scene.ready_button, "pret"), (scene.end_turn_button, "fin de tour")) if p]
        self.game.recorder.save(annotate(frame, fighters=fighters, marks=buttons, text=summary), scene.phase.name.lower())

    def _step(self, frame: Frame, scene: Scene) -> None:
        if scene.phase is not Phase.COMBAT:
            since = None if self._combat_ended_at is None else time.monotonic() - self._combat_ended_at
            if self.popups.handle(frame, since):
                return
        if scene.phase is Phase.PLACEMENT and self.auto_ready and not self._ready_clicked:
            elapsed = time.monotonic() - self._phase_since
            if elapsed < PLACEMENT_FIRST_TRY:
                return
            if not self._placed:
                if entry_banner(frame) and elapsed < PLACEMENT_GIVE_UP:
                    return  # « Tu es entré en combat ! » assombrit une bande de cases : elles se liraient mal
                self.ai.ensure_tactical_mode(frame)  # avant de lire les cases : l'herbe brouille leurs couleurs
                team = self.ai.place(self.game.look(), self.my_team or self._last_team, last_try=elapsed >= PLACEMENT_GIVE_UP)
                if team is None:
                    return  # cases pas encore lisibles (bandeau d'entrée en combat) : nouvel essai au tour suivant
                self._placed = True
                if team and self.learn_team and self.my_team is None:
                    self.my_team = team  # connue dès le placement : mon tour se voit sans attendre le timer
                    log.info("Mon équipe pour ce combat : %s (d'après le placement)", team)
                return  # Prêt au tour de boucle suivant, sur une capture à jour
            # Un seul clic : un second pourrait annuler le premier.
            log.info("Placement : je clique sur Prêt")
            # Fait seulement si le clic est parti : annulé (autre fenêtre sous la souris), il sera refait.
            self._ready_clicked = self.game.click(scene.ready_button, frame.layout.spread(*READY_SPREAD)) is not False
        elif scene.phase is Phase.OUT_OF_COMBAT and not self.spellbook.read_done:
            # Une fois par session : coût et portée des sorts (fenêtre « Tes sorts »), caractéristiques (dégâts des
            # sorts) ; en mode mémoire (session.mode), repris de la dernière session sans ouvrir de fenêtre.
            if time.monotonic() - self._phase_since >= self._settle and self.popups.describe(frame) is None:
                self.spellbook.read()
        elif scene.phase is Phase.OUT_OF_COMBAT and self.engager is None and self.roamer is not None:
            # Test de déplacement, exploration : une carte à la fois, les combats subis restent gérés ici.
            if time.monotonic() - self._phase_since < self._settle or self.popups.describe(frame) is not None:
                return
            if self._rest_pending or (self.rester is not None and self.rester.needs_rest()):
                self._rest_pending = False  # une agression en chemin a pu faire mal : pas de trajet blessé
                if self.rester is not None:
                    self.rester.rest()
                return
            self.roamer.step()
            if self.roamer.done:
                self.finished = True
        elif scene.phase is Phase.OUT_OF_COMBAT and self.engager is not None:
            # Pas tant qu'une fenêtre (même inconnue) couvre la carte : les clics tomberaient dessus.
            if time.monotonic() - self._phase_since < self._settle or self.popups.describe(frame) is not None:
                return
            if self._rest_pending or (self.rester is not None and self.rester.needs_rest()):
                self._rest_pending = False
                if self.rester is not None:
                    self.rester.rest()
            elif time.monotonic() >= self._next_engage_at and not self.engager.engage():
                # Groupe parti avant l'arrivée : on en cherche un autre aussitôt ; aucun groupe : on attend,
                # ou (mode zone) on passe sur une carte voisine de la zone.
                self._empty_searches = self._empty_searches + 1 if self.engager.none_found else 0
                if self.roamer is not None and self._empty_searches >= self.travel_settings.empty_searches:
                    self._empty_searches = 0
                    self.roamer.step()
                    self._next_engage_at = time.monotonic()
                    return
                delay = 1.0 if self.engager.missed else self.engager.settings.retry_delay
                self._next_engage_at = time.monotonic() + delay
        elif scene.phase is Phase.COMBAT:
            if self.learn_team and scene.timer_visible and scene.current_team and scene.current_team != self.my_team:
                # Le timer (anneau de la minimap) ne tourne que pendant mon tour : il confirme ou corrige l'équipe.
                if self.my_team is not None:
                    log.warning("Mon équipe est %s, pas %s (timer de mon tour)", scene.current_team, self.my_team)
                else:
                    log.info("Mon équipe pour ce combat : %s (repérée au timer de mon tour)", scene.current_team)
                self.my_team = scene.current_team
            team = self.my_team or scene.current_team
            if team and is_my_turn(scene, self.my_team):
                self.ai.play_turn(team)
