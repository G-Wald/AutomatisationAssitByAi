# Référence du monde contre la langue du client (11/10)

Question : notre référence (`config/monde/monde_des_douze.json` et `incarnam.json`, importées de xixou.io le 28/09)
est-elle conforme aux données officielles du client (langue des cartes `MA`, version maps 1384) ?

**Réponse courte.**
- **Astrub est juste.** Les 121 cartes de la Cité d'Astrub (75) et du Contour d'Astrub (46) concordent une à une. Les
  faits vus en jeu, les 5 portes et les 21 noms de zone lus par le bot à l'écran concordent aussi.
- **Ce qui manque à la référence, ce sont les remparts, pas les zones.** Le bot relie toutes les cartes voisines
  connues (`atlas.links`). La langue délimite la cité : 36 limites avec le dehors, dont 5 portes connues. 9 limites
  sont vues « bloquées » par le bot, et aucune porte n'en fait partie.
- Dans le reste du Monde des Douze, 4357 des 4716 appartenances à une zone sont identiques. La plupart des écarts
  viennent de noms mal écrits ou de zones regroupées. Restent 24 cartes rangées dans une autre zone que celle de la
  langue, et 19 placées là où il n'y a aucune carte extérieure.
- 20 cartes de la référence n'existent pas comme cartes extérieures : 16 ne sont que des intérieurs (sous la Façade
  de Brâkmar, au cimetière d'Amakna...) et 4 n'existent pas du tout. À l'inverse, 110 cartes extérieures manquent à la
  référence (92 sur l'île de Nowel, 8 en Plaine de Cania, 7 temples...).
- **Incarnam** : 85 ids sur 85 retrouvés, aux mêmes coordonnées. Une sous-zone a été renommée (« Donjon d'Incarnam »
  est devenu « Donjon »). Les 26 cartes extérieures des Pitons rocheux sont rangées dans `interieurs`.

Rien n'a été modifié dans la référence. Le détail complet est dans `data/client_retro/comparaison.json`. Pour le
refaire : `.venv/Scripts/python -m tools.cartes_client --comparer`.

## 1. Méthode

Sources, toutes en lecture seule :
- la langue `data/client_retro/lang/ANKXTRASO_0_MA.sol` (copie faite le 11/10, décodage de `tools/cartes_client.py`) ;
- la référence `config/monde/*.json` : `zones[].cartes` (appartenances) et `cartes[].zone`, la zone que lit
  `bot/brain/atlas.py` ;
- la mémoire du bot `data/carte.json`, pour confronter la langue au jeu. **Elle a changé pendant mon travail** (le bot
  ou une autre session a parcouru 2,-13, 2,-14 et 1,-14 vers 21 h 50). Les chiffres « en jeu » ci-dessous datent de
  21 h 52.

**Rangement.** Une carte de la langue a une sous-zone (`sa`), qui appartient à une zone (`a`), elle-même rattachée
à une super-zone (`sua`) :
- `sua` 0 « Continent Amaknien » = Monde des Douze, 9184 cartes ;
- `sua` 3 « Zone de départ » = Incarnam, 92 cartes.

Comme les deux super-zones réutilisent les mêmes coordonnées, chacune est comparée à son fichier.

**Comment j'ai tranché entre les cartes qui partagent les mêmes x,y.**
1. Je ne garde que les cartes extérieures, c'est-à-dire avec le champ `o` (égal au `bOutdoor` du SWF sur les 9276
   cartes). Les intérieurs (maisons, banque, mines, égouts, donjons) portent les coordonnées de la carte du dehors : ils
   sont écartés de la comparaison des zones.
2. Il reste 153 coordonnées du Monde des Douze (4 à Incarnam) qui ont plusieurs cartes extérieures. La **carte
   principale** est celle de plus petit id : la carte d'origine du monde. Les autres sont des couches ajoutées plus tard.
   Sur 84 de ces coordonnées, les sous-zones diffèrent :
   - Otomaï : la canopée et le village des Zoths sont au-dessus de la jungle et des plaines (65 coordonnées) ;
   - le village des Brigandins, avec ses plates-formes et son prisme, est superposé au Massif de Cania et à la
     Route de Bonta (16) ;
   - le reste (3) : le Donjon des Larves sur le temple Sacrieur, le Donjon des Kitsounes sur la Bordure de Terrdala,
     et le Campement des Bworks sur celui des Gobelins.

   Sur les 69 autres, toutes les extérieures ont la même sous-zone (variantes des temples, de Feudala...) : il n'y a
   rien à trancher. À Astrub, chaque coordonnée a une seule carte extérieure.
3. Pour chaque carte, le nom de zone de la référence reçoit un verdict, comparé à la sous-zone de la carte
   principale :

   | Verdict | Sens |
   |---|---|
   | identique | même nom, au caractère près |
   | graphie | même nom, à un accent, une majuscule ou une apostrophe près |
   | carte secondaire | nom d'une autre carte extérieure de la même coordonnée (une couche) : pas une erreur |
   | discorde | autre nom |
   | absente | aucune carte extérieure à ces coordonnées |
   | sans zone | la référence ne donne pas de zone |

## 2. Chiffres globaux

| | Monde des Douze | Incarnam |
|---|---:|---:|
| Cartes de la langue (super-zone) | 9184 | 92 |
| Coordonnées avec une carte extérieure (langue) | 4794 (5277 cartes) | 52 (72 cartes) |
| Coordonnées sans carte extérieure (intérieurs seulement) | 71 | 0 |
| Cartes de la référence (`cartes`) | 4704 | 46 |
| Communes | 4684 | 46 |
| Seulement dans la référence | 20 | 0 |
| Seulement dans la langue | 110 | 6 |
| Appartenances `zones[].cartes` | 4716 (32 cartes dans deux zones) | 52 |
| identique / graphie / carte secondaire | 4357 / 124 / 19 | 50 / 0 / 0 |
| discorde / absente | 197 / 19 | 2 / 0 |
| Zone lue par le bot (`cartes[].zone`) : identique / graphie | 4356 / 124 | 46 / 0 |
| Zone lue par le bot : carte secondaire / discorde | 3 / 182 | 0 / 0 |
| Zone lue par le bot : sans zone / absente | 19 / 20 | 0 / 0 |
| Zones de la référence identiques à 100 % | 93 sur 131 | 9 sur 11 |

## 3. Cartes extérieures de la langue, par zone

| Zone de la langue (Monde des Douze) | Coordonnées | Cartes extérieures | Dans la référence |
|---|---:|---:|---:|
| Plaine de Cania | 1217 | 1238 | 1209 |
| Amakna | 727 | 749 | 719 |
| Lande de Sidimote | 514 | 515 | 514 |
| Montagne des Koalaks | 391 | 394 | 391 |
| Ile d'Otomaï | 383 | 768 | 382 |
| **Astrub** | **306** | **306** | **306** |
| Bonta | 213 | 214 | 213 |
| Forêt des Abraknydes | 172 | 180 | 172 |
| Brâkmar | 151 | 152 | 151 |
| Pandala Neutre / Eau / Air / Terre / Feu | 143 / 73 / 51 / 45 / 27 | 143 / 83 / 59 / 56 / 37 | 142 / 73 / 51 / 45 / 27 |
| Zone arctique (île de Nowel) | 108 | 108 | 16 |
| L'île de Moon | 58 | 60 | 58 |
| L'île des Wabbits | 58 | 58 | 58 |
| Sufokia | 42 | 42 | 42 |
| Tainéla | 30 | 30 | 30 |
| Foire du Trool | 23 | 23 | 23 |
| Le Champ du Repos | 23 | 23 | 23 |
| Territoire des Dopeuls | 20 | 20 | 20 |
| L'île du Minotoror | 16 | 16 | 16 |
| Village des Brigandins | 3 | 3 | 3 |
| **Incarnam** (super-zone 3) | 52 | 72 | 46 |

## 4. Cartes présentes d'un seul côté

### Monde des Douze : dans la référence, pas en extérieur dans la langue (20)

| Coordonnées | Zone de la référence | Ce que dit la langue |
|---|---|---|
| -34..-32, 40..43 (10 cartes) | Façade de Brâkmar | intérieurs seulement : Bordure de Brâkmar, ids 12137 à 12147 |
| 8,15 ; 10,13 ; 10,18 | Le cimetière | intérieurs seulement : Le cimetière (8262, 8205, 8204) |
| 11,14 | La forêt d'Amakna | intérieur seulement (8297) |
| -8,-5 | Forêt des Abraknydes Sombres | intérieur seulement : sous-zone Amakna (11087) |
| -3,8 | aucune (ressource Orchidée Freyesque) | intérieur seulement : Campement des Bworks (1666) |
| -38,-51 ; -6,-59 ; -6,-6 ; 33,9 | Bonta, Plaine de Cania, Montagne des Craqueleurs, Plage de Moon | aucune carte, ni dehors ni dedans |

Ces cartes sont dangereuses pour les trajets. `atlas.links` les prend pour des voisines atteignables, alors que ce
sont des souterrains ou des cartes inexistantes.

### Monde des Douze : extérieures dans la langue, absentes de la référence (110)

- **Île de Nowel** (sous-zone « L'île de Nowel », zone « Zone arctique », x -69 à -58, y -105 à -92) : 92
  coordonnées. 16 autres y sont dans la référence, mais sans zone (ressource Edelweiss).
- **Plaine de Cania** : la colonne -3,-59 à -3,-52 (8 cartes, ids 3681 à 3688).
- **Temples** (carte extérieure du temple) : Sadida -1,9 ; Crâ 0,3 ; Ecaflip 1,-5 ; Iop 1,3 ; Eniripsa 7,1 ;
  Osamodas 8,2 ; Féca 12,5.
- Isolées : Porte de Sufokia 10,22 ; Sanctuaire de Pandala 28,-59 ; Plage de Corail -53,25.

Dans la référence, 19 cartes n'ont pas de zone alors que la langue en donne une :
- 16 sur l'île de Nowel ;
- -27,8 et -27,9 : Lande de Sidimote ;
- -4,0 : Le temple Sram.

### Incarnam

Aucune carte de la référence ne manque dans la langue. Six coordonnées extérieures de la langue manquent à `cartes` :
-4,3 ; -3,3 ; -2,2 ; -2,3 ; -1,2 ; -1,3, toutes « Pitons rocheux ». L'import les a mises dans `interieurs` parce
qu'elles n'ont pas de soleil de sortie : ce sont des étages de 5 à 7 cartes superposées. Voir § 5.4.

## 5. Noms de zone

### 5.1 Tableau des écarts par zone (Monde des Douze)

Le tableau ne montre que les 38 zones de la référence qui ne sont pas identiques à 100 %. Les 93 autres le sont,
dont les 11 zones d'Astrub.

| Zone de la référence | Région | Cartes | Identiques | Graphie | Carte secondaire | Discordes | Absentes | Sous-zone de la langue pour les écarts |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Brâkmar | Brâkmar | 126 | 70 | | | 56 | | Quartier des bouchers (11), des bûcherons (8), des forgerons (8), des boulangers (8), des bijoutiers (7), des bricoleurs (4), de la milice (4), des tailleurs (4), Extérieur de l'arène (1), Centre-ville (1) |
| Île des Wabbits | Île des Wabbits | 35 | 0 | 35 | | | | Ile des Wabbits |
| Gisgoul, le village dévasté | Lande de Sidimote | 34 | 0 | 34 | | | | Gisgoul, le village devasté (sic) |
| Le territoire des Bandits | Amakna | 33 | 0 | | | 33 | | Territoire des Bandits |
| Fôret de Kaliptus | Montagne des Koalaks | 27 | 0 | 27 | | | | Forêt de Kaliptus |
| Burdure de Brâkmar | Brâkmar | 24 | 0 | | | 24 | | Bordure de Brâkmar |
| Iles des naufragés | Île d'Otomaï | 22 | 0 | | | 22 | | Ile des naufragés |
| Le Château d'Amakna | Amakna | 20 | 0 | 20 | | | | Le château d'Amakna |
| Village des Brigandins | Village des Brigandins | 18 | 0 | | | 18 | | Massif de Cania (14, la couche « Village Brigandins » est dessus), Village Brigandins (3), Route de Bonta (1) |
| Village des Zoths | Village des Zoths | 16 | 0 | | 16 | | | Jungle obscure (10), Feuillage de L'arbre Hakam (6) : couches d'Otomaï |
| Façade de Brâkmar | Brâkmar | 120 | 109 | | | 1 | 10 | Bordure de Brâkmar (1), intérieurs seulement (10) |
| Territoire des Dopeuls | Territoire des Dopeuls | 11 | 0 | | | 11 | | Village Dopeul |
| Territoire des Dopeuls (2e zone du même nom) | Territoire des Dopeuls | 9 | 0 | | | 9 | | Entrainement Dopeuls |
| Île des Wabbits Squelettes | Île des Wabbits | 7 | 0 | 7 | | | | Ile des Wabbits Squelettes |
| Lande de Sidimote | Lande de Sidimote | 254 | 249 | | | 5 | | Façade de Brâkmar (4 : -19,25 à -19,28), Cimetière des Torturés (1 : -15,30) |
| Le cimetière | Amakna | 18 | 15 | | | | 3 | intérieurs seulement |
| La montagne des Craqueleurs | Amakna | 30 | 27 | | | 2 | 1 | Le temple Sacrieur (-2,-8), Le temple Enutrof (-1,-4), aucune carte (-6,-6) |
| Bonta | Bonta | 78 | 75 | | | 2 | 1 | Centre-ville (-30,-53), Extérieur de l'arène (-27,-60), aucune carte (-38,-51) |
| La forêt d'Amakna | Amakna | 75 | 73 | | | 1 | 1 | Le temple Pandawa (5,20), intérieur seulement (11,14) |
| Massif de Cania | Plaine de Cania | 311 | 309 | | | 2 | | Orée de la forêt des Abraknydes (-12,-19 ; -12,-20) |
| Garnison d'Amakna | Amakna | 5 | 4 | | | 1 | | Le château d'Amakna (5,-6) |
| Village de Pandala | Pandala | 47 | 46 | | | 1 | | Forêt de Pandala (26,-34) |
| La jungle profonde de Moon | Île de Moon | 23 | 22 | | | 1 | | Le chemin vers Moon (32,12) |
| La plage de Moon | Île de Moon | 32 | 31 | | | | 1 | aucune carte (33,9) |
| Foire du Trool | Autre | 23 | 22 | | | 1 | | Jeu du Bouftou (-12,-39) |
| Forêt des Abraknydes Sombres | Forêt des Abraknydes | 63 | 62 | | | | 1 | intérieur seulement (-8,-5) |
| La presqu'île des Dragoeufs | Amakna | 50 | 49 | | | 1 | | Amakna Sud (-1,27) |
| Clairière de Brouce Boulgour | Amakna | 15 | 14 | | | 1 | | Le champ des Ingalsses (9,9) |
| Campement des Bworks | Amakna | 14 | 13 | | 1 | | | Campement des Gobelins (-6,4, couche) |
| Le village | Amakna | 38 | 37 | | | 1 | | Le temple Xélor (3,1) |
| Le bosquet du petit talus | Amakna | 1 | 0 | 1 | | | | Le Bosquet du petit talus |
| Bordure de Bonta | Bonta | 45 | 44 | | | 1 | | Bonta (-29,-62) |
| Quartier des Tailleurs | Bonta | 6 | 5 | | | 1 | | Bonta (-30,-57) |
| Quartier de la Milice | Bonta | 10 | 9 | | | 1 | | Bonta (-35,-52) |
| Plaine de Cania | Plaine de Cania | 436 | 435 | | | | 1 | aucune carte (-6,-59) |
| Village des Zoths (2e zone du même nom) | Village des Zoths | 1 | 0 | | | 1 | | Feuillage de L'arbre Hakam (-53,15) |
| Canopée du Kimbo | Île d'Otomaï | 1 | 0 | | 1 | | | Jungle obscure (-51,16, couche) |
| Plage de Corail | Île d'Otomaï | 140 | 139 | | 1 | | | Village de la Canopée (-58,20, couche) |

Ces écarts relèvent de quatre causes :
1. **Le nom de la référence est mal écrit, ou il diffère de celui du jeu.** Cela touche 222 cartes :
   - « Le territoire des Bandits », « Burdure de Brâkmar », « Iles des naufragés », « Village des Brigandins » ;
   - des écarts de graphie : Île / Ile, Château / château, Fôret / Forêt, dévasté / devasté, bosquet / Bosquet.

   Le jeu affiche le nom de la langue, avec ses fautes (« devasté »).
2. **La référence regroupe plusieurs sous-zones sous un seul nom.**
   - Brâkmar : 56 cartes de quartiers.
   - Territoire des Dopeuls : 20 cartes, qui forment deux zones homonymes dans la référence.
   - À Bonta, en revanche, les quartiers sont séparés.
3. **La référence décrit une couche.** Pour Village des Zoths, Canopée du Kimbo et le village des Brigandins, elle
   nomme la couche du dessus à des coordonnées où la carte principale est le sol. Ce n'est pas une erreur, mais une
   coordonnée ne suffit pas à désigner la bonne carte.
4. **Erreurs isolées** (1 à 5 cartes). Elles sont listées dans le tableau. Il s'agit surtout de temples rangés dans la
   zone qui les entoure, et de bords de zones.

### 5.2 Astrub, carte par carte : Cité d'Astrub contre Contour d'Astrub

**121 cartes sur 121 concordent.** Les 75 cartes « Cité d'Astrub » et les 46 cartes « Contour d'Astrub » de la
référence sont exactement celles de la langue (`cartes[].zone` comme `zones[].cartes`). Il n'y a ni carte en trop,
ni carte manquante, ni carte en double. Les 21 cartes dont le bot a lu le nom à l'écran (`data/carte.json`) disent
toutes « Cité d'Astrub » comme la langue.

Grille de la langue (carte principale), de x -5 à 12 et de y -30 à -8. Une lettre signifie que la référence
concorde ; « ! » qu'elle discorde ; « . » qu'il n'y a pas de carte. Il n'y a ni « + » (langue seule) ni « - »
(référence seule) dans ce cadre.

```
       -5  -4  -3  -2  -1   0   1   2   3   4   5   6   7   8   9  10  11  12
  -30   p   p   p   b   b   b   b   F   F   F   b   b   .   .   .   .   .   .
  -29   p   p   p   F   b   b   b   F   F   F   F   b   A   .   a   a   .   .
  -28   B   B   B   F   F   F   F   F   D   A   A   A   A   a   a   a   a   a
  -27   B   B   B   F   F   F   F   F   F   A   A   A   A   a   a   a   a   a
  -26   B   B   B   F   F   F   F   F   F   A   A   A   A   A   a   a   a   a
  -25   B   B   B   F   F   F   F   F   F   A   A   A   A   A   A   a   a   a
  -24   B   B   B   F   F   F   F   F   F   A   A   A   A   M   M   M   a   a
  -23   B   B   B   c   F   F   F   F   c   c   c   c   A   A   M   M   P   a
  -22   B   B   B   c   c   C   C   C   C   C   C   c   c   c   P   P   P   P
  -21   B   B   B   c   C   C   C   C   C   C   C   C   C   c   P   P   P   P
  -20   O   O   c   c   C   C   C   C   C   C   C   C   C   c   P   P   P   P
  -19   O   O   c   c   C   C   C   C   C   C   C   C   C   c   P   P   P   P
  -18   O   O   c   c   C   C   C   C   C   C   C   C   C   c   P   P   P   P
  -17   O   O   c   c   C   C   C   C   C   C   C   C   C   c   P   P   P   P
  -16   O   O   c   c   C   C   C   C   C   C   C   C   C   c   P   P   P   P
  -15   O   O   c   c   C   C   C   C   C   C   C   C   C   c   P   P   P   P
  -14   O   O   c   c   C   C   C   C   C   C   c   c   c   c   P   P   P   .
  -13   O   O   c   c   c   c   c   c   c   c   c   c   T   P   P   P   P   .
  -12   f   O   R   R   R   R   R   T   T   T   T   T   T   P   P   P   P   .
  -11   f   O   R   R   R   R   R   T   T   T   T   T   T   P   P   P   P   .
  -10   .   O   R   R   R   R   R   T   T   T   T   T   T   P   P   P   .   .
   -9   m   R   R   R   R   R   T   T   E   E   E   T   T   P   P   .   .   .
   -8   m   R   R   !   m   R   m   T   E   E   E   G   T   P   P   .   .   .
```

| Lettre | Sous-zone | Lettre | Sous-zone |
|---|---|---|---|
| C | Cité d'Astrub | c | Contour d'Astrub |
| F | Forêt d'Astrub | A | Champs d'Astrub |
| P | Prairies d'Astrub | a | Calanques d'Astrub |
| M | Exploitation minière d'Astrub | D | Campement des Bandits Manchots |
| T | Le coin des Tofus | R | Le champ du repos |
| E | Elevage de Bouftous du Château d'Amakna | G | Garnison d'Amakna |
| b | Le berceau | p | Plaine des Porkass |
| B | Prairie des Blops | O | Orée de la forêt des Abraknydes |
| f | Forêt des Abraknydes | m | La montagne des Craqueleurs |

Le seul « ! » est -2,-8. La référence dit « La montagne des Craqueleurs » ; la langue dit « Le temple Sacrieur »
(2061), avec deux cartes du Donjon des Larves superposées.

La cité, d'après la langue, couvre :
- y -22 : x 0 à 5 ;
- y -21 à -15 : x -1 à 7 ;
- y -14 : x -1 à 4.

Le Contour l'entoure en anneau : colonnes x -3 et -2, rangée y -13, colonne x 8, rangée y -14 de x 5 à 8, et le
haut (-2,-23 ; 3 à 6,-23 ; -2 et -1,-22 ; 6 à 8,-22). Au nord, de 0,-23 à 2,-23, la cité touche directement la
Forêt d'Astrub.

**Limites de la cité.** La langue compte 36 limites entre une carte de la cité et une carte extérieure voisine (33
avec le Contour, 3 avec la Forêt). Voici ce que le bot a noté en jeu (`data/carte.json`, état de 21 h 52) :

| État noté en jeu | Limites |
|---|---|
| Porte donnée par l'utilisateur, passage vu par le bot | 5 : -1,-20\|-2,-20 ; 5,-22\|5,-23 ; 7,-19\|8,-19 ; 4,-14\|4,-13 ; -1,-14\|-1,-13 |
| Bloquée vue | 9 : 0,-22 haut ; 1,-22 haut ; 2,-22 haut (Forêt) ; 3,-22 haut ; 4,-22 haut ; 0,-22 gauche ; -1,-21 gauche ; -1,-17 gauche ; -1,-16 gauche |
| **Passage vu, mais pas dans la liste des portes** | **5** : 0,-14\|0,-13 ; 1,-14\|1,-13 ; 2,-14\|2,-13 ; 3,-14\|3,-13 (notés dans les deux sens) ; -1,-21\|-1,-22 |
| Non vu | 17 : bords est (7,-21 à 7,-15 contre x 8), ouest (-1,-19, -1,-18, -1,-15, -1,-14 gauche), sud-est (4,-14 droite, 5 à 7,-15 bas), nord-est (5,-22 droite, 6,-21 et 7,-21 haut) |

Les 5 passages de la 3e ligne contredisent la liste des portes, si cette liste est complète. Il faut le vérifier en
jeu : soit la rangée -14 a d'autres ouvertures, soit la mémoire du bot est fausse. Deux éléments pèsent dans la
balance :
- à 21 h 50, le bot ou une autre session est passé de 2,-13 à 2,-14 en 7 s ;
- vers 21 h 50, une note « haut : bloquée » de -1,-21 a été retirée de la mémoire.

## 6. Champs de la langue utiles aux trajets

### 6.1 Champ p : soleils conditionnels (vérifié en jeu sur 8 cas)

Le champ `p` porte, sur 323 cartes, 428 objets. Chacun a le gfx 1029/1030, une case et une condition « BE>n ».
- 319 sont sur le **bord** d'une carte extérieure de 15 x 17, à l'endroit d'un soleil de sortie.
- Pour 307 de ces 319, n vaut exactement le champ `ep` de la carte voisine de ce côté, moins 1. Les 12 exceptions
  sont toutes des BE>6 autour de la Côte d'Asse (x 11 à 13, y 3 à 11), vers des voisines d'ep 2 ou 3.
- **8 de ces soleils sont sur des cartes que le bot a parcourues, et il les a vus exactement à la même case.** Sur 5
  autres bords, le bot n'a noté aucun soleil du côté indiqué.
- Aucun soleil n'a été vu à une autre case.

Interprétation, supposée :
- `ep` serait l'épisode de contenu de la carte : 1 pour Astrub et Incarnam, 2 et 3 pour Amakna, 9 pour l'Orée des
  Abraknydes, 13 pour Cania, 22 pour Otomaï...
- « BE>n » ferait apparaître le soleil quand l'épisode du serveur dépasse n.

Sur le serveur actuel, les soleils BE>4, BE>8 et BE>12 ont été vus : tous les épisodes utiles semblent ouverts. Ces
soleils donnent donc la **case exacte** d'une partie des sorties du Monde des Douze, alors que la référence n'en a
aucune.

Près d'Astrub (x de -5 à 12, y de -30 à -8), il y a 57 objets. Case (i, j) : repère du bot.

| Carte | Id | Sous-zone (ep) | Case (i, j) | Côté -> voisine (ep) | Condition | En jeu |
|---|---:|---|---|---|---|---|
| -1,-13 | 7337 | Contour d'Astrub (1) | (15, 31) | bas -> -1,-12 (5) | BE>4 | même case que le soleil vu |
| -2,-13 | 7321 | Contour d'Astrub (1) | (13, 31) | bas -> -2,-12 (5) | BE>4 | carte non vue |
| -2,-21 | 7313 | Contour d'Astrub (1) | (1, 13) | gauche -> -3,-21 (13) | BE>12 | aucun soleil vu de ce côté |
| -2,-22 | 7312 | Contour d'Astrub (1) | (1, 23) | gauche -> -3,-22 (13) | BE>12 | aucun soleil vu de ce côté |
| -2,-23 | 7311 | Contour d'Astrub (1) | (1, 13) | gauche -> -3,-23 (13) | BE>12 | même case que le soleil vu |
| -3,-13 | 7305 | Contour d'Astrub (1) | (1, 17) | gauche -> -4,-13 (9) | BE>8 | carte non vue |
| -3,-13 | 7305 | Contour d'Astrub (1) | (19, 31) | bas -> -3,-12 (5) | BE>4 | carte non vue |
| -3,-14 | 7304 | Contour d'Astrub (1) | (1, 21) | gauche -> -4,-14 (9) | BE>8 | carte non vue |
| -3,-15 | 7303 | Contour d'Astrub (1) | (1, 21) | gauche -> -4,-15 (9) | BE>8 | carte non vue |
| -3,-16 | 7302 | Contour d'Astrub (1) | (1, 19) | gauche -> -4,-16 (9) | BE>8 | même case que le soleil vu |
| -3,-17 | 7301 | Contour d'Astrub (1) | (1, 25) | gauche -> -4,-17 (9) | BE>8 | carte non vue |
| -3,-18 | 7300 | Contour d'Astrub (1) | (1, 15) | gauche -> -4,-18 (9) | BE>8 | même case que le soleil vu |
| -3,-19 | 7299 | Contour d'Astrub (1) | (1, 15) | gauche -> -4,-19 (9) | BE>8 | carte non vue |
| -3,-20 | 7298 | Contour d'Astrub (1) | (1, 9) | gauche -> -4,-20 (9) | BE>8 | carte non vue |
| 0,-13 | 7353 | Contour d'Astrub (1) | (11, 31) | bas -> 0,-12 (5) | BE>4 | même case que le soleil vu |
| 1,-13 | 7369 | Contour d'Astrub (1) | (9, 31) | bas -> 1,-12 (5) | BE>4 | même case que le soleil vu |
| -2,-24 à -2,-28 | 8591 à 8595 | Forêt d'Astrub (1) | (1, 15) | gauche -> -3,y (5) | BE>4 | aucun soleil vu de ce côté (-24, -25, -26) ; carte non vue (-27, -28) |
| -2,-29 | 8596 | Forêt d'Astrub (1) | (1, 15) | gauche -> -3,-29 (13) | BE>12 | carte non vue |
| -5,-20 | 3634 | Orée de la forêt des Abraknydes (9) | (13, 1) | haut -> -5,-21 (13) | BE>12 | même case que le soleil vu |
| -4,-20 | 3675 | Orée de la forêt des Abraknydes (9) | (11, 1) | haut -> -4,-21 (13) | BE>12 | carte non vue |
| -5, -4, -3,-24 | 3630, 3671, 3712 | Prairie des Blops (5) | (15, 31), (15, 31), (13, 31) | bas -> y -23 (13) | BE>12 | carte non vue |
| -5, -4, -3,-28 | 3626, 3667, 3708 | Prairie des Blops (5) | (15, 1), (17, 1), (15, 1) | haut -> y -29 (13) | BE>12 | carte non vue |
| -3,-10 ; -3,-11 | 7843, 7842 | Le champ du repos (5) | (1, 15) | gauche -> -4,y (9) | BE>8 | carte non vue |
| -4,-9 | 7831 | Le champ du repos (5) | (15, 1) | haut -> -4,-10 (9) | BE>8 | carte non vue |
| 1,-9 | 2056 | Le coin des Tofus (1) | (25, 31) | bas -> 1,-8 (5) | BE>4 | carte non vue |
| 2,-11 ; 2,-12 | 7811, 8534 | Le coin des Tofus (1) | (1, 15), (1, 11) | gauche -> 1,y (5) | BE>4 | carte non vue |
| 2,-8 | 7767 | Le coin des Tofus (1) | (27, 21) ; (1, 25) ; (14, 30) | droite -> 3,-8 (2) ; gauche -> 1,-8 (5) ; pas sur un bord | BE>1 ; BE>4 ; BE>1 | carte non vue |
| 2,-9 | 7766 | Le coin des Tofus (1) | (27, 27) | droite -> 3,-9 (2) | BE>1 | carte non vue |
| 3, 4, 5,-10 | 7764, 7763, 7795 | Le coin des Tofus (1) | (13, 31), (9, 31), (15, 31) | bas -> y -9 (2) | BE>1 | carte non vue |
| 6,-9 | 7798 | Le coin des Tofus (1) | (1, 15) ; (13, 31) | gauche -> 5,-9 (2) ; bas -> 6,-8 (2) | BE>1 | carte non vue |
| 7,-8 | 7777 | Le coin des Tofus (1) | (1, 15) ; (20, 22) | gauche -> 6,-8 (2) ; pas sur un bord | BE>1 | carte non vue |
| 2,-10 | 7765 | Le coin des Tofus (1) | (3, 9) | pas sur un bord | BE>1 | carte non vue |
| -2,-8 | 2061 | Le temple Sacrieur (2) | (19, 1) ; (27, 23) ; (7, 31) | haut ; droite ; bas (voisines d'ep 5) | BE>4 | carte non vue |
| 11,-22 | 8583 | Prairies d'Astrub (1) | (27, 15) | droite -> 12,-22 (2) | BE>1 | carte non vue |
| 12,-21 | 7855 | Prairies d'Astrub (1) | (3, 1) ; (25, 7) | haut -> 12,-22 (2) ; pas sur un bord | BE>1 ; BE>17 | carte non vue |
| 8,-8 | 8601 | Prairies d'Astrub (1) | (13, 31) | bas -> 8,-7 (2) | BE>1 | carte non vue |
| 10,-24 | 8538 | Exploitation minière d'Astrub (1) | (18, 8) | pas sur un bord | BE>1 | carte non vue |
| 6,-19 | 7557 | Souterrain d'Astrub (1) | (5, 31) | carte intérieure | BE>1 | carte non vue |

Aucun soleil conditionnel ne se trouve sur une limite de la cité. Ceux du Contour mènent vers l'ouest (Orée des
Abraknydes, Prairie des Blops) et vers le sud (Champ du repos, Coin des Tofus).

### 6.2 Champ v des sous-zones : sous-zones voisines (en partie supposé)

Chaque sous-zone a une liste `v` de sous-zones. Elle ressemble aux sous-zones voisines, peut-être celles de la
conquête de territoire (1.29) :
- sur les 777 liens, 644 sont réciproques ;
- sur les 306 paires de sous-zones qui se touchent sur la carte (cartes extérieures principales), 232 sont dans `v`.

Dans le cadre d'Astrub :
- **Cité d'Astrub** : `v` = Contour d'Astrub, Souterrain d'Astrub, Plaine de Cania. Elle ne contient pas la Forêt
  d'Astrub, qui touche pourtant la cité de 0,-23 à 2,-23. Le bot a justement vu ces trois limites bloquées : c'est
  cohérent avec « pas dans v = pas de passage ».
- Les autres paires qui se touchent sans être dans `v` sont des candidats à « pas de passage », non vérifiés :
  - Exploitation minière / Calanques d'Astrub (10,-24\|11,-24 ; 10,-25\|10,-24) ;
  - Champs d'Astrub / Campement des Bandits Manchots (3,-28\|4,-28) ;
  - Le berceau / Plaine des Porkass (-3,-30\|-2,-30) ;
  - Le coin des Tofus / Port de Madrestam (7,-8\|7,-7).

`v` ne dit rien à l'intérieur d'une paire autorisée : il n'aide pas pour la limite Cité / Contour.

### 6.3 Autres champs

| Champ | Contenu | Utilité pour les trajets |
|---|---|---|
| `o` | carte extérieure | distinguer une carte du monde d'un intérieur aux mêmes coordonnées (§ 4) |
| `p1`, `p2` | cases de placement de combat | aucune (combat) |
| `n` | nom de salle (donjons, « Salle 1 »...) : 178 cartes | aucune dehors |
| `d` | numéro de labyrinthe ou de donjon (Minotoror, Dragon Cochon, Bouftous, « Cité d'Astrub » 47...) : 118 cartes, toutes intérieures | aucune dehors |
| `t`, `c`, `tournament` | arènes de tournoi (en 123,123) : 37 cartes | aucune |
| `ep` | épisode supposé | lire les conditions BE>n (§ 6.1) |
| `sa.tt` | type de terrain (ville, forêt, souterrain...) | repérer les sous-zones souterraines |

Les 303 cartes extérieures de Bonta et Brâkmar font 19 x 22 cases (taille lue dans le SWF), pas 15 x 17. C'est à
retenir si le bot y va : son repère de cases est établi pour 15 de large.

## 7. Faits vus en jeu : contrôle

| Fait (utilisateur) | Langue | Référence |
|---|---|---|
| 4,-14 s'affiche « Cité d'Astrub » | Cité d'Astrub (7416, seule carte à ces coordonnées) | Cité d'Astrub |
| 4,-13, -1,-13, 0,-13, 1,-13, 2,-13, 3,-13 sont dehors | Contour d'Astrub (6 cartes) | Contour d'Astrub (6) |
| Porte -1,-20 \<-> -2,-20 | Cité \| Contour | Cité \| Contour |
| Porte 5,-22 \<-> 5,-23 | Cité \| Contour | Cité \| Contour |
| Porte 7,-19 \<-> 8,-19 | Cité \| Contour | Cité \| Contour |
| Porte 4,-14 \<-> 4,-13 | Cité \| Contour | Cité \| Contour |
| Porte -1,-14 \<-> -1,-13 | Cité \| Contour | Cité \| Contour |

Tout concorde. Le bot a aussi noté un passage à chacune des 5 portes. La seule tension avec la liste des portes est
celle des 5 passages supplémentaires du § 5.2.

## 8. Recommandations (rien n'a été appliqué)

Par ordre d'utilité pour les trajets :

1. **Donner les remparts de la cité à la référence.** Les 11 zones d'Astrub sont justes. Ce qui manque, ce sont les
   passages : `atlas.links` relie toute carte connue voisine, donc il fait passer les itinéraires à travers les murs.
   Sans changer le code du bot, on peut remplir `cartes["x,y"]["sorties"]` des cartes de part et d'autre des 36
   limites :
   - `"vers": "x,y"` pour une porte ;
   - `"vers": null` pour un mur (`atlas.links` ignore alors ce côté) ;
   - `"cases": []`, puisque la case du soleil n'est pas connue.

   Attention : dès qu'une carte a des `sorties`, `links` n'utilise **que** celles-ci. Il faut donc remplir ses quatre
   côtés, y compris ceux qui donnent dans la cité. Avant de le faire, il faut trancher en jeu les 5 passages
   contestés (§ 5.2) et les 17 limites non vues. La liste complète est dans `comparaison.json`,
   `regions."Monde des Douze".astrub.limites_de_la_cite`.
2. **Retirer de `cartes` les 20 cartes qui ne sont pas des cartes extérieures** (§ 4), ou les marquer comme
   intérieures. Elles créent de faux voisins : 16 intérieurs (Façade de Brâkmar, cimetière d'Amakna, -8,-5...) et 4
   coordonnées sans aucune carte.
3. **Ajouter les cartes extérieures manquantes** qui servent de passage. D'abord la colonne de la Plaine de Cania
   (-3,-59 à -3,-52) et les 7 temples. Ensuite Porte de Sufokia, Sanctuaire de Pandala et Plage de Corail -53,25.
   L'île de Nowel est facultative : 108 coordonnées, dont 92 absentes et 16 sans zone, île d'événement supposée. Il
   faut aussi donner leur zone aux 19 cartes « sans zone ».
4. **Renommer les zones** avec le nom exact de la langue, puisque le jeu affiche celui-ci :
   - « Le territoire des Bandits » -> « Territoire des Bandits » ;
   - « Burdure de Brâkmar » -> « Bordure de Brâkmar » ;
   - « Iles des naufragés » -> « Ile des naufragés » ;
   - « Village des Brigandins » -> « Village Brigandins » ;
   - « Île des Wabbits » et « Île des Wabbits Squelettes » -> « Ile ... » ;
   - « Le Château d'Amakna » -> « Le château d'Amakna » ;
   - « Gisgoul, le village dévasté » -> « Gisgoul, le village devasté » ;
   - « Fôret de Kaliptus » -> « Forêt de Kaliptus » ;
   - « Le bosquet du petit talus » -> « Le Bosquet du petit talus ».
5. **Découper les zones regroupées** selon la langue :
   - « Brâkmar » en ses 8 quartiers, plus « Centre-ville » et « Extérieur de l'arène » ;
   - les deux « Territoire des Dopeuls » en « Village Dopeul » et « Entrainement Dopeuls ».
6. **Corriger les erreurs isolées** du tableau 5.1. Par exemple, -2,-8 devient « Le temple Sacrieur », -19,25 à
   -19,28 deviennent « Façade de Brâkmar », -12,-19 et -12,-20 deviennent « Orée de la forêt des Abraknydes ».
7. **Couches** (Otomaï, Brigandins) : une coordonnée ne suffit pas. Il faudrait garder l'id de la carte, comme le
   fait déjà `incarnam.json`, ou au moins la sous-zone de la carte principale avec les couches en `aussi`.
8. **Incarnam** : renommer la zone 447 « Donjon d'Incarnam » en « Donjon » (nom de la langue 1384). Les 26 cartes des
   Pitons rocheux peuvent rester dans `interieurs`, puisque les liaisons d'Incarnam viennent des soleils.
9. **Facultatif** : reprendre dans la référence la case des 319 soleils conditionnels de bord (§ 6.1), avec la
   mention « supposé, vérifié sur 8 ». Garder aussi l'id de la carte principale pour le Monde des Douze. Les
   prochaines comparaisons seraient alors directes.

Mise en œuvre possible : une commande d'outil qui écrit un **nouveau** fichier à relire avant de remplacer la
référence. Elle n'est pas écrite.

## 9. Vérifié et supposé

Vérifié (sur les fichiers, refaisable avec `--comparer`) :
- tous les chiffres et toutes les listes de ce rapport ;
- Astrub : 121 cartes sur 121 concordent entre la référence et la langue ;
- les faits donnés par l'utilisateur concordent avec la langue et avec la référence ;
- les 21 noms de zone lus en jeu par le bot dans la cité sont égaux à la langue ;
- 8 soleils conditionnels sur 8 observés sont à la case vue par le bot ;
- les 9 limites « bloquées » notées par le bot sont toutes des limites de la cité selon la langue, et aucune n'est
  une porte.

Supposé, non vérifié :
- la carte principale d'une coordonnée est l'extérieure de plus petit id ;
- `ep` est un épisode et « BE>n » une condition sur l'épisode du serveur (307 cas sur 319, sans le code du client) ;
- `v` donne les sous-zones entre lesquelles on peut passer ;
- les 17 limites de la cité non vues sont des murs ;
- les 5 passages notés par le bot hors des portes sont à confirmer : ils contredisent la liste des portes ;
- la langue copiée (maps 1384) est celle que sert le jeu aujourd'hui.

Non fait : aucune case de carte n'est lisible (les cartes sont chiffrées, voir `cartes_client.md`). Les soleils non
conditionnels et les murs à l'intérieur d'une carte restent donc inconnus.

## 10. Fichiers

Créés ou modifiés :
- `tools/cartes_client.py`
  - Nouvelle commande `--comparer`.
  - Nouvelles fonctions : `normaliser`, `verdict_zone`, `cote_du_bord`, `comparer_reference`,
    `comparer_par_identifiant`, `detail_astrub`, `grille_zones`, `soleils_conditionnels`, `voisines_sous_zones`,
    `comparer`.
  - Nouvelles méthodes de `Langue` : `nom_sous_zone`, `super_zone`, `par_coordonnees`.
  - Les commandes existantes sont inchangées.
- `tests/test_cartes_client.py` : 5 tests de plus. Les 11 tests passent, et toute la suite aussi (719).
- `data/client_retro/comparaison.json` : le détail (340 Ko, ignoré par git).
- `docs/rapports/comparaison_reference.md` : ce rapport.

Non modifiés : `config/monde/*.json`, `data/carte.json`, `bot/`. Pendant la séance, `data/carte.json` (21 h 50) et
`config/settings.yaml` (21 h 46) ont été modifiés par un autre processus, pas par ce travail. Ma comparaison ne fait
que lire `data/carte.json`.

## Corrections appliquées (12/10)

L'utilisateur a donné son accord directement le 12/10 (« autant les prendre »). L'outil est `tools/corriger_monde.py`,
qui repart toujours de l'original : il peut être relancé. L'original est copié dans
`data/client_retro/monde_des_douze_avant_correction.json`. Incarnam n'a pas été touché.

| | Avant | Après |
|---|---|---|
| Cartes du Monde des Douze (atlas) | 4704 | 4794, exactement les cartes extérieures du jeu |
| Zones dans la référence | 131 | 165 |
| Zones utilisées comme zone principale d'une carte (atlas) | 126 | 156 |
| Cartes dont la zone change | — | 491, dont les 110 ajoutées |
| Lieux (zaaps, donjons) | 60 | 60 ; la zone des 33 donjons suit leur carte |
| Régions | 18 | 19, avec « Zone arctique » (île de Nowel) |

- **Retirées (20)** : cartes qui n'existent qu'en intérieur ou pas du tout, dont 10 de « Façade de Brâkmar », 3 du
  cimetière et -3,8 (où était notée une Orchidée Freyesque). Aucun lieu n'était dessus.
- **Ajoutées (110)** : île de Nowel (108), Plaine de Cania (8), temples (7), Plage de Corail,
  Porte de Sufokia, Sanctuaire de Pandala.
- **Noms exacts du jeu** :
  - 19 zones renommées, par exemple « Burdure de Brâkmar » → « Bordure de Brâkmar », « Fôret de Kaliptus » → « Forêt de
    Kaliptus », et les deux « Territoire des Dopeuls » → « Entrainement Dopeuls » / « Village Dopeul ».
  - Les noms portés à Bonta et à Brâkmar reçoivent le nom de leur ville : « Centre-ville (Bonta) », « Quartier de la
    Milice (Bonta) » / « Quartier de la milice (Brâkmar) »… L'atlas regroupe les zones par nom.
- **Découpées (11)** : Brâkmar en quartiers, Bonta, temples sortis de leur zone, plates-formes et prismes. Chaque
  morceau reprend la liste de monstres de la zone d'origine : on ne sait pas la répartir.
- **Couches** (village des Zoths, canopée du Kimbo, village de la Canopée) :
  - la zone de la carte reste celle que nommait la référence ;
  - les autres couches vont dans « aussi » ;
  - chaque couche garde sa propre zone et ses propres monstres.
- **Gardé tel quel** : ressources des cartes, liste des monstres, zaaps.
