"""Sagesse des soutiens (08/11) : bot/team/wisdom.py."""

from types import SimpleNamespace

from bot.team.wisdom import Gear, chest_contents, plan_wisdom, wisdom_of


def template(name: str) -> int:
    from bot.team.wisdom import _items

    return next(int(k) for k, v in _items().items() if v.get("n") == name)


def test_wisdom_read_from_the_item_effects():
    assert wisdom_of("7c#14##0d0+20,76#3##0d0+3") == 20  # 0x7c = 124, 0x14 = 20
    assert wisdom_of("9c#5##0d0+5") == -5 and wisdom_of("") == 0


def test_each_item_given_once_biggest_gain_first_and_never_a_dps_item():
    cape, ring, boule = template("Cape du Prespic"), template("Alliance de Silimelle"), template("Amulette de la Boule")
    gear = [
        Gear(1, cape, 30, "Eldieso", "sac"),
        Gear(2, cape, 25, "Waldarch", "porté", 7),
        Gear(3, ring, 25, "Walitcoin", "coffre"),
        Gear(4, boule, 33, "Walarbalete", "porté", 0),  # porté par le DPS : jamais pris
        Gear(5, boule, 32, "Waldarch", "sac"),
    ]
    levels = {"Enu-six": 44, "Waldarch": 51, "Pesopepe": 20}
    moves = plan_wisdom(levels, gear, {"Enu-six", "Waldarch", "Pesopepe"}, {"Walarbalete"})
    taken = {(m.taker, m.slot, m.gear.uid) for m in moves}
    assert ("Enu-six", "cape", 1) in taken  # +30 sur rien : avant Waldarch (+5)
    assert ("Waldarch", "amulette", 5) in taken  # la sienne, dans son sac
    assert all(m.gear.uid != 4 for m in moves)
    assert all(not (m.taker == "Pesopepe" and m.gear.uid == 3) for m in moves)  # niveau 26 exigé
    assert len({m.gear.uid for m in moves}) == len(moves)  # chaque objet une fois


def test_chest_contents_from_the_last_el_of_the_journals(tmp_path):
    session = tmp_path / "20261108_150000"
    session.mkdir()
    (session / "reseau_Enu-six-_Gal_.txt").write_text(
        "15:22:24.792 ELO10052e94~137~1~~;O10052e95~17d~1~~7c#14##0d0+20\n", encoding="utf-8")
    chests = chest_contents(["Enu-six-[Gal]", "Pesopepe"], tmp_path)
    assert [(g.template, g.wisdom, g.place) for g in chests["Enu-six-[Gal]"]] == [(0x137, 0, "coffre"),
                                                                                  (0x17d, 20, "coffre")]
    assert chests["Pesopepe"] == []
