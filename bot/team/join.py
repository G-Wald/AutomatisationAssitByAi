"""Rejoindre le combat qu'un personnage de l'équipe vient de lancer (perception/join.py).

Pour chacun des autres personnages, sur la même carte, l'un après l'autre (team/loop.py repère ainsi la case de
chacun : la seule case de l'équipe qui vient de se remplir) :
1. l'épée du combat (souris écartée de la carte seulement si elle ne se voit pas : survolée, elle change d'aspect) ;
   elle met un instant à apparaître. Avec le dernier clic sur la carte du personnage qui a lancé le combat (le groupe
   attaqué, s'il est récent), elle n'est cherchée qu'à moins de NEAR_HINT de lui, la plus proche d'abord : ailleurs, ce
   sont les combats d'autres joueurs ; celles qui étaient déjà là avant son clic d'attaque (même carte, vue de sa
   fenêtre) sont écartées. Pas vue : fenêtres de ce suiveur fermées, souris écartée, nouvelle capture, jusqu'à
   SEARCH_UNTIL après le début du placement (21/10 : le décompte dure 30 à 40 s). Sans clic du principal : sur toute
   la carte, SWORD_WAIT au plus, la plus ressemblante d'abord ;
2. CLIC DROIT sur l'épée : le personnage rejoint aussitôt, sans menu (indication de l'utilisateur, 03/10 : un geste de
   moins). Si un menu « Combat / Rejoindre » s'ouvre malgré tout, clic sur « Rejoindre ». Seulement sur les parties
   vues de l'épée : pommeau, au-dessus des têtes, puis poignée, lame, garde ; si un clic a ouvert un autre menu
   (personnage ou PNJ debout sur l'épée, « Parler » le 01/10 ; menu de Flash, épée pas encore cliquable), il est
   fermé, puis le point suivant. Jamais de clic sur le groupe attaqué (20/10 : 0 entrée sur 32 clics « là où le
   principal a attaqué » : c'était l'épée en bois des monstres, ou rien) ;
3. le personnage entre au placement (il peut d'abord marcher jusqu'à l'épée) ; sinon, un second essai. 31/10 : dit
   par le serveur quand sa fenêtre est écoutée (GJK depuis le clic : entré ; « GA;903 » : refusé, sans second essai ni
   attente) ; l'écran seulement sans écoute vivante.
L'épée est au même endroit dans la fenêtre de chaque suiveur (même carte) : le point qui a marché pour le premier est
essayé d'abord pour les suivants, une fois l'épée revue à sa place dans LEUR fenêtre (25/10 : jamais de clic à
l'aveugle, un suiveur peut être ailleurs). Pour les suivants, le clic part aussitôt, sans attendre de voir le
personnage entrer (15/10, équipe de 8 : 0,7 s par suiveur, 4,4 s pour 7) ; l'entrée est vérifiée ensuite pour tous
(wait_placement), et celui qui n'est pas entré en QUICK_JOIN_WAIT refait tout (join). Un suiveur dont la carte lue
n'est pas celle du combat, et qui ne voit pas d'épée, n'est pas attendu (KNOWN_SWORD).
"""

import logging
import math
import random
import time
from collections.abc import Callable

from bot.perception.join import JOIN_SPREAD, PART_NAMES, Sword, find_fight_swords, find_join_entry, sword_part_scores
from bot.perception.layout import Box
from bot.perception.maplabel import MapLabel, read_map_label
from bot.brain.popups import close_flash_menu, close_small_menu
from bot.perception.chat import join_refused
from bot.perception.popups import find_context_menu, find_flash_menu, find_small_menu
from bot.state.scene import Phase, read_scene
from bot.team.member import Member, to_reference

log = logging.getLogger(__name__)

SWORD_WAIT = 4.0  # sans clic du principal : l'épée apparaît un instant après le début du combat
# 21/10 : avec son clic, l'épée est cherchée jusqu'à 25 s après le début du placement (décompte de 30 à 40 s) ; le
# 20/10, une fenêtre « Résultat du combat » ou le menu de Flash posés dessus la cachaient (211515, 205623), le
# survol de la souris l'éclaircissait.
SEARCH_UNTIL = 25.0
SEARCH_POLL = 0.4
# 20/10 (journaux 195355, 203719, 205623) : clic droit sur le pommeau 0,36 à 0,42 s après le début du placement, 3 fois :
# menu de Flash, l'épée dessinée mais pas encore cliquable ; le point suivant, 1 s plus tard, avait marché (et les
# suiveurs suivants sur le pommeau aussi). Premier clic au plus tôt SWORD_READY après le début du placement, et
# SWORD_SETTLE après l'apparition de l'épée pendant la recherche.
SWORD_READY, SWORD_SETTLE = 1.0, 0.5
MENU_WAIT = 0.4  # clic sur l'épée -> menu affiché, ou placement
JOIN_WAIT = 1.5  # clic droit sur l'épée -> placement : le temps pour le jeu de nous faire entrer
QUICK_JOIN_WAIT = 3.0  # clic aussitôt sur l'épée déjà trouvée -> placement, avant de tout refaire (marche comprise)
SWORDS_TRIED = 2
JOIN_TRIES = 2
HINT_MAX_AGE = 30.0  # dernier clic du personnage qui a lancé le combat : au-delà, il ne dit plus où est l'épée
# px de référence autour du dernier clic du principal (sur « Attaquer », près du groupe) : son épée y est, à 50 à 110 px
# le 20/10 (203456, 205623, 211515) ; plus loin, ce sont les combats d'autres joueurs.
NEAR_HINT = 250
OLD_SWORD = 6  # px de référence : épée au même endroit avant le clic d'attaque du principal, un autre combat
# Suiveurs suivants (18/10, utilisateur : position du clic sur l'épée un peu au hasard, sans jamais rater) : le 17/10,
# les 7 clics droits tombaient à 3 px près du même point. Une fois sur deux le point qui a marché, sinon l'autre
# partie haute vue de la même épée (pommeau ou poignée, au-dessus des têtes ; pas la garde, qu'un personnage
# debout sur l'épée cache : 01/10, menu « Parler » d'un PNJ).
QUICK_SAME_SPOT = 0.5
SPOT_PARTS = ("pommel", "handle")
# 25/10 (journaux 155334 et 181212) : un suiveur à Brâkmar (potion de rappel bue) dans 9 combats sur 34 (Elseiso 8
# fois, Waldarch 1). Le point de l'épée trouvé chez un autre était cliqué dans SA fenêtre sans regarder (« comme le
# précédent ») : 2 à 3 clics par combat sur la carte de Brâkmar (menus de Flash, bulle d'un PNJ ouverte), puis 20 s
# de recherche ; l'équipe au placement attendait (dernier suiveur entré à 26 à 28 s au lieu de 3 à 7). Désormais :
# - l'épée doit se voir à sa place dans la fenêtre du suiveur (centre à KNOWN_SWORD px de référence près) avant d'y
#   cliquer ; sinon, recherche comme pour le premier ;
# - pas d'épée et coordonnées lues d'une autre carte que celle du combat : il n'est pas là, abandon aussitôt.
KNOWN_SWORD = 12
KNOWN_BOX = 70  # px de référence autour de l'épée connue : où la revoir
# Épée annoncée par le serveur (case de son pied) : points visés au-dessus du centre de la case, px de référence.
TOLD_SWORD_UP = (12, 30)


def recent_click(member: Member) -> tuple[float, float] | None:
    """Dernier clic sur la carte du personnage (repère de référence), s'il est récent : près de l'épée de son combat."""
    last = getattr(member.game, "last_map_click", None)
    if last is None or time.monotonic() - last[1] > HINT_MAX_AGE:
        return None
    return last[0]


def _near_box(frame, hint: tuple[float, float], reach: float = NEAR_HINT) -> Box:
    """Carré de `reach` (NEAR_HINT : autour du clic du principal) dans la carte : où sont les centres des épées
    cherchées."""
    x, y = frame.layout.point(*hint)
    reach = frame.layout.length(reach)
    left, top, right, bottom = frame.layout.map_box
    return (max(left, round(x - reach)), max(top, round(y - reach)), min(right, round(x + reach)),
            min(bottom, round(y + reach)))


def _scores_text(scores: dict[str, float]) -> str:
    return ", ".join(f"{PART_NAMES[part]} {score:.2f}" for part, score in scores.items())


def _place(frame) -> MapLabel | None:
    """Nom et coordonnées lus de la carte de cette capture, None s'ils ne se lisent pas en entier."""
    label = read_map_label(frame)
    return label if label is not None and label.readable and label.coords is not None else None


def _digit_lost(read: tuple[int, int], expected: tuple[int, int]) -> bool:
    """`read` est `expected` amputé de son dernier chiffre sur une coordonnée (25/10 : « 0,-2 » lu pour 0,-29)."""
    pairs = list(zip(read, expected))
    cut = [abs(e) >= 10 and str(e)[:-1] == str(r) for r, e in pairs]
    return any(cut) and all(c or r == e for c, (r, e) in zip(cut, pairs))


def other_map(seen: MapLabel, coords: tuple[int, int], region: str | None = None) -> bool:
    """`seen` est sûrement une autre carte que celle du combat (`coords`, région `region` si reconnue) : autre région
    reconnue, ou autres coordonnées (pas un chiffre perdu à la lecture). Dans le doute, False : le suiveur cherche
    l'épée comme avant."""
    if seen.region_name and region and seen.region_name != region:
        return True
    if seen.coords == coords:
        return False
    return not (_digit_lost(seen.coords, coords) or _digit_lost(coords, seen.coords))



# Refus du serveur quand un personnage veut rejoindre un combat (« GA;903;<id>;<code> », Dofus 1.29). Codes d'après le
# protocole 1.29 connu, PAS ENCORE VUS EN JEU (31/10) : le message brut est toujours écrit à côté.
JOIN_REFUSALS = {"c": "combat déjà commencé", "t": "pas dans l'équipe", "f": "combat complet", "o": "trop loin",
                 "d": "personnage occupé", "p": "équipe fermée", "s": "en mode spectateur", "a": "pas de place"}


def _refusal(net, since: float) -> str:
    """31/10 (Walitcoin, salle 8 du donjon Bouftou : « Rejoindre » cliqué deux fois sans effet, cause inconnue) : ce
    que le serveur a dit depuis la tentative, pour le journal : le refus GA;903 décodé s'il y en a un, sinon les derniers
    messages (hors chat)."""
    if net is None or not hasattr(net, "heard_since"):
        return ""
    heard = [text for text in net.heard_since(since) if not text.startswith(("cM", "BN"))]
    for text in heard:
        if text.startswith("GA;903;"):
            code = text.split(";")[-1][:1]
            return f" (refusé par le serveur : {JOIN_REFUSALS.get(code, code or '?')}, « {text} »)"
    return f" (serveur : {' | '.join(heard[-12:]) or 'rien entendu'})"


def _listening(game):
    """Écoute du serveur de cette fenêtre, vivante (connection/network.py) ; None : l'écran en secours (Npcap absent,
    flux périmé)."""
    net = getattr(game, "net", None)
    return net if net is not None and hasattr(net, "fight_since") and net.alive() else None


class Joiner:
    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout  # par personnage
        self.started = time.monotonic()  # début du placement (new_fight)
        self.known: tuple[float, float] | None = None  # point de l'épée qui a marché (repère de référence), ce combat
        self.known_spread: tuple[float, float] = (3, 3)  # sa demi-étendue (repère de référence)
        # Autres parties vues de cette épée : (point, demi-étendue), repère de référence.
        self.spots: list[tuple[tuple[float, float], tuple[float, float]]] = []
        self.quick: dict[str, float] = {}  # personnage -> heure du clic parti sans vérification (ask rapide)
        # Épée introuvable pour un suiveur, ce combat : les suivants ne l'attendent plus. 16/10, Incarnam 6,2 : 7 suiveurs
        # x 2 recherches de 4 s, le principal 1 min 30 au placement avant son premier tour.
        self.sword_missing = False
        self.old: list[tuple[float, float]] = []  # centres des épées déjà là avant l'attaque (repère de référence)
        self.outsiders: set[str] = set()  # pas dans le groupe du principal (chat : « l'équipe est fermée ») : plus essayés
        self.known_center: tuple[float, float] | None = None  # centre de l'épée qui a marché (repère de référence)
        self.place: MapLabel | None = None  # carte du combat (capture du principal, ou du suiveur qui l'a rejoint)
        self.elsewhere: set[str] = set()  # suiveurs sur une autre carte que celle du combat : plus essayés, ce combat
        # Position du principal d'après son trajet (team/loop.py), quand le nom de la carte ne se lit pas.
        self.fight_coords: Callable[[], tuple[int, int] | None] | None = None
        self.host_id: int | None = None  # id du principal pour le serveur (écoute) : son épée est celle de son équipe
        # Touche tenue pendant le clic sur l'épée : les autres joueurs disparaissent, un joueur debout dessus ne prend
        # plus le clic (l'utilisateur, 26/10 ; comme sur les soleils, travel.hide_players_key). "" : sans touche.
        self.hide_key = ""
        # 31/10 : preuve par le serveur. Début de la dernière tentative de chaque suiveur (ask) : un GJK de sa fenêtre
        # depuis = entré ; un refus « GA;903 » depuis = refusé (raison au journal), sans attendre la fin du délai.
        self.asked: dict[str, float] = {}
        self.refused: dict[str, str] = {}  # personnage -> raison du refus (_refusal), tentative en cours

    def new_fight(self) -> None:
        self.started = time.monotonic()
        self.known = None
        self.known_spread = (3, 3)
        self.spots = []
        self.quick = {}
        self.sword_missing = False
        self.old = []
        self.known_center = None
        self.place = None
        self.elsewhere = set()
        self.asked = {}
        self.refused = {}

    def ignore_swords_before(self, frame, hint: tuple[float, float] | None) -> None:
        """Épées déjà visibles près du clic d'attaque du principal, sur sa capture d'avant ce clic : les combats
        d'autres joueurs, écartés ensuite. Sa carte lue sur cette capture : celle du combat."""
        if frame is not None:
            self.place = _place(frame)
        if frame is None or hint is None:
            return
        self.old = [to_reference(frame.layout, s.center) for s in self._near(frame, hint)]
        if self.old:
            log.info("Épée(s) d'autres combats déjà là avant l'attaque, écartée(s) : %s",
                     ", ".join(str(frame.layout.point(*c)) for c in self.old))

    def join(self, member: Member, hint: tuple[float, float] | None = None) -> bool:
        """True si le personnage est au placement du combat."""
        if member.name in self.outsiders:
            log.info("%s n'est pas dans le groupe du principal : il ne rejoint pas ce combat", member.name)
            return False
        if member.name in self.elsewhere:
            return False  # pas sur la carte du combat (dit par ask)
        if member.name in self.refused:  # refusé par le serveur à la tentative rapide (ask, puis wait_placement)
            log.warning("%s n'a pas rejoint le combat : il reste hors combat%s", member.name, self.refused[member.name])
            return False
        deadline = time.monotonic() + self.timeout
        started = time.monotonic()
        for _ in range(1 if self.sword_missing else JOIN_TRIES):
            if self.ask(member, hint, quick=False) and self.wait_placement(member, deadline):
                return True
            if member.name in self.elsewhere:
                return False
            if member.name in self.refused:
                # 31/10 (Walitcoin, « Rejoindre » cliqué deux fois sans effet, 20 s perdues, combat fait à 7) : le
                # serveur a dit non, un second essai n'y changerait rien.
                log.warning("%s n'a pas rejoint le combat : il reste hors combat%s", member.name,
                            self.refused[member.name])
                return False
            if not self.sword_missing and join_refused(member.game.look(park=False)):  # épée cliquée, pas entré
                # 23/10 : Pesopepe, 4 combats d'affilée, 25 s de recherche à chaque fois, l'équipe « fermée ».
                self.outsiders.add(member.name)
                log.warning("%s n'est pas dans le groupe du principal (chat : « l'équipe est fermée ») : il ne pourra "
                            "rejoindre aucun combat ; mets-le dans le groupe du principal", member.name)
                return False
            if time.monotonic() >= deadline:
                break
        log.warning("%s n'a pas rejoint le combat : il reste hors combat%s", member.name,
                    _refusal(getattr(member.game, "net", None), started))
        return False

    def ask(self, member: Member, hint: tuple[float, float] | None, quick: bool = True) -> bool:
        """Clic sur l'épée puis sur « Rejoindre ». True si c'est fait (ou s'il est déjà dans le combat). `quick` : si
        l'épée a déjà marché pour un autre, clic droit au même endroit sans attendre le résultat (vu par wait_placement)."""
        game = member.game
        self.asked[member.name] = time.monotonic()  # avant tout clic : la réponse du serveur vient après
        self.refused.pop(member.name, None)
        frame = self._look(member, park=False)  # pas de geste inutile : la souris n'est écartée que si l'épée est cachée
        if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
            return True
        if self._told_sword(member, frame):
            return True
        if member.name in self.refused:
            return False
        if self.known is not None and not self._known_seen(frame):
            log.info("%s : l'épée du combat n'est pas à sa place (%s) dans sa fenêtre, je la cherche", member.name,
                     frame.layout.point(*self.known_center))  # _known_seen : known_center connu
        elif self.known is not None:  # même épée que pour le suiveur précédent, vue à sa place dans cette fenêtre
            spot, spread = self._spot()
            point, spread = frame.layout.point(*spot), frame.layout.spread(*spread)
            if quick:
                if self._right_click(game, point, spread):
                    self.quick[member.name] = time.monotonic()
                    log.info("Je rejoins le combat (clic droit sur l'épée en %s, comme le précédent)", point)
                    return True
            elif self._try(member, point, spread):
                return True
            elif member.name in self.refused:
                return False
        now = time.monotonic()
        if self.sword_missing:
            end = now  # déjà introuvable pour un autre : une seule recherche
        elif hint is None:
            end = now + SWORD_WAIT
        else:
            end = max(now + SWORD_WAIT, self.started + SEARCH_UNTIL)
        appeared = False  # pas vue à la première capture : elle vient d'apparaître
        while True:
            if read_scene(frame).phase is not Phase.OUT_OF_COMBAT:
                return True
            swords = self._find(frame, hint)
            # Ailleurs ? Jugé sur une capture la souris écartée, ou d'emblée si l'épée se voit chez un autre suiveur.
            if not swords and (appeared or self.known is not None) and self._away(member, frame):
                return False
            if swords or time.monotonic() >= end:
                break
            appeared = True
            game.sleep(SEARCH_POLL)
            frame = self._look(member, park=True)  # souris écartée : survolée, l'épée s'éclaircit
        if not swords:
            box = _near_box(frame, hint) if hint is not None else None
            path = game.recorder.save(frame.image, "epee_introuvable", lossless=True)
            log.info("Je ne vois pas l'épée du combat %s (capture : %s ; meilleurs scores : %s)",
                     "près du clic du principal" if hint is not None else "sur la carte", path,
                     _scores_text(sword_part_scores(frame, box)))
            self.sword_missing = True  # les suivants ne l'attendent plus (17/10 : 7 x 2 recherches)
            return False
        # Épée pas encore cliquable juste après le début du placement, ou tout juste apparue (menu de Flash).
        wait = max(self.started + SWORD_READY - time.monotonic(), SWORD_SETTLE if appeared else 0.0)
        if wait > 0:
            game.sleep(wait)
        for sword in swords[:SWORDS_TRIED]:
            log.info("Épée du combat en %s : %s vu(s) (%s)", sword.center,
                     ", ".join(PART_NAMES[p] for p in sword.parts), _scores_text(sword.scores))
            missed = []  # 23/10 : point raté (menu de Flash) proposé ensuite aux suiveurs, Pesopepe resté dehors
            for (point, spread), part in zip(sword.points, sword.point_parts):
                if self._try(member, point, spread):
                    scale = frame.layout.scale
                    self.known = to_reference(frame.layout, point)
                    self.known_center = to_reference(frame.layout, sword.center)
                    if self.place is None:
                        self.place = _place(frame)
                    self.known_spread = (spread[0] / scale, spread[1] / scale)
                    self.spots = [(to_reference(frame.layout, p), (s[0] / scale, s[1] / scale))
                                  for (p, s), other in zip(sword.points, sword.point_parts)
                                  if other in SPOT_PARTS and p != point and p not in missed]
                    return True
                if member.name in self.refused:
                    return False  # refusé par le serveur : aucun autre point n'y changerait rien
                missed.append(point)
        path =game.recorder.save(game.look(park=False).image, "menu_rejoindre_absent", lossless=True)
        log.info("Pas de menu « Rejoindre » après le clic sur l'épée (capture : %s)", path)
        return False

    def _told_sword(self, member: Member, frame) -> bool:
        """Épée de l'équipe du principal annoncée par le serveur dans la fenêtre de ce suiveur (Gc+, Gt : écoute,
        26/10) : clic droit sur sa case, sans la chercher à l'écran (26/10, journal 212835 : épée d'Eldieso à moitié
        derrière un rocher, 25 s de recherche, resté hors combat). False si rien d'annoncé ou si le clic n'a pas pris
        (recherche à l'écran ensuite, comme avant)."""
        net = getattr(member.game, "net", None)
        cell = net.flag_cell(self.host_id) if net is not None and self.host_id is not None else None
        if cell is None:
            return False
        x, y = frame.grid.center(cell)
        spread = frame.layout.spread(3, 3)
        for up in TOLD_SWORD_UP:  # l'épée se dresse au-dessus de sa case : sa garde, puis sa lame
            point = (x, y - frame.layout.length(up))
            log.info("%s : épée de l'équipe du principal en %s d'après le jeu", member.name, cell)
            if self._try(member, point, spread):
                return True
            if member.name in self.refused:
                return False
        return False

    def _right_click(self, game, point, spread) -> bool:
        """Clic droit sur l'épée, les autres joueurs cachés (hide_key tenue) ; False si le clic a été annulé."""
        if not self.hide_key:
            return game.click(point, spread, button="right") is not False
        with game.holding(self.hide_key):
            return game.click(point, spread, button="right") is not False

    def _look(self, member: Member, park: bool):
        """Capture, après avoir fermé les fenêtres connues de ce suiveur (résultat du combat précédent, le 01/10 ;
        menu de Flash posé sur l'épée, le 20/10) : elles cachent l'épée."""
        game = member.game
        frame = game.look(park=park)
        for _ in range(4):
            if member.popups.describe(frame) is None or not member.popups.handle(frame, 0):
                break
            frame = game.look(park=park)
        return frame

    def _known_seen(self, frame) -> bool:
        """L'épée qui a marché pour un autre suiveur se voit à sa place dans cette fenêtre (même carte, rien dessus)."""
        if self.known_center is None:
            return True  # point connu sans épée retenue (ancien état) : comme avant
        x, y = frame.layout.point(*self.known_center)
        near = frame.layout.length(KNOWN_SWORD)
        return any(abs(s.center[0] - x) <= near and abs(s.center[1] - y) <= near
                   for s in find_fight_swords(frame, _near_box(frame, self.known_center, KNOWN_BOX)))

    def _fight_map(self) -> tuple[tuple[int, int] | None, str | None]:
        """Coordonnées et région de la carte du combat : lues sur la capture du principal (ou d'un suiveur entré), sinon
        sa position connue du trajet (fight_coords : le nom de 1,-33 ne se lit pas sur le décor de Taïnéla)."""
        if self.place is not None:
            return self.place.coords, self.place.region_name
        return (self.fight_coords() if self.fight_coords is not None else None), None

    def _away(self, member: Member, frame) -> bool:
        """Pas d'épée dans sa fenêtre, et sa carte lue est sûrement une autre que celle du combat : il n'est pas là
        (téléporté, resté en arrière), inutile de chercher ni de cliquer sur sa carte. Noté pour ce combat."""
        coords, region = self._fight_map()
        if coords is None:
            return False
        seen = _place(frame)
        if seen is None or not other_map(seen, coords, region):
            return False
        self.elsewhere.add(member.name)
        path = member.game.recorder.save(frame.image, "suiveur_ailleurs", lossless=True)
        log.warning("%s n'est pas sur la carte du combat (sa fenêtre montre %s, le combat est en %d,%d) : il ne peut "
                    "pas le rejoindre, je ne clique pas sur sa carte (capture : %s)", member.name, seen.describe(),
                    *coords, path)
        return True

    def _spot(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """Où le suiveur suivant clique (repère de référence) : le point qui a marché, ou une autre partie vue."""
        if not self.spots or random.random() < QUICK_SAME_SPOT:
            return self.known, self.known_spread
        return random.choice(self.spots)

    def _near(self, frame, hint: tuple[float, float]) -> list[Sword]:
        """Épées à moins de NEAR_HINT du clic du principal, la plus proche d'abord."""
        x, y = frame.layout.point(*hint)
        reach = frame.layout.length(NEAR_HINT)
        swords = [(math.hypot(s.center[0] - x, s.center[1] - y), s) for s in find_fight_swords(frame, _near_box(frame, hint))]
        return [s for d, s in sorted(swords, key=lambda e: e[0]) if d <= reach]

    def _find(self, frame, hint: tuple[float, float] | None) -> list[Sword]:
        """Épées du combat à rejoindre : près du clic du principal (sauf celles d'avant son attaque), sinon (sans clic
        récent) sur toute la carte, de la plus ressemblante à la moins ressemblante."""
        if hint is None:
            return find_fight_swords(frame)
        same = frame.layout.length(OLD_SWORD)
        old = [frame.layout.point(*c) for c in self.old]

        def new(swords: list[Sword]) -> list[Sword]:
            return [s for s in swords
                    if not any(abs(s.center[0] - ox) <= same and abs(s.center[1] - oy) <= same for ox, oy in old)]

        near = new(self._near(frame, hint))
        if near:
            return near
        # Rien près du clic du principal (21/10, Calanques 12,-24 : épée plantée à ~260 px du groupe attaqué, juste hors
        # de NEAR_HINT ; 4 suiveurs sur 7 ne l'ont pas rejointe) : toute la carte, la plus proche du clic d'abord.
        x, y = frame.layout.point(*hint)
        return sorted(new(find_fight_swords(frame)), key=lambda s: math.hypot(s.center[0] - x, s.center[1] - y))

    def _told(self, member: Member) -> bool | None:
        """Réponse du serveur à la tentative en cours de ce suiveur (depuis `asked`) : True entré (GJK de sa fenêtre, ou
        déjà en combat), False refusé (« GA;903 », raison notée dans `refused`), None rien encore ou pas d'écoute."""
        net = _listening(member.game)
        since = self.asked.get(member.name)
        if net is None or since is None:
            return None
        if net.fight_since(since) or net.in_fight():
            return True
        if member.name in self.refused:
            return False
        if net.join_refused_since(since) is not None:
            self.refused[member.name] = _refusal(net, since)
            log.info("%s : le serveur refuse qu'il rejoigne le combat%s", member.name, self.refused[member.name])
            return False
        return None

    def _try(self, member: Member, point, spread) -> bool:
        """Clic droit sur un point de l'épée : on rejoint directement ; « Rejoindre » si un menu s'ouvre quand même. Un
        autre menu (quelqu'un debout sur l'épée) : Échap. True si le personnage rejoint (ou est déjà dans le combat).
        31/10 : entrée (GJK) ou refus (GA;903) dits par le serveur, sans attendre l'écran."""
        game = member.game
        if not self._right_click(game, point, spread):
            return False  # clic annulé : une autre fenêtre sous la souris
        log.info("Je rejoins le combat (clic droit sur l'épée en %s)", point)
        end = time.monotonic() + JOIN_WAIT
        while True:
            game.sleep(MENU_WAIT)
            menu = game.look(park=False)
            told = self._told(member)
            if told is False:
                return False
            if told or read_scene(menu).phase is not Phase.OUT_OF_COMBAT:
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
            # le ferme pas et ouvre le Menu principal derrière. Cherché d'abord, fermé sous la barre de sorts ; le point
            # suivant de l'épée ensuite (20/10 : 3 fois sur le pommeau, la poignée avait marché aussitôt après).
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
        net = _listening(member.game)
        since = self.asked.get(member.name)
        if net is not None and since is not None:
            # 31/10 : le serveur d'abord. GJK de sa fenêtre depuis la tentative = entré ; « GA;903 » = refusé, aussitôt
            # (avant : 20 s à regarder l'écran, Walitcoin). Son silence jusqu'au délai : pas entré.
            while (told := self._told(member)) is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                net.wait_for(("fight_at", "join_refused_at"), since, left)
            if told:
                log.info("%s a rejoint le combat (dit par le serveur)", member.name)
                return True
            if told is None:
                log.info("%s : pas d'entrée dans le combat annoncée par le serveur%s", member.name,
                         _refusal(net, since))
            return False
        while True:
            phase = read_scene(member.game.look(park=False)).phase
            if phase is not Phase.OUT_OF_COMBAT:
                log.info("%s a rejoint le combat", member.name)
                return True
            if time.monotonic() >= deadline:
                return False
            member.game.sleep(0.4)
