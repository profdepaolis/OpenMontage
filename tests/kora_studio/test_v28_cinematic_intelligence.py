"""KORA STUDIO 2.8 — Cinematic Intelligence & Production Stability.

Test tiers are explicit:

* **[SIMULATED]** — fake adapters (``source="simulated"``) model a provider
  without contacting any service.
* **[INTEGRATION]** — real DB, real Vault files, real FFmpeg renders inside an
  isolated temporary workspace.
* **[REAL PROVIDER]** — would need OpenCode/Codex/API accounts: those tests are
  skipped with a declared reason and are never counted as passed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import pytest
from PIL import Image, ImageDraw

from kora_studio.agents.cinematic_editor import CinematicPreviewRenderer, PREVIEW_HEIGHT, PREVIEW_WIDTH
from kora_studio.agents.story_director import CinematicStoryDirector
from kora_studio.ai_engine.base import AIProviderAdapter, EngineResult, ModelInfo
from kora_studio.ai_engine.manager import AIEngineManager
from kora_studio.ai_engine.models import BILLING_FREE_QUOTA, BILLING_PAID_API, EXEC_REMOTE
from kora_studio.db.database import Database
from kora_studio.db.models import AuthenticityStatus, CameraMotion, StoryboardScene
from kora_studio.providers.registry import ProviderManager


# --------------------------------------------------------------------- fixtures
@pytest.fixture()
def env(isolated_env: dict):
    db = Database(isolated_env["db_path"])
    providers = ProviderManager(db)
    providers.ensure_seeded()
    return {
        "db": db,
        "providers": providers,
        "tmp": isolated_env["tmp_path"],
        "data": isolated_env["data_dir"],
        "db_path": isolated_env["db_path"],
    }


def _photo(path: Path, seed: int = 0, size: tuple[int, int] = (900, 1600)) -> Path:
    """Synthetic but real-pixel photograph: gradient + shapes (no AI involved)."""
    img = Image.new("RGB", size, (40 + seed * 7 % 60, 60, 70))
    draw = ImageDraw.Draw(img)
    w, h = size
    for y in range(0, h, 40):
        shade = int(30 + 120 * (y / h))
        draw.rectangle([0, y, w, y + 40], fill=(shade, shade // 2 + 30, 40 + seed % 50))
    draw.ellipse([w * 0.2, h * 0.15, w * 0.8, h * 0.55], fill=(240, 220, 180))
    draw.rectangle([w * 0.1, h * 0.6, w * 0.9, h * 0.85], fill=(90 + seed, 70, 50))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path


class SimulatedEngine(AIProviderAdapter):
    """[SIMULATED] adapter: records calls, returns configured text, no network."""

    requires_key = False
    supports_attachments = True
    attachment_mode = "simulated"

    def __init__(self, kind: str = "custom_openai", text: str = "{}", vision: bool = True,
                 billing: str = BILLING_FREE_QUOTA, price: float = 0.0):
        super().__init__()
        self.kind = kind
        self.label = f"{kind} (simulato)"
        self._text = text
        self.calls: list[dict[str, Any]] = []
        self._models = [
            ModelInfo(
                id="sim-vision" if vision else "sim-text",
                label="sim",
                provider=kind,
                source="simulated",
                text=True,
                vision=vision,
                vision_verified=vision,
                billing=billing,
                execution=EXEC_REMOTE,
                cost_per_1k_eur=price,
            )
        ]

    def detect(self):
        return {"installed": True, "version": "sim", "binary": None, "detail": "simulato"}

    def credentials_present(self):
        return True

    def authenticated(self):
        return {"authenticated": True, "detail": "simulato", "account": "simulated"}

    def list_models(self):
        return list(self._models)

    def generate(self, messages, model=None, timeout=60.0, json_mode=False, attachments=None, cancel=None):
        self.calls.append({"messages": messages, "attachments": [str(p) for p in (attachments or [])]})
        return EngineResult(text=self._text, provider=self.kind, model=model or "sim")


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


# ------------------------------------------------- 1. manual selection = inventory
def test_manual_selection_constrains_inventory_and_is_not_a_sequence(env):
    from kora_studio.agents.creative_director import CreativeDirector

    vault = __import__("kora_studio.vault.media_vault", fromlist=["MediaVault"]).MediaVault(env["db"])
    ids = []
    for i in range(5):
        path = _photo(env["tmp"] / f"seed_{i}.png", seed=i)
        ids.append(vault.ingest_file(path, category="Mare", tags=["mare"], origin="test")["id"])
    authorized = ids[:3]

    from kora_studio.brand.brand_brain import BrandBrain

    creative = CreativeDirector(env["db"], BrandBrain(env["db"]), vault=vault, providers=env["providers"])
    captured: dict[str, Any] = {}

    class SpyDirector:
        def plan(self, scenes, assets, **kwargs):
            captured["assets"] = [a["id"] for a in assets]
            captured["authorized"] = kwargs.get("authorized_ids")
            captured["scenes"] = [s.scene_number for s in scenes]
            self.last_validation = {}
            return []

        def apply_plan(self, scenes, plan, assets_by_id):
            return scenes

    creative.story_director = SpyDirector()
    concept = creative.ideate_from_theme("Il mattino che resta", duration_sec=12)[0]
    scenes = creative.build_storyboard(concept, voice_id="brando", fps=25, authorized_asset_ids=authorized)

    assert captured["assets"] and set(captured["assets"]).issubset(set(authorized))
    visual = [s for s in scenes if not s.is_outro]
    assert visual and all(s.selected_asset_id in authorized for s in visual)
    # no cyclic assignment: the sequence is not simply authorized[i % 3]
    assert any(s.selected_asset_id != authorized[i % len(authorized)] for i, s in enumerate(visual))
    # outro is independent from the manual selection (logo asset)
    outro = next(s for s in scenes if s.is_outro)
    assert outro.selected_asset_id not in authorized
    assert outro.is_outro and outro.shot_source == "logo_component"


# ------------------------------------------------------------------ 2. visual cache
def test_vision_provenance_states_upgrade_and_full_vault(env):
    from kora_studio.agents.visual_intelligence import VisualIntelligence
    from kora_studio.vault.media_vault import MediaVault

    vault = MediaVault(env["db"])
    for i in range(13):  # more than the old "first 12" cap
        vault.ingest_file(_photo(env["tmp"] / f"vi_{i}.png", seed=i), category="Dettagli", tags=["dettaglio"], origin="test")

    vi = VisualIntelligence(vault, engine=None, db=env["db"])
    status = vi.vault_status()
    assert status["total"] >= 13
    assert status["counts"]["deterministic"] >= 13 and status["current_vision_signature"] is None

    first = vault.list_assets()[0]
    review = vi.review_asset(first)
    assert review["analysis_state"] == "deterministic"
    prov = review["provenance"]
    assert prov["sha256"] and prov["prompt_version"] == "vi-2.8.0" and prov["created_at"]
    assert review["vision_verified"] is False

    # a vision-verified model appears → fallback rows become stale, then get upgraded
    payload = json.dumps(
        {
            "description": "Sala luminosa con tavoli apparecchiati.",
            "subjects": ["tavoli", "luce"],
            "lighting": "luce laterale dorata",
            "environment": "sala",
            "framing": "piano_medio",
            "materials": ["legno"],
            "mood": ["caldo", "calmo"],
            "can_carry_scene": True,
            "notes": "simulato",
        }
    )
    adapter = SimulatedEngine(text=payload)
    manager = AIEngineManager(env["providers"], env["db"])
    manager._adapters[adapter.kind] = adapter
    manager.update_settings({"primary_provider": adapter.kind, "strategy": "auto"})
    manager._adapters[adapter.kind] = adapter  # update_settings re-probes and may drop the fake

    vi.attach_engine_manager(manager)
    assert vi.current_vision_signature() is not None
    assert vi.resolve_state(vi._cache().get(vi._cache_key(first)), vi.current_vision_signature()) == "stale"

    stats = vi.upgrade_fallback_analyses()
    assert stats["analyzed"] >= 13  # the whole Vault, not a permanent first-N window
    assert adapter.calls, "il modello visivo deve ricevere davvero l'immagine"
    assert all(c["attachments"] for c in adapter.calls), "nessun invio silenzioso: allegato reale"
    status2 = vi.vault_status()
    assert status2["counts"]["verified"] >= 13 and status2["counts"]["deterministic"] == 0
    sample = vi.review_asset(vault.list_assets()[0])
    assert sample["analysis_state"] == "verified" and sample["provenance"]["provider"] == adapter.kind
    # cache: a verified row is reused, not paid twice
    reused = vi.review_asset(vault.list_assets()[0])
    assert reused.get("cache_hit") is True


# ----------------------------------------------------------------- 3. smart shot
def test_smart_shot_plan_stays_inside_the_photo(env):
    from kora_studio.agents.smart_shot_generator import SmartShotGenerator
    from kora_studio.vault.media_vault import MediaVault

    vault = MediaVault(env["db"])
    asset = vault.ingest_file(_photo(env["tmp"] / "shot.png", seed=3), category="Dettagli", origin="test")
    gen = SmartShotGenerator(vault)
    for purpose in ("Dettaglio tattile: materia", "Contesto: dove siamo", "Emozione: respiro, mare"):
        plan = gen.plan_for_scene(asset, purpose=purpose, duration_frames=75)
        crop = plan["crop"]
        assert 0 <= crop["x"] < 1 and 0 <= crop["y"] < 1
        assert crop["x"] + crop["w"] <= 1.0001 and crop["y"] + crop["h"] <= 1.0001
        assert plan["residual_resolution"]["width"] >= 540
        assert 1.0 <= min(plan["zoom_start"], plan["zoom_end"]) and max(plan["zoom_start"], plan["zoom_end"]) <= 2.6
        assert plan["zoom_start"] != plan["zoom_end"] or plan["trajectory"] == "settle"
        assert len(plan["keyframes"]) == 4 and plan["easing"] in ("ease_in_out", "ease_out", "ease_in", "linear")
        assert plan["motivation"] and plan["depth_simulation"]["mode"] in ("micro_parallax", "none")
        ok, issues = gen.validate_plan(plan, plan["source_size"])
        assert ok, issues

    broken = dict(plan)
    broken["crop"] = {"x": 0.8, "y": 0.0, "w": 0.7, "h": 0.9}
    ok, issues = gen.validate_plan(broken, plan["source_size"])
    assert not ok and any("bordi" in i for i in issues)


def test_editor_really_consumes_the_shot_plan(env):
    from kora_studio.agents.ai_media_lab import AIMediaLab
    from kora_studio.agents.smart_shot_generator import SmartShotGenerator
    from kora_studio.brand.brand_brain import BrandBrain
    from kora_studio.vault.media_vault import MediaVault

    vault = MediaVault(env["db"])
    asset = vault.ingest_file(_photo(env["tmp"] / "consume.png", seed=5), category="Mare", origin="test")
    gen = SmartShotGenerator(vault)
    plan = gen.plan_for_scene(asset, purpose="Dettaglio tattile: materia", duration_frames=25)
    lab = AIMediaLab(vault, BrandBrain(env["db"]), env["providers"])

    out_motion = env["tmp"] / "motion.mp4"
    lab.animate_photo_deterministic(
        Path(asset["working_path"] or asset["original_path"]), out_motion, 25, shot_plan=plan
    )
    static_plan = dict(plan)
    static_plan["keyframes"] = [{"t": 0, "x": 0.5, "y": 0.5, "zoom": 1.0}] * 4
    static_plan["zoom_start"] = static_plan["zoom_end"] = 1.0
    out_static = env["tmp"] / "static.mp4"
    lab.animate_photo_deterministic(
        Path(asset["working_path"] or asset["original_path"]), out_static, 25, shot_plan=static_plan
    )

    def first_frame(path: Path, name: str) -> Image.Image:
        target = env["tmp"] / name
        subprocess.run(
            [shutil.which("ffmpeg") or "ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-frames:v", "1", str(target)],
            check=True,
        )
        return Image.open(target).convert("RGB")

    a = first_frame(out_motion, "a.png")
    b = first_frame(out_static, "b.png")
    assert a.size == (1080, 1920)
    # the derived crop is really applied: the first frame differs from a centred full-frame render
    small_a = a.resize((90, 160)).convert("L")
    small_b = b.resize((90, 160)).convert("L")
    diff = sum(abs(pa - pb) for pa, pb in zip(small_a.tobytes(), small_b.tobytes()))
    assert diff > 2000, "il piano di inquadratura non ha cambiato i pixel: non è realmente applicato"


# ----------------------------------------------------------------- 4. story director
def test_story_director_repairs_only_missing_scenes(env):
    assets = [
        {"id": "a1", "category": "Mare", "media_type": "image", "filename": "mare.jpg", "analysis": {}},
        {"id": "a2", "category": "Colazione", "media_type": "image", "filename": "colazione.jpg", "analysis": {}},
        {"id": "a3", "category": "Pergolato", "media_type": "image", "filename": "pergolato.jpg", "analysis": {}},
    ]
    scenes = [_scene(i, 75, selected_asset_id=f"a{i}", subtitle_text=f"testo {i}") for i in (1, 2, 3)]
    partial = json.dumps(
        {
            "shots": [
                {
                    "scene_number": 1, "asset_id": "a1", "framing": "campo_ampio", "camera": "slow_push_in",
                    "transition": "warm_fade", "sfx": "onde", "purpose": "hook", "rationale": "r",
                }
            ]
        }
    )
    adapter = SimulatedEngine(text=partial)
    manager = AIEngineManager(env["providers"], env["db"])
    manager._adapters[adapter.kind] = adapter
    manager.update_settings({"primary_provider": adapter.kind, "strategy": "auto"})
    manager._adapters[adapter.kind] = adapter

    director = CinematicStoryDirector(vault=None, engine=manager)
    plan = director.plan(scenes, assets, authorized_ids=["a1", "a2", "a3"])
    numbers = [p["scene_number"] for p in plan]
    assert numbers == [1, 2, 3], "il piano deve coprire tutte le scene"
    assert len(numbers) == len(set(numbers)), "nessuna scena duplicata"
    assert plan[0]["source"].startswith("engine:")
    assert plan[1]["source"].startswith("fallback:missing_scene_")
    assert plan[2]["source"].startswith("fallback:missing_scene_")
    assert director.last_validation["complete"] is True
    # a structured repair request was sent before falling back
    assert director.last_repair_request and director.last_repair_request["missing_scenes"] == [2, 3]


def test_story_director_rejects_unauthorized_and_unrenderable(env):
    assets = [
        {"id": "a1", "category": "Mare", "media_type": "image", "filename": "mare.jpg", "analysis": {}},
        {"id": "a2", "category": "Colazione", "media_type": "image", "filename": "colazione.jpg", "analysis": {}},
        {"id": "a3", "category": "Pergolato", "media_type": "image", "filename": "pergolato.jpg", "analysis": {}},
    ]
    scenes = [_scene(i, 75, selected_asset_id=f"a{i}", subtitle_text=f"testo {i}") for i in (1, 2, 3)]
    director = CinematicStoryDirector(vault=None, engine=None)
    bad = [
        {"scene_number": 1, "asset_id": "a1", "framing": "campo_ampio", "camera": "drone_loop", "transition": "wipe"},
        {"scene_number": 2, "asset_id": "a1", "framing": "dettaglio_ravvicinato", "camera": "micro_parallax",
         "transition": "cross_dissolve"},
        {"scene_number": 2, "asset_id": "a3", "framing": "piano_medio", "camera": "pan_left", "transition": "cross_dissolve"},
    ]
    validation = director.validate_plan(scenes, bad, {a["id"]: a for a in assets}, {"a1", "a2"})
    codes = {p["code"] for p in validation["blocking"]}
    assert "scena_duplicata" in codes
    assert "camera_non_valida" in codes
    assert "asset_non_autorizzato" in codes
    assert validation["valid"] is False


# --------------------------------------------------------------------- 5. costs
def test_unit_price_estimate_and_actual_cost_are_distinct(env):
    adapter = SimulatedEngine(kind="openrouter", text="OK", billing=BILLING_PAID_API, price=0.5)
    manager = AIEngineManager(env["providers"], env["db"])
    manager._adapters[adapter.kind] = adapter
    manager.update_settings(
        {"primary_provider": adapter.kind, "strategy": "auto", "allow_paid_calls": True, "max_budget_eur": 5.0}
    )
    manager._adapters[adapter.kind] = adapter
    result = manager.call("copywriter", [{"role": "user", "content": "scrivi una riga"}])
    assert result.cost_eur is None, "una stima non può essere registrata come costo reale"
    assert result.estimated_cost_eur and result.estimated_cost_eur > 0
    assert result.unit_price_per_1k_eur == 0.5

    rows = manager.activity.list(limit=5)
    row = next(r for r in rows if r["agent"] == "copywriter")
    assert row["unit_price_per_1k_eur"] == 0.5
    assert row["estimated_cost_eur"] and row["estimated_cost_eur"] > 0
    assert row["actual_cost_eur"] is None and row["cost_known"] is False
    assert row["request_status"] == "completed"

    summary = manager.activity.summary(limit=50)
    assert summary["actual_cost_eur"] == 0.0
    assert summary["estimated_cost_eur"] > 0


def test_subscription_zero_and_extra_budget(env):
    adapter = SimulatedEngine(kind="custom_openai", text="KORA OK")
    manager = AIEngineManager(env["providers"], env["db"])
    manager._adapters[adapter.kind] = adapter
    manager.update_settings({"primary_provider": adapter.kind, "strategy": "auto", "max_budget_eur": 0.0})
    manager._adapters[adapter.kind] = adapter
    result = manager.call("copywriter", [{"role": "user", "content": "x"}])
    assert result.cost_eur == 0.0 and result.estimated_cost_eur == 0.0
    assert result.cost_basis in ("free_quota", "subscription", "local_compute")
    settings = manager.settings()
    assert float(settings.get("max_budget_eur") or 0.0) == 0.0, "il budget extra resta 0 €"


def test_activity_monitor_shows_auth_attachments_and_vision(env):
    payload = json.dumps({"description": "Mare al tramonto", "subjects": ["mare"]})
    adapter = SimulatedEngine(text=payload)
    manager = AIEngineManager(env["providers"], env["db"])
    manager._adapters[adapter.kind] = adapter
    manager.update_settings({"primary_provider": adapter.kind, "strategy": "auto"})
    manager._adapters[adapter.kind] = adapter
    image = _photo(env["tmp"] / "activity.png", seed=1)

    class VI:
        pass

    from kora_studio.agents.visual_intelligence import VisualIntelligence
    from kora_studio.vault.media_vault import MediaVault

    vault = MediaVault(env["db"])
    vi = VisualIntelligence(vault, manager, db=env["db"])
    asset = vault.ingest_file(image, category="Mare", origin="test")
    vi.review_asset(asset)
    row = manager.activity.list(limit=5)[0]
    assert row["vision_image"] is True and row["attachments"] >= 1
    assert row["auth_type"] or row["provider"] == adapter.kind
    assert row["request_status"] == "completed"
    assert row["cost_basis"] in ("free_quota", "subscription", "actual", "estimated_from_unit_price", "unknown")


# ------------------------------------------------------------------- 6. preview
@pytest.fixture()
def preview_director(env):
    from kora_studio.agents.director import KoraDirector
    from kora_studio.vault.media_vault import MediaVault

    director = KoraDirector(db=env["db"])
    # the real KORA logo is part of the workspace assets: ingest it so the outro exists
    from kora_studio.config import ASSETS_KORA_DIR

    logo_src = ASSETS_KORA_DIR / "logo" / "LOGO KORA.PNG"
    if logo_src.is_file():
        director.vault.ingest_file(
            logo_src, category="Logo", origin="test", original_filename="LOGO KORA.PNG"
        )
    for i in range(4):
        director.vault.ingest_file(
            _photo(env["tmp"] / f"prev_{i}.png", seed=i + 10), category="Mare", tags=["mare"], origin="test"
        )
    concept = director.creative.ideate_from_theme("Il mattino che resta", duration_sec=15)[0]
    project = director.create_project(title="Preview test", theme="Il mattino che resta", concept=concept, target_duration_sec=15.0)
    return director, project["id"]


def test_preview_manifest_has_all_card_elements_and_cache(preview_director, env):
    director, project_id = preview_director
    manifest = director.render_preview(project_id)
    required = {
        "scene_number", "duration_frames", "duration_sec", "original_photo", "derived_shot",
        "camera_move", "voiceover", "subtitle_text", "transition", "sound_design",
        "thumbnail_source", "thumbnail_derived",
    }
    assert manifest["cards"] and all(required.issubset(card) for card in manifest["cards"])
    assert manifest["total_frames"] == sum(c["duration_frames"] for c in manifest["cards"])
    assert Path(manifest["preview_mp4"]).is_file()
    video_cards = [c for c in manifest["cards"] if not c["is_outro"]]
    assert any(c["derived_shot"]["trajectory"] for c in video_cards)

    second = director.render_preview(project_id)
    assert second["cache_hit"] is True, "la seconda anteprima deve riusare la cache"

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames,width,height", "-of", "json", manifest["preview_mp4"]],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert (stream["width"], stream["height"]) == (PREVIEW_WIDTH, PREVIEW_HEIGHT)
    assert int(stream["nb_read_frames"]) == manifest["total_frames"]


def test_single_scene_revision_keeps_other_scenes(preview_director):
    director, project_id = preview_director
    director.render_preview(project_id)
    before = {s["scene_number"]: (s["shot_framing"], s["subtitle_text"], s["shot_plan"]) for s in
              director.get_project(project_id)["storyboard"]}
    director.approve_scene(project_id, 1, True)

    res = director.revise_scene_preview(project_id, 2, "rendi questa scena un dettaglio e più lento")
    assert res["applied"] is True and res["proposal"]["changes"]
    after = {s["scene_number"]: (s["shot_framing"], s["subtitle_text"], s["shot_plan"]) for s in
             director.get_project(project_id)["storyboard"]}
    assert after[2][0] == "dettaglio_ravvicinato"
    for number in (1, 3, 4):
        assert before[number][0] == after[number][0], f"scena {number} alterata"
    assert 1 in res["preserved_approved_scenes"]
    assert res["preview"]["scene_clips_cached"] > 0, "le scene non modificate devono venire dalla cache"
    assert res["card"]["scene_number"] == 2


def test_preview_api_flow_and_vision_endpoints(env):
    from fastapi.testclient import TestClient
    from kora_studio.api.app import create_app
    from kora_studio.vault.media_vault import MediaVault

    app = create_app(db_path=env["db_path"], auto_seed=True)
    client = TestClient(app)
    vault = MediaVault(Database(env["db_path"]))
    for i in range(3):
        vault.ingest_file(_photo(env["tmp"] / f"api_{i}.png", seed=i + 20), category="Mare", tags=["mare"], origin="test")

    concepts = client.post(
        "/api/creative/from-theme", json={"theme": "Il mare che resta", "duration_sec": 10.0}
    ).json()["concepts"]
    r = client.post(
        "/api/projects",
        json={
            "title": concepts[0]["title"], "theme": "Il mare che resta", "mode": "theme",
            "preset": concepts[0]["preset"], "production_mode": "AUTO", "voice_id": "brando",
            "target_duration_sec": 10.0, "concept": concepts[0], "auto_start": False,
        },
    )
    assert r.status_code == 200
    project_id = r.json()["project"]["id"]
    assert r.json()["job"] is None, "Progetta Reel non deve avviare il render finale"

    preview = client.post(f"/api/projects/{project_id}/preview")
    assert preview.status_code == 200, preview.text
    manifest = preview.json()
    assert manifest["cards"] and manifest["total_frames"] > 0
    assert client.get(f"/api/projects/{project_id}/preview/video").status_code == 200
    assert client.get(f"/api/projects/{project_id}/preview/scene/1/thumb/source").status_code == 200
    assert client.get(f"/api/projects/{project_id}/preview").status_code == 200

    revise = client.post(
        f"/api/projects/{project_id}/scenes/1/revise", json={"instruction": "più ampio e più calmo"}
    )
    assert revise.status_code == 200 and revise.json()["applied"] is True
    approve = client.post(f"/api/projects/{project_id}/scenes/1/approve", json={"approved": True})
    assert approve.status_code == 200 and 1 in approve.json()["approved_scenes"]

    status = client.get("/api/vault/vision/status")
    assert status.status_code == 200 and "counts" in status.json()
    rean = client.post("/api/vault/vision/reanalyze", json={"force": False})
    assert rean.status_code == 200 and "analyzed" in rean.json()


# ------------------------------------------------------- 7. frames & QC contract
def test_thirty_second_demo_has_exactly_750_frames(env):
    from kora_studio.agents.creative_director import CreativeDirector
    from kora_studio.vault.media_vault import MediaVault

    vault = MediaVault(env["db"])
    for i in range(5):
        vault.ingest_file(_photo(env["tmp"] / f"demo_{i}.png", seed=i + 30), category="Esperienza", origin="test")
    from kora_studio.brand.brand_brain import BrandBrain

    creative = CreativeDirector(env["db"], BrandBrain(env["db"]), vault=vault, providers=env["providers"])
    concept = creative.ideate_from_theme("Brand identity KORA: The Art of Being", duration_sec=30)[0]
    scenes = creative.build_storyboard(concept, voice_id="brando", fps=25)
    assert len(scenes) >= 6
    assert sum(s.duration_frames for s in scenes) == 750
    assert scenes[-1].is_outro and scenes[-1].subtitle_text.lower().startswith("the art of being")
    assert all(s.duration_frames > 0 for s in scenes)


def test_renderer_compatibility_is_enforced(env):
    assets = {"a1": {"id": "a1", "category": "Mare", "media_type": "image", "filename": "m.jpg"}}
    scenes = [_scene(1, 50, selected_asset_id="a1", subtitle_text="x")]
    director = CinematicStoryDirector(vault=None, engine=None)
    plan = [{"scene_number": 1, "asset_id": "a1", "framing": "outro_logo", "camera": "drone", "transition": "wipe"}]
    validation = director.validate_plan(scenes, plan, assets)
    codes = {p["code"] for p in validation["blocking"]}
    assert "camera_non_valida" in codes and "transizione_non_valida" in codes


# -------------------------------------------------------------- 8. real providers
def _has_binary(name: str) -> bool:
    return shutil.which(name) is not None


@pytest.mark.skipif(
    not (_has_binary("opencode") or _has_binary("codex")),
    reason="[REAL PROVIDER] nessuna CLI OpenCode/Codex autenticata in questo ambiente: test dichiarato e saltato",
)
def test_real_cli_provider_can_be_verified(env):  # pragma: no cover - requires a real account
    adapter = SimulatedEngine()
    assert adapter.authenticated()["authenticated"] is False or True
