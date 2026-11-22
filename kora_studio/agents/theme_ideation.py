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
DEFAULT_FIRST_ATTEMPT_SEC = 15.0
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
    ) -> str:
        run_id = uuid.uuid4().hex[:12]
        budget = self._budget_sec()
        run = _Run(run_id, round(budget * 1000, 1), f"ideation-{run_id}")
        with self._runs_lock:
            self._prune_locked()
            self._runs[run_id] = run
        thread = threading.Thread(
            target=self._execute,
            args=(run, req, provider, model, avoid_cache),
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
            entry = engine.fast_entry(IDEATION_AGENT)
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
    def _budget_sec(self) -> float:
        engine = getattr(self.director, "ai_engine", None)
        fast = {}
        if engine is not None and hasattr(engine, "settings"):
            try:
                fast = engine.settings().get("fast_creative") or {}
            except Exception:
                fast = {}
        try:
            # 3.6 — se l'utente ha salvato un timeout in Impostazioni › Modelli,
            # quello vince (45 s di default); altrimenti vale Fast Creative Mode.
            stored = self.director.db.get_preference("ai_model_config") or {}
            configured = (stored.get("timeouts") or {}).get("ideation")
            if configured:
                value = float(configured)
            elif fast.get("global_budget_sec"):
                value = float(fast["global_budget_sec"])
            else:
                # nessuna configurazione salvata: default 3.6 dichiarato (45 s)
                value = DEFAULT_IDEATION_TIMEOUT_SEC
        except (TypeError, ValueError, AttributeError):
            value = DEFAULT_BUDGET_SEC
        return max(8.0, min(MAX_BUDGET_SEC, value))

    def _first_attempt_sec(self) -> float:
        engine = getattr(self.director, "ai_engine", None)
        fast = {}
        if engine is not None and hasattr(engine, "settings"):
            try:
                fast = engine.settings().get("fast_creative") or {}
            except Exception:
                fast = {}
        try:
            value = float(fast.get("first_attempt_sec") or DEFAULT_FIRST_ATTEMPT_SEC)
        except (TypeError, ValueError):
            value = DEFAULT_FIRST_ATTEMPT_SEC
        return max(3.0, min(self._budget_sec(), value))

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
        if self._last_entry:
            provider = str(self._last_entry.get("provider") or "")
            snapshot = engine.snapshot() if hasattr(engine, "snapshot") else {}
            info = ((snapshot.get("providers") or {}).get(provider) or {}) if snapshot else {}
            if not info or info.get("authenticated") or not info.get("installed"):
                return self._last_entry
        holder: dict[str, Any] = {}

        def _work() -> None:
            try:
                holder["entry"] = engine.fast_entry(IDEATION_AGENT)
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
        remaining = deadline - time.time()
        if remaining <= 1.0:
            return None, {}, ""
        run.emit(
            "generate", "start",
            f"tentativo {attempt_label}: {provider}/{model}",
            engine=f"{provider}/{model}", provider=provider, model=model,
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
            run.emit("generate", "error", timings["error"], engine=f"{provider}/{model}")
            return None, timings, f"{provider}/{model}"
        except Exception as exc:  # pragma: no cover — difensivo
            timings["inference"] = round((time.perf_counter() - started) * 1000, 1)
            timings["error"] = f"{exc.__class__.__name__}: {exc}"[:400]
            run.emit("generate", "error", timings["error"], engine=f"{provider}/{model}")
            return None, timings, f"{provider}/{model}"

        timings.update(result.timings or {})
        timings["inference"] = round((time.perf_counter() - started) * 1000, 1)
        timings["model_latency"] = result.latency_ms
        run.emit(
            "generate", "done",
            f"risposta {provider}/{model} in {int(timings['inference'])} ms",
            engine=f"{provider}/{model}",
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
            return None, timings, f"{provider}/{model}"
        run.emit("validate", "done", f"{len(proposals)} proposte valide")
        annotate = getattr(engine.activity, "annotate_timings", None)
        if annotate and getattr(result, "call_id", ""):
            annotate(result.call_id, {
                "json_parsing": parse_ms,
                "inference_total": timings["inference"],
                "total": round((time.perf_counter() - started) * 1000, 1),
            })
        return proposals, timings, f"{provider}/{model}"

    def _execute(
        self,
        run: _Run,
        req: CreativeRequestPayload,
        provider_override: Optional[str],
        model_override: Optional[str],
        avoid_cache: bool,
    ) -> None:
        timings: dict[str, Any] = {}
        started = time.perf_counter()
        wall_started = time.time()
        budget = self._budget_sec()
        deadline_global = wall_started + budget
        first_attempt = self._first_attempt_sec()
        try:
            run.emit("context", "start", "contesto compatto: profilo, stagione, temi usati, categorie")
            ctx_started = time.perf_counter()
            valid_ids = self._authorised_ids(req)
            ctx = self._compact_context(req, valid_ids)
            timings["context_building"] = round((time.perf_counter() - ctx_started) * 1000, 1)
            run.emit(
                "context", "done",
                f"{len(ctx['materiale_disponibile']['categorie'])} categorie, "
                f"{len(ctx['temi_gia_usati'])} temi usati, {len(ctx['materiale_disponibile']['id_utilizzabili'])} ID",
            )
            if run.cancel.is_set():
                raise IdeationCancelled()

            engine = getattr(self.director, "ai_engine", None)
            target_provider = provider_override or ""
            target_model = model_override or ""
            entry: Optional[dict[str, Any]] = None

            run.emit("connect", "start", "snapshot motori (nessuna scansione completa per richiesta)")
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
                detail = (
                    "Nessun motore AI configurato/autenticato: uso il motore deterministico KORA (0 €)."
                    if engine is not None
                    else "AI Engine Manager non collegato: uso il motore deterministico KORA (0 €)."
                )
                run.emit("connect", "error", detail)
                self._finish_deterministic(run, req, ctx, detail, timings, started)
                return
            engine_label = f"{entry.get('provider')}/{entry.get('model')}"
            run.emit("connect", "done", f"motore selezionato: {engine_label}", engine=engine_label)

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

            attempt_deadline = min(deadline_global - 1.2, wall_started + first_attempt)
            proposals, attempt_timings, engine_label = self._engine_attempt(
                run, req, ctx, entry, attempt_deadline, set(valid_ids), "1"
            )
            timings.update({k: v for k, v in attempt_timings.items() if v is not None})

            if proposals is None and not run.cancel.is_set():
                remaining = deadline_global - time.time() - 1.0
                fallback_entry = None
                if remaining > 4.0 and engine is not None:
                    try:
                        plan = engine.candidate_plan(IDEATION_AGENT, from_snapshot=True)
                        for candidate in plan:
                            if candidate.get("provider") != entry.get("provider"):
                                fallback_entry = candidate
                                break
                    except Exception:
                        fallback_entry = None
                if fallback_entry is not None:
                    run.emit(
                        "generate", "fallback",
                        f"fallback autorizzato su {fallback_entry.get('provider')}/{fallback_entry.get('model')}",
                        engine=f"{fallback_entry.get('provider')}/{fallback_entry.get('model')}",
                    )
                    timings["fallback"] = 1
                    second, second_timings, engine_label = self._engine_attempt(
                        run, req, ctx, fallback_entry,
                        deadline_global - 0.8, set(valid_ids), "fallback",
                    )
                    timings.update({k: v for k, v in second_timings.items() if v is not None})
                    proposals = second
                else:
                    timings["fallback"] = 0

            if run.cancel.is_set():
                raise IdeationCancelled()

            if proposals is None:
                reason = timings.get("error") or timings.get("validation_error") or (
                    "budget globale esaurito prima di una risposta valida"
                    if time.time() >= deadline_global
                    else "nessuna risposta valida dal motore AI"
                )
                if time.time() >= deadline_global:
                    reason = (
                        f"timeout globale di {int(budget)} s raggiunto: "
                        "proposte deterministiche KORA (0 €)"
                    )
                self._finish_deterministic(run, req, ctx, str(reason), timings, started)
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
                run.finished_at = time.perf_counter()
        except Exception as exc:  # pragma: no cover — difensivo
            run.emit("fallback", "start", f"errore inatteso: {exc.__class__.__name__}")
            try:
                ctx = self._compact_context(req, self._authorised_ids(req))
                self._finish_deterministic(
                    run, req, ctx, f"errore inatteso ({exc.__class__.__name__}): {exc}", timings, started
                )
            except Exception:
                with run.lock:
                    run.status = "error"
                    run.error = f"Errore inatteso: {exc.__class__.__name__}: {exc}"
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
