# Recherche Internet : monstres agressifs et passages entre cartes (23/10/2026)

Deux questions :

1. Quels monstres de Dofus Retro agressent les joueurs, où, à quelle distance et à quelles conditions ?
2. Existe-t-il des données publiques sur les sorties des cartes (soleils, cases de changement de carte) et sur les
   cases praticables, et sont-elles fidèles au serveur officiel ?

**Aucune donnée n'est intégrée au bot.** Ce rapport propose des sources. Chaque type de données doit être approuvé
par l'utilisateur avant d'être utilisé. Aucun fichier du code, de `config/` ni de `data/` n'a été modifié. Les
scripts d'analyse et les petits fichiers téléchargés sont dans le dossier temporaire de la session, pas dans le
projet.

## Résumé

| Question | Meilleure source | Ce qui a été vérifié |
|---|---|---|
| Passages entre cartes | **StarLoco-Game** : un script Lua par carte (9066), avec les cases de sortie et la carte et la case d'arrivée (`onMovementEnd`) | Sur les 212 cartes de notre référence du Monde des Douze, **478 des 479 passages vus en jeu** existent dans StarLoco. **31 des 36 sorties vues bloquées** n'y sont pas. À Incarnam, 119 passages sur 119. |
| Cases praticables officielles | **MapKeys-DR** : 9147 clés de cartes « relevées sur les serveurs officiels ». On les applique aux fichiers de cartes du client installé, avec `tools/cles_cartes.py`. | 215 cartes déchiffrées sur 215 essayées, toutes en mapData valide. **8845 des 9306 cartes du client** ont une clé pour la même version de carte. |
| Monstres agressifs | Aucune source officielle. La meilleure base est la colonne `aggroDistance` de StarLoco : 169 monstres sur 1388. On la croise avec les **sous-zones d'apparition officielles** de la langue du client. | La liste est plausible pour les familles connues (Wabbits, Bworks, Chafers, Koalaks, Dragoeufs…). Elle est **fausse pour les événements** : `aggroDistance = 0` pour le Bouftou d'Halouine, que nous avons pourtant vu agressif le 23/10. Les rayons diffèrent d'un guide Retro de 2019. |

## Méthode, et ce qui est vérifié ou seulement supposé

- Recherches web (wikis, forums, guides), puis dépôts GitHub d'émulateurs et d'outils 1.29 / Retro. Les listes de
  fichiers ont été lues avec l'API GitHub. Le dump SQL de StarLoco fait 72 Mo : seul le passage de la table
  `monsters` (3 Mo) a été téléchargé, par requêtes HTTP partielles. Même chose pour la table `monsters`
  d'AncestraRemake.
- Données locales, en lecture seule : la langue du client (`tools/langue_client.py`), les fichiers de cartes copiés
  (`tools/cles_cartes.py`, `dechiffrer_carte`), la référence `config/monde/passages/*.json`, et la liste des fichiers
  de `%LOCALAPPDATA%\Ankama\Retro\...\data\maps` (noms seulement).
- **Vérifié** : ce qui est marqué comme comparé ou déchiffré dans ce rapport.
- **Supposé** : ce qui est écrit au conditionnel, ou ce qui ne s'appuie que sur un émulateur ou un vieux forum.
- Pas de compte créé, pas de formulaire rempli, pas d'action dans le jeu.

---

## 1. Monstres agressifs

### 1.1 Le jeu officiel ne publie pas l'agression

- La langue des monstres du client (table `M`) n'a **aucun champ d'agression**. Les champs sont : `n` nom, `g` dessin,
  `b` race, `a` alignement, `k` « kickable », `d` boss, `s` en vente à l'hôtel des ventes, `g1..g10` grades, et
  `spawn.a` sous-zones d'apparition. Les noms de champs sont confirmés par le code de Cyberia
  (`Libs/Cyberia.Api/Data/Monsters/MonsterData.cs`).
- **En revanche, `spawn.a` est officiel et précieux** : il donne les sous-zones où chaque monstre apparaît. C'est la
  source à utiliser pour « où ».
- Le champ `canAggro` des cartes (dans `data/client_retro/cartes/index.json`) concerne l'agression **entre joueurs**
  (ailes, alignement), pas les monstres. Le patch Retro 1.34 interdit l'agression entre joueurs sur certaines cartes.
- Le serveur n'envoie pas non plus de distance d'agression dans le paquet des groupes de monstres. Elle reste côté
  serveur.

### 1.2 Comment marche l'agression (émulateur et témoignages)

Le code de **StarLoco** (`GameMap.isAggroByMob`, `PathFinding.java`) fonctionne ainsi. C'est la meilleure
description disponible, mais **ce n'est pas le serveur officiel** :

- La distance d'un groupe est la plus grande `aggroDistance` de ses monstres. 0 veut dire non agressif.
- Elle est mesurée depuis **la case du groupe** (la case du chef). Les autres monstres affichés autour ne comptent
  pas. Breakflip conseille d'ailleurs de désactiver « Afficher tous les monstres d'un groupe » pour éviter les
  agressions involontaires.
- La distance est une distance de Manhattan sur la grille : |dx| + |dy| en coordonnées de case.
- Le test est fait **à chaque case du chemin** : le déplacement s'arrête sur la première case à portée, et le combat
  commence. Un chemin s'arrête aussi sur une case de sortie (`cellHasMoveEndActions`).
- Alignement : un joueur neutre n'est pas agressé par un groupe aligné, ni un joueur par un groupe de son propre
  alignement. C'est le cas des Miliciens (rayon 10) et des Zoths gardiens.
- **Aucune condition de niveau dans StarLoco.** Or des joueurs de 2007 (JOL) disent que la distance dépend du niveau
  (« le HL passe, le BL est aggro », Gloutovores, île des Wabbits). Non vérifiable ici.
- Dans un groupe de joueurs, seul le chef du groupe est agressé. Les suiveurs **rejoignent** le combat après le délai
  réglé dans leurs options de groupe. C'est une règle de StarLoco ; sur le serveur officiel, un suiveur peut être
  agressé seul (déjà observé par le bot).

Rayons publiés pour Retro : un guide Breakflip Retro du 10/10/2019 dit que les monstres de l'île des Wabbits
« agressent à deux cases » et les Wabbits squelettes à trois. Il conseille de rester à 3 cases (4 pour les
squelettes). **StarLoco donne autre chose** : Wabbit 3, Black Wabbit 2, Tiwabbit 2, Wabbit squelette 2, Grand Pa
Wabbit 3.

### 1.3 Événement Halouine (en cours)

| Monstre (id) | Niveaux (langue officielle) | Sous-zones d'apparition officielles | Agression |
|---|---|---|---|
| **Bouftou d'Halouine** (793) | 3–15 | Forêt d'Amakna, Coin des Bouftous, Temple Féca, Élevage de Bouftous du Château d'Amakna, **Contour d'Astrub**, **Prairies d'Astrub**, **Le berceau (Tainéla)** | **Observée par nous le 23/10/2026 dans Le berceau.** StarLoco : 0 (faux). |
| **Tofu d'Halouine** (794) | 2–6 | Amakna, Champ des Ingalsses, **Champs d'Astrub**, **Coin des Tofus**, **Contour d'Astrub**, **Prairies d'Astrub**, **Le berceau** | Supposée (même événement). StarLoco : 0. |
| Sanglier d'Halouine (2964) | 10–18 | **Forêt d'Astrub**, Millifutaie, Forêt d'Amakna, La campagne, Bord de la forêt maléfique | Inconnue. Absent de StarLoco (événement récent). |
| Craqueleur d'Halouine (2965) | 25–37 | Montagne des Craqueleurs | Inconnue |
| Abraknyde d'Halouine (2966) | 23–39 | Forêt des Abraknydes, Orée de la forêt des Abraknydes, Forêt d'Amakna, Bord de la forêt maléfique, Bosquet du petit talus, Plaine des Scarafeuilles | Inconnue |
| Chafer Fantassin d'Halouine (2967) | 31–39 | Le cimetière (Amakna) | Inconnue (le Chafer Fantassin normal est agressif à 1 case dans StarLoco) |
| Mulou d'Halouine (2968) | 60–80 | aucune dans la langue | Inconnue |

Principe de l'événement, d'après les guides (Dofus 2 et anciennes versions) : les Bouftous et Tofus deviennent
« d'Halouine », agressent les passants **quel que soit leur niveau**, et le combat transmet la malédiction (on est
transformé pour un combat). La Tête de Citwouille protège de la transformation, pas de l'agression. L'annonce
officielle Retro d'Halouine (édition 2020) n'a pas pu être lue : dofus.com renvoie 403. Rien n'a été trouvé pour
l'édition 2026.

### 1.4 Région d'Astrub et environs

Croisement entre les sous-zones officielles (langue du client) et `aggroDistance` de StarLoco, avec les niveaux
officiels. Les monstres non cités sont **non agressifs selon StarLoco**. C'est le cas, par exemple, de tous les
Bouftous normaux, Tofus, Larves, Champs, Arakne, Moskito, Prespic, Sanglier, Pious, Crabes, Pichons, Blops,
Porkass et Corbacs.

| Zone / sous-zone | Monstres agressifs (StarLoco) | Niveaux | Rayon | Autres sources |
|---|---|---|---|---|
| Astrub / Contour d'Astrub, Prairies d'Astrub, Élevage de Bouftous ; Tainéla / Le berceau | **Bouftou d'Halouine**, Tofu d'Halouine (événement) | 3–15, 2–6 | inconnu (au moins 1) | vu en jeu le 23/10 (Le berceau) |
| Astrub / Champs d'Astrub, Coin des Tofus | Tofu d'Halouine (événement) | 2–6 | inconnu | supposé |
| Astrub / Forêt d'Astrub | Sanglier d'Halouine ? (événement) | 10–18 | inconnu | non vérifié |
| Astrub / Campement des Bandits Manchots (174) | **Bandit Manchot** (466) | 3–9 | 2 | forum JOL 2007 : « seulement les bandits manchots qui agro » |
| Astrub / Cité, Calanques, Exploitation minière, Souterrains, Donjon des Champs, Donjon Ensablé | aucun | | | |
| Incarnam / **Cimetière** (449), Donjon (447) | **Chafer Prépubère** (996) | 10–14 | 1 | **mort d'un personnage le 28/09** ; zone déjà évitée (`travel.avoid_zones`) |
| Incarnam / autres sous-zones | aucun | | | |
| Plaine de Cania / Plaine des Porkass, Plaines Rocheuses, Prairie des Blops, Plaine de Cania, Route de Bonta, Baie, Champs, Forêt de Cania | aucun | | | |
| Plaine de Cania / Massif de Cania | Mineur Sombre (118) | 16–31 | 1 | JOL 2007 : n'agresse plus depuis une mise à jour de l'époque. **Douteux.** |
| Plaine de Cania / Cimetière de Bonta | Chafer, Rib, Chafer Invisible, Chafer Lancier, Chafer Archer, Chafer d'élite | 21–52 | 1 | famille agressive selon tous les témoignages |
| Plaine de Cania / Bois de Litneg | Chafers, Rib, Chafer Fantassin ; **Trooll** (233) | 21–48 ; 105–125 | 1 | JOL 2007 : « seul les trool aggro » à Litneg (Mulous non agressifs) |
| Plaine de Cania / Belvédère d'Ilyzaelle | 11 monstres (Ilyzaelle, Gardes, Mages célestes…) | 100–1000 | 1 | contenu récent ; non vérifié |

### 1.5 Reste du Monde des Douze (StarLoco, par famille)

| Famille / lieu (sous-zones officielles) | Monstres (id) | Niveaux | Rayon StarLoco | Témoignages |
|---|---|---|---|---|
| Île des Wabbits | Wabbit (64), Black Wabbit (65), Tiwabbit (96), Tiwabbit Kiafin (72), Black Tiwabbit (68), Wo Wabbit (97), Grand Pa Wabbit (99), Wabbit Squelette (179) | 9–43 | 1 à 3 | Breakflip Retro 2019 : 2 cases (squelettes 3) |
| Bworks : village, campement, forêt maléfique, Gisgoul | Bwork (876), Bwork Archer (74), Bwork Mage (53), Workette (762) | 15–43 | 1 (Workette 2) | JOL 2007 : oui |
| Cimetières (Amakna, Cryptes, Torturés) | Chafers (54, 110, 290, 291, 292, 396), Rib (108) | 21–52 | 1 | JOL : oui (« les Chafers fantômes » non) |
| Égouts (Amakna, Bonta, Brâkmar) et donjons des rats | Rat d'Égoutant, Chaman, Rat d'Hyoactif, Gardienne des Égouts, rats des donjons, Boo (112) | 15–165 | 1 à 3 | JOL : rats, chamans (1 case), Boos |
| Marécages (Amakna, Koalaks) | Crocodaille (261), Chef Crocodaille (263), Boo | 15–77 | 2 ou 3 | JOL 2007 : « les crocos n'agressent pas », ce sont les Boos et les chefs. **Contradiction.** |
| Montagne des Koalaks | 20 Koalaks, Warkos, Drakoalak, Fauchalak… | 40–120 | 2 | JOL : oui |
| Presqu'île, sanctuaire et donjon des Dragoeufs | Dragoeufs (75 à 95), Dragoss, Dragueuse, Crocabulia | 50–108 | 2 | JOL : oui (« une seule case pour les dragoeufs », contesté) |
| Forêt des Abraknydes sombres | Abraknyde Sombre (253) | 72–84 | 2 | JOL : oui |
| Île de Moon | Kanniboul (211 à 214), Gloutovore (216) | 43–88 | 1–2 | JOL : Gloutovores, selon le niveau |
| Île de Grobe (Pandala) | Fantômes (583 à 598) | 60–440 | 3 | JOL : « tous les fantômes aggro » |
| Otomaï : Jungle obscure, Tourbières | Floribonde, Bitouf Sombre, Nerbe, Chiendent, Fécorce, Brouture, Abrakleur Sombre, Tourbassingue, Bourbassingue, Roissingue | 54–239 | 2 | aucun |
| Village des Zoths | Zoths (1052 à 1056 ; 1098 à 1107 alignés, aux portes) | 79–148 | 2 | aucun |
| Sidimote : Fungus, Sanctuaire de Qu'Tan | Champaknyde, Tromperelle, Champmane… | 113–999 | 1 | aucun |
| Bonta (et Brâkmar ?) | Milicien (296), aligné Bonta | 20–100 | **10** | agresse seulement les joueurs alignés à l'opposé |
| Divers | Scarafeuilles du donjon (795, 798), Trooll apprivoisé (791, rayon 10), archimonstres de ces familles | | | |

Liste complète des 169 monstres (id, niveaux officiels, rayon, alignement, sous-zones) : elle sort d'un croisement
fait pendant la recherche. Elle peut être régénérée sur demande. Elle n'a pas été mise dans le projet.

### 1.6 Fiabilité de l'agression

- **Officiel : rien.** Ni langue, ni patch notes Retro (1.34 à 1.38) sur l'agression des monstres.
- **StarLoco** : utile pour savoir quelles familles agressent, mais **les rayons ne sont pas sûrs** (écart avec le
  guide Breakflip). Les événements sont faux (Halouine à 0) et les monstres récents absents (ids ≥ 2964).
- **Forums 2007** (JOL, jeuxvideo.com) : versions 1.17 à 1.20, d'avant la 1.29 ; utiles pour les familles. Ces fils
  disent aussi que certains monstres ont cessé d'agresser à l'époque (mineurs, boulangers et forgerons sombres).
- **Notre propre observation** reste la meilleure source : Chafers d'Incarnam (28/09), Bouftou d'Halouine (23/10).

---

## 2. Cartes et passages

### 2.1 Ce que contient chaque source

**StarLoco-Game, `scripts/data/maps/<région>/<id>_<x>_<y>.lua`** (9066 fichiers, environ 5 à 10 Ko chacun). Exemple
réel, carte 10170 (12,-28, Calanques) :

```lua
local map = MapDef(10170, "0706131721", "<clé hexa>", "<mapData EN CLAIR, 10 car./case>", 15, 17, 12, -28, 335)
map.positions = "dBdCd4...|eaebeD..."      -- cases de placement en combat
map.mobGroupsCount = 3 ; map.mobGroupsMaxSize = 4
map.allowedMobGrades = { {922, 1}, ... }    -- monstres possibles sur la carte (émulateur)
map.onMovementEnd = {
    [189] = moveEndTeleport(10169, 245),   -- case de sortie -> carte d'arrivée, case d'arrivée
    [202] = moveEndTeleport(10155, 233),
    [455] = moveEndTeleport(10177, 34),
}
```

On y trouve aussi les PNJ (`map.npcs`), le zaap (`map.zaapCell`) et les objets utilisables (`map.onObjectUse`). Les
anciens émulateurs (AncestraRemake) gardent les sorties dans une table SQL `scripted_cells` (MapID, CellID, ActionID,
EventID, ActionsArgs « carte,case », Conditions).

**MapKeys-DR, `maps/<id>_<date>.txt`** : une clé hexadécimale par version de carte, « Collected on official
servers ». Le nom de fichier reprend celui des cartes du client (`<id>_<date>X.swf`). Avec `tools/cles_cartes.py`
(algorithme du client, déjà vérifié dans `loader.swf`), on obtient le mapData en clair des fichiers **officiels** du
client installé : cases actives, praticables, ligne de vue, niveaux, objets.

**dofus-retro-maps** (bucket public `https://pub-0a42c3f1a95b4c198465b01a7deba67e.r2.dev/maps/<id>.json`) : une carte
par JSON, avec en-tête, coordonnées, sous-zone, mapData chiffré et en clair, placements, `teleports` et
`house_doors`. Comparé sur 4 cartes : les `teleports` sont **identiques à StarLoco**, ils en viennent sans doute. Le
mapData est identique au déchiffrement officiel (y compris pour 7411, que StarLoco a modifiée). Il n'existe pas
d'index : il faut une requête par carte. Le code n'est pas publié et il n'y a pas de licence.

### 2.2 Vérifications faites

| Vérification | Résultat |
|---|---|
| Passages vus en jeu (`config/monde/passages/monde_des_douze.json`, 212 cartes) contre les sorties StarLoco, par direction | **478 / 479** présents. Seul manque **-5,-16 → droite** (-4,-16). |
| Sorties vues **bloquées** en jeu | **31 / 36** sans sortie dans StarLoco. Les 5 autres (-1,-21 gauche ; 0,-14 haut ; 0,-23 gauche ; 0,-26 droite ; 12,-22 haut) ont une sortie dans StarLoco. Pour -1,-21 et 0,-14, la case de sortie est dans une partie de la carte séparée du reste (130 cases sur 281, et 264 sur 342) : c'est cohérent avec « bloquée selon le côté d'où l'on vient ». Pour 0,-26 et 12,-22, la case semble atteignable : **à vérifier en jeu**. |
| Incarnam (`incarnam.json`, 41 cartes comparées sur 46) | **119 / 119** passages présents. La seule sortie vue bloquée a une sortie dans StarLoco. |
| Sorties StarLoco jamais essayées par le bot (212 cartes) | **298**. C'est le gain possible pour la référence. |
| Clés MapKeys-DR appliquées aux cartes du client local | **215 / 215** déchiffrées en mapData valide. Le mapData obtenu est identique à celui de StarLoco pour 10170 et 7331, et identique à dofus-retro-maps. |
| Version des cartes (date dans le nom) : client 1.49 contre MapKeys-DR | 8845 cartes du client ont une clé de même date ; 4 ont une clé d'une autre date ; 457 n'ont pas de clé (Foire du Trool : 39 sur 83 ; Moon : 24 sur 93). Astrub : 526 sur 545. Incarnam : 91 sur 92. |
| Version des cartes : StarLoco contre client (sur les 212) | 207 de même date, 5 différentes (1868, 7409, 7460, 8551, 8584). Pour 7411, la date est la même mais StarLoco a remplacé le mapData. |
| Cartes du client couvertes par un script StarLoco | 8362 / 9306. Astrub 521 sur 545, Incarnam 70 sur 92, Plaine de Cania 1286 sur 1333, Tainéla 29 sur 47. |
| La valeur `déplacement = 2` du mapData officiel marque-t-elle les sorties ? | **Pas toujours.** 353 passages vus ont une case de sortie de valeur 2, mais 125 n'en ont pas. Il ne faut donc pas s'en servir seul pour trouver les sorties. |

### 2.3 Autres outils trouvés

| Ressource | Ce que c'est | Intérêt |
|---|---|---|
| `api.dofusretro.fr` (Daweyy, utilisée par MapRenderer-DR : `/maps/<id>` renvoie `cellsData`) | API de cartes Retro | **Injoignable le 23/10/2026** (erreur TLS). À ne pas utiliser. |
| arbll/dofus-key-finder (Go) ; astariul `scripts/cracker_app.py` | Retrouvent la clé d'une carte à partir de ses seules données chiffrées (attaque statistique) | Seulement pour les 457 cartes sans clé. Le premier outil demande une base de cartes déjà déchiffrées. |
| Dysta/Dofutils (Python 3.11+, paquet `dofutils`), Arakne/ArakneUtils (Java, LGPL-3.0) | Décodage des cartes, pathfinding, ligne de vue, mouvement des cases | Le projet a déjà son décodeur. Sert de comparaison pour le pathfinding. |
| Arakne/php-map-parser (LGPL-3.0), Daweyy/MapRenderer-DR | Rendu d'images de cartes | Pas utile au bot |
| DMAP, dofus-map, Jduquenne/dofus-retro-craft-dashboard | Cartes interactives de ressources | Pas de sorties entre cartes |
| dofusretro.fr (`MapKeys-DR`), solomonk.fr, xixou.io (Retro 1.49) | Encyclopédies Retro | Niveaux et butins ; **rien sur l'agression** (vérifié sur xixou ; solomonk est rendu dans le navigateur et n'a pas pu être lu) |

---

## 3. Tableau des ressources

| Ressource | URL | Contenu exact | Format | Couverture | Fiabilité | Licence / conditions |
|---|---|---|---|---|---|---|
| StarLoco-Game | https://github.com/StarLoco/StarLoco-Game | Scripts de cartes : clé, mapData en clair, sorties `onMovementEnd`, placements, monstres permis, PNJ. Dump SQL `db-init/04-game.sql` (72 Mo), dont la table `monsters` avec `aggroDistance`, `AI_Type`, `isBoss`. Code de l'agression. | Lua, SQL MariaDB | 9066 cartes (8362 du client actuel) ; 1388 monstres, dont 169 agressifs | Émulateur « 1.39 » ; sorties **très fidèles** (478 sur 479 passages vus) ; agression **approximative** | **Aucune licence** (tous droits réservés par défaut). Données de jeu propriété d'Ankama. Lecture pour un usage personnel non redistribué : zone grise, à la discrétion de l'utilisateur. |
| StarLoco (docker) | https://github.com/StarLoco/StarLoco | Même dump SQL, guide d'installation | SQL | id. | id. | aucune licence |
| MapKeys-DR | https://github.com/Daweyy/MapKeys-DR | 9147 clés de cartes Retro (dernière mise à jour le 04/12/2025) | 1 fichier texte hexadécimal par carte | 8845 cartes du client 1.49 avec la bonne version | Relevées sur les serveurs officiels ; **vérifiées sur 215 cartes** | **Aucune licence** |
| dofus-retro-maps | https://dofus-retro-maps.pages.dev/ ; données : `https://pub-0a42c3f1a95b4c198465b01a7deba67e.r2.dev/maps/<id>.json` | JSON par carte : mapData chiffré et clair, sorties (venues de StarLoco), placements, portes | JSON | non dénombré (pas d'index) | mapData officiel, sorties de StarLoco | Aucune licence ; code non publié. À ne pas interroger en masse. |
| Langue du client officiel (déjà dans le projet) | `data/client_retro/lang` | Monstres : niveaux, PV, **sous-zones d'apparition** (`spawn.a`) ; cartes : coordonnées, sous-zones | SharedObject AMF0 | 1483 monstres, 9306 cartes | **Officielle** | Propriété d'Ankama, déjà copiée localement |
| AncestraRemake | https://github.com/reben/AncestraRemake | Dump SQL 1.29.1 : `scripted_cells`, `maps`, `monsters` (**sans colonne d'agression**) | SQL MySQL | ancien (2013) | émulateur 1.29.1 | Apache-2.0 |
| Araknemu | https://github.com/Arakne/Araknemu | Émulateur 1.29 ; **aucune donnée fournie** (schéma seul : `resources/db/000-init_mysql.sql`) | Java, SQL | | | LGPL-3.0 |
| Autres dumps d'émulateurs | Dysta/Nao (`RubrumBDD.sql` 41 Mo), ladofie/scruffemu (57 Mo, dérivé de StarLoco), Emudofus/Shivas, codebreak, GDCore, alexandria_server | Dumps 1.29 | SQL | | Émulateurs anciens, non comparés | Licences variables, souvent absentes |
| Dofutils | https://github.com/Dysta/Dofutils (doc : dofutils.readthedocs.io) | Bibliothèque Python : cartes, pathfinder, ligne de vue, encodages | Python | | Portage d'ArakneUtils | Aucun fichier de licence |
| ArakneUtils | https://github.com/Arakne/ArakneUtils | Algorithmes 1.29 (cartes, chemins) | Java | | | LGPL-3.0 |
| dofus-key-finder | https://github.com/arbll/dofus-key-finder | Recherche de clés de carte | Go | | | Aucune licence |
| Breakflip, château Wa Wabbit (Retro) | https://www.breakflip.com/guides/105836.html | Rayons d'agression sur l'île des Wabbits (2 cases, squelettes 3) | Guide | 1 zone | Communautaire, Retro, 2019 | Lecture seule |
| JOL 2007, « Les Agressions des Monstres » | https://forums.jeuxonline.info/sujet/765863/les-agressions-des-monstres | Témoignages : familles agressives, rayons, effet du niveau | Forum | Monde entier | Communautaire, versions 1.17 à 1.20 | Lecture seule |
| JOL 2007, « Les monstres qui agressent » | https://forums.jeuxonline.info/sujet/765880/titre-edite-les-monstres-qui-agressent | Liste par lieu (Wabbits, cimetières, égouts, Bworks, Moon, Koalaks, Abraknydes sombres, Dragoeufs, Litneg, marécage, Grobe, boss) | Forum | Monde entier | idem | Lecture seule |
| Dofus pour les noobs, Halouine | https://www.dofuspourlesnoobs.com/halouine.html | Bouftous et Tofus d'Halouine agressifs quel que soit le niveau, malédiction, Tête de Citwouille | Guide | événement | Dofus 2, pas Retro | Lecture seule |
| Cyberia | https://github.com/Lounek09/Cyberia (déplacé sur codeberg.org/Lounek/Cyberia) | Sens des champs de la langue Retro | C# | | | AGPL-3.0 |
| dofushistory | https://loonaire.github.io/dofushistory/ | Changelogs de la bêta à Retro (rien sur l'agression des monstres) | Markdown | | Communautaire | CC-BY-SA-4.0 |

---

## 4. Recommandations (à approuver, rien n'est intégré)

Trois types de données à approuver **séparément** :

### A. Clés de cartes MapKeys-DR → cases praticables officielles (recommandé en premier)

- **Pourquoi** : c'est la donnée la plus fidèle. Ce sont les fichiers du client officiel lui-même ; seule la clé
  vient d'Internet. La méthode est vérifiée (215 sur 215).
- **Intégration proposée** (sans la faire) :
  1. Un outil `tools/cles_cartes.py --importer-cles <dossier>` lirait les fichiers `<id>_<date>.txt`, n'accepterait
     une clé que si la date est celle de la carte du client, puis écrirait
     `data/client_retro/cartes/cases/<id>.json`. On peut aussi produire un seul fichier compressé.
  2. Chaque carte donnerait ses cases actives et praticables, sa ligne de vue et ses niveaux. Usages : clics de
     déplacement sur une case praticable ; repérage des groupes de monstres (case de la carte → pixel) ; calcul de la
     distance d'agression (voir C) ; parties de carte séparées (sorties « bloquées selon le côté »).
  3. Les 457 cartes sans clé restent comme aujourd'hui.

### B. Sorties StarLoco → passages candidats

- **Pourquoi** : 478 sur 479 passages vus en jeu sont retrouvés, et il y a 298 sorties de plus sur nos seules 212
  cartes. Cela donne la case de sortie exacte et la case d'arrivée.
- **Intégration proposée** :
  1. Un outil `tools/passages.py --importer-starloco` lirait les scripts Lua, à télécharger une fois : le dépôt
     complet ou les seuls dossiers `scripts/data/maps/astrub`, `incarnam`, `cania`, `amakna`… Il convertirait
     `carte → (case, carte d'arrivée, case d'arrivée)` en directions grâce aux coordonnées de la langue.
  2. Ces sorties formeraient une **troisième catégorie** dans l'itinéraire pondéré : « confirmé » (poids 1),
     « StarLoco non confirmé » (poids proposé 1,5), « jamais pris » (2). Ce qui est observé en jeu prime toujours :
     un passage vu bloqué n'est jamais rouvert par StarLoco.
  3. Les clics viseraient la case de sortie exacte au lieu du bord de carte. C'est utile surtout pour les sorties
     décentrées.
  4. À vérifier en session de test : -5,-16 → droite (vu en jeu, absent de StarLoco) ; 0,-26 → droite et 12,-22 →
     haut (vus bloqués, présents dans StarLoco, case apparemment atteignable).

### C. Agression : liste StarLoco et sous-zones officielles, corrigées par l'observation

- **Pourquoi** : aucune source officielle. StarLoco donne les familles ; nos observations corrigent le reste.
- **Intégration proposée** :
  1. Un fichier `config/monstres_agressifs.yaml` (nouveau) : id du monstre, rayon, source (« starloco »,
     « observé le 23/10 », « guide »), et une option « événement ». Le rayon retenu serait le plus grand des sources
     (Wabbits : 3), plus 1 case de marge.
  2. Le bot n'évite pas d'abord des zones entières : il garde ses distances avec **les groupes vus sur la carte**. Il
     calcule la distance de Manhattan depuis la case du groupe et refuse un chemin qui passe à moins de rayon + 1
     cases. Cela suppose de savoir quels monstres compose le groupe : l'infobulle au survol, déjà lue par
     `engage.py`, donne les noms.
  3. Pour les trajets à risque, ajouter à `travel.avoid_zones` les sous-zones des familles agressives quand le
     niveau de l'équipe est trop bas : île des Wabbits, Cimetière de Bonta, Bois de Litneg, cimetières d'Amakna,
     Koalaks, Dragoeufs, Grobe. Le Cimetière d'Incarnam y est déjà.
  4. Halouine (en cours) : mettre Bouftou et Tofu d'Halouine dans la liste, avec rayon inconnu (2 par prudence).
     Leurs sous-zones officielles couvrent tout le terrain d'Astrub (Contour, Prairies, Champs, Coin des Tofus) et Le
     berceau. Les éviter, ou les accepter comme combats faciles (niveaux 2 à 15) si l'équipe le permet.
  5. Mesurer le vrai rayon en session de test : noter la distance (en cases) entre le personnage et le groupe au
     moment de chaque agression, et mettre le fichier à jour.

### Conditions d'utilisation

Les données de jeu appartiennent à Ankama. MapKeys-DR, StarLoco et dofus-retro-maps n'ont pas de licence : aucune
réutilisation n'est accordée explicitement. Une utilisation locale, personnelle et sans redistribution est le cas le
moins exposé. Il ne faudrait pas republier ces fichiers, ni les intégrer au dépôt s'il devient public. Il ne faut pas
non plus interroger en masse le bucket de dofus-retro-maps. Par ailleurs, l'automatisation du jeu reste contraire aux
conditions d'utilisation d'Ankama, quelle que soit la source des données.
