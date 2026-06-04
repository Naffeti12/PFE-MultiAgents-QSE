"""
Connecteur API QALITAS QSE
==========================
Client HTTP pour la plateforme QALITAS QSE (ASP.NET MVC, auth par cookie de session).

Architecture :
    QalitasClient       : gestion session, auth, requetes HTTP
    normalize_*         : remapping champs API -> noms colonnes Excel attendus par les agents
    load_all_context_api: produit le meme contexte que load_excel_data.load_all_context()

Endpoints decouverts (base : /QalitasDemo/) :
    Auth         POST  Account/Login
    Risques      GET   RiskOpportunity/GetEnabledRisks?sourceId=
    Opportunites GET   RiskOpportunity/GetEnabledOpportunities?sourceId=
    Carto/Eval   GET   RiskOpportunityEvaluation/GetAllRiskAppreciation?nature=0&employeeId=
    NC           GET   NonConformity/GetNonConformities
    KPIs         GET   Indicator/GetEnabledIndicator
    Reclamations GET   CustomerComplaint/GetComplaints
    Chart NC     GET   Home/GetChartNonConformity
    Chart Actions GET  Home/GetChartAction
    Chart Audit  GET   Home/GetChartAudit
    Chart Client GET   Home/GetChartCustomer
    Conf Decision GET  Configuration/GetDecisionEvaluationRisk
    Conf Resultat GET  Configuration/GetResultEvaluationRiskOpp
    Evaluations   GET  RiskOpportunityEvaluation/GetRiskEvaluations?projectId=
    Appreciation  GET  RiskOpportunityEvaluation/GetRiskAppreciation?evaluationId=
    Edit Apprec.  POST RiskOpportunityEvaluation/EditRiskAppreciation  (confirme 02/04/2026)
"""

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
import urllib3
from requests import Session

# Charger automatiquement le fichier .env (QALITAS_BASE_URL, USERNAME, PASSWORD)
# Sans cette ligne, os.environ.get() utilise les variables systeme uniquement.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(
        dotenv_path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        override=False,  # ne pas ecraser les variables deja definies dans l'env systeme
    )
except ImportError:
    pass  # python-dotenv non installe : les variables doivent etre definies manuellement

# Supprimer les warnings SSL : le serveur Azure utilise un certificat auto-signe.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("qalitas_api_client")

# =============================================================================
# CONFIGURATION PAR DEFAUT (surcharger via env ou constructeur)
# =============================================================================

DEFAULT_BASE_URL = os.environ.get(
    "QALITAS_BASE_URL",
    "https://timserver.northeurope.cloudapp.azure.com/QalitasDemo"
)
DEFAULT_USERNAME = os.environ.get("QALITAS_USERNAME", "MOHAMED.N")
DEFAULT_PASSWORD = os.environ.get("QALITAS_PASSWORD", "MOHAMED.N")
DEFAULT_GROUP_ID   = os.environ.get("QALITAS_GROUP_ID",   "39d00cd5-3176-1658-7979-c26095e6ec30")  # TIM
DEFAULT_COMPANY_ID = os.environ.get("QALITAS_COMPANY_ID", "39d00cd5-3251-9b25-bca0-bf46aa71c52b")  # Tim
DEFAULT_SITE_ID    = os.environ.get("QALITAS_SITE_ID",    "bbb2b648-82bf-471d-bbf4-6ab8e68b6d0b")  # Paris

# Timeout HTTP en secondes
# Augmente a 60s : le serveur Azure repond en <1s mais urllib3 sur certains
# reseaux Windows (proxy, antivirus SSL) peut bloquer la negociation TLS.
REQUEST_TIMEOUT = 60

# Date de debut par defaut pour les requetes avec filtre date (12 derniers mois)
DEFAULT_MONTHS_BACK = 12


# =============================================================================
# CLIENT HTTP
# =============================================================================

class QalitasClient:
    """
    Client HTTP avec gestion de session cookie ASP.NET.

    Usage :
        client = QalitasClient(base_url, username, password)
        client.login()
        risks = client.get_risks()
        client.logout()

    Ou en context manager :
        with QalitasClient(...) as c:
            risks = c.get_risks()
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        username: str = DEFAULT_USERNAME,
        password: str = DEFAULT_PASSWORD,
        timeout: int = REQUEST_TIMEOUT,
        group_id: str = DEFAULT_GROUP_ID,
        company_id: str = DEFAULT_COMPANY_ID,
        site_id: str = DEFAULT_SITE_ID,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.group_id = group_id
        self.company_id = company_id
        self.site_id = site_id
        self.timeout = timeout
        self._session: Session = requests.Session()
        # trust_env=False : ignore les variables proxy Windows (HTTP_PROXY, HTTPS_PROXY)
        # qui peuvent bloquer ou ralentir la connexion au serveur Azure.
        self._session.trust_env = False
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        })
        self._authenticated = False

    # -------------------------------------------------------------------------
    # Auth
    # -------------------------------------------------------------------------

    def login(self) -> bool:
        """
        Authentification par formulaire POST (ASP.NET MVC).
        Recupere le token CSRF __RequestVerificationToken avant le POST.
        """
        login_url = f"{self.base_url}/"
        # Headers navigateur : sans X-Requested-With pour que le serveur ASP.NET
        # traite la requete comme un formulaire browser et cree un cookie de session web
        browser_headers = {
            "X-Requested-With": None,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            # 1. GET la page de login pour recuperer le token CSRF
            resp = self._session.get(login_url, timeout=self.timeout, verify=False,
                                     headers=browser_headers)
            resp.raise_for_status()

            # Extraire le token CSRF du formulaire
            token = self._extract_csrf_token(resp.text)

            # 2. POST les credentials avec GroupId, CompanyId, SiteId
            payload = {
                "UserName": self.username,
                "Password": self.password,
                "GroupId": self.group_id,
                "CompanyId": self.company_id,
                "SiteId": self.site_id,
                "RememberMe": "false",
            }
            if token:
                payload["__RequestVerificationToken"] = token

            resp2 = self._session.post(
                login_url,
                data=payload,
                timeout=self.timeout,
                verify=False,
                allow_redirects=True,
                headers=browser_headers,
            )

            # Succes si on est redirige vers le dashboard (pas la page login)
            if "Dashboard" in resp2.url or "Home" in resp2.url:
                self._authenticated = True
                logger.info("Authentification QALITAS reussie (%s)", self.username)
                return True

            # Verifier que la reponse n'est pas la page de login (echec credentials)
            if "<title>QALITAS | Login</title>" in resp2.text:
                logger.error("Echec authentification QALITAS : credentials ou CompanyId/SiteId invalides")
                return False

            # Verifier si un cookie d'authentification ASP.NET existe (hors session seule)
            auth_cookies = [c for c in self._session.cookies
                            if c.name.startswith(".ASPX") or c.name.startswith(".AspNet")]
            if auth_cookies:
                self._authenticated = True
                logger.info("Session QALITAS etablie (%s)", self.username)
                return True

            # Derniere chance : tenter un appel API et verifier la reponse JSON
            probe = self._session.get(
                f"{self.base_url}/Indicator/GetEnabledIndicator",
                timeout=self.timeout, verify=False
            )
            if probe.headers.get("Content-Type", "").startswith("application/json"):
                self._authenticated = True
                logger.info("Session QALITAS validee par sondage API (%s)", self.username)
                return True

            logger.error("Echec authentification QALITAS (redirection inattendue: %s)", resp2.url)
            return False

        except Exception as exc:
            logger.error("Erreur login QALITAS: %s", exc)
            return False

    def logout(self) -> None:
        """Ferme la session."""
        try:
            self._session.get(
                f"{self.base_url}/Account/LogOff",
                timeout=self.timeout,
                verify=False
            )
        except Exception:
            pass
        self._session.close()
        self._authenticated = False

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *args):
        self.logout()

    # -------------------------------------------------------------------------
    # Requetes generiques
    # -------------------------------------------------------------------------

    def _get(self, path: str, params: Optional[Dict] = None) -> Any:
        """
        GET sur un endpoint. Retourne le JSON parse ou une liste vide si erreur.
        """
        if not self._authenticated:
            logger.warning("Appel API sans authentification prealable : %s", path)
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = self._session.get(
                url, params=params, timeout=self.timeout, verify=False
            )
            resp.raise_for_status()
            if not resp.text.strip():
                return []
            return resp.json()
        except requests.exceptions.JSONDecodeError:
            logger.warning("Reponse non-JSON pour %s", path)
            return []
        except Exception as exc:
            logger.error("Erreur GET %s: %s", path, exc)
            return []

    @staticmethod
    def _extract_csrf_token(html: str) -> Optional[str]:
        """Extrait le token CSRF du HTML du formulaire de login."""
        import re
        match = re.search(
            r'name="__RequestVerificationToken"[^>]*value="([^"]+)"',
            html
        )
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _date_str(dt: datetime) -> str:
        """Formate une date pour les parametres API (format MM/dd/yyyy)."""
        return dt.strftime("%m/%d/%Y")

    # -------------------------------------------------------------------------
    # Endpoints de donnees
    # -------------------------------------------------------------------------

    def get_risks(self, source_id: str = "") -> List[Dict]:
        """
        Retourne la liste des risques actifs.
        GET /RiskOpportunity/GetEnabledRisks?sourceId=
        """
        data = self._get("RiskOpportunity/GetEnabledRisks", params={"sourceId": source_id})
        return data if isinstance(data, list) else []

    def get_opportunities(self, source_id: str = "") -> List[Dict]:
        """
        Retourne la liste des opportunites actives.
        GET /RiskOpportunity/GetEnabledOpportunities?sourceId=
        """
        data = self._get("RiskOpportunity/GetEnabledOpportunities", params={"sourceId": source_id})
        return data if isinstance(data, list) else []

    def get_risk_mapping(self, nature: int = 0, employee_id: str = "") -> List[Dict]:
        """
        Retourne la cartographie d'evaluation des risques.
        GET /RiskOpportunityEvaluation/GetAllRiskAppreciation?nature=0&employeeId=
        nature=0 : risques, nature=1 : opportunites
        """
        data = self._get(
            "RiskOpportunityEvaluation/GetAllRiskAppreciation",
            params={"nature": nature, "employeeId": employee_id}
        )
        return data if isinstance(data, list) else []

    def get_non_conformities(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Dict]:
        """
        Retourne les non-conformites.
        GET /NonConformity/GetNonConformities
        """
        params: Dict[str, Any] = {}
        if start_date:
            params["startDate"] = self._date_str(start_date)
        if end_date:
            params["endDate"] = self._date_str(end_date)
        data = self._get("NonConformity/GetNonConformities", params=params or None)
        return data if isinstance(data, list) else []

    def get_indicators(self) -> List[Dict]:
        """
        Retourne les indicateurs (KPIs) actifs.
        GET /Indicator/GetEnabledIndicator
        """
        data = self._get("Indicator/GetEnabledIndicator")
        return data if isinstance(data, list) else []

    def get_complaints(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Dict]:
        """
        Retourne les reclamations clients.
        GET /CustomerComplaint/GetComplaints
        """
        params: Dict[str, Any] = {}
        if start_date:
            params["startDate"] = self._date_str(start_date)
        if end_date:
            params["endDate"] = self._date_str(end_date)
        data = self._get("CustomerComplaint/GetComplaints", params=params or None)
        return data if isinstance(data, list) else []

    def get_dashboard_chart_nc(self) -> Dict:
        """Donnees du graphique NC du tableau de bord."""
        result = self._get("Home/GetChartNonConformity")
        return result if isinstance(result, dict) else {}

    def get_dashboard_chart_actions(self) -> Dict:
        """Donnees du graphique Actions du tableau de bord."""
        result = self._get("Home/GetChartAction")
        return result if isinstance(result, dict) else {}

    def get_dashboard_chart_audit(self) -> Dict:
        """Donnees du graphique Audits du tableau de bord."""
        result = self._get("Home/GetChartAudit")
        return result if isinstance(result, dict) else {}

    def get_dashboard_chart_customer(self) -> Dict:
        """Donnees du graphique Reclamations clients du tableau de bord."""
        result = self._get("Home/GetChartCustomer")
        return result if isinstance(result, dict) else {}

    # -------------------------------------------------------------------------
    # Configuration : tables de reference pour les evaluations
    # -------------------------------------------------------------------------

    def get_decision_eval_risks(self) -> List[Dict]:
        """
        Retourne la liste des decisions d'evaluation de risques/opportunites.
        GET /Configuration/GetDecisionEvaluationRisk

        Champs utiles :
            Id          : UUID de la decision (a passer dans DecisionEvalRiskId)
            Designation : label (ex: "Accepter le risque", "Eliminer la source")

        Endpoint confirme par interception navigateur (01/04/2026).
        17 decisions configurees dans QALITAS Demo.
        """
        data = self._get("Configuration/GetDecisionEvaluationRisk")
        return data if isinstance(data, list) else []

    def get_result_eval_risk_opps(self, nature: Optional[int] = None) -> List[Dict]:
        """
        Retourne la liste des resultats d'evaluation risques/opportunites.
        GET /Configuration/GetResultEvaluationRiskOpp

        Champs utiles :
            Id          : UUID du resultat (a passer dans ResultEvalId / ResultEvalPrimeId)
            Designation : label (ex: "Mineur", "Moyen", "Critique")
            Nature      : 0 = risque, 1 = opportunite

        Endpoint confirme par interception navigateur (01/04/2026).
        Exemples :
            Mineur   (Nature=0) -> 50dcadd9-174b-c862-0492-3a0e92059484
            Moyen    (Nature=0) -> bb3f3020-bd5d-b35d-71cd-3a0e938db808
            Critique (Nature=0) -> 61c1c5b5-e77c-dc48-6422-3a0e938db8b5
            Mineure  (Nature=1) -> 4c413fe2-d114-6f61-086f-3a0e9392d793
            Moyenne  (Nature=1) -> d9dec0d1-2d2f-8886-2e83-3a0e9392d82f
            Majeure  (Nature=1) -> b30595dc-7a0f-a889-3adf-3a0e9392d8cc

        nature : si precise, filtre par nature (0=risques, 1=opportunites)
        """
        data = self._get("Configuration/GetResultEvaluationRiskOpp")
        if not isinstance(data, list):
            return []
        if nature is not None:
            return [r for r in data if r.get("Nature") == nature]
        return data

    def get_evaluations(self, project_id: str = "") -> List[Dict]:
        """
        Retourne la liste des campagnes d'evaluation de risques.
        GET /RiskOpportunityEvaluation/GetRiskEvaluations?projectId=

        Champs utiles :
            Id          : UUID de la campagne (a passer dans evaluationId)
            Reference   : code humain (ex: "EVR/05/2023")
            Designation : intitule de la campagne
            State       : etat (0=Brouillon, 1=En cours, 2=Realise)
            Formula     : formule RPN utilisee (ex: "F*G")
        """
        data = self._get(f"RiskOpportunityEvaluation/GetRiskEvaluations?projectId={project_id}")
        return data if isinstance(data, list) else []

    def get_risk_appreciation(self, evaluation_id: str) -> List[Dict]:
        """
        Retourne les lignes d'appreciation pour une campagne d'evaluation.
        GET /RiskOpportunityEvaluation/GetRiskAppreciation?evaluationId=...

        Chaque ligne correspond a un risque evalue dans cette campagne.

        Champs utiles :
            Id                       : UUID propre de la ligne appreciation
                                       (OBLIGATOIRE pour EditRiskAppreciation)
            RiskOpportunityId        : UUID du risque source
            RiskOpportunityCode      : code lisible (ex: "RIS-0003")
            RiskOpportunityEvaluationId : UUID de la campagne (= evaluation_id)
            RiskOpportunityNature    : 0=risque, 1=opportunite
            EvaluationState          : entier (0=Brouillon, 1=En cours, 2=Realise)
            EvaluationFormula        : formule (ex: "F*G")
            Parameter1..10           : valeurs des facteurs (F, G, M...)
            Score                    : RPN brut (calcul automatique)
            ParameterPrime1..10      : facteurs post-maitrise
            ScorePrime               : RPN residuel
            DecisionEvalRiskId       : UUID decision courante
            ResultEvalId             : UUID resultat brut courant
            ResultEvalPrimeId        : UUID resultat residuel courant
            DecisionComments         : commentaire / justification

        Endpoint confirme par capture navigateur (02/04/2026).
        Reponse : liste JSON de toutes les lignes d'appreciation de la campagne.
        """
        data = self._get(
            f"RiskOpportunityEvaluation/GetRiskAppreciation?evaluationId={evaluation_id}"
        )
        return data if isinstance(data, list) else []


# =============================================================================
# UTILITAIRES
# =============================================================================

def strip_html(text: Any) -> Optional[str]:
    """
    Supprime les balises HTML d'un champ texte retourne par l'API QALITAS.
    Exemple : "<p>Statut juridique copie</p>" -> "Statut juridique copie"
    """
    if text is None:
        return None
    cleaned = re.sub(r"<[^>]+>", " ", str(text))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else None


# =============================================================================
# NORMALISATION : API fields -> noms colonnes Excel attendus par les agents
# =============================================================================

def normalize_risk(api_row: Dict) -> Dict:
    """
    Mappe un enregistrement GetEnabledRisks vers les noms de colonnes
    attendus par agent2_evaluation et agent4.

    Colonnes API connues :
        Code, Designation, TypesDesignation, CategoryDesignation,
        RiskOppSourceStr, ReferenceSource, ProcessDesignation,
        Cause, EmployeeFullName, DetectionMeans, PostDesignation,
        IdentificationDate, ImpactStr, System, SiteId
    """
    return {
        "Code":             api_row.get("Code"),
        "Intitule":         api_row.get("Designation"),
        "Risques":          api_row.get("Designation"),        # alias pour agent2
        "Processus":        api_row.get("ProcessDesignation"),
        "Type":             api_row.get("TypesDesignation"),
        "Categorie":        api_row.get("CategoryDesignation"),
        "Source":           api_row.get("RiskOppSourceStr"),
        "ReferenceSource":  api_row.get("ReferenceSource"),
        "Causes":           strip_html(api_row.get("Cause")),
        "Responsable":      api_row.get("EmployeeFullName"),
        "MoyenDetection":   api_row.get("DetectionMeans"),
        "Poste":            api_row.get("PostDesignation"),
        "Date":             api_row.get("IdentificationDate"),
        "Impact":           api_row.get("ImpactStr"),
        "Systeme":          api_row.get("System"),
        "Site":             api_row.get("SiteId"),
        "_source":          "qalitas_api",
        "_raw":             api_row,
    }


def normalize_risk_mapping(api_row: Dict) -> Dict:
    """
    Mappe un enregistrement GetAllRiskAppreciation (cartographie) vers les noms
    de colonnes attendus par agent2_evaluation (champs critiques : RPN, RPN',
    Appréciation, Décision, Date, Etat).

    Colonnes API connues :
        EvaluationReference, EvaluationDesignation, EvaluationFormula,
        EvaluationStateStr, EvaluationDate, EvaluationStartDate,
        EvaluationEndDate, EvaluationMinScore, EvaluationMaxScore,
        RiskOpportunityCode, RiskOpportunityDesignation, ProcessDesignation,
        RiskOpportunityCause, RiskOpportunityConsequence, RiskOppTypeDesignation,
        RiskOppCategoryDesignation, RiskOpportunityDescription, RiskOppSourceStr,
        RiskOppSystem, AppreciationStr, Score, AppreciationPrimeStr, ScorePrime,
        DecisionEvalRiskDesignation, ResultEvalDesignation,
        ResultEvalPrimeDesignation, DecisionComments
    """
    # Score initial = RPN (F*G brut ou score appreciation)
    score_initial = api_row.get("Score")
    score_residuel = api_row.get("ScorePrime")

    try:
        rpn = float(score_initial) if score_initial is not None else 0.0
    except (TypeError, ValueError):
        rpn = 0.0

    try:
        rpn_r = float(score_residuel) if score_residuel is not None else 0.0
    except (TypeError, ValueError):
        rpn_r = 0.0

    # Appréciation : on conserve la chaine brute pour parse_appreciation()
    # La plateforme peut retourner "2 ( F ) , 5 ( G )" ou juste un label
    appreciation = api_row.get("AppreciationStr", "")
    appreciation_prime = api_row.get("AppreciationPrimeStr", "")

    # Si l'appreciation ne contient pas de F/G, on construit une chaine synthetique
    # a partir du Score (RPN = F*G), utile pour parse_appreciation() dans agent2
    if appreciation and "F" not in str(appreciation) and "G" not in str(appreciation):
        appreciation = str(appreciation)  # on passe la chaine telle quelle, agent2 gere

    return {
        # Champs attendus par agent2_evaluation.detect_evaluation_mode()
        "Date":          api_row.get("EvaluationDate"),
        "Etat":          api_row.get("EvaluationStateStr"),
        "RPN":           rpn,
        "RPN '":         rpn_r,
        "RPN'":          rpn_r,                                # alias
        "Décision":      api_row.get("DecisionEvalRiskDesignation"),
        "Decision":      api_row.get("DecisionEvalRiskDesignation"),  # alias
        "Appréciation":  appreciation,
        "AppreciationPrime": appreciation_prime,
        # Champs complementaires
        "Reference":     api_row.get("EvaluationReference"),
        "Intitule":      api_row.get("RiskOpportunityDesignation"),
        "Risques":       api_row.get("RiskOpportunityDesignation"),    # alias
        "Processus":     api_row.get("ProcessDesignation"),
        "Causes":           strip_html(api_row.get("RiskOpportunityCause")),
        "Consequence":      strip_html(api_row.get("RiskOpportunityConsequence")),
        # Alias attendu par evaluate_risk_entry (agent2_evaluation.py ligne 536)
        "Effets Négatifs":  strip_html(api_row.get("RiskOpportunityConsequence")),
        "Effets Negatifs":  strip_html(api_row.get("RiskOpportunityConsequence")),
        "Type":          api_row.get("RiskOppTypeDesignation"),
        "Categorie":     api_row.get("RiskOppCategoryDesignation"),
        "Description":   strip_html(api_row.get("RiskOpportunityDescription")),
        "Resultat":      api_row.get("ResultEvalDesignation"),
        "ResultatPrime": api_row.get("ResultEvalPrimeDesignation"),
        "Commentaires":  api_row.get("DecisionComments"),
        "ScoreMin":      api_row.get("EvaluationMinScore"),
        "ScoreMax":      api_row.get("EvaluationMaxScore"),
        "DateDebut":     api_row.get("EvaluationStartDate"),
        "DateFin":       api_row.get("EvaluationEndDate"),
        # GUIDs critiques en champs de premier niveau (pour inject_agent2_results)
        "RiskOpportunityId":           api_row.get("RiskOpportunityId", ""),
        "RiskOpportunityEvaluationId": api_row.get("RiskOpportunityEvaluationId", ""),
        "EvaluationId":                api_row.get("RiskOpportunityEvaluationId", ""),
        "_source":       "qalitas_api",
        "_raw":          api_row,
    }


def normalize_nc(api_row: Dict) -> Dict:
    """
    Mappe un enregistrement GetNonConformities vers les noms attendus
    par agent4 (champs critiques : Processus, Statut).

    Nommage ASP.NET conventionnel infere (non confirme par introspection) :
        ProcessDesignation, StateStr, Designation, DetectionDate, ...
    """
    return {
        "Processus":    api_row.get("ProcessDesignation", api_row.get("Processus")),
        "Statut":       api_row.get("StateStr", api_row.get("Status", api_row.get("Statut"))),
        "Intitule":     api_row.get("Designation", api_row.get("Title")),
        "Date":         api_row.get("DetectionDate", api_row.get("CreationDate", api_row.get("Date"))),
        "Reference":    api_row.get("Reference", api_row.get("Code")),
        "Type":         api_row.get("TypeDesignation", api_row.get("Type")),
        "Gravite":      api_row.get("GravityDesignation", api_row.get("Gravity")),
        "Responsable":  api_row.get("EmployeeFullName", api_row.get("Responsable")),
        "Description":  api_row.get("Description"),
        "_source":      "qalitas_api",
        "_raw":         api_row,
    }


def normalize_indicator(api_row: Dict) -> Dict:
    """
    Mappe un enregistrement GetEnabledIndicator vers les noms attendus
    par agent4 (champs critiques : Processus, Intitule du KPI).
    """
    return {
        "Processus":        api_row.get("ProcessDesignation", api_row.get("Processus")),
        "Intitule du KPI":  api_row.get("Designation", api_row.get("Name")),
        "Formule":          api_row.get("Formula", api_row.get("Formule")),
        "Frequence":        api_row.get("Frequency", api_row.get("FrequencyDesignation")),
        "Cible":            api_row.get("Target", api_row.get("ObjectiveValue")),
        "Min":              api_row.get("MinValue", api_row.get("Min")),
        "Max":              api_row.get("MaxValue", api_row.get("Max")),
        "Unite":            api_row.get("Unit", api_row.get("UnitDesignation")),
        "Responsable":      api_row.get("EmployeeFullName"),
        "Statut":           api_row.get("StateStr", api_row.get("Status")),
        "Date":             api_row.get("CreationDate"),
        "_source":          "qalitas_api",
        "_raw":             api_row,
    }


def normalize_complaint(api_row: Dict) -> Dict:
    """
    Mappe un enregistrement GetComplaints vers les noms attendus par agent4
    (champs critiques : Produit, Statut).
    """
    return {
        "Produit":      api_row.get("ProductDesignation", api_row.get("Product", api_row.get("Produit"))),
        "Statut":       api_row.get("StateStr", api_row.get("Status", api_row.get("Statut"))),
        "Intitule":     api_row.get("Designation", api_row.get("Subject")),
        "Client":       api_row.get("CustomerName", api_row.get("Client")),
        "Date":         api_row.get("ComplaintDate", api_row.get("CreationDate", api_row.get("Date"))),
        "Reference":    api_row.get("Reference", api_row.get("Code")),
        "Gravite":      api_row.get("GravityDesignation", api_row.get("Gravity")),
        "Responsable":  api_row.get("EmployeeFullName"),
        "Description":  api_row.get("Description"),
        "_source":      "qalitas_api",
        "_raw":         api_row,
    }


# =============================================================================
# INDEX HELPERS (memes que dans load_excel_data)
# =============================================================================

def build_kpi_index(kpis: List[Dict]) -> Dict[str, List[Dict]]:
    index: Dict[str, List[Dict]] = {}
    for kpi in kpis:
        proc = str(kpi.get("Processus", "")).strip()
        if proc:
            index.setdefault(proc, []).append(kpi)
    return index


def build_nc_summary(nc_list: List[Dict]) -> Dict[str, Any]:
    by_process: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for nc in nc_list:
        proc = str(nc.get("Processus", "Non precise")).strip()
        status = str(nc.get("Statut", "Non precise")).strip()
        by_process[proc] = by_process.get(proc, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    return {"total": len(nc_list), "by_process": by_process, "by_status": by_status}


def build_reclamation_summary(recs: List[Dict]) -> Dict[str, Any]:
    by_product: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for rec in recs:
        product = str(rec.get("Produit", "Non precise")).strip()
        status = str(rec.get("Statut", "Non precise")).strip()
        by_product[product] = by_product.get(product, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    return {"total": len(recs), "by_product": by_product, "by_status": by_status}


def build_risque_index(risques: List[Dict]) -> Dict[str, List[Dict]]:
    index: Dict[str, List[Dict]] = {}
    for r in risques:
        proc = str(r.get("Processus", "")).strip()
        if proc:
            index.setdefault(proc, []).append(r)
    return index


def build_cartographie_index(carto: List[Dict]) -> Dict[str, List[Dict]]:
    index: Dict[str, List[Dict]] = {}
    for c in carto:
        proc = str(c.get("Processus", "")).strip()
        if proc:
            index.setdefault(proc, []).append(c)
    return index


# =============================================================================
# CHARGEMENT DU CONTEXTE GLOBAL VIA API
# =============================================================================

def load_all_context_api(
    base_url: str = DEFAULT_BASE_URL,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    months_back: int = DEFAULT_MONTHS_BACK,
) -> Dict[str, Any]:
    """
    Produit le meme contexte que load_excel_data.load_all_context()
    mais en interrogeant l'API QALITAS QSE.

    Retourne un dict avec les cles :
        kpis, nc, reclamations, risques, cartographie,
        kpi_index, risque_index, carto_index, nc_summary, reclamation_summary,
        opportunites_api,    <- bonus : opportunites depuis l'API
        cartographie_opport, <- bonus : cartographie des opportunites (nature=1)
        dashboard_charts,    <- bonus : donnees des graphiques du dashboard
        _source              = "qalitas_api"

    En cas d'echec total, retourne un contexte vide (memes cles, listes vides)
    afin que les agents fonctionnent en mode degradé sans planter.
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=months_back * 30)

    context: Dict[str, Any] = {
        "kpis": [],
        "nc": [],
        "reclamations": [],
        "risques": [],
        "cartographie": [],
        "processus": [],
        "objectifs": [],
        "fournisseurs": [],
        "opportunites_api": [],
        "cartographie_opport": [],
        "dashboard_charts": {},
        "_source": "qalitas_api",
    }

    if not username or not password:
        logger.error(
            "Credentials QALITAS manquants. "
            "Definir QALITAS_USERNAME et QALITAS_PASSWORD en variables d'environnement."
        )
        _add_indexes(context)
        return context

    client = QalitasClient(base_url=base_url, username=username, password=password)

    try:
        if not client.login():
            logger.error("Impossible de se connecter a QALITAS. Contexte vide retourne.")
            _add_indexes(context)
            return context

        logger.info("Chargement des donnees depuis l'API QALITAS...")

        # --- KPIs ---
        try:
            raw_kpis = client.get_indicators()
            context["kpis"] = [normalize_indicator(r) for r in raw_kpis]
            logger.info("  KPIs         : %d enregistrements", len(context["kpis"]))
        except Exception as e:
            logger.warning("  KPIs         : erreur - %s", e)

        # --- Non-conformites ---
        try:
            raw_nc = client.get_non_conformities(start_date=start_date, end_date=end_date)
            context["nc"] = [normalize_nc(r) for r in raw_nc]
            logger.info("  NC           : %d enregistrements", len(context["nc"]))
        except Exception as e:
            logger.warning("  NC           : erreur - %s", e)

        # --- Reclamations clients ---
        try:
            raw_recs = client.get_complaints(start_date=start_date, end_date=end_date)
            context["reclamations"] = [normalize_complaint(r) for r in raw_recs]
            logger.info("  Reclamations : %d enregistrements", len(context["reclamations"]))
        except Exception as e:
            logger.warning("  Reclamations : erreur - %s", e)

        # --- Risques ---
        try:
            raw_risks = client.get_risks()
            context["risques"] = [normalize_risk(r) for r in raw_risks]
            logger.info("  Risques      : %d enregistrements", len(context["risques"]))
        except Exception as e:
            logger.warning("  Risques      : erreur - %s", e)

        # --- Cartographie des risques (evaluations) ---
        # GetAllRiskAppreciation retourne une vue sans UUIDs.
        # On enrichit via GetRiskEvaluations -> GetRiskAppreciation pour avoir
        # RiskOpportunityId + RiskOpportunityEvaluationId dans chaque ligne.
        try:
            evaluations = client.get_evaluations(project_id="")
            raw_carto = []
            for ev in evaluations:
                ev_id = ev.get('Id', '')
                if not ev_id:
                    continue
                rows = client.get_risk_appreciation(ev_id)
                for row in rows:
                    row.setdefault('RiskOpportunityEvaluationId', ev_id)
                    if row.get('RiskOpportunityNature', 0) == 0:
                        raw_carto.append(row)
            context["cartographie"] = [normalize_risk_mapping(r) for r in raw_carto]
            logger.info("  Cartographie : %d enregistrements (%d campagnes)",
                        len(context["cartographie"]), len(evaluations))
        except Exception as e:
            logger.warning("  Cartographie : erreur - %s", e)

        # --- Opportunites ---
        try:
            raw_opps = client.get_opportunities()
            context["opportunites_api"] = [normalize_risk(r) for r in raw_opps]
            logger.info("  Opportunites : %d enregistrements", len(context["opportunites_api"]))
        except Exception as e:
            logger.warning("  Opportunites : erreur - %s", e)

        # --- Cartographie des opportunites (nature=1) ---
        try:
            ev_list = evaluations if isinstance(evaluations, list) else []
            raw_carto_opp = []
            for ev in ev_list:
                ev_id = ev.get('Id', '')
                if not ev_id:
                    continue
                rows = client.get_risk_appreciation(ev_id)
                for row in rows:
                    row.setdefault('RiskOpportunityEvaluationId', ev_id)
                    if row.get('RiskOpportunityNature', 0) == 1:
                        raw_carto_opp.append(row)
            context["cartographie_opport"] = [normalize_risk_mapping(r) for r in raw_carto_opp]
            logger.info("  Carto opport : %d enregistrements", len(context["cartographie_opport"]))
        except Exception as e:
            logger.warning("  Carto opport : erreur - %s", e)

        # --- Graphiques du dashboard (donnees de tendance) ---
        try:
            context["dashboard_charts"] = {
                "nc":       client.get_dashboard_chart_nc(),
                "actions":  client.get_dashboard_chart_actions(),
                "audit":    client.get_dashboard_chart_audit(),
                "customer": client.get_dashboard_chart_customer(),
            }
            logger.info("  Dashboard    : graphiques charges")
        except Exception as e:
            logger.warning("  Dashboard    : erreur graphiques - %s", e)

    finally:
        client.logout()

    _add_indexes(context)

    logger.info(
        "Contexte API QALITAS charge : %d KPIs, %d NC, %d reclamations, "
        "%d risques, %d cartographie",
        len(context["kpis"]),
        len(context["nc"]),
        len(context["reclamations"]),
        len(context["risques"]),
        len(context["cartographie"]),
    )
    return context


def _add_indexes(context: Dict[str, Any]) -> None:
    """Calcule les index et les resumes statistiques."""
    context["kpi_index"] = build_kpi_index(context.get("kpis", []))
    context["risque_index"] = build_risque_index(context.get("risques", []))
    context["carto_index"] = build_cartographie_index(context.get("cartographie", []))
    context["nc_summary"] = build_nc_summary(context.get("nc", []))
    context["reclamation_summary"] = build_reclamation_summary(context.get("reclamations", []))


# =============================================================================
# CHARGEMENT HYBRIDE : API en priorite, fallback Excel
# =============================================================================

def load_all_context_hybrid(
    data_dir: str,
    base_url: str = DEFAULT_BASE_URL,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    prefer_api: bool = True,
) -> Dict[str, Any]:
    """
    Strategie hybride :
    - Si prefer_api=True : essaie l'API QALITAS en premier, fallback Excel si echec
    - Si prefer_api=False : charge Excel, enrichit avec l'API si disponible

    Cela permet un fonctionnement hors-ligne transparent.
    """
    from loaders.load_excel_data import load_all_context as load_excel

    if prefer_api and username and password:
        logger.info("Mode API prioritaire...")
        ctx = load_all_context_api(base_url, username, password)

        # Si l'API a retourne au moins des risques ou de la cartographie, on l'utilise
        if ctx.get("risques") or ctx.get("cartographie"):
            ctx["_load_mode"] = "api"
            return ctx

        logger.warning("API QALITAS sans donnees, fallback sur Excel...")

    logger.info("Chargement depuis les fichiers Excel...")
    ctx = load_excel(data_dir)
    ctx["_load_mode"] = "excel"
    ctx["_source"] = "excel"
    ctx.setdefault("opportunites_api", [])
    ctx.setdefault("cartographie_opport", [])
    ctx.setdefault("dashboard_charts", {})
    return ctx


# =================================================