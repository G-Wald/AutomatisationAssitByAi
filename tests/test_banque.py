"""Passage à la banque (team/bank.py) : portes franchies par toute l'équipe en même temps, étapes qui suivent le serveur
(27/10, « les actions sont lentes »)."""

from contextlib import contextmanager
from types import SimpleNamespace

from bot.connection.network import ClientFeed
import bot.team.bank as bank
from bot.team.bank import DOOR, SETTLE, BankVisit
from conftest import load_frame


class Game:
    """Fenêtre factice : le clic sur un soleil fait changer de carte d'après le serveur."""

    def __init__(self, frame, net: ClientFeed | None, moves: bool = True) -> None:
        self.frame, self.net, self.moves = frame, net, moves
        self.clicks, self.held, self.slept = [], [], []
        self.recorder = SimpleNamespace(save=lambda *a, **k: "capture.png")

    def look(self, park: bool = True):
        return self.frame

    def click(self, point, spread=(0, 0), button: str = "left") -> bool:
        self.clicks.append((point, tuple(self.held)))
        if self.net is not None and self.moves:
            self.net.feed("GDM|7339|0|cle")
            self.net.feed("GDK")
        return True

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    @contextmanager
    def holding(self, key: str):
        self.held.append(key)
        yield
        self.held.remove(key)


def member(name: str, moves: bool = True) -> SimpleNamespace:
    net = ClientFeed(name)
    net.feed("BN")  # écoute vivante
    return SimpleNamespace(name=name, game=Game(load_frame("combat_mon_tour"), net, moves))


def test_the_whole_team_goes_through_the_door_at_once_players_hidden(monkeypatch):
    monkeypatch.setattr(bank, "DOOR_WAIT", 0.3)
    team = [member("Fourboun"), member("Eldieso"), member("Fourbodeux", moves=False)]
    visit = BankVisit(team, hide_key="e")
    there = visit._doors(team, DOOR, inside=True)
    assert there == {"Fourboun", "Eldieso"}  # Fourbodeux : pas de nouvelle carte annoncée
    for m in team:
        (point, held), = m.game.clicks  # un clic chacun, sans attendre les autres
        assert held == ("e",)


def test_each_step_waits_for_the_server_answer_or_the_old_fixed_time():
    quick, blind = member("Pesopepe"), member("Walitcoin")
    blind.game.net = None
    visit = BankVisit([quick, blind])
    clicked = quick.game.net.heard_at - 1.0
    quick.game.net.feed("DQ123|456;789")  # question du banquier déjà là
    visit._wait(quick, "question_at", clicked, 1.5)
    visit._wait(blind, "question_at", clicked, 1.5)
    assert quick.game.slept == [SETTLE] and blind.game.slept == [1.5]


def test_no_new_bank_trip_while_the_pods_are_those_left_after_the_bank():
    # 27/10 : l'équipement reste dans les pods (Pesopepe 42 -> 30 % en sortant) ; sans garde, aller-retour sans fin.
    from bot.team.loop import TeamBot, ZoneRoamer

    pesopepe = member("Pesopepe")
    pesopepe.game.net.feed("Ow540|0|1800|0")  # 30 %
    errands = []
    bot = TeamBot.__new__(TeamBot)
    bot._pods, bot.pods_full, bot.bank_auto = {}, 0.25, True
    bot.roamer = ZoneRoamer.__new__(ZoneRoamer)
    bot.roamer.errand, bot.roamer.start_errand = None, lambda *args: errands.append(args)
    bot._bank_floor, bot._banked = {}, True  # sortie de la banque : pods du serveur pas encore annoncés
    bot._note_pods(pesopepe, None)  # premiers pods lus ensuite : le plancher
    assert errands == []
    bot._pods = {}
    pesopepe.game.net.feed("Ow720|0|1800|0")  # 40 % : de nouvelles ressources
    bot._note_pods(pesopepe, None)
    assert len(errands) == 1


def test_position_given_by_the_game_is_reused_until_the_server_announces_another_map():
    # 27/10 (« pourquoi ça a spammé le %pos% ? ») : départ puis trajet redemandaient à 6 s d'écart, sans avoir bougé.
    import time

    from bot.brain.position import PositionAsker

    game = SimpleNamespace(net=ClientFeed("Eldieso"), told_position=None)
    now = time.monotonic()
    game.net.feed("GDM|7411|0|cle", now=now - 5)
    game.net.feed("GDK", now=now - 5)
    game.told_position = ((0, -16), now - 1)
    asker = PositionAsker(game, "Eldieso")  # un autre demandeur que celui qui a eu la réponse
    assert asker.ask() == (0, -16)  # rien tapé : pas de type_text sur ce jeu factice, la réponse vient de la mémoire
    game.net.feed("GDM|7412|0|cle", now=now)  # nouvelle carte : la réponse ne vaut plus
    assert asker._known() is None
