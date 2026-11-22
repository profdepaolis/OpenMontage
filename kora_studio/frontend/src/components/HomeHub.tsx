"use client";

import React, { useEffect, useRef, useState } from "react";
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
  CheckCircle2,
  Loader2,
  XCircle,
  Clock,
  Database,
  Square,
} from "lucide-react";
import type {
  CreativeConcept,
  CreativeRequestPayload,
  HyperframesConfig,
  MediaAsset,
  ProductionMode,
  ProjectRecord,
  ThemeSuggestionResponse,
  IdeationStageEvent,
  ThemeProposalLite,
  ThemeProposalsLiteEnvelope,
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

// 2.8.1 — messaggi espliciti: mai schermata vuota
function friendlyLiteError(raw: string, budgetSec = 45): string {
  const message = (raw || "").toLowerCase();
  if (
    message.includes("end of life") ||
    message.includes("dismess") ||
    message.includes("deprecat") ||
    message.includes("non più disponibile")
  )
    return "Il modello scelto è stato dismesso dal provider. Scegline un altro in Impostazioni → Modelli & benchmark, poi riprova.";
  if (message.includes("annullat")) return "Richiesta annullata. Nessuna proposta generata.";
  if (message.includes("non autenticat") || message.includes("login") || message.includes("auth"))
    return "Il motore AI selezionato non risulta autenticato (OpenCode/Codex): accedi al provider o scegli un altro motore in Impostazioni → Motori AI. Nel frattempo puoi usare il motore deterministico locale (0 €).";
  if (message.includes("modello") && (message.includes("non disponibil") || message.includes("unavailable")))
    return "Il modello scelto non è più disponibile sul provider: scegline un altro in Impostazioni → Motori AI.";
  if (message.includes("quota") || message.includes("429") || message.includes("rate limit"))
    return "Quota del provider esaurita o limite di frequenza raggiunto: attendi qualche minuto o cambia motore.";
  if (message.includes("timeout") || message.includes("budget") || message.includes("tempo"))
    return `Tempo massimo di ${budgetSec} s raggiunto: sono state restituite le proposte deterministiche KORA (0 €). Puoi alzare il limite in Impostazioni → Modelli & benchmark.`;
  if (message.includes("json") || message.includes("formato non leggibile"))
    return "Il motore ha risposto in un formato non leggibile (JSON non valido o incompleto): KORA ha usato la direzione creativa locale. Riprova: la seconda risposta viene richiesta in JSON compatto.";
  if (message.includes("oltre il budget")) return "Discovery dei motori oltre il budget: proposte deterministiche KORA (0 €). Usa «Riprova» per sfruttare la cache dei motori.";
  if (message.includes("failed to fetch") || message.includes("network"))
    return "Backend KORA non raggiungibile: verifica che il server locale sia attivo (./start.sh).";
  return raw;
}

const LITE_STAGE_ORDER: { stage: IdeationStageEvent["stage"]; label: string }[] = [
  { stage: "context", label: "Preparazione contesto" },
  { stage: "connect", label: "Connessione motore AI" },
  { stage: "generate", label: "Generazione temi" },
  { stage: "validate", label: "Validazione proposte" },
  { stage: "done", label: "Completato" },
];

function stageState(
  events: IdeationStageEvent[],
  stage: IdeationStageEvent["stage"]
): "pending" | "running" | "done" | "error" {
  const last = [...events].reverse().find((event) => event.stage === stage);
  if (!last) return "pending";
  if (last.status === "done") return "done";
  if (last.status === "error" || last.status === "requested") return "error";
  return "running";
}

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
    customizeFirst?: boolean,
    assetIds?: string[],
    destinationDir?: string | null
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
  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>([]);
  const [destinationDir, setDestinationDir] = useState<string>("");
  const [assetFilter, setAssetFilter] = useState<"photo" | "video" | "all">("all");
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
  // 2.8.1 — Fast Creative Mode
  const [liteStages, setLiteStages] = useState<IdeationStageEvent[]>([]);
  const [liteProposals, setLiteProposals] = useState<ThemeProposalLite[]>([]);
  const [liteEnvelope, setLiteEnvelope] = useState<ThemeProposalsLiteEnvelope | null>(null);
  const [liteElapsedMs, setLiteElapsedMs] = useState(0);
  const [liteBudgetMs, setLiteBudgetMs] = useState(45000);
  const [liteRunId, setLiteRunId] = useState<string | null>(null);
  const [liteStatus, setLiteStatus] = useState<"idle" | "running" | "done" | "cancelled" | "error">(
    "idle"
  );
  const [expandingId, setExpandingId] = useState<string | null>(null);
  const liteRunRef = useRef<string | null>(null);
  const [uploadedInfo, setUploadedInfo] = useState<MediaAsset[]>([]);

  React.useEffect(() => {
    if (!showAdvanced || assets.length > 0) return;
    koraApi
      .listVault()
      .then((list) => setAssets(list))
      .catch(() => setAssets([]));
  }, [showAdvanced, assets.length]);

  const buildRequestPayload = (overrideTheme?: string): Partial<CreativeRequestPayload> => ({
    theme: (overrideTheme ?? themeText).trim(),
    objective: objective.trim(),
    duration_sec: durationSec,
    voice_id: voiceId,
    media_selection: mediaSelection,
    generation_policy: generationPolicy,
    budget_eur: budgetEur,
    custom_instructions: customInstructions.trim(),
    asset_ids: selectedAssetIds,
  });

  const handleSuggestThemes = async () => {
    // nessuna richiesta duplicata: un solo run attivo alla volta
    if (liteStatus === "running" || loading) return;
    setActiveAction("suggest");
    setLoading(true);
    setError(null);
    setProposals([]);
    setSelectedConceptId(null);
    setLiteProposals([]);
    setLiteEnvelope(null);
    setLiteStages([]);
    setLiteElapsedMs(0);
    setLiteStatus("running");

    try {
      const start = await koraApi.suggestThemesLite(buildRequestPayload(""));
      liteRunRef.current = start.run_id;
      setLiteRunId(start.run_id);
      const budgetMs = start.budget_ms || 45000;
      setLiteBudgetMs(budgetMs);
      const localDeadline = Date.now() + budgetMs + 8000;
      // polling degli eventi reali del backend (stage + esito)
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, 220));
        if (liteRunRef.current !== start.run_id) return; // annullato dall'utente
        if (Date.now() > localDeadline) {
          setLiteStatus("error");
          setError(
            `Il backend non ha risposto entro il budget di ${Math.round(budgetMs / 1000)} s (nessun processo lasciato attivo). Riprova: la cache dei motori è più calda.`
          );
          return;
        }
        const snapshot = await koraApi.getSuggestThemesLite(start.run_id);
        if (liteRunRef.current !== start.run_id) return;
        setLiteStages(snapshot.events);
        setLiteElapsedMs(snapshot.elapsed_ms);
        if (snapshot.status === "cancelled") {
          setLiteStatus("cancelled");
          setError(friendlyLiteError("annullata"));
          return;
        }
        if (snapshot.status === "error") {
          setLiteStatus("error");
          setError(friendlyLiteError(snapshot.error || "Errore ideazione", Math.round(liteBudgetMs / 1000)));
          return;
        }
        if (snapshot.status === "done" && snapshot.envelope) {
          setLiteEnvelope(snapshot.envelope);
          setLiteProposals(snapshot.envelope.proposals || []);
          setEngineInfo({
            engine: snapshot.envelope.generation_engine,
            fallback: snapshot.envelope.fallback_reason,
          });
          setLiteStatus("done");
          if (snapshot.envelope.fallback_reason) {
            setError(friendlyLiteError(snapshot.envelope.fallback_reason, Math.round(liteBudgetMs / 1000)));
          }
          return;
        }
      }
    } catch (e) {
      setLiteStatus("error");
      setError(friendlyLiteError((e as Error).message));
    } finally {
      liteRunRef.current = null;
      setLoading(false);
    }
  };

  const handleCancelSuggest = async () => {
    const runId = liteRunRef.current || liteRunId;
    liteRunRef.current = null;
    if (runId) {
      try {
        await koraApi.cancelSuggestThemesLite(runId);
      } catch {
        // anche se il backend non risponde, il polling locale si ferma
      }
    }
    setLiteStatus("cancelled");
    setError(friendlyLiteError("annullata"));
    setLiteRunId(null);
    setLoading(false);
  };

  const handleSelectLite = async (proposal: ThemeProposalLite) => {
    if (expandingId) return; // nessun doppio click
    setExpandingId(proposal.id);
    setError(null);
    try {
      // artefatti pesanti (voiceover, storyboard) solo DOPO la scelta del tema
      const res = await koraApi.expandTheme(
        proposal,
        proposal.recommended_duration_sec,
        proposal.asset_ids
      );
      setProposals(res.concepts);
      setEngineInfo({ engine: res.generation_engine, fallback: null });
      if (res.recommendation) setRecommendation(res.recommendation);
      const concept = res.concepts[0];
      if (concept) {
        // Essential: la proposta scelta viaggia con il concept fino a "Progetta Reel"
        concept.creative_request = {
          ...(concept.creative_request || {}),
          lite_proposal: proposal,
        } as unknown as CreativeConcept["creative_request"];
        selectConceptAndPrefill(concept);
        onConceptsReady(res.concepts, proposal.title, "suggest");
      }
    } catch (e) {
      setError(friendlyLiteError((e as Error).message));
    } finally {
      setExpandingId(null);
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

        {/* 3 fasi dichiarate: Suggerisci → Progetta → Realizza */}
        <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-3">
          <button
            type="button"
            onClick={handleSuggestThemes}
            className={`group rounded-2xl border p-5 text-left transition ${
              activeAction === "suggest"
                ? "border-[#224C66] bg-[#224C66]/5"
                : "border-[#DDD4C4] bg-[#FAF7F2] hover:border-[#9A8052]"
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-medium uppercase tracking-[0.2em] text-[#9A8052]">01</span>
              <Sparkles className="h-5 w-5 text-[#9A8052]" />
            </div>
            <div className="mt-3 font-medium text-[#1D1B18]">Suggerisci un tema</div>
            <div className="mt-1 text-xs text-[#6C6459]">
              Esattamente 4 proposte originali: una sola richiesta JSON, nessun rendering.
            </div>
          </button>

          <button
            type="button"
            onClick={() => {
              setActiveAction("idea");
              const anchor = document.getElementById("kora-design");
              if (anchor) anchor.scrollIntoView({ behavior: "smooth", block: "start" });
              else void handleSuggestThemes();
            }}
            className="group rounded-2xl border border-[#DDD4C4] bg-[#FAF7F2] p-5 text-left transition hover:border-[#9A8052]"
          >
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-medium uppercase tracking-[0.2em] text-[#9A8052]">02</span>
              <PenLine className="h-5 w-5 text-[#224C66]" />
            </div>
            <div className="mt-3 font-medium text-[#1D1B18]">Progetta Reel</div>
            <div className="mt-1 text-xs text-[#6C6459]">
              Un unico piano strutturato: scene, voce, movimenti e durate coerenti.
            </div>
          </button>

          <button
            type="button"
            onClick={() => {
              const anchor = document.getElementById("kora-design");
              if (anchor) anchor.scrollIntoView({ behavior: "smooth", block: "start" });
              else void handleSuggestThemes();
            }}
            className="group rounded-2xl border border-[#DDD4C4] bg-[#FAF7F2] p-5 text-left transition hover:border-[#9A8052]"
          >
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-medium uppercase tracking-[0.2em] text-[#9A8052]">03</span>
              <Play className="h-5 w-5 text-[#224C66]" />
            </div>
            <div className="mt-3 font-medium text-[#1D1B18]">Realizza Reel</div>
            <div className="mt-1 text-xs text-[#6C6459]">
              Montaggio locale 1080×1920, QC verificato, esportazione pronta da pubblicare.
            </div>
          </button>
        </div>

        {/* Ingressi alternativi, sempre disponibili e facoltativi */}
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-[#8A8276]">Oppure:</span>
          {(
            [
              ["idea", "parti dalla mia idea"],
              ["media", "crea dai miei media"],
              ["resume", "riprendi una produzione"],
            ] as Array<["idea" | "media" | "resume", string]>
          ).map(([action, label]) => (
            <button
              key={action}
              type="button"
              onClick={() => setActiveAction(action)}
              className={`rounded-full border px-3 py-1 transition ${
                activeAction === action
                  ? "border-[#224C66] bg-[#224C66]/5 text-[#224C66]"
                  : "border-[#DDD4C4] bg-[#FAF7F2] text-[#4A443C] hover:border-[#9A8052]"
              }`}
            >
              {label}
            </button>
          ))}
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

              <div className="rounded-xl border border-[#EDE6D9] bg-white p-3 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-[#1D1B18]">Materiali del Reel</span>
                  <div className="flex items-center gap-1.5 text-[10px]">
                    <select
                      value={assetFilter}
                      onChange={(e) => setAssetFilter(e.target.value as "photo" | "video" | "all")}
                      className="rounded border border-[#DDD4C4] bg-white px-1.5 py-0.5"
                    >
                      <option value="all">Tutti</option>
                      <option value="photo">Solo foto</option>
                      <option value="video">Solo video</option>
                    </select>
                    <span className="text-[#6C6459]">
                      {selectedAssetIds.length > 0
                        ? `${selectedAssetIds.length} selezionati`
                        : "automatici dal Vault"}
                    </span>
                    {selectedAssetIds.length > 0 && (
                      <button
                        type="button"
                        onClick={() => setSelectedAssetIds([])}
                        className="underline decoration-dotted text-[#6C6459]"
                      >
                        azzera
                      </button>
                    )}
                  </div>
                </div>
                <p className="text-[10px] text-[#6C6459]">
                  Se non selezioni nulla, il MediaCurator sceglie automaticamente i materiali autentici più adatti
                  al concept. Selezionando, il Reel userà <em>solo</em> quelli.
                </p>
                <div className="grid max-h-44 grid-cols-3 gap-2 overflow-y-auto sm:grid-cols-5">
                  {assets
                    .filter((a) => (assetFilter === "all" ? true : a.media_type === assetFilter))
                    .map((a) => {
                      const selected = selectedAssetIds.includes(a.id);
                      return (
                        <button
                          key={a.id}
                          type="button"
                          onClick={() =>
                            setSelectedAssetIds((prev) =>
                              prev.includes(a.id) ? prev.filter((x) => x !== a.id) : [...prev, a.id]
                            )
                          }
                          className={`relative overflow-hidden rounded-lg border-2 transition ${
                            selected ? "border-[#224C66] ring-2 ring-[#224C66]/25" : "border-transparent"
                          }`}
                          title={`${a.filename} · ${a.media_type}`}
                        >
                          <img
                            src={`/api/vault/${a.id}/thumb`}
                            alt={a.filename}
                            className="h-16 w-full object-cover"
                          />
                          <span className="absolute bottom-0 left-0 right-0 bg-black/55 px-1 py-0.5 text-[8px] text-white">
                            {a.media_type === "video" ? "video" : "foto"}
                          </span>
                          {selected && (
                            <span className="absolute right-0.5 top-0.5 rounded-full bg-[#224C66] px-1 text-[8px] text-white">
                              ✓
                            </span>
                          )}
                        </button>
                      );
                    })}
                </div>
              </div>

              <label className="block space-y-1">
                <span className="text-[11px] text-[#6C6459]">
                  Cartella di destinazione del Reel (opzionale)
                </span>
                <input
                  type="text"
                  value={destinationDir}
                  onChange={(e) => setDestinationDir(e.target.value)}
                  placeholder="~/Desktop/KORA-REELS"
                  className="w-full rounded-lg border border-[#DDD4C4] bg-white px-3 py-1.5 text-xs"
                />
                <span className="block text-[10px] text-[#6C6459]">
                  Percorso assoluto o con ~. A render finito il pacchetto (MP4, copertina, caption, report,
                  progetto) viene copiato lì automaticamente.
                </span>
              </label>

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
          <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-800">
            <div className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleSuggestThemes}
                disabled={liteStatus === "running"}
                className="rounded-lg border border-red-300 bg-white px-2.5 py-1 text-[11px] text-red-800 disabled:opacity-50"
              >
                Riprova la stessa richiesta
              </button>
              <button
                type="button"
                onClick={() => setLiteStatus("idle")}
                className="rounded-lg border border-red-300 bg-white px-2.5 py-1 text-[11px] text-red-800"
              >
                Usa le proposte locali (0 €)
              </button>
              <span className="self-center text-[10px] text-red-700/80">
                Per cambiare modello o alzare il tempo: Impostazioni → Modelli &amp; benchmark.
              </span>
            </div>
          </div>
        )}
      </section>

      {/* 2.8.1 — Fast Creative Mode: stage reali, tempi, annullamento, proposte lite */}
      {(liteStatus !== "idle" || liteProposals.length > 0) && (
        <section id="kora-design" className="space-y-4 rounded-3xl border border-[#DDD4C4] bg-white/90 p-6 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="font-editorial text-2xl text-[#1D1B18]">
                Suggerisci un tema
                <span className="ml-2 align-middle text-[10px] font-medium uppercase tracking-[0.18em] text-[#9A8052]">
                  Fast Creative Mode
                </span>
              </h2>
              <p className="mt-1 text-xs text-[#6C6459]">
                4 proposte leggere · nessuna analisi multimedia · budget massimo {Math.round(liteBudgetMs / 1000)} s
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <span className="inline-flex items-center gap-1 rounded-full border border-[#DDD4C4] bg-[#FAF7F2] px-2.5 py-1 text-[#4A443C]">
                <Clock className="h-3 w-3" /> {(liteElapsedMs / 1000).toFixed(1)} s
                <span className="text-[#9A8052]">/ {Math.round(liteBudgetMs / 1000)} s</span>
              </span>
              {liteEnvelope?.generation_engine && (
                <span
                  className={`rounded-full border px-2.5 py-1 font-medium ${
                    isLocalFallbackEngine(liteEnvelope.generation_engine)
                      ? "border-[#E4D3B4] bg-[#FBF4E6] text-[#7A5C2E]"
                      : "border-[#C5D9C4] bg-[#F1F7EF] text-[#2F5D3A]"
                  }`}
                >
                  {ideationEngineLabel(liteEnvelope.generation_engine)}
                  {liteEnvelope.model ? ` · ${liteEnvelope.model}` : ""}
                </span>
              )}
              {liteEnvelope?.from_cache && (
                <span className="inline-flex items-center gap-1 rounded-full border border-[#C9D7E3] bg-[#EFF5FA] px-2.5 py-1 font-medium text-[#224C66]">
                  <Database className="h-3 w-3" /> dalla cache
                  {typeof liteEnvelope.cache_age_sec === "number"
                    ? ` · ${Math.round(liteEnvelope.cache_age_sec / 60)} min`
                    : ""}
                </span>
              )}
              {liteStatus === "running" && (
                <button
                  type="button"
                  onClick={handleCancelSuggest}
                  className="inline-flex items-center gap-1.5 rounded-full border border-red-300 bg-red-50 px-3 py-1 font-medium text-red-700 hover:bg-red-100"
                >
                  <Square className="h-3 w-3" /> Annulla
                </button>
              )}
            </div>
          </div>

          <ol className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
            {LITE_STAGE_ORDER.map(({ stage, label }) => {
              const state = stageState(liteStages, stage);
              return (
                <li
                  key={stage}
                  className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-[11px] ${
                    state === "done"
                      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                      : state === "running"
                        ? "border-[#224C66]/30 bg-[#224C66]/5 text-[#224C66]"
                        : state === "error"
                          ? "border-amber-200 bg-amber-50 text-amber-900"
                          : "border-[#E7DFD2] bg-[#FAF7F2] text-[#9A8052]"
                  }`}
                >
                  {state === "done" ? (
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                  ) : state === "running" ? (
                    <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                  ) : state === "error" ? (
                    <XCircle className="h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <span className="h-3.5 w-3.5 shrink-0 rounded-full border border-current opacity-40" />
                  )}
                  <span className="truncate">{label}</span>
                  <span className="ml-auto tabular-nums opacity-70">
                    {(() => {
                      const last = [...liteStages].reverse().find((event) => event.stage === stage);
                      return last ? `${(last.at_ms / 1000).toFixed(1)}s` : "";
                    })()}
                  </span>
                </li>
              );
            })}
          </ol>
          {liteStages.some((event) => event.stage === "fallback") && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-900">
              {[...liteStages].reverse().find((event) => event.stage === "fallback")?.detail}
            </div>
          )}

          {liteProposals.length > 0 && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="font-editorial text-lg text-[#1D1B18]">
                  4 proposte · scegline una per progettare il Reel
                </h3>
                <button
                  type="button"
                  onClick={handleSuggestThemes}
                  disabled={loading}
                  className="rounded-xl border border-[#DDD4C4] bg-white px-3 py-1.5 text-[11px] font-medium text-[#1D1B18] hover:border-[#9A8052] disabled:opacity-50"
                >
                  Rigenera proposte
                </button>
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
                {liteProposals.map((proposal) => {
                  const coverId = proposal.asset_ids[0];
                  const isSelected =
                    proposals[0]?.title?.startsWith(proposal.title) ?? false;
                  const isExpanding = expandingId === proposal.id;
                  return (
                    <div
                      key={proposal.id}
                      className={`flex flex-col justify-between rounded-2xl border bg-white p-4 shadow-sm transition ${
                        isSelected
                          ? "border-[#224C66] ring-2 ring-[#224C66]/20"
                          : "border-[#DDD4C4] hover:border-[#9A8052]"
                      }`}
                    >
                      <div className="space-y-2.5">
                        <div className="flex items-start justify-between gap-2">
                          <h4 className="font-editorial text-base leading-snug text-[#1D1B18]">
                            {proposal.title}
                          </h4>
                          {proposal.from_cache && (
                            <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-[#EFF5FA] px-2 py-0.5 text-[9px] font-medium text-[#224C66]">
                              <Database className="h-2.5 w-2.5" /> cache
                            </span>
                          )}
                        </div>
                        {coverId && (
                          <div className="h-28 w-full overflow-hidden rounded-xl bg-[#EFE8DB]">
                            <img
                              src={`/api/vault/${coverId}/thumb`}
                              alt={proposal.title}
                              className="h-full w-full object-cover"
                            />
                          </div>
                        )}
                        <p className="text-[11px] italic leading-relaxed text-[#4A443C]">
                          “{proposal.hook}”
                        </p>
                        <p
                          className="text-[11px] leading-relaxed text-[#6C6459]"
                          style={{
                            display: "-webkit-box",
                            WebkitLineClamp: 3,
                            WebkitBoxOrient: "vertical",
                            overflow: "hidden",
                          }}
                        >
                          {proposal.central_message}
                        </p>
                        <p
                          className="text-[10px] leading-relaxed text-[#9A8052]"
                          style={{
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                            overflow: "hidden",
                          }}
                        >
                          {proposal.narrative_direction}
                        </p>
                        <div className="flex flex-wrap gap-1 text-[9px]">
                          <span className="rounded-full bg-[#EFE8DB] px-2 py-0.5 text-[#1D1B18]">
                            {proposal.recommended_duration_sec}s
                          </span>
                          <span className="rounded-full bg-[#224C66]/10 px-2 py-0.5 text-[#224C66]">
                            Voce: {proposal.recommended_voice_id}
                          </span>
                          <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-800">
                            {proposal.asset_ids.length} asset
                          </span>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleSelectLite(proposal)}
                        disabled={Boolean(expandingId)}
                        className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-xl bg-[#224C66] px-3 py-2 text-[11px] font-medium text-white transition hover:bg-[#1D1B18] disabled:opacity-60"
                      >
                        {isExpanding ? (
                          <>
                            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Espansione tema…
                          </>
                        ) : (
                          <>
                            <Wand2 className="h-3.5 w-3.5" /> Scegli questo tema
                          </>
                        )}
                      </button>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </section>
      )}

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
                            true,
                            selectedAssetIds,
                            destinationDir.trim() || null
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
                            false,
                            selectedAssetIds,
                            destinationDir.trim() || null
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
