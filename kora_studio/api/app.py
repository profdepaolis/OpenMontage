"""FastAPI backend & static SPA server for KORA STUDIO."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from kora_studio.agents.cinematic_editor import PRESET_DEFINITIONS
from kora_studio.agents.director import KoraDirector
from kora_studio.agents.hyperframes_captions import (
    CAPTION_STYLES,
    HYPERFRAMES_OPTIONS_CATALOG,
    recommend_hyperframes_for_theme,
)
from kora_studio.backup.backup_manager import BackupManager
from kora_studio.config import BACKUPS_DIR, DATA_DIR as KORA_DATA_DIR, REPO_ROOT
from kora_studio.db.database import Database, utcnow_iso
from kora_studio.db.models import CreativeConcept, CreativeRequestPayload, StoryboardScene
from kora_studio.media_tools import check_remotion_runtime, find_ffmpeg, find_ffprobe

FRONTEND_OUT_DIR = REPO_ROOT / "kora_studio" / "frontend" / "out"
MAX_UPLOAD_BYTES = 150 * 1024 * 1024
ALLOWED_LOCAL_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
]
if os.environ.get("KORA_ALLOWED_ORIGIN"):
    ALLOWED_LOCAL_ORIGINS.append(os.environ["KORA_ALLOWED_ORIGIN"].strip())

LOCAL_CLIENT_HOSTS = ("127.0.0.1", "::1", "localhost", "testclient")
LOCAL_HOST_HEADERS = ("127.0.0.1", "localhost", "::1", "testserver")


def _trusted_extra_hosts() -> set[str]:
    """Host aggiuntivi esplicitamente autorizzati (es. tunnel/preview), opt-in via ambiente."""
    raw = os.environ.get("KORA_TRUSTED_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _trusted_host_suffixes() -> tuple[str, ...]:
    """Suffissi di dominio esplicitamente autorizzati (es. .e2b.app per una preview remota)."""
    raw = os.environ.get("KORA_TRUSTED_HOST_SUFFIXES", "")
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


def _host_is_trusted(hostname: str, extra: set[str], suffixes: tuple[str, ...]) -> bool:
    host = (hostname or "").strip().lower()
    if not host:
        return False
    if host in LOCAL_HOST_HEADERS or host in extra:
        return True
    return any(host == suf.lstrip(".") or host.endswith(suf) for suf in suffixes)


def _cors_origin_regex() -> Optional[str]:
    """Regex CORS per host/suffissi fidati opt-in (preview remota). None se non configurato."""
    import re

    parts = [re.escape(h) for h in sorted(_trusted_extra_hosts())]
    parts += [rf"[A-Za-z0-9.-]*{re.escape(s)}" for s in _trusted_host_suffixes()]
    if not parts:
        return None
    return r"^https?://(" + "|".join(parts) + r")(:\d+)?$"


def _require_local_trust(request: Request) -> None:
    """Protect sensitive credential/budget/backup/generation endpoints against remote/CSRF access."""
    extra_hosts = _trusted_extra_hosts()
    suffixes = _trusted_host_suffixes()
    # La modalità preview remota (opt-in) accetta proxy fidati: il vincolo passa sul Host header.
    remote_mode = bool(extra_hosts or suffixes)

    client_host = (request.client.host if request.client else "") or ""
    if client_host not in LOCAL_CLIENT_HOSTS and not remote_mode:
        raise HTTPException(
            status_code=403,
            detail="Accesso negato: endpoint sensibile riservato all'host locale 127.0.0.1.",
        )
    host_header = (request.headers.get("host") or "").split(":")[0].strip().lower()
    if host_header and not _host_is_trusted(host_header, extra_hosts, suffixes):
        raise HTTPException(
            status_code=403,
            detail=f"Host header non autorizzato: {host_header}",
        )
    origin = request.headers.get("origin")
    if origin and origin not in ALLOWED_LOCAL_ORIGINS:
        from urllib.parse import urlparse

        origin_host = (urlparse(origin).hostname or "").lower()
        if not _host_is_trusted(origin_host, extra_hosts, suffixes):
            raise HTTPException(
                status_code=403,
                detail=f"Origine non autorizzata: {origin}",
            )


def _safe_workspace_file(path: Path) -> Path:
    import kora_studio.config as cfg

    resolved = path.resolve()
    allowed_roots = [cfg.DATA_DIR.resolve(), cfg.REPO_ROOT.resolve()]
    if not any(resolved.is_relative_to(r) for r in allowed_roots):
        raise HTTPException(status_code=403, detail="Percorso file non consentito.")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File non trovato su disco.")
    return resolved


# ---- Request Schemas ------------------------------------------------------


ThemeRequest = CreativeRequestPayload


class CreateProjectRequest(BaseModel):
    title: str
    theme: str
    mode: str = "theme"
    preset: str = "KORA Cinematic Identity"
    production_mode: str = "AUTO"
    voice_id: str = "brando"
    target_duration_sec: float = Field(default=20.0, ge=8.0, le=90.0)
    concept: Optional[dict[str, Any]] = None
    caption_style: Optional[str] = None
    hyperframes: Optional[dict[str, Any]] = None
    creative_request: Optional[dict[str, Any]] = None
    auto_start: bool = True


class ProjectSyncRequest(BaseModel):
    caption_style: Optional[str] = None
    hyperframes: Optional[dict[str, Any]] = None
    voice_id: Optional[str] = None
    preset: Optional[str] = None
    music_id: Optional[str] = None
    include_music: Optional[bool] = None
    music_start_sec: Optional[float] = None
    music_volume: Optional[float] = None
    voice_volume: Optional[float] = None
    auto_sync_scenes_to_voice: bool = False


class ProduceRequest(BaseModel):
    include_subtitles: bool = True
    include_no_subtitles_variant: bool = False
    include_no_music_variant: bool = False
    explicit_paid_authorization: bool = False
    sync: bool = False


class RevisionRequest(BaseModel):
    instruction: str
    explicit_paid_authorization: bool = False
    sync: bool = False


class BrandUpdateRequest(BaseModel):
    profile: dict[str, Any]
    note: str = "Aggiornamento profilo KORA"


class ProviderKeyRequest(BaseModel):
    api_key: str


class VoiceIdMappingRequest(BaseModel):
    elevenlabs_voice_id: str


class BudgetUpdateRequest(BaseModel):
    paid_budget_eur: Optional[float] = None
    monthly_cap_eur: Optional[float] = None


class DetailCropRequest(BaseModel):
    x_norm: float = 0.5
    y_norm: float = 0.5
    zoom: float = 1.8
    detail_label: str = "dettaglio reale"


class StoryboardUpdateRequest(BaseModel):
    scenes: list[dict[str, Any]]


class VeoVideoRequest(BaseModel):
    mode: str = "image_to_video"  # image_to_video | text_to_video
    prompt: str = ""
    duration_sec: int = 5
    asset_id: Optional[str] = None
    camera_motion: str = "movimento lento e naturale"
    explicit_authorization: bool = False


class AIVideoRequest(BaseModel):
    engine: str = "google_veo_api"  # google_veo_api | fal_ai | qwen_wan_api | huggingface_api
    model: Optional[str] = None
    mode: str = "image_to_video"
    prompt: str = ""
    duration_sec: int = 5
    asset_id: Optional[str] = None
    camera_motion: str = "movimento lento e naturale"
    explicit_authorization: bool = False


def create_app(db_path: Optional[Path] = None, auto_seed: bool = True) -> FastAPI:
    db = Database(db_path=db_path)
    director = KoraDirector(db=db)
    backup_mgr = BackupManager(db=db)
    if auto_seed:
        director.vault.seed_kora_authentic_assets()
        director.brand_brain.refresh_palette_from_vault()

    app = FastAPI(title="KORA STUDIO", version="1.0.0")
    app.state.director = director
    app.state.backup_mgr = backup_mgr

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_LOCAL_ORIGINS,
        allow_origin_regex=_cors_origin_regex(),
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Kora-Local-Token"],
    )

    # ---- Health & Diagnostics --------------------------------------------

    @app.get("/health")
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "app": "KORA STUDIO",
            "brand": "KORA Beach Club — Policoro",
            "payoff": "The Art of Being",
            "ffmpeg": find_ffmpeg(),
            "ffprobe": find_ffprobe(),
            "remotion": check_remotion_runtime(),
            "db_schema_version": db.get_schema_version(),
            "presets": list(PRESET_DEFINITIONS.values()),
            "caption_styles": list(CAPTION_STYLES.values()),
            "hyperframes_options": HYPERFRAMES_OPTIONS_CATALOG,
            "preferences": db.get_all_preferences(),
        }

    # ---- Brand Brain -----------------------------------------------------

    @app.get("/api/brand")
    def get_brand() -> dict[str, Any]:
        return director.brand_brain.get_current()

    @app.put("/api/brand")
    def update_brand(req: BrandUpdateRequest) -> dict[str, Any]:
        return director.brand_brain.save_version(req.profile, updated_by="utente", note=req.note)

    @app.get("/api/brand/history")
    def brand_history() -> list[dict[str, Any]]:
        return director.brand_brain.get_history()

    @app.post("/api/brand/refresh-palette")
    def refresh_palette() -> dict[str, Any]:
        return director.brand_brain.refresh_palette_from_vault()

    @app.post("/api/brand/rollback/{version}")
    def rollback_brand(version: int) -> dict[str, Any]:
        return director.brand_brain.rollback(version)

    # ---- Media Vault & Sound Library -------------------------------------

    @app.get("/api/vault")
    def list_vault(
        media_type: Optional[str] = None, category: Optional[str] = None
    ) -> list[dict[str, Any]]:
        return director.vault.list_assets(media_type=media_type, category=category)

    @app.get("/api/vault/search")
    def search_vault(q: str = Query(""), media_type: Optional[str] = None) -> list[dict[str, Any]]:
        return director.vault.semantic_search(q, media_type=media_type, limit=20)

    @app.post("/api/vault/upload")
    async def upload_media(
        files: list[UploadFile] = File(...),
        category: Optional[str] = Form(default=None),
        usage_rights: str = Form(default="autorizzato_kora"),
    ) -> dict[str, Any]:
        ingested: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        with tempfile.TemporaryDirectory(prefix="kora_upload_") as tmp:
            for f in files:
                fname = Path(f.filename or "upload.bin").name
                dest = Path(tmp) / fname
                try:
                    content = await f.read()
                    if len(content) > MAX_UPLOAD_BYTES:
                        raise ValueError(f"File troppo grande (> {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
                    dest.write_bytes(content)
                    item = director.vault.ingest_file(
                        dest,
                        origin="upload_utente",
                        usage_rights=usage_rights,
                        category=category,
                        original_filename=fname,
                    )
                    ingested.append(item)
                except Exception as exc:
                    errors.append({"filename": fname, "error": str(exc)})

        suggested_concepts: list[dict[str, Any]] = []
        if ingested:
            ids = [a["id"] for a in ingested if a["media_type"] in ("image", "video")]
            concepts = director.creative.ideate_from_theme(
                "Momenti autentici dai nuovi materiali KORA",
                preferred_asset_ids=ids,
            )
            suggested_concepts = [c.model_dump() for c in concepts]
        return {"uploaded": ingested, "errors": errors, "suggested_concepts": suggested_concepts}

    @app.get("/api/vault/{asset_id}/file")
    def get_vault_file(asset_id: str) -> FileResponse:
        asset = director.vault.get_asset(asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset non trovato")
        p = _safe_workspace_file(Path(asset["working_path"] or asset["original_path"]))
        return FileResponse(p)

    @app.get("/api/vault/{asset_id}/thumb")
    def get_vault_thumb(asset_id: str) -> FileResponse:
        asset = director.vault.get_asset(asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset non trovato")
        for cand in (asset.get("thumbnail_path"), asset.get("working_path"), asset.get("original_path")):
            if cand and Path(cand).is_file():
                return FileResponse(_safe_workspace_file(Path(cand)))
        raise HTTPException(status_code=404, detail="Anteprima non disponibile")

    @app.post("/api/vault/{asset_id}/detail-crop")
    def create_detail_crop(asset_id: str, req: DetailCropRequest) -> dict[str, Any]:
        try:
            return director.media_lab.create_detail_crop(
                asset_id=asset_id,
                x_norm=req.x_norm,
                y_norm=req.y_norm,
                zoom=req.zoom,
                detail_label=req.detail_label,
                ingest_to_vault=True,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/sounds")
    def list_sounds(category: Optional[str] = None) -> list[dict[str, Any]]:
        return director.sound_library.list_sounds(category=category)

    # ---- Creative Studio & Voices ----------------------------------------

    @app.post("/api/creative/surprise")
    def creative_surprise(req: ThemeRequest) -> dict[str, Any]:
        env = director.creative.suggest_four_themes(req)
        return {
            "concepts": [c.model_dump() for c in env.concepts[:3]],
            "generation_engine": env.generation_engine,
            "fallback_reason": env.fallback_reason,
        }

    @app.post("/api/creative/from-theme")
    def creative_from_theme(req: ThemeRequest) -> dict[str, Any]:
        env = director.creative.ideate_from_request(req)
        suggestion = recommend_hyperframes_for_theme(req.theme)
        return {
            "concepts": [c.model_dump() for c in env.concepts],
            "generation_engine": env.generation_engine,
            "fallback_reason": env.fallback_reason,
            "recommendation": suggestion,
        }

    @app.post("/api/creative/suggest")
    def creative_suggest(req: ThemeRequest) -> dict[str, Any]:
        """Genera 4 nuove proposte narrative originali KORA (non raccomandazioni Hyperframes)."""
        env = director.creative.suggest_four_themes(req)
        return {
            "concepts": [c.model_dump() for c in env.concepts],
            "generation_engine": env.generation_engine,
            "fallback_reason": env.fallback_reason,
        }

    @app.post("/api/hyperframes/recommend")
    def hyperframes_recommend(req: ThemeRequest) -> dict[str, Any]:
        """Endpoint dedicato alle raccomandazioni HyperFrames (stile, grana, title card, motion)."""
        return recommend_hyperframes_for_theme(req.theme)

    @app.get("/api/voices")
    def list_voices() -> list[dict[str, Any]]:
        return director.voice_director.list_voices()

    @app.get("/api/voices/{voice_id}/sample")
    def voice_sample(
        voice_id: str,
        engine: str = Query("auto"),
    ) -> FileResponse:
        from kora_studio.config import PROJECTS_DIR
        sample_dir = PROJECTS_DIR / "_voice_samples"
        try:
            res = director.voice_director.generate_voice_sample(
                voice_id=voice_id,
                work_dir=sample_dir,
                engine=engine,
            )
            p = Path(res["path"])
            return FileResponse(
                p,
                media_type=res["media_type"],
                filename=p.name,
                headers={"X-Voice-Engine": res["engine"]},
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # ---- Projects, Production, Revisions & Exports -----------------------

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        return director.list_projects()

    @app.post("/api/projects")
    def create_project(req: CreateProjectRequest) -> dict[str, Any]:
        concept = CreativeConcept(**req.concept) if req.concept else None
        prefs = {}
        if req.creative_request:
            prefs["creative_request"] = req.creative_request
        proj = director.create_project(
            title=req.title,
            theme=req.theme,
            mode=req.mode,
            preset=req.preset,
            production_mode=req.production_mode,
            voice_id=req.voice_id,
            target_duration_sec=req.target_duration_sec,
            concept=concept,
            preferences=prefs,
        )
        if req.caption_style or req.hyperframes:
            proj = director.sync_project_audio_and_caption(
                proj["id"],
                caption_style=req.caption_style,
                hyperframes=req.hyperframes,
            )
        job = None
        if req.auto_start and req.production_mode == "AUTO":
            job = director.create_job(proj["id"], "full_production")
            t = threading.Thread(
                target=director.run_production_pipeline,
                kwargs={"project_id": proj["id"], "job_id": job["id"]},
                daemon=True,
            )
            t.start()
        return {"project": director.get_project(proj["id"]), "job": job}

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        proj = director.get_project(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Progetto non trovato")
        return proj

    @app.put("/api/projects/{project_id}/storyboard")
    def update_storyboard(project_id: str, req: StoryboardUpdateRequest) -> dict[str, Any]:
        proj = director.get_project(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Progetto non trovato")
        fps = max(1, int(proj.get("fps") or 25))
        cursor_sec = 0.0
        cursor_frame = 0
        validated: list[dict[str, Any]] = []
        for idx, raw_scene in enumerate(req.scenes, start=1):
            sc = StoryboardScene(**raw_scene)
            dur_s = max(1.0, float(sc.duration_sec or 3.0))
            df = max(fps, int(round(dur_s * fps)))
            dur_s = round(df / fps, 3)
            sc.scene_number = idx
            sc.start_sec = round(cursor_sec, 3)
            sc.end_sec = round(cursor_sec + dur_s, 3)
            sc.duration_sec = dur_s
            sc.start_frame = cursor_frame
            sc.duration_frames = df
            validated.append(sc.model_dump())
            cursor_sec += dur_s
            cursor_frame += df

        total_frames = sum(int(s.get("duration_frames") or 25) for s in validated)
        target_dur = round(total_frames / fps, 2)
        with db.session() as conn:
            conn.execute(
                "UPDATE projects SET storyboard_json = ?, total_frames = ?, target_duration_sec = ?, updated_at = ? WHERE id = ?",
                (json.dumps(validated, ensure_ascii=False), total_frames, target_dur, utcnow_iso(), project_id),
            )
        director.save_project_snapshot(project_id, "Modifica manuale storyboard", "RegiaUtente")
        return director.get_project(project_id)  # type: ignore

    @app.post("/api/projects/{project_id}/sync-audio")
    def sync_project_audio(project_id: str, req: ProjectSyncRequest) -> dict[str, Any]:
        try:
            if req.voice_id or req.preset:
                proj_cur = director.get_project(project_id)
                if not proj_cur:
                    raise KeyError(f"Progetto {project_id} non trovato")
                with db.session() as conn:
                    conn.execute(
                        "UPDATE projects SET voice_id = ?, preset = ?, updated_at = ? WHERE id = ?",
                        (
                            req.voice_id or proj_cur["voice_id"],
                            req.preset or proj_cur["preset"],
                            utcnow_iso(),
                            project_id,
                        ),
                    )
            return director.sync_project_audio_and_caption(
                project_id=project_id,
                caption_style=req.caption_style,
                hyperframes=req.hyperframes,
                music_id=req.music_id,
                include_music=req.include_music,
                music_start_sec=req.music_start_sec,
                music_volume=req.music_volume,
                voice_volume=req.voice_volume,
                auto_sync_scenes_to_voice=req.auto_sync_scenes_to_voice,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/projects/{project_id}/produce")
    def produce_project(project_id: str, req: ProduceRequest) -> dict[str, Any]:
        proj = director.get_project(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Progetto non trovato")
        try:
            job = director.create_job(project_id, "full_production")
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if req.sync:
            return director.run_production_pipeline(
                project_id=project_id,
                job_id=job["id"],
                include_subtitles=req.include_subtitles,
                include_no_subtitles_variant=req.include_no_subtitles_variant,
                include_no_music_variant=req.include_no_music_variant,
                explicit_paid_authorization=req.explicit_paid_authorization,
            )
        t = threading.Thread(
            target=director.run_production_pipeline,
            kwargs={
                "project_id": project_id,
                "job_id": job["id"],
                "include_subtitles": req.include_subtitles,
                "include_no_subtitles_variant": req.include_no_subtitles_variant,
                "include_no_music_variant": req.include_no_music_variant,
                "explicit_paid_authorization": req.explicit_paid_authorization,
            },
            daemon=True,
        )
        t.start()
        return {"project": proj, "job": job}

    @app.post("/api/projects/{project_id}/revision/preview")
    def preview_revision(project_id: str, req: RevisionRequest) -> dict[str, Any]:
        proj = director.get_project(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Progetto non trovato")
        scenes = [StoryboardScene(**s) for s in proj["storyboard"]]
        prop = director.revision_engine.preview_revision(
            req.instruction, scenes, proj.get("audio_plan") or {}
        )
        return prop.model_dump()

    @app.post("/api/projects/{project_id}/revision/apply")
    def apply_revision(project_id: str, req: RevisionRequest) -> dict[str, Any]:
        proj = director.get_project(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Progetto non trovato")
        if req.sync:
            return director.apply_nl_revision_and_rerender(
                project_id, req.instruction, req.explicit_paid_authorization
            )
        # Apply storyboard diff immediately and run selective re-render in background
        scenes = [StoryboardScene(**s) for s in proj["storyboard"]]
        new_scenes, new_audio, prop = director.revision_engine.apply_revision(
            req.instruction, scenes, proj.get("audio_plan") or {}
        )
        with db.session() as conn:
            conn.execute(
                "UPDATE projects SET storyboard_json = ?, audio_plan_json = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps([s.model_dump() for s in new_scenes], ensure_ascii=False),
                    json.dumps(new_audio, ensure_ascii=False),
                    utcnow_iso(),
                    project_id,
                ),
            )
        director.save_project_snapshot(
            project_id, f"Revisione selettiva: {req.instruction}", "RevisionEngine"
        )
        job = director.create_job(project_id, "selective_revision")
        t = threading.Thread(
            target=director.run_production_pipeline,
            kwargs={
                "project_id": project_id,
                "job_id": job["id"],
                "explicit_paid_authorization": req.explicit_paid_authorization,
            },
            daemon=True,
        )
        t.start()
        return {
            "project": director.get_project(project_id),
            "job": job,
            "revision_proposal": prop.model_dump(),
        }

    @app.get("/api/projects/{project_id}/versions")
    def project_versions(project_id: str) -> list[dict[str, Any]]:
        return director.get_project_versions(project_id)

    @app.post("/api/projects/{project_id}/versions/{version}/restore")
    def restore_version(project_id: str, version: int) -> dict[str, Any]:
        return director.restore_project_version(project_id, version)

    @app.get("/api/projects/{project_id}/video")
    def stream_project_video(project_id: str) -> FileResponse:
        proj = director.get_project(project_id)
        if not proj or not proj.get("output_video_path"):
            raise HTTPException(status_code=404, detail="Video non ancora renderizzato")
        p = _safe_workspace_file(Path(proj["output_video_path"]))
        return FileResponse(p, media_type="video/mp4", filename=p.name)

    @app.get("/api/projects/{project_id}/download/{kind}")
    def download_export_artifact(project_id: str, kind: str) -> FileResponse:
        proj = director.get_project(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Progetto non trovato")
        bundle = proj.get("export_bundle") or {}
        mapping = {
            "reel": bundle.get("reel_mp4"),
            "cover": bundle.get("cover_png"),
            "caption": bundle.get("caption_txt"),
            "no_subtitles": bundle.get("no_subtitles_mp4"),
            "no_music": bundle.get("no_music_mp4"),
            "editable_json": bundle.get("editable_project_json"),
            "report_json": bundle.get("report_json"),
            "report_md": bundle.get("report_md"),
        }
        target = mapping.get(kind)
        if not target:
            raise HTTPException(status_code=404, detail=f"Artefatto '{kind}' non disponibile")
        p = _safe_workspace_file(Path(target))
        return FileResponse(p, filename=p.name)

    # ---- Jobs ------------------------------------------------------------

    @app.get("/api/jobs")
    def list_jobs(project_id: Optional[str] = None) -> list[dict[str, Any]]:
        return director.list_jobs(project_id=project_id)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = director.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job non trovato")
        return job

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        return director.cancel_job(job_id)

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str) -> dict[str, Any]:
        old = director.get_job(job_id)
        if not old:
            raise HTTPException(status_code=404, detail="Job non trovato")
        new_job = director.create_job(old["project_id"], "retry_production")
        t = threading.Thread(
            target=director.run_production_pipeline,
            kwargs={"project_id": old["project_id"], "job_id": new_job["id"]},
            daemon=True,
        )
        t.start()
        return new_job

    # ---- AI Providers & Budget -------------------------------------------

    @app.get("/api/providers", dependencies=[Depends(_require_local_trust)])
    def list_providers() -> list[dict[str, Any]]:
        return director.providers.list_providers()

    @app.post("/api/providers/{provider_id}/key", dependencies=[Depends(_require_local_trust)])
    def set_provider_key(provider_id: str, req: ProviderKeyRequest) -> dict[str, Any]:
        try:
            return director.providers.set_api_key(provider_id, req.api_key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/providers/remotion_local/setup", dependencies=[Depends(_require_local_trust)])
    def setup_remotion() -> dict[str, Any]:
        return director.providers.setup_remotion()

    @app.post("/api/providers/piper_tts_local/install-model", dependencies=[Depends(_require_local_trust)])
    def install_piper_model() -> dict[str, Any]:
        try:
            return director.providers.install_piper_italian_voice()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/providers/elevenlabs_api/voices", dependencies=[Depends(_require_local_trust)])
    def list_elevenlabs_account_voices() -> list[dict[str, Any]]:
        try:
            return director.providers.fetch_elevenlabs_voices()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/media-lab/veo", dependencies=[Depends(_require_local_trust)])
    def run_veo_media_lab(req: VeoVideoRequest) -> dict[str, Any]:
        try:
            return director.media_lab.execute_veo_video(
                mode=req.mode,
                prompt=req.prompt,
                duration_sec=req.duration_sec,
                asset_id=req.asset_id,
                camera_motion=req.camera_motion,
                explicit_authorization=req.explicit_authorization,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/media-lab/video", dependencies=[Depends(_require_local_trust)])
    def run_ai_video_media_lab(req: AIVideoRequest) -> dict[str, Any]:
        try:
            return director.media_lab.execute_ai_video(
                engine=req.engine,
                model=req.model,
                mode=req.mode,
                prompt=req.prompt,
                duration_sec=req.duration_sec,
                asset_id=req.asset_id,
                camera_motion=req.camera_motion,
                explicit_authorization=req.explicit_authorization,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/providers/{provider_id}/check", dependencies=[Depends(_require_local_trust)])
    def check_provider(provider_id: str) -> dict[str, Any]:
        try:
            return director.providers.check_provider(provider_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.put("/api/voices/{voice_id}/elevenlabs-id", dependencies=[Depends(_require_local_trust)])
    def set_voice_elevenlabs_id(voice_id: str, req: VoiceIdMappingRequest) -> dict[str, Any]:
        try:
            return director.voice_director.set_elevenlabs_voice_id(voice_id, req.elevenlabs_voice_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/budget", dependencies=[Depends(_require_local_trust)])
    def get_budget() -> dict[str, Any]:
        return director.providers.cost_guard.get_budget_settings()

    @app.put("/api/budget", dependencies=[Depends(_require_local_trust)])
    def update_budget(req: BudgetUpdateRequest) -> dict[str, Any]:
        try:
            return director.providers.cost_guard.update_budget_settings(
                paid_budget_eur=req.paid_budget_eur,
                monthly_cap_eur=req.monthly_cap_eur,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/budget/history", dependencies=[Depends(_require_local_trust)])
    def budget_history() -> list[dict[str, Any]]:
        return director.providers.cost_guard.get_usage_history()

    # ---- Backup & Maintenance --------------------------------------------

    @app.get("/api/backup/integrity", dependencies=[Depends(_require_local_trust)])
    def check_integrity() -> dict[str, Any]:
        return backup_mgr.check_integrity()

    @app.post("/api/backup/create", dependencies=[Depends(_require_local_trust)])
    def create_backup() -> dict[str, Any]:
        return backup_mgr.create_backup(include_originals=True)

    @app.post("/api/backup/clean-cache", dependencies=[Depends(_require_local_trust)])
    def clean_cache() -> dict[str, Any]:
        return backup_mgr.clean_cache()

    @app.get("/api/backup/list", dependencies=[Depends(_require_local_trust)])
    def list_backups() -> list[dict[str, Any]]:
        if not BACKUPS_DIR.is_dir():
            return []
        return [
            {
                "filename": p.name,
                "size_bytes": p.stat().st_size,
                "path": str(p),
            }
            for p in sorted(BACKUPS_DIR.glob("*.zip"), reverse=True)
        ]

    # ---- Static Next.js UI -----------------------------------------------

    if FRONTEND_OUT_DIR.is_dir():
        next_assets = FRONTEND_OUT_DIR / "_next"
        if next_assets.is_dir():
            app.mount("/_next", StaticFiles(directory=next_assets), name="next_assets")

        @app.get("/", response_class=HTMLResponse)
        def serve_index() -> HTMLResponse:
            idx = FRONTEND_OUT_DIR / "index.html"
            if idx.is_file():
                return HTMLResponse(idx.read_text(encoding="utf-8"))
            return HTMLResponse("<h1>KORA STUDIO — build frontend in corso</h1>")

    return app


app = create_app()
