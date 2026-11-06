"""Mode banque « chacun » (--banque) : chaque personnage vide l'onglet ressources de son inventaire dans son propre coffre,
à la banque d'Astrub (plan : docs/rapports/plan_banque.md ; étapes validées une à une le 11/10 avec tools/entrer_porte,
tools/banquier et tools/coffre_transfert).

L'équipe est d'abord amenée sur la carte de la banque (--aller 4,-16, fait par la boucle de l'équipe). Puis, personnage
après personnage, en arrière-plan : soleil de la porte (case 23,9) -> intérieur (fond noir autour de la pièce) ; clic
GAUCHE sur le banquier (le clic droit ouvre le menu de Flash) -> « Parler » -> « Consulter son coffre personnel » ->
fenêtre « Coffre » ; onglet ressources -> bouton trier / transférer -> « Transférer tous les objets » ; Échap ; soleil de
la sortie (case 8,26) -> dehors. Chaque étape est vérifiée à l'écran avant la suivante ; au moindre doute, ce personnage
s'arrête là (capture gardée) et on passe au suivant.

27/10 (« les actions sont lentes ») : toute l'équipe entre, puis ressort, en même temps (avant : ~5 s d'entrée par
personnage, l'un après l'autre) ; chaque étape suit la réponse du serveur (connection/network.py : carte chargée,
dialogue, coffre ouvert, pods) au lieu d'une attente fixe, celle-ci gardée sans écoute. Portes cliquées les autres
joueurs cachés (touche hide_key tenue) : 27/10, Fourbodeux a ouvert le menu d'un joueur posté devant la porte.

Positions : repère de référence (fenêtre de 1918 x 1008, comme les captures du 11/10), mises à l'échelle de la fenêtre.

30/10 (demande de l'utilisateur : les personnages GARDENT leurs clés de donjon) : reprendre des objets de son coffre
(take_from_chest), pour la répartition des clés au début d'un farm de donjon (team/keys.py) et, en option
(donjon.garder_cle), juste après « Transférer tous les objets », qui envoie aussi les clés au coffre. Pas encore fait en
jeu : les positions du côté coffre de la fenêtre sont à calibrer (CHEST_*) ; chaque geste est vérifié par le serveur
(EsKO : pile du coffre, OAKO / OQ : sac) et, au moindre doute, Échap.
"""

import logging
import time

import cv2
import numpy as np
import win32con

from bot.alerts import tell_user
from bot.brain.popups import close_any_menu, menu_open
from bot.brain.travel import escape_menu
from bot.perception.templates import match_template
from bot.perception.text import text_score
from bot.perception.ui import pods_fill

log = logging.getLogger(__name__)

BANK_MAP = (4, -16)
DOOR, EXIT = (23, 9), (8, 26)  # cases des soleils de la porte (dehors) et de la sortie (dedans)
INDOOR_DARK, OUTDOOR_DARK = 0.25, 0.05  # part de noir dans la zone de jeu : dedans / dehors (mesuré le 11/10)
DOOR_WAIT = 12.0
SETTLE = 0.3  # s après la réponse du serveur : l'écran la montre
# Fenêtre attendue à l'écran après la réponse du serveur : 27/10, coffre annoncé (ECK5, puis son contenu EL...) mais pas
# encore dessiné 0,3 s après chez 6 personnages sur 7 ; regardée toutes les LOOK_EVERY s jusqu'à l'ancienne attente.
LOOK_EVERY = 0.3
# 27/10 : « Parler » cliqué, dialogue pas ouvert (Fourbodeux, l'équipe entière dans la pièce) : un second essai.
VISIT_TRIES = 2
DOOR_RETRY = 5.0  # s sans changement de carte après le clic sur la porte : un second clic (27/10, Waldarch resté dehors)
COUNTER = (1150, 150, 1700, 450)  # derrière le comptoir, où se tient le banquier
RESOURCES_TAB, SORT_BUTTON = (1670, 220), (1875, 220)  # onglet ressources (bûche), bouton trier / transférer
MENU_ZONE = (1450, 200, 1919, 700)
TEXT_SIZES = range(14, 25)
FOUND = 0.75

# Liste des types d'objets (« Tous types », brain/equipment.py) : un type choisi, puis remis.
ALL_TYPES = "Tous types"
KEYS_TYPE = "Clefs"  # type 84 de la langue du client (brain/inventory.py, TYPE_KEYS)
LIST_TOP = 15  # crans de molette vers le haut : liste des types remise en haut
LIST_SCROLLS = 3  # crans de molette (par 3) pour trouver un type sous le bas de la liste
TYPE_TRIES = 2  # liste des types du coffre ouverte au plus tant de fois (06/11)
LIST_LOOK = 1.5  # s : liste des types cherchée toutes les 0,3 s après le clic
LIST_WAIT = 0.6
# Côté coffre de la fenêtre « Coffre » (à gauche ; le sac est à droite : RESOURCES_TAB, SORT_BUTTON). TOUTES À
# CALIBRER EN JEU (30/10 : jamais relevées, valeurs supposées d'après la fenêtre d'échange) : onglet ressources du
# coffre, sa liste des types, le milieu de la liste ouverte, sa première pile et l'écart entre deux piles, une case du
# sac où lâcher la pile, et la fenêtre de quantité (supposée la même que celle de l'échange, team/trade.py).
# Fenêtre « Coffre » (à gauche) et sac (à droite), relevées le 30/10 sur la capture du coffre de Fourboun (fenêtre de
# 1920 x 1057) : onglets à y 231, grille de 5 cases par ligne, 61 px entre deux cases.
CHEST_RESOURCES_TAB = (702, 220)  # onglet ressources (bûche) du coffre
CHEST_FILTER = (755, 264)  # liste des types du coffre (« Tous types »)
CHEST_LIST = (745, 429)  # milieu de la liste ouverte (sous le bouton)
CHEST_FIRST_STACK = (635, 321)  # première pile du coffre
CHEST_STEP, CHEST_COLUMNS = 58.2, 5  # écart entre deux cases (px de référence), cases par ligne
BAG_DROP = (1713, 611)  # case vide du sac (4e ligne) où lâcher une pile reprise du coffre
CHEST_WAIT = 3.0  # s : réponse du serveur (EsKO, OAKO / OQ)
TRANSFER_WAIT = 3.0  # s : « Transférer tous les objets » prouvé par le serveur (pods Ow qui baissent, OR / OQ du sac)


def choose_type(game, layout, name: str, filter_at: tuple[int, int], list_at: tuple[int, int]) -> bool:
    """Liste des types d'une grille d'objets (`filter_at` : son bouton ; `list_at` : le milieu de la liste ouverte) :
    `name` cliqué ; False s'il n'y est pas (liste refermée). Comme brain/equipment.py (_choose, validé le 27/10 et le
    30/10 sur l'inventaire : liste remise en haut, molette si le type est sous le bas), pour d'autres fenêtres."""
    game.click(layout.point(*filter_at), (6, 3))
    game.sleep(0.3)
    game.wheel(layout.point(*list_at), -LIST_TOP)
    clicked = time.monotonic()
    while True:
        game.sleep(0.3)
        score, where = best_text(game.look(park=False), name, dark=True)
        if score >= FOUND or time.monotonic() - clicked >= LIST_LOOK:
            break
    for _ in range(LIST_SCROLLS):
        if score >= FOUND:
            break
        game.wheel(layout.point(*list_at), 3)
        game.sleep(0.4)
        score, where = best_text(game.look(park=False), name, dark=True)
    if score < FOUND:
        game.click(layout.point(*filter_at), (6, 3))  # referme la liste
        game.sleep(LIST_WAIT)
        return False
    game.click(where, (6, 2))
    game.sleep(LIST_WAIT)
    return True


def slow_drag(game, start, end) -> bool:
    """Glisser LENT par messages (team/trade.py, 30/10 : un glisser rapide n'ouvre pas la fenêtre de quantité) ; False
    au premier plan (pas de glisser par messages)."""
    inp = game.input
    post, client = getattr(inp, "_post", None), getattr(inp, "_client", None)
    if post is None or client is None:
        return False
    # 30/10 (2e séance) : 20 pas en 0,8 s avec 0,25 s de prise ne marchait plus ; 0,5 s de prise, 40 pas en 1,6 s et
    # 0,5 s avant de lâcher, sur une case vide de l'offre : fenêtre de quantité à chaque essai.
    post(win32con.WM_MOUSEMOVE, 0, client(start))
    time.sleep(0.3)
    post(win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, client(start))
    time.sleep(DRAG_HOLD)
    for k in range(1, DRAG_STEPS + 1):
        point = (start[0] + (end[0] - start[0]) * k / DRAG_STEPS, start[1] + (end[1] - start[1]) * k / DRAG_STEPS)
        post(win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, client(point))
        time.sleep(DRAG_TIME / DRAG_STEPS)
    time.sleep(DRAG_HOLD)
    post(win32con.WM_LBUTTONUP, 0, client(end))
    return True


def quantity_points(layout, drop) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """Fenêtre de quantité ouverte par un glisser lâché en `drop` : (champ du nombre, coche, « max »). Elle s'ouvre à
    côté du point de lâcher (30/10 : lâcher en (1430, 614) -> max (1210, 587), champ (1312), coche (1406) ; en
    (1500, 614) -> 1280, 1382, 1478 : même décalage)."""
    length = layout.length
    return tuple((drop[0] + length(dx), drop[1] + length(QTY_DY)) for dx in (QTY_FIELD_DX, QTY_OK_DX, QTY_MAX_DX))


# Glisser lent (slow_drag) : 30/10, 3 essais par réglage, fenêtre de quantité ouverte une fois sur deux en alternance
# avec 1,0 s / 60 pas / 2,4 s (et 0,5 / 40 / 1,6) ; jamais avec 0,25 / 20 / 0,8, ni après un survol.
DRAG_HOLD, DRAG_STEPS, DRAG_TIME = 1.0, 60, 2.4
QTY_FIELD_DX, QTY_OK_DX, QTY_MAX_DX, QTY_DY = -112.5, -21.0, -209.8, -25.7  # fenêtre de quantité / point de lâcher


def grid_point(first: tuple[int, int], rank: int, step: float, columns: int) -> tuple[float, float]:
    """Case n° `rank` (0 : la première) d'une grille d'objets, de gauche à droite puis de haut en bas (repère de
    référence)."""
    return first[0] + (rank % columns) * step, first[1] + (rank // columns) * step


def chest_stacks(net, template: int) -> list[tuple[int, int]]:
    """Piles de ce modèle dans le coffre ouvert (uid, quantité), dans l'ordre d'arrivée (EL, puis EsKO) ; [] sans
    coffre ouvert."""
    exchange, _ = net.trade()
    if exchange is None or exchange.kind != 5 or exchange.closed_at:
        return []
    return [(uid, quantity) for uid, (model, quantity) in exchange.storage.items() if model == template and quantity > 0]


def take_from_chest(member, template: int, count: int, rank_of=None) -> int:
    """Coffre de `member` ouvert (ECK5) : reprend au plus `count` objets de ce modèle dans son sac. Renvoie le nombre
    repris d'après le serveur (pile du coffre diminuée ET sac augmenté d'autant). Onglet ressources du coffre, type
    « Clefs » (KEYS_TYPE), pile glissée LENTEMENT vers le sac, nombre tapé dans la fenêtre de quantité. Au moindre doute
    (type pas trouvé, rien d'annoncé, nombre différent) : Échap et ce qui est vérifié. `rank_of` : (piles du coffre de
    ce type, pile voulue) -> rang de la pile dans la grille filtrée ; défaut : son rang parmi les piles de clés du coffre
    (ordre d'arrivée, hypothèse à vérifier en jeu)."""
    from bot.brain.inventory import key_names

    game, net = member.game, _net(member)
    if net is None or count <= 0:
        return 0
    stacks = chest_stacks(net, template)
    if not stacks:
        return 0
    uid, stored = stacks[0]
    exchange, _ = net.trade()
    keys = set(key_names()) or {template}
    same_type = [u for u, (model, quantity) in exchange.storage.items() if model in keys and quantity > 0]
    # Rang de la pile dans la grille filtrée sur « Clefs » : son rang parmi les piles de clés, dans l'ordre d'arrivée
    # (05/11, coffre de Walitcoin : 11 piles de clés, la Clef des Champs au rang 9, vue à cette place).
    rank = rank_of(same_type, uid) if rank_of is not None else (same_type.index(uid) if uid in same_type else 0)
    wanted = min(count, stored)
    layout = game.look(park=False).layout
    for attempt in range(TYPE_TRIES):
        # 06/11 (journal 210755) : « Clefs » pas trouvé chez Fourboun (20 clés au coffre, trouvé une heure plus tôt),
        # aucune capture : rien repris, passages ramenés à ce que les autres coffres donnaient. Capture, puis onglet et
        # liste rouverts une fois.
        game.click(layout.point(*CHEST_RESOURCES_TAB), (5, 5))
        game.sleep(0.6 + attempt)
        if choose_type(game, layout, KEYS_TYPE, CHEST_FILTER, CHEST_LIST):
            break
        frame = game.look(park=False)
        path = getattr(game, "recorder", None) and game.recorder.save(frame.image, "coffre_type_clefs_pas_trouve",
                                                                       lossless=True)
        log.warning("%s : type « %s » pas trouvé dans la liste du coffre (essai %d sur %d, capture : %s)", member.name,
                    KEYS_TYPE, attempt + 1, TYPE_TRIES, path)
    else:
        log.warning("%s : rien repris du coffre", member.name)
        return 0
    taken = 0
    try:
        # 05/11 (tools/observer_coffre.py, Walitcoin) : le glisser de la pile vers le sac ne l'attrapait pas (7 coffres
        # sur 7, et le point de lâcher tombait sur un objet du sac plein) ; un vrai double-clic sur la pile en fait
        # passer UNE dans le sac, annoncé par le serveur (EsKO+pile|reste, OAKO de l'objet). Une clé par double-clic.
        start = layout.point(*grid_point(CHEST_FIRST_STACK, rank, CHEST_STEP, CHEST_COLUMNS))
        for _ in range(wanted):
            before = dict(chest_stacks(net, template)).get(uid, 0)
            if before <= 0:
                break
            since = time.monotonic()
            game.double_click(start, (3, 3))
            if not _chest_moved(net, uid, before, since, CHEST_WAIT):
                log.warning("%s : la pile de clés n'a pas quitté le coffre d'après le serveur (double-clic)%s",
                            member.name, f" après {taken} reprise(s)" if taken else "")
                break
            taken += 1
            game.sleep(0.4)
        left = dict(chest_stacks(net, template)).get(uid, 0)
        if taken != wanted:
            log.warning("%s : %d repris du coffre au lieu de %d (coffre %d -> %d)", member.name, taken, wanted, stored,
                        left)
        else:
            log.info("%s : %d repris du coffre (coffre %d -> %d)", member.name, taken, stored, left)
        return taken
    finally:
        if not choose_type(game, layout, ALL_TYPES, CHEST_FILTER, CHEST_LIST):
            log.info("%s : liste des types du coffre pas remise sur « %s »", member.name, ALL_TYPES)


def _chest_moved(net, uid: int, stored: int, since: float, wait: float) -> bool:
    """La pile `uid` du coffre a diminué (ou disparu) d'après le serveur."""
    def moved() -> bool:
        exchange, _ = net.trade()
        if exchange is None or exchange.closed_at or exchange.changed_at <= since:
            return False
        model_quantity = exchange.storage.get(uid)
        return model_quantity is None or model_quantity[1] < stored
    return _wait_until(moved, wait)


def _wait_until(condition, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.15)
    return condition()


def bag_quantities(net) -> dict[int, int]:
    """Piles du sac d'après le serveur : uid -> quantité (OR retire la pile, OQ change sa quantité)."""
    from bot.brain.inventory import BAG

    return {item.uid: item.quantity for item in net.items() if item.position == BAG}


def resources_in_bag(net) -> bool | None:
    """Le sac a-t-il des ressources (onglet ressources, celui que « Transférer tous les objets » vide), d'après le
    serveur ? None si on ne le sait pas (sac de la connexion pas entendu)."""
    from bot.brain.inventory import BAG, RESOURCES, item_category

    if not getattr(net.inventory, "known", False):
        return None
    return any(item_category(i.template) in (RESOURCES, None) for i in net.items() if i.position == BAG)


def transfer_proof(net, pods_before: int | None, bag_before: dict[int, int]) -> str | None:
    """Preuve, d'après le serveur, que des objets ont quitté le sac (31/10, revue : « Transférer tous les objets »
    jamais prouvé) : pods portés (Ow) en baisse, ou piles du sac retirées / diminuées (OR / OQ). None sans preuve."""
    if pods_before is not None and net.pods and net.pods[0] < pods_before:
        return f"pods {pods_before} -> {net.pods[0]}"
    after = bag_quantities(net)
    gone = [uid for uid, quantity in bag_before.items() if after.get(uid, 0) < quantity]
    return f"{len(gone)} pile(s) sortie(s) du sac" if gone else None


def close_unexpected(game, why: str) -> bool:
    """Ce qui reste ouvert après un échec à la banque : un menu (joueur, Flash, petit menu, bulle) par la fermeture sûre
    (brain/popups.close_any_menu, jamais Échap devant un menu : il ouvre le Menu principal derrière) ; sinon le coffre
    ou le dialogue du banquier, fenêtres qu'Échap ferme (escape_menu). False si un menu reste ouvert."""
    if menu_open(game.look(park=False)) is not None:
        return close_any_menu(game, why)
    escape_menu(game)
    return True


def dark_share(frame) -> float:
    left, top, right, bottom = frame.layout.map_box
    return float((frame.image[top:bottom, left:right].max(axis=2) <= 12).mean())


def find_bankers(frame) -> list[tuple[int, int]]:
    """Banquiers (hiboux dorés) derrière le comptoir, du plus visible au moins visible (11/10 : 1 780 à 2 300 px de
    référence par banquier, les morceaux de coffre 320 au plus)."""
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = ((h >= 12) & (h <= 24) & (s >= 90) & (s <= 200) & (v >= 110) & (v <= 215)).astype(np.uint8)
    x0, y0, x1, y1 = frame.layout.box(*COUNTER)
    zone = cv2.morphologyEx(mask[y0:y1, x0:x1], cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, _, stats, centers = cv2.connectedComponentsWithStats(zone)
    found = [(int(stats[k, 4]), (int(centers[k, 0]) + x0, int(centers[k, 1]) + y0)) for k in range(1, count)
             if stats[k, 4] >= frame.layout.area(1000) and stats[k, 3] >= frame.layout.length(45)]
    return [center for _, center in sorted(found, reverse=True)]


def best_text(frame, text: str, dark: bool, bold: bool = False, box=None) -> tuple[float, tuple[int, int]]:
    """Meilleure place de `text` dans `box` (défaut : la zone de jeu) : (score, point de la fenêtre)."""
    box = frame.layout.map_box if box is None else box
    region = frame.crop(box)
    best = (0.0, (0, 0))
    for size in TEXT_SIZES:
        score, (x, y) = text_score(region, text, frame.layout.length(size), bold, dark)
        if score > best[0]:
            best = (score, (box[0] + x, box[1] + y))
    return best


def inside(member) -> bool:
    """Le personnage est dans un bâtiment (fond noir autour de la pièce)."""
    return dark_share(member.game.look(park=False)) >= INDOOR_DARK


class BankVisit:
    """Passage à la banque de chaque personnage de l'équipe (déjà sur la carte de la banque)."""

    def __init__(self, members, hide_key: str = "", keep: int | None = None) -> None:
        self.members = members
        self.hide_key = hide_key  # joueurs cachés pendant les clics sur les portes (travel.hide_players_key)
        # Clé de donjon (modèle) reprise du coffre après « Transférer tous les objets » (donjon.garder_cle, 30/10) ;
        # None : rien de repris (défaut tant que le côté coffre de la fenêtre n'est pas calibré).
        self.keep = keep
        self.reasons: dict[str, str] = {}  # personnage -> dernière raison d'un échec (dite à l'utilisateur à la fin)
        self.final: set[str] = set()  # échecs à ne pas réessayer (coffre ouvert et payé, transfert pas prouvé)

    def run(self) -> list[str]:
        """Noms de ceux qui ont vidé leur onglet ressources (et sont ressortis)."""
        entered = self._doors(self.members, DOOR, inside=True)
        done = []
        for member in self.members:
            if member.name not in entered:
                self._fail(member, "pas entré dans la banque (soleil de la porte, case 23,9)")
                continue
            if self.deposit(member):
                done.append(member.name)
        left = self._doors([m for m in self.members if m.name in entered], EXIT, inside=False)
        for member in self.members:
            if member.name in entered and member.name not in left:
                self._fail(member, "pas ressorti de la banque (soleil de la sortie, case 8,26)")
        done = [name for name in done if name in left]
        log.info("Banque : %d sur %d ont vidé leurs ressources (%s)", len(done), len(self.members), ", ".join(done))
        self.tell_failures([m for m in self.members if m.name not in done], entered, left)
        return done

    def tell_failures(self, members, entered, left) -> None:
        """31/10, revue : « échecs sans message » : chaque personnage qui n'a pas vidé ses ressources est dit à
        l'utilisateur, avec la dernière raison connue."""
        for member in members:
            why = self.reasons.get(member.name)
            if why is None:
                why = ("pas entré dans la banque" if member.name not in entered
                       else "pas ressorti de la banque" if member.name not in left else "ressources pas vidées")
            tell_user(f"Banque : {member.name} n'a pas vidé ses ressources au coffre : {why}")

    def deposit(self, member) -> bool:
        """Passage au coffre d'un personnage déjà dans la banque, avec VISIT_TRIES essais ; True s'il a vidé ses
        ressources. Dialogue ou coffre laissés ouverts fermés après un échec (la sortie ne se cliquerait pas) ; un menu
        ouvert à la place (joueur, Flash) par la fermeture sûre, jamais Échap (close_unexpected)."""
        self.final.discard(member.name)  # un passage précédent (mode « principal » : plusieurs dépôts) ne compte pas
        for attempt in range(VISIT_TRIES):
            try:
                if self.visit(member):
                    return True
                if not close_unexpected(member.game, f"Banque, {member.name}"):
                    self.reasons[member.name] = "un menu reste ouvert dans la banque (ferme-le toi-même)"
                    return False
            except Exception:  # un personnage en échec ne bloque pas les autres
                log.exception("%s : erreur à la banque", member.name)
                self.reasons[member.name] = "erreur à la banque (voir le journal)"
                return False
            if member.name in self.final:
                return False  # 31/10 : coffre déjà ouvert (accès payé) et transfert pas prouvé : pas de second passage
            if attempt + 1 < VISIT_TRIES:
                log.info("%s : nouvel essai au banquier", member.name)
        return False

    def leave(self, members) -> set[str]:
        """Sortie de la banque de ceux qui y sont restés seuls (passage raté, bot arrêté) ; noms de ceux qui sont
        dehors. Coffre ou dialogue laissés ouverts fermés d'abord (Échap ; un menu par la fermeture sûre)."""
        for member in members:
            close_unexpected(member.game, f"Banque, {member.name}")
        return self._doors(members, EXIT, inside=False)

    def _fail(self, member, why: str) -> bool:
        path = member.game.recorder.save(member.game.look(park=False).image, "banque_echec", lossless=True)
        log.warning("%s : %s (capture : %s) ; je passe au suivant", member.name, why, path)
        self.reasons[member.name] = why
        return False

    def _click(self, game, point, spread) -> None:
        if not self.hide_key:
            game.click(point, spread)
            return
        with game.holding(self.hide_key):
            game.click(point, spread)

    def _doors(self, members, cell: tuple[int, int], inside: bool) -> set[str]:
        """Tous cliquent le soleil `cell` l'un après l'autre sans s'attendre, puis chacun est attendu de l'autre côté
        (carte chargée d'après le serveur, sinon le noir autour de la pièce) ; noms de ceux qui y sont."""
        there, pending = set(), {}
        for member in members:
            game = member.game
            frame = game.look(park=False)
            if (dark_share(frame) >= INDOOR_DARK) if inside else (dark_share(frame) < OUTDOOR_DARK):
                there.add(member.name)
                continue
            clicked = time.monotonic()
            self._click(game, frame.grid.center(cell), frame.grid.click_spread())
            pending[member.name] = (member, clicked)
        deadline = time.monotonic() + DOOR_WAIT
        again = set()
        while pending and time.monotonic() < deadline:
            members[0].game.sleep(0.2)
            for name, (member, clicked) in list(pending.items()):
                if name not in again and time.monotonic() - clicked >= DOOR_RETRY:
                    again.add(name)
                    frame = member.game.look(park=False)
                    log.info("%s : toujours de ce côté de la porte %.0f s après le clic : je reclique", name,
                             DOOR_RETRY)
                    self._click(member.game, frame.grid.center(cell), frame.grid.click_spread())
                net = _net(member)
                if net is not None:
                    arrived = net.new_map_since(clicked) is not None
                else:
                    dark = dark_share(member.game.look(park=False))
                    arrived = (dark >= INDOOR_DARK) if inside else (dark < OUTDOOR_DARK)
                if arrived:
                    there.add(name)
                    del pending[name]
        if members:
            members[0].game.sleep(SETTLE)  # la pièce dessinée avant le premier clic de l'autre côté
        return there

    def _wait(self, member, attribute: str, since: float, timeout: float) -> None:
        """Réponse du serveur (`attribute` de ClientFeed) après `since`, puis SETTLE ; sans écoute, `timeout` s comme
        avant ; sans réponse à temps, rien de plus (le timeout est passé)."""
        net = _net(member)
        if net is None:
            member.game.sleep(timeout)
        elif net.wait_for(attribute, since, timeout) is not None:
            member.game.sleep(SETTLE)

    def _text(self, member, text: str, dark: bool, timeout: float, bold: bool = False) -> tuple[float, tuple]:
        """`text` cherché à l'écran toutes les LOOK_EVERY s jusqu'à `timeout` s : (meilleur score, place)."""
        game = member.game
        end = time.monotonic() + timeout
        while True:
            score, point = best_text(game.look(park=False), text, dark=dark, bold=bold)
            if score >= FOUND or time.monotonic() >= end:
                return score, point
            game.sleep(LOOK_EVERY)

    def visit(self, member) -> bool:
        """Banquier -> « Parler » -> « Consulter son coffre personnel » -> onglet ressources -> « Transférer tous les
        objets » -> Échap. Le personnage est déjà dans la banque ; il en ressort avec les autres (run)."""
        game = member.game
        net = _net(member)
        started = time.monotonic()
        frame = game.look(park=False)
        told = net.pods_ratio() if net is not None else None
        before = pods_fill(frame) if told is None else told
        if not self.open_chest(member, frame):
            return False
        frame = game.look(park=False)
        layout = frame.layout
        game.click(layout.point(*RESOURCES_TAB), (5, 5))
        game.sleep(0.8)
        game.click(layout.point(*SORT_BUTTON), (5, 5))
        entry = None
        for _ in range(4):  # menu du bouton : 0,8 s fixes avant le 27/10 ; regardé toutes les 0,3 s, 1,2 s au plus
            game.sleep(0.3)
            frame = game.look(park=False)
            entry = match_template(frame, "banque/transferer_tous.png", box=layout.box(*MENU_ZONE), threshold=0.8)
            if entry is not None:
                break
        if entry is None:
            # Le menu du bouton est peut-être ouvert sans être reconnu : Échap devant lui ouvrirait le Menu principal
            # (31/10, Waldarch) ; fermeture sûre d'abord (01/11).
            close_unexpected(game, f"Banque, {member.name}")
            return self._fail(member, "« Transférer tous les objets » pas reconnu")
        pods_before = net.pods[0] if net is not None and net.pods else None
        bag_before = bag_quantities(net) if net is not None else {}
        clicked = time.monotonic()
        game.click(entry[0], (20, 3))
        if net is None:
            game.sleep(1.5)  # sans écoute : l'ancienne attente fixe, et la jauge de pods à l'écran (pas de preuve)
        else:
            # 31/10, revue : le transfert se prouve au serveur (pods Ow en baisse, piles du sac retirées ou diminuées) ;
            # sans preuve, c'est dit (et le coffre, déjà payé, n'est pas rouvert pour un second essai).
            proof: list[str | None] = [None]

            def proven() -> bool:
                proof[0] = transfer_proof(net, pods_before, bag_before)
                return proof[0] is not None

            if not _wait_until(proven, TRANSFER_WAIT):
                if resources_in_bag(net) is False:
                    log.info("%s : pas de ressource dans le sac d'après le serveur : rien à transférer", member.name)
                else:
                    escape_menu(game)  # ferme le coffre
                    self.final.add(member.name)
                    return self._fail(member, "« Transférer tous les objets » cliqué, mais le serveur n'annonce aucun "
                                              "objet sorti du sac (ni pods en baisse, ni OR / OQ)")
            else:
                log.info("%s : transfert prouvé par le serveur (%s)", member.name, proof[0])
            game.sleep(SETTLE)
        told = net.pods_ratio() if net is not None and net.pods_at >= clicked else None
        after = pods_fill(game.look(park=False)) if told is None else told
        if self.keep is not None:
            self._keep_keys(member)
        escape_menu(game)  # ferme le coffre (Échap ; Menu principal refermé s'il s'ouvre)
        log.info("%s : ressources au coffre (pods %.0f %% -> %.0f %%, %.1f s)", member.name, 100 * before, 100 * after,
                 time.monotonic() - started)
        if net is not None:
            # 27/10 : réponses du serveur à la banque pas encore relevées en jeu : gardées au journal pour les vérifier.
            log.debug("Serveur à la banque : %s", " | ".join(text for text in net.heard_since(started)
                                                              if text[:1] in "DEO" or text.startswith("Im")) or "rien")
        return True

    def _keep_keys(self, member) -> int:
        """Après « Transférer tous les objets » : la clé de donjon `keep` reprise du coffre (30/10, l'utilisateur : les
        personnages gardent leurs clés). Sans écoute du serveur : rien (pas de preuve de ce qui est au coffre)."""
        net = _net(member)
        if net is None:
            return 0
        stored = sum(quantity for _, quantity in chest_stacks(net, self.keep))
        if not stored:
            return 0
        try:
            taken = take_from_chest(member, self.keep, stored)
        except Exception:  # la clé reste au coffre ; le passage à la banque est fait
            log.exception("%s : clés pas reprises du coffre", member.name)
            return 0
        log.info("%s : clés gardées : %d reprise(s) du coffre sur %d", member.name, taken, stored)
        return taken

    def open_chest(self, member, frame=None) -> bool:
        """Banquier -> « Parler » -> « Consulter son coffre personnel » -> fenêtre « Coffre » vue. Le personnage est
        dans la banque ; False (capture gardée) au premier doute."""
        game = member.game
        frame = game.look(park=False) if frame is None else frame
        # 31/10, revue : un menu déjà ouvert (joueur, Flash) fermé avant tout clic, sans Échap (Menu principal derrière).
        if menu_open(frame) is not None:
            if not close_any_menu(game, f"Banque, {member.name}"):
                return self._fail(member, "un menu ouvert avant le banquier ne se ferme pas sans risque")
            frame = game.look(park=False)
        speak = None
        for bx, by in find_bankers(frame)[:2]:
            # 31/10, revue : clic sur le banquier les autres joueurs cachés (touche hide_key tenue), comme les portes :
            # un joueur posté devant le comptoir ouvrirait son menu à la place.
            self._click(game, (bx, by - frame.layout.length(8)), (3, 3))  # clic GAUCHE sur son corps
            game.sleep(0.8)
            menu = game.look(park=False)
            box = (max(0, round(bx - frame.layout.length(300))), max(0, round(by - frame.layout.length(200))),
                   min(frame.layout.width, round(bx + frame.layout.length(400))),
                   min(frame.layout.height, round(by + frame.layout.length(250))))
            speak = match_template(menu, "banque/menu_parler.png", box=box, threshold=FOUND)
            if speak is not None:
                break
            kind = menu_open(menu)
            if kind is not None:
                # Menu d'un joueur (ou de Flash) à la place de celui du banquier : refermé sans risque, puis l'autre
                # banquier ; rien de sûr : arrêt (aucun clic par-dessus un menu).
                log.info("%s : %s ouvert à la place du menu du banquier", member.name, kind)
                if not close_any_menu(game, f"Banque, {member.name}"):
                    return self._fail(member, f"{kind} ouvert à la place du menu du banquier, pas refermé sans risque")
        if speak is None:
            return self._fail(member, "« Parler » du banquier introuvable")
        clicked = time.monotonic()
        game.click(speak[0], (6, 3))
        self._wait(member, "question_at", clicked, 1.5)
        score, point = self._text(member, "Consulter son coffre personnel", True, clicked + 1.5 - time.monotonic())
        if score < FOUND:
            return self._fail(member, f"option « Consulter son coffre personnel » pas trouvée ({score:.2f})")
        clicked = time.monotonic()
        game.click(point, (20, 3))
        self._wait(member, "exchange_at", clicked, 2.0)
        score, _ = self._text(member, "Coffre", False, clicked + 2.5 - time.monotonic(), bold=True)
        if score < FOUND:
            return self._fail(member, f"fenêtre « Coffre » pas vue ({score:.2f})")
        log_chest_keys(member)
        return True


def log_chest_keys(member) -> dict[str, int]:
    """Clés de donjon du coffre ouvert, d'après la liste du serveur, au journal (06/11 : choisir le donjon suivant
    demande de savoir quelles clés l'équipe a ; seul le coffre de Walitcoin avait été lu). Nom -> nombre."""
    from bot.brain.inventory import key_names

    net = _net(member)
    exchange = net.trade()[0] if net is not None else None
    if exchange is None:
        return {}
    names = key_names()
    keys: dict[str, int] = {}
    for model, quantity in exchange.storage.values():
        if model in names and quantity > 0:
            keys[names[model]] = keys.get(names[model], 0) + quantity
    log.info("Coffre de %s : clés de donjon : %s", member.name,
             ", ".join(f"{name} {count}" for name, count in sorted(keys.items())) or "aucune")
    return keys


def _net(member):
    """Écoute du serveur de ce personnage si elle est vivante (connection/network.py) ; sinon None : l'écran."""
    net = getattr(member.game, "net", None)
    return net if net is not None and net.alive() else None
