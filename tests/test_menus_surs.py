"""Imprévus après un clic (31/10, revue « écran vers réseau ») : ce qui s'ouvre à la place de ce qu'on attendait est
reconnu et refermé sans Échap (brain/popups.close_any_menu), les preuves viennent du serveur, et chaque abandon est dit
à l'utilisateur (bot/alerts.tell_user). Jeux factices : aucun écran réel."""

import logging
import time
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np

import bot.brain.equipment as equipment
import bot.brain.popups as popups
import bot.brain.zaap as zaap
import bot.team.bank as bank
import bot.team.trade as trade
from bot.brain.arena import map_cell
from bot.brain.harvest import Harvester, Resource
from bot.connection.network import ClientFeed
from bot.perception.frame import Frame
from bot.perception.layout import Layout
from conftest import load_frame
from test_brain import FakeGame, clicks, presses

USER = "À l'utilisateur :"


def feed(name: str) -> ClientFeed:
    net = ClientFeed(name)
    net.feed("BN")  # écoute vivante
    return net


def number_of(cell: tuple[int, int]) -> int:
    return next(n for n in range(1000) if map_cell(n) == cell)


# Zaap

def zaap_game(net=None):
    game = FakeGame(load_frame("hors_combat"))
    game.net = net
    return game


def test_zaap_aims_at_the_arch_first_when_a_player_stands_on_its_cell(monkeypatch):
    # 31/10 : Astrasile debout sur le zaap d'Astrub, son menu au lieu de « Zaap / Utiliser » : d'après le serveur (GM),
    # l'arche est visée d'emblée, touche E tenue.
    net = feed("Fourboun")
    net.feed(f"GM|+{number_of((3, 3))};1;0;99;Fourboun;9")  # moi, ailleurs
    net.feed(f"GM|+{number_of((14, 20))};1;0;4242;Astrasile;9")
    game = zaap_game(net)
    monkeypatch.setattr(zaap, "ocr_available", lambda: True)
    monkeypatch.setattr(zaap, "find_use_entry", lambda frame, around: None)
    monkeypatch.setattr(zaap, "menu_open", lambda frame: None)
    assert zaap.player_on(game, (14, 20)) and not zaap.player_on(game, (3, 3))  # moi : pas un autre joueur
    assert not zaap.use_zaap(game, (14, 20), "Le Berceau", (1, -32), hide_key="e")
    frame = load_frame("hors_combat")
    x, y = frame.grid.center((14, 20))
    first = clicks(game)[0]
    assert first == (round(x), round(y - zaap.ZAAP_UP * frame.grid.half_h))  # l'arche
    assert ("hold", "e") in game.actions and "escape" not in presses(game)


def test_zaap_closes_what_opened_instead_without_escape_and_tells_the_user(monkeypatch, caplog):
    game = zaap_game(feed("Eldieso"))
    monkeypatch.setattr(zaap, "ocr_available", lambda: True)
    monkeypatch.setattr(zaap, "find_use_entry", lambda frame, around: None)
    monkeypatch.setattr(zaap, "menu_open", lambda frame: "menu")
    closed = []
    monkeypatch.setattr(zaap, "close_any_menu", lambda game, why="": closed.append(why) or True)
    assert not zaap.use_zaap(game, (14, 20), "Le Berceau", (1, -32))
    assert len(clicks(game)) == 3 and len(closed) == 3  # trois endroits du zaap, chaque menu refermé
    assert "escape" not in presses(game)
    assert f"{USER} Zaap vers Le Berceau abandonné pour Eldieso" in caplog.text


def test_zaap_stops_clicking_when_the_menu_does_not_close(monkeypatch, caplog):
    game = zaap_game()  # sans écoute : le nom vient des captures
    game.recorder.name = "Walitcoin"
    monkeypatch.setattr(zaap, "ocr_available", lambda: True)
    monkeypatch.setattr(zaap, "find_use_entry", lambda frame, around: None)
    monkeypatch.setattr(zaap, "menu_open", lambda frame: "Flash")
    monkeypatch.setattr(zaap, "close_any_menu", lambda game, why="": False)
    assert not zaap.use_zaap(game, (14, 20), "Astrub", (4, -19))
    assert len(clicks(game)) == 1 and "escape" not in presses(game)  # aucun clic par-dessus le menu resté ouvert
    assert "Walitcoin" in caplog.text and "Flash" in caplog.text and USER in caplog.text


# Échange

class TradeGame:
    """Fenêtre d'échange : la croix ferme l'échange (EV aux deux) si `closes`."""

    def __init__(self, net, nets, closes=True):
        self.net, self.nets, self.closes, self.clicked = net, nets, closes, []
        self.layout = Layout(1918, 1008)

    def look(self, park=False):
        return SimpleNamespace(layout=self.layout)

    def click(self, point, spread=None, button="left"):
        self.clicked.append(point)
        if self.closes:
            for net in self.nets:
                net.feed("EV")

    def sleep(self, seconds):
        pass


def trade_pair(closes=True):
    giver_net, taker_net = feed("Fourboun"), feed("Eldieso")
    for net in (giver_net, taker_net):
        net.feed("ECK1")
    giver = SimpleNamespace(name="Fourboun", game=TradeGame(giver_net, (giver_net, taker_net), closes))
    taker = SimpleNamespace(name="Eldieso", game=TradeGame(taker_net, (giver_net, taker_net), closes))
    return trade.Trader(giver, taker, {}), giver, taker


def test_cancel_is_checked_by_the_server(monkeypatch, caplog):
    monkeypatch.setattr(trade, "CANCEL_WAIT", 0.05)
    trader, giver, taker = trade_pair()
    assert trader.cancel()
    assert len(giver.game.clicked) == 1 and taker.game.clicked == []  # EV aux deux : un seul clic
    assert USER not in caplog.text


def test_cancel_clicks_again_then_tells_the_user(monkeypatch, caplog):
    monkeypatch.setattr(trade, "CANCEL_WAIT", 0.05)
    trader, giver, taker = trade_pair(closes=False)
    assert not trader.cancel()
    assert len(giver.game.clicked) == trade.CANCEL_TRIES == 2
    assert f"{USER} Échange Fourboun -> Eldieso : la fenêtre d'échange de Fourboun ne se ferme pas" in caplog.text


def test_portrait_menu_closed_without_escape(monkeypatch, caplog):
    # 31/10, revue : plus d'Échap en dernier recours ; menu resté ouvert : plus aucun clic sur les miniatures, et dit.
    escapes = []
    monkeypatch.setattr(trade, "escape_menu", lambda game: escapes.append(game))
    monkeypatch.setattr(trade, "close_any_menu", lambda game, why="": False)
    monkeypatch.setattr(trade, "_exchange_line", lambda frame, box: None)
    monkeypatch.setattr(trade, "best_text", lambda *args, **kwargs: (0.0, (0, 0)))
    monkeypatch.setattr(trade.Trader, "_group_open", lambda self, game: True)
    game = FakeGame(load_frame("hors_combat"))
    giver = SimpleNamespace(name="Fourboun", game=game)
    taker = SimpleNamespace(name="Eldieso", game=SimpleNamespace(net=feed("Eldieso")))
    trader = trade.Trader(giver, taker, {})
    trader._requests = lambda: {}
    assert not trader.open()
    assert len(clicks(game)) == 1 and escapes == [] and "escape" not in presses(game)
    assert trader.stuck and f"{USER} Échange Fourboun -> Eldieso abandonné" in caplog.text


# Banque

class BankGame:
    def __init__(self, net=None):
        self.net, self.clicks, self.held = net, [], []
        self.frame = load_frame("hors_combat")
        self.recorder = SimpleNamespace(save=lambda *a, **k: "capture.png")

    def look(self, park=False):
        return self.frame

    def click(self, point, spread=(0, 0), button="left"):
        self.clicks.append((point, tuple(self.held)))
        return True

    def sleep(self, seconds):
        pass

    @contextmanager
    def holding(self, key):
        self.held.append(key)
        yield
        self.held.remove(key)


def test_banker_clicked_with_players_hidden_and_a_player_menu_closed_first(monkeypatch):
    game = BankGame()
    member = SimpleNamespace(name="Fourboun", game=game)
    visit = bank.BankVisit([member], hide_key="e")
    monkeypatch.setattr(bank, "find_bankers", lambda frame: [(900, 300), (1100, 300)])
    seen = iter([None, "menu"])  # avant tout : rien ; après le 1er banquier : le menu d'un joueur
    monkeypatch.setattr(bank, "menu_open", lambda frame: next(seen, None))
    closed = []
    monkeypatch.setattr(bank, "close_any_menu", lambda game, why="": closed.append(len(game.clicks)) or True)
    parler = iter([None, ((1100, 350), 0.9)])
    monkeypatch.setattr(bank, "match_template", lambda *args, **kwargs: next(parler))
    monkeypatch.setattr(bank.BankVisit, "_text", lambda self, member, text, dark, timeout, bold=False: (1.0, (5, 5)))
    assert visit.open_chest(member)
    (first, held1), (second, held2) = game.clicks[:2]
    assert held1 == ("e",) and held2 == ("e",)  # les deux banquiers cliqués touche E tenue
    assert closed == [1]  # menu du joueur refermé juste après le 1er clic, avant le 2e banquier


class TalkGame(BankGame):
    """Le serveur n'ouvre le dialogue du banquier qu'au `opens`-ième clic sur « Parler » (07/11, Elseiso)."""

    def __init__(self, net, opens: int):
        super().__init__(net)
        self.opens, self.talks = opens, 0

    def click(self, point, spread=(0, 0), button="left"):
        if point == (1100, 350):  # « Parler »
            self.talks += 1
            if self.talks == self.opens:
                self.net.feed("DCK-2")
                self.net.feed("DQ318;290|259;329")
        return super().click(point, spread, button)


def banker_talk(monkeypatch, opens: int):
    monkeypatch.setattr(bank, "TALK_WAIT", 0.05)
    monkeypatch.setattr(bank, "NPC_STILL", 0.0)
    monkeypatch.setattr(bank, "find_bankers", lambda frame: [(900, 300)])
    monkeypatch.setattr(bank, "menu_open", lambda frame: None)
    monkeypatch.setattr(bank, "find_npc_dialog", lambda frame: None)
    monkeypatch.setattr(bank, "match_template", lambda *args, **kwargs: ((1100, 350), 0.9))
    monkeypatch.setattr(bank.BankVisit, "_text", lambda self, member, text, dark, timeout, bold=False: (1.0, (5, 5)))
    monkeypatch.setattr(bank, "log_chest_keys", lambda member: {})
    game = TalkGame(feed("Elseiso"), opens)
    member = SimpleNamespace(name="Elseiso", game=game)
    return bank.BankVisit([member], hide_key="e"), member, game


def test_banker_spoken_to_again_when_the_server_opens_no_dialog(monkeypatch, caplog):
    # 07/11 (Elseiso) : « Parler » cliqué quand le banquier repartait (« GA;1;-2;... ») : ni DCK ni question du serveur,
    # « Consulter son coffre personnel » cherché pour rien, Échap derrière (Menu principal). Rien n'est ouvert : on lui
    # reparle, sans Échap.
    caplog.set_level(logging.INFO)
    visit, member, game = banker_talk(monkeypatch, opens=2)
    assert visit.open_chest(member)
    assert game.talks == 2 and "je lui reparle (2/3)" in caplog.text


def test_banker_silent_three_times_is_a_failure(monkeypatch):
    visit, member, game = banker_talk(monkeypatch, opens=99)
    assert not visit.open_chest(member)
    assert game.talks == bank.TALK_TRIES
    assert "dialogue du banquier pas ouvert" in visit.reasons["Elseiso"]


def test_banker_clicked_once_his_steps_are_over(monkeypatch):
    # Les banquiers marchent (07/11) : un pas de PNJ annoncé par le serveur, le clic attend NPC_STILL s sans pas.
    monkeypatch.setattr(bank, "NPC_STILL", 0.3)
    net = feed("Elseiso")
    net.feed("GA;1;-2;aeahdY")
    moved = net.npc_moved_at
    assert moved > 0
    net.my_id = 1439688
    net.feed("GA0;1;1439688;afVdf9")  # mon personnage : pas un PNJ
    assert net.npc_moved_at == moved
    member = SimpleNamespace(name="Elseiso", game=BankGame(net))
    visit = bank.BankVisit([member])
    visit._npcs_still(member)
    assert time.monotonic() - moved >= bank.NPC_STILL


def test_nothing_open_for_the_server_means_no_escape(monkeypatch):
    # 07/11 : après un « Parler » sans réponse, Échap ouvrait le Menu principal (rien d'autre à fermer).
    escapes = []
    monkeypatch.setattr(bank, "escape_menu", lambda game: escapes.append(game))
    monkeypatch.setattr(bank, "menu_open", lambda frame: None)
    monkeypatch.setattr(bank, "find_npc_dialog", lambda frame: None)
    net = feed("Elseiso")
    game = BankGame(net)
    assert bank.close_unexpected(game, "Banque") and escapes == []
    net.feed("DCK-2")  # dialogue ouvert, pas refermé (pas de DV) : Échap
    assert bank.close_unexpected(game, "Banque") and len(escapes) == 1
    net.feed("DV")
    net.feed("ECK5")  # coffre ouvert
    assert bank.close_unexpected(game, "Banque") and len(escapes) == 2
    net.feed("EV")
    assert bank.close_unexpected(game, "Banque") and len(escapes) == 2


def test_transfer_is_proven_by_pods_or_by_objects_leaving_the_bag():
    net = feed("Fourboun")
    net.feed("ASK|1|Fourboun|10|0|0|0|0|0|0|aa~121~a~~;ab~970~1~1~|0")  # 10 Blés, un marteau équipé
    net.feed("Ow717|0|1800|0")
    before = bank.bag_quantities(net)
    assert before == {170: 10} and bank.transfer_proof(net, 717, before) is None
    net.feed("Ow697|0|1800|0")
    assert bank.transfer_proof(net, 717, before) == "pods 717 -> 697"
    net.feed("OR170")
    assert bank.transfer_proof(net, None, before) == "1 pile(s) sortie(s) du sac"


def test_unproven_transfer_is_said_and_not_paid_twice(monkeypatch, caplog):
    monkeypatch.setattr(bank, "TRANSFER_WAIT", 0.1)
    monkeypatch.setattr(bank, "menu_open", lambda frame: None)
    escapes = []
    monkeypatch.setattr(bank, "escape_menu", lambda game: escapes.append(game))
    monkeypatch.setattr(bank, "match_template", lambda *args, **kwargs: ((1700, 400), 0.9))
    net = feed("Pesopepe")
    net.feed("ASK|1|Pesopepe|10|0|0|0|0|0|0|aa~121~a~~|0")
    net.feed("Ow717|0|1800|0")
    member = SimpleNamespace(name="Pesopepe", game=BankGame(net))
    visit = bank.BankVisit([member])
    opened = []
    visit.open_chest = lambda m, frame=None: opened.append(m.name) or True
    assert not visit.deposit(member)
    assert opened == ["Pesopepe"]  # pas de second passage au coffre (accès payé)
    visit.tell_failures([member], {"Pesopepe"}, {"Pesopepe"})
    assert f"{USER} Banque : Pesopepe n'a pas vidé ses ressources au coffre : « Transférer tous les objets » " \
           "cliqué" in caplog.text


# Récolte

def test_harvest_menu_not_seen_closed_without_escape_and_stuck_tells_the_user(monkeypatch, caplog):
    monkeypatch.setattr("bot.brain.harvest.MENU_WAIT", 0.0)
    monkeypatch.setattr(bank, "best_text", lambda *args, **kwargs: (0.0, (0, 0)))
    monkeypatch.setattr(popups, "close_context_menu", lambda game, frame: False)
    monkeypatch.setattr(popups, "close_any_menu", lambda game, why="": False)
    game = FakeGame(load_frame("hors_combat"))
    game.net = feed("Elseiso")
    harvester = Harvester(game, lambda r: True, hide_key="")
    blé = Resource(206, "Blé", "Paysan", "Faucher", 1)
    harvester.targets = lambda map_id=None: [blé, Resource(207, "Blé", "Paysan", "Faucher", 1)]
    assert harvester.run() == 0
    assert len(clicks(game)) == 1 and "escape" not in presses(game)  # arrêtée : pas de clic sur le 2e Blé
    assert harvester.stuck and f"{USER} Récolte arrêtée pour Elseiso" in caplog.text


# Équipement : recherche du sac

def field_frame(typed: bool) -> Frame:
    image = np.full((1008, 1918, 3), (160, 190, 200), np.uint8)  # fond beige
    image[220:241, 1600:1831] = 255  # champ de recherche blanc
    if typed:
        image[225:236, 1606:1640] = 30  # « app » écrit dans le champ
    return Frame.of(image)


class SearchGame:
    """Inventaire factice : les lettres tapées s'affichent dans le champ seulement s'il a le focus."""

    def __init__(self, focused: bool):
        self.focused, self.typed, self.pressed, self.shown = focused, [], [], False

    def look(self, park=False):
        return field_frame(self.shown)

    def click(self, point, spread=None, button="left"):
        pass

    def press(self, key):
        self.pressed.append(key)

    def type_text(self, text):
        self.typed.append(text)
        self.shown = self.shown or self.focused
        return True

    def sleep(self, seconds):
        pass


def test_search_text_typed_only_after_the_first_letters_are_seen_in_the_field(monkeypatch, caplog):
    monkeypatch.setattr(equipment, "_search_open", lambda game, layout, field=None: True)
    layout = Layout(1918, 1008)
    game = SearchGame(focused=True)
    assert equipment.search_bag(game, layout, "Faux de l'apprenti paysan")
    assert game.typed == ["app", "renti paysan"]
    lost = SearchGame(focused=False)  # lettres parties ailleurs (raccourcis du jeu) : arrêt aux premières
    assert not equipment.search_bag(lost, layout, "Faux de l'apprenti paysan")
    assert lost.typed == ["app"] and f"{USER} Recherche du sac" in caplog.text


def test_nothing_typed_when_the_search_field_does_not_open(monkeypatch):
    monkeypatch.setattr(equipment, "_search_open", lambda game, layout, field=None: False)
    game = SearchGame(focused=True)
    assert not equipment.search_bag(game, Layout(1918, 1008), "Marteau du Bouftou")
    assert game.typed == [] and game.pressed == []
    equipment.close_bag_search(game, Layout(1918, 1008))  # recherche pas ouverte : rien
    assert game.pressed == []


def test_field_change_ignores_the_blinking_caret():
    layout = Layout(1918, 1008)
    empty = equipment._field_patch(field_frame(False), layout, equipment.FILTER)
    caret = field_frame(False)
    caret.image[224:238, 1606] = 0  # curseur : 14 px
    assert not equipment._field_changed(empty, equipment._field_patch(caret, layout, equipment.FILTER), layout)
    typed = equipment._field_patch(field_frame(True), layout, equipment.FILTER)
    assert equipment._field_changed(empty, typed, layout)


# Équipement : le modèle voulu d'après le serveur, et rien de consommé

class EquipGame:
    def __init__(self, net, on_double=None):
        self.net, self.on_double, self.looked = net, on_double, 0

    def look(self, park=False):
        self.looked += 1
        return SimpleNamespace(layout=Layout(1918, 1008))

    def press(self, key):
        pass

    def wheel(self, point, notches, spread=None):
        return True

    def sleep(self, seconds):
        pass

    def double_click(self, point, spread=None):
        if self.on_double:
            self.on_double()


def test_nothing_is_equipped_when_the_model_is_not_in_the_bag():
    net = feed("Enu-six")
    net.feed("ASK|1|Enu-six|23|0|0|0|0|0|0|aa~241~1~1~;ab~2c4~3~~|0")  # faux 577 déjà portée, 3 potions 708
    game = EquipGame(net)
    assert not equipment.equip_item(game, 765)  # pas dans le sac : l'inventaire n'est même pas ouvert
    assert game.looked == 0


def test_a_consumed_object_after_the_double_click_is_told(monkeypatch, caplog):
    monkeypatch.setattr(equipment, "CONFIRM_WAIT", 0.1)
    monkeypatch.setattr(equipment, "search_bag", lambda *args: True)
    monkeypatch.setattr(equipment, "close_bag_search", lambda *args: None)
    monkeypatch.setattr(bank, "best_text", lambda *args, **kwargs: (1.0, (0, 0)))
    monkeypatch.setattr("bot.brain.travel.escape_menu", lambda game: None)
    net = feed("Enu-six")
    net.feed("ASK|1|Enu-six|23|0|0|0|0|0|0|aa~241~1~~;ab~2c4~3~~|0")  # faux 577 dans le sac, 3 potions 708
    game = EquipGame(net, on_double=lambda: net.feed("OQ171|2"))  # une potion bue à la place
    assert not equipment.equip_item(game, 577)
    assert USER in caplog.text and "modèle 708) x1" in caplog.text


def test_moving_or_merging_stacks_is_not_a_consumption(caplog):
    net = feed("Enu-six")
    net.feed("ASK|1|Enu-six|23|0|0|0|0|0|0|aa~241~1~~;ab~970~1~1~;ac~970~1~~|0")
    before = equipment.held_by_template(net)
    net.feed("OM170|1")  # la faux équipée
    net.feed("OR171")  # l'ancien marteau fondu dans la pile de l'autre
    net.feed("OQ172|2")
    assert equipment._tell_consumed(None, net, before, 577) == [] and USER not in caplog.text


def test_a_long_wait_for_the_foreground_is_told_to_the_user(monkeypatch, caplog):
    # 31/10 : connexion arrêtée 10 min sans rien dire, le launcher attendait d'être au premier plan.
    import logging
    from types import SimpleNamespace

    import bot.actions.input as game_input

    clock = [0.0]
    monkeypatch.setattr(game_input.time, "monotonic", lambda: clock[0])
    answers = iter([False] * 200 + [True])
    window = SimpleNamespace(title="Dofus Retro v1.49.9", is_foreground=lambda: next(answers))

    def sleep(seconds):
        clock[0] += seconds

    control = SimpleNamespace(sleep=sleep)
    with caplog.at_level(logging.WARNING):
        game_input.GameInput(window, control).wait_foreground()
    told = [r.getMessage() for r in caplog.records if "premier plan" in r.getMessage()]
    assert len(told) == 1 and "À l'utilisateur" in told[0]  # une seule fois, au bout de 30 s (60 s attendues ici)
