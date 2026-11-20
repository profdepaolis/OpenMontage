"""HTTP adapters: Ollama (optional), OpenAI-compatible (OpenAI, OpenRouter,
DeepSeek, custom endpoint), Google Gemini, Anthropic."""

from __future__ import annotations

from typing import Any, Optional

from kora_studio.ai_engine.base import (
    AIProviderAdapter,
    EngineError,
    EngineResult,
    ModelInfo,
    now_ms,
)

# Indicative prices in EUR per 1k input tokens (0 = included in a plan/local).
PRICES_PER_1K = {
    "gpt-4o-mini": 0.00014,
    "gpt-4o": 0.0023,
    "gpt-4.1-mini": 0.00036,
    "claude-3-5-sonnet-latest": 0.0027,
    "claude-3-5-haiku-latest": 0.0007,
    "gemini-2.5-flash": 0.00027,
    "gemini-2.0-flash": 0.00009,
    "gemini-2.5-pro": 0.0011,
    "deepseek-chat": 0.00025,
}


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


class OllamaAdapter(AIProviderAdapter):
    kind = "ollama_local"
    label = "Ollama (locale, opzionale)"
    is_local = True
    is_cli = False
    requires_key = False
    requires_paid_authorization = False
    subscription_quota = True
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
            out.append(
                ModelInfo(
                    id=name,
                    label=name,
                    text=True,
                    vision=bool(
                        any(tag in name.lower() for tag in ("llava", "vision", "qwen2.5-vl", "gemma3"))
                    ),
                    free=True,
                    latency_ms=None,
                    source="ollama",
                )
            )
        return out

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        timeout: float = 120.0,
        json_mode: bool = False,
    ) -> EngineResult:
        models = self.list_models()
        if not models:
            raise EngineError("Ollama: nessun modello installato")
        chosen = model or next((m.id for m in models if m.text), models[0].id)
        payload: dict[str, Any] = {
            "model": chosen,
            "stream": False,
            "messages": [
                {"role": m.get("role", "user"), "content": _text_of(m)} for m in messages
            ],
        }
        if json_mode:
            payload["format"] = "json"
        started = now_ms()
        data = self._http_json(
            "POST", f"{self.endpoint}/api/chat", payload=payload, timeout=timeout
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
            subscription=True,
        )


class OpenAICompatibleAdapter(AIProviderAdapter):
    """OpenAI, OpenRouter, DeepSeek and any custom OpenAI-compatible endpoint."""

    kind = "openai_api"
    label = "OpenAI API"
    provider_id = "openai_api"
    base_url = "https://api.openai.com/v1"
    default_models = ("gpt-4o-mini", "gpt-4o")
    extra_headers: dict[str, str] = {}

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
            self._http_json(
                "GET",
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=10,
            )
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

    def list_models(self) -> list[ModelInfo]:
        if not self.api_key():
            return []
        try:
            data = self._http_json(
                "GET", f"{self.base_url}/models", headers=self._headers(), timeout=12
            )
        except EngineError:
            return [ModelInfo(id=m, label=m, text=True) for m in self.default_models]
        out: list[ModelInfo] = []
        for item in data.get("data") or []:
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue
            low = model_id.lower()
            if any(tag in low for tag in ("embed", "whisper", "tts", "dall-e", "moderation", "audio")):
                continue
            out.append(
                ModelInfo(
                    id=model_id,
                    label=model_id,
                    text=True,
                    vision=bool(
                        any(tag in low for tag in ("gpt-4o", "gpt-4.1", "gpt-5", "vision", "claude", "gemini"))
                    ),
                    cost_per_1k_eur=PRICES_PER_1K.get(model_id, 0.002),
                    source=self.kind,
                )
            )
        out.sort(key=lambda m: m.cost_per_1k_eur)
        return out

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        timeout: float = 90.0,
        json_mode: bool = False,
    ) -> EngineResult:
        if not self.api_key():
            raise EngineError(f"{self.label}: API key non configurata")
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
        )
        try:
            text = str(data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise EngineError(f"{self.label}: risposta inattesa") from exc
        if not text:
            raise EngineError(f"{self.label}: risposta vuota")
        prompt_chars = sum(len(_text_of(m)) for m in messages)
        return EngineResult(
            text=text,
            provider=self.kind,
            model=chosen,
            latency_ms=now_ms() - started,
            cost_eur=round((prompt_chars / 1000.0) * PRICES_PER_1K.get(chosen, 0.002), 5),
            local=False,
            subscription=False,
        )


class OpenRouterAdapter(OpenAICompatibleAdapter):
    kind = "openrouter"
    label = "OpenRouter"
    provider_id = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    default_models = ("openai/gpt-4o-mini",)
    extra_headers = {"HTTP-Referer": "http://127.0.0.1:8000", "X-Title": "KORA STUDIO"}


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
    is_local = True

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


class GeminiAdapter(AIProviderAdapter):
    kind = "gemini_api"
    label = "Google Gemini"
    provider_id = "gemini_api"
    endpoint = "https://generativelanguage.googleapis.com"
    TEXT_FALLBACKS = (
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-flash-latest",
        "gemini-1.5-flash",
    )
    NON_TEXT = ("image", "embedding", "embed", "tts", "audio", "live", "veo", "aqa")

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
            return [ModelInfo(id=m, label=m, text=True) for m in self.TEXT_FALLBACKS]
        flash: list[ModelInfo] = []
        pro: list[ModelInfo] = []
        for item in data.get("models") or []:
            name = str(item.get("name", "")).split("/")[-1]
            if "generateContent" not in (item.get("supportedGenerationMethods") or []):
                continue
            low = name.lower()
            if any(tag in low for tag in self.NON_TEXT):
                continue
            info = ModelInfo(
                id=name,
                label=name,
                text=True,
                vision=True,
                cost_per_1k_eur=PRICES_PER_1K.get(name, 0.0003),
                source="gemini",
            )
            (pro if "pro" in low else flash).append(info)
        return flash + pro

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        timeout: float = 90.0,
        json_mode: bool = False,
    ) -> EngineResult:
        key = self.api_key()
        if not key:
            raise EngineError("Gemini: API key non configurata")
        candidates: list[str] = []
        for name in [model] + [m.id for m in self.list_models()] + list(self.TEXT_FALLBACKS):
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
                    )
                except EngineError as exc:
                    last_error = str(exc)
                    continue
                try:
                    chunks = data["candidates"][0]["content"]["parts"]
                    text = "".join(str(chunk.get("text", "")) for chunk in chunks).strip()
                except (KeyError, IndexError, TypeError):
                    continue
                if text:
                    return EngineResult(
                        text=text,
                        provider=self.kind,
                        model=name,
                        latency_ms=now_ms() - started,
                        cost_eur=round(
                            (sum(len(_text_of(m)) for m in messages) / 1000.0)
                            * PRICES_PER_1K.get(name, 0.0003),
                            5,
                        ),
                        local=False,
                    )
                last_error = "risposta vuota"
        raise EngineError(f"Gemini: {last_error}")


class AnthropicAdapter(AIProviderAdapter):
    kind = "claude_api"
    label = "Anthropic Claude"
    provider_id = "claude_api"
    endpoint = "https://api.anthropic.com/v1"
    default_models = ("claude-3-5-sonnet-latest", "claude-3-5-haiku-latest")

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
            # /models may not exist on older keys: a 404 still proves the key is accepted upstream
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
                ModelInfo(id=m, label=m, text=True, vision=True, cost_per_1k_eur=PRICES_PER_1K.get(m, 0.003))
                for m in self.default_models
            ]
        out: list[ModelInfo] = []
        for item in data.get("data") or []:
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue
            out.append(
                ModelInfo(
                    id=model_id,
                    label=str(item.get("display_name") or model_id),
                    text=True,
                    vision=True,
                    cost_per_1k_eur=PRICES_PER_1K.get(model_id, 0.003),
                    source="anthropic",
                )
            )
        return out

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        timeout: float = 90.0,
        json_mode: bool = False,
    ) -> EngineResult:
        if not self.api_key():
            raise EngineError("Anthropic: API key non configurata")
        chosen = model or self.default_models[0]
        system = "\n".join(_text_of(m) for m in messages if m.get("role") == "system")
        payload: dict[str, Any] = {
            "model": chosen,
            "max_tokens": 2000,
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
            "POST", f"{self.endpoint}/messages", headers=self._headers(), payload=payload, timeout=timeout
        )
        try:
            text = "".join(
                str(block.get("text", "")) for block in data.get("content") or [] if isinstance(block, dict)
            ).strip()
        except Exception as exc:
            raise EngineError("Anthropic: risposta inattesa") from exc
        if not text:
            raise EngineError("Anthropic: risposta vuota")
        return EngineResult(
            text=text,
            provider=self.kind,
            model=chosen,
            latency_ms=now_ms() - started,
            cost_eur=round(
                (sum(len(_text_of(m)) for m in messages) / 1000.0)
                * PRICES_PER_1K.get(chosen, 0.003),
                5,
            ),
        )
