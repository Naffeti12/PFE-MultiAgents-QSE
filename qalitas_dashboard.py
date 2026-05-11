"""
QALITAS QSE - Generateur de Dashboard HTML Consolide (Agents 2 + 4).

Sections :
  1. KPI strip global (Agent 2 + Agent 4 combines)
  2. Vue croisee par processus (convergence / divergence)
  3. Top alertes Agent 4 (signaux terrain critiques)
  4. Top risques Agent 2 (evaluation formelle prioritaires)
  5. Cartographie residuelle dynamique
  6. Registre risques Agent 2 (tableau complet)
  7. Opportunites consolidees (Agent 2 + Agent 4)
  8. Footer (sources, conformite CdC, normes ISO)
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List


# =============================================================================
# PALETTE ET UTILITAIRES
# =============================================================================

COLORS = {
    "rouge":   "#dc2626",
    "orange":  "#ea580c",
    "jaune":   "#d97706",
    "vert":    "#16a34a",
    "bleu":    "#0284c7",
    "violet":  "#7c3aed",
    "teal":    "#0d9488",
    "critique":"#dc2626",
    "eleve":   "#ea580c",
    "moyen":   "#d97706",
    "mineur":  "#16a34a",
    "ALERTE":  "#dc2626",
    "SURVEILLANCE": "#d97706",
    "STABLE":  "#16a34a",
    "bg":      "#f8fafc",
    "card":    "#ffffff",
    "border":  "#e2e8f0",
    "text":    "#1e293b",
    "sub":     "#64748b",
    "header":  "#0f2744",
}

VERDICT_COLORS = {
    "RISQUE CONFIRME":          "#dc2626",
    "RISQUE SOUS-ESTIME":       "#dc2626",
    "RISQUE FORMEL SANS SIGNAL": "#ea580c",
    "SURVEILLANCE RENFORCEE":   "#d97706",
    "SURVEILLE":                "#6b7280",
    "STABLE":                   "#16a34a",
}


def esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def fmt(val: Any) -> str:
    try:
        return str(round(float(val), 1))
    except (TypeError, ValueError):
        return str(val) if val else "N/A"


def badge(label: str, color: str, small: bool = False) -> str:
    fs = "10px" if small else "11px"
    return (
        f'<span style="background:{color};color:#fff;padding:2px 8px;'
        f'border-radius:4px;font-size:{fs};font-weight:bold;">'
        f'{esc(label)}</span>'
    )


def niveau_color(niveau: str) -> str:
    return COLORS.get(niveau, "#6b7280")


# =============================================================================
# HEAD + STYLES
# =============================================================================

def render_head(generated_at: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>QALITAS QSE - Dashboard Consolide | Agents 2 &amp; 4</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:{COLORS['bg']};color:{COLORS['text']};font-size:14px;line-height:1.5;}}
.header{{background:linear-gradient(135deg,#0f2744 0%,#1a3f6e 100%);color:#fff;padding:28px 40px;}}
.header h1{{font-size:22px;font-weight:700;margin-bottom:4px;}}
.header .sub{{font-size:12px;opacity:.7;}}
.nav{{background:#1e293b;display:flex;gap:0;overflow-x:auto;}}
.nav a{{color:#94a3b8;text-decoration:none;padding:10px 20px;font-size:13px;border-bottom:3px solid transparent;white-space:nowrap;}}
.nav a:hover{{color:#fff;border-bottom-color:#3b82f6;}}
.container{{max-width:1500px;margin:0 auto;padding:24px 20px;}}
.section{{background:{COLORS['card']};border:1px solid {COLORS['border']};border-radius:10px;padding:24px;margin-bottom:28px;}}
.s-title{{font-size:16px;font-weight:700;margin-bottom:18px;padding-bottom:10px;border-bottom:2px solid {COLORS['border']};display:flex;align-items:center;gap:10px;}}
.agent-tag{{font-size:10px;padding:2px 8px;border-radius:3px;font-weight:600;}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;}}
.kpi-card{{background:{COLORS['bg']};border:1px solid {COLORS['border']};border-radius:8px;padding:16px;text-align:center;}}
.kpi-val{{font-size:30px;font-weight:800;line-height:1;margin-bottom:4px;}}
.kpi-lbl{{font-size:10px;color:{COLORS['sub']};text-transform:uppercase;letter-spacing:.05em;}}
.kpi-src{{font-size:9px;color:{COLORS['sub']};margin-top:2px;}}
.two-col{{display:grid;grid-template-columns:1fr 1fr;gap:24px;}}
.three-col{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;}}
@media(max-width:900px){{.two-col,.three-col{{grid-template-columns:1fr;}}}}
.alert-card{{border-left:5px solid;border-radius:8px;padding:16px;margin-bottom:14px;background:{COLORS['card']};box-shadow:0 1px 4px rgba(0,0,0,.06);}}
.alert-card .a-title{{font-size:14px;font-weight:600;margin-bottom:5px;}}
.alert-card .a-meta{{font-size:12px;color:{COLORS['sub']};margin-bottom:8px;}}
.a-block{{background:#f8fafc;border-radius:5px;padding:8px 12px;margin-top:8px;font-size:13px;}}
.a-lbl{{font-weight:600;font-size:10px;text-transform:uppercase;color:{COLORS['sub']};margin-bottom:3px;}}
table{{width:100%;border-collapse:collapse;font-size:12px;}}
th{{background:#1e3a5f;color:#fff;padding:9px 10px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;white-space:nowrap;}}
td{{padding:8px 10px;border-bottom:1px solid {COLORS['border']};vertical-align:top;}}
tr:hover td{{background:#f1f5f9;}}
.cross-row td{{font-size:12px;}}
.opp-card{{background:#f0fdf4;border:1px solid #86efac;border-radius:8px;padding:14px;margin-bottom:10px;}}
.opp-title{{font-weight:600;font-size:13px;color:#15803d;margin-bottom:5px;}}
.bar-wrap{{height:6px;background:#e2e8f0;border-radius:3px;margin-top:4px;}}
.bar-fill{{height:100%;border-radius:3px;}}
.footer{{background:#1e293b;color:#94a3b8;padding:28px 40px;font-size:12px;margin-top:40px;}}
.footer h3{{color:#fff;margin-bottom:10px;font-size:14px;}}
.check-ok{{color:#4ade80;}} .check-no{{color:#f87171;}}
</style>
</head>
<body>
<div class="header">
  <h1>QALITAS QSE &mdash; Dashboard Consolide</h1>
  <div class="sub">
    Agent 2 : Analyse &amp; Evaluation Formelle &nbsp;|&nbsp;
    Agent 4 : Monitoring &amp; Detection Dynamique &nbsp;|&nbsp;
    Genere le {esc(generated_at)}
  </div>
</div>
<nav class="nav">
  <a href="#kpi">Vue d'ensemble</a>
  <a href="#cross">Croisement processus</a>
  <a href="#a4top">Alertes Agent 4</a>
  <a href="#a2top">Risques Agent 2</a>
  <a href="#carto">Cartographie</a>
  <a href="#register">Registre</a>
  <a href="#opps">Opportunites</a>
</nav>
<div class="container">
"""


# =============================================================================
# KPI STRIP GLOBAL
# =============================================================================

def render_kpi_strip(data: dict) -> str:
    a2 = data.get("agent2", {})
    a4 = data.get("agent4", {})
    s2 = a2.get("summary_regle", {})
    s4 = a4.get("summary", {})
    cross = data.get("cross_process_view", [])

    dist2 = s2.get("by_niveau_residuel", {})
    dist4 = s4.get("by_criticality", {})

    confirmed = sum(1 for c in cross if c.get("verdict") == "RISQUE CONFIRME")
    underest = sum(1 for c in cross if c.get("verdict") == "RISQUE SOUS-ESTIME")

    kpis = [
        (s2.get("total_risques", 0),    "Risques formels",    "#0f2744", "Agent 2"),
        (s4.get("total_alerts", 0),     "Signaux detectes",   "#1e3a5f", "Agent 4"),
        (dist2.get("critique", 0),      "Critique formel",    COLORS["critique"], "A2"),
        (dist2.get("eleve", 0),         "Eleve formel",       COLORS["eleve"], "A2"),
        (dist4.get("critique", 0),      "Critique signal",    COLORS["critique"], "A4"),
        (dist4.get("eleve", 0),         "Eleve signal",       COLORS["eleve"], "A4"),
        (confirmed,                     "Risques confirmes",  "#7c3aed", "Croise"),
        (underest,                      "Sous-estimes",       COLORS["rouge"], "Croise"),
        (len(a2.get("opportunites", [])) + s4.get("total_opportunites", 0),
         "Opportunites",               COLORS["teal"], "Total"),
    ]

    cards = ""
    for val, label, color, src in kpis:
        cards += f"""
    <div class="kpi-card">
      <div class="kpi-val" style="color:{color};">{esc(str(val))}</div>
      <div class="kpi-lbl">{esc(label)}</div>
      <div class="kpi-src">{esc(src)}</div>
    </div>"""

    return f"""
<div class="section" id="kpi">
  <div class="s-title">Vue d'ensemble consolide
    <span class="agent-tag" style="background:#0f2744;color:#fff;">Agent 2 + 4</span>
  </div>
  <div class="kpi-grid">{cards}
  </div>
</div>
"""


# =============================================================================
# VUE CROISEE PROCESSUS
# =============================================================================

def render_cross_view(cross: list) -> str:
    if not cross:
        return ""

    rows = ""
    for c in cross:
        verdict = c.get("verdict", "?")
        vcolor = VERDICT_COLORS.get(verdict, "#6b7280")
        n2 = c.get("niveau_dominant_agent2", "N/A")
        n2c = niveau_color(n2)
        nb_sig = c.get("nb_signaux_agent4", 0)
        sig_color = COLORS["rouge"] if nb_sig >= 3 else (
            COLORS["jaune"] if nb_sig >= 1 else COLORS["vert"]
        )
        codes = ", ".join(c.get("codes_risques", [])[:3])

        rows += f"""
    <tr class="cross-row">
      <td>{esc(c.get('processus', ''))}</td>
      <td style="text-align:center;">{c.get('nb_risques_formels', 0)}</td>
      <td style="text-align:center;font-weight:700;color:{n2c};">{fmt(c.get('score_residuel_moyen'))}</td>
      <td style="text-align:center;">{badge(n2, n2c, True)}</td>
      <td style="text-align:center;font-weight:700;color:{sig_color};">{nb_sig}</td>
      <td>{badge(verdict, vcolor, True)}</td>
      <td style="color:{COLORS['sub']};font-size:11px;">{esc(codes)}</td>
    </tr>"""

    return f"""
<div class="section" id="cross">
  <div class="s-title">Vue Croisee par Processus — Agent 2 vs Agent 4
    <span class="agent-tag" style="background:#7c3aed;color:#fff;">Croisement</span>
  </div>
  <div style="overflow-x:auto;">
    <table>
      <thead>
        <tr>
          <th>Processus</th>
          <th>Risques formels</th>
          <th>Score res. moy.</th>
          <th>Niveau A2</th>
          <th>Signaux A4</th>
          <th>Verdict</th>
          <th>Codes risques</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>
"""


# =============================================================================
# TOP ALERTES AGENT 4
# =============================================================================

def render_agent4_top(a4_data: dict) -> str:
    all_alerts = a4_data.get("all_alerts", [])
    if not all_alerts:
        return ""

    top5 = sorted(
        all_alerts,
        key=lambda a: a.get("criticality_score", 0) or 0,
        reverse=True,
    )[:5]

    cards = ""
    for a in top5:
        level = a.get("criticality_level", "faible")
        color = COLORS.get(level, "#6b7280")
        resume = esc(a.get("resume_final", a.get("snippet", ""))[:220])
        impact = esc(a.get("impact_futur_final", a.get("impact_futur", ""))[:200])
        reco = esc(a.get("recommandation_final", a.get("recommandation", ""))[:200])
        llm_tag = (
            badge("LLM", COLORS["bleu"], True) if a.get("llm_used") else ""
        )

        cards += f"""
    <div class="alert-card" style="border-left-color:{color};">
      <div class="a-title">
        {badge(level.upper(), color)}
        &nbsp;{esc(a.get('process_name', ''))} &mdash; {esc(a.get('type_risque_final', a.get('type_risque_regle', '')))}
        &nbsp;{llm_tag}
      </div>
      <div class="a-meta">
        Source : <strong>{esc(a.get('evidence_source', a.get('source', 'N/A')))}</strong>
        &nbsp;&bull;&nbsp; Score : <strong>{fmt(a.get('criticality_score'))}</strong>
        &nbsp;&bull;&nbsp; Gravite : <strong>{esc(a.get('gravite_final', a.get('gravite_regle', 'N/A')))}</strong>
      </div>
      {f'<div class="a-block"><div class="a-lbl">Resume</div>{resume}</div>' if resume else ''}
      {f'<div class="a-block"><div class="a-lbl">Impact prospectif</div>{impact}</div>' if impact else ''}
      {f'<div class="a-block"><div class="a-lbl">Recommandation</div>{reco}</div>' if reco else ''}
    </div>"""

    s4 = a4_data.get("summary", {})
    return f"""
<div class="section" id="a4top">
  <div class="s-title">Top Alertes &mdash; Agent 4 Monitoring
    <span class="agent-tag" style="background:#1e3a5f;color:#fff;">Agent 4</span>
    &nbsp;<small style="font-weight:400;font-size:12px;">
      {s4.get('total_alerts',0)} signaux | LLM: {s4.get('llm_enrichment_rate','N/A')}
    </small>
  </div>
  {cards}
</div>
"""


# =============================================================================
# TOP RISQUES AGENT 2
# =============================================================================

def render_agent2_top(a2_data: dict) -> str:
    risques = a2_data.get("risques", [])
    if not risques:
        return ""

    top5 = sorted(risques, key=lambda r: r.get("priority_score", 0), reverse=True)[:5]
    cards = ""

    for r in top5:
        niveau = r.get("niveau_residuel", "mineur")
        color = niveau_color(niveau)
        justif = esc(r.get("justification_evaluation", "")[:220])
        impact = esc(r.get("impact_prospectif", "")[:200])
        reco = esc(r.get("recommandation_llm", "")[:200])
        llm_tag = badge("LLM", COLORS["bleu"], True) if r.get("llm_used") else ""
        maitrise = r.get("efficacite_maitrise_pct", 0)
        m_color = (
            COLORS["vert"] if maitrise >= 50 else
            COLORS["jaune"] if maitrise >= 30 else COLORS["rouge"]
        )

        cards += f"""
    <div class="alert-card" style="border-left-color:{color};">
      <div class="a-title">
        {badge(niveau.upper(), color)}
        &nbsp;{esc(r.get('risque', '')[:100])} {llm_tag}
      </div>
      <div class="a-meta">
        {esc(r.get('processus', ''))} &bull;
        Brut: <b>{fmt(r.get('score_brut'))}</b> &rarr;
        Residuel: <b style="color:{color};">{fmt(r.get('score_residuel'))}</b> &bull;
        Priorite: <b>{fmt(r.get('priority_score'))}/10</b> &bull;
        Maitrise: <b style="color:{m_color};">{maitrise}%</b> &bull;
        {esc(r.get('mode_evaluation','')[:20])}
      </div>
      <div class="bar-wrap"><div class="bar-fill" style="width:{min(100,maitrise)}%;background:{m_color};"></div></div>
      {f'<div class="a-block"><div class="a-lbl">Justification</div>{justif}</div>' if justif else ''}
      {f'<div class="a-block"><div class="a-lbl">Impact prospectif (6-12 mois)</div>{impact}</div>' if impact else ''}
      {f'<div class="a-block"><div class="a-lbl">Recommandation</div>{reco}</div>' if reco else ''}
    </div>"""

    return f"""
<div class="section" id="a2top">
  <div class="s-title">Top Risques Prioritaires &mdash; Agent 2 Evaluation Formelle
    <span class="agent-tag" style="background:#0d9488;color:#fff;">Agent 2</span>
  </div>
  {cards}
</div>
"""


# =============================================================================
# CARTOGRAPHIE RESIDUELLE DYNAMIQUE
# =============================================================================

def render_carto(a4_data: dict) -> str:
    carto = a4_data.get("cartographie_residuelle_dynamique", [])
    if not carto:
        return ""

    rows = ""
    for r in carto:
        statut = r.get("statut_dynamique", "")
        if "ALERTE" in statut:
            scolor = COLORS["ALERTE"]
            slabel = "ALERTE"
        elif "SURVEILLANCE" in statut:
            scolor = COLORS["SURVEILLANCE"]
            slabel = "SURVEILLANCE"
        else:
            scolor = COLORS["STABLE"]
            slabel = "STABLE"

        rows += f"""
    <tr>
      <td>{esc(r.get('processus',''))}</td>
      <td style="font-size:11px;">{esc(r.get('risque','')[:70])}</td>
      <td style="text-align:center;">{fmt(r.get('rpn_initial'))}</td>
      <td style="text-align:center;">{fmt(r.get('rpn_residuel_attendu'))}</td>
      <td style="text-align:center;font-weight:700;">
        {r.get('nb_signaux_detectes',0)}</td>
      <td>{badge(slabel, scolor, True)}</td>
      <td style="font-size:11px;">{esc(r.get('action_recommandee',''))}</td>
    </tr>"""

    return f"""
<div class="section" id="carto">
  <div class="s-title">Cartographie Residuelle Dynamique
    <span class="agent-tag" style="background:#1e3a5f;color:#fff;">Agent 4</span>
  </div>
  <div style="overflow-x:auto;">
    <table>
      <thead>
        <tr>
          <th>Processus</th><th>Risque</th>
          <th>RPN initial</th><th>RPN residuel</th>
          <th>Signaux</th><th>Statut</th><th>Action</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>
"""


# =============================================================================
# REGISTRE RISQUES AGENT 2 (TABLEAU COMPLET)
# =============================================================================

def render_risk_register(a2_data: dict) -> str:
    risques = a2_data.get("risques", [])
    if not risques:
        return ""

    rows = ""
    for r in risques:
        niveau = r.get("niveau_residuel", "mineur")
        ncolor = niveau_color(niveau)
        nbrut = r.get("niveau_brut", "mineur")
        nbcolor = niveau_color(nbrut)
        reeval = (
            '<span style="color:#dc2626;">&#9888;</span>'
            if r.get("reevaluation_requise") else ""
        )
        llm_tag = (
            '<span style="background:#0284c7;color:#fff;font-size:9px;'
            'padding:1px 4px;border-radius:2px;">LLM</span>'
            if r.get("llm_used") else ""
        )

        rows += f"""
    <tr>
      <td style="color:{COLORS['sub']};font-size:10px;">{esc(r.get('code',''))}</td>
      <td>{esc(r.get('processus','')[:35])}</td>
      <td style="font-size:11px;">{esc(r.get('risque','')[:70])}</td>
      <td style="text-align:center;font-weight:700;">{fmt(r.get('score_brut'))}</td>
      <td style="text-align:center;">{badge(nbrut.upper(), nbcolor, True)}</td>
      <td style="text-align:center;font-weight:700;color:{ncolor};">{fmt(r.get('score_residuel'))}</td>
      <td style="text-align:center;">{badge(niveau.upper(), ncolor, True)}</td>
      <td style="text-align:center;">{fmt(r.get('indice_maitrise'))}/3</td>
      <td style="text-align:center;font-weight:700;">{fmt(r.get('priority_score'))}</td>
      <td style="font-size:11px;">{esc(r.get('statut_decisionnel','')[:25])}</td>
      <td style="text-align:center;">{reeval} {llm_tag}</td>
    </tr>"""

    return f"""
<div class="section" id="register">
  <div class="s-title">Registre Complet des Risques Evalues
    <span class="agent-tag" style="background:#0d9488;color:#fff;">Agent 2</span>
  </div>
  <div style="overflow-x:auto;">
    <table>
      <thead>
        <tr>
          <th>Code</th><th>Processus</th><th>Risque</th>
          <th>Brut</th><th>Niv.B</th>
          <th>Residuel</th><th>Niv.R</th>
          <th>Maitrise</th><th>Priorite</th><th>Statut</th><th>Info</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>
"""


# =============================================================================
# OPPORTUNITES CONSOLIDEES
# =============================================================================

def render_opportunities(a2_data: dict, a4_data: dict) -> str:
    a2_opps = a2_data.get("opportunites", [])
    a4_opps = a4_data.get("opportunites", [])

    total = len(a2_opps) + len(a4_opps)
    if total == 0:
        return """
<div class="section" id="opps">
  <div class="s-title">Opportunites de Capitalisation</div>
  <p style="color:#6b7280;">Aucune opportunite detectee.</p>
</div>
"""

    def opp_card(o: dict, source_tag: str, tag_color: str) -> str:
        niveau = o.get("niveau_opportunite", o.get("niveau", "faible"))
        nc = COLORS.get(niveau, "#6b7280")
        reduction = o.get("reduction_rpn_pct", "")
        axe = esc(o.get("axe_capitalisation", o.get("benefice_attendu", ""))[:200])
        plan = esc(o.get("plan_action_opp", "")[:200])
        llm_tag = badge("LLM", COLORS["bleu"], True) if o.get("llm_used") else ""
        iop = o.get("indice_priorite_opp", 0)
        iop_w = min(100, int(float(iop) / 3 * 100)) if iop else 0

        return f"""
    <div class="opp-card">
      <div class="opp-title">
        {badge(niveau.upper(), nc)}
        &nbsp;{badge(source_tag, tag_color, True)}
        &nbsp;{esc(o.get('processus',''))} {llm_tag}
      </div>
      <div style="font-size:11px;color:{COLORS['sub']};margin-bottom:6px;">
        {f"Reduction RPN : <b>{reduction}%</b> &nbsp;&bull;&nbsp;" if reduction else ""}
        IOP : <b>{fmt(iop)}/3</b> &nbsp;&bull;&nbsp;
        Strategie : {esc(o.get('strategie_opportunite',''))}
      </div>
      <div class="bar-wrap"><div class="bar-fill" style="width:{iop_w}%;background:{nc};"></div></div>
      {f'<div class="a-block" style="margin-top:8px;"><div class="a-lbl">Axe de capitalisation</div>{axe}</div>' if axe else ''}
      {f'<div class="a-block"><div class="a-lbl">Plan d action</div>{plan}</div>' if plan else ''}
    </div>"""

    cards = ""
    for o in sorted(a2_opps, key=lambda x: x.get("indice_priorite_opp", 0), reverse=True):
        cards += opp_card(o, "Agent 2", COLORS["teal"])
    for o in sorted(a4_opps, key=lambda x: x.get("criticality_score", 0) or 0, reverse=True):
        cards += opp_card(o, "Agent 4", COLORS["bleu"])

    return f"""
<div class="section" id="opps">
  <div class="s-title">Opportunites de Capitalisation ({total})
    <span class="agent-tag" style="background:{COLORS['teal']};color:#fff;">
      A2: {len(a2_opps)} &nbsp;|&nbsp; A4: {len(a4_opps)}</span>
  </div>
  {cards}
</div>
"""


# =============================================================================
# FOOTER
# =============================================================================

def render_footer(data: dict) -> str:
    meta = data.get("metadata", {})
    sources = meta.get("sources", [])
    conf = meta.get("conformite_cdc", {})

    sources_html = "".join(f"<li>{esc(s)}</li>" for s in sources)
    conf_html = "".join(
        f'<span style="margin-right:14px;">'
        f'<span class="{"check-ok" if v else "check-no"}">{"&#10003;" if v else "&#10007;"}</span>'
        f' {esc(k.replace("_", " "))}</span>'
        for k, v in conf.items()
    )

    return f"""
</div>
<div class="footer">
  <h3>Sources de donnees</h3>
  <ul style="margin-left:20px;margin-bottom:16px;">{sources_html}</ul>
  <h3>Conformite Cahier des Charges</h3>
  <div style="margin-bottom:16px;">{conf_html}</div>
  <p>
    Norme de reference : ISO 9001:2015 / ISO 31000:2018 / ISO 45001:2018
    &nbsp;&bull;&nbsp; Modele LLM : {esc(meta.get('model_llm','N/A'))}
    &nbsp;&bull;&nbsp; Genere par QALITAS QSE Orchestrateur
  </p>
</div>
</body>
</html>"""


# =============================================================================
# GENERATEUR PRINCIPAL
# =============================================================================

def generate_html(data: dict) -> str:
    meta = data.get("metadata", {})
    generated_at = meta.get("generated_at", datetime.now().isoformat())[:19]
    a2 = data.get("agent2", {})
    a4 = data.get("agent4", {})
    cross = data.get("cross_process_view", [])

    html = render_head(generated_at)
    html += render_kpi_strip(data)
    html += render_cross_view(cross)
    html += render_agent4_top(a4)
    html += render_agent2_top(a2)
    html += render_carto(a4)
    html += render_risk_register(a2)
    html += render_opportunities(a2, a4)
    html += render_footer(data)
    return html


def generate_qalitas_dashboard(json_path: str, output_html: str) -> None:
    """
    Lit qalitas_latest.json et genere le dashboard HTML consolide.
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    html_content = generate_html(data)

    with open(output_html, "w", encoding="utf-8") as f:
        f.write(html_content)

    size_kb = round(os.path.getsize(output_html) / 1024, 1)
    print(f"Dashboard QALITAS genere : {output_html} ({size_kb} KB)")


if __name__ == "__main__":
    base = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(base, "output", "qalitas_latest.json")
    dst = os.path.join(base, "output", "qalitas_dashboard.html")

    if not os.path.exists(src):
        print("Fichier introuvable. Lancez d'abord : python qalitas_main.py")
    else:
        generate_qalitas_dashboard(src, dst)
