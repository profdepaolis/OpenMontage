"""Tests for AI Provider Manager, SecretVault encryption, CostGuard, and Anti-Repetition."""

from __future__ import annotations

import pytest

from kora_studio.agents.creative_director import AntiRepetitionEngine, CreativeDirector
from kora_studio.brand.brand_brain import BrandBrain
from kora_studio.config import SecretVault
from kora_studio.db.database import Database
from kora_studio.providers.cost_guard import CostGuard
from kora_studio.providers.registry import ProviderManager
from kora_studio.vault.media_vault import MediaVault


def test_secret_vault_encrypts_and_masks(isolated_env):
    sv = SecretVault(isolated_env["master_key_path"])
    secret = "sk-test-1234567890abcdef"
    token = sv.encrypt(secret)
    assert token.startswith("v1:")
    assert secret not in token
    assert sv.decrypt(token) == secret
    assert sv.mask(secret) == "sk-t...cdef"


def test_cost_guard_defaults_to_zero_and_caps_at_20_eur(isolated_env):
    db = Database(isolated_env["db_path"])
    guard = CostGuard(db)
    b = guard.get_budget_settings()
    assert b["paid_budget_eur"] == 0.0
    assert b["monthly_cap_eur"] == 20.0

    # Paid call blocked without explicit authorization & budget
    decision = guard.check_authorization(0.50, explicit_user_authorization=False)
    assert not decision["authorized"]

    # Free call always allowed
    assert guard.check_authorization(0.0, explicit_user_authorization=False)["authorized"]

    # Cap cannot exceed 20 EUR
    with pytest.raises(ValueError):
        guard.update_budget_settings(paid_budget_eur=25.0)
    with pytest.raises(ValueError):
        guard.update_budget_settings(monthly_cap_eur=35.0)

    guard.update_budget_settings(paid_budget_eur=5.0, monthly_cap_eur=15.0)
    assert guard.check_authorization(1.0, explicit_user_authorization=True)["authorized"]
    assert not guard.check_authorization(6.0, explicit_user_authorization=False)["authorized"]
    assert not guard.check_authorization(16.0, explicit_user_authorization=True)["authorized"]


def test_provider_manager_registry_and_zero_spend_selection(isolated_env):
    db = Database(isolated_env["db_path"])
    pm = ProviderManager(db)

    providers = pm.list_providers()
    assert len(providers) >= 7
    # No fake balances: unconfigured/unverified providers report Non verificabile
    openai_p = next(p for p in providers if p["id"] == "openai_api")
    assert openai_p["quota_status"] == "Non verificabile"
    assert openai_p["operational_status"] == "non_configurato"

    # Local FFmpeg/Pillow is free & operational
    local_p = next(p for p in providers if p["id"] == "local_ffmpeg_pillow")
    assert local_p["operational_status"] == "operativo"

    # Best provider for image_editing picks free local first when budget is 0
    selected = pm.select_provider_for("image_editing")
    assert selected["selected"] is not None
    assert selected["selected"]["id"] == "local_ffmpeg_pillow"


def test_creative_director_generates_3_distinct_concepts_and_anti_repetition(isolated_env):
    db = Database(isolated_env["db_path"])
    brain = BrandBrain(db)
    vault = MediaVault(db)
    vault.seed_kora_authentic_assets()
    cd = CreativeDirector(db, brain, vault)

    concepts = cd.ideate_from_theme(
        theme="Mattina lenta sul mare a Policoro",
        duration_sec=20.0,
    )
    assert len(concepts) == 3
    titles = {c.title for c in concepts}
    assert len(titles) == 3
    for c in concepts:
        assert 0.0 <= c.originality_score <= 1.0
        assert c.voiceover_script
        assert c.hook

    # Anti-repetition records past project and detects duplicates
    anti = AntiRepetitionEngine(db)
    score_before, _ = anti.evaluate_originality("Mattina al mare", "Il respiro del mattino", ["Frase uno"])
    assert score_before == 1.0
    anti.record_reel(
        project_id="prj_old",
        title="Mattina al mare",
        theme="Mattina",
        hook="Il respiro del mattino",
        ending="The Art of Being",
        phrases=["Frase uno"],
        asset_ids=[],
        music_id=None,
        preset="KORA Cinematic Identity",
    )
    score_after, reasons = anti.evaluate_originality("Mattina al mare", "Il respiro del mattino", ["Frase uno"])
    assert score_after < 1.0
    assert len(reasons) >= 1


def test_voice_director_never_reuses_old_recording_for_new_script(isolated_env):
    from kora_studio.agents.voice_director import VoiceDirector
    from kora_studio.db.models import StoryboardScene

    db = Database(isolated_env["db_path"])
    vault = MediaVault(db)
    vault.seed_kora_authentic_assets()
    pm = ProviderManager(db)
    vd = VoiceDirector(vault, pm)

    new_scenes = [
        StoryboardScene(
            scene_number=1,
            start_frame=0,
            end_frame=100,
            duration_frames=100,
            start_sec=0.0,
            end_sec=4.0,
            duration_sec=4.0,
            narrative_goal="Hook nuovo",
            spoken_text="Questa mattina la sabbia di Policoro racconta una storia nuova.",
            voice_id="brando",
        )
    ]
    res = vd.prepare_voiceover(
        scenes=new_scenes,
        voice_id="brando",
        work_dir=isolated_env["tmp_path"],
        explicit_paid_authorization=False,
        project_id="prj_new_123",
    )
    # Without ElevenLabs/OpenAI/Piper/Edge active, must NOT paste old voce_brando.mp3 onto a different script
    assert res["audio_path"] is None
    assert res["status"] == "richiede_sintesi_o_registrazione"
    assert res["word_check"]["passed"] is False


def test_gemini_key_shared_with_veo_and_health_route(isolated_env):
    from fastapi.testclient import TestClient
    from kora_studio.agents.ai_media_lab import AIMediaLab
    from kora_studio.api.app import app

    db = Database(isolated_env["db_path"])
    vault = MediaVault(db)
    brain = BrandBrain(db)
    pm = ProviderManager(db)
    pm.set_api_key("gemini_api", "AIzaSyTestFakeKeyForUnitTest0001")
    assert pm.get_decrypted_key("google_veo_api") == "AIzaSyTestFakeKeyForUnitTest0001"

    lab = AIMediaLab(vault, brain, pm)
    # Without explicit paid authorization, Veo must pause safely under 0 EUR default budget
    res = lab.execute_veo_video(
        mode="text_to_video",
        prompt="Onde calme sullo Ionio all'alba",
        duration_sec=5,
        explicit_authorization=False,
    )
    assert res["status"] == "requires_authorization"

    client = TestClient(app)
    health_res = client.get("/health")
    assert health_res.status_code == 200
    assert health_res.json()["ok"] is True

    # Verify new multi-engine providers (fal_ai, qwen_wan_api, deepseek_api, claude_api) and gating
    provider_ids = {p["id"] for p in pm.list_providers()}
    assert {"fal_ai", "qwen_wan_api", "deepseek_api", "claude_api", "huggingface_api"}.issubset(provider_ids)
    fal_res = lab.execute_ai_video(
        engine="fal_ai",
        mode="text_to_video",
        prompt="Onde calme sullo Ionio all'alba",
        explicit_authorization=False,
    )
    assert fal_res["status"] == "requires_authorization"


def test_dynamic_scene_count_sync_and_hyperframes_captions(isolated_env):
    from kora_studio.agents.director import KoraDirector
    from kora_studio.agents.hyperframes_captions import (
        build_caption_segments_for_scene,
        export_hyperframes_workspace,
    )

    db = Database(isolated_env["db_path"])
    director = KoraDirector(db=db)
    director.vault.seed_kora_authentic_assets()

    # 1. Dynamic scene count for 30s Reel (> 6 scenes!)
    concepts = director.creative.ideate_from_theme(
        "Tramonto sul mare di Policoro", duration_sec=30.0
    )
    scenes_30 = director.creative.build_storyboard(concepts[0], voice_id="brando", fps=25)
    assert len(scenes_30) >= 8
    assert sum(s.duration_frames for s in scenes_30) == 750

    # 2. Hyperframes caption segments + workspace export
    sub_dir = isolated_env["tmp_path"] / "hf_subs"
    segs = build_caption_segments_for_scene(
        scenes_30[0], "hyperframes_word_highlight", sub_dir
    )
    assert len(segs) >= 1
    assert all(p["png"].is_file() for p in segs)

    ws = export_hyperframes_workspace(
        scenes_30,
        "hyperframes_kinetic",
        "KORA Test",
        isolated_env["tmp_path"] / "hf_ws",
    )
    assert (isolated_env["tmp_path"] / "hf_ws" / "index.html").is_file()
    assert (isolated_env["tmp_path"] / "hf_ws" / "DESIGN.md").is_file()
    assert ws["index_html"].endswith("index.html")

    # 3. Project audio & caption sync
    proj = director.create_project(
        title="Sync Test",
        theme="Mare",
        mode="theme",
        preset="KORA Cinematic Identity",
        production_mode="GUIDATA",
        voice_id="brando",
        target_duration_sec=30.0,
        concept=concepts[0],
    )
    synced = director.sync_project_audio_and_caption(
        proj["id"],
        caption_style="hyperframes_kinetic",
        music_start_sec=4.5,
        music_volume=0.22,
        voice_volume=1.1,
        auto_sync_scenes_to_voice=True,
    )
    assert synced["preferences"]["caption_style"] == "hyperframes_kinetic"
    assert synced["audio_plan"]["music_start_sec"] == 4.5
    assert synced["audio_plan"]["music_volume"] == 0.22


def test_elevenlabs_reason_preservation_and_full_hyperframes_api(isolated_env):
    from fastapi.testclient import TestClient
    from kora_studio.agents.hyperframes_captions import (
        build_hyperframes_vf_filters,
        normalize_hyperframes_config,
        recommend_hyperframes_for_theme,
        render_title_card_png,
    )
    from kora_studio.agents.voice_director import VoiceDirector
    from kora_studio.api.app import create_app
    from kora_studio.db.models import StoryboardScene

    db = Database(isolated_env["db_path"])
    vault = MediaVault(db)
    vault.seed_kora_authentic_assets()
    pm = ProviderManager(db)
    pm.set_api_key("elevenlabs_api", "sk_fake_test_elevenlabs_key")
    db.set_preference("elevenlabs_voice_brando", "fake_brando_voice_id")
    # Simulate user raising monthly_cap_eur to 5 while leaving paid_budget_eur at 0!
    pm.cost_guard.update_budget_settings(paid_budget_eur=0.0, monthly_cap_eur=5.0)

    vd = VoiceDirector(vault, pm)
    sc = StoryboardScene(
        scene_number=1,
        start_frame=0,
        end_frame=100,
        duration_frames=100,
        start_sec=0.0,
        end_sec=4.0,
        duration_sec=4.0,
        narrative_goal="Test",
        spoken_text="Benvenuti al tramonto di Policoro",
        voice_id="brando",
    )
    res = vd.prepare_voiceover(
        [sc], "brando", isolated_env["tmp_path"] / "vtest", explicit_paid_authorization=False
    )
    # Must explicitly mention Cost Guard / Budget autorizzato (never pretend ElevenLabs wasn't configured!)
    assert "Budget" in (res.get("substitution_notice") or "") or "Cost Guard" in (res.get("substitution_notice") or "")

    # Smart theme recommendations + full Hyperframes filters + title card PNG
    rec = recommend_hyperframes_for_theme("Aperitivo al tramonto con amici")
    assert rec["recommended_preset"] == "KORA Experience"
    assert rec["recommended_hyperframes"]["caption_style"] == "hyperframes_kinetic"
    assert len(rec["theme_suggestions"]) >= 3

    hf_cfg = normalize_hyperframes_config(rec["recommended_hyperframes"])
    vf = build_hyperframes_vf_filters(hf_cfg)
    assert any("noise=" in f for f in vf)
    assert any("vignette=" in f for f in vf)

    tc_png = isolated_env["tmp_path"] / "title_card.png"
    render_title_card_png("Tramonto KORA", tc_png, style=hf_cfg["title_card_intro"])
    assert tc_png.is_file() and tc_png.stat().st_size > 1000

    # API endpoints: /api/hyperframes/recommend + /api/creative/suggest + /api/voices/{id}/sample + /health hyperframes_options
    app = create_app(db_path=isolated_env["db_path"], auto_seed=True)
    client = TestClient(app)
    h = client.get("/health").json()
    assert "hyperframes_options" in h
    assert "grain_overlay" in h["hyperframes_options"]

    sug = client.post("/api/hyperframes/recommend", json={"theme": "poche parole e silenzio sul mare"})
    assert sug.status_code == 200
    assert sug.json()["recommended_preset"] == "KORA Editorial"

    sample = client.get("/api/voices/brando/sample?engine=vault")
    assert sample.status_code == 200
    assert len(sample.content) > 1000



def test_gemini_model_resolution_survives_404_and_uses_available_model(isolated_env, monkeypatch):
    """Se il modello configurato risponde 404, il resolver scopre via ListModels un modello valido e lo usa."""
    import json as _json

    import requests as _requests

    from kora_studio.agents.creative_director import CreativeDirector
    from kora_studio.db.database import Database
    from kora_studio.db.models import CreativeRequestPayload
    from kora_studio.providers.registry import ProviderManager

    db = Database(isolated_env["db_path"])
    brain = BrandBrain(db)
    brain.ensure_initialized()
    vault = MediaVault(db)
    vault.seed_kora_authentic_assets()
    pm = ProviderManager(db)
    pm.ensure_seeded()
    pm.set_api_key("gemini_api", "fake-gemini-key")
    pm.cost_guard.update_budget_settings(paid_budget_eur=5.0, monthly_cap_eur=5.0)
    director = CreativeDirector(db, brain, vault, pm)

    concepts_payload = {
        "concepts": [
            {
                "title": f"Proposta Gemini {i}",
                "central_idea": f"Idea {i} sul mattino mediterraneo",
                "message": "La luce che accarezza il pergolato.",
                "primary_emotion": "Quietudine",
                "hook": f"C'è un'ora in cui il mare tace ({i}).",
                "narrative_arc": "Apertura, sviluppo, chiusura luminosa",
                "ending": "Il payoff KORA.",
                "voiceover_script": f"C'è un'ora, davanti al mare, in cui tutto rallenta. Silenzio numero {i}.",
                "visual_mood": "Avorio, ombre lunghe sotto il pergolato",
                "moodboard_tags": ["avorio", "mattino"],
                "required_materials": [],
                "available_asset_ids": [],
                "missing_materials": [],
                "objective": "Valorizzare l'identità KORA",
                "recommended_voice_id": "brando",
                "duration_sec": 20,
                "feasibility_score": 95,
                "originality_score": 40,
                "preset": "KORA Cinematic Identity",
            }
            for i in range(4)
        ]
    }

    calls: list[str] = []

    class _Resp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def _fake_get(url, params=None, headers=None, timeout=None):
        assert "models" in url
        return _Resp(
            200,
            {
                "models": [
                    {
                        "name": "models/gemini-2.0-flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/gemini-2.5-flash-image",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/text-embedding-004",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
        )

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        if "gemini-2.5-flash:" in url:
            return _Resp(404, {"error": {"message": "models/gemini-2.5-flash is not found"}})
        if "gemini-2.0-flash:" in url:
            return _Resp(
                200,
                {"candidates": [{"content": {"parts": [{"text": _json.dumps(concepts_payload)}]}}]},
            )
        return _Resp(404, {"error": {"message": "not found"}})

    monkeypatch.setattr(_requests, "get", _fake_get)
    monkeypatch.setattr(_requests, "post", _fake_post)

    req = CreativeRequestPayload(
        theme="Il mattino sul mare di Policoro", duration_sec=20, budget_eur=1.0
    )
    env = director.ideate_from_request(req)

    assert env.generation_engine == "gemini_api", env.fallback_reason
    assert env.fallback_reason is None
    assert len(env.concepts) == 3
    assert any("gemini-2.5-flash:" in c for c in calls)
    assert any("gemini-2.0-flash:" in c for c in calls)
    # il modello funzionante viene memorizzato per i run successivi
    assert pm.get_provider("gemini_api")["models"][0] == "gemini-2.0-flash"


def test_gemini_invalid_key_produces_readable_fallback_reason(isolated_env, monkeypatch):
    """Una chiave non autorizzata non espone traceback grezzi: messaggio leggibile + fallback locale."""
    import requests as _requests

    from kora_studio.agents.creative_director import CreativeDirector
    from kora_studio.db.database import Database
    from kora_studio.db.models import CreativeRequestPayload
    from kora_studio.providers.registry import ProviderManager

    db = Database(isolated_env["db_path"])
    brain = BrandBrain(db)
    brain.ensure_initialized()
    vault = MediaVault(db)
    vault.seed_kora_authentic_assets()
    pm = ProviderManager(db)
    pm.ensure_seeded()
    pm.set_api_key("gemini_api", "fake-gemini-key")
    pm.cost_guard.update_budget_settings(paid_budget_eur=5.0, monthly_cap_eur=5.0)
    director = CreativeDirector(db, brain, vault, pm)

    class _Resp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(_requests, "get", lambda *a, **k: _Resp(403, {"error": {"message": "PERMISSION_DENIED"}}))
    monkeypatch.setattr(
        _requests,
        "post",
        lambda *a, **k: _Resp(403, {"error": {"message": "API key not valid. Please pass a valid API key."}}),
    )

    env = director.ideate_from_request(CreativeRequestPayload(theme="Aperitivo al tramonto", duration_sec=20, budget_eur=1.0))
    assert env.generation_engine == "deterministic_offline_fallback"
    assert env.concepts
    reason = env.fallback_reason or ""
    assert "Motore deterministico locale KORA attivo" in reason
    assert "Google Gemini" in reason
    assert "non autorizzata" in reason
