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
