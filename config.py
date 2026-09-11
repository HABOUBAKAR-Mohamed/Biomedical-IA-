"""
Configuration centrale de la plateforme de veille biomédicale temps réel.

Tout est pilotable par variables d'environnement (ou par un fichier `.env`
placé à la racine du projet — voir `.env.example`).
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Chargement optionnel d'un fichier .env (sans dépendance externe)
# ---------------------------------------------------------------------------
def _load_dotenv(path: str = ".env") -> None:
    """Charge les paires CLE=VALEUR d'un fichier .env sans écraser l'environnement."""
    env_file = Path(__file__).resolve().parent / path
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# ---------------------------------------------------------------------------
# Base de données
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("DB_PATH", "veille_sante.db")

# ---------------------------------------------------------------------------
# IA — Claude (Anthropic). Remplace l'ancienne intégration Google Gemini.
# La clé n'est JAMAIS codée en dur : elle vient de l'environnement, d'un
# fichier .env, ou est saisie directement dans le panneau latéral du dashboard.
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Modèle principal (analyse, plan d'investissement, veille concurrentielle)
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5")

# Modèle secondaire pour le scoring de masse des signaux (volume élevé, tâche simple)
CLAUDE_FAST_MODEL = os.getenv("CLAUDE_FAST_MODEL", "claude-opus-5")

# Niveau d'effort de raisonnement : low | medium | high | xhigh | max
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "high")
CLAUDE_EFFORT_BULK = os.getenv("CLAUDE_EFFORT_BULK", "low")

# Nombre maximum de recherches web autorisées par analyse temps réel
WEB_SEARCH_MAX_USES = int(os.getenv("WEB_SEARCH_MAX_USES", "8"))

# ---------------------------------------------------------------------------
# Sujets de veille (PubMed)
# ---------------------------------------------------------------------------
SEARCH_TOPICS = [
    "artificial intelligence medicine",
    "machine learning clinical decision support",
    "oncology precision medicine",
    "telemedicine health data",
    "foundation models healthcare",
]

PUBMED_MAX_RESULTS_PER_TOPIC = int(os.getenv("PUBMED_MAX_RESULTS_PER_TOPIC", "6"))

# ---------------------------------------------------------------------------
# Catalogue des pathologies à risque élevé
#
# Sélection fondée sur la charge de mortalité mondiale (principales causes de
# décès OMS) complétée par des pathologies à létalité ou morbidité très élevée
# et à fort besoin médical non couvert.
#
# Chaque entrée pilote l'extraction temps réel :
#   - `condition`  -> requête ClinicalTrials.gov (essais + traitements testés)
#   - `pubmed`     -> requête PubMed (littérature récente)
#   - `drug_query` -> requête openFDA (traitements homologués / notices)
# ---------------------------------------------------------------------------
HIGH_RISK_PATHOLOGIES = [
    {
        "code": "ISCHEMIC_HEART",
        "label": "Cardiopathie ischémique",
        "family": "Cardiovasculaire",
        "condition": "ischemic heart disease",
        "pubmed": "ischemic heart disease treatment",
        "drug_query": "coronary artery disease",
        "baseline_tier": "Critique",
        "note": "Première cause de mortalité mondiale.",
    },
    {
        "code": "STROKE",
        "label": "Accident vasculaire cérébral",
        "family": "Cardiovasculaire",
        "condition": "stroke",
        "pubmed": "acute ischemic stroke thrombectomy",
        "drug_query": "ischemic stroke",
        "baseline_tier": "Critique",
        "note": "Deuxième cause de mortalité et première cause de handicap acquis.",
    },
    {
        "code": "HEART_FAILURE",
        "label": "Insuffisance cardiaque",
        "family": "Cardiovasculaire",
        "condition": "heart failure",
        "pubmed": "heart failure reduced ejection fraction therapy",
        "drug_query": "heart failure",
        "baseline_tier": "Très élevé",
        "note": "Pronostic à 5 ans comparable à de nombreux cancers.",
    },
    {
        "code": "COPD",
        "label": "BPCO",
        "family": "Respiratoire",
        "condition": "chronic obstructive pulmonary disease",
        "pubmed": "COPD exacerbation management",
        "drug_query": "chronic obstructive pulmonary disease",
        "baseline_tier": "Très élevé",
        "note": "Troisième cause de mortalité mondiale, sous-diagnostiquée.",
    },
    {
        "code": "LUNG_CANCER",
        "label": "Cancer du poumon",
        "family": "Oncologie",
        "condition": "lung cancer",
        "pubmed": "non-small cell lung cancer immunotherapy",
        "drug_query": "non-small cell lung cancer",
        "baseline_tier": "Critique",
        "note": "Cancer le plus meurtrier ; dépistage précoce déterminant.",
    },
    {
        "code": "PANCREATIC_CANCER",
        "label": "Cancer du pancréas",
        "family": "Oncologie",
        "condition": "pancreatic cancer",
        "pubmed": "pancreatic ductal adenocarcinoma therapy",
        "drug_query": "pancreatic cancer",
        "baseline_tier": "Critique",
        "note": "Survie à 5 ans parmi les plus faibles toutes tumeurs confondues.",
    },
    {
        "code": "GLIOBLASTOMA",
        "label": "Glioblastome",
        "family": "Oncologie",
        "condition": "glioblastoma",
        "pubmed": "glioblastoma treatment trial",
        "drug_query": "glioblastoma",
        "baseline_tier": "Critique",
        "note": "Récidive quasi systématique, médiane de survie très courte.",
    },
    {
        "code": "BREAST_CANCER",
        "label": "Cancer du sein",
        "family": "Oncologie",
        "condition": "breast cancer",
        "pubmed": "breast cancer targeted therapy",
        "drug_query": "breast cancer",
        "baseline_tier": "Élevé",
        "note": "Cancer féminin le plus fréquent ; pronostic très dépendant du stade.",
    },
    {
        "code": "COLORECTAL_CANCER",
        "label": "Cancer colorectal",
        "family": "Oncologie",
        "condition": "colorectal cancer",
        "pubmed": "colorectal cancer screening treatment",
        "drug_query": "colorectal cancer",
        "baseline_tier": "Très élevé",
        "note": "Incidence en hausse chez les moins de 50 ans.",
    },
    {
        "code": "SEPSIS",
        "label": "Sepsis",
        "family": "Réanimation / Infectieux",
        "condition": "sepsis",
        "pubmed": "sepsis early detection machine learning",
        "drug_query": "septic shock",
        "baseline_tier": "Critique",
        "note": "Mortalité fortement dépendante du délai de reconnaissance.",
    },
    {
        "code": "AMR",
        "label": "Résistance antimicrobienne",
        "family": "Réanimation / Infectieux",
        "condition": "antimicrobial resistance",
        "pubmed": "multidrug resistant bacterial infection",
        "drug_query": "bacterial infection",
        "baseline_tier": "Critique",
        "note": "Menace sanitaire systémique, pipeline antibiotique appauvri.",
    },
    {
        "code": "TUBERCULOSIS",
        "label": "Tuberculose",
        "family": "Réanimation / Infectieux",
        "condition": "tuberculosis",
        "pubmed": "drug resistant tuberculosis regimen",
        "drug_query": "tuberculosis",
        "baseline_tier": "Très élevé",
        "note": "Première cause de décès par agent infectieux unique.",
    },
    {
        "code": "HIV",
        "label": "VIH / SIDA",
        "family": "Réanimation / Infectieux",
        "condition": "HIV infections",
        "pubmed": "HIV long acting antiretroviral",
        "drug_query": "HIV-1 infection",
        "baseline_tier": "Élevé",
        "note": "Contrôlable mais accès aux traitements très inégal.",
    },
    {
        "code": "MALARIA",
        "label": "Paludisme",
        "family": "Réanimation / Infectieux",
        "condition": "malaria",
        "pubmed": "malaria artemisinin resistance vaccine",
        "drug_query": "malaria",
        "baseline_tier": "Très élevé",
        "note": "Mortalité pédiatrique élevée, résistances émergentes.",
    },
    {
        "code": "T2_DIABETES",
        "label": "Diabète de type 2",
        "family": "Métabolique / Rénal",
        "condition": "type 2 diabetes",
        "pubmed": "type 2 diabetes GLP-1 outcomes",
        "drug_query": "type 2 diabetes mellitus",
        "baseline_tier": "Élevé",
        "note": "Amplificateur de risque cardiovasculaire et rénal.",
    },
    {
        "code": "CKD",
        "label": "Maladie rénale chronique",
        "family": "Métabolique / Rénal",
        "condition": "chronic kidney disease",
        "pubmed": "chronic kidney disease progression therapy",
        "drug_query": "chronic kidney disease",
        "baseline_tier": "Très élevé",
        "note": "Progression silencieuse jusqu'au stade terminal.",
    },
    {
        "code": "ALZHEIMER",
        "label": "Maladie d'Alzheimer et démences",
        "family": "Neurologie",
        "condition": "Alzheimer disease",
        "pubmed": "Alzheimer disease anti-amyloid biomarker",
        "drug_query": "Alzheimer's disease",
        "baseline_tier": "Très élevé",
        "note": "Diagnostic tardif ; nouveaux biomarqueurs sanguins en rupture.",
    },
    {
        "code": "ALS",
        "label": "Sclérose latérale amyotrophique",
        "family": "Neurologie",
        "condition": "amyotrophic lateral sclerosis",
        "pubmed": "amyotrophic lateral sclerosis therapy trial",
        "drug_query": "amyotrophic lateral sclerosis",
        "baseline_tier": "Critique",
        "note": "Évolution rapide, arsenal thérapeutique très limité.",
    },
    {
        "code": "IPF",
        "label": "Fibrose pulmonaire idiopathique",
        "family": "Respiratoire",
        "condition": "idiopathic pulmonary fibrosis",
        "pubmed": "idiopathic pulmonary fibrosis antifibrotic",
        "drug_query": "idiopathic pulmonary fibrosis",
        "baseline_tier": "Très élevé",
        "note": "Médiane de survie de l'ordre de 3 à 5 ans après diagnostic.",
    },
    {
        "code": "SICKLE_CELL",
        "label": "Drépanocytose",
        "family": "Hématologie",
        "condition": "sickle cell disease",
        "pubmed": "sickle cell disease gene therapy",
        "drug_query": "sickle cell disease",
        "baseline_tier": "Élevé",
        "note": "Première indication des thérapies géniques CRISPR approuvées.",
    },
]

CLINICAL_TRIALS_MAX_RESULTS_PER_CONDITION = int(
    os.getenv("CLINICAL_TRIALS_MAX_RESULTS_PER_CONDITION", "12")
)
OPENFDA_MAX_RESULTS_PER_CONDITION = int(os.getenv("OPENFDA_MAX_RESULTS_PER_CONDITION", "5"))

# Conditions ClinicalTrials.gov suivies pour le fil de signaux généraliste
CLINICAL_TRIAL_CONDITIONS = [p["condition"] for p in HIGH_RISK_PATHOLOGIES[:6]]

# ---------------------------------------------------------------------------
# Niveaux de risque — ordonnés du plus faible au plus fort.
# Les couleurs viennent de la palette de statut (voir theme.py) et sont
# toujours accompagnées d'une icône et d'un libellé (jamais la couleur seule).
# ---------------------------------------------------------------------------
RISK_TIERS = ["Modéré", "Élevé", "Très élevé", "Critique"]

# ---------------------------------------------------------------------------
# Segments retenus pour le plan d'investissement
# ---------------------------------------------------------------------------
INVESTMENT_SEGMENTS = [
    "Imagerie & radiologie augmentée",
    "Découverte de médicaments par IA",
    "Agents cliniques & documentation",
    "Diagnostic précoce & biomarqueurs",
    "Médecine de précision en oncologie",
    "Essais cliniques augmentés",
    "Surveillance épidémiologique",
]

# Horizons proposés pour le plan
INVESTMENT_HORIZONS = ["12 mois", "3 ans", "5 ans"]

# ---------------------------------------------------------------------------
# Veille concurrentielle — amorces de recherche.
# Ce ne sont PAS des données figées : la liste sert de point de départ à la
# recherche web temps réel, qui doit confirmer, corriger et compléter.
# ---------------------------------------------------------------------------
COMPETITOR_SEED_QUERIES = [
    "biomedical AI agent startup funding",
    "clinical AI agent FDA clearance",
    "AI drug discovery company pipeline milestone",
    "medical foundation model release",
    "AI scribe ambient clinical documentation market",
    "autonomous clinical trial matching AI",
]

COMPETITOR_SEGMENTS = INVESTMENT_SEGMENTS

# ---------------------------------------------------------------------------
# Catégories utilisées pour classer les publications par l'agent IA
# ---------------------------------------------------------------------------
CATEGORIES = [
    "IA & Imagerie Médicale",
    "Essais Cliniques",
    "Oncologie & Recherche",
    "Données de Santé & E-Santé",
    "Agents IA Cliniques",
    "Découverte de Médicaments",
]

# ---------------------------------------------------------------------------
# NCBI Entrez — bonnes pratiques d'usage de l'API (évite le rate-limiting)
# Voir : https://www.ncbi.nlm.nih.gov/books/NBK25497/
# ---------------------------------------------------------------------------
NCBI_TOOL_NAME = "veille_sante_ia"
NCBI_CONTACT_EMAIL = os.getenv("NCBI_CONTACT_EMAIL", "veille@example.com")
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")  # optionnel, augmente le quota req/s

# ---------------------------------------------------------------------------
# Réseau / robustesse
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = 2

# Parallélisme des appels réseau et des analyses IA
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "6"))

# ---------------------------------------------------------------------------
# Temps réel — fraîcheur des données
# ---------------------------------------------------------------------------
# Au-delà de ce délai, une section du dashboard est signalée comme « à rafraîchir »
STALE_AFTER_MINUTES = int(os.getenv("STALE_AFTER_MINUTES", "30"))

# Intervalle de rafraîchissement automatique du dashboard (secondes, 0 = désactivé)
AUTO_REFRESH_SECONDS = int(os.getenv("AUTO_REFRESH_SECONDS", "60"))


def pathology_by_code(code: str) -> dict:
    """Retourne l'entrée du catalogue correspondant au code, ou {} si inconnue."""
    for patho in HIGH_RISK_PATHOLOGIES:
        if patho["code"] == code:
            return patho
    return {}
