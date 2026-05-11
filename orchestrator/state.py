"""
Etat partage de la plateforme multi-agents (LangGraph PipelineState).
======================================================================
Cet objet est passe de noeud en noeud dans le graphe LangGraph.
Chaque agent lit ce dont il a besoin et ecrit ses sorties dans les
champs qui lui sont reserves.

Flux CDC :
    Agent1 (Identification) -> Agent2 (Evaluation) ->
    Agent3 (Traitement)     -> Agent4 (Monitoring)
                                    |
                  reeval_required=True => retour Agent2

Conforme au cahier des charges TBR.TIM.IA.S5/2025-2026.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict


# =============================================================================
# ETAT GLOBAL DU PIPELINE
# =============================================================================

class PipelineState(TypedDict, total=False):
    """
    Etat partage entre tous les agents de la plateforme.

    Champs par agent :
        Agent 1  : ro_register
        Agent 2  : evaluated_ro
        Agent 3  : treatment_plan
        Agent 4  : monitoring_alerts, reeval_required, reeval_trigger

    Champs de controle :
        iteration       : compteur anti-boucle (max_iterations)
        max_iterations  : plafond de réévaluations automatiques (defaut 3)
        errors          : liste des erreurs non fatales rencontrees
        qalitas_stats   : statistiques d'injection QALITAS consolidees
    """

    # ------------------------------------------------------------------
    # Entrees globales (fournies avant le lancement du graphe)
    # ------------------------------------------------------------------
    data_dir:        str          # chemin vers le dossier /donnees
    pdf_path:        str          # chemin vers dashboard.pdf
    run_id:          str          # identifiant unique de la session (UUID)
    started_at:      str          # ISO datetime de lancement

    # ------------------------------------------------------------------
    # Agent 1 — Identification & Caracterisation
    # Sortie : registre R&O structure pret a etre evalue par Agent 2
    # ------------------------------------------------------------------
    ro_register: List[Dict[str, Any]]
    # Chaque entree du registre contient :
    #   type           : "risque" | "opportunite"
    #   code           : identifiant unique (ex: R-001)
    #   intitule       : description claire et factuelle
    #   causes         : liste des causes probables
    #   consequences   : liste des consequences potentielles
    #   perimetre      : processus / site / activite concerne
    #   domaine        : Q | E | SST | conformite | performance | image
    #   lien_iso       : ex "ISO 9001 §6.1"
    #   lien_objectif  : objectif QHSE concerne
    #   justification  : pourquoi ce R&O a ete identifie
    #   source         : "Agent1_LLM" | "Agent1_regle" | "Excel" | "API"
    #   _raw           : dict QALITAS brut (UUIDs, si disponible)

    # ------------------------------------------------------------------
    # Agent 2 — Analyse & Evaluation
    # Sortie : registre evalue (brut + residuel + mode evaluation)
    # ------------------------------------------------------------------
    evaluated_ro: List[Dict[str, Any]]
    # Chaque entree ajoute aux champs Agent1 :
    #   mode_evaluation      : A | B | C | D
    #   score_brut           : float (RPN avant maitrise)
    #   score_residuel       : float (RPN apres maitrise)
    #   niveau_brut          : mineur | moyen | eleve | critique
    #   niveau_residuel      : mineur | moyen | eleve | critique
    #   indice_maitrise      : float [0-1]
    #   statut               : "A traiter immediatement" | "A traiter planifie" |
    #                          "A surveiller" | "Acceptable"
    #   justification_eval   : str
    #   recommandation_llm   : str (plan d'action LLM ou regle)
    #   impact_prospectif    : str
    #   tendance             : "aggravation" | "stabilite" | "amelioration"

    # ------------------------------------------------------------------
    # Agent 3 — Generation des Actions de Traitement
    # Sortie : plan de traitement structure + actions QALITAS
    # ------------------------------------------------------------------
    treatment_plan: List[Dict[str, Any]]
    # Chaque entree du plan :
    #   risque_code        : code du R&O source
    #   risque_intitule    : intitule du R&O
    #   niveau_residuel    : niveau residuel Agent2
    #   strategie          : Eviter | Reduire | Transferer | Accepter
    #                        Exploiter | Renforcer | Anticiper | Experimenter
    #   justification_strategie : pourquoi cette strategie
    #   actions            : List[ActionDict]  (voir ci-dessous)
    #   efficacite_globale : float [0-1]
    #   effort_global      : float [0-1]
    #   indice_priorite    : float (efficacite / effort)
    #   classe             : "critique" | "prioritaire" | "complementaire"
    #   qalitas_action_id  : UUID cree dans QALITAS (apres injection)

    # Structure d'une ActionDict dans treatment_plan[i]["actions"] :
    #   type        : "preventive" | "corrective" | "surveillance" | "opportunite"
    #   designation : titre court (< 200 chars)
    #   description : description operationnelle complete
    #   responsable : "Pilote processus {processus}" (par defaut)
    #   echeance_j  : int (jours a partir d'aujourd'hui)
    #   indicateur  : indicateur de suivi de l'efficacite
    #   efficacite  : float [0-1] reduction attendue du risque residuel
    #   effort      : float [0-1] cout/complexite/delai relatif
    #   priorite    : float (efficacite / effort)

    # ------------------------------------------------------------------
    # Agent 4 — Suivi, Reevaluation & Pilotage Continu
    # Sortie : alertes, recommandations, declencheurs de reevaluation
    # ------------------------------------------------------------------
    monitoring_alerts: List[Dict[str, Any]]
    # Chaque alerte Agent4 :
    #   process_name       : processus concerne
    #   type_signal        : "derive_kpi" | "nc_majeure" | "action_inefficace" |
    #                        "opportunite" | "risque_accepte_depasse"
    #   criticality_level  : ALERTE | SURVEILLANCE | STABLE
    #   criticality_score  : float
    #   evidence_source    : "PDF" | "Excel_KPI" | "Excel_NC" | ...
    #   snippet            : extrait justificatif
    #   reevaluation_required : bool
    #   risque_code_lie    : code du risque impacte (si identifiable)
    #   recommandation     : str

    # ------------------------------------------------------------------
    # Controle de flux (orchestrateur LangGraph)
    # ------------------------------------------------------------------
    reeval_required:  bool    # Agent4 -> True pour declencher Agent2 a nouveau
    reeval_trigger:   str     # motif : "NC_MAJEURE" | "DERIVE_KPI" | "ACTION_INEFFICACE"
    reeval_risques:   List[str]  # codes des risques a reevaluer (vide = tous)
    iteration:        int     # compteur de passages par Agent2 (protection boucle)
    max_iterations:   int     # plafond (defaut : 3)

    # ------------------------------------------------------------------
    # QALITAS — Statistiques d'injection consolidees
    # ------------------------------------------------------------------
    qalitas_stats: Dict[str, Any]
    # {
    #   "agent2": {"appreciations_updated": N, "actions_created": N, "errors": N},
    #   "agent3": {"actions_created": N, "errors": N},
    #   "agent4": {"actions_created": N, "errors": N},
    # }

    # ------------------------------------------------------------------
    # Erreurs et meta
    # ------------------------------------------------------------------
    errors:      List[str]   # erreurs non fatales (ne bloquent pas le pipeline)
    dry_run:     bool        # True = simulation sans ecriture QALITAS
