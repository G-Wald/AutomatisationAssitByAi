# Écran → réseau : ce qui peut encore passer à l'écoute du serveur (31/10)

Décision de l'utilisateur (31/10) : prendre l'information **uniquement via le réseau**, plus fiable. L'écran ne sert
qu'à cliquer au bon endroit, ou en secours quand le réseau n'a pas l'information (bot relancé en plein combat, écoute
pas encore active).

Analyse en lecture seule du code et des journaux du 31/10 (agent). Déjà fait le 31/10 : fin du combat d'après le
serveur (`TeamBot._fight_told`, `bot/team/loop.py`), receveur d'un échange d'après ERK, sac de connexion écrit dès ASK.

## Où l'écran passe encore avant le réseau

- `team/loop.py` `_turns` : le réseau n'est consulté que si l'écran dit « hors combat ».
- `team/loop.py` `_turn_marks` et `brain/combat.py` `_my_turn` : il faut d'abord que l'écran dise « combat ».
- `brain/combat.py` `_team_from_net` et `team/loop.py` `_circle_colour` : la couleur de l'équipe vient toujours du cercle vu à l'écran.
- `brain/travel.py` `look()` : l'OCR du nom de carte tourne même quand GDM a déjà donné la carte.
- `brain/engage.py` `_engage_by_net` : la phase est revérifiée à l'écran.

## Inventaire priorisé

| Prio | Décision encore à l'écran | Message serveur (lu ?) | Gain / risque | Effort |
|---|---|---|---|---|
| 1 | Phase hors combat / placement / combat : `state/scene.py` `read_scene`, 57 appels | GJK, GTS/GTF, GE/GCK/GDM : oui. GS : non lu | Plus de K.O. pris pour une fin de combat, beaucoup moins de captures ; écran en secours si aucun combat entendu | moyen |
| 1 | Main passée après F1 : `combat.py` `_turn_passed` (captures toutes les 0,25 s) | GTF de mon id / GTS d'un autre : oui | « F1 n'a pas passé » ×4 le 31/10 | petit |
| 1 | Carte officielle du combat (obstacles, ligne de vue) : `combat.py` `_recognize_arena`, `loop.py` `_walk_area` | GDM : oui (numéro = clé de `data/references/combat_*.json`) | Exacte dès le 1er tour ; garder le contrôle du placement (cartes changées) | petit |
| 1 | Suiveur sur la même carte, arrivé ou perdu : `loop.py` `_same_place` et ses appels, `join.py` `_away` | Numéro de carte de chaque fenêtre (GDM) ; acteurs GM de la fenêtre du principal | Plus de décor ni d'OCR (27 % d'arrivées non vérifiées au bilan du 12/10) | petit à moyen |
| 2 | Placement : `loop.py` `read_placement`, `_team_colour` ; `combat.py` `place()` | GP : cases des deux camps et mon équipe (seule l'équipe est lue) | Plus de lecture des couleurs ; à vérifier : équipe 0 = rouge | moyen |
| 2 | « Prêt » pris en compte : `loop.py` `_ready` | GR1<id> : non lu | Vérifier Prêt, cliquer seulement s'il manque | petit |
| 2 | Couleur de l'équipe : `loop.py` `_player`, `combat.py` `_team_from_net` | Équipe de chaque combattant : oui (`my_team()`) | Raisonner en équipe 0/1 (incident du 26/10) | moyen |
| 2 | PM au début et en cours de tour, PA et ennemis debout en rafale : `combat.py` `_pm_left`, `_burst_stop` | GTM, GA;129/127, GA;102/101, GA;103 : oui | Plus de « PM illisibles » ni de morts vues sous l'animation | petit |
| 2 | Mort au combat : `loop.py` `_died` (OCR du chat) | Énergie de « As » (lue, jamais utilisée) ; GA;103 de mon id | Plus d'OCR ; à vérifier : As renvoyé après le combat | petit |
| 2 | PNJ du donjon : `team/dungeon.py` (bulle vue, réponse lue à l'OCR, décor changé) ; salles comptées par les combats | DCK : oui ; DQ : ids des réponses non lus ; GDM : salle = numéro de carte (Bouftou 2073 à 2084) | « Ni menu ni bulle » ×16 ; salle jamais comptée deux fois | petit à moyen |
| 2 | Entrée effective dans le combat : `join.py` `wait_placement` | GJK par fenêtre : oui (`fight_since`) | « N'a pas rejoint le combat » ×5 le 31/10 | petit |
| 3 | Arrivée au zaap (`zaap.py`, décor), mur dans un trajet (`travel.py`) | GDM : oui ; GA0;1 donne mon chemin | « Zaap raté » ×2 | petit |
| 3 | Vie pendant le repos : `loop.py`, `brain/rest.py` | As : oui, mais probablement pas renvoyé pendant la régénération assise ; ILS/ILF à vérifier | Garder l'écran en secours | petit à moyen |
| 3 | Mesure des dégâts : `combat.py` `_read_life` (fiche au survol) | GA;100 : dégâts exacts (lu, seule la vie cumulée est gardée) | Plus de survol | moyen |
| 3 | Sorts, classe, niveau : `spellbook.py`, liste d'amis (OCR), popup « Tu passes niveau » | Classe : champ 5 de GM, sûr ; niveau et sorts (SL) : à vérifier | Démarrage plus rapide | moyen |
| 3 | Bon personnage après la connexion : `connection/login.py` (plaque à l'OCR) | ASK\|id\|nom : oui | Confirmation exacte | petit |
| 4 | Banquier sur la carte, nuit (teinte des infobulles) | PNJ dans GM par modèle ; heure BT : à vérifier | Gain faible | — |

## Ordre de bascule proposé

1. Une fonction unique `phase(member)` d'après le réseau (GJK/GS/GE/GDM), avec l'écran en secours seulement sans
   combat entendu ou flux plus vivant ; branchée d'abord dans `_turns`, `_turn_marks`, `_my_turn`, `_turn_passed`.
2. Les gains rapides : carte du combat par numéro, PM/PA/rafale, mort par l'énergie, entrée par GJK, zaap par GDM.
3. GP et GR (placement et Prêt), puis l'abandon de la couleur au profit des équipes 0/1 (risque de tir entre alliés
   connu : à tester sur un combat court).
4. « Même carte » par numéro et donjon (DCK/DQ, salle = carte).
5. À vérifier en jeu avant de s'y fier : SL, ALK, Im, ILS, BT, équipe 0 = rouge, As renvoyé après un combat.

## Fait le 31/10 au soir (après le test rapide)

- Phase du jeu : `read_scene` la prend au serveur (`ClientFeed.phase`) dès que l'écoute la connaît ; l'écran ne donne
  plus que les boutons, l'équipe qui joue et le timer. Les 57 appels en profitent sans changement. Combat noté même
  sans « GJK » entendu (premier message de placement ou de tour). Validé sur un combat : 7/7 rejoints, fin donnée par
  le serveur 0,7 s avant l'écran.
- Position « /p %pos% » lue au réseau (`cMK$` de mon personnage seulement) ; coordonnées des cartes absentes de la
  référence d'après la langue du client (cartes extérieures du continent seulement).
- Échanges : miniature visée d'après l'initiative (`PM`), refus immédiat entre deux cartes.

- Placement : cases des deux équipes d'après `GP` (16/16 dans la référence, deux combats). Couleur d'équipe : l'équipe
  0 du serveur supposée rouge, comparée à l'écran à chaque combat (accord aux deux combats du 31/10) ; le serveur ne
  tranche que si l'écran ne peut pas et après un accord vu au même lancement ; un désaccord le désactive.
- Vie des cibles d'après le serveur (début de tour, chaque coup, mort) ; en arrière-plan, sans survol, les dégâts de
  chaque sort se mesurent de nouveau (« 17 dégâts (32 -> 15 PV) »).
- Bon personnage après la connexion : déjà sûr (titre de la fenêtre, donné par le jeu, pas lu à l'image).

Reste : classe/niveau/sorts au démarrage (`--lecture memoire` l'évite déjà), vie au repos et liste des zaaps (à
observer d'abord), cadre du groupe jugé aux pixels rouges, placement en solo (`combat.py` `place`).

## Revue critique du 31/10 (soir) : réseau et imprévus après un clic

### Écran → réseau, à faire
- P1 Clic pris ou non : pas de réponse du serveur en ~1 s (GA0;1, DCK, GJK, ECK, liste de zaaps) = clic perdu ou menu ouvert ; aujourd'hui immobilité à l'écran (`travel.py`).
- P1 Carte = mapData (clé GDM) : ligne de vue, cases praticables, soleils ; remplace `_recognize_arena`, `_walk_area`, la touche A.
- P1 Même carte partout par `map_id` : `_same_place`, `join._away`, banque (intérieur jugé au sombre), `dungeon._changed`, arrivée du zaap.
- P1 Rejoindre un combat : GJK de la fenêtre ou refus GA;903 juste après le clic (cas Walitcoin) ; `wait_placement` regarde l'écran.
- P1 Main passée : GTF / GTS (`_turn_passed`).
- P2 Prêt (GR), début (GS), mort (GA;103 de mon id), PM (GTM, GA;129), liste des zaaps (WC/WV, à vérifier), banquier et coffre (GM, ECK).
- Surestimé au premier rapport : « équipe 0 = rouge » inutile (même équipe que mon id) ; mort par As (GA;103 suffit) ; zaap en P3 (P1 depuis le 31/10).

### Imprévus après un clic
- Soleil : menu de Flash pas refermé quand `close_context_menu` renvoie False, Échap pressé à la place (Menu principal).
- Zaap : ne distingue pas rien / Flash / menu d'un joueur ; Échap en dernier recours ; pas de message à l'utilisateur.
- PNJ du donjon : clic sans touche E ; `_talk_entry` clique le milieu de n'importe quel petit menu, y compris celui d'un autre joueur.
- Rejoindre : aucune preuve après « Rejoindre ».
- Échange : `cancel()` sans vérifier EV ; cadre du groupe jugé aux pixels rouges.
- Banque : clic du banquier sans touche E ; « Transférer tous les objets » jamais prouvé ; échecs sans message.
- Recherche du sac : focus du champ jamais vérifié (sinon le texte tapé devient des raccourcis).
- Double-clic d'équipement : un consommable en 1re case serait utilisé avant le contrôle.
- `tell_user` seulement pour les clés, l'entrée du donjon et le personnage resté derrière.

### Priorités
1. Une seule fermeture de menu sûre (Flash, menu d'un joueur, fenêtres connues), à la place des « sinon Échap ».
2. Touche E sur tous les clics de PNJ et du banquier ; plus de « milieu du menu ».
3. « Clic pris » au réseau en 1 s, puis autre point de clic.
4. `map_id` partout ; GJK et GA;903 pour rejoindre ; GTF pour la main passée.
5. `tell_user` sur chaque abandon ; puis la carte complète par mapData.
