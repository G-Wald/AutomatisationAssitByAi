"""Phase de jeu déduite d'une capture : hors combat, placement, combat (et qui joue)."""

from dataclasses import dataclass
from enum import Enum

from bot.perception.frame import Frame
from bot.perception.layout import Point
from bot.perception.ui import current_fighter_team, find_end_turn_button, find_ready_button, turn_timer_visible


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


def read_scene(frame: Frame) -> Scene:
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
