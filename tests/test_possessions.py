"""Possessions gardées d'une session à l'autre (brain/possessions.py, 30/10)."""

from bot.brain import possessions
from bot.connection.network import ClientFeed

# Comme le 30/10 : un champ « 0 » suit la liste des objets. 36 clés Bouftou (620), 28 d'Incarnam (2161).
ASK = "ASK|1610317|Fourboun|43|10|0|100|0|0|0|aa~620~24~~;ab~2161~1c~~;|0"


def test_the_bag_is_saved_then_restored_until_the_next_login(tmp_path):
    feed = ClientFeed("Fourboun")
    feed.feed(ASK)  # sac de la connexion écrit dès qu'il est entendu (31/10)
    assert feed.held(1568) == 36
    assert (possessions.FOLDER / "Fourboun.json").exists()
    assert not possessions.save("Fourboun", feed, tmp_path)  # rien de changé depuis : pas réécrit
    feed.feed("OQ170|30")  # 6 clés données (échange)
    assert possessions.save("Fourboun", feed, tmp_path)
    later = ClientFeed("Fourboun")  # session suivante, connexion pas entendue
    assert later.held(1568) is None
    assert possessions.restore("Fourboun", later, tmp_path)
    assert later.held(1568) == 30 and later.held(8545) == 28
    later.feed(ASK)  # la connexion remplace ce qui a été repris
    assert later.held(1568) == 36
    assert not possessions.restore("Fourboun", later, tmp_path)


def test_removal_of_an_object_unknown_to_the_kept_bag_is_said(caplog):
    # 30/10 : clé d'Elseiso donnée au PNJ, restée dans ses possessions : l'écart est dit au journal.
    import logging

    from bot.brain.inventory import Inventory

    inventory = Inventory()
    inventory.feed("OR123")  # sac pas encore connu : rien à dire
    assert not caplog.records
    inventory.known = True
    with caplog.at_level(logging.INFO, logger="bot.brain.inventory"):
        assert not inventory.feed("OR123")
        assert not inventory.feed("OQ456|3")
    assert len([r for r in caplog.records if "absent du sac connu" in r.getMessage()]) == 2


def test_login_bag_is_written_as_soon_as_heard(tmp_path, monkeypatch):
    # 31/10 : objets renumérotés à chaque connexion ; un lancement repris d'un fichier plus ancien perdait le butin
    # ajouté aux piles. Le sac de la connexion est écrit dès qu'il est entendu, au nom porté par le message.
    import json

    from bot.brain import possessions
    from bot.connection.network import ClientFeed

    monkeypatch.setattr(possessions, "FOLDER", tmp_path)
    net = ClientFeed("")  # fenêtre pas encore nommée pendant la connexion
    net.feed("ASK|1610317|Fourboun|10|0|0|0|0|0|0|aa~620~24~~;ab~2161~3~~|0")
    record = json.loads((tmp_path / "Fourboun.json").read_text(encoding="utf-8"))
    assert sorted(t for _, t, _, _ in record["objets"]) == [0x620, 0x2161]


def test_pods_are_kept_with_the_bag(tmp_path):
    # 31/10 : « Ow » seulement à la connexion et quand le sac change : un lancement repris ne voyait pas une surcharge.
    feed = ClientFeed("Pesopepe")
    feed.feed(ASK)
    feed.feed("Ow1452|0|1225|0")
    assert possessions.save("Pesopepe", feed, tmp_path)
    later = ClientFeed("Pesopepe")
    assert possessions.restore("Pesopepe", later, tmp_path)
    assert later.pods == (1452, 1225)
