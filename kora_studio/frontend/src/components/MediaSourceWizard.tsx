"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Check,
  Film,
  FolderOpen,
  Image as ImageIcon,
  Layers,
  Loader2,
  Search,
  UploadCloud,
  X,
} from "lucide-react";
import { koraApi } from "@/lib/api";
import type { MediaAsset } from "@/lib/types";

export type MediaChoice = {
  mode: "vault_only" | "upload_only" | "mixed";
  assetIds: string[];
  auto: boolean;
  priority?: "archive_first" | "uploads_first";
  note?: string;
};

type Props = {
  open: boolean;
  initialMode?: string;
  initialAssetIds?: string[];
  onCancel: () => void;
  onConfirm: (choice: MediaChoice) => void;
  onVaultUpdated?: () => void;
};

const OPTIONS: Array<{
  id: "vault_only" | "upload_only" | "mixed";
  tag: string;
  title: string;
  detail: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  {
    id: "vault_only",
    tag: "A",
    title: "Solo dal Media Vault",
    detail: "Tutte le foto e i video già in archivio, con filtri e ricerca. Puoi scegliere tu o far scelgliere i materiali migliori.",
    icon: FolderOpen,
  },
  {
    id: "upload_only",
    tag: "B",
    title: "Solo materiali nuovi",
    detail: "Carica adesso foto e video: restano in archivio come originali, ma il Reel userà soltanto quelli.",
    icon: UploadCloud,
  },
  {
    id: "mixed",
    tag: "C",
    title: "Archivio + nuovi",
    detail: "Combina i due mondi: scegli gli asset in archivio, aggiungi i nuovi e decidi quale ordine di priorità seguire.",
    icon: Layers,
  },
];

function labelOf(asset: MediaAsset): string {
  return asset.filename || asset.id;
}

/**
 * Scelta esplicita della provenienza dei materiali (A/B/C).
 * La scelta viene salvata nel progetto: nessun ritorno silenzioso a `vault_auto`.
 */
export function MediaSourceWizard({
  open,
  initialMode,
  initialAssetIds,
  onCancel,
  onConfirm,
  onVaultUpdated,
}: Props) {
  const [mode, setMode] = useState<MediaChoice["mode"]>("vault_only");
  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [uploaded, setUploaded] = useState<MediaAsset[]>([]);
  const [filter, setFilter] = useState<"all" | "photo" | "video">("all");
  const [query, setQuery] = useState("");
  const [auto, setAuto] = useState(true);
  const [priority, setPriority] = useState<"archive_first" | "uploads_first">("archive_first");
  const [error, setError] = useState<string | null>(null);

  const normaliseMode = (raw?: string): MediaChoice["mode"] => {
    const value = (raw || "").toLowerCase();
    if (value === "upload_only" || value === "uploads") return "upload_only";
    if (value === "mixed" || value === "combined") return "mixed";
    return "vault_only";
  };

  useEffect(() => {
    if (!open) return;
    setMode(normaliseMode(initialMode));
    setSelected(initialAssetIds && initialAssetIds.length ? [...initialAssetIds] : []);
    setUploaded([]);
    setQuery("");
    setFilter("all");
    setError(null);
    setLoading(true);
    koraApi
      .listVault()
      .then((list) => setAssets(list))
      .catch((err: Error) => setError(`Archivio non leggibile: ${err.message}`))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return assets.filter((a) => {
      if (filter !== "all" && a.media_type !== filter) return false;
      if (!q) return true;
      return `${a.filename} ${a.category || ""}`.toLowerCase().includes(q);
    });
  }, [assets, filter, query]);

  const toggle = (id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const doUpload = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files);
      if (!list.length) return;
      setUploading(true);
      setError(null);
      try {
        const res = await koraApi.uploadMedia(list, "reel");
        const ok = res.uploaded || [];
        setUploaded((prev) => [...prev, ...ok]);
        setAssets((prev) => [...ok, ...prev]);
        if (res.errors && res.errors.length) {
          setError(
            `Caricati ${ok.length}, scartati ${res.errors.length}: ${res.errors
              .map((e) => `${e.filename} (${e.error})`)
              .join("; ")}`
          );
        }
        onVaultUpdated?.();
      } catch (err) {
        setError(`Caricamento non riuscito: ${(err as Error).message}`);
      } finally {
        setUploading(false);
      }
    },
    [onVaultUpdated]
  );

  if (!open) return null;

  const chosenIds =
    mode === "vault_only"
      ? auto
        ? []
        : selected
      : mode === "upload_only"
        ? uploaded.map((a) => a.id)
        : selected;

  const canConfirm =
    mode === "vault_only" ? auto || selected.length > 0 : mode === "upload_only" ? uploaded.length > 0 : true;

  const confirm = () => {
    if (!canConfirm) return;
    onConfirm({
      mode,
      assetIds: chosenIds,
      auto: mode === "vault_only" ? auto : false,
      priority: mode === "mixed" ? priority : undefined,
      note:
        mode === "vault_only"
          ? auto
            ? "Selezione automatica dei materiali migliori dall'archivio"
            : `${selected.length} asset scelti a mano dall'archivio`
          : mode === "upload_only"
            ? `${uploaded.length} materiali nuovi caricati ora`
            : `Archivio (${priority === "archive_first" ? "prima" : "dopo"}) + ${selected.length} scelti + ${uploaded.length} nuovi`,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-3 sm:p-6">
      <div className="w-full max-w-4xl rounded-3xl border border-[#DDD4C4] bg-[#FAF7F2] shadow-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-[#EDE6D9] px-5 py-4">
          <div>
            <div className="text-[10px] uppercase tracking-[0.25em] text-[#9A8052]">Prima di progettare</div>
            <h2 className="font-editorial text-2xl text-[#1D1B18]">Da dove prendiamo i materiali?</h2>
            <p className="mt-1 text-xs text-[#6C6459]">
              La scelta resta scritta nel progetto: il motore creativo e il renderer la rispettano.
            </p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-full border border-[#DDD4C4] p-1.5 text-[#6C6459] hover:bg-white"
            aria-label="Chiudi"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4 px-5 py-4">
          <div className="grid gap-2 sm:grid-cols-3">
            {OPTIONS.map((opt) => {
              const Icon = opt.icon;
              const active = mode === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setMode(opt.id)}
                  className={`rounded-2xl border p-3 text-left transition ${
                    active
                      ? "border-[#224C66] bg-white shadow-sm ring-1 ring-[#224C66]/30"
                      : "border-[#DDD4C4] bg-white/60 hover:border-[#9A8052]"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold ${
                        active ? "bg-[#224C66] text-white" : "bg-[#EFE8DB] text-[#4A443C]"
                      }`}
                    >
                      {opt.tag}
                    </span>
                    <Icon className="h-4 w-4 text-[#9A8052]" />
                    <span className="text-xs font-semibold text-[#1D1B18]">{opt.title}</span>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-snug text-[#6C6459]">{opt.detail}</p>
                </button>
              );
            })}
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-900">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {(mode === "vault_only" || mode === "mixed") && (
            <section className="rounded-2xl border border-[#EDE6D9] bg-white p-3">
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-[180px] flex-1">
                  <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-[#9A8052]" />
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Cerca per nome o categoria…"
                    className="w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] py-1.5 pl-8 pr-2 text-[11px]"
                  />
                </div>
                {(["all", "photo", "video"] as const).map((f) => (
                  <button
                    key={f}
                    type="button"
                    onClick={() => setFilter(f)}
                    className={`rounded-lg px-2.5 py-1 text-[11px] ${
                      filter === f
                        ? "bg-[#1D1B18] text-[#FAF7F2]"
                        : "border border-[#DDD4C4] text-[#4A443C] hover:border-[#9A8052]"
                    }`}
                  >
                    {f === "all" ? "Tutti" : f === "photo" ? "Foto" : "Video"}
                  </button>
                ))}
                <span className="text-[11px] text-[#6C6459]">
                  {selected.length} scelti · {visible.length} visibili
                </span>
              </div>

              {mode === "vault_only" && (
                <label className="mt-2 flex items-center gap-2 rounded-lg bg-[#FAF7F2] px-2.5 py-1.5 text-[11px] text-[#4A443C]">
                  <input
                    type="checkbox"
                    checked={auto}
                    onChange={(e) => setAuto(e.target.checked)}
                    className="h-3.5 w-3.5 accent-[#224C66]"
                  />
                  Scegli automaticamente i materiali migliori (nessuna selezione manuale)
                </label>
              )}

              {loading ? (
                <div className="flex items-center gap-2 py-6 text-[11px] text-[#6C6459]">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Leggo l'archivio…
                </div>
              ) : (
                <div className="mt-2 grid max-h-64 grid-cols-3 gap-2 overflow-y-auto sm:grid-cols-5">
                  {visible.map((asset) => {
                    const isSelected = selected.includes(asset.id);
                    return (
                      <button
                        key={asset.id}
                        type="button"
                        onClick={() => {
                          setAuto(false);
                          toggle(asset.id);
                        }}
                        className={`group relative overflow-hidden rounded-xl border text-left ${
                          isSelected ? "border-[#224C66] ring-2 ring-[#224C66]/40" : "border-[#EDE6D9]"
                        }`}
                        title={`${labelOf(asset)}${asset.category ? ` · ${asset.category}` : ""}`}
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={`/api/vault/${asset.id}/thumb`}
                          alt={labelOf(asset)}
                          loading="lazy"
                          className="h-20 w-full object-cover"
                        />
                        <span className="absolute bottom-0 left-0 right-0 truncate bg-black/55 px-1 py-0.5 text-[9px] text-white">
                          {asset.media_type === "video" ? "▶ " : ""}
                          {labelOf(asset)}
                        </span>
                        {isSelected && (
                          <span className="absolute right-1 top-1 rounded-full bg-[#224C66] p-0.5 text-white">
                            <Check className="h-3 w-3" />
                          </span>
                        )}
                      </button>
                    );
                  })}
                  {!visible.length && (
                    <p className="col-span-full py-6 text-center text-[11px] text-[#6C6459]">
                      Nessun elemento con questi filtri.
                    </p>
                  )}
                </div>
              )}
            </section>
          )}

          {(mode === "upload_only" || mode === "mixed") && (
            <section className="rounded-2xl border border-dashed border-[#DDD4C4] bg-white p-3">
              <div className="flex flex-wrap items-center gap-2">
                <UploadCloud className="h-4 w-4 text-[#224C66]" />
                <span className="text-xs font-medium text-[#1D1B18]">Carica foto e video nuovi</span>
                <label className="ml-auto cursor-pointer rounded-lg bg-[#1D1B18] px-3 py-1.5 text-[11px] font-medium text-[#FAF7F2] hover:bg-[#224C66]">
                  {uploading ? "Carico…" : "Scegli i file"}
                  <input
                    type="file"
                    multiple
                    accept="image/*,video/*"
                    className="hidden"
                    disabled={uploading}
                    onChange={(e) => {
                      if (e.target.files) void doUpload(e.target.files);
                      e.currentTarget.value = "";
                    }}
                  />
                </label>
              </div>
              <p className="mt-1 text-[10px] text-[#6C6459]">
                Gli originali vengono copiati nel Media Vault e restano intatti.
              </p>
              {uploaded.length > 0 && (
                <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-5">
                  {uploaded.map((asset) => (
                    <div key={asset.id} className="overflow-hidden rounded-xl border border-[#EDE6D9]">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={`/api/vault/${asset.id}/thumb`}
                        alt={labelOf(asset)}
                        className="h-20 w-full object-cover"
                      />
                      <div className="flex items-center gap-1 bg-white px-1 py-0.5 text-[9px] text-[#4A443C]">
                        {asset.media_type === "video" ? (
                          <Film className="h-2.5 w-2.5" />
                        ) : (
                          <ImageIcon className="h-2.5 w-2.5" />
                        )}
                        <span className="truncate">{labelOf(asset)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          {mode === "mixed" && (
            <section className="rounded-2xl border border-[#EDE6D9] bg-white p-3">
              <div className="text-[11px] font-medium text-[#1D1B18]">Priorità nella combinazione</div>
              <div className="mt-1.5 flex flex-wrap gap-2">
                {(
                  [
                    ["archive_first", "Prima l'archivio, poi i nuovi"],
                    ["uploads_first", "Prima i nuovi, poi l'archivio"],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setPriority(id)}
                    className={`rounded-lg px-2.5 py-1 text-[11px] ${
                      priority === id
                        ? "bg-[#224C66] text-white"
                        : "border border-[#DDD4C4] text-[#4A443C] hover:border-[#9A8052]"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="mt-1.5 text-[10px] text-[#6C6459]">
                Nessun doppione: lo stesso file non viene usato due volte anche se compare in entrambi.
              </p>
            </section>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#EDE6D9] px-5 py-3">
          <span className="text-[11px] text-[#6C6459]">
            {mode === "vault_only"
              ? auto
                ? "Selezione automatica dall'archivio"
                : `${selected.length} materiali scelti`
              : mode === "upload_only"
                ? `${uploaded.length} nuovi materiali`
                : `Combinata · ${selected.length} archivio + ${uploaded.length} nuovi`}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-xl border border-[#DDD4C4] px-3 py-1.5 text-[11px] text-[#4A443C]"
            >
              Annulla
            </button>
            <button
              type="button"
              onClick={confirm}
              disabled={!canConfirm || uploading}
              className="rounded-xl bg-[#1D1B18] px-4 py-1.5 text-[11px] font-medium text-[#FAF7F2] disabled:opacity-40"
            >
              Continua con questi materiali
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default MediaSourceWizard;
