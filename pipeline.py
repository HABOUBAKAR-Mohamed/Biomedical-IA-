"""
Orchestration du pipeline de veille.

Quatre étapes indépendantes, déclenchables séparément depuis le tableau de bord
ou en ligne de commande :

  signals      Extract (PubMed + ClinicalTrials.gov) -> qualification IA -> SQLite
  pathologies  Extract (essais + interventions + openFDA + PubMed) -> analyse de
               risque et de paysage thérapeutique -> SQLite
  competition  Recherche web temps réel -> cartographie des agents IA biomédicaux
  investment   Pathologies analysées + concurrence + recherche web -> plan d'allocation

`run_full_pipeline()` les enchaîne dans cet ordre, car `investment` consomme les
résultats des trois autres.

Chaque étape est isolée : son échec est journalisé et renvoyé, sans interrompre
les suivantes ni faire tomber l'interface.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import analysis
import load
from claude_client import active_key, is_enabled
from config import CLAUDE_MODEL, HIGH_RISK_PATHOLOGIES, pathology_by_code
from extract import collect_all_pathology_evidence, collect_all_realtime_data
from load import _now  # horodatage partagé avec la couche de stockage
from transform import analyze_signals

logger = logging.getLogger("veille.pipeline")


# ---------------------------------------------------------------------------
# Étape 1 — Fil de signaux
# ---------------------------------------------------------------------------
def run_signals_stage() -> Dict:
    """Collecte et qualifie les publications et essais récents."""
    started = _now()
    logger.info("[signals] Collecte temps réel PubMed + ClinicalTrials.gov…")
    try:
        raw = collect_all_realtime_data()
        if not raw:
            load.log_run("signals", "warn", "Aucun élément collecté (sources injoignables ?)",
                         0, started)
            return {"stage": "signals", "ok": False, "collected": 0, "inserted": 0,
                    "duplicates": 0, "error": "Aucun élément collecté."}

        logger.info("[signals] Qualification IA de %d élément(s)…", len(raw))
        enriched = analyze_signals(raw)

        inserted = load.save_articles(enriched)
        summary = {
            "stage": "signals", "ok": True,
            "collected": len(raw), "inserted": inserted,
            "duplicates": len(raw) - inserted,
            "ai": is_enabled(), "error": None,
        }
        load.log_run(
            "signals", "ok",
            f"{len(raw)} collectés, {inserted} nouveaux, {len(raw) - inserted} doublons"
            + ("" if is_enabled() else " (mode dégradé, sans analyse IA)"),
            inserted, started,
        )
        return summary

    except Exception as exc:
        logger.exception("[signals] Échec")
        load.log_run("signals", "error", str(exc)[:300], 0, started)
        return {"stage": "signals", "ok": False, "collected": 0, "inserted": 0,
                "duplicates": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Étape 2 — Pathologies à risque élevé et leurs traitements
# ---------------------------------------------------------------------------
def run_pathologies_stage(codes: Optional[List[str]] = None) -> Dict:
    """
    Extrait le dossier de preuves de chaque pathologie (essais, interventions,
    traitements homologués, littérature), le fait analyser par Claude, et stocke
    fiches et traitements.

    `codes=None` traite tout le catalogue.
    """
    started = _now()
    catalog = (
        [p for p in HIGH_RISK_PATHOLOGIES if p["code"] in set(codes)]
        if codes else list(HIGH_RISK_PATHOLOGIES)
    )
    if not catalog:
        return {"stage": "pathologies", "ok": False, "error": "Aucune pathologie sélectionnée."}

    logger.info("[pathologies] Extraction pour %d pathologie(s)…", len(catalog))
    try:
        dossiers = collect_all_pathology_evidence(catalog)

        logger.info("[pathologies] Analyse IA…")
        results = analysis.analyze_pathologies(dossiers)

        analyzed = failed = 0
        treatments_written = 0
        in_tok = out_tok = 0
        errors: List[str] = []

        for dossier, result in zip(dossiers, results):
            code = dossier["pathology"]["code"]
            metrics = dossier["metrics"]

            if result.ok:
                record = {**result.data, "metrics": metrics}
                load.save_pathology(record, "ok", result.model)
                analyzed += 1
                in_tok += result.input_tokens
                out_tok += result.output_tokens
            else:
                # Mode dégradé : la fiche est conservée avec les compteurs réels
                load.save_pathology(analysis.baseline_pathology_record(dossier), "degraded", "")
                failed += 1
                if result.error:
                    errors.append(f"{code}: {result.error}")

            treatments_written += load.save_treatments(dossier["treatments"])

        detail = (
            f"{len(catalog)} pathologie(s) traitée(s), {analyzed} analysée(s) par IA, "
            f"{failed} en mode dégradé, {treatments_written} traitement(s) stocké(s)"
        )
        status = "ok" if failed == 0 else ("warn" if analyzed else "error")
        load.log_run("pathologies", status, detail, len(catalog), started, in_tok, out_tok)

        return {
            "stage": "pathologies", "ok": analyzed > 0 or not is_enabled(),
            "pathologies": len(catalog), "analyzed": analyzed, "degraded": failed,
            "treatments": treatments_written,
            "trials": sum(d["metrics"]["trials_total"] for d in dossiers),
            "input_tokens": in_tok, "output_tokens": out_tok,
            "error": "; ".join(errors[:3]) if errors else None,
        }

    except Exception as exc:
        logger.exception("[pathologies] Échec")
        load.log_run("pathologies", "error", str(exc)[:300], 0, started)
        return {"stage": "pathologies", "ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Étape 3 — Veille concurrentielle temps réel
# ---------------------------------------------------------------------------
def run_competition_stage(focus: Optional[str] = None) -> Dict:
    """Cartographie les agents IA biomédicaux existants et à venir (recherche web)."""
    started = _now()
    if not is_enabled():
        msg = "Veille concurrentielle indisponible : elle exige une clé API Claude (recherche web)."
        load.log_run("competition", "warn", msg, 0, started)
        return {"stage": "competition", "ok": False, "error": msg}

    logger.info("[competition] Recherche web et cartographie…")
    try:
        result = analysis.scan_competition(focus)
        if not result.ok:
            load.log_run("competition", "error", result.error or "échec", 0, started)
            return {"stage": "competition", "ok": False, "error": result.error}

        load.save_competition(result.data, result.sources, result.searches, result.model)
        players = result.data.get("players", [])
        detail = (
            f"{len(players)} acteur(s) cartographié(s), {result.searches} recherche(s) web, "
            f"{len(result.sources)} source(s) citée(s)"
        )
        load.log_run("competition", "ok", detail, len(players), started,
                     result.input_tokens, result.output_tokens)
        return {
            "stage": "competition", "ok": True, "players": len(players),
            "upcoming": len(result.data.get("upcoming_signals", [])),
            "searches": result.searches, "sources": len(result.sources),
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "cost": result.cost_estimate, "error": None,
        }

    except Exception as exc:
        logger.exception("[competition] Échec")
        load.log_run("competition", "error", str(exc)[:300], 0, started)
        return {"stage": "competition", "ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Étape 4 — Plan d'investissement
# ---------------------------------------------------------------------------
def _stored_pathology_records() -> List[Dict]:
    """Recharge les fiches de pathologies depuis la base pour nourrir le plan."""
    records = []
    for row in load.read_pathologies():
        records.append({
            "pathology_code": row["code"],
            "label": row["label"] or pathology_by_code(row["code"]).get("label", row["code"]),
            "risk_tier": row["risk_tier"],
            "risk_score": row["risk_score"],
            "unmet_need_score": row["unmet_need_score"],
            "investment_attractiveness": row["investment_attractiveness"],
            "ai_maturity": row["ai_maturity"],
            "ai_opportunity": row["ai_opportunity"],
            "metrics": {
                "trials_total": row["trials_total"],
                "trials_late_phase": row["trials_late_phase"],
                "treatments_total": row["treatments_total"],
                "treatments_emerging": row["treatments_emerging"],
            },
        })
    return records


def run_investment_stage(horizon: str = "3 ans", capital_note: str = "") -> Dict:
    """Produit un plan d'investissement à partir de tout ce qui a été collecté."""
    started = _now()
    if not is_enabled():
        msg = "Plan d'investissement indisponible : il exige une clé API Claude (recherche web)."
        load.log_run("investment", "warn", msg, 0, started)
        return {"stage": "investment", "ok": False, "error": msg}

    records = _stored_pathology_records()
    if not records:
        msg = "Lancez d'abord l'analyse des pathologies : le plan s'appuie sur ce portefeuille."
        load.log_run("investment", "warn", msg, 0, started)
        return {"stage": "investment", "ok": False, "error": msg}

    competition_row = load.read_latest_competition()
    competition = None
    if competition_row:
        competition = {
            "market_summary": competition_row["market_summary"],
            "players": analysis.from_json(competition_row["players"], []),
        }

    logger.info("[investment] Construction du plan (horizon %s)…", horizon)
    try:
        result = analysis.build_investment_plan(records, competition, horizon, capital_note)
        if not result.ok:
            load.log_run("investment", "error", result.error or "échec", 0, started)
            return {"stage": "investment", "ok": False, "error": result.error}

        load.save_investment_plan(result.data, result.sources, result.searches, result.model)
        allocations = result.data.get("allocations", [])
        detail = (
            f"horizon {result.data.get('horizon')}, {len(allocations)} ligne(s) d'allocation, "
            f"{result.searches} recherche(s) web, confiance {result.data.get('confidence')}/5"
            + ("" if competition else " (sans cartographie concurrentielle)")
        )
        load.log_run("investment", "ok", detail, len(allocations), started,
                     result.input_tokens, result.output_tokens)
        return {
            "stage": "investment", "ok": True, "allocations": len(allocations),
            "horizon": result.data.get("horizon"),
            "confidence": result.data.get("confidence"),
            "searches": result.searches, "sources": len(result.sources),
            "used_competition": competition is not None,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "cost": result.cost_estimate, "error": None,
        }

    except Exception as exc:
        logger.exception("[investment] Échec")
        load.log_run("investment", "error", str(exc)[:300], 0, started)
        return {"stage": "investment", "ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Enchaînement complet
# ---------------------------------------------------------------------------
def run_full_pipeline(horizon: str = "3 ans", capital_note: str = "") -> Dict:
    """
    Exécute les quatre étapes dans l'ordre de dépendance.
    L'échec d'une étape n'empêche pas les suivantes de s'exécuter.
    """
    load.init_db()
    results = {
        "signals": run_signals_stage(),
        "pathologies": run_pathologies_stage(),
        "competition": run_competition_stage(),
    }
    results["investment"] = run_investment_stage(horizon, capital_note)

    failed = [name for name, res in results.items() if not res.get("ok")]
    results["_summary"] = {
        "ok": not failed,
        "failed_stages": failed,
        "ai_enabled": is_enabled(),
        "finished_at": _now(),
    }
    return results


# Compatibilité avec l'ancienne interface en ligne de commande
def run_pipeline() -> Dict:
    """Alias historique : exécute l'étape de collecte des signaux."""
    return run_signals_stage()


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")

    parser = argparse.ArgumentParser(description="Pipeline de veille biomédicale temps réel")
    parser.add_argument(
        "stage", nargs="?", default="all",
        choices=["all", "signals", "pathologies", "competition", "investment"],
        help="Étape à exécuter (défaut : all)",
    )
    parser.add_argument("--horizon", default="3 ans", help="Horizon du plan d'investissement")
    parser.add_argument("--capital", default="", help="Contrainte de capital à transmettre au plan")
    args = parser.parse_args()

    if not active_key():
        print("⚠  Aucune clé ANTHROPIC_API_KEY : exécution en mode dégradé "
              "(collecte des données publiques, sans analyse IA).")
    else:
        print(f"✓  Analyse IA active — modèle {CLAUDE_MODEL}")

    if args.stage == "all":
        outcome = run_full_pipeline(args.horizon, args.capital)
        for name, res in outcome.items():
            if name.startswith("_"):
                continue
            mark = "✓" if res.get("ok") else "✗"
            print(f"{mark} {name:12} {res.get('error') or 'terminé'}")
        summary = outcome["_summary"]
        print(f"\nÉchecs : {', '.join(summary['failed_stages']) or 'aucun'}")
    else:
        stage_fn = {
            "signals": run_signals_stage,
            "pathologies": run_pathologies_stage,
            "competition": run_competition_stage,
            "investment": lambda: run_investment_stage(args.horizon, args.capital),
        }[args.stage]
        print(stage_fn())
