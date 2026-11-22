"""Copywriter — Instagram caption, hashtags and alt text for the KORA Reel.

The text is produced by the configured engine (``copywriter`` agent) and then
validated by the Brand Guardian: a caption that violates the brand rules is
discarded and the deterministic, brand-safe template is used instead. The
response always declares which source produced it.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from kora_studio.ai_engine.base import EngineError

SYSTEM = (
    "Sei il copywriter di KORA Beach Club (Policoro), payoff 'The Art of Being'. "
    "Tono luxury mediterraneo, calmo ed evocativo; mai slogan aggressivi, mai prezzi o sconti, "
    "mai servizi non documentati. Scrivere in italiano. "
    'Rispondi SOLO con JSON: {"caption": str, "hashtags": [str], "alt_text": str}'
)


class Copywriter:
    def __init__(self, engine: Any = None, guardian: Any = None) -> None:
        self.engine = engine
        self.guardian = guardian

    def attach_engine_manager(self, engine: Any) -> None:
        self.engine = engine

    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def write(
        self,
        *,
        title: str,
        message: str,
        theme: str,
        contact: Optional[dict[str, Any]] = None,
        production_id: Optional[str] = None,
        fallback: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        engine_result: Optional[dict[str, Any]] = None
        error: Optional[str] = None
        if self.engine is not None:
            payload = {
                "titolo": title,
                "messaggio": message,
                "tema": theme,
                "contatti": contact or {},
                "vincoli": [
                    "nessun prezzo o sconto",
                    "nessun servizio non documentato",
                    "payoff esatto 'The Art of Being'",
                    "massimo 2 emoji",
                ],
            }
            try:
                result = self.engine.call(
                    "copywriter",
                    [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    json_mode=True,
                    timeout=60,
                    production_id=production_id,
                )
            except EngineError as exc:
                error = str(exc)
            else:
                data = self._parse(result.text or "")
                caption = str(data.get("caption") or "").strip()
                hashtags = [str(h).strip() for h in (data.get("hashtags") or []) if str(h).strip()]
                if caption:
                    verdict = self.guardian.validate_copy(caption) if self.guardian else {"approved": True}
                    if verdict.get("approved"):
                        engine_result = {
                            "caption": caption,
                            "hashtags": hashtags or ["#KoraBeachClub", "#TheArtOfBeing"],
                            "alt_text": str(data.get("alt_text") or "")[:300],
                            "source": f"engine:{result.provider}/{result.model}",
                            "brand_check": verdict,
                        }
                    else:
                        error = "testo scartato dal Brand Guardian: " + "; ".join(
                            str(v) for v in verdict.get("violations", [])
                        )[:220]

        base = dict(fallback or {})
        if engine_result:
            caption = engine_result["caption"]
            hashtags = engine_result["hashtags"]
            base.update(
                {
                    "caption": caption,
                    "hashtags": hashtags,
                    "full_post_text": f"{caption}\n\n{' '.join(hashtags)}",
                    "alt_text": engine_result["alt_text"],
                    "source": engine_result["source"],
                    "engine_error": None,
                }
            )
            return base
        base.setdefault("caption", f"{title}.\n\n{message}")
        base.setdefault("hashtags", ["#KoraBeachClub", "#TheArtOfBeing", "#Policoro"])
        base.setdefault("full_post_text", f"{base['caption']}\n\n{' '.join(base['hashtags'])}")
        base["source"] = "deterministic_template"
        base["engine_error"] = error
        return base
