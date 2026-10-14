"""Importe les sorts des 12 classes de Dofus Rétro depuis solomonk.fr.

    .venv\\Scripts\\python -m tools.import_sorts

Écrit config/sorts/solomonk.json (données de référence, versionnées avec le projet) :
- pour chaque classe, ses sorts dans l'ordre de la page de la classe, qui est aussi celui de la fenêtre
  « Tes sorts » du jeu (vérifié sur le Crâ le 28/09 : Recul, Empoisonnée, Magique, Glacée, Enflammée,
  Tir Eloigné, Expiation, Oeil de Taupe, Tir Critique, Immobilisation) ;
- pour chaque sort, ses 6 niveaux : PA, portée, lancer en ligne, ligne de vue, portée modifiable, cellule libre,
  lancers par tour et par cible, relance, critique et échec, états requis ou interdits, niveau du personnage
  requis, effets normaux et critiques (valeurs, durée, élément, zone, texte).

Sources (solomonk.fr, fansite Dofus Rétro, données tirées du client Rétro ; robots.txt : tout autorisé) :
- pages des classes /fr/classe/<n>/<nom> : liste ordonnée des sorts (data-spellid) et leur nom ;
- page de chaque sort /fr/sort/<id>/<nom> : tableau `var levels` (les 6 niveaux, au format du client) ;
- assets/fr/effects.js et assets/fr/states.js : texte des effets et nom des états.
Une pause sépare les requêtes (--pause, 1 s par défaut). Rien d'autre n'est gardé (ni images, ni descriptions).
"""

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import date
from pathlib import Path

SITE = "https://solomonk.fr"
CLASS_PAGE = "https://www.solomonk.fr/fr/classe/{id}/{slug}"
SPELL_PAGE = SITE + "/fr/sort/{id}/sort-{id}"  # le site ne regarde pas le nom dans l'adresse
EFFECTS, STATES = SITE + "/assets/fr/effects.js", SITE + "/assets/fr/states.js"
OUT = Path(__file__).resolve().parents[1] / "config" / "sorts" / "solomonk.json"

CLASSES = {1: ("feca", "Féca"), 2: ("osamodas", "Osamodas"), 3: ("enutrof", "Enutrof"), 4: ("sram", "Sram"),
           5: ("xelor", "Xélor"), 6: ("ecaflip", "Ecaflip"), 7: ("eniripsa", "Eniripsa"), 8: ("iop", "Iop"),
           9: ("cra", "Crâ"), 10: ("sadida", "Sadida"), 11: ("sacrieur", "Sacrieur"), 12: ("pandawa", "Pandawa")}
ELEMENTS = {"W": "eau", "E": "terre", "A": "air", "F": "feu", "N": "neutre"}
SHAPES = {"P": "point", "C": "cercle", "L": "ligne", "X": "croix", "T": "ligne_perpendiculaire", "D": "damier",
          "O": "anneau", "R": "rectangle"}
SIZES = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"  # taille de zone codée par le client
WHOLE_MAP = SIZES.index("_")  # 63 : Trêve, Perception... (toute la carte)

# Champs d'un niveau dans `var levels` (sens relevé dans le script d'affichage de la page du sort).
EFFECTS_I, CRITICAL_EFFECTS_I, PA_I, RANGE_MIN_I, RANGE_MAX_I, CRITICAL_I, FAILURE_I = 0, 1, 2, 3, 4, 5, 6
IN_LINE_I, SIGHT_I, FREE_CELL_I, RANGE_MODIFIABLE_I = 7, 8, 9, 10
PER_TURN_I, PER_TARGET_I, COOLDOWN_I, ZONES_I, REQUIRED_STATES_I, FORBIDDEN_STATES_I = 12, 13, 14, 15, 16, 17
REQUIRED_LEVEL_I, FAILURE_ENDS_TURN_I = 18, 19


def get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (autobot, import de sorts)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def key_of(name: str) -> str:
    """« Flèche d'Expiation » -> « fleche_d_expiation » (identifiant des sorts dans config/sorts.yaml)."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")


def js_string(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)  # \' -> '


def effect_texts(script: str) -> dict[int, dict]:
    """effects.js : e[ID] = {d:"Dommages : #1{~1~2 à }#2 (feu)",c:0,o:"",t:true,j:true,e:"F"};"""
    texts = {}
    for found in re.finditer(r'^e\[(\d+)\] = \{d:"((?:[^"\\]|\\.)*)"(.*?)\};$', script, re.M):
        element = re.search(r'e:"(\w)"', found[3])
        texts[int(found[1])] = {"texte": js_string(found[2]), "element": ELEMENTS.get(element[1]) if element else None}
    if not texts:
        raise RuntimeError("textes des effets introuvables dans effects.js")
    return texts


def state_names(script: str) -> dict[int, str]:
    names = {int(i): js_string(n) for i, n in re.findall(r'^s\[(\d+)\] = \{n:"((?:[^"\\]|\\.)*)"', script, re.M)}
    if not names:
        raise RuntimeError("noms des états introuvables dans states.js")
    return names


def class_spells(html: str) -> list[tuple[int, str]]:
    """(id, nom) des sorts de la classe, dans l'ordre de la page."""
    found = re.findall(r'data-spellid="(\d+)">.*?font-weight-bold">(.*?) #\1</div>', html, re.S)
    if not found:
        raise RuntimeError("sorts introuvables sur la page de la classe")
    return [(int(i), name.strip()) for i, name in found]


def spell_levels(html: str) -> list[list] | None:
    """Les 6 niveaux du sort ; None si le site les donne tronqués (sorts à nombreux effets aléatoires, comme la
    Roulette de l'Ecaflip : la chaîne est coupée vers 2600 caractères, avant le coût en PA)."""
    found = re.search(r"var levels = \[(.*?)\];?\s*$", html, re.M)
    if found is None:
        raise RuntimeError("tableau « var levels » introuvable")
    try:
        return [json.loads(js_string(text)) for text in re.findall(r"'((?:[^'\\]|\\.)*)'", found[1])]
    except json.JSONDecodeError:
        return None


def describe(template: str, params: list, states: dict[int, str]) -> str:
    """Texte d'un effet : #1 #2 #3 remplacés par les paramètres, {~1~2 à } affiché si ces paramètres existent."""
    def value(n: int):
        v = params[n - 1] if n <= len(params) else None
        if n == 3 and template.startswith("Etat") and v is not None:
            return states.get(v, f"n°{v}")
        return v

    def condition(found: re.Match) -> str:
        needed = [int(n) for n in re.findall(r"~(\d)", found[1])]
        return found[2] if all(value(n) not in (None, 0) for n in needed) else ""

    text = re.sub(r"\{((?:~\d)+)([^}]*)\}", condition, template)
    return re.sub(r"#(\d)", lambda m: "" if value(int(m[1])) is None else str(value(int(m[1]))), text).strip()


def zone_info(shape: str, size: int | None) -> dict:
    """Nombre de cases et description d'une zone, d'après l'aperçu de zone des pages de sorts de Solomonk : cercle n =
    cases à n ou moins de la cible ; ligne n = la cible et n cases au-delà, dans l'axe du tir ; croix n = n cases par
    branche (Flèche Enflammée « Le » : ligne de 5 cases). Taille 63 (« _ », la dernière du codage) : toute la carte.
    `cases` : None quand il dépend de la carte."""
    if shape == "point" or size == 0:
        return {"cases": 1, "texte": "la cible seule"}
    if size is None:
        return {"cases": None, "texte": f"forme {shape}, taille inconnue"}
    if size == WHOLE_MAP:
        return {"cases": None, "texte": "toute la carte" if shape == "cercle" else f"{shape} jusqu'au bord de la carte"}
    cells = {"cercle": 2 * size * size + 2 * size + 1, "ligne": size + 1, "croix": 4 * size + 1,
             "ligne_perpendiculaire": 2 * size + 1, "anneau": 4 * size,
             "damier": 1 + sum(4 * k for k in range(2, size + 1, 2))}.get(shape)
    texts = {"cercle": f"cercle de rayon {size} autour de la cible",
             "ligne": f"ligne : la cible et {size} case(s) derrière, dans l'axe du tir",
             "croix": f"croix de {size} case(s) par branche autour de la cible",
             "ligne_perpendiculaire": f"ligne en travers du tir, {size} case(s) de chaque côté de la cible",
             "anneau": f"anneau à {size} cases de la cible", "damier": f"damier de rayon {size} autour de la cible"}
    text = texts.get(shape, f"forme {shape}, taille {size}")
    return {"cases": cells, "texte": f"{text} ({cells} cases)" if cells else text}


def zone(code: str) -> dict:
    shape, size = SHAPES.get(code[0], code[0]), SIZES.index(code[1]) if code[1] in SIZES else None
    return {"forme": shape, "taille": size, **zone_info(shape, size)}


def recompute(path: Path = OUT) -> int:
    """Recalcule les champs déduits (zones : nombre de cases, description) du fichier déjà importé, sans rien
    télécharger. Renvoie le nombre de zones mises à jour."""
    data = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    for spell in data["sorts"].values():
        for level in spell["niveaux"]:
            for effect in level["effets"] + level["effets_critiques"]:
                if effect.get("zone"):
                    effect["zone"].update(zone_info(effect["zone"]["forme"], effect["zone"]["taille"]))
                    count += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return count


def effect(raw: list, zone_code: str, texts: dict[int, dict], states: dict[int, str]) -> dict:
    effect_id, p1, p2, p3, turns, chance = raw[:6]
    known = texts.get(effect_id, {})
    result = {"id": effect_id, "texte": describe(known.get("texte", f"effet n°{effect_id}"), [p1, p2, p3], states),
              "min": p1, "max": p2 if p2 is not None else p1}
    if p3 is not None:
        result["valeur3"] = p3
    if known.get("element"):
        result["element"] = known["element"]
    if turns:
        result["tours"] = turns
    if chance:
        result["chance"] = chance  # % de chances de cet effet (effets aléatoires)
    if len(raw) > 8 and raw[8]:
        result["des"] = raw[8]
    if zone_code:
        result["zone"] = zone(zone_code)
    return result


def level(number: int, raw: list, texts: dict[int, dict], states: dict[int, str]) -> dict:
    normal, critical = raw[EFFECTS_I] or [], raw[CRITICAL_EFFECTS_I] or []
    zones = re.findall("..", raw[ZONES_I] or "")  # 2 caractères par effet : effets normaux puis critiques
    zones += [""] * (len(normal) + len(critical) - len(zones))

    def count(value):
        return value or None  # 0 : illimité / sans délai

    return {
        "niveau": number,
        "pa": raw[PA_I],
        "portee": [raw[RANGE_MIN_I], raw[RANGE_MAX_I]],
        "lancer_en_ligne": bool(raw[IN_LINE_I]),
        "ligne_de_vue": bool(raw[SIGHT_I]),
        "cellule_libre": bool(raw[FREE_CELL_I]),
        "portee_modifiable": bool(raw[RANGE_MODIFIABLE_I]),
        "lancers_par_tour": count(raw[PER_TURN_I]),
        "lancers_par_cible": count(raw[PER_TARGET_I]),
        "relance": count(raw[COOLDOWN_I]),
        "critique": count(raw[CRITICAL_I]),  # 1 chance sur N
        "echec": count(raw[FAILURE_I]),
        "echec_fin_de_tour": bool(raw[FAILURE_ENDS_TURN_I]),
        "niveau_requis": raw[REQUIRED_LEVEL_I],
        "etats_requis": [states.get(s, f"n°{s}") for s in raw[REQUIRED_STATES_I] or []],
        "etats_interdits": [states.get(s, f"n°{s}") for s in raw[FORBIDDEN_STATES_I] or []],
        "effets": [effect(e, z, texts, states) for e, z in zip(normal, zones)],
        "effets_critiques": [effect(e, z, texts, states) for e, z in zip(critical, zones[len(normal):])],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pause", type=float, default=1.0, help="secondes entre deux requêtes (défaut : 1)")
    parser.add_argument("--recalculer", action="store_true",
                        help="sans rien télécharger : recalcule les champs déduits (zones) de config/sorts/solomonk.json")
    args = parser.parse_args()
    if args.recalculer:
        print(f"{recompute()} zones mises à jour -> {OUT}")
        return 0

    texts, states = effect_texts(get(EFFECTS)), state_names(get(STATES))
    classes, spells, incomplete = {}, {}, []
    for class_id, (slug, name) in CLASSES.items():
        time.sleep(args.pause)
        found = class_spells(get(CLASS_PAGE.format(id=class_id, slug=slug)))
        classes[slug] = {"nom": name, "solomonk": class_id, "sorts": [spell_id for spell_id, _ in found]}
        print(f"{name} : {len(found)} sorts", flush=True)
        for spell_id, spell_name in found:
            if str(spell_id) in spells:
                spells[str(spell_id)]["classes"].append(slug)
                continue
            time.sleep(args.pause)
            levels = spell_levels(get(SPELL_PAGE.format(id=spell_id)))
            spells[str(spell_id)] = {"nom": spell_name, "cle": key_of(spell_name), "classes": [slug],
                                     "niveaux": [level(n, raw, texts, states) for n, raw in enumerate(levels or [], 1)]}
            if levels is None:
                spells[str(spell_id)]["incomplet"] = "niveaux tronqués sur le site"
                incomplete.append(spell_name)
            print(f"  {spell_name} ({len(levels) if levels else 'niveaux tronqués sur le site'})", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = {"source": f"solomonk.fr (Dofus Rétro), importé le {date.today().isoformat()} par tools/import_sorts.py",
            "classes": classes, "sorts": spells}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(spells)} sorts de {len(classes)} classes -> {OUT}")
    if incomplete:
        print(f"Sans niveaux (tronqués sur le site) : {', '.join(incomplete)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
