"""Phase de jeu déduite d'une capture : hors combat, placement, combat (et qui joue)."""

import logging
from dataclasses import dataclass
from enum import Enum

from bot.perception.frame import Frame
from bot.perception.layout import Point
from bot.perception.ui import current_fighter_team, find_end_turn_button, find_ready_button, turn_timer_visible

log = logging.getLogger(__name__)


class Phase(Enum):
    OUT_OF_COMBAT = "hors combat"
    PLACEMENT = "placement"
    COMBAT = "combat"


@dataclass(frozen=True)
class Scene:
    phase: Phase
    current_team: str | None = None  # équipe qui joue (combat)
    timer_visible: bool = False  # timer de tour de ce personnage (anneau de la minimap)
    ready_button: Point | None = None
    end_turn_button: Point | None = None

    def summary(self) -> str:
        if self.phase is not Phase.COMBAT:
            return self.phase.value
        return f"combat, tour de l'équipe {self.current_team or '?'}{', timer visible' if self.timer_visible else ''}"


# Phase annoncée par le serveur (ClientFeed.phase) -> Phase.
TOLD = {"combat": Phase.COMBAT, "placement": Phase.PLACEMENT, "hors combat": Phase.OUT_OF_COMBAT}
_disagreed: dict[int, tuple[Phase, Phase]] = {}  # fenêtre -> dernier désaccord dit (une fois par changement)


def read_scene(frame: Frame) -> Scene:
    """Phase du jeu : d'après le serveur quand l'écoute de la fenêtre la connaît (31/10, utilisateur : l'information
    par le réseau, plus fiable ; un K.O., une infobulle sur la timeline ou un bouton Prêt caché trompaient l'écran),
    l'écran ne donnant alors que ce qui sert à cliquer (boutons, équipe qui joue, timer) ; d'après l'écran sinon."""
    seen = _screen_scene(frame)
    net = getattr(frame, "net", None)
    phase = getattr(net, "phase", None)
    told = TOLD.get(phase()) if callable(phase) else None
    if told is None or told is seen.phase:
        return seen
    key = id(net)
    if _disagreed.get(key) != (told, seen.phase):
        _disagreed[key] = (told, seen.phase)
        log.info("%s : %s d'après le serveur, %s à l'écran : le serveur fait foi", getattr(net, "name", "?") or "?",
                 told.value, seen.phase.value)
    if told is Phase.COMBAT:
        return Scene(Phase.COMBAT, seen.current_team, seen.timer_visible, end_turn_button=seen.end_turn_button)
    if told is Phase.PLACEMENT:
        return Scene(Phase.PLACEMENT, ready_button=seen.ready_button or find_ready_button(frame))
    return Scene(Phase.OUT_OF_COMBAT)


def _screen_scene(frame: Frame) -> Scene:
    # La timeline suffit à reconnaître le combat : le bouton de fin de tour disparaît au survol d'un combattant.
    team = current_fighter_team(frame)
    end_turn = find_end_turn_button(frame)
    if team or end_turn:
        return Scene(Phase.COMBAT, team, turn_timer_visible(frame), end_turn_button=end_turn)
    ready = find_ready_button(frame)
    if ready:
        return Scene(Phase.PLACEMENT, ready_button=ready)
    return Scene(Phase.OUT_OF_COMBAT)


def is_my_turn(scene: Scene, my_team: str | None) -> bool:
    if scene.phase is not Phase.COMBAT:
        return False
    return scene.timer_visible or (my_team is not None and scene.current_team == my_team)
