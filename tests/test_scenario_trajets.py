"""Scénario de trajets du plan de tests (tools/scenario_trajets.py) : étapes enchaînées, commande du bot."""

from pathlib import Path

from tools.scenario_trajets import charger, commande

RACINE = Path(__file__).resolve().parents[1]


def test_session_1_steps_start_where_the_previous_one_arrived():
    scenario = charger(RACINE / "config" / "scenarios" / "session1_deplacements.yaml")
    etapes = scenario["etapes"]
    assert etapes[0]["de"] == scenario["depart"]
    assert all(b["de"] == a["vers"] for a, b in zip(etapes, etapes[1:]))
    assert etapes[-1]["vers"] == scenario["depart"]  # boucle : retour au point de départ
    cmd = commande(scenario, etapes[0])
    assert "--equipe" in cmd and f"--aller={etapes[0]['vers']}" in cmd and f"--position={scenario['depart']}" in cmd
