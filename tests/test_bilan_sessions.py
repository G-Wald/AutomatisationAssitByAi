"""Bilan des sessions (tools/bilan_sessions.py) sur des lignes de journal écrites ici, au format de bot.log."""

import json
from datetime import date, datetime

import pytest

from tools.bilan_sessions import (LINE, TAG, analyser_lignes, cumuler, indicateurs, lire_dossiers, lire_rapport_outil,
                                  main, periode_de, problemes, rapport)

JOUR = date(2026, 10, 11)
EQUIPE = "21:50:03.646 INFO    Équipe : Gela-[A13], Brawli-[Gal], Imthebest ; principal : Gela-[A13]"

# Trajet de deux cartes, version du 11/10 soir : un soleil inatteignable vu en ~4 s, puis l'autre soleil du même côté.
TRAJET_ARRIVE = [
    EQUIPE,
    "21:50:03.700 INFO    Mode : trajet jusqu'à une carte, toute l'équipe suit le principal, sans lancer de combat",
    "21:50:04.634 INFO    [Gela-[A13]] Trajet vers 1,-14 (Monde des Douze), sans combat ; départ : 3,-14 "
    "(Astrub (Cité d'Astrub) 3,-14)",
    "21:50:04.637 INFO    [Gela-[A13]] Itinéraire de 3,-14 à 1,-14 : 2 carte(s) : 2,-14 1,-14",
    "21:50:04.637 INFO    [Gela-[A13]] Trajet : côté gauche, vers 2,-14 (encore 2 carte(s) jusqu'à 1,-14)",
    "21:50:04.643 INFO    [Gela-[A13]] Vers la carte du gauche : clic sur le soleil en (1, 7)",
    "21:50:05.000 INFO    [Imthebest] Toute l'équipe vers le gauche : Brawli-[Gal], Imthebest cliquent aussi sur le "
    "soleil en (1, 7)",
    "21:50:10.643 INFO    [Gela-[A13]] Nouvelle carte : Astrub (Cité d'Astrub) 2,-14",
    "21:50:10.700 INFO    [Brawli-[Gal]] Brawli-[Gal] a suivi le principal (Astrub (Cité d'Astrub) 2,-14)",
    "21:50:10.750 INFO    [Imthebest] Imthebest a changé de carte (nom illisible)",
    "21:50:11.643 INFO    [Gela-[A13]] Trajet : côté gauche, vers 1,-14 (encore 1 carte(s) jusqu'à 1,-14)",
    "21:50:11.650 INFO    [Gela-[A13]] Vers la carte du gauche : clic sur le soleil en (1, 9)",
    "21:50:15.850 INFO    [Gela-[A13]] Le personnage ne bouge plus et n'a pas changé de carte : soleil en (1, 9) pas "
    "atteignable d'ici ? (essai 1/2)",
    "21:50:15.851 INFO    [Gela-[A13]] Vers la carte du gauche : clic sur le soleil en (1, 9) (bas de la case)",
    "21:50:19.051 INFO    [Gela-[A13]] Le personnage ne bouge plus et n'a pas changé de carte : soleil en (1, 9) pas "
    "atteignable d'ici ? (essai 2/2)",
    "21:50:20.000 INFO    [Gela-[A13]] Soleil(s) du gauche retrouvé(s) à l'écran : (1, 29)",
    "21:50:20.001 INFO    [Gela-[A13]] Autre soleil du gauche : (1, 29) (le précédent n'est pas atteignable d'ici)",
    "21:50:20.002 INFO    [Gela-[A13]] Vers la carte du gauche : clic sur le soleil en (1, 29) (contour de la touche A)",
    "21:50:24.643 INFO    [Gela-[A13]] Nom de la carte illisible (décor clair derrière le texte ?) : carte 1,-14 d'après "
    "le chemin (capture : C:\\logs\\0004_nom_de_carte_illisible.png)",
    "21:50:24.643 INFO    [Gela-[A13]] Nouvelle carte : carte au nom illisible (coordonnées 1,-14 d'après le chemin)",
    "21:50:25.643 INFO    [Gela-[A13]] Arrivé en 1,-14 (2 changement(s) de carte)",
]

# Version d'avant le 11/10 21:50 (« Toujours sur la même carte après 20 s (essai 1/3) »), puis le format actuel ; les
# suiveurs passent sans le principal et le rejoignent par leur propre trajet ; le trajet s'arrête.
TRAJET_ARRETE = [
    EQUIPE,
    "21:05:38.646 INFO    [Gela-[A13]] Trajet vers 4,-13 (Monde des Douze), sans combat ; départ : 1,-13 "
    "(Astrub (zone inconnue) 1,-13)",
    "21:05:38.646 INFO    [Gela-[A13]] Itinéraire de 1,-13 à 4,-13 : 3 carte(s) : 2,-13 3,-13 4,-13",
    "21:05:38.646 INFO    [Gela-[A13]] Trajet : côté droite, vers 2,-13 (encore 3 carte(s) jusqu'à 4,-13)",
    "21:05:38.651 INFO    [Gela-[A13]] Vers la carte du droite : clic sur le soleil en (27, 15)",
    "21:05:43.038 DEBUG   [Gela-[A13]] Toujours sur la carte de départ : je reclique sur le soleil",
    "21:06:00.325 WARNING [Gela-[A13]] Toujours sur la même carte après 20 s (essai 1/3)",
    "21:06:00.399 INFO    [Gela-[A13]] Soleil du droite retrouvé à l'écran en (27, 15)",
    "21:06:00.400 INFO    [Gela-[A13]] Vers la carte du droite : clic sur le soleil en (27, 15) (bas de la case)",
    "21:06:21.059 WARNING [Gela-[A13]] Toujours sur la même carte après 20 s (soleil en (27, 15), essai 2/3)",
    "21:06:41.899 WARNING [Gela-[A13]] Sortie du droite inutilisable, je la note bloquée (capture : C:\\logs\\x.png)",
    "21:06:41.942 WARNING [Brawli-[Gal]] Brawli-[Gal] est passé sans le principal (resté sur sa carte) : il le "
    "rejoindra par son trajet",
    "21:06:42.775 INFO    [Gela-[A13]] Brawli-[Gal] rejoint le principal en 1,-13 (depuis 2,-13)",
    "21:06:42.801 INFO    [Brawli-[Gal]] Trajet vers 1,-13 (Monde des Douze), sans combat ; départ : 2,-13 "
    "(Astrub (zone inconnue) 2,-13)",
    "21:06:42.804 INFO    [Brawli-[Gal]] Itinéraire de 2,-13 à 1,-13 : 1 carte(s) : 1,-13",
    "21:06:42.804 INFO    [Brawli-[Gal]] Trajet : côté gauche, vers 1,-13 (encore 1 carte(s) jusqu'à 1,-13)",
    "21:06:42.809 INFO    [Brawli-[Gal]] Vers la carte du gauche : clic sur le soleil en (1, 15)",
    "21:06:45.007 INFO    [Brawli-[Gal]] Nouvelle carte : Astrub (zone inconnue) 1,-13",
    "21:06:51.048 INFO    [Brawli-[Gal]] Arrivé en 1,-13 (1 changement(s) de carte)",
    "21:06:51.090 INFO    [Gela-[A13]] Brawli-[Gal] a rejoint le principal en 1,-13",
    "21:06:51.255 INFO    [Gela-[A13]] Nouvel itinéraire de 1,-13 à 4,-13 : 3 carte(s) : 2,-13 3,-13 4,-13",
    "21:06:51.255 INFO    [Gela-[A13]] Trajet : côté droite, vers 2,-13 (encore 3 carte(s) jusqu'à 4,-13)",
    "21:07:55.977 WARNING [Gela-[A13]] Sortie du droite inutilisable, je la note bloquée (capture : C:\\logs\\y.png)",
    "21:07:55.978 WARNING [Gela-[A13]] Trajet vers 4,-13 arrêté : 5 changements de carte ratés d'affilée",
]

COMBAT = [
    "16:12:00.000 INFO    Équipe : Gela-[A13], Brawli-[Gal] ; principal : Gela-[A13]",
    "16:12:01.000 INFO    [Gela-[A13]] Placement : combat de Gela-[A13]",
    "16:12:05.000 INFO    [Gela-[A13]] Mon tour",
    "16:12:06.000 INFO    [Gela-[A13]] Flèche Magique sur l'ennemi en (5, 6) : le plus de dégâts",
    "16:12:06.100 INFO    [Gela-[A13]] Flèche Magique lancé sur l'ennemi en (5, 6) (120 PV)",
    "16:12:07.000 DEBUG   [Gela-[A13]] Flèche Magique : parti en 1.2 s",
    "16:12:08.000 INFO    [Gela-[A13]] Flèche Explosive lancé sur la case (4, 4)",
    "16:12:12.000 INFO    [Gela-[A13]] Flèche Explosive refusé sur la case (4, 4) (PA inchangés, capture : C:\\x.png) : "
    "sans doute hors de portée ou sans ligne de vue d'ici",
    "16:12:13.000 INFO    [Gela-[A13]] Pas de ligne de vue sur l'ennemi en (5, 6)",
    "16:12:14.000 INFO    [Gela-[A13]] Maîtrise de l'Arc lancé sur moi (clic droit sur sa case)",
    "16:12:15.000 INFO    [Gela-[A13]] Fin de tour (F1)",
    "16:12:15.100 INFO    [Gela-[A13]] Tour : 9,5 s ; 2 sorts (dont 1 en rafale), 1 déplacement, 1 refus ; attentes : "
    "condition",
    "16:12:20.000 INFO    [Brawli-[Gal]] Mon tour",
    "16:12:21.000 INFO    [Brawli-[Gal]] Suite de mon tour (4 PA, 1.5 s après) : la main n'était pas passée, je garde "
    "ce que j'ai lancé",
    "16:12:22.000 INFO    [Brawli-[Gal]] Epée du destin non sélectionnable (touche num7) : plus assez de PA, sort en "
    "relance ou raccourci différent",
    "16:12:23.000 WARNING [Brawli-[Gal]] La main ne semble pas être passée",
    "16:12:30.000 INFO    [Gela-[A13]] Combat terminé",
    "16:12:30.100 INFO    [Gela-[A13]] Bilan du combat 1, Gela-[A13] : 1 tour(s) (9,5 s) ; 2 sort(s) (Flèche Magique "
    "x1, Maîtrise de l'Arc x1) ; 1 refus ; 1 déplacement(s)",
    "16:12:30.200 INFO    [Gela-[A13]] Bilan du combat 1, Brawli-[Gal] : 1 tour(s) (3,0 s) ; 0 sort(s) ; 0 refus ; "
    "0 déplacement(s)",
    "16:12:31.000 INFO    [Gela-[A13]] Fenêtre « fin de combat » : je la ferme dans les 2 fenêtres du combat",
    "16:12:35.000 INFO    Fin de la session : 1 combat(s), le maximum demandé",
]

SECURITE = [
    "19:10:00.000 INFO    [Brawli-[Gal]] Échap a ouvert le Menu principal : je le referme (Échap)",
    "19:10:01.000 WARNING [Imthebest] Échap a ouvert le Menu principal (rien d'autre à fermer) : je le referme",
    "19:10:02.000 WARNING [Imthebest] Fenêtre « Cette action va quitter ta partie en cours » : je réponds « Non »",
    "19:10:03.000 INFO    [Imthebest] Fenêtre « niveau supérieur » : je la ferme",
    "19:10:04.000 WARNING Jesuisleking est déconnecté : je le reconnecte",
    "19:10:05.000 INFO    Fenêtre « Connexion » (déconnecté pour inactivité) : « Ok »",
    "19:10:20.000 INFO    Jesuisleking est en jeu",
    "19:10:21.000 ERROR   Reconnexion de Imthebest impossible : écran inconnu",
    "19:10:22.000 ERROR   Erreur inattendue : le bot s'arrête",
    "Traceback (most recent call last):",
    '  File "bot/__main__.py", line 1, in <module>',
]


def lire(lignes, nom="essai"):
    return analyser_lignes(lignes, nom, JOUR)


def test_nom_du_personnage_avec_crochets():
    found = LINE.match("21:04:43.125 INFO    [Gela-[A13]] Vers la carte du haut : clic sur le soleil en (13, 1)")
    assert found.group(5) == "INFO"
    tag = TAG.match(found.group(6))
    assert tag.group(1) == "Gela-[A13]"
    assert tag.group(2) == "Vers la carte du haut : clic sur le soleil en (13, 1)"


def test_trajet_arrive_passages_et_temps():
    s = lire(TRAJET_ARRIVE)
    assert s.genre == "bot" and s.principal == "Gela-[A13]"
    assert (s.n["passages_ok"], s.n["passages_rates"]) == (2, 0)
    assert s.durees["passage_ok"] == pytest.approx([6.006, 13.0], abs=0.01)
    assert s.durees["battement"] == pytest.approx([1.0, 1.0], abs=0.01)
    trajet, = s.trajets
    assert (trajet.issue, trajet.passages, trajet.prevu, trajet.depart, trajet.but) == ("arrivé", 2, 2, (3, -14), (1, -14))
    assert trajet.duree == pytest.approx(21.0, abs=0.01)
    assert s.fin_raison.startswith("trajet arrivé")


def test_soleil_inatteignable_vu_vite_et_autre_soleil():
    s = lire(TRAJET_ARRIVE)
    assert s.n["ne_bouge_plus"] == 2 and s.n["autre_soleil"] == 1 and s.n["soleil_retrouve"] == 1
    assert s.durees["verdict_ne_bouge_plus"] == pytest.approx([4.2, 3.2], abs=0.01)
    assert s.n["toujours_meme_carte"] == 0


def test_lecture_du_nom_et_suiveurs():
    s = lire(TRAJET_ARRIVE)
    assert (s.n["lecture_complete"], s.n["lecture_illisible"]) == (1, 1)
    assert (s.n["suivi"], s.n["suivi_illisible"]) == (1, 1)
    assert s.n["nom_illisible"] == 1


def test_ancien_et_nouveau_format_du_verdict_de_20_s():
    s = lire(TRAJET_ARRETE)
    assert s.n["toujours_meme_carte"] == 2
    assert s.durees["verdict_toujours_meme_carte"] == pytest.approx([21.674, 20.659], abs=0.01)
    assert s.n["soleil_retrouve"] == 1  # « Soleil du droite retrouvé à l'écran en ... » (ancien format)
    assert s.n["reclic_principal"] == 1


def test_trajet_arrete_et_regroupement_d_un_suiveur():
    s = lire(TRAJET_ARRETE)
    principal = [t for t in s.trajets if t.principal]
    suiveur = [t for t in s.trajets if not t.principal]
    assert len(principal) == 1 and principal[0].issue == "arrêté"
    assert principal[0].raison == "5 changements de carte ratés d'affilée" and principal[0].recalculs == 1
    assert s.arrets["5 changements de carte ratés d'affilée"] == 1
    assert (s.n["passages_ok"], s.n["passages_rates"]) == (0, 2)  # le passage du suiveur ne compte pas au principal
    assert s.durees["passage_rate"][0] == pytest.approx(63.253, abs=0.01)
    assert s.n["passages_ok_regroupement"] == 1
    assert suiveur[0].acteur == "Brawli-[Gal]" and suiveur[0].issue == "arrivé"
    assert (s.n["passe_sans_principal"], s.n["regroupement"], s.n["regroupe"]) == (1, 1, 1)
    assert s.n["episodes_perdus"] == 1
    assert (s.n["sortie_inutilisable"], s.n["nouvel_itineraire"]) == (2, 1)


def test_un_suiveur_perdu_deux_messages_un_episode():
    s = lire([
        EQUIPE,
        "22:07:19.875 WARNING [Imthebest] Imthebest n'est pas sur la carte du principal (décor différent) : il ne le suit "
        "pas à l'aveugle",
        "22:07:24.308 WARNING [Imthebest] Imthebest n'est pas sur la carte que quitte le principal (il est sur région "
        "inconnue (zone inconnue) 5,-15) : il ne le suit pas",
        "22:07:34.010 INFO    [Gela-[A13]] Imthebest a rejoint le principal en 4,-16",
        "22:08:00.000 WARNING [Imthebest] Imthebest n'a pas suivi le principal (capture : C:\\x.png) : il le rejoindra "
        "par son propre trajet",
    ])
    assert (s.n["decor_different"], s.n["pas_sur_carte_quittee"], s.n["pas_suivi"]) == (1, 1, 1)
    assert s.n["episodes_perdus"] == 2  # revenu entre les deux pertes


def test_combat_tours_sorts_et_refus():
    s = lire(COMBAT)
    assert s.n["combats"] == 1 and s.durees["combat"] == [29.0]
    assert s.combats[0].tours == 2
    assert s.tours_par_perso == {"Gela-[A13]": 1, "Brawli-[Gal]": 1}
    assert s.n["sorts_tentes"] == 3  # « X sur Y : raison » (le plan) n'est pas un lancer
    assert s.sorts_tentes["Maîtrise de l'Arc"] == 1
    assert (s.n["sort_refuse"], s.n["pas_ligne_de_vue"], s.n["non_selectionnable"]) == (1, 1, 1)
    assert (s.n["sorts_partis"], s.n["sorts_rafale"], s.n["refus_tour"], s.n["deplacements"]) == (2, 1, 1, 1)
    assert s.durees["tour"] == [9.5]
    assert s.durees["confirmation_parti"] == [1.2]
    assert (s.n["suite_tour"], s.n["main_pas_passee"]) == (1, 1)
    assert (s.n["bilan_tours"], s.n["bilan_sorts"], s.n["bilan_refus"]) == (2, 2, 1)
    assert s.fenetres["fin de combat"] == 1
    assert s.fin_raison == "1 combat(s), le maximum demandé"


def test_fenetres_securite_et_connexion():
    s = lire(SECURITE)
    assert s.n["menu_principal"] == 2 and s.n["quitter"] == 1
    assert dict(s.fenetres) == {"niveau supérieur": 1}  # la fenêtre « quitter » est comptée à part
    assert (s.n["deconnexion"], s.n["connexion_ok"], s.n["deconnexion_inactivite"], s.n["en_jeu"]) == (1, 1, 1, 1)
    assert s.n["connexion_echec"] == 1
    assert (s.n["erreurs"], s.n["erreur_inattendue"], s.n["exceptions"]) == (2, 1, 1)
    assert s.fin_raison == "erreur inattendue"


def test_passage_de_minuit():
    s = lire(["23:59:59.000 INFO    Mode : trajet", "00:00:05.500 INFO    Monde : Monde des Douze"])
    assert s.duree == pytest.approx(6.5)


def test_periodes_d_apres_les_rapports():
    assert periode_de(datetime(2026, 10, 8, 19, 2)).code == "P0"
    assert periode_de(datetime(2026, 10, 11, 21, 5, 7)).code == "P7"  # avant la détection rapide des soleils
    assert periode_de(datetime(2026, 10, 11, 21, 51)).code == "P8"
    assert periode_de(datetime(2026, 10, 11, 22, 11, 44)).code == "P9"


def test_cumul_indicateurs_et_problemes():
    sessions = [lire(TRAJET_ARRIVE, "a"), lire(TRAJET_ARRETE, "b")]
    total = cumuler(sessions, "cumul")
    assert total.sessions == 2 and total.n["passages_ok"] == 2 and total.n["passages_rates"] == 2
    valeurs = {libelle: valeur for _, libelle, valeur in indicateurs(total)}
    assert valeurs["Changements de carte réussis (principal)"] == "2"
    assert valeurs["Taux de réussite d'un changement de carte"] == "50 %"
    assert valeurs["Victoires / défaites"] == "non mesurable"
    rangs = problemes(sessions)
    assert rangs[0][1] == max(r[1] for r in rangs)
    libelles = {r[0]: r for r in rangs}
    assert libelles["« Toujours sur la même carte après 20 s »"][1:3] == (2, 1)
    assert "Toujours sur la même carte" in libelles["« Toujours sur la même carte après 20 s »"][3]
    assert indicateurs(cumuler([], "vide"))  # aucune session : pas d'erreur


def ecrire_rapport(racine, nom, texte):
    dossier = racine / nom
    dossier.mkdir()
    (dossier / "rapport.txt").write_text(texte, encoding="utf-8")
    return dossier


def test_rapports_des_outils(tmp_path):
    banquier = lire_rapport_outil(ecrire_rapport(tmp_path, "banquier_20261011_183301",
                                                 "Banquier : 0.45 en 1659,383\nBanquier pas trouvé : arrêt (rien envoyé)\n"))
    assert (banquier.outil, banquier.issue) == ("banquier", "échec")
    assert banquier.detail.startswith("Banquier pas trouvé")
    coffre = lire_rapport_outil(ecrire_rapport(tmp_path, "coffre_transfert_20261011_190532",
                                               "Fenêtre « Coffre » : 0.95\nTransfert demandé (voir la capture)\n"))
    assert coffre.issue == "réussi"
    porte = lire_rapport_outil(ecrire_rapport(tmp_path, "entrer_porte_20261011_190635",
                                              "Gela-[A13] : sorti (noir 57% -> 0%)\nImthebest : pas sorti (noir 57% -> 57%)\n"))
    assert porte.issue == "échec" and porte.detail.startswith("1/2 personnages passés")
    (tmp_path / "echange_20261011_184000").mkdir()
    assert lire_rapport_outil(tmp_path / "echange_20261011_184000").issue == "sans rapport"
    assert lire_rapport_outil(tmp_path / "20261011_215010") is None  # dossier de session du bot, pas un outil


def test_dossiers_rapport_complet_et_json(tmp_path, capsys):
    session = tmp_path / "20261011_215010"
    session.mkdir()
    (session / "bot.log").write_text("\n".join(TRAJET_ARRIVE) + "\n", encoding="utf-8")
    outil = ecrire_rapport(tmp_path, "echange_valider_20261011_190037",
                           "Brawli-[Gal] : fenêtre d'échange fermée\nGela-[A13] : fenêtre d'échange fermée\n")
    (tmp_path / "divers").mkdir()
    sessions, outils, ignores = lire_dossiers([session, outil, tmp_path / "divers"])
    assert [s.nom for s in sessions] == ["20261011_215010"] and sessions[0].periode.code == "P8"
    assert outils[0].issue == "réussi" and ignores == ["divers"]
    texte = rapport(sessions, outils, None, ignores)
    for titre in ("## Sessions du bot", "## Par période", "## Trajets du principal", "## Problèmes les plus fréquents",
                  "## Banque, échanges et autres outils", "## Non mesurable aujourd'hui"):
        assert titre in texte
    sortie = tmp_path / "bilan.json"
    assert main([str(session), str(outil), "--sans-carte", "--json", str(sortie)]) == 0
    assert "## Par jour" in capsys.readouterr().out
    donnees = json.loads(sortie.read_text(encoding="utf-8"))
    assert donnees["sessions"][0]["compteurs"]["passages_ok"] == 2
    assert donnees["outils"][0]["issue"] == "réussi"
