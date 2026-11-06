"""Clés de donjon au début d'un farm de donjon (30/10, décision de l'utilisateur) : « l'optimisation des clés se fait
SEULEMENT au début d'un farm de donjon, et SEULEMENT si le mode est activé ; deux modes : répartition depuis les sacs
seulement, et sacs + banque ».

Réglage donjon.cles (config/settings.yaml, interface, --set donjon.cles=...) :
- non (défaut) : rien, comme avant (chacun entre avec la clé qu'il a sur lui) ;
- sac : les clés des sacs comptées d'après le serveur (inventaire suivi depuis la connexion, brain/inventory.py),
  passages possibles = total // taille de l'équipe (chacun donne SA clé au PNJ pour entrer), puis répartition par
  échanges entre nos personnages (team/trade.py, Trader.offer_item) pour que chacun en ait autant, le moins
  d'échanges possible (plan_keys) ; recompte d'après le serveur ;
- sac_banque : de même, mais s'il en manque pour les passages demandés, chaque personnage qui en a moins va à SON
  coffre (chacun a le sien) y reprendre ses clés, en un seul aller-retour de toute l'équipe, avant la répartition.
S'il en manque encore : le journal dit combien de passages sont possibles, et le farm en fait autant.

Inventaire inconnu (écoute du serveur arrêtée, ou pas suivie depuis la connexion : ASK manquant), ou clé du donjon
absente de config/donjons_cles.yaml : rien n'est réparti, et le farm s'arrête avant l'entrée (31/10, utilisateur :
« si tout le groupe n'a pas de clé il ne faut pas continuer et faire rentrer solo »).

La clé de chaque donjon vient de config/donjons_cles.yaml (nom exact du donjon -> numéro et nom exact de la clé), jamais
d'une ressemblance de noms (31/10 : « Donjon Bouftou » / « Clef du Donjon des Bouftous » pas reconnus, Fourboun entré
seul).

Le trajet donjon -> banque d'Astrub -> donjon : `trip` (go / back), le trajet d'équipe du mode « aller » (31/10,
team/dungeon.py BankTrip, zaaps compris) ; sans lui (donjon d'Incarnam, autre monde que la banque), le mode sac_banque
fait comme « sac » en le disant.
"""

import logging
import re

import yaml

from bot.alerts import tell_user
from bot.brain.inventory import plan_keys
from bot.config import ROOT
from bot.brain.travel import escape_menu
from bot.team.bank import DOOR, EXIT, BankVisit, take_from_chest
from bot.team.trade import Trader, free_pods

log = logging.getLogger(__name__)

KEY_MODES = ("non", "sac", "sac_banque")
KEYS_FILE = ROOT / "config" / "donjons_cles.yaml"


def key_of(dungeon: str, path=None) -> int | None:
    """Clé du donjon au nom exact `dungeon` (« Donjon Bouftou » -> 1568), d'après config/donjons_cles.yaml ; None
    s'il n'y est pas ou n'a pas de clé connue."""
    try:
        rows = yaml.safe_load((KEYS_FILE if path is None else path).read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError) as error:
        log.warning("Clés des donjons illisibles (%s)", error)
        return None
    for row in rows:
        if row.get("donjon") == dungeon:
            return row.get("cle") or None
    return None
NO_LIMIT = 10**6  # pods libres du receveur inconnus : une clé pèse peu, le jeu refuse de toute façon ce qui ne tient pas


def key_mode(settings: dict) -> str:
    """donjon.cles -> « non », « sac » ou « sac_banque » (« sac + banque », « Sac banque » acceptés) ; « non » si absent
    ou inconnu (le dit)."""
    value = (settings.get("donjon") or {}).get("cles", "non")
    if value is None or value is False:
        return "non"
    text = re.sub(r"[\s+_-]+", "_", str(value).strip().lower())
    if text not in KEY_MODES:
        log.warning("donjon.cles : « %s » inconnu (%s) : pas de répartition des clés", value, ", ".join(KEY_MODES))
        return "non"
    return text


def kept_key(settings: dict) -> int | None:
    """donjon.garder_cle : la clé reprise du coffre après « Transférer tous les objets » (team/bank.py) ; le donjon de
    config/donjons_entree.yaml (« incarnam »), son nom, ou le numéro de l'objet. None : vide ou inconnu."""
    value = (settings.get("donjon") or {}).get("garder_cle")
    if value is None or value is False or str(value).strip() == "":
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    from bot.team.dungeon import load_dungeons  # import local : dungeon.py importe ce module

    text = str(value).strip()
    spec = load_dungeons().get(text.lower())
    template = key_of(spec.name if spec is not None else text)
    if template is None:
        log.warning("donjon.garder_cle : clé de « %s » inconnue : aucune clé reprise du coffre", text)
    return template


def summary(held: dict[str, int]) -> str:
    return ", ".join(f"{name} {count}" for name, count in held.items())


class KeyChests:
    """Équipe sur la carte de la banque : ceux qui manquent de clés entrent, chacun reprend des clés de SON coffre
    (au plus ce qui manque à l'équipe : le surplus est réparti ensuite), puis ils ressortent."""

    def __init__(self, members, hide_key: str = "") -> None:
        self.members = members
        self.visit = BankVisit(members, hide_key)

    def fetch(self, short, template: int, missing: int) -> int:
        """Clés reprises en tout, d'après le serveur."""
        visit = self.visit
        entered = visit._doors(short, DOOR, inside=True)
        taken = 0
        for member in short:
            if taken >= missing:
                break
            if member.name not in entered:
                log.warning("Clés : %s pas entré dans la banque", member.name)
                continue
            try:
                if visit.open_chest(member):
                    taken += take_from_chest(member, template, missing - taken)
            except Exception:  # un personnage en échec ne bloque pas les autres
                log.exception("%s : erreur au coffre (clés)", member.name)
            finally:
                escape_menu(member.game)  # coffre ou dialogue fermés (la sortie ne se cliquerait pas)
        inside = [m for m in short if m.name in entered]
        left = visit._doors(inside, EXIT, inside=False)
        stuck = [m.name for m in inside if m.name not in left]
        if stuck:
            log.warning("Clés : %s pas ressorti(s) de la banque", ", ".join(stuck))
        return taken


class KeyManager:
    """Répartition des clés de donjon au début d'un farm (DungeonFarm.run)."""

    def __init__(self, members, settings: dict, hide_key: str = "", trip=None, trade=None, chests=None) -> None:
        self.members = members  # le principal d'abord
        self.mode = key_mode(settings)
        self.hide_key = hide_key
        # Aller-retour de toute l'équipe à la banque : objet avec go() -> bool (équipe sur la carte de la banque) et
        # back() -> bool (équipe revenue devant l'entrée du donjon). None : pas encore automatique.
        self.trip = trip
        self.trade = trade or self._trade  # (donneur, receveur, modèle, nombre) -> bool : échange conclu
        self.chests = chests or KeyChests(members, hide_key)  # .fetch(qui manque, modèle, manquantes) -> reprises
        self.portraits: dict = {}  # portraits du cadre du groupe retenus d'un échange à l'autre (Trader)
        self.blocked = False  # un échange resté ouvert malgré l'annulation (31/10) : plus aucun échange ensuite
        self.refill = False  # clés refaites ensuite par le farm de donjon (donjon.cles_manquantes) : manque pas signalé

    @property
    def enabled(self) -> bool:
        return self.mode != "non"

    def prepare(self, dungeon: str, wanted: int, template: int | None = None) -> int | None:
        """Passages possibles après répartition (le moins de clés porté par un personnage, d'après le serveur) ; None :
        mode « non », clé du donjon inconnue ou inventaire inconnu (rien de fait, comme avant). `wanted` : passages
        demandés ; `template` : la clé (défaut : celle du donjon nommé `dungeon`, config/donjons_cles.yaml)."""
        if not self.enabled:
            return None
        template = key_of(dungeon) if template is None else template
        if template is None:
            log.warning("Clés de %s : pas de clé pour ce nom exact dans config/donjons_cles.yaml : pas de répartition",
                        dungeon)
            return None
        held = self.count(template)
        if held is None:
            log.warning("Clés de %s : inventaire inconnu (écoute du serveur pas suivie depuis la connexion) : pas de "
                        "répartition, chacun entre avec ce qu'il a", dungeon)
            return None
        size = len(self.members)
        log.info("Clés de %s : %s (%d pour %d personnages) -> %d passage(s) possible(s), %d demandé(s)", dungeon,
                 summary(held), sum(held.values()), size, sum(held.values()) // size, wanted)
        plan = plan_keys(held, wanted)
        if plan.missing and self.mode == "sac_banque":
            fetched = self._from_chests(template, held, wanted, plan.missing)
            if fetched is None:
                return 0  # équipe pas revenue devant le donjon : pas de farm
            if fetched:
                recount = self.count(template)
                held = recount if recount is not None else held
                log.info("Clés de %s après le coffre : %s -> %d passage(s) possible(s)", dungeon, summary(held),
                         sum(held.values()) // size)
                plan = plan_keys(held, wanted)
        if plan.each <= 0:
            log.warning("Clés de %s : %d pour %d personnages, pas une chacun : aucun passage possible", dungeon,
                        sum(held.values()), size)
            if not self.refill:
                tell_user(f"{dungeon} : pas assez de clés ({sum(held.values())} pour {size} personnages : "
                          f"{summary(held)}). Il en faut une par personnage et par passage : farm arrêté, rien n'est "
                          f"entré.")
            return 0
        if plan.moves:
            log.info("Clés de %s : échanges : %s", dungeon,
                     " ; ".join(f"{giver} -> {taker} {count}" for giver, taker, count in plan.moves))
        by_name = {m.name: m for m in self.members}
        failed = [(giver, taker, count) for giver, taker, count in plan.moves
                  if self.blocked or not self.trade(by_name[giver], by_name[taker], template, count)]
        if failed and not self.blocked:
            # 06/11 (journal 200051) : 2 échanges sur 7 annulés (une clé de trop posée), 0 passage pour 14 clés
            # réparties : chaque échange raté retenté une fois, les autres faits.
            log.info("Clés de %s : %d échange(s) raté(s), retentés une fois", dungeon, len(failed))
            failed = [(giver, taker, count) for giver, taker, count in failed
                      if self.blocked or not self.trade(by_name[giver], by_name[taker], template, count)]
        for giver, taker, count in failed:
            log.warning("Clés de %s : échange %s -> %s (%d) pas fait", dungeon, giver, taker, count)
        final = self.count(template)
        if final is None:
            log.warning("Clés de %s : recompte impossible (écoute du serveur arrêtée) : %d passage(s) d'après le plan",
                        dungeon, plan.each if not failed else 0)
            return plan.each if not failed else 0
        passes = min(final.values()) if final else 0
        log.info("Clés de %s après répartition : %s -> %d passage(s)%s", dungeon, summary(final), passes,
                 f" ({len(failed)} échange(s) raté(s))" if failed else "")
        return passes

    def count(self, template: int) -> dict[str, int] | None:
        """Clés portées par chacun d'après le serveur ; None si l'inventaire d'un seul est inconnu."""
        held = {}
        for member in self.members:
            net = getattr(member.game, "net", None)
            # Sac connu (connexion entendue, ou repris du fichier écrit à la connexion) : pas besoin que le serveur ait
            # déjà parlé à ce lancement (31/10 : 1 s après le lancement, aucun flux encore « vivant », rien réparti).
            count = net.held(template) if net is not None else None
            if count is None:
                log.info("Clés : inventaire de %s inconnu", member.name)
                return None
            held[member.name] = count
        return held

    def _from_chests(self, template: int, held: dict[str, int], wanted: int, missing: int) -> int | None:
        """Ceux qui ont moins de `wanted` clés vont à leur coffre (un aller-retour pour toute l'équipe) ; clés reprises,
        ou None si l'équipe n'est pas revenue devant le donjon."""
        short = [m for m in self.members if held[m.name] < wanted]
        if self.trip is None:
            log.warning("Clés : il en manque %d ; aller au coffre demande un trajet jusqu'à la banque d'Astrub qui n'est "
                        "pas encore automatique depuis le donjon : je fais avec les clés des sacs", missing)
            return 0
        log.info("Clés : il en manque %d : %s vont à leur coffre", missing, ", ".join(m.name for m in short))
        if not self.trip.go():
            log.warning("Clés : équipe pas arrivée à la banque : je fais avec les clés des sacs")
            return 0 if self.trip.back() else None
        taken = 0
        try:
            taken = self.chests.fetch(short, template, missing)
            log.info("Clés : %d reprise(s) des coffres", taken)
        finally:
            back = self.trip.back()
        if not back:
            log.warning("Clés : l'équipe n'est pas revenue devant le donjon : pas de farm")
            return None
        return taken

    def _trade(self, giver, taker, template: int, count: int) -> bool:
        """Un échange vérifié : ouvert, `count` clés posées (vues par le receveur), conclu ; annulé au moindre doute."""
        trader = Trader(giver, taker, self.portraits, self.members)
        if not trader.open():
            return False
        try:
            if not trader.offer_item(template, count):
                self.blocked = trader.cancel() is False  # fenêtre restée ouverte : les échanges suivants s'arrêtent
                return False
            free = free_pods(taker.game.net)
            return trader.conclude(free if free is not None else NO_LIMIT)
        except Exception:
            log.exception("Échange %s -> %s : erreur, annulé", giver.name, taker.name)
            self.blocked = trader.cancel() is False
            return False
