export type NavSection =
  | "home"
  | "projects"
  | "vault"
  | "creative"
  | "production"
  | "preview"
  | "providers"
  | "engine"
  | "brand"
  | "export";

export type ProductionMode = "AUTO" | "GUIDATA" | "REGIA_COMPLETA";

export interface MediaAsset {
  id: string;
  filename: string;
  sha256: string;
  media_type: "image" | "video" | "audio" | "graphic";
  extension: string;
  origin: string;
  acquired_at: string;
  file_size: number;
  duration_sec?: number | null;
  orientation: string;
  width?: number | null;
  height?: number | null;
  fps?: number | null;
  category: string;
  tags: string[];
  technical_quality: number;
  authenticity_status: string;
  usage_rights: string;
  projects_used: string[];
  relevance_score?: number;
  duplicate?: boolean;
}

export interface CreativeRequestPayload {
  theme: string;
  objective: string;
  duration_sec: number;
  voice_id: string;
  asset_ids: string[];
  media_selection: string;
  generation_policy: string;
  budget_eur: number;
  brand_profile_version?: number | null;
  previous_production_ids: string[];
  custom_instructions: string;
}

export interface CreativeConcept {
  id: string;
  title: string;
  central_idea: string;
  message: string;
  primary_emotion: string;
  hook: string;
  narrative_arc: string;
  ending: string;
  voiceover_script: string;
  visual_mood: string;
  moodboard_tags?: string[];
  required_materials: string[];
  available_asset_ids: string[];
  missing_materials: string[];
  objective?: string;
  recommended_voice_id?: string;
  duration_sec: number;
  feasibility_score: number;
  originality_score: number;
  preset: string;
  generation_engine?: string;
  fallback_reason?: string | null;
  anti_repetition_notes: string[];
  creative_request?: Partial<CreativeRequestPayload>;
}

export interface StoryboardScene {
  scene_number: number;
  start_frame: number;
  end_frame: number;
  duration_frames: number;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  narrative_goal: string;
  spoken_text: string;
  voice_id: string;
  selected_asset_id?: string | null;
  alternative_asset_id?: string | null;
  visual_rationale?: string;
  shot_framing?: string;
  asset_mode: string;
  camera_motion: string;
  transition: string;
  sfx_category?: string | null;
  music_cue?: string | null;
  subtitle_text: string;
  generative_prompt?: string | null;
  authenticity_status?: string;
  production_status: string;
  approval_status: string;
  is_outro: boolean;
}

export interface ProjectRecord {
  id: string;
  title: string;
  theme: string;
  mode: string;
  preset: string;
  production_mode: ProductionMode;
  voice_id: string;
  fps: number;
  target_duration_sec: number;
  total_frames: number;
  status: string;
  current_version: number;
  concept: Partial<CreativeConcept>;
  storyboard: StoryboardScene[];
  edit_plan: Record<string, unknown>;
  audio_plan: Record<string, unknown>;
  preferences?: Record<string, unknown>;
  qc_report: {
    passed?: boolean;
    needs_human_review?: boolean;
    human_review_reasons?: string[];
    checks?: Array<{
      category: string;
      name: string;
      passed: boolean;
      severity: string;
      detail: string;
      requires_human_review?: boolean;
    }>;
    metrics?: Record<string, unknown>;
  };
  export_bundle: {
    reel_mp4?: string;
    cover_png?: string;
    caption?: string;
    hashtags?: string[];
    no_subtitles_mp4?: string | null;
    no_music_mp4?: string | null;
  };
  output_video_path?: string | null;
  created_at: string;
  updated_at: string;
}

export interface JobRecord {
  id: string;
  project_id: string;
  job_type: string;
  state: string;
  current_step: string;
  completed_steps: string[];
  total_steps: number;
  error_message?: string | null;
  intervention_reason?: string | null;
  cancelled: boolean;
  created_at: string;
  updated_at: string;
  logs: Array<{
    level: string;
    agent: string;
    message: string;
    created_at: string;
  }>;
}

export interface ProviderRecord {
  id: string;
  name: string;
  capabilities: string[];
  endpoint: string;
  auth_type: string;
  models: string[];
  declared_costs: Record<string, unknown>;
  quota_status: string;
  limits_doc: string;
  last_check?: string | null;
  operational_status: string;
  avg_latency_ms?: number | null;
  recent_errors: string[];
  has_api_key: boolean;
  masked_api_key: string;
  enabled: boolean;
  is_free_local: boolean;
}

export interface BudgetSettings {
  paid_budget_eur: number;
  monthly_cap_eur: number;
  max_allowed_cap_eur: number;
  estimated_month_eur: number;
  actual_month_eur: number;
  remaining_paid_budget_eur: number;
  remaining_monthly_cap_eur: number;
}

export interface VoiceOption {
  id: string;
  label: string;
  tone: string;
  status: string;
  vault_duration_sec?: number | null;
  elevenlabs_voice_id?: string;
  elevenlabs_configured: boolean;
  openai_tts_configured?: boolean;
  piper_local_available: boolean;
}

export interface CaptionStyleMeta {
  id: string;
  label: string;
  engine: string;
  description: string;
}

export interface SoundAsset {
  id: string;
  name: string;
  category: string;
  duration_sec: number;
  license: string;
}

export interface HyperframesConfig {
  caption_style: string;
  title_card_intro: string;
  grain_overlay: string;
  vignette: string;
  letterbox: string;
  motion_pace: string;
}

export interface ThemeSuggestionResponse {
  recommended_preset: string;
  recommended_hyperframes: HyperframesConfig;
  rationale: string;
  theme_suggestions: string[];
}

// ---- AI Engine Manager ----------------------------------------------------
export type AIEngineState =
  | "indisponibile"
  | "installato"
  | "autenticato"
  | "operativo"
  | "errore";

export type AIEngineStrategy = "auto" | "max_quality" | "min_cost" | "local_only";

export interface AIEngineProvider {
  provider: string;
  label: string;
  kind: "cli" | "http";
  local: boolean;
  subscription: boolean;
  requires_key: boolean;
  state: AIEngineState;
  installed: boolean;
  authenticated: boolean;
  verified_operational: boolean;
  last_test_at?: string | null;
  last_test_ok?: boolean | null;
  last_latency_ms?: number | null;
  version?: string | null;
  binary?: string | null;
  detail: string;
  auth_detail: string;
  account?: string | null;
  models_count: number;
  supports_vision: boolean;
  unknown_vision_models?: number;
  unknown_cost_models?: number;
  billing_kinds?: string[];
  attachments?: boolean;
  attachment_mode?: string | null;
  docs_url: string;
  cost_summary?: { zero_cost_models: number; paid_models: number; unknown: number };
}

export interface AIEngineAgent {
  id: string;
  label: string;
  needs_vision: boolean;
  description: string;
  mode: "inherit" | "provider";
  provider?: string | null;
  model?: string | null;
}

export interface AIEngineSettings {
  strategy: AIEngineStrategy;
  primary_provider: string;
  fallback: "off" | "free_only" | "authorized";
  max_budget_eur: number;
  allow_paid_calls: boolean;
  allow_unknown_costs: boolean;
  allow_external_media: boolean;
  declared_subscriptions: Record<string, string>;
  declared_free_quota: Record<string, boolean>;
  providers: Record<string, { enabled?: boolean; model?: string; base_url?: string }>;
  agents: Record<string, { mode: "inherit" | "provider"; provider?: string; model?: string }>;
}

export interface AIEngineStatus {
  settings: AIEngineSettings;
  providers: AIEngineProvider[];
  agents: AIEngineAgent[];
  strategies: AIEngineStrategy[];
  fallback_modes: Array<"off" | "free_only" | "authorized">;
  ollama_installed: boolean;
  ollama_required: boolean;
  operational: string[];
  budget: { max_budget_eur: number; allow_paid_calls: boolean; spent_note: string };
  activity?: AIActivitySummary;
}

export interface AIActivitySummary {
  calls: number;
  ok: number;
  errors: number;
  fallbacks: number;
  known_cost_eur: number;
  actual_cost_eur?: number;
  estimated_cost_eur?: number;
  unknown_cost_calls: number;
  note: string;
}

export interface AIActivityCall {
  id: string;
  production_id?: string | null;
  agent: string;
  provider: string;
  model: string;
  started_at: string;
  duration_ms: number;
  outcome: string;
  attempts: number;
  fallback_used: boolean;
  fallback_from?: string | null;
  cost_eur?: number | null;
  cost_known: boolean;
  billing: string;
  execution: string;
  error_kind?: string | null;
  error_message?: string | null;
  cancelled: boolean;
  estimated_cost_eur?: number | null;
  actual_cost_eur?: number | null;
  unit_price_per_1k_eur?: number | null;
  cost_basis?: string;
  attachments?: number;
  auth_type?: string;
  request_status?: string;
  vision_image?: boolean;
}

export interface PreviewSceneCard {
  scene_number: number;
  is_outro: boolean;
  duration_frames: number;
  duration_sec: number;
  start_sec: number;
  original_photo: string | null;
  derived_shot: {
    framing: string;
    trajectory?: string | null;
    crop?: { x: number; y: number; w: number; h: number } | null;
    zoom_start?: number | null;
    zoom_end?: number | null;
    easing?: string | null;
    motion_intensity?: number | null;
    residual_resolution?: { width: number; height: number } | null;
    source?: string;
    motivation?: string | null;
  };
  camera_move: string;
  voiceover: { text: string; voice_id: string; subtitles: string };
  subtitle_text: string;
  transition: string;
  sound_design: { sfx?: string | null; music_cue?: string | null };
  approval_status: string;
  preview_status: string;
  thumbnail_source: string | null;
  thumbnail_derived: string | null;
  preview_clip: string;
}

export interface PreviewManifest {
  project_id: string;
  title: string;
  storyboard_hash: string;
  audio_included: boolean;
  audio_note: string;
  resolution: string;
  fps: number;
  total_frames: number;
  duration_sec: number;
  preview_mp4: string;
  cards: PreviewSceneCard[];
  scene_clips_cached: number;
  cache_hit: boolean;
  scene_approvals?: Record<string, string>;
}

export interface AIEngineModel {
  id: string;
  label: string;
  provider: string;
  auth_type: string;
  billing: string;
  subscription_name?: string | null;
  text: boolean;
  vision: boolean | null;
  vision_documented?: boolean | null;
  vision_known: boolean;
  vision_verified: boolean;
  execution: string;
  zero_cost: boolean;
  cost_known: boolean;
  cost_per_1k_eur: number | null;
  quota_status: string;
  latency_ms?: number | null;
  source: string;
  notes?: string;
}

export interface AIEngineTestResult {
  ok: boolean;
  provider: string;
  model?: string;
  latency_ms?: number;
  preview?: string;
  multimodal?: boolean;
  vision_verified?: boolean | null;
  expected_content_seen?: boolean | null;
  cost_eur?: number | null;
  cost_known?: boolean;
  billing?: string;
  execution?: string;
  detail?: string;
  provider_status?: AIEngineProvider;
  probe?: { expected: string; path: string };
}
