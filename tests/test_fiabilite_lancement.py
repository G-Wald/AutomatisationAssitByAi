"""Lancement des combats et entrée des suiveurs : corrections du 25/10, sur les captures des journaux.

Journaux 20261025_155334, 171736 et 181212 (Taïnéla, Le berceau) : 72 clics de sonde, 40 combats, 31 menus de Flash ;
sur les 34 combats de 155334 et 181212, 9 avec un suiveur à Brâkmar.
"""

from dataclasses import replace

import pytest

from bot.brain.engage import BELOW_OFFSETS, PROBE_SPREAD, Engager, EngageSettings, hover_points, window_over_map
from bot.perception.groups import GroupTooltip, find_group_tooltips, under_its_group
from bot.perception.join import find_fight_swords
from bot.team.join import Joiner, _place, other_map
from bot.team.member import to_reference
from conftest import load_frame
from test_engage import FlashMenuGame, clicks, inside
from test_team import Screen, events, member, with_join_menu, with_sword


def tooltip_from_box(frame, box, monsters) -> GroupTooltip:
    """Infobulle du cadre cyan d'une capture « menu_attaquer_absent » (sa place à la capture Z, cadre de 2 px)."""
    left, top, right, bottom = box
    return GroupTooltip((left + right) / 2, top + frame.layout.length(23), top, bottom, left, right, monsters, False)


def tooltip_at(frame, x, y) -> GroupTooltip:
    return next(t for t in find_group_tooltips(frame) if abs(t.x - x) <= 3 and abs(t.title_y - y) <= 3)


def click_zone_inside(point, spread, body) -> bool:
    """Tout le clic (point ± étendue) tombe dans le corps du monstre."""
    (x, y), (sx, sy) = point, spread
    return inside((x - sx, y - sy), body) and inside((x + sx, y + sy), body)


# --- Où viser : petits monstres -------------------------------------------------------------------------------------

# (capture après le clic raté, cadre de l'infobulle à la capture Z, monstres, corps du monstre mesuré sur la capture)
SMALL_MONSTERS = [
    # 155334, 16:14:13 : Arakne sous une infobulle de 6 monstres en haut de carte ; clic 8 px au-dessus de son corps.
    ("sonde_arakne_sous_infobulle_haut", (858, 95, 1063, 319), 6, (952, 372, 965, 385)),
    # 181212, 18:29 : Arakne sous une infobulle de 7 monstres ; clics sur son bord haut puis son bord bas.
    ("sonde_arakne_sous_infobulle_bas", (788, 458, 1032, 705), 7, (903, 756, 918, 770)),
]


@pytest.mark.parametrize("name, box, monsters, body", SMALL_MONSTERS)
def test_small_monster_clicked_on_its_body(name, box, monsters, body):
    frame = load_frame(name)
    tooltip = tooltip_from_box(frame, box, monsters)
    first = hover_points(tooltip, frame)[0]
    assert click_zone_inside(first, frame.layout.spread(*PROBE_SPREAD), body)


def test_old_aim_was_above_the_small_spider():
    # Avant : 50 px sous l'infobulle (corps des Bouftous, 03/10), au-dessus du corps de l'Arakne.
    frame = load_frame("sonde_arakne_sous_infobulle_haut")
    _, top, _, _ = SMALL_MONSTERS[0][3]
    assert SMALL_MONSTERS[0][1][3] + frame.layout.length(50) < top
    assert BELOW_OFFSETS[0] > 50


def test_medium_monster_below_its_tooltip_still_hit():
    # 181212, 18:54 : Boufton Noir sous son infobulle (3 monstres) ; 2e clic, 62 px sous elle, sur son bord bas.
    frame = load_frame("sonde_boufton_noir_sous_infobulle")
    tooltip = tooltip_from_box(frame, (1357, 241, 1673, 396), 3)
    below = next(p for p in hover_points(tooltip, frame) if p[1] > tooltip.bottom)
    assert click_zone_inside(below, frame.layout.spread(*PROBE_SPREAD), (1503, 440, 1535, 462))


def test_tournesol_and_white_boufton_still_hit_on_real_z_captures():
    # Non-régression, captures Z réelles : Tournesol sous son infobulle (181212, combats à 1414,757 réussis) ; Boufton
    # Blanc au-dessus d'une infobulle de 6 monstres (155334, 16:01), au 2e point (6 monstres : côté indécidable).
    z = load_frame("groupes_z_infobulle_coupee_tournesols")
    first = hover_points(tooltip_at(z, 1414, 550), z)[0]
    assert click_zone_inside(first, z.layout.spread(*PROBE_SPREAD), (1398, 740, 1430, 770))
    z = load_frame("groupes_z_boufton_blanc_dessus_6_monstres")
    tooltip = tooltip_at(z, 1716, 250)
    assert tooltip.monsters == 6 and under_its_group(tooltip, z) is None
    second = hover_points(tooltip, z)[1]
    assert second[1] < tooltip.top and click_zone_inside(second, z.layout.spread(*PROBE_SPREAD), (1706, 184, 1738, 206))


# --- De quel côté : haut de la carte --------------------------------------------------------------------------------

def test_tooltip_too_close_to_the_top_has_its_group_above():
    # 171736, 17:17 : infobulle de 7 monstres à 44 px du haut ; la case ne départage pas (6 à 8 monstres), le 1er clic
    # partait dessous (menu de Flash), le combat s'est lancé au clic au-dessus. Le jeu ne la met au-dessus d'un groupe
    # que si son haut reste à 52,5 px au moins du haut de la carte.
    z = load_frame("groupes_z_haut_7_monstres_groupe_dessus")
    tooltip = tooltip_at(z, 1666, 70)
    assert tooltip.monsters == 7
    assert under_its_group(tooltip, z) is True
    assert hover_points(tooltip, z)[0][1] < tooltip.top


def test_tooltip_seen_above_its_group_near_the_top_unchanged():
    # 55,4 px du haut : le jeu l'a mise au-dessus de son groupe (groupe dessous), comme avant.
    z = load_frame("groupes_z_haut_mixte")
    assert under_its_group(tooltip_at(z, 1629, 78), z) is False


# --- Infobulle coupée par une autre : en dernier --------------------------------------------------------------------

def test_tooltip_cut_by_the_one_below_is_attacked_last():
    # 181212 : infobulle « Niveau 18 » coupée par « Niveau 20 » (8 monstres) ; 0 ligne lue, ses points 140 px trop haut.
    # Elle passait en premier (moins de monstres comptés). Les infobulles entières d'abord.
    z = load_frame("groupes_z_infobulle_coupee_tournesols")
    tooltips = find_group_tooltips(z)
    cut = next(t for t in tooltips if t.truncated)
    assert cut.monsters == 0
    order = Engager(None, EngageSettings()).attack_order(tooltips, tooltips, z)
    assert order[-1] is cut


# --- Pas de clic derrière une fenêtre -------------------------------------------------------------------------------

def test_window_over_the_map_seen():
    assert window_over_map(load_frame("combats_en_cours")) == "combats en cours"
    for name in ("hors_combat", "groupes_z_sable_calanques", "groupes_z_infobulle_coupee_tournesols"):
        assert window_over_map(load_frame(name)) is None


class WindowOpensGame(FlashMenuGame):
    """155334, 16:01 : le clic qui ferme le menu de Flash ouvre « Combats en cours » (l'œil juste au-dessus)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.window = load_frame("combats_en_cours")

    def click(self, point=None, spread=(0, 0), button="left"):
        closing = self.flash is not None
        super().click(point, spread, button)
        if closing:
            self.z = self.empty = self.window
        return True


def test_no_probe_click_behind_a_window_and_the_group_not_counted_as_missed(caplog):
    # Avant : 2e clic de sonde derrière la fenêtre (menu de Flash), ou « groupe parti » (Z ne montrait plus son
    # infobulle). Maintenant : pas de clic tant qu'elle est là ; nouvelle recherche bientôt (la boucle la ferme), le
    # groupe ni sauté ni compté comme raté.
    caplog.set_level("INFO")
    game = WindowOpensGame(load_frame("hors_combat_detection_enemi"), load_frame("hors_combat"),
                           [(1186, 441, 1206, 452)], opens=(), direct=("right",))
    engager = Engager(game, EngageSettings())
    assert not engager.engage()
    assert game.buttons == ["right", "left"]  # le clic à côté, la fermeture du menu de Flash ; rien derrière la fenêtre
    assert engager.missed and not engager.none_found
    assert engager._no_menu == [] and engager._skipped == []
    assert "Fenêtre « combats en cours » ouverte sur la carte" in caplog.text
    assert "le groupe est parti" not in caplog.text


# --- Suiveurs : jamais de clic à l'aveugle, ni sur une autre carte ---------------------------------------------------

def joined_once(screen):
    """Un premier suiveur entre par l'épée (collée en 900,300 sur hors_combat) : le Joiner connaît son point."""
    sword = with_sword("hors_combat", at=(900, 300))
    first = member("Premier", screen, sword, with_join_menu(sword, at=(950, 330)), load_frame("preparation"))
    joiner = Joiner(timeout=5)
    assert joiner.join(first, hint=None) and joiner.known is not None and joiner.known_center is not None
    screen.events.clear()
    return joiner


def test_follower_on_another_map_is_not_clicked_nor_waited_for(caplog):
    # 155334 et 181212 : suiveur à Brâkmar (potion de rappel), 9 combats sur 34 ; le point de l'épée cliqué dans sa
    # fenêtre « comme le précédent » (menus de Flash, bulle d'un PNJ), 20 s de recherche, l'équipe au placement
    # attendait 26 à 28 s. Maintenant : pas d'épée à sa place, carte lue -23,38 au lieu de celle du combat : aucun clic.
    caplog.set_level("INFO")
    screen = Screen()
    joiner = joined_once(screen)
    joiner.place = None
    joiner.fight_coords = lambda: (1, -33)  # nom de 1,-33 illisible sur le décor : position du trajet
    away = member("Elseiso", screen, load_frame("suiveur_a_brakmar"))
    assert not joiner.ask(away, hint=None)
    assert not joiner.join(away, hint=None)
    assert events(screen, "click_right") == [] and events(screen, "click") == []
    assert "Elseiso" in joiner.elsewhere and away.game.recorder.saved == ["suiveur_ailleurs"]
    assert "n'est pas sur la carte du combat" in caplog.text and not joiner.sword_missing
    joiner.new_fight()
    assert joiner.elsewhere == set() and joiner.place is None and joiner.known_center is None


def test_follower_on_the_same_map_without_the_sword_is_not_clicked_blind(monkeypatch):
    # Même carte, mais l'épée ne se voit pas à sa place chez lui (fenêtre dessus, souris) : pas de clic au point connu ;
    # recherche comme pour le premier (ici rien : pas de clic du tout), et il n'est pas cru ailleurs.
    monkeypatch.setattr("bot.team.join.SWORD_WAIT", 0)
    screen = Screen()
    joiner = joined_once(screen)
    blind = member("Second", screen, load_frame("hors_combat"))
    assert not joiner.ask(blind, hint=None)
    assert events(screen, "click_right") == [] and joiner.elsewhere == set()


def test_cut_sword_at_the_top_seen_again_at_its_place():
    # 171736 : épée coupée par le haut de l'écran (centre au-dessus du bord) : revue à sa place chez les suivants.
    frame = load_frame("epee_coupee_lame_et_icone_seules")
    (sword,) = find_fight_swords(frame)
    joiner = Joiner()
    joiner.known, joiner.known_center = sword.points[0][0], to_reference(frame.layout, sword.center)
    assert joiner._known_seen(frame)
    assert not joiner._known_seen(load_frame("hors_combat"))


def test_other_map_only_when_sure():
    brakmar = _place(load_frame("suiveur_a_brakmar"))
    assert brakmar is not None and brakmar.coords == (-23, 38)
    assert other_map(brakmar, (1, -33))
    assert not other_map(brakmar, (-23, 38))
    # 25/10 (journal 201109) : « 0,-2 » lu pour 0,-29 (dernier chiffre perdu sur le décor) : pas une autre carte.
    assert not other_map(replace(brakmar, coords=(0, -2)), (0, -29))
    assert other_map(replace(brakmar, coords=(0, -33)), (1, -33))  # carte voisine : il n'y est pas


def test_group_at_the_very_top_of_the_map_is_not_attacked():
    # 25/10 (journal 171736) et 26/10 (journal 175632) : épée d'un groupe du haut coupée par le bord ou cachée sous le nom
    # de la carte ; aucun suiveur entré, le principal seul. Ces groupes ne sont pas attaqués.
    from bot.brain.engage import Engager, EngageSettings
    from bot.perception.groups import find_group_tooltips
    from test_brain import FakeGame

    frame = load_frame("groupes_z_chevauchement")
    tooltips = find_group_tooltips(frame)
    kept = Engager(FakeGame(frame), EngageSettings())._not_at_the_top(frame, tooltips)
    assert len(kept) == len(tooltips) - 1 and all(round(t.x) != 1292 for t in kept)  # « Niveau 9 » tout en haut
