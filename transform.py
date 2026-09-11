"""
Étape TRANSFORM : agent IA de qualification des signaux, propulsé par Claude.

Chaque publication ou essai collecté est catégorisé, résumé, scoré en impact
scientifique et en pertinence d'investissement, et rattaché à une pathologie du
catalogue à risque élevé.

Deux optimisations de coût, sans perte de qualité :
  * les signaux sont analysés par lots (un appel couvre plusieurs éléments) ;
  * cette tâche de qualification de masse tourne sur un modèle secondaire à
    effort réduit — les analyses de fond (pathologies, investissement,
    concurrence) restent sur le modèle principal.

Sans clé API, un repli neutre est renvoyé : le pipeline collecte et stocke quand
même, avec un résumé tronqué à la place de l'analyse.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

from claude_client import call_structured, is_enabled
from config import (
    CATEGORIES,
    CLAUDE_EFFORT_BULK,
    CLAUDE_FAST_MODEL,
    HIGH_RISK_PATHOLOGIES,
    MAX_WORKERS,
)

logger = logging.getLogger("veille.transform")

# Nombre de signaux qualifiés par appel API
BATCH_SIZE = 8

_PATHOLOGY_LABELS = [p["label"] for p in HIGH_RISK_PATHOLOGIES]

_SYSTEM = (
    "Tu es un analyste de veille biomédicale. Tu qualifies des publications "
    "scientifiques et des essais cliniques collectés en temps réel sur PubMed et "
    "ClinicalTrials.gov. Tu es factuel et sobre : tu ne surestimes pas la portée "
    "d'un résultat préliminaire, et tu n'inventes aucune donnée absente du texte "
    "fourni. Tu réponds en français."
)

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "index": {
            "type": "integer",
            "description": "Index de l'élément analysé, repris tel quel depuis l'entrée.",
        },
        "categorie": {"type": "string", "enum": CATEGORIES},
        "resume_ia": {
            "type": "string",
            "description": "Synthèse en deux phrases de l'intérêt médical concret.",
        },
        "impact_score": {
            "type": "integer",
            "description": "Pertinence scientifique : 1 = anecdotique, 5 = potentiellement structurant.",
        },
        "investment_score": {
            "type": "integer",
            "description": "Pertinence pour une décision d'investissement : 1 = nulle, 5 = signal de marché fort.",
        },
        "mots_cles": {
            "type": "string",
            "description": "3 à 5 mots-clés séparés par des virgules.",
        },
        "pathologie_liee": {
            "type": "string",
            "enum": _PATHOLOGY_LABELS + ["Aucune"],
            "description": "Pathologie du catalogue à risque élevé concernée, ou 'Aucune'.",
        },
    },
    "required": [
        "index", "categorie", "resume_ia", "impact_score",
        "investment_score", "mots_cles", "pathologie_liee",
    ],
    "additionalProperties": False,
}

_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "analyses": {"type": "array", "items": _ITEM_SCHEMA},
    },
    "required": ["analyses"],
    "additionalProperties": False,
}

_FALLBACK = {
    "categorie": "Données de Santé & E-Santé",
    "resume_ia": "",
    "impact_score": 3,
    "investment_score": 2,
    "mots_cles": "Médecine, Temps réel",
    "pathologie_liee": "Aucune",
}


def _degraded(item: Dict, reason: str = "") -> Dict:
    """Repli neutre : la collecte continue même sans analyse IA."""
    summary = item.get("summary") or ""
    out = dict(_FALLBACK)
    if reason:
        out["resume_ia"] = f"Analyse IA indisponible ({reason}). Métadonnées : {summary[:140]}"
        out["impact_score"] = 1
        out["mots_cles"] = "Non analysé"
    else:
        out["resume_ia"] = (summary[:150] + "…") if len(summary) > 150 else summary
    return out


def _render_batch(items: List[Dict]) -> str:
    """Met en forme un lot de signaux pour l'invite."""
    lines = []
    for idx, item in enumerate(items):
        lines.append(
            f"--- Élément {idx} ---\n"
            f"Source : {item.get('source', 'inconnue')}\n"
            f"Sujet suivi : {item.get('topic', '—')}\n"
            f"Titre : {item.get('title', '')}\n"
            f"Détails : {item.get('summary', '')}"
        )
    return "\n\n".join(lines)


def _analyze_batch(items: List[Dict]) -> List[Dict]:
    """Qualifie un lot de signaux en un seul appel API."""
    prompt = (
        f"Qualifie les {len(items)} éléments ci-dessous. Renvoie exactement une "
        f"analyse par élément, en reprenant son index d'origine.\n\n"
        f"{_render_batch(items)}"
    )

    result = call_structured(
        prompt=prompt,
        schema=_BATCH_SCHEMA,
        system=_SYSTEM,
        model=CLAUDE_FAST_MODEL,
        effort=CLAUDE_EFFORT_BULK,
        max_tokens=8000,
    )

    if not result.ok:
        logger.error("Lot non analysé (%d éléments) : %s", len(items), result.error)
        return [_degraded(item, result.error or "erreur") for item in items]

    # Réassociation par index ; tout élément manquant retombe sur le repli
    by_index = {}
    for analysis in result.data.get("analyses", []):
        idx = analysis.get("index")
        if isinstance(idx, int) and 0 <= idx < len(items):
            by_index[idx] = analysis

    analyses = []
    for idx, item in enumerate(items):
        analysis = by_index.get(idx)
        if analysis is None:
            logger.warning("Analyse absente pour l'élément %d, repli appliqué", idx)
            analyses.append(_degraded(item))
            continue
        analysis.pop("index", None)
        analyses.append(analysis)
    return analyses


def analyze_signals(items: List[Dict]) -> List[Dict]:
    """
    Qualifie une liste de signaux et retourne les éléments enrichis.
    L'ordre d'entrée est préservé.
    """
    if not items:
        return []

    if not is_enabled():
        logger.warning("Aucune clé Claude : mode dégradé (résumés bruts, sans catégorisation IA).")
        return [{**item, **_degraded(item)} for item in items]

    batches = [items[i:i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]
    logger.info("Analyse IA de %d signaux en %d lot(s)", len(items), len(batches))

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(batches))) as pool:
        results = list(pool.map(_analyze_batch, batches))

    enriched = []
    for batch, analyses in zip(batches, results):
        for item, analysis in zip(batch, analyses):
            enriched.append({**item, **analysis})
    return enriched


def analyze_article_with_ia(title: str, summary: str, source: str = "PubMed") -> Dict:
    """
    Analyse d'un signal isolé — conservé pour compatibilité avec l'ancienne API.
    Préférez `analyze_signals()`, qui regroupe les appels et coûte bien moins cher.
    """
    item = {"title": title, "summary": summary, "source": source}
    return analyze_signals([item])[0] if is_enabled() else _degraded(item)
