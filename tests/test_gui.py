"""Fenêtre de lancement (bot/gui/launch.py), sans affichage : ligne de commande, validation, choix mémorisés,
processus du bot (un petit script Python à sa place), et option --set (bot/config.py)."""

import argparse
import json
import sys
import time
from dataclasses import replace

import pytest

from bot.brain.travel import WORLDS as TRAVEL_WORLDS
from bot.config import apply_overrides, load_settings, parse_override
from bot.brain.memory import READING_MODES
from bot.gui.launch import (IMAGES, MAIN_PATH, READINGS, RESUMES, WORLDS, BotProcess, Choices, build_args,
                            character_names, coords_text, default_choices, deletion_problems, display_command,
                            dungeon_argument, format_override, known_dungeons, load_choices, main_options, overrides,
                            parse_coords, plays_as_team, save_choices, team_names, validate, window_title)
from bot.team.dungeon import STEPS as DUNGEON_STEPS

SETTINGS = {
    "window": {"title": "- Dofus Retro"},
    "combat": {"burst": True},
    "travel": {"mode": "", "region": "Incarnam"},
    "team": {"enabled": False, "leader": "", "turn_signal": "timeline"},
    "timing": {"wait_mode": "fixe"},
}
TITLES = ["Jesuisleking - Dofus Retro v1.49.5", "Walitcoin - Dofus Retro v1.49.5", "Dofus Retro v1.49.5"]


def choices(**changes) -> Choices:
    return replace(default_choices(SETTINGS), **changes)


def coords(text: str) -> tuple[int, int]:  # comme _coords de bot/__main__.py
    x, y = (int(v) for v in text.replace(" ", "").split(","))
    return x, y


def bot_parser() -> argparse.ArgumentParser:
    """Les options de bot/__main__.py utilisées ici (même forme), pour relire les commandes construites."""
    parser = argparse.ArgumentParser(exit_on_error=False)
    for flag in ("--observe", "--combat-only", "--explore", "--zone", "--travel-test", "--equipe", "--notifications",
                 "--verbose"):
        parser.add_argument(flag, action="store_true")
    for option in ("--combat-zone", "--position", "--aller"):
        parser.add_argument(option, type=coords)
    for option in ("--monde", "--title", "--principal", "--lecture"):
        parser.add_argument(option)
    parser.add_argument("--set", action="append", default=[], type=parse_override)
    return parser


# Choix de départ et ligne de commande


def test_default_choices_follow_settings():
    base = default_choices(load_settings())
    assert base.title == "- Dofus Retro" and base.monde in WORLDS
    defaults = default_choices({"travel": {"mode": "aller", "destination": "5,-18", "region": "Monde des Douze"},
                                "team": {"enabled": True, "leader": "Walitcoin", "turn_signal": "notifications"},
                                "engage": {"enabled": False}, "timing": {"wait_mode": "Condition"}})
    assert defaults == Choices(mode="aller", aller_to="5,-18", monde="douze", team=True, leader="Walitcoin",
                               notifications=True, combat_only=True, wait_mode="condition")
    assert default_choices({}) == Choices()
    assert default_choices({"window": {"ecran": 1}}).screen == "1"
    assert default_choices({"window": {"ecran": None}}).screen == "aucun"


def test_screen_choice():
    assert build_args(Choices(screen="1"), {"window": {"ecran": "auto"}})[-2:] == ["--ecran", "1"]
    assert "--ecran" not in build_args(Choices(), {})  # celui de config/settings.yaml
    assert validate(Choices(screen="2"), {}, screens=2) == []
    assert validate(Choices(screen="3"), {}, screens=2) == ["L'écran 3 n'est plus branché (2 écran(s)) : "
                                                            "choisis-en un autre"]
    assert validate(Choices(screen="gauche"), {}) == ["Écran inconnu : « gauche »"]


def test_foreground_when_background_is_unchecked():
    settings = {"window": {"arriere_plan": True}}
    assert "--set" not in build_args(Choices(background=True), settings)
    args = build_args(Choices(background=False), settings)
    assert args[args.index("--set") + 1] == "window.arriere_plan=false"


def test_characters_from_the_interface():
    settings = {"connexion": {"personnages": ["Gela-[A13]"]}}
    assert default_choices(settings).characters == "Gela-[A13]"
    args = build_args(Choices(connect=True, characters="Gela-[A13],Brawli-[Gal]"), settings)
    assert args[args.index("--personnages") + 1] == "Gela-[A13],Brawli-[Gal]"
    # Toujours transmis, même celui du réglage et sans connexion : ils limitent l'équipe jouée (connection_settings
    # de bot/__main__.py, qui en fait team.members).
    assert "--personnages" in build_args(Choices(connect=True, characters="Gela-[A13]"), settings)
    args = build_args(Choices(characters="Gela-[A13],X"), settings)
    assert args[args.index("--personnages") + 1] == "Gela-[A13],X" and "--connexion" not in args
    assert "--personnages" not in build_args(Choices(), settings)


def test_team_follows_the_characters():
    # Plus de case « Mode équipe » : en équipe dès 2 personnages cochés, sinon (aucun coché) dès 2 fenêtres ouvertes.
    assert plays_as_team("Gela-[A13],Brawli-[Gal]", [])
    assert plays_as_team("", ["Jesuisleking", "Walitcoin"])
    assert not plays_as_team("", ["Jesuisleking"])
    assert not plays_as_team("Walitcoin", ["Jesuisleking", "Walitcoin"])  # un seul coché : l'équipe limitée à lui
    assert team_names("", ["Jesuisleking", "Walitcoin"]) == ("Jesuisleking", "Walitcoin")
    errors = validate(choices(team=True, leader="Walitcoin", characters="Walitcoin,Autre"), SETTINGS, TITLES)
    assert errors == ["Sans fenêtre ouverte : Autre : décoche-les, ou coche « Connexion automatique »"]
    assert validate(choices(team=True, leader="Walitcoin", characters="Walitcoin,Autre", connect=True), SETTINGS,
                    TITLES) == []


def test_observation_with_connection_is_reported():
    assert validate(choices(observe=True, connect=True), SETTINGS) == [
        "Observation : aucun clic, la connexion automatique serait ignorée : décoche l'une des deux"]
    assert validate(choices(observe=True), SETTINGS) == []


def test_session_limits():
    assert "--combats" not in build_args(choices(), SETTINGS) and "--minutes" not in build_args(choices(), SETTINGS)
    args = build_args(choices(combats=" 3 ", minutes="1,5"), SETTINGS)
    assert args[args.index("--combats") + 1] == "3" and args[args.index("--minutes") + 1] == "1.5"
    assert validate(choices(combats="3", minutes="90"), SETTINGS, options=main_options()) == []
    assert validate(choices(combats="0"), SETTINGS) == [
        "S'arrêter après : nombre de combats attendu (1, 2, 3...), pas « 0 »"]
    assert validate(choices(combats="2.5"), SETTINGS)[0].startswith("S'arrêter après : nombre de combats")
    for wrong in ("0", "-1", "abc", "inf", "nan"):
        assert validate(choices(minutes=wrong), SETTINGS) == [
            f"S'arrêter après : nombre de minutes attendu (30, 1,5...), pas « {wrong} »"], wrong
    assert {"--combats", "--minutes"} <= main_options()


def test_notifications_only_when_not_default():
    team = {"team": True, "leader": "Jesuisleking", "notifications": True}
    on = {**SETTINGS, "team": {"enabled": False, "turn_signal": "notifications"}}
    assert "--notifications" in build_args(choices(**team), SETTINGS)  # réglage : timeline
    assert "--notifications" not in build_args(replace(default_choices(on), **team), on)  # déjà le réglage
    assert "team.turn_signal=timeline" in build_args(replace(default_choices(on), **{**team, "notifications": False}),
                                                     on)
    assert "--notifications" not in build_args(choices(notifications=True), SETTINGS)  # solo : sans objet


def test_title_comes_from_settings(tmp_path):
    # Plus de champ « Titre des fenêtres » : window.title de config/settings.yaml, jamais celui de data/gui.json.
    settings = {**SETTINGS, "window": {"title": "- Dofus Retro v1"}}
    defaults = default_choices(settings)
    assert defaults.title == "- Dofus Retro v1"
    path = tmp_path / "gui.json"
    path.write_text('{"title": "- Autre jeu", "verbose": true}', encoding="utf-8")
    assert load_choices(path, defaults) == replace(defaults, verbose=True)
    save_choices(replace(defaults, title="- Autre jeu"), path)
    assert "title" not in json.loads(path.read_text(encoding="utf-8"))
    assert "--title" not in build_args(defaults, settings)


def test_images_override():
    assert default_choices({"logs": {"images": "Problèmes"}}).images == "problemes"
    assert default_choices({}).images == "toutes" and tuple(IMAGES) == ("toutes", "problemes", "aucune")
    assert overrides(choices(), SETTINGS) == []
    args = build_args(choices(images="aucune"), SETTINGS)
    assert args[-2:] == ["--set", "logs.images=aucune"]
    assert apply_overrides({"logs": {"images": "toutes"}}, [parse_override(args[-1])]) == {"logs": {"images": "aucune"}}
    few = {**SETTINGS, "logs": {"images": "problemes"}}
    assert "--set" not in build_args(default_choices(few), few)
    assert validate(choices(images="quelques"), SETTINGS) == [
        "Captures inconnues : 'quelques' (toutes, problemes, aucune)"]


def test_dungeon_resume_argument():
    # Reprise du farm de donjon (bot/__main__.py, --donjon) : NOM:ETAPE, ou NOM:salles:N.
    assert tuple(RESUMES) == ("", *DUNGEON_STEPS, "salles")
    team = {"team": True, "leader": "Koalaloupour", "mode": "donjon", "donjon": "incarnam"}
    assert dungeon_argument(choices(**team)) == "incarnam"
    assert dungeon_argument(choices(**team, resume="dalle")) == "incarnam:dalle"
    assert dungeon_argument(choices(**team, resume="salles", resume_room=" 3 ")) == "incarnam:salles:3"
    args = build_args(choices(**team, resume="soleil", passages="2"), SETTINGS)
    assert args[2:6] == ["--donjon", "incarnam:soleil", "--passages", "2"]
    assert validate(choices(**team, resume="salles", resume_room="2"), SETTINGS) == []
    assert validate(choices(**team, resume="salles", resume_room="0"), SETTINGS) == [
        "Farm de donjon : numéro de la salle attendu (1, 2, 3...), pas « 0 »"]
    assert validate(choices(**team, resume="porte"), SETTINGS)[0].startswith("Farm de donjon : reprise inconnue")
    assert dungeon_argument(choices(mode="carte", resume="dalle")) == ":dalle"  # pas utilisé hors du farm de donjon
    assert "--donjon" not in build_args(choices(resume="dalle"), SETTINGS)


def test_deletion_problems():
    settings = {"connexion": {"personnages": ["Gela-[A13]"]}, "team": {"members": "Brawli-[Gal]", "leader": "Chef"}}
    assert deletion_problems("Vieux", [], [], "", False, settings) == []
    assert deletion_problems("vieux", ["Vieux"], [], "", False, {}) == ["« vieux » est coché : décoche-le d'abord"]
    assert deletion_problems("Vieux", [], ["VIEUX"], "", False, {}) == ["« Vieux » a une fenêtre du jeu ouverte"]
    assert deletion_problems("Vieux", [], [], "vieux", False, {}) == [
        "« Vieux » est le personnage principal : choisis-en un autre"]
    assert deletion_problems("Vieux", [], [], "", True, {}) == ["Le bot tourne : arrête-le d'abord"]
    assert deletion_problems("gela-[a13]", [], [], "", False, settings) == [
        "« gela-[a13] » figure dans config/settings.yaml (connexion.personnages) : retire-le de config/settings.yaml"]
    assert "(team.members)" in deletion_problems("Brawli-[Gal]", [], [], "", False, settings)[0]
    assert "(team.leader)" in deletion_problems("chef", [], [], "", False, settings)[0]
    assert len(deletion_problems("Chef", ["Chef"], ["Chef"], "Chef", True, settings)) == 5


def test_worlds_match_travel():
    assert WORLDS == TRAVEL_WORLDS


def test_coordinates():
    assert parse_coords(" 5 , -18 ") == (5, -18)
    assert parse_coords("-1,-20") == (-1, -20)
    for wrong in ("", "5", "5,", "a,b", "1.5,2", "1,2,3"):
        assert parse_coords(wrong) is None
    assert coords_text(" 5 , -18 ") == "5,-18"


def test_combat_on_the_map_by_default():
    assert build_args(choices(), SETTINGS) == ["--monde", "incarnam"]


def test_no_combat_style_any_more():
    # 03/10 : un seul style de combat, polyvalent. Plus de --style dans la commande ; un vieux réglage combat.style ou
    # un vieux data/gui.json qui en a un ne gênent pas.
    old = {**SETTINGS, "combat": {"style": "prudent", "burst": True}}
    assert "--style" not in build_args(default_choices(old), old) and overrides(default_choices(old), old) == []
    assert not hasattr(Choices(), "style")
    assert "--style" in main_options()  # encore accepté par le bot (anciennes commandes), ignoré avec un avertissement


def test_mode_arguments():
    assert build_args(choices(mode="zone", zone_xy="1, 1"), SETTINGS)[2] == "--combat-zone=1,1"
    assert build_args(choices(mode="zone"), SETTINGS)[2] == "--zone"
    assert build_args(choices(mode="aller", aller_from="3,4", aller_to="5,6"), SETTINGS) == [
        "--monde", "incarnam", "--position=3,4", "--aller=5,6"]
    assert build_args(choices(mode="aller", aller_to="5,6"), SETTINGS)[2:] == ["--aller=5,6"]
    assert build_args(choices(mode="explore", explore_from="1,6"), SETTINGS)[2:4] == ["--explore", "--position=1,6"]
    assert build_args(choices(mode="explore"), SETTINGS)[2:] == ["--explore"]
    assert build_args(choices(mode="test"), SETTINGS)[2] == "--travel-test"


def test_dungeon_farm_arguments():
    # Utilisateur, 18/10 : choisir le donjon dans une liste des donjons connus (config/donjons_entree.yaml).
    assert known_dungeons()["incarnam"] == ("Donjon d'Incarnam", "incarnam")
    team = {"team": True, "leader": "Koalaloupour", "mode": "donjon", "donjon": "incarnam"}
    assert build_args(choices(**team), SETTINGS)[2:4] == ["--donjon", "incarnam"]
    assert build_args(choices(**team, passages="3"), SETTINGS)[2:6] == ["--donjon", "incarnam", "--passages", "3"]
    zone = {**SETTINGS, "travel": {"mode": "zone", "region": "Incarnam"}}  # pas de changement de carte au donjon
    assert ("travel.mode", "") in overrides(choices(**team), zone)


def test_dungeon_farm_errors():
    team = {"team": True, "leader": "Koalaloupour", "mode": "donjon"}
    assert validate(choices(**team, donjon="incarnam"), SETTINGS) == []
    assert validate(choices(**team), SETTINGS) == ["Farm de donjon : choisis le donjon"]
    # 07/11 : l'Ensablé est décrit ; un donjon pas encore décrit (Tofus) est refusé.
    assert "n'est pas dans config/donjons_entree.yaml" in validate(choices(**team, donjon="tofus"), SETTINGS)[0]
    assert validate(choices(**team, donjon="bouftou", monde="douze"), SETTINGS) == []  # 30/10 : entrée décrite
    assert validate(choices(**team, donjon="incarnam", monde="douze"), SETTINGS)[0].endswith("choisis-le dans Monde")
    assert validate(choices(mode="donjon", donjon="incarnam"), SETTINGS) == [
        "Farm de donjon : en équipe seulement (coche au moins 2 personnages, ou ouvre leurs fenêtres)"]
    assert validate(choices(**team, donjon="incarnam", passages="0"), SETTINGS)[0].startswith(
        "Farm de donjon : nombre de passages")


def test_negative_coordinates_are_joined_by_equal_sign():
    # Séparée, « -1,-20 » passerait pour une option : argparse refuserait la commande.
    with pytest.raises(argparse.ArgumentError):
        bot_parser().parse_args(["--position", "-1,-20"])
    travel = choices(mode="aller", aller_from="12,-28", aller_to="-1, -20", monde="douze")
    args = build_args(travel, SETTINGS)
    assert args == ["--monde", "douze", "--position=12,-28", "--aller=-1,-20"]
    assert display_command(args).endswith("-m bot --monde douze --position=12,-28 --aller=-1,-20")
    parsed = bot_parser().parse_args(args)
    assert parsed.position == (12, -28) and parsed.aller == (-1, -20)
    back = build_args(replace(travel, aller_from="-1,-20", aller_to="12,-28"), SETTINGS)
    assert bot_parser().parse_args(back).position == (-1, -20)
    zone = build_args(choices(mode="zone", zone_xy="-1,-20"), SETTINGS)
    assert "--combat-zone=-1,-20" in zone and bot_parser().parse_args(zone).combat_zone == (-1, -20)
    explore = build_args(choices(mode="explore", explore_from="12,-28"), SETTINGS)
    assert "--position=12,-28" in explore


def test_combat_only_and_observe():
    assert "--combat-only" in build_args(choices(mode="zone", zone_xy="1,1", combat_only=True), SETTINGS)
    assert "--combat-only" not in build_args(choices(mode="explore", combat_only=True), SETTINGS)
    assert "--observe" in build_args(choices(mode="aller", aller_to="1,1", observe=True), SETTINGS)


def test_team_arguments():
    args = build_args(choices(team=True, leader="Jesuisleking", notifications=True, verbose=True), SETTINGS)
    assert args == ["--monde", "incarnam", "--equipe", "--principal", "Jesuisleking", "--notifications", "--verbose"]


def test_solo_with_a_chosen_character_targets_its_window():
    args = build_args(choices(leader="Walitcoin"), SETTINGS)
    assert args[args.index("--title") + 1] == "Walitcoin - Dofus Retro"
    assert window_title("Walitcoin", "Dofus Retro") == "Walitcoin - Dofus Retro"
    assert "--title=- Retro" in build_args(choices(title="- Retro"), SETTINGS)  # tiret en tête : forme --title=


def test_settings_without_option_go_through_set_only_when_changed():
    assert overrides(choices(), SETTINGS) == []
    changed = choices(burst=False, wait_mode="condition")
    assert overrides(changed, SETTINGS) == [("combat.burst", False), ("timing.wait_mode", "condition")]
    args = build_args(changed, SETTINGS)
    assert args[-4:] == ["--set", "combat.burst=false", "--set", "timing.wait_mode=condition"]
    on = {**SETTINGS, "team": {"enabled": True, "turn_signal": "notifications"}, "travel": {"mode": "zone"},
          "engage": {"enabled": False}}
    assert overrides(choices(team=False), on) == [("travel.mode", ""), ("engage.enabled", True),
                                                  ("team.enabled", False)]
    assert ("team.turn_signal", "timeline") in overrides(choices(team=True, notifications=False), on)


def test_set_values_read_back_like_settings():
    parsed = bot_parser().parse_args(build_args(
        choices(burst=False, wait_mode="condition"), {**SETTINGS, "travel": {"mode": "explore"}}))
    settings = apply_overrides({"combat": {"burst": True, "style": "prudent"}, "travel": {"mode": "explore"}},
                               parsed.set)
    assert settings == {"combat": {"burst": False, "style": "prudent"}, "travel": {"mode": ""},
                        "timing": {"wait_mode": "condition"}}
    assert parse_override(format_override("team.enabled", True)) == (("team", "enabled"), True)
    assert parse_override("combat.spell.range=[1, 7]") == (("combat", "spell", "range"), [1, 7])
    for wrong in ("burst", "=true", "combat..burst=true", "combat.burst=[1,"):
        with pytest.raises(ValueError):
            parse_override(wrong)


def test_session_start_reading_mode():
    # 04/10 : contrôler (lire sorts et caractéristiques) ou reprendre la dernière session ; --lecture seulement s'il
    # diffère de session.mode de config/settings.yaml.
    assert tuple(READINGS) == READING_MODES
    assert choices().reading == "controle" and "--lecture" not in build_args(choices(), SETTINGS)
    args = build_args(choices(reading="memoire", verbose=True), SETTINGS)
    assert args[-3:] == ["--lecture", "memoire", "--verbose"] and bot_parser().parse_args(args).lecture == "memoire"
    remembered = {**SETTINGS, "session": {"mode": "Mémoire"}}  # accent toléré, comme pour le bot
    assert default_choices(remembered).reading == "memoire"
    assert build_args(default_choices(remembered), remembered) == ["--monde", "incarnam"]
    assert build_args(choices(reading="controle"), remembered)[-2:] == ["--lecture", "controle"]
    assert validate(choices(reading="memoire"), SETTINGS, TITLES, main_options()) == []
    assert validate(choices(reading="souvenir"), SETTINGS) == ["Début de session inconnu : 'souvenir' (controle ou "
                                                               "memoire)"]
    assert "--lecture" in main_options()


def test_display_command_is_copyable():
    text = display_command(build_args(choices(title="Mon - Dofus Retro"), SETTINGS))
    assert text.startswith("python -m bot ") or text.startswith(r".venv\Scripts\python -m bot ")
    assert '--title "Mon - Dofus Retro"' in text


# Validation


def test_valid_choices():
    assert validate(choices(), SETTINGS, TITLES, main_options()) == []
    assert validate(choices(mode="aller", aller_from="-1,-20", aller_to="5,-18"), SETTINGS, TITLES) == []
    assert validate(choices(team=True, leader="walitcoin"), SETTINGS, TITLES) == []


def test_coordinate_errors():
    assert validate(choices(mode="zone", zone_xy="1;1"), SETTINGS) == [
        "Combat en zone : coordonnées de la carte attendues sous la forme X,Y en nombres entiers (ex. 5,-18), "
        "pas « 1;1 »"]
    assert validate(choices(mode="aller", aller_from="2,3"), SETTINGS) == [
        "Déplacement : coordonnées d'arrivée manquantes (X,Y, ex. 5,-18)"]
    assert "l'arrivée est la carte de départ" in validate(choices(mode="aller", aller_from="2,3", aller_to=" 2, 3"),
                                                         SETTINGS)[0]
    assert validate(choices(mode="explore", explore_from="x"), SETTINGS)[0].startswith("Exploration : coordonnées")
    assert validate(choices(mode="carte", zone_xy="mauvais"), SETTINGS) == []  # champ d'un autre mode : ignoré


def test_harvest_mode_is_gone():
    # Plus de mode « Récolte » (jamais programmé) ; un vieux data/gui.json qui l'a gardé revient au défaut.
    assert validate(choices(mode="recolte"), SETTINGS) == ["Mode inconnu : 'recolte'"]


def test_team_errors():
    assert validate(choices(team=True), SETTINGS) == ["Jeu en équipe : choisis le personnage principal"]
    assert "n'a pas de fenêtre ouverte (personnages : Jesuisleking, Walitcoin)" in validate(
        choices(team=True, leader="Autre"), SETTINGS, TITLES)[0]
    assert validate(choices(team=True, leader="Autre"), SETTINGS, ["Dofus Retro v1.49.5"])[0].startswith(
        "Jeu en équipe : aucune fenêtre de personnage ouverte")
    assert validate(choices(), SETTINGS, [])[0].startswith("Aucune fenêtre du jeu ouverte")
    assert validate(choices(), SETTINGS, ["Dofus Retro v1.49.5"]) == []  # solo : l'écran de connexion suffit
    assert validate(choices(title=" "), SETTINGS)[0].startswith("Titre des fenêtres du jeu vide")


def test_missing_options_of_the_bot_are_reported():
    old = frozenset({"--monde", "--position"})
    assert validate(choices(mode="aller", aller_to="1,1"), SETTINGS, options=old) == [
        "L'option --aller n'existe pas encore dans bot/__main__.py"]
    errors = validate(choices(burst=False), SETTINGS, options=old)
    assert errors == ["Réglages différents de config/settings.yaml (combat.burst=false) : l'option --set n'existe pas "
                      "encore dans bot/__main__.py ; en attendant, change-les dans config/settings.yaml"]


def test_main_options_are_read_from_the_code(tmp_path):
    options = main_options()
    assert {"--combat-zone", "--zone", "--explore", "--aller", "--position", "--monde", "--equipe",
            "--principal"} <= options
    assert main_options(tmp_path / "absent.py") is None
    assert MAIN_PATH.exists()


# Personnages et choix mémorisés


def test_character_names():
    titles = TITLES + ["jesuisleking - Dofus Retro v1.49.5", "Autre - Dofus Retro"]
    assert character_names(titles, "- Dofus Retro") == ["Autre", "Jesuisleking", "Walitcoin"]
    assert character_names([], "- Dofus Retro") == []


def test_choices_are_saved_and_reloaded(tmp_path):
    path = tmp_path / "data" / "gui.json"
    chosen = choices(mode="aller", aller_from="-1,-20", aller_to="5,-18", team=True, leader="Walitcoin",
                     burst=False, monde="douze", reading="memoire")
    save_choices(chosen, path)
    assert load_choices(path, default_choices(SETTINGS)) == chosen


def test_bad_saved_choices_fall_back_to_defaults(tmp_path):
    path = tmp_path / "gui.json"
    defaults = default_choices(SETTINGS)
    assert load_choices(path, defaults) == defaults  # pas de fichier
    path.write_text("{abîmé", encoding="utf-8")
    assert load_choices(path, defaults) == defaults
    path.write_text('{"mode": "recolte", "monde": "lune", "burst": "oui", "verbose": true, "inconnu": 1, '
                    '"style": "prudent", "reading": "souvenir"}',
                    encoding="utf-8")
    assert load_choices(path, defaults) == replace(defaults, verbose=True)


# Processus du bot : un petit script Python à sa place (ni le bot, ni le jeu)


def wait_for(condition, seconds: float = 10.0) -> None:
    end = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < end, "délai dépassé"
        time.sleep(0.05)


def test_process_output_is_read_line_by_line():
    runner = BotProcess(send_key=lambda key: pytest.fail("aucune touche à envoyer"))
    runner.start([sys.executable, "-c", "print('Prêt : é à ç'); print('fin')"])
    lines, codes = [], []

    def finished() -> bool:
        codes.append(runner.check())
        lines.extend(runner.lines())
        return codes[-1] is not None

    wait_for(finished)
    assert lines == ["Prêt : é à ç", "fin"] and codes[-1] == 0
    assert not runner.running and runner.check() is None


def test_stop_key_then_forced_end():
    keys, now = [], [0.0]
    runner = BotProcess(send_key=keys.append, clock=lambda: now[0], grace=5.0)
    runner.start([sys.executable, "-c", "import time; print('en marche', flush=True); time.sleep(60)"])
    wait_for(lambda: "en marche" in runner.lines())
    runner.request_stop("f12")
    assert keys == ["f12"] and runner.stopping
    assert runner.check() is None  # le script ignore F12 : il tourne encore
    now[0] = 6.0  # délai dépassé : processus terminé
    codes = []
    wait_for(lambda: codes.append(runner.check()) or codes[-1] is not None)
    assert codes[-1] != 0 and any("terminé de force" in line for line in runner.lines())


def test_second_stop_request_ends_at_once():
    keys = []
    runner = BotProcess(send_key=keys.append)
    runner.start([sys.executable, "-c", "import time; time.sleep(60)"])
    runner.request_stop("f12")
    runner.request_stop("f12")
    assert keys == ["f12"]
    codes = []
    wait_for(lambda: codes.append(runner.check()) or codes[-1] is not None)
    assert any("deuxième demande" in line for line in runner.lines())


def test_zaap_mode_unchecked_is_passed_to_the_bot():
    # 25/10, l'utilisateur : l'utilisation des zaaps doit être un mode avec ou sans, dans l'interface.
    assert choices().zaap  # par défaut : avec zaap (travel.zaap absent de la config)
    changed = choices(zaap=False)
    assert ("travel.zaap", False) in overrides(changed, SETTINGS)
    args = build_args(changed, SETTINGS)
    assert "travel.zaap=false" in args and args[args.index("travel.zaap=false") - 1] == "--set"
    assert "travel.zaap=true" not in build_args(choices(), SETTINGS)


def test_bank_mode_unchecked_is_passed_to_the_bot():
    # 26/10, l'utilisateur : une case pour autoriser ou non l'aller-retour à la banque quand les pods sont pleins.
    assert choices().bank
    assert ("team.banque_auto", False) in overrides(choices(bank=False), SETTINGS)


def test_user_alert_lines_are_recognised():
    # 31/10 : messages du bot pour l'utilisateur (clés qui manquent), montrés aussi dans une fenêtre par l'interface.
    from bot.alerts import alert_text

    assert alert_text("12:00:00.000 WARNING [Fourboun] À l'utilisateur : Donjon Bouftou : pas assez de clés.") == \
        "Donjon Bouftou : pas assez de clés."
    assert alert_text("12:00:00.000 INFO    [Fourboun] Donjon : passage 1/1") is None
