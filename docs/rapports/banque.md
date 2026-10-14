# Banque : vider l'inventaire pendant le farm (08/10)

Recherche (données Dofus Rétro seulement) et conception. Aucune donnée importée : chaque source attend ton accord.

## Ce qui est établi pour Rétro

### Pods

- **Cadran (1.31, Rétro)** : le cadran sous la vie peut afficher, au choix, l'XP, la dragodinde, le métier, les pods
  ou l'énergie. On pourrait donc lire les pods sans survol, comme la vie.
  - Sources : millenium.org/news/357321.html, breakflip.com/?p=100776.
  - Inconnu : comment on bascule le cadran sur les pods.
- **Transferts (1.31)** : le maximum transférable serait proposé automatiquement (à vérifier).
- **Sac plein** : la récolte est refusée, avec un message (JOL Rétro, article 14773).
- **Surcharge** : le personnage ne pourrait plus se déplacer (source ancienne, inaccessible : à confirmer).

### Ergonomie (1.31 et 1.33)

- Ctrl+clic pour sélectionner plusieurs objets ; clic droit sur un objet = menu de l'objet ; Maj+clic sur un PNJ pour
  lui parler directement.
- Ctrl+(double-)clic transférerait une pile entière (forums d'époque, à tester).

### Banque

- **Coût** : 1 kama par type d'objet présent dans le coffre, à chaque ouverture. Coffre lié au compte.
- **Restrictions** :
  - Bonta et Brâkmar réservées aux personnages alignés ;
  - accès à partir du niveau 11 (texte du banquier dans le client Rétro) ;
  - donc pas de banque pour des personnages d'Incarnam.
- **Coordonnées (JOL Rétro, article 14762)** : à recouper, car cet article recopie en partie un article Dofus 2.

  | Banque | Carte |
  |---|---|
  | Astrub | 4,-16 |
  | Village d'Amakna | 2,-2 |
  | Bonta | -29,-58 |
  | Brâkmar | -23,40 |
  | Village des Éleveurs | -16,4 |
  | Otomaï | -47,21 |
  | Sufokia | 14,25 |
  | Pandala | 34,-36 |

  Toutes existent dans notre carte du monde, sauf Pandala 34,-36, absente : probablement 24,-36 (village de Pandala).
- **Sources écartées (Dofus 2)** : Astrub [4,-18] (Millenium, Breakflip : refonte 2.46).

### Téléportation

- **Potion de Rappel** : renvoie au dernier zaap sauvegardé (interdite dans le labyrinthe).
- **Potions de Bonta et de Brâkmar** : elles existent ; destination exacte inconnue.
- **Zaap** : on sauvegarde par clic sur le zaap puis « Sauvegarder ». Prix d'un trajet : 10 x la distance (incertain).
- **Utiliser une potion** : double-clic dans l'inventaire ou dans la barre de raccourcis.

## Conception proposée

1. **Lire les pods** (`perception/pods.py`) : remplissage du cadran réglé sur les pods, sans survol (compatible
   arrière-plan).
   - **Quand** : après chaque combat (et plus tard après chaque récolte). En équipe, pendant le tour des fenêtres qui
     lit déjà la vie.
   - **Seuils** :
     - à 85 %, on va à la banque ;
     - à 95 %, plus aucune attaque ;
     - si les pods restent au-dessus du seuil après le dépôt : arrêt avec message.
2. **Le trajet, étape par étape** (`brain/bank.py`) :
   - Étapes : choisir la banque → (potion) → aller → entrer → parler au banquier → déposer → fermer → sortir →
     revenir → reprendre.
   - Elles passent avant la gestion des fenêtres : sinon le dialogue et la banque seraient fermés comme « fenêtre
     inconnue ».
   - Un combat subi en route est joué normalement, puis le trajet reprend.
3. **Choisir la banque** : la plus proche à pied, d'après la carte du monde. Avec l'option potion, on compare avec
   « potion + trajet depuis le zaap sauvegardé » ; ce zaap doit être déclaré dans les réglages, car il ne se lit pas à
   l'écran.
4. **Au guichet** :
   - clic sur le banquier, puis « Parler » (ou Maj+clic), puis la réponse « consulter » ;
   - dans la fenêtre de banque, filtre « Ressources » de l'inventaire, pour garder équipement et potions ;
   - transfert pile par pile, avec le geste validé par ton test (Ctrl+double-clic, double-clic + quantité, ou
     glisser-déposer) ;
   - contrôle à chaque pile, arrêt après 3 échecs.
5. **Équipe** : toute l'équipe va à la banque ensemble (suivi déjà en place). Au guichet, un personnage après l'autre.
   Un personnage de niveau 10 ou moins est sauté.
6. **Échecs** : chaque étape a un délai, un nombre d'essais et une capture. Si l'aller est impossible, le farm s'arrête
   avec un message (on ne continue pas surchargé).
7. **Arrière-plan** : jamais de survol. Un double-clic = deux clics à 1-2 px d'écart en moins de 150 ms. Pas encore
   testé : Ctrl ou Maj envoyés à la fenêtre, et le glisser-déposer.

## Captures à faire (plein écran 1920 x 1009)

1. **Le cadran réglé sur les pods** à environ 10, 50, 85 et 100 %, avec la valeur exacte notée. Dis-moi comment tu le
   bascules.
2. **La fenêtre d'inventaire** avec sa jauge de pods et le filtre « Ressources ». Quelle touche l'ouvre ?
3. **Les messages** de surcharge et de « sac plein ».
4. **La banque d'Astrub** :
   - la carte extérieure avec la porte ;
   - l'intérieur entier ;
   - le menu du banquier et le dialogue complet ;
   - la fenêtre de banque, peu remplie puis bien remplie ;
   - la fenêtre de quantité ;
   - la sortie.
5. **Tes essais à la main** : Ctrl+double-clic, double-clic seul, glisser-déposer puis Entrée, clic droit sur un objet.
   Y a-t-il un « tout transférer » ?
6. **Kamas insuffisants**, ou un personnage de niveau 10 ou moins (si possible).
7. **La Potion de Rappel** :
   - dans l'inventaire et dans la barre ;
   - l'arrivée au zaap ;
   - le menu du zaap.

## Données proposées (à approuver)

| Source | Ce qu'elle donne | Avis |
|---|---|---|
| Le cache de ton propre client Rétro (fichiers dans `%APPDATA%\Dofus Retro\...`), rien à télécharger | Liste officielle des lieux par carte (« Banque », « Zaap », etc. avec x, y) et textes du banquier | Meilleure source. L'agent n'a fait qu'y repérer ces entrées par recherche de texte : une lecture plus poussée a été refusée par les permissions, et il ne l'a pas contournée. **À toi de décider** si on lit ces fichiers. |
| JOL Rétro, article 14762 | 8 coordonnées de banques | À recouper (Pandala douteuse) |
| xixou.io (déjà utilisé) | Pas de banques | Rien de plus à prendre |
