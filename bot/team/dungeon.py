"""Donjons, en équipe (18/10, donjon d'Incarnam, déroulé et captures de l'utilisateur).

Le chemin est décrit case par case dans config/donjons_entree.yaml :
- entrée : toute l'équipe prend le soleil de l'entrée (la grotte, mêmes coordonnées dehors et dedans : c'est le décor
  qui change), le principal foule la dalle qui libère le soleil de la salle suivante, toute l'équipe le prend, puis
  chacun parle au PNJ et lui donne sa clef ;
- salles : le combat de chaque salle, lancé comme d'habitude (TeamBot) ; la victoire mène l'équipe dans la salle
  suivante, sans marcher. Après le combat de la dernière salle (« salles », leur nombre) : la salle de fin (nombre
  inconnu : la salle sans groupe, deux recherches vides) ;
- sortie : chacun parle au PNJ de la salle de fin (clic droit) et répond « A la sortie. » : il est téléporté dehors,
  devant l'entrée ; et on recommence.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml

from bot.brain.popups import close_flash_menu
from bot.brain.travel import _click_spot, _gap, _scene, same_map
from bot.config import ROOT
from bot.perception.chat import find_text
from bot.perception.frame import Frame
from bot.perception.grid import Cell
from bot.perception.popups import (find_context_menu, find_flash_menu, find_known_popup, find_npc_dialog,
                                   find_small_menu, find_trophy_cross)
from bot.state.scene import Phase, read_scene
from bot.team.member import Member

log = logging.getLogger(__name__)

DUNGEONS_FILE = ROOT / "config" / "donjons_entree.yaml"  # donjons.yaml : recherche du 12/10 (candidats, sources)
CHANGE_WAIT = 12.0  # s : clic sur un soleil -> autre décor (marche jusqu'au soleil comprise)
SETTLE = 1.0  # s : nouveau décor affiché, le temps qu'il finisse de se dessiner
PLATE_WALK = 5.0  # s : marche jusqu'à la dalle, et la pierre qui se déplace
MENU_WAIT = 3.0  # s : clic sur le PNJ -> menu ou bulle
NPC_BODY_UP = 33  # px de référence : le corps du PNJ, au-dessus du centre de sa case (18/10 : 35 px)
STEPS = ("entree", "dalle", "soleil", "pnj")  # étapes de l'entrée, dans l'ordre
TALK_READS = 3  # lectures de « Parler » dans le menu du PNJ, 0,3 s d'écart
REPLY_READS = 5  # lectures de la réponse dans la bulle, 0,3 s d'écart (son texte s'écrit après le cadre)
EMPTY_ROOM = 2  # recherches de groupe vides d'affilée : salle de fin (une seule peut tomber pendant un affichage)
BUTTONS = {"gauche": "left", "droit": "right"}
PLACES_DIR = Path(__file__).resolve().parents[1] / "perception" / "assets" / "donjons"  # décors de référence
PLACES = {"entree": "dehors, devant l'entrée", "dalle": "salle des dalles", "pnj": "salle du PNJ"}  # étapes situables
SAME_PLACE = 12.0  # écart de décor (_gap de travel.py) sous lequel c'est la même salle (même : < 3, autre : > 30)


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
    exit_npc: Cell | None = None
    exit_reply: str | None = None
    exit_click: str = "right"
    rooms: int | None = None  # salles à combattre ; après la dernière, la salle de fin (PNJ de sortie)


def load_dungeons(path: Path = DUNGEONS_FILE) -> dict[str, DungeonSpec]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cell = lambda v: tuple(v) if v else None  # noqa: E731
    return {key: DungeonSpec(key, d["nom"], d.get("monde", key), tuple(d["carte"]), cell(d["entree"]),
                             cell(d.get("dalle")), cell(d.get("soleil")), cell(d["pnj"]), str(d["reponse"]),
                             cell(d.get("pnj_fin")), d.get("reponse_fin"), BUTTONS[d.get("clic_fin", "droit")],
                             d.get("salles"))
            for key, d in data.items()}


class DungeonEntry:
    def __init__(self, members: list[Member], spec: DungeonSpec, hide_key: str = "e") -> None:
        self.members = members  # le principal d'abord
        self.spec = spec
        self.hide_key = hide_key  # touche qui cache les autres joueurs (travel.hide_players_key), tenue sur la carte
        self._place_scenes: dict[str, np.ndarray] | None = None  # décors de référence (locate), lus une fois

    def enter(self, start: str | None = None, only: set[str] | None = None) -> bool:
        """True si toute l'équipe a donné sa clef et passé la porte. Sans `start` : chacun est d'abord situé (locate) et
        ne fait que les étapes qui lui manquent (utilisateur, 19/10 : le bot doit ramener tout le monde au même endroit ;
        Pesopepe resté dehors, les autres dans la salle des dalles). `start` : étape où reprendre (STEPS) ; `only` :
        les seuls personnages qui font cette étape (noms en minuscules), les autres l'ayant déjà faite
        (« --donjon incarnam:entree:Pesopepe »)."""
        if start is None:
            where = {m.name: self.locate(m) for m in self.members}
            groups: dict[str | None, list[str]] = {}
            for name, place in where.items():
                groups.setdefault(place, []).append(name)
            log.info("Donjon : où est chacun : %s", " ; ".join(f"{PLACES.get(place, 'ailleurs')} : {', '.join(names)}"
                                                           for place, names in groups.items()))
            if None in groups:
                log.warning("Donjon : %s ni devant l'entrée ni dans les premières salles : je ne sais pas les ramener",
                            ", ".join(groups[None]))
                return False
            stages = {m.name: STEPS.index(where[m.name]) for m in self.members}
        else:
            first = STEPS.index(start)
            stages = {m.name: first if not only or m.name.lower() in only else first + 1 for m in self.members}
        return self._enter(stages)

    def locate(self, member: Member) -> str | None:
        """Où en est ce personnage dans l'entrée : « entree » (dehors, devant), « dalle » (salle des dalles), « pnj »
        (devant le PNJ), d'après son décor comparé à ceux de référence (assets/donjons/<donjon>_<étape>.jpg ; même
        salle : écart < 3, autre salle : > 30) ; None ailleurs (déjà dans les salles, autre carte)."""
        self._close_bubble(member)
        scene = _scene(member.game.look(park=False))
        gaps = {step: _gap(scene, ref) for step, ref in self._places().items()}
        step = min(gaps, key=gaps.get, default=None)
        return step if step is not None and gaps[step] < SAME_PLACE else None

    def _places(self) -> dict[str, np.ndarray]:
        if self._place_scenes is None:
            self._place_scenes = {}
            for step in PLACES:
                image = cv2.imread(str(PLACES_DIR / f"{self.spec.key}_{step}.jpg"))
                if image is not None:
                    self._place_scenes[step] = _scene(Frame.of(image))
        return self._place_scenes

    def _enter(self, stages: dict[str, int]) -> bool:
        """Les étapes de l'entrée, chacun à partir de la sienne (`stages` : nom -> indice dans STEPS de la première
        étape qui lui manque)."""
        spec = self.spec
        need = lambda step: [m for m in self.members if stages[m.name] <= STEPS.index(step)]  # noqa: E731
        log.info("Donjon : %s, entrée de l'équipe (%s)", spec.name,
                 ", ".join(f"{m.name} : {STEPS[min(stages[m.name], len(STEPS) - 1)]}" for m in self.members))
        late = need("entree")
        if late and not self._all_take(late, spec.entrance, "l'entrée"):
            return False
        if need("dalle") and spec.plate is not None:
            self._step_on_plate(need("dalle")[0])
        late = need("soleil")
        if late and spec.sun is not None and not self._all_take(late, spec.sun, "le soleil libéré par la dalle"):
            return False
        members = need("pnj")
        refused = [m for m in members if not self._give_key(m)]
        for member in list(refused):  # une seconde fois, la bulle refermée
            log.info("Donjon : %s n'a pas passé la porte, je lui reparle", member.name)
            self._close_bubble(member)
            if self._give_key(member):
                refused.remove(member)
        if refused:
            log.warning("Donjon : %s n'ont pas passé la porte", ", ".join(m.name for m in refused))
            return False
        log.info("Donjon : toute l'équipe est entrée dans %s", spec.name)
        return True

    def leave(self) -> bool:
        """Salle de fin : chacun parle au PNJ et choisit la sortie. True si toute l'équipe est dehors."""
        spec = self.spec
        if spec.exit_npc is None or not spec.exit_reply:
            log.warning("Donjon : %s : sortie pas décrite (pnj_fin, reponse_fin)", spec.name)
            return False
        stuck = [m.name for m in self.members if not self._talk(m, spec.exit_npc, spec.exit_reply, spec.exit_click,
                                                                "n'est pas sorti")]
        if stuck:
            log.warning("Donjon : %s encore dans le donjon", ", ".join(stuck))
            return False
        log.info("Donjon : toute l'équipe est sortie de %s", spec.name)
        return True

    # --- Étapes ---

    def _all_take(self, members: list[Member], cell: Cell, what: str) -> bool:
        """Chacun clique le soleil de `cell` (le principal d'abord), puis on vérifie que le décor de chacun a changé ;
        un second clic pour qui n'est pas passé (case cachée par un autre personnage : un autre endroit du soleil).
        Tous les retardataires sont retentés et nommés (19/10 : seul Pesopepe signalé, Pombro aussi resté dehors)."""
        log.info("Donjon : %s prend %s (case %d,%d)", ", ".join(m.name for m in members), what, *cell)
        before = {m.name: self._click_sun(m, cell, 0) for m in members}
        late = [m for m in members if not self._changed(m, before[m.name])]
        stuck = []
        for member in late:
            log.info("Donjon : %s n'est pas passé, je reclique sur %s", member.name, what)
            frame = self._click_sun(member, cell, 1)
            if not self._changed(member, frame):
                path = member.game.recorder.save(member.game.look(park=False).image, "donjon_pas_passe", lossless=True)
                log.warning("Donjon : %s ne passe pas par %s (capture : %s)", member.name, what, path)
                stuck.append(member.name)
        if stuck:
            log.warning("Donjon : %s pas passé(s) par %s", ", ".join(stuck), what)
        return not stuck

    def _step_on_plate(self, member: Member) -> None:
        game = member.game
        frame = game.look(park=False)
        log.info("Donjon : %s marche sur la dalle (case %d,%d) pour libérer le passage", member.name, *self.spec.plate)
        self._hidden_click(member, frame.grid.center(self.spec.plate), frame.grid.click_spread())
        game.sleep(PLATE_WALK)

    def _give_key(self, member: Member) -> bool:
        return self._talk(member, self.spec.npc, self.spec.reply, "left", "n'a pas passé la porte")

    def _talk(self, member: Member, npc: Cell, answer: str, button: str, not_gone: str) -> bool:
        """Clic sur le PNJ ; « Parler » si un menu s'ouvre ; dans sa bulle, la ligne `answer` ; puis le décor change
        (porte passée, ou téléporté dehors)."""
        game = member.game
        frame = game.look(park=False)
        x, y = frame.grid.center(npc)
        point = (x, y - frame.layout.length(NPC_BODY_UP))
        log.info("Donjon : %s parle au PNJ (case %d,%d)", member.name, *npc)
        game.click(point, frame.layout.spread(3, 3), button=button)
        shown = game.wait_until(lambda f: find_npc_dialog(f) is not None or find_small_menu(f, around=point) is not None
                                or find_context_menu(f) is not None, MENU_WAIT, park=False)
        if shown is None:
            return self._failed(member, "ni menu ni bulle après le clic sur le PNJ")
        if find_npc_dialog(shown) is None and button == "right":
            # Clic droit : le PNJ ouvre sa bulle, sans menu. Un menu, c'est qu'il n'était pas sous le clic (18/10 : le
            # menu de Flash, son milieu cliqué ouvrait ses Options) : refermé, et rien d'autre.
            if find_flash_menu(shown, around=point) is not None:
                close_flash_menu(game, shown, point)
            return self._failed(member, "un menu au lieu de la bulle du PNJ (pas sous le clic ?)")
        if find_npc_dialog(shown) is None:
            talk = self._talk_entry(game, shown, point)
            if talk is None:
                return self._failed(member, "menu du PNJ sans « Parler »")
            game.click(talk, shown.layout.spread(8, 3))
            shown = game.wait_until(lambda f: find_npc_dialog(f) is not None, MENU_WAIT, park=False)
            if shown is None:
                return self._failed(member, "pas de bulle après « Parler »")
        reply = self._read_reply(game, shown, answer)
        if reply is None:
            return self._failed(member, f"pas de réponse « {answer} » dans la bulle (clef absente ?)")
        log.info("Donjon : %s répond « %s »", member.name, answer)
        game.click(reply, shown.layout.spread(10, 3))
        if not self._changed(member, frame):  # la salle d'avant le dialogue (la bulle fermée ne compte pas)
            return self._failed(member, f"réponse cliquée, mais il {not_gone}")
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

    def _read_reply(self, game, frame: Frame, answer: str) -> tuple[float, float] | None:
        """La ligne `answer` dans la bulle, relue le temps que son texte s'écrive (18/10 : bulle vue, texte pas encore
        là, Dinarpepe et Vieenjaune restés devant la porte, leur clef en poche)."""
        for attempt in range(REPLY_READS):
            if attempt:
                game.sleep(0.3)
                frame = game.look(park=False)
            reply = find_text(frame, frame.layout.map_box, answer, exact=True)
            if reply is not None:
                return reply
        return None

    # --- Outils ---

    def _close_bubble(self, member: Member) -> None:
        frame = member.game.look(park=False)
        cross = find_npc_dialog(frame)
        if cross is not None:
            member.game.click(cross, frame.layout.spread(2, 2))
            member.game.sleep(0.5)

    def _click_sun(self, member: Member, cell: Cell, spot: int) -> Frame:
        frame = member.game.look(park=False)
        point, spread = _click_spot(frame, cell, spot)
        self._hidden_click(member, point, spread)
        return frame

    def _hidden_click(self, member: Member, point, spread) -> None:
        """Clic sur la carte, les autres joueurs cachés (touche E tenue, comme pour les soleils des cartes ; rappel de
        l'utilisateur, 19/10) : devant la grotte, des marchands debout près du soleil, Pesopepe resté dehors."""
        if not self.hide_key:
            member.game.click(point, spread)
            return
        with member.game.holding(self.hide_key):
            member.game.click(point, spread)

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


class DungeonFarm:
    """Le donjon en boucle : entrée, combats de salle en salle (la boucle d'équipe, TeamBot.step), sortie."""

    def __init__(self, bot, spec: DungeonSpec, runs: int) -> None:
        self.bot = bot
        self.spec = spec
        self.runs = runs
        travel = getattr(bot, "travel_settings", None)
        self.entry = DungeonEntry(bot.members, spec, getattr(travel, "hide_players_key", "e"))

    def run(self, start: str | None = None, only: set[str] | None = None, room: int = 1) -> int:
        """Nombre de passages complets (entrée, salles, sortie). `start` : étape de l'entrée où reprendre (STEPS), ou
        « salles » pour l'équipe déjà passée, aux combats de la salle `room` ; sans : chacun situé (DungeonEntry.enter)."""
        done = 0
        for run in range(1, self.runs + 1):
            log.info("Donjon : passage %d/%d", run, self.runs)
            self._close_windows()
            if run == 1 and start == "salles":
                log.info("Donjon : l'équipe déjà dans les salles, je reprends aux combats")
            elif not (self.entry.enter(start, only) if run == 1 else self.entry.enter()):
                break
            if not self._fight_rooms(room if run == 1 and start == "salles" else 1):
                break
            self._close_windows()
            if not self.entry.leave():
                break
            done += 1
            log.info("Donjon : passage %d/%d terminé", run, self.runs)
        log.info("Donjon : %d passage(s) complet(s) sur %d", done, self.runs)
        return done

    def _close_windows(self) -> None:
        """Fenêtre connue restée ouverte chez un personnage (18/10 : « Options », ouverte par un clic dans le menu de
        Flash) : fermée avant les soleils et les PNJ, qui se cliquent sur la carte qu'elle cache. De même la carte des
        récompenses de trophées (19/10), par sa croix."""
        for member in self.bot.members:
            frame = member.game.look(park=False)
            cross = find_trophy_cross(frame)
            if cross is not None:
                log.info("Donjon : %s : récompenses de trophées affichées, je les ferme (croix)", member.name)
                member.game.click(cross, frame.layout.spread(2, 2))
                member.game.sleep(0.5)
                frame = member.game.look(park=False)
            if find_known_popup(frame, member.popups.specs) is not None:
                member.popups.handle(frame, None)
                member.game.sleep(0.5)

    def _fight_rooms(self, first: int = 1) -> bool:
        """Les combats de chaque salle, de la salle `first` à la dernière (spec.rooms) ; le PNJ de fin n'est cherché
        qu'après (utilisateur, 18/10 : retenir le nombre de salles). Salle 2, ce jour-là : un Petit Tournesol au bord,
        deux clics à côté, salle crue finie et PNJ de fin cherché devant lui. Nombre de salles inconnu : jusqu'à la salle
        sans groupe (EMPTY_ROOM recherches vides, aucun groupe vu, pas seulement des groupes ratés)."""
        bot = self.bot
        bot._empty_searches = 0
        if bot.engager is not None:
            bot.engager.new_map()
        rooms = self.spec.rooms
        fights, won = bot.fights, 0
        while not bot.finished:
            bot.step()
            if bot.fights - fights > won:
                won = bot.fights - fights
                if rooms:
                    log.info("Donjon : salle %d/%d finie", first + won - 1, rooms)
            if bot._round_due or read_scene(bot.leader.game.look(park=False)).phase is not Phase.OUT_OF_COMBAT:
                continue
            if rooms:
                if first + won > rooms:
                    log.info("Donjon : salle de fin (les %d salles finies)", rooms)
                    return True
            elif bot._empty_searches >= EMPTY_ROOM and getattr(bot.engager, "groups_seen", 0) == 0:
                log.info("Donjon : salle de fin (plus de groupe), %d combat(s) dans ce passage", won)
                return True
        log.warning("Donjon : arrêt pendant les combats (%s)", bot._stop_reason or "session terminée")
        return False
