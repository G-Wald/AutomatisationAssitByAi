# Cartes du client Dofus Retro (11/10)

Question : peut-on lire les fichiers de cartes du client installé pour en tirer une base de référence (coordonnées,
cases praticables, soleils, soleils atteignables entre eux) ?

**Réponse : non pour les cases.** Le `mapData` (les cellules) est chiffré dans 100 % des cartes. La clé est envoyée par
le serveur à l'entrée sur la carte et n'est pas dans le client. Conformément à la consigne, je me suis arrêté à
l'étape 3 : aucune recherche de clé et aucune tentative de déchiffrement.

Ce qui se lit en clair : l'en-tête de chaque carte (taille, fond, musique, intérieur/extérieur) et la langue des
cartes (id -> x, y, sous-zone, zone, cases de placement de combat).

## 1. Où sont les fichiers

Installation (lecture seule, rien n'a été modifié) : `%LOCALAPPDATA%\Ankama\Retro\` (Electron, `Dofus Retro.exe`).
Le client Flash est dans `resources\app\retroclient\` (412 Mo) :

| Dossier | Contenu | Taille |
|---|---|---|
| `clips\` | graphismes (gfx, sprites, objets, maps...) | 256 Mo |
| `audio\` | sons | 118 Mo |
| `data\maps\` | **9306 cartes** `<id>_<date>X.swf` (ids 4 à 20019) | 24,6 Mo |
| `data\docs\`, `data\tutorials\` | documents et tutoriels | 1,3 Mo |
| `loader.swf`, `modules\` | code du client | 5,2 Mo |

**Les fichiers de langue ne sont pas dans l'installation.** `config.xml` les fait venir du CDN Ankama. Le client les
garde dans ses SharedObjects Flash (format AMF0) :
`%APPDATA%\Dofus Retro\Pepper Data\Shockwave Flash\WritableRoot\#SharedObjects\Q5Y8PFX4\localhost\##<hash>\*.sol`
(107 fichiers, 20 Mo). Chaque fichier porte son nom dans son en-tête : `ANKXTRASO_0_MA` = langue des cartes,
`ANKLANGSO_0` = textes, etc. Version de la langue des cartes : **maps 1384**.

## 2. Ce qui a été copié (data/client_retro/, 44 Mo, ignoré par git)

- `maps/` : les 9306 SWF de cartes, tels quels (24,6 Mo).
- `lang/` : les 72 SharedObjects `ANK*` (sauf `ANKOPTIONSSO`), renommés d'après leur nom interne
  (`ANKXTRASO_0_MA.sol`...), soit 19,7 Mo. Les raccourcis et l'affichage propres à chaque personnage ne sont pas copiés.

Pour rafraîchir la copie après une mise à jour du jeu : `.venv/Scripts/python -m tools.cartes_client --copier`
(lecture seule de la source, écriture dans data/client_retro/ seulement).

## 3. Format des cartes

- SWF compressé « CWS » (zlib à partir de l'octet 8), version 6. Tags : SetBackgroundColor, **un DoAction**,
  ShowFrame, End.
- Le DoAction contient un pool de constantes, un appel `System.security.allowDomain(_parent._url)`, puis des
  affectations : `id, width, height, backgroundNum, ambianceId, musicId, bOutdoor, capabilities, mapData, canAggro,
  canUseInventory, canUseObject, canChangeCharac`. Le mini-interpréteur AVM1 de l'outil les lit toutes.
- Tailles : 15 x 17 pour 7330 cartes (479 cases), puis 8 x 13, 11 x 15, 19 x 22... (intérieurs).
  Nombre de cases = largeur x hauteur + (largeur - 1) x (hauteur - 1).

### mapData est chiffré : 9306 cartes sur 9306

Constats (vérifiés sur toutes les cartes) :
- `mapData` ne contient que des caractères hexadécimaux, alors qu'un `mapData` en clair (format 1.29) utilise
  l'alphabet `a-z A-Z 0-9 - _`.
- Sa longueur vaut exactement **20 caractères par case** (2 chiffres hexa par octet, 10 octets par case), pour 9305
  cartes. Une seule exception : la carte 7787 (8 x 12, 173 cases annoncées, 180 cases de données).
- Tous les octets sont ≤ 0x7F : compatible avec un texte ASCII combiné à une clé ASCII (XOR, comme en 1.29).
- **Tous les noms de fichier se terminent par « X »**. Le pool de constantes du gestionnaire de cartes de
  `loader.swf` contient `maps/`, `_`, `X`, `.swf`, `mapData`... : le nom est construit comme en 1.29, où le « X »
  signale une carte dont la clé arrive du serveur (paquet GDM id|date|clé).
- Les noms des fonctions de déchiffrement ne sont pas lisibles dans `loader.swf` : on voit seulement des constantes
  `fromCharCode`, `charCodeAt`, `unescape` dans le module réseau.
- La clé ne figure ni dans les SWF des cartes ni dans la langue `MA` (champs connus ci-dessous, aucun ne ressemble
  à une clé).

Conclusion : sans la clé de chaque carte, les cases ne sont pas lisibles : praticabilité, calques graphiques,
soleils, portes. Les étapes 5 à 7 (cellules, soleils, composantes connexes, `cartes.json`) n'ont donc **pas** été
faites.

## 4. Langue des cartes (ANKXTRASO_0_MA.sol), lisible en clair

`MA` contient 4 tables :
- `m` : 9276 cartes, `{x, y, sa, ep, o?, p1?, p2?, p?, n?, d?...}`.
- `sa` : 263 sous-zones (`n` nom, `a` zone, `tt` type, `m` musique, `v` voisines ?).
- `a` : 30 zones (`n`, `sua`).
- `sua` : 2 super-zones (« Continent Amaknien », « Zone de départ »).

Correspondances :
- toutes les cartes de la langue ont un fichier ; 30 fichiers n'ont pas d'entrée dans la langue ;
- `o` (présent sur 5349 cartes) = `bOutdoor` du SWF, **sur 9276 cartes sur 9276 (vérifié)** ; une même coordonnée
  regroupe la carte extérieure et les intérieurs (maisons, banque...) ;
- `p1`, `p2` : cases de placement de combat des deux équipes, 2 caractères de l'alphabet par case (format 1.29,
  **supposé** : valeurs cohérentes, 0 à 478, mais non comparées à un combat réel) ;
- `p` (323 cartes) : objets conditionnels `{gfx: [1029, 1030], case, condition « BE>n »}`. 1030 est, en 1.29, le gfx
  du soleil : ce seraient des soleils qui n'apparaissent que sous condition (**supposé**).

### Cartes des exemples

| Coordonnées | Carte extérieure | Sous-zone | Autres cartes (intérieurs) |
|---|---|---|---|
| -2,-20 | 7314 | Contour d'Astrub (92) | aucune |
| -1,-20 | 7329 | Cité d'Astrub (95) | 7555 |
| -1,-19 | 7331 | Cité d'Astrub (95) | 7694 à 7698 (9 x 12) |
| 4,-16 | 7414 | Cité d'Astrub (95) | 7547 (19 x 22), 7549, 7778, 7779, 12220 |

La banque de 4,-16 est probablement l'un des intérieurs, à confirmer : 7547 est le plus grand (19 x 22). 12220 a le
même fond et les mêmes placements que 7414 : c'est peut-être une variante de la carte extérieure.

## 5. Ce qui se décode et ce qui ne se décode pas

| Donnée | État |
|---|---|
| Décompression SWF, variables du DoAction | décodé, toutes les cartes |
| Taille, fond, musique, ambiance, capacités, extérieur | décodé |
| id -> x, y, sous-zone, zone | décodé (langue maps 1384) |
| Cases de placement de combat | décodé (format supposé) |
| Cases : praticabilité, ligne de vue, calques, soleils, portes | **impossible : mapData chiffré** |
| Soleils atteignables entre eux (composantes connexes) | **impossible** (dépend des cases) |

## 6. Validation sur les exemples

- Soleils de -2,-20, -1,-20, -1,-19 et 4,-16 : **non vérifiables**, les cases sont chiffrées. Aucune correspondance
  avec le jeu n'a pu être testée.
- Repère des cases : l'outil reprend la formule de `tools/import_monde.cell_to_grid` (case 19 -> (9,1), case 173 ->
  (27,11)), déjà vérifiée en jeu sur des soleils cliqués. Il ne l'applique qu'aux cartes de 15 cases de large.
- Coordonnées : la langue place bien 7329 en -1,-20 et 7331 en -1,-19, dans « Cité d'Astrub ». C'est cohérent avec
  la zone que le bot lit à l'écran, mais non confronté à une capture précise.

## 7. Limites et incertitudes

Vérifié :
- 100 % des `mapData` sont en hexadécimal, avec 20 caractères par case (sauf la carte 7787) ;
- tous les noms de fichier finissent par « X » ;
- `o` de la langue = `bOutdoor` du SWF sur toutes les cartes.

Supposé, non vérifié :
- le chiffrement est un XOR avec la clé du paquet GDM, comme en 1.29 (cohérent avec ce qu'on voit, mais je n'ai pas
  lu le code de déchiffrement) ;
- le format des cases `p1`/`p2` ;
- `p` = soleils conditionnels (gfx 1029/1030).

Limites :
- la langue copiée est celle du cache du client à la date de la copie (maps 1384) ; une mise à jour du jeu peut la
  changer ;
- seuls 72 SharedObjects ont été copiés ; d'autres langues (indices `HI`, maisons `H`, objets interactifs `IO`...)
  sont dans la copie mais pas exploitées.

## 8. Fichiers créés

- `tools/cartes_client.py` : lecture SWF + AVM1, bilan du chiffrement, lecteur AMF0 des SharedObjects, langue des
  cartes, copie depuis l'installation. Commandes : `--stats`, `--carte -1,-19`, `--id 7331`, `--copier`.
- `tests/test_cartes_client.py` : 6 tests (SWF et SharedObject synthétiques, repère des cases ; un test sur la vraie
  copie, sauté si elle est absente).
- `docs/rapports/cartes_client.md` : ce rapport.
- `data/client_retro/maps/` (9306 SWF), `data/client_retro/lang/` (72 .sol).
- Pas de `data/client_retro/cartes.json` : sans les cases, il n'aurait contenu ni soleils ni groupes atteignables.
