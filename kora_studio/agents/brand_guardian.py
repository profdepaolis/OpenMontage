"""Brand Guardian agent — enforces KORA narrative tone and non-negotiable authenticity."""

from __future__ import annotations

import re
from typing import Any

from kora_studio.brand.brand_brain import FORBIDDEN_COMMERCIAL_PHRASES, BrandBrain

UNVERIFIED_STRUCTURES = [
    "piscina",
    "rooftop",
    "spa",
    "sauna",
    "discoteca",
    "campo da tennis",
    "terrazza panoramica",
    "elicottero",
    "yacht privato",
]


class BrandGuardian:
    """Consults Brand Brain to validate copy, prompts, and visual transformations."""

    def __init__(self, brand_brain: BrandBrain, engine: Any = None) -> None:
        self.brand_brain = brand_brain
        self.engine = engine

    def attach_engine_manager(self, engine: Any) -> None:
        self.engine = engine

    def review_with_engine(self, text: str, production_id: Any = None) -> dict[str, Any]:
        """Second opinion from the configured model; the deterministic rules always apply."""
        if self.engine is None or not (text or "").strip():
            return {"used": False}
        import json

        from kora_studio.ai_engine.base import EngineError

        prompt = (
            "Valuta questo testo per KORA Beach Club (Policoro). Cerca: claim commerciali, prezzi, "
            "servizi non documentati (piscina, spa, rooftop...), tono aggressivo, promesse non vere. "
            'Rispondi SOLO con JSON: {"approved": bool, "violations": [str], "severity": "ok|warning|block"}'
        )
        try:
            result = self.engine.call(
                "brand_guardian",
                [{"role": "system", "content": prompt}, {"role": "user", "content": text[:4000]}],
                json_mode=True,
                timeout=45,
                production_id=production_id,
            )
        except EngineError as exc:
            return {"used": False, "error": str(exc)}
        try:
            start = result.text.find("{")
            end = result.text.rfind("}")
            data = json.loads(result.text[start : end + 1]) if start >= 0 and end > start else {}
        except ValueError:
            return {"used": False, "error": "risposta non interpretabile"}
        if not isinstance(data, dict):
            return {"used": False}
        return {
            "used": True,
            "approved": bool(data.get("approved", True)),
            "violations": [str(v)[:160] for v in (data.get("violations") or [])][:6],
            "severity": str(data.get("severity") or "ok"),
            "engine": f"{result.provider}/{result.model}",
        }

    def validate_copy_with_engine(self, text: str, production_id: Any = None) -> dict[str, Any]:
        """Deterministic validation + optional model review; blocking is conservative."""
        base = self.validate_copy(text)
        review = self.review_with_engine(text, production_id=production_id)
        if review.get("used") and review.get("severity") == "block":
            base["approved"] = False
            base["violations"] = list(base.get("violations") or []) + list(review.get("violations") or [])
        base["engine_review"] = review
        return base

    def validate_copy(self, text: str) -> dict[str, Any]:
        profile = self.brand_brain.get_current()
        lowered = (text or "").lower()
        violations: list[str] = []
        warnings: list[str] = []

        for phrase in FORBIDDEN_COMMERCIAL_PHRASES:
            if phrase in lowered:
                violations.append(
                    f"Espressione commerciale o non coerente con KORA rilevata: '{phrase}'"
                )

        if text.count("!") > 1:
            warnings.append("Uso eccessivo di punti esclamativi; preferire tono pacato ed evocativo.")

        if re.search(r"\b[A-Z]{5,}\b", text.replace("KORA", "")):
            warnings.append("Evitare parole intere in maiuscolo enfatico (eccetto KORA).")

        for struct in UNVERIFIED_STRUCTURES:
            if struct in lowered:
                violations.append(
                    f"Possibile invenzione di elemento strutturale o servizio non documentato: '{struct}'"
                )

        return {
            "approved": len(violations) == 0,
            "violations": violations,
            "warnings": warnings,
            "brand_version": profile.get("version"),
        }

    def validate_generation_prompt(self, prompt: str, is_real_location: bool = True) -> dict[str, Any]:
        lowered = (prompt or "").lower()
        violations: list[str] = []
        for struct in UNVERIFIED_STRUCTURES:
            if struct in lowered:
                violations.append(
                    f"Violazione principio di autenticità: vietato generare '{struct}' non presente al KORA."
                )
        if is_real_location and any(
            w in lowered for w in ("sposta", "rimuovi il pergolato", "cambia edificio", "aggiungi terrazza")
        ):
            violations.append(
                "Vietato alterare architettura, geometria del pergolato o disposizione reale degli arredi."
            )
        return {
            "approved": len(violations) == 0,
            "violations": violations,
            "requires_human_verification": True if is_real_location else False,
        }

    def validate_logo_asset(self, logo_path: str | None) -> dict[str, Any]:
        from pathlib import Path

        if not logo_path or not Path(logo_path).is_file():
            return {
                "approved": False,
                "error": "Logo originale KORA non trovato. Caricare LOGO KORA.PNG nel Media Vault prima del montaggio finale.",
            }
        return {"approved": True, "logo_path": logo_path, "payoff": "The Art of Being"}
