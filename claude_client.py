"""
Client Claude (Anthropic) — remplace l'ancienne intégration Google Gemini.

Ce module est le seul point de contact avec l'API Claude. Il expose un appel
unique, `call_structured()`, qui garantit :

  * une sortie JSON valide et conforme à un schéma (`output_config.format`) ;
  * la réflexion adaptative (`thinking: adaptive`) + un niveau d'effort réglable ;
  * la recherche web côté serveur pour les analyses qui doivent être temps réel,
    avec restitution des sources citées ;
  * la reprise automatique des tours interrompus (`pause_turn`) ;
  * le repli serveur en cas de refus de sécurité (`fallbacks`) ;
  * un mode dégradé complet : sans clé API, l'application continue de collecter
    et d'afficher les données publiques, sans jamais lever d'exception.

La clé n'est jamais codée en dur. Ordre de résolution :
  1. clé saisie dans le panneau latéral du dashboard (session courante) ;
  2. variable d'environnement `ANTHROPIC_API_KEY` (ou fichier `.env`) ;
  3. profil `ant auth login` détecté par le SDK.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import anthropic

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_EFFORT,
    CLAUDE_MODEL,
    WEB_SEARCH_MAX_USES,
)

logger = logging.getLogger("veille.claude")

# Outil de recherche web côté serveur (variante à filtrage dynamique)
WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": WEB_SEARCH_MAX_USES,
}

# Repli serveur : si un classificateur de sécurité décline la requête,
# l'API relance le même appel sur un modèle de repli au lieu de s'arrêter.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_MAX_PAUSE_RESTARTS = 5

# Clé fournie à chaud (panneau latéral). Protégée : Streamlit exécute des reruns
# concurrents et le pipeline appelle l'API depuis plusieurs threads.
_runtime_key: Optional[str] = None
_key_lock = threading.Lock()
_client_cache: Dict[str, anthropic.Anthropic] = {}

# Certains paramètres n'existent que sur des versions récentes du SDK / de l'API.
# On les désactive définitivement dès qu'un appel les rejette.
_feature_flags = {"fallbacks": True}


# ---------------------------------------------------------------------------
# Gestion de la clé et du client
# ---------------------------------------------------------------------------
def set_runtime_key(key: str) -> None:
    """Enregistre une clé API pour la session courante (saisie dans l'interface)."""
    global _runtime_key
    with _key_lock:
        _runtime_key = (key or "").strip() or None
        _client_cache.clear()


def active_key() -> str:
    """Clé effectivement utilisée (chaîne vide si aucune)."""
    with _key_lock:
        return _runtime_key or ANTHROPIC_API_KEY or ""


def key_source() -> str:
    """Origine de la clé active, pour l'affichage dans l'interface."""
    with _key_lock:
        if _runtime_key:
            return "saisie dans l'interface"
    if ANTHROPIC_API_KEY:
        return "variable d'environnement / .env"
    return "aucune"


def masked_key() -> str:
    """Clé tronquée, sûre à afficher."""
    key = active_key()
    if not key:
        return "—"
    return f"{key[:11]}…{key[-4:]}" if len(key) > 18 else "définie"


def get_client() -> Optional[anthropic.Anthropic]:
    """Client Anthropic mis en cache, ou None si aucune clé n'est disponible."""
    key = active_key()
    if not key:
        return None
    with _key_lock:
        if key not in _client_cache:
            _client_cache[key] = anthropic.Anthropic(api_key=key, max_retries=3, timeout=600.0)
        return _client_cache[key]


def is_enabled() -> bool:
    """True si l'analyse IA est disponible."""
    return get_client() is not None


def check_credentials() -> tuple[bool, str]:
    """Valide la clé par un appel minimal. Retourne (ok, message)."""
    client = get_client()
    if client is None:
        return False, "Aucune clé API Claude détectée."
    try:
        client.models.retrieve(CLAUDE_MODEL)
        return True, f"Clé valide — modèle « {CLAUDE_MODEL} » accessible."
    except anthropic.AuthenticationError:
        return False, "Clé refusée (401) : vérifiez la valeur de ANTHROPIC_API_KEY."
    except anthropic.PermissionDeniedError:
        return False, "Clé valide mais droits insuffisants pour ce modèle."
    except anthropic.NotFoundError:
        return False, f"Modèle « {CLAUDE_MODEL} » introuvable pour cette organisation."
    except anthropic.APIConnectionError:
        return False, "Impossible de joindre api.anthropic.com (réseau ou proxy)."
    except anthropic.APIStatusError as exc:
        return False, f"Erreur API {exc.status_code} : {exc.message}"


# ---------------------------------------------------------------------------
# Résultat d'appel
# ---------------------------------------------------------------------------
@dataclass
class AIResult:
    """Résultat d'un appel structuré à Claude."""

    data: Dict[str, Any] = field(default_factory=dict)
    sources: List[Dict[str, str]] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    searches: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.data)

    @property
    def cost_estimate(self) -> float:
        """Coût indicatif en dollars (tarif Opus 5 : 5 $/M entrée, 25 $/M sortie)."""
        return self.input_tokens / 1e6 * 5.0 + self.output_tokens / 1e6 * 25.0


# ---------------------------------------------------------------------------
# Lecture de la réponse
# ---------------------------------------------------------------------------
def _collect_sources(content: List[Any]) -> List[Dict[str, str]]:
    """Extrait les sources web citées d'une réponse."""
    sources: List[Dict[str, str]] = []
    seen = set()
    for block in content:
        if getattr(block, "type", "") != "web_search_tool_result":
            continue
        results = getattr(block, "content", None)
        # En cas d'erreur d'outil, `content` est un objet unique et non une liste
        if not isinstance(results, list):
            code = getattr(results, "error_code", "inconnu")
            logger.warning("Recherche web en erreur : %s", code)
            continue
        for res in results:
            url = getattr(res, "url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append({
                "url": url,
                "title": getattr(res, "title", "") or url,
                "age": getattr(res, "page_age", "") or "",
            })
    return sources


def _extract_json(content: List[Any]) -> Dict[str, Any]:
    """Récupère l'objet JSON produit sous contrainte de schéma."""
    for block in content:
        if getattr(block, "type", "") == "text" and (block.text or "").strip():
            return json.loads(block.text)
    raise ValueError("Réponse sans bloc de texte exploitable.")


# ---------------------------------------------------------------------------
# Appel principal
# ---------------------------------------------------------------------------
def call_structured(
    *,
    prompt: str,
    schema: Dict[str, Any],
    system: str,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    web_search: bool = False,
    max_tokens: int = 32000,
) -> AIResult:
    """
    Appelle Claude et retourne un objet JSON conforme à `schema`.

    `web_search=True` active la recherche web côté serveur : indispensable pour
    les analyses qui doivent refléter l'état du marché à l'instant présent
    (concurrence, plan d'investissement).

    Ne lève jamais : les échecs sont renvoyés dans `AIResult.error`.
    """
    client = get_client()
    if client is None:
        return AIResult(error="Analyse IA désactivée : aucune clé API Claude.")

    model = model or CLAUDE_MODEL
    tools = [WEB_SEARCH_TOOL] if web_search else None

    params: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        # La réflexion adaptative laisse le modèle doser lui-même sa profondeur.
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": effort or CLAUDE_EFFORT,
            "format": {"type": "json_schema", "schema": schema},
        },
    }
    if tools:
        params["tools"] = tools

    messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]
    result = AIResult(model=model)
    all_content: List[Any] = []

    try:
        for restart in range(_MAX_PAUSE_RESTARTS + 1):
            message = _stream_once(client, {**params, "messages": messages})

            result.input_tokens += getattr(message.usage, "input_tokens", 0) or 0
            result.output_tokens += getattr(message.usage, "output_tokens", 0) or 0
            all_content.extend(message.content)

            if message.stop_reason == "refusal":
                category = getattr(getattr(message, "stop_details", None), "category", None)
                result.error = f"Requête déclinée par les garde-fous du modèle ({category or 'motif non précisé'})."
                return result

            # Un tour peut être mis en pause quand l'outil serveur atteint sa limite
            # d'itérations : on renvoie l'historique pour le reprendre là où il s'est arrêté.
            if message.stop_reason != "pause_turn":
                break

            if restart == _MAX_PAUSE_RESTARTS:
                result.error = "Tour toujours en pause après plusieurs reprises."
                return result

            messages = messages + [{"role": "assistant", "content": message.content}]
            logger.info("Reprise du tour en pause (%d/%d)", restart + 1, _MAX_PAUSE_RESTARTS)

        result.sources = _collect_sources(all_content)
        result.searches = sum(
            1 for b in all_content
            if getattr(b, "type", "") == "server_tool_use" and getattr(b, "name", "") == "web_search"
        )
        result.data = _extract_json(message.content)
        return result

    except anthropic.AuthenticationError:
        result.error = "Clé API Claude refusée (401)."
    except anthropic.PermissionDeniedError:
        result.error = "Droits insuffisants pour ce modèle ou cette fonctionnalité."
    except anthropic.NotFoundError:
        result.error = f"Modèle « {model} » introuvable."
    except anthropic.RateLimitError as exc:
        retry_after = exc.response.headers.get("retry-after", "60") if exc.response else "60"
        result.error = f"Quota atteint (429) — réessayez dans {retry_after} s."
    except anthropic.APIStatusError as exc:
        result.error = (
            f"Erreur serveur {exc.status_code}, réessayez plus tard."
            if exc.status_code >= 500 else f"Erreur API {exc.status_code} : {exc.message}"
        )
    except anthropic.APIConnectionError:
        result.error = "Connexion à l'API Claude impossible (réseau ou proxy)."
    except (json.JSONDecodeError, ValueError) as exc:
        result.error = f"Réponse IA illisible : {exc}"
    except Exception as exc:  # garde-fou : le dashboard ne doit jamais tomber
        logger.exception("Erreur inattendue lors de l'appel Claude")
        result.error = f"Erreur inattendue : {exc}"

    return result


def _stream_once(client: anthropic.Anthropic, params: Dict[str, Any]):
    """
    Un aller-retour avec l'API, en streaming.

    Le streaming évite les délais d'expiration HTTP sur les analyses longues
    (recherche web + effort élevé peuvent dépasser la minute).
    Le repli serveur est tenté puis désactivé définitivement si l'API le rejette.
    """
    if _feature_flags["fallbacks"]:
        try:
            with client.beta.messages.stream(
                **params, betas=[_FALLBACK_BETA], fallbacks="default"
            ) as stream:
                return stream.get_final_message()
        except (TypeError, anthropic.BadRequestError) as exc:
            _feature_flags["fallbacks"] = False
            logger.warning("Repli serveur indisponible, désactivé pour la session : %s", exc)

    with client.messages.stream(**params) as stream:
        return stream.get_final_message()
