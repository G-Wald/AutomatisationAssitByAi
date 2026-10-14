"""Attentes en combat (timing.wait_mode) : le mode « fixe » garde ses pauses d'avant ; le mode « condition » voit le
résultat d'un geste dès la première capture qui le montre ; les deux concluent pareil (sort parti, refusé, pas
bougé...). Faux jeu à horloge simulée : chaque geste programme ce que l'écran montrera, et quand."""

import logging
import re

import pytest

from bot.actions.stop import BotStopped
from bot.brain.combat import Cast, CombatAI, CombatSettings, TurnStats
from bot.brain.tactics import CombatSpell
from bot.config import load_settings
from bot.debug.recorder import Recorder
from bot.perception.colors import RANGE_BLUE, RED, near
from bot.perception.fighters import Fighter
from bot.perception.frame import Frame
from bot.perception.grid import distance
from bot.perception.sight import Terrain
from bot.state.scene import read_scene
from bot.team.member import tag_logs
from conftest import load_frame
from test_brain import FakeGame, clicks, enemy_in_range, me_and_enemy, presses, spent_pa, with_forbidden_cursor_on

MODES = ("fixe", "condition")


class ScriptedGame(FakeGame):
    """Faux jeu dont l'écran suit l'horloge simulée : un geste (« press 3 », « click », « move ») déclenche la
    prochaine réaction prévue pour lui, des captures affichées chacune tant de secondes après le geste. Une capture
    ne coûte pas de temps : seules les attentes en prennent."""

    def __init__(self, screen: Frame) -> None:
        super().__init__(screen)
        self.shown: list[tuple[float, Frame]] = [(0.0, screen)]
        self.reactions: dict[str, list[list[tuple[float, Frame]]]] = {}
        self.looks = 0

    def on(self, gesture: str, *shown: tuple[float, Frame]) -> "ScriptedGame":
        self.reactions.setdefault(gesture, []).append(list(shown))
        return self

    def _react(self, gesture: str) -> None:
        pending = self.reactions.get(gesture)
        if pending:
            self.shown += [(self.clock + delay, frame) for delay, frame in pending.pop(0)]

    def look(self, park=True):
        self.parks.append(park)
        self.looks += 1
        return [frame for at, frame in sorted(self.shown, key=lambda s: s[0]) if at <= self.clock + 1e-9][-1]

    def move(self, point, spread=(0, 0)):
        cursor = super().move(point, spread)
        self._react("move")
        return cursor

    def click(self, point=None, spread=(0, 0), button="left"):
        super().click(point, spread, button)
        self._react("click" if button == "left" else f"click_{button}")

    def press(self, key):
        super().press(key)
        self._react(f"press {key}")


def without_range(frame: Frame) -> Frame:
    """Même capture, sans les cases bleues de portée (sort pas encore sélectionné)."""
    image = frame.image.copy()
    x0, y0, x1, y1 = frame.layout.map_box
    zone = image[y0:y1, x0:x1]
    zone[near(zone, RANGE_BLUE, 25)] = (128, 128, 128)
    return Frame.of(image)


SHOWN = load_frame("combat_portee_sort")  # portée affichée
HIDDEN = without_range(SHOWN)
SPENT = spent_pa(SHOWN)  # PA changés : le sort est parti
MY_TURN, ENEMY_TURN = load_frame("combat_mon_tour"), load_frame("combat_tour_adversaire")


def ai_in(mode: str, game: FakeGame, **settings) -> CombatAI:
    return CombatAI(game, CombatSettings(wait_mode=mode, **settings))


def cast_on_enemy(mode: str, script, **settings) -> tuple[Cast, ScriptedGame]:
    """Sort sur un ennemi à portée ; `script(jeu, cible)` dit comment l'écran réagit aux gestes."""
    me, _ = me_and_enemy(SHOWN)
    target = enemy_in_range(SHOWN)
    game = ScriptedGame(HIDDEN)
    script(game, target)
    return ai_in(mode, game, **settings)._cast(HIDDEN, me, target, [me, target], RED), game


def walk(mode: str, monkeypatch, *shown: tuple[float, str]) -> tuple[int, ScriptedGame]:
    """Déplacement de 3 cases ; `shown` : (délai après le clic, où est alors mon cercle : "arrivé" ou "ailleurs")."""
    me, enemy = me_and_enemy(MY_TURN)
    cells = sorted(MY_TURN.grid.cells())
    best = next(c for c in cells if distance(c, me.cell) == 3)
    other = next(c for c in cells if distance(c, me.cell) == 2)
    frames = {"arrivé": Frame.of(MY_TURN.image.copy()), "ailleurs": Frame.of(MY_TURN.image.copy())}
    where = {id(MY_TURN): me.cell, id(frames["arrivé"]): best, id(frames["ailleurs"]): other}
    monkeypatch.setattr("bot.brain.combat.find_fighters",
                        lambda frame: [Fighter(RED, *frame.grid.center(where[id(frame)]), where[id(frame)]), enemy])
    game = ScriptedGame(MY_TURN).on("click", *[(delay, frames[name]) for delay, name in shown])
    return ai_in(mode, game)._walk(MY_TURN, me, best, [me, enemy], {best}, "destination", 3), game


def gestures(game: FakeGame) -> list[tuple]:
    """Touches et clics, dans l'ordre (le survol de la cible avant chaque clic se refait à l'identique)."""
    return [a for a in game.actions if a[0] != "move"]


def summaries(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Tour : ")]


# --- Game.wait_until ---------------------------------------------------------------------------------------------


def test_wait_until_returns_the_first_frame_that_shows_the_condition():
    game = FakeGame(MY_TURN, MY_TURN, ENEMY_TURN)
    assert game.wait_until(lambda frame: frame is ENEMY_TURN, timeout=1.0) is ENEMY_TURN
    assert len(game.parks) == 3 and game.polled == pytest.approx([0.1, 0.1]) and game.slept == []


def test_wait_until_gives_up_at_the_deadline_with_a_last_look():
    game = FakeGame(MY_TURN)
    assert game.wait_until(lambda frame: False, timeout=0.25, park=False) is None
    assert game.polled == pytest.approx([0.1, 0.1, 0.05])  # dernière capture à l'échéance, pas après
    assert game.parks == [False] * 4


def test_wait_until_without_delay_looks_once():
    game = FakeGame(MY_TURN)
    assert game.wait_until(lambda frame: False, timeout=0) is None
    assert len(game.parks) == 1 and game.polled == []


def test_wait_until_listens_to_the_stop_key():
    class Stopped:
        def sleep(self, seconds):
            raise BotStopped("touche F12")

    game = FakeGame(MY_TURN)
    game.control = Stopped()  # StopControl : F12 pressée pendant l'attente
    with pytest.raises(BotStopped):
        game.wait_until(lambda frame: False, timeout=5)
    assert len(game.parks) == 1


# --- Réglage -----------------------------------------------------------------------------------------------------


def test_wait_mode_setting():
    assert CombatSettings().wait_mode == "fixe"
    assert CombatSettings.from_settings(load_settings()).wait_mode == "fixe"  # livré : comportement validé
    assert CombatSettings.from_settings({"timing": {"wait_mode": "Condition"}}).wait_mode == "condition"
    with pytest.raises(ValueError, match="wait_mode"):
        CombatSettings.from_settings({"timing": {"wait_mode": "rapide"}})


# --- Mode fixe : les pauses d'avant --------------------------------------------------------------------------------


def test_fixed_mode_cast_keeps_its_pauses():
    result, game = cast_on_enemy("fixe", lambda g, t: g.on("press 3", (0.05, SHOWN)).on("click", (0.8, SPENT)))
    assert result is Cast.DONE
    # Portée 0,15 s après la touche, curseur 0,25 s après le survol, after_cast, puis les PA toutes les 0,15 s.
    assert game.slept == pytest.approx([0.15, 0.25, 0.5, 0.15, 0.15]) and game.polled == []


def test_self_cast_after_cast_pause_only_in_fixed_mode():
    me, _ = me_and_enemy(MY_TURN)
    boost = CombatSpell(6, "tir_eloigne", "Tir Éloigné", 2, (0, 0), range_bonus=1, bonus_turns=3, role="boost_portee")
    # PA changés sur la 2e capture après le clic droit.
    game = FakeGame(MY_TURN, spent_pa(MY_TURN))
    assert ai_in("fixe", game)._cast(MY_TURN, me, me, [me], RED, boost) is Cast.DONE
    assert game.slept == [0.5, 0.15] and game.polled == []
    game = FakeGame(MY_TURN, spent_pa(MY_TURN))
    assert ai_in("condition", game)._cast(MY_TURN, me, me, [me], RED, boost) is Cast.DONE
    assert game.slept == [] and game.polled == [0.1]


def test_fixed_mode_walk_keeps_its_pauses(monkeypatch):
    steps, game = walk("fixe", monkeypatch, (1.25, "arrivé"))
    assert steps == 3
    assert game.slept == pytest.approx([0.9, 0.2, 0.2]) and game.polled == []  # 0,3 s par case, puis toutes les 0,2 s


def test_fixed_mode_end_turn_keeps_its_pauses():
    game = ScriptedGame(MY_TURN).on("press f1", (0.3, ENEMY_TURN))
    ai_in("fixe", game, end_turn_key="f1")._end_turn(RED)
    assert game.slept == [0.25, 0.25] and game.polled == []


# --- Mode condition : dès la première capture qui le montre -------------------------------------------------------


def test_watched_range_seen_on_the_first_capture_that_shows_it():
    game = ScriptedGame(HIDDEN).on("press 3", (0.05, SHOWN))
    ai = ai_in("condition", game)
    assert ai._select_spell(HIDDEN, ai.spells[0]) is SHOWN
    assert game.looks == 2 and game.clock == pytest.approx(0.1) and game.slept == []
    # Portée lente à venir (0,3 s) : le mode fixe, qui regarde une fois à 0,15 s, y voit un refus.
    for mode, expected in (("fixe", None), ("condition", SHOWN)):
        ai = ai_in(mode, ScriptedGame(HIDDEN).on("press 3", (0.3, SHOWN)))
        assert ai._select_spell(HIDDEN, ai.spells[0]) is expected


def test_watched_cast_confirmed_without_after_cast_pause():
    result, game = cast_on_enemy("condition", lambda g, t: g.on("press 3", (0.05, SHOWN)).on("click", (0.8, SPENT)))
    assert result is Cast.DONE and game.slept == []
    assert game.clock == pytest.approx(1.15)  # portée à 0,1 s, cible acceptée à 0,35 s, PA vus 0,8 s après le clic


def test_watched_arrival_seen_on_the_first_capture_that_shows_it(monkeypatch):
    steps, game = walk("condition", monkeypatch, (0.75, "arrivé"))
    assert steps == 3 and game.slept == []
    assert game.clock == pytest.approx(0.8) and game.looks == 9  # captures à 0, 0,1... 0,8 s (fixe : à 0,9 s)


def test_watched_turn_passed_seen_on_the_first_capture_that_shows_it():
    game = ScriptedGame(MY_TURN).on("press f1", (0.3, ENEMY_TURN))
    ai_in("condition", game, end_turn_key="f1")._end_turn(RED)
    assert presses(game) == ["f1"] and clicks(game) == []
    assert game.clock == pytest.approx(0.3) and game.looks == 4 and game.slept == []  # fixe : 0,5 s


# --- Mêmes conclusions dans les deux modes ------------------------------------------------------------------------


def no_reaction(game, target):
    pass


@pytest.mark.parametrize("script, expected", [
    (lambda g, t: g.on("press 3", (0.05, SHOWN)).on("click", (0.8, SPENT)), Cast.DONE),
    (lambda g, t: g.on("press 3", (0.05, SHOWN)).on("click").on("click", (0.8, SPENT)), Cast.DONE),  # clic refait
    (no_reaction, Cast.NO_SPELL),  # sort non sélectionnable
    (lambda g, t: g.on("press 3", (0.05, SHOWN)).on("move", (0.05, with_forbidden_cursor_on(SHOWN, t)))
     .on("press escape", (0.05, HIDDEN)), Cast.OUT_OF_REACH),  # pas de ligne de vue
    (lambda g, t: g.on("press 3", (0.05, SHOWN)).on("move", (0.05, with_forbidden_cursor_on(SHOWN, t))),
     Cast.STUCK),  # Échap sans effet : sort resté sélectionné
], ids=["parti", "clic sans effet", "non sélectionnable", "pas de ligne de vue", "resté sélectionné"])
def test_same_cast_conclusion_in_both_modes(script, expected):
    (fixed, fixed_game), (watched, watched_game) = (cast_on_enemy(mode, script, cast_confirm=0.45) for mode in MODES)
    assert fixed is watched is expected
    assert gestures(fixed_game) == gestures(watched_game)
    if expected is not Cast.NO_SPELL:  # sort refusé : portée guettée 0,6 s au lieu d'une capture à 0,15 s
        assert watched_game.clock < fixed_game.clock


def test_same_self_cast_fallback_in_both_modes():
    # 01/10 : clic droit ignoré par le jeu ; le raccourci puis le clic sur moi le lancent, gardés pour la session.
    me, _ = me_and_enemy(MY_TURN)
    boost = CombatSpell(6, "tir_eloigne", "Tir Éloigné", 2, (0, 0), range_bonus=1, bonus_turns=3, role="boost_portee")
    games, kept = {}, {}
    for mode in MODES:
        game = games[mode] = ScriptedGame(MY_TURN).on("click_right").on("click", (0.6, spent_pa(MY_TURN)))
        ai = ai_in(mode, game, cast_confirm=0.45)
        assert ai._cast(MY_TURN, me, me, [me], RED, boost) is Cast.DONE
        kept[mode] = ai._self_cast
    assert gestures(games["fixe"]) == gestures(games["condition"])
    assert [a[0] for a in gestures(games["fixe"])] == ["click_right", "press", "click"]
    assert kept["fixe"] == kept["condition"] == "raccourci + clic sur moi"
    assert games["condition"].clock < games["fixe"].clock


@pytest.mark.parametrize("shown, expected", [
    (((0.75, "arrivé"),), 3),
    (((0.5, "ailleurs"),), 2),  # arrêté en route à 2 cases : 2 PM comptés sur 3
    ((), 0),  # pas bougé
], ids=["arrivé", "ailleurs", "pas bougé"])
def test_same_walk_conclusion_in_both_modes(monkeypatch, shown, expected):
    (fixed, fixed_game), (watched, watched_game) = (walk(mode, monkeypatch, *shown) for mode in MODES)
    assert fixed == watched == expected
    assert gestures(fixed_game) == gestures(watched_game)
    assert watched_game.clock <= fixed_game.clock  # pas arrivé sur la case prévue : conclu à l'échéance, la même


@pytest.mark.parametrize("key_works", [True, False], ids=["F1", "F1 sans effet"])
def test_same_end_turn_in_both_modes(key_works):
    games = {}
    for mode in MODES:
        game = games[mode] = ScriptedGame(MY_TURN)
        if key_works:
            game.on("press f1", (0.3, ENEMY_TURN))
        game.on("click", (0.3, ENEMY_TURN))
        ai_in(mode, game, end_turn_key="f1")._end_turn(RED)
    assert gestures(games["fixe"]) == gestures(games["condition"])
    assert clicks(games["fixe"]) == ([] if key_works else [read_scene(MY_TURN).end_turn_button])  # sinon : le bouton
    assert games["condition"].clock < games["fixe"].clock


def test_same_turn_and_turn_summary_in_both_modes(monkeypatch, caplog):
    monkeypatch.setattr("bot.brain.combat.read_terrain", lambda frame, fighters: Terrain(set(), set(), set()))
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    games = {}
    for mode in MODES:
        games[mode] = ScriptedGame(MY_TURN).on("click", (0.3, ENEMY_TURN))  # le raccourci ne fait rien : sort refusé
        ai_in(mode, games[mode], spell_range=(1, 20)).play_turn(RED)
    assert gestures(games["fixe"]) == gestures(games["condition"])
    assert presses(games["fixe"]) == ["3"] and clicks(games["fixe"]) == [read_scene(MY_TURN).end_turn_button]
    lines = summaries(caplog)
    assert len(lines) == 2
    for line, mode in zip(lines, MODES):
        assert re.fullmatch(rf"Tour : \d+,\d s ; 0 sort, 0 déplacement, 1 refus ; attentes : {mode}", line)


# --- Synthèse du tour et heure du journal --------------------------------------------------------------------------


def test_turn_summary_line():
    stats = TurnStats(turn=4, started=10.0, tries=3, casts=2, moves=1)
    assert stats.describe(16.24, "condition") == "Tour : 6,2 s ; 2 sorts, 1 déplacement, 1 refus ; attentes : condition"
    stats = TurnStats(turn=4, started=0.0, tries=1, casts=1, missed_clicks=1, moves=2, stuck_moves=1)
    assert stats.describe(3.0, "fixe") == ("Tour : 3,0 s ; 1 sort, 2 déplacements, 0 refus, 1 clic sans effet, "
                                           "1 déplacement sans bouger ; attentes : fixe")


def test_turn_counts_do_not_leak_into_the_next_turn(caplog):
    caplog.set_level(logging.INFO, logger="bot.brain.combat")
    me, _ = me_and_enemy(SHOWN)
    target = enemy_in_range(SHOWN)
    ai = ai_in("fixe", ScriptedGame(HIDDEN))
    ai._use.turn = 1
    assert ai._cast(HIDDEN, me, target, [me, target], RED) is Cast.NO_SPELL  # tour fini sans fin de tour (timer)
    ai._use.turn = 2
    ai._end_turn(RED)
    (line,) = summaries(caplog)
    assert "0 sort, 0 déplacement, 0 refus" in line


def test_log_time_to_the_millisecond(tmp_path):
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    record = logging.LogRecord("bot", logging.INFO, __file__, 1, "Mon tour", None, None)
    try:
        Recorder(tmp_path, max_images=0).setup_logging()
        solo = [handler.format(record) for handler in root.handlers]
        tag_logs(lambda: "Jesuisleking")
        team = [handler.format(record) for handler in root.handlers if handler.filter(record)]  # filtre : le nom
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:] = saved
        root.setLevel(level)
    assert solo and all(re.fullmatch(r"\d\d:\d\d:\d\d\.\d{3} INFO    Mon tour", line) for line in solo)
    assert team and all(re.fullmatch(r"\d\d:\d\d:\d\d\.\d{3} INFO    \[Jesuisleking\] Mon tour", line) for line in team)
