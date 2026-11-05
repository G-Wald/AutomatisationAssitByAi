"""Mode banque « principal » (team/trade.py, 30/10) : lots qui tiennent dans les pods du principal, secours au coffre
du suiveur."""

from types import SimpleNamespace

from bot.connection.network import ClientFeed
from bot.team.bank import BankVisit
from bot.team.loop import bank_visit
from bot.team.trade import CentralBank, free_pods, offer_weight, units_that_fit

def test_offer_weight_comes_from_the_weight_of_each_object():
    # 30/10 : « EmKO+291805782|10|289| », 10 Blés (289) de 2 pods chacun dans l'offre reçue par le principal.
    assert offer_weight({291805782: (289, 10), 7: (303, 3)}, {289: 2, 303: 5}) == 35


def test_free_pods_keep_a_reserve_and_are_unknown_without_the_server():
    feed = ClientFeed("Fourboun")
    assert free_pods(feed) is None
    feed.feed("Ow717|0|1800|0")
    assert free_pods(feed) == 1800 - 717 - 36  # 2 % de réserve
    feed.feed("Ow1790|0|1800|0")
    assert free_pods(feed) == 0


def test_only_what_fits_is_offered():
    assert units_that_fit(35, 5) == 7 and units_that_fit(35, 5, wanted=3) == 3 and units_that_fit(4, 5) == 0
    assert units_that_fit(10, 0, wanted=12) == 12  # objet sans poids connu : la pile entière


def test_the_bank_mode_setting_picks_the_visit():
    members = [SimpleNamespace(name="Fourboun", game=None), SimpleNamespace(name="Eldieso", game=None)]
    assert isinstance(bank_visit(members, "e", "principal"), CentralBank)
    assert type(bank_visit(members, "e", "chacun")) is BankVisit


def test_a_follower_goes_to_its_own_chest_when_the_leader_pods_are_unknown():
    leader = SimpleNamespace(name="Fourboun", game=SimpleNamespace(net=ClientFeed("Fourboun")))
    follower = SimpleNamespace(name="Eldieso", game=SimpleNamespace(net=ClientFeed("Eldieso")))
    bank = CentralBank([leader, follower])
    deposited = []
    bank.visit.deposit = lambda member: deposited.append(member.name) or True
    assert bank.centralise(follower) and deposited == ["Eldieso"]


def test_the_leader_deposits_only_when_nearly_full(monkeypatch):
    # 30/10 : 11 lots déposés un à un, 718 kamas l'accès au coffre de Fourboun : un dépôt quand il est presque plein.
    import bot.team.trade as trade

    leader = SimpleNamespace(name="Fourboun", game=SimpleNamespace(net=ClientFeed("Fourboun")))
    leader.game.net.feed("Ow400|0|1800|0")
    followers = [SimpleNamespace(name=n, game=SimpleNamespace(net=ClientFeed(n))) for n in ("Eldieso", "Walitcoin")]
    lots = {"Eldieso": [50], "Walitcoin": [1000, 300]}

    class FakeTrader:
        def __init__(self, giver, taker, portraits, team=()):
            self.giver = giver

        def open(self):
            return True

        def offer(self, free):
            return min(lots[self.giver.name].pop(0), free) if lots[self.giver.name] else 0

        def conclude(self, free):
            used, most = leader.game.net.pods
            leader.game.net.feed(f"Ow{used + lots_weight.pop(0)}|0|{most}|0")
            return True

        def cancel(self):
            pass

    lots_weight = [50, 1000, 300]
    monkeypatch.setattr(trade, "Trader", FakeTrader)
    bank = CentralBank([leader, *followers])
    deposits = []

    def deposit(member):
        deposits.append(member.name)
        if member is leader:
            leader.game.net.feed(f"Ow400|0|1800|0")
        return True

    bank.visit.deposit = deposit
    assert bank.centralise(followers[0]) and bank.centralise(followers[1])
    # 400 + 50 + 1000 + 300 = 1750 / 1800 : plus assez de place, un seul dépôt (avant de demander un lot de plus)
    assert deposits == ["Fourboun"] and not bank.carried


def test_the_taker_is_recognised_by_the_server_not_by_its_name(monkeypatch):
    # 30/10, utilisateur : cliquer chaque miniature puis « Echange », l'écoute réseau dit qui a reçu la demande.
    import bot.team.trade as trade

    team = [SimpleNamespace(name=n, game=SimpleNamespace(net=ClientFeed(n), sleep=lambda s: None))
            for n in ("Fourboun", "Eldieso", "Pesopepe")]
    giver, eldieso, pesopepe = team
    # miniature 0 : la sienne (pas d'« Echange ») ; 1 : Eldieso ; 2 : Pesopepe
    who_by_index = {1: eldieso, 2: pesopepe}
    asked, refused = [], []

    def ask(self, index):
        asked.append(index)
        if index not in who_by_index:
            return False
        who_by_index[index].game.net.feed("ERK1|2|1")
        return True

    monkeypatch.setattr(trade.Trader, "_group_open", lambda self, game: True)
    monkeypatch.setattr(trade.Trader, "_ask", ask)
    monkeypatch.setattr(trade.Trader, "_accept_request", lambda self, since: True)
    monkeypatch.setattr(trade, "escape_menu", lambda game: refused.append(game))
    portraits = {}
    assert trade.Trader(giver, pesopepe, portraits, team).open()
    assert asked == [0, 1, 2] and refused == [eldieso.game]
    assert portraits == {("Fourboun", "Fourboun"): 0, ("Fourboun", "Eldieso"): 1, ("Fourboun", "Pesopepe"): 2}
    asked.clear()
    assert trade.Trader(giver, eldieso, portraits, team).open()
    assert asked == [1]  # miniature retenue : un seul clic


def test_the_taker_portrait_comes_from_the_initiative_told_by_the_server(monkeypatch):
    # 31/10 (observé : reconnexion des 8, miniatures essayées une à une chez Fourboun et Eldieso) : le cadre du groupe
    # range les miniatures par initiative décroissante, celle du personnage comprise ; le serveur la donne (PM).
    import bot.team.trade as trade

    team = [SimpleNamespace(name=n, game=SimpleNamespace(net=ClientFeed(n), sleep=lambda s: None))
            for n in ("Fourboun", "Eldieso", "Pesopepe")]
    giver, eldieso, pesopepe = team
    giver.game.net.feed("PCKFourboun")
    giver.game.net.feed("PM+1610317;Fourboun;100;0;0;0;1b0c,1fe2,1b0f,,;352,352;44;100;100;0|"
                        "1715951;Eldieso;90;15851533;0;15984907;30e,1fe2,96e,6af,;306,306;38;348;122;0")
    giver.game.net.feed("PM+1996393;Pesopepe;30;0;0;0;,,,,;336,336;30;189;139;0")
    assert [giver.game.net.frame_slot(n) for n in ("Eldieso", "Pesopepe", "Fourboun", "Inconnu")] == [0, 1, 2, None]
    who_by_index = {0: eldieso, 1: pesopepe}
    asked = []

    def ask(self, index):
        asked.append(index)
        if index not in who_by_index:
            return False
        who_by_index[index].game.net.feed("ERK1|2|1")
        return True

    monkeypatch.setattr(trade.Trader, "_group_open", lambda self, game: True)
    monkeypatch.setattr(trade.Trader, "_ask", ask)
    monkeypatch.setattr(trade.Trader, "_accept_request", lambda self, since: True)
    monkeypatch.setattr(trade, "escape_menu", lambda game: None)
    assert trade.Trader(giver, pesopepe, {}, team).open()
    assert asked == [1]  # du premier coup, sans demande refusée
    giver.game.net.feed("PM~1996393;Pesopepe;30;0;0;0;,,,,;100,336;30;400;139;0")  # blessé ou équipé : ordre changé
    giver.game.net.feed("PM~1715951;Eldieso;90;0;0;0;,,,,;306,306;38;348;122;0")
    assert giver.game.net.frame_slot("Pesopepe") == 0
    giver.game.net.feed("PM+1;Autre;1;0;0;0;,,,,;1,1;1;348;1;0")  # même initiative qu'Eldieso : ordre des égalités inconnu
    assert giver.game.net.frame_slot("Eldieso") is None
    giver.game.net.feed("PV")
    assert giver.game.net.frame_slot("Pesopepe") is None


def test_no_exchange_is_asked_between_two_maps(monkeypatch):
    # 31/10 (test rapide) : Eldieso en 1,-32, Fourboun en 2,-34 ; toutes les miniatures cliquées pour rien.
    import bot.team.trade as trade

    giver = SimpleNamespace(name="Eldieso", game=SimpleNamespace(net=ClientFeed("Eldieso"), sleep=lambda s: None))
    taker = SimpleNamespace(name="Fourboun", game=SimpleNamespace(net=ClientFeed("Fourboun"), sleep=lambda s: None))
    giver.game.net.feed("GDM|1858|0706131721|aa")
    taker.game.net.feed("GDM|1856|0706131721|aa")
    clicked = []
    monkeypatch.setattr(trade.Trader, "_group_open", lambda self, game: clicked.append(game) or True)
    assert not trade.Trader(giver, taker, {}, [giver, taker]).open()
    assert not clicked
    taker.game.net.feed("GDM|1858|0706131721|aa")  # même carte : l'échange est tenté
    monkeypatch.setattr(trade.Trader, "_ask", lambda self, index: False)
    trade.Trader(giver, taker, {}, [giver, taker]).open()
    assert clicked


def test_resources_left_come_from_the_server_bag():
    # 31/10, utilisateur : constater au réseau. Pesopepe : 1re case pâle prise pour vide, « plus rien à donner » alors
    # que son sac (d'après le serveur) avait 1400 pods de ressources. Seul le serveur dit s'il en reste.
    from bot.team.trade import Trader

    giver_net = ClientFeed("Pesopepe")
    giver_net.feed("ASK|1|Pesopepe|10|0|0|0|0|0|0|aa~130~86~~;ab~970~1~1~|0")  # 134 Cuirs de Bouftou ; un marteau équipé
    giver = SimpleNamespace(name="Pesopepe", game=SimpleNamespace(net=giver_net))
    taker = SimpleNamespace(name="Walitcoin", game=SimpleNamespace(net=ClientFeed("Walitcoin")))
    trader = Trader(giver, taker, {})
    assert trader._resources_left()  # des cuirs
    giver_net.feed("ECK1")
    giver_net.feed("EMKO+170|134")  # tous offerts
    assert not trader._resources_left()  # le marteau équipé n'est pas une ressource


def test_exchange_line_of_the_last_miniature_below_the_map_is_found(monkeypatch):
    # 05/11 (capture d'Eldieso) : le menu de la 8e miniature (Elseiso) descend sous la carte, « Echange » à y=848 ;
    # la zone lue coupée au bas de la carte ne l'avait pas, l'échange n'était jamais demandé.
    from types import SimpleNamespace

    from bot.team import trade
    from conftest import load_frame

    frame = load_frame("menu_miniature_8_sous_la_carte")
    clicked = []
    game = SimpleNamespace(look=lambda park=False: frame, click=lambda point, spread=None: clicked.append(point),
                           sleep=lambda s: None)
    trader = trade.Trader.__new__(trade.Trader)
    trader.giver = SimpleNamespace(game=game)
    monkeypatch.setattr(trade, "close_portrait_menu", lambda g: True)
    assert trader._ask(7)
    assert 1650 <= clicked[-1][0] <= 1780 and 835 <= clicked[-1][1] <= 865  # la ligne « Echange »
