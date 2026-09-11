# Agent IA de veille médicale en temps réel — version corrigée

Pipeline de collecte directe via API publiques ouvertes (PubMed / NCBI Entrez &
ClinicalTrials.gov v2), enrichi par un agent IA (Gemini) et restitué dans un
tableau de bord Streamlit.

## Ce qui a été corrigé par rapport à la version initiale

| Problème identifié | Correction |
|---|---|
| SDK `google-generativeai` **déprécié depuis le 30/11/2025** | Migration vers le nouveau SDK officiel `google-genai` (`from google import genai`) |
| Modèle `gemini-1.5-flash` obsolète | Remplacé par `gemini-2.5-flash` (configurable via `GEMINI_MODEL`) |
| `config.SEARCH_TOPICS` défini mais jamais utilisé (sujets codés en dur dans `extract.py`) | `extract.py` boucle maintenant sur `SEARCH_TOPICS` et sur une nouvelle liste `CLINICAL_TRIAL_CONDITIONS` |
| Aucun timeout / retry sur les appels réseau (risque de blocage indéfini) | Ajout de timeout, 3 tentatives avec backoff progressif |
| Appels NCBI sans identification (`tool`/`email`) → risque de limitation/blocage par NCBI | Ajout des paramètres `tool` et `email` recommandés par NCBI, support d'une clé `NCBI_API_KEY` optionnelle |
| Pas de logging | Logging structuré sur tous les modules |
| `init_db()` sans gestion propre de connexion | Context manager SQLite, ajout d'index sur `categorie`/`source` |
| Pas de validation de la réponse JSON de l'IA | Validation des champs attendus + repli automatique si un champ manque ou si l'IA échoue |
| Tableau de bord limité (2 graphiques, pas de filtre par score) | KPIs enrichis (4 indicateurs clés), distribution des scores d'impact, filtre par score minimum, message d'alerte si clé API absente |

## Installation

```bash
pip install -r requirements.txt
```

## Configuration (variables d'environnement)

| Variable | Obligatoire | Rôle |
|---|---|---|
| `GEMINI_API_KEY` | Non (mode dégradé sinon) | Active l'analyse IA (catégorisation, résumé, score) |
| `GEMINI_MODEL` | Non | Modèle Gemini à utiliser (défaut : `gemini-2.5-flash`) |
| `NCBI_CONTACT_EMAIL` | Non | Email de contact transmis à l'API NCBI (bonne pratique) |
| `NCBI_API_KEY` | Non | Augmente la limite de requêtes/seconde auprès de NCBI |

Sans `GEMINI_API_KEY`, le pipeline fonctionne en **mode dégradé** : les articles
sont tout de même collectés et stockés, avec un résumé tronqué à la place de
l'analyse IA (aucune erreur, aucun blocage).

## Lancer le tableau de bord

```bash
export GEMINI_API_KEY="votre_clé"   # optionnel
streamlit run main.py
```

Cliquez sur **⚡ Interroger les sources en temps réel** dans le panneau latéral
pour déclencher une collecte.

## Lancer le pipeline seul (sans interface, ex. pour une tâche planifiée)

```bash
python pipeline.py
```

Exemple de tâche cron (toutes les 6 heures) :
```
0 */6 * * * cd /chemin/vers/le/projet && /usr/bin/python3 pipeline.py >> logs.txt 2>&1
```

## Personnaliser les sujets suivis

Éditez `config.py` :

```python
SEARCH_TOPICS = [
    "artificial intelligence medicine",
    "oncology research",
    "telemedicine health data",
]

CLINICAL_TRIAL_CONDITIONS = [
    "oncology",
    "cardiovascular disease",
    "diabetes",
]
```

## Structure du projet

```
config.py       # sujets de veille, clés API, paramètres réseau
extract.py      # collecte PubMed + ClinicalTrials.gov (timeout, retries)
transform.py    # agent IA (Gemini via google-genai) : catégorisation, résumé, score
load.py         # stockage SQLite avec déduplication automatique
pipeline.py     # orchestrateur Extract -> Transform -> Load
main.py         # tableau de bord Streamlit
requirements.txt
```

## Limite connue de cet environnement de test

Les domaines `eutils.ncbi.nlm.nih.gov` et `clinicaltrials.gov` ne sont pas
joignables depuis le sandbox utilisé pour développer ce correctif (réseau
restreint). La logique d'extraction, de transformation et de chargement a
été validée de bout en bout avec des données simulées reproduisant le format
exact des API réelles. Sur votre machine, avec un accès internet normal,
le pipeline interrogera directement PubMed et ClinicalTrials.gov.
