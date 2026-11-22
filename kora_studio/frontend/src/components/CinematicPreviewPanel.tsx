"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Clapperboard, Film, Loader2, RefreshCw, Send, Sparkles } from "lucide-react";
import type { PreviewManifest, PreviewSceneCard, ProjectRecord } from "@/lib/types";
import { koraApi } from "@/lib/api";

interface Props {
  project: ProjectRecord;
  onProduce: () => void;
}

function stateBadge(state: string) {
  const map: Record<string, string> = {
    approvata: "border-emerald-200 bg-emerald-50 text-emerald-700",
    modificata: "border-amber-200 bg-amber-50 text-amber-700",
    da_riverificare: "border-[#E2D9C8] bg-[#FAF7F2] text-[#6C6459]",
  };
  return map[state] || map.da_riverificare;
}

export function CinematicPreviewPanel({ project, onProduce }: Props) {
  const [manifest, setManifest] = useState<PreviewManifest | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [sceneBusy, setSceneBusy] = useState<number | null>(null);
  const [v, setV] = useState(0);
  const [checks, setChecks] = useState<Array<{ id: string; label: string; ok: boolean; detail: string }>>([]);

  const loadExisting = useCallback(async () => {
    try {
      const m = await koraApi.previewManifest(project.id);
      setManifest(m);
    } catch {
      setManifest(null);
    }
  }, [project.id]);

  useEffect(() => {
    void loadExisting();
  }, [loadExisting, project.updated_at]);

  const generate = async (force = false) => {
    setBusy(true);
    setMsg(null);
    try {
      const m = await koraApi.createPreview(project.id, force);
      setManifest(m);
      setV((x) => x + 1);
      setMsg(
        m.cache_hit
          ? "Anteprima riusata dalla cache: storyboard e audio non sono cambiati."
          : `Anteprima pronta: ${m.cards.length} scene, ${m.duration_sec.toFixed(2)} s, ${m.resolution}. ${m.audio_note}`
      );
    } catch (e) {
      setMsg((e as Error).message);
      try {
        const ready = await koraApi.projectReadiness(project.id);
        setChecks(ready.checks.filter((c) => !c.ok));
      } catch {
        setChecks([]);
      }
    } finally {
      setBusy(false);
    }
  };

  const revise = async (scene: PreviewSceneCard) => {
    const instruction = (drafts[scene.scene_number] || "").trim();
    if (!instruction) return;
    setSceneBusy(scene.scene_number);
    setMsg(null);
    try {
      const res = await koraApi.reviseScene(project.id, scene.scene_number, instruction);
      if (!res.applied) {
        setMsg(res.proposal.note);
      } else {
        setMsg(
          `Scena ${scene.scene_number} aggiornata (${res.proposal.source}). Scene approvate preservate: ${
            res.preserved_approved_scenes.join(", ") || "nessuna"
          }.`
        );
        setDrafts((d) => ({ ...d, [scene.scene_number]: "" }));
      }
      if (res.preview) {
        setManifest(res.preview);
        setV((x) => x + 1);
      }
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setSceneBusy(null);
    }
  };

  const approve = async (scene: PreviewSceneCard) => {
    setSceneBusy(scene.scene_number);
    try {
      await koraApi.approveScene(project.id, scene.scene_number, true);
      await loadExisting();
      setMsg(`Scena ${scene.scene_number} approvata.`);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setSceneBusy(null);
    }
  };

  return (
    <section className="rounded-2xl border border-[#DDD4C4] bg-white p-6 lg:col-span-12">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-editorial text-lg text-[#1D1B18]">
            <Film className="h-4 w-4 text-[#9A8052]" /> Anteprima cinematografica
          </h3>
          <p className="mt-1 max-w-2xl text-[11px] leading-relaxed text-[#6C6459]">
            Anteprima leggera (540×960) scena per scena: fotografia originale, inquadratura derivata,
            movimento di camera, voce, sottotitoli, durata, transizione e sound design. Le modifiche
            conversazionali toccano <strong>solo</strong> la scena indicata; le scene approvate restano intatte.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void generate(false)}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-xl border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-2 text-xs font-medium text-[#1D1B18] hover:border-[#9A8052] disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5 text-[#9A8052]" />}
            {manifest ? "Aggiorna anteprima" : "Genera anteprima"}
          </button>
          <button
            type="button"
            onClick={() => void generate(true)}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-xl border border-[#DDD4C4] bg-white px-3 py-2 text-xs font-medium text-[#4A443C] hover:border-[#9A8052] disabled:opacity-50"
          >
            <RefreshCw className="h-3.5 w-3.5" /> Rigenera da zero
          </button>
          <button
            type="button"
            onClick={onProduce}
            className="inline-flex items-center gap-1.5 rounded-xl bg-[#1D1B18] px-3 py-2 text-xs font-medium text-white hover:bg-[#224C66]"
          >
            <Clapperboard className="h-3.5 w-3.5" /> Realizza Reel (1080×1920)
          </button>
        </div>
      </div>

      {msg && (
        <p className="mt-3 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] p-3 text-[11px] text-[#4A443C]">{msg}</p>
      )}

      {!manifest ? (
        <p className="mt-4 rounded-xl border border-dashed border-[#E2D9C8] bg-[#FAF7F2] p-6 text-center text-xs text-[#6C6459]">
          Nessuna anteprima ancora generata per questo progetto: clicca «Genera anteprima» per rivedere le
          scene prima del render finale.
        </p>
      ) : (
        <div className="mt-4 grid grid-cols-1 gap-5 lg:grid-cols-12">
          <div className="lg:col-span-4">
            <video
              key={`${project.id}-${v}`}
              src={`${koraApi.previewVideoUrl(project.id)}?v=${v}`}
              controls
              playsInline
              className="aspect-[9/16] w-full max-w-[320px] rounded-xl bg-black shadow-lg"
            />
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-[#6C6459]">
              <span>
                {manifest.total_frames} frame · {manifest.duration_sec.toFixed(2)} s
              </span>
              <span>{manifest.resolution}</span>
              <span>{manifest.fps} fps</span>
              <span className={manifest.audio_included ? "text-emerald-700" : "text-amber-700"}>
                {manifest.audio_included ? "voce + sound design" : "audio non ancora generato"}
              </span>
            </div>
          </div>

          <div className="lg:col-span-8">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {manifest.cards.map((card) => (
                <div key={card.scene_number} className="rounded-xl border border-[#EDE6D9] bg-[#FAF7F2] p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] font-semibold text-[#1D1B18]">
                      Scena {card.scene_number}
                      {card.is_outro && " · outro"}
                    </span>
                    <span
                      className={`rounded-full border px-2 py-0.5 text-[9px] uppercase tracking-wide ${stateBadge(
                        card.approval_status
                      )}`}
                    >
                      {card.approval_status}
                    </span>
                  </div>
                  <div className="mt-2 flex gap-2">
                    <div className="w-1/2">
                      {card.thumbnail_source ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={koraApi.previewThumbUrl(project.id, card.scene_number, "source")}
                          alt={`foto originale scena ${card.scene_number}`}
                          className="aspect-[9/16] w-full rounded-lg object-cover"
                        />
                      ) : (
                        <div className="flex aspect-[9/16] w-full items-center justify-center rounded-lg bg-[#EFE8DB] text-[9px] text-[#6C6459]">
                          foto n/d
                        </div>
                      )}
                      <div className="mt-1 text-center text-[9px] uppercase tracking-wide text-[#9A8052]">originale</div>
                    </div>
                    <div className="w-1/2">
                      {card.thumbnail_derived ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={koraApi.previewThumbUrl(project.id, card.scene_number, "derived")}
                          alt={`inquadratura derivata scena ${card.scene_number}`}
                          className="aspect-[9/16] w-full rounded-lg object-cover"
                        />
                      ) : (
                        <div className="flex aspect-[9/16] w-full items-center justify-center rounded-lg bg-[#EFE8DB] text-[9px] text-[#6C6459]">
                          clip n/d
                        </div>
                      )}
                      <div className="mt-1 text-center text-[9px] uppercase tracking-wide text-[#9A8052]">inquadratura</div>
                    </div>
                  </div>
                  <dl className="mt-2 space-y-0.5 text-[10px] leading-snug text-[#4A443C]">
                    <div>
                      <span className="text-[#9A8052]">Inquadratura:</span> {card.derived_shot.framing}
                      {card.derived_shot.trajectory ? ` · ${card.derived_shot.trajectory}` : ""}
                    </div>
                    <div>
                      <span className="text-[#9A8052]">Movimento:</span> {card.camera_move}
                      {card.derived_shot.zoom_start != null && card.derived_shot.zoom_end != null
                        ? ` · zoom ${card.derived_shot.zoom_start.toFixed(2)}→${card.derived_shot.zoom_end.toFixed(2)}`
                        : ""}
                    </div>
                    <div>
                      <span className="text-[#9A8052]">Durata:</span> {card.duration_sec.toFixed(2)} s ·{" "}
                      {card.duration_frames} frame
                    </div>
                    <div>
                      <span className="text-[#9A8052]">Voce:</span> {card.voiceover.text || "—"}
                      {card.subtitle_text ? ` · sub: «${card.subtitle_text}»` : ""}
                    </div>
                    <div>
                      <span className="text-[#9A8052]">Transizione:</span> {card.transition}
                    </div>
                    <div>
                      <span className="text-[#9A8052]">Sound design:</span>{" "}
                      {[card.sound_design.sfx, card.sound_design.music_cue].filter(Boolean).join(" · ") || "—"}
                    </div>
                    {card.derived_shot.residual_resolution && (
                      <div>
                        <span className="text-[#9A8052]">Pixel reali usati:</span>{" "}
                        {card.derived_shot.residual_resolution.width}×{card.derived_shot.residual_resolution.height}
                      </div>
                    )}
                  </dl>
                  {!card.is_outro && (
                    <div className="mt-2 space-y-1.5">
                      <div className="flex gap-1.5">
                        <input
                          value={drafts[card.scene_number] || ""}
                          onChange={(e) =>
                            setDrafts((d) => ({ ...d, [card.scene_number]: e.target.value }))
                          }
                          placeholder="Es. «più vicino e più lento», «cambia il sottotitolo in …»"
                          className="min-w-0 flex-1 rounded-lg border border-[#DDD4C4] bg-white px-2 py-1.5 text-[10px] text-[#1D1B18] outline-none focus:border-[#9A8052]"
                        />
                        <button
                          type="button"
                          onClick={() => void revise(card)}
                          disabled={sceneBusy === card.scene_number}
                          className="inline-flex items-center gap-1 rounded-lg bg-[#224C66] px-2 py-1.5 text-[10px] font-medium text-white disabled:opacity-50"
                        >
                          {sceneBusy === card.scene_number ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Send className="h-3 w-3" />
                          )}
                          Modifica
                        </button>
                      </div>
                      <button
                        type="button"
                        onClick={() => void approve(card)}
                        disabled={sceneBusy === card.scene_number || card.approval_status === "approvata"}
                        className="inline-flex items-center gap-1 text-[10px] text-emerald-700 underline decoration-dotted disabled:text-[#9A8052] disabled:no-underline"
                      >
                        <CheckCircle2 className="h-3 w-3" /> Approva scena
                      </button>
                    </div>
                  )}
                  {card.derived_shot.motivation && (
                    <p className="mt-1 text-[9px] italic leading-snug text-[#6C6459]">{card.derived_shot.motivation}</p>
                  )}
                </div>
              ))}
            </div>
            {manifest.scene_clips_cached > 0 && (
              <p className="mt-2 flex items-center gap-1 text-[10px] text-[#6C6459]">
                <AlertTriangle className="h-3 w-3 text-[#9A8052]" /> {manifest.scene_clips_cached} clip riusate
                dalla cache: solo le scene modificate sono state rigenerate.
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
