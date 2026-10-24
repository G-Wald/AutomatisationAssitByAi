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
