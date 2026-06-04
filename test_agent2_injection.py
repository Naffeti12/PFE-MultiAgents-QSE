"""
Test Agent 2 - Injection Historique Evaluation QALITAS
=======================================================
Usage :
    python test_agent2_injection.py           # dry-run (simulation)
    python test_agent2_injection.py --real    # reel (ecrit dans QALITAS)
"""

import argparse
import logging
import os
import sys
import urllib3

# Charger le .env AVANT tout import du projet
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    loaded = load_dotenv(dotenv_path=_env_path, override=False)
    print(f"[.env] {_env_path} {'charge' if loaded else '(deja charge)'}")
except ImportError:
    print("[.env] python-dotenv non installe")

# Force encodage UTF-8 sur Windows (evite UnicodeEncodeError avec les caracteres speciaux)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("test_agent2")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loaders.qalitas_api_client import QalitasClient, DEFAULT_BASE_URL
from loaders.qalitas_api_writer import QalitasWriter, inject_agent2_results

_TIMEOUT = 60


def OK(msg):  print(f"  [OK]  {msg}")
def ERR(msg): print(f"  [ERR] {msg}")
def WRN(msg): print(f"  [WRN] {msg}")


def run_test(dry_run: bool = True):
    print("\n" + "=" * 65)
    print("TEST AGENT2 - INJECTION HISTORIQUE EVALUATION QALITAS")
    print(f"Mode : {'DRY-RUN (simulation)' if dry_run else 'REEL (ecriture dans QALITAS)'}")
    print("=" * 65)

    base_url = os.environ.get("QALITAS_BASE_URL", DEFAULT_BASE_URL)
    username = os.environ.get("QALITAS_USERNAME", "MOHAMED.N")
    password = os.environ.get("QALITAS_PASSWORD", "MOHAMED.N")

    print(f"\n  URL      : {base_url}")
    print(f"  Username : {username}")
    print(f"  Timeout  : {_TIMEOUT}s")

    # ------------------------------------------------------------------
    # ETAPE 0 : Test reseau
    # ------------------------------------------------------------------
    print("\n[0/5] Test connectivite reseau...")
    try:
        import requests as _req
        resp_ping = _req.get(base_url + "/", timeout=_TIMEOUT, verify=False)
        OK(f"HTTP {resp_ping.status_code} - serveur accessible ({len(resp_ping.content)} bytes)")
    except Exception as ping_err:
        ERR(f"Connexion impossible : {ping_err}")
        print("       Verifier internet / VPN / antivirus")
        return

    # ------------------------------------------------------------------
    # ETAPE 1 : Login
    # ------------------------------------------------------------------
    print("\n[1/5] Connexion QALITAS...")
    client = QalitasClient(base_url=base_url, username=username,
                           password=password, timeout=_TIMEOUT)
    login_ok = client.login()
    if not login_ok:
        ERR("Login echoue - verifier USERNAME / PASSWORD / BASE_URL")
        return
    OK("Login reussi")

    # ------------------------------------------------------------------
    # ETAPE 2 : Mapping campagnes -> lignes appreciation
    # ------------------------------------------------------------------
    print("\n[2/5] Chargement campagnes et mapping appreciation...")
    all_evals = []
    mapping = {}  # risk_opp_id -> (eval_id, row)

    try:
        all_evals = client.get_evaluations("")
        print(f"  Campagnes trouvees : {len(all_evals)}")

        for ev in all_evals:
            ev_id = ev.get("Id", "")
            if not ev_id:
                continue
            try:
                rows = client.get_risk_appreciation(ev_id)
                for row in rows:
                    r_id   = row.get("RiskOpportunityId", "")
                    row_id = row.get("Id", "")
                    if r_id and row_id and r_id not in mapping:
                        mapping[r_id] = (ev_id, row)
            except Exception:
                pass

        if not mapping:
            WRN("Aucune ligne d'appreciation trouvee dans les campagnes.")
            WRN("=> L'historique ne pourra pas etre mis a jour.")
            WRN("=> Creez d'abord une campagne d'evaluation dans QALITAS.")
        else:
            OK(f"{len(mapping)} risques indexes dans les campagnes")
            # Afficher 3 exemples
            for i, (rid, (eid, row)) in enumerate(list(mapping.items())[:3]):
                print(f"    [{i+1}] Code={row.get('RiskOpportunityCode','?'):10s}"
                      f" | eval={eid[:12]}..."
                      f" | row_id={row.get('Id','')[:12]}...")

    except Exception as e:
        ERR(f"Chargement campagnes : {e}")
        client.logout()
        return

    # ------------------------------------------------------------------
    # ETAPE 3 : Construire des risques de test depuis l'API
    # ------------------------------------------------------------------
    print("\n[3/5] Chargement risques QALITAS pour le test...")
    try:
        real_risks = client.get_risks()
        OK(f"{len(real_risks)} risques charges")
    except Exception as e:
        ERR(f"Impossible de charger les risques : {e}")
        client.logout()
        return

    if not real_risks:
        WRN("Aucun risque dans QALITAS - test impossible.")
        client.logout()
        return

    test_risques = []
    print("  Risques selectionnes pour le test :")
    for r in real_risks[:3]:
        risk_id = r.get("Id", r.get("RiskOpportunityId", ""))
        if not risk_id:
            continue

        eval_id_found  = ""
        row_id_found   = ""
        if risk_id in mapping:
            eval_id_found, mapped_row = mapping[risk_id]
            row_id_found = mapped_row.get("Id", "")

        status_eval = f"eval={eval_id_found[:12]}..." if eval_id_found else "eval=ABSENT"
        status_row  = f"row={row_id_found[:12]}..."  if row_id_found  else "row=ABSENT [PROBLEME]"
        print(f"    [{r.get('Code','?'):10s}] {r.get('Designation','?')[:45]}")
        print(f"             risk_id={risk_id[:12]}... | {status_eval} | {status_row}")

        test_risques.append({
            "code":                        r.get("Code", "?"),
            "risque":                      r.get("Designation", "?")[:80],
            "processus":                   r.get("ProcessDesignation", "Qualite"),
            "RiskOpportunityId":           risk_id,
            "RiskOpportunityEvaluationId": eval_id_found,
            "score_brut":                  8.0,
            "score_residuel":              4.0,
            "niveau_brut":                 "eleve",
            "niveau_residuel":             "moyen",
            "indice_maitrise":             1.5,
            "mode_evaluation":             "B - Mise a jour incrementale",
            "recommandation_llm": (
                "Renforcer les controles existants et planifier "
                "une reevaluation sous 90 jours."
            ),
            "_raw": {
                "RiskOpportunityId":           risk_id,
                "RiskOpportunityEvaluationId": eval_id_found,
                "Id":                          risk_id,
                "Parameter1":                  4.0,
                "Parameter2":                  2.0,
            },
        })

    if not test_risques:
        ERR("Impossible de construire des risques de test.")
        client.logout()
        return

    print(f"\n  {len(test_risques)} risques de test prets.")

    # ------------------------------------------------------------------
    # ETAPE 4 : inject_agent2_results
    # ------------------------------------------------------------------
    print(f"\n[4/5] Lancement inject_agent2_results (dry_run={dry_run})...")

    writer = QalitasWriter(client=client, dry_run=dry_run)
    stats  = inject_agent2_results(test_risques, writer)

    # ------------------------------------------------------------------
    # ETAPE 5 : Resultats
    # ------------------------------------------------------------------
    print("\n[5/5] Resultats :")
    print(f"  Risques traites            : {stats.get('processed', 0)}")
    print(f"  Appreciations mises a jour : {stats.get('appreciations_updated', 0)}")
    print(f"  Actions creees             : {stats.get('actions_created', 0)}")
    print(f"  Historique eval OK         : {stats.get('hist_eval_created', 0)}")
    print(f"  Historique eval erreurs    : {stats.get('hist_eval_errors', 0)}")
    print(f"  Sautes (sans ID)           : {stats.get('skipped_no_id', 0)}")
    print(f"  Sautes (cache)             : {stats.get('skipped_cache', 0)}")
    print(f"  Erreurs totales            : {stats.get('errors', 0)}")

    hist_ok  = stats.get("hist_eval_created", 0)
    hist_err = stats.get("hist_eval_errors", 0)

    print("\n" + "=" * 65)
    processed = stats.get("processed", 0)
    if dry_run:
        # En dry-run, l'historique n'est pas ecrit (c'est normal)
        # Le vrai indicateur est : risques traites + appreciations + actions
        if processed > 0:
            print(f"[OK] DRY-RUN OK : {processed} risques simules avec succes.")
            print(f"     Appreciations : {stats.get('appreciations_updated',0)}")
            print(f"     Actions       : {stats.get('actions_created',0)}")
            print(f"     Historique    : simule (row_id connu pour {sum(1 for i in stats.get('_items',[]) if i) if stats.get('_items') else 'voir logs'})")
            print("")
            print("     => Pour ecrire dans QALITAS, relancer avec --real")
        else:
            print("[WRN] Aucun risque traite en simulation.")
    elif hist_ok > 0:
        print(f"[OK] SUCCES : {hist_ok} entree(s) dans l'Historique d'evaluation.")
        print("     => Verifier dans QALITAS : Risque > Modifier > Historique d'evaluation")
    elif hist_err > 0 and hist_ok == 0:
        print(f"[ERR] ECHEC : {hist_err} erreur(s) - voir logs ci-dessus.")
        print("      Causes frequentes :")
        print("      - row=ABSENT => risque pas encore dans une campagne QALITAS")
        print("      - eval=ABSENT => aucune campagne d'evaluation dans QALITAS")
        print("      - QALITAS repond 'false|Impossible de modifier'")
    else:
        print("[WRN] Aucun risque traite - voir logs.")
    print("=" * 65 + "\n")

    client.logout()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true",
                        help="Mode reel (ecrit dans QALITAS)")
    args = parser.parse_args()
    run_test(dry_run=not args.real)
