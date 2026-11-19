"use client";

import React, { useEffect, useState } from "react";
import { Download, Copy, Check, Archive, ShieldCheck } from "lucide-react";
import type { ProjectRecord } from "@/lib/types";
import { koraApi } from "@/lib/api";

interface ExportViewProps {
  project: ProjectRecord | null;
  onRefresh: () => void;
}

export function ExportView({ project, onRefresh }: ExportViewProps) {
  const [copied, setCopied] = useState(false);
  const [renderingVariants, setRenderingVariants] = useState(false);
  const [integrity, setIntegrity] = useState<Record<string, unknown> | null>(null);
  const [backupMsg, setBackupMsg] = useState<string | null>(null);

  useEffect(() => {
    koraApi.checkIntegrity().then(setIntegrity).catch(() => {});
  }, []);

  const handleCopyCaption = () => {
    if (!project?.export_bundle?.caption) return;
    const text = `${project.export_bundle.caption}\n\n${(project.export_bundle.hashtags || []).join(" ")}`;
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleGenerateVariants = async () => {
    if (!project) return;
    setRenderingVariants(true);
    try {
      await koraApi.produceProject(project.id, {
        include_subtitles: true,
        include_no_subtitles_variant: true,
        include_no_music_variant: true,
      });
      setBackupMsg("Generazione varianti senza sottotitoli e senza musica avviata in Production.");
      onRefresh();
    } finally {
      setRenderingVariants(false);
    }
  };

  const handleCreateBackup = async () => {
    const res = await koraApi.createBackup();
    setBackupMsg(`Backup completo creato: ${String(res.backup_path)}`);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="font-editorial text-2xl text-[#1D1B18]">Export & Archiviazione</h2>
        <p className="text-xs text-[#6C6459]">
          Preset principale: MP4 · H.264 · 1080×1920 (9:16) · 25 fps · AAC · Rec.709.
        </p>
      </div>

      {!project || !project.output_video_path ? (
        <div className="rounded-2xl border border-[#DDD4C4] bg-white p-8 text-center text-sm text-[#6C6459]">
          Nessun Reel completato selezionato per l&apos;export. Completa una produzione prima di esportare.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="rounded-2xl border border-[#DDD4C4] bg-white p-6 space-y-4">
            <h3 className="text-sm font-medium text-[#1D1B18]">
              Pacchetto Export Verificato · {project.title}
            </h3>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <a
                href={`/api/projects/${project.id}/download/reel`}
                className="flex items-center justify-center gap-2 rounded-xl bg-[#1D1B18] px-4 py-2.5 text-xs font-medium text-white hover:bg-[#224C66]"
              >
                <Download className="h-4 w-4" /> Reel Finale MP4
              </a>
              <a
                href={`/api/projects/${project.id}/download/cover`}
                className="flex items-center justify-center gap-2 rounded-xl border border-[#DDD4C4] px-4 py-2.5 text-xs font-medium hover:border-[#9A8052]"
              >
                <Download className="h-4 w-4" /> Copertina Verticale PNG
              </a>
              <a
                href={`/api/projects/${project.id}/download/editable_json`}
                className="flex items-center justify-center gap-2 rounded-xl border border-[#DDD4C4] px-4 py-2.5 text-xs font-medium hover:border-[#9A8052]"
              >
                <Download className="h-4 w-4" /> Progetto Modificabile JSON
              </a>
              <a
                href={`/api/projects/${project.id}/download/report_md`}
                className="flex items-center justify-center gap-2 rounded-xl border border-[#DDD4C4] px-4 py-2.5 text-xs font-medium hover:border-[#9A8052]"
              >
                <Download className="h-4 w-4" /> Report Tecnico MD
              </a>
              {project.export_bundle.no_subtitles_mp4 && (
                <a
                  href={`/api/projects/${project.id}/download/no_subtitles`}
                  className="flex items-center justify-center gap-2 rounded-xl border border-[#DDD4C4] px-4 py-2.5 text-xs font-medium"
                >
                  <Download className="h-4 w-4" /> Variante Senza Sottotitoli
                </a>
              )}
              {project.export_bundle.no_music_mp4 && (
                <a
                  href={`/api/projects/${project.id}/download/no_music`}
                  className="flex items-center justify-center gap-2 rounded-xl border border-[#DDD4C4] px-4 py-2.5 text-xs font-medium"
                >
                  <Download className="h-4 w-4" /> Variante Senza Musica
                </a>
              )}
            </div>
            <button
              type="button"
              onClick={handleGenerateVariants}
              disabled={renderingVariants}
              className="w-full rounded-xl border border-[#9A8052] py-2 text-xs font-medium text-[#1D1B18] hover:bg-[#FAF7F2]"
            >
              {renderingVariants
                ? "Generazione varianti..."
                : "Genera versioni opzionali (senza sottotitoli + senza musica)"}
            </button>
          </div>

          <div className="rounded-2xl border border-[#DDD4C4] bg-white p-6 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-[#1D1B18]">Caption Instagram & Hashtag</h3>
              <button
                type="button"
                onClick={handleCopyCaption}
                className="flex items-center gap-1 rounded-lg border border-[#DDD4C4] px-2.5 py-1 text-xs"
              >
                {copied ? <Check className="h-3.5 w-3.5 text-emerald-700" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? "Copiata" : "Copia"}
              </button>
            </div>
            <pre className="whitespace-pre-wrap rounded-xl bg-[#FAF7F2] p-4 font-sans text-xs text-[#1D1B18] leading-relaxed">
              {project.export_bundle.caption}
              {"\n\n"}
              {(project.export_bundle.hashtags || []).join(" ")}
            </pre>
          </div>
        </div>
      )}

      {/* Backup & Integrity */}
      <div className="rounded-2xl border border-[#DDD4C4] bg-white p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-2 text-sm font-medium text-[#1D1B18]">
            <ShieldCheck className="h-4 w-4 text-emerald-700" />
            Persistenza, Integrità DB & Backup (originali protetti)
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleCreateBackup}
              className="flex items-center gap-1.5 rounded-xl bg-[#224C66] px-4 py-2 text-xs font-medium text-white"
            >
              <Archive className="h-3.5 w-3.5" /> Crea Backup Completo (.zip)
            </button>
            <button
              type="button"
              onClick={async () => {
                const res = await koraApi.cleanCache();
                setBackupMsg(
                  `Cache pulita (${String(res.removed_files)} file temporanei). Originali intatti.`
                );
              }}
              className="rounded-xl border border-[#DDD4C4] px-3 py-2 text-xs"
            >
              Pulisci Cache
            </button>
          </div>
        </div>
        {integrity && (
          <div className="mt-3 text-xs text-[#6C6459]">
            SQLite: <strong className="text-[#1D1B18]">{String(integrity.sqlite_integrity)}</strong> · Asset nel Vault:{" "}
            <strong className="text-[#1D1B18]">{String(integrity.total_vault_assets)}</strong> · Integrità:{" "}
            <strong className="text-emerald-700">{integrity.healthy ? "Verifica OK" : "Attenzione"}</strong>
          </div>
        )}
        {backupMsg && (
          <div className="mt-3 rounded-xl bg-[#EFE8DB] px-4 py-2 text-xs text-[#1D1B18]">
            {backupMsg}
          </div>
        )}
      </div>
    </div>
  );
}
