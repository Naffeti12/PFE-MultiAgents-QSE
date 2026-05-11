const fs = require('fs');
const path = require('path');

const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  ImageRun, Header, Footer, AlignmentType, LevelFormat, BorderStyle,
  WidthType, ShadingType, PageNumber, PageBreak, HeadingLevel,
  TableOfContents, TabStopType, TabStopPosition, PositionalTab,
  PositionalTabAlignment, PositionalTabRelativeTo, PositionalTabLeader
} = require('/usr/local/lib/node_modules_global/lib/node_modules/docx');

// ─── Images ─────────────────────────────────────────────────────────────────
const figDir = '/sessions/trusting-peaceful-fermat/mnt/PFE/rapport/fig';
const isgLogo  = fs.readFileSync(path.join(figDir, 'isg-logo.png'));
const repLogo  = fs.readFileSync(path.join(figDir, 'logo_rep_fixed.png'));
const univLogo = fs.readFileSync(path.join(figDir, 'univ_tunis.png'));
const timLogo  = fs.readFileSync(path.join(figDir, 'avatar.png'));

// ─── Constants ───────────────────────────────────────────────────────────────
const A4_W      = 11906;
const A4_H      = 16838;
const MARGIN    = 1134;
const CONTENT_W = A4_W - 2 * MARGIN;
const HALF_W    = Math.floor(CONTENT_W / 2);
const THIRD_W   = Math.floor(CONTENT_W / 3);
const NO_B      = { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' };
const NO_BORDERS = { top: NO_B, bottom: NO_B, left: NO_B, right: NO_B };

// ─── Helpers ─────────────────────────────────────────────────────────────────
const sp = (after = 120) => new Paragraph({ spacing: { after } });

const hrule = (color = '2E75B6') => new Paragraph({
  border: { bottom: { style: BorderStyle.SINGLE, size: 8, color, space: 1 } },
  spacing: { before: 60, after: 80 }
});

const pgBreak = () => new Paragraph({ children: [new PageBreak()] });

function pCenter(text, size = 24, bold = false, color = '000000') {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 60 },
    children: [new TextRun({ text, size, bold, color })]
  });
}

// Inline **bold** parsing, justified by default
function p(text, opts = {}) {
  const runs = text.split(/(\*\*[^*]+\*\*)/g).filter(s => s).map(s =>
    s.startsWith('**') && s.endsWith('**')
      ? new TextRun({ text: s.slice(2,-2), bold: true })
      : new TextRun({ text: s })
  );
  return new Paragraph({
    alignment: opts.center ? AlignmentType.CENTER
             : opts.right  ? AlignmentType.RIGHT
             : opts.left   ? AlignmentType.LEFT
             : AlignmentType.JUSTIFIED,
    spacing: { after: opts.after !== undefined ? opts.after : 160, line: 276 },
    children: runs
  });
}

const pItalic = text => new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 160, after: 160, line: 276 },
  children: [new TextRun({ text, italics: true })]
});

const bullet = text => {
  const m = text.match(/^\*\*(.+?)\*\*\s*(.*)/s);
  return new Paragraph({
    numbering: { reference: 'bullets', level: 0 },
    spacing: { after: 80, line: 276 },
    children: m
      ? [new TextRun({ text: m[1], bold: true }), new TextRun({ text: ' ' + m[2] })]
      : [new TextRun({ text })]
  });
};

const h2 = text => new Paragraph({
  heading: HeadingLevel.HEADING_2,
  spacing: { before: 280, after: 120 },
  children: [new TextRun({ text })]
});

const h3 = text => new Paragraph({
  heading: HeadingLevel.HEADING_3,
  spacing: { before: 200, after: 80 },
  children: [new TextRun({ text })]
});

const boldLabel = (text, color = '1F3864') => new Paragraph({
  spacing: { before: 180, after: 60 },
  children: [new TextRun({ text, bold: true, size: 26, color })]
});

const hdr = text => new Header({ children: [new Paragraph({
  alignment: AlignmentType.RIGHT,
  border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: '2E75B6', space: 1 } },
  spacing: { after: 60 },
  children: [new TextRun({ text, color: '2E75B6', size: 18 })]
})] });

const ftr = () => new Footer({ children: [new Paragraph({
  alignment: AlignmentType.CENTER,
  children: [new TextRun({ children: [PageNumber.CURRENT] })]
})] });

const logoImg = (data, w, h) => new ImageRun({
  type: 'png', data,
  transformation: { width: w, height: h },
  altText: { title: 'logo', description: 'logo', name: 'logo' }
});

const centeredLogo = (data, w, h, caption) => [
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 100, after: caption ? 40 : 140 },
    children: [logoImg(data, w, h)]
  }),
  ...(caption ? [new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [new TextRun({ text: caption, italics: true, size: 20 })]
  })] : [])
];

// ─── Figure list (28 entries) ────────────────────────────────────────────────
const FIGURES = [
  ['1.1',  'Logo de TIM Group Tunisie',                          '1'],
  ['2.1',  'Architecture globale de QALITAS',                    '2'],
  ['2.2',  'Exemple de dashboard QALITAS (KPI et risques)',       '2'],
  ['2.3',  'Cycle de gestion des risques (ISO)',                  '2'],
  ['2.4',  'Structure du score RPN (probabilité, impact, détectabilité)', '2'],
  ['2.5',  'Architecture d\'un système multi-agents',            '2'],
  ['2.6',  'Schéma simplifié du fonctionnement d\'un LLM',       '2'],
  ['2.7',  'Comparaison approche classique vs approche IA',      '2'],
  ['3.1',  'Architecture globale de la plateforme multi-agents', '3'],
  ['3.2',  'Flux de données entre les agents (pipeline)',        '3'],
  ['3.3',  'Diagramme UML de cas d\'utilisation',               '3'],
  ['3.4',  'Diagramme de séquence du système',                  '3'],
  ['3.5',  'Fonctionnement de l\'Agent 1 — Extraction',         '3'],
  ['3.6',  'Fonctionnement de l\'Agent 2 — Analyse et réévaluation', '3'],
  ['3.7',  'Fonctionnement de l\'Agent 3 — Détection des opportunités', '3'],
  ['3.8',  'Fonctionnement de l\'Agent 4 — Monitoring et alertes', '3'],
  ['3.9',  'Structure des données (JSON)',                       '3'],
  ['3.10', 'Structure du PipelineState',                         '3'],
  ['3.11', 'Intégration du LLM dans le système',                '3'],
  ['4.1',  'Architecture technique (Python, Ollama, API)',       '4'],
  ['5.1',  'Exemple de données en entrée (dashboard PDF)',       '5'],
  ['5.2',  'Exemple d\'alerte détectée',                        '5'],
  ['5.3',  'Exemple de sortie enrichie (JSON final)',            '5'],
  ['5.4',  'Cas complet — entrée → analyse → recommandation',   '5'],
  ['5.5',  'Score de priorité des risques',                      '5'],
  ['5.6',  'Répartition des risques par gravité',               '5'],
  ['6.1',  'Synthèse du fonctionnement global',                  '6'],
  ['6.2',  'Schéma des perspectives d\'évolution',              '6'],
];

// ─── Tables list (27 entries) ────────────────────────────────────────────────
const TABLES = [
  ['1',  'Chiffres clés de TIM Group Tunisie'],
  ['2',  'Comparaison des normes ISO (9001, 45001, 14001)'],
  ['3',  'Services et produits proposés par TIM Tunisie'],
  ['4',  'Endpoints de l\'API REST QALITAS utilisés dans le projet'],
  ['5',  'Comparaison des frameworks d\'orchestration agentique'],
  ['6',  'Comparaison des modèles LLM (GPT-4, Mistral, LLaMA3)'],
  ['7',  'Analyse de l\'existant — forces et limites de QALITAS'],
  ['8',  'Besoins fonctionnels de la plateforme'],
  ['9',  'Besoins non fonctionnels de la plateforme'],
  ['10', 'Description des sources de données utilisées'],
  ['11', 'Structure du message d\'état partagé (PipelineState)'],
  ['12', 'Description des agents — rôles, entrées et sorties'],
  ['13', 'Règles de calcul du score RPN (Agent 2)'],
  ['14', 'Critères de détection des opportunités (Agent 3)'],
  ['15', 'Structure JSON des données d\'entrée (Agent 1)'],
  ['16', 'Structure JSON des risques réévalués (Agent 2)'],
  ['17', 'Structure JSON des opportunités détectées (Agent 3)'],
  ['18', 'Structure JSON des alertes et actions (Agent 4)'],
  ['19', 'Stack technologique — environnement d\'implémentation'],
  ['20', 'Cas de gestion des erreurs et stratégies de traitement'],
  ['21', 'Données de test utilisées (risques QALITAS réels)'],
  ['22', 'Résultats de réévaluation des risques (RPN avant / après)'],
  ['23', 'Opportunités détectées et injectées dans QALITAS'],
  ['24', 'Actions correctives générées et injectées dans QALITAS'],
  ['25', 'Évaluation des performances du système'],
  ['26', 'Comparaison approche classique vs approche IA multi-agents'],
  ['27', 'Synthèse des apports, limites et perspectives du système'],
];

// ─── TOC entries (manual, styled) ────────────────────────────────────────────
const TOC_DATA = [
  { level: 0, text: 'Liste des acronymes' },
  { level: 0, text: 'Liste des figures' },
  { level: 0, text: 'Liste des tableaux' },
  { level: 0, text: 'Introduction générale' },
  { level: 0, text: 'Chapitre 1 : Présentation de l\'entreprise et cahier des charges' },
  { level: 1, text: '1. Présentation de l\'entreprise d\'accueil' },
  { level: 2, text: '1.1 Les atouts et les missions de l\'entreprise' },
  { level: 2, text: '1.2 Chiffres clés et pays d\'interventions' },
  { level: 2, text: '1.3 Les services, produits et références' },
  { level: 3, text: '1.3.1 QALITAS' },
  { level: 3, text: '1.3.2 GMAO PRO' },
  { level: 2, text: '1.4 Références de TIM' },
  { level: 1, text: '2. Contexte du projet' },
  { level: 1, text: '3. Objectifs du projet' },
  { level: 2, text: '3.1 Objectif principal' },
  { level: 2, text: '3.2 Objectifs spécifiques' },
  { level: 1, text: '4. Cahier des charges' },
  { level: 2, text: '4.1 Architecture globale' },
  { level: 2, text: '4.2 Description des agents' },
  { level: 2, text: '4.3 Orchestrateur' },
  { level: 2, text: '4.4 Exigences transverses' },
  { level: 2, text: '4.5 Environnement technique' },
  { level: 1, text: '5. État de l\'art' },
  { level: 2, text: '5.1 Gestion des risques et opportunités (ISO)' },
  { level: 2, text: '5.2 Systèmes multi-agents (SMA)' },
  { level: 2, text: '5.3 Grands modèles de langage (LLM)' },
  { level: 2, text: '5.4 Frameworks d\'orchestration (LangGraph, LangChain)' },
  { level: 2, text: '5.5 Plateformes QSE et API QALITAS' },
  { level: 0, text: 'Chapitre 2 : Analyse des besoins' },
  { level: 1, text: '2.1 Présentation de l\'environnement du projet' },
  { level: 1, text: '2.2 Analyse de l\'existant' },
  { level: 1, text: '2.3 Identification des limites' },
  { level: 1, text: '2.4 Recueil des besoins' },
  { level: 2, text: '2.4.1 Besoins fonctionnels' },
  { level: 2, text: '2.4.2 Besoins non fonctionnels' },
  { level: 1, text: '2.5 Description des données utilisées' },
  { level: 0, text: 'Chapitre 3 : Conception du système' },
  { level: 1, text: '3.1 Architecture globale' },
  { level: 1, text: '3.2 Approche multi-agents' },
  { level: 1, text: '3.3 Description des agents' },
  { level: 2, text: '3.3.1 Agent 1 — Extraction et structuration' },
  { level: 2, text: '3.3.2 Agent 2 — Analyse et réévaluation (RPN)' },
  { level: 2, text: '3.3.3 Agent 3 — Détection des opportunités' },
  { level: 2, text: '3.3.4 Agent 4 — Monitoring, alertes et injection QALITAS' },
  { level: 1, text: '3.4 Flux de données' },
  { level: 1, text: '3.5 Modélisation des données (JSON / PipelineState)' },
  { level: 1, text: '3.6 Intégration du LLM (Ollama)' },
  { level: 1, text: '3.7 Justification des choix techniques' },
  { level: 0, text: 'Chapitre 4 : Implémentation' },
  { level: 1, text: '4.1 Environnement technique' },
  { level: 1, text: '4.2 Implémentation des agents' },
  { level: 2, text: '4.2.1 Agent 1 — Extraction des données' },
  { level: 2, text: '4.2.2 Agent 2 — Analyse et réévaluation' },
  { level: 2, text: '4.2.3 Agent 3 — Détection des opportunités' },
  { level: 2, text: '4.2.4 Agent 4 — LLM + logique métier + injection' },
  { level: 1, text: '4.3 Gestion des erreurs et cache d\'injection' },
  { level: 1, text: '4.4 Structuration des résultats (JSON)' },
  { level: 0, text: 'Chapitre 5 : Résultats et validation' },
  { level: 1, text: '5.1 Données de test' },
  { level: 1, text: '5.2 Résultats obtenus' },
  { level: 1, text: '5.3 Analyse des résultats' },
  { level: 1, text: '5.4 Cas d\'étude réel (alerte → analyse → recommandation)' },
  { level: 1, text: '5.5 Évaluation des performances' },
  { level: 1, text: '5.6 Comparaison avec l\'approche classique' },
  { level: 1, text: '5.7 Limites observées' },
  { level: 0, text: 'Chapitre 6 : Discussion et perspectives' },
  { level: 1, text: '6.1 Apports du système' },
  { level: 1, text: '6.2 Limites' },
  { level: 1, text: '6.3 Améliorations possibles' },
  { level: 0, text: 'Conclusion générale' },
  { level: 0, text: 'Bibliographie' },
  { level: 0, text: 'Annexes' },
];

function tocRow(level, text) {
  const indents  = [0, 360, 720, 1080];
  const sizes    = [26, 24, 22, 20];
  const bolds    = [true, false, false, false];
  const colors   = ['1F3864', '2E75B6', '333333', '555555'];
  return new Paragraph({
    spacing: { after: level === 0 ? 120 : 60, line: 264 },
    indent: { left: indents[level] },
    tabStops: [{ type: TabStopType.RIGHT, position: CONTENT_W, leader: 'dot' }],
    children: [
      new TextRun({ text, bold: bolds[level], size: sizes[level], color: colors[level] }),
      new TextRun({ text: '\t', size: sizes[level] }),
    ]
  });
}

// ─── DOCUMENT ────────────────────────────────────────────────────────────────
const doc = new Document({
  numbering: {
    config: [{ reference: 'bullets', levels: [{
      level: 0, format: LevelFormat.BULLET, text: '\u2022',
      alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 720, hanging: 360 } } }
    }] }]
  },
  styles: {
    default: { document: { run: { font: 'Arial', size: 24 } } },
    paragraphStyles: [
      { id:'Heading1', name:'Heading 1', basedOn:'Normal', next:'Normal', quickFormat:true,
        run: { size:36, bold:true, font:'Arial', color:'1F3864' },
        paragraph: { spacing:{ before:480, after:240 }, outlineLevel:0 } },
      { id:'Heading2', name:'Heading 2', basedOn:'Normal', next:'Normal', quickFormat:true,
        run: { size:28, bold:true, font:'Arial', color:'2E75B6' },
        paragraph: { spacing:{ before:320, after:160 }, outlineLevel:1 } },
      { id:'Heading3', name:'Heading 3', basedOn:'Normal', next:'Normal', quickFormat:true,
        run: { size:24, bold:true, font:'Arial', color:'2E75B6' },
        paragraph: { spacing:{ before:200, after:100 }, outlineLevel:2 } },
    ]
  },

  sections: [

    // ══════════════════════════════════════════════════════════════════════
    // SECTION 1 : PAGE DE GARDE  (fidèle au LaTeX original)
    // ══════════════════════════════════════════════════════════════════════
    {
      properties: {
        page: { size:{ width:A4_W, height:A4_H },
                margin:{ top:720, right:MARGIN, bottom:720, left:MARGIN } }
      },
      children: [
        // 3 logos en ligne
        new Table({
          width: { size: CONTENT_W, type: WidthType.DXA },
          columnWidths: [THIRD_W, THIRD_W, CONTENT_W - 2*THIRD_W],
          rows: [new TableRow({ children: [
            new TableCell({ borders: NO_BORDERS, children: [new Paragraph({
              alignment: AlignmentType.LEFT,
              children: [logoImg(isgLogo, 110, 74)]
            })] }),
            new TableCell({ borders: NO_BORDERS, children: [new Paragraph({
              alignment: AlignmentType.CENTER,
              children: [logoImg(repLogo, 46, 75)]
            })] }),
            new TableCell({ borders: NO_BORDERS, children: [new Paragraph({
              alignment: AlignmentType.RIGHT,
              children: [logoImg(univLogo, 110, 57)]
            })] }),
          ]})]
        }),
        sp(60),
        pCenter('République Tunisienne', 20),
        pCenter('Ministère de l\'Enseignement Supérieur et de la Recherche Scientifique', 20),
        pCenter('Université de Tunis', 22, true),
        pCenter('Institut Supérieur de Gestion de Tunis', 22, true),
        sp(40),
        hrule('333333'),
        sp(40),
        pCenter('Rapport de Projet de Fin d\'Études', 26, true),
        sp(16),
        pCenter('En vue de l\'obtention du diplôme de', 22),
        pCenter('Licence Nationale en Business Computing', 22),
        sp(16),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing:{ after:40 },
          children:[new TextRun({text:'Parcours : ',bold:true,size:22}),
                    new TextRun({text:'Business Information Systems',size:22})] }),
        sp(40),
        hrule('333333'),
        sp(60),
        // Titre du sujet
        new Paragraph({ alignment: AlignmentType.CENTER, spacing:{ before:40, after:40 },
          children:[new TextRun({
            text:'Plateforme d\'Agents IA pour les risques et opportunités\ndes systèmes de management (QALITAS QSE)',
            size:32, bold:true, color:'1F3864'})] }),
        hrule('333333'),
        sp(60),
        // Organisme d'accueil
        pCenter('Organisme d\'accueil :', 22, true),
        sp(16),
        pCenter('TIM Group Tunisie', 28, false, '1F3864'),
        sp(16),
        ...centeredLogo(timLogo, 80, 80, ''),
        sp(16),
        // Élaboré par
        pCenter('Élaboré par :', 22, true),
        sp(16),
        pCenter('Neffati Mohamed', 28, false, '1F3864'),
        sp(40),
        // Encadré par — 2 colonnes
        pCenter('Encadré par :', 22, true),
        sp(24),
        new Table({
          width: { size: CONTENT_W, type: WidthType.DXA },
          columnWidths: [HALF_W, CONTENT_W - HALF_W],
          rows: [new TableRow({ children: [
            new TableCell({ borders: NO_BORDERS, children: [
              new Paragraph({ alignment: AlignmentType.LEFT,
                children:[new TextRun({text:'Enc. Pédagogique',bold:true,size:24})] }),
              new Paragraph({ alignment: AlignmentType.LEFT, spacing:{after:0},
                children:[new TextRun({text:'Dr. Baati Lassaad',size:22})] }),
            ]}),
            new TableCell({ borders: NO_BORDERS, children: [
              new Paragraph({ alignment: AlignmentType.RIGHT,
                children:[new TextRun({text:'Enc. Professionnelle',bold:true,size:24})] }),
              new Paragraph({ alignment: AlignmentType.RIGHT, spacing:{after:0},
                children:[new TextRun({text:'Mme Hzami Olfa',size:22})] }),
            ]}),
          ]})]
        }),
        sp(80),
        pCenter('Année Universitaire', 22, true),
        sp(16),
        pCenter('2025 / 2026', 28, false, '1F3864'),
      ]
    },

    // ══════════════════════════════════════════════════════════════════════
    // SECTION 2 : TABLE DES MATIÈRES + LISTE DES ACRONYMES + LISTE DES FIGURES
    // ══════════════════════════════════════════════════════════════════════
    {
      properties: {
        page: { size:{ width:A4_W, height:A4_H },
                margin:{ top:MARGIN, right:MARGIN, bottom:MARGIN, left:MARGIN } }
      },
      footers: { default: ftr() },
      children: [

        // ── TABLE DES MATIÈRES ──────────────────────────────────────────
        new Paragraph({ alignment: AlignmentType.CENTER, spacing:{ before:0, after:200 },
          children:[new TextRun({text:'Table des matières',size:40,bold:true,color:'1F3864'})] }),
        hrule(),
        sp(160),
        ...TOC_DATA.map(({ level, text }) => tocRow(level, text)),

        // ── LISTE DES ACRONYMES ─────────────────────────────────────────
        pgBreak(),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing:{ before:0, after:200 },
          children:[new TextRun({text:'Liste des acronymes',size:40,bold:true,color:'1F3864'})] }),
        hrule(),
        sp(120),
        new Table({
          width: { size: CONTENT_W, type: WidthType.DXA },
          columnWidths: [2200, CONTENT_W - 2200],
          rows: [
            ['QSE',   'Qualité, Sécurité, Environnement'],
            ['QHSE',  'Qualité, Hygiène, Sécurité, Environnement'],
            ['SMQ',   'Système de Management de la Qualité'],
            ['ISO',   'International Organization for Standardization'],
            ['NC',    'Non-Conformité'],
            ['KPI',   'Key Performance Indicator — Indicateur Clé de Performance'],
            ['R&O',   'Risques et Opportunités'],
            ['RPN',   'Risk Priority Number — Indice de Criticité'],
            ['CDC',   'Cahier des Charges'],
            ['IA',    'Intelligence Artificielle'],
            ['SMA',   'Système Multi-Agents'],
            ['LLM',   'Large Language Model — Grand Modèle de Langage'],
            ['API',   'Application Programming Interface'],
            ['REST',  'Representational State Transfer'],
            ['HTTP',  'HyperText Transfer Protocol'],
            ['HTML',  'HyperText Markup Language'],
            ['JSON',  'JavaScript Object Notation'],
            ['PDF',   'Portable Document Format'],
            ['URL',   'Uniform Resource Locator'],
            ['UI',    'User Interface — Interface Utilisateur'],
            ['GUID',  'Globally Unique Identifier'],
            ['UUID',  'Universally Unique Identifier'],
            ['SHA',   'Secure Hash Algorithm'],
            ['TTL',   'Time To Live'],
            ['XLS',   'Excel Spreadsheet — Format Microsoft Excel'],
            ['UML',   'Unified Modeling Language'],
            ['IDE',   'Integrated Development Environment'],
            ['GIT',   'Global Information Tracker'],
            ['GQAO',  'Gestion de la Qualité Assistée par Ordinateur'],
            ['GMAO',  'Gestion de la Maintenance Assistée par Ordinateur'],
            ['GPAO',  'Gestion de la Production Assistée par Ordinateur'],
          ].map(([abbr, def], i) => new TableRow({ children: [
            new TableCell({
              width: { size: 2200, type: WidthType.DXA },
              borders: { top:{style:BorderStyle.SINGLE,size:1,color:'DDDDDD'}, bottom:{style:BorderStyle.SINGLE,size:1,color:'DDDDDD'}, left:NO_B, right:NO_B },
              shading: { fill: i%2===0 ? 'EEF4FB' : 'FFFFFF', type: ShadingType.CLEAR },
              margins: { top:60, bottom:60, left:80, right:80 },
              children: [new Paragraph({ children:[new TextRun({text:abbr,bold:true,color:'1F3864'})] })]
            }),
            new TableCell({
              width: { size: CONTENT_W-2200, type: WidthType.DXA },
              borders: { top:{style:BorderStyle.SINGLE,size:1,color:'DDDDDD'}, bottom:{style:BorderStyle.SINGLE,size:1,color:'DDDDDD'}, left:NO_B, right:NO_B },
              shading: { fill: i%2===0 ? 'EEF4FB' : 'FFFFFF', type: ShadingType.CLEAR },
              margins: { top:60, bottom:60, left:120, right:80 },
              children: [new Paragraph({ children:[new TextRun({text:def})] })]
            }),
          ]}))
        }),

        // ── LISTE DES FIGURES ───────────────────────────────────────────
        pgBreak(),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing:{ before:0, after:200 },
          children:[new TextRun({text:'Liste des figures',size:40,bold:true,color:'1F3864'})] }),
        hrule(),
        sp(120),
        new Table({
          width: { size: CONTENT_W, type: WidthType.DXA },
          columnWidths: [1400, CONTENT_W - 1400],
          rows: FIGURES.map(([num, label], i) => new TableRow({ children: [
            new TableCell({
              width: { size: 1400, type: WidthType.DXA },
              borders: { top:{style:BorderStyle.SINGLE,size:1,color:'DDDDDD'}, bottom:{style:BorderStyle.SINGLE,size:1,color:'DDDDDD'}, left:NO_B, right:NO_B },
              shading: { fill: i%2===0 ? 'F5F5F5' : 'FFFFFF', type: ShadingType.CLEAR },
              margins: { top:60, bottom:60, left:80, right:80 },
              children: [new Paragraph({ children:[new TextRun({text:`Figure ${num}`,bold:true,color:'2E75B6'})] })]
            }),
            new TableCell({
              width: { size: CONTENT_W-1400, type: WidthType.DXA },
              borders: { top:{style:BorderStyle.SINGLE,size:1,color:'DDDDDD'}, bottom:{style:BorderStyle.SINGLE,size:1,color:'DDDDDD'}, left:NO_B, right:NO_B },
              shading: { fill: i%2===0 ? 'F5F5F5' : 'FFFFFF', type: ShadingType.CLEAR },
              margins: { top:60, bottom:60, left:120, right:80 },
              children: [new Paragraph({ children:[new TextRun({text:label})] })]
            }),
          ]}))
        }),

        // ── LISTE DES TABLEAUX ──────────────────────────────────────────
        pgBreak(),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing:{ before:0, after:200 },
          children:[new TextRun({text:'Liste des tableaux',size:40,bold:true,color:'1F3864'})] }),
        hrule(),
        sp(120),
        ...TABLES.map(([num, label], i) => new Paragraph({
          spacing: { after: 80, line: 264 },
          tabStops: [{ type: TabStopType.RIGHT, position: CONTENT_W }],
          children: [
            new TextRun({ text: `Tableau ${num} : `, bold: true, color: '2E75B6' }),
            new TextRun({ text: label }),
            new TextRun({ text: '\t' }),
          ]
        })),
      ]
    },

    // ══════════════════════════════════════════════════════════════════════
    // SECTION 3 : INTRODUCTION GÉNÉRALE
    // ══════════════════════════════════════════════════════════════════════
    {
      properties: {
        page: { size:{ width:A4_W, height:A4_H },
                margin:{ top:MARGIN, right:MARGIN, bottom:MARGIN, left:MARGIN } }
      },
      headers: { default: hdr('Introduction générale') },
      footers: { default: ftr() },
      children: [
        new Paragraph({ alignment:AlignmentType.CENTER, spacing:{ before:400, after:300 },
          children:[new TextRun({text:'Introduction générale',size:40,bold:true,color:'1F3864'})] }),
        hrule(),
        h2('Contexte général'),
        p('Dans un environnement économique en constante mutation, les organisations sont soumises à des pressions croissantes pour garantir la conformité réglementaire, maîtriser leurs risques opérationnels et saisir les opportunités d\'amélioration continue. Les référentiels normatifs internationaux, tels que l\'ISO 9001 (qualité), l\'ISO 45001 (santé-sécurité au travail) et l\'ISO 14001 (environnement), imposent désormais une gestion formalisée et proactive des risques et des opportunités (R&O) comme condition indispensable à la certification et au maintien de la performance.'),
        p('La digitalisation des systèmes de management QHSE a permis de centraliser ces données dans des plateformes spécialisées, à l\'image de QALITAS développée par TIM Group Tunisie. Ces outils offrent aux responsables qualité des tableaux de bord riches en indicateurs de performance (KPI), des registres de non-conformités (NC), des cartographies de risques et des plans d\'actions. Cependant, malgré cette richesse informationnelle, le pilotage des R&O reste largement manuel, fragmenté et réactif.'),
        p('Parallèlement, l\'émergence des grands modèles de langage (LLM) et des architectures multi-agents a ouvert de nouvelles perspectives pour l\'automatisation de tâches à forte valeur cognitive. Ces technologies, encadrées par des frameworks d\'orchestration tels que LangGraph, permettent de concevoir des systèmes intelligents capables d\'opérer de façon autonome et coordonnée sur des données métier complexes.'),
        h2('Problématique'),
        p('Malgré la richesse des données disponibles, les responsables qualité sont confrontés à plusieurs limites structurelles :'),
        bullet('**Monitoring discontinu :** La réévaluation des risques est ponctuelle, sans mécanisme d\'alerte automatique sur les dépassements de seuils critiques.'),
        bullet('**Sous-exploitation des données :** Les KPI hors cible et les NC récurrentes ne sont pas corrélés aux entrées risques correspondantes.'),
        bullet('**Réévaluation tardive :** Le score RPN n\'est pas mis à jour automatiquement lorsque les conditions métier évoluent.'),
        bullet('**Opportunités non capitalisées :** Les processus performants ne sont pas exploités pour générer des opportunités d\'amélioration conformes aux exigences ISO.'),
        bullet('**Actions sans contexte :** Les plans d\'actions manquent souvent de priorisation et de lien explicite avec les risques identifiés.'),
        sp(80),
        pItalic('Comment concevoir un système multi-agents intelligent capable d\'extraire, d\'analyser et d\'exploiter de manière autonome les données des tableaux de bord qualité pour assurer le monitoring continu, la réévaluation dynamique et le pilotage proactif des risques et des opportunités dans un système de management QHSE ?'),
        h2('Solution proposée'),
        p('Nous proposons la conception d\'une **plateforme d\'agents IA pour la gestion des R&O des systèmes de management QALITAS QSE**, reposant sur quatre agents spécialisés coordonnés par un orchestrateur LangGraph. Les agents communiquent via un état partagé (PipelineState) et peuvent déclencher des boucles de réévaluation automatiques. La plateforme intègre des LLM pour l\'enrichissement sémantique et la génération d\'actions contextualisées, injectées automatiquement dans QALITAS via son API REST.'),
        h2('Plan du rapport'),
        p('Le rapport est structuré en six chapitres :'),
        sp(40),
        boldLabel('Chapitre 1 — Présentation de l\'entreprise et cahier des charges'),
        p('Présentation de TIM Group Tunisie et de QALITAS, contexte du projet, objectifs, cahier des charges des quatre agents, et état de l\'art (LLM, SMA, LangGraph, normes ISO).'),
        boldLabel('Chapitre 2 — Analyse des besoins'),
        p('Analyse de l\'environnement du projet, étude de l\'existant, identification des limites, recueil des besoins fonctionnels et non fonctionnels, et description des données utilisées.'),
        boldLabel('Chapitre 3 — Conception du système'),
        p('Architecture globale de la plateforme, conception des quatre agents, flux de données, modélisation JSON/PipelineState, intégration du LLM et justification des choix techniques.'),
        boldLabel('Chapitre 4 — Implémentation'),
        p('Environnement technique (Python, LangGraph, Ollama, API QALITAS), implémentation de chaque agent, gestion des erreurs, cache d\'injection et structuration des résultats JSON.'),
        boldLabel('Chapitre 5 — Résultats et validation'),
        p('Données de test, résultats obtenus, cas d\'étude réel (alerte → analyse → recommandation), évaluation des performances, comparaison avec l\'approche classique et limites observées.'),
        boldLabel('Chapitre 6 — Discussion et perspectives'),
        p('Apports du système, limites, améliorations possibles et perspectives d\'évolution (temps-réel, apprentissage continu, déploiement production).'),
      ]
    },

    // ══════════════════════════════════════════════════════════════════════
    // SECTION 4 : CHAPITRE 1
    // ══════════════════════════════════════════════════════════════════════
    {
      properties: {
        page: { size:{ width:A4_W, height:A4_H },
                margin:{ top:MARGIN, right:MARGIN, bottom:MARGIN, left:MARGIN } }
      },
      headers: { default: hdr('Chapitre 1 — Présentation de l\'entreprise et cahier des charges') },
      footers: { default: ftr() },
      children: [
        new Paragraph({ alignment:AlignmentType.CENTER, spacing:{ before:600, after:100 },
          children:[new TextRun({text:'Chapitre 1',size:28,bold:true,color:'2E75B6'})] }),
        new Paragraph({ alignment:AlignmentType.CENTER, spacing:{ after:400 },
          children:[new TextRun({text:'Présentation de l\'entreprise\net cahier des charges',size:44,bold:true,color:'1F3864'})] }),
        hrule(),
        h2('Introduction'),
        p('Ce chapitre présente le contexte du stage réalisé au sein de TIM Group Tunisie. Nous y décrivons l\'entreprise d\'accueil et son produit phare QALITAS, le contexte et les objectifs du projet, le cahier des charges de la plateforme multi-agents, puis un état de l\'art couvrant les technologies et concepts clés mobilisés.'),

        // 1.
        new Paragraph({ heading:HeadingLevel.HEADING_1, children:[new TextRun('1. Présentation de l\'entreprise d\'accueil')] }),
        p('TIM Tunisie, ou Techniques Industrielles et Management, est un organisme multidisciplinaire fondé en 2003 et spécialisé dans le domaine du management et de l\'ingénierie. L\'entreprise se distingue par le développement d\'applications informatiques dédiées, offrant une large gamme de produits logiciels évolutifs, notamment la GQAO, la GMAO et la GPAO. Elle compte une dizaine de collaborateurs ingénieurs et consultants en génie industriel, et travaille avec des partenaires en France, au Canada et au Maroc.'),
        ...centeredLogo(timLogo, 90, 90, 'Figure 1.1 — Logo de TIM Group Tunisie'),

        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('1.1  Les atouts et les missions de l\'entreprise')] }),
        p('TIM Tunisie offre une expertise spécialisée dans la gestion de la qualité, la santé et sécurité au travail, la protection de l\'environnement, la production et la maintenance. Elle se distingue par sa flexibilité, son agilité et sa capacité à offrir des projets intégrés combinant l\'expertise métier avec des systèmes d\'information adaptés.'),

        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('1.2  Chiffres clés et pays d\'interventions')] }),
        bullet('Plus de 1 000 projets réalisés entre consulting, formations et logiciels.'),
        bullet('Interventions dans plus de 40 secteurs (agroalimentaire, automobile, oil & gas…).'),
        bullet('Une expérience de plus de 20 ans.'),
        bullet('Plus de 200 000 personnes touchées.'),
        bullet('Présence dans 12 pays : Tunisie, France, Maroc, Algérie, Italie, Allemagne, Espagne, Bulgarie, Guinée Conakry, Afrique du Sud, RD Congo, Libye.'),

        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('1.3  Les services, produits et références')] }),
        p('TIM Tunisie offre trois catégories de services : **Consulting** (optimisation des systèmes de management), **Digitalisation** (modernisation des processus avec des solutions logicielles) et **Formation** (programmes couvrant divers domaines de management).'),
        h3('1.3.1  QALITAS'),
        p('QALITAS est le produit phare de TIM Tunisie. Conçu pour optimiser la gestion des systèmes de management QHSE, il digitalise les processus de la qualité, de la santé-sécurité et de l\'environnement. Il facilite la conformité aux normes ISO 9001, ISO 45001 et ISO 14001 et offre des fonctionnalités avancées pour la gestion des audits, le suivi des KPI, la gestion des NC et la gestion des R&O.'),
        h3('1.3.2  GMAO PRO'),
        p('GMAO PRO est un logiciel de gestion de la maintenance assistée par ordinateur, destiné à la gestion des équipements industriels. Il permet de gérer les pannes, le planning préventif et les stocks de pièces de rechange.'),

        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('1.4  Références de TIM')] }),
        p('TIM Tunisie collabore avec des entreprises issues de la cosmétique, chimie, oil & gas, mécanique, électricité, électronique, plastique, enseignement, agroalimentaire et textile.'),

        // 2.
        new Paragraph({ heading:HeadingLevel.HEADING_1, children:[new TextRun('2. Contexte du projet')] }),
        p('QALITAS centralise la gestion des R&O pour un nombre croissant d\'entreprises. Cependant, TIM a constaté que le monitoring des risques, la réévaluation dynamique et la détection proactive des opportunités restent essentiellement manuels, chronophages et sujets à des oublis. Face à l\'émergence des LLM et des SMA, TIM a souhaité explorer la conception d\'une couche IA capable d\'automatiser ce cycle de pilotage. C\'est dans ce cadre que s\'inscrit ce projet.'),

        // 3.
        new Paragraph({ heading:HeadingLevel.HEADING_1, children:[new TextRun('3. Objectifs du projet')] }),
        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('3.1  Objectif principal')] }),
        p('Concevoir et développer une **plateforme d\'agents IA** capable d\'extraire, d\'analyser et d\'exploiter les données des tableaux de bord qualité de QALITAS, afin d\'assurer le monitoring continu, la détection, la réévaluation et le pilotage des R&O de façon automatisée.'),
        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('3.2  Objectifs spécifiques')] }),
        bullet('**Extraction et structuration :** Lire et parser automatiquement les données QALITAS (Excel, CSV, API REST).'),
        bullet('**Analyse et réévaluation :** Calculer et mettre à jour le score RPN en intégrant probabilité, impact et détectabilité.'),
        bullet('**Détection des opportunités :** Identifier les processus performants pouvant être convertis en opportunités ISO.'),
        bullet('**Génération d\'actions :** Proposer des plans d\'actions contextualisés et les injecter dans QALITAS via l\'API REST.'),
        bullet('**Pilotage et reporting :** Produire des synthèses structurées pour les responsables qualité.'),

        // 4.
        new Paragraph({ heading:HeadingLevel.HEADING_1, children:[new TextRun('4. Cahier des charges')] }),
        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('4.1  Architecture globale')] }),
        p('La plateforme repose sur quatre agents spécialisés coordonnés par un orchestrateur central LangGraph. Les agents communiquent via un état partagé (PipelineState) et peuvent déclencher des boucles de réévaluation automatiques selon l\'évolution de la criticité des risques.'),
        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('4.2  Description des agents')] }),
        boldLabel('Agent 1 — Extraction et structuration des données'),
        p('Collecte et structure les données brutes depuis les exports Excel, fichiers CSV de KPI et l\'API REST de QALITAS. Produit un contexte de données unifié exploitable par les agents suivants.'),
        boldLabel('Agent 2 — Analyse et réévaluation des risques (RPN)'),
        p('Analyse les risques QALITAS. Recalcule le score RPN en tenant compte de l\'évolution des KPI, de l\'historique des NC et des tendances observées. Identifie les risques critiques nécessitant une réévaluation prioritaire.'),
        boldLabel('Agent 3 — Détection des opportunités'),
        p('Analyse les données de performance des processus pour identifier des opportunités d\'amélioration. S\'appuie sur les KPI, les tendances favorables et les bonnes pratiques sectorielles pour proposer des opportunités argumentées et injectables dans QALITAS.'),
        boldLabel('Agent 4 — Monitoring, alertes et injection QALITAS'),
        p('Génère des alertes contextualisées pour les risques critiques et propose des actions correctives ou préventives. Injecte ces actions directement dans QALITAS via l\'API REST avec les liens vers les risques concernés, les responsables suggérés et les délais.'),
        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('4.3  Orchestrateur')] }),
        p('Un orchestrateur central, implémenté avec LangGraph, coordonne l\'enchaînement des agents et gère le contexte partagé. Il gère la boucle de réévaluation automatique (reeval_required) avec un plafond d\'itérations configurable pour éviter les boucles infinies.'),
        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('4.4  Exigences transverses')] }),
        bullet('Robustesse face aux données manquantes ou incohérentes.'),
        bullet('Traçabilité des décisions de l\'IA par journalisation structurée.'),
        bullet('Gestion des doublons via un cache d\'injection (SHA1, TTL 7 jours).'),
        bullet('Conformité avec les modèles de données de l\'API QALITAS.'),
        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('4.5  Environnement technique')] }),
        p('Le système est opérationnel sur Windows avec accès à QALITAS et aux LLM locaux via Ollama (mistral, llama3) ou distants via les API d\'Anthropic et OpenAI. Stack technique : Python 3.11, LangGraph, LangChain, pandas, openpyxl, requests.'),

        // 5. État de l'art
        new Paragraph({ heading:HeadingLevel.HEADING_1, children:[new TextRun('5. État de l\'art')] }),
        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('5.1  Gestion des risques et opportunités (ISO)')] }),
        p('Les normes ISO 9001:2015, ISO 45001:2018 et ISO 14001:2015 intègrent la gestion des R&O comme exigence fondamentale. Le score RPN (Risk Priority Number), issu des méthodes AMDEC, quantifie la criticité d\'un risque selon trois axes : probabilité d\'occurrence (P), gravité de l\'impact (G) et détectabilité (D), avec RPN = P × G × D.'),
        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('5.2  Systèmes multi-agents (SMA)')] }),
        p('Un SMA est un système composé d\'agents autonomes capables de percevoir leur environnement, de raisonner et d\'agir en coordination pour atteindre des objectifs partagés. Dans notre contexte, chaque agent est spécialisé sur une étape du cycle de pilotage des R&O, et leur coordination est assurée par un orchestrateur via un graphe d\'états.'),
        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('5.3  Grands modèles de langage (LLM)')] }),
        p('Les LLM (GPT-4, Claude, Mistral, LLaMA) sont des modèles entraînés sur de vastes corpus textuels, capables de comprendre et de générer du langage naturel avec un haut niveau de cohérence contextuelle. Ils sont utilisés dans notre plateforme pour l\'enrichissement sémantique des risques, la génération d\'actions argumentées et la production de synthèses QSE.'),
        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('5.4  Frameworks d\'orchestration (LangGraph, LangChain)')] }),
        p('LangGraph permet de modéliser des flux de traitement comme des graphes orientés, où chaque nœud est un agent ou une fonction, et les arêtes définissent les conditions de transition. LangChain fournit des abstractions pour l\'intégration des LLM avec des outils externes (API, bases de données, moteurs de recherche).'),
        new Paragraph({ heading:HeadingLevel.HEADING_2, children:[new TextRun('5.5  Plateformes QSE et API QALITAS')] }),
        p('QALITAS expose une API REST permettant la lecture et l\'écriture des données de management : risques, opportunités, actions, indicateurs et non-conformités. Les endpoints clés utilisés dans notre projet sont : GET /evaluations (liste des R&O), POST /actions (injection d\'actions), GET /kpi (indicateurs de performance) et GET /nonconformites.'),

        h2('Conclusion'),
        p('Ce chapitre a présenté l\'entreprise d\'accueil TIM Group Tunisie, le contexte et les objectifs du projet, le cahier des charges de la plateforme multi-agents et les fondements théoriques nécessaires à sa compréhension. Dans le chapitre suivant, nous procédons à l\'analyse détaillée des besoins fonctionnels et non fonctionnels du système.'),
      ]
    }
  ]
});

// ─── Write ───────────────────────────────────────────────────────────────────
Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync('/tmp/rapport_pfe.docx', buf);
  console.log('OK — ' + buf.length + ' bytes');
}).catch(e => { console.error(e.message); process.exit(1); });
