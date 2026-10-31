# Plan de tests croisés en jeu (31/10)

Chaque scénario enchaîne plusieurs fonctions et provoque un imprévu. Réussite = d'après le serveur (journal), pas
l'écran. Toujours : jamais d'arrêt en plein combat ; `--lecture memoire` ; avec `--connexion`, `--personnages`.

## Préparation (une fois)
- Relever le sac et les pods exacts : reconnexion (ASK + Ow écrits aussitôt dans data/possessions).
- Noter les clés (1568) de chacun et le seuil `team.pods_banque`.

## Scénarios
| # | Enchaînement | Imprévu provoqué | Attendu (réseau) |
|---|---|---|---|
| 1 | Connexion 8 → répartition des clés → entrée Bouftou → 10 salles → sortie | aucun (référence) | 8 DQ « clé », 10 victoires, GDM 1856 pour les 8, familier (OAKO) |
| 2 | Comme 1 | 7 clés pour 8 | aucune entrée, message « pas assez de clés » (fenêtre de l'interface) |
| 3 | Comme 1 | une clé des Champs dans le sac de Fourboun | modèle 1568 posé (EmKO), jamais 8143 |
| 4 | Pods ≥ seuil chez un suiveur, équipe devant Altaïr | surcharge (au-dessus du max) chez un autre | échanges de rééquilibrage (Ow ↓), aller banque (zaap), 8 coffres, retour, clés réparties |
| 5 | Zone Le berceau (--combat-zone 2,-34) jusqu'au seuil de pods | un menu de joueur laissé ouvert avant le zaap | menu refermé sans Échap, zaap pris (GDM 4,-19), pas de Menu principal |
| 6 | Trajet Astrub → Berceau | joueur debout sur le zaap (attendre un passant) | arche visée, touche E, téléportation ; sinon message clair |
| 7 | Donjon, combat d'une salle | un suiveur loin de l'épée (déplacé à la main avant) | GJK de sa fenêtre ou refus GA;903 au journal ; s'il reste dehors : arrêt + message, personne coincé |
| 8 | Donjon, fin de combat | un K.O. pendant le boss | fin décidée par GE (pas l'écran), salle comptée une fois |
| 9 | Équipement : faux puis marteau (Enu-six) | recherche du sac déjà ouverte avec un autre texte | modèle confirmé par OM (8540 puis 2416), rien d'autre consommé |
| 10 | Récolte (blé) après équipement de l'outil | popup « métier niveau » pendant la récolte | IQ/GDF entendus, popup fermé, outil remis |
| 11 | Reprise du bot en plein donjon (`bouftou:salles:N`) | bot relancé sans reconnexion | sac et pods repris des fichiers, salle d'après le numéro de carte |
| 12 | Banque mode « principal » | pods du principal presque pleins | dépôt avant le lot suivant, échanges vérifiés (EVa) |

## Croisements à faire en une session (ordre conseillé)
1 → 4 → 1 (après banque) → 9 → 10 → 5 → 2 : couvre clés, pods, banque, zaap, équipement, récolte et le refus d'entrée.

## À relever à chaque scénario
- Durée, échecs écran rattrapés par le réseau, messages « À l'utilisateur », captures gardées.
- Tout Échap pressé derrière un menu (ne doit plus arriver) et tout clic dans le menu d'un autre joueur (jamais).
