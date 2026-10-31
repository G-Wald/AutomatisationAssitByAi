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


def test_an_old_answer_left_in_the_chat_is_not_taken():
    # 31/10 (test rapide) : réponse de 19:16 pas encore affichée, Pesopepe pris en 0,-32 d'après sa ligne de 18:42.
    lines = ["[18:42] (Groupe) Pesopepe : [0,-32]", "[19:15] (Groupe) Fourboun : [2,-34]"]
    assert parse_position(lines, "Pesopepe") == (0, -32)  # sans heure de demande : comme avant
    assert parse_position(lines, "Pesopepe", since=(19, 16)) is None
    assert parse_position(lines + ["[19:16] (Groupe) Pesopepe : [1,-32]"], "Pesopepe", since=(19, 16)) == (1, -32)
    assert parse_position(["[00:00] (Groupe) Pesopepe : [1,-32]"], "Pesopepe", since=(23, 59)) == (1, -32)  # minuit
    assert parse_position(["(Groupe) Pesopepe : [1,-32]"], "Pesopepe", since=(19, 16)) is None  # heure illisible


def test_chat_input_is_under_the_chat_not_on_the_map():
    frame = load_frame("chat_position_6_2")
    x, y = chat_input_point(frame)
    assert not frame.layout.on_map((x, y)) and x < frame.layout.map_box[0] and y > frame.layout.height - 40


@pytest.mark.skipif(not ocr_available(), reason="OCR de Windows absent (requirements-winrt.txt)")
def test_position_read_in_the_real_chat():
    # 16/10 : « /p %pos% » tapé en arrière-plan chez Koalaloupour, en Incarnam 6,2.
    assert read_position(load_frame("chat_position_6_2"), "Koalaloupour") == (6, 2)


@pytest.mark.skipif(not ocr_available(), reason="OCR de Windows absent (requirements-winrt.txt)")
def test_position_read_at_the_top_of_a_chat_still_almost_empty():
    # 21/10, juste après la connexion : la ligne « (Groupe) Koalaloupour : [11,-28] » sous le message de bienvenue, en
    # haut du chat du dessous ; les dernières lignes, seules lues avant, étaient vides. Trajet arrêté à tort.
    assert read_position(load_frame("chat_position_apres_connexion"), "Koalaloupour") == (11, -28)


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


def test_closing_bracket_read_as_a_1_on_an_impossible_coordinate():
    # 23/10 : « (Groupe) Pombro : [11,-27] » lu « [11,-271 » ; « [6,21 » reste refusé (6,21 existe).
    assert parse_position(["[18:45] (Groupe) Pombro : [11,-271"], "Pombro") == (11, -27)
    assert parse_position(["(Groupe) Pombro : [6,21"], "Pombro") is None
    assert parse_position(["(Groupe) Pombro : [6,2]"], "Pombro") == (6, 2)


@pytest.mark.skipif(not ocr_available(), reason="OCR de Windows absent (requirements-winrt.txt)")
def test_position_of_a_follower_read_despite_its_closing_bracket():
    assert read_position(load_frame("position_crochet_lu_comme_un_1"), "Pombro") == (11, -27)


def test_position_line_cut_after_the_name_by_the_ocr():
    # 23/10 : agrandie 3 fois, « [18:46] (Groupe) Koalaloupour : » puis « [10,-28] » sur la ligne suivante.
    lines = ["[18.", "•45] (Groupe) Pombro : [11,-271", "[18.", "•46] (Groupe) Koalaloupour :", "[10,-28]"]
    assert parse_position(lines, "Koalaloupour") == (10, -28)
    assert parse_position(lines, "Pombro") == (11, -27)


@pytest.mark.skipif(not ocr_available(), reason="OCR de Windows absent (requirements-winrt.txt)")
def test_leader_position_read_when_the_ocr_cuts_its_line():
    assert read_position(load_frame("position_coupee_apres_le_nom"), "Koalaloupour") == (10, -28)


@pytest.mark.skipif(not ocr_available(), reason="OCR de Windows absent (requirements-winrt.txt)")
def test_join_refused_because_the_follower_is_not_in_the_leaders_group():
    # 23/10 : Pesopepe resté dans l'ancien groupe, « l'équipe est fermée » à chaque combat, 25 s perdues à chaque fois.
    from bot.perception.chat import join_refused

    assert join_refused(load_frame("equipe_fermee_pas_dans_le_groupe"))
    assert not join_refused(load_frame("chat_position_apres_connexion"))
