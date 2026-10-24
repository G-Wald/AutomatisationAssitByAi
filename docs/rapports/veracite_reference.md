# Véracité de la référence complétée des passages (StarLoco + cartes officielles)

Produit par `tools/reference_complete.py` ; référence locale dans `data/references/` (non versionnée).

## Monde des Douze

- Sorties de bord trouvées dans StarLoco : 16930 (portes et téléporteurs écartés : 839)
- **Contre nos passages vus en jeu** : 511 d'accord, 0 en désaccord ; 1 vus en jeu absents de StarLoco ; 0 sorties que nous avons notées bloquées
- **Cases des soleils retenues en jeu** : 708 sur la case StarLoco, 6 à 1-2 cases, 3 plus loin
- **Contre la carte officielle déchiffrée** (16927 sorties contrôlées) : 16926 praticables (100 %), 16419 avec un objet de soleil (1029, 1030) (97 %)
- **Atteignables selon le côté d'arrivée** (cases praticables officielles) : 39824 liaisons entrée -> sortie atteignables, 534 inatteignables (falaise, rempart) ; contre les blocages notés en jeu selon le côté d'arrivée : 2 retrouvés, 4 non
- Objets les plus fréquents sur les cases de sortie (objet2) : 0 x14813, 1030 x1552, 4088 x473, 1056 x13, 4550 x9, 1352 x8, 959 x6, 1063 x5, 989 x5, 1849 x4, 2114 x4, 4556 x4, 282 x3, 283 x3, 221 x2, 986 x2, 4174 x2, 820 x2, 1695 x2, 220 x1, 551 x1, 253 x1, 204 x1, 289 x1, 1060 x1, 2113 x1, 3381 x1, 3717 x1, 45 x1, 957 x1, 987 x1, 2154 x1, 958 x1, 3654 x1

## Incarnam

- Sorties de bord trouvées dans StarLoco : 194 (portes et téléporteurs écartés : 5)
- **Contre nos passages vus en jeu** : 126 d'accord, 0 en désaccord ; 2 vus en jeu absents de StarLoco ; 1 sorties que nous avons notées bloquées
- **Cases des soleils retenues en jeu** : 94 sur la case StarLoco, 0 à 1-2 cases, 0 plus loin
- **Contre la carte officielle déchiffrée** (194 sorties contrôlées) : 194 praticables (100 %), 158 avec un objet de soleil (1029, 1030) (81 %)
- **Atteignables selon le côté d'arrivée** (cases praticables officielles) : 406 liaisons entrée -> sortie atteignables, 4 inatteignables (falaise, rempart) ; contre les blocages notés en jeu selon le côté d'arrivée : 0 retrouvés, 0 non
- Objets les plus fréquents sur les cases de sortie (objet2) : 1030 x140, 4088 x37, 0 x17


## Contrôle en jeu (24/10, 04:08 → 04:30, `--etapes`)

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
