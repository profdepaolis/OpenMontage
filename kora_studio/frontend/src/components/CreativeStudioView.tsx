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

export function CreativeStudioView({
  project,
  assets,
  onUpdated,
  onProduce,
}: CreativeStudioViewProps) {
  const [scenes, setScenes] = useState<StoryboardScene[]>([]);
  const [saving, setSaving] = useState(false);

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

  const updateField = (idx: number, field: keyof StoryboardScene, value: string | number) => {
    setScenes((prev) =>
      prev.map((s, i) => (i === idx ? { ...s, [field]: value } : s))
    );
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await koraApi.updateStoryboard(project.id, scenes);
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
            Preset: {project.preset} · {scenes.length} scene · {totalFrames} frame totali ({project.fps} fps = {(totalFrames / project.fps).toFixed(2)}s)
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
                Sottotitolo / Testo scena
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
