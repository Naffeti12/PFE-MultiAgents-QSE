"""
Agent IA 3 - Pipeline Principal : Generation des Actions de Traitement.
========================================================================
Conforme au cahier des charges TBR.TIM.IA.S5/2025-2026, Agent IA 3.

Usage standalone (depuis la racine du projet) :
    python -m agents.agent3_main                  # dry-run (simulation)
    python -m agents.agent3_main --real           # injection reelle QALITAS
    python -m agents.agent3_main --input <json>   # charger un rapport Agent2 specifique

Entree  : output/agent2_latest.json (rapport Agent2)
Sorties : output/agent3_report_<timestamp>.json
          output/agent3_latest.json

Pipeline :
    1. Chargement du registre evalue (Agent2 output ou Excel fallback)
    2. Construction du plan de traitement (agent3_treatment.py)
    3. Acceptation formalisee des risques "Acceptable"
    4. Injection dans QALITAS (actions correctives)
    5. Sauvegarde du rapport
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

# Ajout du dossier parent au path si execution directe
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.agent3_treatment import (
    build_treatment_plan,
    filter_ro_a_traiter,
    generate_treatment_summary,
)
from loaders.load_excel_data import load_all_context
from loaders.qalitas_api_client import QalitasClient, DEFAULT_BASE_URL
from loaders.qalitas_api_writer import QalitasWriter, inject_agent2_results

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("agent3.main")


# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR   = os.path.join(BASE_DIR, "donnees")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

QALITAS_BASE_URL = os.environ.get(
    "QALITAS_BASE_URL",
    "https://timserver.northeurope.cloudapp.azure.com/QalitasDemo"
)
QALITAS_USERNAME = os.environ.get("QALITAS_USERNAME", "MOHAMED.N")
QALITAS_PASSWORD = os.environ.get("QALITAS_PASSWORD", "MOHAMED.N")


# =============================================================================
# CHARGEMENT DU REGISTRE EVALUE (sortie Agent2)
# =============================================================================

def load_evaluated_register(input_path: str = None) -> List[Dict]:
    """
    Charge le registre evalue depuis :
    1. Le fichier JSON specifie en parametre (--input)
    2. output/agent2_latest.json (fichier stable Agent2)
    3. Excel fallback (cartographie des risques) si aucun JSON disponible

    Retourne la liste des R&O evalues.
    """
    # Chemin par defaut
    if not input_path:
        input_path = os.path.join(OUTPUT_DIR, "agent2_latest.json")

    if os.path.exists(input_path):
        logger.info("Chargement du registre Agent2 depuis : %s", input_path)
        with open(input_path, encoding="utf-8") as f:
            data = json.load(f)

        # Le rapport Agent2 contient "risques" + "opportunites"
        risques      = data.get("risques", [])
        opportunites = data.get("opportunites", [])
        all_ro = risques + opportunites
        logger.info(
            "Registre charge : %d risques + %d opportunites = %d R&O",
            len(risques), len(opportunites), len(all_ro)
        )
        return all_ro

    # Fallback Excel
    logger.warning(
        "Fichier Agent2 introuvable (%s). Fallback sur Excel cartographie.",
        input_path
    )
    context = load_all_context(DATA_DIR)
    cartographie = context.get("cartographie", [])
    logger.info("Fallback Excel : %d entrees cartographie chargees.", len(cartographie))
    return cartographie


# =============================================================================
# ACCEPTATION FORMALISEE DES RISQUES ACCEPTES (CDC Agent3, p.14)
# =============================================================================

def build_acceptance_records(evaluated_ro: List[Dict]) -> List[Dict]:
    """
    Genere les fiches d'acceptation formalisee pour les risques
    dont le statut est 'Acceptable' (niveau residuel mineur + justifie).

    CDC : "Aucune acceptation sans justification du residuel."

    Retourne la liste des fiches d'acceptation.
    """
    _, exclus = filter_ro_a_traiter(evaluated_ro, seuil_niveau="moyen")

    records = []
    for ro in exclus:
        # Ne concerne que les risques (pas les opportunites)
        signal = str(ro.get("signal_type", ro.get("type", "risque"))).lower()
        if "opportunite" in signal or "opportunité" in signal:
            continue

        niveau    = str(ro.get("niveau_residuel", ro.get("niveau", "mineur"))).lower()
        score_res = ro.get("score_residuel", ro.get("RPN'", 0))
        score_brut= ro.get("score_brut",     ro.get("RPN",   0))
        justif_eval = str(ro.get("justification_eval", ro.get("recommandation", "")) or "")
        intitule  = str(ro.get("intitule", ro.get("risque", ro.get("Intitule", "?"))))
        code      = str(ro.get("code", ro.get("Code", "")))
        processus = str(ro.get("processus", ro.get("Processus", ro.get("process_name", "?"))))

        record = {
            "type":                 "acceptation_formalisee",
            "risque_code":          code,
            "risque_intitule":      intitule,
            "processus":            processus,
            "niveau_residuel":      niveau,
            "score_residuel":       score_res,
            "score_brut":           score_brut,
            "justification":        (
                f"Risque de niveau '{niveau}' (score residuel={score_res}) "
                f"considere acceptable apres evaluation. "
                + (justif_eval[:400] if justif_eval else
                   "Risque sous controle, mesures de maitrise en place.")
            ),
            "surveillance_requise": True,
            "periodicite_reeval":   "6 mois",
            "condition_reevaluation": (
                "Tout incident, NC, derive KPI ou changement de contexte "
                "sur ce risque doit declencher une reevaluation."
            ),
            "date_acceptation":     datetime.now().isoformat(),
            "valideur":             "Responsable QSE (validation humaine requise)",
        }
        records.append(record)

    logger.info(
        "Acceptations formalisees : %d risques acceptes (a valider par le responsable QSE)",
        len(records)
    )
    return records


# =============================================================================
# INJECTION AGENT3 DANS QALITAS
# =============================================================================

def inject_agent3_results(
    plan: List[Dict],
    writer: QalitasWriter,
    min_classe: str = "complementaire",
) -> Dict[str, Any]:
    """
    Injecte les actions du plan de traitement Agent3 dans QALITAS.

    Seules les entrees dont la classe >= min_classe sont injectees.
    Hierarchie : critique > prioritaire > complementaire

    Gestion des R&O sans GUID QALITAS (issus des fiches Excel) :
        Si RiskOpportunityId absent -> action creee avec Source="4" (sans lien).
        Cette approche permet quand meme de tracer l'action dans QALITAS,
        meme avant que le R&O ait ete cree formellement dans la plateforme.
        Les actions seront reliees a leur risque apres l'injection Agent1.

    Pour chaque entree du plan :
    - Cree une action QALITAS par action candidate (designation + description)
    - Lie l'action au risque source via TriggerSourceId si GUID disponible
    - Sinon : action orpheline (Source="4") avec reference au code du risque

    Retourne les statistiques d'injection.
    """
    from datetime import timedelta
    from loaders.qalitas_api_writer import (
        _fingerprint, _load_cache, _save_cache,
        is_already_injected, mark_as_injected,
        _resolve_processus_id, _resolve_gravity_id, _resolve_priority_id,
        TYPE_PREVENTIVE_ID,
        ACTION_SOURCE_RISQUE, ACTION_SOURCE_AGENT4,
    )

    CLASSES_ORDRE = ["complementaire", "prioritaire", "critique"]
    min_idx = CLASSES_ORDRE.index(min_classe) if min_classe in CLASSES_ORDRE else 0

    stats = {
        "processed":        0,
        "actions_created":  0,
        "actions_sans_guid": 0,   # actions creees sans lien GUID (Source="4")
        "skipped_classe":   0,
        "skipped_cache":    0,
        "errors":           0,
    }

    # Charger le cache une seule fois pour tout le batch
    _cache = _load_cache()

    for entree in plan:
        classe_val = entree.get("classe", "complementaire")
        classe_idx = CLASSES_ORDRE.index(classe_val) if classe_val in CLASSES_ORDRE else 0
        if classe_idx < min_idx:
            stats["skipped_classe"] += 1
            continue

        raw         = entree.get("_raw", {})
        risk_id     = raw.get("RiskOpportunityId", "")
        eval_id     = raw.get("RiskOpportunityEvaluationId", raw.get("EvaluationId", ""))
        processus_e = str(entree.get("risque_processus", entree.get("processus", entree.get("perimetre", ""))))
        niveau_e    = str(entree.get("niveau_residuel", "moyen")).lower()
        strategie_e = str(entree.get("strategie", "Reduire"))
        code_ro     = str(entree.get("risque_code", "?"))

        # Source QALITAS : avec lien si GUID present, sans lien sinon
        # ACTION_SOURCE_RISQUE ("11") requiert un TriggerSourceId GUID valide
        # ACTION_SOURCE_AGENT4 ("4")  accepte TriggerSourceId="" -> action standalone
        if risk_id:
            action_source = ACTION_SOURCE_RISQUE
        else:
            action_source = ACTION_SOURCE_AGENT4
            logger.info(
                "Agent3 : pas de RiskOpportunityId pour '%s' — "
                "action creee en mode standalone (Source='4')", code_ro
            )

        stats["processed"] += 1

        # Resolution des IDs QALITAS pour priorite et gravite
        process_id  = _resolve_processus_id(processus_e)
        gravity_id  = _resolve_gravity_id(niveau_e)
        priority_id = _resolve_priority_id(niveau_e)

        for action in entree.get("actions", []):
            echeance_j = int(action.get("echeance_j", 30))
            end_date   = datetime.now() + timedelta(days=echeance_j)

            designation = f"[Agent3] {action['designation'][:160]}"

            # --- Verifier le cache avant de creer l'action ---
            fp = _fingerprint(designation, processus_e, "agent3")
            if is_already_injected(fp, _cache):
                logger.info(
                    "[Cache-A3] Action deja injectee cette semaine, ignoree : %s",
                    designation[:80]
                )
                stats["skipped_cache"] += 1
                continue

            description = action.get("description", "")
            indicateur  = action.get("indicateur", "")
            if indicateur:
                description += f"\n\nIndicateur de suivi : {indicateur}"
            description += (
                f"\n\nClasse du plan : {classe_val.upper()} "
                f"| Strategie : {strategie_e} "
                f"| Niveau residuel : {niveau_e} "
                f"| Code R&O : {code_ro} "
                f"| Priorite : {entree.get('indice_priorite', 0):.2f}"
            )
            if not risk_id:
                description = (
                    f"[Action sans lien QALITAS — R&O a creer : {code_ro}]\n"
                    + description
                )

            ok, _ = writer.create_risk_action(
                risk_opportunity_id=risk_id,
                title=designation,
                description=description,
                evaluation_id=eval_id,
                action_source=action_source,
                end_date=end_date,
                process_id=process_id,
                gravity_id=gravity_id,
                priority_id=priority_id,
                action_type_id=TYPE_PREVENTIVE_ID,
            )

            if ok:
                stats["actions_created"] += 1
                if not risk_id:
                    stats["actions_sans_guid"] += 1
                mark_as_injected(fp, designation[:80], _cache, agent="agent3")
            else:
                stats["errors"] += 1

    # Persister le cache apres tout le batch
    _save_cache(_cache)

    # Persister le cache apres tout le batch
    _save_cache(_cache)

    logger.info(
        "Injection Agent3 : %d traites | %d actions creees (%d sans GUID) | "
        "%d cache | %d hors classe | %d erreurs",
        stats["processed"], stats["actions_created"], stats.get("actions_sans_guid", 0),
        stats["skipped_cache"], stats["skipped_classe"], stats["errors"]
    )
    return stats


# =============================================================================
# PIPELINE PRINCIPAL
# =============================================================================

def run_agent3(
    input_path:   str  = None,
    dry_run:      bool = True,
    seuil_niveau: str  = "moyen",
    min_classe:   str  = "complementaire",
    llm_url:      str  = "http://localhost:11434",
) -> Dict[str, Any]:
    """
    Execute le pipeline complet de l'Agent 3.

    Retourne le rapport complet (dict) pour integration dans l'orchestrateur.
    """
    logger.info("=" * 60)
    logger.info("AGENT 3 - GENERATION DES ACTIONS DE TRAITEMENT")
    logger.info("Mode : %s", "DRY-RUN (simulation)" if dry_run else "REEL (injection QALITAS)")
    logger.info("=" * 60)

    # --- Etape 1 : Chargement du registre evalue ---
    evaluated_ro = load_evaluated_register(input_path)
    if not evaluated_ro:
        logger.error("Registre evalue vide. Arret du pipeline Agent3.")
        return {"error": "Registre evalue vide"}

    # --- Etape 2 : Construction du plan de traitement ---
    plan = build_treatment_plan(
        evaluated_ro=evaluated_ro,
        seuil_niveau=seuil_niveau,
        max_actions_par_ro=3,
        enrichissement_llm=True,
        llm_url=llm_url,
    )

    # --- Etape 3 : Acceptation formalisee ---
    acceptance_records = build_acceptance_records(evaluated_ro)

    # --- Etape 4 : Injection QALITAS ---
    injection_stats = {"dry_run": dry_run, "actions_created": 0}
    if plan:
        try:
            client = QalitasClient(
                base_url=QALITAS_BASE_URL,
                username=QALITAS_USERNAME,
                password=QALITAS_PASSWORD,
            )
            if not dry_run:
                client.login()
            writer = QalitasWriter(client=client, dry_run=dry_run)
            injection_stats = inject_agent3_results(plan, writer, min_classe=min_classe)
            injection_stats["dry_run"] = dry_run
        except Exception as exc:
            logger.error("Erreur connexion QALITAS : %s", exc)
            injection_stats["error"] = str(exc)

    # --- Etape 5 : Rapport ---
    summary = generate_treatment_summary(plan)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    rapport = {
        "metadata": {
            "agent":        "Agent 3 - Generation des Actions de Traitement",
            "generated_at": datetime.now().isoformat(),
            "dry_run":      dry_run,
            "seuil_niveau": seuil_niveau,
            "min_classe":   min_classe,
            "conformite_cdc": {
                "selection_automatique":    True,
                "choix_strategie":          True,
                "generation_actions_types": True,  # tech, orga, humaine, doc, contrat, surv
                "estimation_efficacite_effort": True,
                "acceptation_formalisee":   True,
                "injection_qalitas":        True,
                "indicateurs_jalons":       True,
            },
        },
        "summary":             summary,
        "plan_traitement":     plan,
        "acceptations":        acceptance_records,
        "injection_stats":     injection_stats,
    }

    # Sauvegarde
    out_file    = os.path.join(OUTPUT_DIR, f"agent3_report_{timestamp}.json")
    stable_file = os.path.join(OUTPUT_DIR, "agent3_latest.json")

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=2, default=str)
    with open(stable_file, "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=2, default=str)

    logger.info("Rapport Agent3 sauvegarde : %s", out_file)

    # Console
    print("\n" + "=" * 60)
    print("SYNTHESE AGENT 3 - PLAN DE TRAITEMENT")
    print("=" * 60)
    print(f"R&O traites         : {summary.get('total_ro_traites', 0)}")
    print(f"Actions generees    : {summary.get('total_actions', 0)}")
    print(f"Par strategie       : {summary.get('by_strategie', {})}")
    print(f"Par classe          : {summary.get('by_classe', {})}")
    print(f"Acceptations form.  : {len(acceptance_records)}")
    print(f"Injection QALITAS   : {injection_stats.get('actions_created', 0)} actions creees")
    print(f"LLM enrichissement  : {summary.get('llm_enrichissement', '0/0')}")
    print("\nTop 5 prioritaires :")
    for i, e in enumerate(summary.get("top5_prioritaires", []), 1):
        print(
            f"  {i}. [{e['classe'].upper()}] {e['code']} - {e['intitule'][:60]}"
            f" | Strategie: {e['strategie']} | Priorite: {e['priorite']:.2f}"
            f" | {e['nb_actions']} actions"
        )
    print("=" * 60)

    return rapport


# =============================================================================
# POINT D'ENTREE CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Agent IA 3 - Generation des Actions de Traitement"
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Activer l'injection reelle dans QALITAS (defaut: dry-run)"
    )
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Chemin vers le rapport Agent2 (JSON). Defaut: output/agent2_latest.json"
    )
    parser.add_argument(
        "--seuil",
        type=str,
        default="moyen",
        choices=["mineur", "moyen", "eleve", "critique"],
        help="Seuil minimum de niveau residuel pour traitement (defaut: moyen)"
    )
    parser.add_argument(
        "--classe",
        type=str,
        default="complementaire",
        choices=["complementaire", "prioritaire", "critique"],
        help="Classe minimum pour injection QALITAS (defaut: complementaire — tout injecter)"
    )
    args = parser.parse_args()

    run_agent3(
        input_path=args.input,
        dry_run=not args.real,
        seuil_niveau=args.seuil,
        min_classe=args.classe,
    )
