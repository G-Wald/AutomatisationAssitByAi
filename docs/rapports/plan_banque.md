# Pods, banque et échanges : plan (11/10)

D'après les captures de l'utilisateur du 11/10 (banque d'Astrub, échange Gela / Imthebest).

## Ce que le bot doit faire

1. **Surveiller les pods** de chaque personnage, hors combat (après chaque combat) : jauge orange de l'inventaire.
   Vers 90 % chez l'un d'eux : direction la banque.
2. **Aller à la banque d'Astrub** (carte 4,-16), toute l'équipe groupée (voir « Avant tout » ci-dessous).
3. **Entrer** : clic sur le soleil de la porte. Sa case est apprise au premier passage (le soleil qui n'est pas au
   bord de la carte), puis retenue (data/carte.json et notre référence du Monde des Douze : « Banque d'Astrub »).
4. **Vider le principal** : clic droit sur le banquier (Essiac d'Engrape) -> « Parler » -> « Consulter son coffre
   personnel » (402 kamas à chaque fois : le moins de visites possible) -> onglet ressources de l'inventaire
   (le transfert ne prend que ce qui est dans l'onglet : potions et équipements restent) -> menu de tri ->
   « Transférer tous les objets » -> fermer le coffre.
5. **Échanges** : pour chaque suiveur qui a des ressources, tant que le principal a de la place :
   - le principal : clic droit sur le portrait du suiveur dans le cadre du groupe (à droite) -> « Echange » ;
     il vérifie la fenêtre « En attente de la réponse de <suiveur> » (le bon personnage) ;
   - le suiveur : fenêtre « <principal> te propose de faire un échange. Acceptes-tu ? » : vérifie le nom, « Oui »
     (jamais « Ignorer ... pour cette session ») ;
   - le suiveur met ses ressources (onglet ressources de son inventaire), sans dépasser ce que le principal peut
     porter : chaque pile glissée dans l'échange (fenêtre de quantité : bouton « max », puis la coche pour valider ;
     pas de Ctrl + double-clic, impossible en arrière-plan) ;
   - les deux attendent que « Accepter » ne soit plus grisé, puis acceptent ; la fenêtre se ferme.
6. **Retour au coffre** quand le principal approche du maximum, puis échanges suivants, etc.
7. **Retour sur la zone de combat** quand tout le monde est vidé.

Position : « /p %pos% » dans le chat du groupe donne la position, en tout dernier recours (position perdue), jamais
à chaque carte.

## Avant tout : garder l'équipe groupée en trajet

L'essai du 11/10 (rapport des combats, section 10) a dispersé l'équipe dans la cité.
- Fait (11/10, testé hors jeu) : un suiveur perdu fait son propre trajet jusqu'à la carte du principal, et le
  principal ne repart pas avant (team/loop.py, _regroup) ; s'il ne peut pas la rejoindre, le bot s'arrête.
- Fait : coordonnées lues en partie qui contredisent le chemin : la lecture l'emporte (fausses arrivées dans la cité).
- Fait : « Menu principal » refermé par Échap s'il s'ouvre.
- Reste : la fausse détection de « menu ouvert » (Échap qui a ouvert le Menu principal), sans capture du moment.

## Ce qu'il faut reconnaître à l'écran (hors jeu, sur les captures)

| Élément | Sur quelle capture |
|---|---|
| Jauge de pods (remplissage orange) | inventaire de la banque et de l'échange (11/10) ; inventaire ouvert hors banque : capture à faire |
| Menu du banquier « Parler », dialogue « Consulter son coffre personnel » | 11/10 |
| Fenêtres « Coffre » + inventaire, menu « Transférer tous les objets » | 11/10 |
| Cadre du groupe (portraits à droite), menu « Echange » | 11/10 |
| « En attente de la réponse de X », « X te propose de faire un échange » (noms lus en Verdana) | 11/10 |
| Fenêtre d'échange, bouton « Accepter » grisé / actif | 11/10 |

## Réponses de l'utilisateur (11/10)

- Piles : glisser la pile dans l'échange ; une fenêtre demande la quantité : bouton « max », puis la coche.
- Banque : cliquer sur l'onglet ressources avant « Transférer tous les objets » : seul le contenu de l'onglet part.

## Encore à fournir

- Raccourci de l'inventaire et capture de sa jauge de pods hors banque (et l'infobulle des pods si elle existe).
- Capture de la fenêtre de quantité (bouton « max », coche).

## Tests en jeu (sessions)

1. Trajet groupé jusqu'à 4,-16, entrée dans la banque (case de la porte apprise).
2. Banquier, coffre, « Transférer tous les objets », fermeture.
3. Un échange suiveur -> principal (piles comprises).
4. Le cycle complet avec la limite de pods.

## Session du 11/10, 18:23 à 18:43 (premiers essais en jeu)

| Étape | Résultat | Comment |
|---|---|---|
| Pods | Lus sur l'anneau de la minimap hors combat (`ui.pods_fill`), notés au tour des fenêtres : Brawli 90 % (« il faut passer à la banque »), Gela 30 %, Jesuisleking 18 %, Imthebest 0 % | Jauge orange depuis le haut, sens horaire, le haut caché par le cœur ; test sur capture |
| Trajet groupé 3,-16 -> 4,-16 | Les 4 en 6 s | `--aller=4,-16` |
| Porte de la banque | Soleil en case (23, 9) de la carte 4,-16 (pas au bord : 0,75 au modèle du soleil) ; les 4 entrés (zone de jeu : 0 % -> 57 % de noir, mêmes coordonnées dedans) | `tools/entrer_porte.py` |
| Banquier | Clic GAUCHE sur lui (le clic droit à côté ouvre le menu de Flash, qu'Échap ne ferme pas : il ouvre le Menu principal) ; « Parler » 0,97, « Consulter son coffre personnel » 0,93 (Verdana 18), « Coffre » 0,95 (Verdana gras 20) | `tools/banquier.py` ; les banquiers se déplacent : position donnée à la main pour ce test, détection à faire (un modèle d'une seule pose ne suffit pas, grillage du comptoir devant) |
| Transfert | Onglet ressources (3e icône, bûche), bouton flèches (dernière icône) = menu trier / transférer, « Transférer tous les objets » (modèle 1,00, ligne voisine 0,65) : 10 piles au coffre, il en reste une (pas une ressource transférable ?) | `tools/coffre_transfert.py` |

**Reste à faire** : reconnaître le banquier où qu'il soit ; mettre ces étapes dans le bot (case de la porte retenue dans
data/carte.json, banque dans la référence) ; les échanges suiveur -> principal (glisser une pile, fenêtre de quantité
« max » + coche, « Accepter ») ; le cycle complet avec la limite de pods.

## Session du 11/10, 18:44 à 18:51 (échanges)

| Étape | Résultat | Comment |
|---|---|---|
| Ouvrir l'échange | Chez Gela, clic GAUCHE sur le portrait de Brawli (cadre du groupe, 1890,169) -> « Echange » (modèle 1,00) -> « En attente de la réponse de » 0,87 + « Brawli-[Gal] » 0,94 (Verdana 22) ; chez Brawli « te propose de faire un échange » 0,89 + « Gela-[A13] » 0,95 -> « Oui » 0,98 ; fenêtre d'échange des deux côtés (« Accepter » 0,90) | `tools/echange.py`, 2 fois sans erreur |
| Menu de Flash | Un clic DROIT envoyé (portrait, à côté du banquier) ouvre le menu de Flash (« DOFUS RETRO Client v1.49.6 ») ; il se ferme par un clic gauche dans le noir hors de la pièce (pas par Échap, qui ouvre le Menu principal) | Jamais de clic droit sur l'interface en arrière-plan |
| Glisser une pile | Ne marche pas par messages : la case de l'offre s'éclaire, rien n'est posé, pas de fenêtre de quantité | `tools/echange_pile.py` |
| Double-clic | Marche : un objet par double-clic ; le drapeau Ctrl dans le message est ignoré (pas de pile entière) | |
| Tout donner | Double-clic répété sur la 1re case de l'onglet ressources : quand une pile est vide, la suivante prend sa place ; 20 doubles-clics en 5 s = 20 objets, aucun perdu (~4 objets/s) | Brawli (~800 ressources) : ~3 min par échange en arrière-plan |
| Valider | « Accepter » actif = orange vif autour du texte (0,64 ; grisé : 0) ; chez le suiveur puis le principal ; fenêtre fermée des deux côtés | `tools/echange_valider.py`, 2 fois |

**Question à l'utilisateur** : piles entières plus vite = fenêtre du suiveur devant quelques secondes (vrai Ctrl +
double-clic), ou tout en arrière-plan à ~4 objets/s.

**Touche E (découverte de l'utilisateur)** : tenue par message en arrière-plan, elle cache bien les autres personnages
(vérifié dans la banque : il ne reste que le sien). Ajoutée aux clics sur les soleils du principal et des suiveurs
(`travel.hide_players_key`, « e » par défaut, vide pour la couper) ; testé hors jeu, pas encore en trajet.

**Piles entières (19:00, question de l'utilisateur)** : le glisser-déposer par messages ne marche pas, même fenêtre
devant. Ce qui marche : fenêtre du suiveur passée devant, vrai Ctrl au clavier (keybd_event, ~0,3 s) pendant le
double-clic envoyé par message : la pile entière (9 graines) passe d'un coup, sans toucher à la souris. Échange validé
ensuite des deux côtés.

## Modes pour vider les personnages (demande de l'utilisateur, 11/10, 19:0x)

Réglage `banque.mode` (et case à cocher / liste dans l'interface plus tard) :

| Mode | Ce que fait le bot | Coût |
|---|---|---|
| `un_par_un` (par défaut) | Échange suiveur -> principal, double-clic répété sur la 1re case de l'onglet ressources, tout en arrière-plan | ~4 objets/s ; souris et clavier jamais pris |
| `ctrl` | Échange, fenêtre du suiveur devant, vrai Ctrl (~0,3 s) pendant chaque double-clic : une pile d'un coup | ~10 s par échange ; Ctrl réellement enfoncé à chaque pile |
| `chacun` | Pas d'échange : chaque personnage ouvre son propre coffre (402 kamas chacun) et y transfère l'onglet ressources | 402 kamas par personnage et par visite |

## Session du 11/10, 19:04 à 20:00

| Sujet | Résultat |
|---|---|
| Mode « chacun » | Brawli : banquier trouvé seul (taches dorées les plus grandes derrière le comptoir), coffre, onglet ressources vidé (28 piles), pods 90 % -> 22 % ; coût 76 kamas (le prix dépend du contenu du coffre) |
| Sortie de la banque | Soleil intérieur, case (8, 26) |
| Incident | En trajet dans la cité, un suiveur a pris le Menu principal (ouvert par Échap) pour un menu de joueur ; son clic « ailleurs » est tombé sur « Déconnecter » : fenêtre « Cette action va quitter ta partie en cours » chez trois personnages, fermées par « Non » vérifié |
| Corrigé | Échap seul pour fermer un menu (Menu principal refermé par Échap, jamais de clic) ; fenêtre « quitter » toujours fermée par « Non » ; sortie notée bloquée dans data/carte.json seulement si les coordonnées de la carte sont lues (sinon pour le trajet seulement), jamais par le trajet d'un suiveur ; recherche des sorties E tenue (idée de l'utilisateur) ; même carte jugée sur le décor (0,8 d'écart sur la même carte, 41 sur une autre) avant qu'un suiveur ne suive, au départ, et à la fin d'un regroupement |
| Mémoire nettoyée | data/carte.json : 4,-20 (4 côtés « bloquée »), 3,-20, 5,-17 à 5,-20 retirés (copie de sauvegarde gardée) |
| Trajet 4,-20 -> 1,-26 | 21 cartes, équipe groupée du début à la fin (contournement de la cité par l'ouest) |
| Combats en forêt | 3 combats sans souci (Épée du destin de Brawli au pavé numérique) |
| Trajet forêt -> banque | Arrêté : le clic de Gela n'a pas pris, ses suiveurs sont passés seuls, il est reparti par un autre côté. Corrigé : principal resté sur sa carte -> les suiveurs partis avec lui sont vérifiés au décor, perdus s'ils sont ailleurs (position : la carte où menait le soleil) |
| Lecture « 1, -23 » -> « -1,-23 » | Un éclat clair sous la ligne pris pour un tiret (conséquence de la correction du tiret fin). Corrigé : un tiret doit être à mi-hauteur des chiffres (test sur la capture) |

## Trajet de consolidation dans la cité d'Astrub (11/10, 20:13)

| Sujet | Résultat |
|---|---|
| Recherche des sorties | Touche A tenue sous E à chaque carte, puis soleils en secours ; attente d'arrivée 20 s (`travel.walk_timeout`) |
| Cause de l'échec | **Pas de modèle du chiffre 9** : un 9 ressemble à 0,83 au modèle du 8 (seuil 0,8), lu « 8 ». Toute la rangée -19 de la cité était prise pour la rangée -18 : faux « saut du jeu » -2,-18 <-> -2,-20 confirmé, rempart de -1,-19 (soleil de gauche derrière le mur) noté bloqué sur -1,-18, puis plus aucun chemin |
| Corrigé | Modèle `assets/carte/chiffres/chiffre_9_cite.png` découpé dans la capture (« -1, -19 » lisible à l'œil) : 9 à 0,90-0,96, 8 à 0,80-0,83 ; test sur deux captures (-1,-19 et -2,-19) |
| Mémoire nettoyée | data/carte.json : -1,-18 et -2,-18 retirés, liaison -2,-20 bas retirée (copie de sauvegarde gardée) |
| Rempart | Une carte peut avoir un soleil de chaque côté du mur : il n'est atteignable que depuis la bonne moitié de la carte. Une sortie « bloquée » dépend donc du côté par lequel on est arrivé |

## Session des passages d'Astrub (11/10, 20:46 à 21:13)

Passages donnés par l'utilisateur : ouest -1,-20, nord 5,-22, est 7,-19, sud 4,-15, sud-ouest -1,-14. Équipe de 4,
trajets de carte en carte (`--aller`), touche A sous E à chaque carte.

| Passage | Résultat |
|---|---|
| Ouest -1,-20 <-> -2,-20 | Passé dans les deux sens (12 s, 4 s) |
| Nord 5,-22 <-> 5,-23 | Passé dans les deux sens |
| Est 7,-19 <-> 8,-19 | Passé dans les deux sens (le « 8 » de 8,-19, inconnu sur ce fond, appris en route) |
| Sud 4,-15 -> 4,-14 -> 4,-13 | Passé (4,-14 est encore étiqueté « Cité d'Astrub ») |
| Sud-ouest -1,-13 -> -1,-14 -> -1,-15 -> -1,-16 | Passé |
| Traversées de la cité | -1,-20 -> 5,-22, 5,-22 -> 7,-19, 7,-19 -> 4,-15 : arrivées, équipe groupée |

Murs trouvés (notés dans data/carte.json) : -1,-21 haut ; 5,-15 gauche ; rangée -14 (0 à 3,-14 : gauche et haut),
qui ne s'atteint que par le bas. Chaque mur coûte 60 s (3 essais de 20 s) : ~9 min perdues dans la session.

Problèmes trouvés et corrigés :
- **Suiveurs crus perdus en 5,-21** alors qu'ils étaient avec le principal : le mot « Astrub » mal découpé sur un mur
  clair faisait juger les régions différentes malgré les mêmes coordonnées. Les régions ne départagent plus que si les
  deux noms sont reconnus (`MapLabel.same_map`).
- **Sortie bloquée effacée** : en 1,-13, le clic du principal sur le soleil de droite n'a pas pris (5 fois), alors que
  les suiveurs passaient à chaque fois. Leur trajet de regroupement (2,-13 -> 1,-13) réécrivait « 1,-13 droite ->
  2,-13 » sur la sortie que le principal venait de noter bloquée. Un trajet de regroupement n'écrit plus rien, et le
  lien d'en face déduit n'écrase plus une sortie vue inutilisable. Fausse sortie « 1,-13 droite bloquée » retirée de
  data/carte.json.

À faire : voir plus vite qu'un soleil est inatteignable (le personnage ne bouge pas) ; comprendre pourquoi le
principal ne passait pas en 1,-13 droite quand les suiveurs passaient (captures logs/20261011_210507).

## Choix du soleil sur les cartes coupées par un rempart (11/10, 21:50 à 22:13)

Demandes de l'utilisateur : un soleil inatteignable vu en 2 à 3 s au lieu de 60 s ; sur les cartes coupées par un
rempart, il y a parfois deux soleils d'un même côté (un de chaque côté du mur) : cliquer celui de notre côté.

| Changement | Détail |
|---|---|
| Tous les soleils d'un côté | `find_suns` / `find_marked_exits(every=True)` gardent chaque soleil distinct (plus de 3 cases d'écart) ; avant, un seul par côté |
| Ordre d'essai | Le plus proche de la case d'arrivée estimée (en face du soleil pris sur la carte précédente) ; mémoire `soleils_depuis` : celui qui a marché pour le même côté d'arrivée ; `plusieurs` : côtés à plusieurs soleils (la case gardée seule ne suffit pas) |
| Détection rapide | Vue réduite 160 x 90, autres joueurs cachés (E tenu pendant la marche) : en marche, 20 à 41 cases changent par capture, immobile 0. Immobile 2 s (après 1 s) sans changer de carte : soleil inatteignable. A marché puis s'est arrêté : allé au plus près, pas de second clic. Réglages `travel.still_after`, `still_for`, `motion_cells` |
| Sorties bloquées | Soleil vu mais inatteignable : noté pour le côté d'arrivée seulement (`bloquees_depuis`) ; aucun soleil même avec A : pour tous ; côté d'arrivée inconnu (début du trajet) : pour ce trajet seulement. Itinéraire calculé sur (carte, côté d'arrivée) |
| Principal bloqué par le jeu | Les suiveurs passent un soleil que le principal ne passe pas : la sortie n'est pas bloquée ; deux fois de suite, le bot s'arrête et le dit (défi puis quitter le combat le débloque) |
| Suiveurs | Reclic seulement s'ils sont immobiles depuis 1,5 s (un reclic tombé sur la nouvelle carte a fait descendre Imthebest en 5,-15) |
| Touches | A, E et Z relâchées au début et à la fin du bot (une touche tenue lors d'un arrêt brutal) ; le code relâchait déjà toujours ses touches |

Résultats en jeu (4 personnages, toujours groupés) :

| Trajet | Cartes | Durée | Soleils inatteignables |
|---|---|---|---|
| 2,-14 -> 1,-14 (deux soleils à gauche) | 1 | 14 s | (1,7) en 3,5 s x 2, puis (1,29) |
| 1,-14 -> -1,-14 | 6 | 58 s | 0,-14 gauche inatteignable de la droite et du bas : contourné par -1,-13 |
| -1,-14 -> 3,-14 | 4 | 28 s | aucun |
| 3,-14 -> -1,-19 | 9 | 64 s | aucun |
| -1,-19 -> -1,-17 (deux soleils en bas) | 2 | 22 s | (3,31) vu en 5 s, puis (17,31) |
| -1,-20 -> 8,-19 (ouest -> est) | 10 | 70 s | 5,-19 droite : 6 s |
| 8,-19 -> 4,-13 (contour est) | 10 | 75 s | aucun |
| 4,-13 -> 5,-23 (sud -> nord) | 12 | 75 s | 4,-19 haut : 6 s |
| 5,-23 -> -2,-20, -2,-20 -> -1,-13 | 10 + 8 | 71 s + 52 s | aucun |
| -1,-13 -> 4,-16 (banque) | 8 | 57 s | aucun |
| 4,-16 -> 1,-27 -> 4,-16 -> 1,-27 | 15 + 17 + 15 | ~2 min chacun | aucun ; Imthebest séparé une fois (corrigé, puis revérifié) |

Murs de 60 s notés cet après-midi : plusieurs étaient faux (2,-14 gauche, 3,-14 haut : un second soleil ou un essai
raté). Retirés de la mémoire et retestés : notés maintenant selon le côté d'arrivée.

Comparaison de la référence avec la langue du client (agent, `docs/rapports/comparaison_reference.md`) : Astrub
concorde carte par carte (Cité 75, Contour 46) ; passages vus par le bot hors de la liste de portes de l'utilisateur :
0,-14 à 3,-14 <-> rangée -13 (les cartes -14 sont au sud du mur, encore étiquetées « Cité d'Astrub ») et
-1,-21 <-> -1,-22 (pas revu aujourd'hui).
