"""Mode équipe : surveillance du progrès, hors combat seulement (21/10, après les sessions du 20/10).

Le 20/10, l'équipe est restée bloquée plusieurs fois sans que rien ne bouge : fenêtre de résultat restée ouverte
derrière « Informations » (S11, S15), fenêtre sourde aux touches (S3 à S8 : Z sans infobulle, carte crue vide l'une
après l'autre), touche A sans sortie (S14). Chacun a son remède à l'endroit où il se voit ; ceci est le filet qui
reste quand aucun n'a suffi.

- Progrès (TeamBot._progress_at) : combat lancé ou fini, changement de carte, arrivée d'un suiveur, tour joué ; compté
  à partir du début de la boucle (TeamBot.run). Progrès faible : nouvel essai d'un suiveur en route (sortie essayée,
  itinéraire recalculé), jusqu'à SOFT_PROGRESS_LIMIT s après le dernier vrai progrès (TeamBot._real_progress_at).
- STALL_DIAGNOSE s sans progrès : diagnostic sur le principal puis les suiveurs (diagnose_member, dans cet ordre :
  fenêtres connues ; menu de Flash sur tout l'écran ; surface beige inconnue avec ses boutons génériques ; bulle de
  PNJ ; sonde du clavier par la touche A et clic de déblocage). Un nouveau toutes les STALL_DIAGNOSE s.
- STALL_LAST_RESORT s sans progrès, ou FRUITLESS_LIMIT diagnostics sans cause : une capture de chaque fenêtre, un
  avertissement clair, puis pause (comme F9, StopControl) avec un nouveau diagnostic toutes les PAUSED_DIAGNOSE s ;
  ou arrêt (réglage recovery.last_resort: pause|stop).
- Jamais en combat : un personnage en combat arrête le diagnostic (la boucle s'occupe du combat) ; pendant la pause,
  les fenêtres sont regardées toutes les PAUSE_POLL s (arrière-plan seulement : rien n'est passé devant) et un combat
  lève la pause aussitôt.
"""

import logging
import time
from dataclasses import dataclass

from bot.brain.popups import close_flash_menu, close_npc_dialog
from bot.brain.recovery import capture_once, keyboard_answers, mark_deaf, mark_heard, report_episode, unlock_click
from bot.perception.popups import (find_flash_menu, find_generic_close, find_known_popup, find_npc_dialog,
                                   find_unknown_window)
from bot.state.scene import Phase, read_scene

log = logging.getLogger(__name__)

STALL_DIAGNOSE = 120.0  # s sans progrès avant un diagnostic (puis un toutes les STALL_DIAGNOSE s)
STALL_LAST_RESORT = 300.0  # s sans progrès : dernier recours
# Progrès faibles (TeamBot._progress(soft=True) : un suiveur en route essaie une sortie, sans changer de carte encore,
# 25/10, journaux 175311 et 180222) : ils repoussent la surveillance, mais pas au-delà de ce délai depuis le dernier
# vrai progrès (combat, carte, suiveur arrivé, tour joué) ; ensuite, dernier recours comme sans progrès.
SOFT_PROGRESS_LIMIT = 900.0
FRUITLESS_LIMIT = 2  # diagnostics de suite sans cause : dernier recours
PAUSED_DIAGNOSE = 120.0  # pendant la pause du dernier recours : un diagnostic toutes les 2 min
PAUSE_POLL = 10.0  # pendant cette pause : combat cherché dans chaque fenêtre (arrière-plan seulement)
LAST_RESORTS = ("pause", "stop")


@dataclass(frozen=True)
class RecoverySettings:
    last_resort: str = "pause"

    @classmethod
    def from_settings(cls, settings: dict) -> "RecoverySettings":
        recovery = settings.get("recovery") or {}
        last_resort = str(recovery.get("last_resort") or cls.last_resort).strip().lower()
        if last_resort not in LAST_RESORTS:
            raise ValueError(f"recovery.last_resort doit valoir pause ou stop (pas {last_resort!r})")
        return cls(last_resort=last_resort)


def diagnose_member(member, frame, exits_key: str = "a") -> str | None:
    """Ce qui bloque ce personnage, déjà traité si on sait le faire ; None si rien ne se voit. Hors combat seulement
    (l'appelant l'a vérifié sur `frame`)."""
    game = member.game
    known = find_known_popup(frame, member.popups.specs)
    if known is not None:
        member.popups.new_round()  # notée bloquée : un nouvel essai, le diagnostic le demande
        member.popups.handle(frame, None)
        return f"fenêtre « {known.spec.name} »"
    if find_flash_menu(frame, anywhere=True) is not None:
        closed = close_flash_menu(game, frame)
        return f"menu de Flash{'' if closed else ' (resté ouvert)'}"
    window = find_unknown_window(frame)
    if window is not None:
        generic = find_generic_close(frame, window)
        path = capture_once(game, frame.image, "fenetre_inconnue_diagnostic")
        if generic is None:
            log.warning("Diagnostic : surface beige inconnue sur la carte, aucun bouton sûr pour la fermer%s",
                        f" (capture : {path})" if path else "")
            return "fenêtre inconnue (aucun bouton sûr)"
        point, what = generic
        log.info("Diagnostic : surface beige inconnue, je clique sur %s en (%d, %d)", what, *point)
        game.click(point, frame.layout.spread(4, 3))
        game.sleep(0.4)
        return f"fenêtre inconnue ({what})"
    if find_npc_dialog(frame) is not None:
        close_npc_dialog(game, frame)
        return "bulle de dialogue d'un PNJ"
    if exits_key and not keyboard_answers(game, exits_key):
        mark_deaf(game, f"aucune sortie après {exits_key.upper()} (diagnostic)")
        unlocked = unlock_click(game)
        heard = keyboard_answers(game, exits_key)
        if heard:
            mark_heard(game)
        report_episode(f"touche {exits_key.upper()}, diagnostic", heard,
                       "" if unlocked else "clic impossible, fond beige pas libre")
        return f"clavier sourd (débloqué : {'oui' if heard else 'non'})"
    return None


class Watchdog:
    """Surveillance du progrès de l'équipe (TeamBot), hors combat. TeamBot.step appelle `check` à chaque tour de
    boucle hors combat ; `progress` à chaque progrès."""

    def __init__(self, bot, settings: RecoverySettings, control=None, enabled: bool = True) -> None:
        self.bot = bot
        self.settings = settings
        self.control = control  # StopControl (pause), None : arrêt à la place
        self.enabled = enabled
        self.fruitless = 0  # diagnostics de suite sans cause
        self._diagnosed_at: float | None = None  # dernier diagnostic depuis le dernier progrès
        self._paused_seen = self._paused_total()

    def _paused_total(self) -> float:
        return float(getattr(self.control, "paused_total", 0.0) or 0.0)

    def progress(self) -> None:
        self.fruitless = 0
        self._diagnosed_at = None

    def check(self) -> bool:
        """True si la surveillance a agi (diagnostic, dernier recours) : le tour de boucle s'arrête là."""
        if not self.enabled:
            return False
        paused = self._paused_total()
        if paused > self._paused_seen:
            # Pause de l'utilisateur (F9) : ce temps-là n'est pas du temps perdu par le bot.
            self.bot._progress_at += paused - self._paused_seen
            if getattr(self.bot, "_real_progress_at", None) is not None:
                self.bot._real_progress_at += paused - self._paused_seen
            self._paused_seen = paused
        now = time.monotonic()
        stalled = now - self.bot._progress_at
        real = getattr(self.bot, "_real_progress_at", None)
        if real is not None and now - real >= SOFT_PROGRESS_LIMIT:
            stalled = max(stalled, now - real)  # des essais, mais plus aucun résultat depuis trop longtemps
        if stalled < STALL_DIAGNOSE:
            return False
        if self._diagnosed_at is not None and now - self._diagnosed_at < STALL_DIAGNOSE \
                and stalled < STALL_LAST_RESORT and self.fruitless < FRUITLESS_LIMIT:
            return False  # diagnostic récent : on laisse la boucle travailler
        fighting = self._in_combat()
        if fighting is not None:  # jamais en combat : la boucle s'en occupe (tour des fenêtres)
            self.bot._round_due = True
            return True
        if stalled >= STALL_LAST_RESORT or self.fruitless >= FRUITLESS_LIMIT:
            self.last_resort(stalled)
            return True
        self._diagnosed_at = now
        log.warning("Aucun progrès depuis %.0f s (ni combat, ni carte, ni suiveur arrivé, ni tour joué) : je cherche ce "
                    "qui bloque", stalled)
        cause = self.diagnose()
        if cause is None:
            self.fruitless += 1
            log.warning("Diagnostic : rien de visible ne bloque (%d/%d)", self.fruitless, FRUITLESS_LIMIT)
        else:
            self.fruitless = 0
            log.info("Diagnostic : %s", cause)
        return True

    def _in_combat(self) -> str | None:
        for member in self.bot.members:
            if read_scene(member.game.look(park=False)).phase is not Phase.OUT_OF_COMBAT:
                return member.name
        return None

    def diagnose(self) -> str | None:
        """Le principal puis les suiveurs : la première cause trouvée (et traitée), None si rien ne se voit. Un
        personnage en combat arrête tout : la boucle s'en occupe (tour des fenêtres)."""
        exits_key = getattr(getattr(self.bot, "travel_settings", None), "show_exits_key", "a")
        for member in [self.bot.leader, *self.bot.followers]:
            frame = member.game.look(park=False)
            if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                self.bot._round_due = True
                return f"{member.name} est en combat"
            cause = diagnose_member(member, frame, exits_key)
            if cause is not None:
                self.bot._round_due = True  # tour des fenêtres ensuite : il vérifie et ferme le reste
                return f"{member.name} : {cause}"
        return None

    def last_resort(self, stalled: float) -> None:
        paths = [path for member in self.bot.members
                 if (path := capture_once(member.game, member.game.look(park=False).image, "sans_progres"))]
        why = f"{stalled / 60:.0f} min" if self.fruitless < FRUITLESS_LIMIT else \
            f"{self.fruitless} diagnostics sans rien trouver"
        log.warning("BLOQUÉ : aucun progrès depuis %.0f s (%s). Captures de chaque fenêtre : %s", stalled, why,
                    ", ".join(str(p) for p in paths) or "déjà prises il y a moins de 5 min")
        if self.settings.last_resort == "stop" or self.control is None or not hasattr(self.control, "pause"):
            log.warning("Je m'arrête (recovery.last_resort: %s) : regarde les fenêtres, puis relance",
                        self.settings.last_resort)
            self.bot._stop_reason = "aucun progrès"
            self.bot.finished = True
            return
        log.warning("Je me mets en pause : regarde les fenêtres et débloque ce qui doit l'être, puis appuie sur %s. "
                    "Je refais un diagnostic toutes les %.0f min", (self.control.pause_key_name or "la touche de "
                                                                   "pause").upper(), PAUSED_DIAGNOSE / 60)
        self.control.pause("aucun progrès")
        self._paused()

    def _paused(self) -> None:
        """Pause du dernier recours : levée par l'utilisateur, par un diagnostic qui trouve une cause, ou par un
        combat (jamais de pause en combat)."""
        background = all(getattr(m.game, "background", False) for m in self.bot.members)
        last = time.monotonic()
        while True:
            if not self.control.wait_paused(PAUSE_POLL if background else PAUSED_DIAGNOSE):
                log.info("Pause levée : je reprends")
                break
            with self.control.lifted():
                fighting = self._in_combat() if background else None
                cause = None
                if fighting is None and time.monotonic() - last >= PAUSED_DIAGNOSE:
                    last = time.monotonic()
                    cause = self.diagnose()
                    log.info("Diagnostic pendant la pause : %s", cause or "rien de visible")
            if fighting is not None:
                self.control.resume(f"{fighting} est en combat")
                break
            if cause is not None:
                self.control.resume(f"cause trouvée : {cause}")
                break
        self.bot._round_due = True
        self.bot._progress("reprise après la pause")
