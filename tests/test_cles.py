"""Clés de donjon au début d'un farm (team/keys.py, 30/10) : comptage d'après le serveur, répartition par échanges,
passage aux coffres (mode sacs + banque), passages ramenés à ceux que les clés permettent. Sans le jeu : faux
personnages, faux échanges, faux trajets."""

from types import SimpleNamespace

import pytest

import bot.brain.inventory as inventory_module
from bot.connection.network import ClientFeed
from bot.gui.launch import Choices, build_args, default_choices, overrides, validate
from bot.team import bank, keys, trade
from bot.team.bank import BAG_DROP, chest_stacks, grid_point, quantity_points, take_from_chest
from bot.team.dungeon import DungeonFarm, DungeonSpec
from bot.team.keys import KeyManager, key_mode, kept_key
from bot.team.trade import OFFER_SLOT, Trader

KEY = 8545  # Clef du Donjon d'Incarnam (0x2161)
NAMES = ["Fourboun", "Eldieso", "Enu-six"]


class FakeNet:
    def __init__(self, held: int | None):
        self.count = held

    def alive(self) -> bool:
        return True

    def held(self, template: int) -> int | None:
        return self.count if template == KEY else 0


def team(*held):
    return [SimpleNamespace(name=name, game=SimpleNamespace(net=FakeNet(count))) for name, count in zip(NAMES, held)]


def manager(members, mode="sac", trip=None, chests=None, fail=()):
    """KeyManager dont les échanges déplacent les clés entre les faux inventaires (sauf ceux de `fail`)."""
    trades = []

    def fake_trade(giver, taker, template, count):
        trades.append((giver.name, taker.name, count))
        if (giver.name, taker.name) in fail:
            return False
        giver.game.net.count -= count
        taker.game.net.count += count
        return True

    keys_manager = KeyManager(members, {"donjon": {"cles": mode}}, trip=trip, trade=fake_trade, chests=chests)
    return keys_manager, trades


def test_the_key_mode_setting():
    assert key_mode({}) == "non" and key_mode({"donjon": {"cles": False}}) == "non"
    assert key_mode({"donjon": {"cles": "sac"}}) == "sac"
    assert key_mode({"donjon": {"cles": "Sac + banque"}}) == "sac_banque"
    assert key_mode({"donjon": {"cles": "sac_banque"}}) == "sac_banque"
    assert key_mode({"donjon": {"cles": "partout"}}) == "non"


def test_mode_non_does_nothing():
    members = team(3, 0, 0)
    keys_manager, trades = manager(members, mode="non")
    assert not keys_manager.enabled
    assert keys_manager.prepare("Donjon d'Incarnam", 1, KEY) is None and trades == []


def test_keys_are_shared_so_that_everyone_has_one():
    members = team(3, 0, 1)
    keys_manager, trades = manager(members)
    assert keys_manager.prepare("Donjon d'Incarnam", 1, KEY) == 1
    assert trades == [("Fourboun", "Eldieso", 1)]  # Enu-six en a déjà une : un seul échange
    assert [m.game.net.count for m in members] == [2, 1, 1]


def test_passes_are_capped_by_the_keys():
    members = team(5, 0, 2)  # 7 clés pour 3 : 2 passages
    keys_manager, trades = manager(members)
    assert keys_manager.prepare("Donjon d'Incarnam", 4, KEY) == 2
    assert trades == [("Fourboun", "Eldieso", 2)]
    assert [m.game.net.count for m in members] == [3, 2, 2]


def test_no_trade_when_there_is_not_one_key_each():
    members = team(2, 0, 0)
    keys_manager, trades = manager(members)
    assert keys_manager.prepare("Donjon d'Incarnam", 1, KEY) == 0 and trades == []


def test_an_unknown_inventory_keeps_the_old_behaviour():
    members = team(3, None, 0)
    keys_manager, trades = manager(members)
    assert keys_manager.prepare("Donjon d'Incarnam", 1, KEY) is None and trades == []


def test_a_failed_trade_is_counted_from_the_server():
    members = team(4, 0, 0)
    keys_manager, trades = manager(members, fail={("Fourboun", "Eldieso")})
    assert keys_manager.prepare("Donjon d'Incarnam", 1, KEY) == 0  # Eldieso n'a toujours pas de clé
    assert len(trades) == 2 and members[2].game.net.count == 1


class Trip:
    def __init__(self, go=True, back=True):
        self.calls, self._go, self._back = [], go, back

    def go(self):
        self.calls.append("go")
        return self._go

    def back(self):
        self.calls.append("back")
        return self._back


class Chests:
    """Coffres : clés rangées par personnage ; fetch les rend au sac, au plus `missing` en tout."""

    def __init__(self, stored):
        self.stored, self.calls = stored, []

    def fetch(self, short, template, missing):
        self.calls.append(([m.name for m in short], missing))
        taken = 0
        for member in short:
            n = min(self.stored.get(member.name, 0), missing - taken)
            member.game.net.count += n
            self.stored[member.name] = self.stored.get(member.name, 0) - n
            taken += n
        return taken


def test_bag_and_bank_fetches_from_the_chests_of_those_short_of_keys_then_shares():
    members = team(2, 0, 0)  # 2 passages demandés : 6 clés, il en manque 4
    trip, chests = Trip(), Chests({"Eldieso": 10, "Fourboun": 5})
    keys_manager, trades = manager(members, mode="sac_banque", trip=trip, chests=chests)
    assert keys_manager.prepare("Donjon d'Incarnam", 2, KEY) == 2
    assert chests.calls == [(["Eldieso", "Enu-six"], 4)]  # Fourboun en a 2 : il ne va pas au coffre
    assert trip.calls == ["go", "back"]
    assert trades == [("Eldieso", "Enu-six", 2)]
    assert [m.game.net.count for m in members] == [2, 2, 2]


def test_bag_and_bank_does_not_go_to_the_bank_when_the_bags_are_enough():
    members = team(3, 0, 0)
    trip, chests = Trip(), Chests({})
    keys_manager, trades = manager(members, mode="sac_banque", trip=trip, chests=chests)
    assert keys_manager.prepare("Donjon d'Incarnam", 1, KEY) == 1
    assert trip.calls == [] and chests.calls == []


def test_bag_and_bank_without_a_trip_works_with_the_bags():
    members = team(4, 0, 0)
    chests = Chests({"Eldieso": 10})
    keys_manager, trades = manager(members, mode="sac_banque", chests=chests)
    assert keys_manager.prepare("Donjon d'Incarnam", 2, KEY) == 1
    assert chests.calls == []


def test_no_farm_when_the_team_did_not_come_back_from_the_bank():
    members = team(0, 0, 0)
    keys_manager, trades = manager(members, mode="sac_banque", trip=Trip(back=False), chests=Chests({"Eldieso": 3}))
    assert keys_manager.prepare("Donjon d'Incarnam", 1, KEY) == 0 and trades == []


def test_the_kept_key_setting(monkeypatch):
    assert kept_key({}) is None and kept_key({"donjon": {"garder_cle": ""}}) is None
    assert kept_key({"donjon": {"garder_cle": 8545}}) == 8545
    assert kept_key({"donjon": {"garder_cle": "incarnam"}}) == KEY  # le donjon de config/donjons_entree.yaml
    assert kept_key({"donjon": {"garder_cle": "inconnu"}}) is None


# --- Le farm de donjon : passages ramenés à ceux que les clés permettent ---

SPEC = DungeonSpec("incarnam", "Donjon d'Incarnam", "incarnam", (6, 5), (16, 14), None, None, (20, 14), "Voici")


def farm(passes, runs=3, start=None):
    bot = SimpleNamespace(members=team(1, 1, 1), _settings={"donjon": {"cles": "sac"}})
    dungeon = DungeonFarm(bot, SPEC, runs)
    asked = []
    dungeon.keys.prepare = lambda name, wanted: asked.append((name, wanted)) or passes
    dungeon._close_windows = lambda: None
    dungeon.entry.enter = lambda *args: False  # pas d'entrée : le farm s'arrête aussitôt
    dungeon._fight_rooms = lambda first=1: False  # de même, repris aux salles
    dungeon.run(start)
    return dungeon, asked


def test_the_farm_is_capped_by_the_keys():
    dungeon, asked = farm(1)
    assert asked == [("Donjon d'Incarnam", 3)] and dungeon.runs == 1
    assert farm(5)[0].runs == 3 and farm(None)[0].runs == 3
    assert farm(0)[0].runs == 0


def test_keys_are_not_shared_when_the_team_is_already_in_the_rooms():
    dungeon, asked = farm(1, start="salles")
    assert asked == [] and dungeon.runs == 3


def test_keys_are_not_shared_when_the_mode_is_off():
    bot = SimpleNamespace(members=team(1, 1, 1), _settings={})
    assert not DungeonFarm(bot, SPEC, 2).keys.enabled


# --- Interface ---

def test_the_keys_choice_in_the_window():
    settings = {"donjon": {"cles": "sac_banque"}}
    assert default_choices(settings).keys == "sac_banque" and default_choices({}).keys == "non"
    choices = Choices(mode="donjon", donjon="incarnam", team=True, leader="Fourboun", keys="sac")
    assert ("donjon.cles", "sac") in overrides(choices, {})
    assert "donjon.cles=sac" in build_args(choices, {})
    assert ("donjon.cles", "sac") not in overrides(Choices(mode="carte", keys="sac"), {})  # farm de donjon seulement
    assert not any("Clés" in e for e in validate(choices, {}, dungeons={"incarnam": ("Donjon d'Incarnam", "incarnam")}))
    assert any("Clés" in e for e in validate(Choices(keys="tout"), {}, dungeons={}))


# --- Gestes vérifiés par le serveur (faux jeu : la fenêtre de quantité « répond » comme le serveur) ---

class FakeLayout:
    def point(self, x, y):
        return (round(x), round(y))

    def length(self, value):
        return value


class FakeGame:
    """Fenêtre du jeu : la coche de la fenêtre de quantité (à côté du point de lâcher `drop`) pose le nombre tapé ; un
    double-clic pose une unité (on_ok(None))."""

    def __init__(self, net, drop, on_ok):
        self.net, self.on_ok, self.typed = net, on_ok, None
        self.layout = FakeLayout()
        self.ok_point = quantity_points(self.layout, self.layout.point(*drop))[1]

    def look(self, park=False):
        return SimpleNamespace(layout=self.layout)

    def click(self, point, spread=None, button="left"):
        if abs(point[0] - self.ok_point[0]) < 1 and abs(point[1] - self.ok_point[1]) < 1:
            self.on_ok(self.typed)
            self.typed = None

    def double_click(self, point, spread=None):
        self.on_ok(None)

    def type_text(self, text):
        self.typed = int(text)

    def sleep(self, seconds):
        pass

    def press(self, key):
        pass


@pytest.fixture
def no_list(monkeypatch):
    """Liste des types toujours trouvée ; pas de glisser (l'écran n'existe pas)."""
    from bot.brain import equipment

    monkeypatch.setattr(bank, "choose_type", lambda *args: True)
    monkeypatch.setattr(equipment, "search_bag", lambda *args: True)  # recherche du sac (31/10) : pas d'écran ici
    monkeypatch.setattr(equipment, "close_bag_search", lambda *args: None)
    monkeypatch.setattr(bank, "slow_drag", lambda *args: True)
    monkeypatch.setattr(Trader, "_drag", lambda self, start, end: None)
    monkeypatch.setattr(inventory_module, "key_names", lambda: {KEY: "Clef du Donjon d'Incarnam"})


def trade_pair(model_seen):
    giver_net, taker_net = ClientFeed("Fourboun"), ClientFeed("Eldieso")
    giver_net.feed("ASK|1|Fourboun|10|0|0|0|aa~2161~3~~;")
    for net in (giver_net, taker_net):
        net.feed("ECK1")
    offered = {"n": 0}

    def on_ok(count):
        offered["n"] += count or 1
        giver_net.feed(f"EMKO+170|{offered['n']}")
        taker_net.feed(f"EmKO+170|{offered['n']}|{model_seen}|")

    giver = SimpleNamespace(name="Fourboun", game=FakeGame(giver_net, OFFER_SLOT, on_ok))
    taker = SimpleNamespace(name="Eldieso", game=SimpleNamespace(net=taker_net))
    return Trader(giver, taker, {}), taker_net


def test_offer_item_puts_one_key_checks_it_then_the_rest(no_list):
    trader, taker_net = trade_pair(KEY)
    assert trader.offer_item(KEY, 3)
    assert taker_net.trade()[0].theirs == {170: (KEY, 3)}


def test_offer_item_stops_on_the_wrong_object(no_list):
    trader, taker_net = trade_pair(289)  # du Blé posé au lieu de la clé
    assert not trader.offer_item(KEY, 2)
    assert taker_net.trade()[0].theirs == {170: (289, 1)}  # une seule unité : l'appelant annule


def test_offer_item_refuses_more_than_the_giver_holds(no_list):
    trader, _ = trade_pair(KEY)
    assert not trader.offer_item(KEY, 4)


def test_keys_are_taken_back_from_the_chest(no_list):
    net = ClientFeed("Fourboun")
    net.feed("ASK|1|Fourboun|10|0|0|0|aa~121~a~~;")  # 10 Blés, pas de clé
    net.feed("ECK5")
    net.feed("ELO11655e57~2161~5~~;O244eb62~6f1~7f~~")  # 5 clés (uid 291855959), 127 objets 1777
    assert chest_stacks(net, KEY) == [(0x11655e57, 5)]

    def on_ok(count):
        net.feed(f"EsKO+291855959|{5 - count}|8545|")
        net.feed(f"OAKOb0~2161~{count}~~;")

    member = SimpleNamespace(name="Fourboun", game=FakeGame(net, BAG_DROP, on_ok))
    assert take_from_chest(member, KEY, 3) == 3
    assert net.held(KEY) == 3 and chest_stacks(net, KEY) == [(0x11655e57, 2)]


def test_nothing_is_taken_when_the_server_says_nothing(no_list, monkeypatch):
    monkeypatch.setattr(bank, "close_unexpected", lambda game, why: True)  # menu puis coffre fermés (01/11)
    monkeypatch.setattr(bank, "CHEST_WAIT", 0.2)
    net = ClientFeed("Fourboun")
    net.feed("ASK|1|Fourboun|10|0|0|0|")
    net.feed("ECK5")
    net.feed("ELO11655e57~2161~5~~")
    member = SimpleNamespace(name="Fourboun", game=FakeGame(net, BAG_DROP, lambda count: None))
    assert take_from_chest(member, KEY, 3) == 0


def test_grid_cells_go_left_to_right_then_down():
    assert grid_point((100, 200), 0, 50, 5) == (100, 200)
    assert grid_point((100, 200), 6, 50, 5) == (150, 250)


def test_each_dungeon_key_comes_from_the_exact_table(tmp_path):
    # 31/10, utilisateur : chaque donjon associé à sa clé par le nom exact (config/donjons_cles.yaml), jamais par
    # ressemblance (« Donjon Bouftou » / « Clef du Donjon des Bouftous » pas reconnus : Fourboun entré seul).
    from bot.team.keys import key_of

    assert key_of("Donjon Bouftou") == 1568 and key_of("Donjon d'Incarnam") == KEY
    assert key_of("Donjon bouftou") is None  # nom pas exact : rien deviné
    assert key_of("Gladiatrool") is None  # donjon sans clé connue
    table = tmp_path / "cles.yaml"
    table.write_text('- donjon: "Donjon X"\n  cle: 42\n- donjon: "Donjon Y"\n  cle:\n', encoding="utf-8")
    assert key_of("Donjon X", table) == 42 and key_of("Donjon Y", table) is None


def test_every_dungeon_of_the_entry_file_has_its_key():
    # Chaque donjon décrit pour l'entrée (config/donjons_entree.yaml) a sa clé dans la table.
    from bot.team.dungeon import load_dungeons
    from bot.team.keys import key_of

    assert {key: key_of(spec.name) for key, spec in load_dungeons().items()} == {"incarnam": KEY, "bouftou": 1568, "champs": 8143}


def test_no_entry_without_shared_keys_but_a_keyring_lets_the_entry_decide():
    # 31/10, utilisateur : « si tout le groupe n'a pas de clé il ne faut pas continuer et faire rentrer solo ».
    from dataclasses import replace

    def run(spec, passes):
        bot = SimpleNamespace(members=team(1, 1, 1), _settings={"donjon": {"cles": "sac"}})
        dungeon = DungeonFarm(bot, spec, 2)
        entered = []
        dungeon.keys.prepare = lambda name, wanted: passes
        dungeon._close_windows = lambda: None
        dungeon.entry.enter = lambda *args: entered.append(1) or False
        dungeon.run()
        return dungeon.runs, entered

    assert run(SPEC, None) == (2, [])  # clés pas réparties (clé ou inventaire inconnus) : pas d'entrée
    assert run(SPEC, 0) == (0, [])  # pas une clé chacun, pas de trousseau : pas d'entrée
    keyring = replace(SPEC, keyring="Utiliser le trousseau de clefs", keyring_id=6602)
    assert run(keyring, 0) == (0, [])  # trousseau (une fois par semaine) : jamais un passage tenté à cause de lui


def test_missing_keys_are_told_to_the_user(caplog):
    # 31/10, utilisateur : « gérer un message pour l'utilisateur indiquant le manque de clés ».
    keys_manager, trades = manager(team(2, 0, 0))
    assert keys_manager.prepare("Donjon d'Incarnam", 1, KEY) == 0 and trades == []
    assert "À l'utilisateur : Donjon d'Incarnam : pas assez de clés (2 pour 3 personnages" in caplog.text
    caplog.clear()
    keys_manager, trades = manager(team(3, 0, 0))
    assert keys_manager.prepare("Donjon d'Incarnam", 1, KEY) == 1 and "À l'utilisateur" not in caplog.text
