"""
Couche d'ecriture QALITAS QSE
==============================
Injecte les resultats des agents (Agent2, Agent4) vers la plateforme QALITAS
via ses endpoints HTTP natifs (ASP.NET MVC), confirmes par exploration navigateur.

Architecture :
    QalitasWriter           : methodes d'ecriture POST avec gestion CSRF et dry_run
    inject_agent2_results() : injecte reevaluations Agent2 -> cartographie QALITAS
    inject_agent4_results() : cree actions correctives depuis alertes Agent4

Endpoints confirmes par interception reseau (01-02/04/2026, maj 03/05/2026) :
    Risque (creation)        GET   RiskOpportunity/Create?nature=N&source=0&sourceId=&selectedIssues=
                             POST  RiskOpportunity/Create  (workflow 2 etapes : GET init, POST save)
    Evaluation (campagne)    POST  RiskOpportunityEvaluation/Edit
    Appreciation par risque  POST  RiskOpportunityEvaluation/EditRiskAppreciation
                             GET   RiskOpportunityEvaluation/GetRiskAppreciation (lecture prealable)
    Action corrective        POST  Actions/Create  (workflow 2 etapes : GET init, POST save)

Mecanique d'ecriture QALITAS (ASP.NET MVC) :
    - Toutes les soumissions utilisent application/x-www-form-urlencoded
    - Chaque formulaire embarque un jeton CSRF : __RequestVerificationToken
    - Le jeton est extrait du HTML de la page courante avant chaque POST
    - La creation d'action necessite un GET preliminaire (init session provisoire)
      qui retourne un Id UUID et le jeton CSRF a soumettre ensuite

Champs confirmes pour POST /Actions/Create :
    Id, __RequestVerificationToken, Source (="11"), TriggerSourceId,
    Progression, IsEffective, ProcessId, TypesId, CategoryId,
    PriorityId, GravityId, Designation, Description, RootCause,
    IsConfidential, Q, S, E, H, WithAnalysis, WithEfficiency,
    WithEscalation, StartDateP, EndDateP, CostsP, ManDaysP, State

Champs confirmes pour POST /RiskOpportunityEvaluation/EditRiskAppreciation :
    Id (UUID propre de la ligne appreciation, issu de GetRiskAppreciation),
    RiskOpportunityEvaluationId (= EvaluationId, cle etrangere vers la campagne),
    EvaluationId, RiskOpportunityId, RiskOpportunityNature, EvaluationState,
    EvaluationFormula, CRUD (= "0" pour update),
    Parameter1 (F brut), Parameter2 (G brut), Score (F*G),
    DecisionEvalRiskId (UUID decision),
    ParameterPrime1 (F residuel), ParameterPrime2 (G residuel),
    ScorePrime (F'*G'), ResultEvalId, ResultEvalPrimeId,
    DecisionComments

    IMPORTANT : Le champ Id (appreciation row UUID) est obligatoire.
    Sans lui le serveur retourne "false|Impossible de modifier cet enregistrement!"
    meme avec HTTP 200. Ce UUID est obtenu par GET GetRiskAppreciation avant le POST.

Mode dry_run=True (defaut) : simule les appels sans ecrire dans QALITAS.
Passer dry_run=False uniquement apres validation manuelle des payloads.
"""

import hashlib
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from loaders.qalitas_api_client import QalitasClient, DEFAULT_BASE_URL

logger = logging.getLogger("qalitas_api_writer")


# =============================================================================
# CACHE D'INJECTION - ANTI-DOUBLON
# =============================================================================
# Probleme : en mode --real, chaque run recrée les memes actions dans QALITAS.
# Solution : cache local JSON dans output/injection_cache.json
#   - Chaque action injectee est identifiee par un fingerprint SHA1
#   - Fingerprint = hash(titre_normalise + processus + semaine_ISO)
#   - TTL configurable par type d'injection (defaut 7 jours)
#   - Appreciation Agent2 : pas de TTL car c'est un UPDATE (naturellement idempotent)
# =============================================================================

BASE_DIR_WRITER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_FILE      = os.path.join(BASE_DIR_WRITER, "output", "injection_cache.json")
CACHE_TTL_DAYS  = int(os.environ.get("QALITAS_CACHE_TTL_DAYS", "7"))


def _fingerprint(title: str, processus: str = "", extra: str = "") -> str:
    """
    Calcule un fingerprint SHA1 unique pour une action a injecter.

    La semaine ISO est incluse dans le hash : une meme action peut etre
    re-injectee apres CACHE_TTL_DAYS jours (comportement par defaut 7j).
    Modifier QALITAS_CACHE_TTL_DAYS=0 pour desactiver le cache.
    """
    import unicodedata as _ud

    def _norm(s: str) -> str:
        nfkd = _ud.normalize("NFKD", str(s).lower().strip())
        return "".join(c for c in nfkd
                       if not _ud.category(c).startswith("M"))

    # Fenetre de temps : numero de semaine ISO (renouvellement du cache toutes les N semaines)
    week_key = ""
    if CACHE_TTL_DAYS > 0:
        week_num = datetime.now().isocalendar()[1]  # 1..52
        week_key = str(week_num // max(1, CACHE_TTL_DAYS // 7))

    raw = f"{_norm(title)[:80]}|{_norm(processus)[:30]}|{_norm(extra)[:20]}|{week_key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _load_cache() -> dict:
    """Charge le cache depuis le fichier JSON (cree si absent)."""
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    """Sauvegarde le cache dans le fichier JSON."""
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _find_guid_in_cache(
    title: str, processus: str, extra: str, cache: dict, weeks_back: int = 8
) -> str:
    """
    Cherche le qalitas_id dans le cache en essayant les fingerprints
    des N dernieres semaines (le fingerprint inclut le numero de semaine,
    donc un lookup strict echoue si Agent1 et Agent2 ont tourne des semaines differentes).
    Retourne le GUID trouve ou chaine vide.
    """
    import unicodedata as _ud

    def _norm(s: str) -> str:
        nfkd = _ud.normalize("NFKD", str(s).lower().strip())
        return "".join(c for c in nfkd if not _ud.category(c).startswith("M"))

    current_week = datetime.now().isocalendar()[1]
    ttl = max(1, CACHE_TTL_DAYS // 7)

    for delta in range(0, weeks_back + 1):
        wk = current_week - delta
        if wk < 1:
            wk += 52
        week_key = str(wk // ttl)
        raw = f"{_norm(title)[:80]}|{_norm(processus)[:30]}|{_norm(extra)[:20]}|{week_key}"
        fp = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        entry = cache.get(fp)
        if entry and isinstance(entry, dict):
            guid = entry.get("qalitas_id", "")
            if guid:
                return guid
    return ""


def is_already_injected(fingerprint: str, cache: dict = None) -> bool:
    """
    Retourne True si ce fingerprint est deja dans le cache.
    Passer cache= si vous avez deja charge le cache (evite des lectures multiples).
    """
    if CACHE_TTL_DAYS == 0:
        return False  # cache desactive
    if cache is None:
        cache = _load_cache()
    return fingerprint in cache


def mark_as_injected(
    fingerprint: str,
    label: str,
    cache: dict,
    agent: str = "",
    qalitas_id: str = "",
) -> None:
    """
    Marque un fingerprint comme injece dans le cache (en memoire).
    Appeler _save_cache(cache) a la fin du batch pour persister.
    qalitas_id : GUID QALITAS retourne par le serveur apres creation (risk_id).
                 Stocke pour permettre a Agent2 de retrouver l'ID sans re-GET.
    """
    entry = {
        "label":    label[:100],
        "agent":    agent,
        "injected_at": datetime.now().isoformat(),
    }
    if qalitas_id:
        entry["qalitas_id"] = qalitas_id
    cache[fingerprint] = entry


def purge_old_cache_entries(days: int = None) -> int:
    """
    Supprime les entrees du cache plus anciennes que `days` jours.
    Retourne le nombre d'entrees supprimees.
    """
    if days is None:
        days = CACHE_TTL_DAYS * 2  # nettoyer les entrees 2x plus vieilles que le TTL
    cache = _load_cache()
    cutoff = datetime.now() - timedelta(days=days)
    to_delete = [
        k for k, v in cache.items()
        if isinstance(v, dict) and "injected_at" in v
        and datetime.fromisoformat(v["injected_at"]) < cutoff
    ]
    for k in to_delete:
        del cache[k]
    if to_delete:
        _save_cache(cache)
    logger.info("Cache purge : %d entrees supprimees (anterieures a %d jours).",
                len(to_delete), days)
    return len(to_delete)


# =============================================================================
# CONSTANTES - ENDPOINTS CONFIRMES
# =============================================================================

WRITE_ENDPOINTS = {
    # Confirme par interception reseau navigateur (03/05/2026) :
    # Le formulaire modal de creation soumet vers POST /RiskOpportunity/Create,
    # PAS vers /Edit. Le GET init inclut des query params obligatoires pour
    # obtenir le HTML avec CSRF token (sans params -> JSON vide).
    "save_risk":              "RiskOpportunity/Create",

    # Confirme : sauvegarde campagne d'evaluation
    "save_evaluation":        "RiskOpportunityEvaluation/Edit",

    # Confirme par capture navigateur (02/04/2026) : edition appreciation par risque
    "save_appreciation":      "RiskOpportunityEvaluation/EditRiskAppreciation",

    # Lecture prealable pour obtenir le Id de la ligne et les champs de contexte
    "get_appreciations":      "RiskOpportunityEvaluation/GetRiskAppreciation",

    # Confirme : init session + formulaire creation action (GET, etape 1)
    "init_action_create":     "Actions/Create",

    # Confirme : soumission action (POST, etape 2 - meme URL)
    "save_action":            "Actions/Create",
}

# Source = 11 : valeur confirmee par inspection du formulaire QALITAS (test_injection_browser.js)
# Represente "Risques & Opportunites" comme entite source de l'action.
# Utiliser "11" pour TOUS les agents (agent2, agent3, agent4).
# Valeurs erronees precedentes : "12" (RiskEvaluation) et "4" (generique) -> source vide en UI.
ACTION_SOURCE_RISQUE = "11"   # actions liees a un risque (TriggerSourceId = risk GUID valide)
ACTION_SOURCE_AGENT4 = "4"    # actions sans lien QALITAS (alertes monitoring sans GUID risque)
                               # "4" accepte TriggerSourceId="" - affiche "Sans source" mais ne rejette pas

# =============================================================================
# GUIDs QALITAS — Types de risque, Catégories (extraits via API GetEnabledRisks)
# =============================================================================

RISK_TYPE_GUIDS = {
    "organisationnel":   "82cb41e1-7392-4e54-746c-39d755b6345c",
    "socioculturel":     "e0166113-8b7b-dc3b-addd-39d755b6348b",
    "operationnel":      "5c74b18d-7943-e234-4276-39d755b6343d",
    "opérationnel":      "5c74b18d-7943-e234-4276-39d755b6343d",
    "technologique":     "627a4223-127b-f69b-6e2a-39d755b6349b",
    "politique":         "21924254-7436-c4e0-30b3-39d755b6346c",
    "strategique":       "69f9dd13-366a-7214-5fbc-39d755b6342d",
    "stratégique":       "69f9dd13-366a-7214-5fbc-39d755b6342d",
    "economique":        "f9c91ce9-53ed-fdcb-8eec-39d755b6347b",
    "économique":        "f9c91ce9-53ed-fdcb-8eec-39d755b6347b",
    "risque de gestion": "c3452084-abd9-c305-294d-39d755b6344d",
    "gestion":           "c3452084-abd9-c305-294d-39d755b6344d",
    "environnemental":   "93aef6c2-d2c8-07b5-df86-39ebac854ad3",
    "maintenance":       "7d0ffdb2-fa08-7010-1a9d-3a192341b70d",
}

RISK_CATEGORY_GUIDS = {
    "interne":   "a6e529f0-043d-73e6-4e4e-39dda0b13e7d",
    "externe":   "1c2cf170-90d0-8197-af18-39dda0b13e7f",
    "emballage": "d99158db-8df2-f3ca-aec1-3a192341299c",
}

# Niveaux de criticite Agent4 -> priorite action QALITAS (1=haute, 2=moyenne, 3=basse)
CRITICITE_TO_PRIORITY = {
    "ALERTE":       1,
    "SURVEILLANCE": 2,
    "STABLE":       3,
}

# =============================================================================
# GUIDs QALITAS - extraits par inspection DevExtreme (14/04/2026)
# Source : formulaire /Actions/AllActions -> Modification d'une action
# =============================================================================

# --- Processus ---
# Mapping mots-cles normalises (lowercase) -> GUID QALITAS
# Clef = sous-chaine du nom de processus agent (normalisee), valeur = ProcessId QALITAS
PROCESSUS_ID_MAP: dict = {
    "supply chain":           "b72ad851-cd75-88b1-c670-3a016f72bcd2",  # Approvisionnement et gestion des stocks
    "approvisionnement":      "b72ad851-cd75-88b1-c670-3a016f72bcd2",
    "logistique":             "911d09a5-0b71-f8fe-5711-39f5bb61df31",  # 406 Processus Logistique
    "achats":                 "3818ac10-da78-ac47-1a99-39d7558eaeb4",  # 403 Processus Achats
    "production":             "c79e0654-2a1b-91be-82e4-39e1b73ca72a",  # 407 Processus Production
    "maintenance":            "8a5da9ca-0827-c6d2-bee4-39e4f2fcfcdf",  # 409 Processus Maintenance
    "securite":               "8a5da9ca-0827-c6d2-bee4-39e4f2fcfcdf",  # 409 Processus Maintenance
    "ressources humaines":    "22f26915-0935-ac4b-3523-39d7558eaee3",  # 404 Processus RH
    "rh":                     "22f26915-0935-ac4b-3523-39d7558eaee3",
    "methodes":               "454bdfb6-7745-c444-2cc0-39e1e4755000",  # 408 Processus Methodes
    "industrialisation":      "26b2b7ff-7a01-c93a-3893-39d7558eaec3",  # 201 Processus Industrialisation
    "pilotage":               "cc855151-0934-61b5-233e-39d7558eaed3",  # Gestion & amelioration systeme qualite
    "amelioration":           "cc855151-0934-61b5-233e-39d7558eaed3",
    "surveillance":           "cc855151-0934-61b5-233e-39d7558eaed3",
    "commercial":             "a650d65c-5f33-6d1b-34af-39d7558eaea4",  # Relation client
    "client":                 "a650d65c-5f33-6d1b-34af-39d7558eaea4",
    "reclamation":            "2510e4ab-c451-6b43-043e-3a1dce565e91",  # Traitement reclamations clients
    "direction":              "e49feb02-d513-6651-39ad-39fa03692114",  # 402 Processus Direction
    "informatique":           "d25b99da-5390-1d3a-5949-39f30f0552dc",  # 304 Processus Informatique
    "emballage":              "b02a4bbf-4a44-faea-5168-3a018f199682",  # emballage et conditionnement
    "conditionnement":        "b02a4bbf-4a44-faea-5168-3a018f199682",
    "projets":                "38bb7f31-1a03-0fc5-e1fc-3a049e488eed",  # 408 Gestion des projets
}

# --- Gravite ---
# Mapping niveau/criticite agent -> GravityId QALITAS
# Extraits depuis ddlGravityAction : "Tres critique", "Critique", "Peu critique"
GRAVITY_ID_MAP: dict = {
    # Niveaux Agent2 (niveau_residuel)
    "critique": "711c83dc-3938-6f73-f0d3-3a016ba1aeb2",   # Tres critique
    "eleve":    "a10b730f-1139-3f97-4496-3a016ba1afd0",   # Critique
    "moyen":    "a10b730f-1139-3f97-4496-3a016ba1afd0",   # Critique
    "mineur":   "98d1bd25-8b7d-9ce7-ffc6-3a016ba1b03d",   # Peu critique
    # Niveaux Agent4 (criticality_level / gravite_final)
    "elevee":          "a10b730f-1139-3f97-4496-3a016ba1afd0",  # Critique
    "elevé":           "a10b730f-1139-3f97-4496-3a016ba1afd0",
    "elevee":          "a10b730f-1139-3f97-4496-3a016ba1afd0",
    "moyenne":         "98d1bd25-8b7d-9ce7-ffc6-3a016ba1b03d",  # Peu critique
    "faible":          "98d1bd25-8b7d-9ce7-ffc6-3a016ba1b03d",
    "alerte":          "711c83dc-3938-6f73-f0d3-3a016ba1aeb2",  # Tres critique
    "significatif":    "a10b730f-1139-3f97-4496-3a016ba1afd0",  # Critique
    "modere":          "98d1bd25-8b7d-9ce7-ffc6-3a016ba1b03d",  # Peu critique
}

# --- Priorite ---
# Mapping niveau/criticite agent -> PriorityId QALITAS
# Extraits depuis ddlPriorityAction : "Tres Urgente", "Urgente", "Peu Urgente"
PRIORITY_ID_MAP: dict = {
    # Niveaux Agent2
    "critique": "388eee05-74ac-2043-6167-3a016ba40f02",   # Tres Urgente
    "eleve":    "4d7ee6be-7f6b-0cd0-e0df-3a016ba40fae",   # Urgente
    "moyen":    "588ae7dc-3240-50b3-ead8-3a016ba4100c",   # Peu Urgente
    "mineur":   "588ae7dc-3240-50b3-ead8-3a016ba4100c",   # Peu Urgente
    # Niveaux Agent4
    "alerte":          "388eee05-74ac-2043-6167-3a016ba40f02",  # Tres Urgente
    "surveillance":    "4d7ee6be-7f6b-0cd0-e0df-3a016ba40fae",  # Urgente
    "stable":          "588ae7dc-3240-50b3-ead8-3a016ba4100c",  # Peu Urgente
    "significatif":    "4d7ee6be-7f6b-0cd0-e0df-3a016ba40fae",  # Urgente
    "elevee":          "4d7ee6be-7f6b-0cd0-e0df-3a016ba40fae",
    "moyenne":         "588ae7dc-3240-50b3-ead8-3a016ba4100c",
    "faible":          "588ae7dc-3240-50b3-ead8-3a016ba4100c",
}

# --- Type d'action ---
# "Preventive" est le type par defaut pour toutes les actions generees par les agents
# GUID extrait depuis ddlTypeAction
TYPE_PREVENTIVE_ID = "c39a73c2-be1f-8b26-403f-39de5112ac78"


def _resolve_processus_id(process_name: str) -> str:
    """
    Resout le GUID QALITAS du processus a partir du nom fourni par l'agent.
    Utilise une correspondance par mots-cles (insensible a la casse et aux accents).
    Retourne "" si aucune correspondance n'est trouvee.
    """
    import unicodedata as _ud
    def _norm(s: str) -> str:
        s = s.lower().strip()
        return "".join(
            c for c in _ud.normalize("NFD", s)
            if _ud.category(c) != "Mn"
        )

    name_n = _norm(process_name)
    for keyword, guid in PROCESSUS_ID_MAP.items():
        if _norm(keyword) in name_n:
            return guid
    return ""


def _resolve_gravity_id(niveau: str) -> str:
    """Retourne le GravityId QALITAS correspondant au niveau de l'agent."""
    return GRAVITY_ID_MAP.get(str(niveau).lower().strip(), "")


def _resolve_priority_id(niveau: str) -> str:
    """Retourne le PriorityId QALITAS correspondant au niveau de l'agent."""
    return PRIORITY_ID_MAP.get(str(niveau).lower().strip(), "")

# Mapping niveau residuel Agent2 -> label decision QALITAS (Designation exacte)
# Ces labels correspondent aux Designations retournees par GetDecisionEvaluationRisk
NIVEAU_TO_DECISION = {
    "critique": "Eliminer la source",
    "eleve":    "Diminuer la probabilite",
    "moyen":    "Diminuer la probabilite",
    "mineur":   "Accepter le risque",
}

# Mapping niveau residuel Agent2 -> Designation ResultEval (risque, Nature=0)
# Ces labels correspondent aux Designations retournees par GetResultEvaluationRiskOpp
NIVEAU_TO_RESULT = {
    "critique": "Critique",
    "eleve":    "Critique",
    "moyen":    "Moyen",
    "mineur":   "Mineur",
}


# =============================================================================
# UTILITAIRES
# =============================================================================

def _extract_csrf_token(html: str) -> str:
    """
    Extrait le jeton anti-CSRF (__RequestVerificationToken) d'une page HTML.
    QALITAS utilise le mecanisme standard ASP.NET MVC pour chaque formulaire.
    """
    match = re.search(
        r'<input[^>]+name=["\']__RequestVerificationToken["\'][^>]+value=["\']([^"\']+)["\']',
        html, re.IGNORECASE
    )
    if match:
        return match.group(1)
    # Tentative alternative (ordre attributs inverse)
    match = re.search(
        r'__RequestVerificationToken[^>]+value=["\']([^"\']+)["\']',
        html, re.IGNORECASE
    )
    return match.group(1) if match else ""


def _extract_hidden_id(html: str, field_name: str = "Id") -> str:
    """
    Extrait la valeur d'un champ hidden par son nom depuis un formulaire HTML.
    Utilise pour recuperer le Id UUID genere par QALITAS lors de l'init action.

    Strategies (de la plus specifique a la plus generale) :
    1. name="Id" value="..."   ou  name='Id' value='...'
    2. value="..." name="Id"   (ordre inverse)
    3. Premier UUID 36 caracteres dans un champ hidden (fallback)
    """
    fn = re.escape(field_name)
    # Passe 1 : name avant value
    match = re.search(
        rf'<input[^>]+name=["\']' + fn + r'["\'][^>]+value=["\']([^"\']+)["\']',
        html, re.IGNORECASE
    )
    if match:
        return match.group(1)
    # Passe 2 : value avant name (certains rendus HTML inversent les attributs)
    match = re.search(
        rf'<input[^>]+value=["\']([^"\']+)["\'][^>]+name=["\']' + fn + r'["\']',
        html, re.IGNORECASE
    )
    if match:
        return match.group(1)
    # Passe 3 : premier UUID dans un champ hidden (fallback robuste)
    match = re.search(
        r'<input[^>]+type=["\']hidden["\'][^>]*value=["\']'
        r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})["\']',
        html, re.IGNORECASE
    )
    return match.group(1) if match else ""


def _strip_accents(text: str) -> str:
    """Supprime les accents pour comparaison robuste (ex: probabilité -> probabilite)."""
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.category(c).startswith("M"))


def _resolve_decision_uuid(decisions: List[Dict], label: str) -> str:
    """
    Recherche l'UUID d'une decision par son label.
    Insensible a la casse ET aux accents (ex: "probabilite" == "probabilité").
    Correspondance exacte puis partielle.

    decisions : liste retournee par QalitasClient.get_decision_eval_risks()
    label     : Designation attendue (ex: "Diminuer la probabilite")

    Retourne "" si aucune correspondance (QALITAS conservera la valeur existante).
    """
    if not decisions or not label:
        return ""
    label_norm = _strip_accents(label).lower().strip()
    # Passe 1 : correspondance exacte (sans accents, sans casse)
    for d in decisions:
        desig_norm = _strip_accents(d.get("Designation", "")).lower().strip()
        if desig_norm == label_norm:
            return d.get("Id", "")
    # Passe 2 : correspondance partielle
    for d in decisions:
        desig_norm = _strip_accents(d.get("Designation", "")).lower().strip()
        if label_norm in desig_norm or desig_norm in label_norm:
            return d.get("Id", "")
    logger.debug("Decision non resolue : '%s' (non trouve parmi %d entrees)", label, len(decisions))
    return ""


def _resolve_result_uuid(results: List[Dict], niveau: str, nature: int = 0) -> str:
    """
    Recherche l'UUID d'un resultat d'evaluation par niveau et nature.

    results : liste retournee par QalitasClient.get_result_eval_risk_opps()
    niveau  : niveau Agent2 (mineur | moyen | eleve | critique)
    nature  : 0 = risque (defaut), 1 = opportunite

    Correspondance Agent2 -> QALITAS (Nature=0, risques) :
        critique -> Critique  (61c1c5b5-...)
        eleve    -> Critique  (61c1c5b5-...)
        moyen    -> Moyen     (bb3f3020-...)
        mineur   -> Mineur    (50dcadd9-...)

    Retourne "" si aucune correspondance.
    """
    if not results or not niveau:
        return ""
    target_label = NIVEAU_TO_RESULT.get(niveau.lower(), niveau)
    target_norm  = target_label.lower().strip()
    # Filtrer par nature puis chercher par Designation
    candidates = [r for r in results if r.get("Nature", 0) == nature]
    for r in candidates:
        if r.get("Designation", "").lower().strip() == target_norm:
            return r.get("Id", "")
    # Correspondance partielle (ex: "Mineur" dans "Mineure")
    for r in candidates:
        desig = r.get("Designation", "").lower().strip()
        if target_norm in desig or desig.startswith(target_norm[:4]):
            return r.get("Id", "")
    logger.debug(
        "Resultat non resolu : niveau='%s' nature=%d (non trouve parmi %d entrees)",
        niveau, nature, len(candidates)
    )
    return ""


# =============================================================================
# WRITER
# =============================================================================

class QalitasWriter:
    """
    Couche d'ecriture vers la plateforme QALITAS QSE.

    Chaque methode :
    1. Construit le payload selon les champs confirmes par exploration
    2. En dry_run=True : journalise sans envoyer (mode par defaut)
    3. En dry_run=False : effectue le POST avec les headers QALITAS attendus

    Usage :
        client = QalitasClient(...)
        client.login()
        writer = QalitasWriter(client, dry_run=True)
        ok, resp = writer.save_risk_appreciation(evaluation_id, risk_id, ...)
        # Verifier le log avant de passer dry_run=False
    """

    def __init__(self, client: QalitasClient, dry_run: bool = True):
        self.client = client
        self.dry_run = dry_run
        self._write_log: List[Dict] = []
        self._human_validation_done = False
        self._human_validation_denied = False

    # -------------------------------------------------------------------------
    # Validation humaine avant ecriture reelle
    # -------------------------------------------------------------------------

    def _ensure_human_validation(
        self,
        operation: str,
        payload_preview: Optional[Dict] = None,
    ) -> bool:
        """
        Demande une validation humaine avant le premier POST reel vers QALITAS.

        La protection est active uniquement en dry_run=False. Elle peut etre
        desactivee pour les executions automatisees avec :
            QALITAS_REQUIRE_HUMAN_VALIDATION=false
        """
        if self.dry_run:
            return True
        if self._human_validation_done:
            return True
        if self._human_validation_denied:
            return False

        require_validation = os.environ.get(
            "QALITAS_REQUIRE_HUMAN_VALIDATION", "true"
        ).lower() not in ("0", "false", "no", "non")
        if not require_validation:
            self._human_validation_done = True
            return True

        preview = ""
        if payload_preview:
            clean_payload = {
                k: v for k, v in payload_preview.items()
                if k != "__RequestVerificationToken"
            }
            preview = json.dumps(clean_payload, ensure_ascii=False, default=str)[:900]

        message = (
            "Validation humaine requise avant injection reelle dans QALITAS.\n\n"
            f"Operation : {operation}\n"
            f"Utilisateur : {getattr(self.client, 'username', '')}\n"
            f"Base URL : {getattr(self.client, 'base_url', '')}\n\n"
            "Cliquer sur OUI pour autoriser cette injection.\n"
            "Cliquer sur NON pour bloquer les ecritures de ce batch."
        )
        if preview:
            message += f"\n\nApercu payload :\n{preview}"

        approved = False
        popup_shown = False
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            popup_shown = True
            approved = messagebox.askyesno(
                "Validation injection QALITAS",
                message,
                parent=root,
            )
            root.destroy()
        except Exception as exc:
            logger.warning(
                "Popup de validation indisponible (%s). Fallback console.",
                exc,
            )

        # Fallback console si le popup n'a pas pu s'afficher
        if not popup_shown:
            try:
                import sys
                # Toujours proposer la saisie console, meme si stdin n'est pas un tty
                print(
                    f"\n{'='*60}\n"
                    f"VALIDATION INJECTION QALITAS REELLE\n"
                    f"Operation : {operation}\n"
                    f"Utilisateur : {getattr(self.client, 'username', '')}\n"
                    f"{'='*60}"
                )
                answer = input("Tapez OUI pour autoriser, NON pour annuler : ")
                approved = answer.strip().upper() in ("OUI", "O", "YES", "Y")
            except Exception as e:
                logger.error(
                    "Validation humaine impossible (popup ET console echoues : %s). "
                    "Forcez la validation avec QALITAS_REQUIRE_HUMAN_VALIDATION=false "
                    "pour les executions non-interactives.", e
                )
                approved = False

        if approved:
            self._human_validation_done = True
            logger.info("Injection QALITAS validee humainement.")
            return True

        # IMPORTANT : ne pas poisonner tout le batch si l'utilisateur annule une seule fois.
        # On logue l'avertissement mais on NE met PAS _human_validation_denied=True ici,
        # ce qui permettrait de re-demander pour la prochaine operation majeure.
        logger.warning(
            "Injection QALITAS refusee pour l'operation '%s'. "
            "Les operations suivantes redemanderont confirmation.", operation
        )
        # On ne set PAS self._human_validation_denied = True intentionnellement :
        # cela permettrait d'approuver l'operation suivante sans bloquer tout le batch.
        return False

    # -------------------------------------------------------------------------
    # Recuperation du jeton CSRF
    # -------------------------------------------------------------------------

    def _get_csrf_token(self, page_path: str) -> str:
        """
        Charge la page indiquee et extrait le jeton CSRF.
        Necessite que la session soit deja authentifiee.
        """
        url = f"{self.client.base_url}/{page_path}"
        try:
            # Supprimer temporairement X-Requested-With de la session pour
            # obtenir le HTML complet. Passer None dans headers={} ne suffit
            # pas toujours si le header est defini au niveau session.
            _xrw_backup = self.client._session.headers.pop("X-Requested-With", None)
            try:
                resp = self.client._session.get(
                    url,
                    timeout=self.client.timeout,
                    verify=False,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,"
                                  "application/xml;q=0.9,*/*;q=0.8",
                    },
                )
            finally:
                if _xrw_backup is not None:
                    self.client._session.headers["X-Requested-With"] = _xrw_backup
            token = _extract_csrf_token(resp.text)
            if not token:
                logger.warning(
                    "Jeton CSRF non trouve dans la reponse de %s (status=%d, url=%s)",
                    page_path, resp.status_code, resp.url,
                )
            return token
        except Exception as exc:
            logger.warning("Impossible d'obtenir le jeton CSRF depuis %s : %s", page_path, exc)
            return ""

    # -------------------------------------------------------------------------
    # POST generique (form-encoded, conforme ASP.NET MVC)
    # -------------------------------------------------------------------------

    def _post_form(
        self,
        path: str,
        payload: Dict,
        csrf_token: str = "",
    ) -> Tuple[bool, Any]:
        """
        POST form-encoded vers un endpoint QALITAS avec jeton CSRF.
        ASP.NET MVC attend application/x-www-form-urlencoded, pas JSON.
        """
        if csrf_token:
            payload["__RequestVerificationToken"] = csrf_token

        entry = {
            "timestamp": datetime.now().isoformat(),
            "endpoint":  path,
            "payload":   {k: v for k, v in payload.items() if k != "__RequestVerificationToken"},
            "dry_run":   self.dry_run,
            "success":   None,
            "response":  None,
        }

        if self.dry_run:
            preview = json.dumps(entry["payload"], ensure_ascii=False, default=str)[:300]
            logger.info("[DRY-RUN] POST %s | %s", path, preview)
            entry["success"] = True
            entry["response"] = {"dry_run": True, "simulated": True}
            self._write_log.append(entry)
            return True, entry["response"]

        if not self._ensure_human_validation(f"POST {path}", entry["payload"]):
            entry["success"] = False
            entry["response"] = {"error": "Injection refusee par validation humaine"}
            self._write_log.append(entry)
            return False, entry["response"]

        url = f"{self.client.base_url}/{path}"
        try:
            resp = self.client._session.post(
                url,
                data=payload,                    # form-encoded, pas JSON
                timeout=self.client.timeout,
                verify=False,
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                allow_redirects=True,
            )
            if not resp.ok:
                # Logger le corps de la reponse avant de lever l'exception
                # (utile pour diagnostiquer les 400/500 cote serveur)
                logger.error(
                    "POST %s -> HTTP %d | corps : %s",
                    path, resp.status_code, resp.text[:500]
                )
            resp.raise_for_status()
            try:
                response_data = resp.json()
            except Exception:
                # QALITAS peut repondre avec HTML (redirect apres save)
                response_data = {
                    "raw":         resp.text[:300],
                    "status_code": resp.status_code,
                    "final_url":   resp.url,
                }

            success = resp.status_code in (200, 201, 302)
            entry["success"] = success
            entry["response"] = response_data
            self._write_log.append(entry)

            if success:
                logger.info("POST %s -> OK (%d)", path, resp.status_code)
            else:
                logger.warning("POST %s -> ECHEC %d : %s", path, resp.status_code, response_data)

            return success, response_data

        except Exception as exc:
            logger.error("POST %s -> ERREUR : %s", path, exc)
            entry["success"] = False
            entry["response"] = {"error": str(exc)}
            self._write_log.append(entry)
            return False, {"error": str(exc)}

    # -------------------------------------------------------------------------
    # Sauvegarde appreciation / reevaluation par risque (Agent2)
    # -------------------------------------------------------------------------

    def _get_appreciation_row(
        self,
        evaluation_id: str,
        risk_opportunity_id: str,
    ) -> Dict:
        """
        Charge la ligne d'appreciation d'un risque depuis QALITAS via
        GET /GetRiskAppreciation?evaluationId=...

        Retourne le dict de la ligne correspondant a risk_opportunity_id,
        ou {} si non trouve.

        Champs utiles retournes par QALITAS :
            Id                       : UUID propre de la ligne appreciation (OBLIGATOIRE pour POST)
            RiskOpportunityEvaluationId : = evaluation_id (cle etrangere)
            RiskOpportunityNature    : 0=risque, 1=opportunite
            EvaluationState          : entier (0=Brouillon, 1=En cours, 2=Realise)
            EvaluationFormula        : formule RPN (ex: "F*G")
            Parameter1..10           : valeurs actuelles des facteurs
            Score, ScorePrime        : RPN actuels
            DecisionEvalRiskId, ResultEvalId, ResultEvalPrimeId : UUIDs actuels
        """
        if self.dry_run:
            # En dry-run, on ne peut pas appeler le serveur.
            # Retourner un dict minimal coherent pour que le payload soit valid.
            return {
                "Id":                          "dry-run-appreciation-id",
                "RiskOpportunityEvaluationId": evaluation_id,
                "RiskOpportunityNature":       0,
                "EvaluationState":             2,
                "EvaluationFormula":           "F*G",
            }
        url = (
            f"{self.client.base_url}/{WRITE_ENDPOINTS['get_appreciations']}"
            f"?evaluationId={evaluation_id}"
        )
        try:
            resp = self.client._session.get(url, timeout=self.client.timeout, verify=False)
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list):
                logger.warning("GetRiskAppreciation : reponse inattendue (non liste) pour %s", evaluation_id)
                return {}
            for row in rows:
                if row.get("RiskOpportunityId") == risk_opportunity_id:
                    return row
            # Ligne absente : retourner un stub pour insertion (CRUD=1)
            logger.info(
                "GetRiskAppreciation : risque %s absent de la campagne %s -> mode insertion (CRUD=1)",
                risk_opportunity_id, evaluation_id
            )
            return {
                "Id":                          "",  # vide = nouvelle ligne
                "RiskOpportunityEvaluationId": evaluation_id,
                "RiskOpportunityId":           risk_opportunity_id,
                "RiskOpportunityNature":       0,
                "EvaluationState":             2,
                "EvaluationFormula":           "F*G",
                "_insert":                     True,  # flag interne : mode création
            }
        except Exception as exc:
            logger.error("GetRiskAppreciation ERREUR pour evaluation %s : %s", evaluation_id, exc)
            return {}

    @staticmethod
    def _parse_formula(formula: str) -> Tuple[int, bool]:
        """
        Analyse une formule QALITAS et retourne (nb_parametres, est_multiplicative).

        Une formule est consideree multiplicative si elle ne contient que des operateurs *
        et des termes alphabetiques (ex: F*G, P*G*M*D*C, G*D*F).
        Une formule non multiplicative (9+g, vide, inconnue) est traitee comme libre :
        Agent 2 injectera son propre score calcule sans essayer de decomposer les facteurs.

        Retourne :
            (nb_params, True)  : formule multiplicative reconnue
            (2,         False) : formule non standard ou vide -> Agent 2 calcule seul
        """
        if not formula or not formula.strip():
            return 2, False

        f = formula.strip()

        # Formule multiplicative pure : uniquement lettres et '*'
        # Exemples valides : F*G, P*G*M, P*G*M*D*C, G*D*F
        if re.fullmatch(r'[A-Za-z](\*[A-Za-z])+', f):
            nb = len(f.split('*'))
            return nb, True

        # Formule multiplicative avec termes multi-lettres : F*G*M*d (minuscules acceptees)
        if re.fullmatch(r'[A-Za-z]{1,4}(\*[A-Za-z]{1,4})+', f):
            nb = len(f.split('*'))
            return nb, True

        # Tout autre cas (9+g, vide, formule complexe) : mode libre
        return 2, False

    def save_risk_appreciation(
        self,
        evaluation_id: str,
        risk_opportunity_id: str,
        f_prime: float,
        g_prime: float,
        score_prime: float,
        decision_eval_risk_id: str = "",
        result_eval_id: str = "",
        result_eval_prime_id: str = "",
        decision_comments: str = "",
        f_brut: float = 0.0,
        g_brut: float = 0.0,
        score_brut: float = 0.0,
        indice_maitrise: float = 0.5,
    ) -> Tuple[bool, Any]:
        """
        Met a jour l'appreciation residuelle d'un risque dans une campagne d'evaluation.

        Workflow confirme par capture navigateur (02/04/2026) :
        Etape 1 : GET GetRiskAppreciation?evaluationId=... -> trouver la ligne par RiskOpportunityId
                  -> extraire Id (UUID propre de la ligne), EvaluationFormula, EvaluationState, Nature
        Etape 2 : POST EditRiskAppreciation avec le payload complet incluant Id

        CRITIQUE : Le champ "Id" (UUID de la ligne appreciation) est OBLIGATOIRE.
        Sans lui QALITAS retourne HTTP 200 mais "false|Impossible de modifier cet enregistrement!".

        Champs obligatoires dans le payload :
            Id                       : UUID propre de la ligne (issu de GetRiskAppreciation)
            RiskOpportunityEvaluationId : = evaluation_id
            EvaluationId             : = evaluation_id
            RiskOpportunityId        : GUID du risque
            RiskOpportunityNature    : 0 = risque, 1 = opportunite
            EvaluationState          : entier (etat actuel de l'evaluation)
            EvaluationFormula        : formule RPN (ex: "F*G")
            CRUD                     : "0" (convention QALITAS pour update)
            Parameter1               : F brut
            Parameter2               : G brut
            Score                    : F*G (RPN brut)
            ParameterPrime1          : F' residuel
            ParameterPrime2          : G' residuel
            ScorePrime               : F'*G' (RPN residuel)
            DecisionEvalRiskId       : UUID decision (config QALITAS)
            ResultEvalId             : UUID resultat brut
            ResultEvalPrimeId        : UUID resultat residuel
            DecisionComments         : justification LLM (optionnel)

        Reponse QALITAS : "true|Enregistrement effectue avec succes" si OK.
        """
        if not self._ensure_human_validation(
            "Mise a jour appreciation risque Agent2",
            {
                "EvaluationId": evaluation_id,
                "RiskOpportunityId": risk_opportunity_id,
                "ScorePrime": score_prime,
                "DecisionComments": decision_comments[:300] if decision_comments else "",
            },
        ):
            return False, {"error": "Injection refusee par validation humaine"}

        # --- Etape 1 : recuperer la ligne d'appreciation pour obtenir le Id ---
        row = self._get_appreciation_row(evaluation_id, risk_opportunity_id)
        if row is None and not self.dry_run:
            logger.error(
                "Impossible de contacter QALITAS pour risque %s "
                "dans evaluation %s — abandon.",
                risk_opportunity_id, evaluation_id
            )
            return False, {"error": "Erreur reseau GetRiskAppreciation"}
        if not row:
            row = {
                "Id": "", "RiskOpportunityEvaluationId": evaluation_id,
                "RiskOpportunityId": risk_opportunity_id,
                "RiskOpportunityNature": 0, "EvaluationState": 2,
                "EvaluationFormula": "F*G", "_insert": True,
            }

        appreciation_row_id  = row.get("Id", "dry-run-appreciation-id")
        is_insert_mode       = row.get("_insert", False) or not appreciation_row_id
        nature               = row.get("RiskOpportunityNature", 0)
        eval_state           = row.get("EvaluationState", 2)
        eval_formula         = row.get("EvaluationFormula", "F*G") or "F*G"

        # Conserver les valeurs brutes actuelles si non fournies en parametre
        if not f_brut:
            f_brut = float(row.get("Parameter1") or 0)
        if not g_brut:
            g_brut = float(row.get("Parameter2") or 0)
        if not score_brut:
            score_brut = float(row.get("Score") or 0)

        # Conserver les UUIDs existants si le caller ne les fournit pas
        if not result_eval_id:
            result_eval_id = row.get("ResultEvalId", "")
        if not result_eval_prime_id:
            result_eval_prime_id = row.get("ResultEvalPrimeId", "")
        if not decision_eval_risk_id:
            decision_eval_risk_id = row.get("DecisionEvalRiskId", "")

        # --- Etape 2 : analyser la formule de la campagne ---
        param_count, is_multiplicative = self._parse_formula(eval_formula)

        if is_multiplicative:
            logger.debug(
                "Formule multiplicative '%s' -> %d parametres pour risque %s",
                eval_formula, param_count, risk_opportunity_id
            )
        else:
            logger.debug(
                "Formule non standard ou vide '%s' -> Agent2 utilise son propre score "
                "pour risque %s",
                eval_formula, risk_opportunity_id
            )

        # --- Construction du payload de base ---
        payload = {
            # Champs d'identification (OBLIGATOIRES)
            "Id":                          appreciation_row_id,
            "RiskOpportunityEvaluationId": evaluation_id,
            "EvaluationId":                evaluation_id,
            "RiskOpportunityId":           risk_opportunity_id,
            "RiskOpportunityNature":       str(nature),
            "EvaluationState":             str(eval_state),
            "EvaluationFormula":           eval_formula,
            "CRUD":                        "1" if is_insert_mode else "0",
            # Parametres 1 et 2 : toujours geres par Agent2 (F/P et G depuis Excel)
            "Parameter1":                  str(round(f_brut, 2)) if f_brut else "",
            "Parameter2":                  str(round(g_brut, 2)) if g_brut else "",
            # Score : calcule ci-dessous selon le mode de formule
            "Score":                       str(round(score_brut, 2)) if score_brut else "",
            # Decision
            "DecisionEvalRiskId":          decision_eval_risk_id,
            # Parametres residuels 1 et 2 : Agent2 les calcule
            "ParameterPrime1":             str(round(f_prime, 2)),
            "ParameterPrime2":             str(round(g_prime, 2)),
            # ScorePrime : calcule ci-dessous selon le mode de formule
            "ScorePrime":                  str(round(score_prime, 2)),
            # Resultats
            "ResultEvalId":                result_eval_id,
            "ResultEvalPrimeId":           result_eval_prime_id,
            # Commentaire / justification
            "DecisionComments":            decision_comments[:1000] if decision_comments else "",
        }

        # Initialiser les parametres 3..10 vides par defaut
        for i in range(3, 11):
            payload[f"Parameter{i}"]      = ""
            payload[f"ParameterPrime{i}"] = ""

        if is_multiplicative:
            # ----------------------------------------------------------------
            # MODE FORMULE MULTIPLICATIVE (F*G, P*G*M, P*G*M*D*C, etc.)
            # ----------------------------------------------------------------
            # Parametres 3..N : lus depuis QALITAS (saisis par les auditeurs QSE).
            # Agent2 ne les modifie pas : M, D, C sont des facteurs de maitrise
            # organisationnels qui appartiennent aux responsables de processus.
            # Agent2 preserve ces valeurs et les renvoie pour que QALITAS
            # puisse recalculer le Score complet.
            for i in range(3, param_count + 1):
                raw_brut  = row.get(f"Parameter{i}")
                raw_prime = row.get(f"ParameterPrime{i}")
                if raw_brut is not None and str(raw_brut).strip() not in ("", "None", "0", "0.0"):
                    brut_val = float(raw_brut)
                    payload[f"Parameter{i}"] = str(round(brut_val, 2))
                    if raw_prime is not None and str(raw_prime).strip() not in ("", "None"):
                        payload[f"ParameterPrime{i}"] = str(round(float(raw_prime), 2))
                    else:
                        # Approximation : appliquer l'indice de maitrise Agent2
                        payload[f"ParameterPrime{i}"] = str(round(brut_val * indice_maitrise, 2))

            # Recalculer Score = P1 * P2 * ... * PN si tous presents
            def _product(prefix: str) -> Optional[str]:
                vals = []
                for i in range(1, param_count + 1):
                    v = payload.get(f"{prefix}{i}", "")
                    if not v:
                        return None
                    try:
                        vals.append(float(v))
                    except ValueError:
                        return None
                res = 1.0
                for val in vals:
                    res *= val
                return str(round(res, 2))

            computed_score = _product("Parameter")
            if computed_score is not None:
                payload["Score"] = computed_score
                logger.debug("Score recalcule : %s (%s)", computed_score, eval_formula)

            computed_prime = _product("ParameterPrime")
            if computed_prime is not None:
                payload["ScorePrime"] = computed_prime
                logger.debug("ScorePrime recalcule : %s", computed_prime)

        else:
            # ----------------------------------------------------------------
            # MODE LIBRE : formule vide, inconnue ou non multiplicative
            # ----------------------------------------------------------------
            # Agent2 utilise son propre score calcule (F*G depuis Excel).
            # Il n'essaie pas de decomposer les parametres selon une formule
            # qu'il ne comprend pas. Il envoie uniquement :
            #   - Parameter1 = F (frequence/probabilite brute)
            #   - Parameter2 = G (gravite brute)
            #   - Score      = score_brut  (calcule par agent2_evaluation)
            #   - ParameterPrime1/2 et ScorePrime = valeurs residuelles Agent2
            # Parameter3..10 restent vides (initialises ci-dessus).
            # QALITAS stockera le score Agent2 tel quel.
            logger.info(
                "Formule '%s' non reconnue pour risque %s : injection du score "
                "Agent2 direct (F=%s G=%s Score=%s)",
                eval_formula, risk_opportunity_id,
                payload["Parameter1"], payload["Parameter2"], payload["Score"]
            )

        # Jeton CSRF depuis la page d'appreciation (meme page que la grille)
        csrf = ""
        if not self.dry_run:
            csrf_page = f"RiskOpportunityEvaluation/Appreciation?evaluationId={evaluation_id}"
            csrf = self._get_csrf_token(csrf_page)
            if not csrf:
                logger.warning("Jeton CSRF non obtenu pour evaluation %s", evaluation_id)

        ok, resp_data = self._post_form(
            WRITE_ENDPOINTS["save_appreciation"],
            payload,
            csrf_token=csrf,
        )

        # Verifier la reponse metier QALITAS ("true|..." vs "false|...")
        # QALITAS retourne HTTP 200 pour succes ET echec metier.
        # Le corps est : "true|Message"  ou  "false|Message" (JSON string ou texte brut).
        if ok and not self.dry_run:
            if isinstance(resp_data, str):
                qalitas_msg = resp_data
            elif isinstance(resp_data, dict):
                qalitas_msg = resp_data.get("raw", "")
            else:
                qalitas_msg = ""

            if qalitas_msg.startswith("false|"):
                logger.warning(
                    "EditRiskAppreciation : HTTP 200 mais echec metier : %s", qalitas_msg
                )
                return False, resp_data
            elif qalitas_msg.startswith("true|"):
                logger.info(
                    "EditRiskAppreciation : succes confirme par QALITAS : %s", qalitas_msg
                )

        return ok, resp_data

    # -------------------------------------------------------------------------
    # Creation d'un nouveau risque ou opportunite dans QALITAS (Agent 1)
    # -------------------------------------------------------------------------

    def create_risk_opportunity(
        self,
        designation: str,
        description: str,
        nature: int = 0,
        process_id: str = "",
        system_q: bool = True,
        system_s: bool = False,
        system_e: bool = False,
        source_swot: str = "",
        gravity_code: str = "",
        state: int = 0,
        responsable: str = "",
        causes: str = "",
        consequences: str = "",
        type_code: str = "",
        category_code: str = "",
    ) -> Tuple[bool, Any]:
        """
        Cree un nouveau risque ou opportunite dans QALITAS via POST /RiskOpportunity/Create.

        Workflow confirme par interception navigateur (03/05/2026) :
          1. GET /RiskOpportunity/Create?nature=N&source=0&sourceId=&selectedIssues=
             -> retourne HTML 16KB avec CSRF token + GUID Id server-side
          2. POST /RiskOpportunity/Create avec les 34 champs du formulaire

        Cette methode est appelee par inject_agent1_results() pour les R&O identifies
        depuis les fiches Excel clients et absents du registre QALITAS.

        Champs :
            designation  : intitule court du risque ou de l'opportunite
            description  : description complete (causes + consequences)
            nature       : 0 = risque (defaut), 1 = opportunite
            process_id   : GUID du processus QALITAS (chaine vide = non renseigne)
            system_q/s/e : indicateurs systeme de management (Q=qualite, S=securite, E=env)
            source_swot  : SWOT source (S=forces, W=faiblesses, O=opportunites, T=menaces)
            gravity_code : code gravite brut (G001..G004) pour la nature du risque
            state        : 0=Brouillon, 1=Identifie, 2=En cours de traitement
            responsable  : nom ou libelle du responsable
            causes       : causes identifiees (integre dans la description si fourni)
            consequences : consequences identifiees (integre dans la description si fourni)

        Retourne (True, dict_reponse) si succes, (False, dict_erreur) sinon.
        """
        import uuid as _uuid

        def bstr(b: bool) -> str:
            return "True" if b else "False"

        # Construction de la description complete si causes/consequences fournies
        desc_complete = description.strip()
        if causes and causes not in desc_complete:
            desc_complete += f"\n\nCauses : {causes[:500]}"
        if consequences and consequences not in desc_complete:
            desc_complete += f"\n\nConsequences : {consequences[:500]}"

        # Mapping source_swot vers code QALITAS
        # S/W/O/T correspondent aux sources des inducteurs SWOT
        swot_map = {"S": "1", "W": "2", "O": "3", "T": "4", "": "0"}
        source_code = swot_map.get(str(source_swot).upper()[:1], "0")

        if not self._ensure_human_validation(
            "Creation R&O Agent1 dans QALITAS",
            {
                "Nature":      "Risque" if nature == 0 else "Opportunite",
                "Designation": designation[:100],
                "ProcessId":   process_id,
                "State":       state,
            },
        ):
            return False, {"error": "Injection refusee par validation humaine"}

        # -----------------------------------------------------------------
        # Etape 1 : GET RiskOpportunity/Create?nature=N&source=0&...
        #           -> retourne HTML 16KB avec :
        #              - __RequestVerificationToken (CSRF, 108 cars)
        #              - Id (GUID server-side, ex: 801166d4-...)
        #              - CompanyId = '00000000-0000-0000-0000-000000000000'
        #              - SiteId    = '00000000-0000-0000-0000-000000000000'
        #              - Impact    = '0', Nature = '0', Q/S/E/H = 'False'
        #
        # CRITIQUE : CompanyId et SiteId doivent etre le GUID nul (tout zeros),
        # PAS une chaine vide. Le serveur rejette silencieusement les chaines vides
        # avec "false|Echec de l'ajout..." meme en HTTP 200.
        # Solution : extraire TOUS les hidden inputs du GET et les utiliser comme
        # base du payload (meme comportement que le navigateur).
        #
        # Confirme par interception navigateur + test API (03/05/2026).
        # -----------------------------------------------------------------
        risk_id = ""
        csrf = ""
        server_hidden: Dict[str, str] = {}

        if not self.dry_run:
            init_url = (
                f"{self.client.base_url}/RiskOpportunity/Create"
                f"?nature={nature}&source=0&sourceId=&selectedIssues="
            )
            try:
                init_resp = self.client._session.get(
                    init_url,
                    timeout=self.client.timeout,
                    verify=False,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,"
                                  "application/xml;q=0.9,*/*;q=0.8",
                    },
                )
                # Extraire TOUS les champs hidden (base identique au navigateur)
                for inp in re.findall(
                    r'<input[^>]+type=["\']hidden["\'][^>]*/?>',
                    init_resp.text, re.I
                ):
                    nm = re.search(r'name=["\']([^"\']+)["\']', inp, re.I)
                    vl = re.search(r'value=["\']([^"\']*)["\']', inp, re.I)
                    if nm:
                        server_hidden[nm.group(1)] = vl.group(1) if vl else ""

                csrf    = server_hidden.get("__RequestVerificationToken", "")
                risk_id = server_hidden.get("Id", "")

                if not risk_id:
                    risk_id = str(_uuid.uuid4())
                    logger.warning(
                        "Id GUID non extrait depuis RiskOpportunity/Create, "
                        "UUID local genere : %s", risk_id
                    )
                if not csrf:
                    logger.warning("CSRF non obtenu depuis RiskOpportunity/Create")
                logger.debug(
                    "Init R&O : Id=%s | CompanyId=%s | CSRF=%s...",
                    risk_id,
                    server_hidden.get("CompanyId", "?"),
                    csrf[:20] if csrf else "vide",
                )
            except Exception as exc:
                logger.error("Echec GET init RiskOpportunity/Create : %s", exc)
                return False, {"error": f"Echec GET init : {exc}"}
        else:
            # Dry-run : simuler Id et CSRF
            risk_id = str(_uuid.uuid4())
            csrf    = "dry-run-token"
            # En dry-run : simuler CompanyId/SiteId avec GUID nul (valeurs reelles du serveur)
            server_hidden = {
                "CompanyId": "00000000-0000-0000-0000-000000000000",
                "SiteId":    "00000000-0000-0000-0000-000000000000",
                "Impact":    "0",
            }

        # -----------------------------------------------------------------
        # Construire le payload final :
        # Base = champs hidden extraits du GET (CompanyId, SiteId, Impact...)
        # + surcharge des champs metier (Designation, Description, Q/S/E/H...)
        # Cette approche reproduit exactement le comportement du navigateur.
        # -----------------------------------------------------------------
        payload = dict(server_hidden)  # base : tout ce que le serveur fournit
        # Retirer le CSRF (gere separement par _post_form)
        payload.pop("__RequestVerificationToken", None)

        payload.update({
            "Id":               risk_id,
            "Designation":      designation[:200],
            "Description":      desc_complete[:4000],
            "Objectives":       "",
            "Nature":           str(nature),
            "Source":           source_code,
            "SourceId":         "",
            "RiskOppSourceStr": "",
            "ReferenceSource":  "",
            "ProcessId":        process_id,
            "Q":                bstr(system_q),
            "S":                bstr(system_s),
            "E":                bstr(system_e),
            "H":                "False",
            "TypesId":          RISK_TYPE_GUIDS.get((type_code or "").lower().strip(), ""),
            "CategoryId":       RISK_CATEGORY_GUIDS.get((category_code or "").lower().strip(), ""),
            "PostId":           "",
            "EmployeeId":       "",
            "IdentificationDate": datetime.now().strftime("%d/%m/%Y"),
            "DetectionMeans":   "",
            "Cause":            causes[:500] if causes else "",
            "Consequence":      consequences[:500] if consequences else "",
            "UncertainEvent":   "",
        })

        ok, resp = self._post_form(
            WRITE_ENDPOINTS["save_risk"],
            payload,
            csrf_token=csrf,
        )

        # Verification reponse metier QALITAS (HTTP 200 avec "true|..." ou "false|...")
        if ok and not self.dry_run:
            if isinstance(resp, str):
                qmsg = resp
            elif isinstance(resp, dict):
                qmsg = resp.get("raw", "")
            else:
                qmsg = ""
            if qmsg.startswith("false|"):
                logger.warning("create_risk_opportunity : echec metier QALITAS : %s", qmsg)
                return False, resp

        return ok, resp

    # -------------------------------------------------------------------------
    # Creation d'action corrective (workflow 2 etapes confirme)
    # -------------------------------------------------------------------------

    def create_risk_action(
        self,
        risk_opportunity_id: str,
        title: str,
        description: str,
        evaluation_id: str = "",
        action_source: str = "",
        process_id: str = "",
        priority_id: str = "",
        gravity_id: str = "",
        action_type_id: str = "",
        root_cause: str = "",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        state: int = 0,
        with_analysis: bool = False,
        with_efficiency: bool = True,
        with_escalation: bool = True,
        system_q: bool = True,
        system_s: bool = False,
        system_e: bool = False,
    ) -> Tuple[bool, Any]:
        """
        Cree une action corrective liee a un risque dans QALITAS.

        Workflow confirme par exploration navigateur (01/04/2026) :
        Etape 1 : GET /Actions/Create?requestId=undefined&actionLink={...}
                  -> QALITAS cree un enregistrement provisoire en session
                  -> Retourne un HTML avec : Id (UUID), __RequestVerificationToken
        Etape 2 : POST /Actions/Create avec tous les champs du formulaire

        Champs confirmes par inspection DOM du formulaire (02/04/2026) :
            State       : entier (0=Programme, 1=En cours, 2=Realise, 3=Annule)
            StartDateP  : format "DD/MM/YYYY 00:00:00" (avec heure)
            Booleens    : "True"/"False" (majuscule, convention ASP.NET)
            CostsP/ManDaysP : chaine vide par defaut (pas "0")
            WithEscalation  : True par defaut dans QALITAS

        risk_opportunity_id : GUID du risque source (TriggerSourceId dans QALITAS)
        title               : intitule de l'action (Designation)
        description         : description detaillee (champ Description, HTML accepte)
        process_id          : GUID du processus concerne (vide = non defini)
        priority_id         : GUID de la priorite QALITAS (vide = non defini)
        gravity_id          : GUID de la gravite QALITAS (vide = non defini)
        start_date          : date de debut prevue (defaut = aujourd'hui)
        end_date            : date de fin prevue (defaut = dans 30j)
        """
        import uuid
        import json as _json

        if start_date is None:
            start_date = datetime.now()
        if end_date is None:
            end_date = datetime.now() + timedelta(days=30)

        if not self._ensure_human_validation(
            "Creation action corrective QALITAS",
            {
                "RiskOpportunityId": risk_opportunity_id,
                "Designation": title,
                "Description": description[:300] if description else "",
                "EvaluationId": evaluation_id,
                "EndDate": end_date,
            },
        ):
            return False, {"error": "Injection refusee par validation humaine"}

        # Format confirme par inspection DOM du formulaire (02/04/2026) :
        # QALITAS attend la date ET l'heure : "02/04/2026 00:00:00"
        date_fmt = "%d/%m/%Y 00:00:00"

        # --- Etape 1 : init de la session action (GET) ---
        action_id = ""
        csrf_token = ""
        src = action_source if action_source else ACTION_SOURCE_RISQUE

        # TriggerSourceId = RiskOpportunityId TOUJOURS quand Source="11" (Risques).
        # Confirme par test_injection_browser.js : TriggerSourceId = RISK_ID, ProvisionalLinkId = RISK_ID.
        # L'evaluation_id n'est utilise qu'en ProvisionalLinkId1 optionnel (contexte uniquement).
        action_link = _json.dumps({
            "LinkType":           10,
            "LinkSource":         src,
            "ProvisionalLinkId":  risk_opportunity_id if risk_opportunity_id else None,
            "ProvisionalLinkId1": evaluation_id if evaluation_id else None,
            "ProvisionalLinkId2": None,
        })

        if not self.dry_run:
            init_url = (
                f"{self.client.base_url}/{WRITE_ENDPOINTS['init_action_create']}"
                f"?requestId=undefined&actionLink={action_link}"
            )
            try:
                init_resp = self.client._session.get(
                    init_url, timeout=self.client.timeout, verify=False
                )
                csrf_token = _extract_csrf_token(init_resp.text)
                action_id  = _extract_hidden_id(init_resp.text, "Id")
                if not action_id:
                    # Fallback : generer un UUID local (moins fiable)
                    action_id = str(uuid.uuid4())
                    logger.warning(
                        "Id provisoire non extrait depuis QALITAS, UUID local genere : %s",
                        action_id
                    )
                logger.debug("Init action : Id=%s, CSRF=%s...", action_id, csrf_token[:20])
            except Exception as exc:
                logger.error("Echec init action (etape 1) : %s", exc)
                return False, {"error": f"Echec GET init : {exc}"}
        else:
            # Dry-run : simuler avec un UUID factice
            action_id  = str(uuid.uuid4())
            csrf_token = "dry-run-token"

        # Helper : convertit un bool Python en "True"/"False" (convention ASP.NET MVC)
        def bstr(b: bool) -> str:
            return "True" if b else "False"

        # --- Etape 2 : soumettre le formulaire ---
        # Valeurs confirmees par inspection DOM du formulaire (02/04/2026)
        payload = {
            "Id":                  action_id,
            "Source":              src,
            "TriggerSourceId":     risk_opportunity_id,   # toujours le GUID du R&O (Source="11")
            "Progression":         "0",
            "IsEffective":         "2",
            # Champs optionnels de classification
            "ProcessId":           process_id,
            "TypesId":             action_type_id,
            "CategoryId":          "",
            "PriorityId":          priority_id,
            "GravityId":           gravity_id,
            # Contenu
            "Designation":         title[:200],
            "Description":         description[:4000],
            "ProofEfficiency":     "",
            "RootCause":           root_cause[:2000] if root_cause else "",
            # Systemes (booléens ASP.NET = "True"/"False" avec majuscule)
            "IsConfidential":      "False",
            "Q":                   bstr(system_q),
            "S":                   bstr(system_s),
            "E":                   bstr(system_e),
            "H":                   "False",
            # Options
            "WithAnalysis":        bstr(with_analysis),
            "WithEfficiency":      bstr(with_efficiency),
            "WithEscalation":      bstr(with_escalation),
            # Planification (State = entier : 0=Programme, 1=En cours, 2=Realise)
            "StartDateP":          start_date.strftime(date_fmt),
            "EndDateP":            end_date.strftime(date_fmt),
            "CostsP":              "",   # vide par defaut (confirme)
            "ManDaysP":            "",   # vide par defaut (confirme)
            "State":               str(state),
            # Realisation (vide a la creation)
            "StartDateR":          "",
            "EndDateR":            "",
            "Costs":               "",
            "ManDays":             "",
            "VerifyingRealisationDateP": "",
            "VerifyingRealisationDateR": "",
            "VerifyingEfficiencyDateP":  "",
            "VerifyingEfficiencyDateR":  "",
            "AnalysisDateP":       "",
            "AnalysisState":       "0",  # entier par defaut (confirme)
            "AnalysisDateR":       "",
        }

        return self._post_form(
            WRITE_ENDPOINTS["save_action"],
            payload,
            csrf_token=csrf_token,
        )

    # -------------------------------------------------------------------------
    # Creation d'une nouvelle campagne d'evaluation
    # -------------------------------------------------------------------------

    def create_evaluation_campaign(
        self,
        designation: str,
        formula: str = "F*G",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        min_score: float = 1.0,
        max_score: float = 25.0,
    ) -> Tuple[bool, str]:
        """
        Cree une nouvelle campagne d'evaluation dans QALITAS.

        Workflow (2 etapes, identique a RiskOpportunity/Create) :
          1. GET /RiskOpportunityEvaluation/Create -> HTML avec CSRF + GUID serveur
          2. POST /RiskOpportunityEvaluation/Create avec le payload

        Retourne (True, evaluation_id) si succes, (False, "") sinon.
        """
        import uuid as _uuid

        date_fmt = "%d/%m/%Y"
        eval_id = ""
        csrf = ""
        server_hidden: Dict[str, str] = {}

        if not self.dry_run:
            init_url = f"{self.client.base_url}/RiskOpportunityEvaluation/Create"
            try:
                init_resp = self.client._session.get(
                    init_url,
                    timeout=self.client.timeout,
                    verify=False,
                    headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
                )
                for inp in re.findall(
                    r'<input[^>]+type=["\']hidden["\'][^>]*/?>',
                    init_resp.text, re.I
                ):
                    nm = re.search(r'name=["\']([^"\']+)["\']', inp, re.I)
                    vl = re.search(r'value=["\']([^"\']*)["\']', inp, re.I)
                    if nm:
                        server_hidden[nm.group(1)] = vl.group(1) if vl else ""

                # Utiliser _extract_csrf_token (robuste, plusieurs regex)
                csrf    = _extract_csrf_token(init_resp.text)
                eval_id = server_hidden.get("Id", "")

                # Fallback CSRF : essayer plusieurs pages qui exposent toujours un token
                if not csrf:
                    for fallback_page in [
                        "Actions/Create?nature=0&source=11&sourceId=&selectedIssues=",
                        "RiskOpportunity/Create?nature=0&source=0&sourceId=&selectedIssues=",
                        "Account/ChangePassword",
                    ]:
                        csrf = self._get_csrf_token(fallback_page)
                        if csrf:
                            logger.info("CSRF obtenu via fallback : %s", fallback_page)
                            break
                if not csrf:
                    logger.error("Jeton CSRF introuvable pour EvaluationCreate — abandon")
                    return False, ""
                if not eval_id:
                    eval_id = str(_uuid.uuid4())
                    logger.info("Id campagne genere localement : %s", eval_id)
            except Exception as exc:
                logger.error("Echec GET init RiskOpportunityEvaluation/Create : %s", exc)
                return False, ""
        else:
            eval_id = str(_uuid.uuid4())
            csrf = "dry-run-token"
            server_hidden = {
                "CompanyId": "00000000-0000-0000-0000-000000000000",
                "SiteId":    "00000000-0000-0000-0000-000000000000",
            }

        sd = start_date or datetime.now()
        ed = end_date or datetime.now()

        # Recuperer les identifiants de site/company depuis l'env ou les valeurs confirmes
        site_id    = os.environ.get("QALITAS_SITE_ID",    "39d00cd5-32af-c531-d230-e935a535103e")
        company_id = os.environ.get("QALITAS_COMPANY_ID", "39d00cd5-3251-9b25-bca0-bf46aa71c52b")

        # Determiner la formule et les parametres depuis une campagne existante si possible
        actual_formula      = formula
        actual_param_number = str(len(formula.split("*"))) if "*" in formula else "2"
        param_fields: Dict[str, str] = {}
        try:
            existing_evals = self.client.get_evaluations("") if not self.dry_run else []
            if existing_evals:
                ref = existing_evals[-1]
                actual_formula      = ref.get("Formula") or formula
                pn = len(actual_formula.split("*")) if "*" in actual_formula else 2
                actual_param_number = str(pn)
                for i in range(1, pn + 1):
                    pval = ref.get(f"Parameter{i}") or ""
                    pdes = ref.get(f"ParameterDes{i}") or ""
                    if pval:
                        param_fields[f"Parameter{i}"]    = pval
                        param_fields[f"ParameterDes{i}"] = pdes
        except Exception:
            pass

        payload = dict(server_hidden)
        payload.pop("__RequestVerificationToken", None)
        payload.update({
            "Designation":      designation[:200],
            "Formula":          actual_formula,
            "State":            "2",
            "Nature":           "0",
            "SiteId":           site_id,
            "CompanyId":        company_id,
            "StartDate":        sd.strftime(date_fmt),
            "EndDate":          ed.strftime(date_fmt),
            "MinScore":         str(round(min_score, 2)),
            "MaxScore":         str(round(max_score, 2)),
            "RevaluationState": "1",
            "ParameterNumber":  actual_param_number,
            "CRUD":             "1",
        })
        payload.update(param_fields)

        ok, resp = self._post_form(
            "RiskOpportunityEvaluation/Create",
            payload,
            csrf_token=csrf,
        )

        if ok and isinstance(resp, str) and resp.startswith("false|"):
            logger.warning("create_evaluation_campaign : echec metier QALITAS : %s", resp)
            return False, ""

        if ok:
            logger.info("Campagne evaluation creee : %s (%s)", designation, eval_id)
            return True, eval_id

        return False, ""

    # Mise a jour de la campagne d'evaluation (evaluation metadata)
    # -------------------------------------------------------------------------

    def save_evaluation_campaign(
        self,
        evaluation_id: str,
        designation: str,
        formula: str = "F*G",
        state: int = 2,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        min_score: float = 1.0,
        max_score: float = 25.0,
        revaluation_date_p: Optional[datetime] = None,
        revaluation_state: int = 1,
    ) -> Tuple[bool, Any]:
        """
        Met a jour les metadonnees d'une campagne d'evaluation existante.

        Champs confirmes depuis form-EditRiskOpportunityEvaluation :
            Id, Formula, Designation, ParameterNumber, Parameter1-10,
            Date, State, RealizationDate, StartDate, EndDate, MinScore, MaxScore,
            RevaluationDateP, RevaluationState, RevaluationDateR
        """
        date_fmt = "%d/%m/%Y"
        payload = {
            "Id":               evaluation_id,
            "Designation":      designation[:200],
            "Formula":          formula,
            "State":            str(state),
            "StartDate":        start_date.strftime(date_fmt) if start_date else "",
            "EndDate":          end_date.strftime(date_fmt) if end_date else "",
            "MinScore":         str(round(min_score, 2)),
            "MaxScore":         str(round(max_score, 2)),
            "RevaluationDateP": revaluation_date_p.strftime(date_fmt) if revaluation_date_p else "",
            "RevaluationState": str(revaluation_state),
        }

        if not self._ensure_human_validation(
            "Mise a jour campagne evaluation QALITAS",
            payload,
        ):
            return False, {"error": "Injection refusee par validation humaine"}

        csrf = ""
        if not self.dry_run:
            csrf = self._get_csrf_token(
                f"RiskOpportunityEvaluation/Edit?id={evaluation_id}"
            )

        return self._post_form(
            WRITE_ENDPOINTS["save_evaluation"],
            payload,
            csrf_token=csrf,
        )

    # -------------------------------------------------------------------------
    # Rapport d'ecriture
    # -------------------------------------------------------------------------

    def get_write_report(self) -> Dict[str, Any]:
        """Retourne le resume de toutes les operations d'ecriture."""
        total   = len(self._write_log)
        success = sum(1 for e in self._write_log if e.get("success"))
        failed  = total - success
        return {
            "dry_run": self.dry_run,
            "total":   total,
            "success": success,
            "failed":  failed,
            "details": self._write_log,
        }


# =============================================================================
# INJECTION AGENT 2 -> QALITAS
# =============================================================================

def inject_agent2_results(
    risques: List[Dict],
    writer: QalitasWriter,
    min_niveau: str = "moyen",
) -> Dict[str, Any]:
    """
    Injecte les resultats de l'Agent2 vers QALITAS.

    Pour chaque risque evalue par Agent2 :
    1. Met a jour le score residuel (F', G', RPN') et la decision dans la campagne
    2. Cree une action corrective si le niveau residuel est >= min_niveau

    Le risque doit contenir les champs :
        _raw.EvaluationId         : GUID campagne d'evaluation
        _raw.RiskOpportunityId    : GUID du risque
        score_residuel / RPN'     : valeur numerique du score residuel
        niveau_residuel / niveau  : mineur | moyen | eleve | critique
        indice_maitrise           : float [0-1] (utilise pour deduire F' et G')

    risques     : liste produite par agent2_evaluation + agent2_llm
    writer      : instance QalitasWriter
    min_niveau  : seuil minimum pour creer une action
    """
    NIVEAUX_ORDRE = ["mineur", "moyen", "eleve", "critique"]
    min_idx = NIVEAUX_ORDRE.index(min_niveau) if min_niveau in NIVEAUX_ORDRE else 1

    # --- Chargement de la configuration QALITAS (decisions et resultats) ---
    decisions_cfg: List[Dict] = []
    results_cfg:   List[Dict] = []
    try:
        decisions_cfg = writer.client.get_decision_eval_risks()
        results_cfg   = writer.client.get_result_eval_risk_opps()
        logger.info(
            "Configuration QALITAS chargee : %d decisions, %d resultats",
            len(decisions_cfg), len(results_cfg)
        )
    except Exception as exc:
        logger.warning(
            "Impossible de charger la configuration QALITAS (%s). "
            "Les champs DecisionEvalRiskId/ResultEvalId seront laisses vides.",
            exc
        )

    # -----------------------------------------------------------------------
    # PRE-CHARGEMENT : mapping risk_opp_id -> (eval_id, appreciation_row)
    # Parcourt TOUTES les campagnes existantes pour trouver l'appreciation row
    # de chaque risque (avec son Id UUID obligatoire pour EditRiskAppreciation).
    # Sans ce mapping, les risques dont l'eval_id est absent obtiennent Id=""
    # et QALITAS repond "false|Impossible de modifier cet enregistrement!".
    # -----------------------------------------------------------------------
    _risk_to_eval_row: Dict[str, tuple] = {}  # risk_opp_id -> (eval_id, row_dict)
    _latest_active_eval_id: str = ""          # derniere campagne active (fallback)
    # Le pre-chargement est lecture seule : on le fait aussi en dry_run
    # pour que la simulation affiche les bons row_id (diagnostique fiable).
    try:
        all_evals = writer.client.get_evaluations("")
        # Trier : campagnes les plus recentes en premier (State=1 En cours prioritaire)
        active_evals = sorted(
            all_evals,
            key=lambda e: (e.get("State", 0) == 1, e.get("StartDate", "") or ""),
            reverse=True,
        )
        if active_evals:
            _latest_active_eval_id = active_evals[0].get("Id", "")

        for ev in active_evals:
            ev_id = ev.get("Id", "")
            if not ev_id:
                continue
            try:
                rows = writer.client.get_risk_appreciation(ev_id)
                for row in rows:
                    r_id   = row.get("RiskOpportunityId", "")
                    row_id = row.get("Id", "")
                    # Conserver uniquement la premiere occurrence (campagne la plus recente)
                    if r_id and row_id and r_id not in _risk_to_eval_row:
                        _risk_to_eval_row[r_id] = (ev_id, row)
            except Exception:
                pass

        logger.info(
            "[A2] Mapping appreciation pre-charge : %d risques indexes dans %d campagnes | "
            "campagne active : %s",
            len(_risk_to_eval_row), len(all_evals),
            _latest_active_eval_id[:12] if _latest_active_eval_id else "aucune"
        )
    except Exception as exc:
        logger.warning("[A2] Pre-chargement mapping campagnes echoue : %s", exc)

    stats = {
        "processed":             0,
        "appreciations_updated": 0,
        "actions_created":       0,
        "skipped_no_id":         0,
        "skipped_cache":         0,
        "errors":                0,
        "hist_eval_created":     0,
        "hist_eval_errors":      0,
    }

    # Collecte des risques evalues pour alimenter l'historique evaluation (campagne Agent2)
    _a2_evaluated_risks: List[Dict] = []

    # Charger le cache une seule fois pour tout le batch
    _cache = _load_cache()

    for risque in risques:
        raw         = risque.get("_raw", {}) if isinstance(risque.get("_raw"), dict) else {}
        # Chercher les GUIDs : champs directs (nouveaux) > _raw > vide
        eval_id = (
            risque.get("RiskOpportunityEvaluationId") or risque.get("EvaluationId") or
            raw.get("RiskOpportunityEvaluationId") or raw.get("EvaluationId") or ""
        )
        risk_opp_id = (
            risque.get("RiskOpportunityId") or
            raw.get("RiskOpportunityId") or raw.get("Id") or ""
        )

        # Fallback 1 : le _raw Qualitas utilise "Id" (pas "RiskOpportunityId") pour les
        # risques lus via l'API — verifier les deux cles.
        if not risk_opp_id:
            risk_opp_id = raw.get("Id", "")

        # Fallback 2 : chercher dans le cache Agent1 en testant les fingerprints
        # des 8 dernieres semaines (le fingerprint change chaque semaine).
        if not risk_opp_id:
            intitule_a2  = risque.get("risque", risque.get("Intitule", risque.get("code", "")))
            processus_a2 = risque.get("processus", "")
            risk_opp_id  = (
                _find_guid_in_cache(intitule_a2, processus_a2, "agent1-risk", _cache)
                or _find_guid_in_cache(intitule_a2, processus_a2, "agent1-opp",  _cache)
            )
            if risk_opp_id:
                logger.debug(
                    "[A2-lookup] GUID trouve via cache multi-semaines : %s -> %s",
                    intitule_a2[:60], risk_opp_id[:12]
                )

        if not risk_opp_id:
            # Fallback 3 : interroger l'API QALITAS directement pour trouver le GUID
            # par correspondance sur le code ou l'intitule (cas Excel sans GUID)
            try:
                intitule_search = risque.get("risque", risque.get("Intitule", risque.get("code", "")))
                code_search     = risque.get("code", "")
                all_risks_api   = writer.client.get_risks()
                for r in all_risks_api:
                    api_code = str(r.get("Code", "")).strip()
                    api_name = str(r.get("Designation", r.get("Name", ""))).strip().lower()
                    if (code_search and api_code == code_search) or \
                       (intitule_search and api_name == intitule_search.lower().strip()):
                        risk_opp_id = r.get("Id", r.get("RiskOpportunityId", ""))
                        if risk_opp_id:
                            logger.info(
                                "[A2-lookup] GUID trouve via API directe pour '%s' : %s",
                                intitule_search[:60], risk_opp_id[:12]
                            )
                            break
            except Exception as _e:
                logger.debug("[A2-lookup] Fallback API echoue : %s", _e)

        if not risk_opp_id:
            logger.warning(
                "Risque sans RiskOpportunityId (skipped) : %s — "
                "verifier que les donnees viennent de l'API QALITAS et non de l'Excel seul.",
                risque.get("code", risque.get("risque", risque.get("Intitule", "?")))
            )
            stats["skipped_no_id"] += 1
            continue

        stats["processed"] += 1
        niveau      = risque.get("niveau_residuel", risque.get("niveau", "mineur"))
        rpn_prime   = float(risque.get("score_residuel", risque.get("RPN'", 0)) or 0)
        rpn_brut    = float(risque.get("score_brut",     risque.get("RPN",   0)) or 0)

        # Deduire F' et G' depuis les parametres bruts et l'indice de maitrise
        indice_maitrise = float(risque.get("indice_maitrise", 0.5) or 0.5)
        f_brut  = float(raw.get("Parameter1", 0) or 0)
        g_brut  = float(raw.get("Parameter2", 0) or 0)
        f_prime = round(f_brut * indice_maitrise, 2)
        g_prime = round(g_brut * indice_maitrise, 2)

        # Resoudre le label de decision et l'UUID correspondant
        decision_label        = NIVEAU_TO_DECISION.get(niveau, "Accepter le risque")
        decision_eval_risk_id = _resolve_decision_uuid(decisions_cfg, decision_label)

        # Resoudre le niveau brut a partir du ResultEvalDesignation existant (si disponible)
        # ou a partir du score brut en fallback (non necessaire si deja present dans _raw)
        raw_result_label   = raw.get("ResultEvalDesignation", "")
        result_eval_id     = (
            _resolve_result_uuid(results_cfg, "", nature=0)  # sera vide si label inconnu
            if not raw_result_label
            else next(
                (r.get("Id", "") for r in results_cfg
                 if r.get("Nature", 0) == 0
                 and r.get("Designation", "").lower().strip() == raw_result_label.lower().strip()),
                ""
            )
        )

        # Resoudre le resultat residuel a partir du niveau Agent2
        result_eval_prime_id = _resolve_result_uuid(results_cfg, niveau, nature=0)

        # Apres enrich_evaluations(), recommandation_llm est un champ plat (non imbrique)
        recommandation = (
            risque.get("recommandation_llm", "") or
            risque.get("recommandation", "") or
            ""
        ).strip()
        intitule = risque.get("code", "") + " - " + risque.get("risque", risque.get("Intitule", ""))
        comment  = f"{decision_label}. {recommandation[:400]}" if recommandation else decision_label

        # -----------------------------------------------------------------------
        # Resoudre eval_id et appreciation_row_id via le mapping pre-charge
        # Priorite : mapping (plus precis, contient le row Id) > _raw > latest active
        # -----------------------------------------------------------------------
        appreciation_row_id_pre: str = ""
        mapped_row_pre: Dict = {}

        if risk_opp_id in _risk_to_eval_row:
            mapped_eval_id_pre, mapped_row_pre = _risk_to_eval_row[risk_opp_id]
            if not eval_id:
                eval_id = mapped_eval_id_pre
                logger.debug("[A2] eval_id resolu via mapping : %s pour risque %s",
                             eval_id[:12], risk_opp_id[:12])
            appreciation_row_id_pre = mapped_row_pre.get("Id", "")
        elif not eval_id and _latest_active_eval_id:
            eval_id = _latest_active_eval_id
            logger.debug("[A2] eval_id fallback campagne active : %s", eval_id[:12])

        # Collecter pour l'historique evaluation (nouvelle campagne Agent2)
        _a2_evaluated_risks.append({
            "risk_opp_id":           risk_opp_id,
            "eval_id":               eval_id,
            "appreciation_row_id":   appreciation_row_id_pre,
            "f_prime":               f_prime,
            "g_prime":               g_prime,
            "rpn_prime":             rpn_prime,
            "f_brut":                f_brut,
            "g_brut":                g_brut,
            "rpn_brut":              rpn_brut,
            "indice_maitrise":       indice_maitrise,
            "decision_id":           decision_eval_risk_id,
            "result_id":             result_eval_id,
            "result_prime_id":       result_eval_prime_id,
            "comment":               comment,
            "intitule":              intitule,
            "nature":                int(raw.get("Nature", 0) or 0),
            "mapped_row":            mapped_row_pre,
        })

        # --- 1. Mise a jour de l'appreciation dans la campagne existante ---
        if eval_id:
            ok, _ = writer.save_risk_appreciation(
                evaluation_id=eval_id,
                risk_opportunity_id=risk_opp_id,
                f_prime=f_prime,
                g_prime=g_prime,
                score_prime=rpn_prime,
                decision_eval_risk_id=decision_eval_risk_id,
                result_eval_id=result_eval_id,
                result_eval_prime_id=result_eval_prime_id,
                decision_comments=comment,
                f_brut=f_brut,
                g_brut=g_brut,
                score_brut=rpn_brut,
                indice_maitrise=indice_maitrise,
            )
            if ok:
                stats["appreciations_updated"] += 1
            else:
                stats["errors"] += 1

        # --- 2. Creation d'action corrective si niveau suffisant ---
        niveau_idx = NIVEAUX_ORDRE.index(niveau) if niveau in NIVEAUX_ORDRE else 0
        if niveau_idx >= min_idx:
            echeance_jours = {"critique": 15, "eleve": 30, "moyen": 90}.get(niveau, 90)
            end_date = datetime.now() + timedelta(days=echeance_jours)

            # Titre = premiere phrase de la recommandation (action concrete)
            # Si aucune recommandation disponible, fallback sur decision + processus
            if recommandation:
                premiere_phrase = recommandation.split(".")[0].strip()
                action_title = f"[Agent2] {premiere_phrase[:160]}"
            else:
                action_title = f"[Agent2] {decision_label} - {intitule[:100]}"

            def _clean(v):
                s = str(v).strip() if v is not None else ""
                return "" if s.lower() in ("none", "nan") else s

            causes = _clean(risque.get("causes") or risque.get("Causes"))[:300]
            effets = _clean(risque.get("effets") or risque.get("Effets"))[:300]
            impact = _clean(risque.get("impact_prospectif"))[:300]
            mode   = risque.get("mode_evaluation", risque.get("mode", "?"))

            action_desc = (
                f"Risque source : {intitule}\n"
                f"Processus     : {risque.get('processus', '?')}\n"
                f"Niveau residuel : {niveau} | RPN brut : {rpn_brut} | RPN residuel : {rpn_prime}\n"
                f"Decision retenue : {decision_label}\n"
                f"Mode evaluation  : {mode}\n"
            )
            if causes:
                action_desc += f"Causes         : {causes}\n"
            if effets:
                action_desc += f"Effets negatifs: {effets}\n"
            if impact:
                action_desc += f"Impact prospectif : {impact}\n"
            if recommandation:
                action_desc += f"\nPlan d'action recommande :\n{recommandation[:600]}"

            # --- Verifier le cache avant de creer l'action ---
            processus_a2 = str(risque.get("processus", ""))
            fp = _fingerprint(action_title, processus_a2, "agent2")
            if is_already_injected(fp, _cache):
                logger.info(
                    "[Cache-A2] Action deja injectee cette semaine, ignoree : %s",
                    action_title[:80]
                )
                stats["skipped_cache"] += 1
            else:
                ok2, _ = writer.create_risk_action(
                    risk_opportunity_id=risk_opp_id,
                    title=action_title,
                    description=action_desc,
                    evaluation_id=eval_id,
                    end_date=end_date,
                    process_id=_resolve_processus_id(processus_a2),
                    gravity_id=_resolve_gravity_id(niveau),
                    priority_id=_resolve_priority_id(niveau),
                    action_type_id=TYPE_PREVENTIVE_ID,
                    root_cause=causes,
                )
                if ok2:
                    stats["actions_created"] += 1
                    if not writer.dry_run:
                        mark_as_injected(fp, action_title[:80], _cache, agent="agent2")
                else:
                    stats["errors"] += 1

    # Persister le cache apres tout le batch (mode reel uniquement)
    if not writer.dry_run:
        _save_cache(_cache)

    # -----------------------------------------------------------------------
    # HISTORIQUE D'EVALUATION : mise a jour des appreciations dans les
    # campagnes EXISTANTES (EditRiskAppreciation avec le bon row Id).
    #
    # IMPORTANT : on N'utilise PAS une nouvelle campagne vide car QALITAS
    # rejette EditRiskAppreciation avec Id="" ("false|Impossible de modifier").
    # On utilise le eval_id et l'appreciation_row_id pre-charges depuis le
    # mapping construit au debut de cette fonction.
    # -----------------------------------------------------------------------
    if _a2_evaluated_risks and not writer.dry_run:
        for item in _a2_evaluated_risks:
            item_eval_id = item.get("eval_id", "")
            item_row_id  = item.get("appreciation_row_id", "")

            if not item_eval_id:
                logger.warning(
                    "[Hist-A2] Pas de campagne connue pour '%s' — risque non inclus dans l'historique.",
                    item["intitule"][:60]
                )
                stats["hist_eval_errors"] += 1
                continue

            if not item_row_id:
                logger.warning(
                    "[Hist-A2] appreciation_row_id absent pour '%s' dans campagne %s "
                    "— EditRiskAppreciation impossible (QALITAS exige le Id de la ligne).",
                    item["intitule"][:60], item_eval_id[:12]
                )
                stats["hist_eval_errors"] += 1
                continue

            ok_appr, resp_appr = writer.save_risk_appreciation(
                evaluation_id=item_eval_id,
                risk_opportunity_id=item["risk_opp_id"],
                f_prime=item["f_prime"],
                g_prime=item["g_prime"],
                score_prime=item["rpn_prime"],
                f_brut=item["f_brut"],
                g_brut=item["g_brut"],
                score_brut=item["rpn_brut"],
                decision_eval_risk_id=item["decision_id"],
                result_eval_id=item["result_id"],
                result_eval_prime_id=item["result_prime_id"],
                decision_comments=item["comment"],
                indice_maitrise=item["indice_maitrise"],
            )
            if ok_appr:
                stats["hist_eval_created"] += 1
                logger.info(
                    "[Hist-A2] OK : %s (eval=%s F'=%.2f G'=%.2f RPN'=%.1f)",
                    item["intitule"][:60], item_eval_id[:12],
                    item["f_prime"], item["g_prime"], item["rpn_prime"],
                )
            else:
                stats["hist_eval_errors"] += 1
                logger.warning(
                    "[Hist-A2] Echec EditRiskAppreciation pour '%s' dans campagne %s "
                    "(row_id=%s) — reponse : %s",
                    item["intitule"][:60], item_eval_id[:12],
                    item_row_id[:12], str(resp_appr)[:120]
                )

    elif _a2_evaluated_risks and writer.dry_run:
        logger.info(
            "[DRY-RUN] Historique Agent2 simule : %d risques seraient mis a jour "
            "(dont %d avec row_id connu)",
            len(_a2_evaluated_risks),
            sum(1 for i in _a2_evaluated_risks if i.get("appreciation_row_id"))
        )

    logger.info(
        "Injection Agent2 : %d traites | %d appr. maj | %d actions | "
        "%d hist.eval OK | %d cache | %d sans ID | %d erreurs",
        stats["processed"], stats["appreciations_updated"], stats["actions_created"],
        stats["hist_eval_created"], stats["skipped_cache"],
        stats["skipped_no_id"], stats["errors"],
    )
    return stats


# =============================================================================
# INJECTION AGENT 1 -> QALITAS
# =============================================================================

def inject_agent1_results(
    register_entries: List[Dict],
    writer: QalitasWriter,
    only_new: bool = True,
) -> Dict[str, Any]:
    """
    Injecte les R&O identifies par Agent1 vers la section Risques/Opportunites de QALITAS.

    Logique d'injection :
        1. Filtre les entrees issues des fiches clients (source != "QALITAS_API")
           pour ne pas creer de doublons avec le registre officiel deja present.
        2. Pour chaque R&O :
           a. Resout le ProcessId QALITAS depuis le nom de processus
           b. Mappe les indicateurs Q/S/E depuis le domaine
           c. Appelle QalitasWriter.create_risk_opportunity()
        3. Cache anti-doublon (fingerprint = intitule + processus)

    Parametres :
        register_entries : liste produite par agent1_identification.run_agent1()
                           (chaque entree a au minimum : type, code, intitule,
                            source, perimetre/processus, domaine, causes, consequences)
        writer           : instance QalitasWriter
        only_new         : si True (defaut), n'injecte que les entrees non-QALITAS_API

    Retourne un dict de statistiques.
    """
    stats = {
        "processed":       0,
        "created_risques": 0,
        "created_opps":    0,
        "skipped_qalitas": 0,
        "skipped_cache":   0,
        "errors":          0,
        "eval_created":    0,
        "eval_errors":     0,
    }
    # Collecte des GUIDs crees pour alimentation de l'historique evaluation
    created_risk_guids: List[Dict] = []  # [{guid, nature, gravite, intitule}]

    # Mapping domaine -> indicateurs systeme Q/S/E
    DOMAINE_QSE: Dict[str, Dict[str, bool]] = {
        "Q":           {"q": True,  "s": False, "e": False},
        "S":           {"q": False, "s": True,  "e": False},
        "SST":         {"q": False, "s": True,  "e": False},
        "E":           {"q": False, "s": False, "e": True},
        "Environnement": {"q": False, "s": False, "e": True},
        "Performance": {"q": True,  "s": False, "e": False},
        "Conformite":  {"q": True,  "s": True,  "e": True},
        "Image":       {"q": True,  "s": False, "e": False},
    }

    _cache = _load_cache()

    for entry in register_entries:
        source = entry.get("source", "")

        # Filtrer les R&O deja dans QALITAS (eviter les doublons)
        if only_new and source == "QALITAS_API":
            stats["skipped_qalitas"] += 1
            continue

        stats["processed"] += 1

        intitule     = entry.get("intitule", entry.get("risque", ""))
        type_ro      = str(entry.get("type", "risque")).lower()
        nature       = 1 if type_ro in ("opportunite", "opportunité", "opportunity", "opp") else 0
        processus    = (
            entry.get("perimetre")
            or entry.get("processus")
            or entry.get("domaine_processus")
            or ""
        )
        domaine      = str(entry.get("domaine", "Q"))
        causes       = str(entry.get("causes", ""))
        consequences = str(entry.get("consequences", entry.get("effets", "")))
        description  = str(entry.get("justification", entry.get("description", intitule)))
        responsable  = str(entry.get("responsable", ""))
        gravity_code  = str(entry.get("gravity_code", entry.get("gravite_code", "")))
        type_code     = str(entry.get("type_risque", entry.get("type_code", entry.get("categorie_type", ""))))
        category_code = str(entry.get("categorie", entry.get("category_code", entry.get("interne_externe", ""))))

        # Mapping domaine -> systemes Q/S/E
        qse = DOMAINE_QSE.get(domaine, {"q": True, "s": False, "e": False})

        # Resoudre ProcessId QALITAS
        process_id = _resolve_processus_id(processus) if processus else ""

        # Fingerprint pour cache
        fp = _fingerprint(intitule, processus, f"agent1-{'opp' if nature else 'risk'}")
        if is_already_injected(fp, _cache):
            logger.info(
                "[Cache-A1] R&O deja injecte, ignore : %s", intitule[:80]
            )
            stats["skipped_cache"] += 1
            continue

        ok, resp = writer.create_risk_opportunity(
            designation=intitule[:200],
            description=description,
            nature=nature,
            process_id=process_id,
            system_q=qse["q"],
            system_s=qse["s"],
            system_e=qse["e"],
            gravity_code=gravity_code,
            responsable=responsable,
            causes=causes,
            consequences=consequences,
            type_code=type_code,
            category_code=category_code,
            state=1,  # Identifie : visible dans la liste QALITAS (state=0 Brouillon masque les R&O)
        )

        if ok:
            if nature == 1:
                stats["created_opps"] += 1
            else:
                stats["created_risques"] += 1
            # Ne marquer dans le cache qu'en mode reel (pas en dry-run)
            if not writer.dry_run:
                # Extraire le GUID QALITAS depuis la reponse "true|{GUID}|..."
                qalitas_id = ""
                if isinstance(resp, str) and resp.startswith("true|"):
                    parts = resp.split("|")
                    if len(parts) >= 2:
                        qalitas_id = parts[1].strip()
                if qalitas_id:
                    # Stocker le GUID pour alimenter l'historique evaluation ensuite
                    gravite_raw = entry.get("gravite", entry.get("gravity_code", ""))
                    try:
                        g_val = float(re.sub(r"[^\d.]", "", str(gravite_raw))) if gravite_raw else 2.0
                    except ValueError:
                        g_val = 2.0
                    created_risk_guids.append({
                        "guid":     qalitas_id,
                        "nature":   nature,
                        "gravite":  g_val,
                        "intitule": intitule[:80],
                    })
                mark_as_injected(fp, intitule[:80], _cache, agent="agent1", qalitas_id=qalitas_id)
        else:
            stats["errors"] += 1

    if not writer.dry_run:
        _save_cache(_cache)

    # -----------------------------------------------------------------------
    # Apres creation des R&O : creer une campagne d'evaluation "Agent IA"
    # et y enregistrer une appreciation initiale pour chaque risque cree.
    # Cela alimente l'onglet "Historique Evaluation" visible dans QALITAS.
    # -----------------------------------------------------------------------
    if created_risk_guids and not writer.dry_run:
        today = datetime.now()
        eval_designation = f"Evaluation IA Agent1 — {today.strftime('%d/%m/%Y %H:%M')}"

        ok_eval, eval_id = writer.create_evaluation_campaign(
            designation=eval_designation,
            formula="F*G",
            start_date=today,
            end_date=today,
            min_score=1.0,
            max_score=25.0,
        )

        if ok_eval and eval_id:
            logger.info(
                "Campagne evaluation IA creee : %s (%s) — ajout de %d risques",
                eval_designation, eval_id, len(created_risk_guids)
            )
            for item in created_risk_guids:
                # Score initial = F=1, G=gravite (1-5), RPN = 1*G
                g = max(1.0, min(5.0, item["gravite"]))
                rpn = round(1.0 * g, 2)
                ok_appr, _ = writer.save_risk_appreciation(
                    evaluation_id=eval_id,
                    risk_opportunity_id=item["guid"],
                    f_prime=1.0,
                    g_prime=g,
                    score_prime=rpn,
                    f_brut=1.0,
                    g_brut=g,
                    score_brut=rpn,
                    decision_comments=(
                        f"Evaluation initiale automatique — Agent IA 1. "
                        f"Risque identifie : {item['intitule']}"
                    ),
                )
                if ok_appr:
                    stats["eval_created"] += 1
                    logger.info(
                        "[Eval-A1] Appreciation enregistree : %s (G=%.1f, RPN=%.1f)",
                        item["intitule"], g, rpn
                    )
                else:
                    stats["eval_errors"] += 1
                    logger.warning(
                        "[Eval-A1] Echec appreciation : %s dans campagne %s",
                        item["intitule"], eval_id
                    )
        else:
            logger.warning(
                "Impossible de creer la campagne d'evaluation IA "
                "— historique evaluation non alimente pour %d risques",
                len(created_risk_guids)
            )
            stats["eval_errors"] += len(created_risk_guids)

    logger.info(
        "Injection Agent1 : %d traites | %d risques crees | %d opportunites crees | "
        "%d cache | %d deja dans QALITAS | %d erreurs | "
        "%d appreciations eval creees | %d eval_erreurs",
        stats["processed"], stats["created_risques"], stats["created_opps"],
        stats["skipped_cache"], stats["skipped_qalitas"], stats["errors"],
        stats["eval_created"], stats["eval_errors"],
    )
    return stats


# =============================================================================
# INJECTION AGENT 4 -> QALITAS
# =============================================================================

def inject_agent4_results(
    alerts: List[Dict],
    writer: QalitasWriter,
    min_criticite: str = "SURVEILLANCE",
) -> Dict[str, Any]:
    """
    Injecte les alertes de l'Agent4 vers QALITAS sous forme d'actions correctives.

    Chaque alerte Agent4 est convertie en action liee au risque source si possible,
    ou en action generique sinon.

    Niveaux Agent4 :
        ALERTE       -> action priorite haute, echeance 15j
        SURVEILLANCE -> action priorite moyenne, echeance 30j
        STABLE       -> ignore (en dessous du seuil)

    alerts        : liste d'alertes produites par Agent4
    writer        : instance QalitasWriter
    min_criticite : seuil minimum ("STABLE" | "SURVEILLANCE" | "ALERTE")
    """
    CRITICITES_ORDRE = ["STABLE", "SURVEILLANCE", "ALERTE"]
    min_idx = CRITICITES_ORDRE.index(min_criticite) if min_criticite in CRITICITES_ORDRE else 1

    stats = {
        "processed":      0,
        "actions_created": 0,
        "skipped":        0,
        "skipped_cache":  0,
        "errors":         0,
    }

    # Mapping criticality_level Agent4 -> niveau injection
    CRITICALITY_MAP = {
        "critique":     "ALERTE",
        "elevee":       "ALERTE",
        "significatif": "SURVEILLANCE",
        "moyenne":      "SURVEILLANCE",
        "faible":       "STABLE",
        "stable":       "STABLE",
    }

    # Charger le cache une seule fois pour tout le batch
    _cache = _load_cache()

    for alert in alerts:
        stats["processed"] += 1

        # Lire criticality_level (champ reel Agent4) avec fallback anciens champs
        crit_raw  = str(
            alert.get("criticality_level") or
            alert.get("gravite_final") or
            alert.get("gravite_regle") or
            alert.get("niveau") or
            alert.get("criticite") or
            "faible"
        ).lower()
        criticite = CRITICALITY_MAP.get(crit_raw, "SURVEILLANCE")
        crit_idx  = CRITICITES_ORDRE.index(criticite)

        if crit_idx < min_idx:
            stats["skipped"] += 1
            continue

        echeance_jours = 15 if criticite == "ALERTE" else 30
        end_date       = datetime.now() + timedelta(days=echeance_jours)

        processus   = alert.get("process_name") or alert.get("processus") or alert.get("Processus") or "Processus non precise"
        type_risque = alert.get("type_risque_final") or alert.get("type_risque_regle") or "Alerte"
        details     = alert.get("resume_final") or alert.get("snippet") or alert.get("details") or ""
        recomm      = (
            alert.get("recommandation_final") or
            alert.get("recommandation_regle") or
            alert.get("recommandation") or
            "Traitement requis."
        )
        source_doc  = alert.get("evidence_source") or alert.get("source") or ""
        risk_opp_id = alert.get("risk_opportunity_id") or alert.get("RiskOpportunityId") or ""

        action_title = f"[Agent4-{criticite}] {processus} : {type_risque[:80]}"

        # --- Verifier le cache avant de creer l'action ---
        fp = _fingerprint(action_title, processus, "agent4")
        if is_already_injected(fp, _cache):
            logger.info(
                "[Cache-A4] Action deja injectee cette semaine, ignoree : %s",
                action_title[:80]
            )
            stats["skipped_cache"] += 1
            continue

        action_desc  = (
            f"Alerte detectee automatiquement par Agent4 QALITAS.\n"
            f"Processus : {processus}\n"
            f"Source : {source_doc}\n"
            f"Niveau criticite : {criticite} ({crit_raw})\n"
            f"Detail : {details[:400]}\n"
            f"Recommandation : {recomm[:400]}"
        )

        # Si un GUID risque est disponible, lier l'action au risque (source="11")
        # Sinon, action generique sans lien (source="4" -> "Sans source" dans UI)
        effective_source = ACTION_SOURCE_RISQUE if risk_opp_id else ACTION_SOURCE_AGENT4

        ok, _ = writer.create_risk_action(
            risk_opportunity_id=risk_opp_id,
            title=action_title,
            description=action_desc,
            action_source=effective_source,
            end_date=end_date,
            process_id=_resolve_processus_id(processus),
            gravity_id=_resolve_gravity_id(crit_raw),
            priority_id=_resolve_priority_id(criticite.lower()),
            action_type_id=TYPE_PREVENTIVE_ID,
        )

        if ok:
            stats["actions_created"] += 1
            if not writer.dry_run:
                mark_as_injected(fp, action_title[:80], _cache, agent="agent4")
        else:
            stats["errors"] += 1

    # Persister le cache apres tout le batch (mode reel uniquement)
    if not writer.dry_run:
        _save_cache(_cache)

    logger.info(
        "Injection Agent4 : %d alertes | %d actions creees | "
        "%d cache | %d niveau-insuffisant | %d erreurs",
        stats["processed"], stats["actions_created"],
        stats["skipped_cache"], stats["skipped"], stats["errors"]
    )
    return stats


# =============================================================================
# POINT D'ENTREE : injection complete depuis un rapport JSON
# =============================================================================

def inject_from_report(
    report_json_path: str,
    base_url: str = DEFAULT_BASE_URL,
    username: str = "",
    password: str = "",
    dry_run: bool = True,
    min_niveau_agent2: str = "moyen",
    min_criticite_agent4: str = "SURVEILLANCE",
    inject_agent1: bool = True,
) -> Dict[str, Any]:
    """
    Charge le rapport JSON produit par qalitas_main.py et injecte les resultats
    vers QALITAS dans l'ordre correct du pipeline :

        Agent 1 -> creation des nouveaux R&O (Risques/Opportunites dans QALITAS)
        Agent 2 -> mise a jour des scores residuels (EditRiskAppreciation)
        Agent 4 -> creation d'actions correctives depuis les alertes monitoring

    report_json_path : chemin vers output/qalitas_latest.json
    dry_run          : True = simulation sans ecriture (defaut)
                       False = injection reelle dans QALITAS
    inject_agent1    : si True (defaut), injecte les R&O nouveaux avant Agent2
    """
    if not username:
        username = os.environ.get("QALITAS_USERNAME", "MOHAMED.N")
    if not password:
        password = os.environ.get("QALITAS_PASSWORD", "MOHAMED.N")

    if not username or not password:
        logger.error("Credentials QALITAS manquants (QALITAS_USERNAME / QALITAS_PASSWORD).")
        return {"error": "Credentials manquants"}

    try:
        with open(report_json_path, "r", encoding="utf-8") as f:
            report = json.load(f)
    except Exception as e:
        logger.error("Impossible de lire le rapport : %s", e)
        return {"error": str(e)}

    register_agent1 = report.get("agent1", {}).get("register", [])
    risques_agent2  = report.get("agent2", {}).get("risques", [])
    alerts_agent4   = report.get("agent4", {}).get("alertes", [])

    logger.info(
        "Injection depuis rapport : %d R&O Agent1 | %d risques Agent2 | "
        "%d alertes Agent4 | dry_run=%s",
        len(register_agent1), len(risques_agent2), len(alerts_agent4), dry_run
    )

    client = QalitasClient(base_url=base_url, username=username, password=password)
    if not client.login():
        return {"error": "Echec authentification QALITAS"}

    writer = QalitasWriter(client=client, dry_run=dry_run)
    stats_a1: Dict = {}

    try:
        # Ordre imperatif : Agent1 d'abord (creation R&O), puis Agent2 (evaluation),
        # puis Agent4 (actions correctives depuis alertes monitoring)
        if inject_agent1 and register_agent1:
            stats_a1 = inject_agent1_results(register_agent1, writer, only_new=True)

        stats_a2 = inject_agent2_results(risques_agent2, writer, min_niveau=min_niveau_agent2)
        stats_a4 = inject_agent4_results(alerts_agent4, writer, min_criticite=min_criticite_agent4)
    finally:
        client.logout()

    report_ecriture = writer.get_write_report()

    # Sauvegarde du rapport d'injection
    output_dir = os.path.dirname(report_json_path)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    injection_report_path = os.path.join(output_dir, f"injection_report_{ts}.json")
    try:
        with open(injection_report_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp":    ts,
                    "dry_run":      dry_run,
                    "source":       report_json_path,
                    "agent1_stats": stats_a1,
                    "agent2_stats": stats_a2,
                    "agent4_stats": stats_a4,
                    "write_report": report_ecriture,
                },
                f, ensure_ascii=False, indent=2, default=str,
            )
        logger.info("Rapport d'injection sauvegarde : %s", injection_report_path)
    except Exception as e:
        logger.warning("Impossible de sauvegarder le rapport d'injection : %s", e)

    return {
        "dry_run":      dry_run,
        "agent1_stats": stats_a1,
        "agent2_stats": stats_a2,
        "agent4_stats": stats_a4,
        "write_report": report_ecriture,
    }


# =============================================================================
# TEST EN LIGNE DE COMMANDE
# =============================================================================

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    report_path = sys.argv[1] if len(sys.argv) > 1 else "output/qalitas_latest.json"
    mode        = sys.argv[2] if len(sys.argv) > 2 else "dry_run"
    dry_run     = (mode != "inject")

    if dry_run:
        print("\n[MODE SIMULATION] Aucune donnee ne sera ecrite dans QALITAS.")
        print("Passer 'inject' en 2e argument pour ecriture reelle.\n")
    else:
        print("\n[MODE INJECTION REELLE] Les donnees seront ecrites dans QALITAS.\n")

    result = inject_from_report(
        report_json_path=report_path,
        dry_run=dry_run,
        min_niveau_agent2="moyen",
        min_criticite_agent4="SURVEILLANCE",
    )

    print("\n=== RAPPORT D'INJECTION ===")
    print(f"Mode        : {'DRY-RUN (simulation)' if dry_run else 'INJECTION REELLE'}")
    print(f"Agent2      : {result.get('agent2_stats', {})}")
    print(f"Agent4      : {result.get('agent4_stats', {})}")
    wr = result.get("write_report", {})
    print(f"Operations  : {wr.get('total', 0)} total | {wr.get('success', 0)} OK | {wr.get('failed', 0)} echecs")
