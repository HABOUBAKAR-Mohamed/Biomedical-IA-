from __future__ import annotations

import streamlit as st


"""
Tableau de bord - Veille IA biomédicale temps réel.
"""


import logging
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st

import charts
import claude_client
import load
import pipeline
import theme
from analysis import from_json
from config import (
    AUTO_REFRESH_SECONDS,
    CLAUDE_MODEL,
    HIGH_RISK_PATHOLOGIES,
    INVESTMENT_HORIZONS,
    RISK_TIERS,
    STALE_AFTER_MINUTES,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")

st.set_page_config(
    page_title="Veille IA biomédicale — temps réel",
    layout="wide",
    page_icon="",
    initial_sidebar_state="expanded",
)

theme.inject_css()
load.init_db()


# ===========================================================================
# Lectures mises en cache (TTL court : les données doivent rester fraîches)
# ===========================================================================
@st.cache_data(ttl=15, show_spinner=False)
def get_signals() -> pd.DataFrame:
    return pd.DataFrame(load.read_signals())


@st.cache_data(ttl=15, show_spinner=False)
def get_pathologies() -> pd.DataFrame:
    return pd.DataFrame(load.read_pathologies())


@st.cache_data(ttl=15, show_spinner=False)
def get_treatments() -> pd.DataFrame:
    return pd.DataFrame(load.read_treatments())


@st.cache_data(ttl=15, show_spinner=False)
def get_plan() -> dict | None:
    return load.read_latest_investment_plan()


@st.cache_data(ttl=15, show_spinner=False)
def get_competition() -> dict | None:
    return load.read_latest_competition()


@st.cache_data(ttl=15, show_spinner=False)
def get_runs() -> pd.DataFrame:
    return pd.DataFrame(load.read_runs())


def refresh_all() -> None:
    """Vide les caches de lecture après une écriture en base."""
    st.cache_data.clear()


def minutes_since(iso_timestamp: str | None) -> float | None:
    """Âge en minutes d'un horodatage ISO, ou None s'il est absent/illisible."""
    if not iso_timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(iso_timestamp)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 60


def freshness(stage: str) -> tuple[str, str]:
    """Libellé et état de fraîcheur d'une étape : (texte, 'live'|'stale'|'off')."""
    age = minutes_since(load.last_run_at(stage))
    if age is None:
        return "jamais collecté", "off"
    if age < 1:
        return "à l'instant", "live"
    if age < 60:
        label = f"il y a {age:.0f} min"
    elif age < 1440:
        label = f"il y a {age / 60:.0f} h"
    else:
        label = f"il y a {age / 1440:.0f} j"
    return label, ("live" if age <= STALE_AFTER_MINUTES else "stale")


def stage_pill(stage: str, name: str) -> str:
    """Badge de fraîcheur d'une étape."""
    label, state = freshness(stage)
    tone = {"live": "good", "stale": "warning", "off": ""}[state]
    icon = {"live": "●", "stale": "▲", "off": "○"}[state]
    return theme.badge(f"{name} · {label}", tone, icon)


# ===========================================================================
# Panneau latéral
# ===========================================================================
def render_sidebar() -> dict:
    """Panneau de contrôle. Retourne les préférences d'affichage."""
    st.sidebar.markdown("### Veille IA biomédicale")
    st.sidebar.caption("PubMed · ClinicalTrials.gov · openFDA · Claude")

    # --- Clé API Claude ----------------------------------------------------
    theme.sidebar_heading("Moteur d'analyse — Claude")

    if claude_client.is_enabled():
        st.sidebar.success(
            f"Clé active ({claude_client.key_source()})\n\n"
            f"`{claude_client.masked_key()}`\n\nModèle : `{CLAUDE_MODEL}`"
        )
    else:
        st.sidebar.warning(
            "Aucune clé Claude détectée. La collecte des données publiques "
            "fonctionne, mais l'analyse IA, le plan d'investissement et la veille "
            "concurrentielle sont désactivés."
        )

    with st.sidebar.expander("Configurer la clé API", expanded=not claude_client.is_enabled()):
        entered = st.text_input(
            "ANTHROPIC_API_KEY",
            type="password",
            placeholder="sk-ant-…",
            help="Utilisée uniquement pour cette session. Pour la rendre permanente, "
                 "placez-la dans un fichier .env à la racine du projet.",
        )
        col_a, col_b = st.columns(2)
        if col_a.button("Activer", use_container_width=True, disabled=not entered):
            claude_client.set_runtime_key(entered)
            st.rerun()
        if col_b.button("Tester", use_container_width=True):
            ok, message = claude_client.check_credentials()
            (st.success if ok else st.error)(message)

    # --- Collecte temps réel ------------------------------------------------
    theme.sidebar_heading("Collecte temps réel")
    ai_off = not claude_client.is_enabled()

    if st.sidebar.button("Tout actualiser", use_container_width=True, type="primary"):
        run_stage("all")
    if st.sidebar.button("Signaux (PubMed + essais)", use_container_width=True):
        run_stage("signals")
    if st.sidebar.button("Pathologies & traitements", use_container_width=True):
        run_stage("pathologies")
    if st.sidebar.button(
        "Concurrence agents IA", use_container_width=True, disabled=ai_off,
        help="Nécessite une clé Claude : cette étape s'appuie sur la recherche web." if ai_off else None,
    ):
        run_stage("competition")
    if st.sidebar.button(
        "Plan d'investissement", use_container_width=True, disabled=ai_off,
        help="Nécessite une clé Claude." if ai_off else None,
    ):
        run_stage("investment")

    # --- Paramètres du plan -------------------------------------------------
    theme.sidebar_heading("Paramètres du plan")
    horizon = st.sidebar.selectbox("Horizon d'investissement", INVESTMENT_HORIZONS, index=1)
    capital = st.sidebar.text_input(
        "Contrainte de capital (facultatif)",
        placeholder="ex. 20 M€, amorçage et série A",
        help="Transmise à l'analyse pour calibrer le phasage du déploiement.",
    )
    st.session_state["horizon"] = horizon
    st.session_state["capital"] = capital

    # --- Affichage ----------------------------------------------------------
    theme.sidebar_heading("Affichage")
    auto = st.sidebar.toggle(
        "Rafraîchissement automatique",
        value=False,
        help=f"Recharge l'affichage toutes les {AUTO_REFRESH_SECONDS} s. "
             "Ne relance aucune collecte : les appels API restent manuels.",
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Le pipeline s'exécute aussi hors interface :\n"
        "`python pipeline.py all --horizon \"3 ans\"`"
    )
    return {"auto_refresh": auto}


def run_stage(stage: str) -> None:
    """Déclenche une étape du pipeline avec retour d'état à l'écran."""
    horizon = st.session_state.get("horizon", "3 ans")
    capital = st.session_state.get("capital", "")

    labels = {
        "all": "Actualisation complète",
        "signals": "Collecte des signaux",
        "pathologies": "Pathologies et traitements",
        "competition": "Veille concurrentielle",
        "investment": "Plan d'investissement",
    }

    with st.spinner(f"{labels[stage]} en cours — interrogation des sources en temps réel…"):
        if stage == "all":
            outcome = pipeline.run_full_pipeline(horizon, capital)
            refresh_all()
            failed = outcome["_summary"]["failed_stages"]
            if failed:
                st.warning(
                    "Actualisation terminée avec des étapes en échec : "
                    + ", ".join(failed)
                    + ". Détails dans l'onglet « Journal »."
                )
            else:
                st.success("Actualisation complète terminée.")
            return

        runner = {
            "signals": pipeline.run_signals_stage,
            "pathologies": pipeline.run_pathologies_stage,
            "competition": pipeline.run_competition_stage,
            "investment": lambda: pipeline.run_investment_stage(horizon, capital),
        }[stage]
        result = runner()

    refresh_all()
    if result.get("ok"):
        st.success(f"{labels[stage]} terminée — {_describe(result)}")
    else:
        st.error(f"{labels[stage]} en échec : {result.get('error', 'motif inconnu')}")


def _describe(result: dict) -> str:
    """Résumé lisible du retour d'une étape."""
    stage = result.get("stage")
    if stage == "signals":
        return (f"{result['collected']} élément(s) collecté(s), {result['inserted']} nouveau(x), "
                f"{result['duplicates']} doublon(s) ignoré(s)")
    if stage == "pathologies":
        return (f"{result['pathologies']} pathologie(s), {result['trials']} essai(s) parcouru(s), "
                f"{result['treatments']} traitement(s) stocké(s)")
    if stage == "competition":
        return (f"{result['players']} acteur(s), {result['searches']} recherche(s) web, "
                f"{result['sources']} source(s) citée(s)")
    if stage == "investment":
        return (f"horizon {result['horizon']}, {result['allocations']} ligne(s) d'allocation, "
                f"confiance {result['confidence']}/5")
    return "terminé"


# ===========================================================================
# Onglet 1 — Vue d'ensemble
# ===========================================================================
def tab_overview(signals: pd.DataFrame, pathos: pd.DataFrame, treatments: pd.DataFrame) -> None:
    if pathos.empty and signals.empty:
        theme.empty_state(
            "Aucune donnée pour le moment. Lancez « ⚡ Tout actualiser » dans le "
            "panneau latéral pour interroger PubMed, ClinicalTrials.gov et openFDA "
            "en temps réel."
        )
        return

    critical = int(pathos["risk_tier"].isin(["Critique", "Très élevé"]).sum()) if not pathos.empty else 0
    emerging = int((treatments["is_emerging"] == 1).sum()) if not treatments.empty else 0
    approved = int((treatments["source"] == "openFDA").sum()) if not treatments.empty else 0
    active_trials = int(pathos["trials_active"].sum()) if not pathos.empty else 0

    col_hero, col_kpis = st.columns([1, 3], gap="large")
    with col_hero:
        theme.hero(
            f"{len(treatments):,}".replace(",", " "),
            "Traitements cartographiés",
            f"sur {len(pathos)} pathologie(s) à risque élevé suivie(s)",
        )
    with col_kpis:
        theme.tile_row([
            {"label": "Risque critique ou très élevé", "value": str(critical),
             "delta": f"sur {len(pathos)} analysée(s)" if not pathos.empty else "",
             "tone": "crit" if critical else ""},
            {"label": "Essais actifs suivis", "value": f"{active_trials:,}".replace(",", " ")},
            {"label": "Traitements émergents", "value": str(emerging),
             "delta": "phase 1–2", "tone": ""},
            {"label": "Traitements homologués", "value": str(approved),
             "delta": "notices FDA", "tone": "up" if approved else ""},
        ])

    theme.section(
        "Cartographie du risque",
        "Chaque pathologie est positionnée par son score de risque composite et "
        "par l'ampleur de son besoin médical non couvert. La taille du point "
        "traduit le volume d'essais cliniques en cours.",
    )
    if pathos.empty:
        theme.empty_state("Lancez « 🩺 Pathologies & traitements » pour constituer la cartographie.")
    else:
        left, right = st.columns([3, 2], gap="large")
        with left:
            st.altair_chart(charts.risk_positioning(pathos), use_container_width=True)
        with right:
            st.altair_chart(charts.risk_tier_counts(pathos), use_container_width=True)

    theme.section("Activité de veille")
    col1, col2 = st.columns(2, gap="large")
    with col1:
        if treatments.empty:
            theme.empty_state("Aucun traitement extrait pour l'instant.")
        else:
            st.altair_chart(charts.treatments_by_modality(treatments), use_container_width=True)
    with col2:
        if signals.empty:
            theme.empty_state("Aucun signal collecté pour l'instant.")
        else:
            st.altair_chart(charts.signals_over_time(signals), use_container_width=True)


# ===========================================================================
# Onglet 2 — Pathologies à risque élevé
# ===========================================================================
def tab_pathologies(pathos: pd.DataFrame, treatments: pd.DataFrame) -> None:
    if pathos.empty:
        theme.empty_state(
            "Aucune pathologie analysée. Le bouton « 🩺 Pathologies & traitements » "
            f"parcourt les {len(HIGH_RISK_PATHOLOGIES)} pathologies du catalogue, "
            "extrait leurs essais, interventions et traitements homologués, puis "
            "les fait analyser."
        )
        return

    st.markdown(
        f'<div style="margin-bottom:.6rem">{stage_pill("pathologies", "Dernière extraction")}</div>',
        unsafe_allow_html=True,
    )

    # --- Filtres, en une rangée au-dessus des données ----------------------
    f1, f2, f3 = st.columns([2, 2, 1.4])
    with f1:
        tiers = st.multiselect(
            "Niveau de risque", RISK_TIERS,
            default=[t for t in RISK_TIERS if t in set(pathos["risk_tier"])],
        )
    with f2:
        families = sorted(pathos["family"].dropna().unique().tolist())
        picked_families = st.multiselect("Famille clinique", families, default=families)
    with f3:
        min_score = st.slider("Score de risque minimum", 0, 100, 0, step=5)

    view = pathos[
        pathos["risk_tier"].isin(tiers)
        & pathos["family"].isin(picked_families)
        & (pathos["risk_score"] >= min_score)
    ].copy()

    if view.empty:
        theme.empty_state("Aucune pathologie ne correspond à ces filtres.")
        return

    st.caption(f"{len(view)} pathologie(s) affichée(s) sur {len(pathos)}")

    theme.section("Classement par score de risque")
    st.altair_chart(charts.risk_ranking(view), use_container_width=True)

    theme.section(
        "Fiches détaillées",
        "Niveau de risque, standard de soin, thérapies émergentes et besoin non "
        "couvert, produits à partir du dossier de preuves extrait en temps réel.",
    )

    for _, row in view.iterrows():
        role, _icon = theme.RISK_STYLE.get(row["risk_tier"], ("warning", "▲"))
        degraded = row.get("analysis_status") == "degraded"

        with st.expander(
            f"{theme.risk_icon(row['risk_tier'])}  {row['label']} — "
            f"{row['risk_tier']} ({row['risk_score']}/100) · {row['family']}"
        ):
            if degraded:
                st.info(
                    "Fiche en mode dégradé : niveau de risque de référence du "
                    "catalogue, sans analyse IA. Les compteurs d'essais et de "
                    "traitements proviennent bien de l'extraction temps réel."
                )

            badges = (
                theme.risk_badge(row["risk_tier"])
                + theme.badge(f"Besoin non couvert {row['unmet_need_score']}/5")
                + theme.badge(f"Attractivité {row['investment_attractiveness']}/5")
                + theme.badge(f"Maturité IA : {row['ai_maturity'] or '—'}")
                + theme.badge(f"{row['trials_active']} essai(s) actif(s)")
                + theme.badge(f"{row['trials_late_phase']} en phase 3+")
                + theme.badge(f"{row['treatments_total']} traitement(s)")
            )
            st.markdown(badges, unsafe_allow_html=True)

            st.markdown(
                f'<p style="margin-top:.7rem"><strong>Pourquoi ce niveau de risque</strong><br>'
                f'{theme._esc(row["risk_rationale"])}</p>',
                unsafe_allow_html=True,
            )

            c1, c2 = st.columns(2, gap="large")
            with c1:
                theme.card("Charge de morbidité", f"<p>{theme._esc(row['burden_note'])}</p>", role)
                theme.card("Angle mort diagnostique", f"<p>{theme._esc(row['diagnostic_gap'])}</p>")
            with c2:
                theme.card("Standard de soin", f"<p>{theme._esc(row['standard_of_care'])}</p>")
                theme.card("Besoin médical non couvert", f"<p>{theme._esc(row['unmet_need'])}</p>")

            theme.card("Opportunité pour un agent IA", f"<p>{theme._esc(row['ai_opportunity'])}</p>")

            emerging = from_json(row.get("emerging_therapies"), [])
            if emerging:
                st.markdown("**Thérapies émergentes identifiées**")
                st.dataframe(
                    pd.DataFrame(emerging).rename(columns={
                        "name": "Traitement", "modality": "Modalité",
                        "stage": "Stade", "why_it_matters": "Intérêt",
                    }),
                    use_container_width=True, hide_index=True,
                )

            watchpoints = from_json(row.get("watchpoints"), [])
            if watchpoints:
                st.markdown("**À surveiller**")
                st.markdown("\n".join(f"- {w}" for w in watchpoints))

            patho_treatments = treatments[treatments["pathology_code"] == row["code"]]
            st.caption(
                f"{len(patho_treatments)} traitement(s) rattaché(s) — "
                "détail complet dans l'onglet « Traitements »."
            )


# ===========================================================================
# Onglet 3 — Traitements
# ===========================================================================
def tab_treatments(treatments: pd.DataFrame, pathos: pd.DataFrame) -> None:
    if treatments.empty:
        theme.empty_state(
            "Aucun traitement extrait. L'extraction déplie les interventions "
            "thérapeutiques des essais ClinicalTrials.gov et les croise avec les "
            "notices de traitements homologués d'openFDA."
        )
        return

    labels = {p["code"]: p["label"] for p in HIGH_RISK_PATHOLOGIES}
    view = treatments.copy()
    view["Pathologie"] = view["pathology_code"].map(labels).fillna(view["pathology_code"])

    theme.tile_row([
        {"label": "Traitements suivis", "value": f"{len(view):,}".replace(",", " ")},
        {"label": "Homologués", "value": str(int((view["source"] == "openFDA").sum())),
         "delta": "notices FDA", "tone": "up"},
        {"label": "Émergents (phase 1–2)", "value": str(int((view["is_emerging"] == 1).sum()))},
        {"label": "Phase 3 et plus", "value": str(int((view["phase_rank"] >= 3).sum()))},
        {"label": "Modalités distinctes", "value": str(view["modality"].nunique())},
    ])

    st.markdown(
        f'<div style="margin:.8rem 0 .2rem">{stage_pill("pathologies", "Dernière extraction")}</div>',
        unsafe_allow_html=True,
    )

    theme.section(
        "Répartition des traitements",
        "Par pathologie et par stade de développement : la part émergente indique "
        "où le pipeline se renouvelle, la part homologuée où le standard est établi.",
    )
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        st.altair_chart(charts.treatments_by_pathology(view), use_container_width=True)
    with c2:
        st.altair_chart(charts.treatments_by_phase(view), use_container_width=True)

    theme.section("Catalogue des traitements")

    f1, f2, f3, f4 = st.columns([2, 2, 2, 1.3])
    with f1:
        picked = st.multiselect(
            "Pathologie", sorted(view["Pathologie"].unique()),
            default=[],
            help="Vide = toutes les pathologies.",
        )
    with f2:
        modalities = st.multiselect("Modalité", sorted(view["modality"].dropna().unique()), default=[])
    with f3:
        sources = st.multiselect("Source", sorted(view["source"].dropna().unique()), default=[])
    with f4:
        only_emerging = st.toggle("Émergents seuls", value=False)

    filtered = view
    if picked:
        filtered = filtered[filtered["Pathologie"].isin(picked)]
    if modalities:
        filtered = filtered[filtered["modality"].isin(modalities)]
    if sources:
        filtered = filtered[filtered["source"].isin(sources)]
    if only_emerging:
        filtered = filtered[filtered["is_emerging"] == 1]

    st.caption(f"{len(filtered)} traitement(s) affiché(s) sur {len(view)}")

    if filtered.empty:
        theme.empty_state("Aucun traitement ne correspond à ces filtres.")
        return

    table = filtered[[
        "Pathologie", "name", "modality", "phase", "evidence_level",
        "status", "sponsor", "source", "link",
    ]].rename(columns={
        "name": "Traitement", "modality": "Modalité", "phase": "Stade",
        "evidence_level": "Niveau de preuve", "status": "Statut",
        "sponsor": "Promoteur", "source": "Source", "link": "Référence",
    }).sort_values(["Pathologie", "Traitement"])

    st.dataframe(
        table, use_container_width=True, hide_index=True, height=460,
        column_config={
            "Référence": st.column_config.LinkColumn("Référence", display_text="Ouvrir"),
        },
    )

    st.download_button(
        "⬇ Exporter la sélection (CSV)",
        table.to_csv(index=False).encode("utf-8-sig"),
        file_name="traitements_pathologies_risque_eleve.csv",
        mime="text/csv",
    )

    with st.expander("Descriptions détaillées des traitements sélectionnés"):
        for _, row in filtered.head(60).iterrows():
            if not row.get("detail"):
                continue
            st.markdown(
                f"**{theme._esc(row['name'])}** — {theme._esc(row['Pathologie'])}  \n"
                f"<span class='vs-src'>{theme._esc(row['detail'])}</span>",
                unsafe_allow_html=True,
            )


# ===========================================================================
# Onglet 4 — Signaux & analyse IA
# ===========================================================================
def tab_signals(signals: pd.DataFrame) -> None:
    if signals.empty:
        theme.empty_state(
            "Aucun signal collecté. Le bouton « 📡 Signaux » interroge PubMed et "
            "ClinicalTrials.gov, puis fait qualifier chaque élément par Claude."
        )
        return

    strong = int((signals["impact_score"] >= 4).sum())
    invest_signals = int((signals.get("investment_score", pd.Series(dtype=int)) >= 4).sum())

    theme.tile_row([
        {"label": "Signaux qualifiés", "value": f"{len(signals):,}".replace(",", " ")},
        {"label": "Impact fort (≥ 4/5)", "value": str(strong),
         "delta": f"{strong / len(signals):.0%} du fil", "tone": ""},
        {"label": "Signaux de marché (≥ 4/5)", "value": str(invest_signals)},
        {"label": "Sources intégrées", "value": str(signals["source"].nunique())},
        {"label": "Sujets suivis", "value": str(signals["topic"].nunique())},
    ])

    st.markdown(
        f'<div style="margin:.8rem 0 .2rem">{stage_pill("signals", "Dernière collecte")}</div>',
        unsafe_allow_html=True,
    )

    theme.section("Structure du fil")
    c1, c2, c3 = st.columns([2.2, 1.6, 1.6], gap="large")
    with c1:
        st.altair_chart(charts.signals_by_category(signals), use_container_width=True)
    with c2:
        st.altair_chart(charts.impact_distribution(signals), use_container_width=True)
    with c3:
        st.altair_chart(charts.signals_by_source(signals), use_container_width=True)

    theme.section("Fil des signaux et synthèses IA")

    f1, f2, f3 = st.columns([2, 2, 1.5])
    with f1:
        cats = sorted(signals["categorie"].dropna().unique().tolist())
        picked_cats = st.multiselect("Domaine", cats, default=[], help="Vide = tous les domaines.")
    with f2:
        srcs = sorted(signals["source"].dropna().unique().tolist())
        picked_srcs = st.multiselect("Source", srcs, default=[])
    with f3:
        min_impact = st.slider("Impact minimum", 1, 5, 1)

    view = signals[signals["impact_score"] >= min_impact]
    if picked_cats:
        view = view[view["categorie"].isin(picked_cats)]
    if picked_srcs:
        view = view[view["source"].isin(picked_srcs)]

    st.caption(f"{len(view)} signal(aux) affiché(s) sur {len(signals)}")

    for _, row in view.head(80).iterrows():
        with st.expander(
            f"[{row['source']}] {row['title'][:130]} — impact {row['impact_score']}/5"
        ):
            meta = (
                theme.badge(row["categorie"] or "—")
                + theme.badge(f"Impact {row['impact_score']}/5")
                + theme.badge(f"Marché {row.get('investment_score', 0)}/5")
                + (theme.badge(f"Pathologie : {row['pathologie_liee']}")
                   if row.get("pathologie_liee") and row["pathologie_liee"] != "Aucune" else "")
            )
            st.markdown(meta, unsafe_allow_html=True)
            st.markdown(
                f'<div class="vs-quote" style="margin-top:.7rem">{theme._esc(row["resume_ia"])}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<p class='meta' style='margin-top:.6rem'>Mots-clés : {theme._esc(row['mots_cles'])} · "
                f"Date/statut : {theme._esc(row['published'])} · "
                f"Sujet suivi : {theme._esc(row['topic'])}</p>",
                unsafe_allow_html=True,
            )
            st.markdown(f"[Accéder à la source officielle]({row['link']})")


# ===========================================================================
# Onglet 5 — Plan d'investissement
# ===========================================================================
def tab_investment(plan: dict | None) -> None:
    theme.section(
        "Plan d'investissement",
        "Construit à partir du portefeuille de pathologies analysées, de la "
        "cartographie concurrentielle et d'une recherche web du marché conduite "
        "au moment de la génération.",
    )

    if not claude_client.is_enabled():
        st.warning(
            "Cette vue exige une clé API Claude : le plan repose sur une recherche "
            "web temps réel. Renseignez la clé dans le panneau latéral."
        )

    if not plan:
        theme.empty_state(
            "Aucun plan généré. Lancez « 💰 Plan d'investissement » dans le panneau "
            "latéral — pensez à exécuter d'abord l'analyse des pathologies, qui "
            "constitue le portefeuille de départ."
        )
        return

    allocations = from_json(plan.get("allocations"), [])
    phases = from_json(plan.get("deployment_phases"), [])
    risks = from_json(plan.get("risks"), [])
    catalysts = from_json(plan.get("catalysts"), [])
    priorities = from_json(plan.get("priority_pathologies"), [])
    kpis = from_json(plan.get("kpis"), [])
    sources = from_json(plan.get("sources"), [])

    col_hero, col_kpis = st.columns([1, 3], gap="large")
    with col_hero:
        theme.hero(
            f"{plan.get('confidence', '—')}/5",
            "Confiance de l'analyse",
            f"horizon {plan.get('horizon', '—')}",
        )
    with col_kpis:
        theme.tile_row([
            {"label": "Segments alloués", "value": str(len(allocations))},
            {"label": "Pathologies prioritaires", "value": str(len(priorities))},
            {"label": "Catalyseurs suivis", "value": str(len(catalysts))},
            {"label": "Sources web citées", "value": str(len(sources)),
             "delta": f"{plan.get('searches', 0)} recherche(s)"},
        ])

    st.markdown(
        f'<div style="margin:.7rem 0">{stage_pill("investment", "Plan généré")}'
        f'{theme.badge(f"Modèle : {plan.get('model') or '—'}")}</div>',
        unsafe_allow_html=True,
    )

    theme.card("Thèse d'investissement", f"<p>{theme._esc(plan.get('thesis', ''))}</p>")
    theme.card("Contexte de marché", f"<p>{theme._esc(plan.get('market_context', ''))}</p>")

    if allocations:
        theme.section("Allocation par segment", "Répartition en pourcentage du capital.")
        st.altair_chart(charts.allocation_split(allocations), use_container_width=True)

        alloc_table = pd.DataFrame(allocations)
        alloc_table["target_pathologies"] = alloc_table["target_pathologies"].apply(
            lambda v: ", ".join(v) if isinstance(v, list) else v
        )
        alloc_table["example_players"] = alloc_table["example_players"].apply(
            lambda v: ", ".join(v) if isinstance(v, list) else v
        )
        st.dataframe(
            alloc_table.rename(columns={
                "segment": "Segment", "weight_pct": "Poids (%)", "rationale": "Justification",
                "risk_level": "Risque", "time_to_value": "Délai de retour",
                "target_pathologies": "Pathologies visées", "example_players": "Acteurs cités",
            }).sort_values("Poids (%)", ascending=False),
            use_container_width=True, hide_index=True,
        )

    if phases:
        theme.section("Phasage du déploiement du capital")
        st.altair_chart(charts.deployment_phases(phases), use_container_width=True)
        for phase in phases:
            theme.card(
                f"{phase.get('phase', '')} — {phase.get('share_pct', 0)} % du capital",
                f"<p>{theme._esc(phase.get('focus', ''))}</p>"
                f"<p class='meta'>Déclencheur : {theme._esc(phase.get('trigger', ''))}</p>",
            )

    if priorities:
        theme.section("Pathologies prioritaires")
        for item in priorities:
            theme.card(
                item.get("pathology", ""),
                f"<p>{theme._esc(item.get('why', ''))}</p>"
                f"<p class='meta'>Angle d'entrée : {theme._esc(item.get('entry_angle', ''))}</p>",
            )

    col_cat, col_risk = st.columns(2, gap="large")
    with col_cat:
        if catalysts:
            theme.section("Catalyseurs")
            for cat in catalysts:
                theme.card(
                    cat.get("label", ""),
                    f"<p>{theme._esc(cat.get('impact', ''))}</p>"
                    f"<p class='meta'>Échéance : {theme._esc(cat.get('timing', ''))}</p>",
                )
    with col_risk:
        if risks:
            theme.section("Risques de la thèse")
            severity_role = {"Faible": "good", "Moyenne": "warning",
                             "Élevée": "serious", "Critique": "critical"}
            for risk in risks:
                role = severity_role.get(risk.get("severity", ""), "warning")
                theme.card(
                    risk.get("label", ""),
                    theme.badge(f"Gravité : {risk.get('severity', '—')}", role,
                                theme.risk_icon({"Faible": "Modéré", "Moyenne": "Élevé",
                                                 "Élevée": "Très élevé",
                                                 "Critique": "Critique"}.get(risk.get("severity", ""), "Élevé")))
                    + f"<p style='margin-top:.5rem'>Atténuation : {theme._esc(risk.get('mitigation', ''))}</p>",
                    role,
                )

    if kpis:
        theme.section("Indicateurs de suivi de la thèse")
        st.markdown("\n".join(f"- {k}" for k in kpis))

    if plan.get("disclaimer"):
        st.info(plan["disclaimer"])

    render_sources(sources, "Sources web consultées pour ce plan")


# ===========================================================================
# Onglet 6 — Concurrence des agents IA biomédicaux
# ===========================================================================
def tab_competition(competition: dict | None) -> None:
    theme.section(
        "Agents IA biomédicaux — existants et à venir",
        "Cartographie établie par recherche web au moment de l'analyse : chaque "
        "acteur est adossé à une source consultée.",
    )

    if not claude_client.is_enabled():
        st.warning(
            "Cette vue exige une clé API Claude : la cartographie repose sur une "
            "recherche web temps réel. Renseignez la clé dans le panneau latéral."
        )

    if not competition:
        theme.empty_state(
            "Aucune cartographie disponible. Lancez « 🏁 Concurrence agents IA » "
            "dans le panneau latéral."
        )
        return

    players = from_json(competition.get("players"), [])
    upcoming = from_json(competition.get("upcoming_signals"), [])
    whitespace = from_json(competition.get("whitespace"), [])
    sources = from_json(competition.get("sources"), [])

    if not players:
        theme.empty_state("La cartographie ne contient aucun acteur exploitable.")
        return

    df = pd.DataFrame(players)
    existing = int((df["status"] == "Existant").sum())
    coming = int(df["status"].isin(["En lancement", "Annoncé / à venir"]).sum())
    major = int(df["threat_level"].isin(["Élevée", "Majeure"]).sum())
    funded = df[df["funding_musd"] > 0]

    col_hero, col_kpis = st.columns([1, 3], gap="large")
    with col_hero:
        theme.hero(str(len(df)), "Acteurs cartographiés",
                   f"informations datées : {competition.get('as_of') or 'non précisé'}")
    with col_kpis:
        theme.tile_row([
            {"label": "Déjà opérationnels", "value": str(existing)},
            {"label": "À venir ou en lancement", "value": str(coming), "delta": "pipeline concurrent"},
            {"label": "Menace élevée ou majeure", "value": str(major),
             "tone": "crit" if major else ""},
            {"label": "Financement documenté",
             "value": f"{funded['funding_musd'].sum():,.0f} M$".replace(",", " ")
                      if not funded.empty else "n.d.",
             "delta": f"{len(funded)}/{len(df)} acteurs"},
        ])

    st.markdown(
        f'<div style="margin:.7rem 0">{stage_pill("competition", "Cartographie")}'
        f'{theme.badge(f"Confiance {competition.get('confidence', '—')}/5")}'
        f'{theme.badge(f"{competition.get('searches', 0)} recherche(s) web")}</div>',
        unsafe_allow_html=True,
    )

    theme.card("Synthèse du marché", f"<p>{theme._esc(competition.get('market_summary', ''))}</p>")

    theme.section("Positionnement des acteurs")
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        st.altair_chart(charts.players_by_segment(df), use_container_width=True)
    with c2:
        st.altair_chart(charts.threat_distribution(df), use_container_width=True)

    if not funded.empty:
        theme.section(
            "Financement cumulé documenté",
            "Seuls les acteurs dont le financement a pu être sourcé apparaissent ; "
            "les montants non documentés ne sont pas estimés.",
        )
        st.altair_chart(charts.funding_ranking(funded), use_container_width=True)

    theme.section("Tableau comparatif")
    f1, f2 = st.columns(2)
    with f1:
        picked_status = st.multiselect(
            "Statut", sorted(df["status"].unique()), default=sorted(df["status"].unique())
        )
    with f2:
        picked_seg = st.multiselect("Segment", sorted(df["segment"].unique()), default=[])

    view = df[df["status"].isin(picked_status)]
    if picked_seg:
        view = view[view["segment"].isin(picked_seg)]

    st.dataframe(
        view[[
            "name", "status", "org_type", "segment", "country", "funding_musd",
            "key_product", "differentiator", "moat", "threat_level", "recent_move",
            "evidence_url",
        ]].rename(columns={
            "name": "Acteur", "status": "Statut", "org_type": "Type",
            "segment": "Segment", "country": "Pays", "funding_musd": "Financement (M$)",
            "key_product": "Produit clé", "differentiator": "Différenciateur",
            "moat": "Barrière à l'entrée", "threat_level": "Menace",
            "recent_move": "Fait marquant récent", "evidence_url": "Source",
        }),
        use_container_width=True, hide_index=True, height=440,
        column_config={
            "Source": st.column_config.LinkColumn("Source", display_text="Ouvrir"),
            "Financement (M$)": st.column_config.NumberColumn("Financement (M$)", format="%.0f"),
        },
    )

    if upcoming:
        theme.section("Signaux attendus", "Lancements, homologations et levées à surveiller.")
        for signal in upcoming:
            body = (
                f"<p>{theme._esc(signal.get('why_it_matters', ''))}</p>"
                f"<p class='meta'>Échéance annoncée : {theme._esc(signal.get('expected', ''))}</p>"
            )
            if signal.get("source_url"):
                body += f"<p class='vs-src'>{theme._esc(signal['source_url'])}</p>"
            theme.card(signal.get("label", ""), body)

    if whitespace:
        theme.section("Espaces de marché peu disputés")
        st.markdown("\n".join(f"- {w}" for w in whitespace))

    if competition.get("consolidation_outlook"):
        theme.card("Perspective de consolidation",
                   f"<p>{theme._esc(competition['consolidation_outlook'])}</p>")

    render_sources(sources, "Sources web consultées pour cette cartographie")


# ===========================================================================
# Onglet 7 — Journal
# ===========================================================================
def tab_journal(runs: pd.DataFrame) -> None:
    theme.section("Journal d'exécution", "Historique des collectes et des analyses.")

    if runs.empty:
        theme.empty_state("Aucune exécution enregistrée.")
        return

    total_in = int(runs["input_tokens"].sum())
    total_out = int(runs["output_tokens"].sum())
    # Tarif Opus 5 : 5 $/M jetons en entrée, 25 $/M en sortie
    cost = total_in / 1e6 * 5.0 + total_out / 1e6 * 25.0

    theme.tile_row([
        {"label": "Exécutions journalisées", "value": str(len(runs))},
        {"label": "En échec", "value": str(int((runs["status"] == "error").sum())),
         "tone": "crit" if int((runs["status"] == "error").sum()) else ""},
        {"label": "Jetons en entrée", "value": f"{total_in:,}".replace(",", " ")},
        {"label": "Jetons en sortie", "value": f"{total_out:,}".replace(",", " ")},
        {"label": "Coût IA indicatif", "value": f"{cost:.2f} $",
         "delta": "tarif Opus 5"},
    ])

    st.markdown("<div style='height:.9rem'></div>", unsafe_allow_html=True)
    st.dataframe(
        runs[["finished_at", "stage", "status", "items", "detail",
              "input_tokens", "output_tokens"]].rename(columns={
            "finished_at": "Terminé le", "stage": "Étape", "status": "État",
            "items": "Éléments", "detail": "Détail",
            "input_tokens": "Jetons entrée", "output_tokens": "Jetons sortie",
        }),
        use_container_width=True, hide_index=True, height=420,
    )


# ===========================================================================
# Fragments communs
# ===========================================================================
def render_sources(sources: list, title: str) -> None:
    """Liste des sources web réellement consultées par le modèle."""
    if not sources:
        return
    with st.expander(f"{title} ({len(sources)})"):
        for src in sources:
            age = f" · {src['age']}" if src.get("age") else ""
            st.markdown(
                f"- [{theme._esc(src.get('title', src.get('url', '')))}]({src.get('url', '')})"
                f"<span class='vs-src'>{theme._esc(age)}</span>",
                unsafe_allow_html=True,
            )


# ===========================================================================
# Composition de la page
# ===========================================================================
prefs = render_sidebar()

signals_df = get_signals()
pathos_df = get_pathologies()
treatments_df = get_treatments()
plan_row = get_plan()
competition_row = get_competition()
runs_df = get_runs()

# La fraîcheur affichée en en-tête est celle de la collecte la plus récente
_ages = [
    (stage, minutes_since(load.last_run_at(stage)))
    for stage in ("signals", "pathologies", "competition", "investment")
]
_known = [age for _, age in _ages if age is not None]
if not _known:
    header_label, header_state = "aucune collecte", "off"
else:
    freshest = min(_known)
    header_state = "live" if freshest <= STALE_AFTER_MINUTES else "stale"
    header_label = (
        "données à jour" if freshest < 1
        else f"dernière collecte il y a {freshest:.0f} min" if freshest < 60
        else f"dernière collecte il y a {freshest / 60:.0f} h"
    )

theme.header(
    "Veille IA biomédicale en temps réel",
    "Pathologies à risque élevé, traitements homologués et expérimentaux, analyse "
    "Claude, plan d'investissement et concurrence des agents IA biomédicaux — "
    "adossés à PubMed, ClinicalTrials.gov et openFDA.",
    header_label,
    header_state,
)

st.markdown(
    "<div style='margin:-.4rem 0 1rem'>"
    + stage_pill("signals", "Signaux")
    + stage_pill("pathologies", "Pathologies")
    + stage_pill("competition", "Concurrence")
    + stage_pill("investment", "Investissement")
    + "</div>",
    unsafe_allow_html=True,
)

tabs = st.tabs([
    "Vue d'ensemble",
    f"Pathologies à risque ({len(pathos_df)})",
    f"Traitements ({len(treatments_df)})",
    f"Signaux & analyse IA ({len(signals_df)})",
    "Plan d'investissement",
    "Concurrence IA",
    "Journal",
])

with tabs[0]:
    tab_overview(signals_df, pathos_df, treatments_df)
with tabs[1]:
    tab_pathologies(pathos_df, treatments_df)
with tabs[2]:
    tab_treatments(treatments_df, pathos_df)
with tabs[3]:
    tab_signals(signals_df)
with tabs[4]:
    tab_investment(plan_row)
with tabs[5]:
    tab_competition(competition_row)
with tabs[6]:
    tab_journal(runs_df)


# ---------------------------------------------------------------------------
# Rafraîchissement automatique de l'affichage.
# Volontairement limité au rechargement des données déjà en base : relancer les
# appels API sur minuterie consommerait du crédit sans décision de l'utilisateur.
# ---------------------------------------------------------------------------
if prefs["auto_refresh"] and AUTO_REFRESH_SECONDS > 0:

    @st.fragment(run_every=AUTO_REFRESH_SECONDS)
    def _ticker() -> None:
        refresh_all()
        st.rerun(scope="app")

    _ticker()
