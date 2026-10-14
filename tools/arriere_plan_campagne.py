"""Campagne : chaque geste en arrière-plan répété plusieurs fois, pour une conclusion globale sur la piste 1.

Suite de tools/arriere_plan.py (un essai par cas, 08/10). Le clic envoyé « d'un bloc » marche, mais pas à tous les
coups (fenêtre recouverte : du premier coup un jour, raté le suivant ; bande visible : au 4e essai). Ici, pour chaque
façon d'envoyer un clic et chaque situation, N clics sur le bouton baguette : la fenêtre des sorts s'ouvre-t-elle, en
combien de temps ; puis Échap la referme (fiabilité du clavier). Aussi : touche Z tenue, durée d'une capture
PrintWindow. Rapport avec tableau et conclusion dans logs/arriere_plan_campagne_<date>/.

Situations, avec la fenêtre du dessus (entièrement visible) : visible ; recouverte par une fenêtre grise ; recouverte
sauf une bande à droite. Avec une 2e fenêtre du jeu ouverte (autre personnage), maximisée sur le même écran, sous la
première : la situation réelle à plusieurs personnages, la fenêtre cachée par une autre fenêtre du jeu ; et des clics
alternés entre les deux fenêtres, comme le bot qui joue un personnage puis l'autre.

Rien n'est installé : pywin32 (déjà utilisé par le bot) et tkinter (fourni avec Python).

À lancer soi-même, hors combat, une ou deux fenêtres du jeu maximisées sur l'écran 1920 x 1080 (celle du dessus
entièrement visible : rien d'autre par-dessus), sans le focus, personnages sur une carte tranquille, groupes de
monstres visibles si possible :
    .venv\\Scripts\\python -m tools.arriere_plan_campagne
    .venv\\Scripts\\python -m tools.arriere_plan_campagne --essais 5     (plus court)

Durée : ~3 min avec une fenêtre, ~5 min avec deux (6 essais). Ne clique pas dans les fenêtres du jeu ; tu peux te
servir de l'autre écran.
"""

import argparse
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import win32con
import win32gui

from bot.capture.window import GameWindow, enable_dpi_awareness, find_windows
from bot.config import ROOT, load_settings
from bot.perception.frame import Frame
from bot.perception.groups import find_group_tooltips
from bot.perception.popups import find_unknown_window
from bot.perception.spellbook import find_close_button, find_spells_button
from tools.arriere_plan import (COVER_STRIP, SPELLS_BUTTON, VK_Z, Cover, Poster, Report, children, confirm,
                                covering_windows, print_window, spells_open)

OPEN_WAIT, LATE_WAIT, CLOSE_WAIT, POLL = 1.5, 0.6, 1.0, 0.12


@dataclass(frozen=True)
class Method:
    key: str
    label: str
    send: bool = False
    activate: bool = False
    move: bool = True


METHODS = (Method("bloc", "d'un bloc (souris ici + bouton, à la suite)"),
           Method("sans_deplacement", "bouton seul, sans « souris ici » avant", move=False),
           Method("active", "d'un bloc, avec réveil de la fenêtre", activate=True),
           Method("direct", "d'un bloc, envoi direct (SendMessage)", send=True))
CONDITION_TITLES = {"visible": "fenêtre visible", "recouverte": "recouverte (gris)",
                    "bande": f"recouverte sauf {COVER_STRIP} px", "dessous": "sous l'autre fenêtre du jeu"}


@dataclass
class Tally:
    tries: int = 0
    opened: int = 0
    late: int = 0  # ouverte, mais après OPEN_WAIT
    delays: list[float] = field(default_factory=list)

    def rate(self) -> float:
        return self.opened / self.tries if self.tries else 0.0


@dataclass
class Target:
    """Une fenêtre du jeu : sa fenêtre interne qui reçoit les gestes, le bouton baguette."""

    game: GameWindow
    render: int
    point: tuple[float, float]

    @property
    def name(self) -> str:
        return self.game.title.split(" - ")[0]


class Campaign:
    def __init__(self, report: Report) -> None:
        self.report = report
        self.tally: dict[tuple[str, str], Tally] = {}
        self.escape = Tally()
        self.capture_ms: list[float] = []
        self.z_results: dict[str, list[int]] = {}
        self.alternate = Tally()  # deux fenêtres ouvertes du même coup
        self.wait = time.sleep

    def shot(self, target: Target) -> np.ndarray | None:
        started = time.perf_counter()
        image = print_window(target.game.hwnd)
        self.capture_ms.append(1000 * (time.perf_counter() - started))
        return image

    def watch(self, target: Target, seconds: float, opened: bool) -> float | None:
        """Délai (s) jusqu'à voir la fenêtre des sorts ouverte (`opened`) ou fermée ; None si pas dans le temps."""
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            if spells_open(self.shot(target)) == opened:
                return time.monotonic() - start
            self.wait(POLL)
        return None

    def close(self, target: Target) -> bool:
        """Échap (la fenêtre des sorts est ouverte : jamais Échap à l'aveugle, sur la carte il ouvre le menu principal),
        puis sa croix si Échap n'a pas suffi. Vrai si elle est fermée."""
        self.escape.tries += 1
        Poster(target.game, target.render, burst=True).press(win32con.VK_ESCAPE)
        if self.watch(target, CLOSE_WAIT, opened=False) is not None:
            self.escape.opened += 1
            return True
        image = self.shot(target)
        cross = find_close_button(Frame.of(image)) if image is not None else None
        if cross is not None:
            Poster(target.game, target.render, burst=True).click(cross)
            return self.watch(target, CLOSE_WAIT, opened=False) is not None
        return False

    def click(self, target: Target, method: Method) -> None:
        Poster(target.game, target.render, send=method.send, activate=method.activate, burst=True).click(
            target.point, move=method.move)

    def trial(self, target: Target, method: Method, condition: str) -> None:
        """Un clic sur le bouton baguette ; erreur si la fenêtre des sorts ne se referme pas (campagne arrêtée)."""
        tally = self.tally.setdefault((method.key, condition), Tally())
        tally.tries += 1
        self.click(target, method)
        delay = self.watch(target, OPEN_WAIT, opened=True)
        if delay is None and self.watch(target, LATE_WAIT, opened=True) is not None:
            tally.late += 1
            delay = OPEN_WAIT
        if delay is None:
            return
        tally.opened += 1
        tally.delays.append(delay)
        if not self.close(target):
            raise RuntimeError(f"la fenêtre des sorts de {target.name} est restée ouverte")

    def hold_z(self, target: Target, condition: str, times: int = 3) -> None:
        keys = Poster(target.game, target.render, burst=True)
        counts = []
        for _ in range(times):
            keys.key_down(VK_Z)
            for _ in range(6):
                self.wait(0.1)
                keys.key_down(VK_Z, repeat=True)
            image = self.shot(target)
            keys.key_up(VK_Z)
            counts.append(len(find_group_tooltips(Frame.of(image))) if image is not None else 0)
            self.wait(0.6)
        self.z_results[condition] = counts

    def series(self, target: Target, condition: str, trials: int, cover: Cover | None = None) -> None:
        title = CONDITION_TITLES[condition]
        self.report.say(f"\n{title} ({target.name}) : {trials} clics par façon...")
        self.wait = cover.wait if cover else time.sleep
        try:
            self.wait(1.5)  # le temps que Windows et le jeu constatent que la fenêtre est recouverte
            for number in range(trials):
                for method in METHODS[number % len(METHODS):] + METHODS[:number % len(METHODS)]:  # ordre tournant
                    print(f"  {title} : essai {number + 1}/{trials}, {method.label}          ", end="\r")
                    self.trial(target, method, condition)
            if condition in ("visible", "recouverte", "dessous"):
                self.hold_z(target, condition)
        finally:
            if cover is not None:
                cover.close()
            self.wait = time.sleep
        time.sleep(1.0)
        print()

    def alternate_series(self, top: Target, bottom: Target, trials: int) -> None:
        """Clic dans la fenêtre du dessus puis dans celle du dessous, aussitôt : les deux fenêtres des sorts
        s'ouvrent-elles ? Puis chacune refermée."""
        self.report.say(f"\nclics alternés {top.name} / {bottom.name} : {trials} fois...")
        for number in range(trials):
            print(f"  clics alternés : essai {number + 1}/{trials}          ", end="\r")
            self.alternate.tries += 1
            self.click(top, METHODS[0])
            self.click(bottom, METHODS[0])
            both = [self.watch(t, OPEN_WAIT, opened=True) is not None for t in (top, bottom)]
            self.alternate.opened += all(both)
            for target, opened in zip((top, bottom), both):
                if opened and not self.close(target):
                    raise RuntimeError(f"la fenêtre des sorts de {target.name} est restée ouverte")
        print()

    def conclude(self) -> None:
        say = self.report.say
        conditions = [c for c in CONDITION_TITLES if any(k[1] == c for k in self.tally)]
        say("\nRÉSULTATS (fenêtre des sorts ouverte / clics envoyés, délai médian d'ouverture)")
        say(f"  {'façon d’envoyer le clic':46}" + "".join(f"{CONDITION_TITLES[c]:>29}" for c in conditions))
        for method in METHODS:
            cells = []
            for condition in conditions:
                t = self.tally.get((method.key, condition), Tally())
                median = f", {1000 * statistics.median(t.delays):.0f} ms" if t.delays else ""
                cells.append(f"{t.opened}/{t.tries} ({100 * t.rate():.0f} %{median})")
            say(f"  {method.label:46}" + "".join(f"{c:>29}" for c in cells))
        if self.alternate.tries:
            say(f"  Clics alternés entre deux fenêtres, les deux ouvertes : {self.alternate.opened}/{self.alternate.tries}")
        say(f"  Échap (ferme la fenêtre des sorts) : {self.escape.opened}/{self.escape.tries}")
        for condition, counts in self.z_results.items():
            say(f"  Z tenu, {CONDITION_TITLES[condition]} : infobulles vues {counts}")
        if self.capture_ms:
            say(f"  Capture PrintWindow : {statistics.median(self.capture_ms):.0f} ms (médiane, {len(self.capture_ms)} "
                f"captures)")

        say("\nCONCLUSION")
        for condition in conditions:
            method = max(METHODS, key=lambda m: self.tally.get((m.key, condition), Tally()).rate())
            rate = self.tally[(method.key, condition)].rate()
            twice = 1 - (1 - rate) ** 2
            verdict = "fiable" if rate >= 0.95 else "utilisable en vérifiant chaque clic" if twice >= 0.95 else \
                "pas assez fiable"
            say(f"  {CONDITION_TITLES[condition]} : meilleure façon « {method.label} », {100 * rate:.0f} % du premier "
                f"coup, {100 * twice:.0f} % en deux essais -> {verdict}")
        keys_ok = self.escape.tries and self.escape.opened == self.escape.tries
        say(f"  Clavier : {'fiable' if keys_ok else 'pas toujours reçu'} ; "
            f"Z : {'montre les groupes' if any(max(c, default=0) for c in self.z_results.values()) else 'rien vu'}")


def target_of(game: GameWindow) -> Target | str:
    """La fenêtre prête pour la campagne, ou ce qui ne va pas."""
    if win32gui.IsIconic(game.hwnd):
        return f"« {game.title} » est réduite : remets-la à l'écran (sans la réduire)"
    render = next((c for c, name in children(game.hwnd) if name == "Chrome_RenderWidgetHostHWND"), None)
    if render is None:
        return f"fenêtre interne de « {game.title} » introuvable (client différent de celui du 08/10 ?)"
    image = print_window(game.hwnd)
    if image is None:
        return f"PrintWindow n'a pas pu capturer « {game.title} »"
    frame = Frame.of(image)
    if spells_open(image) or find_unknown_window(frame) is not None:
        return f"une fenêtre est ouverte dans le jeu de « {game.title} » (sorts, menu...) : ferme-la"
    return Target(game, render, find_spells_button(frame) or frame.layout.point(*SPELLS_BUTTON))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--essais", type=int, default=6, help="clics par façon et par situation (défaut 6)")
    args = parser.parse_args()
    enable_dpi_awareness()

    windows = find_windows(load_settings()["window"]["title"])
    if not windows:
        print("Aucune fenêtre du jeu trouvée (personnage connecté ?).")
        return 1
    if any(win32gui.GetForegroundWindow() == w.hwnd for w in windows):
        print("Une fenêtre du jeu a le focus : clique dans une autre fenêtre (sur l'autre écran), puis relance.")
        return 1
    game_titles = {w.title for w in windows}
    tops = [w for w in windows if not covering_windows(w)]
    if not tops:
        others = {t for w in windows for t in covering_windows(w)} - game_titles
        print(f"Aucune fenêtre du jeu n'est entièrement visible (par-dessus : {', '.join(others) or '?'}). Déplace les "
              "autres fenêtres sur l'autre écran, puis relance.")
        return 1
    top_game = tops[0]
    bottom_game = next((w for w in windows if w is not top_game
                        and set(covering_windows(w)) <= game_titles and covering_windows(w)), None)
    targets = []
    for game in [top_game] + ([bottom_game] if bottom_game else []):
        target = target_of(game)
        if isinstance(target, str):
            print(target.capitalize() + ", puis relance.")
            return 1
        targets.append(target)
    top = targets[0]
    bottom = targets[1] if len(targets) > 1 else None

    if not confirm("Campagne sur " + ", ".join(t.game.title for t in targets)
                   + " : clics et touches envoyés à ces fenêtres (~5 min)."):
        return 1
    folder = ROOT / "logs" / f"arriere_plan_campagne_{datetime.now():%Y%m%d_%H%M%S}"
    folder.mkdir(parents=True)
    report = Report(folder)
    report.say(f"Campagne : fenêtre du dessus {top.game.title}"
               + (f", fenêtre du dessous {bottom.game.title}" if bottom else ", une seule fenêtre du jeu (pas de test à "
                  "deux fenêtres)")
               + f" ; {args.essais} clics par façon et par situation. Ne clique pas dans les fenêtres du jeu.")
    campaign = Campaign(report)
    try:
        campaign.series(top, "visible", args.essais)
        if bottom:
            campaign.series(bottom, "dessous", args.essais)
            campaign.alternate_series(top, bottom, args.essais)
        campaign.series(top, "recouverte", args.essais, Cover(top.game))
        campaign.series(top, "bande", args.essais, Cover(top.game, COVER_STRIP))
    except RuntimeError as error:
        report.say(f"\nCampagne arrêtée : {error}. Ferme-la à la main.")
    except KeyboardInterrupt:
        report.say("\nCampagne interrompue (Ctrl+C).")
    finally:
        for target in targets:
            if spells_open(print_window(target.game.hwnd)):
                campaign.close(target)
            report.save(print_window(target.game.hwnd), f"fin_{target.name}")
    campaign.conclude()
    (folder / "rapport.txt").write_text("\n".join(report.lines), encoding="utf-8")
    print(f"\nRapport : {folder.relative_to(ROOT)}\\rapport.txt (l'outil ne bouge jamais ta souris)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
