"use client";

import React, { useEffect, useState } from "react";
import { CheckCircle2, AlertTriangle, Sparkles, Wand2, ArrowRight, RotateCcw, Copy, Upload, ExternalLink, Mic } from "lucide-react";
import type { ProjectRecord } from "@/lib/types";
import { koraApi } from "@/lib/api";

interface PreviewViewProps {
  project: ProjectRecord | null;
  onRevisionStarted: (jobId: string) => void;
  onRestored: () => void;
  onGoExport: () => void;
}

export function PreviewView({ project, onRevisionStarted, onRestored, onGoExport }: PreviewViewProps) {
  const [instruction, setInstruction] = useState("");
  const [diffPreview, setDiffPreview] = useState<{
    what_will_change: string[];
    preserved_scenes: number[];
    target_scenes: number[];
  } | null>(null);
  const [versions, setVersions] = useState<
    Array<{ id: number; version: number; summary: string; agent_name: string; created_at: string }>
  >([]);
  const [loading, setLoading] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [scriptInfo, setScriptInfo] = useState<{
    full_text: string;
    segmented_text: string;
    char_count: number;
    elevenlabs_url: string;
    instructions: string;
  } | null>(null);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [voiceMsg, setVoiceMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!project) return;
    koraApi.projectVersions(project.id).then(setVersions).catch(() => {});
  }, [project]);

  if (!project) {
    return (
      <div className="rounded-2xl border border-[#DDD4C4] bg-white p-10 text-center text-sm text-[#6C6459]">
        Seleziona un progetto completato per vedere l&apos;anteprima e richiedere revisioni.
      </div>
    );
  }

  const handlePreviewDiff = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!instruction.trim()) return;
    setLoading(true);
    try {
      const res = await koraApi.previewRevision(project.id, instruction.trim());
      setDiffPreview(res);
    } finally {
      setLoading(false);
    }
  };

  const handleApplyRevision = async () => {
    if (!instruction.trim()) return;
    setLoading(true);
    try {
      const res = await koraApi.applyRevision(project.id, instruction.trim());
      setDiffPreview(null);
      setInstruction("");
      onRevisionStarted(res.job.id);
    } finally {
      setLoading(false);
    }
  };

  const handleLoadScript = async () => {
    setVoiceBusy(true);
    try {
      const res = await koraApi.voiceoverScript(project.id);
      setScriptInfo(res);
      setVoiceMsg(`Copione pronto: ${res.char_count} caratteri, ${res.segments.length} scene.`);
    } catch (e) {
      setVoiceMsg((e as Error).message);
    } finally {
      setVoiceBusy(false);
    }
  };

  const handleCopyScript = async () => {
    let text = scriptInfo?.full_text;
    if (!text) {
      const res = await koraApi.voiceoverScript(project.id);
      setScriptInfo(res);
      text = res.full_text;
    }
    try {
      await navigator.clipboard.writeText(text);
      setVoiceMsg("Copione copiato: incollalo su elevenlabs.io e scarica l'MP3.");
    } catch {
      setVoiceMsg("Copia non consentita dal browser: usa «Scarica .txt».");
    }
  };

  const handleDownloadScript = async () => {
    const text = scriptInfo?.segmented_text || scriptInfo?.full_text || (await koraApi.voiceoverScript(project.id)).segmented_text;
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `copione_${project.id}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleUploadVoiceover = async (file: File) => {
    setVoiceBusy(true);
    try {
      const res = await koraApi.uploadVoiceover(project.id, file);
      setVoiceMsg(
        `Voce importata: ${res.voiceover.filename} · ${Number(res.voiceover.duration_sec).toFixed(2)}s · ` +
          `${res.voiceover.total_frames} frame. Lo storyboard è stato riallineato: lancia «Realizza Reel» per usarla.`
      );
    } catch (e) {
      setVoiceMsg((e as Error).message);
    } finally {
      setVoiceBusy(false);
    }
  };

  const handleRestore = async (ver: number) => {
    setLoading(true);
    try {
      await koraApi.restoreVersion(project.id, ver);
      setStatusMsg(`Progetto ripristinato alla versione v${ver}.`);
      const vList = await koraApi.projectVersions(project.id);
      setVersions(vList);
      onRestored();
    } finally {
      setLoading(false);
    }
  };

  const qc = project.qc_report || {};
  const checks = qc.checks || [];
  const voiceInfo = (project.edit_plan?.voice || null) as {
    voice_label?: string;
    source_used?: string;
    substituted?: boolean;
    substitution_notice?: string | null;
    status?: string;
  } | null;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
      {/* Vertical 9:16 Video Player */}
      <div className="lg:col-span-5 flex flex-col items-center rounded-2xl border border-[#DDD4C4] bg-[#141311] p-6">
        {project.output_video_path ? (
          <video
            key={`${project.id}-${project.updated_at}`}
            src={`/api/projects/${project.id}/video?v=${encodeURIComponent(project.updated_at)}`}
            controls
            playsInline
            className="aspect-[9/16] w-full max-w-[340px] rounded-xl bg-black shadow-2xl"
          />
        ) : (
          <div className="flex aspect-[9/16] w-full max-w-[320px] items-center justify-center rounded-xl border border-white/10 text-xs text-white/60">
            Video non ancora renderizzato
          </div>
        )}
        <div className="mt-4 flex w-full max-w-[340px] items-center justify-between text-xs text-[#DDD4C4]">
          <span>1080×1920 · {project.fps} fps</span>
          <span>v{project.current_version}</span>
        </div>
        {voiceInfo && (
          <div className="mt-3 w-full max-w-[340px] rounded-xl border border-white/15 bg-white/5 p-3 text-[11px] text-[#EDE6D9]">
            <div className="font-medium">
              Voce: {voiceInfo.voice_label || project.voice_id} ·{" "}
              <span className={voiceInfo.substituted ? "text-amber-300" : "text-emerald-300"}>
                {voiceInfo.source_used || voiceInfo.status}
              </span>
            </div>
            {voiceInfo.substitution_notice && (
              <p className="mt-1 text-[10px] leading-snug text-amber-200/90">
                {voiceInfo.substitution_notice}
              </p>
            )}
          </div>
        )}
      </div>

      {/* Natural Language Revisions, QC, and Version History */}
      <div className="lg:col-span-7 space-y-5">
        {/* Voci: flusso manuale ElevenLabs (copia copione -> sito -> carica MP3) */}
        <div className="rounded-2xl border border-[#DDD4C4] bg-white p-6">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="flex items-center gap-2 font-editorial text-lg text-[#1D1B18]">
                <Mic className="h-4 w-4 text-[#9A8052]" /> Voce del Reel
              </h3>
              <p className="mt-1 text-[11px] leading-relaxed text-[#6C6459]">
                Registri o generi la voce dove preferisci (anche sul sito ElevenLabs col tuo piano), poi la importi
                qui: KORA riallinea le scene alla durata reale e la usa al posto della sintesi automatica.
              </p>
            </div>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
            <button
              type="button"
              onClick={handleCopyScript}
              disabled={voiceBusy}
              className="flex items-center justify-center gap-1.5 rounded-xl border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-2 text-xs font-medium text-[#1D1B18] hover:border-[#9A8052] disabled:opacity-50"
            >
              <Copy className="h-3.5 w-3.5 text-[#9A8052]" /> 1. Copia copione
            </button>
            <a
              href={scriptInfo?.elevenlabs_url || "https://elevenlabs.io/app/speech-synthesis"}
              target="_blank"
              rel="noreferrer"
              className="flex items-center justify-center gap-1.5 rounded-xl border border-[#DDD4C4] bg-white px-3 py-2 text-xs font-medium text-[#1D1B18] hover:border-[#9A8052]"
            >
              <ExternalLink className="h-3.5 w-3.5 text-[#9A8052]" /> 2. Genera sul sito
            </a>
            <label className="flex cursor-pointer items-center justify-center gap-1.5 rounded-xl bg-[#1D1B18] px-3 py-2 text-xs font-medium text-white hover:bg-[#224C66]">
              <Upload className="h-3.5 w-3.5" /> 3. Carica MP3
              <input
                type="file"
                accept="audio/mpeg,audio/wav,audio/mp4,audio/aac,audio/ogg,audio/flac,.mp3,.wav,.m4a,.ogg,.flac"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void handleUploadVoiceover(f);
                  e.target.value = "";
                }}
              />
            </label>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-[#6C6459]">
            <button type="button" onClick={handleLoadScript} className="underline decoration-dotted">
              Mostra anteprima copione
            </button>
            <button type="button" onClick={handleDownloadScript} className="underline decoration-dotted">
              Scarica .txt
            </button>
            <button
              type="button"
              onClick={async () => {
                await koraApi.clearVoiceover(project.id);
                setVoiceMsg("Voce importata rimossa: al prossimo render torna la sintesi automatica.");
              }}
              className="underline decoration-dotted"
            >
              Rimuovi voce importata
            </button>
          </div>

          {scriptInfo && (
            <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-xl bg-[#FAF7F2] p-3 text-[10px] leading-relaxed text-[#4A443C]">
              {scriptInfo.segmented_text}
            </pre>
          )}
          {voiceMsg && (
            <p className="mt-2 rounded-lg bg-[#FBF4E6] px-3 py-2 text-[11px] text-[#7A5C2E]">{voiceMsg}</p>
          )}
        </div>

        <div className="rounded-2xl border border-[#DDD4C4] bg-white p-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="font-editorial text-2xl text-[#1D1B18]">{project.title}</h2>
              <p className="text-xs text-[#6C6459]">{project.preset}</p>
            </div>
            {project.output_video_path && (
              <button
                type="button"
                onClick={onGoExport}
                className="flex items-center gap-1.5 rounded-xl bg-[#1D1B18] px-4 py-2 text-xs font-medium text-white hover:bg-[#224C66]"
              >
                Vai a Export <ArrowRight className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {statusMsg && (
            <div className="mt-3 rounded-xl bg-[#EFE8DB] px-3 py-2 text-xs text-[#1D1B18]">
              {statusMsg}
            </div>
          )}

          {/* NL Revision Prompt */}
          <form onSubmit={handlePreviewDiff} className="mt-5 space-y-3">
            <label className="flex items-center gap-2 text-xs font-medium text-[#1D1B18]">
              <Wand2 className="h-4 w-4 text-[#9A8052]" />
              Revisione con linguaggio naturale (rendering selettivo)
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                type="text"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder='Es. "Rendi la terza scena più emozionante" oppure "Cambia soltanto la musica"'
                className="flex-1 rounded-xl border border-[#DDD4C4] bg-[#FAF7F2] px-4 py-2.5 text-sm focus:border-[#224C66] focus:outline-none"
              />
              <button
                type="submit"
                disabled={loading || !instruction.trim()}
                className="rounded-xl bg-[#224C66] px-4 py-2.5 text-xs font-medium text-white hover:bg-[#1D1B18] disabled:opacity-50"
              >
                Analizza modifica
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {[
                "Rendi la terza scena più emozionante.",
                "Usa meno parole.",
                "Il mare deve comparire prima.",
                "Cambia soltanto la musica.",
              ].map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setInstruction(s)}
                  className="rounded-full border border-[#DDD4C4] px-2.5 py-1 text-[11px] text-[#6C6459] hover:border-[#9A8052]"
                >
                  {s}
                </button>
              ))}
            </div>
          </form>

          {diffPreview && (
            <div className="mt-4 rounded-xl border border-[#9A8052]/50 bg-[#FAF7F2] p-4 space-y-2 text-xs">
              <div className="font-medium text-[#1D1B18]">Anteprima delle modifiche mirate:</div>
              <ul className="list-disc pl-5 space-y-1 text-[#4A443C]">
                {diffPreview.what_will_change.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
              <div className="text-[11px] text-[#6C6459]">
                Scene preservate intatte (cache attiva):{" "}
                {diffPreview.preserved_scenes.join(", ") || "nessuna"}
              </div>
              <div className="pt-2 flex gap-2">
                <button
                  type="button"
                  onClick={handleApplyRevision}
                  disabled={loading}
                  className="flex items-center gap-1 rounded-lg bg-[#1D1B18] px-4 py-2 text-xs font-medium text-white"
                >
                  <Sparkles className="h-3.5 w-3.5" /> Applica e rigenera solo il necessario
                </button>
                <button
                  type="button"
                  onClick={() => setDiffPreview(null)}
                  className="rounded-lg border border-[#DDD4C4] bg-white px-3 py-2 text-xs"
                >
                  Annulla
                </button>
              </div>
            </div>
          )}
        </div>

        {/* QC Report */}
        <div className="rounded-2xl border border-[#DDD4C4] bg-white p-6">
          <h3 className="text-sm font-medium text-[#1D1B18]">
            Controllo Qualità Automatico ({qc.passed ? "Superato" : "In attesa / Verifica"})
          </h3>
          {qc.needs_human_review && qc.human_review_reasons && (
            <div className="mt-3 rounded-xl border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
              <div className="flex items-center gap-1.5 font-medium">
                <AlertTriangle className="h-4 w-4" /> Revisione umana mirata richiesta:
              </div>
              <ul className="mt-1 list-disc pl-5">
                {qc.human_review_reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {checks.map((c, idx) => (
              <div
                key={idx}
                className="flex items-start gap-2 rounded-xl bg-[#FAF7F2] px-3 py-2 text-xs"
              >
                {c.passed ? (
                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-700" />
                ) : (
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600" />
                )}
                <div>
                  <div className="font-medium text-[#1D1B18]">{c.name}</div>
                  <div className="text-[11px] text-[#6C6459]">{c.detail}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Version History & Rollback */}
        {versions.length > 0 && (
          <div className="rounded-2xl border border-[#DDD4C4] bg-white p-5">
            <h3 className="text-sm font-medium text-[#1D1B18]">Cronologia versioni & Rollback</h3>
            <div className="mt-3 space-y-2 max-h-48 overflow-y-auto">
              {versions.map((v) => (
                <div
                  key={v.id}
                  className="flex items-center justify-between rounded-xl bg-[#FAF7F2] px-3.5 py-2 text-xs"
                >
                  <div>
                    <strong>v{v.version}</strong> · {v.summary}{" "}
                    <span className="text-[#6C6459]">({v.agent_name})</span>
                  </div>
                  <button
                    type="button"
                    disabled={loading}
                    onClick={() => handleRestore(v.version)}
                    className="flex items-center gap-1 rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1 text-[11px] hover:border-[#9A8052]"
                  >
                    <RotateCcw className="h-3 w-3" /> Ripristina
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
