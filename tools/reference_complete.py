"""Référence complétée des passages entre cartes, et contrôle de sa véracité (24/10, demande de l'utilisateur).

    .venv/Scripts/python -m tools.reference_complete            # construit data/references/*.json + rapport

Sources (téléchargées dans data/externes/, jamais versionnées ni redistribuées : aucune licence) :
- StarLoco-Game (github.com/StarLoco/StarLoco-Game, scripts/data/maps) : pour chaque carte, ses sorties
  `onMovementEnd` : case -> carte et case d'arrivée (émulateur : peut différer du serveur officiel) ;
- MapKeys-DR (github.com/Daweyy/MapKeys-DR) : clés des cartes relevées sur les serveurs officiels, qui déchiffrent les
  cartes du client installé (data/client_retro/maps) : cases praticables et objets posés, tels que le jeu les a.

Référence produite (data/references/<région>.json, locale) : par carte « x,y » (cartes extérieures de la région), pour
chaque côté, la carte voisine et les cases des soleils sur notre grille (bot/perception/grid.py), avec ce que chaque
contrôle en dit. Contrôles :
1. contre nos observations en jeu (config/monde/passages, data/carte.json) : passages pris, sorties notées bloquées,
   cases des soleils retenues ;
2. contre la carte officielle déchiffrée : la case de sortie est-elle praticable, porte-t-elle un objet de soleil ;
3. version : StarLoco décrit-il la même version de la carte que le client (date du fichier) ?
"""

import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from tools.cartes_client import case_vers_grille
from tools.cles_cartes import GFX_SOLEILS, dechiffrer_carte

RACINE = Path(__file__).resolve().parents[1]
EXTERNES = RACINE / "data" / "externes"
SORTIE = RACINE / "data" / "references"
RAPPORT = RACINE / "docs" / "rapports" / "veracite_reference.md"
INDEX = RACINE / "data" / "client_retro" / "cartes" / "index.json"
MEMOIRE = RACINE / "data" / "carte.json"
PASSAGES = RACINE / "config" / "monde" / "passages"
REGIONS = {0: "Monde des Douze", 3: "Incarnam"}  # super_zone de l'index du client
DELTA = {"droite": (1, 0), "gauche": (-1, 0), "bas": (0, 1), "haut": (0, -1)}
BLOQUEE = "bloquée"

MAPDEF = re.compile(r'MapDef\(\s*(\d+),\s*"(\d+)"')
SORTIE_RE = re.compile(r"\[(\d+)\]\s*=\s*moveEndTeleport\((\d+),\s*(\d+)\)")


def lire_starloco() -> dict[int, dict]:
    cartes = {}
    for chemin in glob.glob(str(EXTERNES / "starloco" / "scripts" / "data" / "maps" / "**" / "*.lua"), recursive=True):
        texte = Path(chemin).read_text(encoding="utf-8", errors="replace")
        entete = MAPDEF.search(texte)
        if entete:
            cartes[int(entete.group(1))] = {
                "date": entete.group(2),
                "sorties": [(int(c), int(d), int(dc)) for c, d, dc in SORTIE_RE.findall(texte)],
            }
    return cartes


def lire_cles() -> dict[int, tuple[str, str]]:
    cles = {}
    for chemin in glob.glob(str(EXTERNES / "mapkeys" / "maps" / "*.txt")):
        nom = Path(chemin).stem
        ident, date = nom.split("_", 1)
        cles[int(ident)] = (date, Path(chemin).read_text(encoding="utf-8").strip())
    return cles


def cote(depart: tuple[int, int], arrivee: tuple[int, int]) -> str | None:
    delta = (arrivee[0] - depart[0], arrivee[1] - depart[1])
    return next((c for c, d in DELTA.items() if d == delta), None)


def joignables(cases: list[dict], largeur: int, depart: set[int]) -> set[int]:
    """Cases (numéros du client) joignables à pied depuis `depart`, sur les cases actives et praticables de la carte
    officielle ; voisines dans les 8 directions (hors combat), sur notre grille : (±1, ±1), (±2, 0), (0, ±2)."""
    if largeur != 15:
        return set()
    grille = {case_vers_grille(n): n for n in range(len(cases))}
    libre = {n for n, c in enumerate(cases) if c["active"] and c["deplacement"] > 0}
    vus, pile = set(), [n for n in depart if n in libre]
    while pile:
        n = pile.pop()
        if n in vus:
            continue
        vus.add(n)
        i, j = case_vers_grille(n)
        for di, dj in ((1, 1), (1, -1), (-1, 1), (-1, -1), (2, 0), (-2, 0), (0, 2), (0, -2)):
            voisin = grille.get((i + di, j + dj))
            if voisin is not None and voisin in libre and voisin not in vus:
                pile.append(voisin)
    return vus


def construire() -> tuple[dict, dict]:
    index = {int(k): v for k, v in json.loads(INDEX.read_text(encoding="utf-8")).items()}
    starloco = lire_starloco()
    cles = lire_cles()
    memoire = json.loads(MEMOIRE.read_text(encoding="utf-8")) if MEMOIRE.exists() else {}
    references, stats = {}, defaultdict(Counter)
    officielles: dict[int, list | None] = {}

    def cases_officielles(ident: int) -> list | None:
        if ident not in officielles:
            officielles[ident] = None
            date_cle = cles.get(ident)
            if date_cle and index.get(ident) and date_cle[0] == str(index[ident].get("date") or date_cle[0]):
                try:
                    officielles[ident] = dechiffrer_carte(ident, date_cle[1])[2]
                except Exception:  # clé d'une autre version, fichier absent : pas de contrôle officiel
                    officielles[ident] = None
        return officielles[ident]

    for super_zone, region in REGIONS.items():
        ref: dict[str, dict] = {}
        vues = json.loads((PASSAGES / f"{region.lower().replace(' ', '_')}.json").read_text(encoding="utf-8")) \
            if (PASSAGES / f"{region.lower().replace(' ', '_')}.json").exists() else {}
        s = stats[region]
        arrivees: dict[int, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))  # carte -> côté d'entrée -> cases
        sorties_cases: dict[int, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))  # carte -> côté -> cases
        for ident, carte in starloco.items():
            info = index.get(ident)
            if not info or info.get("super_zone") != super_zone or not info.get("exterieur"):
                continue
            ici = (info["x"], info["y"])
            cle_ici = f"{ici[0]},{ici[1]}"
            for case, dest, case_dest in carte["sorties"]:
                arrivee = index.get(dest)
                if not arrivee or arrivee.get("super_zone") != super_zone:
                    continue
                c = cote(ici, (arrivee["x"], arrivee["y"]))
                if c is None:
                    s["sorties_hors_voisines"] += 1  # porte, téléporteur : pas un passage de bord
                    continue
                entree = ref.setdefault(cle_ici, {"ids": [], "sorties": {}})
                if ident not in entree["ids"]:
                    entree["ids"].append(ident)
                sortie = entree["sorties"].setdefault(c, {"vers": f"{arrivee['x']},{arrivee['y']}", "cases": [],
                                                          "controles": {}})
                arrivees[dest][{"droite": "gauche", "gauche": "droite", "bas": "haut", "haut": "bas"}[c]].add(case_dest)
                sorties_cases[ident][c].add(case)
                cellule = list(case_vers_grille(case)) if info.get("largeur", 15) == 15 else None
                if cellule and cellule not in sortie["cases"]:
                    sortie["cases"].append(cellule)
                s["sorties"] += 1
                ctl = sortie["controles"]
                # 3. version
                ctl["meme_version"] = ctl.get("meme_version", True) and carte["date"] == _date_client(ident)
                # 2. carte officielle
                cases = cases_officielles(ident)
                if cases is not None and case < len(cases):
                    officielle = cases[case]
                    ctl["praticable"] = ctl.get("praticable", True) and officielle["deplacement"] > 0
                    soleil = officielle["objet2"] in GFX_SOLEILS or officielle["objet1"] in GFX_SOLEILS
                    ctl["soleil_officiel"] = ctl.get("soleil_officiel", False) or soleil
                    s["controle_officiel"] += 1
                    s["praticable" if officielle["deplacement"] > 0 else "non_praticable"] += 1
                    s["avec_soleil" if soleil else "sans_soleil"] += 1
                    s[f"objet2_{officielle['objet2']}"] += 1
        # 4. sorties atteignables depuis chaque côté d'arrivée (cases praticables de la carte officielle)
        for cle_ici, entree in ref.items():
            for ident in entree["ids"]:
                cases = cases_officielles(ident)
                if cases is None:
                    continue
                largeur = index[ident].get("largeur", 15)
                for entree_cote, departs in arrivees[ident].items():
                    zone = joignables(cases, largeur, departs)
                    if not zone:
                        continue
                    for c, cases_sortie in sorties_cases[ident].items():
                        if c == entree_cote or c not in entree["sorties"]:
                            continue
                        if not (cases_sortie & zone):
                            bloquees = entree.setdefault("bloquees_depuis", {}).setdefault(entree_cote, [])
                            if c not in bloquees:
                                bloquees.append(c)
                                s["inatteignable_depuis_un_cote"] += 1
                        else:
                            s["atteignable_depuis_un_cote"] += 1
        # comparaison avec les « inatteignables selon le côté d'arrivée » notés en jeu
        for cle_mem, carte_mem in memoire.items():
            if not cle_mem.startswith(region + " "):
                continue
            calcule = (ref.get(cle_mem[len(region) + 1:]) or {}).get("bloquees_depuis", {})
            for entree_cote, cotes in (carte_mem.get("bloquees_depuis") or {}).items():
                for c in cotes:
                    s["bloque_vu_et_calcule" if c in calcule.get(entree_cote, []) else "bloque_vu_pas_calcule"] += 1
        # 1. nos observations
        for cle_ici, entree in ref.items():
            vu = (vues.get(cle_ici) or {}).get("sorties", {})
            mem = (memoire.get(f"{region} {cle_ici}") or {})
            for c, sortie in entree["sorties"].items():
                obs = vu.get(c)
                if obs and obs != BLOQUEE:
                    sortie["controles"]["vu_en_jeu"] = obs == sortie["vers"]
                    s["accord_vu" if obs == sortie["vers"] else "desaccord_vu"] += 1
                elif obs == BLOQUEE:
                    sortie["controles"]["vu_bloquee"] = True
                    s["starloco_mais_vu_bloquee"] += 1
                soleil_vu = (mem.get("soleils") or {}).get(c)
                if soleil_vu and sortie["cases"]:
                    proche = min(max(abs(soleil_vu[0] - a), abs(soleil_vu[1] - b)) for a, b in sortie["cases"])
                    sortie["controles"]["soleil_vu_proche"] = proche <= 2
                    s["soleil_vu_meme_case" if proche == 0 else "soleil_vu_proche" if proche <= 2 else "soleil_vu_loin"] += 1
        for cle_ici, entree in vues.items():
            for c, obs in (entree.get("sorties") or {}).items():
                if obs != BLOQUEE and c not in (ref.get(cle_ici) or {}).get("sorties", {}):
                    s["vu_en_jeu_absent_de_starloco"] += 1
        references[region] = ref
    return references, stats


_DATES: dict[int, str] = {}


def _date_client(ident: int) -> str | None:
    if not _DATES:
        for chemin in glob.glob(str(RACINE / "data" / "client_retro" / "maps" / "*.swf")):
            nom = Path(chemin).name
            _DATES[int(nom.split("_")[0])] = nom.split("_")[1].rstrip("X.swf")
    return _DATES.get(ident)


def rapport(stats: dict) -> str:
    lignes = ["# Véracité de la référence complétée des passages (StarLoco + cartes officielles)", "",
              "Produit par `tools/reference_complete.py` ; référence locale dans `data/references/` (non versionnée).", ""]
    for region, s in stats.items():
        controle = s["controle_officiel"] or 1
        lignes += [f"## {region}", "",
                   f"- Sorties de bord trouvées dans StarLoco : {s['sorties']} (portes et téléporteurs écartés : "
                   f"{s['sorties_hors_voisines']})",
                   f"- **Contre nos passages vus en jeu** : {s['accord_vu']} d'accord, {s['desaccord_vu']} en désaccord ; "
                   f"{s['vu_en_jeu_absent_de_starloco']} vus en jeu absents de StarLoco ; {s['starloco_mais_vu_bloquee']} "
                   "sorties que nous avons notées bloquées",
                   f"- **Cases des soleils retenues en jeu** : {s['soleil_vu_meme_case']} sur la case StarLoco, "
                   f"{s['soleil_vu_proche']} à 1-2 cases, {s['soleil_vu_loin']} plus loin",
                   f"- **Contre la carte officielle déchiffrée** ({s['controle_officiel']} sorties contrôlées) : "
                   f"{s['praticable']} praticables ({100 * s['praticable'] / controle:.0f} %), "
                   f"{s['avec_soleil']} avec un objet de soleil {GFX_SOLEILS} ({100 * s['avec_soleil'] / controle:.0f} %)",
                   f"- **Atteignables selon le côté d'arrivée** (cases praticables officielles) : "
                   f"{s['atteignable_depuis_un_cote']} liaisons entrée -> sortie atteignables, "
                   f"{s['inatteignable_depuis_un_cote']} inatteignables (falaise, rempart) ; contre les blocages notés en "
                   f"jeu selon le côté d'arrivée : {s['bloque_vu_et_calcule']} retrouvés, {s['bloque_vu_pas_calcule']} non",
                   f"- Objets les plus fréquents sur les cases de sortie (objet2) : "
                   + ", ".join(f"{k[7:]} x{v}" for k, v in s.most_common() if k.startswith("objet2_"))[:400], ""]
    return "\n".join(lignes) + "\n"


def main() -> None:
    references, stats = construire()
    SORTIE.mkdir(parents=True, exist_ok=True)
    for region, ref in references.items():
        chemin = SORTIE / f"{region.lower().replace(' ', '_')}.json"
        chemin.write_text(json.dumps(ref, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{chemin} : {len(ref)} cartes")
    RAPPORT.write_text(rapport(stats), encoding="utf-8")
    print(rapport(stats))


if __name__ == "__main__":
    main()
