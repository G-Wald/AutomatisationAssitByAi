"""Rejoindre le combat qu'un personnage de l'équipe vient de lancer (perception/join.py).

Pour chacun des autres personnages, sur la même carte, l'un après l'autre (team/loop.py repère ainsi la case de
chacun : la seule case de l'équipe qui vient de se remplir) :
1. l'épée du combat (souris écartée de la carte seulement si elle ne se voit pas : survolée, elle change d'aspect) ;
   elle met un instant à apparaître.
   S'il y en a plusieurs (combats d'autres joueurs, ou une par équipe), la plus proche du dernier clic sur la carte
   du personnage qui a lancé le combat (le groupe attaqué), s'il est récent, sinon la plus ressemblante ;
2. CLIC DROIT sur l'épée : le personnage rejoint aussitôt, sans menu (indication de l'utilisateur, 03/10 : un geste de
   moins). Si un menu « Combat / Rejoindre » s'ouvre malgré tout, clic sur « Rejoindre ». Clic d'abord sur le
   pommeau, au-dessus des têtes, puis la poignée, puis la garde : si un clic a ouvert un autre menu (personnage ou PNJ
   debout sur l'épée, « Parler » le 01/10), Échap, puis le point suivant ;
3. le personnage entre au placement (il peut d'abord marcher jusqu'à l'épée) ; sinon, un second essai.
L'épée est au même endroit dans la fenêtre de chaque suiveur (même carte) : le point qui a marché pour le premier est
essayé d'abord pour les suivants, sans la chercher (0,4 s par recherche sur toute la carte). Elle est cherchée d'abord
près du groupe attaqué (dernier clic du principal), puis sur toute la carte. Pour les suivants, le clic part aussitôt,
sans attendre de voir le personnage entrer (15/10, équipe de 8 : 0,7 s par suiveur, 4,4 s pour 7) ; l'entrée est
vérifiée ensuite pour tous (wait_placement), et celui qui n'est pas entré en QUICK_JOIN_WAIT refait tout (join).
"""

import logging
import math
import random
import time

from bot.perception.join import JOIN_SPREAD, SWORD_SPREAD, Sword, find_fight_swords, find_join_entry
from bot.perception.layout import Layout
from bot.brain.popups import close_flash_menu, close_small_menu
from bot.perception.popups import find_context_menu, find_flash_menu, find_small_menu
from bot.state.scene import Phase, read_scene
from bot.team.member import Member, to_reference

log = logging.getLogger(__name__)

SWORD_WAIT = 4.0  # l'épée apparaît un instant après le début du combat
MENU_WAIT = 0.4  # clic sur l'épée -> menu affiché, ou placement
JOIN_WAIT = 1.5  # clic droit sur l'épée -> placement : le temps pour le jeu de nous faire entrer
QUICK_JOIN_WAIT = 3.0  # clic aussitôt sur l'épée déjà trouvée -> placement, avant de tout refaire (marche comprise)
SWORDS_TRIED = 2
JOIN_TRIES = 2
HINT_MAX_AGE = 30.0  # dernier clic du personnage qui a lancé le combat : au-delà, il ne dit plus où est l'épée
NEAR_HINT = 350  # px de référence autour du dernier clic du principal : là où chercher l'épée d'abord
# Suiveurs suivants (18/10, utilisateur : position du clic sur l'épée un peu au hasard, sans jamais rater) : le 17/10,
# les 7 clics droits tombaient à 3 px près du même point. Une fois sur deux le point qui a marché, sinon l'autre
# partie haute visible de la même épée (pommeau ou poignée, au-dessus des têtes ; pas la garde, qu'un personnage
# debout sur l'épée cache : 01/10, menu « Parler » d'un PNJ).
QUICK_SAME_SPOT = 0.5
SWORD_PARTS = 2


def recent_click(member: Member) -> tuple[float, float] | None:
    """Dernier clic sur la carte du personnage (repère de référence), s'il est récent : près de l'épée de son combat."""
    last = getattr(member.game, "last_map_click", None)
    if last is None or time.monotonic() - last[1] > HINT_MAX_AGE:
        return None
    return last[0]


def _ranked(swords: list[Sword], hint: tuple[float, float] | None, layout: Layout) -> list[Sword]:
    if hint is None:
        return swords  # déjà de la plus ressemblante à la moins ressemblante
    x, y = layout.point(*hint)
    return sorted(swords, key=lambda s: math.hypot(s.click[0] - x, s.click[1] - y))


class Joiner:
    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout  # par personnage
        self.known: tuple[float, float] | None = None  # point de l'épée qui a marché (repère de référence), ce combat
        # Autres parties visibles de cette épée : (point, demi-étendue), repère de référence.
        self.spots: list[tuple[tuple[float, float], tuple[float, float]]] = []
        self.quick: dict[str, float] = {}  # personnage -> heure du clic parti sans vérification (ask rapide)
        # Épée introuvable pour un suiveur, ce combat : les suivants ne l'attendent plus. 16/10, Incarnam 6,2 : 7 suiveurs
        # x 2 recherches de 4 s, le principal 1 min 30 au placement avant son premier tour.
        self.sword_missing = False

    def new_fight(self) -> None:
        self.known = None
        self.spots = []
        self.quick = {}
        self.sword_missing = False

    def join(self, member: Member, hint: tuple[float, float] | None = None) -> bool:
        """True si le personnage est au placement du combat."""
        deadline = time.monotonic() + self.timeout
        for _ in range(1 if self.sword_missing else JOIN_TRIES):
            if self.ask(member, hint, quick=False) and self.wait_placement(member, deadline):
                return True
            if time.monotonic() >= deadline:
                break
        log.warning("%s n'a pas rejoint le combat : il reste hors combat", member.name)
        return False

    def ask(self, member: Member, hint: tuple[float, float] | None, quick: bool = True) -> bool:
        """Clic sur l'épée puis sur « Rejoindre ». True si c'est fait (ou s'il est déjà dans le combat). `quick` : si
        l'épée a déjà marché pour un autre, clic droit au même endroit sans attendre le résultat (vu par wait_placement)."""
        game = member.game
        frame = game.look(park=False)  # pas de geste inutile : la souris n'est écartée que si l'épée ne se voit pas
        if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
            return True
        for _ in range(4):  # fenêtre restée ouverte (résultat du combat précédent, le 01/10) : elle cache l'épée
            if member.popups.describe(frame) is None or not member.popups.handle(frame, 0):
                break
            frame = game.look(park=False)
        if self.known is not None:  # même épée que pour le suiveur précédent
            spot, spread = self._spot()
            point, spread = frame.layout.point(*spot), frame.layout.spread(*spread)
            if quick:
                if game.click(point, spread, button="right"):
                    self.quick[member.name] = time.monotonic()
                    log.info("Je rejoins le combat (clic droit sur l'épée en %s, comme le précédent)", point)
                    return True
            elif self._try(game, point, spread):
                return True
        end = time.monotonic() + (0 if self.sword_missing else SWORD_WAIT)
        while True:
            if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                return True
            swords = self._find(frame, hint)
            if swords or time.monotonic() >= end:
                break
            game.sleep(0.4)
            frame = game.look(park=True)
        if not swords:
            path = game.recorder.save(frame.image, "epee_introuvable", lossless=True)
            log.info("Je ne vois pas l'épée du combat sur la carte (capture : %s)", path)
            # 16/10, groupe attaqué tout en haut (Incarnam 6,2) : épée coupée par le bord de l'écran et sous le nom de
            # la carte, introuvable pour les 7 suiveurs, le principal seul en combat. L'épée remplace le groupe : clic
            # droit là où le principal a cliqué pour attaquer.
            self.sword_missing = True  # même sans clic du principal à viser (17/10 : 7 x 2 recherches encore)
            if hint is not None:
                point = frame.layout.point(*hint)
                log.info("Je vise l'endroit où le principal a attaqué, en %s", point)
                if self._try(game, point, frame.layout.spread(*SWORD_SPREAD)):
                    self.known = hint
                    self.sword_missing = False
                    return True
            return False
        for sword in _ranked(swords, hint, frame.layout)[:SWORDS_TRIED]:
            for point, spread in sword.points or ((sword.click, frame.layout.spread(*SWORD_SPREAD)),):
                if self._try(game, point, spread):
                    self.known = to_reference(frame.layout, point)
                    scale = frame.layout.scale
                    self.spots = [(to_reference(frame.layout, p), (s[0] / scale, s[1] / scale))
                                  for p, s in sword.points[:SWORD_PARTS] if p != point]
                    return True
        path = game.recorder.save(game.look(park=False).image, "menu_rejoindre_absent", lossless=True)
        log.info("Pas de menu « Rejoindre » après le clic sur l'épée (capture : %s)", path)
        return False

    def _spot(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """Où le suiveur suivant clique (repère de référence) : le point qui a marché, ou une autre partie visible."""
        if not self.spots or random.random() < QUICK_SAME_SPOT:
            return self.known, (3, 3)
        return random.choice(self.spots)

    def _find(self, frame, hint: tuple[float, float] | None) -> list[Sword]:
        """Épées près du groupe attaqué d'abord (recherche 4 à 5 fois plus courte), sinon sur toute la carte."""
        if hint is not None:
            x, y = frame.layout.point(*hint)
            reach = frame.layout.length(NEAR_HINT)
            left, top, right, bottom = frame.layout.map_box
            box = (max(left, round(x - reach)), max(top, round(y - reach)), min(right, round(x + reach)),
                   min(bottom, round(y + reach)))
            swords = find_fight_swords(frame, box)
            if swords:
                return swords
        return find_fight_swords(frame)

    def _try(self, game, point, spread) -> bool:
        """Clic droit sur un point de l'épée : on rejoint directement ; « Rejoindre » si un menu s'ouvre quand même. Un
        autre menu (quelqu'un debout sur l'épée) : Échap. True si le personnage rejoint (ou est déjà dans le combat)."""
        if not game.click(point, spread, button="right"):
            return False  # clic annulé : une autre fenêtre sous la souris
        log.info("Je rejoins le combat (clic droit sur l'épée en %s)", point)
        end = time.monotonic() + JOIN_WAIT
        while True:
            game.sleep(MENU_WAIT)
            menu = game.look(park=False)
            if read_scene(menu).phase is not Phase.OUT_OF_COMBAT:
                # 17/10 (journal 155034) : entré dans le combat ET menu de Flash ouvert par le même clic, laissé sur la
                # timeline : chacun de ses tours illisible. Fermé aussitôt.
                if find_flash_menu(menu, around=point) is not None:
                    log.info("Entré dans le combat, mais le clic a aussi ouvert le menu de Flash : je le ferme")
                    close_flash_menu(game, menu, around=point)
                return True
            entry = find_join_entry(menu)
            if entry is not None:
                log.info("Menu « Rejoindre » ouvert : je clique dessus")
                game.click(entry, menu.layout.spread(*JOIN_SPREAD))
                return True
            # 17/10 (journaux 125352, 133806) : clic droit juste après l'attaque du principal, l'épée pas encore
            # dessinée : menu de Flash, pris pour le menu de quelqu'un (find_context_menu le trouve aussi) ; Échap ne
            # le ferme pas et ouvre le Menu principal derrière. Cherché d'abord, fermé sous la barre de sorts.
            if find_flash_menu(menu, around=point) is not None:
                log.info("Le clic sur l'épée en %s a ouvert le menu de Flash (rien dessous) : je le ferme (pas d'Échap) "
                         "et je vise un autre endroit de l'épée", point)
                close_flash_menu(game, menu, around=point)
                return False
            if find_small_menu(menu, around=point) is not None or find_context_menu(menu) is not None:
                log.info("Le clic sur l'épée en %s a ouvert le menu de quelqu'un qui est dessus : je le ferme et je vise "
                         "un autre endroit de l'épée", point)
                close_small_menu(game, menu, lambda f: find_small_menu(f, around=point) is not None
                                 or find_context_menu(f) is not None)
                return False
            if time.monotonic() >= end:
                return False

    def wait_placement(self, member: Member, deadline: float) -> bool:
        """Le personnage (qui marche peut-être jusqu'à l'épée) entre au placement. Après un clic rapide (ask), au plus
        QUICK_JOIN_WAIT après ce clic : sinon join refait tout (menu de quelqu'un debout sur l'épée...)."""
        clicked = self.quick.pop(member.name, None)
        if clicked is not None:
            deadline = min(deadline, clicked + QUICK_JOIN_WAIT)
        while True:
            phase = read_scene(member.game.look(park=False)).phase
            if phase is not Phase.OUT_OF_COMBAT:
                log.info("%s a rejoint le combat", member.name)
                return True
            if time.monotonic() >= deadline:
                return False
            member.game.sleep(0.4)
