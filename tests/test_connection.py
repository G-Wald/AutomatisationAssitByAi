"""Connexion automatique : écrans de connexion, groupe (captures de l'utilisateur du 08/10), déroulés simulés."""

from functools import lru_cache

import pytest

from bot.brain.game import Game
from bot.capture import monitors as monitors_module
from bot.capture.monitors import Monitor, ScreenSettings, pick_monitor, place_window, valid_choice
from bot.capture.window import Rect
from bot.connection import launcher as launcher_module
from bot.connection import login as login_module
from bot.connection import manager as manager_module
from bot.connection.__main__ import team_order
from bot.connection.clients import CLIENT_TITLE
from bot.connection.group import Player, form_group
from bot.connection.login import Login, LoginFailed, NotSubscribed
from bot.connection.manager import Watcher, connect_windows
from bot.connection.settings import ConnectionSettings
from bot.gui.launch import Choices, build_args, validate
from bot.perception import party
from bot.perception.launcher import find_play, find_retro_icon
from bot.perception.login import LoginStep, find_character, find_server, read_login, slot_box, slot_point
from bot.perception.text import find_text, render_text
from bot.team.roster import TeamSettings
from tests.conftest import load_frame


@pytest.fixture(autouse=True)
def no_real_windows(monkeypatch):
    """Jamais les vraies fenêtres du jeu ni le vrai launcher pendant les tests (le jeu peut être ouvert) : aucune
    fenêtre, aucun lancement, aucun placement ni fermeture ; chaque test remplace ce dont il a besoin."""
    monkeypatch.setattr(manager_module, "client_windows", lambda: [])
    monkeypatch.setattr(manager_module, "new_windows", lambda known: [])
    monkeypatch.setattr(manager_module, "launch_clients", lambda *args: [])
    monkeypatch.setattr(manager_module, "place_windows", lambda windows, screens, wait: None)
    monkeypatch.setattr(manager_module, "close_window", lambda window, wait: True)
    monkeypatch.setattr(launcher_module, "client_windows", lambda: [])


@lru_cache
def frame(name):
    return load_frame(name)


def inside(point, box) -> bool:
    x0, y0, x1, y1 = box
    return x0 <= point[0] <= x1 and y0 <= point[1] <= y1


# Texte dessiné


def test_rendered_text_is_cropped_and_cached():
    text = render_text("Allisteria", 18, True)
    assert text.ndim == 2 and 10 <= text.shape[0] <= 20 and 70 <= text.shape[1] <= 110
    assert text[0].max() > 40 and text[:, 0].max() > 40  # recadré au plus près
    assert render_text("Allisteria", 18, True) is text


def test_server_name_found_only_for_its_name():
    """Cherché sur la ligne des noms de serveur seulement : sur tout l'écran, un nom court (« Boune ») ressemble à 0,85
    à un mot d'un autre texte (« Temps d'abonnement restant... »)."""
    screen = frame("connexion_choix_serveur")
    whole = (0, 0, screen.layout.width, screen.layout.height)
    found = find_text(screen, "Allisteria", whole, 18, bold=True)
    assert found is not None and abs(found[0][0] - 786) <= 3 and abs(found[0][1] - 624) <= 3 and found[1] > 0.9
    view = read_login(screen)
    for other in ("Boune", "Crail", "Eratz", "Henual", "Draconiros", "Ombre", "Arty"):
        assert find_server(screen, view, other) is None, other


# Écrans de connexion


def test_login_screens():
    servers = read_login(frame("connexion_choix_serveur"))
    assert servers.step is LoginStep.SERVERS and servers.button is not None
    assert abs(servers.button[0] - 1559) <= 3 and abs(servers.button[1] - 792) <= 3  # « Sélectionner »
    characters = read_login(frame("connexion_choix_personnage"))
    assert characters.step is LoginStep.CHARACTERS and characters.button is not None
    assert abs(characters.button[0] - 1241) <= 3 and abs(characters.button[1] - 801) <= 3  # « Jouer »
    for name in ("hors_combat", "combat_mon_tour", "groupe_invitation", "amis_fenetre"):
        assert read_login(frame(name)).step is LoginStep.OTHER, name


def test_welcome_screen_and_discovery_mode():
    welcome = read_login(frame("connexion_accueil"))
    assert welcome.step is LoginStep.WELCOME and abs(welcome.button[0] - 932) <= 4 and abs(welcome.button[1] - 435) <= 4
    discovery = read_login(frame("connexion_mode_decouverte"))
    assert discovery.step is LoginStep.SERVERS and discovery.discovery  # Waldharyx8 : « Mode découverte »
    for name in ("connexion_choix_serveur", "connexion_choix_personnage"):
        assert not read_login(frame(name)).discovery, name  # « Temps d'abonnement restant »


def test_inactivity_message():
    view = read_login(frame("connexion_inactif"))  # « Connexion interrompue... Tu es resté trop longtemps inactif. »
    assert view.step is LoginStep.MESSAGE and view.inactive
    assert abs(view.button[0] - 1247) <= 4 and abs(view.button[1] - 573) <= 4  # « Ok »
    for name in ("connexion_accueil", "groupe_invitation", "fin_de_combat", "hors_combat"):
        assert read_login(frame(name)).step is not LoginStep.MESSAGE, name


def test_server_card_point():
    screen = frame("connexion_choix_serveur")
    view = read_login(screen)
    point = find_server(screen, view, "Allisteria")
    assert point is not None and inside(point, (700, 380, 870, 600))  # l'illustration de sa carte
    assert find_server(screen, view, "Draconiros") is None


def test_characters_found_on_their_slot():
    screen = frame("connexion_choix_personnage")
    view = read_login(screen)
    for slot, name in enumerate(["Gela-[A13]", "Fourbocinq", "Elnano", "Walheal", "Vegano-[Gal]"]):
        assert find_character(screen, view, [name]) == (slot, name)
    assert find_character(screen, view, ["Brawli-[Gal]"]) is None  # autre compte
    assert find_character(screen, view, ["Brawli-[Gal]", "Walheal"]) == (3, "Walheal")  # le premier présent


def test_character_click_avoids_the_delete_cross():
    """La croix de la plaque (à droite du niveau) supprime le personnage : le clic tombe sur le personnage."""
    screen = frame("connexion_choix_personnage")
    view = read_login(screen)
    for slot in range(5):
        point = slot_point(screen, view, slot)
        plate = slot_box(screen, view, slot)
        assert point[1] + 25 < plate[1] - 40  # bien au-dessus de la plaque (nom, niveau, croix)
        assert plate[0] < point[0] < plate[2]


# Groupe


def test_friends_window():
    screen = frame("amis_fenetre")
    assert party.find_friends_icon(screen) is not None
    title = party.find_friends_window(screen)
    assert title is not None
    point, spread = party.find_friend(screen, title, "Brawli-[Gal]")
    assert abs(point[1] - 352) <= 3 and 1060 <= point[0] <= 1140 and spread[0] < 25
    assert party.find_friend(screen, title, "Gela-[A13]") is None
    assert party.find_friend(screen, title, "Waldharyx8#2171") is None  # liste des non connectés : pas cherchée
    assert party.find_friends_window(frame("hors_combat")) is None


def test_invite_menu_and_popups():
    menu = frame("amis_menu_inviter")
    entry = party.find_invite_entry(menu, (1096, 351))
    assert entry is not None and abs(entry[1] - 540) <= 4
    assert party.find_invite_entry(frame("amis_fenetre"), (1096, 351)) is None
    accept = party.find_invitation(frame("groupe_invitation"))
    assert accept is not None and abs(accept[0] - 1123) <= 4 and abs(accept[1] - 425) <= 4  # « Oui », pas « Non »
    assert party.find_pending_invite(frame("amis_invitation_envoyee")) is not None
    for name in ("hors_combat", "amis_fenetre", "amis_invitation_envoyee"):
        assert party.find_invitation(frame(name)) is None, name


def test_group_arrow_states():
    assert party.group_arrow(frame("placement_case_ambigue"))[0] is True  # déplié : flèche ▶
    assert party.group_arrow(frame("groupe_replie"))[0] is False  # replié : flèche ◀
    for name in ("hors_combat", "fin_de_combat", "groupe_invitation", "connexion_choix_personnage"):
        assert party.group_arrow(frame(name)) is None, name


# Déroulés simulés


class FakeWindow:
    def __init__(self, title="Dofus Retro v1.49.6"):
        self.hwnd = id(self)
        self.title = title


class FakeRecorder:
    def save(self, image, label, lossless=False, quality=80):
        return f"{label}.png"


class ScreenGame:
    """Fenêtre simulée : l'écran affiché change selon les clics (`react`) ; horloge simulée, la même pour toutes les
    fenêtres (en cascade, l'attente d'une fenêtre fait avancer les autres)."""

    wait_until = Game.wait_until
    clock = [0.0]

    def __init__(self, screen: str, react, title="Dofus Retro v1.49.6"):
        self.screen = screen
        self.react = react
        self.window = FakeWindow(title)
        self.recorder = FakeRecorder()
        self.actions = []

    def look(self, park=None):
        self.clock[0] += 0.05
        return frame(self.screen)

    def sleep(self, seconds):
        self.clock[0] += seconds

    def now(self):
        return self.clock[0]

    def click(self, point=None, spread=(0, 0), button="left"):
        self.actions.append(("click", point, button))
        self.react(self, point, button)
        return True

    def press(self, key):
        self.actions.append(("press", key))
        self.react(self, key, "key")

    @property
    def control(self):
        return self


@pytest.fixture
def titles(monkeypatch):
    """Titre des fenêtres simulées (game.window.title), lu par la connexion comme celui d'une vraie fenêtre."""
    def logged(window):
        match = CLIENT_TITLE.match(window.title)
        return (match.group("name") or "") if match else ""
    for module in (login_module, manager_module):
        monkeypatch.setattr(module, "logged_in", logged)
        monkeypatch.setattr(module, "is_open", lambda window: bool(CLIENT_TITLE.match(window.title)))


def test_login_goes_from_server_to_game(titles):
    def react(game, point, button):
        if game.screen == "connexion_choix_serveur" and inside(point, (1480, 770, 1640, 815)):
            game.screen = "connexion_choix_personnage"  # « Sélectionner »
        elif game.screen == "connexion_choix_personnage" and inside(point, (1180, 780, 1300, 825)):
            game.window.title = "Gela-[A13] - Dofus Retro v1.49.6"  # « Jouer »
            game.screen = "hors_combat"

    game = ScreenGame("connexion_choix_serveur", react)
    name = Login(game, ConnectionSettings(), ["Brawli-[Gal]", "Gela-[A13]"]).run()
    assert name == "Gela-[A13]"
    points = [action[1] for action in game.actions if action[0] == "click"]
    assert inside(points[0], (700, 380, 870, 600))  # carte du serveur
    assert inside(points[1], (1480, 770, 1640, 815))  # « Sélectionner »
    assert inside(points[2], (740, 480, 820, 560))  # Gela, le premier des voulus présent sur ce compte
    assert inside(points[3], (1180, 780, 1300, 825))  # « Jouer »


def test_login_starts_from_welcome_screen(titles):
    def react(game, point, button):
        if game.screen == "connexion_accueil" and inside(point, (860, 420, 1005, 450)):
            game.screen = "connexion_choix_serveur"  # « Se connecter »
        elif game.screen == "connexion_choix_serveur" and inside(point, (1480, 770, 1640, 815)):
            game.screen = "connexion_choix_personnage"
        elif game.screen == "connexion_choix_personnage" and inside(point, (1180, 780, 1300, 825)):
            game.window.title = "Gela-[A13] - Dofus Retro v1.49.6"
            game.screen = "hors_combat"

    game = ScreenGame("connexion_accueil", react)
    assert Login(game, ConnectionSettings(), ["Gela-[A13]"]).run() == "Gela-[A13]"
    assert inside(game.actions[0][1], (860, 420, 1005, 450))


def test_login_after_inactivity(titles):
    def react(game, point, button):
        if game.screen == "connexion_inactif" and inside(point, (1225, 560, 1270, 590)):
            game.screen = "connexion_accueil"  # « Ok »
        elif game.screen == "connexion_accueil" and inside(point, (860, 420, 1005, 450)):
            game.screen = "connexion_choix_personnage"
        elif game.screen == "connexion_choix_personnage" and inside(point, (1180, 780, 1300, 825)):
            game.window.title = "Gela-[A13] - Dofus Retro v1.49.6"
            game.screen = "hors_combat"

    game = ScreenGame("connexion_inactif", react)
    assert Login(game, ConnectionSettings(), ["Gela-[A13]"]).run() == "Gela-[A13]"
    assert inside(game.actions[0][1], (1225, 560, 1270, 590)) and inside(game.actions[1][1], (860, 420, 1005, 450))


def test_login_stops_on_a_discovery_account(titles):
    game = ScreenGame("connexion_mode_decouverte", lambda *args: None)
    with pytest.raises(NotSubscribed):
        Login(game, ConnectionSettings(), ["Gela-[A13]"]).run()
    assert not game.actions  # aucun clic


def test_connect_windows_closes_discovery_accounts(titles, monkeypatch):
    games = {"abonne": ScreenGame("connexion_choix_personnage", None), "decouverte":
             ScreenGame("connexion_mode_decouverte", lambda *args: None)}

    def react(game, point, button):
        if inside(point, (1180, 780, 1300, 825)):
            game.window.title = "Gela-[A13] - Dofus Retro v1.49.6"
            game.screen = "hors_combat"

    games["abonne"].react = react
    windows = {g.window.hwnd: g for g in games.values()}
    monkeypatch.setattr(manager_module, "client_windows", lambda: [g.window for g in games.values()])
    closed = []
    monkeypatch.setattr(manager_module, "close_window", lambda window, wait: closed.append(window) or True)
    monkeypatch.setattr(manager_module, "place_windows", lambda windows, screens, wait: None)
    names = connect_windows(ConnectionSettings(), ["Gela-[A13]"], lambda window: windows[window.hwnd],
                            games["abonne"])
    assert names == ["Gela-[A13]"] and closed == [games["decouverte"].window]


def jouer_logs_in(name):
    """Réaction d'une fenêtre à l'écran des personnages : « Jouer » la met en jeu avec `name`."""
    def react(game, point, button):
        if game.screen == "connexion_choix_personnage" and inside(point, (1180, 780, 1300, 825)):
            game.window.title = f"{name} - Dofus Retro v1.49.6"
            game.screen = "hors_combat"
    return react


def test_connect_windows_launches_all_accounts_and_keeps_the_wanted_ones(titles, monkeypatch):
    """11/10 : Brawli déjà en jeu ; Ctrl+8 ouvre 3 comptes : un non abonné (fermé), celui de Gela (connecté), puis un
    de trop (fermé : tous les personnages voulus sont en jeu)."""
    brawli = ScreenGame("hors_combat", lambda *args: None, "Brawli-[Gal] - Dofus Retro v1.49.6")
    discovery = ScreenGame("connexion_mode_decouverte", lambda *args: None)
    gela = ScreenGame("connexion_choix_personnage", jouer_logs_in("Gela-[A13]"))
    extra = ScreenGame("connexion_choix_personnage", jouer_logs_in("Fourbocinq"))
    games = {g.window.hwnd: g for g in (brawli, discovery, gela, extra)}
    launches, closed = [], []
    monkeypatch.setattr(manager_module, "client_windows", lambda: [brawli.window])
    monkeypatch.setattr(manager_module, "launch_clients", lambda *args: launches.append(1) or
                        [discovery.window, gela.window, extra.window])
    monkeypatch.setattr(manager_module, "close_window", lambda window, wait: closed.append(window) or True)
    names = connect_windows(ConnectionSettings(), ["Brawli-[Gal]", "Gela-[A13]"], lambda w: games[w.hwnd], brawli)
    assert names == ["Brawli-[Gal]", "Gela-[A13]"] and launches == [1]
    assert closed == [discovery.window, extra.window] and not extra.actions  # la fenêtre en trop : pas un clic


def test_connect_windows_closes_accounts_without_wanted_characters(titles, monkeypatch):
    other = ScreenGame("connexion_choix_personnage", jouer_logs_in("Gela-[A13]"))  # compte de Gela, Fourbocinq...
    closed = []
    monkeypatch.setattr(manager_module, "launch_clients", lambda *args: [other.window])
    monkeypatch.setattr(manager_module, "close_window", lambda window, wait: closed.append(window) or True)
    names = connect_windows(ConnectionSettings(), ["Brawli-[Gal]"], lambda w: other, other)
    assert names == [] and closed == [other.window]
    assert other.screen == "connexion_choix_personnage"  # aucun personnage choisi sur ce compte


def test_connect_windows_without_wanted_characters_logs_in_the_last_played(titles, monkeypatch):
    other = ScreenGame("connexion_choix_personnage", jouer_logs_in("Gela-[A13]"))
    monkeypatch.setattr(manager_module, "launch_clients", lambda *args: [other.window])
    assert connect_windows(ConnectionSettings(), [], lambda w: other, other) == ["Gela-[A13]"]


def test_connect_windows_connects_windows_opened_late(titles, monkeypatch):
    """Les comptes s'ouvrent un à un : une fenêtre apparue pendant la connexion de la première est connectée aussi."""
    first = ScreenGame("connexion_mode_decouverte", lambda *args: None)
    late = ScreenGame("connexion_choix_personnage", jouer_logs_in("Gela-[A13]"))
    games = {g.window.hwnd: g for g in (first, late)}
    monkeypatch.setattr(manager_module, "launch_clients", lambda *args: [first.window])
    monkeypatch.setattr(manager_module, "new_windows",
                        lambda known: [late.window] if late.window.hwnd not in known else [])
    names = connect_windows(ConnectionSettings(), ["Gela-[A13]"], lambda w: games[w.hwnd], first)
    assert names == ["Gela-[A13]"]


def test_windows_are_connected_in_cascade(titles, monkeypatch):
    """11/10 : une fenêtre après l'autre, c'était long. Chaque fenêtre avance d'un geste à tour de rôle : la seconde
    choisit son serveur avant que la première ait fini."""
    order = []

    def react_for(name):
        def react(game, point, button):
            order.append((name, game.screen))
            if game.screen == "connexion_choix_serveur" and inside(point, (1480, 770, 1640, 815)):
                game.screen = "connexion_choix_personnage"  # « Sélectionner »
            elif game.screen == "connexion_choix_personnage" and inside(point, (1180, 780, 1300, 825)):
                game.window.title = f"{name} - Dofus Retro v1.49.6"  # « Jouer »
                game.screen = "hors_combat"
        return react

    first = ScreenGame("connexion_choix_serveur", react_for("Gela-[A13]"))
    second = ScreenGame("connexion_choix_serveur", react_for("Fourbocinq"))
    games = {g.window.hwnd: g for g in (first, second)}
    monkeypatch.setattr(manager_module, "client_windows", lambda: [first.window, second.window])
    names = connect_windows(ConnectionSettings(), [], lambda w: games[w.hwnd], first)
    assert sorted(names) == ["Fourbocinq", "Gela-[A13]"]
    assert order.index(("Fourbocinq", "connexion_choix_serveur")) < order.index(("Gela-[A13]",
                                                                                 "connexion_choix_personnage"))
    assert len(first.actions) == len(second.actions) == 4  # carte, Sélectionner, personnage, Jouer


def test_connect_windows_does_not_launch_when_everyone_is_there(titles, monkeypatch):
    gela = ScreenGame("hors_combat", lambda *args: None, "Gela-[A13] - Dofus Retro v1.49.6")
    launches = []
    monkeypatch.setattr(manager_module, "client_windows", lambda: [gela.window])
    monkeypatch.setattr(manager_module, "launch_clients", lambda *args: launches.append(1) or [])
    assert connect_windows(ConnectionSettings(), ["Gela-[A13]"], lambda w: gela, gela) == ["Gela-[A13]"]
    assert launches == []
    # Il en manque un, et rien ne s'ouvre : un seul essai.
    assert connect_windows(ConnectionSettings(), ["Gela-[A13]", "Brawli-[Gal]"], lambda w: gela, gela) == ["Gela-[A13]"]
    assert launches == [1]


def test_launcher_play_button():
    image = frame("launcher_retro").image
    assert find_play(image) == (368, 576) and find_retro_icon(image) == (44, 250)
    other_game = image.copy()
    other_game[185:225, 470:600] = other_game[240:280, 1500:1630]  # sans le sélecteur « Retro » : autre jeu
    assert find_play(other_game) is None  # le « JOUER » d'un autre jeu n'est jamais cliqué
    for name in ("connexion_choix_personnage", "hors_combat"):
        assert find_play(frame(name).image) is None, name


def test_launch_all_presses_ctrl_8_on_the_retro_page(monkeypatch):
    monkeypatch.setattr(launcher_module, "window_scale", lambda window: 1.0)
    saved = []
    game = ScreenGame("launcher_retro", lambda *args: None, "Ankama Launcher")
    game.recorder.save = lambda image, label, lossless=False: saved.append(label) or label
    assert launcher_module.launch_all(game, game)
    assert game.actions == [("press", "ctrl+8")]  # au clavier, sans clic
    assert saved == ["launcher_apres_ctrl_8"]  # rien ne s'est ouvert : capture


def test_login_takes_leftmost_character_when_none_wanted_is_there(titles):
    def react(game, point, button):
        if game.screen == "connexion_choix_personnage" and inside(point, (1180, 780, 1300, 825)):
            game.window.title = "Gela-[A13] - Dofus Retro v1.49.6"
            game.screen = "hors_combat"

    game = ScreenGame("connexion_choix_personnage", react)
    assert Login(game, ConnectionSettings(), ["Brawli-[Gal]"]).run() == "Gela-[A13]"
    assert inside(game.actions[0][1], (740, 480, 820, 560))  # le plus à gauche : le dernier joué


def test_login_unknown_server_fails_with_capture(titles):
    game = ScreenGame("connexion_choix_serveur", lambda *args: None)
    with pytest.raises(LoginFailed, match="Draconiros"):
        Login(game, ConnectionSettings(server="Draconiros"), []).run()
    assert not game.actions  # aucun clic


def test_login_unknown_screen_tries_keys_then_gives_up(titles):
    game = ScreenGame("groupe_invitation", lambda *args: None)  # écran inconnu pour la connexion
    with pytest.raises(LoginFailed, match="toujours pas en jeu"):
        Login(game, ConnectionSettings(login_timeout=15), []).run()
    assert [a[1] for a in game.actions if a[0] == "press"] == ["enter", "escape"]  # une fois chacune


def test_form_group_invites_accepts_and_collapses():
    def leader_react(game, point, button):
        if button == "key":
            if point == "escape" and game.screen == "amis_fenetre":
                game.screen = "groupe_replie"  # Échap : fenêtre « Amis » fermée (cadre du groupe déjà replié)
        elif game.screen == "hors_combat" and inside(point, (1640, 790, 1700, 845)):
            game.screen = "amis_fenetre"  # icône « Amis »
        elif game.screen == "amis_fenetre" and inside(point, (1050, 340, 1150, 365)):
            game.screen = "amis_menu_inviter"  # clic (gauche, 11/10) sur le nom de Brawli
        elif game.screen == "amis_menu_inviter" and inside(point, (1150, 525, 1390, 555)):
            game.screen = "amis_invitation_envoyee"
            member.screen = "groupe_invitation"

    def member_react(game, point, button):
        if game.screen == "groupe_invitation" and inside(point, (1090, 410, 1160, 440)):
            game.screen = "placement_case_ambigue"  # « Oui » : cadre du groupe déplié
            leader.screen = "amis_fenetre"
        elif game.screen == "placement_case_ambigue" and inside(point, (1895, 80, 1918, 105)):
            game.screen = "groupe_replie"

    leader = ScreenGame("hors_combat", leader_react, "Gela-[A13] - Dofus Retro v1.49.6")
    member = ScreenGame("hors_combat", member_react, "Brawli-[Gal] - Dofus Retro v1.49.6")
    missing = form_group(Player("Gela-[A13]", leader), [Player("Brawli-[Gal]", member)])
    assert missing == []
    leader_clicks = [action[1:] for action in leader.actions if action[0] == "click"]
    assert leader_clicks[1][1] == "left" and 1060 <= leader_clicks[1][0][0] <= 1140  # sur le nom, pas la croix
    assert member.screen == "groupe_replie"  # Oui, puis cadre replié
    assert ("press", "escape") in leader.actions  # fenêtre « Amis » fermée par Échap, pas par sa croix


def test_form_group_skips_grouped_and_reports_missing_friend():
    leader = ScreenGame("amis_fenetre", lambda *args: None, "Gela-[A13] - Dofus Retro v1.49.6")
    grouped = ScreenGame("groupe_replie", lambda *args: None, "Brawli-[Gal] - Dofus Retro v1.49.6")
    stranger = ScreenGame("hors_combat", lambda *args: None, "Autre - Dofus Retro v1.49.6")
    missing = form_group(Player("Gela-[A13]", leader), [Player("Brawli-[Gal]", grouped), Player("Autre", stranger)],
                         collapse_frames=False)
    assert missing == ["Autre"]  # pas dans la liste des amis connectés
    assert not grouped.actions and not stranger.actions
    assert not any(inside(action[1], (969, 328, 1329, 738)) for action in leader.actions if action[0] == "click")


def test_watcher_reconnects_the_same_character(titles, monkeypatch):
    runs = []

    class FakeLogin:
        def __init__(self, game, settings, wanted):
            self.game, self.wanted = game, wanted

        def run(self):
            runs.append(self.wanted)
            self.game.window.title = f"{self.wanted[0]} - Dofus Retro v1.49.6"
            return self.wanted[0]

    monkeypatch.setattr(manager_module, "Login", FakeLogin)
    regrouped = []
    monkeypatch.setattr(manager_module, "form_group", lambda leader, members, collapse: regrouped.append(leader.name))
    gela = ScreenGame("hors_combat", lambda *args: None, "Gela-[A13] - Dofus Retro v1.49.6")
    brawli = ScreenGame("hors_combat", lambda *args: None, "Dofus Retro v1.49.6")  # déconnecté
    watcher = Watcher(ConnectionSettings(check_every=5), [Player("Gela-[A13]", gela), Player("Brawli-[Gal]", brawli)])
    assert watcher.check() is True
    assert runs == [["Brawli-[Gal]"]] and regrouped == ["Gela-[A13]"]
    assert watcher.check() is False  # relu au plus toutes les 5 s


def test_watcher_regroups_only_out_of_combat(titles, monkeypatch):
    monkeypatch.setattr(manager_module, "Login", lambda game, settings, wanted: type("L", (), {
        "run": lambda self: setattr(game.window, "title", "Brawli-[Gal] - Dofus Retro v1.49.6")})())
    regrouped = []
    monkeypatch.setattr(manager_module, "form_group", lambda leader, members, collapse: regrouped.append(1))
    gela = ScreenGame("combat_mon_tour", lambda *args: None, "Gela-[A13] - Dofus Retro v1.49.6")
    brawli = ScreenGame("combat_mon_tour", lambda *args: None, "Dofus Retro v1.49.6")
    watcher = Watcher(ConnectionSettings(check_every=0), [Player("Gela-[A13]", gela), Player("Brawli-[Gal]", brawli)])
    watcher.check()
    assert regrouped == []  # en combat : plus tard
    gela.screen = brawli.screen = "hors_combat"
    assert watcher.check() is True and regrouped == [1]


# Réglages, titres, interface


def screen(left, top, width, height, primary=False, dpi=96):
    return Monitor(left * 7 + top, Rect(left, top, width, height), Rect(left, top, width, height - 48), primary, dpi)


def test_place_window_moves_then_maximizes(monkeypatch):
    """Lectures de Windows réelles (11/10 : win32gui.IsZoomed n'existe pas, le bot plantait au placement) ; seuls
    les gestes sur la fenêtre sont simulés : aucune vraie fenêtre ne bouge."""
    actions = []
    monkeypatch.setattr(monitors_module.win32gui, "IsWindow", lambda hwnd: True)
    monkeypatch.setattr(monitors_module.win32gui, "SetWindowPos", lambda *args: actions.append(("déplacée", args[2:4])))
    monkeypatch.setattr(monitors_module.win32gui, "ShowWindow", lambda hwnd, cmd: actions.append(("affichée", cmd)))
    full_hd = screen(-1920, 1074, 1920, 1080)
    assert place_window(0x0FFFFFF0, full_hd) is True  # fenêtre inexistante : sur aucun écran, ni agrandie
    assert actions == [("déplacée", (-1880, 1114)), ("affichée", monitors_module.win32con.SW_MAXIMIZE)]
    monkeypatch.setattr(monitors_module.win32gui, "IsWindow", lambda hwnd: False)
    assert place_window(0x0FFFFFF0, full_hd) is False  # fenêtre fermée entre-temps


def test_pick_monitor():
    four_k = screen(0, 0, 3840, 2160, primary=True, dpi=168)
    full_hd = screen(-1920, 1074, 1920, 1080)  # les écrans de l'utilisateur, le 11/10
    screens = [full_hd, four_k]
    assert pick_monitor("auto", screens) is full_hd  # l'écran des captures de référence, même s'il n'est pas principal
    assert pick_monitor("principal", screens) is four_k and pick_monitor("secondaire", screens) is full_hd
    assert pick_monitor("2", screens) is four_k and pick_monitor("3", screens) is None
    assert pick_monitor("auto", [four_k]) is four_k  # pas d'écran 1080p : le principal
    assert pick_monitor("secondaire", [four_k]) is None
    assert full_hd.describe() == "1920x1080 à 100 %" and four_k.describe() == "3840x2160 à 175 % (principal)"


def test_characters_text_and_known_ones(tmp_path):
    from bot.connection import characters
    assert characters.parse_names(" Gela-[A13], Brawli-[Gal] ;") == ("Gela-[A13]", "Brawli-[Gal]")
    assert characters.known_characters(tmp_path / "absent") == []
    (tmp_path / "Gela-[A13].json").write_text("{}", encoding="utf-8")
    (tmp_path / "Brawli-[Gal].json").write_text("{}", encoding="utf-8")
    assert characters.known_characters(tmp_path) == ["Brawli-[Gal]", "Gela-[A13]"]


def test_client_titles():
    assert CLIENT_TITLE.match("Gela-[A13] - Dofus Retro v1.49.6").group("name") == "Gela-[A13]"
    assert CLIENT_TITLE.match("Dofus Retro v1.49.6").group("name") is None
    assert not CLIENT_TITLE.match("Dofus Retro v1.49.6 - Google Chrome")
    assert not CLIENT_TITLE.match("Ankama Launcher")


def test_connection_settings():
    settings = ConnectionSettings.from_settings({"connexion": {"auto": True, "serveur": "Boune",
                                                               "personnages": "Gela-[A13]", "delai_connexion": 60}})
    assert settings.enabled and settings.server == "Boune" and settings.characters == ("Gela-[A13]",)
    assert settings.login_timeout == 60 and settings.wanted(("X",)) == ["Gela-[A13]"]
    assert ConnectionSettings.from_settings({}, server="Eratz").server == "Eratz"
    assert ConnectionSettings().wanted(("Brawli-[Gal]",)) == ["Brawli-[Gal]"]
    assert ConnectionSettings.from_settings({}, characters=("Brawli-[Gal]",)).characters == ("Brawli-[Gal]",)
    assert "%" not in ConnectionSettings().launcher_path

def test_screen_settings():
    assert ScreenSettings.from_settings({"window": {"ecran": 2, "plein_ecran": False}}) == ScreenSettings("2", False)
    assert ScreenSettings.from_settings({"window": {"ecran": None}}).choice == "aucun"
    assert ScreenSettings.from_settings({}) == ScreenSettings("auto", True)
    assert ScreenSettings.from_settings({"window": {"ecran": "auto"}}, choice="1").choice == "1"  # --ecran
    assert valid_choice("2") and valid_choice("Secondaire") and not valid_choice("0") and not valid_choice("gauche")


def test_team_order_puts_the_leader_first():
    names = ["Brawli-[Gal]", "Gela-[A13]"]
    assert team_order(names, TeamSettings(), ("Gela-[A13]", "Brawli-[Gal]")) == ["Gela-[A13]", "Brawli-[Gal]"]
    assert team_order(names, TeamSettings(leader="Brawli-[Gal]"), ("Gela-[A13]",)) == ["Brawli-[Gal]", "Gela-[A13]"]
    assert team_order(names, TeamSettings()) == names


def test_gui_connection_options():
    args = build_args(Choices(team=True, leader="Gela-[A13]", connect=True, server="Boune"), {})
    assert "--connexion" in args and args[args.index("--serveur") + 1] == "Boune"
    assert "--serveur" not in build_args(Choices(connect=True), {})  # serveur par défaut
    assert "--connexion" not in build_args(Choices(), {})
    # Connexion : des fenêtres encore à l'écran de connexion ne sont pas une erreur.
    assert validate(Choices(team=True, leader="Gela-[A13]", connect=True), {}, titles=["Dofus Retro v1.49.6"]) == []
    assert validate(Choices(connect=True, server=" "), {}) == ["Connexion automatique : choisis le serveur"]
