"""Test rapide (31/10) : repasse en un quart d'heure les sujets travaillés le 31/10, chaque étape jugée d'après le serveur
(écoute réseau) ou le journal du bot, avec un bilan OK / ÉCHEC / SAUTÉ.

Hors jeu : tests unitaires du jour, réglages (clé exacte du donjon, trousseau désactivé, réponses du PNJ).
En jeu, sans combat : reconnexion (sac écrit dès l'ASK), sacs suivis comparés au serveur par modèle, pods et carte de
chacun (carte du client déchiffrée), menus fermés, clés comptées (message si elles manquent), un échange de clé par la
recherche du sac, rééquilibrage s'il y a une surcharge, équipement par le modèle.
En jeu, par le bot : aller-retour par zaap avec regroupement, puis un combat (rejoindre, Prêt, fin dite par le serveur).

L'équipe doit être hors combat, au Berceau (cartes 1,-32 / 2,-34). Jamais d'arrêt forcé du bot : il s'arrête seul
(--combats 1, --minutes).

    python tools/test_rapide.py [--sans-pytest] [--sans-jeu] [--sans-reconnexion] [--sans-zaap] [--sans-combat]
                                [--equipement NOM:MODELE]
"""
import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.alerts import USER_ALERT  # noqa: E402

TEAM = ["Fourboun", "Eldieso", "Walitcoin", "Waldarch-[Gal]", "Pesopepe", "Enu-six-[Gal]", "Elseiso", "Fourbodeux"]
DUNGEON = "Donjon Bouftou"
KEY = 1568
FARM_MAP = (2, -34)  # entrée du donjon, sous-zone « Le berceau » (Boufton Blanc et Noir)
ZAAP_TRIP = "4,-19;2,-34"  # Astrub par le zaap du Berceau, puis retour
UNIT_TESTS = ["test_pods", "test_menus_surs", "test_mapdata", "test_reseau_combat", "test_cles", "test_dungeon",
              "test_echange", "test_possessions", "test_inventaire", "test_banque", "test_recolte", "test_travel",
              "test_journey", "test_team"]
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
BOT = [PYTHON, "-m", "bot", "--equipe", "--principal", TEAM[0], "--arriere-plan", "--lecture", "memoire",
       "--monde", "douze"]

log = logging.getLogger("test_rapide")
results: list[tuple[str, str, str]] = []
alerts: list[str] = []


def step(name: str, state: str, detail: str = "") -> None:
    results.append((name, state, detail))
    print(f"[{state}] {name}" + (f" : {detail}" if detail else ""), flush=True)


class AlertCatcher(logging.Handler):
    """Messages « À l'utilisateur » du bot pendant les étapes faites ici."""

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if USER_ALERT in text:
            alerts.append(text)


def catch_alerts() -> None:
    """Capteur posé sur le journal « bot » (31/10 : la connexion refait la configuration du journal et retirait celui
    posé au début sur la racine : message des clés dit mais pas vu)."""
    logger = logging.getLogger("bot")
    if not any(isinstance(h, AlertCatcher) for h in logger.handlers):
        logger.addHandler(AlertCatcher())


# ---------------------------------------------------------------------------------------------------------- hors jeu

def unit_tests() -> None:
    files = [str(ROOT / "tests" / f"{name}.py") for name in UNIT_TESTS if (ROOT / "tests" / f"{name}.py").exists()]
    started = time.monotonic()
    done = subprocess.run([PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    last = (done.stdout.strip().splitlines() or ["?"])[-1]
    step("Tests unitaires du jour", "OK" if done.returncode == 0 else "ÉCHEC",
         f"{len(files)} fichiers, {last} ({time.monotonic() - started:.0f} s)")
    if done.returncode != 0:
        print(done.stdout[-3000:], flush=True)


def settings_check() -> None:
    import yaml

    from bot.team.keys import key_mode, key_of

    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")) or {}
    donjon = settings.get("donjon") or {}
    entries = yaml.safe_load((ROOT / "config" / "donjons_entree.yaml").read_text(encoding="utf-8")) or {}
    bouftou = entries.get("bouftou") or {}
    problems = []
    if key_of(DUNGEON) != KEY:
        problems.append(f"clé de « {DUNGEON} » = {key_of(DUNGEON)} au lieu de {KEY}")
    if key_of("Donjon Bouftous") is not None:
        problems.append("un nom approché trouve une clé (le nom exact est exigé)")
    if donjon.get("trousseau") not in (False, "non", None):
        problems.append(f"donjon.trousseau = {donjon.get('trousseau')} (doit rester désactivé)")
    if bouftou.get("reponse_id") != 564 or bouftou.get("trousseau_id") != 6602:
        problems.append(f"réponses du PNJ : {bouftou.get('reponse_id')} / {bouftou.get('trousseau_id')}")
    step("Réglages (clé exacte, trousseau désactivé, réponses du PNJ)", "ÉCHEC" if problems else "OK",
         " ; ".join(problems) or f"clé {KEY}, mode des clés « {key_mode(settings)} », trousseau non")


# ------------------------------------------------------------------------------------------------------------ en jeu

def tracked_bags() -> dict[str, dict[int, int]]:
    """Sacs suivis par le bot (data/possessions), totaux par modèle, avant la reconnexion qui les réécrit."""
    from bot.brain import possessions

    bags = {}
    for name in TEAM:
        path = possessions._path(name, possessions.FOLDER)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        totals: dict[int, int] = {}
        for _, template, quantity, *_ in record["objets"]:
            totals[template] = totals.get(template, 0) + quantity
        bags[name] = totals
    return bags


def reconnect(listener, asks: dict[str, str]) -> bool:
    import win32con
    import win32gui

    from bot.capture.window import find_windows
    from bot.connection import network

    original = network.ClientFeed.feed

    def feed(self, text, now=None):
        if text.startswith("ASK|"):
            parts = text.split("|")
            asks[parts[2]] = parts[10] if len(parts) > 10 else parts[-1]
        return original(self, text, now)

    network.ClientFeed.feed = feed
    for window in find_windows("- Dofus Retro"):
        win32gui.PostMessage(window.hwnd, win32con.WM_CLOSE, 0, 0)
    time.sleep(6)
    sys.argv = ["bot.connection", "--arriere-plan", "--principal", TEAM[0], "--personnages", ",".join(TEAM)]
    from bot.connection.__main__ import main as connect

    connect()
    for _ in range(2):  # fenêtre pas rouverte par le launcher (30/10) : connexion des manquants
        if not missing_windows():
            break
        connect()
    time.sleep(3)
    missing = [name for name in TEAM if name not in asks]
    step("Reconnexion : sac complet reçu (ASK) et écrit", "ÉCHEC" if missing else "OK",
         f"sans sac : {', '.join(missing)}" if missing else f"{len(asks)} personnages")
    return not missing_windows()


def missing_windows() -> list[str]:
    from bot.capture.window import find_windows

    titles = [w.title.lower() for w in find_windows("- Dofus Retro")]
    return [name for name in TEAM if not any(t.startswith(name.lower()) for t in titles)]


def compare_bags(before: dict[str, dict[int, int]], asks: dict[str, str]) -> None:
    from bot.brain.inventory import item_name, parse_item

    gaps = []
    for name, text in asks.items():
        server: dict[int, int] = {}
        for entry in text.split(";"):
            item = parse_item(entry)
            if item is not None:
                server[item.template] = server.get(item.template, 0) + item.quantity
        mine = before.get(name)
        if mine is None:
            gaps.append(f"{name} : aucun sac suivi")
            continue
        for template in sorted(set(server) | set(mine)):
            if server.get(template, 0) != mine.get(template, 0):
                gaps.append(f"{name} {item_name(template) or template} : suivi {mine.get(template, 0)}, "
                            f"serveur {server.get(template, 0)}")
    step("Sacs suivis = sacs du serveur (par modèle)", "ÉCHEC" if gaps else "OK",
         "; ".join(gaps[:8]) + (" ..." if len(gaps) > 8 else "") if gaps else f"{len(asks)} sacs identiques")


def team_members(listener, control):
    from bot.actions.input import Human
    from bot.brain import possessions
    from bot.brain.game import Game
    from bot.capture.window import find_windows

    class NoRecorder:
        def save(self, *args, **kwargs):
            return None

    members = []
    for name in TEAM:
        found = [w for w in find_windows("- Dofus Retro") if w.title.lower().startswith(name.lower())]
        if not found:
            continue
        game = Game(found[0], control, NoRecorder(), Human(), act=True, background=True)
        game.net = listener.follow(found[0].hwnd, name)
        if not game.net.inventory.known:
            possessions.restore(name, game.net)
        members.append(SimpleNamespace(name=name, game=game))
    return members


def pods_and_maps(members) -> None:
    from bot.brain.mapdata import current_map
    from bot.brain.travel import WORLDS, Passages
    from bot.team import pods

    passages = Passages.load(WORLDS["douze"])
    unknown, places, overloaded = [], {}, []
    for member in members:
        net = member.game.net
        load = pods.load(net)
        if load is None:
            unknown.append(f"{member.name} pods")
        elif pods.overload(net) > 0:
            overloaded.append(f"{member.name} {load[0]}/{load[1]}")
        if net.map_id is None:
            unknown.append(f"{member.name} carte")
            continue
        if current_map(net) is None:
            unknown.append(f"{member.name} carte {net.map_id} pas déchiffrée")
        places[member.name] = passages.coords_of(net.map_id) or net.map_id
    step("Pods et carte de chacun d'après le serveur (carte du client lue)", "ÉCHEC" if unknown else "OK",
         ("inconnu : " + ", ".join(unknown) + " ; " if unknown else "")
         + "cartes : " + ", ".join(f"{n} {p}" for n, p in places.items())
         + (" ; surcharge : " + ", ".join(overloaded) if overloaded else " ; aucune surcharge"))


def menus_closed(members) -> None:
    from bot.brain.popups import close_any_menu, menu_open

    opened = []
    for member in members:
        kind = menu_open(member.game.look())
        if kind is not None:
            closed = close_any_menu(member.game, "test rapide")
            opened.append(f"{member.name} {kind} {'refermé' if closed else 'RESTÉ OUVERT'}")
    still = [o for o in opened if "RESTÉ" in o]
    step("Menus : aucun ouvert (sinon refermé sans Échap)", "ÉCHEC" if still else "OK",
         ", ".join(opened) or "aucun menu ouvert")


def keys_and_trade(members) -> None:
    from bot.team.keys import KeyManager

    manager = KeyManager(members, {"donjon": {"cles": "sac"}}, hide_key="e")
    held = manager.count(KEY)
    if held is None:
        step("Clés comptées d'après le serveur", "ÉCHEC", "un sac inconnu")
        return
    total = sum(held.values())
    catch_alerts()
    before = len(alerts)
    passes = manager.prepare(DUNGEON, 1)
    if total < len(members):
        told = len(alerts) > before
        step("Clés manquantes : aucun passage, message à l'utilisateur",
             "OK" if passes == 0 and told else "ÉCHEC",
             f"{total} clés pour {len(members)}, passages {passes}, message {'dit' if told else 'ABSENT'}")
        # un échange ne se fait que sur la même carte (31/10 : Eldieso en 1,-32, Fourboun en 2,-34)
        pairs = [(g, t) for g in members if held[g.name] >= 1 for t in members
                 if held[t.name] == 0 and g.game.net.map_id is not None and g.game.net.map_id == t.game.net.map_id]
        if not pairs:
            step("Échange d'une clé (recherche du sac, vérifié au serveur)", "SAUTÉ",
                 "personne pour donner/recevoir sur une même carte")
            return
        giver, taker = pairs[0]
        done = manager._trade(giver, taker, KEY, 1)
        time.sleep(1.0)
        after_giver, after_taker = giver.game.net.held(KEY), taker.game.net.held(KEY)
        moved = after_giver == held[giver.name] - 1 and after_taker == held[taker.name] + 1
        step("Échange d'une clé (recherche du sac, vérifié au serveur)", "OK" if done and moved else "ÉCHEC",
             f"{giver.name} {held[giver.name]}->{after_giver}, {taker.name} {held[taker.name]}->{after_taker}")
    else:
        step("Clés réparties pour un passage (échanges vérifiés au serveur)", "OK" if passes and passes >= 1 else "ÉCHEC",
             f"{total} clés, passages {passes}")


def rebalance(members) -> None:
    from bot.team import pods

    heavy = [m.name for m in members if pods.overload(m.game.net) > 0]
    if not heavy:
        step("Surcharge répartie sur les autres", "SAUTÉ", "personne en surcharge")
        return
    left = pods.rebalance(members, {})
    step("Surcharge répartie sur les autres", "ÉCHEC" if left else "OK",
         f"avant : {', '.join(heavy)} ; encore : {', '.join(left) or 'personne'}")


def equipment(members, spec: str) -> None:
    from bot.brain.equipment import equip_item
    from bot.brain.inventory import item_name

    name, _, template = spec.partition(":")
    member = next((m for m in members if m.name == name), None)
    if member is None or not template.isdigit():
        step("Équipement par le modèle", "SAUTÉ", f"{spec} : personnage absent ou modèle invalide")
        return
    net, template = member.game.net, int(template)
    before = net.weapon_template()
    if before == template or not net.held(template):
        step("Équipement par le modèle", "SAUTÉ", f"{name} n'a pas {item_name(template)} dans son sac")
        return
    first = equip_item(member.game, template) and net.weapon_template() == template
    time.sleep(1.0)
    back = before is None or (equip_item(member.game, before) and net.weapon_template() == before)
    step("Équipement par le modèle (vérifié par OM)", "OK" if first and back else "ÉCHEC",
         f"{name} : {item_name(template)} {'mis' if first else 'RATÉ'}, "
         f"{item_name(before) if before else 'rien'} {'remis' if back else 'PAS REMIS'}")


def save_bags(members) -> None:
    from bot.brain import possessions

    for member in members:
        possessions.save(member.name, member.game.net)


def in_game(args) -> bool:
    from bot.actions.stop import StopControl
    from bot.capture.window import enable_dpi_awareness
    from bot.connection.network import shared_listener

    enable_dpi_awareness()
    listener = shared_listener()
    if listener is None:
        step("Écoute réseau", "ÉCHEC", "pas d'écoute (Npcap ?) : tests en jeu arrêtés")
        return False
    control = StopControl("f12", "", corner=False).start()
    before = tracked_bags()
    asks: dict[str, str] = {}
    if args.sans_reconnexion:
        step("Reconnexion : sac complet reçu (ASK) et écrit", "SAUTÉ", "--sans-reconnexion")
    elif not reconnect(listener, asks):
        step("Fenêtres", "ÉCHEC", f"manquantes : {', '.join(missing_windows())} : tests en jeu arrêtés")
        return False
    if asks:
        compare_bags(before, asks)
    members = team_members(listener, control)
    time.sleep(2.0)  # pods (Ow) et carte (GDM) entendus après la connexion
    pods_and_maps(members)
    menus_closed(members)
    keys_and_trade(members)
    rebalance(members)
    equipment(members, args.equipement)
    save_bags(members)
    return True


# ------------------------------------------------------------------------------------------------- par le bot lui-même

def run_bot(extra: list[str], label: str) -> tuple[int, str]:
    """Lance le bot (il s'arrête seul) ; code de sortie et texte de son journal."""
    logs = ROOT / "logs"
    known = set(p.name for p in logs.iterdir()) if logs.exists() else set()
    print(f"... {label} : {' '.join(extra)}", flush=True)
    code = subprocess.run(BOT + extra, cwd=ROOT).returncode
    fresh = sorted(p for p in logs.iterdir() if p.name not in known and (p / "bot.log").exists())
    text = (fresh[-1] / "bot.log").read_text(encoding="utf-8", errors="replace") if fresh else ""
    return code, text


def told_user(text: str) -> list[str]:
    return [line.split(USER_ALERT, 1)[1].strip() for line in text.splitlines() if USER_ALERT in line]


def zaap_trip(size: int) -> None:
    code, text = run_bot(["--etapes", ZAAP_TRIP, "--minutes", "12"], "aller-retour par zaap")
    teleported = text.count("Zaap : téléporté")
    rallied = text.count("a rejoint le principal")
    messages = told_user(text)
    ok = code == 0 and teleported >= 2 * size and not messages
    step("Zaap aller-retour (touche E, arrivée dite par le serveur) et regroupement", "OK" if ok else "ÉCHEC",
         f"code {code}, {teleported} téléportations sur {2 * size}, {rallied} regroupements"
         + (f", messages : {' | '.join(messages)}" if messages else ""))


def one_fight(size: int) -> None:
    x, y = FARM_MAP
    code, text = run_bot([f"--combat-zone={x},{y}", "--combats", "1", "--minutes", "10"], "un combat")
    joined = text.count("a rejoint le combat")
    ready = text.count("Placement : Prêt") + text.count("Placement : je clique sur Prêt")
    over = text.count("Combat terminé")
    refused = [line for line in text.splitlines() if "n'a pas rejoint le combat" in line]
    messages = told_user(text)
    ok = code == 0 and over >= 1 and joined >= size - 1 and not refused and not messages
    step("Combat : rejoindre, Prêt, fin dite par le serveur", "OK" if ok else "ÉCHEC",
         f"code {code}, {joined}/{size - 1} rejoints, {ready} Prêt, {over} fin(s)"
         + (f", refus : {len(refused)}" if refused else "") + (f", messages : {' | '.join(messages)}" if messages else ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sans-pytest", action="store_true")
    parser.add_argument("--sans-jeu", action="store_true", help="hors jeu seulement")
    parser.add_argument("--sans-reconnexion", action="store_true", help="sacs repris des fichiers, pas de reconnexion")
    parser.add_argument("--sans-zaap", action="store_true")
    parser.add_argument("--sans-combat", action="store_true")
    parser.add_argument("--equipement", default="Enu-six-[Gal]:8540", metavar="NOM:MODELE",
                        help="arme équipée puis remise (défaut : la faux d'Enu-six)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # accents lisibles dans un terminal Windows comme dans un fichier
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    catch_alerts()
    started = time.monotonic()
    if not args.sans_pytest:
        unit_tests()
    settings_check()
    if not args.sans_jeu and in_game(args):
        size = len(TEAM) - len(missing_windows())
        if not args.sans_zaap:
            zaap_trip(size)
        if not args.sans_combat:
            one_fight(size)
    print(f"\n===== Bilan du test rapide ({(time.monotonic() - started) / 60:.1f} min) =====")
    for name, state, detail in results:
        print(f"{state:7} {name}" + (f"\n        {detail}" if detail else ""))
    if alerts:
        print("Messages à l'utilisateur pendant le test :\n  " + "\n  ".join(alerts))
    failed = sum(1 for _, state, _ in results if state == "ÉCHEC")
    print(f"{sum(1 for r in results if r[1] == 'OK')} OK, {failed} ÉCHEC, "
          f"{sum(1 for r in results if r[1] == 'SAUTÉ')} SAUTÉ")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
