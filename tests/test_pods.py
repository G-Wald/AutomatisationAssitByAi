"""Pods pendant un farm de donjon (31/10) : surcharge rééquilibrée, banque entre deux passages, équipe dehors."""

from types import SimpleNamespace

from bot.connection.network import ClientFeed
from bot.team import pods


def member(name, used, most):
    net = ClientFeed(name)
    net.feed(f"Ow{used}|0|{most}|0")
    return SimpleNamespace(name=name, game=SimpleNamespace(net=net))


def test_overload_is_what_to_give_to_walk_again():
    assert pods.overload(member("A", 1050, 1000).game.net) == 1050 - 1000 + 20  # marge de 2 %
    assert pods.overload(member("A", 990, 1000).game.net) == 0
    assert pods.overload(ClientFeed("B")) == 0  # pods inconnus


def test_an_overloaded_member_gives_to_the_freest_until_he_can_walk(monkeypatch):
    heavy, a, b = member("Pesopepe", 1100, 1000), member("Eldieso", 900, 1000), member("Walitcoin", 200, 1000)
    given = []

    def give(giver, taker, amount, free, portraits, members):
        weight = min(amount, pods.overload(giver.game.net))
        used, most = giver.game.net.pods
        giver.game.net.feed(f"Ow{used - weight}|0|{most}|0")
        t_used, t_most = taker.game.net.pods
        taker.game.net.feed(f"Ow{t_used + weight}|0|{t_most}|0")
        given.append((giver.name, taker.name, weight))
        return True

    monkeypatch.setattr(pods, "_give", give)
    assert pods.rebalance([heavy, a, b]) == []
    assert given == [("Pesopepe", "Walitcoin", 120)]  # le plus libre d'abord, ce qu'il faut pour remarcher
    assert pods.overload(heavy.game.net) == 0


def test_an_overload_nobody_can_take_keeps_everyone_here(monkeypatch):
    heavy, full = member("Pesopepe", 1100, 1000), member("Eldieso", 1000, 1000)
    monkeypatch.setattr(pods, "_give", lambda *args: False)
    assert pods.rebalance([heavy, full]) == ["Pesopepe"]


def test_fullest_member():
    assert pods.fullest([member("A", 500, 1000), member("B", 950, 1000)]) == ("B", 0.95)
    assert pods.fullest([SimpleNamespace(name="C", game=SimpleNamespace(net=ClientFeed("C")))]) is None


def farm(monkeypatch, team, keys=True):
    from bot.team import dungeon
    from bot.team.dungeon import DungeonFarm, load_dungeons

    bot = SimpleNamespace(members=team, _settings={"donjon": {"cles": "sac" if keys else "non"}}, pods_full=0.9,
                          bank_mode="chacun", keep_key=None, travel_settings=SimpleNamespace(hide_players_key="e"))
    farm = DungeonFarm(bot, load_dungeons()["bouftou"], 1)
    events = []
    monkeypatch.setattr(pods, "team_trip", lambda bot, goal, why: events.append(("trajet", goal)) or True)
    monkeypatch.setattr("bot.team.loop.bank_visit", lambda members, hide, mode, keep: SimpleNamespace(
        run=lambda: events.append(("banque", mode)) or {m.name for m in members}))
    farm.keys.prepare = lambda name, wanted: events.append(("clés", name)) or 1
    return farm, events, dungeon


def test_bank_round_trip_between_passes_only_at_the_threshold(monkeypatch):
    # 31/10, utilisateur : la banque seulement équipe dehors ; aller, dépôt, retour devant le PNJ, clés de nouveau.
    farm_, events, _ = farm(monkeypatch, [member("Fourboun", 400, 1000), member("Pesopepe", 950, 1000)])
    assert farm_._pods_ready()
    assert events == [("trajet", (4, -16)), ("banque", "chacun"), ("trajet", (2, -34)), ("clés", "Donjon Bouftou")]
    farm_, events, _ = farm(monkeypatch, [member("Fourboun", 400, 1000), member("Pesopepe", 800, 1000)])
    assert farm_._pods_ready() and events == []  # sous le seuil : rien


def test_no_pass_when_an_overload_cannot_be_shared(monkeypatch):
    farm_, events, _ = farm(monkeypatch, [member("Fourboun", 1000, 1000), member("Pesopepe", 1100, 1000)])
    monkeypatch.setattr(pods, "_give", lambda *args: False)
    assert not farm_._pods_ready() and events == []
