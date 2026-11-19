"use client";

import React, { useState } from "react";
import { Search, Upload, ShieldCheck, Crop, Music, Video, Image as ImageIcon } from "lucide-react";
import type { MediaAsset } from "@/lib/types";
import { koraApi } from "@/lib/api";

interface MediaVaultViewProps {
  assets: MediaAsset[];
  onRefresh: () => void;
}

const CATEGORIES = [
  "Tutti",
  "Mare",
  "Spiaggia",
  "Pergolato",
  "Ombrelloni",
  "Arredi",
  "Colazione",
  "Aperitivo",
  "Ospitalità",
  "Persone",
  "Dettagli",
  "Luce",
  "Tramonto",
  "Logo",
  "Materiali audio",
];

export function MediaVaultView({ assets, onRefresh }: MediaVaultViewProps) {
  const [query, setQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("Tutti");
  const [searchResults, setSearchResults] = useState<MediaAsset[] | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const displayed = (searchResults ?? assets).filter((a) =>
    selectedCategory === "Tutti" ? true : a.category === selectedCategory
  );

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) {
      setSearchResults(null);
      return;
    }
    const res = await koraApi.searchVault(query.trim());
    setSearchResults(res);
  };

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    setStatusMsg(null);
    try {
      const res = await koraApi.uploadMedia(files);
      const dupCount = res.uploaded.filter((u) => u.duplicate).length;
      setStatusMsg(
        `Caricati ${res.uploaded.length} asset (${dupCount} duplicati già archiviati). ` +
          (res.errors.length ? `Errori: ${res.errors.map((e) => e.error).join("; ")}` : "")
      );
      onRefresh();
    } catch (err) {
      setStatusMsg((err as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const handleDetailCrop = async (assetId: string) => {
    setStatusMsg("Generazione ritaglio fedele (Modalità A)...");
    try {
      await koraApi.detailCrop(assetId, "dettaglio reale KORA", 1.8);
      setStatusMsg("Ritaglio fedele salvato nel Media Vault come derivato conservativo.");
      onRefresh();
    } catch (err) {
      setStatusMsg((err as Error).message);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="font-editorial text-2xl text-[#1D1B18]">KORA Media Vault</h2>
          <p className="text-xs text-[#6C6459]">
            Archivio intelligente non distruttivo: originali intatti, copie di lavoro, metadati e ricerca semantica.
          </p>
        </div>
        <label className="flex cursor-pointer items-center gap-2 rounded-xl bg-[#1D1B18] px-4 py-2.5 text-xs font-medium text-white hover:bg-[#224C66]">
          <Upload className="h-4 w-4" />
          <span>{uploading ? "Analisi..." : "Carica media"}</span>
          <input
            type="file"
            multiple
            accept=".jpg,.jpeg,.png,.webp,.heic,.mp4,.mov,.m4v,.wav,.mp3,.aac,.svg"
            className="hidden"
            onChange={(e) => handleUpload(e.target.files)}
          />
        </label>
      </div>

      {/* Semantic Search */}
      <form onSubmit={handleSearch} className="flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <Search className="absolute left-3.5 top-3 h-4 w-4 text-[#6C6459]" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder='Ricerca semantica es. "Trova una foto con la luce che attraversa il pergolato"'
            className="w-full rounded-xl border border-[#DDD4C4] bg-white py-2.5 pl-10 pr-4 text-sm focus:border-[#224C66] focus:outline-none"
          />
        </div>
        <button
          type="submit"
          className="rounded-xl bg-[#224C66] px-5 py-2.5 text-xs font-medium text-white hover:bg-[#1D1B18]"
        >
          Cerca
        </button>
        {searchResults && (
          <button
            type="button"
            onClick={() => {
              setQuery("");
              setSearchResults(null);
            }}
            className="rounded-xl border border-[#DDD4C4] bg-white px-4 py-2.5 text-xs"
          >
            Mostra tutti
          </button>
        )}
      </form>

      {/* Categories */}
      <div className="flex flex-wrap gap-1.5">
        {CATEGORIES.map((cat) => (
          <button
            key={cat}
            type="button"
            onClick={() => setSelectedCategory(cat)}
            className={`rounded-full px-3 py-1 text-xs transition ${
              selectedCategory === cat
                ? "bg-[#1D1B18] text-white"
                : "border border-[#DDD4C4] bg-white text-[#6C6459] hover:border-[#9A8052]"
            }`}
          >
            {cat}
          </button>
        ))}
      </div>

      {statusMsg && (
        <div className="rounded-xl border border-[#DDD4C4] bg-[#EFE8DB] px-4 py-2.5 text-xs text-[#1D1B18]">
          {statusMsg}
        </div>
      )}

      {/* Grid */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {displayed.map((a) => (
          <div
            key={a.id}
            className="overflow-hidden rounded-2xl border border-[#DDD4C4] bg-white shadow-sm"
          >
            <div className="relative h-48 bg-[#EFE8DB] flex items-center justify-center overflow-hidden">
              {a.media_type === "image" || a.media_type === "video" ? (
                <img
                  src={`/api/vault/${a.id}/thumb`}
                  alt={a.filename}
                  className="h-full w-full object-cover"
                />
              ) : (
                <Music className="h-10 w-10 text-[#9A8052]" />
              )}
              <span className="absolute left-3 top-3 flex items-center gap-1 rounded-full bg-white/90 px-2.5 py-0.5 text-[10px] font-medium text-[#1D1B18]">
                <ShieldCheck className="h-3 w-3 text-emerald-700" />
                {a.authenticity_status}
              </span>
              <span className="absolute right-3 top-3 rounded-full bg-[#1D1B18]/80 px-2 py-0.5 text-[10px] text-white">
                {a.category}
              </span>
            </div>
            <div className="p-4 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium text-[#1D1B18]">{a.filename}</span>
                {a.media_type === "video" ? (
                  <Video className="h-3.5 w-3.5 shrink-0 text-[#224C66]" />
                ) : a.media_type === "image" ? (
                  <ImageIcon className="h-3.5 w-3.5 shrink-0 text-[#9A8052]" />
                ) : null}
              </div>
              <div className="text-[11px] text-[#6C6459]">
                {a.width && a.height ? `${a.width}×${a.height} · ${a.orientation}` : a.orientation}
                {a.duration_sec ? ` · ${a.duration_sec.toFixed(1)}s` : ""} · Qualità {a.technical_quality}/100
              </div>
              <div className="flex flex-wrap gap-1">
                {a.tags.slice(0, 4).map((t) => (
                  <span key={t} className="rounded bg-[#FAF7F2] px-1.5 py-0.5 text-[10px] text-[#6C6459]">
                    #{t}
                  </span>
                ))}
              </div>
              {a.media_type === "image" && a.category !== "Logo" && (
                <button
                  type="button"
                  onClick={() => handleDetailCrop(a.id)}
                  className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg border border-[#DDD4C4] py-1.5 text-xs text-[#1D1B18] hover:border-[#9A8052]"
                >
                  <Crop className="h-3.5 w-3.5" /> Estrai dettaglio reale (Modalità A)
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
