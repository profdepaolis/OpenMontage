"""Common adapter contract for every AI engine usable by KORA STUDIO.

The Engine Manager never talks to a vendor directly: it only knows adapters that
implement this interface. Every adapter reports *real* states — nothing is
declared operational without an actual probe — and every model it exposes is
described with honest metadata (billing, execution location, cost, vision).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from kora_studio.ai_engine.models import (
    AUTH_UNKNOWN,
    BILLING_UNKNOWN,
    EXEC_UNKNOWN,
    QUOTA_NOT_VERIFIABLE,
    canonical_model_id,
)

# Provider lifecycle states (never collapsed into a single "active" flag)
STATE_UNAVAILABLE = "indisponibile"  # binary / endpoint not reachable / not installed
STATE_INSTALLED = "installato"  # binary or endpoint present, not authenticated
STATE_AUTHENTICATED = "autenticato"  # credentials accepted, not tested for generation
STATE_OPERATIONAL = "operativo"  # real generation/test succeeded
STATE_ERROR = "errore"

STATE_ORDER = (
    STATE_UNAVAILABLE,
    STATE_INSTALLED,
    STATE_AUTHENTICATED,
    STATE_OPERATIONAL,
    STATE_ERROR,
)

# Routing strategies
STRATEGY_AUTO = "auto"
STRATEGY_QUALITY = "max_quality"
STRATEGY_COST = "min_cost"
STRATEGY_LOCAL = "local_only"
STRATEGIES = (STRATEGY_AUTO, STRATEGY_QUALITY, STRATEGY_COST, STRATEGY_LOCAL)


# --------------------------------------------------------------------------- errors
class EngineError(RuntimeError):
    """Base error: message is user readable and secret-free."""

    kind = "errore"
    retryable = False


class EngineTimeout(EngineError):
    kind = "timeout"
    retryable = True


class EngineRateLimited(EngineError):
    kind = "rate_limit"
    retryable = True


class EngineQuotaExhausted(EngineError):
    kind = "quota_esaurita"


class EngineModelUnavailable(EngineError):
    kind = "modello_non_disponibile"


class EngineAuthError(EngineError):
    kind = "autenticazione"


class EngineBlockedByBudget(EngineError):
    kind = "budget"


class EngineCancelled(EngineError):
    kind = "annullata"


class EngineVisionUnsupported(EngineError):
    kind = "visione_non_supportata"


def classify_error(message: str, status_code: Optional[int] = None) -> type[EngineError]:
    """Map a vendor message/status to the precise error type (drives routing)."""
    text = (message or "").lower()
    if status_code == 429 or "rate limit" in text or "too many requests" in text:
        return EngineRateLimited
    if status_code in (401, 403) or "unauthorized" in text or "invalid api key" in text:
        return EngineAuthError
    if "quota" in text or "insufficient_quota" in text or "billing" in text:
        return EngineQuotaExhausted
    if status_code == 404 or "model_not_found" in text or "does not exist" in text or "not found" in text:
        return EngineModelUnavailable
    if "timeout" in text or "timed out" in text:
        return EngineTimeout
    return EngineError


# --------------------------------------------------------------------------- data
@dataclass
class ModelInfo:
    """A model the probe actually saw, described with verifiable metadata."""

    id: str
    label: str = ""
    # effective provider, even when the model was discovered through a CLI
    provider: str = ""
    auth_type: str = AUTH_UNKNOWN
    billing: str = BILLING_UNKNOWN
    #: only set when the CLI/API output makes the plan verifiable
    subscription_name: Optional[str] = None
    text: bool = True
    #: True only when verified (curated registry or a real multimodal test)
    vision: Optional[bool] = None
    vision_verified: bool = False
    execution: str = EXEC_UNKNOWN
    #: None -> the cost is not known (blocked when the budget is zero)
    cost_per_1k_eur: Optional[float] = None
    quota_status: str = QUOTA_NOT_VERIFIABLE
    latency_ms: Optional[int] = None
    context_tokens: Optional[int] = None
    source: str = "api"
    notes: str = ""

    @property
    def cost_known(self) -> bool:
        return self.cost_per_1k_eur is not None

    @property
    def zero_cost(self) -> bool:
        """True only when no money can be spent by calling this model."""
        return self.billing in ("subscription", "free_quota", "local_compute")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label or self.id,
            "provider": self.provider or self.source,
            "auth_type": self.auth_type,
            "billing": self.billing,
            "subscription_name": self.subscription_name,
            "text": self.text,
            "vision": self.vision,
            "vision_documented": self.vision,
            "vision_known": self.vision is not None,
            "zero_cost": self.zero_cost,
            "vision_verified": self.vision_verified,
            "execution": self.execution,
            "cost_per_1k_eur": self.cost_per_1k_eur,
            "cost_known": self.cost_known,
            "quota_status": self.quota_status,
            "latency_ms": self.latency_ms,
            "context_tokens": self.context_tokens,
            "source": self.source,
            "notes": self.notes,
        }


@dataclass
class EngineResult:
    text: str
    provider: str
    model: str
    latency_ms: int = 0
    #: None -> cost unknown
    cost_eur: Optional[float] = None
    local: bool = False
    subscription: bool = False
    billing: str = BILLING_UNKNOWN
    execution: str = EXEC_UNKNOWN
    attempts: int = 1
    fallback_used: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "cost_eur": self.cost_eur,
            "cost_known": self.cost_eur is not None,
            "local": self.local,
            "subscription": self.subscription,
            "billing": self.billing,
            "execution": self.execution,
            "attempts": self.attempts,
            "fallback_used": self.fallback_used,
        }


# --------------------------------------------------------------------------- adapter
class AIProviderAdapter:
    """Base adapter. Subclasses implement detection, auth, models, and generation."""

    kind: str = "base"
    label: str = "Provider"
    is_cli: bool = False
    is_local: bool = False
    requires_key: bool = True
    requires_paid_authorization: bool = True
    subscription_quota: bool = False
    docs_url: str = ""
    #: True when the adapter can actually transfer image attachments to the model
    supports_attachments: bool = False
    attachment_mode: str = "none"

    def __init__(self, providers: Any = None) -> None:
        self.providers = providers
        self._last_probe: dict[str, Any] = {}

    # ---- detection -----------------------------------------------------
    def detect(self) -> dict[str, Any]:
        """Return {'installed': bool, 'version': str|None, 'binary': str|None, 'detail': str}."""
        return {"installed": False, "version": None, "binary": None, "detail": ""}

    def credentials_present(self) -> bool:
        """True when a key/session exists (never returns the secret itself)."""
        return False

    def authenticated(self) -> dict[str, Any]:
        """Real authentication probe. Returns {'authenticated': bool, 'detail': str, 'account': str|None}."""
        return {
            "authenticated": self.credentials_present(),
            "detail": "" if self.credentials_present() else "credenziali assenti",
            "account": None,
        }

    # ---- models --------------------------------------------------------
    def list_models(self) -> list[ModelInfo]:
        raise EngineError(f"{self.label}: elenco modelli non disponibile")

    def pick_default_model(self) -> Optional[ModelInfo]:
        models = [m for m in self.list_models() if m.text]
        return models[0] if models else None

    # ---- generation ----------------------------------------------------
    def generate(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        timeout: float = 60.0,
        json_mode: bool = False,
        attachments: Optional[list[Path]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> EngineResult:
        raise EngineError(f"{self.label}: generazione non implementata")

    def supports_vision_model(self, model: str) -> bool:
        for info in self.list_models():
            if canonical_model_id(info.id) == canonical_model_id(model):
                return info.vision is True
        return False

    # ---- helpers -------------------------------------------------------
    def _http_json(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        payload: Optional[dict[str, Any]] = None,
        timeout: float = 30.0,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        if cancel and cancel():
            raise EngineCancelled(f"{self.label}: richiesta annullata")
        try:
            resp = requests.request(
                method, url, headers=headers or {}, json=payload, timeout=timeout
            )
        except requests.Timeout as exc:
            raise EngineTimeout(f"{self.label}: timeout di rete dopo {timeout:.0f}s") from exc
        except requests.RequestException as exc:
            raise EngineError(f"{self.label}: rete non raggiungibile ({exc.__class__.__name__})") from exc
        if cancel and cancel():
            raise EngineCancelled(f"{self.label}: richiesta annullata")
        if resp.status_code >= 400:
            message = ""
            try:
                body = resp.json()
                err = body.get("error") if isinstance(body, dict) else None
                message = str((err or {}).get("message") if isinstance(err, dict) else err or "")
            except Exception:
                message = ""
            raise classify_error(message, resp.status_code)(
                f"{self.label}: HTTP {resp.status_code}{(' — ' + message[:180]) if message else ''}"
            )
        try:
            return resp.json()
        except Exception as exc:
            raise EngineError(f"{self.label}: risposta non JSON") from exc


# --------------------------------------------------------------------------- utils
def find_binary(*names: str) -> Optional[str]:
    """Locate an executable without invoking a shell."""
    for name in names:
        path = shutil.which(name)
        if path:
            return path
        for base in (
            Path.home() / ".local" / "bin",
            Path.home() / ".opencode" / "bin",
            Path.home() / ".cargo" / "bin",
            Path("/usr/local/bin"),
            Path("/opt/homebrew/bin"),
        ):
            candidate = base / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def run_cli(
    args: list[str],
    *,
    timeout: float = 60.0,
    cwd: Optional[Path] = None,
    stdin_text: Optional[str] = None,
    env_allow: Optional[dict[str, str]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> tuple[int, str, str]:
    """Run a CLI with an argument list (never a shell string), a hard timeout and cancellation.

    Only a minimal, non-secret environment is forwarded: KORA never duplicates
    the OAuth tokens of the CLI into its own process environment.
    """
    if not args:
        raise EngineError("Comando CLI vuoto")

    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "LANG": os.environ.get("LANG", "it_IT.UTF-8"),
        "TERM": "dumb",
        "NO_COLOR": "1",
        "CI": "1",
    }
    if env_allow:
        env.update({k: v for k, v in env_allow.items() if v})

    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
    except FileNotFoundError as exc:
        raise EngineError("eseguibile non trovato") from exc
    except OSError as exc:
        raise EngineError(f"impossibile avviare il processo ({exc.__class__.__name__})") from exc

    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except OSError:
            pass

    started = time.time()
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=0.25)
                return proc.returncode or 0, out or "", err or ""
            except subprocess.TimeoutExpired:
                if cancel and cancel():
                    proc.kill()
                    proc.communicate()
                    raise EngineCancelled("richiesta annullata")
                if time.time() - started > timeout:
                    proc.kill()
                    proc.communicate()
                    raise EngineTimeout(f"timeout dopo {timeout:.0f}s")
    finally:
        if proc.poll() is None:
            proc.kill()


def now_ms() -> int:
    return int(time.time() * 1000)


def redact(text: str, secrets: list[str]) -> str:
    """Remove any known secret from a message before it reaches logs or the UI."""
    out = text or ""
    for secret in secrets:
        if secret and len(secret) > 6:
            out = out.replace(secret, "***")
    return out
