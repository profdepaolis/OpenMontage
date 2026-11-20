"use client";

import React, { useEffect, useState } from "react";
import {
  Play,
  Save,
  Plus,
  Trash2,
  ArrowUp,
  ArrowDown,
  Music,
  Sparkles,
  Volume2,
  Type,
  Wand2,
} from "lucide-react";
import type {
  HyperframesConfig,
  MediaAsset,
  ProjectRecord,
  SoundAsset,
  StoryboardScene,
  VoiceOption,
} from "@/lib/types";
import { koraApi } from "@/lib/api";

interface CreativeStudioViewProps {
  project: ProjectRecord | null;
  assets: MediaAsset[];
  voices: VoiceOption[];
  onUpdated: () => void;
  onProduce: (projectId: string) => Promise<void>;
}

const MOTIONS = [
  "slow_push_in",
  "slow_pull_out",
  "pan_left",
  "pan_right",
  "tilt_up",
  "tilt_down",
  "micro_parallax",
  "static_breathe",
];

const TRANSITIONS = [
  { id: "cross_dissolve", label: "Dissolvenza morbida" },
  { id: "warm_fade", label: "Warm Fade" },
  { id: "cut_on_motion", label: "Cut on Motion" },
  { id: "fade_black", label: "Fade to Black" },
];

const SFX_OPTIONS = [
  { id: "ambiente_spiaggia", label: "Ambiente spiaggia" },
  { id: "onde", label: "Onde del mare" },
  { id: "vento", label: "Brezza / Vento" },
  { id: "tazzine", label: "Tazzine / Colazione" },
  { id: "bicchieri", label: "Calici / Aperitivo" },
  { id: "transizioni", label: "Transizione soft" },
  { id: "nessuno", label: "Nessun SFX" },
];

const CAPTION_OPTIONS = [
  { id: "serif_ivory", label: "KORA Classic Serif (Playfair Avorio)" },
  { id: "serif_minimal", label: "Editorial Minimal (Solo testo leggero)" },
  { id: "clean_sans", label: "Space Grotesk Clean (Sans moderno)" },
  { id: "hyperframes_kinetic", label: "Hyperframes · Kinetic Gold (#9A8052)" },
  { id: "hyperframes_glass", label: "Hyperframes · Mediterranean Glass" },
  { id: "hyperframes_word_highlight", label: "Hyperframes · Word Highlight (Parola per parola)" },
];

export function CreativeStudioView({
  project,
  assets,
  voices,
  onUpdated,
  onProduce,
}: CreativeStudioViewProps) {
  const [scenes, setScenes] = useState<StoryboardScene[]>([]);
  const [sounds, setSounds] = useState<SoundAsset[]>([]);
  const [voiceId, setVoiceId] = useState("brando");
  const [voicePreviewUrl, setVoicePreviewUrl] = useState<string | null>(null);
  const [captionStyle, setCaptionStyle] = useState("serif_ivory");
  const [hyperframes, setHyperframes] = useState<HyperframesConfig>({
    caption_style: "serif_ivory",
    title_card_intro: "none",
    grain_overlay: "none",
    vignette: "soft_luxury",
    letterbox: "none",
    motion_pace: "balanced",
  });
  const [hfRationale, setHfRationale] = useState<string | null>(null);
  const [musicId, setMusicId] = useState("");
  const [includeMusic, setIncludeMusic] = useState(true);
  const [musicStartSec, setMusicStartSec] = useState(0);
  const [musicVolume, setMusicVolume] = useState(0.18);
  const [voiceVolume, setVoiceVolume] = useState(1.05);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  useEffect(() => {
    koraApi
      .listSounds()
      .then((list) => setSounds(list as unknown as SoundAsset[]))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!project) return;
    setScenes(project.storyboard || []);
    setVoiceId(project.voice_id || "brando");
    const prefs = (project.preferences || {}) as Record<string, unknown>;
    const savedHf = (prefs.hyperframes || {}) as Partial<HyperframesConfig>;
    const cStyle = String(savedHf.caption_style || prefs.caption_style || "serif_ivory");
    setCaptionStyle(cStyle);
    setHyperframes({
      caption_style: cStyle,
      title_card_intro: String(savedHf.title_card_intro || "none"),
      grain_overlay: String(savedHf.grain_overlay || "none"),
      vignette: String(savedHf.vignette || "soft_luxury"),
      letterbox: String(savedHf.letterbox || "none"),
      motion_pace: String(savedHf.motion_pace || "balanced"),
    });
    const ap = (project.audio_plan || {}) as Record<string, unknown>;
    const mus = (ap.music || null) as { id?: string } | null;
    setMusicId(mus?.id || "");
    setIncludeMusic(ap.include_music !== false);
    setMusicStartSec(Number(ap.music_start_sec || 0));
    setMusicVolume(ap.music_volume !== undefined && ap.music_volume !== null ? Number(ap.music_volume) : 0.18);
    setVoiceVolume(ap.voice_volume !== undefined && ap.voice_volume !== null ? Number(ap.voice_volume) : 1.05);
  }, [project]);

  if (!project) {
    return (
      <div className="rounded-2xl border border-[#DDD4C4] bg-white p-10 text-center text-sm text-[#6C6459]">
        Seleziona o crea un progetto dalla Home per aprire lo Storyboard.
      </div>
    );
  }

  const visualAssets = assets.filter((a) => a.media_type === "image" || a.media_type === "video");
  const musicTracks = sounds.filter((s) => s.category.startsWith("musica"));
  const totalFrames = scenes.reduce((acc, s) => acc + s.duration_frames, 0);
  const fps = project.fps || 25;

  const recalculateTimeline = (list: StoryboardScene[]): StoryboardScene[] => {
    let curFrame = 0;
    return list.map((sc, idx) => {
      const df = Math.max(fps, Math.round(sc.duration_sec * fps));
      const startF = curFrame;
      const endF = curFrame + df;
      curFrame = endF;
      return {
        ...sc,
        scene_number: idx + 1,
        duration_frames: df,
        start_frame: startF,
        end_frame: endF,
        start_sec: Number((startF / fps).toFixed(2)),
        end_sec: Number((endF / fps).toFixed(2)),
        duration_sec: Number((df / fps).toFixed(2)),
      };
    });
  };

  const updateField = (idx: number, field: keyof StoryboardScene, value: string | number) => {
    setScenes((prev) => {
      const next = prev.map((s, i) => (i === idx ? { ...s, [field]: value } : s));
      return field === "duration_sec" ? recalculateTimeline(next) : next;
    });
  };

  const handleAddScene = () => {
    const firstAsset = visualAssets[scenes.length % Math.max(1, visualAssets.length)];
    const insertIdx = Math.max(0, scenes.findIndex((s) => s.is_outro));
    const newScene: StoryboardScene = {
      scene_number: scenes.length + 1,
      start_frame: 0,
      end_frame: 75,
      duration_frames: 75,
      start_sec: 0,
      end_sec: 3.0,
      duration_sec: 3.0,
      narrative_goal: "Nuova inquadratura KORA",
      spoken_text: "",
      voice_id: project.voice_id,
      selected_asset_id: firstAsset?.id || null,
      alternative_asset_id: null,
      asset_mode: firstAsset?.media_type === "video" ? "video_clip" : "animated_photo",
      camera_motion: "slow_push_in",
      transition: "cross_dissolve",
      sfx_category: "ambiente_spiaggia",
      music_cue: "musica_cinematic",
      subtitle_text: "",
      production_status: "pianificata",
      approval_status: "approvata",
      is_outro: false,
    };
    const next = [...scenes];
    if (insertIdx >= 0) {
      next.splice(insertIdx, 0, newScene);
    } else {
      next.push(newScene);
    }
    setScenes(recalculateTimeline(next));
    setSavedMsg("Nuova scena inserita nello storyboard (ricordati di salvare o renderizzare).");
  };

  const handleRemoveScene = (idx: number) => {
    if (scenes[idx]?.is_outro || scenes.length <= 2) return;
    setScenes((prev) => recalculateTimeline(prev.filter((_, i) => i !== idx)));
  };

  const handleMoveScene = (idx: number, delta: -1 | 1) => {
    const target = idx + delta;
    if (target < 0 || target >= scenes.length) return;
    if (scenes[idx]?.is_outro || scenes[target]?.is_outro) return;
    const next = [...scenes];
    const [item] = next.splice(idx, 1);
    next.splice(target, 0, item);
    setScenes(recalculateTimeline(next));
  };

  const handleSaveAll = async (autoSyncToVoice = false) => {
    setSaving(true);
    setSavedMsg(null);
    try {
      const normalized = recalculateTimeline(scenes);
      setScenes(normalized);
      await koraApi.updateStoryboard(project.id, normalized);
      const updated = await koraApi.syncProjectAudio(project.id, {
        caption_style: captionStyle,
        hyperframes: { ...hyperframes, caption_style: captionStyle },
        voice_id: voiceId,
        music_id: musicId || undefined,
        include_music: includeMusic,
        music_start_sec: musicStartSec,
        music_volume: musicVolume,
        voice_volume: voiceVolume,
        auto_sync_scenes_to_voice: autoSyncToVoice,
      });
      setScenes(updated.storyboard || normalized);
      setSavedMsg(
        autoSyncToVoice
          ? "Durata delle scene sincronizzata alle parole + voce, musica e opzioni Hyperframes salvate!"
          : "Storyboard, voce, sincronizzazione musica e opzioni Hyperframes salvati."
      );
      onUpdated();
    } finally {
      setSaving(false);
    }
  };

  const handleSuggestHyperframes = async () => {
    try {
      const spokenAll = scenes.map((s) => s.spoken_text).join(" ");
      const rec = await koraApi.suggestTheme(`${project.theme} ${project.title} ${spokenAll}`);
      setHyperframes(rec.recommended_hyperframes);
      setCaptionStyle(rec.recommended_hyperframes.caption_style);
      setHfRationale(rec.rationale);
    } catch {
      // ignore
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="font-editorial text-2xl text-[#1D1B18]">
            Creative Studio · {project.title}
          </h2>
          <p className="text-xs text-[#6C6459]">
            Preset: {project.preset} · {scenes.length} scene · {totalFrames} frame totali ({fps} fps ={" "}
            {(totalFrames / fps).toFixed(2)}s)
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={handleAddScene}
            className="flex items-center gap-1.5 rounded-xl border border-[#224C66] bg-white px-3.5 py-2 text-xs font-medium text-[#224C66] hover:bg-[#224C66]/5"
          >
            <Plus className="h-3.5 w-3.5" /> Aggiungi scena
          </button>
          <button
            type="button"
            onClick={() => handleSaveAll(true)}
            disabled={saving}
            className="flex items-center gap-1.5 rounded-xl border border-[#9A8052] bg-[#FAF7F2] px-3.5 py-2 text-xs font-medium text-[#1D1B18] hover:border-[#224C66]"
          >
            <Sparkles className="h-3.5 w-3.5 text-[#9A8052]" /> Sincronizza scene alla voce
          </button>
          <button
            type="button"
            onClick={() => handleSaveAll(false)}
            disabled={saving}
            className="flex items-center gap-1.5 rounded-xl border border-[#DDD4C4] bg-white px-4 py-2 text-xs font-medium hover:border-[#9A8052]"
          >
            <Save className="h-3.5 w-3.5" /> {saving ? "Salvataggio..." : "Salva"}
          </button>
          <button
            type="button"
            onClick={async () => {
              await handleSaveAll(false);
              await onProduce(project.id);
            }}
            className="flex items-center gap-1.5 rounded-xl bg-[#1D1B18] px-4 py-2 text-xs font-medium text-white hover:bg-[#224C66]"
          >
            <Play className="h-3.5 w-3.5" /> Renderizza Reel
          </button>
        </div>
      </div>

      {/* Sync, Voice & Full Hyperframes Bar */}
      <div className="rounded-2xl border border-[#DDD4C4] bg-white p-4 shadow-sm space-y-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-12">
          <div className="md:col-span-4 space-y-1">
            <label className="flex items-center gap-1.5 text-[11px] font-medium text-[#6C6459]">
              <Volume2 className="h-3.5 w-3.5 text-[#9A8052]" />
              Voce narrante &amp; Ascolto anteprima
            </label>
            <div className="flex items-center gap-2">
              <select
                value={voiceId}
                onChange={(e) => {
                  setVoiceId(e.target.value);
                  setVoicePreviewUrl(null);
                }}
                className="flex-1 rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-2.5 py-2 text-xs text-[#1D1B18]"
              >
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label} {v.id !== "none" && v.elevenlabs_configured ? "(ElevenLabs)" : ""}
                  </option>
                ))}
              </select>
              {voiceId !== "none" && (
                <button
                  type="button"
                  onClick={() =>
                    setVoicePreviewUrl(`/api/voices/${voiceId}/sample?engine=auto&t=${Date.now()}`)
                  }
                  className="inline-flex items-center gap-1 rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-2.5 py-2 text-[11px] font-medium hover:border-[#9A8052]"
                >
                  <Volume2 className="h-3.5 w-3.5 text-[#9A8052]" /> Ascolta
                </button>
              )}
            </div>
            {voicePreviewUrl && (
              <audio key={voicePreviewUrl} src={voicePreviewUrl} controls autoPlay className="mt-1 h-8 w-full" />
            )}
          </div>

          <div className="md:col-span-4 space-y-1">
            <label className="flex items-center gap-1.5 text-[11px] font-medium text-[#6C6459]">
              <Music className="h-3.5 w-3.5 text-[#224C66]" />
              Musica di sottofondo (CC0 / Libreria KORA)
            </label>
            <div className="flex items-center gap-2">
              <select
                value={musicId}
                disabled={!includeMusic}
                onChange={(e) => setMusicId(e.target.value)}
                className="flex-1 rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-2.5 py-2 text-xs text-[#1D1B18]"
              >
                <option value="">Automatica (dal preset)</option>
                {musicTracks.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name} ({Math.round(m.duration_sec)}s)
                  </option>
                ))}
              </select>
              <label className="flex items-center gap-1 text-[11px] text-[#1D1B18] whitespace-nowrap">
                <input
                  type="checkbox"
                  checked={includeMusic}
                  onChange={(e) => setIncludeMusic(e.target.checked)}
                />
                Attiva
              </label>
            </div>
          </div>

          <div className="md:col-span-4 grid grid-cols-3 gap-2">
            <label className="block text-[11px] font-medium text-[#6C6459]">
              Start musica (s)
              <input
                type="number"
                min="0"
                step="1"
                value={musicStartSec}
                onChange={(e) => setMusicStartSec(Math.max(0, Number(e.target.value)))}
                className="mt-1 w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-2 py-1.5 text-xs text-[#1D1B18]"
              />
            </label>
            <label className="block text-[11px] font-medium text-[#6C6459]">
              Musica ({Math.round(musicVolume * 100)}%)
              <input
                type="range"
                min="0.05"
                max="0.6"
                step="0.02"
                value={musicVolume}
                onChange={(e) => setMusicVolume(Number(e.target.value))}
                className="mt-2 w-full"
              />
            </label>
            <label className="block text-[11px] font-medium text-[#6C6459]">
              Voce ({Math.round(voiceVolume * 100)}%)
              <input
                type="range"
                min="0.6"
                max="1.4"
                step="0.05"
                value={voiceVolume}
                onChange={(e) => setVoiceVolume(Number(e.target.value))}
                className="mt-2 w-full"
              />
            </label>
          </div>
        </div>

        <div className="border-t border-[#EDE6D9] pt-3 space-y-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 text-xs font-medium text-[#1D1B18]">
              <Wand2 className="h-3.5 w-3.5 text-[#9A8052]" />
              Opzioni complete Hyperframes (Caption · Title Card · Grana · Vignetta · Cornice · Ritmo)
            </div>
            <button
              type="button"
              onClick={handleSuggestHyperframes}
              className="inline-flex items-center gap-1 rounded-lg bg-[#9A8052] px-3 py-1 text-[11px] font-medium text-white hover:bg-[#7d663e]"
            >
              <Sparkles className="h-3 w-3" /> Suggerisci in base a tema e testo
            </button>
          </div>
          {hfRationale && (
            <div className="rounded-xl bg-[#FAF7F2] px-3 py-2 text-[11px] text-[#4A443C]">
              <strong>Suggerimento AI KORA:</strong> {hfRationale}
            </div>
          )}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 lg:grid-cols-6 text-xs">
            <label className="space-y-1">
              <span className="flex items-center gap-1 text-[11px] text-[#6C6459]">
                <Type className="h-3 w-3" /> Caption
              </span>
              <select
                value={captionStyle}
                onChange={(e) => {
                  setCaptionStyle(e.target.value);
                  setHyperframes((h) => ({ ...h, caption_style: e.target.value }));
                }}
                className="w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-2 py-1.5 text-xs"
              >
                {CAPTION_OPTIONS.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-[11px] text-[#6C6459]">Title Card Scena 1</span>
              <select
                value={hyperframes.title_card_intro}
                onChange={(e) => setHyperframes((h) => ({ ...h, title_card_intro: e.target.value }))}
                className="w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-2 py-1.5 text-xs"
              >
                <option value="none">Nessuna</option>
                <option value="editorial_serif">Editorial Serif</option>
                <option value="hyperframes_gold_line">Hyperframes Gold Line</option>
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-[11px] text-[#6C6459]">Grana (grain-overlay)</span>
              <select
                value={hyperframes.grain_overlay}
                onChange={(e) => setHyperframes((h) => ({ ...h, grain_overlay: e.target.value }))}
                className="w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-2 py-1.5 text-xs"
              >
                <option value="none">Pulito</option>
                <option value="subtle_35mm">Grana 35mm</option>
                <option value="warm_kodak">Kodak Calda</option>
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-[11px] text-[#6C6459]">Vignettatura</span>
              <select
                value={hyperframes.vignette}
                onChange={(e) => setHyperframes((h) => ({ ...h, vignette: e.target.value }))}
                className="w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-2 py-1.5 text-xs"
              >
                <option value="none">Nessuna</option>
                <option value="soft_luxury">Luxury Morbida</option>
                <option value="deep_cinematic">Cinema Intensa</option>
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-[11px] text-[#6C6459]">Cornice / Letterbox</span>
              <select
                value={hyperframes.letterbox}
                onChange={(e) => setHyperframes((h) => ({ ...h, letterbox: e.target.value }))}
                className="w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-2 py-1.5 text-xs"
              >
                <option value="none">Pieno 9:16</option>
                <option value="cinema_frame">Cinema Frame</option>
                <option value="editorial_border">Bordo Avorio</option>
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-[11px] text-[#6C6459]">Ritmo Motion</span>
              <select
                value={hyperframes.motion_pace}
                onChange={(e) => setHyperframes((h) => ({ ...h, motion_pace: e.target.value }))}
                className="w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-2 py-1.5 text-xs"
              >
                <option value="slow_contemplative">Lento</option>
                <option value="balanced">Equilibrato</option>
                <option value="dynamic_experience">Dinamico</option>
              </select>
            </label>
          </div>
        </div>
      </div>

      {savedMsg && (
        <div className="rounded-xl border border-[#DDD4C4] bg-[#EFE8DB] px-4 py-2 text-xs text-[#1D1B18]">
          {savedMsg}
        </div>
      )}

      {/* Scenes */}
      <div className="space-y-3">
        {scenes.map((sc, idx) => (
          <div
            key={`${sc.scene_number}-${idx}`}
            className="grid grid-cols-1 gap-4 rounded-2xl border border-[#DDD4C4] bg-white p-4 md:grid-cols-12"
          >
            <div className="md:col-span-2 flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between gap-1">
                  <span className="text-xs font-semibold uppercase tracking-wider text-[#9A8052]">
                    Scena {sc.scene_number} {sc.is_outro ? "· Outro" : ""}
                  </span>
                  {!sc.is_outro && (
                    <div className="flex items-center gap-0.5">
                      <button
                        type="button"
                        title="Sposta su"
                        onClick={() => handleMoveScene(idx, -1)}
                        className="rounded p-1 text-[#6C6459] hover:bg-[#FAF7F2]"
                      >
                        <ArrowUp className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        title="Sposta giù"
                        onClick={() => handleMoveScene(idx, 1)}
                        className="rounded p-1 text-[#6C6459] hover:bg-[#FAF7F2]"
                      >
                        <ArrowDown className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        title="Elimina scena"
                        onClick={() => handleRemoveScene(idx)}
                        className="rounded p-1 text-red-700 hover:bg-red-50"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  )}
                </div>
                <div className="mt-1 text-xs text-[#6C6459]">
                  Frame {sc.start_frame}–{sc.end_frame} ({sc.duration_frames} f)
                </div>
                <div className="text-[11px] text-[#6C6459]">
                  {sc.start_sec.toFixed(1)}s → {sc.end_sec.toFixed(1)}s ({sc.duration_sec.toFixed(2)}s)
                </div>
              </div>
              {sc.selected_asset_id && (
                <img
                  src={`/api/vault/${sc.selected_asset_id}/thumb`}
                  alt={sc.narrative_goal}
                  className="mt-2 h-24 w-full rounded-xl object-cover bg-[#EFE8DB]"
                />
              )}
            </div>

            <div className="md:col-span-5 space-y-2">
              <label className="block text-[11px] font-medium text-[#6C6459]">
                Obiettivo narrativo
                <input
                  type="text"
                  value={sc.narrative_goal}
                  onChange={(e) => updateField(idx, "narrative_goal", e.target.value)}
                  className="mt-1 w-full rounded-lg border border-[#DDD4C4] px-3 py-1.5 text-xs text-[#1D1B18]"
                />
              </label>
              <label className="block text-[11px] font-medium text-[#6C6459]">
                Testo parlato (Voiceover — sincronizzato a questa scena)
                <input
                  type="text"
                  value={sc.spoken_text}
                  onChange={(e) => updateField(idx, "spoken_text", e.target.value)}
                  className="mt-1 w-full rounded-lg border border-[#DDD4C4] px-3 py-1.5 text-xs text-[#1D1B18]"
                />
              </label>
              <label className="block text-[11px] font-medium text-[#6C6459]">
                Caption / Sottotitolo a schermo
                <input
                  type="text"
                  value={sc.subtitle_text}
                  onChange={(e) => updateField(idx, "subtitle_text", e.target.value)}
                  className="mt-1 w-full rounded-lg border border-[#DDD4C4] px-3 py-1.5 text-xs text-[#1D1B18]"
                />
              </label>
            </div>

            <div className="md:col-span-5 space-y-2">
              <label className="block text-[11px] font-medium text-[#6C6459]">
                Asset Media Vault
                <select
                  value={sc.selected_asset_id || ""}
                  disabled={sc.is_outro}
                  onChange={(e) => updateField(idx, "selected_asset_id", e.target.value)}
                  className="mt-1 w-full rounded-lg border border-[#DDD4C4] bg-white px-3 py-1.5 text-xs text-[#1D1B18]"
                >
                  {visualAssets.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.filename} ({a.category})
                    </option>
                  ))}
                </select>
              </label>
              <div className="grid grid-cols-2 gap-2">
                <label className="block text-[11px] font-medium text-[#6C6459]">
                  Camera Motion
                  <select
                    value={sc.camera_motion}
                    onChange={(e) => updateField(idx, "camera_motion", e.target.value)}
                    className="mt-1 w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs text-[#1D1B18]"
                  >
                    {MOTIONS.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block text-[11px] font-medium text-[#6C6459]">
                  Transizione
                  <select
                    value={sc.transition}
                    onChange={(e) => updateField(idx, "transition", e.target.value)}
                    className="mt-1 w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs text-[#1D1B18]"
                  >
                    {TRANSITIONS.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block text-[11px] font-medium text-[#6C6459]">
                  Durata (secondi)
                  <input
                    type="number"
                    step="0.5"
                    min="1.5"
                    max="12"
                    value={sc.duration_sec}
                    onChange={(e) => updateField(idx, "duration_sec", Number(e.target.value))}
                    className="mt-1 w-full rounded-lg border border-[#DDD4C4] px-2.5 py-1.5 text-xs text-[#1D1B18]"
                  />
                </label>
                <label className="block text-[11px] font-medium text-[#6C6459]">
                  SFX ambientale
                  <select
                    value={sc.sfx_category || "ambiente_spiaggia"}
                    onChange={(e) => updateField(idx, "sfx_category", e.target.value)}
                    className="mt-1 w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs text-[#1D1B18]"
                  >
                    {SFX_OPTIONS.map((sfx) => (
                      <option key={sfx.id} value={sfx.id}>
                        {sfx.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
