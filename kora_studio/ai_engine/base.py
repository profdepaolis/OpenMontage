"""Common adapter contract for every AI engine usable by KORA STUDIO.

The Engine Manager never talks to a vendor directly: it only knows adapters that
implement this interface. Every adapter reports *real* states — nothing is
declared operational without an actual probe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

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


@dataclass
class ModelInfo:
    """A model that the probe actually saw on the provider."""

    id: str
    label: str = ""
    text: bool = True
    vision: bool = False
    free: bool = False
    subscription: bool = False  # quota già pagata dall'utente (abbonamento/CLI)
    cost_per_1k_eur: float = 0.0
    latency_ms: Optional[int] = None
    context_tokens: Optional[int] = None
    source: str = "api"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label or self.id,
            "text": self.text,
            "vision": self.vision,
            "free": self.free,
            "subscription": self.subscription,
            "cost_per_1k_eur": self.cost_per_1k_eur,
            "latency_ms": self.latency_ms,
            "context_tokens": self.context_tokens,
            "source": self.source,
        }


@dataclass
class EngineResult:
    text: str
    provider: str
    model: str
    latency_ms: int = 0
    cost_eur: float = 0.0
    local: bool = False
    subscription: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "cost_eur": self.cost_eur,
            "local": self.local,
            "subscription": self.subscription,
        }


class EngineError(RuntimeError):
    """Raised when an adapter cannot satisfy a request; message is user-readable and secret-free."""


class AIProviderAdapter:
    """Base adapter. Subclasses implement detection, auth, models, and generation."""

    kind: str = "base"
    label: str = "Provider"
    is_cli: bool = False
    is_local: bool = False
    requires_key: bool = True
    requires_paid_authorization: bool = True
    #: se True l'adapter usa una quota già inclusa nell'abbonamento dell'utente
    subscription_quota: bool = False
    docs_url: str = ""

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
    ) -> EngineResult:
        raise EngineError(f"{self.label}: generazione non implementata")

    def supports_vision_model(self, model: str) -> bool:
        for info in self.list_models():
            if info.id == model:
                return bool(info.vision)
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
    ) -> dict[str, Any]:
        try:
            resp = requests.request(
                method, url, headers=headers or {}, json=payload, timeout=timeout
            )
        except requests.RequestException as exc:
            raise EngineError(f"{self.label}: rete non raggiungibile ({exc.__class__.__name__})") from exc
        if resp.status_code >= 400:
            message = ""
            try:
                body = resp.json()
                err = body.get("error") if isinstance(body, dict) else None
                message = str((err or {}).get("message") if isinstance(err, dict) else err or "")
            except Exception:
                message = ""
            raise EngineError(
                f"{self.label}: HTTP {resp.status_code}{(' — ' + message[:180]) if message else ''}"
            )
        try:
            return resp.json()
        except Exception as exc:
            raise EngineError(f"{self.label}: risposta non JSON") from exc


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
) -> tuple[int, str, str]:
    """Run a CLI with an argument list (never a shell string) and a hard timeout.

    Only a minimal, non-secret environment is forwarded: tokens are never
    duplicated into child processes by KORA itself.
    """
    if not args:
        raise EngineError("Comando CLI vuoto")
    executable = Path(args[0]).name
    if executable not in os.listdir("/") and not Path(args[0]).exists():
        pass  # resolved below by subprocess; kept explicit for clarity

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
        proc = subprocess.run(
            args,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise EngineError(f"timeout dopo {timeout:.0f}s") from exc
    except FileNotFoundError as exc:
        raise EngineError("eseguibile non trovato") from exc
    except OSError as exc:
        raise EngineError(f"impossibile avviare il processo ({exc.__class__.__name__})") from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def now_ms() -> int:
    return int(time.time() * 1000)


def redact(text: str, secrets: list[str]) -> str:
    """Remove any known secret from a message before it reaches logs or the UI."""
    out = text or ""
    for secret in secrets:
        if secret and len(secret) > 6:
            out = out.replace(secret, "***")
    return out
