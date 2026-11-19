"use client";

import React, { useState } from "react";
import { Sparkles, PenLine, ImagePlus, History, Play, CheckCircle2, AlertCircle } from "lucide-react";
import type {
  CreativeConcept,
  MediaAsset,
  ProductionMode,
  ProjectRecord,
  VoiceOption,
} from "@/lib/types";
import { koraApi } from "@/lib/api";

interface HomeHubProps {
  voices: VoiceOption[];
  recentProjects: ProjectRecord[];
  onConceptsReady: (
    concepts: CreativeConcept[],
    theme: string,
    mode: string
  ) => void;
  onStartProduction: (
    concept: CreativeConcept,
    theme: string,
    mode: string,
    voiceId: string,
    prodMode: ProductionMode
  ) => Promise<void>;
  onOpenProject: (projectId: string) => void;
  onVaultUpdated: () => void;
}

export function HomeHub({
  voices,
  recentProjects,
  onConceptsReady,
  onStartProduction,
  onOpenProject,
  onVaultUpdated,
}: HomeHubProps) {
  const [activeAction, setActiveAction] = useState<"surprise" | "theme" | "photos" | "resume" | null>(null);
  const [themeText, setThemeText] = useState("");
  const [durationSec, setDurationSec] = useState(20);
  const [presetOverride, setPresetOverride] = useState("AUTO_CONCEPT");
  const [dragActive, setDragActive] = useState(false);
  const [voiceId, setVoiceId] = useState("brando");
  const [prodMode, setProdMode] = useState<ProductionMode>("AUTO");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposals, setProposals] = useState<CreativeConcept[]>([]);
  const [uploadedInfo, setUploadedInfo] = useState<MediaAsset[]>([]);

  const handleSurprise = async () => {
    setActiveAction("surprise");
    setLoading(true);
    setError(null);
    try {
      const res = await koraApi.surpriseMe(durationSec);
      setProposals(res.concepts);
      onConceptsReady(res.concepts, "Sorprendimi · Proposta KORA", "surprise");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleThemeSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!themeText.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await koraApi.fromTheme(themeText.trim(), durationSec);
      setProposals(res.concepts);
      onConceptsReady(res.concepts, themeText.trim(), "theme");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handlePhotoUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const res = await koraApi.uploadMedia(files);
      setUploadedInfo(res.uploaded);
      setProposals(res.suggested_concepts);
      onVaultUpdated();
      if (res.errors.length > 0) {
        setError(res.errors.map((x) => `${x.filename}: ${x.error}`).join(" · "));
      }
      onConceptsReady(res.suggested_concepts, "Creazione dai materiali caricati", "photos");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-10 py-6">
      {/* Zero-Friction Central Prompt */}
      <section className="rounded-3xl border border-[#DDD4C4] bg-white/80 p-8 shadow-sm backdrop-blur-sm md:p-12">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-xs font-medium uppercase tracking-[0.22em] text-[#9A8052]">
            KORA Beach Club · Policoro · The Art of Being
          </p>
          <h1 className="font-editorial mt-3 text-3xl font-normal text-[#1D1B18] md:text-5xl">
            Che storia raccontiamo oggi?
          </h1>
          <p className="mt-3 text-sm text-[#6C6459]">
            Descrivi l&apos;idea o scegli un punto di partenza. KORA STUDIO cura regia, materiali autentici, voce, montaggio 9:16 e controllo qualità.
          </p>
        </div>

        {/* 4 Primary Actions */}
        <div className="mt-9 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <button
            type="button"
            onClick={handleSurprise}
            className={`group rounded-2xl border p-5 text-left transition ${
              activeAction === "surprise"
                ? "border-[#224C66] bg-[#224C66]/5"
                : "border-[#DDD4C4] bg-[#FAF7F2] hover:border-[#9A8052]"
            }`}
          >
            <Sparkles className="h-5 w-5 text-[#9A8052]" />
            <div className="mt-3 font-medium text-[#1D1B18]">1. Sorprendimi</div>
            <div className="mt-1 text-xs text-[#6C6459]">
              3 idee originali basate su archivio, stagione e identità KORA.
            </div>
          </button>

          <button
            type="button"
            onClick={() => setActiveAction("theme")}
            className={`group rounded-2xl border p-5 text-left transition ${
              activeAction === "theme"
                ? "border-[#224C66] bg-[#224C66]/5"
                : "border-[#DDD4C4] bg-[#FAF7F2] hover:border-[#9A8052]"
            }`}
          >
            <PenLine className="h-5 w-5 text-[#224C66]" />
            <div className="mt-3 font-medium text-[#1D1B18]">2. Crea da un tema</div>
            <div className="mt-1 text-xs text-[#6C6459]">
              Anche una sola frase libera diventa un Reel completo.
            </div>
          </button>

          <button
            type="button"
            onClick={() => setActiveAction("photos")}
            className={`group rounded-2xl border p-5 text-left transition ${
              activeAction === "photos"
                ? "border-[#224C66] bg-[#224C66]/5"
                : "border-[#DDD4C4] bg-[#FAF7F2] hover:border-[#9A8052]"
            }`}
          >
            <ImagePlus className="h-5 w-5 text-[#9A8052]" />
            <div className="mt-3 font-medium text-[#1D1B18]">3. Crea dalle mie foto</div>
            <div className="mt-1 text-xs text-[#6C6459]">
              Trascina foto o video: analisi qualità e concept automatici.
            </div>
          </button>

          <button
            type="button"
            onClick={() => setActiveAction("resume")}
            className={`group rounded-2xl border p-5 text-left transition ${
              activeAction === "resume"
                ? "border-[#224C66] bg-[#224C66]/5"
                : "border-[#DDD4C4] bg-[#FAF7F2] hover:border-[#9A8052]"
            }`}
          >
            <History className="h-5 w-5 text-[#224C66]" />
            <div className="mt-3 font-medium text-[#1D1B18]">4. Riprendi un progetto</div>
            <div className="mt-1 text-xs text-[#6C6459]">
              Continua una bozza o rivedi un Reel già prodotto.
            </div>
          </button>
        </div>

        {/* Minimal Production Controls */}
        <div className="mt-6 flex flex-wrap items-center justify-between gap-4 border-t border-[#EDE6D9] pt-5 text-xs text-[#6C6459]">
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2">
              <span className="font-medium text-[#1D1B18]">Modalità:</span>
              <select
                value={prodMode}
                onChange={(e) => setProdMode(e.target.value as ProductionMode)}
                className="rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs text-[#1D1B18]"
              >
                <option value="AUTO">AUTO (predefinita — fino all&apos;anteprima)</option>
                <option value="GUIDATA">GUIDATA (approvazione per fasi)</option>
                <option value="REGIA_COMPLETA">REGIA COMPLETA (controllo scene)</option>
              </select>
            </label>
            <label className="flex items-center gap-2">
              <span className="font-medium text-[#1D1B18]">Voce:</span>
              <select
                value={voiceId}
                onChange={(e) => setVoiceId(e.target.value)}
                className="rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs text-[#1D1B18]"
              >
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label}
                    {v.id !== "none"
                      ? v.elevenlabs_configured
                        ? " (ElevenLabs pronto)"
                        : v.piper_local_available
                          ? " (Piper/Fallback)"
                          : " (Fallback per testi nuovi)"
                      : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2">
              <span className="font-medium text-[#1D1B18]">Durata:</span>
              <select
                value={durationSec}
                onChange={(e) => setDurationSec(Number(e.target.value))}
                className="rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs text-[#1D1B18]"
              >
                <option value={15}>15 s (375 frame)</option>
                <option value={20}>20 s (500 frame)</option>
                <option value={25}>25 s (625 frame)</option>
                <option value={30}>30 s (750 frame)</option>
              </select>
            </label>
            <label className="flex items-center gap-2">
              <span className="font-medium text-[#1D1B18]">Stile:</span>
              <select
                value={presetOverride}
                onChange={(e) => setPresetOverride(e.target.value)}
                className="rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs text-[#1D1B18]"
              >
                <option value="AUTO_CONCEPT">Automatico (dal concept)</option>
                <option value="KORA Cinematic Identity">KORA Cinematic Identity</option>
                <option value="KORA Experience">KORA Experience</option>
                <option value="KORA Seasonal">KORA Seasonal</option>
                <option value="KORA Editorial">KORA Editorial</option>
              </select>
            </label>
          </div>
          <span>1080×1920 · 9:16 · 25 fps · Rec.709</span>
        </div>

        {/* Action Drawers */}
        {activeAction === "theme" && (
          <form onSubmit={handleThemeSubmit} className="mt-6 space-y-3 rounded-2xl bg-[#FAF7F2] p-5 border border-[#DDD4C4]">
            <label className="block text-xs font-medium uppercase tracking-wider text-[#6C6459]">
              Tema libero
            </label>
            <div className="flex flex-col gap-3 sm:flex-row">
              <input
                type="text"
                value={themeText}
                onChange={(e) => setThemeText(e.target.value)}
                placeholder='Es. "Il piacere di stare insieme davanti al mare." oppure "Più emozionante, con poche parole."'
                className="flex-1 rounded-xl border border-[#DDD4C4] bg-white px-4 py-3 text-sm text-[#1D1B18] focus:border-[#224C66] focus:outline-none"
              />
              <button
                type="submit"
                disabled={loading || !themeText.trim()}
                className="rounded-xl bg-[#1D1B18] px-6 py-3 text-sm font-medium text-[#FAF7F2] transition hover:bg-[#224C66] disabled:opacity-50"
              >
                {loading ? "Ideazione..." : "Crea Concept"}
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {[
                "Il piacere di stare insieme davanti al mare.",
                "Vorrei un Reel più emozionante, con poche parole.",
                "La colazione lenta vista mare al KORA.",
              ].map((sample) => (
                <button
                  key={sample}
                  type="button"
                  onClick={() => setThemeText(sample)}
                  className="rounded-full border border-[#DDD4C4] bg-white px-3 py-1 text-xs text-[#6C6459] hover:border-[#9A8052] hover:text-[#1D1B18]"
                >
                  {sample}
                </button>
              ))}
            </div>
          </form>
        )}

        {activeAction === "photos" && (
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragActive(false);
              handlePhotoUpload(e.dataTransfer.files);
            }}
            className={`mt-6 rounded-2xl border-2 border-dashed p-6 text-center transition ${
              dragActive ? "border-[#224C66] bg-[#224C66]/5" : "border-[#9A8052]/60 bg-[#FAF7F2]"
            }`}
          >
            <input
              id="home-photo-upload"
              type="file"
              multiple
              accept=".jpg,.jpeg,.png,.webp,.heic,.mp4,.mov,.m4v"
              className="hidden"
              onChange={(e) => handlePhotoUpload(e.target.files)}
            />
            <label htmlFor="home-photo-upload" className="cursor-pointer">
              <ImagePlus className="mx-auto h-8 w-8 text-[#9A8052]" />
              <p className="mt-2 text-sm font-medium text-[#1D1B18]">
                Clicca o trascina qui foto e clip autentiche
              </p>
              <p className="mt-1 text-xs text-[#6C6459]">
                Gli originali restano intatti. KORA STUDIO analizza qualità, orientamento, duplicati e propone i concept compatibili.
              </p>
            </label>
            {uploadedInfo.length > 0 && (
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                {uploadedInfo.map((u) => (
                  <span key={u.id} className="rounded-full bg-white px-3 py-1 text-xs border border-[#DDD4C4]">
                    {u.filename} · {u.category} ({u.technical_quality}/100)
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {activeAction === "resume" && (
          <div className="mt-6 space-y-2 rounded-2xl border border-[#DDD4C4] bg-[#FAF7F2] p-5">
            {recentProjects.length === 0 ? (
              <p className="text-sm text-[#6C6459]">Nessun progetto ancora salvato.</p>
            ) : (
              recentProjects.slice(0, 5).map((p) => (
                <div
                  key={p.id}
                  className="flex items-center justify-between rounded-xl border border-[#DDD4C4] bg-white px-4 py-3"
                >
                  <div>
                    <div className="text-sm font-medium text-[#1D1B18]">{p.title}</div>
                    <div className="text-xs text-[#6C6459]">
                      {p.status} · {p.preset} · v{p.current_version}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => onOpenProject(p.id)}
                    className="rounded-lg bg-[#224C66] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#1D1B18]"
                  >
                    Apri
                  </button>
                </div>
              ))
            )}
          </div>
        )}

        {error && (
          <div className="mt-4 flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-800">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
      </section>

      {/* 3 Proposals ready for 1-click Launch */}
      {proposals.length > 0 && (
        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-editorial text-2xl text-[#1D1B18]">
              Tre proposte creative KORA
            </h2>
            <span className="text-xs text-[#6C6459]">
              Seleziona una proposta per avviare la produzione ({prodMode})
            </span>
          </div>
          <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
            {proposals.map((c) => (
              <div
                key={c.id}
                className="flex flex-col justify-between rounded-2xl border border-[#DDD4C4] bg-white p-6 shadow-sm"
              >
                <div className="space-y-3">
                  <div className="flex items-center justify-between text-[11px] uppercase tracking-wider text-[#9A8052]">
                    <span>{c.primary_emotion}</span>
                    <span>{c.duration_sec}s</span>
                  </div>
                  <h3 className="font-editorial text-xl text-[#1D1B18]">{c.title}</h3>
                  <p className="text-sm text-[#4A443C] leading-relaxed">{c.message}</p>
                  <div className="rounded-xl bg-[#FAF7F2] p-3 text-xs text-[#6C6459]">
                    <span className="font-medium text-[#1D1B18]">Hook:</span> {c.hook}
                  </div>
                  <div className="flex flex-wrap gap-2 text-[11px]">
                    <span className="rounded-full bg-[#EFE8DB] px-2.5 py-0.5 text-[#1D1B18]">
                      Fattibilità {Math.round(c.feasibility_score * 100)}%
                    </span>
                    <span className="rounded-full bg-[#224C66]/10 px-2.5 py-0.5 text-[#224C66]">
                      Originalità {Math.round(c.originality_score * 100)}%
                    </span>
                    <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-emerald-800">
                      {c.available_asset_ids.length} asset reali pronti
                    </span>
                  </div>
                  {c.missing_materials.length > 0 && (
                    <div className="text-[11px] text-amber-700">
                      Mancanti: {c.missing_materials.join(", ")}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  disabled={loading}
                  onClick={async () => {
                    setLoading(true);
                    try {
                      await onStartProduction(
                        presetOverride === "AUTO_CONCEPT"
                          ? c
                          : { ...c, preset: presetOverride },
                        themeText.trim() || c.central_idea,
                        activeAction || "theme",
                        voiceId,
                        prodMode
                      );
                    } finally {
                      setLoading(false);
                    }
                  }}
                  className="mt-5 flex w-full items-center justify-center gap-2 rounded-xl bg-[#1D1B18] px-4 py-2.5 text-sm font-medium text-[#FAF7F2] transition hover:bg-[#224C66]"
                >
                  {prodMode === "AUTO" ? (
                    <>
                      <Play className="h-4 w-4" /> Produci Reel ora
                    </>
                  ) : (
                    <>
                      <CheckCircle2 className="h-4 w-4" /> Apri in Creative Studio
                    </>
                  )}
                </button>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
