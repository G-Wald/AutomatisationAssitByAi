# Branche `claude/fiabilite-trajets-combats` : 1 h de travail autonome, 23/10 (20:23 → 21:23)

Objectif fixé par l'utilisateur : des trajets et des combats solides, sans erreur ; combats testés dans la Plaine des
Porkass ; attention aux monstres et aux zones qui agressent.

## Ce qui a été testé en jeu

| Étape | Résultat |
|---|---|
| Cité d'Astrub (Pious), 4 combats | repos à 100 % avant d'attaquer ; Pesopepe ne rejoint jamais (voir 3) |
| Trajet Astrub 2,-20 → Porkass -3,-29 | 16 cartes en 2 min, **112 passages de suiveurs sur 112**, 1 reclic, 0 perdu |
| Plaine des Porkass, 11 combats (2 sessions) | **0 mort**, 0 blocage, 0 erreur ; 1 suiveur hors d'un combat ; rejoindre 3,5 s, prêts 2,5 s |
| Trajets dans la plaine (24 cartes) | 14 suiveurs déclarés perdus à 20 s alors qu'ils marchaient encore (grandes cartes) : correctif 7, pas encore vu en jeu |
| 2ᵉ sort sur la même cible (Porkass) | 31 partis sur 31 |
| Avec la portée affichée (après le correctif 5) | refus de sorts : de 15 % à ~2 % ; 3 clics évités en 2 combats |

## Correctifs (un commit chacun)

1. **Référence des passages** (`config/monde/passages/*.json`, `tools/passages.py`) : les cases des cartes du client
   sont chiffrées (clé envoyée par le serveur à l'entrée sur la carte), on ne sait par où l'on passe qu'en y étant
   allé. La référence garde les passages vus en jeu (479 dans le Monde des Douze, 128 à Incarnam), à part de la
   mémoire qu'une lecture ratée peut fausser.
2. **Itinéraire pondéré** : passage confirmé 1, jamais pris 2, entrée dans une autre zone que celles du départ et de
   l'arrivée +2. Le trajet Calanques → Porkass ne passe plus par Le berceau (plateau fermé, impasse du 23/10), sans
   l'interdire (on ira y farmer).
3. **Suiveur hors du groupe du principal** : « Impossible de rejoindre le combat car l'équipe est fermée » lu dans le
   chat ; le bot le dit une fois et ne lui fait plus chercher l'épée 25 s à chaque combat.
4. **Épée** : un point raté (menu de Flash) n'est plus proposé aux suiveurs suivants (4 suiveurs l'avaient repris).
5. **Pas de clic sur une cible hors de vue** d'après la portée affichée par le jeu (carte à relief, prévision juste à
   56 %) : la case de la cible, ou ses voisines de mon côté, en bleu clair.
6. **Niveau des groupes** : titre « Niveau N » de l'infobulle lu par l'OCR ; `engage.max_level` (70) écarte les groupes
   trop forts (un groupe de niveau 75 laissé aux Porkass).
7. **Attente des suiveurs** prolongée tant que l'un d'eux marche encore (jusqu'à 2 fois `travel.walk_timeout`).

Avant l'heure (même branche, premier commit) : tout ce qui n'était pas commité sur main depuis le 21/10 (frise déplacée
quand elle cache le combat, regroupement au départ, /p des suiveurs, sorties sur herbe claire, menu de personnage sur
le soleil, etc.).

## Ce que j'ai remarqué

- **Les zones qui agressent ne sont dans aucune donnée** : l'en-tête des cartes du client a un drapeau « agression
  possible », vrai presque partout ; les références n'ont que le nom et le niveau des monstres. Les Bouftous d'Halouine
  (événement d'Halloween, absents des références) ont tué l'équipe le 23/10 dans Le berceau.
- **Groupe** : après une déconnexion du principal, un autre devient chef ; à la reconnexion, le bot n'invite que ceux
  qui n'ont aucun groupe, Pesopepe est resté dans l'ancien (réglé ensuite par la reconnexion suivante).
- **Plaine des Porkass** : faisable pour l'équipe avec `max_level` 70 ; tours de 4,2 s en moyenne.
- **Pods** : Lestunes à 100 % en fin de session (« il faut passer à la banque ») : la banque devient la prochaine limite.

## Ce que je propose ensuite

1. **Liste des monstres agressifs** (à remplir avec toi, ou depuis une source Retro que tu valides) : zones à éviter en
   trajet quand la vie est basse, repos avant de traverser.
2. **Groupe** : vérifier que le cadre du groupe contient le principal (pas seulement « un groupe ») ; sinon quitter
   l'ancien groupe et accepter l'invitation.
3. **Référence des passages** : la mettre à jour après chaque session (`tools/passages.py --exporter`), et lancer une
   exploration (`--explore`) des zones de farm pour la compléter.
4. **Frise déplacée** : à voir en jeu (pas encore eu de combat dans un coin).
