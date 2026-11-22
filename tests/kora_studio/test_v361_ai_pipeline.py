"""v3.6.1 — ripristino della pipeline AI: Model Manager unica fonte, timeout, decisioni.

Nessun test dichiara «HY4 funziona» per finta: dove il CLI reale non c'è si usa
un *doppio di prova* (adapter in-process o eseguibile `opencode` finto) che
percorre esattamente lo stesso codice di produzione — inclusi timeout, parsing
JSON, Model Manager e pipeline di rendering.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from kora_studio.agents.director import KoraDirector
from kora_studio.ai_engine.base import AIProviderAdapter, EngineResult, EngineTimeout, ModelInfo
from kora_studio.ai_engine.models import BILLING_FREE_QUOTA, EXEC_REMOTE
from kora_studio.api.app import create_app
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload
from kora_studio.providers.registry import ProviderManager

THEMES = {
    "proposals": [
        {
            "title": f"Orizzonte KORA {i + 1}",
            "hook": f"C'è un'ora {i + 1} in cui il mare decide il ritmo.",
            "central_message": f"Messaggio {i + 1}: l'ospitalità è tempo, non servizio.",
            "narrative_direction": "Apertura ampia, dettaglio tattile, chiusura sul pergolato.",
            "recommended_objective": "Identità di marca",
            "recommended_voice_id": "none",
            "recommended_duration_sec": 20,
            "asset_ids": [],
        }
        for i in range(4)
    ]
}

DESIGN = {
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
}


# --------------------------------------------------------------------- doppi di prova
class _CliFake(AIProviderAdapter):
    """Adapter con la stessa interfaccia di un CLI reale (is_cli=True)."""

    requires_key = False
    supports_attachments = False
    attachment_mode = "none"
    is_cli = True

    def __init__(self, kind: str = "opencode", delay: float = 0.0, text: str = "") -> None:
        super().__init__()
        self.kind = kind
        self.label = "OpenCode CLI (doppio di prova)"
        self._delay = delay
        self._text = text or json.dumps(THEMES, ensure_ascii=False)
        self.models = [
            ModelInfo(
                id="opencode/hy4-flash-free",
                label="HY4 Flash",
                provider="opencode",
                source="cli",
                text=True,
                vision=False,
                vision_verified=False,
                billing=BILLING_FREE_QUOTA,
                execution=EXEC_REMOTE,
                cost_per_1k_eur=0.0,
            ),
            ModelInfo(
                id="opencode/mimo-v2.6-flash-free",
                label="MiMo",
                provider="opencode",
                source="cli",
                text=True,
                billing=BILLING_FREE_QUOTA,
                execution=EXEC_REMOTE,
                cost_per_1k_eur=0.0,
            ),
            ModelInfo(
                id="opencode/nemotron-3.5-lightning-free",
                label="Nemotron",
                provider="opencode",
                source="cli",
                text=True,
                billing=BILLING_FREE_QUOTA,
                execution=EXEC_REMOTE,
                cost_per_1k_eur=0.0,
            ),
        ]
        self.calls: list[dict[str, Any]] = []

    def detect(self):
        return {"installed": True, "binary": "/usr/local/bin/opencode", "version": "0.4.2", "detail": "doppio di prova"}

    def credentials_present(self):
        return True

    def authenticated(self):
        return {"authenticated": True, "detail": "account CLI (doppio di prova)", "account": "opencode"}

    def list_models(self):
        return list(self.models)

    def raw_model_ids(self):
        return [m.id for m in self.models]

    def workspace(self):
        return Path("/tmp")

    def prepare_workspace(self):
        return Path("/tmp")

    def generate(
        self,
        messages,
        model=None,
        timeout=90.0,
        json_mode=False,
        attachments=None,
        cancel=None,
    ):
        self.calls.append({"model": model, "timeout": timeout})
        started = time.perf_counter()
        if self._delay:
            target = min(self._delay, max(0.0, timeout - 0.2))
            while time.perf_counter() - started < target:
                if cancel is not None and cancel():
                    raise EngineTimeout("annullato")
                time.sleep(0.02)
            if self._delay >= timeout:
                raise EngineTimeout(f"nessuna risposta entro {timeout:.1f}s (doppio di prova)")
        return EngineResult(text=self._text, provider=self.kind, model=model or "", latency_ms=1)


STUB = r'''#!/usr/bin/env python3
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
if not args:
    sys.exit(1)
if args[0] == "--version" or "--version" in args:
    print("0.4.2")
    sys.exit(0)
if args[0] == "models":
    for name in ("opencode/hy4-flash-free", "opencode/mimo-v2.6-flash-free",
                 "opencode/nemotron-3.5-lightning-free"):
        print(name)
    sys.exit(0)
if args[0] == "auth":
    print(json.dumps([{"id": "opencode", "name": "OpenCode Zen",
                       "connections": [{"label": "opencode Zen"}]}]))
    sys.exit(0)
if args[0] == "run":
    if "--help" in args:
        print("run --format --model --agent --file <prompt>")
        sys.exit(0)
    delay_file = os.path.join(HERE, "delay")
    if os.path.isfile(delay_file):
        try:
            time.sleep(float(open(delay_file).read().strip() or 0))
        except Exception:
            pass
    prompt = args[-1]
    if "scenes" in prompt:
        payload = json.loads(open(os.path.join(HERE, "design.json")).read())
    else:
        payload = json.loads(open(os.path.join(HERE, "themes.json")).read())
    print(json.dumps({"type": "text", "text": json.dumps(payload, ensure_ascii=False)}))
    sys.exit(0)
print("run --format --model --agent --file")
sys.exit(0)
'''


@pytest.fixture()
def director(isolated_env: dict) -> KoraDirector:
    db = Database(isolated_env["db_path"])
    providers = ProviderManager(db)
    providers.ensure_seeded()
    from kora_studio.brand.brand_brain import BrandBrain

    BrandBrain(db).ensure_initialized()
    return KoraDirector(db=db)


def _install(manager, adapter: _CliFake) -> None:
    manager._adapters[adapter.kind] = adapter
    manager._models_cache.clear()
    manager._probe_cache.clear()
    manager._snapshot = {
        "captured_at": time.time(),
        "providers": {adapter.kind: manager._build_snapshot_entry(adapter.kind)},
        "build_ms": 1.0,
        "budget_sec": 0.0,
    }
    manager._snapshot_at = time.time()


def _wait(client: TestClient, run_id: str, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = client.get(f"/api/creative/suggest-lite/{run_id}").json()
        if snap.get("finished"):
            return snap
        time.sleep(0.05)
    raise AssertionError("run di ideazione non terminato entro il timeout del test")


# ------------------------------------------------------------- 1. migrazione legacy
def test_legacy_deepseek_is_replaced_by_hy4_and_legacy_keys_are_cleared(director: KoraDirector):
    engine = director.ai_engine
    engine.update_settings(
        {
            "providers": {"opencode": {"model": "nvidia/deepseek-ai/deepseek-v4.1-flash"}},
            "agents": {"creative_director": {"mode": "provider", "provider": "opencode", "model": "nvidia/deepseek-ai/deepseek-v4.1-flash"}},
        }
    )
    result = director.model_manager.migrate_legacy(engine)
    assert result["migrated"] is True
    cfg = director.model_manager.config()
    assert cfg["roles"]["everyday"] == "opencode/hy4-flash-free"
    assert cfg["migration"]["retired"] is True
    assert "deepseek-v4.1-flash" in cfg["migration"]["detail"]
    # le chiavi legacy non possono più scegliere il modello
    settings = engine.settings()
    assert not (settings["providers"].get("opencode") or {}).get("model")
    assert (settings["agents"].get("creative_director") or {}).get("mode") == "inherit"
    # idempotente: la seconda volta non riscrive nulla
    again = director.model_manager.migrate_legacy(engine)
    assert again["migrated"] is False


def test_a_valid_legacy_preference_is_preserved(director: KoraDirector):
    engine = director.ai_engine
    engine.update_settings({"providers": {"opencode": {"model": "opencode/mimo-v2.6-flash-free"}}})
    director.model_manager.migrate_legacy(engine)
    cfg = director.model_manager.config()
    assert cfg["roles"]["everyday"] == "opencode/mimo-v2.6-flash-free"
    assert cfg["migration"]["retired"] is False


def test_an_explicit_user_choice_is_never_overwritten(director: KoraDirector):
    director.model_manager.update({"roles": {"everyday": "opencode/nemotron-3.5-lightning-free"}})
    director.ai_engine.update_settings({"providers": {"opencode": {"model": "opencode/mimo-v2.6-flash-free"}}})
    result = director.model_manager.migrate_legacy(director.ai_engine)
    assert result["migrated"] is False
    assert director.model_manager.config()["roles"]["everyday"] == "opencode/nemotron-3.5-lightning-free"


# ------------------------------------------------- 2. selezione esplicita del modello
def test_ideation_uses_the_model_manager_model_explicitly(director: KoraDirector):
    adapter = _CliFake()
    _install(director.ai_engine, adapter)
    run_id = director.creative.ideation.start(CreativeRequestPayload(theme="Luce"), avoid_cache=True)
    snap = director.creative.ideation.get(run_id)
    deadline = time.time() + 20
    while snap["status"] == "running" and time.time() < deadline:
        time.sleep(0.02)
        snap = director.creative.ideation.get(run_id)
    assert snap["status"] == "done", snap
    assert adapter.calls[0]["model"] == "opencode/hy4-flash-free"
    assert snap["envelope"]["model"] == "hy4-flash-free"

    director.model_manager.update({"mode": "premium"})
    director.creative.ideation.start(CreativeRequestPayload(theme="Luce premium"), avoid_cache=True)
    deadline = time.time() + 20
    while time.time() < deadline:
        snap2 = director.creative.ideation.get(run_id)
        if adapter.calls and adapter.calls[-1]["model"] == "opencode/mimo-v2.6-flash-free":
            break
        time.sleep(0.05)
    assert adapter.calls[-1]["model"] == "opencode/mimo-v2.6-flash-free"


def test_design_uses_the_same_model_as_the_home(director: KoraDirector):
    adapter = _CliFake(text=json.dumps(DESIGN, ensure_ascii=False))
    _install(director.ai_engine, adapter)
    design = director.design_reel(CreativeRequestPayload(theme="Luce", duration_sec=20, voice_id="none"), deadline_sec=20)
    assert design.generation_engine == "ai_engine:opencode/hy4-flash-free"
    assert adapter.calls[0]["model"] == "opencode/hy4-flash-free"


# ------------------------------------------------------------------ 3. timeout reale
def test_a_valid_answer_after_21_seconds_is_accepted(director: KoraDirector):
    """HY4 impiega 20,7 s: con il vecchio limite nascosto di 15 s veniva scartato."""
    adapter = _CliFake(delay=21.0)
    _install(director.ai_engine, adapter)
    started = time.perf_counter()
    run_id = director.creative.ideation.start(CreativeRequestPayload(theme="Tempo reale"), avoid_cache=True)
    deadline = time.time() + 60
    while True:
        snap = director.creative.ideation.get(run_id)
        if snap["status"] != "running" or time.time() > deadline:
            break
        time.sleep(0.05)
    elapsed = time.perf_counter() - started
    assert snap["status"] == "done", snap
    assert snap["envelope"]["generation_engine"] == "ai_engine:opencode/hy4-flash-free"
    assert len(snap["envelope"]["proposals"]) == 4
    assert elapsed >= 20.5
    assert adapter.calls[0]["timeout"] > 30.0, "il tentativo deve avere il budget di ideazione, non 15 s"


# ------------------------------------------- 4. politica «Chiedi conferma» + decisioni
def test_ask_policy_keeps_the_request_and_never_returns_local_proposals(director: KoraDirector):
    adapter = _CliFake(delay=60.0)  # supera il budget: nessuna risposta
    _install(director.ai_engine, adapter)
    director.model_manager.update({"timeouts": {"ideation": 10}, "policy": "ask"}, internal=True)
    run_id = director.creative.ideation.start(CreativeRequestPayload(theme="Timeout"), avoid_cache=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        snap = director.creative.ideation.get(run_id)
        if snap["status"] != "running":
            break
        time.sleep(0.05)
    assert snap["status"] == "needs_decision", snap
    assert snap["envelope"] is None, "nessuna proposta locale senza consenso"
    decision = snap["decision"]
    assert [o["action"] for o in decision["options"]] == ["retry", "fallback", "local", "cancel"]
    assert "timeout" in decision["reason"].lower()
    assert decision["model"] == "opencode/hy4-flash-free"

    # «Usa proposte locali»: solo ora, e dichiarate come locali
    assert director.creative.ideation.decide(run_id, "local")["ok"] is True
    deadline = time.time() + 10
    while time.time() < deadline:
        snap = director.creative.ideation.get(run_id)
        if snap["status"] != "running":
            break
        time.sleep(0.02)
    assert snap["status"] == "done"
    assert snap["envelope"]["generation_engine"] == "deterministic_offline_fallback"
    assert all(p["source"] == "deterministic" for p in snap["envelope"]["proposals"])
    assert "motivo AI" in snap["envelope"]["fallback_reason"]


def test_decide_fallback_runs_nemotron(director: KoraDirector):
    adapter = _CliFake(delay=60.0)
    _install(director.ai_engine, adapter)
    director.model_manager.update({"timeouts": {"ideation": 8}, "policy": "ask"}, internal=True)
    run_id = director.creative.ideation.start(CreativeRequestPayload(theme="Fallback"), avoid_cache=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        snap = director.creative.ideation.get(run_id)
        if snap["status"] != "running":
            break
        time.sleep(0.05)
    assert snap["status"] == "needs_decision"

    adapter._delay = 0.0  # il modello alternativo risponde
    result = director.creative.ideation.decide(run_id, "fallback")
    assert result["action"] == "fallback"
    second = result["run_id"]
    deadline = time.time() + 20
    while time.time() < deadline:
        snap2 = director.creative.ideation.get(second)
        if snap2["status"] != "running":
            break
        time.sleep(0.02)
    assert snap2["status"] == "done"
    assert snap2["envelope"]["model"] == "nemotron-3.5-lightning-free"
    assert adapter.calls[-1]["model"] == "opencode/nemotron-3.5-lightning-free"


# ------------------------------------------- 5. Gemini non cambia il motore attivo
def test_gemini_key_does_not_steal_the_creative_engine(director: KoraDirector):
    from kora_studio.ai_engine import adapters_http

    original = adapters_http.GeminiAdapter.authenticated
    original_models = adapters_http.GeminiAdapter.list_models
    adapters_http.GeminiAdapter.authenticated = lambda self: {"authenticated": True, "detail": "ok"}
    adapters_http.GeminiAdapter.list_models = lambda self: [
        ModelInfo(id="gemini-2.0-flash-lite", label="Flash-Lite", provider="gemini_api", billing="unknown")
    ]
    try:
        result = director.ai_engine.connect_gemini_api("AIza-test")
    finally:
        adapters_http.GeminiAdapter.authenticated = original
        adapters_http.GeminiAdapter.list_models = original_models
    assert result["ok"] is True
    assert result["activated"] is False
    assert director.ai_engine.essential()["active_provider"] == "opencode"


# --------------------------------------------------------- 6. end-to-end con il CLI
def _seed_vault(director: KoraDirector, tmp: Path) -> list[str]:
    weeks = ["mare.png", "pergolato.png", "colazione.png", "tramonto.png"]
    for index, name in enumerate(weeks):
        path = tmp / name
        image = Image.new("RGB", (1080, 1920), (30 + index * 40, 80, 110))
        draw = ImageDraw.Draw(image)
        draw.rectangle([80, 160, 1000, 1760], outline=(245, 240, 230), width=10)
        image.save(path)
        director.vault.ingest_file(
            path,
            origin="test_v361",
            authenticity_status="autentico",
            usage_rights="proprietario_kora",
            category="Mare",
            tags=["mare", "kora"],
        )
    logo = tmp / "LOGO KORA.PNG"
    logo_image = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    ImageDraw.Draw(logo_image).text((220, 85), "KORA", fill=(250, 247, 242, 255))
    logo_image.save(logo, "PNG")
    director.vault.ingest_file(
        logo,
        origin="test_v361_brand",
        authenticity_status="autentico",
        usage_rights="proprietario_kora",
        category="Logo",
        tags=["logo", "kora", "brand"],
        original_filename="LOGO KORA.PNG",
    )
    return [a["id"] for a in director.vault.list_assets() if a.get("category") != "Logo"]


def test_full_pipeline_with_stub_cli(director: KoraDirector, isolated_env, tmp_path, monkeypatch):
    """Suggerisci → scegli → Progetta → Realizza: MP4 1080×1920 25 fps, 20,00 s."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    binary = stub_dir / "opencode"
    binary.write_text(STUB)
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (stub_dir / "themes.json").write_text(json.dumps(THEMES, ensure_ascii=False))
    (stub_dir / "design.json").write_text(json.dumps(DESIGN, ensure_ascii=False))
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    asset_ids = _seed_vault(director, tmp_path)
    app = create_app(db_path=isolated_env["db_path"], auto_seed=False)
    client = TestClient(app)
    engine = app.state.director.ai_engine
    engine._reset_adapters()
    engine._probe_cache.clear()
    engine._snapshot = {}
    engine._snapshot_at = 0.0
    director.model_manager.update({"mode": "everyday", "policy": "ask", "timeouts": {"ideation": 45}})

    # 1-4. avvio → HY4 → quattro proposte realmente generate dal CLI
    boot = client.get("/api/studio/bootstrap").json()
    assert boot["ai_status"]["model"] == "opencode/hy4-flash-free"
    started = client.post(
        "/api/creative/suggest-lite",
        json={"theme": "Il tempo che rallenta", "duration_sec": 20, "voice_id": "none"},
    ).json()
    snap = _wait(client, started["run_id"], timeout=60)
    assert snap["status"] == "done", snap
    envelope = snap["envelope"]
    assert envelope["generation_engine"] == "ai_engine:opencode/hy4-flash-free"
    assert envelope["model"] == "hy4-flash-free"
    assert len(envelope["proposals"]) == 4
    assert all(p["source"] == "ai_engine" for p in envelope["proposals"])
    assert [p["title"] for p in envelope["proposals"]] == [p["title"] for p in THEMES["proposals"]]

    # 5-8. scelta del tema → copione e storyboard → progetto
    proposal = envelope["proposals"][0]
    expanded = client.post(
        "/api/creative/expand-theme",
        json={"proposal": proposal, "duration_sec": 20, "asset_ids": asset_ids},
    )
    assert expanded.status_code == 200, expanded.text
    reel = client.post(
        "/api/creative/project-reel",
        json={
            "request": {"theme": proposal["title"], "duration_sec": 20, "voice_id": "none"},
            "proposal": proposal,
            "create_project": True,
        },
    )
    assert reel.status_code == 200, reel.text
    payload = reel.json()
    assert payload["design"]["generation_engine"] == "ai_engine:opencode/hy4-flash-free"
    project = payload["project"]
    assert project["total_frames"] == 500
    assert len(project["storyboard"]) >= 3
    assert project["concept"]["voiceover_script"]

    # 9. fotografie autentiche associate alle scene
    detail = client.get(f"/api/projects/{project['id']}").json()
    assigned = [s.get("selected_asset_id") for s in detail["storyboard"] if s.get("selected_asset_id")]
    assert assigned, "nessuna fotografia associata"

    # 10-12. Realizza Reel → job visibile → MP4 1080×1920 · 25 fps · 20,00 s
    produced = client.post(
        f"/api/projects/{project['id']}/produce",
        json={"sync": True, "include_subtitles": True},
    )
    assert produced.status_code == 200, produced.text
    job = produced.json()["job"]
    jobs = client.get("/api/jobs", params={"project_id": project["id"]}).json()
    assert any(j["id"] == job["id"] for j in jobs), "il job non compare nella Production Pipeline"

    done = client.get(f"/api/projects/{project['id']}").json()
    output = Path(done["output_video_path"])
    assert output.is_file() and output.stat().st_size > 200_000

    import subprocess

    probe = json.loads(
        subprocess.run(
            [".venv/bin/ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(output)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    num, den = video["r_frame_rate"].split("/")
    assert abs(float(num) / float(den) - 25.0) < 0.01
    assert abs(float(probe["format"]["duration"]) - 20.0) < 0.1

    # 13. QC ed esportazione
    qc = done["qc_report"]
    assert qc["passed"] is True, qc
    checks = {c["name"]: c for c in qc["checks"]}
    assert checks["Verifica esatta dei fotogrammi"]["status"] == "superato"
    assert qc["checks"], "nessun controllo QC"


# ------------------------- rete di sicurezza: snapshot vecchio ≠ deterministico
class _ApiFake(AIProviderAdapter):
    """[DOPPIO DI PROVA] provider non CLI con un solo modello testuale."""

    requires_key = False
    supports_attachments = False
    attachment_mode = "none"

    def __init__(self, kind: str = "opencode", model_id: str = "sim-creative") -> None:
        super().__init__()
        self.kind = kind
        self.label = f"{kind} (simulato)"
        self._models = [
            ModelInfo(
                id=model_id,
                label=model_id,
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
        self.calls: list[dict[str, Any]] = []

    def detect(self):
        return {"installed": True, "version": "sim", "binary": None, "detail": "simulato"}

    def credentials_present(self):
        return True

    def authenticated(self):
        return {"authenticated": True, "detail": "simulato", "account": "simulated"}

    def list_models(self):
        return list(self._models)

    def generate(self, messages, model=None, timeout=60.0, json_mode=False, attachments=None, cancel=None):
        self.calls.append({"model": model})
        return EngineResult(text="ok", raw={}, model=model or "", latency_ms=1.0)


def test_a_stale_snapshot_never_turns_a_working_engine_into_local_proposals(director: KoraDirector):
    """Uno snapshot senza il provider collegato non deve produrre il deterministico.

    Era la causa intermittente di `calls == 0` con adapter di collaudo: lo snapshot
    catturato prima del collegamento nascondeva un motore che funzionava davvero.
    """
    manager = director.ai_engine
    adapter = _ApiFake()
    manager._adapters[adapter.kind] = adapter
    manager._models_cache.clear()
    manager._probe_cache.clear()
    manager.model_manager.update({"roles": {"everyday": "opencode/sim-creative"}}, internal=True)

    # snapshot fresco ma privo del provider: il provider non è stato verificato
    manager._snapshot = {
        "captured_at": time.time(),
        "providers": {"gemini_api": {"provider": "gemini_api", "authenticated": False, "models": []}},
        "build_ms": 1.0,
        "budget_sec": 0.0,
    }
    manager._snapshot_at = time.time()

    entry = manager.creative_entry("creative_director")
    assert entry is not None, "nessun motore: sarebbe un fallback deterministico ingiustificato"
    assert entry["provider"] == "opencode"
    assert entry["model"] == "sim-creative"  # id del provider, senza prefisso
