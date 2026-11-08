"""Inventaire, échanges et coffre d'après le serveur (connection/network.py), sans rien lire à l'écran (30/10).

Demande de l'utilisateur (30/10) : savoir qui porte quelles clés de donjon, les garder hors du coffre, et les répartir
entre les personnages par des échanges pour le farm de donjon.

Messages lus (serveur -> client). Les nombres d'une chaîne d'objet sont en hexadécimal, ceux des autres messages en
décimal (27/10 : coffre « ELO244eb62~6f1~7f~~ » puis « EsKO+...|...|1777| », 0x6f1 = 1777) :
- « ASK|id|nom|niveau|...|objets » : personnage choisi, son inventaire (dernier champ) ; chaque objet
  « uid~modèle~quantité~position~effets », position vide = dans le sac, 1 = arme ;
- « OAKO<objet>;<objet>;... » : objet ajouté (pile nouvelle ou coupée) ;
- « OQ<uid>|<quantité> » : nouvelle quantité d'une pile ; « OR<uid> » : pile retirée ; « OM<uid>|<position> » :
  objet déplacé (position vide : retour au sac).
Échanges (30/10, essai en jeu Fourboun -> Enu-six, confirmé) :
- « ERK<demandeur>|<invité>|<type> » aux deux fenêtres (type 1 : échange entre joueurs) ; « ECK<type> » : ouvert
  (1 échange, 5 coffre, 27/10) ; « EV » : fermé (annulé par la croix) ;
- « EMKO+<uid>|<quantité> » : mon offre, quantité TOTALE de cette pile dans l'offre (1, puis 10) ; « EmKO+<uid>|
  <quantité>|<modèle>| » : l'offre de l'autre, avec le modèle (289 : Blé) ;
- coffre (27/10) : « EL » son contenu (« O<objet> » séparés par « ; »), « EsKO+<uid>|<quantité>|<modèle>|<effets> »
  à chaque dépôt (quantité totale de la pile du coffre ; l'inventaire reçoit « OR » ou « OQ »).
Pas encore vus, d'après le client 1.29 (à vérifier en jeu) : « EMKO-<uid> » / « EmKO-<uid> » / « EsKO-<uid> » (pile
retirée de l'offre ou du coffre), « EMKG<kamas> », « EK1<id> » / « EK0<id> » (« Accepter » coché ou décoché),
« EVa » (échange conclu).

Les modèles (numéros d'objet) se traduisent par la langue du client (tools/langue_client.py, table I) : type 84
« Clefs », de super-type 9, celui des ressources (onglet ressources, et donc « Transférer tous les objets » au coffre).
"""

import logging
from dataclasses import dataclass, field
from functools import lru_cache

log = logging.getLogger(__name__)

BAG = -1  # position d'un objet dans le sac (champ vide)
WEAPON = 1
TYPE_KEYS = 84  # langue I.t["84"] = {"n": "Clefs", "t": 9}
SUPER_RESOURCES = 9


@dataclass
class Item:
    uid: int
    template: int  # numéro d'objet de la langue du client (I.u)
    quantity: int
    position: int = BAG
    effects: str = ""


def parse_item(entry: str) -> Item | None:
    """« uid~modèle~quantité~position~effets » (hexadécimal, position vide : sac) ; None si ce n'en est pas un."""
    values = entry.split("~")
    if len(values) < 3:
        return None
    try:
        uid, template, quantity = (int(v, 16) for v in values[:3])
        position = int(values[3], 16) if len(values) > 3 and values[3] else BAG
    except ValueError:
        return None
    return Item(uid, template, quantity, position, values[4] if len(values) > 4 else "")


@dataclass
class Exchange:
    """Échange ou coffre ouvert (de « ECK » à « EV »)."""

    kind: int  # 1 échange entre joueurs, 5 coffre
    opened_at: float
    mine: dict[int, int] = field(default_factory=dict)  # uid de ma pile -> quantité dans mon offre
    theirs: dict[int, tuple[int, int]] = field(default_factory=dict)  # uid -> (modèle, quantité) dans l'autre offre
    storage: dict[int, tuple[int, int]] = field(default_factory=dict)  # coffre : uid -> (modèle, quantité)
    kamas_mine: int = 0
    kamas_theirs: int = 0
    kamas_storage: int = 0
    ready: dict[int, bool] = field(default_factory=dict)  # id du personnage -> « Accepter » coché (EK, à vérifier)
    changed_at: float = 0.0  # dernière offre changée (EMK, EmK, EsK)
    closed_at: float = 0.0  # EV
    concluded: bool = False  # « EVa » (à vérifier) : échange conclu, pas annulé


def _int(text: str) -> int | None:
    return int(text) if text.lstrip("-").isdigit() else None


class Inventory:
    """Ce que porte le personnage d'une fenêtre, d'après le serveur. Pas de verrou : ClientFeed le tient."""

    def __init__(self) -> None:
        self.items: dict[int, Item] = {}  # uid -> objet, dans l'ordre d'arrivée
        self.known = False  # ASK reçu (connexion suivie) : sans lui, l'inventaire est incomplet
        # Repris d'un fichier d'une session d'avant (brain/possessions.py) : sans les jets des objets (08/11)
        self.restored = False
        self.my_id: int | None = None
        self.request: tuple[int, int, int, float] | None = None  # dernier ERK : (demandeur, invité, type, heure)
        self.exchange: Exchange | None = None  # le dernier ouvert (fermé si closed_at)

    # Côté fil d'écoute

    def feed(self, text: str, now: float = 0.0) -> bool:
        """Applique un message du serveur ; True s'il concernait l'inventaire, un échange ou le coffre."""
        if text.startswith("ASK|"):
            return self._login(text)
        if text.startswith("OAK"):
            return self._added(text[3:])
        if text.startswith(("OQ", "OR", "OM")):
            return self._object(text)
        if text.startswith("E"):
            return self._exchange(text, now)
        return False

    def _login(self, text: str) -> bool:
        fields = text.split("|")
        if len(fields) < 3:
            return False
        self.my_id = _int(fields[1])
        self.items = {}
        # Objets : 11e champ (30/10 : « ASK|id|nom|niveau|...|objets|0 », un champ suit la liste ; le dernier champ
        # pris pour la liste donnait des sacs vides aux 8 personnages).
        for entry in (fields[10] if len(fields) > 10 else fields[-1]).split(";"):
            item = parse_item(entry)
            if item is not None:
                self.items[item.uid] = item
        self.known = True
        self.restored = False
        return True

    def _added(self, body: str) -> bool:
        changed = False
        for part in body.split("*"):
            if not part.startswith("O"):
                continue
            for entry in part[1:].split(";"):
                item = parse_item(entry)
                if item is not None:
                    self.items[item.uid] = item
                    changed = True
        return changed

    def _object(self, text: str) -> bool:
        values = text[2:].split("|")
        uid = _int(values[0])
        if uid is None:
            return False
        item = self.items.pop(uid, None) if text.startswith("OR") else self.items.get(uid)
        if item is None:
            if self.known:
                # 30/10 : clé d'Elseiso donnée au PNJ du donjon Bouftou, restée dans ses possessions : un retrait d'un
                # objet que le sac connu n'a pas est dit, pour retrouver d'où vient l'écart.
                log.info("Inventaire : « %s » pour un objet absent du sac connu (sac gardé pas à jour ?)", text[:40])
            return False
        if text.startswith("OR"):
            return True
        if len(values) < 2:
            return False
        if text.startswith("OQ"):
            quantity = _int(values[1])
            if quantity is None:
                return False
            item.quantity = quantity
        else:
            item.position = _int(values[1]) if values[1] else BAG
        return True

    def _exchange(self, text: str, now: float) -> bool:
        exchange = self.exchange if self.exchange is not None and not self.exchange.closed_at else None
        if text.startswith("ERK"):
            values = [_int(v) for v in text[3:].split("|")]
            if len(values) >= 3 and None not in values[:3]:
                self.request = (values[0], values[1], values[2], now)
                return True
            return False
        if text.startswith("ECK"):
            kind = _int(text[3:].split("|")[0])
            if kind is None:
                return False
            self.exchange = Exchange(kind, now)
            return True
        if exchange is None:
            return False
        if text.startswith("EV"):
            exchange.closed_at, exchange.concluded = now, text == "EVa"
            return True
        if text.startswith("EL") and exchange.kind == 5:
            for entry in text[2:].split(";"):
                if entry.startswith("O"):
                    item = parse_item(entry[1:])
                    if item is not None:
                        exchange.storage[item.uid] = (item.template, item.quantity)
                elif entry.startswith("G") and _int(entry[1:]) is not None:
                    exchange.kamas_storage = int(entry[1:])
            return True
        if text.startswith("EK") and len(text) > 3 and text[2] in "01" and _int(text[3:]) is not None:
            exchange.ready[int(text[3:])] = text[2] == "1"
            return True
        side = {"EMK": "mine", "EmK": "theirs", "EsK": "storage"}.get(text[:3])
        if side is None or len(text) < 5:
            return False
        exchange.changed_at = now
        if text[3] == "G":
            kamas = _int(text[4:])
            if kamas is not None:
                setattr(exchange, f"kamas_{side}", kamas)
            return kamas is not None
        if text[3] != "O" or text[4:5] not in ("+", "-"):
            return False
        values = text[5:].split("|")
        uid = _int(values[0])
        offer = getattr(exchange, side)
        if uid is None:
            return False
        if text[4] == "-":
            offer.pop(uid, None)
            return True
        quantity = _int(values[1]) if len(values) > 1 else None
        if quantity is None:
            return False
        if side == "mine":
            offer[uid] = quantity  # modèle : celui de ma pile (self.items)
        else:
            template = _int(values[2]) if len(values) > 2 else None
            offer[uid] = (template if template is not None else offer.get(uid, (0, 0))[0], quantity)
        return True

    # Côté bot

    def count(self, template: int) -> int:
        """Nombre d'objets de ce modèle portés (sac et équipement)."""
        return sum(item.quantity for item in self.items.values() if item.template == template)

    def stacks(self, template: int) -> list[Item]:
        return [item for item in self.items.values() if item.template == template]

    def bag(self) -> list[Item]:
        """Objets du sac, dans l'ordre d'arrivée (hypothèse à vérifier en jeu : l'ordre de la grille de l'inventaire)."""
        return [item for item in self.items.values() if item.position == BAG]

    def offered(self, template: int) -> int:
        """Objets de ce modèle dans mon offre de l'échange ouvert (0 sans échange)."""
        if self.exchange is None or self.exchange.closed_at:
            return 0
        return sum(qty for uid, qty in self.exchange.mine.items()
                   if uid in self.items and self.items[uid].template == template)

    def received(self, template: int) -> int:
        """Objets de ce modèle dans l'offre de l'autre (0 sans échange)."""
        if self.exchange is None or self.exchange.closed_at:
            return 0
        return sum(qty for model, qty in self.exchange.theirs.values() if model == template)


# --- Modèles d'objets (langue du client) ----------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _items() -> tuple[dict, dict]:
    """Tables I.u (modèle -> objet) et I.t (type -> nom) de la langue du client ; vides sans data/client_retro."""
    try:
        from tools.langue_client import LangueClient  # import local : lecture des fichiers de langue du client

        table = LangueClient().table("I")
        return table["u"], table["t"]
    except (OSError, KeyError):
        log.debug("Langue du client illisible : objets inconnus", exc_info=True)
        return {}, {}


def item_name(template: int) -> str:
    """Nom exact du modèle (« Clef du Donjon des Bouftous ») ; « modèle N » s'il est inconnu."""
    return _items()[0].get(str(template), {}).get("n") or f"modèle {template}"


RESOURCES = 9  # catégorie des types de l'onglet « ressources » du sac (I.t[type].t : cuirs, laines, plantes, clés...)


def item_category(template: int) -> int | None:
    """Catégorie (onglet du sac) du type du modèle, d'après la langue du client ; None si elle est inconnue."""
    objects, types = _items()
    kind = objects.get(str(template), {}).get("t")
    return types.get(str(kind), {}).get("t") if kind is not None else None


def item_type_name(template: int) -> str | None:
    """Nom du type du modèle (« Faux », « Clefs »...), d'après la langue du client ; None s'il est inconnu."""
    objects, types = _items()
    kind = objects.get(str(template), {}).get("t")
    return types.get(str(kind), {}).get("n") if kind is not None else None


# --- Clés de donjon (langue du client) ------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def key_names() -> dict[int, str]:
    """Modèle -> nom des objets « Clefs » (type 84) de la langue du client ; {} sans data/client_retro."""
    try:
        from tools.langue_client import LangueClient  # import local : lecture des fichiers de langue du client

        objects = LangueClient().table("I")["u"]
    except (OSError, KeyError):
        log.debug("Langue du client illisible : clés inconnues", exc_info=True)
        return {}
    return {int(k): o.get("n", "") for k, o in objects.items() if o.get("t") == TYPE_KEYS}


def dungeon_key(dungeon: str) -> int | None:
    """Clé du donjon nommé `dungeon` (« Donjon d'Incarnam » -> 8545) : celle dont le nom finit par ce nom, sinon celle
    dont la description le cite (« Clef des Champs » : « ... du Donjon des Champs ») ; None si aucune ou plusieurs."""
    wanted = dungeon.casefold()
    names = key_names()
    by_name = [k for k, n in names.items() if n.casefold().endswith(wanted)]
    if len(by_name) == 1:
        return by_name[0]
    if by_name:
        return None
    try:
        from tools.langue_client import LangueClient

        objects = LangueClient().table("I")["u"]
    except (OSError, KeyError):
        return None
    by_text = [k for k in names if wanted in objects[str(k)].get("d", "").casefold()]
    if len(by_text) == 1:
        return by_text[0]
    # 31/10 : « Donjon Bouftou » et « Clef du Donjon des Bouftous » : les mots du nom du donjon, sans accents, petits
    # mots ni pluriel, tous dans le nom d'une seule clé (comme tools/donjons_reference.py).
    words = _words(dungeon)
    by_words = [k for k, n in names.items() if words and words <= _words(n)]
    return by_words[0] if len(by_words) == 1 else None


SMALL_WORDS = {"donjon", "des", "du", "de", "la", "le", "les", "d", "l"}


def _words(text: str) -> set[str]:
    """Mots significatifs d'un nom, sans accents ni « s » final."""
    import unicodedata

    plain = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
    return {w.rstrip("s") for w in "".join(c if c.isalnum() else " " for c in plain).split()
            if w not in SMALL_WORDS and len(w) > 2}


@dataclass
class KeyPlan:
    each: int  # clés visées pour chacun
    moves: list[tuple[str, str, int]]  # (donneur, receveur, nombre), dans l'ordre où les faire
    missing: int  # clés qui manquent pour en avoir `wanted` chacun (0 : assez)


def plan_keys(held: dict[str, int], wanted: int = 1) -> KeyPlan:
    """Échanges pour que chacun ait `wanted` clés (moins s'il n'y en a pas assez : autant pour tous, au moins une) ;
    du plus pourvu vers le moins pourvu, le moins d'échanges possible. Aucun échange s'il n'y a pas une clé par
    personnage (le bot le dit et n'entre pas : une équipe coupée en deux perdrait ses clés)."""
    names = list(held)
    total = sum(held.values())
    each = min(wanted, total // len(names)) if names else 0
    missing = max(0, wanted * len(names) - total)
    if each <= 0:
        return KeyPlan(0, [], missing)
    surplus = {n: held[n] - each for n in names if held[n] > each}
    need = {n: each - held[n] for n in names if held[n] < each}
    moves = []
    while need:
        giver = max(surplus, key=lambda n: (surplus[n], -names.index(n)))
        taker = max(need, key=lambda n: (need[n], -names.index(n)))
        count = min(surplus[giver], need[taker])
        moves.append((giver, taker, count))
        surplus[giver] -= count
        need[taker] -= count
        if not surplus[giver]:
            del surplus[giver]
        if not need[taker]:
            del need[taker]
    return KeyPlan(each, moves, missing)
