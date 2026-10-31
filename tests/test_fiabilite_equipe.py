"""Fiabilité de la boucle d'équipe (25/10) : surveillance du progrès, repos, combat sans l'équipe, captures.

Chaque test part d'un constat dans les journaux du 25/10 (logs/20261025_*), cité dans le code testé."""

import logging
import time

import numpy as np

import bot.team.loop as team_loop
from bot.debug.recorder import PROBLEM_QUOTA, QUOTA_WINDOW, ROUTINE_QUOTA, Recorder, prune_sessions
from bot.perception.frame import Frame
from bot.team.recovery import SOFT_PROGRESS_LIMIT, STALL_DIAGNOSE
from conftest import load_frame
from test_recovery import with_a_outline
from test_team import FakeAI, Screen, events, member, team_bot


def stalled(bot, seconds, real=None):
    """Dernier progrès il y a `seconds` s ; dernier vrai progrès il y a `real` s (le même par défaut)."""
    now = time.monotonic()
    bot._progress_at = now - seconds
    bot._real_progress_at = now - (seconds if real is None else real)


class FakeJourney:
    """Trajet d'un suiveur perdu (brain/travel.Journey) : seulement ses compteurs."""

    def __init__(self) -> None:
        self.crossed = self.failures = self.reroutes = 0


# --- Surveillance du progrès ---------------------------------------------------------------------------------------


def test_progress_counted_from_the_start_of_the_loop_not_from_the_bot_creation():
    # 25/10 (journal 175311) : 29 s de groupe et de reconnexion comptées comme du temps sans progrès.
    screen = Screen()
    bot = team_bot(member("A", screen, with_a_outline()))
    stalled(bot, STALL_DIAGNOSE + 30)
    bot.finished = True  # run() : progrès noté, puis la boucle s'arrête aussitôt
    bot.run()
    assert time.monotonic() - bot._progress_at < 5 and time.monotonic() - bot._real_progress_at < 5
    assert not bot.watchdog.check()


def test_each_new_attempt_of_a_lost_follower_is_a_soft_progress():
    # 25/10 (journal 180222) : 7 suiveurs essayant chacun plusieurs soleils, aucune carte changée : pause à 280 s.
    screen = Screen()
    bot = team_bot(member("A", screen, with_a_outline()), member("B", screen, with_a_outline()))
    journey = FakeJourney()
    bot._rejoin["B"] = journey
    stalled(bot, STALL_DIAGNOSE + 30)
    bot._note_rejoin_attempts()  # trajet commencé
    assert time.monotonic() - bot._progress_at < 5
    assert time.monotonic() - bot._real_progress_at > STALL_DIAGNOSE  # pas un vrai progrès
    assert not bot.watchdog.check() and events(screen, "press") == []  # pas de diagnostic

    stalled(bot, STALL_DIAGNOSE + 30)
    bot._note_rejoin_attempts()  # rien de nouveau : pas de progrès
    assert time.monotonic() - bot._progress_at > STALL_DIAGNOSE
    journey.failures += 1  # une sortie essayée, ratée
    bot._note_rejoin_attempts()
    assert time.monotonic() - bot._progress_at < 5
    journey.reroutes += 1  # nouvel itinéraire
    stalled(bot, 60)
    bot._note_rejoin_attempts()
    assert time.monotonic() - bot._progress_at < 5


def test_attempts_without_any_result_still_reach_the_last_resort():
    # Les essais repoussent la surveillance, pas au-delà de SOFT_PROGRESS_LIMIT s depuis le dernier vrai progrès.
    screen = Screen()
    a, b = member("A", screen, with_a_outline()), member("B", screen, with_a_outline())
    bot = team_bot(a, b)
    stalled(bot, 10, real=SOFT_PROGRESS_LIMIT + 1)
    assert bot.watchdog.check()
    assert bot.finished and bot._stop_reason == "aucun progrès"  # sans touche de pause : arrêt


def test_soft_progress_below_the_limit_keeps_the_watchdog_quiet():
    screen = Screen()
    bot = team_bot(member("A", screen, with_a_outline()))
    stalled(bot, 10, real=SOFT_PROGRESS_LIMIT - 60)
    assert not bot.watchdog.check() and not bot.finished


def test_step_notes_the_attempts_before_the_watchdog(monkeypatch):
    screen = Screen()
    bot = team_bot(member("A", screen, with_a_outline()), member("B", screen, with_a_outline()))
    bot._rejoin["B"] = FakeJourney()
    bot._round_due = False
    checked = []
    monkeypatch.setattr(bot.watchdog, "check", lambda: checked.append(time.monotonic() - bot._progress_at) or True)
    stalled(bot, STALL_DIAGNOSE + 30)
    bot.step()
    assert checked and checked[0] < 5


# --- Combat sans l'équipe ------------------------------------------------------------------------------------------


class ReportAI(FakeAI):
    def fight_report(self):
        return {}


def fight_with(monkeypatch, joined: int):
    screen = Screen()
    host = member("A", screen, load_frame("preparation"), ai=ReportAI)
    others = [member(n, screen, load_frame("hors_combat"), ai=ReportAI) for n in ("B", "C")]
    bot = team_bot(host, *others)
    monkeypatch.setattr(team_loop, "write_fight_report", lambda *args: None)
    monkeypatch.setattr(bot, "_placement", lambda h: [h] + others[:joined])
    monkeypatch.setattr(bot, "_turns", lambda fighters: None)
    bot._fight(host)
    return bot


def test_nobody_joined_the_fight_ends_the_session_after_it(monkeypatch, caplog):
    # 25/10 (journal 171736) : 2 combats sur 3 sans aucun suiveur ; au second, Fourboun est mort seul.
    with caplog.at_level(logging.WARNING):
        bot = fight_with(monkeypatch, joined=0)
    assert "personne n'a rejoint le combat de A" in bot._stop_reason
    assert not bot.finished and bot._round_due  # le combat est fini ; arrêt au tour des fenêtres qui suit
    assert any("il se bat seul" in r.getMessage() for r in caplog.records)


def test_a_fight_with_part_of_the_team_goes_on(monkeypatch):
    # 25/10 (journal 155334) : Elseiso seul resté dehors, 7 combats sur 15, aucune mort : on continue.
    bot = fight_with(monkeypatch, joined=1)
    assert bot._stop_reason == ""


# --- Repos ---------------------------------------------------------------------------------------------------------


def copies(name: str, count: int) -> list[Frame]:
    frame = load_frame(name)
    return [Frame.of(frame.image.copy()) for _ in range(count)]


def test_others_below_the_target_sit_while_the_tired_one_rests(monkeypatch, caplog):
    # 25/10 (journal 181212) : 3 repos d'un seul personnage, 8,1 min sur 53, les 7 autres debout.
    a_low, a_up = copies("hors_combat", 2)
    (b_mid,) = copies("hors_combat", 1)
    (c_full,) = copies("hors_combat", 1)
    ratios = {id(a_low): 0.4, id(a_up): 0.95, id(b_mid): 0.6, id(c_full): 0.97}
    monkeypatch.setattr(team_loop, "hp_ratio", lambda frame: ratios.get(id(frame), 1.0))
    screen = Screen()
    a, b, c = member("A", screen, a_low, a_up), member("B", screen, b_mid), member("C", screen, c_full)
    bot = team_bot(a, b, c)
    bot._hp = {"A": 0.4, "B": 0.6, "C": 0.97}
    bot._tired = [a]
    with caplog.at_level(logging.INFO):
        bot._rest()
    clicks = [(e[0], e[2]) for e in events(screen, "click")]
    assert [name for name, _ in clicks] == ["A", "B"]  # C, au-dessus de rest.until, ne s'assoit pas
    assert all(abs(x - 663) <= 8 and abs(y - 795) <= 8 for _, (x, y) in clicks)  # le tabouret
    assert any("je m'assois aussi" in r.getMessage() for r in caplog.records)
    assert any("je repars" in r.getMessage() for r in caplog.records)  # repos fini quand A est remonté
    bot._sit_while_waiting([a])  # même fin de combat : B est déjà assis, pas de second clic
    assert len(events(screen, "click")) == 2


def test_nobody_else_sits_without_a_rest(monkeypatch):
    (a_ok,) = copies("hors_combat", 1)
    monkeypatch.setattr(team_loop, "hp_ratio", lambda frame: 0.6)  # relue au-dessus de rest.below : pas de repos
    screen = Screen()
    a, b = member("A", screen, a_ok), member("B", screen, load_frame("hors_combat"))
    bot = team_bot(a, b)
    bot._hp, bot._tired = {"A": 0.45, "B": 0.6}, [a]
    bot._rest()
    assert events(screen, "click") == []


def test_hp_read_at_zero_by_everyone_is_a_hidden_heart_not_a_rest(monkeypatch, caplog):
    # 26/10 (journal 181017, combat 6) : résultat resté ouvert par-dessus le cœur, 0 % lu chez les 8, repos sans fin.
    monkeypatch.setattr(team_loop, "hp_ratio", lambda frame: 0.0)
    screen = Screen()
    bot = team_bot(member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("hors_combat")),
                   engage=False)
    with caplog.at_level(logging.WARNING):
        bot._round()
    assert bot._tired == [] and bot._hp == {}
    assert any("cœur est caché" in r.getMessage() for r in caplog.records)
    monkeypatch.setattr(team_loop, "hp_ratio", lambda frame: 0.0 if frame is a_frame else 1.0)
    a_frame = load_frame("hors_combat")
    bot = team_bot(member("A", screen, a_frame), member("B", screen, load_frame("hors_combat")), engage=False)
    bot._round()
    assert [m.name for m in bot._tired] == ["A"]  # un seul à 0 : lu tel quel


def test_team_hp_written_after_a_fight(caplog):
    screen = Screen()
    bot = team_bot(member("A", screen, load_frame("hors_combat")), member("B", screen, load_frame("coeur_43pv"),
                                                                          load_frame("hors_combat")), engage=False)
    bot._hp_report_due = True
    with caplog.at_level(logging.INFO):
        bot._round()
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Vie après le combat")]
    assert "A 100 %" in line and "B " in line and not bot._hp_report_due
    with caplog.at_level(logging.INFO):
        caplog.clear()
        bot._round()  # une fois par combat
    assert not [r for r in caplog.records if r.getMessage().startswith("Vie après le combat")]


# --- Captures ------------------------------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_captures_spread_over_the_session_by_label(tmp_path):
    # 25/10 : les 300 captures courantes prises en 8 à 13 min, puis plus aucune (journal 181212 : 40 min sans).
    clock = Clock()
    recorder = Recorder(tmp_path, max_images=300, clock=clock)
    image = np.zeros((4, 4, 3), np.uint8)
    kept = [recorder.save(image, "Walitcoin_tour") for _ in range(5)]
    assert sum(p is not None for p in kept) == ROUTINE_QUOTA
    assert recorder.save(image, "Pesopepe_tour") is not None  # autre personnage : son propre quota
    problems = [recorder.save(image, "Fourboun_menu_attaquer_absent", lossless=True) for _ in range(6)]
    assert sum(p is not None for p in problems) == PROBLEM_QUOTA
    clock.now += QUOTA_WINDOW / 2
    assert recorder.save(image, "Walitcoin_tour") is None
    clock.now += QUOTA_WINDOW / 2 + 1
    assert recorder.save(image, "Walitcoin_tour") is not None  # 10 min plus tard : de nouveau
    assert recorder.save(image, "Fourboun_menu_attaquer_absent", lossless=True) is not None


def test_session_folder_created_only_when_something_is_written(tmp_path):
    recorder = Recorder(tmp_path)
    assert not recorder.dir.exists()  # outil sans capture : aucun dossier vide (25/10 : 10 entre 19:35 et 19:39)
    path = recorder.save(np.zeros((4, 4, 3), np.uint8), "essai")
    assert path is not None and path.exists() and path.parent == recorder.dir


def test_empty_session_folders_removed_without_counting(tmp_path):
    real = []
    for i, name in enumerate(["20261025_150000", "20261025_160000", "20261025_170000"]):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "bot.log").write_text("x")
        real.append(folder)
    empty = [tmp_path / f"20261025_1935{i:02d}" for i in range(5)]
    for folder in empty:
        folder.mkdir()
    current = tmp_path / "20261025_201109"
    current.mkdir()
    removed = prune_sessions(tmp_path, keep=4, max_total_mb=100, current=current)
    assert sorted(removed) == sorted(empty)  # les 3 vraies sessions et la courante restent
    assert all(f.exists() for f in real) and current.exists()
