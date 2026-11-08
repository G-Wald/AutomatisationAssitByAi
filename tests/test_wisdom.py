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


def test_chest_search_text_left_only_by_the_wanted_item():
    from bot.team.wisdom import unique_query

    others = ["Bottes de l'Aventurier", "Ceinture de l'aventurier", "Anneau de l'Aventurier", "Amulette de la Boule"]
    query = unique_query("Amulette de l'Aventurier", others)  # rien de seul : « Amulette de l » prend aussi la Boule
    assert query and "'" not in query and sum(query.lower() in o.lower() for o in others) == 1
    assert unique_query("Bottes de l'Aventurier", ["Bottes Paysannes", "Ceinture du Rat"]) == "Bottes de l"
    assert unique_query("Ceinture du Rat", ["Ceinture du Prespic"]) == "Ceinture du Rat"


def test_items_taken_from_the_chest_after_it_was_listed_are_gone(tmp_path):
    session = tmp_path / "20261108_172001"
    session.mkdir()
    (session / "reseau_Walitcoin.txt").write_text(
        "17:26:00.000 ELO109f5cde~1b0f~1~~7c#1d##0d0+29;O109f5cdf~17d~1~~7c#14##0d0+20\n"
        "17:26:38.734 EsKO-278879454\n", encoding="utf-8")  # 0x109f5cde
    chest = chest_contents(["Walitcoin"], tmp_path)["Walitcoin"]
    assert [(g.template, g.wisdom) for g in chest] == [(0x17d, 20)]


def test_never_the_same_ring_twice():
    # 08/11 (capture de Walitcoin) : le jeu refuse un 2e Anneau du Sanglier (« Déjà équipé ! »).
    sanglier, silimelle = template("Anneau du Sanglier"), template("Alliance de Silimelle")
    worn = Gear(1, sanglier, 8, "Walitcoin", "porté", 2)
    gear = [worn, Gear(2, sanglier, 10, "Pesopepe", "sac"), Gear(3, silimelle, 25, "Pesopepe", "sac")]
    moves = plan_wisdom({"Walitcoin": 40}, gear, {"Walitcoin"}, set())
    rings = sorted((m.gear.uid, m.replaced.uid if m.replaced else None) for m in moves)
    # la Silimelle dans l'emplacement libre ; le Sanglier +10 remplace le +8 (même emplacement), jamais les deux
    assert [m.gear.template for m in moves].count(sanglier) <= 1
    assert any(uid == 3 for uid, _ in rings)

