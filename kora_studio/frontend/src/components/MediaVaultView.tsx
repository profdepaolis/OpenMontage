"use client";

import React, { useState } from "react";
import {
  Search,
  Upload,
  ShieldCheck,
  Crop,
  Music,
  Video,
  Image as ImageIcon,
  Film,
  Sparkles,
} from "lucide-react";
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
  const [vision, setVision] = useState<{
    current_vision_signature: Record<string, string> | null;
    counts: Record<string, number>;
    total: number;
  } | null>(null);
  const [visionBusy, setVisionBusy] = useState(false);
  const [visionMsg, setVisionMsg] = useState<string | null>(null);

  const loadVision = async () => {
    try {
      setVision(await koraApi.visionStatus());
    } catch {
      setVision(null);
    }
  };

  const handleVisionReanalyze = async () => {
    setVisionBusy(true);
    setVisionMsg(null);
    try {
      const res = await koraApi.visionReanalyze(true);
      setVisionMsg(
        `Analisi Vault: ${res.analyzed ?? 0} asset elaborati, ${res.verified ?? 0} verificati, ` +
          `${res.deterministic ?? 0} deterministici, ${res.stale ?? 0} da aggiornare.`
      );
      await loadVision();
    } catch (e) {
      setVisionMsg((e as Error).message);
    } finally {
      setVisionBusy(false);
    }
  };
  const [selectedCategory, setSelectedCategory] = useState("Tutti");
  const [searchResults, setSearchResults] = useState<MediaAsset[] | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  // Multi-engine AI Video Lab state (Google Veo / Flow, fal.ai Wan/Kling/LTX, Qwen/Wan, HuggingFace)
  const [showVeoLab, setShowVeoLab] = useState(false);
  const [videoEngine, setVideoEngine] = useState<string>("google_veo_api");
  const [videoModel, setVideoModel] = useState<string>("");
  const [veoMode, setVeoMode] = useState<"image_to_video" | "text_to_video">("image_to_video");
  const [veoAssetId, setVeoAssetId] = useState("");
  const [veoDuration, setVeoDuration] = useState(5);
  const [veoMotion, setVeoMotion] = useState("lento movimento avanti, leggera brezza naturale");
  const [veoPrompt, setVeoPrompt] = useState("");
  const [veoAuthorized, setVeoAuthorized] = useState(false);
  const [veoBusy, setVeoBusy] = useState(false);

  const ENGINE_OPTIONS: Array<{
    id: string;
    label: string;
    models: Array<{ id: string; label: string }>;
    costLabel: string;
  }> = [
    {
      id: "google_veo_api",
      label: "Google Veo / Flow (API ufficiale Gemini)",
      models: [{ id: "", label: "Veo 2.0 / Veo 3.0 (auto)" }],
      costLabel: `~${(0.35 * veoDuration).toFixed(2)} € stimati`,
    },
    {
      id: "fal_ai",
      label: "fal.ai (Wan 2.1 · Kling 1.6 · LTX-Video)",
      models: [
        { id: "", label: "Alibaba Wan 2.1 (auto I2V/T2V · ~0.20 €)" },
        { id: "fal-ai/ltx-video", label: "LTX-Video veloce (~0.05 €)" },
        { id: "fal-ai/kling-video/v1.6/standard/image-to-video", label: "Kling 1.6 I2V (~0.30 €)" },
        { id: "fal-ai/kling-video/v1.6/standard/text-to-video", label: "Kling 1.6 T2V (~0.30 €)" },
      ],
      costLabel: videoModel.includes("ltx") ? "~0.05 €" : videoModel.includes("kling") ? "~0.30 €" : "~0.20 €",
    },
    {
      id: "qwen_wan_api",
      label: "Alibaba Qwen / Wan 2.1 (DashScope)",
      models: [{ id: "", label: "wan2.1-i2v/t2v-turbo (~0.22 €)" }],
      costLabel: "~0.22 € stimati",
    },
    {
      id: "huggingface_api",
      label: "Hugging Face (Wan 2.1 · CogVideoX)",
      models: [
        { id: "", label: "Wan-AI/Wan2.1 (auto I2V/T2V)" },
        { id: "THUDM/CogVideoX-5b", label: "CogVideoX-5b" },
      ],
      costLabel: "Quota HF (0 € se free tier)",
    },
  ];
  const currentEngineMeta = ENGINE_OPTIONS.find((e) => e.id === videoEngine) || ENGINE_OPTIONS[0];

  const imageAssets = assets.filter((a) => a.media_type === "image" && a.category !== "Logo");
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

  const handleRunVeo = async (e: React.FormEvent) => {
    e.preventDefault();
    setVeoBusy(true);
    setStatusMsg(null);
    try {
      const targetId = veoAssetId || imageAssets[0]?.id || null;
      const res = await koraApi.runAIVideoLab({
        engine: videoEngine,
        model: videoModel || null,
        mode: veoMode,
        prompt: veoPrompt,
        duration_sec: veoDuration,
        asset_id: veoMode === "image_to_video" ? targetId : null,
        camera_motion: veoMotion,
        explicit_authorization: veoAuthorized,
      });
      if (res.status === "completed") {
        setStatusMsg(
          `Clip generata (${String(res.model || videoEngine)}) e salvata nel Media Vault (${String(res.authenticity_status)}) — verifica umana richiesta prima della pubblicazione.`
        );
        onRefresh();
      } else {
        setStatusMsg(
          String(
            res.reason ||
              (Array.isArray(res.violations) ? res.violations.join(" · ") : "Operazione non avviata")
          )
        );
      }
    } catch (err) {
      setStatusMsg((err as Error).message);
    } finally {
      setVeoBusy(false);
    }
  };

  const handleCopyFlowPrompt = async () => {
    const selectedAsset = imageAssets.find((a) => a.id === (veoAssetId || imageAssets[0]?.id));
    const built =
      veoMode === "image_to_video" && selectedAsset
        ? `Photorealistic slow subtle animation of the exact reference photograph (${selectedAsset.filename}). Camera motion: ${veoMotion}. Keep architecture, pergola geometry, fixed furniture placement, signs, and horizon strictly unchanged. Only gentle breeze on fabric/foliage, soft water ripples, and natural Mediterranean light. Do not invent pools, buildings, terraces, or extra objects. ${veoPrompt}`.trim()
        : `Mediterranean luxury coastal atmosphere, natural sunlight, calm sea breeze, warm sand and neutral linen tones, photorealistic cinematic 9:16 video. Non-documentary atmospheric scene. ${veoPrompt}`.trim();
    try {
      await navigator.clipboard.writeText(built);
      setStatusMsg(
        "Prompt KORA copiato! Incollalo su Google Flow (labs.google/fx/tools/flow) con i tuoi crediti Gemini Plus, scarica l'MP4 e caricalo qui con 'Carica media'."
      );
    } catch {
      setStatusMsg(`Prompt KORA pronto da copiare: ${built}`);
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
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setShowVeoLab((v) => !v)}
            className="flex items-center gap-2 rounded-xl border border-[#224C66] bg-white px-4 py-2.5 text-xs font-medium text-[#224C66] hover:bg-[#224C66]/5"
          >
            <Film className="h-4 w-4" />
            AI Video Lab (Veo / Wan / Kling / LTX)
          </button>
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
      </div>

      {/* Visual Intelligence provenance */}
      <div className="rounded-2xl border border-[#DDD4C4] bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-[#1D1B18]">Visual Intelligence · provenienza</h3>
            <p className="text-[11px] text-[#6C6459]">
              {vision?.current_vision_signature
                ? `Modello visivo attivo: ${vision.current_vision_signature.provider}/${vision.current_vision_signature.model}`
                : "Nessun modello con visione verificata: analisi dichiarate come deterministiche."}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void loadVision()}
              className="rounded-xl border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-2 text-[11px] font-medium text-[#1D1B18]"
            >
              Stato analisi
            </button>
            <button
              type="button"
              onClick={() => void handleVisionReanalyze()}
              disabled={visionBusy}
              className="rounded-xl bg-[#1D1B18] px-3 py-2 text-[11px] font-medium text-white disabled:opacity-50"
            >
              {visionBusy ? "Rianalisi…" : "Rianalizza tutto il Vault"}
            </button>
          </div>
        </div>
        {vision && (
          <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-[#4A443C]">
            <span>verificate: {vision.counts.verified ?? 0}</span>
            <span>deterministiche: {vision.counts.deterministic ?? 0}</span>
            <span>da aggiornare: {vision.counts.stale ?? 0}</span>
            <span>non disponibili: {vision.counts.unavailable ?? 0}</span>
            <span>totale foto: {vision.total}</span>
          </div>
        )}
        {visionMsg && <p className="mt-2 text-[11px] text-[#224C66]">{visionMsg}</p>}
      </div>

      {/* Google Veo / Flow Lab */}
      {showVeoLab && (
        <form
          onSubmit={handleRunVeo}
          className="rounded-2xl border border-[#224C66]/40 bg-white p-5 shadow-sm space-y-4"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="flex items-center gap-2 text-sm font-semibold text-[#1D1B18]">
                <Sparkles className="h-4 w-4 text-[#9A8052]" />
                AI Media Lab · Clip Video Multi-Motore (Modalità C &amp; D)
              </h3>
              <p className="text-xs text-[#6C6459]">
                Google Flow usa sotto il cofano <strong>Google Veo</strong>. Puoi scegliere tra Veo, Alibaba Wan 2.1 (famiglia Qwen), Kling 1.6, LTX-Video o Hugging Face.
              </p>
            </div>
            <div className="flex rounded-xl border border-[#DDD4C4] p-0.5 text-xs">
              <button
                type="button"
                onClick={() => setVeoMode("image_to_video")}
                className={`rounded-lg px-3 py-1.5 ${
                  veoMode === "image_to_video" ? "bg-[#1D1B18] text-white" : "text-[#6C6459]"
                }`}
              >
                Image-to-Video (Mod. C)
              </button>
              <button
                type="button"
                onClick={() => setVeoMode("text_to_video")}
                className={`rounded-lg px-3 py-1.5 ${
                  veoMode === "text_to_video" ? "bg-[#1D1B18] text-white" : "text-[#6C6459]"
                }`}
              >
                Text-to-Video (Mod. D)
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <label className="block text-xs text-[#6C6459]">
              Motore Video AI
              <select
                value={videoEngine}
                onChange={(e) => {
                  setVideoEngine(e.target.value);
                  setVideoModel("");
                }}
                className="mt-1 w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-2 text-xs text-[#1D1B18]"
              >
                {ENGINE_OPTIONS.map((eng) => (
                  <option key={eng.id} value={eng.id}>
                    {eng.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs text-[#6C6459]">
              Modello
              <select
                value={videoModel}
                onChange={(e) => setVideoModel(e.target.value)}
                className="mt-1 w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-2 text-xs text-[#1D1B18]"
              >
                {currentEngineMeta.models.map((m) => (
                  <option key={m.id || "auto"} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {veoMode === "image_to_video" && (
              <label className="block text-xs text-[#6C6459]">
                Foto autentica di partenza
                <select
                  value={veoAssetId || imageAssets[0]?.id || ""}
                  onChange={(e) => setVeoAssetId(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-2 text-xs text-[#1D1B18]"
                >
                  {imageAssets.map((img) => (
                    <option key={img.id} value={img.id}>
                      {img.filename} ({img.category})
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="block text-xs text-[#6C6459]">
              Durata clip
              <select
                value={veoDuration}
                onChange={(e) => setVeoDuration(Number(e.target.value))}
                className="mt-1 w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-2 text-xs text-[#1D1B18]"
              >
                <option value={5}>5 secondi (~{(0.35 * 5).toFixed(2)} € stimati)</option>
                <option value={8}>8 secondi (~{(0.35 * 8).toFixed(2)} € stimati)</option>
              </select>
            </label>
            {veoMode === "image_to_video" && (
              <label className="block text-xs text-[#6C6459]">
                Movimento camera
                <input
                  type="text"
                  value={veoMotion}
                  onChange={(e) => setVeoMotion(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-2 text-xs text-[#1D1B18]"
                />
              </label>
            )}
          </div>

          <label className="block text-xs text-[#6C6459]">
            {veoMode === "image_to_video"
              ? "Indicazione aggiuntiva (es. lieve brezza sulle frange dell'ombrellone)"
              : "Descrizione scena illustrativa (es. onde calme del mare Ionio all'alba)"}
            <input
              type="text"
              value={veoPrompt}
              onChange={(e) => setVeoPrompt(e.target.value)}
              placeholder={
                veoMode === "image_to_video"
                  ? "Solo movimenti naturali coerenti con la foto..."
                  : "Atmosfera mediterranea non documentaria..."
              }
              className="mt-1 w-full rounded-xl border border-[#DDD4C4] bg-[#FAF7F2] px-3.5 py-2 text-xs text-[#1D1B18]"
            />
          </label>

          <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
            <label className="flex items-center gap-2 text-xs text-[#1D1B18]">
              <input
                type="checkbox"
                checked={veoAuthorized}
                onChange={(e) => setVeoAuthorized(e.target.checked)}
                className="rounded border-[#DDD4C4]"
              />
              <span>
                Autorizzo la chiamata al motore selezionato ({currentEngineMeta.costLabel}) nel rispetto del budget impostato.
              </span>
            </label>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleCopyFlowPrompt}
                className="rounded-xl border border-[#DDD4C4] bg-[#FAF7F2] px-3.5 py-2 text-xs font-medium text-[#1D1B18] hover:border-[#9A8052]"
              >
                Copia prompt per Google Flow web (0 €)
              </button>
              <button
                type="submit"
                disabled={veoBusy}
                className="rounded-xl bg-[#224C66] px-4 py-2 text-xs font-medium text-white hover:bg-[#1D1B18] disabled:opacity-50"
              >
                {veoBusy ? "Generazione video in corso (1–3 min)..." : "Genera Clip AI"}
              </button>
            </div>
          </div>
        </form>
      )}

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
