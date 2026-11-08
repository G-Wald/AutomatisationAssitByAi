# Donjon des Blops : XP avec Walarbalete en DPS et les 7 autres en soutien (08/11)

Demande de l'utilisateur (08/11, 15:07) : analyser les personnages en jeu, lancer une session d'XP de 2 h en donjon,
« l'idéal serait Blop + Kani », Walarbalete est le DPS ; puis « plus donjon Blop que Canidés ».

## Les personnages en jeu

| Personnage | Classe | Niveau | Remarque |
|---|---|---|---|
| Walarbalete | Crâ feu | 108 | DPS ; 741 PV, 11 PA, 5 PM, portée +3, Intelligence 366 ; Flèche Explosive niv. 5 (2 par tour), Enflammée et Magique niv. 6, Tir Puissant et Maîtrise de l'Arc niv. 5 |
| Eldieso | Crâ | 42 | principal (attaque, chemin) |
| Elseiso | Crâ | 50 | |
| Waldarch-[Gal] | Crâ | 52 | Flèche Cinglante (-2 PM sûrs) |
| Enu-six-[Gal] | Enutrof | 44 | |
| Walitcoin, Pesopepe | Enutrof | 36 | |
| Fourboun | Sadida | 46 | revenu à la place de Waldhary |

Walarbalete était connecté avant le bot : le serveur n'envoie le sac qu'à la connexion. Il a été reconnecté
(fenêtre fermée, bot relancé avec `--connexion --personnages`) pour connaître ses clés.

## Le donjon (client, StarLoco local, recherche web Retro)

- Entrée : carte 4416 en -25,-17 (Massif de Cania) ; la case 355 (14,24) téléporte dans 11878, où Bibiblop (PNJ 1034)
  prend la clef (« Donner la clef », 4531) ou le trousseau. **Vu en jeu.**
- Salles 11879 à 11885 : 7 combats, la victoire téléporte dans la suivante. **Vu en jeu.**
- 11886 : Bibiblop, « Choisir la salle du Blop Coco / Griotte / Indigo / Reinette Royal ». Le bot choisit l'Indigo
  (résistance feu -20 à -12 %, Griotte 90 %). **Vu en jeu.**
- Après le royal : 11891, Bibiblop, « Sortir du donjon » ramène dans **11878, devant Bibiblop** (pas dehors) : le
  passage suivant entre aussitôt. L'antre du Blop Multicolore (clef de l'antre) n'est jamais choisi (utilisateur : trop
  haut pour l'équipe).
- Danger : le **Gloutoblop** avale un personnage au contact quels que soient ses PV ; 1 PA de moins l'en empêche.
- Canidés (Kanigrous) : niveau 130, plusieurs groupes de 8 par salle, salles traversées à pied : pas fait.

## Ajouts au bot (commits 2940905, 56f494e et suivant)

- **Donjon** (`config/donjons_entree.yaml`, `blops`) : `carte_dehors`, `entree` sur la carte de dehors ; salle du
  choix (`choix_apres`, `choix_pnj`, `choix_reponse`) ; reprise `--donjon blops:choix` ; une salle sans place de
  combat n'est pas faite en vagues ; `--position` sert quand ni le serveur ni l'écran ne donnent la carte.
- **Pas de limite de niveau dans les salles** : le groupe de la salle 5 (325) était laissé (max 293), 10 min perdues.
- **Mode soutien** (demande de l'utilisateur) : quand `team.dps` est défini, les autres
  - lancent leurs bonus de dégâts sur le DPS (Tir Puissant +150 %, Maîtrise de l'Arc) ; un bonus de caractéristique
    seulement s'il sert à l'élément du DPS (Chance, eau, jamais sur Walarbalete, feu) ;
  - retirent PM et PA aux ennemis, d'autant plus aux dangereux (Gloutoblop x3). Utilisateur : « les retraits
    c'est en fonction de la sagesse, alors que Flèche Cinglante retire des PM obligatoirement » : -PA (168) et -PM
    (169) sûrs, comptés en entier ; PA/PM perdus (101, 127) et vols (84, 77) esquivables, comptés à moitié
    (estimation, pas la formule sagesse/esquive).
- **Monstres dangereux** : personne, DPS compris, ne finit son tour à 6 cases ou moins d'un Gloutoblop (salle 6 :
  Walarbalete avancé de 5 PM pour taper, mort au tour des monstres, puis 14 min de repos).
- **Lecture des bulles** : l et I confondus par l'OCR (« BIOP ») : les 8 restaient devant Bibiblop.

## Session (15:19 à ~17:20)

- Passage 1 : entré à 15:33, sorti à 16:41 (dont 10 min bloqué en salle 5, 14 min de repos de Walarbalete, deux
  relances). Morts dans des combats gagnés : Walitcoin (salle 3), Waldarch (salle 4), Walarbalete et Eldieso
  (salle 6, avant le correctif du Gloutoblop). Salle 7 et Blop Indigo Royal : aucun mort.
- XP : 170 262 par personnage pour la salle 7 seule (chat d'Eldieso).
- Mode soutien vu en jeu : Tir Puissant sur Walarbalete (Elseiso, Waldarch, Eldieso), Flèche Cinglante et Flèche
  Glacée sur les Gloutoblops.

## Reste à faire

- Le libellé du journal « l'ennemi en … » pour un bonus lancé sur un allié (le premier message est corrigé, pas tous).
- Le sac d'un personnage repris d'un fichier peut être faux (Enu-six : 4 clés reçues, comptées 0) : à marquer
  inconnu dès qu'un objet du serveur n'y est pas.
- La lecture des coordonnées à l'écran a échoué en -25,-17 (« Coordonnées : -25, -17 » visible).
