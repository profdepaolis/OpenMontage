"use client";

import React, { useCallback, useState } from "react";
import {
  Check,
  Clapperboard,
  Crosshair,
  Film,
  ImagePlus,
  Loader2,
  Lock,
  LockOpen,
  Play,
  Shuffle,
  Upload,
} from "lucide-react";
import { koraApi, type SceneRow } from "@/lib/api";

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

const FOCUS_POINTS: Array<{ id: string; x: number; y: number; label: string }> = [
  { id: "tl", x: 0.25, y: 0.25, label: "in alto a sinistra" },
  { id: "tc", x: 0.5, y: 0.22, label: "in alto" },
  { id: "tr", x: 0.75, y: 0.25, label: "in alto a destra" },
  { id: "ml", x: 0.25, y: 0.5, label: "a sinistra" },
  { id: "mc", x: 0.5, y: 0.5, label: "al centro" },
  { id: "mr", x: 0.75, y: 0.5, label: "a destra" },
  { id: "bl", x: 0.25, y: 0.75, label: "in basso a sinistra" },
  { id: "bc", x: 0.5, y: 0.78, label: "in basso" },
  { id: "br", x: 0.75, y: 0.75, label: "in basso a destra" },
];

type Alternative = {
  asset_id: string;
  filename: string;
  media_type: string;
  category: string;
  score: number;
  reason: string;
  is_current: boolean;
  thumb_url: string;
};

type Props = {
  projectId: string;
  scene: SceneRow;
  onScenes: (scenes: SceneRow[]) => void;
  onNotify: (kind: "ok" | "err" | "info", text: string) => void;
};

/**
 * Materiali e regia della singola scena: sostituzione, alternative autentiche,
 * caricamento inline, punto focale, movimento di camera, anteprima e blocco.
 * Ogni modifica tocca solo questa scena (la voce e le altre scene non si rigenerano).
 */
export function SceneMaterialsPanel({ projectId, scene, onScenes, onNotify }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string>("");
  const [alternatives, setAlternatives] = useState<Alternative[] | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const locks = scene.locks || {};
  const locked = Boolean(locks.scene);

  const patch = useCallback(
    async (body: Record<string, unknown>, label: string) => {
      setBusy(label);
      try {
        const res = await koraApi.updateScene(projectId, scene.scene_number, body);
        if (res.scenes) onScenes(res.scenes);
        onNotify("ok", "Scena aggiornata. Le altre scene non sono state toccate.");
      } catch (err) {
        onNotify("err", `Modifica non riuscita: ${(err as Error).message}`);
      } finally {
        setBusy("");
      }
    },
    [projectId, scene.scene_number, onScenes, onNotify]
  );

  const loadAlternatives = async () => {
    setBusy("alt");
    try {
      const res = await koraApi.sceneAlternatives(projectId, scene.scene_number, 8);
      setAlternatives(res.alternatives || []);
      if (!res.alternatives?.length) onNotify("info", "Nessuna alternativa autentica trovata in archivio.");
    } catch (err) {
      onNotify("err", `Alternative non disponibili: ${(err as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const useAlternative = async (alt: Alternative) => {
    await patch({ asset_id: alt.asset_id }, "use-alt");
    setAlternatives((prev) => prev?.map((a) => ({ ...a, is_current: a.asset_id === alt.asset_id })) || null);
  };

  const upload = async (file: File) => {
    setBusy("upload");
    try {
      const res = await koraApi.sceneUploadMedia(
        projectId,
        scene.scene_number,
        file,
        file.type.startsWith("video") ? "video" : "photo"
      );
      if (res.blocked) {
        onNotify("err", res.reason || "Caricamento bloccato dai blocchi della scena.");
        return;
      }
      if (res.scenes) onScenes(res.scenes);
      onNotify("ok", `「${file.name}」 caricato in archivio e usato in questa scena.`);
    } catch (err) {
      onNotify("err", `Caricamento non riuscito: ${(err as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const preview = async () => {
    setBusy("preview");
    try {
      const res = await koraApi.scenePreview(projectId, scene.scene_number);
      setPreviewUrl(`${res.url}?r=${encodeURIComponent(res.revision)}`);
      onNotify("info", `Anteprima scena ${scene.scene_number}: ${res.resolution} · ${res.fps} fps`);
    } catch (err) {
      onNotify("err", `Anteprima non disponibile: ${(err as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  const toggleLock = async () => {
    setBusy("lock");
    try {
      const next = { ...locks };
      if (locked) {
        delete next.scene;
      } else {
        next.scene = true;
      }
      const res = await koraApi.sceneLocks(projectId, scene.scene_number, next);
      if (res.scenes) onScenes(res.scenes);
      onNotify("ok", locked ? "Scena sbloccata." : "Scena bloccata (approvata).");
    } catch (err) {
      onNotify("err", `Blocco non riuscito: ${(err as Error).message}`);
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="mt-2 rounded-xl border border-[#EDE6D9] bg-white/70 px-2.5 py-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-center gap-1 rounded-lg border border-[#DDD4C4] px-2 py-1 text-[10px] text-[#4A443C] hover:border-[#9A8052]"
        >
          <Clapperboard className="h-3 w-3" /> {open ? "Chiudi materiali" : "Materiali e regia"}
        </button>

        {scene.asset_id ? (
          <span className="inline-flex items-center gap-1 rounded-lg bg-[#FAF7F2] px-1.5 py-0.5 text-[10px] text-[#6C6459]">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`/api/vault/${scene.asset_id}/thumb`}
              alt="materiale attuale"
              className="h-6 w-6 rounded object-cover"
            />
            {scene.asset_id}
          </span>
        ) : (
          <span className="rounded-lg bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-800">nessun materiale</span>
        )}

        <label className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-[#DDD4C4] px-2 py-1 text-[10px] text-[#224C66] hover:border-[#224C66]">
          {busy === "upload" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Upload className="h-3 w-3" />}
          Carica nuovo
          <input
            type="file"
            accept="image/*,video/*"
            className="hidden"
            disabled={busy === "upload"}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void upload(f);
              e.currentTarget.value = "";
            }}
          />
        </label>

        <button
          type="button"
          onClick={() => void loadAlternatives()}
          disabled={busy === "alt"}
          className="inline-flex items-center gap-1 rounded-lg border border-[#DDD4C4] px-2 py-1 text-[10px] text-[#4A443C] disabled:opacity-50"
        >
          {busy === "alt" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Shuffle className="h-3 w-3" />}
          Alternative
        </button>

        <button
          type="button"
          onClick={() => void preview()}
          disabled={busy === "preview"}
          className="inline-flex items-center gap-1 rounded-lg border border-[#DDD4C4] px-2 py-1 text-[10px] text-[#4A443C] disabled:opacity-50"
        >
          {busy === "preview" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
          Anteprima scena
        </button>

        <button
          type="button"
          onClick={() => void toggleLock()}
          disabled={busy === "lock"}
          className={`inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[10px] disabled:opacity-50 ${
            locked
              ? "border-emerald-300 bg-emerald-50 text-emerald-800"
              : "border-[#DDD4C4] text-[#4A443C]"
          }`}
        >
          {locked ? <Lock className="h-3 w-3" /> : <LockOpen className="h-3 w-3" />}
          {locked ? "Bloccata" : "Blocca approvata"}
        </button>
      </div>

      {open && (
        <div className="mt-2 space-y-2 border-t border-[#EDE6D9] pt-2">
          <div className="flex flex-wrap items-end gap-2">
            <label className="min-w-[150px] text-[10px] text-[#6C6459]">
              Movimento di camera
              <select
                value={scene.camera_motion || MOTIONS[0]}
                onChange={(e) => void patch({ camera_motion: e.target.value }, "motion")}
                className="mt-1 block w-full rounded-lg border border-[#DDD4C4] bg-white px-2 py-1.5 text-[11px]"
              >
                {MOTIONS.map((m) => (
                  <option key={m} value={m}>
                    {m.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </label>
            <label className="w-24 text-[10px] text-[#6C6459]">
              Intensità
              <input
                type="number"
                min={0.2}
                max={2}
                step={0.1}
                defaultValue={scene.motion_intensity ?? 1}
                onBlur={(e) => {
                  const value = Number(e.target.value);
                  if (!Number.isNaN(value) && value !== scene.motion_intensity) {
                    void patch({ motion_intensity: value }, "intensity");
                  }
                }}
                className="mt-1 block w-full rounded-lg border border-[#DDD4C4] bg-white px-2 py-1.5 text-[11px]"
              />
            </label>
          </div>

          <div>
            <div className="flex items-center gap-1 text-[10px] text-[#6C6459]">
              <Crosshair className="h-3 w-3" /> Punto focale del ritaglio
            </div>
            <div className="mt-1 grid w-[108px] grid-cols-3 gap-0.5">
              {FOCUS_POINTS.map((p) => {
                const current = scene.crop_focus;
                const active =
                  !!current &&
                  Math.abs(current.x - p.x) < 0.02 &&
                  Math.abs(current.y - p.y) < 0.02;
                return (
                  <button
                    key={p.id}
                    type="button"
                    title={p.label}
                    onClick={() =>
                      void patch(
                        { crop_focus: { x: p.x, y: p.y, zoom: scene.crop_focus?.zoom ?? 1 } },
                        "focus"
                      )
                    }
                    className={`h-8 rounded border text-[10px] ${
                      active
                        ? "border-[#224C66] bg-[#224C66] text-white"
                        : "border-[#DDD4C4] bg-[#FAF7F2] hover:border-[#9A8052]"
                    }`}
                  >
                    {active ? <Check className="mx-auto h-3 w-3" /> : ""}
                  </button>
                );
              })}
            </div>
          </div>

          {alternatives && (
            <div>
              <div className="text-[10px] text-[#6C6459]">Alternative autentiche dall'archivio</div>
              <div className="mt-1 grid grid-cols-4 gap-1.5">
                {alternatives.map((alt) => (
                  <button
                    key={alt.asset_id}
                    type="button"
                    onClick={() => void useAlternative(alt)}
                    title={`${alt.filename} — ${alt.reason}`}
                    className={`overflow-hidden rounded-lg border text-left ${
                      alt.is_current ? "border-[#224C66] ring-2 ring-[#224C66]/40" : "border-[#EDE6D9]"
                    }`}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={alt.thumb_url} alt={alt.filename} loading="lazy" className="h-14 w-full object-cover" />
                    <span className="block truncate bg-white px-1 py-0.5 text-[9px] text-[#4A443C]">
                      {alt.media_type === "video" ? "▶ " : ""}
                      {alt.filename}
                    </span>
                  </button>
                ))}
                {!alternatives.length && (
                  <p className="col-span-4 text-[10px] text-[#6C6459]">Nessuna alternativa disponibile.</p>
                )}
              </div>
            </div>
          )}

          {previewUrl && (
            <div className="rounded-lg bg-black p-1">
              <video src={previewUrl} controls autoPlay loop className="max-h-64 w-full rounded" />
            </div>
          )}

          <p className="flex items-center gap-1 text-[10px] text-[#9A8052]">
            <Film className="h-3 w-3" />
            Sostituire il materiale non rigenera voce, copione o le altre scene.
            <ImagePlus className="ml-2 h-3 w-3" /> Il caricamento inline aggiunge l'originale al Vault.
          </p>
        </div>
      )}
    </div>
  );
}

export default SceneMaterialsPanel;
