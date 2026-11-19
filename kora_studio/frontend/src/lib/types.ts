export type NavSection =
  | "home"
  | "projects"
  | "vault"
  | "creative"
  | "production"
  | "preview"
  | "providers"
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
  required_materials: string[];
  available_asset_ids: string[];
  missing_materials: string[];
  duration_sec: number;
  feasibility_score: number;
  originality_score: number;
  preset: string;
  anti_repetition_notes: string[];
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
  asset_mode: string;
  camera_motion: string;
  transition: string;
  sfx_category?: string | null;
  music_cue?: string | null;
  subtitle_text: string;
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
