"""Connaissance des sorts pour la session.

1. Fenêtre « Tes sorts », une fois par session et sans ouvrir les fiches : niveau, coût en PA, portée et icône de
   chaque sort appris. La liste montre 10 lignes : elle est défilée à la molette jusqu'en bas (04/10 : personnages
   plus haut niveau, sorts appris au-delà de la 10e ligne), pages recalées par les icônes (_next_pages).
2. Référence Solomonk (config/sorts/solomonk.json, tools/import_sorts.py) : tout le reste, au niveau lu (lancer en
   ligne, ligne de vue, portée modifiable, lancers par tour et par cible, relance, effets).
   Classe et sorts : chaque ligne de la fenêtre, apprise ou non (un sort pas appris montre les PA et la portée de
   son niveau 1), est reliée au sort de la classe dont elle a les PA, la portée, le niveau, et l'icône quand elle
   est connue (assets/sorts/icones) : voir match_class. La classe retenue est celle qui explique nettement le mieux
   les lignes ; sinon aucune (signalé : sorts inconnus), à moins qu'elle soit imposée dans config/sorts.yaml.
   La fenêtre range les sorts par niveau requis comme Solomonk, sauf le sort spécial (requis : 0) : en tête chez
   l'Enutrof le 03/10, pas dans les 10 premières lignes chez le Crâ. L'ordre ne fait que départager deux sorts aux
   mêmes PA et portée (Sac Animé et Pelle Animée de l'Enutrof).
   Le niveau lu est croisé avec les PA et la portée lus : s'ils ne sont pas ceux de ce niveau, c'est le niveau le
   plus proche qui leur correspond qui est pris (et signalé).
3. config/sorts.yaml : rôle tactique d'un sort, quand celui déduit de ses effets ne convient pas (deduce_role) ;
   classe imposée d'un personnage (section classes), prioritaire sur celle déduite de la fenêtre.
4. Au premier combat, quand la barre de sorts est affichée : case de la barre de chaque sort.
5. Caractéristiques du personnage (04/10, brain/stats.py), lues juste après la fenêtre des sorts : les dégâts de
   chaque sort en tiennent compte (mean_damage), journalisés « Flèche Magique : 13 -> 25 dégâts moyens (...) ».

Début de session (brain/memory.py, réglage session.mode) : « controle » lit tout à l'écran puis le retient pour le
personnage (data/personnages/<Nom>.json) ; « memoire » reprend ce qui a été retenu à la dernière session, sans ouvrir
de fenêtre, et ne lit à l'écran que ce qui manque.
"""

import json
import logging
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from bot.brain.game import Game
from bot.brain.memory import MEMORY, CharacterMemory, SessionSettings, entry_of, entry_record, now_text, when
from bot.brain.stats import CharacterStats, StatsReader
from bot.brain.tactics import CombatSpell
from bot.capture.window import character_name
from bot.config import ROOT
from bot.perception.frame import Frame
from bot.perception.spellbook import (
    ARROW_DOWN,
    AWAY_POINT,
    LIST_POINT,
    ROWS,
    THUMB_BOTTOM,
    THUMB_TOP,
    SpellEntry,
    find_close_button,
    find_spell_window,
    find_spells_button,
    identify,
    locate_in_bar,
    page_shift,
    read_spell_window,
    row_icons,
    scroll_bar,
    window_point,
)

log = logging.getLogger(__name__)

KNOWLEDGE_FILE = ROOT / "config" / "sorts.yaml"
READ_TRIES = 3  # fenêtre des sorts pas ouverte ou illisible : nouveaux essais avant de jouer avec le sort configuré
REFERENCE_FILE = ROOT / "config" / "sorts" / "solomonk.json"
DAMAGE_EFFECTS = set(range(91, 101))  # vols de vie (91 à 95) et dommages (96 à 100), un par élément
PUSH, PA_LOSS, RANGE_BONUS = 5, 101, 117  # identifiants d'effets du client Rétro
NO_EFFECT = {120, 666}  # Xélor, après chaque sort : « +1 PA » (10 %) ou « pas d'effet supplémentaire » (90 %)
DAMAGE_BONUS, DAMAGE_PERCENT = 112, 138  # « +40 de dommages », « Augmente les dommages de 140% »
# Effets qui peuvent accompagner un bonus de dégâts sans gêner : dévoiler les invisibles (Perception de l'Ecaflip),
# coups critiques. Tout autre effet (état, transformation, PM perdus, dégâts au lanceur : Mutilation, Roue de la
# Fortune, Colère de Zatoïshwan, Momification) : pas un simple bonus, la tactique ne s'en sert pas.
BOOST_SIDE_EFFECTS = {202, 115}
# Bonus de caractéristique (+force, +intelligence, +chance, +agilité) : +1 % aux dégâts de l'élément par point (formule
# des caractéristiques). Un personnage tape d'habitude dans deux éléments : compté pour la moitié de leur somme.
STAT_BOOSTS = {118: "terre", 126: "feu", 123: "eau", 119: "air"}
TEAM_ZONE = 63  # taille de zone « toute la carte » chez Solomonk : bonus pour toute l'équipe
MIN_DAMAGE_PER_PA = 1.0  # un sort à malus (ou recul...) qui fait moins de dégâts moyens par PA n'est pas une attaque
# Classe d'après la fenêtre (match_class) : points des lignes reliées à un sort de la classe.
ICON_POINTS = 3  # icône reconnue : le sort est sûr ; PA, portée et niveau qui collent : 1 point chacun
CONTRADICTION = 2  # points en moins par ligne lisible qu'aucun sort de la classe n'explique
MIN_ROWS, MIN_MARGIN = 3, 4  # classe retenue : 3 lignes lisibles expliquées, 4 points d'avance sur la suivante
# Défilement de la liste des sorts (_next_pages). Lignes par cran de molette inconnues (1 ou 3 selon les listes
# Flash) : mesurées au premier cran, puis assez de crans pour avancer d'environ PAGE_STEP lignes (au moins 2 lignes
# communes entre deux pages pour les recaler).
FIRST_NOTCHES = 2
PAGE_STEP = 7
MAX_PAGES = 6  # 22 sorts par classe : 3 pages suffisent même à une ligne par cran
TOP_NOTCHES = 30  # liste restée défilée à l'ouverture : remontée tout en haut d'un coup
# Molette sans effet (arrière-plan) : clics sur le rail au-dessus de la poignée, ~5 lignes chacun (18/10 : la molette
# n'a pas remonté la liste de Lestunes, lue à partir de la 11e ligne comme si c'était la 1re ; Enutrof sans ses sorts).
TOP_CLICKS = 6
ARROW_CLICKS = 3  # molette et rail sans effet : clics sur la flèche du bas de la barre de défilement
# 11/10 (tools/arriere_plan_sorts.py, en arrière-plan) : molette (3 façons), flèche du bas (clic d'un bloc ou lent) et
# touches sans effet ; un clic sur le rail sous la poignée fait défiler d'environ 5 lignes (poignée glissée aussi).
SCROLL_WAIT = 0.3  # après un défilement, avant la capture
UNMATCHED = -1000.0  # poids d'un sort que la ligne contredit (best_assignment)


def load_knowledge(path: Path = KNOWLEDGE_FILE) -> dict[str, dict]:
    with path.open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("sorts") or {}


def load_classes(path: Path = KNOWLEDGE_FILE) -> dict[str, str]:
    """Classes imposées (section classes de config/sorts.yaml) : nom du personnage en minuscules -> classe écrite."""
    with path.open(encoding="utf-8") as f:
        classes = (yaml.safe_load(f) or {}).get("classes") or {}
    return {str(name).strip().lower(): str(value) for name, value in classes.items()}


@lru_cache
def load_reference(path: Path = REFERENCE_FILE) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _plain(text: str) -> str:
    """Minuscules sans accents : « Crâ » -> « cra »."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).strip().lower()


def class_key_of(text: str, reference: dict) -> str | None:
    """Clé de la classe écrite `text` (clé ou nom, accents et majuscules indifférents) ; None : inconnue."""
    wanted = _plain(text)
    return next((key for key, info in reference["classes"].items() if wanted in (key, _plain(info["nom"]))), None)


def character_of(game) -> str:
    """Nom du personnage d'après le titre de la fenêtre du jeu (« Walitcoin - Dofus Retro v1.49.5 ») ; "" : inconnu."""
    return character_name(getattr(getattr(game, "window", None), "title", "") or "", "Dofus Retro")


def fits(level: dict, entry: SpellEntry) -> bool:
    """PA et portée lus (quand ils sont lisibles) égaux à ceux de ce niveau du sort."""
    return (entry.pa is None or entry.pa == level["pa"]) and \
        (entry.reach is None or tuple(entry.reach) == tuple(level["portee"]))


def readable(entry: SpellEntry) -> bool:
    """PA ou portée lus : la ligne dit quelque chose du sort."""
    return entry.pa is not None or entry.spell_range is not None


def level_agrees(entry: SpellEntry, spell: dict) -> bool:
    """La ligne montre les PA et la portée du niveau lu (1 pour un sort pas appris : la fenêtre montre son niveau 1)."""
    shown = entry.level if entry.learned else 1
    return any(level["niveau"] == shown and fits(level, entry) for level in spell["niveaux"])


def row_points(entry: SpellEntry, spell: dict, key: str | None) -> int | None:
    """Ce que la ligne dit de ce sort : 1 point pour ses PA, sa portée et son niveau quand ils collent avec un même
    niveau du sort (level_agrees), ICON_POINTS si l'icône reconnue (`key`) est la sienne ; None si la ligne le
    contredit (icône d'un autre sort, ou PA et portée d'aucun de ses niveaux)."""
    if key is not None and key != spell["cle"]:
        return None
    points = ICON_POINTS if key is not None else 0
    if not spell["niveaux"]:
        return points  # pas de données chez Solomonk : ni pour ni contre
    if not any(fits(level, entry) for level in spell["niveaux"]):
        return points or None  # icône reconnue : chiffres mal lus
    return points + (entry.pa is not None) + (entry.reach is not None) + level_agrees(entry, spell)


def window_order(reference: dict, class_key: str) -> list[dict]:
    """Sorts de la classe dans l'ordre attendu dans la fenêtre : par niveau de personnage requis, comme chez
    Solomonk, qui met pourtant à la fin le sort spécial (requis : 0). Un sort sans données reste à sa place."""
    spells, required = [], 0
    for index, spell_id in enumerate(reference["classes"][class_key]["sorts"]):
        spell = reference["sorts"][str(spell_id)]
        if spell["niveaux"] and spell["niveaux"][0].get("niveau_requis") is not None:
            required = spell["niveaux"][0]["niveau_requis"]
        spells.append((required, index, spell))
    return [spell for *_, spell in sorted(spells, key=lambda s: s[:2])]


def best_assignment(weights: list[list[float]]) -> list[int]:
    """Colonne de chaque ligne, toutes différentes, pour la plus grande somme des poids (méthode hongroise ; au moins
    autant de colonnes que de lignes)."""
    n, m = len(weights), len(weights[0]) if weights else 0
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    owner, way = [0] * (m + 1), [0] * (m + 1)  # owner[j] : ligne (à partir de 1) qui a la colonne j
    for i in range(1, n + 1):
        owner[0], j0 = i, 0
        lowest, used = [float("inf")] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = owner[j0], float("inf"), 0
            for j in range(1, m + 1):
                if not used[j]:
                    cost = -weights[i0 - 1][j - 1] - u[i0] - v[j]
                    if cost < lowest[j]:
                        lowest[j], way[j] = cost, j0
                    if lowest[j] < delta:
                        delta, j1 = lowest[j], j
            for j in range(m + 1):
                if used[j]:
                    u[owner[j]] += delta
                    v[j] -= delta
                else:
                    lowest[j] -= delta
            j0 = j1
            if owner[j0] == 0:
                break
        while j0:
            owner[j0] = owner[way[j0]]
            j0 = way[j0]
    columns = [0] * n
    for j in range(1, m + 1):
        if owner[j]:
            columns[owner[j] - 1] = j - 1
    return columns


@dataclass
class ClassMatch:
    """Lignes de la fenêtre reliées aux sorts d'une classe."""

    class_key: str
    score: float = 0.0  # points des lignes reliées, moins CONTRADICTION par ligne lisible qu'aucun sort n'explique
    spells: dict[int, dict] = field(default_factory=dict)  # ligne -> sort de la classe
    explained: int = 0  # lignes lisibles (PA ou portée) reliées à un sort
    contradictions: list[int] = field(default_factory=list)  # lignes lisibles qu'aucun sort de la classe n'explique


def match_class(entries: list[SpellEntry], reference: dict, class_key: str,
                keys: dict[int, str | None]) -> ClassMatch:
    """Chaque ligne (apprise ou non) reliée à un sort différent de la classe, pour le plus de points (row_points) ;
    `keys` : ligne -> sort reconnu à son icône.

    Place attendue : la ligne n va avec le n-ième sort de window_order, décalé comme les lignes qui n'ont qu'un sort
    possible. Elle départage les égalités de points (Sac Animé et Pelle Animée) ; un sort qui n'a les PA et la
    portée lus qu'à un autre niveau que celui lu, loin de sa place, est écarté (deux anomalies : plutôt un chiffre
    mal lu, la ligne reste inconnue)."""
    spells = window_order(reference, class_key)
    points = [[row_points(e, s, keys.get(e.row)) for s in spells] for e in entries]
    offsets = Counter()
    for entry, row in zip(entries, points):
        best = max((p for p in row if p is not None), default=0)
        candidates = [j for j, p in enumerate(row) if p == best]
        if best > 0 and len(candidates) == 1:
            offsets[candidates[0] - entry.row] += 1
    shift = max(offsets, key=lambda d: (offsets[d], -abs(d)), default=0)
    for entry, row in zip(entries, points):
        if keys.get(entry.row) is None and (entry.level is not None or not entry.learned):
            for j, spell in enumerate(spells):
                if row[j] is not None and j != entry.row + shift and not level_agrees(entry, spell):
                    row[j] = None
    tiebreak = 0.5 / max(1, len(entries))  # moins d'un point en tout : ne départage que des égalités
    weights = [[UNMATCHED if p is None else p + tiebreak / (1 + abs(j - entry.row - shift))
                for j, p in enumerate(row)] + [-CONTRADICTION if readable(entry) else 0.0] * len(entries)  # aucun sort
               for entry, row in zip(entries, points)]
    match = ClassMatch(class_key)
    for entry, row, column in zip(entries, points, best_assignment(weights)):
        if column < len(spells) and row[column] is not None:
            match.spells[entry.row] = spells[column]
            match.score += row[column]
            match.explained += readable(entry)
        elif readable(entry):
            match.contradictions.append(entry.row)
            match.score -= CONTRADICTION
    return match


def rank_classes(entries: list[SpellEntry], reference: dict, keys: dict[int, str | None]) -> list[ClassMatch]:
    """Les classes, de celle qui explique le mieux les lignes de la fenêtre à celle qui les explique le moins."""
    return sorted((match_class(entries, reference, key, keys) for key in reference["classes"]), key=lambda m: -m.score)


def is_clear(ranking: list[ClassMatch]) -> bool:
    """La première classe explique au moins MIN_ROWS lignes lisibles, avec MIN_MARGIN points d'avance."""
    best = ranking[0]
    return best.explained >= MIN_ROWS and (len(ranking) < 2 or best.score - ranking[1].score >= MIN_MARGIN)


def guess_class(entries: list[SpellEntry], reference: dict, keys: dict[int, str | None]) -> str | None:
    """Classe qui explique nettement le mieux les lignes de la fenêtre ; None si c'est incertain."""
    ranking = rank_classes(entries, reference, keys)
    return ranking[0].class_key if ranking and is_clear(ranking) else None


def choose_level(spell: dict, entry: SpellEntry) -> tuple[dict, str]:
    """Niveau du sort chez Solomonk ({} : pas de données), et ce qui cloche dans la lecture ("" : rien)."""
    levels = spell["niveaux"]
    if not levels:
        return {}, "pas de données chez Solomonk"
    matching = [level for level in levels if fits(level, entry)]
    read = entry.level if entry.level and 1 <= entry.level <= len(levels) else None
    if read is not None and (levels[read - 1] in matching or not matching):
        return levels[read - 1], "" if matching else "PA ou portée lus différents de ceux de ce niveau"
    if matching:
        chosen = min(matching, key=lambda level: abs(level["niveau"] - (read or 1)))
        return chosen, f"{f'niveau lu {read}' if read else 'niveau illisible'} : PA et portée du niveau {chosen['niveau']}"
    return levels[0], "niveau illisible, PA et portée sans correspondance : niveau 1 supposé"


def mean_damage(effects: list[dict], stats: CharacterStats | None = None) -> float:
    """Dégâts moyens par ennemi touché : poison compté à chaque tour, effet aléatoire pondéré par sa chance (Bluff
    de l'Ecaflip : 50 %). Avec `stats` : chaque ligne de dégâts augmentée des caractéristiques du personnage (formule
    validée par l'utilisateur le 04/10, CharacterStats.boost) ; sans : dégâts de base (rôle du sort, deduce_role)."""
    total = 0.0
    for e in effects:
        if e["id"] not in DAMAGE_EFFECTS:
            continue
        line = ((e["min"] or 0) + (e["max"] or 0)) / 2
        if stats is not None:
            line = stats.boost(line, e.get("element"))
        total += line * max(1, e.get("tours", 0)) * (e.get("chance") or 100) / 100
    return total


def boost_of(level: dict) -> tuple[float, float, int] | None:
    """Bonus de dégâts d'un sort lancé sur soi : (dommages par ligne, % de dommages, tours), moyens ; None si ce n'en
    est pas un simple (autre effet, bonus en zone ou pour un allié seulement, portée qui n'inclut pas ma case)."""
    effects = level.get("effets") or []
    ids = {e["id"] for e in effects}
    if not ids & {DAMAGE_BONUS, DAMAGE_PERCENT} or ids - {DAMAGE_BONUS, DAMAGE_PERCENT} - BOOST_SIDE_EFFECTS - NO_EFFECT:
        return None
    boosts = [e for e in effects if e["id"] in (DAMAGE_BONUS, DAMAGE_PERCENT)]
    if level["portee"][0] != 0 or any((e.get("zone") or {}).get("forme", "point") != "point" for e in boosts):
        return None  # Guide de Bravoure (toute la carte), Cri de l'Ours (sur un allié)

    def mean(e: dict) -> float:
        return ((e["min"] or 0) + (e["max"] or e["min"] or 0)) / 2

    return (sum(mean(e) for e in boosts if e["id"] == DAMAGE_BONUS),
            sum(mean(e) for e in boosts if e["id"] == DAMAGE_PERCENT),
            max(e.get("tours") or 1 for e in boosts))


def stat_boost_of(level: dict) -> tuple[float, float, int] | None:
    """Bonus de caractéristiques lancé de ma case (Cupidité de l'Enutrof : +50 force et +50 intelligence à toute
    l'équipe, 5 tours ; Chance : +11 à 20 chance sur soi) : (0, % de dégâts, tours), comme boost_of ; None sinon."""
    effects = level.get("effets") or []
    ids = {e["id"] for e in effects}
    if not ids & set(STAT_BOOSTS) or ids - set(STAT_BOOSTS) - BOOST_SIDE_EFFECTS - NO_EFFECT or level["portee"][0] != 0:
        return None
    boosts = [e for e in effects if e["id"] in STAT_BOOSTS]
    pct = sum(((e["min"] or 0) + (e["max"] or e["min"] or 0)) / 2 for e in boosts) / 2
    return 0.0, pct, max(e.get("tours") or 1 for e in boosts)


def team_boost(level: dict) -> bool:
    """Bonus sur toute la carte (toute l'équipe), pas seulement sur ma case."""
    return any((e.get("zone") or {}).get("taille", 0) >= TEAM_ZONE for e in level.get("effets") or []
               if e["id"] in STAT_BOOSTS or e["id"] in (DAMAGE_BONUS, DAMAGE_PERCENT))


def deduce_role(level: dict) -> str:
    """Rôle tactique d'après les effets (voir config/sorts.yaml) ; « autre » : la tactique ne s'en sert pas.

    Une attaque fait des dégâts aux ennemis (effets 91 à 100) : jamais une invocation, un soin, un bonus ou un malus
    seuls (Sac Animé, Clé Réductrice : « autre »). Un sort qui a aussi un autre effet (malus, recul, vol, état...)
    mais moins de MIN_DAMAGE_PER_PA dégâts moyens par PA sert surtout à cet effet, que la tactique ne sait pas
    peser : « autre » aussi (Aveuglement du Féca au niveau 1 : 1 dégât et -1 à -2 PA, pour 3 PA)."""
    effects = level.get("effets") or []
    ids = {e["id"] for e in effects}
    if RANGE_BONUS in ids and tuple(level["portee"]) == (0, 0):
        return "boost_portee"
    if boost_of(level) is not None or stat_boost_of(level) is not None:
        return "boost_degats"
    damage = [e for e in effects if e["id"] in DAMAGE_EFFECTS]
    if not damage:
        return "autre"
    if ids - DAMAGE_EFFECTS - NO_EFFECT and mean_damage(effects) < MIN_DAMAGE_PER_PA * level["pa"]:
        return "autre"
    if any(e["id"] == PUSH and (e["max"] or 0) >= 2 for e in effects):
        return "repousser"
    if any((e.get("zone") or {}).get("forme") == "ligne" for e in damage):
        return "degats_zone"
    if PA_LOSS in ids:
        return "retrait_pa"
    if any(e.get("tours") for e in damage):
        return "poison"
    return "degats"


@dataclass
class Spell:
    """Un sort appris : sa ligne de la fenêtre, et son niveau chez Solomonk."""

    entry: SpellEntry
    key: str | None  # identifiant (clé Solomonk, comme dans config/sorts.yaml et assets/sorts/icones) ; None : inconnu
    name: str
    data: dict = field(default_factory=dict)  # le niveau du sort chez Solomonk ({} : inconnu)
    role: str = "autre"
    remark: str = ""  # ce qui cloche dans la lecture du niveau

    @property
    def level(self) -> int | None:
        return self.data.get("niveau")

    def summary(self) -> str:
        level = self.data
        if not level:
            return self.entry.describe()
        low, high = level["portee"]
        parts = [f"niv. {level['niveau']}", f"{level['pa']} PA",
                 f"portée {low} à {high}" + (" modifiable" if level["portee_modifiable"] and high else ""),
                 "en ligne" if level["lancer_en_ligne"] else "", "" if level["ligne_de_vue"] else "sans ligne de vue",
                 f"{level['lancers_par_tour']} lancer(s) par tour" if level["lancers_par_tour"] else "",
                 f"{level['lancers_par_cible']} lancer(s) par cible" if level["lancers_par_cible"] else "",
                 f"relance tous les {level['relance']} tours" if level["relance"] else ""]
        effects = " + ".join(e["texte"] + (f" ({e['tours']} tour{'s' if e['tours'] > 1 else ''})" if e.get("tours") else "")
                             for e in level["effets"])
        zone = next((e["zone"] for e in level["effets"] if (e.get("zone") or {}).get("forme", "point") != "point"
                     and e["zone"].get("taille")), None)
        if zone and zone.get("texte"):
            effects += f" ; zone : {zone['texte']}"
        return ", ".join(p for p in parts if p) + (f" ; {effects}" if effects else "") + f" ; rôle {self.role}"

    def combat_spell(self, slot: int, stats: CharacterStats | None = None) -> CombatSpell:
        """Ce que la tactique en sait. PA et portée : ceux lus dans la fenêtre s'ils sont lisibles (ce que le jeu
        applique), sinon ceux de Solomonk.

        Dégâts : mean_damage, avec les caractéristiques du personnage quand elles sont connues. Zone : celle de
        l'effet de dégâts le plus fort (chez Solomonk, dégâts et recul d'un même sort ont la même zone)."""
        level = self.data
        effects = level["effets"]
        hurting = [e for e in effects if e["id"] in DAMAGE_EFFECTS]
        damage = mean_damage(effects, stats)
        strongest = max(hurting, key=lambda e: (e["min"] or 0) + (e["max"] or 0), default=None)
        zone = (strongest or {}).get("zone") or {}
        bonus = next((e for e in effects if e["id"] == RANGE_BONUS), None)
        boost = (boost_of(level) or stat_boost_of(level)) if self.role == "boost_degats" else None
        return CombatSpell(
            slot=slot,
            spell_id=self.key or f"sort_{self.entry.row + 1}",
            name=self.name,
            pa=self.entry.pa if self.entry.pa is not None else level["pa"],
            spell_range=self.entry.reach or tuple(level["portee"]),
            in_line=level["lancer_en_ligne"],
            needs_sight=level["ligne_de_vue"],
            range_modifiable=level["portee_modifiable"],
            per_turn=level["lancers_par_tour"],
            per_target=level["lancers_par_cible"],
            cooldown=level["relance"],
            damage=damage,
            push=max((e["max"] or 0 for e in effects if e["id"] == PUSH), default=0),
            zone=(zone.get("forme") or "point", zone.get("taille") or 0),
            range_bonus=bonus["max"] if bonus else 0,
            bonus_turns=bonus.get("tours", 0) if bonus else 0,
            role=self.role,
            boost_flat=boost[0] if boost else 0.0,
            boost_pct=boost[1] if boost else 0.0,
            boost_turns=boost[2] if boost else 0,
            team_boost=bool(boost) and team_boost(level),
            damage_base=mean_damage(effects),
            damage_lines=sum(max(1, e.get("tours", 0)) * (e.get("chance") or 100) / 100 for e in hurting),
        )


def _filled(entry: SpellEntry) -> int:
    """Ce qu'une lecture de ligne a pu lire : PA, portée, niveau (une même ligne vue sur deux pages de la liste peut
    être mieux lue sur l'une : 04/10, portées « 5-8 PO » lues sur une page, pas sur l'autre)."""
    return (entry.pa is not None) + (entry.reach is not None) + (entry.level is not None)


def _rows_text(rows: list[int]) -> str:
    """Lignes de la liste (numérotées à partir de 0) pour le journal : « lignes 1 à 10 », « ligne 12 », « lignes 1, 3 »."""
    rows = sorted(rows)
    if len(rows) == 1:
        return f"ligne {rows[0] + 1}"
    if rows == list(range(rows[0], rows[-1] + 1)):
        return f"lignes {rows[0] + 1} à {rows[-1] + 1}"
    return "lignes " + ", ".join(str(r + 1) for r in rows)


class SpellBook:
    """Sorts (et caractéristiques) d'un personnage pour la session : lus à l'écran ou repris de la dernière session.

    `session` : mode de début de session, lecture des caractéristiques, dossier de la mémoire des personnages
    (brain/memory.py) ; par défaut (tests) : la fenêtre des sorts seule, rien n'est retenu."""

    # Façon de défiler la liste qui a marché (molette, rail, flèche), pour toute la session : en arrière-plan, la
    # molette n'y fait rien, essayée puis abandonnée pour chacun des 8 personnages de l'équipe (15/10 : 0,9 s chacun).
    scroll_mode: str | None = None

    def __init__(self, game: Game, knowledge: dict[str, dict] | None = None, reference: dict | None = None,
                 classes: dict[str, str] | None = None, name: str | None = None,
                 session: SessionSettings | None = None) -> None:
        self.game = game
        self.knowledge = load_knowledge() if knowledge is None else knowledge
        self.reference = load_reference() if reference is None else reference
        self.classes = load_classes() if classes is None else {k.lower(): v for k, v in classes.items()}
        self.name = character_of(game) if name is None else name  # classe imposée, mémoire du personnage
        self.session = session or SessionSettings(stats=False)
        # Mémoire du personnage, retrouvé à son nom : sans nom (fenêtre sans titre de personnage), rien de retenu.
        self.memory = CharacterMemory(self.session.memory) if self.session.memory and self.name else None
        self.entries: list[SpellEntry] = []  # lus dans la fenêtre des sorts (ou repris de la dernière session)
        self.spells: dict[int, Spell] = {}  # ligne de la liste -> sort appris
        self.slots: dict[int, SpellEntry] = {}  # case de la barre -> sort
        self.class_key: str | None = None
        self.ranking: list[ClassMatch] = []  # classes, de la plus probable d'après la fenêtre à la moins probable
        self.stats: CharacterStats | None = None  # caractéristiques (brain/stats.py) ; None : dégâts de base
        self.read_done = False  # sorts lus (ou abandonnés après READ_TRIES essais) et caractéristiques faites
        self._tries = 0
        self._stats_done = not self.session.stats  # caractéristiques lues, reprises ou abandonnées
        self._resumed = self.session.mode != MEMORY  # reprise de la dernière session déjà tentée (mode mémoire)
        self._spells_read_at: str | None = None  # date de la lecture des sorts (reprise : celle de la dernière session)
        self._incomplete = False  # liste des sorts pas lue jusqu'en bas (elle n'a pas défilé jusqu'au bout)
        self._stats_read_at: str | None = None
        self._recalled_slots: dict[str, str] = {}  # cases de la barre retenues à la dernière session (journal)
        self._damage_logged = False

    def read(self) -> bool:
        """Début de session, une fois par personnage. Mode mémoire : reprend ce qui a été retenu (aucune fenêtre
        ouverte). Ce qui manque est lu à l'écran : fenêtre des sorts (sans ouvrir les fiches), puis caractéristiques
        (brain/stats.py) ; ce qui a été lu est retenu pour la session suivante. True si les sorts sont connus.

        Fenêtre des sorts pas lue : un nouvel essai au passage suivant hors combat, READ_TRIES fois au plus ;
        caractéristiques : StatsReader.read fait ses propres essais, puis le bot continue sans elles."""
        if not self._resumed:
            self._resumed = True
            self._resume()
        fresh = False
        if not self.entries and self._tries < READ_TRIES:
            fresh = self._read_window()
        if not self._stats_done:
            self._stats_done = True
            self.stats = StatsReader(self.game).read()
            if self.stats is not None:
                self._stats_read_at = now_text()
                fresh = True
                log.info("Caractéristiques : %s", self.stats.describe())
        if fresh:
            self._remember()
        self.read_done = (bool(self.entries) or self._tries >= READ_TRIES) and self._stats_done
        if self.read_done:
            self._log_damage()
        return bool(self.entries)

    def _read_window(self) -> bool:
        """Ouvre la fenêtre des sorts, lit la liste, referme. True si lu.

        Lignes numérotées dans toute la liste : la première visible d'après la poignée de la barre de défilement si la
        liste n'a pas pu être remontée en haut. Lecture incomplète (lignes d'avant ou d'après pas lues, moins de lignes
        que n'en compte la poignée) : complétée par la lecture retenue, et jamais retenue à sa place (_remember)."""
        self._tries += 1
        self._incomplete = False
        frame = self.game.look()
        if find_spell_window(frame) is None:
            button = find_spells_button(frame)
            if button is None:
                log.warning("Bouton de la fenêtre des sorts introuvable : portées supposées (combat.spell.range)")
                return False
            self.game.click(button, frame.layout.spread(8, 8))
            self.game.sleep(0.8)
            frame = self.game.look()
        frame = self._scroll_to_top(frame)
        bar = scroll_bar(frame)
        first = bar.first_row if bar is not None else 0
        entries = read_spell_window(frame, first_row=first)
        self.game.recorder.save(frame.image, "fenetre_sorts", lossless=True)
        if not entries:
            log.warning("Fenêtre des sorts introuvable ou illisible : portées supposées (combat.spell.range)")
            return False
        if first:
            self._incomplete = True
            log.warning("Liste des sorts restée défilée : lue à partir de la ligne %d (d'après la poignée de la barre de "
                        "défilement), les %d premières manquent", first + 1, first)
        entries = self._next_pages(frame, entries, first)
        expected = bar.rows if bar is not None else None
        if expected and len(entries) < expected:
            self._incomplete = True
            log.warning("Fenêtre des sorts : %d lignes lues sur %d (d'après la poignée de la barre de défilement)",
                        len(entries), expected)
        if self._incomplete:
            entries = self._completed_from_memory(entries)
        self.learn(entries)
        self._spells_read_at = now_text()
        self._log_spells(frame)
        self._close()
        return True

    def _scroll(self, frame: Frame, notches: int, mode: str = "molette") -> Frame | None:
        """Fait défiler la liste (`notches` crans de molette, un clic sur le rail sous la poignée, ou au-dessus pour
        « rail_haut », ou `notches` clics sur la flèche du bas), écarte la souris de la liste (une ligne survolée
        pourrait changer d'aspect), puis capture. None si le geste a été annulé."""
        corner = find_spell_window(frame)
        if corner is None:
            return None
        spread = frame.layout.spread(6, 6)
        if mode == "rail":
            bar = scroll_bar(frame)
            if bar is None:
                return None
            below = (ARROW_DOWN[0], (bar.bottom + THUMB_BOTTOM) / 2)  # entre le bas de la poignée et la flèche
            if self.game.click(window_point(frame, corner, below), frame.layout.spread(2, 4)) is False:
                return None
        elif mode == "fleche":
            for _ in range(notches):
                if self.game.click(window_point(frame, corner, ARROW_DOWN), frame.layout.spread(2, 2)) is False:
                    return None
        elif mode == "rail_haut":
            bar = scroll_bar(frame)
            if bar is None:
                return None
            above = (ARROW_DOWN[0], (THUMB_TOP + bar.top) / 2)  # entre le haut de la course et la poignée
            if self.game.click(window_point(frame, corner, above), frame.layout.spread(2, 4)) is False:
                return None
        elif self.game.wheel(window_point(frame, corner, LIST_POINT), notches, spread) is False:
            return None
        self.game.move(window_point(frame, corner, AWAY_POINT), spread)
        self.game.sleep(SCROLL_WAIT)
        return self.game.look()

    def _scroll_to_top(self, frame: Frame) -> Frame:
        """La liste peut être restée défilée depuis la dernière ouverture : remontée tout en haut (la ligne 0 de la
        lecture doit être le premier sort)."""
        bar = scroll_bar(frame)
        if bar is None or bar.at_top:
            return frame
        log.info("Liste des sorts défilée à l'ouverture : je la remonte en haut")
        if SpellBook.scroll_mode in (None, "molette"):  # molette pas encore essayée, ou qui a marché
            frame = self._scroll(frame, -TOP_NOTCHES) or frame
            bar = scroll_bar(frame)
            if bar is None or bar.at_top:
                return frame
            log.info("La molette ne remonte pas la liste des sorts : clics sur la barre de défilement, au-dessus de la "
                     "poignée")
        for _ in range(TOP_CLICKS):
            after = self._scroll(frame, 1, "rail_haut")
            if after is None:
                break
            frame, before, bar = after, bar, scroll_bar(after)
            if bar is None or bar.at_top:
                return frame
            if bar.top >= before.top:
                break  # le clic ne la fait pas remonter
        log.warning("Liste des sorts pas remontée en haut")
        return frame

    def _next_pages(self, frame: Frame, entries: list[SpellEntry], first_row: int = 0) -> list[SpellEntry]:
        """Lignes au-delà des 10 premières : la liste défile (molette, sinon flèche du bas) page par page jusqu'en
        bas ; chaque page est recalée sur la précédente par les icônes de ses lignes (page_shift) et ses nouvelles
        lignes sont ajoutées, numérotées dans toute la liste (`first_row` : ligne montrée en haut de la 1re page). Au
        moindre doute, on garde ce qui est déjà lu."""
        bar = scroll_bar(frame)
        if bar is None or not bar.scrolls:
            return entries
        mode = SpellBook.scroll_mode or "molette"
        notches = {"molette": FIRST_NOTCHES, "rail": 1, "fleche": ARROW_CLICKS}[mode]
        icons, offset, pages = row_icons(frame), first_row, 1
        while pages < MAX_PAGES + 3:  # le rail avance moins vite (~5 lignes) : quelques pages de plus
            after = self._scroll(frame, notches, mode)
            if after is None:
                log.warning("Défilement de la liste des sorts annulé : sorts lus jusqu'à la ligne %d", offset + ROWS)
                self._incomplete = True
                break
            frame, new_icons = after, row_icons(after)
            shift = page_shift(icons, new_icons)
            if shift is None:
                path = self.game.recorder.save(frame.image, "fenetre_sorts_page_illisible", lossless=True)
                log.warning("Liste des sorts défilée mais pas recalée sur la page d'avant (capture : %s) : sorts lus "
                            "jusqu'à la ligne %d seulement", path, offset + ROWS)
                self._incomplete = True
                break
            if shift == 0:
                if pages > 1 or (scroll_bar(frame) or bar).at_end:
                    break  # en bas de la liste (la liste a déjà défilé : ce n'est pas la molette qui manque)
                if mode == "fleche":
                    log.warning("La liste des sorts ne défile pas : sorts lus jusqu'à la ligne %d", offset + ROWS)
                    self._incomplete = True
                    break
                if mode == "molette":
                    log.info("La molette ne fait pas défiler la liste des sorts : clic sur la barre de défilement")
                    mode, notches = "rail", 1
                else:
                    log.info("Le clic sur la barre de défilement ne la fait pas défiler : flèche du bas")
                    mode, notches = "fleche", ARROW_CLICKS
                continue
            pages += 1
            offset += shift
            SpellBook.scroll_mode = mode
            page = read_spell_window(frame, first_row=offset)
            by_row = {e.row: e for e in entries}
            new = [e for e in page if e.row not in by_row]
            for entry in page:
                if entry.row in by_row and _filled(entry) > _filled(by_row[entry.row]):
                    by_row[entry.row] = entry  # ligne déjà vue, mieux lue sur cette page
            entries = sorted(list(by_row.values()) + new, key=lambda e: e.row)
            self.game.recorder.save(frame.image, f"fenetre_sorts_page_{pages}", lossless=True)
            log.info("Liste des sorts défilée de %d ligne(s) (%s) : lignes %d à %d, %d sort(s) de plus", shift,
                     {"rail": "clic sur la barre de défilement", "fleche": f"{notches} clic(s) sur la flèche"}.get(
                         mode, f"{notches} cran(s) de molette"), offset + 1,
                     offset + ROWS, len(new))
            bar = scroll_bar(frame) or bar
            if bar.at_end:
                break
            icons = new_icons
            if mode == "molette":
                notches = max(1, round(PAGE_STEP * notches / shift))  # lignes par cran mesurées
        bar = scroll_bar(frame) or bar
        log.info("Fenêtre des sorts : %d lignes lues en %d page(s) (poignée de la barre de défilement : y %d à %d)",
                 len(entries), pages, bar.top, bar.bottom)
        return entries

    def _completed_from_memory(self, entries: list[SpellEntry]) -> list[SpellEntry]:
        """Liste lue en partie (11/10 : en arrière-plan, elle ne défilait pas : 10 sorts sur 23, Tempête de Puissance et
        Maîtrise de l'Arc inconnues) : les lignes manquantes (suivantes, ou précédentes quand la liste n'a pas pu être
        remontée en haut, 18/10) reprises de la dernière lecture retenue, si elle va plus loin. Les lignes lues à
        l'instant gardent la priorité. Reprises d'une lecture elle-même incomplète : le tout le reste."""
        record = self.memory.load(self.name) if self.memory is not None else None
        spells = (record or {}).get("sorts") or {}
        lines = spells.get("lignes") if isinstance(spells, dict) else None
        stored = [entry_of(line) if isinstance(line, dict) else None for line in lines or []]
        if not stored or not all(stored) or len(stored) <= len(entries):
            log.warning("%s : liste des sorts incomplète, et rien de plus complet en mémoire : les sorts des lignes "
                        "manquantes resteront inconnus", self.name or "Personnage")
            return entries
        read = {entry.row for entry in entries}
        added = [entry for entry in stored if entry.row not in read]
        log.info("%s : liste des sorts incomplète : %s reprises de la lecture du %s", self.name,
                 _rows_text([entry.row for entry in added]), when(spells.get("lus_le")))
        self._incomplete = spells.get("complete") is False
        return sorted(entries + added, key=lambda e: e.row)

    def _log_spells(self, frame: Frame | None = None) -> None:
        for spell in self.spells.values():
            log.info("%s : %s", spell.name, spell.summary())
            if spell.remark:
                log.warning("%s : %s", spell.name, spell.remark)
            if not spell.data:
                path = self.game.recorder.save(frame.image, "sort_inconnu", lossless=True) if frame is not None else None
                log.warning("Sort n°%d inconnu%s : la tactique ne s'en servira pas", spell.entry.row + 1,
                            f" (capture : {path})" if path else "")

    def _resume(self) -> None:
        """Mode mémoire : sorts et caractéristiques retenus pour ce personnage à la dernière session ; ce qui manque
        sera lu à l'écran (journalisé)."""
        record = self.memory.load(self.name) if self.memory is not None else None
        spells = (record or {}).get("sorts") or {}
        lines = spells.get("lignes") if isinstance(spells, dict) else None
        entries = [entry_of(line) if isinstance(line, dict) else None for line in lines or []]
        if entries and all(entries) and spells.get("complete") is False:
            log.info("%s : sorts retenus d'une lecture incomplète (%d lignes, %s) : lus à l'écran", self.name,
                     len(entries), when(spells.get("lus_le")))
        elif entries and all(entries):
            self.learn(entries)
            self._spells_read_at = spells.get("lus_le")
            self._recalled_slots = {str(k): str(v) for k, v in (spells.get("cases") or {}).items()}
            self._log_spells()
        elif entries:
            log.warning("%s : sorts retenus abîmés (%s) : lus à l'écran", self.name, self.memory.path(self.name))
        stats = (record or {}).get("caracteristiques") if self.session.stats else None
        if isinstance(stats, dict):
            try:
                self.stats = CharacterStats.from_record(stats)
            except TypeError:
                log.warning("%s : caractéristiques retenues abîmées : lues à l'écran", self.name)
            else:
                self._stats_done = True
                self._stats_read_at = stats.get("lues_le")
                log.info("Caractéristiques : %s", self.stats.describe())
        who = self.name or "Personnage sans nom"
        if self.memory is None:
            log.warning("%s : mode mémoire sans mémoire des personnages (nom inconnu ou session.memory vide) : sorts%s "
                        "lus à l'écran", who, " et caractéristiques" if self.session.stats else "")
        elif not self.entries and self.stats is None:
            log.info("%s : rien de retenu de la dernière session : sorts%s lus à l'écran", who,
                     " et caractéristiques" if self.session.stats else "")
        elif not self.entries:
            log.info("%s : caractéristiques reprises de la dernière session (lues %s) ; sorts pas retenus : lus à "
                     "l'écran", who, when(self._stats_read_at))
        elif self.session.stats and self.stats is None:
            log.info("%s : sorts repris de la dernière session (lus %s) ; caractéristiques pas retenues : lues à "
                     "l'écran", who, when(self._spells_read_at))
        elif self.session.stats:
            spells_at, stats_at = when(self._spells_read_at), when(self._stats_read_at)
            log.info("%s : sorts et caractéristiques repris de la dernière session (lus %s%s)", who, spells_at,
                     "" if spells_at == stats_at else f", caractéristiques {stats_at}")
        else:
            log.info("%s : sorts repris de la dernière session (lus %s)", who, when(self._spells_read_at))

    def _remember(self) -> None:
        """Retient pour la session suivante ce qui est connu ; une partie pas lue cette fois (lecture ratée) garde ce
        qui avait été retenu avant. Une lecture des sorts qui a moins de lignes que celle retenue ne la remplace pas
        (18/10 : 11 lignes sur 21 chez Lestunes, retenues à la place des 21), sauf si celle-ci était incomplète."""
        if self.memory is None:
            return
        record = self.memory.load(self.name) or {}
        spells = record.get("sorts") if isinstance(record.get("sorts"), dict) else {}
        stored = spells.get("lignes") or []
        if self.entries and len(stored) > len(self.entries) and (self._incomplete or spells.get("complete") is not False):
            log.info("%s : lecture des sorts %s (%d lignes), la lecture retenue (%d lignes, %s) est gardée", self.name,
                     "incomplète" if self._incomplete else "plus courte", len(self.entries), len(stored),
                     when(spells.get("lus_le")))
        elif self.entries:
            record["sorts"] = self._spells_record()
        if self.stats is not None:
            record["caracteristiques"] = {"lues_le": self._stats_read_at or now_text(), **self.stats.record()}
        self.memory.save(self.name, record)

    def _spells_record(self) -> dict:
        """Les lignes de la fenêtre telles que lues (refaites à la reprise : learn) ; pour le lecteur, le sort de
        chaque ligne, ses dégâts moyens (de base et avec les caractéristiques) et sa case dans la barre."""
        slot_of = {entry.row: slot for slot, entry in self.slots.items()}
        lines = []
        for entry in self.entries:
            line = entry_record(entry)
            spell = self.spells.get(entry.row)
            if spell is not None:
                line.update(cle=spell.key, nom=spell.name, role=spell.role, case=slot_of.get(entry.row))
                if spell.data:
                    effects = spell.data["effets"]
                    line.update(degats_base=round(mean_damage(effects), 2),
                                degats=round(mean_damage(effects, self.stats), 2))
            lines.append(line)
        slots = {str(slot): self._slot_name(entry) for slot, entry in sorted(self.slots.items())}
        return {"lus_le": self._spells_read_at or now_text(), "classe": self.class_key, "complete": not self._incomplete,
                "lignes": lines, "cases": slots or self._recalled_slots}

    def _slot_name(self, entry: SpellEntry) -> str:
        spell = self.spells.get(entry.row)
        return (spell.key or spell.name) if spell is not None else f"ligne {entry.row + 1}"

    def _log_damage(self) -> None:
        """Une fois : dégâts moyens de chaque attaque, de base -> avec les caractéristiques."""
        if self.stats is None or self._damage_logged:
            return
        self._damage_logged = True
        for spell in self.spells.values():
            if not spell.data or spell.role == "autre":
                continue
            effects = spell.data["effets"]
            base, boosted = mean_damage(effects), mean_damage(effects, self.stats)
            if base <= 0:
                continue
            elements = [e.get("element") for e in effects if e["id"] in DAMAGE_EFFECTS]
            log.info("%s : %.0f -> %.0f dégâts moyens (%s)", spell.name, base, boosted, self.stats.explain(elements))

    def learn(self, entries: list[SpellEntry]) -> None:
        """Relie chaque sort appris de la liste à son niveau chez Solomonk ; gardé pour la session."""
        self.entries = entries
        keys = {e.row: identify(e) for e in entries if e.learned}
        self.ranking = rank_classes(entries, self.reference, keys)
        guessed = self.ranking[0].class_key if self.ranking and is_clear(self.ranking) else None
        forced = self._forced_class()
        self.class_key = forced or guessed
        self._log_class(forced, guessed)
        match = next((m for m in self.ranking if m.class_key == self.class_key), None)
        for entry in entries:
            if not entry.learned:
                continue
            spell = match.spells.get(entry.row) if match else None
            if spell is None:
                if match and entry.row in match.contradictions:
                    log.warning("Sort n°%d (%s) : aucun sort de la classe ne correspond (chiffres mal lus ?)",
                                entry.row + 1, entry.describe())
                spell = next((s for s in self.reference["sorts"].values() if s["cle"] == keys[entry.row]), None)
            if spell is None:
                self.spells[entry.row] = Spell(entry, keys[entry.row], f"sort n°{entry.row + 1}")
                continue
            level, remark = choose_level(spell, entry)
            role = (self.knowledge.get(spell["cle"]) or {}).get("role") or deduce_role(level)
            self.spells[entry.row] = Spell(entry, spell["cle"], spell["nom"], level, role, remark)

    def _forced_class(self) -> str | None:
        """Classe imposée à ce personnage dans config/sorts.yaml (classes), si elle y est et existe."""
        written = self.classes.get(self.name.lower()) if self.name else None
        if written is None:
            return None
        key = class_key_of(written, self.reference)
        if key is None:
            log.warning("Classe « %s » imposée à %s dans config/sorts.yaml inconnue (%s) : ignorée", written, self.name,
                        ", ".join(self.reference["classes"]))
        return key

    def _log_class(self, forced: str | None, guessed: str | None) -> None:
        """Classe retenue et pourquoi ; le doute est signalé, avec le réglage qui le lève."""
        names = {key: info["nom"] for key, info in self.reference["classes"].items()}
        first, second = (self.ranking + [None, None])[:2]
        seen = "rien de lisible"
        if first is not None and second is not None:
            seen = (f"{names[first.class_key]} {first.score:g} points, {first.explained} lignes lisibles sur "
                    f"{sum(readable(e) for e in self.entries)} expliquées ; ensuite {names[second.class_key]} "
                    f"{second.score:g} points")
        if forced is not None:
            log.info("Classe : %s (imposée dans config/sorts.yaml ; fenêtre des sorts : %s)", names[forced], seen)
            if guessed is not None and guessed != forced:
                log.warning("La fenêtre des sorts désigne plutôt la classe %s : classe imposée à vérifier", names[guessed])
        elif guessed is not None:
            log.info("Classe : %s (d'après la fenêtre des sorts : %s)", names[guessed], seen)
        else:
            log.warning("Classe incertaine d'après la fenêtre des sorts (%s) : sorts inconnus, sauf ceux reconnus à leur "
                        "icône ; sans eux, la tactique joue le sort de la case configurée (combat.spell). Pour imposer la "
                        "classe, dans config/sorts.yaml : classes: {%s: %s}", seen, self.name or "NomDuPersonnage",
                        first.class_key if first is not None else "enutrof")

    def _close(self) -> None:
        frame = self.game.look()
        close = find_close_button(frame)
        if close is None:
            return
        self.game.click(close, frame.layout.spread(4, 4))
        self.game.sleep(0.5)
        if find_spell_window(self.game.look()) is not None:
            log.warning("La fenêtre des sorts ne s'est pas fermée")

    def locate(self, frame: Frame) -> None:
        """Relie les sorts lus aux cases de la barre (barre de sorts affichée : en combat)."""
        if not self.entries or self.slots:
            return
        self.slots = locate_in_bar(frame, self.entries)
        for slot, entry in sorted(self.slots.items()):
            spell = self.spells.get(entry.row)
            log.info("Case %d de la barre : %s (%s)", slot, spell.name if spell else f"sort n°{entry.row + 1}",
                     entry.describe())
        missing = [s for s in self.spells.values() if s.entry not in self.slots.values()]
        if missing:
            log.info("Sorts appris absents de la barre : %s", ", ".join(s.name for s in missing))
        if not self.slots:
            return
        now = {str(slot): self._slot_name(entry) for slot, entry in self.slots.items()}
        moved = sorted(k for k in now.keys() | self._recalled_slots.keys() if now.get(k) != self._recalled_slots.get(k))
        if self._recalled_slots and moved:
            log.info("Barre de sorts changée depuis la dernière session (cases %s) : sorts retrouvés à leur icône",
                     ", ".join(moved))
        self._remember()  # cases de la barre, pour le journal de la session suivante

    def in_slot(self, slot: int) -> SpellEntry | None:
        return self.slots.get(slot)

    def bar_spells(self) -> list[CombatSpell]:
        """Tous les sorts de classe connus posés sur la barre, rôle « autre » compris (Bond...) : pour se repérer par la
        portée d'un sort quand aucune attaque n'a 2 cases de portée (11/10, l'Iop). Pas les sorts communs (Capture
        d'âmes : tout le monde ne l'a pas)."""
        return [spell.combat_spell(slot, self.stats) for slot, entry in sorted(self.slots.items())
                if (spell := self.spells.get(entry.row)) is not None and spell.data]

    def combat_spells(self) -> list[CombatSpell]:
        """Sorts de la barre utilisables par la tactique (connus de Solomonk), dégâts avec les caractéristiques."""
        spells = []
        for slot, entry in sorted(self.slots.items()):
            spell = self.spells.get(entry.row)
            if spell is not None and spell.data and spell.role != "autre":
                spells.append(spell.combat_spell(slot, self.stats))
        return spells
