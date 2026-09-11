"""
Étape LOAD : stockage structuré dans SQLite.

Six tables :
  * `veille_sante_realtime` — fil de signaux (publications & essais qualifiés)
  * `pathologies`           — fiches des pathologies à risque élevé
  * `treatments`            — traitements extraits, homologués ou expérimentaux
  * `investment_plan`       — plans d'investissement horodatés
  * `competition`           — cartographies concurrentielles horodatées
  * `runs`                  — journal d'exécution du pipeline

Le schéma préexistant est migré sans perte : les colonnes ajoutées le sont par
`ALTER TABLE`, les données déjà collectées restent lisibles.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional

from config import DB_PATH, pathology_by_code

logger = logging.getLogger("veille.load")

SIGNALS_TABLE = "veille_sante_realtime"


@contextmanager
def _connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")     # lectures du dashboard pendant les écritures
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    """Horodatage UTC en ISO 8601 (secondes)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    """Ajoute les colonnes manquantes d'une table déjà existante (migration)."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if not existing:
        return
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            logger.info("Migration : colonne %s.%s ajoutée", table, name)


def init_db() -> None:
    """Crée ou met à niveau l'ensemble du schéma. Idempotent."""
    with _connection() as conn:
        # --- Fil de signaux -------------------------------------------------
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {SIGNALS_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                link TEXT UNIQUE NOT NULL,
                published TEXT,
                source TEXT,
                topic TEXT,
                categorie TEXT,
                resume_ia TEXT,
                impact_score INTEGER,
                mots_cles TEXT,
                investment_score INTEGER DEFAULT 0,
                pathologie_liee TEXT,
                collected_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _ensure_columns(conn, SIGNALS_TABLE, {
            "investment_score": "INTEGER DEFAULT 0",
            "pathologie_liee": "TEXT",
        })
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_sig_cat ON {SIGNALS_TABLE}(categorie)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_sig_src ON {SIGNALS_TABLE}(source)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_sig_patho ON {SIGNALS_TABLE}(pathologie_liee)")

        # --- Pathologies à risque élevé -------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pathologies (
                code TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                family TEXT,
                risk_tier TEXT,
                risk_score INTEGER,
                risk_rationale TEXT,
                burden_note TEXT,
                diagnostic_gap TEXT,
                standard_of_care TEXT,
                emerging_therapies TEXT,
                unmet_need TEXT,
                unmet_need_score INTEGER,
                ai_opportunity TEXT,
                ai_maturity TEXT,
                investment_attractiveness INTEGER,
                watchpoints TEXT,
                trials_total INTEGER DEFAULT 0,
                trials_active INTEGER DEFAULT 0,
                trials_late_phase INTEGER DEFAULT 0,
                treatments_total INTEGER DEFAULT 0,
                treatments_emerging INTEGER DEFAULT 0,
                treatments_approved INTEGER DEFAULT 0,
                industry_sponsored INTEGER DEFAULT 0,
                analysis_status TEXT,
                model TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_patho_tier ON pathologies(risk_tier)")

        # --- Traitements ----------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS treatments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pathology_code TEXT NOT NULL,
                name TEXT NOT NULL,
                modality TEXT,
                drug_class TEXT,
                phase TEXT,
                phase_rank INTEGER DEFAULT 0,
                status TEXT,
                sponsor TEXT,
                source TEXT,
                source_ref TEXT,
                link TEXT,
                evidence_level TEXT,
                is_emerging INTEGER DEFAULT 0,
                detail TEXT,
                last_update TEXT,
                updated_at TEXT,
                UNIQUE(pathology_code, name, source)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_treat_patho ON treatments(pathology_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_treat_emerging ON treatments(is_emerging)")

        # --- Plans d'investissement -----------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS investment_plan (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                horizon TEXT,
                thesis TEXT,
                market_context TEXT,
                allocations TEXT,
                priority_pathologies TEXT,
                deployment_phases TEXT,
                catalysts TEXT,
                risks TEXT,
                kpis TEXT,
                confidence INTEGER,
                disclaimer TEXT,
                sources TEXT,
                searches INTEGER DEFAULT 0,
                model TEXT
            )
        """)

        # --- Cartographies concurrentielles ---------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS competition (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                as_of TEXT,
                market_summary TEXT,
                players TEXT,
                upcoming_signals TEXT,
                whitespace TEXT,
                consolidation_outlook TEXT,
                confidence INTEGER,
                sources TEXT,
                searches INTEGER DEFAULT 0,
                model TEXT
            )
        """)

        # --- Journal d'exécution --------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT,
                finished_at TEXT,
                stage TEXT,
                status TEXT,
                detail TEXT,
                items INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_stage ON runs(stage)")


# ---------------------------------------------------------------------------
# Écritures
# ---------------------------------------------------------------------------
def save_articles(articles: List[Dict]) -> int:
    """Insère les signaux ; ignore les doublons (contrainte UNIQUE sur `link`)."""
    init_db()
    inserted = 0
    with _connection() as conn:
        for art in articles:
            link = art.get("link")
            if not link:
                logger.warning("Signal ignoré : champ 'link' manquant")
                continue
            try:
                conn.execute(f"""
                    INSERT INTO {SIGNALS_TABLE}
                        (title, link, published, source, topic, categorie, resume_ia,
                         impact_score, mots_cles, investment_score, pathologie_liee, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    art.get("title", "Sans titre"),
                    link,
                    art.get("published", ""),
                    art.get("source", "Inconnu"),
                    art.get("topic", ""),
                    art.get("categorie", "Données de Santé & E-Santé"),
                    art.get("resume_ia", ""),
                    int(art.get("impact_score", 1) or 1),
                    art.get("mots_cles", ""),
                    int(art.get("investment_score", 0) or 0),
                    art.get("pathologie_liee", ""),
                    _now(),
                ))
                inserted += 1
            except sqlite3.IntegrityError:
                pass  # déjà collecté lors d'une exécution précédente

    logger.info("%d nouveau(x) signal(aux) enregistré(s)", inserted)
    return inserted


def save_pathology(record: Dict, analysis_status: str, model: str = "") -> None:
    """Enregistre ou met à jour la fiche d'une pathologie."""
    init_db()
    code = record["pathology_code"]
    catalog = pathology_by_code(code)
    metrics = record.get("metrics") or {}

    with _connection() as conn:
        conn.execute("""
            INSERT INTO pathologies (
                code, label, family, risk_tier, risk_score, risk_rationale, burden_note,
                diagnostic_gap, standard_of_care, emerging_therapies, unmet_need,
                unmet_need_score, ai_opportunity, ai_maturity, investment_attractiveness,
                watchpoints, trials_total, trials_active, trials_late_phase,
                treatments_total, treatments_emerging, treatments_approved,
                industry_sponsored, analysis_status, model, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(code) DO UPDATE SET
                label=excluded.label, family=excluded.family,
                risk_tier=excluded.risk_tier, risk_score=excluded.risk_score,
                risk_rationale=excluded.risk_rationale, burden_note=excluded.burden_note,
                diagnostic_gap=excluded.diagnostic_gap,
                standard_of_care=excluded.standard_of_care,
                emerging_therapies=excluded.emerging_therapies,
                unmet_need=excluded.unmet_need, unmet_need_score=excluded.unmet_need_score,
                ai_opportunity=excluded.ai_opportunity, ai_maturity=excluded.ai_maturity,
                investment_attractiveness=excluded.investment_attractiveness,
                watchpoints=excluded.watchpoints,
                trials_total=excluded.trials_total, trials_active=excluded.trials_active,
                trials_late_phase=excluded.trials_late_phase,
                treatments_total=excluded.treatments_total,
                treatments_emerging=excluded.treatments_emerging,
                treatments_approved=excluded.treatments_approved,
                industry_sponsored=excluded.industry_sponsored,
                analysis_status=excluded.analysis_status, model=excluded.model,
                updated_at=excluded.updated_at
        """, (
            code,
            catalog.get("label", code),
            catalog.get("family", ""),
            record.get("risk_tier", ""),
            int(record.get("risk_score", 0) or 0),
            record.get("risk_rationale", ""),
            record.get("burden_note", ""),
            record.get("diagnostic_gap", ""),
            record.get("standard_of_care", ""),
            json.dumps(record.get("emerging_therapies", []), ensure_ascii=False),
            record.get("unmet_need", ""),
            int(record.get("unmet_need_score", 3) or 3),
            record.get("ai_opportunity", ""),
            record.get("ai_maturity", ""),
            int(record.get("investment_attractiveness", 3) or 3),
            json.dumps(record.get("watchpoints", []), ensure_ascii=False),
            int(metrics.get("trials_total", 0)),
            int(metrics.get("trials_active", 0)),
            int(metrics.get("trials_late_phase", 0)),
            int(metrics.get("treatments_total", 0)),
            int(metrics.get("treatments_emerging", 0)),
            int(metrics.get("treatments_approved", 0)),
            int(metrics.get("industry_sponsored", 0)),
            analysis_status,
            model,
            _now(),
        ))


def save_treatments(treatments: List[Dict]) -> int:
    """Enregistre les traitements extraits ; met à jour ceux déjà connus."""
    if not treatments:
        return 0
    init_db()
    written = 0
    with _connection() as conn:
        for t in treatments:
            conn.execute("""
                INSERT INTO treatments (
                    pathology_code, name, modality, drug_class, phase, phase_rank,
                    status, sponsor, source, source_ref, link, evidence_level,
                    is_emerging, detail, last_update, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(pathology_code, name, source) DO UPDATE SET
                    modality=excluded.modality, drug_class=excluded.drug_class,
                    phase=excluded.phase, phase_rank=excluded.phase_rank,
                    status=excluded.status, sponsor=excluded.sponsor,
                    source_ref=excluded.source_ref, link=excluded.link,
                    evidence_level=excluded.evidence_level,
                    is_emerging=excluded.is_emerging, detail=excluded.detail,
                    last_update=excluded.last_update, updated_at=excluded.updated_at
            """, (
                t["pathology_code"], t["name"], t.get("modality", ""), t.get("drug_class", ""),
                t.get("phase", ""), int(t.get("phase_rank", 0) or 0), t.get("status", ""),
                t.get("sponsor", ""), t.get("source", ""), t.get("source_ref", ""),
                t.get("link", ""), t.get("evidence_level", ""),
                int(t.get("is_emerging", 0) or 0), t.get("detail", ""),
                str(t.get("last_update", "")), _now(),
            ))
            written += 1
    logger.info("%d traitement(s) enregistré(s) ou mis à jour", written)
    return written


def save_investment_plan(data: Dict, sources: List[Dict], searches: int, model: str) -> int:
    """Enregistre un plan d'investissement horodaté et retourne son identifiant."""
    init_db()
    with _connection() as conn:
        cur = conn.execute("""
            INSERT INTO investment_plan (
                run_at, horizon, thesis, market_context, allocations,
                priority_pathologies, deployment_phases, catalysts, risks, kpis,
                confidence, disclaimer, sources, searches, model
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            _now(), data.get("horizon", ""), data.get("thesis", ""),
            data.get("market_context", ""),
            json.dumps(data.get("allocations", []), ensure_ascii=False),
            json.dumps(data.get("priority_pathologies", []), ensure_ascii=False),
            json.dumps(data.get("deployment_phases", []), ensure_ascii=False),
            json.dumps(data.get("catalysts", []), ensure_ascii=False),
            json.dumps(data.get("risks", []), ensure_ascii=False),
            json.dumps(data.get("kpis", []), ensure_ascii=False),
            int(data.get("confidence", 3) or 3), data.get("disclaimer", ""),
            json.dumps(sources, ensure_ascii=False), int(searches), model,
        ))
        return int(cur.lastrowid)


def save_competition(data: Dict, sources: List[Dict], searches: int, model: str) -> int:
    """Enregistre une cartographie concurrentielle horodatée."""
    init_db()
    with _connection() as conn:
        cur = conn.execute("""
            INSERT INTO competition (
                run_at, as_of, market_summary, players, upcoming_signals,
                whitespace, consolidation_outlook, confidence, sources, searches, model
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            _now(), data.get("as_of", ""), data.get("market_summary", ""),
            json.dumps(data.get("players", []), ensure_ascii=False),
            json.dumps(data.get("upcoming_signals", []), ensure_ascii=False),
            json.dumps(data.get("whitespace", []), ensure_ascii=False),
            data.get("consolidation_outlook", ""),
            int(data.get("confidence", 3) or 3),
            json.dumps(sources, ensure_ascii=False), int(searches), model,
        ))
        return int(cur.lastrowid)


def log_run(
    stage: str, status: str, detail: str = "", items: int = 0,
    started_at: str = "", input_tokens: int = 0, output_tokens: int = 0,
) -> None:
    """Journalise une étape du pipeline."""
    init_db()
    with _connection() as conn:
        conn.execute("""
            INSERT INTO runs (started_at, finished_at, stage, status, detail, items,
                              input_tokens, output_tokens)
            VALUES (?,?,?,?,?,?,?,?)
        """, (started_at or _now(), _now(), stage, status, detail, items,
              input_tokens, output_tokens))


# ---------------------------------------------------------------------------
# Lectures
# ---------------------------------------------------------------------------
def _rows(query: str, params: tuple = ()) -> List[Dict]:
    try:
        with _connection() as conn:
            return [dict(r) for r in conn.execute(query, params)]
    except sqlite3.Error as exc:
        logger.warning("Lecture impossible (%s) : %s", query.split()[1] if query else "?", exc)
        return []


def read_signals(limit: int = 2000) -> List[Dict]:
    return _rows(f"SELECT * FROM {SIGNALS_TABLE} ORDER BY id DESC LIMIT ?", (limit,))


def read_pathologies() -> List[Dict]:
    return _rows("SELECT * FROM pathologies ORDER BY risk_score DESC, label ASC")


def read_treatments(pathology_code: Optional[str] = None) -> List[Dict]:
    if pathology_code:
        return _rows(
            "SELECT * FROM treatments WHERE pathology_code = ? "
            "ORDER BY phase_rank DESC, name ASC", (pathology_code,)
        )
    return _rows("SELECT * FROM treatments ORDER BY phase_rank DESC, pathology_code, name")


def read_latest_investment_plan() -> Optional[Dict]:
    rows = _rows("SELECT * FROM investment_plan ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else None


def read_latest_competition() -> Optional[Dict]:
    rows = _rows("SELECT * FROM competition ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else None


def read_runs(limit: int = 40) -> List[Dict]:
    return _rows("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))


def last_run_at(stage: str) -> Optional[str]:
    """Horodatage de la dernière exécution réussie d'une étape."""
    rows = _rows(
        "SELECT finished_at FROM runs WHERE stage = ? AND status = 'ok' "
        "ORDER BY id DESC LIMIT 1", (stage,)
    )
    return rows[0]["finished_at"] if rows else None
