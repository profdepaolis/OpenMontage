"""2.8.1 — Fast Creative Mode per "Suggerisci un tema".

Quattro proposte *lite* (titolo, hook, messaggio centrale, direzione narrativa,
obiettivo/voce/durata consigliati, massimo 3 asset) entro un budget globale di
25 secondi, con:

* contesto compatto (niente profilo brand completo né inventario fotografico);
* nessuna scansione provider/modelli per richiesta (snapshot AI Engine Manager);
* primo tentativo ≤ 15 s, un solo fallback AI autorizzato, fallback deterministico
  alla scadenza;
* annullamento reale (processo CLI ucciso) e cache delle proposte.

Nessun artefatto pesante (voiceover, storyboard, prompt generativi, Vision,
motion graphics) viene prodotto in questa fase: arriva solo dopo la scelta del tema.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from kora_studio.ai_engine.base import EngineCancelled, EngineError
from kora_studio.ai_engine.models import BILLING_LOCAL, EXEC_LOCAL
from kora_studio.db.models import (
    CreativeRequestPayload,
    ThemeProposalLite,
    ThemeProposalsLiteEnvelope,
)

IDEATION_AGENT = "creative_director"
DEFAULT_BUDGET_SEC = 25.0
#: 3.6 — timeout di ideazione dichiarato nelle specifiche, modificabile in Impostazioni
DEFAULT_IDEATION_TIMEOUT_SEC = 45.0
#: 3.6.1 — nessun limite nascosto: l'unico tentativo autorizzato usa quasi tutto
#: il budget di ideazione (restano ~0,9 s per parsing e validazione).
OVERHEAD_SEC = 0.9
MAX_BUDGET_SEC = 60.0
PROPOSALS = 4
RUN_TTL_SEC = 900.0

STAGE_LABELS = {
    "context": "Preparazione contesto",
    "connect": "Connessione motore AI",
    "generate": "Generazione temi",
    "validate": "Validazione proposte",
    "done": "Completato",
    "fallback": "Fallback deterministico",
    "cancelled": "Annullato",
}

PHILOSOPHY = (
    "KORA STUDIO — The Art of Being (KORA Beach Club, Policoro, Basilicata). "
    "Identità luxury mediterranea: calma, luce naturale, ospitalità autentica, dettagli reali. "
    "Vietati: slogan aggressivi, sconti, prezzi, promesse generiche, inglesismi di facciata."
)

VOICES = ("brando", "tiziana", "brando_tiziana", "none")


class IdeationCancelled(Exception):
    """Raised when the user cancels a running ideation request."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Run:
    def __init__(self, run_id: str, budget_ms: float, production_id: str) -> None:
        self.id = run_id
        self.production_id = production_id
        self.status = "running"
        self.events: list[dict[str, Any]] = []
        self.envelope: Optional[dict[str, Any]] = None
        self.error: Optional[str] = None
        self.error_kind: str = ""
        self.decision: Optional[dict[str, Any]] = None
        self.req: Any = None
        self.ctx: Optional[dict[str, Any]] = None
        self.provider: str = ""
        self.model: str = ""
        self.cancel = threading.Event()
        self.started = time.perf_counter()
        self.started_ms = int(time.time() * 1000)
        self.started_wall = _now_iso()
        self.budget_ms = budget_ms
        self.finished_at: Optional[float] = None
        self.lock = threading.Lock()

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 1)

    def emit(self, stage: str, status: str, detail: str = "", **extra: Any) -> None:
        event = {
            "stage": stage,
            "label": STAGE_LABELS.get(stage, stage),
            "status": status,
            "at_ms": self.elapsed_ms(),
            "detail": detail,
        }
        event.update({k: v for k, v in extra.items() if v is not None})
        with self.lock:
            self.events.append(event)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "run_id": self.id,
                "status": self.status,
                "events": list(self.events),
                "elapsed_ms": self.elapsed_ms(),
                "budget_ms": self.budget_ms,
                "envelope": self.envelope,
                "error": self.error,
                "error_kind": self.error_kind,
                "decision": self.decision,
                "provider": self.provider,
                "model": self.model,
                "cancelled": self.cancel.is_set(),
                "finished": self.status != "running",
            }


class ThemeProposalCache:
    """Cache su SQLite: chiave = profilo brand + stagione + storia + config + motore + data."""

    def __init__(self, db: Any) -> None:
        self.db = db

    @staticmethod
    def build_key(
        *,
        brand_version: int,
        season: str,
        history_signature: str,
        request_config: dict[str, Any],
        provider: str,
        model: str,
        day: str,
    ) -> str:
        payload = json.dumps(
            {
                "brand_version": brand_version,
                "season": season,
                "history": history_signature,
                "config": request_config,
                "provider": provider,
                "model": model,
                "day": day,
                "schema": "theme-lite-v1",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[dict[str, Any]]:
        if self.db is None:
            return None
        try:
            with self.db.session() as conn:
                row = conn.execute(
                    "SELECT payload_json, created_at, expires_at, hits FROM theme_proposal_cache WHERE cache_key = ?",
                    (key,),
                ).fetchone()
                if row is None:
                    return None
                now = datetime.now(timezone.utc)
                try:
                    expires = datetime.fromisoformat(str(row["expires_at"]))
                except (TypeError, ValueError):
                    return None
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires <= now:
                    conn.execute("DELETE FROM theme_proposal_cache WHERE cache_key = ?", (key,))
                    return None
                conn.execute(
                    "UPDATE theme_proposal_cache SET hits = hits + 1, last_hit_at = ? WHERE cache_key = ?",
                    (now.isoformat(), key),
                )
                created = datetime.fromisoformat(str(row["created_at"]))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                return {
                    "payload": json.loads(row["payload_json"]),
                    "age_sec": round((now - created).total_seconds(), 1),
                }
        except Exception:
            # la cache non deve mai bloccare l'ideazione
            return None

    def put(self, key: str, payload: dict[str, Any], meta: dict[str, Any], ttl_sec: float) -> None:
        if self.db is None:
            return
        try:
            now = datetime.now(timezone.utc)
            expires = datetime.fromtimestamp(now.timestamp() + max(60.0, ttl_sec), tz=timezone.utc)
            with self.db.session() as conn:
                conn.execute(
                    """
                    INSERT INTO theme_proposal_cache (
                        cache_key, payload_json, brand_version, season, provider, model,
                        created_at, expires_at, hits, last_hit_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at
                    """,
                    (
                        key,
                        json.dumps(payload, ensure_ascii=False),
                        int(meta.get("brand_version") or 0),
                        str(meta.get("season") or ""),
                        str(meta.get("provider") or ""),
                        str(meta.get("model") or ""),
                        now.isoformat(),
                        expires.isoformat(),
                    ),
                )
        except Exception:
            pass

    def clear(self) -> int:
        if self.db is None:
            return 0
        try:
            with self.db.session() as conn:
                row = conn.execute("SELECT COUNT(*) AS c FROM theme_proposal_cache").fetchone()
                conn.execute("DELETE FROM theme_proposal_cache")
                return int(row["c"] if row else 0)
        except Exception:
            return 0


class ThemeIdeationService:
    """Esegue l'ideazione lite in background con eventi di stage reali e cancellazione."""

    def __init__(self, director: Any) -> None:
        self.director = director
        self.db = getattr(director, "db", None)
        self.cache = ThemeProposalCache(self.db)
        self._runs: dict[str, _Run] = {}
        self._runs_lock = threading.Lock()
        self._last_entry: Optional[dict[str, Any]] = None
        self._resolve_thread: Optional[threading.Thread] = None
        self.last_deterministic_reason: str = ""

    # ------------------------------------------------------------------ public
    def start(
        self,
        req: CreativeRequestPayload,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        avoid_cache: bool = False,
        policy_override: Optional[str] = None,
    ) -> str:
        run_id = uuid.uuid4().hex[:12]
        budget = self._budget_sec()
        run = _Run(run_id, round(budget * 1000, 1), f"ideation-{run_id}")
        run.req = req
        run.provider = str(provider or "")
        run.model = str(model or "")
        with self._runs_lock:
            self._prune_locked()
            self._runs[run_id] = run
        thread = threading.Thread(
            target=self._execute,
            # la politica di fallback fa parte della richiesta: «Chiedi conferma»
            # non deve mai finire in proposte locali senza il consenso dell'utente
            args=(run, req, provider, model, avoid_cache, policy_override),
            name=f"kora-ideation-{run_id}",
            daemon=True,
        )
        thread.start()
        return run_id

    def get(self, run_id: str) -> Optional[dict[str, Any]]:
        run = self._runs.get(run_id)
        return run.snapshot() if run else None

    def cancel(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if run is None:
            return {"cancelled": False, "detail": "richiesta inesistente o scaduta"}
        run.cancel.set()
        engine = getattr(self.director, "ai_engine", None)
        if engine is not None and hasattr(engine, "cancel"):
            try:
                engine.cancel(run.production_id)
            except Exception:
                pass
        run.emit("cancelled", "requested", "annullamento richiesto dall'utente")
        return {"cancelled": True, "detail": "annullamento richiesto", "run_id": run_id}

    def prewarm(self, req: Optional[CreativeRequestPayload] = None) -> dict[str, Any]:
        """Pre-generazione in background: solo se autorizzata E a costo zero (0 € verificato)."""
        engine = getattr(self.director, "ai_engine", None)
        if engine is None:
            return {"started": False, "reason": "AI Engine Manager non collegato"}
        try:
            fast = engine.settings().get("fast_creative") or {}
        except Exception:
            fast = {}
        if not fast.get("enabled", True) or not fast.get("background_warm"):
            return {"started": False, "reason": "pre-generazione in background disattivata"}
        try:
            entry = engine.creative_entry(IDEATION_AGENT, purpose="ideation")
        except Exception as exc:
            return {"started": False, "reason": f"motori non disponibili ({exc.__class__.__name__})"}
        if not entry:
            return {"started": False, "reason": "nessun motore autorizzato"}
        if not entry.get("zero_cost"):
            return {
                "started": False,
                "reason": "il motore selezionato non è a costo zero: nessuna spesa non approvata",
            }
        run_id = self.start(req or CreativeRequestPayload(), avoid_cache=False)
        return {
            "started": True,
            "run_id": run_id,
            "engine": f"{entry.get('provider')}/{entry.get('model')}",
            "cost": 0.0,
        }

    def suggest_blocking(
        self, req: Optional[CreativeRequestPayload] = None
    ) -> ThemeProposalsLiteEnvelope:
        """Versione sincrona (test, CLI e chiamate interne)."""
        req = req or CreativeRequestPayload()
        run_id = self.start(req, avoid_cache=True)
        run = self._runs[run_id]
        deadline = time.time() + self._budget_sec() + 5.0
        while run.status == "running" and time.time() < deadline:
            time.sleep(0.02)
        snap = run.snapshot()
        if snap["envelope"]:
            return ThemeProposalsLiteEnvelope(**snap["envelope"])
        return self._deterministic_envelope(req, snap.get("error") or "timeout locale")

    # ----------------------------------------------------------------- helpers
    def _model_manager(self) -> Any:
        """Il servizio riceve il CreativeDirector: il Model Manager sta sul direttore."""
        manager = getattr(self.director, "model_manager", None)
        if manager is None:
            engine = getattr(self.director, "ai_engine", None)
            manager = getattr(engine, "model_manager", None)
        return manager

    def _budget_sec(self) -> float:
        # 3.6.1 — il timeout di ideazione vive nel Model Manager (45 s di default):
        # una sola fonte, la stessa mostrata in Impostazioni › Modelli.
        try:
            manager = self._model_manager()
            if manager is not None:
                value = float(manager.timeout_for("ideation"))
            else:
                value = DEFAULT_IDEATION_TIMEOUT_SEC
        except (TypeError, ValueError, AttributeError):
            value = DEFAULT_IDEATION_TIMEOUT_SEC
        return max(8.0, min(MAX_BUDGET_SEC, value))

    def _first_attempt_sec(self) -> float:
        """L'unico tentativo autorizzato usa il budget intero meno l'overhead reale."""
        budget = self._budget_sec()
        return max(4.0, budget - OVERHEAD_SEC)

    def _policy(self) -> str:
        manager = self._model_manager()
        if manager is None:
            return "ask"
        try:
            return str(manager.config().get("policy") or "ask")
        except Exception:
            return "ask"

    def _choice(self) -> dict[str, Any]:
        manager = self._model_manager()
        if manager is None:
            return {}
        try:
            return manager.effective_choice()
        except Exception:
            return {}

    def _cache_ttl(self) -> float:
        engine = getattr(self.director, "ai_engine", None)
        if engine is not None and hasattr(engine, "settings"):
            try:
                return float((engine.settings().get("fast_creative") or {}).get("cache_ttl_sec") or 21600)
            except (TypeError, ValueError):
                return 21600.0
        return 21600.0

    def _season(self) -> str:
        try:
            return self.director._season_context()
        except Exception:
            return ""

    def _visual_assets(self) -> list[dict[str, Any]]:
        try:
            assets = self.director.vault.list_assets()
        except Exception:
            return []
        return [
            a
            for a in assets
            if a.get("media_type") in ("image", "video") and a.get("category") != "Logo"
        ]

    @staticmethod
    def _request_config(req: CreativeRequestPayload) -> dict[str, Any]:
        return {
            "theme": req.theme.strip(),
            "objective": req.objective.strip(),
            "duration_sec": req.duration_sec,
            "voice_id": req.voice_id,
            "asset_ids": sorted(req.asset_ids),
            "media_selection": req.media_selection,
            "generation_policy": req.generation_policy,
            "custom_instructions": req.custom_instructions.strip(),
            "brand_profile_version": req.brand_profile_version,
            "previous_production_ids": sorted(req.previous_production_ids),
        }

    def _history(self, limit: int = 12) -> list[dict[str, Any]]:
        try:
            return self.director.anti_rep.get_history(limit=limit)
        except Exception:
            return []

    def _compact_context(
        self, req: CreativeRequestPayload, valid_ids: list[str]
    ) -> dict[str, Any]:
        profile = {}
        try:
            profile = self.director.brand_brain.get_current() or {}
        except Exception:
            profile = {}
        history = self._history(12)
        assets = self._visual_assets()
        categories: dict[str, int] = {}
        examples: dict[str, list[str]] = {}
        for asset in assets:
            cat = str(asset.get("category") or "Altro")
            categories[cat] = categories.get(cat, 0) + 1
            bucket = examples.setdefault(cat, [])
            if len(bucket) < 2:
                bucket.append(str(asset.get("id")))
        return {
            "profilo": {
                "versione": profile.get("version"),
                "nome": profile.get("name") or "KORA Beach Club",
                "filosofia": PHILOSOPHY,
            },
            "stagionalita_verificata": self._season(),
            "temi_gia_usati": [str(h.get("title") or "") for h in history if h.get("title")],
            "materiale_disponibile": {
                "categorie": categories,
                "esempi_id_per_categoria": examples,
                "id_utilizzabili": valid_ids[:24],
            },
            "richiesta_utente": {
                "tema": req.theme.strip(),
                "obiettivo": req.objective.strip(),
                "durata_sec": req.duration_sec,
                "voce": req.voice_id,
                "istruzioni": req.custom_instructions.strip(),
            },
            "numero_proposte": PROPOSALS,
        }

    def _authorised_ids(self, req: CreativeRequestPayload) -> list[str]:
        assets = self._visual_assets()
        if req.asset_ids:
            allowed = {str(a.get("id")) for a in assets}
            return [str(i) for i in req.asset_ids if str(i) in allowed]
        return [str(a.get("id")) for a in assets]

    @staticmethod
    def _messages(ctx: dict[str, Any]) -> list[dict[str, Any]]:
        system = (
            "Sei il Direttore Creativo di KORA STUDIO (" + PHILOSOPHY + ") "
            "Lavori in Fast Creative Mode: nessun tool, nessun file, nessuna analisi di immagini. "
            "Rispondi SOLO con JSON valido, senza testo fuori dal JSON, in italiano. "
            'Schema: {"proposals": [{"title": "max 70 caratteri", "hook": "max 120", '
            '"central_message": "max 160", "narrative_direction": "max 220", '
            '"recommended_objective": "max 90", "recommended_voice_id": "brando|tiziana|brando_tiziana|none", '
            '"recommended_duration_sec": 20, "asset_ids": ["max 3 ID reali"]}]}. '
            f"Genera esattamente {PROPOSALS} proposte distinte, originali e non presenti in temi_gia_usati. "
            "Niente voiceover, storyboard, prompt generativi o specifiche di montaggio."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)},
        ]

    # ------------------------------------------------------------------- cache
    def _cache_lookup(
        self, req: CreativeRequestPayload, provider: str, model: str, ctx: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        key = ThemeProposalCache.build_key(
            brand_version=int((ctx.get("profilo") or {}).get("versione") or 0),
            season=str(ctx.get("stagionalita_verificata") or ""),
            history_signature="|".join(ctx.get("temi_gia_usati") or []),
            request_config=self._request_config(req),
            provider=provider,
            model=model,
            day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        hit = self.cache.get(key)
        if not hit:
            return None
        try:
            envelope = ThemeProposalsLiteEnvelope(**hit["payload"])
        except Exception:
            return None
        envelope.from_cache = True
        envelope.cache_age_sec = hit["age_sec"]
        for proposal in envelope.proposals:
            proposal.from_cache = True
            proposal.cache_age_sec = hit["age_sec"]
            proposal.source = "cache"
        return envelope.model_dump()

    def _cache_store(
        self,
        req: CreativeRequestPayload,
        envelope: ThemeProposalsLiteEnvelope,
        ctx: dict[str, Any],
        provider: str,
        model: str,
    ) -> None:
        key = ThemeProposalCache.build_key(
            brand_version=int((ctx.get("profilo") or {}).get("versione") or 0),
            season=str(ctx.get("stagionalita_verificata") or ""),
            history_signature="|".join(ctx.get("temi_gia_usati") or []),
            request_config=self._request_config(req),
            provider=provider,
            model=model,
            day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        payload = envelope.model_dump()
        payload["from_cache"] = False
        payload["cache_age_sec"] = None
        self.cache.put(
            key,
            payload,
            {"brand_version": (ctx.get("profilo") or {}).get("versione"), "season": ctx.get("stagionalita_verificata"), "provider": provider, "model": model},
            self._cache_ttl(),
        )

    # --------------------------------------------------------------- execution
    def _resolve_entry(self, run: _Run, max_wait_sec: float) -> Optional[dict[str, Any]]:
        engine = getattr(self.director, "ai_engine", None)
        if engine is None:
            return None
        choice = self._choice()
        wanted = str(choice.get("model") or "")
        if self._last_entry and (
            not wanted or str(self._last_entry.get("model") or "") == wanted
        ):
            provider = str(self._last_entry.get("provider") or "")
            snapshot = engine.snapshot() if hasattr(engine, "snapshot") else {}
            info = ((snapshot.get("providers") or {}).get(provider) or {}) if snapshot else {}
            if not info or info.get("authenticated") or not info.get("installed"):
                return self._last_entry
        holder: dict[str, Any] = {}

        def _work() -> None:
            try:
                entry = None
                if hasattr(engine, "creative_entry"):
                    entry = engine.creative_entry(IDEATION_AGENT, purpose="ideation")
                holder["entry"] = entry or engine.fast_entry(IDEATION_AGENT)
            except Exception as exc:  # pragma: no cover
                holder["error"] = f"{exc.__class__.__name__}: {exc}"

        if self._resolve_thread is None or not self._resolve_thread.is_alive():
            self._resolve_thread = threading.Thread(
                target=_work, name="kora-ideation-resolve", daemon=True
            )
            self._resolve_thread.start()
        self._resolve_thread.join(max(0.4, min(max_wait_sec, 4.0)))
        entry = holder.get("entry")
        if entry:
            self._last_entry = entry
        return entry

    def _engine_attempt(
        self,
        run: _Run,
        req: CreativeRequestPayload,
        ctx: dict[str, Any],
        entry: dict[str, Any],
        deadline: float,
        valid_ids: set[str],
        attempt_label: str,
    ) -> tuple[Optional[list[ThemeProposalLite]], dict[str, Any], str]:
        engine = getattr(self.director, "ai_engine", None)
        if engine is None:
            return None, {}, ""
        provider = str(entry.get("provider") or "")
        model = str(entry.get("model") or "")
        # «opencode/opencode/hy4-flash-free» → «opencode/hy4-flash-free» (etichetta leggibile)
        display_model = model.split("/", 1)[1] if provider and model.startswith(provider + "/") else model
        label = f"{provider}/{display_model}"
        remaining = deadline - time.time()
        if remaining <= 1.0:
            return None, {}, ""
        run.emit(
            "generate", "start",
            f"tentativo {attempt_label}: {label}",
            engine=label, provider=provider, model=display_model,
        )
        started = time.perf_counter()
        timings: dict[str, Any] = {}
        try:
            result = engine.call(
                IDEATION_AGENT,
                self._messages(ctx),
                json_mode=True,
                timeout=max(2.0, remaining),
                deadline=deadline,
                attempt_timeout=max(2.0, remaining),
                max_candidates=1,
                brand_context=False,
                provider_override=provider,
                model_override=model,
                production_id=run.production_id,
                request_label=f"suggest-lite:{attempt_label}",
            )
        except EngineCancelled:
            raise IdeationCancelled() from None
        except EngineError as exc:
            timings["inference"] = round((time.perf_counter() - started) * 1000, 1)
            timings["error"] = f"{exc.__class__.__name__}: {exc}"[:400]
            run.emit("generate", "error", timings["error"], engine=label)
            return None, timings, label
        except Exception as exc:  # pragma: no cover — difensivo
            timings["inference"] = round((time.perf_counter() - started) * 1000, 1)
            timings["error"] = f"{exc.__class__.__name__}: {exc}"[:400]
            run.emit("generate", "error", timings["error"], engine=label)
            return None, timings, label

        timings.update(result.timings or {})
        timings["inference"] = round((time.perf_counter() - started) * 1000, 1)
        timings["model_latency"] = result.latency_ms
        run.emit(
            "generate", "done",
            f"risposta {label} in {int(timings['inference'])} ms",
            engine=label,
        )
        run.emit("validate", "start", "controllo schema, duplicati e ID asset")
        parse_started = time.perf_counter()
        parse_error = ""
        proposals: Optional[list[ThemeProposalLite]] = None
        try:
            data = self._extract_json(result.text or "")
            timings["json_parsing"] = round((time.perf_counter() - parse_started) * 1000, 1)
            validate_started = time.perf_counter()
            proposals = self._validate_items(data, valid_ids)
            timings["validation"] = round((time.perf_counter() - validate_started) * 1000, 1)
        except Exception as exc:
            # messaggio leggibile per l'utente; il dettaglio tecnico resta nei timings
            parse_error = (
                "Il motore ha risposto in un formato non leggibile "
                f"({exc.__class__.__name__}); usata la direzione creativa locale."
            )
            timings["json_error_raw"] = f"{exc.__class__.__name__}: {exc}"[:300]
            timings.setdefault("json_parsing", round((time.perf_counter() - parse_started) * 1000, 1))
        parse_ms = timings.get("json_parsing", 0.0)
        if proposals is None or len(proposals) != PROPOSALS:
            detail = parse_error or f"JSON non valido o proposte incomplete ({len(proposals or [])}/{PROPOSALS})"
            run.emit("validate", "error", detail)
            timings["validation_error"] = detail
            annotate = getattr(engine.activity, "annotate_timings", None)
            if annotate and getattr(result, "call_id", ""):
                annotate(result.call_id, {
                    "json_parsing": parse_ms,
                    "validation": detail[:200],
                    "total": round((time.perf_counter() - started) * 1000, 1),
                })
            return None, timings, label
        run.emit("validate", "done", f"{len(proposals)} proposte valide")
        annotate = getattr(engine.activity, "annotate_timings", None)
        if annotate and getattr(result, "call_id", ""):
            annotate(result.call_id, {
                "json_parsing": parse_ms,
                "inference_total": timings["inference"],
                "total": round((time.perf_counter() - started) * 1000, 1),
            })
        return proposals, timings, label

    def _execute(
        self,
        run: _Run,
        req: CreativeRequestPayload,
        provider_override: Optional[str],
        model_override: Optional[str],
        avoid_cache: bool,
        policy_override: Optional[str] = None,
    ) -> None:
        timings: dict[str, Any] = {}
        started = time.perf_counter()
        wall_started = time.time()
        budget = self._budget_sec()
        deadline_global = wall_started + budget
        first_attempt = self._first_attempt_sec()
        policy = policy_override or self._policy()
        try:
            run.emit("context", "start", "contesto compatto: profilo, stagione, temi usati, categorie")
            ctx_started = time.perf_counter()
            valid_ids = self._authorised_ids(req)
            ctx = self._compact_context(req, valid_ids)
            run.ctx = ctx
            timings["context_building"] = round((time.perf_counter() - ctx_started) * 1000, 1)
            run.emit(
                "context", "done",
                f"{len(ctx['materiale_disponibile']['categorie'])} categorie, "
                f"{len(ctx['temi_gia_usati'])} temi usati, "
                f"{len(ctx['materiale_disponibile']['id_utilizzabili'])} ID",
            )
            if run.cancel.is_set():
                raise IdeationCancelled()

            engine = getattr(self.director, "ai_engine", None)
            target_provider = provider_override or ""
            target_model = model_override or ""
            entry: Optional[dict[str, Any]] = None

            run.emit("connect", "start", "modello attivo dal Model Manager (nessuna scelta automatica)")
            if engine is not None:
                # snapshot in background: la prima richiesta non paga la discovery completa
                if hasattr(engine, "snapshot_age_sec") and engine.snapshot_age_sec() is None:
                    try:
                        engine.start_snapshot_refresher()
                    except Exception:
                        pass
                if target_provider and target_model:
                    entry = {"provider": target_provider, "model": target_model}
                else:
                    resolve_started = time.perf_counter()
                    entry = self._resolve_entry(
                        run, max_wait_sec=min(3.0, max(0.5, deadline_global - time.time() - 2.0))
                    )
                    timings["provider_discovery"] = round(
                        (time.perf_counter() - resolve_started) * 1000, 1
                    )
                timings["provider_connection"] = 0.0
            if entry is None:
                choice = self._choice()
                manager = self._model_manager()
                detail = ""
                if manager is not None and engine is not None:
                    try:
                        status = manager.status(engine)
                        detail = str(status.get("detail") or "")
                    except Exception:
                        detail = ""
                reason = "Nessun motore AI disponibile"
                if choice.get("model"):
                    reason += f" per {choice.get('model')}"
                if detail:
                    reason += f": {detail}"
                if engine is None:
                    reason = "AI Engine Manager non collegato"
                run.emit("connect", "error", reason[:300])
                self._finish_failure(
                    run, req, ctx, reason, timings, started, policy, tried=[], entry=None
                )
                return
            engine_label = f"{entry.get('provider')}/{entry.get('model')}"
            run.provider = str(entry.get("provider") or "")
            run.model = str(entry.get("model") or "")
            source = str(entry.get("source") or "")
            run.emit(
                "connect", "done",
                f"motore selezionato: {engine_label}"
                + (f" (Model Manager · {entry.get('role')})" if source == "model_manager" else ""),
                engine=engine_label,
            )

            if not avoid_cache:
                cached = self._cache_lookup(
                    req, str(entry.get("provider") or ""), str(entry.get("model") or ""), ctx
                )
                if cached:
                    timings["cache"] = round((time.perf_counter() - started) * 1000, 1)
                    self._finish_envelope(run, cached, timings, started, cached=True)
                    return
            else:
                timings["cache"] = 0.0

            # 3.6.1 — un solo tentativo con il budget di ideazione intero (45 s):
            # restano OVERHEAD_SEC per parsing e validazione. Solo se la politica
            # autorizza già il modello alternativo si riserva il tempo per tentarlo.
            reserve = 0.0
            if policy == "fallback":
                reserve = min(budget * 0.45, max(4.0, budget - 6.0))
            attempt_deadline = min(
                deadline_global - 0.4, wall_started + first_attempt - reserve
            )
            proposals, attempt_timings, engine_label = self._engine_attempt(
                run, req, ctx, entry, attempt_deadline, set(valid_ids), "1"
            )
            timings.update({k: v for k, v in attempt_timings.items() if v is not None})

            if proposals is None and not run.cancel.is_set():
                remaining = deadline_global - time.time() - 1.0
                fallback_entry = None
                if policy == "fallback" and remaining > 4.0:
                    fallback_entry = self._fallback_entry(entry)
                if fallback_entry is not None:
                    run.emit(
                        "generate", "fallback",
                        f"fallback autorizzato su {fallback_entry.get('provider')}/{fallback_entry.get('model')}",
                        engine=f"{fallback_entry.get('provider')}/{fallback_entry.get('model')}",
                    )
                    timings["fallback"] = 1
                    second, second_timings, engine_label = self._engine_attempt(
                        run, req, ctx, fallback_entry,
                        deadline_global - 0.6, set(valid_ids), "fallback",
                    )
                    timings.update({k: v for k, v in second_timings.items() if v is not None})
                    proposals = second
                else:
                    timings["fallback"] = 0

            if run.cancel.is_set():
                raise IdeationCancelled()

            if proposals is None:
                reason = self._failure_reason(timings, budget, deadline_global)
                self._finish_failure(
                    run, req, ctx, reason, timings, started, policy,
                    tried=[engine_label], entry=entry,
                )
                return

            provider_name, _, model_name = engine_label.partition("/")
            envelope = ThemeProposalsLiteEnvelope(
                proposals=proposals,
                generation_engine=f"ai_engine:{engine_label}",
                model=model_name,
                from_cache=False,
                cache_age_sec=None,
                fallback_reason=None,
                timings_ms={},
            )
            payload = envelope.model_dump()
            payload["timings_ms"] = {**timings, "total": round((time.perf_counter() - started) * 1000, 1)}
            try:
                self._cache_store(req, envelope, ctx, provider_name, model_name)
            except Exception:
                pass
            self._finish_envelope(run, payload, timings, started, cached=False)
        except (IdeationCancelled, EngineCancelled):
            run.emit("cancelled", "done", "richiesta annullata: nessun risultato parziale")
            with run.lock:
                run.status = "cancelled"
                run.error = "Richiesta annullata dall'utente"
                run.error_kind = "annullata"
                run.decision = None
                run.finished_at = time.perf_counter()
        except Exception as exc:  # pragma: no cover — difensivo
            run.emit("fallback", "start", f"errore inatteso: {exc.__class__.__name__}")
            try:
                ctx = run.ctx or self._compact_context(req, self._authorised_ids(req))
                self._finish_failure(
                    run, req, ctx,
                    f"errore inatteso ({exc.__class__.__name__}): {exc}",
                    timings, started, policy, tried=[], entry=None,
                )
            except Exception:
                with run.lock:
                    run.status = "error"
                    run.error = f"Errore inatteso: {exc.__class__.__name__}: {exc}"
                    run.error_kind = "errore_interno"
                    run.finished_at = time.perf_counter()

    def _failure_reason(
        self, timings: dict[str, Any], budget: float, deadline_global: float
    ) -> str:
        if time.time() >= deadline_global - 0.5:
            return f"timeout di ideazione: {int(budget)} s esauriti senza una risposta valida"
        raw = str(timings.get("error") or timings.get("validation_error") or "")
        text = re.sub(r"^[A-Za-z]+Error: ", "", raw)
        # «opencode/hy4-flash-free: <causa>» → solo la causa, leggibile all'utente
        text = re.sub(r"^[\w.\-]+/[\w.\-]+: ", "", text).strip()
        if text and ("nessuna risposta" in text.lower() or "timeout" in text.lower()):
            return f"timeout di ideazione a {int(budget)} s: {text}"
        return text or "nessuna risposta valida dal motore AI"

    def _fallback_entry(self, entry: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Nemotron (ruolo fallback) dal Model Manager; piano automatico solo se assente."""
        choice = self._choice()
        model = str(choice.get("fallback_model") or "")
        provider = str(entry.get("provider") or "opencode")
        if model and model != entry.get("model") and model.split("/", 1)[0] == provider:
            return {
                "provider": provider,
                "model": model,
                "source": "model_manager_fallback",
                "role": "fallback",
            }
        engine = getattr(self.director, "ai_engine", None)
        if engine is None:
            return None
        try:
            plan = engine.candidate_plan(IDEATION_AGENT, from_snapshot=True)
        except Exception:
            return None
        for candidate in plan:
            if candidate.get("model") != entry.get("model"):
                return candidate
        return None

    def _finish_failure(
        self,
        run: _Run,
        req: CreativeRequestPayload,
        ctx: dict[str, Any],
        reason: str,
        timings: dict[str, Any],
        started: float,
        policy: str,
        *,
        tried: list[str],
        entry: Optional[dict[str, Any]],
    ) -> None:
        """Esito non riuscito: decide la politica salvata, non il codice."""
        if run.cancel.is_set():
            raise IdeationCancelled()
        run.emit("generate", "error", reason[:300])
        if policy == "local":
            self._finish_deterministic(run, req, ctx, reason, timings, started)
            return
        if policy == "stop":
            with run.lock:
                run.status = "error"
                run.error = reason
                run.error_kind = "interrotto"
                run.finished_at = time.perf_counter()
            run.emit("done", "error", "interrotto come da politica «Interrompi»")
            return
        if policy == "fallback":
            # il modello alternativo è stato già tentato: ultima risorsa dichiarata
            self._finish_deterministic(
                run, req, ctx,
                f"{reason} · anche il modello alternativo non ha risposto",
                timings, started,
            )
            return
        self._finish_decision(run, req, ctx, reason, timings, started, tried=tried, entry=entry)

    def _finish_decision(
        self,
        run: _Run,
        req: CreativeRequestPayload,
        ctx: dict[str, Any],
        reason: str,
        timings: dict[str, Any],
        started: float,
        *,
        tried: list[str],
        entry: Optional[dict[str, Any]],
    ) -> None:
        """Politica «Chiedi conferma»: nessuna proposta locale senza consenso."""
        choice = self._choice()
        fallback_model = str(choice.get("fallback_model") or "opencode/nemotron-3.5-lightning-free")
        current = f"{run.provider}/{run.model}" if run.provider and run.model else (tried[0] if tried else "")
        options = [
            {
                "action": "retry",
                "label": "Riprova",
                "detail": f"stesso modello{(' · ' + current) if current else ''}",
            },
            {
                "action": "fallback",
                "label": "Usa Nemotron",
                "detail": f"modello alternativo autorizzato · {fallback_model}",
            },
            {
                "action": "local",
                "label": "Usa proposte locali",
                "detail": "4 proposte deterministiche KORA · 0 € · non generate dall'AI",
            },
            {"action": "cancel", "label": "Annulla", "detail": "nessun risultato"},
        ]
        run.emit("fallback", "await", reason[:280])
        self._log_decision(run, timings, reason, current)
        with run.lock:
            run.decision = {
                "reason": reason,
                "policy": "ask",
                "tried": tried,
                "provider": run.provider,
                "model": run.model,
                "options": options,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }
            run.status = "needs_decision"
            run.error = reason
            run.error_kind = "serve_decisione"
            run.finished_at = time.perf_counter()

    def _log_decision(
        self, run: _Run, timings: dict[str, Any], reason: str, model_label: str
    ) -> None:
        engine = getattr(self.director, "ai_engine", None)
        log = getattr(engine, "_log", None) if engine is not None else None
        if log is None:
            return
        provider_name, _, model_name = (model_label or "/").partition("/")
        try:
            log(
                run.production_id,
                IDEATION_AGENT,
                {"provider": provider_name, "model": model_name, "billing": "unknown", "execution": "unknown"},
                run.started_wall,
                run.started_ms,
                outcome="error",
                attempts=1,
                fallback_used=False,
                fallback_from=None,
                cost=None,
                error_kind="serve_decisione",
                error_message=reason[:300],
                timings={**timings, "total": round(time.perf_counter() * 1000 - run.started_ms, 1)},
            )
        except Exception:
            pass

    # ------------------------------------------------------- scelta dell'utente
    def decide(self, run_id: str, action: str) -> dict[str, Any]:
        """Applica la scelta esplicita dopo un fallimento («Riprova» / «Usa Nemotron» / proposte locali / «Annulla»)."""
        run = self._runs.get(run_id)
        if run is None:
            return {"ok": False, "detail": "richiesta inesistente o scaduta"}
        if run.status != "needs_decision":
            return {"ok": False, "detail": f"nessuna decisione pendente (stato: {run.status})"}
        action = str(action or "").strip().lower()
        if action == "cancel":
            run.cancel.set()
            with run.lock:
                run.status = "cancelled"
                run.error = "Richiesta annullata dall'utente"
                run.error_kind = "annullata"
                run.decision = None
                run.finished_at = time.perf_counter()
            run.emit("cancelled", "done", "annullata su richiesta dell'utente")
            return {"ok": True, "action": "cancel", "run_id": run_id}
        if action == "local":
            with run.lock:
                run.status = "running"
            thread = threading.Thread(
                target=self._apply_local, args=(run,), name=f"kora-ideation-local-{run_id}", daemon=True
            )
            thread.start()
            return {"ok": True, "action": "local", "run_id": run_id}
        if action in ("retry", "fallback"):
            choice = self._choice()
            provider = run.provider
            model = run.model
            if action == "fallback":
                fallback_model = str(choice.get("fallback_model") or "")
                if fallback_model:
                    provider = fallback_model.split("/", 1)[0]
                    model = fallback_model
            new_id = self.start(
                run.req or CreativeRequestPayload(),
                provider=provider or None,
                model=model or None,
                avoid_cache=True,
                policy_override="ask",
            )
            return {"ok": True, "action": action, "run_id": new_id, "previous_run_id": run_id}
        return {"ok": False, "detail": "azione non riconosciuta"}

    def _apply_local(self, run: _Run) -> None:
        reason = str((run.decision or {}).get("reason") or "scelta dell'utente")
        with run.lock:
            run.decision = None
        try:
            ctx = run.ctx or self._compact_context(
                run.req or CreativeRequestPayload(),
                self._authorised_ids(run.req or CreativeRequestPayload()),
            )
            self._finish_deterministic(
                run,
                run.req or CreativeRequestPayload(),
                ctx,
                f"proposte locali scelte dall'utente · motivo AI: {reason}",
                {},
                run.started,
            )
        except Exception as exc:  # pragma: no cover — difensivo
            with run.lock:
                run.status = "error"
                run.error = f"Errore nella generazione locale: {exc.__class__.__name__}: {exc}"
                run.error_kind = "errore_interno"
                run.finished_at = time.perf_counter()

    # ------------------------------------------------------------- completions
    def _finish_envelope(
        self, run: _Run, payload: dict[str, Any], timings: dict[str, Any], started: float, *, cached: bool
    ) -> None:
        envelope = dict(payload)
        envelope["timings_ms"] = {
            **timings,
            **(payload.get("timings_ms") or {}),
            "total": round((time.perf_counter() - started) * 1000, 1),
        }
        envelope["from_cache"] = bool(envelope.get("from_cache"))
        run.emit(
            "done", "done",
            f"{len(envelope.get('proposals') or [])} proposte"
            + (" (dalla cache)" if envelope["from_cache"] else ""),
            engine=envelope.get("generation_engine"),
            cache=envelope["from_cache"],
        )
        with run.lock:
            run.envelope = envelope
            run.status = "done"
            run.finished_at = time.perf_counter()

    def _finish_deterministic(
        self,
        run: _Run,
        req: CreativeRequestPayload,
        ctx: dict[str, Any],
        reason: str,
        timings: dict[str, Any],
        started: float,
    ) -> None:
        if run.cancel.is_set():
            raise IdeationCancelled()
        run.emit("fallback", "start", reason[:300])
        fallback_started = time.perf_counter()
        envelope = self._deterministic_envelope(req, reason)
        timings["fallback"] = round((time.perf_counter() - fallback_started) * 1000, 1)
        payload = envelope.model_dump()
        payload["timings_ms"] = {**timings, "total": round((time.perf_counter() - started) * 1000, 1)}
        run.emit(
            "fallback", "done",
            f"{len(envelope.proposals)} proposte deterministiche in {int(timings['fallback'])} ms",
        )
        self._log_deterministic(run, timings, reason)
        self._finish_envelope(run, payload, timings, started, cached=False)

    def _log_deterministic(
        self, run: _Run, timings: dict[str, Any], reason: str
    ) -> None:
        """Il fallback deterministico compare nel Monitor AI con le sue fasi reali (0 €)."""
        engine = getattr(self.director, "ai_engine", None)
        log = getattr(engine, "_log", None) if engine is not None else None
        if log is None:
            return
        try:
            log(
                None,
                IDEATION_AGENT,
                {"provider": "deterministic", "model": "kora-deterministic", "billing": BILLING_LOCAL, "execution": EXEC_LOCAL},
                run.started_wall,
                run.started_ms,
                outcome="ok",
                attempts=0,
                fallback_used=True,
                fallback_from="ai_engine" if timings.get("error") or timings.get("validation_error") else "",
                cost=0.0,
                cost_basis="local_compute",
                error_kind="",
                error_message="",
                timings={
                    **{k: v for k, v in timings.items() if isinstance(v, (int, float))},
                    "fallback": timings.get("fallback", 0),
                    "total": round((time.perf_counter() - run.started) * 1000, 1),
                },
                request_label=f"suggest-lite:deterministic:{reason[:80]}",
            )
        except Exception:
            # il monitor non deve mai bloccare l'ideazione
            pass

    def _deterministic_envelope(
        self, req: CreativeRequestPayload, reason: str
    ) -> ThemeProposalsLiteEnvelope:
        try:
            concepts = self.director._deterministic_concepts(req, reason)
        except Exception:
            concepts = []
        proposals: list[ThemeProposalLite] = []
        for idx, concept in enumerate(concepts[:PROPOSALS]):
            assets = [str(a) for a in (concept.available_asset_ids or [])][:3]
            proposals.append(
                ThemeProposalLite(
                    id=f"lite-{uuid.uuid4().hex[:8]}",
                    title=concept.title[:90],
                    hook=concept.hook[:160],
                    central_message=concept.message[:220],
                    narrative_direction=concept.narrative_arc[:280],
                    recommended_objective=concept.objective,
                    recommended_voice_id=concept.recommended_voice_id,
                    recommended_duration_sec=float(concept.duration_sec or req.duration_sec),
                    asset_ids=assets,
                    source="deterministic",
                )
            )
        if not proposals:  # pragma: no cover — difensivo
            proposals = [
                ThemeProposalLite(
                    id=f"lite-{uuid.uuid4().hex[:8]}",
                    title=f"KORA · proposta {i + 1}",
                    hook="Luce mediterranea e ospitalità autentica",
                    central_message="Il tempo che si ferma al KORA",
                    narrative_direction="Sequenza calma di dettagli reali, respiro, luce naturale.",
                    recommended_objective=req.objective or "Identità di marca",
                    recommended_voice_id=req.voice_id,
                    recommended_duration_sec=req.duration_sec,
                    asset_ids=[],
                    source="deterministic",
                )
                for i in range(PROPOSALS)
            ]
        self.last_deterministic_reason = reason
        return ThemeProposalsLiteEnvelope(
            proposals=proposals,
            generation_engine="deterministic_offline_fallback",
            model="kora-deterministic",
            from_cache=False,
            cache_age_sec=None,
            fallback_reason=reason[:500],
            timings_ms={},
        )

    # ---------------------------------------------------------------- parsing
    def _validate_items(self, data: Any, valid_ids: set[str]) -> Optional[list[ThemeProposalLite]]:
        items = data.get("proposals") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return None
        used = {str(t).strip().lower() for t in self._compact_history_titles()}
        proposals: list[ThemeProposalLite] = []
        seen_titles: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            if not title:
                continue
            title_key = title.lower()
            # mai ri-proporre un tema già usato, mai duplicati nella stessa risposta
            if title_key in seen_titles or title_key in used:
                continue
            seen_titles.add(title_key)
            voice = str(raw.get("recommended_voice_id") or "brando").strip().lower()
            if voice not in VOICES:
                voice = "brando"
            try:
                duration = float(raw.get("recommended_duration_sec") or 20.0)
            except (TypeError, ValueError):
                duration = 20.0
            duration = max(8.0, min(90.0, duration))
            asset_ids: list[str] = []
            raw_ids = raw.get("asset_ids")
            if isinstance(raw_ids, list):
                for candidate in raw_ids:
                    candidate = str(candidate)
                    if candidate in valid_ids and candidate not in asset_ids:
                        asset_ids.append(candidate)
                    if len(asset_ids) >= 3:
                        break
            proposals.append(
                ThemeProposalLite(
                    id=f"lite-{uuid.uuid4().hex[:8]}",
                    title=title[:90],
                    hook=str(raw.get("hook") or "").strip()[:160],
                    central_message=str(raw.get("central_message") or "").strip()[:220],
                    narrative_direction=str(raw.get("narrative_direction") or "").strip()[:280],
                    recommended_objective=str(raw.get("recommended_objective") or "").strip()[:160],
                    recommended_voice_id=voice,
                    recommended_duration_sec=duration,
                    asset_ids=asset_ids,
                    source="ai_engine",
                )
            )
            if len(proposals) >= PROPOSALS:
                break
        if len(proposals) != PROPOSALS:
            return None
        if any(not p.hook or not p.central_message or not p.narrative_direction for p in proposals):
            return None
        return proposals

    @staticmethod
    def _extract_json(raw_text: str) -> dict[str, Any]:
        from kora_studio.ai_engine.json_extract import parse_json_object

        return parse_json_object(raw_text)

    def _compact_history_titles(self) -> list[str]:
        return [str(h.get("title") or "") for h in self._history(12) if h.get("title")]

    # ------------------------------------------------------------------ prune
    def _prune_locked(self) -> None:
        now = time.perf_counter()
        stale = [
            run_id
            for run_id, run in self._runs.items()
            if run.finished_at is not None and (now - run.finished_at) > RUN_TTL_SEC
        ]
        for run_id in stale:
            self._runs.pop(run_id, None)
