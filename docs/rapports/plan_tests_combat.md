# Combats : analyse du test du 11/10 (15:05) et plan de tests

## 1. Ce qui a marché

- Connexion de 4 comptes en cascade : 26 s entre Ctrl+8 et le 4e personnage en jeu.
- Groupe formé, combat rejoint par les 3 autres, « Prêt », tours joués dans l'ordre, fin de combat fermée dans les 4
  fenêtres.
- Tours rapides : de 2,7 à 5 s.

## 2. Ce qui n'a pas marché, et pourquoi

| # | Constat (journal) | Cause | Preuve |
|---|---|---|---|
| 1 | L'Iop n'a jamais utilisé Tempête de Puissance, ni le Crâ Maîtrise de l'Arc (ni Tir Puissant, Flèche Explosive...) | **En arrière-plan, la liste des sorts ne défile pas** (« La molette ne fait pas défiler », puis 3 clics sur la flèche du bas sans effet). Seules les 10 premières lignes sont lues. Les sorts plus haut niveau sont inconnus du bot, donc jamais lancés. | À 14:30, au premier plan, Gela avait 23 lignes en 4 pages, dont Maîtrise de l'Arc. À 15:05, en arrière-plan : 10 lignes en 1 page, pour les 4 personnages. |
| 1b | La lecture incomplète a **remplacé** la lecture complète en mémoire | Le bot enregistre sa lecture même quand la liste n'a pas défilé | `data/personnages/Gela-[A13].json` : 10 sorts (lus à 15:05:53) |
| 2 | Le Crâ lance Tir Éloigné alors que le monstre est déjà à portée | Sans Maîtrise de l'Arc (inconnue, voir 1), il lui reste 2 PA. Le moteur donne un petit bonus fixe à Tir Éloigné « pour les tours suivants », même quand il ne sert à rien. | Plan : Tir Éloigné, puis Flèche Magique x2 ; dégâts prévus 78 |
| 3 | L'Iop prévoit Amplification **4 fois** ; le 2e lancer est refusé (« Impossible de lancer ce sort actuellement »), 5 s perdues | La limite « 1 lancer par cible » n'est pas appliquée aux sorts lancés sur soi | Plan du 1er tour de Brawli |
| 4 | Épée du destin « non sélectionnable » alors qu'il restait 6 PA | Hypothèse : sa portée (1 case, en ligne) colore trop peu de cases. Le bot exige environ 1,6 case colorée pour croire le sort sélectionné. | Capture `0029` : 6 PA, 0 PM ; pas de capture du sort sélectionné |
| 5 | L'Iop ne sait pas lequel des 4 cercles rouges est lui | Il se repère avec la portée d'un sort d'au moins 2 cases. Parmi ses sorts connus (voir 1) et posés sur sa barre, aucun n'en a. Il prend un cercle au hasard. | « 4 cercles de ma couleur et rien ne dit lequel est moi » |
| 6 | Caractéristiques avancées pas ouvertes (Gela 2 fois, Jesuisleking 1 fois) : bonus aux dommages de Gela comptés 0 | Le clic en arrière-plan sur le petit bouton de la fenêtre ne passe pas. Probablement le même problème que la flèche de défilement. | Captures `0004`, `0005`, `0007` |
| 7 | « Sort n°1 inconnu » chez le Crâ | C'est **Capture d'âmes** (7 PA, 0 PO), un sort commun, sans dégâts, absent de la liste des sorts de classe | Capture `0003` |

**En résumé, les deux défauts que tu as vus (pas de Tempête de Puissance, Tir Éloigné à la place de Maîtrise de
l'Arc) viennent surtout de la lecture des sorts en arrière-plan.** Le moteur compte déjà les bonus de dégâts des
sorts qu'il connaît ; A4 vérifie qu'il les choisit bien avec tes vrais sorts.

## 3. Plan de tests

### Phase A : corrections vérifiables sans le jeu (par moi, sur les captures, tests automatiques)

| Réf. | Correction | Ce qui la vérifie |
|---|---|---|
| A1 | Une lecture des sorts incomplète (liste qui ne défile pas) ne remplace jamais une lecture complète : le bot garde la plus complète et le dit. | Test : lecture de 10 lignes après une de 23, la mémoire garde les 23 |
| A2 | Sorts sur soi : « 1 lancer par cible » = 1 lancer par tour sur soi. | Test : Iop, 10 PA ; Amplification prévue une seule fois |
| A3 | **Bonus de portée seulement s'il sert** : Tir Éloigné ne vaut quelque chose que si un coup de la suite en a besoin. Sinon, priorité aux bonus de dégâts, puis aux attaques. | Tests : Gela à portée, Tir Éloigné absent du plan ; Gela hors portée de 2 cases, Tir Éloigné puis Flèche Magique |
| A4 | **Bonus de dégâts en priorité quand on a déjà la portée** : je vérifie le calcul sur tes vrais sorts (lecture complète du 11/10 à 14:30) : Maîtrise de l'Arc (+40 par coup, 3 tours), Tir Puissant, Compulsion, Amplification. | Scénarios : Gela 10 PA, 1 ennemi à 6 cases, plan attendu Maîtrise + Flèche Magique x2 (≈ 158 au lieu de 78). Iop 10 PA, ennemi à portée de Tempête, plan attendu bonus puis Tempête. |
| A5 | **Zones : viser la case qui fait le plus de dégâts**, pas forcément un ennemi. Le moteur essaie déjà les cases vides autour des ennemis ; je le vérifie et je l'étends. | Scénarios sur grille (voir la liste après le tableau) |
| A6 | Sort sélectionné : seuil de cases colorées adapté aux portées de 1 case (Épée du destin, Intimidation). | Mesure sur une capture de B2 |
| A7 | Repérage de soi : utiliser aussi les sorts hors tactique posés sur la barre (Bond 1 à 4) quand aucun sort d'attaque n'a 2 cases de portée. | Test : Iop avec Bond seul, repérage fait |
| A8 | Capture d'âmes : sort commun, que tout le monde n'a pas (donjon) ; jamais utilisé pour se repérer. | Les sorts de repérage sont des sorts de classe (A7) |

Scénarios de zone pour A5 :
- **Flèche Explosive** (cercle de 2) sur 3 ennemis groupés : viser la case vide entre eux si elle les touche tous les 3.
- La même zone **jamais** si un allié ou moi sommes dedans, même pour un ennemi de plus.
- **Flèche Enflammée** (ligne de 5 derrière la cible) : viser le 1er ennemi d'une file pour toucher ceux derrière.
- **Épée du destin** (ligne jusqu'au bord) : se placer dans l'axe de plusieurs ennemis si les PM le permettent.
- **Dégressivité** (-10 % par case, réglée le 01/10) : préférer 2 ennemis au centre de la zone à 3 ennemis au bord, si le total est plus grand.

### Phase B : tests en jeu

Chaque test a une commande, ce qu'il faut regarder dans le journal et un critère de réussite. Les journaux et
captures sont dans `logs/<date>/`.

| Réf. | Test | Mise en place | Réussi si |
|---|---|---|---|
| B1 | **Défilement de la liste des sorts en arrière-plan** : un outil essaie chaque méthode (molette, flèche du bas, clic dans la barre sous la poignée, poignée glissée), une fenêtre à la fois, et dit laquelle fait défiler. Même chose pour le bouton des caractéristiques avancées. | Un personnage en jeu, fenêtre des sorts fermée | Une méthode fait défiler à chaque essai ; sinon, plan de repli ci-dessous |
| B2 | **Sorts à portée de 1 case** : Iop collé à un monstre, Épée du destin et Intimidation sélectionnées | Combat lancé à la main, `--combat-only` | Captures de la portée affichée, puis sort lancé |
| B3 | **Bonus de dégâts** : Gela seule contre 1 monstre à portée ; puis Brawli seul | Mode solo, arrière-plan | Gela : Maîtrise de l'Arc puis attaques, jamais Tir Éloigné à portée. Brawli : un seul lancer de chaque bonus, puis Tempête de Puissance. |
| B4 | **Zones** : groupe de 3 monstres ou plus, équipe de 4 | `--equipe`, carte à gros groupes | Journal « N ennemis dans la zone » ; aucun allié touché (chat du jeu) |
| B5 | **Repérage au 1er tour** de chacun des 4 (placement sans bouger) | `--equipe` | « Moi : le cercle en ... » pour les 4, aucun « rien ne dit lequel est moi » |
| B6 | **Série de 5 combats** avec les 4, mode zone | `--equipe --zone` | Pas de sort refusé deux fois, temps par tour, aucun arrêt |

Plan de repli si rien ne fait défiler la liste en arrière-plan (B1) : la fenêtre des sorts est lue une fois au premier
plan avec la vraie molette (2 à 3 s par personnage, au début de la session, comme pour le launcher), puis reprise de
la mémoire les fois suivantes. Le jeu reste en arrière-plan ensuite.

**Bilan de chaque combat** (à ajouter) : à la fin du combat, une ligne par personnage avec ses sorts lancés, les refus
et le temps de ses tours. Ce bilan sert à comparer les tests.

### Ordre proposé

1. Phase A en entier (sans le jeu) ; tous les tests automatiques passent.
2. B1, puis la correction de la lecture des sorts selon son résultat.
3. B2, B3, B5 (courts), puis B4 et B6.

## 4. Avancement (11/10, avant la première session)

| Réf. | État |
|---|---|
| A1 | Fait : une liste incomplète est complétée par la mémoire, et une lecture plus complète n'est jamais remplacée. Mémoire de Gela (23 sorts, captures de 14:30) et de Brawli (22 sorts, captures du 04/10) reconstruite. |
| A2 | Fait : Amplification une seule fois par tour. |
| A3 | Fait : Tir Éloigné seulement si une attaque en a besoin (l'ancien test du 27/09 a été adapté). |
| A4 | Vérifié : Maîtrise de l'Arc puis Flèche Magique x2 ; l'Iop : Amplification puis 3 Tempêtes de Puissance. |
| A5 | Vérifié : Flèche Explosive visée sur la case qui touche les 3 ennemis, jamais avec un allié dans la zone. |
| A6 | Pendant la session (B2) : il faut une capture du sort sélectionné. |
| A7 | Fait : repérage avec n'importe quel sort de classe de la barre (Bond pour l'Iop). |
| B1 | Outil prêt : `tools/arriere_plan_sorts.py` essaie 8 façons de faire défiler la liste en arrière-plan. |

## 5. Sessions de tests autonomes

- **Garde-fous** : chaque lancement est limité par `--combats N` et `--minutes M`. Le bot s'arrête de lui-même une fois
  hors combat, fenêtres de résultat fermées. F12 l'arrête à tout moment.
- **Bilan** : à la fin de chaque combat, une ligne par personnage au journal, et `logs/<date>/combats.jsonl`.
- **Déroulé** :
  1. Lancement court.
  2. Lecture du journal et du bilan.
  3. Correction, avec les tests automatiques.
  4. Relancement. Conclusion à la fin du temps donné.
- **Toujours demandé avant** : installer un paquet, aller sur internet, modifier quoi que ce soit hors du projet.

## 6. Session 1 (11/10, 15:43 à 15:56, forêt d'Astrub, 4 personnages, arrière-plan)

Trois lancements, 6 combats, chaque lancement arrêté de lui-même (`--combats 2`). Journaux :
`logs/20261011_154507`, `logs/20261011_154952`, `logs/20261011_155324` (détaillé), `logs/arriere_plan_sorts_20261011_154328`.

**Corrigé pendant la session**

| Problème | Correction | Résultat |
|---|---|---|
| Liste des sorts qui ne défile pas en arrière-plan | B1 : 8 façons essayées. Molette, flèche et clavier sont sans effet ; le clic sur le rail sous la poignée marche (environ 5 lignes) et la poignée glissée aussi. Le bot clique désormais sur le rail. | 4 personnages lus en entier (Gela 23 sorts, Brawli 22, Imthebest et Jesuisleking 21) |
| Fiche du monstre visé affichée à la place de la barre de sorts (la souris envoyée par message reste sur lui) ; le sort suivant ne partait pas | Après chaque lancer, « souris ici » envoyé dans le chat | Plus de Tempête de Puissance ratée une fois sur deux (5 s perdues à chaque fois) |

**Vérifié en jeu**

- Bonus de dégâts : Maîtrise de l'Arc, Tir Puissant, Amplification, Compulsion et Puissance, un lancer par tour.
- Tempête de Puissance x3.
- Flèche Explosive visée sur une case qui touche 2 ennemis.
- Tir Éloigné lancé seulement quand il fallait la portée (cible à 14 cases).
- Repérage de soi au 1er tour, avec Tempête de Puissance ou une flèche.
- Arrêt après N combats, et bilan de chaque combat.

**À corriger hors jeu (prochaine étape)**

| Réf. | Problème | Piste |
|---|---|---|
| P1 | Après un premier sort sur soi, le plan suivant perd des attaques à ligne de vue : Tempête abandonnée après Amplification (2 fois sur 4), Flèche Explosive qui change de cible puis plus rien | Rejoué hors jeu sur une carte sans obstacle, le moteur garde la bonne suite : c'est ce que le bot lit de la carte juste après le sort (obstacles, ligne de vue) qui change. À chercher dans les journaux détaillés de 15:54. |
| P2 | Sort refusé par le jeu (hors de portée ou sans ligne de vue) : en arrière-plan, pas de curseur à voir. Le bot attend 5 s que les PA baissent, puis réessaie jusqu'à 3 fois (Jesuisleking : 33 s sur 2 tours). | Après un lancer sans effet, le noter refusé pour cette cible depuis cette case, comme le curseur interdit au premier plan. |
| P3 | Épée du destin (portée 1) « non sélectionnable » 2 fois | Seuil de la portée affichée (A6), à mesurer sur les captures `sort_non_selectionnable`. |
| P4 | Bilan : un tour fini par la fin du combat n'est pas compté | Mineur. |
| P5 | Combats très courts dans la forêt (monstres morts en 1 ou 2 tours) | Pour la suite : des groupes plus forts. |

## 7. Session 2 (11/10, 16:09 à 16:39, forêt d'Astrub, 4 personnages, arrière-plan)

Cinq lancements bornés (`--combats 2 ou 3`, `--minutes 3 à 6`), 13 combats, aucun plantage, chaque lancement arrêté
de lui-même. Journaux : `logs/20261011_161228`, `logs/20261011_161539`, `logs/20261011_162457`,
`logs/20261011_162954`, `logs/20261011_163134`.

**Corrigé pendant la session**

| Problème vu | Cause | Correction | Vérifié |
|---|---|---|---|
| P2 : sort refusé en arrière-plan, 3 essais de 5 s puis sort banni | Pas de curseur interdit à voir en arrière-plan | Dès le premier lancer sans effet, le sort est noté refusé pour cette cible depuis cette case ; le bot passe au suivant | En jeu (run 1) |
| P1 : on ne savait pas pourquoi un sort prévu disparaissait du plan | — | Le journal dit pourquoi : « Plan changé : X sur (i, j) n'est plus prévu (raison) » | En jeu |
| Épée du destin jamais lancée | En arrière-plan, un clic sur la barre ne sélectionne pas le sort, et la 2e rangée demande Ctrl, impossible par message | Attaques de la 2e rangée écartées en arrière-plan, avec un avertissement (les bonus lancés sur soi y restent : clic droit) | En jeu |
| Brawli recommence son tour en plein milieu (« 6 PA ce tour (au lieu de 10) »), Tempête et Amplification relancées puis refusées | Un ennemi tué sort de la timeline, le portrait de Brawli glisse d'une place ; son cadre est trouvé un pixel à côté : écart 12,2, pris pour un autre portrait | Portrait comparé à un pixel de décalage près : 3,4 pour Brawli, 12,9 au moins entre deux personnages (seuil 10) | Test + runs 3 à 5 : plus de faux tour de Brawli |
| Tempête de Puissance tentée sur des ennemis à 6 cases (portée 3 à 4) | Les cases plus proches que la portée minimale ne sont pas colorées par le jeu : lues comme 11 obstacles autour de Brawli, ligne de vue prédite juste sur 8 % des cases, carte crue « à relief », 4 cases de marge | Obstacles lus seulement entre portée minimale et maximale (96 % de prédictions justes sur la même capture) | Test + run 3 |
| Gela : 4 refus de suite à 10 cases (portée 1 à 9), une autre case de la zone à chaque fois, ~5 s par refus | Marge d'une carte à relief : chaque case refusée est écartée seule | Refus au-delà de la portée : toute case aussi loin ou plus est écartée d'ici pour ce sort et ceux qui portent moins loin, jusqu'au prochain déplacement | Test (pas revu en jeu) |
| Brawli : carte crue « à relief » (70 %) avec Épée Céleste | Sort en ligne : les cases hors des diagonales ne sont pas colorées, lues comme obstacles (10 sur 12) | Pour un sort en ligne, obstacles gardés sur ses lignes seulement | Test + run 5 |

**À faire ensuite**

| Réf. | Problème | Piste |
|---|---|---|
| P6 | Détection du tour (question de l'utilisateur) : pendant mon tour, « encore mon tour ? » ne regarde que le portrait | Proposé, en attente d'accord : encore mon tour tant que le portrait OU le timer le dit, fin seulement si les deux disent non deux fois de suite ; « nouveau tour » du même personnage quelques secondes après son dernier sort sans PA pleins = même tour. Le chat montre aussi un bouton « Passer le tour » : peut-être un 3e marqueur, capture à demander. |
| P7 | Gela : F1 pendant l'animation du dernier sort, le portrait reste encadré 2 s, « Mon tour » à 0 PA (un F1 de plus, 0,4 s perdues) | Couvert par P6 (PA pas pleins). Sans gravité. |
| P8 | Run 3, combat 2 : les 3 autres n'ont pas rejoint Gela, « épée du combat introuvable » (captures `epee_introuvable`, `logs/20261011_162457`) | Regarder les captures : le combat de Gela est en haut de la carte, au milieu des arbres ; épée cachée ou mal reconnue ? |
| P9 | Lecture de la portée juste après un sort sur soi (Puissance) : l'animation (éclairs) masque des cases, lues comme obstacles | Attendre la fin de l'animation, ou ne pas apprendre d'obstacle sur les cases voisines de moi. |
| P10 | Chat : « Impossible de lancer ce sort actuellement. » à chaque refus | Lire le chat pour reconnaître un refus en arrière-plan sans attendre 5 s que les PA bougent. |
| P11 | Run 5 : 2e Tempête de Puissance refusée sur la même cible (PA inchangés, capture `sort_pas_parti`, `logs/20261011_163134`) | Cible sans doute tuée par la 1re : plan refait sur les combattants d'avant sa mort. Vérifier sur la capture. |
| P12 | Run 5 : Gela, F1 envoyé pendant l'animation du dernier sort, ignoré par le jeu ; le bot a repris le tour (4 PA, Flèche Explosive) mais comme un « nouveau tour » (compteurs remis à zéro), portrait encore jugé « changé » | Va dans le sens de P6. Regarder pourquoi le portrait a changé (captures du combat 2 de `logs/20261011_163134`). |
| P4, P5 | Bilan (tour fini par la fin du combat) ; combats trop courts dans la forêt | Inchangé. |

**Conseil pour jouer en arrière-plan** : mettre les attaques dans la 1re rangée de la barre. Gela : Flèche
d'Expiation (case 8) et Flèche Punitive (case 9) ; Brawli : Épée du destin (case 14). Les bonus lancés sur soi peuvent
rester en 2e rangée.

*Mis à jour en session 3 : la 2e rangée se sélectionne au pavé numérique (2e raccourci « 1 » à « 7 » des cases A à
G, réglé dans Raccourcis clavier) ; vérifié pour Gela. Plus besoin de déplacer les sorts si ce raccourci est réglé
sur le compte.*

## 8. Session 3 (11/10, 16:49 à 17:34, lancements arrêtés à 17:15, forêt d'Astrub, 4 personnages, arrière-plan)

Six lancements bornés, 14 combats, aucun plantage, cartes 2,-25 -> 1,-25 -> 1,-26 -> 1,-27 -> 1,-28 (toutes dans la
forêt). Journaux : `logs/20261011_165532` (arrêté : carte inconnue), `logs/20261011_165836`, `logs/20261011_170216`,
`logs/20261011_170447`, `logs/20261011_170935`, `logs/20261011_171158`.

**Fait pendant la session**

| Sujet | Ce qui a été fait | Vérifié |
|---|---|---|
| P6 : détection du tour (accord de l'utilisateur) | Pendant le tour d'un personnage : encore son tour si son timer OU son portrait le dit ; ni l'un ni l'autre : revu sur une 2e capture. « Mon tour » moins de 5 s après la fin du précédent sans les PA pleins : suite du même tour, compteurs gardés | Tests ; en jeu, aucun faux tour sur 14 combats |
| Le bouton « Passer le tour » comme 3e marqueur ? | Mesuré sur 300 captures de combat : visible aussi pendant le tour des autres. Pas un marqueur | — |
| Attaques de la 2e rangée en arrière-plan | Capture de l'utilisateur : les cases A à G ont « 1 » à « 7 » en 2e raccourci. Le bot les envoie au pavé numérique ; au premier tour, une attaque de la 2e rangée est sélectionnée puis annulée (Échap) pour vérifier. Deux échecs sur deux tours : attaques de la 2e rangée écartées, avec un avertissement | Gela : « Flèche d'Expiation sélectionné par num1 » (3 fois). Brawli : 2 échecs sur 2 tours (run 5), Épée du destin écartée : son compte n'a sans doute pas « 7 » en 2e touche de la case G |
| P8 : ralliement raté (« épée introuvable ») | Poignée cachée par un monstre debout devant l'épée ; l'icône des 3 personnages (0,95) et le pommeau (0,78) au même endroit suffisent désormais | Test ; aucune épée trouvée à tort sur 108 captures hors combat |
| Nom de carte illisible (« 2, -25 » devant un arbre) | Le « - » n'avait qu'une ligne blanche, la ligne à demi blanche suivante comptait comme une ombre pas assez noire. Les pixels à demi blancs ne comptent plus dans l'ombre | Test |
| Carte crue « à relief » (29 %) | Portée lue pendant son fondu : toutes les cases bleu clair (« sans ligne de vue »). Portée sans aucune case bleu foncé : pas lue | Test |
| P4 : bilan | Un tour fini sans F1 (fin du combat) est compté | Test |
| P13 : mesure | Le journal détaillé donne le temps entre le clic et « sort parti / pas parti » | Prochaine session |

**À faire ensuite**

| Réf. | Problème | Piste |
|---|---|---|
| P13 | En arrière-plan, les refus coûtent ~5 s chacun (Gela : 4 refus de suite, tour de 26,6 s) | Mesuré aux runs 5 et 6 (clic -> PA changés, 20 sorts) : sorts sur soi 0,5 à 0,7 s, Flèche Magique 0,7 à 2,6 s, Tempête de Puissance 0,5 à 2,3 s, Flèche Explosive 2,1 à 3,0 s ; un refus : 4,6 s. Un refus pourrait être conclu vers 3,5 s au lieu de ~4,5 s ; mieux : le message « Impossible de lancer ce sort actuellement. » du chat (P10) |
| P14 | Carte à relief, à portée : refus de ligne de vue enchaînés sur des cases voisines (Œil de Taupe 3 fois, puis Flèche Explosive) | Après un refus à portée, écarter aussi les cases voisines dans la même direction pour ce sort |
| P15 | Fin du combat pendant mon tour : 3 sorts « non sélectionnables » de suite avant de voir la fin | Vérifier qu'il reste un ennemi avant chaque sort |
| P16 | Brawli : le pavé numérique ne sélectionne pas sa case 14 | L'utilisateur vérifie Raccourcis clavier sur le compte de Brawli (« 7 » en 2e touche de la case G) |
| P5 | Combats très courts dans la forêt | Zones plus dures quand l'utilisateur le décidera |
| P17 | Carte 1,-27 : nom écrit sur des fleurs claires, région et zone pas reconnues (« région inconnue ») ; coordonnées lues (avant la correction du tiret : rien de lu) | Mineur : le chemin donne la zone. Seuil des mots à revoir sur fond clair |

## 9. Session 4 (11/10, 17:32 à 17:52, forêt d'Astrub, 4 personnages, arrière-plan)

Fenêtres du jeu fermées par l'utilisateur : tout relancé par le bot (`--connexion`). Launcher passé devant, Ctrl+8,
4 fenêtres placées sur l'écran 1080p, 4 personnages en jeu en 10 s, groupe formé en 4 s (37 s en tout). Puis un
lancement borné, 3 combats, aucun plantage. Journal : `logs/20261011_173400`.

| Sujet | Résultat |
|---|---|
| Pavé numérique (2e raccourci « 1 » à « 7 » réglé sur les 4 comptes par l'utilisateur) | Brawli : « Epée du destin sélectionné par num7 », lancée 2 fois au combat 1 ; Gela : Expiation par num1. P16 réglé |
| Lire le chat pour reconnaître un refus | Un clic hors de portée ou sans ligne de vue n'écrit RIEN dans le chat (refus du run 4 de la session 3) ; « Impossible de lancer ce sort actuellement. » n'apparaît que pour une limite de lancers, « Tu n'as pas assez de points d'action. » pour les PA |
| Conclure au refus à 3,5 s | Déjà le cas : after_cast 0,5 + cast_confirm 3,0 (config). Les 4,4 à 4,6 s mesurées sont le temps des captures et le ralentissement au hasard. Un succès a été vu à 3,7 s (Flèche Explosive) : pas de marge pour attendre moins. Plafond essayé puis retiré (sans effet) |
| Piste trouvée | Le journal du combat, en haut du chat (vert) : « Brawli-[Gal] lance Epée du destin. », « Prespic perd 47 PDV. » écrit dès que le sort part. Un sort confirmé par cette ligne (sans attendre les PA), et un refus conclu si elle n'arrive pas en ~1,5 s ; les dégâts réels en prime (rafale, vie des ennemis) |

**À faire ensuite** : P13 par le journal du combat (ci-dessus) ; P14, P15, P17 ; puis le chantier pods et banque
(captures et emplacements des banques à venir de l'utilisateur).

## 10. Essai de trajet vers la banque d'Astrub (11/10, 17:47 à 17:51, arrêté par l'utilisateur)

`--aller=4,-16 --position=1,-28`, toute l'équipe derrière Gela. Journal et 37 captures : `logs/20261011_174754`.

**Ce qui a marché** : itinéraire au plus court sur la référence du Monde des Douze ; les remparts au nord de la cité
découverts en route (pas de sortie vers le bas de 1,-23 à 4,-23, notées bloquées, itinéraire recalculé), entrée trouvée
en 5,-23 -> 5,-22, descente dans la cité jusqu'à 5,-18.

**La catastrophe, et pourquoi**

| Problème | Cause | Fait |
|---|---|---|
| Le principal tourne en rond entre 5,-15 et 5,-19 | Dans la cité, les joueurs qui passent font croire à un changement de carte ; les coordonnées lues en partie (« ?,-18 ») contredisaient le chemin (5,-17) mais le chemin l'emportait | Corrigé : chaque coordonnée lue en entier l'emporte sur le chemin ; lecture contradictoire : rien d'appris (test) |
| Un chiffre appris faux | Le « 5 » de « -15 » appris comme un 7 (position crue 5,-17) | Fichier `chiffre_7_appris_5_-17_4.png` retiré de data/chiffres (mis de côté) |
| Équipe dispersée (Jesuisleking en -1,-23 dès le début, Imthebest bloqué en 5,-21, Brawli en 5,-17) | Le principal n'attend pas ses suiveurs ; dans la cité, soleils occupés par des joueurs, clics qui ne prennent pas | À faire : le principal attend toute l'équipe sur chaque carte ; un suiveur perdu fait son propre trajet jusqu'à la carte du principal |
| « Menu principal » ouvert chez Brawli (Changer de perso / Déconnecter / Quitter Dofus) | Échap envoyé pour fermer un menu de joueur cru ouvert (fausse détection), sans rien à fermer | Corrigé : « Menu principal » est une fenêtre connue, refermée par Échap (= Annuler), jamais par un clic (test). La fausse détection du menu reste à trouver (pas de capture) |
