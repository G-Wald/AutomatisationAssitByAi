"""Notifications de Windows (bot/team/signals.py) : fausses notifications et faux centre de notifications, aucun
accès à l'API de Windows (ni WinRT, ni registre)."""

import functools
import json
import logging
import time
from datetime import datetime, timezone

import pytest

from bot.perception.colors import RED
from bot.state.scene import read_scene
from bot.team import signals as signals_module
from bot.team.loop import EARLY_START, HINT_PATIENCE
from bot.perception.timeline import current_portrait
from bot.team.signals import (DOFUS_APP, NOTIFICATIONS, PACKAGES, RECORD_NAME, TIMELINE, TURN_WORDS, Toast,
                              TurnSignals, character_in, settings_problems, turn_signal, turn_word_in, turn_words)
from conftest import load_frame
from test_team import Screen, events, member, team_bot, without_timer

T0 = 1_790_000_000.0  # heure de départ des tests d'enregistrement (en 2026, comme time.time)


class Clock:
    """Heure réglée à la main (secondes, comme time.time)."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeReader:
    """Faux centre de notifications : rend les relevés prévus l'un après l'autre (puis rien) et compte les relevés."""

    def __init__(self, *batches: list[Toast], refused: str | None = None) -> None:
        self.batches = list(batches)
        self.refused = refused
        self.reads = 0
        self.closed = False

    def access(self):
        return self.refused

    def read(self):
        self.reads += 1
        return self.batches.pop(0) if self.batches else []

    def close(self):
        self.closed = True


def toast(note_id: int, *texts: str, created: float, app: str = DOFUS_APP) -> Toast:
    return Toast(note_id, app, texts, created)


def turn(note_id: int, name: str, created: float) -> Toast:
    """Notification du début du tour de `name` (texte du jeu, vu le 03/10)."""
    return toast(note_id, f"{name} - Dofus Retro v1.49.5", f"C'est à {name} de jouer", created=created)


def signaled(name: str, names=("A", "B", "C")) -> TurnSignals:
    """Écouteur (sans fil d'écoute) qui a reçu la notification du tour de `name`."""
    signals = TurnSignals(names)
    assert signals.feed([turn(1, name, created=time.time())]) == [name]
    return signals


def warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def infos(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]


def records(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).astimezone().isoformat(timespec="milliseconds")


# Notifications lues


def test_character_named_in_the_notification():
    names = ["Jesuisleking", "Imthebest", "Élodie"]
    assert character_in(("Jesuisleking - Dofus Retro v1.49.5", "C'est à toi de jouer !"), names) == "Jesuisleking"
    assert character_in(("Dofus Retro", "IMTHEBEST, à toi de jouer"), names) == "Imthebest"
    assert character_in(("Dofus Retro", "elodie, à toi"), names) == "Élodie"  # sans accents ni majuscules
    assert character_in(("Dofus Retro", "À toi de jouer"), names) is None
    assert character_in(("Dofus Retro", "Jesuisleking invite Imthebest"), names) is None  # deux noms : on ne devine pas
    # Le titre d'abord : le personnage de la fenêtre, pas celui qui l'invite.
    assert character_in(("Imthebest - Dofus Retro", "Jesuisleking t'invite dans son groupe"), names) == "Imthebest"
    assert character_in(("Dofus Retro", "Imthebestiole"), names) is None  # nom entier seulement


def test_turn_word_in_the_notification():
    title, (words,) = "Walitcoin - Dofus Retro v1.49.5", TURN_WORDS
    assert turn_word_in((title, "C'est à Walitcoin de jouer"), TURN_WORDS, "Walitcoin") == words  # texte du jeu, 03/10
    assert turn_word_in((title, "C’EST A walitcoin DE JOUER"), TURN_WORDS, "Walitcoin") == words  # accents, apostrophe
    assert turn_word_in((title, "C'est à\nWalitcoin de  jouer"), TURN_WORDS, "Walitcoin") == words  # espaces
    assert turn_word_in((title, "C'est à Imthebest de jouer"), TURN_WORDS, "Walitcoin") is None  # pas le nommé
    assert turn_word_in((title, "C'est à Walitcoin de jouer"), TURN_WORDS) is None  # {nom} : il faut un personnage
    assert turn_word_in((title, "Succès déverrouillé : Bouftous d'Incarnam."), TURN_WORDS, "Walitcoin") is None  # 03/10
    assert turn_word_in(("Imthebest - Dofus Retro", "Jesuisleking t'invite dans son groupe"), TURN_WORDS,
                        "Imthebest") is None
    assert turn_word_in(("Dofus Retro", "C'est ton tour"), ["tour"]) == "tour"  # sans {nom}
    assert turn_word_in(("Dofus Retro", "Tournoi", "Retour au zaap"), ["tour"]) is None  # mot entier seulement


def test_turn_words_setting():
    assert turn_words({}) == TURN_WORDS == ("c'est à {nom} de jouer",)  # texte du jeu, vu le 03/10
    assert turn_words({"team": {"turn_words": "début du tour"}}) == ("début du tour",)
    assert turn_words({"team": {"turn_words": [" Tour ", "", "à toi"]}}) == ("Tour", "à toi")
    with pytest.raises(ValueError):
        turn_words({"team": {"turn_words": []}})  # aucune notification ne serait un tour


def test_only_turn_notifications_naming_a_character_are_signals(caplog):
    caplog.set_level(logging.INFO)
    clock = Clock(1000.0)
    signals = TurnSignals(["A", "B"], clock=clock)
    clock.now = 1010.0
    assert signals.feed([turn(1, "B", created=1005.0)]) == ["B"]
    # Le même personnage, mais un autre sujet : pas son tour.
    assert signals.feed([toast(2, "B - Dofus Retro v1.49.5", "Imthebest t'invite dans son groupe",
                               created=1006.0)]) == []
    assert signals.take(["A", "B"]) == "B" and signals.take(["A", "B"]) is None
    (line,) = [m for m in infos(caplog) if "sans mot de tour" in m]  # au journal, une fois, avec son texte
    assert "B, sans mot de tour : ignorée" in line and "t'invite dans son groupe" in line
    assert signals.feed([toast(2, "B - Dofus Retro v1.49.5", "Imthebest t'invite dans son groupe",
                               created=1006.0)]) == []
    assert len([m for m in infos(caplog) if "sans mot de tour" in m]) == 1  # déjà vue : pas répétée


def test_turn_words_are_configurable():
    clock = Clock(1000.0)
    signals = TurnSignals(["A"], clock=clock, words=["joue maintenant"])
    clock.now = 1010.0
    assert signals.feed([turn(1, "A", created=1005.0)]) == []  # « c'est à A de jouer » n'en est plus un
    assert signals.feed([toast(2, "A - Dofus Retro", "A JOUE MAINTENANT", created=1006.0)]) == ["A"]


def test_only_new_dofus_notifications_naming_a_character_are_kept():
    clock = Clock(1000.0)
    signals = TurnSignals(["A", "B"], clock=clock)
    clock.now = 1010.0
    found = signals.feed([
        toast(1, "B - Dofus Retro", "C'est à B de jouer", created=1005.0, app="MSTeams_8wekyb3d8bbwe!MSTeams"),
        toast(2, "B - Dofus Retro", "C'est à B de jouer", created=995.0),  # avant le démarrage du bot
        toast(3, "Dofus Retro", "Nouvel échange", created=1006.0),  # aucun personnage de l'équipe
        toast(4, "B - Dofus Retro", "C'est à B de jouer", created=1007.0),
    ])
    assert found == ["B"]
    assert signals.feed([toast(4, "B - Dofus Retro", "C'est à B de jouer", created=1007.0)]) == []  # déjà vue (même Id)
    assert signals.take(["A", "B"]) == "B"


def test_take_gives_the_last_signaled_character_once():
    clock = Clock(1000.0)
    signals = TurnSignals(["A", "B", "C"], clock=clock)
    signals.feed([turn(1, "A", created=1002.0), turn(2, "B", created=1003.0)])
    assert signals.take(["A", "B"]) == "B"  # le plus récent
    assert signals.take(["A", "B"]) is None  # consommé
    signals.feed([turn(3, "C", created=1004.0)])
    assert signals.take(["A", "B"]) is None  # C n'est pas de ce combat
    assert signals.take(["A", "B", "C"]) is None  # et le signal est consommé quand même


def test_notifications_from_before_the_fight_or_the_last_turn_are_ignored():
    clock = Clock(1000.0)
    signals = TurnSignals(["A", "B"], clock=clock)
    clock.now = 1100.0
    signals.new_fight()
    assert signals.feed([turn(1, "A", created=1090.0)]) == []  # hors combat
    # Premier tour : il a pu commencer juste avant que le TeamBot ne voie la timeline.
    assert signals.feed([turn(2, "A", created=1098.0)]) == ["A"]
    signals.playing("A")
    assert not signals.enabled_for_polling and signals.take(["A", "B"]) is None  # oublié : A joue
    clock.now = 1120.0
    signals.feed([turn(3, "A", created=1119.0)])  # la sienne, lue en retard (relevé déjà en cours)
    signals.feed([turn(4, "B", created=1112.0)])  # tour de B, commencé à la fin de celui de A
    signals.played("A")
    assert signals.take(["A", "B"]) == "B"
    assert signals.feed([turn(5, "A", created=1110.0)]) == []  # d'avant la fin de son tour
    clock.now = 1150.0
    assert signals.feed([turn(6, "A", created=1140.0)]) == ["A"]  # son tour suivant


# Relevé : seulement pendant l'attente d'un tour


def test_polling_only_while_enabled():
    clock = Clock(1000.0)
    reader = FakeReader([turn(1, "B", created=1001.0)])
    signals = TurnSignals(["A", "B"], clock=clock)  # sans fichier d'enregistrement
    assert signals.poll(reader) == [] and reader.reads == 0  # hors combat : rien relevé
    signals.enabled_for_polling = True
    assert signals.poll(reader) == ["B"] and reader.reads == 1
    signals.playing("B")
    assert signals.poll(reader) == [] and reader.reads == 1  # pendant un tour : rien relevé


def test_listener_thread_polls_only_while_enabled():
    reader = FakeReader()
    signals = TurnSignals(["A"], interval=0.01, idle=0.05, reader=lambda: reader)
    assert signals.start(wait=2) and signals.active
    try:
        time.sleep(0.1)
        assert reader.reads == 0
        signals.enabled_for_polling = True
        deadline = time.monotonic() + 2
        while reader.reads < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert reader.reads >= 3
        signals.enabled_for_polling = False
        time.sleep(0.05)  # un relevé déjà commencé se termine
        before = reader.reads
        time.sleep(0.1)
        assert reader.reads == before
    finally:
        signals.stop()
    assert reader.closed and not signals.active


def test_read_failure_does_not_stop_the_listener(caplog):
    class Failing(FakeReader):
        def read(self):
            self.reads += 1
            raise OSError("centre de notifications indisponible")

    reader = Failing()
    signals = TurnSignals(["A"])
    signals.enabled_for_polling = True
    assert signals.poll(reader) == [] and signals.poll(reader) == []
    assert reader.reads == 2 and len(warnings(caplog)) == 1  # signalé une fois


# Enregistrement de toutes les notifications de Dofus


def test_every_dofus_notification_is_recorded_once(tmp_path):
    path = tmp_path / RECORD_NAME
    clock = Clock(T0)
    signals = TurnSignals(["A", "B"], clock=clock, record=path)
    clock.now = T0 + 10.25
    batch = [
        turn(1, "B", created=T0 + 5),
        toast(2, "B - Dofus Retro", "Nouvel échange", created=T0 + 6),
        toast(3, "Dofus Retro", "Message de Bob", created=T0 + 6.5),
        toast(4, "Dofus Retro", "A invite B, c'est ton tour", created=T0 + 7),
        turn(5, "A", created=T0 - 30),  # avant le démarrage du bot
        toast(6, "Courrier", "Nouveau message de ta banque", created=T0 + 8,
              app="microsoft.windowscommunicationsapps_8wekyb3d8bbwe!microsoft.windowslive.mail"),
        toast(7, "A - Dofus Retro", "Info", created=0),  # heure de création illisible
    ]
    assert signals.feed(batch) == ["B"]
    assert signals.feed(batch) == []  # déjà vues
    lines = records(path)
    assert [r["id"] for r in lines] == [1, 2, 3, 4, 5, 7]  # une fois chacune ; pas celle d'une autre application
    assert [r["decision"] for r in lines] == ["signal", "sans mot de tour", "sans nom de l'équipe", "plusieurs noms",
                                              "ancienne", "sans mot de tour"]
    assert lines[0] == {"lue": iso(T0 + 10.25), "creee": iso(T0 + 5), "id": 1, "application": DOFUS_APP,
                        "textes": ["B - Dofus Retro v1.49.5", "C'est à B de jouer"], "personnage": "B", "noms": ["B"],
                        "mot_de_tour": TURN_WORDS[0], "decision": "signal", "releve_actif": True}
    assert lines[0]["lue"].endswith(".250" + lines[0]["lue"][-6:])  # à la milliseconde, avec le fuseau
    assert lines[1]["personnage"] == "B" and lines[1]["mot_de_tour"] is None
    assert lines[3]["personnage"] is None and lines[3]["noms"] == ["A", "B"] and lines[3]["mot_de_tour"] is None
    assert lines[5]["creee"] is None
    assert "Nouvel échange" in path.read_text(encoding="utf-8")  # UTF-8 lisible, sans é
    assert "banque" not in path.read_text(encoding="utf-8")


def test_read_while_polling_is_off_recorded_but_never_a_signal(tmp_path):
    path = tmp_path / RECORD_NAME
    clock = Clock(T0)
    reader = FakeReader([toast(1, "A - Dofus Retro", "B t'invite dans son groupe", created=T0 + 5),
                         turn(2, "B", created=T0 + 6)])  # hors combat : reste d'un ancien combat, ou texte trompeur
    signals = TurnSignals(["A", "B"], clock=clock, record=path)
    clock.now = T0 + 10
    assert signals.poll(reader) == [] and reader.reads == 1  # relevé coupé : lu pour l'enregistrement seulement
    assert signals.take(["A", "B"]) is None
    lines = records(path)
    assert [(r["id"], r["decision"], r["releve_actif"]) for r in lines] == [(1, "sans mot de tour", False),
                                                                            (2, "hors relevé", False)]
    clock.now = T0 + 60
    signals.new_fight()
    signals.enabled_for_polling = True
    assert signals.poll(reader) == [] and signals.take(["A", "B"]) is None  # d'avant le combat : jamais un signal
    assert len(records(path)) == 2  # pas écrites deux fois


def test_turn_read_while_polling_is_off_counts_at_the_next_active_poll(tmp_path):
    """Le tour de B commence à la fin de celui de A, quand le relevé est encore coupé : la notification de B, lue par le
    relevé lent, compte quand le relevé reprend (comme si elle était lue à ce moment-là) ; celle de A, non."""
    path = tmp_path / RECORD_NAME
    clock = Clock(T0)
    signals = TurnSignals(["A", "B"], clock=clock, record=path)
    clock.now = T0 + 10
    signals.new_fight()
    signals.playing("A")
    clock.now = T0 + 20
    reader = FakeReader([turn(1, "A", created=T0 + 10.5), turn(2, "B", created=T0 + 19.5)])
    assert signals.poll(reader) == [] and signals.take(["A", "B"]) is None  # relevé coupé : aucun signal
    clock.now = T0 + 20.5
    signals.played("A")
    signals.enabled_for_polling = True
    assert signals.take(["A", "B"]) == "B"  # sans attendre le relevé suivant
    assert [(r["id"], r["decision"], r["releve_actif"]) for r in records(path)] == [(1, "hors relevé", False),
                                                                                    (2, "hors relevé", False)]


def test_listener_thread_records_slowly_while_polling_is_off(tmp_path):
    path = tmp_path / RECORD_NAME
    reader = FakeReader([turn(1, "A", created=time.time() + 60)])  # après le démarrage de l'écouteur
    signals = TurnSignals(["A"], interval=0.01, idle=0.2, reader=lambda: reader, record=path)
    assert signals.start(wait=2)
    try:
        deadline = time.monotonic() + 2
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.3)
        assert 1 <= reader.reads <= 4  # relevé coupé : lent (toutes les 0,2 s ici, 1 s par défaut)
        assert signals.take(["A"]) is None
        signals.enabled_for_polling = True
        before = reader.reads
        deadline = time.monotonic() + 2
        while reader.reads < before + 5 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert reader.reads >= before + 5  # relevé actif : rapide
    finally:
        signals.stop()
    (line,) = records(path)
    assert line["id"] == 1 and line["releve_actif"] is False and line["decision"] == "hors relevé"


def test_recording_failure_does_not_stop_the_listener(tmp_path, caplog):
    signals = TurnSignals(["A"], record=tmp_path)  # un dossier : écriture impossible
    signals.enabled_for_polling = True
    reader = FakeReader([turn(1, "A", created=time.time())], [turn(2, "A", created=time.time() + 1)])
    assert signals.poll(reader) == ["A"] and signals.poll(reader) == ["A"]  # les signaux continuent
    (message,) = warnings(caplog)  # signalé une fois
    assert "Enregistrement des notifications impossible" in message


def test_diagnostic_records_every_notification(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    later = time.time() + 60  # après le démarrage de l'écouteur
    reader = FakeReader([turn(1, "Imthebest", created=later),
                         toast(2, "Imthebest - Dofus Retro", "Nouvel échange", created=later)])

    class Stop:
        """Remplace le module time de signals.py : Ctrl+C dès que le faux centre de notifications a été relu."""

        @staticmethod
        def sleep(seconds):
            deadline = time.monotonic() + 2
            while reader.reads < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            raise KeyboardInterrupt

    monkeypatch.setattr(signals_module, "ROOT", tmp_path)
    monkeypatch.setattr(signals_module, "load_settings", lambda: {"team": {"turn_words": ["à {nom} de jouer"]}})
    monkeypatch.setattr(signals_module, "TurnSignals", functools.partial(TurnSignals, reader=lambda: reader))
    monkeypatch.setattr(signals_module, "time", Stop)
    monkeypatch.setattr(logging, "basicConfig", lambda **options: None)  # le journal de pytest reste en place
    assert signals_module._diagnose(["Jesuisleking", "Imthebest"]) == 0
    (path,) = (tmp_path / "logs").glob("notifications_diagnostic_*.jsonl")
    assert [(r["id"], r["decision"]) for r in records(path)] == [(1, "signal"), (2, "sans mot de tour")]
    shown = [m for m in infos(caplog) if m.startswith("Notification de Dofus")]
    assert any("Imthebest : signal" in m for m in shown) and any("sans mot de tour : ignorée" in m for m in shown)


# Désactivé, avec un message clair


def test_missing_packages_disable_the_listener_with_one_clear_message(monkeypatch, caplog):
    monkeypatch.setattr(signals_module, "missing_packages", lambda: list(PACKAGES))
    monkeypatch.setattr(signals_module, "check_windows_settings", lambda app: pytest.fail("registre lu"))
    signals = TurnSignals(["A"])
    assert not signals.start() and not signals.active and signals._thread is None
    (message,) = warnings(caplog)
    assert "pip install --require-hashes --only-binary=:all: -r requirements-winrt.txt" in message  # versions épinglées
    assert "timeline" in message and "Confidentialité" in message and "bannières" in message
    assert "Notifications en arrière-plan" in message


def test_winrt_import_failure_in_the_listener_thread(caplog):
    def broken():
        raise ImportError("No module named 'winrt'")

    signals = TurnSignals(["A"], reader=broken)
    assert not signals.start(wait=2)
    (message,) = warnings(caplog)
    assert "pip install" in message and "winrt" in message


def test_access_refused_disables_the_listener(caplog):
    reader = FakeReader(refused="DENIED")
    signals = TurnSignals(["A"], reader=lambda: reader)
    assert not signals.start(wait=2) and not signals.active
    (message,) = warnings(caplog)
    assert "DENIED" in message and "Confidentialité" in message and "pip install" not in message
    assert reader.closed and reader.reads == 0


def test_windows_settings_problems():
    off = settings_problems({"Enabled": 0}, {})
    assert any("coupées" in p for p in off) and any("bannières" in p for p in off)  # bannières : affichées par défaut
    assert settings_problems({"ShowBanner": 0}, None) == []
    assert any("aucune notification" in p for p in settings_problems(None, None))
    assert any("toutes les notifications" in p for p in settings_problems({"ShowBanner": 0}, {"ToastEnabled": 0}))
    # Pas gardées dans le centre de notifications : lues quand même (03/10, 17 sur 17).
    assert settings_problems({"ShowBanner": 0, "ShowInActionCenter": 0}, {}) == []


def test_turn_signal_setting():
    assert turn_signal({}) == TIMELINE  # défaut : rien ne change
    assert turn_signal({"team": {"turn_signal": "Notifications"}}) == NOTIFICATIONS
    assert turn_signal({"team": {"turn_signal": "timeline"}}, forced=True) == NOTIFICATIONS  # --notifications
    with pytest.raises(ValueError):
        turn_signal({"team": {"turn_signal": "bannieres"}})


# Dans le TeamBot


def test_signaled_window_looked_at_first_without_waiting():
    screen = Screen()
    seen_from_a = without_timer(load_frame("combat_mon_tour"))  # tour d'un allié, vu dans la fenêtre de A
    a = member("A", screen, seen_from_a)
    b = member("B", screen, without_timer(load_frame("combat_mon_tour")))
    c = member("C", screen, load_frame("combat_mon_tour"))  # le timer de son tour : dans sa fenêtre
    bot = team_bot(a, b, c)
    bot.my_team = RED
    scene = read_scene(seen_from_a)
    assert bot._player(a, seen_from_a, scene, [a, b, c]) is None  # sans notification : on attend le timer ici
    assert events(screen, "look") == []
    bot._turn_seen = None
    bot.signals = signaled("C")
    assert bot._player(a, seen_from_a, scene, [a, b, c]) is c  # tout de suite, sans attendre TIMER_FILL
    assert [e[0] for e in events(screen, "look")] == ["C"]  # sa fenêtre seulement
    assert bot.router.portraits.get("C") is not None  # portrait retenu : ses tours suivants, directement


def test_signal_starts_the_turn_without_waiting_for_the_timer(monkeypatch):
    # Idée de l'utilisateur (03/10) : notification reçue = tour commencé. Sa fenêtre montre le tour de mon équipe : il
    # joue tout de suite ; son timer (qui se remplit en ~1 s) n'est plus attendu.
    clock = [100.0]
    monkeypatch.setattr("bot.team.loop.time.monotonic", lambda: clock[0])
    screen = Screen()
    seen_from_a = without_timer(load_frame("combat_mon_tour"))
    a = member("A", screen, seen_from_a)
    no_timer, timer = without_timer(load_frame("combat_mon_tour")), load_frame("combat_mon_tour")
    c = member("C", screen, no_timer)
    bot = team_bot(a, c)
    bot.my_team = RED
    bot.signals = signaled("C", names=("A", "C"))
    assert bot._player(a, seen_from_a, read_scene(seen_from_a), [a, c]) is c
    assert [e[0] for e in events(screen, "look")] == ["C"] and bot.router.portraits.get("C") is None  # pas encore
    assert bot._shows_turn(c, no_timer)  # « est-ce encore mon tour ? » : oui, sans timer, pendant EARLY_START
    clock[0] += EARLY_START
    assert not bot._shows_turn(c, no_timer)  # au-delà : la règle habituelle
    clock[0] -= EARLY_START
    assert bot._shows_turn(c, timer) and bot._early is None  # son timer : son portrait est retenu
    assert bot.router.portraits.get("C") is not None


def test_signal_contradicted_by_a_known_portrait_does_not_start_the_turn():
    screen = Screen()
    frame = without_timer(load_frame("combat_mon_tour"))
    a, c = member("A", screen, frame), member("C", screen, without_timer(load_frame("combat_mon_tour")))
    bot = team_bot(a, c)
    bot.my_team = RED
    bot.router.learn("A", current_portrait(frame))  # la timeline encadre A, connu
    bot.signals = signaled("C", names=("A", "C"))
    assert bot._player(a, frame, read_scene(frame), [a, c]) is a  # portrait reconnu : A, sans même regarder chez C


def test_signal_does_not_replace_the_timer_check():
    screen = Screen()
    seen_from_a = without_timer(load_frame("combat_mon_tour"))
    a = member("A", screen, seen_from_a)
    b = member("B", screen, load_frame("combat_mon_tour"))  # c'est le tour de B
    c = member("C", screen, without_timer(load_frame("combat_mon_tour")))
    bot = team_bot(a, b, c)
    bot.my_team = None  # équipe pas encore connue : pas de départ sur notification, le timer reste juge
    bot.signals = signaled("C")  # notification qui n'est pas celle de ce tour
    scene = read_scene(seen_from_a)
    assert bot._player(a, seen_from_a, scene, [a, b, c]) is None  # pas de timer chez C : on n'y joue pas
    assert [e[0] for e in events(screen, "look")] == ["C"] and screen.front is c.window  # _turns regarde depuis C
    portrait, seen = bot._turn_seen
    bot._turn_seen = (portrait, seen - 2)  # 2 s plus tard, toujours pas de timer chez C : on l'attend encore chez lui
    from_c = without_timer(load_frame("combat_mon_tour"))
    assert bot._player(c, from_c, read_scene(from_c), [a, b, c]) is None
    assert [e[0] for e in events(screen, "look")] == ["C"]  # pas d'aller-retour dans les autres fenêtres (03/10)
    name, hinted = bot._hinted
    bot._hinted = (name, hinted - HINT_PATIENCE)  # notification trompeuse : la recherche habituelle reprend
    assert bot._player(c, from_c, read_scene(from_c), [a, b, c]) is b


def test_signal_for_the_watched_window_changes_nothing():
    screen = Screen()
    seen_from_a = without_timer(load_frame("combat_mon_tour"))
    a, b = member("A", screen, seen_from_a), member("B", screen, load_frame("combat_mon_tour"))
    bot = team_bot(a, b)
    bot.signals = signaled("A", names=("A", "B"))
    assert bot._player(a, seen_from_a, read_scene(seen_from_a), [a, b]) is None  # équipe inconnue : son timer, ici
    assert events(screen, "look") == []
    bot.my_team = RED
    bot.signals = signaled("A", names=("A", "B"))
    assert bot._player(a, seen_from_a, read_scene(seen_from_a), [a, b]) is a  # timeline de mon équipe : il joue
    assert events(screen, "look") == []


def test_polling_only_while_waiting_for_a_turn(monkeypatch):
    monkeypatch.setattr("bot.team.loop.TIMER_FILL", 0)
    monkeypatch.setattr("bot.team.loop.FIGHT_END_CONFIRM", 0)
    screen = Screen()
    my_turn, enemy, end = load_frame("combat_mon_tour"), load_frame("combat_tour_adversaire"), load_frame("hors_combat")
    a = member("A", screen, enemy, without_timer(my_turn))  # A regarde : tour du monstre, puis celui de B
    b = member("B", screen, my_turn)
    bot = team_bot(a, b)
    bot.my_team = RED
    signals = bot.signals = TurnSignals(["A", "B"])
    states = []
    player = bot._player

    def spy(*args):
        states.append(("attente", signals.enabled_for_polling))
        return player(*args)

    def b_played():  # après le tour de B : tour du monstre, puis fin du combat, dans les deux fenêtres
        states.append(("tour de B", signals.enabled_for_polling))
        b.game.frames = [enemy, end]
        a.game.frames = [end]

    monkeypatch.setattr(bot, "_player", spy)
    b.ai.on_turn = b_played
    bot._turns([a, b])
    assert states == [("attente", True), ("attente", True), ("tour de B", False), ("attente", True)]
    assert not signals.enabled_for_polling  # combat fini : relevé coupé
