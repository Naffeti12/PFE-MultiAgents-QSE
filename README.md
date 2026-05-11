# Plateforme Multi-Agents QSE — Monitoring des Risques et Opportunités

> **Projet de Fin d'Études** — Institut Supérieur de Gestion de Tunis  
> **Entreprise d'accueil** : TIM Tunisie  
> **Étudiant** : Neffati Mohamed  
> **Cahier des charges** : `TBR.TIM.IA.S5/2025-2026`

---

## Présentation

Ce projet implémente une **plateforme multi-agents intelligente** capable d'extraire, analyser et exploiter les données issues de la plateforme QSE QALITAS pour assurer le monitoring, la détection, la réévaluation et le pilotage continu des risques et opportunités.

### Architecture du pipeline

```
QALITAS API
    │
    ▼
[Agent 1] Extraction & Normalisation
    │  → Risques, Opportunités, Actions, Processus
    ▼
[Agent 2] Réévaluation RPN
    │  → RPN résiduel, détection dépassements, création actions [Agent2]
    ▼
[Agent 3] Approfondissement Opportunités (LLM)
    │  → Analyse contextuelle, classification, plan d'action [Agent3]
    ▼
[Agent 4] Monitoring, Alertes & Injection (LLM)
    │  → [Agent4-ALERTE] dépassements critiques
    │  → [Agent4-SURVEILLANCE] risques en zone d'attention
    └─→ POST /Actions → QALITAS
```

---

## Installation

### Prérequis

- Python 3.11+
- [Ollama](https://ollama.ai/) avec le modèle `llama3.2`
- Accès à l'API QALITAS (URL + credentials)

### Setup

```powershell
# Cloner le dépôt
git clone https://github.com/Neffati12/PFE-MultiAgents-QSE.git
cd PFE-MultiAgents-QSE

# Créer l'environnement virtuel
python -m venv .venv
.venv\Scripts\Activate.ps1

# Installer les dépendances
pip install -r requirements.txt

# Configurer les variables d'environnement
copy .env.example .env
# Éditer .env avec vos credentials QALITAS
```

### Configuration `.env`

```env
QALITAS_BASE_URL=https://timserver.northeurope.cloudapp.azure.com/QalitasDemo
QALITAS_USERNAME=votre_username
QALITAS_PASSWORD=votre_password
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

---

## Utilisation

```powershell
# Démarrer Ollama (dans un autre terminal)
ollama serve
ollama pull llama3.2

# Lancer le pipeline complet (dry-run)
python main_orchestrator.py

# Pipeline complet avec injection réelle dans QALITAS
python main_orchestrator.py --real

# Agent individuel
python main_orchestrator.py --agent 1
python main_orchestrator.py --agent 2
python main_orchestrator.py --agent 3
python main_orchestrator.py --agent 4
```

---

## Structure du projet

```
PFE-MultiAgents-QSE/
├── main_orchestrator.py        # Point d'entrée principal
├── requirements.txt            # Dépendances Python
├── .env.example                # Template configuration
│
├── agents/                     # Les 4 agents du pipeline
│   ├── agent1_identification.py
│   ├── agent2_evaluation.py
│   ├── agent2_llm.py
│   ├── agent3_main.py
│   ├── agent3_treatment.py
│   ├── agent4_monitoring.py
│   ├── agent4_llm.py
│   └── agent4_efficacy.py
│
├── orchestrator/               # LangGraph orchestration
│   ├── graph.py                # Définition du graphe d'états
│   └── state.py                # PipelineState partagé
│
└── loaders/                    # Chargement des données
```

---

## Résultats de validation

| Indicateur | Valeur |
|---|---|
| Risques traités | 373 |
| Actions injectées | 133 (35,7%) |
| Alertes critiques Agent 4 | 4 |
| Taux de déduplication | 0 doublon |
| Temps d'exécution complet | < 8 minutes |

---

## Technologies

| Composant | Technologie |
|---|---|
| Orchestration agents | LangGraph + LangChain |
| LLM local | Ollama / llama3.2 |
| API QALITAS | REST (Bearer Token) |
| Langage | Python 3.11 |
| Déduplication | Cache SHA1 |

---

## Conformité

Ce projet est développé conformément au cahier des charges `TBR.TIM.IA.S5/2025-2026` de TIM Tunisie, avec connexion API QALITAS réelle démontrée en environnement de test (`QalitasDemo`).
