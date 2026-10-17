"""Résumé court d'une session (tools/resume_session.py) sur des extraits de vrais journaux (tests/fixtures/journaux,
lignes DEBUG et lignes de sorts retirées) et sur des lignes écrites ici, au format de bot.log."""

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tools import bilan_sessions
from tools.resume_session import derniere_session, main, markdown, resumer_dossier, resumer_lignes

JOURNAUX = Path(__file__).parent / "fixtures" / "journaux"
JOUR = date(2026, 10, 16)
EQUIPE = ("10:00:00.000 INFO    Équipe : Koalaloupour, Dinarpepe, Pesopepe ; principal : Koalaloupour")


def lignes(nom: str) -> list[str]:
    return (JOURNAUX / nom).read_text(encoding="utf-8").splitlines()


def resume(nom: str) -> dict:
    return resumer_lignes(lignes(nom), nom, JOUR)


# Extraits de vrais journaux


def test_equipe_de_8_un_combat():
    """16/10 19:37 : 8 personnages, 4 clics à côté du groupe avant le combat, le dernier tour (Vieenjaune) sans ligne
    « Tour : » : les tours et les sorts viennent des lignes « Bilan du combat »."""
    r = resume("equipe_8_combat.log")
    assert r["principal"] == "Koalaloupour" and len(r["equipe"]) == 8
    (combat,) = r["combats"]
    assert combat["duree_placement_s"] == pytest.approx(9.1, abs=0.1)
    assert combat["duree_combat_s"] == pytest.approx(48.3, abs=0.1)
    assert combat["tours"] == 6 and combat["refus"] == 0 and not combat["interrompu"]
    assert combat["rejoindre_max_s"] == pytest.approx(4.5, abs=0.1)
    p = r["personnages"]
    assert p["Vieenjaune"]["tours"] == 1 and p["Vieenjaune"]["sorts"] == {"Flèche Magique": 1}
    assert p["Vieenjaune"]["tour_max"] == pytest.approx(5.6)  # le dernier tour, d'après le bilan
    assert p["Koalaloupour"]["combats"] == 1 and p["Koalaloupour"]["combats_sans_tour"] == 1
    assert p["Pesopepe"]["sorts"] == {"Pelle Fantomatique": 1}
    t = r["temps"]
    assert t["attaques_ratees"] == 4 and t["attaques_ratees_s"] > 10
    avert = {a["message"]: a for a in r["avertissements"]}
    proportions = next(a for m, a in avert.items() if "proportions différentes" in m)
    assert proportions["n"] == 8 and len(proportions["personnages"]) == 8
    assert any("Menu principal" in m for m in avert)
    attaque = next(a for a in r["anomalies"] if a["message"] == "groupe de monstres pas attaqué")
    assert attaque["n"] == 4 and attaque["captures"] == ["0004_Koalaloupour_menu_attaquer_absent.png",
                                                         "0005_Koalaloupour_menu_attaquer_absent.png"]
    assert r["raison_fin"] is None  # extrait coupé après le 1er combat


def test_suiveurs_sans_epee_combat_solo():
    """16/10 19:33 : les 7 suiveurs ne voient pas l'épée et restent dehors (1 min 32 s de placement), le principal
    combat seul : 2 refus, un faux tour, une carte à relief, 2 déplacements taclés."""
    r = resume("suiveurs_sans_epee.log")
    (combat,) = r["combats"]
    assert len(combat["pas_rejoints"]) == 7
    assert combat["duree_placement_s"] == pytest.approx(92.1, abs=0.2)
    assert combat["refus"] == 2 and combat["carte_a_relief"] == 1 and combat["pas_bouge"] == 2
    k = r["personnages"]["Koalaloupour"]
    assert k["tours"] == 7 and k["sorts"] == {"Ronce": 7}  # 9 « lancé », 2 refusés
    assert k["refus"] == 2 and k["temps_refus"] == pytest.approx(9.9, abs=0.1)
    assert k["clics_sans_effet"] == 2 and k["deplacements_sans_bouger"] == 2 and k["pas_bouge"] == 2
    assert k["bloque"] == {"taclé par un ennemi au contact": 2}
    assert k["faux_tours"] == 1 and k["carte_a_relief"] == 1
    assert k["tour_max"] == pytest.approx(10.5)
    assert r["personnages"]["Dinarpepe"]["pas_rejoint"] == 1
    assert r["personnages"]["Dinarpepe"]["epee_introuvable"] == 3
    tr = r["trajets"]
    assert tr["cartes"] == {"6,2": 1} and tr["changements_de_carte"] == 1
    assert tr["duree_changement_moy_s"] == pytest.approx(4.0, abs=0.1)
    assert tr["noms_illisibles"] == 1
    assert r["temps"]["aucun_groupe"] == 2 and r["temps"]["plus_de_groupe"] == 1
    assert r["raison_fin"] is None and "Vie à 39 %" in r["derniere_ligne"]
    epee = next(a for a in r["anomalies"] if a["message"] == "épée du combat introuvable")
    assert epee["n"] == 21 and len(epee["captures"]) == 2
    refus = next(a for a in r["anomalies"] if a["message"].startswith("sort refusé"))
    assert refus["captures"] == ["0054_Koalaloupour_sort_pas_parti.png", "0071_Koalaloupour_sort_pas_parti.png"]
    pas_rejoint = next(a for a in r["avertissements"] if "n'a pas rejoint le combat" in a["message"])
    assert pas_rejoint["n"] == 7 and pas_rejoint["message"].startswith("<perso> n'a pas rejoint")


def test_trajet_et_position_demandee():
    """16/10 19:58 puis 19:59 : nom de carte illisible, « /p %pos% » sans réponse (trajet arrêté), puis lu (6,1)."""
    r = resume("trajet_position.log")
    tr = r["trajets"]
    assert tr["positions_demandees"] == 2 and tr["positions_lues"] == ["6,1"] and tr["positions_ratees"] == 1
    assert tr["captures_positions_ratees"] == ["0003_Koalaloupour_position_chat_illisible.png"]
    assert tr["lances"] == 1 and tr["arrives"] == 1 and len(tr["arretes"]) == 1
    assert "position de départ inconnue" in tr["arretes"][0]
    assert tr["noms_illisibles"] == 3 and tr["cartes"] == {"6,2": 1}
    assert r["combats"] == []
    texte = markdown(r)
    assert "« /p %pos% » : 2 ; lues : 6,1 ; ratées : 1" in texte


@pytest.mark.parametrize("nom", ["equipe_8_combat.log", "suiveurs_sans_epee.log", "trajet_position.log"])
def test_markdown_court(nom):
    texte = markdown(resume(nom))
    assert texte.startswith(f"# Résumé de la session {nom}")
    assert len(texte.splitlines()) < 150
    assert "C:\\Users" not in texte  # captures citées par leur nom seulement


# Lignes écrites ici


def test_cercle_cache_sorties_suiveurs_et_erreurs():
    r = resumer_lignes([
        EQUIPE,
        "10:00:01.000 INFO    [Koalaloupour] Vers la carte du haut : clic sur le soleil en (11, 1)",
        "10:00:05.000 INFO    [Koalaloupour] Nouvelle carte : Incarnam (Champs) 5,1",
        "10:00:05.500 WARNING [Koalaloupour] Sortie du haut inutilisable, je la note bloquée (capture : "
        "C:\\logs\\x\\0001_Koalaloupour_sortie.png)",
        "10:00:06.000 WARNING [Pesopepe] Pesopepe est passé sans le principal (resté sur sa carte) : il le rejoindra "
        "par son trajet",
        "10:00:20.000 INFO    [Pesopepe] Pesopepe a rejoint le principal en 5,1",
        "10:00:21.000 INFO    [Koalaloupour] Vie à 40 % : je m'assois jusqu'à 90 %",
        "10:00:51.000 INFO    [Koalaloupour] Vie à 91 % : je repars",
        "10:01:00.000 INFO    [Koalaloupour] État : placement",
        "10:01:05.000 INFO    [Dinarpepe] État : combat, tour de l'équipe red",
        "10:01:06.000 INFO    [Dinarpepe] Moi : en (3, 4), au centre de la portée de Ronce ; mon cercle est caché",
        "10:01:07.000 ERROR   [Dinarpepe] Lecture impossible pour Dinarpepe (12 essais)",
        "10:01:08.000 ERROR   [Pesopepe] Lecture impossible pour Pesopepe (3 essais)",
        "Traceback (most recent call last):",
        '  File "bot/x.py", line 3, in f',
        "ValueError: case 12 hors de la grille",
        "10:01:30.000 INFO    [Dinarpepe] Combat terminé",
        "10:01:31.000 INFO    [Dinarpepe] Bilan du combat 1, Dinarpepe : 2 tour(s) (8,0 s) ; 3 sort(s) (Lancer de "
        "Pièces x2, Pelle Fantomatique x1) ; 0 refus ; 1 déplacement(s)",
        "10:01:40.000 INFO    [Koalaloupour] Fin de la session : 1 combat(s), le maximum demandé",
    ], "essai", JOUR)
    assert r["personnages"]["Dinarpepe"]["cercle_cache"] == 1
    assert r["personnages"]["Dinarpepe"]["sorts"] == {"Lancer de Pièces": 2, "Pelle Fantomatique": 1}
    assert r["combats"][0]["cercle_cache"] == 1 and r["combats"][0]["tours"] == 2
    tr = r["trajets"]
    assert len(tr["sorties_bloquees"]) == 1 and tr["sorties_bloquees"][0].startswith("5,1 haut")
    assert tr["suiveurs_perdus"] == {"Pesopepe": 1} and tr["regroupes"] == {"Pesopepe": 1}
    assert r["temps"]["repos_s"] == pytest.approx(30.0)
    assert r["temps"]["changements_de_carte_s"] == pytest.approx(4.0)
    assert r["raison_fin"] == "1 combat(s), le maximum demandé"
    avert = r["avertissements"]
    assert avert[0]["niveau"] == "EXCEPTION" and "ValueError: case N hors de la grille" in avert[0]["message"]
    lecture = next(a for a in avert if a["message"] == "Lecture impossible pour <perso> (N essais)")
    assert lecture["n"] == 2 and lecture["niveau"] == "ERROR"
    sortie = next(a for a in avert if a["message"].startswith("Sortie du haut"))
    assert sortie["captures"] == ["0001_Koalaloupour_sortie.png"]


def test_combat_coupe_par_la_fin_du_journal():
    """Pas de « Combat terminé » ni de bilan : le combat est interrompu, ses tours viennent des lignes « Tour : »."""
    r = resumer_lignes([
        EQUIPE,
        "10:00:01.000 INFO    [Koalaloupour] État : placement",
        "10:00:04.000 INFO    [Koalaloupour] État : combat, tour de l'équipe red",
        "10:00:05.000 INFO    [Koalaloupour] Ronce lancé sur l'ennemi en (8, 14)",
        "10:00:06.000 INFO    [Koalaloupour] Tour : 2,0 s ; 1 sort, 1 déplacement, 0 refus ; attentes : fixe",
    ], "coupe", JOUR)
    (combat,) = r["combats"]
    assert combat["interrompu"] and combat["tours"] == 1
    k = r["personnages"]["Koalaloupour"]
    assert k["tours"] == 1 and k["sorts"] == {"Ronce": 1} and k["deplacements"] == 1
    assert "pas de message de fin" in markdown(r)


def _decaler(ligne: str, ecart: timedelta) -> str:
    h = datetime.strptime(ligne[:12], "%H:%M:%S.%f") + ecart
    return h.strftime("%H:%M:%S.%f")[:12] + ligne[12:]


def test_une_heure_de_farm_reste_courte():
    """40 combats (le combat de l'extrait, rejoué toutes les 90 s, passage de minuit compris) : le résumé reste sous les
    150 lignes, le tableau ne garde que les combats les plus remarquables."""
    brutes = lignes("equipe_8_combat.log")
    entete = [l for l in brutes if not re.match(r"^\S+ \w+ +\[", l)]
    corps = [l for l in brutes if re.match(r"^\S+ \w+ +\[", l) and "proportions" not in l]
    base = timedelta(hours=4, minutes=21)
    journal = [_decaler(l, base) for l in entete]
    for i in range(40):
        journal += [_decaler(l, base + i * timedelta(seconds=90)) for l in corps]
    r = resumer_lignes(journal, "heure", JOUR)
    assert len(r["combats"]) == 40
    assert 3500 < r["duree_s"] < 3700
    texte = markdown(r)
    assert len(texte.splitlines()) < 150
    assert "les 12 combats les plus remarquables" in texte
    assert r["personnages"]["Vieenjaune"]["tours"] == 40
    attaque = next(a for a in r["anomalies"] if a["message"] == "groupe de monstres pas attaqué")
    assert attaque["n"] == 160 and len(attaque["captures"]) == 2


# Commande


def _dossier(tmp_path: Path, nom: str = "20261016_193359", extrait: str = "suiveurs_sans_epee.log") -> Path:
    dossier = tmp_path / "logs" / nom
    dossier.mkdir(parents=True)
    (dossier / "bot.log").write_text((JOURNAUX / extrait).read_text(encoding="utf-8"), encoding="utf-8")
    (dossier / "combats.jsonl").write_text('{"combat": 1}\n', encoding="utf-8")
    return dossier


def test_commande_ecrit_markdown_et_json(tmp_path, capsys):
    dossier = _dossier(tmp_path)
    assert main([str(dossier)]) == 0
    md = (dossier / "resume.md").read_text(encoding="utf-8")
    data = json.loads((dossier / "resume.json").read_text(encoding="utf-8"))
    assert md.startswith("# Résumé de la session 20261016_193359")
    assert data["combats_jsonl"] == 1 and data["combats"][0]["duree_placement_s"] == pytest.approx(92.1, abs=0.2)
    assert data["debut"].startswith("2026-10-16T19:33")  # jour lu dans le nom du dossier
    assert "Écrit :" in capsys.readouterr().out


def test_commande_sortie_et_dossier_sans_journal(tmp_path, capsys):
    dossier = _dossier(tmp_path)
    vide = tmp_path / "logs" / "20261016_000000"
    vide.mkdir()
    sortie = tmp_path / "resumes"
    assert main([str(dossier), str(vide), "--sortie", str(sortie)]) == 1
    assert (sortie / "20261016_193359.md").is_file() and (sortie / "20261016_193359.json").is_file()
    assert not (dossier / "resume.md").exists()
    assert "pas de bot.log" in capsys.readouterr().err


def test_derniere_session_et_bilan_resume(tmp_path, capsys):
    ancien = _dossier(tmp_path, "20261016_190000", "trajet_position.log")
    recent = _dossier(tmp_path, "20261016_193359")
    (tmp_path / "logs" / "comparer_carte_20261016_235959").mkdir()  # dossier d'outil : ignoré
    assert derniere_session(tmp_path / "logs") == [recent]
    assert bilan_sessions.main(["--resume", str(ancien), "--sans-carte"]) == 0
    assert (ancien / "resume.md").is_file() and not (recent / "resume.md").exists()
    assert resumer_dossier(tmp_path / "logs" / "comparer_carte_20261016_235959") is None
