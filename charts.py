"""
Constructeurs de graphiques.

Chaque fonction retourne un graphique Altair déjà stylé ; `render()` l'affiche.
Le thème Streamlit est explicitement désactivé (`theme=None`) : il écraserait
sinon la palette validée définie dans `theme.py`.

Choix de forme, par le travail que le lecteur doit fournir :
  * comparer des magnitudes          -> barres, dégradé d'une seule teinte
  * distinguer des séries            -> barres empilées, palette catégorielle
  * échelle ordonnée (score, phase)  -> barres, rampe ordinale
  * niveau de risque / gravité       -> palette de statut + icône + libellé
  * positionnement sur deux mesures  -> nuage de points
  * évolution dans le temps          -> courbe (série unique, sans légende)
  * part d'un tout                   -> barre empilée à 100 %
"""

from __future__ import annotations

from typing import List, Sequence

import altair as alt
import pandas as pd
import streamlit as st

import theme
from config import RISK_TIERS

# Ordre d'empilement des stades de développement d'un traitement
STAGE_ORDER = ["Émergent (phase 1–2)", "Avancé (phase 3+)", "Homologué"]

# Échelles ordonnées : le rang porte le sens, donc rampe ordinale et non palette catégorielle
THREAT_ORDER = ["Faible", "Modérée", "Élevée", "Majeure"]


def render(chart: alt.Chart) -> None:
    """Affiche un graphique en conservant la palette du projet."""
    st.altair_chart(chart, width="stretch", theme=None)


def _bar_axis(title: str) -> alt.Axis:
    """Axe de valeurs : grille discrète, pas de domaine appuyé."""
    return alt.Axis(title=title, grid=True, tickCount=5, domain=False)


def _cat_axis(title: str = None) -> alt.Axis:
    """Axe de catégories : ni grille, ni graduations."""
    return alt.Axis(title=title, grid=False, ticks=False, domain=False, labelLimit=220)


def _empty(message: str) -> alt.Chart:
    """Substitut lisible quand il n'y a rien à tracer."""
    t = theme.tokens()
    return (
        alt.Chart(pd.DataFrame({"m": [message]}))
        .mark_text(align="center", color=t["muted"], fontSize=12, font=theme.FONT_STACK)
        .encode(text="m:N")
        .properties(height=120)
    )


def _ranked_bars(
    df: pd.DataFrame, value: str, label: str, value_title: str,
    label_title: str = None, height: int = 300, fmt: str = ",.0f",
) -> alt.Chart:
    """
    Barres horizontales classées, dégradé d'une seule teinte : la forme par
    défaut pour comparer des magnitudes. Valeurs étiquetées directement.
    """
    t = theme.tokens()
    order = df.sort_values(value, ascending=False)[label].tolist()

    base = alt.Chart(df).encode(
        y=alt.Y(f"{label}:N", sort=order, axis=_cat_axis(label_title)),
        x=alt.X(f"{value}:Q", axis=_bar_axis(value_title)),
        tooltip=[
            alt.Tooltip(f"{label}:N", title=label_title or "Catégorie"),
            alt.Tooltip(f"{value}:Q", title=value_title, format=fmt),
        ],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height={"band": 0.72}).encode(
        color=alt.Color(
            f"{value}:Q",
            scale=alt.Scale(range=theme.sequential_range()),
            legend=None,
        )
    )
    labels = base.mark_text(
        align="left", dx=6, fontSize=11, color=t["ink_2"], font=theme.FONT_STACK
    ).encode(text=alt.Text(f"{value}:Q", format=fmt))

    return theme.style(bars + labels, height=height)


# ===========================================================================
# Pathologies
# ===========================================================================
def risk_positioning(pathos: pd.DataFrame) -> alt.Chart:
    """
    Nuage de points : score de risque contre besoin médical non couvert.
    La couleur porte le niveau de risque (palette de statut), la taille le
    volume d'essais. Légende présente ; l'identité ne repose jamais sur la
    seule couleur, le survol donnant le libellé complet.
    """
    if pathos.empty:
        return _empty("Aucune pathologie analysée")

    t = theme.tokens()
    df = pathos.copy()
    df["Pathologie"] = df["label"]

    points = (
        alt.Chart(df)
        .mark_circle(opacity=0.9, stroke=t["surface"], strokeWidth=2)
        .encode(
            x=alt.X("risk_score:Q", title="Score de risque composite (0–100)",
                    scale=alt.Scale(domain=[0, 100], nice=False), axis=_bar_axis("Score de risque composite (0–100)")),
            y=alt.Y("unmet_need_score:Q", title="Besoin médical non couvert (1–5)",
                    scale=alt.Scale(domain=[0.5, 5.5], nice=False),
                    axis=alt.Axis(title="Besoin médical non couvert (1–5)", grid=True,
                                  values=[1, 2, 3, 4, 5], domain=False)),
            size=alt.Size("trials_total:Q", title="Essais suivis",
                          scale=alt.Scale(range=[90, 900]),
                          legend=alt.Legend(title="Essais suivis", symbolFillColor=t["muted"])),
            color=alt.Color("risk_tier:N", title="Niveau de risque",
                            scale=theme.risk_scale(),
                            sort=RISK_TIERS,
                            legend=alt.Legend(title="Niveau de risque")),
            tooltip=[
                alt.Tooltip("Pathologie:N", title="Pathologie"),
                alt.Tooltip("family:N", title="Famille"),
                alt.Tooltip("risk_tier:N", title="Niveau de risque"),
                alt.Tooltip("risk_score:Q", title="Score de risque"),
                alt.Tooltip("unmet_need_score:Q", title="Besoin non couvert (/5)"),
                alt.Tooltip("investment_attractiveness:Q", title="Attractivité (/5)"),
                alt.Tooltip("trials_total:Q", title="Essais suivis"),
                alt.Tooltip("treatments_total:Q", title="Traitements"),
                alt.Tooltip("ai_maturity:N", title="Maturité IA"),
            ],
        )
    )

    # Les quatre pathologies les plus à risque sont étiquetées directement :
    # étiqueter tous les points rendrait le nuage illisible.
    top = df.nlargest(4, "risk_score")
    labels = (
        alt.Chart(top)
        .mark_text(align="left", dx=12, dy=-10, fontSize=10,
                   color=t["ink_2"], font=theme.FONT_STACK)
        .encode(x="risk_score:Q", y="unmet_need_score:Q", text="Pathologie:N")
    )

    return theme.style(points + labels, height=340)


def risk_tier_counts(pathos: pd.DataFrame) -> alt.Chart:
    """
    Effectif par niveau de risque. La couleur de statut renforce un axe déjà
    étiqueté ; aucune légende n'est donc nécessaire (série unique).
    """
    if pathos.empty:
        return _empty("Aucune pathologie analysée")

    t = theme.tokens()
    counts = (
        pathos["risk_tier"].value_counts()
        .reindex(RISK_TIERS, fill_value=0)
        .rename_axis("Niveau").reset_index(name="Pathologies")
    )
    counts["Repère"] = counts["Niveau"].map(theme.risk_icon)

    base = alt.Chart(counts).encode(
        y=alt.Y("Niveau:N", sort=RISK_TIERS, axis=_cat_axis()),
        x=alt.X("Pathologies:Q", axis=_bar_axis("Pathologies")),
        tooltip=[
            alt.Tooltip("Niveau:N", title="Niveau de risque"),
            alt.Tooltip("Pathologies:Q", title="Pathologies"),
        ],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height={"band": 0.68}).encode(
        color=alt.Color("Niveau:N", scale=theme.risk_scale(), sort=RISK_TIERS, legend=None)
    )
    labels = base.mark_text(
        align="left", dx=6, fontSize=11, color=t["ink_2"], font=theme.FONT_STACK
    ).encode(text=alt.Text("Pathologies:Q"))

    return theme.style(
        (bars + labels).properties(title="Répartition par niveau de risque"), height=250
    )


def risk_ranking(pathos: pd.DataFrame) -> alt.Chart:
    """Classement des pathologies par score de risque, coloré par niveau."""
    if pathos.empty:
        return _empty("Aucune pathologie à classer")

    t = theme.tokens()
    df = pathos.copy().sort_values("risk_score", ascending=False)
    df["Pathologie"] = df["label"]
    order = df["Pathologie"].tolist()

    base = alt.Chart(df).encode(
        y=alt.Y("Pathologie:N", sort=order, axis=_cat_axis()),
        x=alt.X("risk_score:Q", scale=alt.Scale(domain=[0, 100], nice=False),
                axis=_bar_axis("Score de risque composite (0–100)")),
        tooltip=[
            alt.Tooltip("Pathologie:N", title="Pathologie"),
            alt.Tooltip("risk_tier:N", title="Niveau de risque"),
            alt.Tooltip("risk_score:Q", title="Score de risque"),
            alt.Tooltip("family:N", title="Famille"),
            alt.Tooltip("trials_active:Q", title="Essais actifs"),
            alt.Tooltip("treatments_total:Q", title="Traitements"),
        ],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height={"band": 0.74}).encode(
        color=alt.Color("risk_tier:N", title="Niveau de risque",
                        scale=theme.risk_scale(), sort=RISK_TIERS,
                        legend=alt.Legend(title="Niveau de risque"))
    )
    labels = base.mark_text(
        align="left", dx=6, fontSize=11, color=t["ink_2"], font=theme.FONT_STACK
    ).encode(text=alt.Text("risk_score:Q"))

    return theme.style(bars + labels, height=max(240, 26 * len(df)))


# ===========================================================================
# Traitements
# ===========================================================================
def _stage_bucket(row: pd.Series) -> str:
    """Regroupe un traitement en trois stades lisibles."""
    if row.get("source") == "openFDA":
        return "Homologué"
    return "Émergent (phase 1–2)" if row.get("is_emerging") == 1 else "Avancé (phase 3+)"


def treatments_by_pathology(treatments: pd.DataFrame, top: int = 14) -> alt.Chart:
    """
    Barres empilées : composition du pipeline thérapeutique par pathologie.
    Trois séries -> palette catégorielle, légende présente, séparateur de 2 px
    entre segments.
    """
    if treatments.empty:
        return _empty("Aucun traitement extrait")

    t = theme.tokens()
    df = treatments.copy()
    df["Stade"] = df.apply(_stage_bucket, axis=1)

    grouped = (
        df.groupby(["Pathologie", "Stade"]).size().reset_index(name="Traitements")
    )
    totals = grouped.groupby("Pathologie")["Traitements"].sum().nlargest(top)
    grouped = grouped[grouped["Pathologie"].isin(totals.index)]
    order = totals.index.tolist()

    chart = (
        alt.Chart(grouped)
        .mark_bar(cornerRadius=2, stroke=t["surface"], strokeWidth=2, height={"band": 0.76})
        .encode(
            y=alt.Y("Pathologie:N", sort=order, axis=_cat_axis()),
            x=alt.X("Traitements:Q", stack=True, axis=_bar_axis("Traitements identifiés")),
            color=alt.Color("Stade:N", title="Stade de développement",
                            scale=theme.categorical_scale(STAGE_ORDER),
                            sort=STAGE_ORDER,
                            legend=alt.Legend(title="Stade de développement")),
            order=alt.Order("color_Stade_sort_index:Q"),
            tooltip=[
                alt.Tooltip("Pathologie:N", title="Pathologie"),
                alt.Tooltip("Stade:N", title="Stade"),
                alt.Tooltip("Traitements:Q", title="Traitements"),
            ],
        )
    )
    return theme.style(chart, height=max(260, 28 * len(order)))


def treatments_by_phase(treatments: pd.DataFrame) -> alt.Chart:
    """Répartition par stade de développement : échelle ordonnée -> rampe ordinale."""
    if treatments.empty:
        return _empty("Aucun traitement extrait")

    t = theme.tokens()
    ranked = (
        treatments.groupby(["phase", "phase_rank"]).size()
        .reset_index(name="Traitements")
        .sort_values("phase_rank")
    )
    domain = ranked["phase"].tolist()

    base = alt.Chart(ranked).encode(
        y=alt.Y("phase:N", sort=domain, axis=_cat_axis()),
        x=alt.X("Traitements:Q", axis=_bar_axis("Traitements")),
        tooltip=[
            alt.Tooltip("phase:N", title="Stade"),
            alt.Tooltip("Traitements:Q", title="Traitements"),
        ],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height={"band": 0.7}).encode(
        color=alt.Color("phase:N", scale=theme.ordinal_scale(domain), sort=domain, legend=None)
    )
    labels = base.mark_text(
        align="left", dx=6, fontSize=11, color=t["ink_2"], font=theme.FONT_STACK
    ).encode(text=alt.Text("Traitements:Q"))

    return theme.style(
        (bars + labels).properties(title="Par stade de développement"),
        height=max(240, 28 * len(domain)),
    )


def treatments_by_modality(treatments: pd.DataFrame, top: int = 10) -> alt.Chart:
    """Répartition par modalité thérapeutique."""
    if treatments.empty:
        return _empty("Aucun traitement extrait")

    counts = (
        treatments["modality"].value_counts().nlargest(top)
        .rename_axis("Modalité").reset_index(name="Traitements")
    )
    return _ranked_bars(
        counts, "Traitements", "Modalité", "Traitements", "Modalité",
        height=max(240, 30 * len(counts)),
    ).properties(title="Traitements par modalité")


# ===========================================================================
# Signaux
# ===========================================================================
def signals_over_time(signals: pd.DataFrame) -> alt.Chart:
    """
    Volume de signaux collectés par jour. Série unique : pas de légende, le
    titre nomme la série.
    """
    if signals.empty or "collected_at" not in signals.columns:
        return _empty("Aucun signal collecté")

    t = theme.tokens()
    df = signals.copy()
    df["Jour"] = pd.to_datetime(df["collected_at"], errors="coerce", format="mixed", utc=True)
    df = df.dropna(subset=["Jour"])
    if df.empty:
        return _empty("Aucune date de collecte exploitable")

    daily = (
        df.set_index("Jour").resample("D").size()
        .rename("Signaux").reset_index()
    )
    if len(daily) < 2:
        return _ranked_bars(
            daily.assign(Jour=daily["Jour"].dt.strftime("%d/%m")),
            "Signaux", "Jour", "Signaux collectés", "Jour", height=200,
        ).properties(title="Signaux collectés par jour")

    hover = alt.selection_point(fields=["Jour"], nearest=True, on="pointerover", empty=False)

    line = alt.Chart(daily).mark_line(
        color=t["series"][0], strokeWidth=2, interpolate="monotone",
    ).encode(
        x=alt.X("Jour:T", axis=alt.Axis(title="Jour de collecte", grid=False,
                                        format="%d/%m", domain=False)),
        y=alt.Y("Signaux:Q", axis=_bar_axis("Signaux collectés")),
    )
    points = line.mark_point(
        size=64, filled=True, color=t["series"][0], stroke=t["surface"], strokeWidth=2,
    ).encode(
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[
            alt.Tooltip("Jour:T", title="Jour", format="%d/%m/%Y"),
            alt.Tooltip("Signaux:Q", title="Signaux collectés"),
        ],
    ).add_params(hover)

    return theme.style(
        (line + points).properties(title="Signaux collectés par jour"), height=250
    )


def signals_by_category(signals: pd.DataFrame) -> alt.Chart:
    """Répartition des signaux par domaine d'IA médicale."""
    if signals.empty:
        return _empty("Aucun signal collecté")

    counts = (
        signals["categorie"].fillna("Non catégorisé").value_counts()
        .rename_axis("Domaine").reset_index(name="Signaux")
    )
    # Au-delà de huit classes, la queue est repliée plutôt que colorée
    if len(counts) > theme.MAX_SERIES:
        head = counts.head(theme.MAX_SERIES - 1)
        tail_total = int(counts.iloc[theme.MAX_SERIES - 1:]["Signaux"].sum())
        counts = pd.concat(
            [head, pd.DataFrame([{"Domaine": "Autres", "Signaux": tail_total}])],
            ignore_index=True,
        )

    return _ranked_bars(
        counts, "Signaux", "Domaine", "Signaux", "Domaine",
        height=max(240, 32 * len(counts)),
    ).properties(title="Répartition par domaine")


def impact_distribution(signals: pd.DataFrame) -> alt.Chart:
    """Distribution du score d'impact : échelle ordonnée -> rampe ordinale."""
    if signals.empty:
        return _empty("Aucun signal collecté")

    t = theme.tokens()
    counts = (
        signals["impact_score"].value_counts()
        .reindex([1, 2, 3, 4, 5], fill_value=0)
        .rename_axis("Score").reset_index(name="Signaux")
    )
    counts["Score"] = counts["Score"].astype(str)
    domain = ["1", "2", "3", "4", "5"]

    base = alt.Chart(counts).encode(
        x=alt.X("Score:N", sort=domain, axis=_cat_axis("Score d'impact (1–5)")),
        y=alt.Y("Signaux:Q", axis=_bar_axis("Signaux")),
        tooltip=[
            alt.Tooltip("Score:N", title="Score d'impact"),
            alt.Tooltip("Signaux:Q", title="Signaux"),
        ],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, width={"band": 0.62}).encode(
        color=alt.Color("Score:N", scale=theme.ordinal_scale(domain), sort=domain, legend=None)
    )
    labels = base.mark_text(
        dy=-8, fontSize=11, color=t["ink_2"], font=theme.FONT_STACK
    ).encode(text=alt.Text("Signaux:Q"))

    return theme.style(
        (bars + labels).properties(title="Distribution de l'impact"), height=250
    )


def signals_by_source(signals: pd.DataFrame) -> alt.Chart:
    """Répartition des signaux par source de données."""
    if signals.empty:
        return _empty("Aucun signal collecté")
    counts = (
        signals["source"].value_counts()
        .rename_axis("Source").reset_index(name="Signaux")
    )
    return _ranked_bars(
        counts, "Signaux", "Source", "Signaux", "Source",
        height=max(200, 40 * len(counts)),
    ).properties(title="Répartition par source")


# ===========================================================================
# Plan d'investissement
# ===========================================================================
def allocation_split(allocations: List[dict]) -> alt.Chart:
    """
    Barre empilée à 100 % : part de chaque segment dans l'allocation.
    Palette catégorielle (7 segments au plus), légende présente, étiquettes
    directes sur les segments d'au moins 8 %.
    """
    if not allocations:
        return _empty("Aucune allocation")

    t = theme.tokens()
    df = pd.DataFrame(allocations).sort_values("weight_pct", ascending=False)
    df["Segment"] = df["segment"]
    df["Poids"] = df["weight_pct"]
    df["Ligne"] = "Allocation"
    order = df["Segment"].tolist()

    base = alt.Chart(df).encode(
        x=alt.X("Poids:Q", stack="normalize",
                axis=alt.Axis(title="Part du capital", format="%", grid=True, domain=False)),
        y=alt.Y("Ligne:N", axis=_cat_axis()),
        color=alt.Color("Segment:N", title="Segment",
                        scale=theme.categorical_scale(order), sort=order,
                        legend=alt.Legend(title="Segment", columns=2, symbolLimit=8)),
        order=alt.Order("Poids:Q", sort="descending"),
        tooltip=[
            alt.Tooltip("Segment:N", title="Segment"),
            alt.Tooltip("Poids:Q", title="Poids (%)"),
            alt.Tooltip("risk_level:N", title="Risque"),
            alt.Tooltip("time_to_value:N", title="Délai de retour"),
        ],
    )
    bars = base.mark_bar(
        cornerRadius=2, stroke=t["surface"], strokeWidth=2, height={"band": 0.5}
    )
    # Étiquetage sélectif : seuls les segments assez larges pour porter un texte
    labels = base.transform_filter(alt.datum.Poids >= 8).mark_text(
        color="#ffffff", fontSize=11, fontWeight=600, font=theme.FONT_STACK
    ).encode(text=alt.Text("Poids:Q", format=".0f"), color=alt.value("#ffffff"))

    return theme.style(bars + labels, height=170)


def deployment_phases(phases: List[dict]) -> alt.Chart:
    """Phasage du capital : séquence ordonnée -> rampe ordinale."""
    if not phases:
        return _empty("Aucun phasage")

    t = theme.tokens()
    df = pd.DataFrame(phases)
    df["Phase"] = df["phase"]
    df["Part"] = df["share_pct"]
    domain = df["Phase"].tolist()

    base = alt.Chart(df).encode(
        x=alt.X("Phase:N", sort=domain, axis=_cat_axis()),
        y=alt.Y("Part:Q", axis=_bar_axis("Part du capital (%)")),
        tooltip=[
            alt.Tooltip("Phase:N", title="Phase"),
            alt.Tooltip("Part:Q", title="Part du capital (%)"),
            alt.Tooltip("focus:N", title="Cible"),
            alt.Tooltip("trigger:N", title="Déclencheur"),
        ],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, width={"band": 0.6}).encode(
        color=alt.Color("Phase:N", scale=theme.ordinal_scale(domain), sort=domain, legend=None)
    )
    labels = base.mark_text(
        dy=-8, fontSize=11, color=t["ink_2"], font=theme.FONT_STACK
    ).encode(text=alt.Text("Part:Q", format=".0f"))

    return theme.style(bars + labels, height=240)


# ===========================================================================
# Concurrence
# ===========================================================================
def players_by_segment(players: pd.DataFrame) -> alt.Chart:
    """
    Barres empilées : acteurs par segment, répartis entre déjà opérationnels et
    à venir. Trois séries -> palette catégorielle, légende présente.
    """
    if players.empty:
        return _empty("Aucun acteur cartographié")

    t = theme.tokens()
    status_order = ["Existant", "En lancement", "Annoncé / à venir"]
    grouped = players.groupby(["segment", "status"]).size().reset_index(name="Acteurs")
    totals = grouped.groupby("segment")["Acteurs"].sum().sort_values(ascending=False)
    order = totals.index.tolist()

    chart = (
        alt.Chart(grouped)
        .mark_bar(cornerRadius=2, stroke=t["surface"], strokeWidth=2, height={"band": 0.74})
        .encode(
            y=alt.Y("segment:N", sort=order, axis=_cat_axis()),
            x=alt.X("Acteurs:Q", stack=True, axis=_bar_axis("Acteurs")),
            color=alt.Color("status:N", title="Statut",
                            scale=theme.categorical_scale(status_order),
                            sort=status_order,
                            legend=alt.Legend(title="Statut")),
            order=alt.Order("color_status_sort_index:Q"),
            tooltip=[
                alt.Tooltip("segment:N", title="Segment"),
                alt.Tooltip("status:N", title="Statut"),
                alt.Tooltip("Acteurs:Q", title="Acteurs"),
            ],
        )
    )
    return theme.style(chart, height=max(260, 30 * len(order)))


def threat_distribution(players: pd.DataFrame) -> alt.Chart:
    """Intensité concurrentielle : échelle ordonnée -> rampe ordinale."""
    if players.empty:
        return _empty("Aucun acteur cartographié")

    t = theme.tokens()
    counts = (
        players["threat_level"].value_counts()
        .reindex(THREAT_ORDER, fill_value=0)
        .rename_axis("Menace").reset_index(name="Acteurs")
    )

    base = alt.Chart(counts).encode(
        y=alt.Y("Menace:N", sort=THREAT_ORDER, axis=_cat_axis()),
        x=alt.X("Acteurs:Q", axis=_bar_axis("Acteurs")),
        tooltip=[
            alt.Tooltip("Menace:N", title="Niveau de menace"),
            alt.Tooltip("Acteurs:Q", title="Acteurs"),
        ],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height={"band": 0.68}).encode(
        color=alt.Color("Menace:N", scale=theme.ordinal_scale(THREAT_ORDER),
                        sort=THREAT_ORDER, legend=None)
    )
    labels = base.mark_text(
        align="left", dx=6, fontSize=11, color=t["ink_2"], font=theme.FONT_STACK
    ).encode(text=alt.Text("Acteurs:Q"))

    return theme.style(
        (bars + labels).properties(title="Intensité concurrentielle"), height=250
    )


def funding_ranking(players: pd.DataFrame, top: int = 12) -> alt.Chart:
    """Classement par financement cumulé documenté."""
    if players.empty:
        return _empty("Aucun financement documenté")

    t = theme.tokens()
    df = players.nlargest(top, "funding_musd").copy()
    df["Acteur"] = df["name"]
    order = df["Acteur"].tolist()

    base = alt.Chart(df).encode(
        y=alt.Y("Acteur:N", sort=order, axis=_cat_axis()),
        x=alt.X("funding_musd:Q", axis=_bar_axis("Financement cumulé (M$)")),
        tooltip=[
            alt.Tooltip("Acteur:N", title="Acteur"),
            alt.Tooltip("funding_musd:Q", title="Financement (M$)", format=",.0f"),
            alt.Tooltip("segment:N", title="Segment"),
            alt.Tooltip("status:N", title="Statut"),
            alt.Tooltip("country:N", title="Pays"),
            alt.Tooltip("threat_level:N", title="Menace"),
        ],
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height={"band": 0.72}).encode(
        color=alt.Color("funding_musd:Q", scale=alt.Scale(range=theme.sequential_range()),
                        legend=None)
    )
    labels = base.mark_text(
        align="left", dx=6, fontSize=11, color=t["ink_2"], font=theme.FONT_STACK
    ).encode(text=alt.Text("funding_musd:Q", format=",.0f"))

    return theme.style(bars + labels, height=max(240, 28 * len(df)))
