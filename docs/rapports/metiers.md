# Métiers de récolte (08/10)

Recherche (données Dofus Rétro seulement) et conception. Aucune donnée importée : chaque source attend ton accord.

## Validé en jeu le 27/10 (écoute du serveur)

Toutes les données ci-dessous viennent du serveur du jeu (écoute passive, Npcap) et des fichiers du client déjà copiés
dans `data/client_retro` ; rien n'est pris sur internet.

### Qui a quel métier

Annoncé par le serveur à la connexion : `JS|métier;compétences` et `JX|métier;niveau;...`. Pour l'avoir, on ferme la
fenêtre et on relance `bot.connection`, qui reconnecte le compte (environ 20 s par fenêtre, moins de 2 min pour 7).

| Personnage | Métiers (niveau) | Arme équipée | Outil de récolte dans le sac |
|---|---|---|---|
| Fourboun | Bijoutier 41, Sculpteur de Baguettes 50, Bricoleur 35, Tailleur 32, Alchimiste 2 | Dagues du Bandit | — |
| Eldieso | aucun | Agride (hache) | — |
| Walitcoin | Boulanger 1 | Marteau du Bouftou | — |
| Waldarch-[Gal] | Paysan 3 | Agride (hache) | — |
| Pesopepe | Paysan 15 | Dagues du Bandit | — |
| Enu-six-[Gal] | Paysan 1, Chasseur 1 | Marteau du Bouftou | Faux de l'apprenti paysan |
| Elseiso | **Bûcheron 23**, Chasseur 1 | Agride (hache) | — |
| Fourbodeux | **Mineur 10**, Pêcheur 1, Chasseur 1 | Dagues du Bandit | — |

Le sac complet vient du même moment (`ASK|id|nom|...|objets`, un objet par `uid~modèle~quantité~place~effets`, place 1 =
arme). Les coffres de la banque ne sont pas vus.

### Récolter : validé (Elseiso, Bûcheron 23, carte -1,-16)

- **Où sont les ressources** : la carte arrive avec sa clé (`GDM|numéro|date|clé`), toujours la même pour une carte. Le
  mapData du fichier de la carte, déchiffré avec cette clé (`tools/cles_cartes.py`), donne l'objet interactif de chaque
  case ; la langue du client (IO, SK) donne la ressource, le métier et l'action. Exemple -1,-16 : 4 Frênes, 2
  Châtaigniers, 1 Noyer.
- **Leur état** : le serveur n'envoie que les ressources qui ne sont pas debout (`GDF|case;état;?` : 2 en cours, 3
  récoltée, 4 repousse).
- **Récolter** : clic sur la case de la ressource (joueurs cachés : E tenue), menu, clic sur « Couper ». Le serveur
  annonce le début (`GA1;501;récolteur;case,durée` : 9,7 s pour un Frêne au niveau 23), la fin (`GDF` état 3), la
  quantité (`IQ…|3`), l'objet (`OAKO…`) et l'expérience du métier (`JX`). 12 à 16 s par arbre, marche comprise.
- **Code** : `bot/brain/harvest.py` (ressources d'une carte, récolte une par une) ; essai : `python -m tools.recolte
  --personnage Elseiso --metier Bûcheron --niveau 23 --carte -1,-16`. 4 arbres récoltés sur 4 debout, les 3 déjà coupés
  écartés.
- Clés apprises : `data/cles_cartes.json` (30 cartes ce soir) ; il faudra que le bot les retienne à chaque carte.
- **Mini-jeux de la 1.47** : pas vus, la récolte se fait comme avant.

### Équiper l'outil : geste validé (Enu-six, Faux de l'apprenti paysan)

- Inventaire : touche I. Équiper : **vrai double-clic** sur l'objet (en arrière-plan, deux clics séparés ne font que le
  sélectionner). Le serveur confirme : `OM<uid>|1` (dans l'emplacement d'arme), l'ancienne arme repart au sac ; double-
  clic sur l'ancienne arme pour la remettre (`OAKO…~1~1`).
- L'arme compte dans les caractéristiques (Enu-six : 364 -> 334 PDV avec la faux) : à remettre avant un combat.
- Trouver l'objet dans le sac : la liste des types (« Tous types ») ne propose que les types présents ; choisir « Faux »
  laisse la faux seule, dans la première case. Code : `bot/brain/equipment.py` (`equip(game, "Faux")`), double-clic
  réel ajouté à l'entrée en arrière-plan (`BackgroundInput.double_click`).
- Essai complet (`tools.recolte ... --outil Faux --arme Marteau`) : faux équipée puis marteau remis, confirmés par le
  serveur les deux fois ; 1 Blé récolté sur 8 visés. Les autres : menu sans « Faucher » (Blé déjà fauché : l'outil
  démarre sans les états de la carte) ou « Faucher » cliqué sans récolte annoncée. À reprendre.
- « Faucher » se lit à 0,71 (police du jeu) : seuil de l'action du menu à 0,68 (sans menu : 0,37 à 0,52).

### 30/10 : blé réglé

- Essai : 4 Blés fauchés sur 4 par Enu-six (7 au total ce jour-là), faux équipée puis marteau remis, confirmés.
- Les épis se chevauchent : un clic sur une case prend souvent l'épi dessiné par-dessus (case 100 -> 129). Toute récolte
  annoncée après le clic compte, à la case donnée par le serveur.
- Action du menu cherchée seulement à côté du clic (un « Faucher » à 0,72 loin du blé était cliqué pour rien).
- Fenêtre « Ton métier Paysan passe niveau 2 » : fermée après chaque récolte. Elle bloquait l'inventaire, et sa lecture
  aurait donné « niveau 2 » comme niveau du personnage : corrigé (`read_level_up` ignore les métiers).
- Inventaire : la liste des types et la grille du sac gardent leur place d'une ouverture à l'autre ; remises en haut
  (molette) avant de chercher. Un type sous le bas de la liste (le blé ajoute « Céréale ») : molette vers le bas.
- Clic droit sur une ressource : même menu que le clic gauche, pas de récolte directe (essai sur un Châtaignier).

### 30/10 : échanges entre nos personnages (essai, annulé à la fin)

- `ERK<de>|<à>|1` (demande, aux deux), `ECK1` (échange ouvert), `EMKO+<uid>|<quantité totale dans mon offre>` (moi),
  `EmKO+<uid>|<quantité>|<modèle>|` (l'autre), `EV` (fermé).
- Pile entière en arrière-plan (Ctrl impossible) : glisser LENT de la pile vers l'offre -> fenêtre de quantité
  [min] [max] [1] [✓] ; « max » puis ✓ = toute la pile (`EMKO+…|10`). Le double-clic n'en met qu'une.

### 01/11 : tournée de récolte (mode récolte du bot solo)

- Commande :
  `python -m bot --title Elseiso --arriere-plan --lecture memoire --monde douze --combat-zone 3,-24 --recolte Bûcheron:23 --minutes 35`
- Sur chaque carte de la zone :
  - récolte des ressources du métier jusqu'au niveau donné (Frêne 1, Châtaignier 10, Noyer 20 pour Bûcheron 23) ;
  - puis carte voisine de la zone, celle vue il y a le plus longtemps : même parcours que `--zone`, sans attaquer de
    groupe.
- Combats subis : joués par la boucle habituelle. La récolte s'arrête dès qu'un combat commence.
- Pods :
  - au-delà de 90 % (annoncés par le serveur), plus de récolte ;
  - trajet jusqu'à la banque d'Astrub (4,-16), même passage au coffre que l'équipe (`BankVisit`), puis retour dans la
    zone ;
  - un passage raté : arrêt aux prochains pods pleins.
- Forêt d'Astrub : 42 cartes, 5 à 18 arbres par carte (Frêne, Châtaignier, Noyer d'après la carte du monde).
- Premiers essais :
  - 7 arbres en 1 min 30, environ 13 s chacun, marche comprise ;
  - Elseiso était déjà à 95 % de pods avec le butin des combats ;
  - passage à la banque : 2 min 15 de trajet, pods de 100 % à 39 % en 7,8 s au coffre, retour dans la zone.
- **Corrigé** : une récolte d'un autre joueur sur la carte (`GA1;501` d'un autre identifiant) était comptée comme la
  nôtre. Seules nos récoltes comptent maintenant.

## Ce qui est établi pour Rétro

1. **Métiers de récolte** : Bûcheron, Paysan, Mineur, Alchimiste, Pêcheur.
   - On les apprend auprès du Contremaître Ikul, à Incarnam [3,3].
   - Le Mineur s'apprend aussi auprès de Chipo Atufe, dans la mine d'Astrub [9,-23].
2. **L'outil est obligatoire** : « un joueur ne peut pas exercer de métier s'il n'a pas cet outil » (JOL Rétro).
   - Il s'équipe depuis l'inventaire, très probablement dans l'emplacement d'arme (Solomonk range les outils parmi les
     armes) : à confirmer en jeu.
   - Pêcheur : canne offerte à Incarnam. Paysan : faux vendue par Emia Elliesol.
   - Le geste pour équiper (double-clic ou glisser) n'est décrit nulle part.
3. **Récolter** : clic sur la ressource, puis un menu, puis l'action. Noms des actions tirés du client Rétro :

   | Ressource | Action |
   |---|---|
   | Frêne, Châtaignier | « Couper » |
   | Blé, Orge | « Faucher » |
   | Lin | « Faucher » (Paysan) ou « Cueillir » (Alchimiste) |
   | Petits poissons | « Pêcher » |
   | Puits | « Puiser » |

   L'action du Mineur n'est pas confirmée.
4. **Fin de récolte** : le nombre d'unités récoltées apparaît en rouge au-dessus du personnage.
5. **Durée et quantité** : la durée baisse avec le niveau du métier. La quantité maximale augmente de 1 à chaque niveau
   finissant par 5 ou 0.
6. **Blocages** :
   - inventaire plein, et la récolte est perdue (+5 pods par niveau de métier) ;
   - un protecteur de ressource apparaît environ 1 fois sur 40 et lance un combat ;
   - niveau de métier trop bas.
7. **Niveaux requis** : déjà dans notre carte du monde (config/monde). Un désaccord : le Riz est au niveau 20 chez
   xixou et 50 chez JOL.
8. **Point critique, la mise à jour 1.47 (septembre 2025)** :
   - la limite de 3 métiers est supprimée ;
   - Ankama a annoncé une refonte de la récolte avec des **mini-jeux** (« cliquer successivement certains endroits du
     minerai »), présentée comme un moyen de détecter plus facilement les tricheurs.
   - **On ne sait pas si ces mini-jeux sont actifs en 1.49.6 : c'est la première chose à vérifier.** S'ils le sont,
     chaque récolte demande une étape de plus, et c'est précisément un outil anti-bot.
9. **Écartés (Dofus 2)** : « 3 secondes, pas d'outil », « pas d'outil pour les arbres ».
10. **Non trouvés** :
    - le temps de repousse ;
    - si le personnage marche seul jusqu'à la ressource (très probable) ;
    - ce qui se passe si un autre joueur récolte la même ressource ;
    - la fenêtre des métiers et son raccourci.

## Ce qui existe déjà dans le bot

- **Carte du monde** (`config/monde`) : pour chaque carte, les ressources avec nom, nombre, métier et niveau, mais sans
  leur case. Exemples dans le Monde des Douze : Blé sur 110 cartes, Frêne sur 128.
- **Déplacements** : trajets de carte en carte (`travel.py`) et mémoire des cartes (`data/carte.json`).
- **Menus** : détection des menus ouverts par un clic (`popups.py`), et le même schéma « clic, menu, entrée » que pour
  attaquer un groupe.
- **Interface** : le bouton « Récolte », grisé.

## Conception proposée

1. **Repérer les ressources sur la carte** (`perception/resources.py`) :
   - une image modèle par ressource et par état (mûre / récoltée), cherchée sur la carte, puis convertie en case ;
   - les cases trouvées sont retenues par carte, et vérifiées directement au passage suivant.
2. **Interface de récolte** (`perception/harvest_ui.py`) : entrées de menu (Couper, Faucher…), chiffres rouges,
   fenêtre de mini-jeu éventuelle, inventaire (emplacement d'arme, outil, pods).
3. **La récolte, pas à pas** (`brain/harvest.py`) :
   - vérifier l'outil ;
   - trouver les ressources ;
   - prendre la plus proche de la précédente ;
   - cliquer (jamais au même pixel) ;
   - attendre le menu, cliquer l'action ;
   - attendre la fin : chiffres rouges, ressource passée à « récoltée », ou délai dépassé.

   En cas de problème :
   - pas de menu : la ressource est ignorée 60 s ;
   - protecteur : le combat est joué par la boucle actuelle, puis on reprend ;
   - mini-jeu inconnu : arrêt avec capture ;
   - pods au-dessus de 90 % : arrêt, puis la banque quand elle sera faite.
4. **Équiper l'outil** (`brain/equipment.py`) : ouvrir l'inventaire, trouver l'outil, l'équiper avec le geste
   validé, vérifier, fermer.
5. **Circuit de cartes** :
   - cartes où se trouve la ressource visée, au niveau du métier, hors zones interdites ;
   - choix de la suivante selon la quantité attendue et le temps depuis la dernière récolte (repousse apprise en
     observant) ;
   - les mines et la pêche (intérieurs, portes) dans un second temps ;
   - pour commencer : Blé, Frêne et Lin à Incarnam.
6. **Branchements** : un mode « récolte » dans la boucle (sans attaquer de groupes), une section `harvest` dans les
   réglages, le bouton de l'interface réactivé.
7. **Arrière-plan** : rien ne repose sur le survol ; plusieurs points de clic par ressource. En équipe : un personnage
   récolte, les autres suivent de carte en carte.

## Captures à faire (plein écran 1920 x 1009)

1. **Ressources sur la carte** : pour Blé, Frêne, Lin (puis Orge, Châtaignier), 2 ou 3 cartes où elles sont mûres, avec
   le personnage à des endroits différents. Puis les mêmes cartes après récolte, et pendant la repousse s'il y a des
   états intermédiaires.
2. **Les menus** sur chaque ressource : outil équipé, sans outil, puis niveau trop bas.
3. **Pendant une récolte** : animation, barre de progression s'il y en a une. Juste après : chiffres rouges et message
   dans le chat.
4. **Toute fenêtre de mini-jeu, étape par étape. C'est la priorité absolue.**
5. **Cas particulier** : une ressource déjà récoltée, ou en cours de récolte par un autre joueur.
6. **L'inventaire** :
   - la touche qui l'ouvre ;
   - l'emplacement d'arme, vide puis avec l'outil ;
   - le geste pour équiper ;
   - la barre de pods presque vide et presque pleine, et les messages « inventaire plein ».
7. **La fenêtre des métiers** et son raccourci.
8. **Pêche** : un point d'eau et son menu. **Mine** (plus tard) : l'entrée et un filon.

## Données proposées (à approuver)

| | Source (Rétro) | Champs | Intérêt |
|---|---|---|---|
| A | xixou.io, données de carte d'Incarnam (issues du client Rétro) | nom, type, métiers et **actions** de chaque ressource | savoir quelle entrée de menu attendre ; petit et sûr |
| B | Déjà importé (config/monde) | ressources par carte | à utiliser tel quel |
| C | xixou.io, calculateurs (1.49) | XP par ressource | journal et planification, faible priorité |
| D | dofus-retro.co | nombre de cartes par ressource | recoupement facultatif |
| E | Fichiers de cartes du client | case exacte de chaque ressource | déconseillé : fichiers chiffrés ; le bot apprendra lui-même les cases |
