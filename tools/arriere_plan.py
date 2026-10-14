"""Test : peut-on jouer une fenêtre de Dofus Rétro en arrière-plan, sans prendre la souris ni le clavier ?

Piste 1 du 08/10 : l'utilisateur veut se servir de son PC pendant que le bot tourne. Au lieu de bouger la vraie souris
et de passer chaque fenêtre au premier plan, le bot enverrait clics et touches directement à la fenêtre du jeu
(messages Windows) et la lirait avec PrintWindow, qui marche même quand elle est recouverte.

Le client Rétro est une application Electron (Chromium) qui fait tourner le jeu Flash : rien ne garantit qu'il réagit
à ces messages (clavier sans focus, survol) ni qu'il continue de se dessiner une fois recouvert. Deux phases :
A. fenêtre du jeu visible mais sans le focus : chaque façon d'envoyer un clic, une touche (Échap, Z tenu), un survol
   (groupe de monstres) est essayée ;
B. fenêtre recouverte (fenêtre grise) entièrement, puis sauf une bande : reçoit-elle les clics, se redessine-t-elle ?
(08/10, premier essai : l'appli Claude recouvrait la fenêtre du jeu, les deux questions se mélangeaient ; deuxième
essai, fenêtre visible : aucun des 8 clics n'a rien fait. Hypothèse : à chaque « souris ici », Windows signale aussitôt
au jeu que la vraie souris n'est pas sur sa fenêtre (WM_MOUSELEAVE), entre « souris ici » et « bouton enfoncé ». D'où
le clic envoyé d'un bloc, et un diagnostic, la vraie souris posée sur le jeu. Troisième essai : clic d'un bloc, Échap et
Z marchent ; recouvert, le jeu se redessine et reçoit Échap, mais pas le clic. Échap envoyé sans fenêtre ouverte a
ouvert le menu principal : jamais plus Échap à l'aveugle.)

À lancer soi-même, hors combat, UNE fenêtre du jeu maximisée sur l'écran 1920 x 1080, entièrement visible (rien
par-dessus : mets les autres fenêtres sur l'autre écran), personnage sur une carte tranquille, de préférence avec des
groupes de monstres visibles (test de la touche Z) :
    .venv\\Scripts\\python -m tools.arriere_plan
    .venv\\Scripts\\python -m tools.arriere_plan --fenetre Gela      (si plusieurs fenêtres du jeu sont ouvertes)

Pendant le test (~1 min) : ne clique pas dans la fenêtre du jeu et garde ta souris sur l'autre écran, sauf si l'outil
te demande de la poser sur le jeu (diagnostic, si aucun clic n'a marché). Gestes envoyés au jeu : ouvrir la fenêtre
des sorts (bouton baguette) et la fermer (Échap, ou sa croix), tenir Z, survoler un groupe de monstres.
Captures et rapport dans logs/arriere_plan_<date>/.
"""

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np
import win32api
import win32con
import win32gui

from bot.capture.printwindow import print_window
from bot.capture.screen import ScreenCapture
from bot.capture.window import GameWindow, enable_dpi_awareness, find_windows
from bot.config import ROOT, load_settings
from bot.perception.frame import Frame
from bot.brain.engage import hover_points
from bot.perception.groups import GroupTooltip, find_group_tooltips
from bot.perception.popups import find_unknown_window
from bot.perception.spellbook import BUTTON_ZONE, find_close_button, find_spell_window, find_spells_button
from bot.perception.ui import region_changed

SPELLS_BUTTON = (1512, 815)  # bouton baguette, si le modèle ne le trouve pas (coordonnées de référence)
VK_Z = 0x5A
SEND_TIMEOUT_MS = 500
COVER_STRIP = 40  # B2 : bande de la fenêtre du jeu laissée visible à droite (la fenêtre n'est plus « entièrement cachée »)


@dataclass
class Report:
    folder: object
    lines: list[str] = field(default_factory=list)
    results: dict[str, str] = field(default_factory=dict)
    count: int = 0

    def say(self, text: str) -> None:
        print(text)
        self.lines.append(text)

    def save(self, image: np.ndarray | None, name: str) -> str:
        if image is None:
            return "(pas d'image)"
        self.count += 1
        path = self.folder / f"{self.count:02d}_{name}.png"
        cv2.imencode(".png", image)[1].tofile(str(path))
        return path.name

    def result(self, key: str, value: str, text: str) -> None:
        self.results[key] = value
        self.say(f"  -> {value.upper()} : {text}")


def looks_blank(image: np.ndarray | None) -> bool:
    return image is None or float(image.std()) < 3


def children(hwnd: int) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    win32gui.EnumChildWindows(hwnd, lambda child, _: found.append((child, win32gui.GetClassName(child))), None)
    return found


def covering_windows(game: GameWindow) -> list[str]:
    """Titres des fenêtres qui recouvrent la zone de jeu (sous quelques points de la zone, une autre fenêtre)."""
    rect = game.client_rect()
    titles = []
    for fx, fy in ((0.5, 0.5), (0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8)):
        point = (rect.left + round(rect.width * fx), rect.top + round(rect.height * fy))
        hwnd = win32gui.WindowFromPoint(point)
        top = win32gui.GetAncestor(hwnd, win32con.GA_ROOT) if hwnd else 0
        if top and top != game.hwnd:
            titles.append(win32gui.GetWindowText(top) or win32gui.GetClassName(top))
    return list(dict.fromkeys(titles))


def confirm(what: str) -> bool:
    """Demande d'appuyer sur Entrée avant d'agir sur le jeu. Sans clavier (lancé par un programme, entrée vide) : non.
    08/10 : l'outil, lancé pour vérifier qu'il démarrait, a cliqué dans les fenêtres du jeu ouvertes à ce moment."""
    try:
        input(f"{what}\nAppuie sur Entrée pour commencer (Ctrl+C pour annuler) : ")
    except (EOFError, KeyboardInterrupt):
        print("\nAnnulé : rien n'a été envoyé au jeu.")
        return False
    return True


def lparam(x: int, y: int) -> int:
    return (y << 16) | (x & 0xFFFF)


class Poster:
    """Gestes envoyés à une fenêtre par messages : ni la vraie souris ni le focus ne bougent.

    `send` : SendMessage (traité tout de suite) au lieu de PostMessage (mis en file) ; `activate` : messages
    d'activation envoyés d'abord (la fenêtre se croit active, sans passer au premier plan)."""

    def __init__(self, game: GameWindow, target: int, send: bool = False, activate: bool = False,
                 burst: bool = False) -> None:
        self.game, self.target, self.send, self.activate, self.burst = game, target, send, activate, burst

    def _message(self, message: int, wparam: int, lp: int) -> None:
        if self.send:
            win32gui.SendMessageTimeout(self.target, message, wparam, lp, win32con.SMTO_ABORTIFHUNG, SEND_TIMEOUT_MS)
        else:
            win32api.PostMessage(self.target, message, wparam, lp)

    def wake(self) -> None:
        if self.activate:
            win32api.PostMessage(self.game.hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
            win32api.PostMessage(self.target, win32con.WM_SETFOCUS, 0, 0)
            time.sleep(0.05)

    def _point(self, client_point: tuple[float, float]) -> int:
        """Point de la zone de jeu (fenêtre principale) dans les coordonnées de la fenêtre cible."""
        screen = win32gui.ClientToScreen(self.game.hwnd, (round(client_point[0]), round(client_point[1])))
        x, y = win32gui.ScreenToClient(self.target, screen)
        return lparam(x, y)

    def move(self, point: tuple[float, float]) -> None:
        self._message(win32con.WM_MOUSEMOVE, 0, self._point(point))

    def click(self, point: tuple[float, float], move: bool = True) -> None:
        """`move` faux : bouton enfoncé puis relâché seulement, sans « souris ici » avant (rien qui déclenche le
        signal « souris partie » de Windows)."""
        self.wake()
        where = self._point(point)
        if move:
            self._message(win32con.WM_MOUSEMOVE, 0, where)
        if move and not self.burst:
            time.sleep(0.05)
        self._message(win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, where)
        if not self.burst:
            time.sleep(0.06)
        self._message(win32con.WM_LBUTTONUP, 0, where)

    def key_down(self, vk: int, repeat: bool = False) -> None:
        scan = win32api.MapVirtualKey(vk, 0)
        self._message(win32con.WM_KEYDOWN, vk, 1 | (scan << 16) | ((1 << 30) if repeat else 0))

    def key_up(self, vk: int) -> None:
        scan = win32api.MapVirtualKey(vk, 0)
        self._message(win32con.WM_KEYUP, vk, 1 | (scan << 16) | (1 << 30) | (1 << 31))

    def press(self, vk: int) -> None:
        self.wake()
        self.key_down(vk)
        time.sleep(0.06)
        self.key_up(vk)

    def label(self, names: dict[int, str]) -> str:
        return (f"{'SendMessage' if self.send else 'PostMessage'} vers {names[self.target]}"
                f"{', d’un bloc' if self.burst else ''}{', activation simulée' if self.activate else ''}")


class Cover:
    """Fenêtre grise posée sur celle du jeu (tout en haut) : elle la recouvre, sauf `strip` px à droite."""

    def __init__(self, game: GameWindow, strip: int = 0) -> None:
        import tkinter as tk

        left, top, right, bottom = win32gui.GetWindowRect(game.hwnd)
        self.root = tk.Tk()
        self.root.title("Test arrière-plan : ne touche pas")
        self.root.overrideredirect(True)
        self.root.geometry(f"{right - left - strip}x{bottom - top}+{left}+{top}")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#505050")
        tk.Label(self.root, text="Test du jeu recouvert en cours (~10 s)\nNe touche pas à cette fenêtre",
                 fg="white", bg="#505050", font=("Segoe UI", 20)).pack(expand=True)
        self.root.update()

    def wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.03)

    def close(self) -> None:
        self.root.destroy()


def look(game: GameWindow, visible: bool) -> np.ndarray | None:
    """Ce que montre la fenêtre : capture de l'écran si elle est visible (ce que voit le bot aujourd'hui), sinon
    PrintWindow."""
    return ScreenCapture().grab(game.client_rect()) if visible else print_window(game.hwnd)


def spells_open(image: np.ndarray | None) -> bool:
    return image is not None and find_spell_window(Frame.of(image)) is not None


def try_each(report: Report, posters: list[Poster], names: dict[int, str], action: Callable[[Poster], None],
             done: Callable[[np.ndarray | None], bool], see: Callable[[], np.ndarray | None], name: str,
             wait: float = 1.0, pause: Callable[[float], None] = time.sleep) -> Poster | None:
    """Essaie le geste avec chaque façon de l'envoyer, jusqu'à ce que `done` le voie sur la capture. `pause` : attente
    (celle du cache gris, qui doit rester à l'écran)."""
    for number, poster in enumerate(posters, 1):
        print(f"  essai {number}/{len(posters)}...", end="\r")
        action(poster)
        pause(wait)
        image = see()
        worked = done(image)
        if not worked:  # second regard : un geste en retard ne doit pas passer pour celui de la façon suivante
            pause(0.5)
            image = see()
            worked = done(image)
        report.say(f"  {poster.label(names)} : {report.save(image, name)} -> {'oui' if worked else 'rien'}")
        if worked:
            return poster
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fenetre", help="partie du titre de la fenêtre à tester (nom du personnage)")
    args = parser.parse_args()
    enable_dpi_awareness()

    windows = find_windows(load_settings()["window"]["title"])
    if args.fenetre:
        windows = [w for w in windows if args.fenetre.lower() in w.title.lower()]
    if not windows:
        print("Aucune fenêtre du jeu trouvée (personnage connecté ?).")
        return 1
    game = windows[0]
    if win32gui.IsIconic(game.hwnd):
        print(f"« {game.title} » est réduite : remets-la à l'écran (sans la réduire), puis relance.")
        return 1
    covering = covering_windows(game)
    if covering:
        print(f"La fenêtre du jeu est recouverte par : {', '.join(covering)}.\nDéplace ces fenêtres sur l'autre écran "
              "(la fenêtre du jeu doit être entièrement visible au départ), puis relance.")
        return 1
    if win32gui.GetForegroundWindow() == game.hwnd:
        print("La fenêtre du jeu a le focus : clique dans une autre fenêtre (sur l'autre écran), puis relance.")
        return 1

    if not confirm(f"Test sur « {game.title} » : clics et touches envoyés à cette fenêtre (~1 min)."):
        return 1
    folder = ROOT / "logs" / f"arriere_plan_{datetime.now():%Y%m%d_%H%M%S}"
    folder.mkdir(parents=True)
    report = Report(folder)
    rect = game.client_rect()
    report.say(f"Fenêtre testée : {game.title} ; zone de jeu {rect.width} x {rect.height} en ({rect.left}, {rect.top})")
    kids = children(game.hwnd)
    report.say("Fenêtres filles : " + (", ".join(f"{c} ({name})" for c, name in kids) or "aucune"))
    names = {game.hwnd: "la fenêtre principale"} | {c: f"la fille {name}" for c, name in kids}
    targets = [c for c, name in kids if name == "Chrome_RenderWidgetHostHWND"] + \
        [c for c, name in kids if name != "Chrome_RenderWidgetHostHWND"] + [game.hwnd]
    # Par fenêtre : d'un bloc (avant le signal « souris partie » : a marché le 08/10), mis en file comme une main, d'un
    # bloc traité tout de suite, d'un bloc avec activation simulée.
    posters = [Poster(game, target, send, activate, burst) for target in targets
               for send, activate, burst in ((False, False, True), (False, False, False), (True, False, True),
                                             (False, True, True))]

    def visible() -> np.ndarray | None:
        return look(game, True)

    def printed() -> np.ndarray | None:
        return print_window(game.hwnd)

    # --- A. Fenêtre visible, sans le focus ---
    report.say("\nA1. Captures de la fenêtre visible")
    screen, shot = ScreenCapture().grab(rect), print_window(game.hwnd)
    same = shot is not None and screen.shape == shot.shape and \
        float(np.abs(screen.astype(int) - shot.astype(int)).mean()) < 4
    report.say(f"  capture d'écran {report.save(screen, 'ecran_depart')}, PrintWindow {report.save(shot, 'pw_depart')}")
    report.result("printwindow", "oui" if same else "non", "PrintWindow donne la même image que l'écran" if same else
                  "PrintWindow ne donne pas l'image de l'écran")
    frame = Frame.of(screen)
    if spells_open(screen) or find_unknown_window(frame) is not None:
        report.say("  Une fenêtre est déjà ouverte dans le jeu (sorts, menu...) : ferme-la puis relance le test.")
        return finish(report)
    point = find_spells_button(frame) or frame.layout.point(*SPELLS_BUTTON)

    report.say("\nA2. Clic sur le bouton baguette (ouvre la fenêtre des sorts)")
    clicker = try_each(report, posters, names, lambda p: p.click(point), spells_open, visible, "clic")
    report.result("clic", "oui" if clicker else "non", f"le jeu réagit au clic : {clicker.label(names)}" if clicker else
                  "aucune façon d'envoyer un clic n'a ouvert la fenêtre des sorts")
    if clicker is None:
        diagnose_cursor(report, game, posters, names, point, visible)
        return finish(report)

    report.say("\nA3. Touche Échap (ferme la fenêtre des sorts)")
    presser = try_each(report, posters, names, lambda p: p.press(win32con.VK_ESCAPE),
                       lambda image: image is not None and not spells_open(image), visible, "echap", wait=0.8)
    report.result("clavier", "oui" if presser else "non", f"le jeu réagit aux touches : {presser.label(names)}"
                  if presser else "aucune façon d'envoyer Échap n'a fermé la fenêtre des sorts")
    if presser is None:
        close_spells(clicker, visible)
    keys = presser or clicker

    report.say("\nA4. Touche Z tenue (infobulles des groupes de monstres)")
    tooltips = hold_z(report, keys, visible)

    report.say("\nA5. Survol d'un groupe de monstres (sans Z : son infobulle doit apparaître)")
    hover_group(report, posters, names, tooltips, frame, visible)

    # --- B. Fenêtre recouverte ---
    for strip, title in ((0, "B1. Fenêtre entièrement recouverte"),
                         (COVER_STRIP, f"B2. Fenêtre recouverte sauf une bande de {COVER_STRIP} px à droite")):
        report.say(f"\n{title} : clic sur le bouton baguette")
        cover = Cover(game, strip)
        try:
            cover.wait(1.5)  # le temps que Windows et Chromium constatent que la fenêtre est recouverte
            opener = try_each(report, posters, names, lambda p: p.click(point), spells_open, printed,
                              "recouverte_clic" if not strip else "bande_clic", pause=cover.wait)
            report.result("recouverte" if not strip else "bande", "oui" if opener else "non",
                          f"le jeu recouvert reçoit le clic et se redessine : {opener.label(names)}" if opener else
                          "le jeu recouvert ne réagit à aucun clic (ou ne se redessine pas)")
            if opener is not None:
                keys.press(win32con.VK_ESCAPE)  # la fenêtre des sorts est ouverte : Échap la ferme
                cover.wait(1.0)
                still = printed()
                report.say(f"  Échap, puis PrintWindow : {report.save(still, 'recouverte_echap')} -> fenêtre des sorts "
                           f"{'toujours ouverte' if spells_open(still) else 'fermée'}")
        finally:
            cover.close()
        time.sleep(1.0)
        final = visible()
        if spells_open(final):
            close_spells(keys, visible)
        report.say(f"  fenêtre découverte : {report.save(final, 'decouverte')}")
    return finish(report)


def close_spells(poster: Poster, see: Callable[[], np.ndarray | None]) -> None:
    """Referme la fenêtre des sorts si elle est ouverte (sa croix), jamais Échap à l'aveugle (sur la carte, Échap ouvre
    le menu principal : 08/10)."""
    image = see()
    if spells_open(image):
        close = find_close_button(Frame.of(image))
        if close is not None:
            poster.click(close)
            time.sleep(0.8)


def hold_z(report: Report, poster: Poster, see: Callable[[], np.ndarray | None]) -> list[GroupTooltip]:
    """Z tenu : infobulles des groupes ; renvoie celles vues avec Z."""
    before = see()
    groups_before = len(find_group_tooltips(Frame.of(before)))
    poster.wake()
    poster.key_down(VK_Z)
    for _ in range(6):
        time.sleep(0.1)
        poster.key_down(VK_Z, repeat=True)
    after = see()
    poster.key_up(VK_Z)
    tooltips = find_group_tooltips(Frame.of(after))
    report.say(f"  {report.save(after, 'touche_z')} : {len(tooltips)} infobulle(s) avec Z, {groups_before} sans")
    report.result("touche_z", "oui" if len(tooltips) > groups_before else "?" if not tooltips else "non",
                  "Z tenu montre les groupes" if len(tooltips) > groups_before else
                  "aucun groupe sur la carte ? (rien à conclure)" if not tooltips else "Z sans effet")
    time.sleep(0.6)  # infobulles parties
    return tooltips


def hover_group(report: Report, posters: list[Poster], names: dict[int, str], tooltips: list[GroupTooltip],
                frame: Frame, see: Callable[[], np.ndarray | None]) -> None:
    """Survol d'un groupe à l'endroit où le bot le cherche (sous son infobulle), avec « souris ici » répété toutes les
    40 ms : Windows signale aussitôt au jeu que la vraie souris n'est pas là, chaque nouveau message l'y ramène."""
    if not tooltips:
        report.result("survol", "?", "pas d'infobulle de groupe vue avec Z : pas de groupe à survoler")
        return
    points = hover_points(tooltips[0], frame)[:3]
    for poster in posters[:4]:
        for point in points:
            end, shown = time.monotonic() + 1.0, None
            while time.monotonic() < end:
                poster.move(point)
                time.sleep(0.04)
                if shown is None and time.monotonic() > end - 0.4:
                    shown = see()  # capture pendant le survol, après ~0,6 s
            if shown is not None and find_group_tooltips(Frame.of(shown)):
                report.say(f"  {poster.label(names)} en ({point[0]:.0f}, {point[1]:.0f}) : "
                           f"{report.save(shown, 'survol_groupe')} -> infobulle")
                report.result("survol", "oui", f"le survol envoyé montre l'infobulle du groupe : {poster.label(names)}")
                poster.move(frame.layout.point(400, 150))
                return
        report.say(f"  {poster.label(names)} : {report.save(shown, 'survol_groupe')} -> rien sur {len(points)} endroits")
    report.result("survol", "non", "aucun survol envoyé n'a montré l'infobulle du groupe")


def diagnose_cursor(report: Report, game: GameWindow, posters: list[Poster], names: dict[int, str],
                    point: tuple[float, float], visible: Callable[[], np.ndarray | None]) -> None:
    """Aucun clic envoyé n'a marché : est-ce parce que la vraie souris n'est pas sur la fenêtre du jeu ? L'utilisateur
    pose sa souris sur le jeu, loin du bouton, sans la bouger ; mêmes clics envoyés. Pas une solution : un diagnostic."""
    report.say("\nA2 bis. Diagnostic : les mêmes clics, ta vraie souris posée sur la fenêtre du jeu")
    print("  Pose ta souris sur la CARTE de la fenêtre du jeu (loin des boutons du bas), ne clique pas, ne la bouge "
          "plus jusqu'à la fin. Début dans 8 s...")
    for left in range(8, 0, -1):
        print(f"  {left} s...", end="\r")
        time.sleep(1)
    cursor = win32api.GetCursorPos()
    under = win32gui.WindowFromPoint(cursor)
    if not under or win32gui.GetAncestor(under, win32con.GA_ROOT) != game.hwnd:
        report.result("souris_sur_le_jeu", "?", "ta souris n'était pas sur la fenêtre du jeu : diagnostic sauté")
        return
    clicker = try_each(report, posters[:2], names, lambda p: p.click(point), spells_open, visible, "clic_souris_jeu")
    moved = win32api.GetCursorPos() != cursor
    report.result("souris_sur_le_jeu", "oui" if clicker else "non",
                  "avec la vraie souris sur le jeu, le clic envoyé marche : c'est la position de la vraie souris qui "
                  "bloque" if clicker else "même avec la vraie souris sur le jeu, rien : le jeu ignore ces clics "
                  "envoyés" + (" (ta souris a bougé pendant l'essai)" if moved else ""))
    if clicker is not None:
        print("  Tu peux reprendre ta souris. La fenêtre des sorts est ouverte : je la referme.")
        clicker.click(find_close_button(Frame.of(visible())) or point)


def finish(report: Report) -> int:
    report.say("\nRésumé : " + ", ".join(f"{k} {v}" for k, v in report.results.items()))
    report.say("Test terminé : il s'arrête tout seul quand il a fini ses essais (bouger ta souris sur l'autre écran "
               "n'y change rien ; l'outil ne bouge jamais ta souris).")
    (report.folder / "rapport.txt").write_text("\n".join(report.lines), encoding="utf-8")
    print(f"\nRapport et captures : {report.folder.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
