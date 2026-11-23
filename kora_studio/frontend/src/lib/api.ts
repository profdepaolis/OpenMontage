import type {
  AIActivityCall,
  AIStatus,
  AIActivitySummary,
  AIEngineModel,
  AIEngineSettings,
  AIEngineStatus,
  AIEngineTestResult,
  BudgetSettings,
  CreativeConcept,
  CreativeRequestPayload,
  HyperframesConfig,
  JobRecord,
  MediaAsset,
  ProjectRecord,
  PreviewManifest,
  PreviewSceneCard,
  ProviderRecord,
  StoryboardScene,
  ThemeSuggestionResponse,
  IdeationRun,
  ThemeProposalLite,
  VoiceOption,
} from "./types";

/** Riga di scena restituita dall'editor di produzione. */

export type SyncIssue = {
  code: string;
  severity: "error" | "warning" | string;
  message: string;
};

export type TimelineWord = {
  word_id?: string;
  word: string;
  start_sec: number;
  end_sec: number;
  start_frame?: number;
  end_frame?: number;
  timing_source?: string;
  confidence?: number | null;
};

export type TimelineSegment = {
  segment_uid: string;
  text: string;
  start_sec: number;
  end_sec: number;
  scene_uid?: string | null;
  scene_number?: number | null;
  review_status?: string;
  word_ids?: string[];
};

export type TimelineCue = {
  cue_uid: string;
  text: string;
  start_sec: number;
  end_sec: number;
  scene_number?: number | null;
  timing_source?: string;
};

export type TimelineRecord = {
  fps: number;
  duration_sec: number;
  total_frames: number;
  audio: {
    path: string;
    source: string;
    duration_sec: number;
    start_offset_sec: number;
    end_sec: number;
    end_padding_sec: number;
    effective_duration_sec: number;
    is_scene_aligned: boolean;
    speech_regions: Array<{ start_sec: number; end_sec: number }>;
  };
  transcript: {
    alignment: string;
    verified: boolean;
    engine?: string | null;
    model?: string | null;
    language?: string;
    text: string;
    words: TimelineWord[];
    segments: TimelineSegment[];
  };
  caption_cues: TimelineCue[];
};

export type SceneRow = {
  scene_number: number;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  duration_frames: number;
  spoken_text: string;
  subtitle_text: string;
  asset_id: string | null;
  camera_motion: string;
  transition: string;
  is_outro: boolean;
  preview_status: string;
  approval_status: string;
  narrative_goal: string;
  // v3.5
  scene_uid?: string | null;
  locks?: Record<string, boolean>;
  is_locked?: boolean;
  motion_intensity?: number;
  motion_easing?: string;
  motion_speed?: number;
  composition?: string;
  shot_framing?: string;
  crop_focus?: { x: number; y: number; zoom: number } | null;
  caption_style?: string | null;
  caption_overrides?: Record<string, unknown>;
  title_text?: string | null;
  asset_history?: string[];
  authenticity_status?: string;
  shot_source?: string;
};

export type CaptionPreset = {
  id: string;
  label: string;
  description: string;
  engine_style: string;
  engine: string;
  overrides: Record<string, unknown>;
};

export type DirectionChange = {
  kind: string;
  scene_number?: number | null;
  patch?: Record<string, unknown>;
  preset?: string;
  overrides?: Record<string, unknown>;
  locks?: Record<string, boolean>;
  why?: string;
  blocked?: boolean;
  reason?: string;
  source?: string;
};

export type ScriptProposal = {
  scene_number: number | null;
  scene_uid?: string | null;
  before: string;
  after: string;
  source: string;
  changed: boolean;
};

export type GenerationRecord = {
  id: string;
  scene_number: number;
  mode: string;
  label: string;
  engine: string;
  prompt: string;
  authenticity: string;
  status: string;
  asset_id: string | null;
  created_at: string;
  requires_human_review: boolean;
};

export type VaultOption = {
  id: string;
  label: string;
  media_type: string | null;
  category: string | null;
  path_ok: boolean;
};

/** Trasforma un detail strutturato (es. preflight) in un messaggio leggibile. */
function humanizeError(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (typeof item === "object" && item && "msg" in item ? String((item as { msg: unknown }).msg) : String(item)))
      .join(" · ");
  }
  if (detail && typeof detail === "object") {
    const obj = detail as {
      message?: string;
      blocking?: Array<{ label?: string; detail?: string }>;
      warnings?: Array<{ label?: string; detail?: string }>;
    };
    const lines: string[] = [];
    if (obj.message) lines.push(obj.message);
    for (const item of obj.blocking || []) lines.push(`• ${item.label}: ${item.detail}`);
    for (const item of obj.warnings || []) lines.push(`avviso · ${item.label}: ${item.detail}`);
    if (lines.length) return lines.join("\n");
  }
  return "Errore inatteso";
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail: unknown = `Errore HTTP ${res.status}`;
    try {
      const err = await res.json();
      detail = err.detail ?? detail;
    } catch {
      // corpo non JSON: resta il messaggio HTTP
    }
    throw new Error(humanizeError(detail));
  }
  return res.json() as Promise<T>;
}

export interface ModelTestRecord {
  model: string;
  kind: string;
  at: string;
  ok: boolean;
  latency_ms: number | null;
  error_kind: string;
  error: string;
  notes: string;
  themes?: number;
}

export interface ModelMigrationNote {
  at?: string;
  from?: string;
  from_all?: string[];
  to?: string;
  retired?: boolean;
  detail?: string;
}

export interface ModelManagerConfig {
  user_choice?: boolean;
  migration?: ModelMigrationNote | null;
  roles: { everyday: string; premium: string; fallback: string };
  timeouts: { ideation: number; design: number; revision: number; premium: number };
  policy: "ask" | "fallback" | "local" | "stop";
  mode: "everyday" | "premium";
  policies: Record<string, string>;
  defaults: {
    roles: { everyday: string; premium: string; fallback: string };
    timeouts: { ideation: number; design: number; revision: number; premium: number };
  };
}

export const koraApi = {
  health: () => apiFetch<Record<string, unknown>>("/api/health"),

  // Modelli del motore creativo (v3.6)
  modelConfig: () => apiFetch<ModelManagerConfig>("/api/ai-models/config"),
  updateModelConfig: (patch: Record<string, unknown>) =>
    apiFetch<ModelManagerConfig>("/api/ai-models/config", {
      method: "PUT",
      body: JSON.stringify(patch),
    }),
  refreshModels: () =>
    apiFetch<Record<string, unknown>>("/api/ai-models/refresh", { method: "POST" }),
  searchModels: (query = "", provider = "") =>
    apiFetch<{
      models: Array<{ id: string; provider: string; last_test: ModelTestRecord | null }>;
      total: number;
      providers: string[];
      cached: boolean;
      fetched_at_iso: string | null;
      error: string;
      cli_version: string | null;
    }>(`/api/ai-models/search?q=${encodeURIComponent(query)}&provider=${encodeURIComponent(provider)}`),
  modelDiagnostics: () => apiFetch<Record<string, unknown>>("/api/ai-models/diagnostics"),
  testModel: (model: string, kind: "quick" | "creative" = "quick", timeout?: number) =>
    apiFetch<ModelTestRecord>("/api/ai-models/test", {
      method: "POST",
      body: JSON.stringify({ model, kind, timeout }),
    }),
  runBenchmark: (kind: "quick" | "creative" = "quick", models?: string[]) =>
    apiFetch<{ kind: string; at: string; models: string[]; results: ModelTestRecord[]; passed: number }>(
      "/api/ai-models/benchmark",
      { method: "POST", body: JSON.stringify({ kind, models }) }
    ),
  modelTests: (model = "", limit = 10) =>
    apiFetch<ModelTestRecord[]>(
      `/api/ai-models/tests?model=${encodeURIComponent(model)}&limit=${limit}`
    ),

  // Brand Brain
  getBrand: () => apiFetch<Record<string, unknown>>("/api/brand"),
  updateBrand: (profile: Record<string, unknown>, note: string) =>
    apiFetch<Record<string, unknown>>("/api/brand", {
      method: "PUT",
      body: JSON.stringify({ profile, note }),
    }),
  brandHistory: () => apiFetch<Array<Record<string, unknown>>>("/api/brand/history"),
  refreshPalette: () =>
    apiFetch<Record<string, unknown>>("/api/brand/refresh-palette", { method: "POST" }),
  rollbackBrand: (version: number) =>
    apiFetch<Record<string, unknown>>(`/api/brand/rollback/${version}`, { method: "POST" }),

  // Media Vault
  listVault: (mediaType?: string, category?: string) => {
    const params = new URLSearchParams();
    if (mediaType) params.set("media_type", mediaType);
    if (category) params.set("category", category);
    const qs = params.toString();
    return apiFetch<MediaAsset[]>(`/api/vault${qs ? `?${qs}` : ""}`);
  },
  searchVault: (q: string) =>
    apiFetch<MediaAsset[]>(`/api/vault/search?q=${encodeURIComponent(q)}`),
  uploadMedia: async (files: FileList | File[], category?: string) => {
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    if (category) fd.append("category", category);
    return apiFetch<{
      uploaded: MediaAsset[];
      errors: Array<{ filename: string; error: string }>;
      suggested_concepts: CreativeConcept[];
    }>("/api/vault/upload", { method: "POST", body: fd });
  },
  detailCrop: (assetId: string, detailLabel: string, zoom = 1.8) =>
    apiFetch<Record<string, unknown>>(`/api/vault/${assetId}/detail-crop`, {
      method: "POST",
      body: JSON.stringify({ detail_label: detailLabel, zoom }),
    }),

  // Creative & Voices
  surpriseMe: (durationSec = 20) =>
    apiFetch<{
      concepts: CreativeConcept[];
      generation_engine?: string;
      fallback_reason?: string | null;
    }>("/api/creative/surprise", {
      method: "POST",
      body: JSON.stringify({ theme: "", duration_sec: durationSec }),
    }),
  fromTheme: (
    themeOrPayload: string | Partial<CreativeRequestPayload>,
    durationSec = 20,
    assetIds: string[] = []
  ) => {
    const body =
      typeof themeOrPayload === "string"
        ? { theme: themeOrPayload, duration_sec: durationSec, asset_ids: assetIds }
        : themeOrPayload;
    return apiFetch<{
      concepts: CreativeConcept[];
      generation_engine?: string;
      fallback_reason?: string | null;
      recommendation?: ThemeSuggestionResponse;
    }>("/api/creative/from-theme", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  // 2.8.1 — Fast Creative Mode (4 proposte lite, budget 25 s, annullabile)
  suggestThemesLite: (
    payload: Partial<CreativeRequestPayload> = {},
    options: { provider?: string; model?: string; avoidCache?: boolean } = {}
  ) => {
    const query = new URLSearchParams();
    if (options.provider) query.set("provider", options.provider);
    if (options.model) query.set("model", options.model);
    if (options.avoidCache) query.set("avoid_cache", "true");
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return apiFetch<{
      run_id: string;
      status: string;
      budget_ms: number;
      stages: { stage: string; label: string }[];
      poll_url: string;
    }>(`/api/creative/suggest-lite${suffix}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getSuggestThemesLite: (runId: string) =>
    apiFetch<IdeationRun>(`/api/creative/suggest-lite/${runId}`),
  cancelSuggestThemesLite: (runId: string) =>
    apiFetch<{ cancelled: boolean; detail: string }>(
      `/api/creative/suggest-lite/${runId}/cancel`,
      { method: "POST" }
    ),
  decideSuggestThemesLite: (
    runId: string,
    action: "retry" | "fallback" | "local" | "cancel"
  ) =>
    apiFetch<{ ok: boolean; action: string; run_id: string; previous_run_id?: string }>(
      `/api/creative/suggest-lite/${runId}/decide`,
      { method: "POST", body: JSON.stringify({ action }) }
    ),
  aiModelsStatus: () => apiFetch<AIStatus>("/api/ai-models/status"),
  expandTheme: (
    proposal: ThemeProposalLite,
    durationSec?: number,
    assetIds?: string[]
  ) =>
    apiFetch<{
      concepts: CreativeConcept[];
      generation_engine?: string;
      fallback_reason?: string | null;
      recommendation?: ThemeSuggestionResponse;
    }>("/api/creative/expand-theme", {
      method: "POST",
      body: JSON.stringify({ proposal, duration_sec: durationSec, asset_ids: assetIds }),
    }),
  suggestLiteCacheStats: () =>
    apiFetch<{ entries: number; hits: number }>("/api/creative/suggest-lite-cache/stats"),
  suggestThemes: (payload: Partial<CreativeRequestPayload> = {}) =>
    apiFetch<{
      concepts: CreativeConcept[];
      generation_engine?: string;
      fallback_reason?: string | null;
    }>("/api/creative/suggest", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  // KORA STUDIO Essential — un solo payload per la schermata Studio
  studioBootstrap: () =>
    apiFetch<{
      voices: VoiceOption[];
      recent_projects: ProjectRecord[];
      vault: { total: number; categories: Record<string, number> };
      engine: {
        active_provider: string;
        state: string;
        model: string;
        essential: boolean;
        mode?: string;
        role?: string;
        model_label?: string;
        policy?: string;
        fallback_model?: string;
        detail?: string;
        migration?: { detail?: string; from?: string; to?: string } | null;
        last_test?: { model?: string; at?: string; latency_ms?: number; kind?: string } | null;
        last_inference?: {
          at?: string;
          provider?: string;
          model?: string;
          ok?: boolean;
          latency_ms?: number | null;
          error?: string;
        } | null;
      };
      ai_status?: Record<string, unknown>;
      cached_proposals: number;
    }>("/api/studio/bootstrap"),
  // Essential: Progetta Reel in UNA sola richiesta creativa strutturata
  projectReel: (payload: {
    request: Partial<CreativeRequestPayload>;
    proposal?: unknown | null;
    create_project?: boolean;
    mode?: string;
    production_mode?: string;
    hyperframes?: Partial<HyperframesConfig>;
    caption_style?: string;
    destination_dir?: string | null;
    deadline_sec?: number;
  }) =>
    apiFetch<{
      design: {
        concept: CreativeConcept;
        scenes: Record<string, unknown>[];
        caption: string;
        cover: { title: string; subtitle: string };
        sound_design: Record<string, string>;
        generation_engine: string;
        model: string;
        fallback_reason: string | null;
        timings_ms: Record<string, number | string | null | undefined>;
        calls: number;
      };
      project: (ProjectRecord & { storyboard?: Record<string, unknown>[] }) | null;
    }>("/api/creative/project-reel", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  recommendHyperframes: (theme: string, durationSec = 20) =>
    apiFetch<ThemeSuggestionResponse>("/api/hyperframes/recommend", {
      method: "POST",
      body: JSON.stringify({ theme, duration_sec: durationSec }),
    }),
  suggestTheme: (theme: string, durationSec = 20) =>
    apiFetch<ThemeSuggestionResponse>("/api/hyperframes/recommend", {
      method: "POST",
      body: JSON.stringify({ theme, duration_sec: durationSec }),
    }),
  listVoices: () => apiFetch<VoiceOption[]>("/api/voices"),
  setVoiceElevenLabsId: (voiceId: string, elevenlabsVoiceId: string) =>
    apiFetch<{ voice_id: string; elevenlabs_voice_id: string }>(
      `/api/voices/${voiceId}/elevenlabs-id`,
      {
        method: "PUT",
        body: JSON.stringify({ elevenlabs_voice_id: elevenlabsVoiceId }),
      }
    ),
  listSounds: () => apiFetch<Array<Record<string, unknown>>>("/api/sounds"),

  // Projects & Production
  listProjects: () => apiFetch<ProjectRecord[]>("/api/projects"),
  getProject: (id: string) => apiFetch<ProjectRecord>(`/api/projects/${id}`),
  createProject: (payload: {
    title: string;
    theme: string;
    mode: string;
    preset: string;
    production_mode: string;
    voice_id: string;
    target_duration_sec: number;
    concept?: CreativeConcept;
    caption_style?: string;
    hyperframes?: Partial<HyperframesConfig>;
    creative_request?: Partial<CreativeRequestPayload>;
    asset_ids?: string[];
    destination_dir?: string | null;
    auto_start?: boolean;
  }) =>
    apiFetch<{ project: ProjectRecord; job: JobRecord | null }>("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateStoryboard: (id: string, scenes: StoryboardScene[]) =>
    apiFetch<ProjectRecord>(`/api/projects/${id}/storyboard`, {
      method: "PUT",
      body: JSON.stringify({ scenes }),
    }),
  syncProjectAudio: (
    id: string,
    payload: {
      caption_style?: string;
      hyperframes?: Partial<HyperframesConfig>;
      voice_id?: string;
      preset?: string;
      music_id?: string;
      include_music?: boolean;
      music_start_sec?: number;
      music_volume?: number;
      voice_volume?: number;
      auto_sync_scenes_to_voice?: boolean;
    }
  ) =>
    apiFetch<ProjectRecord>(`/api/projects/${id}/sync-audio`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  produceProject: (
    id: string,
    opts?: {
      include_subtitles?: boolean;
      include_no_subtitles_variant?: boolean;
      include_no_music_variant?: boolean;
    }
  ) =>
    apiFetch<{ project: ProjectRecord; job: JobRecord }>(`/api/projects/${id}/produce`, {
      method: "POST",
      body: JSON.stringify(opts || {}),
    }),
  createPreview: (id: string, force = false) =>
    apiFetch<PreviewManifest>(`/api/projects/${id}/preview?force=${force ? "true" : "false"}`, {
      method: "POST",
    }),
  previewManifest: (id: string) => apiFetch<PreviewManifest>(`/api/projects/${id}/preview`),
  previewVideoUrl: (id: string) => `/api/projects/${id}/preview/video`,
  previewThumbUrl: (id: string, scene: number, kind: "source" | "derived") =>
    `/api/projects/${id}/preview/scene/${scene}/thumb/${kind}`,
  reviseScene: (id: string, scene: number, instruction: string) =>
    apiFetch<{
      applied: boolean;
      scene_number: number;
      proposal: { changes: Record<string, unknown>; source: string; note: string };
      preserved_approved_scenes: number[];
      preview?: PreviewManifest;
      card?: PreviewSceneCard;
    }>(`/api/projects/${id}/scenes/${scene}/revise`, {
      method: "POST",
      body: JSON.stringify({ instruction, regenerate_preview: true }),
    }),
  approveScene: (id: string, scene: number, approved = true) =>
    apiFetch<{ scene_number: number; approval_status: string; approved_scenes: number[] }>(
      `/api/projects/${id}/scenes/${scene}/approve`,
      { method: "POST", body: JSON.stringify({ approved }) }
    ),
  visionStatus: () =>
    apiFetch<{
      current_vision_signature: Record<string, string> | null;
      counts: Record<string, number>;
      total: number;
      skipped_non_image_assets: number;
    }>("/api/vault/vision/status"),
  visionReanalyze: (force = true) =>
    apiFetch<Record<string, number>>("/api/vault/vision/reanalyze", {
      method: "POST",
      body: JSON.stringify({ force }),
    }),
  previewRevision: (id: string, instruction: string) =>
    apiFetch<{
      instruction: string;
      target_scenes: number[];
      global_changes: string[];
      what_will_change: string[];
      preserved_scenes: number[];
    }>(`/api/projects/${id}/revision/preview`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),
  applyRevision: (id: string, instruction: string) =>
    apiFetch<{ project: ProjectRecord; job: JobRecord }>(`/api/projects/${id}/revision/apply`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),
  projectVersions: (id: string) =>
    apiFetch<Array<{ id: number; version: number; summary: string; agent_name: string; created_at: string }>>(
      `/api/projects/${id}/versions`
    ),
  restoreVersion: (id: string, version: number) =>
    apiFetch<ProjectRecord>(`/api/projects/${id}/versions/${version}/restore`, {
      method: "POST",
    }),

  voiceoverScript: (id: string) =>
    apiFetch<{
      project_id: string;
      title: string;
      voice_id: string;
      segments: Array<{ scene_number: number; text: string; start_sec: number; end_sec: number }>;
      full_text: string;
      segmented_text: string;
      char_count: number;
      target_duration_sec: number;
      elevenlabs_url: string;
      instructions: string;
    }>(`/api/projects/${id}/voiceover/script`),
  uploadVoiceover: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiFetch<{
      project: ProjectRecord;
      voiceover: {
        filename: string;
        path: string;
        size_bytes: number;
        duration_sec: number;
        total_frames: number;
      };
    }>(`/api/projects/${id}/voiceover/upload`, { method: "POST", body: form });
  },
  archiveProject: (id: string, destination: string, folderName?: string) =>
    apiFetch<{
      project_id: string;
      destination: string;
      files: string[];
      archived: boolean;
      project: ProjectRecord;
    }>(`/api/projects/${id}/archive`, {
      method: "POST",
      body: JSON.stringify({ destination, folder_name: folderName || null }),
    }),
  exportProject: (id: string, destination: string, folderName?: string) =>
    apiFetch<{ project_id: string; destination: string; files: string[] }>(
      `/api/projects/${id}/export-to`,
      { method: "POST", body: JSON.stringify({ destination, folder_name: folderName || null }) }
    ),
  unarchiveProject: (id: string) =>
    apiFetch<ProjectRecord>(`/api/projects/${id}/unarchive`, { method: "POST" }),
  deleteProject: (id: string) =>
    apiFetch<{ deleted: boolean; project_id: string; title: string; removed_paths: string[] }>(
      `/api/projects/${id}`,
      { method: "DELETE" }
    ),
  clearVoiceover: (id: string) =>
    apiFetch<{ project_id: string; cleared: boolean }>(`/api/projects/${id}/voiceover`, {
      method: "DELETE",
    }),

  // Jobs
  listJobs: (projectId?: string) =>
    apiFetch<JobRecord[]>(`/api/jobs${projectId ? `?project_id=${projectId}` : ""}`),
  getJob: (jobId: string) => apiFetch<JobRecord>(`/api/jobs/${jobId}`),
  cancelJob: (jobId: string) =>
    apiFetch<JobRecord>(`/api/jobs/${jobId}/cancel`, { method: "POST" }),
  retryJob: (jobId: string) =>
    apiFetch<JobRecord>(`/api/jobs/${jobId}/retry`, { method: "POST" }),

  // Providers & Budget
  listProviders: () => apiFetch<ProviderRecord[]>("/api/providers"),
  setProviderKey: (id: string, apiKey: string) =>
    apiFetch<ProviderRecord>(`/api/providers/${id}/key`, {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey }),
    }),
  checkProvider: (id: string) =>
    apiFetch<ProviderRecord>(`/api/providers/${id}/check`, { method: "POST" }),
  installPiperModel: () =>
    apiFetch<{ ok: boolean; files: string[]; directory: string; provider: ProviderRecord }>(
      "/api/providers/piper_tts_local/install-model",
      { method: "POST" }
    ),
  fetchElevenLabsVoices: () =>
    apiFetch<
      Array<{
        voice_id: string;
        name: string;
        category: string;
        preview_url?: string;
        labels?: Record<string, string>;
      }>
    >("/api/providers/elevenlabs_api/voices"),
  runVeoMediaLab: (payload: {
    mode: "image_to_video" | "text_to_video";
    prompt: string;
    duration_sec: number;
    asset_id?: string | null;
    camera_motion?: string;
    explicit_authorization: boolean;
  }) =>
    apiFetch<Record<string, unknown>>("/api/media-lab/veo", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  runAIVideoLab: (payload: {
    engine: string;
    model?: string | null;
    mode: "image_to_video" | "text_to_video";
    prompt: string;
    duration_sec: number;
    asset_id?: string | null;
    camera_motion?: string;
    explicit_authorization: boolean;
  }) =>
    apiFetch<Record<string, unknown>>("/api/media-lab/video", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  setupRemotion: () =>
    apiFetch<{
      remotion: {
        available: boolean;
        node?: string | null;
        remotion_bin?: string | null;
        chromium?: string | null;
        reasons: string[];
        install_command?: string;
      };
      provider: ProviderRecord;
      logs: string[];
    }>("/api/providers/remotion_local/setup", { method: "POST" }),
  getBudget: () => apiFetch<BudgetSettings>("/api/budget"),
  updateBudget: (paidBudgetEur?: number, monthlyCapEur?: number) =>
    apiFetch<BudgetSettings>("/api/budget", {
      method: "PUT",
      body: JSON.stringify({
        paid_budget_eur: paidBudgetEur,
        monthly_cap_eur: monthlyCapEur,
      }),
    }),

  // AI Engine Manager (provider universali, routing, agenti)
  engineStatus: (refresh = false) =>
    apiFetch<AIEngineStatus>(`/api/ai-engine/status${refresh ? "?refresh=true" : ""}`),
  engineUpdateSettings: (patch: Partial<AIEngineSettings>) =>
    apiFetch<AIEngineStatus & AIEngineSettings & { status: AIEngineStatus }>("/api/ai-engine/settings", {
      method: "PUT",
      body: JSON.stringify(patch),
    }),
  engineVerify: (providerId: string, live = false) =>
    apiFetch<AIEngineStatus["providers"][number] & { live_test?: AIEngineTestResult }>(
      `/api/ai-engine/verify/${providerId}${live ? "?live=true" : ""}`,
      { method: "POST" }
    ),
  engineVisionTest: (providerId: string, model?: string) =>
    apiFetch<AIEngineTestResult>("/api/ai-engine/vision-test", {
      method: "POST",
      body: JSON.stringify({ provider: providerId, model }),
    }),
  engineActivity: (limit = 100) =>
    apiFetch<{ calls: AIActivityCall[]; summary: AIActivitySummary }>(
      `/api/ai-engine/activity?limit=${limit}`
    ),
  engineSetPrimary: (providerId: string) =>
    apiFetch<AIEngineStatus & AIEngineSettings & { status: AIEngineStatus }>("/api/ai-engine/settings", {
      method: "PUT",
      body: JSON.stringify({ primary_provider: providerId }),
    }),
  engineTest: (payload: {
    provider: string;
    multimodal?: boolean;
    model?: string;
    prompt?: string;
    asset_id?: string;
  }) =>
    apiFetch<AIEngineTestResult>("/api/ai-engine/test", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  // Produzione scene (v3.2)
  listScenes: (projectId: string) =>
    apiFetch<{
      project_id: string;
      title: string;
      total_sec: number;
      has_voice_mix: boolean;
      scenes: SceneRow[];
      available_assets: VaultOption[];
    }>(`/api/projects/${projectId}/scenes`),
  updateScene: (projectId: string, sceneNumber: number, patch: Record<string, unknown>) =>
    apiFetch<{ ok: boolean; scenes: SceneRow[] }>(`/api/projects/${projectId}/scenes/${sceneNumber}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  addScene: (projectId: string, payload: Record<string, unknown>) =>
    apiFetch<{ ok: boolean; added: SceneRow; scenes: SceneRow[] }>(`/api/projects/${projectId}/scenes`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteScene: (projectId: string, sceneNumber: number) =>
    apiFetch<{ ok: boolean; removed: number; scenes: SceneRow[] }>(
      `/api/projects/${projectId}/scenes/${sceneNumber}`,
      { method: "DELETE" }
    ),
  sceneText: (projectId: string, sceneNumber: number, instruction = "") =>
    apiFetch<{
      ok: boolean;
      applied?: boolean;
      scene_number: number;
      text: string;
      previous_text?: string;
      source: string;
      note: string;
      violations?: string[];
    }>(
      `/api/projects/${projectId}/scenes/${sceneNumber}/text`,
      { method: "POST", body: JSON.stringify({ instruction }) }
    ),
  applySceneText: (projectId: string, sceneNumber: number, text: string) =>
    apiFetch<{ ok: boolean; scene_number: number; text: string; previous_text: string }>(
      `/api/projects/${projectId}/scenes/${sceneNumber}/text/apply`,
      { method: "POST", body: JSON.stringify({ instruction: text }) }
    ),
  projectProgress: (projectId: string) =>
    apiFetch<{
      percent: number;
      done: number;
      total: number;
      current: string;
      stages: Array<{ id: string; label: string; done: boolean; detail: string }>;
    }>(`/api/projects/${projectId}/progress`),
  projectReadiness: (projectId: string) =>
    apiFetch<{
      ready: boolean;
      render_ready?: boolean;
      scene_count: number;
      total_frames?: number;
      target_frames?: number;
      audio_state?: string;
      preview_note?: string;
      checks: Array<{ id: string; label: string; ok: boolean; detail: string; blocking: boolean }>;
    }>(`/api/projects/${projectId}/readiness`),

  engineConnectOptions: () =>
    apiFetch<{
      essential?: { active_provider?: string; enabled?: boolean } | null;
      options: Array<{
        id: string;
        label: string;
        cost: string;
        how: string;
        ready: boolean;
        state?: string;
        detail?: string;
        link?: string;
      }>;
    }>("/api/engines/connect-options"),
  engineConnectGemini: (apiKey: string, model?: string) =>
    apiFetch<{
      ok: boolean;
      model: string;
      models: string[];
      detail: string;
      billing?: string;
      cost_verified?: boolean;
      essential?: { active_provider?: string } | null;
    }>("/api/engines/gemini/connect", {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey, model: model || "" }),
    }),
  engineModels: (providerId: string) =>
    apiFetch<AIEngineModel[]>(`/api/ai-engine/models?provider=${encodeURIComponent(providerId)}`),
  engineSetKey: (providerId: string, apiKey: string) =>
    apiFetch<AIEngineStatus["providers"][number]>(`/api/ai-engine/key/${providerId}`, {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey }),
    }),
  engineSetAgent: (
    agentId: string,
    conf: { mode: "inherit" | "provider"; provider?: string; model?: string }
  ) =>
    apiFetch<{ agent: AIEngineStatus["agents"][number] }>(`/api/ai-engine/agent/${agentId}`, {
      method: "PUT",
      body: JSON.stringify(conf),
    }),

  // ---------------------------------------------------------------- v3.5
  sceneDuplicate: (projectId: string, sceneNumber: number) =>
    apiFetch<{ ok: boolean; blocked?: boolean; reason?: string; scenes: SceneRow[] }>(
      `/api/projects/${projectId}/scenes/${sceneNumber}/duplicate`,
      { method: "POST" }
    ),
  sceneMove: (projectId: string, sceneNumber: number, direction: "up" | "down") =>
    apiFetch<{ ok: boolean; blocked?: boolean; reason?: string; scenes: SceneRow[] }>(
      `/api/projects/${projectId}/scenes/${sceneNumber}/move`,
      { method: "POST", body: JSON.stringify({ direction }) }
    ),
  sceneLocks: (projectId: string, sceneNumber: number, locks: Record<string, boolean>) =>
    apiFetch<{ ok: boolean; locks: Record<string, boolean>; scenes: SceneRow[] }>(
      `/api/projects/${projectId}/scenes/${sceneNumber}/locks`,
      { method: "POST", body: JSON.stringify({ locks }) }
    ),
  scenePreview: (projectId: string, sceneNumber: number) =>
    apiFetch<{
      scene_number: number;
      resolution: string;
      fps: number;
      duration_sec: number;
      revision: string;
      url: string;
      cache_hit: boolean;
      audio_status: string | null;
    }>(`/api/projects/${projectId}/scenes/${sceneNumber}/preview`, { method: "POST" }),
  sceneRestoreAsset: (projectId: string, sceneNumber: number) =>
    apiFetch<{ ok: boolean; blocked?: boolean; reason?: string; scenes: SceneRow[] }>(
      `/api/projects/${projectId}/scenes/${sceneNumber}/restore-asset`,
      { method: "POST" }
    ),
  captionPanel: (projectId: string) =>
    apiFetch<{
      presets: CaptionPreset[];
      defaults: Record<string, unknown>;
      safe_area: { top: number; bottom: number };
      fonts: Record<string, string>;
      positions: string[];
      alignments: string[];
      preset: string;
      overrides: Record<string, unknown>;
      engine_style: string;
    }>(`/api/projects/${projectId}/caption`),
  captionApply: (
    projectId: string,
    payload: { preset: string; overrides?: Record<string, unknown>; scene_number?: number }
  ) =>
    apiFetch<{
      ok: boolean;
      blocked?: boolean;
      reason?: string;
      scope: string;
      preset: string;
      overrides: Record<string, unknown>;
      warnings?: string[];
      audio_untouched: boolean;
      note?: string;
    }>(`/api/projects/${projectId}/caption/apply`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  sceneTitle: (projectId: string, sceneNumber: number, text: string) =>
    apiFetch<{ ok: boolean; blocked?: boolean; reason?: string; title_text: string | null }>(
      `/api/projects/${projectId}/scenes/${sceneNumber}/title`,
      { method: "POST", body: JSON.stringify({ text }) }
    ),
  scriptPropose: (
    projectId: string,
    payload: { scope: string; scene_numbers?: number[]; instruction?: string }
  ) =>
    apiFetch<{
      ok: boolean;
      applied: boolean;
      blocked?: boolean;
      scope: string;
      scope_label?: string;
      proposals: ScriptProposal[];
      rejected?: Array<{ scene_number: number; text: string; violations: string[] }>;
      locked_scenes?: number[];
      source: string;
      calls?: number;
      note?: string;
      reason?: string;
      requires_render?: boolean;
    }>(`/api/projects/${projectId}/script/propose`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  scriptApply: (projectId: string, payload: { scope: string; proposals: ScriptProposal[] }) =>
    apiFetch<{
      ok: boolean;
      applied: boolean;
      skipped?: Array<{ scene_number: number | null; reason: string }>;
      audio_invalidated?: boolean;
      reason?: string;
    }>(`/api/projects/${projectId}/script/apply`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  directionPropose: (projectId: string, instruction: string) =>
    apiFetch<{
      ok: boolean;
      instruction: string;
      source: string;
      changes: DirectionChange[];
      blocked: DirectionChange[];
      notes: string[];
      starts_production: boolean;
    }>(`/api/projects/${projectId}/direction/propose`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),
  directionApply: (projectId: string, changes: DirectionChange[]) =>
    apiFetch<{
      ok: boolean;
      applied: DirectionChange[];
      failed: Array<{ change: DirectionChange; error: string }>;
      audio_invalidated: boolean;
    }>(`/api/projects/${projectId}/direction/apply`, {
      method: "POST",
      body: JSON.stringify({ changes }),
    }),
  generatorModes: () =>
    apiFetch<{
      modes: Array<{
        id: string;
        label: string;
        cost: string;
        engine: string;
        authenticity: string;
        description: string;
        requires_reference: boolean;
        tested: boolean;
      }>;
      engines: Record<string, { label: string; free: boolean; credential: string | null }>;
      budget_zero: boolean;
      note: string;
    }>("/api/scene-generator/modes"),
  generatorPreflight: (payload: { mode: string; engine?: string | null }) =>
    apiFetch<{
      available: boolean;
      requires_authorization: boolean;
      tested: boolean;
      declared_limit: string;
      checks: Array<{ check: string; ok: boolean; detail: string }>;
      blocking: Array<{ check: string; detail: string }>;
    }>("/api/scene-generator/preflight", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  generatorHistory: (projectId: string) =>
    apiFetch<{ project_id: string; generations: GenerationRecord[] }>(
      `/api/projects/${projectId}/scene-generator/history`
    ),
  generatorGenerate: (
    projectId: string,
    payload: {
      mode: string;
      scene_number: number;
      prompt?: string;
      duration_sec?: number;
      engine?: string | null;
      explicit_authorization?: boolean;
    }
  ) =>
    apiFetch<{
      ok: boolean;
      blocked?: boolean;
      requires_authorization?: boolean;
      reason?: string;
      generation?: GenerationRecord;
      note?: string;
    }>(`/api/projects/${projectId}/scene-generator/generate`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  generatorUse: (projectId: string, generationId: string) =>
    apiFetch<{ ok: boolean; generation: GenerationRecord }>(
      `/api/projects/${projectId}/scene-generator/${generationId}/use`,
      { method: "POST" }
    ),
  generatorDiscard: (projectId: string, generationId: string) =>
    apiFetch<{ ok: boolean; discarded: string }>(
      `/api/projects/${projectId}/scene-generator/${generationId}/discard`,
      { method: "POST" }
    ),
  generatorSave: (projectId: string, generationId: string) =>
    apiFetch<{ ok: boolean; saved_path: string; asset_id: string }>(
      `/api/projects/${projectId}/scene-generator/${generationId}/save`,
      { method: "POST" }
    ),

  // ---------------------------------------------------------------- v3.6.2
  /** Inventario reale dei motori (ffmpeg, remotion, whisper, TTS...). */
  mediaEngines: (refresh = false) =>
    apiFetch<{
      generated_at: string;
      engines: Array<{
        id: string;
        label: string;
        kind: string;
        installed: boolean;
        configured: boolean;
        available: boolean;
        test_state: "superato" | "fallito" | "non eseguito";
        test_detail: string;
        missing: string[];
        requirements: string;
        details: Record<string, unknown>;
      }>;
      summary: {
        total: number;
        installed: number;
        available: number;
        with_problems: string[];
        render_ready: boolean;
      };
    }>(`/api/media/engines${refresh ? "?refresh=true" : ""}`),
  mediaEngineTest: (engineId: string) =>
    apiFetch<{
      engine: string;
      ok: boolean;
      detail: string;
      latency_ms: number;
      engine_state: Record<string, unknown> | null;
    }>(`/api/media/engines/${encodeURIComponent(engineId)}/test`, { method: "POST" }),
  mediaVoices: () =>
    apiFetch<{
      ok: boolean;
      detail?: string;
      voices: VoiceOption[];
      tts_engines: Array<{
        id: string;
        label: string;
        installed: boolean;
        configured: boolean;
        available: boolean;
        detail: string;
        requirements: string;
      }>;
    }>("/api/media/voices"),
  mediaSelection: (projectId: string) =>
    apiFetch<{
      ok: boolean;
      media_selection: string;
      label: string;
      media_selection_label?: string;
      selected_asset_ids: string[];
      priority: string;
      media_priority?: string | null;
      note: string;
      effective_assets: number;
      updated_at?: string | null;
    }>(`/api/projects/${projectId}/media-selection`),
  setMediaSelection: (
    projectId: string,
    payload: { mode: string; asset_ids?: string[]; priority?: string; note?: string }
  ) =>
    apiFetch<{
      ok: boolean;
      blocked?: boolean;
      reason?: string;
      media_selection: string;
      label: string;
      media_selection_label?: string;
      selected_asset_ids: string[];
      ignored?: string[];
      priority?: string;
      effective_assets: number;
      regenerated: boolean;
    }>(`/api/projects/${projectId}/media-selection`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  /** 3.6.4 — riallinea i sottotitoli al testo ufficiale della scena. */
  alignCaptions: (projectId: string, sceneNumbers?: number[]) =>
    apiFetch<{
      ok: boolean;
      updated_scenes: number[];
      skipped: Array<{ scene_number: number; reason: string }>;
      scenes: SceneRow[];
    }>(`/api/projects/${projectId}/captions/align`, {
      method: "POST",
      body: JSON.stringify(sceneNumbers && sceneNumbers.length ? { scene_numbers: sceneNumbers } : {}),
    }),
  sceneAlternatives: (projectId: string, sceneNumber: number, limit = 8) =>
    apiFetch<{
      ok: boolean;
      scene_number: number;
      current?: SceneRow | null;
      alternatives: Array<{
        asset_id: string;
        filename: string;
        media_type: string;
        category: string;
        score: number;
        reason: string;
        is_current: boolean;
        thumb_url: string;
      }>;
    }>(`/api/projects/${projectId}/scenes/${sceneNumber}/alternatives?limit=${limit}`),
  sceneUploadMedia: async (
    projectId: string,
    sceneNumber: number,
    file: File,
    category?: string
  ) => {
    const fd = new FormData();
    fd.append("file", file);
    if (category) fd.append("category", category);
    return apiFetch<{
      ok: boolean;
      blocked?: boolean;
      reason?: string;
      asset?: MediaAsset;
      scenes?: SceneRow[];
      note?: string;
    }>(`/api/projects/${projectId}/scenes/${sceneNumber}/media`, {
      method: "POST",
      body: fd,
    });
  },

  // 3.6.6 — timeline canonica, trascrizione, verifica sincronizzazione
  getTimeline: (projectId: string) =>
    apiFetch<{
      project_id: string;
      timeline: TimelineRecord | null;
      issues: SyncIssue[];
      message?: string;
    }>(`/api/projects/${projectId}/timeline`),
  rebuildTimeline: (
    projectId: string,
    body: {
      script_text?: string;
      use_asr?: boolean;
      asr_level?: string;
      mode?: "asr" | "auto" | "script" | "estimated";
      caption_source?: "transcript" | "editorial";
    } = {}
  ) =>
    apiFetch<{
      project_id: string;
      timeline: TimelineRecord;
      issues: SyncIssue[];
      asr_note: string;
      timeline_path: string;
      mode?: string;
      caption_source?: string;
    }>(`/api/projects/${projectId}/timeline/rebuild`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patchTranscript: (
    projectId: string,
    segments: Array<{ segment_uid: string; text?: string; locked?: boolean }>
  ) =>
    apiFetch<{
      project_id: string;
      transcript: TimelineRecord["transcript"];
      caption_cues: TimelineRecord["caption_cues"];
      issues: SyncIssue[];
    }>(`/api/projects/${projectId}/transcript`, {
      method: "PATCH",
      body: JSON.stringify({ segments }),
    }),
  verifySync: (projectId: string) =>
    apiFetch<{
      project_id: string;
      ok: boolean;
      verified: boolean;
      alignment?: string;
      words?: number;
      cues?: number;
      issues: SyncIssue[];
    }>(`/api/projects/${projectId}/sync/verify`, { method: "POST" }),
  asrStatus: () =>
    apiFetch<{
      available: boolean;
      engines: string[];
      preferred: string | null;
      message: string;
      install_hint: string;
    }>("/api/asr/status"),

  // Backup
  checkIntegrity: () => apiFetch<Record<string, unknown>>("/api/backup/integrity"),
  createBackup: () => apiFetch<Record<string, unknown>>("/api/backup/create", { method: "POST" }),
  cleanCache: () => apiFetch<Record<string, unknown>>("/api/backup/clean-cache", { method: "POST" }),
  listBackups: () => apiFetch<Array<{ filename: string; size_bytes: number }>>("/api/backup/list"),
};
