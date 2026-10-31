"""Teinte de la carte par le cycle jour/nuit du jeu, et retour aux couleurs du jour.

26/10 (journal 001614) : passé minuit, la carte (pas l'interface) est teintée de violet. Les cases du mode tactique
passent de (131, 125, 121) et (141, 135, 131) à (116, 97, 114) et (125, 105, 123) (BGR) ; mode tactique cru inactif,
portée des sorts (bleu) et zone de déplacement (vert) plus reconnues : le pas de repérage tombait sur un sort resté
sélectionné, « 9 cercles de ma couleur et rien ne prouve lequel est moi », les 8 personnages ont passé tous leurs tours.

La teinte est une transformation affine par canal (« ColorTransform » de Flash) : mesurée sur 9 couleurs de la carte
tactique vues de jour et de nuit (sol, portée, sans ligne de vue, dessus des blocs, zone de déplacement), à 1,5 près
au plus (2,2 pour le dessus des blocs), NIGHT_GAIN et NIGHT_OFFSET. Les cercles des combattants (rouge et bleu purs)
sont teintés aussi mais restent au-dessus des seuils (rouge 255 -> 245) ; l'interface posée sur la carte (timeline,
fenêtres, boutons) ne l'est pas.

La force de la teinte se lit sur chaque capture aux deux teintes du sol du mode tactique (0 de jour, 1 la nuit du
26/10), et l'image de la carte est ramenée au jour une fois par capture (Frame.day_image) pour les lectures de couleurs
de la carte (mode tactique, portée, zone de déplacement, cases, cercles). Sans sol tactique reconnu (affichage normal,
bandeau du début de combat...), ou de jour : image inchangée.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from bot.perception.layout import Box

DAY_FLOOR = ((131, 125, 121), (141, 135, 131))  # BGR, les deux teintes du sol du mode tactique, de jour (toutes cartes)
NIGHT_GAIN = (0.936, 0.975, 0.961)  # BGR : nuit = jour x gain + décalage (26/10, journaux 234008 et 001614)
NIGHT_OFFSET = (-6.4, -26.3, -2.1)
# Écart toléré entre les teintes du sol lues et celles que donne la force trouvée : 0,9 à 1,3 sur les 140 captures de
# combat de nuit (JPEG compris), 0,4 au plus sur les 503 de jour ; 18 à 114 pour deux couleurs voisines qui ne sont
# pas le sol (bleu « sans ligne de vue », fenêtres beiges, herbe : 36 captures de jour).
MAX_RESIDUAL = 4.0
MIN_STRENGTH = 0.1  # en dessous : le jour (0,00 à 0,03 sur 503 captures de jour en mode tactique), image inchangée
MAX_STRENGTH = 2.0  # au-delà : pas cette teinte-là (nuit plus profonde admise jusqu'au double)
# Part des pixels allumés de la carte qu'une teinte du sol doit couvrir : la portée d'un sort peut cacher presque
# toute la teinte claire (2,5 % sur une capture de repérage du 25/10), les deux ensemble en couvrent 13 % au moins.
MIN_SHADE_SHARE = 0.01
MIN_FLOOR_SHARE = 0.08
SHADE_GAP = (5, 14)  # écart de la teinte claire du sol sur la foncée, sur chaque canal (10 de jour, 8 à 9 la nuit)
SAMPLE_STEP = 6  # un pixel sur 36 suffit à des proportions (~30 000 pixels pour une carte en 1920 x 1057) : ~3 ms
SHADES = 6  # couleurs les plus fréquentes essayées comme teintes du sol

_DAY = np.array(DAY_FLOOR, np.float64)
_SHIFT = _DAY * (np.array(NIGHT_GAIN) - 1) + np.array(NIGHT_OFFSET)  # nuit - jour, sur chaque teinte du sol


@dataclass(frozen=True)
class Tint:
    """Teinte de la carte : force (0 le jour, 1 la nuit du 26/10) et les deux teintes du sol lues (BGR)."""

    strength: float
    floor: tuple[tuple[float, float, float], tuple[float, float, float]]

    def gains(self) -> tuple[np.ndarray, np.ndarray]:
        """Transformation jour -> carte teintée : couleur x gain + décalage, par canal (BGR)."""
        return 1 + self.strength * (np.array(NIGHT_GAIN) - 1), self.strength * np.array(NIGHT_OFFSET)

    def table(self) -> np.ndarray:
        """Table de correspondance carte teintée -> jour (256 x 1 x 3, pour cv2.LUT)."""
        gain, offset = self.gains()
        levels = np.arange(256, dtype=np.float64)[:, None]
        return np.clip(np.rint((levels - offset) / gain), 0, 255).astype(np.uint8).reshape(256, 1, 3)


def _shades(region: np.ndarray) -> tuple[list[tuple[np.ndarray, float]], int]:
    """Couleurs les plus fréquentes des pixels allumés de `region` (moyenne exacte, part des pixels), par cases de
    4 niveaux par canal regroupées avec leurs voisines (une teinte unie, même compressée en JPEG, tient dans 3 cases)."""
    grid = np.ascontiguousarray(region[::SAMPLE_STEP, ::SAMPLE_STEP]).reshape(-1, 3)
    b, g, r = grid[:, 0], grid[:, 1], grid[:, 2]
    lit = np.maximum(np.maximum(b, g), r) > 40  # le vide noir autour des petites cartes ne compte pas
    b, g, r = b[lit], g[lit], r[lit]
    if not len(b):
        return [], 0
    key = ((b >> 2).astype(np.intp) << 12) | ((g >> 2).astype(np.intp) << 6) | (r >> 2)
    counts = np.bincount(key, minlength=1 << 18).reshape(64, 64, 64).astype(np.float64)
    sums = [np.bincount(key, weights=channel, minlength=1 << 18).reshape(64, 64, 64) for channel in (b, g, r)]
    pixels = len(key)
    found = []
    for _ in range(SHADES):
        qb, qg, qr = np.unravel_index(int(np.argmax(counts)), counts.shape)
        window = np.s_[max(0, qb - 1) : qb + 2, max(0, qg - 1) : qg + 2, max(0, qr - 1) : qr + 2]
        total = float(counts[window].sum())
        if total < MIN_SHADE_SHARE * pixels:
            break
        found.append((np.array([float(s[window].sum()) / total for s in sums]), total / pixels))
        counts[window] = 0
        for s in sums:
            s[window] = 0
    return found, pixels


def floor_tint(region: np.ndarray) -> Tint | None:
    """Teinte de la carte `region` (BGR, zone de la carte seule), lue aux deux teintes du sol du mode tactique : la
    force de NIGHT_GAIN / NIGHT_OFFSET qui les explique le mieux (moindres carrés sur les 6 valeurs). None sans sol
    tactique reconnu, de jour (force < MIN_STRENGTH) ou pour une teinte d'une autre forme (écart > MAX_RESIDUAL)."""
    found, _ = _shades(region)
    best: tuple[float, float, np.ndarray] | None = None
    for dark, dark_share in found:
        for light, light_share in found:
            gap = light - dark
            if dark_share + light_share < MIN_FLOOR_SHARE or (gap < SHADE_GAP[0]).any() or (gap > SHADE_GAP[1]).any():
                continue
            seen = np.array([dark, light])
            strength = float(((seen - _DAY) * _SHIFT).sum() / (_SHIFT**2).sum())
            residual = float(np.abs(seen - (_DAY + strength * _SHIFT)).max())
            if best is None or residual < best[1]:
                best = (strength, residual, seen)
    if best is None or best[1] > MAX_RESIDUAL or not MIN_STRENGTH <= best[0] <= MAX_STRENGTH:
        return None
    strength, _, seen = best
    floor = tuple(tuple(round(float(v), 1) for v in shade) for shade in seen)
    return Tint(round(strength, 3), floor)  # type: ignore[arg-type]


def to_daylight(image: np.ndarray, box: Box, tint: Tint) -> np.ndarray:
    """Copie de `image` dont la zone `box` (la carte) est ramenée aux couleurs du jour ; le reste (l'interface du bas,
    le chat) est inchangé. Les niveaux écrasés à 0 par la teinte reviennent au plus bas qu'ils pouvaient avoir (vert :
    ~27) : les seuils des couleurs pures (cercles, cases de placement : vert et bleu <= 40) tiennent toujours."""
    x0, y0, x1, y1 = box
    day = image.copy()
    day[y0:y1, x0:x1] = cv2.LUT(np.ascontiguousarray(image[y0:y1, x0:x1]), tint.table())
    return day
