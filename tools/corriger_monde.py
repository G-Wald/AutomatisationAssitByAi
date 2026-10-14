"""Corrige la référence du Monde des Douze (config/monde/monde_des_douze.json) d'après la langue des cartes du client
Dofus Retro (data/client_retro/lang, copiée par tools/cartes_client.py --copier). Accord de l'utilisateur le 12/10.

- Cartes : exactement les cartes extérieures du jeu. Retirées : celles qui n'existent qu'en intérieur ou pas du tout
  (faux voisins pour les trajets) ; ajoutées : les extérieures absentes (île de Nowel, Plaine de Cania, temples...).
- Zone de chaque carte : la sous-zone du jeu de sa carte extérieure ; plusieurs à la même coordonnée (couches : village
  des Zoths, canopée du Kimbo...), celle que nommait la référence, sinon la plus ancienne ; « aussi » : les autres. Noms exacts du jeu ; un nom porté
  par deux sous-zones (« Centre-ville » à Bonta et à Brâkmar) reçoit le nom de sa ville, l'atlas regroupant les zones
  par nom.
- Zones : une par sous-zone du jeu. Monstres, région et id repris des zones de la référence qui la couvrent (au moins
  la moitié de ses cartes) ; une zone regroupée découpée (Brâkmar) donne sa liste de monstres à chaque morceau.
- Gardé tel quel : objets (ressources) et sorties des cartes, liste des monstres, lieux (la zone d'un donjon suit sa
  carte). Incarnam n'est pas touché.

    .venv/Scripts/python -m tools.corriger_monde            (bilan seulement)
    .venv/Scripts/python -m tools.corriger_monde --ecrire   (copie de l'original dans data/client_retro, puis écriture)

Ensuite, relancer tools/completer_references.py --ecrire (identifiants, soleils connus, portes, placements) :
cette correction repart de l'original et ne les a pas.
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

from tools.cartes_client import DOSSIER, Langue, ecrire_coords, lire_coords, normaliser

RACINE = Path(__file__).resolve().parents[1]
REFERENCE = RACINE / "config" / "monde" / "monde_des_douze.json"
SAUVEGARDE = DOSSIER / "monde_des_douze_avant_correction.json"
MONDE_DES_DOUZE = 0  # super-zone « sua » de la langue
COUVERTURE = 0.5  # part des cartes d'une sous-zone dans une zone de la référence pour lui en reprendre monstres et région
# Région (aire) du jeu -> région de la référence, quand le nom diffère.
REGIONS = {"Ile d'Otomaï": "Île d'Otomaï", "L'île des Wabbits": "Île des Wabbits", "L'île de Moon": "Île de Moon",
           "Pandala Neutre": "Pandala", "Pandala Feu": "Pandala", "Pandala Terre": "Pandala", "Pandala Air": "Pandala",
           "Pandala Eau": "Pandala"}


def slug(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(c for c in texte if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "_", texte).strip("_")


def corriger(reference: dict, langue: Langue) -> tuple[dict, dict]:
    """(nouvelle référence, bilan). Ne modifie pas `reference`."""
    exterieures, _ = langue.par_coordonnees(MONDE_DES_DOUZE)

    def sous_zone(id_carte: int) -> int:
        return langue.cartes[id_carte]["sa"]

    def aire(sa: int) -> str:
        return langue.zones.get(langue.sous_zones[sa].get("a"), {}).get("n", "?")

    noms: dict[str, set[int]] = defaultdict(set)  # nom (sans accents ni majuscules) -> sous-zones qui le portent
    for cartes in exterieures.values():
        for id_carte, _ in cartes:
            sa = sous_zone(id_carte)
            noms[normaliser(langue.sous_zones[sa]["n"])].add(sa)

    def nom(sa: int) -> str:
        """Nom du jeu ; porté par deux sous-zones (« Centre-ville », « Quartier de la Milice » / « de la milice », à
        Bonta et à Brâkmar) : avec le nom de la ville, l'atlas regroupant les zones par nom."""
        base = langue.sous_zones[sa]["n"]
        return f"{base} ({aire(sa)})" if len(noms[normaliser(base)]) > 1 else base

    anciennes_cartes = reference["cartes"]

    def principale_de(coords: tuple[int, int], cartes: list[tuple[int, str]]) -> int:
        """Sous-zone de la carte qu'on voit en jeu à cette coordonnée : plusieurs cartes extérieures (couches : village
        des Zoths, canopée du Kimbo, prismes), celle que nomme déjà la référence (relevée sur une carte de Retro) ;
        sinon la plus ancienne (plus petit id)."""
        ancienne = anciennes_cartes.get(ecrire_coords(coords)) or {}
        voulues = {normaliser(n) for n in [ancienne.get("zone"), *(ancienne.get("aussi") or [])] if n}
        for id_carte, nom_sous_zone in cartes:
            if normaliser(nom_sous_zone) in voulues:
                return sous_zone(id_carte)
        return sous_zone(cartes[0][0])

    principale = {coords: principale_de(coords, cartes) for coords, cartes in exterieures.items()}
    # Cartes de chaque zone : partout où sa sous-zone a une carte extérieure (principale ou couche, comme les zones du
    # site : le village des Zoths n'est la carte principale nulle part, mais ses monstres y sont).
    par_sous_zone: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for coords, cartes in exterieures.items():
        for sa in dict.fromkeys(sous_zone(i) for i, _ in cartes):
            par_sous_zone[sa].append(coords)

    anciennes = reference["zones"]
    cartes_de = [{lire_coords(c) for c in zone["cartes"]} for zone in anciennes]
    zones, ids, bilan_zones = [], set(), {"reprises": [], "renommees": [], "decoupees": defaultdict(list), "creees": []}
    nom_de_sa: dict[int, str] = {}
    for sa in sorted(par_sous_zone, key=lambda s: (aire(s), nom(s))):
        coords = sorted(par_sous_zone[sa])
        ensemble = set(coords)
        couvrantes = sorted((k for k, c in enumerate(cartes_de) if len(c & ensemble) >= COUVERTURE * len(ensemble)),
                            key=lambda k: -len(cartes_de[k] & ensemble))
        # Une couche (village des Zoths) partage ses coordonnées avec la zone de base (Jungle obscure) : la zone du même
        # nom passe seule, sans les monstres de l'autre.
        meme_nom = [k for k in couvrantes if normaliser(anciennes[k]["nom"]) == normaliser(langue.sous_zones[sa]["n"])]
        couvrantes = meme_nom or couvrantes
        nouveau = nom(sa)
        nom_de_sa[sa] = nouveau
        monstres = list(dict.fromkeys(m for k in couvrantes for m in anciennes[k].get("monstres") or []))
        region = anciennes[couvrantes[0]]["region"] if couvrantes else REGIONS.get(aire(sa), aire(sa))
        if couvrantes:
            ancienne = anciennes[couvrantes[0]]
            morceaux = [s for s in par_sous_zone if len(cartes_de[couvrantes[0]] & set(par_sous_zone[s]))
                        >= COUVERTURE * len(par_sous_zone[s])]
            if len(morceaux) > 1 and normaliser(ancienne["nom"]) != normaliser(langue.sous_zones[sa]["n"]):
                identifiant = f"{ancienne['id']}_{slug(nouveau)}"
                bilan_zones["decoupees"][ancienne["nom"]].append(nouveau)
            else:
                identifiant = ancienne["id"]
                (bilan_zones["reprises"] if ancienne["nom"] == nouveau else bilan_zones["renommees"]).append(
                    (ancienne["nom"], nouveau) if ancienne["nom"] != nouveau else nouveau)
        else:
            identifiant = f"zone_{slug(region)}_{slug(nouveau)}"
            bilan_zones["creees"].append(f"{nouveau} ({region}, {len(coords)} cartes)")
        base, n = identifiant, 2
        while identifiant in ids:
            identifiant, n = f"{base}_{n}", n + 1
        ids.add(identifiant)
        zones.append({"id": identifiant, "nom": nouveau, "region": region, "cartes": [ecrire_coords(c) for c in coords],
                      "monstres": monstres})

    cartes, ajoutees, zone_changee = {}, [], 0
    for coords in sorted(exterieures):
        cle = ecrire_coords(coords)
        entree = dict(anciennes_cartes.get(cle) or {"sorties": {}})
        if cle not in anciennes_cartes:
            ajoutees.append(f"{cle} ({nom_de_sa[principale[coords]]})")
        zone = nom_de_sa[principale[coords]]
        zone_changee += entree.get("zone") != zone
        entree["zone"] = zone
        autres = list(dict.fromkeys(nom(sous_zone(i)) for i, _ in exterieures[coords]))
        autres = [a for a in autres if a != zone]
        entree.pop("aussi", None)
        if autres:
            entree["aussi"] = autres
        cartes[cle] = {k: entree[k] for k in ("sorties", "objets", "zone", "aussi") if k in entree}
    retirees = [{"carte": cle, "zone": e.get("zone"), "objets": [o.get("nom") for o in e.get("objets", [])]}
                for cle, e in anciennes_cartes.items() if lire_coords(cle) not in exterieures]

    lieux, lieux_hors, lieux_zone = [], [], 0
    for lieu in reference.get("lieux", []):
        lieu = dict(lieu)
        if lieu.get("carte") not in cartes:
            lieux_hors.append(f"{lieu['type']} {lieu['nom']} ({lieu.get('carte')})")
        elif "zone" in lieu and lieu["zone"] != cartes[lieu["carte"]]["zone"]:
            lieu["zone"] = cartes[lieu["carte"]]["zone"]
            lieux_zone += 1
        lieux.append(lieu)

    source = dict(reference["source"])
    source["corrige_le"] = "2026-10-12"
    source["correction"] = (f"cartes extérieures et zones (noms exacts) d'après la langue des cartes du client Dofus "
                            f"Retro 1.49.6 (maps {langue.version}) ; monstres, ressources et lieux repris du site ; "
                            "remparts et soleils : appris en jeu (data/carte.json)")
    corrigee = {
        "region": reference["region"],
        "source": source,
        "regions": sorted({z["region"] for z in zones}),
        "zones": zones,
        "monstres": reference["monstres"],
        "lieux": lieux,
        "cartes": dict(sorted(cartes.items(), key=lambda item: lire_coords(item[0]))),
    }
    bilan = {
        "cartes": (len(anciennes_cartes), len(cartes)),
        "zones": (len(anciennes), len(zones)),
        "retirees": retirees,
        "ajoutees": ajoutees,
        "zone_changee": zone_changee,
        "renommees": bilan_zones["renommees"],
        "decoupees": dict(bilan_zones["decoupees"]),
        "creees": bilan_zones["creees"],
        "anciennes_sans_suite": [z["nom"] for k, z in enumerate(anciennes)
                                 if not any(z["id"] == n["id"] or n["id"].startswith(z["id"] + "_") for n in zones)],
        "lieux_hors_cartes": lieux_hors,
        "lieux_zone_corrigee": lieux_zone,
        "regions_ajoutees": sorted(set(corrigee["regions"]) - set(reference["regions"])),
        "regions_retirees": sorted(set(reference["regions"]) - set(corrigee["regions"])),
    }
    return corrigee, bilan


def afficher(bilan: dict) -> None:
    print(f"Cartes : {bilan['cartes'][0]} -> {bilan['cartes'][1]} ; zones : {bilan['zones'][0]} -> {bilan['zones'][1]}")
    print(f"Retirées ({len(bilan['retirees'])}) :")
    for r in bilan["retirees"]:
        print(f"  {r['carte']} ({r['zone']}){' objets : ' + ', '.join(r['objets']) if r['objets'] else ''}")
    print(f"Ajoutées ({len(bilan['ajoutees'])}) : {', '.join(bilan['ajoutees'])}")
    print(f"Cartes dont la zone change : {bilan['zone_changee']}")
    print(f"Zones renommées ({len(bilan['renommees'])}) : " + " ; ".join(f"{a} -> {b}" for a, b in bilan["renommees"]))
    print(f"Zones découpées ({len(bilan['decoupees'])}) :")
    for ancienne, morceaux in bilan["decoupees"].items():
        print(f"  {ancienne} -> {', '.join(morceaux)}")
    print(f"Zones créées ({len(bilan['creees'])}) : {' ; '.join(bilan['creees'])}")
    print(f"Anciennes zones sans suite ({len(bilan['anciennes_sans_suite'])}) : {', '.join(bilan['anciennes_sans_suite'])}")
    print(f"Lieux sur une carte retirée (gardés) : {bilan['lieux_hors_cartes'] or 'aucun'}")
    print(f"Lieux dont la zone suit la carte : {bilan['lieux_zone_corrigee']}")
    print(f"Régions ajoutées : {bilan['regions_ajoutees'] or 'aucune'} ; retirées : {bilan['regions_retirees'] or 'aucune'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ecrire", action="store_true", help="écrit la référence corrigée (copie de l'original avant)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    # Toujours à partir de l'original (copie faite à la première écriture) : l'outil peut être relancé.
    texte = (SAUVEGARDE if SAUVEGARDE.exists() else REFERENCE).read_text(encoding="utf-8")
    corrigee, bilan = corriger(json.loads(texte), Langue.charger())
    afficher(bilan)
    if args.ecrire:
        if not SAUVEGARDE.exists():
            SAUVEGARDE.write_text(texte, encoding="utf-8")
        REFERENCE.write_text(json.dumps(corrigee, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Écrit : {REFERENCE} (original : {SAUVEGARDE})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
