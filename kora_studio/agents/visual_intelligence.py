"""Visual Intelligence — real image understanding with honest provenance.

Every analysis carries its provenance (asset hash, provider, model, prompt
version, date) and one of four explicit states:

* ``verified``      — a vision-VERIFIED model really looked at the pixels;
* ``deterministic`` — derived from real pixel statistics, no model used;
* ``stale``         — produced by a fallback or by a model that is no longer the
                      configured one, and a better vision model is now available;
* ``unavailable``   — the asset cannot be read (missing file / unsupported).

When a Vision model becomes verified, fallback analyses are automatically
upgraded. There is no permanent cap on "the first N assets": the whole Vault can
be re-analysed, in batches, without re-paying for valid verified analyses.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from kora_studio.ai_engine.base import EngineError

CACHE_KEY = "visual_intelligence_cache"
PROMPT_VERSION = "vi-2.8.0"

STATES = ("verified", "deterministic", "stale", "unavailable")

PROMPT = (
    "Guarda la fotografia allegata del KORA Beach Club (Policoro) e descrivi SOLO ciò che vedi. "
    "Rispondi in JSON con queste chiavi: description (1-2 frasi), subjects (lista), "
    "lighting (es. luce dorata laterale), environment (mare/pergolato/sala/...), "
    "framing (campo_ampio | piano_medio | dettaglio_ravvicinato), materials (lista), "
    "mood (3 aggettivi), can_carry_scene (true/false), notes."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VisualIntelligence:
    """Vision review of the vault, with provenance, states and auto-upgrade."""

    def __init__(self, vault: Any, engine: Any = None, db: Any = None) -> None:
        self.vault = vault
        self.engine = engine
        self.db = db if db is not None else getattr(vault, "db", None)
        self.last_run: dict[str, Any] = {}

    def attach_engine_manager(self, engine: Any) -> None:
        self.engine = engine

    # ------------------------------------------------------------------ cache
    def _cache(self) -> dict[str, Any]:
        if self.db is None:
            return {}
        try:
            return self.db.get_preference(CACHE_KEY, {}) or {}
        except Exception:
            return {}

    def _store(self, key: str, value: dict[str, Any]) -> None:
        if self.db is None:
            return
        cache = self._cache()
        cache[key] = value
        try:
            self.db.set_preference(CACHE_KEY, cache)
        except Exception:
            pass

    @staticmethod
    def _asset_path(asset: dict[str, Any]) -> Optional[Path]:
        for key in ("working_path", "original_path"):
            value = asset.get(key)
            if value and Path(value).is_file():
                return Path(value)
        return None

    @staticmethod
    def _cache_key(asset: dict[str, Any]) -> str:
        return f"{asset.get('sha256') or asset.get('id')}"

    # ------------------------------------------------------- current signature
    def current_vision_signature(self) -> Optional[dict[str, Any]]:
        """The vision-verified model the engine would actually use right now."""
        engine = self.engine
        if engine is None:
            return None
        try:
            if hasattr(engine, "routing_report"):
                report = engine.routing_report("visual_intelligence", requires_vision=True)
                selected = report.get("selected")
                if selected:
                    return {
                        "provider": selected.get("provider"),
                        "model": selected.get("model"),
                        "billing": selected.get("billing"),
                        "auth_type": selected.get("auth_type") or "",
                    }
            entry = engine.resolve("visual_intelligence", requires_vision=True)
            if entry:
                return {
                    "provider": entry.get("provider"),
                    "model": entry.get("model"),
                    "billing": entry.get("billing"),
                    "auth_type": entry.get("auth_type") or "",
                }
        except Exception:
            return None
        return None

    def _provenance(
        self, asset: dict[str, Any], signature: Optional[dict[str, Any]], state: str
    ) -> dict[str, Any]:
        return {
            "sha256": asset.get("sha256"),
            "provider": (signature or {}).get("provider") or "deterministic",
            "model": (signature or {}).get("model") or "pixel_statistics",
            "prompt_version": PROMPT_VERSION,
            "created_at": _now(),
            "analysis_state": state,
        }

    def resolve_state(self, cached: Optional[dict[str, Any]], current: Optional[dict[str, Any]]) -> str:
        """Map a cache entry to one of the four explicit states."""
        if not cached:
            return "unavailable" if current is None else "unavailable"
        prov = cached.get("provenance") or {}
        if not prov:
            # legacy cache entry (pre-2.8): no provenance, therefore not trustworthy
            return "stale"
        declared = cached.get("analysis_state") or prov.get("analysis_state")
        if declared in STATES:
            state = declared
        elif cached.get("vision_verified"):
            state = "verified"
        else:
            state = "deterministic"
        if state == "unavailable":
            return "unavailable"
        if current is None:
            # no vision model at all: a verified analysis stays valid, a fallback stays fallback
            return "verified" if state == "verified" else ("stale" if state == "stale" else "deterministic")
        same = (
            prov.get("provider") == current.get("provider")
            and prov.get("model") == current.get("model")
            and prov.get("prompt_version") == PROMPT_VERSION
        )
        if state == "verified":
            return "verified" if same else "stale"
        # a better model exists: the fallback (or old model) analysis must be upgraded
        return "stale"

    # ------------------------------------------------------------------ review
    def review_asset(
        self,
        asset: dict[str, Any],
        production_id: Optional[str] = None,
        refresh: bool = False,
        allow_engine: bool = True,
    ) -> dict[str, Any]:
        key = self._cache_key(asset)
        cached = self._cache().get(key)
        current = self.current_vision_signature()
        state = self.resolve_state(cached, current)

        if cached and not refresh and state in ("verified", "deterministic"):
            return {**cached, "cache_hit": True, "analysis_state": state}

        path = self._asset_path(asset)
        declared_path = asset.get("working_path") or asset.get("original_path")
        if path is None:
            review = self._deterministic_review(asset)
            if declared_path:
                # the asset declares a file that cannot be read: say it, don't invent pixels
                review.update(
                    {
                        "analysis_state": "unavailable",
                        "provenance": self._provenance(asset, None, "unavailable"),
                        "notes": "File immagine dichiarato ma non leggibile: nessuna analisi dei pixel.",
                    }
                )
            else:
                review["notes"] = (
                    "Analisi deterministica dai metadati reali dell'asset (nessun file immagine associato, "
                    "nessun modello visivo disponibile)."
                )
            self._store(key, review)
            return {**review, "cache_hit": False}

        fallback = self._deterministic_review(asset)
        engine_ready = allow_engine and self.engine is not None and current is not None
        if not engine_ready:
            review = {
                **fallback,
                "analysis_state": "deterministic" if current is None else (state if state == "stale" else "deterministic"),
                "provenance": self._provenance(asset, None, "deterministic"),
                "fallback_reason": "nessun modello con visione verificata configurato" if current is None else "",
            }
            self._store(key, review)
            return {**review, "cache_hit": False}

        messages = [
            {"role": "system", "content": "Sei il Visual Intelligence di KORA: descrivi solo ciò che vedi."},
            {"role": "user", "content": PROMPT},
        ]
        try:
            result = self.engine.call(
                "visual_intelligence",
                messages,
                requires_vision=True,
                json_mode=True,
                timeout=90,
                image_paths=[path],
                production_id=production_id,
            )
        except EngineError as exc:
            # keep the old analysis, but declare it stale (never fake an upgrade)
            base = cached or fallback
            review = {
                **base,
                "analysis_state": "stale",
                "provenance": {**(base.get("provenance") or self._provenance(asset, None, "stale")),
                               "analysis_state": "stale", "checked_at": _now()},
                "fallback_reason": f"motore visivo non disponibile: {str(exc)[:160]}",
            }
            self._store(key, review)
            return {**review, "cache_hit": False}
        try:
            start = result.text.find("{")
            end = result.text.rfind("}")
            data = json.loads(result.text[start : end + 1]) if start >= 0 and end > start else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict) or not data.get("description"):
            review = {
                **fallback,
                "analysis_state": "stale" if state == "stale" else "deterministic",
                "provenance": self._provenance(asset, None, "deterministic"),
                "fallback_reason": "risposta del modello non interpretabile come JSON",
            }
            self._store(key, review)
            return {**review, "cache_hit": False}
        review = {
            "description": str(data.get("description"))[:400],
            "subjects": [str(s)[:40] for s in (data.get("subjects") or [])][:8],
            "lighting": str(data.get("lighting") or "")[:60],
            "environment": str(data.get("environment") or "")[:60],
            "framing": str(data.get("framing") or "")[:40],
            "materials": [str(m)[:30] for m in (data.get("materials") or [])][:8],
            "mood": [str(m)[:24] for m in (data.get("mood") or [])][:5],
            "can_carry_scene": bool(data.get("can_carry_scene", True)),
            "notes": str(data.get("notes") or "")[:200],
            "source": f"engine:{result.provider}/{result.model}",
            "vision_verified": bool(result.raw.get("vision", True)),
            "analysis_state": "verified",
            "provenance": {
                "sha256": asset.get("sha256"),
                "provider": current.get("provider"),
                "model": result.model or current.get("model"),
                "prompt_version": PROMPT_VERSION,
                "created_at": _now(),
                "analysis_state": "verified",
            },
        }
        self._store(key, review)
        return {**review, "cache_hit": False}

    def _deterministic_review(self, asset: dict[str, Any]) -> dict[str, Any]:
        analysis = asset.get("analysis") or {}
        multimodal = analysis.get("multimodal") or {}
        luminance = float(analysis.get("brightness") or analysis.get("luminance") or 0.5)
        contrast = float(analysis.get("contrast") or 0.5)
        quality = float(analysis.get("technical_quality") or 70.0)
        return {
            "description": (
                f"{asset.get('category', 'Materiale KORA')} reale: "
                f"{multimodal.get('environment', 'ambiente mediterraneo')}, "
                f"{multimodal.get('lighting', 'luce naturale')}."
            ),
            "subjects": [multimodal.get("environment") or asset.get("category") or "ambiente"],
            "lighting": multimodal.get("lighting") or ("luce dorata" if luminance > 0.55 else "luce morbida"),
            "environment": multimodal.get("environment") or asset.get("category") or "",
            "framing": multimodal.get("framing") or "piano_medio",
            "materials": multimodal.get("materials") or [],
            "mood": ["calmo", "mediterraneo", "autentico"] if contrast > 0.25 else ["quieto", "minimale"],
            "can_carry_scene": quality >= 55.0,
            "notes": "Analisi deterministica su statistiche reali dell'immagine (nessun modello visivo disponibile).",
            "source": "deterministic",
            "vision_verified": False,
            "analysis_state": "deterministic",
            "provenance": {
                "sha256": asset.get("sha256"),
                "provider": "deterministic",
                "model": "pixel_statistics",
                "prompt_version": PROMPT_VERSION,
                "created_at": _now(),
                "analysis_state": "deterministic",
            },
        }

    # ------------------------------------------------------------------ choice
    def rank_assets_for_scene(
        self,
        purpose: str,
        assets: list[dict[str, Any]],
        excluded_ids: Optional[set[str]] = None,
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        """Rank real assets for a scene purpose; the engine review improves the ranking."""
        excluded = excluded_ids or set()
        scored: list[tuple[float, dict[str, Any]]] = []
        for asset in assets:
            if asset["id"] in excluded or asset.get("category") == "Logo":
                continue
            review = (asset.get("analysis") or {}).get("multimodal", {}).get("vision_review") or {}
            score = 0.0
            text = " ".join(
                [
                    str(asset.get("category") or ""),
                    " ".join(asset.get("tags") or []),
                    str(review.get("description") or ""),
                    " ".join(review.get("subjects") or []),
                ]
            ).lower()
            for word in (purpose or "").lower().split():
                if len(word) > 3 and word in text:
                    score += 1.0
            if review.get("can_carry_scene") is False:
                score -= 1.5
            if asset.get("media_type") == "video":
                score += 0.2
            score += float((asset.get("analysis") or {}).get("technical_quality") or 0) / 200.0
            scored.append((score, asset))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [asset for _, asset in scored[:limit]]

    # -------------------------------------------------------------- vault runs
    def vault_status(self) -> dict[str, Any]:
        """Provenance overview of the whole Vault, asset by asset."""
        cache = self._cache()
        current = self.current_vision_signature()
        rows: list[dict[str, Any]] = []
        counts = {state: 0 for state in STATES}
        skipped_media = 0
        for asset in self.vault.list_assets():
            if asset.get("media_type") != "image":
                # vision provenance only applies to photographs (audio/video keep their own data)
                skipped_media += 1
                continue
            cached = cache.get(self._cache_key(asset))
            declared = asset.get("working_path") or asset.get("original_path")
            state = self.resolve_state(cached, current) if cached else (
                "unavailable" if (declared and self._asset_path(asset) is None) else "deterministic"
            )
            counts[state] = counts.get(state, 0) + 1
            rows.append(
                {
                    "asset_id": asset["id"],
                    "filename": asset.get("filename"),
                    "analysis_state": state,
                    "provenance": (cached or {}).get("provenance"),
                    "vision_verified": bool((cached or {}).get("vision_verified")),
                }
            )
        return {
            "current_vision_signature": current,
            "counts": counts,
            "total": len(rows),
            "skipped_non_image_assets": skipped_media,
            "assets": rows,
        }

    def annotate_vault(
        self,
        production_id: Optional[str] = None,
        limit: Optional[int] = None,
        force: bool = False,
        include_images_only: bool = True,
    ) -> dict[str, Any]:
        """Analyse every asset that needs it (no permanent first-N cap).

        ``limit`` is a per-run batch size, never a permanent ceiling.
        """
        assets = [
            a
            for a in self.vault.list_assets()
            if not include_images_only or a.get("media_type") == "image"
        ]
        stats = {"scanned": len(assets), "analyzed": 0, "verified": 0, "deterministic": 0,
                 "stale": 0, "unavailable": 0, "skipped": 0, "limit": limit, "force": force}
        processed = 0
        for asset in assets:
            if limit is not None and processed >= limit:
                stats["skipped"] += 1
                continue
            cached = self._cache().get(self._cache_key(asset))
            state = self.resolve_state(cached, self.current_vision_signature())
            needs = force or cached is None or state in ("stale", "unavailable")
            if not needs:
                stats["skipped"] += 1
                continue
            review = self.review_asset(asset, production_id=production_id, refresh=force)
            processed += 1
            stats["analyzed"] += 1
            stats[review.get("analysis_state", "deterministic")] = stats.get(review.get("analysis_state", "deterministic"), 0) + 1
            try:
                self.vault.set_vision_review(asset["id"], review)
            except Exception:
                pass
        self.last_run = stats
        return stats

    def upgrade_fallback_analyses(self, production_id: Optional[str] = None) -> dict[str, Any]:
        """Called after a vision model becomes verified: upgrade stale/fallback rows."""
        if self.current_vision_signature() is None:
            return {"upgraded": 0, "reason": "nessun modello visivo verificato"}
        return self.annotate_vault(production_id=production_id, limit=None, force=False)

    def reanalyze_vault_all(self, production_id: Optional[str] = None) -> dict[str, Any]:
        """Full Vault re-analysis with the currently configured vision model."""
        return self.annotate_vault(production_id=production_id, limit=None, force=True)
