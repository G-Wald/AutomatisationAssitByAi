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
import random
from dataclasses import dataclass
from enum import Enum

from bot.brain.game import WATCH_SLACK, Game
from bot.brain.popups import close_flash_menu
from bot.debug.draw import annotate
from bot.perception.frame import Frame
from bot.perception.groups import (OFF_COLUMN, TITLE_SHIFT, GroupTooltip, can_be_under_its_group, column_offset,
                                   find_group_tooltips, under_its_group)
from bot.perception.layout import Point
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


class Outcome(Enum):
    COMBAT = "combat lancé"
    NO_MENU = "pas de menu « Attaquer »"
    LEFT = "groupe parti"


@dataclass(frozen=True)
class EngageSettings:
    enabled: bool = True
    show_groups_key: str = "z"
    max_monsters: int = 8  # groupes plus gros ignorés (lignes de l'infobulle)
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


def _same_tooltip(a: GroupTooltip, b: GroupTooltip, frame: Frame) -> bool:
    """Même infobulle, à un petit déplacement du groupe près."""
    return abs(a.x - b.x) <= frame.layout.length(70) and abs(a.title_y - b.title_y) <= frame.layout.length(40)


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
        self._skipped: list[_Skip] = []
        self._no_menu: list[tuple[GroupTooltip, float]] = []  # (infobulle, quand) : clics sans menu « Attaquer »
        self._z_until = float("-inf")  # jusqu'à quand les infobulles de Z peuvent rester affichées (game.now)

    def engage(self) -> bool:
        """True si un combat a commencé."""
        self.missed = False
        self.none_found = False
        background = getattr(self.game, "background", False)  # arrière-plan : pas de survol, clics directs (08/10)
        frame = self._show_groups()
        if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
            return False  # un combat a commencé entre-temps (agression, lancement tardif)
        tooltips = [t for t in find_group_tooltips(frame) if t.monsters <= self.settings.max_monsters]
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
        tried: list[tuple[float, float]] = []
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
            self.game.click(button=button)  # là où est la souris virtuelle, jamais au pixel du clic précédent
            if button == "right":
                # Pas de menu : le personnage marche jusqu'au groupe et le combat se lance ; sinon, clic à côté (un
                # menu d'un autre joueur éventuel est fermé, capture, comme sans menu « Attaquer »).
                outcome = Outcome.COMBAT if self._wait_for_combat(RIGHT_CLICK_WAIT, tooltip, flash=aim) else \
                    self._no_menu_after_click(aim, tooltip, frame)
            else:
                outcome = self._after_click(aim, tooltip, frame)
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

    def _attack(self, group: Point, tooltip: GroupTooltip, z_frame: Frame) -> Outcome:
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
        """Clic sans menu « Attaquer » : le menu de Flash (clic droit dans le vide) est fermé d'un clic sous la barre de sorts ;
        un autre menu (personnage ou PNJ devant le groupe) est fermé par Échap, puis combat lancé directement ? Sinon
        le clic est tombé à côté du groupe (le personnage y marche)."""
        frame = self.game.look(park=False)
        if find_flash_menu(frame, around=group) is not None:  # avant les menus beiges : il en a l'air
            log.info("Le clic à côté du groupe a ouvert le menu de Flash : je le ferme (pas d'Échap)")
            close_flash_menu(self.game, frame, around=group)
        else:
            if find_context_menu(frame) is not None or find_small_menu(frame, around=group) is not None:
                log.info("Un autre menu s'est ouvert au clic (personnage ou PNJ sur le groupe ?) : je le ferme")
                self.game.press("escape")
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
