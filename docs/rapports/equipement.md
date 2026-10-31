# Équipement : choisir le meilleur stuff de chaque personnage, et un stuff « pods » pour la banque (30/10)

Proposition seulement : rien n'est codé dans `bot/`. Un essai hors jeu, en lecture seule, chiffre les exemples :
`tools/equipement_essai.py` (il ne touche ni au jeu ni aux fichiers, il affiche).

Demande : pour le mode banque « principal » (`bot/team/trade.py`), le principal doit porter du stuff de pods. Mais il
faut d'abord un système qui choisit, pour chaque personnage, l'équipement le plus efficace, selon des consignes
(PA, PM, dommages, éléments) et en respectant les conditions et les panoplies.

## 1. En bref

- **Tout est déjà dans les fichiers du client** (`data/client_retro/lang`, client Rétro 1.49). On n'a pas besoin
  d'internet pour les objets : effets et jets min/max, conditions, niveau, poids, panoplies et leurs bonus, PA de
  l'arme. Internet ne sert qu'à vérifier les règles et à lire des builds.
- **Le serveur donne les vrais jets** des objets possédés (`ASK` à la connexion, `bot/brain/inventory.py`), dans le
  même format que la langue : un seul lecteur pour les deux.
- **Le score d'un équipement = les dégâts par tour qu'il ajoute**, calculés avec la formule déjà validée
  (`bot/brain/stats.py`) et les sorts lus de chaque personnage, plus des poids que tu choisis pour ce qui ne fait pas
  de dégâts (PM, vitalité, sagesse, pods...).
- **Le client a les « ensembles d'équipement »** (Équipement rapide, 10 ensembles, Rétro 1.39) : on enregistre un
  ensemble « Combat » et un ensemble « Pods » par personnage, et changer de stuff devient un seul « Tout équiper ».
  C'est la façon la plus sûre de passer en mode pods puis de revenir au stuff de combat.
- **Toujours une proposition d'abord** : le bot affiche ce qu'il changerait et pourquoi ; il n'équipe rien tant que
  tu n'as pas dit oui (et jamais en combat).

## 2. Les données locales

| Table (langue) | Contenu utile | Exemple |
|---|---|---|
| `I.u[modèle]` | `n` nom, `t` type, `l` niveau, `w` poids, `c` conditions, `s` panoplie, `e` arme, `tw` deux mains, `p` prix | Ceinture du Bouftou : niv. 20, panoplie 1 |
| `I.t[type].t` | super-type = emplacement : 1 amulette, 2 arme, 3 anneau, 4 ceinture, 5 bottes, 10 coiffe, 11 cape (et sac à dos), 12 familier, 13 dofus, 7 bouclier | |
| `ISTA[modèle]` | effets possibles, `effet#min#max#...` en hexadécimal | Coiffe du Bouftou `76#1#28` = Force 1 à 40, `7e#1#28` = Intelligence 1 à 40 |
| `IS[panoplie]` | `i` objets, `b["e<n>"]` bonus quand n objets sont portés | Bouftou 7/7 : Force 50, Intelligence 50, dommages 5, sagesse 20, **PA 1**, vitalité 30 |
| `E[effet]` | texte de l'effet | 158 « Augmente le poids portable de #1 à #2 pods » |

Effets qui comptent (décimal) : 111 PA, 128 PM, 112 dommages, 138 % dommages, 118 Force, 126 Intelligence,
123 Chance, 119 Agilité, 125 Vitalité, 124 Sagesse, 117 portée, 158 pods, 182 invocations, 176 prospection
(152 à 157, 168, 169 : les malus). Dommages d'arme : 96 eau, 97 terre, 98 air, 99 feu, 100 neutre (91 à 95 : vols
de vie).

Arme, champ `e` : `[bonus coup critique, PA, portée min, portée max, CC 1/x, EC 1/x, en ligne, ligne de vue]`
(Marteau du Bouftou : 5 PA ; Dagues du Bandit : 3 PA ; Agride : 4 PA, Sagesse 11 à 20, condition Sagesse > 15).

Conditions (`c`) : `&` et, `|` ou ; `CS` `CI` `CC` `CA` `CV` `CW` = Force, Intelligence, Chance, Agilité, Vitalité,
Sagesse **totales** (équipement compris) ; `Cs` `Ci`... = de base (parchemins) ; `PG` classe (3 Enutrof, 9 Crâ,
10 Sadida) ; `PJ>2,20` = métier 2 (Bûcheron) au-dessus du niveau 20 ; `PS` sexe, `Ps`/`Pa` alignement, `PN` nom du
personnage, `PX` maître de jeu. Le niveau du personnage doit être au moins celui de l'objet (client :
« Ton niveau est trop faible pour prendre cet objet »).

À écarter d'office (vu dans la langue, qui contient aussi le contenu récent de Rétro) : objets « Lié au compte »
(effet 983 : événements, boutique), objets à 1 kama (Annobusé de Maître Jarbo, Amulette de Jiva), arme sans description
non échangeable (Épée Clipse), Écailles et Dofus de niveau 6 (événements).

Remarque : `tools/cartes_client.lire_sol` lit les chaînes en utf-8 ; les noms d'objets de la table I sont en latin-1
(« Ep�e »). L'essai les relit en latin-1 quand l'utf-8 échoue ; à corriger dans `lire_sol` le jour où on s'en sert
dans le bot (les noms lus par `tools/langue_client.py` ont probablement le même défaut).

## 3. Règles du jeu (Rétro) et ce qu'en disent les guides

| Fait | Source | Confiance |
|---|---|---|
| 1 point de Force = 5 pods ; la Force augmente les dégâts terre et neutre | [Gamosaurus, parchemins de Force Rétro](https://www.gamosaurus.com/jeux/dofus-retro/toutes-les-obtentions-des-parchemins-de-force-sur-dofus-retro) | haute |
| Pods max = 1000 + 5 × Force + 5 × somme des niveaux de métier + pods des objets | serveur (`Ow`, 26/10) : Fourboun, Force 0, métiers 41+50+35+32+2 = 160 → 1000 + 800 = **1800**, exact. Walitcoin (1698) colle si sa Ceinture du Bouftou a tiré 243 pods (à vérifier sur son inventaire) | moyenne (un seul cas exact) ; les 1000 de base sont aussi donnés par JOL, mais pour Dofus 2 |
| Surchargé : « Action impossible car tu es surchargé » | langue du client (`ERROR_295`) | haute |
| Ensembles d'équipement : 10 ensembles, nom et icône, « Enregistrer l'équipement actuel », « Tout équiper » avec confirmation « Veux-tu équiper l'ensemble sélectionné ? » | [Gamosaurus, Rétro 1.39](https://www.gamosaurus.com/jeux/dofus-retro/dofus-retro-resume-du-nouveau-contenu-de-la-mise-a-jour-1-39) + langue (`CUSTOM_SET_*`) | haute (où est le bouton : à voir en jeu) |
| Crâ : voie feu (Intelligence jusqu'à 250) la plus conseillée ; terre et air possibles, eau déconseillée. Stuff feu niv. 20 : panoplie Bouftou + Anneau du Mental ; niv. 43 : Kwak de Flammes + Kam Assutra + Scaranneau Rouge ; Gelano au niv. 60 | [Gamosaurus, stuff Crâ Rétro](https://www.gamosaurus.com/jeux/dofus-retro/guide-du-stuff-cra-sur-dofus-retro) | haute (guide 2020-2022) |
| Enutrof : Chance (230) ou Force (250) ou Chance + Force (200 chacune) ; Agilité déconseillée ; pelle 100 %, marteau 95 % des dégâts. Niv. 20 : Bouftou ; niv. 21 eau : Moskito + Paysan ; niv. 43 : Kwak (Glace ou Terre) ; niv. 38 multi : Prespic + Bandit | [Gamosaurus, stuff Enutrof Rétro](https://www.gamosaurus.com/jeux/dofus-retro/guide-du-stuff-enutrof-sur-dofus-retro) | haute |
| Sadida : feu (Intelligence 300), terre, multi ou « fourbe » (Force et Intelligence puis Vitalité, sorts Tremblement, Puissance Sylvestre, La Sacrifiée) ; bâton 100 %, marteau 95 %. Niv. 20 : Bouftou ; niv. 43 : Kwak + Broche Céleste ou Dragolyre Remaniée ; niv. 44 fourbe : Prespic + Broche Céleste + Anobra | [Gamosaurus, stuff Sadida Rétro](https://www.gamosaurus.com/jeux/dofus-retro/guide-du-stuff-sadida-sur-dofus-retro) | haute |
| Les panoplies donnent des PA/PM par paliers (Bouftou 7/7 : +1 PA ; Kwak 6/8 : +1 PM, 7/8 : +1 PA ; Corbac 3/5 : +1 PA) | langue `IS` | haute |
| Deux anneaux identiques ? Le client a « Déjà équipé ! », sans dire quand | langue (`ALREADY_EQUIPED`) ; aucun guide Rétro trouvé | **inconnue** : à tester en jeu |
| Un objet reste-t-il porté si on retire celui qui remplissait sa condition (ex. Force) ? | rien trouvé | **inconnue** : on équipe dans l'ordre qui remplit les conditions, et on revient au stuff de combat par un ensemble |

Le guide JOL « Débuter » (pods par niveau de métier, 1000 pods de base) est écrit pour Dofus 2 : pas utilisé.

Nos personnages comparés aux guides : Fourboun (Sadida 43, Force et Intelligence 0, dommages +23) est un **Sadida
fourbe** (dommages fixes, Tremblement compté à chaque tour de poison) ; les Enutrof Walitcoin et Enu-six sont
Force + Intelligence (Marteau du Bouftou : terre et feu) ; les Crâ n'ont presque rien en éléments (Elseiso : -10)
et portent l'Agride pour la Sagesse.

## 4. Ce que vaut chaque caractéristique, personnage par personnage

Calcul de l'essai (`--valeurs`) : meilleure suite de sorts d'un tour, une cible, sans déplacement, avec les PA du
personnage et les sorts lus (poisons comptés sur leurs tours, sorts à relance divisés par la relance), puis le gain
de dégâts quand on ajoute une caractéristique. L'arme n'est pas encore dans le calcul.

| Personnage | Classe, niv. | Dégâts/tour | +1 PA | +2 PA | +10 Fo | +10 In | +10 Ch | +1 Do | +10 %Do | Suite retenue |
|---|---|---|---|---|---|---|---|---|---|---|
| Fourboun | Sadida 43 | 62 | 15 | 16 | 1,4 | 0,4 | 0 | 1,8 | 1,8 | Ronce + Tremblement |
| Fourbodeux | Sadida 34 | 42 | 0 | 24 | 1,4 | 0,2 | 0 | 1,6 | 1,6 | Ronce + Tremblement |
| Eldieso | Crâ 36 | 41 | 3 | 16 | 0 | 1,4 | 0,3 | 2,0 | 1,8 | Flèche Enflammée + Immobilisation |
| Waldarch | Crâ 49 | 34 | 3 | 22 | 0 | 1,8 | 0,4 | 2,0 | 2,1 | Flèche Enflammée + Immobilisation |
| Elseiso | Crâ 47 | 32 | 2 | 12 | 0 | 1,8 | 0,3 | 2,0 | 2,2 | Flèche Enflammée + Immobilisation |
| Enu-six | Enutrof 41 | 44 | 11 | 11 | 0 | 1,8 | 0 | 2,0 | 1,8 | Pelle Fantomatique + Roulage de Pelle |
| Walitcoin | Enutrof 28 | 34 | 16 | 16 | 0 | 1,2 | 0,4 | 2,0 | 1,5 | Lancer de Pièces + Pelle Fantomatique |
| Pesopepe | Enutrof 26 | 49 | 0 | 15 | 0 | 0 | 0,9 | 3,0 | 0,9 | 3 × Lancer de Pièces |

Ce qu'on en tire :
- **À ces niveaux, 1 dommage fixe vaut 10 à 14 points de caractéristique** (les sorts ont des bases de 10 à 25) :
  Masque Kanniboule, La Mokette (+3 à 7), Anobra (+4 à 5) battent la plupart des objets à caractéristiques.
- **Les PA vont par paliers** : un Crâ à 6 PA gagne peu au 7ᵉ (3 dégâts) mais beaucoup au 8ᵉ (deux flèches à 4 PA :
  +12 à +22). Le score doit donc évaluer le stuff entier, pas objet par objet.
- **L'élément suit les sorts, pas la caractéristique actuelle** : la Force de Walitcoin (90) ne sert à aucun sort de
  sa meilleure suite (eau + feu) ; elle sert à son marteau (terre). D'où l'arme à ajouter au calcul.
- Limites : une cible (Flèche Enflammée et Ronces Multiples touchent plusieurs ennemis), pas de coups critiques, PM
  et portée non chiffrés (poids à choisir), et la suite « idéale » n'est pas toujours celle que joue la tactique.

## 5. Conception

### 5.1 Entrées

1. **Catalogue** (langue) : tous les objets équipables, leurs jets min/max, conditions, panoplie ; chargé une fois.
2. **Ce qu'on possède** : sac de chaque personnage (`ASK` à la connexion, jets réels), objets équipés (place 0 à 15),
   coffre de banque quand il est ouvert (`EL`), ce qui passe par échange. Sans coffre lu : le sac seul.
3. **Le personnage** : niveau, classe, caractéristiques de base (serveur : `As`, base + équipement + bonus séparés),
   sorts lus (`data/personnages`), métiers (pour les sacs de récolteur).
4. **Tes consignes** : un profil par personnage (5.2).

Dans un premier temps, deux usages : (a) **optimiser avec ce qu'on a** (sac + coffre + ce que les autres portent),
(b) **conseiller quoi acheter ou crafter** (catalogue entier, niveau ≤ niveau du personnage), sans rien faire.

### 5.2 Profils (tes consignes)

Un fichier `config/equipement.yaml` (proposition) :

```yaml
Fourboun:
  combat: {profil: sadida_fourbe, pa_min: 7, poids: {pm: 6, vitalite: 0.05}}
  pods: {pour: banque}            # profil pods pour le principal
Eldieso:
  combat: {profil: cra_feu, element: feu, pa_min: 6, poids: {pm: 4, portee: 3, sagesse: 0.02}}
Walitcoin:
  combat: {profil: enutrof_terre_feu, poids: {prospection: 0.05}}
profils:
  cra_feu: {priorites: [pa, pm, dommages, intelligence]}   # ordre de départage quand les scores sont proches
```

- **Score de base automatique** : dégâts par tour calculés comme au 4 (sorts du personnage, PA du stuff entier).
- **Poids à toi** pour le reste, en « dégâts par tour équivalents » : 1 PM = ?, 10 vitalité = ?, 10 sagesse = ?
  (XP), 10 prospection = ? (butin, Cupidité de l'Enutrof), 1 portée = ? (Crâ).
- **Contraintes dures possibles** : PA minimum, PM minimum, « garder l'Agride » (sagesse), élément imposé, objets
  interdits (en vente, réservés à un autre).
- **Priorités** : départagent deux stuffs à moins de 3 % l'un de l'autre (« PA > PM > dommages > Intelligence »).

### 5.3 Contraintes

- Niveau du personnage ≥ niveau de l'objet ; conditions `c` évaluées sur les caractéristiques **totales du stuff
  proposé** (une condition Force > 59 peut être remplie grâce aux autres objets : l'ordre d'équipement en découle).
- Un objet par emplacement : 2 anneaux, 6 Dofus, arme + bouclier seulement si l'arme n'est pas à deux mains (`tw`).
- Un exemplaire physique ne va qu'à un personnage (uid) ; deux anneaux identiques interdits tant que ce n'est pas
  vérifié en jeu.
- Conditions inconnues de l'outil (quête, alignement, nom, métier non lu) : objet écarté, signalé.
- Objets liés au compte (983) : seulement s'ils sont déjà dans le sac de ce personnage.

### 5.4 Calcul

1. Pour chaque objet possible : ses caractéristiques (jet réel si possédé, moyenne du jet sinon).
2. **Recherche du stuff entier** : 8 emplacements utiles + Dofus. Une recherche gloutonne puis améliorée (on remplace
   un objet à la fois tant que le score monte) suffit pour quelques dizaines de candidats ; les panoplies se traitent
   comme des « paquets » essayés en entier et à 2, 3, 4... objets (le bonus dépend du nombre porté).
3. **Score** = dégâts par tour (avec le total PA du stuff, arme comprise : ses lignes de dégâts et son coût en PA
   entrent dans la suite de sorts) + Σ poids × caractéristiques non offensives ; contraintes dures respectées.
4. Sortie : stuff conseillé, gain par rapport au stuff actuel, objets manquants (à acheter, au coffre, chez un
   autre personnage), ordre d'équipement qui remplit les conditions.

### 5.5 Profil « pods » du principal (mode banque)

Objectif : le plus de pods possible, hors combat, le temps de recevoir les ressources et de les déposer.

- Score = pods des objets + 5 × Force des objets (vérifié ensuite par `Ow`, qui donne le vrai maximum).
- Garder la vitalité positive (ne pas mourir d'un agresseur sur le trajet : le passage en pods se fait **à la
  banque**, pas avant le trajet).
- Pour Fourboun (niv. 43, Force 0), objets ordinaires de la langue (`--pods 43`, jets moyens) :

| Emplacement | Objet (niveau) | Pods moyens | Remarque |
|---|---|---|---|
| Ceinture | Ceinture de Poche (20) | ≈ 350 (201-300 pods + Force 16-25) | ou Ceinture du Boufcoul (37) ≈ 300, Ceinture du Bouftou (20) 1 à 500 |
| Cape (sac) | Sac de l'Aventurier (40) | ≈ 275 | Sac du Récolteur et suivants : métier de récolte 40, 60, 90 |
| Bottes | Bottes Volatiles (41) | ≈ 180 (Force 31-40) | |
| Coiffe | Dora (36) | ≈ 180 (Force 11-60) | |
| Anneau | Force de Crocoburio (40) | ≈ 115 | condition Force > 59 : à mettre après les autres objets à Force |
| Amulette | Amulette du Pirate (41) | ≈ 100 | |

  Total ≈ **+1 200 pods** (1800 → ≈ 3000, + 65 %), soit environ 40 % de lots d'échange en moins.
- Les suiveurs aussi peuvent porter plus avant d'aller à la banque (mêmes objets) ; c'est un second temps.

### 5.6 Changer de stuff sans risque

- **Jamais en combat** (comme pour l'outil de récolte) : seulement quand le serveur n'annonce pas de combat, et
  après la fin de combat.
- **Ensembles du client** (recommandé) : tu enregistres une fois, à la main, « Combat » et « Pods » pour chaque
  personnage ; le bot ne fait que « Tout équiper » sur l'ensemble voulu et vérifie la réponse du serveur (`OM` par
  objet) et le nouveau maximum de pods (`Ow`). Pas de glisser-déposer objet par objet.
- **Sans ensemble** : double-clic validé (`bot/brain/equipment.py`, filtre par type), un objet après l'autre, dans
  l'ordre qui remplit les conditions ; l'état d'avant est noté (uid par place) pour pouvoir revenir.
- **Ordre en banque** : stuff pods → recevoir les lots → tout déposer → **puis** stuff de combat (en le retirant
  avant le dépôt, le principal serait surchargé et bloqué).
- Avant de repartir : stuff de combat vérifié (place 1 = arme de combat, PA/PM attendus d'après `As`) ; sinon arrêt
  avec message.

### 5.7 Présentation à toi

- Un rapport par personnage, sans rien équiper : stuff actuel, stuff conseillé, gain (dégâts par tour, PA, PM, pods),
  raisons (« Masque Kanniboule : +5 dommages = +9 dégâts/tour »), objets manquants et où ils sont.
- Puis, si tu le veux, la liste des changements à faire (personnage, objet, de quelle place, ordre) : tu dis oui, le
  bot les fait hors combat et enregistre le résultat comme ensemble.

## 6. Exemples (catalogue de la langue, jets moyens, conditions évaluées sur les caractéristiques actuelles)

Ce sont des pistes, pas des achats : on ne sait pas encore ce que contiennent les sacs et coffres.

| Personnage | Ce que l'essai met en tête | Panoplies les mieux notées |
|---|---|---|
| Fourboun (Sadida fourbe 43) | Amulette du Kam Assutra ou La Broche Céleste (+1 PA), Ceinture Fulgurante (+1 PA, +1 à 3 dommages), Anobra (+4 à 5 Do), La Mokette, Masque Kanniboule (+3 à 7 Do) | Prespic (dommages +8 à 4/4), Bouftou (6 hors marteau ; 7/7 avec le Marteau = +1 PA) |
| Waldarch (Crâ 49) | L'Amulette du Puzzle de la Griffe (Intelligence, Do), Cape Huccino, Shako, Masque Kanniboule, Ceinture Fulgurante | Kwak de Flammes (6/8 : +1 PM ; 7/8 : +1 PA), Prespic, Scarafeuille Rouge |
| Enu-six (Enutrof 41) | Broche Céleste et Ceinture Fulgurante (+1 PA chacune : 11 dégâts/tour), Masque Kanniboule, Shako, Anobra | Prespic, Bouftou, Fourbasse (7/7 : +1 PA, +1 PM) |
| Walitcoin (Enutrof 28) | Amulette, Ceinture du Bandit (+Do), Cape du Tofu, Coiffe du Bouftou | Tofu, Bandit, Bouftou (7/7 : +1 PA au niv. 20) |
| Eldieso (Crâ 36) | Ceinture Fulgurante, Shako, Dora, Fourbacapa, Ceinture et Amulette du Bandit | Fourbasse, Tofu, Bouftou |

Sources de PA/PM jusqu'au niveau 50 (langue) : **PA** Amulette du Kam Assutra (43), La Broche Céleste (40),
Dragolyre (32) et Dragolyre Remaniée (42), Ceinture Fulgurante (30), Abracaska (45), L'Abramu (46), Casque du Chafer
(50) ; panoplies Bouftou 7/7 (20), Champêtre 7/7 (10), Homme Ours 7/7 (20), Corbac 3/5 (45), Kwak 7/8 (42),
Fourbasse 7/7 (34). **PM** Bottes de Poursuite (31 à 39), Cape du Tofu Fou (36), Cape du Coq Hû (44), Capodindo et
Bottes de Bowisse (50) ; panoplies Kwak 6/8, Fourbasse 7/7, Corbac 4/5. Gelano (PA) et Gelobottes (PM) sont niveau 60.

## 7. Ce qu'il me faut de toi

1. **Rôle et élément de chaque personnage** : Fourboun fourbe (dommages fixes) ? Crâ feu pour les trois (le guide le
   conseille, et leurs meilleurs sorts sont feu) ? Enutrof terre + feu (marteau) ou eau (Lancer de Pièces) ?
2. **Ordre des priorités** (ex. PA > PM > dommages > élément) et ce que valent pour toi 1 PM, la vitalité, la
   sagesse (XP, Agride), la prospection.
3. **Mode pods** : seulement le principal, ou aussi les suiveurs ? Accepter de perdre de la vitalité en mode pods ?
4. **Ensembles du client** : d'accord pour que tu enregistres « Combat » et « Pods » à la main (une fois par
   personnage), le bot se contentant de les charger ?
5. **Achats** : seulement réorganiser ce qu'on a (sacs, coffre, échanges entre nos personnages), ou aussi une liste
   de courses ?

## 8. Incertain, à vérifier en jeu (essais que tu lances)

- Où est l'« Équipement rapide » dans l'interface, et si « Tout équiper » se fait d'un seul message serveur.
- Deux anneaux identiques : refusés (« Déjà équipé ! ») ou non.
- Objet dont la condition n'est plus remplie après qu'on retire un autre objet : reste-t-il porté ?
- Formule des pods : un second cas exact (lire le jet de pods de la Ceinture du Bouftou de Walitcoin dans son `ASK`).
- Coffre : probablement partagé par compte (nos 8 personnages sont sur plusieurs comptes) ; si oui, ce qui est dans
  un coffre ne sert qu'aux personnages de ce compte.
- Bonus de classe aux armes (pelle 100 %, marteau 95 %... d'après les guides) : pas dans la langue, à confirmer
  avant de mettre l'arme dans le calcul.

## 9. Essai hors jeu

```
.venv/Scripts/python -m tools.equipement_essai --valeurs              # tableau du 4
.venv/Scripts/python -m tools.equipement_essai --objets Fourboun      # 3 meilleurs objets par emplacement
.venv/Scripts/python -m tools.equipement_essai --panoplies Fourboun   # panoplies accessibles, bonus compris
.venv/Scripts/python -m tools.equipement_essai --pods 43              # objets de pods jusqu'au niveau 43
```

Lecture seule (langue du client, `data/personnages`, `config/sorts/solomonk.json`) ; réutilise `mean_damage` et
`CharacterStats` du bot. Jets moyens, conditions sur les caractéristiques actuelles (équipement actuel compris) :
c'est une estimation, pas encore l'optimiseur du 5.4.

## Sources

- Gamosaurus, stuff Crâ Rétro : https://www.gamosaurus.com/jeux/dofus-retro/guide-du-stuff-cra-sur-dofus-retro
- Gamosaurus, stuff Enutrof Rétro : https://www.gamosaurus.com/jeux/dofus-retro/guide-du-stuff-enutrof-sur-dofus-retro
- Gamosaurus, stuff Sadida Rétro : https://www.gamosaurus.com/jeux/dofus-retro/guide-du-stuff-sadida-sur-dofus-retro
- Gamosaurus, parchemins de Force Rétro (5 pods par Force) :
  https://www.gamosaurus.com/jeux/dofus-retro/toutes-les-obtentions-des-parchemins-de-force-sur-dofus-retro
- Gamosaurus, mise à jour Rétro 1.39 (ensembles d'équipement) :
  https://www.gamosaurus.com/jeux/dofus-retro/dofus-retro-resume-du-nouveau-contenu-de-la-mise-a-jour-1-39
- Écarté (Dofus 2) : JOL « Débuter sur Dofus », https://dofus.jeuxonline.info/article/2187/debuter-dofus
- Local : langue du client Rétro 1.49 (`I`, `ISTA`, `IS`, `E`, `ANKLANGSO`), serveur (`Ow`, `ASK`, métiers dans
  `docs/rapports/metiers.md`), `config/sorts/solomonk.json`.
