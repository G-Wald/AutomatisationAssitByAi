"""Écrans du jeu avant d'être en jeu : accueil, choix du serveur, choix du personnage (captures de l'utilisateur du
08/10).

- Accueil (après une déconnexion, ou au démarrage du jeu) : bouton orange « Se connecter ».
- Fenêtre « Connexion » par-dessus l'accueil après une déconnexion (« Connexion interrompue avec le serveur. Tu es
  resté trop longtemps inactif. »), bouton « Ok ».
- « Choisis un serveur » : une carte par serveur où le compte a des personnages (nom sous l'illustration), puis le
  bouton « Sélectionner ».
- « Choisis ton personnage » : 5 emplacements (le plus à gauche : le dernier joué), le nom sur une plaque sous chaque
  personnage, puis le bouton « Jouer ». Sur la plaque, à droite du niveau, une croix SUPPRIME le personnage : on clique
  toujours sur le personnage lui-même, jamais sur sa plaque.
- Compte non abonné : « Mode découverte » en rouge à droite du titre, à la place de « Temps d'abonnement restant ».
Tout est placé par rapport au titre de l'écran (trouvé par son modèle) : mesures sur les captures, échelle 1.
"""

from dataclasses import dataclass
from enum import Enum

from bot.perception.frame import Frame
from bot.perception.layout import Box, Point
from bot.perception.templates import match_template
from bot.perception.text import find_text, text_score

SERVER_TITLE, SELECT_BUTTON = "connexion/choix_serveur.png", "connexion/selectionner.png"
CHARACTER_TITLE, PLAY_BUTTON = "connexion/choix_personnage.png", "connexion/jouer.png"
WELCOME_BUTTON = "connexion/se_connecter.png"
MESSAGE_TITLE, MESSAGE_OK, INACTIVE = "connexion/connexion_titre.png", "connexion/ok.png", "connexion/inactif.png"
MESSAGE_BOX = (-60, 30, 560, 230)  # depuis le centre du titre « Connexion » : le texte et le bouton « Ok »
DISCOVERY = "connexion/mode_decouverte.png"
DISCOVERY_BOX = (400, -25, 1100, 25)  # depuis le centre du titre : bande à sa droite

# Choix du serveur, depuis le centre du titre : ligne des noms de serveur (gras, 18 px) ; du nom au milieu de
# l'illustration de sa carte (où cliquer) ; demi-étendue du clic.
SERVER_NAMES = (-160, 298, 1010, 340)
SERVER_SIZE = 18
NAME_TO_CARD, CARD_SPREAD = (0, -140), (45, 60)

# Choix du personnage, depuis le centre du titre : milieu des 5 emplacements, nom sur la plaque (gras, 21,75 px),
# personnage (où cliquer : son corps, loin de la croix de la plaque) ; demi-étendue du clic.
SLOTS_DX = (-40, 192, 425, 659, 893)
PLATE_DY, PLATE_HALF = 327, (105, 20)
CHARACTER_SIZE = 21.75
BODY_DY, BODY_SPREAD = 150, (18, 25)
NAME_THRESHOLD = 0.8  # 0,97 sur sa plaque, 0,72 au plus sur une autre (08/10)


class LoginStep(Enum):
    MESSAGE = "fenêtre « Connexion »"
    WELCOME = "accueil"
    SERVERS = "choix du serveur"
    CHARACTERS = "choix du personnage"
    OTHER = "écran inconnu"  # chargement...


@dataclass(frozen=True)
class LoginView:
    step: LoginStep
    title: Point | None = None  # centre du titre de l'écran (accueil : du bouton « Se connecter »)
    button: Point | None = None  # « Se connecter », « Sélectionner » ou « Jouer »
    discovery: bool = False  # compte non abonné (« Mode découverte »)
    inactive: bool = False  # fenêtre « Connexion » : déconnecté pour inactivité


def read_login(frame: Frame) -> LoginView:
    for step, title, button in ((LoginStep.CHARACTERS, CHARACTER_TITLE, PLAY_BUTTON),
                                (LoginStep.SERVERS, SERVER_TITLE, SELECT_BUTTON)):
        found = match_template(frame, title, threshold=0.85)
        if found is not None:
            pressed = match_template(frame, button, threshold=0.8)
            discovery = match_template(frame, DISCOVERY, box=_relative(frame, found[0], DISCOVERY_BOX), threshold=0.8)
            return LoginView(step, found[0], pressed[0] if pressed else None, discovery is not None)
    message = match_template(frame, MESSAGE_TITLE, threshold=0.85)
    if message is not None:
        box = _relative(frame, message[0], MESSAGE_BOX)
        ok = match_template(frame, MESSAGE_OK, box=box, threshold=0.8)
        inactive = match_template(frame, INACTIVE, box=box, threshold=0.8) is not None
        return LoginView(LoginStep.MESSAGE, message[0], ok[0] if ok else None, inactive=inactive)
    welcome = match_template(frame, WELCOME_BUTTON, threshold=0.85)
    if welcome is not None:
        return LoginView(LoginStep.WELCOME, welcome[0], welcome[0])
    return LoginView(LoginStep.OTHER)


def _relative(frame: Frame, origin: Point, box: tuple[float, float, float, float]) -> Box:
    length = frame.layout.length
    x0, y0, x1, y1 = (round(origin[i % 2] + length(v)) for i, v in enumerate(box))
    return max(0, x0), max(0, y0), min(frame.layout.width, x1), min(frame.layout.height, y1)


def find_server(frame: Frame, view: LoginView, server: str) -> Point | None:
    """Point où cliquer sur la carte du serveur `server` (écran de choix du serveur) ; None s'il n'y est pas."""
    found = find_text(frame, server, _relative(frame, view.title, SERVER_NAMES), SERVER_SIZE, bold=True)
    if found is None:
        return None
    (x, y), _ = found
    return round(x + frame.layout.length(NAME_TO_CARD[0])), round(y + frame.layout.length(NAME_TO_CARD[1]))


def slot_box(frame: Frame, view: LoginView, slot: int) -> Box:
    """Plaque du nom de l'emplacement `slot` (0 : le plus à gauche)."""
    dx = SLOTS_DX[slot]
    return _relative(frame, view.title, (dx - PLATE_HALF[0], PLATE_DY - PLATE_HALF[1],
                                         dx + PLATE_HALF[0], PLATE_DY + PLATE_HALF[1]))


def slot_point(frame: Frame, view: LoginView, slot: int) -> Point:
    """Où cliquer pour choisir l'emplacement `slot` : sur le personnage, jamais sur sa plaque."""
    return (round(view.title[0] + frame.layout.length(SLOTS_DX[slot])),
            round(view.title[1] + frame.layout.length(BODY_DY)))


def find_character(frame: Frame, view: LoginView, names: list[str]) -> tuple[int, str] | None:
    """(emplacement, nom) du premier de `names` présent sur l'écran de choix du personnage ; None si aucun. Chaque nom
    est cherché sur chaque plaque, la plus ressemblante l'emporte (au-dessus du seuil)."""
    size = frame.layout.length(CHARACTER_SIZE)
    for name in names:
        scores = [text_score(frame.crop(slot_box(frame, view, slot)), name, size, bold=True)[0]
                  for slot in range(len(SLOTS_DX))]
        best = max(range(len(scores)), key=scores.__getitem__)
        if scores[best] >= NAME_THRESHOLD:
            return best, name
    return None
