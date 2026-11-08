# Capture d'âmes — recherche (08/11) et idée pour le farm de donjon (plus tard)

## L'idée (utilisateur, 08/11)

Dans le farm de donjon, une case à cocher « capture de salle », puis une case par salle du donjon choisi. Chaque salle
cochée est capturée : un personnage équipé d'une pierre d'âme de niveau suffisant lance le sort de capture pendant le
combat. La pierre fixe le niveau maximal capturable : si tous les monstres d'une salle sont niveau 25 sauf un de niveau
250, il faut une pierre de 250 ou plus pour avoir une chance de capturer la salle.

Pas encore codé : à reprendre quand l'utilisateur le demandera.

## Ce que dit le jeu installé (vérifié)

- Sort **413 « Capture d'âmes »** (langue du client, table S).
- **Waldhary-[Gal]** l'a, au **niveau 1**, rangé en **case 15** de sa barre (liste des sorts envoyée par le serveur à la
  connexion, `SL...413~1~f`), donc hors des 14 cases que le bot sait utiliser. Aucun des 7 autres ne l'a.

## Recherche web (agent, 08/11) — Retro seulement ; les sources Dofus 2/3 sont signalées

Le sort :
- « Capture d'âmes », sort spécial de toutes les classes, gagné à la fin du **donjon des Bulbes** (Pandala, 19,-22,
  6 salles, boss Bulbig). Parchemin aussi possible (source faible). Le donjon du Kwakwa, c'est Dofus 2/3.
- Portée 0 (sur soi), combat contre des monstres seulement. Il met le lanceur dans l'état « chercheur d'âmes » : le
  **dernier monstre doit mourir pendant que l'état dure**. Son bonus s'ajoute à la chance de la pierre.
- Par niveau (wiki-dofus, JOL Retro) : PA 7 / 6 / 5 / 4 / 3 / 2 ; durée de l'état 1 / 2 / 2 / 3 / 4 / 5 tours ; bonus
  1 / 3 / 6 / 10 / 15 / 25 % ; relance 20 / 17 / 14 / 11 / 8 / 6 tours. Le niveau 6 des sorts communs se débloque au
  niveau 100 du personnage. Un autre article JOL Retro donnait 7/7/6/6/5 PA : **les données du client (table S, sort
  413) confirment le premier tableau** (effet 950 : état 2 pendant 1 à 5 tours ; effet 750 : bonus de capture 1 à 25 %).
- Faut-il être vivant à la fin ? Pas trouvé.

Les pierres :
- Équipées à la place de l'arme (plus d'attaque à l'arme) ; sans pierre, le sort ne se lance pas.
- Achetées aux PNJ des arènes de Bonta (-27,-60) et de Brâkmar ; pas de butin.
- Puissance maximale selon la taille (JOL Retro, 23/09/2019) : petite 50, moyenne 100, grande 150, énorme 200,
  gigantesque 500. Chance selon le type : hasardeuse 25 %, normale 50 %, heureuse 75 %. Prix de 1 250 à 37 500 kamas.
- En cas d'échec, la pierre n'est pas perdue (reste équipée).

Les règles :
- La pierre doit couvrir le **monstre le plus haut** du groupe (la règle de l'utilisateur est confirmée). Un monstre
  250 demande une gigantesque (500) : pas de pierre 250 en vente. « Égal » ou « strictement supérieur » : à vérifier.
- C'est l'âme du groupe entier qui est capturée ; la pierre pleine porte le nom du monstre le plus fort (onglet Divers).
- Plusieurs personnages avec une pierre : une seule pierre pleine par combat (source 1.23, confiance faible).
- Boss pas capturable après des abandons, Kralamoure : sources Dofus 2, pas transposées.

Utiliser une pierre pleine : en arène (Bonta, Brâkmar), « Utiliser » relance le combat du groupe (butin normal) ;
quête de l'Éternelle Moisson (Dofus Ocre, Otomaï) ; revente à l'hôtel de vente.

Sources Retro : https://dofusretro.jeuxonline.info/article/14791/sorts-communs ,
https://dofusretro.jeuxonline.info/article/14819/pierres-ame , https://www.millenium.org/guide/345675.html ,
https://www.dofux.org/articles-221-La-capture-d-ames.dx . wiki-dofus (version non dite) :
https://wiki-dofus.eu/w/Capture_d'%C3%A2me .

## À vérifier en jeu avant de coder

1. Monter le sort de Waldhary (niveau 1 : 7 PA, état 1 tour, +1 %) et le ranger dans les 14 premières cases.
2. Le moment du lancer : dans les derniers tours, selon la durée de l'état.
3. Lanceur mort à la fin : capture possible ?
4. Pierre de puissance égale au monstre le plus fort.
5. Plusieurs pierres dans un combat.
6. Capture d'une salle de boss.
7. Reconnaître la pierre vide et la pierre pleine par le réseau (modèle d'objet, effets).
8. Une salle de donjon relancée en arène.
