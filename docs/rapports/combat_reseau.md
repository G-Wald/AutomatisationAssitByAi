# Combat d'après l'écoute du serveur (branche `claude/combat-reseau`, 31/10)

Demande de l'utilisateur : refaire le combat entièrement sur l'écoute du serveur ; tester le cas des Prespic.

## Déjà d'après le serveur
- Phase (hors combat, placement, combat) : `read_scene` ← `ClientFeed.phase`.
- Qui joue (GTS), fin de tour (GTF), PA et PM (GTM, puis chaque action), mort (GA;103), fin du combat (GE).
- Combattants : case et équipe de chacun (`_seen_fighters`) ; allié = même équipe que moi. La couleur n'est plus
  qu'une étiquette « les miens / les autres ».
- Alliés jamais visés (`_told_allies`), vie des cibles (`life_at`), cases de placement (GP).
- Ennemis encore debout et fin de rafale (`_told_foes`), PA restants (`_pa_now`).

## Encore à l'écran (secours, ou à remplacer)
- Qui est moi quand l'écoute ne le sait pas encore (`_told_me` le fait d'abord).
- Placement en solo (`place`) : cases lues aux couleurs.
- Ligne de vue refusée : curseur « interdit » ; la carte du client (mapData) permettrait de la calculer.
- Sorts grisés dans la barre (aucun message du serveur connu pour ça).
- Lancers « à l'aveugle » (transformation) : cible et ma case inconnues.

## Cas des Prespic (forêt d'Astrub, 2,-27)
- Mémoire : « Camouflage » (sort 309) les rend invisibles ; le 04/10, 18 tours « Je ne vois pas d'ennemi » ; ils
  renvoient aussi des dégâts, et taclent.
- 31/10 : journal réseau de session ajouté (`logs/<session>/reseau_<nom>.txt`). Quatre combats en forêt, deux contre
  des Prespic : seulement leur sort 83, pas de Camouflage (combats trop courts).
- Observé sur 8 combats de plus (journal 20261031_231030, 23:15) :
  - Camouflage lancé : `GA;300;-1;309,390,...`, puis `GA;150;-1;-1,3,0` (invisible 3 tours) ;
  - tant qu'il est invisible, sa case vaut -1 dans `GTM` ; ses déplacements ne sont pas annoncés (seulement
    `GA;129`, PM dépensés) ; sa vie reste connue ;
  - il redevient visible en attaquant : `GA;150;-1;-1,0,0`, puis sa vraie case dans `GTM`.
- Défaut trouvé : la case -1 devenait (27, -1), hors de la carte ; l'équipe s'en approchait et Fourbodeux y a lancé
  Ronce. Corrigé : invisible = plus une cible (dernière case connue gardée, jamais visée si c'est un allié), dit une
  fois par tour au journal ; visible de nouveau dès `GA;150 … 0` ou une vraie case.
