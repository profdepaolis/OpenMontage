"""v3.2 — produzione delle scene: aggiungi/togli/modifica, testo AI, avanzamento.

Copre la richiesta dell'utente: dopo aver scelto il tema si deve poter entrare
nella produzione, intervenire sulle scene (materiale, durata, testo), farsi
proporre un testo coerente e capire a che punto è il lavoro.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kora_studio.agents.director import KoraDirector
from kora_studio.api.app import create_app
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload


def _make_director(isolated_env):
    db = Database(pathlib.Path(isolated_env["db_path"]))
    director = KoraDirector(db=db)
    tmp = pathlib.Path(isolated_env["tmp_path"])
    for index, name in enumerate(["mare.png", "tavolo.png", "pergolato.png"]):
        path = tmp / name
        Image.new("RGB", (1080, 1920), (40 + index * 30, 60, 90)).save(path)
        director.vault.ingest_file(
            path,
            origin="test",
            authenticity_status="autentico",
            usage_rights="proprietario_kora",
            category="Mare",
            tags=["mare"],
        )
    design = director.design_reel(
        CreativeRequestPayload(theme="La quiete del mare d'inverno", duration_sec=25)
    )
    project = director.create_project_from_design(design, theme="La quiete del mare d'inverno")
    isolated_env["director"] = director
    isolated_env["project_id"] = project["id"]
    isolated_env["asset_ids"] = [a["id"] for a in director.vault.list_assets()]
    return director, project["id"]


@pytest.fixture()
def env(isolated_env):
    director, project_id = _make_director(isolated_env)
    isolated_env["director"] = director
    isolated_env["project_id"] = project_id
    return isolated_env


def test_list_scenes_and_assets(env):
    data = env["director"].scene_editor.list_scenes(env["project_id"])
    assert data["scenes"], "il progetto deve avere scene"
    assert data["available_assets"], "il vault deve offrire materiale selezionabile"
    first = data["scenes"][0]
    assert first["scene_number"] == 1
    assert first["end_sec"] > first["start_sec"]


def test_add_scene_renumbers_and_keeps_timeline_contiguous(env):
    director = env["director"]
    pid = env["project_id"]
    before = director.scene_editor.list_scenes(pid)["scenes"]
    res = director.scene_editor.add_scene(
        pid, {"asset_id": env["asset_ids"][0], "duration_sec": 3.0, "spoken_text": "Una prova"}
    )
    after = res["scenes"]
    assert len(after) == len(before) + 1
    for previous, current in zip(after, after[1:]):
        assert current["start_sec"] == pytest.approx(previous["end_sec"], abs=0.05)
    assert [s["scene_number"] for s in after] == list(range(1, len(after) + 1))
    added = res["added"]
    assert added["duration_sec"] == pytest.approx(3.0, abs=0.05)


def test_update_scene_text_and_duration_recomputes_totals(env):
    director = env["director"]
    pid = env["project_id"]
    res = director.scene_editor.update_scene(
        pid, 1, {"spoken_text": "Il mare d'inverno non chiede permesso.", "duration_sec": 6.0}
    )
    assert res["ok"] is True
    first = res["scenes"][0]
    assert first["spoken_text"] == "Il mare d'inverno non chiede permesso."
    assert first["duration_sec"] == pytest.approx(6.0, abs=0.1)
    assert first["approval_status"] == "modificata"
    assert first["preview_status"] == "pending"


def test_remove_scene_keeps_at_least_one(env):
    director = env["director"]
    pid = env["project_id"]
    scenes = director.scene_editor.list_scenes(pid)["scenes"]
    res = director.scene_editor.remove_scene(pid, 2)
    assert len(res["scenes"]) == len(scenes) - 1
    with pytest.raises(KeyError):
        director.scene_editor.remove_scene(pid, 99)


def test_suggest_text_falls_back_declaring_the_source(env):
    director = env["director"]
    pid = env["project_id"]
    before = director.scene_editor.list_scenes(pid)["scenes"][1]["spoken_text"]
    result = director.scene_editor.suggest_text(pid, 2)
    assert result["text"], "deve proporre una riga"
    assert result["source"] in {"deterministic_offline_fallback", "guardrail_locale"} or result["source"].startswith("engine")
    assert result["note"]
    # v3.3: proposta, non applicazione
    assert result["applied"] is False
    unchanged = director.scene_editor.list_scenes(pid)["scenes"][1]
    assert unchanged["spoken_text"] == before
    applied = director.scene_editor.apply_text(pid, 2, result["text"])
    assert applied["applied"] is True
    assert director.scene_editor.list_scenes(pid)["scenes"][1]["spoken_text"] == result["text"]


def test_progress_reports_real_stages(env):
    progress = env["director"].project_progress(env["project_id"])
    ids = [stage["id"] for stage in progress["stages"]]
    assert ids == ["concept", "storyboard", "assets", "voiceover", "preview", "render"]
    assert progress["stages"][0]["done"] is True
    assert progress["stages"][1]["done"] is True
    assert 0 <= progress["percent"] < 100
    assert progress["current"]


def test_readiness_explains_what_is_missing(env):
    ready = env["director"].preview_readiness(env["project_id"])
    assert ready["scene_count"] >= 1
    assert any(check["id"] == "ffmpeg" for check in ready["checks"])
    assert all("detail" in check for check in ready["checks"])


def test_api_scene_lifecycle(env):
    client = TestClient(create_app(db_path=env["db_path"], auto_seed=False))
    pid = env["project_id"]

    listing = client.get(f"/api/projects/{pid}/scenes")
    assert listing.status_code == 200, listing.text
    total = len(listing.json()["scenes"])

    added = client.post(
        f"/api/projects/{pid}/scenes",
        json={"asset_id": env["asset_ids"][0], "duration_sec": 3.0, "spoken_text": "Aggiunta da API"},
    )
    assert added.status_code == 200, added.text
    assert len(added.json()["scenes"]) == total + 1

    patched = client.patch(
        f"/api/projects/{pid}/scenes/1", json={"spoken_text": "Testo modificato via API"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["scenes"][0]["spoken_text"] == "Testo modificato via API"

    empty = client.patch(f"/api/projects/{pid}/scenes/1", json={})
    assert empty.status_code == 400

    text = client.post(f"/api/projects/{pid}/scenes/2/text", json={"instruction": "più concreto"})
    assert text.status_code == 200, text.text
    assert text.json()["text"]

    deleted = client.delete(f"/api/projects/{pid}/scenes/2")
    assert deleted.status_code == 200, deleted.text

    missing = client.delete(f"/api/projects/{pid}/scenes/99")
    assert missing.status_code == 404

    progress = client.get(f"/api/projects/{pid}/progress")
    assert progress.status_code == 200
    assert "stages" in progress.json()

    ready = client.get(f"/api/projects/{pid}/readiness")
    assert ready.status_code == 200
    assert "checks" in ready.json()

    diag = client.get("/api/system/diagnostics")
    assert diag.status_code == 200
    assert "ffmpeg" in diag.json()


def test_preview_failure_explains_the_reason(env, monkeypatch):
    director = env["director"]

    def boom(project_id: str, force: bool = False):
        raise RuntimeError("Errore FFmpeg (1): Filtergraph 'overlay' non valido")

    monkeypatch.setattr(director, "render_preview", boom)
    client = TestClient(create_app(db_path=env["db_path"], auto_seed=False), raise_server_exceptions=False)
    res = client.post(f"/api/projects/{env['project_id']}/preview")
    assert res.status_code == 500
    detail = res.json()["detail"]
    assert "RuntimeError" in detail and "FFmpeg" in detail
