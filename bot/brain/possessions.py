"""Possessions de chaque personnage, gardées d'une session à l'autre (30/10, demande de l'utilisateur : « gérer une
référence de ce que possède chaque perso, alimentée par le drop, retirée par échange ou dépôt en banque »).

Pendant le jeu, le sac est tenu à jour par le serveur (brain/inventory.py) : en entier à la connexion (ASK), puis
chaque objet gagné (OAKO, OQ), perdu, échangé ou déposé (OR, OQ), équipé (OM). Le serveur ne renvoie le sac entier qu'à
la connexion : relancé sans reconnecter les personnages, le bot ne saurait plus rien. Le sac est donc écrit dans
data/possessions/<personnage>.json à chaque changement (save, appelé par la boucle de l'équipe) et repris au lancement
quand aucune connexion n'a été entendue (restore) ; la prochaine connexion le remplace par celui du serveur.
Ce qui change hors du bot (jeu à la main entre deux sessions) n'est pas vu avant la prochaine connexion : la date de la
dernière lecture est gardée et dite au journal.
"""

import json
import logging
import time
from pathlib import Path

from bot.brain.inventory import Item
from bot.config import ROOT

log = logging.getLogger(__name__)

FOLDER = ROOT / "data" / "possessions"


def _path(name: str, folder: Path) -> Path:
    return folder / f"{''.join(c if c.isalnum() or c in '-_' else '_' for c in name)}.json"


def save(name: str, net, folder: Path = FOLDER) -> bool:
    """Écrit le sac connu de `name` s'il a changé depuis la dernière écriture ; True s'il a été écrit."""
    if net is None or not getattr(net, "inventory", None) or not net.inventory.known:
        return False
    items = [[i.uid, i.template, i.quantity, i.position] for i in net.items()]
    pods = list(net.pods) if getattr(net, "pods", None) else None
    if getattr(net, "_saved_items", None) == items and getattr(net, "_saved_pods", None) == pods:
        return False  # rien de changé depuis la dernière écriture
    if not write(name, items, folder, pods):
        return False
    net._saved_items, net._saved_pods = items, pods
    return True


def write(name: str, items: list[list[int]], folder: Path | None = None, pods: list[int] | None = None) -> bool:
    """Écrit les objets [uid, modèle, quantité, position] de `name`. Aussi appelé par l'écoute réseau dès le sac
    complet de la connexion (ASK) : 31/10, le serveur renumérote les objets à chaque connexion ; un lancement repris
    d'un fichier plus ancien perdait tout ce qui s'ajoutait aux piles (OQ sur des numéros inconnus : un tiers du butin
    d'un passage du donjon Bouftou). `pods` : [portés, max] d'après « Ow » (31/10 : le serveur ne les envoie qu'à la
    connexion et quand le sac change ; un lancement repris ne voyait pas une surcharge)."""
    path = _path(name, FOLDER if folder is None else folder)
    record = {"personnage": name, "le": time.strftime("%Y-%m-%dT%H:%M:%S"), "objets": items}
    if pods:
        record["pods"] = list(pods)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
    except OSError as error:
        log.warning("Possessions de %s pas écrites (%s)", name, error)
        return False
    return True


def restore(name: str, net, folder: Path = FOLDER) -> bool:
    """Reprend le sac écrit à une session précédente si le serveur ne l'a pas encore envoyé ; True s'il a été repris."""
    if net is None or not getattr(net, "inventory", None) or net.inventory.known:
        return False
    path = _path(name, folder)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    with net._lock:
        if net.inventory.known:  # la connexion est arrivée entre-temps
            return False
        net.inventory.items = {uid: Item(uid, template, quantity, position)
                               for uid, template, quantity, position in record.get("objets", [])}
        net.inventory.known = True
        net.inventory.restored = True  # sans les jets des objets (08/11, team/wisdom.py)
        if not net.pods and record.get("pods"):
            net.pods = tuple(record["pods"])
    log.info("Possessions de %s reprises de la session du %s (%d objets) : à jour à la prochaine connexion", name,
             record.get("le", "?"), len(net.inventory.items))
    return True
