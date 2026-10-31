"""Sûreté des clics de l'interface (25/10) : fenêtres vues chargées avant d'y cliquer, rien sous la carte sans
certitude, groupe et connexion.

Cause de départ : journal 20261025_181212, combat 17. « Résultat du combat » encore en train de s'ouvrir chez Elseiso,
« Fermer » cru sur un objet de la barre de raccourcis (0,80, en (1682, 1003)) et cliqué dans les 8 fenêtres : une
potion de rappel bue, Elseiso téléporté à Brâkmar.
"""

import logging

import numpy as np

from bot.brain.popups import PopupCloser, close_flash_menu, still_open
from bot.brain.position import PositionAsker
from bot.brain.recovery import unlock_click
from bot.connection import manager as manager_module
from bot.connection.group import Player, accept, invite
from bot.connection.login import Login
from bot.connection.manager import Watcher
from bot.connection.settings import ConnectionSettings
from bot.perception import login as login_perception
from bot.perception import popups as perception_popups
from bot.perception.frame import Frame
from bot.perception.login import find_character, read_login
from bot.perception.popups import (BELOW_MAP_BUTTON, below_map, find_generic_close, find_known_popup,
                                   flash_close_point, load_popup_specs)
from bot.perception.templates import load_template
from tests.conftest import load_frame
from tests.test_connection import ScreenGame, frame as screen_frame, inside, no_real_windows, titles  # noqa: F401
from tests.test_team import Screen, events, member, team_bot

RESULT = "résultat du combat"
SPECS = load_popup_specs()


def near(a, b, tolerance):
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


def over_the_bar() -> Frame:
    """Résultat d'un combat à 8 personnages, 6 monstres et un percepteur (journal 171736, capture 0241) : la fenêtre
    descend sous la carte, son « Fermer » en (1663, 1002) recouvre la 2e rangée de la barre de raccourcis."""
    return load_frame("resultat_combat_8_persos_sur_la_barre")


def moved_button(frame: Frame, dx: int, at: tuple[int, int] = (1663, 1002)) -> Frame:
    """La même capture, le bouton « Fermer » recopié `dx` px à droite (par-dessus le vrai) : un bouton parfait, mais pas
    à sa place."""
    image = frame.image.copy()
    height, width = load_template("fin_de_combat_fermer.png").shape[:2]
    w, h = round(width * frame.layout.scale / 2) + 4, round(height * frame.layout.scale / 2) + 4
    x, y = at
    patch = image[y - h : y + h, x - w : x + w].copy()
    image[y - h : y + h, x - w + dx : x + w + dx] = patch
    return Frame.of(image)


def dark_like(frame: Frame) -> Frame:
    """Une fenêtre assombrit tout l'écran : plus aucun fond beige libre."""
    return Frame.of(np.zeros_like(frame.image))


# Fenêtres connues : bouton sous la carte


def test_result_button_over_the_bar_found_at_its_place():
    frame = over_the_bar()
    known = find_known_popup(frame, SPECS)
    assert known is not None and known.spec.name == RESULT
    assert near(known.close_point, (1663, 1002), 3) and below_map(frame, known.close_point)


def test_perfect_button_beside_its_column_refused_under_the_map():
    # 25/10 (combat 17) : le faux « Fermer » de la barre était à 1692 (repère de référence) ; le vrai, toujours à 1673-1676.
    frame = moved_button(over_the_bar(), 18)
    assert find_known_popup(frame, SPECS) is None
    same = find_known_popup(moved_button(over_the_bar(), 0), SPECS)
    assert same is not None and near(same.close_point, (1663, 1002), 3)  # le découpage seul ne change rien


def test_result_window_still_opening_has_no_button_to_click():
    # Capture 0374 du journal 181212, 1,8 s avant le clic qui a fait boire la potion : cadre beige vide, titre pas encore
    # dessiné, bouton plus haut que sa place finale. Rien de connu, et aucun bouton générique sous la carte.
    frame = load_frame("resultat_combat_en_train_de_s_ouvrir")
    assert find_known_popup(frame, SPECS) is None
    generic = find_generic_close(frame)
    assert generic is None or not below_map(frame, generic[0])


def test_result_button_at_its_place_under_the_map_is_enough_at_0_877():
    # 26/10 (journal 181017, combat 6) : titre à 0,95, « Fermer » à 0,877 en (1663, 922), à sa place (x de référence
    # 1672,9) ; refusé à BELOW_MAP_BUTTON, la fenêtre restée ouverte a caché le cœur (vie lue à 0 %, repos sans fin).
    frame = load_frame("resultat_combat_fermer_a_0877_sous_la_carte")
    known = find_known_popup(frame, SPECS)
    assert known is not None and known.spec.name == RESULT and near(known.close_point, (1663, 922), 4)
    assert find_known_popup(moved_button(frame, 18, (1663, 922)), SPECS) is None  # le même bouton, pas à sa place : refusé


def test_result_window_loading_empty_is_not_clicked():
    # Même combat, 10 s plus tôt chez Fourbodeux : cadre vide, sans titre ; rien de connu, pas de clic.
    assert find_known_popup(load_frame("resultat_combat_vide_en_chargement_coeur_cache"), SPECS) is None


def test_generic_close_under_the_map_needs_a_sure_button(monkeypatch):
    # Diagnostic d'une fenêtre (seuil 0,75) : à 0,80, « Fermer » a déjà été cru sur un objet de la barre.
    frame = load_frame("hors_combat")
    bar_point = (1682, 1003)
    score = [0.80]

    def fake_match(frame, name, box=None, threshold=0.85):
        return (bar_point, score[0]) if name == "fin_de_combat_fermer.png" and score[0] >= threshold else None

    monkeypatch.setattr(perception_popups, "match_template", fake_match)
    monkeypatch.setattr(perception_popups, "find_title_cross", lambda frame, window: None)
    window = (537, 300, 1893, 823)
    assert find_generic_close(frame, window) is None
    score[0] = BELOW_MAP_BUTTON + 0.05
    assert find_generic_close(frame, window) == (bar_point, "fin_de_combat_fermer.png")


def test_popup_closer_clicks_a_button_under_the_map_only_once_still():
    screen = Screen()
    first = over_the_bar()
    opening = member("A", screen, load_frame("coeur_43pv"))  # 2e capture : « Fermer » ailleurs (fenêtre qui bouge)
    assert opening.popups.handle(first, 0) is True and events(screen, "click") == []
    screen.events.clear()
    still = member("B", screen, first)  # 2e capture : au même endroit
    assert still.popups.handle(first, 0) is True
    ((_, _, point),) = events(screen, "click")
    assert near(point, (1663, 1002), 50)  # dans le bouton (clic au hasard sur sa largeur)


def test_still_open_needs_the_same_window_at_the_same_place():
    screen = Screen()
    first = over_the_bar()
    known = find_known_popup(first, SPECS)
    assert still_open(member("A", screen, first).game, known, SPECS, first) is not None
    assert still_open(member("B", screen, load_frame("hors_combat")).game, known, SPECS, first) is None  # partie
    assert still_open(member("C", screen, moved_button(first, 18)).game, known, SPECS, first) is None


def test_result_not_clicked_where_its_button_is_not_at_the_watchers_place():
    # Même combat, même fenêtre : chez un suiveur, un « Fermer » ailleurs qu'à la place vue immobile chez le principal,
    # c'est une fenêtre qui s'ouvre encore (ou un faux bouton) : pas de clic, le tour des fenêtres la fermera.
    screen = Screen()
    result = over_the_bar()
    leader = member("A", screen, result)
    elsewhere = member("B", screen, load_frame("coeur_43pv"))  # « Fermer » en (1663, 608), immobile
    same = member("C", screen, result)
    bot = team_bot(leader, elsewhere, same)
    bot._close_results(leader, result, [leader, elsewhere, same])
    assert sorted(e[0] for e in events(screen, "click")) == ["A", "C"]


# Fond beige « sûr » : toujours vérifié sur une capture fraîche


def test_unlock_click_checks_a_fresh_capture():
    screen = Screen()
    free = load_frame("hors_combat")
    m = member("A", screen, dark_like(free))  # depuis la capture de l'appelant, une fenêtre s'est ouverte
    assert flash_close_point(free) is not None
    assert not unlock_click(m.game, free) and events(screen, "click") == []


def test_flash_menu_close_point_checked_on_a_fresh_capture():
    screen = Screen()
    free = load_frame("hors_combat")
    m = member("A", screen, dark_like(free))
    assert close_flash_menu(m.game, free) is False and events(screen, "click") == []


def test_chat_given_back_only_on_a_free_background():
    screen = Screen()
    free = load_frame("hors_combat")
    m = member("A", screen, dark_like(free))
    PositionAsker(m.game)._give_back(free)
    assert events(screen, "click") == []
    m2 = member("B", screen, free)
    PositionAsker(m2.game)._give_back(free)
    ((_, _, point),) = events(screen, "click")
    assert near(point, flash_close_point(free), 2)


# Groupe


def test_accept_reclicks_yes_when_the_first_click_is_lost():
    # 25/10 (journal 201109, 20:12:04) : « Oui » d'Enu-six-[Gal] pas pris, pas de cadre du groupe.
    clicks = []

    def react(game, point, button):
        clicks.append(point)
        if len(clicks) == 2 and inside(point, (1090, 410, 1160, 440)):
            game.screen = "placement_case_ambigue"  # cadre du groupe

    member_game = ScreenGame("groupe_invitation", react, "Enu-six-[Gal] - Dofus Retro v1.49.6")
    assert accept(Player("Enu-six-[Gal]", member_game)) is True
    assert len(clicks) == 2 and all(inside(p, (1090, 410, 1160, 440)) for p in clicks)


def test_invite_cancels_a_pending_invitation_before_clicking_a_name():
    # 25/10 (journal 201109, 20:12:09) : fenêtre « Tu invites Enu-six-[Gal]... / Annuler » restée chez Fourboun ; le clic
    # sur Elseiso n'a ouvert aucun menu.
    def leader_react(game, point, button):
        if button == "key":
            return
        if game.screen == "amis_invitation_envoyee" and inside(point, (1157, 423, 1338, 455)):
            game.screen = "amis_fenetre"  # « Annuler »
        elif game.screen == "amis_fenetre" and inside(point, (1050, 340, 1150, 365)):
            game.screen = "amis_menu_inviter"
        elif game.screen == "amis_menu_inviter" and inside(point, (1150, 525, 1390, 555)):
            game.screen = "amis_invitation_envoyee"
            member_game.screen = "groupe_invitation"

    def member_react(game, point, button):
        if game.screen == "groupe_invitation" and inside(point, (1090, 410, 1160, 440)):
            game.screen = "placement_case_ambigue"
            leader.screen = "amis_fenetre"

    leader = ScreenGame("amis_invitation_envoyee", leader_react, "Gela-[A13] - Dofus Retro v1.49.6")
    member_game = ScreenGame("hors_combat", member_react, "Brawli-[Gal] - Dofus Retro v1.49.6")
    assert invite(Player("Gela-[A13]", leader), Player("Brawli-[Gal]", member_game)) is True
    first = next(action[1] for action in leader.actions if action[0] == "click")
    assert inside(first, (1157, 423, 1338, 455))  # « Annuler » d'abord


def test_failed_acceptance_cancels_the_leaders_invitation():
    def leader_react(game, point, button):
        if button == "key":
            return
        if game.screen == "amis_fenetre" and inside(point, (1050, 340, 1150, 365)):
            game.screen = "amis_menu_inviter"
        elif game.screen == "amis_menu_inviter" and inside(point, (1150, 525, 1390, 555)):
            game.screen = "amis_invitation_envoyee"
            member_game.screen = "groupe_invitation"
        elif game.screen == "amis_invitation_envoyee" and inside(point, (1157, 423, 1338, 455)):
            game.screen = "amis_fenetre"

    leader = ScreenGame("amis_fenetre", leader_react, "Gela-[A13] - Dofus Retro v1.49.6")
    member_game = ScreenGame("hors_combat", lambda *args: None, "Brawli-[Gal] - Dofus Retro v1.49.6")  # « Oui » jamais pris
    assert invite(Player("Gela-[A13]", leader), Player("Brawli-[Gal]", member_game)) is False
    assert len([a for a in member_game.actions if a[0] == "click"]) == 2  # « Oui » recliqué une fois
    last = [action[1] for action in leader.actions if action[0] == "click"][-1]
    assert inside(last, (1157, 423, 1338, 455)) and leader.screen == "amis_fenetre"  # invitation annulée


# Connexion


def test_character_found_on_another_plate_than_a_lookalike(monkeypatch):
    # 25/10 : « Fourbodeux », « Fourbohuit » et « Fourboquatre » sur un même compte ; la plaque la plus ressemblante
    # (un nom voisin) ne doit plus cacher le bon nom, un peu moins ressemblant, sur une autre plaque.
    frame = screen_frame("connexion_choix_personnage")
    view = read_login(frame)
    names = ["Fourboquatre", "Fourboun", "Fourbohuit", "", ""]
    scores = iter([0.93, 0.88, 0.82, 0.1, 0.1])  # une plaque après l'autre, de gauche à droite
    monkeypatch.setattr(login_perception, "text_score", lambda crop, name, size, bold=False: (next(scores), None))
    monkeypatch.setattr(login_perception, "plate_text", lambda frame, view, slot: names[slot] or None)
    assert find_character(frame, view, ["Fourboun"]) == (1, "Fourboun")


def test_wrong_character_in_game_is_reported(titles, caplog):  # noqa: F811
    def react(game, point, button):
        if game.screen == "connexion_choix_personnage" and inside(point, (1180, 780, 1300, 825)):
            game.window.title = "Brawli-[Gal] - Dofus Retro v1.49.6"  # « Jouer » : un autre que celui cliqué
            game.screen = "hors_combat"

    game = ScreenGame("connexion_choix_personnage", react)
    with caplog.at_level(logging.ERROR):
        assert Login(game, ConnectionSettings(), ["Gela-[A13]"]).run() == "Brawli-[Gal]"
    assert "Mauvais personnage en jeu : Brawli-[Gal], alors que Gela-[A13]" in caplog.text


def test_watcher_reports_another_character_after_reconnecting(titles, monkeypatch, caplog):  # noqa: F811
    class OtherLogin:
        def __init__(self, game, settings, wanted):
            self.game = game

        def run(self):
            self.game.window.title = "Autre - Dofus Retro v1.49.6"
            return "Autre"

    monkeypatch.setattr(manager_module, "Login", OtherLogin)
    lost = ScreenGame("hors_combat", lambda *args: None, "Dofus Retro v1.49.6")
    watcher = Watcher(ConnectionSettings(check_every=0, group=False), [Player("Brawli-[Gal]", lost)])
    with caplog.at_level(logging.ERROR):
        watcher.check()
    assert "Reconnexion de Brawli-[Gal] : c'est Autre qui est en jeu" in caplog.text
