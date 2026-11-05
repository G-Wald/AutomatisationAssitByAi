"""Échanges entre nos personnages, et mode banque « principal » : les suiveurs donnent leurs ressources au principal,
qui les dépose dans son coffre (30/10, demande de l'utilisateur : « on centralise toutes les ressources sur un
personnage, par étapes : on ne peut pas accepter un échange qui fait dépasser nos pods »).

Validé en jeu le 30/10 (Fourboun et Enu-six, échange annulé à la fin) :
- ouvrir : clic sur le portrait du personnage dans le cadre du groupe (déplié), menu à son nom, « Echange » ; chez lui,
  « X te propose de faire un échange » puis « Oui » ; le serveur : ERK<demandeur>|<invité>|1 puis ECK1 aux deux ;
- une pile entière en arrière-plan (Ctrl impossible) : glisser LENT de la pile vers l'offre, fenêtre de quantité
  [min] [max] [nombre] [coche] ; le serveur : EMKO+<uid>|<quantité totale> (moi), EmKO+<uid>|<quantité>|<modèle>| (lui) ;
- fermer : la croix, EV aux deux.
Pas encore vu en jeu : « Accepter » des deux côtés et l'échange conclu (EK, EVa d'après le client 1.29). Le principal
vérifie, avant d'accepter, que l'offre reçue tient dans ses pods ; tout ce qui ne passe pas est déposé dans le coffre du
suiveur, comme en mode « chacun » : jamais pire que lui.

Par lot : le suiveur pose ses piles une à une (une unité d'abord pour connaître l'objet et son poids, puis le nombre qui
tient), tant que le poids de l'offre tient dans la place libre du principal (pods max - portés - RESERVE) ; les deux
acceptent ; le principal dépose le lot dans son coffre ; lot suivant, jusqu'à ce que le suiveur n'ait plus de ressource.
"""

import logging
import math
import time

from bot.alerts import tell_user
from bot.brain.popups import close_any_menu
from bot.brain.travel import escape_menu
from bot.team.bank import DOOR, EXIT, BankVisit, best_text, grid_point, quantity_points, slow_drag

log = logging.getLogger(__name__)

# Positions : repère de référence (fenêtre de 1918 x 1008, ancrée à droite), relevées le 30/10 (fenêtres de 1920 x 1057).
GROUP_TOGGLE = (1908, 92)  # flèche qui déplie / replie le cadre du groupe
PORTRAIT = (1887, 167)  # premier portrait du cadre déplié
PORTRAIT_STEP = 54.4
PORTRAITS = 8
LIFE_BAR = (1897, 152, 1907, 182)  # barre de vie rouge à droite du premier portrait : cadre déplié
MENU_BOX = (-520, -340, 10, 480)  # menu du portrait : à sa gauche ; remonté pour les portraits du bas (30/10 :
# titre d'Elseiso, 8e portrait, hors de la zone lue avec -40)
YES = (1122, 426)  # « Oui » de « X te propose de faire un échange. Acceptes-tu ? »
RESOURCES_TAB = (1421, 186)  # onglet ressources du sac, fenêtre d'échange
FIRST_STACK = (1355, 288)  # première pile du sac
OFFER_SLOT = (1517, 586)  # case vide de mon offre où lâcher une pile (la 2e : la 1re prend l'unité posée d'abord)
SLOT_ICON = (-6, -2, 18, 18)  # partie de l'icône d'une case comparée avant / après (sans le nombre, en haut à gauche)
ACCEPT = (1786, 723)  # « Accepter »
CLOSE = (1864, 136)  # croix de la fenêtre d'échange
# Un objet précis (offer_item : clés de donjon, 30/10). À CALIBRER EN JEU (jamais relevées) : la liste des types du
# sac de la fenêtre d'échange (comme « Tous types » de l'inventaire, brain/equipment.py), le milieu de la liste
# ouverte, l'écart entre deux cases du sac et les cases par ligne (pile qui n'est pas la première du type).
TRADE_FILTER = (1420, 230)  # à calibrer en jeu
TRADE_SEARCH = (1665, 231)  # loupe du sac de l'échange (31/10, capture de l'échange de Pesopepe)
STACK_STEP, STACK_COLUMNS = 58.2, 9  # grille du sac de l'échange : 61 px et 9 cases par ligne (30/10, 1920 x 1057)
DRAG_TRIES = 3  # glisser recommencé si la fenêtre de quantité ne s'ouvre pas (une fois sur deux, 30/10)
DOUBLE_MAX = 40  # objets d'une pile posés au double-clic quand le glisser ne prend pas
SMALL_STACK = 5  # objets posés un à un par double-clic (fiable) avant de glisser le reste de la pile
MAX_SKIPS = 6  # cases du sac qui ne passent pas (objets de quête...) sautées au plus

CANCEL_TRIES = 2  # clics sur la croix de l'échange avant de le dire à l'utilisateur (31/10)
CANCEL_WAIT = 2.0  # s : fermeture (EV) annoncée par le serveur après la croix
MENU_WAIT = 1.0  # s après le clic sur une miniature : son menu affiché
REFUSE_WAIT = 0.8  # s après le refus (Échap) d'une demande d'échange arrivée au mauvais personnage
MENU_FOUND = 0.66  # « Echange » : 0,68 le 30/10
RESERVE = 0.02  # part des pods max gardée libre chez le principal
OPEN_SETTLE = 1.5  # s : fenêtre d'échange dessinée (sac compris) après ECK1
SERVER_WAIT = 3.0  # s : réponse du serveur (ERK, ECK, EMK...)
ACCEPT_DELAY = 4.0  # s après le dernier changement d'une offre : « Accepter » de nouveau cliquable
ACCEPT_TRIES = 2
CONCLUDE_WAIT = 6.0  # s : échange conclu après les deux « Accepter »
MIN_ROOM = 10  # pods : place libre en dessous de laquelle on ne pose plus rien (une unité pèse 1 à 10 pods)
MAX_STACKS = 60  # piles posées au plus par lot
FULL_ENOUGH = 0.15  # part des pods max libre en dessous de laquelle le principal dépose avant le lot suivant
MAX_LOTS = 10  # lots au plus par suiveur


def _empty_icon(icon, flat: float = 22.0) -> bool:
    """Case vide du sac : fond presque uni (30/10 : écart type 16,9 pour une case vide, 63 à 77 pour une pile ; 31/10 :
    29,8 pour une orbe pâle de Pesopepe, prise pour une case vide à 30, « plus rien à donner »)."""
    return icon.size == 0 or float(icon.std()) < flat


def _same_icon(a, b, tolerance: float = 25.0) -> bool:
    """Même objet dans une case avant / après (écart moyen des pixels de l'icône)."""
    import numpy as np

    return a.shape == b.shape and a.size > 0 and float(np.abs(a.astype(int) - b.astype(int)).mean()) < tolerance


def close_portrait_menu(game) -> bool:
    """Menu d'un portrait du groupe refermé d'un clic sur le fond beige sous la barre de sorts, jamais sur la carte :
    30/10, chaque clic « à côté » sur la carte faisait marcher Fourboun, entré à force dans la banque ; sur une autre
    carte, « Echange » disparaît des menus. 31/10, revue : plus d'Échap en dernier recours (devant un menu, il ouvre
    le Menu principal derrière) : la fermeture sûre commune (brain/popups.close_any_menu). False s'il reste ouvert."""
    return close_any_menu(game, "Menu d'une miniature du groupe")


def _exchange_line(frame, box):
    """Point de la ligne « Echange » du menu d'une miniature, lu par l'OCR de Windows ; None sans elle (sa propre
    miniature, quelqu'un sur une autre carte) ou sans OCR."""
    import cv2

    from bot.perception.chat import ocr_boxes

    x0, y0, x1, y1 = box
    lines = ocr_boxes(cv2.resize(frame.image[y0:y1, x0:x1], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
    log.debug("Menu de la miniature lu : %s", " | ".join(text for text, _ in lines or []) or "rien")
    for text, (bx0, by0, bx1, by1) in lines or []:
        if "echange" in text.lower().replace("é", "e"):
            return x0 + (bx0 + bx1) / 4, y0 + (by0 + by1) / 4
    return None


def _weights() -> dict[int, int]:
    """Poids en pods de chaque objet (langue du client, I.u[modèle].w) ; vide sans la langue."""
    global _WEIGHTS
    if _WEIGHTS is None:
        try:
            from tools.ressources_essai import LANGUE, _table  # import local : langue du client

            _WEIGHTS = {int(k): int(v.get("w") or 0) for k, v in _table(LANGUE, "I")["u"].items()}
        except Exception:  # langue absente
            _WEIGHTS = {}
    return _WEIGHTS


_WEIGHTS: dict[int, int] | None = None


def offer_weight(offer: dict[int, tuple[int, int]], weights: dict[int, int] | None = None) -> int:
    """Poids d'une offre reçue (uid -> (modèle, quantité)), d'après le poids de chaque objet."""
    weights = _weights() if weights is None else weights
    return sum(weights.get(template, 0) * quantity for template, quantity in offer.values())


def free_pods(net, reserve: float = RESERVE) -> int | None:
    """Pods libres d'après le serveur (« Ow »), moins une réserve ; None s'ils ne sont pas connus."""
    if net is None or not net.alive() or not net.pods:
        return None
    used, most = net.pods
    return max(0, most - used - math.ceil(most * reserve))


def units_that_fit(free: int, weight: int, wanted: int | None = None) -> int:
    """Unités d'un objet de `weight` pods qui tiennent dans `free` pods (au plus `wanted` s'il est donné)."""
    fit = free // weight if weight > 0 else (wanted if wanted is not None else 10**6)
    return max(0, fit if wanted is None else min(fit, wanted))


class Trader:
    """Un échange entre deux de nos personnages : `giver` ouvre, pose, accepte ; `taker` accepte et vérifie."""

    def __init__(self, giver, taker, portraits: dict, team=()) -> None:
        self.giver, self.taker = giver, taker
        self.team = list(team)  # l'équipe : qui a reçu la demande, d'après le serveur
        self.portraits = portraits  # (donneur, receveur) -> numéro du portrait, retenu pour la session
        self.stuck = False  # menu d'une miniature resté ouvert (rien de sûr pour le fermer) : plus de clic (31/10)

    # Ouvrir

    def open(self) -> bool:
        """Demande d'échange au receveur par le cadre du groupe : le donneur clique une miniature puis « Echange », et
        l'écoute réseau dit qui a reçu la demande (30/10, utilisateur : ne pas lire les noms, reconnaître le receveur
        par le réseau). Pas le bon : il refuse, et la miniature est retenue pour la session (self.portraits, par
        donneur) ; sa propre miniature n'a pas d'« Echange » et est retenue aussi."""
        game = self.giver.game
        if self._apart():
            # 31/10 (test rapide) : Eldieso en 1,-32, Fourboun en 2,-34 ; pas d'« Echange » dans aucun menu, 35 s de
            # miniatures cliquées pour rien. Le serveur dit déjà que l'échange est impossible.
            log.warning("Échange %s -> %s impossible : pas sur la même carte d'après le serveur (%s et %s)",
                        self.giver.name, self.taker.name, self.giver.game.net.map_id, self.taker.game.net.map_id)
            return False
        if not self._group_open(game):
            log.info("Échange %s -> %s : cadre du groupe pas déplié", self.giver.name, self.taker.name)
            return False
        for _ in range(2):  # second tour : une miniature dont le menu n'a pas été lu au premier
            for index in self._order():
                asked, before = time.monotonic(), self._requests()
                if not self._ask(index):
                    if self.stuck:
                        tell_user(f"Échange {self.giver.name} -> {self.taker.name} abandonné : le menu d'une miniature "
                                  f"du groupe reste ouvert chez {self.giver.name} (ferme-le toi-même)")
                        return False
                    self.portraits[(self.giver.name, self.giver.name)] = index  # pas d'« Echange » : la sienne
                    continue
                who = self._requested_member(before)
                if who is None:
                    log.info("Échange %s : demande par la miniature %d pas annoncée par le serveur", self.giver.name,
                             index + 1)
                    continue
                self.portraits[(self.giver.name, who.name)] = index
                self._check_slot(index, who)
                if who is self.taker:
                    return self._accept_request(asked)
                log.info("Échange %s -> %s : la miniature %d est %s (d'après le serveur) : demande refusée",
                         self.giver.name, self.taker.name, index + 1, who.name)
                escape_menu(who.game)  # Échap : « Non » à la demande
                game.sleep(REFUSE_WAIT)
        log.info("Échange %s -> %s : aucune miniature n'a donné de demande à %s", self.giver.name, self.taker.name,
                 self.taker.name)
        return False

    def _check_slot(self, index: int, who) -> None:
        """Place prévue d'après l'initiative comparée à celle que le serveur vient de confirmer (ERK). 31/10 : ordre du
        cadre vu par initiative à la formation et après un combat sans blessé ; re-trié ou non quand la vie baisse :
        pas encore vu. Chaque échange le note, pour le savoir au fil des farms."""
        net = getattr(self.giver.game, "net", None)
        slot = net.frame_slot(who.name) if hasattr(net, "frame_slot") else None
        if slot is None:
            return
        if slot == index:
            log.info("Cadre du groupe : %s en miniature %d, comme prévu par l'initiative", who.name, index + 1)
            return
        known = {name: initiative for name, initiative in net.group.values()}
        log.warning("Cadre du groupe : %s en miniature %d, %d prévue par l'initiative (initiatives : %s)", who.name,
                    index + 1, slot + 1, ", ".join(f"{n} {i}" for n, i in sorted(known.items(), key=lambda kv: -kv[1])))

    def _apart(self) -> bool:
        """Donneur et receveur sur deux cartes différentes d'après le serveur (GDM) ; False si l'une est inconnue."""
        maps = [getattr(getattr(m.game, "net", None), "map_id", None) for m in (self.giver, self.taker)]
        return None not in maps and maps[0] != maps[1]

    def _order(self) -> list[int]:
        """Miniatures à essayer : celle du receveur d'après son initiative (le cadre les range par initiative
        décroissante, 31/10), puis celle retenue d'un échange précédent, puis celles d'aucun personnage connu."""
        mine = {who: index for (giver, who), index in self.portraits.items() if giver == self.giver.name}
        others = {index for who, index in mine.items() if who != self.taker.name}
        net = getattr(self.giver.game, "net", None)
        slot = net.frame_slot(self.taker.name) if hasattr(net, "frame_slot") else None
        first = [i for i in dict.fromkeys((slot, mine.get(self.taker.name))) if i is not None and i not in others]
        if slot is not None and slot in first:
            log.debug("Échange %s -> %s : miniature %d d'après l'initiative annoncée par le serveur", self.giver.name,
                      self.taker.name, slot + 1)
        return first + [i for i in range(PORTRAITS) if i not in others and i not in first]

    def _group_open(self, game) -> bool:
        for _ in range(2):
            frame = game.look(park=False)
            box = frame.layout.box(*LIFE_BAR)
            region = frame.image[box[1]:box[3], box[0]:box[2]]
            red = int(((region[:, :, 2] > 150) & (region[:, :, 1] < 90) & (region[:, :, 0] < 90)).sum())
            if red >= 15:
                return True
            game.click(frame.layout.point(*GROUP_TOGGLE), (2, 2))
            game.sleep(0.8)
        return False

    def _ask(self, index: int) -> bool:
        """Menu de la miniature `index` : True si « Echange » y est et a été cliqué ; sinon menu refermé."""
        game = self.giver.game
        frame = game.look(park=False)
        portrait = frame.layout.point(PORTRAIT[0], PORTRAIT[1] + index * PORTRAIT_STEP)
        game.click(portrait, (3, 3))
        game.sleep(MENU_WAIT)
        frame = game.look(park=False)
        length = frame.layout.length
        # Bornes de la fenêtre, pas de la carte : 05/11, le menu de la 8e miniature (Elseiso) descend sous la carte,
        # « Echange » à y=848 hors de la zone lue (coupée à « Message privé ») ; échange jamais demandé.
        height, width = frame.image.shape[:2]
        box = (max(0, round(portrait[0] + length(MENU_BOX[0]))), max(0, round(portrait[1] + length(MENU_BOX[1]))),
               min(width, round(portrait[0] + length(MENU_BOX[2]))), min(height, round(portrait[1] + length(MENU_BOX[3]))))
        where = _exchange_line(frame, box)
        if where is None:
            menu, where = max(best_text(frame, "Echange", dark=True, box=box),
                              best_text(frame, "Echange", dark=False, box=box))
            if menu < MENU_FOUND:
                if not close_portrait_menu(game):
                    # 31/10 : menu resté ouvert : plus aucun clic sur les miniatures par-dessus (open s'arrête).
                    self.stuck = True
                return False
        game.click(where, (6, 2))
        return True

    def _requests(self) -> dict[str, tuple | None]:
        """Dernière demande d'échange (ERK) entendue par chacun des autres, d'après le serveur."""
        others = [m for m in self.team if m is not self.giver] or [self.taker]
        return {m.name: getattr(m.game.net.inventory, "request", None) for m in others}

    def _requested_member(self, before: dict[str, tuple | None]):
        """Celui de l'équipe dont la demande d'échange a changé depuis `before` (il vient d'en recevoir une) ; None
        après l'attente."""
        by_name = {m.name: m for m in self.team} | {self.taker.name: self.taker}
        found = []

        def requested() -> bool:
            found[:] = [name for name, request in self._requests().items() if request is not before.get(name)]
            return bool(found)

        return by_name[found[0]] if self._wait(requested) else None

    def _requested(self, since: float) -> bool:
        request = getattr(self.taker.game.net.inventory, "request", None)
        return request is not None and request[3] >= since

    def _accept_request(self, since: float) -> bool:
        game = self.taker.game
        frame = game.look(park=False)
        game.click(frame.layout.point(*YES), (8, 3))
        if not self._wait(lambda: self._opened(self.taker, since) and self._opened(self.giver, since)):
            log.info("Échange %s -> %s : pas ouvert (« Oui » pas pris ?)", self.giver.name, self.taker.name)
            self.cancel()
            return False
        log.info("Échange ouvert : %s -> %s", self.giver.name, self.taker.name)
        return True

    @staticmethod
    def _opened(member, since: float) -> bool:
        exchange, _ = member.game.net.trade()
        return exchange is not None and exchange.kind == 1 and exchange.opened_at >= since and not exchange.closed_at

    # Poser

    def offer(self, free: int) -> int:
        """Pose les ressources du donneur, pile après pile, tant que leur poids tient dans `free` pods ; renvoie le poids
        de l'offre (d'après ce que le receveur en voit). 0 : plus rien à donner."""
        game = self.giver.game
        game.sleep(OPEN_SETTLE)  # 30/10 : sac encore vide (pas dessiné) juste après l'ouverture : rien de glissé
        frame = game.look(park=False)
        game.click(frame.layout.point(*RESOURCES_TAB), (5, 5))
        game.sleep(0.6)
        weights = _weights()
        retried, rank = False, 0  # rank : case du sac essayée (les objets qui ne s'échangent pas restent devant)
        for _ in range(MAX_STACKS):
            weight = offer_weight(self._received(), weights)
            if free - weight < MIN_ROOM or rank >= MAX_SKIPS:
                break  # plus la place d'une unité de plus, ou trop de cases qui ne bougent pas
            slot = grid_point(FIRST_STACK, rank, STACK_STEP, STACK_COLUMNS)
            icon = self._icon(slot)
            if _empty_icon(icon):
                # 31/10, utilisateur : constater au réseau, pas à l'écran. Une case vue vide (orbe pâle de Pesopepe prise
                # pour une case vide) n'arrête rien tant que le sac, d'après le serveur, a encore des ressources pas
                # encore offertes : la case suivante.
                if self._resources_left():
                    log.info("Échange %s -> %s : case %d vue vide, mais le sac a encore des ressources d'après le "
                             "serveur : la suivante", self.giver.name, self.taker.name, rank + 1)
                    rank += 1
                    continue
                break  # case vide, et plus de ressource dans le sac d'après le serveur
            uid = self._put(None, slot)  # une unité : quel objet, quel poids
            if uid is None and not retried and not self._received():
                retried = True  # premier geste perdu (fenêtre pas prête) : un second essai
                game.sleep(1.0)
                uid = self._put(None, slot)
            if uid is None:
                # 30/10 : 4e case immobile (objet qui ne s'échange pas ?) : la suivante.
                log.info("Échange %s -> %s : la case %d du sac ne passe pas dans l'offre : la suivante",
                         self.giver.name, self.taker.name, rank + 1)
                rank += 1
                continue
            if not _same_icon(icon, self._icon(slot)):
                continue  # pile d'un seul objet, passée en entier : la case montre déjà la pile suivante
            template, quantity = self._received().get(uid, (0, 0))
            unit = weights.get(template, 0)
            # Petites piles : objet par objet au double-clic, tant que la case montre la même pile (30/10 : le glisser
            # n'ouvre la fenêtre de quantité qu'une fois sur deux).
            exhausted = False
            for _ in range(SMALL_STACK - 1):
                if units_that_fit(free - offer_weight(self._received(), weights), unit) <= 0:
                    break
                if self._put(None, slot) is None:
                    break
                if not _same_icon(icon, self._icon(slot)):
                    exhausted = True
                    break
            if exhausted:
                continue
            template, quantity = self._received().get(uid, (template, quantity))
            more = units_that_fit(free - offer_weight(self._received(), weights), unit)
            if more > 0 and self._put(more, slot) is None:
                # 30/10 : fenêtre de quantité pas ouverte (glisser raté une fois sur deux) : le reste de la pile au
                # double-clic, objet par objet, au lieu de clore le lot.
                for _ in range(min(more, DOUBLE_MAX)):
                    if self._put(None, slot) is None or not _same_icon(icon, self._icon(slot)):
                        break
                continue
            if more == 0 or self._received().get(uid, (0, 0))[1] >= quantity + more:
                break  # tout ce qui tenait est posé : lot plein (la fin de la pile au lot suivant)
            # Moins que demandé : la pile est passée en entier (le jeu ramène le nombre à sa taille) ; pile suivante.
        return offer_weight(self._received(), weights)

    def offer_item(self, template: int, count: int) -> bool:
        """Pose exactement `count` objets du modèle `template` (clés de donjon, 30/10) : onglet ressources, type
        « Clefs » dans la liste des types du sac de l'échange, une unité d'abord, vérifiée chez le receveur (EmKO :
        uid -> modèle), puis le reste un double-clic par unité (05/11) ; vérifié encore (quantité offerte = `count`). False au
        moindre écart : l'appelant annule l'échange (cancel), rien ne change de main. La liste des types est remise sur
        « Tous types » dans tous les cas."""
        game, net = self.giver.game, self.giver.game.net
        held = net.held(template)
        if held is None or held < count or count <= 0:
            log.warning("Échange %s -> %s : %s porte %s objet(s) %d, %d voulus", self.giver.name, self.taker.name,
                        self.giver.name, "?" if held is None else held, template, count)
            return False
        from bot.brain.equipment import close_bag_search, search_bag
        from bot.brain.inventory import item_name

        layout = game.look(park=False).layout
        game.click(layout.point(*RESOURCES_TAB), (5, 5))
        game.sleep(0.6)
        # 31/10 : la place de la pile prévue d'après les numéros d'objet était fausse (Clef des Champs 8143 posée au
        # lieu de la clé Bouftou) : la recherche du sac sur le nom exact du modèle ne laisse que sa pile, en première
        # case ; le modèle posé reste vérifié par le serveur.
        try:
            # 31/10, revue : le nom doit être vu dans le champ de recherche avant tout double-clic ; sinon (texte tapé
            # hors du champ) rien n'est posé et l'appelant annule.
            if not search_bag(game, layout, item_name(template), TRADE_FILTER, TRADE_SEARCH):
                log.warning("Échange %s -> %s : recherche du sac pas faite : rien posé", self.giver.name,
                            self.taker.name)
                return False
            start = FIRST_STACK
            uid = self._put(None, start)
            model = self._received().get(uid, (None, 0))[0] if uid is not None else None
            if model != template:
                log.warning("Échange %s -> %s : objet %s posé au lieu de %d (place de la pile ?) : annulé",
                            self.giver.name, self.taker.name, model, template)
                return False
            # 05/11 (journal 203000) : le reste par la fenêtre de quantité (glisser) ne s'ouvrait pas, l'échange
            # restait en suspens et les 6 suivants ratés derrière. Un double-clic par unité, chacun vu par le serveur.
            for placed in range(1, count):
                if self._put(None, start) is None:
                    log.warning("Échange %s -> %s : le reste pas posé (%d sur %d)", self.giver.name, self.taker.name,
                                placed, count)
                    return False
            offered = self._received().get(uid, (template, 0))[1]
            if offered != count:
                log.warning("Échange %s -> %s : %d objet(s) %d offerts au lieu de %d : annulé", self.giver.name,
                            self.taker.name, offered, template, count)
                return False
            log.info("Échange %s -> %s : %d objet(s) %d posés (vu par %s)", self.giver.name, self.taker.name, count,
                     template, self.taker.name)
            return True
        finally:
            close_bag_search(game, layout, TRADE_FILTER, TRADE_SEARCH)

    def _quantity_open(self, ok) -> bool:
        """Fenêtre de quantité ouverte : sa coche orange là où on l'attend (à côté du point de lâcher)."""
        image = getattr(self.giver.game.look(park=False), "image", None)
        if image is None:
            return True  # pas de capture (jeu factice des tests) : on suppose la fenêtre ouverte
        x, y = round(ok[0]), round(ok[1])
        patch = image[max(0, y - 6):y + 6, max(0, x - 6):x + 6]
        if patch.size == 0:
            return False
        b, g, r = (float(patch[:, :, k].mean()) for k in range(3))
        return r > 180 and 60 < g < 170 and b < 90  # bouton orange (30/10)

    def _icon(self, slot):
        frame = self.giver.game.look(park=False)
        x, y = frame.layout.point(*slot)
        length = frame.layout.length
        box = tuple(round(v) for v in (x + length(SLOT_ICON[0]), y + length(SLOT_ICON[1]), x + length(SLOT_ICON[2]),
                                       y + length(SLOT_ICON[3])))
        return frame.crop(box).copy()

    def _resources_left(self) -> bool:
        """Le sac du donneur a-t-il encore des ressources (onglet ressources) pas encore offertes, d'après le serveur ?
        Sac inconnu : non (comme avant, l'écran décide)."""
        from bot.brain.inventory import BAG, RESOURCES, item_category

        net = self.giver.game.net
        if not getattr(net.inventory, "known", False):
            return False
        exchange, _ = net.trade()
        offered = dict(exchange.mine) if exchange is not None and not exchange.closed_at else {}
        return any(item_category(i.template) == RESOURCES and i.quantity > offered.get(i.uid, 0)
                   for i in net.items() if i.position == BAG)

    def _received(self) -> dict[int, tuple[int, int]]:
        exchange, _ = self.taker.game.net.trade()
        return dict(exchange.theirs) if exchange is not None and not exchange.closed_at else {}

    def _put(self, count: int | None, start: tuple[float, float] = FIRST_STACK) -> int | None:
        """Glisse la pile de `start` (repère de référence ; défaut : la première) dans l'offre : `count` unités de plus
        (None : une). Renvoie l'uid de la pile, d'après le serveur ; None si rien n'a bougé."""
        game, net = self.giver.game, self.giver.game.net
        before, _ = net.trade()
        changed = dict(before.mine) if before is not None else {}
        layout = game.look(park=False).layout
        if count is None:
            # Une unité : vrai double-clic, fiable (30/10) ; le serveur dit quel objet c'est (EmKO chez le receveur).
            game.double_click(layout.point(*start), (2, 2))
            if not self._changed(changed, wait=SERVER_WAIT):
                return None
        else:
            drop = layout.point(*OFFER_SLOT)
            _, ok, _ = quantity_points(layout, drop)
            for attempt in range(DRAG_TRIES):
                # 30/10 : le premier glisser après un double-clic sur la même case n'ouvre pas la fenêtre de quantité ;
                # le second, si.
                self._drag(layout.point(*start), drop)
                game.sleep(0.8)
                if self._quantity_open(ok):
                    break
            else:
                log.info("Échange %s -> %s : fenêtre de quantité pas ouverte", self.giver.name, self.taker.name)
                return None
            # Nombre déjà sélectionné (« 1 » en surbrillance) : effacé puis tapé ; le jeu le ramène à la taille de la pile.
            for _ in range(4):
                game.press("backspace")
            game.type_text(str(count))
            game.sleep(0.3)
            game.click(ok, (3, 3))
            if not self._changed(changed, wait=SERVER_WAIT):
                return None  # pas d'Échap : il fermerait l'échange (30/10)
        exchange, _ = net.trade()
        return self._last_changed(before, exchange) if exchange is not None else None

    @staticmethod
    def _last_changed(before, after) -> int | None:
        old = dict(before.mine) if before is not None else {}
        changed = [uid for uid, quantity in after.mine.items() if old.get(uid) != quantity]
        return changed[-1] if changed else None

    def _changed(self, before: dict[int, int], wait: float) -> bool:
        """Mon offre a changé d'après le serveur (EMKO) par rapport à `before` (uid -> quantité)."""
        net = self.giver.game.net
        return self._wait(lambda: (net.trade()[0] is not None and net.trade()[0].mine != before), wait)

    def _drag(self, start, end) -> None:
        """Glisser LENT par messages (30/10 : un glisser rapide n'ouvre pas la fenêtre de quantité) ; team/bank.py."""
        slow_drag(self.giver.game, start, end)

    # Conclure

    def conclude(self, free: int) -> bool:
        """Les deux acceptent si l'offre tient dans `free` pods ; True si le receveur a bien reçu (pods annoncés)."""
        weight = offer_weight(self._received())
        if weight > free:
            log.warning("Échange %s -> %s : %d pods offerts pour %d de libres : annulé", self.giver.name,
                        self.taker.name, weight, free)
            self.cancel()
            return False
        # « Accepter » grisé quelques secondes après chaque changement d'une offre (30/10 : cliqué 0,5 s après le dernier
        # objet posé, EK jamais venu ; 10 s après, accepté) : attente, puis le donneur (EK), puis le receveur (EVa).
        exchange, _ = self.taker.game.net.trade()
        last = exchange.changed_at if exchange is not None else 0.0
        if (wait := ACCEPT_DELAY - (time.monotonic() - last)) > 0:
            time.sleep(wait)
        net = self.taker.game.net
        since = time.monotonic()
        accepted = False
        for _ in range(ACCEPT_TRIES):
            frame = self.giver.game.look(park=False)
            self.giver.game.click(frame.layout.point(*ACCEPT), (10, 3))
            if self._wait(lambda: net.trade()[0] is not None and any(net.trade()[0].ready.values()), 2.0):
                accepted = True
                break
        if not accepted:
            log.warning("Échange %s -> %s : « Accepter » du donneur pas pris", self.giver.name, self.taker.name)
            self.cancel()
            return False
        done = False
        for _ in range(ACCEPT_TRIES):
            frame = self.taker.game.look(park=False)
            self.taker.game.click(frame.layout.point(*ACCEPT), (10, 3))
            done = self._wait(lambda: net.trade()[0] is not None and net.trade()[0].closed_at >= since, CONCLUDE_WAIT)
            if done:
                break
        exchange, _ = net.trade()
        if not done or exchange is None or not (exchange.concluded or net.pods_at >= since):
            log.warning("Échange %s -> %s : pas conclu (fermé : %s)", self.giver.name, self.taker.name, done)
            if not done:
                self.cancel()
            else:
                # 31/10, revue : fermé après les deux « Accepter », sans « EVa » ni pods annoncés : rien ne prouve qui
                # a quoi ; l'utilisateur le sait.
                tell_user(f"Échange {self.giver.name} -> {self.taker.name} fermé sans preuve qu'il a été conclu "
                          f"(ni EVa ni pods annoncés) : vérifie leurs sacs")
            return False
        log.info("Échange conclu : %s -> %s, %d pods", self.giver.name, self.taker.name, weight)
        return True

    def cancel(self) -> bool:
        """Ferme l'échange (croix) chez le donneur, s'il est encore ouvert : rien ne change de main. 31/10, revue : la
        fermeture se constate au serveur (EV : closed_at), un second clic sur la croix sinon ; toujours ouvert après :
        dit à l'utilisateur. True si plus rien n'est ouvert chez les deux."""
        closed = True
        for member in (self.giver, self.taker):
            for attempt in range(1, CANCEL_TRIES + 1):
                exchange, _ = member.game.net.trade()
                if exchange is None or exchange.closed_at:
                    break  # fermé (EV), aussi par la croix de l'autre : EV arrive aux deux
                frame = member.game.look(park=False)
                member.game.click(frame.layout.point(*CLOSE), (3, 3))
                member.game.sleep(0.6)
                net = member.game.net
                if self._wait(lambda: net.trade()[0] is None or bool(net.trade()[0].closed_at), CANCEL_WAIT):
                    break
                log.warning("Échange %s -> %s : pas de fermeture annoncée par le serveur après la croix chez %s "
                            "(essai %d/%d)", self.giver.name, self.taker.name, member.name, attempt, CANCEL_TRIES)
            else:
                closed = False
                tell_user(f"Échange {self.giver.name} -> {self.taker.name} : la fenêtre d'échange de {member.name} ne "
                          f"se ferme pas (pas d'EV du serveur après {CANCEL_TRIES} clics sur la croix) : ferme-la "
                          f"toi-même")
        return closed

    @staticmethod
    def _wait(condition, timeout: float = SERVER_WAIT) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return True
            time.sleep(0.15)
        return condition()


class CentralBank:
    """Mode banque « principal » : tous entrent, le principal vide son sac dans son coffre, puis chaque suiveur lui
    donne ses ressources par lots qui tiennent dans ses pods ; il ne dépose que ses pods presque pleins, et à la fin
    (l'accès au coffre se paie). Ce qui ne passe pas (échange raté, pods inconnus) va dans le coffre du suiveur. Tous
    ressortent."""

    def __init__(self, members, hide_key: str = "", keep: int | None = None) -> None:
        self.members = members
        self.leader = members[0]
        self.visit = BankVisit(members, hide_key, keep)  # keep : clé de donjon reprise du coffre (team/keys.py)
        self.portraits: dict = {}
        self.carried = False  # lots reçus pas encore déposés

    def run(self) -> list[str]:
        """Noms de ceux dont les ressources sont au coffre (celui du principal, ou le leur en secours)."""
        visit = self.visit
        entered = visit._doors(self.members, DOOR, inside=True)
        done = []
        if self.leader.name not in entered or not visit.deposit(self.leader):
            log.warning("Banque (principal) : %s n'a pas vidé son sac : chacun dans son coffre", self.leader.name)
            done = [m.name for m in self.members if m.name in entered and visit.deposit(m)]
        else:
            done.append(self.leader.name)
            for follower in self.members[1:]:
                if follower.name in entered and self.centralise(follower):
                    done.append(follower.name)
            if self.carried and not visit.deposit(self.leader):
                log.warning("Banque (principal) : %s n'a pas déposé les derniers lots", self.leader.name)
        left = visit._doors([m for m in self.members if m.name in entered], EXIT, inside=False)
        done = [name for name in done if name in left]
        log.info("Banque (principal) : %d sur %d vidés (%s)", len(done), len(self.members), ", ".join(done))
        visit.tell_failures([m for m in self.members if m.name not in done], entered, left)  # 31/10 : à l'utilisateur
        return done

    def centralise(self, follower) -> bool:
        """Lots de `follower` vers le principal ; le principal ne dépose que ses pods presque pleins (et à la fin) :
        chaque accès au coffre se paie (30/10 : 718 kamas pour Fourboun, 11 lots déposés un à un ce jour-là). En
        secours, le reste dans le coffre du suiveur."""
        for lot in range(1, MAX_LOTS + 1):
            free = free_pods(self.leader.game.net)
            if free is not None and free < FULL_ENOUGH * (self.leader.game.net.pods or (0, 1))[1] and self.carried:
                if not self.visit.deposit(self.leader):
                    log.warning("%s : lots pas déposés : le reste dans le coffre de %s", self.leader.name,
                                follower.name)
                    return self.visit.deposit(follower)
                self.carried = False
                free = free_pods(self.leader.game.net)
            if free is None or free <= 0:
                log.info("%s : pods libres du principal inconnus : son propre coffre", follower.name)
                return self.visit.deposit(follower)
            trader = Trader(follower, self.leader, self.portraits, self.members)
            if not trader.open():
                return self.visit.deposit(follower)
            weight = trader.offer(free)
            if weight <= 0:
                trader.cancel()
                log.info("%s : plus de ressource à donner (%d lot(s))", follower.name, lot - 1)
                return True
            if not trader.conclude(free):
                return self.visit.deposit(follower)
            self.carried = True
        return self.visit.deposit(follower)
