"""Visual Intelligence — real image understanding for KORA's authentic material.

The agent sends the actual picture to a **vision-verified** model and records what
it sees (subjects, light, framing, textures, whether the frame can carry a scene).
Results are cached per asset + model, so the Reel production does not pay twice.

Without a verified vision model the agent still works, but it says so: it derives
the description from the real pixel statistics and the existing analysis, marks
``source="deterministic"`` and never pretends an image was seen by a model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from kora_studio.ai_engine.base import EngineError

CACHE_KEY = "visual_intelligence_cache"

PROMPT = (
    "Guarda la fotografia allegata del KORA Beach Club (Policoro) e descrivi SOLO ciò che vedi. "
    "Rispondi in JSON con queste chiavi: description (1-2 frasi), subjects (lista), "
    "lighting (es. luce dorata laterale), environment (mare/pergolato/sala/...), "
    "framing (campo_ampio | piano_medio | dettaglio_ravvicinato), materials (lista), "
    "mood (3 aggettivi), can_carry_scene (true/false), notes."
)


class VisualIntelligence:
    """Vision review of the vault and scene-level asset decisions."""

    def __init__(self, vault: Any, engine: Any = None, db: Any = None) -> None:
        self.vault = vault
        self.engine = engine
        self.db = db if db is not None else getattr(vault, "db", None)

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
        # keep the cache bounded
        if len(cache) > 400:
            for stale in list(cache.keys())[:100]:
                cache.pop(stale, None)
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

    # ------------------------------------------------------------------ review
    def review_asset(
        self,
        asset: dict[str, Any],
        production_id: Optional[str] = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        path = self._asset_path(asset)
        key = f"{asset.get('sha256') or asset.get('id')}"
        cached = self._cache().get(key)
        if cached and not refresh:
            return cached
        fallback = self._deterministic_review(asset)
        if self.engine is None or path is None:
            self._store(key, fallback)
            return fallback
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
        except EngineError:
            self._store(key, fallback)
            return fallback
        try:
            start = result.text.find("{")
            end = result.text.rfind("}")
            data = json.loads(result.text[start : end + 1]) if start >= 0 and end > start else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict) or not data.get("description"):
            self._store(key, fallback)
            return fallback
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
        }
        self._store(key, review)
        return review

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

    def annotate_vault(self, production_id: Optional[str] = None, limit: int = 12) -> int:
        """Attach a vision review to the newest assets that do not have one yet."""
        updated = 0
        for asset in self.vault.list_assets()[:limit]:
            if (asset.get("analysis") or {}).get("multimodal", {}).get("vision_review"):
                continue
            review = self.review_asset(asset, production_id=production_id)
            try:
                self.vault.set_vision_review(asset["id"], review)
                updated += 1
            except Exception:
                continue
        return updated
