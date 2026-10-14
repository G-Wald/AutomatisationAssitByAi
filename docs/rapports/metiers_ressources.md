# Métiers, ressources et gardiens : données, repérage à l'écran, conception (12/10)

Suite de `docs/rapports/metiers.md` (08/10), sans le refaire. Rien n'a été importé dans `config/` ni dans `data/`
(hors `data/ressources_essai/`, dossier de l'essai). Le jeu n'a pas été touché.

Repères de fiabilité, utilisés partout dans ce document :

- **[client]** : lu dans la langue du client Retro 1.49.6 (`data/client_retro/lang`, langue des cartes 1384).
  Donnée officielle, vérifiée.
- **[captures]** : mesuré sur les 269 captures réelles de `logs/` (11/10, 21:04 à 22:13). Vérifié.
- **[web]** : page internet Retro ou 1.29, URL donnée. À vérifier en jeu.
- **[émulateur]** : code d'un serveur privé 1.29 (Ancestra). Indice seulement : ce n'est pas Retro 1.49.
- **[supposé]** : déduction de ma part.

Outil de l'essai : `tools/ressources_essai.py`, tests dans `tests/test_ressources_essai.py`.
Suite complète : 786 tests passés, 2 ignorés.

---

## En bref

1. **Repérage des arbres : la méthode marche.**
   - Méthode : un gabarit par ressource et par état, cherché seulement aux centres des cases.
   - Résultats (une capture par carte) :
     - Frêne : 68 trouvés sur 89 attendus (76 %) ;
     - Châtaignier : 29 sur 40 (73 %).
   - Fausses détections :
     - aucune sur les 72 arbres debout trouvés (vérifiés un par un, à l'œil) ;
     - parmi les souches, une seule erreur d'état (un tronc de Frêne pris pour une souche).
   - La case est donnée directement : il suffit de cliquer dessus.
   - Les manques viennent surtout d'arbres cachés derrière un décor (cité) ou coupés par le haut de l'écran.
2. **Une ressource récoltée se voit.** Le Frêne coupé laisse une grosse souche beige, le Châtaignier une petite souche
   brune.
   - Preuve [captures] : en 8 minutes (22:05 → 22:13), sur 3,-27 et 2,-27, quelqu'un a coupé 4 Frênes et
     2 Châtaigniers. Ils sont passés de « debout » à « souche », sur les mêmes cases.
3. **Céréales : pas de résultat exploitable sur ces captures.**
   - Les touffes sont serrées et se recouvrent, et les captures sont en JPEG : impossible d'isoler une touffe.
   - La couleur seule ne marche pas : les champs ont 103 à 147 cases « dorées », les cartes sans céréales en ont
     96 en médiane et jusqu'à 207.
   - Il faut des captures avant et après fauche (section 5.3).
4. **Aides du jeu pour repérer les ressources** :
   - la touche A ne met en valeur que les soleils de sortie [captures] ;
   - aucun raccourci du client ne montre les ressources [client] ;
   - le survol est impossible en arrière-plan (essai du 08/10).
5. **Mini-jeux de récolte (annoncés pour la 1.47) : probablement absents.**
   - La langue du client 1.49.6 n'en contient aucun texte [client].
   - Le système testé en bêta a été retiré de la 1.47 (tweet officiel du 30/06/2025) [web].
   - Reste la première chose à vérifier en jeu.
6. **Gardiens : 57 protecteurs dans le client** [client].
   - 5 grades : niveau 10 à 50, 30 à 70 PV, 6 PA, 2 PM.
   - Ils ne sont placés sur aucune carte : ils n'apparaissent qu'à la récolte.
   - Apparition environ 1 fois sur 40 ; niveau qui suit celui du personnage [web 1.29].
   - Combat seul ou rejoignable par le groupe : **inconnu, à tester**.

---

## 1. Métiers de récolte

### 1.1 Ce que donne le client (tout est [client])

Commande : `.venv/Scripts/python -m tools.ressources_essai --metiers`. Tables lues :

- J : les métiers ;
- SK : les compétences, c'est-à-dire l'action du menu, l'objet interactif et l'objet obtenu ;
- IO : les objets interactifs, avec un graphisme par ressource ;
- I : les objets (niveau, poids en pods, type) ;
- M et MR : les monstres et leurs races.

**Les 5 métiers et leur action**

| Métier (id) | Action du menu | Ressources |
|---|---|---|
| Bûcheron (2) | **Couper** | 16 arbres |
| Paysan (28) | **Faucher** | 9 céréales |
| Alchimiste (26) | **Cueillir** | 7 plantes (Lin et Chanvre aussi pour le Paysan) |
| Mineur (24) | **Collecter** | 11 minerais |
| Pêcheur (36) | **Pêcher** | 10 points de pêche |

- L'action du Mineur, « Collecter », manquait dans `metiers.md`.
- Hors métier, d'autres actions existent : « Puiser » (puits, donne de l'Eau), « Ramasser » (tas de patates),
  « Pêcher KoinKoin ».
- Chaque ressource a **un seul graphisme**, par exemple :
  - Frêne = 7500, Châtaignier = 7501, Noyer = 7502, Chêne = 7503, Érable = 7504 ;
  - Blé = 7511, Orge = 7515, Avoine = 7517, Houblon = 7512.
- Tous les exemplaires d'une ressource se ressemblent donc au pixel près. C'est ce qui rend les gabarits fiables
  (section 3).

**Niveau et poids**

Le niveau de l'objet [client] n'est pas le niveau de métier requis. Celui-ci est décidé par le serveur.
La référence (`config/monde`, relevé solomonk) donne le niveau de récolte. Les deux diffèrent parfois :

| Ressource | Niv. objet [client] | Niv. récolte (référence) | Pods par unité [client] |
|---|---|---|---|
| Frêne / Châtaignier / Noyer / Chêne / Érable | 1 / 10 / 20 / 30 / 40 | 1 / 10 / 20 / 30 / 40 | 5 |
| If / Bombu / Oliviolet / Kaliptus / Bambou | 50 / 50 / 50 / 50 / 50 | 50 / 35 / 40 / 50 / 60 | 5 (Kaliptus 2) |
| Merisier / Ébène / Charme / Orme | 60 / 70 / 80 / 90 | **85 / 60 / 75 / 70** | 5 |
| Blé / Orge / Avoine / Riz / Houblon | 1 / 10 / 20 / 20 / 30 | 1 / 10 / 20 / 20 / 30 | 2 |
| Lin / Seigle / Malt / Chanvre (Paysan) | 40 / 50 / 60 / 70 | 40 / 50 / 60 / 70 | 2 |
| Fleurs et plantes (Alchimiste) | 1 à 50 | 20 à 60 | 1 (Pandouille 10) |
| Minerais (Fer → Or, Dolomite) | 1 à 80 | 1 à 80 | 5 |
| Poissons | 1 à 20 | 1 à 60 | 1 à 3 (Pichon 20) |

Le désaccord sur le Riz (20 ou 50), signalé le 08/10, ne se tranche pas ici : objet niveau 20, référence 20. Les vrais
niveaux se lisent dans la fenêtre des métiers (touche **j**).

**Outils**

- Types d'objet « Hache » (19), « Outil » (20), « Pioche » (21) et « Faux » (22) [client].
- Ils ont le même super-type (2) que les armes (Arc, Épée…).
- La table des emplacements du client range ce super-type à la **position 1, celle de l'arme**. L'outil s'équipe donc
  à la place de l'arme.
- Le texte d'aide du client dit « (Double clique pour l'équiper) ». Le geste est donc très probablement un double-clic.
  À confirmer.
- Outils de niveau 1 sans condition :

| Métier | Outils | Pods | Prix de base |
|---|---|---|---|
| Bûcheron | Hache de Bûcheron | 10 | 150 |
| Bûcheron | Hache de l'apprenti bûcheron | 10 | 0 |
| Paysan | Faux du Paysan | 10 | 150 |
| Paysan | Faux de l'apprenti paysan | 10 | 1 |
| Mineur | Pioche du Mineur | 20 | 150 |
| Pêcheur | Canne à Pêche Courte | 1 | 200 |
| Pêcheur | Canne de l'apprenti pêcheur | 4 | 1 |
| Alchimiste | Gants de l'apprenti alchimiste | 5 | 1 |

- Les objets « de l'apprenti » sont sans doute ceux donnés à Incarnam [supposé].
- Les cannes plus grandes exigent un niveau de Pêcheur : Canne à Pêche Standard au-delà du niveau 9, Grande Canne
  au-delà du niveau 39, etc.
- [captures] La barre d'objets de Gela contient une faux (capture 4,-16 du 11/10). Gela est peut-être déjà Paysan.

**Textes officiels que le bot pourra lire dans le chat** (ANKLANGSO, mot pour mot)

| Clé | Texte | Usage pour le bot |
|---|---|---|
| ITEM_FOUND | « Vous avez trouvé %1 x %2 en récoltant %3. » | Fin de récolte réussie, quantité |
| INFOS_17 | « Tu as gagné %1 XP dans le métier %2. » | Suivi de l'XP |
| NEW_JOB_LEVEL | « Ton métier %1 passe niveau %2. » | Niveau de métier |
| ERROR_307 | « Vous devez attendre %1 minutes et %2 secondes avant de pouvoir récolter cette ressource. » | Délai de récolte affiché ; à voir ce qui le déclenche |
| INFOS_144 | « Votre inventaire est plein. Votre récolte est perdue... » | Arrêt : direction la banque |
| ERROR_311 | « Votre inventaire est plein. Impossible de récolter la ressource » | Idem |
| NO_TOOL_JOB / ERROR_85 | « Pour exercer ce métier il faut déjà équiper son outil. » / « Vous devez équiper votre outil pour cette action. » | Outil à équiper |
| ERROR_2 / INFOS_204 / ERROR_46 | « Tu n'exerces pas le métier nécessaire » / « Tu n'exerces pas le métier %1. » | Mauvais personnage ou métier |
| ERROR_295 / INFOS_62 | « Action impossible car tu es surchargé. » / « Action annulée pour cause de surcharge... » | Pods dépassés |
| ERROR_276 | « Tu es trop chargé. Tu ne pourras bientôt plus rejoindre de combat. » | Alerte de pods |
| PAYZONE_MSG_12 | « Tu as dépassé le niveau 30 dans ton métier. Tu ne peux plus progresser dans ton métier sans devenir membre… » | Plafond sans abonnement |
| LAST_HARVESTER_NAME | « Dernier récolteur » | Sans doute l'infobulle d'une ressource déjà récoltée [supposé] |
| OPTION_ANTILAG_POINTS | « Masquer l'affichage des points (PA, PM, récolte) » | Option à laisser **décochée** : elle cacherait les chiffres de récolte |

**Raccourcis du client** (table SSK, clavier français)

- **j** : Métiers. **i** : Inventaire. **c** : Caractéristiques.
- **a** : « Mettre en valeur les cellules importantes de la carte ».
- **e** : « Cacher les sprites ». **z** : infobulles des monstres.
- **Maj+é** : transparence des joueurs. **Maj+&** : grille.
- Toutes les actions de raccourci du client ont été lues (une cinquantaine) : **aucune ne met en valeur les
  ressources**.

**Base de connaissances du client** (KBA 36 « Les pods »)

- Chaque niveau de métier rapporte **5 pods**, plus **1000 pods** pour chaque métier au niveau 100.
- Un point de force rapporte 5 pods.
- Des sacs de récolteur demandent un niveau de métier : Sac du Petit Récolteur au niveau 20, Sac du Récolteur au
  niveau 40, Sac du Grand Récolteur au niveau 60.
- Un dialogue du client confirme les protecteurs : « Les protecteurs de ressources qu'on trouve sur Amakna attaquent
  parfois les récolteurs, néanmoins, une fois vaincus, ils procureront une bonne quantité de matériaux. »

### 1.2 Comment se passe une récolte

| Étape | Ce qu'on sait | Source |
|---|---|---|
| 1. Clic sur la ressource | Clic gauche sur le graphisme (arbre, touffe) | [web] JOL Retro « introduction aux métiers » |
| | Le clic droit pour récolter est désactivé depuis la 1.34 (déc. 2020) | [web] note de la 1.34, lue en extrait |
| | Maj + clic saute le menu (1.31). **Impossible en arrière-plan** : le bot ne sait pas envoyer Maj en arrière-plan | [web] millenium.org/news/357321 |
| 2. Menu de l'action | « Couper », « Faucher », « Collecter », « Cueillir », « Pêcher » | [client] |
| 3. Trajet | Le personnage marche jusqu'à la ressource | [supposé], aucune source écrite |
| 4. Récolte | Durée de **12 s moins 0,1 s par niveau de métier** : 12 s au niveau 0, 2 s au niveau 100. Barre ou simple animation : inconnu | [émulateur] et wiki (extrait) |
| | La ressource est réservée dès le début : un autre joueur ne peut pas la prendre | [émulateur] |
| 5. Gain | Chiffre rouge au-dessus du personnage, plus le message ITEM_FOUND dans le chat | [web] JOL ; [client] |
| | Quantité : 1 à 2 au début pour le Frêne, +1 à chaque niveau finissant par 0 ou 5 | [web] DofuX (1.29) |
| | Plus la ressource est haute, moins on en tire au départ | [émulateur] formule `niveau/5` |
| 6. Ressource épuisée | Arbre → souche : grosse souche beige pour le Frêne, petite souche brune pour le Châtaignier | **[captures]** : 6 arbres passés de debout à souche sur la même case |
| | États du protocole : plein, en cours, vide, repousse | [émulateur] |
| 7. Repousse | **Au moins 8 min** pour 6 arbres : aucune repousse vue entre 22:05 et 22:13 | **[captures]** |
| | Une souche claire est restée **46 min** au même endroit (0,-13, 21:05 → 21:51) : décor, ou repousse plus longue | **[captures]** |
| | Frêne 5 à 10 min ; Châtaignier et Noyer 7 à 15 ; Chêne 10 à 17 ; Bombu 25 à 40 | [web] forum de serveur privé, version inconnue |
| | Temps fixe par modèle d'objet (table `respawn`) | [émulateur] |
| 8. Délai après changement de personnage | Existe depuis 1.47.1 / 1.47.2 : « appliqué par rapport au temps écoulé depuis la dernière récolte ». Durée inconnue. Concerne l'équipe | [web] notes recopiées par xixou.io/changelog |

### 1.3 Mise à jour 1.47 et mini-jeux

- **Juin 2025** [web] : le devblog « refonte des métiers de récolte » annonce des mini-jeux.
  - Le 30/06/2025, un tweet officiel annonce que le système testé « ne sera pas intégré à la mise à jour 1.47 ».
  - Source : https://api.fxtwitter.com/DOFUSRetro_FR/status/1939708568216748328
- **1.47.0** [web] : la limite du nombre de métiers est retirée.
  - Notes 1.47.12 recopiées sur https://xixou.io/changelog/?xixpn_tab=retro&xixpn_page=1
- **1.48 et 1.49** [web] : aucun changement de récolte trouvé.
  - 1.48 : succès de récolte et « gardiens de ressource vaincus ».
  - 1.49 : encyclopédie.
- **[client]** : aucun texte de mini-jeu dans les 72 tables de langue (recherche de « mini-jeu », « minijeu »,
  « récolteur », « repousse »…).
- **Conclusion** : très probablement pas de mini-jeu en 1.49.6, ce qui reste à vérifier à la première récolte. Le bot
  s'arrêtera sur toute fenêtre inconnue pendant une récolte.

---

## 2. Gardiens (protecteurs) de ressources

**Client [client]**

- 57 protecteurs, répartis en 5 races :

| Race | Nombre | Noms |
|---|---|---|
| Protecteurs des Arbres | 16 | « X Ensorcelé » : Frêne Ensorcelé, Châtaignier Ensorcelé… |
| Protecteurs des Poissons | 14 | « Dévoreur de … » |
| Protecteurs des Minerais | 11 | « Craqueboule de … » |
| Protecteurs des Céréales | 9 | « Epouvantail de … » |
| Protecteurs des Plantes | 7 | Bulbilin, Bulbitrèfle… |

- Ils forment la super-race 15 « Protecteurs des ressources ».
- Ces noms sont bien dans le client Retro 1.49.6. L'agent de recherche web doutait de « Bulbi… » et « Dévoreur… » : le
  client lève le doute.
- **5 grades** : niveaux 10, 20, 30, 40, 50 ; vie 30, 40, 50, 60, 70 ; 6 PA (7 pour le Bambou Ensorcelé) ; 2 PM.
- **Aucune zone d'apparition** (`spawn` vide) : ils ne sont placés sur aucune carte et ne sortent qu'à la récolte.

**Web**

- Toutes les ressources ont un protecteur. Il donne un sac de 50 ressources.
  - [web] https://dofusretro.jeuxonline.info/article/14773/introduction-aux-metiers
  - Exemple : la Craqueboule de Fer donne un Sac de Fer à 100 % (https://xixou.io/encyclopedie/monstres/craqueboule-de-fer/).
- **Probabilité** : environ **1/40** par récolte.
  - [web 1.29] https://www.dofux.org/articles-166-les-metiers.dx
  - Non retrouvé sur les pages JOL Retro lisibles.
- **Niveau** : il suit le niveau du **personnage**, de 10 en 10, au plus 50 [web 1.29, même page]. C'est cohérent avec les
  5 grades du client.
  - Seuil possible : rien avant le niveau 20 ou 21 du **métier** (vieux forum JOL ; millenium.org/guide/345477). À vérifier.
- **Toujours présents en Retro** : bug « [1.33.6] Déconnexion lors d'un clic droit après l'attaque d'un protecteur
  de ressource » [web, extrait], et succès de la 1.48.
- **Sans réponse Retro** :
  - combat seul ou ouvert au groupe ;
  - fuite possible ;
  - perte d'énergie en cas de défaite ;
  - récolte gardée ou perdue ;
  - état de la ressource après le combat.
  - Ce qui circule (« combat seul, pas de perte d'énergie ») vient de pages Dofus 2. Je l'ai écarté.

**Conséquences pour le bot**

1. Un combat peut **démarrer sans que le bot ait cliqué « Attaquer »**, au milieu d'une récolte.
   - La lecture d'état existante (phase de placement, `read_scene`) le voit déjà.
   - Il faut que la boucle de récolte cède la main à la boucle de combat, quel que soit l'étape en cours (menu ouvert,
     marche, récolte).
2. **Équipe** : si le combat est ouvert, les suiveurs sur la même carte voient l'épée et peuvent rejoindre (code de
   ralliement existant). S'il est fermé, le récolteur se bat seul.
   - Avec 30 à 70 PV en face, un personnage de niveau moyen gagne sans peine [supposé].
   - Le risque, c'est un personnage faible ou qui se bat sans ses sorts.
3. **Après le combat** : on est sur la même carte.
   - Fermer « Résultat du combat », puis refaire un repérage.
   - Ne pas se fier à la liste des cases d'avant : la ressource a pu passer à l'état de souche.
   - Le sac de 50 ressources pèse lourd : vérifier les pods.
4. **Arrêt de sécurité** : défaite contre un protecteur → arrêt avec message (comme C8 du plan de tests).

---

## 3. Repérer les ressources à l'écran

### 3.1 Pistes passées en revue

| Piste | Verdict | Pourquoi |
|---|---|---|
| **Gabarit par ressource, ancré sur les cases** | **Retenue** | Un seul graphisme par ressource [client]. Chiffres en 3.3 |
| Couleur et forme seules | Écartée | Mesuré sur les céréales : aucune séparation (3.4). Les arbres de décor ont les mêmes couleurs |
| Contour au survol | Impossible en arrière-plan | Essai du 08/10 (run 5) : Chromium envoie WM_MOUSELEAVE d'après la vraie souris. Le contour existe en Retro mais est « très faible » [web, forum Retro] |
| Curseur ou infobulle | Impossible en arrière-plan | Même raison : il faut le survol |
| Touche du jeu | Aucune | A : seuls les soleils sont marqués. Sur 5,-25 (~75 ressources dans la référence) : 4 losanges, tous des sorties [captures]. Aucun autre raccourci [client]. **E** (cacher les personnages) aide : les arbres restent visibles, les joueurs ne les cachent plus |
| **Avec la référence** | **Retenue, en appoint** | Elle dit quelles ressources chercher et combien. Il ne faut chercher que ces gabarits (plus rapide, moins de confusions). S'il en manque, on cherche plus loin (bas des arbres, seuil plus bas). Ses comptes sont approximatifs, voir 3.3 |
| Mémoire des cases | **Retenue** | Une case vue « debout » une fois est une ressource. Au passage suivant, on ne regarde que ces cases (quelques ms). Une souche jamais vue debout peut être un décor (0,-13) |
| Fichiers de cartes du client | Impossible | Cases chiffrées, la clé vient du serveur (`cartes_client.md`) |

### 3.2 L'expérience hors jeu

**Données**

- Les 269 captures de `logs/` du 11/10.
- 251 ont leur carte retrouvée d'après `bot.log` : le chemin des captures « nom illisible », sinon la dernière arrivée
  du personnage avant l'heure de la capture. Cela fait 84 cartes.
- 86 captures sont sur des cartes qui ont des ressources dans la référence : cité, forêt et champs d'Astrub.
- Index : `data/ressources_essai/index.json`.

**Gabarits** (`data/ressources_essai/gabarits/`, définitions dans `gabarits.json`)

| Gabarit | Aspect | Exemplaires | Boîte autour de la case |
|---|---|---|---|
| `frene` | arbre touffu vert vif, tronc beige | 13, sur 5 cartes de forêt | 150 × 235 px, pied du tronc sur la case |
| `chataignier` | arbre rond vert olive, châtaignes brunes, tronc gris-brun | 7, sur 6 cartes (forêt et cité) | 170 × 240 px |
| `frene_souche` | grosse souche beige, dessus jaune | 10 | 68 × 62 px |
| `chataignier_souche` | petite souche brun foncé, dessus orangé | 8 | 68 × 62 px |

- Chaque gabarit est la **médiane** de ses exemplaires, alignés à ±4 px. Ce qui passe devant disparaît : herbes,
  personnages, autres arbres.
- Le **masque** garde les pixels stables d'un exemplaire à l'autre : 40 à 71 % du gabarit.
- Pour identifier les arbres, j'ai confronté les comptes de la référence aux captures :
  - sur 2,-23, la référence donne seulement « Frêne 6 » : 6 arbres identiques sont visibles ;
  - sur 1,-26 (« Frêne 9 »), on compte à l'œil 9 grosses souches beiges et aucun Frêne debout ;
  - les châtaignes sont visibles sur le Châtaignier.
- Les arbres rouges ou orangés et les arbres fins à tronc foncé sont du décor : la référence ne les compte jamais.

**Repérage**

- Pour chaque case de la carte, le gabarit est posé avec son ancre sur le centre de la case (repère de
  `bot/perception/grid.py`), à ±3 px.
- La corrélation normalisée est calculée avec le masque, et aussi avec l'image retournée (miroir).
- Un seul calcul pour toute la carte ; au cas par cas sur les bords, avec le gabarit rogné à sa partie visible.
- Une case garde son meilleur gabarit. Deux détections à moins de 40 px n'en font qu'une.
- **Toutes les détections tombent pile sur une case** : l'écart mesuré est de ±0,02 case sur 19 arbres. C'est ce qui
  évite les doublons et donne directement où cliquer.

**Seuils retenus** : 0,6 pour un arbre debout, 0,7 pour une souche. Une souche ressemble au pied d'un arbre ou à une
autre souche.

**Commandes**

```
.venv/Scripts/python -m tools.ressources_essai --index
.venv/Scripts/python -m tools.ressources_essai --gabarits
.venv/Scripts/python -m tools.ressources_essai --essai                                   # 251 captures, seuils 0,5 à 0,7
.venv/Scripts/python -m tools.ressources_essai --essai --une-par-carte --bas --seuils 0.6 --seuil-souche 0.7
```

### 3.3 Chiffres

**Une capture par carte** : 84 cartes. Fichiers `resultats_par_carte.json` et `resultats_bas_par_carte.json`.

| | Frêne | Châtaignier |
|---|---|---|
| Cartes où la référence en met | 19 | 18 |
| Attendus (référence) | 89 | 40 |
| **Trouvés, debout ou souche** (gabarit entier) | 64 (72 %), dont 13 souches | 28 (70 %), dont 9 souches |
| **Avec le bas des arbres en plus** (`--bas`) | **68 (76 %)** | **29 (73 %)** |
| Plus que la référence sur une carte | 0 | 0 |
| Trouvés sur une carte où la référence n'en met pas | 3 (voir ci-dessous) | 0 sur 66 cartes |
| Cartes ayant servi aux gabarits / autres | 54 sur 72 / 14 sur 17 | 23 sur 29 / 6 sur 11 |

**Justesse, vérifiée à l'œil sur planches** (`data/ressources_essai/planches/`)

- Sur les 251 captures, j'ai regardé une vignette par objet distinct au-dessus du seuil :
  - Frêne debout : **52 sur 52** justes ;
  - Châtaignier debout : **20 sur 20** justes ;
  - souches de Frêne : 17 sur 18 dans le bon état. L'erreur : un tronc de Frêne coupé par le haut de l'écran, compté
    comme souche ;
  - souches de Châtaignier : **11 sur 11** justes.
- Sous 0,7, trois fausses souches de Châtaignier apparaissent (scores 0,61 à 0,66) : une souche rose, une souche
  claire, un pied d'arbre de décor. D'où le seuil de 0,7.
- Les 3 détections « sans référence » sont réelles :
  - 8,-17 : un vrai Frêne (score 0,96), absent de la référence ;
  - 0,-13 et -2,-17 : de vraies souches claires. Celle de 0,-13 ne bouge pas en 46 min : c'est sans doute un décor.
- **Le bas des arbres** (`--bas`, 110 px au-dessus du pied) ajoute 5 arbres, tous justes : 4 Frênes coupés par le haut
  de l'écran et 1 Châtaignier caché par un rocher.

**Toutes les captures** (`resultats.json`, 251 captures, plusieurs par carte, seuil unique 0,6)

| | Frêne | Châtaignier |
|---|---|---|
| Trouvés / attendus | 125 / 188 | 67 / 94 |
| Captures de cartes sans cette ressource dans la référence, avec une détection | 6 sur 202 : le Frêne de 8,-17 et les 2 souches | 0 sur 208 |

**Répétabilité** : la même carte capturée plusieurs fois donne les mêmes cases. Exemples :
- 1,-27 : 4 captures, 8 objets identiques ;
- 4,-16 : 9 captures, 1 Frêne en (22,30) à chaque fois ;
- 0,-20, 1,-19, 2,-16 : identiques.

**Ce qui manque, et pourquoi**

- **Décor devant** : dans la cité, des arbres orange sont posés devant.
  - -1,-15 : 0 trouvé sur 4. Un Châtaignier est visible à moitié derrière un arbre orange.
  - 4,-16 : 1 sur 2. Le deuxième arbre visible est un arbre de décor, d'un autre dessin.
- **Haut de l'écran** : arbres coupés. La partie basse en rattrape une partie.
- **Référence approximative** : elle oublie le Frêne de 8,-17. Sur 3,-27, elle annonce 18 Frênes, on en voit une quinzaine.

**Vitesse** (hors jeu, un cœur)

- Environ 0,5 s par gabarit et par carte entière sans masque, 1,2 s avec masque pour les arbres, 0,45 s pour les souches.
- Dans le bot, on ne cherche que les ressources annoncées par la référence, puis seulement les cases connues. C'est
  rapide : moins de 0,1 s attendu [supposé].

### 3.4 Céréales : pas de résultat

- **Gabarit** : je n'ai pas pu isoler une touffe.
  - Les champs (5,-24 à 5,-27, 4,-27) sont des massifs continus.
  - Il y a 45 à 73 céréales par carte dans la référence, mêlées d'herbes hautes de décor.
  - Les captures « changement de carte » sont en JPEG.
- **Couleur** : on compte les cases dont le haut contient au moins 10 % de pixels dorés (R − G ≥ 15, R ≥ 150, B ≤ 110).
  - Champs : 103 à 147 cases.
  - Cartes de la cité qui ont des céréales : 88 à 127.
  - 75 cartes sans céréales : 11 à 207 cases, médiane 96.
  - Aucune séparation : chemins, bois et feuillages d'automne ont la même couleur.
- **Ce qui débloquera** : une capture PNG d'un champ avant et après la fauche de 2 ou 3 touffes connues (5.3, n° 5).
  La différence donne le dessin exact de la touffe mûre et de la touffe fauchée. La méthode des arbres s'applique
  ensuite telle quelle.
- **Variante** : le bot apprend tout seul. Après une fauche réussie sur une case, la différence avant/après donne le
  gabarit.

---

## 4. Sources de données sur les ressources (rien n'est importé)

| Source | Contenu et format | Couverture, date | Conditions d'usage | Avis |
|---|---|---|---|---|
| **Langue du client** (`data/client_retro/lang`, déjà sur le disque) | Métiers, actions, objets, poids, outils, protecteurs, textes du chat (section 1) | Officielle, 1.49.6 | Données du jeu installé | **À privilégier**. Pas de position des ressources |
| **Déjà importé** (`config/monde`, 28/09) | Ressources par carte : nom, nombre, métier, niveau. Pas de case | Monde des Douze, relevé solomonk du 08/03/2026 | — | À garder. Comptes approximatifs (3.3) |
| xixou.io, carte interactive | Ressources par métier et niveau, liste des cartes [x,y]. Pas de case, pas de repousse. HTML | Retro 1.49, mis à jour le 06/10/2026 | **CGU v3.2.0 (23/08/2026)** : interdisent « le scraping massif des données du site sans autorisation préalable » et « l'utilisation de robots, scripts automatisés… » | Rien de neuf. **Attention** : l'import du 28/09 a été fait par script (`tools/import_monde.py`) |
| dofus-retro.co | Nombre de cartes par ressource (ex. « Frêne (122) »). HTML | Mises à jour de 2020 au 30/03/2024 | Aucune licence ; « visuels propriété d'Ankama » | Recoupement seulement |
| solomonk.fr | Bestiaire (57 protecteurs), ressources. Pages en JavaScript | 2019–2026 | « Tous droits réservés », CGU ; prévient que des données peuvent être fausses | Déjà la source de la référence |
| wiki.moon-bot.io/api | JSON : `/api/items.json`, `/api/monster/{id}.json`. Pas de cartes ni de repousse | Annoncé 1.29, « extrait du client » | MIT, demande de créditer. **Site d'un vendeur de bot** | Inutile : le client donne mieux |
| Émulateurs 1.29 (Ancestra-Evolutive, AncestraRemake, PeakEmu) | SQL : `interactive_objects_data(id, respawn, duration, unknow, walkable)`, cartes avec cellules | **1.29.1, pas Retro**. SQL de 37 Mo (non téléchargé) | Ancestra et PeakEmu : pas de licence ; Araknemu : LGPLv3, schéma sans données | Seule source chiffrée de repousse, mais pas de Retro : à éviter |
| Arakne php-map-parser | Lecture des cellules (calque 2 « interactif ») | 1.29 et Retro, mais il faut la clé de la carte | LGPLv3 | Inutile : nos cartes sont chiffrées |
| Articles JOL Retro, DofuX, millenium | Textes (déroulé, quantités, protecteurs, outils) | 2007–2020 | Articles protégés : on cite, on ne copie pas | Utilisés ici, avec leurs URL |

**Exemples de quelques lignes**

Langue du client, compétence et objet interactif :

```
SK[6]  = {d: "Couper",    j: 2,  io: 1,  i: 303}     IO.d[1]  = {n: "Frêne", t: 1}   IO.g[7500] = 1
SK[45] = {d: "Faucher",   j: 28, io: 38, i: 289}     I.u[303] = {n: "Bois de Frêne", l: 1, w: 5, t: 38}
SK[24] = {d: "Collecter", j: 24, io: 17, i: 312}     M[711]   = {n: "Frêne Ensorcelé", b: 64, g1: {l: 10, lp: 30, ap: 6, mp: 2}}
```

Référence déjà importée, carte 4,-16 :

```
"objets": [{"nom": "Frêne", "type": "ressource", "nombre": 2, "metiers": ["Bûcheron"], "niveau": 1}]
```

dofus-retro.co : `Frêne (122)`. API moon-bot :

```
{ "id": 37, "name": "Craqueleur", "level_min": 1, "level_max": 6, ...
```

Émulateur 1.29, `Constants.java` d'Ancestra :

```
list.add(new JobAction(6,1 + dropWin,2 + dropWin,false,12000-timeWin,10));  //Couper Frêne
```

**Ma proposition** : ne rien importer d'internet. Le client donne les métiers et les objets. La référence donne les
ressources par carte. Les cases, la repousse et l'état s'apprennent en jeu.

**Incident** : pendant la recherche, l'outil de lecture web a enregistré de lui-même une copie d'un fichier GitHub
(`Constants.java` d'Ancestra). Elle est hors du projet, dans
`C:\Users\gauth\.claude\projects\...\tool-results\webfetch-1791832093376-9zbuyi.bin`. Rien n'a été copié dans le
projet. Tu peux supprimer ce fichier.

---

## 5. Conception

### 5.1 Boucle de récolte en équipe

1. **Choix de la zone**, d'après la référence :
   - cartes de la ressource visée, au niveau du métier ;
   - hors zones interdites, près d'une banque.
   - Pour commencer : la **forêt d'Astrub** (1,-23 à 3,-27 et 2,-23). Frênes et Châtaigniers, 89 et 40 attendus sur
     19 cartes. Banque en 4,-16.
2. **Circuit** :
   - cartes classées par quantité attendue et par temps écoulé depuis la dernière coupe (repousse apprise : on note
     l'heure de chaque passage à l'état de souche) ;
   - l'équipe suit le principal (`travel.py`).
3. **Repérer**, à l'arrivée, touche E enfoncée (personnages cachés) :
   - cases déjà connues pour cette carte, sinon gabarits des ressources annoncées (arbre entier, puis bas de l'arbre) ;
   - on obtient une liste (ressource, case, debout ou souche) ;
   - on retient les cases dans `data/carte.json`, avec une rubrique nouvelle à créer : ressources par case, état, heure.
4. **Répartir** : chaque personnage prend la ressource debout la plus proche de lui, sans doublon.
   - Chacun doit avoir le métier et l'outil. Sinon, seuls ceux qui l'ont récoltent, et les autres attendent ou gardent.
5. **Récolter** (chaque personnage) :
   1. clic gauche sur le tronc, quelques pixels au-dessus de la case, jamais deux fois le même pixel ;
   2. attendre le menu (`popups.py`) et cliquer l'action (« Couper »…) ;
   3. attendre la fin, au plus 12 s + trajet : message ITEM_FOUND dans le chat, ou la case passe à l'état de souche ;
   4. sinon (message d'erreur, rien après le délai) : ignorer la case 60 s.
6. **Gardien** : une phase de combat est détectée à tout moment, et la boucle de combat prend la main.
   - Ralliement si c'est possible.
   - Puis fermer le résultat, refaire un repérage et reprendre.
7. **Pods** : lus après chaque récolte (jauge existante).
   - À 90 % chez le principal, ou chez tous en mode « chacun » : banque, avec la chaîne déjà conçue (`plan_banque.md`).
   - Puis retour au circuit.
8. **Arrêts** : fenêtre inconnue (mini-jeu ?), outil absent, inventaire plein, défaite, message inconnu → capture et
   arrêt propre.

### 5.2 Ce que le bot a, ce qui manque

| Brique | État |
|---|---|
| Trajets de carte en carte, équipe qui suit, mémoire des cartes | **Existe** (`travel.py`, `data/carte.json`) |
| Repère des cases (`grid.py`), recherche de gabarits (`templates.py`) | **Existe** |
| Menu après un clic (`popups.py`, comme « Attaquer ») | **Existe** ; il faut les gabarits ou le texte des entrées de récolte |
| Combat, ralliement, fin de combat | **Existe** ; à brancher sur un combat qu'on n'a pas lancé |
| Lecture des pods, outils de banque et d'échange | **Existe** (`team/loop.py`, `tools/banquier.py`…) ; à intégrer au bot (B2 du plan de tests) |
| Clics en arrière-plan, touche E | **Existe** ; clic droit et Maj impossibles en arrière-plan, sans gêne pour la récolte |
| Repérage des arbres | **Prototype** (`tools/ressources_essai.py`) ; à passer dans `bot/perception/resources.py` |
| Gabarits des autres ressources (Noyer, Chêne, Érable, céréales, minerais) | **Manque** : captures nécessaires |
| Lecture du chat (ITEM_FOUND, erreurs) | **Manque** ; le bot lit déjà du texte (`text.py`), sur un nouveau rectangle |
| Mémoire des cases de ressources et de la repousse | **Manque** (format à ajouter à `data/carte.json`) |
| Équiper l'outil, lire le niveau de métier (fenêtre j) | **Manque** |
| Mode « récolte » dans la boucle, réglages `harvest`, bouton de l'interface | **Manque** |

### 5.3 Captures à demander

Toutes en **PNG**, fenêtre entière (1920 × 1009), en notant **la carte, l'heure et la case visée**. Partout où un
arbre précis est nommé, prendre le **Frêne de 4,-16, case (22,30)**, près de la banque : celui à droite du chemin,
en bas.

1. **Menu de récolte**, hache équipée : clic gauche sur ce Frêne, capture menu ouvert. Puis la même chose sur un
   Châtaignier (1,-27, case (13,19)).
2. **Sans outil**, puis **personnage sans le métier** : même clic. Capture du menu, ou de l'absence de menu, et du chat.
3. **Pendant la récolte** : une capture par seconde, du clic sur « Couper » à la fin. On veut voir la marche,
   l'animation, une barre éventuelle, puis le chiffre rouge et les messages du chat (ITEM_FOUND, XP).
4. **Juste après** : le même arbre en souche (même case), puis une capture toutes les 2 à 3 minutes jusqu'à sa
   repousse, **avec l'heure** : c'est la mesure de la repousse.
   - Puis un clic sur la souche : menu ? message « Vous devez attendre… » ? infobulle « Dernier récolteur » ?
5. **Céréales** : champ 5,-26 ou Avoine de 4,-17. Une capture avant, puis après la fauche de 2 ou 3 touffes, en notant
   leurs cases. Même chose pour le Blé de 1,-16 si possible. Il faut aussi le menu « Faucher ».
6. **Mini-jeu** : toute fenêtre qui apparaît pendant une récolte, étape par étape. **Priorité absolue**.
7. **Gardien**, s'il apparaît :
   - l'écran de placement ;
   - l'écran **d'un suiveur** sur la même carte : épée visible ? « rejoindre » possible ?
   - le résultat du combat ;
   - la carte juste après : état de la ressource, sac reçu.
8. **Fenêtre des métiers** (touche j) : liste des compétences avec quantités et durées. Elle donnera les niveaux
   requis.
9. **Inventaire** (touche i) :
   - l'emplacement d'arme, vide, puis avec l'outil après un **double-clic** sur l'outil ;
   - la jauge de pods presque vide et presque pleine, avec « %1 pods sur %2 ».
10. **Maj + clic** sur une ressource, au premier plan : le menu est-il sauté ? Information seulement, le bot ne pourra
    pas l'utiliser en arrière-plan.
11. Si possible, une carte de forêt **en PNG** (pas de JPEG) avec des Noyers, Chênes ou Érables, pour les gabarits
    suivants : 2,-27 a un Chêne ; 8,-14 à 8,-19 ont Noyer, Chêne, If, Érable.

### 5.4 Risques

- **Anti-triche** :
  - la récolte est la cible classique des bots ;
  - Ankama a testé des mini-jeux contre eux, retirés pour l'instant ;
  - un délai existe après un changement de personnage (1.47.1) ;
  - parades : rythme humain, pauses, pas de série de 4 personnages synchronisés sur la même carte, arrêt sur toute
    fenêtre inconnue.
- **Abonnement** : sans abonnement, un métier plafonne au niveau 30 (PAYZONE_MSG_12) et on ne peut pas récolter en zone
  d'abonnés (PAY_ZONE_DESC) [client]. À vérifier pour les 4 comptes.
- **Référence imparfaite** : comptes faux ou manquants, comme 8,-17. Le bot doit se fier à ce qu'il voit et à sa
  mémoire, la référence ne sert qu'à choisir les cartes.
- **Décor ressemblant** : souches de décor (0,-13), arbres cachés.
  - Une case n'est sûre qu'une fois vue debout ou après un menu de récolte obtenu.
  - Une case cliquée sans menu deux fois de suite est oubliée.
- **Concurrence** : les arbres d'Astrub sont coupés par d'autres joueurs : 6 coupés en 8 minutes sur 2 cartes. Il faut
  revérifier l'état juste avant de cliquer, et accepter une ressource prise pendant la marche.
- **Gardien** : combat imprévu, peut-être en solo, au milieu d'un menu. Il faut que tout état de la récolte puisse être
  interrompu. Défaite = arrêt.
- **Pods** : le sac de 50 ressources du gardien et l'inventaire plein font **perdre la récolte** (INFOS_144). La marge
  doit tenir compte du sac.
- **Arrière-plan** : le menu de récolte doit s'ouvrir avec un clic posté. Le menu « Attaquer » s'ouvre bien ainsi :
  très probable [supposé].
- **CGU de xixou.io** (scripts interdits) : décider s'il faut réimporter ou garder la référence actuelle.

### 5.5 Cas de test à ajouter au plan (`docs/plan_de_tests.md`, section « Récolte (R) »)

Indicateurs proposés :

| Indicateur | Définition | Seuil de validation |
|---|---|---|
| Repérage | Ressources trouvées / visibles à l'œil sur la carte | ≥ 90 % ; 0 arbre debout faux |
| Récolte réussie | Menu, action, message ITEM_FOUND / tentatives sur une ressource debout | ≥ 95 % |
| Temps par récolte | Clic jusqu'à ITEM_FOUND, selon le niveau du métier | À mesurer (12 s − 0,1 s par niveau attendu) |
| Rendement | Ressources par heure et par personnage, sur un circuit | À mesurer (référence pour la suite) |
| Gardiens | Gagnés / rencontrés | 100 %, reprise de la récolte sans intervention |

| ID | Objectif | Départ et commande | Répétitions | Réussi si |
|---|---|---|---|---|
| R0 | Non-régression hors jeu | `python -m tools.ressources_essai --essai --une-par-carte --bas --seuils 0.6 --seuil-souche 0.7` | À chaque changement du repérage | Frêne ≥ 68/89, Châtaignier ≥ 29/40, aucun arbre debout faux sur les planches |
| R1 | Repérage en jeu | Cartes 4,-16, 2,-23, 1,-27, 1,-26 : repérage seul, capture annotée | 2 passages par carte | Toutes les ressources visibles trouvées, sur la bonne case et dans le bon état |
| R2 | Menu de récolte | Clic sur une ressource debout, en arrière-plan | 20 | Menu détecté et « Couper » cliqué ≥ 19/20 |
| R3 | Récolte complète | Frêne de 4,-16 puis forêt : clic, action, fin | 10 | ITEM_FOUND lu, souche vue sur la même case, durée notée |
| R4 | Ressource déjà prise | Souche, ou arbre coupé par un autre pendant la marche | Quand ça arrive (fréquent en forêt) | Pas de blocage, case ignorée, ressource suivante |
| R5 | Erreurs | Sans outil, sans le métier, niveau trop bas, inventaire plein | 1 de chaque | Message reconnu, arrêt propre ou ressource ignorée, jamais de boucle |
| R6 | Gardien | Récolte en série jusqu'à un protecteur (1/40 environ) | Dès qu'il apparaît | Combat joué ; équipe ralliée si c'est possible (noter si ça ne l'est pas) ; reprise seule |
| R7 | Repousse | Noter l'heure de chaque passage souche → debout | Sur toute une session | Durée par ressource notée dans la mémoire |
| R8 | Équipe | 4 personnages sur une carte de forêt | 3 cartes | Aucune ressource visée par deux personnages ; rendement noté |
| R9 | Circuit | Forêt d'Astrub, `--minutes 30` en mode récolte | 1 | 0 intervention, rendement noté |
| R10 | Pods et banque | Récolte jusqu'à 90 % de pods, banque, retour (avec B9) | 2 cycles | Retour au circuit, rien perdu (pas d'INFOS_144) |
| R11 | Fenêtre inconnue | Mini-jeu, invitation, défi pendant une récolte | Quand ça arrive | Arrêt et capture pour le mini-jeu, refus pour le reste |
| R12 | Céréales | Après les gabarits du Blé (capture n° 5) : champ 5,-26 | 2 | Comme R1 et R3 |

---

## 6. Fichiers produits

- `tools/ressources_essai.py` :
  - tables des métiers, outils et protecteurs du client (`--metiers`) ;
  - index des captures ;
  - construction des gabarits ;
  - repérage ancré sur les cases ;
  - bilans.
- `tests/test_ressources_essai.py` : 19 tests sur images synthétiques et fausse langue. Aucun accès au jeu ni à `logs/`.
- `data/ressources_essai/` (non suivi par git) :
  - `index.json` : capture → carte → ressources attendues ;
  - `gabarits.json` : exemplaires de chaque gabarit ;
  - `gabarits/*.png` : médiane et masque ;
  - `resultats*.json` : détections et bilans ;
  - `planches/*.jpg` : vignettes à vérifier à l'œil, une planche par capture.

## Sources web citées

- JOL Retro :
  - https://dofusretro.jeuxonline.info/article/14773/introduction-aux-metiers ;
  - https://dofusretro.jeuxonline.info/article/14776/metier-bucheron ;
  - https://dofusretro.jeuxonline.info/article/14778/metier-mineur ;
  - https://dofusretro.jeuxonline.info/article/14761/chat-textuel-commandes.
- DofuX (1.29) : https://www.dofux.org/articles-166-les-metiers.dx ;
  https://www.dofux.org/articles-75-Bucheron-Monter-au-niveau-100.dx?page=articles&articles=75.
- Millenium : https://www.millenium.org/news/357321.html (Maj + clic, 1.31) ; https://www.millenium.org/guide/345477.html.
- Tweets officiels @DOFUSRetro_FR, lus par api.fxtwitter.com : statuts 1930217970518856007 (refonte annoncée),
  1939708568216748328 (système retiré de la 1.47), 1970775222304256297 (1.47 en ligne), 2039702659372839189 (1.48),
  2089638238633800040 (1.49).
- xixou.io : https://xixou.io/changelog/?xixpn_tab=retro&xixpn_page=1 et page 2 ; https://xixou.io/cgu-site/ ;
  https://xixou.io/encyclopedie/monstres/craqueboule-de-fer/.
- Forum Retro (extraits de recherche, pages refusées en 403) : surbrillance au survol
  (dofus.com/fr/forum/1787-dofus-retro/2342814) ; bug 1.33.6 du protecteur (dofus.com/fr/forum/1785-dofus-retro/2344974).
- solomonk : https://solomonk.fr/fr/monstres/ecosysteme/15/protecteurs-des-ressources.
- Sources de données : https://www.dofus-retro.co/en/ ; https://wiki.moon-bot.io/api/ ;
  https://github.com/Romain-P/Ancestra-Evolutive ; https://github.com/Arakne/php-map-parser.
- Pages officielles dofus-retro.com et dofus.com : refusées (erreur 403). Le contenu officiel n'a été lu
  qu'indirectement, par les tweets, les recopies de xixou et des extraits de recherche.
