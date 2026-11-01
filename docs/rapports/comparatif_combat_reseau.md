# Comparatif : version commitée contre branche `claude/combat-reseau` (01/11)

Même endroit (forêt d'Astrub, 2,-27, Prespic compris), même équipe de 8, 9 combats chacune, en arrière-plan.
Sessions : `logs/20261101_001258` (version commitée), `logs/20261101_003426` (branche). Outil :
`python -m tools.comparatif_sessions "version commitée=logs/20261101_001258" "branche=logs/20261101_003426"`.

| | version commitée | branche |
|---|---|---|
| combats | 9 | 9 |
| durée moyenne d'un combat (s) | 59 | 52 |
| tours joués | 74 | 72 |
| durée moyenne d'un tour (s) | 3.6 | 3.3 |
| sorts par tour | 0.96 | 1.07 |
| refus par tour | 0.23 | 0.06 |
| clics sans effet | 8 | 2 |
| déplacements sans bouger | 3 | 4 |
| pas de ligne de vue lue à l'écran | 3 | 2 |
| ennemi invisible signalé | 0 | 16 |
| morts de mes personnages | 0 | 0 |
| messages à l'utilisateur | 0 | 0 |
| erreurs | 0 | 0 |

## Ce que la branche fait gagner
- Combats plus courts (−12 %) et tours plus courts (−8 %), un peu plus de sorts par tour.
- Refus du jeu divisés par 4 et clics sans effet par 4 : ligne de vue calculée sur la carte du client, ennemis
  debout et PA d'après le serveur.
- Prespic invisible : dit, pas de case fantôme visée (avant le correctif du 31/10, une case hors de la carte).

## Limites de la mesure
- 9 combats par version : les groupes de monstres ne sont pas les mêmes ; écarts à confirmer sur une session longue.
- Version commitée : son premier combat a été repris en plein combat (je l'avais arrêté par erreur) ; d'où 12
  « 9 cercles de ma couleur, rien ne prouve lequel est moi », cas que la branche règle (numéros des personnages
  retenus). Un aller-retour à la banque a aussi eu lieu pendant cette session (hors des combats mesurés).
- Branche : les 2 « pas de ligne de vue lue à l'écran » viennent d'une carte dont la clé n'était pas connue (repli
  sur l'écran, comme prévu).

## Session longue du 01/11 au matin : arrêtée par la nuit
Lancée pour 30 combats, arrêtée d'elle-même après 2 (06:02) : teinte jour/nuit pas reconnue par 5 personnages, règle de
sécurité du 26/10. Ni mort ni erreur. La comparaison longue reste à faire de jour.

## Lectures d'écran remplacées par le serveur (01/11)
| lecture d'écran | remplacée par | vu en jeu |
|---|---|---|
| fenêtre des sorts, barre (sort, niveau, case) | « SL » à la connexion, retenu ; chaque sort lancé contrôlé par « GA;300 » | 5 personnages avaient appris des sorts depuis la lecture de leur fenêtre (Tir Puissant, Cupidité...) ; le « sort n°4 » de Fourboun est Arbre ; toutes les cases confirmées, aucune fausse |
| niveaux de l'équipe (liste des amis) | « PM » (niveau de chaque membre, principal compris) | retenus après chaque combat |
| vie avant le repos (cœur) | « As » ; assis, le cœur reste lu (le serveur n'envoie que le rythme, « ILS2000 ») | pas de repos pendant les essais |
| obstacles lus à l'écran avec la carte du client | rien à lire | à partir du 2e combat (1er combat : numéro de carte pas encore annoncé) |
| même carte que le principal (décor, nom de carte) | numéro de carte annoncé dans les deux fenêtres, sinon réponse de « /p » | Pesopepe reconnecté, décor plus clair que celui du principal (teinte), reconnu par « /p » |
| nom de la carte en déplacement | gardé : 7 ms, et la carte annoncée fait déjà foi | — |

Corrigé en route :
- Une fenêtre déconnectée pendant la connexion des autres est reconnectée avant de former l'équipe. Pesopepe était resté
  hors de l'équipe.
- Les sorts de la tactique sont pris même quand la barre est déjà connue. Le premier essai avait passé tous les tours :
  0 sort, « Sorts inconnus ».

Essai court de 2 combats après ces changements : 1,8 sort par tour, aucun refus, aucun clic sans effet, ni mort ni erreur.

## Combat indépendant de la teinte de nuit (01/11, utilisateur)
- L'arrêt de nuit ne compte plus que les combats joués sans combattants donnés par le serveur. Avant l'aube, les 8 ne
  reconnaissaient pas le sol tactique, mais les combats se jouaient bien : 1,8 sort par tour et aucun refus.
- Avec l'écoute du serveur, la nuit n'empêche plus de lancer un combat. La touche Z n'est plus pressée à chaque carte
  pour lire la teinte des infobulles.
- Pas encore vu en jeu de nuit : il faisait jour au moment de l'essai suivant.

Session de 6 combats (logs/20261101_071237, de jour, carte 1,-28) :

| | version commitée | branche (31/10) | branche + serveur (01/11) |
|---|---|---|---|
| combats | 9 | 9 | 6 |
| durée moyenne d'un combat (s) | 59 | 52 | 45 |
| sorts par tour | 0.96 | 1.07 | 1.42 |
| refus par tour | 0.23 | 0.06 | 0.02 |
| clics sans effet | 8 | 2 | 1 |
| déplacements sans bouger | 3 | 4 | 8 |
| morts / erreurs | 0 / 0 | 0 / 0 | 0 / 0 |

Cupidité (Enutrof) donne +100 Force et Intelligence à tous les combattants autour, un monstre compris (« GA;118 » sur
l'ennemi -1). L'utilisateur la garde en début de tour, avant de taper, même si elle renforce l'ennemi.

### Déplacements sans bouger : trois causes (01/11)
Les 11 déplacements de cette session dont le premier clic est resté sans effet, revus un par un. Le serveur n'a annoncé
aucun déplacement pour ces clics : le client ne les a pas acceptés.

| cause | déplacements | correction |
|---|---|---|
| arbre pris pour une case praticable | 5 sur 5 : (11, 21) deux fois en 1,-28, (18, 24) deux fois en 0,-28, et (9, 21), dont le seul chemin de 3 PM passe par l'arbre de (11, 23) | dans la carte du client, une case sans ligne de vue n'est jamais praticable. Sur 105 cartes, les 169 cases « praticables sans ligne de vue » sont toutes des arbres (objet interactif, déplacement 1). Le mode tactique les dessine en blocs. |
| clic sur le dessin surélevé d'un bloc | (19, 23) : le bord gauche de la case est caché par le bloc de (18, 24) | les points de clic évitent le dessin des blocs juste en dessous de la case visée : dessus 0,95 demi-hauteur plus haut que la case, côtés jusqu'à elle |
| clic pendant l'animation du dernier sort | 4 cas où le second clic, au même endroit, a marché | l'attente est comptée depuis la réponse du serveur (1 s), et non plus 0,7 s depuis le clic du sort. Un sort après lequel un premier clic est ignoré attend 0,4 s de plus pour toute l'équipe, jusqu'à la fin de la session (2,5 s au plus). |
| case cachée par trois corps | (15, 17) : aucun point dégagé, donc clic au centre, sur le Sadida juste devant | quand aucun point n'est dégagé, le bot vise le point le moins enfoncé dans un corps, et non plus le centre |

Le délai mesuré sur 413 clics de déplacement depuis le 27/10 :
- **Après la réponse du serveur au dernier sort** : clic ignoré 6 fois sur 19 à moins de 0,8 s, 5 sur 72 entre 0,8 et 1 s, et 2 sur 24 au-delà.
- **Selon le sort** : Oeil de Taupe 6 sur 7, Pelle Fantomatique 2 sur 7, Ronce 1 sur 18, Chance 0 sur 25.
- **En début de tour** : 7 clics ignorés sur 78 à moins de 0,3 s, 5 sur 137 entre 0,3 et 0,5 s. Ce n'est pas corrigé : attendre coûterait plus que les clics ratés.

Reclic rapide (utilisateur, 01/11) : un clic de déplacement ignoré de temps en temps est toléré s'il est vu vite. Le
serveur annonce un déplacement 0,18 s après le clic en médiane, et 223 fois sur 249 avant 0,6 s. Sans annonce en 0,6 s,
le bot reclique aussitôt, au lieu d'attendre environ 1,9 s. Ce reclic est compté à part, comme « reclic », et non
comme un raté :
- **Fin de tour** : la ligne indique « N reclics ».
- **Comparatif** : `tools/comparatif_sessions.py` a une ligne « reclics de déplacement ».
- **Bilan** : dans `tools/bilan_sessions.py`, seul « Mon personnage n'a pas bougé (capture…) » reste un problème.

Pas encore vu en jeu.

Limite connue : les références de combat construites à l'écran comptent toujours les arbres comme du sol. Le premier
combat d'une session, quand la carte n'a pas encore été annoncée, s'en sert encore.

## Session de 2 heures laissée par l'utilisateur (01/11, fin de matinée)
Désinvocation (utilisateur) : elle ne frappe que les invocations, et tuer l'invocateur tue ses invocations. Elle a
maintenant le rôle « invocations » : seulement sur une invocation (annoncée par « GA;181 »), et seulement quand aucun
autre sort ne touche personne ce tour. Avant, elle était lancée sur n'importe quel monstre.

Premier combat d'une session : la carte reconnue par la référence est remplacée par sa carte du client quand sa clé est
retenue (arbres compris).

Essai court (logs/20261101_112451, 3 combats en 0,-28) : aucun déplacement raté. Un tour d'Eldieso avec 5 sorts refusés
a montré deux causes :
- **Sort cliqué pendant l'animation du précédent** : Tir Éloigné, cliqué 0,15 s après la réponse du serveur à Tir
  Puissant, a été ignoré par le jeu. Tout sort attend maintenant la fin de l'animation du précédent, avec la même règle
  que les déplacements.
- **Sort cru parti sans réponse du serveur** : les PA baissés à l'écran étaient ceux de Tir Puissant. Le bot a compté
  +4 de portée, puis 5 sorts hors de portée au tour suivant. Avec l'écoute, sans PA dépensés annoncés, le sort est
  maintenant refusé, sauf si le tour est fini.

Pendant le farm (logs/20261101_113029) :
- **Paquet TCP renvoyé par le réseau, lu deux fois** : « Tir Puissant » annoncé deux fois, 6 PA dépensés au lieu de 3, et
  le sort suivant, ignoré par le jeu, cru parti. L'écoute suit maintenant le numéro de séquence TCP de chaque fenêtre
  et ne relit jamais un segment déjà lu.
- **Suiveurs crus ailleurs** : le principal est arrivé sur la carte suivante 7 ms avant que 3 suiveurs soient
  comparés à lui. Ils étaient comparés à sa nouvelle carte et ont été rejoints à pied, environ 15 s perdues. Ils sont
  maintenant comparés à sa carte de départ.
- **Déplacement après une mort** : Oeil de Taupe a tué deux monstres. Le jeu a gardé le clic de déplacement pendant
  leur mort et l'a envoyé 4,5 s plus tard, 0,2 s après l'abandon du bot. Quand le dernier sort a tué, le bot attend
  maintenant l'annonce du déplacement 6 s, sans reclic rapide.
- **Relief** : les 10 déplacements ratés entre 11:55 et 12:00 sont tous sur 0,-23, une carte avec des pentes et des
  sommets. Le bot visait le centre de la case comme sur un sol plat. Par exemple, (13, 11) est en pente au niveau 8, et
  le clic est tombé sur le flanc du sommet de (13, 13), au niveau 9, dessiné jusqu'à (13, 9). La carte du client donne
  le niveau et la pente de chaque case. Le point de clic, les corps des combattants et les blocs en tiennent compte
  maintenant : 1 niveau vaut 20 unités du jeu, environ 1,48 demi-hauteur de case, mesuré sur la capture. 0,-23 a 77 cases
  hors du sol de base ; 1,-28 et 0,-28 n'en ont aucune.
- **Fenêtre « résultat du combat » restée ouverte chez les suiveurs** : elle s'ouvre parfois après le passage de
  fermeture. Le clic sur le soleil tombait dessus, et le suiveur rejoignait le principal à pied ensuite. Elle est
  maintenant fermée juste avant le premier clic du suiveur sur le soleil.

Farm de 50 minutes (logs/20261101_113029, forêt d'Astrub, 7 cartes) :

| | 01/11 matin | farm 50 min | vérification (3,-25) |
|---|---|---|---|
| combats | 6 | 31 | 6 |
| durée moyenne d'un combat (s) | 45 | 55 | 53 |
| durée moyenne d'un tour (s) | 4.4 | 3.4 | 3.7 |
| sorts par tour | 1.42 | 1.18 | 1.21 |
| refus par tour | 0.02 | 0.08 | 0.11 |
| clics sans effet | 1 | 26 | 6 |
| déplacements sans bouger | 8 | 21 (17 sur 0,-23) | 0 |
| reclics de déplacement | - | 25 | 0 |
| morts / erreurs | 0 / 0 | 0 / 0 | 0 / 0 |

- **Le relief** explique l'essentiel des ratés du farm : 17 déplacements sur 21 et 21 sorts refusés sur 27, tous sur
  0,-23.
- **La vérification** est sans déplacement raté, mais ses cartes étaient plates : le relief reste à voir en jeu.
- **Ses 6 clics sans effet** viennent de 2 tours d'Eldieso (3 sorts refusés chacun). Tir Éloigné avait fait un échec
  critique (PA perdus sans effet), mais +4 de portée étaient comptés quand même. Corrigé : un échec critique ne donne
  plus aucun bonus.
- **Encore à faire** :
  - Voir le relief en jeu sur 0,-23.
  - Accélérer la fermeture des fenêtres « résultat du combat » : environ 1,4 s par fenêtre, l'une après l'autre.

## Fin de combat et suiveurs d'après le serveur (01/11, après-midi)

- **Fenêtres de résultat** : le serveur annonce la fin du combat (GE). La carte est rechargée environ 3,5 s plus tard
  (GDK, mesuré dans les 8 fenêtres du journal 113029), et la fenêtre de résultat ne s'ouvre qu'après.
  - Avant, le bot concluait « fini sans fenêtre de résultat » 1 s après GE, pour 24 combats sur 31. Le principal
    repartait au combat suivant pendant que les suiveurs fermaient leur fenêtre une par une (environ 1,7 s chacun).
  - Une fenêtre en train de s'ouvrir était même prise pour une fenêtre inconnue et fermée avec Échap.
  - Maintenant, le bot attend GDK dans chaque fenêtre (8 s au plus). Il regarde ensuite toutes les fenêtres à tour de
    rôle et clique « Fermer » dès que le bouton est immobile sur deux regards. Il revoit chaque fenêtre après le
    clic, et reclique si elle est encore là. Pas vue en 4 s : le tour des fenêtres s'en charge, comme avant.
  - Le serveur ne dit rien de la fermeture : c'est une fenêtre du jeu seulement, et l'écoute n'entend que le serveur.
    Seul l'écran confirme la fermeture.
- **Suiveurs** :
  - La carte de départ du principal est relevée d'après le serveur au premier clic du passage, et chaque suiveur est
    comparé à ce numéro de carte.
  - Un suiveur dont le déplacement est annoncé (GA0;1) ou la carte changée n'est plus recliqué. Avant, le bot
    jugeait au décor.
  - Clic sur le soleil sans déplacement annoncé en 1 s : pas pris, recliqué aussitôt, sans attendre de le voir
    immobile.
  - L'arrivée était déjà jugée par le serveur (GDM dans la fenêtre du suiveur).
- **PA** : avec l'écoute, les PA viennent du serveur (GTM, GA;102), et un sort n'est cru parti que sur ses PA
  dépensés annoncés après son clic. La baisse de PA du sort précédent ne peut plus être prise pour celle du suivant.

## Trouvé pendant le comparatif
- Branche : « Je ne vois pas d'ennemi » (16 fois, avec capture) pendant l'invisibilité : corrigé (plus de capture ni
  de second regard quand le serveur dit l'ennemi invisible).
- Version commitée (code commun) : banque, Waldarch, « Consulter son coffre personnel » pas trouvé, Échap a ouvert
  le Menu principal (refermé, nouvel essai réussi). À remplacer par la fermeture sûre des menus (`close_any_menu`).
