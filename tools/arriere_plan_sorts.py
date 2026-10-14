"""Faire défiler la liste « Tes sorts » en arrière-plan : quelle façon marche ? (test B1 du plan du 11/10)

Le 11/10, en arrière-plan, ni la molette (message envoyé à la fenêtre interne) ni trois clics sur la flèche du bas
n'ont fait défiler la liste : 10 sorts lus sur 23, Tempête de Puissance et Maîtrise de l'Arc inconnues. Ici, chaque
façon est essayée l'une après l'autre dans la fenêtre d'un personnage ; la poignée de la barre de défilement est
relevée avant et après : elle a bougé, la façon marche. Rapport et captures dans logs/arriere_plan_sorts_<date>/.

Façons essayées :
- molette envoyée à la fenêtre interne (celle du bot aujourd'hui) ;
- molette envoyée à la fenêtre principale du jeu ;
- « souris ici » puis molette ;
- clic lent sur la flèche du bas (souris ici, pause, bouton enfoncé, pause, relâché) ;
- clic sur le rail, sous la poignée ;
- poignée glissée vers le bas ;
- touches flèche du bas et page suivante.

Un personnage en jeu, hors combat, sa fenêtre non réduite (recouverte, peu importe) :
    .venv\\Scripts\\python -m tools.arriere_plan_sorts --personnage Brawli-[Gal]
"""

import argparse
import sys
import time
from datetime import datetime

import cv2
import win32api
import win32con
import win32gui

from bot.actions.background import BackgroundInput
from bot.actions.input import Human
from bot.actions.stop import StopControl
from bot.brain.game import Game
from bot.capture.window import enable_dpi_awareness, find_windows
from bot.config import ROOT
from bot.perception.spellbook import (ARROW_DOWN, LIST_POINT, THUMB_BOTTOM, THUMB_TOP, find_close_button,
                                      find_spell_window, find_spells_button, scroll_bar, window_point)
from tools.arriere_plan import confirm

WAIT = 0.5  # après un geste, avant de relever la poignée
ARROW_UP = (548, 179)  # flèche du haut de la barre de défilement (repère de la fenêtre des sorts)


def pack(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


class Lab:
    def __init__(self, game: Game) -> None:
        self.game = game
        self.window = game.window
        self.inner = BackgroundInput(game.window, game.control).target()

    def frame(self):
        return self.game.look(park=False)

    def point(self, at) -> tuple[int, int]:
        frame = self.frame()
        corner = find_spell_window(frame)
        x, y = window_point(frame, corner, at)
        return round(x), round(y)

    def client(self, point, hwnd) -> int:
        screen = win32gui.ClientToScreen(self.window.hwnd, point)
        return pack(*win32gui.ScreenToClient(hwnd, screen))

    def screen(self, point) -> int:
        return pack(*win32gui.ClientToScreen(self.window.hwnd, point))

    def post(self, hwnd, message, wparam, lparam) -> None:
        win32api.PostMessage(hwnd, message, wparam, lparam)

    # --- Façons ---

    def wheel(self, hwnd, notches: int, move_first: bool = False) -> None:
        at = self.point(LIST_POINT)
        if move_first:
            self.post(self.inner, win32con.WM_MOUSEMOVE, 0, self.client(at, self.inner))
            time.sleep(0.05)
        delta = -120 if notches > 0 else 120
        for _ in range(abs(notches)):
            self.post(hwnd, win32con.WM_MOUSEWHEEL, (delta & 0xFFFF) << 16, self.screen(at))
            time.sleep(0.06)

    def slow_click(self, at) -> None:
        where = self.client(self.point(at), self.inner)
        self.post(self.inner, win32con.WM_MOUSEMOVE, 0, where)
        time.sleep(0.15)
        self.post(self.inner, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, where)
        time.sleep(0.12)
        self.post(self.inner, win32con.WM_LBUTTONUP, 0, where)

    def block_click(self, point) -> None:
        where = self.client(point, self.inner)
        self.post(self.inner, win32con.WM_MOUSEMOVE, 0, where)
        self.post(self.inner, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, where)
        self.post(self.inner, win32con.WM_LBUTTONUP, 0, where)

    def track(self, down: bool) -> None:
        frame = self.frame()
        bar = scroll_bar(frame)
        corner = find_spell_window(frame)
        y = (bar.bottom + THUMB_BOTTOM) / 2 if down else (THUMB_TOP + bar.top) / 2
        x, y = window_point(frame, corner, (ARROW_DOWN[0], y))
        self.block_click((round(x), round(y)))

    def drag(self, down: bool) -> None:
        frame = self.frame()
        bar = scroll_bar(frame)
        corner = find_spell_window(frame)
        start = window_point(frame, corner, (ARROW_DOWN[0], (bar.top + bar.bottom) / 2))
        x, y = round(start[0]), round(start[1])
        step = frame.layout.length(20) * (1 if down else -1)
        self.post(self.inner, win32con.WM_MOUSEMOVE, 0, self.client((x, y), self.inner))
        self.post(self.inner, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, self.client((x, y), self.inner))
        for k in range(1, 9):
            time.sleep(0.04)
            self.post(self.inner, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON,
                      self.client((x, round(y + k * step)), self.inner))
        time.sleep(0.05)
        self.post(self.inner, win32con.WM_LBUTTONUP, 0, self.client((x, round(y + 8 * step)), self.inner))

    def key(self, vk: int, times: int) -> None:
        for _ in range(times):
            self.post(self.inner, win32con.WM_KEYDOWN, vk, 1)
            time.sleep(0.05)
            self.post(self.inner, win32con.WM_KEYUP, vk, 1 | (1 << 30) | (1 << 31))
            time.sleep(0.08)


WAYS = [
    ("molette, fenêtre interne", lambda lab: lab.wheel(lab.inner, 3), lambda lab: lab.wheel(lab.inner, -30)),
    ("molette, fenêtre principale", lambda lab: lab.wheel(lab.window.hwnd, 3),
     lambda lab: lab.wheel(lab.window.hwnd, -30)),
    ("souris ici puis molette", lambda lab: lab.wheel(lab.inner, 3, move_first=True),
     lambda lab: lab.wheel(lab.inner, -30, move_first=True)),
    ("clic lent sur la flèche du bas", lambda lab: [lab.slow_click(ARROW_DOWN) for _ in range(3)],
     lambda lab: [lab.slow_click(ARROW_UP) for _ in range(30)]),
    ("clic sur le rail sous la poignée", lambda lab: lab.track(True), lambda lab: [lab.track(False) for _ in range(4)]),
    ("poignée glissée", lambda lab: lab.drag(True), lambda lab: lab.drag(False)),
    ("touche flèche du bas", lambda lab: lab.key(win32con.VK_DOWN, 3), lambda lab: lab.key(win32con.VK_UP, 30)),
    ("touche page suivante", lambda lab: lab.key(win32con.VK_NEXT, 1), lambda lab: lab.key(win32con.VK_PRIOR, 5)),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--personnage", required=True, help="personnage dont la fenêtre sert au test")
    args = parser.parse_args()
    enable_dpi_awareness()
    windows = [w for w in find_windows("- Dofus Retro") if w.title.lower().startswith(args.personnage.lower())]
    if not windows:
        print(f"Aucune fenêtre du jeu pour {args.personnage}", file=sys.stderr)
        return 1
    if not confirm(f"Essais de défilement de la liste des sorts dans la fenêtre « {windows[0].title} » (arrière-plan)."):
        return 1
    folder = ROOT / "logs" / f"arriere_plan_sorts_{datetime.now():%Y%m%d_%H%M%S}"
    folder.mkdir(parents=True, exist_ok=True)
    control = StopControl("f12", "", corner=False).start()

    class Recorder:
        dir = folder

        def save(self, image, label, lossless=False, quality=80):
            path = folder / f"{label}.png"
            cv2.imencode(".png", image)[1].tofile(str(path))
            return path

        def keeps(self, lossless=False):
            return True

    game = Game(windows[0], control, Recorder(), Human(), act=True, background=True)
    lab = Lab(game)
    lines = []
    try:
        frame = lab.frame()
        if find_spell_window(frame) is None:
            game.click(find_spells_button(frame), frame.layout.spread(6, 6))
            time.sleep(1.0)
        for index, (label, forward, back) in enumerate(WAYS, 1):
            before = scroll_bar(lab.frame())
            if before is None:
                lines.append(f"{label} : fenêtre des sorts ou barre de défilement introuvable")
                continue
            if not before.at_top:
                back(lab)
                time.sleep(WAIT)
                before = scroll_bar(lab.frame()) or before
            forward(lab)
            time.sleep(WAIT)
            after_frame = lab.frame()
            after = scroll_bar(after_frame)
            Recorder().save(after_frame.image, f"{index:02d}_{label.replace(' ', '_')}")
            moved = after is not None and after.top != before.top
            lines.append(f"{label} : {'DÉFILE' if moved else 'sans effet'} (poignée y {before.top} -> "
                         f"{after.top if after else '?'})")
            print(lines[-1])
            if moved:
                back(lab)
                time.sleep(WAIT)
        frame = lab.frame()
        close = find_close_button(frame)
        if close is not None:
            game.click(close, frame.layout.spread(4, 4))
    finally:
        control.close()
        (folder / "rapport.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Rapport : {folder / 'rapport.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
