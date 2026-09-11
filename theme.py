"""
Système de design du tableau de bord — édition « Bleu / Gris / Orange ».

Un seul endroit définit les couleurs, la typographie, les composants HTML et le
style des graphiques Altair — le reste de l'application ne manipule que des
*rôles* (`surface`, `ink`, `series-1`, `status-critical`...), jamais des hex.

Direction visuelle :
  * bleu ardoise comme couleur de marque (confiance, sobriété) ;
  * gris chaud (pas de gris froid clinique) pour la structure et le texte ;
  * orange comme unique accent vif — réservé aux points d'attention et à la
    mise en avant (CTA visuels, chiffre clé, liseré d'accent) ;
  * les couleurs de statut (risque) restent sémantiques (vert/ambre/rouge) :
    casser ce code universel nuirait à la lisibilité, même dans une refonte.

Règles de visualisation appliquées (inchangées) :
  * palette catégorielle en ordre fixe, jamais recyclée (8 séries maximum) ;
  * une seule teinte en dégradé pour les magnitudes (jamais d'arc-en-ciel) ;
  * palette de statut réservée aux niveaux de risque, toujours accompagnée
    d'une icône et d'un libellé — jamais la couleur seule ;
  * jamais deux axes Y sur un même graphique ;
  * légende dès 2 séries, info-bulles sur toutes les marques, grille discrète ;
  * mode sombre composé de pas dédiés (pas une simple inversion).
"""

from __future__ import annotations

from typing import Dict, List, Sequence

import altair as alt
import streamlit as st

# ---------------------------------------------------------------------------
# Jetons de couleur
# ---------------------------------------------------------------------------
LIGHT: Dict[str, object] = {
    "mode": "light",
    "surface": "#ffffff",
    "surface_alt": "#f3f5f8",
    "plane": "#eef1f5",
    "ink": "#101828",
    "ink_2": "#475467",
    "muted": "#8a94a6",
    "grid": "#e3e7ee",
    "axis": "#c7ced9",
    "border": "rgba(16,24,40,0.09)",
    "shadow": "rgba(16,24,40,0.06)",
    "success_text": "#0c7a3d",
    # Couleur de marque (bleu ardoise) + accent (orange) utilisés hors graphiques
    "brand": "#2b5aa8",
    "brand_2": "#1b3f7d",
    "accent_orange": "#f2661a",
    # Catégoriel — ordre fixe : bleu, orange, gris-bleu, bleu clair, orange doux,
    # ardoise foncé, sable, bleu nuit
    "series": ["#2b5aa8", "#f2661a", "#7c8aa3", "#5f9ed1",
               "#f6a35c", "#1b3f7d", "#c9a06a", "#334463"],
    # Ordinal (échelle discrète ordonnée : score 1→5) — rampe bleu ardoise
    "ordinal": ["#a9c0e4", "#7ea3d6", "#5079b9", "#2b5aa8", "#173a70"],
    # Séquentiel continu (heatmap) — gris-bleu clair vers bleu profond
    "sequential": ["#e6eaf1", "#c9d3e3", "#a9bcd8", "#7c9bc7",
                   "#4f78ac", "#2b5aa8", "#173a70"],
    # Divergent — orange (bas) → gris neutre (milieu) → bleu (haut)
    "diverging": ["#f2661a", "#eef1f5", "#2b5aa8"],
}

DARK: Dict[str, object] = {
    "mode": "dark",
    "surface": "#202327",
    "surface_alt": "#282c31",
    "plane": "#16181b",
    "ink": "#ffffff",
    "ink_2": "#ffffff",
    "muted": "#e6e8eb",
    "grid": "#242a34",
    "axis": "#333a46",
    "border": "rgba(255,255,255,0.09)",
    "shadow": "rgba(0,0,0,0.4)",
    "success_text": "#3fbf7f",
    "brand": "#5f9ed1",
    "brand_2": "#7ea3d6",
    "accent_orange": "#f6a35c",
    "series": ["#5f9ed1", "#f6a35c", "#8b96a8", "#2b5aa8",
               "#f2661a", "#a9c0e4", "#c9a06a", "#3d5a8f"],
    "ordinal": ["#7c9bc7", "#5079b9", "#2b5aa8", "#173a70", "#0f2750"],
    # Sur fond sombre, « proche de zéro » recule vers la surface : le dégradé s'inverse
    "sequential": ["#173a70", "#2b5aa8", "#4f78ac", "#7c9bc7",
                   "#a9bcd8", "#c9d3e3", "#e6eaf1"],
    "diverging": ["#f6a35c", "#242a34", "#5f9ed1"],
}

# Palette de statut — identique dans les deux modes, réservée aux niveaux de risque.
# Volontairement conservée hors du système bleu/gris/orange : c'est un code
# sémantique universel (vert = ok, rouge = danger) qu'il ne faut pas rejouer
# avec la couleur de marque.
STATUS = {
    "good": "#0c9d52",
    "warning": "#e8a723",
    "serious": "#f2661a",
    "critical": "#d13b3b",
}

# Niveau de risque -> (rôle de statut, icône). L'icône et le libellé portent
# toujours le sens : la couleur ne fait que le renforcer.
RISK_STYLE = {
    "Modéré": ("good", "✓"),
    "Élevé": ("warning", "▲"),
    "Très élevé": ("serious", "◆"),
    "Critique": ("critical", "■"),
}

FONT_STACK = '"Inter", system-ui, -apple-system, "Segoe UI", sans-serif'

# Nombre maximum de séries catégorielles ; au-delà, replier dans « Autres »
MAX_SERIES = 8


# ---------------------------------------------------------------------------
# Détection du mode actif
# ---------------------------------------------------------------------------
def tokens() -> Dict[str, object]:
    """Jetons du mode actif.

    Fond noir/gris forcé par défaut (indépendant du thème Streamlit) : si
    `.streamlit/config.toml` définit explicitement `theme.base = "light"`,
    ce choix est respecté ; sinon on bascule sur la palette sombre.
    """
    base = "dark"
    try:
        base = (st.get_option("theme.base") or "dark").lower()
    except Exception:
        pass
    return LIGHT if base == "light" else DARK


def risk_color(tier: str) -> str:
    """Couleur de statut associée à un niveau de risque."""
    role, _ = RISK_STYLE.get(tier, ("warning", "▲"))
    return STATUS[role]


def risk_icon(tier: str) -> str:
    """Icône associée à un niveau de risque (canal redondant à la couleur)."""
    return RISK_STYLE.get(tier, ("warning", "▲"))[1]


def series_colors(n: int) -> List[str]:
    """`n` couleurs catégorielles dans l'ordre fixe de la palette."""
    palette = tokens()["series"]
    return list(palette[: min(n, MAX_SERIES)])


def fold_to_other(labels: Sequence[str], limit: int = MAX_SERIES) -> List[str]:
    """Conserve les `limit - 1` premiers libellés et replie la queue dans « Autres »."""
    labels = list(labels)
    if len(labels) <= limit:
        return labels
    return labels[: limit - 1] + ["Autres"]


# ---------------------------------------------------------------------------
# Feuille de style
# ---------------------------------------------------------------------------
def inject_css() -> None:
    """Injecte la feuille de style du tableau de bord (idempotent par rerun)."""
    t = tokens()
    st.markdown(
        f"""
        <style>
        :root {{
            --surface: {t['surface']};
            --surface-alt: {t['surface_alt']};
            --plane: {t['plane']};
            --ink: {t['ink']};
            --ink-2: {t['ink_2']};
            --muted: {t['muted']};
            --grid: {t['grid']};
            --axis: {t['axis']};
            --border: {t['border']};
            --shadow: {t['shadow']};
            --good: {STATUS['good']};
            --warning: {STATUS['warning']};
            --serious: {STATUS['serious']};
            --critical: {STATUS['critical']};
            --success-text: {t['success_text']};
            --brand: {t['brand']};
            --brand-2: {t['brand_2']};
            --accent: {t['accent_orange']};
        }}

        .stApp {{ background: var(--plane); }}
        html, body, [class*="css"] {{ font-family: {FONT_STACK}; }}

        /* Largeur de lecture et respiration verticale */
        .block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1500px; }}
        #MainMenu, footer {{ visibility: hidden; }}

        /* ---------------- En-tête d'application ---------------- */
        .vs-header {{
            display: flex; align-items: flex-start; justify-content: space-between;
            gap: 1.5rem; flex-wrap: wrap;
            padding: 1.1rem 1.4rem; margin-bottom: 1.1rem;
            background: linear-gradient(135deg, var(--brand-2), var(--brand));
            border: 1px solid var(--border);
            border-radius: 14px; box-shadow: 0 4px 14px var(--shadow);
        }}
        .vs-header h1 {{
            margin: 0; font-size: 1.45rem; font-weight: 650;
            letter-spacing: -0.015em; color: #ffffff; line-height: 1.25;
        }}
        .vs-header p {{
            margin: .35rem 0 0; font-size: .82rem; color: rgba(255,255,255,0.82); max-width: 68ch;
        }}
        .vs-live {{
            display: inline-flex; align-items: center; gap: .45rem;
            font-size: .72rem; font-weight: 600; letter-spacing: .04em;
            text-transform: uppercase; color: #ffffff;
            padding: .34rem .7rem; border-radius: 999px;
            border: 1px solid rgba(255,255,255,0.28); background: rgba(255,255,255,0.12);
            white-space: nowrap;
        }}
        .vs-dot {{
            width: 7px; height: 7px; border-radius: 50%; background: var(--good);
            box-shadow: 0 0 0 3px rgba(255,255,255,0.25);
        }}
        .vs-dot.stale {{ background: var(--warning); }}
        .vs-dot.off {{ background: var(--muted); box-shadow: none; }}

        /* ---------------- Figure vedette ---------------- */
        .vs-hero {{ padding: .2rem 0 .1rem; }}
        .vs-hero .v {{
            font-size: 3.1rem; font-weight: 660; line-height: 1;
            letter-spacing: -0.03em; color: var(--ink);
        }}
        .vs-hero .l {{
            margin-top: .45rem; font-size: .78rem; color: var(--ink-2);
            text-transform: uppercase; letter-spacing: .07em; font-weight: 600;
        }}
        .vs-hero .s {{ margin-top: .3rem; font-size: .8rem; color: var(--muted); }}

        /* ---------------- Tuiles d'indicateurs ---------------- */
        .vs-tile {{
            background: var(--surface); border: 1px solid var(--border);
            border-top: 3px solid var(--brand);
            border-radius: 12px; padding: .85rem 1rem; height: 100%;
            box-shadow: 0 1px 2px var(--shadow);
        }}
        .vs-tile .k {{
            font-size: .68rem; font-weight: 650; letter-spacing: .07em;
            text-transform: uppercase; color: var(--muted);
            display: block; margin-bottom: .4rem;
        }}
        .vs-tile .v {{
            font-size: 1.75rem; font-weight: 620; line-height: 1.05;
            letter-spacing: -0.02em; color: var(--ink);
        }}
        .vs-tile .d {{ font-size: .76rem; margin-top: .3rem; color: var(--ink-2); }}
        .vs-tile .d.up {{ color: var(--success-text); font-weight: 600; }}
        .vs-tile .d.crit {{ color: var(--critical); font-weight: 600; }}

        /* ---------------- Cartouches de section ---------------- */
        .vs-sec {{ margin: 1.7rem 0 .8rem; }}
        .vs-sec h2 {{
            margin: 0; font-size: 1.02rem; font-weight: 650;
            letter-spacing: -0.01em; color: var(--ink);
            padding-bottom: .5rem; border-bottom: 2px solid var(--accent);
            display: inline-block;
        }}
        .vs-sec p {{ margin: .45rem 0 0; font-size: .81rem; color: var(--ink-2); }}

        /* ---------------- Cartes ---------------- */
        .vs-card {{
            background: var(--surface); border: 1px solid var(--border);
            border-radius: 12px; padding: 1rem 1.15rem; margin-bottom: .8rem;
            box-shadow: 0 1px 2px var(--shadow);
        }}
        .vs-card h3 {{
            margin: 0 0 .5rem; font-size: .95rem; font-weight: 640; color: var(--ink);
        }}
        .vs-card p {{ margin: .3rem 0; font-size: .85rem; color: var(--ink-2); line-height: 1.55; }}
        .vs-card .meta {{ font-size: .75rem; color: var(--muted); }}

        /* Liseré de gravité — redondant avec le badge, jamais seul porteur du sens */
        .vs-card.risk-good {{ border-left: 3px solid var(--good); }}
        .vs-card.risk-warning {{ border-left: 3px solid var(--warning); }}
        .vs-card.risk-serious {{ border-left: 3px solid var(--serious); }}
        .vs-card.risk-critical {{ border-left: 3px solid var(--critical); }}

        /* ---------------- Badges ---------------- */
        .vs-badge {{
            display: inline-flex; align-items: center; gap: .35rem;
            font-size: .72rem; font-weight: 600; padding: .2rem .55rem;
            border-radius: 6px; border: 1px solid var(--border);
            background: var(--surface-alt); color: var(--ink-2);
            white-space: nowrap; margin: 0 .3rem .3rem 0;
        }}
        .vs-badge .ic {{ font-size: .78rem; line-height: 1; }}
        .vs-badge.good {{ color: var(--good); border-color: color-mix(in srgb, var(--good) 40%, transparent); }}
        .vs-badge.warning {{ color: color-mix(in srgb, var(--warning) 78%, var(--ink)); border-color: color-mix(in srgb, var(--warning) 45%, transparent); }}
        .vs-badge.serious {{ color: color-mix(in srgb, var(--serious) 80%, var(--ink)); border-color: color-mix(in srgb, var(--serious) 45%, transparent); }}
        .vs-badge.critical {{ color: var(--critical); border-color: color-mix(in srgb, var(--critical) 40%, transparent); }}
        .vs-badge.brand {{ color: var(--brand); border-color: color-mix(in srgb, var(--brand) 45%, transparent); background: color-mix(in srgb, var(--brand) 8%, var(--surface-alt)); }}
        .vs-badge.accent {{ color: var(--accent); border-color: color-mix(in srgb, var(--accent) 45%, transparent); background: color-mix(in srgb, var(--accent) 10%, var(--surface-alt)); }}

        /* ---------------- Jauge (ratio contre une limite) ---------------- */
        .vs-meter {{
            height: 6px; border-radius: 3px; background: var(--surface-alt);
            overflow: hidden; margin: .45rem 0 .15rem;
        }}
        .vs-meter > span {{ display: block; height: 100%; border-radius: 3px; }}

        /* ---------------- Onglets ---------------- */
        .stTabs [data-baseweb="tab-list"] {{
            gap: .15rem; border-bottom: 1px solid var(--grid);
            background: transparent; padding-bottom: 0;
        }}
        .stTabs [data-baseweb="tab"] {{
            font-size: .86rem; font-weight: 560; color: var(--ink-2);
            padding: .55rem .9rem; border-radius: 8px 8px 0 0;
        }}
        .stTabs [aria-selected="true"] {{
            color: var(--ink); font-weight: 650;
            box-shadow: inset 0 -2px 0 var(--accent);
        }}

        /* ---------------- Panneau latéral ---------------- */
        [data-testid="stSidebar"] {{
            background: var(--surface); border-right: 1px solid var(--border);
        }}
        [data-testid="stSidebar"] .block-container {{ padding-top: 1.2rem; }}
        .vs-side-h {{
            font-size: .68rem; font-weight: 700; letter-spacing: .09em;
            text-transform: uppercase; color: var(--muted);
            margin: 1.1rem 0 .5rem;
        }}

        /* ---------------- Tableaux ---------------- */
        [data-testid="stDataFrame"] {{ font-variant-numeric: tabular-nums; }}

        /* ---------------- Lisibilité globale (natif Streamlit) ---------------- */
        /* Les composants HTML custom (vs-*) utilisent déjà var(--ink) via leurs
           propres règles ; ce bloc couvre les widgets natifs Streamlit qui ont
           leurs propres couleurs par défaut et restaient peu lisibles. */
        [data-testid="stSidebar"] *,
        [data-testid="stMarkdownContainer"] *,
        [data-testid="stExpander"] summary,
        [data-testid="stExpander"] summary *,
        [data-testid="stExpander"] p,
        .stAlert p, .stAlert div, .stAlert span,
        .stTextInput label, .stTextInput input,
        .stButton button, .stCaptionContainer, .stCaption,
        h1, h2, h3, h4, h5, h6, p, span, label, li {{
            color: #ffffff !important;
        }}
        /* Icônes/chevrons des expanders restent visibles */
        [data-testid="stExpander"] svg {{ fill: #ffffff !important; }}
        /* Placeholders : blanc légèrement atténué pour rester distinguables du texte saisi */
        ::placeholder {{ color: rgba(255,255,255,0.55) !important; opacity: 1; }}

        /* Texte noir dans le selectbox "Horizon d'investissement" (surcharge du blanc global) */
        [data-testid="stSidebar"] .stSelectbox [data-baseweb="select"],
        [data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] *,
        [data-testid="stSidebar"] .stSelectbox input {{
            color: #000000 !important;
        }}

        /* ---------------- Sidebar : fonds blancs -> bleu ----------------
           Ne touche pas aux boutons "primary" (ex. Tout actualiser, rouge
           intentionnel) : seuls les boutons secondaires, champs de saisie et
           l'en-tête de l'expander, à fond blanc par défaut, passent en bleu. */
        [data-testid="stSidebar"] button[kind="secondary"],
        [data-testid="stSidebar"] .stTextInput input,
        [data-testid="stSidebar"] .stNumberInput input,
        [data-testid="stSidebar"] .stTextArea textarea,
        [data-testid="stSidebar"] .stSelectbox [data-baseweb="select"],
        [data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] > div,
        [data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] div,
        [data-testid="stSidebar"] .stSelectbox input,
        [data-testid="stSidebar"] .stMultiSelect [data-baseweb="select"],
        [data-testid="stSidebar"] .stMultiSelect [data-baseweb="select"] > div,
        [data-testid="stSidebar"] .stMultiSelect [data-baseweb="select"] div,
        [data-testid="stSidebar"] [data-testid="stExpander"] summary,
        [data-testid="stSidebar"] [data-testid="stExpander"] {{
            background: var(--brand) !important;
            border-color: var(--brand) !important;
        }}
        [data-testid="stSidebar"] button[kind="secondary"]:hover {{
            background: var(--brand-2) !important;
            border-color: var(--brand-2) !important;
        }}
        /* Le menu déroulant d'un selectbox/multiselect est rendu dans un portail
           hors du DOM de la sidebar : on le cible séparément, globalement. */
        [data-baseweb="popover"] [data-baseweb="menu"],
        [data-baseweb="popover"] ul[role="listbox"] {{
            background: var(--brand) !important;
        }}
        [data-baseweb="popover"] li[role="option"] {{
            background: transparent !important;
        }}
        [data-baseweb="popover"] li[role="option"]:hover,
        [data-baseweb="popover"] li[aria-selected="true"] {{
            background: var(--brand-2) !important;
        }}
        /* Boutons désactivés : nuance de bleu plus sombre, opacité réduite */
        [data-testid="stSidebar"] button[kind="secondary"]:disabled {{
            background: var(--brand-2) !important;
            opacity: 0.55;
        }}

        /* ---------------- Divers ---------------- */
        .vs-quote {{
            border-left: 2px solid var(--accent); padding: .1rem 0 .1rem .8rem;
            font-size: .87rem; color: var(--ink); line-height: 1.6;
        }}
        .vs-src {{ font-size: .74rem; color: var(--muted); word-break: break-all; }}
        .vs-empty {{
            padding: 2rem 1.2rem; text-align: center; border-radius: 12px;
            border: 1px dashed var(--axis); color: var(--ink-2); font-size: .87rem;
            background: var(--surface);
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Composants HTML
# ---------------------------------------------------------------------------
def esc(text: object) -> str:
    """
    Échappement HTML : obligatoire sur tout texte injecté dans les composants.
    Ces chaînes proviennent d'API externes et de sorties de modèle — jamais de
    contenu de confiance.
    """
    return (
        str(text if text is not None else "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# Alias interne conservé pour la lisibilité des composants ci-dessous
_esc = esc


def header(title: str, subtitle: str, status_label: str, status_state: str = "live") -> None:
    """En-tête d'application (dégradé bleu ardoise) avec pastille d'état de fraîcheur."""
    cls = {"live": "", "stale": " stale", "off": " off"}.get(status_state, "")
    st.markdown(
        f"""<div class="vs-header">
          <div>
            <h1>{_esc(title)}</h1>
            <p>{_esc(subtitle)}</p>
          </div>
          <div class="vs-live"><span class="vs-dot{cls}"></span>{_esc(status_label)}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def section(title: str, subtitle: str = "") -> None:
    """Cartouche de section, soulignée d'un trait orange."""
    sub = f"<p>{_esc(subtitle)}</p>" if subtitle else ""
    st.markdown(f'<div class="vs-sec"><h2>{_esc(title)}</h2>{sub}</div>', unsafe_allow_html=True)


def hero(value: str, label: str, sub: str = "") -> None:
    """Figure vedette : le chiffre par lequel le tableau de bord s'ouvre."""
    s = f'<div class="s">{_esc(sub)}</div>' if sub else ""
    st.markdown(
        f'<div class="vs-hero"><div class="v">{_esc(value)}</div>'
        f'<div class="l">{_esc(label)}</div>{s}</div>',
        unsafe_allow_html=True,
    )


def tile(label: str, value: str, delta: str = "", tone: str = "") -> None:
    """Tuile d'indicateur : libellé, valeur, variation. `tone` ∈ {'', 'up', 'crit'}."""
    d = f'<div class="d {tone}">{_esc(delta)}</div>' if delta else ""
    st.markdown(
        f'<div class="vs-tile"><span class="k">{_esc(label)}</span>'
        f'<div class="v">{_esc(value)}</div>{d}</div>',
        unsafe_allow_html=True,
    )


def tile_row(items: Sequence[dict]) -> None:
    """Rangée de tuiles d'indicateurs (une colonne par tuile)."""
    if not items:
        return
    for col, item in zip(st.columns(len(items)), items):
        with col:
            tile(item.get("label", ""), item.get("value", "—"),
                 item.get("delta", ""), item.get("tone", ""))


def badge(text: str, tone: str = "", icon: str = "") -> str:
    """Badge HTML (chaîne à concaténer).
    `tone` ∈ {'', 'good', 'warning', 'serious', 'critical', 'brand', 'accent'}."""
    ic = f'<span class="ic">{_esc(icon)}</span>' if icon else ""
    return f'<span class="vs-badge {tone}">{ic}{_esc(text)}</span>'


def risk_badge(tier: str) -> str:
    """Badge de niveau de risque : icône + libellé + couleur de statut."""
    role, icon = RISK_STYLE.get(tier, ("warning", "▲"))
    return badge(tier, role, icon)


def meter(ratio: float, color: str) -> str:
    """Jauge horizontale : un ratio contre une limite (jamais un camembert de 2 parts)."""
    pct = max(0.0, min(1.0, float(ratio))) * 100
    return f'<div class="vs-meter"><span style="width:{pct:.1f}%;background:{color}"></span></div>'


def card(title: str, body_html: str, tone: str = "") -> None:
    """Carte de contenu, avec liseré de gravité optionnel."""
    cls = f" risk-{tone}" if tone else ""
    st.markdown(
        f'<div class="vs-card{cls}"><h3>{_esc(title)}</h3>{body_html}</div>',
        unsafe_allow_html=True,
    )


def empty_state(message: str) -> None:
    """État vide explicite (plutôt qu'un graphique sans données)."""
    st.markdown(f'<div class="vs-empty">{_esc(message)}</div>', unsafe_allow_html=True)


def sidebar_heading(text: str) -> None:
    """Intertitre du panneau latéral."""
    st.sidebar.markdown(f'<div class="vs-side-h">{_esc(text)}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Graphiques — helpers Altair
# ---------------------------------------------------------------------------
def style(chart: alt.Chart, height: int = 260) -> alt.Chart:
    """Applique la grille discrète, l'encre de texte et les axes recessifs."""
    t = tokens()
    return (
        chart.properties(height=height, background="transparent")
        .configure_view(stroke=None)
        .configure_axis(
            labelFont=FONT_STACK, titleFont=FONT_STACK,
            labelColor=t["muted"], titleColor=t["ink_2"],
            labelFontSize=11, titleFontSize=11, titleFontWeight=600,
            gridColor=t["grid"], gridWidth=1, domainColor=t["axis"],
            tickColor=t["axis"], tickSize=4, labelPadding=6, titlePadding=10,
        )
        .configure_legend(
            labelFont=FONT_STACK, titleFont=FONT_STACK,
            labelColor=t["ink_2"], titleColor=t["muted"],
            labelFontSize=11, titleFontSize=10, titleFontWeight=600,
            symbolType="square", symbolSize=90, orient="top",
            direction="horizontal", titleOrient="left", offset=8,
        )
        .configure_title(font=FONT_STACK, color=t["ink"], fontSize=12, fontWeight=600, anchor="start")
        .configure_text(font=FONT_STACK)
    )


def ordinal_scale(domain: Sequence) -> alt.Scale:
    """Échelle ordinale (score, palier) : une seule teinte bleue, plus = plus foncé."""
    steps = tokens()["ordinal"]
    domain = list(domain)
    if len(domain) <= len(steps):
        idx = [round(i * (len(steps) - 1) / max(1, len(domain) - 1)) for i in range(len(domain))]
        colors = [steps[i] for i in idx]
    else:
        colors = [steps[i % len(steps)] for i in range(len(domain))]
    return alt.Scale(domain=domain, range=colors)


def categorical_scale(domain: Sequence) -> alt.Scale:
    """Échelle catégorielle : ordre fixe bleu/orange/gris, la couleur suit l'entité."""
    domain = list(domain)
    return alt.Scale(domain=domain, range=series_colors(len(domain)))


def risk_scale() -> alt.Scale:
    """Échelle de niveaux de risque, alignée sur la palette de statut sémantique."""
    from config import RISK_TIERS
    return alt.Scale(domain=RISK_TIERS, range=[risk_color(r) for r in RISK_TIERS])


def sequential_range() -> List[str]:
    """Rampe séquentielle continue (heatmaps) : gris-bleu clair vers bleu profond."""
    return list(tokens()["sequential"])


def diverging_range() -> List[str]:
    """Rampe divergente : orange (bas) → gris neutre (milieu) → bleu (haut)."""
    return list(tokens()["diverging"])