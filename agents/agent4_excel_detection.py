"""
Agent 4 - Detection de risques ET opportunites depuis les sources Excel.
Conforme au cahier des charges :
- Comparaison residuel attendu vs observe
- Suivi des risques acceptes (alerte si depassement seuil)
- Detection de derives d'indicateurs
- Detection d'opportunites
- Capitalisation (schemas recurrents, actions reutilisables)
- Conscience temporelle (donnees anciennes vs recentes)
"""

import re
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from collections import Counter


# =============================================================================
# LISTE OFFICIELLE DES PROCESSUS (reference : Cartographie des risques)
# =============================================================================

OFFICIAL_PROCESSES = [
    "PROCESSUS SUPPLY CHAIN",
    "PROCESSUS METHODES ET INDUSTRIALISATION",
    "PROCESSUS PILOTAGE, SURVEILLANCE ET AMELIORATION CONTINUE",
    "PROCESSUS MAINTENANCE ET SECURITE",
    "PROCESSUS DEVELOPPEMENT COMMERCIAL ET RELATIONS CLIENTS",
    "PROCESSUS PRODUCTION",
    "PROCESSUS RESSOURCES HUMAINES",
]


def _strip_accents(text: str) -> str:
    """Supprime les accents pour comparaison insensible aux accents."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.category(c).startswith("M"))

# Mapping normalise pour unifier les noms
PROCESS_NORMALIZE = {
    "supply chain": "PROCESSUS SUPPLY CHAIN",
    "methodes": "PROCESSUS METHODES ET INDUSTRIALISATION",
    "industrialisation": "PROCESSUS METHODES ET INDUSTRIALISATION",
    "pilotage": "PROCESSUS PILOTAGE, SURVEILLANCE ET AMELIORATION CONTINUE",
    "surveillance": "PROCESSUS PILOTAGE, SURVEILLANCE ET AMELIORATION CONTINUE",
    "amelioration": "PROCESSUS PILOTAGE, SURVEILLANCE ET AMELIORATION CONTINUE",
    "maintenance": "PROCESSUS MAINTENANCE ET SECURITE",
    "securite": "PROCESSUS MAINTENANCE ET SECURITE",
    "commercial": "PROCESSUS DEVELOPPEMENT COMMERCIAL ET RELATIONS CLIENTS",
    "client": "PROCESSUS DEVELOPPEMENT COMMERCIAL ET RELATIONS CLIENTS",
    "production": "PROCESSUS PRODUCTION",
    "ressources humaines": "PROCESSUS RESSOURCES HUMAINES",
    "rh": "PROCESSUS RESSOURCES HUMAINES",
    # Noms issus du mapping pages du dashboard
    "developpement commercial et relations clients": "PROCESSUS DEVELOPPEMENT COMMERCIAL ET RELATIONS CLIENTS",
    "pilotage, surveillance et amelioration continue": "PROCESSUS PILOTAGE, SURVEILLANCE ET AMELIORATION CONTINUE",
    "methodes et industrialisation": "PROCESSUS METHODES ET INDUSTRIALISATION",
    "maintenance et securite": "PROCESSUS MAINTENANCE ET SECURITE",
}


def normalize_process_name(name: str) -> str:
    """
    Normalise un nom de processus vers le nom officiel.
    Comparaison insensible aux accents (SECURITE == SECURITE).
    Si le nom ne correspond a aucun processus officiel, retourne vide.
    """
    if not name:
        return ""
    name_clean = name.strip()
    name_lower = _strip_accents(name_clean).lower()

    # Correspondance directe (noms de la cartographie)
    for official in OFFICIAL_PROCESSES:
        official_lower = _strip_accents(official).lower()
        if official_lower in name_lower or name_lower in official_lower:
            return official

    # Correspondance par mots-cles
    for keyword, official in PROCESS_NORMALIZE.items():
        if keyword in name_lower:
            return official

    return ""


def is_valid_process(name: str) -> bool:
    """Verifie si un nom correspond a un processus reel."""
    return bool(normalize_process_name(name))


# =============================================================================
# UTILITAIRES TEMPORELS
# =============================================================================

def parse_date(value: Any) -> Optional[datetime]:
    """Parse une date depuis differents formats."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y"]:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def is_recent(date_val: Any, months: int = 12) -> bool:
    """Verifie si une date est recente (< N mois)."""
    dt = parse_date(date_val)
    if not dt:
        return False
    cutoff = datetime.now() - timedelta(days=months * 30)
    return dt >= cutoff


# =============================================================================
# EXTRACTION DU PROCESSUS DEPUIS UNE NC
# =============================================================================

def extract_nc_process(nc: Dict[str, Any]) -> str:
    """
    Extrait le nom de processus depuis une NC.
    La colonne 'Source' contient le type de source (Audit Interne, etc.)
    On cherche le processus dans d'autres champs ou dans le texte.
    """
    # Chercher dans les champs potentiels
    for field in ["Processus", "processus", "Source", "Intitule de la source"]:
        val = str(nc.get(field, "")).strip()
        normalized = normalize_process_name(val)
        if normalized:
            return normalized

    return ""


# =============================================================================
# 1. COMPARAISON RESIDUEL ATTENDU VS OBSERVE
# =============================================================================

def detect_residual_vs_observed(
    cartographie: List[Dict[str, Any]],
    nc_list: List[Dict[str, Any]],
    reclamations: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Compare le risque residuel attendu (RPN' dans la cartographie)
    avec la realite observee (NC recentes par processus).
    """
    alerts = []

    if not cartographie:
        return alerts

    # Compter les NC recentes par processus normalise
    nc_recent_by_proc = Counter()
    for nc in nc_list:
        proc = extract_nc_process(nc)
        if proc and is_recent(nc.get("Detectee le", nc.get("Date")), 12):
            nc_recent_by_proc[proc] += 1

    # Regrouper les risques par processus
    processed_procs = set()
    for entry in cartographie:
        processus = normalize_process_name(
            str(entry.get("Processus", "")).strip()
        )
        if not processus or processus in processed_procs:
            continue
        processed_procs.add(processus)

        nc_count = nc_recent_by_proc.get(processus, 0)

        # Recuperer le RPN residuel moyen du processus
        rpn_values = []
        for e in cartographie:
            p = normalize_process_name(str(e.get("Processus", "")).strip())
            if p == processus:
                try:
                    rpn_r = float(e.get("RPN '", e.get("RPN'", 0)) or 0)
                    if rpn_r > 0:
                        rpn_values.append(rpn_r)
                except (ValueError, TypeError):
                    pass

        avg_rpn = sum(rpn_values) / len(rpn_values) if rpn_values else 0

        # Signal : NC recentes significatives malgre RPN acceptable
        if nc_count >= 5 and avg_rpn <= 8:
            alerts.append({
                "source": "Cartographie + NC.xlsx",
                "evidence_source": "Comparaison residuel attendu vs observe",
                "process_name": processus,
                "type_risque_regle": "Ecart residuel",
                "signal_type": "residuel_vs_observe",
                "gravite_regle": "elevee" if nc_count >= 10 else "moyenne",
                "keywords": ["risque residuel", "ecart", "nc", "reevaluation"],
                "snippet": (
                    f"Processus '{processus}' : RPN residuel moyen = "
                    f"{round(avg_rpn, 1)} (considere acceptable), mais "
                    f"{nc_count} NC recentes detectees sur les 12 derniers "
                    f"mois. Ecart significatif entre le risque residuel "
                    f"attendu et la realite observee."
                ),
                "recommandation_regle": (
                    f"Reevaluer les risques du processus '{processus}' : "
                    f"le niveau residuel observe ({nc_count} NC) ne correspond "
                    f"plus au RPN residuel attendu ({round(avg_rpn, 1)}). "
                    f"Envisager un audit cible sous 30 jours."
                ),
                "reevaluation_required_regle": True,
            })

    return alerts


# =============================================================================
# 2. SUIVI DES RISQUES ACCEPTES
# =============================================================================

def detect_accepted_risk_alerts(
    cartographie: List[Dict[str, Any]],
    nc_list: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Surveille les risques acceptes.
    Alerte UNIQUEMENT si le RPN residuel depasse le seuil critique
    OU si le nombre de NC recentes est anormalement eleve.
    Seuil calibre : RPN > 8 (eleve pour l'echelle 3-10 de l'entreprise)
    ou NC recentes >= 5 sur le processus.
    """
    alerts = []

    # NC recentes par processus
    nc_recent_by_proc = Counter()
    for nc in nc_list:
        proc = extract_nc_process(nc)
        if proc and is_recent(nc.get("Detectee le", nc.get("Date")), 12):
            nc_recent_by_proc[proc] += 1

    # Analyser par processus (pas par risque individuel)
    processed_procs = set()
    for risk in cartographie:
        decision = str(risk.get("Decision",
                                 risk.get("Décision", ""))).strip().lower()
        if "accepter" not in decision:
            continue

        processus = normalize_process_name(
            str(risk.get("Processus", "")).strip()
        )
        if not processus or processus in processed_procs:
            continue
        processed_procs.add(processus)

        # RPN max du processus
        rpn_max = 0
        risk_count = 0
        for r in cartographie:
            p = normalize_process_name(str(r.get("Processus", "")).strip())
            if p == processus:
                risk_count += 1
                try:
                    rpn = float(r.get("RPN '", r.get("RPN'", 0)) or 0)
                    rpn_max = max(rpn_max, rpn)
                except (ValueError, TypeError):
                    pass

        nc_count = nc_recent_by_proc.get(processus, 0)

        # Seuils calibres pour l'echelle RPN 3-10
        seuil_rpn = 8
        seuil_nc = 5

        if rpn_max > seuil_rpn or nc_count >= seuil_nc:
            raisons = []
            if rpn_max > seuil_rpn:
                raisons.append(
                    f"RPN residuel max ({rpn_max}) depasse le seuil "
                    f"d'acceptation ({seuil_rpn})"
                )
            if nc_count >= seuil_nc:
                raisons.append(
                    f"{nc_count} NC recentes sur ce processus "
                    f"(seuil: {seuil_nc})"
                )

            alerts.append({
                "source": "Cartographie des risques.xlsx",
                "evidence_source": "Suivi des risques acceptes",
                "process_name": processus,
                "type_risque_regle": "Depassement risque accepte",
                "signal_type": "risque_accepte_depasse",
                "gravite_regle": "elevee",
                "keywords": ["risque accepte", "depassement", "seuil"],
                "snippet": (
                    f"Processus '{processus}' : {risk_count} risques acceptes. "
                    f"Raison(s) d'alerte : {'; '.join(raisons)}. "
                    f"Les conditions d'acceptation initiales ne sont plus "
                    f"reunies, une reevaluation est necessaire."
                ),
                "recommandation_regle": (
                    f"Reevaluer les risques acceptes du processus "
                    f"'{processus}'. Renforcer les mesures de maitrise "
                    f"ou replanifier un traitement dans les 60 jours."
                ),
                "reevaluation_required_regle": True,
            })

    return alerts


# =============================================================================
# 3. DETECTION DE DERIVES D'INDICATEURS
# =============================================================================

def detect_kpi_drift(
    kpis: List[Dict[str, Any]],
    dashboard_pages: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Detecte les derives d'indicateurs dans le dashboard.
    Deduplication par processus : une seule alerte par processus,
    regroupant tous les signaux detectes sur ses pages.
    Seuil : au moins 2 types de signaux differents OU au moins 2 pages
    avec le meme signal pour declencher une alerte.
    """
    alerts = []

    from agents.agent4_monitoring import infer_process_name, extract_snippet

    # Regrouper les signaux par processus
    process_signals = {}  # process -> {signal_types: set, pages: list, snippets: list}

    for page in dashboard_pages:
        text = page.get("combined_text", page.get("text", ""))
        page_num = page.get("page", 0)

        if not text:
            continue

        text_lower = text.lower()

        # Patterns de derive
        drift_signals = []
        if "objectif non atteint" in text_lower or "pas atteint" in text_lower:
            drift_signals.append("objectif_non_atteint")
        if re.search(r"en\s+baisse", text_lower):
            drift_signals.append("tendance_baisse")
        if "seuil critique" in text_lower or "hors limite" in text_lower:
            drift_signals.append("seuil_depasse")

        if drift_signals:
            process_raw = infer_process_name(page_num)
            process = normalize_process_name(process_raw) or process_raw

            if process not in process_signals:
                process_signals[process] = {
                    "signal_types": set(),
                    "pages": [],
                    "snippets": [],
                }

            process_signals[process]["signal_types"].update(drift_signals)
            process_signals[process]["pages"].append(page_num)
            snippet = extract_snippet(text, ["derive", "baisse", "cible",
                                              "seuil", "objectif"])
            if snippet:
                process_signals[process]["snippets"].append(snippet)

    # Generer une alerte par processus si le signal est significatif
    for process, data in process_signals.items():
        nb_types = len(data["signal_types"])
        nb_pages = len(data["pages"])

        # Seuil : au moins 2 types de signaux OU au moins 2 pages concernees
        if nb_types >= 2 or nb_pages >= 2:
            best_snippet = max(data["snippets"], key=len) if data["snippets"] else ""
            all_signals = sorted(data["signal_types"])

            alerts.append({
                "source": "Dashboard PDF",
                "evidence_source": "Detection de derive d'indicateur",
                "process_name": process,
                "type_risque_regle": "Derive indicateur",
                "signal_type": "kpi_drift",
                "gravite_regle": "elevee" if "seuil_depasse" in data["signal_types"]
                else "moyenne",
                "keywords": ["derive", "indicateur", "kpi"] + all_signals,
                "snippet": (
                    f"Derive detectee sur {nb_pages} pages (pages "
                    f"{', '.join(str(p) for p in data['pages'][:5])}). "
                    f"Signaux : {', '.join(all_signals)}. "
                    f"{best_snippet[:300]}"
                )[:500],
                "recommandation_regle": (
                    "Analyser la derive detectee, comparer avec les "
                    "periodes precedentes et declencher une revue du "
                    "KPI concerne. Si la derive persiste, planifier "
                    "un audit cible sous 30 jours."
                ),
                "reevaluation_required_regle": True,
            })

    return alerts


# =============================================================================
# 4. DETECTION D'OPPORTUNITES
# =============================================================================

def detect_opportunities(
    nc_list: List[Dict[str, Any]],
    reclamations: List[Dict[str, Any]],
    cartographie: List[Dict[str, Any]],
    dashboard_pages: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Detecte les opportunites d'amelioration basees sur les processus officiels.
    """
    opportunities = []

    # --- Opp 1 : Processus sans NC recente ---
    # Utiliser les processus OFFICIELS, pas les valeurs brutes de NC.xlsx
    proc_with_recent_nc = set()
    for nc in nc_list:
        proc = extract_nc_process(nc)
        if proc and is_recent(nc.get("Detectee le", nc.get("Date")), 12):
            proc_with_recent_nc.add(proc)

    for proc in OFFICIAL_PROCESSES:
        if proc not in proc_with_recent_nc:
            opportunities.append({
                "source": "NC.xlsx + Cartographie",
                "evidence_source": "Detection d'opportunites",
                "process_name": proc,
                "type_signal": "opportunite",
                "type_risque_regle": "Opportunite - Excellence operationnelle",
                "signal_type": "opportunite",
                "gravite_regle": "faible",
                "keywords": ["opportunite", "excellence", "benchmark"],
                "snippet": (
                    f"Le processus '{proc}' n'a enregistre aucune NC "
                    f"au cours des 12 derniers mois. Opportunite de "
                    f"capitaliser sur ses bonnes pratiques et de les "
                    f"deployer vers d'autres processus."
                ),
                "recommandation_regle": (
                    f"Documenter les bonnes pratiques du processus "
                    f"'{proc}' et realiser un benchmark interne sous "
                    f"3 mois. Partager en revue de direction."
                ),
                "reevaluation_required_regle": False,
            })

    # --- Opp 2 : Risques avec RPN ameliore (initial > residuel) ---
    for entry in cartographie:
        try:
            rpn_i = float(entry.get("RPN", 0) or 0)
            rpn_r = float(entry.get("RPN '", entry.get("RPN'", 0)) or 0)
        except (ValueError, TypeError):
            continue

        if rpn_i > 0 and rpn_r > 0 and rpn_r < rpn_i * 0.7:
            processus = normalize_process_name(
                str(entry.get("Processus", "")).strip()
            )
            intitule = str(entry.get("Intitule",
                                     entry.get("Risques", ""))).strip()
            reduction = round((1 - rpn_r / rpn_i) * 100)
            opportunities.append({
                "source": "Cartographie des risques.xlsx",
                "evidence_source": "Detection d'opportunites",
                "process_name": processus or "Non precise",
                "type_signal": "opportunite",
                "type_risque_regle": "Opportunite - Amelioration confirmee",
                "signal_type": "opportunite",
                "gravite_regle": "faible",
                "keywords": ["opportunite", "amelioration", "rpn"],
                "snippet": (
                    f"Risque '{intitule[:80]}' : RPN ameliore de {rpn_i} "
                    f"a {rpn_r} (reduction de {reduction}%). Les mesures "
                    f"de maitrise sont efficaces."
                ),
                "recommandation_regle": (
                    f"Capitaliser sur cette amelioration. Documenter les "
                    f"actions efficaces et envisager d'alleger la "
                    f"surveillance si pertinent."
                ),
                "reevaluation_required_regle": False,
            })

    # --- Opp 3 : Taux de cloture NC eleve ---
    nc_closed = sum(
        1 for nc in nc_list
        if any(x in str(nc.get("Etat", "")).lower()
               for x in ["clot", "clos", "realis", "ferme"])
    )
    if nc_list and nc_closed / len(nc_list) >= 0.8:
        taux = round(nc_closed / len(nc_list) * 100, 1)
        opportunities.append({
            "source": "NC.xlsx",
            "evidence_source": "Detection d'opportunites",
            "process_name": "Multi-processus",
            "type_signal": "opportunite",
            "type_risque_regle": "Opportunite - Efficacite traitement NC",
            "signal_type": "opportunite",
            "gravite_regle": "faible",
            "keywords": ["opportunite", "nc", "cloture", "efficacite"],
            "snippet": (
                f"Taux de cloture des NC : {taux}% "
                f"({nc_closed}/{len(nc_list)}). Le systeme de traitement "
                f"des non-conformites est efficace. Opportunite de "
                f"valoriser cette performance en revue de direction."
            ),
            "recommandation_regle": (
                "Valoriser ce taux en revue de direction. Analyser les "
                "facteurs de succes et les partager avec les processus "
                "moins performants."
            ),
            "reevaluation_required_regle": False,
        })

    # --- Opp 4 : Tendances positives dans le dashboard ---
    positive_keywords = [
        "amelioration", "progres", "objectif atteint",
        "conforme", "performance"
    ]
    negative_excludes = [
        "non atteint", "pas atteint", "insatisf",
        "baisse", "retard", "probleme", "panne"
    ]

    detected_pages = set()
    for page in dashboard_pages:
        text = page.get("combined_text", page.get("text", ""))
        text_lower = text.lower() if text else ""
        page_num = page.get("page", 0)

        matched_pos = [kw for kw in positive_keywords if kw in text_lower]
        has_negative = any(neg in text_lower for neg in negative_excludes)

        if len(matched_pos) >= 2 and not has_negative:
            from agents.agent4_monitoring import infer_process_name
            process_raw = infer_process_name(page_num)
            process = normalize_process_name(process_raw) or process_raw

            # Eviter les doublons par processus
            if process in detected_pages:
                continue
            detected_pages.add(process)

            opportunities.append({
                "source": "Dashboard PDF",
                "evidence_source": "Detection d'opportunites",
                "process_name": process,
                "type_signal": "opportunite",
                "type_risque_regle": "Opportunite - Tendance positive",
                "signal_type": "opportunite",
                "gravite_regle": "faible",
                "keywords": ["opportunite"] + matched_pos[:3],
                "snippet": (
                    f"Signaux positifs detectes pour '{process}' : "
                    f"{', '.join(matched_pos[:3])}. Opportunite de "
                    f"renforcer et capitaliser."
                ),
                "recommandation_regle": (
                    "Documenter les facteurs de succes et envisager "
                    "l'extension des bonnes pratiques."
                ),
                "reevaluation_required_regle": False,
            })

    return opportunities


# =============================================================================
# 5. CAPITALISATION (SCHEMAS RECURRENTS)
# =============================================================================

def detect_recurring_patterns(
    nc_list: List[Dict[str, Any]],
    reclamations: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Identifie les schemas recurrents."""
    alerts = []

    # --- NC recurrentes par categorie ---
    nc_categories = Counter()
    for nc in nc_list:
        cat = str(nc.get("Categorie NC",
                         nc.get("Type", ""))).strip()
        if cat and cat.lower() not in ["none", ""]:
            nc_categories[cat] += 1

    for cat, count in nc_categories.most_common(5):
        if count >= 8:
            alerts.append({
                "source": "NC.xlsx",
                "evidence_source": "Capitalisation - Schemas recurrents",
                "process_name": "Multi-processus",
                "type_risque_regle": "Schema recurrent NC",
                "signal_type": "capitalisation",
                "gravite_regle": "moyenne",
                "keywords": ["recurrence", "nc", "pattern", "capitalisation"],
                "snippet": (
                    f"La categorie NC '{cat}' revient {count} fois. "
                    f"Ce schema recurrent necessite une action preventive "
                    f"globale plutot que des corrections ponctuelles."
                ),
                "recommandation_regle": (
                    f"Analyse des causes profondes de la categorie '{cat}'. "
                    f"Definir une action preventive standardisee et la "
                    f"documenter comme action reutilisable sous 60 jours."
                ),
                "reevaluation_required_regle": True,
                "pattern_type": "nc_recurrent",
                "pattern_count": count,
            })

    # --- Reclamations recurrentes par nature ---
    rec_natures = Counter()
    for rec in reclamations:
        nature = str(rec.get("Nature", "")).strip()
        if nature and nature.lower() not in ["none", ""]:
            rec_natures[nature] += 1

    for nature, count in rec_natures.most_common(5):
        if count >= 8:
            alerts.append({
                "source": "Reclamations clients.xlsx",
                "evidence_source": "Capitalisation - Schemas recurrents",
                "process_name": "PROCESSUS DEVELOPPEMENT COMMERCIAL ET RELATIONS CLIENTS",
                "type_risque_regle": "Schema recurrent reclamations",
                "signal_type": "capitalisation",
                "gravite_regle": "moyenne",
                "keywords": ["recurrence", "reclamation", "capitalisation"],
                "snippet": (
                    f"La nature de reclamation '{nature}' revient {count} "
                    f"fois. Action preventive standardisee recommandee."
                ),
                "recommandation_regle": (
                    f"Plan d'action preventif pour les reclamations "
                    f"de type '{nature}'. Documenter comme action "
                    f"reutilisable."
                ),
                "reevaluation_required_regle": True,
                "pattern_type": "reclamation_recurrent",
                "pattern_count": count,
            })

    # --- Pics temporels NC ---
    nc_by_month = Counter()
    for nc in nc_list:
        date = parse_date(nc.get("Detectee le", nc.get("Date")))
        if date:
            key = f"{date.year}-{date.month:02d}"
            nc_by_month[key] += 1

    if nc_by_month:
        avg_nc_month = sum(nc_by_month.values()) / len(nc_by_month)
        for month, count in nc_by_month.most_common(3):
            if count > avg_nc_month * 2.5 and count >= 8:
                alerts.append({
                    "source": "NC.xlsx",
                    "evidence_source": "Capitalisation - Analyse temporelle",
                    "process_name": "Multi-processus",
                    "type_risque_regle": "Pic temporel NC",
                    "signal_type": "capitalisation_temporelle",
                    "gravite_regle": "moyenne",
                    "keywords": ["pic", "temporel", "nc", "saisonnier"],
                    "snippet": (
                        f"Pic de {count} NC en {month} (moyenne: "
                        f"{round(avg_nc_month, 1)}). Facteur saisonnier "
                        f"ou evenement ponctuel a anticiper."
                    ),
                    "recommandation_regle": (
                        f"Analyser les causes du pic NC de {month}. "
                        f"Si saisonnier, planifier des actions preventives "
                        f"pour les periodes similaires futures."
                    ),
                    "reevaluation_required_regle": True,
                    "pattern_type": "temporal_peak",
                    "pattern_month": month,
                    "pattern_count": count,
                })

    return alerts


# =============================================================================
# 6. DETECTION NC (avec conscience temporelle)
# =============================================================================

def detect_nc_alerts(nc_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detecte les alertes NC avec conscience temporelle."""
    alerts = []

    if not nc_list:
        return alerts

    # NC ouvertes recentes par processus
    nc_open_by_proc = Counter()
    for nc in nc_list:
        etat = str(nc.get("Etat", "")).strip().lower()
        if "realis" in etat or "clot" in etat or "clos" in etat:
            continue
        proc = extract_nc_process(nc)
        if proc and is_recent(nc.get("Detectee le", nc.get("Date")), 18):
            nc_open_by_proc[proc] += 1

    for process, count in nc_open_by_proc.items():
        if count >= 3:
            alerts.append({
                "source": "NC.xlsx",
                "evidence_source": "Registre des non-conformites",
                "process_name": process,
                "type_risque_regle": "Risque qualite",
                "signal_type": "risque",
                "gravite_regle": "elevee" if count >= 8 else "moyenne",
                "keywords": ["non conformite", "nc", "ouvertes"],
                "snippet": (
                    f"Le processus '{process}' a {count} NC ouvertes "
                    f"recentes (18 derniers mois) necessitant un traitement."
                ),
                "recommandation_regle": (
                    f"Prioriser la cloture des NC ouvertes du processus "
                    f"'{process}'. Analyse des causes racines sous 30 jours."
                ),
                "reevaluation_required_regle": True,
            })

    return alerts


# =============================================================================
# 7. DETECTION RECLAMATIONS (avec conscience temporelle)
# =============================================================================

def detect_reclamation_alerts(
    recs: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Detecte les alertes reclamations avec conscience temporelle."""
    alerts = []

    if not recs:
        return alerts

    # Reclamations recentes ouvertes
    recs_recentes = [r for r in recs if is_recent(r.get("Date"), 12)]
    recs_ouvertes = [
        r for r in recs_recentes
        if not any(x in str(r.get("Etat", "")).lower()
                   for x in ["clot", "clos", "realis", "ferme"])
    ]

    if len(recs_ouvertes) > 5:
        alerts.append({
            "source": "Reclamations clients.xlsx",
            "evidence_source": "Registre des reclamations clients",
            "process_name": "PROCESSUS DEVELOPPEMENT COMMERCIAL ET RELATIONS CLIENTS",
            "type_risque_regle": "Risque client",
            "signal_type": "risque",
            "gravite_regle": "elevee" if len(recs_ouvertes) > 10 else "moyenne",
            "keywords": ["reclamation", "insatisfaction", "ouvertes"],
            "snippet": (
                f"{len(recs_ouvertes)} reclamations recentes ouvertes "
                f"(< 12 mois) sur {len(recs_recentes)} recentes "
                f"et {len(recs)} au total."
            ),
            "recommandation_regle": (
                "Prioriser le traitement des reclamations ouvertes. "
                "Analyse des tendances sous 15 jours."
            ),
            "reevaluation_required_regle": True,
        })

    # Par produit (recentes)
    by_product = Counter()
    for rec in recs_recentes:
        produit = str(rec.get("Produits / Services", "")).strip()
        if produit and produit.lower() != "none":
            by_product[produit] += 1

    for produit, count in by_product.most_common(3):
        if count >= 5:
            alerts.append({
                "source": "Reclamations clients.xlsx",
                "evidence_source": "Registre des reclamations clients",
                "process_name": "PROCESSUS DEVELOPPEMENT COMMERCIAL ET RELATIONS CLIENTS",
                "type_risque_regle": "Risque client",
                "signal_type": "risque",
                "gravite_regle": "elevee" if count >= 10 else "moyenne",
                "keywords": ["reclamation", "produit", "recurrence"],
                "snippet": (
                    f"Le produit '{produit}' cumule {count} reclamations "
                    f"recentes."
                ),
                "recommandation_regle": (
                    f"Analyser les causes des reclamations sur '{produit}'. "
                    f"Verifier le processus de production."
                ),
                "reevaluation_required_regle": True,
            })

    return alerts


# =============================================================================
# AGGREGATION MULTI-SOURCES
# =============================================================================

def detect_all_excel_alerts(
    context: Dict[str, Any],
    dashboard_pages: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """Lance la detection complete sur toutes les sources."""
    all_alerts = []
    pages = dashboard_pages or []

    nc = context.get("nc", [])
    recs = context.get("reclamations", [])
    carto = context.get("cartographie", [])
    kpis = context.get("kpis", [])

    # Risques
    all_alerts.extend(detect_nc_alerts(nc))
    all_alerts.extend(detect_reclamation_alerts(recs))

    # Comparaison residuel vs observe
    all_alerts.extend(detect_residual_vs_observed(carto, nc, recs))

    # Suivi risques acceptes
    all_alerts.extend(detect_accepted_risk_alerts(carto, nc))

    # Derives indicateurs
    all_alerts.extend(detect_kpi_drift(kpis, pages))

    # Capitalisation
    all_alerts.extend(detect_recurring_patterns(nc, recs))

    # Identifier les processus ayant des signaux de risque
    processes_with_risks = set()
    for a in all_alerts:
        if a.get("signal_type") not in ("opportunite", "capitalisation",
                                          "capitalisation_temporelle"):
            proc = a.get("process_name", "")
            if proc:
                processes_with_risks.add(proc)

    # Opportunites (filtrees : pas d'excellence sur un processus en alerte)
    raw_opportunities = detect_opportunities(nc, recs, carto, pages)
    for opp in raw_opportunities:
        opp_type = opp.get("type_risque_regle", "")
        opp_proc = opp.get("process_name", "")
        # Exclure "Excellence operationnelle" si le processus a des risques
        if "Excellence" in opp_type and opp_proc in processes_with_risks:
            continue
        all_alerts.append(opp)

    return all_alerts
