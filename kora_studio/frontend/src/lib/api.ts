import type {
  BudgetSettings,
  CreativeConcept,
  JobRecord,
  MediaAsset,
  ProjectRecord,
  ProviderRecord,
  StoryboardScene,
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
    apiFetch<{ concepts: CreativeConcept[] }>("/api/creative/surprise", {
      method: "POST",
      body: JSON.stringify({ theme: "", duration_sec: durationSec }),
    }),
  fromTheme: (theme: string, durationSec = 20, assetIds: string[] = []) =>
    apiFetch<{ concepts: CreativeConcept[] }>("/api/creative/from-theme", {
      method: "POST",
      body: JSON.stringify({ theme, duration_sec: durationSec, asset_ids: assetIds }),
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
    apiFetch<Array<{ voice_id: string; name: string; category: string }>>(
      "/api/providers/elevenlabs_api/voices"
    ),
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

  // Backup
  checkIntegrity: () => apiFetch<Record<string, unknown>>("/api/backup/integrity"),
  createBackup: () => apiFetch<Record<string, unknown>>("/api/backup/create", { method: "POST" }),
  cleanCache: () => apiFetch<Record<string, unknown>>("/api/backup/clean-cache", { method: "POST" }),
  listBackups: () => apiFetch<Array<{ filename: string; size_bytes: number }>>("/api/backup/list"),
};
