"""Bilans chiffrés gardés dans data/bilans/ (bot/debug/bilans.py) : les journaux de logs/ tournent, pas les chiffres."""

from bot.debug.bilans import archive, archive_missing

JOURNAL = """21:04:41.053 INFO    [Gela-[A13]] Trajet vers -2,-20 (Monde des Douze), sans combat ; départ : -1,-19 (Astrub (Cité d'Astrub) -1,-19)
21:04:45.120 INFO    [Gela-[A13]] Trajet : côté haut, vers -1,-20 (encore 2 carte(s) jusqu'à -2,-20)
21:04:49.900 INFO    [Gela-[A13]] Nouvelle carte : Astrub (Cité d'Astrub) -1,-20
"""


def test_summary_written_once_per_session_and_kept(tmp_path):
    logs, out = tmp_path / "logs", tmp_path / "bilans"
    session = logs / "20261011_210441"
    session.mkdir(parents=True)
    (session / "bot.log").write_text(JOURNAL, encoding="utf-8")
    (logs / "20261011_220000").mkdir()  # lancement en cours : résumé à sa fin seulement
    (logs / "vide").mkdir()  # ni journal ni rapport d'outil : rien
    written = archive_missing(logs, current=logs / "20261011_220000", out=out)
    assert [p.name for p in written] == ["20261011_210441.json"]
    assert archive_missing(logs, current=logs / "20261011_220000", out=out) == []  # déjà gardé
    assert "sessions" in (out / "20261011_210441.json").read_text(encoding="utf-8")


def test_summary_failure_never_stops_the_bot(tmp_path):
    assert archive(tmp_path / "absent", out=tmp_path / "bilans") is None
