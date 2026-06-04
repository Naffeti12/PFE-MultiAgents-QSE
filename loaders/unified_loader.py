"""
Unified Data Loader — QALITAS QSE Multi-Agents
===============================================
Charge les donnees depuis TOUTES les sources disponibles et retourne
TOUJOURS le meme format de contexte, quelle que soit la source.

Sources supportees :
    1. QALITAS API    -> risques, NC, KPIs, reclamations officiels (avec GUIDs)
    2. Excel donnees/ -> cartographie, KPIs, NC, fiches clients
    3. PDF  donnees/  -> dashboards qualite

Principe d'homogeneite :
    Le contexte retourne a TOUJOURS les memes cles :
        kpis, nc, reclamations, risques, cartographie,
        kpi_index, risque_index, nc_summary, reclamation_summary
    Les agents n'ont pas besoin de savoir d'ou viennent les donnees.

Utilisation :
    L'entreprise depose ses fichiers Excel/PDF dans donnees/.
    Le loader les detecte et les fusionne avec les donnees QALITAS API.
    QALITAS API reste prioritaire (contient les GUIDs officiels).
"""

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger("unified_loader")

# Dossier de donnees (Excel + PDF clients)
DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "donnees"
)


# =============================================================================
# UTILITAIRES
# =============================================================================

def _scan_folder(folder: str, extensions: List[str]) -> List[str]:
    """Liste les fichiers du dossier correspondant aux extensions."""
    if not os.path.isdir(folder):
        return []
    files = []
    for fname in sorted(os.listdir(folder)):
        fpath = os.path.join(folder, fname)
        if os.path.isfile(fpath):
            if any(fname.lower().endswith(ext) for ext in extensions):
                files.append(fpath)
    return files


def _merge_lists(base: List, new: List) -> List:
    """Fusionne deux listes en evitant les doublons stricts."""
    if not new:
        return base
    return base + [item for item in new if item not in base]


# =============================================================================
# CHARGEMENT EXCEL (donnees/)
# =============================================================================

def load_excel_donnees(data_dir: str) -> Dict[str, List]:
    """
    Charge tous les fichiers Excel du dossier donnees/.
    Detecte automatiquement le type de chaque fichier par son nom et ses colonnes.

    Types detectes :
        - Fiches/Grilles risques   -> cartographie + fiches
        - KPIs                     -> kpis
        - NC                       -> nc
        - Reclamations             -> reclamations
        - Cartographie             -> cartographie
        - Autres                   -> auto-detection par colonnes
    """
    from loaders.load_excel_data import (
        load_kpis, load_nc, load_reclamations,
        load_cartographie, load_fiches_risque_from_dir,
        read_sheet,
    )

    result: Dict[str, List] = {
        "kpis": [], "nc": [], "reclamations": [],
        "cartographie": [], "fiches": [],
    }

    excel_files = _scan_folder(data_dir, [".xlsx", ".xls"])
    if not excel_files:
        logger.info("[Excel] Aucun fichier Excel dans %s", data_dir)
        return result

    # 1. Fiches et grilles de risques (format clients)
    try:
        fiches = load_fiches_risque_from_dir(data_dir)
        if fiches:
            result["fiches"].extend(fiches)
            logger.info("[Excel] Fiches/Grilles : %d enregistrements", len(fiches))
    except Exception as e:
        logger.warning("[Excel] Chargement fiches echoue : %s", e)

    # 2. Fichiers identifies par nom
    KNOWN_FILES = {
        "KPI's.xlsx":                    ("kpis",         load_kpis),
        "NC.xlsx":                        ("nc",           load_nc),
        "Réclamations clients.xlsx":      ("reclamations", load_reclamations),
        "Reclamations clients.xlsx":      ("reclamations", load_reclamations),
        "Cartographie des risques.xlsx":  ("cartographie", load_cartographie),
    }

    for fpath in excel_files:
        fname = os.path.basename(fpath)
        fname_lower = fname.lower()

        # Fichier connu exactement
        if fname in KNOWN_FILES:
            key, loader = KNOWN_FILES[fname]
            try:
                rows = loader(fpath)
                result[key].extend(rows)
                logger.info("[Excel] '%s' -> %s : %d lignes", fname, key, len(rows))
            except Exception as e:
                logger.warning("[Excel] Erreur '%s' : %s", fname, e)
            continue

        # Detection par mots-cles dans le nom
        try:
            if any(k in fname_lower for k in ["kpi", "indicateur", "indicator"]):
                rows = load_kpis(fpath)
                result["kpis"].extend(rows)
                logger.info("[Excel] KPI auto '%s' : %d lignes", fname, len(rows))

            elif any(k in fname_lower for k in ["nc", "non-conform", "nonconform"]):
                rows = load_nc(fpath)
                result["nc"].extend(rows)
                logger.info("[Excel] NC auto '%s' : %d lignes", fname, len(rows))

            elif any(k in fname_lower for k in ["reclamation", "reclamation", "plainte", "complaint"]):
                rows = load_reclamations(fpath)
                result["reclamations"].extend(rows)
                logger.info("[Excel] Reclamations auto '%s' : %d lignes", fname, len(rows))

            elif any(k in fname_lower for k in ["carto", "cartographie"]):
                rows = load_cartographie(fpath)
                result["cartographie"].extend(rows)
                logger.info("[Excel] Cartographie auto '%s' : %d lignes", fname, len(rows))

            elif any(k in fname_lower for k in ["fiche", "grille", "risque", "risk"]):
                # Deja traite par load_fiches_risque_from_dir, skip
                pass

            else:
                # Fichier inconnu : detection par colonnes
                rows = read_sheet(fpath)
                if not rows:
                    continue
                cols = set(str(k).lower() for k in rows[0].keys())

                if any(c in cols for c in ["cible", "objectif", "kpi", "indicateur du kpi"]):
                    result["kpis"].extend(rows)
                    logger.info("[Excel] KPI colonnes '%s' : %d lignes", fname, len(rows))
                elif any(c in cols for c in ["etat", "detectee le", "source", "type nc", "categorie nc"]):
                    result["nc"].extend(rows)
                    logger.info("[Excel] NC colonnes '%s' : %d lignes", fname, len(rows))
                elif any(c in cols for c in ["rpn", "gravite", "frequence", "appreciation"]):
                    result["cartographie"].extend(rows)
                    logger.info("[Excel] Cartographie colonnes '%s' : %d lignes", fname, len(rows))
                elif any(c in cols for c in ["produit", "client", "reclamation", "nature"]):
                    result["reclamations"].extend(rows)
                    logger.info("[Excel] Reclamations colonnes '%s' : %d lignes", fname, len(rows))
                else:
                    logger.debug("[Excel] Fichier non identifie ignore : %s", fname)

        except Exception as e:
            logger.warning("[Excel] Erreur lecture '%s' : %s", fname, e)

    return result


# =============================================================================
# CHARGEMENT PDF (donnees/)
# =============================================================================

def load_pdf_donnees(data_dir: str) -> List[Dict]:
    """
    Charge tous les fichiers PDF du dossier donnees/.
    Retourne une liste de pages (format standard load_dashboard_text).
    """
    from loaders.load_dashboard_pdf import load_dashboard_text

    pdf_files = _scan_folder(data_dir, [".pdf"])
    if not pdf_files:
        return []

    logger.info("[PDF] %d fichier(s) detecte(s) dans donnees/", len(pdf_files))

    all_pages = []
    for fpath in pdf_files:
        try:
            pages = load_dashboard_text(fpath)
            for page in pages:
                page["source_pdf"] = os.path.basename(fpath)
            all_pages.extend(pages)
            logger.info("[PDF] '%s' : %d pages", os.path.basename(fpath), len(pages))
        except Exception as e:
            logger.error("[PDF] Erreur '%s' : %s", os.path.basename(fpath), e)

    return all_pages


# =============================================================================
# LOADER UNIFIE PRINCIPAL
# =============================================================================

def load_unified_context(
    data_dir: str = None,
    qalitas_base_url: str = None,
    qalitas_username: str = None,
    qalitas_password: str = None,
    prefer_api: bool = True,
) -> Dict[str, Any]:
    """
    Charge et fusionne TOUTES les sources disponibles.
    Retourne TOUJOURS le meme format de contexte.

    Ordre de priorite (en cas de doublon) :
        1. QALITAS API   (source officielle avec GUIDs)
        2. Excel donnees/ (cartographie + fichiers clients)
        3. PDF  donnees/  (dashboards qualite)

    Les agents n'ont pas besoin de savoir d'ou viennent les donnees.
    """
    import os as _os
    try:
        from dotenv import load_dotenv
        load_dotenv(
            _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), ".env"),
            override=False,
        )
    except ImportError:
        pass

    if data_dir is None:
        data_dir = DATA_DIR
    if qalitas_base_url is None:
        qalitas_base_url = _os.environ.get(
            "QALITAS_BASE_URL",
            "https://timserver.northeurope.cloudapp.azure.com/QalitasDemo"
        )
    if qalitas_username is None:
        qalitas_username = _os.environ.get("QALITAS_USERNAME", "MOHAMED.N")
    if qalitas_password is None:
        qalitas_password = _os.environ.get("QALITAS_PASSWORD", "MOHAMED.N")

    logger.info("UNIFIED LOADER — %s", data_dir)

    # ------------------------------------------------------------------
    # Etape 1 : Source principale (QALITAS API ou Excel donnees/)
    # ------------------------------------------------------------------
    context: Dict[str, Any] = {}
    try:
        from loaders.qalitas_api_client import load_all_context_hybrid
        context = load_all_context_hybrid(
            data_dir=data_dir,
            base_url=qalitas_base_url,
            username=qalitas_username,
            password=qalitas_password,
            prefer_api=prefer_api,
        )
        logger.info("[Etape 1] Source principale : mode=%s | %d risques | %d carto",
                    context.get("_load_mode", "?"),
                    len(context.get("risques", [])),
                    len(context.get("cartographie", [])))
    except Exception as e:
        logger.warning("[Etape 1] Source principale echouee (%s), fallback Excel.", e)
        try:
            from loaders.load_excel_data import load_all_context
            context = load_all_context(data_dir)
            context["_load_mode"] = "excel_fallback"
        except Exception as e2:
            logger.error("[Etape 1] Excel fallback echoue : %s", e2)
            context = {"_load_mode": "vide"}

    # ------------------------------------------------------------------
    # Etape 2 : Fusionner avec les Excel de donnees/ non encore charges
    # (fichiers clients deposes dans donnees/ : NC, KPI, Reclamations...)
    # ------------------------------------------------------------------
    excel_extra = load_excel_donnees(data_dir)

    # Fusionner uniquement les donnees manquantes (pas deja dans context)
    for key in ["kpis", "nc", "reclamations", "cartographie"]:
        existing = context.get(key, [])
        extra    = excel_extra.get(key, [])
        if extra:
            before = len(existing)
            context[key] = _merge_lists(existing, extra)
            added = len(context[key]) - before
            if added > 0:
                logger.info("[Etape 2] %s : +%d enregistrements depuis Excel", key, added)

    if excel_extra.get("fiches"):
        context.setdefault("fiches", []).extend(excel_extra["fiches"])
        logger.info("[Etape 2] Fiches : %d ajoutees", len(excel_extra["fiches"]))

    # Reconstruire les index apres fusion
    try:
        from loaders.load_excel_data import (
            build_kpi_index, build_nc_summary,
            build_reclamation_summary, build_risque_index,
            build_cartographie_index,
        )
        context["kpi_index"]           = build_kpi_index(context.get("kpis", []))
        context["nc_summary"]          = build_nc_summary(context.get("nc", []))
        context["reclamation_summary"] = build_reclamation_summary(context.get("reclamations", []))
        context["risque_index"]        = build_risque_index(context.get("risques", []))
        context["carto_index"]         = build_cartographie_index(context.get("cartographie", []))
    except Exception as e:
        logger.warning("[Etape 2] Reconstruction index echouee : %s", e)

    # ------------------------------------------------------------------
    # Etape 3 : Charger les PDF de donnees/
    # ------------------------------------------------------------------
    pdf_pages = load_pdf_donnees(data_dir)
    if pdf_pages:
        context["dashboard_pages_inbox"] = pdf_pages
        logger.info("[Etape 3] PDF : %d pages chargees", len(pdf_pages))

    # ------------------------------------------------------------------
    # Recap final
    # ------------------------------------------------------------------
    logger.info(
        "[Unified] TOTAL : %d KPIs | %d NC | %d Reclamations | "
        "%d Cartographie | %d Risques | %d PDF pages",
        len(context.get("kpis", [])),
        len(context.get("nc", [])),
        len(context.get("reclamations", [])),
        len(context.get("cartographie", [])),
        len(context.get("risques", [])),
        len(pdf_pages),
    )

    context["_unified_loader"] = True
    return context
