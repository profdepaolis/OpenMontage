"""API & real E2E video production tests for KORA STUDIO."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from kora_studio.api.app import create_app
from kora_studio.media_tools import parse_fps, probe_media


def test_api_endpoints_and_full_e2e_reel_production(isolated_env):
    app = create_app(db_path=isolated_env["db_path"], auto_seed=True)
    client = TestClient(app)

    # 1. Health & Brand
    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    health = r_health.json()
    assert health["ok"] is True
    assert health["ffmpeg"] is not None
    assert health["ffprobe"] is not None

    # 2. Vault & Brand
    r_vault = client.get("/api/vault")
    assert r_vault.status_code == 200
    assets = r_vault.json()
    assert len(assets) >= 12

    r_brand = client.get("/api/brand")
    assert r_brand.status_code == 200
    assert r_brand.json()["payoff"] == "The Art of Being"

    # 3. Generate 3 distinct concepts
    r_concepts = client.post(
        "/api/creative/from-theme",
        json={"theme": "Il piacere di stare insieme davanti al mare.", "duration_sec": 20.0},
    )
    assert r_concepts.status_code == 200
    concepts = r_concepts.json()["concepts"]
    assert len(concepts) == 3

    # 4. Create project without auto_start, then produce synchronously
    chosen = concepts[0]
    r_proj = client.post(
        "/api/projects",
        json={
            "title": chosen["title"],
            "theme": "Il piacere di stare insieme davanti al mare.",
            "mode": "theme",
            "preset": chosen["preset"],
            "production_mode": "AUTO",
            "voice_id": "brando",
            "target_duration_sec": 20.0,
            "concept": chosen,
            "auto_start": False,
        },
    )
    assert r_proj.status_code == 200
    project_id = r_proj.json()["project"]["id"]

    r_prod = client.post(
        f"/api/projects/{project_id}/produce",
        json={"include_subtitles": True, "sync": True},
    )
    assert r_prod.status_code == 200
    result = r_prod.json()
    assert result["project"]["status"] == "Completato", f"Production failed: {result}"

    # 5. Verify actual rendered MP4 file via ffprobe
    r_get = client.get(f"/api/projects/{project_id}")
    assert r_get.status_code == 200
    proj = r_get.json()
    video_path = Path(proj["output_video_path"])
    assert video_path.is_file() and video_path.stat().st_size > 100_000

    meta = probe_media(video_path)
    v_streams = [s for s in meta["streams"] if s.get("codec_type") == "video"]
    a_streams = [s for s in meta["streams"] if s.get("codec_type") == "audio"]
    assert len(v_streams) == 1
    assert len(a_streams) == 1
    assert v_streams[0]["width"] == 1080
    assert v_streams[0]["height"] == 1920
    assert abs(parse_fps(v_streams[0]["r_frame_rate"]) - 25.0) < 0.1
    assert abs(float(meta["format"]["duration"]) - 20.0) < 0.25
    assert v_streams[0]["codec_name"] == "h264"

    # 6. Video streaming & downloads work
    assert client.get(f"/api/projects/{project_id}/video").status_code == 200
    assert client.get(f"/api/projects/{project_id}/download/cover").status_code == 200
    assert client.get(f"/api/projects/{project_id}/download/report_md").status_code == 200

    # 7. Natural-language revision diff preview + selective regeneration
    r_diff = client.post(
        f"/api/projects/{project_id}/revision/preview",
        json={"instruction": "Cambia soltanto la musica."},
    )
    assert r_diff.status_code == 200
    diff = r_diff.json()
    assert diff["target_scenes"] == []
    assert len(diff["preserved_scenes"]) >= 4

    r_apply = client.post(
        f"/api/projects/{project_id}/revision/apply",
        json={"instruction": "Cambia soltanto la musica.", "sync": True},
    )
    assert r_apply.status_code == 200
    assert r_apply.json()["project"]["status"] == "Completato"

    # Rollback to version 1
    r_rb = client.post(f"/api/projects/{project_id}/versions/1/restore")
    assert r_rb.status_code == 200

    # 8. Backup & integrity check
    r_bak = client.post("/api/backup/create")
    assert r_bak.status_code == 200
    assert Path(r_bak.json()["backup_path"]).is_file()

    r_int = client.get("/api/backup/integrity")
    assert r_int.status_code == 200
    assert r_int.json()["healthy"] is True
