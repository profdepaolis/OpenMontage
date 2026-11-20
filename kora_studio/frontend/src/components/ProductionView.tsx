"use client";

import React from "react";
import { CheckCircle2, CircleDot, Clock, RotateCw, XCircle, AlertTriangle } from "lucide-react";
import type { JobRecord } from "@/lib/types";
import { koraApi } from "@/lib/api";

interface ProductionViewProps {
  jobs: JobRecord[];
  activeJob: JobRecord | null;
  onRefresh: () => void;
  onOpenPreview: (projectId: string) => void;
}

const PIPELINE_STEPS = [
  "Analisi materiali",
  "Concept",
  "Storyboard",
  "Generazione asset",
  "Voiceover",
  "Sound design",
  "Montaggio",
  "Rendering",
  "Controllo qualità",
];

export function ProductionView({
  jobs,
  activeJob,
  onRefresh,
  onOpenPreview,
}: ProductionViewProps) {
  const job = activeJob || jobs[0] || null;
  const warnCount = (job?.logs ?? []).filter((l) =>
    ["WARN", "WARNING"].includes((l.level || "").toUpperCase())
  ).length;
  const errorCount = (job?.logs ?? []).filter(
    (l) => (l.level || "").toUpperCase() === "ERROR"
  ).length;

  const handleCancel = async (id: string) => {
    await koraApi.cancelJob(id);
    onRefresh();
  };

  const handleRetry = async (id: string) => {
    await koraApi.retryJob(id);
    onRefresh();
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-editorial text-2xl text-[#1D1B18]">Production Pipeline</h2>
          <p className="text-xs text-[#6C6459]">
            Avanzamento reale osservabile per fasi (nessuna percentuale simulata).
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="rounded-xl border border-[#DDD4C4] bg-white px-4 py-2 text-xs font-medium hover:border-[#9A8052]"
        >
          Aggiorna stato
        </button>
      </div>

      {!job ? (
        <div className="rounded-2xl border border-[#DDD4C4] bg-white p-10 text-center text-sm text-[#6C6459]">
          Nessun job di produzione attivo. Seleziona un concept dalla Home per avviare il rendering.
        </div>
      ) : (
        <div className="space-y-5">
          <div className="rounded-2xl border border-[#DDD4C4] bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <div className="text-xs uppercase tracking-wider text-[#9A8052]">
                  Job {job.id} · {job.job_type}
                </div>
                <h3 className="mt-1 font-editorial text-xl text-[#1D1B18]">
                  Stato attuale: {job.state}
                </h3>
                <p className="text-xs text-[#6C6459]">Fase corrente: {job.current_step}</p>
              </div>
              <div className="flex gap-2">
                {job.state !== "Completato" && job.state !== "Errore" && (
                  <button
                    type="button"
                    onClick={() => handleCancel(job.id)}
                    className="flex items-center gap-1 rounded-xl border border-red-200 px-3 py-2 text-xs text-red-700 hover:bg-red-50"
                  >
                    <XCircle className="h-3.5 w-3.5" /> Annulla
                  </button>
                )}
                {(job.state === "Errore" || job.state === "Richiede intervento") && (
                  <button
                    type="button"
                    onClick={() => handleRetry(job.id)}
                    className="flex items-center gap-1 rounded-xl bg-[#1D1B18] px-3 py-2 text-xs text-white"
                  >
                    <RotateCw className="h-3.5 w-3.5" /> Riprova
                  </button>
                )}
                {job.state === "Completato" && (
                  <button
                    type="button"
                    onClick={() => onOpenPreview(job.project_id)}
                    className="rounded-xl bg-[#224C66] px-4 py-2 text-xs font-medium text-white"
                  >
                    Apri Anteprima
                  </button>
                )}
              </div>
            </div>

            {job.intervention_reason && (
              <div className="mt-4 flex items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                <span>{job.intervention_reason}</span>
              </div>
            )}
            {job.error_message && (
              <div className="mt-4 rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-800">
                Errore: {job.error_message}
              </div>
            )}

            {/* Observable Steps */}
            <div className="mt-6 grid grid-cols-1 gap-2 sm:grid-cols-3">
              {PIPELINE_STEPS.map((step) => {
                const done = job.completed_steps.includes(step);
                const active = !done && job.state === step;
                return (
                  <div
                    key={step}
                    className={`flex items-center gap-2.5 rounded-xl border px-3.5 py-2.5 text-xs ${
                      done
                        ? "border-emerald-200 bg-emerald-50/70 text-emerald-900"
                        : active
                          ? "border-[#224C66] bg-[#224C66]/10 text-[#1D1B18] font-medium"
                          : "border-[#EDE6D9] bg-[#FAF7F2] text-[#6C6459]"
                    }`}
                  >
                    {done ? (
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-700" />
                    ) : active ? (
                      <CircleDot className="h-4 w-4 shrink-0 text-[#224C66] animate-pulse" />
                    ) : (
                      <Clock className="h-4 w-4 shrink-0 text-[#9A8052]/60" />
                    )}
                    <span>{step}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Agent Logs */}
          <div className="rounded-2xl border border-[#DDD4C4] bg-white p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h4 className="text-sm font-medium text-[#1D1B18]">Log KORA Director e Agenti</h4>
              <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                {warnCount > 0 && (
                  <span className="rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-800">
                    {warnCount} avvisi · fallback automatici
                  </span>
                )}
                {errorCount > 0 && (
                  <span className="rounded-full bg-red-50 px-2 py-0.5 font-medium text-red-700">
                    {errorCount} errori
                  </span>
                )}
                {warnCount === 0 && errorCount === 0 && (
                  <span className="rounded-full bg-[#F1F7EF] px-2 py-0.5 font-medium text-[#2F5D3A]">
                    nessun avviso
                  </span>
                )}
              </div>
            </div>
            <div className="mt-3 max-h-64 overflow-y-auto space-y-1.5 font-mono text-[11px]">
              {job.logs.map((l, idx) => {
                const level = (l.level || "INFO").toUpperCase();
                const isError = level === "ERROR";
                const isWarn = level === "WARN" || level === "WARNING";
                return (
                  <div
                    key={idx}
                    className={`flex gap-2 rounded px-3 py-1.5 ${
                      isError ? "bg-red-50" : isWarn ? "bg-amber-50/70" : "bg-[#FAF7F2]"
                    }`}
                  >
                    {(isError || isWarn) && (
                      <AlertTriangle
                        className={`mt-0.5 h-3 w-3 shrink-0 ${isError ? "text-red-600" : "text-amber-600"}`}
                      />
                    )}
                    <span
                      className={`shrink-0 ${
                        isError ? "text-red-700" : isWarn ? "text-amber-800" : "text-[#9A8052]"
                      }`}
                    >
                      [{l.agent}]
                    </span>
                    <span className={isError ? "text-red-900" : "text-[#1D1B18]"}>{l.message}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
