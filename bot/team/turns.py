"""À qui le tour ? D'après le portrait encadré d'orange dans la timeline (perception/timeline.py).

Au premier tour de table, le portrait d'un personnage n'est pas encore connu : sa fenêtre est celle qui affiche le
timer de son tour (anneau orange autour de la minimap, voir ui.turn_timer_visible), et son portrait est retenu à ce
moment-là. Ensuite, le portrait encadré suffit : on passe directement dans la bonne fenêtre. L'ordre de jeu des
personnages, appris lui aussi, dit quelle fenêtre regarder d'abord tant qu'un portrait n'est pas reconnu (personnage
qui a changé d'équipement, par exemple).

Un portrait de la couleur de l'équipe qui n'est celui d'aucun personnage (aucune fenêtre n'affiche le timer, deux
fois de suite) : une invocation alliée ; il est retenu pour ce combat, et son tour est laissé passer sans chercher.
"""

import logging

from bot.perception.timeline import Portrait, same_portrait
from bot.team.placement import merge_order

log = logging.getLogger(__name__)


class TurnRouter:
    def __init__(self, names: list[str]) -> None:
        self.names = list(names)
        self.portraits: dict[str, Portrait] = {}  # gardés d'un combat à l'autre (même équipement)
        self.others: list[Portrait] = []  # alliés qui ne sont aucun des personnages (invocations) : ce combat
        self.order: list[str] = []  # ordre de jeu des personnages dans ce combat
        # Ordre de jeu vu aux combats précédents (placement : team/placement.py), complété combat après combat.
        self.known_order: list[str] = []
        self.last: str | None = None  # dernier personnage qui a joué

    def new_fight(self) -> None:
        if self.order:
            self.known_order = merge_order(self.order, self.known_order)
        self.others.clear()
        self.order.clear()
        self.last = None

    def play_order(self, names: list[str]) -> tuple[list[str], bool]:
        """Ordre de jeu attendu de ces personnages, et s'il a été vu (False : aucun combat encore, ordre par défaut :
        celui de `names`, l'équipe du bot). Ceux qui n'ont jamais joué viennent en dernier, dans l'ordre de `names`."""
        seen = [n for n in self.known_order if n in names]
        return seen + [n for n in names if n not in seen], bool(seen)

    def learn(self, name: str, portrait: Portrait | None) -> None:
        """`name` joue (timer de son tour vu dans sa fenêtre) : son portrait est celui encadré à ce moment-là."""
        if portrait is not None:
            known = self.portraits.get(name)
            if known is None or not same_portrait(known, portrait):
                log.info("Portrait de %s retenu (timeline)%s", name, " : il a changé" if known is not None else "")
            self.portraits[name] = portrait
            self.others = [o for o in self.others if not same_portrait(o, portrait)]
        if name not in self.order:
            self.order.append(name)
        self.last = name

    def matches(self, name: str, portrait: Portrait | None) -> bool:
        return same_portrait(self.portraits.get(name), portrait)

    def owner(self, portrait: Portrait | None, among: list[str] | None = None) -> str | None:
        """Le personnage dont c'est le portrait ; None s'il n'est pas reconnu, ou s'il ressemble à plusieurs
        (deux personnages de même classe et mêmes couleurs : on vérifiera au timer)."""
        names = [n for n in (among if among is not None else self.names) if self.matches(n, portrait)]
        return names[0] if len(names) == 1 else None

    def is_other(self, portrait: Portrait | None) -> bool:
        return any(same_portrait(o, portrait) for o in self.others)

    def learn_other(self, portrait: Portrait) -> None:
        log.info("Allié de la timeline qui n'est aucun de mes personnages (invocation ?) : je laisse passer son tour")
        self.others.append(portrait)

    def candidates(self, portrait: Portrait | None, among: list[str]) -> list[str]:
        """Fenêtres à regarder pour trouver qui joue, dans l'ordre : portrait qui ressemble, puis le suivant dans
        l'ordre de jeu appris, puis les personnages au portrait encore inconnu, puis les autres."""
        ranked = [n for n in self.names if self.matches(n, portrait)]
        if self.last in self.order:
            ranked.append(self.order[(self.order.index(self.last) + 1) % len(self.order)])
        ranked += [n for n in self.names if n not in self.portraits] + self.names
        seen: list[str] = []
        for name in ranked:
            if name in among and name not in seen:
                seen.append(name)
        return seen
