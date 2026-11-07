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
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml

from bot.alerts import tell_user
from bot.brain.arena import map_cell
from bot.brain.popups import close_any_menu, close_flash_menu
from bot.brain.travel import _click_spot, _gap, _scene, same_map
from bot.config import ROOT
from bot.perception.chat import find_text
from bot.perception.frame import Frame
from bot.perception.grid import Cell
from bot.perception.popups import (find_context_menu, find_flash_menu, find_known_popup, find_npc_dialog,
                                   find_small_menu, find_trophy_cross)
from bot.state.scene import Phase, read_scene
from bot.team.keys import KeyManager, key_of, summary
from bot.team.member import Member

log = logging.getLogger(__name__)

DUNGEONS_FILE = ROOT / "config" / "donjons_entree.yaml"  # donjons.yaml : recherche du 12/10 (candidats, sources)
CHAIN_MAX = 12  # « Continuer la discussion » au plus dans une bulle d'entrée (07/11)
CHAIN_WAIT = 2.0  # s : question suivante annoncée par le serveur après un « Continuer »
CHAIN_SETTLE = 0.4  # s : texte de la bulle suivante écrit
NPC_WAIT = 30.0  # s au plus : un pas du PNJ de sortie annoncé quand le bot est relancé dans la salle (07/11)
NET_SILENT = 1.5  # s : clic sur une case sans déplacement annoncé par le serveur : pas pris (06/11)
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
SAME_ROOM_WAIT = 5.0  # s : toute l'équipe annoncée sur la carte du principal après une salle gagnée
# Salles à peu de places (05/11, donjon des Champs : salle 3 à 3 joueurs, salle 5 à 4 ; utilisateur : « le bot doit se
# débrouiller pour passer les salles où il y a moins de place, les plus haut level sont plus forts donc peuvent être
# moins par salle ») : l'équipe combat par vagues, le groupe de la salle réapparaissant après chaque victoire.
ROOMS_FILE = ROOT / "data" / "donjons.json"  # référence du client : places de chaque salle (placement p1 : joueurs)
RESPAWN_WAIT = 180.0  # s au plus : le groupe de la salle réapparu, pour la vague suivante
NO_KEY_LIMIT = 10**6
MISSING_MODES = ("non", "farm", "fabrication")  # donjon.cles_manquantes
FARM_MINUTES = 60.0  # donjon.farm_cles_minutes : durée d'un farm de la zone pour le butin des clés, au plus
FARM_LEAST = 5 * 60.0  # s : pas de farm des clés plus court (le temps restant de la session n'y suffit pas)
PASS_ESTIMATE = 18 * 60.0  # s : premier passage d'une session (Champs à 8 : 15 à 18 min, 05/11)
WAVE_RETRY = 3.0  # s entre deux recherches du groupe pendant l'attente de sa réapparition
SAME_PLACE = 12.0  # écart de décor (_gap de travel.py) sous lequel c'est la même salle (même : < 3, autre : > 30)


@dataclass(frozen=True)
class DungeonSpec:
    key: str
    name: str
    world: str
    outside: tuple[int, int]
    entrance: Cell | None  # soleil de l'entrée (grotte d'Incarnam) ; None : le PNJ est dehors (Bouftou, 30/10)
    plate: Cell | None
    sun: Cell | None
    npc: Cell
    reply: str
    exit_npc: Cell | None = None
    exit_reply: str | None = None
    exit_click: str = "right"
    rooms: int | None = None  # salles à combattre ; après la dernière, la salle de fin (PNJ de sortie)
    npc_map: int | None = None  # carte du PNJ (numéro annoncé par le serveur) : chacun y est situé par le réseau
    reply_id: int | None = None  # numéro de la réponse « clé » (DQ du serveur) : proposée seulement à qui a la clé
    keyring: str | None = None  # réponse du trousseau de clefs (31/10 : entrée sans clé), et son numéro
    keyring_id: int | None = None
    exit_cell: Cell | None = None  # sortie par une case de la salle de fin (05/11, donjon des Champs), sans PNJ
    # Ligne de la bulle d'entrée à cliquer avant la clé (07/11, Ensablé : « Continuer la discussion », 8 fois)
    chain: str | None = None
    # Autres numéros de la même réponse « clé » (07/11, Ensablé : 2947, raccourci proposé dès la première bulle à qui a
    # déjà fait le donjon, d'après l'utilisateur ; 2944 au bout de l'histoire)
    reply_more: tuple[int, ...] = ()

    @property
    def reply_ids(self) -> set[int]:
        return {i for i in (self.reply_id, *self.reply_more) if i is not None}
    # Carte de la zone dont les monstres donnent la clé (zone_cles) : farm de leur butin quand les clés manquent (06/11)
    key_zone: tuple[int, int] | None = None
    # Modèle du PNJ d'entrée et de sortie (GM, 5e champ ; 07/11, Ensablé : Igloute 798) : sa case annoncée par le
    # serveur passe avant celle du yaml (Igloute en 268 dans la salle de fin, 253 d'après StarLoco : 16 clics ratés)
    npc_model: int | None = None
    # Réponses de la bulle de sortie avant `exit_reply` (07/11, Ensablé : « Se laisser faire » (q3290), puis
    # « Se sauver en courant » (q3291), seule proposée, qui fait sortir)
    exit_before: tuple[str, ...] = ()


def load_dungeons(path: Path = DUNGEONS_FILE) -> dict[str, DungeonSpec]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cell = lambda v: tuple(v) if v else None  # noqa: E731
    return {key: DungeonSpec(key, d["nom"], d.get("monde", key), tuple(d["carte"]), cell(d.get("entree")),
                             cell(d.get("dalle")), cell(d.get("soleil")), cell(d["pnj"]), str(d["reponse"]),
                             cell(d.get("pnj_fin")), d.get("reponse_fin"), BUTTONS[d.get("clic_fin", "droit")],
                             d.get("salles"), d.get("pnj_carte"), d.get("reponse_id"), d.get("trousseau"),
                             d.get("trousseau_id"), cell(d.get("sortie_case")), key_zone=cell(d.get("zone_cles")),
                             chain=d.get("suite"), reply_more=tuple(d.get("reponse_id_autres") or ()),
                             npc_model=d.get("pnj_modele"), exit_before=tuple(d.get("reponses_fin_avant") or ()))
            for key, d in data.items()}


class DungeonEntry:
    def __init__(self, members: list[Member], spec: DungeonSpec, hide_key: str = "e") -> None:
        self.members = members  # le principal d'abord
        self.spec = spec
        self.hide_key = hide_key  # touche qui cache les autres joueurs (travel.hide_players_key), tenue sur la carte
        self._place_scenes: dict[str, np.ndarray] | None = None  # décors de référence (locate), lus une fois
        self._opened_at: dict[str, float] = {}  # bulle du PNJ ouverte (time.monotonic()), par personnage
        # Trousseau de clefs (donjon.trousseau, DungeonFarm) : `keyring` l'autorise ; `keyring_first` le prend avant la
        # clé quand le serveur le propose (07/11, utilisateur : « utiliser trousseau de clé en priorité si c'est
        # disponible ») ; sans `keyring_first`, seulement pour qui n'a pas la clé (31/10).
        self.keyring = False
        self.keyring_first = False
        self._with_key: set[str] = set()  # au trousseau en ayant aussi la clé : la clé si la porte refuse le trousseau
        self._npc_waited = False  # pas du PNJ de sortie attendus en vain (carte pas annoncée) : plus d'attente

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
        net = getattr(member.game, "net", None)
        here = net.map_id if net is not None and net.alive() else None
        if self.spec.npc_map is not None and here is not None:
            # 30/10 : carte annoncée par le serveur, plutôt que le décor (pas de décor de référence pour Bouftou).
            return "pnj" if here == self.spec.npc_map else None
        if self.spec.npc_map is not None:
            # 05/11 : fenêtres déjà connectées au lancement, aucun changement de carte depuis : carte du serveur
            # inconnue, les 8 crus « ailleurs ». Le PNJ se tient dehors (Altaïr, Mawy) : coordonnées lues à l'écran.
            from bot.perception.maplabel import read_map_label

            label = read_map_label(member.game.look(park=False))
            if label is not None and label.coords == self.spec.outside:
                return "pnj"
        scene = _scene(member.game.look(park=False))
        gaps = {step: _gap(scene, ref) for step, ref in self._places().items()}
        step = min(gaps, key=gaps.get, default=None)
        return step if step is not None and gaps[step] < SAME_PLACE else None

    def away(self, member: Member) -> bool:
        """Sûrement hors du donjon et loin du PNJ de l'entrée : carte annoncée par le serveur qui n'est ni celle du PNJ
        ni une salle du donjon (data/donjons.json), ou, carte pas encore annoncée, coordonnées lues à l'écran autres que
        celles de l'entrée (les salles des Champs portent celles de l'entrée, 7,-25). False si ce n'est pas sûr (sans
        carte du PNJ : donjon d'Incarnam, situé par le décor ; salles du donjon inconnues)."""
        rooms = room_maps(self.spec.name)
        if self.spec.npc_map is None or not rooms:
            return False
        net = getattr(member.game, "net", None)
        here = net.map_id if net is not None and net.alive() else None
        if here is not None:
            return here != self.spec.npc_map and here not in rooms
        from bot.perception.maplabel import read_map_label

        label = read_map_label(member.game.look(park=False))
        return label is not None and label.coords is not None and tuple(label.coords) != tuple(self.spec.outside)

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
        if late and spec.entrance is not None and not self._all_take(late, spec.entrance, "l'entrée"):
            return False
        if need("dalle") and spec.plate is not None:
            self._step_on_plate(need("dalle")[0])
        late = need("soleil")
        if late and spec.sun is not None and not self._all_take(late, spec.sun, "le soleil libéré par la dalle"):
            return False
        return self._through_npc(need("pnj"))

    def _through_npc(self, members: list[Member]) -> bool:
        """Entrée par le PNJ en deux temps (31/10, utilisateur : « si tout le groupe n'a pas de clé il ne faut pas
        continuer et faire rentrer solo ») : chacun ouvre sa bulle et sa réponse est choisie (la clé, sinon le trousseau
        de clefs) d'après les réponses que le serveur lui propose (DQ), à défaut d'après la bulle lue ; si un seul ne
        peut pas entrer, toutes les bulles sont refermées et personne n'entre. Sinon tous répondent."""
        spec = self.spec
        opened, blocked = {}, []
        for member in members:
            bubble = self._open(member, spec.npc, "left")
            if not bubble:  # une seconde fois, ce qui est ouvert refermé
                self._close_bubble(member)
                bubble = self._open(member, spec.npc, "left")
            answer = self._entry_answer(member, bubble[1]) if bubble else None
            if answer is None:
                blocked.append(member.name)
            else:
                opened[member.name] = (member, bubble, answer)
        if blocked:
            for member in members:  # toutes les bulles, celle de qui bloque comprise
                self._close_bubble(member)
            tell_user(f"{spec.name} : {', '.join(blocked)} sans clé (ou bulle du PNJ pas ouverte) : personne n'est "
                      f"entré.")
            log.warning("Donjon : %s ne peu(ven)t pas entrer (ni clé ni trousseau, ou bulle pas ouverte) : personne "
                        "n'entre", ", ".join(blocked))
            return False
        refused = []
        for member, (before, shown), answer in opened.values():
            if not self._reply(member, before, shown, answer, "n'a pas passé la porte"):
                refused.append(member)
        for member in list(refused):  # une seconde fois, la bulle refermée (sa réponse est connue)
            answer = opened[member.name][2]
            if answer == spec.keyring and member.name in self._with_key:
                # Trousseau refusé (déjà servi ?) : la clé, qu'il a aussi (07/11).
                log.info("Donjon : %s n'est pas entré avec le trousseau : je lui reparle, avec la clé", member.name)
                answer = spec.reply
            else:
                log.info("Donjon : %s n'a pas passé la porte, je lui reparle", member.name)
            self._close_bubble(member)
            if self._talk(member, spec.npc, answer, "left", "n'a pas passé la porte"):
                refused.remove(member)
        if refused:
            log.warning("Donjon : %s n'ont pas passé la porte", ", ".join(m.name for m in refused))
            return False
        log.info("Donjon : toute l'équipe est entrée dans %s", spec.name)
        return True

    def _entry_answer(self, member: Member, shown: Frame) -> str | None:
        """Réponse d'entrée de ce personnage, bulle ouverte : le trousseau s'il est proposé et `keyring_first` (07/11,
        utilisateur : en priorité), sinon la clé, sinon le trousseau (`keyring`) ; None s'il n'a ni l'un ni l'autre.
        D'après les réponses proposées par le serveur (DQ entendu depuis l'ouverture), à défaut la bulle lue."""
        spec = self.spec
        self._with_key.discard(member.name)
        net = getattr(member.game, "net", None)
        question = getattr(net, "question", None) if net is not None and net.alive() else None
        answers = None
        if question is not None and spec.reply_ids \
                and net.question_at >= self._opened_at.get(member.name, 0.0):
            answers, source = question[1], "d'après le jeu"
            key = bool(spec.reply_ids & set(answers))
            ring = bool(self.keyring and spec.keyring and spec.keyring_id in answers)
        else:
            source = "bulle lue"
            key = self._read_reply(member.game, shown, spec.reply) is not None
            ring = bool(self.keyring and spec.keyring and (self.keyring_first or not key)
                        and self._read_reply(member.game, shown, spec.keyring) is not None)
        if ring and (self.keyring_first or not key):
            if key:
                self._with_key.add(member.name)
                log.info("Donjon : %s entrera avec le trousseau, sa clé gardée (%s)", member.name, source)
            else:
                log.info("Donjon : %s n'a pas de clé (%s) : il entrera avec le trousseau", member.name, source)
            return spec.keyring
        if key:
            return spec.reply
        if answers is not None:
            log.info("Donjon : %s : ni clé ni trousseau proposés par le jeu (réponses %s)", member.name, answers)
        return None

    def leave(self) -> bool:
        """Salle de fin : chacun parle au PNJ et choisit la sortie. True si toute l'équipe est dehors."""
        spec = self.spec
        if spec.exit_cell is not None:
            # 05/11, donjon des Champs : dans la salle de fin, une case qui ramène dehors (pas de PNJ).
            if self._all_take(self.members, spec.exit_cell, "la sortie"):
                log.info("Donjon : toute l'équipe est sortie de %s", spec.name)
                return True
            tell_user(f"{spec.name} : un ou plusieurs personnages pas sortis du donjon (case de sortie) : bot arrêté.")
            return False
        if spec.exit_npc is None or not spec.exit_reply:
            log.warning("Donjon : %s : sortie pas décrite (pnj_fin, reponse_fin)", spec.name)
            return False
        stuck = [m for m in self.members if not self._talk_exit(m)]
        for member in list(stuck):  # une seconde fois, la bulle refermée (30/10 : Fourboun, pas de bulle après « Parler »)
            log.info("Donjon : %s n'est pas sorti, je lui reparle", member.name)
            self._close_bubble(member)
            if self._talk_exit(member):
                stuck.remove(member)
        stuck = [m.name for m in stuck]
        if stuck:
            log.warning("Donjon : %s encore dans le donjon", ", ".join(stuck))
            tell_user(f"{spec.name} : {', '.join(stuck)} pas sorti(s) du donjon (PNJ de fin) : bot arrêté.")
            return False
        log.info("Donjon : toute l'équipe est sortie de %s", spec.name)
        return True

    # --- Étapes ---

    def _all_take(self, members: list[Member], cell: Cell, what: str) -> bool:
        """Chacun clique le soleil de `cell` (le principal d'abord), puis on vérifie que le décor de chacun a changé ;
        un second clic pour qui n'est pas passé (case cachée par un autre personnage : un autre endroit du soleil).
        Tous les retardataires sont retentés et nommés (19/10 : seul Pesopepe signalé, Pombro aussi resté dehors)."""
        log.info("Donjon : %s prend %s (case %d,%d)", ", ".join(m.name for m in members), what, *cell)
        # 05/11 : changement de carte compté depuis ce clic (_changed : sinon depuis la dernière bulle, ou 0 : n'importe
        # quelle carte d'avant comptait comme une arrivée).
        before = {}
        for member in members:
            self._opened_at[member.name] = time.monotonic()
            before[member.name] = self._click_sun(member, cell, 0)
        # 06/11 (journal 210755) : 3 personnages sur 8 pas passés par la sortie, recliqués seulement après 12 s
        # d'attente chacun (46 s). Comme les soleils des cartes (team/loop.py, NET_SUN_SILENT) : aucun déplacement
        # annoncé par le serveur peu après le clic, il n'a pas été pris ; recliqué tout de suite, à un autre endroit.
        if members:
            members[-1].game.sleep(max(0.0, NET_SILENT - (time.monotonic() - self._opened_at[members[-1].name])))
        for member in members:
            if self._silent(member):
                log.info("Donjon : %s ne bouge pas d'après le serveur, je reclique sur %s", member.name, what)
                self._opened_at[member.name] = time.monotonic()
                before[member.name] = self._click_sun(member, cell, 1)
        late = [m for m in members if not self._changed(m, before[m.name])]
        stuck = []
        for member in late:
            log.info("Donjon : %s n'est pas passé, je reclique sur %s", member.name, what)
            self._opened_at[member.name] = time.monotonic()
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

    def _talk_exit(self, member: Member) -> bool:
        """PNJ de sortie : sa bulle, les réponses `exit_before` une à une (la question suivante attendue du serveur),
        puis `exit_reply`, et le décor change (téléporté dehors)."""
        spec, game = self.spec, member.game
        bubble = self._open(member, spec.exit_npc, spec.exit_click)
        if not bubble:
            return False
        before, shown = bubble
        net = getattr(game, "net", None)
        for answer in spec.exit_before:
            line = self._read_reply(game, shown, answer)
            if line is None:
                return self._failed(member, f"pas de réponse « {answer} » dans la bulle de sortie")
            asked = time.monotonic()
            log.info("Donjon : %s répond « %s »", member.name, answer)
            game.click(line, shown.layout.spread(10, 3))
            if net is not None and net.alive():
                net.wait_for("question_at", asked, CHAIN_WAIT)
            game.sleep(CHAIN_SETTLE)
            shown = game.look(park=False)
        return self._reply(member, before, shown, spec.exit_reply, "n'est pas sorti")

    def _talk(self, member: Member, npc: Cell, answer: str, button: str, not_gone: str) -> bool:
        """Clic sur le PNJ ; « Parler » si un menu s'ouvre ; dans sa bulle, la ligne `answer` ; puis le décor change
        (porte passée, ou téléporté dehors)."""
        bubble = self._open(member, npc, button)
        return bool(bubble) and self._reply(member, *bubble, answer, not_gone)

    def _open(self, member: Member, npc: Cell, button: str) -> tuple[Frame, Frame] | None:
        """Bulle du PNJ ouverte (clic, « Parler » si un menu s'ouvre) : (la salle avant le dialogue, la bulle vue) ;
        None (dit au journal) sinon."""
        game = member.game
        story = npc == self.spec.npc  # le PNJ d'entrée (sa bulle à plusieurs étapes), avant la case du serveur
        npc = self._npc_cell(member, npc, roaming=not story)
        self._opened_at[member.name] = time.monotonic()
        frame = game.look(park=False)
        x, y = frame.grid.center(npc)
        point = (x, y - frame.layout.length(NPC_BODY_UP))
        log.info("Donjon : %s parle au PNJ (case %d,%d)", member.name, *npc)
        # 31/10, revue : joueurs cachés (touche E) pour le clic sur le PNJ aussi, comme pour les soleils (un joueur
        # devant Altaïr ouvrirait son menu).
        self._hidden_click(member, point, frame.layout.spread(3, 3), button)
        shown = game.wait_until(lambda f: find_npc_dialog(f) is not None or find_small_menu(f, around=point) is not None
                                or find_context_menu(f) is not None, MENU_WAIT, park=False)
        if shown is None:
            return self._failed(member, "ni menu ni bulle après le clic sur le PNJ")
        if find_npc_dialog(shown) is None and button == "right":
            # Clic droit : le PNJ ouvre sa bulle, sans menu. Un menu, c'est qu'il n'était pas sous le clic (18/10 : le
            # menu de Flash, son milieu cliqué ouvrait ses Options) : refermé, et rien d'autre.
            if find_flash_menu(shown, around=point) is not None:
                close_flash_menu(game, shown, point)
            else:
                close_any_menu(game, member.name)
            return self._failed(member, "un menu au lieu de la bulle du PNJ (pas sous le clic ?)")
        if find_npc_dialog(shown) is None:
            talk = self._talk_entry(game, shown, point)
            if talk is None:
                close_any_menu(game, member.name)  # 31/10 : le menu d'un autre (joueur...) jamais cliqué, refermé
                return self._failed(member, "menu du PNJ sans « Parler »")
            game.click(talk, shown.layout.spread(8, 3))
            shown = game.wait_until(lambda f: find_npc_dialog(f) is not None, MENU_WAIT, park=False)
            if shown is None:
                self._failed(member, "pas de bulle après « Parler »")
                return None
        if self.spec.chain and story:
            shown = self._through_chain(member, shown)
            if shown is None:
                return None
        return frame, shown

    def _npc_cell(self, member: Member, npc: Cell, roaming: bool = False) -> Cell:
        """Case du PNJ : celle annoncée par le serveur pour son modèle (GM de la carte, `spec.npc_model`), sinon `npc`
        (yaml). 07/11, Ensablé : Igloute marche dans la salle de fin (« GA;1;-1;... » toutes les ~20 s ; 268 = (14,18)
        à l'arrivée, 253 = (13,17) d'après StarLoco). `roaming` (PNJ de sortie) : bot relancé dans la salle, carte pas
        annoncée, le PNJ pas présenté (GM) mais ses pas oui : le seul acteur sans nom ni niveaux, attendu NPC_WAIT s
        une fois (ensuite, plus d'attente)."""
        model = self.spec.npc_model
        net = getattr(member.game, "net", None)
        if model is None or net is None or not net.alive():
            return npc
        guess = roaming and net.map_id is None
        deadline = time.monotonic() + (NPC_WAIT if guess and not self._npc_waited else 0.0)
        while True:
            actors = [a for a in dict(getattr(net, "actors", {}) or {}).values() if a.id < 0]
            found = [a for a in actors if a.name == str(model)]
            if not found and guess:
                found = [a for a in actors if not a.name and not getattr(a, "levels", None)]
            if len(found) == 1 or time.monotonic() >= deadline:
                break
            member.game.sleep(0.5)
        if guess and len(found) != 1:
            self._npc_waited = True
        if len(found) != 1:
            return npc
        cell = map_cell(found[0].cell)
        if cell != tuple(npc):
            log.info("Donjon : PNJ %d en %s d'après le serveur (%s dans la description)", model, cell, tuple(npc))
        return cell

    def _through_chain(self, member: Member, shown: Frame) -> Frame | None:
        """Bulle d'entrée à plusieurs étapes (07/11, donjon Ensablé : le capitaine Igloute raconte son histoire, 8 fois
        « Continuer la discussion » avant « Donner la clef et rentrer », d'après la langue du client ; à confirmer en
        jeu) : la ligne `spec.chain` cliquée jusqu'à la question qui propose la clé (ou le trousseau), vue par le
        serveur (DQ), à défaut lue dans la bulle. La bulle de cette question ; None si la suite ne mène nulle part."""
        spec, game = self.spec, member.game
        finals = spec.reply_ids | ({spec.keyring_id} if spec.keyring_id is not None else set())
        for step in range(CHAIN_MAX):
            net = getattr(game, "net", None)
            question = getattr(net, "question", None) if net is not None and net.alive() else None
            if question is not None and net.question_at >= self._opened_at.get(member.name, 0.0):
                if finals & set(question[1]):
                    return shown
            elif self._read_reply(game, shown, spec.reply) is not None:
                return shown
            line = self._read_reply(game, shown, spec.chain)
            if line is None:
                # Ni la clé ni la suite : la réponse est cherchée ensuite (_entry_answer), qui dira ce qui manque.
                return shown
            asked = time.monotonic()
            log.info("Donjon : %s : « %s » (%d)", member.name, spec.chain, step + 1)
            game.click(line, shown.layout.spread(10, 3))
            if net is not None and net.alive():
                net.wait_for("question_at", asked, CHAIN_WAIT)
            game.sleep(CHAIN_SETTLE)
            self._opened_at[member.name] = asked  # la question suivante comptée depuis ce clic
            shown = game.look(park=False)
        self._close_bubble(member)
        self._failed(member, f"toujours pas la clé après {CHAIN_MAX} « {spec.chain} »")
        return None

    def _reply(self, member: Member, frame: Frame, shown: Frame, answer: str, not_gone: str) -> bool:
        """Dans la bulle ouverte, la ligne `answer`, puis le décor change (`frame` : la salle avant le dialogue)."""
        game = member.game
        reply = self._read_reply(game, shown, answer)
        if reply is None:
            return self._failed(member, f"pas de réponse « {answer} » dans la bulle (clef absente ?)")
        log.info("Donjon : %s répond « %s »", member.name, answer)
        game.click(reply, shown.layout.spread(10, 3))
        if not self._changed(member, frame):  # la salle d'avant le dialogue (la bulle fermée ne compte pas)
            return self._failed(member, f"réponse cliquée, mais il {not_gone}")
        return True

    def _talk_entry(self, game, frame: Frame, point) -> tuple[float, float] | None:
        """« Parler » dans le menu du PNJ (son nom, puis « Parler ») : la seule entrée beige du petit menu près du clic
        (30/10 : « Parler » lu ailleurs sur la carte par l'OCR, Fourboun pas sorti du donjon Bouftou) ; sinon lu par
        l'OCR sur la carte (un cadre serré autour du clic ne le lisait pas, 18/10), relu le temps que le menu finisse
        de s'afficher ; à défaut, le cadre beige d'un menu."""
        small = find_small_menu(frame, around=point)
        if small is not None:
            x0, y0, x1, y1 = small
            return (x0 + x1) / 2, (y0 + y1) / 2
        for attempt in range(TALK_READS):
            talk = find_text(frame, frame.layout.map_box, "Parler")
            if talk is not None:
                return talk
            game.sleep(0.3)
            frame = game.look(park=False)
        # 31/10, revue : plus de clic au milieu d'un menu dont « Parler » n'est pas lu (celui d'un autre joueur, par
        # exemple) : None, l'appelant le referme sans y toucher.
        return None

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

    def _hidden_click(self, member: Member, point, spread, button: str = "left") -> None:
        """Clic sur la carte, les autres joueurs cachés (touche E tenue, comme pour les soleils des cartes ; rappel de
        l'utilisateur, 19/10) : devant la grotte, des marchands debout près du soleil, Pesopepe resté dehors."""
        if not self.hide_key:
            member.game.click(point, spread, button=button)
            return
        with member.game.holding(self.hide_key):
            member.game.click(point, spread, button=button)

    def _silent(self, member: Member) -> bool:
        """Aucun déplacement ni changement de carte annoncé par le serveur depuis le dernier clic de ce personnage,
        NET_SILENT s après (False sans écoute : pas de conclusion)."""
        net = getattr(member.game, "net", None)
        if net is None or not net.alive() or net.my_id is None:
            return False
        clicked = self._opened_at.get(member.name, 0.0)
        moved = max(getattr(net, "moved_at", 0.0), getattr(net, "map_at", 0.0))
        return time.monotonic() - clicked >= NET_SILENT and moved < clicked

    def _changed(self, member: Member, before: Frame) -> bool:
        """Ce personnage est passé dans une autre salle dans les CHANGE_WAIT secondes : nouvelle carte annoncée par le
        serveur depuis l'ouverture de sa bulle (31/10, revue : le réseau d'abord) ; sans écoute, le décor a changé."""
        net = getattr(member.game, "net", None)
        if net is not None and net.alive() and net.my_id is not None:
            since = self._opened_at.get(member.name, 0.0)
            if net.wait_new_map(since, CHANGE_WAIT) is not None:
                member.game.sleep(SETTLE)
                return True
            return False
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


class BankTrip:
    """Aller-retour de toute l'équipe entre le PNJ du donjon et la banque d'Astrub (team/pods.py team_trip, zaaps
    compris) : pour le mode de clés « sac + banque » (team/keys.py, trip.go / trip.back ; 31/10 : il n'existait pas,
    le mode faisait comme « sac »)."""

    def __init__(self, bot, spec: DungeonSpec) -> None:
        self.bot, self.spec = bot, spec

    def go(self) -> bool:
        from bot.team import pods
        from bot.team.bank import BANK_MAP

        return pods.team_trip(self.bot, BANK_MAP, "Clés au coffre")

    def back(self) -> bool:
        from bot.team import pods

        return pods.team_trip(self.bot, self.spec.outside, "Retour au donjon")


def room_maps(dungeon: str, path: Path = ROOMS_FILE) -> set[int]:
    """Cartes des salles du donjon au nom exact `dungeon` (data/donjons.json), salle de fin comprise ; vide s'il n'y
    est pas."""
    import json

    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {room["carte"] for row in rows if row.get("nom") == dungeon for room in row.get("salles") or []
            if room.get("carte") is not None}


def room_places(dungeon: str, map_id: int | None, path: Path = ROOMS_FILE) -> int | None:
    """Places des joueurs dans la salle `map_id` du donjon au nom exact `dungeon` (data/donjons.json, placement p1) ;
    None si la salle ou le donjon n'y sont pas."""
    import json

    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for row in rows:
        if row.get("nom") == dungeon:
            for room in row.get("salles") or []:
                if room.get("carte") == map_id and room.get("placement"):
                    return int(room["placement"][0])
    return None


def plan_waves(levels: dict[str, int], places: int) -> list[list[str]]:
    """Vagues pour une salle de `places` places : le moins de vagues possible, de tailles aussi égales que possible ;
    les plus hauts niveaux dans les plus petites (05/11, utilisateur : « les plus haut level sont plus forts donc
    peuvent être moins par salle »). Noms du plus haut niveau au plus bas dans chaque vague."""
    names = sorted(levels, key=lambda n: (-levels[n], n))
    count = -(-len(names) // max(1, places))
    small, extra = divmod(len(names), count)
    sizes = [small] * (count - extra) + [small + 1] * extra  # les petites d'abord : les plus forts
    waves, start = [], 0
    for size in sizes:
        waves.append(names[start:start + size])
        start += size
    return waves


def pods_threshold() -> float:
    from bot.team.loop import POD_FULL  # import local : loop importe ce module

    return POD_FULL


class DungeonFarm:
    """Le donjon en boucle : entrée, combats de salle en salle (la boucle d'équipe, TeamBot.step), sortie."""

    def __init__(self, bot, spec: DungeonSpec, runs: int) -> None:
        self.bot = bot
        self.spec = spec
        self.runs = runs
        travel = getattr(bot, "travel_settings", None)
        self.entry = DungeonEntry(bot.members, spec, getattr(travel, "hide_players_key", "e"))
        # Trousseau de clefs (donjon.trousseau) : priorite (07/11, utilisateur : en priorité si disponible, la clé
        # gardée pour plus tard), oui (seulement pour qui n'a pas la clé, 31/10), non (jamais).
        donjon = (getattr(bot, "_settings", None) or {}).get("donjon") or {}
        mode = str(donjon.get("trousseau", "priorite")).strip().lower()
        self.entry.keyring = mode in ("priorite", "priorité", "oui", "true", "1", "yes")
        self.entry.keyring_first = mode in ("priorite", "priorité")
        # Reprise dans une salle (05/11) : donjon.reprise=meme_salle certifie l'équipe lancée réunie dans une même salle,
        # cartes pas encore annoncées par le serveur (pas de changement de carte depuis le lancement) ; sans, pas
        # d'attaque sans les cartes de toute l'équipe (_all_here).
        self.trust_together = str(donjon.get("reprise", "")).strip().lower() == "meme_salle"
        # Clés qui manquent pour les passages demandés, coffre compris (06/11, utilisateur : « soit farm soit
        # fabrication ») : farm de la zone de la clé (zone_cles) pour son butin ; fabrication pas encore faite (il faut
        # désigner qui a le métier : Fourboun, Bricoleur 35).
        missing = str(donjon.get("cles_manquantes", "non")).strip().lower()
        if missing not in MISSING_MODES:
            log.warning("donjon.cles_manquantes : « %s » inconnu (%s) : rien", missing, ", ".join(MISSING_MODES))
            missing = "non"
        self.missing_keys = missing
        self.farm_seconds = 60 * float(donjon.get("farm_cles_minutes", FARM_MINUTES))
        # Répartition des clés au début du farm (team/keys.py, donjon.cles ; 30/10, demande de l'utilisateur).
        self.keys = KeyManager(bot.members, getattr(bot, "_settings", None) or {},
                               getattr(travel, "hide_players_key", "e"), trip=BankTrip(bot, spec))
        self.keys.refill = self.missing_keys == "farm"  # pas assez de clés : pas de message « farm arrêté »

    def run(self, start: str | None = None, only: set[str] | None = None, room: int = 1) -> int:
        """Nombre de passages complets (entrée, salles, sortie). `start` : étape de l'entrée où reprendre (STEPS), ou
        « salles » pour l'équipe déjà passée, aux combats de la salle `room` ; sans : chacun situé (DungeonEntry.enter).
        Avec donjon.cles : clés réparties d'abord, et passages ramenés à ceux que les clés permettent (self.runs)."""
        if self.keys.enabled:
            if start == "salles":
                log.info("Clés : l'équipe déjà dans les salles, pas de répartition des clés")
            else:
                passes = self.keys.prepare(self.spec.name, self.runs)
                if passes is None:
                    # 31/10, utilisateur : « si tout le groupe n'a pas de clé il ne faut pas continuer et faire
                    # rentrer solo » (ce matin-là, clé pas reconnue : Fourboun entré seul).
                    log.warning("Donjon : clés pas réparties (clé ou inventaire inconnus) : pas d'entrée")
                    return 0
                if passes < self.runs and self._can_refill():
                    log.info("Donjon : clés pour %d passage(s) sur %d demandés : farm de leur butin quand elles "
                             "manqueront", passes, self.runs)
                elif passes < self.runs:
                    log.warning("Donjon : clés pour %d passage(s) seulement sur %d demandés : j'en fais %d", passes,
                                self.runs, passes)
                    self.runs = passes
                self._keys_left = passes
                self._progress("clés réparties")
        done = 0
        bot = self.bot
        deadline = getattr(bot, "deadline", None)
        estimate = PASS_ESTIMATE
        while done < self.runs:
            run = done + 1
            if self.keys.enabled and start != "salles" and self._keys_left <= 0:
                self._keys_left = self._refill(deadline)
                if self._keys_left <= 0:
                    break
                self._progress("clés refaites")
            resuming = run == 1 and start == "salles"  # reprise dans les salles : l'équipe y est, à finir
            if deadline is not None and not resuming and time.monotonic() + estimate > deadline:
                # 06/11 : --minutes n'arrête plus l'équipe au milieu d'un passage (dans les salles, clé donnée) ; pas de
                # nouveau passage sans le temps de le finir (durée du précédent, PASS_ESTIMATE pour le premier).
                log.info("Donjon : plus le temps pour un passage de plus (%.0f min estimées, %.0f min restantes)",
                         estimate / 60, max(0.0, deadline - time.monotonic()) / 60)
                break
            log.info("Donjon : passage %d/%d", run, self.runs)
            started = time.monotonic()
            if deadline is not None:
                bot.deadline = None  # rendue à la fin du passage (finally)
            try:
                if not self._one_pass(run, start, only, room):
                    break
            finally:
                if deadline is not None:
                    bot.deadline = deadline
            done += 1
            self._keys_left -= 1
            estimate = time.monotonic() - started
            log.info("Donjon : passage %d/%d terminé en %.0f min", run, self.runs, estimate / 60)
        log.info("Donjon : %d passage(s) complet(s) sur %d", done, self.runs)
        return done

    _keys_left = NO_KEY_LIMIT  # passages que les clés réparties permettent encore (donjon.cles ; sans : pas de limite)

    def _can_refill(self) -> bool:
        """Clés manquantes refaites pendant le farm : farm de la zone de la clé (donjon.cles_manquantes=farm)."""
        if self.missing_keys == "fabrication":
            log.warning("Donjon : fabrication des clés pas encore faite (il faut désigner qui a le métier) : je fais "
                        "avec les clés qu'il y a")
            return False
        if self.missing_keys == "farm" and self.spec.key_zone is None:
            log.warning("Donjon : %s : zone de la clé inconnue (zone_cles, config/donjons_entree.yaml) : pas de farm "
                        "des clés", self.spec.name)
            return False
        return self.missing_keys == "farm"

    def _refill(self, deadline: float | None) -> int:
        """Plus de clé pour un passage : farm de la zone de la clé jusqu'à une clé par personnage (butin, d'après le
        serveur), puis répartition. Passages possibles ensuite ; 0 : pas de farm, ou pas de clé de plus."""
        from bot.team import pods

        if not self._can_refill():
            return 0
        seconds = self.farm_seconds
        if deadline is not None:
            seconds = min(seconds, deadline - time.monotonic() - PASS_ESTIMATE)  # le temps du passage d'après compris
        if seconds < FARM_LEAST:
            log.info("Donjon : plus de clé, et plus le temps pour farmer leur butin puis faire un passage")
            return 0
        template = key_of(self.spec.name)
        size = len(self.bot.members)
        if template is None or size == 0:
            return 0

        def enough() -> bool:
            held = self.keys.count(template)
            return held is not None and sum(held.values()) >= size

        log.info("Donjon : plus de clé pour un passage : farm du butin de la zone de %s,%s", *self.spec.key_zone)
        pods.team_zone_farm(self.bot, self.spec.key_zone, enough, seconds, "Clés de " + self.spec.name)
        if getattr(self.bot, "finished", False):
            return 0  # session arrêtée pendant le farm
        held = self.keys.count(template)
        possible = sum(held.values()) // size if held else 0
        if possible <= 0:
            log.warning("Donjon : clés après le farm : %s : pas assez pour un passage",
                        summary(held) if held else "inventaire inconnu")
            return 0
        # Seulement ce que le butin permet : pas de nouvel aller-retour au coffre, vidé au début du farm (sac_banque).
        return self.keys.prepare(self.spec.name, possible, template) or 0

    def _one_pass(self, run: int, start: str | None, only: set[str] | None, room: int) -> bool:
        """Un passage : entrée (ou reprise), salles, sortie. False dès qu'une étape échoue."""
        self._close_windows()
        if run == 1 and start == "salles":
            log.info("Donjon : l'équipe déjà dans les salles, je reprends aux combats")
        elif not self._pods_ready():  # équipe dehors : pods vus avant d'entrer (team/pods.py)
            return False
        elif (run > 1 or start is None) and not self._back_to_entrance():
            return False
        elif not (self.entry.enter(start, only) if run == 1 else self.entry.enter()):
            return False
        else:
            self._progress("équipe entrée dans le donjon")
        if not self._fight_rooms(room if run == 1 and start == "salles" else 1):
            return False
        self._close_windows()
        if not self.entry.leave():
            return False
        self._progress("équipe sortie du donjon")
        return True

    def _progress(self, what: str) -> None:
        """Progrès noté pour la surveillance de l'équipe (TeamBot._progress) : 06/11 (journal 200051), 5 min
        d'échanges de clés sans progrès noté, pause « BLOQUÉ » de 2 min juste après l'entrée."""
        note = getattr(self.bot, "_progress", None)
        if callable(note):
            note(what)

    def _back_to_entrance(self) -> bool:
        """Toute l'équipe sûrement dehors, loin du PNJ de l'entrée (DungeonEntry.away) : ramenée devant lui par le trajet
        d'équipe. 05/11 (journal 212413) : la sortie des Champs par une case dépose l'équipe en 7,-26, pas devant Mawy
        en 7,-25 ; « ni devant l'entrée ni dans les premières salles », passage suivant pas fait. De même au lancement,
        l'équipe laissée là. Sinon (devant le PNJ, dans les salles, ou pas sûr) : rien, l'entrée situe chacun."""
        members = self.bot.members
        if not members or not all(self.entry.away(m) for m in members):
            return True
        from bot.team import pods

        log.info("Donjon : toute l'équipe dehors, loin de l'entrée : je la ramène en %s,%s", *self.spec.outside)
        return pods.team_trip(self.bot, self.spec.outside, "Retour à l'entrée du donjon")

    def _pods_ready(self) -> bool:
        """Équipe dehors, avant un passage (31/10, utilisateur) : surcharge rééquilibrée entre nos personnages, puis
        banque si quelqu'un est au seuil (team.pods_banque), retour devant le PNJ et clés réparties de nouveau. False :
        personne n'entre (surcharge pas résolue, banque ou retour ratés)."""
        from bot.team import pods
        from bot.team.bank import BANK_MAP
        from bot.team.loop import bank_visit  # import local : loop importe ce module

        bot = self.bot
        stuck = pods.rebalance(bot.members, self.keys.portraits)
        if stuck:
            log.warning("Donjon : %s toujours au-dessus du maximum de pods (ne peut pas marcher) : pas de passage",
                        ", ".join(stuck))
            return False
        top = pods.fullest(bot.members)
        threshold = getattr(bot, "pods_full", pods_threshold())
        if top is None or top[1] < threshold:
            return True
        log.warning("Donjon : pods de %s à %.0f %% (seuil %.0f %%) : banque avant le prochain passage", top[0],
                    100 * top[1], 100 * threshold)
        if not pods.team_trip(bot, BANK_MAP, "Banque"):
            return False
        hide = getattr(getattr(bot, "travel_settings", None), "hide_players_key", "e")
        done = bank_visit(bot.members, hide, getattr(bot, "bank_mode", "chacun"), getattr(bot, "keep_key", None)).run()
        failed = [m.name for m in bot.members if m.name not in done]
        if failed:
            log.warning("Banque : coffre pas vidé pour %s", ", ".join(failed))
        bot._pods, bot._bank_floor, bot._banked = {}, {}, True
        if not pods.team_trip(bot, self.spec.outside, "Retour au donjon"):
            return False
        if self.keys.enabled:  # clés passées au coffre avec le reste (sauf donjon.garder_cle) : réparties de nouveau
            self.keys.prepare(self.spec.name, 1)
        return True

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

    def _left_behind(self, wait: float = SAME_ROOM_WAIT) -> list[str]:
        """Après la victoire d'une salle, ceux qui ne sont pas sur la carte du principal d'après le serveur (téléportés
        avec lui dans la salle suivante) ; attendu au plus `wait` s (les GDM n'arrivent pas tous au même instant).
        Cartes inconnues (sans écoute) : personne."""
        end = time.monotonic() + wait
        while True:
            leader = getattr(self.bot.leader.game, "net", None)
            here = leader.map_id if leader is not None else None
            if here is None:
                return []
            behind = [m.name for m in self.bot.members if (net := getattr(m.game, "net", None)) is not None
                      and net.map_id is not None and net.map_id != here]
            if not behind or time.monotonic() >= end:
                return behind
            time.sleep(0.3)

    def _small_room(self, waved: set[int]) -> int | None:
        """Places de la salle où est le principal si elles manquent pour toute l'équipe (salle pas encore faite en
        vagues), hors combat, toute l'équipe sur cette carte d'après le serveur ; None sinon (combat normal)."""
        bot = self.bot
        net = getattr(bot.leader.game, "net", None)
        room = net.map_id if net is not None and net.alive() else None
        if room is None or room in waved or bot._round_due:
            return None
        places = room_places(self.spec.name, room)
        if places is None or places >= len(bot.members):
            return None
        if read_scene(bot.leader.game.look(park=False)).phase is not Phase.OUT_OF_COMBAT:
            return None
        if any(getattr(getattr(m.game, "net", None), "map_id", room) != room for m in bot.members):
            return None  # pas encore toute l'équipe ici (téléportations pas toutes annoncées)
        waved.add(room)
        return places

    def _split_team(self) -> tuple[int, list] | None:
        """Équipe sur deux salles du donjon d'après le serveur, hors combat : (salle d'avant, ceux qui y sont) ; None si
        toute l'équipe est sur la carte du principal, ou si ce n'est pas ce cas-là (carte inconnue, autre carte que
        les salles du donjon)."""
        bot = self.bot
        if bot._round_due or read_scene(bot.leader.game.look(park=False)).phase is not Phase.OUT_OF_COMBAT:
            return None
        maps = {m.name: getattr(getattr(m.game, "net", None), "map_id", None) for m in bot.members}
        rooms = self._room_order()
        here = maps.get(bot.leader.name)
        if None in maps.values() and here in rooms and rooms.index(here) > 0:
            # 05/11 : relancé au milieu d'une salle en vagues, ceux restés derrière n'ont pas changé de carte depuis :
            # pas de carte annoncée. Salle d'avant celle du principal trop petite pour l'équipe : ils y sont restés.
            before = rooms[rooms.index(here) - 1]
            places = room_places(self.spec.name, before)
            if places is not None and places < len(bot.members):
                unknown = [n for n, m in maps.items() if m is None]
                log.info("Donjon : carte de %s pas annoncée ; salle d'avant à %d places : restés en salle %d",
                         ", ".join(unknown), places, before)
                maps = {n: (before if m is None else m) for n, m in maps.items()}
        if None in maps.values() or len(set(maps.values())) < 2:
            return None
        known = [r for r in set(maps.values()) if r in rooms]
        if len(known) != len(set(maps.values())):
            return None
        first = min(known, key=rooms.index)
        return first, [m for m in bot.members if maps[m.name] == first]

    def _all_here(self) -> bool:
        """Avant une attaque dans une salle (hors combat, fenêtres fermées) : toute l'équipe annoncée par le serveur sur
        la carte du principal. True pendant un combat ou le tour des fenêtres (rien à vérifier), ou sans écoute."""
        bot = self.bot
        if bot._round_due or read_scene(bot.leader.game.look(park=False)).phase is not Phase.OUT_OF_COMBAT:
            return True
        nets = [getattr(m.game, "net", None) for m in bot.members]
        if any(net is None for net in nets):
            return True  # sans écoute : comme avant (décor, épée)
        maps = {getattr(net, "map_id", None) for net in nets}
        if maps == {None} and getattr(self, "trust_together", False):
            return True  # réunie d'après celui qui a relancé (donjon.reprise=meme_salle)
        return None not in maps and len(maps) == 1

    def _room_order(self) -> list[int]:
        """Cartes des salles du donjon dans l'ordre (data/donjons.json) ; vide sans la référence."""
        import json

        try:
            rows = json.loads(ROOMS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        row = next((r for r in rows if r.get("nom") == self.spec.name), None)
        return [room.get("carte") for room in (row or {}).get("salles") or []]

    def _catch_up(self, members: list, places: int) -> int | None:
        """Ceux restés dans une salle la font, en vagues de `places` au plus ; nombre de combats, None si une vague
        n'a pas pu combattre."""
        names = {m.name for m in members}
        levels = {name: level for name, level in self._levels().items() if name in names}
        waves = plan_waves(levels, places)
        log.info("Donjon : %s restés dans la salle d'avant : %d vague(s) (%s)", ", ".join(sorted(names)), len(waves),
                 " / ".join(", ".join(w) for w in waves))
        by_name = {m.name: m for m in self.bot.members}
        for index, wave in enumerate(waves, 1):
            if not self._wave_fight([by_name[n] for n in wave], index, len(waves)):
                tell_user(f"{self.spec.name} : {', '.join(wave)} n'ont pas pu combattre dans la salle d'avant : je "
                          "m'arrête.")
                return None
        return len(waves)

    def _levels(self) -> dict[str, int]:
        """Niveau de chacun d'après le serveur (PM+ du groupe, 01/11) ; inconnu : 0 (dans la dernière vague)."""
        known: dict[str, int] = {}
        for member in self.bot.members:
            known.update(getattr(getattr(member.game, "net", None), "group_levels", None) or {})
        return {m.name: int(known.get(m.name, 0)) for m in self.bot.members}

    def _fight_in_waves(self, places: int) -> int | None:
        """Salle à `places` places, moins que l'équipe : une vague après l'autre (plan_waves), chacune lance le combat
        par son plus haut niveau et seuls ses membres le rejoignent ; la victoire les téléporte dans la salle
        suivante, le groupe réapparaît pour la vague d'après. Nombre de combats faits ; None si une vague n'a pas pu
        combattre (la suite de l'équipe reste dans cette salle : dit à l'utilisateur)."""
        bot = self.bot
        waves = plan_waves(self._levels(), places)
        log.info("Donjon : salle à %d places pour %d personnages : %d vagues, les plus hauts niveaux en plus petit "
                 "nombre (%s)", places, len(bot.members), len(waves), " / ".join(", ".join(w) for w in waves))
        by_name = {m.name: m for m in bot.members}
        for index, wave in enumerate(waves, 1):
            if not self._wave_fight([by_name[n] for n in wave], index, len(waves)):
                tell_user(f"{self.spec.name} : la vague {index} ({', '.join(wave)}) n'a pas pu combattre : je m'arrête, "
                          "une partie de l'équipe est déjà dans la salle suivante.")
                return None
        log.info("Donjon : salle faite en %d vagues", len(waves))
        return len(waves)

    def _wave_fight(self, wave: list, index: int, count: int) -> bool:
        """Une vague : son plus haut niveau attaque le groupe de la salle (attendu RESPAWN_WAIT s au plus, le temps qu'il
        réapparaisse après la vague d'avant), les autres de la vague le rejoignent, personne d'autre."""
        from bot.brain.engage import Engager

        bot = self.bot
        host = wave[0]
        engager = Engager(host.game, bot.engager.settings)
        for name in ("probe_key", "probe_first", "miss_forget"):  # pas walk_area : celle du principal
            if hasattr(bot.engager, name):
                setattr(engager, name, getattr(bot.engager, name))
        log.info("Donjon : vague %d/%d : %s lance le combat, %s le rejoignent", index, count, host.name,
                 ", ".join(m.name for m in wave[1:]) or "personne")
        bot.fighting_only = {m.name for m in wave}
        try:
            deadline = time.monotonic() + RESPAWN_WAIT
            while time.monotonic() < deadline:
                if bot._round_due:
                    bot.step()  # fenêtres de résultat de la vague d'avant
                    continue
                if engager.engage():
                    begun = host.game.wait_until(lambda f: read_scene(f).phase is not Phase.OUT_OF_COMBAT, 6.0,
                                                 park=False)
                    if begun is None:
                        continue  # pas de combat finalement : nouvelle recherche
                    bot._fight(host)
                    while bot._round_due:
                        bot.step()
                    return True
                host.game.sleep(WAVE_RETRY)  # groupe pas (encore) réapparu
            log.warning("Donjon : vague %d/%d : pas de groupe à attaquer en %.0f s", index, count, RESPAWN_WAIT)
            return False
        finally:
            bot.fighting_only = None

    def _fight_rooms(self, first: int = 1) -> bool:
        """Les combats de chaque salle, de la salle `first` à la dernière (spec.rooms) ; le PNJ de fin n'est cherché
        qu'après (utilisateur, 18/10 : retenir le nombre de salles). Salle 2, ce jour-là : un Petit Tournesol au bord,
        deux clics à côté, salle crue finie et PNJ de fin cherché devant lui. Nombre de salles inconnu : jusqu'à la salle
        sans groupe (EMPTY_ROOM recherches vides, aucun groupe vu, pas seulement des groupes ratés)."""
        bot = self.bot
        bot._empty_searches = 0
        if bot.engager is not None:
            bot.engager.new_map()
            # 31/10 : donjon partagé, le groupe de la salle pris par d'autres joueurs revient vite : ses ratés oubliés
            # tôt (Bouftou : 15 min bloqués en salle 5 quand ils ne l'étaient jamais).
            from bot.brain.engage import DUNGEON_MISS_FORGET

            bot.engager.miss_forget = DUNGEON_MISS_FORGET
        rooms = self.spec.rooms
        fights, won = bot.fights, 0
        step = getattr(bot, "safe_step", bot.step)  # erreurs rattrapées comme dans TeamBot.run (21/10)
        waved: set[int] = set()  # salles déjà faites en vagues (une fois chacune)
        # Équipe sur deux salles à la reprise (05/11) : réunie par _split_team / _catch_up, pas par la vérification de
        # la boucle d'équipe (« pas sur la carte du principal ... je m'arrête »).
        bot._together_checked = True
        while not bot.finished:
            places = self._small_room(waved)
            if places is not None:
                done = self._fight_in_waves(places)
                if done is None:
                    return False
                fights += done - 1  # une seule salle, quel que soit le nombre de vagues
                continue
            behind = self._split_team()
            if behind is not None:
                # Reprise au milieu d'une salle en vagues (05/11 : bot arrêté par F12 pendant la vague 1 de la salle
                # 5, relancé) : ceux restés dans la salle d'avant la font, en vagues si elle manque de places, puis
                # rejoignent les autres ; cette salle-là ne compte pas une seconde fois.
                room, members = behind
                places = room_places(self.spec.name, room) or len(members)
                if self._catch_up(members, places) is None:
                    return False
                fights = bot.fights - won
                continue
            if not self._all_here():
                # 05/11 (journal 210407) : relancé dans les salles, aucune carte annoncée ; le principal a attaqué, seul
                # Waldarch l'a rejoint (les autres sans la carte ni l'épée), puis le boss à deux. Plus d'attaque tant
                # que le serveur n'a pas dit toute l'équipe dans la salle du principal.
                tell_user(f"{self.spec.name} : je ne sais pas si toute l'équipe est dans la salle du principal (cartes "
                          "pas annoncées par le serveur) : je m'arrête plutôt que d'attaquer sans elle.")
                log.warning("Donjon : cartes de l'équipe pas annoncées : pas d'attaque, arrêt")
                return False
            step()
            if bot.fights - fights > won:
                won = bot.fights - fights
                if rooms:
                    log.info("Donjon : salle %d/%d finie", first + won - 1, rooms)
                behind = self._left_behind()
                if behind:
                    # 31/10 : Walitcoin, combat de la salle 8 pas rejoint, resté seul dans sa salle ; les salles 9 et
                    # 10 faites à 7, et lui coincé dans le donjon. Plus de salle tant que l'équipe n'est pas réunie.
                    tell_user(f"{self.spec.name} : {', '.join(behind)} resté(s) dans une autre salle (combat pas "
                              f"rejoint) : je m'arrête, l'équipe est encore dans le donjon.")
                    return False
            if bot._round_due or read_scene(bot.leader.game.look(park=False)).phase is not Phase.OUT_OF_COMBAT:
                continue
            if rooms and first + won > rooms:
                log.info("Donjon : salle de fin (les %d salles finies)", rooms)
                return True
            if not rooms and bot._empty_searches >= EMPTY_ROOM and getattr(bot.engager, "groups_seen", 0) == 0 \
                    and getattr(bot.engager, "keyboard_ok", True) is True:
                # Jamais sur un clavier sourd (20/10 : Z ignoré par la fenêtre de Koalaloupour) : la dernière recherche
                # vide doit avoir été vérifiée par la touche A (brain/engage.py, _z_unheard : sorties vues). Pas encore
                # vérifiée (None) : la recherche suivante le sera ; sourde (False) : la surveillance du progrès s'en charge.
                log.info("Donjon : salle de fin (plus de groupe), %d combat(s) dans ce passage", won)
                return True
        log.warning("Donjon : arrêt pendant les combats (%s)", bot._stop_reason or "session terminée")
        return False
