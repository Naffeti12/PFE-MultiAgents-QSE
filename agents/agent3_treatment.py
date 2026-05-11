"""
Agent IA 3 - Generation des Actions de Traitement des Risques & Opportunites.
==============================================================================
Conforme au cahier des charges TBR.TIM.IA.S5/2025-2026, Agent IA 3.

Mission :
    Generer, structurer et prioriser des actions de traitement adaptees
    pour reduire/maitriser les risques (sur la base du risque residuel)
    et exploiter les opportunites, en coherence avec la strategie QHSE.

Pipeline interne :
    Etape 1 : Filtrage des R&O necessitant un traitement
    Etape 2 : Choix de la strategie (Eviter/Reduire/Transferer/Accepter
              ou Exploiter/Renforcer/Anticiper/Experimenter)
    Etape 3 : Generation des actions candidates (regles + LLM)
    Etape 4 : Estimation efficacite / effort -> indice de priorite
    Etape 5 : Structuration et sequencement du plan de traitement
    Etape 6 : Validation et preparation injection QALITAS

Separation des responsabilites :
    agent3_treatment.py  : logique metier pure (ce fichier)
    agent3_main.py       : pipeline orchestration + injection QALITAS
"""

import logging
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("agent3.treatment")


# =============================================================================
# BAREMES ET REFERENTIELS
# =============================================================================

# Ordre de severite des niveaux residuels
NIVEAUX_ORDRE = ["mineur", "moyen", "eleve", "critique"]

# Seuil minimum pour generer un plan de traitement
# (les risques "mineur" + statut "Acceptable" sont filtres par defaut)
SEUIL_TRAITEMENT_DEFAUT = "moyen"

# ---
# Matrice de strategie risque : niveau residuel -> strategie CDC
# ---
STRATEGIE_RISQUE = {
    "critique": "Reduire",      # Traitement obligatoire, renforcement urgent
    "eleve":    "Reduire",      # Traitement prioritaire
    "moyen":    "Reduire",      # Traitement planifie
    "mineur":   "Accepter",     # Acceptation justifiee + surveillance
}

# Surcharge possible si le score brut est tres eleve mais residuel faible
# => risque "fortement controle" -> strategie Surveiller meme si niveau mineur
STRATEGIE_RISQUE_FORT_CONTROLE = "Accepter"  # avec surveillance renforcee

# ---
# Matrice de strategie opportunite : niveau -> strategie CDC
# ---
STRATEGIE_OPPORTUNITE = {
    "fort":     "Exploiter",     # Action immediate, ressources allouees
    "moyen":    "Renforcer",     # Investissement cible
    "faible":   "Anticiper",     # Preparation future
}

# ---
# Catalogue d'actions par strategie (base regle, CDC p.13)
# Chaque entree : (designation_template, type_action, echeance_jours)
# ---
CATALOGUE_ACTIONS: Dict[str, List[Tuple[str, str, int]]] = {
    "Eviter": [
        (
            "Redefinir le perimetre de l'activite source du risque '{intitule}'",
            "preventive", 30
        ),
        (
            "Analyser la faisabilite d'externalisation de l'activite a risque ({processus})",
            "preventive", 60
        ),
        (
            "Modifier le processus {processus} pour eliminer la source du risque",
            "preventive", 45
        ),
    ],
    "Reduire": [
        (
            "Renforcer les controles existants sur le processus {processus} "
            "pour le risque : {intitule}",
            "preventive", 30
        ),
        (
            "Mettre en place des barrieres techniques supplementaires "
            "contre : {causes_courtes}",
            "preventive", 45
        ),
        (
            "Former et sensibiliser le personnel du processus {processus} "
            "aux risques identifies",
            "preventive", 60
        ),
        (
            "Mettre a jour les procedures et instructions operationnelles "
            "du processus {processus}",
            "corrective", 30
        ),
        (
            "Augmenter la frequence de verification et de surveillance "
            "des indicateurs lies a {intitule}",
            "surveillance", 15
        ),
    ],
    "Transferer": [
        (
            "Souscrire une assurance ou garantie couvrant le risque '{intitule}'",
            "preventive", 90
        ),
        (
            "Contractualiser la responsabilite avec le fournisseur concerne "
            "({processus})",
            "preventive", 60
        ),
        (
            "Etablir un accord de partage de risque avec un partenaire strategique",
            "preventive", 90
        ),
    ],
    "Accepter": [
        (
            "Documenter formellement l'acceptation du risque residuel '{intitule}' "
            "avec justification",
            "surveillance", 15
        ),
        (
            "Definir et suivre les indicateurs de surveillance periodique "
            "du risque accepte ({processus})",
            "surveillance", 15
        ),
        (
            "Planifier une reevaluation du risque '{intitule}' a echeance de 6 mois",
            "surveillance", 180
        ),
    ],
    "Exploiter": [
        (
            "Lancer un projet d'amelioration cible pour exploiter l'opportunite "
            "sur {processus}",
            "opportunite", 30
        ),
        (
            "Allouer les ressources necessaires a l'exploitation de l'opportunite "
            "identifiee",
            "opportunite", 45
        ),
        (
            "Definir des indicateurs de suivi de la valeur generee par l'opportunite",
            "opportunite", 15
        ),
    ],
    "Renforcer": [
        (
            "Investir dans le renforcement des capacites existantes "
            "sur le processus {processus}",
            "opportunite", 60
        ),
        (
            "Developper les competences associees a l'opportunite identifiee",
            "opportunite", 90
        ),
        (
            "Systematiser les bonnes pratiques identifiees dans {processus}",
            "opportunite", 45
        ),
    ],
    "Anticiper": [
        (
            "Elaborer un plan de developpement pour l'opportunite future "
            "({processus})",
            "opportunite", 90
        ),
        (
            "Realiser une etude de faisabilite sur l'opportunite identifiee",
            "opportunite", 60
        ),
        (
            "Mettre en place une veille reglementaire et technologique "
            "sur le domaine concerne",
            "opportunite", 30
        ),
    ],
    "Experimenter": [
        (
            "Lancer un projet pilote pour tester l'opportunite a petite echelle",
            "opportunite", 60
        ),
        (
            "Definir des criteres de succes et des jalons pour l'experimentation",
            "opportunite", 15
        ),
    ],
}

# ---
# Baremes efficacite / effort par strategie [0-1]
# efficacite : reduction attendue du risque residuel (ou gain opportunite)
# effort     : cout + complexite + delai relatif
# ---
BAREME_EFF_EFFORT: Dict[str, Tuple[float, float]] = {
    #              (efficacite, effort)
    "Eviter":        (0.90, 0.85),
    "Reduire":       (0.70, 0.55),
    "Transferer":    (0.60, 0.45),
    "Accepter":      (0.15, 0.10),
    "Exploiter":     (0.85, 0.75),
    "Renforcer":     (0.65, 0.50),
    "Anticiper":     (0.50, 0.35),
    "Experimenter":  (0.40, 0.30),
}

# ---
# Classification du plan d'action
# L'indice_priorite = efficacite_globale / effort_global produit un ratio
# mathematiquement plus eleve pour les strategies legeres (Accepter : eff=0.15,
# effort=0.10 -> 1.5) que pour les strategies lourdes (Reduire : eff=0.95,
# effort=1.0 -> 0.95). On corrige via le niveau residuel qui exprime l'urgence
# metier reelle, independamment du cout de l'action.
#
# Matrice de classification :
#   - Risques "critique" ou "eleve"                        -> "critique"
#   - Risques "moyen" avec strategie Reduire/Eviter/Transf -> "prioritaire"
#   - Tous les autres R&O                                  -> "complementaire"
# ---
def _classe_priorite(indice: float, niveau_residuel: str = "moyen", strategie: str = "Reduire") -> str:
    """
    Classification metier de l'entree du plan.

    Logique CDC :
    - Les strategies passives (Accepter, Anticiper) indiquent un risque sous controle
      ou une opportunite a surveiller -> "complementaire" (pas d'action urgente)
    - Les strategies actives (Reduire, Eviter, Transferer, Exploiter, Renforcer)
      sont prioritaires en fonction du niveau residuel :
        * eleve / critique -> "critique"  (traitement immediat)
        * moyen            -> "prioritaire" (traitement planifie)
        * mineur           -> "complementaire" ou "prioritaire" selon indice

    Cette approche corrige le biais mathematique : les baremes "Accepter" (eff=0.15,
    effort=0.10) produisent un ratio efficacite/effort artificellement eleve (1.5x)
    alors que "Reduire" (eff=0.70, effort=0.55) donne un ratio < 1.0 apres
    combinaison de 3 actions. Le niveau residuel reflete l'urgence reelle.
    """
    niv = str(niveau_residuel).lower().strip()
    strat = str(strategie).strip()

    # Strategies passives : surveillance uniquement, pas d'urgence de traitement
    STRATEGIES_PASSIVES = ("Accepter", "Anticiper")
    if strat in STRATEGIES_PASSIVES:
        return "complementaire"

    # Strategies actives : classification par niveau residuel (urgence QSE)
    if niv in ("critique", "eleve"):
        return "critique"

    if niv == "moyen":
        return "prioritaire"

    # Niveau mineur avec strategie active : fallback sur indice
    if indice >= 0.90:
        return "prioritaire"

    return "complementaire"

# ---
# Echeances par niveau residuel (jours)
# ---
ECHEANCES_NIVEAU = {
    "critique": 15,
    "eleve":    30,
    "moyen":    90,
    "mineur":   180,
}


# =============================================================================
# UTILITAIRES
# =============================================================================

def _c(val: Any) -> str:
    """Nettoie une valeur (None, nan, HTML) -> chaine propre."""
    if val is None:
        return ""
    s = str(val).strip()
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return "" if s.lower() in ("none", "nan") else s


def _niveau_opportunite(score: float) -> str:
    """Convertit un score d'opportunite en niveau qualitatif."""
    if score >= 15:
        return "fort"
    elif score >= 8:
        return "moyen"
    else:
        return "faible"


def _render_template(template: str, context: Dict[str, str]) -> str:
    """Remplace les variables {key} dans un template."""
    for key, value in context.items():
        template = template.replace(f"{{{key}}}", value or "?")
    return template


def _est_opportunite(ro: Dict) -> bool:
    """Detecte si un element est une opportunite (pas un risque)."""
    signal = str(ro.get("signal_type", ro.get("type", "risque"))).lower()
    return "opportunite" in signal or "opportunité" in signal


# =============================================================================
# ETAPE 1 : FILTRAGE DES R&O A TRAITER
# =============================================================================

def filter_ro_a_traiter(
    evaluated_ro: List[Dict],
    seuil_niveau: str = SEUIL_TRAITEMENT_DEFAUT,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Separe les R&O en deux groupes :
    - a_traiter : niveau residuel >= seuil (ou obligations reglementaires)
    - exclus    : niveau mineur accepte, deja traite, ou hors perimetre

    Retourne (a_traiter, exclus).
    """
    seuil_idx = NIVEAUX_ORDRE.index(seuil_niveau) if seuil_niveau in NIVEAUX_ORDRE else 1

    a_traiter: List[Dict] = []
    exclus:    List[Dict] = []

    for ro in evaluated_ro:
        # Opportunites : toujours incluses si score > 0
        if _est_opportunite(ro):
            score = float(ro.get("score_brut", ro.get("criticality_score", 0)) or 0)
            if score > 0:
                a_traiter.append(ro)
            else:
                exclus.append(ro)
            continue

        # Risques : filtrage par niveau residuel
        niveau = _c(ro.get("niveau_residuel", ro.get("niveau", "mineur"))).lower()
        niveau_idx = NIVEAUX_ORDRE.index(niveau) if niveau in NIVEAUX_ORDRE else 0

        statut = _c(ro.get("statut", "")).lower()
        # Forcer l'inclusion si obligations reglementaires ou tendance aggravation
        force_inclusion = (
            "reglementaire" in _c(ro.get("lien_iso", "")).lower()
            or "aggravation" in _c(ro.get("tendance", "")).lower()
        )

        if force_inclusion or niveau_idx >= seuil_idx:
            a_traiter.append(ro)
        else:
            logger.debug(
                "R&O exclu du plan de traitement : %s (niveau=%s statut=%s)",
                ro.get("code", ro.get("risque", "?")), niveau, statut
            )
            exclus.append(ro)

    logger.info(
        "Filtrage Agent3 : %d R&O a traiter / %d exclus (seuil=%s)",
        len(a_traiter), len(exclus), seuil_niveau
    )
    return a_traiter, exclus


# =============================================================================
# ETAPE 2 : CHOIX DE LA STRATEGIE
# =============================================================================

def select_strategy(ro: Dict) -> Tuple[str, str]:
    """
    Choisit la strategie de traitement pour un R&O.

    Logique CDC (page 13) :
    - Risques  : Eviter / Reduire / Transferer / Accepter
    - Opport.  : Exploiter / Renforcer / Anticiper / Experimenter

    Retourne (strategie, justification).
    """
    if _est_opportunite(ro):
        score = float(ro.get("score_brut", ro.get("criticality_score", 0)) or 0)
        niveau_opp = _niveau_opportunite(score)
        strategie = STRATEGIE_OPPORTUNITE.get(niveau_opp, "Anticiper")
        justif = (
            f"Opportunite de niveau '{niveau_opp}' (score={score:.1f}). "
            f"Strategie '{strategie}' selectionnee pour maximiser la valeur "
            f"generee sur le processus {_c(ro.get('processus', ro.get('process_name', '?')))}."
        )
        return strategie, justif

    # Risque : lecture du niveau residuel
    niveau = _c(ro.get("niveau_residuel", ro.get("niveau", "mineur"))).lower()
    score_brut = float(ro.get("score_brut", ro.get("RPN", 0)) or 0)
    score_res  = float(ro.get("score_residuel", ro.get("RPN'", 0)) or 0)

    # Detection risque "fortement controle" : brut eleve, residuel faible
    # -> strategie Accepter avec surveillance renforcee (pas besoin de Reduire)
    brut_idx = NIVEAUX_ORDRE.index(
        ro.get("niveau_brut", "mineur")
        if ro.get("niveau_brut", "mineur") in NIVEAUX_ORDRE else "mineur"
    )
    res_idx = NIVEAUX_ORDRE.index(niveau) if niveau in NIVEAUX_ORDRE else 0
    fort_controle = (brut_idx >= 2) and (res_idx <= 1)  # brut eleve/critique, residuel mineur/moyen

    if fort_controle and res_idx <= 1:
        strategie = STRATEGIE_RISQUE_FORT_CONTROLE
        justif = (
            f"Risque fortement controle : score brut={score_brut:.1f} mais "
            f"residuel={score_res:.1f} (niveau={niveau}). "
            f"Les mesures de maitrise existantes sont efficaces. "
            f"Strategie '{strategie}' avec surveillance renforcee."
        )
    else:
        strategie = STRATEGIE_RISQUE.get(niveau, "Reduire")
        tendance  = _c(ro.get("tendance", "stabilite")).lower()
        # Si tendance aggravantion -> forcer Reduire meme pour niveau moyen
        if tendance == "aggravation" and strategie == "Accepter":
            strategie = "Reduire"
            justif = (
                f"Risque de niveau '{niveau}' avec tendance d'AGGRAVATION detectee. "
                f"Surcharge de strategie : '{strategie}' impose pour contrer la derive."
            )
        else:
            justif = (
                f"Risque de niveau residuel '{niveau}' (score={score_res:.1f}). "
                f"Strategie '{strategie}' selectionnee selon la matrice CDC. "
                f"Tendance observee : {tendance}."
            )

    return strategie, justif


# =============================================================================
# ETAPE 3 : GENERATION DES ACTIONS CANDIDATES
# =============================================================================

def generate_actions(
    ro: Dict,
    strategie: str,
    max_actions: int = 3,
) -> List[Dict]:
    """
    Genere les actions candidates pour un R&O selon sa strategie.

    Utilise le catalogue regle-metier.
    L'enrichissement LLM est effectue separement dans enrich_plan_llm().

    Retourne une liste de dicts ActionDict.
    """
    catalogue = CATALOGUE_ACTIONS.get(strategie, CATALOGUE_ACTIONS["Reduire"])

    niveau = _c(ro.get("niveau_residuel", ro.get("niveau", "mineur"))).lower()
    echeance_base = ECHEANCES_NIVEAU.get(niveau, 90)

    # Contexte pour le rendu des templates
    intitule   = _c(ro.get("intitule", ro.get("risque", ro.get("Intitule", "?"))))[:100]
    processus  = _c(ro.get("processus", ro.get("process_name", ro.get("Processus", "?"))))
    causes_raw = _c(ro.get("causes", ro.get("Causes", "")))
    # Resume des causes en < 60 chars pour les templates
    causes_courtes = causes_raw[:60] + "..." if len(causes_raw) > 60 else causes_raw

    ctx = {
        "intitule":       intitule,
        "processus":      processus,
        "causes_courtes": causes_courtes or "causes identifiees",
    }

    eff, eff_effort = BAREME_EFF_EFFORT.get(strategie, (0.5, 0.5))

    actions: List[Dict] = []
    for i, (tmpl, type_action, echeance_tmpl) in enumerate(catalogue[:max_actions]):
        designation = _render_template(tmpl, ctx)

        # Echeance = max(echeance du niveau residuel, echeance catalogue)
        echeance_finale = max(echeance_base, echeance_tmpl)

        # Description operationnelle complete
        description = _build_action_description(ro, designation, strategie, type_action)

        # Efficacite individuelle : decroissante avec le rang (premiere action = plus impactante)
        decay = 1.0 - (i * 0.10)
        efficacite_i = round(eff * decay, 3)
        effort_i     = round(eff_effort * (1.0 - i * 0.05), 3)
        priorite_i   = round(efficacite_i / effort_i, 3) if effort_i > 0 else 0

        actions.append({
            "type":        type_action,
            "designation": designation[:200],
            "description": description[:2000],
            "responsable": f"Pilote du processus {processus}",
            "echeance_j":  echeance_finale,
            "indicateur":  _indicateur_defaut(strategie, intitule),
            "efficacite":  efficacite_i,
            "effort":      effort_i,
            "priorite":    priorite_i,
        })

    return actions


def _build_action_description(
    ro: Dict,
    designation: str,
    strategie: str,
    type_action: str,
) -> str:
    """Construit la description operationnelle complete d'une action."""
    intitule   = _c(ro.get("intitule", ro.get("risque", ro.get("Intitule", "?"))))
    processus  = _c(ro.get("processus", ro.get("process_name", ro.get("Processus", "?"))))
    niveau_res = _c(ro.get("niveau_residuel", ro.get("niveau", "?")))
    score_res  = ro.get("score_residuel", ro.get("RPN'", "?"))
    causes     = _c(ro.get("causes", ro.get("Causes", "")))
    effets     = _c(ro.get("effets", ro.get("consequences", ro.get("Effets Négatifs", ""))))
    code       = _c(ro.get("code", ro.get("Code", "")))

    desc = f"Action de type '{type_action}' - Strategie : {strategie}\n"
    desc += f"Risque/Opportunite source : [{code}] {intitule}\n"
    desc += f"Processus concerne : {processus}\n"
    desc += f"Niveau residuel : {niveau_res} | Score residuel : {score_res}\n"

    if causes:
        desc += f"Causes a traiter : {causes[:300]}\n"
    if effets:
        desc += f"Effets a prevenir : {effets[:300]}\n"

    desc += f"\nObjectif de l'action :\n{designation}"

    return desc.strip()


def _indicateur_defaut(strategie: str, intitule: str) -> str:
    """Genere un indicateur de suivi par defaut selon la strategie."""
    indicateurs = {
        "Eviter":       f"Nombre d'occurrences residuelles de : {intitule[:50]}",
        "Reduire":      f"Reduction du score RPN residuel apres action",
        "Transferer":   f"Couverture contractuelle / assurance en place (O/N)",
        "Accepter":     f"Suivi periodique du niveau residuel (seuil d'alerte)",
        "Exploiter":    f"Valeur generee par l'opportunite (mesurable)",
        "Renforcer":    f"Taux d'amelioration de la performance cible",
        "Anticiper":    f"Etat d'avancement du plan de preparation",
        "Experimenter": f"Resultats du pilote vs criteres de succes",
    }
    return indicateurs.get(strategie, "Indicateur a definir par le responsable")


# =============================================================================
# ETAPE 4 : ESTIMATION EFFICACITE / EFFORT GLOBALE
# =============================================================================

def compute_global_metrics(actions: List[Dict]) -> Tuple[float, float, float]:
    """
    Calcule l'efficacite globale, l'effort global et l'indice de priorite
    du plan de traitement pour un R&O.

    Retourne (efficacite_globale, effort_global, indice_priorite).
    """
    if not actions:
        return 0.0, 0.0, 0.0

    # Efficacite combinee : loi de diminution marginale
    # 1 - prod(1 - eff_i) : les actions se renforcent mais avec decroissance
    efficacite = 1.0
    for a in actions:
        efficacite *= (1.0 - a.get("efficacite", 0))
    efficacite_globale = round(1.0 - efficacite, 3)

    # Effort total : somme normalisee (plafonee a 1)
    effort_global = round(min(sum(a.get("effort", 0) for a in actions), 1.0), 3)

    # Indice de priorite = efficacite / effort
    indice = round(efficacite_globale / effort_global, 3) if effort_global > 0 else 0.0

    return efficacite_globale, effort_global, indice


# =============================================================================
# ETAPE 3-BIS : ENRICHISSEMENT LLM (optionnel, avec fallback)
# =============================================================================

def enrich_plan_llm(
    plan: List[Dict],
    llm_url: str = "http://localhost:11434",
    model: str = "llama3.2",
) -> List[Dict]:
    """
    Enrichit les actions du plan via LLM (Ollama local).

    Pour chaque entree du plan :
    - Reformule la designation de l'action principale pour la rendre
      plus concrete et contextuelle.
    - Genere un indicateur de suivi specifique au risque.

    En cas d'indisponibilite du LLM : retourne le plan inchange (pas de plantage).
    """
    try:
        import requests as _req
        _req.get(f"{llm_url}/api/tags", timeout=2).raise_for_status()
    except Exception:
        logger.info(
            "LLM Ollama indisponible (%s) - plan de traitement conserve tel quel.",
            llm_url
        )
        return plan

    enriched = []
    for entry in plan:
        try:
            ro_intitule = entry.get("risque_intitule", "risque identifie")
            strategie   = entry.get("strategie", "Reduire")
            processus   = entry.get("processus", "processus concerne")
            niveau      = entry.get("niveau_residuel", "moyen")
            actions_txt = "\n".join(
                f"- {a['designation']}" for a in entry.get("actions", [])[:3]
            )

            prompt = (
                f"Tu es un expert QSE. Pour le risque suivant :\n"
                f"  Risque : {ro_intitule}\n"
                f"  Processus : {processus}\n"
                f"  Niveau residuel : {niveau}\n"
                f"  Strategie retenue : {strategie}\n"
                f"  Actions proposees :\n{actions_txt}\n\n"
                f"Propose UN indicateur de suivi SMART (Specifique, Mesurable, "
                f"Atteignable, Realiste, Temporel) pour verifier l'efficacite "
                f"du traitement. Reponds en 1 seule phrase concise, sans introduction."
            )

            resp = _req.post(
                f"{llm_url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=30,
            )
            resp.raise_for_status()
            indicateur_llm = resp.json().get("response", "").strip()

            if indicateur_llm and len(indicateur_llm) > 10:
                # Mettre a jour l'indicateur de la premiere action (la plus critique)
                if entry.get("actions"):
                    entry["actions"][0]["indicateur"] = indicateur_llm[:300]
                entry["indicateur_efficacite_global"] = indicateur_llm[:300]
                entry["llm_used"] = True
            else:
                entry["llm_used"] = False

        except Exception as exc:
            logger.debug("Enrichissement LLM echec pour '%s' : %s", entry.get("risque_code"), exc)
            entry["llm_used"] = False

        enriched.append(entry)

    llm_count = sum(1 for e in enriched if e.get("llm_used"))
    logger.info(
        "Enrichissement LLM Agent3 : %d/%d entrees enrichies.",
        llm_count, len(enriched)
    )
    return enriched


# =============================================================================
# ETAPE 5 : STRUCTURATION DU PLAN DE TRAITEMENT
# =============================================================================

def build_treatment_plan(
    evaluated_ro: List[Dict],
    seuil_niveau: str = SEUIL_TRAITEMENT_DEFAUT,
    max_actions_par_ro: int = 3,
    enrichissement_llm: bool = True,
    llm_url: str = "http://localhost:11434",
) -> List[Dict]:
    """
    Pipeline complet Agent 3 : du registre evalue au plan de traitement.

    Etapes internes :
        1. Filtrage (filter_ro_a_traiter)
        2. Strategie (select_strategy)
        3. Actions (generate_actions)
        4. Metriques (compute_global_metrics)
        5. Classification (critique / prioritaire / complementaire)
        6. Enrichissement LLM (enrich_plan_llm) - optionnel

    Retourne la liste des entrees du plan, triee par indice de priorite desc.
    """
    logger.info(
        "Agent3 - Construction du plan de traitement (%d R&O en entree, seuil=%s)",
        len(evaluated_ro), seuil_niveau
    )

    # Etape 1 : filtrage
    a_traiter, exclus = filter_ro_a_traiter(evaluated_ro, seuil_niveau)

    plan: List[Dict] = []

    for ro in a_traiter:
        code      = _c(ro.get("code", ro.get("Code", ro.get("risque", "?"))))
        intitule  = _c(ro.get("intitule", ro.get("risque", ro.get("Intitule", "?"))))
        processus = _c(ro.get("processus", ro.get("process_name", ro.get("Processus", "?"))))
        niveau    = _c(ro.get("niveau_residuel", ro.get("niveau", "moyen"))).lower()
        domaine   = _c(ro.get("domaine", ro.get("Systeme", ro.get("systeme", "Q"))))

        # Etape 2 : strategie
        strategie, justif_strategie = select_strategy(ro)

        # Etape 3 : actions
        actions = generate_actions(ro, strategie, max_actions=max_actions_par_ro)

        # Etape 4 : metriques globales
        eff_globale, effort_global, indice_priorite = compute_global_metrics(actions)

        # Etape 5 : classification (basee sur niveau residuel + strategie + indice)
        classe = _classe_priorite(indice_priorite, niveau_residuel=niveau, strategie=strategie)

        entree = {
            # Identification
            "risque_code":              code,
            "risque_intitule":          intitule,
            "processus":                processus,
            "domaine":                  domaine,
            "type_ro":                  "opportunite" if _est_opportunite(ro) else "risque",
            # Evaluation Agent2 (contexte)
            "niveau_residuel":          niveau,
            "score_residuel":           ro.get("score_residuel", ro.get("RPN'", 0)),
            "score_brut":               ro.get("score_brut", ro.get("RPN", 0)),
            "statut_agent2":            _c(ro.get("statut", "")),
            "tendance":                 _c(ro.get("tendance", "stabilite")),
            # Traitement Agent3
            "strategie":                strategie,
            "justification_strategie":  justif_strategie,
            "actions":                  actions,
            # Metriques
            "efficacite_globale":       eff_globale,
            "effort_global":            effort_global,
            "indice_priorite":          indice_priorite,
            "classe":                   classe,
            # Timestamps
            "genere_le":                datetime.now().isoformat(),
            # Injection QALITAS (rempli plus tard par agent3_main)
            "qalitas_actions_ids":      [],
            "llm_used":                 False,
            # Lien vers les donnees QALITAS brutes (UUIDs)
            "_raw":                     ro.get("_raw", {}),
        }

        plan.append(entree)

    # Tri par indice de priorite decroissant (action la plus urgente en premier)
    plan.sort(key=lambda e: e.get("indice_priorite", 0), reverse=True)

    # Etape 6 : enrichissement LLM (optionnel)
    if enrichissement_llm and plan:
        plan = enrich_plan_llm(plan, llm_url=llm_url)

    logger.info(
        "Plan de traitement Agent3 : %d entrees generees "
        "(%d critique, %d prioritaire, %d complementaire)",
        len(plan),
        sum(1 for e in plan if e.get("classe") == "critique"),
        sum(1 for e in plan if e.get("classe") == "prioritaire"),
        sum(1 for e in plan if e.get("classe") == "complementaire"),
    )
    return plan


# =============================================================================
# RAPPORT DE SYNTHESE
# =============================================================================

def generate_treatment_summary(plan: List[Dict]) -> Dict[str, Any]:
    """
    Genere un rapport de synthese du plan de traitement Agent3.
    """
    if not plan:
        return {"total": 0, "message": "Aucun element a traiter."}

    total_actions = sum(len(e.get("actions", [])) for e in plan)

    by_strategie: Dict[str, int] = {}
    by_classe:    Dict[str, int] = {}
    by_processus: Dict[str, int] = {}

    for e in plan:
        s = e.get("strategie", "?")
        by_strategie[s] = by_strategie.get(s, 0) + 1
        c = e.get("classe", "?")
        by_classe[c] = by_classe.get(c, 0) + 1
        p = e.get("processus", "?")
        by_processus[p] = by_processus.get(p, 0) + 1

    top5 = [
        {
            "code":      e.get("risque_code"),
            "intitule":  e.get("risque_intitule", "")[:80],
            "strategie": e.get("strategie"),
            "classe":    e.get("classe"),
            "priorite":  e.get("indice_priorite"),
            "nb_actions": len(e.get("actions", [])),
        }
        for e in plan[:5]
    ]

    llm_count = sum(1 for e in plan if e.get("llm_used"))

    return {
        "total_ro_traites":     len(plan),
        "total_actions":        total_actions,
        "by_strategie":         by_strategie,
        "by_classe":            by_classe,
        "by_processus":         by_processus,
        "top5_prioritaires":    top5,
        "llm_enrichissement":   f"{llm_count}/{len(plan)}",
    }
