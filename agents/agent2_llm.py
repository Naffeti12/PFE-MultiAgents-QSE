"""
Agent IA 2 - Couche LLM : Justification et Enrichissement des Evaluations.

Role :
  - Enrichir les evaluations regle-metier avec une analyse LLM qualitative
  - Justifier les scores calcules (pourquoi ce niveau de risque ?)
  - Anticiper l'evolution du risque a 6-12 mois
  - Formuler des recommandations actionnables avec delais
  - Evaluer l'acceptabilite du risque residuel
  - Enrichir les opportunites avec des axes de capitalisation

Filtrage :
  - LLM appele uniquement pour les risques critiques / eleves
  - Risques mineurs ou acceptables -> enrichissement regle (fallback rapide)
  - Seuil configurable via LLM_GRAVITY_FILTER

Architecture :
  - Execution sequentielle (Ollama local, pas de parallelisme)
  - Timeout par appel : 90s
  - Fallback automatique si LLM indisponible ou parsing impossible
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

try:
    from langchain_ollama import ChatOllama
    from langchain_core.prompts import ChatPromptTemplate
    _LLM_AVAILABLE = True
except ImportError:
    # langchain-ollama non installe : le module charge quand meme,
    # toutes les fonctions utilisent le fallback regle-metier.
    _LLM_AVAILABLE = False

    class ChatOllama:  # type: ignore
        def __init__(self, **kwargs):
            raise RuntimeError(
                "langchain-ollama non disponible. "
                "Installez-le avec : pip install langchain-ollama"
            )

    class ChatPromptTemplate:  # type: ignore
        @staticmethod
        def from_template(template):
            return None

logger = logging.getLogger("agent2.llm")


# =============================================================================
# CONFIGURATION LLM
# =============================================================================

LLM_CONFIG = {
    "model": "llama3.2",
    "temperature": 0.1,
    "num_predict": 1024,
    "format": "json",
}

# Niveaux de risque residuel pour lesquels on appelle le LLM
LLM_GRAVITY_FILTER = {"critique"}

# Niveaux d'opportunite pour lesquels on appelle le LLM
LLM_OPPORTUNITY_FILTER = {"fort", "moyen"}


# =============================================================================
# CONNEXION LLM
# =============================================================================

def _test_llm_connection() -> bool:
    """Teste la disponibilite du modele LLM local."""
    try:
        llm = ChatOllama(**LLM_CONFIG)
        prompt = ChatPromptTemplate.from_template("Reponds uniquement: ok")
        chain = prompt | llm
        result = chain.invoke({})
        return bool(result.content.strip())
    except Exception as e:
        logger.warning("LLM indisponible : %s", e)
        return False


# =============================================================================
# NETTOYAGE JSON
# =============================================================================

def clean_json(raw: str) -> Optional[Dict]:
    """
    Extrait et parse un objet JSON depuis la reponse brute du LLM.
    Gere les blocs markdown, prefixes textuels, et troncatures.
    """
    if not raw:
        return None

    # Supprimer les blocs markdown ```json ... ```
    md_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if md_match:
        candidate = md_match.group(1).strip()
    else:
        candidate = raw.strip()

    # Trouver le premier { et le dernier } pour isoler le JSON
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1:
        return None

    candidate = candidate[start:end + 1]

    # Tenter le parsing direct (sans modifier les apostrophes)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Tenter de reparer les virgules manquantes avant les guillemets
    candidate = re.sub(r'"\s*\n\s*"', '",\n"', candidate)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


# =============================================================================
# FALLBACK SANS LLM
# =============================================================================

def _fallback_enrich_risk(risk: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrichissement regle-metier rapide pour les risques non soumis au LLM.
    """
    niveau = risk.get("niveau_residuel", "mineur")
    statut = risk.get("statut_decisionnel", "A surveiller")
    strategie = risk.get("strategie_recommandee", "A definir")
    efficacite = risk.get("efficacite_maitrise_pct", 0)
    nc_count = risk.get("nc_count_processus", 0)
    score_r = risk.get("score_residuel", 0)
    score_b = risk.get("score_brut", 0)
    delta = risk.get("delta_brut_residuel", 0)

    # Justification automatique
    justification = (
        f"Niveau residuel {niveau.upper()} confirme par la regle metier. "
        f"Score brut {score_b} reduit a {score_r} (delta {delta}). "
        f"Efficacite de maitrise : {efficacite}%. "
        f"NC recentes sur le processus : {nc_count}."
    )

    # Impact prospectif selon le niveau
    impact_map = {
        "critique": (
            "Sans action corrective, ce risque est susceptible de se materialiser "
            "dans les 3 a 6 mois, avec des consequences graves sur la qualite "
            "et la conformite reglementaire."
        ),
        "eleve": (
            "Le risque residuel reste significatif. Sans renforcement des controles, "
            "une derive est probable dans les 6 a 12 mois."
        ),
        "moyen": (
            "Le risque est sous surveillance. Les mesures actuelles contiennent "
            "le risque mais une attention reguliere est requise."
        ),
        "mineur": (
            "Le risque est acceptable dans les conditions actuelles. "
            "Surveillance periodique suffisante."
        ),
    }
    impact_futur = impact_map.get(niveau, "Impact a evaluer selon contexte.")

    # --- Plan d'action concret genere par regle metier ---
    def _c(v):
        s = str(v).strip() if v is not None else ""
        return "" if s.lower() in ("none", "nan") else s

    causes    = _c(risk.get("causes") or risk.get("Causes"))
    effets    = _c(risk.get("effets") or risk.get("Effets Negatifs"))
    processus = _c(risk.get("processus"))
    risque_lb = risk.get("risque", "").strip()

    # Verbe d'action selon niveau et strategie
    verbe_map = {
        "critique": "Mettre en place immediatement",
        "eleve":    "Planifier et renforcer",
        "moyen":    "Surveiller et mettre en oeuvre",
        "mineur":   "Maintenir la surveillance de",
    }
    echeance_map = {
        "critique": "15 jours",
        "eleve":    "30 jours",
        "moyen":    "3 mois",
        "mineur":   "6 mois",
    }
    verbe     = verbe_map.get(niveau, "Traiter")
    echeance  = echeance_map.get(niveau, "3 mois")

    # Construction de la recommandation actionnable
    if causes and effets:
        recommandation = (
            f"{verbe} des mesures correctives pour eliminer les causes ({causes[:150]}) "
            f"et prevenir les effets ({effets[:150]}) sur le processus {processus}. "
            f"Echeance : {echeance}. Responsable : Pilote du processus {processus}."
        )
    elif causes:
        recommandation = (
            f"{verbe} des actions pour traiter les causes identifiees : {causes[:200]}. "
            f"Processus : {processus}. Echeance : {echeance}."
        )
    elif effets:
        recommandation = (
            f"{verbe} des dispositions preventives pour eviter : {effets[:200]}. "
            f"Processus : {processus}. Echeance : {echeance}."
        )
    else:
        recommandation = (
            f"{verbe} un plan de traitement pour le risque '{risque_lb[:100]}' "
            f"({strategie}) sur le processus {processus}. "
            f"Echeance : {echeance}. Statut : {statut}."
        )
    if nc_count >= 3:
        recommandation += (
            f" Traiter en parallele les {nc_count} non-conformites recentes "
            f"liees a ce processus."
        )

    return {
        **risk,
        "justification_evaluation": justification,
        "analyse_causes_llm": causes or "Non documente dans la cartographie.",
        "impact_prospectif": impact_futur,
        "recommandation_llm": recommandation,
        "acceptabilite_residuel": (
            "Acceptable" if niveau in ("mineur", "moyen")
            else "Non acceptable - action requise"
        ),
        "reevaluation_requise": niveau in ("critique", "eleve") or nc_count >= 3,
        "llm_used": False,
    }


def _fallback_enrich_opportunity(opp: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrichissement regle-metier pour les opportunites non soumises au LLM.
    """
    niveau = opp.get("niveau_opportunite", "faible")
    reduction = opp.get("reduction_rpn_pct", 0)
    strategie = opp.get("strategie_opportunite", "Anticiper")

    return {
        **opp,
        "axe_capitalisation": (
            f"La reduction de {reduction}% du RPN constitue une bonne pratique "
            f"a formaliser et a diffuser. {strategie}."
        ),
        "plan_action_opp": (
            f"Documenter la methode ayant permis cette reduction, "
            f"partager avec les processus a niveau de risque similaire."
        ),
        "benefice_quantifie": f"Reduction du RPN de {reduction}% deja observee.",
        "llm_used": False,
    }


# =============================================================================
# ENRICHISSEMENT LLM - RISQUE
# =============================================================================

def _needs_llm_risk(risk: Dict[str, Any]) -> bool:
    """Determine si ce risque necessite un enrichissement LLM."""
    niveau = risk.get("niveau_residuel", "mineur")
    return niveau in LLM_GRAVITY_FILTER


def _enrich_risk_llm(risk: Dict[str, Any], llm: Any) -> Dict[str, Any]:
    """
    Appel LLM pour enrichir l'evaluation d'un risque.
    Retourne le risque enrichi (ou le fallback en cas d'erreur).
    """
    payload = {
        "processus": risk.get("processus", ""),
        "risque": risk.get("risque", "")[:200],
        "type": risk.get("type", ""),
        "causes": risk.get("causes", "")[:200],
        "effets": risk.get("effets", "")[:200],
        "mode_evaluation": risk.get("mode_evaluation", ""),
        "score_brut": risk.get("score_brut", 0),
        "niveau_brut": risk.get("niveau_brut", ""),
        "score_residuel": risk.get("score_residuel", 0),
        "niveau_residuel": risk.get("niveau_residuel", ""),
        "indice_maitrise": risk.get("indice_maitrise", 0),
        "niveau_maitrise": risk.get("niveau_maitrise", ""),
        "efficacite_maitrise_pct": risk.get("efficacite_maitrise_pct", 0),
        "nc_count": risk.get("nc_count_processus", 0),
        "decision_initiale": risk.get("decision_initiale", "")[:150],
        "statut_decisionnel": risk.get("statut_decisionnel", ""),
    }

    prompt = ChatPromptTemplate.from_template(
        """Tu es un expert QSE certifie ISO 9001/14001/45001. Analyse ce risque industriel evalue.

Processus: {processus}
Risque: {risque}
Type: {type}
Causes identifiees: {causes}
Effets negatifs: {effets}
Mode evaluation applique: {mode_evaluation}
Score brut (F*G): {score_brut} [{niveau_brut}]
Score residuel: {score_residuel} [{niveau_residuel}]
Indice de maitrise: {indice_maitrise}/3 [{niveau_maitrise}]
Efficacite maitrise: {efficacite_maitrise_pct}%
NC recentes sur le processus: {nc_count}
Decision initiale: {decision_initiale}
Statut decisionnel: {statut_decisionnel}

Retourne un JSON avec exactement ces champs:
- justification_evaluation: explication en 2-3 phrases du niveau de risque calcule et pourquoi ce score est justifie ou non
- analyse_causes_llm: analyse des causes racines probables en 1-2 phrases
- impact_prospectif: consequence probable a 6-12 mois si aucune action n'est prise
- recommandation_llm: action concrete avec delai precis et responsable suggere
- acceptabilite_residuel: le risque residuel est-il acceptable compte tenu du contexte
- reevaluation_requise: true ou false selon si une reevaluation est necessaire avant 6 mois"""
    )

    try:
        chain = prompt | llm
        result = chain.invoke(payload)
        raw = result.content.strip()

        # Parsing direct (format=json force le JSON natif)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = clean_json(raw)

        if not parsed or not isinstance(parsed, dict):
            logger.warning("Parsing LLM echoue pour risque: %s", payload["risque"][:60])
            return _fallback_enrich_risk(risk)

        return {
            **risk,
            "justification_evaluation": str(
                parsed.get("justification_evaluation", "")
            ),
            "analyse_causes_llm": str(
                parsed.get("analyse_causes_llm", risk.get("causes", ""))
            ),
            "impact_prospectif": str(parsed.get("impact_prospectif", "")),
            "recommandation_llm": str(parsed.get("recommandation_llm", "")),
            "acceptabilite_residuel": str(parsed.get("acceptabilite_residuel", "")),
            "reevaluation_requise": bool(parsed.get("reevaluation_requise", False)),
            "llm_used": True,
        }

    except Exception as e:
        logger.warning(
            "Erreur LLM ligne risque '%s': %s",
            payload["risque"][:60], str(e)
        )
        return _fallback_enrich_risk(risk)


# =============================================================================
# ENRICHISSEMENT LLM - OPPORTUNITE
# =============================================================================

def _needs_llm_opportunity(opp: Dict[str, Any]) -> bool:
    """Determine si cette opportunite necessite un enrichissement LLM."""
    niveau = opp.get("niveau_opportunite", "faible")
    return niveau in LLM_OPPORTUNITY_FILTER


def _enrich_opportunity_llm(opp: Dict[str, Any], llm: Any) -> Dict[str, Any]:
    """
    Appel LLM pour enrichir une opportunite identifiee.
    """
    payload = {
        "processus": opp.get("processus", ""),
        "risque_source": opp.get("risque_source", "")[:200],
        "reduction_rpn_pct": opp.get("reduction_rpn_pct", 0),
        "valeur_attendue": opp.get("valeur_attendue", ""),
        "faisabilite": opp.get("faisabilite", ""),
        "alignement_strategique": opp.get("alignement_strategique", ""),
        "indice_priorite_opp": opp.get("indice_priorite_opp", 0),
        "niveau_opportunite": opp.get("niveau_opportunite", ""),
        "strategie_opportunite": opp.get("strategie_opportunite", ""),
        "benefice_attendu": opp.get("benefice_attendu", "")[:200],
    }

    prompt = ChatPromptTemplate.from_template(
        """Tu es un expert QSE. Une opportunite de capitalisation a ete identifiee.

Processus: {processus}
Risque source: {risque_source}
Reduction RPN obtenue: {reduction_rpn_pct}%
Valeur attendue: {valeur_attendue}
Faisabilite: {faisabilite}
Alignement strategique: {alignement_strategique}
Indice de priorite opportunite: {indice_priorite_opp}/3
Niveau: {niveau_opportunite}
Strategie recommandee: {strategie_opportunite}
Benefice attendu: {benefice_attendu}

Retourne un JSON avec exactement ces champs:
- axe_capitalisation: comment formaliser et diffuser cette bonne pratique en 1-2 phrases
- plan_action_opp: plan d'action concret en 2-3 etapes avec responsables et delais
- benefice_quantifie: estimation chiffree ou qualitative du gain attendu
- risques_associes: risques eventuels lies a l'exploitation de cette opportunite"""
    )

    try:
        chain = prompt | llm
        result = chain.invoke(payload)
        raw = result.content.strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = clean_json(raw)

        if not parsed or not isinstance(parsed, dict):
            return _fallback_enrich_opportunity(opp)

        return {
            **opp,
            "axe_capitalisation": str(parsed.get("axe_capitalisation", "")),
            "plan_action_opp": str(parsed.get("plan_action_opp", "")),
            "benefice_quantifie": str(parsed.get("benefice_quantifie", "")),
            "risques_associes": str(parsed.get("risques_associes", "")),
            "llm_used": True,
        }

    except Exception as e:
        logger.warning("Erreur LLM opportunite '%s': %s", payload["processus"], str(e))
        return _fallback_enrich_opportunity(opp)


# =============================================================================
# ENRICHISSEMENT PRINCIPAL
# =============================================================================

def enrich_evaluations(
    risques: List[Dict[str, Any]],
    opportunites: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Enrichit les risques et opportunites evalues via LLM.

    Arguments :
        risques      -- liste des risques evalues par agent2_evaluation
        opportunites -- liste des opportunites identifiees

    Retourne :
        dict avec risques_enrichis, opportunites_enrichies, stats_llm
    """
    total_risques = len(risques)
    total_opps = len(opportunites)

    # Test de connexion LLM
    logger.info("Test de connexion au LLM...")
    llm_available = _test_llm_connection()

    if llm_available:
        logger.info("LLM disponible. Modele : %s", LLM_CONFIG["model"])
        llm = ChatOllama(**LLM_CONFIG)
    else:
        logger.warning(
            "LLM indisponible. Enrichissement regle-metier uniquement."
        )
        llm = None

    # -------------------------------------------------------------------------
    # Enrichissement des risques
    # -------------------------------------------------------------------------
    risques_enrichis = []
    llm_risk_count = 0

    for i, risk in enumerate(risques):
        risque_label = risk.get("risque", "")[:60]

        if llm_available and _needs_llm_risk(risk):
            logger.info(
                "[%d/%d] LLM risque : %s [%s]",
                i + 1, total_risques,
                risque_label,
                risk.get("niveau_residuel", "")
            )
            enriched = _enrich_risk_llm(risk, llm)
            if enriched.get("llm_used"):
                llm_risk_count += 1
        else:
            logger.debug(
                "[%d/%d] Fallback risque : %s [%s]",
                i + 1, total_risques,
                risque_label,
                risk.get("niveau_residuel", "")
            )
            enriched = _fallback_enrich_risk(risk)

        risques_enrichis.append(enriched)

    # -------------------------------------------------------------------------
    # Enrichissement des opportunites
    # -------------------------------------------------------------------------
    opps_enrichies = []
    llm_opp_count = 0

    for i, opp in enumerate(opportunites):
        opp_label = opp.get("risque_source", "")[:60]

        if llm_available and _needs_llm_opportunity(opp):
            logger.info(
                "[%d/%d] LLM opportunite : %s [%s]",
                i + 1, total_opps,
                opp_label,
                opp.get("niveau_opportunite", "")
            )
            enriched_opp = _enrich_opportunity_llm(opp, llm)
            if enriched_opp.get("llm_used"):
                llm_opp_count += 1
        else:
            enriched_opp = _fallback_enrich_opportunity(opp)

        opps_enrichies.append(enriched_opp)

    # -------------------------------------------------------------------------
    # Statistiques d'enrichissement
    # -------------------------------------------------------------------------
    total_llm = llm_risk_count + llm_opp_count
    total_items = total_risques + total_opps
    taux = round(total_llm / total_items * 100, 1) if total_items > 0 else 0

    eligible_risks = sum(
        1 for r in risques if r.get("niveau_residuel") in LLM_GRAVITY_FILTER
    )
    eligible_opps = sum(
        1 for o in opportunites if o.get("niveau_opportunite") in LLM_OPPORTUNITY_FILTER
    )

    stats = {
        "llm_disponible": llm_available,
        "model_utilise": LLM_CONFIG["model"] if llm_available else "N/A",
        "risques_evalues": total_risques,
        "risques_eligibles_llm": eligible_risks,
        "risques_enrichis_llm": llm_risk_count,
        "opportunites_evaluees": total_opps,
        "opportunites_eligibles_llm": eligible_opps,
        "opportunites_enrichies_llm": llm_opp_count,
        "taux_enrichissement_llm": f"{taux}%",
    }

    logger.info(
        "Enrichissement termine : %d/%d risques via LLM, %d/%d opportunites via LLM "
        "(taux global : %s)",
        llm_risk_count, eligible_risks,
        llm_opp_count, eligible_opps,
        stats["taux_enrichissement_llm"]
    )

    return {
        "risques_enrichis": risques_enrichis,
        "opportunites_enrichies": opps_enrichies,
        "stats_llm": stats,
    }
