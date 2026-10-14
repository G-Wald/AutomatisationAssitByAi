"""Ouvre la fenêtre des sorts, clique sur chaque sort et enregistre sa fiche (affichée à gauche de la liste).

Depuis la racine du projet, le jeu ouvert et hors combat :
    .venv\\Scripts\\python -m tools.spell_sheets

Les captures vont dans captures/sorts_<date_heure>/ : envoie-les-moi, j'en tirerai la description des sorts.
F12 arrête.
"""

import sys
from datetime import datetime

import cv2

from bot.actions.input import Human
from bot.actions.stop import BotStopped, StopControl
from bot.brain.game import Game
from bot.capture.window import find_windows
from bot.config import ROOT, load_settings
from bot.perception.spellbook import find_close_button, find_spell_window, find_spells_button, name_point, read_spell_window


class _NoRecorder:
    def save(self, image, label, lossless=False, quality=80):
        return None


def main() -> int:
    settings = load_settings()
    matches = find_windows(settings["window"]["title"])
    if not matches:
        print("Fenêtre du jeu introuvable")
        return 1
    window = matches[0]
    control = StopControl((settings.get("safety") or {}).get("stop_key") or "f12").start()
    out = ROOT / "captures" / f"sorts_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    try:
        game = Game(window, control, _NoRecorder(), Human.from_settings(settings))
        window.bring_to_front()
        control.sleep(0.5)
        frame = game.look()
        if find_spell_window(frame) is None:
            button = find_spells_button(frame)
            if button is None:
                print("Bouton de la fenêtre des sorts introuvable (hors combat, barre d'icônes visible ?)")
                return 1
            game.click(button, frame.layout.spread(8, 8))
            game.sleep(1.0)
            frame = game.look()
        corner = find_spell_window(frame)
        entries = read_spell_window(frame)
        if corner is None or not entries:
            print("Fenêtre des sorts introuvable")
            return 1
        scale = frame.layout.scale
        for entry in entries:
            corner = find_spell_window(game.look()) or corner  # la liste se décale quand une fiche s'ouvre
            game.click(name_point(frame, corner, entry.row), (30 * scale, 3 * scale))
            game.sleep(1.0)
            path = out / f"sort_{entry.row + 1:02d}.png"
            path.write_bytes(cv2.imencode(".png", game.look().image)[1].tobytes())
            print(f"{path.relative_to(ROOT)}  ({entry.describe()})")
        close = find_close_button(game.look())
        if close is not None:
            game.click(close, frame.layout.spread(4, 4))
    except BotStopped:
        print("Arrêté")
    finally:
        control.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
