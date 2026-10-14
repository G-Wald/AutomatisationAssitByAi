"""Repos entre deux combats, seulement si la vie est basse : s'asseoir (icône tabouret au-dessus de la minimap)
jusqu'à ce que le cœur soit presque plein. La vie est lue au remplissage du cœur (voir ui.hp_ratio).
Le personnage se relève tout seul au premier clic ailleurs (vers le groupe suivant).
"""

import logging
from dataclasses import dataclass

from bot.brain.game import Game
from bot.perception.templates import match_template
from bot.perception.ui import hp_ratio
from bot.state.scene import Phase, read_scene

log = logging.getLogger(__name__)

STOOL, STOOL_ZONE, STOOL_SPREAD = "tabouret.png", (572, 775, 1000, 830), (6, 5)
CHECK_EVERY = 2  # secondes entre deux lectures du cœur pendant le repos


@dataclass(frozen=True)
class RestSettings:
    enabled: bool = True
    below: float = 0.5  # s'asseoir sous 50 % de vie
    until: float = 0.9  # jusqu'à 90 %
    max_seconds: float = 180.0

    @classmethod
    def from_settings(cls, settings: dict) -> "RestSettings":
        rest = settings.get("rest") or {}
        return cls(
            enabled=bool(rest.get("enabled", cls.enabled)),
            below=float(rest.get("below", cls.below)),
            until=float(rest.get("until", cls.until)),
            max_seconds=float(rest.get("max_seconds", cls.max_seconds)),
        )


class Rester:
    def __init__(self, game: Game, settings: RestSettings) -> None:
        self.game = game
        self.settings = settings

    def needs_rest(self) -> bool:
        ratio = hp_ratio(self.game.look(park=False))
        return ratio is not None and ratio < self.settings.below

    def rest(self) -> None:
        """S'assoit si la vie est basse, jusqu'à `until` (ou `max_seconds`)."""
        frame = self.game.look()
        ratio = hp_ratio(frame)
        if ratio is None or ratio >= self.settings.below:
            return
        stool = match_template(frame, STOOL, box=frame.layout.box(*STOOL_ZONE))
        if stool is None:
            log.info("Vie à %.0f %% : tabouret introuvable, je me repose debout", 100 * ratio)
        else:
            log.info("Vie à %.0f %% : je m'assois jusqu'à %.0f %%", 100 * ratio, 100 * self.settings.until)
            self.game.click(stool[0], frame.layout.spread(*STOOL_SPREAD))
        for _ in range(max(1, round(self.settings.max_seconds / CHECK_EVERY))):
            self.game.sleep(CHECK_EVERY)
            frame = self.game.look(park=False)
            if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                log.info("Combat pendant le repos")  # agressé par un groupe
                return
            ratio = hp_ratio(frame)
            if ratio is not None and ratio >= self.settings.until:
                log.info("Vie à %.0f %% : je repars", 100 * ratio)
                return
        log.info("Fin du repos (durée maximale)")
