"""KORA STUDIO Upgrade 2.0 — 10+ Acceptance Scenarios & Security Verification."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from kora_studio.agents.ai_media_lab import AIMediaLab
from kora_studio.agents.creative_director import CreativeDirector
from kora_studio.agents.director import KoraDirector
from kora_studio.api.app import create_app
from kora_studio.brand.brand_brain import BrandBrain
from kora_studio.config import DEFAULT_FPS
from kora_studio.db.database import Database
from kora_studio.db.models import (
    AuthenticityStatus,
    CreativeRequestPayload,
    ProductionMode,
    StoryboardScene,
)
from kora_studio.providers.registry import ProviderManager
from kora_studio.vault.media_vault import MediaVault
from kora_studio.vault.sound_library import SoundLibrary


def _seed_test_vault(tmp_path: Path, vault: MediaVault, sound_lib: SoundLibrary) -> None:
    """Create diverse realistic test assets + KORA logo + sound library."""
    assets_dir = tmp_path / "seed_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        ("alba_beach.jpg", (235, 200, 160), "Alba", ["alba", "mare", "spiaggia"]),
        ("mare_lido.jpg", (50, 120, 155), "Mare", ["mare", "acqua", "onde"]),
        ("aperitivo_deck.jpg", (200, 155, 110), "Cocktail", ["cocktail", "tramonto", "aperitivo"]),
        ("gazebo_lounge.jpg", (215, 205, 185), "Gazebo", ["gazebo", "architettura", "lino"]),
        ("cucina_piatto.jpg", (210, 175, 130), "Food", ["cucina", "pranzo", "piatto"]),
        ("tramonto_oro.jpg", (220, 140, 85), "Tramonto", ["tramonto", "oro", "sera"]),
        ("ospiti_relax.jpg", (180, 165, 145), "Ospitalità", ["ospitalità", "relax", "estate"]),
    ]
    for fname, color, cat, tags in specs:
        p = assets_dir / fname
        im = Image.new("RGB", (1080, 1920), color)
        draw = ImageDraw.Draw(im)
        draw.rectangle([100, 200, 980, 1720], outline=(245, 240, 230), width=8)
        im.save(p, "JPEG", quality=90)
        vault.ingest_file(
            p,
            origin="seed_kora_reale",
            authenticity_status=AuthenticityStatus.AUTENTICO.value,
            usage_rights="autorizzato_kora",
            category=cat,
            tags=tags,
            original_filename=fname,
        )

    logo_path = assets_dir / "LOGO KORA.PNG"
    logo_im = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(logo_im)
    draw.text((220, 85), "KORA", fill=(250, 247, 242, 255))
    logo_im.save(logo_path, "PNG")
    vault.ingest_file(
        logo_path,
        origin="seed_kora_brand",
        authenticity_status=AuthenticityStatus.AUTENTICO.value,
        usage_rights="autorizzato_kora",
        category="Brand",
        tags=["logo", "kora", "brand"],
        original_filename="LOGO KORA.PNG",
    )
    sound_lib.ensure_seeded()


def _build_director(isolated_env: dict) -> KoraDirector:
    db = Database(isolated_env["db_path"])
    brain = BrandBrain(db)
    brain.ensure_initialized()
    vault = MediaVault(db)
    sound_lib = SoundLibrary(db)
    _seed_test_vault(isolated_env["tmp_path"], vault, sound_lib)
    providers = ProviderManager(db)
    providers.ensure_seeded()
    return KoraDirector(db)


def test_scenario_1_four_original_themes_and_hyperframes_separation(isolated_env):
    """Scenario 1: POST /api/creative/suggest yields 4 distinct narrative proposals;
    POST /api/hyperframes/recommend handles Hyperframes separately."""
    _build_director(isolated_env)
    app = create_app(db_path=isolated_env["db_path"], auto_seed=False)
    client = TestClient(app)
    res = client.post("/api/creative/suggest", json={"duration_sec": 20})
    assert res.status_code == 200, res.text
    data = res.json()
    concepts = data["concepts"]
    assert len(concepts) == 4
    titles = {c["title"] for c in concepts}
    hooks = {c["hook"] for c in concepts}
    assert len(titles) == 4
    assert len(hooks) == 4
    for c in concepts:
        assert c["message"]
        assert c["voiceover_script"]
        assert c["recommended_voice_id"] in ("brando", "tiziana", "brando_tiziana", "none")
        assert c["objective"]
        assert len(c["available_asset_ids"]) >= 1
        assert c["generation_engine"]

    hf_res = client.post("/api/hyperframes/recommend", json={"theme": "tramonto oro", "duration_sec": 20})
    assert hf_res.status_code == 200
    assert "recommended_hyperframes" in hf_res.json()


def test_scenario_2_free_theme_outside_templates(isolated_env):
    """Scenario 2: Free custom theme outside fixed templates + extended request schema."""
    director = _build_director(isolated_env)
    req = CreativeRequestPayload(
        theme="Il silenzio salmastro di fine ottobre tra le vele chiuse di Policoro",
        objective="Raccontare l'anima autunnale e contemplativa del club",
        duration_sec=20,
        voice_id="tiziana",
        generation_policy="authentic_first",
        budget_eur=0.0,
        custom_instructions="Privilegia dettagli architettonici e onde lente.",
    )
    env = director.creative.ideate_from_request(req)
    assert len(env.concepts) == 3
    assert env.concepts[0].creative_request["objective"] == req.objective
    assert env.concepts[0].creative_request["theme"] == req.theme
    assert env.concepts[0].recommended_voice_id == "tiziana"


def test_scenario_3_20s_brand_identity_reel_and_frame_math(isolated_env):
    """Scenario 3: 20s Brand Identity Reel — exact 500 frames @ 25fps, non-cyclic assets, logo outro."""
    director = _build_director(isolated_env)
    req = CreativeRequestPayload(
        theme="KORA Beach Club: identità mediterranea e leggerezza",
        objective="Brand Identity",
        duration_sec=20,
        voice_id="brando",
    )
    concept = director.creative.ideate_from_request(req).concepts[0]
    scenes = director.creative.build_storyboard(concept, voice_id="brando")
    assert sum(s.duration_frames for s in scenes) == 20 * DEFAULT_FPS
    assert scenes[-1].is_outro is True
    non_outro_assets = [s.selected_asset_id for s in scenes[:-1] if s.selected_asset_id]
    assert len(set(non_outro_assets)) >= 4
    assert all(s.visual_rationale for s in scenes)
    assert all(s.shot_framing for s in scenes)


def test_scenario_4_30s_seasonal_reel(isolated_env):
    """Scenario 4: 30s Seasonal Reel — exact 750 frames @ 25fps."""
    director = _build_director(isolated_env)
    req = CreativeRequestPayload(
        theme="Stagione d'oro: tramonti e sapori sul mare Ionio",
        objective="Seasonal Campaign",
        duration_sec=30,
        voice_id="tiziana",
    )
    concept = director.creative.ideate_from_request(req).concepts[0]
    scenes = director.creative.build_storyboard(concept, voice_id="tiziana")
    assert sum(s.duration_frames for s in scenes) == 30 * DEFAULT_FPS
    assert scenes[-1].is_outro is True
    assert len(scenes) >= 7


def test_scenario_5_brando_tiziana_two_voice_mode(isolated_env, monkeypatch):
    """Scenario 5: Dual-voice brando_tiziana assigns alternating speakers & synthesizes per-scene audio."""
    import wave
    import numpy as np
    from kora_studio.agents.voice_director import VoiceDirector

    def _fake_piper(self, text: str, output_wav: Path):
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        sr = 22050
        dur = max(0.8, min(2.2, len(text) * 0.04))
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        tone = (np.sin(2 * np.pi * 220.0 * t) * 0.15 * 32767).astype(np.int16)
        with wave.open(str(output_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(tone.tobytes())
        return output_wav

    monkeypatch.setattr(VoiceDirector, "_synthesize_piper", _fake_piper)
    director = _build_director(isolated_env)
    req = CreativeRequestPayload(
        theme="Due sguardi sul mattino a Policoro",
        objective="Dialogo narrativo tra due voci",
        duration_sec=20,
        voice_id="brando_tiziana",
    )
    concept = director.creative.ideate_from_request(req).concepts[0]
    scenes = director.creative.build_storyboard(concept, voice_id="brando_tiziana")
    speakers = {s.voice_id for s in scenes if s.spoken_text.strip()}
    assert "brando" in speakers and "tiziana" in speakers

    out_dir = isolated_env["tmp_path"] / "voice_dual"
    out_dir.mkdir(parents=True, exist_ok=True)
    voice_res = director.voice_director.prepare_voiceover(
        scenes, "brando_tiziana", out_dir
    )
    assert voice_res["voice_id"] == "brando_tiziana"
    assert Path(voice_res["audio_path"]).is_file()
    assert len(voice_res["speech_durations_by_scene"]) >= 2


def test_scenario_6_reel_without_new_media_zero_budget(isolated_env):
    """Scenario 6: Strict conservative cascade without generating new paid media (budget 0 €)."""
    director = _build_director(isolated_env)
    req = CreativeRequestPayload(
        theme="Luce sulle verande",
        duration_sec=20,
        voice_id="brando",
        generation_policy="conservative_only",
        budget_eur=0.0,
    )
    concept = director.creative.ideate_from_request(req).concepts[0]
    scenes = director.creative.build_storyboard(concept, voice_id="brando")
    lab = AIMediaLab(director.vault, director.brand_brain, director.providers)
    for sc in scenes[:-1]:
        res = lab.resolve_scene_visual_cascade(
            sc,
            theme=req.theme,
            generation_policy="conservative_only",
            budget_eur=0.0,
        )
        assert res["estimated_eur"] == 0.0
        assert res["step"] in ("Originale", "crop", "animazione conservativa")


def test_scenario_7_external_providers_unavailable_offline_fallback(isolated_env, monkeypatch):
    """Scenario 7: With Ollama and all external APIs unreachable, explicit offline fallback activates cleanly."""
    director = _build_director(isolated_env)
    monkeypatch.setattr(
        CreativeDirector,
        "_call_llm_proposals",
        lambda self, req, count=4: (
            None,
            "deterministic_offline_fallback",
            "Ollama e provider cloud non raggiungibili: fallback deterministico offline attivo.",
        ),
    )
    env = director.creative.suggest_four_themes(
        CreativeRequestPayload(theme="", duration_sec=20, budget_eur=0.0)
    )
    assert len(env.concepts) == 4
    assert env.generation_engine == "deterministic_offline_fallback"
    assert env.fallback_reason is not None and len(env.fallback_reason) > 10


def test_scenario_8_and_9_single_scene_revision_and_version_restore(isolated_env):
    """Scenarios 8 & 9: Single-scene revision preserves others, and version restore reverts cleanly."""
    director = _build_director(isolated_env)
    concept = director.creative.surprise_me(duration_sec=20)[0]
    project = director.create_project(
        title="Revision Test",
        theme="Alba KORA",
        mode="theme",
        preset=concept.preset,
        production_mode=ProductionMode.GUIDATA.value,
        voice_id="brando",
        target_duration_sec=20,
        concept=concept,
    )
    pid = project["id"]
    orig_scenes = project["storyboard"]
    orig_scene2_text = orig_scenes[1]["spoken_text"]

    app = create_app(db_path=isolated_env["db_path"], auto_seed=False)
    client = TestClient(app)

    # Scenario 8: Modify only scene 2 via storyboard API
    revised = [dict(s) for s in orig_scenes]
    revised[1]["spoken_text"] = "Nuova battuta dedicata esclusivamente alla scena due."
    revised[1]["subtitle_text"] = "Nuova battuta scena due"
    r_upd = client.put(f"/api/projects/{pid}/storyboard", json={"scenes": revised})
    assert r_upd.status_code == 200, r_upd.text
    v2 = r_upd.json()
    assert v2["current_version"] == 2
    assert v2["storyboard"][1]["spoken_text"] == "Nuova battuta dedicata esclusivamente alla scena due."
    assert v2["storyboard"][0]["spoken_text"] == orig_scenes[0]["spoken_text"]

    # Scenario 9: Restore v1
    r_rest = client.post(f"/api/projects/{pid}/versions/1/restore")
    assert r_rest.status_code == 200, r_rest.text
    restored = r_rest.json()
    assert restored["storyboard"][1]["spoken_text"] == orig_scene2_text
    assert restored["current_version"] == 3


def test_scenario_10_export_and_real_video_verification(isolated_env):
    """Scenario 10: Full production pipeline renders a real 9:16 25fps MP4 Reel and passes QC."""
    director = _build_director(isolated_env)
    concept = director.creative.surprise_me(duration_sec=12)[0]
    project = director.create_project(
        title="Real Render QC Test",
        theme="Luce sul mare Ionio",
        mode="theme",
        preset=concept.preset,
        production_mode=ProductionMode.AUTO.value,
        voice_id="brando",
        target_duration_sec=12,
        concept=concept,
    )
    pid = project["id"]
    job = director.create_job(pid, "full_production")
    result = director.run_production_pipeline(pid, job["id"])

    proj_done = result["project"]
    output_mp4 = Path(proj_done["output_video_path"])
    assert output_mp4.is_file()
    assert output_mp4.stat().st_size > 200_000

    qc: dict = proj_done["qc_report"]
    assert qc["passed"] is True, qc
    by_name = {c["name"]: c["passed"] for c in qc["checks"]}
    assert by_name["Decodifica completa del video (zero pacchetti corrotti)"] is True
    assert by_name["Verifica effettiva dei fotogrammi"] is True
    assert by_name["Nessun clipping audio"] is True
    assert by_name["Integrità asset e logo originale"] is True
    assert by_name["Logo originale e payoff esatto 'The Art of Being'"] is True
    assert by_name["Coerenza tra voiceover e sottotitoli"] is True


def test_scenario_11_security_and_multimodal_vault(isolated_env):
    """Bonus Security + Multimodal Vault checks: da_verificare default, embeddings, local trust, 409 lock."""
    director = _build_director(isolated_env)

    # Multimodal Vault checks: unverified origin -> da_verificare, 64-dim embedding saved
    unverified_img = isolated_env["tmp_path"] / "unknown.jpg"
    Image.new("RGB", (800, 1200), (140, 175, 190)).save(unverified_img, "JPEG")
    ing = director.vault.ingest_file(
        unverified_img,
        origin="upload_esterno_non_verificato",
        authenticity_status=AuthenticityStatus.AUTENTICO.value,
        usage_rights="da_verificare",
        category="Mare",
    )
    assert ing["authenticity_status"] == AuthenticityStatus.DA_VERIFICARE.value
    assert len(ing["analysis"]["embedding"]) == 64
    assert "multimodal" in ing["analysis"]

    app = create_app(db_path=isolated_env["db_path"], auto_seed=False)
    client = TestClient(app)
    # Disallowed non-local host header on sensitive endpoint must return 403
    r_forbid = client.get("/api/providers", headers={"host": "evil.example.com"})
    assert r_forbid.status_code == 403

    # Duplicate concurrent job protection (409 Conflict)
    concept = director.creative.surprise_me(duration_sec=12)[0]
    p = director.create_project(
        title="Lock Test",
        theme="Alba",
        mode="theme",
        preset=concept.preset,
        production_mode=ProductionMode.AUTO.value,
        voice_id="none",
        target_duration_sec=12,
        concept=concept,
    )
    director.create_job(p["id"], "render")
    r_dup = client.post(f"/api/projects/{p['id']}/produce", json={"sync": False})
    assert r_dup.status_code == 409


def test_scenario_12_manual_elevenlabs_voiceover_roundtrip(isolated_env):
    """Scenario 12: copione esportabile -> MP3 generato fuori -> import -> storyboard riallineato e usato nel render."""
    import subprocess

    from fastapi.testclient import TestClient

    from kora_studio.api.app import create_app

    director = _build_director(isolated_env)
    concept = director.creative.surprise_me(duration_sec=20)[0]
    project = director.create_project(
        title="Voce manuale",
        theme="Mattino lento sul mare",
        mode="theme",
        preset=concept.preset,
        production_mode=ProductionMode.AUTO.value,
        voice_id="brando",
        target_duration_sec=20,
        concept=concept,
    )
    pid = project["id"]
    client = TestClient(create_app(db_path=isolated_env["db_path"], auto_seed=False))

    # 1) Il copione è esportabile (testo per scena + full text per ElevenLabs)
    r_script = client.get(f"/api/projects/{pid}/voiceover/script")
    assert r_script.status_code == 200, r_script.text
    script = r_script.json()
    assert script["char_count"] > 80
    assert len(script["segments"]) >= 3
    assert all(seg["text"] for seg in script["segments"])
    assert "elevenlabs" in script["elevenlabs_url"]

    # 2) L'utente genera un MP3 altrove: qui simuliamo con un WAV di 15,4s
    audio = isolated_env["tmp_path"] / "voce_manuale.wav"
    subprocess.run(
        [
            str(isolated_env.get("ffmpeg") or "ffmpeg"),
            "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=15.4",
            "-ar", "48000", "-ac", "2", str(audio),
        ],
        check=True,
    )

    # 3) Upload: storyboard riallineato alla durata reale della voce
    with audio.open("rb") as fh:
        r_up = client.post(
            f"/api/projects/{pid}/voiceover/upload",
            files={"file": ("voce_manuale.wav", fh, "audio/wav")},
        )
    assert r_up.status_code == 200, r_up.text
    payload = r_up.json()
    assert payload["voiceover"]["total_frames"] == 385  # 15.4s * 25fps
    assert abs(payload["project"]["target_duration_sec"] - 15.4) < 0.05
    assert sum(s["duration_frames"] for s in payload["project"]["storyboard"]) == 385

    # 4) Il render usa la voce importata invece della sintesi automatica
    scenes = [StoryboardScene(**s) for s in payload["project"]["storyboard"]]
    voice = director.voice_director.prepare_voiceover(
        scenes, "brando", isolated_env["tmp_path"] / "vo", project_id=pid
    )
    assert voice["source_used"] == "voce_importata_manuale"
    assert voice["status"] == "pronta"
    assert Path(voice["audio_path"]).is_file()

    # 5) Rimozione: torna la sintesi automatica
    r_del = client.delete(f"/api/projects/{pid}/voiceover")
    assert r_del.status_code == 200
    assert director.voice_director._project_voiceover_override(pid) is None
