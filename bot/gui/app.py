"""Fenêtre de lancement du bot (tkinter) : cocher les options, voir la ligne de commande, lancer et arrêter le bot,
suivre son journal. Ce qui ne dépend pas de l'affichage (commande, validation, choix mémorisés, processus du bot) :
bot/gui/launch.py.

Depuis la racine du projet :
    .venv\\Scripts\\pythonw -m bot.gui     sans fenêtre de console
    .venv\\Scripts\\python -m bot.gui      avec, pour voir les erreurs éventuelles de l'interface elle-même

Au démarrage, le bot met lui-même la fenêtre du jeu au premier plan (en mode équipe : celle du principal).
"""

import ctypes
import time
import tkinter as tk
import traceback
from dataclasses import fields
from pathlib import Path
from tkinter import messagebox, ttk

from bot.capture.window import character_name
from bot.config import load_settings
from bot.connection.characters import known_characters, parse_names
from bot.gui.launch import (COMBAT_MODES, GUI_PATH, MODES, READINGS, SERVERS, SOON, WORLDS, BotProcess, Choices,
                            bot_command, build_args, character_names, default_choices, detect_screens, detect_windows,
                            display_command, known_dungeons, load_choices, main_options, save_choices, stop_key,
                            validate)

LOG_LINES = 1000  # lignes du journal gardées à l'écran
TICK_MS = 100  # lecture du journal et de l'état du bot
GREY = "#6b6b6b"
RED = "#c00000"
LEVEL_COLORS = {"WARNING": "#b35c00", "ERROR": RED, "CRITICAL": RED}
INTERFACE = "[interface]"  # lignes écrites par cette fenêtre (pas par le bot)

HELP = {
    "carte": "Attaque les groupes de monstres de la carte où se trouve le personnage, sans en changer.",
    "zone": "Combats en restant dans la sous-zone (Prairie, Forêt...) de la carte X,Y, où le personnage doit se "
            "trouver ; carte voisine de la même zone quand il n'y a plus de groupe. Vide : carte lue à l'écran.",
    "aller": "Sans combat : de la carte de départ à la carte d'arrivée, par le plus court chemin. Coordonnées en haut "
             "à gauche du jeu ; départ vide : lu à l'écran.",
    "explore": "Sans combat : visite toutes les cartes de la région. Départ vide : lu à l'écran.",
    "test": "Sans combat : s'éloigne de 2 cartes puis revient sur la carte de départ (vérifie les déplacements).",
    "donjon": "Mode équipe : l'équipe devant l'entrée du donjon choisi y entre (dalles, clef au PNJ), fait les combats "
              "de salle en salle, ressort par le PNJ de fin, et recommence autant de fois que de passages "
              "(--donjon, --passages). Donjons connus : config/donjons_entree.yaml.",
    "recolte": "Récolte des ressources d'un métier : prévue, pas encore programmée.",
    "combat_only": "Le bot n'attaque pas les groupes : il joue les combats que tu lances toi-même (--combat-only).",
    "observe": "Le bot regarde et écrit dans le journal ce qu'il voit, sans aucun clic ni aucune touche (--observe).",
    "monde": "Où se trouve le personnage : Incarnam ou le Monde des Douze, qui ont chacun leurs coordonnées (--monde).",
    "team": "Une fenêtre du jeu par personnage : le principal attaque et choisit le chemin, les autres le rejoignent "
            "en combat et le suivent (--equipe).",
    "leader": "Mode équipe : le personnage principal (--principal). En solo : la fenêtre jouée quand plusieurs jeux "
              "sont ouverts ; vide : la première trouvée.",
    "refresh": "Cherche à nouveau les fenêtres du jeu ouvertes (titre « <Nom> - Dofus Retro »).",
    "notifications": "Mode équipe : qui joue, d'après les notifications Windows que le jeu envoie au début de chaque "
                     "tour (option du jeu « Notifications en arrière-plan »), vérifiées au timer (--notifications).",
    "combat": "Un seul style de combat, polyvalent : taper un ennemi à chaque tour, achever les ennemis entamés, ne pas "
              "finir au contact, ne pas se coller aux alliés ni couper leurs lignes de vue (brain/engine.py).",
    "burst": "Rafale (à l'essai) : quand le même sort est relancé sur la même cible, les lancers s'enchaînent sans "
             "nouveau plan entre deux (combat.burst de config/settings.yaml).",
    "wait_mode": "Attentes en combat. Fixe : pauses puis vérification (validé). Condition (à l'essai) : surveille "
                 "l'écran jusqu'au résultat (timing.wait_mode de config/settings.yaml).",
    "verbose": "Écrit aussi le détail dans le journal : clics, combattants (--verbose).",
    "background": "Arrière-plan : le bot envoie ses gestes directement aux fenêtres du jeu, sans toucher à ta souris ni "
                  "à ton clavier, ni passer une fenêtre devant. Les fenêtres du jeu peuvent être recouvertes (vidéo, "
                  "autre application), jamais réduites dans la barre des tâches (--arriere-plan).",
    "reading": "Début de session, pour chaque personnage. Contrôler : ouvre la fenêtre des sorts et celles des "
               "caractéristiques, les lit (dégâts des sorts selon Force, Intelligence, Chance, Agilité et les bonus aux "
               "dommages), puis les retient. Reprendre : n'ouvre aucune fenêtre, reprend ce qui a été retenu à la "
               "dernière session (data/personnages) ; un personnage jamais lu est lu à l'écran (--lecture).",
    "connect": "Connexion automatique : le bot mène lui-même en jeu les fenêtres restées à l'écran de connexion (serveur, "
               "puis personnage : ceux de connexion.personnages, sinon le dernier joué), reforme le groupe par la "
               "fenêtre « Amis » du principal et replie son cadre ; pendant le jeu, il reconnecte un personnage "
               "déconnecté (inactivité). S'il manque un personnage après les fenêtres déjà ouvertes, il passe l'Ankama "
               "Launcher devant (il le lance au besoin) et lance tous les comptes disponibles (Ctrl+8) ; chaque "
               "nouvelle fenêtre est connectée avec un personnage coché, ou refermée (--connexion).",
    "server": "Serveur choisi à la connexion (--serveur).",
    "characters": "Personnages à connecter et à jouer en équipe : coche-les. Ceux qui n'ont pas de fenêtre sont "
                  "cherchés dans les comptes que le launcher lance (--personnages).",
    "add_character": "Un personnage que le bot n'a encore jamais vu : son nom exact, tel qu'écrit dans le jeu.",
    "screen": "Écran où le bot place les fenêtres du jeu au lancement, puis les agrandit. Automatique : l'écran 1080p "
              "(celui des captures de référence, le seul validé). Actualiser : écrans détectés à nouveau (--ecran).",
    "title": "Partie du titre commune aux fenêtres du jeu (window.title de config/settings.yaml).",
    "command": "La commande lancée par le bouton Lancer ; elle peut aussi être copiée dans un terminal ouvert à la "
               "racine du projet.",
    "launch": "Lance le bot ; il met lui-même la fenêtre du jeu (celle du principal en mode équipe) au premier plan.",
    "stop": "Arrête le bot proprement (sa touche d'arrêt, comme au clavier) ; s'il ne s'arrête pas, son processus est "
            "terminé. Deuxième clic : arrêt immédiat.",
}


class Infobulle:
    """Aide affichée au survol d'un élément de la fenêtre."""

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 500) -> None:
        self.widget, self.text, self.delay_ms = widget, text, delay_ms
        self._pending: str | None = None
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._pending = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._pending is not None:
            self.widget.after_cancel(self._pending)
            self._pending = None

    def _show(self) -> None:
        self._pending = None
        if self._window is not None:
            return
        self._window = window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{self.widget.winfo_pointerx() + 14}+{self.widget.winfo_pointery() + 18}")
        tk.Label(window, text=self.text, justify="left", wraplength=400, background="#ffffe8", relief="solid",
                 borderwidth=1, padx=6, pady=4).pack()

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._window is not None:
            self._window.destroy()
            self._window = None


class App:
    def __init__(self, root: tk.Tk, settings: dict, choices: Choices, runner: BotProcess | None = None,
                 save_path: Path = GUI_PATH) -> None:
        self.root = root
        self.settings = settings
        self.runner = runner or BotProcess()
        self.save_path = save_path
        self.options = main_options()  # pour prévenir si une option attendue manque encore dans bot/__main__.py
        self.titles: list[str] | None = None  # fenêtres du jeu ouvertes (Actualiser)
        self.screens: list[tuple[str, str]] = []  # écrans détectés (Actualiser) : (valeur de --ecran, libellé)
        self.open_names: list[str] = []  # personnages des fenêtres du jeu ouvertes (Actualiser)
        # Une case par personnage proposé ; les cochés sont recopiés dans le choix « characters » à chaque changement.
        self.character_rows: dict[str, tk.BooleanVar] = {}
        self.stop_key = stop_key(settings)
        self.pause_key = str((settings.get("safety") or {}).get("pause_key") or "")
        self._closing = False
        self._scale = root.winfo_fpixels("1i") / 96  # écran mis à l'échelle (125 %, 150 %...)
        self.vars: dict[str, tk.Variable] = {}  # une variable tkinter par choix (Choices), du même nom
        for field in fields(Choices):
            value = getattr(choices, field.name)
            self.vars[field.name] = tk.BooleanVar(value=value) if isinstance(value, bool) else tk.StringVar(value=value)

        root.title("Autobot - Dofus Rétro")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self._on_error
        main = ttk.Frame(root, padding=10)
        main.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        left, right = ttk.Frame(main), ttk.Frame(main)
        left.grid(row=0, column=0, sticky="nw", padx=(0, 10))
        right.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)
        for row, build in enumerate((self._build_mode, self._build_world, self._build_team, self._build_connection,
                                     self._build_screen, self._build_session, self._build_options)):
            build(left).grid(row=row, column=0, sticky="ew", pady=(0, 8))
        self._build_run(right)

        for var in self.vars.values():
            var.trace_add("write", lambda *_: self.update_view())
        self.refresh_windows()
        self._show_state()
        root.after(TICK_MS, self._tick)

    # Construction

    def _px(self, pixels: int) -> int:
        return int(pixels * self._scale)

    def _grey(self, parent: tk.Widget, text: str, wrap: int = 0) -> ttk.Label:
        return ttk.Label(parent, text=text, foreground=GREY, wraplength=self._px(wrap) if wrap else 0)

    def _check(self, parent: tk.Widget, text: str, name: str) -> ttk.Checkbutton:
        check = ttk.Checkbutton(parent, text=text, variable=self.vars[name])
        Infobulle(check, HELP[name])
        return check

    def _radios(self, parent: tk.Widget, name: str, values: dict[str, str]) -> ttk.Frame:
        cell = ttk.Frame(parent)
        for value, text in values.items():
            radio = ttk.Radiobutton(cell, text=text, value=value, variable=self.vars[name])
            radio.pack(side="left", padx=(0, 12))
            Infobulle(radio, HELP[name])
        return cell

    def _build_mode(self, parent: tk.Widget) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Que doit faire le bot ?", padding=8)
        rows = {"carte": [], "zone": [("carte X,Y", "zone_xy")],
                "aller": [("départ X,Y", "aller_from"), ("arrivée X,Y", "aller_to")],
                "explore": [("départ X,Y", "explore_from")], "test": [], "donjon": [("passages", "passages")],
                "recolte": []}
        notes = {"zone": "vide : lue à l'écran", "aller": "départ vide : lu à l'écran", "explore": "facultatif",
                 "recolte": "bientôt"}
        self.entries: dict[str, tuple[str, ttk.Entry]] = {}  # champ de coordonnées -> (son mode, la case)
        for row, (mode, cells) in enumerate(rows.items()):
            radio = ttk.Radiobutton(box, text=MODES[mode], value=mode, variable=self.vars["mode"])
            radio.grid(row=row, column=0, sticky="w", pady=2)
            Infobulle(radio, HELP[mode])
            if mode in SOON:
                radio.state(["disabled"])
            cell = ttk.Frame(box)
            cell.grid(row=row, column=1, sticky="w")
            if mode == "donjon":
                self._dungeon_select(cell)
            for label, name in cells:
                ttk.Label(cell, text=label).pack(side="left", padx=(8, 3))
                entry = ttk.Entry(cell, textvariable=self.vars[name], width=9)
                entry.pack(side="left")
                Infobulle(entry, HELP[mode])
                self.entries[name] = (mode, entry)
            if mode in notes:
                self._grey(box, notes[mode]).grid(row=row, column=2, sticky="w", padx=(8, 0))
        row = len(rows)
        ttk.Separator(box).grid(row=row, column=0, columnspan=3, sticky="ew", pady=6)
        self.combat_only = self._check(box, "Je lance les combats moi-même (le bot n'attaque pas les groupes)",
                                       "combat_only")
        self.combat_only.grid(row=row + 1, column=0, columnspan=3, sticky="w")
        self._check(box, "Observation seulement : aucun clic, aucune touche", "observe").grid(
            row=row + 2, column=0, columnspan=3, sticky="w", pady=(2, 0))
        return box

    def _dungeon_select(self, cell: ttk.Frame) -> None:
        """Liste des donjons connus (config/donjons_entree.yaml), par leur nom ; le choix garde leur clé (--donjon)."""
        self.dungeons = known_dungeons()
        names = {name: key for key, (name, _) in self.dungeons.items()}
        shown = tk.StringVar(value=self.dungeons.get(self.vars["donjon"].get(), ("",))[0])
        select = ttk.Combobox(cell, textvariable=shown, values=list(names), state="readonly", width=22)
        select.bind("<<ComboboxSelected>>", lambda _: self._pick_dungeon(names.get(shown.get(), "")))
        select.pack(side="left", padx=(8, 0))
        Infobulle(select, HELP["donjon"])
        self.entries["donjon"] = ("donjon", select)

    def _pick_dungeon(self, key: str) -> None:
        """Donjon choisi : son monde coché aussi (Monde), le farm de donjon en mode équipe."""
        self.vars["donjon"].set(key)
        if key in self.dungeons:
            self.vars["monde"].set(self.dungeons[key][1])
            self.vars["mode"].set("donjon")
            self.vars["team"].set(True)

    def _build_world(self, parent: tk.Widget) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Monde", padding=8)
        self._radios(box, "monde", WORLDS).grid(row=0, column=0, sticky="w")
        return box

    def _build_team(self, parent: tk.Widget) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Équipe", padding=8)
        self._check(box, "Mode équipe (une fenêtre du jeu par personnage)", "team").grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(box, text="Personnages :").grid(row=1, column=0, sticky="nw", pady=(6, 0))
        self.found = ttk.Label(box, text="", wraplength=self._px(260))
        self.found.grid(row=1, column=1, sticky="w", pady=(6, 0))
        refresh = ttk.Button(box, text="Actualiser", command=self.refresh_windows)
        refresh.grid(row=1, column=2, sticky="e", padx=(8, 0), pady=(6, 0))
        Infobulle(refresh, HELP["refresh"])
        ttk.Label(box, text="Principal :").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.leader = ttk.Combobox(box, textvariable=self.vars["leader"], width=24)
        self.leader.grid(row=2, column=1, sticky="w", pady=(6, 0))
        Infobulle(self.leader, HELP["leader"])
        self.notifications = self._check(box, "Notifications Windows (qui joue, d'après le jeu)", "notifications")
        self.notifications.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        members = (self.settings.get("team") or {}).get("members") or []
        if members:
            members = [members] if isinstance(members, str) else members
            self._grey(box, "Équipe limitée à team.members (config/settings.yaml) : " + ", ".join(map(str, members)),
                       wrap=380).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        box.columnconfigure(1, weight=1)
        return box

    def _build_connection(self, parent: tk.Widget) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Connexion automatique", padding=8)
        connect = self._check(box, "Connecter et reconnecter les personnages", "connect")
        connect.grid(row=0, column=0, columnspan=3, sticky="w")
        Infobulle(connect, HELP["connect"])
        ttk.Label(box, text="Serveur :").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.server = ttk.Combobox(box, textvariable=self.vars["server"], values=SERVERS, width=20)
        self.server.grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        Infobulle(self.server, HELP["server"])
        ttk.Label(box, text="Personnages :").grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.characters_box = ttk.Frame(box)
        self.characters_box.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.new_character = tk.StringVar()
        entry = ttk.Entry(box, textvariable=self.new_character, width=20)
        entry.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        entry.bind("<Return>", lambda _: self.add_character())
        Infobulle(entry, HELP["add_character"])
        add = ttk.Button(box, text="Ajouter", command=self.add_character)
        add.grid(row=4, column=2, sticky="w", pady=(4, 0))
        Infobulle(add, HELP["add_character"])
        return box

    def refresh_characters(self, extra: tuple[str, ...] = ()) -> None:
        """Une case par personnage proposé : ceux cochés, ceux des fenêtres ouvertes, ceux que le bot a déjà vus
        (data/personnages)."""
        chosen = parse_names(self.vars["characters"].get())
        names: list[str] = []
        for name in (*chosen, *self.open_names, *known_characters(), *extra):
            if name.lower() not in {n.lower() for n in names}:
                names.append(name)
        for child in self.characters_box.winfo_children():
            child.destroy()
        self.character_rows = {}
        for row, name in enumerate(names):
            checked = tk.BooleanVar(value=name in chosen or name in extra)
            check = ttk.Checkbutton(self.characters_box, text=name, variable=checked)
            check.grid(row=row // 2, column=row % 2, sticky="w", padx=(0, 12))
            Infobulle(check, HELP["characters"])
            checked.trace_add("write", lambda *_: self._characters_changed())
            self.character_rows[name] = checked
        if not names:
            self._grey(self.characters_box, "aucun personnage connu : ajoute-le ci-dessous").grid(row=0, column=0)
        self._characters_changed()

    def add_character(self) -> None:
        name = self.new_character.get().strip()
        if name:
            self.new_character.set("")
            self.refresh_characters(extra=(name,))

    def _characters_changed(self) -> None:
        """Cases cochées -> choix (ligne de commande) ; principal proposé parmi les personnages cochés."""
        checked = [name for name, on in self.character_rows.items() if on.get()]
        self.vars["characters"].set(",".join(checked))
        self.leader.configure(values=list(dict.fromkeys(checked + self.open_names)))
        if self.vars["connect"].get() and len(checked) >= 2:
            self.vars["team"].set(True)  # plusieurs personnages à connecter : mode équipe
        leader = self.vars["leader"].get().strip()
        if checked and self.vars["connect"].get() and leader not in checked + self.open_names:
            self.vars["leader"].set(checked[0])

    def _build_screen(self, parent: tk.Widget) -> ttk.LabelFrame:
        self.screen_box = ttk.LabelFrame(parent, text="Écran du jeu (fenêtres placées et agrandies)", padding=8)
        return self.screen_box

    def refresh_screens(self) -> None:
        """Une case par écran détecté (plus « Automatique ») ; un choix qui n'existe plus revient à Automatique."""
        try:
            self.screens = detect_screens()
        except Exception as error:  # pywin32 : ne doit pas empêcher d'ouvrir la fenêtre
            self.screens = []
            text = f"détection des écrans impossible ({error})"
        else:
            text = ""
        for child in self.screen_box.winfo_children():
            child.destroy()
        for row, (value, label) in enumerate(self.screens):
            radio = ttk.Radiobutton(self.screen_box, text=label, value=value, variable=self.vars["screen"])
            radio.grid(row=row, column=0, sticky="w", pady=(0 if row == 0 else 2, 0))
            Infobulle(radio, HELP["screen"])
        if text:
            self._grey(self.screen_box, text, wrap=380).grid(row=0, column=0, sticky="w")
        values = {value for value, _ in self.screens}
        if self.screens and self.vars["screen"].get().strip().lower() not in values | {"aucun"}:
            self.vars["screen"].set("auto")

    def _build_session(self, parent: tk.Widget) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Début de session : sorts et caractéristiques", padding=8)
        for row, (value, text) in enumerate(READINGS.items()):
            radio = ttk.Radiobutton(box, text=text, value=value, variable=self.vars["reading"])
            radio.grid(row=row, column=0, sticky="w", pady=(0 if row == 0 else 2, 0))
            Infobulle(radio, HELP["reading"])
        return box

    def _build_options(self, parent: tk.Widget) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Combat et réglages", padding=8)
        style = self._grey(box, "Combat polyvalent : taper vite, à l'abri, sans gêner les alliés", wrap=380)
        style.grid(row=0, column=0, columnspan=2, sticky="w")
        Infobulle(style, HELP["combat"])
        self._check(box, "Rafale : même sort enchaîné sur la même cible", "burst").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(box, text="Attentes en combat :").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self._radios(box, "wait_mode", {"fixe": "fixe", "condition": "condition (à l'essai)"}).grid(
            row=2, column=1, sticky="w", pady=(4, 0))
        self._check(box, "Journal détaillé (clics, combattants)", "verbose").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._check(box, "Jouer en arrière-plan (souris et clavier libres)", "background").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(box, text="Titre des fenêtres :").grid(row=5, column=0, sticky="w", pady=(4, 0))
        title = ttk.Entry(box, textvariable=self.vars["title"], width=24)
        title.grid(row=5, column=1, sticky="w", pady=(4, 0))
        Infobulle(title, HELP["title"])
        return box

    def _build_run(self, parent: tk.Widget) -> None:
        box = ttk.LabelFrame(parent, text="Ligne de commande", padding=8)
        box.grid(row=0, column=0, sticky="ew")
        self.command = tk.StringVar()
        entry = ttk.Entry(box, textvariable=self.command, state="readonly")
        entry.grid(row=0, column=0, sticky="ew")
        Infobulle(entry, HELP["command"])
        ttk.Button(box, text="Copier", command=self.copy_command).grid(row=0, column=1, padx=(6, 0))
        self.problems = ttk.Label(box, text="", foreground=RED, justify="left", wraplength=self._px(620))
        self.problems.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        box.columnconfigure(0, weight=1)

        bar = ttk.Frame(parent)
        bar.grid(row=1, column=0, sticky="ew", pady=(8, 2))
        self.launch_button = ttk.Button(bar, text="Lancer", width=14, command=self.launch)
        self.launch_button.pack(side="left")
        Infobulle(self.launch_button, HELP["launch"])
        self.stop_button = ttk.Button(bar, text="", width=18, command=self.stop)
        self.stop_button.pack(side="left", padx=(6, 0))
        Infobulle(self.stop_button, HELP["stop"])
        self.status = ttk.Label(bar, text="")
        self.status.pack(side="left", padx=(12, 0))
        keys = f"{self.stop_key.upper()} : arrêt" + (f", {self.pause_key.upper()} : pause (pour reprendre la main)"
                                                     if self.pause_key else "")
        self._grey(parent, f"Le bot met lui-même la fenêtre du jeu au premier plan. Au clavier, {keys}.").grid(
            row=2, column=0, sticky="w", pady=(0, 6))

        logbox = ttk.LabelFrame(parent, text="Journal du bot", padding=4)
        logbox.grid(row=3, column=0, sticky="nsew")
        parent.rowconfigure(3, weight=1)
        parent.columnconfigure(0, weight=1)
        self.log = tk.Text(logbox, width=100, height=28, wrap="word", font=("Consolas", 9), state="disabled")
        scroll = ttk.Scrollbar(logbox, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        logbox.rowconfigure(0, weight=1)
        logbox.columnconfigure(0, weight=1)
        for level, color in LEVEL_COLORS.items():
            self.log.tag_configure(level, foreground=color)
        self.log.tag_configure(INTERFACE, foreground="#1f5fa8")

    # État

    def choices(self) -> Choices:
        return Choices(**{name: var.get() for name, var in self.vars.items()})

    def update_view(self) -> None:
        """Ligne de commande, problèmes, et cases utiles ou non selon le mode choisi."""
        choices = self.choices()
        self.command.set(display_command(build_args(choices, self.settings)))
        self.problems.configure(text="\n".join(validate(choices, self.settings, self.titles, self.options,
                                                         len(self.screens) - 1 if self.screens else None,
                                                         self.dungeons)))
        for mode, entry in self.entries.values():
            entry.state(["!disabled"] if choices.mode == mode else ["disabled"])
        self.combat_only.state(["!disabled"] if choices.mode in COMBAT_MODES else ["disabled"])
        self.notifications.state(["!disabled"] if choices.team else ["disabled"])
        self.server.state(["!disabled"] if choices.connect else ["disabled"])

    def refresh_windows(self) -> None:
        """Fenêtres du jeu ouvertes : personnages proposés comme principal ; écrans détectés."""
        self.refresh_screens()
        title = self.vars["title"].get().strip()
        try:
            self.titles = detect_windows(title)
        except Exception as error:  # pywin32 : ne doit pas empêcher d'ouvrir la fenêtre
            self.titles = None
            self.found.configure(text=f"recherche impossible ({error})")
        else:
            names = character_names(self.titles, title)
            login = sum(1 for window in self.titles if not character_name(window, title))
            text = ", ".join(names) if names else "aucun (jeu fermé ou pas connecté)"
            if login:
                text += f" ; {login} fenêtre(s) sans personnage (écran de connexion ?)"
            self.found.configure(text=text)
            self.open_names = names
            self.leader.configure(values=names)
            # Mode équipe choisi tout seul : dès que plusieurs personnages sont ouverts (demande du 03/10).
            self.vars["team"].set(len(names) >= 2)
            leader = self.vars["leader"].get().strip()
            connecting = self.vars["connect"].get() and leader in parse_names(self.vars["characters"].get())
            if names and leader not in names and not connecting:  # un personnage à connecter reste le principal
                self.vars["leader"].set(names[0])
        self.refresh_characters()
        self.update_view()

    def _show_state(self, status: str = "") -> None:
        running = self.runner.running
        self.launch_button.state(["disabled"] if running else ["!disabled"])
        self.stop_button.state(["!disabled"] if running else ["disabled"])
        self.stop_button.configure(text="Forcer l'arrêt" if self.runner.stopping else f"Arrêter ({self.stop_key.upper()})")
        if running:
            status = f"{'Arrêt en cours' if self.runner.stopping else 'Bot en marche'} (processus {self.runner.pid})"
        self.status.configure(text=status or "Bot arrêté")

    # Actions

    def launch(self) -> None:
        if self.runner.running:
            return
        self.refresh_windows()
        choices = self.choices()
        errors = validate(choices, self.settings, self.titles, self.options,
                          len(self.screens) - 1 if self.screens else None)
        if errors:
            messagebox.showerror("Lancement impossible", "\n\n".join(errors), parent=self.root)
            return
        self._save(choices)
        args = build_args(choices, self.settings)
        try:
            self.runner.start(bot_command(args))
        except OSError as error:
            messagebox.showerror("Lancement impossible", f"Le bot n'a pas pu démarrer : {error}", parent=self.root)
            return
        self.note(f"{time.strftime('%H:%M:%S')} Lancement : {display_command(args)}")
        self._show_state()

    def stop(self) -> None:
        self.runner.request_stop(self.stop_key)
        self._show_state()

    def copy_command(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.command.get())
        if not self.runner.running:
            self.status.configure(text="Commande copiée")

    def close(self) -> None:
        self._save(self.choices())
        if not self.runner.running:
            self.root.destroy()
            return
        if not messagebox.askokcancel("Fermer", "Le bot tourne encore : l'arrêter et fermer cette fenêtre ?",
                                      parent=self.root):
            return
        self._closing = True  # _tick ferme la fenêtre une fois le bot arrêté
        if not self.runner.stopping:
            self.runner.request_stop(self.stop_key)
        self._show_state()

    def note(self, text: str) -> None:
        """Ligne de cette fenêtre dans le journal."""
        self._append(f"{INTERFACE} {text}")

    # Journal

    def _tick(self) -> None:
        code = self.runner.check()
        for line in self.runner.lines():
            self._append(line)
        if code is not None:
            result = "terminé normalement" if code == 0 else f"terminé (code {code})"
            self.note(f"{time.strftime('%H:%M:%S')} Bot {result}")
            self._show_state(f"Bot {result}")
            if self._closing:
                self.root.destroy()
                return
        elif self.runner.running:
            self._show_state()
        self.root.after(TICK_MS, self._tick)

    def _append(self, line: str) -> None:
        if line.startswith(INTERFACE):
            tag = INTERFACE
        else:  # « 12:00:00.123 WARNING message » (bot/debug/recorder.py)
            tag = next((level for level in LEVEL_COLORS if f" {level} " in line[:40]), "")
        at_end = self.log.yview()[1] >= 0.999  # ne fait défiler que si l'on lisait déjà la fin
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n", tag)
        excess = int(self.log.index("end-1c").split(".")[0]) - 1 - LOG_LINES
        if excess > 0:
            self.log.delete("1.0", f"{excess + 1}.0")
        self.log.configure(state="disabled")
        if at_end:
            self.log.see("end")

    def _save(self, choices: Choices) -> None:
        try:
            save_choices(choices, self.save_path)
        except OSError as error:
            self.note(f"Choix pas enregistrés ({self.save_path}) : {error}")

    def _on_error(self, kind, value, trace) -> None:
        text = "".join(traceback.format_exception(kind, value, trace))
        messagebox.showerror("Erreur de l'interface", text[-2000:], parent=self.root)


def main() -> int:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # texte net sur un écran mis à l'échelle
    except (AttributeError, OSError):
        pass
    try:
        settings, problem = load_settings(), ""
    except Exception as error:  # fichier absent ou YAML invalide : le bot lui-même ne démarrerait pas
        settings, problem = {}, f"config/settings.yaml illisible : {error}"
    root = tk.Tk()
    app = App(root, settings, load_choices(GUI_PATH, default_choices(settings)))
    if problem:
        app.note(problem)
    root.mainloop()
    return 0
