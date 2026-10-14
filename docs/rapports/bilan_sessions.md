# Bilan chiffré des sessions passées : base de départ du plan de tests (12/10)

Chiffres produits par `tools/bilan_sessions.py` sur tous les dossiers de `logs/` (lecture seule) :

```
.venv\Scripts\python -m tools.bilan_sessions                        tous les dossiers de logs/
.venv\Scripts\python -m tools.bilan_sessions logs\20261012_xxxxxx    une session de test
.venv\Scripts\python -m tools.bilan_sessions --json bilan.json       et les chiffres bruts en JSON
```

L'outil reconnaît les messages écrits par le code (travel.py, team/loop.py, combat.py, turns.py, popups.py,
connection/, debug/report.py), anciens formats compris, et lit `combats.jsonl` et les `rapport.txt` des outils.

**Légende.** **Mesuré** : compté par l'outil dans les journaux encore présents. **Rapporté** : écrit dans
`plan_tests_combat.md` ou `plan_banque.md`, journal effacé, impossible à recalculer. **Supposé** : déduction, dite
comme telle.

## 1. Ce que les journaux permettent de dire, et ce qu'ils ne permettent plus

- **Il reste 20 journaux du bot, tous du 11/10 entre 21:04 et 22:13** (22,8 min en tout) : 18 trajets (`--aller`,
  toute l'équipe) et 2 connexions seules. **Aucun combat.**
- **La rotation des journaux a effacé tout le reste** (`logs.keep_sessions: 20`). Sont partis : les 4 sessions de
  combat du 11/10 (15:43 à 17:52, 36 combats rapportés) avec leurs `combats.jsonl`, l'essai de trajet vers la banque
  (17:47) et les trajets de 19:04 à 21:01. Les journaux restants citent 20 sessions effacées (de 19:54 à 21:01, ligne
  « Anciennes sessions supprimées »). Celles d'avant ne laissent aucune trace.
- **29 rapports d'outils** sont restés : banque, échanges, porte, essais d'arrière-plan (08/10 et 11/10).
- **Conséquence : on ne peut pas dire aujourd'hui, chiffres à l'appui, si les combats se sont améliorés.** Seuls les
  trajets ont un avant / après mesurable, et sur un petit échantillon : 2 journaux avant, 16 après.
- **Contrôle de l'outil.** Les durées de trajet qu'il calcule recoupent le tableau de `plan_banque.md` (« Choix du
  soleil… ») : 14 s, 56 s (58 rapportées), 27 s (28), 64 s, 22 s, 70 s, 74 s (75), 75 s, 71 s, 53 s (52) et 57 s.
  Le nombre de cartes diffère sur deux lignes. Pour 4,-13 → 5,-23, le journal dit « 11 changement(s) de carte » et le
  rapport 12. Pour les trois trajets 4,-16 ↔ 1,-27, le journal dit 16, 16 et 16, le rapport 15, 17 et 15.

## 2. Périodes

Une session est rangée selon l'heure de son lancement (nom du dossier). Les débuts de période viennent des rapports.

| Code | Début | Changé juste avant (d'après les rapports) | Journaux du bot | Rapports d'outils |
|---|---|---|---|---|
| P0 | avant le 11/10 15:40 | premiers tests (dont 11/10 15:05, lecture des sorts) | 0 | 10 (08/10, arrière-plan) |
| P1 | 11/10 15:43 | combats S1 : liste des sorts défilée par le rail, souris écartée après chaque lancer | 0 | 1 |
| P2 | 11/10 16:09 | combats S2 : refus noté dès le 1er lancer sans effet, portrait à 1 px près, obstacles entre portée min. et max. | 0 | 0 |
| P3 | 11/10 16:49 | combats S3 : tour = timer OU portrait, 2e rangée au pavé numérique, épée de ralliement | 0 | 0 |
| P4 | 11/10 17:32 | combats S4 (reconnexion automatique) ; 17:47, 1er trajet vers la banque | 0 | 0 |
| P5 | 11/10 18:23 | banque et échanges (outils) ; vers 19:00, Menu principal refermé par Échap, « quitter » -> Non, équipe groupée en trajet | 0 | 18 |
| P6 | 11/10 20:13 | modèle du chiffre 9 (cité d'Astrub) | 0 | 0 |
| P7 | 11/10 20:46 | **avant** la détection rapide : soleil inatteignable = 3 essais de 20 s | 2 (21:04, 21:05) | 0 |
| P8 | 11/10 21:50 | **détection rapide** (immobile 2 s), tous les soleils d'un côté, sortie bloquée selon le côté d'arrivée | 17 (dont 2 connexions) | 0 |
| P9 | 11/10 ~22:08 (supposé) | reclic d'un suiveur seulement s'il est immobile | 1 (22:11) | 0 |

- **P7 / P8 vérifié dans les journaux (mesuré).** Les deux journaux de P7 ont les anciens messages : « Toujours sur la
  même carte après 20 s (essai 1/3) » et « Soleil du droite retrouvé à l'écran en … ». Ceux de P8 ont les lignes
  « Immobile … s après le clic ».
- **Début de P9 supposé.** D'après le rapport, Imthebest a été séparé une fois, puis c'est « corrigé, puis revérifié ».
  La correction est donc entre le trajet de 22:05 et celui de 22:11.
- **Par jour.** Tous les journaux du bot sont du 11/10 : le cumul du tableau 3 est le bilan de ce jour. Le 08/10 n'a
  que des essais d'arrière-plan (outils).

## 3. Trajets par période (mesuré)

| Indicateur | P7 (2 journaux) | P8 (17) | P9 (1) | Cumul 11/10 (20) |
|---|---|---|---|---|
| Durée cumulée des journaux | 6,7 min | 14,4 min | 1,7 min | 22,8 min |
| Trajets du principal : lancés / arrivés / arrêtés | 2 / 1 / 1 | 15 / 15 / 0 | 1 / 1 / 0 | 18 / 17 / 1 |
| Raison d'arrêt | 5 changements de carte ratés d'affilée | – | – | |
| Changements de carte réussis / ratés (principal) | 7 / 5 | 115 / 4 | 16 / 1 | 138 / 10 |
| **Taux de réussite d'un changement de carte** | **58 %** | **97 %** | **94 %** | 93 % |
| Changement de carte réussi (« Trajet : côté » → « Nouvelle carte »), moyenne (médiane) | 5,9 s (5,9) | 6,1 s (5,9) | 5,5 s (5,5) | 6,0 s (5,8) |
| **Temps par carte, trajets arrivés** (suiveurs compris) | 7,8 s (1 trajet de 2 cartes) | **7,3 s** | 6,3 s | 7,2 s |
| **Temps perdu dans les changements ratés** | **320 s** (5 ratés) | **21 s** (4) | 1 s (1) | 342 s |
| « Toujours sur la même carte après 20 s » : nombre ; délai du verdict | 15 ; 21,1 s | 0 | 0 | 15 |
| « Le personnage ne bouge plus » : nombre ; délai du verdict | 0 | 10 ; **4,3 s** | 0 | 10 |
| « a marché vers … puis s'est arrêté » : nombre ; délai | 0 | 2 ; 7,2 s | 0 | 2 |
| Temps passé sur des soleils inatteignables (somme des verdicts) | 316 s | 57 s | 0 s | 373 s |
| « Autre soleil » (passage par un 2e soleil du même côté) | 0 | 5 | 0 | 5 |
| Sorties notées bloquées (« inutilisable » + « pas de sortie ») | 5 | 4 | 1 | 10 |
| … dont « bloquée pour ce trajet seulement » (rien d'appris) | 0 | 2 | 1 | 3 |
| … dont inatteignables depuis un côté d'arrivée | 0 | 2 | 0 | 2 |
| « Nouvel itinéraire » | 4 | 4 | 1 | 9 |
| Cartes en trop / cartes prévues (trajets arrivés) | 0 / 2 | 6 / 109 (6 %) | 2 / 14 (14 %) | 8 / 125 (6 %) |
| Reclics du principal sur le même soleil (journal détaillé) | 88 (7,3 par passage) | 0 * | 0 * | 88 |
| Arrivées du principal sans coordonnées lues (nom illisible) | 2 / 7 (29 %) | 22 / 115 (19 %) | 4 / 16 (25 %) | 28 / 138 (20 %) |
| … région non reconnue (coordonnées lues) | 1 | 10 | 1 | 12 |
| Coordonnées lues différentes du chemin | 0 | 0 | 0 | 0 |
| Arrivées de suiveurs : vérifiées / non vérifiées (« nom illisible ») | 15 / 6 (29 %) | 256 / 88 (26 %) | 32 / 16 (33 %) | 303 / 110 (27 %) |
| **Suiveurs perdus (épisodes)** | **15** (tous « passé sans le principal ») | **1** | **0** | 16 |
| Regroupements lancés / réussis / impossibles | 12 / 12 / 0 | 1 / 1 / 0 | 0 | 13 / 13 / 0 |
| Reclics d'un suiveur (« Je suis le principal … ») | 0 | 12 | 1 | 13 |
| Battement entre deux cartes (vérification des suiveurs) | 0,6 s | 0,9 s | 0,8 s | 0,9 s |
| Lignes ERROR / exceptions / WARNING | 0 / 0 / 40 | 0 / 0 / 34 | 0 / 0 / 2 | 0 / 0 / 76 |

\* Depuis 21:50, le principal ne reclique plus toutes les 3 s : il conclut « ne bouge plus » et essaie ailleurs. Ces
deux chiffres ne se comparent pas.

**Ce qu'il faut savoir sur P7.** Un des deux journaux (21:05) est entièrement pris par une seule situation : en 1,-13,
le clic du principal sur le soleil de droite ne prend pas, alors que les suiveurs passent. Cela s'est produit 5 fois,
puis le trajet s'est arrêté. Les 5 ratés, les 15 « Toujours sur la même carte » et les 15 suiveurs perdus de P7
viennent de cet endroit.

Trajets de référence (mesuré), à rejouer pour comparer :

| Session | Trajet | Cartes prévues / passées | Durée | s/carte | Remarque |
|---|---|---|---|---|---|
| 21:51 (P8) | 1,-14 → -1,-14 | 2 / 6 | 56 s | 9,4 | 2 murs (0,-14 gauche), contournés par -1,-13 |
| 21:55 (P8) | -1,-20 → 8,-19 | 10 / 10 | 70 s | 7,0 | « a marché » en 4,-19 droite (7,4 s) ; le rapport dit 5,-19 |
| 21:58 (P8) | 4,-13 → 5,-23 | 11 / 11 | 75 s | 6,8 | « a marché » en 4,-19 haut (7,0 s) ; 4,-22 haut : pas de sortie |
| 22:03 (P8) | 4,-16 → 1,-27 | 14 / 16 | 110 s | 6,9 | 4,-22 haut, +2 cartes |
| 22:05 (P8) | 1,-27 → 4,-16 | 16 / 16 | 122 s | 7,6 | Imthebest perdu en 5,-15 puis regroupé |
| 22:11 (P9) | 4,-16 → 1,-27 | 14 / 16 | 101 s | 6,3 | 4,-22 haut, +2 cartes, comme à 22:03 |

## 4. Combats (rapporté seulement : aucun journal)

| Session | Lancements | Combats | Plantages | Chiffres écrits dans le rapport |
|---|---|---|---|---|
| Test 15:05 | 1 | – | – | tours de 2,7 à 5 s ; 4 comptes connectés en 26 s ; 2e Amplification refusée (5 s perdues) ; sorts lus sur 10 lignes seulement |
| S1 15:43–15:56 | 3 | 6 | 0 | sorts lus en entier (23, 22, 21, 21) ; Tempête de Puissance ratée une fois sur deux avant correction ; Jesuisleking : 33 s sur 2 tours de refus |
| S2 16:09–16:39 | 5 | 13 | 0 | Gela : 4 refus de suite (~5 s chacun) ; faux tour de Brawli (corrigé) |
| S3 16:49–17:34 | 6 | 14 | 0 | aucun faux tour sur 14 combats ; confirmation d'un sort en 0,5 à 3,0 s, un refus en 4,6 s ; un tour de Gela de 26,6 s (4 refus) |
| S4 17:32–17:52 | 1 | 3 | 0 | connexion : 4 personnages en 10 s, groupe en 4 s ; Épée du destin au pavé numérique |
| 19:04–20:00 | ? | 3 | 0 | « 3 combats sans souci » |

Il y a 39 combats et 0 plantage rapportés, mais aucun indicateur ne se recalcule : pas de durée de combat, pas de taux
de refus, pas de nombre de tours. L'outil sait déjà lire tout ce qui le permettrait dès la prochaine session de
combat : « Mon tour », « Tour : … », « lancé sur », « refusé sur », « Pas de ligne de vue », « hors de portée »,
« non sélectionnable », « ne semble pas parti », « Suite de mon tour », « Bilan du combat » et `combats.jsonl`.

## 5. Fenêtres, sécurité, connexion

- **Mesuré :**
  - 0 Menu principal ouvert par Échap, 0 fenêtre « quitter ta partie », 0 fenêtre inconnue ;
  - 0 fenêtre fermée, ce qui est normal sans combat ;
  - 2 déconnexions pour inactivité à 21:49, pendant une connexion seule : « Fenêtre « Connexion » (déconnecté pour
    inactivité) : « Ok » », les deux personnages remis en jeu ;
  - 0 ERROR, 0 exception.
- **Rapporté :**
  - Menu principal ouvert chez Brawli (17:47) ;
  - vers 19:00, un clic tombé sur « Déconnecter » : fenêtre « quitter » chez 3 personnages, fermée par « Non ».
  - Aucun journal restant ne permet de vérifier que la correction tient.

## 6. Banque et échanges (outils, mesuré)

| Outil | Lancements | Réussis | Échecs (cause) |
|---|---|---|---|
| entrer_porte | 3 | 3 | 0. 8 passages de personnages sur 8 (entrée 4/4, sortie 2/2 + 2/2) |
| banquier | 4 | 2 | 2. « Parler » à 0,54, puis banquier à 0,45 : les deux premiers essais (18:28, 18:33) |
| coffre_transfert | 2 | 2 | 0 |
| echange | 4 | 3 | 1. « Echange » à 0,55 (18:45, 1er essai) |
| echange_pile | 2 | 0 | 2. « max » à 0,40 : le glisser-déposer par message ne marche pas (rapporté) |
| echange_valider | 3 | 3 | 0 |

- **Pods mesurés** au début de chaque session du soir : Gela 35 %, Brawli 23 %, Jesuisleking 20 %, Imthebest 0 %.
  Aucune alerte « il faut passer à la banque ». Brawli à 90 % à 18:23 est un chiffre rapporté, avant le vidage du coffre.
- **Le bot ne fait pas encore la banque** : rien n'est mesurable en dehors des outils (§ 9).

## 7. Ce qui s'est amélioré, ce qui stagne, ce qui a régressé

### Amélioré (mesuré)

1. **Coût d'un soleil inatteignable.**
   - Un essai raté : 21,1 s (P7, n = 15), puis 4,3 s en P8 (« ne bouge plus », n = 10) ou 7,2 s (« a marché »,
     n = 2).
   - Un mur : 64 s (3 essais) avant, environ 9 s après. Exemple : 0,-14 gauche, conclu en 9,8 s puis en 9,1 s.
   - Temps perdu dans les changements ratés : 320 s pour 5 (P7), puis 21 s pour 4 (P8).
2. **Taux de réussite d'un changement de carte** : 58 % (P7), puis 97 % (P8) et 94 % (P9, 1 raté sur 17).
3. **Trajets arrivés** : 1 sur 2 en P7, puis 16 sur 16 depuis 21:50, sans aucun arrêt.
4. **Suiveurs perdus** : 15 épisodes en P7, puis 1 en P8 et 0 en P9. Aucun regroupement impossible (13 sur 13).
5. **Murs à deux soleils** : 5 passages par « Autre soleil » en P8, un comportement qui n'existait pas en P7.

**Limite (supposé).** La situation qui a fait tout P7 ne s'est pas reproduite depuis : en 1,-13, le principal est
bloqué par le jeu pendant que ses suiveurs passent. Le garde-fou ajouté arrête le bot après 2 fois
(« Le principal ne passe pas des soleils que ses suiveurs passent ») ; il n'a jamais été vu en jeu.

### Stagne (mesuré)

1. **Vitesse de base d'un changement de carte.**
   - 5,9 s, 6,1 s puis 5,5 s (médianes 5,9, 5,9 et 5,5). Avec la vérification des suiveurs, 7,3 s par carte en P8.
   - Les corrections du 11/10 n'y ont rien changé : c'est la marche et le chargement de la carte.
   - Seules marges : la recherche à la touche A (~1 s quand le soleil n'est pas en mémoire) et le battement de 0,9 s.
2. **Lecture du nom de carte.**
   - 20 % des arrivées du principal sans coordonnées lues (29 %, 19 %, 25 %).
   - 41 « Nom de la carte illisible » sur 19 cartes distinctes. Pour le principal, 4,-16 (la banque) revient 5 fois
     et 4,-20 4 fois.
   - 12 arrivées en « région inconnue », dont 4,-19 4 fois.
3. **Suiveurs non vérifiés.** Conséquence de 2 : 27 % des arrivées de suiveurs ne sont pas vérifiées, avec le
   message « a changé de carte (nom illisible) ». Cela fait 110 arrivées sur 413 (29 %, 26 %, 33 %).
4. **Même mur redécouvert à chaque trajet.** Autre conséquence de 2 : 4,-22 n'a pas de sortie vers le haut.
   - Le bot l'a vu 3 fois (21:59, 22:04, 22:12) et a noté chaque fois « bloquée pour ce trajet seulement : carte pas
     sûre », car le nom de 4,-22 est illisible.
   - `data/carte.json` n'a toujours rien pour 4,-22 haut. L'itinéraire repasse donc par là à chaque trajet :
     14 cartes prévues, 16 passées, soit environ 14 s de plus par trajet.
5. **« Sort n°1 inconnu » (Capture d'âmes)** à chaque lancement, 18 sur 18. C'est connu et sans effet, mais c'est le
   premier avertissement de chaque journal.

### Régressé ou apparu (mesuré, échantillon trop petit pour conclure)

1. **Reclics des suiveurs.**
   - 0 en P7 (21 arrivées de suiveurs), puis 12 en P8 (344 arrivées) et 1 en P9 (48).
   - Celui de 22:07:13 a fait descendre Imthebest en 5,-15 : c'est la seule perte de suiveur de P8.
   - Corrigé d'après le rapport, avec 1 reclic et 0 perte en P9. À confirmer sur plus de trajets : P7 n'a que 21
     arrivées, on ne peut pas parler de régression.
2. **Cartes en trop.** 0 % en P7 (un trajet de 2 cartes), puis 6 % en P8 et 14 % en P9 (un seul trajet).
   - Ce n'est pas une régression du code : c'est le mur de 4,-22 (+2 cartes à chaque fois) et 0,-14 (+4 une fois).

### Ne peut pas être jugé

Les combats, les fenêtres en combat, les déconnexions en jeu et la banque dans le bot n'ont aucun journal (§ 1).
C'est probablement ce qui donne l'impression de ne pas progresser : sans base, aucune comparaison n'est possible.

## 8. Les 10 problèmes les plus fréquents (mesuré, 20 journaux)

| # | Problème | Occurrences | Sessions | Exemple de ligne (session) |
|---|---|---|---|---|
| 1 | Suiveur arrivé sans vérification (nom de carte illisible) | 110 | 11 | `21:04:48.908 INFO [Brawli-[Gal]] Brawli-[Gal] a changé de carte (nom illisible)` (210441) |
| 2 | Reclic du principal : son clic sur le soleil ne prend pas | 88 | 1 | `21:05:36.543 DEBUG [Gela-[A13]] Toujours sur la carte de départ : je reclique sur le soleil` (210507) |
| 3 | « Nom de la carte illisible » (cartes distinctes) | 41 | 10 | `21:04:48.509 INFO [Gela-[A13]] Nom de la carte illisible (décor clair derrière le texte ?) : carte -1,-15 d'après le chemin` (210441) |
| 4 | Sort de la barre inconnu (Capture d'âmes) | 18 | 18 | `21:04:41.810 WARNING [Gela-[A13]] Sort n°1 inconnu : la tactique ne s'en servira pas` (210441) |
| 5 | « Toujours sur la même carte après 20 s » | 15 | 1 | `21:06:00.325 WARNING [Gela-[A13]] Toujours sur la même carte après 20 s (essai 1/3)` (210507) |
| 6 | « passé sans le principal » | 15 | 1 | `21:06:41.942 WARNING [Brawli-[Gal]] Brawli-[Gal] est passé sans le principal (resté sur sa carte) : il le rejoindra par son trajet` (210507) |
| 7 | Reclic d'un suiveur | 13 | 5 | `21:59:46.946 INFO [Imthebest] Je suis le principal vers le haut : clic sur le soleil en (21, 1)` (215845) |
| 8 | « Le personnage ne bouge plus » (soleil inatteignable) | 10 | 3 | `21:50:16.127 INFO [Gela-[A13]] Le personnage ne bouge plus et n'a pas changé de carte : soleil en (1, 7) pas atteignable d'ici ? (essai 1/2)` (215010) |
| 9 | « Nouvel itinéraire » | 9 | 5 | `21:06:51.255 INFO [Gela-[A13]] Nouvel itinéraire de 1,-13 à 4,-13 : 3 carte(s) : 2,-13 3,-13 4,-13` (210507) |
| 10 | « Sortie … inutilisable, je la note bloquée » | 7 | 2 | `21:06:41.899 WARNING [Gela-[A13]] Sortie du droite inutilisable, je la note bloquée (capture : …)` (210507) |

Ces dix lignes viennent de trois causes :

- **Clic du principal qui ne prend pas en 1,-13** : n° 2, 5, 6, et une partie des n° 9 et 10. Un seul endroit, 5 min
  20 s perdues.
- **Nom de carte illisible** : n° 1 et 3, plus le mur de 4,-22 redécouvert. C'est la cause qui stagne.
- **Remparts** : n° 8, 9 et 10. C'est la cause la mieux traitée depuis 21:50.

## 9. Indicateurs à ajouter au journal pour les prochains tests

| Indicateur | Aujourd'hui | Message à ajouter |
|---|---|---|
| **Garder la base** (le plus important) | `keep_sessions: 20` efface les journaux en une soirée | lancer l'outil après chaque session et garder sa sortie (`--json`) hors de `logs/`, ou augmenter `logs.keep_sessions` (à décider) |
| Fin de session | arrêt brutal : pas de ligne de fin | « Bilan de session : durée, cartes, combats, arrêts », écrit à chaque fin |
| Victoires / défaites | « Combat terminé » ne dit pas l'issue | « Combat terminé : victoire\|défaite (N tours, D s, morts : …) » |
| PA restants en fin de tour | absents de la ligne « Tour : » | « Tour : … ; PA restants N/M, PM restants N/M » |
| Tour détecté en retard | « Mon tour » sans heure du début réel du tour | « Mon tour (timer vu N,N s après la fin du tour précédent) » |
| Faux « Mon tour » | seuls ceux rattrapés (« Suite de mon tour ») | « Faux tour : … » (tour fini sans action ni F1 accepté, timer pas revu) |
| Dégâts, morts | rien | « Dégâts : N sur X (sort) », « X est mort », lus dans le chat du combat (piste P13) |
| Suiveur arrivé, nom illisible | « a changé de carte (nom illisible) », sans vérification | « … (même décor que le principal : oui\|non, N,N s) » |
| Cause d'un changement de carte raté | « Sortie … inutilisable » couvre plusieurs causes | « … : clic pas pris \| soleil inatteignable \| 20 s écoulées » |
| Mur appris ou non | « bloquée pour ce trajet seulement » | « … : à revoir : carte x,y côté c, nom illisible » (liste en fin de session) |
| Banque (quand elle sera dans le bot) | outils seulement | « Banque : porte (s), coffre ouvert (s), N piles transférées, pods X % → Y % » ; « Échange X → Y : N objets en D s » |
| Pods | au début seulement | aussi en fin de session |

## 10. Pour comparer les prochaines sessions

- Lancer `python -m tools.bilan_sessions logs\<session>` juste après chaque test, avant que d'autres lancements
  n'effacent le journal, et garder la sortie.
- **Valeurs de départ des trajets** (P8, 115 cartes), à égaler ou à battre :

  | Indicateur | Valeur de départ |
  |---|---|
  | Taux de réussite d'un changement de carte | 97 % |
  | Temps par carte (suiveurs compris) | 7,3 s |
  | Verdict d'un soleil inatteignable | 4,3 s |
  | Suiveurs perdus par trajet | 0,07 (1 sur 15) |
  | Arrivées de suiveurs non vérifiées | 26 % |
  | Arrivées sans coordonnées lues | 19 % |
  | Cartes en trop | 6 % |
- **Rejouer les mêmes trajets** que le tableau du § 3 : 1,-14 → -1,-14, -1,-20 → 8,-19, 4,-16 ↔ 1,-27.
- **Combats** : la première session donnera la base. Lancer `--combats N`, puis l'outil, qui lit déjà les lignes
  « Tour : » et « Bilan du combat » et `combats.jsonl`. Prendre les mêmes zones et le même nombre de combats d'une
  session à l'autre.
