"""Caractéristiques du personnage (04/10) : lecture des fenêtres « Tes caractéristiques » et « Caracteristiques
avancées » (captures de l'utilisateur collées sur une capture plein écran), dégâts des sorts avec les
caractéristiques, déroulé en début de session (faux jeu : clics, Échap), mémoire des personnages entre les sessions."""

import json
import logging

import cv2
import numpy as np
import pytest

from bot.brain.combat import CombatAI, CombatSettings
from bot.brain.memory import CharacterMemory, SessionSettings, entry_of, entry_record, reading_mode, when
from bot.brain.spellbook import SpellBook, load_reference, mean_damage
from bot.brain.stats import CharacterStats, StatsReader
from bot.brain.tactics import CombatSpell, SpellUse, spell_range
from bot.config import load_settings
from bot.perception import stats as perception
from bot.perception.frame import Frame
from bot.perception.spellbook import read_spell_window
from conftest import FIXTURES, load_frame, whole_list

SIMPLE_AT, ADVANCED_AT, SPELLS_AT = (1300, 60), (600, 50), (640, 40)
WALITCOIN = {"vitalite": 52, "sagesse": 0, "force": 80, "intelligence": 50, "chance": 0, "agilite": 7, "pa": 6,
             "pm": 3, "portee": 0, "invocations": 2, "dommages": 4, "dommages_physiques": 0, "maitrise_arme": 0,
             "dommages_pourcent": 8}


def crop(name: str) -> np.ndarray:
    image = cv2.imread(str(FIXTURES / f"{name}.png"))
    assert image is not None, name
    return image


def paste(image: np.ndarray, window: np.ndarray, at: tuple[int, int]) -> np.ndarray:
    x, y = at
    image[y : y + window.shape[0], x : x + window.shape[1]] = window
    return image


def screen(simple: bool = False, advanced: bool = False, spells: bool = False,
           advanced_window: np.ndarray | None = None) -> Frame:
    """Capture hors combat (barre d'icônes visible), avec les fenêtres demandées collées dessus."""
    image = load_frame("hors_combat").image.copy()
    if spells:
        paste(image, whole_list(crop("fenetre_sorts")), SPELLS_AT)  # rien à faire défiler
    if simple:
        paste(image, crop("caracteristiques"), SIMPLE_AT)
    if advanced:
        paste(image, crop("caracteristiques_avancees") if advanced_window is None else advanced_window, ADVANCED_AT)
    return Frame.of(image)


# Perception


def test_stats_button_in_the_icon_bar():
    x, y = perception.find_stats_button(load_frame("hors_combat"))
    assert abs(x - 1457) <= 3 and abs(y - 820) <= 3  # 1er bouton orange, à gauche de la baguette des sorts
    assert perception.find_stats_window(load_frame("hors_combat")) is None
    assert perception.find_advanced_window(load_frame("hors_combat")) is None


def test_user_crops_of_the_buttons_match():
    # Découpes faites à part par l'utilisateur : le bouton de la barre, l'épée de la fenêtre (survolée ? 0,93).
    image = np.zeros_like(load_frame("hors_combat").image)
    paste(image, crop("caracteristiques_bouton"), (1428, 790))
    x, y = perception.find_stats_button(Frame.of(image))
    assert abs(x - 1457) <= 2 and abs(y - 821) <= 2
    window = crop("caracteristiques")
    score = cv2.minMaxLoc(cv2.matchTemplate(window, crop("caracteristiques_epee"), cv2.TM_CCOEFF_NORMED))[1]
    assert score >= perception.SWORD_THRESHOLD


def test_advanced_window_total_column():
    frame = screen(simple=True, advanced=True)
    assert perception.find_advanced_window(frame) == ADVANCED_AT
    reading = perception.read_advanced_window(frame)
    assert reading.source == "avancées" and reading.values == WALITCOIN
    assert reading.missing(perception.REQUIRED) == []


def test_simple_window_sums_base_and_equipment():
    frame = screen(simple=True)
    assert perception.find_stats_window(frame) == SIMPLE_AT
    sword = perception.find_sword_button(frame)
    assert abs(sword[0] - (SIMPLE_AT[0] + 406)) <= 2 and abs(sword[1] - (SIMPLE_AT[1] + 485)) <= 2
    values = perception.read_stats_window(frame).values
    # « Force 0 (+80) » : 80 ; « Sagesse 0 » : 0 ; PA et PM en gras.
    assert values == {"pa": 6, "pm": 3, "portee": 0, "invocations": 2, "vitalite": 52, "sagesse": 0, "force": 80,
                      "intelligence": 50, "chance": 0, "agilite": 7}


def test_sword_clicked_on_the_sword_itself_in_a_1057_window():
    # 17/10, fenêtre de 1920 x 1057 : clic visé au milieu du modèle (vers x 1861), donc souvent sur le fond à gauche de
    # l'épée, et la fenêtre avancée ne s'ouvrait pas (capture de l'un de ces échecs). Tout le carré du clic doit être
    # sur l'épée, à droite de x 1862 (en deçà : 10 clics sur 10 sans effet ; au-delà : 14 sur 14 ouverts).
    frame = load_frame("caracteristiques_1057")
    assert perception.find_advanced_window(frame) is None
    corner = perception.find_stats_window(frame)
    assert corner is not None
    x, y = perception.find_sword_button(frame, corner)
    assert abs(x - 1871) <= 2 and abs(y - 560) <= 2
    rx, ry = (round(v) for v in frame.layout.spread(*perception.SWORD_SPREAD))
    assert x - rx >= 1862
    background = frame.image[560, 1845].astype(int)  # fond de la barre « Caractéristiques », à gauche de l'épée
    for dx in (-rx, 0, rx):
        for dy in (-ry, 0, ry):
            assert np.abs(frame.image[y + dy, x + dx].astype(int) - background).sum() > 60, (dx, dy)


def test_rows_found_by_their_label_when_the_list_is_scrolled():
    window = crop("caracteristiques_avancees")
    scrolled = window.copy()
    scrolled[115 : 728 - 73] = window[188:728]  # liste défilée de 2 lignes : Force en premier
    reading = perception.read_advanced_window(screen(advanced=True, advanced_window=scrolled))
    assert reading.values["vitalite"] is None and reading.values["sagesse"] is None
    assert {k: reading.values[k] for k in perception.REQUIRED} == {k: WALITCOIN[k] for k in perception.REQUIRED}


def test_unreadable_digit_is_reported_missing():
    window = crop("caracteristiques_avancees")
    blurred = window.copy()
    blurred[200:214, 640:660] = cv2.GaussianBlur(window[200:214, 640:660], (9, 9), 4)  # Total de la Force brouillé
    reading = perception.read_advanced_window(screen(advanced=True, advanced_window=blurred))
    assert reading.missing(perception.REQUIRED) == ["force"]


@pytest.mark.parametrize("factor", [0.9, 1.1])
def test_windows_read_in_a_smaller_or_larger_game_window(factor):
    frame = screen(simple=True, advanced=True)
    scaled = Frame.of(cv2.resize(frame.image, None, fx=factor, fy=factor,
                                 interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_LINEAR))
    assert perception.read_advanced_window(scaled).values == WALITCOIN


def test_values_and_signs():
    assert perception.parse_value(["0", "(+80)"]) == 80
    assert perception.parse_value(["12", "(-5)"]) == 7
    assert perception.parse_value(["-"]) == 0  # case vide du tableau
    assert perception.parse_value(["-15"]) == -15
    assert perception.parse_value(["52"]) == 52
    for wrong in ([], ["?"], ["5?"], ["0", "+80"], ["0", "(+8?)"], ["1", "(+2)", "3"]):
        assert perception.parse_value(wrong) is None


# Dégâts


def level_of(key: str, level: int) -> dict:
    spell = next(s for s in load_reference()["sorts"].values() if s["cle"] == key)
    return spell["niveaux"][level - 1]


def test_user_example_magic_arrow_with_100_intelligence():
    effects = level_of("fleche_magique", 4)["effets"]  # 12 à 14 (feu)
    assert mean_damage(effects) == 13
    assert mean_damage(effects, CharacterStats(intelligence=100)) == pytest.approx(26)  # +100 %
    assert mean_damage(effects, CharacterStats(force=100, chance=100, agilite=100)) == 13  # pas l'élément du sort


def test_formula_with_percent_and_fixed_bonus_on_each_damage_line():
    walitcoin = CharacterStats(force=80, intelligence=50, agilite=7, dommages=4, dommages_pourcent=8)
    assert walitcoin.boost(13, "feu") == pytest.approx(13 * 1.58 + 4)  # Flèche Magique : 24,5
    assert walitcoin.boost(10, "terre") == walitcoin.boost(10, "neutre") == pytest.approx(10 * 1.88 + 4)
    assert walitcoin.boost(10, "eau") == pytest.approx(10 * 1.08 + 4)
    assert walitcoin.boost(10, "air") == pytest.approx(10 * 1.15 + 4)
    persecutrice = level_of("fleche_persecutrice", 1)["effets"]  # 2 à 4 feu + 2 à 4 air
    assert mean_damage(persecutrice, walitcoin) == pytest.approx(3 * 1.58 + 4 + 3 * 1.15 + 4)  # +4 sur chaque ligne
    drain = [{"id": 94, "min": 10, "max": 10, "element": "feu"}]  # vol de vie feu : même formule
    assert mean_damage(drain, walitcoin) == pytest.approx(10 * 1.58 + 4)
    weak = CharacterStats(agilite=-150)
    assert weak.boost(10, "air") == 0  # jamais de dégâts négatifs
    physical = CharacterStats(dommages_physiques=20)  # volontairement ignoré pour les sorts (décision du 04/10)
    assert physical.boost(10, "terre") == 10 and "dommages physiques +20, pas comptés" in physical.describe()


def test_stats_line_for_the_log():
    stats = CharacterStats.from_reading(perception.StatsReading(WALITCOIN, "avancées"))
    assert stats.describe() == "Force 80, Intelligence 50, Chance 0, Agilité 7, dommages +4, +8 % ; PA 6, PM 3, portée 0"
    assert stats.explain(["feu"]) == "Intelligence 50, +8 %, +4"
    simple = CharacterStats(force=80, source="simple")
    assert "bonus aux dommages non lus" in simple.describe()
    assert CharacterStats.from_record({**stats.record(), "lues_le": "2026-10-04T15:01:00", "inconnu": 1}) == stats


def cra_book(**options) -> SpellBook:
    window = screen(spells=True)
    book = SpellBook(FakeGame(window), **options)
    book.learn(read_spell_window(window))
    return book


def test_spells_for_the_tactics_count_the_stats():
    book = cra_book()
    book.locate(load_frame("combat_mon_tour"))  # barre : Recul, Empoisonnée, Magique, Glacée
    base = {s.spell_id: s.damage for s in book.combat_spells()}
    book.stats = CharacterStats(intelligence=100, dommages=2)
    boosted = {s.spell_id: s.damage for s in book.combat_spells()}
    assert base["fleche_magique"] == 13 and boosted["fleche_magique"] == pytest.approx(28)  # 13 x 2 + 2
    assert boosted["fleche_de_recul"] == pytest.approx(base["fleche_de_recul"] + 2)  # air : seul le fixe


def test_pa_pm_and_gear_range_used_in_combat():
    book = cra_book()
    ai = CombatAI(FakeGame(load_frame("combat_mon_tour")), CombatSettings(), book)
    assert ai._max_pa == 6 and ai._max_pm == 3  # combat.max_pa / max_pm tant qu'on ne sait pas
    book.stats = CharacterStats(pa=7, pm=4, portee=2)
    assert ai._max_pa == 7 and ai._max_pm == 4
    magique = CombatSpell(3, "fleche_magique", "Flèche Magique", 4, (1, 10), range_modifiable=True)
    recul = CombatSpell(1, "fleche_de_recul", "Flèche de Recul", 4, (1, 3), range_modifiable=False)
    use = SpellUse(gear_range=2)
    assert spell_range(magique, use) == (1, 12) and spell_range(recul, use) == (1, 3)
    use.new_turn(6)
    assert use.range_bonus == 2  # gardée d'un tour à l'autre


# Déroulé, avec un faux jeu


class FakeRecorder:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def keeps(self, lossless=False):
        return True

    def save(self, image, label, lossless=False, quality=80):
        self.saved.append(label)
        return f"<{label}>"


class Window:
    def __init__(self, name: str) -> None:
        self.title = f"{name} - Dofus Retro v1.49.5"


class FakeGame:
    """Rejoue les captures dans l'ordre (la dernière se répète) ; `after_press` : captures après chaque appui sur une
    touche."""

    def __init__(self, *frames: Frame, name: str = "") -> None:
        self.frames = list(frames)
        self.actions: list[tuple] = []
        self.recorder = FakeRecorder()
        self.after_press: dict[str, list[Frame]] = {}
        if name:
            self.window = Window(name)

    def look(self, park=None):
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]

    def sleep(self, seconds):
        pass

    def click(self, point=None, spread=(0, 0), button="left"):
        self.actions.append(("click", (round(point[0]), round(point[1]))))
        return True

    def press(self, key):
        self.actions.append(("press", key))
        if key in self.after_press:
            self.frames = list(self.after_press[key])

    def move(self, point, spread=(0, 0)):
        return point

    def wheel(self, point, notches, spread=(0, 0)):
        self.actions.append(("wheel", notches))
        return True


def clicks(game: FakeGame) -> list[tuple[int, int]]:
    return [a[1] for a in game.actions if a[0] == "click"]


def presses(game: FakeGame) -> list[str]:
    return [a[1] for a in game.actions if a[0] == "press"]


def test_button_then_sword_then_escape():
    closed, simple, both = screen(), screen(simple=True), screen(simple=True, advanced=True)
    game = FakeGame(closed, simple, both)
    game.after_press["escape"] = [closed]  # Échap ferme les deux fenêtres d'un coup
    stats = StatsReader(game).read()
    button, sword = clicks(game)
    assert abs(button[0] - 1457) <= 8 and abs(button[1] - 820) <= 8
    assert abs(sword[0] - (SIMPLE_AT[0] + 406)) <= 3 and abs(sword[1] - (SIMPLE_AT[1] + 485)) <= 3
    assert presses(game) == ["escape"]
    assert stats == CharacterStats(force=80, intelligence=50, chance=0, agilite=7, dommages=4, dommages_pourcent=8,
                                   pa=6, pm=3, portee=0, vitalite=52, sagesse=0, invocations=2)
    assert game.recorder.saved == ["caracteristiques"]


def nothing() -> Frame:
    """Écran noir : ni bouton, ni fenêtre."""
    return Frame.of(np.zeros_like(load_frame("hors_combat").image))


def test_no_button_no_stats_and_no_escape(caplog):
    game = FakeGame(nothing())
    with caplog.at_level(logging.WARNING):
        assert StatsReader(game).read() is None
    assert game.actions == []  # ni clic, ni Échap sans fenêtre ouverte
    assert game.recorder.saved == ["bouton_caracteristiques_introuvable"] * 2  # 2 essais
    assert "je continue sans elles" in caplog.text


def test_unreadable_advanced_window_falls_back_on_the_simple_one(caplog):
    window = crop("caracteristiques_avancees")
    window[115:700, 618:681] = window[120, 650]  # colonne Total vide
    closed, simple = screen(), screen(simple=True)
    unreadable = screen(simple=True, advanced=True, advanced_window=window)
    game = FakeGame(closed, simple, unreadable)
    game.after_press["escape"] = [closed, closed, simple, unreadable]  # 2e essai : pareil
    with caplog.at_level(logging.INFO):
        stats = StatsReader(game).read()
    assert stats.source == "simple" and (stats.force, stats.intelligence, stats.agilite) == (80, 50, 7)
    assert stats.dommages == 0 and stats.pa == 6
    assert presses(game) == ["escape", "escape"] and len(clicks(game)) == 4  # bouton, épée, deux fois
    assert "nouvel essai" in caplog.text and "sans les bonus aux dommages" in caplog.text
    assert game.recorder.saved.count("caracteristiques_illisibles") == 2


def session(tmp_path, mode="controle", stats=True) -> SessionSettings:
    return SessionSettings(mode=mode, stats=stats, memory=tmp_path / "personnages")


def full_read_game(name: str = "Jesuisleking") -> FakeGame:
    """Fenêtre des sorts (bouton baguette, croix), puis caractéristiques (bouton, épée, Échap)."""
    closed, spells = screen(), screen(spells=True)
    game = FakeGame(closed, spells, spells, closed, closed, screen(simple=True), screen(simple=True, advanced=True),
                    name=name)
    game.after_press["escape"] = [closed]
    return game


def test_session_start_reads_spells_and_stats_then_remembers(tmp_path, caplog):
    game = full_read_game()
    book = SpellBook(game, session=session(tmp_path))
    with caplog.at_level(logging.INFO):
        assert book.read()
    assert book.read_done and book.class_key == "cra" and book.stats.intelligence == 50
    assert len(clicks(game)) == 4 and presses(game) == ["escape"]  # baguette, croix, bouton, épée
    assert "Caractéristiques : Force 80, Intelligence 50, Chance 0, Agilité 7, dommages +4, +8 % ; PA 6, PM 3, " \
           "portée 0" in caplog.text
    assert "Flèche Magique : 13 -> 25 dégâts moyens (Intelligence 50, +8 %, +4)" in caplog.text
    (path,) = (tmp_path / "personnages").glob("*.json")
    assert path.name == "Jesuisleking.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["personnage"] == "Jesuisleking" and record["sorts"]["classe"] == "cra"
    assert len(record["sorts"]["lignes"]) == 10 and record["caracteristiques"]["force"] == 80
    magique = next(line for line in record["sorts"]["lignes"] if line.get("cle") == "fleche_magique")
    assert magique["niveau"] == 4 and magique["degats_base"] == 13 and magique["degats"] == pytest.approx(24.54)
    book.locate(load_frame("combat_mon_tour"))  # premier tour : cases de la barre retenues aussi
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["sorts"]["cases"] == {"1": "fleche_de_recul", "2": "fleche_empoisonnee", "3": "fleche_magique",
                                        "4": "fleche_glacee"}


def test_memory_mode_resumes_without_opening_any_window(tmp_path, caplog):
    first = SpellBook(full_read_game(), session=session(tmp_path))
    first.read()
    first.locate(load_frame("combat_mon_tour"))
    game = FakeGame(screen(), name="Jesuisleking")
    resumed = SpellBook(game, session=session(tmp_path, mode="memoire"))
    with caplog.at_level(logging.INFO):
        assert resumed.read()
    assert game.actions == [] and resumed.read_done  # aucun clic, aucune touche
    assert "Jesuisleking : sorts et caractéristiques repris de la dernière session (lus le " in caplog.text
    assert resumed.class_key == "cra" and resumed.stats == first.stats
    assert [(e.row, e.pa, e.spell_range, e.level, e.learned) for e in resumed.entries] == \
           [(e.row, e.pa, e.spell_range, e.level, e.learned) for e in first.entries]
    assert all(np.array_equal(a.icon, b.icon) for a, b in zip(resumed.entries, first.entries))  # PNG : sans perte
    resumed.locate(load_frame("combat_mon_tour"))  # icônes retenues : même case pour chaque sort
    assert {slot: e.row for slot, e in resumed.slots.items()} == {slot: e.row for slot, e in first.slots.items()}
    assert [s.damage for s in resumed.combat_spells()] == [s.damage for s in first.combat_spells()]


def test_memory_mode_reads_a_character_never_seen(tmp_path, caplog):
    game = full_read_game("Nouveau")
    book = SpellBook(game, session=session(tmp_path, mode="memoire"))
    with caplog.at_level(logging.INFO):
        assert book.read()
    assert "Nouveau : rien de retenu de la dernière session : sorts et caractéristiques lus à l'écran" in caplog.text
    assert len(clicks(game)) == 4 and (tmp_path / "personnages" / "Nouveau.json").is_file()


def test_memory_mode_reads_only_what_is_missing(tmp_path, caplog):
    SpellBook(full_read_game(), session=session(tmp_path, stats=False)).read()  # sorts seuls retenus
    closed = screen()
    game = FakeGame(closed, screen(simple=True), screen(simple=True, advanced=True), name="Jesuisleking")
    game.after_press["escape"] = [closed]
    book = SpellBook(game, session=session(tmp_path, mode="memoire"))
    with caplog.at_level(logging.INFO):
        book.read()
    assert "sorts repris de la dernière session (lus le" in caplog.text and "caractéristiques pas retenues" in caplog.text
    button, _ = clicks(game)  # bouton des caractéristiques et épée : pas la baguette des sorts
    assert abs(button[0] - 1457) <= 8
    record = json.loads((tmp_path / "personnages" / "Jesuisleking.json").read_text(encoding="utf-8"))
    assert set(record) == {"personnage", "sorts", "caracteristiques"}


def test_one_entry_per_character_replaced_by_the_last_read(tmp_path):
    memory = CharacterMemory(tmp_path)
    memory.save("Walitcoin", {"caracteristiques": {"force": 80}})
    memory.save("Walitcoin", {"caracteristiques": {"force": 95}})
    memory.save("Imthebest", {"caracteristiques": {"force": 1}})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Imthebest.json", "Walitcoin.json"]
    assert memory.load("Walitcoin") == {"personnage": "Walitcoin", "caracteristiques": {"force": 95}}
    assert memory.load("Absent") is None
    (tmp_path / "Abime.json").write_text("{abîmé", encoding="utf-8")
    assert memory.load("Abime") is None
    assert memory.path("a/b:c").name == "a_b_c.json"


def test_failed_stats_read_keeps_what_was_remembered(tmp_path):
    SpellBook(full_read_game(), session=session(tmp_path)).read()
    path = tmp_path / "personnages" / "Jesuisleking.json"
    before = json.loads(path.read_text(encoding="utf-8"))["caracteristiques"]
    closed, spells = screen(), screen(spells=True)
    game = FakeGame(closed, spells, spells, closed, nothing(), name="Jesuisleking")
    book = SpellBook(game, session=session(tmp_path))  # contrôle : sorts relus, caractéristiques ratées
    book.read()
    assert book.stats is None and book.read_done
    assert json.loads(path.read_text(encoding="utf-8"))["caracteristiques"] == before


def test_spell_line_round_trip():
    entry = read_spell_window(screen(spells=True))[2]
    back = entry_of(json.loads(json.dumps(entry_record(entry))))
    assert (back.row, back.pa, back.spell_range, back.learned, back.level, back.low_written) == \
           (entry.row, entry.pa, entry.spell_range, entry.learned, entry.level, entry.low_written)
    assert np.array_equal(back.icon, entry.icon)
    assert entry_of({"ligne": 1, "icone": "pas du png"}) is None


# Réglages


def test_session_settings():
    settings = SessionSettings.from_settings(load_settings())
    assert settings.mode == "controle" and settings.stats and settings.memory.name == "personnages"
    assert SessionSettings.from_settings({"session": {"mode": "Mémoire", "memory": ""}}) == \
           SessionSettings(mode="memoire", stats=True, memory=None)
    assert reading_mode("MEMOIRE") == "memoire" and reading_mode(" contrôle ") == "controle"
    with pytest.raises(ValueError):
        reading_mode("souvenir")
    assert when("2026-10-04T15:01:12") == "le 04/10 à 15:01" and when(None) == "à une date inconnue"


def test_without_session_settings_only_the_spell_window_is_read():
    book = SpellBook(FakeGame(screen(), name="Jesuisleking"))  # comme avant le 04/10 (tests, anciens appels)
    assert book.memory is None and book._stats_done


def test_advanced_window_of_fourbocinq_read_entirely():
    # 04/10 : le « 3 » de « 36 » (Intelligence, colonne Total) à 0,742 contre 0,75 : illisible, repli sur la fenêtre
    # simple, bonus aux dommages perdus. Modèle de 3 ajouté (chiffre_3_a2.png, découpé dans cette capture).
    reading = perception.read_advanced_window(load_frame("caracteristiques_avancees_fourbocinq"))
    assert reading.values["intelligence"] == 36 and reading.values["force"] == 81
    assert reading.values["dommages"] == 11 and reading.values["dommages_pourcent"] == 6
