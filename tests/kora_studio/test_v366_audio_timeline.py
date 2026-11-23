"""3.6.6 — sincronizzazione: una timeline sola, offset dichiarati, niente tagli.

I test coprono i difetti trovati dall'audit sul codice 3.6.5:

* le parole erano distribuite sulla scena, non sulle pause realmente misurate;
* il mix applicava un ritardo fisso di 250 ms all'MP3 importato e poteva
  tagliare l'ultima parola;
* le caption dividevano la scena in parti uguali, senza i tempi delle parole;
* il QC non distingueva "testo coerente" da "parlato verificato".

Nessun test simula un motore ASR: qui non è installato, quindi si collauda il
percorso onesto (tempi stimati dentro le pause misurate, dichiarati come tali).
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kora_studio.agents import asr
from kora_studio.agents.audio_timeline import (
    AudioTimelineManager,
    VoiceTruncationError,
    cues_to_ass,
    cues_to_srt,
    cues_to_vtt,
    rebuild_words_from_segments,
    speech_regions,
)
from kora_studio.agents.director import KoraDirector
from kora_studio.agents.sound_designer import SoundDesigner
from kora_studio.api.app import create_app
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload, StoryboardScene
from kora_studio.media_tools import find_ffmpeg


def _audio(path: pathlib.Path) -> pathlib.Path:
    """Parlato simulato: tono 0.4–1.9s, silenzio 1.9–3.0s, tono 3.0–4.2s."""
    subprocess.run(
        [
            find_ffmpeg(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-t", "1.5", "-i", "sine=frequency=320:duration=1.5",
            "-f", "lavfi", "-t", "1.1", "-i", "anullsrc=r=48000:cl=mono",
            "-f", "lavfi", "-t", "1.2", "-i", "sine=frequency=320:duration=1.2",
            "-f", "lavfi", "-t", "0.4", "-i", "anullsrc=r=48000:cl=mono",
            "-filter_complex",
            "[0:a][1:a][2:a][3:a]concat=n=4:v=0:a=1,adelay=400|400,apad=whole_dur=4.2[out]",
            "-map", "[out]", "-t", "4.2", "-ar", "48000", str(path),
        ],
        check=True,
    )
    return path


def _clip(path: pathlib.Path, seconds: float = 2.0) -> pathlib.Path:
    from kora_studio.media_tools import run_ffmpeg

    run_ffmpeg(
        [
            "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=s=540x960:r=25:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}",
            "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "30",
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=9", "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-shortest", str(path),
        ],
        timeout=120,
    )
    return path


@pytest.fixture()
def director(isolated_env) -> KoraDirector:
    db = Database(pathlib.Path(isolated_env["db_path"]))
    d = KoraDirector(db=db)
    tmp = pathlib.Path(isolated_env["tmp_path"])
    for index, name in enumerate(["mare.png", "tavolo.png", "pergolato.png"]):
        path = tmp / name
        Image.new("RGB", (1080, 1920), (25 + index * 20, 80, 95)).save(path)
        d.vault.ingest_file(
            path, origin="test", authenticity_status="autentico",
            usage_rights="proprietario_kora", category="Mare",
        )
    return d


@pytest.fixture()
def client(isolated_env, director):
    return TestClient(create_app(db_path=isolated_env["db_path"], auto_seed=False))


def _project(director: KoraDirector) -> tuple[str, list[StoryboardScene]]:
    ids = [a["id"] for a in director.vault.list_assets() if a.get("media_type") == "image"]
    design = director.design_reel(
        CreativeRequestPayload(
            theme="La luce radente", duration_sec=12.0, voice_id="brando",
            asset_ids=ids, media_selection="vault_only",
        )
    )
    project = director.create_project_from_design(design, theme="La luce radente")
    return project["id"], [StoryboardScene(**s) for s in project["storyboard"]]


# --------------------------------------------------------------------------- #
# 1. Il parlato misura le pause: le parole non finiscono nei silenzi
# --------------------------------------------------------------------------- #
def test_words_stay_inside_measured_speech_regions(tmp_path):
    clip = _audio(tmp_path / "voce.wav")
    regions = speech_regions(clip)
    assert len(regions) >= 2, "il file di prova contiene due blocchi di parlato"
    gap = (regions[0].end_sec, regions[1].start_sec)
    manager = AudioTimelineManager(fps=25)
    timeline = manager.build(
        audio_path=clip,
        text="La luce di novembre sfiora il pergolato. Il mare resta calmo e lucido.",
        source="upload",
    )
    words = timeline["transcript"]["words"]
    assert words, "le parole sono state stimate"
    inside = [
        w for w in words if not (gap[0] + 0.02 < (w["start_sec"] + w["end_sec"]) / 2 < gap[1] - 0.02)
    ]
    assert len(inside) == len(words), "nessuna parola deve cadere nel silenzio misurato"
    assert timeline["transcript"]["alignment"] == "estimated"
    assert timeline["transcript"]["verified"] is False


def test_frames_are_exact_and_keep_seconds(tmp_path):
    clip = _audio(tmp_path / "voce.wav")
    timeline = AudioTimelineManager(fps=25).build(
        audio_path=clip, text="Una frase breve per misurare i frame.", source="upload"
    )
    for word in timeline["transcript"]["words"]:
        assert word["start_frame"] == round(word["start_sec"] * 25)
        assert word["end_frame"] >= word["start_frame"] + 1
        assert word["start_sec"] >= 0
    assert timeline["total_frames"] == round(timeline["duration_sec"] * 25)


# --------------------------------------------------------------------------- #
# 2. Offset dichiarati, nessun ritardo magico, nessun taglio
# --------------------------------------------------------------------------- #
def test_imported_voice_starts_at_zero_without_hidden_delay(tmp_path):
    flt = SoundDesigner._voice_filter(1, 0.0, 4.2, 1.05)
    assert "adelay" not in flt, "nessun ritardo non dichiarato sull'MP3 importato"
    assert "atrim=0:4.200" in flt


def test_declared_offset_is_honoured(tmp_path):
    flt = SoundDesigner._voice_filter(1, 0.3, 4.0, 1.0)
    assert "adelay=300|300" in flt


def test_voice_longer_than_reel_is_refused_not_cut(tmp_path):
    clip = _audio(tmp_path / "voce.wav")
    manifest = {
        "start_offset_sec": 0.0,
        "end_padding_sec": 0.0,
        "source_duration_sec": 4.2,
        "effective_duration_sec": 4.2,
    }
    with pytest.raises(VoiceTruncationError):
        SoundDesigner.voice_manifest(manifest, clip, total_duration_sec=3.0)
    ok = SoundDesigner.voice_manifest(manifest, clip, total_duration_sec=4.2)
    assert ok["end_sec"] == pytest.approx(4.2, abs=0.01)


# --------------------------------------------------------------------------- #
# 3. Cue e sottotitoli dagli stessi tempi
# --------------------------------------------------------------------------- #
def test_cues_follow_word_timings_not_uniform_split(tmp_path):
    clip = _audio(tmp_path / "voce.wav")
    timeline = AudioTimelineManager(fps=25).build(
        audio_path=clip,
        text="La luce sfiora il pergolato. Il posto è già pronto per te.",
        source="upload",
    )
    cues = timeline["caption_cues"]
    words = timeline["transcript"]["words"]
    assert cues
    first = cues[0]
    assert first["start_sec"] == pytest.approx(words[0]["start_sec"], abs=0.001)
    ends = {w["end_sec"] for w in words}
    for cue in cues:
        assert cue["end_sec"] in ends, "ogni cue finisce sulla fine reale di una parola"


def test_subtitle_files_share_the_same_timings(tmp_path):
    clip = _audio(tmp_path / "voce.wav")
    timeline = AudioTimelineManager(fps=25).build(
        audio_path=clip, text="Una frase. Una seconda frase più lunga.", source="upload"
    )
    cues = timeline["caption_cues"]
    srt = cues_to_srt(cues)
    vtt = cues_to_vtt(cues)
    ass = cues_to_ass(cues)
    assert "00:00:" in srt and "-->" in srt
    assert vtt.startswith("WEBVTT")
    assert "[Events]" in ass and "Dialogue:" in ass
    first = cues[0]
    assert srt.splitlines()[1].split(" --> ")[0].endswith(f"{int(round(first['start_sec'] * 1000)) % 60000 // 1000:02d},{int(round(first['start_sec'] * 1000)) % 1000:03d}")


def test_caption_pngs_use_real_word_windows(tmp_path):
    from kora_studio.agents.hyperframes_captions import build_caption_segments_for_scene
    from kora_studio.db.models import StoryboardScene

    scene = StoryboardScene(
        scene_number=1,
        narrative_goal="Aprire sulla luce radente",
        start_sec=0.0,
        end_sec=3.0,
        start_frame=0,
        end_frame=75,
        duration_frames=75,
        duration_sec=3.0,
        spoken_text="La luce sfiora il pergolato",
        subtitle_text="La luce sfiora il pergolato",
        selected_asset_id="ast_x",
    )
    timings = [
        {"start_sec": 0.40, "end_sec": 0.72, "word_index": 0},
        {"start_sec": 0.72, "end_sec": 1.10, "word_index": 1},
        {"start_sec": 1.10, "end_sec": 1.80, "word_index": 2},
        {"start_sec": 1.80, "end_sec": 2.60, "word_index": 3},
    ]
    segs = build_caption_segments_for_scene(
        scene=scene,
        caption_style="hyperframes_word_highlight",
        sub_dir=tmp_path,
        word_timings=timings,
    )
    assert len(segs) == len(timings)
    assert segs[0]["start_sec"] == pytest.approx(0.40, abs=0.001)
    assert segs[-1]["end_sec"] == pytest.approx(2.60, abs=0.001)


# --------------------------------------------------------------------------- #
# 4. QC: coerenza lessicale ≠ sincronizzazione verificata
# --------------------------------------------------------------------------- #
def _qc(director, tmp_path, project_dir, total_sec=2.0):
    _, scenes = _project(director)
    return director.qc.inspect_reel(
        _clip(tmp_path / f"clip_{total_sec}.mp4", seconds=total_sec), scenes, expected_duration_sec=total_sec,
        expect_audio=True, work_dir=project_dir, voiceover_present=True,
    )


def test_qc_marks_estimated_alignment_as_not_verified(director, tmp_path):
    clip = _audio(tmp_path / "voce.wav")
    manager = AudioTimelineManager(fps=25, project_dir=tmp_path)
    timeline = manager.build(audio_path=clip, text="La luce sfiora il pergolato.", source="upload")
    manager.save(timeline, tmp_path)
    report = _qc(director, tmp_path, tmp_path, total_sec=4.2)
    sync = next(c for c in report.checks if c.name == "Sincronizzazione audio, scene e sottotitoli")
    verified = next(c for c in report.checks if c.name.startswith("Trascrizione verificata"))
    assert sync.passed is True
    assert verified.passed is False
    assert verified.status == "non_verificato"
    assert verified.blocks_delivery() is False
    # il clip sintetico non è 1080x1920: altri controlli possono fallire, ma
    # nessuno di quelli sulla sincronizzazione deve bloccare la consegna
    assert sync.blocks_delivery() is False and verified.blocks_delivery() is False


def test_qc_blocks_when_voice_is_cut(director, tmp_path):
    clip = _audio(tmp_path / "voce.wav")
    manager = AudioTimelineManager(fps=25, project_dir=tmp_path)
    timeline = manager.build(audio_path=clip, text="La luce sfiora il pergolato.", source="upload")
    manager.save(timeline, tmp_path)
    # il Reel dura 3 s, la voce finisce a 4,2 s: ultima parola persa
    report = _qc(director, tmp_path, tmp_path, total_sec=3.0)
    cut = next(c for c in report.checks if c.name.startswith("Voce non tagliata"))
    assert cut.passed is False
    assert cut.blocks_delivery() is True


def test_qc_without_timeline_declares_not_measured(director, tmp_path):
    empty = tmp_path / "vuota"
    empty.mkdir()
    report = _qc(director, tmp_path, empty)
    sync = next(c for c in report.checks if c.name == "Sincronizzazione audio, scene e sottotitoli")
    assert sync.status == "non_verificato"
    assert sync.blocks_delivery() is False


# --------------------------------------------------------------------------- #
# 5. Correzione manuale del testo: l'audio non si tocca
# --------------------------------------------------------------------------- #
def test_manual_edit_rebuilds_words_and_cues(tmp_path):
    clip = _audio(tmp_path / "voce.wav")
    manager = AudioTimelineManager(fps=25)
    timeline = manager.build(audio_path=clip, text="Testo iniziale da correggere.", source="upload")
    segments = timeline["transcript"]["segments"]
    segments[0]["text"] = "Testo corretto a mano"
    updated = rebuild_words_from_segments(timeline, segments=segments)
    assert updated["transcript"]["alignment"] == "manual"
    rebuilt = " ".join(w["word"] for w in updated["transcript"]["words"])
    assert rebuilt.startswith("Testo corretto a mano")
    assert "iniziale" not in rebuilt
    assert updated["caption_cues"]
    # i tempi del segmento non sono stati spostati
    assert updated["transcript"]["segments"][0]["start_sec"] == segments[0]["start_sec"]


# --------------------------------------------------------------------------- #
# 6. API: timeline, correzioni, verifica, stato ASR
# --------------------------------------------------------------------------- #
def test_api_timeline_rebuild_transcript_and_verify(client, director, tmp_path):
    project_id, _ = _project(director)
    manager = AudioTimelineManager(fps=25, project_dir=director.project_dir_for(project_id))
    clip = _audio(tmp_path / "voce.wav")
    timeline = manager.build(
        audio_path=clip,
        text="La luce sfiora il pergolato. Il mare resta calmo.",
        source="upload",
    )
    manager.save(timeline)

    got = client.get(f"/api/projects/{project_id}/timeline")
    assert got.status_code == 200
    assert got.json()["timeline"]["transcript"]["alignment"] == "estimated"

    verify = client.post(f"/api/projects/{project_id}/sync/verify")
    assert verify.status_code == 200
    body = verify.json()
    assert body["verified"] is False
    assert body["alignment"] == "estimated"
    assert body["cues"] >= 1

    segs = body and got.json()["timeline"]["transcript"]["segments"]
    patch = client.patch(
        f"/api/projects/{project_id}/transcript",
        json={"segments": [{"segment_uid": segs[0]["segment_uid"], "text": "La luce sfiora il pergolato"}]},
    )
    assert patch.status_code == 200
    assert patch.json()["transcript"]["alignment"] == "manual"


def test_api_asr_status_is_honest(client):
    res = client.get("/api/asr/status")
    assert res.status_code == 200
    data = res.json()
    assert set(["available", "engines", "message", "install_hint"]).issubset(data)
    assert data["available"] == bool(asr.available_engines())


def test_asr_unavailable_does_not_invent_text(tmp_path):
    if asr.available_engines():
        pytest.skip("motore ASR presente in questo ambiente: il percorso onesto non è testabile qui")
    clip = _audio(tmp_path / "voce.wav")
    with pytest.raises(asr.AsrUnavailable) as exc:
        asr.transcribe(clip)
    assert "copione" in str(exc.value)


# --------------------------------------------------------------------------- #
# 7. Upload esterno: manifest e timeline dichiarati
# --------------------------------------------------------------------------- #
def test_uploaded_voiceover_builds_declared_manifest_and_timeline(director, tmp_path):
    project_id, _ = _project(director)
    clip = _audio(tmp_path / "voce_esterna.wav")
    director.apply_uploaded_voiceover(
        project_id, clip, "voce_esterna.wav",
        script_text="La luce sfiora il pergolato. Il mare resta calmo.",
        use_asr=False,
    )
    raw = director.db.get_preference(f"voice_manifest_{project_id}")
    manifest = json.loads(raw)
    assert manifest["declared"] is True
    assert manifest["is_scene_aligned"] is False
    assert manifest["start_offset_sec"] == 0.0
    timeline_path = director.project_dir_for(project_id) / "project_timeline.json"
    assert timeline_path.is_file()
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    assert timeline["audio"]["source"] == "upload"
    assert timeline["transcript"]["alignment"] == "estimated"
    assert timeline["transcript"]["verified"] is False
    project = director.get_project(project_id)
    assert project["total_frames"] == round(4.2 * int(project["fps"] or 25))
