"""Messages pour l'utilisateur (31/10, utilisateur : « il faudra gérer un message pour l'utilisateur indiquant le manque
de clés ») : une ligne marquée USER_ALERT au journal ; l'interface (bot/gui/app.py) la montre aussi dans une fenêtre
d'avertissement. En ligne de commande, la ligne du journal suffit."""

import logging

log = logging.getLogger("bot")

USER_ALERT = "À l'utilisateur :"


def tell_user(text: str) -> None:
    log.warning("%s %s", USER_ALERT, text)


def alert_text(line: str) -> str | None:
    """Le message d'une ligne du journal marquée USER_ALERT ; None pour les autres lignes."""
    _, marker, text = line.partition(USER_ALERT)
    return text.strip() if marker else None
