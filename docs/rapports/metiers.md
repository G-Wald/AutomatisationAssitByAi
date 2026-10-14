# Métiers de récolte (08/10)

Recherche (données Dofus Rétro seulement) et conception. Aucune donnée importée : chaque source attend ton accord.

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
