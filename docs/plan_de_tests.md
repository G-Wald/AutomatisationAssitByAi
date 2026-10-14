# Plan de tests robuste : déplacements, combat, banque

Proposé le 12/10, à valider avec l'utilisateur.

## 0. Pourquoi ce plan

Jusqu'ici, chaque session de test servait à trouver le prochain bug. On le corrigeait, puis la session suivante allait
ailleurs, et rien ne mesurait d'une fois sur l'autre si le bot faisait mieux. D'où l'impression de ne pas avancer.

Ce plan fixe les règles suivantes :

- **Des scénarios fixes**, joués aux mêmes endroits et de la même façon à chaque fois, pour comparer les sessions entre
  elles.
- **Des critères chiffrés** : un test passe ou ne passe pas, sans « ça a l'air mieux ».
- **Un bilan automatique** des journaux (`tools/bilan_sessions.py`, en préparation) à la fin de chaque session.
- **Une règle de validation** : un module (déplacements, combat, banque) est validé quand tous ses tests obligatoires
  passent sur **deux sessions de suite**, à **deux endroits différents** au moins. La validation finale enchaîne les
  trois modules sans intervention.

Trois niveaux de test :

| Niveau | Quoi | Quand |
|---|---|---|
| N1 hors jeu | Tests automatiques (`pytest`) sur des captures réelles : chaque bug vu en jeu y devient un test | À chaque changement de code (aujourd'hui 719 tests) |
| N2 sessions ciblées | 20 à 30 min, un thème, un banc d'essai (section 3), la liste de tests de la section 4 | Une session par thème |
| N3 validation | 60 à 90 min d'affilée : combats en zone, pods pleins, banque, retour, combats | Quand N2 est validé partout |

## 1. Protocole d'une session

**Avant**

- L'utilisateur ouvre la session et en fixe la durée. Le bot ne touche au jeu que pendant ce temps.
- Les 4 personnages sont connectés et groupés sur la carte de départ du banc d'essai, avec leurs PV pleins. Leurs pods
  sont sous 50 %, sauf pour les tests banque.
- Les fenêtres du jeu ne sont pas réduites. Aucun menu ni aucune fenêtre ouverts. Les touches du pavé numérique sont
  réglées sur les 4 comptes.
- Copie de `data/carte.json` (mémoire des cartes), pour pouvoir rejouer un test « mémoire vide » ou « mémoire apprise ».

**Pendant**

- Chaque test se lance avec sa commande (section 4). On ne touche à rien.
- Toute intervention manuelle est notée et fait échouer le test, sauf si le test la prévoit (déconnexion volontaire…).
- Un test qui échoue s'arrête et se note. On passe au suivant au lieu de corriger en pleine session, sauf blocage
  total.

**Après**

- `tools/bilan_sessions.py` sur les dossiers de la session, puis la fiche de résultats (section 6) remplie.
- Chaque échec est classé : **bot** (bug à corriger), **jeu** (blocage du personnage, lag), ou **environnement**
  (déconnexion, fenêtre recouverte).
- Pour chaque échec « bot » : capture gardée, test N1 ajouté, correction, puis le même test rejoué à la session
  suivante.

## 2. Indicateurs et seuils

Les seuils ci-dessous sont des propositions. La référence de départ vient des trajets du 11/10 au soir, et des chiffres
des sessions passées (bilan en cours).

### Déplacements

| Indicateur | Définition | Seuil de validation | Référence 11/10 soir |
|---|---|---|---|
| Changements de carte réussis | Réussis / tentés, sorties murées non comptées | ≥ 98 % avec la mémoire apprise, ≥ 90 % en zone nouvelle | 100 % sur environ 120 cartes |
| Temps par carte | Clic sur le soleil jusqu'à la carte suivante lue, moyenne | ≤ 8 s en zone connue, ≤ 12 s en zone nouvelle | 6 à 7 s |
| Soleil inatteignable | Clic jusqu'au passage au soleil suivant | ≤ 7 s, médiane ≤ 5 s | 3 à 6 s |
| Trajets arrivés | Arrivés / lancés, s'il existe un chemin | 100 % | 13 sur 13 |
| Cohésion | Suiveur perdu | ≤ 1 pour 50 cartes, regroupé seul à 100 % | 1 sur environ 120 cartes, regroupé |
| Mémoire juste | Sorties bloquées contredites par un passage réussi ; liaisons entre cartes non voisines | 0 | Plusieurs fausses avant le 11/10 au soir |
| Sécurité | Menu principal resté ouvert, « Oui » à « quitter ta partie », intervention manuelle | 0 | 0 |

### Combat

| Indicateur | Définition | Seuil de validation |
|---|---|---|
| Combats gagnés | Gagnés / joués, en zone de niveau adapté | ≥ 95 %, aucun combat bloqué |
| Ralliement | Suiveurs qui rejoignent le combat du principal | 100 %, prêts en ≤ 15 s |
| Détection du tour | Faux « Mon tour » ; tour passé sans action | 0 ; 0 |
| Réactivité | Début du tour jusqu'à la 1re action, médiane | ≤ 2 s |
| Sorts partis | Partis / tentés | ≥ 90 % ; refus ≤ 1 par combat en moyenne |
| PA gaspillés | Tours finis avec une attaque encore possible | ≤ 1 tour sur 10 |
| Fin de combat | Fenêtre de résultat fermée partout, reprise | ≤ 5 s |

### Banque, pods et échanges

| Indicateur | Définition | Seuil de validation |
|---|---|---|
| Pods lus | Jauge lue contre l'inventaire (en %) | Écart ≤ 3 points |
| Chaîne banque | Trajet, porte, banquier, coffre, transfert, sortie | ≥ 9 réussites sur 10 |
| Banquier trouvé | Banquier repéré et dialogue ouvert, sachant qu'il bouge | ≥ 95 % |
| Échange | Bon destinataire vérifié (nom dans la fenêtre), tout donné, validé des deux côtés | 100 % ; pods du suiveur ≤ 5 % après |
| Durée | Par suiveur, en mode un par un | À mesurer, selon le nombre d'objets |

Un indicateur que le journal ne permet pas encore de calculer sera signalé par le bilan (agent en cours). Le message de
journal qui manque sera ajouté avant la session concernée.

## 3. Bancs d'essai (lieux fixes)

| Banc | Où | Ce qu'il éprouve |
|---|---|---|
| A, cité d'Astrub | -1,-20 à 8,-19, -1,-13 à 5,-23 | Remparts, cartes coupées par les murs (deux soleils d'un côté), noms de carte illisibles, beaucoup de joueurs |
| F, forêt d'Astrub | -1,-23 à 3,-27 | Combats faciles en série, arbres (épée du combat cachée), trajets en forêt |
| I, plaine à l'ouest | autour de -5,-18 | Monstres qui invoquent (pas encore observés), contour ouest de la cité |
| C, champs et prairies | 5,-25 et 9,-20 | Grands espaces, ressources, autres joueurs |
| N, zone nouvelle | À choisir ensemble (niveau adapté) | Généralisation : mémoire vide, soleils jamais vus, autre décor |
| B, banque d'Astrub | 4,-16 (porte (23,9), sortie intérieure (8,26)) | Banquier qui bouge, coffre, échanges |

## 4. Catalogue des tests

Pour chaque test : l'objectif, le départ, la commande, le nombre de répétitions et le critère de réussite. Les
commandes supposent l'équipe de 4 en arrière-plan :
`.venv/Scripts/python -m bot --monde douze --equipe --principal "Gela-[A13]" --lecture memoire --verbose`, appelée
**ÉQUIPE** ci-dessous.

### 4.1 Déplacements (D)

| ID | Objectif | Départ et commande | Répétitions | Réussi si |
|---|---|---|---|---|
| D1 | Pas simple | Banc A : aller-retour 2,-14 ↔ 2,-13 (ÉQUIPE `--aller`) | 10 passages | 10/10, ≤ 8 s par carte |
| D2 | Carte coupée par un rempart | -1,-19 (bas), 2,-14 (gauche), 1,-14 (gauche), 5,-19 (droite), 4,-19 (haut) ; d'abord mémoire de ces cartes effacée, puis apprise | 3 allers-retours par carte | 1er passage ≤ 15 s ; ensuite aucun soleil inatteignable essayé |
| D3 | Vrai mur | 0,-14 gauche depuis la droite et depuis le bas | 2 par côté d'arrivée | Mur vu en ≤ 7 s, contourné, pas réessayé au passage suivant |
| D4 | Les 5 portes de la cité | -1,-20/-2,-20, 5,-22/5,-23, 7,-19/8,-19, 4,-14/4,-13, -1,-14/-1,-13 | Chaque porte dans les deux sens, 2 fois | 20/20 |
| D5 | Parcours longs de référence | -1,-20→8,-19 ; 8,-19→4,-13 ; 4,-13→5,-23 ; 5,-23→-2,-20 ; -2,-20→-1,-13 ; -1,-13→4,-16 ; 4,-16↔1,-27 | Chacun 2 fois | Tous arrivés, équipe groupée, durée ≤ référence du 11/10 + 10 % |
| D6 | Départ sans position donnée | Sans `--position`, d'une carte au nom lisible, puis d'une carte au nom illisible | 3 + 3 | Position trouvée ; « /p %pos% » rarement utilisé *(à implémenter, approche validée le 11/10)* |
| D7 | Imprévus en route | Cité aux heures de pointe ; monstre agressif sur le trajet ; suiveur déconnecté ; menu d'un joueur ouvert | 1 de chaque | Trajet repris et arrivé, combat d'agression joué, suiveur reconnecté et regroupé |
| D8 | Mémoire juste | Après chaque session : contrôle de `data/carte.json` *(script à écrire)* | — | 0 sortie bloquée contredite par un passage, 0 liaison non voisine |
| D9 | Zone nouvelle | Banc N, mémoire vide, trajet de plus de 20 cartes | 2 | Arrivé, ≥ 90 % de passages réussis, ≤ 12 s par carte |
| D10 | Principal bloqué par le jeu | Observé quand ça arrive (rare) | — | Le bot s'arrête au 2e cas et le dit ; aucune sortie notée bloquée à tort |

### 4.2 Combat (C)

| ID | Objectif | Départ et commande | Répétitions | Réussi si |
|---|---|---|---|---|
| C1 | Série de combats simples | Banc F, ÉQUIPE `--zone --combats 10` | 10 combats | ≥ 95 % gagnés, 0 faux tour, ≥ 90 % de sorts partis |
| C2 | Ralliement | Banc F, épée parfois cachée par les arbres | Dans C1 | 10/10 combats rejoints par les 3 suiveurs, prêts en ≤ 15 s |
| C3 | Détection du tour | Dans C1 et C7 | — | 0 faux « Mon tour », 0 tour passé sans action, 1re action en ≤ 2 s (médiane) |
| C4 | Sorts de la 2e ligne | Dans C1 | — | Chaque personnage lance au moins une fois un sort de la 2e ligne (pavé numérique) |
| C5 | Portée et ligne de vue | Cartes avec relief et obstacles (forêt dense, cité) | 5 combats | ≤ 1 refus par combat, jamais 2 refus de suite sur des cases voisines (P14) |
| C6 | Fin de combat pendant mon tour | Dans tous les combats | — | Aucun sort tenté sur un ennemi mort (P15) |
| C7 | Invocateurs | Banc I (-5,-18) | 5 combats | Gagnés, invocations prises en compte, aucun blocage |
| C8 | Combat difficile | Zone plus dure (à choisir ensemble) | 3 | Victoire, ou défaite propre : arrêt et message, rien de bloqué |
| C9 | Agression en trajet | Trajet passant par une zone de monstres agressifs | 2 | Combat joué, puis trajet repris jusqu'à l'arrivée |
| C10 | Endurance | Banc F, ÉQUIPE `--zone --minutes 30` | 1 | 0 intervention ; combats par heure notés (référence de vitesse) |
| C11 | Repos | PV bas après un combat | Dans C10 | Repos jusqu'au seuil avant le combat suivant |

### 4.3 Banque, pods et échanges (B)

| ID | Objectif | Départ et commande | Répétitions | Réussi si |
|---|---|---|---|---|
| B1 | Lecture des pods | Jauge contre l'inventaire, vers 20 %, 50 % et 90 % | 3 par personnage | Écart ≤ 3 points |
| B2 | Déclenchement | Pods ≥ 90 % pendant les combats | — | Départ vers la banque *(à intégrer au bot : aujourd'hui, outils séparés)* |
| B3 | Porte de la banque | 4,-16 : entrée (23,9) et sortie (8,26) | 5 par personnage | 20/20 |
| B4 | Banquier | Dans la banque, à des heures différentes | 10 | ≥ 95 % : trouvé et dialogue « Consulter son coffre » ouvert |
| B5 | Coffre | Onglet ressources, « Transférer tous les objets » | 4 personnages | Onglet ressources vide après, prix noté |
| B6 | Échange en mode un par un | Suiveur → principal, depuis le portrait du groupe | 3 suiveurs | Nom vérifié des deux côtés, tout donné, accepté des deux côtés |
| B7 | Échange en mode ctrl | Même chose, piles entières (fenêtre devant, vraie touche Ctrl) | 3 suiveurs | Idem, durée notée |
| B8 | Mode chacun | Chaque personnage vide dans son propre coffre | 4 personnages | Tout transféré |
| B9 | Cycle complet | Combats au banc F jusqu'à 90 % de pods, banque, retour, combats | 3 cycles | Sans intervention, retour au même banc |
| B10 | Sécurité | Demande d'échange d'un inconnu, banquier introuvable, coffre plein | Quand ça se présente, ou provoqué | Refusé ; arrêt propre avec message ; jamais d'échange avec un inconnu |

### 4.4 Transverses (S)

| ID | Objectif | Réussi si |
|---|---|---|
| S1 | Fenêtres surgissantes : niveau supérieur, résultat, défi reçu, invitations d'inconnus | Fermées ou refusées, jamais acceptées |
| S2 | Déconnexion pour inactivité | Reconnexion seule (`--connexion`), le groupe reformé |
| S3 | F12 à n'importe quel moment | Touches relâchées, aucun menu laissé ouvert |
| S4 | L'utilisateur se sert de son PC pendant la session | Aucun clic ni aucune touche hors des fenêtres du jeu |

### 4.5 Donjons (DJ), repris de docs/rapports/donjons.md (12/10)

Banc DC : Donjon des Champs, entrée 7,-25 ; banc B : banque d'Astrub. Les hypothèses H1 à H6 (fenêtre du coffre,
filtre, reprise des clés) sont décrites dans ce rapport.

| ID | Objectif | Départ | Répétitions | Réussi si |
|---|---|---|---|---|
| DJ0 | Annonce des clés au démarrage | Quatre personnages avec 0, 1, 2 et 5 clés | 2 | Nombres annoncés justes pour chacun, total et nombre de runs possibles |
| DJ0b | Répartition par échanges | Total suffisant mais mal réparti (ex. 5, 0, 2, 0) | 2 | Plan annoncé avant d'agir ; chacun a ensuite floor(total / 4) clés ; noms vérifiés, nombres posés relus |
| DJ0c | Pas assez de clés | Total inférieur à 4 | 1 | Message « pas assez de clés pour tout le monde (N demandées, M en tout) », aucun échange ni dialogue |
| DJ1 | Clé lue dans l'inventaire | Hors combat, avec 0, 1 et 5 clés | 3 par personnage | Présence et nombre justes |
| DJ2 | Dialogue d'entrée d'un personnage | Banc DC, une clé | 1 par personnage | Réponse par texte, entré, clé consommée notée (oui/non) |
| DJ3 | Entrée de l'équipe | Banc DC, 4 clés | 3 | 4/4 dans la salle 1, ordre suiveurs puis principal |
| DJ4 | Sans clé | Un suiveur sans clé | 1 | Personne n'entre (ou sortie propre), message clair |
| DJ5 | Salle reconnue | Salle 1, puis chaque salle | Tout le run | Numéro de salle juste, `data/carte.json` inchangé |
| DJ6 | Passage de salle | Après chaque combat | Tout le run | Équipe groupée, ≤ 15 s par passage |
| DJ6b | Salle limitée | Salle 3 des Champs (3 places) | 2 runs | 3 rejoignent, le 4e fait son combat à part (ou attend), tous passent ensuite à la salle 4 |
| DJ7 | Run complet | Banc DC | 3 | Boss battu, dehors, ≤ 1 intervention au total |
| DJ8 | Sortie | Après le boss | Dans DJ7 | Dehors reconnu (sous-zone de l'entrée) |
| DJ9 | Mort ou défaite | Quand ça arrive | — | Personnage manquant vu, suite selon 5.1, aucun blocage |
| DJ10 | Cycle complet | Banc DC → B → DC | 2 cycles | Pods, banque, clés reprises, retour, runs enchaînés |
| B11 | Filtre du coffre | Banc B, coffre avec clés et ressources | 3 | Seules les clés affichées (H1 à H4) |
| B12 | Reprise des clés | Coffre filtré | 3 par personnage | Bon nombre repris, rien d'autre (H6) |
| B13 | Clés à Imthebest (sans coffre) | Échange d'un personnage pourvu vers Imthebest | 2 | Nom vérifié, 5 clés reçues ; ses ressources données par échange |

### 4.6 Récolte (R), repris de docs/rapports/metiers_ressources.md (12/10)

R0 est un test hors jeu : repérage des arbres sur les captures de logs/, avec `tools/ressources_essai.py`.

| ID | Objectif | Départ et commande | Répétitions | Réussi si |
|---|---|---|---|---|
| R0 | Non-régression hors jeu | `python -m tools.ressources_essai --essai --une-par-carte --bas --seuils 0.6 --seuil-souche 0.7` | À chaque changement du repérage | Frêne ≥ 68/89, Châtaignier ≥ 29/40, aucun arbre debout faux sur les planches |
| R1 | Repérage en jeu | Cartes 4,-16, 2,-23, 1,-27, 1,-26 : repérage seul, capture annotée | 2 passages par carte | Toutes les ressources visibles trouvées, sur la bonne case et dans le bon état |
| R2 | Menu de récolte | Clic sur une ressource debout, en arrière-plan | 20 | Menu détecté et « Couper » cliqué ≥ 19/20 |
| R3 | Récolte complète | Frêne de 4,-16 puis forêt : clic, action, fin | 10 | ITEM_FOUND lu, souche vue sur la même case, durée notée |
| R4 | Ressource déjà prise | Souche, ou arbre coupé par un autre pendant la marche | Quand ça arrive (fréquent en forêt) | Pas de blocage, case ignorée, ressource suivante |
| R5 | Erreurs | Sans outil, sans le métier, niveau trop bas, inventaire plein | 1 de chaque | Message reconnu, arrêt propre ou ressource ignorée, jamais de boucle |
| R6 | Gardien | Récolte en série jusqu'à un protecteur (1/40 environ) | Dès qu'il apparaît | Combat joué ; équipe ralliée si c'est possible (noter si ça ne l'est pas) ; reprise seule |
| R7 | Repousse | Noter l'heure de chaque passage souche → debout | Sur toute une session | Durée par ressource notée dans la mémoire |
| R8 | Équipe | 4 personnages sur une carte de forêt | 3 cartes | Aucune ressource visée par deux personnages ; rendement noté |
| R9 | Circuit | Forêt d'Astrub, `--minutes 30` en mode récolte | 1 | 0 intervention, rendement noté |
| R10 | Pods et banque | Récolte jusqu'à 90 % de pods, banque, retour (avec B9) | 2 cycles | Retour au circuit, rien perdu (pas d'INFOS_144) |
| R11 | Fenêtre inconnue | Mini-jeu, invitation, défi pendant une récolte | Quand ça arrive | Arrêt et capture pour le mini-jeu, refus pour le reste |
| R12 | Céréales | Après les gabarits du Blé (capture n° 5) : champ 5,-26 | 2 | Comme R1 et R3 |

## 5. Programme des sessions

| Session | Thème | Banc | Tests | Durée |
|---|---|---|---|---|
| 1 | Déplacements | A | D1, D2, D3, D4, D5, D8 | 30 min |
| 2 | Combat de base | F | C1, C2, C3, C4, C6, S1 | 30 min |
| 3 | Combat difficile et imprévus | Cité et forêt dense | C5, C9, D7 | 30 min |
| 4 | Invocateurs et contour ouest | I | Trajet A → I, C7, D5 (contour) | 30 min |
| 5 | Banque, outils | B | B1, B3 à B8 | 30 min |
| 6 | Zone nouvelle et endurance | N, puis F | D9, C10, C11, S2 | 45 min |
| 7 | **Validation finale** | F → B → F | B9, puis tous les indicateurs de la section 2 | 60 à 90 min |
| 8 | Donjon des Champs (plus tard, décidé par l'utilisateur) | DC → B → DC | DJ0 à DJ10, B11 à B13 | 45 min |
| 9 | Récolte (après les captures demandées) | F, puis C | R1 à R12 | 30 min |

Chaque session N2 est rejouée une seconde fois, à un autre endroit pour les déplacements et le combat, avant de compter
le module comme validé.

## 6. Fiche de résultats (modèle à copier pour chaque session)

| Test | Répétitions | Réussis | Mesure clé | Seuil | Résultat | Échecs (classe : bot, jeu, environnement) |
|---|---|---|---|---|---|---|
| D1 | 10 | | temps moyen par carte | ≤ 8 s | | |
| … | | | | | | |

Puis le bilan de `tools/bilan_sessions.py`, et la liste des corrections décidées.

## 7. À préparer avant la première session

| Quoi | Pour | État |
|---|---|---|
| `tools/bilan_sessions.py` et chiffres de départ à partir des anciens journaux | Section 2, toutes les sessions | En cours (agent) |
| Messages de journal manquants pour les indicateurs | Section 2 | Après le bilan |
| Outil « oublier ces cartes » dans `data/carte.json`, avec copie de sauvegarde | D2 (mémoire vide) | À écrire |
| Contrôle de cohérence de `data/carte.json` | D8 | À écrire |
| Position sans `--position` : chemin, décor, « /p %pos% » rarement | D6 | À implémenter (approche validée le 11/10) |
| Banque et échanges intégrés au bot : seuil de pods, trajet, modes un par un, ctrl, chacun | B2, B9, validation finale | À implémenter (aujourd'hui, outils séparés) |
| Refus de sort lu dans le chat | C5, indicateur « sorts partis » | Optionnel, à décider après la mesure de C1 |
