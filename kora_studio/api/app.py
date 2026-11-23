"""FastAPI backend & static SPA server for KORA STUDIO."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from kora_studio.agents.cinematic_editor import PRESET_DEFINITIONS
from kora_studio.ai_engine.agents import AGENTS
from kora_studio.ai_engine.manager import PROVIDER_ORDER as PROVIDER_IDS
from kora_studio.agents.director import KoraDirector
from kora_studio.agents.hyperframes_captions import (
    CAPTION_STYLES,
    HYPERFRAMES_OPTIONS_CATALOG,
    recommend_hyperframes_for_theme,
)
from kora_studio.backup.backup_manager import BackupManager
from kora_studio.config import BACKUPS_DIR, DATA_DIR as KORA_DATA_DIR, PROJECTS_DIR, REPO_ROOT

ERROR_LOG_PATH = KORA_DATA_DIR / "logs" / "errors.log"


def _log_error(exc: BaseException, context: str, extra: Optional[dict[str, Any]] = None) -> None:
    """Registra l'errore reale: senza traccia non si può spiegare un 500 all'utente."""
    import traceback
    from datetime import datetime, timezone

    try:
        ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]
        payload = ""
        if extra:
            payload = " | " + " · ".join(f"{k}={v}" for k, v in extra.items() if v not in (None, ""))
        with ERROR_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {context}: {type(exc).__name__}: {exc}{payload}\n{detail}\n")
    except Exception:
        pass


def _recent_errors(limit: int = 5) -> list[str]:
    try:
        if not ERROR_LOG_PATH.is_file():
            return []
        lines = ERROR_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        head = [line for line in lines if line.startswith("[") and "] " in line]
        return head[-limit:]
    except Exception:
        return []
from kora_studio.db.database import Database, utcnow_iso
from kora_studio.db.models import CreativeConcept, CreativeRequestPayload, StoryboardScene
from kora_studio.media_tools import check_remotion_runtime, find_ffmpeg, find_ffprobe
from kora_studio.media.engines import engines_inventory, test_engine

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
    asset_ids: Optional[list[str]] = None
    destination_dir: Optional[str] = None
    auto_start: bool = True


class SceneReviseRequest(BaseModel):
    instruction: str
    regenerate_preview: bool = True


class SceneApproveRequest(BaseModel):
    approved: bool = True


class SceneUpdateRequest(BaseModel):
    """Modifica manuale di una scena già presente nello storyboard."""

    spoken_text: Optional[str] = None
    subtitle_text: Optional[str] = None
    asset_id: Optional[str] = None
    duration_sec: Optional[float] = None
    camera_motion: Optional[str] = None
    transition: Optional[str] = None
    narrative_goal: Optional[str] = None
    # v3.5 — regia, visual e caption per singola scena
    shot_framing: Optional[str] = None
    motion_intensity: Optional[float] = None
    motion_easing: Optional[str] = None
    motion_speed: Optional[float] = None
    composition: Optional[str] = None
    crop_focus: Optional[dict[str, float]] = None
    caption_style: Optional[str] = None
    caption_overrides: Optional[dict[str, Any]] = None


class MediaSelectionRequest(BaseModel):
    """3.6.2 — provenienza dei materiali scelta dall'utente."""

    mode: str = "vault_auto"
    asset_ids: list[str] = []
    priority: Optional[str] = None
    note: Optional[str] = None


class SceneLocksRequest(BaseModel):
    locks: dict[str, bool] = {}


class SceneMoveRequest(BaseModel):
    direction: str = "down"


class SceneReorderRequest(BaseModel):
    order: list[int]


class CaptionApplyRequest(BaseModel):
    preset: str = "kora_editorial"
    overrides: Optional[dict[str, Any]] = None
    scene_number: Optional[int] = None


class CaptionTitleRequest(BaseModel):
    text: str = ""


class ScriptProposeRequest(BaseModel):
    scope: str = "scene"
    scene_numbers: list[int] = []
    instruction: str = ""


class ScriptApplyRequest(BaseModel):
    scope: str = "group"
    proposals: list[dict[str, Any]] = []


class DirectionProposeRequest(BaseModel):
    instruction: str


class DirectionApplyRequest(BaseModel):
    changes: list[dict[str, Any]] = []


class SceneGeneratorRequest(BaseModel):
    mode: str = "A"
    scene_number: int = 1
    prompt: str = ""
    engine: Optional[str] = None
    reference_asset_id: Optional[str] = None
    duration_sec: int = 5
    explicit_authorization: bool = False
    vertical: bool = True


class SceneCreateRequest(BaseModel):
    """Nuova scena inserita dall'utente (materiale obbligatorio)."""

    asset_id: str
    spoken_text: str = ""
    subtitle_text: str = ""
    duration_sec: float = 4.0
    insert_after: Optional[int] = None
    camera_motion: Optional[str] = None
    transition: Optional[str] = None
    narrative_goal: Optional[str] = None
    shot_framing: Optional[str] = None


class SceneTextRequest(BaseModel):
    instruction: str = ""


class VisionReanalyzeRequest(BaseModel):
    force: bool = True
    limit: Optional[int] = None
    production_id: Optional[str] = None


class AIEngineTestRequest(BaseModel):
    provider: str
    model: Optional[str] = None
    prompt: str = "Rispondi solo con: KORA OK"
    multimodal: bool = False
    asset_id: Optional[str] = None


class AIEngineVisionTestRequest(BaseModel):
    provider: str
    model: Optional[str] = None


class ProjectExportRequest(BaseModel):
    destination: str = Field(..., min_length=1)
    folder_name: Optional[str] = None


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
    #: l'utente accetta di procedere nonostante gli avvisi di preflight
    force: bool = False


class RevisionRequest(BaseModel):
    instruction: str
    explicit_paid_authorization: bool = False
    sync: bool = False


class BrandUpdateRequest(BaseModel):
    profile: dict[str, Any]
    note: str = "Aggiornamento profilo KORA"


class ProviderKeyRequest(BaseModel):
    api_key: str


class EngineConnectRequest(BaseModel):
    """Collegamento assistito di un motore creativo (oggi: Google AI Studio)."""

    api_key: str = ""
    model: str = ""


class VoiceIdMappingRequest(BaseModel):
    elevenlabs_voice_id: str


class BudgetUpdateRequest(BaseModel):
    paid_budget_eur: Optional[float] = None
    monthly_cap_eur: Optional[float] = None


class ModelConfigRequest(BaseModel):
    roles: Optional[dict[str, str]] = None
    timeouts: Optional[dict[str, float]] = None
    policy: Optional[str] = None
    mode: Optional[str] = None


class ModelTestRequest(BaseModel):
    model: str
    kind: str = "quick"
    timeout: Optional[float] = None


class BenchmarkRequest(BaseModel):
    kind: str = "quick"
    models: Optional[list[str]] = None


class RestoreRequest(BaseModel):
    backup_path: Optional[str] = None
    confirm: bool = False


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


_DIRECTOR_CACHE: dict[str, "KoraDirector"] = {}


def _director_for(db: "Database", key: str) -> "KoraDirector":
    """Un solo direttore per database nello stesso processo.

    Evita due orchestrazioni concorrenti sullo stesso stato (job, cache, anteprime)
    quando l'app viene ricreata — per esempio nei test o dopo un riavvio del server.
    """
    from kora_studio.agents.director import DIRECTOR_REGISTRY

    cached = _DIRECTOR_CACHE.get(key) or DIRECTOR_REGISTRY.get(key)
    if cached is not None:
        _DIRECTOR_CACHE[key] = cached
        return cached
    created = KoraDirector(db=db)
    _DIRECTOR_CACHE[key] = created
    return created


def create_app(db_path: Optional[Path] = None, auto_seed: bool = True) -> FastAPI:
    db = Database(db_path=db_path)
    import kora_studio.config as _cfg

    cache_key = str(Path(db_path or _cfg.DB_PATH).expanduser().resolve())
    director = _director_for(db, cache_key)
    backup_mgr = BackupManager(db=db)
    if auto_seed:
        # un asset non leggibile non deve impedire l'avvio dell'app
        try:
            director.vault.seed_kora_authentic_assets()
        except Exception as exc:  # pragma: no cover - dipende dai media locali
            _log_error(exc, "seed_kora_authentic_assets")
            print(f"[KORA] Avviso: import iniziale del Media Vault incompleto ({exc})")
        try:
            director.brand_brain.refresh_palette_from_vault()
        except Exception as exc:  # pragma: no cover
            _log_error(exc, "refresh_palette_from_vault")

    app = FastAPI(title="KORA STUDIO", version="3.6.4")

    def _v35(fn: Any) -> Any:
        """Errori v3.5 tradotti in risposte parlanti: 404 scena, 400 richiesta, 409 blocco."""
        try:
            result = fn()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if isinstance(result, dict) and result.get("blocked"):
            return result
        return result

    app.state.director = director
    app.state.backup_mgr = backup_mgr

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception):  # pragma: no cover - difensivo
        """Mai un 500 muto: l'utente vede la causa, il log la conserva."""
        from fastapi.responses import JSONResponse

        _log_error(exc, f"{request.method} {request.url.path}")
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"{type(exc).__name__}: {str(exc)[:900] or 'errore interno'}",
                "path": request.url.path,
                "hint": "Dettagli completi in ~/.kora/logs/errors.log",
            },
        )
    # 2.8.1 — scoperta motori in background: nessuna scansione completa per richiesta
    try:
        director.ai_engine.start_snapshot_refresher()
    except Exception:
        pass

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

    # ------------------------------------------------- 3.6.2 inventario motori
    @app.get("/api/media/engines")
    def media_engines(refresh: bool = False) -> dict[str, Any]:
        """Inventario REALE dei motori audiovisivi installati su questa macchina."""
        return engines_inventory(director.providers, refresh=bool(refresh))

    @app.post("/api/media/engines/{engine_id}/test", dependencies=[Depends(_require_local_trust)])
    def media_engine_test(engine_id: str) -> dict[str, Any]:
        """Test operativo esplicito: nessuna installazione, nessuna spesa."""
        return test_engine(engine_id, director.providers)

    @app.get("/api/media/voices")
    def media_voices() -> dict[str, Any]:
        """Voci dichiarate dal Voice Studio + motori TTS realmente disponibili.

        Brando e Tiziana restano «operativi» solo se esiste una registrazione nel
        Vault o una voce ElevenLabs autorizzata: nessuna voce sintetica sostitutiva
        viene spacciata per loro.
        """
        try:
            voices = director.voice_director.list_voices()
        except Exception as exc:
            return {"ok": False, "detail": f"{exc.__class__.__name__}: {exc}", "voices": []}
        inventory = engines_inventory(director.providers)
        tts = [
            {
                "id": e["id"],
                "label": e["label"],
                "installed": e["installed"],
                "configured": e.get("configured", False),
                "available": e["available"],
                "detail": e.get("test_detail") or "",
                "requirements": e.get("requirements", ""),
            }
            for e in inventory["engines"]
            if e["kind"] == "tts"
        ]
        return {
            "ok": True,
            "voices": voices,
            "tts_engines": tts,
            "import_voiceover": True,
            "no_voiceover": True,
        }

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

    @app.post("/api/creative/suggest-lite")
    def creative_suggest_lite(
        req: ThemeRequest,
        provider: Optional[str] = Query(None),
        model: Optional[str] = Query(None),
        avoid_cache: bool = Query(False),
    ) -> dict[str, Any]:
        """2.8.1 Fast Creative Mode: avvia subito, risponde con run_id (nessun blocco HTTP)."""
        ideation = director.creative.ideation
        run_id = ideation.start(req, provider=provider, model=model, avoid_cache=avoid_cache)
        return {
            "run_id": run_id,
            "status": "running",
            "budget_ms": round(ideation._budget_sec() * 1000, 1),
            "stages": [
                {"stage": "context", "label": "Preparazione contesto"},
                {"stage": "connect", "label": "Connessione motore AI"},
                {"stage": "generate", "label": "Generazione temi"},
                {"stage": "validate", "label": "Validazione proposte"},
                {"stage": "done", "label": "Completato"},
            ],
            "poll_url": f"/api/creative/suggest-lite/{run_id}",
        }

    @app.post("/api/creative/suggest-lite/prewarm")
    def creative_suggest_lite_prewarm() -> dict[str, Any]:
        """Pre-generazione facoltativa: parte solo se autorizzata e a costo zero."""
        return director.creative.ideation.prewarm()

    @app.get("/api/creative/suggest-lite/{run_id}")
    def creative_suggest_lite_status(run_id: str) -> dict[str, Any]:
        snapshot = director.creative.ideation.get(run_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Richiesta inesistente o scaduta")
        return snapshot

    @app.post("/api/creative/suggest-lite/{run_id}/cancel")
    def creative_suggest_lite_cancel(run_id: str) -> dict[str, Any]:
        return director.creative.ideation.cancel(run_id)

    @app.post("/api/creative/suggest-lite/{run_id}/decide")
    def creative_suggest_lite_decide(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Politica «Chiedi conferma»: la scelta è dell'utente, non del codice."""
        try:
            result = director.creative.ideation.decide(run_id, str(payload.get("action") or ""))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Decisione non applicata: {exc}") from exc
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result.get("detail") or "decisione non valida")
        return result

    @app.get("/api/creative/suggest-lite/{run_id}/stream")
    def creative_suggest_lite_stream(run_id: str) -> StreamingResponse:
        """SSE con gli stage reali del backend (in alternativa al polling)."""
        import json as _json

        ideation = director.creative.ideation

        def _events():
            last = 0
            while True:
                snapshot = ideation.get(run_id)
                if snapshot is None:
                    yield "event: error\ndata: " + _json.dumps({"detail": "run inesistente"}) + "\n\n"
                    return
                events = snapshot["events"]
                for event in events[last:]:
                    yield f"event: stage\ndata: {_json.dumps(event, ensure_ascii=False)}\n\n"
                last = len(events)
                if snapshot["finished"]:
                    yield f"event: result\ndata: {_json.dumps(snapshot, ensure_ascii=False)}\n\n"
                    return
                time.sleep(0.2)

        return StreamingResponse(
            _events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/creative/suggest-lite-cache/stats")
    def creative_suggest_lite_cache_stats() -> dict[str, Any]:
        cache = director.creative.ideation.cache
        try:
            with db.session() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS entries, COALESCE(SUM(hits), 0) AS hits FROM theme_proposal_cache"
                ).fetchone()
                return {"entries": int(row["entries"]), "hits": int(row["hits"])}
        except Exception:
            return {"entries": 0, "hits": 0}

    @app.post("/api/creative/suggest-lite-cache/clear")
    def creative_suggest_lite_cache_clear() -> dict[str, Any]:
        return {"cleared": director.creative.ideation.cache.clear()}

    @app.post("/api/creative/project-reel")
    def creative_project_reel(payload: dict[str, Any]) -> dict[str, Any]:
        """Essential: UNA richiesta creativa completa per progettare il Reel scelto."""
        from kora_studio.agents.reel_designer import ReelDesigner
        from kora_studio.db.models import ThemeProposalLite

        raw_request = payload.get("request") or {}
        try:
            request = CreativeRequestPayload(**raw_request)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Richiesta non valida: {exc}")
        from kora_studio.agents.director import normalize_media_selection

        if normalize_media_selection(request.media_selection) == "upload_only" and not request.asset_ids:
            raise HTTPException(
                status_code=400,
                detail="Hai scelto solo materiali nuovi: carica almeno un file prima di progettare il Reel.",
            )
        proposal = None
        if payload.get("proposal"):
            try:
                proposal = ThemeProposalLite(**payload["proposal"])
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Proposta non valida: {exc}")
        try:
            design = director.design_reel(
                request, proposal, deadline_sec=float(payload.get("deadline_sec") or 45.0)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Progettazione non riuscita: {exc}")
        if design.generation_engine == "deterministic_offline_fallback":
            ReelDesigner.log_deterministic(director, design.timings_ms, design.fallback_reason or "")
        result: dict[str, Any] = {
            "design": {
                "concept": design.concept.model_dump(),
                "scenes": [s.model_dump() for s in design.scenes],
                "caption": design.caption,
                "cover": design.cover,
                "sound_design": design.sound_design,
                "generation_engine": design.generation_engine,
                "model": design.model,
                "fallback_reason": design.fallback_reason,
                "timings_ms": design.timings_ms,
                "calls": design.calls,
            },
            "project": None,
        }
        if payload.get("create_project", True):
            try:
                project = director.create_project_from_design(
                    design,
                    theme=(proposal.title if proposal else request.theme) or design.concept.title,
                    mode=str(payload.get("mode") or "suggest"),
                    production_mode=str(payload.get("production_mode") or "AUTO"),
                    destination_dir=payload.get("destination_dir"),
                    hyperframes=payload.get("hyperframes"),
                    caption_style=payload.get("caption_style"),
                )
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Creazione progetto non riuscita: {exc}")
            result["project"] = project
        return result

    @app.get("/api/studio/bootstrap")
    def studio_bootstrap() -> dict[str, Any]:
        """Essential: solo i dati della schermata Studio. Nessuna scansione provider."""
        try:
            voices = director.voice_director.list_voices()
        except Exception:
            voices = []
        try:
            projects = director.list_projects()[:6]
        except Exception:
            projects = []
        try:
            assets = director.vault.list_assets()
        except Exception:
            assets = []
        categories: dict[str, int] = {}
        for asset in assets:
            if asset.get("category") == "Logo":
                continue
            categories[str(asset.get("category") or "Altro")] = categories.get(
                str(asset.get("category") or "Altro"), 0
            ) + 1
        engine_summary = {
            "active_provider": "",
            "state": "non verificato",
            "model": "",
            "essential": True,
            "mode": "",
            "role": "",
            "model_label": "",
            "last_test": None,
            "last_inference": None,
            "fallback_model": "",
            "migration": None,
            "policy": "",
            "detail": "",
        }
        engine = director.ai_engine
        try:
            # 3.6.1 — provider/modello mostrati = quelli realmente usati dal Model Manager
            status = director.model_manager.status(engine)
            engine_summary.update(
                {
                    "active_provider": status.get("provider") or "",
                    "model": status.get("model") or "",
                    "model_label": status.get("model_label") or "",
                    "mode": status.get("mode") or "",
                    "role": status.get("role") or "",
                    "policy": status.get("policy") or "",
                    "last_test": status.get("last_test"),
                    "last_inference": status.get("last_inference"),
                    "fallback_model": status.get("fallback_model") or "",
                    "migration": status.get("migration"),
                    "detail": status.get("detail") or "",
                }
            )
            conf = engine.essential()
            engine_summary["essential"] = conf["enabled"]
            snapshot = engine.snapshot()
            info = ((snapshot.get("providers") or {}).get(conf["active_provider"]) or {})
            if info:
                engine_summary["state"] = str(info.get("state") or "")
            elif status.get("installed"):
                engine_summary["state"] = "installato"
        except Exception:
            pass
        try:
            with db.session() as conn:
                row = conn.execute("SELECT COUNT(*) AS c FROM theme_proposal_cache").fetchone()
                cached = int(row["c"]) if row else 0
        except Exception:
            cached = 0
        return {
            "ai_status": engine_summary,
            "voices": voices,
            "recent_projects": projects,
            "vault": {
                "total": sum(categories.values()),
                "categories": dict(sorted(categories.items())),
            },
            "engine": engine_summary,
            "cached_proposals": cached,
        }

    @app.post("/api/creative/expand-theme")
    def creative_expand_theme(payload: dict[str, Any]) -> dict[str, Any]:
        """Espande la proposta lite scelta nei concept completi (solo dopo la selezione)."""
        from kora_studio.db.models import ThemeProposalLite

        try:
            proposal = ThemeProposalLite(**payload["proposal"])
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Proposta non valida: {exc}")
        concept = director.creative.expand_lite_proposal(
            proposal,
            duration_sec=payload.get("duration_sec"),
            preferred_asset_ids=payload.get("asset_ids"),
        )
        suggestion = recommend_hyperframes_for_theme(concept.title)
        return {
            "concepts": [concept.model_dump()],
            "generation_engine": concept.generation_engine,
            "fallback_reason": None,
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
        if req.destination_dir:
            raw_dest = req.destination_dir.strip()
            if raw_dest and not Path(raw_dest).expanduser().is_absolute():
                raise HTTPException(
                    status_code=400,
                    detail="La cartella di destinazione deve essere un percorso assoluto (es. ~/Desktop/KORA-REELS).",
                )
            prefs["destination_dir"] = raw_dest
        if req.asset_ids and concept is not None:
            valid_ids = {a["id"] for a in director.vault.list_assets()}
            selected = [aid for aid in req.asset_ids if aid in valid_ids]
            if not selected:
                raise HTTPException(
                    status_code=400, detail="Nessuno dei materiali selezionati esiste nel Media Vault."
                )
            concept.available_asset_ids = selected
            prefs["selected_asset_ids"] = selected
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

    @app.get(
        "/api/projects/{project_id}/voiceover/script",
        dependencies=[Depends(_require_local_trust)],
    )
    def voiceover_script(project_id: str) -> dict[str, Any]:
        """Copione parlato pronto da incollare sul sito ElevenLabs (o altro TTS) e reimportare come MP3."""
        proj = director.get_project(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Progetto non trovato")
        segments: list[dict[str, Any]] = []
        for sc in proj["storyboard"]:
            text = (sc.get("spoken_text") or "").strip()
            if not text:
                continue
            segments.append(
                {
                    "scene_number": sc.get("scene_number"),
                    "text": text,
                    "start_sec": sc.get("start_sec"),
                    "end_sec": sc.get("end_sec"),
                }
            )
        full_text = " ".join(seg["text"] for seg in segments)
        return {
            "project_id": project_id,
            "title": proj["title"],
            "voice_id": proj.get("voice_id"),
            "segments": segments,
            "full_text": full_text,
            "segmented_text": "\n\n".join(seg["text"] for seg in segments),
            "char_count": len(full_text),
            "target_duration_sec": proj.get("target_duration_sec"),
            "elevenlabs_url": "https://elevenlabs.io/app/speech-synthesis",
            "instructions": (
                "1) Copia il copione e incollalo su elevenlabs.io scegliendo la voce desiderata. "
                "2) Scarica l'MP3. 3) Caricalo qui: KORA riallinea automaticamente le scene alla durata reale "
                "della voce e lo usa al posto della sintesi automatica (nessun costo API)."
            ),
        }

    @app.post(
        "/api/projects/{project_id}/voiceover/upload",
        dependencies=[Depends(_require_local_trust)],
    )
    def upload_voiceover(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        """Importa un voiceover già pronto (MP3/WAV/M4A) e riallinea lo storyboard alla sua durata."""
        proj = director.get_project(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Progetto non trovato")
        suffix = Path(file.filename or "voce.mp3").suffix.lower() or ".mp3"
        if suffix not in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"):
            raise HTTPException(
                status_code=400,
                detail=f"Formato audio non supportato ({suffix}). Usa MP3, WAV, M4A, AAC, OGG o FLAC.",
            )
        target_dir = KORA_DATA_DIR / "voiceovers"
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / f"{project_id}{suffix}"
        written = 0
        with dest.open("wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="File audio troppo grande (max 150 MB).")
                out.write(chunk)
        try:
            updated = director.apply_uploaded_voiceover(project_id, dest, file.filename or dest.name)
        except Exception as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "project": updated,
            "voiceover": {
                "filename": file.filename or dest.name,
                "path": str(dest),
                "size_bytes": written,
                "duration_sec": updated.get("target_duration_sec"),
                "total_frames": updated.get("total_frames"),
            },
        }

    @app.delete(
        "/api/projects/{project_id}/voiceover",
        dependencies=[Depends(_require_local_trust)],
    )
    def clear_voiceover(project_id: str) -> dict[str, Any]:
        proj = director.get_project(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Progetto non trovato")
        director.voice_director.clear_project_voiceover(project_id)
        return {"project_id": project_id, "cleared": True}

    @app.post("/api/projects/{project_id}/produce")
    def produce_project(project_id: str, req: ProduceRequest) -> dict[str, Any]:
        proj = director.get_project(project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Progetto non trovato")

        # preflight: non si parte con problemi bloccanti già rilevabili
        preflight = director.preview_readiness(project_id)
        blocking = [
            check for check in preflight["checks"] if check["blocking"] and not check["ok"]
        ]
        if blocking and not req.force:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Produzione non avviata: mancano requisiti bloccanti. Puoi forzare l'avvio.",
                    "blocking": [
                        {"id": c["id"], "label": c["label"], "detail": c["detail"]} for c in blocking
                    ],
                    "warnings": [
                        {"id": c["id"], "label": c["label"], "detail": c["detail"]}
                        for c in preflight["checks"]
                        if not c["ok"] and not c["blocking"]
                    ],
                    "preview_note": preflight.get("preview_note"),
                },
            )

        try:
            job = director.create_job(project_id, "full_production")
        except RuntimeError as exc:
            # già in produzione: nessun rendering duplicato
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

    @app.post("/api/projects/{project_id}/export-to", dependencies=[Depends(_require_local_trust)])
    def export_project_to_folder(project_id: str, req: ProjectExportRequest) -> dict[str, Any]:
        """Copia il pacchetto completo del Reel (MP4, cover, caption, report, progetto) in una cartella scelta."""
        try:
            return director.export_project_bundle(
                project_id, req.destination, archive=False, folder_name=req.folder_name
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"Impossibile scrivere nella cartella indicata: {exc}")

    @app.post("/api/projects/{project_id}/archive", dependencies=[Depends(_require_local_trust)])
    def archive_project(project_id: str, req: ProjectExportRequest) -> dict[str, Any]:
        """Esporta in locale e archivia il progetto (resta ripristinabile, sparisce dai progetti attivi)."""
        try:
            return director.export_project_bundle(
                project_id, req.destination, archive=True, folder_name=req.folder_name
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"Impossibile scrivere nella cartella indicata: {exc}")

    @app.post("/api/projects/{project_id}/unarchive", dependencies=[Depends(_require_local_trust)])
    def unarchive_project(project_id: str) -> dict[str, Any]:
        try:
            return director.unarchive_project(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.delete("/api/projects/{project_id}", dependencies=[Depends(_require_local_trust)])
    def delete_project(project_id: str) -> dict[str, Any]:
        """Elimina definitivamente il progetto e tutti i file generati (irreversibile)."""
        if director.has_active_job(project_id):
            raise HTTPException(
                status_code=409, detail="Job in corso su questo progetto: attendi o annulla prima di eliminarlo."
            )
        try:
            return director.delete_project(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/api/projects/{project_id}/video")
    def stream_project_video(project_id: str) -> FileResponse:
        proj = director.get_project(project_id)
        if not proj or not proj.get("output_video_path"):
            raise HTTPException(status_code=404, detail="Video non ancora renderizzato")
        p = _safe_workspace_file(Path(proj["output_video_path"]))
        return FileResponse(p, media_type="video/mp4", filename=p.name)

    # ---- Cinematic Preview (v2.8) ---------------------------------------

    @app.post("/api/projects/{project_id}/preview", dependencies=[Depends(_require_local_trust)])
    def project_preview(project_id: str, force: bool = Query(False)) -> dict[str, Any]:
        try:
            return director.render_preview(project_id, force=force)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # ffmpeg, font, file mancante: motivo esplicito
            import re as _re

            exit_code = None
            match = _re.search(r"Errore FFmpeg \((\d+)\)", str(exc))
            if match:
                exit_code = match.group(1)
            _log_error(
                exc,
                f"preview:{project_id}",
                extra={
                    "fase": "render_anteprima",
                    "exit_code": exit_code,
                    "scene": (getattr(exc, "scene_number", None) or ""),
                    "risorsa": (getattr(exc, "resource", None) or ""),
                },
            )
            hint = ""
            if "FFmpeg" in str(exc) or isinstance(exc, FileNotFoundError):
                hint = " — FFmpeg non è raggiungibile dal processo KORA: verifica con `which ffmpeg` e riavvia."
            detail = f"{type(exc).__name__}: {str(exc)[:900]}{hint}"
            raise HTTPException(status_code=500, detail=detail) from exc

    def _preview_dir(project_id: str) -> Path:
        from kora_studio.config import PROJECTS_DIR as _projects_dir

        return _projects_dir / project_id / "preview"

    @app.post("/api/projects/{project_id}/scenes/{scene_number}/preview")
    def project_scene_preview(project_id: str, scene_number: int) -> dict[str, Any]:
        """Anteprima a bassa risoluzione della sola scena indicata (cache + revisione)."""
        return _v35(lambda: director.render_scene_preview(project_id, scene_number))

    @app.get("/api/projects/{project_id}/scenes/{scene_number}/preview/video")
    def project_scene_preview_video(project_id: str, scene_number: int) -> FileResponse:
        from pathlib import Path as _Path

        info = _v35(lambda: director.render_scene_preview(project_id, scene_number))
        clip = _Path(str(info.get("clip") or ""))
        if not clip.is_file():
            raise HTTPException(status_code=404, detail="Anteprima della scena non disponibile")
        return FileResponse(clip, media_type="video/mp4", filename=clip.name)

    @app.get("/api/projects/{project_id}/preview")
    def project_preview_manifest(project_id: str) -> dict[str, Any]:
        manifest_path = _preview_dir(project_id) / "preview_manifest.json"
        if not manifest_path.is_file():
            raise HTTPException(status_code=404, detail="Nessuna anteprima disponibile per questo progetto")
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    @app.get("/api/projects/{project_id}/preview/video")
    def project_preview_video(project_id: str) -> FileResponse:
        manifest_path = _preview_dir(project_id) / "preview_manifest.json"
        if not manifest_path.is_file():
            raise HTTPException(status_code=404, detail="Nessuna anteprima disponibile")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        path = Path(manifest.get("preview_mp4") or "")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="File di anteprima mancante")
        return FileResponse(_safe_workspace_file(path), media_type="video/mp4", filename=path.name)

    @app.get("/api/projects/{project_id}/preview/scene/{scene_number}/thumb/{kind}")
    def project_preview_thumb(project_id: str, scene_number: int, kind: str) -> FileResponse:
        manifest_path = _preview_dir(project_id) / "preview_manifest.json"
        if not manifest_path.is_file():
            raise HTTPException(status_code=404, detail="Nessuna anteprima disponibile")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        card = next((c for c in manifest.get("cards", []) if c.get("scene_number") == scene_number), None)
        if not card:
            raise HTTPException(status_code=404, detail="Scena non presente nell'anteprima")
        key = "thumbnail_source" if kind == "source" else "thumbnail_derived"
        path = Path(card.get(key) or "")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Miniatura non disponibile")
        return FileResponse(_safe_workspace_file(path), media_type="image/png")

    # ---- Editor delle scene (v3.2): aggiungi/togli/modifica, testo AI, avanzamento
    @app.get("/api/projects/{project_id}/scenes", dependencies=[Depends(_require_local_trust)])
    def project_scenes(project_id: str) -> dict[str, Any]:
        try:
            return director.scene_editor.list_scenes(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/projects/{project_id}/scenes/{scene_number}", dependencies=[Depends(_require_local_trust)])
    def project_scene_update(project_id: str, scene_number: int, req: SceneUpdateRequest) -> dict[str, Any]:
        patch = req.model_dump(exclude_none=True)
        if not patch:
            raise HTTPException(status_code=400, detail="Nessuna modifica indicata")
        try:
            return director.scene_editor.update_scene(project_id, scene_number, patch)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/media-selection", dependencies=[Depends(_require_local_trust)])
    def project_media_selection(project_id: str) -> dict[str, Any]:
        try:
            return director.media_selection(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/api/projects/{project_id}/media-selection", dependencies=[Depends(_require_local_trust)])
    def project_set_media_selection(project_id: str, req: MediaSelectionRequest) -> dict[str, Any]:
        try:
            return director.set_media_selection(
                project_id, req.mode, req.asset_ids, priority=req.priority, note=req.note
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/scenes/{scene_number}/alternatives")
    def project_scene_alternatives(project_id: str, scene_number: int, limit: int = 8) -> dict[str, Any]:
        """Alternative autentiche per una scena: stesso tipo di materiale, escluso quello in uso."""
        scenes = director.scene_editor.list_scenes(project_id).get("scenes") or []
        scene = next((s for s in scenes if int(s.get("scene_number") or 0) == scene_number), None)
        if scene is None:
            raise HTTPException(status_code=404, detail=f"Scena {scene_number} non trovata")
        current = str(scene.get("selected_asset_id") or "")
        current_asset = director.vault.get_asset(current) if current else None
        media_type = (current_asset or {}).get("media_type") or "image"
        items = [
            a
            for a in director.vault.list_assets()
            if a.get("media_type") == media_type and a.get("category") != "Logo" and a.get("id") != current
        ][: max(1, min(limit, 24))]
        alternatives = [
            {
                **a,
                "asset_id": a.get("id"),
                "thumb_url": f"/api/vault/{a.get('id')}/thumb",
                "is_current": False,
                "score": float(a.get("quality_score") or 0.0),
                "reason": f"{a.get('category') or 'archivio'} · {a.get('filename') or a.get('id')}",
            }
            for a in items
        ]
        return {
            "ok": True,
            "scene_number": scene_number,
            "media_type": media_type,
            "current_asset_id": current,
            "current": scene,
            "alternatives": alternatives,
        }

    @app.post("/api/projects/{project_id}/scenes/{scene_number}/media", dependencies=[Depends(_require_local_trust)])
    async def project_scene_media_upload(
        project_id: str,
        scene_number: int,
        file: UploadFile = File(...),
        category: Optional[str] = Form(default=None),
    ) -> dict[str, Any]:
        """Carica un nuovo materiale e lo assegna SUBITO alla scena, senza rigenerare altro."""
        filename = Path(file.filename or "scene.bin").name
        try:
            content = await file.read()
        except Exception as exc:  # pragma: no cover - I/O difensivo
            raise HTTPException(status_code=400, detail=f"Lettura del file non riuscita: {exc}") from exc
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File troppo grande (> {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
            )
        with tempfile.TemporaryDirectory(prefix="kora_scene_") as tmp:
            dest = Path(tmp) / filename
            dest.write_bytes(content)
            try:
                asset = director.vault.ingest_file(
                    dest,
                    origin="upload_utente",
                    category=category or "Materiale scena",
                    original_filename=filename,
                )
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Import non riuscito: {exc}") from exc
        if asset.get("media_type") not in ("image", "video"):
            raise HTTPException(status_code=400, detail="Servono una fotografia o un video")
        try:
            result = director.scene_editor.update_scene(project_id, scene_number, {"asset_id": asset["id"]})
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result.get("blocked"):
            return {
                "ok": False,
                "blocked": True,
                "reason": result.get("reason"),
                "asset": asset,
                "scene": result,
                "scenes": result.get("scenes") or [],
            }
        return {
            "ok": True,
            "asset": asset,
            "scene": result,
            "scenes": result.get("scenes") or [],
            "note": "Materiale sostituito solo in questa scena: voce, copione e altre scene restano intatti.",
        }

    @app.post("/api/projects/{project_id}/scenes", dependencies=[Depends(_require_local_trust)])
    def project_scene_add(project_id: str, req: SceneCreateRequest) -> dict[str, Any]:
        try:
            return director.scene_editor.add_scene(project_id, req.model_dump(exclude_none=True))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/projects/{project_id}/scenes/{scene_number}", dependencies=[Depends(_require_local_trust)])
    def project_scene_delete(project_id: str, scene_number: int) -> dict[str, Any]:
        try:
            return director.scene_editor.remove_scene(project_id, scene_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/captions/align", dependencies=[Depends(_require_local_trust)])
    def project_align_captions(project_id: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """3.6.4 — riallinea i sottotitoli al testo della scena (voce = sottotitolo).

        `{"scene_numbers": [2, 3, 5]}` limita l'intervento; senza corpo si allineano
        tutte le scene discordi. La voce registrata non viene toccata: cambia solo
        il testo mostrato, quindi l'anteprima va rigenerata (non l'audio).
        """
        numbers = None
        if payload:
            raw = payload.get("scene_numbers")
            if isinstance(raw, list) and raw:
                try:
                    numbers = [int(n) for n in raw]
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="scene_numbers non validi") from None
        try:
            return director.scene_editor.align_subtitles(project_id, numbers)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/scenes/{scene_number}/text", dependencies=[Depends(_require_local_trust)])
    def project_scene_text(project_id: str, scene_number: int, req: SceneTextRequest) -> dict[str, Any]:
        """Genera una riga di voice-over coerente con il reel; dichiara la fonte (engine o locale)."""
        try:
            return director.scene_editor.suggest_text(project_id, scene_number, req.instruction)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/projects/{project_id}/scenes/{scene_number}/text/apply",
        dependencies=[Depends(_require_local_trust)],
    )
    def project_scene_text_apply(project_id: str, scene_number: int, req: SceneTextRequest) -> dict[str, Any]:
        """Applica una proposta (o un testo scritto a mano) dopo la conferma dell'utente."""
        try:
            return director.scene_editor.apply_text(project_id, scene_number, req.instruction, source="utente")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ------------------------------------------------------------------ v3.5
    @app.post(
        "/api/projects/{project_id}/scenes/{scene_number}/duplicate",
        dependencies=[Depends(_require_local_trust)],
    )
    def project_scene_duplicate(project_id: str, scene_number: int) -> dict[str, Any]:
        return _v35(lambda: director.scene_editor.duplicate_scene(project_id, scene_number))

    @app.post(
        "/api/projects/{project_id}/scenes/{scene_number}/move",
        dependencies=[Depends(_require_local_trust)],
    )
    def project_scene_move(project_id: str, scene_number: int, req: SceneMoveRequest) -> dict[str, Any]:
        return _v35(lambda: director.scene_editor.move_scene(project_id, scene_number, req.direction))

    @app.post("/api/projects/{project_id}/scenes/reorder", dependencies=[Depends(_require_local_trust)])
    def project_scenes_reorder(project_id: str, req: SceneReorderRequest) -> dict[str, Any]:
        return _v35(lambda: director.scene_editor.reorder(project_id, req.order))

    @app.post(
        "/api/projects/{project_id}/scenes/{scene_number}/locks",
        dependencies=[Depends(_require_local_trust)],
    )
    def project_scene_locks(project_id: str, scene_number: int, req: SceneLocksRequest) -> dict[str, Any]:
        return _v35(lambda: director.scene_editor.set_locks(project_id, scene_number, req.locks))

    @app.post(
        "/api/projects/{project_id}/scenes/{scene_number}/restore-asset",
        dependencies=[Depends(_require_local_trust)],
    )
    def project_scene_restore_asset(project_id: str, scene_number: int) -> dict[str, Any]:
        return _v35(lambda: director.scene_editor.restore_asset(project_id, scene_number))

    @app.get("/api/projects/{project_id}/caption", dependencies=[Depends(_require_local_trust)])
    def project_caption(project_id: str) -> dict[str, Any]:
        return _v35(lambda: {**director.caption_designer.presets(), **director.caption_designer.current(project_id)})

    @app.post("/api/projects/{project_id}/caption/apply", dependencies=[Depends(_require_local_trust)])
    def project_caption_apply(project_id: str, req: CaptionApplyRequest) -> dict[str, Any]:
        return _v35(
            lambda: director.caption_designer.apply(project_id, req.preset, req.overrides, req.scene_number)
        )

    @app.post(
        "/api/projects/{project_id}/scenes/{scene_number}/title",
        dependencies=[Depends(_require_local_trust)],
    )
    def project_scene_title(project_id: str, scene_number: int, req: CaptionTitleRequest) -> dict[str, Any]:
        return _v35(lambda: director.caption_designer.set_title_text(project_id, scene_number, req.text))

    @app.post("/api/projects/{project_id}/script/propose", dependencies=[Depends(_require_local_trust)])
    def project_script_propose(project_id: str, req: ScriptProposeRequest) -> dict[str, Any]:
        return _v35(
            lambda: director.script_director.propose(project_id, req.scope, req.scene_numbers, req.instruction)
        )

    @app.post("/api/projects/{project_id}/script/apply", dependencies=[Depends(_require_local_trust)])
    def project_script_apply(project_id: str, req: ScriptApplyRequest) -> dict[str, Any]:
        return _v35(lambda: director.script_director.apply(project_id, req.proposals, req.scope))

    @app.post("/api/projects/{project_id}/direction/propose", dependencies=[Depends(_require_local_trust)])
    def project_direction_propose(project_id: str, req: DirectionProposeRequest) -> dict[str, Any]:
        return _v35(lambda: director.art_director.propose(project_id, req.instruction))

    @app.post("/api/projects/{project_id}/direction/apply", dependencies=[Depends(_require_local_trust)])
    def project_direction_apply(project_id: str, req: DirectionApplyRequest) -> dict[str, Any]:
        return _v35(lambda: director.art_director.apply(project_id, req.changes))

    @app.get("/api/scene-generator/modes", dependencies=[Depends(_require_local_trust)])
    def scene_generator_modes() -> dict[str, Any]:
        return _v35(lambda: director.scene_generator.modes())

    @app.post("/api/scene-generator/preflight", dependencies=[Depends(_require_local_trust)])
    def scene_generator_preflight(req: SceneGeneratorRequest) -> dict[str, Any]:
        return _v35(lambda: director.scene_generator.preflight(req.mode, req.engine))

    @app.get("/api/projects/{project_id}/scene-generator/history", dependencies=[Depends(_require_local_trust)])
    def scene_generator_history(project_id: str) -> dict[str, Any]:
        return _v35(lambda: director.scene_generator.history(project_id))

    @app.post("/api/projects/{project_id}/scene-generator/generate", dependencies=[Depends(_require_local_trust)])
    def scene_generator_generate(project_id: str, req: SceneGeneratorRequest) -> dict[str, Any]:
        return _v35(
            lambda: director.scene_generator.generate(
                project_id,
                req.scene_number,
                req.mode,
                req.prompt,
                req.reference_asset_id,
                req.duration_sec,
                req.explicit_authorization,
                req.vertical,
            )
        )

    @app.post(
        "/api/projects/{project_id}/scene-generator/{generation_id}/use",
        dependencies=[Depends(_require_local_trust)],
    )
    def scene_generator_use(project_id: str, generation_id: str) -> dict[str, Any]:
        return _v35(lambda: director.scene_generator.use_in_scene(project_id, generation_id))

    @app.post(
        "/api/projects/{project_id}/scene-generator/{generation_id}/discard",
        dependencies=[Depends(_require_local_trust)],
    )
    def scene_generator_discard(project_id: str, generation_id: str) -> dict[str, Any]:
        return _v35(lambda: director.scene_generator.discard(project_id, generation_id))

    @app.post(
        "/api/projects/{project_id}/scene-generator/{generation_id}/save",
        dependencies=[Depends(_require_local_trust)],
    )
    def scene_generator_save(project_id: str, generation_id: str) -> dict[str, Any]:
        return _v35(lambda: director.scene_generator.save_copy(project_id, generation_id))

    @app.get("/api/projects/{project_id}/progress", dependencies=[Depends(_require_local_trust)])
    def project_progress(project_id: str) -> dict[str, Any]:
        try:
            return director.project_progress(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/readiness", dependencies=[Depends(_require_local_trust)])
    def project_readiness(project_id: str) -> dict[str, Any]:
        """Cosa manca davvero per generare l'anteprima (ffmpeg, materiali, logo)."""
        try:
            return director.preview_readiness(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/system/diagnostics")
    def system_diagnostics() -> dict[str, Any]:
        """Stato reale dell'ambiente: serve a spiegare un errore 500 senza indovinare."""
        from kora_studio.media_tools import find_ffmpeg, find_ffprobe

        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe()
        board = director.ai_engine.status_board(refresh=False)
        return {
            "ffmpeg": {"path": ffmpeg, "ok": bool(ffmpeg)},
            "ffprobe": {"path": ffprobe, "ok": bool(ffprobe)},
            "fonts": {
                "playfair": (REPO_ROOT / "assets" / "kora" / "PlayfairDisplay.ttf").is_file(),
                "space_grotesk": (REPO_ROOT / "assets" / "kora" / "SpaceGrotesk.ttf").is_file(),
            },
            "data_dir": str(KORA_DATA_DIR),
            "engine": board.get("essential"),
            "recent_errors": _recent_errors(limit=5),
        }

    @app.post("/api/projects/{project_id}/scenes/{scene_number}/revise", dependencies=[Depends(_require_local_trust)])
    def revise_scene(project_id: str, scene_number: int, req: SceneReviseRequest) -> dict[str, Any]:
        try:
            return director.revise_scene_preview(
                project_id, scene_number, req.instruction, regenerate_preview=req.regenerate_preview
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/scenes/{scene_number}/approve", dependencies=[Depends(_require_local_trust)])
    def approve_scene(project_id: str, scene_number: int, req: SceneApproveRequest) -> dict[str, Any]:
        try:
            return director.approve_scene(project_id, scene_number, approved=req.approved)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ---- Visual Intelligence provenance (v2.8) --------------------------

    @app.get("/api/vault/vision/status")
    def vault_vision_status() -> dict[str, Any]:
        return director.creative.visual_intelligence.vault_status()

    @app.post("/api/vault/vision/reanalyze", dependencies=[Depends(_require_local_trust)])
    def vault_vision_reanalyze(req: VisionReanalyzeRequest) -> dict[str, Any]:
        vi = director.creative.visual_intelligence
        if req.force:
            return vi.annotate_vault(production_id=req.production_id, limit=req.limit, force=True)
        return vi.annotate_vault(production_id=req.production_id, limit=req.limit, force=False)

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

    # ---- AI Engine Manager (provider universali, routing, agenti) ---------
    @app.get("/api/ai-engine/status", dependencies=[Depends(_require_local_trust)])
    def ai_engine_status(refresh: bool = Query(False)) -> dict[str, Any]:
        return director.ai_engine.status_board(refresh=refresh)

    @app.get("/api/ai-engine/models", dependencies=[Depends(_require_local_trust)])
    def ai_engine_models(provider: str = Query(...)) -> list[dict[str, Any]]:
        try:
            return director.ai_engine.models(provider)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/ai-engine/routing", dependencies=[Depends(_require_local_trust)])
    def ai_engine_routing(agent_id: str = Query(...), requires_vision: bool = Query(False)) -> dict[str, Any]:
        if agent_id not in {a.id for a in AGENTS}:
            raise HTTPException(status_code=400, detail=f"Agente non valido: {agent_id}")
        plan = director.ai_engine.candidate_plan(agent_id, requires_vision=requires_vision)
        chosen = director.ai_engine.resolve(agent_id, requires_vision=requires_vision)
        return {"agent_id": agent_id, "plan": plan[:10], "selected": chosen}

    @app.put("/api/ai-engine/settings", dependencies=[Depends(_require_local_trust)])
    def ai_engine_update_settings(patch: dict[str, Any]) -> dict[str, Any]:
        try:
            director.ai_engine.update_settings(patch)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        board = director.ai_engine.status_board(refresh=True)
        # impostazioni "piatte" per la UI + stato completo dei provider
        return {**board, **board["settings"], "status": board}

    @app.post("/api/ai-engine/verify/{provider_id}", dependencies=[Depends(_require_local_trust)])
    def ai_engine_verify(provider_id: str, live: bool = Query(False)) -> dict[str, Any]:
        """Rileva installazione/autenticazione; con ``live=true`` esegue una generazione reale."""
        if provider_id not in PROVIDER_IDS:
            raise HTTPException(status_code=400, detail=f"Provider non valido: {provider_id}")
        probe = director.ai_engine.probe(provider_id, refresh=True)
        if not live:
            return probe
        if not probe.get("authenticated"):
            return {**probe, "live_test": {"ok": False, "detail": "provider non autenticato: nessun test live"}}
        live_result = director.ai_engine.test_provider(provider_id)
        return {
            **director.ai_engine.probe(provider_id, refresh=True),
            "live_test": live_result,
        }

    @app.post("/api/ai-engine/vision-test", dependencies=[Depends(_require_local_trust)])
    def ai_engine_vision_test(req: AIEngineVisionTestRequest) -> dict[str, Any]:
        """Prova multimodale con immagine a contenuto noto: verifica reale della visione."""
        if req.provider not in PROVIDER_IDS:
            raise HTTPException(status_code=400, detail=f"Provider non valido: {req.provider}")
        result = director.ai_engine.vision_probe(req.provider, model=req.model)
        result["provider_status"] = director.ai_engine.probe(req.provider, refresh=True)
        return result

    @app.get("/api/ai-engine/activity", dependencies=[Depends(_require_local_trust)])
    def ai_engine_activity(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
        """AI Activity Monitor: una riga per chiamata, senza prompt né credenziali."""
        engine = director.ai_engine
        rows = engine.activity.list(limit=limit)
        return {"calls": rows, "summary": engine.activity.summary(limit=limit)}

    @app.post("/api/ai-engine/cancel/{production_id}", dependencies=[Depends(_require_local_trust)])
    def ai_engine_cancel(production_id: str) -> dict[str, Any]:
        return director.ai_engine.cancel(production_id)

    @app.get("/api/ai-engine/media-services", dependencies=[Depends(_require_local_trust)])
    def ai_engine_media_services() -> dict[str, Any]:
        """Servizi media (voce/immagine/video) separati dai motori linguistici."""
        from kora_studio.ai_engine.agents import MEDIA_SERVICES

        return {"services": MEDIA_SERVICES, "note": "I motori di testo non producono media: servizi dedicati."}

    @app.post("/api/ai-engine/test", dependencies=[Depends(_require_local_trust)])
    def ai_engine_test(req: AIEngineTestRequest) -> dict[str, Any]:
        if req.multimodal:
            image_path = None
            if req.asset_id:
                asset = director.vault.get_asset(req.asset_id)
                if asset:
                    image_path = Path(asset["working_path"] or asset["original_path"])
            if image_path is None:
                photos = [a for a in director.vault.list_assets(media_type="photo")]
                if photos:
                    image_path = Path(photos[0]["working_path"] or photos[0]["original_path"])
            if image_path is None:
                return {"ok": False, "provider": req.provider, "detail": "Nessuna foto nel Vault per il test multimodale"}
        else:
            image_path = None
        result = director.ai_engine.test_provider(
            req.provider, prompt=req.prompt, model=req.model, image_path=image_path
        )
        if req.multimodal and not result.get("ok"):
            # un test foto fallito non deve lasciare il provider "operativo"
            result["provider_status"] = director.ai_engine.probe(req.provider, refresh=True)
        return result

    @app.post("/api/ai-engine/key/{provider_id}", dependencies=[Depends(_require_local_trust)])
    def ai_engine_set_key(provider_id: str, req: ProviderKeyRequest) -> dict[str, Any]:
        """Salva la chiave cifrata nel backend. Non viene mai restituita al frontend."""
        if provider_id not in PROVIDER_IDS:
            raise HTTPException(status_code=400, detail=f"Provider non valido: {provider_id}")
        if provider_id in ("opencode", "codex", "ollama_local"):
            raise HTTPException(
                status_code=400,
                detail="Questo motore usa l'autenticazione della CLI/installazione locale: nessuna chiave da salvare",
            )
        director.providers.set_api_key(provider_id, req.api_key)
        director.ai_engine.invalidate(provider_id)
        return director.ai_engine.probe(provider_id, refresh=True)

    @app.put("/api/ai-engine/agent/{agent_id}", dependencies=[Depends(_require_local_trust)])
    def ai_engine_set_agent(agent_id: str, conf: dict[str, Any]) -> dict[str, Any]:
        try:
            director.ai_engine.update_settings({"agents": {agent_id: conf}})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"agent": director.ai_engine.agent_configuration(agent_id)}

    @app.get("/api/providers", dependencies=[Depends(_require_local_trust)])
    def list_providers() -> list[dict[str, Any]]:
        return director.providers.list_providers()

    @app.post("/api/engines/gemini/connect", dependencies=[Depends(_require_local_trust)])
    def connect_gemini_engine(req: EngineConnectRequest) -> dict[str, Any]:
        """Un solo passaggio: valida la chiave, sceglie il modello, attiva Gemini (0 €)."""
        try:
            result = director.ai_engine.connect_gemini_api(req.api_key, model=req.model or None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:  # rete/limit: motivo esplicito, nessun fallback silenzioso
            raise HTTPException(status_code=502, detail=f"Collegamento non riuscito: {exc}")
        board = director.ai_engine.status_board(refresh=False)
        return {**result, "engine": board.get("essential"), "providers": board.get("providers")}

    @app.get("/api/engines/connect-options", dependencies=[Depends(_require_local_trust)])
    def engine_connect_options() -> dict[str, Any]:
        """Cosa può collegare l'utente adesso, con lo stato reale di ciascun motore."""
        board = director.ai_engine.status_board(refresh=False)
        states = {p["provider"]: p for p in board.get("providers") or []}
        # le estensioni spente non vengono monitorate dallo snapshot: qui serve lo
        # stato reale, così la UI può dire "collega" oppure "già pronto".
        for pid in ("gemini_api", "codex", "opencode"):
            try:
                # refresh=True: verifica esplicita, anche se l'estensione è spenta
                states[pid] = director.ai_engine.probe(pid, refresh=True)
            except Exception:
                states.setdefault(pid, {})
        gemini = states.get("gemini_api") or {}
        codex = states.get("codex") or {}
        opencode = states.get("opencode") or {}
        return {
            "essential": board.get("essential"),
            "options": [
                {
                    "id": "gemini_api",
                    "label": "Google Gemini (AI Studio)",
                    "cost": "costo non verificabile: dipende dal progetto Google",
                    "how": "incolla la API key da aistudio.google.com/apikey",
                    "ready": bool(gemini.get("authenticated")),
                    "state": gemini.get("state"),
                    "detail": gemini.get("detail"),
                    "link": "https://aistudio.google.com/apikey",
                },
                {
                    "id": "codex",
                    "label": "Codex CLI · ChatGPT Plus",
                    "cost": "incluso nel piano ChatGPT",
                    "how": "installa Codex e accedi con l'account ChatGPT",
                    "ready": bool(codex.get("authenticated")),
                    "state": codex.get("state"),
                    "detail": codex.get("detail"),
                    "link": "https://developers.openai.com/codex",
                },
                {
                    "id": "opencode",
                    "label": "OpenCode (già installato)",
                    "cost": "dipende dal modello collegato",
                    "how": "nessuna azione: usa l'account già configurato",
                    "ready": bool(opencode.get("authenticated")),
                    "state": opencode.get("state"),
                    "detail": opencode.get("detail"),
                    "link": "https://opencode.ai/docs",
                },
            ],
        }

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

    # ---- Modelli del motore creativo (v3.6) -------------------------------

    @app.get("/api/ai-models/config", dependencies=[Depends(_require_local_trust)])
    def get_model_config() -> dict[str, Any]:
        return director.model_manager.config()

    @app.put("/api/ai-models/config", dependencies=[Depends(_require_local_trust)])
    def update_model_config(req: ModelConfigRequest) -> dict[str, Any]:
        try:
            return director.model_manager.update(req.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/ai-models/refresh", dependencies=[Depends(_require_local_trust)])
    def refresh_models() -> dict[str, Any]:
        try:
            return director.model_manager.refresh(force=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/ai-models/search", dependencies=[Depends(_require_local_trust)])
    def search_models(q: str = "", provider: str = "") -> dict[str, Any]:
        try:
            return director.model_manager.search(q, provider)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/ai-models/diagnostics", dependencies=[Depends(_require_local_trust)])
    def model_diagnostics() -> dict[str, Any]:
        return director.model_manager.diagnostics()

    @app.get("/api/ai-models/status", dependencies=[Depends(_require_local_trust)])
    def model_status() -> dict[str, Any]:
        """Diagnostica visibile: provider e modello realmente attivi, ultimo esito."""
        return director.model_manager.status(director.ai_engine)

    @app.post("/api/ai-models/migrate-legacy", dependencies=[Depends(_require_local_trust)])
    def model_migrate_legacy() -> dict[str, Any]:
        """Riesegue la migrazione della vecchia configurazione (idempotente)."""
        return director.model_manager.migrate_legacy(director.ai_engine)

    @app.post("/api/ai-models/test", dependencies=[Depends(_require_local_trust)])
    def test_model(req: ModelTestRequest) -> dict[str, Any]:
        try:
            return director.model_manager.test_model(req.model, kind=req.kind, timeout=req.timeout)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/ai-models/benchmark", dependencies=[Depends(_require_local_trust)])
    def run_benchmark(req: BenchmarkRequest) -> dict[str, Any]:
        try:
            return director.model_manager.benchmark(kind=req.kind, models=req.models)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/ai-models/tests", dependencies=[Depends(_require_local_trust)])
    def model_tests(model: str = "", limit: int = 30) -> list[dict[str, Any]]:
        return director.model_manager.history(model=model, limit=limit)

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

    @app.post("/api/backup/restore", dependencies=[Depends(_require_local_trust)])
    def restore_backup(req: RestoreRequest) -> dict[str, Any]:
        if not req.confirm:
            raise HTTPException(
                status_code=400,
                detail="Ripristino non confermato: serve confirm=true (operazione che sovrascrive lo stato attuale).",
            )
        from pathlib import Path as _Path

        from kora_studio.config import BACKUPS_DIR

        target = _Path(req.backup_path) if req.backup_path else None
        if target is None:
            candidates = sorted(BACKUPS_DIR.glob("kora_studio_backup_*.zip"))
            if not candidates:
                raise HTTPException(status_code=404, detail="Nessun backup disponibile")
            target = candidates[-1]
        # solo backup dentro la cartella KORA: nessun percorso arbitrario
        try:
            target.resolve().relative_to(BACKUPS_DIR.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="Il file indicato non è un backup KORA")
        try:
            return backup_mgr.restore_backup(target, safety_backup=True)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

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
