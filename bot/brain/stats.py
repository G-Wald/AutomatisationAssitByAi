"""Caractéristiques du personnage : lues en début de session, appliquées aux dégâts des sorts.

Dégâts d'une ligne d'effet de dégâts d'un sort (formule validée par l'utilisateur le 04/10) :
    base x (100 + caractéristique de l'élément + bonus aux dégâts %) / 100 + bonus aux dégâts fixe
le bonus fixe s'ajoutant à CHAQUE ligne de dégâts du sort. Élément -> caractéristique : terre et neutre -> Force,
feu -> Intelligence, eau -> Chance, air -> Agilité. Exemple de l'utilisateur : Flèche Magique 12 à 14 (feu) avec 100
d'Intelligence tape 100 % de plus : 13 -> 26 en moyenne. Vols de vie compris (même élément, même formule).
Le bonus aux dégâts physiques est volontairement ignoré pour les sorts (décision de l'utilisateur, 04/10 : « bonus au
dégât et bonus au dégât %, mais pas dégât physique ») : lu et retenu s'il est visible, journalisé, jamais compté.
Pas comptés non plus (pas encore étudiés) : les coups critiques, les soins.

Lecture (brain/spellbook.py, SpellBook.read, une fois par session et par personnage) : bouton ADN de la barre
d'icônes -> « Tes caractéristiques » ; épée -> « Caracteristiques avancées », colonne Total ; Échap ferme tout.
READ_TRIES essais ; ensuite, à défaut de la fenêtre avancée, ce que montre « Tes caractéristiques » (base +
équipement, sans les bonus aux dommages) ; sinon rien : dégâts de base des sorts, comme avant le 04/10.
"""

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields

from bot.perception.frame import Frame
from bot.perception.layout import Point
from bot.perception.stats import (
    REQUIRED,
    SIMPLE_REQUIRED,
    SWORD_SPREAD,
    StatsReading,
    find_advanced_window,
    find_stats_button,
    find_stats_window,
    find_sword_button,
    read_advanced_window,
    read_stats_window,
)

log = logging.getLogger(__name__)

ELEMENT_STAT = {"terre": "force", "neutre": "force", "feu": "intelligence", "eau": "chance", "air": "agilite"}
STAT_NAMES = {"force": "Force", "intelligence": "Intelligence", "chance": "Chance", "agilite": "Agilité"}
READ_TRIES = 2  # lecture ratée (fenêtre pas ouverte, chiffre illisible) : un second essai, puis sans les caractéristiques
OPEN_WAIT, OPEN_CHECKS = 0.4, 5  # fenêtre attendue après un clic : un regard toutes les 0,4 s, 5 fois au plus
CLOSE_TRIES = 2  # Échap ferme les deux fenêtres d'un coup ; une seconde fois si l'une est encore là
CLOSE_KEY = "escape"


@dataclass(frozen=True)
class CharacterStats:
    """Totaux lus (équipement compris). Les quatre caractéristiques des éléments et les deux bonus aux dommages
    servent aux dégâts ; PA, PM et portée au combat ; le reste au journal."""

    force: int = 0
    intelligence: int = 0
    chance: int = 0
    agilite: int = 0
    dommages: int = 0  # bonus aux dégâts : ajouté à chaque ligne de dégâts
    dommages_pourcent: int = 0  # bonus aux dégâts (%) : ajouté au pourcentage de la caractéristique
    # Bonus aux dégâts physiques : lu et retenu, volontairement ignoré pour les sorts (décision de l'utilisateur, 04/10).
    dommages_physiques: int = 0
    pa: int | None = None
    pm: int | None = None
    portee: int | None = None
    vitalite: int | None = None
    sagesse: int | None = None
    invocations: int | None = None
    source: str = "avancées"  # fenêtre lue : « avancées », ou « simple » (sans les bonus aux dommages)

    @classmethod
    def from_reading(cls, reading: StatsReading) -> "CharacterStats":
        values = {key: value for key, value in reading.values.items() if key in _FIELDS and value is not None}
        return cls(**values, source=reading.source)

    @classmethod
    def from_record(cls, record: dict) -> "CharacterStats":
        """Depuis la mémoire des personnages (brain/memory.py) ; les clés inconnues sont ignorées."""
        return cls(**{key: value for key, value in record.items() if key in _FIELDS or key == "source"})

    def record(self) -> dict:
        return asdict(self)

    def stat_of(self, element: str | None) -> int:
        """Caractéristique qui augmente les dégâts de cet élément (0 pour un élément inconnu)."""
        return getattr(self, ELEMENT_STAT[element]) if element in ELEMENT_STAT else 0

    def boost(self, damage: float, element: str | None) -> float:
        """Dégâts d'une ligne d'effet (base `damage`) avec les caractéristiques : formule validée le 04/10."""
        percent = max(0, 100 + self.stat_of(element) + self.dommages_pourcent)
        return damage * percent / 100 + self.dommages

    def explain(self, elements: list[str | None]) -> str:
        """Ce qui change les dégâts de ces éléments : « Intelligence 50, +8 %, +4 »."""
        parts = []
        for stat in dict.fromkeys(ELEMENT_STAT[e] for e in elements if e in ELEMENT_STAT):
            parts.append(f"{STAT_NAMES[stat]} {getattr(self, stat)}")
        if self.dommages_pourcent:
            parts.append(f"{self.dommages_pourcent:+d} %")
        if self.dommages:
            parts.append(f"{self.dommages:+d}")
        return ", ".join(parts) or "rien"

    def describe(self) -> str:
        """« Force 80, Intelligence 50, Chance 0, Agilité 7, dommages +4, +8 % ; PA 6, PM 3, portée 0 »."""
        text = ", ".join(f"{name} {getattr(self, stat)}" for stat, name in STAT_NAMES.items())
        if self.source == "simple":
            text += ", bonus aux dommages non lus"
        else:
            text += f", dommages {self.dommages:+d}, {self.dommages_pourcent:+d} %"
            if self.dommages_physiques:
                text += f" (dommages physiques {self.dommages_physiques:+d}, pas comptés)"
        others = [f"{name} {value}" for name, value in (("PA", self.pa), ("PM", self.pm), ("portée", self.portee))
                  if value is not None]
        return text + (f" ; {', '.join(others)}" if others else "")


_FIELDS = {f.name for f in fields(CharacterStats)} - {"source"}
Windows = tuple[Point | None, Point | None]  # coins de « Tes caractéristiques » et de « Caracteristiques avancées »


def _windows(frame: Frame) -> Windows:
    """Fenêtres des caractéristiques ouvertes sur cette capture (titres cherchés dans tout l'écran : ~0,1 s chacun)."""
    return find_stats_window(frame), find_advanced_window(frame)


class StatsReader:
    """Ouvre les fenêtres des caractéristiques d'un personnage, les lit, les referme (Échap)."""

    def __init__(self, game) -> None:
        self.game = game

    def read(self) -> CharacterStats | None:
        """READ_TRIES essais de la fenêtre avancée ; à défaut, « Tes caractéristiques » ; None si rien n'est lisible
        (avertissement et capture : le combat se fera avec les dégâts de base des sorts)."""
        fallback: StatsReading | None = None
        problem = ""
        for attempt in range(1, READ_TRIES + 1):
            reading, simple, problem = self._attempt()
            self._close()
            if simple is not None and not simple.missing(SIMPLE_REQUIRED):
                fallback = simple
            if reading is not None:
                return CharacterStats.from_reading(reading)
            if attempt < READ_TRIES:
                log.info("Caractéristiques : %s ; nouvel essai", problem)
        if fallback is not None:
            log.warning("Caractéristiques : %s ; lues dans « Tes caractéristiques », sans les bonus aux dommages "
                        "(comptés 0)", problem)
            return CharacterStats.from_reading(fallback)
        log.warning("Caractéristiques : %s ; je continue sans elles (dégâts de base des sorts)", problem)
        return None

    def _attempt(self) -> tuple[StatsReading | None, StatsReading | None, str]:
        """(fenêtre avancée lue en entier, « Tes caractéristiques » lue, ce qui a manqué)."""
        frame = self.game.look()
        simple_at, advanced_at = _windows(frame)
        if simple_at is None and advanced_at is None:
            button = find_stats_button(frame)
            if button is None:
                return None, None, self._failed(frame, "bouton des caractéristiques introuvable",
                                                "bouton_caracteristiques_introuvable")
            self.game.click(button, frame.layout.spread(8, 8))
            frame, (simple_at, advanced_at) = self._wait(lambda shown: any(shown))
            if simple_at is None and advanced_at is None:
                return None, None, self._failed(frame, "fenêtre « Tes caractéristiques » pas ouverte",
                                                "caracteristiques_pas_ouvertes")
        # « Tes caractéristiques » d'abord : la fenêtre avancée peut la couvrir.
        simple = read_stats_window(frame, simple_at) if simple_at is not None else None
        if advanced_at is None:
            sword = find_sword_button(frame, simple_at)
            if sword is None:
                return None, simple, self._failed(frame, "bouton épée introuvable", "epee_caracteristiques_introuvable")
            self.game.click(sword, frame.layout.spread(*SWORD_SPREAD))
            frame, (_, advanced_at) = self._wait(lambda shown: shown[1] is not None)
            if advanced_at is None:
                return None, simple, self._failed(frame, "fenêtre « Caracteristiques avancées » pas ouverte",
                                                  "caracteristiques_avancees_pas_ouvertes")
        reading = read_advanced_window(frame, advanced_at)
        missing = reading.missing(REQUIRED) if reading is not None else list(REQUIRED)
        if missing:
            return None, simple, self._failed(frame, "illisible dans « Caracteristiques avancées » : "
                                              + ", ".join(missing), "caracteristiques_illisibles")
        self.game.recorder.save(frame.image, "caracteristiques", lossless=True)
        unread = [key for key, value in reading.values.items() if value is None]
        if unread:
            log.info("Caractéristiques : pas lues (sans effet sur les dégâts) : %s", ", ".join(unread))
        return reading, simple, ""

    def _failed(self, frame: Frame, problem: str, label: str) -> str:
        path = self.game.recorder.save(frame.image, label, lossless=True)
        return f"{problem} (capture : {path})"

    def _wait(self, expected: Callable[[Windows], bool]) -> tuple[Frame, Windows]:
        """Dernière capture et fenêtres ouvertes dessus : dès que `expected` les voit, au plus OPEN_CHECKS regards."""
        for _ in range(OPEN_CHECKS):
            self.game.sleep(OPEN_WAIT)
            frame = self.game.look()
            shown = _windows(frame)
            if expected(shown):
                break
        return frame, shown

    def _close(self) -> None:
        """Échap tant qu'une fenêtre des caractéristiques est ouverte (CLOSE_TRIES fois au plus), jamais sans elle : Échap
        sur la carte seule n'a pas été essayé (il pourrait ouvrir autre chose)."""
        for _ in range(CLOSE_TRIES):
            if not any(_windows(self.game.look())):
                return
            self.game.press(CLOSE_KEY)
            self.game.sleep(0.4)
        frame = self.game.look()
        if any(_windows(frame)):
            path = self.game.recorder.save(frame.image, "caracteristiques_pas_fermees", lossless=True)
            log.warning("Fenêtre des caractéristiques pas fermée par Échap (capture : %s)", path)
