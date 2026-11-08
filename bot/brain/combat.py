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
from bot.brain.arena import Arena, Arenas
from bot.brain.game import Game
from bot.brain.popups import close_flash_menu
from bot.brain.recovery import is_deaf, mark_deaf, mark_heard, report_episode, unlock_click
from bot.brain.spellbook import SpellBook
from bot.brain.tactics import (DANGER_DISTANCE, SIGHT_DOUBT_CELLS, CombatSpell, HitLog, SpellUse, reach_range,
                               refusals, refusals_on_hit_target, refused_beyond, sight_refusals, spell_range)
from bot.debug.draw import annotate
from bot.perception.chat import _plain, cast_announced, combat_lines
from bot.perception.colors import BLUE, RED
from bot.perception.daylight import DAY_FLOOR, Tint
from bot.perception.fighters import Fighter, cell_point, clear_point, find_fighters
from bot.perception.frame import Frame
from bot.perception.grid import Cell, distance
from bot.perception.hp import read_hovered_hp, read_hovered_name, same_name, sheet_visible
from bot.perception.layout import Point
from bot.perception.placement import read_placement, standing_cells
from bot.perception.popups import find_flash_menu
from bot.perception.sight import (RangeView, Terrain, in_line_only, line_of_sight, locate_caster, read_blocks,
                                  range_fit, read_range, read_terrain, sight_agreement)
from bot.perception.spellbook import slot_greyed, slot_scores
from bot.perception.timeline import challenge_icons, timeline_portraits
from bot.perception.ui import spell_bar_tab
from bot.perception.toolbar import ToolbarButton, find_creature_button, find_tactical_button
from bot.perception.ui import (
    END_TURN_SPREAD,
    PA_BUBBLE,
    SPELL_SLOT_SPREAD,
    TIMELINE_DOWN,
    TIMELINE_UP,
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
    timeline_at_top,
)
from bot.state.scene import Phase, is_my_turn, read_scene

log = logging.getLogger(__name__)

CAST_ATTEMPTS = 3  # clics sur la cible au plus, quand le sort ne part pas
TRANSFORMED_KEYS, TRANSFORMED_MAX_RANGE = ("1", "2", "3"), 10  # sorts inconnus (transformation) : _transformed_turn
LOCATE_RANGE = 6  # repérage par la portée d'un sort : portée maximale visée (losange visible en entier, assez grand)
LOCATE_SAVED = 12  # captures « reperage_portee » gardées par personnage et par session (mesure du repérage)
LOCATE_TRIES = 2  # repérage par la portée : sorts essayés au plus (le second si la portée du premier ne se lit pas)
HOVER_SHEET = 0.35  # survol d'un cercle : attente avant de lire la fiche (nom) du combattant survolé
HOVER_MISSES = 2  # survols sans aucune fiche, sans qu'une ait jamais paru : le survol ne sert plus de la session
NOT_GONE_LIMIT = 2  # lancers de suite jamais partis sur un ennemi : sort écarté pour la session (03/10)
SELF_CAST_METHODS = ("clic droit", "raccourci + clic sur moi")  # sort sur moi : une tentative de chaque
# Bonus de portée qu'un allié peut m'avoir donné sans que je le sache (Tir Éloigné touche les alliés à 2 cases : +2 au
# niveau 2) : une portée lue jusque-là au-delà de celle du sort reste crédible.
ALLY_RANGE_BONUS = 2
MAX_CASTS_PER_TURN = 5  # garde-fou
RELIEF_MARGIN = 4  # cases de marge sur la portée prévue, sur une carte à plusieurs niveaux
RELIEF_MIN_WRONG = 2  # cases à portée mal prédites, au moins, pour croire la carte à relief (une seule : animation, ombre)
FALLBACK_SPELL = "sort_configure"  # identifiant du sort de la case configurée, sans sort connu (spells)
FALLBACK_REFUSED_TURNS = 2  # tours où le sort de secours est refusé d'entrée : sorts changés (transformation)
STEP_PROOF_WAIT = 1.5  # s : marche d'un pas avant de revoir les cercles (_locate_by_step)
BAR_ICON_MATCH = 0.65  # case de la barre qui montre l'icône du sort (comme locate_in_bar) : _bar_shows
ARENA_TRIES = 6  # essais de reconnaissance de la carte officielle par combat (_recognize_arena)
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
# 21/10 (journaux du 20 et du 21/10) : 74 annonces sur 74 arrivées 0,5 à 0,8 s après le clic. Refus conclu à 1,2 s (avant :
# 2,0) ; en arrière-plan (pas de rafale), parti CHAT_PA_GRACE_BACKGROUND s après l'annonce (avant : 1,2 s de plus, les
# sorts confirmés par le chat jugés partis à 1,7-1,9 s au lieu de 0,5-0,6). Idée de l'utilisateur : s'appuyer sur le chat.
CHAT_REFUSED_AFTER = 1.2  # s après le clic sans annonce ni PA dépensés : sort refusé
CHAT_PA_GRACE = 1.2  # s après l'annonce pour voir les PA changer (capture de départ d'une rafale), puis parti quand même
CHAT_PA_GRACE_BACKGROUND = 0.3  # arrière-plan : la rafale n'y joue jamais, le temps que la bulle des PA suive
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
# Relecture du 21/10 : 4 « clavier sourd … débloqué : oui » dans un combat, 4 fenêtres, touches parties 0,2 s après
# « Mon tour » : portée juste lente à venir, pas une fenêtre sourde. Une capture de plus avant le clic de déblocage.
RANGE_LATE = 0.35
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
RESELECT_AFTER_MOVE = 1.5  # s après la fin d'un déplacement (arrivée vue ou non) : sort refusé, même touche une fois de plus
# Sort enchaîné sur une cible (19/10, journal 204116, vérifié dans le chat de combat) : Pelle Fantomatique cliquée 1,3 à
# 1,9 s après Lancer de Pièces, pendant son animation (pièce en vol) : le jeu ignore le clic, 3 partis sur 26 ; relancée
# 4 à 6 s après : 8 sur 8. Les PA baissent ~0,5 s après le clic, bien avant la fin de l'animation : « parti » n'est pas
# « prêt ». Le clic d'un sort attend CHAIN_GAP s après celui du dernier sort lancé sur une cible ce tour ; rien après
# un sort sur moi (flèches après Tir Éloigné : 8 sur 8 à 1,3-2,2 s).
# 21/10 (Calanques) : 4 Pelles sur 9 encore refusées à 2,6-2,9 s, les autres parties à 2,6-2,7 s : l'animation de Lancer
# de Pièces dure plus longtemps sur une cible lointaine (la pièce vole jusqu'à elle). 3 s.
# 21/10 au soir (journaux des 20 et 21/10) : pas le délai. 2e sort sur la même cible, refusé une fois : relancé de la
# même case, 0 parti sur 10 (même 6,7 s après le premier) ; sur une autre cible 3 sur 3, après un pas 5 sur 6. Refus après
# un sort parti sur cette cible ce tour : un essai de plus à un autre endroit de sa case (_cast, à mesurer), puis plus
# aucun sort sur elle d'ici (_play_turn, Cast.OUT_OF_REACH). Le 2e sort vise déjà un autre endroit que le 1er.
CHAIN_GAP = 3.0
# 26/10, l'utilisateur : « se focus sur la lecture du chat ». Le clic suivant n'attend plus CHAIN_GAP s : dès qu'une ligne
# suit l'annonce « Nom lance Sort. » du sort précédent dans le chat de combat (son effet : « ... perd N PDV », « +5 à la
# portée »...), écrite quand son animation touche la cible, CHAIN_CHAT_MARGIN s de plus et le sort suivant part. Chat
# relu toutes les CHAIN_CHAT_EVERY s (OCR : ~40 ms). Rien de lu (pas d'OCR, canal fermé) : CHAIN_GAP, comme avant.
CHAIN_CHAT_EVERY = 0.1
CHAIN_CHAT_MARGIN = 0.15
# Écoute du serveur (connection/network.py, 26/10) : sort confirmé par « GA;102 » (PA dépensés), reçu ~0,2 s après le
# clic (au lieu de la bulle des PA, 0,5-0,6 s) ; rien après NET_REFUSED s : refusé (au lieu de 1,2 s).
NET_REFUSED = 0.8  # 26/10 (audit) : 70 sorts sur 847 confirmés au-delà de 0,5 s
# Enchaînement avec l'écoute (26/10, idée de l'utilisateur) : le serveur annonce dégâts et PA dès le clic, pas la fin de
# l'animation (que le chat, lui, écrit 1,2 à 2 s après). Sort suivant cliqué NET_CHAIN_FIRST s après le précédent ;
# ignoré par le jeu (animation en cours : rien du serveur en NET_REFUSED s), recliqué toutes les NET_RETRY_EVERY s
# tant que le sort reste sélectionné et la cible debout, jusqu'à CHAIN_GAP s après le précédent.
NET_CHAIN_FIRST = 1.0
NET_RETRY_EVERY = 0.3
# La touche du sort suivant aussi est ignorée pendant l'animation (26/10, journal 213859 : touche pressée 0,47 s après le
# lancer, ignorée ; nouvel essai 1 s plus tard, sorts enchaînés 1,6 à 2 s après le précédent). Avec l'écoute : touche
# pressée NET_KEY_AFTER_CAST s après le lancer précédent, puis de nouveau toutes les NET_KEY_RETRY_EVERY s (NET_KEY_RETRIES
# fois au plus) tant que la portée ne s'affiche pas.
NET_KEY_AFTER_CAST = 0.9
NET_KEY_RETRY_EVERY, NET_KEY_RETRIES = 0.25, 1  # 3 appuis au plus par sort (l'utilisateur, 26/10 : pas de rafale)
NET_CLICK_RETRIES = 2  # clics de lancer recommencés au plus (_animation_retry)
# Clic de déplacement pendant l'animation de mon dernier sort : ignoré par le jeu, comme la touche (27/10, 11 sessions :
# 16 « pas bougé » sur 111 clics à moins de 0,6 s du lancer, 0 sur 13 entre 0,6 et 1,2 s) : pas avant WALK_AFTER_CAST s.
WALK_AFTER_CAST = 0.7
# Avec l'écoute, depuis la réponse du serveur (début de l'animation) : 01/11, 413 clics de déplacement depuis le 27/10,
# ignorés 6 fois sur 19 à moins de 0,8 s de la réponse, 5 sur 72 entre 0,8 et 1 s, 2 sur 24 au-delà ; Oeil de Taupe 6
# sur 7, Chance 0 sur 25. Pas avant WALK_AFTER_SERVED s ; un sort après lequel le premier clic est ignoré et le second,
# au même endroit, pris (animation plus longue) : WALK_SLOW_MARGIN s de plus que ce clic-là, WALK_SLOW_MAX s au plus,
# pour toute l'équipe jusqu'à la fin de la session (_slow_animations).
WALK_AFTER_SERVED = 1.0
WALK_SLOW_MARGIN, WALK_SLOW_MAX = 0.4, 2.5
_slow_animations: dict[str, float] = {}  # nom du sort -> attente après la réponse du serveur
PA_BUFF_MAX = 4  # PA en plus du maximum (bonus d'un allié) : au-delà, la bulle est mal lue (« 10 » lu « 16 » en JPEG)
ARRIVAL_CHECKS, ARRIVAL_EVERY = 8, 0.2  # fixe : arrivée vérifiée 8 fois, toutes les 0,2 s, après per_cell_moved
# Écoute : déplacement annoncé par le serveur 0,18 s après le clic (médiane, 249 déplacements depuis le 27/10), 223 fois
# sur 249 avant 0,6 s ; plus tard, un clic gardé par le jeu pendant l'animation d'un sort (01/11 : 10 sur 249). Rien en
# NET_MOVE_SILENT s après le premier clic : il n'est pas pris, nouveau clic aussitôt (01/11, l'utilisateur : un reclic
# vaut mieux qu'une longue attente ; compté à part des déplacements ratés, TurnStats.move_retries).
NET_MOVE_SILENT = 0.6
# Mort annoncée avec mon dernier sort : le jeu garde le clic de déplacement pendant l'animation de la mort (01/11, journal
# 113029 : deux monstres tués par Oeil de Taupe, déplacement parti 4,5 s après, 0,2 s après l'abandon du bot). Pas de
# reclic rapide alors, annonce attendue DEATH_MOVE_WAIT s.
DEATH_MOVE_WAIT = 6.0
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
    move_retries: int = 0  # clics de déplacement pas pris, recliqués (pas des ratés : le second peut marcher)
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
        if self.move_retries:
            counts.append(_plural(self.move_retries, "reclic"))
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
    lift: dict[Cell, float] = field(default_factory=dict)  # relief de la carte du client, en niveaux (MapData.lift)
    # Carte officielle reconnue (brain/arena.py) : ses blocs et trous font foi pour tout le combat ; l'écran ne sert
    # plus qu'à voir les combattants (24/10 : 1388 captures, aucun bloc officiel lu comme du sol, 1513 sols lus blocs).
    arena: Arena | None = None
    arena_tries: int = 0
    placed: tuple = (frozenset(), None)  # cases de placement lues (le plus grand nombre vu) et leur capture
    placement_checked: bool = False  # contrôle contre la référence fait (CombatAI._check_placements)

    def adopt(self, arena: Arena) -> None:
        self.arena = arena
        # Obstacles : la ligne de vue seulement (la marche suit unwalkable). Les arbres de la carte du client sont des
        # blocs (brain/mapdata.py, 01/11) ; les références les prenaient pour du sol.
        self.obstacles = set(arena.blocks)
        self.unwalkable = set(arena.unwalkable)
        self.lift = dict(getattr(arena, "lift", {}))
        self.known = True

    @property
    def raised(self) -> set[Cell]:
        """Cases dessinées en bloc en mode tactique : les blocs, et les cases interdites surélevées (sommets)."""
        return self.obstacles | {c for c in self.unwalkable if self.lift.get(c, 0.0) > 0}

    def learn_terrain(self, terrain: Terrain, fighters: list[Fighter]) -> None:
        """Carte propre : une case revue comme du sol n'est plus un obstacle (un chiffre de dégâts ou une animation
        posés dessus un instant ne la bloquent pas pour le reste du combat)."""
        if self.arena is not None:
            self.seen |= terrain.seen | {f.cell for f in fighters}
            return
        self.obstacles = (self.obstacles - terrain.seen) | terrain.blocks
        self.unwalkable = (self.unwalkable - terrain.seen) | terrain.blocks | terrain.holes
        self.seen |= terrain.seen | {f.cell for f in fighters}
        self.known = True

    def learn_range(self, view: RangeView) -> None:
        free = view.castable | view.no_sight
        if self.arena is not None:
            self.seen |= free | view.obstacles
            return
        # Paroi teintée par la portée (côté d'un plateau ou d'une fosse) : pas un bloc, même lue bloc avant (17/10).
        self.obstacles = (self.obstacles - free - view.walls) | view.obstacles
        self.unwalkable = (self.unwalkable - free) | view.obstacles
        self.seen |= free | view.obstacles
        self.known = True

    def unseen(self, cells: set[Cell]) -> set[Cell]:
        """Terrain lu, les cases jamais vues depuis le début du combat (cachées par le corps d'un combattant) : pas de
        marche, ce sont peut-être des trous (03/10 : l'Enutrof a voulu aller sur un trou caché derrière un allié)."""
        return cells - self.seen if self.known and self.arena is None else set()

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
    def __init__(self, game: Game, settings: CombatSettings, spellbook: SpellBook | None = None,
                 name: str | None = None) -> None:
        self.game = game
        self.name = name  # nom du personnage (mode équipe) : lu sur sa fiche au survol, pour savoir lequel est moi
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
        # Ma case suivie est prouvée (portée d'un sort, nom au survol, arrivée vue, case sûre donnée par l'équipe) ;
        # jamais devinée (19/10 : 4 Crâs sur le premier cercle rouge venu, 44 déplacements ratés, 15 tours sans tirer).
        self._me_sure = False
        self._proven_now = False  # ... prouvée pendant ce tour (pas seulement héritée du tour d'avant)
        self._doubt = False  # déplacement raté ou arrêté en route : ma case est à revérifier avant d'agir
        self._no_proof = False  # _whoami : des cercles de ma couleur, aucun prouvé le mien
        self._timeline_moved = -1  # tour où la timeline a été changée de côté (_move_timeline)
        self._spells_dead = False  # ce tour, aucun sort connu ne s'est sélectionné (transformation : _transformed_turn)
        self._stepped_turn = -1  # tour du dernier repérage par un pas (_locate_by_step)
        self._shown_view: tuple[Cell, str, RangeView] | None = None  # dernière portée lue : (ma case, sort, cases)
        self._hover_seen = False  # une fiche est déjà apparue au survol pendant cette session
        self._hover_misses = 0  # survols sans fiche : au-delà de HOVER_MISSES, le survol ne sert plus (arrière-plan)
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
        self._clicks: tuple[int, dict[Cell, list[Point]]] = (-1, {})  # (tour, points cliqués sur chaque case ce tour)
        self._cast_at = float("-inf")  # heure du dernier clic de lancer (sélection refusée juste après : animation)
        self._served_at = float("-inf")  # heure de la réponse du serveur au dernier sort (début de son animation)
        self._served_spell: CombatSpell | None = None  # ce sort (_walk : attente propre à son animation)
        self._target_cast_at = float("-inf")  # heure du clic du dernier sort lancé sur une cible ce tour (CHAIN_GAP)
        self._target_spell = ""  # son nom, et le chat de combat d'avant son clic (_effect_in_chat)
        self._clicked_at = 0.0  # time.monotonic() juste avant le dernier clic de lancer (écoute du serveur)
        self._cast_checked: set[tuple[str, int]] = set()  # (sort pressé, sort parti d'après le serveur) déjà dits
        self._spellbook_used = False  # sorts de la tactique pris dans le livre de sorts (_use_spellbook)
        self._walk_clicked_at = 0.0  # et du dernier clic de déplacement
        self._chain_chat: list[str] | None = None
        self._moved_at = float("-inf")  # fin du dernier déplacement (sort refusé juste après : RESELECT_AFTER_MOVE)
        self._life_saved: set[str] = set()  # captures de fiche déjà gardées (vie lue, illisible : _read_life)
        # Fenêtre sourde aux touches (20/10, _deaf_key) : vérifiée une fois par tour ; sourde, sorts cliqués ce tour.
        self._deaf_checked = False
        self._keys_deaf = False
        self._spell_stuck = False  # un sort est resté sélectionné malgré Échap : plus aucun clic sur la carte ce tour
        self._sight_refused = False  # dernier sort refusé avant le clic : cible montrée sans ligne de vue par le jeu
        self._team: str | None = None  # ma couleur pendant mon tour (ennemis encore debout : _foes_standing)
        self._bar_turn = -1  # tour (SpellUse.turn) où les sorts grisés de la barre ont été lus
        self._unknown_floor_saved = False  # capture « sol_tactique_inconnu » : une par session (_note_unknown_floor)
        self.unknown_tint = False  # sol tactique pas reconnu, mode tactique coché, ce combat (_note_unknown_floor)
        self.told_fight = False  # combattants donnés par le serveur pendant ce combat (_seen_fighters)
        self.new_combat()

    @property
    def spells(self) -> list[CombatSpell]:
        """Sorts connus (fenêtre des sorts + référence Solomonk), sinon le sort de la case configurée."""
        if not self._spells:
            slot = self.settings.spell_slot
            entry = self.spellbook.in_slot(slot) if self.spellbook else None
            self._spells = [CombatSpell(slot, FALLBACK_SPELL, f"sort de la case {slot}", pa=0,
                                        spell_range=(entry.reach if entry and entry.reach else self.settings.spell_range),
                                        damage=1.0, per_turn=self.settings.casts_per_turn)]
        return self._spells

    def new_combat(self) -> None:
        self._use = SpellUse()  # délais de relance et bonus de portée : valables sur le combat
        self.unknown_tint = False
        self.told_fight = False  # combattants donnés par le serveur pendant ce combat (_seen_fighters)
        self._turn_ended_at: float | None = None  # fin de mon dernier tour (suite du même tour, SAME_TURN_WINDOW)
        self.map = MapMemory()  # carte de ce combat (mode équipe : partagée, team/loop.py)
        self._warned_pm = False
        self._warned_pa = False
        self._sight_saved = False
        self._bar_changed = False  # aucun de mes sorts dans la barre (transformation), vu sur un sort refusé
        self._fallback_refused: set[int] = set()  # tours où le sort de secours a été refusé d'entrée
        self._map_reliable = True  # faux sur une carte à relief : mes cases y sont décalées, le jeu tranche
        self._tactical_tried = False
        self._creature_tried = False
        self._warned_not_tactical = False
        self._tint_logged = False  # teinte de nuit de la carte : dite une fois par combat (_note_tint)
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
            self._note_unknown_floor(frame)
            return  # déjà coché (carte cachée par une fiche...) : cliquer le désactiverait
        self._tactical_tried = True
        if button is None and timeline_at_top(frame):
            # 26/10 (journal 234008) : timeline mise en haut un combat plus tôt (équipe cachée dessous, _move_timeline) ;
            # les boutons du mode tactique et du mode créature ne se voient plus (pas au-dessus d'elle en haut) : mode
            # tactique perdu les combats suivants, dans les 8 fenêtres, et 9 cercles pareils (invocation) ont fait passer
            # leur tour à la moitié de l'équipe. La timeline est remise en bas, puis le bouton cherché de nouveau.
            log.info("Bouton du mode tactique caché (timeline en haut) : je remets la timeline en bas")
            self.game.click(frame.layout.point(*TIMELINE_DOWN), frame.layout.spread(2, 2))
            self.game.sleep(0.5)
            frame = self.game.look(park=True)
            if tactical_mode(frame):
                return
            button = find_tactical_button(frame)
            if button is not None and button.active:
                return
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

    def _note_tint(self, frame: Frame) -> None:
        """Dit au journal, une fois par combat, que la carte est teintée par la nuit et ramenée au jour pour la lire
        (perception/daylight.py ; 26/10, journal 001614 : sans cela, mode tactique et portée des sorts plus reconnus)."""
        tint = getattr(frame, "tint", None)
        if not isinstance(tint, Tint) or self._tint_logged:
            return
        self._tint_logged = True
        log.info("Carte teintée par la nuit (force %.2f : sol %s au lieu de %s) : couleurs de la carte ramenées au jour",
                 tint.strength, " et ".join(str(tuple(round(v) for v in shade)) for shade in tint.floor),
                 " et ".join(map(str, DAY_FLOOR)))

    @property
    def blind_fight(self) -> bool:
        """Combat joué sans voir : sol tactique pas reconnu (teinte de nuit pas apprise) et combattants pas donnés par
        le serveur. Le 01/11 avant l'aube, sol pas reconnu par les 8, mais combattants, cartes et sorts d'après le
        serveur : 1,8 sort par tour, aucun refus ; ce combat-là ne compte pas pour l'arrêt de nuit (team/loop.py)."""
        return self.unknown_tint and not self.told_fight

    def _note_unknown_floor(self, frame: Frame) -> None:
        """Bouton du mode tactique coché, sol tactique pas reconnu : carte cachée (fiche, fenêtre), ou teinte jour/nuit
        d'une autre forme que celle de minuit (aube, crépuscule : pas encore vues le 26/10). Une capture sans perte par
        session, pour mesurer la teinte (perception/daylight.py). Noté pour ce combat (unknown_tint) : team/loop.py
        arrête la session après le combat quand plusieurs personnages l'ont vu (nuit d'une teinte pas encore apprise)."""
        self.unknown_tint = True
        if self._unknown_floor_saved:
            return
        self._unknown_floor_saved = True
        path = self.game.recorder.save(frame.image, "sol_tactique_inconnu", lossless=True)
        log.info("Mode tactique coché mais sol tactique pas reconnu (carte cachée, ou teinte jour/nuit inconnue) "
                 "(capture : %s)", path)

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
        self._team = my_team
        self._is_mine, self._find_me, self._me_cell = is_mine, find_me, me_cell
        self._me_sure, self._proven_now, self._doubt = me_cell is not None, False, False  # me_cell : case sûre
        self._warned_who = False  # « rien ne prouve lequel est moi » : une fois par tour
        self._spells_dead = False
        self._team_cells = team_cells
        self._located = False  # repérage par la portée d'un sort fait ce tour (_locate_by_range)
        self._deaf_checked, self._keys_deaf, self._spell_stuck = False, False, False  # touches réessayées à chaque tour
        self._target_cast_at = float("-inf")
        self._expected_plan = ""
        self._expected_step = None
        net = self._net()
        # 31/10 : mon tour annoncé par le serveur (GTS de mon id) : la main passée le sera aussi (_turn_passed).
        self._turn_told = net is not None and hasattr(net, "fight_turn") and net.fight_turn()[0] == net.my_id
        frame = self.game.look()
        my_team = self._team = self._team_from_net(frame, my_team)
        self._note_tint(frame)
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
            fighters = self._seen_fighters(frame, my_team)
            me, allies, enemies = self._sides(frame, fighters, my_team)
            fighters = fighters + [a for a in allies if a not in fighters]  # alliés invisibles : obstacles aussi
            if self._spell_stuck:
                # Sort resté sélectionné malgré Échap (repérage par la portée, fenêtre sourde) : un clic sur la carte le
                # lancerait sur une case vide. Fin du tour.
                log.warning("Un sort est resté sélectionné malgré %s : je finis mon tour sans toucher la carte",
                            self.settings.cancel_key.upper())
                break
            if me is None and self._no_proof:
                if self._spells_dead and self._transformed_turn(frame, fighters, my_team):
                    casts += 1
                break  # cercles vus, aucun prouvé le mien (déjà dit, capture gardée) : rien sur une supposition
            if me is not None and (self._spells_dead or self._bar_unknown):
                # Sorts changés (transformation) mais moi repéré (par mon pas) : les touches 1 à 3 sur l'ennemi.
                # 24/10 : aucun sort connu dans la barre, le plan visait la case 3 de secours, 30 min de « L'état
                # Citrouillard est requis » sans un coup.
                if self._transformed_turn(frame, fighters, my_team, known=me.cell):
                    casts += 1
                break
            if me is not None and not enemies and self._only_hidden_foes():
                # 01/11 (comparatif) : Prespic invisible, déjà dit par _say_hidden ; ni second regard, ni timeline
                # déplacée, ni capture « combattants introuvables » : le serveur sait qu'il n'y a pas de cible.
                break
            if me is None or not enemies:
                if not looked_again:
                    looked_again = True  # une animation (sort, coup, mort) peut cacher un cercle un instant
                    self.game.sleep(0.3)
                    continue
                # Cercles vraiment absents (pas « vus, aucun prouvé le mien » : 23/10, Contour d'Astrub, équipe changée
                # en citrouilles par l'événement d'Halloween, la timeline déplacée 41 fois pour rien).
                if self._move_timeline(frame):
                    looked_again = False
                    continue  # timeline de l'autre côté de la carte : combattants revus sur une nouvelle capture
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
                self._use.empty_refused.clear()
                committed = committed or step.kind == MOVE
                placed = placed or (step.kind != MOVE and steps == step.pm)
                continue
            spell = step.spell
            target = me if spell.on_self else next((e for e in enemies if e.cell == step.cell), None) or \
                Fighter("", *cell_point(frame.grid, step.cell, self.map.lift), step.cell)  # zone visant une case vide
            log.info("%s sur %s : %s", spell.name, "moi" if spell.on_self else _aim_label(target), step.reason)
            repeats = burst_length(plan) if self.settings.burst else 0
            # Dernière action du plan (le sort, et sa rafale) : fin de tour aussitôt après, sans attendre l'animation.
            # Le sort lui-même est vérifié (PA dépensés, ~0,5 s) : 19/10, 15 derniers sorts jamais partis comptés lancés.
            final = self.settings.fast_end and not spell.on_self and len(plan.steps) == 1 + repeats
            result = self._cast(frame, me, target, fighters, my_team, spell)
            if result is Cast.DONE:
                self._use.record(spell, step.cell, failed=self._critical_failure())
                self._wound(spell, step)
                casts += 1
                # La suite du plan se lance d'ici (le moteur ne bouge jamais entre deux sorts) : les plans suivants
                # partent de ma case, sauf si plus rien ne s'y lance (04/10 : un plan complet, toutes cases et toutes
                # suites de sorts, coûte jusqu'à 0,4 s avec 10 PA, 5 PM et des zones ; il était refait après chaque sort).
                # Sauf un bonus de zone lancé avant le déplacement prévu (27/10, Tir Éloigné : plus d'alliés dans sa
                # zone de ma case de départ) : le plan suivant va d'abord à la case choisie.
                after = plan.rest().first
                committed = committed or after is None or after.kind != MOVE
                done = 0
                if self.settings.burst:
                    done, stuck = self._burst(target, step, burst_length(plan), my_team, MAX_CASTS_PER_TURN - casts,
                                              final=final)
                    casts += done
                    if stuck:
                        break
                if final and done >= repeats:
                    log.debug("Dernier sort du plan parti : fin de tour sans attendre son animation")
                    break
            elif result is Cast.NO_SPELL:
                self._use.unavailable.add(spell.spell_id)  # plus assez de PA pour lui, relance... : un autre
            elif result is Cast.OUT_OF_REACH:
                self._use.refused |= refusals(spell, step.cell, self.spells, self._use)
                if self._sight_refused:
                    self._note_no_sight(me.cell, step.cell)
                hit = refusals_on_hit_target(step.cell, self.spells, self._use)
                if hit:
                    log.info("Plus de sort sur %s d'ici ce tour : une autre cible, ou un pas d'abord", _aim_label(target))
                    self._use.refused |= hit
                if all(e.cell != step.cell for e in enemies):
                    self._use.empty_refused.add(spell.spell_id)  # zone sur une case vide : pas une autre d'ici
                    self._use.empty_refusals[spell.spell_id] += 1  # ... ni du combat, au-delà de EMPTY_REFUSALS_LIMIT
                self._use.beyond.update(refused_beyond(spell, distance(me.cell, step.cell), self.spells, self._use))
            else:
                break
        self._end_turn(my_team)

    def _note_no_sight(self, me: Cell, target: Cell) -> None:
        """La portée affichée par le jeu montre `target` sans ligne de vue depuis ma case `me` : plus aucun sort à ligne
        de vue sur lui d'ici (sight_refusals) ; vu ainsi depuis SIGHT_DOUBT_CELLS de mes cases ce tour, plus visé du
        tour par un sort à ligne de vue (SpellUse.blind_targets)."""
        self._use.refused |= sight_refusals(target, self.spells)
        cells = self._use.no_sight.setdefault(target, set())
        cells.add(me)
        if len(cells) == SIGHT_DOUBT_CELLS:
            log.info("Cible en %s montrée sans ligne de vue par le jeu depuis %d de mes cases (%s) : je ne la vise plus "
                     "ce tour avec un sort à ligne de vue", target, len(cells), ", ".join(map(str, sorted(cells))))

    def _my_turn(self, frame: Frame, my_team: str) -> bool:
        """C'est encore mon tour : timeline (joueur seul) ou fonction du mode équipe (play_turn, is_mine). 31/10 : mort
        d'après le serveur (GA;103 de mon id, ou GTM) : plus jamais mon tour, quoi que montre l'écran."""
        if self._dead_told():
            return False
        if self._is_mine is None:
            return is_my_turn(read_scene(frame), my_team)
        return read_scene(frame).phase is Phase.COMBAT and self._is_mine(frame)

    def _sides(self, frame: Frame, fighters: list[Fighter],
               my_team: str) -> tuple[Fighter | None, list[Fighter], list[Fighter]]:
        """Moi, mes alliés (les autres cercles de ma couleur) et les ennemis. Moi : None en mode équipe si rien ne
        prouve lequel des cercles de ma couleur est le mien (le tour se termine sans rien faire, voir _whoami)."""
        mine = [f for f in fighters if f.team == my_team]
        enemies = [f for f in fighters if f.team != my_team]
        me = self._whoami(frame, mine, fighters, my_team)
        if me is not None and (self._find_me is not None or self._me_cell is not None):
            self._me_cell = me.cell
        allies = [f for f in mine if f is not me]
        friends = self._told_allies()
        if friends and any(e.cell in friends for e in enemies):
            # Jamais un allié pour cible, même si les couleurs lues le disent ennemi (26/10, journal 205705).
            log.warning("Combattant(s) de mon équipe d'après le jeu vu(s) comme ennemi(s) en %s : écartés des cibles",
                        ", ".join(str(e.cell) for e in enemies if e.cell in friends))
            allies += [e for e in enemies if e.cell in friends]
            enemies = [e for e in enemies if e.cell not in friends]
        self._told_life(enemies)
        if self._team_cells is not None and me is not None:
            seen = {f.cell for f in fighters}
            known = self._team_cells()
            self._names = dict(known) if isinstance(known, dict) else {}
            for cell in sorted(set(known) - seen - {me.cell}):
                # Cercle invisible (sous la timeline, derrière un corps) : l'allié est toujours là, jamais visé ni
                # pris dans une zone, et on ne marche pas sur sa case.
                allies.append(Fighter(my_team, *cell_point(frame.grid, cell, self.map.lift), cell))
        return me, allies, enemies

    def _whoami(self, frame: Frame, mine: list[Fighter], fighters: list[Fighter], my_team: str) -> Fighter | None:
        """Moi parmi les cercles de ma couleur, toujours sur une preuve (19/10, donjon d'Incarnam : le premier cercle
        rouge pris faute de mieux, 4 Crâs sur la même case supposée, 44 déplacements ratés et 15 tours sans tirer) :
        1. ma case sûre (donnée par l'équipe, ou prouvée plus tôt) avec un cercle dessus, ou find_me (mode équipe) ;
        2. sinon, ou si un déplacement raté la rend douteuse : la portée d'un sort, affichée autour de MA case ;
        3. sinon : mon nom sur la fiche du cercle survolé ;
        4. sinon : ma case prouvée ce tour, mon cercle caché (corps d'un allié devant, timeline).
        Rien de tout ça en mode équipe : None, on ne joue pas sur une supposition. Joueur seul : son cercle."""
        team = self._find_me is not None or self._me_cell is not None or self.name is not None
        self._no_proof = False
        told = self._told_me(frame, mine, my_team)
        if told is not None:
            # Ma case annoncée par le jeu (GTM, déplacements, poussées : connection/network.py, 26/10) : elle fait foi.
            self._me_sure = self._proven_now = True
            self._doubt = False
            return told
        if not team and len(mine) <= 1:
            return mine[0] if mine else None
        if not self._doubt and (self._me_sure or self._me_cell is None):
            me = self._find_me(frame, mine) if mine and self._find_me is not None else None
            if me is None and self._me_cell is not None:
                me = next((f for f in mine if f.cell == self._me_cell), None)  # un cercle sur ma case sûre : moi
            if me is not None:
                self._me_sure = True
                return me
            if self._proven_now and self._me_cell is not None:
                # Prouvée pendant ce tour (arrivée vue, portée) : seuls mes gestes la changent. Son cercle caché un
                # instant (20/10 : souris restée sur la case d'arrivée, case éclairée, cercle pas vu) : pas de repérage.
                return Fighter(my_team, *cell_point(frame.grid, self._me_cell, self.map.lift), self._me_cell)
        if mine and (self._doubt or not self._located):
            # Pas de cercle sur ma case sûre (case inconnue, périmée : 03/10, un monstre m'avait poussé, 3 déplacements
            # ratés de suite) ou case douteuse : repérage, une fois par tour et après chaque déplacement raté.
            self._located, self._doubt = True, False
            me = self._locate_by_range(frame, mine, fighters) or self._locate_by_hover(mine)                 or self._locate_by_step(frame, mine, fighters)
            if me is not None:
                self._me_sure = self._proven_now = True
                return me
            self._me_sure = self._me_sure and self._proven_now  # case prouvée ce tour : toujours valable
        if self._me_cell is not None and self._me_sure and (mine or my_team):
            return next((f for f in mine if f.cell == self._me_cell), None) or \
                Fighter(my_team, *cell_point(frame.grid, self._me_cell, self.map.lift), self._me_cell)
        if not team:
            # Joueur seul avec plusieurs cercles de sa couleur (invocations) : le premier, comme avant.
            if mine and not self._warned_who:
                self._warned_who = True
                log.warning("%d cercles de ma couleur et rien ne dit lequel est moi : je prends celui en %s",
                            len(mine), mine[0].cell)
            return mine[0] if mine else None
        self._no_proof = bool(mine)
        if mine and not self._warned_who:
            self._warned_who = True  # une fois par tour (remis à zéro dans _play_turn)
            path = self.game.recorder.save(frame.image, "qui_est_moi", lossless=True)
            log.warning("%d cercles de ma couleur et rien ne prouve lequel est moi (ni la portée d'un sort, ni mon nom "
                        "au survol) : je ne joue pas sur une supposition (capture : %s)", len(mine), path)
        return None

    def _net(self):
        """Écoute du serveur utilisable pour ce combat (mon id connu, combat en cours) ; None sinon : l'écran."""
        net = getattr(self.game, "net", None)
        return net if net is not None and net.my_id is not None and net.alive() and net.in_fight() else None

    def _pa_now(self, frame: Frame) -> int | None:
        """Mes PA restants : annoncés par le serveur (GTM, puis chaque action), sinon lus dans la bulle bleue."""
        net = self._net()
        state = net.my_state() if net is not None else None
        return state.pa if state is not None else read_pa(frame)

    def _told_foes(self) -> set[Cell] | None:
        """Cases des ennemis debout d'après le serveur (équipe autre que la mienne) ; None sans écoute de ce combat."""
        net = self._net()
        mine = net.my_team() if net is not None else None
        if mine is None:
            return None
        return {cell for _, cell, team, _ in net.fighters() if team is not None and team != mine}

    def _dead_told(self) -> bool:
        """Mon personnage est mort dans ce combat d'après le serveur (« GA;103;tueur;mon id », ou « GTM » qui le dit
        mort) ; False sans écoute. Dit une fois au journal par combat."""
        net = self._net()
        my_state = getattr(net, "my_state", None) if net is not None else None
        state = my_state() if my_state is not None else None
        if state is None or not state.dead:
            return False
        fight = getattr(net, "fight", None)
        if getattr(self, "_dead_said", None) is not fight:
            self._dead_said = fight
            log.warning("Mon personnage est mort d'après le serveur : plus de tour à jouer dans ce combat")
        return True

    def _seen_fighters(self, frame: Frame, my_team: str) -> list[Fighter]:
        """Combattants debout. Écoute du serveur : sa liste (case et équipe de chacun, GTM, GIC, déplacements,
        poussées, morts), aux couleurs de l'écran (la mienne : `my_team`) ; sinon les cercles vus à l'écran.
        26/10 (journal 205705) : les couleurs lues à l'écran au mauvais moment ont fait taper mes personnages entre
        eux ; l'équipe de chaque combattant vient maintenant du serveur."""
        net = self._net()
        told = net.fighters() if net is not None else []
        mine = net.my_team() if net is not None else None
        if not told or mine is None:
            return find_fighters(frame)
        self.told_fight = True
        self._say_hidden(net, mine, told)
        other = BLUE if my_team == RED else RED
        # Équipe inconnue (invocation annoncée sans elle) : un personnage (id positif) est des miens, un monstre non.
        return [Fighter(my_team if (team == mine if team is not None else who > 0) else other,
                        *cell_point(frame.grid, cell, self.map.lift), cell) for who, cell, team, _ in told]

    def _team_from_net(self, frame: Frame, my_team: str) -> str:
        """Ma couleur d'après le cercle sur ma case annoncée par le jeu : elle l'emporte sur celle reçue (26/10, journal
        205705 : couleur de la timeline lue en retard, celle des Blops ; mes personnages se sont tapé dessus)."""
        net = getattr(self.game, "net", None)
        if net is None or net.my_id is None or not net.in_fight():
            return my_team
        cell = net.my_grid_cell()
        circle = next((f for f in find_fighters(frame) if f.cell == cell), None) if cell is not None else None
        if circle is None or not circle.team or circle.team == my_team:
            return my_team
        log.warning("Mon cercle (case %s annoncée par le jeu) est %s, pas %s : je joue avec l'équipe %s", cell,
                    circle.team, my_team, circle.team)
        return circle.team

    def _say_hidden(self, net, mine: int, told: list) -> None:
        """Ennemi invisible d'après le serveur (31/10, Prespic) : dit une fois par tour, avec sa dernière case connue ;
        il n'est pas une cible (sa case est inconnue jusqu'à ce qu'il réapparaisse)."""
        hidden = getattr(net, "hidden_fighters", None)
        foes = [(who, cell) for who, cell, team in (hidden() if hidden is not None else []) if team != mine]
        turn = net.fight_turn()[2]
        if not foes or getattr(self, "_hidden_said", None) == turn:
            return
        self._hidden_said = turn
        visible = sum(1 for _, _, team, _ in told if team is not None and team != mine)
        log.info("%d ennemi(s) invisible(s) d'après le jeu (dernière case connue : %s)%s", len(foes),
                 ", ".join(str(cell) for _, cell in foes if cell) or "inconnue",
                 "" if visible else " : aucun ennemi visible, pas de cible ce tour")

    def _only_hidden_foes(self) -> bool:
        """Aucun ennemi visible, mais au moins un invisible d'après le serveur (pas de cible ce tour)."""
        net = self._net()
        mine = net.my_team() if net is not None else None
        hidden = getattr(net, "hidden_fighters", None) if net is not None else None
        if mine is None or hidden is None or self._told_foes():
            return False
        return any(team is not None and team != mine for _, _, team in hidden())

    def _told_allies(self) -> set[Cell]:
        """Cases des combattants de mon équipe d'après le jeu (moi excepté) : jamais visés, quelle que soit la couleur
        lue à l'écran. Vide sans écoute."""
        net = getattr(self.game, "net", None)
        if net is None or net.my_id is None or not net.in_fight():
            return set()
        fighters = net.fighters()
        mine = next((team for who, _, team, _ in fighters if who == net.my_id), None)
        if mine is None:
            return set()
        hidden = getattr(net, "hidden_fighters", None)
        # Allié invisible : sa dernière case connue n'est jamais visée non plus (31/10).
        unseen = {cell for _, cell, team in (hidden() if hidden is not None else []) if team == mine and cell}
        return {cell for who, cell, team, _ in fighters if team == mine and who != net.my_id} | unseen

    def _told_me(self, frame: Frame, mine: list[Fighter], my_team: str) -> Fighter | None:
        """Moi d'après le jeu (écoute réseau) : le cercle de ma couleur sur ma case annoncée, sinon cette case (cercle
        caché) ; None sans écoute, hors combat, ou ma case pas encore annoncée."""
        net = getattr(self.game, "net", None)
        if net is None or net.my_id is None or not net.in_fight():
            return None
        cell = net.my_grid_cell()
        if cell is None:
            return None
        return next((f for f in mine if f.cell == cell), None) \
            or Fighter(my_team, *cell_point(frame.grid, cell, self.map.lift), cell)

    def _told_life(self, enemies: list[Fighter]) -> None:
        """Vie exacte des ennemis d'après le jeu (GTM, puis dégâts) : remplace l'estimation (MapMemory.life)."""
        net = getattr(self.game, "net", None)
        if net is None or not net.in_fight():
            return
        told = {cell: state.hp for who, cell, team, state in net.fighters() if who != net.my_id and state.hp > 0}
        for enemy in enemies:
            if enemy.cell in told:
                self.map.life[enemy.cell] = told[enemy.cell]

    def _locate_by_range(self, frame: Frame, mine: list[Fighter], fighters: list[Fighter]) -> Fighter | None:
        """Moi parmi plusieurs cercles de ma couleur quand rien d'autre ne le dit (combat repris sans connaître les
        cases : 03/10, le bot s'est pris pour un allié, déplacements et sorts faux) : la portée d'un sort s'affiche
        autour de MA case. Sa case est le centre du losange coloré (sight.locate_caster), cherché parmi toutes les
        cases : mon cercle peut être caché (sous la timeline, 03/10). Un sort sélectionné puis Échap (~1,2 s) ; un
        second sort si la portée du premier ne dit rien (LOCATE_TRIES). None si aucune portée ne se lit, ou sans mode
        tactique."""
        known = {s.spell_id for s in self.spells}
        others = [s for s in getattr(self.spellbook, "bar_spells", lambda: [])() if s.spell_id not in known]
        # 11/10 : l'Iop n'avait aucune attaque à 2 cases de portée ; un sort hors tactique de la barre (Bond, 1 à 4)
        # se sélectionne aussi bien, et Échap l'annule sans le lancer.
        spells = [s for s in [*self.spells, *others] if not s.on_self and s.spell_range[1] >= 2]
        if not spells or not tactical_mode(frame):
            return None
        # Portée moyenne : un losange trop grand déborde de la carte et son centre se décale (03/10 : Lancer de Pièces,
        # portée 12, centre trouvé 2 cases à côté) ; trop petit, peu de cases. Un sort « en ligne » ne colore qu'une
        # croix : en dernier (19/10 : Flèche de Recul passée de 1-3 à 1-5, préférée à Flèche Empoisonnée, 117 repérages
        # ratés chez les Crâs, 44 « n'a pas bougé » sur 54).
        # Un sort que les PA restants ne paient pas ne montre pas sa portée (20/10 : 2 PA, deux touches pour rien).
        spells = [s for s in spells if s.pa <= self._use.pa]
        if not spells:
            return None
        ranked = sorted(spells, key=lambda s: (s.in_line, abs(s.spell_range[1] - LOCATE_RANGE)))
        self._learn_blocks(frame, fighters)  # trous et blocs connus : jamais colorés, exclus du losange attendu
        # Sorts grisés lus avant de sélectionner : une touche sans portée n'est soupçonnée d'être ignorée (fenêtre
        # sourde, _deaf_key) que pour un sort que la barre de CE tour montre lançable.
        self._read_bar(frame)
        selected = False
        for spell in ranked[:LOCATE_TRIES]:
            shown = self._select_spell(frame, spell)
            if shown is None:
                continue
            selected = True
            expected = spell_range(spell, self._use)
            # Mes cercles lus aussi sur cette capture : sur celle du tour, le mien pouvait manquer (20/10 : souris sur
            # ma case d'arrivée, case éclairée en bleu, cercle pas vu ; il l'est une fois la portée affichée).
            on_shown = [f for f in find_fighters(shown) if f.team == mine[0].team]
            # Pas les cases des autres personnages, prouvées à la fin de leur tour (TeamBot._cells) : 26/10 (journal
            # 190101), équipe serrée dans un coin, losange coupé, plusieurs portées centrées chacune sur un allié ;
            # écartées, il ne reste souvent qu'un cercle (locate_caster refuse quand plusieurs restent plausibles).
            claimed = set(self._team_cells()) if self._team_cells is not None else set()
            circles = {f.cell for f in [*mine, *on_shown]} - claimed
            found = locate_caster(shown, expected, circles, fighters, exclude=self.map.unwalkable,
                                  in_line=spell.in_line, modifiable=spell.range_modifiable)
            if self._locate_saved < LOCATE_SAVED:  # captures pour vérifier le repérage (sans repère dessiné dessus)
                self._locate_saved += 1
                self.game.recorder.save(shown.image, "reperage_portee")
            self._cancel_spell()
            if found is None:
                log.info("Qui est moi parmi %d cercles de ma couleur ? La portée de %s ne le dit pas", len(mine),
                         spell.name)
                continue
            shown_range = "" if found.spell_range == expected else \
                f", affichée {found.spell_range[0]} à {found.spell_range[1]} au lieu de {expected[0]} à {expected[1]}"
            if found.hidden:
                log.info("Moi : en %s, au centre de la portée de %s%s ; mon cercle est caché", found.cell, spell.name,
                         shown_range)
                return Fighter(mine[0].team, *cell_point(frame.grid, found.cell, self.map.lift), found.cell)
            # Le cercle pile au centre, jamais celui d'à côté (celui d'un allié). Aucun cercle connu là (ne devrait pas
            # arriver : locate_caster ne rend que l'un d'eux, ou « caché ») : la portée ne prouve rien.
            circle = next((f for f in [*mine, *on_shown] if f.cell == found.cell), None)
            if circle is None:
                log.info("Qui est moi parmi %d cercles de ma couleur ? Centre de la portée de %s en %s, sur aucun "
                         "cercle connu", len(mine), spell.name, found.cell)
                continue
            log.info("Moi : le cercle en %s (au centre de la portée de %s%s)", circle.cell, spell.name, shown_range)
            return circle
        self._spells_dead = not selected  # aucune touche n'a montré de portée : sorts changés ?
        return None

    def _locate_by_step(self, frame: Frame, mine: list[Fighter], fighters: list[Fighter]) -> Fighter | None:
        """Moi parmi des cercles tous pareils, quand la portée ne le dit pas (24/10, Astrub 4,-20 : équipe changée en
        citrouilles par l'événement d'Halloween, serrée ; la portée des sorts cachée sous les cercles des alliés, tous
        les tours passés sans rien faire) : un pas vers l'ennemi le plus proche, sur une case libre que le plus de mes
        cercles peuvent atteindre ; le cercle de ma couleur arrivé sur une nouvelle case, c'est moi. Une fois par tour."""
        if self._stepped_turn == self._use.turn or not mine:
            return None
        team = mine[0].team
        enemies = [f for f in fighters if f.team and f.team != team]
        pm = read_pm(frame)
        pm = self._max_pm if pm is None else pm  # 24/10, Orée (transformés en Abraknydes) : PM illisibles, pas de pas
        if pm <= 0 or not enemies:
            return None
        self._stepped_turn = self._use.turn
        taken = {f.cell for f in fighters} | set(self.map.unwalkable)

        def reach(cell: Cell) -> int:
            return sum(distance(cell, m.cell) <= pm for m in mine)

        cells = [c for c in frame.grid.cells() if c not in taken and reach(c)]
        if not cells:
            return None
        cell = min(cells, key=lambda c: (-reach(c), min(distance(c, e.cell) for e in enemies), c))
        log.info("Cercles de ma couleur tous pareils : un pas en %s vers l'ennemi ; le cercle qui y arrive, c'est moi",
                 cell)
        before = {m.cell for m in mine}
        self.game.click(*clear_point(frame.grid, cell, fighters, raised=self.map.raised, lift=self.map.lift))
        self.game.sleep(STEP_PROOF_WAIT)
        moved = [f for f in find_fighters(self.game.look()) if f.team == team and f.cell not in before]
        if len(moved) != 1:
            log.info("Le pas n'a rien prouvé (%d cercle(s) de ma couleur sur une nouvelle case)", len(moved))
            return None
        log.info("Repéré par mon pas : moi en %s", moved[0].cell)
        return moved[0]

    def _locate_by_hover(self, mine: list[Fighter]) -> Fighter | None:
        """Moi parmi les cercles de ma couleur : celui dont la fiche, au survol, porte mon nom (perception/hp.py,
        read_hovered_name). Le plus proche de ma dernière case connue d'abord. En arrière-plan, le survol n'a encore
        jamais montré de fiche (08/10 : ni infobulle de groupe, ni effet sur un bouton) : après HOVER_MISSES survols
        sans fiche, sans qu'une ait jamais paru, il ne sert plus de la session. None si aucun nom lu n'est le mien."""
        hover = getattr(self.game, "hover", None)
        if self.game_background:
            # 20/10 : deux survols en combat, aucune fiche ; 08/10 (tools/arriere_plan.py) : aucun effet de survol non
            # plus. Le message « souris ici » seul suffit à écarter la souris, pas à montrer une fiche. Une souris
            # posée par un clic, elle, la montre (20/10 : clic de déplacement, personnage arrivé dessous, sa fiche et
            # son nom lus ; tests/fixtures/fiche_pesopepe_arriere_plan.png).
            return None
        if not self.name or hover is None or not mine or (not self._hover_seen and self._hover_misses >= HOVER_MISSES):
            return None
        last = self._me_cell
        found = None
        for circle in sorted(mine, key=lambda f: distance(f.cell, last) if last is not None else 0):
            hover((circle.x, circle.y))
            self.game.sleep(HOVER_SHEET)
            shown = self.game.look(park=False)
            if not sheet_visible(shown):
                self._hover_misses += 1
                if not self._hover_seen:
                    if self._hover_misses >= HOVER_MISSES:
                        path = self.game.recorder.save(shown.image, "survol_sans_fiche", lossless=True)
                        log.info("Survol d'un cercle : aucune fiche affichée (capture : %s) ; je ne me repère plus au "
                                 "survol pendant cette session", path)
                    break  # pas de fiche pour celui-ci : pas pour les autres non plus
                continue
            self._hover_seen = True
            read = read_hovered_name(shown)
            if same_name(read, self.name):
                log.info("Moi : le cercle en %s (mon nom sur sa fiche, au survol)", circle.cell)
                found = circle
                break
            log.debug("Cercle en %s : fiche de %s", circle.cell, read or "?")
        self._mouse_away()
        if found is None:
            log.info("Qui est moi parmi %d cercles de ma couleur ? Mon nom n'est sur aucune fiche lue au survol",
                     len(mine))
        return found

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
        """PA de ce tour, lus dans la bulle bleue (moins que le maximum si un monstre en a retiré) ; écoute du
        serveur : ceux qu'il annonce (GTM au début du tour)."""
        net = self._net()
        state = net.my_state() if net is not None else None
        if state is not None and state.pa > 0:
            if state.pa != self._max_pa:
                log.info("%d PA ce tour (au lieu de %d, d'après le jeu)", state.pa, self._max_pa)
            return state.pa
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
        net = self._net()
        state = net.my_state() if net is not None else None
        pa = state.pa if state is not None else read_pa(frame)
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
        self._bar_turn = self._use.turn  # sorts grisés lus ce tour (soupçon de fenêtre sourde : _deaf_suspect)
        if greyed != self._use.greyed:
            log.debug("Sorts grisés dans la barre : %s",
                      ", ".join(s.name for s in self.spells if s.spell_id in greyed) or "aucun")
        self._use.greyed = greyed

    def _resistances(self, enemies: list[Fighter]) -> dict[Cell, dict[str, int]]:
        """Résistances des ennemis, par case (08/11) : modèle et grade de chaque monstre d'après le serveur (GM|+ du
        combat), résistances de la langue du client (brain/monsters.py). {} sans écoute."""
        from bot.brain.monsters import resistances

        net = self._net() if not self._watching else None
        if net is None:
            return {}
        cells = {e.cell for e in enemies}
        out: dict[Cell, dict[str, int]] = {}
        for who, cell, _, _ in net.fighters():
            actor = net.actors.get(who) if who < 0 and cell in cells else None
            if actor is None or not str(actor.name).isdigit():
                continue
            found = resistances(int(actor.name), getattr(actor, "grade", None))
            if found:
                out[cell] = found
        return out

    def _plan(self, frame: Frame, me: Fighter, enemies: list[Fighter], allies: list[Fighter], pm: int,
              avoid: set[Cell] = frozenset(), missed: set[Cell] = frozenset(), can_move: bool = True,
              move_first: bool = True) -> TurnPlan:
        """Plan du moteur depuis l'état lu sur cette capture (PA suivis et lus, PM `pm`, terrain connu).

        `avoid` : cases déjà occupées ce tour (pas d'aller-retour avant de lancer) ; `missed` : destinations pas
        atteintes ce tour."""
        cells = set(frame.grid.cells())
        board = Board(frozenset(cells), frozenset(self.map.unwalkable),
                      frozenset(self.map.obstacles) if self.map.known else None,
                      reliable=self._trusted_map, margin=RELIEF_MARGIN, unseen=frozenset(self.map.unseen(cells)))
        self._board = board
        started = time.perf_counter()
        spells = [s for s in self.spells if s.spell_id not in self._banned]
        # Désinvocation (01/11, l'utilisateur) : tuer l'invocateur tue ses invocations ; elle ne sert que sur une
        # invocation, et seulement quand aucun autre sort ne touche personne ce tour.
        last_resort = [s for s in spells if s.summons_only]
        spells = [s for s in spells if not s.summons_only]

        def planned(spells: list[CombatSpell], summons: frozenset[Cell] = frozenset()) -> TurnPlan:
            return plan_turn(me.cell, pm, [e.cell for e in enemies], [a.cell for a in allies], spells, self._use,
                             board, self.settings.weights, avoid=avoid, missed=missed, can_move=can_move,
                             move_first=move_first, life=self.map.life, names=self._names, summons=summons,
                             resist=resist)

        resist = self._resistances(enemies)

        plan = planned(spells)
        summons = self._summon_cells() & {e.cell for e in enemies} if last_resort else frozenset()
        if summons and not any(step.hits for step in plan.casts):
            other = planned(spells + last_resort, frozenset(summons))
            if any(step.spell is not None and step.spell.summons_only for step in other.casts):
                log.info("Aucun autre sort ne touche : %s sur une invocation", last_resort[0].name)
                plan = other
        log.debug("Plan calculé en %.0f ms (%d suites de sorts, %d PA, %d PM)", 1000 * (time.perf_counter() - started),
                  plan.sequences, self._use.pa, pm)
        return plan

    def _summon_cells(self) -> frozenset[Cell]:
        """Cases des invocations du combat d'après le serveur (« GA;181 ») ; vide sans écoute."""
        net = self._net()
        if net is None:
            return frozenset()
        return frozenset(cell for _, cell, _, state in net.fighters() if getattr(state, "summoned", False))

    @property
    def _trusted_map(self) -> bool:
        """Mes cases et distances valent : carte crue sans relief, ou carte officielle reconnue (même reconnue après un
        passage en « carte à relief » : la carte est partagée par l'équipe, la reconnaissance peut venir d'un autre)."""
        return self._map_reliable or self.map.arena is not None

    def _spell_fits(self) -> bool:
        """Un sort d'attaque tient encore dans mes PA ce tour (sinon, pas de plan d'ailleurs : rien à y lancer)."""
        use = self._use
        return any(s.damage > 0 and not s.on_self and s.pa <= use.pa and s.spell_id not in self._banned
                   and s.spell_id not in use.unavailable and s.spell_id not in use.greyed for s in self.spells)

    def _pm_left(self, frame: Frame, pm_left: int) -> int:
        """PM restants : ceux de la bulle verte, sans dépasser `pm_left` (PM du début du tour moins ceux dépensés) :
        juste après un déplacement, la bulle montre parfois encore l'ancien nombre (01/10 : un déplacement de 2 PM
        planifié avec 0 PM, 2 à 3 s perdues). `pm_left` seul si la bulle est illisible.
        31/10 : écoute du serveur d'abord, ses PM font foi (GTM au début du tour, puis GA;129 dépensés et GA;127
        perdus : tacle, sort d'un ennemi) ; la bulle seulement sans écoute ou mon état pas annoncé."""
        net = self._net()
        state = net.my_state() if net is not None else None
        if state is not None and (state.max_hp or state.hp):  # GTM reçu pour moi (sinon PM à 0 par défaut)
            return max(state.pm, 0)
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
        if self.spellbook is None or not self.spellbook.entries:
            return
        # Cases de la barre déjà connues (01/11 : annoncées par le serveur à la connexion) : les sorts de la tactique
        # sont pris quand même, une fois (le 01/11, 0 sort par tour, « Sorts inconnus » : ils n'étaient jamais pris).
        if self.spellbook.slots and self._spellbook_used:
            # Caractéristiques annoncées par le serveur après le premier tour (27/10 : « As » envoyé en combat) :
            # dégâts des sorts recalculés, mêmes sorts (ceux écartés en route le restent).
            if getattr(self.spellbook, "refresh_stats", lambda: False)():
                fresh = {s.spell_id: s for s in self.spellbook.combat_spells()}
                self._spells = [fresh.get(s.spell_id, s) for s in self._spells]
            return
        self._spellbook_used = True
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

    @property
    def _bar_unknown(self) -> bool:
        """Sorts lus dans la fenêtre des sorts, mais aucun dans la barre : sorts changés (transformation)."""
        return self._bar_changed or (self.spellbook is not None and bool(self.spellbook.entries) and self._fallback_only)

    @property
    def _fallback_only(self) -> bool:
        """Aucun sort connu : seul le sort de la case configurée (voir spells)."""
        return all(s.spell_id == FALLBACK_SPELL for s in self._spells)

    def _learn_blocks(self, frame: Frame, fighters: list[Fighter]) -> None:
        """Blocs et trous lus sur la carte propre (mode tactique, sans portée affichée) : MapMemory.learn_terrain.
        Carte du client connue (numéro et clé du GDM) : ses blocs et trous font foi, rien à lire (01/11 : « Carte hors
        mode tactique : obstacles inconnus » alors que la carte du client était adoptée)."""
        if self.map.arena is None:
            self._adopt_client_arena()
        if getattr(self.map.arena, "from_client", False):
            return
        if not tactical_mode(frame):
            if not self._warned_not_tactical:
                self._warned_not_tactical = True
                log.warning("Carte hors mode tactique : obstacles inconnus, déplacements estimés sans eux")
        elif not spell_range_visible(frame):
            terrain = read_terrain(frame, fighters)
            self._recognize_arena(terrain=terrain)
            self.map.learn_terrain(terrain, fighters)

    def _recognize_arena(self, terrain: Terrain | None = None, placed: set[Cell] | None = None) -> None:
        """Carte officielle du combat (brain/arena.py), reconnue au terrain lu ou aux cases de placement ; ARENA_TRIES
        essais par combat (la reconnaissance compare toutes les cartes du jeu)."""
        if self.map.arena is not None or self.map.arena_tries >= ARENA_TRIES:
            return
        # 31/10 : la carte du client d'abord (numéro et clé du GDM, brain/mapdata.py ; 60 cartes sur 60 d'accord avec
        # les références, et juste aussi pour les cartes changées depuis) ; la reconnaissance par les références en
        # secours.
        if self._adopt_client_arena():
            return
        arenas = Arenas.load()
        if not len(arenas):
            return
        self.map.arena_tries += 1
        arena = arenas.by_terrain(terrain) if terrain is not None else arenas.by_placement(placed or set())
        if arena is not None:
            # Carte reconnue par la référence, sans GDM entendu (1er combat de la session) : sa carte du client si sa
            # clé est retenue (01/11 : la référence compte les arbres comme du sol).
            from bot.brain.arena import client_arena

            told = client_arena(int(arena.ident)) if arena.ident.isdigit() else None
            if told is not None:
                self.map.adopt(told)
                log.info("Carte du combat reconnue %s : %s ; blocs et trous d'après la carte du client (%d, %d)",
                         "au terrain" if terrain is not None else "au placement", told.coords, len(told.blocks),
                         len(told.holes))
                self._check_placements()
                return
            self.map.adopt(arena)
            log.info("Carte du combat reconnue %s : %s (référence officielle : %d blocs, %d trous) ; obstacles et "
                     "lignes de vue d'après le jeu", "au terrain" if terrain is not None else "au placement",
                     arena.coords, len(arena.blocks), len(arena.holes))
            self._check_placements()

    def _adopt_client_arena(self) -> bool:
        """Carte du combat d'après la carte du client (numéro et clé annoncés par le serveur) ; True si adoptée."""
        from bot.brain.arena import fight_arena

        arena = fight_arena(getattr(self.game, "net", None))
        if arena is None:
            return False
        self.map.adopt(arena)
        log.info("Carte du combat d'après la carte du client : %s (%d blocs, %d trous)", arena.coords, len(arena.blocks),
                 len(arena.holes))
        self._check_placements()
        return True

    def _check_placements(self) -> None:
        """Contrôle en jeu de la référence (24/10, utilisateur) : les cases de placement lues sont-elles celles de la
        carte officielle (StarLoco) ? Une case hors de la référence est gardée en capture. Une fois par combat."""
        placed, frame = self.map.placed
        arena = self.map.arena
        if arena is None or not arena.placements or not placed or self.map.placement_checked:
            return
        self.map.placement_checked = True
        official = frozenset().union(*arena.placements)
        outside = placed - official
        if outside:
            path = self.game.recorder.save(frame.image, "placement_hors_reference", lossless=True)
            log.warning("Placement : %d case(s) lue(s) hors des placements de la référence en %s : %s (capture : %s)",
                        len(outside), arena.coords, sorted(outside), path)
        else:
            log.info("Placement : les %d cases lues sont parmi les %d de la référence en %s", len(placed),
                     len(official), arena.coords)

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

    def _clicked_on(self, cell: Cell) -> list[Point]:
        """Points cliqués ce tour sur `cell` (liste à compléter), oubliés au tour suivant."""
        turn, clicks = self._clicks
        if turn != self._use.turn:
            clicks = {}
            self._clicks = (self._use.turn, clicks)
        return clicks.setdefault(cell, [])

    def _transformed_turn(self, frame: Frame, fighters: list[Fighter], my_team: str, known: Cell | None = None) -> bool:
        """Aucun sort connu ne se sélectionne (23/10, Contour d'Astrub : l'équipe changée en Bouftous d'Halouine par
        l'événement d'Halloween, autres sorts dans les cases 1 à 3 ; 40 min de combat sans un coup) : les touches 1 à 3
        essayées ; la portée affichée dit qui je suis (la taille dont le losange, centré sur un de mes cercles, colle le
        mieux) et qui est à portée ; l'ennemi le plus proche est frappé, PA vérifiés. True si un sort est parti."""
        mine = {f.cell for f in fighters if f.team == my_team}
        enemies = [f for f in fighters if f.team and f.team != my_team]
        if not mine or not enemies:
            return False
        for key in TRANSFORMED_KEYS:
            if read_scene(self.game.look()).phase is not Phase.COMBAT:
                return False  # combat fini : une touche de la barre des objets boirait une potion (25/10)
            self.game.press(key)
            shown = self._range_frame()
            if shown is None:
                continue
            best = (1.0, known) if known is not None else None
            for high in (range(1, TRANSFORMED_MAX_RANGE + 1) if known is None else ()):
                for low in (0, 1):
                    center, score = range_fit(shown, (low, high), self.map.unwalkable, False)
                    if center in mine and score >= 0.5 and (best is None or score > best[0]):
                        best = (score, center)
            if best is None and self._blind_cast(key, enemies, fighters, mine):
                return True
            if best is None:
                path = self.game.recorder.save(shown.image, "transformation_portee", lossless=True)
                log.info("Sort de la case %s (transformation) : portée affichée, centre sur aucun de mes cercles "
                         "(capture : %s)", key, path)
                self.game.press(self.settings.cancel_key)
                continue
            me = Fighter(my_team, *cell_point(shown.grid, best[1], self.map.lift), best[1])
            view = read_range(shown, me, fighters)
            low, high = view.spell_range or (1, 1)
            targets = [e for e in enemies if low <= distance(e.cell, me.cell) <= high and (
                e.cell in view.castable
                or any((e.cell[0] + dx, e.cell[1] + dy) in view.castable for dx in (-1, 1) for dy in (-1, 1)))]
            if not targets:
                # Portée cachée près de l'ennemi (24/10 : Piou en (2, 2), sous le nom de la carte) : à la bonne
                # distance, le clic est essayé quand même, PA vérifiés (le jeu tranche).
                targets = [e for e in enemies if low <= distance(e.cell, me.cell) <= high]
            if not targets:
                self.game.press(self.settings.cancel_key)
                self._step_toward(shown, me, enemies, fighters, key, high)
                return False
            target = min(targets, key=lambda e: distance(e.cell, me.cell))
            pa = self._pa_now(shown)
            point, spread = clear_point(shown.grid, target.cell, fighters, raised=self.map.raised,
                                        lift=self.map.lift)
            log.info("Sorts inconnus (transformation ?) : moi en %s d'après la portée de la case %s, je frappe %s",
                     me.cell, key, _aim_label(target))
            self.game.move(point, spread)
            self.game.click()
            self._mouse_away()
            self.game.sleep(1.2)
            after = self._pa_now(self.game.look())
            if pa is not None and after is not None and after < pa:
                return True
        return False

    def _blind_cast(self, key: str, enemies: list[Fighter], fighters: list[Fighter], mine: set[Cell]) -> bool:
        """Sort `key` sélectionné, ma case inconnue (24/10, Astrub 4,-20 : citrouilles toutes pareilles, la portée
        cachée sous les cercles des alliés) : le jeu tranche. Un clic sur chaque ennemi, le plus entouré des miens
        d'abord ; des PA dépensés = sort parti. Jamais un clic ailleurs que sur un ennemi : aucun allié touché."""
        def around(target: Fighter) -> int:
            return sum(distance(target.cell, c) <= 1 for c in mine)

        for n, target in enumerate(sorted(enemies, key=lambda e: (-around(e), e.cell))):
            if n:
                if read_scene(self.game.look(park=False)).phase is not Phase.COMBAT:
                    return False  # combat fini : la touche viserait la barre des objets (25/10)
                self.game.press(key)  # un clic hors de portée a désélectionné le sort
                self.game.sleep(0.3)
            frame = self.game.look()
            pa = self._pa_now(frame)
            if pa is None or pa <= 0:
                break
            point, spread = clear_point(frame.grid, target.cell, fighters, raised=self.map.raised,
                                        lift=self.map.lift)
            self.game.move(point, spread)
            self.game.click()
            self._mouse_away()
            self.game.sleep(1.2)
            after = self._pa_now(self.game.look())
            if after is not None and after < pa:
                log.info("Sorts inconnus (transformation ?) : case %s sur %s, ma case inconnue : le sort est parti",
                         key, _aim_label(target))
                return True
        self.game.press(self.settings.cancel_key)
        return False

    def _step_toward(self, frame: Frame, me: Fighter, enemies: list[Fighter], fighters: list[Fighter], key: str,
                     reach: int) -> None:
        """Sorts inconnus, aucun ennemi à portée : quelques pas vers le plus proche (case libre la plus proche de lui,
        à portée de mes PM), pour le frapper au tour suivant."""
        pm = read_pm(frame) or 0
        if pm <= 0:
            return
        target = min(enemies, key=lambda e: distance(e.cell, me.cell))
        taken = {f.cell for f in fighters} | set(self.map.unwalkable)
        cells = [c for c in frame.grid.cells() if c not in taken and 0 < distance(c, me.cell) <= pm]
        if not cells:
            return
        cell = min(cells, key=lambda c: (max(0, distance(c, target.cell) - reach), distance(c, me.cell)))
        log.info("Sort de la case %s (transformation) : aucun ennemi à portée (%d case(s)), je m'avance en %s vers %s",
                 key, reach, cell, _aim_label(target))
        self.game.click(*clear_point(frame.grid, cell, fighters, raised=self.map.raised, lift=self.map.lift))
        self.game.sleep(1.5)

    def _move_timeline(self, frame: Frame) -> bool:
        """Moi ou les ennemis introuvables alors que des portraits de la timeline couvrent la carte : la timeline passe
        de l'autre côté (▲ / ▼ de son onglet orange), une fois par tour. 23/10, Tainéla 5,-30 : agression dans le coin
        bas-droit, l'équipe et les monstres sous les portraits ; tours passés sans un sort, 4 morts."""
        if self._timeline_moved == self._use.turn or not timeline_portraits(frame):
            return False
        self._timeline_moved = self._use.turn
        top = timeline_at_top(frame)
        log.info("Combattants introuvables, portraits de la timeline par-dessus la carte : je la mets %s",
                 "en bas" if top else "en haut")
        self.game.click(frame.layout.point(*(TIMELINE_DOWN if top else TIMELINE_UP)), frame.layout.spread(2, 2))
        self.game.sleep(0.5)
        self._no_proof = False
        return True

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
        if getattr(self.spellbook, "numpad_known", lambda: False)():
            # 27/10 : l'essai (sort sélectionné puis Échap) refait à chaque session ressemblait à un repérage ; une
            # touche qui ne marche plus est quand même vue au moment de s'en servir (_numpad_missed).
            self._numpad_checked = True
            log.debug("Pavé numérique : la 2e rangée a marché à une session précédente, pas de nouvel essai")
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
            getattr(self.spellbook, "remember_numpad", lambda: None)()
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
        relance...). Clic seulement pour une case sans raccourci (bouton à gauche de la barre, combat.spell.key ""),
        ou quand la fenêtre est sourde aux touches ce tour (_deaf_key)."""
        if not self._bar_shows(frame, spell):
            return None
        key = self._slot_key(spell.slot)
        if key and not self._keys_deaf:
            self.game.press(key)
        else:
            self.game.click(spell_slot_point(frame.layout, spell.slot), frame.layout.spread(*SPELL_SLOT_SPREAD))
        shown = self._range_frame(watch=self._watching)
        if shown is None and key and not self._keys_deaf and self._deaf_suspect(spell, key):
            shown = self._deaf_key(frame, spell, key)
        return shown

    def _deaf_suspect(self, spell: CombatSpell, key: str) -> bool:
        """Pas de portée après la touche d'un sort qui devrait se sélectionner : ses PA, pas grisé dans la barre lue CE
        tour, une touche ordinaire (pas le pavé numérique de l'arrière-plan, qui peut ne pas être réglé). Une fois par
        tour au plus. Juste après un lancer (animation), c'est attendu : pas de soupçon."""
        return (not self._deaf_checked and not key.startswith("num") and spell in self.spells
                and self._bar_turn == self._use.turn and spell.pa <= self._use.pa
                and spell.spell_id not in self._use.greyed and spell.spell_id not in self._use.unavailable
                and time.monotonic() - self._cast_at >= RESELECT_AFTER_CAST)

    def _deaf_key(self, frame: Frame, spell: CombatSpell, key: str) -> Frame | None:
        """20/10 (S3, S4, S6, S8) : la fenêtre de Koalaloupour ignorait les chiffres des sorts, les clics passaient.
        Clic de déblocage sur le fond beige (brain/recovery.py), la touche de nouveau, puis un clic sur la case du
        sort. Portée affichée au clic seulement : fenêtre sourde, les sorts se cliquent pour le reste du tour (et F1
        devient un clic sur le bouton : _pass_turn)."""
        self._deaf_checked = True
        self.game.sleep(RANGE_LATE)
        late = self.game.look(park=False)
        if spell_range_visible(late):
            log.debug("Portée de %s affichée %.2f s après sa touche : lente, pas une fenêtre sourde", spell.name,
                      RANGE_SHOWN + RANGE_LATE)
            return late
        if not self._foes_standing(late):
            # 25/10 (journaux 153121 à 181212) : 29 clics de déblocage, 25 suivis d'un clic sur la case du sort sans rien
            # sélectionner, aucune fenêtre sourde trouvée ainsi ; 12 suivis aussitôt de « hors combat » (le sort d'avant
            # venait de tuer le dernier ennemi : Pesopepe 18:54:21, Walitcoin 16:00:56). Plus d'ennemi debout :
            # pas de clic, ni sur le fond ni sur la barre.
            log.info("%s pas sélectionné et plus aucun ennemi debout (combat fini ?) : pas de clic de déblocage",
                     spell.name)
            return None
        what = f"touche {key.upper()} de {spell.name}"
        unlocked = unlock_click(self.game, frame)
        if not self._press_spell_key(key, spell):
            return None
        shown = self._range_frame(watch=self._watching)
        if shown is not None:
            mark_heard(self.game)
            report_episode(what, True, "" if unlocked else "sans clic possible : la touche a pris au 2e appui")
            return shown
        # 25/10 (journal 181212) : 1 à 2 s depuis la vérification de la barre ; combat fini entre-temps, clic sur la case
        # hors combat. Barre revue juste avant le clic (une potion de la barre se boit d'un clic).
        if not self._bar_shows(self.game.look(park=False), spell):
            return None
        self.game.click(spell_slot_point(frame.layout, spell.slot), frame.layout.spread(*SPELL_SLOT_SPREAD))
        shown = self._range_frame(watch=self._watching)
        if shown is None:
            log.info("%s pas sélectionné, ni par sa touche ni par un clic sur sa case : pas une touche ignorée", spell.name)
            return None
        self._keys_deaf = True
        mark_deaf(self.game, f"{what} ignorée, le clic sur sa case l'a sélectionné")
        report_episode(what, False, "sélectionné par un clic sur sa case : sorts cliqués jusqu'à la fin du tour")
        return shown

    def _foes_standing(self, frame: Frame) -> bool:
        """Toujours en combat, et au moins un cercle d'une autre couleur que la mienne sur la carte. Vrai si ma couleur
        n'est pas connue (rien à conclure)."""
        if read_scene(frame).phase is not Phase.COMBAT:
            return False
        foes = self._told_foes()
        if foes is not None:
            return bool(foes)  # d'après le serveur (31/10) : ni cercle caché ni couleur lue au mauvais moment
        if not self._team:
            return True
        return any(f.team and f.team != self._team for f in find_fighters(frame))

    @property
    def _client_sight(self) -> bool:
        """Ligne de vue calculée, sans lire la portée affichée (31/10, utilisateur : en priorité) : carte du combat
        d'après la carte du client (blocs et cases sans ligne de vue de la version du jeu) et combattants d'après le
        serveur. Le 31/10, la prévision était juste sur 99 à 100 % des cases à portée affichées."""
        arena = self.map.arena
        return arena is not None and getattr(arena, "from_client", False) and self._told_foes() is not None

    def _sight_blockers(self, me: Fighter, target: Fighter) -> set[Cell]:
        """Ce qui coupe la ligne de vue de `me` à `target` : obstacles de la carte et combattants debout (serveur)."""
        net = self._net()
        standing = {cell for _, cell, _, _ in net.fighters()} if net is not None else set()
        return (set(self.map.obstacles) | standing) - {me.cell, target.cell}

    def _no_sight_shown(self, me: Fighter, target: Fighter, spell: CombatSpell) -> bool:
        """La portée affichée par le jeu dit que la cible est sans ligne de vue : sa case en bleu clair, ou (case cachée
        par son corps) toutes ses voisines de mon côté en bleu clair. 23/10, Porkass, carte à relief (ligne de vue
        prévue juste à 56 %) : Ronce refusée 3 fois d'affilée, ~2 s chacune, sur des ennemis dont les cases voisines
        étaient toutes sans ligne de vue. Carte du client connue : la ligne de vue calculée, sans regarder l'écran."""
        if spell.on_self:
            return False
        if self._client_sight:
            return spell.needs_sight and not line_of_sight(me.cell, target.cell, self._sight_blockers(me, target))
        if self._shown_view is None or self._shown_view[:2] != (me.cell, spell.spell_id) or spell.on_self:
            return False
        view, cell = self._shown_view[2], target.cell
        if cell in view.castable:
            return False
        if cell in view.no_sight:
            return True
        toward = [(cell[0] + dx, cell[1] + dy) for dx in (-1, 1) for dy in (-1, 1)]
        toward = [n for n in toward if distance(n, me.cell) < distance(cell, me.cell)]
        known = [n for n in toward if n in view.castable or n in view.no_sight]
        return bool(known) and all(n in view.no_sight for n in known)

    def _learn_range(self, shown: Frame, me: Fighter, fighters: list[Fighter], spell: CombatSpell) -> None:
        """Portée du sort et obstacles, lus sur les cases colorées (mode tactique seulement). Carte du client connue :
        rien à lire, la ligne de vue se calcule (_client_sight)."""
        if self._client_sight or not tactical_mode(shown):
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
        self._shown_view = (me.cell, spell.spell_id, view)
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
        if not spell.needs_sight:
            # Sort sans ligne de vue (Roulage de Pelle, Flèche Harcelante) : le jeu colore en bleu foncé toutes ses cases
            # à portée, même derrière un bloc ; les comparer à la ligne de vue prédite ne dit rien. 25/10 (journaux
            # 153121 à 181212) : 21 des 27 passages en « carte à relief » venaient de ces deux sorts (accord 50 à 86 %,
            # 32 captures « ligne_de_vue_a_revoir » relues avec la carte officielle) ; ensuite 40 % des
            # clics refusés (41 sur 103 : tirs au-delà de la portée, marge de 4 cases) contre 4 % avant (21 sur 579).
            # (Pour les sorts à ligne de vue, sur ces captures : 4 cases sur 1757 vues bloquées alors que prédites dégagées.)
            log.debug("Portée de %s (sans ligne de vue) : pas de contrôle de la ligne de vue prédite", spell.name)
            return
        blockers = self.map.obstacles | ({f.cell for f in fighters} - {me.cell})
        agreement = sight_agreement(view, me.cell, blockers)
        log.debug("Obstacles connus : %d ; ligne de vue prédite juste sur %.0f %% des cases à portée",
                  len(self.map.obstacles), 100 * agreement)
        # 15/10 : sorts en ligne, 7 à 10 cases colorées : UNE case fausse (sous l'animation du sort, ombre d'un mur)
        # donnait 86-90 % et tout le combat passait en « carte à relief ». Il en faut au moins deux.
        shown_cells = len(view.castable | view.no_sight)
        wrong = round((1 - agreement) * shown_cells) if shown_cells else 0
        if agreement < 0.9 and wrong >= RELIEF_MIN_WRONG and self._map_reliable:
            if self.map.arena is not None:
                # Carte officielle reconnue (brain/arena.py) : ses cases font foi, mes distances sont justes. Les écarts
                # viennent de l'écran (25/10 : animation du sort d'avant sur la portée, cercle d'un ennemi qui vient de
                # mourir) : pas de « carte à relief », la portée reste vérifiée avant le clic.
                log.info("Ligne de vue prédite juste sur %.0f %% des cases seulement, mais carte officielle reconnue : "
                         "je garde mes prévisions", 100 * agreement)
            else:
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
        if self._keys_deaf and self._range_frame() is not None:
            # Fenêtre sourde ce tour (_deaf_key) : clic de déblocage, puis Échap de nouveau.
            unlock_click(self.game)
            self.game.press(self.settings.cancel_key)
        if self._range_frame() is not None:
            self._spell_stuck = True  # le tour s'arrête là (_play_turn) : aucun clic sur la carte, il lancerait le sort
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

    def _check_cast_spell(self, net, spell: CombatSpell) -> None:
        """Le sort parti d'après le serveur (GA;300) est-il celui de la case pressée ? Contrôle des cases de la barre
        annoncées à la connexion (01/11 : chez Fourboun, l'écran et le serveur ne plaçaient pas La Bloqueuse dans la
        même case). Dit une fois par sort."""
        told = getattr(net, "cast_spell", None) if getattr(net, "cast_at", 0.0) >= self._clicked_at else None
        sorts = (getattr(self.spellbook, "reference", None) or {}).get("sorts") or {}
        expected = next((int(k) for k, s in sorts.items() if s.get("cle") == spell.spell_id and k.isdigit()), None)
        if told is None or expected is None or (spell.spell_id, told) in self._cast_checked:
            return
        self._cast_checked.add((spell.spell_id, told))
        if told == expected:
            log.info("%s : case %d confirmée par le serveur", spell.name, spell.slot)
        else:
            log.warning("%s (case %d) : le serveur annonce le sort n°%d (%s) : case de la barre fausse", spell.name,
                        spell.slot, told, (sorts.get(str(told)) or {}).get("nom", "inconnu"))

    def _confirm_cast(self, before: Frame, my_team: str, spell: CombatSpell) -> Frame | None:
        """Juste après le clic qui lance le sort : None s'il est parti (ou si le tour / le combat est fini), la capture
        qui l'a montré gardée dans `_sent` (point de départ d'une rafale) ; sinon la dernière capture.

        Fixe : after_cast s (début de l'animation), puis une capture toutes les 0,15 s pendant cast_confirm s.
        Condition : captures dès le clic, jusqu'à voir les PA ou l'icône changer ; after_cast + cast_confirm s au
        plus, la même fenêtre d'observation (un sort lent à se confirmer n'est pas relancé pour autant)."""

        how = "PA ou fin du tour"

        def gone(frame: Frame) -> bool:
            return not self._my_turn(frame, my_team) or self._was_cast(before, frame, spell)

        clicked = time.monotonic()
        window = self.settings.after_cast + self.settings.cast_confirm
        self._chain_chat = None
        net = self._net() if not self._watching else None
        if net is not None:
            # PA dépensés annoncés par le serveur (GA;102) : parti, ~0,2 s après le clic ; rien en NET_REFUSED s :
            # refusé. Le chat d'avant le clic est gardé pour l'enchaînement (_effect_in_chat : fin de l'animation).
            self._chain_chat = self._chat_before(before)
            spent = net.wait_for("spent_at", self._clicked_at, NET_REFUSED)
            after = self.game.look()
            stats = self._turn_stats()
            # Sans PA dépensés annoncés, seul un tour fini compte : des PA baissés à l'écran peuvent être ceux du sort
            # précédent (01/11 : Tir Éloigné ignoré cru parti, +4 de portée supposés, 5 refus au tour suivant).
            if spent is None and self._my_turn(after, my_team):
                log.debug("%s : pas de PA dépensés d'après le jeu en %.1f s : refusé", spell.name, NET_REFUSED)
                stats.missed_clicks += 1
                return after
            if net.failed_at >= self._clicked_at:
                # PA perdus sans effet ; si ce sort finit le tour sur un échec critique, le serveur passe la main
                # (GTF) et mon tour s'arrête là (_my_turn).
                log.info("%s : échec critique (d'après le jeu), PA perdus sans effet", spell.name)
            self._check_cast_spell(net, spell)
            self._served_at, self._served_spell = (spent if spent is not None else time.monotonic()), spell
            log.debug("%s : parti en %.2f s (d'après le jeu)", spell.name, self._served_at - self._clicked_at)
            stats.casts += 1
            self._sent = after
            return None
        if self._watching:
            shown, after = self._watch("sort parti", gone, window)
            sent = shown is not None
            after = shown or after
        else:
            chat = self._chat_before(before)
            self._chain_chat = chat
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
                grace = CHAT_PA_GRACE_BACKGROUND if self.game_background else CHAT_PA_GRACE
                if announced_at is not None and elapsed >= announced_at + grace:
                    sent, how = True, "chat"  # parti ; PA pas encore à jour : la capture d'après les montrera
                    break
                if check < checks - 1:
                    self.game.sleep(CONFIRM_EVERY)
        stats = self._turn_stats()
        # 11/10 : en arrière-plan, un sort réussi semblait mettre ~4 s à se confirmer, un refus en coûte 5 : mesuré ici
        # pour régler l'attente (P13 du rapport des tests de combat).
        log.debug("%s : %s en %.1f s", spell.name, "parti" if sent else "pas parti", time.monotonic() - clicked)
        if sent:
            log.debug("%s : vu parti par %s", spell.name, how)
        if sent:
            stats.casts += 1
            self._sent = after
            return None
        stats.missed_clicks += 1
        return after

    def _chain_wait(self, spell: CombatSpell) -> float | None:
        """Avant le clic d'un sort, en arrière-plan : au moins CHAIN_GAP s après le clic du dernier sort lancé sur une
        cible ce tour (son animation finie, sinon le jeu ignore le clic). Au premier plan, les clics pendant l'animation
        passent (rafales du 04/10, 22 sur 22) : pas d'attente. Rend l'écart d'avant l'attente (None : premier sort)."""
        gap = time.monotonic() - self._target_cast_at
        if gap == float("inf"):
            return None
        if gap < NET_CHAIN_FIRST and self.game_background and self._net() is not None:
            log.debug("%s : %.1f s après le sort précédent, je tente à %.1f s (écoute du serveur)", spell.name, gap,
                      NET_CHAIN_FIRST)
            self.game.sleep(NET_CHAIN_FIRST - gap)
        elif gap < CHAIN_GAP and self.game_background and self._net() is None:
            seen = self._effect_in_chat(self._target_cast_at + CHAIN_GAP)
            if seen is not None:
                log.debug("%s : effet de %s lu dans le chat %.1f s après son clic, je lance", spell.name,
                          self._target_spell, seen)
                self.game.sleep(CHAIN_CHAT_MARGIN)
            else:
                gap = time.monotonic() - self._target_cast_at
                log.debug("%s : j'attends la fin de l'animation du sort précédent (%.1f s après son clic, %.1f s au "
                          "moins)", spell.name, gap, CHAIN_GAP)
                self.game.sleep(max(0.0, CHAIN_GAP - gap))
        return gap

    def _animation_retry(self, previous: float, target: Fighter, after: Frame) -> bool:
        """Clic sans réponse du serveur, sans doute pendant l'animation du sort précédent : on reclique si l'écoute est
        là, moins de CHAIN_GAP s après le précédent, le sort encore sélectionné (portée affichée) et la cible encore
        debout d'après le serveur (une case vide recevrait un déplacement)."""
        net = self._net()
        if net is None or not self.game_background or time.monotonic() - previous >= CHAIN_GAP:
            return False
        if not spell_range_visible(after):
            return False
        return not target.team or any(cell == target.cell for _, cell, _, _ in net.fighters())

    def _effect_in_chat(self, deadline: float) -> float | None:
        """Secondes entre le clic du sort précédent et son effet lu dans le chat de combat : une ligne après sa dernière
        annonce « Nom lance Sort. », le chat changé depuis avant son clic. None si rien avant `deadline` (ou pas de chat
        lisible : _chat_before)."""
        before = self._chain_chat
        if before is None or not self._target_spell:
            return None
        wanted = _plain(f"{self._name} lance {self._target_spell}")
        while True:
            lines = combat_lines(self.game.look(park=False)) or []
            announced = [i for i, line in enumerate(lines) if _plain(line).startswith(wanted)]
            if lines != before and announced and announced[-1] < len(lines) - 1:
                return time.monotonic() - self._target_cast_at
            if time.monotonic() + CHAIN_CHAT_EVERY >= deadline:
                return None
            self.game.sleep(CHAIN_CHAT_EVERY)

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
              spell: CombatSpell | None = None) -> Cast:
        """`before` : capture avant de sélectionner le sort (référence des PA). `target` : moi pour un sort sur soi,
        un combattant d'équipe "" pour une zone qui vise une case vide."""
        spell = spell or self.spells[0]
        self._turn_stats().tries += 1
        self._sight_refused = False
        net = self._net() if self.game_background else None
        if net is not None:
            # Rien pendant l'animation du sort précédent (01/11 : Tir Éloigné cliqué 0,15 s après la réponse du serveur
            # à Tir Puissant, ignoré par le jeu) : NET_KEY_AFTER_CAST s après son clic, _animation_wait après sa réponse.
            now = time.monotonic()
            wait = max(NET_KEY_AFTER_CAST - (now - self._cast_at), self._animation_wait() - (now - self._served_at))
            if wait > 0:
                self.game.sleep(wait)
        if spell.on_self:
            return self._cast_on_self(before, me, spell, my_team)
        shown = self._select_spell(before, spell)
        just_cast = time.monotonic() - self._cast_at < RESELECT_AFTER_CAST
        # Ou juste après un déplacement : 19/10, touche pressée 0,5 s après le clic du pas, pendant la marche, refusée
        # avec 4 PA (Pelle Fantomatique écartée du tour, Lancer de Pelle, plus faible, à la place). 25/10 (journaux
        # 153121 à 181212) : « juste après un déplacement » valait pour tout déplacement du tour ; 18 nouveaux essais,
        # touche pressée 1,7 à 11 s après l'ordre de marche, 18 « non sélectionnable » ensuite. Seulement dans les
        # RESELECT_AFTER_MOVE s qui suivent l'arrivée (ou l'échec) du déplacement.
        just_moved = time.monotonic() - self._moved_at < RESELECT_AFTER_MOVE
        if shown is None and (just_cast or just_moved) and spell.pa <= self._use.pa:
            log.info("%s pas sélectionné juste après %s : nouvel essai", spell.name,
                     "un lancer (animation)" if just_cast else "un déplacement")
            self.game.sleep(NET_KEY_RETRY_EVERY if net is not None else RESELECT_PAUSE)
            shown = self._range_frame(watch=self._watching)
            if shown is None:
                # 25/10 (journal 231114, et 19:01 journal 181212) : 2e appui sur num5 pour Flèche d'Immobilisation alors
                # que le sort précédent venait de tuer le dernier Blop ; le jeu a remis la barre sur « Obj. » et la
                # touche a bu la potion de Brâkmar de la case 12 (Elseiso téléporté, deux fois). Pas de nouvel essai
                # sans ennemi debout.
                fresh = self.game.look()
                if self._foes_standing(fresh):
                    shown = self._select_spell(fresh, spell)
                else:
                    log.info("%s : plus aucun ennemi debout (combat fini ?) : pas de nouvel essai", spell.name)
                    return Cast.NO_SPELL
            for _ in range(NET_KEY_RETRIES if net is not None else 0):
                if shown is not None:
                    break
                self.game.sleep(NET_KEY_RETRY_EVERY)
                fresh = self.game.look()
                if not self._foes_standing(fresh):
                    break
                shown = self._select_spell(fresh, spell)
        if shown is None:
            key = self._slot_key(spell.slot)
            log.info("%s non sélectionnable (touche %s) : plus assez de PA, sort en relance ou raccourci différent",
                     spell.name, key or "aucune")
            self.game.recorder.save(self.game.look().image, "sort_non_selectionnable")
            if self._fallback_only and not self._turn_stats().casts:
                self._fallback_refused.add(self._use.turn)  # refusé d'entrée : ni PA dépensés, ni sort lancé ce tour
            if not self._bar_changed and len(self._fallback_refused) >= FALLBACK_REFUSED_TURNS:
                # 24/10, citrouilles d'Halloween : aucun sort connu, le sort de secours (case 3) refusé d'entrée à chaque
                # tour, 30 min sans un coup : sorts changés. (Pas la barre relue : illisible juste après un refus, une
                # fausse transformation vue aux Blops le même jour.)
                self._bar_changed = True
                log.warning("Sort de secours refusé, aucun sort connu (transformation ?) : touches 1 à 3 sur l'ennemi "
                            "pour la suite du combat")
            if key.startswith("num"):
                self._numpad_missed(spell)
            return Cast.NO_SPELL
        self._shown_view = None
        self._learn_range(shown, me, fighters, spell)
        if self._no_sight_shown(me, target, spell):
            log.info("Pas de ligne de vue sur %s d'après la portée affichée par le jeu : pas de clic", _aim_label(target))
            self._sight_refused = True  # ni ce sort ni aucun autre à ligne de vue sur lui d'ici (_play_turn)
            return self._cancel_spell()
        high = spell_range(spell, self._use)[1]  # bonus de portée (Tir Éloigné) compris, dès le tour où il est lancé
        if self._trusted_map and distance(me.cell, target.cell) > high:
            log.info("%s à %d cases, hors de portée (%d max)", _aim_label(target).capitalize(),
                     distance(me.cell, target.cell), high)
            return self._cancel_spell()
        # Pas sur le corps d'un autre combattant debout devant la case : le clic le viserait lui (une zone visant une
        # case vide près d'un allié le toucherait). Ni sur un portrait de la timeline (case presque cachée, 15/10), ni
        # sur une icône de challenge (20/10).
        # Case déjà cliquée ce tour (2e sort sur la même cible) : un autre endroit de la case (utilisateur, 21/10 :
        # « pas exactement au même endroit que le sort d'avant »).
        standing = fighters if me in fighters else fighters + [me]
        hidden = timeline_portraits(before) + challenge_icons(before)
        clicked = self._clicked_on(target.cell)
        point, spread = clear_point(before.grid, target.cell, standing, hidden, away=clicked,
                                    raised=self.map.raised, lift=self.map.lift)
        self._aim = (target.cell, point, spread)  # la rafale vise au même endroit
        for attempt in range(1, CAST_ATTEMPTS + 1):
            aim = self.game.move(point, spread)
            clicked.append(aim)
            if self._forbidden_target(aim):
                log.info("Pas de ligne de vue sur %s", _aim_label(target))
                return self._cancel_spell()
            # Arrière-plan : pas de survol ni de fiche ; la vie annoncée par le serveur suffit (31/10).
            hp = self._read_life(self._hovered, target) if target.team else None
            gap = self._chain_wait(spell)
            log.info("%s lancé sur %s%s%s", spell.name, _aim_label(target), f" ({hp} PV)" if hp is not None else "",
                     f" ({gap:.1f} s après le sort précédent)" if gap is not None else "")
            previous = self._target_cast_at  # clic du sort précédent sur une cible ce tour (-inf : aucun)
            self._clicked_at = time.monotonic()
            self.game.click()  # sur place : la souris est déjà sur la cible vérifiée
            self._mouse_away()
            self._cast_at = self._target_cast_at = time.monotonic()
            self._target_spell = spell.name
            after = self._confirm_cast(before, my_team, spell)
            retries = 0
            while after is not None and retries < NET_CLICK_RETRIES and self._animation_retry(previous, target, after):
                retries += 1
                # Clic ignoré pendant l'animation du sort précédent : le sort est toujours sélectionné, la cible debout
                # (serveur) ; nouveau clic au même endroit.
                self.game.sleep(NET_RETRY_EVERY)
                self.game.move(point, spread)
                log.debug("%s : clic ignoré (animation du sort précédent), nouveau clic %.1f s après lui", spell.name,
                          time.monotonic() - previous)
                self._clicked_at = time.monotonic()
                self.game.click()
                self._mouse_away()
                self._cast_at = self._target_cast_at = time.monotonic()
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
            if self.game_background and attempt == 1 and any(c == target.cell for _, c in self._use.casts_on_target):
                # Cible déjà touchée ce tour : de nouveau, à un autre endroit de sa case (21/10 : relancé à 3 px du clic
                # refusé, 0 parti sur 10 ; autre endroit à mesurer, sinon refusals_on_hit_target).
                point, spread = clear_point(before.grid, target.cell, standing, hidden, away=clicked,
                                            raised=self.map.raised, lift=self.map.lift)
                log.info("%s refusé sur %s (capture : %s) : nouvel essai à un autre endroit de sa case, en (%d, %d)",
                         spell.name, _aim_label(target), path, *point)
                if spell_range_visible(after) or self._select_spell(self.game.look(), spell) is not None:
                    continue
                log.info("%s non resélectionnable après le refus", spell.name)
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

    def _left_its_cell(self, before: Frame, after: Frame, target: Fighter) -> bool:
        """Le combattant visé avait son cercle sur sa case avant le lancer, et ne l'a plus (serveur : plus personne
        debout sur sa case)."""
        net = self._net()
        if net is not None:
            return all(cell != target.cell for _, cell, _, _ in net.fighters())

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

    def _read_life(self, frame: Frame | None, target: Fighter) -> int | None:
        """Vie de la cible survolée, lue sur sa fiche (perception/hp.py) ; None sans fiche ou illisible. Gardée pour
        toute l'équipe (MapMemory.life) : le moteur achève les ennemis entamés et concentre les coups.

        Si un seul sort est parti depuis la lecture précédente de sa vie, dans ce tour et sur sa case, ses dégâts sont
        mesurés (HitLog). Gardées pour vérifier : une capture de la fiche lue par combat, une de la fiche illisible
        (sans perte) par session."""
        net = getattr(self.game, "net", None)
        told = net.life_at(target.cell) if net is not None and hasattr(net, "life_at") and net.alive() else None
        # 31/10, utilisateur : le réseau d'abord ; la vie annoncée par le serveur (début de tour, chaque coup, mort),
        # la fiche survolée en secours (écoute absente ou combattant pas encore annoncé).
        hp = told if told is not None else read_hovered_hp(frame) if frame is not None else None
        label = "vie_illisible" if hp is None else "vie_lue"
        if told is None and frame is not None and label not in self._life_saved:
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
            if not self.settings.fast_end and not self._press_spell_key(key, spell):
                break
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
            self._use.record(spell, step.cell, failed=self._critical_failure())
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
        pa = self._pa_now(frame)
        if pa is not None and pa < self._use.pa:
            self._use.pa = pa
        if not self._use.available(spell):
            return f"{spell.name} plus lançable ({self._use.pa} PA, {self._use.casts_this_turn[spell.spell_id]} ce tour)"
        if spell.per_target and self._use.casts_on_target[(spell.spell_id, step.cell)] >= spell.per_target:
            return f"{spell.per_target} lancers par cible atteints"
        told = self._told_foes()  # 31/10 : ennemis debout d'après le serveur (mort : GA;103), pas les cercles
        standing = told if told is not None else {f.cell for f in find_fighters(frame) if f.team != my_team}
        if told is None and not standing & set(step.hits):
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
        if read_scene(self.game.look(park=False)).phase is not Phase.COMBAT:
            return None  # combat fini : la touche viserait la barre des objets (25/10)
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
                if read_scene(frame).phase is not Phase.COMBAT:
                    return None  # combat fini entre deux pressions (25/10)
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
        if not self._bar_shows(before, spell):
            return Cast.NO_SPELL
        for attempt, method in enumerate(sorted(SELF_CAST_METHODS, key=lambda m: m != self._self_cast)):
            if attempt and not self._bar_shows(self.game.look(park=False), spell):
                return Cast.NO_SPELL  # 2e façon, après un essai raté : combat peut-être fini, barre revue (25/10)
            if method == "clic droit":
                log.info("%s lancé sur moi (clic droit sur sa case)", spell.name)
                self._clicked_at = time.monotonic()  # écoute du serveur : sa réponse est attendue après ce clic
                self.game.click(spell_slot_point(before.layout, spell.slot), before.layout.spread(*SPELL_SLOT_SPREAD),
                                button="right")
            else:
                key = self._slot_key(spell.slot)
                if not key:
                    continue
                # Portée 0 : seule ma case s'éclaire, trop peu pour spell_range_visible ; le clic sur moi tranche.
                if not self._press_spell_key(key, spell):
                    return Cast.NO_SPELL
                self.game.sleep(0.15)
                if not self._self_cast_saved:
                    self._self_cast_saved = True  # une capture par session : ce que le jeu affiche
                    self.game.recorder.save(self.game.look(park=False).image, "sort_sur_moi_selectionne", lossless=True)
                log.info("%s lancé sur moi (touche %s puis clic sur mon personnage)", spell.name, key)
                self._clicked_at = time.monotonic()
                self.game.click((me.x, me.y), before.grid.click_spread())
            after = self._confirm_cast(before, my_team, spell)  # after_cast compris (mode fixe)
            if after is None:
                # 26/10 (journal 212835, Elseiso) : confirmé par le serveur en 0,28 s, la touche du sort suivant pressée
                # pendant l'animation de celui-ci, ignorée ; Flèche Harcelante crue indisponible, Tir Éloigné à la place
                # et pas d'attaque. Lancer noté : le sort suivant pas sélectionné juste après est retenté (_cast).
                self._cast_at = time.monotonic()
                if method != self._self_cast:
                    log.info("Sorts sur moi : %s, gardé pour la session", method)
                self._self_cast = method
                return Cast.DONE
            path = self.game.recorder.save(after.image, "sort_pas_parti", lossless=True)
            log.warning("%s ne semble pas parti par %s (PA inchangés, capture : %s)", spell.name, method, path)
            if spell_range_visible(after) and self._cancel_spell() is Cast.STUCK:
                return Cast.STUCK
        return Cast.NO_SPELL

    def _press_spell_key(self, key: str, spell: CombatSpell) -> bool:
        """Touche d'un sort, seulement si une capture fraîche montre encore le combat et ce sort dans sa case (25/10 :
        le combat fini, la barre repasse sur « Obj. » et la même touche boit l'objet de la case). False : pas pressée."""
        if not self._bar_shows(self.game.look(park=False), spell):
            return False
        self.game.press(key)
        return True

    def _bar_shows(self, frame: Frame, spell: CombatSpell, flash_retry: bool = True) -> bool:
        """Avant un clic droit sur la case d'un sort ou sa touche : toujours en combat, et la case montre bien ce sort
        (25/10, l'utilisateur : deux fois une potion de téléportation utilisée, la barre montrait les objets, combat fini
        ou onglet « Obj. » ouvert ; un clic droit sur la case d'une potion la boit). Sort sans icône connue : le combat
        seul est vérifié."""
        if read_scene(frame).phase is not Phase.COMBAT:
            log.warning("%s : plus en combat, pas de clic sur la barre (elle montre peut-être les objets)", spell.name)
            return False
        if spell_bar_tab(frame) == "objets":
            # 25/10 (journal 231114) : combat fini, la barre repassée sur « Obj. » ; la touche du sort a bu une potion.
            log.warning("%s : la barre montre l'onglet « Obj. » : ni touche ni clic", spell.name)
            return False
        entry = self.spellbook.in_slot(spell.slot) if self.spellbook is not None else None
        if entry is None or getattr(entry, "icon_core", None) is None:
            return True
        if slot_scores(frame, entry, range(spell.slot, spell.slot + 1)).get(spell.slot, -1.0) >= BAR_ICON_MATCH:
            return True
        # 25/10 (journal 171736, Elseiso) : le clic droit de Tir Puissant sur sa case l'a lancé ET a ouvert le menu de
        # Flash, resté par-dessus la barre ; Oeil de Taupe refusé deux fois derrière. Fermé, puis la case relue.
        if flash_retry and find_flash_menu(frame, anywhere=True) is not None:
            log.info("Le menu de Flash cache la barre de sorts : je le ferme avant %s", spell.name)
            if close_flash_menu(self.game, frame):
                return self._bar_shows(self.game.look(park=False), spell, flash_retry=False)
        path = self.game.recorder.save(frame.image, "barre_sans_le_sort", lossless=True)
        log.warning("La case %d de la barre ne montre pas %s (onglet des objets ?) : pas de clic (capture : %s)",
                    spell.slot, spell.name, path)
        return False

    def _walk(self, frame: Frame, me: Fighter, best: Cell, fighters: list[Fighter], zone: set[Cell], label: str,
              steps: int | None = None, retry: bool = True) -> int:
        """Clic sur `best` et vérification du déplacement ; renvoie les PM utilisés (0 : pas bougé ; moins que `steps` :
        arrêté en route, le jeu n'a pas trouvé de chemin jusqu'à `best`).

        `steps` : longueur du chemin prévu (plus que la distance quand il contourne un obstacle)."""
        steps = steps or distance(best, me.cell)
        seen = frame  # avant le clic : les cercles de ma couleur déjà là (preuve par différence, plus bas)
        stats = self._turn_stats()
        stats.moves += 1
        self.game.recorder.save(annotate(frame, fighters=fighters, cells=zone, marks=[(frame.grid.center(best), label)]),
                                "deplacement")
        # Pas sur le corps d'un combattant debout devant la case, ni sur le mien (03/10 : case juste au-dessus de moi,
        # cachée par mon propre corps : le clic m'a sélectionné, « n'a pas bougé »).
        point, spread = clear_point(frame.grid, best, fighters if me in fighters else fighters + [me],
                                    timeline_portraits(frame) + challenge_icons(frame), raised=self.map.raised,
                                    lift=self.map.lift)
        now = time.monotonic()
        if (wait := max(WALK_AFTER_CAST - (now - self._cast_at), self._animation_wait() - (now - self._served_at))) > 0:
            self.game.sleep(wait)
        clicked_at = self._walk_clicked_at = time.monotonic()
        self.game.click(point, spread)
        # Arrière-plan : souris virtuelle écartée, sinon elle reste sur la case d'arrivée, éclairée en bleu avec le
        # curseur du jeu par-dessus, et mon cercle n'y est plus vu (20/10, Leproduitlatte : 7 s de repérage pour rien).
        self._mouse_away()
        tracking = self._me_cell is not None or self._find_me is not None  # mode équipe : ma case suivie
        arrived, frame = self._wait_arrival(me, best, steps, quick=retry)
        self._moved_at = time.monotonic()
        net = getattr(self.game, "net", None)
        if not arrived and net is not None and net.my_id is not None and net.in_fight() and net.moved_at >= clicked_at:
            # Mon déplacement annoncé par le jeu (GA0;1, chemin jusqu'à sa dernière case : connection/network.py,
            # 26/10) : où je suis arrivé, sans chercher mon cercle à l'écran.
            now = net.my_grid_cell()
            if now is not None:
                if tracking:
                    self._me_cell, self._me_sure, self._proven_now, self._doubt = now, True, True, False
                if now == best:
                    return steps
                used = max(0, min(steps, net.move_steps))
                log.info("Arrêté en %s au lieu de %s (%d PM dépensés sur %d, d'après le jeu)", now, best, used, steps)
                return used
        if (retry and not arrived and net is not None and not self._watching and net.my_id is not None
                and net.in_fight() and net.moved_at < clicked_at and getattr(net, "tackled_at", 0.0) < clicked_at
                and self._my_turn(frame, me.team)):
            # Rien du serveur en NET_MOVE_SILENT s : clic pas pris (animation, corps devant la case), nouveau clic.
            return self._reclick(frame, me, best, fighters, zone, label, steps, clicked_at,
                                 f"rien du serveur en {NET_MOVE_SILENT:.1f} s")
        if arrived:
            if tracking:
                # Un cercle de ma couleur est apparu sur la case libre visée : c'est moi, preuve faite.
                self._me_cell, self._me_sure, self._proven_now = best, True, True
            return steps  # arrivé
        mine = [f.cell for f in find_fighters(frame) if f.team == me.team]
        # Personne d'autre ne bouge pendant mon tour : un seul cercle de ma couleur apparu, à ma portée, sur une case où
        # aucun n'était avant mon clic, c'est moi (preuve, sans sortilège : 20/10, 4 fins de tour sans PA pour se
        # repérer par la portée). Les cases des alliés, même au cercle caché, ne comptent pas.
        before = {f.cell for f in [*fighters, *find_fighters(seen)] if f.team == me.team} | {me.cell}
        appeared = [c for c in mine if c not in before and distance(c, me.cell) <= steps]
        # Seulement si ma case est vide : toujours un cercle dessus, je n'ai pas bougé, et celui qui apparaît est un
        # autre (invocation de ma couleur : La Bloqueuse, La Folle du Sadida, Sac Animé des Enutrofs).
        if len(appeared) == 1 and me.cell not in mine:
            now = appeared[0]
            if tracking:
                self._me_cell, self._me_sure, self._proven_now, self._doubt = now, True, True, False
            if now == best:
                return steps  # arrivé : vu après l'attente seulement
            used = min(steps, distance(me.cell, now))
            log.info("Arrêté en %s au lieu de %s (%d PM dépensés sur %d) : chemin bloqué", now, best, used, steps)
            return used
        if me.cell not in mine:
            # Parti (mon cercle n'est plus sur ma case), sans nouveau cercle sûr : le mien est caché, ou plusieurs ont
            # bougé à l'écran. Le cercle de ma couleur le plus proche de là où je voulais aller, hors des cases de mes
            # alliés (03/10 : chemin coupé par un trou et un allié, 1 PM dépensé sur 3) : case supposée, revérifiée
            # (portée) avant d'agir encore, jamais transmise à l'équipe telle quelle.
            allies = {f.cell for f in fighters if f.team == me.team and f.cell != me.cell}
            now = min((c for c in mine if c not in allies), key=lambda c: distance(c, best), default=best)
            if tracking:
                self._me_cell, self._me_sure, self._doubt = now, False, True
            if now != best:
                used = min(steps, distance(me.cell, now))
                log.info("Arrêté en %s au lieu de %s (%d PM dépensés sur %d) : chemin bloqué ; je vérifie où je suis",
                         now, best, used, steps)
                return used
            log.info("Arrivé en %s, mais mon cercle ne s'y voit pas : je vérifie où je suis", best)
            return steps
        if net is not None and getattr(net, "tackled_at", 0.0) >= clicked_at:
            # 27/10 : annoncé par le serveur (GA;104) dès le premier clic ; avant, un second clic et ~4 s pour conclure.
            stats.stuck_moves += 1
            log.info("Taclé en quittant le contact d'un ennemi (annoncé par le jeu : PM perdus) : je reste en %s",
                     me.cell)
            return 0
        if retry and self._my_turn(frame, me.team):
            # Clic tombé sur un combattant qui passait sur la case (03/10), ou pas pris juste après « Mon tour » (20/10 :
            # 4 premiers gestes sur 75, 0,13 s après) : un nouveau clic, d'après les positions de maintenant.
            return self._reclick(frame, me, best, find_fighters(frame), zone, label, steps, clicked_at,
                                 "mon cercle n'a pas bougé")
        stats.stuck_moves += 1
        path = self.game.recorder.save(frame.image, "deplacement_rate", lossless=True)
        log.warning("Mon personnage n'a pas bougé (capture : %s)", path)
        if (net := getattr(self.game, "net", None)) is not None and hasattr(net, "heard_since"):
            # 27/10 : souvent en quittant le contact d'un ennemi (tacle ?) ; ce que dit le serveur, pour le reconnaître.
            log.debug("Serveur depuis le premier clic : %s", " | ".join(net.heard_since(clicked_at - 3.0)) or "rien")
        # Mon cercle toujours sur ma case et aucun autre apparu : personne n'a bougé (taclé, chemin bloqué). Ma case
        # reste ce qu'elle était (20/10, Vertdepomme taclé : revérifiée sans PA pour le faire, tour fini « sans preuve »).
        return 0

    def _reclick(self, frame: Frame, me: Fighter, best: Cell, fighters: list[Fighter], zone: set[Cell], label: str,
                 steps: int, clicked_at: float, why: str) -> int:
        """Premier clic de déplacement pas pris : un second, compté comme reclic et non comme raté (l'utilisateur,
        01/11 : les analyses ne comptent comme ratés que les déplacements restés sans effet après lui)."""
        self._turn_stats().move_retries += 1
        log.info("Clic de déplacement pas pris (%s) : je reclique sur %s", why, best)
        used = self._walk(frame, me, best, fighters if me in fighters else fighters + [me], zone, label, steps,
                          retry=False)
        if used:
            self._slow_animation(clicked_at)
        return used

    def _critical_failure(self) -> bool:
        """Le dernier sort lancé a fait un échec critique d'après le serveur (GA;302) : PA perdus sans effet."""
        net = self._net()
        return net is not None and getattr(net, "failed_at", 0.0) >= self._clicked_at

    def _animation_wait(self) -> float:
        """Attente avant un clic de déplacement depuis la réponse du serveur au dernier sort (WALK_AFTER_SERVED)."""
        spell = self._served_spell
        return WALK_AFTER_SERVED if spell is None else _slow_animations.get(spell.name, WALK_AFTER_SERVED)

    def _slow_animation(self, clicked_at: float) -> None:
        """Premier clic de déplacement ignoré, le second pris au même endroit : l'animation du dernier sort courait
        encore. Son attente passe à WALK_SLOW_MARGIN s de plus que ce premier clic (WALK_SLOW_MAX s au plus)."""
        spell, gap = self._served_spell, clicked_at - self._served_at
        if spell is None or not 0 <= gap < WALK_SLOW_MAX:
            return
        wait = min(gap + WALK_SLOW_MARGIN, WALK_SLOW_MAX)
        if wait > self._animation_wait():
            _slow_animations[spell.name] = wait
            log.info("%s : animation plus longue (clic de déplacement ignoré %.2f s après la réponse du jeu) ; "
                     "déplacements %.1f s après elle désormais", spell.name, gap, wait)

    def _wait_arrival(self, me: Fighter, best: Cell, steps: int, quick: bool = False) -> tuple[bool, Frame]:
        """Après le clic de déplacement : (mon cercle est sur `best`, dernière capture). `quick` : écoute, rien du
        serveur en NET_MOVE_SILENT s suffit à conclure (premier clic, recliqué aussitôt).

        Fixe : per_cell_moved s par case, puis jusqu'à 8 captures toutes les 0,2 s. Condition : captures dès le clic,
        une toutes les 0,1 s, pendant autant de temps au plus (per_cell_moved s par case + 8 × 0,2 s)."""

        def arrived(frame: Frame) -> bool:
            return best in {f.cell for f in find_fighters(frame) if f.team == me.team}

        net = self._net()
        if net is not None and not self._watching:
            # Chemin annoncé par le serveur (GA0;1, sa dernière case) dès le départ ; la marche dure per_cell_moved s
            # par case : attendue, sans chercher mon cercle à l'écran.
            # Une mort avec mon dernier sort (pas encore de sort : -inf, aucune).
            # Un déplacement annoncé depuis la mort : son animation est finie (01/11, journal 144405 : Pesopepe, 6 s
            # attendues deux fois pour un clic suivant un autre déplacement).
            death = getattr(net, "death_at", 0.0)
            killed = self._served_at > 0 and death >= self._served_at and getattr(net, "moved_at", 0.0) < death
            timeout = DEATH_MOVE_WAIT if killed else NET_MOVE_SILENT if quick else ARRIVAL_CHECKS * ARRIVAL_EVERY
            moved = net.wait_for(("moved_at", "tackled_at"), self._walk_clicked_at, timeout)
            if moved is not None and net.tackled_at >= self._walk_clicked_at:
                return False, self.game.look()  # taclé : rien à attendre (_walk)
            if moved is not None:
                cells = max(1, net.move_steps)
                self.game.sleep(max(0.0, self.settings.per_cell_moved * cells - (time.monotonic() - moved)))
                # La marche dure plus que per_cell_moved par case (26/10, journal 234233 : Cupidité cliquée pendant la
                # marche, ignorée, la façon de lancer sur soi changée à tort) : fin de la marche vue à l'écran, mon
                # cercle sur la case annoncée, comme avant l'écoute ; la case, elle, vient du serveur.
                there = net.my_grid_cell()
                for _ in range(ARRIVAL_CHECKS):
                    frame = self.game.look()
                    if there in {f.cell for f in find_fighters(frame) if f.team == me.team}:
                        break
                    self.game.sleep(ARRIVAL_EVERY)
                return there == best, frame
            return False, self.game.look()
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
    def me_sure(self) -> bool:
        """Ma case suivie (_me_cell) est prouvée : l'équipe la retient (TeamBot._cells) ; sinon elle l'oublie."""
        return self._me_sure

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
        placed = set().union(*placement.free.values(), *placement.occupied.values())
        if len(placed) > len(self.map.placed[0]):
            self.map.placed = (placed, frame)  # gardées pour le contrôle, même si la carte se reconnaît plus tard
        self._recognize_arena(placed=placed)
        if self.map.arena is not None:
            blockers = set(self.map.arena.blocks)  # carte officielle : même les blocs cachés par l'interface
        else:
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
                "deplacements_sans_bouger": sum(s.stuck_moves for s in stats),
                "reclics": sum(s.move_retries for s in stats)}

    def _pass_turn(self, my_team: str) -> None:
        """Touche de fin de tour (F1), sinon le bouton ; on vérifie que la main passe. Fenêtre déjà vue sourde aux
        touches (20/10, S3 à S8 : F1 ignoré par la fenêtre de Koalaloupour ; brain/recovery.py) : le bouton
        directement, sans perdre deux essais de F1."""
        if self._end_turn_key and (self._keys_deaf or is_deaf(self.game)):
            log.info("Fenêtre sourde aux touches : je clique sur le bouton de fin de tour plutôt que %s",
                     self._end_turn_key.upper())
        elif self._end_turn_key:
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
        31/10 : d'après le serveur d'abord (GTF de mon id, GTS d'un autre, GE) ; l'écran seulement sans écoute, flux
        pas vivant, ou mon tour pas annoncé par le serveur (combat pas entendu depuis son début).
        Écran, fixe : une capture toutes les 0,25 s ; condition : captures dès le geste, une toutes les 0,1 s."""
        net = getattr(self.game, "net", None)  # pas _net : un combat fini (GE) dit aussi la main passée
        if net is not None and getattr(net, "my_id", None) is not None and hasattr(net, "wait_turn_over") \
                and net.alive():
            turn, _, _ = net.fight_turn()
            # Mon tour annoncé (GTS), maintenant ou au début de ce tour (play_turn) : sa fin le sera aussi.
            if turn == net.my_id or getattr(self, "_turn_told", False):
                if net.wait_turn_over(net.my_id, checks * TURN_PASSED_EVERY):
                    return True
                log.info("Main pas passée d'après le serveur (pas de fin de tour annoncée en %.1f s)",
                         checks * TURN_PASSED_EVERY)
                return False

        def passed(frame: Frame) -> bool:
            return not self._my_turn(frame, my_team)

        if self._watching:  # la timeline reste visible même souris sur la carte
            return self._watch("main passée", passed, checks * TURN_PASSED_EVERY, park=False)[0] is not None
        for _ in range(checks):
            self.game.sleep(TURN_PASSED_EVERY)
            if passed(self.game.look(park=False)):  # la timeline reste visible
                return True  # main passée, ou combat fini
        return False
