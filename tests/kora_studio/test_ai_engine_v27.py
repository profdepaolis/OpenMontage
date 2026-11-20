"""KORA STUDIO 2.7 — Cinematic Intelligence, Monitor, Copy, QC esatto, centralizzazione.

Ogni test qui separa chiaramente ciò che è **simulato** (adapters finti, marcati
``source="simulated"``) da ciò che richiede un **account reale**: i test che
richiedono OpenCode/Codex/API reali sono saltati con un motivo esplicito e non
vengono mai dichiarati superati.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import pytest

from kora_studio.agents.copywriter import Copywriter
from kora_studio.agents.story_director import CinematicStoryDirector, phases_for
from kora_studio.agents.visual_intelligence import VisualIntelligence
from kora_studio.ai_engine.activity import AIActivityLog
from kora_studio.ai_engine.base import AIProviderAdapter, EngineCancelled, EngineResult, ModelInfo
from kora_studio.ai_engine.manager import AIEngineManager
from kora_studio.ai_engine.models import BILLING_FREE_QUOTA, EXEC_REMOTE
from kora_studio.agents.quality_controller import QualityController
from kora_studio.db.database import Database
from kora_studio.db.models import CameraMotion, SceneTransition, StoryboardScene
from kora_studio.providers.registry import ProviderManager


@pytest.fixture()
def env(isolated_env: dict):
    db = Database(isolated_env["db_path"])
    providers = ProviderManager(db)
    providers.ensure_seeded()
    return {"db": db, "providers": providers, "tmp": isolated_env["tmp_path"]}


class SimulatedEngine(AIProviderAdapter):
    """Adattatore SIMULATO: non contatta nessun servizio reale."""

    requires_key = False
    supports_attachments = True
    attachment_mode = "simulated"

    def __init__(self, kind: str = "custom_openai", text: str = "{}"):
        super().__init__()
        self.kind = kind
        self.label = f"{kind} (simulato)"
        self._text = text
        self.calls: list[dict[str, Any]] = []
        self._models = [
            ModelInfo(
                id="sim-model",
                label="sim-model",
                provider=kind,
                source="simulated",
                text=True,
                vision=True,
                vision_verified=True,
                billing=BILLING_FREE_QUOTA,
                execution=EXEC_REMOTE,
                cost_per_1k_eur=0.0,
            )
        ]

    def detect(self):
        return {"installed": True, "version": "sim", "binary": None, "detail": "adattatore simulato"}

    def credentials_present(self):
        return True

    def authenticated(self):
        return {"authenticated": True, "detail": "simulato", "account": "simulated"}

    def list_models(self):
        return list(self._models)

    def generate(self, messages, model=None, timeout=60.0, json_mode=False, attachments=None, cancel=None):
        if cancel and cancel():
            raise EngineCancelled("simulated: annullata")
        self.calls.append({"messages": messages, "attachments": [str(p) for p in (attachments or [])]})
        return EngineResult(text=self._text, provider=self.kind, model=model or "sim-model", cost_eur=0.0)


def _manager(env, adapter: SimulatedEngine) -> AIEngineManager:
    manager = AIEngineManager(env["providers"], env["db"])
    manager._adapters[adapter.kind] = adapter
    manager.update_settings({"primary_provider": adapter.kind, "strategy": "auto"})
    return manager


def _scene(number: int, frames: int, **kwargs) -> StoryboardScene:
    start = (number - 1) * frames
    base = dict(
        scene_number=number,
        start_frame=start,
        end_frame=start + frames,
        duration_frames=frames,
        start_sec=start / 25,
        end_sec=(start + frames) / 25,
        duration_sec=frames / 25,
        narrative_goal=f"Scena {number}",
        spoken_text="",
    )
    base.update(kwargs)
    return StoryboardScene(**base)


# --------------------------------------------------------------- story director
def test_narrative_arc_is_proportional_not_cyclic():
    assert [phase for phase, _ in phases_for(6)] == ["hook", "context", "detail", "experience", "emotion", "payoff"]
    assert len(phases_for(3)) == 3
    assert phases_for(3)[0][0] == "hook" and phases_for(3)[-1][0] == "payoff"
    # con 8 scene il numero di hook/payoff non cresce ciclicamente
    eight = [phase for phase, _ in phases_for(8)]
    assert eight.count("hook") <= 2 and eight.count("payoff") <= 2


def test_story_director_plan_uses_real_assets_and_keeps_exact_frames(env):
    director = CinematicStoryDirector(vault=None, engine=None)
    assets = [
        {"id": "a1", "category": "Mare", "media_type": "image", "filename": "mare.jpg", "analysis": {}},
        {"id": "a2", "category": "Colazione", "media_type": "image", "filename": "colazione.jpg", "analysis": {}},
        {"id": "a3", "category": "Pergolato", "media_type": "image", "filename": "pergolato.jpg", "analysis": {}},
    ]
    scenes = [
        _scene(1, 100, selected_asset_id="a1"),
        _scene(2, 90, selected_asset_id="a2"),
        _scene(3, 80, selected_asset_id="a3"),
        _scene(4, 70, selected_asset_id="a1"),
    ]
    frames_before = [s.duration_frames for s in scenes]
    plan = director.plan(scenes, assets, preset="KORA Cinematic Identity")
    assert len(plan) == 4
    assert len({p["phase"] for p in plan}) >= 3, "il piano non deve ripetere la stessa funzione"
    assert all(p["asset_id"] in {"a1", "a2", "a3"} for p in plan)
    assert all(p["framing"] in {"campo_ampio", "piano_medio", "dettaglio_ravvicinato"} for p in plan)
    assert len({(p["camera"], p["framing"]) for p in plan}) > 1

    director.apply_plan(scenes, plan, {a["id"]: a for a in assets})
    assert [s.duration_frames for s in scenes] == frames_before, "il piano non tocca i fotogrammi"
    assert all(s.camera_motion in {m.value for m in CameraMotion} for s in scenes)
    assert all(s.transition in {t.value for t in SceneTransition} for s in scenes)


def test_story_director_outro_always_fades_and_uses_logo(env):
    director = CinematicStoryDirector(vault=None, engine=None)
    outro = _scene(2, 75, is_outro=True, shot_framing="outro_logo", transition=SceneTransition.CROSS_DISSOLVE.value)
    plan = [{"scene_number": 1, "asset_id": "a1", "framing": "campo_ampio", "camera": "pan_left",
             "transition": "cut_on_motion", "sfx": "onde", "purpose": "hook", "rationale": "x"}]
    director.apply_plan([_scene(1, 425, selected_asset_id="a1"), outro], plan, {"a1": {"id": "a1", "media_type": "image"}})
    assert outro.is_outro is True
    assert outro.transition == SceneTransition.FADE_BLACK.value
    assert outro.camera_motion == CameraMotion.STATIC_BREATHE.value


def test_story_director_uses_engine_when_available(env):
    plan_json = {
        "shots": [
            {"scene_number": 1, "asset_id": "a1", "framing": "campo_ampio", "camera": "slow_push_in",
             "transition": "cross_dissolve", "sfx": "onde", "music": "musica_cinematic", "purpose": "hook", "rationale": "r1"},
            {"scene_number": 2, "asset_id": "INVENTATO", "framing": "inventato", "camera": "drone_loop",
             "transition": "wipe", "sfx": "sparatoria", "purpose": "contesto", "rationale": "r2"},
        ]
    }
    adapter = SimulatedEngine(text=json.dumps(plan_json))
    manager = _manager(env, adapter)
    director = CinematicStoryDirector(vault=None, engine=manager)
    scenes = [_scene(1, 250, selected_asset_id="a1"), _scene(2, 250, selected_asset_id="a1")]
    plan = director.plan(scenes, [{"id": "a1", "category": "Mare", "media_type": "image", "filename": "mare.jpg"}])
    assert plan[0]["source"].startswith("engine:"), "con un motore disponibile il piano arriva dal modello"
    # output non valido: l'asset inventato e i valori sconosciuti non passano
    assert plan[1]["asset_id"] == "a1"
    assert plan[1]["framing"] == "piano_medio" and plan[1]["camera"] == "slow_push_in"
    assert plan[1]["transition"] == "cross_dissolve"
    assert plan[1]["sfx"] == "ambiente_spiaggia", "una categoria audio inventata non passa"


# ----------------------------------------------------------- visual intelligence
def test_visual_intelligence_reviews_with_verified_vision_and_caches(env):
    review = {
        "description": "Mare calmo all'alba con luce dorata sul pergolato.",
        "subjects": ["mare", "pergolato"],
        "lighting": "luce dorata laterale",
        "environment": "mare",
        "framing": "campo_ampio",
        "materials": ["legno", "lino"],
        "mood": ["calmo", "autentico"],
        "can_carry_scene": True,
    }
    adapter = SimulatedEngine(text=json.dumps(review))
    manager = _manager(env, adapter)

    photo = env["tmp"] / "mare.jpg"
    from PIL import Image

    Image.new("RGB", (800, 600), (40, 120, 160)).save(photo, "JPEG")
    asset = {"id": "asset-1", "sha256": "abc", "working_path": str(photo), "category": "Mare",
             "media_type": "image", "tags": ["mare"], "analysis": {}}

    vi = VisualIntelligence(vault=None, engine=manager, db=env["db"])
    first = vi.review_asset(asset)
    assert first["source"].startswith("engine:")
    assert "luce dorata" in first["description"]
    assert adapter.calls and adapter.calls[-1]["attachments"], "l'immagine reale deve essere inviata"

    calls_before = len(adapter.calls)
    second = vi.review_asset(asset)  # cache: nessuna seconda chiamata
    assert second["description"] == first["description"]
    assert len(adapter.calls) == calls_before


def test_visual_intelligence_without_vision_model_is_declared_deterministic(env):
    asset = {"id": "asset-2", "sha256": "def", "category": "Pergolato", "media_type": "image",
             "tags": ["pergolato"], "analysis": {"multimodal": {"environment": "pergolato"}, "technical_quality": 80}}
    vi = VisualIntelligence(vault=None, engine=None, db=env["db"])
    review = vi.review_asset(asset)
    assert review["source"] == "deterministic"
    assert review["vision_verified"] is False
    assert "nessun modello visivo" in review["notes"]


# -------------------------------------------------------------------- copywriter
class _Guardian:
    def __init__(self, approved: bool):
        self.approved = approved

    def validate_copy(self, text: str):
        return {"approved": self.approved, "violations": [] if self.approved else ["espressione vietata"]}


def test_copywriter_uses_engine_text_and_brand_check(env):
    adapter = SimulatedEngine(text=json.dumps({"caption": "La luce che resta.", "hashtags": ["#Kora"], "alt_text": "mare"}))
    manager = _manager(env, adapter)
    writer = Copywriter(manager, guardian=_Guardian(True))
    out = writer.write(
        title="Alba", message="Il mare tace", theme="mattino",
        fallback={"caption": "template", "hashtags": ["#KoraBeachClub"]},
    )
    assert out["source"].startswith("engine:")
    assert out["caption"] == "La luce che resta."
    assert out["engine_error"] is None


def test_copywriter_rejects_offbrand_engine_text(env):
    adapter = SimulatedEngine(text=json.dumps({"caption": "Sconto 50% solo oggi!", "hashtags": ["#Kora"]}))
    manager = _manager(env, adapter)
    writer = Copywriter(manager, guardian=_Guardian(False))
    out = writer.write(
        title="Alba", message="Il mare tace", theme="mattino",
        fallback={"caption": "template sicuro", "hashtags": ["#KoraBeachClub"], "full_post_text": "x"},
    )
    assert out["source"] == "deterministic_template"
    assert out["caption"] == "template sicuro"
    assert "Brand Guardian" in (out["engine_error"] or "")


# ------------------------------------------------------------------ activity log
def test_activity_monitor_records_every_call_without_prompts(env):
    adapter = SimulatedEngine(text="KORA OK")
    manager = _manager(env, adapter)

    manager.call(
        "copywriter",
        [{"role": "user", "content": "PROMPT SEGRETISSIMO: chiave sk-abc123"}],
        production_id="prj_test01",
    )
    rows = manager.activity.list(limit=10)
    assert rows, "ogni chiamata va registrata"
    row = rows[0]
    assert row["production_id"] == "prj_test01"
    assert row["agent"] == "copywriter"
    assert row["provider"] == adapter.kind
    assert row["model"] == "sim-model"
    assert row["outcome"] == "ok"
    assert row["duration_ms"] >= 0
    assert row["cost_known"] is True and row["cost_eur"] == 0.0
    blob = json.dumps(rows)
    assert "PROMPT SEGRETISSIMO" not in blob and "sk-abc123" not in blob

    summary = manager.activity.summary()
    assert summary["calls"] >= 1 and summary["ok"] >= 1


def test_activity_monitor_records_unknown_cost_as_unknown(env):
    log = AIActivityLog(env["db"])
    log.record(
        production_id="prj_x", agent="visual_intelligence", provider="openrouter", model="mystery/model",
        started_at="2026-11-20T10:00:00+00:00", duration_ms=42, outcome="ok", attempts=1,
        fallback_used=True, fallback_from="gemini_api", cost_eur=None, billing="unknown", execution="remote",
    )
    row = log.list(limit=5)[0]
    assert row["cost_known"] is False and row["cost_eur"] is None
    assert row["fallback_used"] is True and row["fallback_from"] == "gemini_api"
    exported = json.loads(log.export_json())
    assert exported[0]["agent"] == "visual_intelligence"


# ------------------------------------------------------- frame count verification
def _ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg") or str(Path(".venv/bin/ffmpeg").resolve()) if Path(".venv/bin/ffmpeg").is_file() else shutil.which("ffmpeg")


def test_qc_counts_exact_frames_and_never_estimates(env, monkeypatch):
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        pytest.skip("ffmpeg non disponibile in questo ambiente: conteggio frame non eseguibile")
    from kora_studio.config import DEFAULT_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH

    clip = env["tmp"] / "clip.mp4"
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c=0x1D1B18:s={DEFAULT_WIDTH}x{DEFAULT_HEIGHT}:r={DEFAULT_FPS}",
            "-t", "2", "-pix_fmt", "yuv420p", str(clip),
        ],
        check=True,
        capture_output=True,
    )
    count, detail = QualityController.count_frames(clip)
    assert count == 2 * DEFAULT_FPS, f"2 s @ {DEFAULT_FPS} fps devono essere esattamente {2 * DEFAULT_FPS} frame ({detail})"
    assert detail == "nb_read_frames"

    # senza ffprobe il QC non deve ripiegare su una stima
    monkeypatch.setattr("kora_studio.agents.quality_controller.find_ffprobe", lambda: None)
    unknown, reason = QualityController.count_frames(clip)
    assert unknown is None and "ffprobe" in reason


def test_qc_expected_frames_for_20s_and_30s_are_exact():
    scenes20 = [_scene(1, 425), _scene(2, 75)]
    scenes30 = [_scene(1, 650), _scene(2, 100)]
    assert sum(s.duration_frames for s in scenes20) == 500
    assert sum(s.duration_frames for s in scenes30) == 750


# ------------------------------------------------------------- centralisation
def test_no_agent_calls_providers_directly():
    """Requisito 3: nessun agente può chiamare un provider fuori dall'AI Engine Manager."""
    root = Path(__file__).resolve().parents[2] / "kora_studio" / "agents"
    # i servizi media (voce, video, immagini) restano separati dai motori linguistici;
    # qui si vieta qualunque chiamata diretta agli endpoint dei MODELLI DI TESTO.
    text_endpoints = (
        "api.openai.com/v1/chat",
        "api.anthropic.com",
        "api.deepseek.com",
        "openrouter.ai/api",
        "/api/chat",
        "chat/completions",
    )
    offenders: list[str] = []
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in text_endpoints:
            if token in text:
                offenders.append(f"{path.name}: {token}")
    assert offenders == [], f"chiamate dirette ai motori di testo: {offenders}"


def test_all_nine_agents_are_wired_to_the_engine():
    from kora_studio.agents.director import KoraDirector

    director = KoraDirector()
    agents = {
        "creative_director": director.creative,
        "cinematic_story_director": director.creative.story_director,
        "visual_intelligence": director.creative.visual_intelligence,
        "brand_guardian": director.guardian,
        "ai_media_lab": director.media_lab,
        "voice_director": director.voice_director,
        "sound_director": director.sound_designer,
        "quality_guardian": director.qc,
        "copywriter": director.copywriter,
    }
    assert len(agents) == 9
    for agent_id, instance in agents.items():
        engine = getattr(instance, "engine", None) or getattr(instance, "ai_engine", None)
        assert engine is director.ai_engine, f"{agent_id} non passa dall'AI Engine Manager"


# ------------------------------------------------ simulated vs real separation
def test_simulated_engine_is_never_reported_as_operational(env):
    adapter = SimulatedEngine(text="KORA OK")
    manager = _manager(env, adapter)
    probe = manager.probe(adapter.kind, refresh=True)
    # lo stato è "autenticato" (simulato) finché non avviene una generazione reale
    assert probe["state"] in ("autenticato", "installato")
    assert probe["verified_operational"] is False


@pytest.mark.skipif(
    shutil.which("opencode") is None,
    reason="OpenCode non installato in questo ambiente: test reale non eseguibile (nessuna simulazione)",
)
def test_real_opencode_account_reports_verifiable_plan(env):
    manager = AIEngineManager(env["providers"], env["db"])
    probe = manager.probe("opencode", refresh=True)
    assert probe["installed"] is True
    models = manager.models("opencode")
    assert models, "OpenCode deve elencare i modelli dell'account reale"
    for model in models:
        assert model["cost_known"] in (True, False)  # dichiarato, mai inventato
        if model["billing"] == "subscription":
            assert model["subscription_name"], "un abbonamento è tale solo se la CLI ne espone il nome"


@pytest.mark.skipif(
    shutil.which("codex") is None,
    reason="Codex non installato in questo ambiente: test reale non eseguibile (nessuna simulazione)",
)
def test_real_codex_account_is_detected(env):
    manager = AIEngineManager(env["providers"], env["db"])
    probe = manager.probe("codex", refresh=True)
    assert probe["installed"] is True
    assert probe["state"] in ("installato", "autenticato", "operativo")
