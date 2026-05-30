"""
fix_agent1_state.py
====================
Corrige les risques crees par Agent 1 en etat Brouillon (state=0)
en les passant a l'etat Identifie (state=1) pour les rendre visibles dans QALITAS.

Logique :
    1. Connexion QALITAS via le client existant
    2. Recuperation de tous les risques via plusieurs endpoints
    3. Detection des risques en etat Brouillon (State=0 ou StateStr contient "brouillon")
    4. Mise a jour via POST RiskOpportunity/Edit pour chaque risque concerne
    5. Rapport final

Usage :
    python fix_agent1_state.py
    python fix_agent1_state.py --dry-run    (simulation sans ecriture)
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loaders.qalitas_api_client import QalitasClient, DEFAULT_BASE_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


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
                    k, v = k.strip(), v.strip()
                    if k == "QALITAS_BASE_URL":
                        base_url = v
                    elif k == "QALITAS_USERNAME":
                        username = v
                    elif k == "QALITAS_PASSWORD":
                        password = v
    return {"base_url": base_url, "username": username, "password": password}


def get_all_risks_including_brouillon(client: QalitasClient) -> list:
    """
    Recupere TOUS les risques QALITAS y compris les Brouillons.
    Essaie plusieurs endpoints par ordre de fiabilite.
    """
    risks = []

    # Endpoint 1 : risques actifs (state >= 1)
    try:
        enabled = client._get("RiskOpportunity/GetEnabledRisks", params={"sourceId": ""})
        if isinstance(enabled, list):
            logger.info("GetEnabledRisks : %d risques actifs trouves", len(enabled))
            risks.extend(enabled)
    except Exception as exc:
        logger.warning("GetEnabledRisks echec : %s", exc)

    # Endpoint 2 : cartographie complete (inclut parfois les brouillons)
    try:
        mapping = client._get(
            "RiskOpportunityEvaluation/GetAllRiskAppreciation",
            params={"nature": 0, "employeeId": ""},
        )
        if isinstance(mapping, list):
            logger.info("GetAllRiskAppreciation : %d entrees trouves", len(mapping))
            existing_ids = {r.get("Id") for r in risks}
            for row in mapping:
                rid = row.get("RiskOpportunityId") or row.get("Id")
                if rid and rid not in existing_ids:
                    row["Id"] = rid
                    risks.append(row)
                    existing_ids.add(rid)
    except Exception as exc:
        logger.warning("GetAllRiskAppreciation echec : %s", exc)

    # Endpoint 3 : opportunites en brouillon
    try:
        opps = client._get("RiskOpportunity/GetEnabledOpportunities", params={"sourceId": ""})
        if isinstance(opps, list):
            logger.info("GetEnabledOpportunities : %d opportunites trouvees", len(opps))
            existing_ids = {r.get("Id") for r in risks}
            for row in opps:
                rid = row.get("Id")
                if rid and rid not in existing_ids:
                    risks.append(row)
                    existing_ids.add(rid)
    except Exception as exc:
        logger.warning("GetEnabledOpportunities echec : %s", exc)

    return risks


def is_brouillon(risk: dict) -> bool:
    """Retourne True si le risque est en etat Brouillon (state=0)."""
    state = risk.get("State") or risk.get("state") or risk.get("EtatId")
    state_str = str(risk.get("StateStr", risk.get("Statut", ""))).lower()

    if state is not None:
        try:
            return int(state) == 0
        except (ValueError, TypeError):
            pass

    return "brouillon" in state_str or "draft" in state_str


def get_csrf_token(session, base_url: str, risk_id: str) -> str:
    """Extrait le CSRF token depuis la page d'edition du risque."""
    try:
        resp = session.get(
            f"{base_url}/RiskOpportunity/Edit/{risk_id}",
            timeout=30,
            verify=False,
            headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        )
        match = re.search(
            r'<input[^>]+name=["\']__RequestVerificationToken["\'][^>]+value=["\']([^"\']+)["\']',
            resp.text, re.I
        )
        if match:
            return match.group(1)
        logger.warning("CSRF non trouve pour risque %s", risk_id)
    except Exception as exc:
        logger.error("Erreur GET page edition risque %s : %s", risk_id, exc)
    return ""


def update_risk_state(session, base_url: str, risk: dict, dry_run: bool = False) -> bool:
    """
    Met a jour le state d'un risque de Brouillon (0) vers Identifie (1).
    Utilise POST RiskOpportunity/Edit avec les champs du risque existant.
    """
    risk_id = risk.get("Id") or risk.get("RiskOpportunityId", "")
    designation = risk.get("Designation", risk.get("Title", risk.get("Intitule", "?")))

    if not risk_id:
        logger.warning("GUID manquant pour risque : %s", designation)
        return False

    if dry_run:
        logger.info("[DRY-RUN] Mettrait a jour state=1 pour : %s (%s)", designation, risk_id)
        return True

    # GET la page d'edition pour extraire CSRF + champs hidden
    csrf = get_csrf_token(session, base_url, risk_id)
    if not csrf:
        logger.error("Impossible de mettre a jour %s : CSRF manquant", risk_id)
        return False

    # Construction du payload minimal avec le state mis a jour
    payload = {
        "__RequestVerificationToken": csrf,
        "Id":          risk_id,
        "State":       "1",  # Identifie
        "Designation": designation[:200],
        "Nature":      str(risk.get("Nature", risk.get("RiskOpportunityNature", 0))),
        "ProcessId":   risk.get("ProcessId", risk.get("ProcessGuid", "")),
        "Q":           str(risk.get("Q", "False")),
        "S":           str(risk.get("S", "False")),
        "E":           str(risk.get("E", "False")),
        "H":           "False",
        "Description": risk.get("Description", ""),
        "Cause":       risk.get("Cause", ""),
        "Consequence": risk.get("Consequence", ""),
        "Source":      str(risk.get("Source", "0")),
        "SourceId":    risk.get("SourceId", ""),
        "TypesId":     risk.get("TypeId", ""),
        "CategoryId":  risk.get("CategoryId", ""),
    }

    try:
        resp = session.post(
            f"{base_url}/RiskOpportunity/Edit",
            data={k: v for k, v in payload.items() if v is not None},
            timeout=30,
            verify=False,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{base_url}/RiskOpportunity/Edit/{risk_id}",
            },
        )
        body = resp.text.strip() if resp.text else ""

        if body.startswith("true") or resp.status_code in (200, 302):
            if not body.startswith("false"):
                logger.info("OK state=1 : %s (%s)", designation[:60], risk_id)
                return True

        logger.warning("Echec update state pour %s : HTTP %s | %s",
                       risk_id, resp.status_code, body[:120])
        return False

    except Exception as exc:
        logger.error("Erreur POST Edit risque %s : %s", risk_id, exc)
        return False


def main():
    parser = argparse.ArgumentParser(description="Corrige le state des risques Agent1 Brouillon -> Identifie")
    parser.add_argument("--dry-run", action="store_true", help="Simulation sans ecriture")
    args = parser.parse_args()

    env = load_env()
    logger.info("Connexion QALITAS : %s", env["base_url"])

    client = QalitasClient(
        base_url=env["base_url"],
        username=env["username"],
        password=env["password"],
    )

    # Authentification obligatoire avant tout appel API
    logger.info("Authentification en cours...")
    if not client.login():
        logger.error("Echec de l'authentification QALITAS. Verifiez les credentials dans .env")
        sys.exit(1)
    logger.info("Authentification reussie.")

    # Recuperer tous les risques
    logger.info("Recuperation des risques QALITAS...")
    all_risks = get_all_risks_including_brouillon(client)
    logger.info("Total risques recuperes : %d", len(all_risks))

    # Filtrer les Brouillons
    brouillons = [r for r in all_risks if is_brouillon(r)]
    logger.info("Risques en etat Brouillon (state=0) : %d", len(brouillons))

    if not brouillons:
        logger.info("Aucun risque en Brouillon trouve — QALITAS est deja propre.")
        logger.info("Note : si les risques Agent1 ne sont toujours pas visibles,")
        logger.info("       ils sont peut-etre dans un endpoint non accessible par API.")
        return

    # Mise a jour
    ok_count = 0
    fail_count = 0

    for risk in brouillons:
        designation = risk.get("Designation", risk.get("Title", "?"))
        logger.info("Traitement : %s", designation[:80])
        success = update_risk_state(client._session, env["base_url"], risk, dry_run=args.dry_run)
        if success:
            ok_count += 1
        else:
            fail_count += 1

    print("\n" + "="*60)
    print(f"RAPPORT FINAL {'[DRY-RUN] ' if args.dry_run else ''}")
    print("="*60)
    print(f"Risques Brouillon trouves  : {len(brouillons)}")
    print(f"Mis a jour avec succes     : {ok_count}")
    print(f"Echecs                     : {fail_count}")
    if not args.dry_run and ok_count > 0:
        print("\nLes risques sont maintenant visibles dans QALITAS (state=Identifie).")
    print("="*60)


if __name__ == "__main__":
    main()
