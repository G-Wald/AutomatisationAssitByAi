from pathlib import Path

import cv2
import numpy as np

from bot.perception.frame import Frame

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Captures prises en plein écran : on garde la zone client de la fenêtre du jeu (sans barre de titre ni barre des tâches).
CLIENT_ROWS = {"combat_infobulle": slice(23, 1031)}

SCREENS = ["hors_combat", "preparation", "combat_mon_tour", "combat_tour_adversaire", "combat_portee_sort", "combat_infobulle"]


def whole_list(window: np.ndarray) -> np.ndarray:
    """Fenêtre des sorts (découpée à 574 x 729) dont la liste tiendrait entière : poignée de la barre de défilement
    sur toute sa course, rien à faire défiler (tests qui ne portent pas sur le défilement)."""
    window = window.copy()
    window[193:692, 540:558] = (60, 74, 81)
    return window


def load_frame(name: str) -> Frame:
    path = FIXTURES / f"{name}.png"
    if not path.exists():
        path = FIXTURES / f"{name}.jpg"
    image = cv2.imread(str(path))
    assert image is not None, name
    return Frame.of(image[CLIENT_ROWS.get(name, slice(None))])


# Menu de Flash ouvert par un clic droit à côté d'un Piou (14/10, carte -1,-19, fenêtre de 1920 x 1057) : cadre blanc,
# coin haut gauche au point du clic.
FLASH_MENU_CLICK = (1466, 113)
FLASH_MENU_BOX = (1466, 113, 1764, 326)


def with_flash_menu(frame: Frame, at) -> Frame:
    """Même capture, le menu de Flash (découpé tel quel dans sa capture) ouvert au clic `at` (son coin haut gauche)."""
    x0, y0, x1, y1 = FLASH_MENU_BOX
    menu = load_frame("menu_flash_clic_droit").image[y0:y1, x0:x1]
    image = frame.image.copy()
    x, y = at
    image[y : y + menu.shape[0], x : x + menu.shape[1]] = menu
    return Frame.of(image)


def without_flash_menu() -> Frame:
    """La capture du menu de Flash, le menu effacé (herbe unie) : ce que montre le jeu une fois le menu fermé."""
    image = load_frame("menu_flash_clic_droit").image.copy()
    x0, y0, x1, y1 = FLASH_MENU_BOX
    image[y0:y1, x0:x1] = (13, 191, 142)
    return Frame.of(image)
