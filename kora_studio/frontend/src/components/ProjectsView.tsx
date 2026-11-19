"use client";

import React, { useEffect, useState } from "react";
import { Film, RotateCcw, ArrowRight } from "lucide-react";
import type { ProjectRecord } from "@/lib/types";
import { koraApi } from "@/lib/api";

interface ProjectsViewProps {
  projects: ProjectRecord[];
  activeProjectId: string | null;
  onSelectProject: (id: string, tab?: "creative" | "production" | "preview" | "export") => void;
  onRefresh: () => void;
}

export function ProjectsView({
  projects,
  activeProjectId,
  onSelectProject,
  onRefresh,
}: ProjectsViewProps) {
  const [versions, setVersions] = useState<
    Array<{ id: number; version: number; summary: string; agent_name: string; created_at: string }>
  >([]);

  useEffect(() => {
    if (activeProjectId) {
      koraApi.projectVersions(activeProjectId).then(setVersions).catch(() => setVersions([]));
    }
  }, [activeProjectId, projects]);

  const handleRestore = async (ver: number) => {
    if (!activeProjectId) return;
    await koraApi.restoreVersion(activeProjectId, ver);
    onRefresh();
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-editorial text-2xl text-[#1D1B18]">Progetti Reel</h2>
          <p className="text-xs text-[#6C6459]">
            Storico completo dei Reel in corso, completati e versioni ripristinabili.
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="rounded-xl border border-[#DDD4C4] bg-white px-4 py-2 text-xs font-medium text-[#1D1B18] hover:border-[#9A8052]"
        >
          Aggiorna
        </button>
      </div>

      {projects.length === 0 ? (
        <div className="rounded-2xl border border-[#DDD4C4] bg-white p-10 text-center text-sm text-[#6C6459]">
          Nessun progetto ancora creato. Parti dalla Home per produrre il primo Reel KORA.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {projects.map((p) => (
            <div
              key={p.id}
              className={`rounded-2xl border bg-white p-5 shadow-sm transition ${
                p.id === activeProjectId ? "border-[#224C66]" : "border-[#DDD4C4]"
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <Film className="h-4 w-4 text-[#9A8052]" />
                    <span className="font-medium text-[#1D1B18]">{p.title}</span>
                  </div>
                  <p className="mt-1 text-xs text-[#6C6459] line-clamp-2">{p.theme}</p>
                </div>
                <span
                  className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium ${
                    p.status === "Completato"
                      ? "bg-emerald-50 text-emerald-800"
                      : p.status === "Errore"
                        ? "bg-red-50 text-red-700"
                        : "bg-[#EFE8DB] text-[#1D1B18]"
                  }`}
                >
                  {p.status}
                </span>
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-[#6C6459]">
                <span>{p.preset}</span>
                <span>·</span>
                <span>{p.target_duration_sec}s ({p.total_frames} frame)</span>
                <span>·</span>
                <span>Voce: {p.voice_id}</span>
                <span>·</span>
                <span>v{p.current_version}</span>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => onSelectProject(p.id, "creative")}
                  className="rounded-lg border border-[#DDD4C4] px-3 py-1.5 text-xs hover:border-[#9A8052]"
                >
                  Storyboard
                </button>
                <button
                  type="button"
                  onClick={() => onSelectProject(p.id, "production")}
                  className="rounded-lg border border-[#DDD4C4] px-3 py-1.5 text-xs hover:border-[#9A8052]"
                >
                  Produzione
                </button>
                {p.output_video_path && (
                  <>
                    <button
                      type="button"
                      onClick={() => onSelectProject(p.id, "preview")}
                      className="flex items-center gap-1 rounded-lg bg-[#224C66] px-3 py-1.5 text-xs text-white"
                    >
                      Anteprima <ArrowRight className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      onClick={() => onSelectProject(p.id, "export")}
                      className="rounded-lg bg-[#1D1B18] px-3 py-1.5 text-xs text-white"
                    >
                      Export
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {activeProjectId && versions.length > 0 && (
        <div className="rounded-2xl border border-[#DDD4C4] bg-white p-5">
          <h3 className="text-sm font-medium text-[#1D1B18]">
            Cronologia versioni progetto selezionato
          </h3>
          <div className="mt-3 space-y-2">
            {versions.map((v) => (
              <div
                key={v.id}
                className="flex items-center justify-between rounded-xl bg-[#FAF7F2] px-4 py-2.5 text-xs"
              >
                <div>
                  <span className="font-semibold text-[#1D1B18]">v{v.version}</span> · {v.summary}{" "}
                  <span className="text-[#6C6459]">({v.agent_name})</span>
                </div>
                <button
                  type="button"
                  onClick={() => handleRestore(v.version)}
                  className="flex items-center gap-1 rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1 hover:border-[#224C66]"
                >
                  <RotateCcw className="h-3 w-3" /> Ripristina
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
