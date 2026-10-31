"""Reformer le groupe de l'équipe après une connexion (déroulé et captures de l'utilisateur du 08/10).

Sans groupe, l'épée du combat du principal est « limitée au groupe » : les autres ne peuvent pas le rejoindre. Le
principal ouvre sa fenêtre « Amis », clic (gauche, 11/10 : le clic droit n'ouvre pas le menu) sur le nom de l'autre
dans la liste des connectés, « Inviter dans mon groupe » ; l'autre clique « Oui ». Un personnage qui a déjà le cadre du groupe (en haut à droite) n'est pas invité.
Enfin, le cadre du groupe est replié (flèche), dans chaque fenêtre : il cache moins la carte.

Les personnages de l'équipe doivent être amis du principal (une fois pour toutes, à la main : « Ajouter un ami »).
"""

import logging
from dataclasses import dataclass

from bot.brain.game import Game
from bot.brain.popups import still_open
from bot.perception.frame import Frame
from bot.perception.layout import Point
from bot.perception.party import (ACCEPT, CANCEL, INVITE_ENTRY, find_friend, find_friends_icon, find_friends_window,
                                  find_invitation, find_invite_entry, find_pending_invite, group_arrow,
                                  read_friend_level)
from bot.perception.popups import find_known_popup, load_popup_specs
from bot.perception.templates import template_spread

log = logging.getLogger(__name__)

OPEN_WAIT = 4.0  # fenêtre « Amis » ouverte après le clic sur son icône
MENU_WAIT = 3.0  # menu du clic droit
INVITATION_WAIT = 8.0  # invitation reçue chez l'autre
JOINED_WAIT = 5.0  # cadre du groupe apparu chez l'autre après « Oui »
ARROW_SPREAD = (3, 4)  # flèche du cadre du groupe : petite
# Fenêtre « Groupe » de l'invitation vue chargée avant de cliquer « Oui » : bouton revu au même endroit, STILL_LAG s plus
# tard, à STILL_TOLERANCE px de référence près.
STILL_LAG = 0.15
STILL_TOLERANCE = 3
ACCEPT_TRIES = 2  # clics sur « Oui » au plus, si l'invitation est encore là après le premier
CANCEL_WAIT = 2.0  # « Annuler » cliqué -> fenêtre « Tu invites... » partie


@dataclass(frozen=True)
class Player:
    name: str
    game: Game


def in_group(player: Player) -> bool:
    return group_arrow(player.game.look(park=False)) is not None


def open_friends(leader: Player):
    """Fenêtre « Amis » du principal ouverte : (capture, centre de son titre) ; None si elle ne s'ouvre pas."""
    frame = leader.game.look(park=False)
    # 18/10 (journal 112233) : « Résultat du combat » resté ouvert chez le principal (bot arrêté juste après un combat),
    # par-dessus l'icône « Amis » : fenêtre jamais ouverte, Dinarpepe hors du groupe. Fenêtre connue fermée d'abord.
    specs = load_popup_specs()
    known = find_known_popup(frame, specs)
    if known is not None and known.close_point is not None:
        # Revue immobile avant le clic (25/10 : un « Fermer » vu sur une fenêtre en train de s'ouvrir était un objet de
        # la barre de raccourcis) : sinon, pas de clic, la fenêtre reste au tour des fenêtres.
        seen = still_open(leader.game, known, specs, frame)
        if seen is None:
            log.info("%s : fenêtre « %s » pas encore immobile, pas de clic avant d'ouvrir « Amis »", leader.name,
                     known.spec.name)
        else:
            log.info("%s : fenêtre « %s » fermée avant d'ouvrir « Amis »", leader.name, known.spec.name)
            leader.game.click(seen.close_point, seen.close_spread)
            leader.game.sleep(0.5)
        frame = leader.game.look(park=False)
    title = find_friends_window(frame)
    if title is None:
        icon = find_friends_icon(frame)
        if icon is None:
            log.warning("%s : icône « Amis » introuvable dans la barre d'icônes", leader.name)
            return None
        leader.game.click(icon, frame.layout.spread(12, 12))
        frame = leader.game.wait_until(lambda f: find_friends_window(f) is not None, OPEN_WAIT)
        if frame is None:
            log.warning("%s : la fenêtre « Amis » ne s'ouvre pas", leader.name)
            return None
        title = find_friends_window(frame)
    return frame, title


def close_friends(leader: Player) -> None:
    """Fenêtre « Amis » fermée par Échap (11/10 : sa croix est cachée par l'icône du cadre du groupe ; cliquer à sa
    place toucherait le groupe)."""
    if find_friends_window(leader.game.look(park=False)) is None:
        return
    leader.game.press("escape")
    if leader.game.wait_until(lambda f: find_friends_window(f) is None, 2.0) is None:
        log.warning("%s : la fenêtre « Amis » ne se ferme pas avec Échap, je la laisse ouverte", leader.name)


def invite(leader: Player, member: Player) -> bool:
    """Le principal invite `member`, qui accepte ; True s'il a rejoint le groupe."""
    opened = open_friends(leader)
    if opened is None:
        return False
    frame, title = opened
    if find_pending_invite(frame) is not None:
        # 25/10 (journal 201109, 20:12:09) : « Oui » d'Enu-six-[Gal] pas pris, sa fenêtre « Tu invites... / Annuler »
        # restée ouverte chez Fourboun ; le clic suivant, sur Elseiso, n'a ouvert aucun menu (« pas d'entrée « Inviter
        # dans mon groupe » »). L'invitation en attente est annulée d'abord.
        log.info("%s : une invitation est encore en attente, je l'annule avant d'inviter %s", leader.name, member.name)
        _cancel(leader, frame)
        frame = leader.game.look(park=False)
        title = find_friends_window(frame)
        if title is None:
            opened = open_friends(leader)
            if opened is None:
                return False
            frame, title = opened
    found = find_friend(frame, title, member.name)
    if found is None:
        path = leader.game.recorder.save(frame.image, "amis_introuvable", lossless=True)
        log.warning("%s n'est pas dans la liste des amis connectés de %s (capture : %s) : ajoute-le en ami une fois "
                    "(fenêtre « Amis », « Ajouter un ami »)", member.name, leader.name, path)
        return False
    point, spread = found
    log.info("%s invite %s dans son groupe", leader.name, member.name)
    leader.game.click(point, spread)
    menu = leader.game.wait_until(lambda f: find_invite_entry(f, point) is not None, MENU_WAIT)
    if menu is None:
        log.warning("%s : pas d'entrée « Inviter dans mon groupe » après le clic sur %s", leader.name, member.name)
        leader.game.press("escape")
        return False
    leader.game.click(find_invite_entry(menu, point), template_spread(menu, INVITE_ENTRY))
    invited = member.game.wait_until(lambda f: find_invitation(f) is not None, INVITATION_WAIT)
    if invited is None:
        log.warning("%s n'a pas reçu l'invitation de %s", member.name, leader.name)
        _cancel(leader)
        return False
    if accept(member):
        log.info("%s a rejoint le groupe de %s", member.name, leader.name)
        return True
    _cancel(leader)  # sa fenêtre « Tu invites... » bloquerait l'invitation suivante (20:12, journal 201109)
    return False


def accept(member: Player) -> bool:
    """« Oui » cliqué dans la fenêtre « Groupe » de l'invitation, vue chargée (bouton immobile sur deux captures) ;
    recliqué une fois si l'invitation est encore là sans cadre du groupe. True si le cadre du groupe est apparu.

    25/10 (journal 201109, 20:12:04) : « Oui » d'Enu-six-[Gal] cliqué 0,6 s après l'invitation, pas pris (pas de cadre du
    groupe en 5 s) ; réinvité 11 s plus tard, il a rejoint du premier coup."""
    for attempt in range(1, ACCEPT_TRIES + 1):
        seen = _stable_invitation(member)
        if seen is None:
            if in_group(member):
                return True
            if attempt < ACCEPT_TRIES:
                continue  # fenêtre « Groupe » encore en train de s'ouvrir : revue
            log.warning("%s : invitation plus visible (ou pas encore immobile), pas de clic sur « Oui »", member.name)
            return False
        point, frame = seen
        member.game.click(point, template_spread(frame, ACCEPT))
        if member.game.wait_until(lambda f: group_arrow(f) is not None, JOINED_WAIT) is not None:
            return True
        if attempt < ACCEPT_TRIES and find_invitation(member.game.look(park=False)) is not None:
            log.info("%s : « Oui » pas pris, l'invitation est encore là : je reclique", member.name)
            continue
        break
    log.warning("%s : « Oui » cliqué, mais pas de cadre du groupe", member.name)
    return False


def _stable_invitation(member: Player) -> tuple[Point, Frame] | None:
    """Bouton « Oui » de l'invitation, vu au même endroit sur deux captures à STILL_LAG s d'écart (fenêtre « Groupe »
    chargée), et la seconde capture ; None sinon."""
    first = member.game.look(park=False)
    point = find_invitation(first)
    if point is None:
        return None
    member.game.sleep(STILL_LAG)
    second = member.game.look(park=False)
    again = find_invitation(second)
    if again is None:
        return None
    before, after = first.layout.to_reference(point), second.layout.to_reference(again)
    if max(abs(a - b) for a, b in zip(before, after)) > STILL_TOLERANCE:
        return None
    return again, second


def _cancel(leader: Player, frame=None) -> None:
    """« Annuler » de l'invitation en attente chez le principal (fenêtre « Tu invites... »), s'il est là."""
    frame = frame if frame is not None else leader.game.look(park=False)
    cancel = find_pending_invite(frame)
    if cancel is not None:
        leader.game.click(cancel, template_spread(frame, CANCEL))
        if leader.game.wait_until(lambda f: find_pending_invite(f) is None, CANCEL_WAIT) is None:
            log.warning("%s : l'invitation en attente ne s'annule pas", leader.name)


def collapse(player: Player) -> None:
    """Cadre du groupe replié (flèche ▶ cliquée), s'il est déplié."""
    frame = player.game.look(park=False)
    arrow = group_arrow(frame)
    if arrow is None or not arrow[0]:
        return
    player.game.click(arrow[1], frame.layout.spread(*ARROW_SPREAD))
    if player.game.wait_until(lambda f: (a := group_arrow(f)) is not None and not a[0], 2.0) is None:
        log.warning("%s : le cadre du groupe ne se replie pas", player.name)


def friend_levels(leader: Player, names: list[str]) -> dict[str, int]:
    """Niveaux de `names` lus dans la liste des amis du principal (fenêtre « Amis » ouverte puis refermée) ; ceux
    qui ne s'y lisent pas manquent."""
    opened = open_friends(leader)
    if opened is None:
        return {}
    frame, title = opened
    levels = {name: level for name in names if (level := read_friend_level(frame, title, name)) is not None}
    close_friends(leader)
    return levels


def form_group(leader: Player, members: list[Player], collapse_frames: bool = True) -> list[str]:
    """Invite chaque membre qui n'a pas de groupe ; renvoie les noms de ceux qui n'ont pas pu rejoindre."""
    missing = []
    invited = False
    for member in members:
        if in_group(member):
            log.info("%s est déjà dans un groupe", member.name)
            continue
        invited = True
        if not invite(leader, member):
            missing.append(member)
    # 18/10 (journal 110414) : Dinarpepe, le premier invité juste après la connexion, n'avait pas reçu l'invitation ; pas
    # de nouvel essai, hors du groupe toute la session (combats réservés au groupe : épée grisée, jamais rejoints). Un
    # second passage, après les autres (le temps pour sa fenêtre de finir de charger).
    for member in list(missing):
        log.info("Nouvel essai : %s invité de nouveau dans le groupe", member.name)
        if in_group(member) or invite(leader, member):
            missing.remove(member)
    missing = [member.name for member in missing]
    if invited:
        close_friends(leader)
    if collapse_frames:
        for player in [leader] + members:
            collapse(player)
    if missing:
        log.warning("Pas dans le groupe : %s (ils ne pourront pas rejoindre les combats du principal)",
                    ", ".join(missing))
    return missing
