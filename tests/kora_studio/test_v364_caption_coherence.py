"""3.6.4 — voce e sottotitoli: si blocca solo quando i testi sono davvero diversi.

Il caso reale: dopo una revisione, tre scene avevano sottotitoli discordi dal testo
letto dalla voce e l'export si fermava con un messaggio generico. Ora:

* un sottotitolo **più sintetico** della frase detta è un avviso, non un blocco;
* testi che raccontano cose diverse bloccano, con numeri di scena e testo a confronto;
* «Allinea sottotitoli al testo» riporta le scene in ordine in un solo passaggio,
  rispettando i blocchi e senza toccare la voce registrata.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kora_studio.agents.director import KoraDirector
from kora_studio.api.app import create_app
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload, StoryboardScene


@pytest.fixture()
def director(isolated_env) -> KoraDirector:
    db = Database(pathlib.Path(isolated_env["db_path"]))
    d = KoraDirector(db=db)
    tmp = pathlib.Path(isolated_env["tmp_path"])
    for index, name in enumerate(["mare.png", "tavolo.png", "pergolato.png"]):
        path = tmp / name
        Image.new("RGB", (1080, 1920), (25 + index * 20, 80, 95)).save(path)
        d.vault.ingest_file(
            path,
            origin="test",
            authenticity_status="autentico",
            usage_rights="proprietario_kora",
            category="Mare",
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


def test_shortened_subtitle_is_a_warning_not_a_block(director, tmp_path):
    _, scenes = _project(director)
    target = scenes[0]
    assert target.spoken_text
    target.subtitle_text = " ".join(target.spoken_text.split()[:3])
    report = director.qc.inspect_reel(
        _clip(tmp_path / "clip.mp4"), scenes, expected_duration_sec=2.0,
        expect_audio=True, voiceover_present=True,
    )
    check = _vc_check(report)
    assert check.passed is True
    assert check.blocks_delivery() is False
    assert check.status == "superato"
    assert check.requires_human_review is True
    assert "sintetici" in check.detail


def test_divergent_text_blocks_with_scene_numbers_and_repair_hint(director, tmp_path):
    _, scenes = _project(director)
    scenes[0].subtitle_text = "Il vento sposta le sedie del portico"
    report = director.qc.inspect_reel(
        _clip(tmp_path / "clip.mp4"), scenes, expected_duration_sec=2.0,
        expect_audio=True, voiceover_present=True,
    )
    check = _vc_check(report)
    assert check.passed is False
    assert check.blocks_delivery() is True
    assert "Allinea sottotitoli" in check.detail
    assert "scena 1" in check.detail


def test_align_endpoint_fixes_only_discordant_scenes(client, director):
    project_id, scenes = _project(director)
    # due scene discordi + una con sottotitoli volutamente brevi (non va toccata dal lock)
    director.scene_editor.update_scene(project_id, 1, {"subtitle_text": "Testo completamente diverso qui"})
    director.scene_editor.update_scene(project_id, 2, {"subtitle_text": "Altra frase senza rapporto"})
    director.scene_editor.set_locks(project_id, 3, {"caption": True})

    res = client.post(f"/api/projects/{project_id}/captions/align")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert set(body["updated_scenes"]) >= {1, 2}

    rows = {s["scene_number"]: s for s in body["scenes"]}
    assert rows[1]["subtitle_text"] == rows[1]["spoken_text"]
    assert rows[2]["subtitle_text"] == rows[2]["spoken_text"]

    # la voce non viene rigenerata: il testo letto resta quello
    detail = client.get(f"/api/projects/{project_id}/scenes").json()
    assert detail["scenes"][0]["spoken_text"]


def test_align_is_idempotent(client, director):
    project_id, _ = _project(director)
    client.post(f"/api/projects/{project_id}/captions/align")
    again = client.post(f"/api/projects/{project_id}/captions/align").json()
    assert again["updated_scenes"] == []


def test_align_accepts_scene_subset(client, director):
    project_id, _ = _project(director)
    director.scene_editor.update_scene(project_id, 1, {"subtitle_text": "Diverso uno"})
    director.scene_editor.update_scene(project_id, 2, {"subtitle_text": "Diverso due"})
    res = client.post(f"/api/projects/{project_id}/captions/align", json={"scene_numbers": [1]})
    assert res.status_code == 200, res.text
    assert res.json()["updated_scenes"] == [1]
    scenes = {s["scene_number"]: s for s in client.get(f"/api/projects/{project_id}/scenes").json()["scenes"]}
    assert scenes[2]["subtitle_text"] == "Diverso due"
