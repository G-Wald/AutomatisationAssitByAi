"""Dossier de session logs/<date_heure>/ : journal bot.log et captures (brutes ou annotées).

C'est ce dossier qu'il faut m'envoyer après un test : il montre ce que le bot a vu et décidé.
Pour ne pas remplir le disque : captures en JPEG sauf les échecs (PNG), nombre limité par session, et au
démarrage suppression des plus vieilles sessions (réglages `logs` de config/settings.yaml).
"""

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from bot.config import ROOT

LOGS_DIR = ROOT / "logs"
SESSION_NAME = re.compile(r"\d{8}_\d{6}")  # seuls les dossiers de session du bot sont supprimés
PROBLEM_SHARE = 3  # captures d'échec (PNG, ~2,5 Mo) : au plus max_images // PROBLEM_SHARE, en plus des autres


def _size_mb(folder: Path) -> float:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1e6


def prune_sessions(root: Path, keep: int, max_total_mb: float, current: Path | None = None) -> list[Path]:
    """Supprime les plus vieilles sessions : au plus `keep` en tout (avec la courante) et `max_total_mb` Mo."""
    if not root.is_dir():
        return []
    sessions = sorted(d for d in root.iterdir() if d.is_dir() and SESSION_NAME.fullmatch(d.name) and d != current)
    sizes = {d: _size_mb(d) for d in sessions}
    removed = []
    while sessions and (len(sessions) + (current is not None) > keep or sum(sizes[d] for d in sessions) > max_total_mb):
        oldest = sessions.pop(0)
        shutil.rmtree(oldest, ignore_errors=True)
        removed.append(oldest)
    return removed


class Recorder:
    def __init__(self, root: Path = LOGS_DIR, max_images: int = 300, images: str = "toutes") -> None:
        """`images` : « toutes » (tests), « problemes » (seulement les captures des échecs) ou « aucune »."""
        self.dir = root / f"{datetime.now():%Y%m%d_%H%M%S}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._count = 0  # numéro de la dernière capture (toutes sortes)
        # 17/10 (journal 125352) : les 300 captures prises avant 13:03 (tours, déplacements), plus aucune des 68 attaques
        # ratées ensuite. Les captures d'échec (PNG) ont leur propre limite, en plus.
        self._kept = {False: 0, True: 0}  # par sorte : JPEG, PNG (échecs)
        self._max = {False: max_images, True: max_images // PROBLEM_SHARE}
        self.images = images

    def keeps(self, lossless: bool = False) -> bool:
        """Une capture de ce genre serait enregistrée (sinon, inutile de la préparer : annotations...)."""
        return self.images == "toutes" or (self.images == "problemes" and lossless)

    def setup_logging(self, verbose: bool = False) -> None:
        # Heure à la milliseconde : durée de chaque geste et de chaque attente lisible d'une ligne à l'autre.
        formatter = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s", "%H:%M:%S")
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
        self._kept[lossless] += 1
        self._count += 1
        extension = ".png" if lossless else ".jpg"
        path = self.dir / f"{self._count:04d}_{re.sub(r'[^A-Za-z0-9_-]+', '_', label)}{extension}"
        params = [] if lossless else [cv2.IMWRITE_JPEG_QUALITY, quality]
        ok, data = cv2.imencode(extension, image, params)
        if ok:
            path.write_bytes(data.tobytes())
        return path
