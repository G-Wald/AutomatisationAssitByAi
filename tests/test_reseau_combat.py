"""Combat d'après le serveur (31/10, docs/rapports/ecran_vers_reseau.md, « Revue critique du 31/10 ») : Prêt (GR), début
du combat (GS), refus de rejoindre (GA;903), main passée (GTF / GTS), PM (GTM, GA;129) et mort (GA;103) ; l'écran
seulement en secours."""

import threading
import time

import pytest

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


def test_foes_standing_and_my_pa_come_from_the_server():
    # 31/10 : ennemis debout et PA d'après le serveur, plus d'après les cercles et la bulle.
    from bot.connection.network import map_cell

    feed = fighting_feed(f"GA;102;{ME};{ME},-2")
    feed.feed("GIC|" + f"{ME};250;1|-1;260;1")
    feed.actors[ME].team, feed.actors[-1].team = 0, 1
    ai = ai_with(feed)
    frame = load_frame("combat_mon_tour")
    assert ai._told_foes() == {map_cell(260)}
    assert ai._pa_now(frame) == 4
    feed.feed(f"GA;103;{ME};-1")
    assert ai._told_foes() == set()
    assert not ai._foes_standing(frame)
    assert ai_with(None)._told_foes() is None  # sans écoute : l'écran


def test_an_invisible_prespic_is_no_target_and_never_placed_off_the_map():
    # 31/10 (journal réseau, forêt d'Astrub) : Camouflage annoncé « GA;150;-1;-1,3,0 », case -1 dans GTM tant qu'il est
    # invisible ; la case -1 devenait (27, -1), hors de la carte, et Fourbodeux y a lancé Ronce.
    from bot.connection.network import map_cell

    feed = fighting_feed()
    feed.feed("GIC|" + f"{ME};250;1|-1;260;1")
    feed.actors[ME].team, feed.actors[-1].team = 0, 1
    feed.feed("GA;150;-1;-1,3,0")
    feed.feed(f"GTM|{ME};0;300;6;3;250;;300|-1;0;75;6;6;-1;;75")
    assert [who for who, *_ in feed.fighters()] == [ME]
    assert feed.hidden_fighters() == [(-1, map_cell(260), 1)]  # dernière case connue gardée
    ai = ai_with(feed)
    assert ai._told_foes() == set() and not ai._foes_standing(load_frame("combat_mon_tour"))
    assert ai._only_hidden_foes()  # pas de « Je ne vois pas d'ennemi » ni de capture (comparatif du 01/11)
    feed.feed("GA;150;-1;-1,0,0")
    feed.feed(f"GTM|{ME};0;300;6;3;250;;300|-1;0;75;6;6;225;;75")
    assert ai._told_foes() == {map_cell(225)} and feed.hidden_fighters() == []


def test_line_of_sight_comes_from_the_client_map_without_reading_the_shown_range():
    # 31/10, utilisateur : la ligne de vue d'après la carte du client en priorité ; la portée colorée n'est plus lue.
    from bot.brain.arena import Arena
    from bot.brain.tactics import CombatSpell
    from bot.connection.network import map_cell
    from bot.perception.fighters import Fighter

    feed = fighting_feed()
    feed.feed("GIC|" + f"{ME};250;1|-1;260;1")
    feed.actors[ME].team, feed.actors[-1].team = 0, 1
    ai = ai_with(feed)
    me_cell, foe_cell = map_cell(250), map_cell(260)
    me, foe = Fighter(RED, 0, 0, me_cell), Fighter("blue", 0, 0, foe_cell)
    arrow = CombatSpell(1, "fleche", "Flèche", 3, (1, 8))
    assert not ai._client_sight  # carte des références, ou pas de carte : l'écran comme avant
    ai.map.adopt(Arena("1856", "2,-34", frozenset(), frozenset(), from_client=True))
    assert ai._client_sight and not ai._no_sight_shown(me, foe, arrow)
    assert (me_cell, foe_cell) == ((7, 17), (27, 17))
    ai.map.adopt(Arena("1856", "2,-34", frozenset({(17, 17)}), frozenset(), from_client=True))  # bloc entre les deux
    assert ai._no_sight_shown(me, foe, arrow)
    assert not ai._no_sight_shown(me, foe, CombatSpell(2, "pelle", "Roulage de Pelle", 3, (1, 8), needs_sight=False))


def test_no_obstacles_read_on_screen_with_the_client_map(monkeypatch, caplog):
    # 01/11 : « Carte hors mode tactique : obstacles inconnus » alors que la carte du client était adoptée.
    from bot.brain import combat
    from bot.brain.arena import Arena

    ai = ai_with(fighting_feed())
    monkeypatch.setattr(combat, "tactical_mode", lambda frame: pytest.fail("écran lu"))
    ai.map.adopt(Arena("1856", "2,-34", frozenset(), frozenset(), from_client=True))
    ai._learn_blocks(None, [])
    assert "obstacles inconnus" not in caplog.text


def test_tactics_takes_the_spells_when_the_bar_is_already_known():
    # 01/11 : cases de la barre annoncées par le serveur avant le combat ; le combat ne prenait ses sorts qu'en
    # repérant la barre lui-même : aucun sort, tous les tours passés.
    from types import SimpleNamespace

    from bot.brain.tactics import CombatSpell

    arrow = CombatSpell(1, "fleche_magique", "Flèche Magique", 3, (1, 8))
    book = SimpleNamespace(entries=[object()], slots={1: object()}, locate=lambda frame: None,
                           combat_spells=lambda: [arrow], refresh_stats=lambda: False)
    ai = ai_with(fighting_feed())
    ai.spellbook = book
    ai._use_spellbook(None)
    assert ai._spells == [arrow]


def test_walk_waits_after_the_server_answer_and_learns_long_animations(caplog):
    # 01/11 (413 clics depuis le 27/10) : un clic de déplacement pendant l'animation du dernier sort est ignoré ; Oeil
    # de Taupe 6 fois sur 7. Attente comptée depuis la réponse du serveur ; un sort après lequel le second clic, au même
    # endroit, a marché attend plus longtemps, pour toute l'équipe.
    from bot.brain import combat
    from bot.brain.tactics import CombatSpell

    caplog.set_level("INFO")
    mole = CombatSpell(5, "oeil_de_taupe", "Oeil de Taupe", 3, (1, 6))
    ai, other = ai_with(fighting_feed()), ai_with(fighting_feed())
    assert ai._animation_wait() == combat.WALK_AFTER_SERVED  # pas de sort encore
    ai._served_at, ai._served_spell = 10.0, mole
    ai._slow_animation(10.0 + 0.9)  # premier clic 0,9 s après la réponse, ignoré
    assert ai._animation_wait() == pytest.approx(0.9 + combat.WALK_SLOW_MARGIN)
    other._served_spell = mole
    assert other._animation_wait() == pytest.approx(0.9 + combat.WALK_SLOW_MARGIN)  # appris pour l'équipe
    assert "Oeil de Taupe : animation plus longue" in caplog.text
    ai._slow_animation(10.0 + 0.3)  # plus tôt : rien de plus long à apprendre
    ai._slow_animation(10.0 + 5.0)  # bien après : pas l'animation
    assert ai._animation_wait() == pytest.approx(0.9 + combat.WALK_SLOW_MARGIN)


class _ClickFeeds(FakeGame):
    """Faux jeu dont le n-ième clic gauche fait annoncer `line` par le serveur (déplacement pris)."""

    def __init__(self, feed: ClientFeed, line: str | None, at_click: int, *frames) -> None:
        super().__init__(*frames)
        self.feed, self.line, self.at_click, self.left_clicks = feed, line, at_click, 0

    def click(self, point=None, spread=(0, 0), button="left"):
        super().click(point, spread, button)
        if button == "left":
            self.left_clicks += 1
            if self.left_clicks == self.at_click and self.line is not None:
                self.feed.feed(self.line)


def _walk_with(line_at: int | None, caplog):
    from bot.brain.arena import map_cell
    from bot.connection.network import HASH
    from bot.perception.fighters import Fighter

    start, best = (14, 20), (15, 19)
    number = next(n for n in range(479) if map_cell(n) == best)
    start_number = next(n for n in range(479) if map_cell(n) == start)
    feed = fighting_feed(f"GTS{ME}|29000|1")
    move = f"GA0;1;{ME};a{HASH[start_number // 64]}{HASH[start_number % 64]}b{HASH[number // 64]}{HASH[number % 64]}"
    frame = load_frame("combat_mon_tour")
    game = _ClickFeeds(feed, move if line_at else None, line_at or 0, frame)
    game.net = feed
    ai = CombatAI(game, CombatSettings())
    me = Fighter(RED, *frame.grid.center(start), start)
    caplog.set_level("INFO")
    used = ai._walk(frame, me, best, [me], {best}, "test", 1)
    return used, game, ai._turn_stats()


def test_a_move_click_without_server_answer_is_clicked_again_at_once_and_counted_as_a_retry(caplog):
    # 01/11 (l'utilisateur) : rien du serveur en NET_MOVE_SILENT s après un clic de déplacement, nouveau clic aussitôt
    # (avant : ~1,9 s pour conclure) ; compté comme reclic, pas comme déplacement raté.
    started = time.monotonic()
    used, game, stats = _walk_with(2, caplog)  # le second clic est pris
    assert used == 1 and game.left_clicks == 2
    assert stats.move_retries == 1 and stats.stuck_moves == 0
    assert "Clic de déplacement pas pris (rien du serveur en 0.6 s)" in caplog.text
    assert "n'a pas bougé" not in caplog.text
    assert time.monotonic() - started < 1.5


def test_a_move_answered_by_the_server_is_never_clicked_twice(caplog):
    used, game, stats = _walk_with(1, caplog)
    assert used == 1 and game.left_clicks == 1 and stats.move_retries == 0


def test_a_summon_is_told_by_the_server():
    # 01/11 : « GA;181 » (invocation) : seule cible de Désinvocation.
    feed = fighting_feed("GA;181;-1;+200;1;0;-7;0")
    states = {who: state for who, _, _, state in feed.fighters()}
    assert states[-7].summoned and not states[-1].summoned and not states[ME].summoned


def test_a_cast_the_server_did_not_hear_is_refused_even_if_the_pa_dropped_on_screen(monkeypatch):
    # 01/11 (journal 112451, Eldieso) : Tir Éloigné cliqué pendant l'animation de Tir Puissant, ignoré par le jeu ;
    # cru parti d'après les PA de Tir Puissant baissés à l'écran : +4 de portée supposés, 5 refus au tour suivant.
    from bot.brain import combat
    from bot.brain.tactics import CombatSpell

    monkeypatch.setattr(combat, "NET_REFUSED", 0.05)
    far = CombatSpell(10, "tir_eloigne", "Tir Eloigné", 2, (0, 0), range_bonus=4, bonus_turns=3, role="boost_portee")
    ai = ai_with(fighting_feed(f"GTS{ME}|29000|1"))
    monkeypatch.setattr(ai, "_was_cast", lambda before, after, spell: True)  # PA baissés à l'écran
    ai._clicked_at = time.monotonic()
    before = ai.game.look()
    assert ai._confirm_cast(before, RED, far) is not None  # pas parti
    assert ai._turn_stats().missed_clicks == 1


def test_after_a_kill_the_move_is_awaited_longer_without_quick_reclick(caplog):
    # 01/11 (journal 113029, Waldarch) : Oeil de Taupe tue deux monstres ; le jeu garde le clic de déplacement pendant
    # leur mort et l'envoie 4,5 s après. Pas de reclic à 0,6 s : l'annonce est attendue (DEATH_MOVE_WAIT).
    from bot.brain import combat

    feed = fighting_feed(f"GTS{ME}|29000|1")
    ai = ai_with(feed)
    ai._served_at = time.monotonic()
    feed.feed("GA;103;100;-1")  # mort annoncée avec mon dernier sort
    calls = []
    feed.wait_for = lambda names, after, timeout: calls.append(timeout)
    from bot.perception.fighters import Fighter

    me = Fighter(RED, *ai.game.look().grid.center((14, 20)), (14, 20))
    ai._walk_clicked_at = time.monotonic()
    ai._wait_arrival(me, (15, 19), 1, quick=True)
    assert calls == [combat.DEATH_MOVE_WAIT]
    # 01/11 (journal 144405, Pesopepe) : un déplacement annoncé depuis la mort, l'animation est finie : attente courte.
    feed.moved_at = time.monotonic()
    ai._wait_arrival(me, (15, 19), 1, quick=True)
    assert calls[-1] == combat.NET_MOVE_SILENT


def test_a_critical_failure_gives_no_range_bonus():
    # 01/11 (journal 122800, Eldieso) : Tir Éloigné en échec critique (GA;302), +4 de portée comptés quand même,
    # 3 sorts refusés hors de portée.
    from bot.brain.tactics import CombatSpell, SpellUse

    far = CombatSpell(10, "tir_eloigne", "Tir Eloigné", 2, (0, 0), range_bonus=4, bonus_turns=3, role="boost_portee")
    use = SpellUse()
    use.new_turn(6)
    use.record(far, (0, 0), failed=True)
    assert use.range_bonus == 0 and use.pa == 4 and use.casts_this_turn["tir_eloigne"] == 1
    use.record(far, (0, 0))
    assert use.range_bonus == 4
