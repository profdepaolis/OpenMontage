"""Canonical KORA brand context shared by every engine.

The same versioned brand profile, authenticity rules, confirmed seasonal data and
relevant creative history are injected into each adapter call, so the output does
not depend on any external account memory (ChatGPT threads, OpenCode sessions):
the official memory of the productions lives in the KORA STUDIO database.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional


class BrandContextBuilder:
    """Builds the identical context block for all agents/engines."""

    AUTHENTICITY_RULES = (
        "Gerarchia materiali: originale reale → ritaglio originale → animazione conservativa → "
        "generazione vincolata → image-to-video. Mai inventare architetture o arredi non presenti "
        "nelle foto reali KORA.",
        "Logo originale 'LOGO KORA.PNG' e payoff esatto 'The Art of Being' solo in chiusura.",
        "Nessun prezzo, sconto, urgenza o claim aggressivo. Nessuna data promozionale scaduta.",
    )

    def __init__(
        self,
        brand_brain: Any,
        history_provider: Optional[Any] = None,
        season_provider: Optional[Any] = None,
    ) -> None:
        self.brand_brain = brand_brain
        self.history_provider = history_provider
        self.season_provider = season_provider

    def _season(self) -> str:
        if self.season_provider is not None:
            try:
                return str(self.season_provider())
            except Exception:
                pass
        month = datetime.now(timezone.utc).month
        if month in (11, 12, 1, 2):
            return "Autunno/Inverno sul mare — luce radente, quiete, accoglienza intima"
        if month in (3, 4, 5):
            return "Primavera mediterranea — aria limpida, prime colazioni all'aperto"
        if month in (6, 7, 8):
            return "Estate al KORA — luce dorata, mare vivo, tramonti sotto il parasole"
        return "Fine estate sul litorale — ritmo disteso, aperitivi al tramonto"

    def _history(self, limit: int = 8) -> list[dict[str, Any]]:
        if self.history_provider is None:
            return []
        try:
            rows = self.history_provider(limit)
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for row in rows or []:
            out.append(
                {
                    "title": row.get("title"),
                    "hook": row.get("hook"),
                    "preset": row.get("preset"),
                    "theme": row.get("theme"),
                }
            )
        return out

    def build(self, agent_id: str, vault_inventory: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        profile = {}
        version = None
        try:
            profile = self.brand_brain.get_active_profile() or {}
            version = profile.get("version")
        except Exception:
            profile = {}
        return {
            "brand": "KORA Beach Club · Policoro",
            "payoff": "The Art of Being",
            "brand_profile_version": version,
            "tone_of_voice": profile.get("tone_of_voice", []),
            "values": profile.get("values", []),
            "forbidden_claims": profile.get("forbidden_claims", []),
            "palette": profile.get("palette", []),
            "authenticity_rules": list(self.AUTHENTICITY_RULES),
            "season_confirmed": self._season(),
            "creative_history": self._history(),
            "vault_inventory": vault_inventory or [],
            "agent": agent_id,
            "context_source": "kora_studio.database",
        }

    def to_prompt_block(self, context: dict[str, Any]) -> str:
        """Compact, deterministic text block prepended to every engine call."""
        return (
            "CONTESTO UFFICIALE KORA STUDIO (fonte: database locale, non memoria esterna)\n"
            + json.dumps(context, ensure_ascii=False, indent=2)
        )
