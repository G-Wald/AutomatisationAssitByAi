# Farm avec un gros DPS (Waldhary-[Gal], Crâ 187) — 07 et 08/11

Demande de l'utilisateur (07/11) : prendre un Crâ de plus de 100 pour farmer plus efficacement ; un mode où l'on
définit les DPS, placés en priorité pour taper, à distance ou au contact selon la classe ; tester d'abord avec un Crâ
qui peut lancer deux Flèches Explosives. 08/11 : farmer les Blops (groupes plus forts, résistances), éviter Tir
Éloigné quand Tir Puissant et Maîtrise de l'Arc suffisent ; un farm efficace se joue en un tour ; proposer des zones.

## Le personnage

- **Waldhary-[Gal]**, Crâ niveau 187, sur le compte de Fourboun (pseudo Waldharyx8), 3e emplacement. Trouvé dans la liste
  des personnages que le serveur envoie à la connexion (`ALK`). Autres Crâs de plus de 100 : Gela-[A13] (113, compte de
  Pesopepe), Cuelotte-[A13] (112, compte de Walitcoin), Walarbalete (108, compte de Fourbodeux).
- D'après le serveur : 11 PA, 5 PM, portée +2 ; Force 174, Intelligence 318, Chance 230, Agilité 101, dommages +34 et
  +33 %.
- **Flèche Explosive** (sort 179) niveau 5 : 4 PA, portée 1 à 8 modifiable, **2 lancers par tour**, dégâts feu,
  zone cercle de rayon 2 (13 cases), environ 133 dégâts moyens avec ses caractéristiques.
- L'équipe : Eldieso (principal, il garde le chemin et les attaques), Waldhary et les 6 autres (niveau 33 à 51).
  Waldhary rejoint le principal, pas l'inverse : un principal de haut niveau emmènerait les autres dans ses zones.

## Ce qui a été fait

- **Mode DPS au placement** (`team.dps`, commit 1f0658d) : « Nom » ou « Nom:rôle », dans l'ordre de priorité ; rôle
  `distance` (à portée de son sort qui porte le plus loin, en ligne de vue, le plus loin possible des ennemis) ou
  `contact` (au plus près d'un ennemi, sans couper la ligne de tir d'un DPS d'avant). Seuls eux bougent ; leur case de
  départ vient du serveur (GIC), les autres sont Prêts tout de suite. `--set "team.dps=Waldhary-[Gal]:distance"`.
- **Barre de sorts au-delà de 14 cases** (commit 3d436bb) : Waldhary a 22 cases d'après le serveur. Les sorts des cases
  15 à 22 faisaient « Emplacement de sort invalide » à chaque tour : aucun tour joué au premier combat. Ils sont
  écartés (dit une fois au journal) : Flèche d'Expiation, Punitive, Destructrice, Absorbante, Persécutrice, Harcelante.
  **À faire par l'utilisateur, s'il le veut** : ranger les sorts utiles dans les 14 premières cases.
- **Gros groupes d'abord** (`engage.groupes=gros`, commit e4e02a7) : le bot attaquait toujours le plus petit groupe
  (un Biblop seul alors qu'un groupe de 8 était sur la carte). `gros` : le plus de monstres, puis le plus haut niveau
  total, toujours sous `max_level`. `petits` reste le défaut.
- **Coffre** (commit 1f0658d) : la clé reprise après une recherche sur son nom (loupe du coffre, « Ensabl » pour la
  Clef du Donjon Ensablé), la pile en première case. Pas encore vu en jeu (aucun passage à la banque depuis).

## Tir Éloigné

La règle demandée existe déjà dans le moteur (11/10) : une suite qui lance Tir Éloigné sans qu'aucune attaque n'ait
besoin de la portée gagnée est écartée. La valeur pour les alliés dans sa zone ne sert qu'à départager (0,05 par
allié). Vu en jeu :
- 23:01:43 (Calanques) : Tir Puissant + 2 Flèches Explosives, sans Tir Éloigné (301 prévus).
- 10:33:42 (Blops) : Tir Puissant + Maîtrise de l'Arc + 2 Flèches Explosives sur 6 ennemis chacune (2 757 prévus).
- Quand il est lancé, c'est qu'un ennemi n'est à portée que grâce à lui (Calanques : monstres loin au premier tour).

## Zones (données du client et cartes StarLoco locales)

Résistance feu (la Flèche Explosive) entre parenthèses ; agressivité d'après `recherche_monstres_agressifs_et_passages.md`.

| Zone | Centre | Monstres | Groupes | Agressifs | Remarque |
|---|---|---|---|---|---|
| Prairie des Blops | -5,-25 | Blops 30-38, Biblops 14-18 ; feu : Griotte 62 %, Coco et Reinette 23 %, Indigo -50 % | jusqu'à 8 | non | testée le 08/11 ; beaucoup de petits groupes |
| Massif de Cania | -18,-26 | Craqueleurs des plaines 39-51 (28 %), Blops | jusqu'à 8 | non | 301 cartes, grande zone |
| Plaines Rocheuses | -14,-46 | Craqueleurs 39-51, Cavaliers Porkass 30-50 (10 %) | jusqu'à 8 | non | |
| Plaine des Porkass | -3,-35 | Bergers 13-25 (0 %), Cavaliers 30-50 (10 %) | jusqu'à 8 | non | déjà farmée par l'équipe |
| Plage de Corail | -54,11 | Crustorails 30-46, Palmifleurs 28-60 (-1 à -11 %), Corailleurs 48-60 | jusqu'à 8 | inconnu | loin |
| Forêt de Kaliptus, Canyon sauvage | -10,10 / -16,10 | Koalaks 40-98 (Immature -32 %, Coco -12 %) | jusqu'à 8 | **oui (2 cases)** | risqué pour les 7 autres |
| Bordure d'Aerdala (Pandala) | 19,-34 | Kitsou Nae 53-61 (-10 %), Pandit, Araknawa | jusqu'à 8 | inconnu | Pandala |

## 08/11 : sessions de farm

- **Prairie des Blops** (journaux 102716 et 103434, `engage.groupes=gros` à partir de 103434) : **46 combats en
  56 min, aucun mort**. Banque au bout de 15 min (Eldieso à 90 % de pods) : les 8 vidés du premier coup, ~9 s chacun.
  Double Flèche Explosive vue plusieurs fois (jusqu'à 2 757 dégâts prévus sur 6 Blops en un tour). Beaucoup de cartes
  n'ont que des groupes de 1 ou 2 Biblops : 7 combats contre 1 monstre, 7 contre 2, 2 contre 3, 8 contre 4, 6 contre 5,
  2 contre 6 (journal 103434).
- **Massif de Cania, Craqueleurs** (journaux 113148 à 114902, `engage.level_ratio=0.5` : groupes de 241 niveaux au
  plus) : **17 combats en ~40 min, aucun mort**, groupes jusqu'à 6 monstres (Craqueleurs 39 à 51 et Blops). Pesopepe le
  plus touché (46 à 66 % après les gros groupes). Jusqu'à 7 min sans groupe attaquable : la zone est très grande.

## 08/11 : ajouts

- **Résistances des monstres** (commit 1bf8ac4) : élément de chaque sort (sa ligne de dégâts la plus forte), modèle et
  grade de chaque monstre d'après le serveur au début du combat, résistances de la langue du client
  (`bot/brain/monsters.py`). Les dégâts prévus par ennemi sont multipliés par (1 - résistance / 100) : Flèche
  Explosive (feu) 47 % sur un Blop Griotte, 162 % sur un Blop Indigo. Ordre des résistances du client (0 neutre,
  1 terre, 2 feu, 3 eau, 4 air) déduit du serveur (élément de chaque coup, « GA;100;lanceur;cible,-dégâts,élément » :
  Explosive en 2, Œil de Taupe et Flèche d'Expiation, eau, en 3) et des Blops (paires opposées). Pas encore comparé à
  des dégâts réels : les Biblops meurent sur le coup, leurs dégâts sont coupés à leurs PV.
- **Repos en mode DPS** (commit 6d08c7d, utilisateur : « il faut vérifier les PV que des DPS, le reste on s'en fiche ») :
  seuls les DPS se reposent avant un combat. Vu en jeu : Pesopepe à 49 %, combat suivant lancé aussitôt.
- **Placement, couleur de l'équipe pas tranchée** (commit fba843f) : personne ne bouge. Une méthode disparue (`_place`)
  faisait une erreur toutes les 2 s pendant le placement ; la session s'était arrêtée sur 5 erreurs.

## Reste à faire

- **Sortir d'un bâtiment** : Waldhary connecté dans une taverne de 5,-17 ; le bot l'a prise pour la banque, puis pour
  l'extérieur, et a cliqué des soleils de bord de carte qui n'existent pas dedans (sorti à la main par le soleil au sol).
- **Sort cru refusé alors qu'il est parti** (11:02, Flèche Magique « PA inchangés ») : l'annonce du serveur est arrivée
  après le sort suivant ; le bot a écrit « case de la barre fausse » (journal seulement, la barre n'a pas changé).
- **Case de placement pas annoncée** une fois (10:33) : déplacement du DPS sans effet, repéré à son premier tour.

## 08/11, après-midi : Massif de Cania (Craqueleurs et Blops), corrections

- **Farm** (journal 122923, `engage.level_ratio=0.6`) : 28 combats en ~55 min, **1 mort** (Waldarch, combat 7, gagné).
  Son énergie est restée à 10 000 : un mort dans un combat gagné ne perd pas d'énergie ; reparti à 1 PV, comme voulu.
- **« Bot muet » à 13:24** : pas un blocage, un calcul. Plan de Waldhary : 7,4 millions de suites, **79 s** (13 PA,
  5 PM, une quinzaine de sorts, 9 ennemis) ; son tour fini avant. Calcul limité à 3 s (`PLAN_BUDGET`, le meilleur plan
  trouvé est gardé, dit au journal) (commit 77a6169).
- **Relance en plein combat** : les sorts n'étaient jamais repris (lus seulement hors combat) ; Waldhary en mode de
  secours (« touche 1, Échap, fin de tour ») pendant 17 min. Repris au premier tour de la mémoire et du serveur, sans
  fenêtre (commit 78a67f5). Vu en jeu : « Sorts repris en combat (29) », double Explosive, combat fini.
- **Sortie des bâtiments** (commit 81be2ea, `bot/brain/building.py`) : bâtiment d'après la carte annoncée par le
  serveur et l'index des cartes du client ; sorties par les soleils au sol de la carte du client (objet1 1030, la
  banque : (8, 26)). Au lancement et au début de chaque trajet. Jamais une salle de donjon.
- **Sortie des bâtiments, vue en jeu** (journaux 135931 et 140049) :
  - taverne de 5,-17 (carte 7573), Waldhary entré avant le lancement : carte pas encore annoncée par le serveur, pièce
    sombre à l'écran, soleil vu au sol en (8, 24), sorti au début du trajet, puis l'équipe rejointe ;
  - banque d'Astrub (7549), même cas : sorti dès le lancement par (8, 26), arrivé sur 7414 (commits b64ec85, 6ca6720).
  - Premier essai (journal 135502) raté : carte inconnue au lancement (le serveur ne la redit qu'au changement de
    carte), d'où la recherche des soleils au sol à l'écran.
