"""Équiper un objet du sac dans l'emplacement d'arme : l'outil d'un métier avant de récolter, l'arme de combat ensuite.

Validé en jeu le 27/10 sur Enu-six (Faux de l'apprenti paysan) :
- l'inventaire s'ouvre à la touche I (« Ton inventaire ») ;
- la liste des types (« Tous types ») ne propose que les types présents dans le sac ; choisir « Faux » laisse la faux
  seule, dans la première case du sac (sa place n'est pas donnée par le serveur, son icône n'est pas connue) ;
- un VRAI double-clic l'équipe (en arrière-plan, deux clics séparés ne font que la sélectionner) ; le serveur confirme
  (« OM<uid>|1 ») et l'ancienne arme repart dans le sac ;
- l'arme compte dans les caractéristiques (Enu-six : 364 -> 334 PDV avec la faux) : remettre l'arme de combat après.
Le filtre est remis sur « Tous types » et l'inventaire refermé (Échap), même en cas d'échec.

31/10, utilisateur : l'objet se vérifie par son modèle (le numéro fixe du jeu), jamais d'après l'écran : l'exemplaire
annoncé par le serveur (OM<uid>|1) donne son modèle par le sac de la session, et le type de ce modèle (langue du
client, table I) doit être celui demandé ; avec `template`, le modèle exact.
"""

import logging
import time

from bot.brain.inventory import BAG as BAG_POSITION  # position « dans le sac » d'un objet (BAG, ici : la grille)

log = logging.getLogger(__name__)

INVENTORY_KEY = "i"
FILTER = (1723, 230)  # liste des types, repère de référence (fenêtre de 1918 x 1008, ancré à droite)
FIRST_SLOT = (1652, 291)  # première case du sac
BAG, BAG_TOP = (1700, 500), 15  # milieu de la grille du sac ; crans de molette vers le haut pour la remonter
ALL_TYPES = "Tous types"
OPEN_WAIT = 1.0
LIST_WAIT = 0.6
LIST = (1708, 430)  # milieu de la liste des types ouverte
LIST_SCROLLS = 8  # crans de molette (par 3) pour trouver un type sous le bas de la liste (31/10 : avec le butin du
# donjon Bouftou, une vingtaine de types dans le sac ; « Faux » et « Marteau » sous « Familier », 3 ne suffisaient plus)
LIST_LOOK = 1.5  # s : liste des types cherchée toutes les 0,3 s après le clic
CONFIRM_WAIT = 2.0  # s : objet dans l'emplacement d'arme d'après le serveur
FOUND = 0.75
SEARCH_BUTTON = (1846, 229)  # loupe à droite de la liste des types : la remplace par un champ de recherche (31/10)
SEARCH_WAIT = 1.0  # s : sac filtré après le nom tapé
SEARCH_PATCH = 60  # px de référence de part et d'autre du milieu du champ, regardés pour son blanc
# Lettres vues dans le champ (31/10, revue) : les SEARCH_PROBE premières tapées puis la bande FIELD_BAND (px de
# référence à gauche et à droite du point du champ, demi-hauteur) comparée avant / après ; FIELD_CHANGED px (aire de
# référence) qui changent de plus de FIELD_DIFF sur un canal : quelques lettres (le curseur clignotant : ~14 px).
SEARCH_PROBE = 3
FIELD_BAND = (170, 100, 7)
FIELD_DIFF = 80
FIELD_CHANGED = 25


def equip(game, item_type: str, template: int | None = None) -> bool:
    """Équipe le premier objet de type `item_type` (« Faux », « Hache », « Pioche », « Marteau »...) du sac ; True si le
    serveur le confirme et que le modèle équipé est bien de ce type (`template` : ce modèle exact). Sans écoute : True
    une fois le double-clic fait, pas de preuve."""
    from bot.brain.inventory import item_type_name
    from bot.brain.travel import escape_menu
    from bot.team.bank import best_text

    net = getattr(game, "net", None)
    if template is None and net is not None and net.inventory.known:
        # 31/10 : le modèle d'abord (référence fixe), pris dans le sac connu ; équipé par la recherche du sac.
        template = next((i.template for i in net.items() if i.position != 1 and item_type_name(i.template) == item_type),
                        None)
        if template is None:
            log.info("Aucun objet de type « %s » dans le sac (d'après le serveur) : rien à équiper", item_type)
            return False
    if template is not None:
        return equip_item(game, template)
    frame = game.look(park=False)
    score, _ = best_text(frame, "Ton inventaire", dark=False, bold=True)
    if score < FOUND:  # déjà ouvert : la touche le refermerait (27/10)
        game.press(INVENTORY_KEY)
        game.sleep(OPEN_WAIT)
        frame = game.look(park=False)
        score, _ = best_text(frame, "Ton inventaire", dark=False, bold=True)
    if score < FOUND:
        log.info("Inventaire pas vu après la touche %s (%.2f) : %s pas équipé", INVENTORY_KEY.upper(), score, item_type)
        escape_menu(game)
        return False
    layout = frame.layout
    try:
        if not _choose(game, layout, item_type):
            log.info("Type « %s » pas dans la liste du sac : rien à équiper", item_type)
            return False
        # Sac remis en haut : la première case est celle du haut de la grille (30/10 : grille descendue par la molette
        # destinée à la liste des types, cachée par « Ton métier passe niveau 2 » ; double-clic sur une case vide).
        game.wheel(layout.point(*BAG), -BAG_TOP)
        game.sleep(0.3)
        clicked = time.monotonic()
        game.double_click(layout.point(*FIRST_SLOT), (3, 3))
        if net is None or not (net.alive() or net.inventory.known):
            game.sleep(CONFIRM_WAIT)
            log.info("%s équipé (double-clic, sans confirmation du serveur)", item_type)
            return True
        done = net.wait_for("weapon_at", clicked, CONFIRM_WAIT) is not None
        if not done:
            log.info("%s pas équipé : le serveur n'a rien dit", item_type)
            return False
        return _right_item(net.weapon_template(), item_type, template)
    finally:
        if not _choose(game, layout, ALL_TYPES):
            log.info("Filtre du sac pas remis sur « %s »", ALL_TYPES)
        escape_menu(game)


def equip_item(game, template: int) -> bool:
    """Équipe l'objet de modèle `template` (le numéro fixe du jeu, 31/10, utilisateur : « utiliser la référence
    fixe ») : son nom exact (langue du client) tapé dans la recherche du sac (loupe) ne laisse que lui, en première
    case ; double-clic ; le serveur doit annoncer ce modèle dans l'emplacement d'arme. Plus de liste des types à faire
    défiler (31/10 : une vingtaine de types avec le butin, ni la molette ni la flèche ne la font défiler en
    arrière-plan). True seulement si le modèle équipé est `template`."""
    from bot.brain.inventory import item_name, item_type_name
    from bot.brain.travel import escape_menu
    from bot.team.bank import best_text

    net = getattr(game, "net", None)
    if net is not None and net.weapon_uid is not None and net.weapon_template() == template:
        # Seulement d'après un OM entendu à ce lancement (31/10 : sac repris d'un fichier écrit avant un équipement fait
        # ailleurs : « déjà équipé » à tort).
        log.info("%s déjà équipé (d'après le serveur)", item_name(template))
        return True
    name = item_name(template)
    # Écoute suivie : flux vivant, ou sac connu (31/10 : écoute neuve, rien encore entendu, la faux crue « sans
    # confirmation » alors que OM est arrivé aussitôt après).
    heard = net is not None and (net.alive() or net.inventory.known)
    if heard and net.inventory.known and not any(i.template == template and i.position == BAG_POSITION
                                                 for i in net.items()):
        # 31/10, revue : le modèle voulu doit être dans le sac d'après le serveur AVANT le double-clic : sinon la
        # première case filtrée serait un autre objet (un consommable serait utilisé).
        log.info("%s (modèle %d) pas dans le sac d'après le serveur : rien à équiper", name, template)
        return False
    frame = game.look(park=False)
    score, _ = best_text(frame, "Ton inventaire", dark=False, bold=True)
    if score < FOUND:  # déjà ouvert : la touche le refermerait (27/10)
        game.press(INVENTORY_KEY)
        game.sleep(OPEN_WAIT)
        frame = game.look(park=False)
        score, _ = best_text(frame, "Ton inventaire", dark=False, bold=True)
    if score < FOUND:
        log.info("Inventaire pas vu après la touche %s (%.2f) : %s pas équipé", INVENTORY_KEY.upper(), score, name)
        escape_menu(game)
        return False
    layout = frame.layout
    try:
        if not search_bag(game, layout, name):
            log.info("%s pas équipé : recherche du sac pas faite (aucun double-clic)", name)
            return False
        game.wheel(layout.point(*BAG), -BAG_TOP)
        game.sleep(0.3)
        before = held_by_template(net) if heard else {}
        clicked = time.monotonic()
        game.double_click(layout.point(*FIRST_SLOT), (3, 3))
        if not heard:
            game.sleep(CONFIRM_WAIT)
            log.info("%s équipé (double-clic, sans confirmation du serveur)", name)
            return True
        confirmed = net.wait_for("weapon_at", clicked, CONFIRM_WAIT) is not None
        _tell_consumed(game, net, before, template)
        if not confirmed:
            log.info("%s pas équipé : le serveur n'a rien dit (pas dans le sac ?)", name)
            return False
        return _right_item(net.weapon_template(), item_type_name(template) or "", template)
    finally:
        close_bag_search(game, layout)
        escape_menu(game)


def held_by_template(net) -> dict[int, int]:
    """Objets portés par modèle (sac et équipement), d'après le serveur : un objet qui change de place ou dont la pile
    se fond dans une autre garde son total ; seul un objet consommé (OQ en baisse, OR) le fait baisser."""
    totals: dict[int, int] = {}
    for item in net.items():
        totals[item.template] = totals.get(item.template, 0) + item.quantity
    return totals


def _tell_consumed(game, net, before: dict[int, int], wanted: int) -> list[int]:
    """31/10, revue : un consommable en première case du sac filtré serait utilisé par le double-clic. Modèles autres
    que `wanted` dont le total a baissé depuis `before` (OQ / OR du serveur) : dit à l'utilisateur. Leur liste."""
    from bot.alerts import tell_user
    from bot.brain.inventory import item_name

    after = held_by_template(net)
    lost = {model: count - after.get(model, 0) for model, count in before.items()
            if model != wanted and after.get(model, 0) < count}
    if lost:
        who = getattr(net, "name", "") or "le personnage"
        what = ", ".join(f"{item_name(model)} (modèle {model}) x{count}" for model, count in lost.items())
        tell_user(f"Équipement de {item_name(wanted)} pour {who} : le double-clic a consommé {what} d'après le serveur")
    return list(lost)


def search_bag(game, layout, name: str, field=None, button=None) -> bool:
    """Recherche du sac (loupe) sur le nom `name` (search_text) : il ne reste que cet objet, en première case. `field`,
    `button` : le champ (à la place de la liste des types) et la loupe, repères de référence (défaut : inventaire ;
    fenêtre d'échange : team/trade.py). La loupe bascule : cliquée seulement si la recherche est fermée.

    31/10, revue : le focus du champ n'était jamais vérifié ; des lettres tapées hors du champ deviennent des
    raccourcis du jeu. Le champ doit être vu ouvert avant toute lettre, puis les SEARCH_PROBE premières lettres vues
    dedans (le champ change) avant le reste. False (rien de plus tapé, aucun double-clic à faire) sinon."""
    field, button = field or FILTER, button or SEARCH_BUTTON
    if not _search_open(game, layout, field):
        game.click(layout.point(*button), (2, 2))
        game.sleep(0.5)
        if not _search_open(game, layout, field):
            log.warning("Recherche du sac pas ouverte après la loupe : rien tapé (« %s »)", name)
            return False
    game.click(layout.point(*field), (20, 3))
    game.sleep(0.3)
    for _ in range(40):  # champ vidé (une recherche d'avant)
        game.press("backspace")
    game.sleep(0.2)
    text = search_text(name)
    empty = _field_patch(game.look(park=False), layout, field)
    probe = text[:SEARCH_PROBE]
    game.type_text(probe)
    game.sleep(0.3)
    if not _field_changed(empty, _field_patch(game.look(park=False), layout, field), layout):
        from bot.alerts import tell_user

        log.warning("Recherche du sac : « %s » tapé mais pas vu dans le champ : arrêt (pas de focus ?)", probe)
        tell_user(f"Recherche du sac : les lettres « {probe} » ne sont pas arrivées dans le champ de recherche "
                  f"(peut-être prises comme raccourcis du jeu) : {name} pas cherché, vérifie la fenêtre")
        return False
    if text[len(probe):]:
        game.type_text(text[len(probe):])
    game.sleep(SEARCH_WAIT)
    return True


def _field_patch(frame, layout, field):
    """Bande du champ de recherche (FIELD_BAND autour du point `field`, repère de référence) ; vide sans capture."""
    import numpy as np

    image = getattr(frame, "image", None)
    if image is None:
        return np.zeros((0, 0, 3), np.uint8)
    x, y = layout.point(*field)
    left, right, half = (layout.length(v) for v in FIELD_BAND)
    x0, x1 = max(0, round(x - left)), min(image.shape[1], round(x + right))
    y0, y1 = max(0, round(y - half)), min(image.shape[0], round(y + half))
    return image[y0:y1, x0:x1].copy()


def _field_changed(before, after, layout) -> bool:
    """Le champ a changé (lettres affichées) : au moins FIELD_CHANGED px de référence (aire) nettement différents ; le
    curseur qui clignote n'y suffit pas."""
    import numpy as np

    if before.size == 0 or before.shape != after.shape:
        return False
    moved = (np.abs(before.astype(int) - after.astype(int)).max(axis=2) > FIELD_DIFF).sum()
    return int(moved) >= layout.area(FIELD_CHANGED)


def close_bag_search(game, layout, field=None, button=None) -> None:
    """Recherche vidée (le sac entier de nouveau) et liste des types de retour. Recherche pas ouverte : rien (31/10 :
    le clic sur le champ ouvrirait la liste des types, les retours arrière iraient au jeu)."""
    field, button = field or FILTER, button or SEARCH_BUTTON
    if not _search_open(game, layout, field):
        return
    game.click(layout.point(*field), (20, 3))
    for _ in range(40):
        game.press("backspace")
    game.sleep(0.3)
    if _search_open(game, layout, field):
        game.click(layout.point(*button), (2, 2))
        game.sleep(0.3)


def search_text(name: str) -> str:
    """Texte tapé dans la recherche du sac : le nom après sa dernière apostrophe (31/10 : « Faux de l'apprenti paysan »
    tapé en entier ne filtrait rien, l'apostrophe ne passe pas en arrière-plan ; « apprenti paysan » ne laisse que la
    faux). Un autre objet laissé par la recherche est écarté par la vérification du modèle."""
    return name.replace("’", "'").split("'")[-1].strip() or name


def _search_open(game, layout, field=None) -> bool:
    """Champ de recherche du sac affiché à la place de la liste des types ? Les deux sont blancs (31/10) : la liste se
    reconnaît à son texte « Tous types »."""
    from bot.team.bank import best_text

    frame = game.look(park=False)
    x, y = (round(v) for v in layout.point(*(field or FILTER)))
    reach = round(layout.length(SEARCH_PATCH))
    patch = frame.image[y - 5:y + 5, x - reach:x + reach]
    white = patch.size > 0 and float((patch.min(axis=2) >= 245).mean()) >= 0.5
    return white and best_text(frame, ALL_TYPES, dark=True)[0] < FOUND


def _right_item(equipped: int | None, item_type: str, template: int | None) -> bool:
    """Le modèle équipé (d'après le serveur) est-il le bon : `template` s'il est donné, sinon un objet de type
    `item_type` (langue du client) ? Modèle inconnu (sac de la session pas connu) : le dit, et True (confirmé, sans
    preuve du modèle)."""
    from bot.brain.inventory import item_name, item_type_name

    if equipped is None:
        log.info("%s équipé (confirmé par le serveur ; modèle inconnu, sac de la session pas connu)", item_type)
        return True
    kind = item_type_name(equipped)
    right = equipped == template if template is not None else kind == item_type
    if right:
        log.info("%s équipé : %s (modèle %d, confirmé par le serveur)", item_type, item_name(equipped), equipped)
    else:
        log.warning("Objet équipé : %s (modèle %d, type %s), pas %s : mauvais objet", item_name(equipped), equipped,
                    kind, f"le modèle {template}" if template is not None else f"un(e) {item_type}")
    return right


def _choose(game, layout, name: str) -> bool:
    """Ouvre la liste des types du sac et y clique `name` ; False s'il n'y est pas (liste refermée)."""
    from bot.team.bank import best_text

    game.click(layout.point(*FILTER), (6, 3))
    game.sleep(0.3)
    game.wheel(layout.point(*LIST), -BAG_TOP)  # liste remise en haut : elle garde sa place d'une ouverture à l'autre
    clicked = time.monotonic()
    while True:  # 27/10 : liste pas encore là 0,6 s après le clic, « Faux » pas trouvé
        game.sleep(0.3)
        score, where = best_text(game.look(park=False), name, dark=True)
        if score >= FOUND or time.monotonic() - clicked >= LIST_LOOK:
            break
    for _ in range(LIST_SCROLLS):
        # 30/10 : le blé récolté a ajouté un type, « Marteau » est passé sous le bas de la liste : molette dessus.
        if score >= FOUND:
            break
        game.wheel(layout.point(*LIST), 3)
        game.sleep(0.4)
        score, where = best_text(game.look(park=False), name, dark=True)
    if score < FOUND:
        game.click(layout.point(*FILTER), (6, 3))  # referme la liste
        game.sleep(LIST_WAIT)
        return False
    game.click(where, (6, 2))
    game.sleep(LIST_WAIT)
    return True
