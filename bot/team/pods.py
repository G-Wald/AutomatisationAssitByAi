"""Pods pendant un farm de donjon (31/10, utilisateur : « le farm donjon doit aussi avoir la surveillance des pods, mais
il ne doit déclencher l'aller que lorsque les personnages sont dehors, et voir si un personnage est trop en pods pour
répartir sur les autres pour qu'il se déplace sans le malus »).

Avant chaque passage, équipe dehors devant le PNJ (jamais dans les salles) :
1. pods de chacun d'après le serveur (« Ow ») ;
2. surcharge (pods au-dessus du maximum : le personnage ne peut plus marcher) : il donne des ressources aux autres par
   échanges (team/trade.py, Trader : offre limitée aux pods libres du receveur, vue par le serveur), jusqu'à repasser
   sous son maximum ; s'il n'y arrive pas, personne ne part ;
3. quelqu'un au seuil de la banque (team.pods_banque) : toute l'équipe va à la banque d'Astrub par le trajet d'équipe
   du mode « aller » (zaaps compris), y dépose selon team.banque_mode, puis revient devant le PNJ du donjon.
"""

import logging
import math
import time
from dataclasses import replace

from bot.team.trade import Trader, free_pods

log = logging.getLogger(__name__)

OVERLOAD_MARGIN = 0.02  # part des pods max gardée libre après un rééquilibrage (le butin du prochain combat)
TRIP_TIMEOUT = 900.0  # s : trajet d'équipe abandonné au-delà (zaap compris : ~4 min le 30/10)
GIVE_ROUNDS = 3  # lots donnés au plus à chaque receveur
GIVE_SAFETY = 10  # pods : l'offre s'arrête sous la place du receveur (31/10 : 152 posés pour 150 libres, annulé)
POD_UPDATE_WAIT = 3.0  # s : pods du donneur annoncés après l'échange
GIVE_EXTRA = 30  # pods donnés en plus du nécessaire (pas tout ce que le receveur peut porter : 567 pour 49 le 31/10)


def load(net) -> tuple[int, int] | None:
    """(pods portés, pods max) d'après le serveur ; None s'ils ne sont pas connus."""
    return tuple(net.pods) if net is not None and getattr(net, "pods", None) else None


def overload(net) -> int:
    """Pods à donner pour repasser sous le maximum, avec la marge OVERLOAD_MARGIN ; 0 sans surcharge ou pods inconnus."""
    pods = load(net)
    if pods is None:
        return 0
    used, most = pods
    return max(0, used - most + math.ceil(most * OVERLOAD_MARGIN)) if used > most else 0


def rebalance(members, portraits: dict | None = None) -> list[str]:
    """Chaque personnage en surcharge donne des ressources aux autres (les plus libres d'abord) ; noms de ceux qui
    restent en surcharge (ils ne peuvent pas marcher : personne ne part)."""
    portraits = {} if portraits is None else portraits
    stuck = []
    for heavy in members:
        need = overload(heavy.game.net)
        if need <= 0:
            continue
        log.warning("Pods de %s : %s, au-dessus du maximum (il ne peut plus marcher) : %d pods à donner aux autres",
                    heavy.name, "/".join(map(str, load(heavy.game.net))), need)
        others = sorted((m for m in members if m is not heavy), key=lambda m: free_pods(m.game.net) or 0, reverse=True)
        for taker in others:
            for _ in range(GIVE_ROUNDS):
                need = overload(heavy.game.net)
                free = free_pods(taker.game.net) or 0
                if need <= 0 or free <= 0:
                    break
                given = _give(heavy, taker, min(free, need + GIVE_EXTRA), free, portraits, members)
                if given is None:  # échange resté ouvert malgré l'annulation : plus aucun échange (31/10)
                    return [m.name for m in members if overload(m.game.net) > 0]
                if not given:
                    break
            if overload(heavy.game.net) <= 0:
                break
        if overload(heavy.game.net) > 0:
            stuck.append(heavy.name)
        else:
            log.info("Pods de %s : %s après le rééquilibrage", heavy.name, "/".join(map(str, load(heavy.game.net))))
    return stuck


def _give(giver, taker, amount: int, free: int, portraits: dict, members) -> bool | None:
    """Un lot d'environ `amount` pods de ressources de `giver` à `taker` (`free` : ce que le receveur peut porter) ;
    True s'il a été reçu (d'après le serveur) ; None si l'échange est resté ouvert malgré l'annulation."""
    trader = Trader(giver, taker, portraits, members)
    if not trader.open():
        return False
    started = time.monotonic()
    weight = trader.offer(max(1, min(amount, free - GIVE_SAFETY)))
    if weight <= 0:
        if trader.cancel() is False:
            return None
        log.info("Pods : %s n'a plus de ressource à donner à %s", giver.name, taker.name)
        return False
    if not trader.conclude(free):
        return False
    log.info("Pods : %s a donné %d pods de ressources à %s", giver.name, weight, taker.name)
    # Pods du donneur à jour (« Ow ») avant de recompter ce qui reste à donner (31/10 : un second lot inutile tenté
    # sur les pods d'avant l'échange).
    wait_for = getattr(giver.game.net, "wait_for", None)
    if wait_for is not None:
        wait_for("pods_at", started, POD_UPDATE_WAIT)
    return True


def fullest(members) -> tuple[str, float] | None:
    """Le personnage le plus chargé et son remplissage (0 à 1) d'après le serveur ; None si personne n'est connu."""
    known = [(m.name, used / most) for m in members if (pods := load(m.game.net)) and (used := pods[0]) is not None
             and (most := pods[1])]
    return max(known, key=lambda item: item[1]) if known else None


def team_trip(bot, goal: tuple[int, int], why: str) -> bool:
    """Toute l'équipe jusqu'à la carte `goal`, comme le mode « aller » (le principal mène, les suiveurs suivent, zaaps
    compris), sans combat ; le farm reprend la main ensuite. True si toute l'équipe y est."""
    from bot.brain.travel import make_roamer

    saved = bot.roamer, bot.engager, bot.travel_settings, bot.finished
    settings = replace(bot.travel_settings, mode="aller", destination=goal, start=None)
    log.info("%s : trajet de l'équipe jusqu'à %s,%s", why, *goal)
    _leave_rooms(bot)
    roamer = make_roamer(bot.leader.game, settings)
    if roamer is not None and hasattr(bot, "_dispatch"):
        # 05/11 (journal 192048) : nouveau voyageur sans ces branchements, les suiveurs ne cliquaient plus les soleils ;
        # le principal est arrivé seul à la banque, « pas entré dans la banque » pour les 7 autres (restés en route).
        roamer.traveler.on_crossed = bot._crossed
        roamer.traveler.on_click = bot._dispatch
    bot.travel_settings, bot.roamer, bot.engager, bot.finished = settings, roamer, None, False
    step = getattr(bot, "safe_step", bot.step)
    end = time.monotonic() + TRIP_TIMEOUT
    try:
        while not bot.finished and time.monotonic() < end:
            step()
        arrived = getattr(bot.roamer.traveler, "coords", None) == goal and not bot._lost
    finally:
        bot.roamer, bot.engager, bot.travel_settings, bot.finished = saved
    if not arrived:
        log.warning("%s : l'équipe n'est pas toute arrivée en %s,%s", why, *goal)
    return arrived


def _leave_rooms(bot) -> None:
    """Avant un trajet : chacun sort de la salle intérieure où il est, par le soleil au sol (08/11, validation des
    Blops : « Sortir du donjon » ramène devant Bibiblop, salle 11878 aux coordonnées de dehors ; banque demandée par les
    pods de Walarbalete, trajet arrêté : « aucun chemin de -25,-17 »). Rien dehors ni sans carte annoncée."""
    from bot.brain.building import leave_room

    hide = getattr(getattr(bot, "travel_settings", None), "hide_players_key", "e")
    for member in getattr(bot, "members", []):
        net = getattr(getattr(member, "game", None), "net", None)
        if net is not None and hasattr(net, "map_id"):
            leave_room(member.game, net, hide, member.name)


def team_zone_farm(bot, zone: tuple[int, int], enough, seconds: float, why: str) -> bool:
    """Toute l'équipe en farm dans la sous-zone de la carte `zone`, comme le mode zone (--combat-zone : groupes
    attaqués, carte suivante quand il n'y en a plus, banque quand les pods sont pleins), jusqu'à `enough()` vrai entre
    deux combats, ou `seconds` écoulées ; le farm d'avant reprend la main ensuite. True si `enough()` a été atteint."""
    from bot.brain.travel import make_roamer

    if bot.engager is None:
        log.warning("%s : pas de lancement de combat (combats désactivés) : pas de farm", why)
        return False
    joiner = getattr(bot, "joiner", None)
    saved = bot.roamer, bot.travel_settings, bot.finished, getattr(joiner, "fight_coords", None)
    settings = replace(bot.travel_settings, mode="zone", start=zone, destination=None)
    log.info("%s : farm de l'équipe dans la zone de %s,%s (%.0f min au plus)", why, *zone, seconds / 60)
    roamer = make_roamer(bot.leader.game, settings)
    if roamer is None:
        return False
    if hasattr(bot, "_dispatch"):  # comme team_trip : sans eux, les suiveurs ne cliquent pas les soleils
        roamer.traveler.on_crossed = bot._crossed
        roamer.traveler.on_click = bot._dispatch
    bot.travel_settings, bot.roamer, bot.finished = settings, roamer, False
    if joiner is not None:
        joiner.fight_coords = lambda: roamer.traveler.coords  # carte du combat quand son nom ne se lit pas
    step = getattr(bot, "safe_step", bot.step)
    end = time.monotonic() + seconds
    reached = False
    try:
        while not bot.finished:
            step()
            pending = getattr(bot, "fight_pending", None)
            if bot._round_due or (callable(pending) and pending()):
                # 06/11 (journal 194826) : attaque lancée au dernier tour, farm arrêté 2 s après (durée écoulée),
                # Fourboun seul en combat sans personne pour jouer. Combat fini et fenêtres fermées d'abord.
                continue
            if enough():
                reached = True
                break
            if time.monotonic() >= end:
                break
    finally:
        stopped = bot.finished
        bot.roamer, bot.travel_settings, bot.finished = saved[:3]
        bot.finished = bot.finished or (stopped and bool(getattr(bot, "_stop_reason", None)))
        if joiner is not None:
            joiner.fight_coords = saved[3]
    log.info("%s : %s", why, "atteint" if reached else "pas atteint (durée écoulée ou arrêt)")
    return reached
