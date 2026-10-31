"""Dossier de session logs/<date_heure>/ : journal bot.log et captures (brutes ou annotées).

C'est ce dossier qu'il faut m'envoyer après un test : il montre ce que le bot a vu et décidé.
Pour ne pas remplir le disque : captures en JPEG sauf les échecs (PNG), nombre limité par session et par libellé
(personnage + sorte) toutes les QUOTA_WINDOW s, et au démarrage suppression des plus vieilles sessions (réglages
`logs` de config/settings.yaml).
"""

import logging
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from bot.config import ROOT

LOGS_DIR = ROOT / "logs"
SESSION_NAME = re.compile(r"\d{8}_\d{6}")  # seuls les dossiers de session du bot sont supprimés
PROBLEM_SHARE = 3  # captures d'échec (PNG, ~2,5 Mo) : au plus max_images // PROBLEM_SHARE, en plus des autres
# Captures gardées par libellé (« Walitcoin_tour », « Fourboun_menu_attaquer_absent ») toutes les QUOTA_WINDOW s :
# courantes (JPEG) ROUTINE_QUOTA, d'échec (PNG) PROBLEM_QUOTA. 25/10 : les 300 captures courantes d'une session étaient
# prises en 8 à 13 min (journaux 153121, 155334, 171736, 181212 : 192 à 300 dans les 10 premières minutes, dont « tour »
# 45 à 56 %), puis plus aucune : 40 min sans capture courante dans la session de référence (181212, 53 min). Avec 1
# capture courante par libellé et par 10 min, 39 à 44 dans les 10 premières minutes (mêmes journaux), un échantillon
# de chaque personnage et de chaque sorte pendant toute la session.
QUOTA_WINDOW = 600.0
ROUTINE_QUOTA = 1
PROBLEM_QUOTA = 3


def _size_mb(folder: Path) -> float:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1e6


def _empty(folder: Path) -> bool:
    return not any(folder.iterdir())


def prune_sessions(root: Path, keep: int, max_total_mb: float, current: Path | None = None) -> list[Path]:
    """Supprime les plus vieilles sessions : au plus `keep` en tout (avec la courante) et `max_total_mb` Mo. Les
    dossiers de session vides sont supprimés d'abord, sans compter (25/10 : 10 dossiers vides créés entre 19:35 et
    19:39 par des outils, Recorder() sans capture, comptaient parmi les 20 sessions gardées : 9 vraies sessions
    seulement restaient dans logs/)."""
    if not root.is_dir():
        return []
    sessions = sorted(d for d in root.iterdir() if d.is_dir() and SESSION_NAME.fullmatch(d.name) and d != current)
    removed = []
    for folder in [d for d in sessions if _empty(d)]:
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(folder)
        sessions.remove(folder)
    sizes = {d: _size_mb(d) for d in sessions}
    while sessions and (len(sessions) + (current is not None) > keep or sum(sizes[d] for d in sessions) > max_total_mb):
        oldest = sessions.pop(0)
        shutil.rmtree(oldest, ignore_errors=True)
        removed.append(oldest)
    return removed


class Recorder:
    def __init__(self, root: Path = LOGS_DIR, max_images: int = 300, images: str = "toutes",
                 clock=time.monotonic) -> None:
        """`images` : « toutes » (tests), « problemes » (seulement les captures des échecs) ou « aucune ». Le dossier
        n'est créé qu'au journal (setup_logging) ou à la première capture : un Recorder sans rien ne laisse rien."""
        self.dir = root / f"{datetime.now():%Y%m%d_%H%M%S}"
        self._count = 0  # numéro de la dernière capture (toutes sortes)
        # 17/10 (journal 125352) : les 300 captures prises avant 13:03 (tours, déplacements), plus aucune des 68 attaques
        # ratées ensuite. Les captures d'échec (PNG) ont leur propre limite, en plus.
        self._kept = {False: 0, True: 0}  # par sorte : JPEG, PNG (échecs)
        self._max = {False: max_images, True: max_images // PROBLEM_SHARE}
        self.images = images
        self._clock = clock
        self._recent: dict[tuple[str, bool], list[float]] = {}  # (libellé, PNG ?) -> heures des captures gardées
        self._skipped: set[tuple[str, bool]] = set()  # quota atteint, déjà dit au journal pour cette fenêtre de temps

    def keeps(self, lossless: bool = False) -> bool:
        """Une capture de ce genre serait enregistrée (sinon, inutile de la préparer : annotations...)."""
        return self.images == "toutes" or (self.images == "problemes" and lossless)

    def setup_logging(self, verbose: bool = False) -> None:
        # Heure à la milliseconde : durée de chaque geste et de chaque attente lisible d'une ligne à l'autre.
        formatter = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s", "%H:%M:%S")
        self.dir.mkdir(parents=True, exist_ok=True)
        to_file = logging.FileHandler(self.dir / "bot.log", encoding="utf-8")
        to_file.setLevel(logging.DEBUG)
        to_console = logging.StreamHandler()
        to_console.setLevel(logging.DEBUG if verbose else logging.INFO)
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        root.handlers[:] = []
        for handler in (to_file, to_console):
            handler.setFormatter(formatter)
            root.addHandler(handler)

    def save(self, image: np.ndarray, label: str, lossless: bool = False, quality: int = 80) -> Path | None:
        """`lossless` (PNG) pour les échecs que je réanalyserai, JPEG sinon (plus léger)."""
        if not self.keeps(lossless):
            return None
        if self._kept[lossless] >= self._max[lossless]:
            if self._kept[lossless] == self._max[lossless]:
                self._kept[lossless] += 1
                logging.getLogger(__name__).info("Limite de %d captures %s atteinte : je n'en enregistre plus",
                                                 self._max[lossless], "d'échec" if lossless else "courantes")
            return None
        if not self._within_quota(label, lossless):
            return None
        self._kept[lossless] += 1
        self._count += 1
        self.dir.mkdir(parents=True, exist_ok=True)
        extension = ".png" if lossless else ".jpg"
        path = self.dir / f"{self._count:04d}_{re.sub(r'[^A-Za-z0-9_-]+', '_', label)}{extension}"
        params = [] if lossless else [cv2.IMWRITE_JPEG_QUALITY, quality]
        ok, data = cv2.imencode(extension, image, params)
        if ok:
            path.write_bytes(data.tobytes())
        return path

    def _within_quota(self, label: str, lossless: bool) -> bool:
        """Au plus ROUTINE_QUOTA (JPEG) ou PROBLEM_QUOTA (PNG) captures de ce libellé dans les QUOTA_WINDOW dernières
        secondes ; la capture gardée est comptée."""
        now = self._clock()
        key = (label, lossless)
        times = [t for t in self._recent.get(key, []) if now - t < QUOTA_WINDOW]
        if len(times) >= (PROBLEM_QUOTA if lossless else ROUTINE_QUOTA):
            if key not in self._skipped:
                self._skipped.add(key)
                logging.getLogger(__name__).debug("Captures « %s » : %d en %.0f min déjà, les suivantes ne sont pas "
                                                  "gardées pendant %.0f s", label, len(times), QUOTA_WINDOW / 60,
                                                  QUOTA_WINDOW - (now - times[0]))
            self._recent[key] = times
            return False
        self._skipped.discard(key)
        self._recent[key] = times + [now]
        return True
