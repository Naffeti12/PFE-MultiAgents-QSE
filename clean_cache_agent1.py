"""
clean_cache_agent1.py
======================
Nettoie UNIQUEMENT les entrees Agent1 du cache d'injection,
en verifiant d'abord dans QALITAS quels risques existent deja
pour eviter les doublons lors du prochain cycle.

Actions :
    1. Charge le cache output/injection_cache.json
    2. Affiche un rapport : entrees Agent1 vs Agent2 vs Agent4
    3. Connecte QALITAS et recupere tous les risques/opportunites actifs
    4. Pour chaque entree Agent1 du cache, verifie si le risque
       existe deja dans QALITAS (par correspondance de designation)
    5. Supprime du cache UNIQUEMENT les entrees Agent1
       -> Agent2 et Agent4 restent intacts (leurs actions ne seront pas recreees)
    6. Sauvegarde le cache nettoye

Usage :
    python clean_cache_agent1.py             # nettoyage reel
    python clean_cache_agent1.py --dry-run   # simulation, ne touche pas au cache
"""

import argparse
import json
import logging
import os
import sys
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loaders.qalitas_api_client import QalitasClient, DEFAULT_BASE_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

CACHE_PATH = os.path.join(os.path.dirname(__file__), "output", "injection_cache.json")


def load_env() -> dict:
    base_url = os.getenv("QALITAS_BASE_URL", DEFAULT_BASE_URL)
    username = os.getenv("QALITAS_USERNAME", "")
    password = os.getenv("QALITAS_PASSWORD", "")
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() == "QALITAS_BASE_URL":
                        base_url = v.strip()
                    elif k.strip() == "QALITAS_USERNAME":
                        username = v.strip()
                    elif k.strip() == "QALITAS_PASSWORD":
                        password = v.strip()
    return {"base_url": base_url, "username": username, "password": password}


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def find_in_qalitas(designation: str, qalitas_risks: list, threshold: float = 0.75) -> dict:
    """Cherche un risque dans QALITAS par similarite de designation."""
    best_score = 0.0
    best_match = None
    for r in qalitas_risks:
        d = r.get("Designation") or r.get("Title") or r.get("Intitule") or ""
        score = similarity(designation, d)
        if score > best_score:
            best_score = score
            best_match = r
    if best_score >= threshold:
        return {"match": best_match, "score": round(best_score, 2)}
    return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Affiche le rapport sans modifier le cache")
    parser.add_argument("--threshold", type=float, default=0.75,
                        help="Seuil de similarite pour detecter un doublon (defaut: 0.75)")
    args = parser.parse_args()

    # Charger le cache
    if not os.path.exists(CACHE_PATH):
        logger.info("Cache introuvable : %s — rien a nettoyer.", CACHE_PATH)
        sys.exit(0)

    with open(CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)

    # Segmenter par agent
    agent1_entries = {k: v for k, v in cache.items()
                      if isinstance(v, dict) and v.get("agent") == "agent1"}
    agent2_entries = {k: v for k, v in cache.items()
                      if isinstance(v, dict) and v.get("agent") == "agent2"}
    agent4_entries = {k: v for k, v in cache.items()
                      if isinstance(v, dict) and v.get("agent") == "agent4"}
    other_entries  = {k: v for k, v in cache.items()
                      if k not in agent1_entries and k not in agent2_entries
                      and k not in agent4_entries}

    print("\n" + "="*65)
    print("RAPPORT CACHE D'INJECTION")
    print("="*65)
    print(f"  Agent1 (R&O identifies)   : {len(agent1_entries):4d} entrees")
    print(f"  Agent2 (evaluations)      : {len(agent2_entries):4d} entrees")
    print(f"  Agent4 (actions correctiv): {len(agent4_entries):4d} entrees")
    print(f"  Autres                    : {len(other_entries):4d} entrees")
    print(f"  TOTAL                     : {len(cache):4d} entrees")
    print("="*65)

    if not agent1_entries:
        print("\nAucune entree Agent1 dans le cache.")
        print("-> Agent1 peut etre relance sans risque de doublon cache.")
        print("-> Les actions Agent2/Agent4 seront preservees.")
        sys.exit(0)

    # Connexion QALITAS pour verification des doublons
    print("\nConnexion QALITAS pour verification des doublons...")
    env = load_env()
    client = QalitasClient(
        base_url=env["base_url"],
        username=env["username"],
        password=env["password"],
    )
    if not client.login():
        logger.error("Echec authentification QALITAS.")
        sys.exit(1)
    logger.info("Authentifie : %s", env["username"])

    # Recuperer risques et opportunites
    risks = client._get("RiskOpportunity/GetEnabledRisks", params={"sourceId": ""})
    opps  = client._get("RiskOpportunity/GetEnabledOpportunities", params={"sourceId": ""})
    qalitas_all = []
    if isinstance(risks, list):
        qalitas_all.extend(risks)
    if isinstance(opps, list):
        qalitas_all.extend(opps)
    logger.info("QALITAS : %d risques + opportunites actifs trouves", len(qalitas_all))

    # Verifier chaque entree Agent1
    print("\n" + "-"*65)
    print("VERIFICATION DES ENTREES AGENT1 DANS QALITAS")
    print("-"*65)

    already_in_qalitas = []
    not_in_qalitas     = []

    for fp, entry in agent1_entries.items():
        label = entry.get("label", entry.get("intitule", fp[:60]))
        # Extraire la designation (label peut commencer par "[Agent1] ")
        designation = label.replace("[Agent1]", "").replace("[agent1]", "").strip()

        result = find_in_qalitas(designation, qalitas_all, args.threshold)
        if result:
            match = result["match"]
            score = result["score"]
            already_in_qalitas.append((fp, designation, match, score))
            state_str = match.get("StateStr", match.get("State", "?"))
            print(f"  [EXISTE] {designation[:55]}")
            print(f"           -> '{match.get('Designation','?')[:55]}'")
            print(f"              Similarite={score} | State={state_str} | GUID={match.get('Id','?')[:18]}...")
        else:
            not_in_qalitas.append((fp, designation))
            print(f"  [ABSENT] {designation[:60]}")

    print("-"*65)
    print(f"  Deja dans QALITAS : {len(already_in_qalitas)}")
    print(f"  Absents (a creer) : {len(not_in_qalitas)}")
    print("-"*65)

    # Rapport final et action
    print("\n" + "="*65)
    if args.dry_run:
        print("MODE DRY-RUN — cache non modifie.")
        print(f"Si tu lances sans --dry-run :")
        print(f"  -> {len(agent1_entries)} entrees Agent1 supprimees du cache")
        print(f"  -> {len(agent2_entries) + len(agent4_entries)} entrees Agent2/Agent4 preservees")
        print(f"  -> Agent1 recreera uniquement les {len(not_in_qalitas)} risques absents")
        print(f"     (les {len(already_in_qalitas)} deja presents seront detectes comme doublons par Agent1)")
    else:
        # Supprimer uniquement les entrees Agent1
        new_cache = {k: v for k, v in cache.items() if k not in agent1_entries}
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(new_cache, f, ensure_ascii=False, indent=2)

        print("CACHE NETTOYE")
        print(f"  Entrees Agent1 supprimees : {len(agent1_entries)}")
        print(f"  Entrees Agent2 conservees : {len(agent2_entries)}")
        print(f"  Entrees Agent4 conservees : {len(agent4_entries)}")
        print(f"\n  Tu peux maintenant relancer Agent1 ou le pipeline complet.")
        print(f"  Agent1 creera les {len(not_in_qalitas)} risques absents en state=1 (Identifie).")
        if already_in_qalitas:
            print(f"  Les {len(already_in_qalitas)} risques deja presents seront ignores")
            print(f"  (detectes comme doublons par le fingerprint Agent1).")
        print(f"\n  Commandes :")
        print(f"    python qalitas_main.py          # pipeline complet")
        print(f"    python qalitas_main.py --agent1  # Agent1 seul (si supporte)")
    print("="*65 + "\n")


if __name__ == "__main__":
    main()
