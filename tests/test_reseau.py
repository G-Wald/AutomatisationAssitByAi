"""Écoute passive du trafic du jeu (bot/connection/network.py, 26/10) : messages capturés en jeu le 26/10."""

import threading
import time

from bot.brain.travel import Passages, Traveler
from bot.connection import network
from bot.connection.network import ClientFeed, NetworkListener, path_cells, payloads

# Fourboun, sortie du bas de -4,-28 (carte 3667) vers -4,-27 (3668), capture du 26/10 à 20:28:18.
CROSSING = [
    "GA0;1;1610317;agNbhfahj",
    "GA;2;1610317;",
    "GDM|3668|0706131721|6b7e282c25",
    "BT1793050097896",
    "GM|+37;0;0;1610317;Fourboun;10;100^100;0;0,0,0,1610359;0;0;0;1b0c,1fe2,1b0f,,;0;;;;;0;;;0;",
    "GM|+266;7;31;-2;275,278,278,274,277,273;-3;1172^100,1178^100;34,16,14,30,14,30;-1,-1,-1;0,0,0,0|"
    "+150;7;15;-3;275,278,277,273,276,280;-3;1172^90;30,14,14,30,30,14;-1,-1,-1;0,0,0,0",
    "GDK",
    "EW+1610317|",
]


def test_path_cells_end_on_the_clicked_sun():
    assert path_cells("agNbhfahj") == [423, 453, 457]  # 457 : (15, 31), le soleil du bas de -4,-28
    assert path_cells("agedgGfgr") == [388, 416, 401]  # Eldieso en combat : départ 388, arrivée 401


def test_feed_follows_a_map_change():
    feed = ClientFeed("Fourboun")
    clicked = time.monotonic()
    for line in CROSSING[:3]:
        feed.feed(line)
    assert feed.new_map_since(clicked) is None  # carte annoncée, pas encore chargée (GDK)
    for line in CROSSING[3:]:
        feed.feed(line)
    assert feed.new_map_since(clicked) == 3668
    assert feed.my_id == 1610317 and feed.my_cell() == 37 and feed.my_grid_cell() == (16, 2)
    assert feed.actors[-2].cell == 266 and feed.actors[-3].cell == 150  # groupes de monstres
    feed.feed("GA;1;-3;achfb4")  # le groupe -3 se déplace
    assert feed.actors[-3].cell == path_cells("achfb4")[-1]
    feed.feed("GM|--3")
    assert -3 not in feed.actors


def test_my_moves_are_noted_once_my_id_is_known():
    feed = ClientFeed("Fourboun")
    for line in CROSSING:
        feed.feed(line)
    feed.feed("GA0;1;1610317;afNhe9")
    assert feed.move_cells == path_cells("afNhe9") and feed.my_cell() == path_cells("afNhe9")[-1]
    feed.feed("GA0;1;986971;agHbgW")  # un autre personnage : pas mon déplacement
    assert feed.move_cells == path_cells("afNhe9")


def test_wait_new_map_wakes_up_on_the_announcement():
    feed = ClientFeed("Fourboun")
    clicked = time.monotonic()
    threading.Timer(0.1, lambda: [feed.feed(line) for line in CROSSING]).start()
    assert feed.wait_new_map(clicked, 2.0) == 3668
    time.sleep(0.05)  # horloge de Windows : ~16 ms de résolution
    assert feed.wait_new_map(time.monotonic(), 0.05) is None  # rien de nouveau


def test_tcp_payload_of_an_ethernet_frame():
    ip = bytes([0x45, 0, 0, 0, 0, 0, 0, 0, 64, 6, 0, 0, 34, 253, 140, 241, 192, 168, 1, 71])
    tcp = (443).to_bytes(2, "big") + (49701).to_bytes(2, "big") + bytes(8) + bytes([0x50, 0x18]) + bytes(6)
    frame = bytes(14) + ip + tcp + b"GDK\x00"
    assert payloads(frame, 1) == (49701, b"GDK\x00")


def test_map_ids_of_the_reference():
    passages = Passages({}, {"-4,-28": {"ids": [3667]}, "-4,-27": {"ids": [3668]}, "x": {"ids": [1]}})
    assert passages.coords_of(3668) == (-4, -27) and passages.coords_of(3667) == (-4, -28)
    assert passages.coords_of(9999) is None


class FakeNet:
    def __init__(self, map_id=None, cell=None):
        self.map_id, self.cell, self.at = map_id, cell, 0.0

    def snapshot(self):
        return self.map_id, self.at, self.at

    def alive(self):
        return True

    def my_grid_cell(self):
        return self.cell

    def new_map_since(self, after):
        return self.map_id if self.at > after else None


def test_traveler_believes_the_announced_map_over_the_screen(monkeypatch):
    from conftest import load_frame
    from test_travel import traveler as make_traveler

    traveler = make_traveler(load_frame("hors_combat"), load_frame("hors_combat"))
    traveler.net = FakeNet(3668)
    monkeypatch.setattr(Passages, "coords_of", lambda self, map_id: {3668: (-4, -27)}.get(map_id))
    traveler.coords = (-4, -28)
    traveler.look()
    assert traveler.coords == (-4, -27) and not traveler.doubt


def replay(feed: ClientFeed, until: str | None = None) -> None:
    """Combat aux Blops du 26/10 (fenêtre de Fourboun), messages du serveur jusqu'à `until` compris."""
    from conftest import FIXTURES

    for line in (FIXTURES / "reseau_combat_blops.txt").read_text(encoding="utf-8").splitlines():
        feed.feed(line)
        if until is not None and line.startswith(until):
            return


def test_fight_cells_turns_and_life_from_the_server():
    feed = ClientFeed("Fourboun")
    replay(feed, "GTS1715951")  # tour d'Eldieso
    assert feed.in_fight() and feed.my_id == 1610317 and feed.fight.my_team == 0
    turn, over, _ = feed.fight_turn()
    assert turn == 1715951 and not over
    fighters = {who: (cell, team, state.hp) for who, cell, team, state in feed.fighters()}
    assert fighters[986971][0] == (18, 22)  # Enu-six-[Gal] après son déplacement (vu à l'écran : (20, 24) avant)
    assert fighters[1610317] == ((22, 24), 0, 342)  # moi
    assert fighters[-2][1] == 1 and fighters[-2][2] == 330  # un Blop de l'autre équipe, sa vie au début du tour
    assert feed.my_grid_cell() == (22, 24)


def test_turn_over_dead_and_fight_end():
    feed = ClientFeed("Fourboun")
    replay(feed, "GTF1715951")
    assert feed.fight_turn()[1]  # tour d'Eldieso fini
    replay(feed)
    assert not feed.in_fight() and feed.fighters() == []


def test_monster_groups_on_the_map():
    feed = ClientFeed("Fourboun")
    for line in CROSSING:
        feed.feed(line)
    groups = dict(feed.groups())
    assert len(groups) == 2 and sorted(len(levels) for levels in groups.values()) == [6, 6]


def test_teammates_from_the_server_are_never_targets():
    # 26/10 (journal 205705) : couleur de la timeline lue en retard, mes personnages pris pour des ennemis et tapés.
    from bot.brain.combat import CombatAI, CombatSettings
    from bot.perception.fighters import Fighter

    class Game:
        net = ClientFeed("Fourboun")

    replay(Game.net, "GTS1715951")
    ai = CombatAI(Game(), CombatSettings())
    friends = ai._told_allies()
    assert (18, 22) in friends and (22, 26) in friends and (22, 24) not in friends  # Enu-six, Eldieso ; pas moi
    assert all(cell not in friends for cell in [(19, 17), (11, 5), (12, 12)])  # Blops
    enemies = [Fighter("blue", 0, 0, (18, 22)), Fighter("blue", 0, 0, (19, 17))]
    assert [e.cell for e in enemies if e.cell not in friends] == [(19, 17)]


def test_every_turn_of_the_captured_fight_splits_allies_and_enemies_right():
    # À chaque début de tour du combat capturé : mes 7 alliés de mon équipe, les Blops de l'autre, moi connu.
    from conftest import FIXTURES

    ours = {986971, 1095456, 1439688, 1568583, 1610317, 1610318, 1715951, 1996393}
    feed, checked = ClientFeed("Fourboun"), 0
    for line in (FIXTURES / "reseau_combat_blops.txt").read_text(encoding="utf-8").splitlines():
        feed.feed(line)
        if line.startswith("GTS") and feed.in_fight():
            mine = feed.my_team()
            for who, cell, team, state in feed.fighters():
                expected_mine = who in ours or (team is None and who > 0)
                assert (team == mine if team is not None else who > 0) == expected_mine, (line, who, team)
            assert feed.my_state() is not None and feed.my_grid_cell() is not None
            checked += 1
    assert checked >= 20


def test_my_pa_follow_the_spells():
    feed = ClientFeed("Pesopepe")
    for line in ("GJK2|0|1|0|30000|4", "GM|+329;1;0;1996393;Pesopepe;3;30^100;0;23;0;0;0;0;301;6;3;0;;0;0;301;",
                 "GTM|1996393;0;301;6;3;329;;301", "GTS1996393|29000|1"):
        feed.feed(line)
    assert feed.my_state().pa == 6
    before = time.monotonic()
    feed.feed("GA;300;1996393;51,256,306,4,21,1,1")
    feed.feed("GA;102;1996393;1996393,-2")
    assert feed.my_state().pa == 4 and feed.wait_for("spent_at", before, 0.1) is not None
    assert feed.wait_for("spent_at", time.monotonic() + 1, 0.05) is None


def test_the_sword_of_the_leaders_team():
    feed = ClientFeed("Eldieso")
    for line in ("Gc+-286;4|-286;185;0;-1|-287;171;1;-1", "Gt-286|+1610317;Fourboun;42",
                 "Gt-287|+-1;280;14|+-2;276;30"):
        feed.feed(line)
    from bot.brain.arena import map_cell

    assert feed.flag_cell(1610317) == map_cell(185) and feed.flag_cell(-1) == map_cell(171)
    assert feed.flag_cell(42) is None
    feed.feed("Gc--286")
    assert feed.flag_cell(1610317) is None


def test_damage_and_critical_failure_from_the_server():
    feed = ClientFeed("Pesopepe")
    replay(feed, "GTS1996393")
    hp = {who: state.hp for who, _, _, state in feed.fighters()}[-2]
    feed.feed("GA;300;1996393;51,256,306,4,21,1,1")
    feed.feed("GA;100;1996393;-2,-11,3")
    assert {who: state.hp for who, _, _, state in feed.fighters()}[-2] == hp - 11
    before = time.monotonic()
    feed.feed("GA;302;1996393;51")
    assert feed.failed_at >= before


def test_errand_to_the_bank_then_back_to_the_zone():
    from conftest import load_frame
    from test_travel import traveler as make_traveler

    from bot.brain.travel import ZoneRoamer

    roamer = ZoneRoamer(make_traveler(load_frame("hors_combat")))
    roamer.meeting, roamer._away = (-3, -25), False
    roamer.start_errand((4, -16), "Pods pleins")
    assert roamer.away and roamer.meeting is None and roamer.errand == (4, -16)
    roamer.traveler.coords = (4, -16)
    roamer.step()
    assert roamer.errand_reached
    roamer.end_errand()
    assert roamer.errand is None and roamer.meeting == (-3, -25) and roamer.away is None


def test_life_energy_and_pods_from_the_server():
    feed = ClientFeed("Fourboun")
    feed.feed("As2452562,2250000,2530000|20207|7|2|0~0,0,0,0,0,0|171,342|9580,10000|100|100|6,0,0,0,0,6|3,0,0,0,0,3")
    feed.feed("Ow721|0|1800|4200")
    assert feed.life_ratio() == 0.5 and feed.energy == (9580, 10000)
    assert abs(feed.pods_ratio() - 721 / 1800) < 1e-9  # pods portés / pods max (40 %, comme la jauge)


def test_groups_to_attack_from_the_server():
    from bot.brain.arena import map_cell

    feed = ClientFeed("Fourboun")
    for line in CROSSING:
        feed.feed(line)
    groups = {gid: (cell, levels) for gid, cell, levels in feed.group_list()}
    assert groups[-2] == (map_cell(266), (34, 16, 14, 30, 14, 30))
    feed.feed("GA;1;-2;ahohha")  # le groupe -2 bouge
    assert feed.group_cell(-2) == map_cell(path_cells("ahohha")[-1])
    clicked = time.monotonic()
    feed.feed("GJK2|0|1|0|30000|4")
    assert feed.fight_since(clicked) and feed.group_list() == []


def test_walk_length_comes_from_the_pm_spent():
    # Chemin compressé (changements de direction seulement) : 2 points pour 3 cases, 3 PM dépensés (GA;129).
    feed = ClientFeed("Fourboun")
    replay(feed, "GTS1610317")
    feed.feed("GA0;1;1610317;afNhe9")
    assert feed.move_steps == 1
    feed.feed("GA;129;1610317;1610317,-3")
    assert feed.move_steps == 3


def test_a_fight_without_its_end_is_closed_by_the_map_reload_and_stale_feeds_are_not_alive():
    feed = ClientFeed("Fourboun")
    replay(feed, "GTS1715951")
    assert feed.in_fight() and feed.alive()
    feed.feed("GDM|3667|0706131721|cle")
    assert not feed.in_fight()
    feed.heard_at -= 120
    assert not feed.alive()


def test_summons_join_the_fight_with_the_team_of_their_summoner():
    feed = ClientFeed("Fourboun")
    replay(feed, "GTS1715951")
    feed.feed("GA;181;-2;+210;1;0;-9;280;-1;1177^90;1;-1;-1;-1;0,0,0,0;84;5;4;1;1;84")
    team_of = {who: team for who, _, team, _ in feed.fighters()}
    assert team_of[-9] == team_of[-2] == 1


def test_short_route_to_help_an_aggressed_teammate():
    from bot.team.loop import _short_route

    passages = Passages({}, {"0,0": {"sorties": {"droite": {"vers": "1,0"}}},
                             "1,0": {"sorties": {"bas": {"vers": "1,1"}, "gauche": {"vers": "0,0"}}},
                             "1,1": {"sorties": {"droite": {"vers": "2,1"}}}})
    assert _short_route(passages, (0, 0), (1, 1), 2) == [("droite", (1, 0)), ("bas", (1, 1))]
    assert _short_route(passages, (0, 0), (2, 1), 2) is None  # 3 cartes : trop loin pour le placement


class _FailingPcap:
    """Npcap factice : chaque poignée renvoie une erreur à la première lecture (veille, câble débranché)."""

    def __init__(self):
        self.closed, self.opened = [], 0

    def pcap_datalink(self, handle):
        return 1

    def pcap_next_ex(self, handle, header, data):
        return -1

    def pcap_close(self, handle):
        self.closed.append(handle)


def test_listening_reopens_an_interface_after_a_npcap_error(monkeypatch):
    pcap = _FailingPcap()
    monkeypatch.setattr(network, "_interfaces", lambda _: [(b"wifi", ["192.168.1.20"]), (b"lo", ["127.0.0.1"]),
                                                           (b"off", ["169.254.3.4"])])

    def open_handle(_, name):
        pcap.opened += 1
        return f"{name.decode()}-{pcap.opened}"

    monkeypatch.setattr(network, "_open_handle", open_handle)
    listener = NetworkListener(pcap, {b"wifi": "wifi-0"})
    listener._run(b"wifi", "wifi-0")  # erreur Npcap : interface fermée et oubliée
    assert pcap.closed == ["wifi-0"] and listener.listening() == 0
    monkeypatch.setattr(listener, "_spawn", lambda name, handle: None)
    assert listener.rescan() == 1  # seule l'interface avec une vraie adresse est rouverte
    assert listener._handles == {b"wifi": "wifi-1"}
    assert listener.rescan() == 0  # déjà écoutée


AS_ENU_SIX = "As1971466,1780000,2000000|9495|6|3|0~0,0,0,0,0,0|359,359|10000,10000|375|120|6,1,0,0,0,7|3,0,0,0,0,3|0,92,0,100,0|0,109,0,0,0|63,20,0,0,0|0,0,0,0,0|0,0,0,0,0|0,85,0,100,0|0,0,0,0,0|1,1,0,0,0|0,5,0,0,0|0,0,0,0,0|0,0,0,0,0|0,6,0,0|0,0,0,0,0|0,0,0,0,0|40|-1"  # 26/10, en combat : Force et Intelligence +100 de boost


def test_characteristics_from_the_server_match_the_advanced_window():
    # 27/10 : base + équipement + dons = colonne Total lue à l'écran, pour les 8 personnages ; le boost laissé de côté.
    feed = ClientFeed("Enu-six-[Gal]")
    feed.feed(AS_ENU_SIX)
    assert feed.stats == {"pa": 7, "pm": 3, "force": 92, "vitalite": 109, "sagesse": 83, "chance": 0, "agilite": 0,
                          "intelligence": 85, "portee": 0, "invocations": 2, "dommages": 5, "dommages_physiques": 0,
                          "dommages_pourcent": 6}
    feed.feed("As1|2|3")  # tronqué : rien de changé
    assert feed.stats["force"] == 92


def test_a_window_followed_from_the_login_screen_keeps_its_feed(monkeypatch):
    monkeypatch.setattr(network, "window_pid", lambda hwnd: 4242)
    listener = NetworkListener(None, {})
    early = listener.follow(1, "")
    early.feed(AS_ENU_SIX)
    assert listener.follow(1, "Enu-six-[Gal]") is early and early.name == "Enu-six-[Gal]" and early.stats
    assert listener.follow(1, "Autre") is not early  # autre personnage dans la fenêtre : flux neuf


def test_recent_messages_for_a_failed_move_never_keep_the_players_chat():
    feed = ClientFeed("Walitcoin")
    feed.feed("GTS1|29000|1", now=10.0)
    feed.feed("cMK|1|Untel|bonjour", now=11.0)  # chat d'un autre joueur : jamais gardé
    feed.feed("GA;129;1;1,-1", now=12.0)
    assert feed.heard_since(10.5) == ["GA;129;1;1,-1"] and len(feed.heard_since(0)) == 2


def test_a_tackle_is_announced_with_the_pm_and_pa_lost():
    # 27/10, Walitcoin quittant le contact d'un ennemi : « GA;104 », tous ses PM perdus et un PA ; aucun déplacement.
    feed = ClientFeed("Pesopepe")
    for line in ("GJK2|0|1|0|30000|4", "GM|+329;1;0;1996393;Pesopepe;3;30^100;0;23;0;0;0;0;301;6;3;0;;0;0;301;",
                 "GTM|1996393;0;301;6;3;329;;301", "GTS1996393|29000|1"):
        feed.feed(line)
    clicked = time.monotonic()
    for line in ("GA;104;1996393;", "GA;127;1996393;1996393,-3", "GA;101;1996393;1996393,-1"):
        feed.feed(line)
    assert feed.wait_for(("moved_at", "tackled_at"), clicked, 0.1) is not None and feed.tackled_at >= clicked
    assert feed.moved_at < clicked and feed.move_steps == 0 and feed.spent_at < clicked
    assert (feed.my_state().pa, feed.my_state().pm) == (5, 0)



def test_my_position_answer_is_read_from_the_server_and_nothing_else_of_the_chat():
    # 31/10 (observé) : « /p %pos% » d'Eldieso renvoyé à tout le groupe : « cMK$|1715951|Eldieso|[2, -34]| ».
    from bot.connection.network import ClientFeed

    net = ClientFeed("Eldieso")
    net.feed("ASK|1715951|Eldieso|38|9|0|10|0|0|0||0")
    net.feed("cMK$|1610317|Fourboun|[1, -32]|", now=5.0)  # un autre du groupe : pas ma position
    net.feed("cMK:|1715951|Eldieso|[9, 9]|", now=6.0)  # autre canal
    assert net.position_told_since(0.0) is None
    net.feed("cMK$|1715951|Eldieso|[2, -34]|", now=10.0)
    assert net.position_told_since(9.0) == (2, -34)
    assert net.position_told_since(11.0) is None  # réponse d'avant la demande
    net.feed("cMK$|1715951|Eldieso|salut|", now=12.0)
    assert net.position_told_since(11.0) is None
    assert all(not text.startswith("cMK") for _, text in net.recent)  # le chat n'est jamais gardé


def test_the_session_network_journal_keeps_every_server_message_but_never_the_chat(tmp_path):
    # 31/10, compte rendu : chaque raté doit pouvoir s'expliquer après coup ; le chat des autres joueurs jamais gardé.
    from bot.connection.network import ClientFeed, NetworkListener

    listener = NetworkListener(None, {})
    feed = ClientFeed("Waldarch-[Gal]")
    listener._write_journal(feed, "GA;150;-1;-1,1")  # rien tant que le journal n'est pas demandé
    listener.keep_journal(tmp_path)
    for text in ("GTS-1|29000", "cMK:|123|Autre|salut|", "cMK$|1|Waldarch-[Gal]|[2, -34]|", "GE1|0|1"):
        listener._write_journal(feed, text)
    lines = (tmp_path / "reseau_Waldarch-_Gal_.txt").read_text(encoding="utf-8").splitlines()
    assert [line.split(" ", 1)[1] for line in lines] == ["GTS-1|29000", "GE1|0|1"]


def test_character_ids_are_kept_from_one_session_to_the_next():
    # 31/10 : le numéro d'un personnage ne change pas ; connu dès le lancement, même relancé en plein combat.
    from bot.connection import network

    net = network.ClientFeed("Eldieso")
    net.feed("ASK|1715951|Eldieso|38|9|0|10|0|0|0||0")
    assert net.my_id == 1715951 and network.known_ids() == {"Eldieso": 1715951}
    network._ids_cache = None  # nouvelle session : relu du fichier
    listener = network.NetworkListener(None, {})
    network.window_pid, saved = (lambda hwnd: 42), network.window_pid
    try:
        assert listener.follow(1, "Eldieso").my_id == 1715951
    finally:
        network.window_pid = saved


def test_group_levels_and_life_from_the_server():
    # 01/11 : « PM~id;nom;gfx;c1;c2;c3;accessoires;vie,max;niveau;initiative;... » : niveau et vie de chaque membre.
    net = ClientFeed("Fourboun")
    net.feed("PM~1610317;Fourboun;100;0;0;0;1b0c,1fe2,1b0f,,;352,352;44;100;100;0")
    net.feed("PM+1610318;Fourbodeux;100;6623967;8521707;7802321;1b0c,1fe2,96e,6af,;300,335;36;259;100;0")
    assert net.group_levels == {"Fourboun": 44, "Fourbodeux": 36}
    assert net.group_life == {"Fourboun": (352, 352), "Fourbodeux": (300, 335)}


def test_cast_spell_number_from_the_server():
    net = ClientFeed("Eldieso")
    net.my_id = 1715951
    net.feed("GA;300;1715951;168,297,1014,5,51,1,1")
    assert net.cast_spell == 168 and net.cast_at > 0


def test_team_levels_come_from_the_group_messages(tmp_path):
    # 01/11 : niveaux de l'équipe d'après PM (principal compris), retenus ; pas de liste des amis à lire.
    from types import SimpleNamespace

    from bot.team.loop import TeamBot
    from bot.team.member import load_level

    settings = {"session": {"memory": str(tmp_path)}}
    net = ClientFeed("Fourboun")
    net.heard_at = time.monotonic()
    net.feed("PM~1610317;Fourboun;100;0;0;0;;352,352;44;100;100;0")
    net.feed("PM~1610318;Fourbodeux;100;0;0;0;;335,335;36;259;100;0")
    members = [SimpleNamespace(name=name, game=SimpleNamespace(net=net)) for name in ("Fourboun", "Fourbodeux")]
    engager = SimpleNamespace(team_levels=[None, 30], level_limit=lambda: 0)
    bot = SimpleNamespace(engager=engager, members=members, leader=members[0], _settings=settings)
    TeamBot.learn_levels(bot, friends=False)
    assert [load_level(settings, m.name) for m in members] == [44, 36] and engager.team_levels == [44, 36]


def test_followers_on_the_leader_map_by_the_server():
    from types import SimpleNamespace

    from bot.team.loop import TeamBot

    def member(map_id):
        net = ClientFeed("x")
        net.heard_at, net.map_id = time.monotonic(), map_id
        return SimpleNamespace(game=SimpleNamespace(net=net))

    leader = member(1906)
    bot = SimpleNamespace(leader=leader)
    assert TeamBot._net_same_map(bot, member(1906)) is True
    assert TeamBot._net_same_map(bot, member(1911)) is False
    assert TeamBot._net_same_map(bot, SimpleNamespace(game=SimpleNamespace(net=None))) is None
    # 01/11 (journal 113029) : le principal arrivé sur la carte suivante pendant le tour des suiveurs ; comparés à sa
    # carte de départ, ceux restés sur elle le suivent.
    leader.game.net.map_id = 1911
    assert TeamBot._net_same_map(bot, member(1906), leader_map=1906) is True


def test_reconnected_follower_compared_with_where_the_leader_knows_he_is():
    # 01/11 : Pesopepe reconnecté en 1,-28 (carte annoncée à sa connexion), le principal sans carte annoncée depuis le
    # début de l'écoute mais qui se sait en 1,-28 : même carte, sans juger au décor.
    from types import SimpleNamespace

    from bot.team.loop import TeamBot

    peso_net, leader_net = ClientFeed("Pesopepe"), ClientFeed("Fourboun")
    peso_net.heard_at = leader_net.heard_at = time.monotonic()
    peso_net.map_id = 7354
    passages = SimpleNamespace(coords_of=lambda map_id: {7354: (1, -28)}.get(map_id))
    traveler = SimpleNamespace(coords=(1, -28), doubt=False, passages=passages)
    peso = SimpleNamespace(game=SimpleNamespace(net=peso_net))
    bot = SimpleNamespace(leader=SimpleNamespace(game=SimpleNamespace(net=leader_net)),
                          roamer=SimpleNamespace(traveler=traveler))
    bot._told_at = lambda member: TeamBot._told_at(bot, member)
    assert TeamBot._net_same_map(bot, peso) is True
    traveler.coords = (2, -27)
    assert TeamBot._net_same_map(bot, peso) is False
    traveler.doubt = True  # le principal ne sait pas où il est : le décor
    assert TeamBot._net_same_map(bot, peso) is None


def test_a_retransmitted_tcp_segment_is_read_once():
    # 01/11 (journal 113029, Eldieso) : segment renvoyé par le réseau relu, « Tir Puissant » annoncé deux fois.
    from bot.connection.network import fresh_part, segment

    ip = bytes([0x45, 0, 0, 0, 0, 0, 0, 0, 64, 6, 0, 0, 34, 253, 140, 241, 192, 168, 1, 71])
    tcp = (443).to_bytes(2, "big") + (49701).to_bytes(2, "big") + (1000).to_bytes(4, "big") + bytes(4) \
        + bytes([0x50, 0x18]) + bytes(6)
    assert segment(bytes(14) + ip + tcp + b"GDK\x00", 1) == (49701, 1000, b"GDK\x00")
    data, nxt = fresh_part(None, 1000, b"GA;300\x00")
    assert data == b"GA;300\x00" and nxt == 1007
    assert fresh_part(nxt, 1000, b"GA;300\x00") == (b"", 1007)  # renvoyé : rien de neuf
    assert fresh_part(nxt, 1004, b"00\x00BN\x00") == (b"BN\x00", 1010)  # à cheval : la fin seulement
    assert fresh_part(1010, 1020, b"GTF\x00") == (b"GTF\x00", 1024)  # trou (segment perdu) : lu quand même
    assert fresh_part(1024, 3_000_000_000, b"GDK\x00") == (b"GDK\x00", 3_000_000_004)  # nouvelle connexion
    assert fresh_part((1 << 32) - 2, (1 << 32) - 2, b"ABCD") == (b"ABCD", 2)  # numéro qui reboucle


def test_ethernet_padding_is_not_tcp_data():
    # 01/11 (journal 144405) : accusé de réception sans données, trame complétée à 60 octets ; ce remplissage compté
    # comme données faisait jeter le début du segment suivant (« GA;129 » lu « ;129 »).
    from bot.connection.network import fresh_part, segment

    ip = bytes([0x45, 0, 0, 40, 0, 0, 0, 0, 64, 6, 0, 0, 34, 253, 140, 241, 192, 168, 1, 71])  # 20 + 20 octets
    tcp = (443).to_bytes(2, "big") + (49701).to_bytes(2, "big") + (1000).to_bytes(4, "big") + bytes(4) \
        + bytes([0x50, 0x10]) + bytes(6)
    assert segment(bytes(14) + ip + tcp + bytes(6), 1) == (49701, 1000, b"")
    assert fresh_part(1000, 1000, b"GA;129;1;1,-3\x00") == (b"GA;129;1;1,-3\x00", 1014)


def test_zone_is_kept_when_the_bank_errand_starts_before_the_first_step():
    # 01/11 (journal 162522) : --zone sans position, pods pleins au premier tour ; au retour de la banque, la cité
    # d'Astrub prise pour la zone de combat.
    from types import SimpleNamespace

    from conftest import load_frame
    from test_travel import traveler as make_traveler

    from bot.brain.travel import ZoneRoamer

    traveler = make_traveler(load_frame("hors_combat"))
    roamer = ZoneRoamer(traveler)
    roamer.zone_name, roamer.meeting = None, None
    from bot.brain.atlas import Atlas

    traveler.atlas = Atlas.load()
    traveler.region, traveler.coords = "Monde des Douze", (3, -26)  # forêt d'Astrub
    traveler.look = lambda: (None, SimpleNamespace(zone_name=None))
    roamer.start_errand((4, -16), "Pods pleins")
    assert roamer.zone_name == "Forêt d'Astrub" and roamer._saved_meeting == (3, -26)


def test_listener_health_line_for_the_session_summary():
    # 01/11 : les messages tronqués ne se voyaient qu'en fouillant les journaux ; l'état de l'écoute au bilan.
    import threading

    from bot.connection.network import NetworkListener

    listener = NetworkListener.__new__(NetworkListener)
    listener._lock = threading.Lock()
    quiet, alive = ClientFeed("Pesopepe"), ClientFeed("Fourboun")
    quiet.heard_at, alive.heard_at = time.monotonic() - 120, time.monotonic()
    listener._feeds = {1: quiet, 2: alive}
    listener.counts = {"messages": 1200, "segments_renvoyes": 3, "segments_a_cheval": 1, "octets_ecartes": 410,
                       "messages_anormaux": 0}
    line = listener.health()
    assert line.startswith("1200 messages, 3 paquets renvoyés et 1 à cheval écartés (410 octets), 0 message(s)")
    assert "plus rien du serveur pour Pesopepe" in line and "Fourboun" not in line


def test_monster_template_and_grade_read_in_a_fight():
    # 08/11 : résistances des monstres (brain/monsters.py) d'après leur modèle et leur grade, donnés par le serveur au
    # début du combat (« case;dir;0;id;modèle;-2;apparence;grade;... », journal 103434 : Biblop Griotte 280 grade 2).
    from bot.brain.monsters import resistances

    feed = ClientFeed("Waldhary-[Gal]")
    feed.feed("BN")
    feed.feed("GJK2|0|1|0|45000|4")
    feed.feed("GM|+270;1;0;-1;280;-2;1177^95;2;-1;-1;-1;0,0,0,0;95;5;4;;1;95|+285;1;0;-2;275;-2;1172^90;1;-1;-1;-1;"
              "0,0,0,0;330;6;4;;1;330")
    assert (feed.actors[-1].name, feed.actors[-1].grade) == ("280", 2)
    assert (feed.actors[-2].name, feed.actors[-2].grade) == ("275", 1)
    assert resistances(275, 1)["feu"] == 50 and resistances(275, 1)["eau"] == -62  # Blop Griotte
