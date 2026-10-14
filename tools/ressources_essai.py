"""Essai hors jeu (12/10) : métiers de récolte d'après la langue du client, et repérage des ressources sur des captures
réelles du jeu (logs/), comparé à la référence (config/monde). Voir docs/rapports/metiers_ressources.md.

    .venv/Scripts/python -m tools.ressources_essai --metiers     # métiers de récolte, actions, objets, pods, protecteurs
    .venv/Scripts/python -m tools.ressources_essai --index       # captures de logs/ -> carte et ressources attendues
    .venv/Scripts/python -m tools.ressources_essai --gabarits    # découpe les gabarits (médiane de plusieurs exemplaires)
    .venv/Scripts/python -m tools.ressources_essai --essai       # repérage sur toutes les captures indexées, bilan chiffré

Lecture seule sur logs/, config/ et data/client_retro/. N'écrit que dans data/ressources_essai/ :
index.json, gabarits/<nom>.png (+ _masque.png), resultats.json, planches/ (détections à vérifier à l'œil).

Méthode de repérage (« gabarit ancré sur les cases ») :
- chaque ressource est un objet interactif dessiné par le client avec UN seul graphisme (langue IO : un gfx par
  ressource) posé sur une case ; ses exemplaires sont donc identiques au pixel près (au retournement près) ;
- le gabarit est la médiane de plusieurs exemplaires découpés dans des captures et alignés : ce qui passe devant
  (herbes, personnages, autres arbres) disparaît ; le masque garde les pixels stables d'un exemplaire à l'autre ;
- on ne cherche le gabarit qu'aux centres des cases (repère bot/perception/grid.py), à ± RECHERCHE px : un score
  par case, ce qui donne directement la case à cliquer et évite les doublons ;
- une case retient le meilleur gabarit (arbre debout ou souche) au-dessus du seuil.
"""

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from bot.perception.frame import Frame

RACINE = Path(__file__).resolve().parents[1]
LOGS = RACINE / "logs"
MONDE = RACINE / "config" / "monde" / "monde_des_douze.json"
LANGUE = RACINE / "data" / "client_retro" / "lang"
SORTIE = RACINE / "data" / "ressources_essai"
DEFINITIONS = SORTIE / "gabarits.json"

RECHERCHE = 3  # px autour de la position attendue (centre de case + ancre du gabarit)
VISIBLE_MIN = 0.55  # part du gabarit qui doit être dans la zone de la carte pour noter une case
SEUIL = 0.6
NMS = 40  # px : deux détections plus proches sont le même objet (deux cases voisines sont à 54 px au moins)
ECART_MASQUE = 28  # écart à la médiane (max des canaux) en dessous duquel un pixel est « stable »
STABLE_MIN = 0.7  # part des exemplaires où le pixel doit être stable pour entrer dans le masque


# --- Lecture d'images ------------------------------------------------------------------------------------------------

def lire_image(chemin: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(str(chemin), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(chemin)
    return image


def ecrire_image(chemin: Path, image: np.ndarray) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    ok, contenu = cv2.imencode(chemin.suffix, image, [cv2.IMWRITE_JPEG_QUALITY, 85] if chemin.suffix == ".jpg" else [])
    if not ok:
        raise ValueError(chemin)
    contenu.tofile(str(chemin))


# --- Métiers de récolte (langue du client) ---------------------------------------------------------------------------

METIERS_DE_RECOLTE = {2: "Bûcheron", 24: "Mineur", 26: "Alchimiste", 28: "Paysan", 36: "Pêcheur"}
RACES_PROTECTEURS = {62, 63, 64, 65, 66}  # langue MR : « Protecteurs des Céréales / Minerais / Arbres / Poissons / Plantes »


def _table(dossier: Path, nom: str) -> dict:
    from tools.cartes_client import lire_sol
    return lire_sol(dossier / f"ANKXTRASO_0_{nom}.sol")["_Data"][nom]


def recoltes(dossier: Path = LANGUE) -> list[dict]:
    """Une ligne par compétence de récolte : métier, action du menu, ressource (objet interactif), objet obtenu.

    SK[id] = {d action, j métier, io objet interactif, i objet obtenu} ; IO.d[io] = {n nom, t type (1 = ressource)} ;
    I.u[objet] = {n nom, l niveau de l'objet, w poids en pods, t type}."""
    metiers, competences, interactifs = _table(dossier, "J"), _table(dossier, "SK"), _table(dossier, "IO")
    objets = _table(dossier, "I")
    gfx: dict[int, list[int]] = {}
    for g, io in interactifs["g"].items():
        gfx.setdefault(int(io), []).append(int(g))
    lignes = []
    for id_sk, sk in competences.items():
        if sk.get("j") not in METIERS_DE_RECOLTE or not sk.get("i"):
            continue
        io = interactifs["d"].get(str(sk["io"]), {})
        objet = objets["u"].get(str(sk["i"]), {})
        lignes.append({
            "metier": metiers[str(sk["j"])]["n"], "action": sk["d"], "ressource": io.get("n"), "io": sk["io"],
            "gfx": sorted(gfx.get(int(sk["io"]), [])), "objet": objet.get("n"), "niveau_objet": objet.get("l"),
            "pods": objet.get("w"), "competence": int(id_sk),
        })
    return sorted(lignes, key=lambda l: (l["metier"], l["niveau_objet"] or 0, l["ressource"] or ""))


def outils(dossier: Path = LANGUE) -> list[dict]:
    """Objets des types Hache (19), Outil (20), Pioche (21), Faux (22), de niveau 1 : les outils de départ."""
    objets = _table(dossier, "I")
    types = {int(k): v for k, v in objets["t"].items()}
    lignes = []
    for id_objet, o in objets["u"].items():
        if o.get("t") in (19, 20, 21, 22) and o.get("l") == 1 and not o.get("c"):
            lignes.append({"objet": o["n"], "id": int(id_objet), "type": types[o["t"]]["n"],
                           "super_type": types[o["t"]].get("t"), "pods": o.get("w"), "prix": o.get("p")})
    return sorted(lignes, key=lambda l: (l["type"], l["objet"]))


def protecteurs(dossier: Path = LANGUE) -> list[dict]:
    """Monstres des races « Protecteurs des ... » : nom et, par grade, niveau, vie (lp), PA, PM."""
    monstres, races = _table(dossier, "M"), _table(dossier, "MR")
    lignes = []
    for id_m, m in monstres.items():
        if m.get("b") not in RACES_PROTECTEURS:
            continue
        grades = [(m[g].get("l"), m[g].get("lp"), m[g].get("ap"), m[g].get("mp")) for g in ("g1", "g2", "g3", "g4", "g5")
                  if g in m]
        lignes.append({"race": races[str(m["b"])]["n"], "nom": m["n"], "id": int(id_m), "grades": grades})
    return sorted(lignes, key=lambda l: (l["race"], l["id"]))


def afficher_metiers(dossier: Path = LANGUE) -> None:
    for l in recoltes(dossier):
        print(f"{l['metier']:<11} {l['action']:<10} {l['ressource']:<28} -> {l['objet']:<28} niv. objet {l['niveau_objet']:>3}"
              f"  {l['pods']} pod(s)  gfx {l['gfx']}")
    print()
    for o in outils(dossier):
        print(f"outil : {o['objet']:<34} {o['type']:<6} {o['pods']} pods, {o['prix']} kamas (super-type {o['super_type']})")
    print()
    for p in protecteurs(dossier):
        print(f"{p['race']:<26} {p['nom']:<30} grades (niveau, vie, PA, PM) : {p['grades']}")


# --- Index des captures ----------------------------------------------------------------------------------------------

LIGNE = re.compile(r"^(\d\d:\d\d:\d\d\.\d+) \w+\s+\[(.+?)\] (.*)$")
NOUVELLE = re.compile(r"Nouvelle carte : .*?(-?\d+,-?\d+)")
SUIVI = re.compile(r"a suivi le principal \(.*?(-?\d+,-?\d+)\)")
DEPART = re.compile(r"; départ : (-?\d+,-?\d+)")
ILLISIBLE = re.compile(r"Nom de la carte illisible.*: carte (-?\d+,-?\d+) d'après le chemin \(capture : (.+)\)")


def _cle_personnage(nom: str) -> str:
    return re.sub(r"[^a-z0-9]", "", nom.lower())


def evenements_de_carte(journal: str) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    """(heure, personnage, coordonnées) de chaque arrivée sur une carte, et {nom de capture : coordonnées} des captures
    « nom de carte illisible », dont le journal donne le chemin."""
    evenements, captures = [], {}
    for ligne in journal.splitlines():
        m = LIGNE.match(ligne)
        if not m:
            continue
        heure, perso, texte = m.groups()
        illisible = ILLISIBLE.search(texte)
        if illisible:
            captures[Path(illisible.group(2).replace("\\", "/")).name] = illisible.group(1)
            evenements.append((heure, perso, illisible.group(1)))
            continue
        for motif in (NOUVELLE, SUIVI, DEPART):
            trouve = motif.search(texte)
            if trouve:
                evenements.append((heure, perso, trouve.group(1)))
                break
    return evenements, captures


def carte_de_capture(nom_fichier: str, heure: str, evenements: list, captures: dict) -> str | None:
    """Carte d'une capture : donnée par le journal pour « nom de carte illisible », sinon la dernière carte où était
    arrivé son personnage (nom en tête du fichier) avant l'heure d'écriture de la capture."""
    if nom_fichier in captures:
        return captures[nom_fichier]
    perso = _cle_personnage(nom_fichier.split("_", 1)[1]) if "_" in nom_fichier else ""
    avant = [e for e in evenements if e[0] <= heure]
    siens = [e for e in avant if _cle_personnage(e[1]) and perso.startswith(_cle_personnage(e[1]))]
    choix = siens or avant
    return choix[-1][2] if choix else None


def indexer(logs: Path = LOGS, monde: Path = MONDE) -> list[dict]:
    reference = json.loads(monde.read_text(encoding="utf-8"))["cartes"]
    lignes = []
    for dossier in sorted(p for p in logs.iterdir() if p.is_dir() and p.name[:1].isdigit()):
        journal = dossier / "bot.log"
        if not journal.exists():
            continue
        evenements, captures = evenements_de_carte(journal.read_text(encoding="utf-8", errors="replace"))
        for image in sorted(dossier.iterdir()):
            if image.suffix not in (".png", ".jpg"):
                continue
            heure = datetime.fromtimestamp(image.stat().st_mtime).strftime("%H:%M:%S.%f")[:12]
            carte = carte_de_capture(image.name, heure, evenements, captures)
            infos = reference.get(carte, {}) if carte else {}
            attendu: dict[str, int] = {}
            for objet in infos.get("objets", []):
                attendu[objet["nom"]] = attendu.get(objet["nom"], 0) + int(objet.get("nombre") or 0)
            lignes.append({"fichier": image.relative_to(RACINE).as_posix() if image.is_relative_to(RACINE) else str(image),
                           "carte": carte, "zone": infos.get("zone"), "attendu": attendu})
    return lignes


# --- Gabarits --------------------------------------------------------------------------------------------------------

@dataclass
class Gabarit:
    nom: str
    ressource: str  # nom de la ressource dans la référence (config/monde)
    etat: str  # « debout » (récoltable) ou « coupe » (récoltée, en repousse)
    image: np.ndarray
    masque: np.ndarray | None
    ancre: tuple[int, int]  # position du centre de la case dans le gabarit (x, y)

    def retourne(self) -> "Gabarit":
        """Le client dessine parfois les objets en miroir (retournement horizontal autour de la case)."""
        h, w = self.image.shape[:2]
        masque = None if self.masque is None else cv2.flip(self.masque, 1)
        return Gabarit(self.nom, self.ressource, self.etat, cv2.flip(self.image, 1), masque, (w - 1 - self.ancre[0], self.ancre[1]))


def centre_de_case(frame: Frame, case: tuple[int, int]) -> tuple[float, float]:
    return frame.grid.center(case)


def decouper(image: np.ndarray, centre: tuple[float, float], boite: tuple[int, int, int, int]) -> np.ndarray | None:
    """boite = (gauche, haut, droite, bas) en px autour du centre de la case."""
    g, h, d, b = boite
    x, y = round(centre[0]), round(centre[1])
    if y - h < 0 or x - g < 0 or y + b > image.shape[0] or x + d > image.shape[1]:
        return None
    return image[y - h:y + b, x - g:x + d]


def construire_gabarit(exemplaires: list[np.ndarray], recherche: int = 4) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """Médiane d'exemplaires d'un même graphisme, après alignement (± recherche px) sur le premier.

    Chaque exemplaire est découpé avec une marge de `recherche` px de chaque côté. Renvoie l'image médiane, le masque
    des pixels stables (255) et le score d'alignement de chaque exemplaire."""
    ref = exemplaires[0][recherche:-recherche, recherche:-recherche]
    alignes, scores = [], []
    for ex in exemplaires:
        res = cv2.matchTemplate(ex, ref, cv2.TM_CCOEFF_NORMED)
        _, s, _, (dx, dy) = cv2.minMaxLoc(res)
        alignes.append(ex[dy:dy + ref.shape[0], dx:dx + ref.shape[1]])
        scores.append(float(s))
    pile = np.stack(alignes).astype(np.int16)
    mediane = np.median(pile, axis=0).astype(np.uint8)
    ecart = np.abs(pile - mediane.astype(np.int16)).max(axis=3)
    stable = (ecart <= ECART_MASQUE).mean(axis=0) >= STABLE_MIN
    masque = cv2.morphologyEx(stable.astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return mediane, masque, scores


def construire_gabarits(definitions: Path = DEFINITIONS, sortie: Path = SORTIE / "gabarits") -> list[dict]:
    """Lit gabarits.json ({nom: {ressource, etat, boite, sources: [[fichier, i, j], ...]}}) et écrit les gabarits."""
    bilan = []
    for nom, d in json.loads(definitions.read_text(encoding="utf-8")).items():
        g, h, dr, b = d["boite"]
        r = 4
        exemplaires = []
        for fichier, i, j in d["sources"]:
            image = lire_image(RACINE / fichier)
            frame = Frame.of(image)
            ex = decouper(image, centre_de_case(frame, (i, j)), (g + r, h + r, dr + r, b + r))
            if ex is not None:
                exemplaires.append(ex)
        mediane, masque, scores = construire_gabarit(exemplaires, r)
        ecrire_image(sortie / f"{nom}.png", mediane)
        ecrire_image(sortie / f"{nom}_masque.png", masque)
        bilan.append({"nom": nom, "exemplaires": len(exemplaires), "alignement": [round(s, 2) for s in scores],
                      "masque": round(float((masque > 0).mean()), 2)})
    return bilan


def charger_gabarits(definitions: Path = DEFINITIONS, dossier: Path = SORTIE / "gabarits") -> list[Gabarit]:
    gabarits = []
    for nom, d in json.loads(definitions.read_text(encoding="utf-8")).items():
        image = lire_image(dossier / f"{nom}.png")
        masque = cv2.imdecode(np.fromfile(str(dossier / f"{nom}_masque.png"), np.uint8), cv2.IMREAD_GRAYSCALE)
        g, h, _, _ = d["boite"]
        gabarits.append(Gabarit(nom, d["ressource"], d["etat"], image, masque if d.get("masque", True) else None, (g, h)))
    return gabarits


def partie_basse(gab: Gabarit, hauteur: int = 110) -> Gabarit:
    """Bas d'un arbre (pied du tronc et bas du feuillage, `hauteur` px au-dessus de la case) : retrouve les arbres
    coupés par le haut de la carte ou masqués en partie par un décor posé devant."""
    ax, ay = gab.ancre
    haut = max(0, ay - hauteur)
    masque = None if gab.masque is None else gab.masque[haut:]
    return Gabarit(gab.nom + "_bas", gab.ressource, gab.etat, gab.image[haut:], masque, (ax, ay - haut))


def une_capture_par_carte(index: list[dict]) -> list[dict]:
    """La première capture de chaque carte (hors captures touche A), pour compter les objets une seule fois."""
    vues, lignes = set(), []
    for ligne in index:
        if ligne["carte"] and ligne["carte"] not in vues and "sorties_touche_a" not in ligne["fichier"]:
            vues.add(ligne["carte"])
            lignes.append(ligne)
    return lignes


# --- Repérage --------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Detection:
    ressource: str
    etat: str
    gabarit: str
    case: tuple[int, int]
    point: tuple[int, int]  # centre de la case (où cliquer)
    score: float
    miroir: bool


def _score(zone_img: np.ndarray, gab: Gabarit, x: int, y: int, recherche: int) -> float | None:
    """Meilleur score du gabarit posé avec son ancre en (x, y) (repère de zone_img), à ± recherche px. Le gabarit est
    rogné à la partie visible ; None si trop peu en est visible."""
    h, w = gab.image.shape[:2]
    ax, ay = gab.ancre
    gauche, haut = x - ax, y - ay
    H, W = zone_img.shape[:2]
    v0, v1 = max(0, -haut + recherche), min(h, H - haut - recherche)
    u0, u1 = max(0, -gauche + recherche), min(w, W - gauche - recherche)
    if v1 <= v0 or u1 <= u0 or (v1 - v0) * (u1 - u0) < VISIBLE_MIN * h * w:
        return None
    tpl = gab.image[v0:v1, u0:u1]
    masque = None if gab.masque is None else gab.masque[v0:v1, u0:u1]
    if masque is not None and cv2.countNonZero(masque) < 50:
        return None
    region = zone_img[haut + v0 - recherche:haut + v1 + recherche, gauche + u0 - recherche:gauche + u1 + recherche]
    res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED, mask=masque)
    res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
    return float(res.max())


def scores_par_case(frame: Frame, gab: Gabarit, recherche: int = RECHERCHE) -> dict[tuple[int, int], tuple[float, bool]]:
    """Score du gabarit (et de son miroir) sur chaque case de la carte : {case : (score, miroir)}."""
    x0, y0, x1, y1 = frame.layout.map_box
    zone = frame.image[y0:y1, x0:x1]
    H, W = zone.shape[:2]
    scores: dict[tuple[int, int], tuple[float, bool]] = {}
    for g, miroir in ((gab, False), (gab.retourne(), True)):
        h, w = g.image.shape[:2]
        ax, ay = g.ancre
        # Un seul calcul sur toute la carte pour les cases où le gabarit tient en entier ; les autres (bords) au cas par cas.
        entier = None
        if H >= h and W >= w:
            entier = cv2.matchTemplate(zone, g.image, cv2.TM_CCOEFF_NORMED, mask=g.masque)
            entier = np.nan_to_num(entier, nan=-1.0, posinf=-1.0, neginf=-1.0)
        for case in frame.grid.cells():
            cx, cy = frame.grid.center(case)
            gauche, haut = round(cx) - x0 - ax, round(cy) - y0 - ay
            if (entier is not None and gauche - recherche >= 0 and haut - recherche >= 0
                    and gauche + recherche <= W - w and haut + recherche <= H - h):
                s = float(entier[haut - recherche:haut + recherche + 1, gauche - recherche:gauche + recherche + 1].max())
            else:
                s = _score(zone, g, round(cx) - x0, round(cy) - y0, recherche)
            if s is not None and s > scores.get(case, (-2.0, False))[0]:
                scores[case] = (s, miroir)
    return scores


def detecter(frame: Frame, gabarits: list[Gabarit], seuil: float = SEUIL) -> list[Detection]:
    candidats = []
    for gab in gabarits:
        for case, (s, miroir) in scores_par_case(frame, gab).items():
            if s >= seuil:
                cx, cy = frame.grid.center(case)
                candidats.append(Detection(gab.ressource, gab.etat, gab.nom, case, (round(cx), round(cy)), s, miroir))
    retenues: list[Detection] = []
    for d in sorted(candidats, key=lambda d: -d.score):
        if all(math.dist(d.point, r.point) >= NMS for r in retenues):
            retenues.append(d)
    return retenues


# --- Bilan -----------------------------------------------------------------------------------------------------------

def comparer(detections: list[Detection], attendu: dict[str, int], ressources: list[str]) -> dict[str, dict]:
    """Par ressource : debout, coupées, total trouvé, attendu (référence), trouvées dans la limite de l'attendu,
    en trop (au moins autant de fausses détections), manquantes."""
    bilan = {}
    for r in ressources:
        debout = sum(1 for d in detections if d.ressource == r and d.etat == "debout")
        coupees = sum(1 for d in detections if d.ressource == r and d.etat != "debout")
        total, n = debout + coupees, attendu.get(r, 0)
        bilan[r] = {"debout": debout, "coupees": coupees, "trouvees": total, "attendues": n,
                    "dans_l_attendu": min(total, n), "en_trop": max(0, total - n), "manquantes": max(0, n - total)}
    return bilan


def planche(image: np.ndarray, detections: list[Detection], largeur: int = 120, dessus: int = 200, dessous: int = 40,
            echelle: float = 0.75, par_ligne: int = 12) -> np.ndarray | None:
    """Vignettes des détections (case marquée d'un point rouge), avec ressource, état et score, à vérifier à l'œil."""
    vignettes = []
    for n, d in enumerate(detections):
        x, y = d.point
        v = np.zeros((dessus + dessous, largeur, 3), np.uint8)
        y0, x0 = max(0, y - dessus), max(0, x - largeur // 2)
        morceau = image[y0:y + dessous, x0:x0 + largeur]
        v[dessus - (y - y0):dessus - (y - y0) + morceau.shape[0], :morceau.shape[1]] = morceau
        cv2.circle(v, (x - x0, dessus), 3, (0, 0, 255), -1)
        v = cv2.resize(v, None, fx=echelle, fy=echelle, interpolation=cv2.INTER_AREA)
        v = cv2.copyMakeBorder(v, 16, 2, 0, 2, cv2.BORDER_CONSTANT)
        texte = f"{n + 1} {d.ressource[:4]}{'' if d.etat == 'debout' else '-s'} {d.score:.2f}"
        cv2.putText(v, texte, (2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (255, 255, 255), 1, cv2.LINE_AA)
        vignettes.append(v)
    if not vignettes:
        return None
    vide = np.zeros_like(vignettes[0])
    lignes = [np.hstack(vignettes[i:i + par_ligne] + [vide] * (par_ligne - len(vignettes[i:i + par_ligne])))
              for i in range(0, len(vignettes), par_ligne)]
    return np.vstack(lignes)


def totaliser(captures: list[dict], ressources: list[str], seuil: float, seuil_souche: float = 0.0) -> dict[str, dict]:
    """Bilan sur toutes les captures, au seuil donné (détections gardées : score >= seuil, et >= seuil_souche pour les
    souches, plus faciles à confondre avec le pied d'un arbre ou une autre souche)."""
    total = {}
    for r in ressources:
        bilans = []
        for c in captures:
            dets = [Detection(d["ressource"], d["etat"], d.get("gabarit", ""), tuple(d["case"]), tuple(d["point"]),
                              d["score"], d["miroir"]) for d in c["detections"]
                    if d["score"] >= (seuil if d["etat"] == "debout" else max(seuil, seuil_souche))]
            bilans.append(comparer(dets, c["attendu"], [r])[r])
        avec = [b for b in bilans if b["attendues"] > 0]
        sans = [b for b in bilans if b["attendues"] == 0]
        total[r] = {
            "captures_avec": len(avec), "attendues": sum(b["attendues"] for b in avec),
            "trouvees": sum(b["trouvees"] for b in avec), "dont_coupees": sum(b["coupees"] for b in avec),
            "trouvees_dans_l_attendu": sum(b["dans_l_attendu"] for b in avec),
            "en_trop_cartes_avec": sum(b["en_trop"] for b in avec),
            "captures_sans": len(sans), "fausses_cartes_sans": sum(b["trouvees"] for b in sans),
            "captures_sans_avec_fausse": sum(1 for b in sans if b["trouvees"]),
        }
    return total


def essai(index: list[dict], gabarits: list[Gabarit], seuils: tuple[float, ...] = (0.5, 0.55, 0.6, 0.65, 0.7),
          sortie: Path = SORTIE, planches: bool = True, cartes_sources: set[str] = frozenset(),
          nom: str = "resultats.json", seuil_souche: float = 0.0) -> dict:
    """Détecte sur chaque capture de carte connue (au plus bas des seuils), puis fait le bilan pour chaque seuil,
    séparément pour les cartes qui ont fourni des exemplaires aux gabarits et pour les autres."""
    ressources = sorted({g.ressource for g in gabarits})
    captures = []
    for ligne in index:
        if not ligne["carte"]:
            continue
        image = lire_image(RACINE / ligne["fichier"])
        detections = detecter(Frame.of(image), gabarits, min(seuils))
        captures.append({"fichier": ligne["fichier"], "carte": ligne["carte"], "zone": ligne["zone"],
                         "attendu": ligne["attendu"], "carte_source": ligne["carte"] in cartes_sources,
                         "detections": [{"ressource": d.ressource, "etat": d.etat, "gabarit": d.gabarit,
                                         "case": list(d.case), "point": list(d.point), "score": round(d.score, 3),
                                         "miroir": d.miroir} for d in detections]})
        if planches and detections:
            nom = Path(ligne["fichier"]).parent.name + "_" + Path(ligne["fichier"]).stem[:4] + f"_{ligne['carte']}.jpg"
            ecrire_image(sortie / "planches" / nom, planche(image, detections))
    bilans = {}
    for seuil in seuils:
        bilans[str(seuil)] = {
            "toutes": totaliser(captures, ressources, seuil, seuil_souche),
            "cartes_sources": totaliser([c for c in captures if c["carte_source"]], ressources, seuil, seuil_souche),
            "autres_cartes": totaliser([c for c in captures if not c["carte_source"]], ressources, seuil, seuil_souche),
        }
    resultat = {"seuils": list(seuils), "seuil_souche": seuil_souche, "gabarits": [g.nom for g in gabarits],
                "bilans": bilans, "captures": captures}
    sortie.mkdir(parents=True, exist_ok=True)
    (sortie / nom).write_text(json.dumps(resultat, ensure_ascii=False, indent=1), encoding="utf-8")
    return resultat


def cartes_des_sources(definitions: Path, index: list[dict]) -> set[str]:
    """Cartes dont une capture a fourni un exemplaire à un gabarit."""
    fichiers = {s[0] for d in json.loads(definitions.read_text(encoding="utf-8")).values() for s in d["sources"]}
    return {l["carte"] for l in index if l["fichier"] in fichiers and l["carte"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--metiers", action="store_true")
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--gabarits", action="store_true")
    parser.add_argument("--essai", action="store_true")
    parser.add_argument("--seuils", default="0.5,0.55,0.6,0.65,0.7")
    parser.add_argument("--sans-planches", action="store_true")
    parser.add_argument("--seuil-souche", type=float, default=0.0)
    parser.add_argument("--bas", action="store_true", help="ajoute le bas des arbres (arbres coupés par le bord, masqués)")
    parser.add_argument("--une-par-carte", action="store_true", help="une seule capture par carte")
    args = parser.parse_args(argv)
    if args.metiers:
        afficher_metiers()
    if args.index:
        lignes = indexer()
        SORTIE.mkdir(parents=True, exist_ok=True)
        (SORTIE / "index.json").write_text(json.dumps(lignes, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(lignes)} captures, {sum(1 for l in lignes if l['carte'])} avec carte connue, "
              f"{sum(1 for l in lignes if l['attendu'])} sur une carte qui a des ressources dans la référence")
    if args.gabarits:
        for b in construire_gabarits():
            print(b)
    if args.essai:
        index = json.loads((SORTIE / "index.json").read_text(encoding="utf-8"))
        seuils = tuple(float(s) for s in args.seuils.split(","))
        gabarits = charger_gabarits()
        if args.bas:
            gabarits += [partie_basse(g) for g in gabarits if g.etat == "debout" and g.ancre[1] > 150]
        lignes = une_capture_par_carte(index) if args.une_par_carte else index
        nom = "resultats" + ("_bas" if args.bas else "") + ("_par_carte" if args.une_par_carte else "") + ".json"
        resultat = essai(lignes, gabarits, seuils, planches=not args.sans_planches and not args.une_par_carte,
                         cartes_sources=cartes_des_sources(DEFINITIONS, index), nom=nom,
                         seuil_souche=args.seuil_souche)
        for seuil, parties in resultat["bilans"].items():
            for partie, total in parties.items():
                for r, t in total.items():
                    print(seuil, partie, r, t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
