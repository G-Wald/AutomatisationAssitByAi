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
près du groupe attaqué (dernier clic du principal), puis sur toute la carte.
"""

import logging
import math
import time

from bot.perception.join import JOIN_SPREAD, SWORD_SPREAD, Sword, find_fight_swords, find_join_entry
from bot.perception.layout import Layout
from bot.perception.popups import find_context_menu, find_small_menu
from bot.state.scene import Phase, read_scene
from bot.team.member import Member, to_reference

log = logging.getLogger(__name__)

SWORD_WAIT = 4.0  # l'épée apparaît un instant après le début du combat
MENU_WAIT = 0.4  # clic sur l'épée -> menu affiché, ou placement
JOIN_WAIT = 1.5  # clic droit sur l'épée -> placement : le temps pour le jeu de nous faire entrer
SWORDS_TRIED = 2
JOIN_TRIES = 2
HINT_MAX_AGE = 30.0  # dernier clic du personnage qui a lancé le combat : au-delà, il ne dit plus où est l'épée
NEAR_HINT = 350  # px de référence autour du dernier clic du principal : là où chercher l'épée d'abord


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

    def new_fight(self) -> None:
        self.known = None

    def join(self, member: Member, hint: tuple[float, float] | None = None) -> bool:
        """True si le personnage est au placement du combat."""
        deadline = time.monotonic() + self.timeout
        for _ in range(JOIN_TRIES):
            if self.ask(member, hint) and self.wait_placement(member, deadline):
                return True
            if time.monotonic() >= deadline:
                break
        log.warning("%s n'a pas rejoint le combat : il reste hors combat", member.name)
        return False

    def ask(self, member: Member, hint: tuple[float, float] | None) -> bool:
        """Clic sur l'épée puis sur « Rejoindre ». True si c'est fait (ou s'il est déjà dans le combat)."""
        game = member.game
        frame = game.look(park=False)  # pas de geste inutile : la souris n'est écartée que si l'épée ne se voit pas
        if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
            return True
        for _ in range(4):  # fenêtre restée ouverte (résultat du combat précédent, le 01/10) : elle cache l'épée
            if member.popups.describe(frame) is None or not member.popups.handle(frame, 0):
                break
            frame = game.look(park=False)
        if self.known is not None and self._try(game, frame.layout.point(*self.known), frame.layout.spread(3, 3)):
            return True  # même épée, même endroit que pour le suiveur précédent
        end = time.monotonic() + SWORD_WAIT
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
            return False
        for sword in _ranked(swords, hint, frame.layout)[:SWORDS_TRIED]:
            for point, spread in sword.points or ((sword.click, frame.layout.spread(*SWORD_SPREAD)),):
                if self._try(game, point, spread):
                    self.known = to_reference(frame.layout, point)
                    return True
        path = game.recorder.save(game.look(park=False).image, "menu_rejoindre_absent", lossless=True)
        log.info("Pas de menu « Rejoindre » après le clic sur l'épée (capture : %s)", path)
        return False

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
                return True
            entry = find_join_entry(menu)
            if entry is not None:
                log.info("Menu « Rejoindre » ouvert : je clique dessus")
                game.click(entry, menu.layout.spread(*JOIN_SPREAD))
                return True
            if find_small_menu(menu, around=point) is not None or find_context_menu(menu) is not None:
                log.info("Le clic sur l'épée en %s a ouvert le menu de quelqu'un qui est dessus : je le ferme et je vise "
                         "un autre endroit de l'épée", point)
                game.press("escape")
                game.sleep(0.3)
                return False
            if time.monotonic() >= end:
                return False

    def wait_placement(self, member: Member, deadline: float) -> bool:
        """Le personnage (qui marche peut-être jusqu'à l'épée) entre au placement."""
        while True:
            phase = read_scene(member.game.look(park=False)).phase
            if phase is not Phase.OUT_OF_COMBAT:
                log.info("%s a rejoint le combat", member.name)
                return True
            if time.monotonic() >= deadline:
                return False
            member.game.sleep(0.4)
