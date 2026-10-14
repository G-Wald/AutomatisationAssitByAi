# Clés des cartes du client Dofus Retro (12/10)

Question : le client garde-t-il sur le disque (cache, stockage local) les clés des cartes reçues dans le paquet
« GDM|id|date|clé », ce qui permettrait de déchiffrer le `mapData` des cartes déjà visitées ? Contrainte : sans rien
installer et sans toucher au jeu.

**Réponse : non.** Aucune clé de carte n'est stockée sur le disque. Le bytecode du client le confirme : la clé reçue
va dans un tableau en mémoire du gestionnaire de cartes, et aucune classe qui touche ce tableau n'écrit de
SharedObject. Le déchiffrement n'a donc pas pu être essayé sur une vraie carte. En revanche, l'algorithme est
maintenant **vérifié dans le bytecode** : c'est exactement celui de la 1.29, et il est prêt dans
`tools/cles_cartes.py`.

Tout a été fait en lecture seule. Le jeu tournait peut-être pendant la copie (le Session Storage a changé de taille
entre deux lectures) : aucune action sur lui, ni réseau, ni mémoire.

## 1. Inventaire de %APPDATA%\Dofus Retro (lecture seule)

272 fichiers, 56,2 Mo. Commande : `.venv/Scripts/python -m tools.cles_cartes --inventaire`.

| Dossier ou fichier | Taille | Format | Contenu | Copié |
|---|---|---|---|---|
| `Pepper Data\...\#SharedObjects\Q5Y8PFX4\localhost\##*\*.sol` | 106 fichiers, 19,8 Mo | SharedObject Flash (AMF0) | voir ci-dessous | oui, sauf 9 |
| `Cache\` (`data_0..3`, `index`, 132 `f_*`) | 137 fichiers, 35,9 Mo | cache HTTP Chromium (fichiers de blocs + réponses) | langues du CDN | oui |
| `Session Storage\` | 6 fichiers, 15 Ko | LevelDB | un seul enregistrement « namespace », puis environ 750 lots vides de 12 octets | oui (hors LOCK, LOG) |
| `Local Storage\leveldb\` | 6 fichiers, 353 o | LevelDB | vide (journal de 0 octet) | oui (hors LOCK, LOG) |
| `config.json` | 326 o | JSON | réglages de l'appli : langue, Discord, chat sombre, tailles de texte, panneau | oui |
| `GPUCache\` | 5 fichiers, 557 Ko | cache GPU Chromium | shaders | non (sans rapport) |
| `Code Cache\` | 4 fichiers, 144 o | cache JS | index vides | non |
| `Preferences`, `Network Persistent State` | 158 o, 193 o | JSON Chromium | réglages du navigateur intégré | non |
| `Pepper Data\...\mms.cfg`, `FlashPlayerTrust\DofusRetro.cfg` | 119 o, 70 o | texte | autorisations du lecteur Flash | non |
| `Cookies`, `Cookies-journal` | 20 Ko, 0 o | SQLite | **identification : ni ouvert, ni copié** | non |
| `Dictionaries\`, `blob_storage\`, `CacheWritableAdobeRoot\AssetCache\` | vides | | | |

Les 106 SharedObjects (nom lu dans l'en-tête de chacun) :

| SharedObject | Nombre | Contenu | Copié |
|---|---|---|---|
| `ANKXTRASO_0_*`, `ANKXTRASO_0`, `ANKLANGSO_0` | 72 | langue (cartes, objets, sorts...) | déjà dans `data/client_retro/lang`, identique à la source (empreintes SHA-256 comparées) |
| `SHARED_OBJECT_DISPLAYS<personnage>` | 20 | compteurs `display1` à `display26` par personnage | oui |
| `SHARED_OBJECT_TIPS`, `SHARED_OBJECT_CACHEDATE`, `SHARED_OBJECT_OPTIONS` | 3 | astuces vues ; `clearDate: 2026-3-29` ; `dataBanksCount: 2` | oui |
| `settings`, `local/settings` (macromedia.com) | 2 | réglages Flash (`gain`, `allow`) | oui |
| `ANKOPTIONSSO` | 1 | options du client, dont le compte mémorisé en 1.29 | **non : en-tête seul lu, pas ouvert plus loin** |
| `<compte>_<n>_SHARED_OBJECT_SHORTCUTS` | 8 | raccourcis ; le nom du SharedObject contient le nom du compte | **non : en-tête seul lu** |

Non parcourus : `%APPDATA%\zaap`, `%APPDATA%\Ankama Launcher` (lanceur et comptes), `%LOCALAPPDATA%\Ankama\Dofus-dofus3`
et `Wakfu`. Aucun autre dossier « Dofus Retro » dans `%LOCALAPPDATA%`.

## 2. Copie (data/client_retro/stockage/, 54 Mo, ignoré par git)

`.venv/Scripts/python -m tools.cles_cartes --copier` : 169 fichiers de %APPDATA% et le code du client.
- `sharedobjects/` : les 25 SharedObjects copiables (renommés d'après leur nom interne) ;
- `cache_http/` : le cache HTTP entier ;
- `electron/` : Session Storage, Local Storage, `config.json` ;
- `code/` : `loader.swf`, `preloader.swf`, `modules/core.swf`, `modules/soma.swf`, `config.xml`, `D1*.html`,
  `js/D1*.js` (depuis `retroclient\`) et `main.jsc` (processus principal Electron) ;
- `inventaire.json` (noms de compte masqués), `recherche_cles.json` (résultat de la recherche).

Tout le travail qui suit porte sur ces copies.

## 3. Recherche de clés dans les copies

`.venv/Scripts/python -m tools.cles_cartes --chercher` : 241 fichiers (72 langues + 169 copies), **0 candidat**.

| Recherche | Résultat |
|---|---|
| Hexadécimal de 32 caractères ou plus (ASCII et UTF-16) | 1044 suites, toutes dans le cache HTTP : 522 ETag (`etag:"…"`) et 522 identifiants de relais CloudFront. Aucune dans un SharedObject. |
| « GDM » | aucun (le seul « GDM » brut était dans des octets compressés d'un SWF en cache ; le SWF décompressé n'en contient pas) |
| SWF en cache, décompressés (452) | ni « GDM » ni hexadécimal long |
| Entrées rangées sous 7331, 7329, 7314, 7414 | 18, toutes dans la langue et sans hexadécimal : `MA` (x, y, sous-zone, placements), `H` (portes de maisons des cartes 7331 et 7414), et des tables où ces nombres sont des ids d'objets, de recettes ou de réponses de dialogue (`I`, `ISTA`, `CR`, `D`) |
| Champs ressemblant à un cache de cartes | aucun : les SharedObjects hors langue ne contiennent que des compteurs, des astuces, une date et des réglages |
| Cache HTTP | uniquement `dofusretro.cdn.ankama.com/static-data/612/lang/` : `versions_fr.txt` et 41 fichiers de langue en 11 versions (mars à octobre). Aucun SWF de carte (les cartes sont dans l'installation). |
| Stockage web d'Electron | vide (Local Storage) ou presque (Session Storage : un enregistrement, sans « GDM », sans id de carte) |
| Code Electron | `main.jsc` (bytecode V8) ne contient ni « GDM », ni « mapData », ni « XMLSocket » ; les `js/D1*.js` (obscurcis) n'ont pas d'appel visible à un stockage local |

## 4. Le code du client : où va la clé (vérifié dans le bytecode)

`loader.swf` (SWF 8, 759 classes en DoInitAction) est obscurci : identifiants renommés en 2 ou 3 caractères de contrôle,
prédicats opaques (`GetTime`, `Push False ; Not`, `Push '\x07' ; CharToAscii`, `Push True`) devant des `If` toujours
pris, octets parasites derrière ces sauts, faux `ConstantPool`. Un désassemblage linéaire donne n'importe quoi ;
`tools/cles_cartes.py --code` suit le flot d'exécution et retire ces artifices. Les noms natifs (`parseInt`,
`substr`, `charCodeAt`, `fromCharCode`, `unescape`, `mapData`, `'X'`, `'.swf'`) restent lisibles.

**Classe réseau** (constantes `XMLSocket`, `onData`, `send`, `d2h`...) :
- `prepareKey(d)` : `s = "" ; pour i de 0 à d.length par pas de 2 : s += fromCharCode(parseInt(d.substr(i, 2), 16)) ;
  return unescape(s)` ;
- `checksum(s)` : `n = 0 ; pour chaque caractère : n += charCodeAt(i) % 16 ; return HEX_CHARS[n % 16]` ;
- `decypherData(d, k, c)` : `s = "" ; j = 0 ; pour i de 0 à d.length par pas de 2 :
  s += fromCharCode(parseInt(d.substr(i, 2), 16) ^ k.charCodeAt((j++ + c) % k.length)) ; return unescape(s)` ;
- `cypherData` (sens inverse, avec `d2h` et un échappement préalable des caractères < 32, > 127, « % », « + »).

**Gestionnaire de cartes** (constantes `maps/`, `.swf`, `mapData`, `CHECK_MAP_FILE_ID`...) :
- chargement : si la clé n'est pas vide, `this.<tableau>[Number(sID)] = prepareKey(clé)`, sinon l'entrée est
  supprimée ; puis chargement de `sID + "_" + date + ("X" si une clé existe) + ".swf"` ;
- lecture de la carte : si une clé existe pour cet id,
  `mapData = decypherData(mapData, clé, parseInt(checksum(clé), 16) * 2)`.

L'hypothèse de départ est donc **exacte** : `décalage = int(checksum, 16) * 2`, XOR de la paire n avec le caractère
`(n + décalage) % len(clé)` de la clé préparée, puis `unescape`. Une précision : le checksum est la somme des
`code % 16`, modulo 16 (même résultat que somme des codes modulo 16).

**Pas d'écriture sur le disque** : le tableau des clés (même nom obscurci) n'apparaît que dans 3 classes : le
gestionnaire de cartes, la classe réseau (son propre tableau de clés de chiffrement des paquets) et
`TemporisConfigManager` (un tableau de réglages du même nom d'origine). Les 12 classes qui appellent
`SharedObject.getLocal` ne mentionnent ni ce tableau, ni `prepareKey`, `checksum`, `cypherData`, `decypherData`.
Le module `soma.swf` n'utilise un SharedObject que pour le son (`soundCookies`).

**Format des cases** (même méthode) : la classe de décompression des cartes (constantes `backgroundNum`, `validCells`,
`lineOfSightCells`) découpe `mapData` par 10 caractères et décode chaque case avec exactement les masques de la 1.29
(`&32>>5`, `&1`, `&48>>4`, `&15`, `&56>>3`, `(&24)<<6`, `&60>>2`, `(&4)<<11`, `(&2)<<12`...).

## 5. Déchiffrement

Pas de clé trouvée : **aucune carte réelle n'a été déchiffrée**, et la carte -1,-19 (7331 : rempart, soleils aux
cases (3,31) et (17,31)) n'a pas pu servir de contrôle.

Ce qui est prêt et testé sur des données synthétiques (`tests/test_cles_cartes.py`) :
- `preparer_cle`, `checksum`, `decalage`, `dechiffrer`, `chiffrer` (aller-retour sur un `mapData` de 479 cases) ;
- `decoder_case` : active, ligne de vue, déplacement, niveau et pente du sol, gfx du sol, de l'objet 1 et de
  l'objet 2, objet 2 interactif ;
- `.venv/Scripts/python -m tools.cles_cartes --dechiffrer 7331 --cle <hexa>` : déchiffre la carte, refuse le
  résultat s'il n'a pas 10 caractères de l'alphabet par case (mauvaise clé), compte les cases actives et praticables
  et liste les soleils supposés avec leur case (i, j) du bot.

## 6. Ce que ça permettrait avec une clé

Pour chaque case (479 sur une carte de 15 x 17) :
- **cases praticables** : `deplacement > 0` sur une case active (0 = bloquée en 1.29, supposé ici) ;
- **murs et obstacles** : cases à `deplacement` 0, avec leur gfx d'objet 1 (murs, arbres, rochers) et la ligne de vue ;
- **soleils** : objet 2 de gfx 1030 (ou 1029), comme les soleils conditionnels de la langue (supposé) ;
- ensuite, les groupes de cases praticables reliées entre elles, donc les soleils atteignables les uns depuis les
  autres, ce qui manquait à `docs/rapports/cartes_client.md`.

## 7. Vérifié, supposé, limites

Vérifié :
- aucun fichier copié ne contient de clé, de « GDM » ou d'hexadécimal long inexpliqué ;
- algorithme de préparation, de checksum et de déchiffrement (bytecode de `loader.swf`) ;
- la clé est rangée dans un tableau en mémoire ; aucune classe qui écrit un SharedObject n'y touche ;
- découpage par 10 caractères et masques de bits des cases.

Supposé :
- noms des champs des cases (repris de la 1.29 : ceux du client sont obscurcis), `deplacement` 0 = bloquée,
  gfx 1029/1030 = soleil ;
- `unescape` pour des octets > 0x7F (lus en UTF-8 comme Flash 6+) : sans effet sur l'alphabet des cartes, mais une
  clé contenant de tels octets serait à vérifier ;
- la clé d'une carte est fixe tant que le fichier de la carte ne change pas (comme en 1.29) ;
- `main.jsc` n'a pas été désassemblé : on sait seulement qu'il ne contient pas les chaînes cherchées et que le client
  Flash parle au serveur par son propre `XMLSocket`.

Limites :
- `ANKOPTIONSSO` et les 8 SharedObjects de raccourcis n'ont pas été ouverts (identifiants de compte possibles). Le
  bytecode montre qu'aucune classe qui écrit un SharedObject ne touche aux clés, mais leur contenu n'a pas été
  regardé ;
- le contrôle du bytecode se fait par noms : une sauvegarde indirecte (objet entier passé ailleurs) y échapperait ;
- la copie est une photo du 12/10 ; une mise à jour du client peut changer le code (relancer `--copier` et `--code`).

Pour obtenir une clé, il reste :
- le paquet GDM (réseau) ou la mémoire du client : exclus par les règles du projet ;
- une piste hors ligne, **non essayée** : le chiffrement est un XOR à clé répétée sur un texte très contraint
  (64 caractères possibles, nombreuses cases inactives presque identiques). La clé pourrait probablement se
  retrouver à partir du seul SWF d'une carte. C'est à décider avant d'aller plus loin.

## 8. Fichiers créés

- `tools/cles_cartes.py` : inventaire, copie, recherche, analyse du bytecode AVM1 (suivi du flot, prédicats opaques),
  algorithme de déchiffrement et décodage des cases. Commandes : `--inventaire`, `--copier`, `--chercher`, `--code`,
  `--dechiffrer ID --cle HEXA`.
- `tests/test_cles_cartes.py` : 14 tests (algorithme, vecteur calculé à la main, cases, sauts déguisés, inventaire
  qui n'ouvre pas les cookies, copie qui ne modifie pas la source, recherche ; deux tests sur les vraies copies,
  sautés si elles sont absentes).
- `data/client_retro/stockage/` (copies, `inventaire.json`, `recherche_cles.json`).
- Ce rapport. Aucun fichier existant n'a été modifié.

## 9. Index des cartes en clair (sans aucun déchiffrement)

Le `mapData` (cases) reste chiffré et n'est pas attaqué. Mais deux sources sont lisibles **sans clé** et rassemblent
beaucoup de ce que le bot cherche. `tools/cartes_index.py --construire` les lit et écrit `data/client_retro/cartes/`
(ignoré par git) en **2 secondes** pour les 9306 cartes, 5,4 Mo.

Sources :
- la **langue des cartes** (`MA`) : coordonnées, sous-zone, zone, extérieur/intérieur, cases de placement de combat
  (`p1`/`p2`), soleils conditionnels (`p`) ;
- l'**en-tête de chaque SWF** : largeur, hauteur, fond, ambiance, musique, `capabilities` (bits) et les quatre droits
  `canAggro`/`canUseInventory`/`canUseObject`/`canChangeCharac`.

Fichiers écrits :
- `index.json` : 9306 cartes, une entrée `{x, y, sous-zone, zone, super-zone, extérieur, largeur, hauteur, cases,
  fond, ambiance, musique, capacités, 4 droits}` ;
- `coordonnees.json` : `"x,y" -> [ids]` (4865 coordonnées, 1053 à plusieurs cartes : intérieurs, couches, salles) ;
- `placements.json` : cases de placement des deux équipes, en cases (i, j) du bot sur les cartes de 15 de large
  (9090 cartes) ;
- `objets_langue.json` : portes de maisons (1095 portes sur 451 cartes, par case, avec le nom de la maison) et
  soleils conditionnels (428 sur 323 cartes, case + condition « BE>n ») ;
- `catalogue_objets_interactifs.json` : 194 dessins d'objets interactifs -> métier (récolte, atelier, zaap, porte,
  coffre, enclos, levier, statue...) et compétences. **Catalogue seulement : la position des objets interactifs est
  dans le mapData chiffré, donc absente.**
- `zones.json` : noms des zones, sous-zones (avec leur type : ville, donjon...) et super-zones.

Lecture par le bot : `IndexCartes` (classe du même module) charge à la demande et donne `carte(id)`,
`cartes_en(x, y)`, `carte_principale(x, y)`.

Contrôles :
- 7331 (-1,-19) : extérieure, Cité d'Astrub, 15x17, trois portes de maisons (cases 124, 129, 446) ; 7414 (4,-16,
  banque) : la coordonnée regroupe 6 cartes (extérieure + intérieurs) ;
- les 148 357 cases de placement tiennent toutes dans la grille (0..28, 0..32) du bot.

À quoi ça sert : **trajets** (coordonnées, voisinage, intérieurs d'une case), **combat** (cases de placement des deux
équipes avant même d'entrer, musique/ambiance), **métiers** (catalogue des récoltes et ateliers par dessin, à
reconnaître à l'écran), **donjons** (sous-zones de type « donjon », déjà reliées par `tools/langue_client.py`).

Ce qui N'y est PAS, faute de clé : cases praticables, ligne de vue, murs, soleils réels et objets interactifs posés
sur chaque carte. Ces informations continuent de venir de l'observation en jeu (`data/carte.json` : soleils vus,
accessibilité). Limites : les cases (i, j) ne sont établies que pour les cartes de 15 de large ; `capabilities` est
gardé en brut (bits non détaillés) ; l'index est une photo de la copie du 12/10.

Fichiers : `tools/cartes_index.py`, `tests/test_cartes_index.py` (9 tests), `data/client_retro/cartes/`.
