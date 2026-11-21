"""KORA STUDIO v3.0 Essential — un motore, una richiesta, quattro sezioni.

* **[SIMULATED]** adapter finti (nessuna rete, nessun CLI).
* **[INTEGRATION]** DB, Vault e API reali in workspace isolato.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import pytest

from kora_studio.agents.director import KoraDirector
from kora_studio.ai_engine.base import AIProviderAdapter, EngineError, EngineResult, ModelInfo
from kora_studio.ai_engine.manager import AIEngineManager
from kora_studio.ai_engine.models import BILLING_FREE_QUOTA, EXEC_REMOTE
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload, ThemeProposalLite
from kora_studio.providers.registry import ProviderManager

DESIGN_JSON = json.dumps(
    {
        "concept": {
            "title": "L'ora che rallenta",
            "hook": "C'è un'ora, davanti al mare, in cui tutto rallenta.",
            "central_message": "Il tempo al KORA non si misura: si assapora.",
            "narrative_arc": "Apertura ampia sul mare, dettagli tattili, chiusura sul pergolato.",
            "voiceover_script": (
                "C'è un'ora, davanti al mare, in cui tutto rallenta. "
                "La luce accarezza il pergolato, il caffè ha il sapore del mattino. "
                "KORA. The Art of Being."
            ),
            "visual_mood": "luce dorata, calma mediterranea",
            "objective": "Identità di marca",
            "primary_emotion": "serenità",
            "ending": "The Art of Being",
        },
        "scenes": [
            {
                "visual_goal": "Apertura sul mare",
                "framing": "campo_ampio",
                "camera_motion": "slow_push_in",
                "transition": "cross_dissolve",
                "subtitle_text": "C'è un'ora",
            },
            {
                "visual_goal": "Dettaglio del pergolato",
                "framing": "dettaglio_tattile",
                "camera_motion": "micro_parallax",
                "transition": "warm_fade",
                "subtitle_text": "in cui tutto rallenta",
            },
            {
                "visual_goal": "Colazione servita",
                "framing": "piano_medio",
                "camera_motion": "slow_pull_out",
                "transition": "cut_on_motion",
                "subtitle_text": "il caffè ha il sapore del mattino",
            },
        ],
        "sound_design": {"music_mood": "mediterraneo calmo", "texture": "mare e brezza", "mix_note": "voce -6 dB"},
        "cover": {"title": "L'ora che rallenta", "subtitle": "The Art of Being"},
        "caption": "C'è un'ora davanti al mare in cui tutto rallenta. KORA · The Art of Being.",
    },
    ensure_ascii=False,
)


class FakeAdapter(AIProviderAdapter):
    """[SIMULATED] un modello testuale, nessun tool, nessuna immagine."""

    requires_key = False
    supports_attachments = False
    attachment_mode = "none"

    def __init__(self, kind: str = "opencode", text: str = DESIGN_JSON, error: Optional[Exception] = None):
        super().__init__()
        self.kind = kind
        self.label = f"{kind} (simulato)"
        self._text = text
        self._error = error
        self.auth_calls = 0
        self.calls: list[dict[str, Any]] = []
        self._models = [
            ModelInfo(
                id="sim-creative",
                label="sim",
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
        return {"authenticated": True, "detail": "simulato", "account": "simulated"}

    def list_models(self):
        return list(self._models)

    def generate(self, messages, model=None, timeout=60.0, json_mode=False, attachments=None, cancel=None):
        self.calls.append({"messages": messages, "model": model, "attachments": [str(p) for p in (attachments or [])]})
        if self._error:
            raise self._error
        return EngineResult(text=self._text, provider=self.kind, model=model or "sim-creative")


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


def activate(manager: AIEngineManager, adapter: FakeAdapter) -> None:
    """Attiva esplicitamente l'estensione (come farebbe l'utente dalle Impostazioni)."""
    manager._adapters[adapter.kind] = adapter
    entry = manager._build_snapshot_entry(adapter.kind)
    manager._snapshot = {
        "captured_at": time.time(),
        "providers": {adapter.kind: entry},
        "build_ms": 1.0,
        "budget_sec": 0.0,
    }
    manager._snapshot_at = time.time()
    manager.update_settings(
        {
            "primary_provider": adapter.kind,
            "essential": {
                "enabled": True,
                "active_provider": adapter.kind,
                "enabled_providers": [adapter.kind],
            },
        }
    )
    # update_settings invalida le cache: reinstallo adapter e snapshot controllato
    manager._adapters[adapter.kind] = adapter
    manager._snapshot = {
        "captured_at": time.time(),
        "providers": {adapter.kind: manager._build_snapshot_entry(adapter.kind)},
        "build_ms": 1.0,
        "budget_sec": 0.0,
    }
    manager._snapshot_at = time.time()


# ------------------------------------------------- 1. un solo motore attivo
def test_essential_keeps_exactly_one_active_engine(env):
    manager: AIEngineManager = env["manager"]
    conf = manager.essential()
    assert conf["enabled"] is True
    assert conf["active_provider"] == "opencode"
    assert conf["enabled_providers"] == ["opencode"]
    assert manager.active_provider_ids() == ["opencode"]


# --------------------------- 2. provider disattivati: mai interrogati
def test_disabled_extensions_are_never_probed_or_monitored(env):
    manager: AIEngineManager = env["manager"]
    gemini = FakeAdapter(kind="gemini_api")
    manager._adapters[gemini.kind] = gemini
    board = manager.status_board(refresh=True)
    states = {p["provider"]: p for p in board["providers"]}
    assert states["gemini_api"]["enabled"] is False
    assert states["gemini_api"]["state"] == "indisponibile"
    assert "estensione opzionale" in states["gemini_api"]["detail"]
    assert gemini.auth_calls == 0
    assert gemini.calls == []
    assert manager.candidate_plan("creative_director") == []


# ------------------------------------------- 3. attivazione esplicita
def test_enabling_an_extension_starts_probing_it(env):
    manager: AIEngineManager = env["manager"]
    gemini = FakeAdapter(kind="gemini_api")
    manager._adapters[gemini.kind] = gemini
    manager.update_settings(
        {
            "essential": {
                "enabled": True,
                "active_provider": "gemini_api",
                "enabled_providers": ["gemini_api"],
            }
        }
    )
    manager._adapters[gemini.kind] = gemini
    board = manager.status_board(refresh=True)
    states = {p["provider"]: p for p in board["providers"]}
    assert states["gemini_api"]["enabled"] is True
    assert gemini.auth_calls >= 1
    # opencode torna estensione spenta e non viene interrogato
    assert states["opencode"]["enabled"] is False


# --------------------------------------- 4. nessuna scansione periodica
def test_snapshot_refresher_is_single_shot(env):
    manager: AIEngineManager = env["manager"]
    manager._snapshot = {}
    manager._snapshot_at = 0.0
    manager.start_snapshot_refresher()
    thread = manager._snapshot_thread
    assert thread is not None
    thread.join(timeout=10)
    assert not thread.is_alive(), "nessun loop periodico di scansione provider"
    assert manager.snapshot_age_sec() is not None


# --------------------------- 5. Progetta Reel: una sola richiesta creativa
def test_reel_design_single_call_and_exact_frames(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeAdapter()
    activate(manager, adapter)
    director: KoraDirector = env["director"]
    req = CreativeRequestPayload(theme="Tramonto sul mare", duration_sec=20.0, voice_id="brando")
    design = director.design_reel(req, deadline_sec=10)
    assert design.generation_engine.startswith("ai_engine:")
    assert design.calls == 1
    assert len(adapter.calls) == 1, "una sola richiesta creativa per progettare il Reel"
    assert design.fallback_reason is None
    assert sum(s.duration_frames for s in design.scenes) == 20 * 25
    assert any(s.is_outro for s in design.scenes)
    assert "art of being" in design.concept.voiceover_script.lower()
    assert design.caption and design.cover["title"] and design.sound_design["music_mood"]
    assert design.timings_ms.get("json_parsing") is not None
    # nessun allegato/multimedia inviato
    assert all(not c["attachments"] for c in adapter.calls)
    # inquadrature e movimenti provengono dall'AI ma restano nel set ammesso
    framings = {s.shot_framing for s in design.scenes if not s.is_outro}
    assert framings & {"campo_ampio", "dettaglio_tattile", "piano_medio"}


# --------------------------------------- 6. JSON non valido → deterministico
def test_reel_design_invalid_json_falls_back(env):
    manager: AIEngineManager = env["manager"]
    adapter = FakeAdapter(text="ecco il progetto: ... (non JSON)")
    activate(manager, adapter)
    director: KoraDirector = env["director"]
    design = director.design_reel(CreativeRequestPayload(theme="Ospitalità", duration_sec=30.0), deadline_sec=10)
    assert design.generation_engine == "deterministic_offline_fallback"
    assert design.fallback_reason
    assert design.calls == 1
    assert sum(s.duration_frames for s in design.scenes) == 30 * 25


# ------------------------------------------- 7. nessun motore → deterministico
def test_reel_design_without_engine_is_deterministic(env):
    director: KoraDirector = env["director"]
    started = time.perf_counter()
    design = director.design_reel(CreativeRequestPayload(theme="Brand identity", duration_sec=20.0))
    elapsed = (time.perf_counter() - started) * 1000
    assert design.generation_engine == "deterministic_offline_fallback"
    assert design.calls == 0
    assert len(design.scenes) >= 3
    assert sum(s.duration_frames for s in design.scenes) == 20 * 25
    assert elapsed < 5000
    print(f"\n[3.0 SIMULATED design deterministico] {elapsed:.0f} ms, {len(design.scenes)} scene")


# ------------------------------ 8. progetto creato dal design (una volta)
def test_project_from_design_persists_scene_plan(env):
    manager: AIEngineManager = env["manager"]
    activate(manager, FakeAdapter())
    director: KoraDirector = env["director"]
    design = director.design_reel(CreativeRequestPayload(theme="Esperienza", duration_sec=20.0), deadline_sec=10)
    project = director.create_project_from_design(design, "Esperienza")
    assert project["total_frames"] == 500
    assert len(project["storyboard"]) == len(design.scenes)
    assert project["storyboard"][0]["duration_frames"] > 0
    design_meta = project["preferences"]["reel_design"]
    assert design_meta["caption"] == design.caption
    assert design_meta["cover"]["title"] == design.cover["title"]
    assert project["concept"]["voiceover_script"] == design.concept.voiceover_script


# ------------------------------------- 9. API: bootstrap cheap + project-reel
def test_api_bootstrap_and_project_reel(env):
    from fastapi.testclient import TestClient

    from kora_studio.api.app import create_app

    app = create_app(db_path=env["tmp"] / "essential.db", auto_seed=True)
    client = TestClient(app)
    manager: AIEngineManager = app.state.director.ai_engine
    adapter = FakeAdapter(kind="opencode")
    manager._adapters[adapter.kind] = adapter
    manager._update_test_snapshot = True  # noqa: SLF001 - solo marcatore di lettura
    manager._snapshot = {
        "captured_at": time.time(),
        "providers": {adapter.kind: manager._build_snapshot_entry(adapter.kind)},
        "build_ms": 1.0,
        "budget_sec": 0.0,
    }
    manager._snapshot_at = time.time()

    boot = client.get("/api/studio/bootstrap")
    assert boot.status_code == 200
    body = boot.json()
    assert body["voices"] and body["engine"]["active_provider"] == "opencode"
    assert "categories" in body["vault"]
    # il bootstrap non interroga i provider
    assert manager.provider_enabled("gemini_api") is False

    res = client.post(
        "/api/creative/project-reel",
        json={
            "request": {"theme": "Aperitivo al tramonto", "duration_sec": 20, "voice_id": "brando"},
            "create_project": True,
        },
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["design"]["calls"] == 1
    assert payload["design"]["concept"]["voiceover_script"]
    assert payload["project"]["total_frames"] == 500
    assert len(adapter.calls) == 1


# ------------------------------- 10. proposta lite scelta → design coerente
def test_design_from_selected_lite_proposal(env):
    director: KoraDirector = env["director"]
    proposal = ThemeProposalLite(
        id="lite-x",
        title="Il silenzio del mattino",
        hook="Prima che il KORA si svegli",
        central_message="Solo luce, mare e caffè",
        narrative_direction="Apertura ampia, dettaglio, chiusura sul mare.",
        recommended_objective="Identità di marca",
        recommended_voice_id="brando",
        recommended_duration_sec=30.0,
        asset_ids=[],
    )
    design = director.design_reel(CreativeRequestPayload(), proposal)
    assert design.concept.duration_sec == 30.0
    assert sum(s.duration_frames for s in design.scenes) == 30 * 25
    assert design.concept.title == "Il silenzio del mattino"
