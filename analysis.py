"""
Analyses de fond conduites par Claude sur le modèle principal.

Trois livrables, tous adossés aux données réellement collectées :

  1. `analyze_pathology()`   — niveau de risque, standard de soin, thérapies
                               émergentes et besoin non couvert d'une pathologie,
                               à partir de son dossier de preuves temps réel
                               (essais, interventions, notices FDA, littérature).

  2. `scan_competition()`    — cartographie des agents IA biomédicaux existants
                               ET à venir, via recherche web côté serveur, avec
                               sources citées.

  3. `build_investment_plan()` — plan d'allocation appuyé simultanément sur les
                               pathologies analysées, le paysage concurrentiel et
                               une recherche web du marché à l'instant présent.

Les deux derniers activent la recherche web : sans elle, un « plan temps réel »
ne serait qu'une restitution de mémoire d'entraînement.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from claude_client import AIResult, call_structured, is_enabled
from config import (
    COMPETITOR_SEED_QUERIES,
    COMPETITOR_SEGMENTS,
    INVESTMENT_HORIZONS,
    INVESTMENT_SEGMENTS,
    MAX_WORKERS,
    RISK_TIERS,
)

logger = logging.getLogger("veille.analysis")

AI_MATURITY = ["Recherche amont", "Prototype", "Validation clinique", "Déployé en routine"]
SEVERITY = ["Faible", "Moyenne", "Élevée", "Critique"]
ORG_TYPES = ["Startup", "Scale-up", "Big Tech", "Pharma / Biotech", "Académique", "Public / Institutionnel"]
PLAYER_STATUS = ["Existant", "En lancement", "Annoncé / à venir"]
THREAT_LEVELS = ["Faible", "Modérée", "Élevée", "Majeure"]


# ===========================================================================
# 1. Analyse d'une pathologie à risque élevé
# ===========================================================================
_PATHOLOGY_SYSTEM = (
    "Tu es un expert en épidémiologie clinique et en évaluation des technologies "
    "de santé. On te fournit un dossier de preuves extrait en temps réel de "
    "ClinicalTrials.gov, d'openFDA et de PubMed pour une pathologie donnée.\n\n"
    "Règles impératives :\n"
    "- Tu t'appuies sur le dossier fourni ; tu ne cites aucun essai, molécule ou "
    "chiffre qui n'y figure pas.\n"
    "- Les ordres de grandeur épidémiologiques que tu donnes sont qualifiés comme "
    "tels et restent prudents.\n"
    "- Tu distingues nettement le standard de soin établi des pistes encore "
    "expérimentales.\n"
    "- Tu réponds en français, dans un style dense et sans emphase commerciale.\n"
    "- Tu ne produis jamais de recommandation de prise en charge individuelle : "
    "cette analyse sert une veille stratégique, pas une décision médicale."
)

_PATHOLOGY_SCHEMA = {
    "type": "object",
    "properties": {
        "risk_tier": {"type": "string", "enum": RISK_TIERS},
        "risk_score": {
            "type": "integer",
            "description": "Score de risque composite : létalité, charge de morbidité, retard diagnostique, carence thérapeutique.",
        },
        "risk_rationale": {"type": "string", "description": "Justification du niveau de risque en 2 à 3 phrases."},
        "burden_note": {"type": "string", "description": "Ordre de grandeur de la charge de mortalité/morbidité, explicitement qualifié d'approximatif."},
        "diagnostic_gap": {"type": "string", "description": "Où le diagnostic échoue ou arrive trop tard."},
        "standard_of_care": {"type": "string", "description": "Traitements de référence actuels, en synthèse."},
        "emerging_therapies": {
            "type": "array",
            "description": "Pistes thérapeutiques émergentes identifiées dans le dossier.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "modality": {"type": "string"},
                    "stage": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                },
                "required": ["name", "modality", "stage", "why_it_matters"],
                "additionalProperties": False,
            },
        },
        "unmet_need": {"type": "string", "description": "Besoin médical non couvert le plus saillant."},
        "unmet_need_score": {"type": "integer"},
        "ai_opportunity": {"type": "string", "description": "Où un agent IA apporterait le plus de valeur sur cette pathologie."},
        "ai_maturity": {"type": "string", "enum": AI_MATURITY},
        "investment_attractiveness": {
            "type": "integer",
            "description": "Attractivité d'investissement : besoin non couvert croisé à la faisabilité technique et à la dynamique d'essais.",
        },
        "watchpoints": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2 à 4 points à surveiller dans les prochains mois.",
        },
    },
    "required": [
        "risk_tier", "risk_score", "risk_rationale", "burden_note", "diagnostic_gap",
        "standard_of_care", "emerging_therapies", "unmet_need", "unmet_need_score",
        "ai_opportunity", "ai_maturity", "investment_attractiveness", "watchpoints",
    ],
    "additionalProperties": False,
}


def _render_dossier(dossier: Dict) -> str:
    """Met en forme le dossier de preuves d'une pathologie pour l'invite."""
    patho = dossier["pathology"]
    metrics = dossier["metrics"]

    trials = "\n".join(
        f"  - [{t['phase']} · {t['status']}] {t['title'][:150]} "
        f"(NCT {t['nct_id']}, promoteur : {t['sponsor']}, MàJ {t['last_update']})"
        for t in dossier["trials"][:12]
    ) or "  (aucun essai récupéré)"

    treatments = "\n".join(
        f"  - {t['name']} | {t['modality']} | {t['phase']} | {t['evidence_level']} "
        f"| source : {t['source']}"
        + (f" | {t['detail'][:160]}" if t.get("detail") else "")
        for t in dossier["treatments"][:25]
    ) or "  (aucun traitement identifié)"

    literature = "\n".join(
        f"  - {a['title'][:150]} ({a['published']})" for a in dossier["literature"][:6]
    ) or "  (aucune publication récupérée)"

    return (
        f"PATHOLOGIE : {patho['label']} ({patho['family']})\n"
        f"Repère du catalogue : {patho['note']}\n\n"
        f"INDICATEURS DU DOSSIER\n"
        f"  Essais récupérés : {metrics['trials_total']} "
        f"(actifs : {metrics['trials_active']}, phase 3+ : {metrics['trials_late_phase']}, "
        f"promoteur industriel : {metrics['industry_sponsored']})\n"
        f"  Traitements identifiés : {metrics['treatments_total']} "
        f"(émergents : {metrics['treatments_emerging']}, homologués : {metrics['treatments_approved']})\n\n"
        f"ESSAIS CLINIQUES RÉCENTS (ClinicalTrials.gov)\n{trials}\n\n"
        f"TRAITEMENTS EXTRAITS (essais + notices openFDA)\n{treatments}\n\n"
        f"LITTÉRATURE RÉCENTE (PubMed)\n{literature}"
    )


def analyze_pathology(dossier: Dict) -> AIResult:
    """Analyse le risque et le paysage thérapeutique d'une pathologie."""
    patho = dossier["pathology"]
    prompt = (
        "Analyse la pathologie suivante à partir de son seul dossier de preuves.\n\n"
        f"{_render_dossier(dossier)}\n\n"
        "Produis l'évaluation structurée demandée. Le niveau de risque doit refléter "
        "la létalité, la charge de morbidité, le retard diagnostique et la carence "
        "thérapeutique — pas le volume d'essais, qui mesure l'attention de la "
        "recherche et non la gravité."
    )
    result = call_structured(
        prompt=prompt,
        schema=_PATHOLOGY_SCHEMA,
        system=_PATHOLOGY_SYSTEM,
        max_tokens=12000,
    )
    if result.ok:
        result.data["pathology_code"] = patho["code"]
        logger.info("Pathologie analysée : %s -> %s", patho["code"], result.data.get("risk_tier"))
    else:
        logger.error("Analyse impossible pour %s : %s", patho["code"], result.error)
    return result


def analyze_pathologies(dossiers: List[Dict]) -> List[AIResult]:
    """Analyse plusieurs pathologies en parallèle."""
    if not dossiers:
        return []
    if not is_enabled():
        logger.warning("Aucune clé Claude : les pathologies sont indexées sans analyse IA.")
        return [AIResult(error="Analyse IA désactivée : aucune clé API Claude.") for _ in dossiers]
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(dossiers))) as pool:
        return list(pool.map(analyze_pathology, dossiers))


def baseline_pathology_record(dossier: Dict) -> Dict:
    """
    Fiche de pathologie sans IA (mode dégradé) : on conserve le niveau de risque
    de référence du catalogue et les compteurs réellement extraits.
    """
    patho = dossier["pathology"]
    metrics = dossier["metrics"]
    return {
        "pathology_code": patho["code"],
        "risk_tier": patho["baseline_tier"],
        "risk_score": {"Modéré": 40, "Élevé": 60, "Très élevé": 78, "Critique": 92}
            .get(patho["baseline_tier"], 60),
        "risk_rationale": f"Niveau de référence du catalogue (analyse IA indisponible). {patho['note']}",
        "burden_note": patho["note"],
        "diagnostic_gap": "Non analysé (clé API Claude absente).",
        "standard_of_care": "Non analysé — consultez l'onglet Traitements pour les données brutes extraites.",
        "emerging_therapies": [],
        "unmet_need": "Non analysé (clé API Claude absente).",
        "unmet_need_score": 3,
        "ai_opportunity": "Non analysé (clé API Claude absente).",
        "ai_maturity": AI_MATURITY[0],
        "investment_attractiveness": 3,
        "watchpoints": [],
        "metrics": metrics,
    }


# ===========================================================================
# 2. Veille concurrentielle des agents IA biomédicaux (temps réel)
# ===========================================================================
_COMPETITION_SYSTEM = (
    "Tu es analyste concurrence sur le marché des agents d'IA biomédicale et "
    "clinique. Tu dois cartographier les acteurs à partir de recherches web que "
    "tu conduis toi-même, maintenant.\n\n"
    "Règles impératives :\n"
    "- Tu utilises l'outil de recherche web avant de conclure. Ta mémoire "
    "d'entraînement est datée : elle sert à formuler des requêtes, pas à répondre.\n"
    "- Chaque acteur doit être adossé à une source web que tu as réellement "
    "consultée ; renseigne son URL.\n"
    "- Les montants de financement que tu ne peux pas sourcer valent 0 "
    "(« non documenté ») — tu n'estimes jamais un chiffre au doigt mouillé.\n"
    "- Tu couvres à la fois les acteurs déjà opérationnels et ceux annoncés ou en "
    "cours de lancement, en les distinguant par leur statut.\n"
    "- Tu réponds en français."
)

_COMPETITION_SCHEMA = {
    "type": "object",
    "properties": {
        "market_summary": {"type": "string", "description": "État du marché en 3 à 5 phrases, daté."},
        "as_of": {"type": "string", "description": "Date ou période à laquelle se rapportent les informations trouvées."},
        "players": {
            "type": "array",
            "description": "10 à 18 acteurs, mêlant acteurs établis et acteurs à venir.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "org_type": {"type": "string", "enum": ORG_TYPES},
                    "status": {"type": "string", "enum": PLAYER_STATUS},
                    "segment": {"type": "string", "enum": COMPETITOR_SEGMENTS},
                    "country": {"type": "string"},
                    "funding_musd": {
                        "type": "number",
                        "description": "Financement cumulé en millions de dollars ; 0 si non documenté.",
                    },
                    "key_product": {"type": "string"},
                    "differentiator": {"type": "string"},
                    "moat": {"type": "string", "description": "Barrière à l'entrée : données propriétaires, homologation, distribution…"},
                    "threat_level": {"type": "string", "enum": THREAT_LEVELS},
                    "recent_move": {"type": "string", "description": "Fait marquant le plus récent trouvé, daté."},
                    "evidence_url": {"type": "string", "description": "URL de la source consultée."},
                },
                "required": [
                    "name", "org_type", "status", "segment", "country", "funding_musd",
                    "key_product", "differentiator", "moat", "threat_level",
                    "recent_move", "evidence_url",
                ],
                "additionalProperties": False,
            },
        },
        "upcoming_signals": {
            "type": "array",
            "description": "Lancements, homologations ou levées attendus.",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "expected": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": ["label", "expected", "why_it_matters", "source_url"],
                "additionalProperties": False,
            },
        },
        "whitespace": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Espaces de marché encore peu disputés.",
        },
        "consolidation_outlook": {"type": "string"},
        "confidence": {
            "type": "integer",
            "description": "Confiance dans la cartographie, au vu de ce que les recherches ont réellement permis de sourcer.",
        },
    },
    "required": [
        "market_summary", "as_of", "players", "upcoming_signals",
        "whitespace", "consolidation_outlook", "confidence",
    ],
    "additionalProperties": False,
}


def scan_competition(focus: Optional[str] = None) -> AIResult:
    """
    Cartographie temps réel des agents IA biomédicaux existants et à venir.
    S'appuie sur la recherche web côté serveur ; les sources sont restituées.
    """
    queries = "\n".join(f"  - {q}" for q in COMPETITOR_SEED_QUERIES)
    segments = "\n".join(f"  - {s}" for s in COMPETITOR_SEGMENTS)
    focus_line = (
        f"\nAxe d'attention prioritaire demandé : {focus}.\n" if focus else ""
    )

    prompt = (
        "Cartographie le paysage concurrentiel des agents d'IA biomédicale à "
        "l'instant présent.\n\n"
        "Commence par lancer plusieurs recherches web, en t'inspirant de ces "
        f"pistes de requêtes (adapte-les, complète-les) :\n{queries}\n\n"
        f"Classe chaque acteur dans l'un de ces segments :\n{segments}\n"
        f"{focus_line}\n"
        "Couvre explicitement les deux populations :\n"
        "  1. les agents et plateformes déjà opérationnels ou commercialisés ;\n"
        "  2. ceux annoncés, en préversion, en cours d'homologation ou attendus.\n\n"
        "Pour chaque acteur, renseigne l'URL de la source qui l'atteste. "
        "Si tes recherches ne permettent pas de documenter un champ, dis-le "
        "explicitement plutôt que de le combler."
    )

    result = call_structured(
        prompt=prompt,
        schema=_COMPETITION_SCHEMA,
        system=_COMPETITION_SYSTEM,
        web_search=True,
        max_tokens=32000,
    )
    if result.ok:
        logger.info(
            "Veille concurrentielle : %d acteur(s), %d recherche(s) web, %d source(s)",
            len(result.data.get("players", [])), result.searches, len(result.sources),
        )
    else:
        logger.error("Veille concurrentielle échouée : %s", result.error)
    return result


# ===========================================================================
# 3. Plan d'investissement (temps réel, ancré sur les données collectées)
# ===========================================================================
_INVESTMENT_SYSTEM = (
    "Tu es analyste d'investissement spécialisé en santé numérique et en IA "
    "biomédicale. Tu construis un plan d'allocation à partir de trois intrants : "
    "un portefeuille de pathologies à risque élevé analysées en temps réel, une "
    "cartographie concurrentielle, et tes propres recherches web sur l'état du "
    "marché.\n\n"
    "Règles impératives :\n"
    "- Tu lances des recherches web pour actualiser le contexte de marché avant "
    "de conclure ; ta mémoire d'entraînement est datée.\n"
    "- Les pondérations d'allocation totalisent exactement 100.\n"
    "- Chaque ligne d'allocation est justifiée par un élément des intrants ou "
    "d'une source consultée, jamais par une intuition de marché.\n"
    "- Tu nommes explicitement les risques, y compris ceux qui affaiblissent ta "
    "propre thèse.\n"
    "- Tu réponds en français.\n\n"
    "Ce plan est un support d'analyse et de veille stratégique. Il ne constitue "
    "pas un conseil en investissement personnalisé, et tu le rappelles dans le "
    "champ prévu."
)

_INVESTMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "horizon": {"type": "string", "enum": INVESTMENT_HORIZONS},
        "thesis": {"type": "string", "description": "Thèse d'investissement en 4 à 6 phrases."},
        "market_context": {"type": "string", "description": "Contexte de marché actualisé par tes recherches web, daté."},
        "allocations": {
            "type": "array",
            "description": "Une ligne par segment retenu ; les pondérations totalisent 100.",
            "items": {
                "type": "object",
                "properties": {
                    "segment": {"type": "string", "enum": INVESTMENT_SEGMENTS},
                    "weight_pct": {"type": "integer",},
                    "rationale": {"type": "string"},
                    "risk_level": {"type": "string", "enum": SEVERITY},
                    "time_to_value": {"type": "string"},
                    "target_pathologies": {"type": "array", "items": {"type": "string"}},
                    "example_players": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "segment", "weight_pct", "rationale", "risk_level",
                    "time_to_value", "target_pathologies", "example_players",
                ],
                "additionalProperties": False,
            },
        },
        "priority_pathologies": {
            "type": "array",
            "description": "3 à 6 pathologies du portefeuille à prioriser.",
            "items": {
                "type": "object",
                "properties": {
                    "pathology": {"type": "string"},
                    "why": {"type": "string"},
                    "entry_angle": {"type": "string", "description": "Par quel type d'actif ou de technologie entrer."},
                },
                "required": ["pathology", "why", "entry_angle"],
                "additionalProperties": False,
            },
        },
        "deployment_phases": {
            "type": "array",
            "description": "Échelonnement du capital dans le temps.",
            "items": {
                "type": "object",
                "properties": {
                    "phase": {"type": "string"},
                    "share_pct": {"type": "integer"},
                    "focus": {"type": "string"},
                    "trigger": {"type": "string", "description": "Condition de déclenchement de cette tranche."},
                },
                "required": ["phase", "share_pct", "focus", "trigger"],
                "additionalProperties": False,
            },
        },
        "catalysts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "timing": {"type": "string"},
                    "impact": {"type": "string"},
                },
                "required": ["label", "timing", "impact"],
                "additionalProperties": False,
            },
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "severity": {"type": "string", "enum": SEVERITY},
                    "mitigation": {"type": "string"},
                },
                "required": ["label", "severity", "mitigation"],
                "additionalProperties": False,
            },
        },
        "kpis": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Indicateurs de suivi de la thèse.",
        },
        "confidence": {"type": "integer"},
        "disclaimer": {"type": "string"},
    },
    "required": [
        "horizon", "thesis", "market_context", "allocations", "priority_pathologies",
        "deployment_phases", "catalysts", "risks", "kpis", "confidence", "disclaimer",
    ],
    "additionalProperties": False,
}


def _render_pathology_portfolio(records: List[Dict]) -> str:
    """Résume les pathologies analysées pour l'invite d'investissement."""
    if not records:
        return "  (aucune pathologie analysée — lancez d'abord l'analyse des pathologies)"
    ordered = sorted(records, key=lambda r: r.get("risk_score", 0), reverse=True)
    lines = []
    for rec in ordered[:20]:
        metrics = rec.get("metrics") or {}
        lines.append(
            f"  - {rec.get('label', rec.get('pathology_code'))} "
            f"[{rec.get('risk_tier')}, score {rec.get('risk_score')}/100] "
            f"besoin non couvert {rec.get('unmet_need_score')}/5, "
            f"attractivité {rec.get('investment_attractiveness')}/5, "
            f"maturité IA : {rec.get('ai_maturity')} | "
            f"essais {metrics.get('trials_total', 0)} "
            f"(phase 3+ : {metrics.get('trials_late_phase', 0)}), "
            f"traitements {metrics.get('treatments_total', 0)} "
            f"(émergents : {metrics.get('treatments_emerging', 0)}) | "
            f"opportunité IA : {str(rec.get('ai_opportunity', ''))[:180]}"
        )
    return "\n".join(lines)


def _render_competition(competition: Optional[Dict]) -> str:
    """Résume la cartographie concurrentielle pour l'invite d'investissement."""
    if not competition or not competition.get("players"):
        return "  (cartographie concurrentielle non disponible)"
    lines = [f"  Synthèse marché : {competition.get('market_summary', '')[:600]}"]
    for p in competition["players"][:18]:
        funding = f"{p.get('funding_musd', 0):.0f} M$" if p.get("funding_musd") else "financement non documenté"
        lines.append(
            f"  - {p.get('name')} [{p.get('status')} · {p.get('org_type')} · {p.get('segment')}] "
            f"{funding}, menace {p.get('threat_level')} | {str(p.get('differentiator', ''))[:140]}"
        )
    return "\n".join(lines)


def build_investment_plan(
    pathology_records: List[Dict],
    competition: Optional[Dict] = None,
    horizon: str = "3 ans",
    capital_note: str = "",
) -> AIResult:
    """
    Construit un plan d'investissement ancré sur les pathologies analysées, la
    cartographie concurrentielle et une recherche web du marché en temps réel.
    """
    segments = "\n".join(f"  - {s}" for s in INVESTMENT_SEGMENTS)
    capital_line = f"\nContrainte de capital précisée par l'utilisateur : {capital_note}\n" if capital_note else ""

    prompt = (
        f"Construis un plan d'investissement à horizon {horizon} sur l'IA biomédicale.\n\n"
        "INTRANT 1 — PORTEFEUILLE DE PATHOLOGIES À RISQUE ÉLEVÉ (analysé en temps réel)\n"
        f"{_render_pathology_portfolio(pathology_records)}\n\n"
        "INTRANT 2 — PAYSAGE CONCURRENTIEL\n"
        f"{_render_competition(competition)}\n\n"
        "INTRANT 3 — À TOI DE LE PRODUIRE : lance des recherches web pour "
        "actualiser le contexte de marché (dynamique de financement du secteur, "
        "homologations récentes, évolution réglementaire, mouvements de "
        "consolidation). Ne conclus pas avant de les avoir menées.\n\n"
        f"Segments d'allocation autorisés :\n{segments}\n"
        f"{capital_line}\n"
        "Contraintes de sortie : les pondérations d'allocation totalisent exactement "
        "100 ; celles des phases de déploiement également. Chaque justification "
        "renvoie à un élément des intrants ou à une source que tu as consultée."
    )

    result = call_structured(
        prompt=prompt,
        schema=_INVESTMENT_SCHEMA,
        system=_INVESTMENT_SYSTEM,
        web_search=True,
        max_tokens=32000,
    )

    if result.ok:
        _normalize_weights(result.data.get("allocations", []), "weight_pct")
        _normalize_weights(result.data.get("deployment_phases", []), "share_pct")
        result.data["horizon"] = result.data.get("horizon") or horizon
        logger.info(
            "Plan d'investissement produit : %d ligne(s) d'allocation, %d recherche(s) web",
            len(result.data.get("allocations", [])), result.searches,
        )
    else:
        logger.error("Plan d'investissement échoué : %s", result.error)
    return result


def _normalize_weights(rows: List[Dict], key: str) -> None:
    """
    Ramène la somme des pondérations à 100 si le modèle a dérivé de quelques
    points, en ajustant la ligne la plus lourde (garde-fou d'affichage).
    """
    if not rows:
        return
    total = sum(int(r.get(key, 0) or 0) for r in rows)
    if total == 100 or total <= 0:
        return
    logger.warning("Pondérations '%s' à %d %%, renormalisation appliquée.", key, total)
    for row in rows:
        row[key] = round(int(row.get(key, 0) or 0) * 100 / total)
    drift = 100 - sum(r[key] for r in rows)
    if drift:
        heaviest = max(rows, key=lambda r: r[key])
        heaviest[key] += drift


# ===========================================================================
# Sérialisation utilitaire
# ===========================================================================
def to_json(value: object) -> str:
    """Sérialise une structure pour stockage SQLite."""
    return json.dumps(value, ensure_ascii=False)


def from_json(raw: object, default: object = None):
    """Désérialise une colonne JSON, en tolérant les valeurs absentes ou corrompues."""
    if not raw:
        return default if default is not None else []
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []
