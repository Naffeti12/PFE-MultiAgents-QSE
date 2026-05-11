import re
import hashlib
from typing import Dict, List, Any, Optional, Tuple


# =============================================================================
# CONFIGURATION METIER
# =============================================================================

KEYWORDS = [
    "crise", "guerre",
    "baisse", "diminution", "chute", "degradation",
    "retard", "rupture", "panne", "arret",
    "non conformite", "non-conformite", "nc",
    "reclamation", "reclamations",
    "absence", "absences", "absenteisme", "turnover",
    "depasse", "depassement", "hors limite",
    "probleme", "problemes",
    "manque", "insuffisant", "insuffisance",
    "incident", "accident",
    "ecart", "derive",
    "obsolete", "perime",
    "rejet", "rebut", "dechet",
    "insatisfaction", "plainte",
]

# Mapping pages du dashboard PDF vers processus (noms officiels)
PROCESS_RULES = {
    range(1, 5): "PROCESSUS DEVELOPPEMENT COMMERCIAL ET RELATIONS CLIENTS",
    range(5, 9): "PROCESSUS PILOTAGE, SURVEILLANCE ET AMELIORATION CONTINUE",
    range(9, 14): "PROCESSUS METHODES ET INDUSTRIALISATION",
    range(14, 36): "PROCESSUS PRODUCTION",
    range(36, 45): "PROCESSUS SUPPLY CHAIN",
    range(45, 69): "PROCESSUS RESSOURCES HUMAINES",
    range(69, 78): "PROCESSUS MAINTENANCE ET SECURITE",
}

# Liens d'impact inter-processus
INTER_PROCESS_IMPACT = {
    "PROCESSUS SUPPLY CHAIN": [
        {"process": "PROCESSUS PRODUCTION", "impact": "Retard ou rupture supply chain entrainant un arret ou ralentissement de production."},
        {"process": "PROCESSUS DEVELOPPEMENT COMMERCIAL ET RELATIONS CLIENTS", "impact": "Retard de livraison impactant la satisfaction client."},
    ],
    "PROCESSUS PRODUCTION": [
        {"process": "PROCESSUS DEVELOPPEMENT COMMERCIAL ET RELATIONS CLIENTS", "impact": "Defaut qualite ou retard de production impactant les engagements clients."},
        {"process": "PROCESSUS SUPPLY CHAIN", "impact": "Surconsommation ou rebuts augmentant la pression sur les approvisionnements."},
    ],
    "PROCESSUS MAINTENANCE ET SECURITE": [
        {"process": "PROCESSUS PRODUCTION", "impact": "Indisponibilite des equipements impactant la capacite de production."},
        {"process": "PROCESSUS RESSOURCES HUMAINES", "impact": "Risque securite pouvant affecter la sante des operateurs."},
    ],
    "PROCESSUS RESSOURCES HUMAINES": [
        {"process": "PROCESSUS PRODUCTION", "impact": "Absenteisme ou turnover perturbant la continuite de production."},
        {"process": "PROCESSUS PILOTAGE, SURVEILLANCE ET AMELIORATION CONTINUE", "impact": "Manque de competences pouvant degrader le pilotage des processus."},
    ],
    "PROCESSUS METHODES ET INDUSTRIALISATION": [
        {"process": "PROCESSUS PRODUCTION", "impact": "Erreur methode ou industrialisation impactant la qualite de fabrication."},
    ],
    "PROCESSUS PILOTAGE, SURVEILLANCE ET AMELIORATION CONTINUE": [
        {"process": "PROCESSUS PRODUCTION", "impact": "Defaut de pilotage pouvant masquer des derives de performance."},
    ],
    "PROCESSUS DEVELOPPEMENT COMMERCIAL ET RELATIONS CLIENTS": [
        {"process": "PROCESSUS PRODUCTION", "impact": "Surcharge de commandes ou exigences non anticipees perturbant la planification."},
    ],
}


# =============================================================================
# FONCTIONS UTILITAIRES
# =============================================================================

def normalize_text(text: str) -> str:
    """Nettoie le texte : espaces multiples, strip."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def normalize_for_search(text: str) -> str:
    """
    Normalise le texte pour la recherche de mots-cles :
    minuscules, suppression accents simples.
    """
    if not text:
        return ""
    text = text.lower()
    replacements = {
        "e": "eeeee",
        "a": "aaa",
        "i": "ii",
        "o": "oo",
        "u": "uu",
        "c": "c",
    }
    # Remplacements accents courants
    for char_from, char_to in [
        ("e", "e"), ("e", "e"), ("e", "e"), ("e", "e"),
        ("a", "a"), ("a", "a"),
        ("i", "i"), ("i", "i"),
        ("o", "o"),
        ("u", "u"), ("u", "u"),
        ("c", "c"),
    ]:
        pass  # Les accents sont deja geres par lower() en Python 3

    # Remplacement des caracteres accentues
    accent_map = str.maketrans(
        "eeeeeaaaiioouuc",
        "eeeeeaaaiioouuc"
    )
    text = text.translate(accent_map)
    return text


def infer_process_name(page_number: int) -> str:
    """Deduit le processus a partir du numero de page du dashboard."""
    for page_range, process_name in PROCESS_RULES.items():
        if page_number in page_range:
            return process_name
    return "Non precise"


def find_matched_keywords(text: str, keywords: List[str]) -> List[str]:
    """Retourne les mots-cles detectes dans le texte, tries."""
    text_lower = text.lower()
    matched = sorted({kw for kw in keywords if kw in text_lower})
    return matched


def extract_snippet(text: str, keywords: List[str], max_len: int = 400) -> str:
    """Extrait un snippet centre sur le premier mot-cle trouve."""
    clean_text = normalize_text(text)
    text_lower = clean_text.lower()

    for kw in keywords:
        idx = text_lower.find(kw.lower())
        if idx != -1:
            start = max(0, idx - 120)
            end = min(len(clean_text), idx + 280)
            return clean_text[start:end][:max_len]

    return clean_text[:max_len]


# =============================================================================
# CLASSIFICATION DU TYPE DE RISQUE
# =============================================================================

def classify_issue(keywords: List[str], text: str) -> str:
    """Classe le type de risque selon les mots-cles et le contexte."""
    joined = " ".join(keywords).lower()
    text_lower = text.lower()

    # Risque technique
    if any(k in joined for k in ["panne", "arret"]):
        return "Risque technique"
    if ("depasse" in joined or "depassement" in joined) and \
       any(w in text_lower for w in ["machine", "indisponibilite", "equipement"]):
        return "Risque technique"

    # Risque logistique / supply chain
    if any(k in joined for k in ["retard", "rupture", "manque", "insuffisant", "insuffisance"]):
        return "Risque logistique"

    # Risque RH
    if any(k in joined for k in ["absence", "absences", "absenteisme", "turnover"]):
        return "Risque RH"

    # Risque client
    if any(k in joined for k in ["reclamation", "reclamations", "insatisfaction", "plainte"]):
        return "Risque client"

    # Risque qualite
    if any(k in joined for k in ["non conformite", "non-conformite", "nc", "rejet", "rebut", "dechet"]):
        return "Risque qualite"

    # Risque performance
    if any(k in joined for k in [
        "crise", "guerre", "baisse", "diminution", "chute",
        "degradation", "incident", "accident", "ecart", "derive"
    ]):
        return "Risque performance"

    if "depasse" in joined or "depassement" in joined or "hors limite" in joined:
        return "Risque performance"

    if "obsolete" in joined or "perime" in joined:
        return "Risque conformite"

    return "Autre"


# =============================================================================
# CALCUL DE LA GRAVITE
# =============================================================================

def compute_severity(keywords: List[str], risk_type: str, text: str = "") -> str:
    """Estime la gravite : faible | moyenne | elevee."""
    joined = " ".join(keywords).lower()
    text_lower = text.lower()

    # Gravite elevee
    if any(k in joined for k in ["panne", "rupture", "guerre", "crise", "accident", "arret"]):
        return "elevee"

    if ("depasse" in joined or "depassement" in joined) and \
       risk_type in ["Risque technique", "Risque performance"]:
        return "elevee"

    if "seuil critique" in text_lower or "hors limite" in text_lower:
        return "elevee"

    # Gravite moyenne
    if any(k in joined for k in [
        "retard", "baisse", "absence", "reclamation",
        "non conformite", "non-conformite", "incident",
        "manque", "ecart", "derive", "diminution",
        "chute", "degradation", "rejet", "rebut"
    ]):
        return "moyenne"

    # Faible
    return "faible"


# =============================================================================
# RECOMMANDATION PAR DEFAUT
# =============================================================================

def make_recommendation(risk_type: str) -> str:
    """Recommandation metier par defaut selon le type de risque."""
    mapping = {
        "Risque technique": (
            "Declencher une analyse maintenance et verifier la disponibilite "
            "des pieces ou equipements critiques."
        ),
        "Risque logistique": (
            "Analyser la chaine d'approvisionnement, les delais fournisseurs "
            "et les points de rupture potentiels."
        ),
        "Risque RH": (
            "Analyser les causes d'absenteisme ou de turnover et evaluer "
            "l'impact sur la continuite d'activite."
        ),
        "Risque client": (
            "Examiner les reclamations associees et mesurer l'impact "
            "sur la satisfaction client et la fidelisation."
        ),
        "Risque qualite": (
            "Verifier les non-conformites associees, identifier la cause racine "
            "et planifier une action corrective."
        ),
        "Risque performance": (
            "Analyser l'ecart de performance par rapport a la cible, "
            "declencher une revue du KPI concerne."
        ),
        "Risque conformite": (
            "Verifier la conformite reglementaire et normative, "
            "planifier une mise a jour documentaire si necessaire."
        ),
    }
    return mapping.get(risk_type, "Effectuer une analyse manuelle complementaire.")


# =============================================================================
# SCORE DE CRITICITE COMPOSITE
# =============================================================================

GRAVITY_SCORES = {"elevee": 3, "moyenne": 2, "faible": 1}

RISK_TYPE_WEIGHTS = {
    "Risque technique": 1.3,
    "Risque qualite": 1.2,
    "Risque client": 1.2,
    "Risque logistique": 1.1,
    "Risque performance": 1.0,
    "Risque RH": 1.0,
    "Risque conformite": 0.9,
    "Autre": 0.8,
}


def compute_criticality_score(
    gravity: str,
    risk_type: str,
    keyword_count: int,
    has_context_data: bool = False
) -> float:
    """
    Calcule un score de criticite composite :
    score = gravity_score * type_weight * (1 + 0.1 * nb_keywords) * context_bonus

    Retourne un float entre 0 et ~6.
    """
    g_score = GRAVITY_SCORES.get(gravity, 1)
    t_weight = RISK_TYPE_WEIGHTS.get(risk_type, 1.0)
    kw_factor = 1.0 + 0.1 * min(keyword_count, 5)
    context_bonus = 1.15 if has_context_data else 1.0

    return round(g_score * t_weight * kw_factor * context_bonus, 2)


def criticality_level(score: float) -> str:
    """Convertit le score en niveau de criticite."""
    if score >= 4.0:
        return "critique"
    if score >= 2.5:
        return "significatif"
    if score >= 1.5:
        return "modere"
    return "mineur"


# =============================================================================
# REEVALUATION
# =============================================================================

def should_trigger_reevaluation(keywords: List[str], risk_type: str) -> bool:
    """Determine si une reevaluation est necessaire."""
    joined = " ".join(keywords).lower()

    trigger_kw = [
        "panne", "rupture", "retard", "incident", "accident",
        "crise", "guerre", "baisse", "absence",
        "non conformite", "non-conformite",
        "reclamation", "depasse", "depassement",
        "manque", "ecart", "derive", "arret",
        "chute", "degradation", "hors limite",
    ]

    if any(k in joined for k in trigger_kw):
        return True

    if risk_type in ["Risque technique", "Risque qualite", "Risque performance"]:
        return True

    return False


# =============================================================================
# IMPACT INTER-PROCESSUS
# =============================================================================

def get_inter_process_impacts(process_name: str) -> List[Dict[str, str]]:
    """
    Retourne les impacts potentiels sur d'autres processus.
    """
    return INTER_PROCESS_IMPACT.get(process_name, [])


# =============================================================================
# ENRICHISSEMENT VIA CONTEXTE EXCEL
# =============================================================================

def enrich_with_context(
    alert: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Enrichit une alerte avec les donnees de contexte Excel :
    - Nombre de NC liees au processus
    - Nombre de reclamations liees
    - KPIs associes au processus
    - Risques existants dans le registre
    """
    if not context:
        return alert

    process_name = alert.get("process_name", "")
    enrichment = {}

    # NC liees
    nc_summary = context.get("nc_summary", {})
    nc_by_proc = nc_summary.get("by_process", {})
    nc_count = 0
    for proc_key, count in nc_by_proc.items():
        if process_name.lower() in str(proc_key).lower() or \
           str(proc_key).lower() in process_name.lower():
            nc_count += count
    enrichment["nc_count_process"] = nc_count

    # Reclamations liees
    rec_summary = context.get("reclamation_summary", {})
    enrichment["reclamation_total"] = rec_summary.get("total", 0)

    # KPIs du processus
    kpi_index = context.get("kpi_index", {})
    related_kpis = []
    for proc_key, kpis in kpi_index.items():
        if process_name.lower() in proc_key.lower() or \
           proc_key.lower() in process_name.lower():
            for kpi in kpis:
                kpi_name = str(kpi.get("Intitule du KPI", kpi.get("intitule", "")))
                if kpi_name:
                    related_kpis.append(kpi_name)
    enrichment["related_kpis"] = related_kpis[:5]

    # Risques existants pour ce processus
    risque_index = context.get("risque_index", {})
    existing_risks = []
    for proc_key, risks in risque_index.items():
        if process_name.lower() in proc_key.lower() or \
           proc_key.lower() in process_name.lower():
            for r in risks:
                risk_desc = str(r.get("Description", r.get("description", "")))
                if risk_desc:
                    existing_risks.append(risk_desc)
    enrichment["existing_risks"] = existing_risks[:5]

    enrichment["has_context_data"] = bool(
        nc_count > 0 or related_kpis or existing_risks
    )

    alert["context_enrichment"] = enrichment
    return alert


# =============================================================================
# DEDUPLICATION DES ALERTES
# =============================================================================

def compute_alert_hash(alert: Dict[str, Any]) -> str:
    """
    Hash unique base sur processus + type de risque + mots-cles tries.
    Permet de detecter les doublons inter-pages.
    """
    key = (
        str(alert.get("process_name", "")) + "|" +
        str(alert.get("type_risque_regle", "")) + "|" +
        ",".join(sorted(alert.get("keywords", [])))
    )
    return hashlib.md5(key.encode()).hexdigest()


def deduplicate_alerts(alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Supprime les doublons : si meme processus + meme type + memes mots-cles,
    on garde l'alerte avec la gravite la plus elevee et on fusionne les pages.
    """
    seen = {}
    gravity_order = {"elevee": 3, "moyenne": 2, "faible": 1}

    for alert in alerts:
        h = compute_alert_hash(alert)

        if h not in seen:
            alert["pages"] = [alert.get("page")]
            seen[h] = alert
        else:
            existing = seen[h]
            existing["pages"].append(alert.get("page"))

            # Garder la gravite la plus elevee
            existing_g = gravity_order.get(existing.get("gravite_regle"), 0)
            new_g = gravity_order.get(alert.get("gravite_regle"), 0)
            if new_g > existing_g:
                existing["gravite_regle"] = alert["gravite_regle"]
                existing["snippet"] = alert["snippet"]

            # Fusionner les mots-cles
            merged_kw = sorted(set(existing.get("keywords", []) + alert.get("keywords", [])))
            existing["keywords"] = merged_kw

    return list(seen.values())


# =============================================================================
# DETECTION PRINCIPALE
# =============================================================================

def detect_pdf_alerts(
    pages_data: List[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Detecte les alertes dans les pages extraites du dashboard PDF.
    Chaque element de pages_data doit contenir :
    {
        "page": int,
        "text": str,
        "combined_text": str (optionnel, texte + tables)
    }

    Parametres :
        pages_data : pages extraites du PDF
        context : donnees Excel chargees (optionnel)

    Retourne une liste d'alertes enrichies et dedupliquees.
    """
    raw_alerts = []

    for page in pages_data:
        page_number = page.get("page")
        # Utiliser combined_text si disponible (texte + tables)
        text = normalize_text(
            page.get("combined_text", page.get("text", ""))
        )

        if not text:
            continue

        matched = find_matched_keywords(text, KEYWORDS)

        if not matched:
            continue

        process_name = infer_process_name(page_number)
        type_risque = classify_issue(matched, text)
        gravite = compute_severity(matched, type_risque, text)
        snippet = extract_snippet(text, matched)
        reevaluation = should_trigger_reevaluation(matched, type_risque)
        inter_impacts = get_inter_process_impacts(process_name)

        has_context = False
        if context:
            has_context = True

        score = compute_criticality_score(
            gravite, type_risque, len(matched), has_context
        )

        alert = {
            "page": page_number,
            "process_name": process_name,
            "keywords": matched,
            "snippet": snippet,
            "evidence_source": f"Dashboard PDF - page {page_number}",
            "type_risque_regle": type_risque,
            "gravite_regle": gravite,
            "recommandation_regle": make_recommendation(type_risque),
            "reevaluation_required_regle": reevaluation,
            "criticality_score": score,
            "criticality_level": criticality_level(score),
            "inter_process_impacts": inter_impacts,
        }

        # Enrichissement contexte Excel
        if context:
            alert = enrich_with_context(alert, context)

        raw_alerts.append(alert)

    # Deduplication
    deduplicated = deduplicate_alerts(raw_alerts)

    # Tri par score de criticite decroissant
    deduplicated.sort(key=lambda a: a.get("criticality_score", 0), reverse=True)

    return deduplicated


# =============================================================================
# TEST LOCAL
# =============================================================================

if __name__ == "__main__":
    sample_pages = [
        {
            "page": 70,
            "text": (
                "Cette mesure depasse la cible a cause de panne de la machine "
                "de coupe Prospin. Le fournisseur n'a pas un magasin local pour "
                "les pieces en Tunisie."
            )
        },
        {
            "page": 71,
            "text": (
                "Cette mesure depasse la cible suite a la panne de la machine "
                "de coupe. Intervention du fournisseur en cours."
            )
        },
        {
            "page": 48,
            "text": (
                "2020 mars 33,9 ABSENCES A CAUSE DE COVID-19 "
                "2020 avril 81,73 ABSENCES A CAUSE DE COVID-19"
            )
        }
    ]

    detected = detect_pdf_alerts(sample_pages)

    import json
    print(f"\n{len(detected)} alertes detectees (apres deduplication):\n")
    print(json.dumps(detected, ensure_ascii=False, indent=2))
