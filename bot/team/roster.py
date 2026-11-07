"""Qui fait partie de l'équipe : une fenêtre du jeu par personnage, repérée par son titre.

Titre d'une fenêtre : « Jesuisleking - Dofus Retro v1.49.5 » -> personnage « Jesuisleking ». Réglages : section `team`
de config/settings.yaml ; en ligne de commande, --equipe et --principal NOM.
"""

import logging
from dataclasses import dataclass

from bot.capture.window import GameWindow, character_name

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamSettings:
    enabled: bool = False
    leader: str = ""  # personnage principal ; "" : le premier de `members`, sinon le premier trouvé (ordre alphabétique)
    members: tuple[str, ...] = ()  # personnages de l'équipe ; vide : toutes les fenêtres du jeu ouvertes
    switch_delay: float = 0.25  # après le passage d'une fenêtre au premier plan, le temps que le jeu se redessine
    ready_key: str = "f1"  # « Prêt » au placement ; "" : clic sur le bouton
    join_timeout: float = 20.0  # secondes au plus, pour chacun des autres, pour rejoindre le combat
    round_every: float = 15.0  # hors combat, tour des fenêtres au plus toutes les 15 s (suiveur agressé...)
    # Placement : False (défaut, 03/10) : personne ne bouge, Prêt tout de suite ; chacun est repéré à son premier tour
    # par la portée d'un sort (rapide même à 8). True : le principal choisit sa case, chaque suiveur la sienne.
    place: bool = False
    # DPS placés en priorité (07/11, utilisateur : « un mode où on définit les dps qui doivent être placés correctement
    # en priorité pour taper ; en fonction de la classe on veut du distance ou autre ») : (nom, rôle), dans l'ordre de
    # priorité ; rôle « distance » (à portée de son sort qui porte le plus loin, en ligne de vue, le plus loin possible
    # des ennemis) ou « contact » (au plus près d'un ennemi). Seuls eux bougent ; les autres sont Prêts tout de suite.
    dps: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_settings(cls, settings: dict, enabled: bool | None = None, leader: str | None = None) -> "TeamSettings":
        """`enabled`, `leader` : options de la ligne de commande (None : réglage de config/settings.yaml)."""
        team = settings.get("team") or {}
        raw = team.get("enabled", cls.enabled)
        members = team.get("members") or ()
        if isinstance(members, str):
            members = [members]
        return cls(
            # « auto » : décidé au lancement d'après les fenêtres ouvertes (bot/__main__.py) ; ici, pas d'équipe.
            enabled=(bool(raw) and str(raw).strip().lower() != "auto") if enabled is None else enabled,
            leader=(leader if leader is not None else str(team.get("leader") or "")).strip(),
            members=tuple(str(m).strip() for m in members if str(m).strip()),
            switch_delay=float(team.get("switch_delay", cls.switch_delay)),
            ready_key=str(team.get("ready_key", cls.ready_key) or ""),
            join_timeout=float(team.get("join_timeout", cls.join_timeout)),
            round_every=float(team.get("round_every", cls.round_every)),
            place=bool(team.get("place", cls.place)),
            dps=parse_dps(team.get("dps")),
        )


DPS_ROLES = ("distance", "contact")


def parse_dps(raw) -> tuple[tuple[str, str], ...]:
    """team.dps : liste de « Nom » ou « Nom:rôle » (aussi une chaîne séparée par des virgules, pour --set), ou
    {Nom: rôle} ; rôle « distance » par défaut. Un rôle inconnu est remplacé par « distance » (dit au journal)."""
    import logging

    if not raw:
        return ()
    if isinstance(raw, dict):
        items = [f"{name}:{role}" for name, role in raw.items()]
    elif isinstance(raw, str):
        items = raw.split(",")
    else:
        items = [str(item) for item in raw]
    out = []
    for item in items:
        name, _, role = str(item).strip().partition(":")
        name, role = name.strip(), (role.strip().lower() or "distance")
        if not name:
            continue
        if role not in DPS_ROLES:
            logging.getLogger(__name__).warning("team.dps : rôle « %s » inconnu pour %s (%s) : distance", role, name,
                                                ", ".join(DPS_ROLES))
            role = "distance"
        out.append((name, role))
    return tuple(out)


def pick_team(windows: list[GameWindow], title_part: str, settings: TeamSettings) -> list[tuple[str, GameWindow]]:
    """(nom, fenêtre) de chaque personnage, le principal en premier. ValueError si l'équipe ne peut pas être formée."""
    found: dict[str, tuple[str, GameWindow]] = {}
    for window in windows:
        name = character_name(window.title, title_part)
        if not name:
            log.info("Fenêtre « %s » ignorée : pas de nom de personnage dans son titre (écran de connexion ?)", window.title)
            continue
        if name.lower() in found:
            log.warning("Deux fenêtres pour %s : je garde la première", name)
            continue
        found[name.lower()] = (name, window)
    if not found:
        raise ValueError(f"Aucune fenêtre de personnage ne contient « {title_part} ». Les jeux sont-ils lancés et connectés ?")
    if settings.members:
        missing = [m for m in settings.members if m.lower() not in found]
        if missing:
            log.warning("Personnage(s) sans fenêtre ouverte : %s (fenêtres : %s)", ", ".join(missing),
                        ", ".join(n for n, _ in found.values()))
        team = [found[m.lower()] for m in settings.members if m.lower() in found]
        if not team:
            raise ValueError("Aucun des personnages de team.members n'a de fenêtre ouverte")
    else:
        team = sorted(found.values(), key=lambda entry: entry[0].lower())
    leader = settings.leader or team[0][0]
    chosen = next((entry for entry in team if entry[0].lower() == leader.lower()), None)
    if chosen is None:
        raise ValueError(f"Personnage principal « {leader} » introuvable parmi les fenêtres de l'équipe : "
                         f"{', '.join(n for n, _ in team)}")
    return [chosen] + [entry for entry in team if entry is not chosen]
