"""v3.6.2 — provenienza dei materiali, motori reali, materiali per scena.

Le verifiche usano il codice di produzione: archivio vero, route FastAPI vere,
renderer FFmpeg vero. Dove un motore non è installato sulla macchina il test
dichiara `non collaudato` e controlla che l'inventario lo dica, invece di
fingere una disponibilità.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kora_studio.agents.director import KoraDirector, normalize_media_selection
from kora_studio.api.app import create_app
from kora_studio.db.database import Database
from kora_studio.db.models import CreativeRequestPayload
from kora_studio.media import engines as engines_module


# --------------------------------------------------------------------- fixtures
@pytest.fixture()
def director(isolated_env) -> KoraDirector:
    db = Database(Path(isolated_env["db_path"]))
    d = KoraDirector(db=db)
    tmp = Path(isolated_env["tmp_path"])
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
def client(isolated_env, director):
    app = create_app(db_path=isolated_env["db_path"], auto_seed=False)
    return TestClient(app)


def _asset_ids(director: KoraDirector) -> list[str]:
    return [a["id"] for a in director.vault.list_assets() if a.get("media_type") == "image"]


def _project_from(director: KoraDirector, asset_ids: list[str], mode: str = "vault_only") -> str:
    req = CreativeRequestPayload(
        theme="Il tempo che rallenta",
        duration_sec=20.0,
        voice_id="none",
        asset_ids=asset_ids,
        media_selection=mode,
    )
    design = director.design_reel(req)
    project = director.create_project_from_design(design, theme="Il tempo che rallenta")
    return project["id"]


# ------------------------------------------------------- vocabolario modalità
def test_media_selection_vocabulary_is_normalised_without_silent_reset():
    assert normalize_media_selection("vault_only") == "vault_only"
    assert normalize_media_selection("upload_only") == "upload_only"
    assert normalize_media_selection("mixed") == "mixed"
    assert normalize_media_selection("vault_auto") == "vault_auto"
    # sinonimi storici: letti, non riscritti a vault_auto
    assert normalize_media_selection("selected_only") == "vault_only"
    assert normalize_media_selection("combined") == "mixed"
    assert normalize_media_selection("uploads") == "upload_only"
    assert normalize_media_selection(None) == "vault_auto"


# --------------------------------------------------- (A) solo archivio scelto
def test_a_vault_selection_restricts_inventory(director):
    ids = _asset_ids(director)[:2]
    req = CreativeRequestPayload(
        theme="Solo archivio", duration_sec=20.0, voice_id="none",
        asset_ids=ids, media_selection="vault_only",
    )
    inventory = director.reel_designer._inventory(req) if hasattr(director, "reel_designer") and director.reel_designer else None
    if inventory is None:
        from kora_studio.agents.reel_designer import ReelDesigner

        inventory = ReelDesigner(director)._inventory(req)
    assert {a["id"] for a in inventory} == set(ids)

    design = director.design_reel(req)
    assert set(design.concept.available_asset_ids) <= set(ids)


def test_a_project_stores_selected_assets_and_mode(director):
    ids = _asset_ids(director)[:2]
    project_id = _project_from(director, ids, "vault_only")
    selection = director.media_selection(project_id)
    assert selection["media_selection"] == "vault_only"
    assert set(selection["selected_asset_ids"]) == set(ids)


# ------------------------------------------------------------ (B) solo nuovi
def test_b_upload_only_rejects_empty_and_accepts_new_assets(client, director, tmp_path):
    empty = client.post(
        "/api/creative/project-reel",
        json={
            "request": {
                "theme": "Solo nuovi",
                "duration_sec": 20,
                "voice_id": "none",
                "media_selection": "upload_only",
                "asset_ids": [],
            },
            "create_project": False,
        },
    )
    assert empty.status_code == 400
    assert "materiali nuovi" in empty.json()["detail"]

    new_path = tmp_path / "nuovo_scatto.png"
    Image.new("RGB", (1080, 1920), (200, 120, 60)).save(new_path)
    with new_path.open("rb") as handle:
        upload = client.post(
            "/api/vault/upload",
            files={"files": ("nuovo_scatto.png", handle, "image/png")},
            data={"category": "Mare"},
        )
    assert upload.status_code == 200, upload.text
    uploaded = upload.json()["uploaded"]
    assert uploaded, upload.text
    new_id = uploaded[0]["id"]

    res = client.post(
        "/api/creative/project-reel",
        json={
            "request": {
                "theme": "Solo nuovi materiali",
                "duration_sec": 20,
                "voice_id": "none",
                "media_selection": "upload_only",
                "asset_ids": [new_id],
            },
            "create_project": True,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["project"]["id"]
    selection = client.get(f"/api/projects/{body['project']['id']}/media-selection").json()
    assert selection["media_selection"] == "upload_only"
    assert selection["selected_asset_ids"] == [new_id]


# ------------------------------------------------------------ (C) combinata
def test_c_mixed_keeps_both_with_priority(client, director, tmp_path):
    ids = _asset_ids(director)[:2]
    project_id = _project_from(director, ids, "vault_only")
    res = client.patch(
        f"/api/projects/{project_id}/media-selection",
        json={"mode": "mixed", "asset_ids": ids, "priority": "uploads_first", "note": "prova C"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["media_selection"] == "mixed"
    assert body["priority"] == "uploads_first"
    assert body["regenerated"] is False

    # una modalità vincolante non può restare vuota
    empty = client.patch(
        f"/api/projects/{project_id}/media-selection", json={"mode": "vault_only", "asset_ids": []}
    )
    assert empty.status_code == 400
    # e la scelta precedente NON è stata azzerata
    again = client.get(f"/api/projects/{project_id}/media-selection").json()
    assert again["media_selection"] == "mixed"


def test_selection_ignores_logo_and_unknown_assets(client, director):
    project_id = _project_from(director, _asset_ids(director)[:1], "vault_only")
    res = client.patch(
        f"/api/projects/{project_id}/media-selection",
        json={"mode": "vault_only", "asset_ids": ["ast_inesistente", _asset_ids(director)[1]]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "ast_inesistente" not in body["selected_asset_ids"]
    assert body["ignored"] == ["ast_inesistente"]


# ---------------------------------------------- (D) sostituzione in montaggio
def test_d_replace_asset_touches_only_that_scene(client, director):
    project_id = _project_from(director, _asset_ids(director), "vault_only")
    before = client.get(f"/api/projects/{project_id}/scenes").json()["scenes"]
    assert len(before) >= 2
    target = before[0]
    new_asset = next(
        a["id"] for a in director.vault.list_assets() if a["id"] != target["asset_id"]
    )
    res = client.patch(
        f"/api/projects/{project_id}/scenes/{target['scene_number']}", json={"asset_id": new_asset}
    )
    assert res.status_code == 200, res.text
    after = res.json()["scenes"]
    changed = next(s for s in after if s["scene_number"] == target["scene_number"])
    assert changed["asset_id"] == new_asset
    assert changed["spoken_text"] == target["spoken_text"]
    for old_scene, new_scene in zip(before, after):
        if old_scene["scene_number"] == target["scene_number"]:
            continue
        assert new_scene["asset_id"] == old_scene["asset_id"]
        assert new_scene["spoken_text"] == old_scene["spoken_text"]
        assert new_scene["camera_motion"] == old_scene["camera_motion"]
    # nessun nuovo job di produzione creato dalla modifica
    assert client.get("/api/jobs").json() == [] or all(
        j["state"] != "In esecuzione" for j in client.get("/api/jobs").json()
    )


# ------------------------------------------------ (E) movimento di una scena
def test_e_camera_motion_change_is_local(client, director):
    project_id = _project_from(director, _asset_ids(director), "vault_only")
    scenes = client.get(f"/api/projects/{project_id}/scenes").json()["scenes"]
    scene = scenes[-1] if len(scenes) > 1 else scenes[0]
    original = scene["camera_motion"]
    new_motion = "pan_left" if original != "pan_left" else "pan_right"
    res = client.patch(
        f"/api/projects/{project_id}/scenes/{scene['scene_number']}", json={"camera_motion": new_motion}
    )
    assert res.status_code == 200, res.text
    updated = next(
        s for s in res.json()["scenes"] if s["scene_number"] == scene["scene_number"]
    )
    assert updated["camera_motion"] == new_motion
    for other in res.json()["scenes"]:
        if other["scene_number"] != scene["scene_number"]:
            original_other = next(
                s for s in scenes if s["scene_number"] == other["scene_number"]
            )
            assert other["camera_motion"] == original_other["camera_motion"]


# ------------------------------------- (L) blocchi: modifica selettiva sicura
def test_l_locked_scene_refuses_asset_change(client, director):
    project_id = _project_from(director, _asset_ids(director), "vault_only")
    scenes = client.get(f"/api/projects/{project_id}/scenes").json()["scenes"]
    scene = scenes[0]
    locks = client.post(
        f"/api/projects/{project_id}/scenes/{scene['scene_number']}/locks", json={"locks": {"scene": True}}
    )
    assert locks.status_code == 200, locks.text
    assert locks.json()["locks"] == {"scene": True}
    res = client.patch(
        f"/api/projects/{project_id}/scenes/{scene['scene_number']}",
        json={"asset_id": _asset_ids(director)[-1]},
    )
    assert res.status_code in (200, 409)
    body = res.json()
    assert body.get("blocked") is True or res.status_code == 409


# ------------------------------------- materiali per scena: alternative e upload
def test_scene_alternatives_come_from_real_vault(client, director):
    project_id = _project_from(director, _asset_ids(director)[:2], "vault_only")
    scenes = client.get(f"/api/projects/{project_id}/scenes").json()["scenes"]
    res = client.get(f"/api/projects/{project_id}/scenes/{scenes[0]['scene_number']}/alternatives")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    for alt in body["alternatives"]:
        assert alt["asset_id"] in {a["id"] for a in director.vault.list_assets()}
        assert alt["thumb_url"].startswith("/api/vault/")


def test_scene_upload_route_replaces_material(client, director, tmp_path):
    project_id = _project_from(director, _asset_ids(director)[:2], "vault_only")
    scenes = client.get(f"/api/projects/{project_id}/scenes").json()["scenes"]
    scene = scenes[0]
    path = tmp_path / "scena_nuova.png"
    Image.new("RGB", (1080, 1920), (10, 150, 90)).save(path)
    with path.open("rb") as handle:
        res = client.post(
            f"/api/projects/{project_id}/scenes/{scene['scene_number']}/media",
            files={"file": ("scena_nuova.png", handle, "image/png")},
            data={"category": "foto"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["asset"]["filename"].endswith("scena_nuova.png")
    updated = next(s for s in body["scenes"] if s["scene_number"] == scene["scene_number"])
    assert updated["asset_id"] == body["asset"]["id"]


# --------------------------------------------------- inventario motori reale
def test_engine_inventory_is_honest(client):
    engines_module.reset_cache()
    res = client.get("/api/media/engines?refresh=true")
    assert res.status_code == 200, res.text
    body = res.json()
    ids = {e["id"] for e in body["engines"]}
    assert {
        "ffmpeg",
        "ffprobe",
        "remotion",
        "hyperframes_internal",
        "hyperframes_external",
        "whisper",
        "faster_whisper",
        "chatterbox_tts",
        "elevenlabs",
        "piper_tts",
        "edge_tts",
        "qwen3_tts",
    } <= ids
    ffmpeg = next(e for e in body["engines"] if e["id"] == "ffmpeg")
    assert ffmpeg["available"] is True and ffmpeg["test_state"] == "superato"
    internal = next(e for e in body["engines"] if e["id"] == "hyperframes_internal")
    external = next(e for e in body["engines"] if e["id"] == "hyperframes_external")
    assert internal["available"] is True
    assert external["id"] != internal["id"]
    if not external["available"]:
        assert external["test_detail"], "un motore assente deve dichiarare il motivo"
    remotion = next(e for e in body["engines"] if e["id"] == "remotion")
    if not remotion["available"]:
        assert remotion["missing"] or remotion["test_detail"]


def test_engine_test_route_really_executes(client):
    ok = client.post("/api/media/engines/ffmpeg/test")
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert "ffmpeg" in body["detail"].lower()
    missing = client.post("/api/media/engines/chatterbox_tts/test")
    assert missing.status_code in (200, 400, 409)
    if missing.status_code == 200:
        assert missing.json()["ok"] is False
        assert missing.json()["detail"]


def test_voices_route_lists_engine_availability(client):
    res = client.get("/api/media/voices")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert isinstance(body["voices"], list)
    tts_ids = {e["id"] for e in body["tts_engines"]}
    assert {"elevenlabs", "piper_tts", "edge_tts", "qwen3_tts", "chatterbox_tts"} <= tts_ids
    for engine in body["tts_engines"]:
        if not engine["available"]:
            assert engine["detail"] or engine["requirements"]
    # nessuna voce sintetica sostitutiva viene presentata come Brando
    for voice in body["voices"]:
        if voice.get("id") in {"brando", "tiziana"} and voice.get("status") != "operativa_api":
            assert voice.get("elevenlabs_configured") is False
