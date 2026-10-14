"""Réglages de la connexion automatique : section `connexion` de config/settings.yaml."""

import os
from dataclasses import dataclass

DEFAULT_LAUNCHER = r"%LOCALAPPDATA%\Programs\Ankama Launcher\Ankama Launcher.exe"


@dataclass(frozen=True)
class ConnectionSettings:
    enabled: bool = False  # au lancement : connexion des fenêtres ; pendant le jeu : reconnexion
    server: str = "Allisteria"
    characters: tuple[str, ...] = ()  # à choisir sur l'écran des personnages (vide : ceux de team.members)
    launcher: str = DEFAULT_LAUNCHER
    login_timeout: float = 120.0  # secondes au plus pour qu'une fenêtre arrive en jeu
    clients_timeout: float = 180.0  # secondes au plus pour voir apparaître les fenêtres du jeu (launcher)
    check_every: float = 5.0  # pendant le jeu : titres des fenêtres relus au plus toutes les 5 s
    group: bool = True  # mode équipe : reformer le groupe (invitation par la fenêtre « Amis »)
    collapse_group: bool = True  # replier le cadre du groupe (moins de place sur la carte)

    @classmethod
    def from_settings(cls, settings: dict, enabled: bool | None = None, server: str | None = None,
                      characters: tuple[str, ...] | None = None) -> "ConnectionSettings":
        """`enabled`, `server`, `characters` : options de la ligne de commande, remplies par l'interface (None :
        config/settings.yaml)."""
        section = settings.get("connexion") or {}
        if characters is None:
            characters = section.get("personnages") or ()
        if isinstance(characters, str):
            characters = [characters]
        return cls(
            enabled=bool(section.get("auto", cls.enabled)) if enabled is None else enabled,
            server=str(server or section.get("serveur") or cls.server).strip(),
            characters=tuple(str(c).strip() for c in characters if str(c).strip()),
            launcher=str(section.get("launcher") or cls.launcher),
            login_timeout=float(section.get("delai_connexion", cls.login_timeout)),
            clients_timeout=float(section.get("delai_launcher", cls.clients_timeout)),
            check_every=float(section.get("verifier_toutes", cls.check_every)),
            group=bool(section.get("groupe", cls.group)),
            collapse_group=bool(section.get("replier_groupe", cls.collapse_group)),
        )

    @property
    def launcher_path(self) -> str:
        return os.path.expandvars(self.launcher)

    def wanted(self, members: tuple[str, ...] = ()) -> list[str]:
        """Personnages à choisir sur l'écran des personnages : connexion.personnages, sinon team.members."""
        return list(self.characters or members)
