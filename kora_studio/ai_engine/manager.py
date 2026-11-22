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

import copy
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
    EXEC_REMOTE,
    QUOTA_EXHAUSTED,
    QUOTA_NOT_VERIFIABLE,
    canonical_model_id,
    provider_of,
    quality_score,
)

SETTINGS_KEY = "ai_engine_settings"
PROBE_TTL_SEC = 90.0
MODELS_TTL_SEC = 120.0
#: how long the discovery snapshot (installed/auth/models/caps/billing) stays valid
SNAPSHOT_TTL_SEC = 300.0

#: 2.8.1 — Fast Creative Mode defaults (ideation only, never changes the user's model)
DEFAULT_FAST_BUDGET = 25.0
DEFAULT_FAST_FIRST = 15.0
MAX_FAST_BUDGET = 60.0

FAST_CREATIVE_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "global_budget_sec": 25.0,
    "first_attempt_sec": 15.0,
    "max_output_tokens": 700,
    "prefer_model": "",
    "cache_ttl_sec": 21600,
    "background_warm": False,
    "avoid_cache": False,
}

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
    "fast_creative": dict(FAST_CREATIVE_DEFAULTS),
    #: KORA STUDIO Essential — un solo motore creativo attivo, gli altri sono estensioni
    "essential": {
        "enabled": True,
        "active_provider": "opencode",
        "enabled_providers": ["opencode"],
    },
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
        #: 3.6.1 — Model Manager (impostato da KoraDirector): fonte unica del modello creativo
        self.model_manager: Any = None
        # 2.8.1 — discovery snapshot: avoid a full provider/model scan on every request
        self._snapshot: dict[str, Any] = {}
        self._snapshot_at: float = 0.0
        self._snapshot_lock = threading.Lock()
        self._snapshot_thread: Optional[threading.Thread] = None
        self._snapshot_building = False
        # 3.6.1 — un solo probe per provider alla volta: due richieste ravvicinate
        # non devono autenticarsi due volte (né duplicare i thread di risoluzione)
        self._probe_locks: dict[str, threading.Lock] = {}
        self._probe_locks_guard = threading.Lock()

    # ------------------------------------------------------------------ settings
    def settings(self) -> dict[str, Any]:
        stored = {}
        if self.db is not None:
            try:
                stored = self.db.get_preference(SETTINGS_KEY, {}) or {}
            except Exception:
                stored = {}
        merged = {**DEFAULT_SETTINGS, **stored}
        for key in (
            "providers",
            "agents",
            "declared_subscriptions",
            "declared_free_quota",
            "vision_verified",
            "fast_creative",
            "essential",
        ):
            # deepcopy: i default contengono liste/dict mutabili che non devono
            # essere modificati in-place da update_settings (isolamento fra istanze)
            base = copy.deepcopy(DEFAULT_SETTINGS[key])
            merged[key] = {**base, **copy.deepcopy(stored.get(key) or {})}
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
        if isinstance(patch.get("fast_creative"), dict):
            fast = {**FAST_CREATIVE_DEFAULTS, **(current.get("fast_creative") or {})}
            incoming = patch["fast_creative"]
            for key in ("enabled", "background_warm", "avoid_cache"):
                if key in incoming:
                    fast[key] = bool(incoming[key])
            if "global_budget_sec" in incoming:
                value = float(incoming["global_budget_sec"] or DEFAULT_FAST_BUDGET)
                if value < 8 or value > MAX_FAST_BUDGET:
                    raise ValueError("Budget Fast Creative fuori intervallo (8–60 s)")
                fast["global_budget_sec"] = value
            if "first_attempt_sec" in incoming:
                value = float(incoming["first_attempt_sec"] or DEFAULT_FAST_FIRST)
                if value < 3 or value > MAX_FAST_BUDGET:
                    raise ValueError("Primo tentativo Fast Creative fuori intervallo (3–60 s)")
                fast["first_attempt_sec"] = value
            if "max_output_tokens" in incoming:
                fast["max_output_tokens"] = max(128, min(4000, int(incoming["max_output_tokens"] or 700)))
            if "cache_ttl_sec" in incoming:
                fast["cache_ttl_sec"] = max(60.0, min(604800.0, float(incoming["cache_ttl_sec"] or 21600)))
            if "prefer_model" in incoming:
                # solo una preferenza: il modello configurato dal provider non viene mai riscritto
                fast["prefer_model"] = str(incoming["prefer_model"] or "").strip()[:120]
            current["fast_creative"] = fast
        if isinstance(patch.get("essential"), dict):
            incoming = patch["essential"]
            current_essential = {**self.essential()}
            if "enabled" in incoming:
                current_essential["enabled"] = bool(incoming["enabled"])
            if "active_provider" in incoming:
                value = str(incoming["active_provider"] or "opencode")
                if value not in PROVIDER_ORDER:
                    raise ValueError(f"Provider non valido: {value}")
                current_essential["active_provider"] = value
            if isinstance(incoming.get("enabled_providers"), list):
                ids = [str(p) for p in incoming["enabled_providers"] if str(p) in PROVIDER_ORDER]
                if not ids:
                    raise ValueError("Almeno un motore deve restare attivo")
                current_essential["enabled_providers"] = ids
            if current_essential["active_provider"] not in current_essential["enabled_providers"]:
                current_essential["enabled_providers"].append(current_essential["active_provider"])
            if (
                current_essential["active_provider"] != self.essential()["active_provider"]
                or set(current_essential["enabled_providers"]) != set(self.essential()["enabled_providers"])
            ):
                self._models_cache.clear()
                self._probe_cache.clear()
                self._snapshot = {}
                self._snapshot_at = 0.0
            current["essential"] = current_essential
            # primary_provider resta coerente con il motore creativo attivo
            current["primary_provider"] = current_essential["active_provider"]
        if isinstance(patch.get("agents"), dict):
            for agent_id, conf in patch["agents"].items():
                if agent_id not in AGENTS_BY_ID:
                    raise ValueError(f"Agente non valido: {agent_id}")
                current["agents"][agent_id] = self._validate_agent_conf(agent_id, conf or {})
        # coerenza Essential: il provider principale È l'unico motore creativo attivo.
        # Le altre estensioni restano spente finché non vengono attivate esplicitamente
        # (in questa stessa patch o dalle Impostazioni): niente accumulo silenzioso.
        if (current.get("essential") or {}).get("enabled") and "primary_provider" in patch:
            primary = current.get("primary_provider") or "auto"
            if primary != "auto":
                essential_conf = current["essential"]
                essential_conf["active_provider"] = primary
                explicit = isinstance((patch.get("essential") or {}).get("enabled_providers"), list)
                legacy_multi = any(k in patch for k in ("strategy", "fallback", "auto_fallback"))
                if legacy_multi:
                    # configurazione multi-provider esplicita (compatibilità 2.x): non si tocca
                    if primary not in essential_conf["enabled_providers"]:
                        essential_conf["enabled_providers"].append(primary)
                elif not explicit:
                    essential_conf["enabled_providers"] = [primary]
                elif primary not in essential_conf["enabled_providers"]:
                    essential_conf["enabled_providers"].append(primary)
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

    # ------------------------------------------------------- discovery snapshot
    def snapshot_age_sec(self) -> Optional[float]:
        if not self._snapshot:
            return None
        return max(0.0, time.time() - self._snapshot_at)

    def snapshot(self) -> dict[str, Any]:
        """Last discovery snapshot: installed, authenticated, models, capabilities, billing."""
        return self._snapshot

    def _build_snapshot_entry(self, provider_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        probe = self.probe(provider_id, refresh=True)
        models: list[ModelInfo] = []
        if probe.get("authenticated"):
            adapter = self.adapter(provider_id)
            if adapter is not None:
                try:
                    models = [self._enrich_model(m, provider_id) for m in adapter.list_models()]
                except EngineError:
                    models = []
        # keep the model cache warm too: the fast path never rescans
        if models:
            self._models_cache[provider_id] = (time.time(), models)
        return {
            "provider": provider_id,
            "state": probe.get("state"),
            "installed": bool(probe.get("installed")),
            "authenticated": bool(probe.get("authenticated")),
            "detail": probe.get("detail") or "",
            "auth_detail": probe.get("auth_detail") or "",
            "billing_kinds": probe.get("billing_kinds") or [],
            "last_health_ok": probe.get("last_test_ok"),
            "last_latency_ms": probe.get("last_latency_ms"),
            "models": models,
            "captured_at": time.time(),
            "probe_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    def refresh_snapshot(self, budget_sec: float = 20.0) -> dict[str, Any]:
        """Rebuild the discovery snapshot for every provider (parallel, bounded)."""
        from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout

        with self._snapshot_lock:
            self._snapshot_building = True
        started = time.perf_counter()
        providers: dict[str, Any] = {}
        try:
            with ThreadPoolExecutor(max_workers=min(8, max(1, len(self.active_provider_ids())))) as pool:
                active_ids = self.active_provider_ids()
                futures = {pool.submit(self._build_snapshot_entry, pid): pid for pid in active_ids}
                try:
                    for future in as_completed(futures, timeout=max(0.5, budget_sec)):
                        pid = futures[future]
                        try:
                            providers[pid] = future.result()
                        except Exception as exc:  # defensive: never break the snapshot
                            providers[pid] = {
                                "provider": pid,
                                "state": STATE_UNAVAILABLE,
                                "installed": False,
                                "authenticated": False,
                                "models": [],
                                "detail": f"discovery non riuscita ({exc.__class__.__name__})",
                                "captured_at": time.time(),
                            }
                except FutureTimeout:
                    for future, pid in futures.items():
                        if pid in providers:
                            continue
                        future.cancel()
                        providers[pid] = {
                            "provider": pid,
                            "state": STATE_UNAVAILABLE,
                            "installed": False,
                            "authenticated": False,
                            "models": [],
                            "detail": "discovery oltre il budget: provider non usato in questa richiesta",
                            "captured_at": time.time(),
                        }
        finally:
            self._snapshot_building = False
            self._snapshot_at = time.time()
            self._snapshot = {
                "captured_at": self._snapshot_at,
                "providers": providers,
                "build_ms": round((time.perf_counter() - started) * 1000, 1),
                "budget_sec": budget_sec,
            }
        return self._snapshot

    def ensure_snapshot(self, budget_sec: float = 2.0) -> dict[str, Any]:
        """Return a fresh snapshot; rebuild only if stale, never scanning per request."""
        if self._snapshot and (time.time() - self._snapshot_at) < SNAPSHOT_TTL_SEC:
            return self._snapshot
        if self._snapshot and self._snapshot_building:
            return self._snapshot
        return self.refresh_snapshot(budget_sec=budget_sec)

    def start_snapshot_refresher(self, interval_sec: float = 0.0) -> None:
        """Essential: UNA verifica all'avvio dei soli motori attivi. Nessuna scansione periodica.

        Con ``interval_sec > 0`` (solo diagnostica manuale) è possibile un refresh ripetuto,
        ma il percorso ordinario non lo usa: i provider disattivati non vengono interrogati.
        """
        if self._snapshot_thread and self._snapshot_thread.is_alive():
            return

        def _once() -> None:
            try:
                self.refresh_snapshot(budget_sec=20.0)
            except Exception:
                pass
            if interval_sec <= 0:
                return
            while True:
                time.sleep(max(60.0, interval_sec))
                try:
                    self.refresh_snapshot(budget_sec=20.0)
                except Exception:
                    continue

        thread = threading.Thread(target=_once, name="kora-engine-snapshot", daemon=True)
        self._snapshot_thread = thread
        thread.start()

    def _provider_fast(self, provider_id: str) -> tuple[bool, list[ModelInfo], str]:
        """Installed/authenticated/models from the snapshot, falling back to cached probes."""
        snapshot = self._snapshot
        if snapshot and (time.time() - self._snapshot_at) < SNAPSHOT_TTL_SEC:
            providers = snapshot.get("providers") or {}
            info = providers.get(provider_id)
            if info is None:
                # snapshot fresco: il provider mancante non è stato verificato → non usato
                return False, [], STATE_UNAVAILABLE
            return bool(info.get("authenticated")), list(info.get("models") or []), str(info.get("state"))
        probe = self.probe(provider_id)
        return bool(probe.get("authenticated")), self._models_cached(provider_id), str(probe.get("state"))

    def fast_plan(self, agent_id: str, requires_vision: bool = False) -> list[dict[str, Any]]:
        """Candidate plan built from the snapshot (no discovery, no model scan)."""
        return self.candidate_plan(agent_id, requires_vision=requires_vision, from_snapshot=True)

    def fast_entry(self, agent_id: str, requires_vision: bool = False) -> Optional[dict[str, Any]]:
        plan = self.fast_plan(agent_id, requires_vision=requires_vision)
        if plan:
            return plan[0]
        # 3.6 — uno snapshot inutilizzabile (provider presente ma senza modelli) non
        # deve produrre un fallback deterministico ingiustificato: un probe reale,
        # con cache, dice la verità in pochi millisecondi.
        try:
            plan = self.candidate_plan(agent_id, requires_vision=requires_vision)
        except EngineError:
            return None
        return plan[0] if plan else None

    # ------------------------------------------- 3.6.1 scelta creativa esplicita
    def _entry_for_model(self, provider_id: str, model: str) -> dict[str, Any]:
        """Entry esplicita per un modello scelto dall'utente, anche se l'elenco è vecchio."""
        infos = list(self._models_cached(provider_id))
        if not infos:
            snapshot = self._snapshot or {}
            infos = list(((snapshot.get("providers") or {}).get(provider_id) or {}).get("models") or [])
        info = next(
            (m for m in infos if canonical_model_id(m.id) == canonical_model_id(model)),
            None,
        )
        if info is None:
            free = str(model).lower().endswith("-free")
            info = ModelInfo(
                id=model,
                label=canonical_model_id(model),
                provider=provider_id,
                source="cli",
                text=True,
                vision=None,
                vision_verified=False,
                execution=EXEC_REMOTE,
                billing=BILLING_FREE_QUOTA if free else BILLING_UNKNOWN,
                cost_per_1k_eur=0.0 if free else None,
                quota_status=QUOTA_NOT_VERIFIABLE,
                notes=(
                    "modello gratuito dichiarato dal nome (-free)"
                    if free
                    else "modello scelto a mano: costo non verificabile"
                ),
            )
        return self._entry(provider_id, info, preferred=True, explicit=True)

    def creative_entry(self, agent_id: str, purpose: str = "ideation") -> Optional[dict[str, Any]]:
        """Modello creativo secondo il Model Manager, trasmesso esplicitamente al motore.

        Il piano automatico non può più scegliere un modello diverso da quello
        configurato (era la causa del ritorno silenzioso a DeepSeek).
        """
        mm = getattr(self, "model_manager", None)
        if mm is None:
            return self._any_engine_entry(agent_id)
        choice = mm.effective_choice()
        provider = str(choice.get("provider") or "")
        model = str(choice.get("model") or "")
        if not provider or not model:
            return self._any_engine_entry(agent_id)
        adapter = self.adapter(provider)
        if adapter is None:
            return None
        if getattr(adapter, "is_cli", False):
            probe = self.probe(provider)
            if not probe.get("installed") or not probe.get("authenticated"):
                return None
            entry = self._entry_for_model(provider, model)
            entry.update(
                {
                    "source": "model_manager",
                    "mode": choice.get("mode"),
                    "role": choice.get("role"),
                    "purpose": purpose,
                }
            )
            return entry
        # provider non CLI (adapter di collaudo, API): il modello deve esistere nell'elenco
        snapshot = self._snapshot or {}
        info = ((snapshot.get("providers") or {}).get(provider) or {}) if snapshot else {}
        snapshot_knows_provider = bool(info) and bool(info.get("authenticated"))
        entry = None
        if snapshot:
            entry = self._model_entry_in_plan(agent_id, provider, model, from_snapshot=True)
        if entry is None and not snapshot_knows_provider:
            # 3.6.1 — lo snapshot può essere vecchio (provider collegato dopo l'ultima
            # verifica): una prova reale, una volta sola, evita il falso «nessun motore»
            # e quindi il deterministico ingiustificato. Se invece il provider è già
            # verificato e il modello non è nell'elenco, non si torna a interrogare:
            # si usa il percorso standard.
            entry = self._model_entry_in_plan(agent_id, provider, model, from_snapshot=False)
        if entry is not None:
            entry.update(
                {
                    "source": "model_manager",
                    "mode": choice.get("mode"),
                    "role": choice.get("role"),
                    "purpose": purpose,
                }
            )
            return entry
        return self._any_engine_entry(agent_id)

    def _model_entry_in_plan(
        self,
        agent_id: str,
        provider: str,
        model: str,
        *,
        from_snapshot: bool,
    ) -> Optional[dict[str, Any]]:
        """Candidato esatto (stesso provider e stesso modello) dentro il piano."""
        try:
            plan = self.candidate_plan(
                agent_id,
                provider_override=provider,
                model_override=model,
                from_snapshot=from_snapshot,
            )
        except (EngineError, ValueError):
            return None
        # il piano può contenere altri provider come fallback: qui serve proprio
        # il modello configurato, altrimenti si torna al percorso standard
        return next(
            (
                candidate
                for candidate in plan
                if candidate.get("provider") == provider
                and canonical_model_id(str(candidate.get("model") or "")) == canonical_model_id(model)
            ),
            None,
        )

    def _any_engine_entry(self, agent_id: str) -> Optional[dict[str, Any]]:
        """Ultima rete di sicurezza: snapshot, poi probe reale (mai deterministico per errore)."""
        entry = self.fast_entry(agent_id)
        if entry is not None:
            return entry
        try:
            return self.resolve(agent_id)
        except EngineError:
            return None

    def _record_creative_inference(
        self, provider: str, model: str, ok: bool, latency_ms: Optional[float], error: str = ""
    ) -> None:
        mm = getattr(self, "model_manager", None)
        if mm is None:
            return
        try:
            mm.record_inference(
                provider=provider,
                model=model,
                ok=ok,
                latency_ms=latency_ms,
                error=error,
                purpose="creative",
            )
        except Exception:
            pass

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
    # ------------------------------------------------------- Essential mode
    def essential(self) -> dict[str, Any]:
        conf = (self.settings().get("essential") or {})
        enabled = conf.get("enabled")
        enabled = True if enabled is None else bool(enabled)
        active = str(conf.get("active_provider") or "opencode")
        ids = [str(p) for p in (conf.get("enabled_providers") or [active]) if str(p) in PROVIDER_ORDER]
        if active not in ids:
            ids.insert(0, active)
        return {"enabled": enabled, "active_provider": active, "enabled_providers": ids}

    # --------------------------------------------------- collegamento assistito
    def connect_gemini_api(self, api_key: str, model: Optional[str] = None) -> dict[str, Any]:
        """Collega Google AI Studio validando la chiave PRIMA di salvarla.

        La chiave viene provata su un adapter temporaneo: se non funziona, quella
        precedente resta al suo posto e nulla viene attivato. La quota gratuita
        NON viene dichiarata d'ufficio: senza verifica il costo resta sconosciuto.
        """
        key = (api_key or "").strip()
        if not key:
            raise ValueError("Inserisci la API key di Google AI Studio")

        previous_key = None
        try:
            previous_key = self.providers.get_decrypted_key("gemini_api")
        except Exception:
            previous_key = None

        # 1) prova la chiave senza consolidarla
        self.providers.set_api_key("gemini_api", key)
        self.invalidate("gemini_api")
        adapter = self.adapter("gemini_api")
        if adapter is None:
            if previous_key is not None:
                self.providers.set_api_key("gemini_api", previous_key or "")
            self.invalidate("gemini_api")
            raise ValueError("Adapter Gemini non disponibile")

        try:
            auth = adapter.authenticated()
        except Exception as exc:
            self.providers.set_api_key("gemini_api", previous_key or "")
            self.invalidate("gemini_api")
            raise ValueError(f"Chiave non verificata: {exc}") from exc

        if not auth.get("authenticated"):
            # 2) ripristina la situazione precedente: nessuna chiave invalida salvata
            self.providers.set_api_key("gemini_api", previous_key or "")
            self.invalidate("gemini_api")
            raise ValueError(
                "Chiave non valida o non raggiungibile: "
                f"{auth.get('detail') or 'verifica su aistudio.google.com/apikey'}"
            )

        try:
            models = [m.id for m in adapter.list_models()]
        except Exception:
            models = []

        chosen = (model or "").strip()
        if not chosen:
            lowered = {m.lower(): m for m in models}
            for candidate in ("flash-lite", "flash"):
                for low, original in lowered.items():
                    if candidate in low:
                        chosen = original
                        break
                if chosen:
                    break
            chosen = chosen or (models[0] if models else "")

        # 3.6.1 — collegare una chiave NON cambia il motore creativo: se OpenCode
        # è il motore principale, Gemini resta un'estensione inattiva finché
        # l'utente non la attiva esplicitamente.
        patch: dict[str, Any] = {}
        if chosen:
            patch["providers"] = {"gemini_api": {"model": chosen}}
        # nessun declared_free_quota automatico: la quota gratuita va dichiarata dall'utente
        if patch:
            self.update_settings(patch)
        self.invalidate("gemini_api")
        self._snapshot = {}
        self._snapshot_at = 0.0

        # 3) stato di fatturazione: si dichiara solo ciò che risulta
        declared_free = bool((self.settings().get("declared_free_quota") or {}).get("gemini_api"))
        billing = "free_quota_dichiarata" if declared_free else "non_verificabile"
        active = self.essential()["active_provider"]
        return {
            "ok": True,
            "provider": "gemini_api",
            "model": chosen,
            "models": models[:20],
            "essential": self.essential(),
            "activated": active == "gemini_api",
            "billing": billing,
            "cost_verified": False,
            "detail": (
                "Gemini collegato come estensione: il motore creativo resta "
                f"{active}. Attivalo dalle Impostazioni solo se lo vuoi usare. "
                "Costo non verificabile via API: con budget a 0 € KORA blocca "
                "comunque le chiamate non autorizzate."
                + (" Quota gratuita dichiarata da te." if declared_free else "")
            ),
        }

    def provider_enabled(self, provider_id: str) -> bool:
        """In Essential i provider non attivati non vengono nemmeno interrogati."""
        conf = self.essential()
        if not conf["enabled"]:
            return True
        return provider_id in conf["enabled_providers"]

    def active_provider_ids(self) -> list[str]:
        conf = self.essential()
        if not conf["enabled"]:
            return list(PROVIDER_ORDER)
        active = conf["active_provider"]
        return [active] + [p for p in PROVIDER_ORDER if p in conf["enabled_providers"] and p != active]

    def _disabled_probe(self, provider_id: str) -> dict[str, Any]:
        adapter = self.adapter(provider_id)
        return {
            "provider": provider_id,
            "label": getattr(adapter, "label", provider_id),
            "kind": "cli" if getattr(adapter, "is_cli", False) else "http",
            "local": bool(getattr(adapter, "is_local", False)),
            "subscription": bool(getattr(adapter, "subscription_quota", False)),
            "requires_key": bool(getattr(adapter, "requires_key", False)),
            "state": STATE_UNAVAILABLE,
            "installed": False,
            "authenticated": False,
            "detail": "disattivato: estensione opzionale (nessun controllo eseguito)",
            "auth_detail": "",
            "account": None,
            "version": None,
            "binary": None,
            "models_count": 0,
            "supports_vision": False,
            "unknown_vision_models": 0,
            "unknown_cost_models": 0,
            "billing_kinds": [],
            "attachments": False,
            "attachment_mode": "",
            "verified_operational": False,
            "last_test_at": None,
            "last_test_ok": None,
            "last_latency_ms": None,
            "docs_url": getattr(adapter, "docs_url", ""),
            "extension": False,
            "enabled": True,
            "active": provider_id == self.essential()["active_provider"],
            "cost_summary": {"zero_cost_models": 0, "paid_models": 0, "unknown": 0},
            "extension": True,
            "enabled": False,
            "active": False,
        }

    def _probe_lock_for(self, provider_id: str) -> threading.Lock:
        with self._probe_locks_guard:
            lock = self._probe_locks.get(provider_id)
            if lock is None:
                lock = threading.Lock()
                self._probe_locks[provider_id] = lock
            return lock

    def probe(self, provider_id: str, refresh: bool = False) -> dict[str, Any]:
        """Stato del provider, con cache e **un solo** probe in volo per provider.

        Due richieste che arrivano insieme (es. thread di risoluzione duplicato)
        non devono autenticarsi due volte: la seconda riusa il risultato appena
        scritto in cache.
        """
        if refresh:
            return self._probe_uncached(provider_id, refresh=True)
        with self._probe_lock_for(provider_id):
            cached = self._probe_cache.get(provider_id)
            if cached and (time.time() - cached[0]) < PROBE_TTL_SEC:
                return cached[1]
            return self._probe_uncached(provider_id, refresh=False)

    def _probe_uncached(self, provider_id: str, refresh: bool = False) -> dict[str, Any]:
        # refresh=True = verifica esplicita dell'utente su una singola estensione
        if not self.provider_enabled(provider_id) and not refresh:
            result = self._disabled_probe(provider_id)
            self._probe_cache[provider_id] = (time.time(), result)
            return result
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
            "extension": False,
            "enabled": True,
            "active": provider_id == self.essential()["active_provider"],
            "cost_summary": {
                "zero_cost_models": sum(1 for m in models if m.zero_cost),
                "paid_models": cost_note.count(BILLING_PAID_API),
                "unknown": sum(1 for m in models if m.cost_per_1k_eur is None),
            },
        }
        self._probe_cache[provider_id] = (time.time(), result)
        return result

    def extensions_catalog(self) -> list[dict[str, Any]]:
        """Estensioni provider: attivabili, mai caricate se disattivate."""
        conf = self.essential()
        catalog = []
        for pid in PROVIDER_ORDER:
            adapter = self.adapter(pid)
            catalog.append(
                {
                    "provider": pid,
                    "label": getattr(adapter, "label", pid),
                    "kind": "cli" if getattr(adapter, "is_cli", False) else "http",
                    "local": bool(getattr(adapter, "is_local", False)),
                    "enabled": pid in conf["enabled_providers"],
                    "active": pid == conf["active_provider"],
                    "docs_url": getattr(adapter, "docs_url", ""),
                }
            )
        return catalog

    def status_board(self, refresh: bool = False) -> dict[str, Any]:
        # Essential: i provider disattivati non vengono né installati-check né interrogati
        active = set(self.active_provider_ids()) if self.essential()["enabled"] else set(PROVIDER_ORDER)
        providers = [
            self.probe(pid, refresh=refresh) if pid in active else self._disabled_probe(pid)
            for pid in PROVIDER_ORDER
        ]
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
            "extensions": self.extensions_catalog(),
            "essential_mode": self.essential()["enabled"],
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
            "fast_creative": settings.get("fast_creative") or dict(FAST_CREATIVE_DEFAULTS),
            "essential": self.essential(),
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

    # -------------------------------------------------- vision-change listeners
    def register_vision_listener(self, callback: Any) -> None:
        """Register a callback fired when a model becomes vision-VERIFIED."""
        if not hasattr(self, "_vision_listeners"):
            self._vision_listeners = []
        if callback not in self._vision_listeners:
            self._vision_listeners.append(callback)

    def _fire_vision_listeners(self, provider_id: str, model: str) -> list[Any]:
        results = []
        for callback in list(getattr(self, "_vision_listeners", [])):
            try:
                results.append(callback(provider_id=provider_id, model=model))
            except TypeError:
                try:
                    results.append(callback())
                except Exception:
                    continue
            except Exception:
                continue
        return results

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

    def _selected_role_model(self, provider_id: str) -> Optional[str]:
        """Modello preferito per il provider secondo la modalità Everyday/Premium.

        3.6.1 — la fonte è il Model Manager (con i suoi default), non più la
        chiave grezza: una configurazione vuota non deve far vincere il legacy.
        """
        if provider_id != "opencode":
            return None
        mm = getattr(self, "model_manager", None)
        if mm is not None:
            try:
                choice = mm.effective_choice()
                if choice.get("provider") == provider_id and choice.get("model"):
                    return str(choice["model"])
            except Exception:
                pass
        try:
            stored = self.db.get_preference("ai_model_config") or {}
        except Exception:
            return None
        roles = stored.get("roles") or {}
        if not roles:
            return None
        mode = str(stored.get("mode") or "everyday")
        return roles.get("premium" if mode == "premium" else "everyday") or None

    def candidate_plan(
        self,
        agent_id: str,
        requires_vision: bool = False,
        *,
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
        from_snapshot: bool = False,
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
        # Essential: solo i motori attivati possono essere interrogati
        enabled_ids = self.active_provider_ids() if self.essential()["enabled"] else list(PROVIDER_ORDER)
        if explicit_provider:
            order = [explicit_provider] + [p for p in enabled_ids if p != explicit_provider]
        elif primary != "auto" and primary in enabled_ids:
            order = [primary] + [p for p in enabled_ids if p != primary]
        else:
            order = list(enabled_ids)

        fallback = settings.get("fallback", FALLBACK_FREE_ONLY)
        strict = bool(explicit_provider)

        plan: list[dict[str, Any]] = []
        for position, provider_id in enumerate(order):
            if from_snapshot:
                authenticated, infos, _state = self._provider_fast(provider_id)
                if not authenticated:
                    continue
            else:
                probe = self.probe(provider_id)
                if not probe.get("authenticated"):
                    continue
                infos = self._models_cached(provider_id)
            adapter = self.adapter(provider_id)
            if adapter is None:
                continue
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
                if not model_pref:
                    # 3.6 — modello scelto nel Model Manager (solo se l'utente ha salvato una scelta)
                    selected = self._selected_role_model(provider_id)
                    if selected and any(canonical_model_id(m.id) == canonical_model_id(selected) for m in infos):
                        model_pref = selected
                if not model_pref and not explicit_provider:
                    # Fast Creative Mode may prefer a fast model without changing the provider
                    fast_pref = str((settings.get("fast_creative") or {}).get("prefer_model") or "").strip()
                    if fast_pref:
                        candidate = canonical_model_id(fast_pref)
                        if any(canonical_model_id(m.id) == candidate for m in infos):
                            model_pref = fast_pref
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
            # 3.6.1 — modello scelto dall'utente assente da un elenco CLI vecchio:
            # lo si usa comunque, esplicitamente (il CLI è la fonte reale).
            if (
                not plan
                and explicit_provider
                and getattr(self.adapter(explicit_provider), "is_cli", False)
            ):
                adapter = self.adapter(explicit_provider)
                probe = self.probe(explicit_provider)
                if adapter is not None and probe.get("installed") and probe.get("authenticated"):
                    synthetic = self._entry_for_model(explicit_provider, explicit_model)
                    synthetic["explicit"] = True
                    plan = [synthetic]

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
        from_snapshot: bool = False,
    ) -> Optional[dict[str, Any]]:
        plan = self.candidate_plan(
            agent_id,
            requires_vision=requires_vision,
            provider_override=provider_override,
            model_override=model_override,
            from_snapshot=from_snapshot,
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
        # 2.8.1 — bounded fast path
        deadline: Optional[float] = None,
        attempt_timeout: Optional[float] = None,
        max_candidates: Optional[int] = None,
        brand_context: bool = True,
        request_label: str = "",
    ) -> EngineResult:
        if agent_id not in AGENTS_BY_ID:
            raise EngineError(f"Agente sconosciuto: {agent_id}")
        images = [Path(p) for p in (image_paths or [])] + self._image_parts(messages)
        images = [p for p in images if p.is_file()]
        needs_vision = requires_vision or agent_id in VISION_AGENTS or bool(images)

        wall_start = time.perf_counter()
        timings: dict[str, Any] = {"discovery": 0.0, "connection": 0.0, "inference": 0.0}
        discovery_started = time.perf_counter()
        # usa lo snapshot solo se è fresco: altrimenti comportamento classico (probe+cache)
        fresh_snapshot = bool(self._snapshot) and (time.time() - self._snapshot_at) < SNAPSHOT_TTL_SEC
        plan = self.candidate_plan(
            agent_id,
            requires_vision=needs_vision,
            provider_override=provider_override,
            model_override=model_override,
            from_snapshot=fresh_snapshot,
        )
        timings["discovery"] = round((time.perf_counter() - discovery_started) * 1000, 1)
        if deadline is not None:
            deadline = deadline - 0.35  # reserve time for parsing/validation
        max_candidates = max_candidates if max_candidates and max_candidates > 0 else len(plan)
        if needs_vision and not plan:
            detail = (
                f"{agent_id}: nessun modello con visione VERIFICATA disponibile "
                "(i modelli con capacità non verificata non vengono usati per le immagini)"
            )
            # a blocked request is still a request: the monitor must show it
            self._log(
                production_id,
                agent_id,
                {"provider": "", "model": ":vision", "billing": "unknown", "execution": "unknown"},
                _utcnow_iso(),
                now_ms(),
                outcome="error",
                attempts=0,
                fallback_used=False,
                fallback_from=None,
                cost=None,
                error_kind="visione_non_verificata",
                error_message=detail,
                attachments=len(images),
            )
            raise EngineVisionUnsupported(detail)

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
                connect_started = time.perf_counter()
                adapter = self.adapter(entry["provider"])
                timings["connection"] = round(timings["connection"] + (time.perf_counter() - connect_started) * 1000, 1)
                if adapter is None:
                    continue
                if index >= max_candidates:
                    errors.append("budget: numero massimo di motori tentati raggiunto")
                    break
                if deadline is not None and time.time() >= deadline:
                    errors.append("budget: tempo globale esaurito prima di contattare il motore")
                    break
                if images and not adapter.supports_attachments:
                    errors.append(
                        f"{entry['provider']}: il motore non trasferisce allegati immagine "
                        "(nessun invio silenzioso)"
                    )
                    continue
                if index > 0 and fallback_from is None:
                    fallback_from = plan[0]["provider"] if plan else None
                payload = self._with_brand_context(agent_id, messages) if brand_context else list(messages)
                staged = stage_attachments(images, adapter.workspace() if adapter.is_cli else None) if images else []
                attempt = 0
                while attempt < max(1, max_attempts):
                    attempt += 1
                    attempts_total += 1
                    if is_cancelled():
                        raise EngineCancelled(f"{agent_id}: richiesta annullata dall'utente")
                    call_timeout = timeout
                    if attempt_timeout:
                        call_timeout = min(call_timeout, attempt_timeout)
                    if deadline is not None:
                        remaining = deadline - time.time()
                        if remaining <= 0.5:
                            errors.append(
                                f"{entry['provider']}/{entry['model']}: budget globale esaurito prima dell'inferenza"
                            )
                            break
                        call_timeout = min(call_timeout, remaining)
                    attempt_started = time.perf_counter()
                    try:
                        result = adapter.generate(
                            payload,
                            model=entry["model"],
                            timeout=max(1.0, call_timeout),
                            json_mode=json_mode,
                            attachments=staged or None,
                            cancel=is_cancelled,
                        )
                    except EngineCancelled:
                        timings["inference"] = round(
                            timings["inference"] + (time.perf_counter() - attempt_started) * 1000, 1
                        )
                        self._log(
                            production_id, agent_id, entry, started_wall, started,
                            outcome="cancelled", attempts=attempts_total,
                            fallback_used=fallback_from is not None, fallback_from=fallback_from,
                            cost=None, error_kind="annullata", error_message="annullata", cancelled=True,
                            timings={**timings, "total": round((time.perf_counter() - wall_start) * 1000, 1)},
                            request_label=request_label,
                        )
                        raise
                    except (EngineRateLimited, EngineTimeout) as exc:
                        timings["inference"] = round(
                            timings["inference"] + (time.perf_counter() - attempt_started) * 1000, 1
                        )
                        errors.append(f"{entry['provider']}/{entry['model']}: {exc}")
                        if attempt < max_attempts:
                            time.sleep(min(2.0 * attempt, 4.0))
                            continue
                        break
                    except (EngineAuthError, EngineModelUnavailable, EngineQuotaExhausted) as exc:
                        timings["inference"] = round(
                            timings["inference"] + (time.perf_counter() - attempt_started) * 1000, 1
                        )
                        errors.append(f"{entry['provider']}/{entry['model']}: {exc}")
                        self._mark_operational(entry["provider"], False)
                        break
                    except EngineError as exc:
                        timings["inference"] = round(
                            timings["inference"] + (time.perf_counter() - attempt_started) * 1000, 1
                        )
                        errors.append(f"{entry['provider']}/{entry['model']}: {exc}")
                        break

                    # success
                    result.attempts = attempts_total
                    result.fallback_used = fallback_from is not None and fallback_from != entry["provider"]
                    result.provider = entry["provider"]
                    result.model = entry["model"]
                    result.billing = entry["billing"]
                    result.execution = entry["execution"]
                    # Unit rate, estimated cost and actual cost are three distinct facts:
                    # an estimate is never written as an actual cost.
                    result.unit_price_per_1k_eur = entry.get("cost_per_1k_eur")
                    result.auth_type = entry.get("auth_type") or ("oauth_subscription" if entry["billing"] == "subscription" else "")
                    result.attachments_transmitted = len(staged or [])
                    result.request_status = "completed"
                    if result.cost_eur is None:
                        if entry["zero_cost"]:
                            result.cost_eur = 0.0
                            result.cost_basis = {
                                "subscription": "subscription",
                                "local_compute": "local_compute",
                                "free_quota": "free_quota",
                            }.get(entry["billing"], "subscription")
                        elif result.cost_basis in ("", "unknown"):
                            result.cost_basis = "unknown"
                    elif result.cost_basis in ("", "unknown"):
                        result.cost_basis = "actual"
                    if result.estimated_cost_eur is None:
                        if entry["zero_cost"]:
                            result.estimated_cost_eur = 0.0
                        elif entry["cost_known"]:
                            result.estimated_cost_eur = estimated
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
                        "unit_price_per_1k_eur": entry.get("cost_per_1k_eur"),
                        "actual_cost_eur": result.cost_eur,
                        "cost_basis": result.cost_basis,
                        "production_id": production_id,
                        "actual_provider": entry.get("actual_provider"),
                    }
                    self._mark_operational(entry["provider"], True)
                    timings["inference"] = round(
                        timings["inference"] + (time.perf_counter() - attempt_started) * 1000, 1
                    )
                    timings["total"] = round((time.perf_counter() - wall_start) * 1000, 1)
                    timings["cli_startup"] = (result.raw or {}).get("cli_startup_ms")
                    timings["model_latency"] = result.latency_ms
                    result.timings = {k: v for k, v in timings.items() if v is not None}
                    call_id = self._log(
                        production_id, agent_id, entry, started_wall, started,
                        outcome="ok", attempts=attempts_total,
                        fallback_used=bool(result.fallback_used), fallback_from=fallback_from,
                        cost=result.cost_eur,
                        estimated=result.estimated_cost_eur,
                        unit_price=result.unit_price_per_1k_eur,
                        cost_basis=result.cost_basis,
                        attachments=result.attachments_transmitted,
                        auth_type=result.auth_type,
                        timings=result.timings,
                        request_label=request_label,
                    )
                    result.call_id = call_id
                    return result

            self._log(
                production_id, agent_id,
                {"provider": (plan[0]["provider"] if plan else ""), "model": (plan[0]["model"] if plan else "")},
                started_wall, started,
                outcome="error", attempts=attempts_total, fallback_used=False, fallback_from=None,
                cost=None, error_kind="nessun_motore", error_message="; ".join(errors)[:300],
                timings={**timings, "fallback": 0.0, "total": round((time.perf_counter() - wall_start) * 1000, 1)},
                request_label=request_label,
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
        estimated: Optional[float] = None,
        unit_price: Optional[float] = None,
        cost_basis: str = "unknown",
        attachments: int = 0,
        auth_type: str = "",
        timings: Optional[dict[str, Any]] = None,
        request_label: str = "",
    ) -> str:
        # 3.6.1 — l'esito reale dell'inferenza creativa alimenta la diagnostica della Home
        provider_name = str(entry.get("provider") or "")
        if provider_name and provider_name != "deterministic":
            self._record_creative_inference(
                provider_name,
                str(entry.get("model") or ""),
                outcome == "ok",
                (timings or {}).get("model_latency"),
                "" if outcome == "ok" else (error_message or error_kind),
            )
        return self.activity.record(
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
            estimated_cost_eur=estimated,
            unit_price_per_1k_eur=unit_price,
            cost_basis=cost_basis,
            attachments=attachments,
            auth_type=auth_type or entry.get("auth_type") or "",
            request_status="cancelled" if cancelled else ("completed" if outcome == "ok" else "error"),
            vision_image=bool(attachments),
            timings=timings,
            request_label=request_label,
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
            if vision_ok:
                # a new verified Vision model exists: fallback analyses can be upgraded
                self._fire_vision_listeners(provider_id, result.model or chosen or "")
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
            estimated_cost_eur=result.estimated_cost_eur,
            unit_price_per_1k_eur=result.unit_price_per_1k_eur,
            cost_basis=result.cost_basis,
            attachments=result.attachments_transmitted,
            auth_type=result.auth_type,
            vision_image=bool(image_path),
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
            "estimated_cost_eur": result.estimated_cost_eur,
            "unit_price_per_1k_eur": result.unit_price_per_1k_eur,
            "cost_basis": result.cost_basis,
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
