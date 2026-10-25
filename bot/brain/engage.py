"""Hors combat : trouver un groupe de monstres et l'attaquer.

1. Touche Z enfoncée : chaque groupe de la carte affiche son infobulle (repérée par son mot « Niveau »).
2. Le groupe est juste en dessous de son infobulle, ou juste au-dessus s'il manquait la place en haut de la
   carte ; si l'infobulle est collée à un bord de la carte, elle est décalée et le groupe est plus près du bord.
   On survole d'abord le corps du monstre sur la case déduite de l'infobulle, puis ses cases voisines, jusqu'à voir
   apparaître la même infobulle SOUS LA SOURIS IMMOBILE (deux captures de suite) : une infobulle déjà là à
   l'arrivée a pu être montrée par la souris en passant sur le groupe pendant son trajet. Les groupes nets (ni
   collés à un bord, ni chevauchés) sont essayés d'abord.
3. Clic sur place (sur le monstre) -> menu « Monstres / Attaquer / Bestiaire » -> clic sur « Attaquer ».
4. Le personnage marche jusqu'au groupe : on attend le placement.

Groupe raté (introuvable, ou clic sans menu « Attaquer » deux fois de suite) : sauté un moment s'il y en a d'autres ;
s'il n'y a que lui sur la carte, retenté après un délai.

Arrière-plan (08/10, window.arriere_plan) : le survol ne montre rien (la vraie souris est ailleurs, le jeu reçoit aussitôt
« souris partie » : ni infobulle ni fiche). Z et le choix de l'infobulle ne changent pas ; le 2 devient un clic direct
sur les mêmes points, dans le même ordre (le 1er touche le groupe presque toujours : « Groupe trouvé au survol 1/N »
dans les journaux), PROBES au plus par groupe. Menu « Attaquer » (ou combat lancé) : suite comme aux 3 et 4. Sinon le
clic est tombé à côté : un autre menu est fermé, le combat guetté le temps que le personnage marche jusqu'au clic (clic
gauche), puis Z de nouveau (les groupes ont pu bouger) et le point suivant. Échec : clic sans menu, compté comme au
premier plan ; nouvelle recherche aussitôt, avec Z, plutôt que le groupe suivant de la capture d'avant la marche.
Bouton du clic : engage.clic_sonde (droit : à côté, le personnage ne bouge pas ; sur un groupe, menu ou attaque
directe : à essayer en jeu, les deux sont gérés). Clic droit à côté d'un petit monstre (14/10, un Piou) : le menu de
Flash s'ouvre ; Échap ne le ferme pas (il ouvre le Menu principal derrière), un clic gauche sous la barre de sorts si (pas dans le chat).
"""

import logging
import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from bot.brain.arena import ALL_CELLS, STEPS
from bot.brain.game import WATCH_SLACK, Game
from bot.brain.popups import close_flash_menu, close_npc_dialog, close_small_menu
from bot.brain.recovery import capture_once, keyboard_answers, mark_deaf, mark_heard, report_episode, unlock_click
from bot.debug.draw import annotate
from bot.perception.frame import Frame
from bot.perception.groups import (OFF_COLUMN, TITLE_SHIFT, GroupTooltip, can_be_under_its_group, column_offset,
                                   find_group_tooltips, read_group_level, under_its_group)
from bot.perception.layout import Layout, Point
from bot.perception.popups import find_context_menu, find_flash_menu, find_small_menu
from bot.perception.templates import match_template
from bot.state.scene import Phase, read_scene

log = logging.getLogger(__name__)

ATTACK_ENTRY, ATTACK_SPREAD = "menu_attaquer.png", (22, 5)
# Distances (px de référence) entre l'infobulle et l'endroit où survoler le groupe, par ordre d'essai. Le 03/10, sur
# les 14 attaques réussies des journaux (nombre de monstres relu), le clic qui a ouvert le menu était 12 à 16 px
# au-dessus du centre de la case du groupe (son corps) et 0 à 5 px à gauche de sa colonne, du côté donné par la case
# (groups.under_its_group) 14 fois sur 14. Sous l'infobulle, ce corps est à 50 px de son bas ; au-dessus, 35 px de
# son haut. Puis un peu plus bas et plus haut (monstre plus petit ou plus grand).
BELOW_OFFSETS, ABOVE_OFFSETS = (50, 62, 38), (35, 20, 52)
EDGE_SHIFTS = (0, 45, 90)  # infobulle collée à un bord : le groupe peut être plus près du bord
EDGE_COLUMNS = 3  # infobulle repoussée par un bord : colonnes de cases essayées vers ce bord (puis sous son centre)
# Puis les 4 cases voisines en diagonale (une demi-case de côté et de haut, réseau de grid.py), au niveau du corps :
# le groupe a pu faire un pas depuis la capture Z. Plus loin, Z est refait à la recherche suivante (l'infobulle suit
# son groupe) plutôt que de balayer large : jusqu'au 03/10, ±45 et ±90 px sur une rangée, sous une infobulle dont le
# bas était lu 100 à 200 px trop bas sur le sable (groups.LINE_DARK) : « des clics super larges ».
NEIGHBOURS = ((-1, 1), (1, 1), (-1, -1), (1, -1))  # (colonnes, rangées) en demi-cases
MAX_HOVERS = 16  # pas plus de survols qu'avant le 03/10 (infobulle collée à un bord en haut de la carte : 16)
# Survol d'un groupe : son infobulle s'affiche peu après (vue 0,2 s après l'arrivée de la souris aux 5 attaques
# réussies du 03/10). Une capture toutes les 0,1 s ; le groupe est trouvé quand l'infobulle est là sur deux captures de
# suite, la souris immobile : APPARUE après une capture sans elle, ou là depuis l'arrivée et encore là au bout de
# SHOWN_ON_ARRIVAL. Le 03/10 (journaux 184624, 191636, 193222), 4 groupes « trouvés » sur une infobulle vue dès la 1re
# capture (62 ms) : la souris était passée sur le groupe pendant son trajet entre deux survols ; le clic était tombé
# à côté (sous le groupe, ou de l'autre côté de l'infobulle), pas de menu « Attaquer », 4 fois sur 4.
HOVER_WAIT = 0.5
SHOWN_ON_ARRIVAL = 0.3
# Touche Z relâchée : ses infobulles peuvent rester affichées un instant (avant le 03/10, le 1er survol n'était regardé
# qu'au bout de 0,5 s). Pas de groupe « trouvé » avant ce délai. Le 03/10, aux 9 appuis sur Z suivis d'un 1er survol à
# côté du groupe, l'infobulle n'était plus là passé ce délai (sinon ce survol aurait été pris pour le groupe).
Z_LINGER = 0.25
MENU_WAIT = 1.0  # clic sur le groupe -> menu « Attaquer » (vu à la capture faite 0,4 s après, jusqu'au 03/10)
DIRECT_ATTACK_WAIT = 3.0  # pas de menu : combat lancé directement (agression en chemin) ?
# Clic droit sur un groupe (08/10, l'utilisateur) : le personnage y marche et lance le combat, sans menu. Le temps de la
# marche (le groupe peut être loin), avant de conclure à un clic à côté.
RIGHT_CLICK_WAIT = 5.0
ATTACK_WAIT = 8.0  # clic sur « Attaquer » -> placement : 2 à 3 s d'habitude ; au-delà, le groupe est parti
# Groupe raté, sauté pendant SKIP_FOR s s'il revient au même endroit et que la carte a d'autres groupes :
# - introuvable (caché derrière une maison : Z montre quand même son infobulle ; le 01/10, la même avait été
#   cherchée deux fois de suite, ~5 s chacune) ;
# - « trouvé » mais clic sans menu « Attaquer » NO_MENU_LIMIT fois de suite (une fois : retenté, après les autres).
SKIP_FOR = 60.0
NO_MENU_LIMIT = 2
NO_MENU_MEMORY = 60.0  # clic sans menu oublié au bout de ce temps
# Pas d'autre groupe sur la carte : les groupes sautés sont retentés au bout de ce délai. Le 03/10 (193222), les 2
# groupes de la carte, ratés, étaient « sautés » toutes les 1,7 s (Z à chaque fois) : plus aucun combat.
RETRY_SKIPPED = 10.0
# Arrière-plan (08/10) : clics directs sur un groupe, sans survol, au plus par groupe et par recherche. Chaque clic gauche
# à côté fait marcher le personnage jusque-là : on ne balaie pas les 7 à 16 points de hover_points.
PROBES = 2
PROBE_BUTTONS = {"gauche": "left", "droit": "right"}  # engage.clic_sonde -> bouton de la souris
WALK_POINTS = 3  # premiers points de survol d'un groupe dont la case est comparée à la carte officielle
# Groupe hors d'atteinte (17/10, Incarnam 3,0, journal 125352) : le clic droit le touche (pas de menu de Flash), le
# personnage marche, mais le combat ne se lance jamais (groupe de l'autre côté d'un obstacle). 68 clics ainsi en 20 min
# sur les groupes du côté droit de la carte, 7 min 21 s perdues : seul le groupe d'en bas à gauche était attaquable.
# UNREACHABLE_HITS clics de ce genre à moins de UNREACHABLE_RADIUS px de référence : les groupes de cet endroit sont
# laissés jusqu'au changement de carte (au plus UNREACHABLE_FOR s) ; s'il ne reste qu'eux, la carte compte comme vide
# (mode zone : carte suivante). Pas de 2e clic sur un groupe touché : dans les journaux du 17/10, les 3 clics 2/2 suivis
# d'un combat venaient tous après un 1er clic dans le vide (menu de Flash).
UNREACHABLE_RADIUS = 150  # deux groupes à 196 px l'un de l'autre (Calanques, 03/10) restent distincts
UNREACHABLE_HITS = 2
UNREACHABLE_FOR = 600.0
# Mémoire de ces endroits d'une visite à l'autre (17/10 : sur Incarnam 3,0, coupée en deux, ~1 min de clics perdus à
# chaque passage pour les réapprendre). Mode zone seulement, carte sûre (Traveler.place) : data/carte.json, par carte et
# par côté d'arrivée (ce n'est pas la même moitié qui est accessible), en px de référence. Une zone notée y laisse ses
# groupes d'emblée. Un combat lancé par un clic dans une zone notée la retire ; et toutes les UNREACHABLE_RECHECK
# visites où elle a été laissée, elle est revérifiée : un clic permis (au lieu de UNREACHABLE_HITS), qui la confirme
# s'il touche sans combat. Une zone notée par erreur n'est donc pas laissée pour toujours.
UNREACHABLE_RECHECK = 5
# Fenêtre sourde aux touches (20/10, brain/recovery.py) : sonde du clavier après tant de recherches Z vides de suite
# sur une même carte, et cycles « clic de déblocage, Z » au plus.
DEAF_PROBE_AFTER = 2
DEAF_CYCLES = 3


class Outcome(Enum):
    COMBAT = "combat lancé"
    NO_MENU = "pas de menu « Attaquer »"
    LEFT = "groupe parti"


@dataclass(frozen=True)
class EngageSettings:
    enabled: bool = True
    show_groups_key: str = "z"
    max_monsters: int = 8  # groupes plus gros ignorés (lignes de l'infobulle)
    max_level: int = 0  # groupes de niveau total plus haut ignorés (titre « Niveau N » de l'infobulle) ; 0 : sans limite
    # « auto » (max_level_auto) : level_ratio x niveau cumulé de l'équipe (niveaux retenus, team/member.py) ; max_level
    # sert tant qu'un niveau manque.
    max_level_auto: bool = False
    level_ratio: float = 0.7
    pause_between_fights: tuple[float, float] = (1.5, 2.5)
    retry_delay: float = 10.0  # attente quand aucun groupe n'est attaquable
    # Arrière-plan : bouton du clic direct sur le groupe (engage.clic_sonde). Gauche : à côté, le personnage y marche ;
    # droit : à côté, il ne bouge pas (menu « Attaquer » ou attaque directe sur un groupe : à essayer en jeu).
    probe_click: str = "droit"

    @classmethod
    def from_settings(cls, settings: dict) -> "EngageSettings":
        engage = settings.get("engage") or {}
        probe_click = str(engage.get("clic_sonde") or cls.probe_click).strip().lower()
        if probe_click not in PROBE_BUTTONS:
            raise ValueError(f"engage.clic_sonde doit valoir gauche ou droit (pas {probe_click!r})")
        return cls(
            enabled=bool(engage.get("enabled", cls.enabled)),
            show_groups_key=engage.get("show_groups_key") or cls.show_groups_key,
            max_monsters=int(engage.get("max_monsters", cls.max_monsters)),
            max_level=70 if str(engage.get("max_level")).strip().lower() == "auto"
            else int(engage.get("max_level", cls.max_level) or 0),
            max_level_auto=str(engage.get("max_level")).strip().lower() == "auto",
            level_ratio=float(engage.get("level_ratio", cls.level_ratio)),
            pause_between_fights=tuple(float(v) for v in engage.get("pause_between_fights", cls.pause_between_fights)),
            retry_delay=float(engage.get("retry_delay", cls.retry_delay)),
            probe_click=probe_click,
        )


@dataclass(frozen=True)
class _Skip:
    tooltip: GroupTooltip
    since: float  # game.now()
    until: float
    reason: str


@dataclass
class _Zone:
    """Endroit noté hors d'atteinte à une visite précédente (data/carte.json)."""
    x: float  # px de référence
    y: float
    noted: str  # date de la dernière confirmation
    left: int = 0  # visites où ses groupes ont été laissés d'emblée depuis
    recheck: bool = False  # cette visite : revérifiée (un clic permis)

    @classmethod
    def of(cls, record: dict) -> "_Zone | None":
        try:
            return cls(float(record["x"]), float(record["y"]), str(record.get("notee") or ""),
                       int(record.get("laissee") or 0))
        except (KeyError, TypeError, ValueError):
            return None

    def record(self) -> dict:
        return {"x": round(self.x), "y": round(self.y), "notee": self.noted, "laissee": self.left}

    def at(self, layout: Layout) -> Point:
        return layout.point(self.x, self.y)


def _same_tooltip(a: GroupTooltip, b: GroupTooltip, frame: Frame) -> bool:
    """Même infobulle, à un petit déplacement du groupe près."""
    return abs(a.x - b.x) <= frame.layout.length(70) and abs(a.title_y - b.title_y) <= frame.layout.length(40)


def _on_walk_area(tooltip: GroupTooltip, frame: Frame, area: set) -> bool:
    """Le groupe de l'infobulle peut être sur une case joignable : une des cases de ses premiers points de survol, ou
    une voisine, est dans `area` ; ou aucune n'est une case de la carte (pas de quoi l'écarter)."""
    cells = [frame.grid.cell_at(*point) for point in hover_points(tooltip, frame)[:WALK_POINTS]]
    cells = [c for c in cells if c is not None]
    if not any(c in ALL_CELLS for c in cells):
        return True
    return any((c[0] + di, c[1] + dj) in area for c in cells for di, dj in ((0, 0),) + STEPS)


def hover_points(tooltip: GroupTooltip, frame: Frame) -> list[tuple[float, float]]:
    """Où survoler pour trouver le groupe, par ordre d'essai : le corps du monstre sur la case déduite de l'infobulle
    (SOUS elle, presque toujours), un peu plus bas et plus haut, puis les cases voisines.

    En haut de la carte, l'infobulle passe sous son groupe faute de place au-dessus (groups.can_be_under_its_group) :
    la place juste au-dessus d'elle vient alors juste après la première sous elle (2e survol, ou après ses décalages
    vers le bord), ou en premier si la case du groupe le dit (groups.under_its_group). Le 03/10, 13 des 23 groupes du
    haut de la carte étaient au-dessus de leur infobulle : cherchés d'abord en dessous, ils étaient trouvés au 8e
    survol (4,4 s), ou jamais (infobulle un peu plus bas que la limite fixe d'alors, 220 px)."""
    layout, grid = frame.layout, frame.grid
    shifts = _edge_shifts(tooltip, frame)
    below = [tooltip.bottom + layout.length(d) for d in BELOW_OFFSETS]
    first, second = below, []
    possible, under = can_be_under_its_group(tooltip, layout), under_its_group(tooltip, frame)
    if possible or under:  # la case peut tromper (relief) : au-dessus en 1er seulement si le haut de carte le permet
        above = [tooltip.top - layout.length(d) for d in ABOVE_OFFSETS]
        first, second = (above, below) if possible and under else (below, above)
    rows = [first[0], *second[:1], *first[1:]]  # les deux places les plus probables d'abord
    points = [(tooltip.x + dx, y) for y in rows for dx in shifts]
    column_x = tooltip.x + shifts[0]
    points += [(column_x + columns * grid.half_w, first[0] + lines * grid.half_h) for columns, lines in NEIGHBOURS]
    points += [(tooltip.x + dx, y) for y in second[1:] for dx in shifts]
    kept: list[tuple[float, float]] = []
    for point in points:  # les décalages vers le bord d'une infobulle collée recoupent les cases voisines
        if layout.on_map(point) and all(abs(point[0] - x) + abs(point[1] - y) > layout.length(8) for x, y in kept):
            kept.append(point)
    return kept[:MAX_HOVERS]


def _edge_shifts(tooltip: GroupTooltip, frame: Frame) -> list[float]:
    """Décalages horizontaux où chercher le groupe. Infobulle collée à un bord : vers ce bord. Si elle n'est plus
    centrée sur une colonne de cases, le jeu l'a repoussée : le groupe est sur une des colonnes suivantes vers le bord
    (le 03/10 : la 1re, à 34 px de son centre, trouvée au 5e survol d'avant), sinon sous son centre d'abord."""
    if not tooltip.clipped:
        return [0.0]
    layout = frame.layout
    map_left, _, map_right, _ = layout.map_box
    toward_edge = -1 if tooltip.left - map_left < map_right - tooltip.right else 1
    offset = column_offset(tooltip, frame)
    if abs(offset) <= layout.length(OFF_COLUMN):
        return [toward_edge * layout.length(d) for d in EDGE_SHIFTS]
    step = frame.grid.half_w
    nearest = -offset - layout.length(TITLE_SHIFT)  # du titre à la colonne la plus proche (côté bord ou non)
    first = nearest if nearest * toward_edge > 0 else nearest + toward_edge * step
    columns = [first + toward_edge * k * step for k in range(EDGE_COLUMNS)]
    # Pas de colonne à moins d'une demi-case du bord : le groupe n'y tiendrait pas.
    return [dx for dx in columns if map_left + step / 2 <= tooltip.x + dx <= map_right - step / 2] + [0.0]


class _HoverWatch:
    """Guet de l'infobulle d'un groupe pendant un survol, la souris immobile (voir HOVER_WAIT)."""

    def __init__(self, engager: "Engager", tooltip: GroupTooltip) -> None:
        self.engager, self.tooltip = engager, tooltip
        self.game = engager.game
        self.posed = self.game.now()
        self.shown: list[bool] = []  # infobulle vue, capture après capture depuis l'arrivée de la souris
        self.first_seen: float | None = None

    def __call__(self, hover: Frame) -> bool:
        """Infobulle du groupe montrée par le survol, ou combat commencé (agression) : on arrête de guetter."""
        if read_scene(hover).phase is not Phase.OUT_OF_COMBAT:
            return True
        now = self.game.now()
        shown = any(_same_tooltip(self.tooltip, t, hover) for t in find_group_tooltips(hover))
        self.shown.append(shown)
        if not shown:
            self.first_seen = None
            return False
        if self.first_seen is None:
            self.first_seen = now
        if now < self.engager._z_until or len(self.shown) < 2 or not self.shown[-2]:
            return False
        return self.appeared or now - self.posed >= SHOWN_ON_ARRIVAL - WATCH_SLACK  # à 1 ms près, comme wait_until

    @property
    def appeared(self) -> bool:
        """Absente à une capture depuis l'arrivée de la souris, puis là : c'est le survol qui l'a montrée."""
        return not all(self.shown)

    def describe(self) -> str:
        now = self.game.now()
        if self.appeared:
            return (f"infobulle apparue {1000 * (self.first_seen - self.posed):.0f} ms après l'arrivée de la souris, "
                    f"encore là à {1000 * (now - self.posed):.0f} ms")
        return f"infobulle là dès l'arrivée de la souris et encore là à {1000 * (now - self.posed):.0f} ms"


class Engager:
    def __init__(self, game: Game, settings: EngageSettings) -> None:
        self.game = game
        self.settings = settings
        self.missed = False  # dernier échec : des groupes restent à essayer (on refait une recherche tout de suite)
        self.none_found = False  # dernière recherche : aucun groupe attaquable sur la carte (ou tous ratés)
        self.groups_seen = 0  # dernière recherche : groupes vus sur la carte, ratés compris (salle de donjon finie : 0)
        self._skipped: list[_Skip] = []
        self._no_menu: list[tuple[GroupTooltip, float]] = []  # (infobulle, quand) : clics sans menu « Attaquer »
        self._z_until = float("-inf")  # jusqu'à quand les infobulles de Z peuvent rester affichées (game.now)
        self._unreachable: list[tuple[Point, float]] = []  # (clic, quand) : groupe touché, combat jamais lancé
        self._probed: list[tuple[GroupTooltip, list[tuple[float, float]]]] = []  # points cliqués par groupe (_probe)
        self._hit = False  # dernier clic sans menu « Attaquer » : quelque chose a été touché (ni menu de Flash, ni autre)
        self._last_click: Point | None = None  # dernier clic sur un groupe de cette recherche
        # Mémoire des zones hors d'atteinte (remember_in) : où l'écrire (World de travel.py) et où l'on est
        # (Traveler.place : carte sûre et côté d'arrivée, ou None).
        self._memory = None
        self._where: Callable[[], tuple[str, str] | None] | None = None
        self._place: tuple[str, str] | None = None  # carte et côté d'arrivée dont les zones sont chargées
        self._zones: list[_Zone] = []
        # Z sans aucune infobulle : la carte est vide, ou la fenêtre n'a pas reçu la touche (20/10, S3, S4, S6, S8 :
        # fenêtre de Koalaloupour sourde aux touches, cartes crues vides l'une après l'autre). Sondé par la touche
        # `probe_key` (A : toute carte a au moins une sortie) après DEAF_PROBE_AFTER recherches vides de suite sur la
        # même carte, ou dès la 1re avec `probe_first` (mode zone : avant de changer de carte).
        self.probe_key = "a"  # "" : pas de sonde
        self.probe_first = False
        self.keyboard_ok: bool | None = None  # dernière sonde : True clavier reçu, False sourd malgré les clics, None aucune
        self._empty_z = 0  # recherches de suite sans aucune infobulle sur cette carte
        # Cases joignables à pied d'où l'on est, d'après la carte officielle (brain/arena.walk_area), ou None.
        self.walk_area: Callable[[], set | None] | None = None

    def remember_in(self, world, where: Callable[[], tuple[str, str] | None]) -> None:
        """Mode zone : zones hors d'atteinte gardées dans `world` (travel.World : unreachable_from,
        set_unreachable_from) pour la carte et le côté d'arrivée que donne `where`."""
        self._memory, self._where = world, where

    def new_map(self) -> None:
        """Changement de carte : les groupes ratés et les endroits hors d'atteinte de l'ancienne sont oubliés (ceux
        notés de la nouvelle sont chargés à la recherche suivante)."""
        self._skipped, self._no_menu, self._unreachable = [], [], []
        self._probed = []
        self._place, self._zones = None, []
        self._empty_z, self.keyboard_ok = 0, None

    def _arrive(self) -> None:
        """Zones notées de la carte où l'on est, pour le côté par lequel on y est arrivé, chargées à la 1re recherche
        (et de nouveau si la carte ou ce côté changent). Revérifiée tous les UNREACHABLE_RECHECK passages."""
        place = self._where() if self._where is not None and self._memory is not None else None
        if place is None or place == self._place:
            return
        self._place = place
        self._zones = [z for z in map(_Zone.of, self._memory.unreachable_from(*place)) if z is not None]
        if not self._zones:
            return
        for zone in self._zones:
            zone.recheck = zone.left >= UNREACHABLE_RECHECK
            if not zone.recheck:
                zone.left += 1
        rechecked = sum(z.recheck for z in self._zones)
        log.info("Carte %s, arrivée par le %s : %d zone(s) hors d'atteinte notée(s) à un passage précédent, ses groupes "
                 "laissés d'emblée%s", *place, len(self._zones),
                 f" ({rechecked} à revérifier d'un clic)" if rechecked else "")
        self._save()

    def _save(self) -> None:
        if self._place is not None and self._memory is not None:
            self._memory.set_unreachable_from(*self._place, [z.record() for z in self._zones])

    def _zone_near(self, point: tuple[float, float], layout: Layout) -> _Zone | None:
        radius = layout.length(UNREACHABLE_RADIUS)
        near = [(math.dist(point, z.at(layout)), z) for z in self._zones]
        return min(((d, z) for d, z in near if d <= radius), key=lambda dz: dz[0], default=(None, None))[1]

    def _hits_near(self, point: tuple[float, float], layout: Layout) -> int:
        """Clics qui ont touché sans combat près de `point`, plus une zone notée : comptée UNREACHABLE_HITS (laissée
        d'emblée), ou un de moins à revérifier."""
        radius = layout.length(UNREACHABLE_RADIUS)
        hits = sum(math.dist(point, p) <= radius for p, _ in self._unreachable)
        zone = self._zone_near(point, layout)
        return hits + (0 if zone is None else UNREACHABLE_HITS - 1 if zone.recheck else UNREACHABLE_HITS)

    def _out_of_reach(self, tooltip: GroupTooltip, frame: Frame) -> bool:
        points = hover_points(tooltip, frame)
        return bool(points) and self._hits_near(points[0], frame.layout) >= UNREACHABLE_HITS

    def _note_unreachable(self, aim: Point, frame: Frame) -> None:
        """Clic qui a touché sans que le combat se lance : noté ; s'il confirme l'endroit hors d'atteinte, celui-ci est
        gardé pour les passages suivants (carte sûre, mode zone)."""
        self._unreachable.append((aim, self.game.now()))
        layout = frame.layout
        if self._place is None or self._hits_near(aim, layout) < UNREACHABLE_HITS:
            return
        radius = layout.length(UNREACHABLE_RADIUS)
        near = [layout.to_reference(p) for p, _ in self._unreachable if math.dist(aim, p) <= radius]
        x, y = (sum(c) / len(near) for c in zip(*near))
        zone = self._zone_near(aim, layout)
        noted = datetime.now().isoformat(timespec="seconds")
        if zone is None:
            self._zones.append(_Zone(x, y, noted))
            log.info("Endroit hors d'atteinte noté pour les prochains passages (carte %s, arrivée par le %s)",
                     *self._place)
        else:
            zone.x, zone.y, zone.noted, zone.left, zone.recheck = x, y, noted, 0, False
            log.info("Zone hors d'atteinte confirmée (carte %s, arrivée par le %s)", *self._place)
        self._save()

    def _reached(self, point: Point | None, frame: Frame) -> None:
        """Combat lancé par un clic en `point` : cet endroit n'est pas hors d'atteinte (clics notés et zone oubliés)."""
        if point is None:
            return
        layout = frame.layout
        radius = layout.length(UNREACHABLE_RADIUS)
        self._unreachable = [(p, when) for p, when in self._unreachable if math.dist(point, p) > radius]
        kept = [z for z in self._zones if math.dist(point, z.at(layout)) > radius]
        if len(kept) < len(self._zones):
            self._zones = kept
            log.info("Combat lancé dans une zone notée hors d'atteinte : je l'oublie (carte %s, arrivée par le %s)",
                     *self._place)
            self._save()

    def engage(self) -> bool:
        """True si un combat a commencé."""
        self.missed = False
        self.none_found = False
        self._last_click = None
        self._arrive()
        background = getattr(self.game, "background", False)  # arrière-plan : pas de survol, clics directs (08/10)
        frame = self._show_groups()
        if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
            return False  # un combat a commencé entre-temps (agression, lancement tardif)
        seen = find_group_tooltips(frame)
        self.keyboard_ok = None
        self._empty_z = 0 if seen else self._empty_z + 1
        if not seen and self.probe_key and (self.probe_first or self._empty_z >= DEAF_PROBE_AFTER):
            self._empty_z = 0
            frame, seen = self._z_unheard(frame)
            if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                return False
        self.groups_seen = len(seen)
        tooltips = [t for t in seen if t.monsters <= self.settings.max_monsters]
        if self.settings.max_level or self.settings.max_level_auto:
            tooltips = self._not_too_strong(frame, tooltips)
        self.game.recorder.save(frame.image, "groupes_z", quality=92)  # capture brute, pour réanalyse
        firsts = [points[0] for points in (hover_points(t, frame) for t in tooltips) if points]  # 1er survol prévu
        self.game.recorder.save(
            annotate(frame, boxes=[t.box for t in tooltips],
                     marks=[((t.x, t.title_y), f"{t.monsters}{' bord' if t.clipped else ''}") for t in tooltips]
                     + [(point, "") for point in firsts],
                     text=f"{len(tooltips)} groupe(s), + : 1er {'clic' if background else 'survol'}"),
            "groupes",
        )
        if not tooltips:
            log.info("Aucun groupe de monstres attaquable sur la carte")
            self.none_found = True
            return False
        log.info("%d groupe(s) de monstres sur la carte", len(tooltips))
        now = self.game.now()
        self._unreachable = [(p, when) for p, when in self._unreachable if now - when < UNREACHABLE_FOR]
        reachable = [t for t in tooltips if not self._out_of_reach(t, frame)]
        if len(reachable) < len(tooltips):
            log.info("Je laisse %d groupe(s) hors d'atteinte (touchés sans que le combat se lance, au même endroit, "
                     "ou zone notée à un passage précédent)", len(tooltips) - len(reachable))
        if not reachable:
            log.info("Plus que des groupes hors d'atteinte sur la carte")
            self.none_found = True
            return False
        area = self.walk_area() if self.walk_area is not None else None
        if area:
            # Carte officielle (brain/arena.py) : un groupe sur une case qu'on ne peut pas joindre à pied d'ici (en bas
            # d'une falaise, de l'autre côté de l'eau) n'est pas cliqué (25/10, Taïnéla 5,-30 : deux clics perdus).
            kept = [t for t in reachable if _on_walk_area(t, frame, area)]
            if len(kept) < len(reachable):
                log.info("Je laisse %d groupe(s) sur des cases que la carte officielle dit injoignables d'ici",
                         len(reachable) - len(kept))
            reachable = kept
            if not reachable:
                log.info("Plus que des groupes injoignables d'ici sur la carte")
                self.none_found = True
                return False
        tooltips = reachable
        candidates = self._candidates(tooltips, frame)
        if not candidates:
            self.none_found = True  # la boucle attend (engage.retry_delay), ou change de carte en mode zone
            return False

        def difficulty(t: GroupTooltip) -> tuple:
            overlapped = any(t is not o and t.overlaps(o) for o in tooltips)
            return self._no_menu_count(t, frame), t.clipped, overlapped, t.monsters, random.random()

        for tooltip in sorted(candidates, key=difficulty)[:3]:
            if background:
                outcome = self._probe(frame, tooltip)
            else:
                point = self._find_group(frame, tooltip)
                if point is Outcome.COMBAT:
                    return True
                if point is None:
                    self._skip(tooltip, "introuvable")
                    continue
                outcome = self._attack(point, tooltip, frame)
            if outcome is Outcome.COMBAT:
                self._forget(tooltip, frame)
                self._reached(self._last_click, frame)
                return True
            if outcome is Outcome.LEFT:
                self.missed = True  # il a bougé : nouvelle recherche aussitôt, avec Z
                return False
            self._no_menu.append((tooltip, self.game.now()))
            count = self._no_menu_count(tooltip, frame)
            if count >= NO_MENU_LIMIT:
                log.info("Clic sans menu « Attaquer » %d fois de suite sur ce groupe : je passe aux autres", count)
                self._skip(tooltip, "clic sans menu")
            else:
                log.info("Je le retenterai à la prochaine recherche, après les autres groupes")
            if background:
                break  # le personnage a pu marcher, les groupes bouger : nouvelle recherche aussitôt, avec Z
        # Des groupes restent à essayer : nouvelle recherche aussitôt (ils ont pu bouger). Tous ratés : on attend.
        self.missed = any(not self._skipped_now(t, frame) for t in tooltips)
        self.none_found = not self.missed
        return False

    def level_limit(self) -> int:
        """Niveau total maximal d'un groupe attaqué : engage.max_level, ou (auto) level_ratio x niveau cumulé de
        l'équipe (team_levels : un par personnage, None si jamais lu ; max_level tant qu'il en manque un)."""
        levels = getattr(self, "team_levels", None) or []
        known = [level for level in levels if level is not None]
        if self.settings.max_level_auto and levels and 2 * len(known) >= len(levels):
            # Niveaux manquants (le principal n'est pas dans sa propre liste d'amis) : la moyenne des autres (25/10).
            return round(self.settings.level_ratio * sum(known) * len(levels) / len(known))
        return self.settings.max_level

    def _not_too_strong(self, frame: Frame, tooltips: list[GroupTooltip]) -> list[GroupTooltip]:
        """Groupes de niveau total au plus engage.max_level (23/10 : zones à choisir pour l'équipe, Porkass jusqu'au
        niveau 30 par monstre). Niveau illisible : gardé, et dit."""
        kept, limit = [], self.level_limit()
        for tooltip in tooltips:
            level = read_group_level(frame, tooltip)
            if level is not None and level > limit:
                log.info("Groupe de niveau %d laissé (niveau maximal : %d)", level, limit)
                continue
            if level is None:
                log.debug("Niveau du groupe illisible : je le garde")
            kept.append(tooltip)
        return kept

    def _candidates(self, tooltips: list[GroupTooltip], frame: Frame) -> list[GroupTooltip]:
        """Groupes à essayer : pas ceux ratés récemment, sauf s'il n'y a qu'eux sur la carte et qu'ils l'ont été il y a
        plus de RETRY_SKIPPED s."""
        now = self.game.now()
        self._skipped = [s for s in self._skipped if s.until > now]
        self._no_menu = [(t, when) for t, when in self._no_menu if now - when < NO_MENU_MEMORY]
        findable = [t for t in tooltips if not self._skipped_now(t, frame)]
        if len(findable) == len(tooltips):
            return findable
        skipped = len(tooltips) - len(findable)
        if findable:
            reasons = sorted({s.reason for t in tooltips for s in self._skips_of(t, frame)})
            log.info("Je saute %d groupe(s) raté(s) à une recherche précédente (%s)", skipped, ", ".join(reasons))
            return findable
        last_miss = [max(s.since for s in self._skips_of(t, frame)) for t in tooltips]
        retry = [t for t, since in zip(tooltips, last_miss) if now - since >= RETRY_SKIPPED]
        if not retry:
            log.info("Pas d'autre groupe sur la carte que %d raté(s) à une recherche précédente : je les retenterai "
                     "dans %s s", skipped, f"{min(last_miss) + RETRY_SKIPPED - now:.0f}")
            return []
        log.info("Pas d'autre groupe sur la carte : je retente %d groupe(s) raté(s) il y a plus de %d s",
                 len(retry), RETRY_SKIPPED)
        for tooltip in retry:
            self._forget(tooltip, frame)
        return retry

    def _skips_of(self, tooltip: GroupTooltip, frame: Frame) -> list[_Skip]:
        return [s for s in self._skipped if _same_tooltip(tooltip, s.tooltip, frame)]

    def _skipped_now(self, tooltip: GroupTooltip, frame: Frame) -> bool:
        return bool(self._skips_of(tooltip, frame))

    def _no_menu_count(self, tooltip: GroupTooltip, frame: Frame) -> int:
        return sum(_same_tooltip(tooltip, t, frame) for t, _ in self._no_menu)

    def _skip(self, tooltip: GroupTooltip, reason: str) -> None:
        now = self.game.now()
        self._skipped.append(_Skip(tooltip, now, now + SKIP_FOR, reason))

    def _forget(self, tooltip: GroupTooltip, frame: Frame) -> None:
        self._skipped = [s for s in self._skipped if not _same_tooltip(tooltip, s.tooltip, frame)]
        self._no_menu = [(t, when) for t, when in self._no_menu if not _same_tooltip(tooltip, t, frame)]

    def _z_unheard(self, frame: Frame) -> tuple[Frame, list[GroupTooltip]]:
        """Z n'a montré aucune infobulle : sonde de la touche A (sorties marquées). Sorties vues : la carte est vraiment
        vide. Rien : clic de déblocage (brain/recovery.py), puis Z de nouveau tout de suite ; DEAF_CYCLES fois au plus,
        puis un avertissement (keyboard_ok False : la boucle ne conclut pas à une carte vide, la surveillance du progrès
        prend le relais). Renvoie la dernière capture Z et ses infobulles."""
        unlocked = False  # un clic de déblocage a été fait dans cet épisode
        for _ in range(DEAF_CYCLES):
            if keyboard_answers(self.game, self.probe_key):
                self.keyboard_ok = True
                mark_heard(self.game)
                if unlocked:
                    report_episode("touche Z", True, "sorties vues avec A : la carte est vide")
                return frame, []
            mark_deaf(self.game, f"ni Z ni {self.probe_key.upper()} n'ont rien montré")
            unlocked = unlock_click(self.game) or unlocked
            frame = self._show_groups()
            seen = find_group_tooltips(frame)
            if seen or read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                self.keyboard_ok = True
                mark_heard(self.game)
                report_episode("touche Z", True, f"{len(seen)} groupe(s) vu(s) après le clic")
                return frame, seen
        self.keyboard_ok = False
        report_episode("touche Z", False, f"{DEAF_CYCLES} essais")
        path = capture_once(self.game, frame.image, "clavier_sourd")
        log.warning("Ni Z ni %s ne montrent rien malgré %d clics de déblocage : la fenêtre ne reçoit sans doute pas les "
                    "touches. Je ne conclus pas que la carte est vide%s", self.probe_key.upper(), DEAF_CYCLES,
                    f" (capture : {path})" if path else "")
        return frame, []

    def _show_groups(self) -> Frame:
        self.game.look()  # écarte la souris de la carte
        with self.game.holding(self.settings.show_groups_key):
            self.game.sleep(0.4)
            frame = self.game.look()
        self._z_until = self.game.now() + Z_LINGER
        return frame

    def _find_group(self, frame: Frame, tooltip: GroupTooltip) -> Point | Outcome | None:
        """Où cliquer (la souris y est déjà), None si le groupe reste introuvable, Outcome.COMBAT si un combat a
        commencé pendant la recherche (agression)."""
        candidates = hover_points(tooltip, frame)
        started = self.game.now()
        for rank, point in enumerate(candidates, 1):
            aim = self.game.move(point, frame.layout.spread(4, 3))
            watch = _HoverWatch(self, tooltip)
            hover = self.game.wait_until(watch, HOVER_WAIT, park=False)
            if hover is None:
                if any(watch.shown):
                    log.debug("Survol %d/%d : infobulle vue à %d capture(s) sur %d sans confirmation, la souris "
                              "immobile (passage sur le groupe pendant le trajet ?) : pas là", rank, len(candidates),
                              sum(watch.shown), len(watch.shown))
                continue
            if read_scene(hover).phase is not Phase.OUT_OF_COMBAT:
                return Outcome.COMBAT
            place = "au-dessus de" if point[1] < tooltip.top else "sous"
            shift = f", à {point[0] - tooltip.x:+.0f} px" if abs(point[0] - tooltip.x) > frame.layout.length(8) else ""
            log.info("Groupe trouvé au survol %d/%d, %s son infobulle%s ; %s (recherche : %s s)", rank,
                     len(candidates), place, shift, watch.describe(),
                     f"{self.game.now() - started:.1f}".replace(".", ","))
            return aim
        path = self.game.recorder.save(annotate(frame, boxes=[tooltip.box], marks=[(p, "") for p in candidates]),
                                       "groupe_introuvable", lossless=True)
        log.info("Je ne trouve pas le groupe près de son infobulle en (%.0f, %.0f)%s (capture : %s)",
                 tooltip.x, tooltip.title_y, ", collée à un bord" if tooltip.clipped else "", path)
        return None

    def _probe(self, frame: Frame, tooltip: GroupTooltip) -> Outcome:
        """Arrière-plan : le groupe cherché par des clics directs, sans survol (voir le docstring du module). Clic sur
        le premier point de hover_points pas encore cliqué, PROBES fois au plus, puis comme au premier plan
        (_after_click). Pas de menu : Z de nouveau avant le clic suivant, les points recalculés depuis l'infobulle
        revue (le groupe a pu bouger : ses points ne sont plus ceux déjà cliqués, le 1er est retenté). NO_MENU si aucun
        clic n'a ouvert le menu, ou si l'infobulle n'est plus là (groupe parti)."""
        button = PROBE_BUTTONS[self.settings.probe_click]
        tried = self._probed_points(tooltip, frame)
        for probe in range(1, PROBES + 1):
            near = frame.layout.length(8)
            point = next((p for p in hover_points(tooltip, frame)
                          if all(abs(p[0] - x) + abs(p[1] - y) > near for x, y in tried)), None)
            if point is None:
                break
            tried.append(point)
            aim = self.game.move(point, frame.layout.spread(4, 3))  # souris virtuelle : rien n'est survolé ni envoyé
            log.info("Groupe visé sans survol (arrière-plan), clic %s %d/%d en (%d, %d)", self.settings.probe_click,
                     probe, PROBES, *aim)
            self._last_click = aim
            self.game.click(button=button)  # là où est la souris virtuelle, jamais au pixel du clic précédent
            if button == "right":
                # Pas de menu : le personnage marche jusqu'au groupe et le combat se lance ; sinon, clic à côté (un
                # menu d'un autre joueur éventuel est fermé, capture, comme sans menu « Attaquer »).
                outcome = Outcome.COMBAT if self._wait_for_combat(RIGHT_CLICK_WAIT, tooltip, flash=aim) else \
                    self._no_menu_after_click(aim, tooltip, frame)
            else:
                outcome = self._after_click(aim, tooltip, frame)
            if outcome is Outcome.NO_MENU and button == "right" and self._hit:
                tried.remove(point)  # le groupe est là (touché) : le même point à la recherche suivante, pour confirmer
                self._note_unreachable(aim, frame)
                log.info("Groupe touché mais pas de combat : hors d'atteinte ? (endroit noté, pas d'autre clic dessus)")
                return outcome
            if outcome is not Outcome.NO_MENU or probe == PROBES:
                return outcome
            log.info("Clic à côté du groupe%s : je réaffiche les groupes (Z) avant le clic suivant",
                     " (le personnage a pu y marcher)" if button == "left" else "")
            frame = self._show_groups()
            if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                return Outcome.COMBAT
            again = [t for t in find_group_tooltips(frame) if _same_tooltip(tooltip, t, frame)]
            if not again:
                log.info("Son infobulle n'est plus près de (%.0f, %.0f) : le groupe est parti", tooltip.x,
                         tooltip.title_y)
                return Outcome.NO_MENU
            previous = tooltip
            tooltip = min(again, key=lambda t: abs(t.x - previous.x) + abs(t.title_y - previous.title_y))
        return Outcome.NO_MENU

    def _probed_points(self, tooltip: GroupTooltip, frame: Frame) -> list[tuple[float, float]]:
        """Points déjà cliqués sur ce groupe (même infobulle) aux recherches précédentes de cette carte : la recherche
        suivante continue la liste de hover_points au lieu de la recommencer (25/10, Taïnéla 1,-34 : infobulle collée au
        bord, le groupe 3 colonnes plus loin ; les deux mêmes points recliqués à chaque recherche, puis le groupe
        abandonné et la carte quittée avec 3 groupes dessus)."""
        for known, points in self._probed:
            if _same_tooltip(known, tooltip, frame):
                return points
        points: list[tuple[float, float]] = []
        self._probed.append((tooltip, points))
        return points

    def _attack(self, group: Point, tooltip: GroupTooltip, z_frame: Frame) -> Outcome:
        self._last_click = group
        self.game.click()  # sur place : la souris survole le groupe
        return self._after_click(group, tooltip, z_frame)

    def _after_click(self, group: Point, tooltip: GroupTooltip, z_frame: Frame) -> Outcome:
        """Le groupe vient d'être cliqué en `group` : menu « Attaquer » -> clic sur « Attaquer », placement attendu ;
        ou combat lancé directement ; sinon _no_menu_after_click."""

        def answered(frame: Frame) -> bool:
            return (read_scene(frame).phase is not Phase.OUT_OF_COMBAT
                    or match_template(frame, ATTACK_ENTRY, box=frame.layout.map_box) is not None)

        frame = self.game.wait_until(answered, MENU_WAIT, park=False)
        if frame is not None and read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
            return Outcome.COMBAT  # attaque directe, sans menu
        entry = None if frame is None else match_template(frame, ATTACK_ENTRY, box=frame.layout.map_box)
        if entry is None:
            return self._no_menu_after_click(group, tooltip, z_frame)
        log.info("J'attaque le groupe en %s", group)
        self.game.click(entry[0], frame.layout.spread(*ATTACK_SPREAD))
        if self._wait_for_combat(ATTACK_WAIT, tooltip):
            return Outcome.COMBAT
        path = self.game.recorder.save(self.game.look().image, "combat_pas_lance")
        log.info("Le combat n'a pas commencé (capture : %s) : je cherche un autre groupe", path)
        return Outcome.LEFT

    def _no_menu_after_click(self, group: Point, tooltip: GroupTooltip, z_frame: Frame) -> Outcome:
        """Clic sans menu « Attaquer » : le menu de Flash (clic droit dans le vide) ou un autre menu (ressource,
        personnage devant le groupe) est fermé d'un clic sous la barre de sorts, la bulle d'un PNJ par sa croix ; puis
        combat lancé directement ? Sinon le clic est tombé à côté du groupe (le personnage y marche)."""
        frame = self.game.look(park=False)
        self._hit = False
        if find_flash_menu(frame, around=group) is not None:  # avant les menus beiges : il en a l'air
            log.info("Le clic à côté du groupe a ouvert le menu de Flash : je le ferme (pas d'Échap)")
            close_flash_menu(self.game, frame, around=group)
        elif close_npc_dialog(self.game, frame):
            pass  # PNJ cliqué à la place du groupe (17/10, Matu Vuh) : pas un groupe hors d'atteinte
        else:
            if find_context_menu(frame) is not None or find_small_menu(frame, around=group) is not None:
                log.info("Un autre menu s'est ouvert au clic (ressource, personnage ou PNJ sur le groupe ?) : je le "
                         "ferme d'un clic sous la barre de sorts")
                close_small_menu(self.game, frame, lambda f: find_context_menu(f) is not None
                                 or find_small_menu(f, around=group) is not None)
            else:
                self._hit = True
            if self._wait_for_combat(DIRECT_ATTACK_WAIT):
                return Outcome.COMBAT
        path = self.game.recorder.save(
            annotate(frame, boxes=[tooltip.box], marks=[(group, "clic")],
                     text="pas de menu Attaquer (cadre : infobulle a la capture Z)"),
            "menu_attaquer_absent", lossless=True)
        log.info("Pas de menu « Attaquer » après le clic sur le groupe en %s (capture : %s)", group, path)
        return Outcome.NO_MENU

    def _wait_for_combat(self, seconds: float, tooltip: GroupTooltip | None = None, flash: Point | None = None) -> bool:
        """Pendant que le personnage marche : combat lancé ? Toutes les 2,4 s, le groupe est-il encore là ? `flash` :
        le clic droit qui vient d'être fait ; s'il a ouvert le menu de Flash, il est tombé à côté (pas d'attente)."""
        for check in range(round(seconds / 0.3)):
            frame = self.game.look(park=False)
            if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                return True
            if flash is not None and find_flash_menu(frame, around=flash) is not None:
                return False
            if tooltip is not None and check and check % 8 == 0 and self._group_left(tooltip):
                log.info("Le groupe s'est déplacé pendant que j'y allais")
                return False
            self.game.sleep(0.3)
        return False

    def _group_left(self, tooltip: GroupTooltip) -> bool:
        with self.game.holding(self.settings.show_groups_key):
            self.game.sleep(0.3)
            frame = self.game.look(park=False)
        self._z_until = self.game.now() + Z_LINGER  # le groupe suivant peut être cherché juste après
        if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
            return False
        layout = frame.layout
        return not any(abs(t.x - tooltip.x) <= layout.length(150) and abs(t.title_y - tooltip.title_y) <= layout.length(100)
                       for t in find_group_tooltips(frame))
