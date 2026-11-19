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
