# Contrôle en jeu de la référence complétée

Rapport tenu à la main (`tools/reference_complete.py` réécrit `veracite_reference.md` à chaque passage).

## Premier contrôle (24/10, 04:08 → 04:30, `--etapes`)

| Passage douteux | Avant | En jeu |
|---|---|---|
| 0,-23 gauche, 0,-26 droite, 0,-14 haut, 12,-22 haut | appris « bloquée », référence : passage | **passés** : l'apprentissage se trompait |
| -1,-21 gauche | appris « bloquée » | passé en arrivant par le haut ; depuis le bas, inatteignable (rempart), comme calculé |
| -1,-21 bas en venant du haut | référence : inatteignable | confirmé (le bot l'a essayé 25 fois avant le correctif) |
| -5,-16 droite | vu en jeu, absent de StarLoco | passé : StarLoco n'est pas complet partout |
| -1,-24 droite | référence : passage | raté une fois (personnage immobile), passé ensuite |
| 9,-20 haut | référence et déjà pris en jeu | contours de A pas vus : erreur de perception du bot (corrigée : case de la référence essayée d'abord) |

66 traversées distinctes réussies, dont 16 jamais vues avant ; 11 étapes sur 11. Après coup : 511 passages vus en jeu,
511 d'accord avec la référence, aucune sortie vue bloquée qu'elle donne praticable. Restent à voir : les 3 blocages
de la Plaine des Porkass (probablement faux, notés quand l'attente était trop courte) et 0,-14 en venant de la droite.

## Second contrôle (24/10, 05:09 → 05:50, `--etapes`)

| Passage douteux | Avant | En jeu |
|---|---|---|
| -1,-38 bas en venant du haut | appris « inatteignable » | **passé** du premier coup |
| -2,-39 gauche en venant du bas | appris « inatteignable » | **passé** du premier coup |
| -3,-39 gauche en venant du bas | appris « inatteignable » | **passé** du premier coup |
| 0,-14 gauche en venant de 1,-14 | référence : joignable depuis la droite | raté : le bot avait pris en 1,-14 le soleil gauche du **bas** (1,29), qui mène en 0,-14 sur la case (26,30), sous le rempart ; d'où la sortie gauche est hors d'atteinte. La référence le dit maintenant par case d'arrivée (`arrivees`, `depuis_arrivee`) et l'itinéraire choisit le soleil du haut (1,9) |

Ce qui faussait l'apprentissage dans la Plaine des Porkass : d'une carte à la voisine le décor change si peu que l'écart
reste sous le seuil « autre carte ». Le principal arrêté était pris pour bloqué (sorties notées inatteignables alors que
l'équipe était passée), les suiveurs crus « toujours sur la carte de départ » étaient recliqués sur la nouvelle carte et
partaient une ou deux cartes trop loin (1 à 1,5 min de regroupement par carte). Corrigé : les coordonnées lues
tranchent avant de conclure (principal, suiveurs, regroupement). Après correctif : ~10 s par carte, les 7 suiveurs
passent au premier clic.

Faux blocages appris retirés de `data/carte.json` (sauvegarde dans `%TEMP%\carte_avant_24-10_nettoyage.json`) : les
7 cartes de la Plaine des Porkass ci-dessus, 0,-14 en venant de la droite, 9,-20 haut.

## Contrôle de la référence de combat et d'un zaap (24/10, 09:00 → 12:37)

**Placements de combat** (cases lues au placement, comparées aux placements StarLoco de la carte reconnue) :

| Carte | Reconnue | Cases lues dans la référence |
|---|---|---|
| 4,-20 (cité d'Astrub), 4 combats | au terrain, puis au placement | 15 sur 15 à chaque fois (16 dans la référence ; une cachée) |
| -4,-30, -5,-30, -5,-31, -5,-32 (x2), -5,-34 (x2), -5,-36, -5,-37 (Porkass) | au placement | 16 sur 16 (15 sur 15 une fois) |

Aucune case de placement lue hors de la référence ; chaque carte reconnue est celle où le bot se savait (coordonnées
du trajet). Session des Porkass : 11 combats, 11 placements contrôlés, aucun mort.

**Zaap d'Astrub (4,-19)** : la case de la référence, (14, 20), tombe au pied du zaap à l'écran. Clic depuis
l'arrière-plan sans effet (ni sur la case ni sur le portail) : la fenêtre des destinations ne s'est pas ouverte ; à
revoir pour utiliser les zaaps (premier plan, ou menu de l'objet). Le zaap de 1,-32 (Le berceau) n'a pas été visité
(Bouftous d'Halouine agressifs).

**Trouvé en chemin** :
- équipe transformée en citrouilles (Halloween) : cercles tous pareils, portée cachée sous les alliés ; repérage par
  un pas vers l'ennemi, puis sort lancé sur un ennemi sans connaître sa case (le jeu tranche, PA vérifiés) ; le combat
  bloqué depuis 50 min s'est fini ;
- `--combat-zone` donné hors de la zone : le bot restait sur place ; il fait maintenant le trajet, sans combat en
  chemin (position demandée au jeu si le nom de carte est illisible).

## Heure de tests avant le merge (24/10, 15:05 → 16:10)

| Zone | Combats | Morts | Placements contrôlés | Hors référence |
|---|---|---|---|---|
| Prairie des Blops (-4,-27, -4,-25, -4,-24) | 7 | 0 | 4 | 0 |
| Orée de la forêt des Abraknydes (-4,-19, -6,-19, -7,-20) | 4 gagnés + 1 en cours | 0 | 4 (dont 12 sur 12 en -7,-20) | 0 |

Corrigé pendant l'heure : la reconnaissance des cartes de plaine (avance relative sur la suivante), une fausse
transformation (barre de sorts relue juste après un refus), les PM illisibles qui empêchaient le repérage par un pas.

Reste faible : les combats où l'équipe est transformée par les monstres d'Halouine (Abraknyde d'Halouine à l'Orée,
le 24/10 à 15:48) : portraits de la timeline tous pareils (le tour d'un personnage pris pour celui d'un autre),
cercles pareils, pas sans effet ; le combat traîne (aucun mort, monstres faibles). Le zaap ne s'utilise pas encore
depuis l'arrière-plan.

## Deux heures de tests de la branche « la référence fait foi » (25/10)

| Zone | Durée | Combats (tous gagnés) | Cartes reconnues | Placements conformes | Anomalies de trajet | Morts |
|---|---|---|---|---|---|---|
| Prairie des Blops (-3,-23 → -3,-27) | 11:50 → 12:54 | 10 | 10 | 10 / 10 | 0 | 1 (Koalaloupour, combat 9) |
| Plaine des Porkass (-3,-31 → 0,-38) | 12:55 → 13:58 | 23 | 23 | 23 / 23 | 0 | 1 (Pombro, combat 3) |

Sorts aux Porkass : 243 lancés, 10 refus, 4 clics sans effet. Aucun passage officiel raté (donc rien dans
`data/anomalies_passages.jsonl`), aucune carte du jeu changée depuis la construction des références.

Corrigé : une fenêtre d'échange proposée par un autre joueur (ouverte à la connexion) bloquait le départ ; elle est
maintenant refusée par Échap. Observé, à revoir : le Sadida (Koalaloupour) avance en première ligne pour avoir la ligne
de vue de Ronce ; des lectures de portée suspectes (« 0 à 10 au lieu de 0 à 8 ») donnent une ligne de vue prédite
juste à 40-90 % sur certaines cartes plates, le bot se rabat alors sur la vérification par le sort.
