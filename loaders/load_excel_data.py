import openpyxl
import os
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple
import re
import unicodedata


def _normalize_text(value: Any) -> str:
    """Normalise un texte pour comparaison lexicale robuste."""
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.category(c).startswith("M"))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


HEADER_ALIASES = {
    "Processus": [
        "processus", "process", "process name", "nom processus", "perimetre",
        "service", "departement", "direction",
    ],
    "Intitule": [
        "intitule", "intitule risque", "risque", "risques", "designation",
        "libelle", "description risque", "titre", "objet",
    ],
    "Type": [
        "type", "nature", "categorie", "type ro", "risque opportunite",
    ],
    "Causes": [
        "causes", "cause", "origine", "source cause", "cause racine",
    ],
    "Consequences": [
        "consequences", "consequence", "effets", "effet", "impact",
        "impacts", "dommage",
    ],
    "Valeur": [
        "valeur", "valeur reelle", "realise", "mesure", "resultat",
        "taux", "note",
    ],
    "Cible": [
        "cible", "objectif", "valeur cible", "target", "seuil objectif",
    ],
    "Min": [
        "min", "minimum", "seuil min", "borne min", "limite basse",
        "score min", "score min.",
    ],
    "Max": [
        "max", "maximum", "seuil max", "borne max", "limite haute",
        "score max", "score max.",
    ],
    "Niveau": [
        "niveau", "criticite", "critique", "gravite", "severite",
        "danger", "dangerosite", "priorite", "urgence", "classe",
        "classification",
    ],
    "Frequence": [
        "frequence", "frequence occurrence", "occurrence", "probabilite",
        "vraisemblance", "f", "p",
    ],
    "Gravite": [
        "gravite", "impact", "severite", "g",
    ],
    "RPN": [
        "rpn", "score risque", "cotation", "criticite score",
        "score initial",
    ],
    "RPN'": [
        "rpn residuel", "rpn residual", "score residuel",
        "criticite residuelle", "cotation residuelle",
    ],
}

HEADER_LOOKUP = {
    _normalize_text(alias): canonical
    for canonical, aliases in HEADER_ALIASES.items()
    for alias in aliases
}

NUMBER_WORDS = {
    "zero": 0, "nul": 0, "aucun": 0,
    "un": 1, "une": 1, "premier": 1, "faible": 1, "bas": 1,
    "deux": 2, "second": 2, "deuxieme": 2,
    "trois": 3, "troisieme": 3,
    "quatre": 4, "quatrieme": 4,
    "cinq": 5, "cinquieme": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
}

ROMAN_NUMERALS = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
}

LETTER_SCORES = {
    "a": 1, "b": 2, "c": 3, "d": 4, "e": 5,
}

LEVEL_KEYWORDS = {
    "critique": [
        "critique", "critical", "catastrophique", "catastrophe",
        "dangereux", "dangereuse", "dangeureux", "dangeureuse",
        "danger", "mortel", "grave majeur",
        "tres eleve", "tres elevee", "extreme", "urgent", "urgence",
        "inacceptable",
    ],
    "eleve": [
        "eleve", "elevee", "haut", "haute", "important", "importante",
        "majeur", "majeure", "significatif", "significative", "fort",
        "forte", "serieux", "serieuse",
    ],
    "moyen": [
        "moyen", "moyenne", "modere", "moderee", "intermediaire",
        "acceptable sous controle", "surveillance",
    ],
    "mineur": [
        "mineur", "mineure", "faible", "bas", "basse", "leger",
        "legere", "negligeable", "minimal", "minime",
    ],
}

LEVEL_BY_SCORE = {
    1: "mineur",
    2: "moyen",
    3: "eleve",
    4: "critique",
    5: "critique",
}

NUMERIC_CANONICAL_FIELDS = {
    "Valeur", "Cible", "Min", "Max", "Frequence", "Gravite", "RPN", "RPN'",
}

LEVEL_CANONICAL_FIELDS = {"Niveau"}


def parse_smart_number(value: Any) -> Optional[float]:
    """
    Convertit des valeurs Excel heterogenes en nombre :
    1, "1", "un", "I", "A", "niveau 3", "75%"...
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)

    raw = str(value).strip()
    if not raw:
        return None

    normalized = _normalize_text(raw)
    if not normalized:
        return None

    try:
        return float(raw.replace(",", ".").replace("%", "").strip())
    except ValueError:
        pass

    if normalized in NUMBER_WORDS:
        return float(NUMBER_WORDS[normalized])
    if normalized in ROMAN_NUMERALS:
        return float(ROMAN_NUMERALS[normalized])
    if normalized in LETTER_SCORES:
        return float(LETTER_SCORES[normalized])

    match = re.search(r"\b(\d+(?:[.,]\d+)?)\b", raw)
    if match:
        return float(match.group(1).replace(",", "."))

    return None


def normalize_level(value: Any) -> Optional[str]:
    """
    Ramene les niveaux textuels vers un vocabulaire unique :
    mineur | moyen | eleve | critique.
    Retourne None si la valeur est vide ou non reconnue.
    """
    if value is None:
        return None

    normalized = _normalize_text(value)
    if not normalized:
        return None

    for level, keywords in LEVEL_KEYWORDS.items():
        for keyword in keywords:
            if _normalize_text(keyword) in normalized:
                return level

    number = parse_smart_number(value)
    if number is not None:
        result = LEVEL_BY_SCORE.get(int(round(number)))
        return result if result else None

    return None


def _canonical_header(header: str) -> Optional[str]:
    normalized = _normalize_text(header)
    if normalized in HEADER_LOOKUP:
        return HEADER_LOOKUP[normalized]
    for alias, canonical in HEADER_LOOKUP.items():
        if alias and len(alias) >= 8 and alias in normalized:
            return canonical
    return None


def enrich_record_smart(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ajoute une lecture intelligente des champs Excel.

    - Synonymes d'en-tetes -> champs canoniques (Processus, Intitule, Niveau...)
    - Valeurs numeriques textuelles -> nombres utilisables
    - Niveaux lexicaux -> mineur/moyen/eleve/critique
    """
    enriched = dict(record)

    for header, value in list(record.items()):
        canonical = _canonical_header(header)
        if not canonical:
            continue

        smart_value = value
        if canonical in NUMERIC_CANONICAL_FIELDS:
            parsed = parse_smart_number(value)
            if parsed is not None:
                smart_value = int(parsed) if parsed.is_integer() else parsed
            else:
                normalized_level = normalize_level(value)
                if normalized_level:
                    smart_value = normalized_level
                    enriched[f"{header}_normalise"] = normalized_level
                    if "Niveau" not in enriched or enriched.get("Niveau") in (None, ""):
                        enriched["Niveau"] = normalized_level
        elif canonical in LEVEL_CANONICAL_FIELDS:
            normalized_level = normalize_level(value)
            if normalized_level:
                smart_value = normalized_level
                enriched[f"{header}_normalise"] = normalized_level

        current = enriched.get(canonical)
        if (
            canonical not in enriched
            or current in (None, "")
            or (isinstance(current, str) and smart_value != current)
        ):
            enriched[canonical] = smart_value

    return enriched


def read_sheet(filepath: str, sheet_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Lit une feuille Excel et retourne une liste de dictionnaires.
    La premiere ligne est utilisee comme en-tete.
    """
    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.active

    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if len(rows) < 2:
        return []

    headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows[0])]
    data = []
    for row in rows[1:]:
        record = {}
        for j, val in enumerate(row):
            if j < len(headers):
                record[headers[j]] = val
        if any(v is not None for v in record.values()):
            data.append(enrich_record_smart(record))
    return data


# =========================
# Chargement des KPIs
# =========================
def load_kpis(filepath: str) -> List[Dict[str, Any]]:
    """
    Charge les KPIs. Colonnes attendues :
    Processus, Intitule du KPI, Formule, Frequence, Cible, Min, Max, ...
    """
    return read_sheet(filepath)


def build_kpi_index(kpis: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Index des KPIs par processus pour recherche rapide.
    """
    index = {}
    for kpi in kpis:
        proc = str(kpi.get("Processus", "")).strip()
        if proc:
            index.setdefault(proc, []).append(kpi)
    return index


# =========================
# Chargement des NC
# =========================
def load_nc(filepath: str) -> List[Dict[str, Any]]:
    """
    Charge les non-conformites.
    """
    return read_sheet(filepath)


def build_nc_summary(nc_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Resume statistique des NC par processus et par statut.
    """
    by_process = {}
    by_status = {}
    total = len(nc_list)

    for nc in nc_list:
        proc = str(nc.get("Processus", nc.get("processus", "Non precise"))).strip()
        status = str(nc.get("Statut", nc.get("statut", "Non precise"))).strip()

        by_process[proc] = by_process.get(proc, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1

    return {
        "total": total,
        "by_process": by_process,
        "by_status": by_status
    }


# =========================
# Chargement des Reclamations
# =========================
def load_reclamations(filepath: str) -> List[Dict[str, Any]]:
    """
    Charge les reclamations clients.
    """
    return read_sheet(filepath)


def build_reclamation_summary(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Resume statistique des reclamations.
    """
    by_product = {}
    by_status = {}
    total = len(recs)

    for rec in recs:
        product = str(rec.get("Produit", rec.get("produit", "Non precise"))).strip()
        status = str(rec.get("Statut", rec.get("statut", "Non precise"))).strip()

        by_product[product] = by_product.get(product, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1

    return {
        "total": total,
        "by_product": by_product,
        "by_status": by_status
    }


# =========================
# Chargement des Risques existants
# =========================
def load_risques(filepath: str) -> List[Dict[str, Any]]:
    """
    Charge le registre des risques existants.
    """
    return read_sheet(filepath)


def build_risque_index(risques: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Index des risques par processus.
    """
    index = {}
    for r in risques:
        proc = str(r.get("Processus", r.get("processus", ""))).strip()
        if proc:
            index.setdefault(proc, []).append(r)
    return index


# =========================
# Chargement des Objectifs
# =========================
def load_objectifs(filepath: str) -> List[Dict[str, Any]]:
    """
    Charge les objectifs qualite.
    """
    return read_sheet(filepath)


# =========================
# Chargement de la Cartographie des risques
# =========================
def load_cartographie(filepath: str) -> List[Dict[str, Any]]:
    """
    Charge la cartographie des risques (avec RPN, cotation, decisions).
    """
    return read_sheet(filepath)


def build_cartographie_index(carto: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Index de la cartographie par processus.
    """
    index = {}
    for c in carto:
        proc = str(c.get("Processus", c.get("processus", ""))).strip()
        if proc:
            index.setdefault(proc, []).append(c)
    return index


# =========================
# Contexte global agrege
# =========================
def load_all_context(data_dir: str) -> Dict[str, Any]:
    """
    Charge toutes les donnees Excel et construit un contexte global
    utilisable par les agents.
    """
    import os

    context = {}

    files_config = {
        "kpis": ("KPI's.xlsx", load_kpis),
        "nc": ("NC.xlsx", load_nc),
        "reclamations": ("Réclamations clients.xlsx", load_reclamations),
        "risques": ("Risques.xlsx", load_risques),
        "cartographie": ("Cartographie des risques.xlsx", load_cartographie),
        "processus": ("Les Processus.xlsx", read_sheet),
        "objectifs": ("Objectifs.xlsx", load_objectifs),
        "fournisseurs": ("Fournisseurs.xlsx", read_sheet),
    }

    for key, (filename, loader) in files_config.items():
        path = os.path.join(data_dir, filename)
        if os.path.exists(path):
            try:
                context[key] = loader(path)
            except Exception as e:
                print(f"[WARN] Erreur chargement {filename}: {e}")
                context[key] = []
        else:
            print(f"[WARN] Fichier non trouve: {filename}")
            context[key] = []

    # Index precalcules
    context["kpi_index"] = build_kpi_index(context.get("kpis", []))
    context["risque_index"] = build_risque_index(context.get("risques", []))
    context["carto_index"] = build_cartographie_index(context.get("cartographie", []))
    context["nc_summary"] = build_nc_summary(context.get("nc", []))
    context["reclamation_summary"] = build_reclamation_summary(context.get("reclamations", []))

    # Chargement des fiches clients (Formats Fiche + Grille)
    try:
        fiches = load_fiches_risque_from_dir(data_dir)
        context["fiches"] = fiches
        context["fiches_payloads"] = fiches_to_qalitas_payloads(fiches)
    except Exception as e:
        print(f"[WARN] Erreur chargement fiches: {e}")
        context["fiches"] = []
        context["fiches_payloads"] = []

    return context


# =============================================================================
# CHARGEMENT FICHES D'ANALYSE (Formats clients : Fiche APSYS + Grille management)
# Integre directement dans load_excel_data pour centraliser tous les loaders Excel
# =============================================================================

# Alias interne : meme logique que _normalize_text
_norm = _normalize_text


def _clean(val: Any) -> str:
    """Nettoie une cellule Excel : None -> '', espaces multiples -> un seul."""
    if val is None or (isinstance(val, float) and val != val):
        return ""
    return re.sub(r"\s+", " ", str(val)).strip()


def _to_int(val: Any) -> Optional[int]:
    """Convertit une valeur heterogene en entier, None si impossible."""
    if val is None:
        return None
    try:
        return int(float(str(val).replace(",", ".")))
    except (ValueError, TypeError):
        return None


def _row_text(row_vals: list) -> str:
    return " ".join(_norm(v) for v in row_vals if v is not None and str(v).strip())


# ---------------------------------------------------------------------------
# Splitting multi-actions
# ---------------------------------------------------------------------------

def split_actions(text: Any) -> List[str]:
    """
    Decoupe une cellule contenant plusieurs actions en liste d'actions individuelles.

    Patterns reconnus (ordre de priorite) :
      1. Prefixe _  avec >=2 espaces : "   _ action1   _ action2"
      2. Prefixe *  avec >=2 espaces : "   * action1   * action2"
      3. Double slash : "action1 // action2"
      4. Tiret en debut de ligne
      5. Puces lettres a) b) c)
      6. Numerotation 1. 2.
      7. Double saut de ligne
      8. Saut de ligne simple (segments >= 10 chars)
    """
    if text is None or (isinstance(text, float) and text != text):
        return []
    raw = str(text).strip()
    if not raw:
        return []

    def _c(s: str) -> str:
        return re.sub(r"\s+", " ", s.replace("\n", " ")).strip()

    def _valid(parts: list, min_len: int = 5) -> List[str]:
        return [_c(p.lstrip("_*- \t")) for p in parts
                if len(_c(p.lstrip("_*- \t"))) >= min_len]

    for prefix in ["_", "*"]:
        if re.search(rf"(?:[ \t]{{2,}}|\n){re.escape(prefix)}[ \t]+\S", raw):
            parts = re.split(rf"(?:[ \t]{{2,}}|\n){re.escape(prefix)}[ \t]+", raw)
            results = _valid(parts)
            if len(results) > 1:
                return results

    if "//" in raw:
        results = _valid(raw.split("//"))
        if len(results) > 1:
            return results

    if re.search(r"(?:^|\n)[ \t]*-[ \t]+\w", raw):
        results = _valid(re.split(r"(?:^|\n)[ \t]*-[ \t]+", raw))
        if len(results) > 1:
            return results

    if re.search(r"(?:^|\n)\s*[a-d][)\.][ \t]+\w", raw, re.IGNORECASE):
        results = _valid(re.split(r"(?:^|\n)\s*[a-d][)\.][ \t]+", raw, flags=re.IGNORECASE))
        if len(results) > 1:
            return results

    if re.search(r"(?:^|\n)\s*\d+[)\.]\s+\w", raw):
        results = _valid(re.split(r"(?:^|\n)\s*\d+[)\.]\s+", raw))
        if len(results) > 1:
            return results

    if "\n\n" in raw:
        results = _valid(raw.split("\n\n"))
        if len(results) > 1:
            return results

    if "\n" in raw:
        parts = [p.strip() for p in raw.split("\n") if len(p.strip()) >= 10]
        if len(parts) >= 2:
            return parts

    return [_c(raw)]


# ---------------------------------------------------------------------------
# Detection du format
# ---------------------------------------------------------------------------

def _detect_fiche_format(xl: pd.ExcelFile) -> str:
    """Retourne 'grille' ou 'fiche' selon la structure du fichier."""
    sheets_norm = [_norm(s) for s in xl.sheet_names]
    if (any("management des risques" in s for s in sheets_norm) and
            any("management des opportunit" in s for s in sheets_norm)):
        return "grille"
    if any("fiche" in s or re.search(r"\d{4}", s) for s in sheets_norm):
        return "fiche"
    try:
        df = pd.read_excel(xl, sheet_name=xl.sheet_names[0], header=None, nrows=5)
        combined = " ".join(_row_text(df.iloc[i].tolist()) for i in range(min(5, len(df))))
        if "inducteur" in combined or "grille" in combined:
            return "grille"
    except Exception:
        pass
    return "fiche"


# ---------------------------------------------------------------------------
# Format 1 : Fiche d'analyse (FAB, MRH, MRM, LOG, Direction, Qualite)
# ---------------------------------------------------------------------------

_FICHE_COL_MATCHERS: List[Tuple[str, List[str]]] = [
    ("origine",            ["origine de l action", "origine action"]),
    ("intitule",           ["risques", "intitule risque", "description risque"]),
    ("causes",             ["causes probables", "cause probables", "5m", "probables"]),
    ("consequences",       ["impact de risque", "impact risque", "consequences"]),
    ("gravite",            ["gravite", "gravit"]),
    ("occurrence",         ["occurrence frequence", "occurrence probabilite",
                             "probabilite d apparition", "frequence probabilite"]),
    ("actions_existantes", ["actions existantes"]),
    ("detectabilite_det",  ["detectabilite", "detection"]),
    ("ipr",                ["ipr", "evaluation du risque", "evaluation risque", "cotation"]),
    ("action_designation", ["bref descriptif", "descriptif de l action",
                             "actions a mettre en oeuvre"]),
    ("responsable",        ["responsable"]),
    ("criteres_efficacite",["criteres d evaluation", "criteres evaluation",
                             "evaluation de l efficacite", "evaluation efficacite"]),
    ("efficacite_oui",     ["oui"]),
    ("efficacite_non",     ["non"]),
    ("date_cloture",       ["date de cloture", "date cloture"]),
]


def _fiche_detect_columns(df: pd.DataFrame) -> Dict[str, int]:
    ncols = len(df.columns)
    header_row = 3
    for i in range(min(8, len(df))):
        row_vals = [_norm(df.iloc[i, j]) for j in range(ncols)]
        has_risque = any("risque" in v for v in row_vals if v)
        has_gravite = any("gravit" in v for v in row_vals if v)
        has_occurrence = any("occurrence" in v or "probabilite" in v for v in row_vals if v)
        has_bref = any("bref descriptif" in v or "descriptif" in v for v in row_vals if v)
        if has_risque and (has_gravite or has_occurrence or has_bref):
            header_row = i
            break

    col_texts: Dict[int, List[str]] = {}
    for r in range(header_row, min(header_row + 3, len(df))):
        for j in range(ncols):
            txt = _norm(df.iloc[r, j])
            if txt:
                col_texts.setdefault(j, []).append(txt)

    mapping: Dict[str, int] = {}
    for canonical, keywords in _FICHE_COL_MATCHERS:
        best_col, best_score = None, 0
        for j, texts in col_texts.items():
            cell = " ".join(texts)
            for kw in keywords:
                kn = _norm(kw)
                if kn in cell:
                    score = len(kn)
                    if canonical == "efficacite_oui" and "non" in cell and "oui" not in cell:
                        continue
                    if score > best_score:
                        best_score = score
                        best_col = j
        if best_col is not None:
            mapping[canonical] = best_col

    if "intitule" not in mapping:
        mapping["intitule"] = mapping.get("origine", -1) + 1 if "origine" in mapping else 2
    if "causes" not in mapping:
        mapping["causes"] = mapping.get("intitule", 2) + 1
    if "consequences" not in mapping:
        mapping["consequences"] = mapping.get("causes", 3) + 1
    if "gravite" not in mapping:
        mapping["gravite"] = 5
    if "occurrence" not in mapping:
        mapping["occurrence"] = mapping["gravite"] + 1
    if "detectabilite_det" not in mapping:
        mapping["detectabilite_det"] = mapping["gravite"] + 2
    if "ipr" not in mapping:
        mapping["ipr"] = mapping["gravite"] + 3
    if "action_designation" not in mapping:
        mapping["action_designation"] = mapping["ipr"] + 1
    if "responsable" not in mapping:
        mapping["responsable"] = mapping["action_designation"] + 1

    mapping["_header_row"] = header_row
    return mapping


def _fiche_find_data_start(df: pd.DataFrame, header_row: int) -> int:
    for i in range(header_row + 1, min(header_row + 6, len(df))):
        row = df.iloc[i]
        row_text = " ".join(str(v) for v in row if pd.notna(v)).lower()
        if sum(1 for kw in ["interne", "externe", "detection", "occurrence "]
               if kw in row_text) >= 2:
            continue
        numeric_found = any(
            pd.notna(row.iloc[j]) and str(row.iloc[j]).strip().replace(".", "").isdigit()
            for j in range(min(5, len(row)), min(15, len(row)))
        )
        has_intitule = len(row) > 2 and pd.notna(row.iloc[2]) and len(str(row.iloc[2]).strip()) > 5
        if numeric_found or has_intitule:
            return i
    return header_row + 2


def _fiche_find_opp_section(df: pd.DataFrame, data_start: int) -> Optional[int]:
    for i in range(data_start, len(df)):
        row_text = " ".join(str(v) for v in df.iloc[i] if pd.notna(v)).lower()
        non_empty = sum(1 for v in df.iloc[i] if pd.notna(v) and str(v).strip())
        if "opportunit" in row_text and non_empty <= 4:
            return i
    return None


def _fiche_extract_metadata(df: pd.DataFrame, sheet_name: str) -> Dict[str, str]:
    meta = {"processus": "", "pilote": "", "annee": ""}
    for i in range(min(4, len(df))):
        row_text = " ".join(str(v) for v in df.iloc[i] if pd.notna(v))
        if not meta["processus"]:
            m = re.search(
                r"[Pp]rocessus\s*[:\-]\s*(.+?)(?:\s{2,}|\s*[Dd]ate\s|\s*[,\|]|$)",
                row_text
            )
            if m:
                proc = re.sub(r"\s+", " ", m.group(1)).strip()
                meta["processus"] = re.sub(r"\s*[Dd]ate.*$", "", proc).strip()[:80]
        if not meta["pilote"]:
            m = re.search(r"[Pp]ilote[^:\-]*[:\-]\s*(.+?)(?:\s{3,}|$)", row_text)
            if m:
                meta["pilote"] = re.sub(r"\s+", " ", m.group(1)).strip()[:60]
        if not meta["annee"]:
            m = re.search(r"(\d{4})", sheet_name)
            if m:
                meta["annee"] = m.group(1)
            elif not meta["annee"]:
                m = re.search(r"(\d{4})", row_text)
                if m:
                    meta["annee"] = m.group(1)
    return meta


def _fiche_parse_rows(
    df: pd.DataFrame,
    col_map: Dict[str, int],
    start_row: int,
    end_row: int,
    record_type: str,
    meta: Dict[str, str],
    source_fichier: str,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    action_col = col_map.get("action_designation")
    current_origine = ""

    def _get(row: pd.Series, field: str) -> str:
        col = col_map.get(field)
        if col is None or col >= len(row):
            return ""
        return _clean(row.iloc[col])

    def _get_int(row: pd.Series, field: str) -> Optional[int]:
        return _to_int(_get(row, field))

    for i in range(start_row, end_row):
        row = df.iloc[i]
        if sum(1 for v in row if pd.notna(v) and str(v).strip()) < 2:
            continue

        intitule = _get(row, "intitule")
        if not intitule:
            for fb in [2, 1, 3]:
                if fb < len(row):
                    v = _clean(row.iloc[fb])
                    if v and len(v) > 5:
                        intitule = v
                        break
        if not intitule:
            continue

        origine_raw = _get(row, "origine")
        if origine_raw:
            current_origine = origine_raw

        action_raw = row.iloc[action_col] if action_col is not None and action_col < len(row) else None
        action_text = str(action_raw).strip() if action_raw is not None and pd.notna(action_raw) else ""
        actions_list = split_actions(action_text) if action_text else []

        responsable_raw = _get(row, "responsable")
        criteres_raw = _get(row, "criteres_efficacite")
        responsables = [r.strip() for r in re.split(r"[/&;]+", responsable_raw) if r.strip()]

        actions = []
        for idx, act_text in enumerate(actions_list):
            resp = (responsables[idx] if idx < len(responsables)
                    else (responsables[0] if responsables else responsable_raw))
            actions.append({"designation": act_text, "responsable": resp,
                            "criteres_efficacite": criteres_raw})

        oui_text = _get(row, "efficacite_oui").lower()
        non_text = _get(row, "efficacite_non").lower()
        ipr_val = _get_int(row, "ipr")
        gravite_val = _get_int(row, "gravite")
        occurrence_val = _get_int(row, "occurrence")
        detectabilite_val = _get_int(row, "detectabilite_det")

        if ipr_val is None and gravite_val and occurrence_val:
            ipr_val = (gravite_val * occurrence_val * detectabilite_val
                       if detectabilite_val else gravite_val * occurrence_val)

        records.append({
            "processus":          meta.get("processus", ""),
            "pilote":             meta.get("pilote", ""),
            "annee":              int(meta["annee"]) if meta.get("annee", "").isdigit() else None,
            "source_fichier":     source_fichier,
            "format":             "fiche",
            "type":               record_type,
            "origine":            current_origine,
            "intitule":           intitule,
            "causes":             _get(row, "causes"),
            "consequences":       _get(row, "consequences"),
            "gravite":            gravite_val,
            "occurrence":         occurrence_val,
            "detectabilite":      detectabilite_val,
            "ipr":                ipr_val,
            "actions_existantes": _get(row, "actions_existantes"),
            "actions":            actions,
            "nb_actions":         len(actions),
            "efficacite_oui":     any(c in oui_text for c in ["x", "oui", "yes", "v"]),
            "efficacite_non":     any(c in non_text for c in ["x", "non", "no"]),
            "date_cloture":       _get(row, "date_cloture") or None,
        })
    return records


def _parse_fiche_sheet(df: pd.DataFrame, source_fichier: str, sheet_name: str) -> List[Dict[str, Any]]:
    meta = _fiche_extract_metadata(df, sheet_name)
    col_map = _fiche_detect_columns(df)
    header_row = col_map.pop("_header_row", 3)
    data_start = _fiche_find_data_start(df, header_row)
    opp_row = _fiche_find_opp_section(df, data_start)
    risk_end = opp_row if opp_row is not None else len(df)

    records = _fiche_parse_rows(df, col_map, data_start, risk_end, "risque", meta, source_fichier)
    if opp_row is not None:
        records += _fiche_parse_rows(df, col_map, opp_row + 2, len(df), "opportunite", meta, source_fichier)
    return records


# ---------------------------------------------------------------------------
# Format 2 : Grille management (risques et opportunites separees)
# ---------------------------------------------------------------------------

def _grille_extract_header_map(df: pd.DataFrame) -> Tuple[int, Dict[str, int]]:
    ncols = len(df.columns)
    header_row = 1
    for i in range(min(5, len(df))):
        row_vals = [_norm(df.iloc[i, j]) for j in range(ncols)]
        combined = " ".join(row_vals)
        if (("source" in combined or "inducteur" in combined) and
                ("gravit" in combined or "vraisemblance" in combined or
                 " gi" in combined or "gi " in combined)):
            header_row = i
            break

    col_texts: Dict[int, str] = {}
    for r in range(header_row, min(header_row + 3, len(df))):
        for j in range(ncols):
            txt = _norm(df.iloc[r, j])
            if txt:
                col_texts[j] = col_texts.get(j, "") + " " + txt

    def _find(keywords: List[str]) -> Optional[int]:
        best_col, best_score = None, 0
        for j, combined in col_texts.items():
            for kw in keywords:
                kn = _norm(kw)
                if kn in combined and len(kn) > best_score:
                    best_score = len(kn)
                    best_col = j
        return best_col

    gi_col = _find(["gi", "gravite initiale", "gravite"]) or 7
    mapping: Dict[str, int] = {
        "source":             _find(["source du risque", "source de l opportunite", "source"]) or 1,
        "qse":                _find(["q s e", "qse", "q/s/e"]) or 2,
        "inducteur":          _find(["inducteur du risque", "inducteur de l opportunite",
                                      "inducteur", "aspects"]) or 3,
        "intitule":           _find(["description du risque", "description de l opportunite",
                                      "description"]) or 4,
        "causes":             _find(["causes possibles", "cause de l opportunite", "causes"]) or 5,
        "consequences":       _find(["consequences", "consequence de l opportunite"]) or 6,
        "gravite":            gi_col,
        "occurrence":         _find(["vi", "vraisemblance initiale", "vraisemblance"]) or (gi_col + 1),
        "ipr":                _find(["ci", "criticite initiale", "criticite"]) or (gi_col + 2),
        "mesures_existantes": _find(["mesures existantes", "mesure existante"]) or (gi_col + 3),
        "actions":            _find(["actions a envisager", "actions a renforcer",
                                      "actions a mettre"]) or (gi_col + 8),
        "responsable":        _find(["responsable"]) or (gi_col + 9),
        "delai":              _find(["delai"]) or (gi_col + 10),
        "etat":               _find(["etat"]) or (gi_col + 11),
        "criteres":           _find(["critere d efficacite", "criteres d efficacite"]) or (gi_col + 12),
    }
    return header_row, mapping


def _parse_grille_sheet(
    df: pd.DataFrame,
    record_type: str,
    source_fichier: str,
    annee: Optional[int],
) -> List[Dict[str, Any]]:
    header_row, col_map = _grille_extract_header_map(df)
    data_start = header_row + 1
    for i in range(data_start, min(data_start + 3, len(df))):
        row = df.iloc[i]
        row_text = " ".join(str(v) for v in row if pd.notna(v)).lower()
        is_subheader = (
            sum(1 for v in [str(v).strip() for v in row if pd.notna(v) and str(v).strip()]
                if v in ["G", "A", "B", "I", "V", "C", "D"]) >= 2
            or "risque final" in row_text
        )
        if is_subheader:
            data_start = i + 1
        else:
            break

    records: List[Dict[str, Any]] = []

    def _get(row: pd.Series, field: str) -> str:
        col = col_map.get(field)
        if col is None or col >= len(row):
            return ""
        return _clean(row.iloc[col])

    for i in range(data_start, len(df)):
        row = df.iloc[i]
        if sum(1 for v in row if pd.notna(v) and str(v).strip()) < 3:
            continue
        intitule = _get(row, "intitule") or _get(row, "inducteur")
        if not intitule or len(intitule) < 3:
            continue

        action_col = col_map.get("actions")
        action_raw = row.iloc[action_col] if action_col is not None and action_col < len(row) else None
        action_text = str(action_raw).strip() if action_raw is not None and pd.notna(action_raw) else ""
        actions_list = split_actions(action_text) if action_text else []

        responsable_raw = _get(row, "responsable")
        criteres_raw = _get(row, "criteres")
        responsables = [r.strip() for r in re.split(r"[/&;,]+", responsable_raw) if r.strip()]

        actions = []
        for idx, act_text in enumerate(actions_list):
            resp = (responsables[idx] if idx < len(responsables)
                    else (responsables[0] if responsables else responsable_raw))
            actions.append({"designation": act_text, "responsable": resp,
                            "criteres_efficacite": criteres_raw})

        gravite_val = _to_int(_get(row, "gravite"))
        occurrence_val = _to_int(_get(row, "occurrence"))
        ipr_val = _to_int(_get(row, "ipr"))
        if ipr_val is None and gravite_val and occurrence_val:
            ipr_val = gravite_val * occurrence_val

        etat = _get(row, "etat").lower()
        records.append({
            "processus":          _infer_processus_grille(
                                      _get(row, "qse"), _get(row, "inducteur"),
                                      _get(row, "source")),
            "pilote":             "",
            "annee":              annee,
            "source_fichier":     source_fichier,
            "format":             "grille",
            "type":               record_type,
            "origine":            _get(row, "source"),
            "intitule":           intitule,
            "causes":             _get(row, "causes"),
            "consequences":       _get(row, "consequences"),
            "gravite":            gravite_val,
            "occurrence":         occurrence_val,
            "detectabilite":      None,
            "ipr":                ipr_val,
            "actions_existantes": _get(row, "mesures_existantes"),
            "actions":            actions,
            "nb_actions":         len(actions),
            "efficacite_oui":     any(c in etat for c in ["clos", "clotur", "efficace", "oui"]),
            "efficacite_non":     any(c in etat for c in ["non", "en cours", "reporte"]),
            "date_cloture":       _clean(_get(row, "delai")) or None,
        })
    return records


# ---------------------------------------------------------------------------
# Mapping processus et QALITAS
# ---------------------------------------------------------------------------

_PROCESSUS_MAP: Dict[str, str] = {
    "fabrication": "201", "controle": "201", "conditionnement": "201",
    "production": "201", "fab": "201", "injection": "201",
    "traitement des commandes": "301", "logistique": "301", "log": "301",
    "commandes": "301", "expeditions": "301",
    "ressources materielles": "404", "mrm": "404", "maintenance": "404", "achat": "404",
    "management des ressources humaines": "405", "ressources humaines": "405",
    "mrh": "405", "rh": "405", "formation": "405", "recrutement": "405",
    "direction": "101", "strategique": "101", "management strategique": "101",
    "pilotage qualite": "501", "surveillance mesure": "501",
    "qualite": "501", "smq": "501", "audit": "501",
}


def _infer_process_id(processus: str) -> str:
    p = _norm(processus)
    best_match, best_score = "201", 0
    for kw, pid in _PROCESSUS_MAP.items():
        kn = _norm(kw)
        if kn in p and len(kn) > best_score:
            best_score = len(kn)
            best_match = pid
    return best_match


def _infer_processus_grille(qse: str, inducteur: str, source_swot: str) -> str:
    ind_norm = _norm(inducteur)
    for kw in _PROCESSUS_MAP:
        if _norm(kw) in ind_norm:
            return kw.title()
    qse_norm = _norm(qse).replace(" ", "")
    if "s" in qse_norm:
        return "Securite"
    if "e" in qse_norm:
        return "Environnement"
    if "force" in _norm(source_swot):
        return "Direction"
    return "Qualite"


def _ipr_to_gravity(ipr: Optional[int], gravite: Optional[int]) -> str:
    if gravite is not None and gravite > 0:
        g = min(4, max(1, gravite))
    elif ipr is not None:
        g = 4 if ipr >= 30 else 3 if ipr >= 18 else 2 if ipr >= 8 else 1
    else:
        g = 2
    return f"G00{g}"


def _ipr_to_priority(ipr: Optional[int]) -> str:
    if ipr is None:
        return "P002"
    if ipr >= 30:
        return "P001"
    if ipr >= 18:
        return "P002"
    if ipr >= 10:
        return "P003"
    return "P004"


# ---------------------------------------------------------------------------
# API publique : chargement des fiches
# ---------------------------------------------------------------------------

def load_fiche_risque(
    filepath: str,
    annee_filtre: Optional[int] = None,
    toutes_annees: bool = False,
) -> List[Dict[str, Any]]:
    """Charge un fichier Fiche d'analyse ou Grille management."""
    source = os.path.basename(filepath)
    xl = pd.ExcelFile(filepath)
    fmt = _detect_fiche_format(xl)
    all_records: List[Dict[str, Any]] = []

    if fmt == "grille":
        for sname in xl.sheet_names:
            sn = _norm(sname)
            rtype = None
            if "management des risques" in sn or "management risques" in sn:
                rtype = "risque"
            elif "management des opportunit" in sn or "management opportunit" in sn:
                rtype = "opportunite"
            if rtype:
                try:
                    df = pd.read_excel(filepath, sheet_name=sname, header=None)
                    recs = _parse_grille_sheet(df, rtype, source, annee_filtre)
                    for r in recs:
                        r["feuille"] = sname
                    all_records.extend(recs)
                except Exception as e:
                    print(f"[WARN] {source} / '{sname}': {e}")
    else:
        sheets = xl.sheet_names
        if annee_filtre:
            target = [s for s in sheets if str(annee_filtre) in s]
            sheets = target if target else [sheets[-1]]
        elif not toutes_annees:
            sheets = [sheets[-1]]
        for sheet_name in sheets:
            try:
                df = pd.read_excel(filepath, sheet_name=sheet_name, header=None)
                recs = _parse_fiche_sheet(df, source, sheet_name)
                for r in recs:
                    r["feuille"] = sheet_name
                all_records.extend(recs)
            except Exception as e:
                print(f"[WARN] {source} / '{sheet_name}': {e}")

    return all_records


def load_fiches_risque_from_dir(
    directory: str,
    annee_filtre: Optional[int] = None,
    toutes_annees: bool = False,
) -> List[Dict[str, Any]]:
    """
    Charge tous les fichiers Excel de risques/fiches/grille du repertoire.
    Detecte automatiquement le format de chaque fichier.
    """
    all_records: List[Dict[str, Any]] = []
    patterns = ["fiche", "risque", "opportunit", "grille", "management"]

    for fname in sorted(os.listdir(directory)):
        if not fname.lower().endswith((".xlsx", ".xls")):
            continue
        if not any(p in _norm(fname) for p in patterns):
            continue
        fpath = os.path.join(directory, fname)
        try:
            recs = load_fiche_risque(fpath, annee_filtre=annee_filtre,
                                     toutes_annees=toutes_annees)
            all_records.extend(recs)
            print(f"  [OK] {fname}: {len(recs)} entrees")
        except Exception as e:
            print(f"  [WARN] {fname}: {e}")

    return all_records


def to_qalitas_action_payload(record: Dict[str, Any], action: Dict[str, str]) -> Dict[str, Any]:
    """Convertit un enregistrement fiche + une action en payload POST /Actions/Create."""
    processus_id = _infer_process_id(record.get("processus", ""))
    gravity_code = _ipr_to_gravity(record.get("ipr"), record.get("gravite"))
    priority_code = _ipr_to_priority(record.get("ipr"))
    fmt = record.get("format", "fiche")

    desc_parts = []
    if record.get("causes"):
        desc_parts.append(f"Causes: {record['causes']}")
    if record.get("consequences"):
        desc_parts.append(f"Consequences: {record['consequences']}")
    if record.get("actions_existantes"):
        desc_parts.append(f"Mesures existantes: {record['actions_existantes']}")

    proc_norm = _norm(record.get("processus", ""))
    q_val, s_val, e_val = "true", "false", "false"
    if fmt == "grille":
        if "securite" in proc_norm:
            q_val, s_val = "false", "true"
        elif "environnement" in proc_norm:
            q_val, e_val = "false", "true"

    return {
        "_source_fichier":   record.get("source_fichier", ""),
        "_feuille":          record.get("feuille", ""),
        "_format":           fmt,
        "_intitule_risque":  record.get("intitule", "")[:100],
        "_type":             record.get("type", "risque"),
        "_processus_nom":    record.get("processus", ""),
        "_ipr":              record.get("ipr"),
        "_nb_actions_total": record.get("nb_actions", 1),
        "_annee":            record.get("annee"),
        "Designation":       action.get("designation", "")[:200],
        "Description":       " | ".join(desc_parts),
        "RootCause":         record.get("causes", "")[:200],
        "ProcessId":         processus_id,
        "GravityCode":       gravity_code,
        "PriorityCode":      priority_code,
        "Source":            "11",
        "State":             "0",
        "Progression":       "0",
        "IsEffective":       "false",
        "IsConfidential":    "false",
        "WithAnalysis":      "false",
        "WithEfficiency":    "true",
        "WithEscalation":    "false",
        "Q":                 q_val,
        "S":                 s_val,
        "E":                 e_val,
        "H":                 "false",
        "TypesId":           "1",
        "CategoryId":        "1",
        "_responsable":      action.get("responsable", ""),
        "_criteres":         action.get("criteres_efficacite", ""),
    }


def fiches_to_qalitas_payloads(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convertit une liste de R&O en payloads QALITAS (un par action individuelle)."""
    return [
        to_qalitas_action_payload(rec, action)
        for rec in records
        for action in rec.get("actions", [])
        if action.get("designation")
    ]


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python load_excel_data.py <dossier_donnees>")
        sys.exit(1)

    ctx = load_all_context(sys.argv[1])
    print(f"Fiches       : {len(ctx['fiches'])} R&O")
    print(f"Payloads     : {len(ctx['fiches_payloads'])} actions QALITAS")
    print(f"KPIs         : {len(ctx['kpis'])}")
    print(f"NC           : {len(ctx['nc'])}")
    print(f"Reclamations : {len(ctx['reclamations'])}")
    print(f"Risques      : {len(ctx['risques'])}")