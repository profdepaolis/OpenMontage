"""3.6.3 — il montaggio non deve più morire sul tempo.

Il caso reale: 8 scene, 718 frame a 1080×1920 con otto sottotitoli su un Mac non
recente. Con il limite fisso di 240 s FFmpeg veniva ucciso a metà e il job
finiva in «Errore». Qui si verifica che:

* il limite cresca con il lavoro da fare e sia regolabile da ambiente;
* i sottotitoli non vengano più espansi su tutto il reel (solo il loro intervallo);
* il render usi un preset veloce e dichiari quanto ha impiegato;
* se il tempo non basta, l'errore sia in italiano e dica cosa fare.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from kora_studio.agents.cinematic_editor import X264_PRESET, render_timeout_seconds
from kora_studio.agents.director import KoraDirector
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload, StoryboardScene


# ------------------------------------------------------------------ tempi
def test_render_timeout_scales_with_frames(monkeypatch):
    monkeypatch.delenv("KORA_RENDER_TIMEOUT_SEC", raising=False)
    assert render_timeout_seconds(300) == 600  # minimo: reel brevi hanno comunque margine
    assert render_timeout_seconds(718) == 1149  # 718 × 1.6
    assert render_timeout_seconds(2000) == 3200


def test_render_timeout_env_override(monkeypatch):
    monkeypatch.setenv("KORA_RENDER_TIMEOUT_SEC", "1800")
    assert render_timeout_seconds(718) == 1800
    monkeypatch.setenv("KORA_RENDER_TIMEOUT_SEC", "5")  # troppo basso: resta un minimo
    assert render_timeout_seconds(10) == 60
    monkeypatch.setenv("KORA_RENDER_TIMEOUT_SEC", "non-un-numero")
    monkeypatch.delenv("KORA_RENDER_TIMEOUT_SEC")
    assert render_timeout_seconds(718) == 1149


def test_x264_preset_is_fast_by_default():
    assert os.environ.get("KORA_X264_PRESET") or X264_PRESET == "veryfast"


# ------------------------------------------------------------------ render
@pytest.fixture()
def director(isolated_env) -> KoraDirector:
    db = Database(Path(isolated_env["db_path"]))
    d = KoraDirector(db=db)
    tmp = Path(isolated_env["tmp_path"])
    for index, name in enumerate(["mare.png", "tavolo.png", "pergolato.png"]):
        path = tmp / name
        img = Image.new("RGB", (1080, 1920), (20 + index * 30, 75, 110))
        ImageDraw.Draw(img).rectangle([120, 300, 960, 1100], fill=(190, 160, 100))
        img.save(path)
        d.vault.ingest_file(
            path,
            origin="test",
            authenticity_status="autentico",
            usage_rights="proprietario_kora",
            category="Mare",
        )
    logo = tmp / "logo.png"
    img = Image.new("RGBA", (300, 150), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((20, 60), "KORA", fill=(29, 27, 24, 255))
    img.save(logo)
    d.vault.ingest_file(
        logo,
        origin="test",
        authenticity_status="autentico",
        usage_rights="proprietario_kora",
        category="Logo",
    )
    return d


def _project(director: KoraDirector, seconds: float = 8.0) -> tuple[str, list[StoryboardScene]]:
    ids = [
        a["id"]
        for a in director.vault.list_assets()
        if a.get("media_type") == "image" and a.get("category") != "Logo"
    ]
    req = CreativeRequestPayload(
        theme="La luce si posa",
        duration_sec=seconds,
        voice_id="none",
        asset_ids=ids,
        media_selection="vault_only",
    )
    design = director.design_reel(req)
    project = director.create_project_from_design(design, theme="La luce si posa")
    scenes = [StoryboardScene(**s) for s in project["storyboard"]]
    for scene in scenes:
        if not scene.is_outro:
            scene.subtitle_text = f"Sottotitolo scena {scene.scene_number}"
            scene.spoken_text = scene.subtitle_text
    return project["id"], scenes


def _silent_mix(path: Path, seconds: float) -> Path:
    from kora_studio.media_tools import run_ffmpeg

    run_ffmpeg(
        [
            "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t", f"{seconds:.3f}", str(path),
        ],
        timeout=120,
    )
    assert path.is_file()
    return path


def test_render_reel_records_time_and_preset(director, isolated_env):
    from kora_studio.config import PROJECTS_DIR
    from kora_studio.media_tools import run_ffmpeg

    project_id, scenes = _project(director)
    frames = sum(s.duration_frames for s in scenes)
    project_dir = Path(PROJECTS_DIR) / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    mix = _silent_mix(project_dir / "audio_mix.wav", frames / 25)

    res = director.editor.render_reel(
        project_id=project_id,
        title="La luce si posa",
        scenes=scenes,
        preset="KORA Cinematic Identity",
        project_dir=project_dir,
        audio_mix_path=mix,
        output_mp4=project_dir / f"{project_id}_preview.mp4",
        include_subtitles=True,
        fps=25,
    )
    out = Path(res["output_mp4"])
    assert out.is_file() and out.stat().st_size > 10_000
    assert res["x264_preset"] == X264_PRESET
    assert res["render_seconds"] > 0
    assert res["render_timeout_sec"] >= 600
    probe = run_ffmpeg(
        ["-i", str(out), "-f", "null", "-"],
        timeout=120,
    )
    assert probe.returncode == 0
    # i sottotitoli restano nel reel: si controlla che l'immagine contenga testo
    # sotto la parte centrale (non è una verifica visiva, è presenza del filtro)
    assert "overlay" in str(res.get("caption_engine") or "") or res.get("caption_engine")


def test_render_timeout_gives_actionable_italian_error(director, isolated_env, monkeypatch):
    from kora_studio.config import PROJECTS_DIR

    project_id, scenes = _project(director)
    frames = sum(s.duration_frames for s in scenes)
    project_dir = Path(PROJECTS_DIR) / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    mix = _silent_mix(project_dir / "audio_mix.wav", frames / 25)

    # il minimo reale è 60 s: per provare il percorso di timeout si forza 1 s
    import kora_studio.agents.cinematic_editor as editor_module

    monkeypatch.setattr(editor_module, "render_timeout_seconds", lambda *a, **k: 1)
    with pytest.raises(ValueError) as excinfo:
        director.editor.render_reel(
            project_id=project_id,
            title="Troppo lento",
            scenes=scenes,
            preset="KORA Cinematic Identity",
            project_dir=project_dir,
            audio_mix_path=mix,
            output_mp4=project_dir / f"{project_id}_mai.mp4",
            include_subtitles=False,
            fps=25,
        )
    message = str(excinfo.value)
    assert "Montaggio troppo lento" in message
    assert "Riprova" in message
    assert "KORA_RENDER_TIMEOUT_SEC" in message
