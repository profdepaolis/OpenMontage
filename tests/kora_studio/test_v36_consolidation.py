"""v3.6 — consolidamento: stati QC, blocchi regia, manifest audio, ripristino.

Ogni verifica qui è deterministica e locale: niente chiamate a provider,
niente render lunghi. Dove una capacità non è verificabile si dichiara.
"""

from __future__ import annotations

import json
import pathlib
import zipfile

import pytest
from PIL import Image

from kora_studio.agents.director import KoraDirector
from kora_studio.agents.voice_director import manifest_hash, voice_segment_manifest
from kora_studio.backup.backup_manager import BackupManager
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload, QC_STATUSES, QualityCheckItem


@pytest.fixture()
def director(isolated_env) -> KoraDirector:
    db = Database(pathlib.Path(isolated_env["db_path"]))
    d = KoraDirector(db=db)
    tmp = pathlib.Path(isolated_env["tmp_path"])
    for index, name in enumerate(["mare.png", "tavolo.png", "pergolato.png", "colazione.png"]):
        path = tmp / name
        Image.new("RGB", (1080, 1920), (25 + index * 20, 80, 95)).save(path)
        d.vault.ingest_file(
            path,
            origin="test",
            authenticity_status="autentico",
            usage_rights="proprietario_kora",
            category="Mare",
            tags=["mare"],
        )
    isolated_env["director"] = d
    return d


@pytest.fixture()
def project_id(director) -> str:
    design = director.design_reel(
        CreativeRequestPayload(theme="La bellezza non fa rumore", duration_sec=20.0, voice_id="brando")
    )
    project = director.create_project_from_design(design, theme="La bellezza non fa rumore")
    return project["id"]


# ------------------------------------------------------------------ stati QC
def test_qc_status_is_derived_and_limited_to_four_values():
    ok = QualityCheckItem(category="technical", name="prova", passed=True)
    ko = QualityCheckItem(category="technical", name="prova", passed=False)
    na = QualityCheckItem(category="technical", name="prova", passed=True, status="non_applicabile")
    unknown = QualityCheckItem(category="technical", name="prova", passed=True, status="inventato")
    assert ok.status == "superato"
    assert ko.status == "non_superato"
    assert na.status == "non_applicabile"
    assert unknown.status == "superato"
    assert set(QC_STATUSES) == {"superato", "non_superato", "non_applicabile", "non_verificato"}
    assert na.status_label == "Non applicabile"
    assert ko.blocks_delivery() is True
    assert QualityCheckItem(
        category="technical", name="x", passed=False, severity="warning"
    ).blocks_delivery() is False


def _silent_reel(dest: pathlib.Path, seconds: float = 2.0, with_audio: bool = True) -> pathlib.Path:
    from kora_studio.media_tools import run_ffmpeg

    frames = int(round(seconds * 25))
    cmd = [
        "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=s=1080x1920:r=25:d={seconds}",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=320:duration={seconds}"]
    cmd += ["-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "28", "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=9", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest"]
    cmd.append(str(dest))
    run_ffmpeg(cmd, timeout=120)
    assert dest.is_file()
    return dest


def _fake_scenes(frames: int = 50, spoken: str = ""):
    from kora_studio.db.models import StoryboardScene

    return [
        StoryboardScene(
            scene_number=1,
            start_frame=0,
            end_frame=frames,
            duration_frames=frames,
            start_sec=0.0,
            end_sec=frames / 25,
            duration_sec=frames / 25,
            narrative_goal="prova",
            spoken_text=spoken,
            subtitle_text=spoken,
        )
    ]


def test_qc_marks_sync_not_applicable_without_voiceover(director, tmp_path):
    clip = _silent_reel(tmp_path / "voce_assente.mp4")
    report = director.qc.inspect_reel(
        clip, _fake_scenes(spoken=""), expected_duration_sec=2.0, expect_audio=True
    )
    sync = next(c for c in report.checks if "Sottotitoli sincronizzati" in c.name)
    assert sync.status == "non_applicabile"
    assert report.metrics["qc_status_counts"].get("non_applicabile", 0) >= 1

    # testo scritto in storyboard ≠ voce prodotta: senza traccia reale resta non applicabile
    report2 = director.qc.inspect_reel(
        clip,
        _fake_scenes(spoken="Il mare tace"),
        expected_duration_sec=2.0,
        expect_audio=True,
        voiceover_present=False,
    )
    sync2 = next(c for c in report2.checks if "Sottotitoli sincronizzati" in c.name)
    assert sync2.status == "non_applicabile"


def test_qc_measures_real_loudness(director, tmp_path):
    clip = _silent_reel(tmp_path / "loudness.mp4")
    report = director.qc.inspect_reel(
        clip, _fake_scenes(spoken="Il mare tace"), expected_duration_sec=2.0, expect_audio=True
    )
    loud = next(c for c in report.checks if "Loudness del master" in c.name)
    assert loud.status in {"superato", "non_superato", "non_verificato"}
    if loud.status != "non_verificato":
        assert -18.0 <= report.metrics["lufs"] <= -10.0


def test_qc_audio_checks_are_not_applicable_for_silent_export(director, tmp_path):
    clip = _silent_reel(tmp_path / "senza_audio.mp4", with_audio=False)
    report = director.qc.inspect_reel(
        clip, _fake_scenes(spoken=""), expected_duration_sec=2.0, expect_audio=False
    )
    for name in ("Audio AAC presente", "Nessun clipping audio", "Loudness del master"):
        item = next(c for c in report.checks if name in c.name)
        assert item.status == "non_applicabile", name
        assert item.blocks_delivery() is False
    # le uniche bocciature restano quelle narrative (logo/outro assenti nella clip di prova)
    blocking = [c.name for c in report.checks if c.blocks_delivery()]
    assert all("logo" in name.lower() or "asset" in name.lower() for name in blocking), blocking


# ------------------------------------------------------------ blocchi regia
def test_direction_lock_covers_motion_speed(director, project_id):
    director.scene_editor.set_locks(project_id, 1, {"direction": True})
    blocked = director.scene_editor.update_scene(project_id, 1, {"motion_speed": 1.4})
    assert blocked.get("blocked") is True
    assert blocked.get("reason")

    director.scene_editor.set_locks(project_id, 1, {"direction": False})
    applied = director.scene_editor.update_scene(project_id, 1, {"motion_speed": 1.4})
    assert applied.get("ok") is not False
    scene = director.scene_editor.get_scene(project_id, 1) if hasattr(director.scene_editor, "get_scene") else None
    if scene:
        assert scene["motion_speed"] == 1.4


def test_scenario_lock_variants_still_protect_their_fields(director, project_id):
    director.scene_editor.set_locks(project_id, 2, {"voice": True, "duration": True})
    assert director.scene_editor.update_scene(project_id, 2, {"spoken_text": "testo nuovo"}).get("blocked")
    assert director.scene_editor.update_scene(project_id, 2, {"duration_sec": 5.0}).get("blocked")


# ------------------------------------------------------- manifest voce
def test_voice_manifest_hash_depends_on_text_voice_provider_params():
    base = dict(voice_id="brando", text="Il mare al tramonto", source_used="elevenlabs_api")
    a = manifest_hash(voice_segment_manifest(**base))
    b = manifest_hash(voice_segment_manifest(**{**base, "text": "Il mare all'alba"}))
    c = manifest_hash(voice_segment_manifest(**{**base, "voice_id": "tiziana"}))
    d = manifest_hash(voice_segment_manifest(**{**base, "source_used": "edge_tts_fallback"}))
    e = manifest_hash(
        voice_segment_manifest(**base, params={"speech_rate": 1.1})
    )
    assert len({a, b, c, d, e}) == 5


def test_voice_manifest_declares_provider_and_model():
    manifest = voice_segment_manifest(voice_id="brando", text="Ciao", source_used="openai_tts")
    assert manifest["provider"] == "openai_api"
    assert manifest["model"]
    assert manifest["text_sha256"]
    # la sola timeline non entra nell'hash: stesso testo, stesso hash
    twice = voice_segment_manifest(voice_id="brando", text="Ciao", source_used="openai_tts")
    assert manifest_hash(manifest) == manifest_hash(twice)


# ------------------------------------------------- miniature da clip video
def test_thumbnail_of_a_video_asset_uses_first_frame(director, tmp_path):
    """Difetto 3.6: una clip del Vault non è un'immagine. La miniatura deve funzionare."""
    from kora_studio.media_tools import run_ffmpeg

    clip = tmp_path / "clip.mp4"
    run_ffmpeg(
        [
            "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=540x960:r=25:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
        ],
        timeout=120,
    )
    out = tmp_path / "thumb.png"
    result = director.preview_renderer._thumb(clip, out)
    assert result.is_file()
    from PIL import Image

    with Image.open(result) as img:
        assert img.size == (270, 480)


# ------------------------------------------------------------ ripristino
def test_restore_maps_folders_and_is_reversible(isolated_env, tmp_path):
    db = Database(pathlib.Path(isolated_env["db_path"]))
    manager = BackupManager(db)
    backup_path = tmp_path / "handmade_backup.zip"
    with zipfile.ZipFile(backup_path, "w") as zf:
        zf.writestr("projects/prj_test/storyboard.json", json.dumps({"ok": True}))
        zf.writestr("originals/foto.jpg", b"immagine")
        zf.writestr("backup_manifest.json", json.dumps({"created_at": "2026-11-22"}))
    result = manager.restore_backup(backup_path, safety_backup=False)
    data_dir = pathlib.Path(result["restored_to"])
    assert (data_dir / "projects" / "prj_test" / "storyboard.json").is_file()
    assert (data_dir / "vault" / "originals" / "foto.jpg").is_file()
    assert result["restored_files"] >= 2
    assert result["reversible"] is False  # nessun backup di sicurezza richiesto in questo test


def test_restore_rejects_unsafe_paths(isolated_env, tmp_path):
    db = Database(pathlib.Path(isolated_env["db_path"]))
    manager = BackupManager(db)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", "no")
    with pytest.raises(ValueError):
        manager.restore_backup(evil, safety_backup=False)


# --------------------------------------------------------------- ideazione
def test_ideation_budget_follows_saved_timeout(director):
    ideation = director.creative.ideation
    director.db.set_preference("ai_model_config", {"timeouts": {"ideation": 45}})
    assert ideation._budget_sec() == 45.0
    director.db.set_preference("ai_model_config", {"timeouts": {"ideation": 30}})
    assert ideation._budget_sec() == 30.0
    director.db.set_preference("ai_model_config", {})
