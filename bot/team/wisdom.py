"""Sagesse des soutiens, équipée par le bot (08/11, l'utilisateur : « un mécanisme pour optimiser les soutiens :
analyser le stuff sur chaque perso et dans chaque banque, voir si certains permettent de donner plus de sagesse ; on ne
touche pas au stuff des DPS » ; puis « le bot devrait le faire lui-même »).

Les retraits de PA et de PM esquivables des soutiens (Flèche Glacée...) dépendent de la sagesse du lanceur ; la
sagesse fait aussi gagner plus d'expérience.

1. Plan (`plan_wisdom`, sans rien toucher) : objets portés et sacs de l'équipe d'après le serveur (jets compris), coffres
   d'après le dernier contenu vu (« EL », journaux réseau de logs/) ; pour chaque soutien et chaque emplacement, l'objet
   de plus de sagesse que le porté, à son niveau ; chaque objet une fois, le plus gros gain d'abord. Les objets portés
   par un DPS ne sont jamais pris.
2. Coffres : l'équipe à la banque, chaque propriétaire reprend ses objets (onglet équipement, recherche sur le nom,
   double-clic ; le serveur doit annoncer le bon modèle dans son sac).
3. Échanges : chaque objet donné à qui le portera (onglet équipement de la fenêtre d'échange, team/trade.py).
4. Équipement : recherche du sac sur le nom, double-clic ; le serveur doit annoncer un exemplaire de ce modèle dans
   l'emplacement voulu (« OM<uid>|<position> »).
Au moindre doute, l'étape est laissée (dit au journal) et la suite continue ; rien n'est vendu ni jeté.
"""

import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from bot.config import ROOT

log = logging.getLogger(__name__)

WISDOM, WISDOM_LESS = 124, 156  # effets « +sagesse », « -sagesse » (hexadécimal 7c, 9c)
# Positions des objets portés -> emplacement ; types de la langue du client (I.t) -> emplacement
SLOT_OF_POSITION = {0: "amulette", 1: "arme", 2: "anneau", 3: "ceinture", 4: "anneau", 5: "bottes", 6: "chapeau",
                    7: "cape", 8: "familier", 15: "bouclier"}
POSITIONS = {slot: tuple(p for p, s in SLOT_OF_POSITION.items() if s == slot) for slot in set(SLOT_OF_POSITION.values())}
TYPE_SLOT = {1: "amulette", 9: "anneau", 10: "ceinture", 11: "bottes", 16: "chapeau", 17: "cape", 18: "familier",
             82: "bouclier", **{t: "arme" for t in (2, 3, 4, 5, 6, 7, 8, 19, 20, 21, 22)}}
SLOT_ORDER = ("amulette", "anneau", "ceinture", "bottes", "chapeau", "cape", "familier", "bouclier", "arme")
LOGS = ROOT / "logs"
CHEST_EQUIP_TAB = (624, 220)  # onglet équipement du coffre (capture du 11/10 : à gauche de l'onglet ressources, 702)
TRADE_EQUIP_TAB = (1344, 186)  # onglet équipement du sac, fenêtre d'échange (à gauche des ressources, 1421)
EQUIP_WAIT = 2.5  # s : l'objet dans son emplacement d'après le serveur
MAX_SLOT_TRIES = 3  # cases du coffre filtré essayées quand la recherche laisse d'autres objets


@dataclass(eq=False)
class Gear:
    uid: int
    template: int
    wisdom: int
    holder: str  # personnage qui l'a (porté ou sac), ou propriétaire du coffre
    place: str  # « porté », « sac » ou « coffre »
    position: int = -1


@dataclass(frozen=True)
class Move:
    gear: Gear
    taker: str
    slot: str
    replaced: Gear | None
    gain: int


@lru_cache(maxsize=1)
def _items() -> dict:
    try:
        from tools.langue_client import LangueClient

        return LangueClient().table("I")["u"]
    except (OSError, KeyError):
        return {}


def item_level(template: int) -> int:
    return int(_items().get(str(template), {}).get("l", 0) or 0)


def item_slot(template: int) -> str | None:
    return TYPE_SLOT.get(int(_items().get(str(template), {}).get("t", 0) or 0))


def wisdom_of(effects: str) -> int:
    """Sagesse d'un objet d'après ses effets (« 7c#14##0d0+20,... », hexadécimal)."""
    total = 0
    for part in filter(None, (effects or "").split(",")):
        fields = part.split("#")
        try:
            ident, value = int(fields[0], 16), int(fields[1], 16) if len(fields) > 1 and fields[1] else 0
        except ValueError:
            continue
        total += value if ident == WISDOM else -value if ident == WISDOM_LESS else 0
    return total


def plan_wisdom(levels: dict[str, int], gear: list[Gear], supports: set[str], dps: set[str]) -> list[Move]:
    """Échanges d'objets qui donnent le plus de sagesse aux soutiens (`supports`) : `gear` = tout ce que l'équipe porte,
    a dans ses sacs et ses coffres ; chaque objet une fois, le plus gros gain d'abord ; jamais un objet porté par un DPS."""
    places = []  # (soutien, emplacement, objet porté ou None)
    for name in sorted(supports):
        worn = {g.position: g for g in gear if g.holder == name and g.place == "porté"}
        for slot in SLOT_ORDER:
            for position in POSITIONS[slot]:
                places.append((name, slot, worn.get(position)))
    candidates = [g for g in gear if g.wisdom > 0 and item_slot(g.template)
                  and not (g.place == "porté" and (g.holder in dps or g.holder in supports))]
    taken: set[int] = set()
    filled: set[int] = set()
    moves = []
    while True:
        best = None
        for index, (name, slot, worn) in enumerate(places):
            if index in filled:
                continue
            current = worn.wisdom if worn is not None else 0
            for item in candidates:
                if item.uid in taken or item_slot(item.template) != slot or item_level(item.template) > levels.get(name, 0):
                    continue
                gain = item.wisdom - current
                if gain > 0 and (best is None or gain > best[0]):
                    best = (gain, index, item)
        if best is None:
            return moves
        gain, index, item = best
        name, slot, worn = places[index]
        taken.add(item.uid)
        filled.add(index)
        moves.append(Move(item, name, slot, worn, gain))


def chest_contents(names: list[str], logs: Path = LOGS) -> dict[str, list[Gear]]:
    """Dernier contenu vu du coffre de chacun (« EL... » de son journal réseau, toutes sessions) ; [] s'il n'a jamais
    été ouvert sous écoute."""
    from bot.brain.inventory import parse_item

    out: dict[str, list[Gear]] = {}
    for name in names:
        safe = re.sub(r"[^\w\-]", "_", name)
        last = None
        for journal in sorted(logs.glob(f"*/reseau_{safe}.txt")):
            for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
                text = line.partition(" ")[2]
                if text.startswith("EL") and not text.startswith("ELE"):
                    last = text[2:]
        items = []
        for entry in filter(None, (last or "").split(";")):
            item = parse_item(entry[1:] if entry.startswith("O") else entry)
            if item is not None:
                items.append(Gear(item.uid, item.template, wisdom_of(item.effects), name, "coffre"))
        out[name] = items
    return out


def team_gear(members) -> tuple[dict[str, int], list[Gear], list[str]]:
    """(niveau de chacun, objets portés et des sacs d'après le serveur, noms dont le sac est inconnu)."""
    from bot.brain.inventory import BAG

    known: dict[str, int] = {}
    for member in members:  # niveaux du groupe d'après le serveur (PM+, comme DungeonFarm._levels)
        known.update(getattr(getattr(member.game, "net", None), "group_levels", None) or {})
    levels, gear, unknown = {}, [], []
    for member in members:
        net = getattr(member.game, "net", None)
        if net is None or not net.inventory.known or getattr(net.inventory, "restored", False):
            unknown.append(member.name)  # sac repris d'un fichier : sans les jets, la sagesse lue serait 0
            continue
        levels[member.name] = int(known.get(member.name, 0))
        for item in net.items():
            place = "sac" if item.position == BAG else "porté" if item.position in SLOT_OF_POSITION else None
            if place:
                gear.append(Gear(item.uid, item.template, wisdom_of(item.effects), member.name, place, item.position))
    return levels, gear, unknown


class WisdomGear:
    """Le plan, puis coffres, échanges et équipement (voir le début du module)."""

    def __init__(self, bot, dps: set[str], hide_key: str = "e") -> None:
        self.bot = bot
        self.members = bot.members
        self.by_name = {m.name: m for m in self.members}
        self.dps = set(dps)
        self.hide_key = hide_key
        self.portraits: dict = {}

    def plan(self) -> list[Move] | None:
        from bot.brain.inventory import item_name

        levels, gear, unknown = team_gear(self.members)
        if unknown:
            log.warning("Sagesse : sac de %s inconnu (connecté avant le bot) : relancer avec --connexion pour le lire",
                        ", ".join(unknown))
            return None
        chests = chest_contents([m.name for m in self.members])
        gear += [g for items in chests.values() for g in items]
        supports = {m.name for m in self.members} - self.dps
        moves = plan_wisdom(levels, gear, supports, self.dps)
        for name in sorted(supports):
            mine = [m for m in moves if m.taker == name]
            worn = sum(g.wisdom for g in gear if g.holder == name and g.place == "porté")
            log.info("Sagesse : %s (niveau %d) : +%d portée, +%d de plus prévus%s", name, levels.get(name, 0), worn,
                     sum(m.gain for m in mine), "".join(
                         f" ; {m.slot} : {item_name(m.gear.template)} ({m.gear.wisdom:+d}, {m.gear.place} de "
                         f"{m.gear.holder})" for m in mine))
        return moves

    def run(self) -> int:
        """Objets équipés (confirmés par le serveur) ; 0 si rien à faire ou plan impossible."""
        moves = self.plan()
        if not moves:
            log.info("Sagesse : rien à changer")
            return 0
        self._from_chests([m for m in moves if m.gear.place == "coffre"])
        for move in moves:
            if move.gear.holder != move.taker:
                self._give(move)
        done = 0
        for move in moves:
            if self._equip(move):
                done += 1
        log.info("Sagesse : %d objet(s) équipé(s) sur %d prévus", done, len(moves))
        return done

    # --- Étapes ---

    def _from_chests(self, moves: list[Move]) -> None:
        if not moves:
            return
        from bot.team import pods
        from bot.team.bank import BANK_MAP, DOOR, EXIT, BankVisit

        owners = [self.by_name[n] for n in dict.fromkeys(m.gear.holder for m in moves) if n in self.by_name]
        from bot.brain.building import leave_room

        for member in self.members:  # 08/11 : équipe dans la salle d'entrée des Blops, sortie par un soleil au sol
            net = getattr(member.game, "net", None)
            if net is not None and not leave_room(member.game, net, self.hide_key, member.name):
                log.warning("Sagesse : %s pas sorti de sa salle : objets des coffres laissés", member.name)
                return
        if not pods.team_trip(self.bot, BANK_MAP, "Banque : objets de sagesse"):
            log.warning("Sagesse : équipe pas arrivée à la banque : objets des coffres laissés")
            return
        visit = BankVisit(owners, self.hide_key)
        entered = visit._doors(owners, DOOR, inside=True)
        from bot.brain.travel import escape_menu

        for owner in owners:
            if owner.name not in entered:
                continue
            try:
                if visit.open_chest(owner):
                    for move in [m for m in moves if m.gear.holder == owner.name]:
                        if take_gear(owner, move.gear.template):
                            self._moved(move, owner.name)
            except Exception:
                log.exception("Sagesse : %s : erreur au coffre", owner.name)
            finally:
                escape_menu(owner.game)
        inside = [m for m in owners if m.name in entered]
        visit._doors(inside, EXIT, inside=False)

    @staticmethod
    def _moved(move: Move, holder: str) -> None:
        """L'objet est maintenant dans le sac de `holder` (repris du coffre, ou reçu)."""
        move.gear.holder, move.gear.place = holder, "sac"

    def _give(self, move: Move) -> bool:
        from bot.brain.inventory import item_name
        from bot.team.keys import NO_LIMIT
        from bot.team.trade import Trader, free_pods

        if move.gear.place != "sac":
            log.info("Sagesse : %s pas dans un sac (%s de %s) : pas d'échange", item_name(move.gear.template),
                     move.gear.place, move.gear.holder)
            return False
        giver, taker = self.by_name.get(move.gear.holder), self.by_name.get(move.taker)
        if giver is None or taker is None:
            return False
        trader = Trader(giver, taker, self.portraits, self.members)
        if not trader.open():
            return False
        try:
            from bot.brain.inventory import BAG

            names = [item_name(i.template) for i in giver.game.net.items()
                     if i.position == BAG and i.template != move.gear.template]
            query = unique_query(item_name(move.gear.template), names) or None
            if not trader.offer_item(move.gear.template, 1, tab=TRADE_EQUIP_TAB, query=query):
                recorder = getattr(giver.game, "recorder", None)
                if recorder is not None:  # 08/11 : Pesopepe, objets repris du coffre jamais posés : à voir
                    path = recorder.save(giver.game.look(park=False).image, "sagesse_echange_rate", lossless=True)
                    log.info("Sagesse : échange %s -> %s raté (recherche « %s », capture : %s)", giver.name,
                             taker.name, query, path)
                trader.cancel()
                return False
            free = free_pods(taker.game.net)
            if trader.conclude(free if free is not None else NO_LIMIT):
                self._moved(move, taker.name)
                return True
            return False
        except Exception:
            log.exception("Sagesse : échange %s -> %s : erreur, annulé", giver.name, taker.name)
            trader.cancel()
            return False

    def _equip(self, move: Move) -> bool:
        from bot.brain.inventory import item_name

        member = self.by_name.get(move.taker)
        if member is None or move.gear.holder != move.taker:
            log.info("Sagesse : %s pas chez %s : pas équipé", item_name(move.gear.template), move.taker)
            return False
        ok = equip_gear(member.game, move.gear.template, POSITIONS[move.slot])
        log.info("Sagesse : %s : %s %s (%s, %+d)", move.taker, item_name(move.gear.template),
                 "équipé" if ok else "PAS équipé", move.slot, move.gear.wisdom)
        return ok


def equip_gear(game, template: int, positions: tuple[int, ...]) -> bool:
    """Équipe l'objet de modèle `template` du sac (recherche du sac sur son nom, double-clic) ; True si le serveur
    annonce un exemplaire de ce modèle, absent avant, dans une des `positions` (anneaux : 2 ou 4)."""
    from bot.brain.equipment import BAG, BAG_TOP, FIRST_SLOT, FOUND, INVENTORY_KEY, OPEN_WAIT, close_bag_search, \
        search_bag
    from bot.brain.inventory import BAG as IN_BAG
    from bot.brain.inventory import item_name
    from bot.brain.travel import escape_menu
    from bot.team.bank import best_text

    net = getattr(game, "net", None)
    name = item_name(template)
    if net is None or not net.inventory.known:
        log.info("%s : sac inconnu : pas équipé", name)
        return False
    if not any(i.template == template and i.position == IN_BAG for i in net.items()):
        log.info("%s pas dans le sac d'après le serveur : rien à équiper", name)
        return False
    worn_before = {i.uid for i in net.items() if i.template == template and i.position in positions}
    frame = game.look(park=False)
    score, _ = best_text(frame, "Ton inventaire", dark=False, bold=True)
    if score < FOUND:
        game.press(INVENTORY_KEY)
        game.sleep(OPEN_WAIT)
        frame = game.look(park=False)
        score, _ = best_text(frame, "Ton inventaire", dark=False, bold=True)
    if score < FOUND:
        log.info("Inventaire pas vu : %s pas équipé", name)
        escape_menu(game)
        return False
    layout = frame.layout
    try:
        # Texte qui ne laisse que cet objet dans le sac (« aventurier » laisserait aussi les bottes, la ceinture...)
        others = [item_name(i.template) for i in net.items() if i.position == IN_BAG and i.template != template]
        if not search_bag(game, layout, unique_query(name, others) or name):
            log.info("%s : recherche du sac pas faite : pas de double-clic", name)
            return False
        game.wheel(layout.point(*BAG), -BAG_TOP)
        game.sleep(0.3)
        game.double_click(layout.point(*FIRST_SLOT), (3, 3))
        deadline = time.monotonic() + EQUIP_WAIT
        while time.monotonic() < deadline:
            now = {i.uid for i in net.items() if i.template == template and i.position in positions}
            if now - worn_before:
                return True
            game.sleep(0.2)
        return False
    finally:
        close_bag_search(game, layout)
        escape_menu(game)


def take_gear(member, template: int) -> bool:
    """Coffre ouvert : reprend UN objet de modèle `template` (onglet équipement, recherche sur son nom, double-clic sur la
    première case) ; True si le serveur annonce ce modèle quitté du coffre et arrivé dans le sac."""
    from bot.brain.equipment import close_bag_search, search_bag
    from bot.brain.inventory import item_name
    from bot.team.bank import CHEST_FILTER, CHEST_FIRST_STACK, CHEST_SEARCH, CHEST_WAIT, _net, chest_stacks, \
        search_word

    game, net = member.game, _net(member)
    if net is None:
        return False
    name = item_name(template)
    if not chest_stacks(net, template):
        log.info("%s : %s plus dans le coffre d'après le serveur", member.name, name)
        return False
    held = net.held(template) or 0
    layout = game.look(park=False).layout
    game.click(layout.point(*CHEST_EQUIP_TAB), (5, 5))
    game.sleep(0.6)
    exchange, _ = net.trade()
    others = [item_name(model) for model, _ in (exchange.storage.values() if exchange is not None else [])
              if model != template]
    words = unique_query(name, others) or search_word(name)
    if not words or not search_bag(game, layout, words, CHEST_FILTER, CHEST_SEARCH):
        log.info("%s : recherche du coffre sur « %s » pas faite : %s laissé", member.name, words, name)
        return False
    # Recherche qui laisse aussi d'autres objets (« Amulette de l » : l'Amulette de la Boule aussi) : les cases
    # suivantes essayées, chaque objet arrivé vérifié par le serveur (un autre objet repris reste dans le sac).
    plain = words.lower()
    tries = min(MAX_SLOT_TRIES, 1 + sum(plain in o.lower() for o in others))
    from bot.team.bank import CHEST_COLUMNS, CHEST_STEP, grid_point

    try:
        for rank in range(tries):
            game.double_click(layout.point(*grid_point(CHEST_FIRST_STACK, rank, CHEST_STEP, CHEST_COLUMNS)), (3, 3))
            deadline = time.monotonic() + CHEST_WAIT
            while time.monotonic() < deadline:
                if (net.held(template) or 0) > held:
                    log.info("%s : %s repris du coffre", member.name, name)
                    return True
                game.sleep(0.2)
            if rank + 1 < tries:
                log.info("%s : case %d du coffre filtré : pas %s, case suivante", member.name, rank + 1, name)
        log.warning("%s : %s pas arrivé dans le sac d'après le serveur (%d case(s) essayée(s))", member.name, name,
                    tries)
        return False
    finally:
        close_bag_search(game, layout, CHEST_FILTER, CHEST_SEARCH)


def unique_query(name: str, others: list[str]) -> str:
    """Texte à taper dans la recherche du coffre pour ne laisser que `name` (08/11 : « Aventurier » laissait aussi les
    bottes, la ceinture et l'anneau de l'Aventurier ; « Ceinture du Rat » donnait un mot vide) : morceaux du nom sans
    apostrophe ni accent (ils ne se tapent pas en arrière-plan), de 3 lettres ou plus ; celui que le moins d'autres
    objets du coffre contiennent, le plus long à égalité. "" sans morceau utilisable."""
    import unicodedata

    def plain(text: str) -> str:
        text = unicodedata.normalize("NFKD", text)
        return "".join(c for c in text if not unicodedata.combining(c)).lower()

    runs = [r.strip() for r in re.split(r"[’'-￿]", name) if r.strip()]
    pieces = set()
    for run in runs:
        words = run.split()
        for i in range(len(words)):
            for j in range(i + 1, len(words) + 1):
                piece = " ".join(words[i:j])
                if len(piece) >= 3:
                    pieces.add(piece)
    if not pieces:
        return ""
    names = [plain(o) for o in others]
    return min(pieces, key=lambda q: (sum(plain(q) in n for n in names), -len(q), q))

