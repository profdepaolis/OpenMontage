"""OpenCode / Codex CLI adapters.

KORA never reads, copies or re-uses the OAuth tokens of these tools: it simply
asks the official CLI to perform the work, so the account stays managed by the
tool itself (allowed usage, no subscription circumvention). Processes are
spawned with an argument list (no shell), a controlled working directory, a
minimal environment and a hard timeout.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from kora_studio.ai_engine.base import (
    AIProviderAdapter,
    EngineError,
    EngineResult,
    ModelInfo,
    find_binary,
    now_ms,
    run_cli,
)

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")

#: Agente OpenCode dedicato a KORA: nessun accesso indiscriminato al filesystem,
#: nessuna esecuzione di comandi, nessuna navigazione web. Il modello deve solo
#: ragionare sul testo che riceve e restituire JSON.
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
        return "\n".join(chunks).strip()
    # Fallback: plain-text mode
    plain = (raw_stdout or "").strip()
    if plain and not plain.startswith("{") and not plain.startswith("["):
        return plain
    return ""


class _CliAdapter(AIProviderAdapter):
    """Shared plumbing for CLI-based engines."""

    binary_names: tuple[str, ...] = ()
    is_cli = True
    is_local = True
    requires_key = False
    requires_paid_authorization = False
    subscription_quota = True

    def __init__(self, providers: Any = None, binary_override: Optional[str] = None) -> None:
        super().__init__(providers)
        self._binary_override = binary_override
        self._flags_cache: dict[str, bool] = {}

    # --- workspace ---
    def workspace(self) -> Optional[Path]:
        """Cartella di lavoro isolata: il CLI non vede la home dell'utente."""
        return workspace_dir()

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
    def authenticated(self) -> dict[str, Any]:
        binary = self.binary_path()
        if not binary:
            return {"authenticated": False, "detail": "CLI non installato", "account": None, "providers": []}
        for args in ([binary, "auth", "list"], [binary, "auth", "status"]):
            try:
                code, out, err = run_cli(args, timeout=15)
            except EngineError:
                continue
            text = f"{out}\n{err}".strip()
            if code != 0:
                continue
            providers = sorted(
                {
                    match.group(1)
                    for match in re.finditer(r"^\s*[•\-\*]?\s*([a-zA-Z0-9_\-]+)\s*(?:\(|:|\s{2,})", text, re.M)
                }
            )
            if "no credentials" in text.lower() or "not logged" in text.lower():
                return {"authenticated": False, "detail": "nessun account collegato", "account": None, "providers": []}
            return {
                "authenticated": True,
                "detail": "account gestito dalla CLI (token non letti da KORA)",
                "account": providers[0] if providers else None,
                "providers": providers,
            }
        return {
            "authenticated": False,
            "detail": "stato autenticazione non verificabile con questa versione",
            "account": None,
            "providers": [],
        }

    def list_models(self) -> list[ModelInfo]:
        binary = self.binary_path()
        if not binary:
            return []
        for args in ([binary, "models"], [binary, "models", "--json"]):
            try:
                code, out, err = run_cli(args, timeout=25)
            except EngineError:
                continue
            if code != 0:
                continue
            seen: list[ModelInfo] = []
            for line in (out or "").splitlines():
                name = line.strip().split()[0] if line.strip() else ""
                if not name or name.startswith("-"):
                    continue
                if "/" not in name and not re.match(r"^[A-Za-z0-9._\-]+$", name):
                    continue
                seen.append(
                    ModelInfo(
                        id=name,
                        label=name,
                        text=True,
                        vision=bool(re.search(r"(vision|gpt-4o|gpt-5|claude|gemini|llava|qwen)", name, re.I)),
                        subscription=True,
                        source=self.kind,
                    )
                )
            unique: dict[str, ModelInfo] = {}
            for model in seen:
                unique.setdefault(model.id, model)
            if unique:
                return list(unique.values())
        return []

    # --- generation ---
    def _build_args(self, prompt: str, model: Optional[str]) -> list[str]:
        raise NotImplementedError

    def prepare_workspace(self) -> Optional[Path]:
        """Hook: prepara la cartella di lavoro prima di eseguire il CLI."""
        return self.workspace()

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        timeout: float = 90.0,
        json_mode: bool = False,
    ) -> EngineResult:
        binary = self.binary_path()
        if not binary:
            raise EngineError(f"{self.label}: CLI non installata")
        self.prepare_workspace()
        prompt = self._flatten(messages)
        args = self._build_args(prompt, model)
        started = now_ms()
        code, out, err = run_cli(args, timeout=timeout, cwd=self.workspace())
        latency = now_ms() - started
        if code != 0:
            message = (err or out or "").strip().splitlines()
            hint = message[-1][:200] if message else f"exit {code}"
            raise EngineError(f"{self.label}: comando fallito ({hint})")
        text = _extract_text(_json_lines(out), out)
        if not text:
            raise EngineError(f"{self.label}: risposta vuota dal CLI")
        return EngineResult(
            text=text,
            provider=self.kind,
            model=model or "auto",
            latency_ms=latency,
            cost_eur=0.0,
            local=True,
            subscription=True,
        )

    @staticmethod
    def _flatten(messages: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    str(part.get("text", "")) for part in content if isinstance(part, dict)
                )
            parts.append(f"[{role}] {content}")
        return "\n\n".join(parts).strip()


class OpenCodeAdapter(_CliAdapter):
    kind = "opencode"
    label = "OpenCode CLI/SDK"
    binary_names = ("opencode",)
    docs_url = "https://opencode.ai/docs"

    def _build_args(self, prompt: str, model: Optional[str]) -> list[str]:
        binary = self.binary_path() or "opencode"
        args = [binary, "run"]
        if self.supports_flag("run", "--format"):
            args += ["--format", "json"]
        if model and self.supports_flag("run", "--model"):
            args += ["--model", model]
        # Agente dedicato con permessi minimi (definito in workspace/opencode.json).
        if self.supports_flag("run", "--agent"):
            args += ["--agent", KORA_AGENT_NAME]
        # Prompt passed as a single argv element: no shell, no concatenation.
        args.append(prompt)
        return args


    def prepare_workspace(self) -> Optional[Path]:
        workspace = self.workspace()
        ensure_opencode_agent_config(workspace)
        return workspace


class CodexAdapter(_CliAdapter):
    kind = "codex"
    label = "Codex CLI"
    binary_names = ("codex",)
    docs_url = "https://github.com/openai/codex"

    def _build_args(self, prompt: str, model: Optional[str]) -> list[str]:
        binary = self.binary_path() or "codex"
        args = [binary, "exec"]
        if self.supports_flag("exec", "--json"):
            args += ["--json"]
        if model and self.supports_flag("exec", "--model"):
            args += ["--model", model]
        # Nessuna scrittura sul filesystem dell'utente: esecuzione in sola lettura.
        if self.supports_flag("exec", "--sandbox"):
            args += ["--sandbox", "read-only"]
        args.append(prompt)
        return args
