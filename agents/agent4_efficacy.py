"""
Agent IA 4 - Module d'Evaluation de l'Efficacite des Traitements.
==================================================================
Conforme au cahier des charges TBR.TIM.IA.S5/2025-2026, Agent IA 4.

Ce module complete l'Agent 4 (Monitoring) en ajoutant :
    - L'evaluation de l'efficacite des actions generees par Agent 3
    - La comparaison RPN_residuel_prevu vs RPN_observe (delta d'ecart)
    - La detection des actions inefficaces (declencheur de reevaluation)
    - La mise a jour du statut des entrees du plan de traitement

Logique metier :
    Pour chaque entree du plan de traitement (Agent3),
    croiser avec les alertes detectees (Agent4) sur le meme processus.
    Si des alertes persistantes sont detectees sur un processus deja traite :
        -> Traitement "inefficace" ou "partiellement efficace"
        -> Reevaluation requise (Agent4 -> Agent2)

    Statuts d'efficacite :
        EFFICACE        : pas d'alerte sur ce processus / RPN observe < RPN prevu
        PARTIELLEMENT   : alerte de faible criticite sur ce processus
        INEFFICACE      : alerte ALERTE sur ce processus apres traitement
        NON_EVALUE      : aucune donnee comparative disponible
"""

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("agent4.efficacy")

# =============================================================================
# CONSTANTES
# =============================================================================

# Seuil d'ecart RPN tolere avant de declarer "INEFFICACE"
# Si RPN_observe > RPN_prevu * (1 + SEUIL_ECART_RPN), le traitement est inefficace
SEUIL_ECART_RPN = 0.20  # 20% de tolerance

# Poids de criticite des alertes pour le calcul de l'efficacite
POIDS_CRITICITE = {
    "ALERTE":      3,
    "SURVEILLANCE": 1,
    "STABLE":      0,
}

# Score d'efficacite : 100 = parfaitement efficace, 0 = completement inefficace
SEUIL_EFFICACITE_MIN   = 30   # en dessous -> INEFFICACE
SEUIL_EFFICACITE_PARTIEL = 70  # entre 30 et 70 -> PARTIELLEMENT


# =============================================================================
# CORRESPONDANCE PROCESSUS
# =============================================================================

def _normalize_processus(name: str) -> str:
    """Normalise un nom de processus pour la comparaison."""
    if not name:
        return ""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", str(name).lower())
    cleaned = "".join(c for c in nfkd if not unicodedata.category(c).startswith("M"))
    return re.sub(r"[^a-z0-9]", "", cleaned).strip()


def _match_processus(proc_plan: str, proc_alerte: str) -> bool:
    """
    Verifie si deux noms de processus correspondent (normalises).
    Tolerance aux variations orthographiques.
    """
    p1 = _normalize_processus(proc_plan)
    p2 = _normalize_processus(proc_alerte)
    if not p1 or not p2:
        return False
    # Correspondance exacte
    if p1 == p2:
        return True
    # Correspondance partielle (sous-chaine)
    if p1 in p2 or p2 in p1:
        return True
    return False


# =============================================================================
# CALCUL DE L'EFFICACITE D'UNE ACTION
# =============================================================================

def evaluate_action_efficacy(
    action: Dict,
    alertes_processus: List[Dict],
    rpn_initial: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Evalue l'efficacite d'une action de traitement en croisant avec
    les alertes detectees par Agent 4 sur le meme processus.

    Parametres :
        action            : dict de l'action (Agent3)
        alertes_processus : alertes Agent4 sur le meme processus
        rpn_initial       : RPN avant traitement (depuis Agent2, si disponible)

    Retourne un dict d'efficacite :
        statut    : EFFICACE / PARTIELLEMENT / INEFFICACE / NON_EVALUE
        score     : entier 0-100
        delta_rpn : ecart RPN observe vs prevu (si disponible)
        alertes   : liste des alertes croisees
        justification : texte explicatif
    """
    nb_alertes     = len(alertes_processus)
    score_pression = sum(
        POIDS_CRITICITE.get(a.get("criticality_level", "STABLE"), 0)
        for a in alertes_processus
    )

    # Score d'efficacite brut : 100 si aucune pression, decremente par les alertes
    score_efficacite = max(0, 100 - (score_pression * 12))

    # Calcul delta RPN si disponible
    rpn_prevu   = action.get("rpn_cible", action.get("rpn_attendu", None))
    rpn_observe = None
    delta_rpn   = None
    delta_pct   = None

    if rpn_initial is not None and rpn_prevu is not None:
        try:
            rpn_prevu   = float(rpn_prevu)
            rpn_initial = float(rpn_initial)
            # Estimation RPN observe a partir de la pression des alertes
            # Plus les alertes sont severes, plus le RPN observe est eleve
            rpn_observe = rpn_prevu + (score_pression * rpn_initial * 0.05)
            delta_rpn   = rpn_observe - rpn_prevu
            delta_pct   = (delta_rpn / rpn_prevu * 100) if rpn_prevu != 0 else 0

            if delta_pct > SEUIL_ECART_RPN * 100:
                score_efficacite = max(0, score_efficacite - 25)
        except (ValueError, TypeError):
            pass

    # Determination du statut
    if nb_alertes == 0:
        statut = "EFFICACE"
        justification = (
            "Aucune alerte detectee sur ce processus. "
            "Le traitement semble avoir produit les effets attendus."
        )
    elif score_efficacite >= SEUIL_EFFICACITE_PARTIEL:
        statut = "PARTIELLEMENT"
        justification = (
            f"{nb_alertes} alerte(s) de faible criticite detectee(s) "
            f"sur ce processus. Le traitement est partiellement efficace "
            f"mais necessite un suivi renforce."
        )
    elif score_efficacite >= SEUIL_EFFICACITE_MIN:
        statut = "PARTIELLEMENT"
        justification = (
            f"{nb_alertes} alerte(s) significative(s) sur ce processus. "
            f"Le traitement produit des effets limites. "
            f"Reevaluation recommandee."
        )
    else:
        statut = "INEFFICACE"
        justification = (
            f"{nb_alertes} alerte(s) critique(s) persistantes sur ce processus "
            f"malgre le traitement. Le traitement est INEFFICACE. "
            f"Reevaluation obligatoire."
        )

    return {
        "action_id":    action.get("id", action.get("titre", "?")),
        "strategie":    action.get("strategie", "?"),
        "processus":    action.get("processus", action.get("perimetre", "?")),
        "statut":       statut,
        "score":        score_efficacite,
        "nb_alertes":   nb_alertes,
        "score_pression": score_pression,
        "rpn_initial":  rpn_initial,
        "rpn_prevu":    rpn_prevu,
        "rpn_observe":  rpn_observe,
        "delta_rpn":    delta_rpn,
        "delta_pct":    delta_pct,
        "alertes_liees": [
            {
                "type":  a.get("type_risque_final", a.get("type_risque_regle", "?")),
                "level": a.get("criticality_level", "?"),
                "score": a.get("criticality_score", 0),
                "process": a.get("process_name", "?"),
            }
            for a in alertes_processus[:5]  # max 5 alertes retournees
        ],
        "justification": justification,
    }


# =============================================================================
# EVALUATION D'EFFICACITE DU PLAN COMPLET (Agent3)
# =============================================================================

def evaluate_plan_efficacy(
    treatment_plan:   List[Dict],
    monitoring_alerts: List[Dict],
    evaluated_ro:     List[Dict] = None,
) -> Dict[str, Any]:
    """
    Evalue l'efficacite globale du plan de traitement Agent3
    en croisant avec les alertes detectees par Agent4.

    Parametres :
        treatment_plan    : plan de traitement genere par Agent3
        monitoring_alerts : alertes detectees par Agent4
        evaluated_ro      : R&O evalues par Agent2 (pour recuperer RPN initial)

    Retourne :
        {
            "evaluations"   : liste des evaluations par action
            "summary"       : synthese (taux efficacite, nb inefficaces, etc.)
            "inefficaces"   : liste des actions declarees inefficaces
            "reevaluation"  : {required: bool, codes: list, justification: str}
        }
    """
    if not treatment_plan:
        return {
            "evaluations": [],
            "summary": {
                "message": "Plan de traitement vide. Aucune evaluation possible."
            },
            "inefficaces": [],
            "reevaluation": {"required": False, "codes": [], "justification": ""},
        }

    # Index des alertes par processus normalise
    alertes_by_proc: Dict[str, List[Dict]] = {}
    for alerte in monitoring_alerts:
        proc = _normalize_processus(
            alerte.get("process_name", alerte.get("processus", ""))
        )
        if proc:
            alertes_by_proc.setdefault(proc, []).append(alerte)

    # Index RPN initial par code (depuis Agent2)
    rpn_by_code: Dict[str, float] = {}
    if evaluated_ro:
        for ro in evaluated_ro:
            code = str(ro.get("code", "")).strip()
            rpn  = ro.get("score_brut", ro.get("score_residuel", None))
            if code and rpn is not None:
                try:
                    rpn_by_code[code] = float(rpn)
                except (ValueError, TypeError):
                    pass

    # Evaluation de chaque entree du plan
    evaluations: List[Dict] = []
    non_evalues  = 0
    efficaces    = 0
    partiels     = 0
    inefficaces  = 0

    for entry in treatment_plan:
        processus   = entry.get("risque_processus", entry.get("perimetre",
                       entry.get("processus", "")))
        code_risque = entry.get("risque_code", "")
        rpn_initial = rpn_by_code.get(code_risque)

        # Alertes sur le meme processus
        proc_norm = _normalize_processus(processus)
        alertes_liees = alertes_by_proc.get(proc_norm, [])

        # Si pas d'alertes directes, chercher par correspondance partielle
        if not alertes_liees:
            for proc_a, alertes in alertes_by_proc.items():
                if _match_processus(proc_norm, proc_a):
                    alertes_liees = alertes
                    break

        # Evaluer chaque action du plan
        actions = entry.get("actions", [])
        if not actions:
            # Evaluation de l'entree globale (sans sous-actions)
            eval_result = evaluate_action_efficacy(
                action={
                    "id":        code_risque or entry.get("risque_intitule", "?"),
                    "strategie": entry.get("strategie", "?"),
                    "processus": processus,
                },
                alertes_processus=alertes_liees,
                rpn_initial=rpn_initial,
            )
            eval_result["code_risque"]      = code_risque
            eval_result["risque_intitule"]  = entry.get("risque_intitule", "")
            evaluations.append(eval_result)

            if eval_result["statut"] == "EFFICACE":      efficaces   += 1
            elif eval_result["statut"] == "PARTIELLEMENT": partiels  += 1
            elif eval_result["statut"] == "INEFFICACE":  inefficaces += 1
            else:                                        non_evalues += 1
        else:
            for action in actions:
                eval_result = evaluate_action_efficacy(
                    action={**action, "processus": processus},
                    alertes_processus=alertes_liees,
                    rpn_initial=rpn_initial,
                )
                eval_result["code_risque"]     = code_risque
                eval_result["risque_intitule"] = entry.get("risque_intitule", "")
                evaluations.append(eval_result)

                if eval_result["statut"] == "EFFICACE":        efficaces   += 1
                elif eval_result["statut"] == "PARTIELLEMENT": partiels    += 1
                elif eval_result["statut"] == "INEFFICACE":    inefficaces += 1
                else:                                          non_evalues += 1

    total = max(len(evaluations), 1)
    taux_efficacite = round(efficaces / total * 100, 1)

    # Construire la liste des actions inefficaces
    actions_inefficaces = [e for e in evaluations if e["statut"] == "INEFFICACE"]

    # Determiner si reevaluation requise
    reevaluation_required = len(actions_inefficaces) > 0
    codes_a_reevaluer = list({
        e["code_risque"] for e in actions_inefficaces
        if e.get("code_risque")
    })
    justif_reeval = (
        f"{len(actions_inefficaces)} action(s) declarees inefficaces "
        f"sur les processus : "
        f"{list({e['processus'] for e in actions_inefficaces})}. "
        f"Reevaluation des risques associes recommandee."
    ) if reevaluation_required else "Plan de traitement efficace. Aucune reevaluation requise."

    logger.info(
        "Evaluation efficacite plan Agent3 : total=%d | "
        "efficace=%d (%.0f%%) | partiel=%d | inefficace=%d | non_evalue=%d",
        total, efficaces, taux_efficacite, partiels, inefficaces, non_evalues
    )

    return {
        "generated_at":  datetime.now().isoformat(),
        "evaluations":   evaluations,
        "summary": {
            "total_actions_evaluees": total,
            "nb_efficaces":           efficaces,
            "nb_partiels":            partiels,
            "nb_inefficaces":         inefficaces,
            "nb_non_evalues":         non_evalues,
            "taux_efficacite_pct":    taux_efficacite,
        },
        "inefficaces":   actions_inefficaces,
        "reevaluation": {
            "required":      reevaluation_required,
            "codes":         codes_a_reevaluer,
            "justification": justif_reeval,
        },
    }


# =============================================================================
# INTEGRATION DANS LE RAPPORT AGENT 4
# =============================================================================

def enrich_agent4_with_efficacy(
    rapport_agent4: Dict,
    treatment_plan: List[Dict],
    evaluated_ro:   List[Dict] = None,
) -> Dict:
    """
    Enrichit le rapport Agent4 avec l'evaluation d'efficacite du plan Agent3.

    Met a jour :
        - rapport_agent4["efficacite_traitements"]  : evaluation complete
        - rapport_agent4["reeval_required"]         : True si actions inefficaces
        - rapport_agent4["reeval_codes"]            : codes a reevaluer
        - rapport_agent4["reeval_trigger"]          : "ACTION_INEFFICACE"

    Retourne le rapport enrichi.
    """
    alerts = rapport_agent4.get("all_alerts", rapport_agent4.get("alertes", []))

    efficacy = evaluate_plan_efficacy(
        treatment_plan=treatment_plan,
        monitoring_alerts=alerts,
        evaluated_ro=evaluated_ro,
    )

    rapport_agent4["efficacite_traitements"] = efficacy

    # Fusionner avec la reevaluation existante
    if efficacy["reevaluation"]["required"]:
        rapport_agent4["reeval_required"] = True
        existing_codes = list(rapport_agent4.get("reeval_codes", []))
        new_codes = efficacy["reevaluation"]["codes"]
        rapport_agent4["reeval_codes"] = list(set(existing_codes + new_codes))

        # Trigger le plus urgent (NC > ACTION_INEFFICACE > KPI)
        existing_trigger = str(rapport_agent4.get("reeval_trigger", ""))
        if existing_trigger not in ("NC", "NON_CONFORMITE"):
            rapport_agent4["reeval_trigger"] = "ACTION_INEFFICACE"

        logger.warning(
            "Efficacite traitements : %d action(s) inefficace(s) -> "
            "reevaluation codes : %s",
            len(efficacy["inefficaces"]),
            rapport_agent4["reeval_codes"],
        )

    return rapport_agent4
