"""Analyse des captures comme le ferait le bot, sans le jeu, et enregistre une version annotée.

Depuis la racine du projet :
    .venv\\Scripts\\python -m tools.analyze tests\\fixtures\\combat_mon_tour.png
    .venv\\Scripts\\python -m tools.analyze captures\\*.png
"""

import argparse
import glob
import sys
from pathlib import Path

import cv2
import numpy as np

from bot.config import ROOT
from bot.debug.draw import annotate
from bot.perception.fighters import find_fighters
from bot.perception.frame import Frame
from bot.perception.groups import find_group_tooltips
from bot.perception.popups import find_known_popup, find_unknown_window, load_popup_specs
from bot.perception.sight import read_range
from bot.perception.ui import movement_zone, spell_range_visible, tactical_mode
from bot.state.scene import read_scene

OUT_DIR = ROOT / "captures" / "analyse"


def analyze(path: Path) -> None:
    image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        print(f"{path} : image illisible")
        return
    frame = Frame.of(image)
    scene = read_scene(frame)
    fighters = find_fighters(frame)
    zone = movement_zone(frame)
    known = find_known_popup(frame, load_popup_specs())
    window = find_unknown_window(frame)
    groups = find_group_tooltips(frame)

    print(f"{path}  ({frame.layout.width}x{frame.layout.height}{'' if frame.layout.matches_reference() else ', proportions inhabituelles'})")
    print(f"  état       : {scene.summary()}")
    for fighter in fighters:
        print(f"  combattant : {fighter.team:4s} case {fighter.cell}  ({fighter.x:.0f}, {fighter.y:.0f})")
    print(f"  tactique   : {'oui' if tactical_mode(frame) else 'non'}")
    print(f"  portée     : {'affichée' if spell_range_visible(frame) else 'non'}")
    reds = [f for f in fighters if f.team == "red"]
    if spell_range_visible(frame) and len(reds) == 1:
        view = read_range(frame, reds[0], fighters)
        print(f"  portée lue : {view.spell_range}, sans ligne de vue {sorted(view.no_sight)}, obstacles {sorted(view.obstacles)}")
    print(f"  zone verte : {len(zone)} cases")
    print(f"  fenêtre    : {known.spec.name if known else 'inconnue' if window else 'aucune'}")
    for group in groups:
        print(f"  groupe     : titre en ({group.x:.0f}, {group.title_y:.0f}), infobulle {group.box}, "
              f"{group.monsters} monstre(s){', collée à un bord' if group.clipped else ''}")

    marks = [(p, name) for p, name in ((scene.ready_button, "pret"), (scene.end_turn_button, "fin de tour")) if p]
    out = OUT_DIR / f"{path.stem}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    annotated = annotate(frame, fighters=fighters, cells=zone, marks=marks, boxes=([window] if window else []) + [g.box for g in groups],
                         text=scene.summary())
    out.write_bytes(cv2.imencode(".png", annotated)[1].tobytes())
    print(f"  annotée    : {out.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", help="captures (motifs * acceptés)")
    args = parser.parse_args()
    paths = [Path(p) for pattern in args.images for p in (glob.glob(pattern) or [pattern])]
    for path in paths:
        analyze(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
