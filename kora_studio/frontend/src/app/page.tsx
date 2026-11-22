"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Home, Images, FolderKanban, SlidersHorizontal, AlertCircle, ArrowLeft } from "lucide-react";
import type {
  BudgetSettings,
  CreativeConcept,
  HyperframesConfig,
  JobRecord,
  MediaAsset,
  ProductionMode,
  ProjectRecord,
  ProviderRecord,
  VoiceOption,
} from "@/lib/types";
import { koraApi } from "@/lib/api";
import { HomeHub } from "@/components/HomeHub";
import { ProjectsView } from "@/components/ProjectsView";
import { MediaVaultView } from "@/components/MediaVaultView";
import { CreativeStudioView } from "@/components/CreativeStudioView";
import { ProductionView } from "@/components/ProductionView";
import { PreviewView } from "@/components/PreviewView";
import { CreativeEngineModels } from "@/components/CreativeEngineModels";
import { ProvidersView } from "@/components/ProvidersView";
import { AIEngineView } from "@/components/AIEngineView";
import { EngineConnect } from "@/components/EngineConnect";
import { SceneEditor } from "@/components/SceneEditor";
import { BrandProfileView } from "@/components/BrandProfileView";
import { ExportView } from "@/components/ExportView";

/** Essential: quattro sezioni visibili. Le schermate interne restano raggiungibili dal flusso. */
type NavId = "studio" | "vault" | "projects" | "settings";
type StudioPanel = "home" | "preview" | "export" | "production" | "creative" | "scenes";
type SettingsTab = "engine" | "models" | "providers" | "brand";

const NAV_ITEMS: Array<{ id: NavId; label: string; icon: React.ComponentType<{ className?: string }> }> = [
  { id: "studio", label: "Studio", icon: Home },
  { id: "vault", label: "Archivio", icon: Images },
  { id: "projects", label: "Progetti", icon: FolderKanban },
  { id: "settings", label: "Impostazioni", icon: SlidersHorizontal },
];

const TERMINAL_JOB_STATES = ["Completato", "Errore", "Richiede intervento"];

export default function KoraStudioPage() {
  const [nav, setNav] = useState<NavId>("studio");
  const [studioPanel, setStudioPanel] = useState<StudioPanel>("home");
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("engine");

  // Cache per sezione: nessun caricamento indiscriminato all'avvio.
  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [voices, setVoices] = useState<VoiceOption[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [providers, setProviders] = useState<ProviderRecord[]>([]);
  const [budget, setBudget] = useState<BudgetSettings | null>(null);
  const [vaultSummary, setVaultSummary] = useState<{ total: number; categories: Record<string, number> }>({
    total: 0,
    categories: {},
  });
  const [engineSummary, setEngineSummary] = useState<{
    active_provider: string;
    state: string;
    model: string;
    essential: boolean;
    mode?: string;
    model_label?: string;
    migration?: { detail?: string } | null;
    last_inference?: { ok?: boolean } | null;
  } | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [bootReady, setBootReady] = useState(false);

  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const loaded = useRef<Record<string, boolean>>({});
  const jobTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // ---------------------------------------------------------------- loaders
  const loadStudio = useCallback(async (force = false) => {
    if (loaded.current.studio && !force) return;
    const boot = await koraApi.studioBootstrap();
    setVoices(boot.voices || []);
    setProjects(boot.recent_projects || []);
    setVaultSummary(boot.vault || { total: 0, categories: {} });
    setEngineSummary(boot.engine || null);
    loaded.current.studio = true;
  }, []);

  const loadVault = useCallback(async (force = false) => {
    if (loaded.current.vault && !force) return;
    const list = await koraApi.listVault();
    setAssets(list);
    loaded.current.vault = true;
  }, []);

  const loadProjects = useCallback(async (force = false) => {
    if (loaded.current.projects && !force) return;
    const [list, jobList] = await Promise.all([koraApi.listProjects(), koraApi.listJobs()]);
    setProjects(list);
    setJobs(jobList);
    loaded.current.projects = true;
  }, []);

  const loadSettingsData = useCallback(async (force = false) => {
    if (loaded.current.settings && !force) return;
    const [providerList, budgetData] = await Promise.all([
      koraApi.listProviders(),
      koraApi.getBudget(),
    ]);
    setProviders(providerList);
    setBudget(budgetData);
    loaded.current.settings = true;
  }, []);

  // Solo i dati della sezione corrente (Essential)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (nav === "studio") await loadStudio();
        if (nav === "vault") await loadVault();
        if (nav === "projects") await loadProjects();
        if (nav === "settings") {
          await loadSettingsData();
          if (settingsTab === "providers") await loadSettingsData();
        }
      } catch (err) {
        if (!cancelled) setBanner((err as Error).message);
      } finally {
        if (!cancelled) setBootReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [nav, settingsTab, loadStudio, loadVault, loadProjects, loadSettingsData]);

  const activeProject = projects.find((p) => p.id === activeProjectId) || projects[0] || null;
  const activeJob = jobs.find((j) => j.id === activeJobId) || null;

  // Durante il rendering: SOLO lo stato del job attivo, nessun refresh globale.
  useEffect(() => {
    if (jobTimer.current) {
      clearInterval(jobTimer.current);
      jobTimer.current = null;
    }
    if (!activeJobId) return;
    let stopped = false;
    const poll = async () => {
      try {
        const job = await koraApi.getJob(activeJobId);
        if (stopped) return;
        setJobs((prev) => prev.map((j) => (j.id === job.id ? job : j)));
        if (TERMINAL_JOB_STATES.includes(job.state)) {
          if (jobTimer.current) {
            clearInterval(jobTimer.current);
            jobTimer.current = null;
          }
          // a fine job aggiorno solo i progetti (non provider, budget, voci o archivio)
          const list = await koraApi.listProjects();
          if (!stopped) setProjects(list);
        }
      } catch {
        // un errore di polling non blocca l'interfaccia
      }
    };
    void poll();
    jobTimer.current = setInterval(poll, 1500);
    return () => {
      stopped = true;
      if (jobTimer.current) {
        clearInterval(jobTimer.current);
        jobTimer.current = null;
      }
    };
  }, [activeJobId]);

  // ---------------------------------------------------------------- actions
  const handleProjectReel = async (
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
  ) => {
    const effectiveMode: ProductionMode = customizeFirst ? "GUIDATA" : prodMode;
    const request: Partial<CreativeConcept["creative_request"]> = {
      ...(concept.creative_request || {}),
      theme: theme || concept.title,
      objective: concept.objective || "",
      duration_sec: concept.duration_sec,
      voice_id: voiceId,
      asset_ids: assetIds && assetIds.length > 0 ? assetIds : concept.available_asset_ids.slice(0, 3),
      media_selection: "vault_auto",
      generation_policy: "authentic_first",
    };
    const proposal = (concept.creative_request as Record<string, unknown> | undefined)?.lite_proposal;
    setBanner(null);
    try {
      const res = await koraApi.projectReel({
        request,
        proposal: proposal || null,
        create_project: true,
        mode: mode || "suggest",
        production_mode: effectiveMode,
        hyperframes,
        caption_style: captionStyle || hyperframes?.caption_style,
        destination_dir: destinationDir || null,
      });
      if (res.project) {
        setActiveProjectId(res.project.id);
        setProjects((prev) => [res.project as ProjectRecord, ...prev.filter((p) => p.id !== res.project?.id)]);
      }
      if (res.design.fallback_reason) setBanner(res.design.fallback_reason);
      setNav("studio");
      setStudioPanel("preview");
    } catch (err) {
      setBanner(`Progettazione non riuscita: ${(err as Error).message}`);
      throw err;
    }
  };

  const handleProduceExisting = async (projectId: string) => {
    const res = await koraApi.produceProject(projectId);
    setActiveProjectId(projectId);
    setActiveJobId(res.job.id);
    setStudioPanel("production");
    setNav("studio");
  };

  const goProjectTab = (id: string, tab?: "creative" | "production" | "preview" | "export") => {
    setActiveProjectId(id);
    setNav("studio");
    if (tab === "preview" || tab === "export") setStudioPanel(tab);
    else if (tab === "production") {
      setStudioPanel("production");
    } else setStudioPanel("creative");
  };

  // ---------------------------------------------------------------- render
  const sidebarBadge = activeJob && !TERMINAL_JOB_STATES.includes(activeJob.state) ? activeJob.state : null;

  return (
    <div className="min-h-screen flex flex-col lg:flex-row bg-[#FAF7F2]">
      <aside className="border-b border-[#DDD4C4] bg-white/90 px-4 py-4 lg:w-64 lg:shrink-0 lg:border-b-0 lg:border-r lg:py-8 lg:px-5">
        <div className="flex items-center justify-between lg:block">
          <div>
            <div className="font-editorial text-xl tracking-wide text-[#1D1B18]">KORA STUDIO</div>
            <div className="text-[10px] uppercase tracking-[0.2em] text-[#9A8052]">
              Essential · The Art of Being · Policoro
            </div>
          </div>
          {sidebarBadge && (
            <span className="lg:mt-3 inline-flex items-center gap-1.5 rounded-full bg-[#224C66]/10 px-2.5 py-1 text-[11px] font-medium text-[#224C66]">
              <span className="h-2 w-2 rounded-full bg-[#224C66] animate-pulse" />
              {sidebarBadge}
            </span>
          )}
        </div>

        <nav className="mt-4 flex gap-1.5 overflow-x-auto pb-1 lg:mt-8 lg:flex-col lg:overflow-visible">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = nav === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => {
                  setNav(item.id);
                  if (item.id === "studio") setStudioPanel("home");
                }}
                className={`flex shrink-0 items-center gap-2.5 rounded-xl px-3.5 py-2.5 text-xs font-medium transition ${
                  active
                    ? "bg-[#1D1B18] text-[#FAF7F2]"
                    : "text-[#4A443C] hover:bg-[#EFE8DB]/70 hover:text-[#1D1B18]"
                }`}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="mt-6 hidden rounded-2xl border border-[#EDE6D9] bg-[#FAF7F2] p-3.5 text-[11px] text-[#6C6459] lg:block">
          <div className="text-[10px] uppercase tracking-wider text-[#9A8052]">Motore creativo</div>
          <div className="mt-1 truncate text-xs font-medium text-[#1D1B18]">
            {engineSummary?.active_provider || "—"}
            {engineSummary?.mode ? ` · ${engineSummary.mode === "premium" ? "Premium" : "Everyday"}` : ""}
          </div>
          <div className="mt-0.5 truncate text-[11px] text-[#6C6459]">
            {engineSummary?.model_label || engineSummary?.model || engineSummary?.state || "non verificato"}
          </div>
          {engineSummary?.migration?.detail && (
            <div className="mt-1 text-[10px] leading-snug text-[#7A5C2E]">{engineSummary.migration.detail}</div>
          )}
          <div className="mt-2 border-t border-[#EDE6D9] pt-2">
            Archivio: <strong>{vaultSummary.total}</strong> elementi
          </div>
          {budget && (
            <div className="mt-1">
              Spesa mese: {budget.actual_month_eur.toFixed(2)} / {budget.monthly_cap_eur.toFixed(0)} €
            </div>
          )}
        </div>

        {activeProject && nav !== "studio" && (
          <div className="mt-3 hidden rounded-2xl border border-[#EDE6D9] bg-white p-3.5 lg:block">
            <div className="text-[10px] uppercase tracking-wider text-[#9A8052]">Progetto attivo</div>
            <div className="mt-1 truncate text-xs font-medium text-[#1D1B18]">{activeProject.title}</div>
            <div className="mt-0.5 text-[11px] text-[#6C6459]">
              {activeProject.status} · v{activeProject.current_version}
            </div>
          </div>
        )}
      </aside>

      <main className="flex-1 overflow-y-auto p-5 md:p-8 lg:p-10">
        {banner && (
          <div className="mb-4 flex items-start justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900">
            <span className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{banner}</span>
            </span>
            <button
              type="button"
              onClick={() => setBanner(null)}
              className="rounded-lg border border-amber-300 px-2 py-0.5 text-[11px] hover:bg-amber-100"
            >
              Chiudi
            </button>
          </div>
        )}

        {!bootReady && nav === "studio" && (
          <div className="rounded-2xl border border-[#EDE6D9] bg-white p-6 text-sm text-[#6C6459]">
            Caricamento Studio…
          </div>
        )}

        {nav === "studio" && bootReady && (
          <div className="space-y-4">
            {studioPanel !== "home" && (
              <button
                type="button"
                onClick={() => setStudioPanel("home")}
                className="inline-flex items-center gap-1.5 text-xs font-medium text-[#224C66] hover:underline"
              >
                <ArrowLeft className="h-3.5 w-3.5" /> Torna a Studio
              </button>
            )}
            {studioPanel === "home" && <EngineConnect variant="compact" />}
            {studioPanel === "home" && (
              <HomeHub
                voices={voices}
                recentProjects={projects}
                onConceptsReady={() => {}}
                onStartProduction={handleProjectReel}
                onOpenProject={(id) => {
                  setActiveProjectId(id);
                  const pr = projects.find((x) => x.id === id);
                  setStudioPanel(pr?.output_video_path ? "preview" : "creative");
                }}
                onVaultUpdated={() => {
                  loaded.current.vault = false;
                  setBanner(null);
                }}
              />
            )}
            {studioPanel === "creative" && (
              <CreativeStudioView
                project={activeProject}
                assets={assets.length ? assets : []}
                voices={voices}
                onUpdated={async () => {
                  const list = await koraApi.listProjects();
                  setProjects(list);
                }}
                onProduce={handleProduceExisting}
              />
            )}
            {studioPanel === "preview" && (
              <PreviewView
                project={activeProject}
                onRevisionStarted={(jobId) => {
                  setActiveJobId(jobId);
                  setStudioPanel("production");
                }}
                onRestored={async () => {
                  const list = await koraApi.listProjects();
                  setProjects(list);
                }}
                onGoExport={() => setStudioPanel("export")}
                onOpenScenes={(pid) => {
                  setActiveProjectId(pid);
                  setStudioPanel("scenes");
                }}
                onProduce={(projectId) => void handleProduceExisting(projectId)}
              />
            )}
            {studioPanel === "scenes" && activeProject && (
              <SceneEditor
                projectId={activeProject.id}
                projectTitle={activeProject.title}
                onPreview={() => setStudioPanel("preview")}
              />
            )}
            {studioPanel === "scenes" && !activeProject && (
              <p className="rounded-2xl border border-[#EDE6D9] bg-white p-6 text-sm text-[#6C6459]">
                Apri un progetto dalla Home per lavorare sulle scene.
              </p>
            )}
            {studioPanel === "production" && (
              <ProductionView
                jobs={jobs}
                activeJob={activeJob}
                onRefresh={async () => {
                  const jobList = await koraApi.listJobs();
                  setJobs(jobList);
                }}
                onOpenPreview={(pid) => {
                  setActiveProjectId(pid);
                  setStudioPanel("preview");
                }}
              />
            )}
            {studioPanel === "export" && (
              <ExportView
                project={activeProject}
                onRefresh={async () => {
                  const list = await koraApi.listProjects();
                  setProjects(list);
                }}
              />
            )}
          </div>
        )}

        {nav === "vault" && <MediaVaultView assets={assets} onRefresh={() => loadVault(true)} />}

        {nav === "projects" && (
          <ProjectsView
            projects={projects}
            activeProjectId={activeProject?.id || null}
            onSelectProject={goProjectTab}
            onRefresh={() => loadProjects(true)}
          />
        )}

        {nav === "settings" && (
          <div className="space-y-5">
            <div className="flex flex-wrap gap-2">
              {(
                [
                  ["engine", "Motore AI"],
                  ["models", "Modelli & benchmark"],
                  ["providers", "Provider & budget"],
                  ["brand", "Profilo KORA"],
                ] as [SettingsTab, string][]
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setSettingsTab(id)}
                  className={`rounded-xl px-3.5 py-2 text-xs font-medium transition ${
                    settingsTab === id
                      ? "bg-[#1D1B18] text-[#FAF7F2]"
                      : "border border-[#DDD4C4] bg-white text-[#4A443C] hover:border-[#9A8052]"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            {settingsTab === "engine" && (
              <div className="space-y-4">
                <EngineConnect variant="full" />
                <AIEngineView />
              </div>
            )}
            {settingsTab === "models" && <CreativeEngineModels />}
            {settingsTab === "providers" && (
              <ProvidersView
                providers={providers}
                budget={budget}
                voices={voices}
                onRefresh={() => loadSettingsData(true)}
              />
            )}
            {settingsTab === "brand" && <BrandProfileView />}
          </div>
        )}
      </main>
    </div>
  );
}
