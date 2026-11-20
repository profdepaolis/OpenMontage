"use client";

import React, { useState } from "react";
import {
  Sparkles,
  PenLine,
  ImagePlus,
  History,
  Play,
  SlidersHorizontal,
  AlertCircle,
  Volume2,
  Wand2,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import type {
  CreativeConcept,
  CreativeRequestPayload,
  HyperframesConfig,
  MediaAsset,
  ProductionMode,
  ProjectRecord,
  ThemeSuggestionResponse,
  VoiceOption,
} from "@/lib/types";
import { koraApi } from "@/lib/api";

const IDEATION_ENGINE_LABELS: Record<string, string> = {
  ollama_local: "Ollama locale · 0 €",
  gemini_api: "Google Gemini",
  openai_api: "OpenAI",
  claude_api: "Anthropic Claude",
  deepseek_api: "DeepSeek",
  deterministic_offline_fallback: "Motore locale deterministico · 0 €",
};

const CLOUD_IDEATION_ENGINES = ["gemini_api", "openai_api", "claude_api", "deepseek_api"];

function ideationEngineLabel(engine: string): string {
  if (engine.startsWith("kora_deterministic")) {
    return IDEATION_ENGINE_LABELS.deterministic_offline_fallback;
  }
  return IDEATION_ENGINE_LABELS[engine] ?? engine;
}

function isLocalFallbackEngine(engine: string): boolean {
  return !CLOUD_IDEATION_ENGINES.includes(engine) && engine !== "ollama_local";
}

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
    prodMode: ProductionMode,
    captionStyle?: string,
    hyperframes?: Partial<HyperframesConfig>,
    customizeFirst?: boolean
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
  const [activeAction, setActiveAction] = useState<"suggest" | "idea" | "media" | "resume" | null>(
    null
  );
  const [themeText, setThemeText] = useState("");
  const [objective, setObjective] = useState("Valorizzare l'identità e l'ospitalità KORA");
  const [durationSec, setDurationSec] = useState(20);
  const [voiceId, setVoiceId] = useState("brando");
  const [voicePreviewUrl, setVoicePreviewUrl] = useState<string | null>(null);

  // Advanced / Technical controls (collapsed by default per Section 8 UX)
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [prodMode, setProdMode] = useState<ProductionMode>("AUTO");
  const [presetOverride, setPresetOverride] = useState("AUTO_CONCEPT");
  const [mediaSelection, setMediaSelection] = useState("vault_auto");
  const [generationPolicy, setGenerationPolicy] = useState("authentic_first");
  const [budgetEur, setBudgetEur] = useState(0);
  const [customInstructions, setCustomInstructions] = useState("");
  const [hyperframes, setHyperframes] = useState<HyperframesConfig>({
    caption_style: "serif_ivory",
    title_card_intro: "none",
    grain_overlay: "none",
    vignette: "soft_luxury",
    letterbox: "none",
    motion_pace: "balanced",
  });

  const [recommendation, setRecommendation] = useState<ThemeSuggestionResponse | null>(null);
  const [engineInfo, setEngineInfo] = useState<{ engine?: string; fallback?: string | null } | null>(
    null
  );
  const [selectedConceptId, setSelectedConceptId] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposals, setProposals] = useState<CreativeConcept[]>([]);
  const [uploadedInfo, setUploadedInfo] = useState<MediaAsset[]>([]);

  const buildRequestPayload = (overrideTheme?: string): Partial<CreativeRequestPayload> => ({
    theme: (overrideTheme ?? themeText).trim(),
    objective: objective.trim(),
    duration_sec: durationSec,
    voice_id: voiceId,
    media_selection: mediaSelection,
    generation_policy: generationPolicy,
    budget_eur: budgetEur,
    custom_instructions: customInstructions.trim(),
  });

  const handleSuggestThemes = async () => {
    setActiveAction("suggest");
    setLoading(true);
    setError(null);
    try {
      const res = await koraApi.suggestThemes(buildRequestPayload(""));
      setProposals(res.concepts);
      setEngineInfo({ engine: res.generation_engine, fallback: res.fallback_reason });
      if (res.concepts[0]) {
        setSelectedConceptId(res.concepts[0].id);
        if (res.concepts[0].objective) setObjective(res.concepts[0].objective);
        if (res.concepts[0].recommended_voice_id) setVoiceId(res.concepts[0].recommended_voice_id);
      }
      onConceptsReady(res.concepts, "Suggerisci un tema · Proposte KORA", "suggest");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleIdeaSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!themeText.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await koraApi.fromTheme(buildRequestPayload());
      setProposals(res.concepts);
      setEngineInfo({ engine: res.generation_engine, fallback: res.fallback_reason });
      if (res.recommendation) {
        setRecommendation(res.recommendation);
      }
      if (res.concepts[0]) {
        setSelectedConceptId(res.concepts[0].id);
      }
      onConceptsReady(res.concepts, themeText.trim(), "theme");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleRecommendHyperframes = async () => {
    setLoading(true);
    setError(null);
    try {
      const rec = await koraApi.recommendHyperframes(
        themeText.trim() || objective || "mare e ospitalità a Policoro",
        durationSec
      );
      setRecommendation(rec);
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
      onConceptsReady(res.suggested_concepts, "Creazione dai media caricati", "photos");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const selectConceptAndPrefill = (c: CreativeConcept) => {
    setSelectedConceptId(c.id);
    if (c.objective) setObjective(c.objective);
    if (c.recommended_voice_id) setVoiceId(c.recommended_voice_id);
    if (c.duration_sec) setDurationSec(c.duration_sec);
  };

  return (
    <div className="mx-auto max-w-6xl space-y-8 py-4">
      {/* Conversational Prompt Hub */}
      <section className="rounded-3xl border border-[#DDD4C4] bg-white/85 p-7 shadow-sm backdrop-blur-sm md:p-10">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-xs font-medium uppercase tracking-[0.22em] text-[#9A8052]">
            KORA Beach Club · Policoro · The Art of Being
          </p>
          <h1 className="font-editorial mt-3 text-3xl font-normal text-[#1D1B18] md:text-5xl">
            Che storia raccontiamo oggi?
          </h1>
          <p className="mt-2.5 text-sm text-[#6C6459]">
            Scegli come iniziare: <strong>Suggerisci un tema → Progetta il nuovo Reel → Realizza Reel</strong>.
          </p>
        </div>

        {/* 4 Primary Conversational Actions */}
        <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <button
            type="button"
            onClick={handleSuggestThemes}
            className={`group rounded-2xl border p-5 text-left transition ${
              activeAction === "suggest"
                ? "border-[#224C66] bg-[#224C66]/5"
                : "border-[#DDD4C4] bg-[#FAF7F2] hover:border-[#9A8052]"
            }`}
          >
            <Sparkles className="h-5 w-5 text-[#9A8052]" />
            <div className="mt-3 font-medium text-[#1D1B18]">1. Suggerisci un tema</div>
            <div className="mt-1 text-xs text-[#6C6459]">
              4 proposte originali dal Media Vault, stagione e storico KORA.
            </div>
          </button>

          <button
            type="button"
            onClick={() => setActiveAction("idea")}
            className={`group rounded-2xl border p-5 text-left transition ${
              activeAction === "idea"
                ? "border-[#224C66] bg-[#224C66]/5"
                : "border-[#DDD4C4] bg-[#FAF7F2] hover:border-[#9A8052]"
            }`}
          >
            <PenLine className="h-5 w-5 text-[#224C66]" />
            <div className="mt-3 font-medium text-[#1D1B18]">2. Parti dalla mia idea</div>
            <div className="mt-1 text-xs text-[#6C6459]">
              Inserisci un tema libero, obiettivo e durata desiderata.
            </div>
          </button>

          <button
            type="button"
            onClick={() => setActiveAction("media")}
            className={`group rounded-2xl border p-5 text-left transition ${
              activeAction === "media"
                ? "border-[#224C66] bg-[#224C66]/5"
                : "border-[#DDD4C4] bg-[#FAF7F2] hover:border-[#9A8052]"
            }`}
          >
            <ImagePlus className="h-5 w-5 text-[#9A8052]" />
            <div className="mt-3 font-medium text-[#1D1B18]">3. Crea dai miei media</div>
            <div className="mt-1 text-xs text-[#6C6459]">
              Trascina foto o clip: analisi multimodale e concept immediati.
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
            <div className="mt-3 font-medium text-[#1D1B18]">4. Riprendi una produzione</div>
            <div className="mt-1 text-xs text-[#6C6459]">
              Continua una bozza o rivedi un Reel con revisione conversazionale.
            </div>
          </button>
        </div>

        {/* Essential Decisions Only (Objective, Voice + Preview, Duration) */}
        <div className="mt-6 grid grid-cols-1 gap-3 border-t border-[#EDE6D9] pt-5 md:grid-cols-12 text-xs">
          <label className="md:col-span-5 space-y-1">
            <span className="font-medium text-[#1D1B18]">Obiettivo narrativo</span>
            <input
              type="text"
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              placeholder="Es. Raccontare il mattino lento o l'aperitivo vista mare"
              className="w-full rounded-xl border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-2 text-xs text-[#1D1B18]"
            />
          </label>
          <div className="md:col-span-4 space-y-1">
            <span className="font-medium text-[#1D1B18]">Voce narrante</span>
            <div className="flex items-center gap-2">
              <select
                value={voiceId}
                onChange={(e) => {
                  setVoiceId(e.target.value);
                  setVoicePreviewUrl(null);
                }}
                className="flex-1 rounded-xl border border-[#DDD4C4] bg-[#FAF7F2] px-2.5 py-2 text-xs text-[#1D1B18]"
              >
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label}
                    {v.id !== "none"
                      ? v.elevenlabs_configured
                        ? " (ElevenLabs pronto)"
                        : v.piper_local_available
                          ? " (Piper/Fallback)"
                          : " (Fallback)"
                      : ""}
                  </option>
                ))}
              </select>
              {voiceId !== "none" && (
                <button
                  type="button"
                  onClick={() =>
                    setVoicePreviewUrl(`/api/voices/${voiceId}/sample?engine=auto&t=${Date.now()}`)
                  }
                  className="inline-flex items-center gap-1 rounded-xl border border-[#DDD4C4] bg-white px-2.5 py-2 text-[11px] font-medium hover:border-[#9A8052]"
                >
                  <Volume2 className="h-3.5 w-3.5 text-[#9A8052]" /> Ascolta
                </button>
              )}
            </div>
          </div>
          <label className="md:col-span-3 space-y-1">
            <span className="font-medium text-[#1D1B18]">Durata target</span>
            <select
              value={durationSec}
              onChange={(e) => setDurationSec(Number(e.target.value))}
              className="w-full rounded-xl border border-[#DDD4C4] bg-[#FAF7F2] px-2.5 py-2 text-xs text-[#1D1B18]"
            >
              <option value={12}>12 s (ritmo rapido)</option>
              <option value={15}>15 s (compatto)</option>
              <option value={20}>20 s (standard KORA)</option>
              <option value={30}>30 s (racconto esteso)</option>
              <option value={45}>45 s (editoriale)</option>
              <option value={60}>60 s (documentario breve)</option>
            </select>
          </label>
        </div>

        {voicePreviewUrl && (
          <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-[#EDE6D9] bg-[#FAF7F2] px-4 py-2 text-xs">
            <span className="font-medium text-[#1D1B18]">Anteprima voce ({voiceId}):</span>
            <audio key={voicePreviewUrl} src={voicePreviewUrl} controls autoPlay className="h-8 flex-1 min-w-[200px]" />
          </div>
        )}

        {/* Collapsible Technical & Hyperframes Advanced Section */}
        <div className="mt-4">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-[#6C6459] hover:text-[#1D1B18]"
          >
            <Wand2 className="h-3.5 w-3.5 text-[#9A8052]" />
            Parametri avanzati &amp; Hyperframes ({hyperframes.caption_style.replace("hyperframes_", "HF ")})
            {showAdvanced ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>

          {showAdvanced && (
            <div className="mt-3 rounded-2xl border border-[#DDD4C4] bg-[#FAF7F2] p-4 space-y-4 text-xs">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <label className="space-y-1">
                  <span className="text-[11px] text-[#6C6459]">Modalità produzione</span>
                  <select
                    value={prodMode}
                    onChange={(e) => setProdMode(e.target.value as ProductionMode)}
                    className="w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs"
                  >
                    <option value="AUTO">AUTO (fino al rendering)</option>
                    <option value="GUIDATA">GUIDATA (approvazione scene)</option>
                    <option value="REGIA_COMPLETA">REGIA COMPLETA</option>
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-[11px] text-[#6C6459]">Preset KORA</span>
                  <select
                    value={presetOverride}
                    onChange={(e) => setPresetOverride(e.target.value)}
                    className="w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs"
                  >
                    <option value="AUTO_CONCEPT">Automatico (dal concept)</option>
                    <option value="KORA Cinematic Identity">KORA Cinematic Identity</option>
                    <option value="KORA Experience">KORA Experience</option>
                    <option value="KORA Seasonal">KORA Seasonal</option>
                    <option value="KORA Editorial">KORA Editorial</option>
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-[11px] text-[#6C6459]">Politica generazione media</span>
                  <select
                    value={generationPolicy}
                    onChange={(e) => {
                      setGenerationPolicy(e.target.value);
                      setMediaSelection(e.target.value === "conservative_only" ? "allow_crops" : "vault_auto");
                    }}
                    className="w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs"
                  >
                    <option value="authentic_first">Solo autentici → crop → I2V</option>
                    <option value="conservative_only">Solo originali + crop conservativi</option>
                    <option value="controlled_i2v">Consenti I2V vincolato se necessario</option>
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-[11px] text-[#6C6459]">Budget massimo richiesta (€)</span>
                  <input
                    type="number"
                    min="0"
                    max="20"
                    step="0.5"
                    value={budgetEur}
                    onChange={(e) => setBudgetEur(Math.max(0, Number(e.target.value)))}
                    className="w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs"
                  />
                </label>
              </div>

              <label className="block space-y-1">
                <span className="text-[11px] text-[#6C6459]">Istruzioni creative personalizzate</span>
                <input
                  type="text"
                  value={customInstructions}
                  onChange={(e) => setCustomInstructions(e.target.value)}
                  placeholder="Es. Evita primi piani sul tavolo, privilegia onde e luce radente."
                  className="w-full rounded-lg border border-[#DDD4C4] bg-white px-3 py-1.5 text-xs"
                />
              </label>

              <div className="border-t border-[#EDE6D9] pt-3 space-y-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-[#1D1B18]">
                    Opzioni Motore Hyperframes
                  </span>
                  <button
                    type="button"
                    onClick={handleRecommendHyperframes}
                    className="inline-flex items-center gap-1 rounded-lg bg-[#9A8052] px-3 py-1 text-[11px] font-medium text-white hover:bg-[#7e673e]"
                  >
                    <Sparkles className="h-3 w-3" /> Consiglia opzioni Hyperframes
                  </button>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  <label className="space-y-1">
                    <span className="text-[11px] text-[#6C6459]">Stile Caption</span>
                    <select
                      value={hyperframes.caption_style}
                      onChange={(e) => setHyperframes((h) => ({ ...h, caption_style: e.target.value }))}
                      className="w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs"
                    >
                      <option value="serif_ivory">KORA Classic Serif</option>
                      <option value="serif_minimal">Editorial Minimal</option>
                      <option value="clean_sans">Space Grotesk Clean</option>
                      <option value="hyperframes_kinetic">Hyperframes · Kinetic Gold</option>
                      <option value="hyperframes_glass">Hyperframes · Mediterranean Glass</option>
                      <option value="hyperframes_word_highlight">Hyperframes · Word Highlight</option>
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-[11px] text-[#6C6459]">Title Card Apertura</span>
                    <select
                      value={hyperframes.title_card_intro}
                      onChange={(e) => setHyperframes((h) => ({ ...h, title_card_intro: e.target.value }))}
                      className="w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs"
                    >
                      <option value="none">Nessuna Title Card</option>
                      <option value="editorial_serif">Editorial Serif Intro</option>
                      <option value="hyperframes_gold_line">Hyperframes · Gold Line Title</option>
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-[11px] text-[#6C6459]">Grana (grain-overlay)</span>
                    <select
                      value={hyperframes.grain_overlay}
                      onChange={(e) => setHyperframes((h) => ({ ...h, grain_overlay: e.target.value }))}
                      className="w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs"
                    >
                      <option value="none">Pulito</option>
                      <option value="subtle_35mm">Grana 35mm leggera</option>
                      <option value="warm_kodak">Pellicola calda Kodak</option>
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-[11px] text-[#6C6459]">Vignettatura</span>
                    <select
                      value={hyperframes.vignette}
                      onChange={(e) => setHyperframes((h) => ({ ...h, vignette: e.target.value }))}
                      className="w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs"
                    >
                      <option value="none">Nessuna</option>
                      <option value="soft_luxury">Luxury morbida</option>
                      <option value="deep_cinematic">Cinema intensa</option>
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-[11px] text-[#6C6459]">Cornice / Letterbox</span>
                    <select
                      value={hyperframes.letterbox}
                      onChange={(e) => setHyperframes((h) => ({ ...h, letterbox: e.target.value }))}
                      className="w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs"
                    >
                      <option value="none">Pieno 9:16</option>
                      <option value="cinema_frame">Cornice Cinema</option>
                      <option value="editorial_border">Passepartout Avorio</option>
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-[11px] text-[#6C6459]">Ritmo Motion</span>
                    <select
                      value={hyperframes.motion_pace}
                      onChange={(e) => setHyperframes((h) => ({ ...h, motion_pace: e.target.value }))}
                      className="w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs"
                    >
                      <option value="slow_contemplative">Lento contemplativo</option>
                      <option value="balanced">Equilibrato KORA</option>
                      <option value="dynamic_experience">Dinamico Experience</option>
                    </select>
                  </label>
                </div>
                {recommendation && (
                  <div className="rounded-xl border border-[#DDD4C4] bg-white p-3 flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[#4A443C]">{recommendation.rationale}</span>
                    <button
                      type="button"
                      onClick={() => {
                        setHyperframes(recommendation.recommended_hyperframes);
                        setPresetOverride(recommendation.recommended_preset);
                      }}
                      className="rounded-lg bg-[#224C66] px-3 py-1 text-[11px] font-medium text-white"
                    >
                      Applica Hyperframes consigliati
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Action Drawers */}
        {activeAction === "idea" && (
          <form onSubmit={handleIdeaSubmit} className="mt-6 space-y-3 rounded-2xl bg-[#FAF7F2] p-5 border border-[#DDD4C4]">
            <label className="block text-xs font-medium uppercase tracking-wider text-[#6C6459]">
              Tema personalizzato
            </label>
            <div className="flex flex-col gap-3 sm:flex-row">
              <input
                type="text"
                value={themeText}
                onChange={(e) => setThemeText(e.target.value)}
                placeholder='Es. "Un risveglio d&apos;autunno tra sabbia umida e caffè caldo a Policoro."'
                className="flex-1 rounded-xl border border-[#DDD4C4] bg-white px-4 py-3 text-sm text-[#1D1B18] focus:border-[#224C66] focus:outline-none"
              />
              <button
                type="submit"
                disabled={loading || !themeText.trim()}
                className="rounded-xl bg-[#1D1B18] px-6 py-3 text-sm font-medium text-[#FAF7F2] transition hover:bg-[#224C66] disabled:opacity-50"
              >
                {loading ? "Ideazione..." : "Progetta concept"}
              </button>
            </div>
          </form>
        )}

        {activeAction === "media" && (
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
                Analisi multimodale immediata (ambiente, luce, inquadratura, potenziale cinematografico) senza alterare gli originali.
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
              recentProjects.slice(0, 6).map((p) => (
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

      {/* Selectable Photographic Concept Cards (up to 4) + Custom Theme */}
      {proposals.length > 0 && (
        <section className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="font-editorial text-2xl text-[#1D1B18]">
                {proposals.length === 4 ? "4 Proposte Narrative KORA" : "Proposte Creative KORA"}
              </h2>
              {engineInfo?.engine && (
                <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-[#6C6459]">
                  <span>Motore ideazione</span>
                  <span
                    className={`rounded-full border px-2 py-0.5 font-medium ${
                      isLocalFallbackEngine(engineInfo.engine)
                        ? "border-[#E4D3B4] bg-[#FBF4E6] text-[#7A5C2E]"
                        : "border-[#C5D9C4] bg-[#F1F7EF] text-[#2F5D3A]"
                    }`}
                  >
                    {ideationEngineLabel(engineInfo.engine)}
                  </span>
                  {engineInfo.fallback && (
                    <details className="inline-block align-middle">
                      <summary className="cursor-pointer select-none text-[#9A8052] underline decoration-dotted underline-offset-2">
                        dettagli motore
                      </summary>
                      <p className="mt-1 max-w-3xl whitespace-pre-wrap leading-relaxed text-[#6C6459]">
                        {engineInfo.fallback}
                      </p>
                    </details>
                  )}
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={() => setActiveAction("idea")}
              className="rounded-xl border border-[#DDD4C4] bg-white px-3.5 py-1.5 text-xs font-medium text-[#1D1B18] hover:border-[#9A8052]"
            >
              + Scrivi un tema personalizzato
            </button>
          </div>

          <div
            className={`grid grid-cols-1 gap-5 ${
              proposals.length >= 4 ? "md:grid-cols-2 lg:grid-cols-4" : "md:grid-cols-3"
            }`}
          >
            {proposals.map((c) => {
              const coverId = c.available_asset_ids[0];
              const isSelected = selectedConceptId === c.id;
              return (
                <div
                  key={c.id}
                  onClick={() => selectConceptAndPrefill(c)}
                  className={`cursor-pointer flex flex-col justify-between rounded-2xl border bg-white p-4 shadow-sm transition ${
                    isSelected
                      ? "border-[#224C66] ring-2 ring-[#224C66]/20"
                      : "border-[#DDD4C4] hover:border-[#9A8052]"
                  }`}
                >
                  <div className="space-y-3">
                    {coverId && (
                      <div className="relative h-36 w-full overflow-hidden rounded-xl bg-[#EFE8DB]">
                        <img
                          src={`/api/vault/${coverId}/thumb`}
                          alt={c.title}
                          className="h-full w-full object-cover"
                        />
                        <span className="absolute bottom-2 left-2 rounded-full bg-black/60 px-2.5 py-0.5 text-[10px] text-[#FAF7F2]">
                          {c.preset.replace("KORA ", "")} · {c.duration_sec}s
                        </span>
                      </div>
                    )}
                    <div className="flex items-center justify-between text-[11px] uppercase tracking-wider text-[#9A8052]">
                      <span className="truncate">{c.primary_emotion}</span>
                      <span>Voce: {c.recommended_voice_id || voiceId}</span>
                    </div>
                    <h3 className="font-editorial text-lg text-[#1D1B18] leading-snug">{c.title}</h3>
                    <p className="text-xs text-[#4A443C] leading-relaxed">{c.message}</p>
                    <div className="rounded-xl bg-[#FAF7F2] p-2.5 text-[11px] text-[#6C6459]">
                      <span className="font-medium text-[#1D1B18]">Hook:</span> {c.hook}
                    </div>
                    <div className="flex flex-wrap gap-1.5 text-[10px]">
                      <span className="rounded-full bg-[#EFE8DB] px-2 py-0.5 text-[#1D1B18]">
                        Fattibilità {Math.round(c.feasibility_score * 100)}%
                      </span>
                      <span className="rounded-full bg-[#224C66]/10 px-2 py-0.5 text-[#224C66]">
                        Originalità {Math.round(c.originality_score * 100)}%
                      </span>
                      <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-800">
                        {c.available_asset_ids.length} asset reali
                      </span>
                    </div>
                  </div>

                  <div className="mt-4 space-y-2">
                    <button
                      type="button"
                      disabled={loading}
                      onClick={async (e) => {
                        e.stopPropagation();
                        setLoading(true);
                        try {
                          await onStartProduction(
                            presetOverride === "AUTO_CONCEPT"
                              ? c
                              : { ...c, preset: presetOverride },
                            themeText.trim() || c.central_idea,
                            activeAction || "theme",
                            c.recommended_voice_id || voiceId,
                            prodMode,
                            hyperframes.caption_style,
                            hyperframes,
                            true
                          );
                        } finally {
                          setLoading(false);
                        }
                      }}
                      className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-[#224C66] bg-[#FAF7F2] px-3 py-2 text-xs font-medium text-[#224C66] transition hover:bg-[#224C66] hover:text-white"
                    >
                      <SlidersHorizontal className="h-3.5 w-3.5" /> Progetta il nuovo Reel
                    </button>
                    <button
                      type="button"
                      disabled={loading}
                      onClick={async (e) => {
                        e.stopPropagation();
                        setLoading(true);
                        try {
                          await onStartProduction(
                            presetOverride === "AUTO_CONCEPT"
                              ? c
                              : { ...c, preset: presetOverride },
                            themeText.trim() || c.central_idea,
                            activeAction || "theme",
                            c.recommended_voice_id || voiceId,
                            prodMode,
                            hyperframes.caption_style,
                            hyperframes,
                            false
                          );
                        } finally {
                          setLoading(false);
                        }
                      }}
                      className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-[#1D1B18] px-3 py-2 text-xs font-medium text-[#FAF7F2] transition hover:bg-[#224C66]"
                    >
                      <Play className="h-3.5 w-3.5" /> Realizza Reel
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
