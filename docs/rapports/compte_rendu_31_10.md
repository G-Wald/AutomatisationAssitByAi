# Compte rendu du 31/10 et analyse de la méthode

## Validé en jeu
- Donjon Bouftou : passages complets (entrée, 10 salles, boss, sortie), dont un d'un seul tenant avec répartition des clés.
- Entrée en deux temps : réponses du PNJ lues au réseau (DQ) ; personne n'entre si un seul n'a pas de clé (vu 3 fois).
- Clés : table exacte donjon → clé ; clé posée par la recherche du sac, modèle vérifié (EmKO) ; message si clés manquantes.
- Pods : surcharge rééquilibrée par échanges ; banque aller-retour par zaap (7/7 coffres vidés, dépôt prouvé par Ow).
- Zaap : touche E, autres points de clic, second essai ; regroupement d'après la vraie carte du suiveur.
- Équipement par le modèle (recherche du sac + OM) : faux puis marteau, plusieurs fois.
- Farm de clés au Berceau : 9 clés en 15 combats, puis 4 en 19 (banque entre-temps).
- Fin de combat par le serveur (GE) : K.O. et écran « hors combat » ignorés.

## Corrigé dans la journée (cause → correction)
- Sacs repris avec des identifiants d'une autre connexion → sac écrit dès la connexion, pods gardés avec lui.
- Groupe de salle « raté déjà » pour toujours → oubli des ratés (15 s en donjon).
- Clé devinée par le nom → table explicite ; clé prise par sa place dans la grille → recherche du sac.
- Case pâle prise pour vide → arrêt seulement si le serveur dit le sac vide.
- Menus fermés par Échap → `close_any_menu` partout (jamais Échap, jamais de clic dedans).
- Zaap cliqué depuis la mauvaise carte, puis 3 autres zaaps → carte du serveur, arrêt sinon.
- Personnage resté dans une salle → arrêt + message.

## Encore ouvert
- Reprise des clés au coffre (`take_from_chest`) : la pile ne bouge pas (glisser à recalibrer).
- La banque range aussi les clés (ressources) ; `donjon.garder_cle` dépend du point précédent.
- « Rejoindre » sans effet (Walitcoin, salle 8) : cause inconnue ; la réponse du serveur sera écrite au prochain cas.
- Au lancement, la carte de chacun est inconnue du réseau tant que personne n'a changé de carte.
- Références 1906 et 1911 à refaire (la carte du client les remplace pour les combats).

## Analyse de ma façon de travailler
Ce qui n'a pas marché :
- J'ai codé des mécanismes du jeu sur des suppositions (marche par soleils en donjon, trousseau utilisable à volonté, ordre de la grille) avant de les observer : temps perdu, code retiré ensuite.
- Le réflexe « écran » est revenu plusieurs fois alors que la règle est le réseau d'abord : il a fallu me le rappeler.
- J'ai arrêté un lancement au milieu d'une étape à plusieurs (Fourboun entré seul) : un arrêt n'est pas neutre.
- Des tests unitaires ont écrit dans les vrais fichiers de possessions (deux fichiers écrasés, restaurés).
- Diagnostics impossibles faute de trace : la réponse du serveur au « Rejoindre » de Walitcoin n'était gardée nulle part.
- Beaucoup de lancements ont avorté sur des préconditions (fenêtres fermées, clés absentes, quelqu'un ailleurs) découvertes en cours de route.
- Attente passive pendant les passages de 25 min ; tests en jeu surtout séquentiels.
- Travail non commité depuis plusieurs jours : pas de point de retour, et pas de copies séparées possibles pour les agents.
- Une fois, j'ai répondu sans rien lancer (« Oui, relance » ignoré).

À mettre en place :
1. Journal réseau de session (tous les messages du serveur hors chat, par personnage) : tout raté devient diagnostiquable après coup.
2. Contrôle avant lancement : fenêtres, carte de chacun, pods, clés, sacs connus ; un message clair et pas de lancement si une condition manque.
3. Arrêt propre « après l'étape en cours » (touche ou fichier), à la place de l'arrêt brutal du processus.
4. Règle de travail : un mécanisme du jeu inconnu s'observe d'abord (enregistrement réseau, ou ta confirmation) avant d'être codé.
5. Rejeu hors ligne : garder les messages réseau d'un passage et rejouer les décisions en tests, pour réduire les tests en jeu.
6. Scénarios croisés lancés par une commande avec un bilan automatique (critères réseau réussis ou ratés).
7. Commits par étape validée (je prépare le message, tu commites) : retour arrière possible et agents en parallèle dans des copies séparées.
