"""v3.6 — errori del provider, fail-fast e Model Manager.

Nessun test qui simula un successo di OpenCode: dove il CLI non esiste
(sandbox di collaudo) il risultato atteso è «non collaudato», non «ok».
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from kora_studio.ai_engine.base import (
    EngineCancelled,
    EngineEmptyResponse,
    EngineError,
    EngineModelRetired,
    EngineNetworkError,
    EngineTimeout,
    classify_error,
    provider_failure,
)
from kora_studio.ai_engine.model_manager import DEFAULT_CONFIG, ModelManager


# --------------------------------------------------------------------- errori
def test_retired_model_inside_stdout_is_recognised():
    text = (
        "The model 'nvidia/deepseek-ai/deepseek-v4.1-flash' has reached its "
        "end of life on 2026-11-16T08:00:00Z and is no longer available."
    )
    detected = provider_failure(text)
    assert detected is not None
    kind, detail = detected
    assert kind is EngineModelRetired
    assert "deepseek-v4.1-flash" in detail


def test_classify_retired_wins_over_generic_not_found():
    assert classify_error("model not found: this model is deprecated") is EngineModelRetired
    assert classify_error("model_not_found") is not None


def test_network_and_empty_markers():
    assert provider_failure("getaddrinfo ENOTFOUND api.opencode.ai")[0] is EngineNetworkError
    assert provider_failure("empty response from provider")[0] is EngineEmptyResponse
    assert provider_failure("Ecco il tuo Reel: ...") is None
    assert provider_failure("OK") is None


def test_retired_is_a_model_unavailable_for_routing():
    from kora_studio.ai_engine.base import EngineModelUnavailable

    assert issubclass(EngineModelRetired, EngineModelUnavailable)
    assert EngineModelRetired().kind == "modello_dismesso"


# ------------------------------------------------------------------ adapter
class _FakeDone:
    """Processo finto: esce subito con output controllato."""

    def __init__(self, out: str, err: str = "", code: int = 0) -> None:
        self._payload = (code, out, err)
        self.calls = 0

    def __call__(self, args, **kwargs):
        self.calls += 1
        return self._payload


def _adapter_with_fake_cli(monkeypatch, fake):
    import kora_studio.ai_engine.adapters_cli as mod

    adapter = mod.OpenCodeAdapter.__new__(mod.OpenCodeAdapter)
    adapter.parent = types.SimpleNamespace(get_api_key=lambda pid: None, key_for=lambda pid: None)
    monkeypatch.setattr(adapter, "binary_path", lambda: "/usr/local/bin/opencode")
    monkeypatch.setattr(adapter, "prepare_workspace", lambda: None)
    monkeypatch.setattr(adapter, "workspace", lambda: Path("/tmp"))
    monkeypatch.setattr(adapter, "supports_flag", lambda *a, **k: True)
    monkeypatch.setattr(mod, "run_cli", fake)
    adapter._auth_cache = None
    adapter.auth_integrations = lambda refresh=False: []
    adapter._provider_billing = lambda provider: (mod.BILLING_UNKNOWN, None, None)
    return adapter


def test_exit_zero_with_eol_text_raises_immediately(monkeypatch):
    fake = _FakeDone(
        "The model 'nvidia/deepseek-ai/deepseek-v4.1-flash' has reached its end of life and is no longer available."
    )
    adapter = _adapter_with_fake_cli(monkeypatch, fake)
    with pytest.raises(EngineModelRetired):
        adapter.generate(
            [{"role": "user", "content": "ciao"}],
            model="nvidia/deepseek-ai/deepseek-v4.1-flash",
            timeout=25,
        )
    assert fake.calls == 1


def test_exit_zero_with_empty_output_is_not_a_valid_answer(monkeypatch):
    adapter = _adapter_with_fake_cli(monkeypatch, _FakeDone("   \n  "))
    with pytest.raises(EngineEmptyResponse):
        adapter.generate([{"role": "user", "content": "ciao"}], model="opencode/hy4-flash-free")


def test_real_timeout_is_still_a_timeout(monkeypatch):
    import kora_studio.ai_engine.adapters_cli as mod

    def _boom(args, **kwargs):
        raise EngineTimeout("timeout dopo 25s")

    adapter = _adapter_with_fake_cli(monkeypatch, _boom)
    with pytest.raises(EngineTimeout):
        adapter.generate([{"role": "user", "content": "ciao"}], model="opencode/hy4-flash-free")


def test_valid_answer_passes_through(monkeypatch):
    adapter = _adapter_with_fake_cli(monkeypatch, _FakeDone("OK"))
    result = adapter.generate([{"role": "user", "content": "ciao"}], model="opencode/hy4-flash-free")
    assert result.text.strip() == "OK"


# ------------------------------------------------- motore: nessun falso fallback
def test_fast_entry_probes_when_snapshot_has_no_usable_model():
    """Uno snapshot senza modelli non deve diventare «motore assente»."""
    import time as _time

    from kora_studio.ai_engine.manager import AIEngineManager

    manager = AIEngineManager.__new__(AIEngineManager)
    manager._snapshot = {"providers": {"opencode": {"authenticated": True, "models": []}}}
    manager._snapshot_at = _time.time()
    calls = {"probe": 0}

    def _empty_plan(*args, **kwargs):
        return []

    def _real_plan(*args, **kwargs):
        calls["probe"] += 1
        return [{"provider": "opencode", "model": "opencode/hy4-flash-free"}]

    manager.fast_plan = _empty_plan  # type: ignore[assignment]
    manager.candidate_plan = _real_plan  # type: ignore[assignment]
    entry = manager.fast_entry("reel_designer")
    assert entry and entry["model"] == "opencode/hy4-flash-free"
    assert calls["probe"] == 1


# -------------------------------------------------------------- model manager
class _FakeDB:
    def __init__(self) -> None:
        self.store: dict = {}

    def get_preference(self, key, default=None):
        return self.store.get(key, default)

    def set_preference(self, key, value):
        self.store[key] = value


class _FakeAdapter:
    def __init__(self, models=None, answer="OK", error=None) -> None:
        self.models = models or ["opencode/hy4-flash-free", "opencode/mimo-v2.6-flash-free"]
        self.answer = answer
        self.error = error
        self.generated = []

    def detect(self):
        return {"installed": True, "binary": "/usr/local/bin/opencode", "version": "0.4.2"}

    def raw_model_ids(self):
        return list(self.models)

    def generate(self, messages, model=None, timeout=90.0, json_mode=False, attachments=None, cancel=None):
        if self.error:
            raise self.error
        from kora_studio.ai_engine.base import EngineResult

        self.generated.append({"model": model, "timeout": timeout})
        return EngineResult(text=self.answer, provider="opencode", model=model or "x", latency_ms=1)


def test_default_config_matches_the_measured_models():
    assert DEFAULT_CONFIG["roles"]["everyday"] == "opencode/hy4-flash-free"
    assert DEFAULT_CONFIG["roles"]["premium"] == "opencode/mimo-v2.6-flash-free"
    assert DEFAULT_CONFIG["roles"]["fallback"] == "opencode/nemotron-3.5-lightning-free"
    assert DEFAULT_CONFIG["timeouts"] == {"ideation": 45, "design": 90, "revision": 45, "premium": 150}


def test_config_update_is_persisted_and_clamped():
    db = _FakeDB()
    mm = ModelManager(db, lambda pid: _FakeAdapter())
    mm.update({"timeouts": {"ideation": 5000}, "mode": "premium", "policy": "boh"})
    cfg = mm.config()
    assert cfg["timeouts"]["ideation"] == 600
    assert cfg["mode"] == "premium"
    assert cfg["policy"] == "ask"


def test_models_cache_is_reused_until_expired():
    db = _FakeDB()
    adapter = _FakeAdapter()
    calls = {"n": 0}

    def raw():
        calls["n"] += 1
        return adapter.models

    adapter.raw_model_ids = raw
    mm = ModelManager(db, lambda pid: adapter)
    first = mm.search()
    second = mm.search()
    assert calls["n"] == 1
    assert first["total"] == 2
    assert second["cached"] is True


def test_search_filters_by_query_and_provider():
    mm = ModelManager(_FakeDB(), lambda pid: _FakeAdapter())
    assert [m["id"] for m in mm.search("mimo")["models"]] == ["opencode/mimo-v2.6-flash-free"]
    assert len(mm.search(provider="opencode")["models"]) == 2
    assert mm.search(provider="nvidia")["models"] == []


def test_quick_test_records_latency_and_passes_on_ok():
    adapter = _FakeAdapter(answer="OK")
    mm = ModelManager(_FakeDB(), lambda pid: adapter)
    record = mm.test_model("opencode/hy4-flash-free", kind="quick")
    assert record["ok"] is True
    assert record["latency_ms"] is not None
    assert adapter.generated[0]["model"] == "opencode/hy4-flash-free"


def test_quick_test_fails_when_answer_is_not_ok():
    mm = ModelManager(_FakeDB(), lambda pid: _FakeAdapter(answer="Certo, ecco un tema..."))
    record = mm.test_model("opencode/hy4-flash-free", kind="quick")
    assert record["ok"] is False
    assert "OK" in record["notes"]


def test_test_records_error_classification():
    error = EngineModelRetired("modello dismesso")
    mm = ModelManager(_FakeDB(), lambda pid: _FakeAdapter(error=error))
    record = mm.test_model("nvidia/deepseek-ai/deepseek-v4.1-flash", kind="quick")
    assert record["ok"] is False
    assert record["error_kind"] == "modello_dismesso"


def test_creative_test_requires_exactly_four_themes():
    payload = '{"themes": [{"title": "a"}, {"title": "b"}, {"title": "c"}]}'
    mm = ModelManager(_FakeDB(), lambda pid: _FakeAdapter(answer=payload))
    record = mm.test_model("opencode/hy4-flash-free", kind="creative")
    assert record["ok"] is False
    assert record["themes"] == 3


def test_benchmark_runs_only_configured_models_and_never_automatic():
    db = _FakeDB()
    adapter = _FakeAdapter()
    mm = ModelManager(db, lambda pid: adapter)
    result = mm.benchmark(kind="quick")
    assert result["models"] == [
        "opencode/hy4-flash-free",
        "opencode/mimo-v2.6-flash-free",
        "opencode/nemotron-3.5-lightning-free",
    ]
    assert len(result["results"]) == 3
    assert db.get_preference("ai_model_tests")


def test_missing_cli_is_reported_not_faked():
    class _NoCli(_FakeAdapter):
        def detect(self):
            return {"installed": False, "detail": "opencode non trovato nel PATH"}

    mm = ModelManager(_FakeDB(), lambda pid: _NoCli())
    found = mm.search()
    assert found["total"] == 0
    assert "non trovato" in found["error"]
    diag = mm.diagnostics()
    assert diag["installed"] is False
