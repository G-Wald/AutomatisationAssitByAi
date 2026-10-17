"""Résumé court d'une longue session du bot (1 h de farm en équipe de 8) : seulement ce qu'il faut analyser.

Un journal d'une heure fait des dizaines de milliers de lignes (INFO et DEBUG). Ce résumé (moins de ~150 lignes) garde :
- la durée, les combats (temps de placement et de combat, tours, refus, suiveurs restés dehors), l'issue si elle est
  écrite (le bot ne l'écrit pas aujourd'hui : gagnés / perdus restent « non écrits ») ;
- les temps morts : avant le 1er combat, entre deux combats (dont repos, changements de carte, attaques ratées), au
  placement (suiveurs qui rejoignent) ;
- par personnage : tours, sorts, refus et le temps qu'ils coûtent, « Carte à relief », « Moi : ... cercle est caché »,
  déplacements ratés, combats pas rejoints ;
- les trajets : cartes, sorties bloquées, noms de carte illisibles, positions demandées par « /p %pos% », suiveurs
  perdus ;
- les anomalies notées en INFO (motifs « problème » de tools/bilan_sessions.py, captures .png) et les WARNING / ERROR,
  dédoublonnés (nombres, chemins et noms des personnages retirés), avec un compte et 1 ou 2 captures d'exemple.

Lit bot.log en flux (ligne par ligne) et combats.jsonl s'il existe. Écrit resume.md et resume.json dans le dossier de la
session (ou dans --sortie), et affiche le Markdown. Ne touche ni au jeu ni aux fichiers lus.

Depuis la racine du projet :
    .venv\\Scripts\\python -m tools.resume_session                          la dernière session de logs/
    .venv\\Scripts\\python -m tools.resume_session logs\\20261016_193753
    .venv\\Scripts\\python -m tools.resume_session logs\\20261016_193753 --sortie data\\resumes
    .venv\\Scripts\\python -m tools.bilan_sessions --resume logs\\20261016_193753     (même chose)
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable

from tools.bilan_sessions import LINE, LOGS_DIR, MOTIFS, SESSION_DIR, TAG, fr

MAX_COMBATS = 12  # lignes du tableau des combats (au-delà : les plus remarquables)
MAX_GROUPES = 20  # lignes des tableaux d'anomalies et d'avertissements
MAX_CARTES = 12  # cartes les plus visitées
REFUS_MAX = 20.0  # s : au-delà, le « lancé » retenu n'est plus celui du refus

CAPTURE = re.compile(r"capture : ([^)]*?\.(?:png|jpe?g))")  # « (capture : ...) », « (PA inchangés, capture : ...) »
CHEMIN = re.compile(r"[A-Z]:\\[^ )]*")
COORDS = re.compile(r"(-?\d+),(-?\d+)")
PROBLEMES = [m for m in MOTIFS if m.probleme]


def _regex(motif: str) -> re.Pattern:
    return re.compile(motif)


# Messages lus ici (écrits par bot/team/loop.py, bot/team/join.py, bot/brain/combat.py, engage.py, travel.py, rest.py,
# position.py, bot/__main__.py).
R = {
    "equipe": _regex(r"^Équipe : (.*) ; principal : (.+)$"),
    "mode": _regex(r"^Mode : (.*)$"),
    "limite": _regex(r"^Session limitée : (.*)$"),
    "fin_session": _regex(r"^Fin de la session : (.*)$"),
    "bot_arrete": _regex(r"^(?:Bot arrêté|Arrêté) \((.*)\)$"),
    "arret_demande": _regex(r"^Arrêt demandé : (.*)$"),
    "etat_placement": _regex(r"^État : placement"),
    "etat_combat": _regex(r"^État : combat"),
    "placement": _regex(r"^Placement : combat de (.+)$"),
    "combat_fin": _regex(r"^Combat terminé$"),
    "rejoint": _regex(r"^(.+?) a rejoint le combat$"),
    "pas_rejoint": _regex(r"^(.+?) n'a pas rejoint le combat"),
    "epee": _regex(r"^Je ne vois pas l'épée du combat|^Pas de menu « Rejoindre »"),
    "tour": _regex(r"^Tour : ([\d,.]+) s ; (.*)$"),
    "bilan": _regex(r"^Bilan du combat (\d+), (.+?) : (\d+) tour\(s\) \(([\d,]+) s\) ; (\d+) sort\(s\)(?: \((.*)\))? ; "
                    r"\d+ refus(?: ; (\d+) clic\(s\) sans effet)? ; (\d+) déplacement\(s\)(?: ; dont (\d+) sans bouger)?"),
    "sort_lance": _regex(r"^(.+?) lancé sur (.+)$"),
    "refus": _regex(r"^(.+?) refusé sur "),
    "pas_parti": _regex(r"^Le sort ne semble pas parti|^(.+?) ne semble pas parti par "),
    "relief": _regex(r"^Carte à relief \(ligne de vue prédite juste sur (\d+) %"),
    "cercle_cache": _regex(r"^Moi : .*cercle est caché"),
    "pas_bouge": _regex(r"^Mon personnage n'a pas bougé \(capture"),
    "bloque": _regex(r"^Je ne peux pas bouger \((.*?)\)"),
    "faux_tour": _regex(r"^Suite de mon tour "),
    "groupe_vise": _regex(r"^Groupe visé "),
    "attaque_ratee": _regex(r"^Pas de menu « Attaquer »"),
    "aucun_groupe": _regex(r"^Aucun groupe de monstres attaquable"),
    "plus_de_groupe": _regex(r"^Plus de groupe ici : carte du (\w+) \((-?\d+),(-?\d+)"),
    "assis": _regex(r"^Vie à (\d+) % : (?:je m'assois|tabouret introuvable)"),
    "repars": _regex(r"^Vie à (\d+) % : je repars"),
    "trajet_lance": _regex(r"^Trajet vers (-?\d+),(-?\d+) .*départ : (.*)$"),
    "trajet_arrete": _regex(r"^Trajet vers (-?\d+),(-?\d+) arrêté : (.*)$"),
    "arrive": _regex(r"^Arrivé en (-?\d+),(-?\d+)"),
    "clic_soleil": _regex(r"^Vers la carte du (\w+) : clic sur le soleil"),
    "nouvelle_carte": _regex(r"^Nouvelle carte : (.*)$"),
    "sortie_bloquee": _regex(r"^Sortie du (\w+) inutilisable|^Pas de sortie vers le (\w+)|^Sortie du (\w+) bloquée pour "
                             r"ce trajet|^Sortie du (\w+) notée inatteignable"),
    "nom_illisible": _regex(r"^Nom de la carte (?:de départ )?illisible"),
    "position_ok": _regex(r"^Position donnée par le jeu .* : (-?\d+),(-?\d+)"),
    "position_rate": _regex(r"%pos%.*pas de position lue"),
    "suiveur_perdu": _regex(r"^(.+?) (?:n'a pas suivi le principal|est arrivé sur .*, pas sur la carte du principal|"
                            r"n'est pas sur la carte du principal|n'est pas sur la carte que quitte|est passé sans le "
                            r"principal)"),
    "regroupe": _regex(r"^(.+?) a rejoint le principal en "),
}

# Compteurs de la ligne « Tour : 10,5 s ; 1 sort, 1 déplacement, 1 refus, 1 clic sans effet, 1 déplacement sans
# bouger ; attentes : fixe » (bot/brain/combat.py).
TOUR_PART = re.compile(r"(\d+) (sorts?|déplacements? sans bouger|déplacements?|refus|clics? sans effet)")


def _tour_part(mot: str) -> str:
    if "sans bouger" in mot:
        return "sans_bouger"
    if mot.startswith("déplacement"):
        return "deplacements"
    if mot.startswith("clic"):
        return "clics_sans_effet"
    return "refus" if mot == "refus" else "sorts"


@dataclass
class Combat:
    numero: int
    placement: datetime
    debut: datetime | None = None  # premier « État : combat »
    fin: datetime | None = None
    tours: int = 0  # lignes « Tour : » ; remplacées par les « Bilan du combat » s'il y en a
    tours_bilan: int | None = None
    refus: int = 0
    rejoints: dict[str, float] = field(default_factory=dict)  # suiveur -> s depuis le début du placement
    pas_rejoints: list[str] = field(default_factory=list)
    relief: int = 0
    caches: int = 0
    pas_bouge: int = 0
    interrompu: bool = False

    def duree_placement(self) -> float | None:
        return (self.debut - self.placement).total_seconds() if self.debut else None

    def duree_combat(self) -> float | None:
        return (self.fin - self.debut).total_seconds() if self.debut and self.fin else None


@dataclass
class Perso:
    combats: int = 0  # lignes « Bilan du combat »
    combats_sans_tour: int = 0
    tours: int = 0
    duree_tours: float = 0.0
    tour_max: float = 0.0
    sorts: Counter = field(default_factory=Counter)
    refus: int = 0
    temps_refus: float = 0.0
    clics_sans_effet: int = 0
    temps_sans_effet: float = 0.0
    deplacements: int = 0
    sans_bouger: int = 0
    pas_bouge: int = 0  # « Mon personnage n'a pas bougé (capture) »
    bloque: Counter = field(default_factory=Counter)  # raison de « Je ne peux pas bouger »
    relief: int = 0
    caches: int = 0
    faux_tours: int = 0
    pas_rejoints: int = 0
    epee_introuvable: int = 0
    attente_rejoindre: list[float] = field(default_factory=list)


@dataclass
class Groupe:
    """Messages semblables (même texte une fois nombres, chemins et noms retirés)."""
    libelle: str
    niveau: str
    n: int = 0
    persos: Counter = field(default_factory=Counter)
    exemple: str = ""
    premiere: str = ""
    captures: list[str] = field(default_factory=list)


def _secondes(texte: str) -> float:
    return float(texte.replace(",", "."))


def duree_texte(secondes: float | None) -> str:
    if secondes is None:
        return "–"
    s = int(round(secondes))
    if s < 60:
        return f"{fr(secondes)} s" if secondes < 10 else f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"


def stats_texte(valeurs: list[float]) -> str:
    if not valeurs:
        return "–"
    return (f"moy. {duree_texte(statistics.fmean(valeurs))}, méd. {duree_texte(statistics.median(valeurs))}, "
            f"max {duree_texte(max(valeurs))} (n = {len(valeurs)})")


def _carte(texte: str) -> str | None:
    """« Incarnam (Champs) 6,2 » ou « carte au nom illisible (coordonnées 6,1 d'après le chemin) » -> « 6,2 »."""
    trouves = COORDS.findall(texte)
    return f"{trouves[-1][0]},{trouves[-1][1]}" if trouves else None


class Resumeur:
    """Lit un journal ligne par ligne (`lire`), puis `resume` rend le dictionnaire du résumé."""

    def __init__(self, nom: str, jour: date | None = None) -> None:
        self.nom = nom
        self._jour = jour or date(2000, 1, 1)
        self._dernier: datetime | None = None
        self.debut: datetime | None = None
        self.fin: datetime | None = None
        self.lignes = 0
        self.niveaux: Counter = Counter()
        self.equipe: list[str] = []
        self.principal: str | None = None
        self.mode = ""
        self.limite = ""
        self.raison_fin = ""
        self.derniere_ligne = ""
        self.combats: list[Combat] = []
        self._combat: Combat | None = None
        self.persos: dict[str, Perso] = defaultdict(Perso)
        # Tours du combat en cours lus aux lignes « Tour : », par personnage : remplacés par sa ligne « Bilan du
        # combat » (le dernier tour d'un combat n'a pas de ligne « Tour : », un sort refusé a sa ligne « lancé »), gardés
        # pour un combat sans bilan (journal coupé).
        self._en_cours: dict[str, Perso] = defaultdict(Perso)
        self._dernier_combat: Combat | None = None
        self._lance: dict[str, datetime] = {}  # dernier « X lancé sur » de chaque personnage
        # Temps morts hors combat
        self.repos = 0.0
        self._assis: datetime | None = None
        self.temps_cartes = 0.0  # changements de carte du principal hors combat (clic -> « Nouvelle carte »)
        self._clic: datetime | None = None
        self.attaques_ratees = 0
        self.temps_attaques_ratees = 0.0
        self._vise: datetime | None = None
        self.aucun_groupe = 0
        self.plus_de_groupe = 0
        # Trajets
        self.trajets_lances = 0
        self.trajets_arrives = 0
        self.trajets_arretes: list[str] = []
        self.cartes: Counter = Counter()
        self.changements: list[float] = []
        self.carte_actuelle: str | None = None
        self.sorties_bloquees: list[str] = []
        self.noms_illisibles = 0
        self.captures_illisibles: list[str] = []
        self.positions_ok: list[str] = []
        self.positions_ratees = 0
        self.captures_positions: list[str] = []
        self.suiveurs_perdus: Counter = Counter()
        self.regroupes: Counter = Counter()
        # Anomalies et avertissements
        self.anomalies: dict[str, Groupe] = {}
        self.avertissements: dict[str, Groupe] = {}
        self._trace: list[str] | None = None

    # Lecture

    def lire(self, brute: str) -> None:
        ligne = brute.rstrip("\r\n")
        m = LINE.match(ligne)
        if m is None:
            self._hors_format(ligne)
            return
        self._fin_trace()
        h, mi, se, ms, niveau, reste = m.groups()
        t = self._heure(int(h), int(mi), int(se), int((ms or "0").ljust(3, "0")))
        acteur, message = "", reste
        tag = TAG.match(reste)
        if tag:
            acteur, message = tag.group(1), tag.group(2)
        self.debut = self.debut or t
        self.fin = t
        self.lignes += 1
        self.niveaux[niveau] += 1
        if niveau == "DEBUG":
            return
        self.derniere_ligne = f"{ligne[:12]} {message[:140]}"
        self._evenement(t, acteur, message)
        if niveau in ("WARNING", "ERROR", "CRITICAL"):
            self._grouper(self.avertissements, self._cle(message), niveau, acteur, message, ligne)
        else:
            self._anomalie(niveau, acteur, message, ligne)

    def _hors_format(self, ligne: str) -> None:
        """Traceback : regroupé par sa dernière ligne (le type de l'exception)."""
        if ligne.startswith("Traceback"):
            self._fin_trace()
            self._trace = [ligne]
        elif self._trace is not None and ligne.strip():
            self._trace.append(ligne)

    def _fin_trace(self) -> None:
        if self._trace is None:
            return
        derniere = self._trace[-1].strip() if len(self._trace) > 1 else self._trace[0]
        self._trace = None
        self._grouper(self.avertissements, "exception : " + self._cle(derniere), "EXCEPTION", "", derniere, derniere)

    def _heure(self, h: int, mi: int, se: int, ms: int) -> datetime:
        t = datetime.combine(self._jour, time(h, mi, se, ms * 1000))
        if self._dernier is not None and t < self._dernier - timedelta(hours=12):
            self._jour += timedelta(days=1)  # passage de minuit
            t += timedelta(days=1)
        self._dernier = t
        return t

    def _est_principal(self, acteur: str) -> bool:
        return self.principal is None or not acteur or acteur == self.principal

    # Regroupement des messages

    def _cle(self, message: str) -> str:
        message = CAPTURE.sub("capture", message)
        message = CHEMIN.sub("<chemin>", message)
        for nom in sorted(self.equipe, key=len, reverse=True):
            message = message.replace(nom, "<perso>")
        return re.sub(r"-?\d+(?:[.,]\d+)?", "N", message)[:170]

    def _grouper(self, groupes: dict[str, Groupe], cle: str, niveau: str, acteur: str, message: str,
                 ligne: str) -> None:
        g = groupes.get(cle)
        if g is None:
            exemple = CAPTURE.sub(lambda c: f"capture : {Path(c.group(1)).name}", message)
            g = groupes[cle] = Groupe(libelle=cle, niveau=niveau, exemple=CHEMIN.sub("<chemin>", exemple)[:200],
                                      premiere=ligne[:8])
        g.n += 1
        if acteur:
            g.persos[acteur] += 1
        capture = CAPTURE.search(message)
        if capture and len(g.captures) < 2:
            g.captures.append(Path(capture.group(1)).name)

    def _anomalie(self, niveau: str, acteur: str, message: str, ligne: str) -> None:
        for motif in PROBLEMES:
            if motif.regex.search(message):
                self._grouper(self.anomalies, motif.libelle, niveau, acteur, message, ligne)
                return
        capture = CAPTURE.search(message)
        if capture and Path(capture.group(1)).suffix.lower() == ".png":
            # Capture d'anomalie (les .png ; les .jpg sont les captures de routine) : nom du type dans le fichier.
            type_ = re.sub(r"^\d+_[^_]+_", "", Path(capture.group(1)).stem).replace("_", " ")
            self._grouper(self.anomalies, f"capture « {type_} »", niveau, acteur, message, ligne)

    # Événements

    def _evenement(self, t: datetime, acteur: str, message: str) -> None:
        r = R
        perso = self.persos[acteur] if acteur else None
        if m := r["equipe"].search(message):
            self.equipe = [n.strip() for n in m.group(1).split(",") if n.strip()]
            self.principal = m.group(2).strip()
            return
        if m := r["mode"].search(message):
            self.mode = self.mode or m.group(1)
            return
        if m := r["limite"].search(message):
            self.limite = m.group(1)
            return
        for cle in ("fin_session", "bot_arrete", "arret_demande"):
            if m := r[cle].search(message):
                self.raison_fin = self.raison_fin or m.group(1)
                return
        # Combats
        if r["etat_placement"].search(message) or r["placement"].search(message):
            if self._combat is None:
                self._fin_repos(t)
                self._vider_en_cours()
                self._vise = self._clic = None
                self._combat = Combat(numero=len(self.combats) + 1, placement=t)
                self.combats.append(self._combat)
            return
        c = self._combat
        if r["etat_combat"].search(message):
            if c is not None and c.debut is None:
                c.debut = t
            return
        if r["combat_fin"].search(message):
            if c is not None:
                c.debut = c.debut or t
                c.fin = t
                self._dernier_combat = c
            self._combat = None
            return
        if m := r["bilan"].search(message):
            self._bilan(m)
            return
        if m := r["rejoint"].search(message):
            if c is not None and perso is not None:
                attente = (t - c.placement).total_seconds()
                c.rejoints[acteur] = attente
                perso.attente_rejoindre.append(attente)
            return
        if m := r["pas_rejoint"].search(message):
            if c is not None:
                c.pas_rejoints.append(m.group(1))
            if perso is not None:
                perso.pas_rejoints += 1
            return
        if r["epee"].search(message):
            if perso is not None:
                perso.epee_introuvable += 1
            return
        if m := r["tour"].search(message):
            if perso is not None:
                duree = _secondes(m.group(1))
                perso.tour_max = max(perso.tour_max, duree)
                b = self._en_cours[acteur]
                b.tours += 1
                b.duree_tours += duree
                parts = Counter()
                for nombre, mot in TOUR_PART.findall(m.group(2)):
                    parts[_tour_part(mot)] += int(nombre)
                b.deplacements += parts["deplacements"]
                b.sans_bouger += parts["sans_bouger"]
                b.clics_sans_effet += parts["clics_sans_effet"]
            if c is not None:
                c.tours += 1
            return
        if m := r["refus"].search(message):
            if perso is not None:
                perso.refus += 1
                perso.temps_refus += self._depuis_lance(acteur, t)
                sorts = self._en_cours[acteur].sorts
                if sorts[m.group(1)] > 0:
                    sorts[m.group(1)] -= 1
            if c is not None:
                c.refus += 1
            return
        if r["pas_parti"].search(message):
            if perso is not None:
                perso.temps_sans_effet += self._depuis_lance(acteur, t)
            return
        if m := r["sort_lance"].search(message):
            if perso is not None and not message.startswith("Je "):
                self._en_cours[acteur].sorts[m.group(1)] += 1
                self._lance[acteur] = t
            return
        if r["relief"].search(message):
            if perso is not None:
                perso.relief += 1
            if c is not None:
                c.relief += 1
            return
        if r["cercle_cache"].search(message):
            if perso is not None:
                perso.caches += 1
            if c is not None:
                c.caches += 1
            return
        if r["pas_bouge"].search(message):
            if perso is not None:
                perso.pas_bouge += 1
            if c is not None:
                c.pas_bouge += 1
            return
        if m := r["bloque"].search(message):
            if perso is not None:
                perso.bloque[m.group(1)] += 1
            return
        if r["faux_tour"].search(message):
            if perso is not None:
                perso.faux_tours += 1
            return
        # Recherche de groupe, repos
        if r["groupe_vise"].search(message):
            self._vise = self._vise or t
            return
        if r["attaque_ratee"].search(message):
            self.attaques_ratees += 1
            if self._vise is not None:
                self.temps_attaques_ratees += (t - self._vise).total_seconds()
            self._vise = None
            return
        if r["aucun_groupe"].search(message):
            self.aucun_groupe += 1
            return
        if r["plus_de_groupe"].search(message):
            self.plus_de_groupe += 1
            return
        if r["assis"].search(message):
            self._assis = self._assis or t
            return
        if r["repars"].search(message):
            self._fin_repos(t)
            return
        self._trajet(t, acteur, message)

    def _bilan(self, m: re.Match) -> None:
        """« Bilan du combat 1, Koalaloupour : 7 tour(s) (44,3 s) ; 7 sort(s) (Ronce x7) ; 2 refus ; 2 clic(s) sans
        effet ; 6 déplacement(s) ; dont 2 sans bouger » (bot/debug/report.py), écrit par la fenêtre d'un autre."""
        _, nom, tours, duree, _, sorts, clics, deplacements, sans_bouger = m.groups()
        lus = self._en_cours.pop(nom, None) or Perso()
        p = self.persos[nom]
        p.combats += 1
        p.combats_sans_tour += int(tours) == 0
        p.tours += int(tours)
        p.duree_tours += _secondes(duree)
        if int(tours) == lus.tours + 1:  # le dernier tour, sans ligne « Tour : »
            p.tour_max = max(p.tour_max, _secondes(duree) - lus.duree_tours)
        for sort in (sorts or "").split(", "):
            if " x" in sort:
                nom_sort, nombre = sort.rsplit(" x", 1)
                p.sorts[nom_sort] += int(nombre)
        p.clics_sans_effet += int(clics or 0)
        p.deplacements += int(deplacements)
        p.sans_bouger += int(sans_bouger or 0)
        if self._dernier_combat is not None:
            self._dernier_combat.tours_bilan = (self._dernier_combat.tours_bilan or 0) + int(tours)

    def _vider_en_cours(self) -> None:
        for nom, b in self._en_cours.items():
            p = self.persos[nom]
            p.tours += b.tours
            p.duree_tours += b.duree_tours
            p.sorts.update({k: v for k, v in b.sorts.items() if v > 0})
            p.clics_sans_effet += b.clics_sans_effet
            p.deplacements += b.deplacements
            p.sans_bouger += b.sans_bouger
        self._en_cours.clear()

    def _depuis_lance(self, acteur: str, t: datetime) -> float:
        lance = self._lance.pop(acteur, None)
        if lance is None:
            return 0.0
        ecart = (t - lance).total_seconds()
        return ecart if 0 <= ecart <= REFUS_MAX else 0.0

    def _fin_repos(self, t: datetime) -> None:
        if self._assis is not None:
            self.repos += (t - self._assis).total_seconds()
            self._assis = None

    def _trajet(self, t: datetime, acteur: str, message: str) -> None:
        r = R
        principal = self._est_principal(acteur)
        if r["trajet_lance"].search(message):
            self.trajets_lances += 1
        elif m := r["trajet_arrete"].search(message):
            self.trajets_arretes.append(f"vers {m.group(1)},{m.group(2)} : {m.group(3)[:110]}")
        elif r["arrive"].search(message):
            self.trajets_arrives += 1
        elif r["clic_soleil"].search(message):
            if principal:
                self._clic = self._clic or t
        elif m := r["nouvelle_carte"].search(message):
            if principal:
                carte = _carte(m.group(1)) or "illisible"
                self.cartes[carte] += 1
                self.carte_actuelle = carte
                if self._clic is not None:
                    ecart = (t - self._clic).total_seconds()
                    self.changements.append(ecart)
                    if self._combat is None:
                        self.temps_cartes += ecart
                self._clic = None
        elif m := r["sortie_bloquee"].search(message):
            cote = next(g for g in m.groups() if g)
            self.sorties_bloquees.append(f"{self.carte_actuelle or '?'} {cote} : {message[:90]}")
        elif r["nom_illisible"].search(message):
            if principal:
                self.noms_illisibles += 1
                capture = CAPTURE.search(message)
                if capture and len(self.captures_illisibles) < 2:
                    self.captures_illisibles.append(Path(capture.group(1)).name)
            if (carte := _carte(message.split("capture :")[0])) and principal:
                self.carte_actuelle = carte
        elif m := r["position_ok"].search(message):
            self.positions_ok.append(f"{m.group(1)},{m.group(2)}")
            self.carte_actuelle = f"{m.group(1)},{m.group(2)}"
        elif r["position_rate"].search(message):
            self.positions_ratees += 1
            capture = CAPTURE.search(message)
            if capture and len(self.captures_positions) < 2:
                self.captures_positions.append(Path(capture.group(1)).name)
        elif m := r["suiveur_perdu"].search(message):
            self.suiveurs_perdus[m.group(1)] += 1
        elif m := r["regroupe"].search(message):
            self.regroupes[m.group(1)] += 1

    # Résultat

    def resume(self, combats_jsonl: int | None = None) -> dict:
        self._fin_trace()
        fin = self.fin
        if self._combat is not None and fin is not None:
            self._combat.interrompu = True
            self._combat.debut = self._combat.debut or fin
            self._combat.fin = fin
        if fin is not None:
            self._fin_repos(fin)
        self._vider_en_cours()
        total = (fin - self.debut).total_seconds() if self.debut and fin else 0.0
        combats = self.combats
        placement = sum(c.duree_placement() or 0 for c in combats)
        en_combat = sum(c.duree_combat() or 0 for c in combats)
        avant = (combats[0].placement - self.debut).total_seconds() if combats and self.debut else None
        apres = (fin - combats[-1].fin).total_seconds() if combats and combats[-1].fin and fin else None
        entre = [(b.placement - a.fin).total_seconds() for a, b in zip(combats, combats[1:]) if a.fin]
        hors_combat = total - placement - en_combat
        autres = max(0.0, hors_combat - self.repos - self.temps_cartes - self.temps_attaques_ratees)
        persos = {}
        ordre = self.equipe + sorted(n for n in self.persos if n not in self.equipe)
        for nom in ordre:
            p = self.persos.get(nom)
            if p is None:
                continue
            persos[nom] = {
                "combats": p.combats, "combats_sans_tour": p.combats_sans_tour, "tours": p.tours, "duree_tours": round(p.duree_tours, 1), "tour_max": p.tour_max,
                "sorts": dict(p.sorts.most_common()), "refus": p.refus, "temps_refus": round(p.temps_refus, 1),
                "clics_sans_effet": p.clics_sans_effet, "temps_sans_effet": round(p.temps_sans_effet, 1),
                "deplacements": p.deplacements, "deplacements_sans_bouger": p.sans_bouger,
                "pas_bouge": p.pas_bouge, "bloque": dict(p.bloque), "carte_a_relief": p.relief,
                "cercle_cache": p.caches, "faux_tours": p.faux_tours, "pas_rejoint": p.pas_rejoints,
                "epee_introuvable": p.epee_introuvable,
                "attente_rejoindre_moy": round(statistics.fmean(p.attente_rejoindre), 1)
                if p.attente_rejoindre else None,
            }
        return {
            "session": self.nom,
            "debut": self.debut.isoformat(timespec="seconds") if self.debut else None,
            "fin": fin.isoformat(timespec="seconds") if fin else None,
            "duree_s": round(total, 1),
            "lignes": self.lignes, "niveaux": dict(self.niveaux),
            "equipe": self.equipe, "principal": self.principal, "mode": self.mode, "limite": self.limite,
            "raison_fin": self.raison_fin or None, "derniere_ligne": self.derniere_ligne,
            "combats": [{
                "numero": c.numero, "placement": c.placement.strftime("%H:%M:%S"),
                "duree_placement_s": _r(c.duree_placement()), "duree_combat_s": _r(c.duree_combat()),
                "tours": c.tours if c.tours_bilan is None else c.tours_bilan, "refus": c.refus, "pas_rejoints": c.pas_rejoints,
                "rejoindre_max_s": _r(max(c.rejoints.values())) if c.rejoints else None,
                "carte_a_relief": c.relief, "cercle_cache": c.caches, "pas_bouge": c.pas_bouge,
                "interrompu": c.interrompu, "issue": None,
            } for c in combats],
            "combats_jsonl": combats_jsonl,
            "temps": {
                "en_combat_s": round(en_combat, 1), "placement_s": round(placement, 1),
                "hors_combat_s": round(hors_combat, 1), "avant_premier_combat_s": _r(avant),
                "apres_dernier_combat_s": _r(apres), "entre_combats_s": [round(x, 1) for x in entre],
                "repos_s": round(self.repos, 1), "changements_de_carte_s": round(self.temps_cartes, 1),
                "attaques_ratees_s": round(self.temps_attaques_ratees, 1), "recherche_et_autres_s": round(autres, 1),
                "attaques_ratees": self.attaques_ratees, "aucun_groupe": self.aucun_groupe,
                "plus_de_groupe": self.plus_de_groupe,
            },
            "personnages": persos,
            "trajets": {
                "lances": self.trajets_lances, "arrives": self.trajets_arrives, "arretes": self.trajets_arretes,
                "changements_de_carte": len(self.changements),
                "duree_changement_moy_s": _r(statistics.fmean(self.changements)) if self.changements else None,
                "duree_changement_max_s": _r(max(self.changements)) if self.changements else None,
                "cartes": dict(self.cartes.most_common()), "sorties_bloquees": self.sorties_bloquees,
                "noms_illisibles": self.noms_illisibles, "captures_noms_illisibles": self.captures_illisibles,
                "positions_demandees": len(self.positions_ok) + self.positions_ratees,
                "positions_lues": self.positions_ok, "positions_ratees": self.positions_ratees,
                "captures_positions_ratees": self.captures_positions,
                "suiveurs_perdus": dict(self.suiveurs_perdus), "regroupes": dict(self.regroupes),
            },
            "anomalies": [_groupe_json(g) for g in _tri(self.anomalies)],
            "avertissements": [_groupe_json(g) for g in _tri(self.avertissements)],
        }


def _r(valeur: float | None) -> float | None:
    return None if valeur is None else round(valeur, 1)


def _tri(groupes: dict[str, Groupe]) -> list[Groupe]:
    rang = {"EXCEPTION": 0, "CRITICAL": 0, "ERROR": 1, "WARNING": 2}
    return sorted(groupes.values(), key=lambda g: (rang.get(g.niveau, 3), -g.n, g.libelle))


def _groupe_json(g: Groupe) -> dict:
    return {"message": g.libelle, "niveau": g.niveau, "n": g.n, "personnages": dict(g.persos.most_common()),
            "premiere": g.premiere, "exemple": g.exemple, "captures": g.captures}


# Markdown

def _cellule(texte: object) -> str:
    return str(texte).replace("|", "/").replace("\n", " ")


def _tableau(entetes: list[str], lignes: list[list[object]]) -> list[str]:
    sortie = ["| " + " | ".join(entetes) + " |", "|" + "---|" * len(entetes)]
    sortie += ["| " + " | ".join(_cellule(c) for c in ligne) + " |" for ligne in lignes]
    return sortie


def _persos_texte(persos: dict[str, int], equipe: list[str]) -> str:
    if not persos:
        return "–"
    if len(persos) >= 4:
        return f"les {len(persos)}" if len(persos) == len(equipe) else f"{len(persos)} personnages"
    return ", ".join(f"{n} ×{k}" if k > 1 else n for n, k in persos.items())


def markdown(r: dict) -> str:
    L: list[str] = []
    t = r["temps"]
    combats = r["combats"]
    total = r["duree_s"]
    debut = (r["debut"] or "")[11:19]
    fin = (r["fin"] or "")[11:19]
    L.append(f"# Résumé de la session {r['session']}")
    L.append("")
    L.append(f"- **Durée** : {duree_texte(total)} ({debut} → {fin}) ; {r['lignes']} lignes de journal "
             f"({r['niveaux'].get('WARNING', 0)} WARNING, {r['niveaux'].get('ERROR', 0)} ERROR)")
    if r["equipe"]:
        L.append(f"- **Équipe** : {len(r['equipe'])} personnages, principal {r['principal']}"
                 + (f" ; limite : {r['limite']}" if r["limite"] else ""))
    if r["mode"]:
        L.append(f"- **Mode** : {r['mode'][:150]}")
    L.append(f"- **Fin** : {r['raison_fin'] or 'pas de message de fin (journal coupé ?) ; dernière ligne : ' + r['derniere_ligne']}")
    # Combats
    L.append("")
    L.append("## Combats")
    L.append("")
    termines = [c for c in combats if not c["interrompu"]]
    jsonl = r["combats_jsonl"]
    L.append(f"- **{len(combats)} combat(s)**, dont {len(termines)} terminé(s)"
             + (f" ; {len(combats) - len(termines)} interrompu(s) par la fin du journal" if len(termines) < len(combats)
                else "")
             + (f" ; combats.jsonl : {jsonl}" if jsonl is not None and jsonl != len(termines) else "")
             + ". Gagnés / perdus : non écrits au journal (le bot n'écrit pas l'issue).")
    if combats:
        L.append(f"- Placement (début → 1er tour) : {stats_texte([c['duree_placement_s'] for c in combats if c['duree_placement_s'] is not None])}")
        L.append(f"- Combat (1er tour → fin) : {stats_texte([c['duree_combat_s'] for c in combats if c['duree_combat_s'] is not None])}")
        montres = combats
        if len(combats) > MAX_COMBATS:
            score = lambda c: (3 * len(c["pas_rejoints"]) + 2 * c["refus"] + c["carte_a_relief"] + c["cercle_cache"]
                               + c["pas_bouge"] + int(c["interrompu"]))
            pires = sorted(combats, key=lambda c: (-score(c), -(c["duree_combat_s"] or 0)))[:MAX_COMBATS]
            montres = sorted(pires, key=lambda c: c["numero"])
            L.append(f"- Tableau : les {MAX_COMBATS} combats les plus remarquables (anomalies, puis les plus longs) ; "
                     f"tous dans resume.json")
        L.append("")
        lignes = []
        for c in montres:
            notes = []
            if c["pas_rejoints"]:
                notes.append(f"{len(c['pas_rejoints'])} pas rejoint(s)")
            if c["carte_a_relief"]:
                notes.append(f"relief ×{c['carte_a_relief']}")
            if c["cercle_cache"]:
                notes.append(f"cercle caché ×{c['cercle_cache']}")
            if c["pas_bouge"]:
                notes.append(f"pas bougé ×{c['pas_bouge']}")
            if c["interrompu"]:
                notes.append("interrompu")
            lignes.append([c["numero"], c["placement"], duree_texte(c["duree_placement_s"]),
                           duree_texte(c["rejoindre_max_s"]), duree_texte(c["duree_combat_s"]), c["tours"], c["refus"],
                           ", ".join(notes) or "–"])
        L += _tableau(["#", "début", "placement", "dernier entré", "combat", "tours", "refus", "anomalies"], lignes)
    # Temps
    L.append("")
    L.append("## Temps")
    L.append("")
    pc = lambda s: f"{fr(100 * s / total, 0)} %" if total else "–"
    L.append(f"- En combat : {duree_texte(t['en_combat_s'])} ({pc(t['en_combat_s'])}) ; placement : "
             f"{duree_texte(t['placement_s'])} ({pc(t['placement_s'])}) ; hors combat : {duree_texte(t['hors_combat_s'])} "
             f"({pc(t['hors_combat_s'])})")
    L.append(f"- Avant le 1er combat : {duree_texte(t['avant_premier_combat_s'])} ; après le dernier : "
             f"{duree_texte(t['apres_dernier_combat_s'])} ; entre deux combats : {stats_texte(t['entre_combats_s'])}")
    L.append(f"- Hors combat, dont repos (assis) {duree_texte(t['repos_s'])}, changements de carte "
             f"{duree_texte(t['changements_de_carte_s'])}, attaques ratées {duree_texte(t['attaques_ratees_s'])} "
             f"({t['attaques_ratees']} « Pas de menu Attaquer »), recherche de groupe et reste "
             f"{duree_texte(t['recherche_et_autres_s'])}")
    if t["aucun_groupe"] or t["plus_de_groupe"]:
        L.append(f"- « Aucun groupe attaquable » ×{t['aucun_groupe']} ; carte vide quittée (« Plus de groupe ici ») "
                 f"×{t['plus_de_groupe']}")
    # Personnages
    persos = r["personnages"]
    if any(p["tours"] or p["pas_rejoint"] or p["epee_introuvable"] for p in persos.values()):
        L.append("")
        L.append("## Par personnage")
        L.append("")
        lignes = []
        for nom, p in persos.items():
            sorts = sum(p["sorts"].values())
            principaux = ", ".join(f"{s} ×{k}" for s, k in list(p["sorts"].items())[:2])
            depl = f"{p['deplacements']}"
            rates = p["pas_bouge"] + p["deplacements_sans_bouger"]
            if rates:
                depl += f" ({p['deplacements_sans_bouger']} sans bouger, {p['pas_bouge']} « pas bougé »)"
            if p["bloque"]:
                depl += " ; bloqué : " + ", ".join(f"{k} ×{v}" for k, v in p["bloque"].items())
            refus = f"{p['refus']}" + (f" ({duree_texte(p['temps_refus'])})" if p["refus"] else "")
            if p["clics_sans_effet"] > p["refus"]:
                refus += f" ; sans effet {p['clics_sans_effet']}"
            entree = []
            if p["pas_rejoint"]:
                entree.append(f"pas rejoint ×{p['pas_rejoint']}")
            if p["epee_introuvable"]:
                entree.append(f"épée introuvable ×{p['epee_introuvable']}")
            if p["attente_rejoindre_moy"] is not None:
                entree.append(f"entré en {duree_texte(p['attente_rejoindre_moy'])}")
            moy = p["duree_tours"] / p["tours"] if p["tours"] else None
            lignes.append([nom, p["tours"], f"{duree_texte(moy)} / {duree_texte(p['tour_max'] or None)}",
                           f"{sorts}" + (f" ({principaux})" if principaux else ""), refus, depl,
                           f"{p['combats']} ({p['combats_sans_tour']})" if p["combats"] else "–",
                           p["carte_a_relief"] or "–", p["cercle_cache"] or "–", p["faux_tours"] or "–",
                           ", ".join(entree) or "–"])
        L += _tableau(["perso", "tours", "tour moy. / max", "sorts", "refus (temps)", "déplacements",
                       "combats (sans tour)", "relief",
                       "cercle caché", "faux tour", "entrée en combat"], lignes)
    # Trajets
    tr = r["trajets"]
    L.append("")
    L.append("## Trajets et cartes")
    L.append("")
    if tr["lances"] or tr["arretes"]:
        L.append(f"- Trajets : {tr['lances']} lancé(s), {tr['arrives']} arrivé(s), {len(tr['arretes'])} arrêté(s)")
        for arret in tr["arretes"][:5]:
            L.append(f"  - arrêté {arret}")
    cartes = tr["cartes"]
    if tr["changements_de_carte"] or cartes:
        top = ", ".join(f"{k} ×{v}" for k, v in list(cartes.items())[:MAX_CARTES])
        L.append(f"- Changements de carte du principal : {tr['changements_de_carte']} (moy. "
                 f"{duree_texte(tr['duree_changement_moy_s'])}, max {duree_texte(tr['duree_changement_max_s'])}) ; "
                 f"{len(cartes)} carte(s) : {top}")
    else:
        L.append("- Aucun changement de carte du principal")
    L.append(f"- Sorties bloquées : {len(tr['sorties_bloquees'])}"
             + ("".join(f"\n  - {s}" for s in tr["sorties_bloquees"][:6])))
    L.append(f"- Noms de carte illisibles (principal) : {tr['noms_illisibles']}"
             + (f" (ex. {', '.join(tr['captures_noms_illisibles'])})" if tr["captures_noms_illisibles"] else ""))
    L.append(f"- Positions demandées par « /p %pos% » : {tr['positions_demandees']} ; lues : "
             f"{', '.join(tr['positions_lues']) or 'aucune'} ; ratées : {tr['positions_ratees']}"
             + (f" (ex. {', '.join(tr['captures_positions_ratees'])})" if tr["captures_positions_ratees"] else ""))
    if tr["suiveurs_perdus"] or tr["regroupes"]:
        L.append("- Suiveurs perdus : " + (", ".join(f"{k} ×{v}" for k, v in tr["suiveurs_perdus"].items()) or "0")
                 + " ; regroupés : " + (", ".join(f"{k} ×{v}" for k, v in tr["regroupes"].items()) or "0"))
    # Anomalies et avertissements
    for titre, cle, note in (
            ("Anomalies notées en INFO", "anomalies", "motifs « problème » de bilan_sessions et captures .png"),
            ("WARNING / ERROR dédoublonnés", "avertissements", "nombres, chemins et noms retirés")):
        groupes = r[cle]
        L.append("")
        L.append(f"## {titre}")
        L.append("")
        if not groupes:
            L.append("Aucun.")
            continue
        L.append(f"{len(groupes)} message(s) distinct(s), {sum(g['n'] for g in groupes)} ligne(s) ({note}) :")
        L.append("")
        lignes = []
        for g in groupes[:MAX_GROUPES]:
            exemple = g["exemple"] if cle == "avertissements" else g["message"]
            lignes.append([g["n"], g["niveau"] if cle == "avertissements" else g["premiere"],
                           exemple[:150], _persos_texte(g["personnages"], r["equipe"]),
                           ", ".join(g["captures"]) or "–"])
        L += _tableau(["n", "niveau" if cle == "avertissements" else "1re", "message (exemple)", "personnages",
                       "captures"], lignes)
        if len(groupes) > MAX_GROUPES:
            L.append(f"… et {len(groupes) - MAX_GROUPES} autre(s) dans resume.json")
    return "\n".join(L) + "\n"


# Lecture d'un dossier

def resumer_lignes(lignes: Iterable[str], nom: str = "journal", jour: date | None = None,
                   combats_jsonl: int | None = None) -> dict:
    resumeur = Resumeur(nom, jour)
    for ligne in lignes:
        resumeur.lire(ligne)
    return resumeur.resume(combats_jsonl)


def resumer_dossier(dossier: Path) -> dict | None:
    log = dossier / "bot.log"
    if not log.is_file():
        return None
    m = SESSION_DIR.search(dossier.name)
    jour = datetime.strptime(m.group(1), "%Y%m%d").date() if m else None
    jsonl = dossier / "combats.jsonl"
    n_jsonl = None
    if jsonl.is_file():
        with jsonl.open(encoding="utf-8", errors="replace") as f:
            n_jsonl = sum(1 for ligne in f if ligne.strip())
    with log.open(encoding="utf-8", errors="replace") as f:
        return resumer_lignes(f, dossier.name, jour, n_jsonl)


def derniere_session(logs: Path = LOGS_DIR) -> list[Path]:
    """Le dernier dossier logs/<date>_<heure> qui a un bot.log (les dossiers d'outils ont un préfixe)."""
    if not logs.is_dir():
        return []
    sessions = sorted(d for d in logs.iterdir() if re.fullmatch(r"\d{8}_\d{6}", d.name) and (d / "bot.log").is_file())
    return sessions[-1:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dossiers", nargs="*", type=Path,
                        help="dossiers de session (logs/<date>_<heure>) ; par défaut : la dernière session")
    parser.add_argument("--sortie", type=Path,
                        help="dossier où écrire <session>.md et <session>.json (par défaut : resume.md et resume.json "
                             "dans le dossier de la session)")
    parser.add_argument("--sans-ecrire", action="store_true", help="affiche seulement, n'écrit aucun fichier")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    dossiers = args.dossiers or derniere_session()
    if not dossiers:
        print(f"Aucune session dans {LOGS_DIR}", file=sys.stderr)
        return 1
    code = 0
    for dossier in dossiers:
        resume = resumer_dossier(dossier)
        if resume is None:
            print(f"{dossier} : pas de bot.log, ignoré", file=sys.stderr)
            code = 1
            continue
        texte = markdown(resume)
        print(texte)
        if args.sans_ecrire:
            continue
        if args.sortie:
            args.sortie.mkdir(parents=True, exist_ok=True)
            md, js = args.sortie / f"{dossier.name}.md", args.sortie / f"{dossier.name}.json"
        else:
            md, js = dossier / "resume.md", dossier / "resume.json"
        md.write_text(texte, encoding="utf-8")
        js.write_text(json.dumps(resume, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Écrit : {md} ({texte.count(chr(10))} lignes) et {js}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
