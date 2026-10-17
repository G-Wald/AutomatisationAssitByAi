import pytest

from bot.perception.chat import chat_input_point, ocr_available, parse_position, read_position
from conftest import load_frame


def test_position_line_parsed_from_the_group_channel():
    lines = ["[19:48] (Commerce) Sholyx : [Dofus Emeraude] IM7", "[19:48] (Groupe) Koalaloupour : [6,2]"]
    assert parse_position(lines) == (6, 2)
    assert parse_position(lines, "Koalaloupour") == (6, 2)
    assert parse_position(lines, "Dinarpepe") is None
    assert parse_position(["[20:01] (Groupe) Gela-[A13] ; [ -1 , -19 ]"]) == (-1, -19)  # « ; » et espaces de l'OCR
    assert parse_position(["[19:48] (Commerce) Sholyx : [Dofus Emeraude]"]) is None


def test_chat_input_is_under_the_chat_not_on_the_map():
    frame = load_frame("chat_position_6_2")
    x, y = chat_input_point(frame)
    assert not frame.layout.on_map((x, y)) and x < frame.layout.map_box[0] and y > frame.layout.height - 40


@pytest.mark.skipif(not ocr_available(), reason="OCR de Windows absent (requirements-winrt.txt)")
def test_position_read_in_the_real_chat():
    # 16/10 : « /p %pos% » tapé en arrière-plan chez Koalaloupour, en Incarnam 6,2.
    assert read_position(load_frame("chat_position_6_2"), "Koalaloupour") == (6, 2)


def test_cast_announcements_counted_without_accents_or_case():
    from bot.perception.chat import cast_announced, cast_count

    before = ["Dinarpepe lance Lancer de Pièces.", "Rose Démoniaque Fragile perd PDV."]
    after = before + ["Pesopepe lance Lancer de Pieces.", "Rose Démoniaque Fragile perd PDV."]
    assert cast_count(after, "Pesopepe", "Lancer de Pièces") == 1
    assert cast_announced(before, after, "Pesopepe", "Lancer de Pièces")
    assert not cast_announced(before, after, "Dinarpepe", "Lancer de Pièces")  # rien de neuf pour lui
    assert not cast_announced(before, before, "Pesopepe", "Lancer de Pièces")  # refus : aucune ligne


@pytest.mark.skipif(not ocr_available(), reason="OCR de Windows absent (requirements-winrt.txt)")
def test_combat_chat_read_on_a_real_capture():
    from bot.perception.chat import cast_count, combat_lines

    lines = combat_lines(load_frame("chat_combat_annonces"))
    assert cast_count(lines, "Dinarpepe", "Lancer de Pièces") == 2 and cast_count(lines, "Pesopepe", "Lancer de Pièces") == 1


def test_death_seen_in_the_combat_chat():
    from bot.perception.chat import died

    assert died(["Koalaloupour est mort !", "Tu as perdu 50 points d'énergie.", "Combat terminé"])
    assert not died(["Petit Tofu est mort !", "Combat terminé (ouvrir l'écran de [fin de combat])."])


@pytest.mark.skipif(not ocr_available(), reason="OCR de Windows absent (requirements-winrt.txt)")
def test_death_read_on_the_real_capture():
    # 17/10 : Koalaloupour mort seul à Incarnam 6,2 (puis réapparu à Astrub, le bot ne l'avait pas vu).
    from bot.perception.chat import combat_lines, died

    assert died(combat_lines(load_frame("mort_perte_energie")))
