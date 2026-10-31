"""Récolte d'un personnage sur sa carte (27/10 : premier essai validé en jeu, Elseiso, Bûcheron 23, Frêne en -1,-16).

Les ressources viennent du serveur, pas de l'écran :
- la carte (GDM) arrive avec sa clé, toujours la même pour une carte (27/10 : vérifié sur toutes les cartes capturées) ;
  retenue dans data/cles_cartes.json pour les cartes où l'on se trouve déjà au lancement ;
- le mapData de la carte (data/client_retro/maps), déchiffré avec cette clé (tools/cles_cartes.py), donne l'objet
  interactif de chaque case ; la langue du client (IO, SK) donne la ressource, le métier et l'action du menu ;
- l'état des ressources qui ne sont pas dans leur état de départ arrive par GDF (« case;état » : 2 en cours, 3 récolté,
  4 repousse) ; sans GDF, une ressource est debout.

Un essai, en jeu : clic sur la case de la ressource (joueurs cachés : touche E tenue), menu, clic sur l'action
(« Couper »...) ; le serveur annonce la récolte (GA1;501;récolteur;case,durée : 9,7 s pour un Frêne au niveau 23), la
case passe à l'état 3 à la fin, la quantité arrive (IQ, 3 Bois de Frêne). L'outil du métier doit être équipé (Bûcheron :
une hache, l'Agride d'Elseiso convient).
"""

import json
import logging
import time
from dataclasses import dataclass

from bot.brain.arena import map_cell
from bot.config import ROOT

log = logging.getLogger(__name__)

KEYS = ROOT / "data" / "cles_cartes.json"
STANDING = 1  # état de départ d'une ressource (absent des GDF)
MENU_WAIT = 2.0
MENU_LEFT, MENU_UP, MENU_RIGHT, MENU_DOWN = 120, 150, 400, 260  # px de référence autour du clic : où s'ouvre le menu  # s : menu de la ressource attendu à l'écran après le clic
START_WAIT = 8.0  # s : récolte annoncée par le serveur après le clic sur l'action (le personnage marche jusqu'à elle)
END_MARGIN = 2.0  # s après la durée annoncée : fin de la récolte (GDF état 3, ou quantité IQ)
SKIP_FOR = 120.0  # s : ressource écartée après un échec (menu pas vu, récolte pas commencée)
# Action du menu de la ressource : 27/10, « Couper » 0,76, « Faucher » 0,71-0,72 (police du jeu), sans menu 0,37-0,43.
FOUND = 0.68


@dataclass(frozen=True)
class Resource:
    cell: int  # numéro de case du client
    name: str  # « Frêne »
    job: str  # « Bûcheron »
    action: str  # « Couper »
    level: int  # niveau de l'objet obtenu (proche du niveau de métier demandé)


def harvest_table() -> dict[int, list[Resource]]:
    """Graphisme de l'objet interactif -> ressources possibles (une par compétence de récolte), d'après la langue du
    client (data/client_retro/lang). Vide si elle n'est pas là."""
    try:
        from tools.ressources_essai import recoltes  # import local : lecture des fichiers de langue du client
    except Exception:  # langue absente
        return {}
    table: dict[int, list[Resource]] = {}
    try:
        rows = recoltes()
    except (OSError, KeyError):
        return {}
    for row in rows:
        for gfx in row["gfx"]:
            table.setdefault(gfx, []).append(Resource(-1, row["ressource"], row["metier"], row["action"],
                                                      int(row.get("niveau_objet") or 0)))
    return table


def resources_on_map(map_id: int, key: str, table: dict[int, list[Resource]] | None = None) -> list[Resource]:
    """Ressources récoltables de la carte, une par case (mapData déchiffré avec la clé du GDM). Vide sans le fichier de
    la carte ou si la clé ne va pas."""
    from tools.cles_cartes import dechiffrer_carte  # import local : déchiffrement du client (outil validé le 20/10)

    table = harvest_table() if table is None else table
    try:
        _, _, cells = dechiffrer_carte(map_id, key)
    except (KeyError, ValueError, OSError) as error:
        log.info("Carte %s : ressources inconnues (%s)", map_id, error)
        return []
    found = []
    for number, cell in enumerate(cells):
        if cell.get("objet2_interactif") and cell.get("objet2") in table:
            for kind in table[cell["objet2"]]:
                found.append(Resource(number, kind.name, kind.job, kind.action, kind.level))
    return found


def load_keys() -> dict[int, str]:
    try:
        return {int(k): v for k, v in json.loads(KEYS.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        return {}


def remember_key(map_id: int, key: str) -> None:
    keys = load_keys()
    if keys.get(map_id) != key:
        keys[map_id] = key
        KEYS.parent.mkdir(parents=True, exist_ok=True)
        KEYS.write_text(json.dumps({str(k): v for k, v in sorted(keys.items())}, indent=0), encoding="utf-8")


class Harvester:
    """Récolte, par un personnage, des ressources de sa carte que `wanted` accepte (nom de ressource -> True)."""

    def __init__(self, game, wanted, hide_key: str = "e", popups=None) -> None:
        self.game = game
        self.wanted = wanted
        self.hide_key = hide_key
        self.popups = popups  # brain/popups.PopupCloser : fenêtres fermées après chaque récolte (niveau de métier)
        self.skipped: dict[int, float] = {}  # case -> écartée jusqu'à (time.monotonic())
        self.stuck = False  # un menu resté ouvert après un essai (rien de sûr pour le fermer) : récolte arrêtée (31/10)

    def targets(self, map_id: int | None = None) -> list[Resource]:
        net = self.game.net
        map_id = net.map_id if map_id is None else map_id
        key = net.map_key if net.map_key and map_id == net.map_id else load_keys().get(map_id)
        if map_id is None or key is None:
            log.info("Récolte : carte ou clé inconnue (pas encore de changement de carte entendu)")
            return []
        if net.map_key:
            remember_key(map_id, net.map_key)
        return [r for r in resources_on_map(map_id, key) if self.wanted(r)]

    def available(self, targets: list[Resource]) -> list[Resource]:
        now = time.monotonic()
        objects = dict(self.game.net.objects)
        return [r for r in targets if objects.get(r.cell, STANDING) == STANDING and self.skipped.get(r.cell, 0) < now]

    def run(self, map_id: int | None = None, most: int = 50) -> int:
        """Récolte jusqu'à `most` ressources de la carte, la plus proche d'abord ; nombre de récoltes réussies."""
        if self.stuck:  # menu resté ouvert à la récolte d'avant : refermé sans risque, sinon rien de cliqué
            from bot.brain.popups import close_any_menu

            if not close_any_menu(self.game, "Récolte"):
                return 0
            self.stuck = False
        targets = self.targets(map_id)
        names = sorted({r.name for r in targets})
        log.info("Récolte : %d ressource(s) visée(s) sur la carte (%s)", len(targets), ", ".join(names) or "aucune")
        done = 0
        while done < most:
            left = self.available(targets)
            if not left:
                break
            mine = self.game.net.my_cell()
            here = map_cell(mine) if mine is not None else None
            resource = min(left, key=lambda r: _distance(map_cell(r.cell), here))
            if self.harvest(resource):
                done += 1
            elif self.stuck:
                from bot.alerts import tell_user

                tell_user(f"Récolte arrêtée pour {getattr(self.game.net, 'name', '') or 'le personnage'} : un menu "
                          f"reste ouvert après le clic sur {resource.name} (ferme-le toi-même)")
                break
        log.info("Récolte finie : %d ressource(s) récoltée(s)", done)
        return done

    def harvest(self, resource: Resource) -> bool:
        from bot.brain.popups import close_any_menu, close_context_menu
        from bot.team.bank import best_text

        game, net = self.game, self.game.net
        frame = game.look(park=False)
        clicked = time.monotonic()
        point = frame.grid.center(map_cell(resource.cell))
        if self.hide_key:
            with game.holding(self.hide_key):
                game.click(point, (2, 2))
        else:
            game.click(point, (2, 2))
        score, where = 0.0, (0, 0)
        while time.monotonic() - clicked < MENU_WAIT:
            game.sleep(0.3)
            frame = game.look(park=False)
            # Texte clair sur fond gris (27/10 : « Faucher » à 0,71 cherché en texte foncé ; « Couper » passait à 0,76).
            # Menu à côté de la ressource cliquée : cherché là seulement (30/10 : « Faucher » trouvé à 0,72 en
            # (1855, 212), loin du Blé, cliqué pour rien).
            near = _near(frame, point)
            score, where = max(best_text(frame, resource.action, dark=False, box=near),
                               best_text(frame, resource.action, dark=True, box=near))
            if score >= FOUND:
                break
        if score < FOUND:
            path = game.recorder.save(frame.image, "recolte_menu_absent", lossless=True)
            log.info("%s en case %d : « %s » pas vu dans le menu (%.2f, capture : %s) : écarté %.0f s", resource.name,
                     resource.cell, resource.action, score, path, SKIP_FOR)
            # Échap ne ferme pas le menu d'une ressource (il ouvre le Menu principal) : clic à côté (27/10, Enu-six).
            # 31/10, revue : plus d'Échap « sinon » : ce qui reste ouvert (menu d'un joueur, de Flash, petit menu,
            # bulle) passe par la fermeture sûre ; rien de sûr : la récolte s'arrête (aucun clic par-dessus un menu).
            close_context_menu(game, game.look(park=False))
            if not close_any_menu(game, f"Récolte, {resource.name}"):
                self.stuck = True
            self.skipped[resource.cell] = time.monotonic() + SKIP_FOR
            return False
        menu = frame
        chosen = time.monotonic()
        game.click(where, (8, 2))
        # La récolte peut partir sur une ressource voisine (30/10 : clic sur le Blé de la case 100, celui de la case 129,
        # dessiné par-dessus, fauché) : toute récolte annoncée après le clic compte, à sa case.
        while time.monotonic() - chosen < START_WAIT and not self._started_since(chosen):
            game.sleep(0.2)
        cell = self._started_since(chosen) or resource.cell
        started, duration = net.harvests.get(cell, (0.0, 0))
        if cell != resource.cell:
            log.info("%s : le clic sur la case %d a pris celle de la case %d (dessinée par-dessus)", resource.name,
                     resource.cell, cell)
            resource = Resource(cell, resource.name, resource.job, resource.action, resource.level)
        if started < chosen:
            path = game.recorder.save(menu.image, "recolte_pas_annoncee", lossless=True)
            log.info("%s en case %d : « %s » cliqué en (%d, %d) (%.2f), récolte pas annoncée par le serveur (menu : %s) : "
                     "écarté %.0f s", resource.name, resource.cell, resource.action, *where, score, path, SKIP_FOR)
            if hasattr(net, "heard_since"):
                log.debug("Serveur depuis le clic : %s", " | ".join(net.heard_since(clicked)) or "rien")
            self.skipped[resource.cell] = time.monotonic() + SKIP_FOR
            return False
        end = started + duration / 1000 + END_MARGIN
        while time.monotonic() < end and net.objects.get(resource.cell) != 3 and net.harvested_at < started:
            game.sleep(0.2)
        quantity = net.harvested if net.harvested_at >= started else None
        log.info("%s récolté en case %d : %s (%.1f s depuis le clic)", resource.name, resource.cell,
                 f"{quantity} récolté(s)" if quantity is not None else "quantité pas annoncée",
                 time.monotonic() - clicked)
        if self.popups is not None:  # « Ton métier Paysan passe niveau 2 » bloque l'écran (30/10)
            game.sleep(0.5)
            self.popups.handle(game.look(park=False), None)
        return True

    def _started_since(self, since: float) -> int | None:
        """Case d'une récolte annoncée par le serveur après `since` (la plus récente) ; None sinon."""
        started = [(at, cell) for cell, (at, _) in dict(self.game.net.harvests).items() if at >= since]
        return max(started)[1] if started else None


def _near(frame, point) -> tuple[int, int, int, int]:
    """Zone où s'ouvre le menu d'une ressource cliquée en `point` (en bas à droite du clic), dans la zone de jeu."""
    x0, y0, x1, y1 = frame.layout.map_box
    length = frame.layout.length
    return (max(x0, round(point[0] - length(MENU_LEFT))), max(y0, round(point[1] - length(MENU_UP))),
            min(x1, round(point[0] + length(MENU_RIGHT))), min(y1, round(point[1] + length(MENU_DOWN))))


def _distance(a: tuple[int, int], b: tuple[int, int] | None) -> float:
    return 0.0 if b is None else abs(a[0] - b[0]) + abs(a[1] - b[1])
