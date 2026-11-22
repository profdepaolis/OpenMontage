"""Catalogue of the KORA agents that can be bound to a specific engine/model.

The nine agents are *language* agents: they reason and return text/JSON. Media
generation (images, video, voice) is a different class of service and is handled
by its own provider (Veo/Wan, ElevenLabs/Piper): a model listed here never
generates media, and a media service is never used as a text engine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    id: str
    label: str
    needs_vision: bool = False
    description: str = ""
    #: "text" -> only text output is enough; "vision" -> the agent needs image input
    modality: str = "text"
    #: what the agent must return, used to validate the engine answer
    output: str = "json"


AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        "creative_director",
        "Creative Director",
        description="Ideazione concept, hook, payoff e proposte narrative.",
        output="json",
    ),
    AgentSpec(
        "cinematic_story_director",
        "Cinematic Story Director",
        description="Piano di regia: inquadrature, movimenti camera, transizioni, ritmo.",
        output="json",
    ),
    AgentSpec(
        "visual_intelligence",
        "Visual Intelligence",
        needs_vision=True,
        modality="vision",
        description="Analisi visiva reale dei materiali: contenuto, luce, coerenza, idoneità alla scena.",
        output="json",
    ),
    AgentSpec(
        "brand_guardian",
        "Brand Guardian",
        description="Verifica tono di voce, claim vietati e identità KORA.",
        output="json",
    ),
    AgentSpec(
        "ai_media_lab",
        "AI Media Lab",
        needs_vision=True,
        modality="vision",
        description="Prompt e valutazione per crop, animazioni conservative e generazioni vincolate.",
        output="json",
    ),
    AgentSpec(
        "voice_director",
        "Voice Director",
        description="Adattamento del copione a pause, respiro e voce scelta.",
        output="json",
    ),
    AgentSpec(
        "sound_director",
        "Sound Director",
        description="Scelta traccia e SFX coerenti con l'inventario sonoro reale.",
        output="json",
    ),
    AgentSpec(
        "quality_guardian",
        "Quality Guardian",
        needs_vision=True,
        modality="vision",
        description="Controlli visivi e narrativi del Reel su frame realmente estratti.",
        output="json",
    ),
    AgentSpec(
        "copywriter",
        "Copywriter",
        description="Caption Instagram, hashtag e testi social.",
        output="json",
    ),
)

AGENTS_BY_ID = {agent.id: agent for agent in AGENTS}
VISION_AGENTS = {agent.id for agent in AGENTS if agent.needs_vision}

#: Media services are NOT text engines: they are configured in AI Providers, and
#: the engine manager only ever sends them prompts/decisions through an agent.
MEDIA_SERVICES = {
    "voice": ("elevenlabs_api", "piper_tts_local"),
    "video": ("google_veo_api", "qwen_wan_api", "fal_ai"),
    "image": ("leonardo_ai", "google_veo_api"),
}
