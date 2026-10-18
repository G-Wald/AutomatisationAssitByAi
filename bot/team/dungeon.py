"""Donjons, en équipe : l'entrée (18/10, donjon d'Incarnam, déroulé et captures de l'utilisateur).

Le chemin est décrit case par case dans config/donjons_entree.yaml : toute l'équipe prend le soleil de l'entrée (la grotte,
même coordonnées dehors et dedans : c'est le décor qui change), le principal foule la dalle qui libère le soleil de
la salle suivante, toute l'équipe le prend, puis chacun parle au PNJ et lui donne sa clef. Les combats de salle en
salle et la sortie viendront ensuite (étapes suivantes des tests de l'utilisateur).
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from bot.brain.travel import _click_spot, same_map
from bot.config import ROOT
from bot.perception.chat import find_text
from bot.perception.frame import Frame
from bot.perception.grid import Cell
from bot.perception.popups import find_context_menu, find_npc_dialog, find_small_menu
from bot.team.member import Member

log = logging.getLogger(__name__)

DUNGEONS_FILE = ROOT / "config" / "donjons_entree.yaml"  # donjons.yaml : recherche du 12/10 (candidats, sources)
CHANGE_WAIT = 12.0  # s : clic sur un soleil -> autre décor (marche jusqu'au soleil comprise)
SETTLE = 1.0  # s : nouveau décor affiché, le temps qu'il finisse de se dessiner
PLATE_WALK = 5.0  # s : marche jusqu'à la dalle, et la pierre qui se déplace
MENU_WAIT = 3.0  # s : clic sur le PNJ -> menu ou bulle
NPC_BODY_UP = 33  # px de référence : le corps du PNJ, au-dessus du centre de sa case (18/10 : 35 px)
STEPS = ("entree", "dalle", "soleil", "pnj")  # étapes de l'entrée, dans l'ordre
TALK_READS = 3  # lectures de « Parler » dans le menu du PNJ, 0,3 s d'écart  # px de référence autour du clic : où lire le menu du PNJ (« Parler »)


@dataclass(frozen=True)
class DungeonSpec:
    key: str
    name: str
    world: str
    outside: tuple[int, int]
    entrance: Cell
    plate: Cell | None
    sun: Cell | None
    npc: Cell
    reply: str


def load_dungeons(path: Path = DUNGEONS_FILE) -> dict[str, DungeonSpec]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cell = lambda v: tuple(v) if v else None  # noqa: E731
    return {key: DungeonSpec(key, d["nom"], d.get("monde", key), tuple(d["carte"]), cell(d["entree"]),
                             cell(d.get("dalle")), cell(d.get("soleil")), cell(d["pnj"]), str(d["reponse"]))
            for key, d in data.items()}


class DungeonEntry:
    def __init__(self, members: list[Member], spec: DungeonSpec) -> None:
        self.members = members  # le principal d'abord
        self.spec = spec

    def enter(self, start: str = "entree") -> bool:
        """True si toute l'équipe a donné sa clef et passé la porte. `start` : étape où reprendre (STEPS), l'équipe
        déjà là (test d'une étape : « --donjon incarnam:pnj »)."""
        spec = self.spec
        steps = STEPS[STEPS.index(start):]
        log.info("Donjon : %s, entrée de l'équipe (étapes : %s)", spec.name, ", ".join(steps))
        if "entree" in steps and not self._all_take(spec.entrance, "l'entrée"):
            return False
        if "dalle" in steps and spec.plate is not None:
            self._step_on_plate(self.members[0])
        if "soleil" in steps and spec.sun is not None and not self._all_take(spec.sun, "le soleil libéré par la dalle"):
            return False
        refused = [m.name for m in self.members if not self._give_key(m)]
        if refused:
            log.warning("Donjon : %s n'ont pas passé la porte", ", ".join(refused))
            return False
        log.info("Donjon : toute l'équipe est entrée dans %s", spec.name)
        return True

    # --- Étapes ---

    def _all_take(self, cell: Cell, what: str) -> bool:
        """Chacun clique le soleil de `cell` (le principal d'abord), puis on vérifie que le décor de chacun a changé ;
        un second clic pour qui n'est pas passé (case cachée par un autre personnage : un autre endroit du soleil)."""
        log.info("Donjon : toute l'équipe prend %s (case %d,%d)", what, *cell)
        before = {m.name: self._click_sun(m, cell, 0) for m in self.members}
        late = [m for m in self.members if not self._changed(m, before[m.name])]
        for member in late:
            log.info("Donjon : %s n'est pas passé, je reclique sur %s", member.name, what)
            frame = self._click_sun(member, cell, 1)
            if not self._changed(member, frame):
                path = member.game.recorder.save(member.game.look(park=False).image, "donjon_pas_passe", lossless=True)
                log.warning("Donjon : %s ne passe pas par %s (capture : %s)", member.name, what, path)
                return False
        return True

    def _step_on_plate(self, member: Member) -> None:
        game = member.game
        frame = game.look(park=False)
        log.info("Donjon : %s marche sur la dalle (case %d,%d) pour libérer le passage", member.name, *self.spec.plate)
        game.click(frame.grid.center(self.spec.plate), frame.grid.click_spread())
        game.sleep(PLATE_WALK)

    def _give_key(self, member: Member) -> bool:
        """Clic sur le PNJ ; « Parler » si un menu s'ouvre ; dans sa bulle, la réponse qui donne la clef ; puis le
        décor change (le personnage passe la porte)."""
        game, spec = member.game, self.spec
        frame = game.look(park=False)
        x, y = frame.grid.center(spec.npc)
        point = (x, y - frame.layout.length(NPC_BODY_UP))
        log.info("Donjon : %s parle au PNJ (case %d,%d)", member.name, *spec.npc)
        game.click(point, frame.layout.spread(3, 3))
        shown = game.wait_until(lambda f: find_npc_dialog(f) is not None or find_small_menu(f, around=point) is not None
                                or find_context_menu(f) is not None, MENU_WAIT, park=False)
        if shown is None:
            return self._failed(member, "ni menu ni bulle après le clic sur le PNJ")
        if find_npc_dialog(shown) is None:
            talk = self._talk_entry(game, shown, point)
            if talk is None:
                return self._failed(member, "menu du PNJ sans « Parler »")
            game.click(talk, shown.layout.spread(8, 3))
            shown = game.wait_until(lambda f: find_npc_dialog(f) is not None, MENU_WAIT, park=False)
            if shown is None:
                return self._failed(member, "pas de bulle après « Parler »")
        reply = find_text(shown, shown.layout.map_box, spec.reply)
        if reply is None:
            return self._failed(member, f"pas de réponse « {spec.reply} » dans la bulle (clef absente ?)")
        log.info("Donjon : %s répond « %s »", member.name, spec.reply)
        game.click(reply, shown.layout.spread(10, 3))
        if not self._changed(member, frame):  # la salle d'avant le dialogue (la bulle fermée ne compte pas)
            return self._failed(member, "réponse cliquée, mais il n'a pas passé la porte")
        return True

    def _talk_entry(self, game, frame: Frame, point) -> tuple[float, float] | None:
        """« Parler » dans le menu du PNJ (son nom, puis « Parler ») : lu par l'OCR sur la carte (un cadre serré
        autour du clic ne le lisait pas, 18/10), relu le temps que le menu finisse de s'afficher ; à défaut, le cadre
        beige du menu (sa ligne « Parler »)."""
        for attempt in range(TALK_READS):
            talk = find_text(frame, frame.layout.map_box, "Parler")
            if talk is not None:
                return talk
            game.sleep(0.3)
            frame = game.look(park=False)
        menu = find_small_menu(frame, around=point) or find_context_menu(frame)
        if menu is None:
            return None
        x0, y0, x1, y1 = menu
        log.info("Donjon : « Parler » pas lu, je clique dans le menu")
        return (x0 + x1) / 2, (y0 + y1) / 2

    # --- Outils ---

    def _click_sun(self, member: Member, cell: Cell, spot: int) -> Frame:
        frame = member.game.look(park=False)
        point, spread = _click_spot(frame, cell, spot)
        member.game.click(point, spread)
        return frame

    def _changed(self, member: Member, before: Frame) -> bool:
        """Le décor de ce personnage a changé (autre salle) dans les CHANGE_WAIT secondes."""
        after = member.game.wait_until(lambda f: find_npc_dialog(f) is None and not same_map(before, f), CHANGE_WAIT,
                                       park=False)
        if after is None:
            return False
        member.game.sleep(SETTLE)
        return True

    def _failed(self, member: Member, why: str) -> bool:
        path = member.game.recorder.save(member.game.look(park=False).image, "donjon_pnj", lossless=True)
        log.warning("Donjon : %s : %s (capture : %s)", member.name, why, path)
        return False
