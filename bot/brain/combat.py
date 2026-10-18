"""IA de combat : plan du tour par le moteur (brain/engine.py), exécuté pas à pas ; placement au début du combat.

Déroulé d'un tour (« horizon glissant ») :
1. carte « propre » -> obstacles lus sur la carte tactique (perception/sight.py) ;
2. plan : [déplacement] + sorts + [repli], comparant dégâts et déplacements depuis chaque case atteignable, avec
   les PA lus dans la bulle bleue (un monstre peut en retirer), les PM de la bulle verte, les sorts grisés dans la
   barre, la portée, le lancer en ligne, la ligne de vue et les limites de chaque sort, la vie des ennemis lue au
   survol (MapMemory.life, partagée par l'équipe : achever, taper la même cible) ; un seul style, polyvalent :
   toujours taper si possible, ne pas finir au contact d'un ennemi, ne gêner ni les lignes de vue ni les pas des
   alliés (voir brain/engine.py) ;
3. seule la première étape est jouée, puis capture et nouveau plan (PA, morts, cibles refusées, portées mesurées) ;
   rafale (combat.burst) : les relances prévues du même sort sur la même cible s'enchaînent d'abord, limitées par
   la vie de la cible lue au survol (voir _burst) ; si aucun sort ne touche ce tour : placement du moteur pour le
   tour suivant (case d'où taper au plus vite, ligne de vue dégagée des alliés, hors contact), SANS sélectionner de
   sort, un seul par tour ; 4. fin de tour.

Chaque action est vérifiée à l'écran plutôt que supposée réussie :
- sort sur soi (bonus) -> clic droit sur sa case de la barre : le jeu le lance directement sur moi ;
- sort sélectionné avec son raccourci clavier (jamais par un clic sur sa case) -> les cases de portée bleues
  apparaissent (sinon : plus de PA ou sort indisponible) ;
  elles donnent la portée réelle du sort et les obstacles, mémorisés pour le combat ;
- cible survolée -> pas de curseur « interdit » (sinon : hors de portée ou pas de ligne de vue) ;
- sort lancé -> les PA (bulle bleue) ou l'icône du sort ont changé ; le jeu ne les met à jour qu'à la fin
  de l'animation, donc on les surveille quelques secondes ; sinon le clic n'est pas passé et on relance ;
- déplacement -> mon cercle a changé de case ; fin de tour -> la timeline passe à un autre combattant.

Rythme de ces vérifications (réglage timing.wait_mode) : « fixe » (validé) attend un temps fixe puis regarde ;
« condition » (à l'essai) regarde dès le geste, une capture toutes les 0,1 s, jusqu'à voir le résultat attendu, avec
le même délai au plus : mêmes conclusions, plus tôt. Une ligne « Tour : ... » par tour permet de comparer les deux.

Si la carte n'est pas en mode tactique, le bot l'active (couleurs de portée et de déplacement fiables).
Placement : case libre de ma couleur d'où un ennemi est à portée, la plus loin des ennemis possible.

Mode équipe (plusieurs personnages de ma couleur) : voir play_turn (is_mine, me_cell, find_me).
"""

import logging
import random
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto

from bot.brain.engine import (
    CAST,
    CONTACT_PENALTY,
    MOVE,
    MOVE_COST,
    POSITION,
    RETREAT,
    TACKLE_PENALTY,
    ZONE_FALLOFF,
    Board,
    Step,
    TurnPlan,
    Weights,
    cast_blocker,
    plan_turn,
)
from bot.brain.game import Game
from bot.brain.spellbook import SpellBook
from bot.brain.tactics import (DANGER_DISTANCE, CombatSpell, HitLog, SpellUse, reach_range, refusals, refused_beyond,
                               spell_range)
from bot.debug.draw import annotate
from bot.perception.chat import cast_announced, combat_lines
from bot.perception.fighters import Fighter, clear_point, find_fighters
from bot.perception.frame import Frame
from bot.perception.grid import Cell, distance
from bot.perception.hp import read_hovered_hp
from bot.perception.placement import read_placement, standing_cells
from bot.perception.sight import (RangeView, Terrain, in_line_only, line_of_sight, range_center, read_blocks,
                                  read_range, read_terrain, sight_agreement)
from bot.perception.spellbook import slot_greyed
from bot.perception.timeline import timeline_portraits
from bot.perception.toolbar import ToolbarButton, find_creature_button, find_tactical_button
from bot.perception.ui import (
    END_TURN_SPREAD,
    PA_BUBBLE,
    SPELL_SLOT_SPREAD,
    default_slot_key,
    find_end_turn_button,
    forbidden_cursor_near,
    pa_changed,
    read_pa,
    read_pm,
    region_changed,
    spell_range_visible,
    spell_slot_box,
    spell_slot_point,
    tactical_mode,
)
from bot.state.scene import Phase, is_my_turn, read_scene

log = logging.getLogger(__name__)

CAST_ATTEMPTS = 3  # clics sur la cible au plus, quand le sort ne part pas
LOCATE_RANGE = 6  # repérage par la portée d'un sort : portée maximale visée (losange visible en entier, assez grand)
NOT_GONE_LIMIT = 2  # lancers de suite jamais partis sur un ennemi : sort écarté pour la session (03/10)
SELF_CAST_METHODS = ("clic droit", "raccourci + clic sur moi")  # sort sur moi : une tentative de chaque
# Bonus de portée qu'un allié peut m'avoir donné sans que je le sache (Tir Éloigné touche les alliés à 2 cases : +2 au
# niveau 2) : une portée lue jusque-là au-delà de celle du sort reste crédible.
ALLY_RANGE_BONUS = 2
MAX_CASTS_PER_TURN = 5  # garde-fou
RELIEF_MARGIN = 4  # cases de marge sur la portée prévue, sur une carte à plusieurs niveaux
RELIEF_MIN_WRONG = 2  # cases à portée mal prédites, au moins, pour croire la carte à relief (une seule : animation, ombre)
# Arrière-plan, 2e rangée de la barre (cases 8 à 14) : Ctrl+1 à Ctrl+7 ne passent pas (l'état de Ctrl est celui du
# vrai clavier) et un clic gauche sur la case ne sélectionne pas le sort (Épée du destin, 11/10). Le jeu leur donne un
# 2e raccourci, « 1 » à « 7 » (Raccourcis clavier, capture de l'utilisateur du 11/10) : touches du pavé numérique.
# Sans effet NUMPAD_MISSES fois (sort avec ses PA, pas grisé) : attaques de la 2e rangée écartées pour la session.
ROW2_BACKGROUND_KEY = "num{}"
NUMPAD_MISSES = 2
CONFIRM_EVERY = 0.15  # secondes entre deux vérifications qu'un sort est parti (PA mis à jour en fin d'animation)
# Chat de combat (perception/chat.py) : « Nom lance Sort. » écrit dès que le sort part ; rien pour un refus. 17/10 : un
# refus coûtait after_cast + cast_confirm (4,6 s) ; sans annonce ni PA dépensés en CHAT_REFUSED_AFTER s, refusé.
CHAT_EVERY = 0.3  # s entre deux lectures du chat (OCR : ~40 ms)
CHAT_REFUSED_AFTER = 2.0  # s après le clic sans annonce ni PA dépensés : sort refusé (temps d'annonce mesuré au journal)
CHAT_PA_GRACE = 1.2  # s après l'annonce pour voir les PA changer (capture de départ d'une rafale), puis parti quand même
MOVES_PER_TURN = 3  # déplacements au plus par tour : aller lancer, encore un pas, repli (01/10)
TURN_RECHECK = 0.6  # secondes avant de revérifier que mon tour est vraiment fini
# « Mon tour » moins de SAME_TURN_WINDOW secondes après la fin du précédent, sans mes PA pleins : c'est la suite du même
# tour (F1 ignoré pendant l'animation du dernier sort, ou fin de tour mal vue), pas un nouveau tour ; ses compteurs
# (lancers, relances, bonus) sont gardés. 11/10 : Gela, « 4 PA ce tour (au lieu de 10) » 2,2 s après son F1 ;
# Brawli, « 6 PA » 3 s après son premier Tempête de Puissance. Un vrai nouveau tour vient après celui de tous les
# autres combattants.
SAME_TURN_WINDOW = 5.0
# Réglages des anciens styles agressif / prudent (un seul style, polyvalent, depuis le 03/10) : encore lus dans un
# vieux config/settings.yaml, ignorés avec un avertissement (une fois par lancement).
OBSOLETE_SETTINGS = ("style", "approach_below", "flee")
_warned_obsolete: set[str] = set()
WAIT_MODES = ("fixe", "condition")  # timing.wait_mode
RANGE_SHOWN = 0.15  # fixe : capture 0,15 s après la touche du sort, portée affichée ou non
RANGE_WAIT = 0.6  # condition : portée guettée au plus 0,6 s après la touche
CURSOR_WAIT = 0.25  # curseur « interdit » : capture 0,25 s après avoir posé la souris (condition : guetté jusque-là)
# Rafale (combat.burst, 03/10) : capture 0,15 s après avoir reposé la souris sur la cible, la touche du sort pressée
# juste avant (comme RANGE_SHOWN) ; fiche de la cible attendue 0,1 s de plus si elle manque (elle suit la souris).
BURST_HOVER, SHEET_RECHECK = 0.15, 0.1
# Rafale rapide (combat.fast_end) : touche du sort suivant pressée BURST_GAP s après le clic, sans attendre les PA ; tant
# que le jeu ne la prend pas (animation du lancer précédent), une nouvelle pression toutes les BURST_REPRESS s, jusqu'à
# BURST_READY s. 04/10 : 22 rafales sur 22 prises à la première pression, pendant l'animation : 0,3 -> 0,1 s.
BURST_GAP, BURST_REPRESS, BURST_READY = 0.1, 0.35, 2.0
BURST_RECHECK = 0.3  # cible plus vue après un lancer en rafale : second regard (animation, chiffres de dégâts)
# Sort refusé juste après un lancer (04/10 : 3e Tempête de Puissance du Iop demandée 0,4 s après le clic de la 2e,
# pendant son animation : « non sélectionnable », Épée Céleste lancée à la place, l'ennemi à 25 PV resté debout) :
# même touche une fois de plus, après une courte pause.
RESELECT_AFTER_CAST, RESELECT_PAUSE = 1.5, 0.4
PA_BUFF_MAX = 4  # PA en plus du maximum (bonus d'un allié) : au-delà, la bulle est mal lue (« 10 » lu « 16 » en JPEG)
ARRIVAL_CHECKS, ARRIVAL_EVERY = 8, 0.2  # fixe : arrivée vérifiée 8 fois, toutes les 0,2 s, après per_cell_moved
TURN_PASSED_EVERY = 0.25  # fixe : main passée vérifiée toutes les 0,25 s après la fin de tour


class Cast(Enum):
    DONE = auto()
    NO_SPELL = auto()  # pas de portée affichée : plus assez de PA, sort en relance, ou mauvaise case
    OUT_OF_REACH = auto()  # hors de portée ou sans ligne de vue
    STUCK = auto()  # sort resté sélectionné malgré l'annulation : plus rien à faire ce tour


@dataclass(frozen=True)
class CombatSettings:
    spell_slot: int = 3
    spell_key: str = "auto"  # auto : raccourci par défaut de la case ; "" : clic sur la case (sans raccourci)
    casts_per_turn: int = 1
    cancel_key: str = "escape"
    end_turn_key: str = ""
    max_pm: int = 3
    max_pa: int = 6  # si la bulle des PA est illisible
    danger_distance: int = DANGER_DISTANCE
    spell_range: tuple[int, int] = (1, 7)  # portée supposée tant qu'elle n'a pas été mesurée à l'écran
    contact_penalty: float = CONTACT_PENALTY  # moteur : finir au contact d'un ennemi coûte autant de dégâts
    tackle_penalty: float = TACKLE_PENALTY  # moteur : quitter le contact d'un ennemi (tacle)
    move_cost: float = MOVE_COST  # moteur : par PM dépensé
    zone_falloff: float = ZONE_FALLOFF  # moteur : dégâts de zone perdus par case d'éloignement de la case visée
    turn_budget: float = 25.0  # secondes d'actions au plus, avant de finir le tour quoi qu'il arrive
    after_cast: float = 0.5  # début de l'animation, avant de surveiller les PA
    cast_confirm: float = 4.0  # surveillance des PA / de l'icône, avant de conclure que le sort n'est pas parti
    per_cell_moved: float = 0.3
    wait_mode: str = "fixe"  # fixe : pauses fixes puis vérification ; condition : guetter le résultat à l'écran
    burst: bool = False  # rafale : même sort relancé sur la même cible sans nouveau plan entre deux lancers (_burst)
    # Accélérations (demande du 04/10, peu de clics de sort ratés) : dernier sort du tour, fin de tour aussitôt sans
    # attendre la fin de son animation ; en rafale, la touche du sort suivant pressée sans attendre que les PA baissent.
    fast_end: bool = False
    creature_mode: bool = False  # mode créature activé en combat (bouton boule, à côté du mode tactique ; 04/10)

    @classmethod
    def from_settings(cls, settings: dict) -> "CombatSettings":
        combat = settings.get("combat") or {}
        spell = combat.get("spell") or {}
        timing = settings.get("timing") or {}
        for key in OBSOLETE_SETTINGS:
            if key in combat and key not in _warned_obsolete:
                _warned_obsolete.add(key)
                log.warning("Réglage combat.%s ignoré : un seul style de combat, polyvalent (taper vite, à l'abri, sans "
                            "gêner les alliés) ; tu peux l'enlever de config/settings.yaml", key)
        return cls(
            spell_slot=int(spell.get("slot", cls.spell_slot)),
            spell_key=str(spell.get("key", cls.spell_key) or ""),
            casts_per_turn=int(spell.get("casts_per_turn", cls.casts_per_turn)),
            cancel_key=combat.get("cancel_key") or cls.cancel_key,
            end_turn_key=combat.get("end_turn_key") or "",
            max_pm=int(combat.get("max_pm", cls.max_pm)),
            max_pa=int(combat.get("max_pa", cls.max_pa)),
            danger_distance=int(combat.get("danger_distance", cls.danger_distance)),
            spell_range=tuple(int(v) for v in spell.get("range", cls.spell_range)),
            contact_penalty=float(combat.get("contact_penalty", cls.contact_penalty)),
            tackle_penalty=float(combat.get("tackle_penalty", cls.tackle_penalty)),
            move_cost=float(combat.get("move_cost", cls.move_cost)),
            zone_falloff=float(combat.get("zone_falloff", cls.zone_falloff)),
            after_cast=float(timing.get("after_cast", cls.after_cast)),
            cast_confirm=float(timing.get("cast_confirm", cls.cast_confirm)),
            per_cell_moved=float(timing.get("per_cell_moved", cls.per_cell_moved)),
            wait_mode=_wait_mode(timing.get("wait_mode", cls.wait_mode)),
            burst=bool(combat.get("burst", cls.burst)),
            fast_end=bool(combat.get("fast_end", cls.fast_end)),
            creature_mode=bool(combat.get("creature_mode", cls.creature_mode)),
        )

    @property
    def resolved_spell_key(self) -> str:
        return default_slot_key(self.spell_slot) if self.spell_key == "auto" else self.spell_key

    @property
    def weights(self) -> Weights:
        """Poids du moteur de tour (style polyvalent) ; les autres sont ceux de brain/engine.py."""
        return Weights(contact=self.contact_penalty, tackle=self.tackle_penalty, move=self.move_cost,
                       danger=self.danger_distance, zone_falloff=self.zone_falloff)


def _wait_mode(value) -> str:
    mode = str(value).lower()
    if mode not in WAIT_MODES:
        raise ValueError(f"timing.wait_mode doit valoir fixe ou condition (pas {value!r})")
    return mode


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count > 1 else ''}"


@dataclass
class TurnStats:
    """Mesure d'un tour pour comparer les modes d'attente (timing.wait_mode) : une ligne au journal en fin de tour.

    Commence au premier geste du tour (sort, déplacement ou fin de tour) : la capture et le plan qui le précèdent
    sont les mêmes dans les deux modes. `turn` : numéro du tour (SpellUse.turn) ; un tour fini sans fin de tour
    (combat gagné par mon dernier sort, timer) n'a pas de ligne, et ses compteurs ne passent pas au tour suivant."""

    turn: int
    started: float
    tries: int = 0  # sorts tentés (_cast)
    casts: int = 0  # sorts partis
    missed_clicks: int = 0  # clics sur la cible sans effet visible (sort relancé)
    moves: int = 0  # clics de déplacement
    stuck_moves: int = 0  # déplacements sans bouger
    burst: int = 0  # sorts partis en rafale (compris dans casts)

    def describe(self, now: float, wait_mode: str) -> str:
        """« Tour : 6,2 s ; 2 sorts, 1 déplacement, 0 refus ; attentes : condition » (« 3 sorts (dont 2 en rafale) »)."""
        seconds = f"{now - self.started:.1f}".replace(".", ",")
        casts = _plural(self.casts, "sort") + (f" (dont {self.burst} en rafale)" if self.burst else "")
        counts = [casts, _plural(self.moves, "déplacement"), f"{self.tries - self.casts} refus"]
        if self.missed_clicks:
            counts.append(f"{self.missed_clicks} clic{'s' if self.missed_clicks > 1 else ''} sans effet")
        if self.stuck_moves:
            counts.append(f"{_plural(self.stuck_moves, 'déplacement')} sans bouger")
        return f"Tour : {seconds} s ; {', '.join(counts)} ; attentes : {wait_mode}"


@dataclass
class TargetLife:
    """Vie d'une cible lue au survol (sa fiche), et les sorts partis depuis dans le tour : (sort, case visée)."""

    turn: int
    cell: Cell
    hp: int
    casts: list[tuple[str, Cell]] = field(default_factory=list)


@dataclass
class MapMemory:
    """La carte du combat en cours, apprise capture après capture. En mode équipe, la même pour tous les personnages
    (team/loop.py) : un trou caché derrière un allié dans une fenêtre est vu dans une autre."""

    obstacles: set[Cell] = field(default_factory=set)  # blocs (coupent la ligne de vue)
    unwalkable: set[Cell] = field(default_factory=set)  # blocs et trous (on ne peut pas y marcher)
    seen: set[Cell] = field(default_factory=set)  # cases dont on a vu la nature, ou un combattant debout dessus
    known: bool = False  # terrain lu (carte tactique ou portée affichée) : ligne de vue prédite
    # Vie des ennemis, par case : lue au survol avant un sort (par n'importe quel personnage de l'équipe), puis
    # diminuée des dégâts attendus de chaque sort parti. Le moteur s'en sert pour achever un ennemi et pour que toute
    # l'équipe tape le plus entamé (03/10). Oubliée quand l'ennemi quitte sa case (déplacé, poussé, mort).
    life: dict[Cell, int] = field(default_factory=dict)

    def learn_terrain(self, terrain: Terrain, fighters: list[Fighter]) -> None:
        """Carte propre : une case revue comme du sol n'est plus un obstacle (un chiffre de dégâts ou une animation
        posés dessus un instant ne la bloquent pas pour le reste du combat)."""
        self.obstacles = (self.obstacles - terrain.seen) | terrain.blocks
        self.unwalkable = (self.unwalkable - terrain.seen) | terrain.blocks | terrain.holes
        self.seen |= terrain.seen | {f.cell for f in fighters}
        self.known = True

    def learn_range(self, view: RangeView) -> None:
        free = view.castable | view.no_sight
        # Paroi teintée par la portée (côté d'un plateau ou d'une fosse) : pas un bloc, même lue bloc avant (17/10).
        self.obstacles = (self.obstacles - free - view.walls) | view.obstacles
        self.unwalkable = (self.unwalkable - free) | view.obstacles
        self.seen |= free | view.obstacles
        self.known = True

    def unseen(self, cells: set[Cell]) -> set[Cell]:
        """Terrain lu, les cases jamais vues depuis le début du combat (cachées par le corps d'un combattant) : pas de
        marche, ce sont peut-être des trous (03/10 : l'Enutrof a voulu aller sur un trou caché derrière un allié)."""
        return cells - self.seen if self.known else set()

    def keep_life(self, enemies: set[Cell]) -> None:
        """Seule reste la vie des ennemis encore sur leur case : un autre a pu prendre celle d'un ennemi parti."""
        for cell in set(self.life) - enemies:
            del self.life[cell]

    def wound(self, cells: dict[Cell, float]) -> None:
        """Sort parti : vie connue diminuée des dégâts attendus (au moins 1 PV tant que l'ennemi est debout ; s'il est
        mort, sa case vide l'effacera à la capture suivante)."""
        for cell, damage in cells.items():
            if cell in self.life:
                self.life[cell] = max(1, round(self.life[cell] - damage))


def _aim_label(target: Fighter) -> str:
    return f"l'ennemi en {target.cell}" if target.team else f"la case {target.cell}"


def burst_length(plan: TurnPlan) -> int:
    """Lancers prévus juste après la première étape du plan, du même sort sur la même case : la rafale possible."""
    first, count = plan.first, 0
    for step in plan.steps[1:]:
        if step.kind != CAST or step.spell.spell_id != first.spell.spell_id or step.cell != first.cell:
            break
        count += 1
    return count


def choose_placement(me: Cell, free: set[Cell], enemies: set[Cell], spell_range: tuple[int, int],
                     blockers: set[Cell] | None = None) -> Cell | None:
    """Case de placement d'où un ennemi est à portée (et en ligne de vue si les blocs sont connus), la plus loin
    des ennemis ; sinon la plus proche d'eux. None si ma case actuelle est déjà la meilleure."""
    low, high = spell_range
    candidates = free | {me}

    def nearest(cell: Cell) -> int:
        return min(distance(cell, e) for e in enemies)

    def hits(cell: Cell) -> bool:
        others = (enemies | {me}) - {cell}
        return any(low <= distance(cell, e) <= high
                   and (blockers is None or line_of_sight(cell, e, (blockers | others) - {e})) for e in enemies)

    in_range = [c for c in candidates if hits(c)]
    if in_range:
        best = max(in_range, key=lambda c: (nearest(c), c == me, random.random()))
    else:
        best = min(candidates, key=lambda c: (nearest(c), c != me, random.random()))
    return None if best == me else best


def _button_spread(frame: Frame, button: ToolbarButton) -> tuple[float, float]:
    """Clic au hasard sans sortir de la partie visible du bouton (flèche ▽ du tour à côté)."""
    rx, ry = frame.layout.spread(5, 4)
    room = max(0.0, button.room - 1)
    return min(rx, room), min(ry, room)


class CombatAI:
    def __init__(self, game: Game, settings: CombatSettings, spellbook: SpellBook | None = None) -> None:
        self.game = game
        self.settings = settings
        self.spellbook = spellbook
        self._keys: dict[int, str] = {settings.spell_slot: settings.resolved_spell_key}  # case -> raccourci ("" : clic)
        self._numpad_misses = 0  # arrière-plan : sorts de la 2e rangée pas sélectionnés par le pavé numérique
        self._numpad_checked = False  # pavé numérique essayé sur une attaque de la 2e rangée (_check_numpad)
        self._numpad_ok = False  # ... et il a marché : un sort de la 2e rangée pas sélectionné l'est pour autre chose
        self._end_turn_key = settings.end_turn_key  # vidée si la touche s'avère sans effet
        self._spells: list[CombatSpell] = []  # sorts de la barre, établis au premier tour
        self._self_cast = SELF_CAST_METHODS[0]  # façon de lancer un sort sur moi qui a marché en dernier
        self._self_cast_saved = False
        # Mode équipe (voir play_turn) : qui dit si c'est mon tour, et qui est moi parmi les cercles de ma couleur.
        self._is_mine: Callable[[Frame], bool] | None = None
        self._find_me: Callable[[Frame, list[Fighter]], Fighter | None] | None = None
        # Cases connues des autres personnages (mode équipe) : ensemble, ou dictionnaire case -> nom (journal).
        self._team_cells: Callable[[], set[Cell] | dict[Cell, str]] | None = None
        self._names: dict[Cell, str] = {}  # noms des alliés, par case, pour le journal (mode équipe)
        self.placed_cell: Cell | None = None  # ma case au placement, quand elle est sûre (place)
        self._located = False  # repérage par la portée d'un sort fait ce tour
        self._locate_saved = 0
        self._me_cell: Cell | None = None  # ma case suivie pendant le tour (mode équipe)
        self._expected_plan = ""  # ce qu'il restait du dernier plan journalisé
        self._expected_step = None  # étape suivante du dernier plan (sort prévu : on dit pourquoi s'il disparaît)
        self._board = None  # terrain du dernier plan (pour le dire)
        # Sorts que le jeu refuse sur les ennemis (cliqués, jamais partis) : 03/10, Sacrifice Poupesque du Sadida (ne
        # vise que ses invocations) essayé à chaque tour, ~5 s perdues à chaque fois. Écartés pour la session.
        self._not_gone: Counter = Counter()
        self._banned: set[str] = set()
        self._hits = HitLog()  # dégâts mesurés de chaque sort, sur la session (rafale)
        self._hovered: Frame | None = None  # dernière capture souris sur la cible d'un sort (_forbidden_target)
        self._sent: Frame | None = None  # capture qui a montré le dernier sort parti (_confirm_cast)
        self._aim: tuple[Cell, tuple[float, float], tuple[float, float]] | None = None  # (case, point, dispersion) visés
        self._cast_at = float("-inf")  # heure du dernier clic de lancer (sélection refusée juste après : animation)
        self._life_saved: set[str] = set()  # captures de fiche déjà gardées (vie lue, illisible : _read_life)
        self.new_combat()

    @property
    def spells(self) -> list[CombatSpell]:
        """Sorts connus (fenêtre des sorts + référence Solomonk), sinon le sort de la case configurée."""
        if not self._spells:
            slot = self.settings.spell_slot
            entry = self.spellbook.in_slot(slot) if self.spellbook else None
            self._spells = [CombatSpell(slot, "sort_configure", f"sort de la case {slot}", pa=0,
                                        spell_range=(entry.reach if entry and entry.reach else self.settings.spell_range),
                                        damage=1.0, per_turn=self.settings.casts_per_turn)]
        return self._spells

    def new_combat(self) -> None:
        self._use = SpellUse()  # délais de relance et bonus de portée : valables sur le combat
        self._turn_ended_at: float | None = None  # fin de mon dernier tour (suite du même tour, SAME_TURN_WINDOW)
        self.map = MapMemory()  # carte de ce combat (mode équipe : partagée, team/loop.py)
        self._warned_pm = False
        self._warned_pa = False
        self._sight_saved = False
        self._map_reliable = True  # faux sur une carte à relief : mes cases y sont décalées, le jeu tranche
        self._tactical_tried = False
        self._creature_tried = False
        self._warned_not_tactical = False
        self._warned_who = False
        self._stats: TurnStats | None = None  # mesure du tour en cours (voir _turn_stats)
        self.turns_not_passed = 0  # tours d'affilée finis sans que la main passe (team/loop.py : boucle évitée)
        self._fight_turns: list[tuple[float, TurnStats]] = []  # (durée, mesure) de chaque tour fini (bilan du combat)
        self._life: TargetLife | None = None  # vie de la dernière cible survolée (rafale)
        self._life_saved.discard("vie_lue")  # capture de la fiche lue : une par combat (illisible : une par session)

    def ensure_tactical_mode(self, frame: Frame) -> None:
        """Affichage du combat : mode tactique (cube au-dessus de la timeline), puis mode créature (la boule à côté :
        personnages réduits, ils cachent moins les cases ; demande du 04/10). Une fois par combat au plus chacun, et
        jamais un clic sur un bouton déjà coché (il le désactiverait)."""
        self._ensure_tactical(frame)
        self.ensure_creature_mode(frame)

    def _ensure_tactical(self, frame: Frame) -> None:
        if self._tactical_tried or tactical_mode(frame):
            return
        button = find_tactical_button(frame)  # trouvé même sous la flèche ▽ du tour (04/10)
        if button is not None and button.active:
            return  # déjà coché (carte cachée par une fiche...) : cliquer le désactiverait
        self._tactical_tried = True
        if button is None:
            path = self.game.recorder.save(frame.image, "bouton_tactique_introuvable", lossless=True)
            log.warning("Carte hors mode tactique et bouton du mode tactique introuvable (capture : %s)", path)
            return
        log.info("J'active le mode tactique")
        self.game.click(button.point, _button_spread(frame, button))
        self.game.sleep(0.6)
        after = self.game.look(park=True)  # souris écartée du bouton : survolé, il peut changer d'aspect
        if not tactical_mode(after):
            path = self.game.recorder.save(after.image, "mode_tactique_pas_actif", lossless=True)
            log.warning("Le mode tactique ne s'est pas activé (capture : %s)", path)

    def ensure_creature_mode(self, frame: Frame) -> None:
        """Active le mode créature (boule cerclée de rouge, à droite du cube), une fois par combat au plus. Pas de
        boule au placement : essayé de nouveau à la capture suivante."""
        if self._creature_tried or not self.settings.creature_mode:
            return
        button = find_creature_button(frame)
        if button is None:
            return
        self._creature_tried = True
        if button.active:
            return
        log.info("J'active le mode créature")
        self.game.click(button.point, _button_spread(frame, button))
        self.game.sleep(0.6)
        after = self.game.look(park=True)
        check = find_creature_button(after)
        if check is None or not check.active:
            path = self.game.recorder.save(after.image, "mode_creature_pas_actif", lossless=True)
            log.warning("Le mode créature ne s'est pas activé (capture : %s)", path)

    def play_turn(self, my_team: str, is_mine: Callable[[Frame], bool] | None = None, me_cell: Cell | None = None,
                  find_me: Callable[[Frame, list[Fighter]], Fighter | None] | None = None,
                  team_cells: Callable[[], set[Cell] | dict[Cell, str]] | None = None) -> None:
        try:
            self._play_turn(my_team, is_mine, me_cell, find_me, team_cells)
        finally:
            self._turn_ended_at = time.monotonic()  # suite du même tour, si « mon tour » revient aussitôt
            if self._stats is not None:  # tour fini sans F1 (fin du combat, main passée) : compté au bilan aussi
                self._fight_turns.append((self._turn_ended_at - self._stats.started, self._stats))
                self._stats = None

    def _play_turn(self, my_team: str, is_mine: Callable[[Frame], bool] | None = None, me_cell: Cell | None = None,
                   find_me: Callable[[Frame, list[Fighter]], Fighter | None] | None = None,
                   team_cells: Callable[[], set[Cell] | dict[Cell, str]] | None = None) -> None:
        """Joue mon tour, étape par étape : plan du moteur, première étape, nouvelle capture, nouveau plan.

        Mode équipe (plusieurs personnages de ma couleur) ; sans ces paramètres, comportement d'un joueur seul :
        - `is_mine(frame)` : vrai tant que c'est le tour de CE personnage. Remplace la lecture de la timeline (couleur
          de l'équipe qui joue, timer) partout pendant le tour : boucle, confirmation des sorts, fin de tour. Le bot
          vérifie en plus que le combat continue (phase lue à l'écran) ;
        - qui est moi parmi les cercles de ma couleur : `find_me(frame, combattants de ma couleur)` -> moi, ou None
          si on ne sait pas ; sinon `me_cell`, ma case au début du tour, que le bot suit ensuite après chacun de ses
          déplacements (le cercle de ma couleur le plus proche de la case suivie). Sans l'un ni l'autre : le premier
          cercle de ma couleur (un seul en jouant seul).
        Les autres combattants de ma couleur sont des alliés : jamais visés, obstacles pour la ligne de vue et le
        déplacement, jamais pris dans la zone d'un sort.
        - `team_cells()` : dernières cases connues des autres personnages de l'équipe (ensemble, ou dictionnaire case ->
          nom pour le journal) ; un allié dont le cercle ne se voit pas (caché sous la timeline, 03/10) y est compté
          quand même.
        """
        log.info("Mon tour")
        self._is_mine, self._find_me, self._me_cell = is_mine, find_me, me_cell
        self._team_cells = team_cells
        self._located = False  # repérage par la portée d'un sort fait ce tour (_locate_by_range)
        self._expected_plan = ""
        self._expected_step = None
        frame = self.game.look()
        self.ensure_tactical_mode(frame)
        self._use_spellbook(frame)
        self._use.gear_range = self._stat("portee") or 0
        pa = self._turn_pa(frame)
        since = time.monotonic() - self._turn_ended_at if self._turn_ended_at is not None else None
        if since is not None and since < SAME_TURN_WINDOW and pa < self._max_pa:
            log.info("Suite de mon tour (%d PA, %.1f s après) : la main n'était pas passée, je garde ce que j'ai lancé",
                     pa, since)
            self._use.pa = pa
        else:
            self._use.new_turn(pa)
        self._check_numpad(frame)
        pm_start = read_pm(frame)
        pm_start = self._max_pm if pm_start is None else pm_start
        deadline = time.monotonic() + self.settings.turn_budget
        casts, moves, pm_used, stuck = 0, 0, 0, 0  # stuck : déplacements sans bouger ce tour
        visited: set[Cell] = set()  # cases déjà occupées ce tour : ne pas y revenir avant de lancer
        missed: set[Cell] = set()  # destinations pas atteintes ce tour (tacle, chemin bloqué) : pas de nouvel essai
        looked_again = False
        committed = False  # déplacé pour lancer : on lance d'ici si on peut, sans nouveau détour
        # Placé pour la fin du tour (repli, ou placement pour le tour suivant quand rien ne touche) : pas d'autre pas
        # ensuite, d'une case à l'autre (01/10 : 2 + 1 + 0 PM ; 03/10 : 1 PM, puis plus rien, 2 PM perdus).
        placed = False
        while time.monotonic() < deadline and casts < MAX_CASTS_PER_TURN:
            frame = self.game.look()
            if not self._my_turn(frame, my_team):
                # La timeline se lit mal un instant (monstre qui meurt, animation) : on revérifie avant de conclure.
                self.game.sleep(TURN_RECHECK)
                frame = self.game.look()
                if not self._my_turn(frame, my_team):
                    return  # combat fini, ou tour passé (timer écoulé, échec critique)
            fighters = find_fighters(frame)
            me, allies, enemies = self._sides(frame, fighters, my_team)
            fighters = fighters + [a for a in allies if a not in fighters]  # alliés invisibles : obstacles aussi
            if me is None or not enemies:
                if not looked_again:
                    looked_again = True  # une animation (sort, coup, mort) peut cacher un cercle un instant
                    self.game.sleep(0.3)
                    continue
                path = self.game.recorder.save(frame.image, "combattants_introuvables", lossless=True)
                log.warning("Je ne vois pas %s sur la carte (capture : %s)",
                            "mon personnage" if me is None else "d'ennemi", path)
                break
            visited.add(me.cell)
            self.map.keep_life({e.cell for e in enemies})
            self._learn_blocks(frame, fighters)
            self._read_bar(frame)
            pm = self._pm_left(frame, pm_start - pm_used)
            can_move = moves < MOVES_PER_TURN
            # Plan tenu : arrivé sur la case choisie pour lancer, on n'en change pas pour une autre un peu meilleure
            # selon des prévisions qui bougent à chaque capture (01/10 : 3 déplacements, 3 plans dans un tour).
            # Au contact d'un ennemi : les sorts d'ici d'abord (18/10, utilisateur : un déplacement d'abord est souvent
            # taclé, et l'ennemi au contact est à portée) ; se déplacer seulement si rien ne part d'ici.
            in_contact = any(distance(e.cell, me.cell) <= 1 for e in enemies)
            plan = self._plan(frame, me, enemies, allies, pm, visited, missed, can_move=can_move,
                              move_first=not committed and not in_contact)
            if (committed or in_contact) and can_move and (plan.first is None or plan.first.kind != CAST) \
                    and self._spell_fits():
                plan = self._plan(frame, me, enemies, allies, pm, visited, missed, can_move=can_move)  # rien d'ici
            self._explain_dropped(plan, me, enemies, allies)
            self._log_plan(plan)
            step = plan.first
            nearest = min(enemies, key=lambda f: distance(f.cell, me.cell))
            aim = frame.grid.center(step.cell) if step else (nearest.x, nearest.y)
            what = (step.spell.name if step.spell else step.kind) if step else "deplacement"
            self.game.recorder.save(
                annotate(frame, fighters=fighters, cells=self.map.obstacles, text=f"mon tour, {what}",
                         marks=[(aim, "cible" if step is None or step.kind == CAST else step.kind)]),
                "tour",
            )
            if step is None:
                break  # rien d'utile : ni sort qui vaille, ni meilleure case (le moteur a tout pesé)
            if step.kind != CAST:
                if placed and not plan.casts:
                    break  # déjà placé pour la fin du tour : pas de pas de plus
                moves += 1
                label, title = {RETREAT: ("repli", "Repli"),
                                POSITION: ("placement", "Placement pour le prochain tour")}.get(
                    step.kind, ("destination", "Déplacement"))
                log.info("%s de %s vers %s (%d PM) : %s", title, me.cell, step.cell, step.pm, step.reason)
                steps = self._walk(frame, me, step.cell, fighters, {step.cell}, label, step.pm)
                if steps < step.pm:
                    missed.add(step.cell)  # taclé ou chemin bloqué : nouveau plan d'ici (PM relus), sans y retourner
                if not steps:
                    stuck += 1
                    in_contact = any(distance(e.cell, me.cell) <= 1 for e in enemies)
                    if in_contact or stuck >= 2:
                        # Taclé (« Le jet d'esquive a échoué ») ou bloqué : plus aucun déplacement ce tour (04/10 :
                        # Fourbocinq, au contact d'un Prespic, 4 déplacements ratés d'affilée, ~5 s chacun).
                        moves = MOVES_PER_TURN
                        log.info("Je ne peux pas bouger (%s) : plus de déplacement ce tour",
                                 "taclé par un ennemi au contact" if in_contact else "2 déplacements sans bouger")
                    continue
                pm_used += steps
                self._use.refused.clear()  # nouvelle case : la ligne de vue a pu se dégager
                self._use.beyond.clear()
                committed = committed or step.kind == MOVE
                placed = placed or (step.kind != MOVE and steps == step.pm)
                continue
            spell = step.spell
            target = me if spell.on_self else next((e for e in enemies if e.cell == step.cell), None) or \
                Fighter("", *frame.grid.center(step.cell), step.cell)  # zone visant une case vide
            log.info("%s sur %s : %s", spell.name, "moi" if spell.on_self else _aim_label(target), step.reason)
            repeats = burst_length(plan) if self.settings.burst else 0
            # Dernière action du plan (le sort, et sa rafale) : fin de tour aussitôt après, sans attendre l'animation.
            final = self.settings.fast_end and not spell.on_self and len(plan.steps) == 1 + repeats
            result = self._cast(frame, me, target, fighters, my_team, spell, confirm=not (final and repeats == 0))
            if result is Cast.DONE:
                self._use.record(spell, step.cell)
                self._wound(spell, step)
                casts += 1
                # La suite du plan se lance d'ici (le moteur ne bouge jamais entre deux sorts) : les plans suivants
                # partent de ma case, sauf si plus rien ne s'y lance (04/10 : un plan complet, toutes cases et toutes
                # suites de sorts, coûte jusqu'à 0,4 s avec 10 PA, 5 PM et des zones ; il était refait après chaque sort).
                committed = True
                done = 0
                if self.settings.burst:
                    done, stuck = self._burst(target, step, burst_length(plan), my_team, MAX_CASTS_PER_TURN - casts,
                                              final=final)
                    casts += done
                    if stuck:
                        break
                if final and done >= repeats:
                    log.debug("Dernier sort du plan lancé : fin de tour sans attendre son animation")
                    break
            elif result is Cast.NO_SPELL:
                self._use.unavailable.add(spell.spell_id)  # plus assez de PA pour lui, relance... : un autre
            elif result is Cast.OUT_OF_REACH:
                self._use.refused |= refusals(spell, step.cell, self.spells, self._use)
                self._use.beyond.update(refused_beyond(spell, distance(me.cell, step.cell), self.spells, self._use))
            else:
                break
        self._end_turn(my_team)

    def _my_turn(self, frame: Frame, my_team: str) -> bool:
        """C'est encore mon tour : timeline (joueur seul) ou fonction du mode équipe (play_turn, is_mine)."""
        if self._is_mine is None:
            return is_my_turn(read_scene(frame), my_team)
        return read_scene(frame).phase is Phase.COMBAT and self._is_mine(frame)

    def _sides(self, frame: Frame, fighters: list[Fighter],
               my_team: str) -> tuple[Fighter | None, list[Fighter], list[Fighter]]:
        """Moi, mes alliés (les autres cercles de ma couleur) et les ennemis."""
        mine = [f for f in fighters if f.team == my_team]
        enemies = [f for f in fighters if f.team != my_team]
        me = None
        if mine and self._find_me is not None:
            me = self._find_me(frame, mine)
        if me is None and self._me_cell is not None:
            me = next((f for f in mine if f.cell == self._me_cell), None)  # un cercle sur ma case suivie : moi
        if me is None and len(mine) > 1 and not self._located:
            # Pas de cercle sur ma case suivie (ou case inconnue) : repérage par la portée d'un sort, une fois par tour
            # (03/10 : case suivie périmée, un monstre m'avait poussé : 3 déplacements ratés de suite).
            self._located = True
            me = self._locate_by_range(frame, mine, fighters)
        if me is None and self._me_cell is not None and (mine or my_team):
            # Case suivie, déjà vérifiée ce tour : mon cercle est caché (corps d'un allié devant, timeline).
            me = next((f for f in mine if f.cell == self._me_cell), None) or \
                Fighter(my_team, *frame.grid.center(self._me_cell), self._me_cell)
        if me is None and mine:
            me = mine[0]
            if len(mine) > 1 and not self._warned_who:
                self._warned_who = True
                log.warning("%d cercles de ma couleur et rien ne dit lequel est moi : je prends celui en %s (mode "
                            "équipe : play_turn(me_cell=...) ou find_me)", len(mine), me.cell)
        if me is not None and (self._find_me is not None or self._me_cell is not None):
            self._me_cell = me.cell
        allies = [f for f in mine if f is not me]
        if self._team_cells is not None and me is not None:
            seen = {f.cell for f in fighters}
            known = self._team_cells()
            self._names = dict(known) if isinstance(known, dict) else {}
            for cell in sorted(set(known) - seen - {me.cell}):
                # Cercle invisible (sous la timeline, derrière un corps) : l'allié est toujours là, jamais visé ni
                # pris dans une zone, et on ne marche pas sur sa case.
                allies.append(Fighter(my_team, *frame.grid.center(cell), cell))
        return me, allies, enemies

    def _locate_by_range(self, frame: Frame, mine: list[Fighter], fighters: list[Fighter]) -> Fighter | None:
        """Moi parmi plusieurs cercles de ma couleur quand rien d'autre ne le dit (combat repris sans connaître les
        cases : 03/10, le bot s'est pris pour un allié, déplacements et sorts faux) : la portée d'un sort s'affiche
        autour de MA case. Sa case est le centre du losange coloré (sight.range_center), cherché parmi toutes les
        cases : mon cercle peut être caché (sous la timeline, 03/10). Un sort sélectionné puis Échap (~0,5 s). None si
        la portée ne se lit pas, ou sans mode tactique."""
        known = {s.spell_id for s in self.spells}
        others = [s for s in getattr(self.spellbook, "bar_spells", lambda: [])() if s.spell_id not in known]
        # 11/10 : l'Iop n'avait aucune attaque à 2 cases de portée ; un sort hors tactique de la barre (Bond, 1 à 4)
        # se sélectionne aussi bien, et Échap l'annule sans le lancer.
        spells = [s for s in [*self.spells, *others] if not s.on_self and s.spell_range[1] >= 2]
        if not spells or not tactical_mode(frame):
            return None
        # Portée moyenne : un losange trop grand déborde de la carte et son centre se décale (03/10 : Lancer de Pièces,
        # portée 12, centre trouvé 2 cases à côté) ; trop petit, peu de cases.
        spell = min(spells, key=lambda s: abs(s.spell_range[1] - LOCATE_RANGE))
        self._learn_blocks(frame, fighters)  # trous et blocs connus : jamais colorés, exclus du losange attendu
        shown = self._select_spell(frame, spell)
        if shown is None:
            return None
        center = range_center(shown, spell_range(spell, self._use), exclude=self.map.unwalkable)
        if self._locate_saved < 6:  # quelques captures pour vérifier le repérage
            self._locate_saved += 1
            self.game.recorder.save(annotate(shown, marks=[(shown.grid.center(center), "moi")] if center else []),
                                    "reperage_portee")
        self._cancel_spell()
        if center is None:
            log.info("Qui est moi parmi %d cercles de ma couleur ? La portée de %s ne le dit pas", len(mine), spell.name)
            return None
        circle = min(mine, key=lambda f: distance(f.cell, center))
        if distance(circle.cell, center) <= 1:
            log.info("Moi : le cercle en %s (au centre de la portée de %s)", circle.cell, spell.name)
            return circle
        log.info("Moi : en %s, au centre de la portée de %s ; mon cercle est caché", center, spell.name)
        return Fighter(circle.team, *frame.grid.center(center), center)

    def _explain_dropped(self, plan: TurnPlan, me: Fighter, enemies: list[Fighter], allies: list[Fighter]) -> None:
        """Le sort que le plan d'avant prévoyait ensuite n'y est plus : pourquoi, d'après ce qui est connu (11/10 :
        Tempête de Puissance abandonnée juste après Amplification ; le moteur, rejoué à part, la gardait)."""
        expected, self._expected_step = self._expected_step, None
        if expected is None or expected.kind != CAST or self._board is None:
            return
        first = plan.first
        if first is not None and first.spell is expected.spell and first.cell == expected.cell:
            return
        why = cast_blocker(expected.spell, me.cell, expected.cell, [e.cell for e in enemies], [a.cell for a in allies],
                           self._use, self._board)
        log.info("Plan changé : %s sur %s n'est plus prévu (%s)", expected.spell.name, expected.cell,
                 why or "rien ne l'empêche : un autre plan vaut plus")

    def _log_plan(self, plan: TurnPlan) -> None:
        """Le plan au journal quand il change (pas quand il ne fait que se poursuivre après une étape)."""
        text = plan.describe()
        if text != self._expected_plan:
            log.info("%s", text)
        else:
            log.debug("Plan inchangé")
        self._expected_plan = plan.rest().describe()
        self._expected_step = plan.rest().first

    def _stat(self, name: str) -> int | None:
        """Caractéristique lue en début de session (brain/stats.py) ; None si elle n'est pas connue."""
        stats = getattr(self.spellbook, "stats", None)
        return getattr(stats, name, None) if stats is not None else None

    @property
    def _max_pa(self) -> int:
        """PA au début d'un tour : ceux des caractéristiques (équipement compris), sinon combat.max_pa."""
        return self._stat("pa") or self.settings.max_pa

    @property
    def _max_pm(self) -> int:
        return self._stat("pm") or self.settings.max_pm

    def _turn_pa(self, frame: Frame) -> int:
        """PA de ce tour, lus dans la bulle bleue (moins que le maximum si un monstre en a retiré)."""
        pa = read_pa(frame)
        if pa is None:
            if not self._warned_pa:
                self._warned_pa = True
                path = self.game.recorder.save(frame.image, "pa_illisibles", lossless=True)
                log.warning("PA illisibles dans la bulle (capture : %s) : je compte %d PA", path, self._max_pa)
            return self._max_pa
        if pa > self._max_pa + PA_BUFF_MAX:
            log.info("%d PA lus dans la bulle, impossible (%d au plus) : je compte %d PA", pa, self._max_pa,
                     self._max_pa)
            return self._max_pa
        if pa != self._max_pa:
            log.info("%d PA ce tour (au lieu de %d)", pa, self._max_pa)
        return pa

    def _read_bar(self, frame: Frame) -> None:
        """PA restants (bulle bleue) et sorts grisés par le jeu, avant de choisir un sort.

        Les PA suivis baissent de ce que coûte chaque sort lancé ; la bulle corrige s'il en reste moins. Un sort
        grisé n'est même pas essayé : sa touche ne ferait rien."""
        pa = read_pa(frame)
        greyed = None
        if self.spellbook is not None and find_end_turn_button(frame) is not None:
            greyed = {s.spell_id for s in self.spells
                      if (entry := self.spellbook.in_slot(s.slot)) is not None and slot_greyed(frame, s.slot, entry)}
        if pa is not None and pa < self._use.pa:
            # 04/10 : 8 PA lus « 0 » (chiffre 8 inconnu) après Tir Éloigné, le Crâ a passé son tour. Un sort qui coûte
            # plus que les PA lus serait grisé par le jeu : s'il ne l'est pas, c'est la lecture qui est fausse.
            lit = [s.name for s in self.spells if greyed is not None and s.pa > pa and s.spell_id not in greyed
                   and self.spellbook.in_slot(s.slot) is not None]
            if lit:
                log.info("PA lus %d, mais %s pas grisé dans la barre : je garde %d PA", pa, ", ".join(lit[:3]),
                         self._use.pa)
            else:
                log.debug("PA : %d (et non %d)", pa, self._use.pa)
                self._use.pa = pa
        if greyed is None:
            self._use.greyed = set()  # barre cachée (fiche d'un combattant survolé) : on ne sait pas
            return
        if greyed != self._use.greyed:
            log.debug("Sorts grisés dans la barre : %s",
                      ", ".join(s.name for s in self.spells if s.spell_id in greyed) or "aucun")
        self._use.greyed = greyed

    def _plan(self, frame: Frame, me: Fighter, enemies: list[Fighter], allies: list[Fighter], pm: int,
              avoid: set[Cell] = frozenset(), missed: set[Cell] = frozenset(), can_move: bool = True,
              move_first: bool = True) -> TurnPlan:
        """Plan du moteur depuis l'état lu sur cette capture (PA suivis et lus, PM `pm`, terrain connu).

        `avoid` : cases déjà occupées ce tour (pas d'aller-retour avant de lancer) ; `missed` : destinations pas
        atteintes ce tour."""
        cells = set(frame.grid.cells())
        board = Board(frozenset(cells), frozenset(self.map.unwalkable),
                      frozenset(self.map.obstacles) if self.map.known else None,
                      reliable=self._map_reliable, margin=RELIEF_MARGIN, unseen=frozenset(self.map.unseen(cells)))
        self._board = board
        started = time.perf_counter()
        spells = [s for s in self.spells if s.spell_id not in self._banned]
        plan = plan_turn(me.cell, pm, [e.cell for e in enemies], [a.cell for a in allies], spells, self._use,
                         board, self.settings.weights, avoid=avoid, missed=missed, can_move=can_move,
                         move_first=move_first, life=self.map.life, names=self._names)
        log.debug("Plan calculé en %.0f ms (%d suites de sorts, %d PA, %d PM)", 1000 * (time.perf_counter() - started),
                  plan.sequences, self._use.pa, pm)
        return plan

    def _spell_fits(self) -> bool:
        """Un sort d'attaque tient encore dans mes PA ce tour (sinon, pas de plan d'ailleurs : rien à y lancer)."""
        use = self._use
        return any(s.damage > 0 and not s.on_self and s.pa <= use.pa and s.spell_id not in self._banned
                   and s.spell_id not in use.unavailable and s.spell_id not in use.greyed for s in self.spells)

    def _pm_left(self, frame: Frame, pm_left: int) -> int:
        """PM restants : ceux de la bulle verte, sans dépasser `pm_left` (PM du début du tour moins ceux dépensés) :
        juste après un déplacement, la bulle montre parfois encore l'ancien nombre (01/10 : un déplacement de 2 PM
        planifié avec 0 PM, 2 à 3 s perdues). `pm_left` seul si la bulle est illisible."""
        pm = read_pm(frame)
        if pm is None:
            if not self._warned_pm:
                self._warned_pm = True
                path = self.game.recorder.save(frame.image, "pm_illisibles", lossless=True)
                log.warning("PM illisibles dans la bulle (capture : %s) : j'estime %d PM d'après mes déplacements",
                            path, pm_left)
            return max(pm_left, 0)
        return min(pm, max(pm_left, 0))

    def _use_spellbook(self, frame: Frame) -> None:
        """Premier tour de la session : cases de la barre -> sorts, et ce que la tactique en sait."""
        if self.spellbook is None or self.spellbook.slots or not self.spellbook.entries:
            return
        self.spellbook.locate(frame)
        self._spells = self.spellbook.combat_spells()
        if self.game_background:
            row2 = self._second_row_attacks()
            if row2:
                log.info("Arrière-plan : %s en 2e rangée de la barre, sélectionné(s) au pavé numérique (1 à 7)",
                         ", ".join(f"{s.name} (case {s.slot})" for s in row2))
        for spell in self._spells:
            if spell.damage_boost:
                effect = " et ".join(([f"+{spell.boost_flat:g} par coup"] if spell.boost_flat else []) +
                                     ([f"+{spell.boost_pct:g} %"] if spell.boost_pct else []))
                what = f"bonus de dégâts {effect}, {spell.boost_turns} tour(s), lancé sur moi avant de taper"
            elif spell.range_bonus:
                what = f"bonus de portée +{spell.range_bonus}, lancé sur moi avant de taper"
            else:
                what = f"{spell.damage:.0f} dégâts moyens"
            log.info("Sort pour la tactique : %s (case %d, %d PA, portée %d à %d%s, %s)", spell.name, spell.slot,
                     spell.pa, *spell.spell_range, ", en ligne" if spell.in_line else "", what)
        if not self._spells:
            # « Solomonk » : la référence des sorts (config/sorts/solomonk.json), pas un sort lu.
            log.warning("Aucun sort de la barre n'est connu (sorts lus dans la fenêtre des sorts et retrouvés dans la "
                        "référence Solomonk) : j'utilise la case %d", self.settings.spell_slot)

    def _learn_blocks(self, frame: Frame, fighters: list[Fighter]) -> None:
        """Blocs et trous lus sur la carte propre (mode tactique, sans portée affichée) : MapMemory.learn_terrain."""
        if not tactical_mode(frame):
            if not self._warned_not_tactical:
                self._warned_not_tactical = True
                log.warning("Carte hors mode tactique : obstacles inconnus, déplacements estimés sans eux")
        elif not spell_range_visible(frame):
            self.map.learn_terrain(read_terrain(frame, fighters), fighters)

    @property
    def _watching(self) -> bool:
        """Mode d'attente « condition » (timing.wait_mode) : guetter le résultat d'un geste plutôt qu'attendre."""
        return self.settings.wait_mode == "condition"

    def _watch(self, what: str, predicate: Callable[[Frame], bool], timeout: float,
               park: bool | None = None) -> tuple[Frame | None, Frame]:
        """Mode « condition » : guette `predicate` (Game.wait_until). Renvoie la capture qui le montre (None : pas vu
        avant `timeout` s) et la dernière capture. Le délai au journal (debug), pour régler les délais au plus."""
        seen: list[Frame] = []
        started = time.monotonic()
        shown = self.game.wait_until(lambda frame: seen.append(frame) or predicate(frame), timeout, park=park)
        log.debug("Guet « %s » : %s en %d ms (%d captures)", what, "vu" if shown is not None else "pas vu",
                  1000 * (time.monotonic() - started), len(seen))
        return shown, seen[-1]

    def _turn_stats(self) -> TurnStats:
        """Mesure du tour en cours, commencée à son premier geste (voir TurnStats)."""
        if self._stats is None or self._stats.turn != self._use.turn:
            self._stats = TurnStats(self._use.turn, time.monotonic())
        return self._stats

    def _range_frame(self, watch: bool = False) -> Frame | None:
        """Capture montrant la portée du sort sélectionné, None si aucune portée n'est affichée.

        Une capture 0,15 s après la touche ; `watch` (mode « condition », sélection d'un sort) : captures dès la
        touche, jusqu'à voir la portée (RANGE_WAIT s au plus). Après Échap (_cancel_spell), toujours la capture à
        0,15 s : guettée, la portée encore là juste après la touche passerait pour un sort resté sélectionné."""
        if watch:
            return self._watch("portée du sort", spell_range_visible, RANGE_WAIT, park=False)[0]
        self.game.sleep(RANGE_SHOWN)
        frame = self.game.look(park=False)  # la portée reste visible même souris sur la carte
        return frame if spell_range_visible(frame) else None

    @property
    def game_background(self) -> bool:
        """Le jeu se joue en arrière-plan (gestes par messages, pas de survol : brain/game.py)."""
        return bool(getattr(self.game, "background", False))

    def _mouse_away(self) -> None:
        """Arrière-plan : souris « virtuelle » écartée juste après le clic qui lance un sort (Game.mouse_away)."""
        away = getattr(self.game, "mouse_away", None)
        if away is not None and self.game_background:
            self.game.sleep(0.05)
            away()

    def _slot_key(self, slot: int) -> str:
        """Raccourci clavier d'une case de la barre ("" : clic sur la case). En arrière-plan, pas de combinaison
        (Ctrl+3, 2e rangée) : l'état de Ctrl est celui du vrai clavier de l'utilisateur, qu'un message ne change pas ;
        2e rangée : son 2e raccourci, au pavé numérique (ROW2_BACKGROUND_KEY)."""
        key = self._keys.setdefault(slot, default_slot_key(slot))
        if key and "+" in key and self.game_background:
            ctrl, _, digit = key.partition("+")
            if ctrl == "ctrl" and digit.isdigit() and self._numpad_misses < NUMPAD_MISSES:
                return ROW2_BACKGROUND_KEY.format(digit)
            return ""
        return key

    def _check_numpad(self, frame: Frame) -> None:
        """Arrière-plan, premier tour où c'est possible : une attaque de la 2e rangée sélectionnée au pavé numérique,
        sa portée doit s'afficher ; puis annulée. Ces sorts, souvent moins forts, servent rarement : sans cet essai, on
        ne saurait qu'au moment où il en faudrait un si sa touche marche (11/10 : Expiation et Punitive de Gela,
        derrière Flèche Magique et Flèche Explosive)."""
        if not self.game_background or self._numpad_checked or self._numpad_misses >= NUMPAD_MISSES:
            return
        self._read_bar(frame)
        spell = next((s for s in self._second_row_attacks()
                      if s.pa <= self._use.pa and s.spell_id not in self._use.greyed), None)
        if spell is None:
            return  # pas d'attaque de la 2e rangée lançable ce tour : au prochain
        self._numpad_checked = True
        key = self._slot_key(spell.slot)
        if self._select_spell(frame, spell) is not None:
            log.info("Pavé numérique : %s (case %d) sélectionné par %s, la 2e rangée marche en arrière-plan",
                     spell.name, spell.slot, key)
            self._numpad_ok = True
            self._cancel_spell()
            return
        path = self.game.recorder.save(self.game.look().image, "pave_numerique_sans_effet")
        log.info("Pavé numérique : %s (case %d) pas sélectionné par %s (capture : %s)%s", spell.name, spell.slot, key,
                 path, "" if self._numpad_misses + 1 >= NUMPAD_MISSES else " ; nouvel essai au prochain tour")
        # 11/10 : Brawli, essai 0,3 s après le début de son 1er tour ; la touche 1 juste après n'a rien sélectionné non
        # plus (début de tour pas encore prêt ?). Un échec ne suffit pas : nouvel essai au tour suivant.
        self._numpad_checked = self._numpad_misses + 1 >= NUMPAD_MISSES
        self._numpad_missed(spell)

    def _second_row_attacks(self) -> list[CombatSpell]:
        return [s for s in self._spells if not s.on_self and "+" in default_slot_key(s.slot)]

    def _numpad_missed(self, spell: CombatSpell) -> None:
        """Arrière-plan : `spell` (2e rangée) pas sélectionné par sa touche du pavé numérique, alors qu'il a ses PA et
        n'est pas grisé. Au bout de NUMPAD_MISSES fois, les attaques de la 2e rangée sont écartées pour la session."""
        if self._numpad_ok or spell.pa > self._use.pa or spell.spell_id in self._use.greyed:
            return  # 11/10 : Expiation « non sélectionnable », le dernier ennemi venait de mourir (combat fini)
        self._numpad_misses += 1
        if self._numpad_misses < NUMPAD_MISSES:
            return
        dropped = self._second_row_attacks()
        log.warning("Arrière-plan : le pavé numérique ne sélectionne pas %s (2e rangée) : je ne m'en sers plus. Dans "
                    "Raccourcis clavier, mets « 1 » à « 7 » (pavé numérique) en 2e touche des cases A à G, ou les "
                    "attaques en 1re rangée", ", ".join(f"{s.name} (case {s.slot})" for s in dropped))
        self._spells = [s for s in self._spells if s not in dropped]

    def _select_spell(self, frame: Frame, spell: CombatSpell) -> Frame | None:
        """Sélectionne le sort avec son raccourci clavier ; renvoie la capture qui montre sa portée (None : refusé).

        Pas de clic sur la case du sort : un sort que sa touche ne sélectionne pas est refusé par le jeu (PA,
        relance...). Clic seulement pour une case sans raccourci (bouton à gauche de la barre, combat.spell.key "")."""
        key = self._slot_key(spell.slot)
        if key:
            self.game.press(key)
        else:
            self.game.click(spell_slot_point(frame.layout, spell.slot), frame.layout.spread(*SPELL_SLOT_SPREAD))
        return self._range_frame(watch=self._watching)

    def _learn_range(self, shown: Frame, me: Fighter, fighters: list[Fighter], spell: CombatSpell) -> None:
        """Portée du sort et obstacles, lus sur les cases colorées (mode tactique seulement)."""
        if not tactical_mode(shown):
            return
        view = read_range(shown, me, fighters)
        if view.spell_range is None:
            return
        if not view.castable:
            # Portée encore en fondu : toutes ses cases bleu clair, comme sans ligne de vue. 11/10, Flèche Explosive
            # juste après Maîtrise de l'Arc : 120 cases « sans ligne de vue », aucune à portée, carte crue à relief.
            log.debug("Portée de %s pas encore affichée en entier (aucune case bleu foncé) : pas lue", spell.name)
            return
        if spell.in_line:
            view = in_line_only(view, me.cell)
        low, high = spell.spell_range
        high += (self._use.range_bonus + ALLY_RANGE_BONUS) if spell.range_modifiable else 0
        if spell.spell_id != "sort_configure" and (view.spell_range[1] > high or view.spell_range[0] < low):
            # Portée lue impossible : cases colorées décalées (carte à relief) ou lue depuis une autre case que la mienne
            # (03/10 : 1 à 12 pour une Flèche Magique de 1 à 8, le bot est allé chercher l'ennemi à 12 cases).
            log.debug("Portée de %s lue %d à %d, impossible (%d à %d) : ignorée", spell.name, *view.spell_range, low, high)
            # Losange pas centré sur ma case : ses « obstacles » (cases non colorées à la bonne distance de MA case) et
            # l'accord de la ligne de vue ne veulent rien dire. 17/10 : 6 lectures de Pelle Fantomatique, obstacles
            # appris à tort et 3 cartes crues à relief (22 à 88 %). Seules ses cases colorées servent : pas des blocs.
            self.map.learn_range(RangeView(view.castable, view.no_sight, set(), view.spell_range, walls=view.walls))
            return
        if view.spell_range != self._use.measured.get(spell.spell_id):
            log.info("Portée de %s : %d à %d cases", spell.name, *view.spell_range)
        self._use.measured[spell.spell_id] = view.spell_range
        self.map.learn_range(view)
        blockers = self.map.obstacles | ({f.cell for f in fighters} - {me.cell})
        agreement = sight_agreement(view, me.cell, blockers)
        log.debug("Obstacles connus : %d ; ligne de vue prédite juste sur %.0f %% des cases à portée",
                  len(self.map.obstacles), 100 * agreement)
        # 15/10 : sorts en ligne, 7 à 10 cases colorées : UNE case fausse (sous l'animation du sort, ombre d'un mur)
        # donnait 86-90 % et tout le combat passait en « carte à relief ». Il en faut au moins deux.
        shown_cells = len(view.castable | view.no_sight)
        wrong = round((1 - agreement) * shown_cells) if shown_cells else 0
        if agreement < 0.9 and wrong >= RELIEF_MIN_WRONG and self._map_reliable:
            self._map_reliable = False
            log.info("Carte à relief (ligne de vue prédite juste sur %.0f %% des cases) : je ne me fie plus à mes "
                     "prévisions de portée et de ligne de vue, je vérifie avec le sort", 100 * agreement)
        if agreement < 0.95 and not self._sight_saved:
            self._sight_saved = True  # une capture par combat suffit pour améliorer la prédiction
            path = self.game.recorder.save(shown.image, "ligne_de_vue_a_revoir", lossless=True)
            log.info("Ligne de vue prédite juste sur %.0f %% des cases seulement (capture : %s)", 100 * agreement, path)

    def _was_cast(self, before: Frame, after: Frame, spell: CombatSpell) -> bool:
        """PA dépensés (chiffres de la bulle changés ; bulle entière si les chiffres ne se voient pas), ou icône du sort
        changée (grisée, relance) sans que le sort reste sélectionné."""
        spent = pa_changed(before, after)
        if spent or spent is None and region_changed(before, after, PA_BUBBLE):
            return True
        return not spell_range_visible(after) and region_changed(before, after, spell_slot_box(spell.slot))

    def _cancel_spell(self) -> Cast:
        self.game.press(self.settings.cancel_key)
        if self._range_frame() is not None:
            # Sort toujours sélectionné : un clic de déplacement le lancerait sur une case vide.
            log.warning("%s n'a pas annulé le sort : je finis mon tour (règle combat.cancel_key)",
                        self.settings.cancel_key.upper())
            return Cast.STUCK
        return Cast.OUT_OF_REACH

    @property
    def _name(self) -> str:
        """Nom du personnage (titre de sa fenêtre), tel que le chat de combat l'écrit."""
        title = getattr(getattr(self.game, "window", None), "title", "") or ""
        return title.split(" - ")[0].strip()

    def _chat_before(self, before: Frame) -> list[str] | None:
        """Lignes du chat de combat avant le clic ; None (le chat ne sert pas) sans OCR, sans nom de personnage, ou si
        aucune annonce « ... lance ... » ne s'y lit (canal de combat désactivé dans cette fenêtre)."""
        if not self._name or self._watching:
            return None
        lines = combat_lines(before)
        if not lines or not any(" lance " in line for line in lines):
            return None
        return lines

    def _confirm_cast(self, before: Frame, my_team: str, spell: CombatSpell) -> Frame | None:
        """Juste après le clic qui lance le sort : None s'il est parti (ou si le tour / le combat est fini), la capture
        qui l'a montré gardée dans `_sent` (point de départ d'une rafale) ; sinon la dernière capture.

        Fixe : after_cast s (début de l'animation), puis une capture toutes les 0,15 s pendant cast_confirm s.
        Condition : captures dès le clic, jusqu'à voir les PA ou l'icône changer ; after_cast + cast_confirm s au
        plus, la même fenêtre d'observation (un sort lent à se confirmer n'est pas relancé pour autant)."""

        def gone(frame: Frame) -> bool:
            return not self._my_turn(frame, my_team) or self._was_cast(before, frame, spell)

        clicked = time.monotonic()
        window = self.settings.after_cast + self.settings.cast_confirm
        if self._watching:
            shown, after = self._watch("sort parti", gone, window)
            sent = shown is not None
            after = shown or after
        else:
            chat = self._chat_before(before)
            announced_at, read_at = None, float("-inf")
            self.game.sleep(self.settings.after_cast)
            checks = 1 + round(max(0.0, window - self.settings.after_cast) / CONFIRM_EVERY)
            for check in range(checks):
                after = self.game.look()  # écarte la souris si elle cache la barre de sorts
                if sent := gone(after):
                    break
                elapsed = time.monotonic() - clicked
                if chat is not None and announced_at is None and time.monotonic() - read_at >= CHAT_EVERY:
                    read_at = time.monotonic()
                    if cast_announced(chat, combat_lines(after) or [], self._name, spell.name):
                        announced_at = elapsed
                        log.debug("%s annoncé dans le chat de combat en %.1f s", spell.name, elapsed)
                    elif elapsed >= CHAT_REFUSED_AFTER:
                        log.debug("%s : ni annoncé dans le chat ni PA dépensés en %.1f s : refusé", spell.name, elapsed)
                        break
                if announced_at is not None and elapsed >= announced_at + CHAT_PA_GRACE:
                    sent = True  # parti (chat) ; PA pas encore à jour : la capture d'après les montrera
                    break
                if check < checks - 1:
                    self.game.sleep(CONFIRM_EVERY)
        stats = self._turn_stats()
        # 11/10 : en arrière-plan, un sort réussi semblait mettre ~4 s à se confirmer, un refus en coûte 5 : mesuré ici
        # pour régler l'attente (P13 du rapport des tests de combat).
        log.debug("%s : %s en %.1f s", spell.name, "parti" if sent else "pas parti", time.monotonic() - clicked)
        if sent:
            stats.casts += 1
            self._sent = after
            return None
        stats.missed_clicks += 1
        return after

    def _forbidden_target(self, aim: tuple[float, float]) -> bool:
        """Curseur « interdit » sur la cible que la souris vient de survoler. Fixe : une capture après 0,25 s ;
        condition : captures dès la souris posée, jusqu'à 0,25 s. Une cible valide ne montre rien : elle n'est
        acceptée qu'à l'échéance, comme en mode fixe ; une cible refusée l'est dès que le curseur apparaît.
        La dernière capture (souris sur la cible : sa fiche, et sa vie) est gardée dans `_hovered`.

        Arrière-plan (08/10) : pas de survol, ni curseur ni fiche à voir : la cible est acceptée, un refus du jeu se
        verra aux PA inchangés (sort pas parti, _confirm_cast)."""
        if self.game_background:
            self._hovered = None
            return False
        if self._watching:
            shown, self._hovered = self._watch("curseur interdit", lambda frame: forbidden_cursor_near(frame, aim),
                                               CURSOR_WAIT, park=False)
            return shown is not None
        self.game.sleep(CURSOR_WAIT)
        self._hovered = self.game.look(park=False)
        return forbidden_cursor_near(self._hovered, aim)

    def _cast(self, before: Frame, me: Fighter, target: Fighter, fighters: list[Fighter], my_team: str,
              spell: CombatSpell | None = None, confirm: bool = True) -> Cast:
        """`before` : capture avant de sélectionner le sort (référence des PA). `target` : moi pour un sort sur soi,
        un combattant d'équipe "" pour une zone qui vise une case vide."""
        spell = spell or self.spells[0]
        self._turn_stats().tries += 1
        if spell.on_self:
            return self._cast_on_self(before, me, spell, my_team)
        shown = self._select_spell(before, spell)
        if shown is None and time.monotonic() - self._cast_at < RESELECT_AFTER_CAST and spell.pa <= self._use.pa:
            log.info("%s pas sélectionné juste après un lancer (animation) : nouvel essai", spell.name)
            self.game.sleep(RESELECT_PAUSE)
            shown = self._range_frame(watch=self._watching) or self._select_spell(self.game.look(), spell)
        if shown is None:
            key = self._slot_key(spell.slot)
            log.info("%s non sélectionnable (touche %s) : plus assez de PA, sort en relance ou raccourci différent",
                     spell.name, key or "aucune")
            self.game.recorder.save(self.game.look().image, "sort_non_selectionnable")
            if key.startswith("num"):
                self._numpad_missed(spell)
            return Cast.NO_SPELL
        self._learn_range(shown, me, fighters, spell)
        high = spell_range(spell, self._use)[1]  # bonus de portée (Tir Éloigné) compris, dès le tour où il est lancé
        if self._map_reliable and distance(me.cell, target.cell) > high:
            log.info("%s à %d cases, hors de portée (%d max)", _aim_label(target).capitalize(),
                     distance(me.cell, target.cell), high)
            return self._cancel_spell()
        # Pas sur le corps d'un autre combattant debout devant la case : le clic le viserait lui (une zone visant une
        # case vide près d'un allié le toucherait).
        point, spread = clear_point(before.grid, target.cell, fighters if me in fighters else fighters + [me],
                                    timeline_portraits(before))  # ni sur un portrait (case presque cachée, 15/10)
        self._aim = (target.cell, point, spread)  # la rafale vise au même endroit
        for attempt in range(1, CAST_ATTEMPTS + 1):
            aim = self.game.move(point, spread)
            if self._forbidden_target(aim):
                log.info("Pas de ligne de vue sur %s", _aim_label(target))
                return self._cancel_spell()
            hp = self._read_life(self._hovered, target) if target.team and self._hovered is not None else None
            log.info("%s lancé sur %s%s", spell.name, _aim_label(target), f" ({hp} PV)" if hp is not None else "")
            self.game.click()  # sur place : la souris est déjà sur la cible vérifiée
            self._mouse_away()
            self._cast_at = time.monotonic()
            if not confirm:  # dernier sort du tour (fast_end) : la fin de tour suit, sans attendre l'animation
                self._turn_stats().casts += 1
                self._note_cast(spell, target.cell)
                return Cast.DONE
            after = self._confirm_cast(before, my_team, spell)
            if after is not None and target.team and self._left_its_cell(before, after, target):
                # L'ennemi visé n'est plus sur sa case : le sort est parti (mort, repoussé), même si les PA ne l'ont pas
                # montré. Jamais de nouvel essai sur une case vide (04/10 : Tempêtes de Puissance crues pas parties,
                # relancées deux fois sur la case de l'ennemi déjà mort).
                log.info("%s n'est plus sur sa case : %s est parti", _aim_label(target).capitalize(), spell.name)
                self._turn_stats().casts += 1
                after = None
            if after is None:
                self._note_cast(spell, target.cell)
                self._not_gone[spell.spell_id] = 0
                return Cast.DONE
            path = self.game.recorder.save(after.image, "sort_pas_parti", lossless=True)
            if self.game_background:
                # 11/10 : en arrière-plan, pas de curseur « interdit » à voir avant de cliquer ; un sort refusé par le
                # jeu (hors de portée, sans ligne de vue) ne se voit qu'aux PA inchangés. Pas de nouvel essai (5 s
                # chacun, Jesuisleking : 33 s sur 2 tours) ni de mise à l'écart pour la session : refusé d'ici,
                # comme avec le curseur interdit, et un autre plan.
                log.info("%s refusé sur %s (PA inchangés, capture : %s) : sans doute hors de portée ou sans ligne de "
                         "vue d'ici", spell.name, _aim_label(target), path)
                if spell_range_visible(after):
                    return self._cancel_spell()
                return Cast.OUT_OF_REACH
            log.warning("Le sort ne semble pas parti (PA inchangés, capture : %s) : nouvel essai %d/%d",
                        path, attempt, CAST_ATTEMPTS)
            if not spell_range_visible(after) and self._select_spell(after, spell) is None:
                break
        self._not_gone[spell.spell_id] += 1
        if self._not_gone[spell.spell_id] >= NOT_GONE_LIMIT and spell.spell_id not in self._banned:
            self._banned.add(spell.spell_id)
            log.warning("%s n'est jamais parti sur un ennemi (%d fois de suite) : je ne l'utilise plus de la session "
                        "(sort réservé aux invocations ? rôle « autre » dans config/sorts.yaml)", spell.name,
                        NOT_GONE_LIMIT)
        return Cast.NO_SPELL

    @staticmethod
    def _left_its_cell(before: Frame, after: Frame, target: Fighter) -> bool:
        """Le combattant visé avait son cercle sur sa case avant le lancer, et ne l'a plus."""
        def standing(frame: Frame) -> bool:
            return any(f.cell == target.cell and f.team == target.team for f in find_fighters(frame))

        return standing(before) and not standing(after)

    def _note_cast(self, spell: CombatSpell, cell: Cell) -> None:
        """Un sort vient de partir sur `cell` : noté pour mesurer ses dégâts à la prochaine lecture de vie."""
        if self._life is not None:
            self._life.casts.append((spell.spell_id, cell))

    def _wound(self, spell: CombatSpell, step: Step) -> None:
        """Sort parti : vie connue des ennemis touchés diminuée des dégâts attendus (mesurés : HitLog ; zone
        dégressive ; bonus de dégâts en cours), pour le plan suivant, le mien ou celui d'un allié (MapMemory.life)."""
        expected = self._hits.expected(spell, self._use.gain(spell))
        self.map.wound({cell: expected * max(0.0, 1 - self.settings.zone_falloff * distance(step.cell, cell))
                        for cell in step.hits})

    def _read_life(self, frame: Frame, target: Fighter) -> int | None:
        """Vie de la cible survolée, lue sur sa fiche (perception/hp.py) ; None sans fiche ou illisible. Gardée pour
        toute l'équipe (MapMemory.life) : le moteur achève les ennemis entamés et concentre les coups.

        Si un seul sort est parti depuis la lecture précédente de sa vie, dans ce tour et sur sa case, ses dégâts sont
        mesurés (HitLog). Gardées pour vérifier : une capture de la fiche lue par combat, une de la fiche illisible
        (sans perte) par session."""
        hp = read_hovered_hp(frame)
        label = "vie_illisible" if hp is None else "vie_lue"
        if label not in self._life_saved:
            self._life_saved.add(label)
            path = self.game.recorder.save(frame.image, label, lossless=hp is None)
            log.debug("Vie de %s au survol : %s (capture : %s)", _aim_label(target),
                      "illisible" if hp is None else f"{hp} PV", path)
        if hp is None:
            return None
        last = self._life
        if last is not None and last.turn == self._use.turn and last.cell == target.cell and last.casts \
                and hp >= last.hp:
            # Vie pas encore baissée : le lancer est encore en vol (dégâts affichés à la fin de son animation). Ni
            # mesure (ce n'est pas un coup à 0), ni nouvelle vie retenue : la prochaine lecture les comptera.
            return hp
        if last is not None and last.turn == self._use.turn and last.cell == target.cell \
                and [cell for _, cell in last.casts] == [target.cell]:
            spell = next((s for s in self.spells if s.spell_id == last.casts[0][0]), None)
            damage = last.hp - hp
            gain = self._use.gain(spell) if spell is not None else 0.0  # bonus de dégâts en cours : pas retenus
            if spell is not None and 0 <= damage <= 3 * self._hits.expected(spell, gain) + 10:  # sinon : autre fiche
                self._hits.learn(spell, max(0, round(damage - gain)))
                log.debug("%s : %d dégâts (%d -> %d PV)%s, %.1f en moyenne", spell.name, damage, last.hp, hp,
                          f" dont {gain:.0f} de bonus" if gain else "", self._hits.expected(spell))
        self._life = TargetLife(self._use.turn, target.cell, hp)
        if hp > 0:
            self.map.life[target.cell] = hp
        return hp

    def _burst(self, target: Fighter, step: Step, repeats: int, my_team: str, room: int,
               final: bool = False) -> tuple[int, bool]:
        """Rafale (combat.burst), pour tout sort : le plan relance le même sort sur la même case juste après celui qui
        vient de partir (Lancer de Pièces x3, Flèche Magique x2 avec 8 PA...). Ces lancers s'enchaînent sans capture
        complète ni nouveau plan entre eux : touche du sort, souris sur la cible, une capture (sort sélectionné, pas
        de curseur interdit, encore mon tour, vie de la cible), clic, PA changés. Renvoie le nombre de lancers partis,
        déjà comptés dans SpellUse, et vrai si le sort est resté sélectionné malgré l'annulation (Cast.STUCK : plus
        rien à faire ce tour) ; sinon le tour reprend son cours (capture, nouveau plan : autre cible...).

        Au plus `repeats` lancers (ceux du plan) et `room` (garde-fou du tour), dans les PA lus, les lancers par tour
        et par cible et la relance. Cible seule touchée et vie lue au survol : seulement les lancers qui devraient la
        tuer (dégâts mesurés, HitLog). Arrêt dès que plus aucun ennemi visé n'a de cercle sur sa case (mort), que la
        fiche de la cible ne s'affiche plus alors qu'elle se lisait, ou au moindre doute. Pas de rafale pour un sort qui
        repousse (la cible change de case : le lancer suivant n'est plus celui prévu) ni sans raccourci."""
        spell = step.spell
        key = self._slot_key(spell.slot) if spell.slot in self._keys else None
        if repeats <= 0 or room <= 0 or spell.on_self or spell.push or not key:
            return 0, False
        if self.game_background and not (self._life is not None and self._life.cell == target.cell
                                         and self._life.turn == self._use.turn):
            # Arrière-plan : pas de fiche au survol, donc pas de vie lue ; une rafale à l'aveugle partirait sur la case
            # d'un ennemi déjà mort (04/10). Un sort à la fois, avec une nouvelle capture entre deux.
            return 0, False
        wanted = min(repeats, room)
        single = bool(target.team) and step.hits == (target.cell,)  # sa vie dit combien de lancers il faut
        life = self._life if single and self._life is not None and self._life.cell == target.cell \
            and self._life.turn == self._use.turn else None
        gain = self._use.gain(spell)
        if life is not None:
            needed = self._hits.casts_to_kill(spell, life.hp, gain)
            wanted = min(wanted, needed - 1)  # le premier vient de partir
            log.info("Rafale : %s avait %d PV, %s fait environ %.0f dégâts : %s pour le tuer, %d de plus",
                     _aim_label(target), life.hp, spell.name, self._hits.expected(spell, gain),
                     _plural(needed, "lancer"), max(wanted, 0))
        stats = self._turn_stats()
        sheet_seen = life is not None
        last_hp = life.hp if life is not None else None  # vie lue juste avant le clic précédent
        done, stuck = 0, False
        while done < wanted:
            before = self._sent  # capture qui a montré le lancer précédent parti
            reason = self._burst_stop(before, spell, step, my_team)
            if reason:
                log.info("Rafale arrêtée : %s", reason)
                break
            if not self.settings.fast_end:
                self.game.press(key)
            if self._aim is not None and self._aim[0] == target.cell:
                point, spread = self._aim[1:]
            else:
                point, spread = (target.x, target.y), before.grid.click_spread()
            aim = self.game.move(point, spread)
            hovered = self._burst_ready(key) if self.settings.fast_end else self._burst_aim()
            if hovered is None:
                log.info("Rafale arrêtée : %s pas resélectionné par la touche %s", spell.name, key)
                break
            if not self._my_turn(hovered, my_team):
                log.info("Rafale arrêtée : plus mon tour")
                break
            if forbidden_cursor_near(hovered, aim):
                log.info("Rafale arrêtée : curseur interdit sur %s", _aim_label(target))
                stuck = self._cancel_spell() is Cast.STUCK
                break
            hp = None
            if single:
                hp = self._read_life(hovered, target)
                if hp is None and sheet_seen:
                    self.game.sleep(SHEET_RECHECK)  # la fiche suit la souris d'un instant
                    hp = self._read_life(self.game.look(park=False), target)
                if (hp is None and sheet_seen) or hp == 0:
                    log.info("Rafale arrêtée : plus de vie lue au survol de %s (mort ?)", _aim_label(target))
                    stuck = self._cancel_spell() is Cast.STUCK
                    break
                if hp is not None:
                    sheet_seen = True
                    left = hp
                    if last_hp is not None and hp >= last_hp:
                        # Vie pas encore baissée : les dégâts du lancer précédent s'affichent à la fin de son animation
                        # (04/10 : sinon un lancer de trop, parti sur la case vide de l'ennemi mort).
                        left = hp - self._hits.expected(spell, gain)
                    last_hp = hp
                    wanted = min(repeats, room, done + self._hits.casts_to_kill(spell, max(0, round(left)), gain))
                    if wanted <= done:
                        log.info("Rafale arrêtée : %s (%d PV) devrait mourir du lancer précédent", _aim_label(target), hp)
                        stuck = self._cancel_spell() is Cast.STUCK
                        break
            stats.tries += 1
            log.info("%s lancé sur %s (rafale, %d/%d%s)", spell.name, _aim_label(target), done + 2, wanted + 1,
                     "" if hp is None else f", {hp} PV")
            self.game.click()  # sur place : la souris est sur la cible vérifiée
            self._mouse_away()
            self._cast_at = time.monotonic()
            if self.settings.fast_end:
                # Sans attendre que les PA baissent : la touche suivante est pressée tout de suite (_burst_ready), et
                # un lancer refusé se verrait au suivant (sort pas resélectionné). Dernier de la rafale et du tour :
                # la fin de tour suit.
                after = None
                self._turn_stats().casts += 1
                self._sent = hovered
                if not (final and done + 1 >= wanted):
                    self.game.sleep(BURST_GAP)
            else:
                after = self._confirm_cast(before, my_team, spell)
            if after is not None:
                path = self.game.recorder.save(after.image, "sort_pas_parti", lossless=True)
                log.warning("Rafale arrêtée : %s ne semble pas parti (PA inchangés, capture : %s)", spell.name, path)
                stuck = spell_range_visible(after) and self._cancel_spell() is Cast.STUCK
                break
            self._use.record(spell, step.cell)
            self._note_cast(spell, step.cell)
            self._wound(spell, step)
            stats.burst += 1
            done += 1
        return done, stuck

    def _burst_stop(self, frame: Frame, spell: CombatSpell, step: Step, my_team: str) -> str | None:
        """Raison de ne pas relancer en rafale, lue sur la capture qui a montré le lancer précédent parti ; None : on
        relance. PA de la bulle (un monstre a pu en retirer), lancers par tour et par cible, relance, ennemi visé encore
        debout (son cercle sur sa case)."""
        if not self._my_turn(frame, my_team):
            return "plus mon tour"
        pa = read_pa(frame)
        if pa is not None and pa < self._use.pa:
            self._use.pa = pa
        if not self._use.available(spell):
            return f"{spell.name} plus lançable ({self._use.pa} PA, {self._use.casts_this_turn[spell.spell_id]} ce tour)"
        if spell.per_target and self._use.casts_on_target[(spell.spell_id, step.cell)] >= spell.per_target:
            return f"{spell.per_target} lancers par cible atteints"
        standing = {f.cell for f in find_fighters(frame) if f.team != my_team}
        if not standing & set(step.hits):
            # Le cercle disparaît un instant sous l'animation du coup et ses chiffres (03/10 : « ennemi mort » à 76 PV) :
            # un second regard avant de conclure.
            self.game.sleep(BURST_RECHECK)
            standing = {f.cell for f in find_fighters(self.game.look(park=False)) if f.team != my_team}
        if not standing & set(step.hits):
            return f"plus de cercle en {step.cell} (ennemi mort)" if step.hits == (step.cell,) else \
                "plus d'ennemi debout dans la zone"
        return None

    def _burst_ready(self, key: str) -> Frame | None:
        """Rafale rapide (fast_end) : touche du sort pressée tout de suite après le lancer précédent ; si le jeu ne la
        prend pas encore (animation en cours), nouvelle pression toutes les BURST_REPRESS s. Capture qui montre la
        portée (sort sélectionné), None au bout de BURST_READY s. Le délai est noté au journal (mise au point)."""
        start = self.game.now()
        pressed = start
        self.game.press(key)
        while True:
            self.game.sleep(BURST_HOVER if pressed == start else 0.1)
            frame = self.game.look(park=False)
            if spell_range_visible(frame):
                log.debug("Rafale : sort resélectionné %.0f ms après la touche (%d pression(s))",
                          1000 * (self.game.now() - start), 1 + round((pressed - start) / BURST_REPRESS))
                return frame
            now = self.game.now()
            if now - start >= BURST_READY:
                return None
            if now - pressed >= BURST_REPRESS:
                self.game.press(key)
                pressed = now

    def _burst_aim(self) -> Frame | None:
        """Rafale, touche du sort pressée puis souris posée sur la cible : capture qui montre la portée (sort
        sélectionné), None sinon. Fixe : BURST_HOVER s après la souris posée, une seconde capture RANGE_SHOWN s plus
        tard si la portée manque ; condition : guettée dès la souris posée, RANGE_WAIT s au plus."""
        if self._watching:
            return self._watch("sort resélectionné", spell_range_visible, RANGE_WAIT, park=False)[0]
        for wait in (BURST_HOVER, RANGE_SHOWN):
            self.game.sleep(wait)
            frame = self.game.look(park=False)
            if spell_range_visible(frame):
                return frame
        return None

    def _cast_on_self(self, before: Frame, me: Fighter, spell: CombatSpell, my_team: str) -> Cast:
        """Sort de bonus, lancé sur moi : clic droit sur sa case de la barre, ou son raccourci puis un clic sur mon
        personnage. Le 01/10, le jeu a ignoré le clic droit à partir du 6e combat (sort survolé, rien de lancé) :
        la façon qui a marché en dernier passe en premier, pour toute la session."""
        for method in sorted(SELF_CAST_METHODS, key=lambda m: m != self._self_cast):
            if method == "clic droit":
                log.info("%s lancé sur moi (clic droit sur sa case)", spell.name)
                self.game.click(spell_slot_point(before.layout, spell.slot), before.layout.spread(*SPELL_SLOT_SPREAD),
                                button="right")
            else:
                key = self._slot_key(spell.slot)
                if not key:
                    continue
                # Portée 0 : seule ma case s'éclaire, trop peu pour spell_range_visible ; le clic sur moi tranche.
                self.game.press(key)
                self.game.sleep(0.15)
                if not self._self_cast_saved:
                    self._self_cast_saved = True  # une capture par session : ce que le jeu affiche
                    self.game.recorder.save(self.game.look(park=False).image, "sort_sur_moi_selectionne", lossless=True)
                log.info("%s lancé sur moi (touche %s puis clic sur mon personnage)", spell.name, key)
                self.game.click((me.x, me.y), before.grid.click_spread())
            after = self._confirm_cast(before, my_team, spell)  # after_cast compris (mode fixe)
            if after is None:
                if method != self._self_cast:
                    log.info("Sorts sur moi : %s, gardé pour la session", method)
                self._self_cast = method
                return Cast.DONE
            path = self.game.recorder.save(after.image, "sort_pas_parti", lossless=True)
            log.warning("%s ne semble pas parti par %s (PA inchangés, capture : %s)", spell.name, method, path)
            if spell_range_visible(after) and self._cancel_spell() is Cast.STUCK:
                return Cast.STUCK
        return Cast.NO_SPELL

    def _walk(self, frame: Frame, me: Fighter, best: Cell, fighters: list[Fighter], zone: set[Cell], label: str,
              steps: int | None = None, retry: bool = True) -> int:
        """Clic sur `best` et vérification du déplacement ; renvoie les PM utilisés (0 : pas bougé ; moins que `steps` :
        arrêté en route, le jeu n'a pas trouvé de chemin jusqu'à `best`).

        `steps` : longueur du chemin prévu (plus que la distance quand il contourne un obstacle)."""
        steps = steps or distance(best, me.cell)
        stats = self._turn_stats()
        stats.moves += 1
        self.game.recorder.save(annotate(frame, fighters=fighters, cells=zone, marks=[(frame.grid.center(best), label)]),
                                "deplacement")
        # Pas sur le corps d'un combattant debout devant la case, ni sur le mien (03/10 : case juste au-dessus de moi,
        # cachée par mon propre corps : le clic m'a sélectionné, « n'a pas bougé »).
        point, spread = clear_point(frame.grid, best, fighters if me in fighters else fighters + [me],
                                    timeline_portraits(frame))
        self.game.click(point, spread)
        tracking = self._me_cell is not None or self._find_me is not None  # mode équipe : ma case suivie
        arrived, frame = self._wait_arrival(me, best, steps)
        if arrived:
            if tracking:
                self._me_cell = best
            return steps  # arrivé
        mine = [f.cell for f in find_fighters(frame) if f.team == me.team]
        if me.cell not in mine:
            # Parti, mais pas exactement où prévu : le cercle de ma couleur le plus proche de là où je voulais aller,
            # hors des cases de mes alliés. Arrêté en route (03/10 : chemin coupé par un trou et un allié, 1 PM
            # dépensé sur 3, 2 PM perdus) : PM comptés d'après la distance parcourue, la bulle corrige s'il en reste
            # moins (_pm_left).
            allies = {f.cell for f in fighters if f.team == me.team and f.cell != me.cell}
            now = min((c for c in mine if c not in allies), key=lambda c: distance(c, best), default=best)
            if tracking:
                self._me_cell = now
            if now != best:
                used = min(steps, distance(me.cell, now))
                log.info("Arrêté en %s au lieu de %s (%d PM dépensés sur %d) : chemin bloqué", now, best, used, steps)
                return used
            return steps
        if retry and self._my_turn(frame, me.team):
            # Clic tombé sur un combattant qui passait sur la case (03/10) : un nouveau clic, d'après les positions
            # de maintenant.
            log.info("Mon personnage n'a pas bougé : je reclique sur %s", best)
            now = find_fighters(frame)
            return self._walk(frame, me, best, now if me in now else now + [me], zone, label, steps, retry=False)
        stats.stuck_moves += 1
        path = self.game.recorder.save(frame.image, "deplacement_rate", lossless=True)
        log.warning("Mon personnage n'a pas bougé (capture : %s)", path)
        return 0

    def _wait_arrival(self, me: Fighter, best: Cell, steps: int) -> tuple[bool, Frame]:
        """Après le clic de déplacement : (mon cercle est sur `best`, dernière capture).

        Fixe : per_cell_moved s par case, puis jusqu'à 8 captures toutes les 0,2 s. Condition : captures dès le clic,
        une toutes les 0,1 s, pendant autant de temps au plus (per_cell_moved s par case + 8 × 0,2 s)."""

        def arrived(frame: Frame) -> bool:
            return best in {f.cell for f in find_fighters(frame) if f.team == me.team}

        if self._watching:
            shown, frame = self._watch(f"arrivée en {best}", arrived,
                                       self.settings.per_cell_moved * steps + ARRIVAL_CHECKS * ARRIVAL_EVERY)
            return shown is not None, frame
        self.game.sleep(self.settings.per_cell_moved * steps)
        for _ in range(ARRIVAL_CHECKS):
            frame = self.game.look()
            if arrived(frame):
                return True, frame
            self.game.sleep(ARRIVAL_EVERY)
        return False, frame

    def wanted_range(self) -> tuple[int, int]:
        """Portée à viser au placement : celle du sort de dégâts qui porte le plus loin."""
        return reach_range(self.spells, self._use) or self.settings.spell_range

    @property
    def move_points(self) -> int:
        """PM au début d'un tour (placement de l'équipe : où le sort portera après avoir marché)."""
        return self._max_pm

    def place(self, frame: Frame, my_team: str, last_try: bool = True) -> str | None:
        """Placement : aller sur la case libre de ma couleur la plus intéressante (voir choose_placement).

        Renvoie mon équipe ; None si les cases ne se lisent pas encore (bandeau d'entrée en combat) et qu'un nouvel
        essai viendra ; "" si j'abandonne (dernier essai) ou si ma couleur reste inconnue. `placed_cell` : ma case à la
        fin, si elle est sûre (mode équipe : la case du principal).

        Cas net : une seule case occupée d'une couleur (moi, seul de mon équipe), à l'écart des autres combattants.
        Sinon (03/10 : cases rouges et bleues en damier, le corps d'un monstre déborde sur les cases voisines de
        l'autre couleur, 3 cases « occupées » en rouge contre 1 en bleu : le bot s'est cru bleu, sur la case du
        monstre ; mon propre corps qui déborde sur 2 cases), la couleur se vérifie : le jeu ne me déplace que sur une
        case libre de mon équipe. Clic sur une case libre de la couleur supposée ; s'il ne me déplace pas, l'autre."""
        self.placed_cell = None
        placement = read_placement(frame)
        occupied = {team: standing_cells(cells) for team, cells in placement.occupied.items()}
        other = next(t for t in occupied if t != my_team)
        if len(occupied[my_team]) != 1 and len(occupied[other]) == 1:
            my_team, other = other, my_team
        mine, enemies = occupied[my_team], occupied[other]
        if not mine or not enemies or not (placement.free[my_team] or placement.free[other]):
            if not last_try:
                return None
            path = self.game.recorder.save(frame.image, "placement_illisible", lossless=True)
            log.info("Placement : je ne vois pas où je suis (%d case(s)) ou pas d'ennemi (%d) : je ne bouge pas "
                     "(capture : %s)", len(mine), len(enemies), path)
            return ""
        grid = frame.grid
        standing = [Fighter(team, *grid.center(c), c) for team, cells in placement.occupied.items() for c in cells]
        blockers = read_blocks(frame, standing) if tactical_mode(frame) else None
        wanted = reach_range(self.spells, self._use) or self.settings.spell_range
        clean = len(mine) == 1 and all(distance(next(iter(mine)), c) > 2 for c in placement.occupied[other])
        if clean:
            (me,) = mine
            best = choose_placement(me, placement.free[my_team], enemies, wanted, blockers)
            if best is None:
                log.info("Placement : ma case %s est déjà la meilleure", me)
                self.placed_cell = me
                return my_team
            self._place_on(grid, best, standing, my_team, enemies, wanted, came_from=me)
            return my_team  # cas net : ma couleur ne fait pas de doute, même si le déplacement ne s'est pas vu
        else:
            log.info("Placement : où suis-je ? %s %s, %s %s : je vérifie ma couleur en allant sur une case libre",
                     len(mine), my_team, len(enemies), other)
        for team, foes in ((my_team, enemies), (other, mine)):  # couleur supposée d'abord, puis l'autre
            free = placement.free[team]
            if not free:
                continue
            guess = min(free)
            best = choose_placement(guess, free, foes, wanted, blockers) or guess
            if self._place_on(grid, best, standing, team, foes, wanted):
                return team
        log.warning("Placement : aucune case libre ne m'a déplacé : ma couleur reste inconnue")
        return ""

    def _place_on(self, grid, best: Cell, standing: list[Fighter], team: str, enemies: set[Cell],
                  wanted: tuple[int, int], came_from: Cell | None = None) -> bool:
        """Clic sur la case de placement `best` ; True si mon personnage y est allé (`placed_cell`)."""
        nearest = min(distance(best, e) for e in enemies) if enemies else 0
        log.info("Placement : %s -> %s (ennemi le plus proche à %d cases, portée %d à %d)",
                 came_from if came_from is not None else "?", best, nearest, *wanted)
        frame = self.game.look()
        self.game.recorder.save(annotate(frame, cells={best}, marks=[(grid.center(best), "placement")]),
                                "placement_choisi")
        self.game.click(*clear_point(grid, best, standing))  # pas sur un personnage debout devant la case
        self.game.sleep(0.4)
        if best not in read_placement(self.game.look()).occupied[team]:
            log.warning("Placement : mon personnage ne semble pas être allé en %s", best)
            return False
        self.placed_cell = best
        return True

    def _end_turn(self, my_team: str) -> None:
        """Fin de tour, puis la ligne de synthèse du tour (TurnStats) au journal."""
        stats = self._turn_stats()  # tour sans sort ni déplacement : mesuré d'ici
        self._pass_turn(my_team)
        self._stats = None
        log.info("%s", stats.describe(time.monotonic(), self.settings.wait_mode))
        self._fight_turns.append((time.monotonic() - stats.started, stats))

    def fight_report(self) -> dict:
        """Bilan du combat (debug/report.py) : tours joués, temps, sorts lancés par sort, refus, déplacements."""
        turns = getattr(self, "_fight_turns", [])
        stats = [s for _, s in turns]
        return {"tours": len(turns), "duree_tours": round(sum(d for d, _ in turns), 1),
                "sorts": dict(self._use.fight_casts), "refus": sum(s.tries - s.casts for s in stats),
                "clics_sans_effet": sum(s.missed_clicks for s in stats), "deplacements": sum(s.moves for s in stats),
                "deplacements_sans_bouger": sum(s.stuck_moves for s in stats)}

    def _pass_turn(self, my_team: str) -> None:
        """Touche de fin de tour (F1), sinon le bouton ; on vérifie que la main passe."""
        if self._end_turn_key:
            self.game.press(self._end_turn_key)
            log.info("Fin de tour (%s)", self._end_turn_key.upper())
            if self._turn_passed(my_team, checks=10):
                self.turns_not_passed = 0
                return
            # Pressée pendant l'animation du dernier sort (fast_end), elle a pu être ignorée : une seconde fois.
            self.game.press(self._end_turn_key)
            if self._turn_passed(my_team, checks=6):
                self.turns_not_passed = 0
                return
            log.warning("%s n'a pas passé le tour : je clique sur le bouton désormais", self._end_turn_key.upper())
            self._end_turn_key = ""
        frame = self.game.look()
        button = find_end_turn_button(frame)
        if button is None:
            if read_scene(frame).phase is Phase.COMBAT:
                path = self.game.recorder.save(frame.image, "fin_de_tour_introuvable", lossless=True)
                log.warning("Bouton de fin de tour introuvable (capture : %s)", path)
            return  # sinon le combat vient de se terminer : plus de tour à finir
        self.game.click(button, frame.layout.spread(*END_TURN_SPREAD))
        log.info("Fin de tour")
        if self._turn_passed(my_team, checks=6):
            self.turns_not_passed = 0
            return
        # 17/10 (journal 161256, Vieenjaune ×3) : clic 1 s après un déplacement, le personnage marchait encore : ignoré,
        # vu 4 s plus tard, et le tour suivant (« rien d'utile ») recliquait. Comme pour F1 : un second clic.
        frame = self.game.look()
        button = find_end_turn_button(frame)
        if button is not None:
            self.game.click(button, frame.layout.spread(*END_TURN_SPREAD))
        if self._turn_passed(my_team, checks=10):
            self.turns_not_passed = 0
        else:
            self.turns_not_passed += 1
            log.warning("La main ne semble pas être passée")

    def _turn_passed(self, my_team: str, checks: int) -> bool:
        """Main passée, ou combat fini, dans les `checks` × 0,25 s qui suivent la touche ou le clic de fin de tour.
        Fixe : une capture toutes les 0,25 s ; condition : captures dès le geste, une toutes les 0,1 s."""

        def passed(frame: Frame) -> bool:
            return not self._my_turn(frame, my_team)

        if self._watching:  # la timeline reste visible même souris sur la carte
            return self._watch("main passée", passed, checks * TURN_PASSED_EVERY, park=False)[0] is not None
        for _ in range(checks):
            self.game.sleep(TURN_PASSED_EVERY)
            if passed(self.game.look(park=False)):  # la timeline reste visible
                return True  # main passée, ou combat fini
        return False
