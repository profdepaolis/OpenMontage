import type {
  AIActivityCall,
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
  VoiceOption,
} from "./types";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = `Errore HTTP ${res.status}`;
    try {
      const err = await res.json();
      detail = err.detail || detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const koraApi = {
  health: () => apiFetch<Record<string, unknown>>("/api/health"),

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
  suggestThemes: (payload: Partial<CreativeRequestPayload> = {}) =>
    apiFetch<{
      concepts: CreativeConcept[];
      generation_engine?: string;
      fallback_reason?: string | null;
    }>("/api/creative/suggest", {
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

  // Backup
  checkIntegrity: () => apiFetch<Record<string, unknown>>("/api/backup/integrity"),
  createBackup: () => apiFetch<Record<string, unknown>>("/api/backup/create", { method: "POST" }),
  cleanCache: () => apiFetch<Record<string, unknown>>("/api/backup/clean-cache", { method: "POST" }),
  listBackups: () => apiFetch<Array<{ filename: string; size_bytes: number }>>("/api/backup/list"),
};
