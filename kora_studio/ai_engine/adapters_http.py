"""HTTP adapters: Ollama (optional), OpenAI-compatible (OpenAI, OpenRouter,
DeepSeek, custom endpoint), Google Gemini, Anthropic.

Every model is described with the metadata KORA can actually prove: a price is
reported only when the vendor publishes it (otherwise ``None`` = unknown), a
vision capability only when the curated registry knows the model (otherwise
``None`` = unverified), and the execution location is explicit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from kora_studio.ai_engine.base import (
    AIProviderAdapter,
    EngineAuthError,
    EngineError,
    EngineModelUnavailable,
    EngineQuotaExhausted,
    EngineResult,
    ModelInfo,
    classify_error,
    now_ms,
)
from kora_studio.ai_engine.models import (
    AUTH_API_KEY,
    AUTH_LOCAL_NO_AUTH,
    BILLING_FREE_QUOTA,
    BILLING_LOCAL,
    BILLING_PAID_API,
    BILLING_UNKNOWN,
    EXEC_LOCAL,
    EXEC_REMOTE,
    QUOTA_NOT_VERIFIABLE,
    canonical_model_id,
    known_price,
    known_vision,
)


def _image_part(message: dict[str, Any]) -> Optional[str]:
    """Return the base64 data-url of the first image inside a message, if any."""
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
                value = part.get("image_url") or part.get("image") or {}
                if isinstance(value, dict):
                    return value.get("url")
                if isinstance(value, str):
                    return value
    return None


def _text_of(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("text")
        )
    return ""


def _api_model(
    model_id: str,
    *,
    provider_id: str,
    source: str,
    billing: str,
    execution: str = EXEC_REMOTE,
    auth_type: str = AUTH_API_KEY,
    vision: Optional[bool] = "__registry__",  # type: ignore[assignment]
    price: Optional[float] = "__registry__",  # type: ignore[assignment]
    notes: str = "",
    label: str = "",
) -> ModelInfo:
    canonical = canonical_model_id(model_id)
    resolved_vision = known_vision(canonical) if vision == "__registry__" else vision
    if billing == BILLING_PAID_API:
        resolved_cost: Optional[float] = known_price(canonical) if price == "__registry__" else price
    elif billing in (BILLING_FREE_QUOTA, BILLING_LOCAL):
        resolved_cost = 0.0
    else:
        resolved_cost = None
    return ModelInfo(
        id=model_id,
        label=label or canonical,
        provider=provider_id,
        auth_type=auth_type,
        billing=billing,
        subscription_name=None,
        text=True,
        vision=resolved_vision,
        vision_verified=False,
        execution=execution,
        cost_per_1k_eur=resolved_cost,
        quota_status=QUOTA_NOT_VERIFIABLE,
        source=source,
        notes=notes,
    )


class OllamaAdapter(AIProviderAdapter):
    kind = "ollama_local"
    label = "Ollama (locale, opzionale)"
    is_local = True
    is_cli = False
    requires_key = False
    requires_paid_authorization = False
    subscription_quota = False
    supports_attachments = True
    attachment_mode = "inline_base64"
    endpoint = "http://127.0.0.1:11434"

    def detect(self) -> dict[str, Any]:
        try:
            data = self._http_json("GET", f"{self.endpoint}/api/tags", timeout=4)
            count = len(data.get("models") or [])
            return {
                "installed": True,
                "binary": None,
                "version": str((data.get("version") or "")).strip() or None,
                "detail": f"servizio locale attivo · {count} modelli",
            }
        except EngineError:
            return {
                "installed": False,
                "binary": None,
                "version": None,
                "detail": "Ollama non in esecuzione (opzionale: KORA funziona anche senza)",
            }

    def credentials_present(self) -> bool:
        return self.detect()["installed"]

    def authenticated(self) -> dict[str, Any]:
        up = self.detect()["installed"]
        return {
            "authenticated": up,
            "detail": "nessuna autenticazione richiesta (locale)" if up else "servizio non attivo",
            "account": None,
        }

    def list_models(self) -> list[ModelInfo]:
        try:
            data = self._http_json("GET", f"{self.endpoint}/api/tags", timeout=6)
        except EngineError:
            return []
        out: list[ModelInfo] = []
        for item in data.get("models") or []:
            name = str(item.get("name") or item.get("model") or "").strip()
            if not name:
                continue
            info = _api_model(
                name,
                provider_id="ollama",
                source="ollama",
                billing=BILLING_LOCAL,
                execution=EXEC_LOCAL,
                auth_type=AUTH_LOCAL_NO_AUTH,
                notes="inferenza sul computer locale",
            )
            details = item.get("details") or {}
            info.context_tokens = None
            if details.get("parameter_size"):
                info.notes = f"{info.notes} · {details.get('parameter_size')}"
            out.append(info)
        return out

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        timeout: float = 120.0,
        json_mode: bool = False,
        attachments: Optional[list[Path]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> EngineResult:
        models = self.list_models()
        if not models:
            raise EngineError("Ollama: nessun modello installato")
        chosen = model or next((m.id for m in models if m.text), models[0].id)
        payload_messages: list[dict[str, Any]] = []
        for message in messages:
            entry: dict[str, Any] = {"role": message.get("role", "user"), "content": _text_of(message)}
            images: list[str] = []
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
                        value = part.get("image_url") or part.get("image") or {}
                        url = value.get("url") if isinstance(value, dict) else value
                        if isinstance(url, str) and url.startswith("data:"):
                            images.append(url.split(",", 1)[-1])
                    elif isinstance(part, dict) and isinstance(part.get("data"), str):
                        images.append(part["data"])
            if images:
                entry["images"] = images
            payload_messages.append(entry)
        payload: dict[str, Any] = {"model": chosen, "stream": False, "messages": payload_messages}
        if json_mode:
            payload["format"] = "json"
        started = now_ms()
        data = self._http_json(
            "POST", f"{self.endpoint}/api/chat", payload=payload, timeout=timeout, cancel=cancel
        )
        text = str(((data.get("message") or {}).get("content")) or "").strip()
        if not text:
            raise EngineError("Ollama: risposta vuota")
        return EngineResult(
            text=text,
            provider=self.kind,
            model=chosen,
            latency_ms=now_ms() - started,
            cost_eur=0.0,
            local=True,
            subscription=False,
            billing=BILLING_LOCAL,
            execution=EXEC_LOCAL,
        )


class OpenAICompatibleAdapter(AIProviderAdapter):
    """OpenAI, OpenRouter, DeepSeek and any custom OpenAI-compatible endpoint."""

    kind = "openai_api"
    label = "OpenAI API"
    provider_id = "openai_api"
    base_url = "https://api.openai.com/v1"
    default_models = ("gpt-4o-mini", "gpt-4o")
    extra_headers: dict[str, str] = {}
    supports_attachments = True
    attachment_mode = "inline_base64"
    billing = BILLING_PAID_API

    def __init__(self, providers: Any = None, base_url: Optional[str] = None) -> None:
        super().__init__(providers)
        if base_url:
            self.base_url = base_url.rstrip("/")

    # --- credentials ---
    def credentials_present(self) -> bool:
        return bool(self.api_key())

    def api_key(self) -> Optional[str]:
        if self.providers is None:
            return None
        try:
            return self.providers.get_decrypted_key(self.provider_id)
        except Exception:
            return None

    def detect(self) -> dict[str, Any]:
        return {
            "installed": True,
            "binary": None,
            "version": None,
            "detail": f"endpoint {self.base_url}",
        }

    def authenticated(self) -> dict[str, Any]:
        if not self.api_key():
            return {"authenticated": False, "detail": "API key non configurata", "account": None}
        try:
            self._http_json("GET", f"{self.base_url}/models", headers=self._headers(), timeout=10)
        except EngineError as exc:
            return {"authenticated": False, "detail": str(exc), "account": None}
        return {"authenticated": True, "detail": "chiave valida", "account": self.kind}

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        headers.update(self.extra_headers)
        return headers

    def _model_from_api(self, item: dict[str, Any]) -> Optional[ModelInfo]:
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            return None
        low = model_id.lower()
        if any(tag in low for tag in ("embed", "whisper", "tts", "dall-e", "moderation", "audio", "image-")):
            return None
        return _api_model(
            model_id,
            provider_id=self.provider_id,
            source=self.kind,
            billing=self.billing,
            notes="" if known_price(model_id) is not None else "prezzo non pubblicato per questo modello",
        )

    def list_models(self) -> list[ModelInfo]:
        if not self.api_key():
            return []
        try:
            data = self._http_json("GET", f"{self.base_url}/models", headers=self._headers(), timeout=12)
        except EngineError:
            return [
                _api_model(m, provider_id=self.provider_id, source=self.kind, billing=self.billing)
                for m in self.default_models
            ]
        out: list[ModelInfo] = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            info = self._model_from_api(item)
            if info:
                out.append(info)
        out.sort(key=lambda m: (m.cost_per_1k_eur is None, m.cost_per_1k_eur or 0.0))
        return out

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        timeout: float = 90.0,
        json_mode: bool = False,
        attachments: Optional[list[Path]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> EngineResult:
        if not self.api_key():
            raise EngineAuthError(f"{self.label}: API key non configurata")
        chosen = model or (self.default_models[0] if self.default_models else "")
        payload: dict[str, Any] = {
            "model": chosen,
            "messages": [
                {"role": m.get("role", "user"), "content": m.get("content") or _text_of(m)}
                for m in messages
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = now_ms()
        data = self._http_json(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            payload=payload,
            timeout=timeout,
            cancel=cancel,
        )
        try:
            text = str(data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise EngineError(f"{self.label}: risposta inattesa") from exc
        if not text:
            raise EngineError(f"{self.label}: risposta vuota")
        prompt_chars = sum(len(_text_of(m)) for m in messages)
        price = known_price(chosen)
        cost: Optional[float] = (
            round((prompt_chars / 1000.0) * price, 5) if price is not None else None
        )
        return EngineResult(
            text=text,
            provider=self.kind,
            model=chosen,
            latency_ms=now_ms() - started,
            cost_eur=cost,
            local=False,
            subscription=False,
            billing=self.billing,
            execution=EXEC_REMOTE,
        )


class OpenRouterAdapter(OpenAICompatibleAdapter):
    kind = "openrouter"
    label = "OpenRouter"
    provider_id = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    default_models = ("openai/gpt-4o-mini",)
    extra_headers = {"HTTP-Referer": "http://127.0.0.1:8000", "X-Title": "KORA STUDIO"}

    def _model_from_api(self, item: dict[str, Any]) -> Optional[ModelInfo]:
        info = super()._model_from_api(item)
        if info is None:
            return None
        model_id = str(item.get("id") or "")
        if model_id.endswith(":free"):
            info.billing = BILLING_FREE_QUOTA
            info.cost_per_1k_eur = 0.0
            info.notes = "modello gratuito dichiarato da OpenRouter"
        return info


class DeepSeekAdapter(OpenAICompatibleAdapter):
    kind = "deepseek_api"
    label = "DeepSeek"
    provider_id = "deepseek_api"
    base_url = "https://api.deepseek.com/v1"
    default_models = ("deepseek-chat",)


class CustomEndpointAdapter(OpenAICompatibleAdapter):
    """Any OpenAI-compatible endpoint; base URL and key come from the settings."""

    kind = "custom_openai"
    label = "Endpoint OpenAI-compatible personalizzato"
    provider_id = "custom_openai"
    base_url = "http://127.0.0.1:1234/v1"
    default_models = ()
    requires_key = False
    requires_paid_authorization = False
    is_local = True
    billing = BILLING_LOCAL

    def detect(self) -> dict[str, Any]:
        """Un endpoint locale esiste solo se risponde davvero."""
        try:
            data = self._http_json("GET", f"{self.base_url}/models", headers=self._headers(), timeout=5)
        except EngineError as exc:
            return {
                "installed": False,
                "binary": None,
                "version": None,
                "detail": f"endpoint non raggiungibile su {self.base_url} ({exc})",
            }
        count = len((data or {}).get("data") or []) if isinstance(data, dict) else 0
        return {
            "installed": True,
            "binary": None,
            "version": None,
            "detail": f"endpoint attivo su {self.base_url} ({count} modelli dichiarati)",
        }

    def authenticated(self) -> dict[str, Any]:
        if self.api_key():
            return {"authenticated": True, "detail": "chiave configurata", "account": None}
        return {
            "authenticated": True,
            "detail": "endpoint locale senza chiave (nessuna credenziale richiesta)",
            "account": None,
        }

    def _model_from_api(self, item: dict[str, Any]) -> Optional[ModelInfo]:
        info = super()._model_from_api(item)
        if info is not None:
            info.billing = BILLING_LOCAL
            info.execution = EXEC_LOCAL
            info.auth_type = AUTH_LOCAL_NO_AUTH
            info.cost_per_1k_eur = 0.0
            info.notes = "inferenza sul computer locale"
        return info

    def generate(self, *args: Any, **kwargs: Any) -> EngineResult:
        result = super().generate(*args, **kwargs)
        result.local = True
        result.billing = BILLING_LOCAL
        result.execution = EXEC_LOCAL
        result.cost_eur = 0.0
        return result


class GeminiAdapter(AIProviderAdapter):
    kind = "gemini_api"
    label = "Google Gemini"
    provider_id = "gemini_api"
    endpoint = "https://generativelanguage.googleapis.com"
    NON_TEXT = ("image", "embedding", "embed", "tts", "audio", "live", "veo", "aqa")
    supports_attachments = True
    attachment_mode = "inline_base64"

    def api_key(self) -> Optional[str]:
        if self.providers is None:
            return None
        try:
            return self.providers.get_decrypted_key(self.provider_id)
        except Exception:
            return None

    def credentials_present(self) -> bool:
        return bool(self.api_key())

    def detect(self) -> dict[str, Any]:
        return {"installed": True, "binary": None, "version": None, "detail": "endpoint Google AI"}

    def authenticated(self) -> dict[str, Any]:
        if not self.api_key():
            return {"authenticated": False, "detail": "API key non configurata", "account": None}
        try:
            data = self._http_json(
                "GET",
                f"{self.endpoint}/v1beta/models",
                headers={"x-goog-api-key": self.api_key() or ""},
                timeout=10,
            )
        except EngineError as exc:
            return {"authenticated": False, "detail": str(exc), "account": None}
        count = len(data.get("models") or [])
        return {"authenticated": count > 0, "detail": f"{count} modelli visibili", "account": "google"}

    def list_models(self) -> list[ModelInfo]:
        if not self.api_key():
            return []
        try:
            data = self._http_json(
                "GET",
                f"{self.endpoint}/v1beta/models",
                headers={"x-goog-api-key": self.api_key() or ""},
                timeout=12,
            )
        except EngineError:
            # nessun elenco inventato: senza API raggiungibile non conosciamo i modelli
            return []
        flash: list[ModelInfo] = []
        pro: list[ModelInfo] = []
        for item in data.get("models") or []:
            name = str(item.get("name", "")).split("/")[-1]
            if "generateContent" not in (item.get("supportedGenerationMethods") or []):
                continue
            low = name.lower()
            if any(tag in low for tag in self.NON_TEXT):
                continue
            info = _api_model(
                name,
                provider_id=self.provider_id,
                source="gemini",
                billing=BILLING_UNKNOWN,
                notes="piano gratuito/a consumo non verificabile dall'API",
            )
            (pro if "pro" in low else flash).append(info)
        return flash + pro

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        timeout: float = 90.0,
        json_mode: bool = False,
        attachments: Optional[list[Path]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> EngineResult:
        key = self.api_key()
        if not key:
            raise EngineAuthError("Gemini: API key non configurata")
        candidates: list[str] = []
        for name in [model] + [m.id for m in self.list_models()]:
            if name and name not in candidates:
                candidates.append(name)

        system = "\n".join(_text_of(m) for m in messages if m.get("role") == "system")
        parts: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "system":
                continue
            image = _image_part(message)
            if image:
                header, _, payload = str(image).partition(",")
                mime = header.split(";")[0].replace("data:", "") or "image/jpeg"
                parts.append({"inline_data": {"mime_type": mime, "data": payload or image}})
            parts.append({"text": _text_of(message)})
        if system:
            parts.insert(0, {"text": f"ISTRUZIONI DI SISTEMA:\n{system}"})

        body: dict[str, Any] = {"contents": [{"parts": parts}]}
        if json_mode:
            body["generationConfig"] = {"responseMimeType": "application/json"}

        started = now_ms()
        last_error = "nessun modello disponibile"
        for version in ("v1beta", "v1"):
            for name in candidates[:6]:
                try:
                    data = self._http_json(
                        "POST",
                        f"{self.endpoint}/{version}/models/{name}:generateContent",
                        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                        payload=body,
                        timeout=timeout,
                        cancel=cancel,
                    )
                except (EngineAuthError, EngineQuotaExhausted) as exc:
                    # errore dell'account, non del singolo modello: nessun altro candidato può funzionare
                    raise
                except EngineModelUnavailable as exc:
                    last_error = str(exc)
                    continue
                except EngineError as exc:
                    last_error = str(exc)
                    continue
                try:
                    chunks = data["candidates"][0]["content"]["parts"]
                    text = "".join(str(chunk.get("text", "")) for chunk in chunks).strip()
                except (KeyError, IndexError, TypeError):
                    continue
                if text:
                    price = known_price(name)
                    cost = (
                        round((sum(len(_text_of(m)) for m in messages) / 1000.0) * price, 5)
                        if price is not None
                        else None
                    )
                    return EngineResult(
                        text=text,
                        provider=self.kind,
                        model=name,
                        latency_ms=now_ms() - started,
                        cost_eur=cost,
                        local=False,
                        billing=BILLING_UNKNOWN,
                        execution=EXEC_REMOTE,
                    )
                last_error = "risposta vuota"
        raise classify_error(last_error)(f"Gemini: {last_error}")


class AnthropicAdapter(AIProviderAdapter):
    kind = "claude_api"
    label = "Anthropic Claude"
    provider_id = "claude_api"
    endpoint = "https://api.anthropic.com/v1"
    default_models = ("claude-sonnet-4-5", "claude-haiku-4-5")
    supports_attachments = True
    attachment_mode = "inline_base64"

    def api_key(self) -> Optional[str]:
        if self.providers is None:
            return None
        try:
            return self.providers.get_decrypted_key(self.provider_id)
        except Exception:
            return None

    def credentials_present(self) -> bool:
        return bool(self.api_key())

    def detect(self) -> dict[str, Any]:
        return {"installed": True, "binary": None, "version": None, "detail": "endpoint Anthropic"}

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key() or "",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def authenticated(self) -> dict[str, Any]:
        if not self.api_key():
            return {"authenticated": False, "detail": "API key non configurata", "account": None}
        try:
            self._http_json("GET", f"{self.endpoint}/models", headers=self._headers(), timeout=10)
        except EngineError as exc:
            if "HTTP 404" in str(exc):
                return {"authenticated": True, "detail": "chiave accettata (elenco modelli non esposto)", "account": "anthropic"}
            return {"authenticated": False, "detail": str(exc), "account": None}
        return {"authenticated": True, "detail": "chiave valida", "account": "anthropic"}

    def list_models(self) -> list[ModelInfo]:
        if not self.api_key():
            return []
        try:
            data = self._http_json("GET", f"{self.endpoint}/models", headers=self._headers(), timeout=12)
        except EngineError:
            return [
                _api_model(m, provider_id=self.provider_id, source="anthropic", billing=BILLING_PAID_API)
                for m in self.default_models
            ]
        out: list[ModelInfo] = []
        for item in data.get("data") or []:
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue
            out.append(
                _api_model(
                    model_id,
                    provider_id=self.provider_id,
                    source="anthropic",
                    billing=BILLING_PAID_API,
                    label=str(item.get("display_name") or model_id),
                )
            )
        return out

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        timeout: float = 90.0,
        json_mode: bool = False,
        attachments: Optional[list[Path]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> EngineResult:
        if not self.api_key():
            raise EngineAuthError("Anthropic: API key non configurata")
        chosen = model or self.default_models[0]
        system = "\n".join(_text_of(m) for m in messages if m.get("role") == "system")
        payload: dict[str, Any] = {
            "model": chosen,
            "max_tokens": 4000,
            "messages": [
                {"role": m.get("role", "user"), "content": _text_of(m)}
                for m in messages
                if m.get("role") != "system"
            ],
        }
        if system:
            payload["system"] = system
        started = now_ms()
        data = self._http_json(
            "POST",
            f"{self.endpoint}/messages",
            headers=self._headers(),
            payload=payload,
            timeout=timeout,
            cancel=cancel,
        )
        try:
            text = "".join(
                str(block.get("text", "")) for block in data.get("content") or [] if isinstance(block, dict)
            ).strip()
        except Exception as exc:
            raise EngineError("Anthropic: risposta inattesa") from exc
        if not text:
            raise EngineError("Anthropic: risposta vuota")
        price = known_price(chosen)
        cost = (
            round((sum(len(_text_of(m)) for m in messages) / 1000.0) * price, 5)
            if price is not None
            else None
        )
        return EngineResult(
            text=text,
            provider=self.kind,
            model=chosen,
            latency_ms=now_ms() - started,
            cost_eur=cost,
            billing=BILLING_PAID_API,
            execution=EXEC_REMOTE,
        )
