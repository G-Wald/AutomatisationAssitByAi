"""Bilan chiffré des sessions passées, lu dans les journaux : la base de départ du plan de tests.

Lit, en flux (ligne par ligne, rien n'est chargé en entier) :
- logs/<date>_<heure>/bot.log : un dossier par lancement du bot (trajets, suiveurs, combats, fenêtres, connexion) ;
  combats.jsonl à côté, s'il existe (bilan de chaque combat, bot/debug/report.py) ;
- logs/<outil>_<date>_<heure>/rapport.txt : rapports des outils (banquier, coffre_transfert, echange*, entrer_porte,
  arriere_plan*) ;
- data/carte.json : état de la mémoire des cartes (lecture seule).

Les messages reconnus sont ceux qu'écrit le code (bot/brain/travel.py, bot/team/loop.py, bot/brain/combat.py,
bot/team/turns.py, bot/brain/popups.py, bot/connection/, bot/debug/report.py), anciens formats compris (avant le
11/10 21:50 : « Toujours sur la même carte après 20 s (essai 1/3) », « Soleil du droite retrouvé à l'écran en ... »).
Ce qui n'est pas au journal est listé à la fin (« non mesurable aujourd'hui »), avec le message qu'il faudrait ajouter.

Le personnage entre crochets d'une ligne est celui dont la fenêtre servait à ce moment-là (bot/team/member.py,
MemberTag) : les lignes de trajet du principal portent son nom, celles du trajet de regroupement d'un suiveur le nom
du suiveur.

Depuis la racine du projet :
    .venv\\Scripts\\python -m tools.bilan_sessions                        tous les dossiers de logs/
    .venv\\Scripts\\python -m tools.bilan_sessions logs\\20261011_215114   un ou plusieurs dossiers
    .venv\\Scripts\\python -m tools.bilan_sessions --json bilan.json       et les chiffres bruts en JSON
    .venv\\Scripts\\python -m tools.bilan_sessions --resume logs\\20261016_193753   résumé court à analyser (resume_session)

Ne touche ni au jeu ni aux fichiers lus.
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

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
CARTE = ROOT / "data" / "carte.json"

SESSION_DIR = re.compile(r"(\d{8})_(\d{6})")
TOOL_DIR = re.compile(r"(.+?)_(\d{8})_(\d{6})")

# Ligne du journal : « 21:04:41.053 INFO    [Gela-[A13]] message » (bot/debug/recorder.py, bot/team/member.py).
LINE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,3}))? (DEBUG|INFO|WARNING|ERROR|CRITICAL) +(.*)$")
# Nom du personnage, un niveau de crochets permis dedans (« [Gela-[A13]] »).
TAG = re.compile(r"^\[((?:[^\[\]]|\[[^\[\]]*\])+)\] (.*)$")


# Périodes : d'après docs/rapports/plan_tests_combat.md et plan_banque.md (heure de début de chaque série de tests,
# avec les corrections faites juste avant). Une session est rangée dans la dernière période commencée à son heure de
# lancement (nom du dossier). Les journaux de la plupart de ces périodes ont été effacés (rotation de logs/).
@dataclass(frozen=True)
class Periode:
    code: str
    debut: datetime
    titre: str


PERIODES = [
    Periode("P0", datetime(2026, 9, 1), "Avant le 11/10 15:40 : premiers tests (dont 11/10 15:05, lecture des sorts)"),
    Periode("P1", datetime(2026, 10, 11, 15, 40),
            "11/10 15:43, combats session 1 : sorts défilés par le rail, souris écartée après chaque lancer"),
    Periode("P2", datetime(2026, 10, 11, 16, 5),
            "11/10 16:09, combats session 2 : refus noté au 1er lancer sans effet, portrait à 1 px près, "
            "obstacles entre portée min. et max."),
    Periode("P3", datetime(2026, 10, 11, 16, 45),
            "11/10 16:49, combats session 3 : tour = timer OU portrait, 2e rangée au pavé numérique, "
            "épée de ralliement"),
    Periode("P4", datetime(2026, 10, 11, 17, 30),
            "11/10 17:32, combats session 4 (reconnexion auto) et 17:47 1er trajet vers la banque"),
    Periode("P5", datetime(2026, 10, 11, 18, 20),
            "11/10 18:23, banque et échanges (outils) ; 19:0x Menu principal refermé par Échap, « quitter » -> Non, "
            "équipe groupée en trajet"),
    Periode("P6", datetime(2026, 10, 11, 20, 10), "11/10 20:13, trajets dans la cité : modèle du chiffre 9"),
    Periode("P7", datetime(2026, 10, 11, 20, 45),
            "11/10 20:46, passages d'Astrub : touche A sous E, soleil jugé inatteignable après 3 essais de 20 s"),
    Periode("P8", datetime(2026, 10, 11, 21, 45),
            "11/10 21:50, détection rapide des soleils inatteignables (personnage immobile 2 s), tous les soleils "
            "d'un côté, sortie bloquée selon le côté d'arrivée"),
    Periode("P9", datetime(2026, 10, 11, 22, 8),
            "11/10 ~22:08 (heure supposée, entre deux lancements), reclic d'un suiveur seulement s'il est immobile"),
]


def periode_de(moment: datetime | None) -> Periode | None:
    if moment is None:
        return None
    found = None
    for periode in PERIODES:
        if moment >= periode.debut:
            found = periode
    return found


# Messages reconnus. Une ligne compte pour le premier motif qui la reconnaît (l'ordre compte : « X a rejoint le
# principal » avant « X rejoint le principal », « lancé sur moi » avant « lancé sur »). `probleme` : compté dans le
# classement des problèmes les plus fréquents.
@dataclass(frozen=True)
class Motif:
    cle: str
    regex: re.Pattern
    domaine: str
    libelle: str
    probleme: bool = False


def _m(cle: str, motif: str, domaine: str, libelle: str, probleme: bool = False) -> Motif:
    return Motif(cle, re.compile(motif), domaine, libelle, probleme)


MOTIFS: list[Motif] = [
    # Session
    _m("equipe", r"^Équipe : (.*) ; principal : (.+)$", "session", "équipe"),
    _m("mode", r"^Mode : (.*)$", "session", "mode"),
    _m("limite", r"^Session limitée : (.*)$", "session", "limite de la session"),
    _m("fin_session", r"^Fin de la session : (.*)$", "session", "fin de la session"),
    _m("bot_arrete", r"^(?:Bot arrêté|Arrêté) \((.*)\)$", "session", "bot arrêté"),
    _m("arret_demande", r"^Arrêt demandé : (.*)$", "session", "arrêt demandé (F12)"),
    _m("erreur_inattendue", r"^Erreur inattendue", "erreurs", "« Erreur inattendue » (exception)", True),
    _m("sessions_supprimees", r"^Anciennes sessions supprimées : (.*)$", "session", "sessions effacées"),
    # Trajets
    _m("trajet_arrete", r"^Trajet vers (-?\d+),(-?\d+) arrêté : (.*)$", "trajet", "trajet arrêté", True),
    _m("trajet_lance", r"^Trajet vers (-?\d+),(-?\d+) \(.*\), sans combat ; départ : (?:(-?\d+),(-?\d+))?", "trajet",
       "trajet lancé"),
    _m("itineraire", r"^Itinéraire de (-?\d+),(-?\d+) à (-?\d+),(-?\d+) : (\d+) carte\(s\)", "trajet", "itinéraire"),
    _m("nouvel_itineraire", r"^Nouvel itinéraire de .* : (\d+) carte\(s\)", "trajet", "« Nouvel itinéraire »", True),
    _m("carte_inattendue", r"^Carte (-?\d+),(-?\d+) au lieu de (-?\d+),(-?\d+).*: nouvel itinéraire", "trajet",
       "« Carte X au lieu de Y : nouvel itinéraire »", True),
    _m("etape", r"^Trajet : côté (\w+), vers (-?\d+),(-?\d+)", "trajet", "étape de trajet"),
    _m("clic_soleil", r"^Vers la carte du (\w+) : clic sur le soleil en \((\d+), (\d+)\)", "trajet",
       "clic sur un soleil"),
    _m("nouvelle_carte", r"^Nouvelle carte : (.*)$", "trajet", "nouvelle carte"),
    _m("arrive", r"^Arrivé en (-?\d+),(-?\d+)", "trajet", "arrivé"),
    _m("sortie_inutilisable", r"^Sortie du (\w+) inutilisable, je la note bloquée", "trajet",
       "« Sortie ... inutilisable, je la note bloquée »", True),
    _m("pas_de_sortie", r"^Pas de sortie vers le (\w+) sur cette carte", "trajet",
       "« Pas de sortie vers le ... même avec la touche A »", True),
    _m("inatteignable_depuis", r"^Sortie du (\w+) notée inatteignable pour qui arrive par le (\w+)", "trajet",
       "sortie notée inatteignable depuis un côté"),
    _m("bloquee_ce_trajet", r"^Sortie du (\w+) bloquée pour ce trajet seulement(.*)$", "trajet",
       "sortie bloquée pour ce trajet seulement (rien d'appris)", True),
    _m("debloquee", r"^Sortie du (\w+) pas bloquée : ", "trajet", "sortie débloquée (des suiveurs l'ont prise)"),
    _m("toujours_meme_carte", r"^Toujours sur la même carte après (\d+) s", "trajet",
       "« Toujours sur la même carte après 20 s »", True),
    _m("ne_bouge_plus", r"^Le personnage ne bouge plus et n'a pas changé de carte", "trajet",
       "« Le personnage ne bouge plus » (soleil inatteignable vu vite)", True),
    _m("a_marche", r"^Le personnage a marché vers le soleil en .* puis s'est arrêté", "trajet",
       "« Le personnage a marché vers le soleil ... puis s'est arrêté »", True),
    _m("autre_soleil", r"^Autre soleil du (\w+)", "trajet", "« Autre soleil »"),
    _m("soleil_retrouve", r"^Soleil(?:\(s\))? du (\w+) retrouvé", "trajet", "soleil retrouvé à l'écran"),
    _m("plusieurs_soleils", r"^Plusieurs soleils ", "trajet", "plusieurs soleils d'un côté (rempart)"),
    _m("sur_le_soleil", r"^Quelqu'un se tient sur le soleil", "trajet", "quelqu'un sur le soleil (menu ouvert)", True),
    _m("reclic_principal", r"^Toujours sur la carte de départ : je reclique sur le soleil", "trajet",
       "reclic du principal (clic qui n'a pas pris)", True),
    _m("immobile", r"^Immobile ([\d.]+) s après le clic", "trajet", "immobile après le clic"),
    _m("pas_arrive", r"^Pas arrivé en (\d+) s", "trajet", "pas arrivé (attente écoulée)"),
    _m("touche_a", r"^Sorties avec la touche A : (.*)$", "trajet", "recherche des sorties (touche A)"),
    _m("combat_trajet", r"^Combat pendant le trajet", "trajet", "combat pendant le trajet"),
    _m("deplace_ailleurs", r"^Personnage déplacé ailleurs", "trajet", "personnage déplacé ailleurs", True),
    _m("principal_bloque", r"^Le principal ne passe pas des soleils que ses suiveurs passent", "suiveurs",
       "principal bloqué par le jeu (arrêt)", True),
    # Lecture du nom de la carte
    _m("nom_illisible", r"^Nom de la carte (?:de départ )?illisible", "lecture",
       "« Nom de la carte illisible » (cartes distinctes)", True),
    _m("coord_differentes", r"^Coordonnées lues (-?\d+),(-?\d+) : j'attendais", "lecture",
       "« Coordonnées lues X : j'attendais Y »", True),
    _m("coord_contradictoires", r"^Coordonnées lues « .* » : pas la carte", "lecture",
       "« Coordonnées lues ... pas la carte attendue »", True),
    _m("coord_partielles", r"^Coordonnées lues « ", "lecture", "coordonnées lues en partie"),
    _m("chiffre_appris", r"^Chiffre\(s\) .* appris sur la carte", "lecture", "chiffre appris"),
    _m("saut_du_jeu", r"^Saut du jeu confirmé", "lecture", "saut du jeu confirmé"),
    _m("liaison_douteuse", r"^Liaison .* pas encore enregistrée", "lecture", "liaison douteuse (cartes pas voisines)",
       True),
    _m("memoire_cartes", r"^Mémoire des cartes (?:non enregistrée|illisible)", "lecture", "mémoire des cartes en échec",
       True),
    # Suiveurs
    _m("equipe_part", r"^Toute l'équipe vers le (\w+) : (.*) cliquent aussi", "suiveurs", "équipe partie ensemble"),
    _m("suivi", r"^(.+?) a suivi le principal \(", "suiveurs", "suiveur arrivé (carte lue)"),
    _m("suivi_illisible", r"^(.+?) a changé de carte \(nom illisible\)", "suiveurs",
       "suiveur arrivé sans vérification (nom illisible)", True),
    _m("pas_suivi", r"^(.+?) n'a pas suivi le principal", "suiveurs", "« n'a pas suivi le principal »", True),
    _m("arrive_ailleurs", r"^(.+?) est arrivé sur .*, pas sur la carte du principal", "suiveurs",
       "suiveur arrivé ailleurs", True),
    _m("decor_different", r"^(.+?) n'est pas sur la carte du principal \(décor différent\)", "suiveurs",
       "suiveur pas sur la carte du principal (décor)", True),
    _m("pas_sur_carte_quittee", r"^(.+?) n'est pas sur la carte que quitte le principal", "suiveurs",
       "suiveur pas sur la carte que quitte le principal", True),
    _m("absent_depart", r"^(.+?) n'est pas sur la carte du principal au départ", "suiveurs",
       "suiveur absent au départ", True),
    _m("position_inconnue", r"^(.+?) n'est pas sur la carte du principal et je ne sais pas où il est", "suiveurs",
       "suiveur perdu, position inconnue (arrêt)", True),
    _m("passe_sans_principal", r"^(.+?) est passé sans le principal", "suiveurs", "« passé sans le principal »", True),
    _m("regroupe", r"^(.+?) a rejoint le principal en ", "suiveurs", "suiveur regroupé"),
    _m("regroupement", r"^(.+?) rejoint le principal en (-?\d+),(-?\d+)", "suiveurs", "regroupement lancé"),
    _m("regroupement_decor", r"^(.+?) se croit en .* mais ne voit pas le même décor", "suiveurs",
       "regroupement : pas le même décor", True),
    _m("regroupement_impossible", r"^(.+?) ne peut pas rejoindre le principal", "suiveurs",
       "regroupement impossible (arrêt)", True),
    _m("reclic_suiveur", r"^Je suis le principal vers le (\w+) : clic sur le soleil", "suiveurs",
       "reclic d'un suiveur", True),
    # Combats
    _m("combat_placement", r"^Placement : combat de (.+)$", "combat", "début de combat (placement)"),
    _m("combat_deja", r"^Combat déjà commencé : ", "combat", "combat déjà commencé"),
    _m("attaque_groupe", r"^J'attaque le groupe en ", "combat", "attaque d'un groupe"),
    _m("combat_pas_eu_lieu", r"^Le combat n'a pas commencé|^Ni placement ni combat", "combat",
       "combat qui n'a pas eu lieu", True),
    _m("combat_ne_commence_pas", r"^Le combat ne commence pas", "combat", "« Le combat ne commence pas »", True),
    _m("combat_fin", r"^Combat terminé$", "combat", "combat terminé"),
    _m("etat", r"^État : (.*)$", "combat", "état"),
    _m("mon_tour", r"^Mon tour$", "combat", "« Mon tour »"),
    _m("suite_tour", r"^Suite de mon tour \((\d+) PA, ([\d.,]+) s après\)", "combat",
       "« Suite de mon tour » (faux nouveau tour rattrapé)", True),
    _m("tour_bilan", r"^Tour : ([\d,.]+) s ; (.*)$", "combat", "ligne « Tour : »"),
    _m("bilan_combat", r"^Bilan du combat (\d+), (.+?) : (.*)$", "combat", "bilan de combat"),
    _m("fin_tour", r"^Fin de tour", "combat", "fin de tour"),
    _m("sort_rafale", r"^(.+?) lancé sur .* \(rafale, ", "combat", "sort lancé (rafale)"),
    _m("sort_sur_moi", r"^(.+?) lancé sur moi \(", "combat", "sort lancé sur soi"),
    _m("sort_lance", r"^(.+?) lancé sur (.+)$", "combat", "sort lancé"),
    _m("sort_refuse", r"^(.+?) refusé sur .* \(PA inchangés", "combat", "sort refusé (PA inchangés)", True),
    _m("sort_trop_tot", r"^(.+?) pas parti ([\d,]+) s après le sort précédent", "combat",
       "sort enchaîné trop tôt (jeu pas prêt, relancé d'ici)", True),
    _m("pas_ligne_de_vue", r"^Pas de ligne de vue sur ", "combat", "« Pas de ligne de vue »", True),
    _m("hors_portee", r"^(.+?) à (\d+) cases, hors de portée", "combat", "« hors de portée »", True),
    _m("non_selectionnable", r"^(.+?) non sélectionnable \(touche", "combat", "sort non sélectionnable", True),
    _m("pas_selectionne", r"^(.+?) pas sélectionné juste après un lancer", "combat",
       "sort pas resélectionné (animation)"),
    _m("sort_pas_parti", r"^Le sort ne semble pas parti|^(.+?) ne semble pas parti par |^Rafale arrêtée : .* ne semble "
       r"pas parti", "combat", "sort « pas parti »", True),
    _m("sort_banni", r"n'est jamais parti sur un ennemi", "combat", "sort écarté pour la session", True),
    _m("confirmation", r"^(.+?) : (parti|pas parti) en ([\d.]+) s$", "combat", "délai de confirmation d'un sort"),
    _m("pa_ce_tour", r"^(\d+) PA ce tour \(au lieu de (\d+)\)", "combat", "PA du tour inférieurs au maximum"),
    _m("pa_illisibles", r"^P[AM] illisibles dans la bulle|PA lus dans la bulle, impossible", "combat",
       "PA/PM illisibles", True),
    _m("main_pas_passee", r"^La main ne semble pas être passée|n'a pas passé le tour : je clique sur le bouton",
       "combat", "main pas passée / F1 ignoré", True),
    _m("fin_tour_introuvable", r"^Bouton de fin de tour introuvable", "combat", "bouton de fin de tour introuvable",
       True),
    _m("moi_ambigu", r"cercles de ma couleur et rien ne dit lequel est moi", "combat", "« rien ne dit lequel est moi »",
       True),
    _m("pas_bouge", r"^Mon personnage n'a pas bougé", "combat", "déplacement sans effet", True),
    _m("bloque_deplacement", r"^Je ne peux pas bouger", "combat", "plus de déplacement (tacle, bloqué)", True),
    _m("plan_change", r"^Plan changé : ", "combat", "« Plan changé »"),
    _m("carte_relief", r"^Carte à relief", "combat", "carte à relief (ligne de vue imprévisible)", True),
    _m("pave_num_echec", r"^Pavé numérique : .* pas sélectionné|le pavé numérique ne sélectionne pas", "combat",
       "pavé numérique sans effet", True),
    _m("epee_introuvable", r"^Je ne vois pas l'épée du combat|^Pas de menu « Rejoindre »", "combat",
       "épée du combat introuvable", True),
    _m("pas_rejoint_combat", r"n'a pas rejoint le combat", "combat", "suiveur pas entré en combat", True),
    _m("rejoint_combat", r"^(.+?) a rejoint le combat$", "combat", "suiveur entré en combat"),
    _m("notification_attente", r"^Notification : tour de .* \? Pas encore de timer", "combat",
       "tour signalé sans timer (attente)", True),
    _m("notification_tour", r"^Notification : tour de ", "combat", "tour signalé par notification"),
    _m("equipe_contredite", r"^Mon équipe est .*, pas ", "combat", "couleur d'équipe contredite", True),
    _m("mode_tactique", r"^Le mode tactique ne s'est pas activé|^Carte hors mode tactique et bouton|^Le mode créature "
       r"ne s'est pas activé", "combat", "mode tactique / créature pas activé", True),
    _m("groupe_introuvable", r"^Je ne trouve pas le groupe|^Pas de menu « Attaquer »", "combat",
       "groupe de monstres pas attaqué", True),
    _m("sort_inconnu", r"^Sort n°(\d+) inconnu", "combat", "sort de la barre inconnu (Capture d'âmes)", True),
    _m("sort_sans_classe", r"^Sort n°(\d+) \(.*\) : aucun sort de la classe ne correspond", "combat",
       "sort lu sans correspondance (suivi de « Sort n° inconnu »)"),
    _m("sorts_incomplets", r"liste des sorts incomplète|^La liste des sorts ne défile pas", "combat",
       "liste des sorts lue en partie", True),
    # Fenêtres, sécurité, connexion
    _m("quitter", r"^Fenêtre « Cette action va quitter ta partie en cours »", "fenetres",
       "fenêtre « quitter ta partie » (réponse Non)", True),
    _m("connexion_ok", r"^Fenêtre « Connexion »(.*) : « Ok »", "connexion",
       "fenêtre « Connexion » (déconnecté par le jeu)", True),
    _m("fenetre_fermee", r"^Fenêtre « (.+?) » : je la ferme", "fenetres", "fenêtre fermée"),
    _m("fenetre_inconnue", r"^Fenêtre inconnue sur la carte", "fenetres", "fenêtre inconnue", True),
    _m("fenetre_pas_fermee", r"^Je n'arrive pas à fermer cette fenêtre", "fenetres", "fenêtre qui ne se ferme pas",
       True),
    _m("menu_principal", r"Échap a ouvert le Menu principal", "fenetres", "Menu principal ouvert par Échap", True),
    _m("deconnexion", r"^(.+?) est déconnecté : je le reconnecte", "connexion", "personnage déconnecté", True),
    _m("principal_deconnecte", r"^Le principal a été déconnecté", "connexion", "principal déconnecté", True),
    _m("en_jeu", r"^(.+?) est en jeu$", "connexion", "personnage (re)mis en jeu"),
    _m("connexion_lancee", r"^Connexion de (\d+) fenêtre\(s\) du jeu", "connexion", "connexion lancée"),
    _m("connexion_groupe", r"^En jeu : |^Groupe : .* invite ", "connexion", "personnages en jeu, groupe"),
    _m("connexion_echec", r"^Reconnexion de .* impossible|^Connexion impossible|^Aucune nouvelle fenêtre du jeu",
       "connexion", "connexion impossible", True),
    _m("fenetre_jeu_fermee", r"^La fenêtre de .* est fermée", "connexion", "fenêtre du jeu fermée", True),
    _m("pas_dans_groupe", r"^Pas dans le groupe : ", "connexion", "personnage hors du groupe", True),
    _m("touches_tenues", r"encore enfoncé\(s\) avant mon action", "fenetres", "touches restées enfoncées", True),
    _m("clic_annule", r"^(?:Clic|Molette) .*annulée? |annulé en .* : une autre fenêtre", "fenetres",
       "clic ou molette annulé", True),
    _m("proportions", r"proportions différentes des captures de référence", "fenetres",
       "fenêtre aux proportions inattendues", True),
    _m("limite_captures", r"^Limite de \d+ captures atteinte", "session", "limite de captures atteinte"),
    # Pods (banque)
    _m("pods", r"^Pods de (.+?) : (\d+) %( : il faut passer à la banque)?", "banque", "pods"),
]
MOTIF = {m.cle: m for m in MOTIFS}

LOST = ("pas_suivi", "arrive_ailleurs", "decor_different", "pas_sur_carte_quittee", "absent_depart",
        "position_inconnue", "passe_sans_principal")
VERDICTS = ("toujours_meme_carte", "ne_bouge_plus", "a_marche")


@dataclass
class Trajet:
    """Un trajet (mode --aller) : celui du principal, ou le trajet de regroupement d'un suiveur."""

    acteur: str
    principal: bool
    but: tuple[int, int]
    debut: datetime
    fin: datetime | None = None
    issue: str = ""  # arrivé, arrêté, interrompu
    raison: str = ""
    passages: int = 0
    rates: int = 0
    prevu: int | None = None
    recalculs: int = 0
    depart: tuple[int, int] | None = None
    session: str = ""

    @property
    def duree(self) -> float | None:
        return (self.fin - self.debut).total_seconds() if self.fin else None


@dataclass
class Combat:
    debut: datetime | None
    fin: datetime | None = None
    tours: int = 0

    @property
    def duree(self) -> float | None:
        return (self.fin - self.debut).total_seconds() if self.fin and self.debut else None


@dataclass
class Session:
    nom: str
    jour: date | None = None
    lancement: datetime | None = None  # d'après le nom du dossier
    debut: datetime | None = None
    fin: datetime | None = None
    genre: str = "autre"  # bot, connexion, autre
    mode: str = ""
    principal: str | None = None
    limite: str = ""
    fin_raison: str = ""
    n: Counter = field(default_factory=Counter)
    durees: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    exemples: dict[str, str] = field(default_factory=dict)
    trajets: list[Trajet] = field(default_factory=list)
    combats: list[Combat] = field(default_factory=list)
    fenetres: Counter = field(default_factory=Counter)
    sorts_tentes: Counter = field(default_factory=Counter)
    sorts_refuses: Counter = field(default_factory=Counter)
    tours_par_perso: Counter = field(default_factory=Counter)
    arrets: Counter = field(default_factory=Counter)
    autres_avertissements: Counter = field(default_factory=Counter)
    pods: dict[str, int] = field(default_factory=dict)
    supprimees: list[str] = field(default_factory=list)
    sessions: int = 1

    @property
    def duree(self) -> float:
        return (self.fin - self.debut).total_seconds() if self.fin and self.debut else 0.0

    @property
    def periode(self) -> Periode | None:
        return periode_de(self.lancement or self.debut)


def _normalise(message: str) -> str:
    """Message d'avertissement sans ses nombres ni chemins de capture : pour regrouper les lignes semblables."""
    message = re.sub(r"\(capture : [^)]*\)", "(capture)", message)
    message = re.sub(r"[A-Z]:\\[^ )]*", "<chemin>", message)
    return re.sub(r"-?\d+(?:[.,]\d+)?", "N", message)[:160]


class Lecteur:
    """Lit un journal bot.log ligne par ligne (`lire`), puis `terminer` rend la Session."""

    def __init__(self, nom: str, jour: date | None = None, lancement: datetime | None = None) -> None:
        self.s = Session(nom=nom, jour=jour, lancement=lancement)
        self._jour = jour or date(2000, 1, 1)
        self._dernier: datetime | None = None
        self._trajets: dict[str, Trajet] = {}  # trajet en cours de chaque personnage
        self._etapes: dict[str, datetime] = {}  # étape (passage d'une carte) en cours de chaque personnage
        self._clics: dict[str, datetime] = {}  # dernier clic sur un soleil de chaque personnage
        self._apres_carte: datetime | None = None  # arrivée du principal sur une carte (battement jusqu'au départ)
        self._combat: Combat | None = None
        self._perdus: set[str] = set()  # suiveurs perdus pas encore revenus (un épisode = plusieurs messages possibles)

    # Lecture

    def lire(self, brute: str) -> None:
        ligne = brute.rstrip("\r\n")
        m = LINE.match(ligne)
        if m is None:
            if ligne.startswith("Traceback"):
                self.s.n["exceptions"] += 1
                self.s.exemples.setdefault("exceptions", f"{self.s.nom} : {ligne[:220]}")
            return
        h, mi, se, ms, niveau, reste = m.groups()
        t = self._heure(int(h), int(mi), int(se), int((ms or "0").ljust(3, "0")))
        acteur, message = "", reste
        tag = TAG.match(reste)
        if tag:
            acteur, message = tag.group(1), tag.group(2)
        s = self.s
        s.debut = s.debut or t
        s.fin = t
        s.n["lignes"] += 1
        if niveau == "WARNING":
            s.n["avertissements"] += 1
        elif niveau in ("ERROR", "CRITICAL"):
            s.n["erreurs"] += 1
            s.exemples.setdefault("erreurs", f"{s.nom} : {ligne[:220]}")
        for motif in MOTIFS:
            found = motif.regex.search(message)
            if found:
                s.n[motif.cle] += 1
                s.exemples.setdefault(motif.cle, f"{s.nom} : {ligne[:220]}")
                self._evenement(motif.cle, found, t, acteur, message)
                return
        if niveau in ("WARNING", "ERROR", "CRITICAL"):
            s.autres_avertissements[_normalise(message)] += 1
            s.exemples.setdefault("avert:" + _normalise(message), f"{s.nom} : {ligne[:220]}")

    def _heure(self, h: int, mi: int, se: int, ms: int) -> datetime:
        t = datetime.combine(self._jour, time(h, mi, se, ms * 1000))
        if self._dernier is not None and t < self._dernier - timedelta(hours=12):
            self._jour += timedelta(days=1)  # passage de minuit
            t += timedelta(days=1)
        self._dernier = t
        return t

    def _est_principal(self, acteur: str) -> bool:
        return self.s.principal is None or not acteur or acteur == self.s.principal

    def _evenement(self, cle: str, m: re.Match, t: datetime, acteur: str, message: str) -> None:
        s = self.s
        principal = self._est_principal(acteur)
        if cle in LOST:
            nom = m.group(1) or ""
            if nom not in self._perdus:
                self._perdus.add(nom)
                s.n["episodes_perdus"] += 1
        elif cle in ("suivi", "suivi_illisible", "regroupe"):
            self._perdus.discard(m.group(1))
        if cle == "equipe":
            s.principal = m.group(2).strip()
            s.genre = "bot"
        elif cle == "mode":
            s.mode = s.mode or m.group(1)
            s.genre = "bot"
        elif cle == "limite":
            s.limite = m.group(1)
        elif cle in ("fin_session", "bot_arrete", "arret_demande"):
            s.fin_raison = s.fin_raison or m.group(1)
        elif cle == "erreur_inattendue":
            s.fin_raison = s.fin_raison or "erreur inattendue"
        elif cle == "sessions_supprimees":
            s.supprimees += [x.strip() for x in m.group(1).split(",") if x.strip()]
        elif cle in ("connexion_lancee", "connexion_groupe") and s.genre == "autre":
            s.genre = "connexion"  # python -m bot.connection (connexion et groupe, sans trajet ni combat)
        # Trajets
        elif cle == "trajet_lance":
            self._fermer_trajet(acteur, t, "interrompu", "nouveau trajet lancé")
            depart = (int(m.group(3)), int(m.group(4))) if m.group(3) else None
            self._trajets[acteur] = Trajet(acteur, principal, (int(m.group(1)), int(m.group(2))), t, depart=depart,
                                           session=s.nom)
            s.trajets.append(self._trajets[acteur])
            if not principal:
                s.n["trajets_regroupement"] += 1
        elif cle == "itineraire":
            trajet = self._trajets.get(acteur)
            if trajet is not None and trajet.prevu is None:
                trajet.prevu = int(m.group(5))
        elif cle == "nouvel_itineraire":
            trajet = self._trajets.get(acteur)
            if trajet is not None:
                trajet.recalculs += 1
        elif cle == "etape":
            if acteur in self._etapes:
                self._fin_etape(acteur, t, ok=False)  # étape précédente finie sans nouvelle carte
            self._etapes[acteur] = t
            if principal and self._apres_carte is not None:
                s.durees["battement"].append((t - self._apres_carte).total_seconds())
                self._apres_carte = None
        elif cle == "clic_soleil":
            self._clics[acteur] = t
            s.n["clics_soleil_principal" if principal else "clics_soleil_regroupement"] += 1
        elif cle in VERDICTS:
            clic = self._clics.get(acteur)
            if clic is not None:
                s.durees["verdict_" + cle].append((t - clic).total_seconds())
                s.durees["verdicts"].append((t - clic).total_seconds())
        elif cle == "nouvelle_carte":
            trajet = self._trajets.get(acteur)
            if trajet is not None:
                trajet.passages += 1
            if acteur in self._etapes:
                self._fin_etape(acteur, t, ok=True)
            if principal:
                s.n["passages_ok"] += 1
                texte = m.group(1)
                if texte.startswith("carte au nom illisible"):
                    s.n["lecture_illisible"] += 1
                elif "d'après le chemin" in texte:
                    s.n["lecture_partielle"] += 1
                else:
                    s.n["lecture_complete"] += 1
                    if texte.startswith("région inconnue"):
                        s.n["lecture_region_inconnue"] += 1
                self._apres_carte = t
            else:
                s.n["passages_ok_regroupement"] += 1
        elif cle in ("sortie_inutilisable", "pas_de_sortie"):
            if acteur in self._etapes:
                self._fin_etape(acteur, t, ok=False)
        elif cle == "combat_trajet":
            if acteur in self._etapes:
                del self._etapes[acteur]
                s.n["passages_interrompus"] += 1
        elif cle == "arrive":
            if principal and self._apres_carte is not None:
                s.durees["battement"].append((t - self._apres_carte).total_seconds())
                self._apres_carte = None
            self._fermer_trajet(acteur, t, "arrivé", f"arrivé en {m.group(1)},{m.group(2)}")
        elif cle == "trajet_arrete":
            self._fermer_trajet(acteur, t, "arrêté", m.group(3))
        elif cle == "principal_bloque":
            self._fermer_trajet(s.principal or "", t, "arrêté", "principal bloqué par le jeu")
        elif cle in ("regroupement_impossible", "position_inconnue"):
            self._fermer_trajet(s.principal or "", t, "arrêté", "suiveur qui ne peut pas rejoindre")
        # Combats
        elif cle in ("combat_placement", "combat_deja") or (cle == "etat" and m.group(1).split(",")[0] in
                                                          ("placement", "combat")):
            if self._combat is None:
                self._combat = Combat(debut=t)
                s.combats.append(self._combat)
        elif cle == "combat_fin":
            if self._combat is None:
                s.combats.append(Combat(debut=None, fin=t))
            else:
                self._combat.fin = t
                if self._combat.duree is not None:
                    s.durees["combat"].append(self._combat.duree)
            self._combat = None
        elif cle == "mon_tour":
            s.tours_par_perso[acteur or "?"] += 1
            if self._combat is not None:
                self._combat.tours += 1
        elif cle == "tour_bilan":
            self._ligne_tour(m.group(1), m.group(2))
        elif cle == "bilan_combat":
            self._bilan_combat(m.group(3))
        elif cle in ("sort_lance", "sort_rafale", "sort_sur_moi"):
            s.sorts_tentes[m.group(1).strip()] += 1
            s.n["sorts_tentes"] += 1
        elif cle in ("sort_refuse", "hors_portee", "non_selectionnable"):
            s.sorts_refuses[m.group(1).strip()] += 1
        elif cle == "confirmation":
            s.durees["confirmation_" + ("parti" if m.group(2) == "parti" else "pas_parti")].append(float(m.group(3)))
        elif cle == "pa_ce_tour":
            s.durees["pa_manquants_debut"].append(float(int(m.group(2)) - int(m.group(1))))
        # Fenêtres, connexion, pods
        elif cle == "fenetre_fermee":
            s.fenetres[m.group(1)] += 1
        elif cle == "connexion_ok" and "inactivit" in m.group(1):
            s.n["deconnexion_inactivite"] += 1
        elif cle == "pods":
            s.pods[m.group(1)] = int(m.group(2))
            if m.group(3):
                s.n["pods_pleins"] += 1

    def _fin_etape(self, acteur: str, t: datetime, ok: bool) -> None:
        debut = self._etapes.pop(acteur)
        duree = (t - debut).total_seconds()
        principal = self._est_principal(acteur)
        if ok:
            self.s.durees["passage_ok" if principal else "passage_ok_regroupement"].append(duree)
            return
        if principal:
            self.s.n["passages_rates"] += 1
            self.s.durees["passage_rate"].append(duree)
        else:
            self.s.n["passages_rates_regroupement"] += 1
        trajet = self._trajets.get(acteur)
        if trajet is not None:
            trajet.rates += 1

    def _fermer_trajet(self, acteur: str, t: datetime, issue: str, raison: str) -> None:
        trajet = self._trajets.pop(acteur, None)
        if trajet is None:
            return
        trajet.fin, trajet.issue, trajet.raison = t, issue, raison
        if acteur in self._etapes and issue != "arrivé":
            self._etapes.pop(acteur)
            if trajet.principal:
                self.s.n["passages_interrompus"] += 1
        if trajet.principal and issue != "arrivé":
            self.s.arrets[raison] += 1

    def _ligne_tour(self, secondes: str, reste: str) -> None:
        s = self.s
        s.n["tours_bilan"] += 1
        s.durees["tour"].append(float(secondes.replace(",", ".")))
        for cle, motif in (("sorts_partis", r"(\d+) sorts?\b"), ("sorts_rafale", r"dont (\d+) en rafale"),
                           ("refus_tour", r"(\d+) refus"), ("clics_sans_effet", r"(\d+) clics? sans effet"),
                           ("deplacements_sans_bouger", r"(\d+) déplacements? sans bouger")):
            found = re.search(motif, reste)
            if found:
                s.n[cle] += int(found.group(1))
        moves = re.search(r"(\d+) déplacements?(?! sans)", reste)
        if moves:
            s.n["deplacements"] += int(moves.group(1))

    def _bilan_combat(self, texte: str) -> None:
        """« 3 tour(s) (11,2 s) ; 7 sort(s) (Flèche Magique x4, ...) ; 1 refus ; 2 déplacement(s) » (report.py)."""
        s = self.s
        s.n["bilans_combat"] += 1
        for cle, motif in (("bilan_tours", r"(\d+) tour\(s\)"), ("bilan_sorts", r"(\d+) sort\(s\)"),
                           ("bilan_refus", r"(\d+) refus"), ("bilan_clics_sans_effet", r"(\d+) clic\(s\) sans effet")):
            found = re.search(motif, texte)
            if found:
                s.n[cle] += int(found.group(1))
        found = re.search(r"tour\(s\) \(([\d,.]+) s\)", texte)
        if found:
            s.durees["bilan_duree_tours"].append(float(found.group(1).replace(",", ".")))

    def terminer(self) -> Session:
        s = self.s
        fin = s.fin or datetime.combine(self._jour, time())
        for acteur in list(self._trajets):
            self._fermer_trajet(acteur, fin, "interrompu", "fin du journal sans arrivée")
        for acteur in list(self._etapes):
            del self._etapes[acteur]
            s.n["passages_interrompus"] += 1
        if self._combat is not None:
            s.n["combats_inacheves"] += 1
        s.n["combats"] = sum(1 for c in s.combats if c.fin is not None)
        if not s.fin_raison:
            mine = [t for t in s.trajets if t.principal]
            if mine:
                s.fin_raison = f"trajet {mine[-1].issue} ({mine[-1].raison})"
            elif s.genre == "connexion":
                s.fin_raison = "connexion faite" if s.n["en_jeu"] or s.n["connexion_groupe"] else "connexion inachevée"
            else:
                s.fin_raison = "fin non journalisée"
        return s


def analyser_lignes(lignes: Iterable[str], nom: str = "journal", jour: date | None = None,
                    lancement: datetime | None = None) -> Session:
    lecteur = Lecteur(nom, jour, lancement)
    for ligne in lignes:
        lecteur.lire(ligne)
    return lecteur.terminer()


def analyser_dossier(dossier: Path) -> Session | None:
    """Session du bot d'un dossier logs/<date>_<heure>/ (None : pas de bot.log)."""
    journal = dossier / "bot.log"
    if not journal.is_file():
        return None
    found = SESSION_DIR.fullmatch(dossier.name)
    lancement = datetime.strptime(dossier.name, "%Y%m%d_%H%M%S") if found else None
    with journal.open(encoding="utf-8", errors="replace") as f:
        session = analyser_lignes(f, dossier.name, lancement.date() if lancement else None, lancement)
    combats = dossier / "combats.jsonl"
    if combats.is_file() and not session.n["bilans_combat"]:
        _lire_combats_jsonl(combats, session)
    notifications = dossier / "notifications.jsonl"
    if notifications.is_file():
        with notifications.open(encoding="utf-8", errors="replace") as f:
            session.n["notifications"] += sum(1 for line in f if line.strip())
    return session


def _lire_combats_jsonl(path: Path, session: Session) -> None:
    """Bilans des combats (bot/debug/report.py), quand ils ne sont pas au journal."""
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            session.n["bilans_combat"] += len(entry.get("personnages", {}))
            for report in entry.get("personnages", {}).values():
                session.n["bilan_tours"] += int(report.get("tours", 0))
                session.n["bilan_sorts"] += sum(report.get("sorts", {}).values())
                session.n["bilan_refus"] += int(report.get("refus", 0))
                session.n["bilan_clics_sans_effet"] += int(report.get("clics_sans_effet", 0))
                session.durees["bilan_duree_tours"].append(float(report.get("duree_tours", 0)))


# Rapports des outils (banque, échanges, porte, essais d'arrière-plan)

@dataclass
class RapportOutil:
    outil: str
    moment: datetime | None
    dossier: str
    issue: str  # réussi, échec, incomplet, essai, sans rapport
    detail: str
    lignes: int = 0


ECHEC = re.compile(r": arrêt\b|^Arrêt :|pas trouvée? : arrêt|Coffre pas vu|\bpas entré\b|\bpas sorti\b|encore ouverte|"
                   r"grisé ou absent|^Offre : changement 0[.,]0")
REUSSITE = {
    "banquier": re.compile(r"^Coffre ouvert"),
    "coffre_transfert": re.compile(r"^Transfert demandé"),
    "echange": re.compile(r"^Fenêtre d'échange chez le principal"),
    "echange_pile": re.compile(r"^Offre : changement [1-9]"),
    "echange_valider": re.compile(r"fenêtre d'échange fermée"),
    "entrer_porte": re.compile(r" : (?:entré|sorti) "),
}


def lire_rapport_outil(dossier: Path) -> RapportOutil | None:
    found = TOOL_DIR.fullmatch(dossier.name)
    if found is None or SESSION_DIR.fullmatch(dossier.name):
        return None
    outil = found.group(1)
    moment = datetime.strptime(f"{found.group(2)}_{found.group(3)}", "%Y%m%d_%H%M%S")
    path = dossier / "rapport.txt"
    if not path.is_file():
        return RapportOutil(outil, moment, dossier.name, "sans rapport", "pas de rapport.txt (outil interrompu ?)")
    with path.open(encoding="utf-8", errors="replace") as f:
        lignes = [line.strip() for line in f if line.strip()]
    echecs = [line for line in lignes if ECHEC.search(line)]
    reussite = REUSSITE.get(outil)
    if outil.startswith("arriere_plan"):
        issue, detail = "essai", f"{len(lignes)} lignes (essais de clics et de défilement en arrière-plan)"
    elif echecs:
        issue, detail = "échec", echecs[0]
    elif reussite is not None and any(reussite.search(line) for line in lignes):
        issue, detail = "réussi", lignes[-1]
    else:
        issue, detail = "incomplet", lignes[-1] if lignes else "rapport vide"
    if outil == "entrer_porte":
        ok = sum(1 for line in lignes if re.search(r" : (?:entré|sorti) ", line))
        detail = f"{ok}/{len(lignes)} personnages passés ; " + (lignes[0] if lignes else "")
    return RapportOutil(outil, moment, dossier.name, issue, detail, len(lignes))


# Mémoire des cartes

def resume_carte(path: Path = CARTE) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    regions, vues = Counter(), Counter()
    resume = Counter()
    for key, info in data.items():
        regions[key.rsplit(" ", 1)[0]] += 1
        sorties = info.get("sorties") or {}
        resume["sorties connues"] += sum(1 for v in sorties.values() if v and v != "bloquée")
        resume["sorties « bloquée »"] += sum(1 for v in sorties.values() if v == "bloquée")
        resume["sorties inconnues (null)"] += sum(1 for v in sorties.values() if v is None)
        resume["cartes avec soleils retenus selon le côté d'arrivée"] += bool(info.get("soleils_depuis"))
        resume["cartes avec sorties inatteignables selon le côté d'arrivée"] += bool(info.get("bloquees_depuis"))
        resume["cartes à plusieurs soleils d'un côté (rempart)"] += bool(info.get("plusieurs"))
        resume["cartes « à vérifier »"] += bool(info.get("a_verifier"))
        if isinstance(info.get("vue"), (int, float)):
            vues[datetime.fromtimestamp(info["vue"]).strftime("%d/%m")] += 1
    return {"cartes": len(data), "regions": dict(regions), "resume": dict(resume), "derniere_vue_par_jour": dict(vues)}


# Cumul et affichage

def cumuler(sessions: list[Session], nom: str) -> Session:
    total = Session(nom=nom, sessions=0)
    for s in sessions:
        total.sessions += 1
        total.n.update(s.n)
        for cle, valeurs in s.durees.items():
            total.durees[cle].extend(valeurs)
        for cle, exemple in s.exemples.items():
            total.exemples.setdefault(cle, exemple)
        total.trajets += s.trajets
        total.combats += s.combats
        for attr in ("fenetres", "sorts_tentes", "sorts_refuses", "tours_par_perso", "arrets", "autres_avertissements"):
            getattr(total, attr).update(getattr(s, attr))
        total.pods.update(s.pods)
        total.supprimees += s.supprimees
        total.n["duree_totale"] += round(s.duree)
        total.n["sessions_bot"] += s.genre == "bot"
        total.n["sessions_connexion"] += s.genre == "connexion"
        total.debut = min(filter(None, (total.debut, s.debut)), default=None)
        total.fin = max(filter(None, (total.fin, s.fin)), default=None)
    return total


def fr(valeur: float | int | None, decimales: int = 1) -> str:
    if valeur is None:
        return "–"
    if isinstance(valeur, int):
        return str(valeur)
    return f"{valeur:.{decimales}f}".replace(".", ",")


def moyenne(valeurs: list[float]) -> float | None:
    return statistics.fmean(valeurs) if valeurs else None


def stat(valeurs: list[float]) -> str:
    """« 12,3 s (méd. 10,1 ; n = 40) »."""
    if not valeurs:
        return "–"
    return f"{fr(statistics.fmean(valeurs))} s (méd. {fr(statistics.median(valeurs))} ; n = {len(valeurs)})"


def pourcent(part: float, total: float) -> str:
    return f"{fr(100 * part / total, 0)} %" if total else "–"


def indicateurs(s: Session) -> list[tuple[str, str, str]]:
    """(section, indicateur, valeur) : les lignes des tableaux par jour, par période et en cumul. `s` : un cumul
    (cumuler), même pour une seule session."""
    n, d = s.n, s.durees
    mine = [t for t in s.trajets if t.principal]
    arrives = [t for t in mine if t.issue == "arrivé"]
    regroupements = [t for t in s.trajets if not t.principal]
    passages = n["passages_ok"] + n["passages_rates"]
    temps_arrives = sum(t.duree or 0 for t in arrives)
    cartes_arrivees = sum(t.passages for t in arrives)
    en_trop = sum(max(0, t.passages - t.prevu) for t in arrives if t.prevu is not None)
    prevues = sum(t.prevu for t in arrives if t.prevu is not None)
    perdus = sum(n[k] for k in LOST)
    bloquees = n["sortie_inutilisable"] + n["pas_de_sortie"]
    suivis = n["suivi"] + n["suivi_illisible"]
    lectures = n["lecture_complete"] + n["lecture_partielle"] + n["lecture_illisible"]
    refus = n["sort_refuse"] + n["pas_ligne_de_vue"] + n["hors_portee"]
    arrets = "; ".join(f"{raison} x{c}" for raison, c in s.arrets.most_common()) or "–"
    fenetres = ", ".join(f"« {nom} » x{c}" for nom, c in s.fenetres.most_common()) or "0"
    nm = "non mesurable"
    return [
        ("Sessions", "Lancements du bot (dont connexion seule)", f"{s.sessions} ({n['sessions_connexion']})"),
        ("Sessions", "Durée cumulée des journaux (min)", fr(n["duree_totale"] / 60)),
        ("Trajets", "Trajets du principal : lancés / arrivés / arrêtés / interrompus",
         f"{len(mine)} / {len(arrives)} / {sum(t.issue == 'arrêté' for t in mine)} / "
         f"{sum(t.issue == 'interrompu' for t in mine)}"),
        ("Trajets", "Raisons d'arrêt", arrets),
        ("Trajets", "Changements de carte réussis (principal)", fr(n["passages_ok"])),
        ("Trajets", "Changements de carte ratés (principal)", fr(n["passages_rates"])),
        ("Trajets", "Taux de réussite d'un changement de carte", pourcent(n["passages_ok"], passages)),
        ("Trajets", "Temps d'un changement de carte réussi (« Trajet : côté » -> « Nouvelle carte »)",
         stat(d["passage_ok"])),
        ("Trajets", "Temps par carte, trajets arrivés (durée / cartes passées)",
         f"{fr(temps_arrives / cartes_arrivees)} s" if cartes_arrivees else "–"),
        ("Trajets", "Temps perdu dans les changements ratés", f"{fr(sum(d['passage_rate']), 0)} s"),
        ("Trajets", "Cartes en trop / cartes prévues (trajets arrivés)",
         f"{en_trop} / {prevues} ({pourcent(en_trop, prevues)})" if prevues else "–"),
        ("Trajets", "« Nouvel itinéraire » (itinéraire recalculé)", fr(n["nouvel_itineraire"])),
        ("Trajets", "« Carte X au lieu de Y » (arrivé ailleurs que prévu)", fr(n["carte_inattendue"])),
        ("Trajets", "Sorties notées bloquées (« inutilisable » + « pas de sortie »)", fr(bloquees)),
        ("Trajets", "  dont « bloquée pour ce trajet seulement » (rien d'appris)", fr(n["bloquee_ce_trajet"])),
        ("Trajets", "  notées inatteignables depuis un côté d'arrivée", fr(n["inatteignable_depuis"])),
        ("Trajets", "  débloquées (des suiveurs l'ont prise)", fr(n["debloquee"])),
        ("Trajets", "« Toujours sur la même carte après 20 s » (délai du verdict)",
         f"{n['toujours_meme_carte']} ; {stat(d['verdict_toujours_meme_carte'])}"),
        ("Trajets", "« ne bouge plus » (délai du verdict)",
         f"{n['ne_bouge_plus']} ; {stat(d['verdict_ne_bouge_plus'])}"),
        ("Trajets", "« a marché vers ... puis s'est arrêté » (délai du verdict)",
         f"{n['a_marche']} ; {stat(d['verdict_a_marche'])}"),
        ("Trajets", "Temps passé sur des soleils inatteignables (somme des verdicts)",
         f"{fr(sum(d['verdicts']), 0)} s"),
        ("Trajets", "« Autre soleil »", fr(n["autre_soleil"])),
        ("Trajets", "Reclics du principal sur le même soleil (journal détaillé)",
         f"{n['reclic_principal']} ({fr(n['reclic_principal'] / passages if passages else None, 2)} par passage)"),
        ("Trajets", "Quelqu'un sur le soleil (menu ouvert)", fr(n["sur_le_soleil"])),
        ("Lecture des cartes", "Arrivées du principal : nom lu / lu en partie / illisible",
         f"{n['lecture_complete']} / {n['lecture_partielle']} / {n['lecture_illisible']} "
         f"({pourcent(n['lecture_illisible'] + n['lecture_partielle'], lectures)} sans coordonnées lues)"
         if lectures else "–"),
        ("Lecture des cartes", "  dont région non reconnue (coordonnées lues)", fr(n["lecture_region_inconnue"])),
        ("Lecture des cartes", "Coordonnées lues différentes du chemin",
         fr(n["coord_differentes"] + n["coord_contradictoires"])),
        ("Suiveurs", "Arrivées de suiveurs : carte vérifiée / nom illisible (non vérifiée)",
         f"{n['suivi']} / {n['suivi_illisible']} ({pourcent(n['suivi_illisible'], suivis)} non vérifiées)"
         if suivis else "–"),
        ("Suiveurs", "Suiveurs perdus : épisodes (messages)", f"{n['episodes_perdus']} ({perdus})"),
        ("Suiveurs", "  dont « passé sans le principal »", fr(n["passe_sans_principal"])),
        ("Suiveurs", "  dont pas sur la carte du principal (décor, départ, carte quittée)",
         fr(n["decor_different"] + n["absent_depart"] + n["pas_sur_carte_quittee"])),
        ("Suiveurs", "  dont « n'a pas suivi » / arrivé ailleurs", fr(n["pas_suivi"] + n["arrive_ailleurs"])),
        ("Suiveurs", "Regroupements lancés / réussis / impossibles",
         f"{n['regroupement']} / {n['regroupe']} / {n['regroupement_impossible'] + n['position_inconnue']}"),
        ("Suiveurs", "Trajets de regroupement : cartes passées ; temps d'un passage",
         f"{sum(t.passages for t in regroupements)} ; {stat(d['passage_ok_regroupement'])}" if regroupements else "–"),
        ("Suiveurs", "Reclics d'un suiveur (« Je suis le principal ... »)", fr(n["reclic_suiveur"])),
        ("Suiveurs", "Battement entre deux cartes (suiveurs vérifiés, regroupement)", stat(d["battement"])),
        ("Suiveurs", "Arrêts « principal bloqué par le jeu »", fr(n["principal_bloque"])),
        ("Combats", "Combats terminés ; durée", f"{n['combats']} ; {stat(d['combat'])}"),
        ("Combats", "Victoires / défaites", nm),
        ("Combats", "« Mon tour » ; lignes « Tour : » ; durée d'un tour",
         f"{n['mon_tour']} ; {n['tours_bilan']} ; {stat(d['tour'])}"),
        ("Combats", "Sorts tentés (« lancé sur ») / partis (lignes « Tour : »)",
         f"{n['sorts_tentes']} / {n['sorts_partis']}"),
        ("Combats", "Refus : PA inchangés / pas de ligne de vue / hors de portée",
         f"{n['sort_refuse']} / {n['pas_ligne_de_vue']} / {n['hors_portee']} (total {refus})"),
        ("Combats", "Non sélectionnables / « pas partis » / écartés pour la session",
         f"{n['non_selectionnable']} / {n['sort_pas_parti']} / {n['sort_banni']}"),
        ("Combats", "Sorts enchaînés trop tôt (jeu pas prêt, relancés d'ici)", fr(n["sort_trop_tot"])),
        ("Combats", "Délai de confirmation : parti / pas parti",
         f"{stat(d['confirmation_parti'])} / {stat(d['confirmation_pas_parti'])}"),
        ("Combats", "PA restants en fin de tour", nm),
        ("Combats", "Faux « Mon tour » rattrapés (« Suite de mon tour »)", fr(n["suite_tour"])),
        ("Combats", "Main pas passée / F1 ignoré", fr(n["main_pas_passee"])),
        ("Combats", "Tours détectés en retard", f"{nm} (tours signalés sans timer : {n['notification_attente']})"),
        ("Combats", "Bilans de combat : tours / sorts / refus",
         f"{n['bilan_tours']} / {n['bilan_sorts']} / {n['bilan_refus']}" if n["bilans_combat"] else "–"),
        ("Fenêtres et sécurité", "Fenêtres fermées", fenetres),
        ("Fenêtres et sécurité", "Fenêtres inconnues / qui ne se ferment pas",
         f"{n['fenetre_inconnue']} / {n['fenetre_pas_fermee']}"),
        ("Fenêtres et sécurité", "Menu principal ouvert par Échap", fr(n["menu_principal"])),
        ("Fenêtres et sécurité", "Fenêtre « quitter ta partie » (réponse Non)", fr(n["quitter"])),
        ("Fenêtres et sécurité", "Déconnexions : reconnexion du bot / principal / « Connexion » (inactivité)",
         f"{n['deconnexion']} / {n['principal_deconnecte']} / {n['connexion_ok']} ({n['deconnexion_inactivite']})"),
        ("Fenêtres et sécurité", "Personnages mis en jeu (« X est en jeu ») ; connexions impossibles",
         f"{n['en_jeu']} ; {n['connexion_echec']}"),
        ("Erreurs", "Lignes ERROR / « Erreur inattendue » / Traceback / avertissements (WARNING)",
         f"{n['erreurs']} / {n['erreur_inattendue']} / {n['exceptions']} / {n['avertissements']}"),
        ("Banque", "Alertes « il faut passer à la banque » (pods ≥ 90 %)", fr(n["pods_pleins"])),
    ]


def tableau(entetes: list[str], lignes: list[list[str]]) -> str:
    def cellule(texte: str) -> str:
        return str(texte).replace("|", "/").replace("\n", " ")
    out = ["| " + " | ".join(entetes) + " |", "|" + "|".join("---" for _ in entetes) + "|"]
    out += ["| " + " | ".join(cellule(c) for c in ligne) + " |" for ligne in lignes]
    return "\n".join(out)


def tableau_indicateurs(groupes: list[tuple[str, Session]]) -> str:
    colonnes = [indicateurs(s) for _, s in groupes]
    lignes = []
    for i, (section, libelle, _) in enumerate(colonnes[0]):
        lignes.append([section, libelle] + [col[i][2] for col in colonnes])
    return tableau(["Domaine", "Indicateur"] + [nom for nom, _ in groupes], lignes)


def problemes(sessions: list[Session], limite: int = 10) -> list[tuple[str, int, int, str]]:
    """(libellé, occurrences, sessions touchées, exemple de ligne), du plus fréquent au moins fréquent : messages
    marqués « problème » (MOTIFS), puis avertissements que ne reconnaît aucun motif."""
    total = cumuler(sessions, "cumul")
    rangs = []
    for motif in MOTIFS:
        if motif.probleme and total.n[motif.cle]:
            touchees = sum(1 for s in sessions if s.n[motif.cle])
            rangs.append((motif.libelle, total.n[motif.cle], touchees, total.exemples.get(motif.cle, "")))
    for message, count in total.autres_avertissements.items():
        touchees = sum(1 for s in sessions if s.autres_avertissements[message])
        rangs.append((f"Avertissement : {message}", count, touchees, total.exemples.get("avert:" + message, "")))
    rangs.sort(key=lambda r: (-r[1], r[0]))
    return rangs[:limite]


NON_MESURABLE = [
    ("Victoires et défaites", "« Combat terminé » ne dit pas l'issue ; la fenêtre de fin de combat est fermée sans "
     "être lue", "« Combat terminé : victoire|défaite (N tours, D s, personnages morts : ...) »"),
    ("PA (et PM) restants en fin de tour", "la ligne « Tour : ... » donne la durée, les sorts et les refus, pas les "
     "PA", "ajouter « PA restants N/M, PM restants N/M » à la ligne « Tour : »"),
    ("Tours détectés en retard", "« Mon tour » n'a pas l'heure du début réel du tour (timer ou timeline)",
     "« Mon tour (timer vu N,N s après la fin du tour précédent dans la timeline) »"),
    ("Faux « Mon tour »", "seuls ceux que le bot rattrape (« Suite de mon tour ») sont au journal",
     "« Faux tour : ... » quand un tour finit sans action ni F1 accepté, ou que le timer n'est pas revu"),
    ("Dégâts infligés et reçus, morts", "rien au journal (piste P13 : journal du combat dans le chat)",
     "« Dégâts : N sur X (sort) » et « X est mort » lus dans le chat du combat"),
    ("Banque dans le bot", "le bot ne journalise que les pods ; banque et échanges ne sont faits que par des outils",
     "« Banque : porte (s), coffre ouvert (s), N piles transférées, pods X % -> Y % », "
     "« Échange X -> Y : N objets en D s »"),
    ("Fin réelle d'une session", "arrêt brutal (fenêtre fermée, F12 pendant une attente) : la dernière ligne n'est "
     "pas une ligne de fin", "« Bilan de session : durée, cartes, combats, arrêts » écrit à chaque fin, même brutale"),
    ("Comparaison dans le temps", "logs.keep_sessions = 20 : les journaux des tests de combat du 11/10 (15:43 à "
     "17:52) sont effacés", "garder ce bilan (ou combats.jsonl et une ligne de bilan par session) hors de logs/"),
]


def rapport(sessions: list[Session], outils: list[RapportOutil], carte: dict | None, ignores: list[str]) -> str:
    out: list[str] = ["# Bilan des sessions (tools/bilan_sessions.py)", ""]
    bots = list(sessions)
    out.append(f"Journaux du bot lus : {len(bots)} ; rapports d'outils lus : {len(outils)}"
               + (f" ; dossiers ignorés : {', '.join(ignores)}" if ignores else ""))
    out.append("")
    if bots:
        out += ["## Sessions du bot", ""]
        lignes = []
        for s in sorted(bots, key=lambda x: x.lancement or x.debut or datetime.min):
            mine = [t for t in s.trajets if t.principal]
            passages = s.n["passages_ok"] + s.n["passages_rates"]
            lignes.append([
                s.nom, s.periode.code if s.periode else "?", fr(s.duree / 60), s.genre,
                f"{sum(t.issue == 'arrivé' for t in mine)}/{len(mine)}",
                f"{s.n['passages_ok']}/{s.n['passages_rates']}",
                fr(moyenne(s.durees["passage_ok"])),
                str(s.n["toujours_meme_carte"]), f"{s.n['ne_bouge_plus']}/{s.n['a_marche']}",
                str(s.n["sortie_inutilisable"] + s.n["pas_de_sortie"]),
                str(s.n["episodes_perdus"]), str(s.n["combats"]),
                f"{s.n['avertissements']}/{s.n['erreurs']}", s.fin_raison[:70],
            ])
        out.append(tableau(["Session", "Pér.", "Durée (min)", "Genre", "Trajets arrivés", "Cartes OK/ratées",
                            "s/carte OK", "« 20 s »", "« ne bouge plus »/« a marché »", "Sorties bloquées",
                            "Suiveurs perdus", "Combats", "Avert./Erreurs", "Fin"], lignes))
        out.append("")
        par_jour: dict[str, list[Session]] = defaultdict(list)
        par_periode: dict[str, list[Session]] = defaultdict(list)
        for s in bots:
            moment = s.lancement or s.debut
            par_jour[moment.strftime("%d/%m/%Y") if moment else "?"].append(s)
            par_periode[s.periode.code if s.periode else "?"].append(s)
        out += ["## Par jour", ""]
        out.append(tableau_indicateurs([(f"{jour} ({len(ss)} sessions)", cumuler(ss, jour))
                                        for jour, ss in sorted(par_jour.items())]))
        out += ["", "## Par période", ""]
        for periode in PERIODES:
            count = len(par_periode.get(periode.code, []))
            outils_p = sum(1 for o in outils if periode_de(o.moment) is periode)
            out.append(f"- {periode.code} : {periode.titre} — {count} journal(aux) du bot, {outils_p} rapport(s) "
                       f"d'outil")
        out.append("")
        groupes = [(f"{code} ({len(ss)})", cumuler(ss, code)) for code, ss in
                   sorted(par_periode.items(), key=lambda kv: kv[0])]
        total = cumuler(bots, "cumul")
        out.append(tableau_indicateurs(groupes + [(f"Cumul ({len(bots)})", total)]))
        trajets = [t for t in total.trajets if t.principal]
        if trajets:
            out += ["", "## Trajets du principal", ""]
            out.append(tableau(
                ["Session", "Pér.", "Trajet", "Cartes prévues", "Cartes passées", "Ratés", "Recalculs", "Durée (s)",
                 "s/carte", "Issue"],
                [[t.session, (periode_de(t.debut).code if periode_de(t.debut) else "?"),
                  (f"{t.depart[0]},{t.depart[1]}" if t.depart else "?") + f" -> {t.but[0]},{t.but[1]}",
                  fr(t.prevu), str(t.passages), str(t.rates), str(t.recalculs), fr(t.duree, 0),
                  fr(t.duree / t.passages) if t.duree and t.passages else "–", f"{t.issue} ({t.raison})"]
                 for t in trajets]))
        out += ["", "## Problèmes les plus fréquents", ""]
        rangs = problemes(bots, 10)
        out.append(tableau(["#", "Problème", "Occurrences", "Sessions", "Exemple"],
                           [[str(i + 1), libelle, str(count), str(touchees), exemple]
                            for i, (libelle, count, touchees, exemple) in enumerate(rangs)]))
        if total.sorts_tentes:
            out += ["", "Sorts tentés : " + ", ".join(f"{k} x{v}" for k, v in total.sorts_tentes.most_common()),
                    "Sorts refusés : " + (", ".join(f"{k} x{v}" for k, v in total.sorts_refuses.most_common()) or "0")]
        if total.tours_par_perso:
            out.append("Tours par personnage : " + ", ".join(f"{k} {v}" for k, v in total.tours_par_perso.items()))
        if total.pods:
            out.append("Pods (dernier relevé) : " + ", ".join(f"{k} {v} %" for k, v in total.pods.items()))
        if total.supprimees:
            out += ["", f"Sessions effacées par la rotation des journaux (d'après « Anciennes sessions supprimées ») : "
                        f"{len(total.supprimees)} : {', '.join(total.supprimees)}"]
    if outils:
        out += ["", "## Banque, échanges et autres outils (rapport.txt)", ""]
        par_outil: dict[str, Counter] = defaultdict(Counter)
        for o in outils:
            par_outil[o.outil][o.issue] += 1
        out.append(tableau(["Outil", "Lancements", "Réussis", "Échecs", "Incomplets / essais / sans rapport"],
                           [[outil, str(sum(c.values())), str(c["réussi"]), str(c["échec"]),
                             str(c["incomplet"] + c["essai"] + c["sans rapport"])]
                            for outil, c in sorted(par_outil.items())]))
        out.append("")
        out.append(tableau(["Dossier", "Pér.", "Issue", "Détail"],
                           [[o.dossier, (periode_de(o.moment).code if periode_de(o.moment) else "?"), o.issue,
                             o.detail[:120]] for o in sorted(outils, key=lambda x: x.moment or datetime.min)]))
    if carte:
        out += ["", "## Mémoire des cartes (data/carte.json)", ""]
        out.append(f"{carte['cartes']} cartes : " + ", ".join(f"{k} {v}" for k, v in carte["regions"].items()))
        out.append("; ".join(f"{k} : {v}" for k, v in carte["resume"].items()))
        out.append("Dernier passage par jour : " + ", ".join(f"{k} : {v}" for k, v in
                                                           sorted(carte["derniere_vue_par_jour"].items())))
    out += ["", "## Non mesurable aujourd'hui", ""]
    out.append(tableau(["Indicateur", "Pourquoi", "Message à ajouter au journal"], [list(x) for x in NON_MESURABLE]))
    return "\n".join(out) + "\n"


def en_json(sessions: list[Session], outils: list[RapportOutil], carte: dict | None) -> dict:
    def session(s: Session) -> dict:
        return {
            "nom": s.nom, "periode": s.periode.code if s.periode else None, "genre": s.genre, "mode": s.mode,
            "debut": s.debut.isoformat() if s.debut else None, "fin": s.fin.isoformat() if s.fin else None,
            "fin_raison": s.fin_raison, "compteurs": dict(s.n),
            "durees": {k: {"n": len(v), "moyenne": round(statistics.fmean(v), 2), "mediane": statistics.median(v)}
                       for k, v in s.durees.items() if v},
            "trajets": [{"acteur": t.acteur, "principal": t.principal, "depart": t.depart, "but": t.but,
                         "issue": t.issue,
                         "raison": t.raison, "passages": t.passages, "prevu": t.prevu, "rates": t.rates,
                         "duree": t.duree} for t in s.trajets],
            "fenetres": dict(s.fenetres), "indicateurs": [list(x) for x in indicateurs(cumuler([s], s.nom))],
        }
    return {"sessions": [session(s) for s in sessions],
            "outils": [{"outil": o.outil, "dossier": o.dossier, "issue": o.issue, "detail": o.detail} for o in outils],
            "carte": carte}


def lire_dossiers(dossiers: list[Path]) -> tuple[list[Session], list[RapportOutil], list[str]]:
    sessions, outils, ignores = [], [], []
    for dossier in sorted(dossiers):
        if not dossier.is_dir():
            ignores.append(f"{dossier.name} (pas un dossier)")
            continue
        session = analyser_dossier(dossier)
        if session is not None:
            sessions.append(session)
            continue
        outil = lire_rapport_outil(dossier)
        if outil is not None:
            outils.append(outil)
        else:
            ignores.append(dossier.name)
    return sessions, outils, ignores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dossiers", nargs="*", type=Path, help="dossiers de logs/ (par défaut : tous)")
    parser.add_argument("--json", type=Path, help="écrit aussi les chiffres bruts dans ce fichier JSON")
    parser.add_argument("--sans-carte", action="store_true", help="sans le résumé de data/carte.json")
    parser.add_argument("--resume", action="store_true",
                        help="résumé court de chaque dossier donné, à analyser (tools/resume_session.py)")
    args = parser.parse_args(argv)
    if args.resume:
        from tools.resume_session import main as resumer  # import local : resume_session importe ce module
        return resumer([str(d) for d in args.dossiers])
    dossiers = args.dossiers or (sorted(p for p in LOGS_DIR.iterdir() if p.is_dir()) if LOGS_DIR.is_dir() else [])
    sessions, outils, ignores = lire_dossiers(dossiers)
    carte = None if args.sans_carte else resume_carte()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(rapport(sessions, outils, carte, ignores))
    if args.json:
        args.json.write_text(json.dumps(en_json(sessions, outils, carte), ensure_ascii=False, indent=1, default=str),
                             encoding="utf-8")
        print(f"Chiffres bruts : {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
