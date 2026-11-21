"""OpenCode / Codex CLI adapters.

KORA never reads, copies or re-uses the OAuth tokens of these tools: it simply
asks the official CLI to perform the work, so the account stays managed by the
tool itself (allowed usage, no subscription circumvention). Processes are
spawned with an argument list (no shell), a controlled working directory, a
minimal environment, a hard timeout and cancellation.

A CLI installed on the laptop does **not** mean "local, free, included":
the inference usually runs on the vendor's servers and the billing depends on
the account that authenticated the CLI. Every model this module exposes carries
the real (or explicitly unknown) provider, auth, billing, execution, cost and
vision metadata — see :mod:`kora_studio.ai_engine.models`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from kora_studio.ai_engine.base import (
    AIProviderAdapter,
    EngineCancelled,
    EngineError,
    EngineModelUnavailable,
    EngineQuotaExhausted,
    EngineRateLimited,
    EngineResult,
    EngineTimeout,
    ModelInfo,
    classify_error,
    find_binary,
    now_ms,
    run_cli,
)
from kora_studio.ai_engine.models import (
    AUTH_LOCAL_NO_AUTH,
    AUTH_OAUTH_SUBSCRIPTION,
    AUTH_UNKNOWN,
    BILLING_LOCAL,
    BILLING_SUBSCRIPTION,
    BILLING_UNKNOWN,
    EXEC_LOCAL,
    EXEC_REMOTE,
    QUOTA_NOT_VERIFIABLE,
    canonical_model_id,
    execution_for_provider,
    known_price,
    known_vision,
    lookup_known,
    provider_of,
    subscription_from_label,
)

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")

#: Agente OpenCode dedicato a KORA: nessun accesso indiscriminato al filesystem,
#: nessuna esecuzione di comandi, nessuna navigazione web.
KORA_AGENT_NAME = "kora"
KORA_OPENCODE_CONFIG: dict[str, Any] = {
    "$schema": "https://opencode.ai/config.json",
    "agent": {
        KORA_AGENT_NAME: {
            "description": "Agente di produzione KORA: sola generazione di testo/JSON, nessun comando",
            "mode": "primary",
            "prompt": (
                "Sei un agente di produzione per KORA Beach Club (Policoro). "
                "Rispondi solo con testo o JSON valido, in italiano. "
                "Non devi mai eseguire comandi, modificare file o accedere alla rete."
            ),
            "permission": {
                "edit": "deny",
                "bash": "deny",
                "webfetch": "deny",
                "write": "deny",
                "patch": "deny",
                "external_directory": "deny",
            },
        }
    },
}

#: Provider ids used by the CLIs mapped to what KORA can honestly say about them.
CLI_PROVIDER_META: dict[str, dict[str, Any]] = {
    "openai": {"label": "OpenAI", "execution": EXEC_REMOTE, "plan_hint": "ChatGPT"},
    "anthropic": {"label": "Anthropic", "execution": EXEC_REMOTE, "plan_hint": "Claude"},
    "google": {"label": "Google", "execution": EXEC_REMOTE, "plan_hint": "Google AI"},
    "google-vertex": {"label": "Google Vertex", "execution": EXEC_REMOTE, "plan_hint": None},
    "github-copilot": {"label": "GitHub Copilot", "execution": EXEC_REMOTE, "plan_hint": "Copilot"},
    "openrouter": {"label": "OpenRouter", "execution": EXEC_REMOTE, "plan_hint": None},
    "deepseek": {"label": "DeepSeek", "execution": EXEC_REMOTE, "plan_hint": None},
    "opencode": {"label": "OpenCode Zen", "execution": EXEC_REMOTE, "plan_hint": None},
    "ollama": {"label": "Ollama", "execution": EXEC_LOCAL, "plan_hint": None},
    "lmstudio": {"label": "LM Studio", "execution": EXEC_LOCAL, "plan_hint": None},
    "llamacpp": {"label": "llama.cpp", "execution": EXEC_LOCAL, "plan_hint": None},
}


def workspace_dir() -> Optional[Path]:
    """Directory di lavoro controllata per i CLI: mai la home, mai il progetto dell'utente."""
    try:
        from kora_studio import config as kora_config

        base = Path(getattr(kora_config, "DATA_DIR", Path.home() / "kora_data")) / "ai_engine_workspace"
        base.mkdir(parents=True, exist_ok=True)
        return base
    except Exception:
        return None


def ensure_opencode_agent_config(workspace: Optional[Path]) -> Optional[Path]:
    """Scrive (una sola volta) la configurazione dell'agente KORA nella workspace del CLI."""
    if workspace is None:
        return None
    target = workspace / "opencode.json"
    try:
        if not target.is_file():
            target.write_text(json.dumps(KORA_OPENCODE_CONFIG, indent=2, ensure_ascii=False))
        return target
    except OSError:
        return None


def _versions(text: str) -> Optional[str]:
    match = _VERSION_RE.search(text or "")
    return match.group(1) if match else None


def _json_lines(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
        elif isinstance(parsed, list):
            out.extend([p for p in parsed if isinstance(p, dict)])
    return out


def _extract_text(events: list[dict[str, Any]], raw_stdout: str) -> str:
    """Extract the assistant text from any of the JSON shapes these CLIs emit."""
    chunks: list[str] = []
    for event in events:
        for key in ("text", "content", "message", "delta", "output"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                chunks.append(value.strip())
            elif isinstance(value, list):
                for part in value:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        chunks.append(part["text"].strip())
        part = event.get("part") or {}
        if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
            chunks.append(part["text"].strip())
    if chunks:
        # de-duplicate consecutive identical chunks (streamed events repeat the message)
        deduped: list[str] = []
        for chunk in chunks:
            if not deduped or deduped[-1] != chunk:
                deduped.append(chunk)
        return "\n".join(deduped).strip()
    plain = (raw_stdout or "").strip()
    if plain and not plain.startswith("{") and not plain.startswith("["):
        return plain
    return ""


def _model_info(
    model_id: str,
    *,
    default_provider: str,
    auth_type: str,
    billing: str,
    subscription_name: Optional[str],
    execution: str,
    source: str,
    notes: str = "",
) -> ModelInfo:
    provider = provider_of(model_id, default=default_provider)
    canonical = canonical_model_id(model_id)
    vision = known_vision(canonical)
    price = known_price(canonical)
    # a cost is "known" only for a paid API with a published price; a subscription
    # or a local model costs 0 and that is known; anything else stays unknown.
    if billing in (BILLING_SUBSCRIPTION, BILLING_LOCAL):
        cost: Optional[float] = 0.0
    elif billing == "free_quota":
        cost = 0.0
    else:
        cost = price  # may be None -> unknown
    return ModelInfo(
        id=model_id,
        label=canonical,
        provider=provider,
        auth_type=auth_type,
        billing=billing,
        subscription_name=subscription_name,
        text=True,
        vision=vision,
        vision_verified=False,
        execution=execution or execution_for_provider(provider),
        cost_per_1k_eur=cost,
        quota_status=QUOTA_NOT_VERIFIABLE,
        context_tokens=(lookup_known(canonical) or {}).get("context"),
        source=source,
        notes=notes,
    )


class _CliAdapter(AIProviderAdapter):
    """Shared plumbing for CLI-based engines."""

    binary_names: tuple[str, ...] = ()
    is_cli = True
    #: the binary runs locally, the *inference* does not: this stays False on purpose
    is_local = False
    requires_key = False
    requires_paid_authorization = False
    subscription_quota = False
    attachment_mode = "none"

    def __init__(self, providers: Any = None, binary_override: Optional[str] = None) -> None:
        super().__init__(providers)
        self._binary_override = binary_override
        self._flags_cache: dict[str, bool] = {}
        self._auth_cache: Optional[dict[str, Any]] = None

    # --- workspace ---
    def workspace(self) -> Optional[Path]:
        return workspace_dir()

    def prepare_workspace(self) -> Optional[Path]:
        return self.workspace()

    # --- binary / version ---
    def binary_path(self) -> Optional[str]:
        if self._binary_override:
            path = Path(self._binary_override)
            return str(path) if path.is_file() and os.access(path, os.X_OK) else None
        return find_binary(*self.binary_names)

    def detect(self) -> dict[str, Any]:
        binary = self.binary_path()
        if not binary:
            return {
                "installed": False,
                "binary": None,
                "version": None,
                "detail": f"{' / '.join(self.binary_names)} non trovato nel PATH",
            }
        version = None
        detail = "installato"
        try:
            code, out, err = run_cli([binary, "--version"], timeout=10)
            version = _versions(out) or _versions(err)
            if code != 0 and not version:
                detail = f"installato ma --version non risponde (exit {code})"
        except EngineError as exc:
            detail = f"installato, versione non rilevabile: {exc}"
        return {"installed": True, "binary": binary, "version": version, "detail": detail}

    # --- capability probing of the installed version ---
    def command_help(self, subcommand: str) -> str:
        binary = self.binary_path()
        if not binary:
            return ""
        for args in ([binary, subcommand, "--help"], [binary, "--help"]):
            try:
                code, out, err = run_cli(args, timeout=10)
            except EngineError:
                continue
            text = f"{out}\n{err}"
            if subcommand in text:
                return text
        return ""

    def supports_flag(self, subcommand: str, flag: str) -> bool:
        key = f"{subcommand}:{flag}"
        if key not in self._flags_cache:
            self._flags_cache[key] = flag in self.command_help(subcommand)
        return self._flags_cache[key]

    # --- auth (delegated, tokens never touched) ---
    def auth_integrations(self, refresh: bool = False) -> list[dict[str, Any]]:
        """Provider ids authenticated inside the CLI, with their connection labels."""
        if self._auth_cache is not None and not refresh:
            return list(self._auth_cache.get("integrations") or [])
        binary = self.binary_path()
        integrations: list[dict[str, Any]] = []
        if binary:
            for args in (
                [binary, "auth", "list", "--format", "json"],
                [binary, "auth", "list"],
                [binary, "auth", "status"],
            ):
                try:
                    code, out, err = run_cli(args, timeout=15)
                except EngineError:
                    continue
                if code != 0:
                    continue
                text = (out or "").strip()
                parsed = self._parse_auth_json(text)
                if parsed:
                    integrations = parsed
                    break
                if text:
                    integrations = self._parse_auth_text(text)
                    break
        self._auth_cache = {"integrations": integrations}
        return list(integrations)

    @staticmethod
    def _parse_auth_json(text: str) -> list[dict[str, Any]]:
        try:
            data = json.loads(text)
        except ValueError:
            return []
        rows: list[dict[str, Any]] = []
        if isinstance(data, dict) and isinstance(data.get("integrations"), list):
            data = data["integrations"]
        if not isinstance(data, list):
            return []
        for item in data:
            if not isinstance(item, dict):
                continue
            provider_id = str(item.get("id") or item.get("provider") or "").strip()
            if not provider_id:
                continue
            labels = []
            for connection in item.get("connections") or []:
                if not isinstance(connection, dict):
                    continue
                label = connection.get("label") or connection.get("name") or connection.get("type") or ""
                if label:
                    labels.append(str(label))
            rows.append({"provider": provider_id, "name": item.get("name") or provider_id, "labels": labels})
        return rows

    @staticmethod
    def _parse_auth_text(text: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if "no authenticated" in text.lower() or "not logged" in text.lower():
            return rows
        for line in text.splitlines():
            line = line.strip().lstrip("•-* ").strip()
            if not line:
                continue
            match = re.match(r"([A-Za-z0-9_.\-]+)\s{1,}(.*)", line)
            if not match:
                continue
            rows.append(
                {
                    "provider": match.group(1),
                    "name": match.group(1),
                    "labels": [match.group(2).strip()] if match.group(2).strip() else [],
                }
            )
        return rows

    def authenticated(self) -> dict[str, Any]:
        binary = self.binary_path()
        if not binary:
            return {"authenticated": False, "detail": "CLI non installato", "account": None, "providers": []}
        integrations = self.auth_integrations(refresh=True)
        providers = sorted({row["provider"] for row in integrations})
        if not integrations:
            return {
                "authenticated": False,
                "detail": "nessun account collegato al CLI (esegui il login ufficiale del CLI)",
                "account": None,
                "providers": [],
            }
        return {
            "authenticated": True,
            "detail": "account gestito dalla CLI (token non letti da KORA)",
            "account": providers[0] if providers else None,
            "providers": providers,
            "integrations": integrations,
        }

    # --- models ---
    def _provider_billing(self, provider_id: str) -> tuple[str, Optional[str], str]:
        """Billing, verifiable subscription name and auth type for a CLI provider."""
        meta = CLI_PROVIDER_META.get(provider_id, {})
        execution = meta.get("execution") or execution_for_provider(provider_id)
        if execution == EXEC_LOCAL:
            return BILLING_LOCAL, None, AUTH_LOCAL_NO_AUTH
        for row in self.auth_integrations():
            if row.get("provider") != provider_id:
                continue
            for label in row.get("labels") or []:
                plan = subscription_from_label(str(label))
                if plan:
                    return BILLING_SUBSCRIPTION, plan, AUTH_OAUTH_SUBSCRIPTION
        # authenticated through the CLI but the plan is not verifiable
        return BILLING_UNKNOWN, None, AUTH_UNKNOWN

    def _build_models(self, model_ids: list[str], default_provider: str) -> list[ModelInfo]:
        out: list[ModelInfo] = []
        for model_id in model_ids:
            provider = provider_of(model_id, default=default_provider)
            billing, plan, auth_type = self._provider_billing(provider)
            out.append(
                _model_info(
                    model_id,
                    default_provider=default_provider,
                    auth_type=auth_type,
                    billing=billing,
                    subscription_name=plan,
                    execution=CLI_PROVIDER_META.get(provider, {}).get("execution")
                    or execution_for_provider(provider),
                    source=self.kind,
                    notes="" if billing != BILLING_UNKNOWN else "piano non verificabile dal CLI",
                )
            )
        return out

    def raw_model_ids(self) -> list[str]:
        raise NotImplementedError

    def list_models(self) -> list[ModelInfo]:
        try:
            return self._build_models(self.raw_model_ids(), self.kind)
        except EngineError:
            return []

    # --- generation ---
    def _build_args(self, prompt: str, model: Optional[str], attachments: list[Path]) -> list[str]:
        raise NotImplementedError

    def _attachment_args(self, attachments: list[Path]) -> tuple[list[str], str]:
        """Return (extra argv, prompt suffix). Never silently drops an image."""
        return [], ""

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        timeout: float = 90.0,
        json_mode: bool = False,
        attachments: Optional[list[Path]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> EngineResult:
        binary = self.binary_path()
        if not binary:
            raise EngineError(f"{self.label}: CLI non installata")
        files = [Path(p) for p in (attachments or []) if Path(p).is_file()]
        missing = [str(p) for p in (attachments or []) if not Path(p).is_file()]
        if missing:
            raise EngineError(f"{self.label}: allegato non trovato ({', '.join(missing[:2])})")
        if files and not self.supports_attachments:
            raise EngineError(
                f"{self.label}: questa versione del CLI non trasferisce immagini al modello "
                "(nessun allegato inviato: la richiesta è stata rifiutata, non svuotata)"
            )

        self.prepare_workspace()
        prompt = self._flatten(messages)
        extra_args, suffix = self._attachment_args(files)
        if suffix:
            prompt = f"{prompt}\n\n{suffix}".strip()
        args = self._build_args(prompt, model, files)
        # image flags go before the positional prompt
        if extra_args:
            args = args[:-1] + extra_args + [args[-1]] if args else extra_args

        started = now_ms()
        cli_timings: dict[str, Any] = {}
        try:
            code, out, err = run_cli(
                args, timeout=timeout, cwd=self.workspace(), cancel=cancel, timings=cli_timings
            )
        except EngineTimeout as exc:
            raise EngineTimeout(f"{self.label}: {exc}") from exc
        except EngineCancelled:
            raise
        latency = now_ms() - started
        if code != 0:
            blob = f"{err}\n{out}".strip()
            hint = (err or out or "").strip().splitlines()
            last = hint[-1][:200] if hint else f"exit {code}"
            raise classify_error(blob)(
                f"{self.label}: comando fallito ({last})"
            )
        text = _extract_text(_json_lines(out), out)
        if not text:
            raise EngineError(f"{self.label}: risposta vuota dal CLI")

        provider = provider_of(model or "", default=self.kind)
        billing, plan, _ = self._provider_billing(provider)
        price = known_price(canonical_model_id(model or ""))
        if billing in (BILLING_SUBSCRIPTION, BILLING_LOCAL):
            cost: Optional[float] = 0.0
        else:
            cost = price  # CLI usage through an API key: only the published price is known
        return EngineResult(
            text=text,
            provider=provider or self.kind,
            model=model or "default",
            latency_ms=latency,
            cost_eur=cost,
            local=CLI_PROVIDER_META.get(provider, {}).get("execution") == EXEC_LOCAL,
            subscription=billing == BILLING_SUBSCRIPTION,
            billing=billing,
            execution=CLI_PROVIDER_META.get(provider, {}).get("execution") or execution_for_provider(provider),
            raw={
                "plan": plan,
                "attachments": [p.name for p in files],
                "engine": self.kind,
                "actual_provider": provider or self.kind,
                "cli_startup_ms": cli_timings.get("spawn_ms"),
                "cli_total_ms": latency,
            },
        )

    @staticmethod
    def _flatten(messages: list[dict[str, Any]]) -> str:
        """Flatten text only: images travel through the attachment channel, never dropped silently."""
        parts: list[str] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content")
            if isinstance(content, list):
                texts = [
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") != "image_url"
                ]
                content = "\n".join(t for t in texts if t)
            parts.append(f"[{role}] {content}")
        return "\n\n".join(parts).strip()


class OpenCodeAdapter(_CliAdapter):
    kind = "opencode"
    label = "OpenCode CLI/SDK"
    binary_names = ("opencode",)
    docs_url = "https://opencode.ai/docs"
    supports_attachments = True
    attachment_mode = "cli_flag"

    def prepare_workspace(self) -> Optional[Path]:
        workspace = self.workspace()
        ensure_opencode_agent_config(workspace)
        return workspace

    def raw_model_ids(self) -> list[str]:
        binary = self.binary_path()
        if not binary:
            return []
        code, out, err = run_cli([binary, "models"], timeout=30)
        if code != 0:
            raise EngineError(f"{self.label}: elenco modelli non disponibile ({(err or out).strip()[:120]})")
        ids: list[str] = []
        for line in (out or "").splitlines():
            name = line.strip()
            if not name or " " in name:
                continue
            if "/" not in name:
                continue
            if name not in ids:
                ids.append(name)
        return ids

    def _attachment_args(self, attachments: list[Path]) -> tuple[list[str], str]:
        if not attachments:
            return [], ""
        if not self.supports_flag("run", "--file"):
            # fall back to the short alias only when the long flag is absent
            if self.supports_flag("run", "-f"):
                args: list[str] = []
                for path in attachments:
                    args += ["-f", str(path)]
                return args, ""
            raise EngineError(
                "OpenCode installato senza supporto allegati (--file): impossibile inviare l'immagine"
            )
        args = []
        for path in attachments:
            args += ["--file", str(path)]
        return args, ""

    def _build_args(self, prompt: str, model: Optional[str], attachments: list[Path]) -> list[str]:
        binary = self.binary_path() or "opencode"
        args = [binary, "run"]
        if self.supports_flag("run", "--format"):
            args += ["--format", "json"]
        if model and model != "default" and self.supports_flag("run", "--model"):
            args += ["--model", model]
        if self.supports_flag("run", "--agent"):
            args += ["--agent", KORA_AGENT_NAME]
        args.append(prompt)
        return args


class CodexAdapter(_CliAdapter):
    kind = "codex"
    label = "Codex CLI"
    binary_names = ("codex",)
    docs_url = "https://github.com/openai/codex"
    #: current Codex reads attachments referenced by path inside the workspace
    supports_attachments = True
    attachment_mode = "workspace_file"

    def config_model(self) -> Optional[str]:
        """Model declared in the CLI configuration (read-only, no credentials)."""
        home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        config_path = home / "config.toml"
        try:
            text = config_path.read_text()
        except OSError:
            return None
        match = re.search(r'^\s*model\s*=\s*"([^"]+)"', text, re.M)
        return match.group(1).strip() if match else None

    def raw_model_ids(self) -> list[str]:
        model = self.config_model()
        return [model] if model else []

    def _attachment_args(self, attachments: list[Path]) -> tuple[list[str], str]:
        if not attachments:
            return [], ""
        if self.supports_flag("exec", "--image"):
            args: list[str] = []
            for path in attachments:
                args += ["--image", str(path)]
            return args, ""
        if self.supports_flag("exec", "-i"):
            args = []
            for path in attachments:
                args += ["-i", str(path)]
            return args, ""
        # documented behaviour of `codex exec`: files inside the workspace are readable
        listed = "\n".join(f"- {p}" for p in attachments)
        return [], (
            "Allegati immagine nella workspace corrente (leggili come immagini e descrivine il contenuto):\n"
            f"{listed}"
        )

    def _build_args(self, prompt: str, model: Optional[str], attachments: list[Path]) -> list[str]:
        binary = self.binary_path() or "codex"
        args = [binary, "exec"]
        if self.supports_flag("exec", "--json"):
            args += ["--json"]
        if model and model != "default" and self.supports_flag("exec", "--model"):
            args += ["--model", model]
        if self.supports_flag("exec", "--sandbox"):
            args += ["--sandbox", "read-only"]
        if self.supports_flag("exec", "--ephemeral"):
            args += ["--ephemeral"]
        args.append(prompt)
        return args


def stage_attachments(paths: list[Path], workspace: Optional[Path]) -> list[Path]:
    """Copy attachments into the controlled workspace (CLIs never see the user's folders)."""
    if workspace is None:
        return list(paths)
    staged: list[Path] = []
    for src in paths:
        src = Path(src)
        if not src.is_file():
            continue
        target = workspace / f"allegato_{src.name}"
        try:
            shutil.copyfile(src, target)
            staged.append(target)
        except OSError:
            staged.append(src)
    return staged
