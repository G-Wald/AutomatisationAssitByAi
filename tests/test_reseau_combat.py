"""Combat d'après le serveur (31/10, docs/rapports/ecran_vers_reseau.md, « Revue critique du 31/10 ») : Prêt (GR), début
du combat (GS), refus de rejoindre (GA;903), main passée (GTF / GTS), PM (GTM, GA;129) et mort (GA;103) ; l'écran
seulement en secours."""

import threading
import time

from bot.brain.combat import CombatAI, CombatSettings
from bot.connection.network import ClientFeed
from bot.perception.colors import RED
from conftest import FIXTURES, load_frame
from test_brain import FakeGame

ME = 100


def blops(feed: ClientFeed, until: str | None = None) -> None:
    """Combat contre des Blops capturé le 26/10 (fenêtre de Fourboun, id 1610317), jusqu'à la ligne `until`."""
    for line in (FIXTURES / "reseau_combat_blops.txt").read_text(encoding="utf-8").splitlines():
        feed.feed(line)
        if until is not None and line.startswith(until):
            return


def test_ready_and_fight_start_from_the_server():
    feed = ClientFeed("Fourboun")
    joined = time.monotonic()
    blops(feed, "GR11610318")  # 3 Prêt sur 8, placement en cours
    assert feed.fight_since(joined) and feed.fight_at >= joined
    assert feed.fight_ready() == {1996393, 986971, 1610318} and feed.ready_at == 0.0
    assert not feed.fight_begun() and feed.fight_begun_at == 0.0
    blops(feed, "GS")
    assert 1610317 in feed.fight_ready() and feed.ready_at >= joined  # « GR11610317 » : moi
    assert len(feed.fight_ready()) == 8 and feed.fight_begun() and feed.fight_begun_at >= feed.ready_at
    feed.feed("GR01610317")  # plus prêt
    assert 1610317 not in feed.fight_ready()


def test_join_refusal_is_kept_with_its_time():
    feed = ClientFeed("Walitcoin")
    before = time.monotonic()
    assert feed.join_refused_since(before) is None
    feed.feed("GA;903;1568583;f")
    assert feed.join_refused_since(before) == "GA;903;1568583;f" and feed.join_refused_at >= before
    assert feed.join_refused_since(time.monotonic() + 1) is None  # refus d'avant cette tentative
    assert not feed.in_fight()  # entendu hors combat, avant tout GJK


def fighting_feed(*lines: str) -> ClientFeed:
    feed = ClientFeed("Moi")
    feed.my_id = ME
    for line in ("GJK2|0|1|0|30000|4", f"GTM|{ME};0;300;6;3;250;;300|-1;0;80;5;4;260;;80", *lines):
        feed.feed(line)
    return feed


def ai_with(feed: ClientFeed | None, *frames) -> CombatAI:
    game = FakeGame(*(frames or (load_frame("combat_mon_tour"),)))
    game.net = feed
    return CombatAI(game, CombatSettings())


def test_turn_passed_is_told_by_the_server_without_looking():
    ai = ai_with(fighting_feed(f"GTS{ME}|29000|1", f"GTF{ME}"))
    assert ai._turn_passed(RED, checks=10) and ai.game.parks == []  # GTF de mon id : pas de capture
    ai = ai_with(fighting_feed(f"GTS{ME}|29000|1", "GTS-1|29000|1"))  # GTF perdu, tour d'un autre commencé
    ai._turn_told = True  # mon tour annoncé au début de play_turn
    assert ai._turn_passed(RED, checks=10) and ai.game.parks == []
    ai = ai_with(fighting_feed(f"GTS{ME}|29000|1", "GE1|0|1"))  # combat fini
    ai._turn_told = True
    assert ai._turn_passed(RED, checks=10) and ai.game.parks == []


def test_turn_not_passed_while_the_server_says_it_is_still_mine():
    # L'écran dirait la main passée (tour de l'adversaire à la timeline) : le serveur fait foi.
    ai = ai_with(fighting_feed(f"GTS{ME}|29000|1"), load_frame("combat_tour_adversaire"))
    started = time.monotonic()
    assert not ai._turn_passed(RED, checks=1)
    assert time.monotonic() - started < 1.0 and ai.game.parks == []


def test_turn_passed_wakes_up_on_the_turn_end():
    feed = fighting_feed(f"GTS{ME}|29000|1")
    ai = ai_with(feed)
    threading.Timer(0.1, feed.feed, args=(f"GTF{ME}",)).start()
    started = time.monotonic()
    assert ai._turn_passed(RED, checks=20)  # 5 s au plus
    assert time.monotonic() - started < 1.0


def test_turn_passed_read_on_the_screen_without_the_server():
    theirs = load_frame("combat_tour_adversaire")
    assert ai_with(None, theirs)._turn_passed(RED, checks=2)  # pas d'écoute
    ai = ai_with(fighting_feed(), theirs)  # mon tour jamais annoncé (combat pas entendu depuis son début)
    assert ai._turn_passed(RED, checks=2) and ai.game.parks
    stale = fighting_feed(f"GTS{ME}|29000|1")
    stale.heard_at = time.monotonic() - 120  # flux plus vivant
    assert ai_with(stale, theirs)._turn_passed(RED, checks=2)


def test_pm_left_come_from_the_server_first():
    frame = load_frame("combat_mon_tour")  # bulle : 3 PM
    ai = ai_with(fighting_feed(f"GTS{ME}|29000|1", f"GA;129;{ME};{ME},-2"), frame)
    assert ai._pm_left(frame, 3) == 1  # 3 au début du tour (GTM), 2 dépensés (GA;129)
    ai = ai_with(fighting_feed(f"GTS{ME}|29000|1", f"GA;127;-1;{ME},-3"), frame)
    assert ai._pm_left(frame, 3) == 0  # tous perdus (tacle, sort d'un ennemi)
    assert ai_with(None, frame)._pm_left(frame, 3) == 3  # sans écoute : la bulle


def test_dead_by_the_server_is_never_my_turn_again():
    frame = load_frame("combat_mon_tour")
    feed = fighting_feed(f"GTS{ME}|29000|1")
    ai = ai_with(feed, frame)
    assert ai._my_turn(frame, RED)
    feed.feed(f"GA;103;-1;{ME}")  # « GA;103;tueur;mort »
    assert not ai._my_turn(frame, RED)
    feed.feed("GA;103;-1;-1")  # un autre : rien à voir avec moi
    other = ai_with(fighting_feed(f"GTS{ME}|29000|1", "GA;103;100;-1"), frame)
    assert other._my_turn(frame, RED)


def test_the_game_phase_comes_from_the_server_and_the_screen_only_gives_the_buttons():
    # 31/10, utilisateur : la phase d'après le réseau ; l'écran trompait (K.O. pris pour hors combat, timeline cachée).
    from dataclasses import replace

    from bot.state.scene import Phase, read_scene

    net = ClientFeed("Eldieso")
    out_frame = load_frame("hors_combat")
    assert net.phase() is None  # rien entendu : l'écran décide
    assert read_scene(replace(out_frame, net=net)).phase is Phase.OUT_OF_COMBAT
    net.feed("GDM|1856|0706131721|aa")
    net.feed("GDK")
    assert net.phase() == "hors combat"
    net.feed("GJK2|0|1|0|30000|4")
    assert net.phase() == "placement"
    assert read_scene(replace(out_frame, net=net)).phase is Phase.PLACEMENT  # écran hors combat : le serveur fait foi
    net.feed("GS")
    assert read_scene(replace(out_frame, net=net)).phase is Phase.COMBAT  # K.O., timeline cachée...
    net.feed("GE")
    assert read_scene(replace(load_frame("combat_timeline_en_haut"), net=net)).phase is Phase.OUT_OF_COMBAT
    assert read_scene(load_frame("combat_timeline_en_haut")).phase is Phase.COMBAT  # sans écoute : l'écran


def test_a_fight_heard_without_its_start_is_noted_from_its_turn_messages():
    # Écoute ouverte en plein combat, ou « GJK » perdu : le premier message de tour suffit (jamais juste après « GE »).
    net = ClientFeed("Eldieso")
    t0 = time.monotonic() - 30.0  # horloge du flux : « muet » au-delà de 60 s
    net.feed("GDM|1856|0706131721|aa", now=t0 + 1.0)
    net.feed("GTS1715951|30000", now=t0 + 2.0)
    assert net.phase() == "combat" and net.fight_turn()[0] == 1715951
    net.feed("GE", now=t0 + 10.0)
    net.feed("GTF1715951", now=t0 + 11.0)  # dernier message de tour arrivé après la fin
    assert net.phase() == "hors combat"
    net.feed("GPeTfjfkfLfMfNgdge|aYbabbbobqclc0de|0", now=t0 + 20.0)  # placement d'un nouveau combat, « GJK » perdu
    assert net.phase() == "placement"


def test_target_life_comes_from_the_server_before_the_hovered_sheet():
    # 31/10, utilisateur : le réseau d'abord ; la vie de la cible (GTM, puis chaque coup GA;100, mort GA;103) plutôt
    # que la fiche lue au survol.
    from bot.connection.network import map_cell

    feed = fighting_feed()
    cell = map_cell(260)
    assert feed.life_at(cell) == 80
    feed.feed(f"GA;100;{ME};-1,-24,3")
    assert feed.life_at(cell) == 56
    feed.feed(f"GA;103;{ME};-1")
    assert feed.life_at(cell) == 0
    assert feed.life_at(map_cell(300)) is None  # personne de connu sur cette case
    feed.feed("GE")
    assert feed.life_at(map_cell(250)) is None  # hors combat


def test_placement_cells_and_my_team_come_from_the_server():
    # Message réel (journal du 31/10) : cases de l'équipe 0, de l'équipe 1, mon équipe.
    from bot.connection.network import coded_cells, map_cell

    feed = fighting_feed("GPeTfjfkfLfMfNgdge|aYbabbbobqclc0de|0")
    team0, team1, mine = feed.placement_told()
    assert mine == 0 and len(team0) == 8 and len(team1) == 8
    assert map_cell(coded_cells("eT")[0]) in team0 and coded_cells("eT") == [4 * 64 + 45]
    feed.feed("GE")
    assert feed.placement_told() is None


def test_the_team_colour_from_the_server_is_used_only_after_the_screen_agreed():
    # 31/10 : équipe 0 en rouge supposée ; une erreur ferait tirer sur l'équipe (incident du 26/10).
    from types import SimpleNamespace

    from bot.perception.colors import BLUE
    from bot.team.loop import TeamBot

    feed = fighting_feed("GPeTfjfkfLfMfNgdge|aYbabbbobqclc0de|0")
    host = SimpleNamespace(game=SimpleNamespace(net=feed))
    bot = SimpleNamespace(_told_placement=TeamBot._told_placement, _told_colour_ok=None)
    told = TeamBot._colour_told
    assert told(bot, host, None) is None  # pas encore d'accord vu : le serveur ne tranche pas
    assert told(bot, host, RED) == RED and bot._told_colour_ok is True  # accord
    assert told(bot, host, None) == RED  # l'écran ne peut pas trancher : le serveur, après accord
    assert told(bot, host, BLUE) == BLUE and bot._told_colour_ok is False  # désaccord : l'écran fait foi
    assert told(bot, host, RED) == RED and bot._told_colour_ok is False  # et le serveur ne tranchera plus
    assert told(bot, host, None) is None
