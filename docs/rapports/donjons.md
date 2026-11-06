# Farm de donjon : clés, entrée, salles, banque (12/10)

Recherche et conception. Rien n'est branché dans le bot, rien n'a touché au jeu.

- **Vérifié** : ce qui vient des fichiers de langue du client Retro 1.49.6 copiés dans `data/client_retro/lang` (données
  officielles), lus par le nouvel outil `tools/langue_client.py` (tests : `tests/test_langue_client.py`).
- **À vérifier en jeu** : ce qui vient d'internet (Retro / 1.29 seulement, sources en 3.4) et mes déductions.
- Données structurées : `config/donjons.yaml` (chaque valeur avec sa source et « à vérifier en jeu : oui / non »).

Commandes utiles (lecture seule) :

```
.venv/Scripts/python -m tools.langue_client --donjons            # les 54 donjons du client : niveau, entrée, salles
.venv/Scripts/python -m tools.langue_client --donjon 2           # salles du Donjon des Champs
.venv/Scripts/python -m tools.langue_client --cles               # les 47 objets « Clefs » : recette, monstres
.venv/Scripts/python -m tools.langue_client --objet 8680         # Ecaille poisseuse : qui la laisse tomber
.venv/Scripts/python -m tools.langue_client --dialogues "clef et rentrer"
```

## 0. Réponses de l'utilisateur (12/10)

| Sujet | Réponse | Conséquence |
|---|---|---|
| Niveaux | Gela-[A13] 113, Brawli-[Gal] 103, Jesuisleking 14, Imthebest 6 ; Imthebest pourrait être remplacé | Imthebest (niveau 6) n'a **pas accès au coffre** (niveau > 10 exigé) et risque la mort dans les salles 4 à 7 des Champs ; Jesuisleking (14) y a accès |
| Comptes | Quatre comptes différents : **un coffre par personnage** | Chacun dépose et reprend ses propres clés ; Imthebest passe par des échanges |
| Points de sauvegarde | À régler au zaap d'Astrub, 4,-19 | Un personnage mort (ou une défaite) réapparaît à 3 cartes de la banque (4,-16) |
| Inventaire | Touche « i » | Lecture des clés possible (5.1, état DÉPART) |
| Exigence du mode « farm de donjon » | Au démarrage, le bot **annonce le nombre de clés de chaque personnage**, puis **fait les échanges pour les répartir** ; s'il n'y en a pas assez pour tout le monde, **il le dit** | États DÉPART et RÉPARTITION (5.1), cas de test DJ0 à DJ0c (5.6) |
| Sessions en jeu | Plus tard | — |

## 1. Sources

| Source | Ce qu'elle donne | Ce qu'elle ne donne pas |
|---|---|---|
| Langue du client (tables I objets, CR recettes, M monstres, DU donjons, HI indices de la carte du monde, MA cartes, N PNJ, D dialogues, SK métiers ; ANKLANGSO textes d'interface) | Donjons, cartes de leurs salles, carte d'entrée, clés, recettes, monstres qui laissent tomber chaque objet (sans le taux), niveaux et vie des monstres, textes des dialogues, textes de l'interface du coffre | Quel PNJ dit quel dialogue (envoyé par le serveur), monstres de chaque salle, passage d'une salle à l'autre, taux de butin, cases (cartes chiffrées) |
| `config/monde/monde_des_douze.json`, liste « lieux » (xixou.io) | 33 donjons : nom, boss, niveau, carte | Concorde avec le client pour les entrées (7,-25 ; 2,-34 ; 13,-28), pas pour les niveaux (xixou : 10 ; client : 20 et 30) |
| Internet (agent, section 3) | Dialogues, déroulé des salles, monstres, sortie | Tout est à vérifier en jeu |
| Barbok et xixou (section 3.5) | Noms affichés, images des clés, recettes, entrées | Barbok n'est pas explicitement Retro (recettes souvent différentes) ; xixou concorde avec le client |
| Installation du client (section 2.5, lecture seule) | Dessins des objets (`clips/items/<type>/<gfx>.swf`) | Vectoriels : à dessiner, pas à extraire |

## 2. Ce que disent les fichiers du client (vérifié)

### 2.1 Donjons proches d'Astrub

| Donjon (id DU) | Niveau (DU) | Entrée (indice « Donjons » de la carte du monde) | Salles | Clé | Boss (niveau / vie selon le grade) |
|---|---|---|---|---|---|
| Donjon des Champs (2) | 20 | 7,-25, carte 7453, Champs d'Astrub | 8 cartes : 9768 à 9774 (« Salle 1 » à « Salle 7 ») et 9786 (nommée aussi « Salle 7 ») | Clef des Champs (8143) | Tournesol Affamé : 40 à 48, 500 à 620 PV, 6 PA, 2 PM |
| Donjon des Bouftous (1) | 30 | 2,-34, carte 1856, Le berceau | 11 cartes : 2073 à 2084 sans 2078 (« Salle 1 » à « Salle 11 ») | Clef du Donjon des Bouftous (1568) | Bouftou Royal : 35 à 43, 500 à 700 PV, 7 PA, 5 PM |
| Donjon Ensablé (9) | 20 | 13,-28, carte 10155, Calanques d'Astrub | 10 cartes : 10156 à 10165, sans nom | Clef du Donjon Ensablé (8437) | Mob l'Eponge : 30 à 34, 240 à 320 PV, 6 PA, 3 PM |
| Donjon d'Incarnam (41) | 10 | Incarnam 6,5, carte 10359 | 7 cartes | Clef du Donjon d'Incarnam (8545), sans recette | Milimilou : 18 à 26 |

Les autres (Squelettes 10,15 ; Tofus 5,6 ; Scarafeuilles 1,26 ; Bworks -5,10...) sont plus loin et plus durs :
`--donjons` les liste tous.

Faits importants pour le bot :

1. **Les salles de ces trois donjons portent les coordonnées de l'entrée.** Les 11 salles des Bouftous sont toutes en 2,-34, les 8 des
   Champs en 7,-25, les 10 de l'Ensablé en 13,-28 (vrai pour 40 donjons sur 49 ; les Larves, les Wabbits, le Corbac
   ont des salles à d'autres coordonnées). Seule la sous-zone change : le nom en haut à gauche devrait passer de
   « (Champs d'Astrub) » à « (Donjon des Champs) » (à vérifier sur capture). Les coordonnées ne disent donc ni qu'on est
   entré, ni dans quelle salle on est (voir 5.3).
2. **Plan des salles** (table DU, Champs et Bouftous seulement) : position de chaque salle sur le plan du donjon et une
   icône sur certaines.
   - Bouftous : (0,0) → (4,0), puis (4,1), (4,2), puis (3,2) → (0,2) : une chaîne de 11 salles en U (déduction :
     on passe d'une salle à sa voisine sur le plan). Icônes : 900 en salle 1, 905 en salle 10, 901 en salle 11.
   - Champs : (0,0) → (30,0) → (30,30) en L pour les salles 1 à 7, et la carte 9786 à part en (0,30).
   - Gladiatrool (autre donjon) : 900 en salle 1, 905 en salle 10, 906 sur la salle « Sortie ». Hypothèse : 900 =
     entrée, 905 = boss, 906 et 901 = sortie ou récompense. À confirmer (section 3).
3. **Ensablé** : 10 cartes sans nom ni plan : ordre inconnu.
4. En 7,-25 existe une 2e carte « Champs d'Astrub » intérieure (7613) : peut-être l'atelier (« grange ») de la
   gardienne, d'après la recherche internet (à vérifier).
5. **Cases de placement par salle** (p1, p2 de chaque carte) : aux Champs, la carte 9770 (salle 3) n'a que **3 cases
   p1** et la 9772 (salle 5) **4** ; les autres en ont 8 à 10. Toutes les salles des Bouftous en ont 8, celles de
   l'Ensablé 8 à 14. Si p1 est bien le côté des joueurs (déduction), **la salle 3 des Champs n'accepte que 3
   combattants** : le 4e personnage ne peut pas rejoindre ce combat. La recherche internet le dit aussi (une salle à 3,
   une à 4). Les deux cartes « Salle 7 » des Champs (9774 et 9786) ont les mêmes nombres de cases (9 et 25) : 9786
   ressemble à une variante de la salle du boss plutôt qu'à une salle de plus.

### 2.2 Les clés

Type d'objet 84 « Clefs » ; fabriquées par le **Bricoleur** (compétence « Confectionner une clef », 34 clés).

| Clé (id) | Niv. | Poids | Recette | Monstres qui la laissent tomber (taux non fourni) | « Détruite après utilisation » écrit |
|---|---|---|---|---|---|
| Clef des Champs (8143) | 10 | 1 | 3 Pétale de Tournesol Sauvage + 3 Fleur de Pissenlit Diabolique | 46 monstres des champs : Tournesol Sauvage, Pissenlit Diabolique, Rose Démoniaque, Champ Champ, larves, Moskito, Champa Vert, Bleu, Rouge, Marron, Rose Obscure... | non (description sans mention) |
| Clef du Donjon des Bouftous (1568) | 10 | 2 | 10 Laine de Bouftou + 1 Oeil de Bouftou + 1 Sabot de Bouftou | Bouftou, Boufton Blanc et Noir, Chef de Guerre Bouftou, Ballotin le Bouftou... | **oui** |
| Clef du Donjon Ensablé (8437) | 10 | 1 | 5 Goujon (poisson : aucun monstre, pêche) + 3 Ecaille poisseuse | Pichons Bleu, Vert, Blanc, Orange, Kloune, Etoile de la Mer d'Asse, Raul Mops | non (description sans mention) |
| Clef du Donjon d'Incarnam (8545) | 1 | 1 | aucune | 17 monstres d'Incarnam | non |

Ingrédients : Pétale ← Tournesol Sauvage ; Fleur de Pissenlit ← Pissenlit Diabolique ; Laine, Oeil, Sabot ← Bouftou ;
Ecaille poisseuse ← Pichons des Calanques et Mob l'Eponge.

- **Les clés sont rangées avec les ressources** : le type 84 a le super-type 9, celui des ressources (laine, fleurs...).
  Elles partent donc avec « Transférer tous les objets » de l'onglet ressources : d'où la mécanique demandée « tout
  déposer, puis reprendre les clés ».
- **Trousseau de clefs** (10207) : objet de quête, poids 0. D'après un dialogue (q7613) : accès gratuit à tous les
  donjons une fois par semaine, obtenu contre un doplon (dopeul du temple de classe). La réponse « Utiliser le trousseau
  de clefs » existe à côté de « Donner la clef et rentrer » : **le bot doit choisir sa réponse par le texte**, sinon il
  dépense la charge hebdomadaire du trousseau.

### 2.3 PNJ et dialogues

Le lien PNJ → dialogue est envoyé par le serveur : la langue a les textes, pas qui les dit.

| Donjon | Gardien (PNJ du client) | Textes trouvés | Commentaire |
|---|---|---|---|
| Champs | **Mawy Ingalsse** (PNJ 780) | q3178 : elle se présente, propose de « visiter ma grange », mais il faut une clef, perdue dans les champs (« une vilaine créature des champs » l'a peut-être prise) | Nom donné aussi par internet (wiki de 2012) ; la réponse elle-même n'est pas identifiée : sans doute « Donner la clef et rentrer » |
| Bouftous | Inconnu ; « Rotabla le berger triste » (PNJ 173) n'est nommé gardien que par une source Dofus 2 ; « Altaïr » (PNJ 174 et 177) existe | q3353 « Toi, tu veux aller tondre la laine du roi des Bouftous, pas vrai ? » ; réponse a564 « Donner la clé des bouftous. » ; q3355 « Hey, hey! Tu n'as pas de clé! » ; q3358 « Et voilà, bonne tonte ! ». Fin : q677 « Félicitations, vous avez réussi avec brio l'épreuve de ce donjon » + un animal de compagnie, réponse a2578 « Prendre le familier avec soi et sortir du donjon. » | La récompense « Chacha » est confirmée par internet ; le lien q677 / a2578 avec ce donjon est une déduction |
| Ensablé | **Capitaine Igloute** (PNJ 798) | Récit en 9 textes, de q3281 « Bonjour, je suis Igloute, le célèbre capitaine ! » à q3289 « Ah tu veux rentrer dans le donjon ensablé ? » ; 8 réponses « Continuer la discussion » (a2936 à a2943), puis a2944 « Donner la clef et rentrer ». Après le boss : q3290 « Toi aussi tu viens de vaincre, le terrible Mob l'Eponge ! », q3291 (il apprend à « s'allonger »), réponses a2945 « Se laisser faire » et a2946 « Se sauver en courant » | Enchaînement par identifiants voisins : déduction, mais 9 textes pour 8 « Continuer » + 1 « Donner » concordent. Internet : « longue discussion avec le capitaine », émote « s'allonger » à la fin |
| Tous | — | « Sortir du donjon » (a2929, a2932, a4540...), « Utiliser le trousseau de clefs. » | |

Inconnu : q2941 « Seul un groupe d'au moins trois aventuriers peut espérer en sortir vivant » (quel donjon ?).

### 2.4 Banque, coffre, mort (textes du client)

- **Niveau** : « L'accès aux coffres de banque est réservé aux joueurs dont le niveau est supérieur à 10 » (q6205). Un
  personnage de niveau 10 ou moins ne peut ni déposer ni reprendre de clés : il passe par un échange avec le principal.
- **Coût** : chaque consultation est payante, le prix dépend du contenu du coffre (q2875 ; 76 kamas mesurés le 11/10).
- **Fenêtre du coffre** (ANKLANGSO) : « Trier et transférer », « Tri et recherche », « **Recherche d'un objet** »,
  « **Nom de l'objet (%1 caractères minimum) :** », « Réinitialiser la recherche », « Aucun objet correspondant à la
  recherche », « Transférer tous les objets », « Transférer les objets déjà présents », tris (nom, type, niveau, poids,
  quantité, prix, date), « Quantité », « Lot (quantité) ». Le **filtre par nom existe donc** ; où il se trouve
  (menu du bouton aux flèches ?) reste à voir sur capture.
- « Vous avez trop d'objets dans votre banque, retirez-en quelque-uns pour tous les voir. » (ERROR_179) : dans un
  coffre très rempli, seule la recherche garantit de retrouver les clés.
- **Mort** : « Les tombes sont interdites en donjon, vous avez été ramené à votre dernier point de sauvegarde. »
  (ERROR_142) ; l'énergie se perd lors des défaites (HELP_ENERGY) ; « %1 est mort ! » (DIE).

### 2.5 Dessins des clés dans l'installation (source locale des icônes)

- Chaque objet a un identifiant graphique (champ « g » de la langue). Son dessin est dans l'installation, en lecture
  seule : `%LOCALAPPDATA%\Ankama\Retro\resources\app\retroclient\clips\items\<type>\<gfx>.swf`.
  `tools/langue_client.py --objet <id>` affiche ce chemin et la taille du fichier.
- Les 47 objets « Clefs » ont leur dessin (dossier `items\84`, 53 fichiers) : Clef des Champs `84\13.swf` (2 036
  octets), Clef du Donjon des Bouftous `84\226.swf` (1 014), Clef du Donjon Ensablé `84\237.swf` (2 889).
- **Ce ne sont pas des images intégrées** : les SWF des clés ne contiennent que des formes vectorielles (DefineShape,
  DefineShape3) placées par PlaceObject2. Sur les 6 888 dessins d'objets, 542 seulement contiennent des bitmaps, et
  aucune des clés examinées. Une extraction « zlib + struct » ne suffit donc pas : il faut **dessiner** les formes.
- **Essai réussi** (script de travail, hors du projet ; lecture seule de l'installation) : lecture des formes
  (bords droits et courbes, remplissages unis et dégradés, contours, matrice et couleur de PlaceObject2) avec
  `struct`/`zlib`, tracé avec numpy et OpenCV (déjà installés), sur-échantillonnage x4. Résultat dans
  `data/donjons_essai/` : `cle_8143_clef_des_champs.png` (clé-tournesol, 440 x 731 à l'échelle 2) et
  `cle_1568_clef_du_donjon_des_bouftous.png` (clé en laine, 175 x 271). Les deux dessins sont nets et reconnaissables.
- Limites : rendu proche mais pas identique à celui de Flash (lissage, dégradés) ; la taille à l'écran dépend de la
  case de l'inventaire. Pour un modèle de détection, il faudra mettre le dessin à l'échelle de la case et le comparer
  à une capture réelle de l'inventaire avant de s'y fier. Les sprites imbriqués et les remplissages bitmap ne sont
  pas gérés (inutiles pour les clés).
- Si l'utilisateur le souhaite, ce rendu peut devenir un outil du projet (nouveau fichier, à valider) : il évite de
  télécharger les images d'internet.

## 3. Internet (Retro / 1.29 seulement, à vérifier en jeu)

Recherche faite par un agent (pages lues, rien téléchargé). J'ai relu moi-même deux pages (solomonk Champs, Millenium
1.31) : conformes. Écartés car Dofus 2, Touch ou Unity : papycha.fr (Touch), lincroyable59.wixsite.com, gambin.co,
dofuspourlesnoobs.com, articles dofus.jeuxonline.info (Dofus 2). Inaccessibles : forums JOL et dofus.com, dofuswiki.

### 3.1 Le plus utile : une carte de plus que de salles de combat

| Donjon | Cartes (client) | Salles de combat (web) | La carte en plus |
|---|---|---|---|
| Champs | 8 | 7 | Deux cartes « Salle 7 » aux mêmes cases de placement (2.1) : variante de la salle du boss ? |
| Bouftous | 11 | 10 | Salle 11 (icône 901) : salle de fin, le familier offert (2.3) ? |
| Ensablé | 10 | 9 | Salle de fin avec le capitaine (émote « s'allonger ») ? |

### 3.2 Salles et monstres (solomonk, xixou, concordants ; confiance haute)

- **Champs** : salle 1 : 3 monstres tirés parmi Champ Champ, Moskito, Pissenlit Diabolique ; 2 : 4 (+ Larve Bleue) ;
  3 : 5 (+ Larve Verte) ; 4 : 6 parmi les trois Larves et Tournesol Sauvage ; 5 : 7 parmi Tournesol Sauvage, Champ
  Champ, Rose Démoniaque ; 6 : 8 parmi Tournesol Sauvage, Moskito, Rose, Pissenlit ; 7 (boss) : groupe fixe de 8,
  Tournesol Affamé et un de chaque autre monstre. Niveaux 1 à 15, boss 40 à 48.
  - Salles limitées : une à 3 personnages, une à 4 (guidedofus) : concorde avec les cases du client (salles 3 et 5).
    D'après le même guide, le reste du groupe combat un groupe réapparu (à vérifier).
- **Bouftous** : 1 : 2 Boufton Blanc + 2 Boufton Noir ; 2 : 4 + 4 ; 3 : 2 Bouftou + 2 + 2 ; 4 : 3 Bouftou + 3 + 2 ;
  5 : 4 Bouftou + 2 + 2 ; 6 : 6 Bouftou + 1 + 1 ; 7 : 8 Bouftou ; 8 : 6 Bouftou + 2 Chef de Guerre ; 9 : 4 Bouftou +
  4 Chef de Guerre ; 10 (boss) : Bouftou Royal + 3 Chef de Guerre + 4 Bouftou. Niveaux : Bouftons 1 à 3, Bouftou 3 à
  15, Chef de Guerre 15 à 23, boss 35 à 43.
- **Ensablé** : 1 : 8 Crabes ; 2 : 6 Crabes + 2 Étoiles ; 3 : 4 Crabes, Pichon Bleu, Pichon Blanc, 2 Étoiles ;
  4 : 2 Crabes, 2 Bleus, 2 Blancs ; 5 : 2 Crabes, 2 Oranges, 2 Verts ; 6 : 1 Pichon de chaque couleur + 4 Étoiles ;
  7 : 2 Pichons de chaque couleur ; 8 : 3 Crabes, 1 de chaque couleur, Pichon Kloune ; 9 (boss) : 1 de chaque
  couleur, Pichon Kloune, **Raul Mops (40 à 80)**, Étoile, Mob l'Eponge. Raul Mops est la vraie difficulté.

### 3.3 Clés, entrée, sortie, règles

| Sujet | Ce que dit internet | Source | Confiance |
|---|---|---|---|
| Recettes | Identiques au client (3+3 ; 10+1+1 ; 5+3). Gamosaurus donne 10+10 et 5+10 : contredit par le client | xixou, wgpanel ; gamosaurus | Haute (client) |
| Butin des clés | Clef des Champs ~0,5 % sur larves, Tournesol Sauvage, Champ Champ ; Clef des Bouftous ~0,5 % sur Bouftou, Bouftons, Chef de Guerre ; Clef Ensablé ~1 % sur Pichons et Étoile | xixou | Moyenne |
| Ingrédients | Laine de Bouftou ~80 %, Oeil et Sabot ~1 % ; Écaille poisseuse ~5 % sur Pichons et sur le boss ; Goujon : pêche, Pêcheur niveau 1 | xixou, gamosaurus | Moyenne |
| Bricoleur | Clés des Champs et des Bouftous parmi les recettes des premiers niveaux (1 à 20) ; niveau exact non trouvé | gamosaurus | Moyenne |
| Prix à l'hôtel de vente | Non trouvé | — | — |
| Entrée Champs | 7,-25, « devant l'atelier des paysans d'Astrub » ; gardienne Mawy Ingalsse, dans l'atelier | JOL Retro 14804 ; wiki-dofus.eu (2012) | Haute (lieu), moyenne (PNJ, confirmé par le client) |
| Entrée Bouftous | 2,-34 (Tainéla, le Berceau), zaap du Berceau 1,-32 ; nom du gardien non trouvé en Retro | JOL Retro 14805, xixou | Haute (lieu) |
| Entrée Ensablé | 13,-28 ; Capitaine Igloute, « longue discussion » avant d'entrer | JOL Retro 14804, guidedofus, wiki-dofus.eu | Moyenne (confirmé par le client) |
| Accès | Parler au PNJ « à condition d'avoir la Clef ou le trousseau de Clefs » ; une clé par personnage (xixou, peut-être un gabarit du site) | Millenium (Squelettes, Blops, Bulbes), xixou | Haute / moyenne |
| Clé consommée | Bouftous : oui (aussi dans le client). Champs, Ensablé : non dit | xixou | Haute (Bouftous) |
| Passage de salle | Non trouvé pour ces trois donjons ; aux Bulbes, on marche dans un soleil en haut de la carte ; il faut battre le groupe de la salle pour passer | Millenium 345297, JOL Retro 14805 | Faible (à observer) |
| Sortie | Bouftous : « Bravo ! » et un Chacha offert ; Ensablé : émote « s'allonger » ; lieu d'arrivée non trouvé | guidedofus, JOL Retro | Moyenne |
| Quitter en cours | Impossible avec une potion : mourir ou battre le boss | gamosaurus 54067 | Moyenne |
| Défaite | Recommencer du début, énergie et clé perdues (comportement d'avant la sauvegarde de progression de Dofus 2, que Retro n'a pas) | Devblog Dofus 2 de 2011 : inférence | Moyenne |
| Mort, équipe gagnante | Revient à 1 PV en fin de combat | wiki d'époque 1.x | Faible |
| Niveaux conseillés | Champs ~15 (groupe de 3 à 8) ; Bouftous à partir de 20, groupe de 4 à 8 vers 30 ; Ensablé 20 à 30, groupe de 4 à 8 ; aucun minimum imposé trouvé | guidedofus, JOL Retro | Moyenne |
| Trousseau | 1 doplon au PNJ du temple ; une entrée par donjon et par semaine, recharge une semaine après l'usage | Millenium 344664, Breakflip 106405 | Haute |
| Incarnam | Retour sur l'île jusqu'au niveau 15 seulement : **exclu pour Gela** | Breakflip 106212 | Moyenne-haute |
| Coffre | Recherche d'objets en banque dès 2 caractères (1.31) ; transfert : le maximum possible selon les pods (1.31) ; sélection multiple avec Ctrl (1.33, coffre non cité) | Millenium 357321 (relu), Gamosaurus 1.33 | Haute / moyenne |

### 3.4 Pages

- xixou (Retro 1.49) : https://xixou.io/guides/donjons/donjon-des-champs/ ,
  https://xixou.io/guides/donjons/donjon-des-bouftous/ , https://xixou.io/guides/donjons/donjon-ensable/ ,
  https://xixou.io/encyclopedie/ressources/ecaille-poisseuse/
- solomonk (fansite Retro) : https://solomonk.fr/fr/donjon/champs , https://solomonk.fr/fr/donjon/bouftous ,
  https://solomonk.fr/fr/donjon/ensable
- guidedofus (« Guide Dofus Rétro (1.29) ») : https://guidedofus.com/donjon-des-champs/ ,
  https://guidedofus.com/donjon-bouftou/ , https://guidedofus.com/donjon-ensable/ ,
  https://guidedofus.com/donjon-dincarnam/
- JOL Retro : https://dofusretro.jeuxonline.info/article/14804/donjons-astrub ,
  https://dofusretro.jeuxonline.info/article/14805/donjon-tainela ,
  https://dofusretro.jeuxonline.info/article/14800/dofus-retro-donjons ,
  https://dofusretro.jeuxonline.info/article/14803/quetes-incarnam
- wgpanel (données 1.29/1.48) : https://wgpanel.net/encyclopedie/craft/clef-des-champs ,
  https://wgpanel.net/encyclopedie/craft/clef-du-donjon-des-bouftous ,
  https://wgpanel.net/encyclopedie/craft/clef-du-donjon-ensable
- wiki-dofus.eu (2012, version incertaine) : https://wiki-dofus.eu/w/Donjon_des_Champs ,
  https://wiki-dofus.eu/w/Donjon_des_Bouftous , https://wiki-dofus.eu/w/Donjon_Ensabl%C3%A9
- Millenium (Retro) : https://www.millenium.org/news/357321.html , https://www.millenium.org/guide/344664.html ,
  https://www.millenium.org/guide/345090.html , https://www.millenium.org/guide/345147.html ,
  https://www.millenium.org/guide/345297.html
- Gamosaurus (Retro) : https://www.gamosaurus.com/?p=45090 , https://www.gamosaurus.com/?p=44309 ,
  https://www.gamosaurus.com/?p=54067 ,
  https://www.gamosaurus.com/jeux/dofus-retro/dofus-retro-mise-a-jour-1-33-tutoriel-arakne-corrections-et-ameliorations
- Breakflip (Retro) : https://www.breakflip.com/guides/106212.html , https://www.breakflip.com/guides/106405.html
- Inférences seulement : https://dofus.jeuxonline.info/actualite/30247/devblog-sauvegarde-progression-donjon (Dofus 2),
  https://wow.allakhazam.com/wiki/Life_%28DoFus%29 (2008)

### 3.5 Les clés sur Barbok et xixou, comparées à la langue du client (12/10)

Recherche par un agent (pages lues, aucune image téléchargée).

- **Barbok** : https://barbok.fr, page des clés https://barbok.fr/index.php?type=clefs, fiches de donjons
  https://barbok.fr/index.php?type=accdonjon. **Pas explicitement Retro** : ancien site de fans de l'époque 1.x,
  mélangé (il liste aussi des donjons de Frigost, absents de Retro). 31 clés sur 40 ; ni coordonnées ni id.
  Images `https://barbok.fr/images/Clefs/<nom>.jpg` (JPG ; nom en mots, pas un gfx). Confiance moyenne.
- **xixou.io** (« Fan-site Dofus Rétro 1.49 ») : les 40 clés, par une API JSON
  (`https://xixou.io/wp-json/xixou/v1/resource/<slug>`) et les fiches de donjons. Images
  `https://xixou.io/wp-content/uploads/xixou-ressources/images_clefs/<N>.svg` (SVG). « Gardien » y désigne le boss,
  pas le PNJ d'entrée. Confiance haute.

Contrôle par la langue du client (`tools/langue_client.py`) :

- **Les 40 numéros d'image de xixou sont exactement les gfx du client** (13 pour la Clef des Champs, 226, 237...) : ses
  SVG sont donc les dessins du client, ceux que le rendu local (2.5) produit déjà. Seule anomalie de xixou : l'API
  donne `8971.svg` pour la Clef de la Cale de l'arche d'Otomaï, la fiche du donjon `242.svg` (gfx du client : 242).
- **Niveaux** : les 40 identiques au client. **Recettes de xixou** : identiques au client (contrôle de 16 recettes).
- **Recettes de Barbok** : 15 identiques au client, **16 différentes** (quantités presque toujours plus fortes : Clef
  des Champs 10 + 10 au lieu de 3 + 3, comme Gamosaurus ; sans doute une ancienne version du jeu), 9 clés absentes
  (dont la Clef du Donjon Ensablé). **On garde les recettes du client.**
- **Noms** : Barbok écrit « Clé ... » et abrège (« Clé Donjon Bouftou ») ; le jeu écrit « Clef ... » (« Clef du Donjon
  des Bouftous ») : la recherche dans le coffre doit utiliser le nom du client.
- **Entrées** : toutes celles de xixou concordent avec le client ; le client donne en plus celles que xixou n'a pas
  (Forgerons 13,21 ; Koulosse -17,8 ; Minotoror et Minotot -42,-17 ; Maître Corbac -20,-62 ; Skeunk -20,10 ;
  Incarnam 6,5). Le Labyrinthe du Dragon Cochon n'a pas d'indice dans le client (xixou : -1,33, comme l'antre).
- **Gardiens** : Barbok cite Virgil (Maître Corbac), « le capitaine » (Ensablé), Rigroulak (Skeunk) ; des extraits
  Millenium citent Klik Klak (Squelettes), Bibiblop (Blops), Iturik (Bulbes). Tous existent comme PNJ dans le client,
  sauf Rigroulak (cité seulement dans la description de la clé). Aucun site ne nomme le gardien des Bouftous.

| id | Clé (client) | gfx = image xixou | Donjon et entrée (client) | Gardien | Barbok : nom, image, recette |
|---|---|---|---|---|---|
| 1568 | Clef du Donjon des Bouftous | 226 | Donjon Bouftou (2,-34) | — | Clé Donjon Bouftou ; `bouftou.jpg` ; = |
| 1569 | Clef du Donjon des Forgerons | 227 | Donjon des Forgerons (13,21) | — | Clé du Donjon Forgeron ; `forgeron.jpg` ; ≠ |
| 1570 | Clef du Donjon des Squelettes | 228 | Donjon des Squelettes (10,15) | Klik Klak (Millenium ; PNJ 1163) | Clé Donjon Squelette ; `squel.jpg` ; = |
| 6884 | Clef du Donjon du Bworker | 713 | Donjon du Bworker (-21,12) | — | Clé du Donjon du Bworker ; `bworker.jpg` ; ≠ |
| 7309 | Clef des Pandikazes | 229 | Repaire des Pandikazes (18,-36) | — | Clé du Donjon Pandikaze ; `pandi.jpg` ; = |
| 7310 | Clef de la caverne des Bulbes | 230 | Donjon des Bulbes (19,-22) | Iturik (Millenium ; PNJ 1165) | Clé Donjon Bulbes ; `bulbes.jpg` ; = |
| 7311 | Clef du Donjon des Kitsounes | 231 | Donjon des Kitsounes (32,-38) | — | Clé du Donjon Kitsoune ; `kitsou.jpg` ; ≠ |
| 7312 | Clef du Donjon des Firefoux | 232 | Donjon des Firefoux (28,-52) | — | Clé du Donjon Firefoux ; `foux.jpg` ; = |
| 7511 | Clef du labyrinthe du Dragon Cochon | 3 | Labyrinthe du Dragon Cochon (pas d'indice ; xixou : -1,33) | — | Clé du Labyrinthe du Dragon Cochon ; `dc.jpg` ; = |
| 7557 | Clef du Donjon Abraknyde | 4 | Donjon des Abraknydes (-9,-14) | — | Clé du Donjon Abraknyde ; `abra.jpg` ; ≠ |
| 7908 | Clef du Donjon du Koulosse | 5 | Donjon du Koulosse (-17,8) | — | Clé Donjon Koulosse ; `koul.jpg` ; = |
| 7918 | Clef du Donjon des Tofus | 6 | Donjon des Tofus (5,6) | — | Clé Donjon Tofu ; `tofu.jpg` ; ≠ |
| 7924 | Clef du Labyrinthe du Minotoror | 7 | Labyrinthe du Minotoror (-42,-17) | — | Clé du Labyrinthe du Minotoror ; `mino.jpg` ; = |
| 7926 | Clef du Donjon du Maître Corbac | 8 | Bibliothèque du Maître Corbac (-20,-62) | Virgil (Barbok ; PNJ 774 du client) | Clé Donjon Maître Corbac ; `mc.jpg` ; ≠ |
| 7927 | Clef du Donjon des Craqueleurs | 9 | Donjon des Craqueleurs (-3,-7) | — | Clé Donjon Craqueleur ; `craq.jpg` ; ≠ |
| 8073 | Clef du Repaire de Skeunk | 11 | Repaire de Skeunk (-20,10) | Rigroulak donne la clé (client, description) | absente |
| 8135 | Clef du Donjon des Bworks | 234 | Donjon des Bworks (-5,10) | — | Clé du Donjon Bwork ; `bwork.jpg` ; ≠ |
| 8139 | Clef du Donjon des Scarafeuilles | 233 | Donjon des Scarafeuilles (1,26) | — | Clé du Donjon Scarafeuille ; `scara.jpg` ; ≠ |
| 8142 | Clef Secrète du Tofulailler Royal | 12 | Donjon du Tofu Royal (5,6) | — | Clé Secrète Tofulailler Royal ; `royal.jpg` ; ≠ |
| 8143 | Clef des Champs | 13 | Donjon des champs (7,-25) | Mawy Ingalsse (client) | Clé Donjon des Champs ; `champs.jpg` ; ≠ 10 + 10 |
| 8156 | Clef du Donjon des Canidés | 235 | Donjon des Canidés (-24,1) | — | Clé du Donjon des Canidés ; `canide.jpg` ; ≠ |
| 8307 | Clef de la salle du Minotot | 15 | Antre du Minotot (-42,-17) | — | Clé de la Salle du Minotot ; `tot.jpg` ; = |
| 8320 | Clef de l'antre du Dragon Cochon | 16 | Antre du Dragon Cochon (-1,33) | — | Clé Antre Dragon Cochon ; `antre.jpg` ; ≠ |
| 8342 | Clef du Sanctuaire des Dragoeufs | 18 | Sanctuaire des Dragoeufs (-3,24) | — | Clé du Sanctuaire des Dragoeufs ; `sanctu.jpg` ; = |
| 8343 | Clef du Donjon des Dragoeufs | 17 | Sanctuaire des Dragoeufs (-3,24) | — | Clé du Donjon des Dragoeufs ; `drag.jpg` ; = |
| 8436 | Clef du Donjon du Chêne Mou | 236 | Donjon du Chêne Mou (-9,-14) | — | Clé du Donjon du Chêne Mou ; `cm.jpg` ; ≠ |
| 8437 | Clef du Donjon Ensablé | 237 | Donjon Ensablé (13,-28) | Capitaine Igloute (client) | absente |
| 8438 | Clef du Donjon des Rats de Brâkmar | 238 | Donjon des Rats de Brâkmar (-26,31) | Gardien du donjon des rats de Brâkmar (client) | Clé du Donjon des Rats de Brakmar ; `ratn.jpg` ; = |
| 8439 | Clef du Donjon des Rats de Bonta | 239 | Donjon des Rats de Bonta (-35,-59) | Gardien du donjon des rats de Bonta (client) | Clé du Donjon des Rats de Bonta ; `ratb.jpg` ; = |
| 8545 | Clef du Donjon d'Incarnam | 19 | Donjon d'Incarnam (6,5) | Ma'ster Donge (JOL Retro ; PNJ 886) | absente |
| 8917 | Clef de la Grotte Hesque | 244 | Grotte Hesque (-59,15) | — | Clé de la Grotte Hesque ; `hesque.jpg` ; = |
| 8971 | Clef de la Cale de l'arche d'Otomaï | 242 | Cale de l'Arche d'Otomai (-55,-4) | — | absente |
| 8972 | Clef du Goulet du Rasboul | 243 | Goulet du Rasboul (-51,9) | — | absente |
| 8975 | Clef du Laboratoire du Tynril | 246 | Laboratoire du Tynril (-53,20) | — | absente |
| 8977 | Clef de la Canopée du Kimbo | 245 | Canopée du Kimbo (-54,16) | — | absente |
| 9247 | Clef du Donjon Fungus | 247 | Donjon Fungus (-18,28) | — | Clé du Donjon Fungus ; `fungus.jpg` ; ≠ |
| 9248 | Clef du donjon des Blops | 248 | Donjon des Blops (-25,-17) | Bibiblop (Millenium ; PNJ 1034) | Clé de la caverne Blop ; `blop.jpg` ; ≠ |
| 9254 | Clef de l'antre du Blop Multicolore | 253 | Antre du Blop Multicolore Royal (-25,-17) | — | Clé de l'Antre du blop multicolore ; `blopmulti.jpg` ; = |
| 11474 | Clef du Belvédère d'Ilyzaelle | 255 | Belvédère d'Ilyzaelle (-21,-29) | — | absente |
| 11475 | Clef du Sanctuaire de Qu'Tan | 256 | Sanctuaire de Qu'Tan (-14,16) | — | absente |

Images proposées au téléchargement (**rien n'a été téléchargé** ; tailles lues dans les en-têtes HTTP seulement) :

| Image | Source | Format | Taille | Intérêt |
|---|---|---|---|---|
| Clef des Champs | https://xixou.io/wp-content/uploads/xixou-ressources/images_clefs/13.svg | SVG | non donnée par le serveur | Même dessin que le client (gfx 13) : contrôle du rendu local |
| Clef du Donjon des Bouftous | https://xixou.io/wp-content/uploads/xixou-ressources/images_clefs/226.svg | SVG | non donnée | Idem (gfx 226) |
| Clef du Donjon Ensablé | https://xixou.io/wp-content/uploads/xixou-ressources/images_clefs/237.svg | SVG | non donnée | Idem (gfx 237) |
| Clef des Champs | https://barbok.fr/images/Clefs/champs.jpg | JPG | 2 120 octets | Icône d'une autre époque, peut-être différente |
| Clef du Donjon des Bouftous | https://barbok.fr/images/Clefs/bouftou.jpg | JPG | 1 714 octets | Idem |
| Image « clé » de la fiche Champs | https://barbok.fr/images/donjon/champs/cleechamps.jpg | JPG | 8 920 octets | Contenu inconnu (icône ou capture) |
| Image « clé » de la fiche Bouftous | https://barbok.fr/images/donjon/bouftou/cleebouftou.JPG | JPG | 8 957 octets | Idem |
| Image « clé » de la fiche Ensablé | https://barbok.fr/images/donjon/Ensable/cleebob.jpg | JPG | 11 371 octets | Idem |

Total connu : 33 082 octets (5 JPG de Barbok) + 3 SVG de taille inconnue (quelques Ko attendus). Pour toutes les clés :
40 SVG xixou (`images_clefs/<gfx>.svg`) et 31 JPG Barbok, tailles inconnues. **Avis** : aucun téléchargement n'est
nécessaire, les dessins locaux (2.5) sont les mêmes que ceux de xixou ; les 3 SVG suffiraient à contrôler le rendu
local si l'utilisateur le veut.

## 4. Recommandation

L'équipe : Gela (Crâ 113) et Brawli (Iop 103) portent l'équipe ; Jesuisleking (Crâ 14) et surtout Imthebest (Crâ 6,
6 PA, peu de sorts, pas de coffre) sont fragiles. Le donjon d'Incarnam est exclu (retour impossible après le
niveau 15 pour Gela et Brawli).

| Rang | Donjon | Pour | Contre |
|---|---|---|---|
| **1** | **Donjon des Champs** (7,-25) | Le plus proche : ~12 cartes de la banque, mémoire du bot jusqu'en 8,-25. Monstres de niveau 1 à 15 ; boss à **2 PM** (à distance, idéal pour trois Crâs). Clé bon marché (3 + 3 ressources des champs), aussi laissée par les monstres des champs (et du donjon). Gardienne identifiée (Mawy Ingalsse), dialogue sans doute court | **Salle 3 limitée à 3 combattants** (et salle 5 à 4) : un personnage combat seul ou à part (voir R13). Clé consommée ou non : à vérifier |
| **2** | **Donjon des Bouftous** (2,-34) | Toutes les salles à 8 places : l'équipe de 4 reste ensemble. Clé confirmée « détruite » (rien de flou). Butin de laine. Familier offert à la fin | ~20 cartes de la banque, 7 hors de la mémoire du bot. Bouftous (jusqu'à 15) et Chefs de Guerre (jusqu'à 23) frappent fort au contact ; boss 7 PA, 5 PM, 700 PV : dangereux pour les deux Crâs de bas niveau. 10 combats par run |
| 3 | Donjon Ensablé (13,-28) | Monstres faibles au début (Crabes) | Dialogue d'entrée en 9 étapes ; **Raul Mops (40 à 80)** dans la salle du boss ; clé à base de Goujons (pêche) ; ~21 cartes de la banque |

Proposition : commencer par les **Champs**, avec une règle simple pour la salle 3 (Brawli, niveau 103, fait le
combat à part ; Imthebest jamais seul) ; passer aux **Bouftous** quand les deux Crâs auront pris des niveaux.
Remplacer Imthebest (niveau 6) par un personnage de niveau supérieur à 10 simplifierait la banque (un coffre de plus)
et réduirait le risque de mort ; à défaut, il reçoit ses clés et donne ses ressources par échange.

À demander à l'utilisateur : section 6.

## 5. Conception

### 5.1 Boucle du donjon (états)

Mené par le principal, comme le mode équipe actuel (`bot/team/loop.py`) : un personnage agit à la fois, les suiveurs
suivent. Réglages proposés : `donjon.nom` (clé de `config/donjons.yaml`), `donjon.cles_par_personnage`,
`donjon.runs` (0 = sans fin), `banque.seuil_pods` (0,9 comme aujourd'hui).

| État | Ce que fait le bot | Passe à | Si ça rate |
|---|---|---|---|
| **DÉPART** (exigence de l'utilisateur) | Pour chaque personnage : touche « i », onglet ressources, recherche du nom de la clé, **nombre lu** (chiffres de la pile, ou 0 si « Aucun objet correspondant ») ; puis **annonce** au journal et dans l'interface, par exemple « Clés des Champs : Gela 5, Brawli 0, Jesuisleking 2, Imthebest 0 ; total 7 ; 4 par run : 1 run possible, 3 clés en trop » | RÉPARTITION si au moins une clé par personnage au total ; sinon **message** « pas assez de clés pour tout le monde (il en faut N, il y en a M) » puis BANQUE (reprendre celles des coffres) ou ARRÊT selon le réglage | Nombre illisible : arrêt et message, rien d'échangé |
| **RÉPARTITION** | Objectif : chacun a `floor(total / taille de l'équipe)` clés (le reste chez le principal). Plan d'échanges le plus court (du plus pourvu au moins pourvu), annoncé avant d'agir. Chaque échange : le donneur ouvre l'échange depuis le portrait du receveur (cadre du groupe), vérifie le nom, double-clique **N fois** sur la pile de clés filtrée (un objet par double-clic, validé le 11/10), lit le nombre posé, les deux acceptent ; nombres relus après | CLÉS | Échange refusé, mauvais nom, nombre posé différent : annuler l'échange, arrêt et message |
| **CLÉS** (avant chaque run) | Compteur tenu par le bot : clés après la répartition moins runs faits ; relu dans l'inventaire au retour de la banque | TRAJET_ENTRÉE si chacun en a une ; RÉPARTITION si le total suffit mais pas la répartition ; BANQUE sinon | Inventaire illisible : arrêt et message |
| **POIDS** (avant de partir) | Pods de chacun (`pods_fill`, déjà fait au tour des fenêtres) | BANQUE si l'un est à 90 % ou plus | |
| **TRAJET_ENTRÉE** | `Journey` jusqu'aux coordonnées de l'entrée (7,-25 pour les Champs), toute l'équipe suit (existant) | PNJ | Comme aujourd'hui (regroupement, arrêt) |
| **PNJ** | Sur la carte d'entrée : trouver le gardien (case apprise dans `config/donjons.yaml`, sinon modèle d'image ou couleur, comme le banquier) | DIALOGUE | Gardien introuvable : arrêt |
| **DIALOGUE** (un personnage à la fois, suiveurs d'abord, principal en dernier) | Clic gauche sur le gardien, « Parler » (modèle existant), lire la question du PNJ (texte attendu), cliquer la réponse **par son texte** (« Donner la clef et rentrer » ; jamais « Utiliser le trousseau de clefs » sauf réglage), répéter pour un dialogue en plusieurs étapes (Ensablé), puis attendre le changement de décor | ENTRÉ (personnage suivant), puis SALLE 1 quand tous sont entrés | « Tu n'as pas de clé » ou réponse absente : Échap, ce personnage n'entre pas ; voir le risque R1 |
| **SALLE n** | Tous dans la même salle (décor comparé entre fenêtres : `same_map` existe) ; le principal attaque le groupe (Engager, touche Z), les suiveurs rejoignent, combat (existant). **Salle limitée** (nombre de places lu dans `config/donjons.yaml`, salle 3 des Champs : 3) : seuls les personnages prévus rejoignent, le dernier fait un 2e combat sur le groupe réapparu (le plus fort de préférence) ou attend, selon le réglage | APRÈS_COMBAT | Personne à attaquer : salle déjà nettoyée par d'autres, on passe à la suite ; rejoindre refusé (combat plein) : ce personnage passe au 2e combat |
| **APRÈS_COMBAT** | Fenêtres de résultat fermées (existant), **chacun présent ?** (fenêtre dans la salle, vie > 0), vie basse : repos (existant), pods notés | SORTIE_SALLE ; FIN si c'était le boss | Personnage absent : MORT |
| **SORTIE_SALLE** | Selon le donjon (inconnu pour les trois : à observer) : soleil ou porte de la salle (case apprise à la 1re visite, touche A pour les voir), ou téléportation automatique à la fin du combat. Arrivée jugée au **décor** (les coordonnées ne changent pas) et au numéro de salle tenu par le bot | SALLE n+1 | Soleil introuvable : touche A, puis arrêt avec captures |
| **FIN** (boss battu) | Selon le donjon : salle de fin (la carte « en plus » de 3.1) avec un PNJ (Bouftous : « Prendre le familier avec soi et sortir du donjon » ? Ensablé : capitaine, « Se laisser faire » ?), ou téléporté dehors ; dehors quand la sous-zone redevient celle de l'entrée | CONTRÔLE | Réponse attendue absente : capture et arrêt |
| **CONTRÔLE** | Pods de chacun ; clés restantes (compteur) | BANQUE si pods ≥ 90 % ou plus de clé ; sinon DIALOGUE (déjà devant l'entrée) | |
| **BANQUE** | Trajet 4,-16, porte (23,9), puis pour chaque personnage : 5.2 ; sortie (8,26) | TRAJET_ENTRÉE | Voir 5.2 |
| **MORT** | Un personnage a été ramené à son point de sauvegarde (ERROR_142), le zaap d'Astrub 4,-19 : il quitte le run et attend à la banque (3 cartes) ; les autres finissent si le principal et assez de forces restent, sinon tous sortent | Fin du run, puis regroupement à la banque | Équipe battue : tous ramenés en 4,-19, arrêt avec message |
| **ARRÊT** | Plus de clés au coffre, fenêtre inconnue, défaite, personnage perdu deux fois | — | Message clair, touches relâchées |

Mémoire du donjon : un fichier séparé (`data/donjons/<donjon>.json`, à créer) avec, par numéro de salle, l'empreinte du
décor, la case du soleil de sortie, la durée du combat. **Rien dans `data/carte.json`**, dont les clés « région x,y »
mélangeraient toutes les salles avec la carte de l'entrée.

### 5.2 Banque : tout déposer, filtrer sur les clés, les reprendre

Hypothèse de déroulé, par personnage de niveau 11 ou plus, coffre ouvert (`tools/banquier.py`, existant) :

1. Inventaire : onglet ressources, menu du bouton aux flèches, « Transférer tous les objets » (existant,
   `tools/coffre_transfert.py`) : les clés partent aussi.
2. Coffre : onglet ressources du **coffre**, menu aux flèches (ou « Tri et recherche »), « Recherche d'un objet »,
   saisir le nom (« Clef des Champs » : le nom entier écarte les autres clés ; 2 caractères minimum en banque depuis
   la 1.31 d'après Millenium, à lire sur la fenêtre), valider.
3. Reprendre la pile filtrée : **Ctrl + double-clic** sur la 1re case du coffre (pile entière ; validé le 11/10 dans
   l'échange avec la fenêtre devant et une vraie touche Ctrl), ou double-clic puis fenêtre de quantité : saisir le
   nombre, ou « max » puis la coche (modèles `quantite_max.png`, `quantite_valider.png` existants). Depuis la 1.31,
   un transfert propose de lui-même le maximum que les pods permettent (Millenium). Ne jamais utiliser
   « Transférer tous les objets » côté coffre tant qu'on n'a pas vérifié qu'il respecte le filtre : sinon tout revient
   dans l'inventaire.
4. « Réinitialiser la recherche », puis fermer le coffre (geste à confirmer sur capture, `tools/coffre_transfert.py`
   ne le fait pas encore ; attention : Échap sans fenêtre ouverte ouvre le Menu principal).

Combien de clés : 1 par personnage et par run, si chacun donne la sienne (à confirmer). Proposition :
`cles_par_personnage = 5` (5 runs entre deux banques ; une clé pèse 1 ou 2 pods).

Les quatre personnages sont sur **quatre comptes : un coffre chacun** (réponse du 12/10). Donc :
- Gela, Brawli, Jesuisleking (niveau > 10) : chacun « tout déposer, filtrer, reprendre » dans son coffre, 5 clés ;
- Imthebest (niveau 6) : **pas de coffre**. Il donne ses ressources à un autre par échange (mode `un_par_un`
  existant) et reçoit ses 5 clés par échange (état RÉPARTITION) ;
- les clés en trop d'un coffre ne servent qu'à son personnage : la RÉPARTITION les fait circuler par échanges entre
  inventaires, pas par les coffres.

À vérifier sur captures (hypothèses) :

| # | Hypothèse | Comment vérifier |
|---|---|---|
| H1 | Le coffre a, comme l'inventaire, des onglets (dont ressources) et un bouton aux flèches | Capture du coffre ouvert |
| H2 | Son menu contient « Recherche d'un objet » (ou un champ de recherche visible) | Capture du menu ouvert, côté coffre et côté inventaire |
| H3 | La fenêtre « Nom de l'objet (N caractères minimum) » : champ, bouton de validation | Capture de la fenêtre |
| H4 | Le filtre ne garde que les objets dont le nom contient le texte | Capture du coffre filtré sur « Clef des Champs » |
| H5 | Taper du texte par messages en arrière-plan marche dans ce champ (le bot n'envoie aujourd'hui que des touches, pas de caractères) | Essai en session |
| H6 | Double-clic sur une pile du coffre : un objet, une fenêtre de quantité, ou la pile entière avec Ctrl | Essai en session, capture de la fenêtre de quantité |
| H7 | « Transférer tous les objets » côté coffre respecte (ou non) le filtre | Essai avec un coffre de 2 objets seulement |
| H8 | Les clés sont bien dans l'onglet ressources du coffre et de l'inventaire | Capture (le client le laisse prévoir : super-type 9) |

Repli si la recherche n'est pas utilisable : trier le coffre par nom ou par type et trouver l'icône de la clé par
modèle d'image dans la grille.

### 5.3 Ce qui existe dans le bot et ce qui manque

| Besoin | Existe | Manque |
|---|---|---|
| Détecter une clé dans l'inventaire | Rien (pas de lecture de l'inventaire) ; textes Verdana reconnus (`perception/text.py`) ; dessins des clés dans l'installation (2.5) | Ouvrir l'inventaire (touche « i »), onglet ressources, recherche par nom ou modèle de l'icône, chiffres de la quantité ; compteur de clés ; annonce au départ |
| Répartir les clés (exigence du 12/10) | Échange principal ↔ suiveur ouvert depuis le portrait, nom vérifié, double-clic objet par objet, « Accepter » des deux côtés (`tools/echange*.py`, testés le 11/10) | Échange entre deux personnages quelconques ; poser exactement N clés et le vérifier ; plan d'échanges ; message « pas assez de clés » |
| Dialogue avec le PNJ d'entrée | Dans les outils seulement : clic gauche sur un PNJ, « Parler », réponse reconnue par son texte (`tools/banquier.py`) | Module de dialogue dans le bot : trouver le PNJ, lire la question, choisir une réponse par texte, dialogue en plusieurs étapes, « pas de clé » |
| Reconnaître qu'on est dans le donjon | Nom de zone comparé en image (`perception/maplabel.py`) ; décor comparé (`travel.same_map`) | Modèles des noms de sous-zone du donjon ; ne plus juger l'arrivée sur les coordonnées (`_wait_arrival` attend qu'elles changent : dans un donjon il échouerait à chaque salle) |
| Savoir dans quelle salle | Rien | Compteur de salle + empreinte du décor par salle (mémoire du donjon) ; **désactiver l'apprentissage de `data/carte.json`** dans le donjon |
| Passer à la salle suivante | Soleils trouvés (touche A, `perception/exits.py`), passage de toute l'équipe (`team/loop.py`) | Accepter un soleil ou une porte hors du bord (aujourd'hui écartés : maisons, grottes) ; arrivée au décor ; variante « téléporté à la fin du combat » |
| Combat en salle | Engager, rejoindre, tours, sorts, fin de combat, repos (existants) | Ne pas « changer de carte faute de groupe » dans une salle déjà nettoyée ; salle limitée en places (Champs salle 3) : choisir qui rejoint, 2e combat pour le dernier, combat plein reconnu |
| Mort d'un personnage | Rien (le trajet voit seulement un personnage « déplacé ailleurs ») | Après chaque combat : chacun encore dans la salle ? Sinon : il est sorti du run, message, suite selon 5.1 |
| Sortie du donjon | Rien | Selon le donjon : PNJ « Sortir du donjon », soleil de sortie, téléportation ; retour reconnu à la sous-zone de l'entrée |
| Pods | `ui.pods_fill`, noté au tour des fenêtres, avertissement à 90 % | Déclencher l'aller à la banque (prévu, `plan_banque.md`) |
| Banque | Outils séparés : porte, banquier, coffre, « Transférer tous les objets », échanges | Les mettre dans le bot ; filtre du coffre (5.2) ; saisie de texte ; reprise d'une pile ; échange principal → suiveur |
| Données | `tools/langue_client.py`, `config/donjons.yaml` (nouveaux) | Lecture de `config/donjons.yaml` par le bot, cases du PNJ et des soleils apprises |

### 5.4 Captures d'écran à demander à l'utilisateur

Fenêtre entière (1920 x 1009, comme d'habitude), sans rien masquer. Pour le donjon choisi (les Champs en premier) :

1. **Inventaire** hors banque (touche « i ») : onglet ressources avec une clé (icône et nombre, avec 1 clé puis
   plusieurs) ; infobulle de la clé au survol ; la recherche par nom dans l'inventaire.
2. **Menu du bouton aux flèches** de l'inventaire, ouvert (toutes ses entrées).
3. **Coffre ouvert** (banque d'Astrub) avec ses onglets ; **menu aux flèches côté coffre** ouvert.
4. **Fenêtre « Recherche d'un objet »** vide, puis avec « Clef des Champs » tapé ; le **coffre filtré** ; la même chose
   sans résultat (« Aucun objet correspondant à la recherche »).
5. **Fenêtre de quantité** en reprenant une pile du coffre (bouton « max », coche).
6. **Carte d'entrée** (7,-25) : vue entière avec le gardien, sans touche puis avec A tenu (soleils, porte).
7. **Menu au clic sur le gardien** (« Parler »...), puis **la fenêtre de dialogue complète** : avec une clé, et (si
   possible) sans clé ; chaque étape s'il y en a plusieurs.
8. **Salle 1** juste après l'entrée : vue entière, nom de la carte en haut à gauche lisible ; puis avec Z tenu (groupe).
9. **Une salle après la victoire** : avec A tenu (sortie), et l'arrivée dans la salle suivante.
   **Salle 3 des Champs** : le placement avec 3 personnages, et ce que voit le 4e (épée du combat, groupe réapparu ?).
10. **Salle du boss** avant le combat, la fenêtre de résultat, et ce qui suit (PNJ, coffre, téléportation, salle de
    sortie) jusqu'à l'arrivée dehors.
11. **Si ça arrive** : fin de combat avec un personnage mort (sa fenêtre après), et une défaite.
12. **Échange entre deux personnages** avec des clés posées (nombre affiché dans la case de l'échange), vu des deux
    côtés, notamment vers Imthebest (pas de coffre).
13. Pods maximum de chaque personnage (les niveaux sont connus).

### 5.5 Risques

| # | Risque | Effet | Parade proposée |
|---|---|---|---|
| R1 | Clé consommée mais groupe incomplet (un personnage n'a pas de clé, ou son dialogue rate) | Une partie de l'équipe seule en salle 1, clés perdues | Clés vérifiées dans **tous** les inventaires avant le premier dialogue ; suiveurs d'abord, principal en dernier ; si un suiveur échoue, le principal n'entre pas et ceux déjà dedans sortent (à vérifier : sortie possible de la salle 1 ?) |
| R2 | Personnage de bas niveau mort (6 PA, peu de sorts) | Ramené à son point de sauvegarde, énergie perdue, sa clé perdue | Donjon facile pour Gela et Brawli ; placement en retrait (case la plus loin) ; Brawli au contact ; vérification de présence après chaque combat ; option : runs avec Gela et Brawli seulement |
| R3 | Défaite de l'équipe | Tous ramenés, énergie perdue, clés perdues | Arrêt immédiat avec message ; donjon choisi bien en dessous du niveau de Gela |
| R4 | Salles aux mêmes coordonnées | Arrivée jamais vue, mémoire des cartes polluée (soleils de salle notés sur la carte d'entrée) | Mode donjon : arrivée au décor, apprentissage de `data/carte.json` coupé, mémoire à part |
| R5 | Mauvaise réponse au dialogue (trousseau, « Se sauver en courant ») | Charge hebdomadaire du trousseau dépensée, ou combat raté | Réponse choisie par texte exact, sinon Échap et arrêt |
| R6 | Accès au coffre : Imthebest (niveau 6) n'y a pas accès ; un coffre par personnage | Imthebest ne peut ni déposer ni reprendre | Échanges (RÉPARTITION, mode `un_par_un`) ; ou le remplacer par un personnage de niveau > 10 |
| R6b | Échange de clés mal compté | Un personnage sans clé devant le gardien (R1) | Nombre posé relu dans la fenêtre d'échange avant d'accepter, inventaires relus après |
| R7 | « Transférer tous les objets » côté coffre ignore le filtre | Tout le coffre revient dans l'inventaire, personnage surchargé | Ne pas l'utiliser côté coffre (5.2), sauf essai H7 réussi |
| R8 | Saisie du nom impossible en arrière-plan | Filtre inutilisable | Fenêtre devant le temps de la saisie (comme Ctrl pour les piles), ou modèle d'icône dans le coffre trié |
| R9 | Autres joueurs dans le donjon (groupe déjà battu, combat en cours) | Pas de groupe à attaquer, ou mauvais groupe | Salle sans groupe : passer à la suivante ; ne jamais rejoindre un combat d'inconnus |
| R10 | Plus de clés au coffre | Boucle bloquée | Compteur de clés ; arrêt propre quand il en manque pour toute l'équipe |
| R11 | Dialogue d'entrée long (Ensablé : 9 étapes) | Étape ratée | Chaque étape vérifiée par le texte de la question avant de répondre |
| R12 | Agression en chemin dans le Berceau (Bouftous agressifs ?) | Combat imprévu | Déjà géré (combat en trajet) |
| R13 | Salle limitée à 3 combattants (salle 3 des Champs) | Le 4e ne peut pas rejoindre ; s'il attaque seul le groupe réapparu, un Crâ de bas niveau peut mourir | Ordre de ralliement prévu par salle ; le 2e combat confié à Gela ou Brawli ; vérifier en jeu que le groupe réapparaît et que la sortie s'ouvre pour tous |
| R14 | Défaite en cours de donjon | D'après internet : tout recommencer, clés et énergie perdues | Arrêt ; le bot ne relance pas un run sans avis |

### 5.6 Cas de test à ajouter au plan (`docs/plan_de_tests.md`, sections 2 à 5)

Le plan n'est pas modifié : voici les ajouts proposés.

**Section 2, indicateurs « Donjon »**

| Indicateur | Définition | Seuil de validation |
|---|---|---|
| Clés lues | Présence (et nombre) lus contre l'inventaire | 100 % |
| Répartition | Clés de chacun après les échanges = plan annoncé | 100 %, aucun échange avec le mauvais personnage |
| Entrée | Personnages entrés / tentés, au 1er dialogue | ≥ 95 %, jamais d'équipe coupée en deux (R1) |
| Passage de salle | Salles passées / tentées, équipe groupée | ≥ 98 % |
| Run complet | Runs finis dehors, sans intervention | ≥ 90 % |
| Clés | Clés consommées = runs x personnages | 100 % (aucune perdue) |
| Morts | Personnages ramenés au point de sauvegarde | 0 sur 10 runs |
| Banque des clés | Clés reprises = clés voulues, coffre filtré | 100 % |
| Durée | Run complet, et cycle runs + banque | À mesurer (référence) |

**Section 3, bancs d'essai**

| Banc | Où | Ce qu'il éprouve |
|---|---|---|
| DC, Donjon des Champs | Entrée 7,-25, 8 cartes de salle | Dialogue, salles aux mêmes coordonnées, boss à 2 PM |
| DB, Donjon des Bouftous | Entrée 2,-34, 11 salles | Run long, trajet par le Berceau |
| DE, Donjon Ensablé | Entrée 13,-28, 10 salles | Dialogue en plusieurs étapes, sortie par PNJ |
| B, banque d'Astrub | 4,-16 (existant) | Filtre du coffre, reprise des clés, échange principal → suiveur |

**Section 4, catalogue : 4.5 Donjon (DJ), et ajouts au 4.3 Banque (B)**

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

**Section 5, programme**

| Session | Thème | Banc | Tests | Durée |
|---|---|---|---|---|
| 8 | Clés, répartition et coffre (outils) | B | DJ0, DJ0b, DJ0c, DJ1, B11, B12, B13 | 45 min |
| 9 | Entrée et première salle | DC | DJ2, DJ3, DJ4, DJ5 | 30 min |
| 10 | Run complet | DC | DJ5 à DJ8 | 45 min |
| 11 | Cycle complet | DC → B → DC | DJ9, DJ10 | 60 min |

## 6. Questions pour l'utilisateur

Réponses du 12/10 : section 0 (niveaux, comptes, zaap 4,-19, touche « i », annonce et répartition des clés).

Restent ouvertes :
1. Avez-vous déjà des **clés** (lesquelles, combien, sur qui) ? Un **Bricoleur** ? Achat à l'hôtel de vente ?
   Un **trousseau de clefs** ?
2. Quel donjon en premier (proposition : les Champs) ?
3. Imthebest remplacé, ou gardé (échanges pour ses clés et ses ressources, jamais seul en salle 3) ?
4. Pods maximum de chacun.
5. Accord pour **télécharger les images** des clés proposées en 3.5, ou préférence pour les dessins locaux (2.5).

## 05/11 : donjon des Champs, premier essai (session d'une heure)

### Observé en jeu (Fourboun, `tools/observer_pnj.py`, rien consommé)

- Mawy Ingalsse (modèle 780) se tient **dehors**, en 7,-25 (carte 7453), case 187, soit (26,12).
  - Clic gauche, menu « Parler », question 3178.
  - Réponses proposées : « Donner la clef et rentrer » (2802) et « Utiliser le trousseau de clefs. » (6615).
  - La bulle a été refermée sans répondre.
- La porte en (18,12) mène à l'atelier (carte 7613) d'Emia Elliesol (558) : ce n'est pas l'entrée.
  - On en sort par la case 367, soit (9,25).
- Sacs, d'après la connexion :
  - Fourboun a 1 Clef des Champs, et chacun des 8 a un trousseau.
  - Les **coffres** contiennent des Clefs des Champs chez au moins 7 personnages : le serveur liste leur pile.

### Ajouté

- `config/donjons_entree.yaml` : entrée `champs`, d'après ce qui a été observé. La sortie se fait par une case de la
  salle de fin (`sortie_case`, case 260 de 9786, d'après les données StarLoco locales : **à vérifier en jeu**).
- **Combats en vagues** (demande de l'utilisateur : à 8, le bot passe les salles à peu de places, et les plus hauts
  niveaux vont en plus petit nombre) :
  - places de chaque salle d'après la référence du client : salle 3 à 3, salle 5 à 4 ;
  - vagues de tailles aussi égales que possible, les plus forts dans les plus petites. Salle 3 : Waldarch et Elseiso,
    puis Fourboun, Eldieso et Fourbodeux, puis Walitcoin, Enu-six et Pesopepe ;
  - chaque vague lance le combat par son plus haut niveau, et seuls ses membres le rejoignent ;
  - la vague suivante attend la réapparition du groupe (3 min au plus) ;
  - pas encore essayé en jeu.
- **Corrigé** : le trajet de l'équipe vers la banque ou le donjon (`team_trip`) ne faisait pas suivre les suiveurs.
  Le principal est arrivé seul à la banque, « pas entré dans la banque » pour les 7 autres, et le trajet a été cru
  réussi. Vérifié en jeu au second essai : toute l'équipe suit.

### Ce qui bloque encore : les clés des coffres

- 7 coffres ouverts sur 7, mais « la pile de clés n'a pas quitté le coffre d'après le serveur » chez chacun : le
  glisser du coffre vers le sac ne prend pas. La fermeture passe ensuite par Échap, qui ouvre le Menu principal.
- Eldieso : la bulle du banquier s'est refermée sans ouvrir le coffre (127 kamas par consultation : assez de kamas ?).
- Le bot s'est donc arrêté avant l'entrée, comme prévu (1 clé pour 8 personnages) : rien n'a été consommé.
- À faire : observer le glisser du coffre (captures avant/après, messages du serveur), sans la souris de l'utilisateur
  sur une fenêtre du jeu (elle casse les glisser en arrière-plan, 30/10).

### 05/11, suite : les clés des coffres (observé avec `tools/observer_coffre.py`)

- Coffre de Walitcoin, filtré sur « Clefs » : 11 piles de clés, et la Clef des Champs à la place prévue (rang 9 dans
  l'ordre d'arrivée des messages du serveur). Le rang était donc juste.
- **Le glisser n'attrapait pas la pile** : aucune fenêtre de quantité, rien du serveur. En plus, le point de lâcher
  (`BAG_DROP`) tombait sur un objet du sac plein, pas sur une case vide.
- **Un vrai double-clic sur la pile en fait passer une dans le sac**, confirmé par le serveur (`EsKO+pile|reste`,
  `OAKO…~1fcf~1`). `take_from_chest` reprend maintenant une clé par double-clic, chacune vérifiée au serveur.
- Essai complet : Eldieso a repris 7 clés de son coffre (7 -> 0), puis 5 échanges sur 6 ont réussi.
- **Échange Eldieso -> Elseiso raté** : le menu de la miniature d'Elseiso n'avait ni « Défier » ni « Échanger »,
  alors que les deux étaient en 7,-25 d'après le serveur. Cause pas encore vue.
- Le bot s'est arrêté avant l'entrée, comme prévu : rien consommé, personne entré.
- État des sacs à la fin : chacun a sa Clef des Champs, sauf Elseiso ; Eldieso en a 2.

### 05/11, soir : premier passage complet du donjon des Champs, à 8 (journal 200925)

- **Échange vers Elseiso** : le menu de la 8e miniature descend sous la carte, et « Echange » (y=848) était hors de
  la zone lue, limitée à la carte. Zone limitée à la fenêtre : échange conclu.
- **Sac mal compté** : la clé prise au coffre pendant l'observation (processus séparé) n'avait pas été entendue par le
  bot ; la clé reçue ensuite s'était empilée dessus (`OQ36102380|2`, sans modèle). Ajoutée au sac mémorisé de Walitcoin
  d'après ces messages du serveur.
- **Situer l'équipe** : fenêtres déjà connectées, aucune carte annoncée ; les coordonnées lues à l'écran (7,-25)
  situent chacun devant Mawy.
- **Le passage** : entrée à 8 en 22 s (« Donner la clef et rentrer »), puis 7 salles.
  - Salle 3, en 3 vagues : Waldarch et Elseiso, puis Fourboun, Enu-six et Eldieso, puis Fourbodeux, Walitcoin et
    Pesopepe.
  - Salle 5, en 2 vagues de 4.
  - Boss battu.
  - Sortie par la case (27,17) de la salle de fin : les 8 dehors (carte 7452).
- 10 combats en 17 min 40, 0 mort, 0 erreur, 0 déplacement sans bouger.

### 05/11, nuit : session de 3 passages (journaux 205041 à 214119)

- **Passage 1 coupé par F12** pendant la salle 5 (vague 1). Relancé tout de suite (choix de l'utilisateur).
  - Les cartes de l'équipe n'étaient pas encore annoncées : Fourboun et Waldarch ont fait seuls la salle 6 et le boss
    (sortis à 23 % et 40 % de vie, sans mort).
  - Correction : pas d'attaque sans toute l'équipe sur la carte du principal (`_all_here`).
  - `donjon.reprise=meme_salle` sert à reprendre une équipe réunie.
  - Reprise à la main en trois étapes : vague 2 en salle 5, puis les 6 en salle 6 et au boss, puis retour des 8 en
    7,-25.
- **Passage 2** : à 8, 10 combats en 15 min 50, 0 mort (salle 3 en 3 vagues, salle 5 en 2).
- **Passage 3 pas lancé à la suite** : la sortie par la case (27,17) dépose l'équipe en 7,-26 (carte 7452), pas devant
  Mawy en 7,-25 ; « ni devant l'entrée ni dans les premières salles ». Correction : avant un nouveau passage, quand la
  sortie est une case, l'équipe retourne devant l'entrée (`DungeonFarm._back_to_entrance`, `team_trip`).
  - Pas encore vu en jeu, car le passage 3 a été lancé après un `--aller 7,-25`.
- **Passage 3** : à 8, 10 combats en 14 min 40, 0 mort. Pesopepe est passé niveau 31 et Fourbodeux niveau 37.
- **Clés** : 3 par personnage au départ, toutes utilisées. Il en reste 36 au coffre de Fourboun. Pas besoin de farm de
  clés dans les Champs pour ces 3 passages.

### 06/11 : donjon en boucle (sans essai en jeu)

- **Retour à l'entrée avant chaque passage**, le premier compris. Il se fait quand toute l'équipe est sûrement dehors et
  loin du PNJ (`DungeonEntry.away`).
  - « Sûrement dehors » : la carte annoncée par le serveur n'est ni celle du PNJ ni une salle du donjon
    (`data/donjons.json`). Si la carte n'est pas encore annoncée, ce sont les coordonnées lues à l'écran qui comptent,
    quand elles diffèrent de celles de l'entrée.
  - Jamais depuis une salle. Jamais pour un donjon sans carte de PNJ (Incarnam).
  - Ce test remplace celui d'hier, qui se basait sur la sortie par une case.
- **`--minutes` ne coupe plus un passage.** Un passage ne commence que s'il a le temps de finir, estimé d'après la durée
  du passage précédent (18 min pour le premier). Pendant le passage, la limite de la session est suspendue. Une reprise
  dans les salles se finit toujours.
- **Clés** : le mode `donjon.cles=sac_banque` existe déjà. Au début du farm, ceux qui manquent de clés vont à leur
  coffre, puis les clés sont réparties.
- **À décider : clés quand le coffre est vide.** La Clef des Champs tombe à environ 0,5 % sur les monstres des Champs
  (client et web, `config/donjons.yaml`). Elle se fabrique aussi au métier de Bricoleur, avec 3 Pétales de Tournesol
  Sauvage et 3 Fleurs de Pissenlit Diabolique.
- **Décidé (utilisateur, 06/11) : farm du butin.** Le réglage permet les deux voies, mais la fabrication n'est pas encore
  faite : il faut désigner qui a le métier (Fourboun, Bricoleur 35).
  - Réglage `donjon.cles_manquantes` : `farm`, `fabrication` ou `non` (par défaut).
  - Avec `farm` : quand les clés réparties ne suffisent plus pour un passage, l'équipe farme la zone de la clé
    (`zone_cles: [7, -25]`, Champs d'Astrub) en mode zone (`pods.team_zone_farm`).
  - Le farm s'arrête dès que le butin donne une clé par personnage d'après le serveur. Sinon, il s'arrête au bout de
    `donjon.farm_cles_minutes` (60 min), ou plus tôt pour garder le temps d'un passage sur `--minutes`.
  - Les clés sont alors réparties, sans retour au coffre. L'équipe revient à l'entrée, puis fait le passage suivant.
  - Si le butin ne donne pas une clé chacun, les passages s'arrêtent.
  - Il faut `donjon.cles=sac` ou `sac_banque`.
  - Exemple :
    `--donjon champs --passages 10 --set donjon.cles=sac_banque --set donjon.cles_manquantes=farm --minutes 240`.
  - Pas encore vu en jeu.

### 06/11, soir : essai en jeu de la boucle (session d'une heure, journaux 194826 et 200051)

- **Farm des clés** (10 min, `donjon.cles=sac`, sacs vides) : l'équipe est partie tout de suite farmer la zone des
  Champs. 8 combats, 0 mort. Elle s'est arrêtée à la fin des 10 minutes, après la fin du dernier combat. Aucune clé
  n'est tombée (environ 0,5 %), donc pas de passage. La mécanique est validée.
- **Waldarch pas connecté** : son premier clic sur « Jouer » s'est perdu. En cascade, la liste des personnages voulus
  ne comptait plus le choix fait par sa propre fenêtre : elle était vide, et la fenêtre a été crue inutile puis fermée
  (« aucun de  sur ce compte »). Corrigé dans `login._character`, testé. Au lancement suivant, les 8 étaient en jeu.
- **Boucle** (`sac_banque` + `cles_manquantes=farm`, 2 passages demandés, `--minutes 45`) :
  - Coffre : trajet de l'équipe jusqu'à la banque, Fourboun a repris 16 clés (coffre 36 -> 20), retour devant
    l'entrée. Validé.
  - Échanges : 5 sur 7 du premier coup. Les 2 autres ont été annulés avec 3 clés posées au lieu de 2 : un double-clic
    en avait posé 2, annoncées en deux messages, et le clic suivant partait dès la première annonce.
    - Correction : la quantité offerte est relue une fois stable, et le bot ne pose que ce qui manque. Testé : le test
      échoue sans la correction.
    - Les échanges ratés sont aussi retentés une fois.
    - En jeu, c'est le farm des clés qui a refait la répartition : 16 clés pour 8, assez tout de suite. Les 2 échanges
      ont réussi au deuxième essai.
  - Pause « BLOQUÉ » de 2 min juste après l'entrée : les 5 minutes d'échanges ne comptaient pas comme un progrès.
    Corrigé : la répartition des clés, l'entrée et la sortie du donjon sont notées comme des progrès.
  - Passage : 11 combats, 0 mort, 19 min (pause comprise).
  - Limite de temps : « plus le temps pour un passage de plus (19 min estimées, 16 min restantes) ». L'équipe s'est
    arrêtée dehors, pas au milieu du donjon. Validé.
- Pas encore vu en jeu : le retour à l'entrée depuis 7,-26 au passage suivant (l'équipe était déjà devant l'entrée au
  départ), ni les deux corrections d'échange.
- État à la fin : les 8 sont en 7,-26 avec 1 clé chacun ; il reste 20 clés au coffre de Fourboun.

### 06/11, nuit : donjon des Champs validé (session de 2 h, journal 210755)

Demande de l'utilisateur : 3 donjons des Champs sans problème pour valider. Fait en un seul lancement (`--donjon champs
--passages 3 --set donjon.cles=sac_banque --set donjon.cles_manquantes=farm --minutes 115`) :
- **Clés** : 1 par sac au départ. L'équipe est allée à la banque et a repris 16 clés dans les coffres de Walitcoin,
  Waldarch, Pesopepe et Enu-six. Ensuite, 6 échanges sur 6 ont réussi du premier coup (environ 30 s chacun) : 3 clés
  chacun.
  - Chez Fourboun, le type « Clefs » n'a pas été trouvé dans la liste du coffre. Correction : capture, puis onglet et
    liste rouverts une fois. Pas encore vu en jeu.
- **Passages** : 17, 16 et 16 minutes. 30 combats, 0 mort, 0 erreur, 0 pause « BLOQUÉ ». Le retour à l'entrée
  depuis 7,-26 s'est fait seul avant les passages 2 et 3 (30 s).
- **Sortie** : à chaque passage, Waldarch, Pesopepe et Enu-six ont raté leur premier clic. Les journaux réseau
  montrent que les 8 clics tombent au même endroit pendant que les premiers personnages marchent sur la case : aucun
  déplacement annoncé pour eux. Ils ont été recliqués après 12 s chacun (environ 50 s pour sortir).
  - Correction : sans déplacement annoncé 1,5 s après le clic, le bot reclique tout de suite à un autre endroit
    (`_all_take`, comme les soleils des cartes). Pas encore vu en jeu.
- **Clés des coffres**, relevées dans les journaux réseau ; désormais aussi notées au journal à chaque coffre ouvert
  (`log_chest_keys`). Par donjon, total des coffres lus (Fourboun, Eldieso, Walitcoin, Waldarch, Pesopepe, Enu-six) :
  - Bouftou 242 ;
  - Rats de Brâkmar 48 ;
  - Blops 32 ;
  - Squelettes 29 ;
  - Incarnam 80 ;
  - Tofus 19 ;
  - Champs 24 (avant la session) ;
  - Scarafeuilles 16 ;
  - Bworks 15 ;
  - Abraknyde 14 ;
  - Ensablé 10 ;
  - Canidés 12.

**Le donjon des Champs est validé** (critère de l'utilisateur : 3 passages sans problème).
