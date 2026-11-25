"""3.6.8 — regressioni indipendenti sull'allineamento voce/testo/sottotitoli.

Questi test riproducono i tre difetti trovati dall'audit esterno della 3.6.7 e li
bloccano:

1. la modalità «copione» non chiamava mai l'ASR (e `apply_uploaded_voiceover`
   spegneva l'ASR quando c'era un copione);
2. `caption_timings_from_transcript` usava solo il **primo** segmento di una
   scena, quindi la seconda frase finiva nei tempi della prima;
3. `verified` diventava `True` appena le parole avevano un tempo, anche se i
   tempi venivano da un copione allineato e non dal riconoscimento del parlato.

Regola di onestà applicata nei test: dove serve un risultato ASR, è **fornito
come input dichiarato** (o il motore è sostituito con un finto esplicito); nessun
test presenta una stima come trascrizione reale.
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from kora_studio.agents import asr as asr_mod
from kora_studio.agents.audio_timeline import (
    AudioTimelineManager,
    caption_timings_from_transcript,
)
from kora_studio.agents.hyperframes_captions import build_caption_segments_for_scene
from kora_studio.db.models import StoryboardScene

# fixture condivise (isolated_env, director, client, _audio, _project)
from tests.kora_studio.test_v367_speech_sync import (  # noqa: F401
    _audio,
    _project,
    client,
    director,
)

ASSETS = pathlib.Path(__file__).resolve().parents[2] / "assets" / "kora" / "voices"

SCRIPT = "La luce sfiora il pergolato e il posto è già pronto"


def _fake_asr(*_args, **_kwargs) -> SimpleNamespace:
    """Risultato ASR finto ma **dichiarato**: serve a provare che il motore è chiamato."""

    def word(text: str, start: float) -> dict:
        return {"word": text, "start_sec": start, "end_sec": start + 0.35, "confidence": 0.9}

    tokens = SCRIPT.split()
    words = [word(t, round(0.3 + i * 0.42, 3)) for i, t in enumerate(tokens)]
    return SimpleNamespace(
        engine="fake",
        model="fake-small",
        to_dict=lambda: {
            "text": SCRIPT,
            "engine": "fake",
            "model": "fake-small",
            "words_have_timestamps": True,
            "segments": [
                {
                    "text": SCRIPT,
                    "start_sec": 0.3,
                    "end_sec": words[-1]["end_sec"],
                    "words": words,
                }
            ],
        },
    )


# --------------------------------------------------------------------------- #
# 1. La modalità copione chiama davvero l'ASR
# --------------------------------------------------------------------------- #
def test_script_mode_calls_asr_and_forces_alignment(director, tmp_path, monkeypatch):
    project_id, _ = _project(director)
    clip = _audio(tmp_path / "voce.wav", seconds=8.0)
    director.apply_uploaded_voiceover(project_id, clip, "voce.wav", use_asr=False)

    calls: list[pathlib.Path] = []

    def spy(path, **_kwargs):
        calls.append(path)
        return _fake_asr()

    monkeypatch.setattr(asr_mod, "transcribe", spy)
    result = director.rebuild_audio_timeline(project_id, mode="script", script_text=SCRIPT)

    assert calls, "mode=script non ha chiamato l'ASR"
    transcript = result["timeline"]["transcript"]
    assert transcript["alignment"] == "forced_aligned"
    assert transcript["verified"] is False
    assert result["verified"] is False
    assert all(w["timing_source"] == "forced_aligned" for w in transcript["words"])
    assert result["mismatches"] == []
    assert "copione allineato" in result["asr_note"]


def test_script_mode_reports_mismatched_words(director, tmp_path, monkeypatch):
    project_id, _ = _project(director)
    clip = _audio(tmp_path / "voce.wav", seconds=8.0)
    director.apply_uploaded_voiceover(project_id, clip, "voce.wav", use_asr=False)
    monkeypatch.setattr(asr_mod, "transcribe", lambda *a, **k: _fake_asr())
    result = director.rebuild_audio_timeline(
        project_id, mode="script", script_text="La luce sfiora il pergolato e il posto è già chiuso"
    )
    assert "chiuso" in result["mismatches"]
    assert result["timeline"]["transcript"]["alignment"] == "forced_aligned"


def test_script_mode_without_asr_declares_estimated(director, tmp_path, monkeypatch):
    project_id, _ = _project(director)
    clip = _audio(tmp_path / "voce.wav", seconds=8.0)
    director.apply_uploaded_voiceover(project_id, clip, "voce.wav", use_asr=False)

    def explode(*_a, **_k):
        raise asr_mod.AsrUnavailable("nessun motore installato")

    monkeypatch.setattr(asr_mod, "transcribe", explode)
    result = director.rebuild_audio_timeline(project_id, mode="script", script_text=SCRIPT)
    transcript = result["timeline"]["transcript"]
    assert transcript["alignment"] == "estimated"
    assert transcript["verified"] is False
    assert "stimati" in result["asr_note"]
    assert all(w["timing_source"] == "estimated" for w in transcript["words"])


def test_uploaded_voiceover_keeps_asr_when_script_is_given(director, tmp_path, monkeypatch):
    project_id, _ = _project(director)
    clip = _audio(tmp_path / "voce.wav", seconds=8.0)
    calls: list[str] = []

    def spy(*_a, **_k):
        calls.append("called")
        return _fake_asr()

    monkeypatch.setattr(asr_mod, "transcribe", spy)
    director.apply_uploaded_voiceover(project_id, clip, "voce.wav", script_text=SCRIPT)
    assert calls, "apply_uploaded_voiceover spegne ancora l'ASR quando c'è un copione"


def test_script_mode_requires_a_script(director):
    project_id, _ = _project(director)
    with pytest.raises(ValueError):
        director.rebuild_audio_timeline(project_id, mode="script")


# --------------------------------------------------------------------------- #
# 2. Più frasi nella stessa scena
# --------------------------------------------------------------------------- #
def _multi_segment_timeline() -> tuple[dict, StoryboardScene]:
    scene = StoryboardScene(
        scene_number=1,
        narrative_goal="test",
        start_sec=0.0,
        end_sec=5.0,
        start_frame=0,
        end_frame=125,
        duration_sec=5.0,
        duration_frames=125,
        spoken_text="Uno due tre quattro",
        subtitle_text="Uno due tre quattro",
        selected_asset_id="dummy",
    )
    words = [
        {
            "word": name,
            "word_id": f"w_{index + 1:04d}",
            "start_sec": start,
            "end_sec": end,
            "timing_source": "asr",
            "recognized": True,
        }
        for index, (name, start, end) in enumerate(
            [("Uno", 0.1, 0.4), ("due", 0.4, 0.8), ("tre", 2.2, 2.6), ("quattro", 2.6, 3.0)]
        )
    ]
    timeline = {
        "transcript": {
            "words": words,
            "segments": [
                {
                    "text": "Uno due",
                    "scene_number": 1,
                    "word_ids": ["w_0001", "w_0002"],
                    "start_sec": 0.1,
                    "end_sec": 0.8,
                },
                {
                    "text": "tre quattro",
                    "scene_number": 1,
                    "word_ids": ["w_0003", "w_0004"],
                    "start_sec": 2.2,
                    "end_sec": 3.0,
                },
            ],
        }
    }
    return timeline, scene


def test_caption_includes_every_segment_of_the_scene():
    timeline, scene = _multi_segment_timeline()
    timings = caption_timings_from_transcript(timeline, [scene])[1]
    assert len(timings) == 4
    assert [round(t["start_sec"], 2) for t in timings] == [0.1, 0.4, 2.2, 2.6]
    # la parola «quattro» non può cadere nei tempi della prima frase
    assert timings[-1]["start_sec"] == pytest.approx(2.6, abs=0.001)
    assert all(t["mapping"] == "exact" for t in timings)


def test_silent_scene_and_pause_are_supported():
    timeline, scene = _multi_segment_timeline()
    silent = StoryboardScene(
        scene_number=2,
        narrative_goal="silenzio",
        start_sec=5.0,
        end_sec=7.0,
        start_frame=125,
        end_frame=175,
        duration_sec=2.0,
        duration_frames=50,
        spoken_text="",
        subtitle_text="",
        selected_asset_id="dummy",
    )
    timings = caption_timings_from_transcript(timeline, [scene, silent])
    assert 1 in timings and 2 not in timings


def test_sentence_split_across_two_scenes_keeps_its_own_words():
    """Una frase sola, due scene: ogni scena riceve solo le parole che le competono."""
    timeline, scene = _multi_segment_timeline()
    # una sola frase lunga che copre 0.1–3.0 s, assegnata alla scena 1
    timeline["transcript"]["segments"] = [
        {
            "text": "Uno due tre quattro",
            "scene_number": 1,
            "word_ids": ["w_0001", "w_0002", "w_0003", "w_0004"],
            "start_sec": 0.1,
            "end_sec": 3.0,
        }
    ]
    second = StoryboardScene(
        scene_number=3,
        narrative_goal="seconda metà",
        start_sec=2.0,
        end_sec=5.0,
        start_frame=50,
        end_frame=125,
        duration_sec=3.0,
        duration_frames=75,
        spoken_text="tre quattro",
        subtitle_text="tre quattro",
        selected_asset_id="dummy",
    )
    timings = caption_timings_from_transcript(
        timeline,
        [
            StoryboardScene(**{**scene.model_dump(), "start_sec": 0.0, "end_sec": 2.0, "end_frame": 50,
                               "duration_sec": 2.0, "duration_frames": 50, "subtitle_text": "Uno due"}),
            second,
        ],
    )
    assert [round(t["start_sec"], 2) for t in timings[1]] == [0.1, 0.4]
    assert [round(t["start_sec"], 2) for t in timings[3]] == [2.2, 2.6]


# --------------------------------------------------------------------------- #
# 3. Nessun falso `verified`
# --------------------------------------------------------------------------- #
def test_forced_aligned_words_are_not_verified(tmp_path):
    clip = _audio(tmp_path / "voce.wav", seconds=3.0)
    timeline = AudioTimelineManager(fps=25).build(
        audio_path=clip,
        text="",
        source="upload",
        asr_result={
            "engine": "faster_whisper",
            "model": "test",
            "words_have_timestamps": True,
            "text": "parola arbitraria",
            "segments": [
                {
                    "start_sec": 0.0,
                    "end_sec": 1.0,
                    "text": "parola arbitraria",
                    "words": [
                        {"word": "parola", "start_sec": 0.1, "end_sec": 0.5, "timing_source": "forced_aligned"}
                    ],
                }
            ],
        },
    )
    transcript = timeline["transcript"]
    assert transcript["verified"] is False
    assert transcript["alignment"] == "forced_aligned"
    assert transcript["verification"]["text_status"] == "script_matched"
    assert transcript["verification"]["words_from_asr"] == 0
    assert transcript["words"][0]["recognized"] is False


def test_asr_words_are_verified_and_counted(tmp_path):
    clip = _audio(tmp_path / "voce.wav", seconds=3.0)
    timeline = AudioTimelineManager(fps=25).build(
        audio_path=clip,
        text="",
        source="upload",
        asr_result={
            "engine": "faster_whisper",
            "model": "test",
            "words_have_timestamps": True,
            "text": "la luce",
            "segments": [
                {
                    "start_sec": 0.0,
                    "end_sec": 1.0,
                    "text": "la luce",
                    "words": [
                        {"word": "la", "start_sec": 0.1, "end_sec": 0.3},
                        {"word": "luce", "start_sec": 0.3, "end_sec": 0.7},
                    ],
                }
            ],
        },
    )
    transcript = timeline["transcript"]
    assert transcript["verified"] is True
    assert transcript["alignment"] == "asr"
    assert transcript["verification"]["words_from_asr"] == 2
    assert all(w["recognized"] is True for w in transcript["words"])


# --------------------------------------------------------------------------- #
# 4. Offset coerenti su audio, parole, scene e caption
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("offset", [0.0, 0.1, 0.25, 0.5])
def test_offset_is_consistent_everywhere(tmp_path, offset):
    clip = _audio(tmp_path / "voce.wav", seconds=6.0)
    manager = AudioTimelineManager(fps=25)
    base = manager.build(audio_path=clip, text="Uno due tre.", source="upload", start_offset_sec=0.0)
    shifted = manager.build(audio_path=clip, text="Uno due tre.", source="upload", start_offset_sec=offset)
    for key in ("words", "segments"):
        first_base = base["transcript"][key][0]["start_sec"]
        first_shift = shifted["transcript"][key][0]["start_sec"]
        assert first_shift == pytest.approx(first_base + offset, abs=0.002)
    assert shifted["caption_cues"][0]["start_sec"] == pytest.approx(
        base["caption_cues"][0]["start_sec"] + offset, abs=0.002
    )
    assert shifted["transcript"]["words"][0]["start_frame"] == round(
        shifted["transcript"]["words"][0]["start_sec"] * 25
    )


# --------------------------------------------------------------------------- #
# 5. HyperFrames usa i timestamp della trascrizione
# --------------------------------------------------------------------------- #
def test_hyperframes_word_highlight_uses_transcript_timestamps(tmp_path):
    timeline, scene = _multi_segment_timeline()
    timings = caption_timings_from_transcript(timeline, [scene])[1]
    segments = build_caption_segments_for_scene(
        scene,
        "hyperframes_word_highlight",
        tmp_path,
        word_timings=timings,
    )
    assert len(segments) == 4
    assert [round(s["start_sec"], 2) for s in segments] == [0.1, 0.4, 2.2, 2.6]
    assert all(s["timing_source"] == "asr" for s in segments)


# --------------------------------------------------------------------------- #
# 6. Import reale degli MP3 Brando e Tiziana
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("voice_filename", ["voce_brando.mp3", "voce_tiziana.mp3"])
def test_real_kora_mp3_import_preserves_audio_and_marks_unverified(
    client, director, voice_filename
):
    voice_path = ASSETS / voice_filename
    if not voice_path.is_file():
        pytest.skip(f"asset non presente: {voice_path}")
    project_id, _ = _project(director)
    with voice_path.open("rb") as fh:
        res = client.post(
            f"/api/projects/{project_id}/voiceover/upload",
            files={"file": (voice_filename, fh, "audio/mpeg")},
            data={"use_asr": "false"},
        )
    assert res.status_code == 200, res.text[:500]
    body = res.json()
    duration = float(body["voiceover"]["duration_sec"])
    assert duration > 15
    timeline = AudioTimelineManager(
        fps=25, project_dir=director.project_dir_for(project_id)
    ).load()
    assert timeline is not None
    assert timeline["transcript"]["verified"] is False
    assert timeline["audio"]["source"] == "upload"
    assert timeline["audio"]["start_offset_sec"] == 0.0
    assert timeline["total_frames"] == int(body["voiceover"]["total_frames"])
    print(
        "REAL_MP3:",
        voice_filename,
        "durata",
        duration,
        "allineamento",
        timeline["transcript"]["alignment"],
    )


# --------------------------------------------------------------------------- #
# 7. I blocchi restano
# --------------------------------------------------------------------------- #
def test_locked_scene_caption_is_not_rewritten(director, tmp_path):
    project_id, scenes = _project(director)
    clip = _audio(tmp_path / "voce.wav", seconds=8.0)
    director.apply_uploaded_voiceover(project_id, clip, "voce.wav", use_asr=False)
    storyboard = director.get_project(project_id)["storyboard"]
    storyboard[0]["locks"] = {"caption": True}
    storyboard[0]["subtitle_text"] = "CAPTION APPROVATA"
    with director.db.session() as conn:
        conn.execute(
            "UPDATE projects SET storyboard_json = ? WHERE id = ?",
            (json.dumps(storyboard, ensure_ascii=False), project_id),
        )
    timeline = AudioTimelineManager(
        fps=25, project_dir=director.project_dir_for(project_id)
    ).load()
    result = director.apply_transcript_to_scenes(project_id, timeline, mode="transcript")
    after = director.get_project(project_id)["storyboard"]
    assert after[0]["subtitle_text"] == "CAPTION APPROVATA"
    assert any(s["reason"] == "sottotitoli bloccati" for s in result["skipped"])
