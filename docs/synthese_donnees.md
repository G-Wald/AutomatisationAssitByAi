# Synthèse : ce que les fichiers du jeu et les analyses du 12/10 apportent au bot

Ce document relie les rapports du 12/10 :

- `docs/rapports/comparaison_reference.md` : référence corrigée d'après le client ;
- `docs/rapports/cartes_client.md` et `cles_cartes.md` : fichiers du client, index en clair ;
- `docs/rapports/bilan_sessions.md` : chiffres des sessions passées ;
- `docs/rapports/donjons.md` : clés, donjons ;
- `docs/rapports/metiers_ressources.md` : métiers, repérage des ressources.

Il dit, domaine par domaine, à quoi servent ces données.

## 1. D'où viennent les données, et ce qu'elles valent

| Source | Contenu | Fiabilité | Où |
|---|---|---|---|
| Langue des cartes du client (MA) | 9306 cartes : coordonnées, zone, intérieur ou extérieur, cases de placement en combat (p1/p2), soleils « conditionnels » (case exacte), portes de maisons (case exacte) | Officielle (client Retro 1.49.6) | `data/client_retro/cartes/` (`tools/cartes_index.py`) |
| En-têtes des cartes (SWF) | Taille, fond, musique, droits (agression, objets…) | Officielle | idem |
| Autres langues du client | Donjons, 47 clés et recettes, monstres (niveaux, grades), PNJ, dialogues, métiers (actions, pods), 57 gardiens de ressources, messages du chat, catalogue des objets interactifs (dessin → métier) | Officielle | `tools/langue_client.py`, `config/donjons.yaml` |
| Dessins des objets du client | Icônes vectorielles (clés, ressources) : rendu local possible | Officielle | essai : `data/donjons_essai/` |
| Référence du Monde des Douze | Cartes extérieures exactes, noms de zone exacts (corrigés d'après le client le 12/10), ressources par carte (solomonk.fr, 28/09), monstres par zone et donjons (xixou.io, 28/09) | Cartes et zones : officielles. Ressources et monstres : sites tiers | `config/monde/` |
| Mémoire apprise en jeu | Soleils par côté d'arrivée, murs et remparts, sorties bloquées | Observée, contrôlable (`tools/memoire_cartes.py --controle`) | `data/carte.json` |
| Captures et journaux | Écrans réels (repérage des ressources, combats), chiffres de chaque session | Observés | `logs/` (tournant), `data/bilans/` (gardé) |

**Ce qu'on n'a pas** : les cases praticables, les murs, la ligne de vue, et la position des soleils et des objets
interactifs. Tout cela est dans les données chiffrées des cartes. Le déchiffrement en masse a été refusé (contrôle des
permissions, puis l'agent, puis moi), et un seul essai a été fait, sur -1,-19. Ces informations restent donc
**apprises en jeu**, ce qui marche bien pour les trajets depuis le 11/10 au soir.

**Pourquoi le catalogue des objets interactifs n'a pas de positions.** Le jeu sépare deux choses :

- **ce qu'est chaque dessin** (« le dessin n° 7500 est un Frêne, métier Bûcheron ») : une table de la langue, en clair ;
- **quel dessin est posé sur quelle case** de quelle carte : il est dans les données de cases de chaque carte,
  chiffrées.

Les portes de maisons et les soleils conditionnels sont l'exception : la langue donne aussi leur case.

## 2. Ce que les analyses disent ensemble

- **Soleils.**
  - La langue donne la case exacte de soleils sur 48 cartes autour d'Astrub. Ceux que le bot avait vus en jeu sont
    pile à la case annoncée : -3,-18, -3,-16, -2,-23, -2,-13, -1,-13, 0,-13, 1,-13, -5,-20.
  - Les autres soleils, et les deux soleils d'un côté des cartes coupées par un rempart, s'apprennent en jeu. Depuis le
    11/10, le bot retient le bon soleil selon le côté d'arrivée, et un mur se voit en 3 à 6 s.
- **Même coordonnées, cartes différentes.**
  - 1053 coordonnées ont plusieurs cartes : 4,-16 en a 6 (dehors, banque, maisons), et les salles d'un donjon portent
    toutes les coordonnées de l'entrée.
  - La mémoire du bot, rangée par coordonnées, mélangerait ces cartes. C'est le point dur des donjons et des
    intérieurs : il faudra reconnaître la carte au décor, ce que le bot sait déjà faire (`same_map`).
- **Noms de carte illisibles** : 20 % des arrivées dans la cité.
  - Avant, rien n'était appris sur ces cartes : la sortie du haut de 4,-22 était redécouverte à chaque trajet.
  - Et 27 % des arrivées de suiveurs n'étaient pas vérifiées.
  - Corrigé le 12/10 : voir la section 3.
- **Combat.**
  - Les cases de placement des deux équipes sont connues pour 9090 cartes, avant même le combat.
  - Les niveaux et grades des monstres, des gardiens et des boss sont dans la langue.
  - La ligne de vue et les obstacles restent lus à l'écran.
- **Banque.**
  - Les clés sont rangées avec les ressources, donc « Transférer tous les objets » les envoie au coffre.
  - Le coffre a une recherche par nom : le jeu écrit « Clef … ».
  - Le coffre n'est accessible qu'au-dessus du niveau 10 : Imthebest (niveau 6) passe par des échanges.
  - Points faibles des outils de banque (11/10) : banquier trouvé 2 fois sur 4, bouton « max » 0 sur 2.
- **Métiers.**
  - Le repérage par modèle d'image, au centre des cases, trouve 76 % des Frênes et 73 % des Châtaigniers, sans fausse
    détection. Une souche montre qu'un arbre est déjà coupé (repousse en plus de 8 min).
  - La référence dit quelles ressources et combien il y en a par carte (solomonk).
  - Le catalogue du client relie chaque dessin à son métier.
  - Pods par unité : bois 5, minerai 5, céréale 2, fleur 1. Gardiens : environ 1 récolte sur 40.
- **Mesure.**
  - Les combats de l'après-midi du 11/10 ont été effacés par la rotation des journaux : pas de chiffres de départ pour
    le combat.
  - Depuis le 12/10, le bilan chiffré de chaque lancement est gardé dans `data/bilans/`.

## 3. Ce qu'on en fait, domaine par domaine

| Domaine | Donnée | Usage dans le bot | État |
|---|---|---|---|
| Déplacements | Soleils dont le client donne la case | Cliqués directement, sans recherche ni touche A ; recherche à l'écran si raté | **Fait le 12/10** (atlas : `client_suns`) |
| Déplacements | Nom illisible mais chemin sûr | Apprentissage jusqu'à 2 cartes illisibles d'affilée après une carte lue, sans saut (4,-22) | **Fait le 12/10** |
| Déplacements | Décor des suiveurs | Suiveur arrivé sur une carte au nom illisible : vérifié au décor du principal ; sinon perdu, regroupé | **Fait le 12/10** |
| Déplacements | Référence corrigée | Plus de fausses voisines (20 cartes retirées), zones exactes | Fait le 12/10 |
| Déplacements | Plusieurs cartes par coordonnée | Savoir qu'on est entré dans un bâtiment (banque) ou une salle de donjon | À faire, avec la banque et les donjons |
| Déplacements | Portes de maisons (case) | Ne pas confondre une porte avec une sortie ; entrer dans un bâtiment voulu | Plus tard |
| Combat | Placements p1/p2 | Savoir de quel côté on commence, choisir sa case : Crâs loin, Iop près | À faire (après la session 2 du plan) |
| Combat | Niveaux et grades des monstres | Estimer la difficulté d'un groupe avant d'attaquer ; gardiens, boss | À faire |
| Combat | Ligne de vue | Lue à l'écran (P14, test C5) | Pas de donnée du client |
| Banque | Recherche du coffre, nom exact des clés | Tout déposer, filtrer « Clef … », reprendre les clés (B11, B12) | À faire : taper du texte en arrière-plan, à essayer |
| Banque | Niveau minimum du coffre | Imthebest : clés et ressources par échange (B13), ou le remplacer | Décision de l'utilisateur |
| Donjons | Clés, recettes, entrées, gardiens, monstres | Données de `config/donjons.yaml` ; boucle de `donjons.md` (répartition des clés au départ) | Conçu ; sessions plus tard |
| Donjons | Icônes des clés | Repérer les clés dans l'inventaire ou le coffre | Rendu local essayé ; à comparer à une capture |
| Métiers | Modèles d'image, catalogue, quantités par carte | Repérer les arbres (76 %), choisir les cartes riches, ignorer les souches | Essai hors jeu fait ; captures à demander |
| Métiers | Gardiens (57) | Combat pendant la récolte, puis reprise | Conçu ; rejoindre en groupe : à tester |
| Mesure | Bilans, scénarios fixes | Comparer les sessions (plan de tests) | **Fait le 12/10** |

## 4. Ordre proposé

1. **Session 1 du plan : déplacements**, avec les nouveautés ci-dessus. Prête :
   `docs/sessions/session_1_deplacements.md`.
2. **Session 2 : combat de base** en forêt, avec les premiers chiffres de combat (aucune base aujourd'hui), puis les
   placements p1/p2.
3. **Banque intégrée au bot** (pods, trajet, banquier, coffre, échanges). C'est le passage obligé pour les donjons et la
   récolte.
4. **Récolte**, après les captures demandées.
5. **Donjon des Champs.**

## 5. Points ouverts pour l'utilisateur

- **xixou.io** : ses conditions d'utilisation (23/08/2026) interdisent la récupération massive par script, alors que
  la référence du 28/09 a été importée par script. Garder ou non les monstres par zone et les donjons venus de ce
  site ?
- Remplacer Imthebest (niveau 6, pas de coffre) par un personnage de niveau supérieur à 10 ?
- Les captures listées dans `donjons.md` (5.4) et `metiers_ressources.md` (5.3).
