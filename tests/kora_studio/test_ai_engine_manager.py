"""KORA STUDIO — AI Engine Manager acceptance tests.

Covers the real requirements of the engine manager:
  * OpenCode/Codex detection + JSON CLI runs with argv-only invocation
    (no shell, no token extraction, injection-proof prompt handling);
  * Ollama is optional and the whole platform works without it;
  * provider switch without restarting the app;
  * zero-budget respect (no silent paid calls) and authorized fallback;
  * vision agents never routed to text-only models;
  * per-agent configuration with inherit/provider modes;
  * UI states installato / autenticato / operativo / indisponibile are earned,
    never guessed, and secrets never reach the payload.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kora_studio.agents.director import KoraDirector
from kora_studio.ai_engine.adapters_cli import CodexAdapter, OpenCodeAdapter
from kora_studio.ai_engine.agents import AGENTS_BY_ID, VISION_AGENTS
from kora_studio.ai_engine.base import AIProviderAdapter, EngineError, EngineResult, ModelInfo
from kora_studio.ai_engine.manager import AIEngineManager
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
    print("opencode run [prompt]\n  --format <fmt>  output json\n  --model <m>\n  --agent <name>")
    sys.exit(0)
if argv[:2] == ["auth", "list"]:
    print("• anthropic (Claude Pro)\n• openai (ChatGPT Plus)")
    sys.exit(0)
if argv[:1] == ["models"]:
    print("anthropic/claude-sonnet-4\nopenai/gpt-5.1\nlocal/qwen2.5-coder")
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
def test_opencode_detected_authenticated_and_json_run_is_injection_safe(engine_env, monkeypatch):
    log = engine_env["tmp"] / "opencode_calls.log"
    binary = _write_script(engine_env["tmp"] / "opencode", FAKE_OPENCODE, log)
    adapter = OpenCodeAdapter(engine_env["providers"], binary_override=str(binary))

    detection = adapter.detect()
    assert detection["installed"] is True
    assert detection["version"] == "0.4.2"

    auth = adapter.authenticated()
    assert auth["authenticated"] is True
    assert auth["account"] == "anthropic"  # derivato da `auth list`, senza leggere token

    models = adapter.list_models()
    ids = {m.id for m in models}
    assert {"anthropic/claude-sonnet-4", "openai/gpt-5.1"} <= ids
    assert all(m.subscription for m in models)  # quota già inclusa nell'abbonamento

    hostile = "Genera 4 concept; rm -rf ~/Desktop/KORA-STUDIO && echo $HOME `whoami` | tee /tmp/pwn"
    result = adapter.generate([{"role": "user", "content": hostile}], model="openai/gpt-5.1")
    assert result.text == "KORA OK"
    assert result.cost_eur == 0.0 and result.subscription is True

    calls = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    runs = [c["argv"] for c in calls if c["argv"] and c["argv"][0] == "run"]
    assert runs, "il CLI non è mai stato eseguito"
    argv = runs[-1]
    assert "--format" in argv and argv[argv.index("--format") + 1] == "json"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "openai/gpt-5.1"
    # agente dedicato KORA con permessi minimi
    assert "--agent" in argv and argv[argv.index("--agent") + 1] == "kora"
    # il prompt ostile arriva come UNICO argomento: nessuna interpretazione di shell
    assert argv[-1].endswith(hostile)
    assert [a for a in argv if "rm -rf" in a] == [argv[-1]]
    # cwd controllata: mai la home dell'utente
    workspace = Path(calls[-1]["cwd"])
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
    # mai "operativo" senza una generazione reale riuscita
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
    """Deterministic adapter standing in for a remote/API engine."""

    requires_key = False
    requires_paid_authorization = False

    def __init__(self, kind: str, models: list[ModelInfo], *, text: str = "KORA OK", fail: bool = False):
        super().__init__()
        self.kind = kind
        self.label = kind
        self._models = models
        self._text = text
        self._fail = fail
        self.calls: list[dict[str, Any]] = []

    def detect(self):
        return {"installed": True, "version": "test", "binary": None, "detail": "test"}

    def credentials_present(self):
        return True

    def authenticated(self):
        return {"authenticated": True, "detail": "test", "account": "test-account"}

    def list_models(self):
        return list(self._models)

    def generate(self, messages, model=None, timeout=60.0, json_mode=False):
        self.calls.append({"model": model, "messages": messages})
        if self._fail:
            raise EngineError(f"{self.kind}: quota esaurita")
        return EngineResult(text=self._text, provider=self.kind, model=model or "auto", cost_eur=0.0)


def _vision_model(model_id: str = "gemini-2.5-pro") -> ModelInfo:
    return ModelInfo(id=model_id, label=model_id, text=True, vision=True, cost_per_1k_eur=0.0)


def _text_only_model(model_id: str = "gpt-4.1-mini") -> ModelInfo:
    return ModelInfo(id=model_id, label=model_id, text=True, vision=False, cost_per_1k_eur=0.0)


def _paid_model(model_id: str = "gpt-5-paid") -> ModelInfo:
    return ModelInfo(id=model_id, label=model_id, text=True, vision=True, cost_per_1k_eur=0.02)


# ---------------------------------------------------------------------- routing
def test_platform_works_without_ollama_and_with_custom_endpoint(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    manager._adapters["ollama_local"] = AIProviderAdapter()  # nessun Ollama installato
    custom = FakeAdapter("custom_openai", [_vision_model("qwen2.5-vl-7b")])
    manager._adapters["custom_openai"] = custom
    manager.update_settings({"primary_provider": "custom_openai", "strategy": "local_only"})

    result = manager.call("copywriter", [{"role": "user", "content": "Scrivi una caption"}])
    assert result.provider == "custom_openai"
    assert len(custom.calls) == 1
    board = manager.status_board(refresh=True)
    ollama = next(p for p in board["providers"] if p["provider"] == "ollama_local")
    assert ollama["state"] == "indisponibile"
    assert board["ollama_required"] is False


def test_provider_switch_takes_effect_without_restart(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    gemini = FakeAdapter("gemini_api", [_vision_model("gemini-flash")])
    openai = FakeAdapter("openai_api", [_vision_model("gpt-5.1")])
    manager._adapters["gemini_api"] = gemini
    manager._adapters["openai_api"] = openai

    manager.update_settings({"primary_provider": "gemini_api"})
    first = manager.call("creative_director", [{"role": "user", "content": "4 concept"}])
    assert first.provider == "gemini_api" and not openai.calls

    manager.update_settings({"primary_provider": "openai_api"})  # nessun riavvio
    second = manager.call("creative_director", [{"role": "user", "content": "4 concept"}])
    assert second.provider == "openai_api" and len(openai.calls) == 1


def test_zero_budget_blocks_paid_call_and_authorized_fallback_works(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    paid = FakeAdapter("openai_api", [_paid_model()])
    local = FakeAdapter("custom_openai", [_vision_model("qwen2.5-vl-7b")])
    manager._adapters["openai_api"] = paid
    manager._adapters["custom_openai"] = local
    manager.update_settings(
        {
            "primary_provider": "openai_api",
            "max_budget_eur": 0.0,
            "allow_paid_calls": False,
            "strategy": "auto",
            "agents": {"copywriter": {"mode": "inherit"}},
        }
    )

    result = manager.call("copywriter", [{"role": "user", "content": "ciao"}])
    # la chiamata a pagamento non viene mai eseguita: si usa il motore a costo zero
    assert paid.calls == []
    assert result.provider == "custom_openai"


def test_all_paid_without_authorization_raises_instead_of_spending(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    paid = FakeAdapter("openai_api", [_paid_model()])
    manager._adapters["openai_api"] = paid
    manager.update_settings({"primary_provider": "openai_api", "max_budget_eur": 0.0, "allow_paid_calls": False})

    with pytest.raises(EngineError) as exc:
        manager.call("copywriter", [{"role": "user", "content": "ciao"}])
    assert "non autorizzata" in str(exc.value)
    assert paid.calls == []


def test_fallback_after_provider_error_is_declared(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    broken = FakeAdapter("gemini_api", [_vision_model("gemini-flash")], fail=True)
    backup = FakeAdapter("custom_openai", [_vision_model("qwen2.5-vl-7b")])
    manager._adapters["gemini_api"] = broken
    manager._adapters["custom_openai"] = backup
    manager.update_settings({"primary_provider": "gemini_api", "strategy": "auto"})

    result = manager.call("brand_guardian", [{"role": "user", "content": "verifica"}])
    assert result.provider == "custom_openai"
    assert result.raw["attempts"] and "gemini_api" in result.raw["attempts"][0]


def test_vision_agents_never_get_text_only_models(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    text_only = FakeAdapter("openai_api", [_text_only_model()])
    manager._adapters["openai_api"] = text_only
    manager.update_settings({"primary_provider": "openai_api", "strategy": "auto"})

    for agent_id in VISION_AGENTS:
        plan = manager.candidate_plan(agent_id)
        assert plan == [], f"{agent_id} non deve ricevere modelli senza visione"
        assert manager.resolve(agent_id) is None

    # un agente testuale può usare lo stesso provider
    assert manager.resolve("copywriter") is not None

    manager.update_settings({"agents": {"visual_intelligence": {"mode": "provider", "provider": "openai_api", "model": "gpt-4.1-mini"}}})
    assert manager.candidate_plan("visual_intelligence") == []


def test_agent_configuration_inherit_and_validation(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    assert len(AGENTS_BY_ID) == 9
    conf = manager.agent_configuration("voice_director")
    assert conf["mode"] == "inherit"

    manager.update_settings({"agents": {"voice_director": {"mode": "provider", "provider": "gemini_api", "model": "gemini-flash"}}})
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


def test_strategy_local_only_excludes_cloud_providers(engine_env):
    manager = AIEngineManager(engine_env["providers"], engine_env["db"])
    manager._adapters["gemini_api"] = FakeAdapter("gemini_api", [_vision_model("gemini-flash")])
    manager.update_settings({"strategy": "local_only", "primary_provider": "auto"})
    plan = manager.candidate_plan("creative_director")
    assert all(entry["provider"] not in {"gemini_api", "openai_api", "openrouter", "claude_api", "deepseek_api"} for entry in plan)


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
    assert states  # ogni provider espone uno stato esplicito
    body = json.dumps(payload)
    assert secret not in body
    assert len(payload["agents"]) == 9


_API_FAKE: dict[str, FakeAdapter] = {}


def test_api_test_then_operational_state_only_after_real_generation(engine_env, monkeypatch):
    director = _build_director(engine_env)
    fake = FakeAdapter("gemini_api", [_vision_model("gemini-flash")], text="KORA OK")
    fakes = {"gemini_api": fake, "ollama_local": AIProviderAdapter()}
    monkeypatch.setattr(AIEngineManager, "adapter", lambda self, pid: fakes.get(pid))

    app = create_app(db_path=engine_env["db_path"], auto_seed=False)
    client = TestClient(app)

    photo = engine_env["tmp"] / "test_vision.jpg"
    Image.new("RGB", (1080, 1920), (30, 90, 140)).save(photo, "JPEG")
    asset = director.vault.ingest_file(photo, origin="kora_test", authenticity_status="autentico", usage_rights="autorizzato_kora")

    r = client.post("/api/ai-engine/test", json={"provider": "gemini_api", "multimodal": True, "asset_id": asset["id"]})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True and body["multimodal"] is True
    sent = fake.calls[-1]["messages"]
    assert isinstance(sent[-1]["content"], list), "il test multimodale deve inviare l'immagine"
    assert any(part.get("type") == "image_url" for part in sent[-1]["content"])

    r_text = client.post("/api/ai-engine/test", json={"provider": "gemini_api"})
    assert r_text.json()["ok"] is True

    board = client.get("/api/ai-engine/status").json()
    gemini = next(p for p in board["providers"] if p["provider"] == "gemini_api")
    assert gemini["state"] == "operativo"
    assert gemini["verified_operational"] is True
    assert fake.calls, "lo stato operativo richiede una generazione realmente avvenuta"


def test_api_settings_patch_validates_and_agents_endpoint(engine_env):
    director = _build_director(engine_env)
    app = create_app(db_path=engine_env["db_path"], auto_seed=False)
    client = TestClient(app)

    r = client.put("/api/ai-engine/settings", json={"strategy": "min_cost", "max_budget_eur": 0, "allow_paid_calls": False})
    assert r.status_code == 200
    assert r.json()["strategy"] == "min_cost"

    assert client.put("/api/ai-engine/settings", json={"strategy": "boom"}).status_code == 400
    assert client.put("/api/ai-engine/settings", json={"max_budget_eur": 500}).status_code == 400

    r_agent = client.put("/api/ai-engine/agent/copywriter", json={"mode": "provider", "provider": "custom_openai", "model": "qwen"})
    assert r_agent.status_code == 200 and r_agent.json()["agent"]["provider"] == "custom_openai"
    assert client.put("/api/ai-engine/agent/unknown", json={"mode": "inherit"}).status_code == 400


def test_creative_director_uses_engine_manager_when_available(engine_env):
    director = _build_director(engine_env)
    manager = director.ai_engine
    manager._adapters["gemini_api"] = FakeAdapter(
        "gemini_api",
        [_vision_model("gemini-flash")],
        text=json.dumps(
            {
                "concepts": [
                    {"title": "Alba sul pontile", "description": "Onda lenta, luce rosata, nessun testo sovrapposto.", "preset": "cinematic"},
                    {"title": "Lino e salsedine", "description": "Dettagli tessili, brezza, ritmo lento.", "preset": "lifestyle"},
                    {"title": "Aperitivo al tramonto", "description": "Bicchieri controluce, ospiti rilassati.", "preset": "cinematic"},
                    {"title": "Tavola condivisa", "description": "Cucina mediterranea, gesti semplici.", "preset": "food"},
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
    """Requisito 6: nessuna memoria esterna — il profilo KORA versionato arriva a ogni chiamata."""
    director = _build_director(engine_env)
    fake = FakeAdapter("gemini_api", [_vision_model("gemini-flash")])
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

    # lo stesso blocco viene inviato a un agente diverso e a un motore diverso
    other = FakeAdapter("custom_openai", [_vision_model("qwen2.5-vl-7b")])
    director.ai_engine.invalidate("custom_openai")  # scarta il probe reale fatto prima dell'iniezione
    director.ai_engine._adapters["custom_openai"] = other
    director.ai_engine.update_settings({"primary_provider": "custom_openai"})
    director.ai_engine.call("quality_guardian", [{"role": "system", "content": "Ruolo QA"}, {"role": "user", "content": "Verifica"}])
    sent2 = other.calls[-1]["messages"]
    assert sent2[0]["content"].startswith("CONTESTO UFFICIALE KORA STUDIO")
    assert sent2[1]["content"] == "Ruolo QA"
    assert "The Art of Being" in sent2[0]["content"]
