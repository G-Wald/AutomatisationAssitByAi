"""Liste de la fenêtre « Tes sorts » plus longue que ses 10 lignes visibles (04/10 : personnages plus haut niveau) :
barre de défilement, recalage des pages par les icônes, lecture de toute la liste à la molette.

Pas encore de capture d'une liste défilée : les pages sont reconstituées à partir des fenêtres du Crâ et de
l'Enutrof du 03/10 (une liste de 20 lignes : les 10 du Crâ puis les 10 de l'Enutrof), lignes recopiées à leur
nouvelle place et poignée de la barre de défilement redessinée, en supposant que la liste défile ligne par ligne."""

import logging

import cv2
import numpy as np

from bot.brain.spellbook import SpellBook
from bot.perception.frame import Frame
from bot.perception.spellbook import ROWS, page_shift, read_spell_window, row_icons, scroll_bar
from conftest import FIXTURES, load_frame
from test_brain import FakeGame, clicks

CRA, ENUTROF = "fenetre_sorts_cra_03_10", "fenetre_sorts_enutrof_03_10"
WINDOW_AT = (640, 40)  # coin de la fenêtre collée sur la capture hors combat
ROW_TOP, PITCH, BAND, TEXT_RIGHT = 172, 54.5, 54, 536  # bande d'une ligne de la liste (sans la barre de défilement)
TRACK, THUMB = (152, 185, 190), (60, 74, 81)  # rail et poignée de la barre de défilement (BGR)


def window(name: str) -> np.ndarray:
    return cv2.imread(str(FIXTURES / f"{name}.png"))


def list_page(offset: int, total: int = 20) -> Frame:
    """La fenêtre, liste de `total` lignes (Crâ puis Enutrof) défilée de `offset` lignes, sur une capture."""
    sources = [window(CRA), window(ENUTROF)]
    page = sources[0].copy()
    for visible in range(ROWS):
        line = offset + visible
        source, row = sources[line // ROWS], line % ROWS
        sy, dy = round(ROW_TOP + PITCH * row), round(ROW_TOP + PITCH * visible)
        page[dy : dy + BAND, :TEXT_RIGHT] = source[sy : sy + BAND, :TEXT_RIGHT]
    page[185:700, 539:559] = TRACK
    length = round(499 * ROWS / total)
    top = 193 + round((499 - length) * offset / (total - ROWS))
    page[top : top + length, 540:558] = THUMB
    image = load_frame("hors_combat").image.copy()
    x, y = WINDOW_AT
    image[y : y + page.shape[0], x : x + page.shape[1]] = page
    return Frame.of(image)


def on_screen(name: str) -> Frame:
    image = load_frame("hors_combat").image.copy()
    x, y = WINDOW_AT
    source = window(name)
    image[y : y + source.shape[0], x : x + source.shape[1]] = source
    return Frame.of(image)


# Perception


def test_scroll_bar_of_the_real_windows():
    for name, bottom in ((CRA, 432), (ENUTROF, 421)):  # 21 et 22 lignes : poignée de 239 et 228 px
        bar = scroll_bar(on_screen(name))
        assert bar.top == 193 and bar.bottom == bottom, name
        assert bar.scrolls and bar.at_top and not bar.at_end
    assert scroll_bar(load_frame("hors_combat")) is None  # fenêtre fermée


def test_scroll_bar_follows_the_list():
    assert scroll_bar(list_page(4)).top > 193 and not scroll_bar(list_page(4)).at_end
    assert scroll_bar(list_page(10)).at_end and not scroll_bar(list_page(10)).at_top


def test_pages_lined_up_by_their_icons():
    first = row_icons(list_page(0))
    assert all(icon is not None for icon in first)
    assert page_shift(first, row_icons(list_page(0))) == 0
    for shift in (1, 3, 6, 8):
        assert page_shift(first, row_icons(list_page(shift))) == shift, shift
    assert page_shift(first, row_icons(list_page(10))) is None  # plus aucune ligne en commun


def test_rows_of_a_scrolled_page_numbered_in_the_whole_list():
    entries = read_spell_window(list_page(6), first_row=6)
    enutrof = read_spell_window(on_screen(ENUTROF))
    assert [e.row for e in entries][-6:] == [10, 11, 12, 13, 14, 15]
    assert [(e.pa, e.spell_range, e.level) for e in entries if e.row >= 10] == \
        [(e.pa, e.spell_range, e.level) for e in enutrof[:6]]


# Lecture de toute la liste


def test_whole_list_read_with_the_wheel(caplog):
    closed = load_frame("hors_combat")
    game = FakeGame(closed, list_page(0), list_page(6), list_page(10), list_page(10), closed)
    book = SpellBook(game)
    with caplog.at_level(logging.INFO):
        assert book.read()
    rows = [e.row for e in book.entries]
    assert rows == sorted(set(rows)) and rows[-1] == 19  # aucune ligne en double, jusqu'à la dernière
    enutrof = read_spell_window(on_screen(ENUTROF))
    assert [(e.pa, e.spell_range) for e in book.entries if e.row >= 10] == [(e.pa, e.spell_range) for e in enutrof]
    # 2 crans : 6 lignes (3 par cran) ; puis 2 crans pour ~7 lignes : en bas, 4 lignes de plus.
    assert [a[1] for a in game.actions if a[0] == "wheel"] == [2, 2]
    button, close = clicks(game)
    assert abs(close[0] - 1191) <= 3 and abs(close[1] - 65) <= 3  # croix de la fenêtre, une fois en bas
    assert "Liste des sorts défilée de 6 ligne(s) (2 cran(s) de molette) : lignes 7 à 16" in caplog.text
    assert "Liste des sorts défilée de 4 ligne(s) (2 cran(s) de molette) : lignes 11 à 20" in caplog.text
    assert "Fenêtre des sorts : 20 lignes lues en 3 page(s)" in caplog.text


def test_one_row_per_notch_reads_with_more_notches():
    closed = load_frame("hors_combat")
    game = FakeGame(closed, list_page(0), list_page(2), list_page(9), list_page(10), list_page(10), closed)
    book = SpellBook(game)
    assert book.read()
    assert [a[1] for a in game.actions if a[0] == "wheel"] == [2, 7, 7]  # 1 ligne par cran mesurée : 7 crans
    assert [e.row for e in book.entries][-1] == 19


def test_scroll_bar_click_when_the_wheel_does_nothing(caplog):
    # 11/10, arrière-plan : la molette ne fait rien ; un clic sur le rail, sous la poignée, fait défiler.
    closed = load_frame("hors_combat")
    game = FakeGame(closed, list_page(0), list_page(0), list_page(5), list_page(10), list_page(10), closed)
    book = SpellBook(game)
    with caplog.at_level(logging.INFO):
        assert book.read()
    assert "La molette ne fait pas défiler la liste des sorts : clic sur la barre de défilement" in caplog.text
    rail = clicks(game)[1]  # après le bouton baguette : sous la poignée (en haut, 193 à 442), au-dessus de la flèche
    assert abs(rail[0] - (640 + 548)) <= 3 and 40 + 442 < rail[1] < 40 + 694
    assert [e.row for e in book.entries][-1] == 19


def test_next_character_goes_straight_to_the_scroll_bar(caplog):
    # 15/10, équipe de 8 en arrière-plan : la molette essayée puis abandonnée pour chacun (0,9 s x 8). Le 2e personnage
    # clique tout de suite sur la barre de défilement.
    closed = load_frame("hors_combat")
    first = FakeGame(closed, list_page(0), list_page(0), list_page(5), list_page(10), list_page(10), closed)
    assert SpellBook(first).read()
    second = FakeGame(closed, list_page(0), list_page(5), list_page(10), list_page(10), closed)
    book = SpellBook(second)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert book.read()
    assert "La molette ne fait pas défiler" not in caplog.text
    rail = clicks(second)[1]  # premier geste après le bouton baguette : le rail, pas un cran de molette
    assert abs(rail[0] - (640 + 548)) <= 3 and 40 + 442 < rail[1] < 40 + 694
    assert [e.row for e in book.entries][-1] == 19


def test_down_arrow_when_neither_wheel_nor_scroll_bar_work(caplog):
    closed = load_frame("hors_combat")
    game = FakeGame(closed, list_page(0), list_page(0), list_page(0), list_page(3), list_page(10), list_page(10),
                    closed)
    book = SpellBook(game)
    with caplog.at_level(logging.INFO):
        assert book.read()
    assert "flèche du bas" in caplog.text
    arrow = clicks(game)[2:5]  # après le bouton baguette et le rail : 3 clics sur la flèche du bas
    assert all(abs(x - (640 + 548)) <= 3 and abs(y - (40 + 704)) <= 3 for x, y in arrow)
    assert [e.row for e in book.entries][-1] == 19


def test_list_that_does_not_scroll_is_completed_from_memory(tmp_path, caplog):
    """11/10 : en arrière-plan, la liste n'a pas défilé (10 sorts sur 23) et cette lecture a remplacé la complète en
    mémoire. Désormais, les lignes suivantes sont reprises de la mémoire, et la lecture complète y reste."""
    from bot.brain.memory import SessionSettings
    session = SessionSettings(mode="controle", stats=False, memory=tmp_path)
    closed = load_frame("hors_combat")
    full = SpellBook(FakeGame(closed, list_page(0), list_page(6), list_page(10), list_page(10), closed),
                     name="Gela-[A13]", session=session)
    assert full.read() and len(full.entries) == 20
    stuck = FakeGame(closed, list_page(0), list_page(0), list_page(0), list_page(0), closed)  # rien ne fait défiler
    book = SpellBook(stuck, name="Gela-[A13]", session=session)
    with caplog.at_level(logging.INFO):
        assert book.read()
    assert [e.row for e in book.entries] == list(range(20))
    assert "lignes 11 à 20 reprises de la lecture" in caplog.text
    again = SpellBook(FakeGame(closed, list_page(0), list_page(0), list_page(0), list_page(0), closed), name="Gela-[A13]",
                      session=session)
    assert again.read() and len(again.entries) == 20  # la mémoire a gardé les 20 lignes


def test_list_left_scrolled_goes_back_to_the_top_first():
    closed = load_frame("hors_combat")
    game = FakeGame(closed, list_page(6), list_page(0), list_page(6), list_page(10), list_page(10), closed)
    book = SpellBook(game)
    assert book.read()
    assert [a[1] for a in game.actions if a[0] == "wheel"][0] < 0  # remontée d'abord
    assert [e.row for e in book.entries][0] == 0 and book.class_key is not None


def test_scroll_that_cannot_be_lined_up_keeps_the_first_page(caplog):
    closed = load_frame("hors_combat")
    game = FakeGame(closed, list_page(0), list_page(10), list_page(10), closed)  # 10 lignes d'un coup : rien en commun
    book = SpellBook(game)
    with caplog.at_level(logging.WARNING):
        assert book.read()
    assert "pas recalée sur la page d'avant" in caplog.text
    assert [e.row for e in book.entries][-1] <= 9


# Vraie liste défilée (04/10, Gela-[A13], Crâ plus haut niveau : 23 lignes, de Capture d'âmes à Invocation de Dopeul
# Crâ) : 2 crans de molette = 6 lignes, puis une dernière page d'une seule ligne (un nombre impair de lignes : chaque
# icône un demi-pixel plus loin, recalage manqué pendant le test, d'où le flou avant comparaison).
REAL = [f"fenetre_sorts_cra_niveau_page{n}" for n in (1, 2, 3, 4)]


def test_real_pages_lined_up_and_scroll_bar_at_the_end():
    icons = [row_icons(on_screen(name)) for name in REAL]
    assert [page_shift(a, b) for a, b in zip(icons, icons[1:])] == [6, 6, 1]
    bars = [scroll_bar(on_screen(name)) for name in REAL]
    assert bars[0].at_top and not any(bar.at_end for bar in bars[:3]) and bars[3].at_end


def test_real_list_read_to_the_last_row():
    closed = load_frame("hors_combat")
    pages = [on_screen(name) for name in REAL]
    game = FakeGame(closed, *pages, pages[-1], closed)
    book = SpellBook(game)
    assert book.read()
    assert [a[1] for a in game.actions if a[0] == "wheel"] == [2, 2, 2]
    by_row = {e.row: e for e in book.entries}
    assert sorted(by_row) == list(range(23)) and book.class_key == "cra"
    assert by_row[0].pa == 7 and not by_row[22].learned  # Capture d'âmes (7 PA), Dopeul pas appris
    names = {s.entry.row: (s.name, s.level) for s in book.spells.values()}
    assert names[4] == ("Flèche Magique", 6) and names[6] == ("Flèche Enflammée", 6)  # « Niv. 6 » lu
    assert names[21] == ("Maîtrise de l'Arc", 5) and names[13] == ("Tir Puissant", 4)
    assert by_row[9].reach == (1, 9) and by_row[12].reach == (6, 8)  # chiffres 9 et 6 lus


# Liste restée défilée en bas à l'ouverture, que la molette ne remonte pas (18/10, Lestunes, en arrière-plan : lue à
# partir de sa 11e ligne comme si c'était la 1re, l'Enutrof sans aucun sort de sa barre, case 3 jouée 41 fois).
LESTUNES = ("fenetre_sorts_enutrof_defilee_en_bas", "fenetre_sorts_enutrof_defilee_en_bas_page_2")


def test_scroll_bar_tells_the_list_length_and_the_first_row_shown():
    first, last = (scroll_bar(load_frame(name)) for name in LESTUNES)
    assert (first.rows, first.first_row) == (21, 10) and not first.at_end  # lignes 11 à 20 sur 21
    assert (last.rows, last.first_row) == (21, 11) and last.at_end
    assert scroll_bar(on_screen(CRA)).first_row == 0
    assert [scroll_bar(list_page(offset)).first_row for offset in (0, 4, 6, 10)] == [0, 4, 6, 10]


def test_list_left_scrolled_goes_back_up_with_the_scroll_bar_when_the_wheel_does_nothing(caplog):
    closed = load_frame("hors_combat")
    game = FakeGame(closed, list_page(10), list_page(10), list_page(5), list_page(0),  # molette sans effet, 2 clics
                    list_page(0), list_page(5), list_page(10), list_page(10), closed)
    book = SpellBook(game)
    with caplog.at_level(logging.INFO):
        assert book.read()
    assert "La molette ne remonte pas la liste des sorts" in caplog.text
    up = clicks(game)[1:3]  # après le bouton baguette : sur le rail, au-dessus de la poignée (en bas : 442 à 692)
    assert all(abs(x - (640 + 548)) <= 3 and 40 + 193 < y < 40 + 442 for x, y in up)
    assert [e.row for e in book.entries] == list(range(20)) and not book._incomplete


def test_list_stuck_at_the_bottom_is_numbered_from_the_scroll_bar_and_completed_from_memory(tmp_path, caplog):
    from bot.brain.memory import CharacterMemory, SessionSettings
    session = SessionSettings(mode="controle", stats=False, memory=tmp_path)
    closed = load_frame("hors_combat")
    full = SpellBook(FakeGame(closed, list_page(0), list_page(6), list_page(10), list_page(10), closed),
                     name="Lestunes", session=session)
    assert full.read() and len(full.entries) == 20
    stuck = FakeGame(closed, list_page(10), list_page(10), list_page(10), list_page(10), closed)  # rien ne la remonte
    book = SpellBook(stuck, name="Lestunes", session=session)
    with caplog.at_level(logging.INFO):
        assert book.read()
    assert "lue à partir de la ligne 11" in caplog.text and "lignes 1 à 10 reprises de la lecture" in caplog.text
    assert [e.row for e in book.entries] == list(range(20))
    enutrof = read_spell_window(on_screen(ENUTROF))  # lignes 11 à 20 : lues à l'instant, à leur place
    assert [(e.pa, e.spell_range) for e in book.entries[10:]] == [(e.pa, e.spell_range) for e in enutrof]
    assert [(e.pa, e.spell_range) for e in book.entries] == [(e.pa, e.spell_range) for e in full.entries]
    stored = CharacterMemory(tmp_path).load("Lestunes")["sorts"]
    assert len(stored["lignes"]) == 20 and stored["complete"]


def test_real_list_stuck_at_the_bottom_rows_numbered_from_the_scroll_bar(caplog):
    closed = load_frame("hors_combat")
    first, last = (load_frame(name) for name in LESTUNES)
    game = FakeGame(closed, first, first, first, last, last, closed)  # ni la molette ni le rail ne la remontent
    book = SpellBook(game)
    with caplog.at_level(logging.INFO):
        assert book.read()
    assert [e.row for e in book.entries] == list(range(10, 21)) and book.class_key == "enutrof"
    assert "11 lignes lues sur 21" in caplog.text and book._incomplete


def test_shorter_reading_never_replaces_a_longer_one_in_memory(tmp_path, caplog):
    """18/10 : la lecture de 11 lignes de Lestunes, pas signalée incomplète, a remplacé ses 21 lignes retenues."""
    from bot.brain.memory import CharacterMemory, SessionSettings
    session = SessionSettings(mode="controle", stats=False, memory=tmp_path)
    closed = load_frame("hors_combat")
    full = SpellBook(FakeGame(closed, list_page(0), list_page(6), list_page(10), list_page(10), closed),
                     name="Lestunes", session=session)
    assert full.read()
    short = SpellBook(FakeGame(closed, on_screen(ENUTROF), closed), name="Lestunes", session=session)
    short.learn(read_spell_window(on_screen(ENUTROF)))  # 10 lignes, lecture que rien ne dit incomplète
    with caplog.at_level(logging.INFO):
        short._remember()
    assert "la lecture retenue (20 lignes" in caplog.text
    assert len(CharacterMemory(tmp_path).load("Lestunes")["sorts"]["lignes"]) == 20


def test_incomplete_reading_in_memory_is_read_again_on_screen(tmp_path, caplog):
    from bot.brain.memory import CharacterMemory, SessionSettings
    closed = load_frame("hors_combat")
    stuck = FakeGame(closed, list_page(10), list_page(10), list_page(10), list_page(10), closed)
    control = SessionSettings(mode="controle", stats=False, memory=tmp_path)
    assert SpellBook(stuck, name="Lestunes", session=control).read()
    assert CharacterMemory(tmp_path).load("Lestunes")["sorts"]["complete"] is False  # rien de mieux en mémoire
    memory = SessionSettings(mode="memoire", stats=False, memory=tmp_path)
    game = FakeGame(closed, list_page(0), list_page(6), list_page(10), list_page(10), closed)
    book = SpellBook(game, name="Lestunes", session=memory)
    with caplog.at_level(logging.INFO):
        assert book.read()
    assert "sorts retenus d'une lecture incomplète" in caplog.text
    assert [e.row for e in book.entries] == list(range(20))
    stored = CharacterMemory(tmp_path).load("Lestunes")["sorts"]
    assert len(stored["lignes"]) == 20 and stored["complete"]  # la lecture complète remplace l'incomplète
