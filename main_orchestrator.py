"""
Plateforme Multi-Agents QSE - Orchestrateur Principal
=======================================================
Conforme au cahier des charges TBR.TIM.IA.S5/2025-2026.

Ce script est le point d'entree unique de la plateforme.
Il orchestre les 4 agents via LangGraph (ou le graphe sequentiel de secours).

Architecture :
    Agent 1 (Identification)  ->  Agent 2 (Evaluation)
    Agent 2 (Evaluation)      ->  Agent 3 (Traitement)
    Agent 3 (Traitement)      ->  Agent 4 (Monitoring)
    Agent 4 (Monitoring)      ->  [Reevaluation Agent2 si alerte] ou Fin

Usage :
    python main_orchestrator.py                   # dry-run complet
    python main_orchestrator.py --real            # injection reelle QALITAS
    python main_orchestrator.py --agent 1         # Agent 1 uniquement
    python main_orchestrator.py --agent 2         # Agent 2 uniquement
    python main_orchestrator.py --agent 3         # Agent 3 uniquement
    python main_orchestrator.py --agent 4         # Agent 4 uniquement
    python main_orchestrator.py --agent 1 2 3 4   # Pipeline complet
    python main_orchestrator.py --report          # Generer rapport HTML final

Variables d'environnement :
    QALITAS_BASE_URL   : URL de base QALITAS
    QALITAS_USERNAME   : Identifiant QALITAS
    QALITAS_PASSWORD   : Mot de passe QALITAS
    OLLAMA_URL         : URL serveur Ollama (defaut http://localhost:11434)
"""

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

# ===================================================================
# PATH SETUP
# ===================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

DATA_DIR   = os.path.join(BASE_DIR, "donnees")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ===================================================================
# LOGGING
# ===================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(OUTPUT_DIR, "orchestrateur.log"),
            encoding="utf-8",
            mode="a",
        ),
    ],
)
logger = logging.getLogger("orchestrateur")

# ===================================================================
# CONFIGURATION
# ===================================================================

QALITAS_BASE_URL = os.environ.get(
    "QALITAS_BASE_URL",
    "https://timserver.northeurope.cloudapp.azure.com/QalitasDemo"
)
QALITAS_USERNAME = os.environ.get("QALITAS_USERNAME", "MOHAMED.N")
QALITAS_PASSWORD = os.environ.get("QALITAS_PASSWORD", "MOHAMED.N")
OLLAMA_URL       = os.environ.get("OLLAMA_URL",       "http://localhost:11434")


# ===================================================================
# IMPORT MODULES AGENTS
# ===================================================================

def _safe_import(module_name: str, agent_label: str):
    """Import securise avec message d'erreur clair."""
    try:
        return __import__(module_name, fromlist=["*"])
    except ImportError as e:
        logger.error("Impossible d'importer %s (%s) : %s", module_name, agent_label, e)
        return None


# ===================================================================
# EXECUTION INDIVIDUELLE PAR AGENT
# ===================================================================

def run_agent_1(dry_run: bool = True) -> Dict:
    """Lance l'Agent 1 - Identification & Caracterisation."""
    logger.info("[AGENT 1] Demarrage identification R&O...")
    try:
        from agents.agent1_identification import run_agent1
        rapport = run_agent1(
            data_dir=DATA_DIR,
            use_api=True,
            dry_run=dry_run,
            llm_url=OLLAMA_URL,
        )
        n = len(rapport.get("register", []))
        logger.info("[AGENT 1] Termine : %d R&O identifies.", n)
        return rapport
    except Exception as exc:
        logger.error("[AGENT 1] Erreur : %s", exc, exc_info=True)
        return {"error": str(exc), "register": []}


def run_agent_2(
    input_path: str = None,
    dry_run:    bool = True,
    reeval_codes: List[str] = None,
) -> Dict:
    """
    Lance l'Agent 2 - Analyse & Evaluation.
    Utilise directement agent2_node de orchestrator.graph (sans agent2_main).
    """
    logger.info("[AGENT 2] Demarrage evaluation R&O...")
    try:
        from orchestrator.graph import agent2_node
        # Charger le registre Agent1 si input_path fourni
        ro_register = []
        _input = input_path or os.path.join(OUTPUT_DIR, "agent1_latest.json")
        if os.path.exists(_input):
            with open(_input, encoding="utf-8") as _f:
                _d = json.load(_f)
                ro_register = _d.get("register", [])
        # Construire l'etat minimal requis par agent2_node
        state = {
            "dry_run":         dry_run,
            "ro_register":     ro_register,
            "data_dir":        DATA_DIR,
            "reeval_required": bool(reeval_codes),
            "reeval_risques":  reeval_codes or [],
            "reeval_trigger":  "",
            "evaluated_ro":    [],
            "errors":          [],
            "qalitas_stats":   {},
            "iteration":       1,
            "max_iterations":  3,
        }
        result = agent2_node(state)
        # Lire le rapport depuis agent2_latest.json (mis a jour par agent2_node)
        a2_path = os.path.join(OUTPUT_DIR, "agent2_latest.json")
        if os.path.exists(a2_path):
            with open(a2_path, encoding="utf-8") as _f:
                rapport = json.load(_f)
        else:
            rapport = {"risques": result.get("risques_evalues", []),
                       "opportunites": result.get("opportunites_evaluees", [])}
        n_r = len(rapport.get("risques", []))
        n_o = len(rapport.get("opportunites", []))
        logger.info("[AGENT 2] Termine : %d risques + %d opportunites evalues.", n_r, n_o)
        return rapport
    except Exception as exc:
        logger.error("[AGENT 2] Erreur : %s", exc, exc_info=True)
        return {"error": str(exc), "risques": [], "opportunites": []}


def run_agent_3(
    input_path: str = None,
    dry_run:    bool = True,
) -> Dict:
    """Lance l'Agent 3 - Generation des Actions de Traitement."""
    logger.info("[AGENT 3] Demarrage generation plan de traitement...")
    try:
        from agents.agent3_main import run_agent3
        rapport = run_agent3(
            input_path=input_path,
            dry_run=dry_run,
            llm_url=OLLAMA_URL,
        )
        n_plan = len(rapport.get("plan_traitement", []))
        logger.info("[AGENT 3] Termine : %d entrees dans le plan de traitement.", n_plan)
        return rapport
    except Exception as exc:
        logger.error("[AGENT 3] Erreur : %s", exc, exc_info=True)
        return {"error": str(exc), "plan_traitement": []}


def run_agent_4(dry_run: bool = True) -> Dict:
    """
    Lance l'Agent 4 - Monitoring & Reevaluation Continue.
    Utilise directement agent4_node de orchestrator.graph (sans agent4_main).
    """
    logger.info("[AGENT 4] Demarrage monitoring & detection alertes...")
    try:
        from orchestrator.graph import agent4_node
        # Charger l'etat Agent3 si disponible
        a3_path = os.path.join(OUTPUT_DIR, "agent3_latest.json")
        treatment_plan = []
        if os.path.exists(a3_path):
            with open(a3_path, encoding="utf-8") as _f:
                _d = json.load(_f)
                treatment_plan = _d.get("plan_traitement", [])
        state = {
            "dry_run":           dry_run,
            "data_dir":          DATA_DIR,
            "pdf_path":          os.path.join(DATA_DIR, "dashboard.pdf"),
            "treatment_plan":    treatment_plan,
            "errors":            [],
            "qalitas_stats":     {},
            "iteration":         1,
            "max_iterations":    3,
            "reeval_required":   False,
            "reeval_trigger":    "",
            "reeval_risques":    [],
            "monitoring_alerts": [],
        }
        result = agent4_node(state)
        # Lire le rapport depuis agent4_latest.json (mis a jour par agent4_node)
        a4_path = os.path.join(OUTPUT_DIR, "agent4_latest.json")
        if os.path.exists(a4_path):
            with open(a4_path, encoding="utf-8") as _f:
                rapport = json.load(_f)
        else:
            rapport = result
        logger.info("[AGENT 4] Termine.")
        return rapport
    except Exception as exc:
        logger.error("[AGENT 4] Erreur : %s", exc, exc_info=True)
        return {"error": str(exc)}


# ===================================================================
# PIPELINE COMPLET SANS LANGGRAPH (mode sequentiel)
# ===================================================================

def run_pipeline_sequential(
    dry_run:       bool = True,
    max_iterations: int = 3,
    agents:        List[int] = None,
) -> Dict:
    """
    Execute le pipeline complet en mode sequentiel (sans LangGraph).
    Supporte la boucle de reevaluation Agent4 -> Agent2.
    """
    if agents is None:
        agents = [1, 2, 3, 4]

    run_id = str(uuid.uuid4())[:8]
    started_at = datetime.now().isoformat()

    logger.info("Pipeline sequentiel | run_id=%s | agents=%s | dry_run=%s",
                run_id, agents, dry_run)

    rapport_global = {
        "run_id":     run_id,
        "started_at": started_at,
        "dry_run":    dry_run,
        "agents_executes": [],
        "iterations": 0,
    }

    agent1_latest = os.path.join(OUTPUT_DIR, "agent1_latest.json")
    agent2_latest = os.path.join(OUTPUT_DIR, "agent2_latest.json")
    agent3_latest = os.path.join(OUTPUT_DIR, "agent3_latest.json")

    # --- Agent 1 ---
    if 1 in agents:
        r1 = run_agent_1(dry_run=dry_run)
        rapport_global["agent1"] = {
            "total_ro":   len(r1.get("register", [])),
            "error":      r1.get("error"),
        }
        rapport_global["agents_executes"].append(1)

    # --- Boucle Agent 2 / 3 / 4 avec reevaluation ---
    iteration = 0
    reeval_codes: List[str] = []

    while iteration < max_iterations:
        iteration += 1
        rapport_global["iterations"] = iteration

        # Agent 2
        if 2 in agents:
            input_a2 = agent1_latest if iteration == 1 else None
            r2 = run_agent_2(
                input_path=input_a2,
                dry_run=dry_run,
                reeval_codes=reeval_codes,
            )
            rapport_global["agent2"] = {
                "risques":      len(r2.get("risques", [])),
                "opportunites": len(r2.get("opportunites", [])),
                "error":        r2.get("error"),
            }
            if 2 not in rapport_global["agents_executes"]:
                rapport_global["agents_executes"].append(2)

        # Agent 3
        if 3 in agents:
            r3 = run_agent_3(
                input_path=agent2_latest,
                dry_run=dry_run,
            )
            rapport_global["agent3"] = {
                "plan_traitement": len(r3.get("plan_traitement", [])),
                "acceptations":    len(r3.get("acceptations", [])),
                "error":           r3.get("error"),
            }
            if 3 not in rapport_global["agents_executes"]:
                rapport_global["agents_executes"].append(3)

        # Agent 4
        if 4 in agents:
            r4 = run_agent_4(dry_run=dry_run)
            rapport_global["agent4"] = {
                "error": r4.get("error"),
            }
            if 4 not in rapport_global["agents_executes"]:
                rapport_global["agents_executes"].append(4)

            # Verifier si Agent4 demande une reevaluation
            reeval_required = r4.get("reeval_required", False)
            reeval_codes    = r4.get("reeval_codes", [])
            reeval_trigger  = r4.get("reeval_trigger", "")

            if reeval_required and 2 in agents:
                logger.info(
                    "REEVALUATION demandee par Agent4 (iteration %d/%d) | "
                    "Motif: %s | Codes: %s",
                    iteration, max_iterations, reeval_trigger, reeval_codes
                )
                rapport_global.setdefault("reeval_history", []).append({
                    "iteration":  iteration,
                    "trigger":    reeval_trigger,
                    "codes":      reeval_codes,
                })
                continue  # Reboucler sur Agent2

        # Pas de reevaluation : sortir de la boucle
        break

    rapport_global["ended_at"]   = datetime.now().isoformat()
    rapport_global["status"]     = "completed"

    return rapport_global


# ===================================================================
# PIPELINE COMPLET AVEC LANGGRAPH
# ===================================================================

def run_pipeline_langgraph(
    dry_run:       bool = True,
    max_iterations: int = 3,
) -> Dict:
    """
    Execute le pipeline via LangGraph StateGraph.
    Necessite : pip install langgraph langchain langchain-core
    """
    try:
        from orchestrator.graph import get_graph
        from orchestrator.state import PipelineState
    except ImportError as exc:
        logger.warning("LangGraph non disponible (%s). Bascule sur mode sequentiel.", exc)
        return run_pipeline_sequential(dry_run=dry_run, max_iterations=max_iterations)

    run_id     = str(uuid.uuid4())[:8]
    started_at = datetime.now().isoformat()

    initial_state: PipelineState = {
        "data_dir":      DATA_DIR,
        "pdf_path":      os.path.join(DATA_DIR, "dashboard.pdf"),
        "run_id":        run_id,
        "started_at":    started_at,
        "dry_run":       dry_run,
        "iteration":     0,
        "max_iterations": max_iterations,
        "errors":        [],
        "qalitas_stats": {},
        "reeval_required": False,
        "reeval_trigger":  "",
        "reeval_risques":  [],
    }

    logger.info("Pipeline LangGraph | run_id=%s | dry_run=%s", run_id, dry_run)

    graph = get_graph()
    final_state = graph.invoke(initial_state)

    rapport = {
        "run_id":     run_id,
        "started_at": started_at,
        "ended_at":   datetime.now().isoformat(),
        "status":     "completed",
        "mode":       "langgraph",
        "dry_run":    dry_run,
        "iterations": final_state.get("iteration", 0),
        "agent1_ro":  len(final_state.get("ro_register", [])),
        # Compteur coherent avec agent2_latest.json : risques + opportunites
        # evalues avec scoring complet (pas le total du pipeline state)
        "agent2_evaluated": (
            len(final_state.get("risques_evalues", [])) +
            len(final_state.get("opportunites_evaluees", []))
            or len(final_state.get("evaluated_ro", []))
        ),
        "agent3_plan": len(final_state.get("treatment_plan", [])),
        "agent4_alerts": len(final_state.get("monitoring_alerts", [])),
        "errors":     final_state.get("errors", []),
        "qalitas_stats": final_state.get("qalitas_stats", {}),
    }

    return rapport


# ===================================================================
# GENERATION DU RAPPORT HTML FINAL
# ===================================================================

def generate_html_report(output_dir: str = OUTPUT_DIR) -> str:
    """
    Genere un rapport HTML enrichi de synthese de la derniere execution.
    Lit les fichiers JSON latest de chaque agent.
    Corrections v2 :
      - Mapping champs correct : 'risque' (pas 'intitule'), 'statut_decisionnel' (pas 'statut')
      - Sections Agent 4 (alertes, gravite, processus)
      - Compteurs coherents avec les JSON reels
      - Tableau complet des risques evalues (pas seulement critiques)
      - Section opportunites
    """
    def _load_json(path):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    a1 = _load_json(os.path.join(output_dir, "agent1_latest.json"))
    a2 = _load_json(os.path.join(output_dir, "agent2_latest.json"))
    a3 = _load_json(os.path.join(output_dir, "agent3_latest.json"))
    a4 = _load_json(os.path.join(output_dir, "agent4_latest.json"))

    now = datetime.now().strftime("%d/%m/%Y %H:%M")

    # --- Stats Agent 1 ---
    register    = a1.get("register", [])
    total_ro    = len(register)
    nb_risques_a1  = sum(1 for r in register if str(r.get("nature", r.get("type", ""))).lower() in ("risque", "risk", "0"))
    nb_opps_a1     = total_ro - nb_risques_a1

    # --- Stats Agent 2 (mapping correct sur les vrais champs JSON) ---
    risques_ev  = a2.get("risques", [])
    opps_ev     = a2.get("opportunites", [])
    summary_a2  = a2.get("summary", a2.get("eval_summary", {}))
    nb_risques_ev  = len(risques_ev)
    nb_oppty_ev    = len(opps_ev)
    by_niveau      = summary_a2.get("by_niveau_residuel", {})
    nb_critique    = by_niveau.get("critique", 0)
    nb_eleve       = by_niveau.get("eleve", 0)
    nb_moyen       = by_niveau.get("moyen", 0)
    nb_mineur      = by_niveau.get("mineur", 0)
    nb_a_traiter   = summary_a2.get("a_traiter_planifie", 0) + summary_a2.get("a_traiter_immediat", 0)
    nb_surveiller  = summary_a2.get("a_surveiller", 0)
    nb_acceptable  = summary_a2.get("acceptable", 0)
    reevaluation_requise = summary_a2.get("reevaluation_requise_count", 0)

    # --- Stats Agent 3 ---
    plan_traitement = a3.get("plan_traitement", [])
    acceptations    = a3.get("acceptations", [])
    nb_plan    = len(plan_traitement)
    nb_accept  = len(acceptations)

    # --- Stats Agent 4 ---
    summary_a4    = a4.get("summary", {})
    all_alerts    = a4.get("all_alerts", [])
    nb_alerts     = summary_a4.get("total_alerts", len(all_alerts))
    nb_risques_a4 = summary_a4.get("total_risques", 0)
    nb_opps_a4    = summary_a4.get("total_opportunites", 0)
    by_gravity    = summary_a4.get("by_gravity", {})
    by_type_a4    = summary_a4.get("by_type", {})
    by_process_a4 = summary_a4.get("by_process", {})
    capitalisations = a4.get("capitalisations", [])

    # Efficacite Agent 4 (lue depuis bilan si disponible)
    # Lire l'efficacite depuis le bilan le plus complet (celui qui a agent4_efficacy)
    # et non pas systematiquement le dernier (qui peut etre un run partiel --agent 1 2)
    efficacite_stats = {}
    bilan_files = sorted([
        f for f in os.listdir(output_dir)
        if f.startswith("bilan_orchestrateur_") and f.endswith(".json")
    ])
    for bf in reversed(bilan_files):
        _b = _load_json(os.path.join(output_dir, bf))
        _eff = _b.get("qalitas_stats", {}).get("agent4_efficacy", {})
        if _eff:
            efficacite_stats = _eff
            break
    # Fallback : lire depuis agent4_latest.json (summary.efficacite si present)
    if not efficacite_stats:
        _a4_sum = a4.get("summary", {})
        efficacite_stats = _a4_sum.get("efficacite", {})

    taux_efficacite  = efficacite_stats.get("taux_efficacite_pct", 0)
    nb_efficaces     = efficacite_stats.get("nb_efficaces", 0)
    total_actions_ev = efficacite_stats.get("total_actions_evaluees", 0)

    # --- Filtres risques critiques/eleves (CORRECTION : champ 'risque' pas 'intitule') ---
    risques_prioritaires = [
        r for r in risques_ev
        if str(r.get("niveau_residuel", "")).lower() in ("critique", "eleve", "moyen")
    ]
    risques_prioritaires.sort(
        key=lambda x: (
            {"critique": 3, "eleve": 2, "moyen": 1}.get(str(x.get("niveau_residuel", "")).lower(), 0),
            float(x.get("score_residuel", 0) or 0)
        ),
        reverse=True
    )

    # Top 5 plan de traitement
    top5_plan = sorted(
        plan_traitement,
        key=lambda x: float(x.get("indice_priorite", 0) or 0),
        reverse=True
    )[:5]

    # --- Helpers HTML ---
    def _esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _badge(niveau, text=None):
        niv = str(niveau).lower()
        display = text or niveau
        cls_map = {
            "critique": "badge-critique", "eleve": "badge-eleve",
            "moyen": "badge-moyen", "mineur": "badge-mineur",
            "elevee": "badge-eleve", "moyenne": "badge-moyen", "faible": "badge-mineur",
            "prioritaire": "badge-prioritaire",
            "complementaire": "badge-complementaire",
        }
        cls = cls_map.get(niv, "badge-moyen")
        return f"<span class='{cls}'>{_esc(display)}</span>"

    def _tr_risk(r):
        # CORRECTION : utiliser 'risque' (pas 'intitule') et 'statut_decisionnel' (pas 'statut')
        code     = r.get("code", "?")
        intitule = r.get("risque", r.get("Intitule", r.get("intitule", "?")))
        processus = r.get("processus", r.get("perimetre", "?"))
        niv      = str(r.get("niveau_residuel", "")).lower()
        score    = r.get("score_residuel", "?")
        statut   = r.get("statut_decisionnel", r.get("statut", "?"))
        priority = float(r.get("priority_score", 0) or 0)
        return (
            f"<tr>"
            f"<td><strong>{_esc(code)}</strong></td>"
            f"<td>{_esc(str(intitule)[:70])}</td>"
            f"<td>{_esc(str(processus)[:40])}</td>"
            f"<td>{_badge(niv)}</td>"
            f"<td style='text-align:center;font-weight:bold'>{score}</td>"
            f"<td>{_esc(str(statut)[:40])}</td>"
            f"<td style='text-align:center'>{priority:.1f}</td>"
            f"</tr>"
        )

    def _tr_plan(p):
        nb_actions = len(p.get("actions", []))
        classe     = str(p.get("classe", "")).lower()
        return (
            f"<tr>"
            f"<td><strong>{_esc(p.get('risque_code', '?'))}</strong></td>"
            f"<td>{_esc(str(p.get('risque_intitule', '?'))[:65])}</td>"
            f"<td>{_esc(p.get('processus', '?')[:35])}</td>"
            f"<td>{_badge(str(p.get('niveau_residuel', '?')).lower())}</td>"
            f"<td>{_esc(p.get('strategie', '?'))}</td>"
            f"<td>{_badge(classe, str(p.get('classe','?')).upper())}</td>"
            f"<td style='text-align:center'>{nb_actions}</td>"
            f"<td style='text-align:center;font-weight:bold'>{float(p.get('indice_priorite', 0) or 0):.2f}</td>"
            f"</tr>"
        )

    def _tr_alert(a):
        gravite = str(a.get("gravite_final", a.get("gravite_regle", "?"))).lower()
        return (
            f"<tr>"
            f"<td>{_esc(a.get('process_name', '?')[:35])}</td>"
            f"<td>{_esc(str(a.get('type_risque_final', a.get('type_risque_regle', '?')))[:35])}</td>"
            f"<td>{_esc(str(a.get('resume_final', a.get('snippet', '?')))[:70])}</td>"
            f"<td>{_badge(gravite)}</td>"
            f"<td style='text-align:center'>{a.get('criticality_score', '?')}</td>"
            f"</tr>"
        )

    def _tr_opp(o):
        return (
            f"<tr>"
            f"<td>{_esc(str(o.get('processus', o.get('process_name', '?')))[:35])}</td>"
            f"<td>{_esc(str(o.get('risque_source', o.get('resume_final', '?')))[:65])}</td>"
            f"<td>{o.get('reduction_rpn_pct', o.get('reduction_pct', '?'))}%</td>"
            f"<td>{_badge(str(o.get('niveau_opportunite', o.get('niveau', '?'))).lower())}</td>"
            f"<td>{_esc(str(o.get('strategie_opportunite', o.get('strategie', '?')))[:40])}</td>"
            f"</tr>"
        )

    rows_risks  = "".join(_tr_risk(r)  for r in risques_prioritaires[:15])
    rows_plan   = "".join(_tr_plan(p)  for p in top5_plan)
    rows_alerts = "".join(_tr_alert(a) for a in all_alerts[:15])

    # Opportunites : combiner Agent2 + Agent4
    opps_all = list(opps_ev)
    for a in all_alerts:
        if str(a.get("opportunite", "")).lower() in ("true", "1", "yes"):
            opps_all.append(a)
    rows_opps = "".join(_tr_opp(o) for o in opps_all[:10])

    # Repartition par processus Agent2
    by_proc_a2 = summary_a2.get("by_processus", {})
    proc_rows_a2 = "".join(
        f"<tr><td>{_esc(proc[:50])}</td><td style='text-align:center'>{cnt}</td></tr>"
        for proc, cnt in sorted(by_proc_a2.items(), key=lambda x: x[1], reverse=True)
    )

    # Types alertes Agent4 (top 8)
    type_rows_a4 = "".join(
        f"<tr><td>{_esc(t[:45])}</td><td style='text-align:center'>{c}</td></tr>"
        for t, c in sorted(by_type_a4.items(), key=lambda x: x[1], reverse=True)[:8]
    )

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Rapport Plateforme Multi-Agents QSE - {now}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: Segoe UI, Arial, sans-serif; margin: 0; background: #f4f6f9; color: #222; }}
  header {{ background: #003366; color: white; padding: 24px 40px; }}
  header h1 {{ margin: 0; font-size: 1.6em; }}
  header p  {{ margin: 4px 0 0; opacity: 0.8; font-size: 0.9em; }}
  .container {{ max-width: 1200px; margin: 30px auto; padding: 0 20px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 30px; }}
  .card {{ background: white; border-radius: 10px; padding: 18px 22px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08); flex: 1; min-width: 150px; text-align: center; }}
  .card .val {{ font-size: 2.2em; font-weight: bold; color: #003366; }}
  .card .lbl {{ font-size: 0.8em; color: #666; margin-top: 4px; line-height: 1.3; }}
  .card.alert  {{ border-top: 4px solid #e74c3c; }}
  .card.ok     {{ border-top: 4px solid #27ae60; }}
  .card.warn   {{ border-top: 4px solid #e67e22; }}
  .card.info   {{ border-top: 4px solid #2980b9; }}
  .section-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 30px; }}
  h2 {{ color: #003366; border-bottom: 2px solid #003366; padding-bottom: 6px; margin-top: 40px; }}
  h3 {{ color: #003366; font-size: 1em; margin: 20px 0 8px; }}
  table {{ width: 100%; border-collapse: collapse; background: white;
           border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.06); margin-bottom: 20px; }}
  th {{ background: #003366; color: white; padding: 9px 10px; text-align: left; font-size: 0.82em; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #eee; font-size: 0.82em; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f0f4ff; }}
  .badge-critique   {{ background: #fde8e8; color: #c0392b; font-weight: bold;
                       padding: 2px 7px; border-radius: 4px; white-space: nowrap; }}
  .badge-eleve      {{ background: #fef3e2; color: #e67e22; font-weight: bold;
                       padding: 2px 7px; border-radius: 4px; white-space: nowrap; }}
  .badge-moyen      {{ background: #fff9e0; color: #d68910; font-weight: bold;
                       padding: 2px 7px; border-radius: 4px; white-space: nowrap; }}
  .badge-mineur     {{ background: #e8f8e8; color: #27ae60; font-weight: bold;
                       padding: 2px 7px; border-radius: 4px; white-space: nowrap; }}
  .badge-prioritaire    {{ background: #fef3e2; color: #e67e22; font-weight: bold;
                           padding: 2px 7px; border-radius: 4px; white-space: nowrap; }}
  .badge-complementaire {{ background: #e8f8e8; color: #27ae60; font-weight: bold;
                           padding: 2px 7px; border-radius: 4px; white-space: nowrap; }}
  .kpi-bar {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }}
  .kpi {{ background: white; border-radius: 6px; padding: 10px 16px;
           box-shadow: 0 1px 4px rgba(0,0,0,0.07); flex: 1; min-width: 120px; text-align: center; }}
  .kpi .v {{ font-size: 1.5em; font-weight: bold; color: #003366; }}
  .kpi .l {{ font-size: 0.75em; color: #888; }}
  .efficacite-bar {{ background: #eee; border-radius: 20px; height: 22px; margin: 8px 0; overflow: hidden; }}
  .efficacite-fill {{ background: linear-gradient(90deg, #27ae60, #2ecc71); height: 100%;
                      display: flex; align-items: center; justify-content: center;
                      color: white; font-weight: bold; font-size: 0.85em; border-radius: 20px; }}
  .no-data {{ text-align: center; color: #999; padding: 20px; font-style: italic; }}
  footer {{ text-align: center; padding: 30px; color: #999; font-size: 0.8em; margin-top: 40px;
             border-top: 1px solid #e0e0e0; }}
</style>
</head>
<body>
<header>
  <h1>Plateforme Multi-Agents QSE &mdash; Rapport de Synthese</h1>
  <p>Genere le {now} &nbsp;|&nbsp; Conforme au CDC TBR.TIM.IA.S5/2025-2026 &nbsp;|&nbsp; 4 agents IA &bull; LangGraph</p>
</header>

<div class="container">

  <!-- ============================================================ -->
  <!-- KPI GLOBAUX                                                   -->
  <!-- ============================================================ -->
  <h2>Indicateurs Globaux du Pipeline</h2>
  <div class="cards">
    <div class="card info">
      <div class="val">{total_ro}</div>
      <div class="lbl">R&O Identifies<br>(Agent 1)</div>
    </div>
    <div class="card info">
      <div class="val">{nb_risques_ev}</div>
      <div class="lbl">Risques Evalues<br>(Agent 2)</div>
    </div>
    <div class="card info">
      <div class="val">{nb_oppty_ev}</div>
      <div class="lbl">Opportunites Evaluees<br>(Agent 2)</div>
    </div>
    <div class="card {'alert' if nb_critique + nb_eleve > 0 else 'ok'}">
      <div class="val">{nb_critique + nb_eleve}</div>
      <div class="lbl">Risques Critiques<br>ou Eleves</div>
    </div>
    <div class="card warn">
      <div class="val">{nb_plan}</div>
      <div class="lbl">Entrees Plan<br>Traitement (Agent 3)</div>
    </div>
    <div class="card warn">
      <div class="val">{nb_alerts}</div>
      <div class="lbl">Alertes Monitoring<br>(Agent 4)</div>
    </div>
    <div class="card {'ok' if taux_efficacite >= 80 else 'warn'}">
      <div class="val">{taux_efficacite:.0f}%</div>
      <div class="lbl">Taux d'Efficacite<br>Actions (Agent 4)</div>
    </div>
  </div>

  <!-- ============================================================ -->
  <!-- AGENT 2 : EVALUATION                                         -->
  <!-- ============================================================ -->
  <h2>Agent 2 &mdash; Evaluation des Risques</h2>

  <div class="kpi-bar">
    <div class="kpi"><div class="v" style="color:#c0392b">{nb_critique}</div><div class="l">Critique</div></div>
    <div class="kpi"><div class="v" style="color:#e67e22">{nb_eleve}</div><div class="l">Eleve</div></div>
    <div class="kpi"><div class="v" style="color:#d68910">{nb_moyen}</div><div class="l">Moyen</div></div>
    <div class="kpi"><div class="v" style="color:#27ae60">{nb_mineur}</div><div class="l">Mineur</div></div>
    <div class="kpi"><div class="v">{nb_a_traiter}</div><div class="l">A Traiter</div></div>
    <div class="kpi"><div class="v">{nb_surveiller}</div><div class="l">A Surveiller</div></div>
    <div class="kpi"><div class="v">{nb_acceptable}</div><div class="l">Acceptable</div></div>
    <div class="kpi"><div class="v" style="color:#8e44ad">{reevaluation_requise}</div><div class="l">Reevaluation requise</div></div>
  </div>

  <h3>Risques Evalues par Niveau de Criticite (Top 15)</h3>
  <table>
    <thead>
      <tr>
        <th>Code</th><th>Intitule du Risque</th><th>Processus</th>
        <th>Niveau Residuel</th><th>Score</th><th>Statut Decisionnel</th><th>Priorite</th>
      </tr>
    </thead>
    <tbody>
      {rows_risks if rows_risks else f'<tr><td colspan="7" class="no-data">Aucun risque moyen/eleve/critique detecte</td></tr>'}
    </tbody>
  </table>

  <div class="section-grid">
    <div>
      <h3>Repartition par Processus</h3>
      <table>
        <thead><tr><th>Processus</th><th style="text-align:center">Nb Risques</th></tr></thead>
        <tbody>
          {proc_rows_a2 if proc_rows_a2 else '<tr><td colspan="2" class="no-data">-</td></tr>'}
        </tbody>
      </table>
    </div>
    <div>
      <h3>Opportunites Identifiees</h3>
      <table>
        <thead>
          <tr><th>Processus</th><th>Description</th><th>Red. RPN</th><th>Niveau</th><th>Strategie</th></tr>
        </thead>
        <tbody>
          {rows_opps if rows_opps else '<tr><td colspan="5" class="no-data">Aucune opportunite detectee</td></tr>'}
        </tbody>
      </table>
    </div>
  </div>

  <!-- ============================================================ -->
  <!-- AGENT 3 : PLAN DE TRAITEMENT                                 -->
  <!-- ============================================================ -->
  <h2>Agent 3 &mdash; Plan de Traitement (Top 5 Prioritaires)</h2>
  <table>
    <thead>
      <tr>
        <th>Code</th><th>Intitule</th><th>Processus</th><th>Niveau</th>
        <th>Strategie</th><th>Classe</th><th>Actions</th><th>Indice Priorite</th>
      </tr>
    </thead>
    <tbody>
      {rows_plan if rows_plan else '<tr><td colspan="8" class="no-data">Plan de traitement non disponible</td></tr>'}
    </tbody>
  </table>
  <p style="font-size:0.85em;color:#666;">
    {nb_plan} entrees au plan de traitement &bull; {nb_accept} risques acceptes formalises
  </p>

  <!-- ============================================================ -->
  <!-- AGENT 4 : MONITORING                                         -->
  <!-- ============================================================ -->
  <h2>Agent 4 &mdash; Monitoring Continu & Alertes</h2>

  <div class="kpi-bar">
    <div class="kpi"><div class="v">{nb_alerts}</div><div class="l">Alertes totales</div></div>
    <div class="kpi"><div class="v" style="color:#c0392b">{by_gravity.get('elevee', 0)}</div><div class="l">Gravite Elevee</div></div>
    <div class="kpi"><div class="v" style="color:#e67e22">{by_gravity.get('moyenne', 0)}</div><div class="l">Gravite Moyenne</div></div>
    <div class="kpi"><div class="v" style="color:#27ae60">{by_gravity.get('faible', 0)}</div><div class="l">Gravite Faible</div></div>
    <div class="kpi"><div class="v">{nb_risques_a4}</div><div class="l">Signaux Risque</div></div>
    <div class="kpi"><div class="v">{nb_opps_a4}</div><div class="l">Signaux Opportunite</div></div>
    <div class="kpi"><div class="v">{len(capitalisations)}</div><div class="l">Capitalisations</div></div>
  </div>

  <h3>Efficacite des Actions (evaluation sur {total_actions_ev} actions)</h3>
  <div class="efficacite-bar">
    <div class="efficacite-fill" style="width:{min(taux_efficacite, 100):.0f}%">
      {taux_efficacite:.1f}% efficaces ({nb_efficaces}/{total_actions_ev})
    </div>
  </div>

  <div class="section-grid">
    <div>
      <h3>Top Alertes par Signal (Top 15)</h3>
      <table>
        <thead>
          <tr><th>Processus</th><th>Type</th><th>Signal detecte</th><th>Gravite</th><th>Score</th></tr>
        </thead>
        <tbody>
          {rows_alerts if rows_alerts else '<tr><td colspan="5" class="no-data">Aucune alerte detectee</td></tr>'}
        </tbody>
      </table>
    </div>
    <div>
      <h3>Repartition Alertes par Type</h3>
      <table>
        <thead><tr><th>Type d'alerte</th><th style="text-align:center">Nb</th></tr></thead>
        <tbody>
          {type_rows_a4 if type_rows_a4 else '<tr><td colspan="2" class="no-data">-</td></tr>'}
        </tbody>
      </table>
    </div>
  </div>

</div>

<footer>
  Plateforme Multi-Agents QSE &bull; TBR.TIM.IA.S5/2025-2026
  &bull; Agent 1 (Identification) &bull; Agent 2 (Evaluation) &bull; Agent 3 (Traitement) &bull; Agent 4 (Monitoring)
  &bull; Genere le {now}
</footer>
</body>
</html>
"""

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(output_dir, f"rapport_final_{ts}.html")
    stable   = os.path.join(output_dir, "rapport_final_latest.html")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    with open(stable, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info("Rapport HTML genere : %s", out_path)
    return out_path


# ===================================================================
# AFFICHAGE DU BILAN FINAL
# ===================================================================

def print_bilan(rapport: Dict) -> None:
    """Affiche le bilan synthetique de l'execution."""
    print("\n" + "=" * 65)
    print("  BILAN PLATEFORME MULTI-AGENTS QSE")
    print("=" * 65)
    print(f"  Run ID         : {rapport.get('run_id', '?')}")
    print(f"  Debut          : {rapport.get('started_at', '?')[:19]}")
    print(f"  Fin            : {rapport.get('ended_at', '?')[:19]}")
    print(f"  Mode           : {'DRY-RUN (simulation)' if rapport.get('dry_run') else 'REEL (QALITAS)'}")
    print(f"  Iterations     : {rapport.get('iterations', 0)}")
    print(f"  Statut         : {rapport.get('status', '?').upper()}")
    print("-" * 65)

    if "agent1" in rapport:
        a1 = rapport["agent1"]
        print(f"  Agent 1        : {a1.get('total_ro', 0)} R&O identifies"
              f"{' [ERREUR]' if a1.get('error') else ''}")

    if "agent2" in rapport:
        a2 = rapport["agent2"]
        print(f"  Agent 2        : {a2.get('risques', 0)} risques + "
              f"{a2.get('opportunites', 0)} opportunites evalues"
              f"{' [ERREUR]' if a2.get('error') else ''}")

    if "agent3" in rapport:
        a3 = rapport["agent3"]
        print(f"  Agent 3        : {a3.get('plan_traitement', 0)} entrees plan | "
              f"{a3.get('acceptations', 0)} acceptations formalisees"
              f"{' [ERREUR]' if a3.get('error') else ''}")

    if "agent4" in rapport:
        a4 = rapport["agent4"]
        print(f"  Agent 4        : Monitoring termine"
              f"{' [ERREUR]' if a4.get('error') else ''}")

    # LangGraph stats
    if rapport.get("agent1_ro") is not None:
        print(f"  Agent 1        : {rapport['agent1_ro']} R&O identifies")
        print(f"  Agent 2        : {rapport.get('agent2_evaluated', 0)} R&O evalues")
        print(f"  Agent 3        : {rapport.get('agent3_plan', 0)} entrees plan")
        print(f"  Agent 4        : {rapport.get('agent4_alerts', 0)} alertes")

    if rapport.get("errors"):
        print(f"  Avertissements : {len(rapport['errors'])} erreur(s) non fatale(s)")
        for e in rapport["errors"][:3]:
            print(f"    - {str(e)[:80]}")

    if "reeval_history" in rapport:
        print(f"  Reevaluations  : {len(rapport['reeval_history'])} declenchee(s)")

    print("=" * 65)


# ===================================================================
# POINT D'ENTREE CLI
# ===================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Plateforme Multi-Agents QSE - Orchestrateur Principal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python main_orchestrator.py                  # Pipeline complet dry-run
  python main_orchestrator.py --real           # Pipeline complet QALITAS reel
  python main_orchestrator.py --agent 1        # Agent 1 uniquement
  python main_orchestrator.py --agent 2 3      # Agents 2 et 3 uniquement
  python main_orchestrator.py --report         # Rapport HTML depuis derniere exec
  python main_orchestrator.py --langgraph      # Forcer mode LangGraph
        """
    )

    parser.add_argument(
        "--real",
        action="store_true",
        help="Mode reel : injection et lecture reelle dans QALITAS"
    )
    parser.add_argument(
        "--agent",
        type=int,
        nargs="+",
        choices=[1, 2, 3, 4],
        default=[1, 2, 3, 4],
        metavar="N",
        help="Agents a executer (ex: --agent 1 2 3 4). Defaut: tous."
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=3,
        dest="max_iter",
        help="Nombre maximum d'iterations de reevaluation (defaut: 3)"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generer uniquement le rapport HTML (sans relancer les agents)"
    )
    parser.add_argument(
        "--langgraph",
        action="store_true",
        help="Forcer le mode LangGraph (defaut: auto-detection)"
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Forcer le mode sequentiel (sans LangGraph)"
    )

    args = parser.parse_args()
    dry_run = not args.real

    # Rapport HTML uniquement
    if args.report:
        out = generate_html_report()
        print(f"\nRapport HTML genere : {out}")
        return

    print("\n" + "=" * 65)
    print("  PLATEFORME MULTI-AGENTS QSE")
    print("  Systeme intelligent de pilotage des Risques & Opportunites")
    print(f"  Mode : {'REEL (injection QALITAS)' if not dry_run else 'DRY-RUN (simulation)'}")
    print(f"  Agents : {args.agent}")
    print("=" * 65 + "\n")

    # Choix du mode d'execution
    if args.langgraph:
        rapport = run_pipeline_langgraph(dry_run=dry_run, max_iterations=args.max_iter)
    elif args.sequential:
        rapport = run_pipeline_sequential(
            dry_run=dry_run,
            max_iterations=args.max_iter,
            agents=args.agent,
        )
    else:
        # Auto-detection : essayer LangGraph si tous les agents sont demandes
        if set(args.agent) == {1, 2, 3, 4}:
            try:
                from langgraph.graph import StateGraph
                rapport = run_pipeline_langgraph(
                    dry_run=dry_run,
                    max_iterations=args.max_iter,
                )
            except ImportError:
                logger.info("LangGraph non installe -> mode sequentiel.")
                rapport = run_pipeline_sequential(
                    dry_run=dry_run,
                    max_iterations=args.max_iter,
                    agents=args.agent,
                )
        else:
            rapport = run_pipeline_sequential(
                dry_run=dry_run,
                max_iterations=args.max_iter,
                agents=args.agent,
            )

    # Bilan console
    print_bilan(rapport)

    # Sauvegarde du bilan
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bilan_path = os.path.join(OUTPUT_DIR, f"bilan_orchestrateur_{ts}.json")
    with open(bilan_path, "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=2, default=str)
    logger.info("Bilan sauvegarde : %s", bilan_path)

    # Rapport HTML automatique
    try:
        html_path = generate_html_report()
        print(f"\nRapport HTML disponible : {html_path}")
    except Exception as exc:
        logger.warning("Rapport HTML non genere : %s", exc)


if __name__ == "__main__":
    main()
