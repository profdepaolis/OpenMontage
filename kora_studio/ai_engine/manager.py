"""AI Engine Manager — one place to configure, verify and route every AI engine.

Design notes
------------
* Settings live in SQLite (``user_preferences.ai_engine_settings``), so changing the
  engine takes effect on the next call: **no application restart**.
* Credentials stay in the existing encrypted SecretVault through ProviderManager;
  this module only ever handles booleans and masked values.
* A provider is reported as *operativo* only after a real generation succeeded; a
  successful auth probe yields *autenticato*, never *operativo*.
"""

from __future__ import annotations

import time
from base64 import b64encode
from pathlib import Path
from typing import Any, Optional

from kora_studio.ai_engine.adapters_cli import CodexAdapter, OpenCodeAdapter
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
    EngineError,
    EngineResult,
    ModelInfo,
)

SETTINGS_KEY = "ai_engine_settings"
PROBE_TTL_SEC = 90.0

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "strategy": STRATEGY_AUTO,
    "primary_provider": "auto",
    "max_budget_eur": 0.0,
    "allow_paid_calls": False,
    "allow_external_media": False,
    "providers": {},
    "agents": {},
}

PROVIDER_ORDER = (
    "opencode",
    "codex",
    "ollama_local",
    "gemini_api",
    "openai_api",
    "openrouter",
    "claude_api",
    "deepseek_api",
    "custom_openai",
)


class AIEngineManager:
    """Facade used by the API, the UI and the KORA agents."""

    def __init__(self, providers: Any, db: Any = None, context_builder: Any = None) -> None:
        self.providers = providers
        self.db = db if db is not None else getattr(providers, "db", None)
        #: profilo KORA versionato iniettato in OGNI chiamata (stessa memoria per tutti i motori)
        self.context_builder = context_builder
        self._probe_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._adapters: dict[str, AIProviderAdapter] = {}

    # ------------------------------------------------------------------ settings
    def settings(self) -> dict[str, Any]:
        stored = {}
        if self.db is not None:
            try:
                stored = self.db.get_preference(SETTINGS_KEY, {}) or {}
            except Exception:
                stored = {}
        merged = {**DEFAULT_SETTINGS, **stored}
        merged["providers"] = {**DEFAULT_SETTINGS["providers"], **(stored.get("providers") or {})}
        merged["agents"] = {**DEFAULT_SETTINGS["agents"], **(stored.get("agents") or {})}
        if merged.get("strategy") not in STRATEGIES:
            merged["strategy"] = STRATEGY_AUTO
        return merged

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.settings()
        if "strategy" in patch:
            if patch["strategy"] not in STRATEGIES:
                raise ValueError(f"Strategia non valida: {patch['strategy']}")
            current["strategy"] = patch["strategy"]
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
        for flag in ("allow_paid_calls", "allow_external_media"):
            if flag in patch:
                current[flag] = bool(patch[flag])
        if isinstance(patch.get("providers"), dict):
            for provider_id, conf in patch["providers"].items():
                if provider_id not in PROVIDER_ORDER:
                    raise ValueError(f"Provider non valido: {provider_id}")
                current["providers"][provider_id] = {
                    **(current["providers"].get(provider_id) or {}),
                    **{k: v for k, v in (conf or {}).items() if k in ("enabled", "model", "base_url")},
                }
                # config cambiata: l'istanza in cache può essere obsoleta (es. nuovo base_url)
                self.invalidate(provider_id)
        if isinstance(patch.get("agents"), dict):
            for agent_id, conf in patch["agents"].items():
                if agent_id not in AGENTS_BY_ID:
                    raise ValueError(f"Agente non valido: {agent_id}")
                current["agents"][agent_id] = self._validate_agent_conf(agent_id, conf or {})
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

    def invalidate(self, provider_id: str) -> None:
        """Forget cached adapter/probe for one provider (config, key or base_url changed)."""
        self._adapters.pop(provider_id, None)
        self._probe_cache.pop(provider_id, None)

    # ------------------------------------------------------------------ probes
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
        auth = adapter.authenticated() if detection.get("installed") else {
            "authenticated": False,
            "detail": "non installato",
            "account": None,
        }
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
        verified = (self.settings().get("providers", {}).get(provider_id) or {}).get("verified_operational")
        if state == STATE_AUTHENTICATED and verified:
            state = STATE_OPERATIONAL
        models: list[ModelInfo] = []
        if state != STATE_UNAVAILABLE:
            try:
                models = adapter.list_models()
            except EngineError:
                models = []
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
                auth.get("detail")
                if (adapter.requires_key and not adapter.credentials_present() and adapter.kind != "custom_openai")
                else (detection.get("detail") or auth.get("detail") or "")
            ),
            "auth_detail": auth.get("detail") or "",
            "account": auth.get("account"),
            "models_count": len(models),
            "supports_vision": any(m.vision for m in models),
            "verified_operational": state == STATE_OPERATIONAL,
            "docs_url": getattr(adapter, "docs_url", ""),
        }
        self._probe_cache[provider_id] = (time.time(), result)
        return result

    def status_board(self, refresh: bool = False) -> dict[str, Any]:
        providers = [self.probe(pid, refresh=refresh) for pid in PROVIDER_ORDER]
        return {
            "settings": self._public_settings(),
            "providers": providers,
            "agents": [self.agent_configuration(agent.id) for agent in AGENTS],
            "strategies": list(STRATEGIES),
            "ollama_installed": next(
                (bool(p.get("installed")) for p in providers if p["provider"] == "ollama_local"),
                False,
            ),
            # Ollama è opzionale: la piattaforma funziona anche senza.
            "ollama_required": False,
            "operational": [p["provider"] for p in providers if p["state"] == STATE_OPERATIONAL],
            "budget": {
                "max_budget_eur": self.settings()["max_budget_eur"],
                "allow_paid_calls": self.settings()["allow_paid_calls"],
                "spent_note": "Consumo reale solo dagli utilizzi registrati; saldo non verificabile.",
            },
        }

    def _public_settings(self) -> dict[str, Any]:
        settings = self.settings()
        return {
            "strategy": settings["strategy"],
            "primary_provider": settings["primary_provider"],
            "max_budget_eur": settings["max_budget_eur"],
            "allow_paid_calls": settings["allow_paid_calls"],
            "allow_external_media": settings["allow_external_media"],
            "providers": settings.get("providers") or {},
            "agents": settings.get("agents") or {},
        }

    def models(self, provider_id: str, refresh: bool = True) -> list[dict[str, Any]]:
        adapter = self.adapter(provider_id)
        if adapter is None:
            raise ValueError(f"Provider non valido: {provider_id}")
        models = adapter.list_models()
        return [m.to_dict() for m in models]

    # ------------------------------------------------------------------ tests
    def test_provider(
        self,
        provider_id: str,
        prompt: str = "Rispondi solo con: KORA OK",
        model: Optional[str] = None,
        image_path: Optional[Path] = None,
    ) -> dict[str, Any]:
        adapter = self.adapter(provider_id)
        if adapter is None:
            return {"ok": False, "provider": provider_id, "detail": "provider sconosciuto"}
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "Sei un test di connessione. Rispondi breve."},
            {"role": "user", "content": prompt},
        ]
        if image_path and Path(image_path).is_file():
            raw = Path(image_path).read_bytes()
            suffix = Path(image_path).suffix.lower().lstrip(".") or "jpeg"
            messages[1]["content"] = [
                {"type": "text", "text": prompt or "Descrivi questa immagine in una frase."},
                {"type": "image_url", "image_url": {"url": f"data:image/{suffix};base64,{b64encode(raw).decode()}"}},
            ]
        chosen = model or self._configured_model(provider_id)
        try:
            result = adapter.generate(messages, model=chosen, timeout=45, json_mode=False)
        except EngineError as exc:
            self._mark_operational(provider_id, False)
            return {"ok": False, "provider": provider_id, "detail": str(exc)}
        except Exception as exc:  # pragma: no cover - defensive
            self._mark_operational(provider_id, False)
            return {"ok": False, "provider": provider_id, "detail": f"errore imprevisto ({exc.__class__.__name__})"}
        self._mark_operational(provider_id, True)
        if result.cost_eur > 0:
            try:
                self.providers.cost_guard.record_usage(
                    provider_id, "engine_test", result.cost_eur, result.cost_eur, True, "completed"
                )
            except Exception:
                pass
        return {
            "ok": True,
            "provider": provider_id,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "preview": result.text[:240],
            "multimodal": bool(image_path),
            "cost_eur": result.cost_eur,
        }

    def _mark_operational(self, provider_id: str, ok: bool) -> None:
        settings = self.settings()
        conf = dict(settings["providers"].get(provider_id) or {})
        conf["verified_operational"] = ok
        settings["providers"][provider_id] = conf
        if self.db is not None:
            self.db.set_preference(SETTINGS_KEY, settings)
        self._probe_cache.pop(provider_id, None)

    # ------------------------------------------------------------------ routing
    def _configured_model(self, provider_id: str) -> Optional[str]:
        return ((self.settings().get("providers") or {}).get(provider_id) or {}).get("model") or None

    def agent_configuration(self, agent_id: str) -> dict[str, Any]:
        spec = AGENTS_BY_ID[agent_id]
        conf = (self.settings().get("agents") or {}).get(agent_id) or {"mode": "inherit"}
        return {
            "id": spec.id,
            "label": spec.label,
            "needs_vision": spec.needs_vision,
            "description": spec.description,
            "mode": conf.get("mode", "inherit"),
            "provider": conf.get("provider"),
            "model": conf.get("model"),
        }

    def _paid_authorized(self, estimated_eur: float) -> bool:
        settings = self.settings()
        if not settings.get("allow_paid_calls"):
            return False
        if float(settings.get("max_budget_eur") or 0.0) <= 0:
            return False
        if estimated_eur > float(settings.get("max_budget_eur") or 0.0):
            return False
        try:
            verdict = self.providers.cost_guard.check_authorization(
                max(estimated_eur, 0.001), explicit_user_authorization=True
            )
        except Exception:
            return False
        return bool(verdict.get("authorized"))

    def candidate_plan(self, agent_id: str, requires_vision: bool = False) -> list[dict[str, Any]]:
        """Ordered list of (provider, model) candidates for the given agent."""
        settings = self.settings()
        strategy = settings["strategy"]
        needs_vision = requires_vision or agent_id in VISION_AGENTS
        plan: list[dict[str, Any]] = []

        agent_conf = settings["agents"].get(agent_id) or {}
        explicit = (
            [agent_conf.get("provider")]
            if agent_conf.get("mode") == "provider" and agent_conf.get("provider")
            else []
        )
        if not explicit and settings["primary_provider"] != "auto":
            explicit = [settings["primary_provider"]]
        order = explicit + [p for p in PROVIDER_ORDER if p not in explicit]

        for provider_id in order:
            if strategy == STRATEGY_LOCAL and provider_id in (
                "gemini_api",
                "openai_api",
                "openrouter",
                "claude_api",
                "deepseek_api",
            ):
                continue
            probe = self.probe(provider_id)
            if not probe.get("authenticated"):
                continue
            adapter = self.adapter(provider_id)
            if adapter is None:
                continue
            try:
                models = adapter.list_models()
            except EngineError:
                models = []
            if not models:
                continue
            preferred = agent_conf.get("model") if agent_conf.get("provider") == provider_id else None
            preferred = preferred or self._configured_model(provider_id)
            for info in models:
                if needs_vision and not info.vision:
                    continue
                if not info.text:
                    continue
                plan.append(
                    {
                        "provider": provider_id,
                        "model": info.id,
                        "preferred": bool(preferred and info.id == preferred),
                        "free": bool(
                            info.free
                            or adapter.is_local
                            or (
                                info.cost_per_1k_eur == 0
                                and not getattr(adapter, "requires_paid_authorization", True)
                            )
                        ),
                        "subscription": info.subscription,
                        "vision": info.vision,
                        "cost_per_1k_eur": info.cost_per_1k_eur,
                    }
                )

        def _score(entry: dict[str, Any]) -> tuple:
            if strategy == STRATEGY_QUALITY:
                return (
                    0 if entry["subscription"] else 1,
                    0 if entry["vision"] else 1,
                    -float(entry["cost_per_1k_eur"] or 0),
                )
            if strategy == STRATEGY_COST:
                return (
                    0 if (entry["free"] or entry["subscription"]) else 1,
                    float(entry["cost_per_1k_eur"] or 0),
                )
            if strategy == STRATEGY_LOCAL:
                return (0 if entry["free"] else 1, float(entry["cost_per_1k_eur"] or 0))
            # auto: subscription/free first, then cheap, preferred model wins ties
            return (
                0 if (entry["free"] or entry["subscription"]) else 1,
                0 if entry["preferred"] else 1,
                float(entry["cost_per_1k_eur"] or 0),
            )

        plan.sort(key=_score)
        return plan

    def resolve(self, agent_id: str, requires_vision: bool = False) -> Optional[dict[str, Any]]:
        plan = self.candidate_plan(agent_id, requires_vision=requires_vision)
        for entry in plan:
            if entry["free"] or entry["subscription"]:
                return entry
            if self._paid_authorized(float(entry["cost_per_1k_eur"] or 0) * 4):
                return entry
        return None

    def _with_brand_context(
        self, agent_id: str, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Antepone il blocco KORA canonico: stessa memoria per qualsiasi motore."""
        if self.context_builder is None:
            return list(messages)
        try:
            block = self.context_builder.to_prompt_block(self.context_builder.build(agent_id))
        except Exception:
            return list(messages)
        if not block:
            return list(messages)
        preamble = {"role": "system", "content": block}
        if messages and messages[0].get("role") == "system":
            return [preamble] + list(messages)  # contesto canonico prima del prompt di ruolo
        return [preamble] + list(messages)

    # ------------------------------------------------------------------ execution
    def call(
        self,
        agent_id: str,
        messages: list[dict[str, Any]],
        *,
        requires_vision: bool = False,
        json_mode: bool = True,
        timeout: float = 120.0,
        image_path: Optional[Path] = None,
    ) -> EngineResult:
        if agent_id not in AGENTS_BY_ID:
            raise EngineError(f"Agente sconosciuto: {agent_id}")
        plan = self.candidate_plan(
            agent_id,
            requires_vision=requires_vision
            or image_path is not None
            or agent_id in VISION_AGENTS,
        )
        errors: list[str] = []
        for entry in plan:
            provider_id = entry["provider"]
            paid = not (entry["free"] or entry["subscription"])
            if paid and not self._paid_authorized(float(entry["cost_per_1k_eur"] or 0) * 4):
                errors.append(f"{provider_id}: chiamata a pagamento non autorizzata")
                continue
            adapter = self.adapter(provider_id)
            if adapter is None:
                continue
            payload = self._with_brand_context(agent_id, messages)
            if image_path and Path(image_path).is_file():
                raw = Path(image_path).read_bytes()
                suffix = Path(image_path).suffix.lower().lstrip(".") or "jpeg"
                payload = payload + [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Analizza l'immagine allegata."},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/{suffix};base64,{b64encode(raw).decode()}"
                                },
                            },
                        ],
                    }
                ]
            try:
                result = adapter.generate(
                    payload, model=entry["model"], timeout=timeout, json_mode=json_mode
                )
            except EngineError as exc:
                errors.append(f"{provider_id}: {exc}")
                continue
            if result.cost_eur > 0:
                try:
                    self.providers.cost_guard.record_usage(
                        provider_id, agent_id, result.cost_eur, result.cost_eur, True, "completed"
                    )
                except Exception:
                    pass
            self._mark_operational(provider_id, True)
            result.raw = {"agent": agent_id, "attempts": errors}
            return result
        raise EngineError(
            "; ".join(errors) if errors else "Nessun motore AI disponibile con i criteri richiesti"
        )

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
