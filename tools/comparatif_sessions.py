"""Comparatif de sessions de combat (31/10, utilisateur : voir ce que le réseau fait gagner, branche contre version
commitée, et contre l'ancienne version sans réseau). Lit le journal (bot.log) de chaque session et compare : combats,
durée des combats et des tours, sorts, refus, clics sans effet, déplacements sans bouger, lectures de la portée à
l'écran, alertes, erreurs.

    python -m tools.comparatif_sessions "écran seul=logs/20261101_001500" "réseau=logs/..." [--md rapport.md]
"""
import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

TIME = re.compile(r"^(\d\d):(\d\d):(\d\d)\.(\d{3})")
TURN = re.compile(r"\] Tour : ([\d,]+) s ; (\d+) sorts?, (\d+) déplacements?, (\d+) refus(.*)$")
NO_EFFECT = re.compile(r"(\d+) clics? sans effet")
STILL = re.compile(r"(\d+) déplacements? sans bouger")
RECLICKS = re.compile(r"(\d+) reclics?")  # 01/11 : clics de déplacement pas pris, recliqués (pas des ratés)


@dataclass
class Session:
    name: str
    fights: int = 0
    fight_seconds: list[float] = field(default_factory=list)
    turns: list[float] = field(default_factory=list)
    spells: int = 0
    moves: int = 0
    refusals: int = 0
    no_effect: int = 0
    still_moves: int = 0
    reclicks: int = 0
    shown_range_refusals: int = 0  # « Pas de ligne de vue … d'après la portée affichée »
    hidden_said: int = 0
    alerts: int = 0
    errors: int = 0
    warnings: int = 0
    deaths: int = 0
    duration: float = 0.0

    def row(self) -> dict[str, str]:
        turns = len(self.turns)

        def per_turn(value: int) -> str:
            return f"{value / turns:.2f}" if turns else "-"

        return {
            "combats": str(self.fights),
            "durée moyenne d'un combat (s)": f"{sum(self.fight_seconds) / len(self.fight_seconds):.0f}"
            if self.fight_seconds else "-",
            "tours joués": str(turns),
            "durée moyenne d'un tour (s)": f"{sum(self.turns) / turns:.1f}" if turns else "-",
            "sorts par tour": per_turn(self.spells),
            "refus par tour": per_turn(self.refusals),
            "clics sans effet": str(self.no_effect),
            "déplacements sans bouger": str(self.still_moves),
            "reclics de déplacement": str(self.reclicks),
            "pas de ligne de vue lue à l'écran": str(self.shown_range_refusals),
            "ennemi invisible signalé": str(self.hidden_said),
            "morts de mes personnages": str(self.deaths),
            "messages à l'utilisateur": str(self.alerts),
            "avertissements": str(self.warnings),
            "erreurs": str(self.errors),
        }


def _seconds(line: str) -> float | None:
    match = TIME.match(line)
    if match is None:
        return None
    h, m, s, ms = (int(v) for v in match.groups())
    return h * 3600 + m * 60 + s + ms / 1000


def read_session(name: str, folder: Path) -> Session:
    session = Session(name)
    first = last = None
    fight_start = None
    for line in (folder / "bot.log").read_text(encoding="utf-8", errors="replace").splitlines():
        at = _seconds(line)
        if at is not None:
            if first is None:
                first = at
            last = at
        if " DEBUG " in line:
            continue
        if " WARNING " in line:
            session.warnings += 1
        if " ERROR " in line or "Traceback" in line:
            session.errors += 1
        if "À l'utilisateur :" in line:
            session.alerts += 1
        if "] Mon tour" in line and fight_start is None:
            fight_start = at
        if line.endswith("] Combat terminé"):
            session.fights += 1
            if fight_start is not None and at is not None:
                session.fight_seconds.append((at - fight_start) % 86400)
            fight_start = None
        if (turn := TURN.search(line)) is not None:
            session.turns.append(float(turn.group(1).replace(",", ".")))
            session.spells += int(turn.group(2))
            session.moves += int(turn.group(3))
            session.refusals += int(turn.group(4))
            rest = turn.group(5)
            if (found := NO_EFFECT.search(rest)) is not None:
                session.no_effect += int(found.group(1))
            if (found := STILL.search(rest)) is not None:
                session.still_moves += int(found.group(1))
            if (found := RECLICKS.search(rest)) is not None:
                session.reclicks += int(found.group(1))
        if "d'après la portée affichée" in line:
            session.shown_range_refusals += 1
        if "invisible(s) d'après le jeu" in line:
            session.hidden_said += 1
        if "Mon personnage est mort" in line or "est mort au combat" in line:
            session.deaths += 1
    if first is not None and last is not None:
        session.duration = (last - first) % 86400
    return session


def table(sessions: list[Session]) -> str:
    rows = [s.row() for s in sessions]
    head = "| | " + " | ".join(s.name for s in sessions) + " |"
    lines = [head, "|---" * (len(sessions) + 1) + "|"]
    for key in rows[0]:
        lines.append(f"| {key} | " + " | ".join(row[key] for row in rows) + " |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sessions", nargs="+", metavar="NOM=DOSSIER")
    parser.add_argument("--md", help="écrit aussi le tableau dans ce fichier")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sessions = []
    for spec in args.sessions:
        name, _, folder = spec.partition("=")
        sessions.append(read_session(name, Path(folder)))
    text = table(sessions)
    print(text)
    if args.md:
        Path(args.md).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
