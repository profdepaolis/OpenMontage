"""KORA STUDIO 2.8.1 — Creative Director Performance (Fast Creative Mode).

Tiers are explicit:

* **[SIMULATED]** — fake adapters model OpenCode/Codex/HTTP providers without any
  network or CLI (``source="simulated"``), including hangs, timeouts, invalid JSON,
  missing auth and cancellation.
* **[INTEGRATION]** — real DB, real Vault, real AIEngineManager, real HTTP API
  inside an isolated temporary workspace.
* **[REAL PROVIDER]** — requires OpenCode/Codex/API accounts: not available in this
  workspace, so no timing here is presented as a real provider measurement.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pytest

from kora_studio.agents.director import KoraDirector
from kora_studio.ai_engine.base import (
    AIProviderAdapter,
    EngineCancelled,
    EngineModelUnavailable,
    EngineQuotaExhausted,
    EngineResult,
    EngineTimeout,
    ModelInfo,
)
from kora_studio.ai_engine.manager import AIEngineManager
from kora_studio.ai_engine.models import BILLING_FREE_QUOTA, EXEC_REMOTE
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload, ThemeProposalLite
from kora_studio.providers.registry import ProviderManager

PROPOSALS_JSON = json.dumps(
    {
        "proposals": [
            {
                "title": f"Tema simulato {i + 1}",
                "hook": f"Hook {i + 1}",
                "central_message": f"Messaggio centrale {i + 1}",
                "narrative_direction": f"Direzione narrativa {i + 1}: luce naturale, dettagli reali.",
                "recommended_objective": "Identità di marca",
                "recommended_voice_id": "brando",
                "recommended_duration_sec": 20,
                "asset_ids": [],
            }
            for i in range(4)
        ]
    },
    ensure_ascii=False,
)


# --------------------------------------------------------------------- adapters
class FakeCreativeAdapter(AIProviderAdapter):
    """[SIMULATED] adapter: text/delay/error/cancel behaviour under test."""

    requires_key = False
    supports_attachments = False
    attachment_mode = "none"

    def __init__(
        self,
        kind: str = "custom_openai",
        text: str = PROPOSALS_JSON,
        delay: float = 0.0,
        hang: bool = False,
        error: Optional[Exception] = None,
        authenticated: bool = True,
    ) -> None:
        super().__init__()
        self.kind = kind
        self.label = f"{kind} (simulato)"
        self._text = text
        self._delay = delay
        self._hang = hang
        self._error = error
        self._authenticated = authenticated
        self.calls: list[dict[str, Any]] = []
        self.auth_calls = 0
        self.cancelled = False
        self.cancel_after: Optional[float] = None
        self._models = [
            ModelInfo(
                id="sim-creative",
                label="sim creative",
                provider=kind,
                source="simulated",
                text=True,
                vision=False,
                vision_verified=False,
                billing=BILLING_FREE_QUOTA,
                execution=EXEC_REMOTE,
                cost_per_1k_eur=0.0,
            )
        ]

    def detect(self):
        return {"installed": True, "version": "sim", "binary": None, "detail": "simulato"}

    def credentials_present(self):
        return True

    def authenticated(self):
        self.auth_calls += 1
        return {
            "authenticated": self._authenticated,
            "detail": "simulato" if self._authenticated else "non autenticato (simulato)",
            "account": "simulated" if self._authenticated else "",
        }

    def list_models(self):
        return list(self._models)

    def generate(self, messages, model=None, timeout=60.0, json_mode=False, attachments=None, cancel=None):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "timeout": timeout,
                "attachments": [str(p) for p in (attachments or [])],
            }
        )
        started = time.perf_counter()
        if self._hang:
            raise EngineTimeout(f"{self.kind}: nessuna risposta entro {timeout:.1f}s (simulato)")
        if self._delay:
            while time.perf_counter() - started < min(self._delay, max(0.0, timeout - 0.1)):
                if cancel is not None and cancel():
                    self.cancelled = True
                    raise EngineCancelled("annullata durante l'inferenza (simulato)")
                time.sleep(0.02)
            if self._delay >= timeout:
                raise EngineTimeout(f"{self.kind}: timeout simulato dopo {timeout:.1f}s")
        if self._error is not None:
            raise self._error
        return EngineResult(text=self._text, provider=self.kind, model=model or "sim-creative")


# --------------------------------------------------------------------- fixtures
@pytest.fixture()
def env(isolated_env: dict):
    db = Database(isolated_env["db_path"])
    providers = ProviderManager(db)
    providers.ensure_seeded()
    director = KoraDirector(db=db)
    return {
        "db": db,
        "providers": providers,
        "director": director,
        "manager": director.ai_engine,
        "tmp": isolated_env["tmp_path"],
    }


def install_adapter(manager: AIEngineManager, adapter: AIProviderAdapter) -> None:
    """Register a fake adapter and a controlled snapshot (no global discovery)."""
    manager._adapters[adapter.kind] = adapter
    entry = manager._build_snapshot_entry(adapter.kind)
    manager._snapshot = {
        "captured_at": time.time(),
        "providers": {adapter.kind: entry},
        "build_ms": 1.0,
        "budget_sec": 0.0,
    }
    manager._snapshot_at = time.time()
    manager._models_cache.clear()
    manager._probe_cache.clear()


def fast_settings(manager: AIEngineManager, **overrides: Any) -> None:
    payload = {"global_budget_sec": 8.0, "first_attempt_sec": 3.0, "cache_ttl_sec": 3600}
    payload.update(overrides)
    manager.update_settings({"fast_creative": payload})


def wait_run(ideation, run_id: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = ideation.get(run_id)
        assert snapshot is not None
        if snapshot["finished"]:
            return snapshot
        time.sleep(0.02)
    raise AssertionError("run non terminato entro il timeout del test")


def run_request(director: KoraDirector, req: Optional[CreativeRequestPayload] = None, avoid_cache: bool = False, timeout: float = 15.0) -> dict[str, Any]:
    run_id = director.creative.ideation.start(req or CreativeRequestPayload(), avoid_cache=avoid_cache)
    return wait_run(director.creative.ideation, run_id, timeout=timeout)


# ------------------------------------------------------- 1. OpenCode authenticated
def test_opencode_authenticated_returns_four_lite_proposals(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeCreativeAdapter(kind="opencode")
    install_adapter(manager, adapter)
    fast_settings(manager)
    snapshot = run_request(env["director"])
    assert snapshot["status"] == "done"
    envelope = snapshot["envelope"]
    assert len(envelope["proposals"]) == 4
    assert envelope["generation_engine"] == "ai_engine:opencode/sim-creative"
    assert envelope["fallback_reason"] is None
    assert all(p["source"] == "ai_engine" for p in envelope["proposals"])
    # nessun allegato, nessun tool, nessuna analisi multimedia durante l'ideazione
    assert all(not call["attachments"] for call in adapter.calls)
    prompt_chars = sum(len(m["content"]) for m in adapter.calls[0]["messages"])
    assert prompt_chars < 6000, "contesto compatto: niente profilo brand completo"
    assert "vault_inventory" not in adapter.calls[0]["messages"][1]["content"]
    stages = [event["stage"] for event in snapshot["events"]]
    assert stages[0] == "context"
    ordered = list(dict.fromkeys(stages))
    assert ordered == ["context", "connect", "generate", "validate", "done"]
    assert snapshot["events"][-1]["stage"] == "done"


# ----------------------------------------------------------- 2. cold first request
def test_cold_first_request_does_not_scan_every_request(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeCreativeAdapter(kind="opencode")
    install_adapter(manager, adapter)
    fast_settings(manager)
    auth_before = adapter.auth_calls
    started = time.perf_counter()
    snapshot = run_request(env["director"])
    cold_ms = (time.perf_counter() - started) * 1000
    assert snapshot["status"] == "done"
    assert cold_ms < 2000, f"prima richiesta troppo lenta: {cold_ms:.0f} ms"
    for _ in range(10):
        assert run_request(env["director"])["status"] == "done"
    assert adapter.auth_calls == auth_before, "nessun probe provider per richiesta"
    assert manager.snapshot_age_sec() is not None


# ------------------------------------------- 3. main scenario: 12 measured runs
def test_main_scenario_latency_min_median_max(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeCreativeAdapter(kind="opencode", delay=0.05)
    install_adapter(manager, adapter)
    fast_settings(manager)
    times: list[float] = []
    successes = 0
    for _ in range(12):
        started = time.perf_counter()
        snapshot = run_request(env["director"], avoid_cache=True)
        times.append((time.perf_counter() - started) * 1000)
        if snapshot["status"] == "done" and len(snapshot["envelope"]["proposals"]) == 4:
            successes += 1
    times.sort()
    median = statistics.median(times)
    print(
        f"\n[2.8.1 SIMULATED main scenario] n={len(times)} min={times[0]:.1f} ms "
        f"median={median:.1f} ms max={times[-1]:.1f} ms success={successes / len(times) * 100:.0f}%"
    )
    assert successes == 12
    assert max(times) < 8000, "budget Fast Creative rispettato"
    assert median < 1000


# ------------------------------------------------------- 4. model unavailable
def test_model_unavailable_falls_back_without_crash(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeCreativeAdapter(kind="opencode", error=EngineModelUnavailable("modello rimosso"))
    install_adapter(manager, adapter)
    fast_settings(manager)
    snapshot = run_request(env["director"])
    assert snapshot["status"] == "done"
    assert len(snapshot["envelope"]["proposals"]) == 4
    assert snapshot["envelope"]["generation_engine"] == "deterministic_offline_fallback"
    assert snapshot["envelope"]["fallback_reason"]
    assert any(event["stage"] == "fallback" for event in snapshot["events"])


# --------------------------------------------------- 5. provider not authenticated
def test_provider_not_authenticated_is_explicit(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeCreativeAdapter(kind="opencode", authenticated=False)
    install_adapter(manager, adapter)
    fast_settings(manager)
    snapshot = run_request(env["director"])
    assert snapshot["status"] == "done"
    assert snapshot["envelope"]["generation_engine"] == "deterministic_offline_fallback"
    assert "Nessun motore AI" in (snapshot["envelope"]["fallback_reason"] or "")
    assert len(snapshot["envelope"]["proposals"]) == 4


# ------------------------------------------------------------ 6. no internet
def test_no_internet_falls_back_and_reports_reason(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeCreativeAdapter(kind="opencode", error=EngineQuotaExhausted("rete non raggiungibile / quota"))
    install_adapter(manager, adapter)
    fast_settings(manager)
    snapshot = run_request(env["director"])
    assert snapshot["status"] == "done"
    envelope = snapshot["envelope"]
    assert envelope["generation_engine"] == "deterministic_offline_fallback"
    assert envelope["fallback_reason"]
    assert all(p["source"] == "deterministic" for p in envelope["proposals"])


# --------------------------------------------------------- 7. model timeout
def test_model_timeout_respects_first_attempt_budget(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeCreativeAdapter(kind="opencode", delay=30.0)
    install_adapter(manager, adapter)
    fast_settings(manager, global_budget_sec=8.0, first_attempt_sec=3.0)
    started = time.perf_counter()
    snapshot = run_request(env["director"], timeout=20)
    elapsed = time.perf_counter() - started
    assert snapshot["status"] == "done"
    assert elapsed < 6.0, f"primo tentativo non chiuso al budget: {elapsed:.1f}s"
    assert snapshot["envelope"]["generation_engine"] == "deterministic_offline_fallback"


# ------------------------------------------------------------ 8. invalid JSON
def test_invalid_json_uses_deterministic_fallback(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeCreativeAdapter(kind="opencode", text="Ecco le mie proposte: ... (non JSON)")
    install_adapter(manager, adapter)
    fast_settings(manager)
    snapshot = run_request(env["director"])
    assert snapshot["status"] == "done"
    envelope = snapshot["envelope"]
    assert envelope["generation_engine"] == "deterministic_offline_fallback"
    assert len(envelope["proposals"]) == 4
    assert any(event["stage"] == "validate" and event["status"] == "error" for event in snapshot["events"])


# --------------------------------------------------- 9. deterministic fallback
def test_deterministic_fallback_is_always_four_proposals(env):
    director: KoraDirector = env["director"]
    started = time.perf_counter()
    envelope = director.creative.suggest_four_themes_lite(CreativeRequestPayload())
    elapsed = (time.perf_counter() - started) * 1000
    assert len(envelope.proposals) == 4
    assert envelope.generation_engine == "deterministic_offline_fallback"
    assert elapsed < 3000
    for proposal in envelope.proposals:
        assert len(proposal.asset_ids) <= 3
        assert 8 <= proposal.recommended_duration_sec <= 90
        dumped = proposal.model_dump()
        for forbidden in ("voiceover_script", "storyboard", "image_prompt", "motion_graphics"):
            assert forbidden not in dumped


# --------------------------------------------- 10. cancellation during processing
def test_cancellation_stops_the_request_for_real(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeCreativeAdapter(kind="opencode", delay=20.0)
    install_adapter(manager, adapter)
    fast_settings(manager, global_budget_sec=25.0, first_attempt_sec=15.0)
    director: KoraDirector = env["director"]
    run_id = director.creative.ideation.start(CreativeRequestPayload(), avoid_cache=True)
    time.sleep(0.6)
    result = director.creative.ideation.cancel(run_id)
    assert result["cancelled"] is True
    snapshot = wait_run(director.creative.ideation, run_id, timeout=5)
    assert snapshot["status"] == "cancelled"
    assert snapshot["elapsed_ms"] < 4000
    assert snapshot["envelope"] is None
    assert adapter.calls, "la richiesta era davvero in corso"
    time.sleep(0.3)
    assert adapter.cancelled is True, "il motore ha ricevuto l'annullamento reale"
    assert not manager._cancellations, "nessuna richiesta resta registrata dopo l'annullamento"


# ------------------------------- 11. global budget with one authorised fallback
def test_global_budget_and_single_authorised_fallback(env):
    manager: AIEngineManager = env["manager"]
    slow = FakeCreativeAdapter(kind="opencode", delay=30.0)
    fast = FakeCreativeAdapter(kind="custom_openai")
    manager._adapters[slow.kind] = slow
    manager._adapters[fast.kind] = fast
    manager._snapshot = {
        "captured_at": time.time(),
        "providers": {
            slow.kind: manager._build_snapshot_entry(slow.kind),
            fast.kind: manager._build_snapshot_entry(fast.kind),
        },
        "build_ms": 1.0,
        "budget_sec": 0.0,
    }
    manager._snapshot_at = time.time()
    fast_settings(manager, global_budget_sec=8.0, first_attempt_sec=3.0)
    started = time.perf_counter()
    snapshot = run_request(env["director"], timeout=20)
    elapsed = time.perf_counter() - started
    envelope = snapshot["envelope"]
    assert snapshot["status"] == "done"
    assert elapsed < 7.5, f"budget globale non rispettato: {elapsed:.1f}s"
    assert envelope["generation_engine"].startswith("ai_engine:custom_openai/")
    assert envelope["timings_ms"].get("fallback") == 1
    assert fast.calls, "il fallback autorizzato è stato realmente usato"
    assert len(slow.calls) <= 1, "un solo tentativo sul primo motore"


# --------------------------------------------------------- 12. proposal cache
def test_proposal_cache_is_marked_and_reused(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeCreativeAdapter(kind="opencode")
    install_adapter(manager, adapter)
    fast_settings(manager)
    first = run_request(env["director"], CreativeRequestPayload(theme="cache test"))
    assert first["envelope"]["from_cache"] is False
    calls_after_first = len(adapter.calls)
    second = run_request(env["director"], CreativeRequestPayload(theme="cache test"))
    envelope = second["envelope"]
    assert envelope["from_cache"] is True
    assert envelope["cache_age_sec"] is not None
    assert all(p["from_cache"] and p["source"] == "cache" for p in envelope["proposals"])
    assert len(adapter.calls) == calls_after_first, "la cache evita una nuova inferenza"
    fresh = run_request(env["director"], CreativeRequestPayload(theme="cache test"), avoid_cache=True)
    assert fresh["envelope"]["from_cache"] is False


# --------------------------------------------- 13. used themes are not re-proposed
def test_used_themes_are_never_re_proposed(env):
    manager: AIEngineManager = env["manager"]
    director: KoraDirector = env["director"]
    director.creative.anti_rep.record_reel(
        project_id="prj_old",
        title="Tema simulato 1",
        theme="tema vecchio",
        hook="hook",
        ending="ending",
        phrases=["a", "b"],
        asset_ids=[],
        music_id=None,
        preset="KORA Cinematic Identity",
    )
    adapter = FakeCreativeAdapter(kind="opencode")
    install_adapter(manager, adapter)
    fast_settings(manager)
    snapshot = run_request(director)
    titles = {p["title"].lower() for p in snapshot["envelope"]["proposals"]}
    assert "tema simulato 1" not in titles


# ------------------------------------------- 14. API: start, poll, cancel, 404
def test_api_suggest_lite_flow_and_cancel(env):
    from fastapi.testclient import TestClient

    from kora_studio.api.app import create_app

    app = create_app(db_path=env["tmp"] / "api.db", auto_seed=True)
    client = TestClient(app)
    manager: AIEngineManager = app.state.director.ai_engine
    adapter = FakeCreativeAdapter(kind="opencode", delay=0.05)
    install_adapter(manager, adapter)
    fast_settings(manager)
    started = client.post("/api/creative/suggest-lite", json={"theme": "API test"}).json()
    assert started["run_id"] and started["budget_ms"] > 0
    snapshot = None
    for _ in range(100):
        snapshot = client.get(f"/api/creative/suggest-lite/{started['run_id']}").json()
        if snapshot["finished"]:
            break
        time.sleep(0.05)
    assert snapshot and snapshot["status"] == "done"
    assert len(snapshot["envelope"]["proposals"]) == 4
    assert client.get("/api/creative/suggest-lite/nope").status_code == 404
    assert client.post("/api/creative/suggest-lite/nope/cancel").json()["cancelled"] is False

    # annullamento via API su una richiesta lenta
    slow = FakeCreativeAdapter(kind="opencode", delay=20.0)
    install_adapter(manager, slow)
    slow_run = client.post("/api/creative/suggest-lite", json={"theme": "API cancel"}).json()
    time.sleep(0.5)
    cancelled = client.post(f"/api/creative/suggest-lite/{slow_run['run_id']}/cancel").json()
    assert cancelled["cancelled"] is True
    for _ in range(100):
        final = client.get(f"/api/creative/suggest-lite/{slow_run['run_id']}").json()
        if final["finished"]:
            break
        time.sleep(0.05)
    assert final["status"] == "cancelled"


# --------------------------------- 15. AI Activity Monitor: distinct timings
def test_activity_monitor_records_phase_timings(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeCreativeAdapter(kind="opencode")
    install_adapter(manager, adapter)
    fast_settings(manager)
    run_request(env["director"])
    activity = manager.activity.list(limit=5)
    assert activity, "la chiamata deve essere registrata"
    row = activity[0]
    timings = row["timings_ms"]
    assert "inference" in timings and "total" in timings
    assert "json_parsing" in timings, "parsing JSON misurato separatamente"
    assert timings["total"] > 0
    assert row["model"] == "sim-creative"
    assert row["request_status"] == "completed"


# ---------------------------------------- 16. heavy artefacts only after choice
def test_expand_selected_lite_proposal_builds_full_concept(env):
    director: KoraDirector = env["director"]
    proposal = director.creative.suggest_four_themes_lite(CreativeRequestPayload()).proposals[0]
    # prima della scelta non esistono voiceover/storyboard: solo campi lite
    lite_fields = set(ThemeProposalLite.model_fields)
    assert "voiceover_script" not in lite_fields
    concept = director.creative.expand_lite_proposal(proposal)
    assert concept.voiceover_script is not None
    assert concept.hook == proposal.hook
    assert concept.message == proposal.central_message
    assert concept.generation_engine.startswith("expanded_from_lite:")


# ------------------------- 17. regression: classic full ideation still works
def test_classic_ideation_not_regressed(env):
    director: KoraDirector = env["director"]
    envelope = director.creative.suggest_four_themes(CreativeRequestPayload())
    assert len(envelope.concepts) == 4
    assert envelope.generation_engine in ("deterministic_offline_fallback",) or envelope.concepts[0].voiceover_script


# ---------------------------------------- 18. model selection never changed
def test_user_model_selection_is_never_changed(env):
    manager: AIEngineManager = env["manager"]
    manager.update_settings({"providers": {"custom_openai": {"model": "modello-utente"}}})
    before = manager.settings()["providers"]["custom_openai"]["model"]
    adapter = FakeCreativeAdapter(kind="opencode")
    install_adapter(manager, adapter)
    fast_settings(manager, prefer_model="")
    run_request(env["director"])
    after = manager.settings()["providers"]["custom_openai"]["model"]
    assert before == after == "modello-utente"


# ------------------------------- 19. optional background pre-generation (free only)
def test_background_prewarm_requires_opt_in_and_zero_cost(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeCreativeAdapter(kind="opencode")
    install_adapter(manager, adapter)
    fast_settings(manager, background_warm=False)
    assert env["director"].creative.ideation.prewarm()["started"] is False
    fast_settings(manager, background_warm=True)
    outcome = env["director"].creative.ideation.prewarm()
    assert outcome["started"] is True and outcome["cost"] == 0.0
    wait_run(env["director"].creative.ideation, outcome["run_id"])
    cached = run_request(env["director"])
    assert cached["envelope"]["from_cache"] is True


# --------------------------- 20. deterministic fallback is visible in the monitor
def test_deterministic_fallback_is_logged_with_phases(env):
    director: KoraDirector = env["director"]
    director.creative.suggest_four_themes_lite(CreativeRequestPayload())
    rows = director.ai_engine.activity.list(limit=5)
    row = next((r for r in rows if r["model"] == "kora-deterministic"), None)
    assert row is not None, "il fallback deterministico deve comparire nel Monitor AI"
    assert row["outcome"] == "ok" and row["cost_eur"] == 0.0
    assert row["timings_ms"].get("total") is not None
    assert row["timings_ms"].get("fallback") is not None
    assert row["request_status"] == "completed"
