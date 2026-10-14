"""Fenêtre de lancement, sans l'affichage : choix de l'utilisateur, ligne de commande du bot, validation, choix
mémorisés (data/gui.json), fenêtres du jeu ouvertes et processus du bot. L'affichage (tkinter) : bot/gui/app.py.

Les choix deviennent les options de bot/__main__.py. Les réglages qui n'en ont pas (rafale, mode d'attente...)
passent par --set section.cle=valeur (lu par bot/config.py : parse_override), seulement quand le choix diffère de
config/settings.yaml : tant qu'on ne touche pas à ces réglages, la ligne de commande n'utilise que les autres options.
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from pynput import keyboard

from bot.actions.stop import parse_key
from bot.capture.monitors import monitors, pick_monitor, screen_labels, valid_choice
from bot.capture.window import character_name, find_windows
from bot.connection.characters import parse_names
from bot.config import ROOT

GUI_PATH = ROOT / "data" / "gui.json"  # derniers choix, rechargés à l'ouverture
MAIN_PATH = ROOT / "bot" / "__main__.py"  # options de la ligne de commande, lues dans le code (main_options)
VENV_PYTHON = Path(".venv") / "Scripts" / "python.exe"

MODES = {
    "carte": "Combat sur la carte",
    "zone": "Combat en zone",
    "aller": "Déplacement",
    "explore": "Exploration",
    "test": "Test de déplacement",
    "recolte": "Récolte (métier)",
}
SOON = ("recolte",)  # prévus, pas encore programmés : bouton grisé
COMBAT_MODES = ("carte", "zone")  # les seuls où le bot attaque des groupes (--combat-only)
WORLDS = {"incarnam": "Incarnam", "douze": "Monde des Douze"}  # clés de --monde (WORLDS de bot/brain/travel.py)
WAIT_MODES = ("fixe", "condition")
# Début de session (--lecture, session.mode ; READING_MODES de bot/brain/memory.py) : lire à l'écran, ou reprendre.
READINGS = {"controle": "Contrôler (lire les sorts et les caractéristiques)", "memoire": "Reprendre la dernière session"}
DEFAULT_TITLE = "- Dofus Retro"
# Serveurs proposés (--serveur) ; la liste complète des serveurs Rétro sera ajoutée une fois la source approuvée.
SERVERS = ("Allisteria",)
DEFAULT_SERVER = "Allisteria"
COORDS = re.compile(r"^\s*(-?\d+)\s*,\s*(-?\d+)\s*$")
OPTION = re.compile(r"""add_argument\(\s*["'](--[\w-]+)["']""")
STOP_GRACE = 8.0  # secondes laissées au bot pour s'arrêter après la touche d'arrêt, avant de terminer son processus


@dataclass(frozen=True)
class Choices:
    """Ce qui est coché dans la fenêtre ; les coordonnées restent du texte (« X,Y ») tel que tapé."""

    mode: str = "carte"  # voir MODES
    zone_xy: str = ""  # combat en zone : carte de la sous-zone (--combat-zone) ; vide : --zone, carte lue à l'écran
    aller_from: str = ""  # déplacement : carte de départ (--position) ; vide : lue à l'écran
    aller_to: str = ""  # déplacement : carte d'arrivée (--aller)
    explore_from: str = ""  # exploration : carte de départ (--position) ; vide : lue à l'écran
    combat_only: bool = False  # --combat-only : le bot n'attaque pas les groupes, il joue les combats lancés à la main
    observe: bool = False  # --observe : aucun clic, aucune touche
    monde: str = "incarnam"  # --monde
    team: bool = False  # --equipe
    leader: str = ""  # --principal ; en solo : la fenêtre de ce personnage (--title), vide : la première trouvée
    notifications: bool = False  # --notifications (mode équipe)
    burst: bool = False  # combat.burst (--set)
    wait_mode: str = "fixe"  # timing.wait_mode (--set)
    reading: str = "controle"  # --lecture : début de session, sorts et caractéristiques lus (controle) ou repris (memoire)
    verbose: bool = False  # --verbose
    background: bool = False  # --arriere-plan : gestes envoyés aux fenêtres du jeu, souris et clavier libres
    screen: str = "auto"  # --ecran : écran où les fenêtres du jeu sont placées et agrandies (window.ecran)
    connect: bool = False  # --connexion : connecte les fenêtres restées à l'écran de connexion, reconnecte pendant le jeu
    server: str = DEFAULT_SERVER  # --serveur : serveur choisi à la connexion
    characters: str = ""  # --personnages : personnages cochés, « Gela-[A13],Brawli-[Gal] »
    title: str = DEFAULT_TITLE  # window.title : partie du titre des fenêtres du jeu (--title)


def default_choices(settings: dict) -> Choices:
    """Choix d'après config/settings.yaml : ce que fait le bot lancé sans option."""
    combat = settings.get("combat") or {}
    travel = settings.get("travel") or {}
    team = settings.get("team") or {}
    mode = str(travel.get("mode") or "")
    region = str(travel.get("region") or "").strip().lower()
    destination = travel.get("destination")
    wait_mode = str((settings.get("timing") or {}).get("wait_mode") or "").strip().lower()
    reading = _plain(str((settings.get("session") or {}).get("mode") or ""))
    return Choices(
        mode=mode if mode in ("zone", "aller", "explore", "test") else "carte",
        aller_to=destination.strip() if isinstance(destination, str) else "",
        combat_only=not bool((settings.get("engage") or {}).get("enabled", True)),
        monde=next((key for key, name in WORLDS.items() if name.lower() == region), "incarnam"),
        team=bool(team.get("enabled", False)),
        leader=str(team.get("leader") or "").strip(),
        notifications=str(team.get("turn_signal") or "").strip().lower() == "notifications",
        burst=bool(combat.get("burst", False)),
        wait_mode=wait_mode if wait_mode in WAIT_MODES else "fixe",
        reading=reading if reading in READINGS else "controle",
        background=bool((settings.get("window") or {}).get("arriere_plan", False)),
        screen=_screen_setting((settings.get("window") or {}).get("ecran", "auto")),
        connect=bool((settings.get("connexion") or {}).get("auto", False)),
        server=str((settings.get("connexion") or {}).get("serveur") or DEFAULT_SERVER).strip(),
        characters=",".join(_setting_names((settings.get("connexion") or {}).get("personnages"))),
        title=str((settings.get("window") or {}).get("title") or DEFAULT_TITLE).strip(),
    )


def _plain(text: str) -> str:
    """« Mémoire » -> « memoire » (session.mode peut être écrit avec ses accents)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).strip().lower()


def parse_coords(text: str) -> tuple[int, int] | None:
    """« 5, -18 » -> (5, -18) ; None si ce ne sont pas deux entiers séparés par une virgule."""
    match = COORDS.match(text)
    return (int(match[1]), int(match[2])) if match else None


def coords_text(text: str) -> str:
    """Coordonnées telles que le bot les attend (« 5,-18 », sans espace) ; le texte tel quel s'il est illisible."""
    coords = parse_coords(text)
    return f"{coords[0]},{coords[1]}" if coords else text.strip()


def window_title(name: str, title_part: str) -> str:
    """Titre de la fenêtre d'un personnage : « Jesuisleking », « - Dofus Retro » -> « Jesuisleking - Dofus Retro »."""
    return f"{name.strip()} - {title_part.strip().lstrip('-').strip()}"


def character_names(titles: list[str], title_part: str) -> list[str]:
    """Personnages des fenêtres du jeu (ordre alphabétique, sans doublon), comme le mode équipe les trouve
    (bot/team/roster.py) ; les fenêtres sans nom (écran de connexion) sont ignorées."""
    names: dict[str, str] = {}
    for title in titles:
        name = character_name(title, title_part)
        if name:
            names.setdefault(name.lower(), name)
    return sorted(names.values(), key=str.lower)


def detect_windows(title_part: str) -> list[str]:
    """Titres des fenêtres du jeu ouvertes, cherchées comme le fait le bot (bot/capture/window.py)."""
    return [window.title for window in find_windows(title_part)] if title_part.strip() else []


def overrides(choices: Choices, settings: dict) -> list[tuple[str, object]]:
    """Réglages de config/settings.yaml à remplacer au lancement (--set) : seulement ceux qui diffèrent des choix."""
    base = default_choices(settings)
    changed: list[tuple[str, object]] = []
    if choices.mode == "carte" and (settings.get("travel") or {}).get("mode"):
        changed.append(("travel.mode", ""))  # sinon le bot changerait de carte d'après travel.mode
    if choices.mode in COMBAT_MODES and not choices.combat_only and base.combat_only:
        changed.append(("engage.enabled", True))
    if not choices.team and base.team:
        changed.append(("team.enabled", False))  # pas d'option pour le mode solo quand team.enabled vaut true
    if choices.team and not choices.notifications and base.notifications:
        changed.append(("team.turn_signal", "timeline"))
    if choices.burst != base.burst:
        changed.append(("combat.burst", choices.burst))
    if choices.wait_mode != base.wait_mode:
        changed.append(("timing.wait_mode", choices.wait_mode))
    if not choices.background and base.background:
        changed.append(("window.arriere_plan", False))  # pas d'option pour le premier plan quand le réglage dit oui
    return changed


def format_override(key: str, value) -> str:
    """("combat.burst", False) -> « combat.burst=false », relu par parse_override (bot/config.py)."""
    if isinstance(value, bool):
        value = "true" if value else "false"
    return f"{key}={'' if value is None else value}"


def _option(name: str, value: str) -> list[str]:
    """« --title=- Dofus Retro » quand la valeur commence par un tiret : séparée, argparse la prendrait pour une
    option."""
    return [f"{name}={value}"] if value.startswith("-") else [name, value]


def _coords_option(name: str, text: str) -> str:
    """« --aller=-1,-20 » : coordonnées toujours collées par « = » (x négatif : séparé, « -1,-20 » passerait pour une
    option auprès d'argparse)."""
    return f"{name}={coords_text(text)}"


def build_args(choices: Choices, settings: dict) -> list[str]:
    """Options de `python -m bot` pour ces choix (à vérifier d'abord avec validate)."""
    args = ["--monde", choices.monde]
    if choices.mode == "zone":
        args.append(_coords_option("--combat-zone", choices.zone_xy) if choices.zone_xy.strip() else "--zone")
    elif choices.mode == "aller":
        if choices.aller_from.strip():
            args.append(_coords_option("--position", choices.aller_from))
        args.append(_coords_option("--aller", choices.aller_to))
    elif choices.mode == "explore":
        args.append("--explore")
        if choices.explore_from.strip():
            args.append(_coords_option("--position", choices.explore_from))
    elif choices.mode == "test":
        args.append("--travel-test")
    if choices.mode in COMBAT_MODES and choices.combat_only:
        args.append("--combat-only")
    if choices.observe:
        args.append("--observe")
    leader = choices.leader.strip()
    if choices.team:
        args.append("--equipe")
        if leader:
            args += _option("--principal", leader)
        if choices.notifications:
            args.append("--notifications")
    title = choices.title.strip()
    if title and leader and not choices.team:
        title = window_title(leader, title)  # solo : la fenêtre de ce personnage, pas la première trouvée
    if title and title != default_choices(settings).title:
        args += _option("--title", title)
    if choices.reading != default_choices(settings).reading:
        args += ["--lecture", choices.reading]  # seulement s'il diffère de session.mode
    if choices.verbose:
        args.append("--verbose")
    if choices.background and not default_choices(settings).background:
        args.append("--arriere-plan")
    if choices.screen.strip().lower() != default_choices(settings).screen:
        args += ["--ecran", choices.screen.strip().lower()]
    if choices.connect:
        if not default_choices(settings).connect:
            args.append("--connexion")
        if choices.server.strip() and choices.server.strip() != default_choices(settings).server:
            args += _option("--serveur", choices.server.strip())
        names = parse_names(choices.characters)
        if names and names != parse_names(default_choices(settings).characters):
            args += _option("--personnages", ",".join(names))
    for key, value in overrides(choices, settings):
        args += ["--set", format_override(key, value)]
    return args


def _check_coords(errors: list[str], what: str, label: str, text: str, required: bool = False) -> tuple[int, int] | None:
    if not text.strip():
        if required:
            errors.append(f"{what} : coordonnées {label} manquantes (X,Y, ex. 5,-18)")
        return None
    coords = parse_coords(text)
    if coords is None:
        errors.append(f"{what} : coordonnées {label} attendues sous la forme X,Y en nombres entiers (ex. 5,-18), "
                      f"pas « {text.strip()} »")
    return coords


def _setting_names(value) -> list[str]:
    """connexion.personnages (liste, ou texte seul) -> noms."""
    values = [value] if isinstance(value, str) else (value or [])
    return [str(name).strip() for name in values if str(name).strip()]


def _screen_setting(value) -> str:
    """window.ecran tel que l'interface le coche : vide ou null, pas de placement (« aucun »)."""
    return str(value if value is not None else "aucun").strip().lower() or "aucun"


def detect_screens() -> list[tuple[str, str]]:
    """(valeur de --ecran, libellé) : « Automatique » puis chaque écran détecté, de gauche à droite."""
    screens = monitors()
    auto = pick_monitor("auto", screens)
    number = next((str(i) for i, m in enumerate(screens, 1) if m is auto), "")
    return [("auto", f"Automatique (écran 1080p{f' : écran {number}' if number else ''})")] + screen_labels(screens)


def validate(choices: Choices, settings: dict, titles: list[str] | None = None,
             options: frozenset[str] | None = None, screens: int | None = None) -> list[str]:
    """Ce qui empêche de lancer le bot, en clair ; [] si tout va bien.

    `titles` : titres des fenêtres du jeu ouvertes (detect_windows ; None : pas vérifié) ;
    `options` : options connues de bot/__main__.py (main_options ; None : pas vérifié) ;
    `screens` : nombre d'écrans détectés (None : pas vérifié)."""
    errors: list[str] = []
    screen = choices.screen.strip().lower()
    if not valid_choice(screen):
        errors.append(f"Écran inconnu : « {choices.screen} »")
    elif screen.isdigit() and screens is not None and int(screen) > screens:
        errors.append(f"L'écran {screen} n'est plus branché ({screens} écran(s)) : choisis-en un autre")
    if choices.mode not in MODES:
        errors.append(f"Mode inconnu : {choices.mode!r}")
    elif choices.mode in SOON:
        errors.append(f"{MODES[choices.mode]} : pas encore disponible")
    elif choices.mode == "zone":
        _check_coords(errors, "Combat en zone", "de la carte", choices.zone_xy)
    elif choices.mode == "aller":
        start = _check_coords(errors, "Déplacement", "de départ", choices.aller_from)
        end = _check_coords(errors, "Déplacement", "d'arrivée", choices.aller_to, required=True)
        if start is not None and start == end:
            errors.append(f"Déplacement : l'arrivée est la carte de départ ({start[0]},{start[1]})")
    elif choices.mode == "explore":
        _check_coords(errors, "Exploration", "de départ", choices.explore_from)
    if choices.monde not in WORLDS:
        errors.append(f"Monde inconnu : {choices.monde!r} ({' ou '.join(WORLDS)})")
    if choices.wait_mode not in WAIT_MODES:
        errors.append(f"Mode d'attente inconnu : {choices.wait_mode!r} ({' ou '.join(WAIT_MODES)})")
    if choices.reading not in READINGS:
        errors.append(f"Début de session inconnu : {choices.reading!r} ({' ou '.join(READINGS)})")
    title, leader = choices.title.strip(), choices.leader.strip()
    if not title:
        errors.append(f"Titre des fenêtres du jeu vide (d'habitude « {DEFAULT_TITLE} »)")
    if choices.team and not leader:
        errors.append("Mode équipe : choisis le personnage principal")
    if choices.connect and not choices.server.strip():
        errors.append("Connexion automatique : choisis le serveur")
    if titles is not None and title and not choices.connect:  # connexion : les fenêtres seront connectées par le bot
        names = character_names(titles, title)
        where = f"titre contenant « {title} »"
        if choices.team and not names:
            errors.append(f"Mode équipe : aucune fenêtre de personnage ouverte ({where}) : lance et connecte les jeux, "
                          "puis clique sur Actualiser")
        elif not titles:
            errors.append(f"Aucune fenêtre du jeu ouverte ({where}) : lance le jeu, puis clique sur Actualiser")
        elif leader and leader.lower() not in {name.lower() for name in names}:
            errors.append(f"« {leader} » n'a pas de fenêtre ouverte (personnages : {', '.join(names) or 'aucun'}) : "
                          "choisis-en un autre, ou clique sur Actualiser")
    if options is not None:
        needed = dict.fromkeys(arg.split("=")[0] for arg in build_args(choices, settings) if arg.startswith("--"))
        for option in needed:
            if option in options:
                continue
            if option == "--set":
                changed = ", ".join(format_override(key, value) for key, value in overrides(choices, settings))
                errors.append(f"Réglages différents de config/settings.yaml ({changed}) : l'option --set n'existe pas "
                              "encore dans bot/__main__.py ; en attendant, change-les dans config/settings.yaml")
            else:
                errors.append(f"L'option {option} n'existe pas encore dans bot/__main__.py")
    return errors


def main_options(path: Path = MAIN_PATH) -> frozenset[str] | None:
    """Options de la ligne de commande du bot, lues dans son code (sans l'importer ni le lancer) ; None si illisible."""
    try:
        return frozenset(OPTION.findall(path.read_text(encoding="utf-8")))
    except OSError:
        return None


def bot_python(root: Path = ROOT) -> Path:
    """Python du .venv du projet (python.exe : la sortie du bot doit pouvoir être lue, ce que pythonw ne permet pas)."""
    venv = root / VENV_PYTHON
    if venv.exists():
        return venv
    current = Path(sys.executable)
    console = current.with_name("python.exe")
    return console if current.name.lower() == "pythonw.exe" and console.exists() else current


def bot_command(args: list[str], root: Path = ROOT) -> list[str]:
    return [str(bot_python(root)), "-m", "bot", *args]


def display_command(args: list[str], root: Path = ROOT) -> str:
    """La commande à taper soi-même dans un terminal, depuis la racine du projet."""
    python = r".venv\Scripts\python" if (root / VENV_PYTHON).exists() else "python"
    return subprocess.list2cmdline([python, "-m", "bot", *args])


def _allowed(name: str, value) -> bool:
    return {"mode": lambda: value in MODES and value not in SOON, "monde": lambda: value in WORLDS,
            "wait_mode": lambda: value in WAIT_MODES, "reading": lambda: value in READINGS}.get(name, lambda: True)()


def load_choices(path: Path, defaults: Choices) -> Choices:
    """Derniers choix mémorisés ; `defaults` pour ce qui manque ou n'est plus valable (fichier absent, abîmé...)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    if not isinstance(data, dict):
        return defaults
    kept = {}
    for field in fields(Choices):
        value, default = data.get(field.name), getattr(defaults, field.name)
        if type(value) is type(default) and _allowed(field.name, value):
            kept[field.name] = value
    return replace(defaults, **kept)


def save_choices(choices: Choices, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(choices), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stop_key(settings: dict) -> str:
    """Touche d'arrêt du bot (safety.stop_key)."""
    return str((settings.get("safety") or {}).get("stop_key") or "f12")


def press_key(name: str) -> None:
    """Un appui sur la touche, comme au clavier : le bot écoute sa touche d'arrêt dans tout le système
    (bot/actions/stop.py), quelle que soit la fenêtre au premier plan (celle-ci, après le clic sur Arrêter)."""
    keyboard.Controller().tap(parse_key(name))


class BotProcess:
    """Le bot dans un processus à part : les lignes de son journal (sortie du processus) au fil de l'eau, et son arrêt.

    Arrêt propre : sa touche d'arrêt (F12), comme au clavier. S'il tourne encore STOP_GRACE secondes après, ou à
    la deuxième demande, son processus est terminé."""

    def __init__(self, popen: Callable = subprocess.Popen, send_key: Callable[[str], None] = press_key,
                 clock: Callable[[], float] = time.monotonic, grace: float = STOP_GRACE) -> None:
        self._popen = popen
        self._send_key = send_key
        self._clock = clock
        self.grace = grace
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._lines: queue.Queue[str] = queue.Queue()
        self._stop_at: float | None = None
        self._stop_key = ""

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def stopping(self) -> bool:
        """Arrêt demandé, le bot pas encore arrêté."""
        return self._stop_at is not None and self.running

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def start(self, command: list[str], cwd: Path = ROOT) -> None:
        if self.running:
            raise RuntimeError("le bot tourne déjà")
        self._stop_at = None
        # Journal du bot en UTF-8 (accents) et ligne par ligne ; pas de console à lui (pythonw -m bot.gui).
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        process = self._popen(command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._process = process
        self._reader = threading.Thread(target=self._read, args=(process,), name="journal-du-bot", daemon=True)
        self._reader.start()

    def _read(self, process: subprocess.Popen) -> None:
        with process.stdout:
            for line in process.stdout:
                self._lines.put(line.rstrip("\r\n"))

    def lines(self) -> list[str]:
        """Lignes du journal arrivées depuis le dernier appel."""
        lines = []
        while True:
            try:
                lines.append(self._lines.get_nowait())
            except queue.Empty:
                return lines

    def request_stop(self, key: str) -> None:
        """Touche d'arrêt du bot ; à la deuxième demande, fin du processus sans attendre."""
        if not self.running:
            return
        if self._stop_at is not None:
            self._kill("deuxième demande d'arrêt")
            return
        self._stop_at, self._stop_key = self._clock(), key
        try:
            self._send_key(key)
        except Exception as error:  # touche inconnue, clavier inaccessible...
            self._kill(f"touche {key.upper()} pas envoyée ({error})")
            return
        self._note(f"Arrêt demandé : touche {key.upper()}")

    def check(self) -> int | None:
        """Code de sortie du bot, une seule fois, quand il s'est terminé ; None tant qu'il tourne (ou sans bot).
        Termine son processus s'il tourne encore STOP_GRACE secondes après la touche d'arrêt."""
        process = self._process
        if process is None:
            return None
        code = process.poll()
        if code is None:
            if self._stop_at is not None and self._clock() - self._stop_at >= self.grace:
                self._kill(f"toujours en marche {self.grace:g} s après la touche {self._stop_key.upper()}")
            return None
        if self._reader is not None:
            self._reader.join(timeout=1.0)  # ses dernières lignes de journal
        self._process = self._reader = None
        self._stop_at = None
        return code

    def _kill(self, why: str) -> None:
        self._note(f"Processus du bot terminé de force : {why}")
        self._stop_at = None
        try:
            self._process.terminate()
        except OSError:
            pass  # déjà terminé

    def _note(self, text: str) -> None:
        self._lines.put(f"[interface] {text}")
