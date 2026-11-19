"use client";

import React, { useEffect, useState } from "react";
import { Play, Save } from "lucide-react";
import type { MediaAsset, ProjectRecord, StoryboardScene } from "@/lib/types";
import { koraApi } from "@/lib/api";

interface CreativeStudioViewProps {
  project: ProjectRecord | null;
  assets: MediaAsset[];
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

export function CreativeStudioView({
  project,
  assets,
  onUpdated,
  onProduce,
}: CreativeStudioViewProps) {
  const [scenes, setScenes] = useState<StoryboardScene[]>([]);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  useEffect(() => {
    setScenes(project?.storyboard || []);
  }, [project]);

  if (!project) {
    return (
      <div className="rounded-2xl border border-[#DDD4C4] bg-white p-10 text-center text-sm text-[#6C6459]">
        Seleziona o crea un progetto dalla Home per aprire lo Storyboard.
      </div>
    );
  }

  const visualAssets = assets.filter((a) => a.media_type === "image" || a.media_type === "video");
  const totalFrames = scenes.reduce((acc, s) => acc + s.duration_frames, 0);
  const fps = project.fps || 25;

  const recalculateTimeline = (list: StoryboardScene[]): StoryboardScene[] => {
    let curFrame = 0;
    return list.map((sc) => {
      const df = Math.max(fps, Math.round(sc.duration_sec * fps));
      const startF = curFrame;
      const endF = curFrame + df;
      curFrame = endF;
      return {
        ...sc,
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

  const handleSave = async () => {
    setSaving(true);
    setSavedMsg(null);
    try {
      const normalized = recalculateTimeline(scenes);
      setScenes(normalized);
      await koraApi.updateStoryboard(project.id, normalized);
      setSavedMsg("Storyboard salvato con nuova versione.");
      onUpdated();
    } finally {
      setSaving(false);
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
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-1.5 rounded-xl border border-[#DDD4C4] bg-white px-4 py-2 text-xs font-medium hover:border-[#9A8052]"
          >
            <Save className="h-3.5 w-3.5" /> {saving ? "Salvataggio..." : "Salva Storyboard"}
          </button>
          <button
            type="button"
            onClick={async () => {
              await handleSave();
              await onProduce(project.id);
            }}
            className="flex items-center gap-1.5 rounded-xl bg-[#1D1B18] px-4 py-2 text-xs font-medium text-white hover:bg-[#224C66]"
          >
            <Play className="h-3.5 w-3.5" /> Renderizza Reel
          </button>
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
            key={sc.scene_number}
            className="grid grid-cols-1 gap-4 rounded-2xl border border-[#DDD4C4] bg-white p-4 md:grid-cols-12"
          >
            <div className="md:col-span-2 flex flex-col justify-between">
              <div>
                <span className="text-xs font-semibold uppercase tracking-wider text-[#9A8052]">
                  Scena {sc.scene_number} {sc.is_outro ? "· Outro" : ""}
                </span>
                <div className="mt-1 text-xs text-[#6C6459]">
                  Frame {sc.start_frame}–{sc.end_frame} ({sc.duration_frames} f)
                </div>
                <div className="text-[11px] text-[#6C6459]">{sc.duration_sec.toFixed(2)} s</div>
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
                Testo parlato (Voiceover)
                <input
                  type="text"
                  value={sc.spoken_text}
                  onChange={(e) => updateField(idx, "spoken_text", e.target.value)}
                  className="mt-1 w-full rounded-lg border border-[#DDD4C4] px-3 py-1.5 text-xs text-[#1D1B18]"
                />
              </label>
              <label className="block text-[11px] font-medium text-[#6C6459]">
                Sottotitolo a schermo
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
                    max="10"
                    value={sc.duration_sec}
                    onChange={(e) => updateField(idx, "duration_sec", Number(e.target.value))}
                    className="mt-1 w-full rounded-lg border border-[#DDD4C4] px-2.5 py-1.5 text-xs text-[#1D1B18]"
                  />
                </label>
                <label className="block text-[11px] font-medium text-[#6C6459]">
                  SFX
                  <input
                    type="text"
                    value={sc.sfx_category || ""}
                    onChange={(e) => updateField(idx, "sfx_category", e.target.value)}
                    className="mt-1 w-full rounded-lg border border-[#DDD4C4] px-2.5 py-1.5 text-xs text-[#1D1B18]"
                  />
                </label>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
