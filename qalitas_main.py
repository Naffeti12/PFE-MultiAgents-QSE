"""
QALITAS QSE - Orchestrateur Principal (Agents 2 + 4).

Architecture :
  Agent 2 - Analyse & Evaluation des risques formels
    > cartographie F*G, modes A/B/C/D, indice de maitrise, residuel, priorite
  Agent 4 - Monitoring & Detection dynamique des signaux terrain
    > alertes KPI/NC/reclamations/PDF, cartographie residuelle dynamique

Ce script :
  1. Charge le contexte Excel une seule fois (partage entre les deux agents)
  2. Execute Agent 2 (evaluation formelle de la cartographie)
  3. Execute Agent 4 (monitoring signaux terrain + PDF si disponible)
  4. Construit une vue croisee par processus (recoupement Agent2 vs Agent4)
  5. Genere le rapport JSON consolide
  6. Genere le dashboard HTML unifie

Sorties :
  output/qalitas_report_<timestamp>.json  -- rapport complet
  output/qalitas_latest.json              -- rapport stable
  output/qalitas_dashboard_<timestamp>.html -- dashboard
  output/qalitas_dashboard.html           -- dashboard stable
"""

import json
import logging
import os
import shutil
from collections import Counter
from datetime import datetime

# Charger les variables d'environnement depuis .env
try:
    from dotenv import load_dotenv
    load_dotenv(
        dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        override=False,
    )
except ImportError:
    pass

from loaders.load_excel_data import load_all_context
from loaders.load_dashboard_pdf import load_dashboard_text
from loaders.qalitas_api_client import load_all_context_hybrid, QalitasClient
from loaders.qalitas_api_writer import (
    QalitasWriter,
    inject_agent1_results,
    inject_agent2_results,
    inject_agent4_results,
)

# ---- Agent 2 ----
from agents.agent2_evaluation import evaluate_all as agent2_evaluate
from agents.agent2_llm import enrich_evaluations as agent2_enrich

# ---- Agent 4 ----
from agents.agent4_monitoring import (
    detect_pdf_alerts,
    compute_criticality_score,
    criticality_level,
)
from agents.agent4_excel_detection import (
    detect_all_excel_alerts,
    normalize_process_name,
)
from agents.agent4_llm import enrich_alerts as agent4_enrich


# =============================================================================
# CONFIGURATION
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("qalitas.main")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

DATA_DIR = os.path.join(BASE_DIR, "donnees")
PDF_PATH = os.path.join(BASE_DIR, "donnees", "dashboard.pdf")

# Configuration API QALITAS (optionnel - fallback Excel si non configure)
QALITAS_BASE_URL = os.environ.get(
    "QALITAS_BASE_URL",
    "https://timserver.northeurope.cloudapp.azure.com/QalitasDemo"
)
QALITAS_USERNAME = os.environ.get("QALITAS_USERNAME", "MOHAMED.N")
QALITAS_PASSWORD = os.environ.get("QALITAS_PASSWORD", "MOHAMED.N")
USE_API = bool(QALITAS_USERNAME and QALITAS_PASSWORD)

# Injection des resultats vers QALITAS apres analyse
# DRY_RUN=True : simulation (defaut securise) | False : ecriture reelle
DRY_RUN = os.environ.get("QALITAS_DRY_RUN", "true").lower() != "false"
INJECT_MIN_NIVEAU    = os.environ.get("QALITAS_INJECT_MIN_NIVEAU", "moyen")      # Agent2
INJECT_MIN_CRITICITE = os.environ.get("QALITAS_INJECT_MIN_CRITICITE", "SURVEILLANCE")  # Agent4


# =============================================================================
# UTILITAIRES AGENT 4
# =============================================================================

def prepare_excel_alerts(excel_alerts: list) -> list:
    """Ajoute le score de criticite aux alertes Excel."""
    for alert in excel_alerts:
        gravite = alert.get("gravite_regle", "moyenne")
        type_risque = alert.get("type_risque_regle", "Autre")
        kw_count = len(alert.get("keywords", []))
        score = compute_criticality_score(gravite, type_risque, kw_count, True)
        alert["criticality_score"] = score
        alert["criticality_level"] = criticality_level(score)
        alert["inter_process_impacts"] = []
    return excel_alerts


def build_residual_cartography(cartographie: list, alerts: list) -> list:
    """
    Cartographie residuelle dynamique Agent 4 :
    croise la cartographie formelle avec les signaux detectes.
    """
    residual_map = []
    for entry in cartographie:
        processus_raw = str(entry.get("Processus", "")).strip()
        processus = normalize_process_name(processus_raw) or processus_raw
        intitule = str(entry.get("Intitule", entry.get("Risques", ""))).strip()
        decision = str(entry.get("Decision", entry.get("Décision", ""))).strip()
        try:
            rpn_i = float(entry.get("RPN", 0) or 0)
        except (ValueError, TypeError):
            rpn_i = 0
        try:
            rpn_r = float(entry.get("RPN '", entry.get("RPN'", 0)) or 0)
        except (ValueError, TypeError):
            rpn_r = 0

        related = [
            a for a in alerts
            if normalize_process_name(str(a.get("process_name", ""))) == processus
            and a.get("signal_type") != "opportunite"
        ]
        if len(related) >= 3:
            statut = "ALERTE - Risque residuel depasse"
            action = "Reevaluation immediate requise"
        elif len(related) >= 1:
            statut = "SURVEILLANCE - Signaux detectes"
            action = "Renforcer la surveillance"
        else:
            statut = "STABLE - Sous controle"
            action = "Maintenir les mesures actuelles"

        residual_map.append({
            "processus": processus,
            "risque": intitule,
            "decision_initiale": decision,
            "rpn_initial": rpn_i,
            "rpn_residuel_attendu": rpn_r,
            "nb_signaux_detectes": len(related),
            "statut_dynamique": statut,
            "action_recommandee": action,
        })
    return residual_map


def generate_agent4_summary(alerts: list) -> dict:
    """Synthese statistique Agent 4."""
    total = len(alerts)
    if total == 0:
        return {"total_alerts": 0}
    by_gravity = Counter(
        a.get("gravite_final", a.get("gravite_regle", "nd")) for a in alerts
    )
    by_type = Counter(
        a.get("type_risque_final", a.get("type_risque_regle", "Autre"))
        for a in alerts
    )
    by_process = Counter(a.get("process_name", "nd") for a in alerts)
    by_source = Counter(
        a.get("evidence_source", a.get("source", "nd")) for a in alerts
    )
    by_criticality = Counter(a.get("criticality_level", "nd") for a in alerts)
    llm_count = sum(1 for a in alerts if a.get("llm_used", False))
    reeval_count = sum(1 for a in alerts if a.get("reevaluation_required", False))
    inter_count = sum(1 for a in alerts if a.get("inter_process_impacts"))

    sorted_alerts = sorted(
        alerts, key=lambda a: a.get("criticality_score", 0) or 0, reverse=True
    )
    top5 = [
        {
            "process": a.get("process_name"),
            "type": a.get("type_risque_final", a.get("type_risque_regle")),
            "gravite": a.get("gravite_final", a.get("gravite_regle")),
            "score": a.get("criticality_score"),
            "level": a.get("criticality_level"),
            "source": a.get("evidence_source", a.get("source")),
            "resume": a.get("resume_final", a.get("snippet", ""))[:150],
        }
        for a in sorted_alerts[:5]
    ]
    return {
        "total_alerts": total,
        "total_risques": sum(1 for a in alerts if a.get("signal_type") != "opportunite"),
        "total_opportunites": sum(
            1 for a in alerts if a.get("signal_type") == "opportunite"
        ),
        "total_capitalisations": sum(
            1 for a in alerts
            if "capitalisation" in str(a.get("signal_type", ""))
        ),
        "by_gravity": dict(by_gravity),
        "by_type": dict(by_type),
        "by_process": dict(by_process),
        "by_source": dict(by_source),
        "by_criticality": dict(by_criticality),
        "llm_enrichment_rate": f"{round(llm_count / total * 100, 1)}%",
        "reevaluation_required_count": reeval_count,
        "inter_process_impact_count": inter_count,
        "top_5_critical": top5,
    }


# =============================================================================
# VUE CROISEE PAR PROCESSUS (Agent 2 x Agent 4)
# =============================================================================

def build_cross_process_view(
    risques_agent2: list,
    alerts_agent4: list,
) -> list:
    """
    Croise les resultats Agent 2 (evaluation formelle) et Agent 4 (signaux)
    par processus pour identifier les processus convergents ou divergents.

    Pour chaque processus :
    - Niveau residuel formel (Agent 2)
    - Nb signaux detectes (Agent 4)
    - Statut consolide
    - Verdict : risque confirme / risque sous-estime / risque maitrise / stable
    """
    # Agent 2 : score residuel moyen par processus
    a2_by_proc: dict = {}
    for r in risques_agent2:
        p = normalize_process_name(str(r.get("processus", "")))
        if p not in a2_by_proc:
            a2_by_proc[p] = {"scores": [], "niveaux": [], "codes": []}
        a2_by_proc[p]["scores"].append(r.get("score_residuel", 0))
        a2_by_proc[p]["niveaux"].append(r.get("niveau_residuel", "mineur"))
        a2_by_proc[p]["codes"].append(r.get("code", ""))

    # Agent 4 : signaux par processus
    a4_by_proc: dict = Counter(
        normalize_process_name(str(a.get("process_name", "")))
        for a in alerts_agent4
        if a.get("signal_type") != "opportunite"
    )

    all_procs = set(a2_by_proc.keys()) | set(a4_by_proc.keys())
    cross_view = []

    for proc in sorted(all_procs):
        if not proc:
            continue
        a2_data = a2_by_proc.get(proc, {})
        scores = a2_data.get("scores", [])
        niveaux = a2_data.get("niveaux", [])
        codes = a2_data.get("codes", [])
        score_moyen = round(sum(scores) / len(scores), 1) if scores else 0
        # Niveau dominant Agent 2
        if niveaux:
            niveau_dominant = Counter(niveaux).most_common(1)[0][0]
        else:
            niveau_dominant = "N/A"

        nb_signaux = a4_by_proc.get(proc, 0)

        # Verdict de convergence
        if niveau_dominant in ("critique", "eleve") and nb_signaux >= 2:
            verdict = "RISQUE CONFIRME"
            couleur = "rouge"
        elif niveau_dominant in ("critique", "eleve") and nb_signaux == 0:
            verdict = "RISQUE FORMEL SANS SIGNAL"
            couleur = "orange"
        elif niveau_dominant in ("mineur", "moyen") and nb_signaux >= 3:
            verdict = "RISQUE SOUS-ESTIME"
            couleur = "rouge"
        elif nb_signaux >= 2:
            verdict = "SURVEILLANCE RENFORCEE"
            couleur = "jaune"
        elif nb_signaux == 0 and niveau_dominant in ("mineur",):
            verdict = "STABLE"
            couleur = "vert"
        else:
            verdict = "SURVEILLE"
            couleur = "jaune"

        cross_view.append({
            "processus": proc,
            "nb_risques_formels": len(scores),
            "codes_risques": codes[:5],
            "score_residuel_moyen": score_moyen,
            "niveau_dominant_agent2": niveau_dominant,
            "nb_signaux_agent4": nb_signaux,
            "verdict": verdict,
            "couleur": couleur,
        })

    # Trier par gravite decroissante
    order = {"rouge": 0, "orange": 1, "jaune": 2, "vert": 3}
    cross_view.sort(key=lambda x: order.get(x["couleur"], 9))
    return cross_view


# =============================================================================
# PIPELINE PRINCIPAL
# =============================================================================

def main():
    logger.info("=" * 60)
    logger.info("QALITAS QSE - ORCHESTRATEUR AGENTS 2 & 4")
    logger.info("=" * 60)
    logger.info("Dossier projet : %s", BASE_DIR)

    # ------------------------------------------------------------------
    # ETAPE 1 : Chargement du contexte (API QALITAS ou Excel, une seule fois)
    # ------------------------------------------------------------------
    if USE_API:
        logger.info("Chargement via API QALITAS (mode hybride, partage entre agents)...")
        context = load_all_context_hybrid(
            data_dir=DATA_DIR,
            base_url=QALITAS_BASE_URL,
            username=QALITAS_USERNAME,
            password=QALITAS_PASSWORD,
            prefer_api=True,
        )
    else:
        logger.info("Chargement du contexte Excel (partage entre agents)...")
        context = load_all_context(DATA_DIR)

    logger.info(
        "Contexte [%s] : %d KPIs | %d NC | %d reclamations | %d risques | %d cartographie",
        context.get("_source", "excel"),
        len(context.get("kpis", [])),
        len(context.get("nc", [])),
        len(context.get("reclamations", [])),
        len(context.get("risques", [])),
        len(context.get("cartographie", [])),
    )

    # ------------------------------------------------------------------
    # ETAPE 2 : AGENT 2 - Evaluation formelle
    # ------------------------------------------------------------------
    logger.info("-" * 40)
    logger.info("AGENT 2 - Evaluation formelle de la cartographie...")
    eval_result = agent2_evaluate(context)
    enrich_result = agent2_enrich(
        eval_result["risques"],
        eval_result["opportunites"]
    )
    a2_risques = enrich_result["risques_enrichis"]
    a2_opps = enrich_result["opportunites_enrichies"]
    a2_llm_stats = enrich_result["stats_llm"]
    a2_summary_regle = eval_result["summary"]

    logger.info(
        "Agent 2 : %d risques evalues | %d opportunites | LLM: %s",
        len(a2_risques),
        len(a2_opps),
        a2_llm_stats["taux_enrichissement_llm"],
    )

    # ------------------------------------------------------------------
    # ETAPE 3 : AGENT 4 - Monitoring signaux terrain
    # ------------------------------------------------------------------
    logger.info("-" * 40)
    logger.info("AGENT 4 - Detection dynamique des signaux terrain...")

    pages_data = []
    if os.path.exists(PDF_PATH):
        logger.info("Extraction du dashboard PDF...")
        pages_data = load_dashboard_text(PDF_PATH)
        logger.info("%d pages PDF extraites.", len(pages_data))
    else:
        logger.info("dashboard.pdf absent - analyse PDF ignoree.")

    # Detection alertes PDF
    pdf_alerts = []
    if pages_data:
        pdf_alerts = detect_pdf_alerts(pages_data, context=context)
        logger.info("%d alertes PDF detectees.", len(pdf_alerts))

    # Detection alertes Excel
    excel_alerts = detect_all_excel_alerts(context, dashboard_pages=pages_data)
    excel_alerts = prepare_excel_alerts(excel_alerts)
    logger.info("%d alertes Excel detectees.", len(excel_alerts))

    # Fusion et tri
    all_alerts = pdf_alerts + excel_alerts
    all_alerts.sort(
        key=lambda a: a.get("criticality_score", 0) or 0, reverse=True
    )
    logger.info("%d signaux totaux (PDF + Excel).", len(all_alerts))

    # Enrichissement LLM Agent 4
    enriched_alerts = agent4_enrich(all_alerts) if all_alerts else []

    # Synthese + cartographie residuelle
    a4_summary = generate_agent4_summary(enriched_alerts)
    a4_residual_carto = build_residual_cartography(
        context.get("cartographie", []),
        enriched_alerts,
    )
    logger.info(
        "Agent 4 : %d signaux | LLM: %s",
        len(enriched_alerts),
        a4_summary.get("llm_enrichment_rate", "N/A"),
    )

    # Separation Agent 4
    a4_risques = [
        a for a in enriched_alerts if a.get("signal_type") != "opportunite"
    ]
    a4_opportunites = [
        a for a in enriched_alerts if a.get("signal_type") == "opportunite"
    ]
    a4_capitalisations = [
        a for a in enriched_alerts
        if "capitalisation" in str(a.get("signal_type", ""))
    ]

    # ------------------------------------------------------------------
    # ETAPE 4 : Vue croisee par processus
    # ------------------------------------------------------------------
    logger.info("-" * 40)
    logger.info("Construction de la vue croisee par processus...")
    cross_view = build_cross_process_view(a2_risques, enriched_alerts)
    logger.info("%d processus dans la vue croisee.", len(cross_view))

    # ------------------------------------------------------------------
    # ETAPE 5 : Sauvegarde JSON consolide
    # ------------------------------------------------------------------
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    final_output = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "data_dir": DATA_DIR,
            "orchestrateur": "QALITAS QSE - Agents 2 & 4",
            "model_llm": "llama3",
            "sources": [
                "Cartographie des risques.xlsx",
                "NC.xlsx",
                "Reclamations clients.xlsx",
                "KPI's.xlsx",
                "Risques.xlsx",
                "dashboard.pdf (si present)",
            ],
            "conformite_cdc": {
                "agent2_evaluation_formelle": True,
                "agent2_modes_ABCD": True,
                "agent2_score_brut_residuel": True,
                "agent2_indice_maitrise": True,
                "agent2_priorisation_composite": True,
                "agent4_monitoring_signaux": True,
                "agent4_cartographie_residuelle_dynamique": True,
                "agent4_detection_opportunites": True,
                "vue_croisee_processus": True,
                "enrichissement_llm": True,
            },
        },
        # ---- Agent 2 ----
        "agent2": {
            "llm_stats": a2_llm_stats,
            "summary_regle": a2_summary_regle,
            "risques": a2_risques,
            "opportunites": a2_opps,
            "fortement_maitrises": eval_result.get("fortement_maitrises", []),
        },
        # ---- Agent 4 ----
        "agent4": {
            "summary": a4_summary,
            "cartographie_residuelle_dynamique": a4_residual_carto,
            "risques": a4_risques,
            "opportunites": a4_opportunites,
            "capitalisations": a4_capitalisations,
            "all_alerts": enriched_alerts,
        },
        # ---- Vue croisee ----
        "cross_process_view": cross_view,
    }

    output_file = os.path.join(OUTPUT_DIR, f"qalitas_report_{timestamp}.json")
    stable_file = os.path.join(OUTPUT_DIR, "qalitas_latest.json")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(final_output, f, ensure_ascii=False, indent=2)
    with open(stable_file, "w", encoding="utf-8") as f:
        json.dump(final_output, f, ensure_ascii=False, indent=2)

    logger.info("Rapport consolide : %s", output_file)
    logger.info("Rapport stable    : %s", stable_file)

    # ------------------------------------------------------------------
    # ETAPE 6 : Dashboard HTML unifie
    # ------------------------------------------------------------------
    try:
        from qalitas_dashboard import generate_qalitas_dashboard
        html_file = os.path.join(
            OUTPUT_DIR, f"qalitas_dashboard_{timestamp}.html"
        )
        html_stable = os.path.join(OUTPUT_DIR, "qalitas_dashboard.html")
        generate_qalitas_dashboard(stable_file, html_file)
        shutil.copy(html_file, html_stable)
        logger.info("Dashboard HTML genere : %s", html_file)
    except Exception as e:
        logger.warning("Erreur generation HTML : %s", e)

    # ------------------------------------------------------------------
    # ETAPE 6 : Injection des resultats vers QALITAS (si API active)
    # ------------------------------------------------------------------
    inject_stats = {}
    if USE_API:
        logger.info(
            "Injection vers QALITAS [dry_run=%s] | Agent2 min=%s | Agent4 min=%s",
            DRY_RUN, INJECT_MIN_NIVEAU, INJECT_MIN_CRITICITE
        )
        try:
            inj_client = QalitasClient(
                base_url=QALITAS_BASE_URL,
                username=QALITAS_USERNAME,
                password=QALITAS_PASSWORD,
            )
            if inj_client.login():
                writer = QalitasWriter(client=inj_client, dry_run=DRY_RUN)

                # ----- Agent 1 : Creation des R&O identifies -----
                # Charge le registre genere par agent1_identification.py (fichier stable)
                a1_register_file = os.path.join(OUTPUT_DIR, "agent1_latest.json")
                stats_a1 = {"skipped": "agent1_latest.json absent"}
                if os.path.exists(a1_register_file):
                    try:
                        with open(a1_register_file, "r", encoding="utf-8") as _f:
                            a1_data = json.load(_f)
                        # Le registre peut etre une liste directe ou un dict avec cle "register"
                        if isinstance(a1_data, list):
                            a1_register = a1_data
                        else:
                            a1_register = a1_data.get("register", a1_data.get("risques_opportunites", []))
                        stats_a1 = inject_agent1_results(
                            a1_register, writer, only_new=True
                        )
                        logger.info("Agent1 injection : %s", stats_a1)
                    except Exception as _e:
                        logger.warning("Erreur lecture agent1_latest.json : %s", _e)
                        stats_a1 = {"error": str(_e)}
                else:
                    logger.warning(
                        "agent1_latest.json absent — injections Agent1 ignorees. "
                        "Lancez d'abord : python agents/agent1_identification.py"
                    )

                # ----- Agent 2 : Mise a jour des evaluations -----
                stats_a2 = inject_agent2_results(
                    a2_risques, writer, min_niveau=INJECT_MIN_NIVEAU
                )

                # ----- Agent 4 : Actions de surveillance -----
                stats_a4 = inject_agent4_results(
                    enriched_alerts, writer, min_criticite=INJECT_MIN_CRITICITE
                )

                inject_stats = {
                    "dry_run":      DRY_RUN,
                    "agent1_stats": stats_a1,
                    "agent2_stats": stats_a2,
                    "agent4_stats": stats_a4,
                    "write_report": writer.get_write_report(),
                }
                inj_client.logout()
                logger.info(
                    "Injection terminee : A1=%s | A2=%s | A4=%s",
                    stats_a1, stats_a2, stats_a4
                )
            else:
                logger.warning("Injection ignoree : echec reconnexion QALITAS")
        except Exception as e:
            logger.error("Erreur injection QALITAS : %s", e)
    else:
        logger.info("Injection QALITAS ignoree (API non configuree).")

    # Mise a jour du rapport JSON avec les stats d'injection
    if inject_stats and os.path.exists(stable_file):
        try:
            with open(stable_file, "r", encoding="utf-8") as f:
                report_data = json.load(f)
            report_data["injection_qalitas"] = inject_stats
            with open(stable_file, "w", encoding="utf-8") as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.warning("Impossible de mettre a jour le rapport avec stats injection : %s", e)

    # ------------------------------------------------------------------
    # Affichage console
    # ------------------------------------------------------------------
    print("\n" + "=" * 62)
    print("QALITAS QSE - SYNTHESE CONSOLIDEE")
    print("=" * 62)

    print("\n[AGENT 2 - Evaluation formelle]")
    s2 = a2_summary_regle
    print(f"  Risques evalues : {s2.get('total_risques', 0)}")
    dist = s2.get("by_niveau_residuel", {})
    print(f"  Distribution    : {dist}")
    print(f"  Modes           : {s2.get('by_mode_evaluation', {})}")
    print(f"  Opportunites    : {len(a2_opps)}")
    print(f"  LLM             : {a2_llm_stats['taux_enrichissement_llm']}")

    print("\n[AGENT 4 - Monitoring signaux]")
    print(f"  Signaux totaux  : {a4_summary.get('total_alerts', 0)}")
    print(f"  Par criticite   : {a4_summary.get('by_criticality', {})}")
    print(f"  Opportunites    : {a4_summary.get('total_opportunites', 0)}")
    print(f"  LLM             : {a4_summary.get('llm_enrichment_rate', 'N/A')}")

    print("\n[VUE CROISEE PAR PROCESSUS]")
    for c in cross_view[:8]:
        print(
            f"  {c['verdict']:<28} | {c['processus'][:35]:<35} "
            f"| A2: {c['niveau_dominant_agent2']:<8} | A4: {c['nb_signaux_agent4']} signaux"
        )

    print("\n[TOP 5 RISQUES PRIORITAIRES (Agent 2)]")
    for i, r in enumerate(
        sorted(a2_risques, key=lambda x: x.get("priority_score", 0), reverse=True)[:5],
        1,
    ):
        print(
            f"  {i}. [{r.get('niveau_residuel', '?').upper():<8}] "
            f"{r.get('processus', '')[:28]:<28} prio={r.get('priority_score', 0)}"
        )

    print("\n[TOP 5 ALERTES CRITIQUES (Agent 4)]")
    for i, a in enumerate(
        sorted(
            enriched_alerts,
            key=lambda x: x.get("criticality_score", 0) or 0,
            reverse=True,
        )[:5],
        1,
    ):
        print(
            f"  {i}. [{a.get('criticality_level', '?'):<8}] "
            f"{a.get('process_name', '')[:28]:<28} score={a.get('criticality_score', 0)}"
        )

    if inject_stats:
        wr = inject_stats.get("write_report", {})
        mode_lbl = "DRY-RUN (simulation)" if inject_stats.get("dry_run") else "INJECTION REELLE"
        print(f"\n[INJECTION QALITAS - {mode_lbl}]")
        a1s = inject_stats.get("agent1_stats", {})
        a2s = inject_stats.get("agent2_stats", {})
        a4s = inject_stats.get("agent4_stats", {})
        print(f"  Agent1 : {a1s.get('created', 0)} R&O crees | "
              f"{a1s.get('skipped_existing', 0)} deja existants")
        print(f"  Agent2 : {a2s.get('appreciations_updated', 0)} evaluations maj | "
              f"{a2s.get('actions_created', 0)} actions creees")
        print(f"  Agent4 : {a4s.get('actions_created', 0)} actions creees | "
              f"{a4s.get('skipped', 0)} alertes ignorees")
        print(f"  Total  : {wr.get('success', 0)} OK | {wr.get('failed', 0)} echecs")

    print("=" * 62)


if __name__ == "__main__":
    main()
