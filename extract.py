"""
Étape EXTRACT : collecte temps réel sur trois sources publiques ouvertes.

  * PubMed / NCBI Entrez      -> littérature scientifique récente
  * ClinicalTrials.gov v2     -> essais cliniques ET interventions testées
                                 (c'est de là que sortent les traitements
                                 expérimentaux d'une pathologie)
  * openFDA `drug/label`      -> traitements déjà homologués (notices officielles)

Aucune clé n'est requise pour ces trois sources. Toutes les erreurs réseau sont
absorbées source par source : une source indisponible ne fait jamais tomber la
collecte des autres.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Tuple

from config import (
    CLINICAL_TRIALS_MAX_RESULTS_PER_CONDITION,
    CLINICAL_TRIAL_CONDITIONS,
    HIGH_RISK_PATHOLOGIES,
    MAX_RETRIES,
    MAX_WORKERS,
    NCBI_API_KEY,
    NCBI_CONTACT_EMAIL,
    NCBI_TOOL_NAME,
    OPENFDA_MAX_RESULTS_PER_CONDITION,
    PUBMED_MAX_RESULTS_PER_TOPIC,
    REQUEST_TIMEOUT,
    RETRY_BACKOFF_SECONDS,
    SEARCH_TOPICS,
)

logger = logging.getLogger("veille.extract")

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
CTGOV_BASE = "https://clinicaltrials.gov/api/v2/studies"
OPENFDA_LABEL = "https://api.fda.gov/drug/label.json"

# ---------------------------------------------------------------------------
# Limiteur de débit pour NCBI : 3 requêtes/seconde sans clé API, 10 avec.
# NCBI bloque les clients trop rapides ; le limiteur évite les 429.
# ---------------------------------------------------------------------------
_NCBI_MIN_INTERVAL = 0.11 if NCBI_API_KEY else 0.36
_ncbi_lock = threading.Lock()
_ncbi_last_call = [0.0]


def _ncbi_throttle() -> None:
    with _ncbi_lock:
        elapsed = time.monotonic() - _ncbi_last_call[0]
        if elapsed < _NCBI_MIN_INTERVAL:
            time.sleep(_NCBI_MIN_INTERVAL - elapsed)
        _ncbi_last_call[0] = time.monotonic()


def _http_get_json(url: str, throttle_ncbi: bool = False) -> dict:
    """GET JSON avec timeout et tentatives successives ; lève si tout échoue."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        if throttle_ncbi:
            _ncbi_throttle()
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": f"{NCBI_TOOL_NAME}/2.0", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                json.JSONDecodeError, OSError) as exc:
            last_error = exc
            logger.warning("Échec requête (%d/%d) %s : %s", attempt, MAX_RETRIES, url[:110], exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"Échec définitif après {MAX_RETRIES} tentatives : {last_error}")


def _strip_html(text: str) -> str:
    """Retire le balisage que PubMed insère parfois dans les titres."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


# ---------------------------------------------------------------------------
# Normalisation ClinicalTrials.gov
# ---------------------------------------------------------------------------
_PHASE_LABELS = {
    "EARLY_PHASE1": "Phase 1 précoce",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "Sans phase",
}

_STATUS_LABELS = {
    "RECRUITING": "Recrutement en cours",
    "NOT_YET_RECRUITING": "Pas encore ouvert",
    "ACTIVE_NOT_RECRUITING": "En cours, sans recrutement",
    "ENROLLING_BY_INVITATION": "Inclusion sur invitation",
    "COMPLETED": "Terminé",
    "SUSPENDED": "Suspendu",
    "TERMINATED": "Arrêté",
    "WITHDRAWN": "Retiré",
    "UNKNOWN": "Statut inconnu",
}

_INTERVENTION_TYPES = {
    "DRUG": "Médicament",
    "BIOLOGICAL": "Biologique",
    "DEVICE": "Dispositif médical",
    "PROCEDURE": "Procédure",
    "RADIATION": "Radiothérapie",
    "GENETIC": "Thérapie génique / cellulaire",
    "DIAGNOSTIC_TEST": "Test diagnostique",
    "DIETARY_SUPPLEMENT": "Complément alimentaire",
    "BEHAVIORAL": "Intervention comportementale",
    "COMBINATION_PRODUCT": "Produit combiné",
    "OTHER": "Autre",
}

# Types d'interventions retenus comme « traitements » (on exclut le diagnostic pur)
_THERAPEUTIC_TYPES = {
    "DRUG", "BIOLOGICAL", "DEVICE", "PROCEDURE",
    "RADIATION", "GENETIC", "COMBINATION_PRODUCT",
}


def _phase_label(phases: List[str]) -> str:
    if not phases:
        return "Sans phase"
    return " / ".join(_PHASE_LABELS.get(p, p.title()) for p in phases)


def _phase_rank(phases: List[str]) -> int:
    """Rang numérique de la phase la plus avancée (0 = sans phase, 4 = phase 4)."""
    order = {"EARLY_PHASE1": 1, "PHASE1": 1, "PHASE2": 2, "PHASE3": 3, "PHASE4": 4}
    return max((order.get(p, 0) for p in phases), default=0)


def _evidence_level(phase_rank: int, status: str) -> str:
    """Niveau de preuve lisible, déduit de la phase et du statut de l'essai."""
    if phase_rank >= 4:
        return "Post-homologation"
    if phase_rank == 3:
        return "Essai avancé (phase 3)"
    if phase_rank == 2:
        return "Essai intermédiaire (phase 2)"
    if phase_rank == 1:
        return "Essai précoce (phase 1)"
    return "Exploratoire" if status != "COMPLETED" else "Exploratoire (terminé)"


def _fetch_ctgov(condition: str, max_results: int) -> List[dict]:
    """Requête brute ClinicalTrials.gov v2, triée par mise à jour la plus récente."""
    params = {
        "query.cond": condition,
        "pageSize": str(max_results),
        "sort": "LastUpdatePostDate:desc",
    }
    data = _http_get_json(f"{CTGOV_BASE}?{urllib.parse.urlencode(params)}")
    return data.get("studies", []) or []


def _parse_study(study: dict, condition: str) -> dict:
    """Aplatit un essai ClinicalTrials.gov en un enregistrement exploitable."""
    protocol = study.get("protocolSection", {}) or {}
    ident = protocol.get("identificationModule", {}) or {}
    status_mod = protocol.get("statusModule", {}) or {}
    design = protocol.get("designModule", {}) or {}
    sponsors = protocol.get("sponsorCollaboratorsModule", {}) or {}
    arms = protocol.get("armsInterventionsModule", {}) or {}
    conditions = (protocol.get("conditionsModule", {}) or {}).get("conditions", []) or []

    nct_id = ident.get("nctId", "")
    phases = design.get("phases", []) or []
    status_raw = status_mod.get("overallStatus", "UNKNOWN")
    lead = (sponsors.get("leadSponsor", {}) or {})
    enrollment = (design.get("enrollmentInfo", {}) or {}).get("count")

    return {
        "nct_id": nct_id,
        "title": ident.get("briefTitle", "Essai clinique"),
        "link": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else "",
        "status_raw": status_raw,
        "status": _STATUS_LABELS.get(status_raw, status_raw.replace("_", " ").title()),
        "phases_raw": phases,
        "phase": _phase_label(phases),
        "phase_rank": _phase_rank(phases),
        "start_date": (status_mod.get("startDateStruct", {}) or {}).get("date", ""),
        "last_update": (status_mod.get("lastUpdatePostDateStruct", {}) or {}).get("date", ""),
        "sponsor": lead.get("name", "Promoteur non renseigné"),
        "sponsor_class": lead.get("class", ""),
        "enrollment": enrollment,
        "conditions": conditions,
        "interventions": arms.get("interventions", []) or [],
        "condition_query": condition,
    }


# ---------------------------------------------------------------------------
# PubMed
# ---------------------------------------------------------------------------
def fetch_pubmed_latest(topic: str, max_results: int = 6) -> List[Dict]:
    """Extraction temps réel depuis PubMed via l'API Entrez (esearch + esummary)."""
    common = {"tool": NCBI_TOOL_NAME, "email": NCBI_CONTACT_EMAIL}
    if NCBI_API_KEY:
        common["api_key"] = NCBI_API_KEY

    search_params = {
        "db": "pubmed", "term": topic, "retmode": "json",
        "retmax": str(max_results), "sort": "pub_date", **common,
    }

    try:
        data = _http_get_json(
            f"{EUTILS_BASE}esearch.fcgi?{urllib.parse.urlencode(search_params)}",
            throttle_ncbi=True,
        )
        id_list = (data.get("esearchresult", {}) or {}).get("idlist", []) or []
        if not id_list:
            logger.info("Aucun résultat PubMed pour '%s'", topic)
            return []

        summary_params = {"db": "pubmed", "id": ",".join(id_list), "retmode": "json", **common}
        sum_data = _http_get_json(
            f"{EUTILS_BASE}esummary.fcgi?{urllib.parse.urlencode(summary_params)}",
            throttle_ncbi=True,
        ).get("result", {}) or {}

        articles = []
        for pmid in id_list:
            art = sum_data.get(pmid) or {}
            if not art:
                continue
            authors = art.get("authors", []) or []
            author_names = ", ".join(a.get("name", "") for a in authors[:6] if a.get("name"))
            journal = art.get("fulljournalname") or art.get("source") or ""
            details = " · ".join(x for x in [journal, f"Auteurs : {author_names}" if author_names else ""] if x)
            articles.append({
                "title": _strip_html(art.get("title", "")) or "Titre non disponible",
                "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "published": art.get("pubdate", ""),
                "summary": details or "Métadonnées non renseignées",
                "source": "PubMed",
                "topic": topic,
            })
        logger.info("PubMed '%s' : %d article(s)", topic, len(articles))
        return articles

    except Exception as exc:
        logger.error("Erreur extraction PubMed pour '%s' : %s", topic, exc)
        return []


# ---------------------------------------------------------------------------
# ClinicalTrials.gov — fil de signaux
# ---------------------------------------------------------------------------
def fetch_clinical_trials(condition: str, max_results: int = 8) -> List[Dict]:
    """Essais cliniques récents d'une condition, formatés pour le fil de veille."""
    try:
        studies = [_parse_study(s, condition) for s in _fetch_ctgov(condition, max_results)]
        trials = []
        for s in studies:
            treatments = ", ".join(
                i.get("name", "") for i in s["interventions"][:4] if i.get("name")
            )
            trials.append({
                "title": f"[{s['phase']} · {s['status']}] {s['title']}",
                "link": s["link"],
                "published": s["last_update"] or s["start_date"],
                "summary": (
                    f"NCT : {s['nct_id']} | Promoteur : {s['sponsor']} | "
                    f"Phase : {s['phase']} | Statut : {s['status']}"
                    + (f" | Interventions : {treatments}" if treatments else "")
                ),
                "source": "ClinicalTrials.gov",
                "topic": condition,
            })
        logger.info("ClinicalTrials.gov '%s' : %d essai(s)", condition, len(trials))
        return trials
    except Exception as exc:
        logger.error("Erreur extraction ClinicalTrials.gov pour '%s' : %s", condition, exc)
        return []


def collect_all_realtime_data() -> List[Dict]:
    """Fil de signaux : littérature PubMed + essais cliniques, collectés en parallèle."""
    results: List[Dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        pubmed_jobs = [
            pool.submit(fetch_pubmed_latest, topic, PUBMED_MAX_RESULTS_PER_TOPIC)
            for topic in SEARCH_TOPICS
        ]
        trial_jobs = [
            pool.submit(fetch_clinical_trials, cond, CLINICAL_TRIALS_MAX_RESULTS_PER_CONDITION // 2)
            for cond in CLINICAL_TRIAL_CONDITIONS
        ]
        for job in pubmed_jobs + trial_jobs:
            results.extend(job.result())

    logger.info("Collecte de signaux : %d élément(s)", len(results))
    return results


# ---------------------------------------------------------------------------
# openFDA — traitements homologués
# ---------------------------------------------------------------------------
def fetch_fda_treatments(patho: dict, max_results: int = 5) -> List[Dict]:
    """
    Traitements déjà homologués pour une pathologie, via les notices openFDA.
    Sert de socle « standard de soin » face aux traitements expérimentaux.
    """
    query = patho.get("drug_query") or patho.get("condition", "")
    params = {"search": f'indications_and_usage:"{query}"', "limit": str(max_results)}

    try:
        data = _http_get_json(f"{OPENFDA_LABEL}?{urllib.parse.urlencode(params)}")
    except Exception as exc:
        # openFDA renvoie un 404 quand la recherche ne matche rien : cas normal
        logger.info("openFDA sans résultat exploitable pour '%s' (%s)", query, exc)
        return []

    treatments = []
    for res in data.get("results", []) or []:
        of = res.get("openfda", {}) or {}
        names = of.get("brand_name") or of.get("generic_name") or []
        if not names:
            continue
        name = str(names[0]).strip().title()
        drug_class = (of.get("pharm_class_epc") or ["Classe non renseignée"])[0]
        route = ", ".join(of.get("route") or []) or "Voie non renseignée"
        indication = (res.get("indications_and_usage") or [""])[0]
        spl_id = res.get("id", "")

        treatments.append({
            "pathology_code": patho["code"],
            "name": name,
            "modality": _INTERVENTION_TYPES["DRUG"],
            "drug_class": drug_class,
            "phase": "Homologué",
            "phase_rank": 5,
            "status": f"Sur le marché ({route})",
            "sponsor": ", ".join(of.get("manufacturer_name") or []) or "Laboratoire non renseigné",
            "source": "openFDA",
            "source_ref": spl_id,
            "link": (
                f"https://labels.fda.gov/{spl_id}" if spl_id
                else "https://open.fda.gov/apis/drug/label/"
            ),
            "evidence_level": "Homologué (notice FDA)",
            "is_emerging": 0,
            "detail": re.sub(r"\s+", " ", indication)[:600],
            "last_update": str(res.get("effective_time", "")),
        })

    logger.info("openFDA '%s' : %d traitement(s) homologué(s)", query, len(treatments))
    return treatments


# ---------------------------------------------------------------------------
# Extraction des traitements expérimentaux depuis les essais
# ---------------------------------------------------------------------------
def _treatments_from_trials(patho: dict, studies: List[dict]) -> List[Dict]:
    """
    Déplie les interventions thérapeutiques des essais en traitements distincts,
    en gardant pour chaque traitement l'essai le plus avancé qui le teste.
    """
    best: Dict[str, Dict] = {}

    for s in studies:
        for iv in s["interventions"]:
            iv_type = (iv.get("type") or "OTHER").upper()
            if iv_type not in _THERAPEUTIC_TYPES:
                continue
            name = (iv.get("name") or "").strip()
            if not name:
                continue

            key = name.lower()
            candidate = {
                "pathology_code": patho["code"],
                "name": name[:200],
                "modality": _INTERVENTION_TYPES.get(iv_type, "Autre"),
                "drug_class": ", ".join(iv.get("otherNames") or [])[:200] or "—",
                "phase": s["phase"],
                "phase_rank": s["phase_rank"],
                "status": s["status"],
                "sponsor": s["sponsor"],
                "source": "ClinicalTrials.gov",
                "source_ref": s["nct_id"],
                "link": s["link"],
                "evidence_level": _evidence_level(s["phase_rank"], s["status_raw"]),
                # Un traitement est « émergent » s'il n'a pas dépassé la phase 2
                "is_emerging": 1 if s["phase_rank"] <= 2 else 0,
                "detail": re.sub(r"\s+", " ", iv.get("description") or "")[:600],
                "last_update": s["last_update"],
            }
            previous = best.get(key)
            if previous is None or candidate["phase_rank"] > previous["phase_rank"]:
                best[key] = candidate

    return sorted(best.values(), key=lambda t: (-t["phase_rank"], t["name"]))


def collect_pathology_evidence(patho: dict) -> Dict:
    """
    Dossier de preuves temps réel d'une pathologie à risque élevé :
    essais en cours, traitements expérimentaux, traitements homologués,
    et littérature récente.
    """
    code = patho["code"]
    trials: List[dict] = []
    try:
        trials = [
            _parse_study(s, patho["condition"])
            for s in _fetch_ctgov(patho["condition"], CLINICAL_TRIALS_MAX_RESULTS_PER_CONDITION)
        ]
    except Exception as exc:
        logger.error("Essais indisponibles pour %s : %s", code, exc)

    treatments = _treatments_from_trials(patho, trials)
    treatments += fetch_fda_treatments(patho, OPENFDA_MAX_RESULTS_PER_CONDITION)

    literature = fetch_pubmed_latest(patho["pubmed"], 4)

    active_statuses = {"RECRUITING", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING",
                       "ENROLLING_BY_INVITATION"}
    logger.info(
        "%s : %d essai(s), %d traitement(s), %d publication(s)",
        code, len(trials), len(treatments), len(literature),
    )

    return {
        "pathology": patho,
        "trials": trials,
        "treatments": treatments,
        "literature": literature,
        "metrics": {
            "trials_total": len(trials),
            "trials_active": sum(1 for t in trials if t["status_raw"] in active_statuses),
            "trials_late_phase": sum(1 for t in trials if t["phase_rank"] >= 3),
            "treatments_total": len(treatments),
            "treatments_emerging": sum(1 for t in treatments if t["is_emerging"]),
            "treatments_approved": sum(1 for t in treatments if t["source"] == "openFDA"),
            "industry_sponsored": sum(1 for t in trials if t["sponsor_class"] == "INDUSTRY"),
        },
    }


def collect_all_pathology_evidence(pathologies: List[dict] | None = None) -> List[Dict]:
    """Dossiers de preuves pour tout le catalogue (ou un sous-ensemble), en parallèle."""
    catalog = pathologies if pathologies is not None else HIGH_RISK_PATHOLOGIES
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(catalog)))) as pool:
        dossiers = list(pool.map(collect_pathology_evidence, catalog))
    logger.info("Dossiers pathologies constitués : %d", len(dossiers))
    return dossiers
