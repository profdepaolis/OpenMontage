"""Catalogue of the KORA agents that can be bound to a specific engine/model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    id: str
    label: str
    needs_vision: bool = False
    description: str = ""


AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        "creative_director",
        "Creative Director",
        description="Ideazione concept, hook, payoff e proposte narrative.",
    ),
    AgentSpec(
        "cinematic_story_director",
        "Cinematic Story Director",
        description="Storyboard, ritmo, arco narrativo e transizioni.",
    ),
    AgentSpec(
        "visual_intelligence",
        "Visual Intelligence",
        needs_vision=True,
        description="Analisi visiva dei materiali, scelta dettagli e coerenza estetica.",
    ),
    AgentSpec(
        "brand_guardian",
        "Brand Guardian",
        description="Verifica tono di voce, claim vietati e identità KORA.",
    ),
    AgentSpec(
        "ai_media_lab",
        "AI Media Lab",
        needs_vision=True,
        description="Prompt e valutazione per crop, animazioni e generazioni controllate.",
    ),
    AgentSpec(
        "voice_director",
        "Voice Director",
        description="Adattamento del copione alle pause e alla voce scelta.",
    ),
    AgentSpec(
        "sound_director",
        "Sound Director",
        needs_vision=False,
        description="Scelta traccia e SFX coerenti con la scena.",
    ),
    AgentSpec(
        "quality_guardian",
        "Quality Guardian",
        needs_vision=True,
        description="Controlli visivi e narrativi automatici del Reel.",
    ),
    AgentSpec(
        "copywriter",
        "Copywriter",
        description="Caption Instagram, hashtag e testi social.",
    ),
)

AGENTS_BY_ID = {agent.id: agent for agent in AGENTS}
VISION_AGENTS = {agent.id for agent in AGENTS if agent.needs_vision}
