"""Récolte (bot/brain/harvest.py) : ressources d'après la carte et le serveur (27/10, Elseiso en -1,-16)."""

import time

import pytest

from bot.brain.harvest import Harvester, Resource, harvest_table, load_keys, resources_on_map
from bot.connection.network import ClientFeed

KEY_7334 = load_keys().get(7334)


def test_server_messages_of_a_harvest():
    # 27/10, Elseiso coupe le Frêne de la case 206 : récolte de 9,7 s, case récoltée, 3 Bois de Frêne, métier niveau 23.
    feed = ClientFeed("Elseiso")
    feed.feed("GDM|7334|0905131019|cle")
    feed.feed("GDF|438;4;0|207;4;0")
    now = time.monotonic()
    for line in ("GA1;501;1439688;206,9700", "GDF|206;2;0", "GDF|206;3;0", "IQ1439688|3", "JX|2;23;10875;11548;11903"):
        feed.feed(line)
    assert feed.map_key == "cle" and feed.objects == {438: 4, 207: 4, 206: 3}
    assert feed.harvests[206][1] == 9700 and feed.harvests[206][0] >= now
    assert feed.harvested == 3 and feed.jobs == {2: 23}
    feed.feed("GDM|7335|0905131019|autre")  # autre carte : états oubliés
    assert feed.objects == {} and feed.harvests == {}


def test_only_standing_resources_are_targets():
    class Game:
        net = ClientFeed("Elseiso")

    Game.net.feed("GDF|438;4;0|207;4;0|206;3;0")
    targets = [Resource(c, "Frêne", "Bûcheron", "Couper", 1) for c in (168, 206, 207, 438)]
    harvester = Harvester(Game(), lambda r: True)
    harvester.skipped[168] = time.monotonic() + 60
    assert harvester.available(targets) == []
    harvester.skipped.clear()
    assert [r.cell for r in harvester.available(targets)] == [168]


@pytest.mark.skipif(KEY_7334 is None or not harvest_table(), reason="clé ou langue du client absente (data/)")
def test_trees_of_map_minus_1_minus_16_from_its_map_data():
    found = {(r.cell, r.name) for r in resources_on_map(7334, KEY_7334)}
    assert found == {(75, "Noyer"), (168, "Frêne"), (206, "Frêne"), (207, "Châtaignier"), (321, "Frêne"),
                     (366, "Frêne"), (438, "Châtaignier")}


def test_tool_put_in_the_weapon_slot_is_seen_from_the_server():
    # 27/10, Enu-six : « OM<uid>|1 » (faux dans l'emplacement d'arme), « OM<uid>| » (retour au sac), « OAKO » à la place 1
    # (marteau sorti d'une pile de 2).
    feed = ClientFeed("Enu-six-[Gal]")
    feed.feed("OM49185363|", now=10.0)
    assert feed.weapon_at == 0.0
    feed.feed("OM49185363|1", now=11.0)
    assert feed.weapon_at == 11.0
    feed.feed("OAKO2fb92c1~970~1~5~63#4#8#1d5+3;", now=12.0)  # bottes : pas l'arme
    assert feed.weapon_at == 11.0
    feed.feed("OAKO2fb92c1~970~1~1~63#4#8#1d5+3,61#4#8#1d5+3,b6#1##0d0+1;", now=13.0)
    assert feed.weapon_at == 13.0
    assert feed.weapon_template() == 0x970  # 31/10 : le modèle de l'objet équipé, d'après le serveur


def test_equipped_item_checked_by_its_template(caplog):
    # 31/10, utilisateur : l'objet se vérifie par son modèle (numéro fixe du jeu). « OM<uid>|1 » donne l'exemplaire
    # (uid décimal), le sac de la session son modèle, la langue du client son type.
    from bot.brain.equipment import _right_item

    feed = ClientFeed("Enu-six-[Gal]")
    feed.feed("ASK|1|Enu-six-[Gal]|23|0|0|0|0|0|0|" + format(49185363, "x") + "~241~1~~;" + "2c~753~1~~|0")
    feed.feed("OM49185363|1")
    assert feed.weapon_template() == 0x241  # 577 : Faux du Paysan
    assert _right_item(feed.weapon_template(), "Faux", None)
    assert _right_item(577, "Faux", 577)
    assert not _right_item(1875, "Faux", None)  # Faux du Maître Corbac : objet de quête, pas une faux
    assert not _right_item(577, "Faux", 765)  # pas le modèle demandé
    assert "mauvais objet" in caplog.text
    assert _right_item(None, "Faux", None)  # modèle inconnu (sac pas connu) : confirmé sans preuve, dit


def test_a_real_double_click_is_sent_in_the_background(monkeypatch):
    import win32con

    from bot.actions.background import BackgroundInput
    from bot.actions.input import Human

    posted = []
    inp = BackgroundInput.__new__(BackgroundInput)
    inp.enabled, inp._cursor, inp._last_move = True, None, None
    inp.human = Human(reaction=(0, 0), hesitation_chance=0, move_time=(0, 0))
    inp.control = type("C", (), {"check": lambda self: None, "sleep": lambda self, s: None})()
    monkeypatch.setattr(inp, "_inside", lambda point: True)
    monkeypatch.setattr(inp, "_client", lambda point: 1)
    monkeypatch.setattr(inp, "_post", lambda message, wparam, lparam: posted.append(message))
    assert inp.double_click((1641, 305))
    assert posted == [win32con.WM_MOUSEMOVE, win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP,
                      win32con.WM_LBUTTONDBLCLK, win32con.WM_LBUTTONUP]


def test_search_text_of_an_item_name():
    # 31/10 : l'apostrophe ne passe pas en arrière-plan ; le nom après elle suffit à la recherche du sac.
    from bot.brain.equipment import search_text

    assert search_text("Faux de l'apprenti paysan") == "apprenti paysan"
    assert search_text("Marteau du Bouftou") == "Marteau du Bouftou"


def test_harvest_stops_when_a_fight_starts():
    # 01/11, mode récolte : une agression pendant la récolte, plus aucun clic sur la carte.
    class Game:
        net = ClientFeed("Elseiso")

    Game.net.feed("GJK2|0|1|0|30000|4")
    harvester = Harvester(Game(), lambda r: True)
    harvester.targets = lambda map_id=None: [Resource(168, "Frêne", "Bûcheron", "Couper", 1)]
    harvester.harvest = lambda resource: pytest.fail("pas de récolte en combat")
    assert harvester.run(7334) == 0


def test_harvest_mode_harvests_each_map_once_then_stops_with_full_pods(monkeypatch):
    from bot.brain.loop import Bot
    from bot.config import load_settings
    from tests.test_brain import FakeGame
    from tests.conftest import load_frame

    game = FakeGame(load_frame("hors_combat"))
    game.net = ClientFeed("Elseiso")
    game.net.map_id = 7334
    bot = Bot(game, load_settings(), engage=False, harvest=("Bûcheron", 23))
    runs = []
    monkeypatch.setattr(bot.harvester, "run", lambda map_id: runs.append(map_id) or 4)
    assert bot._harvest_here() and not bot._harvest_here()  # une fois par passage : la carte suivante ensuite
    game.net.map_id, game.net.pods = 7335, (1700, 1800)
    assert bot._harvest_here()
    assert runs == [7334] and bot.harvested == 4 and "pods" in bot._stop_reason  # pleins : plus de récolte
    assert bot.harvester.wanted(Resource(1, "Noyer", "Bûcheron", "Couper", 20))
    assert not bot.harvester.wanted(Resource(1, "Chêne", "Bûcheron", "Couper", 30))
    assert not bot.harvester.wanted(Resource(1, "Blé", "Paysan", "Faucher", 1))


def test_harvest_option_is_a_job_and_a_level():
    import argparse

    from bot.__main__ import _harvest

    assert _harvest("Bûcheron:23") == ("Bûcheron", 23)
    with pytest.raises(argparse.ArgumentTypeError):
        _harvest("Bûcheron")


def test_full_pods_send_the_harvester_to_the_bank_and_back(monkeypatch):
    # 01/11 : Elseiso plein après 7 arbres ; banque d'Astrub (même passage au coffre que l'équipe), puis la zone.
    from types import SimpleNamespace

    from bot.brain.loop import Bot
    from bot.config import load_settings
    from bot.team import bank
    from tests.test_brain import FakeGame
    from tests.conftest import load_frame

    game = FakeGame(load_frame("hors_combat"))
    game.net = ClientFeed("Elseiso")
    game.net.map_id, game.net.pods = 7334, (1063, 1120)
    bot = Bot(game, load_settings(), engage=False, harvest=("Bûcheron", 23))
    errands = []
    bot.roamer = SimpleNamespace(start_errand=lambda goal, why: errands.append(goal), end_errand=lambda: errands.append("fin"),
                                 errand=None, errand_reached=False)
    monkeypatch.setattr(bot.harvester, "run", lambda map_id: 7)
    assert bot._harvest_here() and errands == [bank.BANK_MAP] and not bot._stop_reason
    visits = []
    monkeypatch.setattr(bank.BankVisit, "run", lambda self: visits.append([m.name for m in self.members]) or ["Elseiso"])
    bot._bank()
    assert visits == [["Elseiso"]] and errands[-1] == "fin" and bot._harvested_map is None


def test_another_players_harvest_is_not_mine():
    feed = ClientFeed("Elseiso")
    feed.my_id = 1439688
    feed.feed("GDM|7334|0905131019|cle")
    feed.feed("GA1;501;999999;446,9700")  # un autre joueur coupe le Frêne de la case 446
    assert feed.harvests == {}
    feed.feed("GA1;501;1439688;206,9700")
    assert list(feed.harvests) == [206]


def test_no_harvest_with_nearly_full_pods():
    class Game:
        net = ClientFeed("Elseiso")

    Game.net.pods = (1063, 1120)
    harvester = Harvester(Game(), lambda r: True)
    harvester.targets = lambda map_id=None: [Resource(168, "Frêne", "Bûcheron", "Couper", 1)]
    harvester.harvest = lambda resource: pytest.fail("pas de récolte, pods presque pleins")
    assert harvester.full() and harvester.run(7334) == 0


def test_harvest_announced_with_action_zero():
    # 01/11 (journal 154855, Elseiso) : « GA0;501;1439688;197,9600 », pas reconnu, Frêne cru pas commencé.
    feed = ClientFeed("Elseiso")
    feed.feed("GDM|7334|0905131019|cle")
    feed.feed("GA0;501;1439688;197,9600")
    assert feed.harvests[197][1] == 9600
