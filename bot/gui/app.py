"""Fenêtre de lancement du bot (tkinter) : cocher les options, voir la ligne de commande, lancer et arrêter le bot,
suivre son journal. Ce qui ne dépend pas de l'affichage (commande, validation, choix mémorisés, processus du bot) :
bot/gui/launch.py.

Depuis la racine du projet :
    .venv\\Scripts\\pythonw -m bot.gui     sans fenêtre de console
    .venv\\Scripts\\python -m bot.gui      avec, pour voir les erreurs éventuelles de l'interface elle-même

Au démarrage, le bot met lui-même la fenêtre du jeu au premier plan (en équipe : celle du principal), sauf en
arrière-plan : il ne passe alors aucune fenêtre devant.
"""

import ctypes
import time
import tkinter as tk
import traceback
from dataclasses import fields
from pathlib import Path
from tkinter import messagebox, ttk

from bot.brain.memory import CharacterMemory
from bot.capture.window import character_name
from bot.config import ROOT, load_settings
from bot.connection.characters import FORGOTTEN, forget_character, known_characters, memory_dir, parse_names
from bot.gui.launch import (COMBAT_MODES, GUI_PATH, IMAGES, MODES, RESUMES, SERVERS, WORLDS, BotProcess, Choices,
                            bot_command, build_args, character_names, default_choices, deletion_problems,
                            detect_screens, detect_windows, display_command, known_dungeons, load_choices,
                            main_options, save_choices, stop_key, team_names, validate)

LOG_LINES = 1000  # lignes du journal gardées à l'écran
TICK_MS = 100  # lecture du journal et de l'état du bot
GREY = "#6b6b6b"
RED = "#c00000"
LEVEL_COLORS = {"WARNING": "#b35c00", "ERROR": RED, "CRITICAL": RED}
INTERFACE = "[interface]"  # lignes écrites par cette fenêtre (pas par le bot)
READING_LABELS = {"controle": "lire à l'écran", "memoire": "reprendre la dernière session"}  # READINGS, en court
NO_PLACEMENT = ("aucun", "Ne pas déplacer les fenêtres")  # window.ecran vide
WRAP = 400  # px (avant mise à l'échelle) : largeur des textes gris de la colonne des choix

HELP = {
    "carte": "Attaque les groupes de monstres de la carte où se trouve le personnage, sans en changer.",
    "zone": "Combats en restant dans la sous-zone (Prairie, Forêt...) de la carte X,Y, où le personnage doit se "
            "trouver ; carte voisine de la même zone quand il n'y a plus de groupe. Vide : carte lue à l'écran.",
    "aller": "Sans combat : de la carte de départ à la carte d'arrivée, par le plus court chemin. Coordonnées en haut "
             "à gauche du jeu ; départ vide : lu à l'écran.",
    "explore": "Sans combat : visite toutes les cartes de la région. Départ vide : lu à l'écran.",
    "test": "Sans combat : s'éloigne de 2 cartes puis revient sur la carte de départ (vérifie les déplacements).",
    "donjon": "En équipe : l'équipe devant l'entrée du donjon choisi y entre (dalles, clef au PNJ), fait les combats "
              "de salle en salle, ressort par le PNJ de fin, et recommence autant de fois que de passages "
              "(--donjon, --passages). Donjons connus : config/donjons_entree.yaml.",
    "resume": "Reprise du farm de donjon. Début : chacun est situé d'après son décor et ne fait que ce qui lui manque. "
              "Une étape de l'entrée (entrée, salle des dalles, soleil, PNJ) : toute l'équipe reprend à cette étape. "
              "Salles : l'équipe déjà passée, aux combats à partir de la salle N (--donjon nom:etape, nom:salles:N).",
    "combat_only": "Le bot n'attaque pas les groupes : il joue les combats que tu lances toi-même (--combat-only).",
    "observe": "Le bot regarde et écrit dans le journal ce qu'il voit, sans aucun clic ni aucune touche (--observe).",
    "monde": "Où se trouve le personnage : Incarnam ou le Monde des Douze, qui ont chacun leurs coordonnées (--monde).",
    "team": "Jeu en équipe dès 2 personnages cochés ; sans case cochée, dès 2 fenêtres de personnage ouvertes. Le "
            "principal attaque et choisit le chemin, les autres le rejoignent en combat et le suivent (--equipe).",
    "leader": "Le personnage principal en équipe (--principal). En solo : la fenêtre jouée quand plusieurs jeux sont "
              "ouverts.",
    "refresh": "Cherche à nouveau les fenêtres du jeu ouvertes (titre « <Nom> - Dofus Retro ») et les écrans.",
    "notifications": "En équipe : qui joue, d'après les notifications Windows que le jeu envoie au début de chaque "
                     "tour (option du jeu « Notifications en arrière-plan »), vérifiées au timer (--notifications).",
    "burst": "Rafale (à l'essai) : quand le même sort est relancé sur la même cible, les lancers s'enchaînent sans "
             "nouveau plan entre deux (combat.burst de config/settings.yaml). Au premier plan seulement.",
    "verbose": "Écrit aussi le détail dans le journal : clics, combattants (--verbose).",
    "images": "Captures gardées dans logs/ : toutes (tests), seulement celles des problèmes, ou aucune. Le bot regarde "
              "toujours l'écran ; ceci ne règle que ce qui est enregistré (logs.images de config/settings.yaml).",
    "background": "Arrière-plan : le bot envoie ses gestes directement aux fenêtres du jeu, sans toucher à ta souris ni "
                  "à ton clavier, ni passer une fenêtre devant. Les fenêtres du jeu peuvent être recouvertes (vidéo, "
                  "autre application), jamais réduites dans la barre des tâches (--arriere-plan).",
    "reading": "Début de session, pour chaque personnage. Lire à l'écran : ouvre la fenêtre des sorts et celles des "
               "caractéristiques, les lit (dégâts des sorts selon Force, Intelligence, Chance, Agilité et les bonus aux "
               "dommages), puis les retient. Reprendre : n'ouvre aucune fenêtre, reprend ce qui a été retenu à la "
               "dernière session (data/personnages) ; un personnage jamais lu est lu à l'écran (--lecture).",
    "limits": "Le bot s'arrête de lui-même après ce nombre de combats (fenêtres de résultat fermées) ou de minutes (hors "
              "combat : un combat commencé est fini). Vide : sans limite (--combats, --minutes).",
    "connect": "Connexion automatique : le bot mène lui-même en jeu les fenêtres restées à l'écran de connexion (serveur, "
               "puis personnage : ceux cochés, sinon le dernier joué), reforme le groupe par la fenêtre « Amis » du "
               "principal et replie son cadre ; pendant le jeu, il reconnecte un personnage déconnecté (inactivité). "
               "S'il manque un personnage après les fenêtres déjà ouvertes, il passe l'Ankama Launcher devant (il le "
               "lance au besoin) et lance tous les comptes disponibles (Ctrl+8) ; chaque nouvelle fenêtre est "
               "connectée avec un personnage coché, ou refermée (--connexion).",
    "server": "Serveur choisi à la connexion (--serveur).",
    "characters": "Personnages joués : coche-les (--personnages) ; ils limitent l'équipe aux cochés. « (ouvert) » : sa "
                  "fenêtre du jeu est ouverte. Avec la connexion automatique, ceux qui n'ont pas de fenêtre sont "
                  "cherchés dans les comptes que le launcher lance. Clic droit : supprimer de la liste.",
    "add_character": "Un personnage que le bot n'a encore jamais vu : son nom exact, tel qu'écrit dans le jeu.",
    "screen": "Écran où le bot place les fenêtres du jeu au lancement, puis les agrandit. Automatique : l'écran 1080p "
              "(celui des captures de référence, le seul validé). Actualiser : écrans détectés à nouveau (--ecran).",
    "diagnostics": "Pour vérifier le bot : observation sans clic, test de déplacement, journal détaillé, captures.",
    "command": "La commande lancée par le bouton Lancer ; elle peut aussi être copiée dans un terminal ouvert à la "
               "racine du projet.",
    "stop": "Arrête le bot proprement (sa touche d'arrêt, comme au clavier) ; s'il ne s'arrête pas, son processus est "
            "terminé. Deuxième clic : arrêt immédiat.",
}
FOREGROUND = {  # textes qui dépendent de l'arrière-plan (case « Jouer en arrière-plan »)
    False: ("Lance le bot ; il met lui-même la fenêtre du jeu (celle du principal en équipe) au premier plan.",
            "Le bot met lui-même la fenêtre du jeu au premier plan."),
    True: ("Lance le bot en arrière-plan : il ne passe aucune fenêtre du jeu devant, ta souris et ton clavier restent "
           "libres.", "Arrière-plan : le bot ne passe aucune fenêtre devant, souris et clavier libres."),
}


class Infobulle:
    """Aide affichée au survol d'un élément de la fenêtre ; `text` peut changer en cours de route."""

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
        self.memory_dir = memory_dir(settings)  # fichiers des personnages déjà vus (session.memory)
        self.titles: list[str] | None = None  # fenêtres du jeu ouvertes (Actualiser)
        self.screens: list[tuple[str, str]] = []  # écrans détectés (Actualiser) : (valeur de --ecran, libellé)
        self.open_names: list[str] = []  # personnages des fenêtres du jeu ouvertes (Actualiser)
        self.added: list[str] = []  # personnages ajoutés pendant la séance (bouton Ajouter), peut-être sans fichier
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
        self.entries: dict[str, tuple[str, tk.Widget]] = {}  # champ propre à un mode -> (son mode, le champ)
        for row, build in enumerate((self._build_characters, self._build_connection, self._build_activity,
                                     self._build_session, self._build_combat, self._build_diagnostics)):
            build(left).grid(row=row, column=0, sticky="ew", pady=(0, 6))
        left.columnconfigure(0, weight=1)
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

    def _check(self, parent: tk.Widget, text: str, name: str, help_name: str = "") -> ttk.Checkbutton:
        check = ttk.Checkbutton(parent, text=text, variable=self.vars[name])
        Infobulle(check, HELP[help_name or name])
        return check

    def _radios(self, parent: tk.Widget, name: str, values: dict[str, str]) -> ttk.Frame:
        cell = ttk.Frame(parent)
        for value, text in values.items():
            radio = ttk.Radiobutton(cell, text=text, value=value, variable=self.vars[name])
            radio.pack(side="left", padx=(0, 10))
            Infobulle(radio, HELP[name])
        return cell

    def _entry(self, parent: tk.Widget, name: str, width: int, help_name: str) -> ttk.Entry:
        entry = ttk.Entry(parent, textvariable=self.vars[name], width=width)
        Infobulle(entry, HELP[help_name])
        return entry

    def _select(self, parent: tk.Widget, name: str, values: dict[str, str], width: int) -> ttk.Combobox:
        """Liste déroulante par libellés ; le choix `name` garde la valeur (clé de `values`)."""
        shown = tk.StringVar(value=values.get(self.vars[name].get(), ""))
        select = ttk.Combobox(parent, textvariable=shown, values=list(values.values()), state="readonly", width=width)
        keys = {text: key for key, text in values.items()}
        select.bind("<<ComboboxSelected>>", lambda _: self.vars[name].set(keys.get(shown.get(), "")))
        Infobulle(select, HELP[name])
        return select

    def _build_characters(self, parent: tk.Widget) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Personnages", padding=8)
        self.characters_box = ttk.Frame(box)
        self.characters_box.grid(row=0, column=0, sticky="ew")
        line = ttk.Frame(box)
        line.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(line, text="Principal :").pack(side="left")
        self.leader = ttk.Combobox(line, textvariable=self.vars["leader"], width=22)
        self.leader.pack(side="left", padx=(6, 0))
        Infobulle(self.leader, HELP["leader"])
        refresh = ttk.Button(line, text="Actualiser", command=self.refresh_windows)
        refresh.pack(side="right")
        Infobulle(refresh, HELP["refresh"])
        self.found = self._grey(box, "", wrap=WRAP)
        self.found.grid(row=2, column=0, sticky="w", pady=(4, 0))
        add = ttk.Frame(box)
        add.grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.new_character = tk.StringVar()
        entry = ttk.Entry(add, textvariable=self.new_character, width=22)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _: self.add_character())
        Infobulle(entry, HELP["add_character"])
        button = ttk.Button(add, text="Ajouter", command=self.add_character)
        button.pack(side="left", padx=(6, 0))
        Infobulle(button, HELP["add_character"])
        self.team_line = ttk.Label(box, text="")
        self.team_line.grid(row=4, column=0, sticky="w", pady=(6, 0))
        Infobulle(self.team_line, HELP["team"])
        box.columnconfigure(0, weight=1)
        return box

    def refresh_characters(self) -> None:
        """Une case par personnage proposé : ceux cochés, ceux des fenêtres ouvertes, ceux que le bot a déjà vus
        (session.memory), ceux ajoutés pendant la séance ; sur plusieurs colonnes, pour que la fenêtre tienne sur
        l'écran."""
        chosen = parse_names(self.vars["characters"].get())
        names: list[str] = []
        for name in (*chosen, *self.open_names, *known_characters(self.memory_dir), *self.added):
            if name.lower() not in {n.lower() for n in names}:
                names.append(name)
        for child in self.characters_box.winfo_children():
            child.destroy()
        self.character_rows = {}
        opened = {name.lower() for name in self.open_names}
        columns = 2 if len(names) <= 8 else 3 if len(names) <= 15 else 4
        for index, name in enumerate(names):
            checked = tk.BooleanVar(value=name.lower() in {n.lower() for n in chosen})
            text = f"{name} (ouvert)" if name.lower() in opened else name
            check = ttk.Checkbutton(self.characters_box, text=text, variable=checked)
            check.grid(row=index // columns, column=index % columns, sticky="w", padx=(0, 12))
            check.bind("<Button-3>", lambda event, n=name: self._character_menu(event, n))
            Infobulle(check, HELP["characters"])
            checked.trace_add("write", lambda *_: self._characters_changed())
            self.character_rows[name] = checked
        if not names:
            self._grey(self.characters_box, "aucun personnage connu : ajoute-le ci-dessous").grid(row=0, column=0)
        self._characters_changed()

    def add_character(self) -> None:
        name = self.new_character.get().strip()
        if not name:
            return
        self.new_character.set("")
        if name.lower() not in {n.lower() for n in self.added}:
            self.added.append(name)
        chosen = parse_names(self.vars["characters"].get())
        if name.lower() not in {n.lower() for n in chosen}:
            self.vars["characters"].set(",".join((*chosen, name)))  # ajouté : coché
        self.refresh_characters()

    def _characters_changed(self) -> None:
        """Cases cochées -> choix (ligne de commande) ; principal parmi les personnages joués ; jeu en équipe ou non."""
        checked = [name for name, on in self.character_rows.items() if on.get()]
        self.vars["characters"].set(",".join(checked))
        self.leader.configure(values=list(dict.fromkeys(checked + self.open_names)))
        played = team_names(self.vars["characters"].get(), self.open_names)
        leader = self.vars["leader"].get().strip()
        if played and leader.lower() not in {name.lower() for name in played}:
            self.vars["leader"].set(played[0])
        team = len(played) >= 2
        self.vars["team"].set(team)
        if team:
            text = f"Jeu en équipe : {len(played)} personnages ({'cochés' if checked else 'fenêtres ouvertes'})"
        else:
            text = "Jeu en solo (en équipe : coche au moins 2 personnages)"
        self.team_line.configure(text=text)

    def _character_menu(self, event: tk.Event, name: str) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"Supprimer « {name} »…", command=lambda: self.delete_character(name))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def delete_character(self, name: str) -> None:
        """Personnage retiré de la liste ; son fichier (s'il en a un) déplacé dans supprimes/, après confirmation."""
        problems = deletion_problems(name, list(parse_names(self.vars["characters"].get())), self.open_names,
                                     self.vars["leader"].get(), self.runner.running, self.settings)
        if problems:
            messagebox.showerror("Suppression impossible", "\n\n".join(problems), parent=self.root)
            return
        path = CharacterMemory(self.memory_dir).path(name) if self.memory_dir is not None else None
        if path is not None and path.is_file():
            target = path.parent / FORGOTTEN / f"{path.stem}_<date_heure>.json"
            if not messagebox.askyesno(
                    "Supprimer un personnage",
                    f"Supprimer « {name} » de la liste ?\n\nSon fichier {_shown(path)} sera déplacé vers "
                    f"{_shown(target)} (rien n'est effacé : le remettre à sa place pour le retrouver).",
                    parent=self.root):
                return
            try:
                moved = forget_character(name, self.memory_dir)
            except OSError as error:
                messagebox.showerror("Suppression impossible", f"{_shown(path)} n'a pas pu être déplacé : {error}",
                                     parent=self.root)
                return
            self.note(f"« {name} » supprimé de la liste : {_shown(path)} déplacé vers {_shown(moved)}")
        self.added = [n for n in self.added if n.lower() != name.lower()]
        self.refresh_characters()

    def _build_connection(self, parent: tk.Widget) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Connexion", padding=8)
        self._check(box, "Connexion automatique", "connect").pack(side="left")
        ttk.Label(box, text="Serveur :").pack(side="left", padx=(16, 4))
        self.server = ttk.Combobox(box, textvariable=self.vars["server"], values=SERVERS, width=18)
        self.server.pack(side="left")
        Infobulle(self.server, HELP["server"])
        return box

    def _build_activity(self, parent: tk.Widget) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Activité", padding=8)
        rows = {"carte": [], "zone": [("carte X,Y", "zone_xy")],
                "aller": [("départ X,Y", "aller_from"), ("arrivée X,Y", "aller_to")],
                "explore": [("départ X,Y", "explore_from")], "donjon": [("passages", "passages")]}
        notes = {"zone": "vide : lue à l'écran", "explore": "vide : lu à l'écran"}
        for row, (mode, cells) in enumerate(rows.items()):
            radio = ttk.Radiobutton(box, text=MODES[mode], value=mode, variable=self.vars["mode"])
            radio.grid(row=row, column=0, sticky="w", pady=2)
            Infobulle(radio, HELP[mode])
            cell = ttk.Frame(box)
            cell.grid(row=row, column=1, sticky="w")
            if mode == "donjon":
                self._dungeon_select(cell)
            for label, name in cells:
                ttk.Label(cell, text=label).pack(side="left", padx=(8, 3))
                entry = self._entry(cell, name, 4 if name == "passages" else 8, mode)
                entry.pack(side="left")
                self.entries[name] = (mode, entry)
            if mode in notes:
                self._grey(cell, notes[mode]).pack(side="left", padx=(8, 0))
        row = len(rows)
        resume = ttk.Frame(box)
        resume.grid(row=row, column=1, sticky="w", pady=(0, 2))
        ttk.Label(resume, text="reprise").pack(side="left", padx=(8, 3))
        select = self._select(resume, "resume", RESUMES, 24)
        select.pack(side="left")
        self.entries["resume"] = ("donjon", select)
        self.resume_room = self._entry(resume, "resume_room", 3, "resume")
        self.resume_room.pack(side="left", padx=(4, 0))
        world = ttk.Frame(box)
        world.grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(world, text="Monde :").pack(side="left", padx=(0, 8))
        self._radios(world, "monde", WORLDS).pack(side="left")
        self.combat_only = self._check(box, "Je lance les combats moi-même (le bot n'attaque pas les groupes)",
                                       "combat_only")
        self.combat_only.grid(row=row + 2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        return box

    def _dungeon_select(self, cell: ttk.Frame) -> None:
        """Liste des donjons connus (config/donjons_entree.yaml), par leur nom ; le choix garde leur clé (--donjon)."""
        self.dungeons = known_dungeons()
        names = {name: key for key, (name, _) in self.dungeons.items()}
        shown = tk.StringVar(value=self.dungeons.get(self.vars["donjon"].get(), ("",))[0])
        select = ttk.Combobox(cell, textvariable=shown, values=list(names), state="readonly", width=20)
        select.bind("<<ComboboxSelected>>", lambda _: self._pick_dungeon(names.get(shown.get(), "")))
        select.pack(side="left", padx=(8, 0))
        Infobulle(select, HELP["donjon"])
        self.entries["donjon"] = ("donjon", select)

    def _pick_dungeon(self, key: str) -> None:
        """Donjon choisi : son monde coché aussi (Monde), et le farm de donjon."""
        self.vars["donjon"].set(key)
        if key in self.dungeons:
            self.vars["monde"].set(self.dungeons[key][1])
            self.vars["mode"].set("donjon")

    def _build_session(self, parent: tk.Widget) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Session", padding=8)
        reading = ttk.Frame(box)
        reading.grid(row=0, column=0, sticky="w")
        ttk.Label(reading, text="Sorts et caractéristiques :").pack(side="left", padx=(0, 8))
        self._radios(reading, "reading", READING_LABELS).pack(side="left")
        limits = ttk.Frame(box)
        limits.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(limits, text="S'arrêter après").pack(side="left")
        self._entry(limits, "combats", 5, "limits").pack(side="left", padx=(6, 3))
        ttk.Label(limits, text="combats ou").pack(side="left")
        self._entry(limits, "minutes", 5, "limits").pack(side="left", padx=(6, 3))
        ttk.Label(limits, text="minutes").pack(side="left")
        self._grey(limits, "vide : sans limite").pack(side="left", padx=(8, 0))
        self._check(box, "Jouer en arrière-plan (souris et clavier libres)", "background").grid(
            row=2, column=0, sticky="w", pady=(4, 0))
        screen = ttk.Frame(box)
        screen.grid(row=3, column=0, sticky="w", pady=(4, 0))
        ttk.Label(screen, text="Écran du jeu :").pack(side="left", padx=(0, 6))
        self.screen_shown = tk.StringVar()
        self.screen_select = ttk.Combobox(screen, textvariable=self.screen_shown, state="readonly", width=38)
        self.screen_select.bind("<<ComboboxSelected>>", lambda _: self._pick_screen())
        self.screen_select.pack(side="left")
        Infobulle(self.screen_select, HELP["screen"])
        return box

    def refresh_screens(self) -> None:
        """Écrans détectés (plus « Automatique » et « Ne pas déplacer ») ; un choix qui n'existe plus revient à
        Automatique."""
        try:
            self.screens = detect_screens()
        except Exception as error:  # pywin32 : ne doit pas empêcher d'ouvrir la fenêtre
            self.screens = []
            self.note(f"Détection des écrans impossible : {error}")
        values = {value for value, _ in self.screens}
        if self.screens and self.vars["screen"].get().strip().lower() not in values | {"aucun"}:
            self.vars["screen"].set("auto")
        self._screen_labels = dict(self.screens or [("auto", "Automatique (écran 1080p)")])
        self._screen_labels.setdefault(*NO_PLACEMENT)
        current = self.vars["screen"].get().strip().lower()
        self._screen_labels.setdefault(current, f"écran {current}")  # pas détecté : gardé tel quel
        self.screen_select.configure(values=list(self._screen_labels.values()))
        self.screen_shown.set(self._screen_labels[current])

    def _pick_screen(self) -> None:
        keys = {label: value for value, label in self._screen_labels.items()}
        self.vars["screen"].set(keys.get(self.screen_shown.get(), "auto"))

    def _build_combat(self, parent: tk.Widget) -> ttk.LabelFrame:
        box = ttk.LabelFrame(parent, text="Combat", padding=8)
        self.notifications = self._check(box, "Notifications Windows (qui joue, d'après le jeu ; en équipe)",
                                         "notifications")
        self.notifications.grid(row=0, column=0, sticky="w")
        self.burst = self._check(box, "Rafale : même sort enchaîné sur la même cible (premier plan seulement)",
                                 "burst")
        self.burst.grid(row=1, column=0, sticky="w", pady=(4, 0))
        return box

    def _build_diagnostics(self, parent: tk.Widget) -> ttk.Frame:
        """Cadre repliable : observation, test de déplacement, journal détaillé, captures ; ouvert d'emblée si l'un
        d'eux n'est pas à sa valeur habituelle."""
        frame = ttk.Frame(parent)
        base = default_choices(self.settings)
        self.diagnostics_open = (self.vars["mode"].get() == "test" or self.vars["observe"].get()
                                 or self.vars["verbose"].get() or self.vars["images"].get() != base.images)
        self.diagnostics_button = ttk.Button(frame, command=self._toggle_diagnostics, style="Toolbutton")
        self.diagnostics_button.grid(row=0, column=0, sticky="w")
        Infobulle(self.diagnostics_button, HELP["diagnostics"])
        self.diagnostics = box = ttk.Frame(frame, padding=8, relief="groove", borderwidth=2)
        frame.columnconfigure(0, weight=1)
        self._check(box, "Observation seulement : aucun clic, aucune touche", "observe").grid(row=0, column=0,
                                                                                             sticky="w")
        test = ttk.Radiobutton(box, text=f"{MODES['test']} (s'éloigne de 2 cartes puis revient)", value="test",
                               variable=self.vars["mode"])
        test.grid(row=1, column=0, sticky="w", pady=(4, 0))
        Infobulle(test, HELP["test"])
        self._check(box, "Journal détaillé (clics, combattants)", "verbose").grid(row=2, column=0, sticky="w",
                                                                                  pady=(4, 0))
        images = ttk.Frame(box)
        images.grid(row=3, column=0, sticky="w", pady=(4, 0))
        ttk.Label(images, text="Captures gardées :").pack(side="left", padx=(0, 8))
        self._radios(images, "images", IMAGES).pack(side="left")
        self._show_diagnostics()
        return frame

    def _toggle_diagnostics(self) -> None:
        self.diagnostics_open = not self.diagnostics_open
        self._show_diagnostics()

    def _show_diagnostics(self) -> None:
        self.diagnostics_button.configure(text=f"{'▾' if self.diagnostics_open else '▸'} Tests et diagnostic")
        if self.diagnostics_open:
            self.diagnostics.grid(row=1, column=0, sticky="ew")
        else:
            self.diagnostics.grid_remove()

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
        self.launch_help = Infobulle(self.launch_button, FOREGROUND[False][0])
        self.stop_button = ttk.Button(bar, text="", width=18, command=self.stop)
        self.stop_button.pack(side="left", padx=(6, 0))
        Infobulle(self.stop_button, HELP["stop"])
        self.status = ttk.Label(bar, text="")
        self.status.pack(side="left", padx=(12, 0))
        self.keys_note = self._grey(parent, "")
        self.keys_note.grid(row=2, column=0, sticky="w", pady=(0, 6))

        logbox = ttk.LabelFrame(parent, text="Journal du bot", padding=4)
        logbox.grid(row=3, column=0, sticky="nsew")
        parent.rowconfigure(3, weight=1)
        parent.columnconfigure(0, weight=1)
        self.log = tk.Text(logbox, width=96, height=28, wrap="word", font=("Consolas", 9), state="disabled")
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
        """Ligne de commande, problèmes, et cases utiles ou non selon les choix."""
        choices = self.choices()
        self.command.set(display_command(build_args(choices, self.settings)))
        self.problems.configure(text="\n".join(self._problems(choices)))
        for mode, entry in self.entries.values():
            entry.state(["!disabled"] if choices.mode == mode else ["disabled"])
        on = choices.mode == "donjon" and choices.resume == "salles"
        self.resume_room.state(["!disabled"] if on else ["disabled"])
        self.combat_only.state(["!disabled"] if choices.mode in COMBAT_MODES else ["disabled"])
        self.notifications.state(["!disabled"] if choices.team else ["disabled"])
        self.burst.state(["disabled"] if choices.background else ["!disabled"])
        self.server.state(["!disabled"] if choices.connect else ["disabled"])
        launch, note = FOREGROUND[choices.background]
        self.launch_help.text = launch
        keys = f"{self.stop_key.upper()} : arrêt" + (f", {self.pause_key.upper()} : pause (pour reprendre la main)"
                                                     if self.pause_key else "")
        self.keys_note.configure(text=f"{note} Au clavier, {keys}.")

    def _problems(self, choices: Choices) -> list[str]:
        return validate(choices, self.settings, self.titles, self.options,
                        len(self.screens) - 1 if self.screens else None, self.dungeons)

    def refresh_windows(self) -> None:
        """Fenêtres du jeu ouvertes : personnages proposés, jeu en équipe ou non ; écrans détectés."""
        self.refresh_screens()
        title = self.vars["title"].get().strip()
        try:
            self.titles = detect_windows(title)
        except Exception as error:  # pywin32 : ne doit pas empêcher d'ouvrir la fenêtre
            self.titles = None
            self.found.configure(text=f"Recherche des fenêtres du jeu impossible ({error})")
        else:
            self.open_names = character_names(self.titles, title)
            login = sum(1 for window in self.titles if not character_name(window, title))
            if not self.titles:
                text = "Aucune fenêtre du jeu ouverte"
            else:
                text = f"Fenêtres du jeu : {len(self.open_names)} personnage(s) en jeu"
                if login:
                    text += f", {login} sans personnage (écran de connexion ?)"
            self.found.configure(text=text)
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
        errors = self._problems(choices)
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


def _shown(path: Path) -> str:
    """Chemin depuis la racine du projet (data/personnages/...), sinon en entier."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


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
