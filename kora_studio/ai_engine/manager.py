"""AI Engine Manager — the single entry point to every AI model used by KORA.

Design notes
------------
* Settings live in SQLite (``user_preferences.ai_engine_settings``): changing the
  engine takes effect on the next call, **no restart**.
* Credentials stay in the encrypted SecretVault through ProviderManager; this
  module only handles booleans and masked values.
* A provider is *operativo* only after a real generation succeeded.
* Every model carries honest metadata (billing, execution, cost, vision); a
  model with unknown cost is **blocked when the budget is zero**, and a model
  without verified vision is never used for a multimodal task.
* Every call is recorded in the AI Activity Monitor (no prompts, no secrets).
"""

from __future__ import annotations

import threading
import time
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from kora_studio.ai_engine.activity import AIActivityLog
from kora_studio.ai_engine.adapters_cli import CodexAdapter, OpenCodeAdapter, stage_attachments
from kora_studio.ai_engine.adapters_http import (
    AnthropicAdapter,
    CustomEndpointAdapter,
    DeepSeekAdapter,
    GeminiAdapter,
    OllamaAdapter,
    OpenAICompatibleAdapter,
    OpenRouterAdapter,
)
from kora_studio.ai_engine.agents import AGENTS, AGENTS_BY_ID, VISION_AGENTS
from kora_studio.ai_engine.base import (
    STATE_AUTHENTICATED,
    STATE_OPERATIONAL,
    STATE_UNAVAILABLE,
    STRATEGIES,
    STRATEGY_AUTO,
    STRATEGY_COST,
    STRATEGY_LOCAL,
    STRATEGY_QUALITY,
    AIProviderAdapter,
    EngineAuthError,
    EngineBlockedByBudget,
    EngineCancelled,
    EngineError,
    EngineModelUnavailable,
    EngineQuotaExhausted,
    EngineRateLimited,
    EngineResult,
    EngineTimeout,
    EngineVisionUnsupported,
    ModelInfo,
    now_ms,
)
from kora_studio.ai_engine.models import (
    AUTH_API_KEY,
    BILLING_FREE_QUOTA,
    BILLING_LOCAL,
    BILLING_PAID_API,
    BILLING_SUBSCRIPTION,
    BILLING_UNKNOWN,
    EXEC_LOCAL,
    QUOTA_EXHAUSTED,
    QUOTA_NOT_VERIFIABLE,
    canonical_model_id,
    provider_of,
    quality_score,
)

SETTINGS_KEY = "ai_engine_settings"
PROBE_TTL_SEC = 90.0
MODELS_TTL_SEC = 120.0

FALLBACK_OFF = "off"
FALLBACK_FREE_ONLY = "free_only"
FALLBACK_AUTHORIZED = "authorized"
FALLBACK_MODES = (FALLBACK_OFF, FALLBACK_FREE_ONLY, FALLBACK_AUTHORIZED)

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 2,
    "strategy": STRATEGY_AUTO,
    "primary_provider": "auto",
    "fallback": FALLBACK_FREE_ONLY,
    "max_budget_eur": 0.0,
    "allow_paid_calls": False,
    "allow_unknown_costs": False,
    "allow_external_media": False,
    #: provider -> plan name declared by the user (e.g. {"openai": "ChatGPT Plus"})
    "declared_subscriptions": {},
    #: provider -> True when the user declares a free quota for that account
    "declared_free_quota": {},
    #: "provider/model" -> True/False once a real image test proved the capability
    "vision_verified": {},
    "providers": {},
    "agents": {},
}

PROVIDER_ORDER = (
    "opencode",
    "codex",
    "ollama_local",
    "custom_openai",
    "gemini_api",
    "openai_api",
    "openrouter",
    "claude_api",
    "deepseek_api",
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AIEngineManager:
    """Facade used by the API, the UI and every KORA agent."""

    def __init__(self, providers: Any, db: Any = None, context_builder: Any = None) -> None:
        self.providers = providers
        self.db = db if db is not None else getattr(providers, "db", None)
        self.context_builder = context_builder
        self._probe_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._models_cache: dict[str, tuple[float, list[ModelInfo]]] = {}
        self._adapters: dict[str, AIProviderAdapter] = {}
        self._cancellations: dict[str, threading.Event] = {}
        self.activity = AIActivityLog(self.db)

    # ------------------------------------------------------------------ settings
    def settings(self) -> dict[str, Any]:
        stored = {}
        if self.db is not None:
            try:
                stored = self.db.get_preference(SETTINGS_KEY, {}) or {}
            except Exception:
                stored = {}
        merged = {**DEFAULT_SETTINGS, **stored}
        for key in ("providers", "agents", "declared_subscriptions", "declared_free_quota", "vision_verified"):
            base = DEFAULT_SETTINGS[key]
            merged[key] = {**base, **(stored.get(key) or {})}
        if merged.get("strategy") not in STRATEGIES:
            merged["strategy"] = STRATEGY_AUTO
        if merged.get("fallback") not in FALLBACK_MODES:
            merged["fallback"] = FALLBACK_FREE_ONLY
        return merged

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.settings()
        if "strategy" in patch:
            if patch["strategy"] not in STRATEGIES:
                raise ValueError(f"Strategia non valida: {patch['strategy']}")
            current["strategy"] = patch["strategy"]
        if "fallback" in patch:
            if patch["fallback"] not in FALLBACK_MODES:
                raise ValueError(f"Politica di fallback non valida: {patch['fallback']}")
            current["fallback"] = patch["fallback"]
        if "primary_provider" in patch:
            value = str(patch["primary_provider"] or "auto")
            if value != "auto" and value not in PROVIDER_ORDER:
                raise ValueError(f"Provider non valido: {value}")
            current["primary_provider"] = value
        if "max_budget_eur" in patch:
            value = float(patch["max_budget_eur"] or 0.0)
            if value < 0 or value > 20:
                raise ValueError("Budget massimo fuori intervallo (0–20 €)")
            current["max_budget_eur"] = value
        for flag in ("allow_paid_calls", "allow_unknown_costs", "allow_external_media"):
            if flag in patch:
                current[flag] = bool(patch[flag])
        declarations_changed = False
        if isinstance(patch.get("declared_subscriptions"), dict):
            for provider_id, name in patch["declared_subscriptions"].items():
                if provider_id not in PROVIDER_ORDER:
                    raise ValueError(f"Provider non valido: {provider_id}")
                if name:
                    current["declared_subscriptions"][provider_id] = str(name)[:60]
                    declarations_changed = True
                else:
                    current["declared_subscriptions"].pop(provider_id, None)
                declarations_changed = True
        if isinstance(patch.get("declared_free_quota"), dict):
            for provider_id, value in patch["declared_free_quota"].items():
                if provider_id not in PROVIDER_ORDER:
                    raise ValueError(f"Provider non valido: {provider_id}")
                if value:
                    current["declared_free_quota"][provider_id] = True
                    declarations_changed = True
                else:
                    current["declared_free_quota"].pop(provider_id, None)
        if isinstance(patch.get("providers"), dict):
            for provider_id, conf in patch["providers"].items():
                if provider_id not in PROVIDER_ORDER:
                    raise ValueError(f"Provider non valido: {provider_id}")
                current["providers"][provider_id] = {
                    **(current["providers"].get(provider_id) or {}),
                    **{k: v for k, v in (conf or {}).items() if k in ("enabled", "model", "base_url")},
                }
                self.invalidate(provider_id)
        if isinstance(patch.get("agents"), dict):
            for agent_id, conf in patch["agents"].items():
                if agent_id not in AGENTS_BY_ID:
                    raise ValueError(f"Agente non valido: {agent_id}")
                current["agents"][agent_id] = self._validate_agent_conf(agent_id, conf or {})
        if declarations_changed:
            # le dichiarazioni cambiano il costo dei modelli: le cache vanno rigenerate
            self._models_cache.clear()
            self._probe_cache.clear()
        if self.db is not None:
            self.db.set_preference(SETTINGS_KEY, current)
        return current

    def _validate_agent_conf(self, agent_id: str, conf: dict[str, Any]) -> dict[str, Any]:
        mode = str(conf.get("mode") or "inherit")
        if mode not in ("inherit", "provider"):
            raise ValueError("Modalità agente non valida (inherit|provider)")
        if mode == "inherit":
            return {"mode": "inherit"}
        provider_id = str(conf.get("provider") or "")
        if provider_id not in PROVIDER_ORDER:
            raise ValueError(f"Provider non valido per l'agente {agent_id}: {provider_id}")
        return {
            "mode": "provider",
            "provider": provider_id,
            "model": str(conf.get("model") or "") or None,
        }

    # ------------------------------------------------------------------ adapters
    def adapter(self, provider_id: str) -> Optional[AIProviderAdapter]:
        if provider_id in self._adapters:
            return self._adapters[provider_id]
        conf = (self.settings().get("providers") or {}).get(provider_id) or {}
        adapter: Optional[AIProviderAdapter]
        if provider_id == "opencode":
            adapter = OpenCodeAdapter(self.providers)
        elif provider_id == "codex":
            adapter = CodexAdapter(self.providers)
        elif provider_id == "ollama_local":
            adapter = OllamaAdapter(self.providers)
        elif provider_id == "gemini_api":
            adapter = GeminiAdapter(self.providers)
        elif provider_id == "openai_api":
            adapter = OpenAICompatibleAdapter(self.providers)
        elif provider_id == "openrouter":
            adapter = OpenRouterAdapter(self.providers)
        elif provider_id == "claude_api":
            adapter = AnthropicAdapter(self.providers)
        elif provider_id == "deepseek_api":
            adapter = DeepSeekAdapter(self.providers)
        elif provider_id == "custom_openai":
            adapter = CustomEndpointAdapter(self.providers, base_url=conf.get("base_url"))
        else:
            adapter = None
        if adapter is not None:
            self._adapters[provider_id] = adapter
        return adapter

    def _reset_adapters(self) -> None:
        self._adapters.clear()
        self._probe_cache.clear()
        self._models_cache.clear()

    def invalidate(self, provider_id: str) -> None:
        """Forget cached adapter/probe/models for one provider (config/key changed)."""
        self._adapters.pop(provider_id, None)
        self._probe_cache.pop(provider_id, None)
        self._models_cache.pop(provider_id, None)

    # ----------------------------------------------------------------- metadata
    def _enrich_model(self, info: ModelInfo, provider_id: Optional[str] = None) -> ModelInfo:
        """Apply user declarations and verified capabilities on top of the raw metadata."""
        settings = self.settings()
        provider = info.provider or provider_id or info.source
        declared = settings.get("declared_subscriptions") or {}
        declared_plan = declared.get(provider_id or "") or declared.get(provider) or declared.get(info.source or "")
        if declared_plan and info.billing in (BILLING_UNKNOWN, BILLING_PAID_API):
            info.billing = BILLING_SUBSCRIPTION
            info.subscription_name = declared_plan
            info.cost_per_1k_eur = 0.0
            info.notes = f"piano dichiarato dall'utente: {declared_plan}"
        elif (
            (settings.get("declared_free_quota") or {}).get(provider_id or "")
            or (settings.get("declared_free_quota") or {}).get(provider)
        ) and info.billing == BILLING_UNKNOWN:
            info.billing = BILLING_FREE_QUOTA
            info.cost_per_1k_eur = 0.0
            info.notes = "quota gratuita dichiarata dall'utente"
        canonical = canonical_model_id(info.id)
        verified_map = settings.get("vision_verified") or {}
        verified = None
        for first in (info.source, provider, provider_id, "api"):
            for second in (provider, provider_id, info.source):
                if not first or not second:
                    continue
                key = f"{first}|{second}|{canonical}"
                if key in verified_map:
                    verified = verified_map[key]
                    break
            if verified is not None:
                break
        if verified is not None:
            info.vision = bool(verified)
            info.vision_verified = True
            if verified:
                info.notes = (info.notes + " · visione verificata con test reale").strip(" ·")
        return info

    def models(self, provider_id: str, refresh: bool = True) -> list[dict[str, Any]]:
        adapter = self.adapter(provider_id)
        if adapter is None:
            raise ValueError(f"Provider non valido: {provider_id}")
        cached = self._models_cache.get(provider_id)
        if cached and not refresh and (time.time() - cached[0]) < MODELS_TTL_SEC:
            return [m.to_dict() for m in cached[1]]
        try:
            raw = adapter.list_models()
        except EngineError:
            raw = []
        enriched = [self._enrich_model(m, provider_id) for m in raw]
        self._models_cache[provider_id] = (time.time(), enriched)
        return [m.to_dict() for m in enriched]

    def _models_cached(self, provider_id: str) -> list[ModelInfo]:
        adapter = self.adapter(provider_id)
        if adapter is None:
            return []
        cached = self._models_cache.get(provider_id)
        if cached and (time.time() - cached[0]) < MODELS_TTL_SEC:
            return cached[1]
        try:
            raw = adapter.list_models()
        except EngineError:
            raw = []
        enriched = [self._enrich_model(m, provider_id) for m in raw]
        self._models_cache[provider_id] = (time.time(), enriched)
        return enriched

    def _mark_operational(self, provider_id: str, ok: bool, latency_ms: Optional[int] = None) -> None:
        settings = self.settings()
        conf = dict(settings["providers"].get(provider_id) or {})
        conf["verified_operational"] = ok
        conf["last_test_at"] = _utcnow_iso()
        conf["last_test_ok"] = bool(ok)
        if latency_ms is not None:
            conf["last_latency_ms"] = int(latency_ms)
        settings["providers"][provider_id] = conf
        if self.db is not None:
            self.db.set_preference(SETTINGS_KEY, settings)
        self._probe_cache.pop(provider_id, None)

    # -------------------------------------------------------------------- probes
    def probe(self, provider_id: str, refresh: bool = False) -> dict[str, Any]:
        cached = self._probe_cache.get(provider_id)
        if cached and not refresh and (time.time() - cached[0]) < PROBE_TTL_SEC:
            return cached[1]
        adapter = self.adapter(provider_id)
        if adapter is None:
            result = {
                "provider": provider_id,
                "state": STATE_UNAVAILABLE,
                "installed": False,
                "authenticated": False,
                "detail": "adapter sconosciuto",
                "account": None,
                "version": None,
                "binary": None,
                "models_count": 0,
                "verified_operational": False,
            }
            self._probe_cache[provider_id] = (time.time(), result)
            return result

        detection = adapter.detect()
        auth = (
            adapter.authenticated()
            if detection.get("installed")
            else {"authenticated": False, "detail": "non installato", "account": None}
        )
        if not detection.get("installed"):
            state = STATE_UNAVAILABLE
        elif adapter.requires_key and not adapter.credentials_present():
            state = STATE_UNAVAILABLE
            auth = {
                "authenticated": False,
                "detail": "API key non configurata: provider non utilizzabile",
                "account": None,
            }
        elif not auth.get("authenticated"):
            state = "installato"
        else:
            state = STATE_AUTHENTICATED
        settings = self.settings()
        provider_conf = settings.get("providers", {}).get(provider_id) or {}
        verified = provider_conf.get("verified_operational")
        if state == STATE_AUTHENTICATED and verified:
            state = STATE_OPERATIONAL
        models: list[ModelInfo] = []
        if state != STATE_UNAVAILABLE:
            try:
                models = adapter.list_models()
            except EngineError:
                models = []
        cost_note = [m.billing for m in models]
        result = {
            "provider": provider_id,
            "label": adapter.label,
            "kind": "cli" if adapter.is_cli else "http",
            "local": adapter.is_local,
            "subscription": adapter.subscription_quota,
            "requires_key": adapter.requires_key,
            "state": state,
            "installed": bool(detection.get("installed")),
            "authenticated": bool(auth.get("authenticated")),
            "version": detection.get("version"),
            "binary": detection.get("binary") if adapter.is_cli else None,
            "detail": (
                detection.get("detail") or ""
                if auth.get("authenticated")
                else (auth.get("detail") or detection.get("detail") or "")
            ),
            "auth_detail": auth.get("detail") or "",
            "account": auth.get("account"),
            "models_count": len(models),
            "supports_vision": any(m.vision is True for m in models),
            "unknown_vision_models": sum(1 for m in models if m.vision is None),
            "unknown_cost_models": sum(1 for m in models if m.cost_per_1k_eur is None),
            "billing_kinds": sorted(set(m.billing for m in models)),
            "attachments": adapter.supports_attachments,
            "attachment_mode": adapter.attachment_mode,
            "verified_operational": state == STATE_OPERATIONAL,
            "last_test_at": provider_conf.get("last_test_at"),
            "last_test_ok": provider_conf.get("last_test_ok"),
            "last_latency_ms": provider_conf.get("last_latency_ms"),
            "docs_url": getattr(adapter, "docs_url", ""),
            "cost_summary": {
                "zero_cost_models": sum(1 for m in models if m.zero_cost),
                "paid_models": cost_note.count(BILLING_PAID_API),
                "unknown": sum(1 for m in models if m.cost_per_1k_eur is None),
            },
        }
        self._probe_cache[provider_id] = (time.time(), result)
        return result

    def status_board(self, refresh: bool = False) -> dict[str, Any]:
        providers = [self.probe(pid, refresh=refresh) for pid in PROVIDER_ORDER]
        settings = self.settings()
        return {
            "settings": self._public_settings(),
            "providers": providers,
            "agents": [self.agent_configuration(agent.id) for agent in AGENTS],
            "strategies": list(STRATEGIES),
            "fallback_modes": list(FALLBACK_MODES),
            "ollama_installed": next(
                (bool(p.get("installed")) for p in providers if p["provider"] == "ollama_local"),
                False,
            ),
            "ollama_required": False,
            "operational": [p["provider"] for p in providers if p["state"] == STATE_OPERATIONAL],
            "budget": {
                "max_budget_eur": settings["max_budget_eur"],
                "allow_paid_calls": settings["allow_paid_calls"],
                "allow_unknown_costs": settings["allow_unknown_costs"],
                "spent_note": "Costo registrato solo dalle chiamate con prezzo pubblicato; saldo non verificabile.",
            },
            "activity": self.activity.summary(limit=200),
        }

    def _public_settings(self) -> dict[str, Any]:
        settings = self.settings()
        return {
            "strategy": settings["strategy"],
            "primary_provider": settings["primary_provider"],
            "fallback": settings["fallback"],
            "max_budget_eur": settings["max_budget_eur"],
            "allow_paid_calls": settings["allow_paid_calls"],
            "allow_unknown_costs": settings["allow_unknown_costs"],
            "allow_external_media": settings["allow_external_media"],
            "declared_subscriptions": settings["declared_subscriptions"],
            "declared_free_quota": settings["declared_free_quota"],
            "providers": settings.get("providers") or {},
            "agents": settings.get("agents") or {},
        }

    def _configured_model(self, provider_id: str) -> Optional[str]:
        return ((self.settings().get("providers") or {}).get(provider_id) or {}).get("model") or None

    def agent_configuration(self, agent_id: str) -> dict[str, Any]:
        spec = AGENTS_BY_ID.get(agent_id)
        if spec is None:
            raise ValueError(f"Agente non valido: {agent_id}")
        conf = (self.settings().get("agents") or {}).get(agent_id) or {}
        return {
            "id": spec.id,
            "label": spec.label,
            "needs_vision": spec.needs_vision,
            "modality": spec.modality,
            "description": spec.description,
            "mode": conf.get("mode", "inherit"),
            "provider": conf.get("provider"),
            "model": conf.get("model"),
        }

    # ------------------------------------------------------------------- budget
    def _estimate_cost(self, entry: dict[str, Any], prompt_chars: int) -> Optional[float]:
        price = entry.get("cost_per_1k_eur")
        if price is None:
            return None
        # ~4x the prompt length covers the completion at KORA's short answers
        return round((max(prompt_chars, 200) / 1000.0) * float(price) * 4, 5)

    def _authorize(self, entry: dict[str, Any], estimated: Optional[float]) -> tuple[bool, str]:
        settings = self.settings()
        if entry.get("zero_cost"):
            return True, "costo zero (abbonamento, quota gratuita o calcolo locale)"
        if estimated is None and not settings.get("allow_unknown_costs"):
            # un costo non verificabile è bloccato SEMPRE, a meno che l'utente
            # non abbia autorizzato esplicitamente i costi sconosciuti
            return False, "costo sconosciuto: autorizzazione specifica non concessa"
        if not settings.get("allow_paid_calls"):
            return False, "chiamate a pagamento non autorizzate"
        budget = float(settings.get("max_budget_eur") or 0.0)
        if budget <= 0:
            return False, "budget a 0 €"
        if estimated is None:
            estimated = 0.001
        if estimated > budget:
            return False, f"costo stimato {estimated:.3f} € oltre il budget di {budget:.2f} €"
        try:
            verdict = self.providers.cost_guard.check_authorization(
                max(estimated, 0.001), explicit_user_authorization=True
            )
        except Exception:
            return False, "controllo economico non disponibile"
        if not verdict.get("authorized"):
            return False, str(verdict.get("reason") or "budget esaurito")
        return True, "autorizzata"

    # ------------------------------------------------------------------ routing
    def _entry(self, provider_id: str, info: ModelInfo, *, preferred: bool, explicit: bool) -> dict[str, Any]:
        return {
            "provider": provider_id,
            "actual_provider": info.provider or info.source or provider_id,
            "model": info.id,
            "label": info.label or canonical_model_id(info.id),
            "vision": info.vision,
            "vision_verified": info.vision_verified,
            "billing": info.billing,
            "execution": info.execution,
            "cost_per_1k_eur": info.cost_per_1k_eur,
            "cost_known": info.cost_per_1k_eur is not None,
            "zero_cost": info.zero_cost,
            "subscription_name": info.subscription_name,
            "quota_status": info.quota_status,
            "preferred": preferred,
            "explicit": explicit,
            "quality": quality_score(info.id),
        }

    def candidate_plan(
        self,
        agent_id: str,
        requires_vision: bool = False,
        *,
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Ordered candidates: explicit choice first, fallback only when authorised."""
        if agent_id not in AGENTS_BY_ID:
            raise ValueError(f"Agente non valido: {agent_id}")
        settings = self.settings()
        strategy = settings["strategy"]
        needs_vision = requires_vision or agent_id in VISION_AGENTS

        agent_conf = settings["agents"].get(agent_id) or {}
        explicit_provider = provider_override
        explicit_model = model_override
        if explicit_provider is None and agent_conf.get("mode") == "provider":
            explicit_provider = agent_conf.get("provider")
            explicit_model = explicit_model or agent_conf.get("model")
        if explicit_provider is None and model_override and "/" in str(model_override):
            explicit_provider = provider_of(str(model_override))

        primary = settings["primary_provider"]
        if explicit_provider:
            order = [explicit_provider] + [p for p in PROVIDER_ORDER if p != explicit_provider]
        elif primary != "auto":
            order = [primary] + [p for p in PROVIDER_ORDER if p != primary]
        else:
            order = list(PROVIDER_ORDER)

        fallback = settings.get("fallback", FALLBACK_FREE_ONLY)
        strict = bool(explicit_provider)

        plan: list[dict[str, Any]] = []
        for position, provider_id in enumerate(order):
            probe = self.probe(provider_id)
            if not probe.get("authenticated"):
                continue
            adapter = self.adapter(provider_id)
            if adapter is None:
                continue
            infos = self._models_cached(provider_id)
            if not infos:
                continue
            conf_model = self._configured_model(provider_id)
            for info in infos:
                if not info.text:
                    continue
                if info.quota_status == QUOTA_EXHAUSTED:
                    continue
                if needs_vision and not info.vision_verified:
                    continue  # capability documented ≠ verified: never used for images
                if strategy == STRATEGY_LOCAL and info.execution != EXEC_LOCAL:
                    continue
                model_pref = None
                if explicit_model and provider_id == explicit_provider:
                    model_pref = explicit_model
                model_pref = model_pref or conf_model
                plan.append(
                    self._entry(
                        provider_id,
                        info,
                        preferred=bool(model_pref and canonical_model_id(info.id) == canonical_model_id(model_pref)),
                        explicit=bool(strict and position == 0 and provider_id == explicit_provider),
                    )
                )

        # explicit model requested: it must be the only candidate from that provider
        if explicit_model:
            filtered = [
                e
                for e in plan
                if not (e["provider"] == explicit_provider)
                or canonical_model_id(e["model"]) == canonical_model_id(explicit_model)
            ]
            plan = filtered

        def _score(entry: dict[str, Any]) -> tuple:
            if strategy == STRATEGY_QUALITY:
                return (
                    0 if entry["preferred"] else 1,
                    0 if entry["zero_cost"] else 1,
                    -int(entry["quality"]),
                    0 if entry["cost_known"] else 1,
                    float(entry["cost_per_1k_eur"] or 0),
                )
            if strategy == STRATEGY_COST:
                return (
                    0 if entry["preferred"] else 1,
                    0 if entry["zero_cost"] else 1,
                    0 if entry["cost_known"] else 1,
                    float(entry["cost_per_1k_eur"] or 0),
                )
            if strategy == STRATEGY_LOCAL:
                return (0 if entry["preferred"] else 1, float(entry["cost_per_1k_eur"] or 0))
            return (
                0 if entry["preferred"] else 1,
                0 if entry["zero_cost"] else 1,
                0 if entry["cost_known"] else 1,
                float(entry["cost_per_1k_eur"] or 0),
            )

        plan.sort(key=_score)

        # explicit provider/model is respected: fallback only when the user allowed it
        if strict:
            first_provider = plan[0]["provider"] if plan else explicit_provider
            kept: list[dict[str, Any]] = []
            for entry in plan:
                if entry["provider"] == first_provider:
                    kept.append(entry)
                    continue
                if fallback == FALLBACK_OFF:
                    continue
                if fallback == FALLBACK_FREE_ONLY and not entry["zero_cost"]:
                    continue
                kept.append(entry)
            plan = kept
        return plan

    def resolve(
        self,
        agent_id: str,
        requires_vision: bool = False,
        *,
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        plan = self.candidate_plan(
            agent_id,
            requires_vision=requires_vision,
            provider_override=provider_override,
            model_override=model_override,
        )
        for entry in plan:
            estimated = self._estimate_cost(entry, 600)
            ok, _reason = self._authorize(entry, estimated)
            if ok:
                return entry
        return None

    def routing_report(self, agent_id: str, requires_vision: bool = False) -> dict[str, Any]:
        plan = self.candidate_plan(agent_id, requires_vision=requires_vision)
        evaluated = []
        for entry in plan[:12]:
            estimated = self._estimate_cost(entry, 600)
            ok, reason = self._authorize(entry, estimated)
            evaluated.append({**entry, "authorized": ok, "blocked_reason": None if ok else reason, "estimated_eur": estimated})
        return {
            "agent_id": agent_id,
            "requires_vision": requires_vision or agent_id in VISION_AGENTS,
            "plan": evaluated,
            "selected": next((e for e in evaluated if e["authorized"]), None),
        }

    # ------------------------------------------------------------- cancellation
    def cancellation_key(self, production_id: Optional[str]) -> Optional[str]:
        return production_id or None

    def cancel(self, production_id: str) -> dict[str, Any]:
        event = self._cancellations.pop(production_id, None)
        if event is None:
            return {"cancelled": False, "detail": "nessuna richiesta attiva per questa produzione"}
        event.set()
        return {"cancelled": True, "detail": "annullamento richiesto"}

    def _register(self, key: Optional[str]) -> tuple[Optional[str], Callable[[], bool]]:
        if not key:
            return None, lambda: False
        event = self._cancellations.get(key) or threading.Event()
        event.clear()
        self._cancellations[key] = event
        return key, event.is_set

    def _unregister(self, key: Optional[str]) -> None:
        if key:
            self._cancellations.pop(key, None)

    # ----------------------------------------------------------------- context
    def _with_brand_context(self, agent_id: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Antepone il blocco KORA canonico: stessa memoria per qualsiasi motore."""
        if self.context_builder is None:
            return list(messages)
        try:
            block = self.context_builder.to_prompt_block(self.context_builder.build(agent_id))
        except Exception:
            return list(messages)
        if not block:
            return list(messages)
        return [{"role": "system", "content": block}] + list(messages)

    @staticmethod
    def _image_parts(messages: list[dict[str, Any]]) -> list[Path]:
        """Images already materialised as files (written by the caller)."""
        paths: list[Path] = []
        for message in messages:
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_path" and part.get("path"):
                        paths.append(Path(part["path"]))
        return paths

    # ----------------------------------------------------------------- execution
    def call(
        self,
        agent_id: str,
        messages: list[dict[str, Any]],
        *,
        requires_vision: bool = False,
        json_mode: bool = True,
        timeout: float = 120.0,
        image_paths: Optional[list[Path]] = None,
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
        production_id: Optional[str] = None,
        max_attempts: int = 1,
    ) -> EngineResult:
        if agent_id not in AGENTS_BY_ID:
            raise EngineError(f"Agente sconosciuto: {agent_id}")
        images = [Path(p) for p in (image_paths or [])] + self._image_parts(messages)
        images = [p for p in images if p.is_file()]
        needs_vision = requires_vision or agent_id in VISION_AGENTS or bool(images)

        plan = self.candidate_plan(
            agent_id,
            requires_vision=needs_vision,
            provider_override=provider_override,
            model_override=model_override,
        )
        if needs_vision and not plan:
            raise EngineVisionUnsupported(
                f"{agent_id}: nessun modello con visione VERIFICATA disponibile "
                "(i modelli con capacità non verificata non vengono usati per le immagini)"
            )

        settings = self.settings()
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
        errors: list[str] = []
        attempts_total = 0
        fallback_from: Optional[str] = None
        cancel_key, is_cancelled = self._register(production_id)
        started_wall = _utcnow_iso()
        started = now_ms()

        try:
            for index, entry in enumerate(plan):
                estimated = self._estimate_cost(entry, prompt_chars)
                authorized, reason = self._authorize(entry, estimated)
                if not authorized:
                    errors.append(f"{entry['provider']}/{entry['model']}: {reason}")
                    continue
                adapter = self.adapter(entry["provider"])
                if adapter is None:
                    continue
                if images and not adapter.supports_attachments:
                    errors.append(
                        f"{entry['provider']}: il motore non trasferisce allegati immagine "
                        "(nessun invio silenzioso)"
                    )
                    continue
                if index > 0 and fallback_from is None:
                    fallback_from = plan[0]["provider"] if plan else None
                payload = self._with_brand_context(agent_id, messages)
                staged = stage_attachments(images, adapter.workspace() if adapter.is_cli else None) if images else []
                attempt = 0
                while attempt < max(1, max_attempts):
                    attempt += 1
                    attempts_total += 1
                    if is_cancelled():
                        raise EngineCancelled(f"{agent_id}: richiesta annullata dall'utente")
                    try:
                        result = adapter.generate(
                            payload,
                            model=entry["model"],
                            timeout=timeout,
                            json_mode=json_mode,
                            attachments=staged or None,
                            cancel=is_cancelled,
                        )
                    except EngineCancelled:
                        self._log(
                            production_id, agent_id, entry, started_wall, started,
                            outcome="cancelled", attempts=attempts_total,
                            fallback_used=fallback_from is not None, fallback_from=fallback_from,
                            cost=None, error_kind="annullata", error_message="annullata", cancelled=True,
                        )
                        raise
                    except (EngineRateLimited, EngineTimeout) as exc:
                        errors.append(f"{entry['provider']}/{entry['model']}: {exc}")
                        if attempt < max_attempts:
                            time.sleep(min(2.0 * attempt, 4.0))
                            continue
                        break
                    except (EngineAuthError, EngineModelUnavailable, EngineQuotaExhausted) as exc:
                        errors.append(f"{entry['provider']}/{entry['model']}: {exc}")
                        self._mark_operational(entry["provider"], False)
                        break
                    except EngineError as exc:
                        errors.append(f"{entry['provider']}/{entry['model']}: {exc}")
                        break

                    # success
                    result.attempts = attempts_total
                    result.fallback_used = fallback_from is not None and fallback_from != entry["provider"]
                    result.provider = entry["provider"]
                    result.model = entry["model"]
                    result.billing = entry["billing"]
                    result.execution = entry["execution"]
                    if result.cost_eur is None and entry["cost_known"]:
                        result.cost_eur = 0.0 if entry["zero_cost"] else estimated
                    if result.cost_eur is not None and result.cost_eur > 0:
                        try:
                            self.providers.cost_guard.record_usage(
                                entry["provider"], agent_id, result.cost_eur, result.cost_eur, True, "completed"
                            )
                        except Exception:
                            pass
                    if entry["execution"] == EXEC_LOCAL:
                        result.local = True
                    result.raw = {
                        **(result.raw or {}),
                        "agent": agent_id,
                        "attempts": list(errors),
                        "fallback_from": fallback_from,
                        "estimated_eur": estimated,
                        "production_id": production_id,
                        "actual_provider": entry.get("actual_provider"),
                    }
                    self._mark_operational(entry["provider"], True)
                    self._log(
                        production_id, agent_id, entry, started_wall, started,
                        outcome="ok", attempts=attempts_total,
                        fallback_used=bool(result.fallback_used), fallback_from=fallback_from,
                        cost=result.cost_eur,
                    )
                    return result

            self._log(
                production_id, agent_id,
                {"provider": (plan[0]["provider"] if plan else ""), "model": (plan[0]["model"] if plan else "")},
                started_wall, started,
                outcome="error", attempts=attempts_total, fallback_used=False, fallback_from=None,
                cost=None, error_kind="nessun_motore", error_message="; ".join(errors)[:300],
            )
            detail = "; ".join(errors) if errors else "Nessun motore AI disponibile con i criteri richiesti"
            if any("budget" in e or "pagamento" in e or "costo sconosciuto" in e for e in errors):
                raise EngineBlockedByBudget(detail)
            raise EngineError(detail)
        finally:
            self._unregister(cancel_key)

    def _log(
        self,
        production_id: Optional[str],
        agent_id: str,
        entry: dict[str, Any],
        started_wall: str,
        started_ms: int,
        *,
        outcome: str,
        attempts: int,
        fallback_used: bool,
        fallback_from: Optional[str],
        cost: Optional[float],
        error_kind: str = "",
        error_message: str = "",
        cancelled: bool = False,
    ) -> None:
        self.activity.record(
            production_id=production_id,
            agent=agent_id,
            provider=entry.get("provider", ""),
            model=entry.get("model", ""),
            started_at=started_wall,
            duration_ms=now_ms() - started_ms,
            outcome=outcome,
            attempts=attempts,
            fallback_used=fallback_used,
            fallback_from=fallback_from,
            cost_eur=cost,
            billing=entry.get("billing", BILLING_UNKNOWN),
            execution=entry.get("execution", "unknown"),
            error_kind=error_kind,
            error_message=error_message,
            cancelled=cancelled,
        )

    # -------------------------------------------------------------- diagnostics
    def test_provider(
        self,
        provider_id: str,
        prompt: str = "Rispondi solo con: KORA OK",
        model: Optional[str] = None,
        image_path: Optional[Path] = None,
        expected_content: Optional[str] = None,
        timeout: float = 45.0,
    ) -> dict[str, Any]:
        adapter = self.adapter(provider_id)
        if adapter is None:
            return {"ok": False, "provider": provider_id, "detail": "provider sconosciuto"}
        cached = self._models_cached(provider_id)
        info = next(
            (m for m in cached if canonical_model_id(m.id) == canonical_model_id(model or "")),
            None,
        )
        chosen = model or self._configured_model(provider_id)
        if chosen is None and cached:
            pool = [m for m in cached if m.text and (m.vision is True or not image_path)]
            chosen = (pool or cached)[0].id
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "Sei un test di connessione. Rispondi breve, in italiano."},
            {"role": "user", "content": prompt},
        ]
        started = now_ms()
        try:
            if image_path and Path(image_path).is_file():
                if not adapter.supports_attachments:
                    return {
                        "ok": False,
                        "provider": provider_id,
                        "detail": "questo motore non trasferisce immagini: test multimodale non eseguibile",
                    }
                result = adapter.generate(
                    messages,
                    model=chosen,
                    timeout=timeout,
                    json_mode=False,
                    attachments=[Path(image_path)],
                )
            else:
                result = adapter.generate(messages, model=chosen, timeout=timeout, json_mode=False)
        except EngineError as exc:
            self._mark_operational(provider_id, False)
            self.activity.record(
                production_id=None,
                agent="engine_test",
                provider=provider_id,
                model=chosen or "",
                started_at=_utcnow_iso(),
                duration_ms=now_ms() - started,
                outcome="error",
                attempts=1,
                fallback_used=False,
                fallback_from=None,
                cost_eur=None,
                billing=BILLING_UNKNOWN,
                execution="unknown",
                error_kind=exc.kind,
                error_message=str(exc)[:300],
            )
            return {"ok": False, "provider": provider_id, "detail": str(exc), "kind": exc.kind}
        except Exception as exc:  # pragma: no cover - defensive
            self._mark_operational(provider_id, False)
            self.activity.record(
                production_id=None,
                agent="engine_test",
                provider=provider_id,
                model=chosen or "",
                started_at=_utcnow_iso(),
                duration_ms=now_ms() - started,
                outcome="error",
                attempts=1,
                fallback_used=False,
                fallback_from=None,
                cost_eur=None,
                billing=BILLING_UNKNOWN,
                execution="unknown",
                error_kind=exc.__class__.__name__,
                error_message=str(exc)[:300],
            )
            return {"ok": False, "provider": provider_id, "detail": f"errore imprevisto ({exc.__class__.__name__})"}

        vision_ok: Optional[bool] = None
        if image_path and expected_content:
            vision_ok = expected_content.lower() in (result.text or "").lower()
        self._mark_operational(provider_id, True, latency_ms=result.latency_ms or (now_ms() - started))
        if vision_ok is not None:
            canonical = canonical_model_id(chosen or "")
            settings = self.settings()
            for key in (
                f"{adapter.kind}|{result.provider}|{canonical}",
                f"{adapter.kind}|{adapter.kind}|{canonical}",
            ):
                settings["vision_verified"][key] = bool(vision_ok)
            if self.db is not None:
                self.db.set_preference(SETTINGS_KEY, settings)
            self.invalidate(provider_id)
        if result.cost_eur is not None and result.cost_eur > 0:
            try:
                self.providers.cost_guard.record_usage(
                    provider_id, "engine_test", result.cost_eur, result.cost_eur, True, "completed"
                )
            except Exception:
                pass
        self.activity.record(
            production_id=None,
            agent="engine_test",
            provider=provider_id,
            model=result.model or chosen or "",
            started_at=_utcnow_iso(),
            duration_ms=result.latency_ms or (now_ms() - started),
            outcome="ok",
            attempts=1,
            fallback_used=False,
            fallback_from=None,
            cost_eur=result.cost_eur,
            billing=result.billing,
            execution=result.execution,
        )
        return {
            "ok": True,
            "provider": provider_id,
            "model": result.model,
            "latency_ms": result.latency_ms or (now_ms() - started),
            "preview": (result.text or "")[:240],
            "multimodal": bool(image_path),
            "vision_verified": vision_ok,
            "expected_content_seen": vision_ok,
            "cost_eur": result.cost_eur,
            "cost_known": result.cost_eur is not None,
            "billing": result.billing,
            "execution": result.execution,
        }

    # ------------------------------------------------------------ multimodal probe
    def vision_probe(self, provider_id: str, model: Optional[str] = None) -> dict[str, Any]:
        """Send an image whose content is known and check the answer mentions it."""
        from kora_studio.ai_engine.vision_probe import build_probe_image

        probe = build_probe_image()
        result = self.test_provider(
            provider_id,
            prompt=(
                "Guarda l'immagine allegata e dimmi: il numero scritto, il colore dello sfondo "
                "e la forma in basso a destra. Rispondi in una frase."
            ),
            model=model,
            image_path=probe["path"],
            expected_content=probe["expected"],
            timeout=60,
        )
        result["probe"] = {"expected": probe["expected"], "path": str(probe["path"])}
        if result.get("ok") and result.get("vision_verified") is False:
            result["detail"] = (
                "Il motore ha risposto ma NON ha riconosciuto il contenuto noto dell'immagine: "
                "capacità visiva non verificata, non verrà usato per i compiti multimodali."
            )
        return result

    def availability_summary(self) -> dict[str, Any]:
        providers = {p: self.probe(p) for p in PROVIDER_ORDER}
        operational = [p for p, info in providers.items() if info["state"] == STATE_OPERATIONAL]
        authenticated = [p for p, info in providers.items() if info["authenticated"]]
        return {
            "any_operational": bool(operational),
            "operational": operational,
            "authenticated": authenticated,
            "ollama_optional_ok": True,
        }
