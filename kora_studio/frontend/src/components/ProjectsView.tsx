"use client";

import React, { useEffect, useState } from "react";
import { Film, RotateCcw, ArrowRight, Archive, Trash2, FolderInput, Undo2 } from "lucide-react";
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
  const [showArchived, setShowArchived] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const defaultArchiveDir = () => {
    const saved = typeof window !== "undefined" ? window.localStorage.getItem("kora_archive_dir") : null;
    return saved || "~/Desktop/KORA-ARCHIVIO";
  };

  const askDestination = (projectTitle: string) => {
    const hint = defaultArchiveDir();
    const value = window.prompt(
      `Cartella in cui archiviare "${projectTitle}"\n(percorso assoluto oppure ~/...)`,
      hint
    );
    if (!value) return null;
    window.localStorage.setItem("kora_archive_dir", value.trim());
    return value.trim();
  };

  const handleArchive = async (id: string, title: string) => {
    const destination = askDestination(title);
    if (!destination) return;
    setBusyId(id);
    setFeedback(null);
    try {
      const res = await koraApi.archiveProject(id, destination);
      setFeedback(`Archiviato in: ${res.destination} (${res.files.length} file copiati)`);
      onRefresh();
    } catch (e) {
      setFeedback((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const handleUnarchive = async (id: string) => {
    setBusyId(id);
    try {
      await koraApi.unarchiveProject(id);
      setFeedback("Progetto ripristinato tra i progetti attivi.");
      onRefresh();
    } catch (e) {
      setFeedback((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (id: string, title: string) => {
    const ok = window.confirm(
      `Eliminare definitivamente "${title}"?\n\nVerranno rimossi video, copertina, report e versioni. L'operazione è irreversibile.`
    );
    if (!ok) return;
    setBusyId(id);
    setFeedback(null);
    try {
      await koraApi.deleteProject(id);
      setFeedback(`Progetto "${title}" eliminato definitivamente.`);
      onRefresh();
    } catch (e) {
      setFeedback((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const visibleProjects = projects.filter((p) =>
    showArchived ? p.status === "Archiviato" : p.status !== "Archiviato"
  );
  const archivedCount = projects.filter((p) => p.status === "Archiviato").length;

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
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowArchived((v) => !v)}
            className={`flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-medium ${
              showArchived
                ? "border-[#9A8052] bg-[#FBF4E6] text-[#7A5C2E]"
                : "border-[#DDD4C4] bg-white text-[#1D1B18] hover:border-[#9A8052]"
            }`}
          >
            <Archive className="h-3.5 w-3.5" />
            {showArchived ? "Vedi attivi" : `Archiviati (${archivedCount})`}
          </button>
          <button
            type="button"
            onClick={onRefresh}
            className="rounded-xl border border-[#DDD4C4] bg-white px-4 py-2 text-xs font-medium text-[#1D1B18] hover:border-[#9A8052]"
          >
            Aggiorna
          </button>
        </div>
      </div>

      {feedback && (
        <div className="rounded-xl border border-[#E4D3B4] bg-[#FBF4E6] px-4 py-2.5 text-[11px] text-[#7A5C2E]">
          {feedback}
        </div>
      )}

      {visibleProjects.length === 0 ? (
        <div className="rounded-2xl border border-[#DDD4C4] bg-white p-10 text-center text-sm text-[#6C6459]">
          {showArchived
            ? "Nessun progetto archiviato."
            : "Nessun progetto ancora creato. Parti dalla Home per produrre il primo Reel KORA."}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {visibleProjects.map((p) => (
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
                        : p.status === "Archiviato"
                          ? "bg-amber-50 text-amber-800"
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
                <button
                  type="button"
                  disabled={busyId === p.id}
                  onClick={() =>
                    showArchived ? void handleUnarchive(p.id) : void handleArchive(p.id, p.title)
                  }
                  className="flex items-center gap-1 rounded-lg border border-[#DDD4C4] px-3 py-1.5 text-xs hover:border-[#9A8052] disabled:opacity-50"
                >
                  {showArchived ? (
                    <>
                      <Undo2 className="h-3 w-3" /> Ripristina
                    </>
                  ) : (
                    <>
                      <FolderInput className="h-3 w-3" /> Archivia…
                    </>
                  )}
                </button>
                <button
                  type="button"
                  disabled={busyId === p.id}
                  onClick={() => void handleDelete(p.id, p.title)}
                  className="flex items-center gap-1 rounded-lg border border-red-200 px-3 py-1.5 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
                >
                  <Trash2 className="h-3 w-3" /> Elimina
                </button>
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
