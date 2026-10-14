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
from bot.perception.party import (ACCEPT, CANCEL, INVITE_ENTRY, find_friend, find_friends_icon, find_friends_window,
                                  find_invitation, find_invite_entry, find_pending_invite, group_arrow)
from bot.perception.templates import template_spread

log = logging.getLogger(__name__)

OPEN_WAIT = 4.0  # fenêtre « Amis » ouverte après le clic sur son icône
MENU_WAIT = 3.0  # menu du clic droit
INVITATION_WAIT = 8.0  # invitation reçue chez l'autre
JOINED_WAIT = 5.0  # cadre du groupe apparu chez l'autre après « Oui »
ARROW_SPREAD = (3, 4)  # flèche du cadre du groupe : petite


@dataclass(frozen=True)
class Player:
    name: str
    game: Game


def in_group(player: Player) -> bool:
    return group_arrow(player.game.look(park=False)) is not None


def open_friends(leader: Player):
    """Fenêtre « Amis » du principal ouverte : (capture, centre de son titre) ; None si elle ne s'ouvre pas."""
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
    member.game.click(find_invitation(invited), template_spread(invited, ACCEPT))
    if member.game.wait_until(lambda f: group_arrow(f) is not None, JOINED_WAIT) is None:
        log.warning("%s : « Oui » cliqué, mais pas de cadre du groupe", member.name)
        return False
    log.info("%s a rejoint le groupe de %s", member.name, leader.name)
    return True


def _cancel(leader: Player) -> None:
    frame = leader.game.look(park=False)
    cancel = find_pending_invite(frame)
    if cancel is not None:
        leader.game.click(cancel, template_spread(frame, CANCEL))
        leader.game.sleep(0.4)


def collapse(player: Player) -> None:
    """Cadre du groupe replié (flèche ▶ cliquée), s'il est déplié."""
    frame = player.game.look(park=False)
    arrow = group_arrow(frame)
    if arrow is None or not arrow[0]:
        return
    player.game.click(arrow[1], frame.layout.spread(*ARROW_SPREAD))
    if player.game.wait_until(lambda f: (a := group_arrow(f)) is not None and not a[0], 2.0) is None:
        log.warning("%s : le cadre du groupe ne se replie pas", player.name)


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
            missing.append(member.name)
    if invited:
        close_friends(leader)
    if collapse_frames:
        for player in [leader] + members:
            collapse(player)
    if missing:
        log.warning("Pas dans le groupe : %s (ils ne pourront pas rejoindre les combats du principal)",
                    ", ".join(missing))
    return missing
