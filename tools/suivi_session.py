"""Suivi d'une session de farm : expérience, niveaux gagnés, kamas et butin de chaque personnage (08/11, demande de
l'utilisateur : « monitorer les levels gagnés », « une partie où on monitore le drop et l'XP », « il faudrait une
interface »).

Tout vient de la fin de chaque combat annoncée par le serveur, écrite dans les journaux réseau de la session
(logs/<session>/reseau_<perso>.txt) :

    GE<durée>;...|<id du lanceur>|...|2;<id>;<nom>;<niveau>;...;<xp min du niveau>;<xp>;<xp du niveau suivant>;
        <xp gagnée>;;;<butin modèle~quantité,...>;<kamas>;...

Chaque fin de combat est dans le journal de chacun des personnages : elle n'est comptée qu'une fois (heure et texte).
Les noms des objets viennent de la langue du client (table I).

    .venv\\Scripts\\python -m tools.suivi_session                      # sessions d'aujourd'hui, page écrite une fois
    .venv\\Scripts\\python -m tools.suivi_session --suivre             # réécrite toutes les 15 s (page qui se recharge)
    .venv\\Scripts\\python -m tools.suivi_session --depuis 15:30 --sessions 20261108_*

Page : logs/suivi_session.html (ouverte dans un navigateur, elle se recharge seule). Lecture seule : rien n'est
envoyé au jeu.
"""

import argparse
import html
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
PAGE = LOGS / "suivi_session.html"
REFRESH = 15  # s


@dataclass
class Perso:
    nom: str
    niveau_debut: int | None = None
    niveau: int | None = None
    xp_debut: int | None = None
    xp: int | None = None
    xp_suivant: int | None = None
    xp_min: int | None = None
    xp_gagnee: int = 0
    kamas: int = 0
    combats: int = 0
    butin: dict[int, int] = field(default_factory=dict)


@dataclass
class Combat:
    heure: str
    duree_s: float
    xp: dict[str, int]
    butin: dict[str, dict[int, int]]


def lire_fins(dossier: Path, depuis: str | None = None) -> list[tuple[str, str]]:
    """(heure, message GE) de la session, chaque fin de combat une fois."""
    vus, fins = set(), []
    for journal in sorted(dossier.glob("reseau_*.txt")):
        for ligne in journal.read_text(encoding="utf-8", errors="replace").splitlines():
            heure, _, texte = ligne.partition(" ")
            if not texte.startswith("GE") or (depuis and heure[:5] < depuis):
                continue
            cle = texte  # même message dans chaque journal (l'heure diffère de quelques ms)
            if cle not in vus:
                vus.add(cle)
                fins.append((heure, texte))
    return sorted(fins)


def analyser(fins: list[tuple[str, str]], persos: dict[str, Perso]) -> list[Combat]:
    combats = []
    for heure, texte in fins:
        parts = texte[2:].split("|")
        try:
            duree = int(parts[0].split(";")[0]) / 1000
        except ValueError:
            duree = 0.0
        xp, butin = {}, {}
        for bloc in parts[2:]:
            champs = bloc.split(";")
            if len(champs) < 13 or champs[0] != "2" or not champs[2]:
                continue  # monstre (0;...) ou bloc incomplet
            nom = champs[2]
            try:
                niveau = int(champs[3])
                xp_min, xp_now, xp_max, gagne = (int(c or 0) for c in champs[6:10])
            except ValueError:
                continue
            kamas = int(champs[13]) if len(champs) > 13 and champs[13].isdigit() else 0
            p = persos.setdefault(nom, Perso(nom))
            if p.niveau_debut is None:
                p.niveau_debut, p.xp_debut = niveau, xp_now - gagne
            p.niveau, p.xp, p.xp_min, p.xp_suivant = niveau, xp_now, xp_min, xp_max
            p.xp_gagnee += gagne
            p.kamas += kamas
            p.combats += 1
            objets = {}
            for item in filter(None, champs[12].split(",")):
                modele, _, quantite = item.partition("~")
                if modele.isdigit():
                    objets[int(modele)] = objets.get(int(modele), 0) + int(quantite or 1)
                    p.butin[int(modele)] = p.butin.get(int(modele), 0) + int(quantite or 1)
            xp[nom], butin[nom] = gagne, objets
        if xp:
            combats.append(Combat(heure[:8], duree, xp, butin))
    return combats


def noms_objets() -> dict[int, str]:
    try:
        from tools.langue_client import LangueClient

        return {int(k): v.get("n", k) for k, v in LangueClient().table("I")["u"].items()}
    except Exception:  # langue du client absente : les numéros
        return {}


def _nombre(n: int | None) -> str:
    return "—" if n is None else f"{n:,}".replace(",", " ")


def page(persos: dict[str, Perso], combats: list[Combat], sessions: list[str], noms: dict[int, str],
         suivre: bool) -> str:
    e = html.escape
    debut = combats[0].heure if combats else "—"
    fin = combats[-1].heure if combats else "—"
    lignes = []
    for p in sorted(persos.values(), key=lambda p: -p.xp_gagnee):
        gagnes = (p.niveau or 0) - (p.niveau_debut or 0)
        span = max(1, (p.xp_suivant or 0) - (p.xp_min or 0))
        part = min(100, max(0, round(100 * ((p.xp or 0) - (p.xp_min or 0)) / span)))
        lignes.append(
            f"<tr><td>{e(p.nom)}</td><td>{p.niveau_debut} → <b>{p.niveau}</b>"
            f"{f' <span class=up>+{gagnes}</span>' if gagnes else ''}</td>"
            f"<td><div class=bar><div style='width:{part}%'></div></div><small>{part} % du niveau</small></td>"
            f"<td class=n>{_nombre(p.xp_gagnee)}</td><td class=n>{_nombre(p.xp_gagnee // max(1, p.combats))}</td>"
            f"<td class=n>{_nombre(p.kamas)}</td><td class=n>{p.combats}</td></tr>")
    total = {}
    for p in persos.values():
        for modele, q in p.butin.items():
            total[modele] = total.get(modele, 0) + q
    butin = "".join(f"<tr><td>{e(noms.get(m, str(m)))}</td><td class=n>{q}</td><td>"
                    + ", ".join(f"{e(n)} {p.butin[m]}" for n, p in sorted(persos.items()) if m in p.butin)
                    + "</td></tr>" for m, q in sorted(total.items(), key=lambda kv: -kv[1]))
    derniers = "".join(
        f"<tr><td>{c.heure}</td><td class=n>{c.duree_s / 60:.1f} min</td><td class=n>{_nombre(sum(c.xp.values()))}</td>"
        f"<td>{e(', '.join(noms.get(m, str(m)) + (f' x{q}' if q > 1 else '') for b in c.butin.values() for m, q in b.items()) or '—')}</td></tr>"
        for c in reversed(combats[-15:]))
    xp_total = sum(p.xp_gagnee for p in persos.values())
    rafraichir = f"<meta http-equiv=refresh content={REFRESH}>" if suivre else ""
    return f"""<!doctype html><html lang=fr><head><meta charset=utf-8>{rafraichir}
<meta name=viewport content="width=device-width, initial-scale=1"><title>Suivi de session</title>
<style>
:root{{--bg:#f6f4ef;--fg:#222;--muted:#6b6b6b;--card:#fff;--line:#e3dfd6;--acc:#2f7d4f;--bar:#d9e9df}}
@media (prefers-color-scheme: dark){{:root{{--bg:#1c1c1c;--fg:#eee;--muted:#9a9a9a;--card:#262626;--line:#3a3a3a;--acc:#6cc58f;--bar:#2e4537}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:24px 0 8px}} .muted{{color:var(--muted)}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}} .card{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:140px}}
.card b{{display:block;font-size:20px}} table{{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line)}}
td,th{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{font-weight:600;color:var(--muted)}}
.n{{text-align:right;font-variant-numeric:tabular-nums}} .up{{color:var(--acc);font-weight:700}}
.bar{{height:8px;background:var(--bar);border-radius:4px;overflow:hidden;width:120px}} .bar div{{height:100%;background:var(--acc)}}
.wrap{{overflow-x:auto}}
</style></head><body>
<h1>Suivi de session</h1>
<div class=muted>Sessions : {e(', '.join(sessions))} · combats de {debut} à {fin} · mise à jour {datetime.now():%H:%M:%S}{' · se recharge toutes les ' + str(REFRESH) + ' s' if suivre else ''}</div>
<div class=cards><div class=card>Combats<b>{len(combats)}</b></div><div class=card>XP de l'équipe<b>{_nombre(xp_total)}</b></div>
<div class=card>Niveaux gagnés<b>{sum((p.niveau or 0) - (p.niveau_debut or 0) for p in persos.values())}</b></div>
<div class=card>Kamas<b>{_nombre(sum(p.kamas for p in persos.values()))}</b></div></div>
<h2>Personnages</h2><div class=wrap><table><tr><th>Personnage</th><th>Niveau</th><th>Progression</th><th class=n>XP gagnée</th><th class=n>XP par combat</th><th class=n>Kamas</th><th class=n>Combats</th></tr>{''.join(lignes)}</table></div>
<h2>Butin</h2><div class=wrap><table><tr><th>Objet</th><th class=n>Total</th><th>Par personnage</th></tr>{butin or '<tr><td colspan=3>—</td></tr>'}</table></div>
<h2>Derniers combats</h2><div class=wrap><table><tr><th>Fin</th><th class=n>Durée</th><th class=n>XP</th><th>Butin</th></tr>{derniers or '<tr><td colspan=4>—</td></tr>'}</table></div>
</body></html>"""


def construire(motif: str, depuis: str | None, suivre: bool, noms: dict[int, str]) -> dict:
    dossiers = sorted(d for d in LOGS.glob(motif) if d.is_dir())
    persos: dict[str, Perso] = {}
    fins, avec = [], []
    for dossier in dossiers:
        lues = lire_fins(dossier, depuis)
        fins += lues
        if lues:
            avec.append(dossier)  # seules les sessions qui ont des combats sont nommées
    seen, uniques = set(), []
    for heure, texte in sorted(fins):
        if texte not in seen:
            seen.add(texte)
            uniques.append((heure, texte))
    combats = analyser(uniques, persos)
    PAGE.write_text(page(persos, combats, [d.name for d in avec], noms, suivre), encoding="utf-8")
    return {"combats": len(combats), "xp": sum(p.xp_gagnee for p in persos.values()),
            "niveaux": {p.nom: [p.niveau_debut, p.niveau] for p in persos.values()}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sessions", default=f"{datetime.now():%Y%m%d}_*", help="dossiers de logs/ (motif)")
    parser.add_argument("--depuis", help="heure HH:MM : combats finis à partir de là")
    parser.add_argument("--suivre", action="store_true", help=f"page réécrite toutes les {REFRESH} s")
    args = parser.parse_args(argv)
    noms = noms_objets()
    while True:
        bilan = construire(args.sessions, args.depuis, args.suivre, noms)
        if not args.suivre:
            print(json.dumps(bilan, ensure_ascii=False))
            print(PAGE)
            return 0
        time.sleep(REFRESH)


if __name__ == "__main__":
    raise SystemExit(main())
