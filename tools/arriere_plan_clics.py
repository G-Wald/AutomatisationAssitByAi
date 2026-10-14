"""Fiabilité des clics envoyés en arrière-plan, dans l'usage du bot : un personnage à la fois.

Campagne du 08/10 (tools/arriere_plan_campagne.py) : clavier 100 %, capture 18 ms, clic « d'un bloc » 5 fois sur 6.
Essai suivant avec une 3e façon, « gel » (fil du jeu mis en pause le temps de déposer le clic, SuspendThread) : 1 clic
réussi sur 56, puis plus aucun, dans les deux fenêtres. Le gel a sans doute déréglé l'état de la souris dans le jeu
(bouton cru enfoncé ?) : retiré. Besoin de l'utilisateur (08/10) : une seule équipe, un personnage après l'autre,
jamais deux fenêtres en même temps ; les déplacements de l'équipe, fenêtre par fenêtre.

Ici, chaque clic sur le bouton baguette est noté un par un (fenêtre des sorts ouverte ou non, délai), avec le résultat
du clic d'avant ; capture des premiers ratés. Façons :
- « même pixel » : souris ici + bouton, à la suite, toujours au même pixel (comme tous les essais jusqu'ici) ;
- « pixel différent » : la même, à un endroit du bouton tiré au hasard (comme le bot, qui ne clique jamais deux fois au
  même pixel). Hypothèse du 08/10 : le jeu (Chromium) ignore un « souris ici » au pixel exact du précédent ; une fois
  le « souris partie » de Windows reçu, il ne voit plus la souris revenir et tous les clics suivants ratent ;
- « détour » : « souris ici » ailleurs sur la carte d'abord, puis le clic à un endroit au hasard du bouton.
Puis des clics alternés entre les deux fenêtres, chacun vérifié avant le suivant (le bot qui passe d'un personnage à
l'autre), avec la meilleure façon.

À lancer soi-même. Avant : clique UNE fois avec ta vraie souris dans chaque fenêtre du jeu (sur la carte, loin des
boutons), pour remettre leur souris d'aplomb, puis clique dans une fenêtre de l'autre écran. Deux fenêtres du jeu (ou
une) maximisées l'une sur l'autre sur l'écran 1920 x 1080, rien d'autre par-dessus, hors combat :
    .venv\\Scripts\\python -m tools.arriere_plan_clics
Durée : ~2 min. Rapport et captures dans logs/arriere_plan_clics_<date>/.
"""

import argparse
import random
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime

import win32gui

from bot.capture.window import enable_dpi_awareness, find_windows
from bot.config import ROOT, load_settings
from tools.arriere_plan import Poster, Report, confirm, covering_windows, print_window
from tools.arriere_plan_campagne import OPEN_WAIT, Campaign, Target, target_of

FAILURE_CAPTURES = 3  # captures des premiers ratés, par fenêtre


JITTER = 6  # px : écart au hasard autour du centre du bouton baguette (36 px de côté)


@dataclass(frozen=True)
class Way:
    key: str
    label: str
    jitter: bool = False
    detour: bool = False


WAYS = (Way("meme", "même pixel"), Way("decale", "pixel différent", jitter=True),
        Way("detour", "détour puis pixel différent", jitter=True, detour=True))


@dataclass
class Trial:
    number: int
    situation: str
    way: str
    opened: bool
    delay: float | None
    after: str  # résultat du clic précédent dans la même fenêtre : « réussi », « raté », « aucun »
    switched: bool  # le clic précédent (toutes fenêtres) visait l'autre fenêtre


class Clicks(Campaign):
    def __init__(self, report: Report) -> None:
        super().__init__(report)
        self.trials: list[Trial] = []
        self.last: dict[int, str] = {}  # fenêtre -> résultat de son dernier clic
        self.previous: int | None = None  # fenêtre du dernier clic envoyé
        self.failures: dict[int, int] = {}  # fenêtre -> ratés capturés

    def one(self, target: Target, way: Way, situation: str) -> None:
        after = self.last.get(target.game.hwnd, "aucun")
        switched = self.previous is not None and self.previous != target.game.hwnd
        self.previous = target.game.hwnd
        poster = Poster(target.game, target.render, burst=True)
        x, y = target.point
        if way.detour:
            poster.move((x - random.uniform(300, 600), y - random.uniform(250, 450)))  # ailleurs sur la carte
            time.sleep(random.uniform(0.05, 0.12))
        if way.jitter:
            x, y = x + random.uniform(-JITTER, JITTER), y + random.uniform(-JITTER, JITTER)
        poster.click((x, y))
        delay = self.watch(target, OPEN_WAIT, opened=True)
        opened = delay is not None
        self.trials.append(Trial(len(self.trials) + 1, situation, way.key, opened, delay, after, switched))
        self.last[target.game.hwnd] = "réussi" if opened else "raté"
        capture = ""
        if not opened and self.failures.get(target.game.hwnd, 0) < FAILURE_CAPTURES:
            self.failures[target.game.hwnd] = self.failures.get(target.game.hwnd, 0) + 1
            capture = f" (capture {self.report.save(print_window(target.game.hwnd), f'rate_{target.name}')})"
        self.report.say(f"  {len(self.trials):3d}. {target.name:12} {way.label:28} après un clic {after:7}"
                        f"{' (autre fenêtre juste avant)' if switched else ''} -> "
                        f"{'ouverte en %.0f ms' % (1000 * delay) if opened else 'RATÉ'}{capture}")
        # Rapport réécrit à chaque clic : un test interrompu garde ses résultats (08/10 : rien d'écrit après un arrêt).
        (self.report.folder / "rapport.txt").write_text("\n".join(self.report.lines), encoding="utf-8")
        if opened and not self.close(target):
            raise RuntimeError(f"la fenêtre des sorts de {target.name} est restée ouverte")

    def run_ways(self, target: Target, situation: str, per_way: int, rng: random.Random) -> None:
        order = [way for way in WAYS for _ in range(per_way)]
        rng.shuffle(order)  # ordre au hasard (graine fixe) : le clic d'avant varie
        for way in order:
            print(f"  {target.name} : clic {len(self.trials) + 1}...      ", end="\r")
            self.one(target, way, situation)

    def best(self) -> Way:
        return max(WAYS, key=lambda way: sum(t.opened for t in self.trials if t.way == way.key))

    def alternate(self, targets: list[Target], times: int) -> None:
        way = self.best()
        self.report.say(f"  (façon : {way.label})")
        for number in range(times):
            target = targets[number % len(targets)]
            print(f"  alterné : clic {number + 1}/{times}...      ", end="\r")
            time.sleep(1.0)  # un personnage, puis l'autre : le temps d'un tour
            self.one(target, way, "alterné")

    def summary(self) -> None:
        say = self.report.say
        say("\nRÉSULTATS (fenêtre des sorts ouverte / clics)")
        for situation in dict.fromkeys(t.situation for t in self.trials):
            for way in WAYS:
                done = [t for t in self.trials if t.way == way.key and t.situation == situation]
                if done:
                    ok = [t for t in done if t.opened]
                    median = f", {1000 * statistics.median(t.delay for t in ok):.0f} ms" if ok else ""
                    say(f"  {situation:8} {way.label:28} : {len(ok)}/{len(done)} "
                        f"({100 * len(ok) / len(done):.0f} %{median})")
        say("\nLE CLIC D'AVANT COMPTE-T-IL ?")
        for after in ("réussi", "raté", "aucun"):
            done = [t for t in self.trials if t.after == after]
            if done:
                ok = sum(t.opened for t in done)
                say(f"  après un clic {after:7} dans la même fenêtre : {ok}/{len(done)} ({100 * ok / len(done):.0f} %)")
        switched = [t for t in self.trials if t.switched]
        if switched:
            ok = sum(t.opened for t in switched)
            say(f"  juste après un clic dans l'autre fenêtre : {ok}/{len(switched)} ({100 * ok / len(switched):.0f} %)")
        say(f"Échap (ferme la fenêtre des sorts) : {self.escape.opened}/{self.escape.tries}")
        total = len(self.trials)
        ok = sum(t.opened for t in self.trials)
        if total:
            rate = ok / total
            say(f"\nCONCLUSION : {ok}/{total} clics réussis ({100 * rate:.0f} %), "
                f"{100 * (1 - (1 - rate) ** 2):.0f} % en deux essais -> "
                + ("fiable" if rate >= 0.95 else "utilisable en vérifiant chaque clic" if 1 - (1 - rate) ** 2 >= 0.95
                   else "pas assez fiable"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--essais", type=int, default=8, help="clics par façon et par fenêtre (défaut 8)")
    args = parser.parse_args()
    enable_dpi_awareness()

    windows = find_windows(load_settings()["window"]["title"])
    if not windows:
        print("Aucune fenêtre du jeu trouvée (personnage connecté ?).")
        return 1
    if any(win32gui.GetForegroundWindow() == w.hwnd for w in windows):
        print("Une fenêtre du jeu a le focus : clique dans une autre fenêtre (sur l'autre écran), puis relance.")
        return 1
    titles = {w.title for w in windows}
    tops = [w for w in windows if not covering_windows(w)]
    if not tops:
        print("Aucune fenêtre du jeu n'est entièrement visible : déplace les autres fenêtres sur l'autre écran.")
        return 1
    bottom_game = next((w for w in windows if w is not tops[0] and covering_windows(w)
                        and set(covering_windows(w)) <= titles), None)
    targets = []
    for game in [tops[0]] + ([bottom_game] if bottom_game else []):
        target = target_of(game)
        if isinstance(target, str):
            print(target.capitalize() + ", puis relance.")
            return 1
        targets.append(target)

    if not confirm("Clics sur " + ", ".join(t.game.title for t in targets) + " (~2 min). As-tu cliqué une fois avec ta "
                   "vraie souris dans chaque fenêtre du jeu avant ?"):
        return 1
    folder = ROOT / "logs" / f"arriere_plan_clics_{datetime.now():%Y%m%d_%H%M%S}"
    folder.mkdir(parents=True)
    report = Report(folder)
    report.say("Fiabilité des clics : " + ", ".join(t.game.title for t in targets) +
               f" ; {args.essais} clics par façon et par fenêtre. Ne clique pas dans les fenêtres du jeu.")
    clicks = Clicks(report)
    rng = random.Random()
    try:
        for target, situation in zip(targets, ("dessus", "dessous")):
            report.say(f"\n{target.name} ({'visible' if situation == 'dessus' else 'cachée par l’autre'})")
            clicks.run_ways(target, situation, args.essais, rng)
        if len(targets) > 1:
            report.say("\nUn personnage puis l'autre (clics alternés, chacun vérifié avant le suivant)")
            clicks.alternate(targets, 2 * args.essais)
    except RuntimeError as error:
        report.say(f"\nArrêt : {error}. Ferme-la à la main.")
    except KeyboardInterrupt:
        report.say("\nInterrompu (Ctrl+C).")
    finally:
        print()
    clicks.summary()
    (folder / "rapport.txt").write_text("\n".join(report.lines), encoding="utf-8")
    print(f"\nRapport : {folder.relative_to(ROOT)}\\rapport.txt (l'outil ne bouge jamais ta souris)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
