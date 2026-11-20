"""Real, non-invented metadata for every model an engine can expose.

Why this module exists
----------------------
A model seen through a CLI (OpenCode, Codex) is **not** automatically local,
free or included in a subscription: the CLI is installed locally, but the
inference happens on the vendor's servers, billed according to the account that
authenticated the CLI. This module is the single place where KORA records, for
each model:

* the effective provider (``openai``, ``anthropic`` … also when reached through
  OpenCode, where ids look like ``openai/gpt-5.1``);
* the authentication type (OAuth subscription vs API key vs local, no auth);
* the billing type (subscription / free quota / paid API / local compute /
  **unknown**);
* the subscription name **only when it is verifiable**;
* the multimodal capabilities (**only when verified**, never guessed from a
  name fragment such as "vision");
* where the inference runs (local machine or remote servers);
* whether the cost is known, and the known cost when it is;
* the quota status.

Anything KORA cannot prove is recorded as ``unknown``: unknown cost is *blocked*
when the budget is zero, and unknown vision is never used for a multimodal task.
"""

from __future__ import annotations

from typing import Any, Optional

# --- authentication types -------------------------------------------------
AUTH_OAUTH_SUBSCRIPTION = "oauth_subscription"  # abbonamento gestito dal CLI ufficiale
AUTH_API_KEY = "api_key"
AUTH_LOCAL_NO_AUTH = "local_no_auth"
AUTH_UNKNOWN = "unknown"

AUTH_LABELS = {
    AUTH_OAUTH_SUBSCRIPTION: "Abbonamento OAuth (gestito dal CLI)",
    AUTH_API_KEY: "API key",
    AUTH_LOCAL_NO_AUTH: "Locale senza autenticazione",
    AUTH_UNKNOWN: "Non verificabile",
}

# --- billing types --------------------------------------------------------
BILLING_SUBSCRIPTION = "subscription"
BILLING_FREE_QUOTA = "free_quota"
BILLING_PAID_API = "paid_api"
BILLING_LOCAL = "local_compute"
BILLING_UNKNOWN = "unknown"

BILLING_LABELS = {
    BILLING_SUBSCRIPTION: "Incluso nell'abbonamento",
    BILLING_FREE_QUOTA: "Quota gratuita",
    BILLING_PAID_API: "API a consumo",
    BILLING_LOCAL: "Calcolo locale (0 €)",
    BILLING_UNKNOWN: "Fatturazione non verificabile",
}

#: billing values that can never generate an unrequested expense
ZERO_COST_BILLING = (BILLING_SUBSCRIPTION, BILLING_FREE_QUOTA, BILLING_LOCAL)

# --- execution location ---------------------------------------------------
EXEC_LOCAL = "local"
EXEC_REMOTE = "remote"
EXEC_UNKNOWN = "unknown"

# --- quota ----------------------------------------------------------------
QUOTA_AVAILABLE = "disponibile"
QUOTA_EXHAUSTED = "esaurita"
QUOTA_RATE_LIMITED = "rate_limit"
QUOTA_NOT_VERIFIABLE = "non_verificabile"

# --- known multimodal capabilities ---------------------------------------
# Curated registry: it lists only capabilities KORA can actually rely on.
# ``vision=True``  -> the model accepts image input per the vendor documentation.
# ``vision=False`` -> text only.
# Missing entry      -> **unknown**: never used for a vision task automatically.
KNOWN_MODELS: dict[str, dict[str, Any]] = {
    # OpenAI (API and ChatGPT plans through OpenCode/Codex)
    "gpt-4o": {"vision": True, "context": 128000},
    "gpt-4o-mini": {"vision": True, "context": 128000},
    "gpt-4.1": {"vision": True, "context": 1000000},
    "gpt-4.1-mini": {"vision": True, "context": 1000000},
    "gpt-5": {"vision": True},
    "gpt-5.1": {"vision": True},
    "gpt-5-mini": {"vision": True},
    "o4-mini": {"vision": True},
    # Anthropic
    "claude-sonnet-4": {"vision": True},
    "claude-sonnet-4-5": {"vision": True},
    "claude-opus-4": {"vision": True},
    "claude-haiku-4-5": {"vision": True},
    "claude-3-5-sonnet-latest": {"vision": True},
    "claude-3-5-haiku-latest": {"vision": False},
    # Google
    "gemini-2.5-flash": {"vision": True},
    "gemini-2.5-pro": {"vision": True},
    "gemini-2.0-flash": {"vision": True},
    "gemini-1.5-flash": {"vision": True},
    "gemini-flash-latest": {"vision": True},
    # DeepSeek: text only
    "deepseek-chat": {"vision": False},
    "deepseek-reasoner": {"vision": False},
    # Local families
    "llava": {"vision": True},
    "qwen2.5-vl": {"vision": True},
    "qwen2-vl": {"vision": True},
    "gemma3": {"vision": True},
    "llama3.1": {"vision": False},
    "qwen2.5-coder": {"vision": False},
}

#: families whose binaries/models are explicitly text-only (used for a *negative*
#: answer only: a positive capability always needs the registry above or a real test)
KNOWN_TEXT_ONLY_FAMILIES = ("deepseek", "qwen2.5-coder", "llama3", "codestral", "phi")

#: providers whose inference runs on the local machine
LOCAL_PROVIDER_IDS = {"ollama", "lmstudio", "llama.cpp", "llamacpp", "local", "vllm"}

#: paid API providers (list price per 1k input tokens, EUR, indicative)
PRICES_PER_1K: dict[str, float] = {
    "gpt-4o-mini": 0.00014,
    "gpt-4o": 0.0023,
    "gpt-4.1-mini": 0.00036,
    "gpt-4.1": 0.0018,
    "gpt-5": 0.0012,
    "gpt-5.1": 0.0012,
    "claude-sonnet-4": 0.0027,
    "claude-sonnet-4-5": 0.0027,
    "claude-haiku-4-5": 0.0007,
    "claude-3-5-sonnet-latest": 0.0027,
    "claude-3-5-haiku-latest": 0.0007,
    "gemini-2.5-flash": 0.00027,
    "gemini-2.5-pro": 0.0011,
    "gemini-2.0-flash": 0.00009,
    "gemini-1.5-flash": 0.00008,
    "gemini-flash-latest": 0.00027,
    "deepseek-chat": 0.00025,
    "deepseek-reasoner": 0.0006,
}

#: models known to be offered on a free tier (only when the provider exposes it)
FREE_TIER_PREFIXES = ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest")

#: plan words that make an account's subscription name *verifiable* in CLI output
PLAN_HINTS = ("plus", "pro", "max", "team", "business", "enterprise", "ultra", "chatgpt", "claude")


def canonical_model_id(model_id: str) -> str:
    """``openai/gpt-5.1`` -> ``gpt-5.1``; keeps the provider part out of capability lookup."""
    value = (model_id or "").strip()
    if "/" in value:
        return value.split("/", 1)[1]
    return value


def provider_of(model_id: str, default: str = "") -> str:
    value = (model_id or "").strip()
    if "/" in value:
        return value.split("/", 1)[0]
    return default


def lookup_known(model_id: str) -> Optional[dict[str, Any]]:
    """Exact capability entry, or the entry of the family the model belongs to."""
    canonical = canonical_model_id(model_id).lower()
    if canonical in KNOWN_MODELS:
        return KNOWN_MODELS[canonical]
    # variants such as ``gpt-4o-2024-08-06`` or ``llama3.1:8b``
    base = canonical.split(":", 1)[0]
    if base in KNOWN_MODELS:
        return KNOWN_MODELS[base]
    best: Optional[str] = None
    for key in KNOWN_MODELS:
        if canonical.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return KNOWN_MODELS[best] if best else None


def known_vision(model_id: str) -> Optional[bool]:
    """``True`` / ``False`` only when the capability is known; ``None`` otherwise."""
    entry = lookup_known(model_id)
    if entry is not None:
        return bool(entry.get("vision"))
    canonical = canonical_model_id(model_id).lower()
    if any(family in canonical for family in KNOWN_TEXT_ONLY_FAMILIES):
        return False
    return None


def known_price(model_id: str) -> Optional[float]:
    canonical = canonical_model_id(model_id)
    if canonical in PRICES_PER_1K:
        return PRICES_PER_1K[canonical]
    entry = lookup_known(canonical)
    if entry and "price_per_1k" in entry:
        return float(entry["price_per_1k"])
    return None


def subscription_from_label(label: str) -> Optional[str]:
    """Extract a *verifiable* subscription name from a CLI auth label, if present."""
    text = (label or "").strip()
    if not text:
        return None
    low = text.lower()
    if not any(hint in low for hint in PLAN_HINTS):
        return None
    # Keep the vendor wording, normalising known products.
    if "chatgpt" in low and "plus" in low:
        return "ChatGPT Plus"
    if "chatgpt" in low and "pro" in low:
        return "ChatGPT Pro"
    if "claude" in low and "max" in low:
        return "Claude Max"
    if "claude" in low and "pro" in low:
        return "Claude Pro"
    return text[:60]


def execution_for_provider(provider_id: str) -> str:
    pid = (provider_id or "").strip().lower()
    if pid in LOCAL_PROVIDER_IDS:
        return EXEC_LOCAL
    if not pid:
        return EXEC_UNKNOWN
    return EXEC_REMOTE


def vision_candidates(model_ids: list[str]) -> list[str]:
    """Models that may receive an image *because the capability is verified*."""
    return [m for m in model_ids if known_vision(m) is True]


#: Relative quality tiers used only by the "massima qualità" strategy.
QUALITY_TIERS: dict[str, int] = {
    "opus": 100,
    "gpt-5": 95,
    "gpt-5.1": 95,
    "sonnet": 90,
    "gpt-4.1": 85,
    "gemini-2.5-pro": 88,
    "gpt-4o": 80,
    "pro": 85,
    "gemini-2.5-flash": 70,
    "gpt-4.1-mini": 65,
    "flash": 60,
    "gpt-4o-mini": 55,
    "haiku": 50,
    "mini": 50,
    "coder": 45,
    "llama3": 40,
}


def quality_score(model_id: str) -> int:
    """Rough, documented ranking used by the max-quality strategy (never a cost claim)."""
    canonical = canonical_model_id(model_id).lower()
    best = 0
    for key, score in QUALITY_TIERS.items():
        if key in canonical:
            best = max(best, score)
    return best
