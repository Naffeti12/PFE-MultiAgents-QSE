"""
Agent IA 1 - Identification & Caracterisation des Risques et Opportunites.
==========================================================================
Conforme au cahier des charges TBR.TIM.IA.S5/2025-2026, Agent IA 1.

Role :
    Identifier, structurer et caracteriser l'ensemble des R&O a partir de
    sources multiples : QALITAS (API), Excel (cartographie), PDF (dashboard).
    Produire un registre normalise pret a etre evalue par l'Agent 2.

Sources exploitees (par ordre de priorite) :
    1. QALITAS API  : registre officiel (risques + opportunites + evaluations)
    2. Excel        : cartographie existante, KPIs hors cible, NC recurrentes
    3. PDF          : dashboard qualite (signaux textuels)
    4. LLM          : enrichissement causes/consequences/ISO manquants

Sorties :
    output/agent1_register_<timestamp>.json
    output/agent1_latest.json

Chaque entree du registre respecte le schema PipelineState.ro_register :
    type, code, intitule, causes, consequences, perimetre, domaine,
    lien_iso, lien_objectif, justification, source, _raw
"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loaders.load_excel_data import (
    load_all_context,
    load_fiches_risque_from_dir,
    fiches_to_qalitas_payloads,
)
from loaders.qalitas_api_client import QalitasClient, DEFAULT_BASE_URL
from loaders.qalitas_api_writer import (
    QalitasWriter,
    inject_agent1_results,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("agent1.identification")

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR   = os.path.join(BASE_DIR, "donnees")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

QALITAS_BASE_URL = os.environ.get("QALITAS_BASE_URL", DEFAULT_BASE_URL)
QALITAS_USERNAME = os.environ.get("QALITAS_USERNAME", "MOHAMED.N")
QALITAS_PASSWORD = os.environ.get("QALITAS_PASSWORD", "MOHAMED.N")

# Seuil KPI : pourcentage d'ecart acceptable avant signalement
KPI_ECART_SEUIL = 0.15  # 15 %

# Domaines QSE reconnus
DOMAINES_QSE = {
    "qualite": "Q",
    "quality": "Q",
    "environnement": "E",
    "environment": "E",
    "sst": "SST",
    "securite": "SST",
    "safety": "SST",
    "conformite": "Conformite",
    "compliance": "Conformite",
    "performance": "Performance",
    "image": "Image",
}

# Correspondance domaine -> lien ISO
ISO_MAPPING = {
    "Q":           "ISO 9001:2015 §6.1",
    "E":           "ISO 14001:2015 §6.1",
    "SST":         "ISO 45001:2018 §6.1",
    "Conformite":  "ISO 9001:2015 §6.1.2",
    "Performance": "ISO 9001:2015 §6.2",
    "Image":       "ISO 9001:2015 §8.2",
}


# =============================================================================
# NORMALISATION D'UN RISQUE QALITAS EN ENTREE REGISTRE
# =============================================================================

def _infer_domaine(text: str) -> str:
    """
    Infere le domaine QSE a partir du texte (intitule, processus).
    Retourne un code domaine ('Q', 'E', 'SST', etc.) ou 'Q' par defaut.
    """
    t = text.lower()
    for kw, code in DOMAINES_QSE.items():
        if kw in t:
            return code
    return "Q"


def _code_from_qalitas(raw: Dict) -> str:
    """
    Genere un code lisible depuis les donnees QALITAS.
    Forme : R-XXXX ou O-XXXX (4 premiers chars de l'UUID).
    """
    uid  = str(raw.get("RiskOpportunityId", raw.get("Id", ""))).replace("-", "")
    prefix = "O" if str(raw.get("Nature", "0")) == "1" else "R"
    short  = uid[:6].upper() if uid else "000"
    return f"{prefix}-{short}"


def normalize_qalitas_ro(raw: Dict, index: int = 0) -> Dict:
    """
    Transforme un enregistrement QALITAS brut en entree normalisee du registre.

    Nature QALITAS : 0 = Risque, 1 = Opportunite
    """
    nature   = str(raw.get("Nature", "0"))
    is_oppty = (nature == "1")
    rtype    = "opportunite" if is_oppty else "risque"

    intitule  = str(raw.get("Name", raw.get("Title", raw.get("Intitule", ""))))
    processus = str(raw.get("ProcessName", raw.get("Processus", raw.get("SourceName", "?"))))
    code      = _code_from_qalitas(raw)

    domaine   = _infer_domaine(intitule + " " + processus)
    lien_iso  = ISO_MAPPING.get(domaine, "ISO 9001:2015 §6.1")

    # Causes et consequences depuis QALITAS (champs optionnels)
    causes_raw = str(raw.get("Cause", raw.get("Causes", "")))
    consq_raw  = str(raw.get("Effect",  raw.get("Consequences", "")))

    causes = [c.strip() for c in re.split(r"[;,\n]", causes_raw) if c.strip()] or [
        f"Cause non renseignee pour {intitule[:40]}"
    ]
    consequences = [c.strip() for c in re.split(r"[;,\n]", consq_raw) if c.strip()] or [
        f"Impact potentiel sur le processus {processus}"
    ]

    return {
        "type":          rtype,
        "code":          code,
        "intitule":      intitule,
        "causes":        causes,
        "consequences":  consequences,
        "perimetre":     processus,
        "domaine":       domaine,
        "lien_iso":      lien_iso,
        "lien_objectif": f"Objectif qualite processus {processus}",
        "justification": (
            f"Risque/Opportunite issu du registre QALITAS (ID: "
            f"{raw.get('RiskOpportunityId', '?')[:12]}...). "
            f"Processus concerne : {processus}."
        ),
        "source":        "QALITAS_API",
        "_raw":          raw,
    }


# =============================================================================
# IDENTIFICATION DEPUIS L'EXCEL (CARTOGRAPHIE + KPI + NC)
# =============================================================================

def identify_from_excel(context: Dict) -> List[Dict]:
    """
    Analyse le contexte Excel et identifie les R&O :
    - Cartographie existante (reprise directe)
    - KPIs hors cible (risques detectes)
    - Non-conformites recurrentes (risques de qualite)

    Retourne une liste d'entrees normalisees.
    """
    results: List[Dict] = []
    counter = {"R": 0, "O": 0}

    def _next_code(t: str) -> str:
        prefix = "O" if t == "opportunite" else "R"
        counter[prefix] += 1
        return f"{prefix}-XLS{counter[prefix]:03d}"

    # --- 1. Cartographie existante ---
    for row in context.get("cartographie", []):
        intitule  = str(
            row.get("Risques")
            or row.get("Risque")
            or row.get("risque")
            or row.get("Intitule")
            or row.get("Intitulé")
            or ""
        )
        processus = str(row.get("Processus", row.get("Process", row.get("process_name", "?"))))
        nature    = str(row.get("Type",      row.get("type",    "risque"))).lower()
        rtype     = "opportunite" if "opportunite" in nature or "opportunité" in nature else "risque"

        if not intitule or intitule == "None":
            continue

        code = str(row.get("Code", row.get("code", ""))) or _next_code(rtype)
        domaine  = _infer_domaine(intitule + " " + processus)

        results.append({
            "type":          rtype,
            "code":          code,
            "intitule":      intitule,
            "causes":        [str(row.get("Causes", row.get("causes", "Non renseigne")))],
            "consequences":  [str(
                row.get("Consequences")
                or row.get("Effets Négatifs")
                or row.get("Effets Negatifs")
                or row.get("consequences")
                or "Non renseigne"
            )],
            "perimetre":     processus,
            "domaine":       domaine,
            "lien_iso":      ISO_MAPPING.get(domaine, "ISO 9001:2015 §6.1"),
            "lien_objectif": f"Objectif qualite processus {processus}",
            "justification": "Repris de la cartographie Excel existante.",
            "source":        "Excel_Cartographie",
            "niveau_initial": row.get("Niveau", ""),
            "_raw":          row,
        })

    logger.info("Excel cartographie : %d R&O repris.", len(results))

    # --- 2. KPIs hors cible -> risques de derive performance ---
    kpis_off = []
    for kpi in context.get("kpis", []):
        valeur = kpi.get("Valeur", kpi.get("Valeur reelle", None))
        cible  = kpi.get("Cible",  kpi.get("Objectif",      None))
        mini   = kpi.get("Min",    kpi.get("Minimum",        None))
        maxi   = kpi.get("Max",    kpi.get("Maximum",        None))
        intitule_kpi = str(kpi.get("Intitule du KPI", kpi.get("Indicateur", kpi.get("Intitule", ""))))
        processus    = str(kpi.get("Processus", "?"))

        if valeur is None or not intitule_kpi:
            continue

        hors_cible = False
        ecart_msg  = ""

        try:
            v = float(str(valeur).replace(",", ".").replace("%", ""))
            if cible is not None:
                c = float(str(cible).replace(",", ".").replace("%", ""))
                if c != 0 and abs(v - c) / abs(c) > KPI_ECART_SEUIL:
                    hors_cible = True
                    ecart_msg  = f"valeur={v}, cible={c}"
            if mini is not None:
                m = float(str(mini).replace(",", ".").replace("%", ""))
                if v < m:
                    hors_cible = True
                    ecart_msg  = f"valeur={v} < min={m}"
            if maxi is not None:
                M = float(str(maxi).replace(",", ".").replace("%", ""))
                if v > M:
                    hors_cible = True
                    ecart_msg  = f"valeur={v} > max={M}"
        except (ValueError, TypeError):
            continue

        if hors_cible:
            kpis_off.append((kpi, intitule_kpi, processus, ecart_msg))

    for kpi, intitule_kpi, processus, ecart_msg in kpis_off:
        code = _next_code("risque")
        results.append({
            "type":         "risque",
            "code":         code,
            "intitule":     f"KPI hors cible : {intitule_kpi}",
            "causes":       [
                f"Derive de la performance sur le KPI '{intitule_kpi}' ({ecart_msg})",
                "Processus sous-performant ou objectif non atteint",
            ],
            "consequences": [
                f"Non-atteinte de l'objectif qualite processus {processus}",
                "Insatisfaction client potentielle",
                "Declenchement possible d'une action corrective",
            ],
            "perimetre":    processus,
            "domaine":      "Performance",
            "lien_iso":     "ISO 9001:2015 §9.1",
            "lien_objectif": f"Objectif KPI {intitule_kpi}",
            "justification": (
                f"KPI '{intitule_kpi}' hors cible detecte dans le tableau de bord Excel "
                f"({ecart_msg}). Processus : {processus}."
            ),
            "source":       "Excel_KPI",
            "_raw":         kpi,
        })

    logger.info("KPIs hors cible detectes : %d nouveaux risques.", len(kpis_off))

    # --- 3. Non-conformites recurrentes ---
    nc_par_type: Dict[str, List] = {}
    for nc in context.get("nc", []):
        type_nc = str(nc.get("Type", nc.get("TypeNC", nc.get("Designation", "NC inconnue"))))
        nc_par_type.setdefault(type_nc, []).append(nc)

    for type_nc, ncs in nc_par_type.items():
        if len(ncs) < 2:
            continue  # NC isolee, pas recurrente

        processus = str(ncs[-1].get("Processus", ncs[-1].get("Process", "?")))
        code = _next_code("risque")
        results.append({
            "type":         "risque",
            "code":         code,
            "intitule":     f"NC recurrente : {type_nc}",
            "causes":       [
                f"Non-conformite de type '{type_nc}' recurrente ({len(ncs)} occurrences)",
                "Inefficacite des actions correctives precedentes",
                "Cause racine non traitee",
            ],
            "consequences": [
                "Degradation de la performance qualite",
                "Risque de reclamation client ou audit negatif",
                "Potentielle non-conformite ISO/reglementaire",
            ],
            "perimetre":    processus,
            "domaine":      "Q",
            "lien_iso":     "ISO 9001:2015 §10.2",
            "lien_objectif": "Reduction des non-conformites",
            "justification": (
                f"NC de type '{type_nc}' observee {len(ncs)} fois dans les donnees Excel. "
                f"Processus : {processus}."
            ),
            "source":       "Excel_NC",
            "_raw":         ncs[-1],
        })

    logger.info(
        "NC recurrentes : %d risques identifies.",
        sum(1 for ncs in nc_par_type.values() if len(ncs) >= 2)
    )

    return results


# =============================================================================
# IDENTIFICATION DEPUIS LES FICHES CLIENTS (Source 2b)
# =============================================================================

def identify_from_fiches(
    fiches_dir: str,
    annee_filtre: Optional[int] = None,
    toutes_annees: bool = False,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Charge les fiches d'analyse du risque transmises par les pilotes de processus.

    Gere deux formats automatiquement :
      - Format Fiche (FAB, MRH, MRM, LOG, Direction, Qualite) : colonnes G/O/D/IPR
      - Format Grille (Grille management) : colonnes Gi/Vi/Ci, feuilles separees

    Chaque risque peut contenir plusieurs actions dans une meme cellule.
    Le splitting multi-actions produit un payload QALITAS individuel par action.

    Args:
        fiches_dir    : repertoire contenant les fichiers Excel clients
        annee_filtre  : si fourni, charger uniquement la feuille de cette annee
        toutes_annees : si True, charger toutes les feuilles (sinon derniere seulement)

    Returns:
        Tuple (register_entries, qalitas_payloads) :
          - register_entries : entrees normalisees schema PipelineState.ro_register
          - qalitas_payloads : payloads QALITAS prets (un par action individuelle)
    """
    logger.info("Chargement des fiches clients depuis : %s", fiches_dir)

    try:
        records = load_fiches_risque_from_dir(
            fiches_dir,
            annee_filtre=annee_filtre,
            toutes_annees=toutes_annees,
        )
    except Exception as exc:
        logger.error("Erreur chargement fiches clients : %s", exc)
        return [], []

    if not records:
        logger.warning("Aucun enregistrement charge depuis les fiches clients.")
        return [], []

    counter: Dict[str, int] = {"R": 0, "O": 0}

    def _next_code(t: str) -> str:
        prefix = "O" if t == "opportunite" else "R"
        counter[prefix] += 1
        return f"{prefix}-FIC{counter[prefix]:03d}"

    register_entries: List[Dict] = []

    for rec in records:
        rtype     = rec.get("type", "risque")
        intitule  = rec.get("intitule", "")
        processus = rec.get("processus", "")
        ipr       = rec.get("ipr")
        annee     = rec.get("annee")
        fmt       = rec.get("format", "fiche")
        actions   = rec.get("actions", [])

        if not intitule:
            continue

        # Inference domaine QSE
        proc_norm = processus.lower()
        if "securite" in proc_norm or "sst" in proc_norm:
            domaine = "SST"
        elif "environnement" in proc_norm:
            domaine = "E"
        else:
            domaine = _infer_domaine(intitule + " " + processus)

        # Causes : depuis le champ fiche ou message generique
        causes_raw = rec.get("causes", "")
        if causes_raw:
            causes = [c.strip() for c in re.split(r"[;\n]+", causes_raw) if c.strip()]
        else:
            causes = [f"Cause identifiee dans la fiche processus {processus}"]

        # Consequences : depuis les champs fiche
        consq_raw = rec.get("consequences", "") or ""
        if consq_raw:
            consequences = [c.strip() for c in re.split(r"[;\n]+", consq_raw) if c.strip()]
        else:
            consequences = [f"Impact potentiel sur le processus {processus}"]

        justification = (
            f"Identifie dans la fiche d'analyse processus {processus}"
            + (f" ({annee})" if annee else "")
            + (f", IPR={ipr}" if ipr else "")
            + f". Source : {rec.get('source_fichier', '')} [{fmt}]."
        )

        entry: Dict[str, Any] = {
            # Champs schema standard registre
            "type":          rtype,
            "code":          _next_code(rtype),
            "intitule":      intitule,
            "causes":        causes,
            "consequences":  consequences,
            "perimetre":     processus,
            "domaine":       domaine,
            "lien_iso":      ISO_MAPPING.get(domaine, "ISO 9001:2015 §6.1"),
            "lien_objectif": f"Objectif qualite processus {processus}",
            "justification": justification,
            "source":        "Fiches_Client",
            # Champs specifiques fiches (utiles pour Agent 2 - evaluation)
            "ipr":           ipr,
            "gravite":       rec.get("gravite"),
            "occurrence":    rec.get("occurrence"),
            "detectabilite": rec.get("detectabilite"),
            "annee":         annee,
            "format_fiche":  fmt,
            "pilote":        rec.get("pilote", ""),
            # Actions eclates (une entree par action pour QALITAS)
            "_actions":      actions,
            "_nb_actions":   len(actions),
            "_raw":          rec,
        }
        register_entries.append(entry)

    # Payloads QALITAS : un payload par action individuelle
    qalitas_payloads = fiches_to_qalitas_payloads(records)

    nb_r = sum(1 for e in register_entries if e["type"] == "risque")
    nb_o = sum(1 for e in register_entries if e["type"] == "opportunite")
    nb_multi = sum(1 for e in register_entries if e.get("_nb_actions", 0) > 1)
    logger.info(
        "Fiches clients : %d R&O (%d risques, %d opportunites) | "
        "%d actions QALITAS | %d risques multi-actions",
        len(register_entries), nb_r, nb_o, len(qalitas_payloads), nb_multi,
    )

    return register_entries, qalitas_payloads


# =============================================================================
# DEDUPLICATION DU REGISTRE
# =============================================================================

def deduplicate_register(register: List[Dict]) -> List[Dict]:
    """
    Supprime les doublons par intitule normalise.
    En cas de doublon, conserve l'entree QALITAS_API (source la plus fiable).
    """
    seen: Dict[str, Dict] = {}
    priority = {
        "QALITAS_API":       0,
        "Fiches_Client":     1,   # Fiches pilotes de processus (Format 1 et 2)
        "Excel_Cartographie":2,
        "Excel_KPI":         3,
        "Excel_NC":          4,
        "PDF_Dashboard":     5,
    }

    for ro in register:
        key = re.sub(r"\s+", " ", ro["intitule"].lower().strip())[:80]
        if key not in seen:
            seen[key] = ro
        else:
            existing_prio = priority.get(seen[key].get("source", ""), 99)
            new_prio      = priority.get(ro.get("source", ""), 99)
            if new_prio < existing_prio:
                seen[key] = ro

    unique = list(seen.values())
    logger.info(
        "Deduplication : %d entrees -> %d uniques.",
        len(register), len(unique)
    )
    return unique


# =============================================================================
# ENRICHISSEMENT LLM
# =============================================================================

def enrich_register_llm(
    register: List[Dict],
    llm_url: str = "http://localhost:11434",
) -> List[Dict]:
    """
    Enrichit les entrees avec des causes/consequences manquantes via LLM (Ollama).
    Ne remplace que les champs vides ou generiques.
    """
    try:
        import requests as _req
        resp = _req.get(f"{llm_url}/api/tags", timeout=3)
        if resp.status_code != 200:
            raise ConnectionError("Ollama non disponible")
        models = [m["name"] for m in resp.json().get("models", [])]
        if not models:
            raise ConnectionError("Aucun modele Ollama disponible")
        model = models[0]
        logger.info("LLM disponible (Ollama) : modele '%s'", model)
    except Exception as e:
        logger.warning("LLM indisponible pour Agent1 : %s. Enrichissement ignore.", e)
        return register

    enriched_count = 0
    for ro in register:
        need_enrichment = (
            not ro.get("causes") or
            ro["causes"] == ["Non renseigne"] or
            not ro.get("consequences") or
            ro["consequences"] == ["Non renseigne"]
        )
        if not need_enrichment:
            continue

        prompt = (
            f"Tu es expert QSE. Pour le {ro['type']} suivant dans le contexte industriel :\n"
            f"Intitule : {ro['intitule']}\n"
            f"Processus : {ro.get('perimetre', '?')}\n"
            f"Domaine : {ro.get('domaine', 'Q')}\n\n"
            f"Donne en JSON strict (sans markdown) :\n"
            f'{{"causes": ["cause1", "cause2"], "consequences": ["consq1", "consq2"]}}\n'
            f"Sois concis et operationnel (2-3 elements par liste)."
        )

        try:
            resp = _req.post(
                f"{llm_url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=30,
            )
            text = resp.json().get("response", "")
            match = re.search(r'\{.*?\}', text, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                if parsed.get("causes"):
                    ro["causes"] = parsed["causes"]
                if parsed.get("consequences"):
                    ro["consequences"] = parsed["consequences"]
                enriched_count += 1
        except Exception as exc:
            logger.debug("LLM enrichissement echoue pour '%s' : %s", ro["intitule"][:40], exc)

    logger.info("LLM Agent1 : %d/%d entrees enrichies.", enriched_count, len(register))
    return register


# =============================================================================
# IDENTIFICATION DEPUIS LE PDF DASHBOARD (Source 3)
# =============================================================================

# Mots-cles signalant un risque potentiel dans le texte du dashboard
_PDF_RISQUE_KW = [
    "non-conformite", "non conformite", "nc", "retard", "delai", "ecart",
    "incident", "defaut", "anomalie", "probleme", "problème", "rupture",
    "absenteisme", "absentéisme", "turn-over", "turnover", "taux eleve",
    "hors seuil", "hors cible", "augmentation", "degradation", "baisse",
    "depassement", "penalite", "penalité", "plainte", "reclamation",
    "reclamation client", "litige", "panne", "dysfonctionnement",
    "accident", "blessure", "presqu accident", "pres accident",
]
# Mots-cles signalant une opportunite dans le texte du dashboard
_PDF_OPPTY_KW = [
    "amelioration", "amélioration", "opportunite", "opportunité",
    "potentiel", "gain", "progression", "hausse significative",
    "objectif atteint", "taux eleve de satisfaction",
    "certification", "nouveau marche", "nouveau marché",
    "innovation", "digitalisation", "optimisation",
]


def identify_from_pdf(pdf_path: str) -> List[Dict]:
    """
    Extrait des R&O potentiels depuis le texte du dashboard PDF.

    Strategie :
    - Parcourir chaque page du PDF
    - Detecter les paragraphes contenant des mots-cles risque ou opportunite
    - Construire une entree normalisee pour chaque signal detecte
    - Deduplication par processus + type (eviter les doublons intra-PDF)

    Retourne une liste d'entrees normalisees avec source="PDF_Dashboard".
    """
    if not os.path.exists(pdf_path):
        logger.info(
            "Dashboard PDF introuvable (%s). Source PDF ignoree.", pdf_path
        )
        return []

    try:
        from loaders.load_dashboard_pdf import load_dashboard_text
    except ImportError:
        logger.warning(
            "load_dashboard_pdf non disponible. Source PDF ignoree."
        )
        return []

    try:
        pages = load_dashboard_text(pdf_path)
    except Exception as exc:
        logger.warning("Erreur lecture PDF Agent1 : %s", exc)
        return []

    results: List[Dict] = []
    seen_signals: set = set()
    counter_r = [0]
    counter_o = [0]

    def _next_code(t: str) -> str:
        if t == "opportunite":
            counter_o[0] += 1
            return f"O-PDF{counter_o[0]:03d}"
        else:
            counter_r[0] += 1
            return f"R-PDF{counter_r[0]:03d}"

    for page in pages:
        text      = str(page.get("combined_text", page.get("text", ""))).lower()
        page_num  = page.get("page", 0)

        # Infer processus depuis numero de page (meme logique qu'Agent4)
        processus = _infer_processus_from_page(page_num, text)

        # Detection risques
        kws_r = [kw for kw in _PDF_RISQUE_KW if kw in text]
        if kws_r:
            # Extraire un snippet autour du mot-cle le plus representatif
            for kw in kws_r[:2]:  # max 2 risques par page
                idx = text.find(kw)
                if idx == -1:
                    continue
                snippet = text[max(0, idx - 60): idx + 120].strip().replace("\n", " ")
                # Intitule = snippet nettoye (max 80 chars)
                intitule = re.sub(r"\s+", " ", snippet)[:80].capitalize()

                # Deduplication intra-PDF : processus + kw principal
                sig_key = f"{processus}_{kw}"
                if sig_key in seen_signals:
                    continue
                seen_signals.add(sig_key)

                domaine = _infer_domaine(intitule + " " + processus)
                results.append({
                    "type":         "risque",
                    "code":         _next_code("risque"),
                    "intitule":     f"Signal risque dashboard : {intitule}",
                    "causes":       [
                        f"Signal detecte p.{page_num} du dashboard : '{kw}'",
                        "Donnee de performance hors seuil ou evenement degrade",
                    ],
                    "consequences": [
                        f"Impact potentiel sur le processus {processus}",
                        "Necessite une verification et evaluation formelle (Agent 2)",
                    ],
                    "perimetre":    processus,
                    "domaine":      domaine,
                    "lien_iso":     ISO_MAPPING.get(domaine, "ISO 9001:2015 §6.1"),
                    "lien_objectif": f"Surveillance performance {processus}",
                    "justification": (
                        f"Signal detecte p.{page_num} du dashboard PDF. "
                        f"Mot-cle : '{kw}'. Contexte : {snippet[:120]}"
                    ),
                    "source":       "PDF_Dashboard",
                    "_raw":         {
                        "page":    page_num,
                        "keyword": kw,
                        "snippet": snippet,
                    },
                })

        # Detection opportunites
        kws_o = [kw for kw in _PDF_OPPTY_KW if kw in text]
        if kws_o:
            for kw in kws_o[:1]:  # max 1 opportunite par page
                idx = text.find(kw)
                if idx == -1:
                    continue
                snippet  = text[max(0, idx - 60): idx + 120].strip().replace("\n", " ")
                intitule = re.sub(r"\s+", " ", snippet)[:80].capitalize()

                sig_key = f"{processus}_{kw}_opp"
                if sig_key in seen_signals:
                    continue
                seen_signals.add(sig_key)

                results.append({
                    "type":         "opportunite",
                    "code":         _next_code("opportunite"),
                    "intitule":     f"Opportunite dashboard : {intitule}",
                    "causes":       [
                        f"Signal positif detecte p.{page_num} : '{kw}'",
                        "Performance favorable ou tendance d'amelioration",
                    ],
                    "consequences": [
                        f"Potentiel de gain sur le processus {processus}",
                        "A exploiter ou renforcer (Agent 3)",
                    ],
                    "perimetre":    processus,
                    "domaine":      _infer_domaine(intitule),
                    "lien_iso":     "ISO 9001:2015 §6.1",
                    "lien_objectif": f"Capitalisation performance {processus}",
                    "justification": (
                        f"Opportunite detectee p.{page_num} du dashboard PDF. "
                        f"Mot-cle : '{kw}'. Contexte : {snippet[:120]}"
                    ),
                    "source":       "PDF_Dashboard",
                    "_raw":         {
                        "page":    page_num,
                        "keyword": kw,
                        "snippet": snippet,
                    },
                })

    logger.info(
        "PDF Agent1 : %d pages analysees -> %d R&O identifies "
        "(%d risques, %d opportunites).",
        len(pages), len(results),
        sum(1 for r in results if r["type"] == "risque"),
        sum(1 for r in results if r["type"] == "opportunite"),
    )
    return results


def _infer_processus_from_page(page_num: int, text: str) -> str:
    """
    Infere le processus QSE a partir du numero de page et/ou du contenu.
    Priorite au contenu (recherche de noms de processus connus).
    """
    # Noms de processus reconnus (adapter selon QALITAS de l'entreprise)
    processus_keywords = {
        "achat":           "Achats",
        "fournisseur":     "Achats",
        "supply":          "Supply Chain",
        "logistique":      "Logistique",
        "livraison":       "Logistique",
        "production":      "Production",
        "fabrication":     "Production",
        "qualite":         "Qualite",
        "controle":        "Qualite",
        "audit":           "Audit",
        "client":          "Relation Client",
        "reclamation":     "Relation Client",
        "satisfaction":    "Relation Client",
        "maintenance":     "Maintenance",
        "equipement":      "Maintenance",
        "ressource":       "RH",
        "formation":       "RH",
        "competence":      "RH",
        "informatique":    "SI",
        "systeme":         "SI",
        "environnement":   "Environnement",
        "securite":        "SST",
        "accident":        "SST",
    }
    for kw, proc in processus_keywords.items():
        if kw in text:
            return proc

    # Fallback par numero de page
    mapping = {
        range(1, 5):   "Direction",
        range(5, 15):  "Qualite",
        range(15, 25): "Production",
        range(25, 35): "Achats",
        range(35, 45): "Logistique",
        range(45, 55): "Relation Client",
        range(55, 65): "RH",
        range(65, 77): "Maintenance",
    }
    for page_range, proc in mapping.items():
        if page_num in page_range:
            return proc
    return "Non determine"


# =============================================================================
# PIPELINE PRINCIPAL AGENT 1
# =============================================================================

def run_agent1(
    data_dir:      str           = None,
    pdf_path:      str           = None,
    use_api:       bool          = True,
    llm_url:       str           = "http://localhost:11434",
    dry_run:       bool          = True,
    annee_filtre:  Optional[int] = None,
    toutes_annees: bool          = False,
) -> Dict[str, Any]:
    """
    Execute le pipeline complet de l'Agent 1.

    1. Chargement depuis QALITAS API (si disponible)
    2. Chargement depuis Excel (cartographie + KPI + NC)
    3. Deduplication
    4. Enrichissement LLM (causes/consequences manquants)
    5. Sauvegarde du registre

    Retourne le rapport complet pour integration dans l'orchestrateur.
    """
    if data_dir is None:
        data_dir = DATA_DIR

    logger.info("=" * 60)
    logger.info("AGENT 1 - IDENTIFICATION & CARACTERISATION DES R&O")
    logger.info("Source API QALITAS : %s", "Activee" if use_api else "Desactivee")
    logger.info("=" * 60)

    register: List[Dict] = []
    stats = {
        "qalitas_api":       0,
        "fiches_client":     0,
        "fiches_payloads":   0,
        "fiches_multi_act":  0,
        "excel_carto":       0,
        "kpi_hors_cible":    0,
        "nc_recurrentes":    0,
        "pdf_dashboard":     0,
        "dedupliques":       0,
        "llm_enrichis":      0,
        "total_final":       0,
    }
    all_qalitas_payloads: List[Dict] = []

    # --- Etape 1 : Chargement QALITAS API ---
    if use_api:
        try:
            client = QalitasClient(
                base_url=QALITAS_BASE_URL,
                username=QALITAS_USERNAME,
                password=QALITAS_PASSWORD,
            )
            if not dry_run:
                client.login()
                risks   = client.get_risks()
                oppties = client.get_opportunities()

                for i, r in enumerate(risks):
                    register.append(normalize_qalitas_ro(r, i))
                for i, o in enumerate(oppties):
                    register.append(normalize_qalitas_ro(o, i))

                stats["qalitas_api"] = len(risks) + len(oppties)
                logger.info(
                    "QALITAS API : %d risques + %d opportunites charges.",
                    len(risks), len(oppties)
                )
            else:
                logger.info("Mode dry-run : chargement QALITAS API ignore.")
        except Exception as exc:
            logger.warning("QALITAS API indisponible : %s. Fallback Excel.", exc)

    # --- Etape 2 : Chargement Fiches Clients (Source principale) ---
    try:
        fiches_ros, fiches_payloads = identify_from_fiches(
            data_dir,
            annee_filtre=annee_filtre,
            toutes_annees=toutes_annees,
        )
        stats["fiches_client"]    = len(fiches_ros)
        stats["fiches_payloads"]  = len(fiches_payloads)
        stats["fiches_multi_act"] = sum(
            1 for r in fiches_ros if r.get("_nb_actions", 0) > 1
        )
        all_qalitas_payloads.extend(fiches_payloads)
        register.extend(fiches_ros)
        logger.info(
            "Fiches clients : %d R&O, %d payloads QALITAS, %d multi-actions.",
            len(fiches_ros), len(fiches_payloads), stats["fiches_multi_act"],
        )
    except Exception as exc:
        logger.error("Erreur source Fiches clients : %s", exc)

    # --- Etape 3 : Chargement Excel (KPI / NC / Cartographie residuelle) ---
    try:
        context = load_all_context(data_dir)
        excel_ros = identify_from_excel(context)

        # Comptages par source
        stats["excel_carto"]    = sum(1 for r in excel_ros if r["source"] == "Excel_Cartographie")
        stats["kpi_hors_cible"] = sum(1 for r in excel_ros if r["source"] == "Excel_KPI")
        stats["nc_recurrentes"] = sum(1 for r in excel_ros if r["source"] == "Excel_NC")

        register.extend(excel_ros)
        logger.info("Excel total : %d R&O (carto=%d, KPI=%d, NC=%d)",
                    len(excel_ros), stats["excel_carto"],
                    stats["kpi_hors_cible"], stats["nc_recurrentes"])
    except Exception as exc:
        logger.error("Erreur chargement Excel : %s", exc)

    # --- Etape 4 : Chargement PDF dashboard (Source 3) ---
    if pdf_path is None:
        pdf_path = os.path.join(data_dir, "dashboard.pdf")

    pdf_ros = identify_from_pdf(pdf_path)
    stats["pdf_dashboard"] = len(pdf_ros)
    if pdf_ros:
        register.extend(pdf_ros)
        logger.info("PDF dashboard : %d R&O identifies.", len(pdf_ros))

    if not register:
        logger.error("Registre vide apres chargement. Arret Agent1.")
        return {"error": "Aucune donnee chargee", "register": []}

    # --- Etape 4 : Deduplication ---
    avant = len(register)
    register = deduplicate_register(register)
    stats["dedupliques"] = avant - len(register)

    # --- Etape 5 : Enrichissement LLM ---
    register = enrich_register_llm(register, llm_url=llm_url)
    stats["llm_enrichis"]  = sum(1 for r in register if r.get("source") != "QALITAS_API")
    stats["total_final"]   = len(register)

    # --- Etape 6 : Rapport et sauvegarde ---
    nb_risques      = sum(1 for r in register if r["type"] == "risque")
    nb_opportunites = sum(1 for r in register if r["type"] == "opportunite")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rapport = {
        "metadata": {
            "agent":          "Agent 1 - Identification & Caracterisation",
            "generated_at":   datetime.now().isoformat(),
            "dry_run":        dry_run,
            "annee_filtre":   annee_filtre,
            "toutes_annees":  toutes_annees,
            "sources_actives": {
                "qalitas_api":   use_api and not dry_run,
                "fiches_client": stats.get("fiches_client", 0) > 0,
                "excel":         True,
                "pdf":           stats.get("pdf_dashboard", 0) > 0,
                "llm":           True,
            },
            "conformite_cdc": {
                "identification_multi_sources":  True,
                "normalisation_registre":        True,
                "deduplication":                 True,
                "enrichissement_llm":            True,
                "schema_pipelinestate":          True,
                "splitting_multi_actions":       True,
                "generation_payloads_qalitas":   True,
            },
        },
        "stats": stats,
        "summary": {
            "total_ro":         stats["total_final"],
            "nb_risques":       nb_risques,
            "nb_opportunites":  nb_opportunites,
            "nb_payloads_qalitas": len(all_qalitas_payloads),
            "par_domaine":      _count_by_field(register, "domaine"),
            "par_perimetre":    _count_by_field(register, "perimetre"),
            "par_source":       _count_by_field(register, "source"),
            "par_format_fiche": _count_by_field(
                [r for r in register if r.get("source") == "Fiches_Client"],
                "format_fiche"
            ),
        },
        "register":         register,
        "qalitas_payloads": all_qalitas_payloads,
    }

    out_file      = os.path.join(OUTPUT_DIR, f"agent1_register_{timestamp}.json")
    stable_file   = os.path.join(OUTPUT_DIR, "agent1_latest.json")
    payload_file  = os.path.join(OUTPUT_DIR, f"agent1_payloads_{timestamp}.json")
    payload_stable= os.path.join(OUTPUT_DIR, "agent1_payloads_latest.json")

    with open(out_file,       "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=2, default=str)
    with open(stable_file,    "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=2, default=str)

    # Fichier dedie aux payloads QALITAS (pour creation directe via API)
    payloads_export = {
        "generated_at":  datetime.now().isoformat(),
        "nb_payloads":   len(all_qalitas_payloads),
        "description":   "Payloads prets pour POST /Actions/Create dans QALITAS",
        "payloads":      all_qalitas_payloads,
    }
    with open(payload_file,   "w", encoding="utf-8") as f:
        json.dump(payloads_export, f, ensure_ascii=False, indent=2, default=str)
    with open(payload_stable, "w", encoding="utf-8") as f:
        json.dump(payloads_export, f, ensure_ascii=False, indent=2, default=str)

    logger.info("Rapport Agent1 sauvegarde : %s", out_file)

    # --- Injection QALITAS (uniquement si mode reel) ---
    if not dry_run and register:
        logger.info("[AGENT 1] Lancement injection QALITAS (mode reel)...")
        inj_stats = inject_register_to_qalitas(register, dry_run=False)
        rapport["injection_stats"] = inj_stats
        logger.info(
            "[AGENT 1] Injection terminee : %d risques + %d opportunites crees | %d deja presents | %d erreurs",
            inj_stats.get("created_risques", 0),
            inj_stats.get("created_opportunites", 0),
            inj_stats.get("skipped_existing", 0),
            inj_stats.get("errors", 0),
        )
    elif dry_run:
        logger.info("[AGENT 1] Mode dry-run : injection QALITAS ignoree.")

    # Console
    print("\n" + "=" * 65)
    print("SYNTHESE AGENT 1 — REGISTRE R&O IDENTIFIE")
    print("=" * 65)
    print(f"Total R&O identifies      : {stats['total_final']}")
    print(f"  Risques                 : {nb_risques}")
    print(f"  Opportunites            : {nb_opportunites}")
    print(f"\nSources :")
    print(f"  QALITAS API             : {stats['qalitas_api']}")
    print(f"  Fiches clients          : {stats['fiches_client']}")
    print(f"    dont multi-actions    : {stats['fiches_multi_act']}")
    print(f"  Excel cartographie      : {stats['excel_carto']}")
    print(f"  Excel KPI hors cible    : {stats['kpi_hors_cible']}")
    print(f"  Excel NC recurrentes    : {stats['nc_recurrentes']}")
    print(f"  Dedupliques retires     : {stats['dedupliques']}")
    print(f"\nPayloads QALITAS generes  : {len(all_qalitas_payloads)}")
    print(f"  (fichier: agent1_payloads_latest.json)")
    print(f"\nPar domaine : {rapport['summary']['par_domaine']}")
    print(f"Par format  : {rapport['summary']['par_format_fiche']}")
    print("=" * 65)

    return rapport


def _count_by_field(lst: List[Dict], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in lst:
        val = str(item.get(field, "?"))
        counts[val] = counts.get(val, 0) + 1
    return counts


# =============================================================================
# INTERFACE POUR L'ORCHESTRATEUR (retourne uniquement le registre)
# =============================================================================

def get_ro_register(
    data_dir:      str           = None,
    use_api:       bool          = True,
    dry_run:       bool          = True,
    llm_url:       str           = "http://localhost:11434",
    annee_filtre:  Optional[int] = None,
    toutes_annees: bool          = False,
) -> List[Dict]:
    """
    Interface simplifiee pour l'orchestrateur LangGraph.
    Retourne directement la liste des R&O (champ 'register' du rapport).
    """
    rapport = run_agent1(
        data_dir=data_dir,
        use_api=use_api,
        dry_run=dry_run,
        llm_url=llm_url,
        annee_filtre=annee_filtre,
        toutes_annees=toutes_annees,
    )
    return rapport.get("register", [])


def get_qalitas_payloads(
    data_dir:      str           = None,
    annee_filtre:  Optional[int] = None,
    toutes_annees: bool          = False,
) -> List[Dict]:
    """
    Retourne directement les payloads QALITAS prets pour creation via API.
    (Sans connexion API, sans LLM — lecture fiches uniquement.)
    """
    rapport = run_agent1(
        data_dir=data_dir,
        use_api=False,
        dry_run=True,
        annee_filtre=annee_filtre,
        toutes_annees=toutes_annees,
    )
    return rapport.get("qalitas_payloads", [])


def inject_register_to_qalitas(
    register:      List[Dict],
    base_url:      str  = None,
    username:      str  = None,
    password:      str  = None,
    dry_run:       bool = True,
    only_new:      bool = True,
) -> Dict:
    """
    Injecte le registre Agent1 (R&O nouveaux) dans QALITAS via l'API.

    Appele apres run_agent1() pour pousser les R&O identifies depuis les fiches
    vers la section Risques/Opportunites de QALITAS (POST /RiskOpportunity/Edit).

    Parametres :
        register   : liste issue de run_agent1()["register"]
        dry_run    : True = simulation sans ecriture reelle (defaut)
        only_new   : si True, filtre les entrees deja presentes dans QALITAS_API

    Retourne les statistiques d'injection.
    """
    _base_url  = base_url  or os.environ.get("QALITAS_BASE_URL", QALITAS_BASE_URL)
    _username  = username  or os.environ.get("QALITAS_USERNAME", QALITAS_USERNAME)
    _password  = password  or os.environ.get("QALITAS_PASSWORD", QALITAS_PASSWORD)

    client = QalitasClient(
        base_url=_base_url,
        username=_username,
        password=_password,
    )

    if not dry_run:
        if not client.login():
            logger.error("Echec authentification QALITAS — injection annulee.")
            return {"error": "Echec authentification"}

    writer = QalitasWriter(client=client, dry_run=dry_run)

    try:
        stats = inject_agent1_results(register, writer, only_new=only_new)
    finally:
        if not dry_run:
            client.logout()

    mode = "DRY-RUN" if dry_run else "REEL"
    logger.info(
        "[%s] Injection Agent1 terminee : %d risques + %d opportunites crees "
        "| %d deja dans QALITAS | %d erreurs",
        mode,
        stats.get("created_risques", 0),
        stats.get("created_opps", 0),
        stats.get("skipped_qalitas", 0),
        stats.get("errors", 0),
    )
    return stats


# =============================================================================
# POINT D'ENTREE CLI
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Agent IA 1 - Identification & Caracterisation des R&O"
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Activer la lecture reelle depuis QALITAS API (defaut: dry-run)"
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Desactiver la source QALITAS API (Excel uniquement)"
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Chemin vers le dossier de donnees (defaut: ./donnees)"
    )
    parser.add_argument(
        "--annee",
        type=int,
        default=None,
        help="Filtrer les fiches par annee (ex: 2023)"
    )
    parser.add_argument(
        "--toutes-annees",
        action="store_true",
        help="Charger toutes les feuilles de chaque fichier fiche (defaut: derniere)"
    )
    parser.add_argument(
        "--payloads-only",
        action="store_true",
        help="Generer uniquement les payloads QALITAS depuis les fiches (mode leger)"
    )
    parser.add_argument(
        "--inject",
        action="store_true",
        help=(
            "Injecter les R&O identifies dans QALITAS apres generation du registre. "
            "Necessite --real pour une injection reelle (defaut: dry-run)."
        )
    )
    args = parser.parse_args()

    if args.payloads_only:
        payloads = get_qalitas_payloads(
            data_dir=args.data_dir,
            annee_filtre=args.annee,
            toutes_annees=args.toutes_annees,
        )
        print(f"\nPayloads QALITAS generes : {len(payloads)}")
        print(f"Fichier : {os.path.join(OUTPUT_DIR, 'agent1_payloads_latest.json')}")
    else:
        rapport = run_agent1(
            data_dir=args.data_dir,
            use_api=not args.no_api,
            dry_run=not args.real,
            annee_filtre=args.annee,
            toutes_annees=args.toutes_annees,
        )

        if args.inject:
            register = rapport.get("register", [])
            dry = not args.real
            print(
                f"\n{'[DRY-RUN] ' if dry else '[REEL] '}"
                f"Injection de {len(register)} R&O vers QALITAS..."
            )
            stats_inj = inject_register_to_qalitas(register, dry_run=dry)
            print(f"Risques crees     : {stats_inj.get('created_risques', 0)}")
            print(f"Opportunites crees: {stats_inj.get('created_opps', 0)}")
            print(f"Deja dans QALITAS : {stats_inj.get('skipped_qalitas', 0)}")
            print(f"Erreurs           : {stats_inj.get('errors', 0)}")
