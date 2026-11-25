"""3.6.9 — il blocco «testo e sottotitoli diversi» non è più un vicolo cieco.

Caso reale (Mac dell'utente, progetto «L'aperitivo senza fretta»): la voce dice
«Nel tardo pomeriggio d'autunno, la luce si abbassa…», il sottotitolo creativo dice
«Il pomeriggio rallenta.». La produzione si fermava senza spiegare che esistono due
scelte legittime.

Comportamento atteso:

* default (sottotitoli «fedeli»): il testo diverso resta un errore bloccante, ma il
  messaggio indica **due** rimedi (allineare, oppure dichiarare l'editoriale);
* sottotitoli dichiarati «editoriali»: nessun blocco, un avviso esplicito che dice
  che non sono la trascrizione letterale;
* il messaggio di blocco elenca solo i controlli che bloccano davvero, separando
  gli avvisi non bloccanti.
"""

from __future__ import annotations

import pathlib

import pytest
from PIL import Image

from kora_studio.agents.director import KoraDirector
from kora_studio.agents.export_manager import _qc_block_message
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload, QualityCheckItem, StoryboardScene

SPOKEN = "Nel tardo pomeriggio d'autunno, la luce si abbassa e le ombre si allungano sul pergolato."


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


def _vc_check(report):
    return next(c for c in report.checks if c.name.startswith("Coerenza tra voiceover"))


def _report(director, tmp_path, caption_source: str):
    _, scenes = _project(director)
    scenes[0].spoken_text = SPOKEN
    scenes[0].subtitle_text = "Il pomeriggio rallenta."
    return director.qc.inspect_reel(
        _clip(tmp_path / "clip.mp4"),
        scenes,
        expected_duration_sec=2.0,
        expect_audio=True,
        voiceover_present=True,
        caption_source=caption_source,
    )


def test_default_is_faithful_and_blocks_with_two_remedies(director, tmp_path):
    report = _report(director, tmp_path, caption_source="transcript")
    check = _vc_check(report)
    assert check.passed is False
    assert check.blocks_delivery() is True
    assert "Allinea sottotitoli" in check.detail
    assert "Editoriali sintetici" in check.detail
    assert "scena 1" in check.detail


def test_declared_editorial_captions_are_declared_not_blocking(director, tmp_path):
    report = _report(director, tmp_path, caption_source="editorial")
    check = _vc_check(report)
    assert check.passed is True
    assert check.blocks_delivery() is False
    assert check.status == "superato"
    assert check.requires_human_review is True
    assert "editoriali" in check.detail.lower()
    assert "non sono la trascrizione" in check.detail


def test_caption_source_defaults_to_faithful_and_respects_the_choice(director):
    project_id, _ = _project(director)
    assert director.caption_source_for(project_id) == "transcript"
    director.db.set_preference(f"caption_source_{project_id}", "editorial")
    assert director.caption_source_for(project_id) == "editorial"


def test_block_message_lists_only_blocking_checks_and_separates_warnings():
    blocking = QualityCheckItem(
        category="narrative",
        name="Coerenza tra voiceover e sottotitoli",
        passed=False,
        severity="error",
        detail="Testo e sottotitoli raccontano cose diverse (scena 1).",
    )
    not_verified = QualityCheckItem(
        category="narrative",
        name="Trascrizione verificata sulle parole pronunciate",
        passed=False,
        severity="warning",
        status="non_verificato",
        detail="Tempi stimati dentro le pause misurate (nessun motore ASR).",
    )

    class _Report:
        checks = [blocking, not_verified]

    message = _qc_block_message(_Report())
    head, _, tail = message.partition("Avvisi (non bloccanti):")
    assert "Testo e sottotitoli raccontano cose diverse" in head
    assert "Trascrizione verificata" not in head
    assert "Trascrizione verificata" in tail


# --------------------------------------------------------------------------- #
# 3.6.9 — la produzione non distrugge i sottotitoli scritti dall'utente
# --------------------------------------------------------------------------- #
def _timeline_with_scene_text(director, project_id: str, scenes, text: str, tmp_dir: pathlib.Path):
    from kora_studio.agents.audio_timeline import AudioTimelineManager

    clip = _clip(tmp_dir / "voce.wav", seconds=2.0)
    manager = AudioTimelineManager(fps=25, project_dir=director.project_dir_for(project_id))
    timeline = manager.build(audio_path=clip, text=text, source="upload", scenes=scenes)
    manager.save(timeline)
    return timeline


def test_creative_caption_is_not_overwritten_by_the_transcript(director, tmp_path):
    project_id, scenes = _project(director)
    scenes[0].spoken_text = SPOKEN
    scenes[0].subtitle_text = "Il pomeriggio rallenta."
    director._persist_scenes(project_id, scenes, "Collaudo: sottotitolo creativo")
    timeline = _timeline_with_scene_text(director, project_id, scenes, SPOKEN, tmp_path)
    result = director.apply_transcript_to_scenes(project_id, timeline, mode="transcript")
    after = {s["scene_number"]: s for s in director.get_project(project_id)["storyboard"]}
    assert after[1]["subtitle_text"] == "Il pomeriggio rallenta."
    assert 1 in result["preserved_captions"]


def test_faithful_caption_still_follows_the_spoken_text(director, tmp_path):
    project_id, scenes = _project(director)
    scenes[0].spoken_text = SPOKEN
    scenes[0].subtitle_text = SPOKEN
    director._persist_scenes(project_id, scenes, "Collaudo: sottotitoli fedeli")
    timeline = _timeline_with_scene_text(director, project_id, scenes, "Testo nuovo pronunciato davvero.", tmp_path)
    result = director.apply_transcript_to_scenes(project_id, timeline, mode="transcript")
    after = {s["scene_number"]: s for s in director.get_project(project_id)["storyboard"]}
    assert 1 in result["updated_scenes"]
    assert after[1]["subtitle_text"] == after[1]["spoken_text"]


def test_editorial_cues_say_what_the_reel_shows(director, tmp_path):
    """I file consegnati (SRT/VTT) devono contenere le parole scritte dall'utente."""
    from kora_studio.agents.audio_timeline import editorial_caption_cues

    project_id, scenes = _project(director)
    scenes[0].subtitle_text = "Il pomeriggio rallenta."
    scenes[1].subtitle_text = "Ombre lunghe."
    scenes[2].subtitle_text = "Un bicchiere, il silenzio."
    cues = editorial_caption_cues(scenes, fps=25)
    assert cues, "nessun cue prodotto"
    assert {c["timing_source"] for c in cues} == {"editorial"}
    for scene in scenes[:3]:
        text = " ".join(
            c["text"] for c in cues if c["scene_number"] == scene.scene_number
        )
        assert text.split() == (scene.subtitle_text or "").split()
        for cue in (c for c in cues if c["scene_number"] == scene.scene_number):
            assert float(cue["start_sec"]) >= float(scene.start_sec) - 0.01
            assert float(cue["end_sec"]) <= float(scene.end_sec) + 0.01


def test_timeline_built_as_editorial_replaces_transcript_cues(director, tmp_path):
    project_id, scenes = _project(director)
    scenes[0].subtitle_text = "Il pomeriggio rallenta."
    clip = _clip(tmp_path / "voce.wav", seconds=2.0)
    timeline = director._build_timeline_for(
        project_dir=director.project_dir_for(project_id),
        voice_path=clip,
        voice_res={"source_used": "upload", "voice_manifest": {}},
        scenes=scenes,
        fps=25,
        caption_source="editorial",
    )
    assert timeline.get("caption_cues_source") == "editorial"
    scene_one = " ".join(
        c["text"] for c in timeline["caption_cues"] if c.get("scene_number") == 1
    )
    assert "rallenta" in scene_one.lower()
