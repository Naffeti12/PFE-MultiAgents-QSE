import json
import re
import logging
from typing import Any, Dict, List, Optional

try:
    from langchain_ollama import ChatOllama
    from langchain_core.prompts import ChatPromptTemplate
    _LLM_AVAILABLE = True
except ImportError:
    # langchain-ollama non installe : toutes les fonctions utilisent le fallback.
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

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION DU MODELE
# =============================================================================

LLM_CONFIG = {
    "model": "llama3",
    "temperature": 0.1,
    "num_predict": 1024,
    "format": "json",
}

# Instantiation differee : si langchain-ollama est absent, llm=None et
# _test_llm_connection() retournera False (fallback regle-metier).
if _LLM_AVAILABLE:
    try:
        llm = ChatOllama(**LLM_CONFIG)
    except Exception:
        llm = None
else:
    llm = None


# =============================================================================
# PROMPT PRINCIPAL
# =============================================================================

_PROMPT_TEMPLATE = """Tu es un expert QSE. Analyse ce signal QSE et anticipe son evolution.

Processus: {process_name}
Type de signal: {type_risque_regle}
Gravite: {gravite_regle}
Extrait: {snippet}
Contexte: {context_extra}

Retourne un JSON avec ces champs:
- type_risque: type du risque ou opportunite
- gravite: faible, moyenne ou elevee
- resume: description du signal en 1-2 phrases
- impact_futur: consequence a 6-12 mois si rien n'est fait
- recommandation: action concrete avec delai precis
- reevaluation_required: true ou false
- opportunite: opportunite associee ou chaine vide"""

if _LLM_AVAILABLE:
    prompt = ChatPromptTemplate.from_template(_PROMPT_TEMPLATE)
else:
    prompt = None

chain = (prompt | llm) if _LLM_AVAILABLE and prompt is not None and llm is not None else None


# =============================================================================
# FONCTIONS UTILITAIRES
# =============================================================================

def clean_json(text: str) -> str:
    """
    Nettoyage de secours pour JSON mal forme.
    N'est utilise QUE si json.loads(raw) echoue directement.
    NE remplace JAMAIS les apostrophes (casse le texte francais).
    """
    # Tentative 1 : bloc markdown ```json ... ```
    md_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if md_match:
        candidate = md_match.group(1)
    else:
        # Tentative 2 : premier objet JSON brut
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            candidate = match.group(0)
        else:
            return "{}"

    # Nettoyage minimal, sans toucher aux apostrophes
    candidate = re.sub(r"//[^\n]*", "", candidate)   # commentaires JS
    candidate = re.sub(r",\s*}", "}", candidate)      # virgule finale objet
    candidate = re.sub(r",\s*]", "]", candidate)      # virgule finale tableau
    candidate = re.sub(r"\bTrue\b", "true", candidate)
    candidate = re.sub(r"\bFalse\b", "false", candidate)
    candidate = re.sub(r"\bNone\b", "null", candidate)
    return candidate


def safe_str(value: Any, default: str = "") -> str:
    """Convertit une valeur en string propre."""
    if value is None:
        return default
    result = str(value).strip()
    return result if result else default


def normalize_gravity(value: str, default: str = "moyenne") -> str:
    """Normalise la gravite vers : faible | moyenne | elevee."""
    if not value:
        return default

    value = value.strip().lower()

    if any(x in value for x in ["elev", "haute", "critic", "fort"]):
        return "elevee"
    if any(x in value for x in ["moy", "mod", "inter"]):
        return "moyenne"
    if any(x in value for x in ["faib", "bas", "minor", "mineur"]):
        return "faible"

    return default


def infer_process_name(alert: Dict[str, Any]) -> str:
    """Recupere le nom du processus depuis l'alerte."""
    for key in ["process_name", "process", "processus", "source_process"]:
        if key in alert and alert[key]:
            return safe_str(alert[key])
    return "Non precise"


def default_impact(type_risque: str) -> str:
    """Impact futur par defaut si le LLM n'en fournit pas."""
    type_risque = safe_str(type_risque).lower()

    impacts = {
        "technique": (
            "Un maintien de cette situation peut degrader la disponibilite "
            "des equipements et perturber la production."
        ),
        "logistique": (
            "Si la situation persiste, elle peut entrainer des retards "
            "supplementaires, des ruptures ou une desorganisation de la "
            "chaine d'approvisionnement."
        ),
        "rh": (
            "Si le signal se repete, il peut affecter la continuite "
            "d'activite, la charge de travail et la stabilite operationnelle."
        ),
        "performance": (
            "Si cette tendance continue, elle peut degrader la performance "
            "globale du processus et compromettre l'atteinte des objectifs."
        ),
        "client": (
            "Si les reclamations persistent, elles peuvent impacter la "
            "satisfaction client, la fidelisation et l'image de marque."
        ),
        "qualite": (
            "Si les non-conformites se repetent, elles peuvent entrainer "
            "des couts de non-qualite, des retouches et une perte de "
            "confiance client."
        ),
        "conformite": (
            "Un defaut de conformite non traite peut exposer l'entreprise "
            "a des sanctions reglementaires ou a une perte de certification."
        ),
    }

    for key, impact in impacts.items():
        if key in type_risque:
            return impact

    return (
        "Si ce signal se confirme dans le temps, une reevaluation "
        "du risque sera necessaire."
    )


def build_context_extra(alert: Dict[str, Any]) -> str:
    """
    Construit un texte de contexte supplementaire a partir
    des donnees Excel enrichies.
    """
    ctx = alert.get("context_enrichment", {})
    if not ctx or not ctx.get("has_context_data"):
        return "Aucun contexte supplementaire disponible."

    parts = []

    nc_count = ctx.get("nc_count_process", 0)
    if nc_count > 0:
        parts.append(f"{nc_count} non-conformites enregistrees pour ce processus.")

    kpis = ctx.get("related_kpis", [])
    if kpis:
        parts.append(f"KPIs associes : {', '.join(kpis[:3])}.")

    risks = ctx.get("existing_risks", [])
    if risks:
        parts.append(f"Risques existants : {risks[0][:100]}.")

    return " ".join(parts) if parts else "Aucun contexte supplementaire disponible."


# =============================================================================
# VALIDATION DE LA REPONSE LLM
# =============================================================================

REQUIRED_FIELDS = [
    "type_risque", "gravite", "resume",
    "impact_futur", "recommandation", "reevaluation_required"
]


def validate_llm_response(parsed: Dict[str, Any]) -> bool:
    """
    Verifie que la reponse LLM est utilisable.
    Un champ manquant n'est plus un echec total — on accepte si au moins
    'resume' est present et non vide (les autres champs ont des defaults).
    """
    if not parsed:
        return False
    # Le seul champ vraiment critique : le resume (valeur ajoutee principale du LLM)
    if not safe_str(parsed.get("resume")):
        return False
    return True


def complete_llm_response(parsed: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Comble les champs manquants d'une reponse LLM partielle avec les valeurs
    issues des regles metier. Evite le fallback total quand le LLM a fourni
    au moins un resume exploitable.
    """
    defaults = {
        "type_risque":          payload.get("type_risque_regle", "Autre"),
        "gravite":              payload.get("gravite_regle", "moyenne"),
        "impact_futur":         default_impact(payload.get("type_risque_regle", "Autre")),
        "recommandation":       payload.get("recommandation_regle", "Effectuer une analyse complementaire."),
        "reevaluation_required": True,
        "opportunite":          "",
    }
    for field, default_val in defaults.items():
        if field not in parsed or not parsed[field]:
            if field not in parsed:
                logger.debug("Champ LLM manquant '%s' comble par defaut.", field)
            parsed[field] = default_val
    return parsed


# =============================================================================
# ENRICHISSEMENT D'UNE ALERTE
# =============================================================================

def enrich_alert_with_llm(alert: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrichit une alerte avec le LLM.
    Fallback robuste vers les valeurs regles en cas d'echec.
    """
    if chain is None:
        enriched = dict(alert)
        enriched["llm_used"] = False
        enriched["llm_error"] = "langchain-ollama non disponible"
        return enriched

    process_name = infer_process_name(alert)

    signal_type = alert.get("signal_type", "risque")
    evidence_source = alert.get("evidence_source",
                                 f"Dashboard PDF - page {alert.get('page', '?')}")

    payload = {
        "process_name": process_name,
        "evidence_source": evidence_source,
        "signal_type": signal_type,
        "type_risque_regle": safe_str(
            alert.get("type_risque_regle", "Autre"), "Autre"
        ),
        "gravite_regle": normalize_gravity(
            safe_str(alert.get("gravite_regle", "moyenne")), "moyenne"
        ),
        "recommandation_regle": safe_str(
            alert.get("recommandation_regle",
                       "Effectuer une analyse complementaire.")
        ),
        "keywords": ", ".join(alert.get("keywords", []))
        if isinstance(alert.get("keywords"), list)
        else safe_str(alert.get("keywords", "")),
        "snippet": safe_str(
            alert.get("snippet", ""), "Aucun extrait fourni."
        ),
        "context_extra": build_context_extra(alert),
    }

    try:
        response = chain.invoke(payload)
        raw = response.content if hasattr(response, "content") else str(response)

        # Tentative 1 : parsing direct (format="json" produit du JSON valide)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Tentative 2 : nettoyage de secours
            cleaned = clean_json(raw)
            parsed = json.loads(cleaned)

        if not validate_llm_response(parsed):
            logger.warning(
                "Reponse LLM inutilisable pour page %s (resume absent), fallback regles.",
                alert.get("page")
            )
            raise ValueError("Reponse LLM inutilisable : resume absent")

        # Combler les champs optionnels manquants avec les defaults metier
        parsed = complete_llm_response(parsed, payload)

        # Conserver le type defini par les regles pour coherence des statistiques
        # Le LLM enrichit le contenu, pas la taxonomie
        type_final = payload["type_risque_regle"]
        gravite_final = normalize_gravity(
            safe_str(parsed.get("gravite")), payload["gravite_regle"]
        )
        resume_final = safe_str(
            parsed.get("resume"), payload["snippet"]
        )
        impact_futur = safe_str(
            parsed.get("impact_futur"), default_impact(type_final)
        )
        recommandation_final = safe_str(
            parsed.get("recommandation"), payload["recommandation_regle"]
        )

        reevaluation_required = parsed.get("reevaluation_required", True)
        if not isinstance(reevaluation_required, bool):
            reevaluation_required = True

        opportunite = safe_str(parsed.get("opportunite"), "")

        return {
            "process_name": process_name,
            "page": alert.get("page"),
            "pages": alert.get("pages", [alert.get("page")]),
            "evidence_source": evidence_source,
            "signal_type": signal_type,
            "type_risque_final": type_final,
            "gravite_final": gravite_final,
            "resume_final": resume_final,
            "impact_futur": impact_futur,
            "recommandation_final": recommandation_final,
            "reevaluation_required": reevaluation_required,
            "opportunite": opportunite,
            "criticality_score": alert.get("criticality_score"),
            "criticality_level": alert.get("criticality_level"),
            "inter_process_impacts": alert.get("inter_process_impacts", []),
            "context_enrichment": alert.get("context_enrichment", {}),
            "llm_used": True,
        }

    except Exception as e:
        logger.warning(
            "Erreur LLM pour page %s: %s. Fallback regles.",
            alert.get("page"), str(e)
        )
        # Respecter la valeur definie par les regles (ex: False pour opportunites)
        reeval_fallback = alert.get("reevaluation_required_regle", True)
        if not isinstance(reeval_fallback, bool):
            reeval_fallback = True

        return {
            "process_name": process_name,
            "page": alert.get("page"),
            "pages": alert.get("pages", [alert.get("page")]),
            "evidence_source": evidence_source,
            "signal_type": signal_type,
            "type_risque_final": payload["type_risque_regle"],
            "gravite_final": payload["gravite_regle"],
            "resume_final": payload["snippet"],
            "impact_futur": default_impact(payload["type_risque_regle"]),
            "recommandation_final": payload["recommandation_regle"],
            "reevaluation_required": reeval_fallback,
            "opportunite": "",
            "criticality_score": alert.get("criticality_score"),
            "criticality_level": alert.get("criticality_level"),
            "inter_process_impacts": alert.get("inter_process_impacts", []),
            "context_enrichment": alert.get("context_enrichment", {}),
            "llm_used": False,
        }


# =============================================================================
# ENRICHISSEMENT D'UNE LISTE
# =============================================================================

def _needs_llm_enrichment(alert: Dict[str, Any]) -> bool:
    """
    Determine si une alerte necessite un enrichissement LLM.
    Les opportunites faibles et les alertes mineures utilisent
    directement les valeurs regles (gain de temps significatif).
    """
    signal_type = alert.get("signal_type", "")
    gravite = str(alert.get("gravite_regle", "")).lower()
    criticality = alert.get("criticality_level", "")

    # Toujours enrichir : risques eleves/critiques, capitalisations
    if gravite == "elevee" or criticality in ("critique", "significatif"):
        return True
    # Ne pas enrichir : opportunites (deja bien decrites par les regles)
    if signal_type == "opportunite":
        return False
    # Enrichir les risques moyens
    if signal_type in ("risque", "kpi_drift", "risque_accepte_depasse",
                        "residuel_vs_observe"):
        return True
    # Le reste : capitalisation moyenne, etc. -> enrichir
    return True


def _fallback_enrich(alert: Dict[str, Any]) -> Dict[str, Any]:
    """Enrichissement rapide sans LLM (valeurs regles uniquement)."""
    process_name = infer_process_name(alert)
    signal_type = alert.get("signal_type", "risque")
    evidence_source = alert.get("evidence_source",
                                 f"Dashboard PDF - page {alert.get('page', '?')}")
    type_risque = safe_str(alert.get("type_risque_regle", "Autre"), "Autre")
    gravite = normalize_gravity(
        safe_str(alert.get("gravite_regle", "moyenne")), "moyenne"
    )
    reeval = alert.get("reevaluation_required_regle", True)
    if not isinstance(reeval, bool):
        reeval = True

    return {
        "process_name": process_name,
        "page": alert.get("page"),
        "pages": alert.get("pages", [alert.get("page")]),
        "evidence_source": evidence_source,
        "signal_type": signal_type,
        "type_risque_final": type_risque,
        "gravite_final": gravite,
        "resume_final": safe_str(alert.get("snippet", ""), "Aucun extrait."),
        "impact_futur": default_impact(type_risque),
        "recommandation_final": safe_str(
            alert.get("recommandation_regle", "Analyse complementaire requise.")
        ),
        "reevaluation_required": reeval,
        "opportunite": "",
        "criticality_score": alert.get("criticality_score"),
        "criticality_level": alert.get("criticality_level"),
        "inter_process_impacts": alert.get("inter_process_impacts", []),
        "context_enrichment": alert.get("context_enrichment", {}),
        "llm_used": False,
    }


def _test_llm_connection() -> bool:
    """Teste si le LLM est accessible avant de lancer l'enrichissement."""
    if not _LLM_AVAILABLE or llm is None:
        return False
    try:
        test_response = llm.invoke("Reponds uniquement: OK")
        raw = test_response.content if hasattr(test_response, "content") else str(test_response)
        if raw and len(raw.strip()) > 0:
            logger.info("Test LLM reussi: modele %s accessible.", LLM_CONFIG["model"])
            return True
        logger.warning("Test LLM: reponse vide.")
        return False
    except Exception as e:
        logger.error("Test LLM echoue: %s", str(e))
        return False


def enrich_alerts(alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Enrichit une liste d'alertes via le LLM.
    Optimisations :
    - Seules les alertes critiques/significatives passent par le LLM
    - Les opportunites et alertes mineures gardent les valeurs regles
    - Test de connexion LLM avant de commencer
    - Appels sequentiels (plus fiable qu'en parallele avec Ollama local)
    """
    # Separer alertes a enrichir vs skip
    to_enrich = []
    skipped = []
    for alert in alerts:
        if _needs_llm_enrichment(alert):
            to_enrich.append(alert)
        else:
            skipped.append(alert)

    logger.info(
        "Enrichissement LLM: %d alertes a enrichir, %d en mode regles.",
        len(to_enrich), len(skipped)
    )

    # Enrichir les alertes skippees avec fallback rapide
    results = []
    for alert in skipped:
        enriched = _fallback_enrich(alert)
        results.append({**alert, **enriched})

    # Tester la connexion LLM
    total = len(to_enrich)
    if total > 0:
        llm_available = _test_llm_connection()

        if not llm_available:
            logger.warning(
                "LLM non disponible. Verifiez qu'Ollama est lance "
                "(ollama serve) et que le modele '%s' est installe "
                "(ollama pull %s). Toutes les alertes utilisent le "
                "mode regles.", LLM_CONFIG["model"], LLM_CONFIG["model"]
            )
            print(
                f"\n[AVERTISSEMENT] LLM non disponible!"
                f"\n  -> Verifiez: ollama serve (dans un terminal separe)"
                f"\n  -> Verifiez: ollama pull {LLM_CONFIG['model']}"
                f"\n  -> Toutes les alertes utilisent le mode regles.\n"
            )
            for alert in to_enrich:
                enriched = _fallback_enrich(alert)
                results.append({**alert, **enriched})
        else:
            # Enrichir en sequentiel (plus fiable avec Ollama local)
            llm_success = 0
            for i, alert in enumerate(to_enrich):
                logger.info(
                    "Enrichissement LLM %d/%d (page %s)...",
                    i + 1, total, alert.get("page")
                )
                enriched = enrich_alert_with_llm(alert)
                if enriched.get("llm_used"):
                    llm_success += 1
                results.append({**alert, **enriched})

            logger.info(
                "Enrichissement termine: %d/%d alertes enrichies par LLM.",
                llm_success, total
            )

    # Tri final par criticite (or 0 gere le cas ou la valeur est None)
    results.sort(
        key=lambda a: a.get("criticality_score") or 0,
        reverse=True
    )

    return results


# =============================================================================
# CHARGEMENT / SAUVEGARDE
# =============================================================================

def load_json(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# =============================================================================
# EXECUTION DIRECTE
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    input_file = "alerts_v2.json"
    output_file = "alerts_v3.json"

    alerts = load_json(input_file)
    enriched = enrich_alerts(alerts)
    save_json(enriched, output_file)

    print(f"{len(enriched)} alertes enrichies sauvegardees dans {output_file}")
