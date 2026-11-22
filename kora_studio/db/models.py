"""Canonical Pydantic domain models for KORA STUDIO."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class JobState(str, Enum):
    IN_PREPARAZIONE = "In preparazione"
    ANALISI_MATERIALI = "Analisi materiali"
    CONCEPT = "Concept"
    STORYBOARD = "Storyboard"
    GENERAZIONE_ASSET = "Generazione asset"
    VOICEOVER = "Voiceover"
    MONTAGGIO = "Montaggio"
    SOUND_DESIGN = "Sound design"
    RENDERING = "Rendering"
    CONTROLLO_QUALITA = "Controllo qualità"
    COMPLETATO = "Completato"
    RICHIEDE_INTERVENTO = "Richiede intervento"
    ERRORE = "Errore"


class ProductionMode(str, Enum):
    AUTO = "AUTO"
    GUIDATA = "GUIDATA"
    REGIA_COMPLETA = "REGIA_COMPLETA"


class AuthenticityStatus(str, Enum):
    AUTENTICO = "autentico"
    DERIVATO_CONSERVATIVO = "derivato_conservativo"
    GENERATIVO_CONTROLLATO = "generativo_controllato"
    ILLUSTRATIVO_SINTETICO = "illustrativo_sintetico"
    DA_VERIFICARE = "da_verificare"


class EditingPreset(str, Enum):
    CINEMATIC_IDENTITY = "KORA Cinematic Identity"
    EXPERIENCE = "KORA Experience"
    SEASONAL = "KORA Seasonal"
    EDITORIAL = "KORA Editorial"
    CUSTOM = "KORA Custom"


class CameraMotion(str, Enum):
    SLOW_PUSH_IN = "slow_push_in"
    SLOW_PULL_OUT = "slow_pull_out"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    TILT_UP = "tilt_up"
    TILT_DOWN = "tilt_down"
    MICRO_PARALLAX = "micro_parallax"
    STATIC_BREATHE = "static_breathe"


class SceneTransition(str, Enum):
    CROSS_DISSOLVE = "cross_dissolve"
    WARM_FADE = "warm_fade"
    CUT_ON_MOTION = "cut_on_motion"
    FADE_BLACK = "fade_black"


class StoryboardScene(BaseModel):
    scene_number: int
    start_frame: int
    end_frame: int
    duration_frames: int
    start_sec: float
    end_sec: float
    duration_sec: float
    narrative_goal: str
    spoken_text: str = ""
    voice_id: str = "brando"
    selected_asset_id: Optional[str] = None
    alternative_asset_id: Optional[str] = None
    visual_rationale: str = ""
    shot_framing: str = "piano_medio"
    asset_mode: str = "original"  # original | crop_detail | animated_photo | i2v | outro
    camera_motion: str = CameraMotion.SLOW_PUSH_IN.value
    transition: str = SceneTransition.CROSS_DISSOLVE.value
    sfx_category: Optional[str] = None
    sfx_asset_id: Optional[str] = None
    music_cue: Optional[str] = None
    subtitle_text: str = ""
    generative_prompt: Optional[str] = None
    authenticity_status: str = AuthenticityStatus.AUTENTICO.value
    production_status: str = "pianificata"
    approval_status: str = "approvata"
    is_outro: bool = False
    crop_focus: Optional[dict[str, float]] = None  # x, y, zoom
    # v3.5 — identità stabile, blocchi per componente, direzione artistica
    scene_uid: Optional[str] = None
    locks: dict[str, bool] = Field(default_factory=dict)
    motion_intensity: float = 1.0
    motion_easing: str = "ease_in_out"
    caption_style: Optional[str] = None
    caption_overrides: dict[str, Any] = Field(default_factory=dict)
    title_text: Optional[str] = None
    asset_history: list[str] = Field(default_factory=list)
    motion_speed: float = 1.0
    composition: str = "regola_terzi"
    # Smart Shot Generator output: the real derived framing consumed by the editor
    shot_plan: Optional[dict[str, Any]] = None
    # Provenance of the plan: smart_shot | story_director_fallback | manual
    shot_source: str = "smart_shot"
    preview_status: str = "pending"  # pending | previewed | approved



class CreativeRequestPayload(BaseModel):
    theme: str = ""
    objective: str = ""
    duration_sec: float = Field(default=20.0, ge=8.0, le=90.0)
    voice_id: str = "brando"
    asset_ids: list[str] = Field(default_factory=list)
    media_selection: str = "vault_auto"  # vault_auto | selected_only | allow_crops | allow_controlled_gen
    generation_policy: str = "authentic_first"  # authentic_first | conservative_only | controlled_i2v
    budget_eur: float = Field(default=0.0, ge=0.0, le=20.0)
    brand_profile_version: Optional[int] = None
    #: la durata scelta dall'utente vince sempre sul consiglio dell'AI
    duration_locked: bool = True
    #: la voce scelta dall'utente non viene sostituita senza autorizzazione
    voice_locked: bool = True
    previous_production_ids: list[str] = Field(default_factory=list)
    custom_instructions: str = ""


class CreativeConcept(BaseModel):
    id: str
    title: str
    central_idea: str
    message: str
    primary_emotion: str
    hook: str
    narrative_arc: str
    ending: str
    voiceover_script: str
    visual_mood: str
    moodboard_tags: list[str] = Field(default_factory=list)
    required_materials: list[str]
    available_asset_ids: list[str] = Field(default_factory=list)
    missing_materials: list[str] = Field(default_factory=list)
    objective: str = ""
    recommended_voice_id: str = "brando"
    duration_sec: float = 20.0
    feasibility_score: float = 1.0
    originality_score: float = 1.0
    preset: str = EditingPreset.CINEMATIC_IDENTITY.value
    generation_engine: str = "deterministic_offline_fallback"
    fallback_reason: Optional[str] = None
    anti_repetition_notes: list[str] = Field(default_factory=list)
    creative_request: dict[str, Any] = Field(default_factory=dict)


class ThemeProposalLite(BaseModel):
    """2.8.1 — lightweight ideation payload (no voiceover, no storyboard, no prompts)."""

    id: str
    title: str
    hook: str
    central_message: str
    narrative_direction: str
    recommended_objective: str = ""
    recommended_voice_id: str = "brando"
    recommended_duration_sec: float = 20.0
    asset_ids: list[str] = Field(default_factory=list, max_length=3)
    #: engine:<provider>/<model> | deterministic | cache
    source: str = "deterministic"
    from_cache: bool = False
    cache_age_sec: Optional[float] = None


class ThemeProposalsLiteEnvelope(BaseModel):
    proposals: list[ThemeProposalLite] = Field(min_length=1, max_length=6)
    generation_engine: str = "deterministic_offline_fallback"
    model: str = ""
    from_cache: bool = False
    cache_age_sec: Optional[float] = None
    fallback_reason: Optional[str] = None
    timings_ms: dict[str, Any] = Field(default_factory=dict)


class CreativeProposalsEnvelope(BaseModel):
    concepts: list[CreativeConcept] = Field(min_length=1, max_length=6)
    generation_engine: str = "deterministic_offline_fallback"
    fallback_reason: Optional[str] = None


QC_STATUS_PASSED = "superato"
QC_STATUS_FAILED = "non_superato"
QC_STATUS_NOT_APPLICABLE = "non_applicabile"
QC_STATUS_NOT_VERIFIED = "non_verificato"
QC_STATUSES = (
    QC_STATUS_PASSED,
    QC_STATUS_FAILED,
    QC_STATUS_NOT_APPLICABLE,
    QC_STATUS_NOT_VERIFIED,
)


class QualityCheckItem(BaseModel):
    category: str  # technical | visual | narrative
    name: str
    passed: bool
    severity: str = "error"  # error | warning | info
    detail: str = ""
    requires_human_review: bool = False
    #: superato | non_superato | non_applicabile | non_verificato (v3.6)
    status: str = ""

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        if not self.status:
            object.__setattr__(self, "status", QC_STATUS_PASSED if self.passed else QC_STATUS_FAILED)
        elif self.status not in QC_STATUSES:
            object.__setattr__(self, "status", QC_STATUS_PASSED if self.passed else QC_STATUS_FAILED)

    @property
    def status_label(self) -> str:
        return {
            QC_STATUS_PASSED: "Superato",
            QC_STATUS_FAILED: "Non superato",
            QC_STATUS_NOT_APPLICABLE: "Non applicabile",
            QC_STATUS_NOT_VERIFIED: "Non verificato",
        }.get(self.status, self.status)

    def blocks_delivery(self) -> bool:
        return self.status == QC_STATUS_FAILED and self.severity == "error"


class QualityReport(BaseModel):
    passed: bool
    needs_human_review: bool = False
    human_review_reasons: list[str] = Field(default_factory=list)
    checks: list[QualityCheckItem] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class RevisionProposal(BaseModel):
    instruction: str
    target_scenes: list[int]
    global_changes: list[str]
    what_will_change: list[str]
    preserved_scenes: list[int]
    selective_render_possible: bool = True
