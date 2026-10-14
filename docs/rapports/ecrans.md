# Faire marcher le bot sur n'importe quel écran (08/10)

Analyse en lecture seule du code (aucun fichier modifié, jeu non touché). Mesures faites en redimensionnant 28
captures de référence aux échelles 0,5 / 0,64 / 0,82 / 1,07 / 1,34 / 2,0, puis en lançant les perceptions actuelles :
- « telles quelles » : la capture redimensionnée est lue directement (comme aujourd'hui) ;
- « ramenées » : la capture est d'abord remise à la taille de référence.

## Résumé

- **Le vrai blocage n'est pas l'échelle, c'est la position de la scène Flash dans la fenêtre.** `Layout` suppose une
  scène 4:3 calée sur la hauteur et collée à droite, le chat prenant le reste. Rien ne le prouve ailleurs que sur
  l'écran 1080p : les 81 captures complètes font toutes de 1916 à 1920 x 1005 à 1010.
- **Recommandation** : trouver la scène sur chaque capture, puis ramener chaque capture à l'échelle de référence.
  Garder le code déjà adapté à l'échelle, qui ne fera plus rien à l'échelle 1.
- **Aujourd'hui, seule la configuration « fenêtre maximisée sur un écran 1080p à 100 % » est validée.**

## 1. Ce qui dépend de la taille de l'écran

### Priorité 0 : le modèle de disposition (tout en dépend)

- **`layout.py:30-38`** : échelle = hauteur / 1008, x mesurés depuis le bord droit. C'est faux dans trois cas :
  - la scène a des bandes noires (fenêtre plus étroite que 4:3 + chat) ;
  - le séparateur du chat a été déplacé ;
  - le chat a une largeur fixe en pixels CSS : à 175 %, il vaut 1,75 fois plus de pixels réels.
- **`frame.py:19`, `game.py`** : `Frame.of(capture)` prend toute la zone de la fenêtre pour la scène. C'est le point
  de passage unique, donc le bon endroit pour corriger.
- **`team/member.py:39-41`** : `to_reference` réécrit l'inverse de `Layout`. Ce doublon est fragile.
- **`ui.py`** : `PARK_ZONES` range la souris dans le chat, ce qui suppose que le chat existe.
- **Séparateur du chat mesuré** : 6 px, couleur BGR (104,131,140), en x 565-570. La scène commence en x 571, le bord
  haut de l'interface est en y 785.
- **Écart déjà présent sur une capture recadrée à la main** : sur `combat_mon_tour`, la scène commence en x 570
  alors que `Layout` prévoit 574,7 (4,7 px d'erreur).

### Priorité 1 : ce qui casse (mesuré, captures lues telles quelles)

| Perception | Où | Résultat |
|---|---|---|
| Nom de carte | `maplabel.py` (taille à ±2 px absolus, ombre 1-2 px, chiffres appris à l'échelle du moment) | 0 à 1 lecture sur 11 à toutes les échelles |
| Bouton créature (boule) | `toolbar.py` `MAX_GAP=25`, `MIN_CHECK_PIXELS=6` | 5/13 à 0,5 |
| PA | `ui.py` (≥ 4 px, décalage ±2 px) | 3/8 à 0,64 |
| Infobulles des groupes | `groups.py` | groupes trouvés, mais nombre de monstres faux à ≤ 0,64 |
| Fenêtre des sorts | `spellbook.py` seuil `learned` (contraste > 60) | une ligne grisée lue « apprise » à 1,07 et 1,34 |
| Portrait (mode équipe) | `timeline.py` `SAME_PORTRAIT=10` | identification ratée si les fenêtres n'ont pas toutes la même taille |
| Combattants | `fighters.py` (dilatation et fermeture fixes) | 8/9 à x2 |

Performance : à x2 (4K), une lecture complète prend 274 ms, contre 72 ms à x1. Ramener l'image coûte 4 à 5 ms.

### Priorité 2 : seuils en pixels fixes (passent entre 0,8 et 2, à surveiller)

- **Contours des soleils** : `exits.py` (contour de la touche A de 3 px, perdu sous 0,6).
- **Épée** : `join.py` (érosion fixe).
- **Fenêtres** : `popups.py` (fermeture de 3 px).
- **Modèles agrandis** : `match_template` agrandit les modèles pris à x1, ce qui les floute.

### Ce qui tient à toutes les échelles testées

- Lecture de la scène (placement, combat, hors combat), cases de placement, mode tactique.
- Soleils (dès 0,64), fenêtres connues, boutons des sorts et des caractéristiques, grille.
- Les fenêtres des sorts et des caractéristiques sont déjà remises à la taille de référence localement : c'est déjà la
  stratégie recommandée, en petit.

### DPI (mise à l'échelle Windows)

- **Ce qui est déjà bon** : le bot travaille en pixels physiques (captures, clics, PrintWindow, messages en
  arrière-plan).
- **Ce que le DPI change** :
  - la largeur du chat en pixels réels, donc la position de la scène ;
  - le rendu du Flash (net, ou image agrandie) : on ne sait pas encore ;
  - une fenêtre passée d'un écran à l'autre en pleine session.
- **Bonne nouvelle** : sur ton 4K à 175 %, fenêtre maximisée, la zone de jeu fait environ 3840 x 2020. Ce sont les
  mêmes proportions que la référence, soit une échelle d'environ 2, si le chat suit le modèle.

## 2. Deux stratégies

**Les deux ont besoin de détecter la scène.**

- **(a) Ramener chaque capture à la référence**, puis convertir les clics vers la fenêtre.
  - Pour :
    - une seule transformation ; constantes et modèles inchangés ; journaux comparables ;
    - mesuré : nom de carte 11/11 à x2, boutons 12-13/13, PA 7-8/8, combattants 9/9.
  - Contre :
    - une petite fenêtre agrandie ne récupère pas l'information perdue ;
    - toutes les sorties de coordonnées doivent être converties.
- **(b) Rendre chaque perception consciente de l'échelle.**
  - Pour : garde toute la finesse sur grand écran.
  - Contre :
    - plus de 20 constantes à revoir ;
    - 3,8 fois plus lent en 4K ;
    - tests à multiplier par chaque échelle.

**Taille minimale** : le texte est fiable à partir d'une scène d'environ 800 px de haut (échelle 0,8). Entre 0,64 et
0,8 la lecture se dégrade ; en dessous, il faut refuser.

## 3. Plan proposé (3 à 4 jours)

| Étape | Contenu | Effort |
|---|---|---|
| 0 | Tes captures (liste ci-dessous) | — |
| 1 | `perception/stage.py` : trouver la scène (séparateur du chat, bord droit, ligne haute de l'interface, bandes noires), vérifiée par des repères, gardée en cache par taille de fenêtre | 0,5-1 j |
| 2 | Ramener chaque capture à la référence dans `Game._grab` ; une conversion référence ↔ fenêtre partagée par les deux façons de jouer (premier plan, arrière-plan) | 1 j |
| 3 | Corrections restantes (tolérances relatives, portraits à ±2 px, taille minimale avec avertissement) | 0,5 j |
| 4 | DPI : journaliser au démarrage taille, DPI et scène de chaque fenêtre ; recalibrer si la taille change | 0,25 j |
| 5 | Tests de non-régression à plusieurs échelles, puis tes vraies captures | 0,5-1 j |

## 4. Captures à faire (par toi)

- **A** (priorité) : 4K à 175 %, fenêtre maximisée.
- **B** : 4K à 175 %, fenêtre à environ la moitié de l'écran.
- **C** : écran 1080p à 100 %, fenêtre en 1600 x 900 puis en 1280 x 720.
- **D** : 1080p, fenêtre presque carrée (~1000 x 1000) et en 1280 x 1024.
- **E** : 1080p, séparateur du chat déplacé (si le jeu le permet).
- **F** (facultatif) : 1080p à 125 %.
- **G** : plein écran du jeu, s'il existe.

Pour A, B et C, prends ces 7 états :
1. hors combat ;
2. Z tenu ;
3. placement en mode tactique ;
4. mon tour avec un sort sélectionné ;
5. fenêtre des sorts ;
6. caractéristiques (simple et avancées) ;
7. résultat du combat.

Pour D, E, F et G, un hors combat et un combat suffisent. Note chaque fois la taille de la fenêtre et la mise à
l'échelle Windows.

## 5. Corrections rapides possibles tout de suite

1. **Au démarrage** : journaliser le DPI de chaque fenêtre et l'écart entre la scène détectée et celle prévue, avec un
   avertissement au-delà de 3 px.
2. **Nom de carte** : tolérance relative sur la taille des lettres.
3. **Fenêtre des sorts** : seuil « appris » relatif.
4. **Portraits** : comparaison avec un décalage de ±2 px.
5. **Boule du mode créature** : écart maximal propre à ce bouton.
6. **Hors de la configuration validée** : le bot doit le dire clairement.
