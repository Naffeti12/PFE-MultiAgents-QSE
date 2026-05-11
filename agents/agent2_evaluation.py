"""
Agent IA 2 - Analyse & Evaluation des Risques & Opportunites.
Conforme au cahier des charges QALITAS QSE :

Logique metier :
  - Etape 0 : Detection du mode d'evaluation (A/B/C/D)
  - Etape 1 : Calcul du score brut (F x G)
  - Etape 2 : Analyse des mesures de maitrise (indice de maitrise)
  - Etape 3 : Calcul du risque residuel
  - Etape 4 : Evaluation des opportunites (valeur + faisabilite)
  - Etape 5 : Priorisation et statut decisionnelFormule entreprise : F * G (Frequence * Gravite)
Echelle : 1-5 pour F et G => RPN de 1 a 25
Seuils (calibres sur les donnees) :
  Critique  : RPN >= 12
  Eleve     : RPN >= 8
  Moyen     : RPN >= 4
  Mineur    : RPN < 4
"""

import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


# =============================================================================
# CONSTANTES ET BAREMES
# =============================================================================

# Seuils RPN (echelle F*G, max 25)
RPN_SEUILS = {
    "critique": 12,
    "eleve":     8,
    "moyen":     4,
    "mineur":    1,
}

# Statuts decisionnels selon le RPN residuel
STATUT_DECISIONNELS = {
    "critique": "A traiter immediatement",
    "eleve":    "A traiter - planifie",
    "moyen":    "A surveiller",
    "mineur":   "Acceptable",
}

# Strategies de traitement recommandees par niveau
STRATEGIES_RISQUE = {
    "critique": "Reduire (renforcement urgent des controles)",
    "eleve":    "Reduire / Transferer",
    "moyen":    "Reduire / Accepter sous surveillance",
    "mineur":   "Accepter (avec surveillance periodique)",
}

STRATEGIES_OPPORTUNITE = {
    "fort":   "Exploiter (action immediate)",
    "moyen":  "Renforcer (investissement cible)",
    "faible": "Anticiper / Experimenter",
}

# Modes d'evaluation
MODE_A = "A - Reprise telle quelle"
MODE_B = "B - Mise a jour incrementale (delta)"
MODE_C = "C - Recalibrage / harmonisation"
MODE_D = "D - Reevaluation complete"


# =============================================================================
# UTILITAIRES
# =============================================================================

def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.category(c).startswith("M"))


def normalize(text: str) -> str:
    return strip_accents(text).lower().strip()


def parse_date(value: Any) -> Optional[datetime]:
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


def is_recent(date_val: Any, months: int = 18) -> bool:
    dt = parse_date(date_val)
    if not dt:
        return False
    cutoff = datetime.now() - timedelta(days=months * 30)
    return dt >= cutoff


def parse_appreciation(appreciation: str) -> Tuple[int, int]:
    """
    Parse une appreciation du type '2 ( F ) , 5 ( G )' ou '1(F),3(G)'.
    Retourne (frequence, gravite).
    """
    if not appreciation:
        return 0, 0
    text = str(appreciation).replace("\n", " ")
    # Extraire tous les nombres
    numbers = re.findall(r"\d+", text)
    if len(numbers) >= 2:
        return int(numbers[0]), int(numbers[1])
    return 0, 0


def rpn_niveau(rpn: float) -> str:
    """Retourne le niveau de criticite selon le RPN."""
    if rpn >= RPN_SEUILS["critique"]:
        return "critique"
    if rpn >= RPN_SEUILS["eleve"]:
        return "eleve"
    if rpn >= RPN_SEUILS["moyen"]:
        return "moyen"
    return "mineur"


# =============================================================================
# ETAPE 0 : DETECTION DU MODE D'EVALUATION
# =============================================================================

def detect_evaluation_mode(entry: Dict[str, Any], nc_count: int) -> Tuple[str, str]:
    """
    Detecte le mode d'evaluation approprie selon le CdC :
    A - Reprise    : evaluation recente, methode conforme, donnees stables
    B - Delta      : nouvelles donnees (NC, KPI) mais methode inchangee
    C - Recalibrage: echelles incoherentes ou donnees manquantes
    D - Complet    : changement majeur, derive forte, donnees obsoletes

    Retourne (mode, justification).
    """
    eval_date = parse_date(entry.get("Date"))
    etat = normalize(str(entry.get("Etat", "")))
    rpn = float(entry.get("RPN", 0) or 0)
    rpn_r = float(entry.get("RPN '", entry.get("RPN'", 0)) or 0)
    decision = normalize(str(entry.get("Décision", entry.get("Decision", ""))))
    appreciation = str(entry.get("Appréciation", ""))

    # Pas d'evaluation existante
    if not eval_date and rpn == 0:
        return MODE_D, "Aucune evaluation anterieure detectee. Evaluation complete requise."

    # Donnees manquantes critiques
    if not appreciation or rpn == 0:
        return MODE_C, "Appreciation ou RPN manquant. Recalibrage necessaire."

    # Evaluation recente (< 12 mois) et stable
    if eval_date and is_recent(eval_date, months=12):
        if nc_count == 0:
            return MODE_A, (
                f"Evaluation recente ({eval_date.strftime('%Y-%m-%d') if eval_date else 'N/A'}), "
                f"methode F*G conforme, aucune NC recente. Reprise telle quelle justifiee."
            )
        if nc_count < 3:
            return MODE_B, (
                f"Evaluation recente mais {nc_count} NC recentes detectees. "
                f"Mise a jour incrementale du score residuel."
            )
        return MODE_B, (
            f"{nc_count} NC recentes sur ce processus. "
            f"Mise a jour incrementale - recalcul du residuel requis."
        )

    # Evaluation ancienne (> 12 mois) ou derive significative
    if eval_date and not is_recent(eval_date, months=12):
        if nc_count >= 5:
            return MODE_D, (
                f"Evaluation datant de plus de 12 mois "
                f"({eval_date.strftime('%Y-%m-%d') if eval_date else 'N/A'}) "
                f"et {nc_count} NC recentes. Reevaluation complete requise."
            )
        return MODE_C, (
            f"Evaluation datant de plus de 12 mois. "
            f"Recalibrage pour harmonisation avec les donnees actuelles."
        )

    # Cas par defaut : mise a jour si NC existent
    if nc_count >= 3:
        return MODE_B, f"{nc_count} NC recentes. Mise a jour incrementale."

    return MODE_A, "Donnees stables, reprise de l'evaluation existante."


# =============================================================================
# ETAPE 1 : CALCUL DU SCORE BRUT
# =============================================================================

def compute_score_brut(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calcule le score brut a partir de l'appreciation (F * G).
    Utilise les valeurs de la cartographie si disponibles,
    sinon estime depuis le RPN total.
    """
    appreciation = str(entry.get("Appréciation", entry.get("Appreciation", "")))
    frequence, gravite = parse_appreciation(appreciation)

    rpn_existing = float(entry.get("RPN", 0) or 0)

    if frequence > 0 and gravite > 0:
        score_brut = frequence * gravite
        methode = f"F({frequence}) x G({gravite}) = {score_brut}"
    elif rpn_existing > 0:
        # Retroengineering : on utilise le RPN existant
        score_brut = rpn_existing
        frequence = 0
        gravite = 0
        methode = f"RPN existant utilise directement : {score_brut}"
    else:
        score_brut = 0
        methode = "Score brut indetermine - donnees manquantes"

    niveau = rpn_niveau(score_brut)

    return {
        "frequence": frequence,
        "gravite": gravite,
        "score_brut": score_brut,
        "niveau_brut": niveau,
        "methode_brut": methode,
        "score_min_acceptable": entry.get("Score Min..", 0),
        "score_max_acceptable": entry.get("Score Max..", 0),
    }


# =============================================================================
# ETAPE 2 : ANALYSE DES MESURES DE MAITRISE
# =============================================================================

def compute_indice_maitrise(
    entry: Dict[str, Any],
    nc_count: int,
    has_actions: bool,
    kpi_ok: bool
) -> Dict[str, Any]:
    """
    Evalue la qualite des mesures de maitrise sur une echelle 0-3 :
    0 = aucune maitrise
    1 = maitrise faible (controles existants mais insuffisants)
    2 = maitrise moyenne (controles adequats, quelques lacunes)
    3 = maitrise forte (controles robustes, preuves d'efficacite)

    Criteres evalues :
    - Existence de la decision de traitement
    - NC recentes (temoignent de l'efficacite reelle)
    - Actions en cours
    - KPIs en bonne sante
    - Commentaires (temoignent d'un suivi actif)
    """
    score = 0
    details = []

    decision = normalize(str(entry.get("Décision", entry.get("Decision", ""))))
    commentaires = str(entry.get("Commentaires", "")).strip()
    rpn_r = float(entry.get("RPN '", entry.get("RPN'", 0)) or 0)
    rpn_brut = float(entry.get("RPN", 0) or 0)

    # Critere 1 : decision de traitement definie
    if "accepter" in decision or "diminuer" in decision:
        score += 1
        details.append("Decision de traitement definie")

    # Critere 2 : efficacite observee (reduction du RPN)
    if rpn_brut > 0 and rpn_r < rpn_brut:
        score += 1
        reduction = round((1 - rpn_r / rpn_brut) * 100)
        details.append(f"Reduction RPN observee : {rpn_brut} -> {rpn_r} ({reduction}%)")
    elif rpn_brut > 0 and rpn_r == rpn_brut:
        details.append("Aucune reduction RPN observee")

    # Critere 3 : absence de NC recentes (preuve d'efficacite)
    if nc_count == 0:
        score += 1
        details.append("Aucune NC recente : maitrise efficace")
    elif nc_count < 3:
        details.append(f"{nc_count} NC recentes : maitrise partielle")
    else:
        details.append(f"{nc_count} NC recentes : maitrise insuffisante")

    # Critere 4 : suivi actif (commentaires documentes)
    if commentaires and len(commentaires) > 10:
        score = min(3, score + 0.5)
        details.append("Suivi documente dans les commentaires")

    # Normaliser sur 3
    indice = min(3.0, round(score, 1))

    if indice >= 2.5:
        niveau = "fort"
        label = "Maitrise forte"
    elif indice >= 1.5:
        niveau = "moyen"
        label = "Maitrise moyenne"
    elif indice >= 0.5:
        niveau = "faible"
        label = "Maitrise faible"
    else:
        niveau = "absent"
        label = "Maitrise absente ou non evaluable"

    return {
        "indice_maitrise": indice,
        "niveau_maitrise": niveau,
        "label_maitrise": label,
        "details_maitrise": details,
    }


# =============================================================================
# ETAPE 3 : CALCUL DU RISQUE RESIDUEL
# =============================================================================

def compute_score_residuel(
    entry: Dict[str, Any],
    score_brut_data: Dict[str, Any],
    maitrise_data: Dict[str, Any],
    mode: str,
    nc_count: int
) -> Dict[str, Any]:
    """
    Calcule le score residuel selon le mode d'evaluation :
    - Mode A : reprise directe du RPN residuel existant
    - Mode B : ajustement du residuel existant selon NC recentes
    - Mode C/D : recalcul complet base sur brut et indice de maitrise
    """
    rpn_r_existant = float(entry.get("RPN '", entry.get("RPN'", 0)) or 0)
    score_brut = score_brut_data["score_brut"]
    indice = maitrise_data["indice_maitrise"]
    appreciation_r = str(entry.get("Appréciation'", entry.get("Appreciation'", "")))

    if mode == MODE_A:
        # Reprise du residuel existant sans modification
        score_residuel = rpn_r_existant
        methode = f"Mode A : reprise du RPN residuel existant ({rpn_r_existant})"

    elif mode == MODE_B:
        # Ajustement incremental : penalite si NC recentes
        penalite = min(2, nc_count * 0.4)
        score_residuel = round(min(score_brut, rpn_r_existant + penalite), 1)
        methode = (
            f"Mode B : RPN residuel {rpn_r_existant} + "
            f"penalite NC ({nc_count} NC -> +{penalite:.1f}) = {score_residuel}"
        )

    else:
        # Mode C ou D : recalcul depuis le brut et l'indice de maitrise
        if score_brut > 0 and indice > 0:
            # Facteur de reduction : plus la maitrise est forte, plus le residuel est bas
            facteur_reduction = indice / 3.0
            score_residuel = round(score_brut * (1 - facteur_reduction * 0.6), 1)
            score_residuel = max(1, min(score_brut, score_residuel))
        elif rpn_r_existant > 0:
            score_residuel = rpn_r_existant
        else:
            score_residuel = score_brut

        methode = (
            f"Mode {mode[0]} : brut({score_brut}) x (1 - {indice}/3 x 0.6) "
            f"= {score_residuel}"
        )

    # Ajuster selon les limites definies dans la cartographie
    score_min = entry.get("Score Min..", 0) or 0
    score_max = entry.get("Score Max..", 0) or 0
    if score_min and score_max and score_residuel:
        if score_residuel < score_min or score_residuel > score_max:
            note_seuils = (
                f"Score residuel {score_residuel} hors de la plage "
                f"acceptable [{score_min}-{score_max}]"
            )
        else:
            note_seuils = f"Score dans la plage acceptable [{score_min}-{score_max}]"
    else:
        note_seuils = ""

    niveau_residuel = rpn_niveau(score_residuel)
    statut = STATUT_DECISIONNELS.get(niveau_residuel, "A surveiller")
    strategie = STRATEGIES_RISQUE.get(niveau_residuel, "A definir")

    return {
        "score_residuel": score_residuel,
        "niveau_residuel": niveau_residuel,
        "statut_decisionnel": statut,
        "strategie_recommandee": strategie,
        "methode_residuel": methode,
        "note_seuils": note_seuils,
        "delta_brut_residuel": round(score_brut - score_residuel, 1),
        "efficacite_maitrise_pct": (
            round((1 - score_residuel / score_brut) * 100)
            if score_brut > 0 else 0
        ),
    }


# =============================================================================
# ETAPE 4 : EVALUATION DES OPPORTUNITES
# =============================================================================

def evaluate_opportunity(
    entry: Dict[str, Any],
    context: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Identifie et evalue les opportunites associees aux risques maîtrises.
    Une opportunite est detectable quand :
    - Le RPN residuel est nettement inferieur au brut (maitrise efficace)
    - Ou quand la decision est de capitaliser sur une bonne pratique

    Evalue selon : valeur attendue + faisabilite + alignement strategique.
    """
    rpn_brut = float(entry.get("RPN", 0) or 0)
    rpn_r = float(entry.get("RPN '", entry.get("RPN'", 0)) or 0)
    decision = normalize(str(entry.get("Décision", entry.get("Decision", ""))))
    commentaires = str(entry.get("Commentaires", "")).strip()
    processus = str(entry.get("Processus", "")).strip()
    risque = str(entry.get("Risques", "")).strip()

    # Condition : reduction significative du RPN (>= 30%) => opportunite de capitaliser
    if rpn_brut <= 0 or rpn_r <= 0:
        return None
    reduction_pct = (1 - rpn_r / rpn_brut) * 100
    if reduction_pct < 30:
        return None

    # Valeur : proportionnelle a la reduction obtenue
    if reduction_pct >= 70:
        valeur = "elevee"
        valeur_score = 3
    elif reduction_pct >= 50:
        valeur = "moyenne"
        valeur_score = 2
    else:
        valeur = "faible"
        valeur_score = 1

    # Faisabilite : si commentaires documentes => faisabilite prouvee
    faisabilite_score = 2 if commentaires else 1

    # Alignement strategique : base sur le type de risque
    type_r = normalize(str(entry.get("Type", "")))
    if "strategique" in type_r:
        alignement_score = 3
    elif "operationnel" in type_r:
        alignement_score = 2
    else:
        alignement_score = 1

    # Indice de priorite opportunite
    iop = round((valeur_score + faisabilite_score + alignement_score) / 3, 1)

    niveau_opp = "fort" if iop >= 2.5 else "moyen" if iop >= 1.5 else "faible"
    strategie = STRATEGIES_OPPORTUNITE.get(niveau_opp, "Anticiper")

    return {
        "type_signal": "opportunite",
        "processus": processus,
        "risque_source": risque[:100],
        "reduction_rpn_pct": round(reduction_pct),
        "valeur_attendue": valeur,
        "faisabilite": "prouvee" if commentaires else "a evaluer",
        "alignement_strategique": type_r or "non precise",
        "indice_priorite_opp": iop,
        "niveau_opportunite": niveau_opp,
        "strategie_opportunite": strategie,
        "benefice_attendu": (
            f"Capitaliser sur la reduction de {round(reduction_pct)}% du RPN "
            f"({rpn_brut} -> {rpn_r}) pour '{risque[:80]}'"
        ),
    }


# =============================================================================
# ETAPE 5 : PRIORISATION FINALE
# =============================================================================

def compute_priority_score(
    score_residuel: float,
    score_brut: float,
    indice_maitrise: float,
    nc_count: int
) -> float:
    """
    Score de priorite composite pour classer les risques.
    Formule : residuel (60%) + brut normalise (20%) + penalite NC (20%)
    """
    residuel_norm = score_residuel / 25  # max theorique F*G = 25
    brut_norm = score_brut / 25
    nc_penalite = min(1.0, nc_count / 10)

    priority = (
        residuel_norm * 0.60
        + brut_norm * 0.20
        + nc_penalite * 0.20
    )
    return round(priority * 10, 2)  # Score sur 10


# =============================================================================
# EVALUATION COMPLETE D'UN RISQUE
# =============================================================================

def evaluate_risk(
    entry: Dict[str, Any],
    nc_list: List[Dict[str, Any]],
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Pipeline d'evaluation complet pour un risque de la cartographie.
    """
    def _s(val, fallback=""):
        """Convertit en str propre : supprime None/nan, balises HTML, espaces."""
        import re as _re
        v = val if val is not None else fallback
        s = str(v).strip()
        # Supprimer les balises HTML (ex: <p><br></p> depuis cellules Excel riches)
        s = _re.sub(r"<[^>]+>", " ", s)
        s = _re.sub(r"\s+", " ", s).strip()
        return "" if s.lower() in ("none", "nan", "") else s

    processus         = _s(entry.get("Processus"))
    risque            = _s(entry.get("Risques"))
    code              = _s(entry.get("Code"))
    type_r            = _s(entry.get("Type"))
    categorie         = _s(entry.get("Catégorie", entry.get("Categorie")))
    causes            = _s(entry.get("Causes"))
    effets            = _s(entry.get("Effets Négatifs", entry.get("Effets Negatifs")))
    decision_initiale = _s(entry.get("Décision", entry.get("Decision")))
    commentaires      = _s(entry.get("Commentaires"))
    systeme           = _s(entry.get("Système", entry.get("Systeme")))
    description       = _s(entry.get("Description"))

    # Compter les NC recentes liees a ce processus
    from agents.agent4_excel_detection import normalize_process_name
    proc_norm = normalize_process_name(processus)
    nc_count = sum(
        1 for nc in nc_list
        if normalize_process_name(str(nc.get("Processus", ""))) == proc_norm
        or normalize_process_name(str(nc.get("Source", ""))) == proc_norm
    )

    # Etape 0 : mode d'evaluation
    mode, justif_mode = detect_evaluation_mode(entry, nc_count)

    # Etape 1 : score brut
    brut_data = compute_score_brut(entry)

    # Etape 2 : indice de maitrise
    has_actions = bool(commentaires)
    kpi_ok = True  # simplifie (sera enrichi par LLM)
    maitrise_data = compute_indice_maitrise(entry, nc_count, has_actions, kpi_ok)

    # Etape 3 : score residuel
    residuel_data = compute_score_residuel(
        entry, brut_data, maitrise_data, mode, nc_count
    )

    # Score de priorite composite
    priority_score = compute_priority_score(
        residuel_data["score_residuel"],
        brut_data["score_brut"],
        maitrise_data["indice_maitrise"],
        nc_count
    )

    # Etape 4 : opportunite associee ?
    opportunite = evaluate_opportunity(entry, context)

    return {
        # Identification
        "code": code,
        "processus": processus,
        "risque": risque,
        "type": type_r,
        "categorie": categorie,
        "systeme": systeme,
        "causes": causes,
        "effets": effets,
        "description": description,
        "decision_initiale": decision_initiale,
        "commentaires": commentaires,

        # Mode d'evaluation
        "mode_evaluation": mode,
        "justification_mode": justif_mode,
        "nc_count_processus": nc_count,

        # Etape 1 : brut
        **brut_data,

        # Etape 2 : maitrise
        **maitrise_data,

        # Etape 3 : residuel
        **residuel_data,

        # Priorisation
        "priority_score": priority_score,

        # Opportunite
        "opportunite_associee": opportunite,

        # Tracabilite
        "date_evaluation_source": str(entry.get("Date", "")),
        "reference_evaluation": str(entry.get("Référence", entry.get("Reference", ""))).strip(),
        "resultat_existant": str(entry.get("Résultat Eval. Risq & Opp", "")).strip(),
        "resultat_residuel_existant": str(entry.get("Résultat Eval. Risq & Opp '", "")).strip(),

        # UUIDs QALITAS : preserve la cle _raw attachee avant evaluation
        # (contient RiskOpportunityId + RiskOpportunityEvaluationId)
        "_raw": entry.get("_raw", {}),
    }


# =============================================================================
# EVALUATION DE TOUTE LA CARTOGRAPHIE
# =============================================================================

def evaluate_all(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evalue l'ensemble des risques de la cartographie.
    Retourne un registre structure avec risques + opportunites + synthese.
    """
    cartographie = context.get("cartographie", [])
    nc_list = context.get("nc", [])

    risques_evalues = []
    opportunites = []
    modes_count = Counter()

    for entry in cartographie:
        if not str(entry.get("Risques", "")).strip():
            continue

        evaluated = evaluate_risk(entry, nc_list, context)
        risques_evalues.append(evaluated)
        modes_count[evaluated["mode_evaluation"]] += 1

        # Collecter les opportunites distinctes
        opp = evaluated.get("opportunite_associee")
        if opp:
            opp["code_source"] = evaluated["code"]
            opp["processus"] = evaluated["processus"]
            opportunites.append(opp)

    # Trier par score de priorite decroissant
    risques_evalues.sort(key=lambda r: r.get("priority_score", 0), reverse=True)
    opportunites.sort(key=lambda o: o.get("indice_priorite_opp", 0), reverse=True)

    # Synthese par niveau
    by_niveau_brut = Counter(r["niveau_brut"] for r in risques_evalues)
    by_niveau_residuel = Counter(r["niveau_residuel"] for r in risques_evalues)
    by_statut = Counter(r["statut_decisionnel"] for r in risques_evalues)
    by_processus = Counter(r["processus"] for r in risques_evalues)
    by_mode = dict(modes_count)

    # Top risques residuels (les plus urgents)
    top_residuels = [
        {
            "code": r["code"],
            "processus": r["processus"],
            "risque": r["risque"][:80],
            "score_residuel": r["score_residuel"],
            "niveau_residuel": r["niveau_residuel"],
            "statut": r["statut_decisionnel"],
            "priority_score": r["priority_score"],
        }
        for r in risques_evalues[:5]
    ]

    # Risques fortement maîtrises (brut eleve, residuel faible)
    fortement_maitrises = [
        r for r in risques_evalues
        if r["score_brut"] >= 6 and r["score_residuel"] <= 3
        and r["efficacite_maitrise_pct"] >= 40
    ]

    return {
        "risques": risques_evalues,
        "opportunites": opportunites,
        "summary": {
            "total_risques": len(risques_evalues),
            "total_opportunites": len(opportunites),
            "by_niveau_brut": dict(by_niveau_brut),
            "by_niveau_residuel": dict(by_niveau_residuel),
            "by_statut": dict(by_statut),
            "by_processus": dict(by_processus),
            "by_mode_evaluation": by_mode,
            "top_5_residuels": top_residuels,
            "nb_fortement_maitrises": len(fortement_maitrises),
            "a_traiter_immediat": by_statut.get("A traiter immediatement", 0),
            "a_traiter_planifie": by_statut.get("A traiter - planifie", 0),
            "a_surveiller": by_statut.get("A surveiller", 0),
            "acceptable": by_statut.get("Acceptable", 0),
        },
        "fortement_maitrises": fortement_maitrises[:5],
    }
