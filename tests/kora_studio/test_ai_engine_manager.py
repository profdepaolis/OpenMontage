"""KORA STUDIO 2.7 — AI Engine Manager acceptance tests.

Contract under test (v2.7 "AI Engine Reliability"):
  * OpenCode/Codex detection + JSON CLI runs with argv-only invocation
    (no shell, no token extraction, injection-proof prompt handling);
  * per-model honest metadata: provider, auth type, billing, verifiable plan,
    multimodal capability, execution, cost known/unknown, quota;
  * budget 0 blocks any call whose cost is not verifiably zero;
  * vision requires a *verified* capability, never a name;
  * Ollama optional, provider switch without restart, per-agent config;
  * UI states installato / autenticato / operativo / indisponibile are earned.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

from kora_studio.agents.director import KoraDirector
from kora_studio.ai_engine.adapters_cli import CodexAdapter, OpenCodeAdapter
from kora_studio.ai_engine.agents import AGENTS_BY_ID, VISION_AGENTS
from kora_studio.ai_engine.base import AIProviderAdapter, EngineError, EngineResult, ModelInfo
from kora_studio.ai_engine.manager import AIEngineManager
from kora_studio.ai_engine.models import (
    BILLING_FREE_QUOTA,
    BILLING_PAID_API,
    BILLING_SUBSCRIPTION,
    BILLING_UNKNOWN,
    EXEC_LOCAL,
    EXEC_REMOTE,
)
from kora_studio.api.app import create_app
from kora_studio.db.database import Database
from kora_studio.providers.registry import ProviderManager

FAKE_OPENCODE = r'''#!/usr/bin/env python3
import json, sys
LOG = "__LOG__"
argv = sys.argv[1:]
import os
with open(LOG, "a") as fh:
    fh.write(json.dumps({"argv": argv, "cwd": os.getcwd()}) + "\n")
if argv[:1] == ["--version"]:
    print("opencode 0.4.2")
    sys.exit(0)
if argv[:2] == ["run", "--help"] or argv[:1] == ["--help"]:
    print("opencode run [prompt]\n  --format <fmt>  output json\n  --model <m>\n  --agent <name>\n  --file <path>")
    sys.exit(0)
if argv[:2] == ["auth", "list"]:
    print("anthropic  (Claude Pro)      stored")
    print("openai     (ChatGPT Plus)    environment")
    sys.exit(0)
if argv[:1] == ["models"]:
    print("anthropic/claude-sonnet-4\nopenai/gpt-5.1\nlmstudio/qwen2.5-coder")
    sys.exit(0)
if argv[:1] == ["run"]:
    print(json.dumps({"type": "text", "text": "KORA OK"}))
    print(json.dumps({"type": "step_finish", "reason": "stop"}))
    sys.exit(0)
print("unknown", file=sys.stderr)
sys.exit(2)
'''

FAKE_CODEX_LOGGED_OUT = r'''#!/usr/bin/env python3
import sys
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("codex-cli 0.48.0")
    sys.exit(0)
if argv[:1] == ["exec"] and "--help" in argv:
    print("codex exec [prompt]\n  --json\n  --model <m>")
    sys.exit(0)
print("Not logged in. Run codex login.", file=sys.stderr)
sys.exit(1)
'''


def _write_script(path: Path, body: str, log: Optional[Path] = None) -> Path:
    path.write_text(body.replace("__LOG__", str(log or path.with_suffix(".log"))))
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture()
def engine_env(isolated_env: dict):
    db = Database(isolated_env["db_path"])
    providers = ProviderManager(db)
    providers.ensure_seeded()
    return {
        "db": db,
        "providers": providers,
        "tmp": isolated_env["tmp_path"],
        "db_path": isolated_env["db_path"],
    }


# --------------------------------------------------------------------------- CLI
def test_opencode_models_carry_real_provider_billing_and_execution(engine_env):
    log = engine_env["tmp"] / "opencode_calls.log"
    binary = _write_script(engine_env["tmp"] / "opencode", FAKE_OPENCODE, log)
    adapter = OpenCodeAdapter(engine_env["providers"], binary_override=str(binary))

    detection = adapter.detect()
    assert detection["installed"] is True
    assert detection["version"] == "0.4.2"

    auth = adapter.authenticated()
    assert auth["authenticated"] is True
    assert auth["account"] == "anthropic"  # derivato da `auth list`, senza leggere token

    models = {m.id: m for m in adapter.list_models()}
    assert {"anthropic/claude-sonnet-4", "openai/gpt-5.1"} <= set(models)

    claude = models["anthropic/claude-sonnet-4"]
    assert claude.provider == "anthropic"          # non "opencode"
    assert claude.auth_type == "oauth_subscription"
    assert claude.billing == BILLING_SUBSCRIPTION   # piano leggibile da `auth list`
    assert claude.subscription_name == "Claude Pro"
    assert claude.execution == EXEC_REMOTE
    assert claude.zero_cost is True and claude.cost_known is True

    chatgpt = models["openai/gpt-5.1"]
    assert chatgpt.provider == "openai" and chatgpt.execution == EXEC_REMOTE
    assert chatgpt.billing == BILLING_SUBSCRIPTION
    assert chatgpt.subscription_name == "ChatGPT Plus"

    local = models["lmstudio/qwen2.5-coder"]
    assert local.execution == EXEC_LOCAL
    assert local.billing in (BILLING_FREE_QUOTA, BILLING_UNKNOWN, "local_compute")
    # un modello locale non è mai "a pagamento"
    assert local.zero_cost is True


def test_opencode_generate_is_argv_only_and_never_shells(engine_env):
    log = engine_env["tmp"] / "opencode_calls.log"
    binary = _write_script(engine_env["tmp"] / "opencode", FAKE_OPENCODE, log)
    adapter = OpenCodeAdapter(engine_env["providers"], binary_override=str(binary))

    hostile = "Genera 4 concept; rm -rf ~/Desktop/KORA-STUDIO && echo $HOME `whoami` | tee /tmp/pwn"
    result = adapter.generate([{"role": "user", "content": hostile}], model="openai/gpt-5.1")
    assert result.text == "KORA OK"
    assert result.provider == "openai" and result.execution == EXEC_REMOTE
    assert result.billing == BILLING_SUBSCRIPTION
    assert result.cost_eur == 0.0  # abbonamento verificato dalla CLI

    calls = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    run_calls = [c for c in calls if c["argv"] and c["argv"][0] == "run"]
    assert run_calls, "il CLI non è mai stato eseguito"
    argv = run_calls[-1]["argv"]
    assert "--format" in argv and argv[argv.index("--format") + 1] == "json"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "openai/gpt-5.1"
    assert "--agent" in argv and argv[argv.index("--agent") + 1] == "kora"
    # il prompt ostile arriva come UNICO argomento: nessuna interpretazione di shell
    assert argv[-1].endswith(hostile)
    assert [a for a in argv if "rm -rf" in a] == [argv[-1]]
    workspace = Path(run_calls[-1]["cwd"])
    assert workspace.name == "ai_engine_workspace"
    agent_config = json.loads((workspace / "opencode.json").read_text())
    perms = agent_config["agent"]["kora"]["permission"]
    assert perms["bash"] == "deny" and perms["edit"] == "deny" and perms["webfetch"] == "deny"


def test_codex_installed_but_not_authenticated_stays_non_operational(engine_env):
    binary = _write_script(engine_env["tmp"] / "codex", FAKE_CODEX_LOGGED_OUT)
    adapter = CodexAdapter(engine_env["providers"], binary_override=str(binary))
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    manager._adapters["codex"] = adapter

    probe = manager.probe("codex", refresh=True)
    assert probe["installed"] is True
    assert probe["authenticated"] is False
    assert probe["state"] == "installato"
    assert probe["state"] != "operativo"


def test_missing_cli_is_reported_unavailable_not_operational(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    manager._adapters["opencode"] = OpenCodeAdapter(
        engine_env["providers"], binary_override=str(engine_env["tmp"] / "nope-opencode")
    )
    probe = manager.probe("opencode", refresh=True)
    assert probe["state"] == "indisponibile"
    assert probe["authenticated"] is False


# ----------------------------------------------------------------- fake HTTP-ish
class FakeAdapter(AIProviderAdapter):
    """Deterministic adapter standing in for a remote/API engine (SIMULATED)."""

    requires_key = False
    requires_paid_authorization = False
    supports_attachments = True
    attachment_mode = "inline_base64"

    def __init__(
        self,
        kind: str,
        models: list[ModelInfo],
        *,
        text: str = "KORA OK",
        fail: Optional[Exception] = None,
        images_seen: bool = True,
    ):
        super().__init__()
        self.kind = kind
        self.label = kind
        self._models = models
        self._text = text
        self._fail = fail
        self._images_seen = images_seen
        self.calls: list[dict[str, Any]] = []

    def detect(self):
        return {"installed": True, "version": "test", "binary": None, "detail": "adattatore simulato"}

    def credentials_present(self):
        return True

    def authenticated(self):
        return {"authenticated": True, "detail": "simulato", "account": "test-account"}

    def list_models(self):
        return list(self._models)

    def generate(self, messages, model=None, timeout=60.0, json_mode=False, attachments=None, cancel=None):
        if cancel and cancel():
            from kora_studio.ai_engine.base import EngineCancelled

            raise EngineCancelled(f"{self.kind}: annullata")
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "attachments": [str(p) for p in (attachments or [])],
            }
        )
        if self._fail is not None:
            raise self._fail
        if attachments and self._images_seen:
            text = "KORA OK · 47 · sfondo verde acqua · triangolo rosso"
        else:
            text = self._text
        return EngineResult(text=text, provider=self.kind, model=model or "auto", cost_eur=0.0)


def _model(
    model_id: str,
    *,
    vision: Optional[bool] = False,
    verified: bool = False,
    cost: Optional[float] = 0.0,
    billing: str = BILLING_FREE_QUOTA,
    execution: str = EXEC_REMOTE,
    provider: str = "",
) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        label=model_id,
        provider=provider or model_id.split("/")[0] if "/" in model_id else provider,
        text=True,
        vision=vision,
        vision_verified=verified,
        cost_per_1k_eur=cost,
        billing=billing,
        execution=execution,
    )


VISION_MODEL = _model("gemini-2.5-pro", vision=True, verified=True)
TEXT_MODEL = _model("gpt-4.1-mini", vision=False, verified=False)
PAID_MODEL = _model("gpt-5-paid", vision=True, verified=True, cost=0.02, billing=BILLING_PAID_API)


# ---------------------------------------------------------------------- routing
def test_platform_works_without_ollama_and_with_custom_endpoint(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    manager._adapters["ollama_local"] = AIProviderAdapter()  # nessun Ollama installato
    custom = FakeAdapter("custom_openai", [VISION_MODEL])
    manager._adapters["custom_openai"] = custom
    manager.update_settings({"primary_provider": "custom_openai", "strategy": "local_only"})

    # local_only esclude i motori remoti: nessun candidato autorizzato ⇒ errore esplicito
    with pytest.raises(EngineError):
        manager.call("copywriter", [{"role": "user", "content": "Scrivi una caption"}])
    board = manager.status_board(refresh=True)
    ollama = next(p for p in board["providers"] if p["provider"] == "ollama_local")
    assert ollama["state"] == "indisponibile"
    assert board["ollama_required"] is False


def test_local_endpoint_works_under_local_only(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    local = FakeAdapter("custom_openai", [_model("qwen2.5-vl-7b", vision=True, verified=True, execution=EXEC_LOCAL)])
    manager._adapters["custom_openai"] = local
    manager.update_settings({"primary_provider": "custom_openai", "strategy": "local_only"})

    result = manager.call("copywriter", [{"role": "user", "content": "Scrivi una caption"}])
    assert result.provider == "custom_openai"
    assert result.local is True
    assert len(local.calls) == 1


def test_provider_switch_takes_effect_without_restart(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    gemini = FakeAdapter("gemini_api", [VISION_MODEL])
    openai = FakeAdapter("openai_api", [_model("gpt-5.1", vision=True, verified=True)])
    manager._adapters["gemini_api"] = gemini
    manager._adapters["openai_api"] = openai

    manager.update_settings({"primary_provider": "gemini_api"})
    first = manager.call("creative_director", [{"role": "user", "content": "4 concept"}])
    assert first.provider == "gemini_api" and not openai.calls

    manager.update_settings({"primary_provider": "openai_api"})  # nessun riavvio
    second = manager.call("creative_director", [{"role": "user", "content": "4 concept"}])
    assert second.provider == "openai_api" and len(openai.calls) == 1


def test_zero_budget_blocks_paid_and_unknown_cost_models(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    paid = FakeAdapter("openai_api", [PAID_MODEL])
    unknown = FakeAdapter("openrouter", [_model("mystery/model", cost=None, billing=BILLING_UNKNOWN)])
    free = FakeAdapter("custom_openai", [VISION_MODEL])
    manager._adapters.update({"openai_api": paid, "openrouter": unknown, "custom_openai": free})
    manager.update_settings(
        {
            "primary_provider": "openai_api",
            "max_budget_eur": 0.0,
            "allow_paid_calls": False,
            "allow_unknown_costs": False,
            "strategy": "auto",
        }
    )

    result = manager.call("copywriter", [{"role": "user", "content": "ciao"}])
    assert paid.calls == [], "budget 0: nessuna chiamata a pagamento"
    assert unknown.calls == [], "costo sconosciuto: bloccato senza autorizzazione specifica"
    assert result.provider == "custom_openai"


def test_all_unknown_costs_with_budget_zero_raise(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    unknown = FakeAdapter("openrouter", [_model("mystery/model", cost=None, billing=BILLING_UNKNOWN)])
    manager._adapters["openrouter"] = unknown
    manager.update_settings({"primary_provider": "openrouter", "max_budget_eur": 0.0, "allow_paid_calls": False})

    with pytest.raises(EngineError) as exc:
        manager.call("copywriter", [{"role": "user", "content": "ciao"}])
    assert "costo sconosciuto" in str(exc.value).lower() or "budget" in str(exc.value).lower()
    assert unknown.calls == []


def test_fallback_after_provider_error_is_declared(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    broken = FakeAdapter("gemini_api", [VISION_MODEL], fail=EngineError("gemini_api: quota esaurita"))
    backup = FakeAdapter("custom_openai", [VISION_MODEL])
    manager._adapters["gemini_api"] = broken
    manager._adapters["custom_openai"] = backup
    manager.update_settings({"primary_provider": "gemini_api", "strategy": "auto", "fallback": "free_only"})

    result = manager.call("brand_guardian", [{"role": "user", "content": "verifica"}])
    assert result.provider == "custom_openai"
    assert result.fallback_used is True
    assert result.raw["fallback_from"] == "gemini_api"
    assert result.raw["attempts"] and "gemini_api" in result.raw["attempts"][0]


def test_vision_requires_verified_capability_not_a_name(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    # nome che *sembra* multimodale, ma capacità non verificata
    unverified = FakeAdapter("openai_api", [_model("gpt-4o-vision-lookalike", vision=True, verified=False)])
    manager._adapters["openai_api"] = unverified
    manager.update_settings({"primary_provider": "openai_api", "strategy": "auto"})

    for agent_id in VISION_AGENTS:
        assert manager.candidate_plan(agent_id) == []
        assert manager.resolve(agent_id) is None
    # un agente testuale può usare lo stesso provider
    assert manager.resolve("copywriter") is not None


def test_vision_call_sends_real_attachment_and_verification_persists(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    fake = FakeAdapter("gemini_api", [_model("gemini-2.5-flash", vision=True, verified=False)])
    manager._adapters["gemini_api"] = fake
    manager.update_settings({"primary_provider": "gemini_api", "strategy": "auto"})

    from kora_studio.ai_engine.vision_probe import build_probe_image

    probe = build_probe_image(engine_env["tmp"] / "probe.png")
    result = manager.vision_probe("gemini_api", model="gemini-2.5-flash")
    assert result["ok"] is True
    assert result["vision_verified"] is True, "il modello deve riconoscere il contenuto noto"
    assert fake.calls[-1]["attachments"], "l'immagine deve essere stata realmente allegata"

    # l'invalidazione dopo il test scarta l'adattatore: lo si reinietta (in produzione
    # viene ricostruito dal provider manager, qui è un finto)
    manager._adapters["gemini_api"] = fake
    # la verifica è persistita: ora il modello è usabile per i compiti visivi
    models = {m["id"]: m for m in manager.models("gemini_api")}
    assert models["gemini-2.5-flash"]["vision_verified"] is True
    plan = manager.candidate_plan("visual_intelligence")
    assert any(e["model"] == "gemini-2.5-flash" for e in plan)
    assert probe["expected"] == "47"


def test_text_only_reply_to_an_image_is_not_verified(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    blind = FakeAdapter("gemini_api", [_model("gemini-2.5-flash", vision=True, verified=False)], images_seen=False)
    blind._text = "Non posso vedere immagini."
    manager._adapters["gemini_api"] = blind

    result = manager.vision_probe("gemini_api", model="gemini-2.5-flash")
    assert result["ok"] is True
    assert result["vision_verified"] is False
    assert "non ha riconosciuto" in result["detail"].lower()
    assert manager.candidate_plan("visual_intelligence") == []


def test_agent_configuration_inherit_and_validation(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    assert len(AGENTS_BY_ID) == 9
    conf = manager.agent_configuration("voice_director")
    assert conf["mode"] == "inherit"

    manager.update_settings(
        {"agents": {"voice_director": {"mode": "provider", "provider": "gemini_api", "model": "gemini-flash"}}}
    )
    conf = manager.agent_configuration("voice_director")
    assert conf["mode"] == "provider" and conf["provider"] == "gemini_api"

    with pytest.raises(ValueError):
        manager.update_settings({"agents": {"voice_director": {"mode": "provider", "provider": "skynet"}}})
    with pytest.raises(ValueError):
        manager.update_settings({"agents": {"not_an_agent": {"mode": "inherit"}}})
    with pytest.raises(ValueError):
        manager.update_settings({"strategy": "whatever"})
    with pytest.raises(ValueError):
        manager.update_settings({"max_budget_eur": 99})


def test_strategy_local_only_excludes_cloud_and_remote_cli(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    manager._adapters["gemini_api"] = FakeAdapter("gemini_api", [VISION_MODEL])
    manager._adapters["custom_openai"] = FakeAdapter(
        "custom_openai", [_model("qwen2.5-vl-7b", execution=EXEC_LOCAL)]
    )
    manager.update_settings({"strategy": "local_only", "primary_provider": "auto"})
    plan = manager.candidate_plan("creative_director")
    assert plan, "un endpoint locale deve restare disponibile"
    assert all(entry["execution"] == EXEC_LOCAL for entry in plan)
    assert all(entry["provider"] not in {"gemini_api", "openai_api", "openrouter", "claude_api", "deepseek_api"} for entry in plan)


def test_declared_subscription_makes_cli_plan_verifiable(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    cli_model = ModelInfo(
        id="gpt-5.1",
        label="gpt-5.1",
        provider="openai",
        source="opencode",
        auth_type="cli_session",
        billing=BILLING_UNKNOWN,
        execution=EXEC_REMOTE,
        cost_per_1k_eur=None,
        text=True,
    )
    manager._adapters["opencode"] = FakeAdapter("opencode", [cli_model])
    manager.update_settings({"primary_provider": "opencode", "fallback": "off"})

    # costo sconosciuto a budget 0 → nessun candidato autorizzato
    assert manager.resolve("copywriter") is None

    manager.update_settings({"declared_subscriptions": {"opencode": "ChatGPT Plus"}})
    entry = manager.resolve("copywriter")
    assert entry is not None
    assert entry["billing"] == BILLING_SUBSCRIPTION
    assert entry["zero_cost"] is True


# ------------------------------------------------------------------- API + UI
def _build_director(engine_env) -> KoraDirector:
    return KoraDirector(engine_env["db"])


def test_status_board_api_distinguishes_states_and_hides_secrets(engine_env, monkeypatch):
    monkeypatch.setattr(AIEngineManager, "adapter", lambda self, pid: _API_FAKE.get(pid))
    secret = "sk-test-SUPERSECRET-1234567890"
    engine_env["providers"].set_api_key("openai_api", secret)

    app = create_app(db_path=engine_env["db_path"], auto_seed=False)
    client = TestClient(app)

    assert client.get("/api/ai-engine/status", headers={"host": "evil.example.com"}).status_code == 403

    payload = client.get("/api/ai-engine/status").json()
    assert set(payload["providers"][0]) >= {"provider", "state", "installed", "authenticated", "detail"}
    states = {p["provider"]: p["state"] for p in payload["providers"]}
    assert states
    body = json.dumps(payload)
    assert secret not in body
    assert len(payload["agents"]) == 9
    assert payload["fallback_modes"] == ["off", "free_only", "authorized"]


_API_FAKE: dict[str, FakeAdapter] = {}


def test_api_test_then_operational_state_only_after_real_generation(engine_env, monkeypatch):
    director = _build_director(engine_env)
    fake = FakeAdapter("gemini_api", [_model("gemini-flash", vision=True, verified=True)], text="KORA OK")
    fakes = {"gemini_api": fake, "ollama_local": AIProviderAdapter()}
    monkeypatch.setattr(AIEngineManager, "adapter", lambda self, pid: fakes.get(pid))

    app = create_app(db_path=engine_env["db_path"], auto_seed=False)
    client = TestClient(app)

    r_text = client.post("/api/ai-engine/test", json={"provider": "gemini_api"})
    assert r_text.json()["ok"] is True
    board = client.get("/api/ai-engine/status").json()
    gemini = next(p for p in board["providers"] if p["provider"] == "gemini_api")
    assert gemini["state"] == "operativo"
    assert gemini["verified_operational"] is True
    assert gemini["last_test_at"]

    r_vision = client.post("/api/ai-engine/vision-test", json={"provider": "gemini_api", "model": "gemini-flash"})
    body = r_vision.json()
    assert body["ok"] is True and body["vision_verified"] is True
    assert fake.calls[-1]["attachments"], "l'immagine nota deve essere inviata al modello"

    activity = client.get("/api/ai-engine/activity").json()
    assert activity["summary"]["calls"] >= 1
    assert all("prompt" not in row for row in activity["calls"])


def test_api_settings_patch_validates_and_agents_endpoint(engine_env):
    _build_director(engine_env)
    app = create_app(db_path=engine_env["db_path"], auto_seed=False)
    client = TestClient(app)

    r = client.put("/api/ai-engine/settings", json={"strategy": "min_cost", "max_budget_eur": 0, "allow_paid_calls": False})
    assert r.status_code == 200
    assert r.json()["strategy"] == "min_cost"

    assert client.put("/api/ai-engine/settings", json={"strategy": "boom"}).status_code == 400
    assert client.put("/api/ai-engine/settings", json={"max_budget_eur": 500}).status_code == 400
    assert client.put("/api/ai-engine/settings", json={"fallback": "sempre"}).status_code == 400

    r_agent = client.put("/api/ai-engine/agent/copywriter", json={"mode": "provider", "provider": "custom_openai", "model": "qwen"})
    assert r_agent.status_code == 200 and r_agent.json()["agent"]["provider"] == "custom_openai"
    assert client.put("/api/ai-engine/agent/unknown", json={"mode": "inherit"}).status_code == 400


def test_creative_director_uses_engine_manager_when_available(engine_env):
    director = _build_director(engine_env)
    manager = director.ai_engine
    manager._adapters["gemini_api"] = FakeAdapter(
        "gemini_api",
        [_model("gemini-flash", vision=True, verified=True)],
        text=json.dumps(
            {
                "concepts": [
                    {"title": "Alba sul pontile", "description": "Onda lenta, luce rosata.", "preset": "cinematic"},
                    {"title": "Lino e salsedine", "description": "Dettagli tessili, brezza.", "preset": "lifestyle"},
                    {"title": "Aperitivo al tramonto", "description": "Bicchieri controluce.", "preset": "cinematic"},
                    {"title": "Tavola condivisa", "description": "Cucina mediterranea.", "preset": "food"},
                ]
            }
        ),
    )
    manager.update_settings({"primary_provider": "gemini_api", "strategy": "auto"})

    from kora_studio.db.models import CreativeRequestPayload

    req = CreativeRequestPayload(theme="Estate 2026 a Policoro", duration_sec=20)
    concepts, engine_error = director.creative._call_engine_manager(req, 4, set())
    assert engine_error is None
    assert concepts and len(concepts) == 4
    assert manager._adapters["gemini_api"].calls, "il Creative Director deve passare dal motore configurato"
    sent = json.dumps(manager._adapters["gemini_api"].calls[-1]["messages"])
    assert "kora" in sent.lower()
    assert all(c.generation_engine == "gemini_api" for c in concepts)


def test_every_engine_receives_the_same_versioned_kora_profile(engine_env):
    director = _build_director(engine_env)
    fake = FakeAdapter("gemini_api", [VISION_MODEL])
    director.ai_engine._adapters["gemini_api"] = fake
    director.ai_engine.update_settings({"primary_provider": "gemini_api"})

    director.ai_engine.call("copywriter", [{"role": "user", "content": "Scrivi una caption"}])
    sent = fake.calls[-1]["messages"]
    assert sent[0]["role"] == "system"
    block = sent[0]["content"]
    assert "CONTESTO UFFICIALE KORA STUDIO" in block
    assert "The Art of Being" in block
    assert "kora_studio.database" in block
    assert "authenticity_rules" in block

    other = FakeAdapter("custom_openai", [VISION_MODEL])
    director.ai_engine.invalidate("custom_openai")
    director.ai_engine._adapters["custom_openai"] = other
    director.ai_engine.update_settings({"primary_provider": "custom_openai"})
    director.ai_engine.call(
        "quality_guardian",
        [{"role": "system", "content": "Ruolo QA"}, {"role": "user", "content": "Verifica"}],
    )
    sent2 = other.calls[-1]["messages"]
    assert sent2[0]["content"].startswith("CONTESTO UFFICIALE KORA STUDIO")
    assert sent2[1]["content"] == "Ruolo QA"
    assert "The Art of Being" in sent2[0]["content"]
