"""
Orchestrateur LangGraph - Plateforme Multi-Agents QSE QALITAS.
===============================================================
Conforme au cahier des charges TBR.TIM.IA.S5/2025-2026.

Architecture du graphe (StateGraph) :
--------------------------------------

    [START]
       |
       v
  [ agent1_node ]  Identification & Caracterisation des R&O
       |
       v
  [ agent2_node ]  Analyse & Evaluation (brut / maitrise / residuel)
       |
       v
  [ agent3_node ]  Generation des Actions de Traitement
       |
       v
  [ agent4_node ]  Suivi, Reevaluation & Pilotage Continu
       |
       v (reeval_required=False)
     [END]
       |
       ^ (reeval_required=True, iteration < max_iterations)
       |
       +-- retour vers agent2_node (reevaluation declenchee)

Chaque noeud :
    - Recoit le PipelineState complet
    - Execute son agent
    - Retourne un dict partiel (seuls les champs qu'il modifie)
    - LangGraph fusionne automatiquement dans l'etat global

Installation requise (une seule fois) :
    pip install langgraph langchain langchain-core langchain-community

"""

import logging
import os
import sys
from typing import Any, Dict, Literal

# Ajout du dossier racine au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator.state import PipelineState

logger = logging.getLogger("orchestrator.graph")


# =============================================================================
# NOEUDS DU GRAPHE
# =============================================================================

def agent1_node(state: PipelineState) -> Dict[str, Any]:
    """
    Noeud Agent 1 : Identification & Caracterisation des R&O.

    Utilise agents/agent1_identification.py (CDC §1) :
    - Source 1 : QALITAS API (registre officiel risques + opportunites)
    - Source 2 : Excel (cartographie, KPI hors cible, NC recurrentes)
    - Source 3 : PDF dashboard (alertes textuelles)
    - Deduplication et enrichissement LLM

    Sortie : state["ro_register"] = liste normalisee des R&O
    """
    logger.info("[Agent1] Demarrage - Identification & Caracterisation (CDC §1)")

    data_dir = state.get("data_dir", "donnees")
    dry_run  = state.get("dry_run", True)
    errors   = list(state.get("errors", []))

    try:
        from agents.agent1_identification import run_agent1
        rapport = run_agent1(
            data_dir=data_dir,
            use_api=True,
            dry_run=dry_run,
            llm_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        )
        ro_register = rapport.get("register", [])

        logger.info(
            "[Agent1] %d R&O identifies | risques=%d opportunites=%d | "
            "sources=%s",
            len(ro_register),
            sum(1 for r in ro_register if r.get("type") == "risque"),
            sum(1 for r in ro_register if r.get("type") == "opportunite"),
            rapport.get("summary", {}).get("par_source", {}),
        )

        return {
            "ro_register": ro_register,
            "errors":      errors,
        }

    except Exception as exc:
        logger.error("[Agent1] Erreur agent1_identification : %s", exc)
        errors.append(f"Agent1: {exc}")
        # Fallback : chargement direct Excel cartographie
        try:
            from loaders.load_excel_data import load_all_context
            import re
            context  = load_all_context(data_dir)
            def _c(v):
                s = str(v).strip() if v is not None else ""
                s = re.sub(r"<[^>]+>", " ", s)
                return "" if s.lower() in ("none", "nan") else s
            ro_register = []
            for entry in context.get("cartographie", []):
                ro_register.append({
                    "type":      "risque",
                    "code":      _c(entry.get("Code", entry.get("Reference", ""))),
                    "intitule":  _c(entry.get("Intitule", entry.get("Risques", ""))),
                    "causes":    [_c(entry.get("Causes", "Non renseigne"))],
                    "consequences": [_c(entry.get("Effets Negatifs", "Non renseigne"))],
                    "perimetre": _c(entry.get("Processus", "")),
                    "domaine":   "Q",
                    "lien_iso":  "ISO 9001:2015 §6.1",
                    "source":    "Agent1_Fallback_Excel",
                    **{k: v for k, v in entry.items()
                       if k not in ("Code", "Intitule", "Causes", "Processus")},
                })
            logger.warning(
                "[Agent1] Fallback Excel : %d R&O charges.", len(ro_register)
            )
            return {"ro_register": ro_register, "errors": errors}
        except Exception as exc2:
            logger.error("[Agent1] Fallback Excel echoue : %s", exc2)
            errors.append(f"Agent1_fallback: {exc2}")
            return {"ro_register": [], "errors": errors}


def agent2_node(state: PipelineState) -> Dict[str, Any]:
    """
    Noeud Agent 2 : Analyse & Evaluation des R&O (CDC §2).

    Flux :
    1. Charger le contexte complet (Excel/QALITAS) via load_all_context
       -> necessite pour evaluate_all (champs F, G, M, mesures, NC, KPI...)
    2. Integrer les R&O supplementaires de Agent1 (sources PDF, KPI-hors-cible,
       NC recurrentes) comme entrees additionnelles de la cartographie
    3. evaluate_all(context) -> {risques, opportunites, summary, ...}
    4. enrich_evaluations(risques, opportunites) -> enrichissement LLM
    5. Injection QALITAS (appreciation + score residuel)

    En reevaluation ciblee (Agent4->Agent2) :
    - Ne recalculer QUE les risques cibles (codes dans reeval_risques)
    - Conserver les evaluations precedentes des autres risques
    """
    logger.info("[Agent2] Demarrage - Analyse & Evaluation (iteration %d)",
                state.get("iteration", 1))

    from agents.agent2_evaluation import evaluate_all
    from agents.agent2_llm import enrich_evaluations

    data_dir        = state.get("data_dir", "donnees")
    ro_register     = state.get("ro_register", [])
    reeval_required = state.get("reeval_required", False)
    reeval_risques  = state.get("reeval_risques", [])
    errors          = list(state.get("errors", []))
    dry_run         = state.get("dry_run", True)
    prev_qstats     = dict(state.get("qalitas_stats") or {})

    # ------------------------------------------------------------------
    # Etape 1 : Chargement unifie (API + inbox Excel/PDF + donnees/)
    # Le Unified Loader fusionne toutes les sources en un seul contexte.
    # ------------------------------------------------------------------
    try:
        from loaders.unified_loader import load_unified_context
        context = load_unified_context(data_dir=data_dir)
        logger.info(
            "[Agent2] Contexte unifie : %d KPIs | %d NC | %d Carto | mode=%s",
            len(context.get("kpis", [])),
            len(context.get("nc", [])),
            len(context.get("cartographie", [])),
            context.get("_load_mode", "?"),
        )
    except Exception as exc:
        logger.warning("[Agent2] Unified loader echoue (%s), fallback Excel.", exc)
        try:
            from loaders.load_excel_data import load_all_context
            context = load_all_context(data_dir)
        except Exception as exc2:
            logger.error("[Agent2] Impossible de charger le contexte : %s", exc2)
            errors.append(f"Agent2_context: {exc2}")
            return {"evaluated_ro": state.get("evaluated_ro", []), "errors": errors}

    # ------------------------------------------------------------------
    # Etape 2 : Integrer les entrees supplementaires de Agent1
    # (R&O detectes depuis KPI hors-cible, NC recurrentes, PDF, QALITAS)
    # qui ne sont pas dans la cartographie Excel brute
    # ------------------------------------------------------------------
    agent1_extras = [
        ro for ro in ro_register
        if ro.get("source") not in (
            "Excel_Cartographie", "Agent1_Excel", "Agent1_Fallback_Excel"
        ) and ro.get("intitule")
    ]

    if agent1_extras:
        # Convertir les entrees Agent1 au format attendu par evaluate_all
        # (champs compatibles avec la cartographie Excel)
        extra_carto = []
        for ro in agent1_extras:
            causes = ro.get("causes", [])
            if isinstance(causes, list):
                causes = "; ".join(causes)
            consq = ro.get("consequences", [])
            if isinstance(consq, list):
                consq = "; ".join(consq)

            # Recuperer les scores depuis les champs Agent1 (lowercase)
            # ipr = IPR pre-calcule = F*G*D (plus precis que F*G seul)
            ipr = ro.get("ipr")
            det = ro.get("detectabilite")

            # On passe toujours l'IPR comme RPN (F*G*D complet).
            # On ne construit PAS d'Appreciation F*G : compute_score_brut
            # privilegierait F*G et perdrait la composante D, sous-estimant
            # le score brut de ~33% pour une detectabilite standard de 3.
            extra_carto.append({
                "Code":             ro.get("code", ""),
                "Risques":          ro.get("intitule", ""),
                "Causes":           causes,
                "Effets Negatifs":  consq,
                "Processus":        ro.get("perimetre", ""),
                "Systeme":          ro.get("domaine", "Q"),
                # RPN = IPR complet -> utilise directement par compute_score_brut
                "RPN":              ipr,
                "Maitrise":         det if det is not None else 3,
                "_source_agent1":   ro.get("source", ""),
            })
        context.setdefault("cartographie", []).extend(extra_carto)
        logger.info(
            "[Agent2] %d R&O supplementaires de Agent1 integres (sources: %s)",
            len(agent1_extras),
            list({r.get("source", "?") for r in agent1_extras})
        )

    # ------------------------------------------------------------------
    # Etape 3 : Filtrage en cas de reevaluation ciblee
    # ------------------------------------------------------------------
    conserves: list = []

    if reeval_required and reeval_risques:
        codes_cibles = set(str(c).strip() for c in reeval_risques)
        context["cartographie"] = [
            e for e in context.get("cartographie", [])
            if str(e.get("Code", "")).strip() in codes_cibles
        ]
        logger.info(
            "[Agent2] Reevaluation ciblee : %d entrees retenues (motif: %s)",
            len(context["cartographie"]),
            state.get("reeval_trigger", "inconnu")
        )
        # Conserver les evaluations precedentes non recalculees
        prev_evaluated = state.get("evaluated_ro", [])
        conserves = [
            e for e in prev_evaluated
            if str(e.get("code", "")).strip() not in codes_cibles
        ]

    if not context.get("cartographie"):
        logger.warning("[Agent2] Cartographie vide apres filtrage.")
        return {
            "evaluated_ro":    state.get("evaluated_ro", []),
            "reeval_required": False,
            "reeval_trigger":  "",
            "reeval_risques":  [],
            "errors":          errors,
        }

    # ------------------------------------------------------------------
    # Etape 4 : Evaluation regle-metier (score brut, maitrise, residuel)
    # ------------------------------------------------------------------
    try:
        eval_result  = evaluate_all(context)
        risques      = eval_result.get("risques", [])
        opportunites = eval_result.get("opportunites", [])

        logger.info(
            "[Agent2] Evaluation regle-metier : %d risques, %d opportunites",
            len(risques), len(opportunites)
        )
    except Exception as exc:
        logger.error("[Agent2] evaluate_all echoue : %s", exc)
        errors.append(f"Agent2_eval: {exc}")
        return {"evaluated_ro": state.get("evaluated_ro", []), "errors": errors}

    # ------------------------------------------------------------------
    # Etape 4b : Enrichissement des _raw avec les GUIDs QALITAS
    # Indispensable pour que Agent3 retrouve RiskOpportunityId et cree
    # des actions correctement liees dans QALITAS.
    # Strategie 1 : matcher depuis context["risques"] (Code/Id)
    # Strategie 2 : get_risks() direct si Strategie 1 insuffisante
    # ------------------------------------------------------------------
    try:
        # Enrichissement inline : matcher chaque risque evalue contre le contexte API
        # pour recuperer RiskOpportunityId + RiskOpportunityEvaluationId depuis _raw
        _code_to_raw: dict = {}
        for entry in context.get("risques", []):
            code = str(entry.get("Code") or entry.get("code") or "").strip()
            raw  = entry.get("_raw", {})
            if code and raw:
                _code_to_raw[code] = raw
        enriched_count = 0
        for r in risques:
            if r.get("_raw") and r["_raw"].get("RiskOpportunityId"):
                continue  # deja enrichi
            code = str(r.get("code") or "").strip()
            if code and code in _code_to_raw:
                r["_raw"] = r.get("_raw") or {}
                r["_raw"].update(_code_to_raw[code])
                enriched_count += 1
        logger.info("[Agent2] Enrichissement _raw GUIDs : %d/%d risques enrichis.",
                    enriched_count, len(risques))
    except Exception as exc:
        logger.warning("[Agent2] Enrichissement _raw GUIDs echoue : %s", exc)

    # ------------------------------------------------------------------
    # Etape 5 : Enrichissement LLM (justification, impact, recommandation)
    # ------------------------------------------------------------------
    try:
        enrich_result   = enrich_evaluations(risques, opportunites)
        risques_enrichis = enrich_result.get("risques_enrichis", risques)
        opps_enrichies   = enrich_result.get("opportunites_enrichies", opportunites)
    except Exception as exc:
        logger.warning("[Agent2] enrich_evaluations echoue : %s. Pas d'enrichissement.", exc)
        risques_enrichis = risques
        opps_enrichies   = opportunites

    # ------------------------------------------------------------------
    # Etape 6 : Injection QALITAS (appreciation evaluee)
    # ------------------------------------------------------------------
    try:
        from loaders.qalitas_api_client import QalitasClient, DEFAULT_BASE_URL
        from loaders.qalitas_api_writer import QalitasWriter, inject_agent2_results

        qalitas_url  = os.environ.get("QALITAS_BASE_URL", DEFAULT_BASE_URL)
        qalitas_user = os.environ.get("QALITAS_USERNAME", "MOHAMED.N")
        qalitas_pass = os.environ.get("QALITAS_PASSWORD", "MOHAMED.N")

        client = QalitasClient(
            base_url=qalitas_url,
            username=qalitas_user,
            password=qalitas_pass,
        )
        # Toujours se connecter : necessaire pour charger les tables de config
        # (decisions, resultats) utilisees pour remplir l'historique evaluation,
        # meme si dry_run=True (lecture seule, pas d'ecriture).
        login_ok = client.login()
        if not login_ok:
            logger.warning("[Agent2] Echec login QALITAS — injection annulee.")
            errors.append("Agent2_login: echec authentification QALITAS")
        else:
            writer   = QalitasWriter(client=client, dry_run=dry_run)
            a2_stats = inject_agent2_results(risques_enrichis, writer)
            prev_qstats["agent2"] = a2_stats

    except Exception as exc:
        logger.warning("[Agent2] Injection QALITAS echouee : %s", exc)
        errors.append(f"Agent2_injection: {exc}")

    # ------------------------------------------------------------------
    # Etape 7 : Fusion avec evaluations conservees (reevaluation ciblee)
    # ------------------------------------------------------------------
    evaluated_ro_final = conserves + risques_enrichis + opps_enrichies

    logger.info(
        "[Agent2] Fin evaluation | total=%d | critique=%d eleve=%d moyen=%d mineur=%d | opps=%d",
        len(evaluated_ro_final),
        sum(1 for r in risques_enrichis if r.get("niveau_residuel") == "critique"),
        sum(1 for r in risques_enrichis if r.get("niveau_residuel") == "eleve"),
        sum(1 for r in risques_enrichis if r.get("niveau_residuel") == "moyen"),
        sum(1 for r in risques_enrichis if r.get("niveau_residuel") == "mineur"),
        len(opps_enrichies),
    )

    return {
        "evaluated_ro":    evaluated_ro_final,
        "reeval_required": False,
        "reeval_trigger":  "",
        "reeval_risques":  [],
        "qalitas_stats":   prev_qstats,
        "errors":          errors,
    }


def agent3_node(state: PipelineState) -> Dict[str, Any]:
    """
    Noeud Agent 3 : Generation des Actions de Traitement.

    Entrees : state["evaluated_ro"] (Agent2)
    Sorties : state["treatment_plan"]
    """
    logger.info("[Agent3] Demarrage - Generation des Actions de Traitement")

    from agents.agent3_treatment import build_treatment_plan
    from agents.agent3_main import (
        build_acceptance_records,
        inject_agent3_results,
    )
    from loaders.qalitas_api_client import QalitasClient, DEFAULT_BASE_URL
    from loaders.qalitas_api_writer import QalitasWriter

    evaluated_ro = state.get("evaluated_ro", [])
    dry_run      = state.get("dry_run", True)
    errors       = list(state.get("errors", []))

    if not evaluated_ro:
        logger.warning("[Agent3] Aucun R&O evalue disponible.")
        return {"treatment_plan": [], "errors": errors}

    try:
        plan = build_treatment_plan(
            evaluated_ro=evaluated_ro,
            seuil_niveau="moyen",
            max_actions_par_ro=3,
            enrichissement_llm=True,
        )

        # Injection QALITAS
        if plan:
            try:
                qalitas_url  = os.environ.get("QALITAS_BASE_URL", DEFAULT_BASE_URL)
                qalitas_user = os.environ.get("QALITAS_USERNAME", "MOHAMED.N")
                qalitas_pass = os.environ.get("QALITAS_PASSWORD", "MOHAMED.N")

                client = QalitasClient(
                    base_url=qalitas_url,
                    username=qalitas_user,
                    password=qalitas_pass,
                )
                if not dry_run:
                    client.login()

                writer   = QalitasWriter(client=client, dry_run=dry_run)
                a3_stats = inject_agent3_results(plan, writer, min_classe="prioritaire")

                prev_qstats = dict(state.get("qalitas_stats") or {})
                prev_qstats["agent3"] = a3_stats

            except Exception as exc:
                logger.warning("[Agent3] Injection QALITAS echouee : %s", exc)
                errors.append(f"Agent3_injection: {exc}")
                prev_qstats = dict(state.get("qalitas_stats") or {})

        logger.info(
            "[Agent3] Plan : %d entrees | critique=%d prioritaire=%d complementaire=%d",
            len(plan),
            sum(1 for e in plan if e.get("classe") == "critique"),
            sum(1 for e in plan if e.get("classe") == "prioritaire"),
            sum(1 for e in plan if e.get("classe") == "complementaire"),
        )

        return {
            "treatment_plan": plan,
            "qalitas_stats":  prev_qstats,
            "errors":         errors,
        }

    except Exception as exc:
        logger.error("[Agent3] Erreur : %s", exc)
        errors.append(f"Agent3: {exc}")
        return {"treatment_plan": [], "errors": errors}


def agent4_node(state: PipelineState) -> Dict[str, Any]:
    """
    Noeud Agent 4 : Suivi, Reevaluation & Pilotage Continu.

    Entrees : state["treatment_plan"] + contexte (PDF + Excel)
    Sorties : state["monitoring_alerts"]
              state["reeval_required"]   <- declencheur de reevaluation
              state["reeval_trigger"]    <- motif
              state["reeval_risques"]    <- codes des risques a reevaluer
    """
    logger.info("[Agent4] Demarrage - Suivi, Reevaluation & Pilotage Continu")

    from agents.agent4_monitoring import detect_pdf_alerts, compute_criticality_score, criticality_level
    from agents.agent4_excel_detection import detect_all_excel_alerts
    from agents.agent4_llm import enrich_alerts
    from loaders.load_excel_data import load_all_context
    from loaders.load_dashboard_pdf import load_dashboard_text
    from loaders.qalitas_api_client import QalitasClient, DEFAULT_BASE_URL
    from loaders.qalitas_api_writer import QalitasWriter
    from loaders.qalitas_api_writer import inject_agent4_results

    data_dir = state.get("data_dir", "donnees")
    pdf_path = state.get("pdf_path", os.path.join(data_dir, "dashboard.pdf"))
    dry_run  = state.get("dry_run", True)
    errors   = list(state.get("errors", []))
    iteration = int(state.get("iteration", 1))
    max_iter  = int(state.get("max_iterations", 3))

    try:
        # Chargement unifie (API + inbox Excel/PDF + donnees/)
        try:
            from loaders.unified_loader import load_unified_context
            context = load_unified_context(data_dir=data_dir)
            logger.info(
                "[Agent4] Contexte unifie : %d KPIs | %d NC | %d Carto | mode=%s",
                len(context.get("kpis", [])),
                len(context.get("nc", [])),
                len(context.get("cartographie", [])),
                context.get("_load_mode", "?"),
            )
        except Exception as _ctx_exc:
            logger.warning("[Agent4] Unified loader echoue (%s), fallback Excel.", _ctx_exc)
            context = load_all_context(data_dir)

        # Detection alertes PDF
        # Inclure les pages PDF de l'inbox si disponibles
        pdf_alerts: list = []
        all_pages: list = []

        # 1. PDF fixe (dashboard.pdf)
        if os.path.exists(pdf_path):
            all_pages.extend(load_dashboard_text(pdf_path))

        # 2. PDFs de l'inbox (deposes par les clients)
        all_pages.extend(context.get("dashboard_pages_inbox", []))

        if all_pages:
            pdf_alerts = detect_pdf_alerts(all_pages, context=context)
            logger.info("[Agent4] %d pages PDF analysees (%d alertes)",
                        len(all_pages), len(pdf_alerts))

        # Detection alertes Excel
        excel_alerts = detect_all_excel_alerts(context)
        for a in excel_alerts:
            grav = a.get("gravite_regle", "moyenne")
            typ  = a.get("type_risque_regle", "Autre")
            kw   = len(a.get("keywords", []))
            a["criticality_score"] = compute_criticality_score(grav, typ, kw, True)
            a["criticality_level"] = criticality_level(a["criticality_score"])

        all_alerts = pdf_alerts + excel_alerts
        all_alerts.sort(
            key=lambda a: a.get("criticality_score") or 0,
            reverse=True
        )

        # Enrichissement LLM
        enriched_alerts = enrich_alerts(all_alerts)

        # ------------------------------------------------------------------
        # Enrichissement RiskOpportunityId sur chaque alerte Agent4
        # Principe : matcher le process_name de l'alerte contre les risques
        # QALITAS du contexte -> recuperer le GUID du premier risque du meme
        # processus -> passer source="11" au lieu de source="4" (Sans source).
        # ------------------------------------------------------------------
        try:
            _process_guid_map = {}
            for r in context.get("risques", []):
                proc_raw = str(r.get("Processus") or r.get("processus") or "").strip().lower()
                guid     = r.get("_raw", {}).get("Id") or r.get("_raw", {}).get("RiskOpportunityId") or ""
                if proc_raw and guid and proc_raw not in _process_guid_map:
                    _process_guid_map[proc_raw] = guid

            def _norm_proc(s: str) -> str:
                import unicodedata
                s = s.lower().strip()
                s = unicodedata.normalize("NFKD", s)
                s = "".join(c for c in s if not unicodedata.category(c).startswith("M"))
                return s

            _norm_map = {_norm_proc(k): v for k, v in _process_guid_map.items()}

            enriched_count = 0
            for alert in enriched_alerts:
                if alert.get("risk_opportunity_id"):
                    continue
                proc = str(alert.get("process_name") or alert.get("processus") or "")
                proc_norm = _norm_proc(proc)
                # Recherche exacte puis par inclusion
                guid_found = _norm_map.get(proc_norm, "")
                if not guid_found:
                    for k, v in _norm_map.items():
                        if k in proc_norm or proc_norm in k:
                            guid_found = v
                            break
                if guid_found:
                    alert["risk_opportunity_id"] = guid_found
                    enriched_count += 1

            logger.info(
                "[Agent4] Enrichissement RiskOpportunityId : %d/%d alertes liees a un risque QALITAS",
                enriched_count, len(enriched_alerts)
            )
        except Exception as exc:
            logger.warning("[Agent4] Enrichissement RiskOpportunityId echoue : %s", exc)

        # Injection QALITAS (actions correctives Agent4)
        prev_qstats = dict(state.get("qalitas_stats") or {})
        if enriched_alerts:
            try:
                qalitas_url  = os.environ.get("QALITAS_BASE_URL", DEFAULT_BASE_URL)
                qalitas_user = os.environ.get("QALITAS_USERNAME", "MOHAMED.N")
                qalitas_pass = os.environ.get("QALITAS_PASSWORD", "MOHAMED.N")

                client = QalitasClient(
                    base_url=qalitas_url,
                    username=qalitas_user,
                    password=qalitas_pass,
                )
                if not dry_run:
                    client.login()

                writer   = QalitasWriter(client=client, dry_run=dry_run)
                a4_stats = inject_agent4_results(enriched_alerts, writer)
                prev_qstats["agent4"] = a4_stats

            except Exception as exc:
                logger.warning("[Agent4] Injection QALITAS echouee : %s", exc)
                errors.append(f"Agent4_injection: {exc}")

        # -----------------------------------------------------------
        # DECLENCHEMENT DE REEVALUATION (CDC Agent4, Etape 3)
        # -----------------------------------------------------------
        # Criteres de declenchement :
        # 1. Au moins 1 alerte de niveau ALERTE sur un processus
        # 2. Signal de type "nc_majeure" ou "action_inefficace"
        # 3. Protection anti-boucle : iteration < max_iterations
        # -----------------------------------------------------------
        reeval_required = False
        reeval_trigger  = ""
        reeval_risques: list = []

        if iteration < max_iter:
            alertes_critiques = [
                a for a in enriched_alerts
                if a.get("criticality_level") == "ALERTE"
            ]
            # Chercher les signaux de reevaluation
            for alerte in alertes_critiques:
                type_signal = str(alerte.get("signal_type",
                                             alerte.get("type_risque_final",
                                             alerte.get("type_risque_regle", "")))).lower()
                if any(t in type_signal for t in ["nc", "non_conformite", "action_inefficace",
                                                   "derive_kpi", "incident"]):
                    reeval_required = True
                    reeval_trigger  = type_signal.upper()
                    # Identifier les risques concernes par processus
                    processus_alerte = alerte.get("process_name", "")
                    evaluated_ro     = state.get("evaluated_ro", [])
                    codes_lies = [
                        str(r.get("code", r.get("Code", "")))
                        for r in evaluated_ro
                        if str(r.get("processus",
                                     r.get("Processus",
                                     r.get("process_name", "")))).strip()
                        == processus_alerte.strip()
                    ]
                    reeval_risques.extend(codes_lies)
                    break   # un seul declencheur suffit

            if reeval_required:
                logger.warning(
                    "[Agent4] Reevaluation declenchee ! Motif: %s | "
                    "%d risques cibles | Iteration suivante: %d/%d",
                    reeval_trigger, len(set(reeval_risques)),
                    iteration + 1, max_iter
                )
        else:
            logger.info(
                "[Agent4] Limite d'iterations atteinte (%d/%d). "
                "Reevaluation automatique desactivee.",
                iteration, max_iter
            )

        # -----------------------------------------------------------
        # EVALUATION D'EFFICACITE DES TRAITEMENTS (CDC Agent4, Etape 4)
        # -----------------------------------------------------------
        # Croiser le plan de traitement Agent3 avec les alertes Agent4
        # pour detecter les actions inefficaces et declencher une reevaluation
        # -----------------------------------------------------------
        treatment_plan = state.get("treatment_plan", [])
        evaluated_ro   = state.get("evaluated_ro", [])

        if treatment_plan:
            try:
                from agents.agent4_efficacy import evaluate_plan_efficacy
                efficacy = evaluate_plan_efficacy(
                    treatment_plan=treatment_plan,
                    monitoring_alerts=enriched_alerts,
                    evaluated_ro=evaluated_ro,
                )
                prev_qstats["agent4_efficacy"] = efficacy.get("summary", {})

                # Fusionner declencheurs de reevaluation
                if (
                    efficacy["reevaluation"]["required"]
                    and iteration < max_iter
                ):
                    reeval_required = True
                    if not reeval_trigger or reeval_trigger == "":
                        reeval_trigger = "ACTION_INEFFICACE"
                    codes_inefficaces = efficacy["reevaluation"].get("codes", [])
                    reeval_risques.extend(codes_inefficaces)
                    logger.warning(
                        "[Agent4] Efficacite traitements : %d inefficaces "
                        "-> reevaluation declenchee (codes: %s)",
                        len(efficacy.get("inefficaces", [])),
                        codes_inefficaces[:5],
                    )
            except Exception as exc_eff:
                logger.warning(
                    "[Agent4] Evaluation efficacite echouee : %s", exc_eff
                )
                errors.append(f"Agent4_efficacy: {exc_eff}")

        logger.info(
            "[Agent4] %d alertes | ALERTE=%d | SURVEILLANCE=%d | STABLE=%d | reeval=%s",
            len(enriched_alerts),
            sum(1 for a in enriched_alerts if a.get("criticality_level") == "ALERTE"),
            sum(1 for a in enriched_alerts if a.get("criticality_level") == "SURVEILLANCE"),
            sum(1 for a in enriched_alerts if a.get("criticality_level") == "STABLE"),
            reeval_required,
        )

        return {
            "monitoring_alerts": enriched_alerts,
            "reeval_required":   reeval_required,
            "reeval_trigger":    reeval_trigger,
            "reeval_risques":    list(set(reeval_risques)),
            "iteration":         iteration + 1,
            "qalitas_stats":     prev_qstats,
            "errors":            errors,
        }

    except Exception as exc:
        logger.error("[Agent4] Erreur : %s", exc)
        errors.append(f"Agent4: {exc}")
        return {
            "monitoring_alerts": [],
            "reeval_required":   False,
            "errors":            errors,
        }


# =============================================================================
# ROUTEUR CONDITIONNEL (Agent4 -> Agent2 ou END)
# =============================================================================

def should_reevaluate(
    state: PipelineState,
) -> Literal["agent2_evaluate", "__end__"]:
    """
    Fonction de routage conditionnel apres Agent4.

    Retourne :
        "agent2_evaluate"  si une reevaluation est requise
                           ET la limite d'iterations n'est pas atteinte
        "__end__"          sinon (fin du pipeline)
    """
    reeval  = state.get("reeval_required", False)
    iter_   = int(state.get("iteration", 1))
    max_it  = int(state.get("max_iterations", 3))

    if reeval and iter_ <= max_it:
        logger.info(
            "[Routeur] Reevaluation declenchee -> Agent2 (iteration %d/%d, motif: %s)",
            iter_, max_it, state.get("reeval_trigger", "?")
        )
        return "agent2_evaluate"

    logger.info("[Routeur] Pipeline termine -> END")
    return "__end__"


# =============================================================================
# CONSTRUCTION DU GRAPHE LANGGRAPH
# =============================================================================

def build_graph():
    """
    Construit et compile le graphe LangGraph de la plateforme multi-agents.

    Retourne le graphe compile, pret a etre execute avec .invoke(state).

    Necessite : pip install langgraph
    """
    from langgraph.graph import StateGraph, END

    graph = StateGraph(PipelineState)

    # Enregistrement des noeuds
    graph.add_node("agent1_identify",  agent1_node)
    graph.add_node("agent2_evaluate",  agent2_node)
    graph.add_node("agent3_treat",     agent3_node)
    graph.add_node("agent4_monitor",   agent4_node)

    # Flux principal
    graph.set_entry_point("agent1_identify")
    graph.add_edge("agent1_identify", "agent2_evaluate")
    graph.add_edge("agent2_evaluate", "agent3_treat")
    graph.add_edge("agent3_treat",    "agent4_monitor")

    # Flux conditionnel : Agent4 -> Agent2 (reevaluation) ou END
    graph.add_conditional_edges(
        "agent4_monitor",
        should_reevaluate,
        {
            "agent2_evaluate": "agent2_evaluate",
            "__end__":         END,
        },
    )

    return graph.compile()


# =============================================================================
# GRAPHE SANS LANGGRAPH (fallback sequentiel si non installe)
# =============================================================================

class SequentialFallbackGraph:
    """
    Execute les 4 agents en sequence sans LangGraph.
    Utilise comme fallback si langgraph n'est pas installe.

    Meme interface que le graphe compile : .invoke(state)
    """

    def invoke(self, state: dict, config: dict = None) -> dict:
        logger.warning(
            "LangGraph non disponible. Execution sequentielle (fallback)."
        )
        # Sequence : 1 -> 2 -> 3 -> 4, avec boucle de reevaluation
        state = {**state}   # copie pour ne pas modifier l'original
        state.setdefault("iteration",      1)
        state.setdefault("max_iterations", 3)
        state.setdefault("errors",         [])
        state.setdefault("reeval_required", False)

        state.update(agent1_node(state))
        state.update(agent2_node(state))
        state.update(agent3_node(state))
        state.update(agent4_node(state))

        # Boucle de reevaluation
        while (
            state.get("reeval_required")
            and state.get("iteration", 1) <= state.get("max_iterations", 3)
        ):
            logger.info(
                "[Fallback] Reevaluation iteration %d/%d...",
                state.get("iteration"), state.get("max_iterations")
            )
            state.update(agent2_node(state))
            state.update(agent3_node(state))
            state.update(agent4_node(state))

        return state


def get_graph():
    """
    Retourne le graphe LangGraph compile si disponible,
    sinon le fallback sequentiel.
    """
    try:
        return build_graph()
    except ImportError:
        logger.warning(
            "LangGraph non installe. "
            "Utilisez : pip install langgraph langchain langchain-core"
        )
        return SequentialFallbackGraph()
