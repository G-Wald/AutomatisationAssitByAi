"""Mode banque « chacun » (--banque) : chaque personnage vide l'onglet ressources de son inventaire dans son propre coffre,
à la banque d'Astrub (plan : docs/rapports/plan_banque.md ; étapes validées une à une le 11/10 avec tools/entrer_porte,
tools/banquier et tools/coffre_transfert).

L'équipe est d'abord amenée sur la carte de la banque (--aller 4,-16, fait par la boucle de l'équipe). Puis, personnage
après personnage, en arrière-plan : soleil de la porte (case 23,9) -> intérieur (fond noir autour de la pièce) ; clic
GAUCHE sur le banquier (le clic droit ouvre le menu de Flash) -> « Parler » -> « Consulter son coffre personnel » ->
fenêtre « Coffre » ; onglet ressources -> bouton trier / transférer -> « Transférer tous les objets » ; Échap ; soleil de
la sortie (case 8,26) -> dehors. Chaque étape est vérifiée à l'écran avant la suivante ; au moindre doute, ce personnage
s'arrête là (capture gardée) et on passe au suivant.

Positions : repère de référence (fenêtre de 1918 x 1008, comme les captures du 11/10), mises à l'échelle de la fenêtre.
"""

import logging
import time

import cv2
import numpy as np

from bot.brain.travel import escape_menu
from bot.perception.templates import match_template
from bot.perception.text import text_score
from bot.perception.ui import pods_fill

log = logging.getLogger(__name__)

BANK_MAP = (4, -16)
DOOR, EXIT = (23, 9), (8, 26)  # cases des soleils de la porte (dehors) et de la sortie (dedans)
INDOOR_DARK, OUTDOOR_DARK = 0.25, 0.05  # part de noir dans la zone de jeu : dedans / dehors (mesuré le 11/10)
DOOR_WAIT = 12.0
COUNTER = (1150, 150, 1700, 450)  # derrière le comptoir, où se tient le banquier
RESOURCES_TAB, SORT_BUTTON = (1670, 220), (1875, 220)  # onglet ressources (bûche), bouton trier / transférer
MENU_ZONE = (1450, 200, 1919, 700)
TEXT_SIZES = range(14, 25)
FOUND = 0.75


def dark_share(frame) -> float:
    left, top, right, bottom = frame.layout.map_box
    return float((frame.image[top:bottom, left:right].max(axis=2) <= 12).mean())


def find_bankers(frame) -> list[tuple[int, int]]:
    """Banquiers (hiboux dorés) derrière le comptoir, du plus visible au moins visible (11/10 : 1 780 à 2 300 px de
    référence par banquier, les morceaux de coffre 320 au plus)."""
    hsv = cv2.cvtColor(frame.image, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = ((h >= 12) & (h <= 24) & (s >= 90) & (s <= 200) & (v >= 110) & (v <= 215)).astype(np.uint8)
    x0, y0, x1, y1 = frame.layout.box(*COUNTER)
    zone = cv2.morphologyEx(mask[y0:y1, x0:x1], cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, _, stats, centers = cv2.connectedComponentsWithStats(zone)
    found = [(int(stats[k, 4]), (int(centers[k, 0]) + x0, int(centers[k, 1]) + y0)) for k in range(1, count)
             if stats[k, 4] >= frame.layout.area(1000) and stats[k, 3] >= frame.layout.length(45)]
    return [center for _, center in sorted(found, reverse=True)]


def best_text(frame, text: str, dark: bool, bold: bool = False) -> tuple[float, tuple[int, int]]:
    box = frame.layout.map_box
    region = frame.crop(box)
    best = (0.0, (0, 0))
    for size in TEXT_SIZES:
        score, (x, y) = text_score(region, text, frame.layout.length(size), bold, dark)
        if score > best[0]:
            best = (score, (box[0] + x, box[1] + y))
    return best


def inside(member) -> bool:
    """Le personnage est dans un bâtiment (fond noir autour de la pièce)."""
    return dark_share(member.game.look(park=False)) >= INDOOR_DARK


class BankVisit:
    """Passage à la banque de chaque personnage de l'équipe (déjà sur la carte de la banque)."""

    def __init__(self, members) -> None:
        self.members = members

    def run(self) -> list[str]:
        """Noms de ceux qui ont vidé leur onglet ressources."""
        done = []
        for member in self.members:
            try:
                if self.visit(member):
                    done.append(member.name)
            except Exception:  # un personnage en échec ne bloque pas les autres
                log.exception("%s : erreur à la banque", member.name)
        log.info("Banque : %d sur %d ont vidé leurs ressources (%s)", len(done), len(self.members), ", ".join(done))
        return done

    def _fail(self, member, why: str) -> bool:
        path = member.game.recorder.save(member.game.look(park=False).image, "banque_echec", lossless=True)
        log.warning("%s : %s (capture : %s) ; je passe au suivant", member.name, why, path)
        return False

    def _door(self, member, cell: tuple[int, int], inside: bool) -> bool:
        game = member.game
        frame = game.look(park=False)
        game.click(frame.grid.center(cell), frame.grid.click_spread())
        deadline = time.monotonic() + DOOR_WAIT
        while time.monotonic() < deadline:
            game.sleep(0.5)
            dark = dark_share(game.look(park=False))
            if (dark >= INDOOR_DARK) if inside else (dark < OUTDOOR_DARK):
                return True
        return False

    def visit(self, member) -> bool:
        game = member.game
        frame = game.look(park=False)
        before = pods_fill(frame)
        if dark_share(frame) < INDOOR_DARK:
            if not self._door(member, DOOR, inside=True):
                return self._fail(member, "pas entré dans la banque (soleil de la porte, case 23,9)")
        game.sleep(1.0)
        frame = game.look(park=False)
        speak = None
        for bx, by in find_bankers(frame)[:2]:
            game.click((bx, by - frame.layout.length(8)), (3, 3))  # clic GAUCHE sur son corps
            game.sleep(0.8)
            menu = game.look(park=False)
            box = (max(0, round(bx - frame.layout.length(300))), max(0, round(by - frame.layout.length(200))),
                   min(frame.layout.width, round(bx + frame.layout.length(400))),
                   min(frame.layout.height, round(by + frame.layout.length(250))))
            speak = match_template(menu, "banque/menu_parler.png", box=box, threshold=FOUND)
            if speak is not None:
                break
        if speak is None:
            return self._fail(member, "« Parler » du banquier introuvable")
        game.click(speak[0], (6, 3))
        game.sleep(1.5)
        score, point = best_text(game.look(park=False), "Consulter son coffre personnel", dark=True)
        if score < FOUND:
            return self._fail(member, f"option « Consulter son coffre personnel » pas trouvée ({score:.2f})")
        game.click(point, (20, 3))
        game.sleep(2.0)
        frame = game.look(park=False)
        score, _ = best_text(frame, "Coffre", dark=False, bold=True)
        if score < FOUND:
            return self._fail(member, f"fenêtre « Coffre » pas vue ({score:.2f})")
        layout = frame.layout
        game.click(layout.point(*RESOURCES_TAB), (5, 5))
        game.sleep(0.8)
        game.click(layout.point(*SORT_BUTTON), (5, 5))
        game.sleep(0.8)
        frame = game.look(park=False)
        entry = match_template(frame, "banque/transferer_tous.png", box=layout.box(*MENU_ZONE), threshold=0.8)
        if entry is None:
            escape_menu(game)
            return self._fail(member, "« Transférer tous les objets » pas reconnu")
        game.click(entry[0], (20, 3))
        game.sleep(1.5)
        after = pods_fill(game.look(park=False))
        escape_menu(game)  # ferme le coffre (Échap ; Menu principal refermé s'il s'ouvre)
        game.sleep(0.8)
        log.info("%s : ressources au coffre (pods %.0f %% -> %.0f %%)", member.name, 100 * before, 100 * after)
        if not self._door(member, EXIT, inside=False):
            return self._fail(member, "pas ressorti de la banque (soleil de la sortie, case 8,26)")
        return True
