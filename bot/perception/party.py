"""Groupe de l'équipe : inviter depuis la fenêtre « Amis », accepter l'invitation, replier le cadre du groupe.

Captures de l'utilisateur du 08/10 :
- icône « Amis » (deux personnages beiges sur rond orange) dans la barre d'icônes, en bas à droite ;
- fenêtre « Amis », onglet « Amis » : liste « Connectés » (nom du personnage, texte foncé, Verdana 18 px) ; clic (gauche)
  sur un nom : menu dont la dernière entrée est « Inviter dans mon groupe ». Chaque ligne commence par une croix
  orange qui RETIRE l'ami : on ne clique que sur le nom ; la fenêtre se ferme par Échap (sa croix est cachée par
  l'icône du cadre du groupe, 11/10) ;
- chez l'invité, fenêtre « Groupe » : « <chef> t'invite à rejoindre son groupe. Acceptes-tu ? », boutons « Oui »,
  « Non » ; chez celui qui invite, « Tu invites <nom>... » et « Annuler » ;
- cadre du groupe, en haut à droite de la carte : flèche ▶ quand il est déplié (portraits des membres), ◀ quand il est
  replié (07-08/10 : 0,95 ou plus sur la bonne flèche, 0,77 au plus sur l'autre, 0,58 sans groupe).
"""

import cv2

from bot.perception.frame import Frame
from bot.perception.layout import Box, Point
from bot.perception.templates import match_template
from bot.perception.text import find_text, render_text

FRIENDS_ICON, FRIENDS_TITLE = "groupe/amis_icone.png", "groupe/amis_titre.png"
INVITE_ENTRY = "groupe/inviter.png"
INVITATION, ACCEPT, CANCEL = "groupe/acceptes_tu.png", "groupe/oui.png", "groupe/annuler.png"
COLLAPSE, EXPAND = "groupe/reduire.png", "groupe/deplier.png"

TOOLBAR = (1400, 785, 1918, 890)  # barre d'icônes (repère de référence)
# Fenêtre « Amis », depuis le centre de son titre : liste des connectés.
FRIENDS_LIST = (-30, 235, 330, 645)
FRIEND_SIZE = 18
# 25/10 : « Walitcoin » 0,798 et « Waldarch-[Gal] » 0,788 dans la liste, pas trouvés au seuil de 0,8 de find_text ; un
# nom absent de la liste ne dépasse pas 0,73 sur les captures des tests.
FRIEND_THRESHOLD = 0.76
NAME_SPREAD = 0.35  # part de la demi-largeur du nom où cliquer (la croix « retirer » est à sa gauche)
MENU_BOX = (-60, -30, 420, 300)  # menu du clic droit, depuis le clic
INVITATION_TO_ACCEPT = (-260, 30, 0, 90)  # du centre de « Acceptes-tu ? » au bouton « Oui »
GROUP_ARROW = (1880, 60, 1918, 125)  # flèche du cadre du groupe (repère de référence)
ARROW_THRESHOLD = 0.85


def _offset(frame: Frame, origin: Point, box: tuple[float, float, float, float]) -> Box:
    length = frame.layout.length
    x0, y0, x1, y1 = (round(origin[i % 2] + length(v)) for i, v in enumerate(box))
    return max(0, x0), max(0, y0), min(frame.layout.width, x1), min(frame.layout.height, y1)


def find_friends_icon(frame: Frame) -> Point | None:
    found = match_template(frame, FRIENDS_ICON, box=frame.layout.box(*TOOLBAR), threshold=0.85)
    return found[0] if found else None


def find_friends_window(frame: Frame) -> Point | None:
    """Centre du titre de la fenêtre « Amis » ; None si elle est fermée."""
    found = match_template(frame, FRIENDS_TITLE, threshold=0.85)
    return found[0] if found else None


def find_friend(frame: Frame, title: Point, name: str) -> tuple[Point, tuple[float, float]] | None:
    """Où cliquer sur le nom `name` dans la liste des amis connectés, et la demi-étendue du clic ; None s'il n'y est
    pas (pas connecté, ou pas dans la liste d'amis)."""
    found = find_text(frame, name, _offset(frame, title, FRIENDS_LIST), FRIEND_SIZE, dark=True, threshold=FRIEND_THRESHOLD)
    if found is None:
        return None
    width = render_text(name, frame.layout.length(FRIEND_SIZE)).shape[1]
    return found[0], (width / 2 * NAME_SPREAD, frame.layout.length(3))


FRIEND_LEVEL = (320, 420)  # px de référence depuis le titre « Amis » : colonne « Niveau » de la liste des amis
LEVEL_ROW = 11  # px de référence au-dessus et au-dessous du nom
LEVEL_ZOOM = 4  # agrandi pour l'OCR de Windows (lu juste sur les 7 noms de la capture du 25/10)


def read_friend_level(frame: Frame, title: Point, name: str) -> int | None:
    """Niveau de `name` dans la liste des amis (colonne « Niveau », OCR de Windows) ; None s'il n'y est pas ou ne se
    lit pas. 25/10 : nouvelle équipe, niveaux jamais lus, groupes de niveau 75 laissés (limite par défaut 70) alors
    que l'équipe en cumule ~270."""
    from bot.perception.chat import ocr_available, ocr_lines  # OCR de Windows (chat.py)

    found = find_friend(frame, title, name)
    if found is None or not ocr_available():
        return None
    (_, y), _ = found
    length = frame.layout.length
    crop = frame.image[round(y - length(LEVEL_ROW)):round(y + length(LEVEL_ROW)),
                       round(title[0] + length(FRIEND_LEVEL[0])):round(title[0] + length(FRIEND_LEVEL[1]))]
    if crop.size == 0:
        return None
    lines = ocr_lines(cv2.resize(crop, None, fx=LEVEL_ZOOM, fy=LEVEL_ZOOM, interpolation=cv2.INTER_CUBIC)) or []
    digits = "".join(c for c in "".join(lines) if c.isdigit())
    return int(digits) if digits and 1 <= int(digits) <= 200 else None


def find_invite_entry(frame: Frame, click: Point) -> Point | None:
    """Entrée « Inviter dans mon groupe » du menu ouvert par le clic en `click`."""
    found = match_template(frame, INVITE_ENTRY, box=_offset(frame, click, MENU_BOX), threshold=0.85)
    return found[0] if found else None


def find_invitation(frame: Frame) -> Point | None:
    """Bouton « Oui » de l'invitation à rejoindre un groupe ; None s'il n'y a pas d'invitation."""
    question = match_template(frame, INVITATION, box=frame.layout.map_box, threshold=0.85)
    if question is None:
        return None
    found = match_template(frame, ACCEPT, box=_offset(frame, question[0], INVITATION_TO_ACCEPT), threshold=0.8)
    return found[0] if found else None


def find_pending_invite(frame: Frame) -> Point | None:
    """Bouton « Annuler » de l'invitation qu'on vient d'envoyer (en attente de réponse)."""
    found = match_template(frame, CANCEL, box=frame.layout.map_box, threshold=0.85)
    return found[0] if found else None


def group_arrow(frame: Frame) -> tuple[bool, Point] | None:
    """Cadre du groupe : (déplié ?, flèche) ; None sans groupe."""
    box = frame.layout.box(*GROUP_ARROW)
    collapse = match_template(frame, COLLAPSE, box=box, threshold=0)
    expand = match_template(frame, EXPAND, box=box, threshold=0)
    best = max((c for c in (collapse, expand) if c is not None), key=lambda c: c[1], default=None)
    if best is None or best[1] < ARROW_THRESHOLD:
        return None
    return best is collapse, best[0]
