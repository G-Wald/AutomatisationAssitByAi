"""Écoute passive du trafic du jeu (Npcap) : ce que le serveur annonce à chaque fenêtre, sans rien lire à l'écran.

Demande de l'utilisateur (26/10) : la position du personnage, mal lue à l'écran dans beaucoup de tests, récupérée du
client plutôt que de l'image. Le client Dofus Retro parle au serveur en texte clair (port 443, sans TLS), messages
séparés par un octet nul ; vérifié le 26/10 sur une capture de 13 min (combat, changement de carte). Écoute seulement :
rien n'est envoyé ni modifié, le jeu et le serveur ne voient aucune différence.

Messages lus (serveur -> client), d'après les captures du 26/10 :
- « GDM|3668|date|clé » : nouvelle carte (numéro du client ; data/references donne ses coordonnées, champ « ids ») ;
- « GDK » : carte chargée ;
- « GM|+cell;dir;0;id;Nom;... » : acteurs de la carte (personnages, groupes de monstres à id négatif), avec leur case ;
  « GM|-id » : acteur parti ;
- « GA0;1;id;chemin » : déplacement (chemin : groupes de 3 caractères, direction + case ; la dernière est l'arrivée) ;
- « GA;2;id; » : changement de carte en cours.
Combat (31/10) : « GJK » entré dans un combat, « GR1<id> » / « GR0<id> » Prêt (ou plus) d'un combattant, « GS » fin du
placement, « GTS<id>|... » / « GTF<id> » début et fin d'un tour, « GA;903;<id>;<code> » refus de rejoindre un combat.

Numéros de case du client -> cases de la grille du bot : brain/arena.map_cell (vérifié : 37 -> (16, 2), case
d'arrivée de la référence ; 457 -> (15, 31), le soleil cliqué ; 358 -> (20, 24), lu à l'écran au même moment).

Chaque connexion au serveur est tenue par un processus enfant de celui de la fenêtre du personnage (26/10 : parent =
processus de la fenêtre, pour les 8) : port local -> PID -> parent -> fenêtre -> personnage.

Npcap absent (wpcap.dll introuvable) : `start_listener` rend None et le bot lit l'écran comme avant. Interface en
erreur ou pas encore là : écran en attendant que la veille la rouvre (RESCAN_EVERY). Installation : npcap.com, mode compatible WinPcap, accès non réservé aux administrateurs (sinon il faudrait
lancer le bot en administrateur).
"""

import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import socket
import struct
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from bot.brain.arena import map_cell
from bot.brain.inventory import Exchange, Inventory, Item

log = logging.getLogger(__name__)

NPCAP_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Npcap")
GAME_PORT = 443
HASH = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"  # chiffres des cases dans les chemins
READ_TIMEOUT_MS = 200  # pcap_next_ex rend la main au moins toutes les 0,2 s (arrêt du fil)
# Connexions du jeu relues au plus toutes les REFRESH_EVERY s quand arrive un port inconnu ; un port inconnu (pas une
# fenêtre suivie) n'est retenu comme tel que UNKNOWN_FOR s. 30/10 : 5 s et retenu pour de bon, les premiers messages de
# la connexion ouverte après le choix du personnage (le sac, ASK) étaient perdus pour 6 personnages sur 8.
REFRESH_EVERY = 0.3
UNKNOWN_FOR = 1.0
# « As » : une caractéristique par champ, « base,équipement,dons,boost[,...] ». Base + équipement + dons = colonne Total
# des caractéristiques avancées hors combat (27/10 : identiques pour les 8 personnages) ; le boost (sorts, en combat)
# est laissé de côté.
STAT_FIELDS = {"pa": 9, "pm": 10, "force": 11, "vitalite": 12, "sagesse": 13, "chance": 14, "agilite": 15,
               "intelligence": 16, "portee": 17, "invocations": 18, "dommages": 19, "dommages_physiques": 20,
               "dommages_pourcent": 22}
RECENT_KEPT = 60  # messages gardés par fenêtre (ClientFeed.heard_since)
RESCAN_EVERY = 30.0  # s : interfaces relues (carte réseau changée, VPN, erreur Npcap après une veille) -> écoute rouverte


def coded_cells(text: str) -> list[int]:
    """Numéros de case codés sur 2 caractères en base 64 (HASH), à la suite (« GP » : cases de placement)."""
    cells = []
    for i in range(0, len(text) - 1, 2):
        hi, lo = HASH.find(text[i]), HASH.find(text[i + 1])
        if hi < 0 or lo < 0:
            break
        cells.append(hi * 64 + lo)
    return cells


def path_cells(path: str) -> list[int]:
    """Numéros de case d'un chemin « GA0;1 » (« agNbhfahj » -> [423, 453, 457]) : 3 caractères par étape, une
    direction puis la case en base 64 (HASH). La première est le départ, la dernière l'arrivée."""
    cells = []
    for i in range(0, len(path) - 2, 3):
        hi, lo = HASH.find(path[i + 1]), HASH.find(path[i + 2])
        if hi < 0 or lo < 0:
            break
        cells.append(hi * 64 + lo)
    return cells


@dataclass
class Actor:
    """Acteur de la carte (GM|+) : personnage, PNJ, groupe de monstres (id négatif) ou combattant."""

    id: int
    cell: int
    name: str = ""
    levels: tuple[int, ...] = ()  # groupe de monstres hors combat : niveau de chacun
    team: int | None = None  # en combat : 0 ou 1 (GM|+ du combat, avant-dernier champ)


@dataclass
class FighterState:
    """Combattant d'après le dernier « GTM » (début de chaque tour) et les actions qui ont suivi."""

    hp: int = 0
    max_hp: int = 0
    pa: int = 0
    pm: int = 0
    dead: bool = False
    # Invisible (31/10, Prespic, sort Camouflage) : « GA;150;lanceur;cible,tours,? » (tours 0 : visible de nouveau),
    # et case -1 dans GTM tant qu'il l'est ; ses déplacements ne sont pas annoncés. Sa dernière case connue reste
    # celle de son acteur, mais il n'est plus une cible.
    hidden: bool = False
    summoned: bool = False  # invocation (« GA;181 ») : seule cible de Désinvocation (01/11, l'utilisateur)


@dataclass
class Fight:
    """Combat en cours (de « GJK » à « GE »)."""

    started_at: float
    my_team: int | None = None  # GP...|équipe : la mienne
    order: list[int] = field(default_factory=list)  # GTL : ordre de jeu
    turn: int | None = None  # GTS : id de celui qui joue
    turn_at: float = 0.0
    turn_over: bool = False  # GTF de `turn` reçu
    ready: dict[int, bool] = field(default_factory=dict)  # GR : Prêt de chaque combattant (placement)
    begun_at: float = 0.0  # GS : fin du placement, premier tour à venir
    fighters: dict[int, FighterState] = field(default_factory=dict)
    ended_at: float = 0.0  # GE
    # GP : cases de placement de l'équipe 0 et de l'équipe 1 (cases de la grille). Couleur de chacune pas encore sûre :
    # l'équipe 0 en rouge est une supposition, contrôlée à l'écran à chaque combat (team/loop.py, 31/10).
    places: tuple[frozenset, frozenset] | None = None


@dataclass
class ClientFeed:
    """Ce que le serveur a annoncé à une fenêtre. Écrit par le fil d'écoute, lu par le bot (verrou)."""

    name: str  # personnage de la fenêtre (titre)
    map_id: int | None = None
    map_at: float = 0.0  # time.monotonic() du dernier GDM
    loaded_at: float = 0.0  # du dernier GDK (carte chargée)
    my_id: int | None = None
    actors: dict[int, Actor] = field(default_factory=dict)
    moved_at: float = 0.0  # dernier déplacement annoncé de mon personnage (GA0;1)
    move_cells: list[int] = field(default_factory=list)
    spent_at: float = 0.0  # dernière action de mon personnage qui a coûté des PA (GA;102 : sort lancé, raté ou arme)
    cast_at: float = 0.0  # dernier sort lancé par mon personnage (GA;300)
    cast_spell: int | None = None  # son numéro (celui de Solomonk)
    failed_at: float = 0.0  # dernier échec critique de mon personnage (GA;302 : sort raté, PA perdus)
    # Banque (team/bank.py) : dialogue d'un PNJ ouvert (DCK), sa question et ses réponses (DQ), échange ou coffre ouvert
    # (ECK), fermé (EV), pods changés (Ow).
    dialog_at: float = 0.0
    question_at: float = 0.0
    # Dernière question d'un PNJ : « DQ<question>|<réponse>;<réponse> » -> (question, réponses proposées). 31/10 : Altaïr
    # (donjon Bouftou) ne propose « Donner la clé » (564) qu'à qui a la clé ; le trousseau (6602) à tous.
    question: tuple[int, tuple[int, ...]] | None = None
    map_date: str | None = None  # version de la carte du dernier GDM
    weapon_uid: int | None = None  # exemplaire passé dans l'emplacement d'arme (OM<uid>|1, OAKO position 1)
    exchange_at: float = 0.0
    exchange_left_at: float = 0.0
    pods_at: float = 0.0
    tackled_at: float = 0.0
    death_at: float = 0.0  # dernier combattant mort annoncé (GA;103) : son animation retient le clic suivant (01/11)
    # Récolte (brain/harvest.py, 27/10) : clé de la carte (GDM, la même à chaque passage : mapData déchiffré, ressources
    # par case), état des objets interactifs qui ne sont pas dans leur état de départ (GDF « case;état;? » : 2 en cours,
    # 3 récolté, 4 repousse), récoltes commencées (GA1;501;récolteur;case,durée en ms), quantité obtenue (IQ), niveaux
    # des métiers (JX|métier;niveau;...).
    map_key: str | None = None
    objects: dict[int, int] = field(default_factory=dict)
    harvests: dict[int, tuple[float, int]] = field(default_factory=dict)  # case -> (heure, durée en ms)
    harvested_at: float = 0.0
    harvested: int = 0
    jobs: dict[int, int] = field(default_factory=dict)
    # Objet posé dans l'emplacement d'arme (« OM<uid>|1 », ou « OAKO<uid>~modèle~qté~1~... » : pile coupée) ; 27/10.
    weapon_at: float = 0.0  # dernier tacle de mon personnage (GA;104, suivi de GA;127 : PM perdus, GA;101 : PA perdus)
    # Caractéristiques de mon personnage (« As » : ...|vie,vie max|énergie,énergie max|...) et pods (« Ow?|?|pods|max »).
    life: tuple[int, int] | None = None
    energy: tuple[int, int] | None = None
    stats: dict[str, int] | None = None  # clés de STAT_FIELDS (celles de brain/stats.CharacterStats)
    pods: tuple[int, int] | None = None
    fight: Fight | None = None  # le dernier combat (fini si fight.ended_at)
    # Inventaire, échanges et coffre (brain/inventory.py, 30/10) : complet seulement si la connexion a été suivie (ASK).
    inventory: Inventory = field(default_factory=Inventory, repr=False)
    inventory_at: float = 0.0  # dernier message d'inventaire, d'échange ou de coffre
    # Combats posés sur la carte (épées) : « Gc+combat;type|équipe;case;n°;?|... » et « Gt<équipe>|+id;nom;niveau ».
    flags: dict[int, int] = field(default_factory=dict)  # équipe -> case de son épée
    flag_fights: dict[int, list[int]] = field(default_factory=dict)  # combat -> ses équipes
    flag_members: dict[int, set[int]] = field(default_factory=dict)  # équipe -> ids de ses combattants
    # Groupe (31/10, observé) : membres annoncés par le serveur, id -> (nom, initiative) dans l'ordre d'arrivée
    # (« PM+id;nom;gfx;c1;c2;c3;accessoires;vie,max;niveau;initiative;prospection;côté|... », « PM~ » mise à jour,
    # « PM-id » départ, « PCK » nouveau groupe, « PV » groupe quitté). Le cadre du groupe range ses miniatures par
    # initiative décroissante (vu chez Fourboun et Eldieso : Enu-six 371, Eldieso 348, ... Elseiso 6).
    group: dict[int, tuple[str, int]] = field(default_factory=dict)
    # Niveau et vie (vie, vie max) de chaque membre du groupe, par nom, des mêmes messages (01/11).
    group_levels: dict[str, int] = field(default_factory=dict)
    group_life: dict[str, tuple[int, int]] = field(default_factory=dict)
    # Connexion (01/11) : personnage du dernier « ASK » de cette fenêtre, et ses sorts (« SL », parse_spell_list).
    login_name: str = ""
    spells: dict[int, tuple[int, int | None]] = field(default_factory=dict)
    # Réponse à « /p %pos% » renvoyée par le serveur (31/10, observé : « cMK$|1715951|Eldieso|[2, -34]| », reçue par
    # tout le groupe, l'expéditeur compris) : seules les réponses de mon personnage sont lues, rien d'autre du chat.
    told_coords: tuple[int, int] | None = None
    told_at: float = 0.0
    messages: int = 0
    heard_at: float = 0.0  # time.monotonic() du dernier message reçu (alive)
    move_steps: int = 0  # PM dépensés par mon dernier déplacement en combat (GA;129 : le chemin est compressé)
    # 31/10 (combat d'après le serveur, docs/rapports/ecran_vers_reseau.md) : entré dans un combat (GJK), refus de
    # rejoindre un combat (« GA;903;<id>;<code> », code d'erreur d'une lettre, pas encore vu en jeu), Prêt de mon
    # personnage (GR1<mon id>), fin du placement (GS). Heures time.monotonic() (wait_for).
    fight_at: float = 0.0
    join_refused_at: float = 0.0
    join_refusal: str = ""  # dernier refus entendu, message brut « GA;903;... »
    ready_at: float = 0.0
    fight_begun_at: float = 0.0
    # Derniers messages (heure, texte), pour le journal d'un geste raté ; jamais le chat des autres joueurs (« cMK »).
    recent: deque = field(default_factory=lambda: deque(maxlen=RECENT_KEPT), repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _changed: threading.Condition = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._changed = threading.Condition(self._lock)

    # Côté fil d'écoute

    def feed(self, text: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self.messages += 1
            self.heard_at = now
            if not text.startswith("cMK"):
                self.recent.append((now, text[:160]))
            try:  # un message d'inventaire inattendu ne doit pas empêcher la lecture qui suit (As, Ow, OM...)
                if text[:1] in ("A", "E", "O") and self.inventory.feed(text, now):
                    self.inventory_at = now  # en plus de ce qui suit (ECK, EV, OM, OAKO gardent leur rôle)
                    if text.startswith("ASK|"):
                        self._keep_login_bag(text)
                        parts = text.split("|")
                        if len(parts) > 2 and _is_int(parts[1]):
                            remember_id(parts[2], int(parts[1]))  # « ASK|id|nom|... » : mon personnage
                            self.login_name = parts[2]
                            if self.my_id is None and self.name in ("", parts[2]):
                                self.my_id = int(parts[1])
            except Exception:
                log.debug("Inventaire : message ignoré (%s)", text[:60], exc_info=True)
            if text.startswith("GDM|"):
                parts = text.split("|")
                if len(parts) > 1 and parts[1].isdigit():
                    self.map_id, self.map_at = int(parts[1]), now
                    self.map_key = parts[3] if len(parts) > 3 and parts[3] else None
                    # Version de la carte (31/10, brain/mapdata.py : une clé ne déchiffre qu'une version du fichier).
                    self.map_date = parts[2] if len(parts) > 2 and parts[2] else None
                    self.objects, self.harvests = {}, {}
                    self.actors = {}
                    self.flags, self.flag_fights, self.flag_members = {}, {}, {}
                    if self.fight is not None and not self.fight.ended_at:
                        self.fight.ended_at = now  # carte rechargée : combat fini, même sans « GE » (coupure)
            elif text.startswith("GCK") and self.fight is not None and not self.fight.ended_at:
                self.fight.ended_at = now  # « GCK|1|Nom » suit toujours la fin d'un combat
            elif text == "GDK":
                self.loaded_at = now
            elif text.startswith("SL") and text[2:3].isdigit():
                # Sorts du personnage qui vient de se connecter (suit son « ASK ») : retenus, la fenêtre des sorts et la
                # barre n'ont plus à être lues pour savoir lesquels, à quel niveau, dans quelle case.
                self.spells = parse_spell_list(text[2:].rstrip("|"))
                remember_spells(self.login_name or self.name, self.spells)
            elif text.startswith("As"):
                fields = text[2:].split("|")
                for index, name in ((5, "life"), (6, "energy")):
                    values = fields[index].split(",") if len(fields) > index else []
                    if len(values) == 2 and all(v.isdigit() for v in values):
                        setattr(self, name, (int(values[0]), int(values[1])))
                stats = {}
                for name, index in STAT_FIELDS.items():
                    values = fields[index].split(",")[:3] if len(fields) > index else []
                    if len(values) == 3 and all(_is_int(v) for v in values):
                        stats[name] = sum(int(v) for v in values)
                if len(stats) == len(STAT_FIELDS):
                    self.stats = stats
            elif text.startswith("Ow"):
                values = text[2:].split("|")
                # « Ow<pods portés>|0|<pods max>|? » (26/10 : Fourboun 717/1800 = 40 %, Walitcoin 1193/1698 = 70 %,
                # comme leur jauge ; le max suit la Force, bonus de combat compris : pas cru en combat).
                if len(values) >= 3 and values[0].isdigit() and values[2].isdigit() and int(values[2]) > 0:
                    self.pods, self.pods_at = (int(values[0]), int(values[2])), now
            elif text.startswith("GDF|"):
                for entry in text[4:].split("|"):
                    values = entry.split(";")
                    if len(values) >= 2 and values[0].isdigit() and values[1].isdigit():
                        self.objects[int(values[0])] = int(values[1])
            elif text.startswith(("GA1;501;", "GA0;501;", "GA;501;")):  # 01/11 : « GA0;501 » vu aussi
                parts = text.split(";")
                target = parts[3].split(",") if len(parts) > 3 else []
                # Récolte d'un autre joueur sur la carte : pas la mienne (01/11, Elseiso : un Frêne pris par un autre
                # compté récolté en 3,9 s, sans quantité). Sa ressource passe de toute façon à l'état 2 (GDF).
                mine = self.my_id is None or not _is_int(parts[2]) or int(parts[2]) == self.my_id
                if mine and len(target) >= 2 and target[0].isdigit() and target[1].isdigit():
                    self.harvests[int(target[0])] = (now, int(target[1]))
            elif text.startswith("IQ"):
                values = text[2:].split("|")
                if len(values) >= 2 and values[1].isdigit():
                    self.harvested_at, self.harvested = now, int(values[1])
            elif text.startswith("OM"):
                if text.split("|")[-1] == "1":
                    self.weapon_at = now
                    uid = text[2:].split("|")[0]
                    self.weapon_uid = int(uid) if uid.isdigit() else None  # décimal, comme OQ/OR (ASK, OAKO : hexa)
            elif text.startswith("OAKO"):
                for entry in text[4:].split(";"):
                    fields = entry.split("~")
                    if fields[3:4] == ["1"]:
                        self.weapon_at = now
                        self.weapon_uid = int(fields[0], 16) if fields[0] else None
            elif text.startswith("JX|"):
                for entry in text[3:].split("|"):
                    values = entry.split(";")
                    if len(values) >= 2 and values[0].isdigit() and values[1].isdigit():
                        self.jobs[int(values[0])] = int(values[1])
            elif text.startswith("DCK"):
                self.dialog_at = now
            elif text.startswith("DQ"):
                self.question_at = now
                head, _, answers = text[2:].partition("|")
                question = head.split(";")[0]
                if question.isdigit():
                    self.question = (int(question), tuple(int(a) for a in answers.split(";") if a.isdigit()))
            elif text.startswith("ECK"):
                self.exchange_at = now
            elif text.startswith("EV"):
                self.exchange_left_at = now
            elif text.startswith("GM|"):
                self._actors(text[3:])
            elif text.startswith(("PM+", "PM~")):
                for entry in text[3:].split("|"):
                    fields = entry.split(";")
                    if len(fields) > 9 and _is_int(fields[0]) and _is_int(fields[9]):
                        self.group[int(fields[0])] = (fields[1], int(fields[9]))
                        if fields[8].isdigit():
                            self.group_levels[fields[1]] = int(fields[8])
                        life = fields[7].split(",")
                        if len(life) == 2 and all(v.isdigit() for v in life) and int(life[1]) > 0:
                            self.group_life[fields[1]] = (int(life[0]), int(life[1]))
            elif text.startswith("PM-"):
                if _is_int(text[3:]):
                    self.group.pop(int(text[3:]), None)
            elif text.startswith(("PCK", "PV")):
                self.group.clear()
            elif text.startswith("cMK$|"):
                self._party_position(text[5:], now)
            elif text.startswith("GA0;1;") or text.startswith("GA;1;"):
                parts = text.split(";")
                if len(parts) >= 4 and _is_int(parts[2]):
                    who, cells = int(parts[2]), path_cells(parts[3])
                    if cells:
                        self._place(who, cells[-1])
                        if who == self.my_id:
                            # Chemin compressé (seuls les changements de direction) : sa longueur vient de GA;129,
                            # reçu dans la même salve ; à défaut, au moins une case par point.
                            self.moved_at, self.move_cells, self.move_steps = now, cells, max(1, len(cells) - 1)
            elif text.startswith(("GA;4;", "GA;5;")):
                # Téléportation, poussée : « GA;5;lanceur;cible,case,... ».
                parts = text.split(";")
                target = parts[3].split(",") if len(parts) > 3 else []
                if len(target) >= 2 and _is_int(target[0]) and _is_int(target[1]):
                    self._place(int(target[0]), int(target[1]))
            elif text.startswith("GA;104;"):
                # Taclé en quittant le contact d'un ennemi (27/10, Walitcoin : « GA;104;id; », puis tous ses PM perdus
                # « GA;127;id;id,-3 » et un PA « GA;101;id;id,-1 ») : pas de déplacement.
                parts = text.split(";")
                if len(parts) > 2 and _is_int(parts[2]) and int(parts[2]) == self.my_id:
                    self.tackled_at = now
            elif text.startswith(("GA;102;", "GA;129;", "GA;101;", "GA;127;")):
                # PA (102) ou PM (129) dépensés : « GA;102;lanceur;cible,-2 » ; PA (101) ou PM (127) perdus (tacle,
                # sort d'un ennemi), même forme.
                pa = text.startswith(("GA;102;", "GA;101;"))
                parts = text.split(";")
                values = parts[3].split(",") if len(parts) > 3 else []
                if len(values) >= 2 and _is_int(values[0]) and _is_int(values[1]):
                    who, delta = int(values[0]), int(values[1])
                    if self.fight is not None and not self.fight.ended_at:
                        state = self.fight.fighters.setdefault(who, FighterState())
                        if pa:
                            state.pa = max(0, state.pa + delta)
                        else:
                            state.pm = max(0, state.pm + delta)
                    if who == self.my_id and text.startswith("GA;102;"):
                        self.spent_at = now
                    elif who == self.my_id and text.startswith("GA;129;"):
                        self.move_steps = -delta
            elif text.startswith(("GA;300;", "GA;302;")):
                parts = text.split(";")
                if len(parts) > 2 and _is_int(parts[2]) and int(parts[2]) == self.my_id:
                    if text.startswith("GA;300;"):
                        self.cast_at = now
                        # « GA;300;lanceur;sort,case,... » (01/11) : le numéro du sort parti, pour vérifier la case.
                        spell = parts[3].split(",")[0] if len(parts) > 3 else ""
                        self.cast_spell = int(spell) if spell.isdigit() else None
                    else:
                        self.failed_at = now
            elif text.startswith(("GA;100;", "GA;108;")):
                # Vie perdue (100) ou rendue (108) : « GA;100;lanceur;cible,-24,élément » ; la vie de chacun à jour
                # après chaque coup, pas seulement au début du tour (GTM).
                parts = text.split(";")
                values = parts[3].split(",") if len(parts) > 3 else []
                if len(values) >= 2 and _is_int(values[0]) and _is_int(values[1]) and self.fight is not None \
                        and not self.fight.ended_at:
                    state = self.fight.fighters.setdefault(int(values[0]), FighterState())
                    state.hp = max(0, state.hp + int(values[1]))
                    if state.max_hp:
                        state.hp = min(state.hp, state.max_hp)
            elif text.startswith("GA;181;"):
                parts = text.split(";")
                if len(parts) > 6 and _is_int(parts[2]) and parts[3].startswith("+") and _is_int(parts[3][1:]) \
                        and _is_int(parts[6]):
                    summoner, cell, who = int(parts[2]), int(parts[3][1:]), int(parts[6])
                    actor = Actor(who, cell)
                    owner = self.actors.get(summoner)
                    actor.team = owner.team if owner is not None else None
                    self.actors[who] = actor
                    if self.fight is not None and not self.fight.ended_at:
                        state = self.fight.fighters.setdefault(who, FighterState())
                        state.dead, state.summoned = False, True
            elif text.startswith("GA;150;"):
                parts = text.split(";")
                target, _, rest = (parts[3] if len(parts) > 3 else "").partition(",")
                turns = rest.split(",")[0]
                if _is_int(target) and _is_int(turns) and self.fight is not None:
                    self.fight.fighters.setdefault(int(target), FighterState()).hidden = int(turns) > 0
            elif text.startswith("GA;103;"):
                parts = text.split(";")
                if len(parts) > 3 and _is_int(parts[3]) and self.fight is not None:
                    self.fight.fighters.setdefault(int(parts[3]), FighterState()).dead = True
                    self.death_at = now
            elif text.startswith("GA;903;"):
                # Refus de rejoindre un combat (31/10, Walitcoin : « Rejoindre » cliqué deux fois sans effet, 20 s
                # perdues) : dit à cette fenêtre seule, avant tout GJK.
                self.join_refused_at, self.join_refusal = now, text
            elif text.startswith("Gc+"):
                head, *teams = text[3:].split("|")
                fight_id = head.split(";")[0]
                ids = []
                for team in teams:
                    values = team.split(";")
                    if len(values) >= 2 and _is_int(values[0]) and _is_int(values[1]):
                        self.flags[int(values[0])] = int(values[1])
                        ids.append(int(values[0]))
                if _is_int(fight_id):
                    self.flag_fights[int(fight_id)] = ids
            elif text.startswith("Gc-"):
                if _is_int(text[3:]):
                    for team in self.flag_fights.pop(int(text[3:]), []):
                        self.flags.pop(team, None)
                        self.flag_members.pop(team, None)
            elif text.startswith("Gt") and not text.startswith("GT"):
                head, *entries = text[2:].split("|")
                if _is_int(head):
                    members = self.flag_members.setdefault(int(head), set())
                    for entry in entries:
                        who = entry[1:].split(";")[0]
                        if _is_int(who):
                            (members.add if entry.startswith("+") else members.discard)(int(who))
            elif text.startswith("GJK"):
                self.fight = Fight(now)
                self.fight_at = now
            elif (self.fight is None or self.fight.ended_at) and not self._fight_deduced(text, now):
                return
            elif text.startswith("GIC|"):
                for entry in text[4:].split("|"):
                    values = entry.split(";")
                    if len(values) >= 2 and _is_int(values[0]) and _is_int(values[1]):
                        self._place(int(values[0]), int(values[1]))
            elif text.startswith("GTL|"):
                self.fight.order = [int(v) for v in text[4:].split("|") if _is_int(v)]
            elif text.startswith("GTS"):
                who = text[3:].split("|")[0]
                if _is_int(who):
                    self.fight.turn, self.fight.turn_at, self.fight.turn_over = int(who), now, False
            elif text.startswith("GTF"):
                if _is_int(text[3:]) and int(text[3:]) == self.fight.turn:
                    self.fight.turn_over = True
            elif text.startswith("GTM|"):
                self._turn_states(text[4:])
            elif text.startswith("GR") and text[2:3] in ("0", "1") and _is_int(text[3:]):
                # « GR1<id> » : Prêt ; « GR0<id> » : plus prêt (capture du 26/10 : « GR11996393 », « GR1986971 »).
                who, ready = int(text[3:]), text[2] == "1"
                self.fight.ready[who] = ready
                if ready and who == self.my_id:
                    self.ready_at = now
            elif text.startswith("GS"):
                self.fight.begun_at = self.fight_begun_at = now
            elif text.startswith("GP") and "|" in text:
                parts = text[2:].split("|")
                if parts[-1].isdigit():
                    self.fight.my_team = int(parts[-1])
                if len(parts) >= 3:
                    self.fight.places = tuple(frozenset(map_cell(c) for c in coded_cells(part)) for part in parts[:2])
            elif text.startswith("GE"):
                self.fight.ended_at = now
            else:
                return
            self._changed.notify_all()

    def _fight_deduced(self, text: str, now: float) -> bool:
        """Message qui n'existe qu'en combat (placement GP/GIC, tours GTL/GTS/GTM/GTF) sans « GJK » entendu : écoute
        ouverte en plein combat, ou « GJK » perdu (interfaces rouvertes toutes les 30 s). Le combat est noté (en cours,
        commencé si c'est un message de tour) pour que la phase d'après le serveur reste juste ; False sinon. Pas juste
        après un « GE » (dernier message de tour arrivé après la fin)."""
        placing = text.startswith(("GIC|", "GP")) and "|" in text
        turning = text.startswith(("GTL|", "GTS", "GTM|", "GTF"))
        if not (placing or turning) or (self.fight is not None and now - self.fight.ended_at < FIGHT_TAIL):
            return False
        self.fight = Fight(now, begun_at=now if turning else 0.0)
        log.info("%s : combat en cours d'après « %s » (début pas entendu)", self.name or "?", text[:12])
        return True

    def phase(self) -> str | None:
        """Phase du jeu d'après le serveur : « combat » (après GS ou un message de tour), « placement » (GJK, pas encore
        GS), « hors combat » ; None quand l'écoute ne peut pas le dire : flux muet depuis 60 s, ou aucune carte chargée
        entendue (écoute ouverte en plein combat : le combat n'est connu qu'au premier message de tour)."""
        with self._lock:
            if time.monotonic() - self.heard_at >= 60.0:
                return None
            fight = self.fight
            if fight is not None and not fight.ended_at:
                return "combat" if fight.begun_at else "placement"
            if fight is None and not self.map_at and not self.loaded_at:
                return None
            return "hors combat"

    def _place(self, who: int, cell: int) -> None:
        actor = self.actors.setdefault(who, Actor(who, cell))
        actor.cell = cell

    def _turn_states(self, body: str) -> None:
        """« GTM|id;mort;vie;PA;PM;case;;vie max|... » (mort : « id;1 »)."""
        for entry in body.split("|"):
            values = entry.split(";")
            if not values or not _is_int(values[0]):
                continue
            who = int(values[0])
            state = self.fight.fighters.setdefault(who, FighterState())
            if len(values) >= 2 and values[1] == "1":
                state.dead = True
                continue
            if len(values) >= 6 and all(_is_int(v) for v in values[2:6]):
                state.hp, state.pa, state.pm = int(values[2]), int(values[3]), int(values[4])
                state.dead = False
                state.hidden = int(values[5]) < 0  # case -1 : invisible (31/10, Prespic) ; sinon sa case
                if not state.hidden:
                    self._place(who, int(values[5]))
            if len(values) >= 8 and _is_int(values[7]):
                state.max_hp = int(values[7])

    def _actors(self, body: str) -> None:
        for entry in body.split("|"):
            if entry.startswith("-"):
                if _is_int(entry[1:]):
                    self.actors.pop(int(entry[1:]), None)
                continue
            if not entry.startswith("+"):
                continue
            fields = entry[1:].split(";")
            if len(fields) < 4 or not _is_int(fields[0]) or not _is_int(fields[3]):
                continue
            actor = Actor(int(fields[3]), int(fields[0]), fields[4] if len(fields) > 4 else "")
            fighting = self.fight is not None and not self.fight.ended_at
            if actor.id < 0 and not fighting and len(fields) > 7 and fields[5] == "-3":
                # Groupe de monstres sur la carte : « case;dir;0;id;modèles;-3;apparences;niveaux;... ».
                actor.levels = tuple(int(v) for v in fields[7].split(",") if v.isdigit())
            if fighting:
                tail = [f for f in fields if f != ""]
                if len(tail) >= 2 and tail[-2] in ("0", "1"):
                    actor.team = int(tail[-2])
            self.actors[actor.id] = actor
            if actor.name and actor.name == self.name:
                self.my_id = actor.id
                remember_id(self.name, actor.id)

    def _party_position(self, text: str, now: float) -> None:
        """« id|nom|[x, y]| » du canal de groupe : coordonnées retenues seulement si c'est mon personnage qui parle."""
        parts = text.split("|")
        if len(parts) < 3:
            return
        sender = int(parts[0]) if _is_int(parts[0]) else None
        mine = (sender is not None and sender in (self.my_id, self.inventory.my_id)) or (self.name and parts[1] == self.name)
        match = PARTY_POSITION.fullmatch(parts[2].strip())
        if mine and match:
            self.told_coords, self.told_at = (int(match[1]), int(match[2])), now

    def position_told_since(self, after: float) -> tuple[int, int] | None:
        """Coordonnées de ma dernière réponse à « /p %pos% » entendue après `after` (time.monotonic())."""
        with self._lock:
            return self.told_coords if self.told_at >= after and self.told_at > 0 else None

    def _keep_login_bag(self, text: str) -> None:
        """Sac complet de la connexion écrit tout de suite (brain/possessions.py), au nom porté par le message
        (« ASK|id|nom|... » ; la fenêtre n'est pas encore nommée pendant la connexion) : les numéros d'objets changent
        à chaque connexion."""
        from bot.brain import possessions  # import local : brain -> connection seulement à l'usage

        parts = text.split("|")
        if len(parts) > 2 and parts[2]:
            items = [[i.uid, i.template, i.quantity, i.position] for i in self.inventory.items.values()]
            if possessions.write(parts[2], items, pods=list(self.pods) if self.pods else None):
                self._saved_items = items

    # Côté bot

    def weapon_template(self) -> int | None:
        """Modèle de l'objet dans l'emplacement d'arme (31/10 : l'exemplaire du serveur, son modèle d'après le sac de
        la session) ; None si inconnu."""
        with self._lock:
            item = self.inventory.items.get(self.weapon_uid) if self.weapon_uid is not None else None
            if item is None:  # pas de OM entendu à ce lancement : l'objet en position 1 du sac connu
                item = next((i for i in self.inventory.items.values() if i.position == 1), None)
            return item.template if item is not None else None

    def my_cell(self) -> int | None:
        """Numéro de case de mon personnage sur la carte actuelle (None : pas encore annoncé)."""
        with self._lock:
            actor = self.actors.get(self.my_id) if self.my_id is not None else None
            return actor.cell if actor else None

    def my_state(self) -> FighterState | None:
        """Mon état en combat (vie, PA, PM d'après GTM puis les actions) ; None hors combat ou inconnu."""
        with self._lock:
            if self.fight is None or self.fight.ended_at or self.my_id is None:
                return None
            state = self.fight.fighters.get(self.my_id)
            return FighterState(state.hp, state.max_hp, state.pa, state.pm, state.dead) if state else None

    def my_team(self) -> int | None:
        """Mon équipe dans le combat en cours (0 ou 1)."""
        with self._lock:
            actor = self.actors.get(self.my_id) if self.my_id is not None else None
            if actor is not None and actor.team is not None:
                return actor.team
            return self.fight.my_team if self.fight is not None else None

    def wait_for(self, attribute: str | tuple[str, ...], after: float, timeout: float,
                 poll: float = 0.03) -> float | None:
        """Attend que `attribute` (heure d'un évènement : spent_at, cast_at, moved_at ; ou le premier de plusieurs)
        dépasse `after` ; son heure, ou None après `timeout` s."""
        names = (attribute,) if isinstance(attribute, str) else attribute
        end = time.monotonic() + timeout
        with self._lock:
            while (latest := max(getattr(self, name) for name in names)) < after:
                left = end - time.monotonic()
                if left <= 0:
                    return None
                self._changed.wait(min(left, poll))
            return latest

    def flag_cell(self, player_id: int) -> tuple[int, int] | None:
        """Case de la grille de l'épée de l'équipe où combat `player_id` (Gc+, Gt) ; None si aucune annoncée."""
        with self._lock:
            team = next((t for t, ids in self.flag_members.items() if player_id in ids and t in self.flags), None)
            return map_cell(self.flags[team]) if team is not None else None

    def life_ratio(self) -> float | None:
        """Vie de mon personnage hors combat (0 à 1), d'après le serveur (« As ») ; None si pas encore annoncée."""
        return self.life[0] / self.life[1] if self.life and self.life[1] else None

    def frame_slot(self, name: str) -> int | None:
        """Place (0 = en haut) de la miniature de `name` dans le cadre du groupe : rang de son initiative, de la plus
        haute à la plus basse, d'après le serveur ; None s'il n'est pas dans le groupe connu ou si son initiative est
        aussi celle d'un autre (ordre des égalités pas observé)."""
        with self._lock:
            members = list(self.group.values())
        mine = [initiative for who, initiative in members if who == name]
        if len(mine) != 1 or sum(1 for _, initiative in members if initiative == mine[0]) > 1:
            return None
        return sum(1 for _, initiative in members if initiative > mine[0])

    def pods_ratio(self) -> float | None:
        """Pods de mon personnage (0 à 1), d'après le serveur (« Ow ») ; None si pas encore annoncés."""
        return self.pods[0] / self.pods[1] if self.pods and self.pods[1] else None

    def held(self, template: int) -> int | None:
        """Objets de ce modèle portés, d'après le serveur ; None si l'inventaire n'est pas connu (connexion pas suivie)."""
        with self._lock:
            return self.inventory.count(template) if self.inventory.known else None

    def items(self) -> list[Item]:
        """Copie de l'inventaire (ordre d'arrivée) ; partielle sans ASK (seulement les objets ajoutés depuis)."""
        with self._lock:
            return [Item(i.uid, i.template, i.quantity, i.position, i.effects) for i in self.inventory.items.values()]

    def trade(self) -> tuple[Exchange | None, dict[int, int]]:
        """(dernier échange ou coffre ouvert, copie ; ses offres en modèle -> quantité : la mienne d'après mes piles).
        L'échange est fermé si closed_at."""
        with self._lock:
            exchange = self.inventory.exchange
            if exchange is None:
                return None, {}
            copy = Exchange(exchange.kind, exchange.opened_at, dict(exchange.mine), dict(exchange.theirs),
                            dict(exchange.storage), exchange.kamas_mine, exchange.kamas_theirs,
                            exchange.kamas_storage, dict(exchange.ready), exchange.changed_at, exchange.closed_at,
                            exchange.concluded)
            mine: dict[int, int] = {}
            for uid, quantity in exchange.mine.items():
                item = self.inventory.items.get(uid)
                if item is not None:
                    mine[item.template] = mine.get(item.template, 0) + quantity
            return copy, mine

    def heard_since(self, since: float) -> list[str]:
        """Messages reçus depuis `since` (time.monotonic()), les RECENT_KEPT derniers au plus, hors chat."""
        with self._lock:
            return [text for at, text in self.recent if at >= since]

    def alive(self, quiet: float = 60.0) -> bool:
        """Le serveur a parlé à cette fenêtre depuis moins de `quiet` s (il envoie au moins « BN » toutes les ~30 s) :
        sinon (fenêtre reconnectée sur un autre port, écoute arrêtée), ce flux est périmé."""
        return time.monotonic() - self.heard_at < quiet

    def in_fight(self) -> bool:
        with self._lock:
            return self.fight is not None and not self.fight.ended_at

    def wait_turn_over(self, who: int, timeout: float, poll: float = 0.03) -> bool:
        """Le tour de `who` finit dans les `timeout` s : GTF de son id, GTS d'un autre, ou fin du combat (GE, carte
        rechargée). True aussi si ce n'était déjà plus son tour ; False s'il l'est encore après `timeout` s."""
        end = time.monotonic() + timeout
        with self._lock:
            while self.fight is not None and not self.fight.ended_at and self.fight.turn == who                     and not self.fight.turn_over:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._changed.wait(min(left, poll))
            return True

    def fight_ready(self) -> set[int]:
        """Combattants prêts (GR1) du combat en cours ; vide hors combat."""
        with self._lock:
            if self.fight is None or self.fight.ended_at:
                return set()
            return {who for who, ready in self.fight.ready.items() if ready}

    def placement_told(self) -> tuple[frozenset, frozenset, int | None] | None:
        """Cases de placement de l'équipe 0, de l'équipe 1 (GP) et le numéro de mon équipe, pendant le combat en
        cours ; None sans « GP » entendu."""
        with self._lock:
            if self.fight is None or self.fight.ended_at or self.fight.places is None:
                return None
            return self.fight.places[0], self.fight.places[1], self.fight.my_team

    def fight_begun(self) -> bool:
        """Le combat en cours a commencé (GS) : placement fini."""
        with self._lock:
            return self.fight is not None and not self.fight.ended_at and self.fight.begun_at > 0

    def join_refused_since(self, after: float) -> str | None:
        """Refus de rejoindre un combat (« GA;903;... », message brut) entendu depuis `after` ; None sinon."""
        with self._lock:
            return self.join_refusal if self.join_refused_at and self.join_refused_at >= after else None

    def fight_turn(self) -> tuple[int | None, bool, float]:
        """(id de celui qui joue, son tour fini (GTF), heure de début de son tour) ; (None, True, 0) hors combat."""
        with self._lock:
            if self.fight is None or self.fight.ended_at:
                return None, True, 0.0
            return self.fight.turn, self.fight.turn_over, self.fight.turn_at

    def fighters(self) -> list[tuple[int, tuple[int, int], int | None, FighterState]]:
        """Combattants debout du combat en cours : (id, case de la grille, équipe, état). Vide hors combat."""
        with self._lock:
            if self.fight is None or self.fight.ended_at:
                return []
            out = []
            for who, state in self.fight.fighters.items():
                actor = self.actors.get(who)
                if actor is not None and not state.dead and not state.hidden and actor.cell >= 0:
                    out.append((who, map_cell(actor.cell), actor.team, state))
            return out

    def hidden_fighters(self) -> list[tuple[int, tuple[int, int] | None, int | None]]:
        """Combattants invisibles du combat en cours : (id, dernière case connue ou None, équipe)."""
        with self._lock:
            if self.fight is None or self.fight.ended_at:
                return []
            out = []
            for who, state in self.fight.fighters.items():
                actor = self.actors.get(who)
                if state.hidden and not state.dead:
                    cell = map_cell(actor.cell) if actor is not None and actor.cell >= 0 else None
                    out.append((who, cell, actor.team if actor is not None else None))
            return out

    def life_at(self, cell: tuple[int, int]) -> int | None:
        """Vie du combattant sur `cell` (case de la grille) d'après le serveur (GTM, puis GA;100 / GA;108) ; 0 s'il y
        est mort (GA;103) ; None hors combat, personne de connu sur cette case, ou vie pas encore annoncée."""
        with self._lock:
            if self.fight is None or self.fight.ended_at:
                return None
            found = None
            for who, state in self.fight.fighters.items():
                actor = self.actors.get(who)
                if actor is None or map_cell(actor.cell) != cell:
                    continue
                if not state.dead and state.max_hp:
                    return state.hp
                if state.dead:
                    found = 0
            return found

    def group_list(self) -> list[tuple[int, tuple[int, int], tuple[int, ...]]]:
        """Groupes de monstres de la carte (hors combat) : (id, case de la grille, niveaux de ses monstres)."""
        with self._lock:
            if self.fight is not None and not self.fight.ended_at:
                return []
            return [(a.id, map_cell(a.cell), a.levels) for a in self.actors.values() if a.id < 0 and a.levels]

    def group_cell(self, group_id: int) -> tuple[int, int] | None:
        """Case actuelle de ce groupe (déplacements compris, GA;1) ; None s'il n'est plus sur la carte."""
        with self._lock:
            actor = self.actors.get(group_id)
            return map_cell(actor.cell) if actor is not None and actor.levels else None

    def fight_since(self, after: float) -> bool:
        """Un combat a commencé (GJK) depuis `after` (time.monotonic())."""
        with self._lock:
            return self.fight is not None and self.fight.started_at >= after

    def groups(self) -> list[tuple[tuple[int, int], tuple[int, ...]]]:
        """Groupes de monstres de la carte (hors combat) : (case de la grille, niveaux)."""
        with self._lock:
            if self.fight is not None and not self.fight.ended_at:
                return []
            return [(map_cell(a.cell), a.levels) for a in self.actors.values() if a.id < 0 and a.levels]

    def my_grid_cell(self) -> tuple[int, int] | None:
        cell = self.my_cell()
        return map_cell(cell) if cell is not None else None

    def snapshot(self) -> tuple[int | None, float, float]:
        with self._lock:
            return self.map_id, self.map_at, self.loaded_at

    def wait_new_map(self, after: float, timeout: float) -> int | None:
        """Numéro de la carte annoncée (GDM) après `after` (time.monotonic()), carte chargée (GDK) ; None après
        `timeout` s sans changement de carte."""
        end = time.monotonic() + timeout
        with self._lock:
            while not (self.map_at >= after and self.loaded_at >= self.map_at):
                left = end - time.monotonic()
                if left <= 0:
                    return None
                self._changed.wait(left)
            return self.map_id

    def new_map_since(self, after: float) -> int | None:
        """Comme wait_new_map, sans attendre."""
        with self._lock:
            return self.map_id if self.map_at >= after and self.loaded_at >= self.map_at else None


IDS_FILE = Path(__file__).resolve().parents[2] / "data" / "personnages_ids.json"  # nom -> numéro (local)
_ids_cache: dict[str, int] | None = None


def known_ids() -> dict[str, int]:
    """Numéros des personnages déjà entendus (data/personnages_ids.json) : fixes d'une session à l'autre."""
    global _ids_cache
    if _ids_cache is None:
        try:
            _ids_cache = {str(k): int(v) for k, v in json.loads(IDS_FILE.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            _ids_cache = {}
    return _ids_cache


def remember_id(name: str, ident: int) -> None:
    """Retient le numéro de `name` (écrit seulement s'il est nouveau ou a changé)."""
    ids = known_ids()
    if not name or ids.get(name) == ident:
        return
    ids[name] = ident
    try:
        IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        IDS_FILE.write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        log.debug("Numéros des personnages pas écrits", exc_info=True)


SPELLS_FILE = Path(__file__).resolve().parents[2] / "data" / "personnages_sorts.json"  # nom -> sorts (local)
NOT_IN_BAR = 0x7E  # position d'un sort appris absent de la barre (« 173~1~7e », 01/11)
_spells_cache: dict[str, dict[int, tuple[int, int | None]]] | None = None


def parse_spell_list(body: str) -> dict[int, tuple[int, int | None]]:
    """« SL<id>~<niveau>~<case>;... » (envoyé à la connexion, 01/11) -> {id: (niveau, case de la barre ou None)}.

    Le numéro est celui des sorts chez Solomonk (161 Flèche Magique, 183 Ronce) ; la case s'écrit en hexadécimal, comme
    la numérotation de la barre du bot (Eldieso : Flèche Empoisonnée « c », case 12 ; Flèche d'Immobilisation « d »,
    case 13 ; Walitcoin : Remblai 7, Force de l'Age « a », 10)."""
    spells = {}
    for entry in body.split(";"):
        parts = entry.split("~")
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        try:
            slot = int(parts[2], 16)
        except ValueError:
            continue
        spells[int(parts[0])] = (int(parts[1]), slot if 0 < slot < NOT_IN_BAR else None)
    return spells


def known_spells(name: str) -> dict[int, tuple[int, int | None]]:
    """Sorts de `name` annoncés à sa dernière connexion entendue (data/personnages_sorts.json) ; {} si jamais."""
    global _spells_cache
    if _spells_cache is None:
        try:
            raw = json.loads(SPELLS_FILE.read_text(encoding="utf-8"))
            _spells_cache = {str(who): {int(k): (int(v[0]), v[1] if v[1] is None else int(v[1]))
                                        for k, v in (spells or {}).get("sorts", {}).items()}
                             for who, spells in raw.items()}
        except (OSError, ValueError, TypeError, IndexError, AttributeError):
            _spells_cache = {}
    return _spells_cache.get(name, {})


def remember_spells(name: str, spells: dict[int, tuple[int, int | None]]) -> None:
    """Retient les sorts annoncés à la connexion de `name` (écrit seulement s'ils ont changé)."""
    if not name or not spells or known_spells(name) == spells:
        return
    _spells_cache[name] = dict(spells)
    record = {who: {"sorts": {str(k): list(v) for k, v in sorted(told.items())}}
              for who, told in sorted(_spells_cache.items())}
    try:
        SPELLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SPELLS_FILE.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        log.debug("Sorts des personnages pas écrits", exc_info=True)


FIGHT_TAIL = 3.0  # s après « GE » : un message de tour encore reçu n'est pas un nouveau combat
PARTY_POSITION = re.compile(r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]")


def _is_int(text: str) -> bool:
    return text.lstrip("-").isdigit()


# --- Connexions du jeu : port local -> fenêtre ---------------------------------------------------------------------

class _TcpRow(ctypes.Structure):
    _fields_ = [("state", wt.DWORD), ("local_addr", wt.DWORD), ("local_port", wt.DWORD), ("remote_addr", wt.DWORD),
                ("remote_port", wt.DWORD), ("pid", wt.DWORD)]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_char * 260)]


def _port(value: int) -> int:
    return socket.ntohs(value & 0xFFFF)


def game_connections(port: int = GAME_PORT) -> dict[int, tuple[int, str]]:
    """Connexions TCP établies vers `port` : port local -> (PID, adresse distante). Toutes, pas seulement celles du jeu
    (filtrées par le PID parent ensuite)."""
    iphlpapi = ctypes.WinDLL("iphlpapi")
    size = wt.DWORD(0)
    TCP_TABLE_OWNER_PID_CONNECTIONS = 4
    iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, socket.AF_INET, TCP_TABLE_OWNER_PID_CONNECTIONS, 0)
    buffer = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetExtendedTcpTable(buffer, ctypes.byref(size), False, socket.AF_INET,
                                    TCP_TABLE_OWNER_PID_CONNECTIONS, 0) != 0:
        return {}
    count = struct.unpack_from("I", buffer, 0)[0]
    rows = (_TcpRow * count).from_buffer_copy(buffer, ctypes.sizeof(wt.DWORD))
    out = {}
    for row in rows:
        if row.state == 5 and _port(row.remote_port) == port:  # 5 : MIB_TCP_STATE_ESTAB
            out[_port(row.local_port)] = (row.pid, socket.inet_ntoa(struct.pack("<I", row.remote_addr)))
    return out


def parent_pids() -> dict[int, int]:
    """PID -> PID parent, pour tous les processus (instantané de Windows)."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    entry = _ProcessEntry()
    entry.dwSize = ctypes.sizeof(_ProcessEntry)
    parents = {}
    try:
        ok = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return parents


def window_pid(hwnd: int) -> int:
    import win32process

    return win32process.GetWindowThreadProcessId(hwnd)[1]


# --- Npcap ---------------------------------------------------------------------------------------------------------

class _Timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class _PktHdr(ctypes.Structure):
    _fields_ = [("ts", _Timeval), ("caplen", ctypes.c_uint32), ("len", ctypes.c_uint32)]


class _SockAddr(ctypes.Structure):
    _fields_ = [("sa_family", ctypes.c_ushort), ("sa_data", ctypes.c_ubyte * 14)]


class _PcapAddr(ctypes.Structure):
    pass


_PcapAddr._fields_ = [("next", ctypes.POINTER(_PcapAddr)), ("addr", ctypes.POINTER(_SockAddr)),
                      ("netmask", ctypes.POINTER(_SockAddr)), ("broadaddr", ctypes.POINTER(_SockAddr)),
                      ("dstaddr", ctypes.POINTER(_SockAddr))]


class _PcapIf(ctypes.Structure):
    pass


_PcapIf._fields_ = [("next", ctypes.POINTER(_PcapIf)), ("name", ctypes.c_char_p), ("description", ctypes.c_char_p),
                    ("addresses", ctypes.POINTER(_PcapAddr)), ("flags", ctypes.c_uint)]


class _BpfProgram(ctypes.Structure):
    _fields_ = [("bf_len", ctypes.c_uint), ("bf_insns", ctypes.c_void_p)]


def _load_pcap():
    path = os.path.join(NPCAP_DIR, "wpcap.dll")
    if not os.path.exists(path):
        return None
    os.add_dll_directory(NPCAP_DIR)
    pcap = ctypes.CDLL(path)
    pcap.pcap_findalldevs.argtypes = [ctypes.POINTER(ctypes.POINTER(_PcapIf)), ctypes.c_char_p]
    pcap.pcap_freealldevs.argtypes = [ctypes.POINTER(_PcapIf)]
    pcap.pcap_open_live.restype = ctypes.c_void_p
    pcap.pcap_open_live.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_char_p]
    pcap.pcap_compile.argtypes = [ctypes.c_void_p, ctypes.POINTER(_BpfProgram), ctypes.c_char_p, ctypes.c_int,
                                  ctypes.c_uint32]
    pcap.pcap_setfilter.argtypes = [ctypes.c_void_p, ctypes.POINTER(_BpfProgram)]
    pcap.pcap_next_ex.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(_PktHdr)),
                                  ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
    pcap.pcap_geterr.restype = ctypes.c_char_p
    pcap.pcap_geterr.argtypes = [ctypes.c_void_p]
    pcap.pcap_datalink.argtypes = [ctypes.c_void_p]
    pcap.pcap_close.argtypes = [ctypes.c_void_p]
    return pcap


def _interfaces(pcap) -> list[tuple[bytes, list[str]]]:
    alldevs = ctypes.POINTER(_PcapIf)()
    err = ctypes.create_string_buffer(256)
    if pcap.pcap_findalldevs(ctypes.byref(alldevs), err) != 0:
        return []
    found, dev = [], alldevs
    while dev:
        d, ips, addr = dev.contents, [], dev.contents.addresses
        while addr:
            a = addr.contents
            if a.addr and a.addr.contents.sa_family == socket.AF_INET:
                ips.append(socket.inet_ntoa(bytes(a.addr.contents.sa_data[2:6])))
            addr = a.next
        found.append((d.name, ips))
        dev = d.next
    pcap.pcap_freealldevs(alldevs)
    return found


def segment(raw: bytes, link: int) -> tuple[int, int, bytes] | None:
    """(port de destination, numéro de séquence, données TCP) d'une trame IPv4 ; None si ce n'en est pas une."""
    off = 14 if link == 1 else 4 if link == 0 else 0  # Ethernet ; boucle locale (NULL) ; IP brute
    if len(raw) < off + 20 or raw[off] >> 4 != 4:
        return None
    tcp = off + (raw[off] & 0x0F) * 4
    if len(raw) < tcp + 20:
        return None
    dport, seq = struct.unpack("!HI", raw[tcp + 2:tcp + 8])
    # Longueur du paquet d'après l'en-tête IP : une petite trame Ethernet est complétée jusqu'à 60 octets (01/11,
    # journal 144405 : ce remplissage compté comme données, le numéro TCP attendu passait trop loin, et fresh_part
    # jetait le début du segment suivant, « GA;129 » lu « ;129 », PM perdus jamais comptés).
    total = struct.unpack("!H", raw[off + 2:off + 4])[0]
    end = off + total if total else len(raw)
    return dport, seq, raw[tcp + (raw[tcp + 12] >> 4) * 4:end]


def payloads(raw: bytes, link: int) -> tuple[int, bytes] | None:
    """(port de destination, données TCP) d'une trame IPv4 ; None si ce n'en est pas une."""
    found = segment(raw, link)
    return (found[0], found[2]) if found is not None else None


SEQ_MOD = 1 << 32
NEW_STREAM_BEHIND = 1 << 20  # numéro très en arrière (plus d'1 Mo) : nouvelle connexion sur le même port


def fresh_part(expected: int | None, seq: int, data: bytes) -> tuple[bytes, int | None]:
    """(données pas encore lues, numéro attendu ensuite) d'un segment TCP. 01/11 (journal 113029, Eldieso) : un
    segment renvoyé par le réseau (retransmission) relu deux fois, « Tir Puissant » annoncé deux fois, 6 PA dépensés
    au lieu de 3 et le sort suivant, ignoré par le jeu, cru parti. Déjà lu : rien ; à cheval : la fin seulement ; trou
    (segment perdu ou en avance) ou nouvelle connexion : tout, et la suite repart de lui."""
    if not data:
        return b"", expected
    end = (seq + len(data)) % SEQ_MOD
    if expected is None:
        return data, end
    behind = (expected - seq) % SEQ_MOD  # octets du segment déjà lus (s'il commence avant l'attendu)
    if behind >= SEQ_MOD // 2 or behind > NEW_STREAM_BEHIND:
        return data, end  # en avance (trou), ou nouvelle connexion
    if behind >= len(data):
        return b"", expected  # déjà lu en entier
    return data[behind:], end


def _usable(ips: list[str]) -> bool:
    """Interface avec une adresse IPv4 hors boucle locale et hors auto-configuration (pas de réseau)."""
    return bool(ips) and not all(ip.startswith(("127.", "169.254.")) for ip in ips)


def _open_handle(pcap, name: bytes):
    """Poignée Npcap sur l'interface `name`, filtrée sur le trafic du serveur du jeu ; None si impossible."""
    err = ctypes.create_string_buffer(256)
    handle = pcap.pcap_open_live(name, 65535, 0, READ_TIMEOUT_MS, err)  # sans mode promiscuité : ce PC seul
    if not handle:
        return None
    program = _BpfProgram()
    if pcap.pcap_compile(handle, ctypes.byref(program), f"tcp and src port {GAME_PORT}".encode(), 1,
                         0xFFFFFFFF) != 0 or pcap.pcap_setfilter(handle, ctypes.byref(program)) != 0:
        pcap.pcap_close(handle)
        return None
    return handle


class NetworkListener:
    """Fil d'écoute : trafic du serveur du jeu vers ce PC, messages rendus à la ClientFeed de chaque fenêtre.
    Une veille relit les interfaces toutes les RESCAN_EVERY s et rouvre l'écoute de celles qui n'en ont plus
    (27/10 : erreur Npcap, Wi-Fi <-> câble, VPN) ; en attendant, chaque fenêtre se rabat sur son écran (alive)."""

    def __init__(self, pcap, handles: dict) -> None:
        self._pcap = pcap
        self._handles = dict(handles)  # nom de l'interface -> poignée Npcap ouverte
        self._feeds: dict[int, ClientFeed] = {}  # PID de la fenêtre -> son flux
        self._ports: dict[int, tuple[ClientFeed | None, float]] = {}  # port local -> (flux ou None, relu à)
        self._buffers: dict[int, bytes] = {}
        self._next_seq: dict[int, int] = {}  # port local -> numéro TCP attendu ensuite (fresh_part)
        self._refreshed = 0.0
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        # Journal réseau de la session (31/10, compte rendu : un raté doit pouvoir s'expliquer après coup) : chaque
        # message du serveur, par personnage, dans le dossier de la session ; jamais le chat (« cM »).
        self._journal: Path | None = None
        self._journal_files: dict[str, object] = {}
        self._journal_lock = threading.Lock()

    def keep_journal(self, folder) -> None:
        """Écrit désormais les messages du serveur dans `folder`/reseau_<personnage>.txt (heure, texte)."""
        self._journal = Path(folder)

    def _write_journal(self, feed: "ClientFeed", text: str) -> None:
        if self._journal is None or text.startswith("cM"):
            return
        name = "".join(c if c.isalnum() or c in "-_" else "_" for c in (feed.name or "connexion"))
        with self._journal_lock:
            handle = self._journal_files.get(name)
            if handle is None:
                try:
                    handle = self._journal_files[name] = open(self._journal / f"reseau_{name}.txt", "a",
                                                              encoding="utf-8", buffering=1)
                except OSError:
                    log.debug("Journal réseau impossible", exc_info=True)
                    self._journal = None
                    return
            handle.write(f"{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} {text}\n")

    def follow(self, hwnd: int, name: str) -> ClientFeed:
        """Flux de la fenêtre `hwnd` (personnage `name`). Nom vide : fenêtre suivie dès l'écran de connexion (pour les
        caractéristiques envoyées à la connexion) ; son flux revient au personnage qui la suit ensuite."""
        pid = window_pid(hwnd)
        with self._lock:
            feed = self._feeds.get(pid)
            if feed is None or feed.name not in ("", name):
                feed = self._feeds[pid] = ClientFeed(name)
            feed.name = feed.name or name
            if feed.my_id is None and name:
                # Numéro du personnage, le même à chaque session (31/10) : connu dès le lancement, même relancé en
                # plein combat (les messages du combat ne donnent que des numéros).
                feed.my_id = known_ids().get(name)
            self._ports = {}  # relu au prochain message
            return feed

    def start(self) -> "NetworkListener":
        for name, handle in list(self._handles.items()):
            self._spawn(name, handle)
        threading.Thread(target=self._watch, name="ecoute-reseau-veille", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1.0)
        with self._lock:
            handles, self._handles = list(self._handles.values()), {}
        for handle in handles:
            self._pcap.pcap_close(handle)

    def listening(self) -> int:
        """Nombre d'interfaces écoutées en ce moment."""
        with self._lock:
            return len(self._handles)

    def rescan(self) -> int:
        """Ouvre l'écoute des interfaces utilisables qui n'en ont pas (nouvelles, ou tombées en erreur) ; rend leur
        nombre."""
        opened = 0
        for name, ips in _interfaces(self._pcap):
            with self._lock:
                if name in self._handles or not _usable(ips):
                    continue
            handle = _open_handle(self._pcap, name)
            if handle is None:
                continue
            with self._lock:
                self._handles[name] = handle
            self._spawn(name, handle)
            opened += 1
        if opened:
            log.info("Écoute réseau rouverte sur %d interface(s) : le serveur est de nouveau entendu", opened)
        return opened

    def _spawn(self, name: bytes, handle) -> None:
        thread = threading.Thread(target=self._run, args=(name, handle), name="ecoute-reseau", daemon=True)
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def _watch(self) -> None:
        while not self._stop.wait(RESCAN_EVERY):
            try:
                self.rescan()
            except Exception:  # la veille ne doit jamais s'arrêter
                log.debug("Relecture des interfaces réseau impossible", exc_info=True)

    def _feed_of(self, port: int) -> ClientFeed | None:
        with self._lock:
            now = time.monotonic()
            known = self._ports.get(port)
            if known is not None and (known[0] is not None or now - known[1] < UNKNOWN_FOR):
                return known[0]
            if now - self._refreshed < REFRESH_EVERY:
                return None  # pas retenu : relu au prochain message
            self._refreshed = now
            connections, parents = game_connections(), parent_pids()
            self._ports = {local: (self._feeds.get(parents.get(pid, -1)) or self._feeds.get(pid), now)
                           for local, (pid, _) in connections.items()}
            self._ports.setdefault(port, (None, now))
            return self._ports[port][0]

    def _run(self, name: bytes, handle) -> None:
        pcap = self._pcap
        link = pcap.pcap_datalink(handle)
        header = ctypes.POINTER(_PktHdr)()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        while not self._stop.is_set():
            result = pcap.pcap_next_ex(handle, ctypes.byref(header), ctypes.byref(data))
            if result == 0:
                continue
            if result < 0:
                with self._lock:
                    mine = self._handles.get(name) == handle
                    if mine:
                        del self._handles[name]
                if mine:
                    pcap.pcap_close(handle)
                    log.warning("Écoute réseau interrompue (erreur Npcap %d) : lecture de l'écran, nouvel essai dans "
                                "%.0f s", result, RESCAN_EVERY)
                return
            try:
                parsed = segment(ctypes.string_at(data, header.contents.caplen), link)
                if parsed is None or not parsed[2]:
                    continue
                port, seq, chunk = parsed
                feed = self._feed_of(port)
                if feed is None:
                    continue
                chunk, self._next_seq[port] = fresh_part(self._next_seq.get(port), seq, chunk)
                if not chunk:
                    continue
                *messages, self._buffers[port] = (self._buffers.get(port, b"") + chunk).split(b"\x00")
                for message in messages:
                    text = message.decode("utf-8", errors="replace").strip()
                    if text:
                        feed.feed(text)
                        self._write_journal(feed, text)
            except Exception:  # un message inattendu ne doit jamais arrêter l'écoute
                log.debug("Message réseau ignoré", exc_info=True)


_shared: NetworkListener | None = None
_shared_tried = False


def current_listener() -> NetworkListener | None:
    """L'écoute du bot si elle est déjà démarrée (sans la démarrer : réglage reseau.ecoute respecté)."""
    return _shared


def shared_listener() -> NetworkListener | None:
    """L'écoute du bot, démarrée une fois pour toutes les fenêtres (None sans Npcap)."""
    global _shared, _shared_tried
    if not _shared_tried:
        _shared_tried = True
        _shared = start_listener()
    return _shared


def start_listener() -> NetworkListener | None:
    """Écoute du trafic du jeu sur chaque interface réseau IPv4 ; None sans Npcap (lecture de l'écran seule)."""
    try:
        pcap = _load_pcap()
    except OSError as error:
        log.info("Écoute réseau impossible (%s) : lecture de l'écran seule", error)
        return None
    if pcap is None:
        log.info("Npcap n'est pas installé : position lue à l'écran seulement (écoute réseau : voir "
                 "bot/connection/network.py)")
        return None
    handles = {}
    for name, ips in _interfaces(pcap):
        if _usable(ips):
            handle = _open_handle(pcap, name)
            if handle is not None:
                handles[name] = handle
    if handles:
        log.info("Écoute réseau du jeu active (%d interface(s)) : carte et cases annoncées par le serveur",
                 len(handles))
    else:  # réseau pas encore prêt (démarrage du PC, Wi-Fi) : la veille réessaie
        log.info("Écoute réseau : aucune interface utilisable pour l'instant, lecture de l'écran ; nouvel essai "
                 "toutes les %.0f s", RESCAN_EVERY)
    return NetworkListener(pcap, handles).start()
