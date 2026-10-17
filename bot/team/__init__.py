"""Coordination de plusieurs fenêtres (équipe) : un personnage par fenêtre du jeu, menés ensemble (--equipe).

- roster.py : quelles fenêtres, quel personnage principal (réglages `team`, --principal) ;
- member.py : un personnage = sa fenêtre (passée au premier plan avant chaque regard ou geste), son jeu, ses sorts,
  son IA de combat ; journal et captures à son nom ;
- join.py : rejoindre le combat lancé par un autre (épée sur la carte, menu « Rejoindre ») ;
- turns.py : à qui le tour, d'après le portrait encadré dans la timeline ; ordre de jeu appris ;
- placement.py : qui va sur quelle case au placement (logique pure) ;
- loop.py : l'orchestrateur (TeamBot).

Actions refaites dans chaque fenêtre, et comment :
- changement de carte : le principal choisit et prend le soleil ; chaque suiveur clique ensuite sur la MÊME CASE de
  la carte dans sa fenêtre (convertie par sa grille : même case quelle que soit la taille de la fenêtre), tous d'abord,
  puis vérification de l'arrivée de chacun (nom de la carte = celui du principal) ;
- rejoindre le combat : tous sauf celui qui l'a lancé (épée cherchée dans chaque fenêtre) ;
- Prêt au placement : touche F1 (team.ready_key) dans chaque fenêtre, celui qui a lancé le combat en dernier ;
- fin de combat : fenêtres « Résultat du combat » et « Informations » (niveau supérieur) cherchées et fermées dans
  chaque fenêtre (le niveau supérieur n'arrive pas à tout le monde en même temps : pas de clic à l'aveugle) ;
- repos : tabouret dans la fenêtre de chacun de ceux dont la vie est basse, assis en même temps ;
- fenêtre des sorts : lue une fois par personnage, au premier tour des fenêtres ;
- mode tactique : activé par l'IA de chaque personnage, dans sa fenêtre (réglage d'affichage propre à chaque jeu).
- placement (team.place) : toute l'équipe selon l'ordre de jeu (placement.py), un clic par fenêtre et par passe,
  vérifié par une seule lecture des cases dans la fenêtre de celui qui a lancé le combat.
Actions du principal seul : recherche des groupes (Z), attaque, choix du trajet.
"""
