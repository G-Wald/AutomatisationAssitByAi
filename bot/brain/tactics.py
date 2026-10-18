"""Ce que la tactique sait d'un sort, et ce qui a été lancé (logique pure : pas d'écran, testable).

Le choix des sorts, de leurs cibles et des déplacements est fait par brain/engine.py, pour toute classe, à partir
de ces seuls champs (tirés de la référence Solomonk). Priorités validées le 27/09, reprises dans son score :
bonus de portée (Tir Éloigné) en début de tour s'il reste de quoi attaquer ensuite ; ennemi à 3 cases ou moins
-> le repousser (Flèche de Recul) ; sinon le plus de dégâts (zones comprises), poussée contre un obstacle en plus.
Bonus de dégâts (04/10, demande de l'utilisateur : « si on a la portée pour taper, autant booster les dégâts ») :
lancés sur soi avant les attaques du tour, quand ils rapportent plus que les PA qu'ils prennent (Maîtrise de l'Arc,
Tir Puissant du Crâ ; Puissance, Compulsion, Amplification du Iop).

Contraintes : PA, portée (+ bonus si « portée modifiable »), lancer en ligne, ligne de vue, lancers par tour,
lancers par cible, délai de relance.
"""

import math
from collections import Counter
from dataclasses import dataclass, field

from bot.perception.grid import Cell

DANGER_DISTANCE = 3
PUSH_BLOCKED_BONUS = 6.0  # dégâts de poussée estimés quand la cible repoussée heurte un obstacle
HITS_KEPT = 20  # dégâts mesurés gardés par sort (les plus récents)


@dataclass(frozen=True)
class CombatSpell:
    slot: int
    spell_id: str
    name: str
    pa: int
    spell_range: tuple[int, int]
    in_line: bool = False
    needs_sight: bool = True
    range_modifiable: bool = True
    per_turn: int | None = None
    per_target: int | None = None
    cooldown: int | None = None  # tours entre deux lancers
    damage: float = 0.0  # dégâts moyens par ennemi touché
    push: int = 0  # cases de recul données aux ennemis touchés
    # Zone des dégâts (et du recul) : (forme, taille) comme chez Solomonk — point, cercle, croix, ligne,
    # ligne_perpendiculaire, anneau, damier ; taille 0 : la cible seule (voir engine.in_zone).
    zone: tuple[str, int] = ("point", 0)
    range_bonus: int = 0  # PO donnés (sort de bonus)
    bonus_turns: int = 0
    role: str = "degats"
    # Bonus de dégâts donnés (sort de bonus lancé sur soi) : « +40 de dommages » par ligne de dégâts, « Augmente les
    # dommages de 140 % » (de la base de chaque ligne), pendant boost_turns tours (le tour du lancer compris).
    boost_flat: float = 0.0
    boost_pct: float = 0.0
    boost_turns: int = 0
    # Ce qu'un bonus de dégâts change à ce sort (formule des caractéristiques, validée le 04/10 : ligne = base x
    # (100 + carac + %) / 100 + dommages) : base des lignes de dégâts (pour les %) et nombre de lignes (pour les
    # dommages fixes), poisons comptés à chaque tour. None : inconnus (sort configuré) -> damage et 1 ligne.
    damage_base: float | None = None
    damage_lines: float | None = None

    @property
    def damage_boost(self) -> bool:
        """Sort de bonus de dégâts (sans dégâts lui-même)."""
        return self.damage <= 0 and bool(self.boost_flat or self.boost_pct)

    @property
    def on_self(self) -> bool:
        """Lancé sur moi : portée 0, ou bonus de dégâts qui peut se lancer sur sa propre case (portée 0 à n)."""
        return self.spell_range == (0, 0) or (self.damage_boost and self.spell_range[0] == 0)

    def boosted(self, flat: float = 0.0, pct: float = 0.0) -> float:
        """Dégâts moyens avec des bonus de dégâts en cours (`flat` par ligne, `pct` % de la base)."""
        if self.damage <= 0 or not (flat or pct):
            return self.damage
        base = self.damage if self.damage_base is None else self.damage_base
        lines = 1.0 if self.damage_lines is None else self.damage_lines
        return self.damage + base * pct / 100 + lines * flat


@dataclass
class SpellUse:
    """Ce qui a été lancé : délais de relance sur le combat, compteurs sur le tour."""

    turn: int = 0
    pa: int = 6
    last_cast_turn: dict[str, int] = field(default_factory=dict)
    casts_this_turn: Counter = field(default_factory=Counter)
    casts_on_target: Counter = field(default_factory=Counter)  # (sort, case de la cible)
    refused: set = field(default_factory=set)  # (sort, case) refusés par le jeu ce tour
    # Sort refusé au-delà de sa portée (marge d'une carte à relief) : distance d'ici à partir de laquelle il ne part
    # plus, pour lui et les sorts qui portent moins loin (refused_beyond). Oublié après un déplacement.
    beyond: dict[str, int] = field(default_factory=dict)
    # Sort de zone refusé d'ici sur une case vide : plus d'autre case vide pour lui d'ici (18/10, Champs 4,3 : Flèche
    # Enflammée refusée sur 3 cases vides alignées vers un ennemi caché par un bloc, puis de nouveau après un pas, 6
    # refus et ~20 s pour Vertdepomme, autant pour Vieenjaune). Oublié après un déplacement, comme `refused`.
    empty_refused: set = field(default_factory=set)
    # Refus sur des cases vides, ce combat (pas oubliés après un déplacement) : à EMPTY_REFUSALS_LIMIT, ce sort ne vise
    # plus que des ennemis jusqu'à la fin du combat. 18/10, Chafers 3,6 : la même case vide (18, 10) refusée à
    # Vertdepomme depuis 3 cases de suite, un pas entre chaque (le pas efface `empty_refused`).
    empty_refusals: Counter = field(default_factory=Counter)
    unavailable: set = field(default_factory=set)  # sorts non sélectionnables ce tour
    greyed: set = field(default_factory=set)  # sorts grisés dans la barre sur la dernière capture
    measured: dict[str, tuple[int, int]] = field(default_factory=dict)  # portée affichée ce tour
    bonuses: list[tuple[int, int]] = field(default_factory=list)  # (PO en plus, dernier tour où ça vaut)
    boosts: list[tuple[float, float, int]] = field(default_factory=list)  # (dommages, %, dernier tour où ça vaut)
    fight_casts: Counter = field(default_factory=Counter)  # lancers de chaque sort (nom) sur tout le combat (bilan)
    # Portée de l'équipement (caractéristiques lues en début de session, 04/10) : en plus pour les sorts à portée
    # modifiable, tout le combat. La fenêtre des sorts est supposée montrer la portée sans elle (à vérifier avec un
    # équipement qui en donne).
    gear_range: int = 0

    def new_turn(self, pa: int) -> None:
        self.turn += 1
        self.pa = pa
        self.casts_this_turn.clear()
        self.casts_on_target.clear()
        self.refused.clear()
        self.beyond.clear()
        self.empty_refused.clear()
        self.unavailable.clear()
        self.greyed.clear()
        self.measured.clear()

    @property
    def range_bonus(self) -> int:
        return self.gear_range + sum(po for po, until in self.bonuses if until >= self.turn)

    @property
    def damage_bonus(self) -> tuple[float, float]:
        """Bonus de dégâts en cours ce tour : (dommages par ligne, % de la base)."""
        active = [(flat, pct) for flat, pct, until in self.boosts if until >= self.turn]
        return sum(f for f, _ in active), sum(p for _, p in active)

    def gain(self, spell: CombatSpell) -> float:
        """Dégâts en plus de `spell` grâce aux bonus de dégâts en cours."""
        return spell.boosted(*self.damage_bonus) - spell.damage

    def record(self, spell: CombatSpell, target: Cell) -> None:
        self.pa -= spell.pa
        self.last_cast_turn[spell.spell_id] = self.turn
        self.casts_this_turn[spell.spell_id] += 1
        self.casts_on_target[(spell.spell_id, target)] += 1
        self.fight_casts[spell.name] += 1
        if spell.range_bonus:
            self.bonuses.append((spell.range_bonus, self.turn + max(spell.bonus_turns, 1) - 1))
        if spell.damage_boost:
            self.boosts.append((spell.boost_flat, spell.boost_pct, self.turn + max(spell.boost_turns, 1) - 1))

    def available(self, spell: CombatSpell) -> bool:
        if spell.spell_id in self.unavailable or spell.spell_id in self.greyed or spell.pa > self.pa:
            return False
        last = self.last_cast_turn.get(spell.spell_id)
        if spell.cooldown and last is not None and self.turn - last < spell.cooldown:
            return False
        if spell.on_self and spell.per_target and self.casts_this_turn[spell.spell_id] >= spell.per_target:
            return False  # sur soi : une seule cible, moi
        return not (spell.per_turn and self.casts_this_turn[spell.spell_id] >= spell.per_turn)


@dataclass
class HitLog:
    """Dégâts réellement faits par chaque sort, mesurés à la vie de la cible lue au survol avant et après un lancer ;
    gardés sur la session. Les dégâts de base du sort (Solomonk) ne comptent pas les caractéristiques du personnage :
    le 03/10, Lancer de Pièces (1 à 6 de base, 3,5 en moyenne) a fait 1 à 11 dans le chat, environ 5 en moyenne.
    Depuis le 04/10, CombatSpell.damage compte les caractéristiques lues en début de session (brain/stats.py) : c'est
    la valeur de départ. Moyenne des mesures, avec ces dégâts attendus comptés comme une mesure : un coup critique
    isolé ne fait pas croire à des dégâts doublés."""

    seen: dict[str, list[int]] = field(default_factory=dict)

    def learn(self, spell: CombatSpell, damage: int) -> None:
        values = self.seen.setdefault(spell.spell_id, [])
        values.append(damage)
        del values[:-HITS_KEPT]

    def expected(self, spell: CombatSpell, gain: float = 0.0) -> float:
        """Dégâts moyens attendus ; `gain` : en plus, bonus de dégâts en cours (SpellUse.gain)."""
        values = self.seen.get(spell.spell_id, [])
        return max(1.0, (spell.damage + sum(values)) / (1 + len(values))) + gain

    def casts_to_kill(self, spell: CombatSpell, hp: int, gain: float = 0.0) -> int:
        """Lancers qui devraient tuer une cible à `hp` PV (dégâts moyens) ; 0 si elle n'en a plus."""
        return max(0, math.ceil(hp / self.expected(spell, gain)))


def aligned(a: Cell, b: Cell) -> bool:
    """Sur une même ligne de la grille (les deux diagonales de l'écran) : condition des sorts « en ligne »."""
    di, dj = b[0] - a[0], b[1] - a[1]
    return (di, dj) != (0, 0) and abs(di) == abs(dj)


def unit(a: Cell, b: Cell) -> Cell:
    """Pas de la grille dans la direction de `a` vers `b` (signe de chaque coordonnée)."""
    di, dj = b[0] - a[0], b[1] - a[1]
    return (di > 0) - (di < 0), (dj > 0) - (dj < 0)


EMPTY_REFUSALS_LIMIT = 2  # refus sur des cases vides, un combat : ce sort ne vise plus que des ennemis (SpellUse)


def spell_range(spell: CombatSpell, use: SpellUse) -> tuple[int, int]:
    if spell.spell_id in use.measured:
        return use.measured[spell.spell_id]  # affichée par le jeu ce tour : bonus compris
    low, high = spell.spell_range
    return low, high + (use.range_bonus if spell.range_modifiable else 0)


def refusals(refused: CombatSpell, target: Cell, spells: list[CombatSpell], use: SpellUse) -> set[tuple[str, Cell]]:
    """Le jeu a refusé `refused` sur `target` (curseur interdit : hors de portée ou sans ligne de vue). D'ici, il
    refuserait aussi tout sort dont la portée tient dans la sienne et qui en demande au moins autant (ligne de vue, en
    ligne) : la ligne de vue ne dépend pas du sort (03/10, carte à relief : Flèche Magique puis Flèche Glacée refusées
    sur le même ennemi, depuis la même case). Entrées pour SpellUse.refused."""
    low, high = spell_range(refused, use)
    same = {(refused.spell_id, target)}
    for spell in spells:
        if spell.on_self:
            continue
        lo, hi = spell_range(spell, use)
        if low <= lo and hi <= high and (spell.needs_sight or not refused.needs_sight) \
                and (spell.in_line or not refused.in_line):
            same.add((spell.spell_id, target))
    return same


def refused_beyond(refused: CombatSpell, gap: int, spells: list[CombatSpell], use: SpellUse) -> dict[str, int]:
    """Le jeu a refusé `refused` à `gap` cases, plus loin que sa portée (marge d'une carte à relief) : d'ici, il
    refuserait aussi toute case aussi loin ou plus, et de même pour les sorts qui portent moins loin. 11/10 : Gela, 4
    refus de suite à 10 cases (portée 1 à 9), une autre case de la zone visée à chaque fois, ~5 s par refus. Entrées
    pour SpellUse.beyond (rien si la cible était à portée : ligne de vue, refus propre à cette case)."""
    high = spell_range(refused, use)[1]
    if gap <= high:
        return {}
    return {spell.spell_id: min(gap, use.beyond.get(spell.spell_id, gap)) for spell in spells
            if not spell.on_self and spell_range(spell, use)[1] <= high}


def reach_range(spells: list[CombatSpell], use: SpellUse) -> tuple[int, int] | None:
    """Portée à viser en se déplaçant : celle du sort de dégâts qui porte le plus loin (sans contrainte de ligne)."""
    ranges = [spell_range(s, use) for s in spells if s.damage > 0 and not s.in_line]
    return max(ranges, key=lambda r: r[1]) if ranges else None
