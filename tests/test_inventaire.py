import pytest

from bot.brain.inventory import BAG, Inventory, dungeon_key, key_names, parse_item, plan_keys
from bot.connection.network import ClientFeed

# Inventaire à la connexion (forme de « ASK », objets en hexadécimal) : 10 Blé (0x121 = 289) au sac, une Clef du Donjon
# d'Incarnam (0x2161 = 8545) en 3 exemplaires, une arme (position 1).
ASK = ("ASK|986971|Enu-six-[Gal]|23|0|1|11|ffffff|-1|-1|"
       "11655e56~121~a~~;11655e57~2161~3~~;2ef0a3c~970~1~1~63#4#8#1d5+3;")
WHEAT, KEY = 289, 8545


def test_an_item_string_is_hexadecimal_and_an_empty_position_is_the_bag():
    item = parse_item("244eb62~6f1~7f~~")  # 27/10, coffre de Walitcoin : 127 objets du modèle 1777
    assert (item.uid, item.template, item.quantity, item.position) == (0x244eb62, 1777, 127, BAG)
    assert parse_item("2fb92c1~970~1~1~63#4#8#1d5+3").position == 1
    assert parse_item("O") is None and parse_item("zz~1~1~") is None


def test_the_login_inventory_then_additions_quantities_moves_and_removals():
    inventory = Inventory()
    assert inventory.feed(ASK)
    assert inventory.known and inventory.my_id == 986971
    assert inventory.count(WHEAT) == 10 and inventory.count(KEY) == 3
    assert [i.template for i in inventory.bag()] == [WHEAT, KEY]  # l'arme n'est pas dans le sac
    # Dans les autres messages, l'uid est en décimal : 0x11655e57 = 291855959.
    assert inventory.feed("OQ291855959|2")
    assert inventory.count(KEY) == 2
    assert inventory.feed("OAKO11655e60~2161~1~~;")  # une clé reçue, en pile à part
    assert inventory.count(KEY) == 3
    assert inventory.feed("OM291855959|23") and inventory.items[0x11655e57].position == 23
    assert inventory.feed("OM291855959|") and inventory.items[0x11655e57].position == BAG
    assert inventory.feed("OR291855959")
    assert inventory.count(KEY) == 1
    assert not inventory.feed("OR1")  # inconnu : rien ne change
    assert not inventory.feed("Ow372|0|1800|4300") and not inventory.feed("Oa1999174|,,69f,,")


def test_the_trade_seen_in_game_on_30_10():
    # Fourboun (1610317) propose un échange à Enu-six (986971), qui accepte ; Fourboun pose 1 Blé puis le reste de la
    # pile (la quantité est le total dans l'offre) ; puis la croix.
    giver, taker = Inventory(), Inventory()
    giver.feed("ASK|1610317|Fourboun|52|0|1|11|0|0|0|11655e56~121~a~~;")
    taker.feed(ASK)
    for inventory in (giver, taker):
        assert inventory.feed("ERK1610317|986971|1", now=1.0)
        assert inventory.request == (1610317, 986971, 1, 1.0)
        assert inventory.feed("ECK1", now=2.0)
    assert giver.feed("EMKO+291855958|1", now=3.0)
    assert taker.feed("EmKO+291855958|1|289|", now=3.0)
    assert giver.offered(WHEAT) == 1 and taker.received(WHEAT) == 1
    giver.feed("EMKO+291855958|10", now=4.0)
    taker.feed("EmKO+291855958|10|289|", now=4.0)
    assert giver.offered(WHEAT) == 10 and taker.received(WHEAT) == 10
    assert giver.exchange.changed_at == 4.0
    for inventory in (giver, taker):
        assert inventory.feed("EV", now=5.0)
        assert inventory.exchange.closed_at == 5.0 and not inventory.exchange.concluded
    assert giver.offered(WHEAT) == 0 and taker.received(WHEAT) == 0
    assert not giver.feed("EMKO+291855958|3")  # plus d'échange ouvert


def test_messages_from_the_1_29_client_not_yet_seen_in_game():
    inventory = Inventory()
    inventory.feed(ASK)
    inventory.feed("ECK1")
    inventory.feed("EMKO+291855959|2")
    inventory.feed("EmKO+5|4|289|")
    assert inventory.feed("EK11610317") and inventory.exchange.ready == {1610317: True}
    assert inventory.feed("EK01610317") and inventory.exchange.ready == {1610317: False}
    assert inventory.feed("EMKO-291855959") and inventory.offered(KEY) == 0
    assert inventory.feed("EmKO-5") and inventory.received(WHEAT) == 0
    assert inventory.feed("EMKG150") and inventory.exchange.kamas_mine == 150
    assert inventory.feed("EVa") and inventory.exchange.concluded


def test_the_bank_chest_of_27_10():
    # Fourboun à la banque (journal du 27/10) : la liste du coffre, puis chaque pile déposée retirée de l'inventaire
    # (OR, décimal) et ajoutée au coffre (EsKO+, décimal, quantité totale de la pile du coffre).
    inventory = Inventory()
    inventory.feed("ASK|1610317|Fourboun|52|0|1|11|0|0|0|244ee10~12d~b0~~;" + "2450a51~6f1~3~~;")
    assert inventory.feed("ECK5", now=1.0)
    assert inventory.feed("ELO244eb61~6ea~84~~;O244eb62~6f1~7f~~;G1200")
    assert inventory.exchange.storage[0x244eb62] == (1777, 127) and inventory.exchange.kamas_storage == 1200
    assert inventory.feed("OR38079057")  # 0x2450a51
    assert inventory.feed("EsKO+38070114|130|1777|", now=2.0)
    assert inventory.exchange.storage[38070114] == (1777, 130)
    assert inventory.count(1777) == 0
    assert inventory.feed("EV", now=3.0)


def test_the_client_feed_keeps_the_inventory_and_its_other_roles():
    feed = ClientFeed("Enu-six-[Gal]")
    assert feed.held(KEY) is None  # connexion pas suivie : inconnu, pas zéro
    feed.feed(ASK, now=1.0)
    assert feed.held(KEY) == 3 and feed.inventory_at == 1.0
    feed.feed("OAKO2fb92c1~970~1~1~63#4#8#1d5+3;", now=2.0)
    assert feed.weapon_at == 2.0  # rôle d'avant gardé
    feed.feed("ECK1", now=3.0)
    assert feed.exchange_at == 3.0
    feed.feed("EMKO+291855959|2", now=4.0)
    exchange, mine = feed.trade()
    assert exchange.kind == 1 and mine == {KEY: 2}
    assert feed.wait_for("inventory_at", 4.0, 0.1) == 4.0
    feed.feed("EV", now=5.0)
    assert feed.exchange_left_at == 5.0 and feed.trade()[0].closed_at == 5.0
    assert [i.template for i in feed.items()] == [WHEAT, KEY, 2416, 2416]  # arme de ASK, puis celle de OAKO


def test_keys_shared_from_the_best_provided():
    plan = plan_keys({"Fourboun": 5, "Enu-six": 0, "Elseiso": 2, "Pesopepe": 0}, wanted=1)
    assert plan.each == 1 and plan.missing == 0
    assert plan.moves == [("Fourboun", "Enu-six", 1), ("Fourboun", "Pesopepe", 1)]
    plan = plan_keys({"A": 9, "B": 0, "C": 0}, wanted=3)
    assert plan.each == 3 and plan.moves == [("A", "B", 3), ("A", "C", 3)]


def test_not_enough_keys_for_everyone():
    plan = plan_keys({"A": 2, "B": 0, "C": 0}, wanted=1)
    assert plan == plan_keys({"A": 2, "B": 0, "C": 0}) and plan.each == 0 and plan.moves == [] and plan.missing == 1
    plan = plan_keys({"A": 4, "B": 1, "C": 0}, wanted=3)  # 5 clés : une chacun, 4 manquent pour 3 chacun
    assert plan.each == 1 and plan.moves == [("A", "C", 1)] and plan.missing == 4
    assert plan_keys({"A": 1, "B": 1}).moves == []


@pytest.mark.skipif(not key_names(), reason="langue du client absente (data/client_retro)")
def test_dungeon_keys_from_the_client_language():
    assert key_names()[KEY] == "Clef du Donjon d'Incarnam"
    assert dungeon_key("Donjon d'Incarnam") == KEY
    assert dungeon_key("Donjon des Champs") == 8143  # nommée « Clef des Champs » : trouvée par sa description
    assert dungeon_key("Donjon des Bouftous") == 1568
