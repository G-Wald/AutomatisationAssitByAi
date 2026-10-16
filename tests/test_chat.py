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
