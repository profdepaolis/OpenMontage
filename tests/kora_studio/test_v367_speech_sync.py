"""3.6.7 — il testo che si vede deve venire dalla trascrizione, non da un indice.

Copre le correzioni richieste dall'audit:

* l'offset dell'audio viene applicato a parole, frasi e cue (0 / 100 / 250 / 500 ms);
* forced alignment: un copione noto riceve i tempi delle parole riconosciute,
  con le discordanze dichiarate;
* i sottotitoli fedeli usano la corrispondenza 1:1 con il segmento, non una
  rimappatura arbitraria per indice;
* le scene bloccate non vengono riscritte dal transcript;
* l'API distingue esplicitamente le modalità asr / script / estimated.

Nessun test finge una trascrizione: dove serve l'ASR si usa un risultato ASR
costruito a mano come *input*, dichiarato come tale.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kora_studio.agents.audio_timeline import (
    AudioTimelineManager,
    align_script_to_asr,
    caption_timings_from_transcript,
)
from kora_studio.agents.director import KoraDirector
from kora_studio.api.app import create_app
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload, StoryboardScene
from kora_studio.media_tools import find_ffmpeg


def _audio(path: pathlib.Path, seconds: float = 6.0) -> pathlib.Path:
    subprocess.run(
        [
            find_ffmpeg(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-t", "2.4", "-i", "sine=frequency=300:duration=2.4",
            "-f", "lavfi", "-t", "1.2", "-i", "anullsrc=r=48000:cl=mono",
            "-f", "lavfi", "-t", "2.4", "-i", "sine=frequency=300:duration=2.4",
            "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1,apad=whole_dur=6.0[out]",
            "-map", "[out]", "-t", str(seconds), "-ar", "48000", str(path),
        ],
        check=True,
    )
    return path


def _asr_payload(words: list[str], step: float = 0.32) -> dict:
    """Risultato ASR **fornito come input** al codice di allineamento."""
    asr_words = [
        {
            "word": word,
            "start_sec": round(index * step, 3),
            "end_sec": round(index * step + step * 0.9, 3),
            "confidence": 0.92,
            "start_frame": round(index * step * 25),
            "end_frame": round((index * step + step * 0.9) * 25),
        }
        for index, word in enumerate(words)
    ]
    return {"segments": [{"text": " ".join(words), "start_sec": 0.0, "end_sec": len(words) * step, "words": asr_words}]}


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
# 1. Offset applicato a tutto
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("offset", [0.0, 0.1, 0.25, 0.5])
def test_offset_shifts_words_segments_and_cues(tmp_path, offset):
    clip = _audio(tmp_path / "voce.wav")
    timeline = AudioTimelineManager(fps=25).build(
        audio_path=clip,
        text="La luce sfiora il pergolato. Il posto è già pronto.",
        source="upload",
        start_offset_sec=offset,
    )
    base = AudioTimelineManager(fps=25).build(
        audio_path=clip,
        text="La luce sfiora il pergolato. Il posto è già pronto.",
        source="upload",
        start_offset_sec=0.0,
    )
    first = timeline["transcript"]["words"][0]["start_sec"]
    assert first == pytest.approx(base["transcript"]["words"][0]["start_sec"] + offset, abs=0.002)
    assert timeline["caption_cues"][0]["start_sec"] == pytest.approx(
        base["caption_cues"][0]["start_sec"] + offset, abs=0.002
    )
    assert timeline["transcript"]["segments"][0]["start_sec"] == pytest.approx(
        base["transcript"]["segments"][0]["start_sec"] + offset, abs=0.002
    )
    assert timeline["audio"]["speech_regions"][0]["start_sec"] == pytest.approx(
        base["audio"]["speech_regions"][0]["start_sec"] + offset, abs=0.002
    )
    frames = timeline["transcript"]["words"][0]["start_frame"]
    assert frames == round(first * 25)


def test_offset_beyond_reel_is_declared_not_hidden(tmp_path):
    clip = _audio(tmp_path / "voce.wav")
    manager = AudioTimelineManager(fps=25)
    timeline = manager.build(
        audio_path=clip, text="La luce sfiora il pergolato.", source="upload", start_offset_sec=0.5
    )
    issues = manager.validate(timeline, total_duration_sec=6.0)
    assert any(i["code"] == "voce_tagliata" for i in issues)


# --------------------------------------------------------------------------- #
# 2. Forced alignment
# --------------------------------------------------------------------------- #
def test_forced_alignment_maps_known_script_onto_asr_times():
    asr = _asr_payload(["La", "luce", "di", "novembre", "sfiora", "il", "pergolato"])
    result = align_script_to_asr("La luce di novembre sfiora il pergolato", asr["segments"][0]["words"], fps=25)
    assert len(result["words"]) == 7
    assert all(w["timing_source"] == "forced_aligned" for w in result["words"])
    assert result["words"][3]["start_sec"] == pytest.approx(0.96, abs=0.001)
    assert result["mismatches"] == []


def test_forced_alignment_declares_mismatched_words():
    asr = _asr_payload(["La", "luce", "di", "novembre", "sfiora", "il", "pergolato"])
    result = align_script_to_asr("La luce di dicembre sfiora il pergolato", asr["segments"][0]["words"], fps=25)
    assert "dicembre" in result["mismatches"]


def test_forced_alignment_label_is_not_verified(tmp_path):
    asr = _asr_payload(["La", "luce"])
    result = align_script_to_asr("La luce", asr["segments"][0]["words"], fps=25)
    for word in result["words"]:
        assert word["timing_source"] == "forced_aligned"
        # il forced alignment non è una trascrizione: il campo verified resta False
    clip = _audio(tmp_path / "voce.wav", seconds=2.0)
    timeline = AudioTimelineManager(fps=25).build(
        audio_path=clip,
        text="La luce",
        source="upload",
    )
    assert timeline["transcript"]["verified"] is False
    assert timeline["transcript"]["alignment"] == "estimated"
    assert all(w["timing_source"] == "estimated" for w in timeline["transcript"]["words"])


# --------------------------------------------------------------------------- #
# 3. Caption fedeli alla trascrizione (1:1), non per indice
# --------------------------------------------------------------------------- #
def test_caption_timings_come_from_transcript_segments(tmp_path):
    clip = _audio(tmp_path / "voce.wav")
    manager = AudioTimelineManager(fps=25)
    timeline = manager.build(
        audio_path=clip, text="La luce sfiora il pergolato.", source="upload"
    )
    seg = timeline["transcript"]["segments"][0]
    scene = StoryboardScene(
        scene_number=1, narrative_goal="x",
        start_sec=0.0, end_sec=6.0, start_frame=0, end_frame=150,
        duration_frames=150, duration_sec=6.0,
        spoken_text=seg["text"], subtitle_text=seg["text"], selected_asset_id="ast_x",
    )
    seg["scene_number"] = 1
    timings = caption_timings_from_transcript(timeline, [scene])
    assert 1 in timings
    words = [w for w in timeline["transcript"]["words"] if w["word_id"] in seg["word_ids"]]
    assert timings[1][0]["start_sec"] == pytest.approx(words[0]["start_sec"], abs=0.001)
    assert len(timings[1]) == len(scene.subtitle_text.split())


def test_editorial_caption_is_marked_as_such(tmp_path):
    clip = _audio(tmp_path / "voce.wav")
    timeline = AudioTimelineManager(fps=25).build(
        audio_path=clip, text="La luce di novembre sfiora il pergolato.", source="upload"
    )
    seg = timeline["transcript"]["segments"][0]
    seg["scene_number"] = 1
    scene = StoryboardScene(
        scene_number=1, narrative_goal="x",
        start_sec=0.0, end_sec=6.0, start_frame=0, end_frame=150,
        duration_frames=150, duration_sec=6.0,
        spoken_text=seg["text"], subtitle_text="Luce sul pergolato", selected_asset_id="ast_x",
    )
    timings = caption_timings_from_transcript(timeline, [scene])
    assert timings[1]
    assert all(t["timing_source"] in ("editorial_in_segment",) for t in timings[1])


# --------------------------------------------------------------------------- #
# 4. Propagazione alle scene, blocchi rispettati
# --------------------------------------------------------------------------- #
def test_transcript_updates_scene_text_but_not_locked_scenes(director, tmp_path):
    project_id, scenes = _project(director)
    clip = _audio(tmp_path / "voce.wav")
    manager = AudioTimelineManager(fps=25, project_dir=director.project_dir_for(project_id))
    timeline = manager.build(
        audio_path=clip,
        text="La luce sfiora il pergolato. Il posto è già pronto per te.",
        source="upload",
        scenes=scenes,
    )
    manager.save(timeline)
    director.scene_editor.set_locks(project_id, 1, {"caption": True})
    result = director.apply_transcript_to_scenes(project_id, timeline, mode="transcript")
    updated = director.get_project(project_id)
    by_number = {s["scene_number"]: s for s in updated["storyboard"]}
    assert 1 not in result["updated_scenes"]
    assert result["skipped"] and result["skipped"][0]["scene_number"] == 1
    for number in result["updated_scenes"]:
        assert by_number[number]["subtitle_text"] == by_number[number]["spoken_text"]


def test_editorial_mode_keeps_subtitles(director, tmp_path):
    project_id, scenes = _project(director)
    before = {s.scene_number: s.subtitle_text for s in scenes}
    clip = _audio(tmp_path / "voce.wav")
    manager = AudioTimelineManager(fps=25, project_dir=director.project_dir_for(project_id))
    timeline = manager.build(
        audio_path=clip, text="Testo pronunciato del tutto diverso.", source="upload", scenes=scenes
    )
    manager.save(timeline)
    director.apply_transcript_to_scenes(project_id, timeline, mode="editorial")
    after = {s["scene_number"]: s["subtitle_text"] for s in director.get_project(project_id)["storyboard"]}
    assert after == before


# --------------------------------------------------------------------------- #
# 5. API: modalità esplicite
# --------------------------------------------------------------------------- #
def test_api_rebuild_rejects_unknown_mode(client, director):
    project_id, _ = _project(director)
    res = client.post(f"/api/projects/{project_id}/timeline/rebuild", json={"mode": "magia"})
    assert res.status_code == 400


def test_api_rebuild_estimated_mode_is_declared(client, director, tmp_path):
    project_id, _ = _project(director)
    clip = _audio(tmp_path / "voce.wav")
    director.apply_uploaded_voiceover(project_id, clip, "voce.wav", use_asr=False)
    res = client.post(
        f"/api/projects/{project_id}/timeline/rebuild",
        json={"mode": "estimated", "caption_source": "transcript"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "estimated"
    assert body["timeline"]["transcript"]["verified"] is False
    assert body["caption_source"] == "transcript"


def test_api_asr_mode_without_engine_declares_unavailable(client, director, tmp_path):
    from kora_studio.agents import asr as asr_mod

    if asr_mod.available_engines():
        pytest.skip("motore ASR presente: il percorso «indisponibile» non è testabile qui")
    project_id, _ = _project(director)
    clip = _audio(tmp_path / "voce.wav")
    director.apply_uploaded_voiceover(project_id, clip, "voce.wav", use_asr=False)
    res = client.post(f"/api/projects/{project_id}/timeline/rebuild", json={"mode": "asr"})
    assert res.status_code == 200
    body = res.json()
    assert "ASR non disponibile" in body["asr_note"]
    assert body["timeline"]["transcript"]["alignment"] != "asr"


def test_upload_without_script_does_not_pretend_to_transcribe(director, tmp_path):
    project_id, _ = _project(director)
    clip = _audio(tmp_path / "voce.wav")
    director.apply_uploaded_voiceover(project_id, clip, "voce.wav", use_asr=True)
    timeline = json.loads(
        (director.project_dir_for(project_id) / "project_timeline.json").read_text(encoding="utf-8")
    )
    assert timeline["transcript"]["verified"] is False
    assert timeline["transcript"]["alignment"] in ("estimated", "none")
