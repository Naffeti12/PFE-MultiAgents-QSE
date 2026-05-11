import pdfplumber
from typing import List, Dict, Any


def flatten_table(table: List[List[str]]) -> str:
    """
    Convertit une table extraite par pdfplumber en texte lisible.
    Chaque ligne est jointe par ' | ' et les lignes par des retours.
    """
    lines = []
    for row in table:
        cleaned = [str(cell).strip() if cell else "" for cell in row]
        if any(cleaned):
            lines.append(" | ".join(cleaned))
    return "\n".join(lines)


def load_dashboard_text(pdf_path: str) -> List[Dict[str, Any]]:
    """
    Extrait le texte ET les tableaux de chaque page du dashboard PDF.
    Retourne une liste de dictionnaires :
    {
        "page": int,
        "text": str,           # texte brut de la page
        "tables_text": str,    # texte issu des tableaux
        "combined_text": str,  # texte + tableaux combines
        "has_tables": bool,
        "table_count": int
    }
    """
    pages_data = []

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""

            tables = page.extract_tables() or []
            tables_text_parts = []
            for table in tables:
                tables_text_parts.append(flatten_table(table))

            tables_text = "\n".join(tables_text_parts)

            combined = text
            if tables_text:
                combined = text + "\n" + tables_text

            pages_data.append({
                "page": i,
                "text": text,
                "tables_text": tables_text,
                "combined_text": combined,
                "has_tables": len(tables) > 0,
                "table_count": len(tables)
            })

    return pages_data


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python load_dashboard_pdf.py <chemin_pdf>")
        sys.exit(1)

    pages = load_dashboard_text(sys.argv[1])
    for p in pages[:3]:
        print(f"--- Page {p['page']} (tables: {p['table_count']}) ---")
        print(p["combined_text"][:400])
        print()
