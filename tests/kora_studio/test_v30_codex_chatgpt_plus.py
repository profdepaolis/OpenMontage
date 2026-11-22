"""Codex CLI + piano ChatGPT: riconoscimento reale dell'account e costo zero.

Nessuna rete: i CLI sono script finti che riproducono l'output documentato di
`codex login status` (account ChatGPT) e `codex login status` da disconnesso.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from kora_studio.ai_engine.adapters_cli import CodexAdapter
from kora_studio.ai_engine.manager import AIEngineManager
from kora_studio.db.database import Database
from kora_studio.providers.registry import ProviderManager

CODEX_ON_PLUS = r'''#!/usr/bin/env python3
import sys
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("codex-cli 0.50.0")
    sys.exit(0)
if argv[:2] == ["login", "status"]:
    print("Logged in using ChatGPT (Plus)")
    sys.exit(0)
if argv[:2] == ["auth", "list"]:
    print("error: unrecognized subcommand 'auth'", file=sys.stderr)
    sys.exit(2)
if argv[:2] == ["exec", "--help"]:
    print("codex exec [prompt]\n  --json\n  --model <m>\n  --sandbox <mode>")
    sys.exit(0)
print("ok")
sys.exit(0)
'''

CODEX_LOGGED_OUT = r'''#!/usr/bin/env python3
import sys
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("codex-cli 0.50.0")
    sys.exit(0)
if argv[:2] == ["login", "status"]:
    print("Not logged in", file=sys.stderr)
    sys.exit(1)
if argv[:2] == ["exec", "--help"]:
    print("codex exec [prompt]\n  --json")
    sys.exit(0)
sys.exit(1)
'''


@pytest.fixture()
def env(isolated_env: dict):
    db = Database(isolated_env["db_path"])
    providers = ProviderManager(db)
    providers.ensure_seeded()
    return {"db": db, "providers": providers, "tmp": isolated_env["tmp_path"]}


def _script(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_codex_chatgpt_plus_is_authenticated_and_zero_cost(env):
    binary = _script(env["tmp"] / "codex", CODEX_ON_PLUS)
    adapter = CodexAdapter(env["providers"], binary_override=str(binary))
    manager = AIEngineManager(env["providers"], env["db"])
    manager._adapters["codex"] = adapter
    manager.update_settings({"essential": {"enabled": True, "active_provider": "codex", "enabled_providers": ["codex"]}})

    auth = adapter.authenticated()
    assert auth["authenticated"] is True
    assert "openai" in auth["providers"]

    models = adapter.list_models()
    assert models, "il CLI deve esporre almeno il modello predefinito"
    assert models[0].zero_cost is True
    assert models[0].billing == "subscription"
    assert models[0].cost_per_1k_eur == 0.0

    plan = manager.candidate_plan("creative_director", from_snapshot=False)
    assert plan and plan[0]["provider"] == "codex"
    assert plan[0]["zero_cost"] is True
    ok, reason = manager._authorize(plan[0], None)
    assert ok is True, reason


def test_codex_logged_out_is_not_authenticated(env):
    binary = _script(env["tmp"] / "codex-out", CODEX_LOGGED_OUT)
    adapter = CodexAdapter(env["providers"], binary_override=str(binary))
    assert adapter.authenticated()["authenticated"] is False
