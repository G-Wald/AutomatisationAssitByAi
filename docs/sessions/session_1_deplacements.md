# Session 1 du plan de tests : déplacements autour d'Astrub et dans la cité

Prévue pour 45 minutes, banc A. Tests D1 à D5 et D8 du plan (`docs/plan_de_tests.md`). On y vérifie aussi les
nouveautés du 12/10 (`docs/synthese_donnees.md`, section 3).

## Ce qu'on vérifie en plus des tests du plan

| Nouveauté | Où on la voit | Réussi si |
|---|---|---|
| Soleils dont le client donne la case | Contour ouest et rangée -13 (ex. -1,-13 bas (15,31), -2,-13 bas (13,31)), étapes 15, 16 et 31 | Clic direct, sans « Sorties avec la touche A » sur ces côtés |
| Apprentissage sur nom illisible | 4,-22 haut (étapes 29 et 33), 4,-16, 2,-16, 3,-16… | 4,-22 haut essayé au plus une fois dans toute la session ; plus de « bloquée pour ce trajet seulement : carte pas sûre » à moins de 2 cartes d'une carte lue |
| Suiveurs vérifiés au décor | Toute arrivée sur un nom illisible | « a suivi le principal (nom de carte illisible, même décor) » ; plus aucun « a changé de carte (nom illisible) » sans vérification |
| Bon soleil sur une carte coupée par un rempart | 2,-14 et 1,-14 (gauche), -1,-19 (bas), 5,-19 (droite), 4,-19 (haut) | 1er passage ≤ 15 s ; au 2e passage (étapes 20 et 29), aucun mauvais soleil essayé |

## Avant de commencer

1. Les 4 personnages sont connectés, groupés en **1,-27** (forêt d'Astrub), avec PV pleins et pods sous 50 %.
   S'ils sont déconnectés : `.venv\Scripts\python -m bot.connection --arriere-plan`.
2. Aucune fenêtre ni aucun menu ouvert dans le jeu, et les fenêtres du jeu non réduites.
3. État de la mémoire : `.venv\Scripts\python -m tools.memoire_cartes --sauvegarder --controle` (attendu : 0 point à
   revoir).
4. Vérifier le scénario sans rien lancer : `.venv\Scripts\python -m tools.scenario_trajets
   config/scenarios/session1_deplacements.yaml --a-sec`.

## Lancement

```
.venv\Scripts\python -m tools.scenario_trajets config/scenarios/session1_deplacements.yaml
```

- 33 trajets enchaînés, chacun partant de l'arrivée du précédent ; le scénario boucle et revient en 1,-27.
- Une étape ratée arrête le scénario. On la note, puis on reprend avec `--depuis N`, au besoin après avoir regroupé
  l'équipe à la main (intervention notée).
- L'étape 13 fait d'abord oublier à la mémoire 2,-14, 1,-14, 0,-14, -1,-19, 5,-19 et 4,-19 (test D2 « mémoire vide »),
  avec une copie de sauvegarde automatique.
- F12 arrête le bot à tout moment.

## Après

1. Résultat de chaque étape : affiché à la fin, et gardé dans `data/bilans/scenario_session1_deplacements_<date>.json`.
2. Bilan chiffré de la session : `.venv\Scripts\python -m tools.bilan_sessions logs\<dossiers de la session>`. Le
   bilan de chaque lancement est aussi gardé automatiquement dans `data/bilans/`.
3. D8 : `.venv\Scripts\python -m tools.memoire_cartes --controle` (attendu : 0 point à revoir).
4. Remplir la fiche ci-dessous, puis classer chaque échec : bot, jeu ou environnement.

## Fiche de résultats

| Test | Étapes | Mesure | Seuil (plan, section 2) | Référence 11/10 soir | Résultat |
|---|---|---|---|---|---|
| D1 | 6 à 12 | passages réussis, temps par carte | 8/8, ≤ 8 s | — | |
| D2 | 13, 17, 20, 25, 29 | 1er passage, puis mauvais soleils au 2e passage | ≤ 15 s, 0 | 14 s (2,-14) | |
| D3 | 14 | mur de 0,-14 vu, contourné | ≤ 7 s, contourné | 6,5 s par essai | |
| D4 | 2 à 4, 15, 16, 18, 19, 23, 24, 26, 27 | portes franchies | 10/10 | 10/10 | |
| D5 | 1, 28 à 33 | arrivés, durée, suiveurs perdus | 7/7, ≤ référence + 10 %, 0 perdu non regroupé | 57 à 75 s par trajet | |
| D8 | après | incohérences de la mémoire | 0 | 0 (12/10) | |
| Nouveautés | toute la session | voir le premier tableau | | | |
| Global | toute la session | changements de carte réussis, temps moyen par carte, interventions | ≥ 98 %, ≤ 8 s, 0 | 97 %, 7,3 s | |
