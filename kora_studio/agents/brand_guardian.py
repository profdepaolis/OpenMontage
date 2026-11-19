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

    def __init__(self, brand_brain: BrandBrain) -> None:
        self.brand_brain = brand_brain

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
