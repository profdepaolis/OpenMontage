"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import {
  AlertCircle,
  Clapperboard,
  Download,
  Film,
  FolderKanban,
  Home,
  Images,
  LayoutGrid,
  Palette,
  PlaySquare,
  Server,
  Sparkles,
} from "lucide-react";
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

/**
 * Sezioni caricate su richiesta: il bundle iniziale resta leggero e ogni
 * schermata porta con sé solo i propri dati (nessuna scansione all'avvio).
 */
const SectionSkeleton = ({ label }: { label: string }) => (
  <div className="animate-pulse rounded-2xl border border-[#EDE6D9] bg-white p-6 text-sm text-[#6C6459]">
    Caricamento {label}…
  </div>
);

const HomeHub = dynamic(() => import("@/components/HomeHub").then((m) => m.HomeHub), {
  loading: () => <SectionSkeleton label="Home" />,
});
const ProjectsView = dynamic(() => import("@/components/ProjectsView").then((m) => m.ProjectsView), {
  loading: () => <SectionSkeleton label="Progetti" />,
});
const MediaVaultView = dynamic(() => import("@/components/MediaVaultView").then((m) => m.MediaVaultView), {
  loading: () => <SectionSkeleton label="Media Vault" />,
});
const CreativeStudioView = dynamic(
  () => import("@/components/CreativeStudioView").then((m) => m.CreativeStudioView),
  { loading: () => <SectionSkeleton label="Creative Studio" /> }
);
const ProductionView = dynamic(() => import("@/components/ProductionView").then((m) => m.ProductionView), {
  loading: () => <SectionSkeleton label="Produzione" />,
});
const PreviewView = dynamic(() => import("@/components/PreviewView").then((m) => m.PreviewView), {
  loading: () => <SectionSkeleton label="Anteprima" />,
});
const SceneEditor = dynamic(() => import("@/components/SceneEditor").then((m) => m.SceneEditor), {
  loading: () => <SectionSkeleton label="Storyboard" />,
});
const ProvidersView = dynamic(() => import("@/components/ProvidersView").then((m) => m.ProvidersView), {
  loading: () => <SectionSkeleton label="Provider" />,
});
const AIEngineView = dynamic(() => import("@/components/AIEngineView").then((m) => m.AIEngineView), {
  loading: () => <SectionSkeleton label="Motore AI" />,
});
const CreativeEngineModels = dynamic(
  () => import("@/components/CreativeEngineModels").then((m) => m.CreativeEngineModels),
  { loading: () => <SectionSkeleton label="Modelli" /> }
);
const EngineConnect = dynamic(() => import("@/components/EngineConnect").then((m) => m.EngineConnect), {
  loading: () => null,
});
const EnginesInventory = dynamic(
  () => import("@/components/EnginesInventory").then((m) => m.EnginesInventory),
  { loading: () => <SectionSkeleton label="motori locali" /> }
);
const BrandProfileView = dynamic(
  () => import("@/components/BrandProfileView").then((m) => m.BrandProfileView),
  { loading: () => <SectionSkeleton label="Brand Profile" /> }
);
const ExportView = dynamic(() => import("@/components/ExportView").then((m) => m.ExportView), {
  loading: () => <SectionSkeleton label="Export" />,
});

/** Le dieci sezioni della Home professionale; i dati si caricano una volta sola. */
type NavId =
  | "home"
  | "projects"
  | "vault"
  | "creative"
  | "production"
  | "preview"
  | "storyboard"
  | "providers"
  | "brand"
  | "export";

type ProviderTab = "engines" | "ai" | "models" | "budget";

const NAV_GROUPS: Array<{
  title: string;
  items: Array<{ id: NavId; label: string; icon: React.ComponentType<{ className?: string }> }>;
}> = [
  {
    title: "Studio",
    items: [
      { id: "home", label: "Home", icon: Home },
      { id: "projects", label: "Progetti", icon: FolderKanban },
      { id: "vault", label: "Media Vault", icon: Images },
      { id: "creative", label: "Creative Studio", icon: Sparkles },
    ],
  },
  {
    title: "Lavorazione",
    items: [
      { id: "production", label: "Production", icon: Clapperboard },
      { id: "preview", label: "Preview", icon: PlaySquare },
      { id: "storyboard", label: "Storyboard", icon: LayoutGrid },
      { id: "export", label: "Export", icon: Download },
    ],
  },
  {
    title: "Sistema",
    items: [
      { id: "providers", label: "AI Providers", icon: Server },
      { id: "brand", label: "Brand Profile", icon: Palette },
    ],
  },
];

const TERMINAL_JOB_STATES = ["Completato", "Errore", "Richiede intervento"];

export default function KoraStudioPage() {
  const [nav, setNav] = useState<NavId>("home");
  const [providerTab, setProviderTab] = useState<ProviderTab>("engines");

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

  // Solo i dati della sezione corrente: niente refresh globale.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (nav === "home") await loadStudio();
        if (nav === "projects") await loadProjects();
        if (nav === "vault") await loadVault();
        if (nav === "creative") {
          await Promise.all([loadProjects(), loadVault()]);
        }
        if (nav === "production" || nav === "preview" || nav === "storyboard" || nav === "export") {
          await loadProjects();
        }
        if (nav === "providers") {
          await loadSettingsData();
          if (providerTab === "budget" || providerTab === "ai") await loadSettingsData();
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
  }, [nav, providerTab, loadStudio, loadVault, loadProjects, loadSettingsData]);

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
    destinationDir?: string | null,
    media?: { mode: string; assetIds: string[]; priority?: string; note?: string }
  ) => {
    const effectiveMode: ProductionMode = customizeFirst ? "GUIDATA" : prodMode;
    const mediaMode = media?.mode || "vault_auto";
    const chosenIds =
      media && media.assetIds.length > 0
        ? media.assetIds
        : assetIds && assetIds.length > 0
          ? assetIds
          : concept.available_asset_ids.slice(0, 3);
    const request: Partial<CreativeConcept["creative_request"]> = {
      ...(concept.creative_request || {}),
      theme: theme || concept.title,
      objective: concept.objective || "",
      duration_sec: concept.duration_sec,
      voice_id: voiceId,
      asset_ids: chosenIds,
      media_selection: mediaMode,
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
        if (media) {
          // La scelta resta scritta nel progetto: nessun ritorno a vault_auto.
          try {
            await koraApi.setMediaSelection(res.project.id, {
              mode: media.mode,
              asset_ids: media.assetIds,
              priority: media.priority,
              note: media.note,
            });
          } catch (err) {
            setBanner(`Scelta materiali non salvata: ${(err as Error).message}`);
          }
        }
      }
      if (res.design.fallback_reason) setBanner(res.design.fallback_reason);
      setNav("preview");
    } catch (err) {
      setBanner(`Progettazione non riuscita: ${(err as Error).message}`);
      throw err;
    }
  };

  const handleProduceExisting = async (projectId: string) => {
    const res = await koraApi.produceProject(projectId);
    setActiveProjectId(projectId);
    setActiveJobId(res.job.id);
    setNav("production");
  };

  const goProjectTab = (id: string, tab?: "creative" | "production" | "preview" | "export") => {
    setActiveProjectId(id);
    if (tab === "export") setNav("export");
    else if (tab === "production") setNav("production");
    else if (tab === "creative") setNav("creative");
    else setNav("preview");
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
              The Art of Being · Policoro
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
          {NAV_GROUPS.map((group) => (
            <div key={group.title} className="contents lg:block">
              <div className="hidden px-2 pb-1 pt-3 text-[9px] uppercase tracking-[0.2em] text-[#9A8052] lg:block">
                {group.title}
              </div>
              {group.items.map((item) => {
                const Icon = item.icon;
                const active = nav === item.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setNav(item.id)}
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
            </div>
          ))}
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

        {activeProject && (
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

        {nav === "home" && (
          <div className="space-y-4">
            <EngineConnect variant="compact" />
            {bootReady ? (
              <HomeHub
                voices={voices}
                recentProjects={projects}
                onConceptsReady={() => {}}
                onStartProduction={handleProjectReel}
                onOpenProject={(id) => {
                  setActiveProjectId(id);
                  const pr = projects.find((x) => x.id === id);
                  setNav(pr?.output_video_path ? "preview" : "creative");
                }}
                onVaultUpdated={() => {
                  loaded.current.vault = false;
                  setBanner(null);
                }}
              />
            ) : (
              <SectionSkeleton label="Home" />
            )}
          </div>
        )}

        {nav === "projects" && (
          <ProjectsView
            projects={projects}
            activeProjectId={activeProject?.id || null}
            onSelectProject={goProjectTab}
            onRefresh={() => loadProjects(true)}
          />
        )}

        {nav === "vault" && <MediaVaultView assets={assets} onRefresh={() => loadVault(true)} />}

        {nav === "creative" && (
          <CreativeStudioView
            project={activeProject}
            assets={assets}
            voices={voices}
            onUpdated={async () => {
              const list = await koraApi.listProjects();
              setProjects(list);
            }}
            onProduce={handleProduceExisting}
          />
        )}

        {nav === "production" && (
          <ProductionView
            jobs={jobs}
            activeJob={activeJob}
            onRefresh={async () => {
              const jobList = await koraApi.listJobs();
              setJobs(jobList);
            }}
            onOpenPreview={(pid) => {
              setActiveProjectId(pid);
              setNav("preview");
            }}
          />
        )}

        {nav === "preview" && (
          <PreviewView
            project={activeProject}
            onRevisionStarted={(jobId) => {
              setActiveJobId(jobId);
              setNav("production");
            }}
            onRestored={async () => {
              const list = await koraApi.listProjects();
              setProjects(list);
            }}
            onGoExport={() => setNav("export")}
            onOpenScenes={(pid) => {
              setActiveProjectId(pid);
              setNav("storyboard");
            }}
            onProduce={(projectId) => void handleProduceExisting(projectId)}
          />
        )}

        {nav === "storyboard" && (
          <div className="space-y-4">
            {projects.length > 1 && (
              <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-[#EDE6D9] bg-white px-4 py-3">
                <Film className="h-4 w-4 text-[#9A8052]" />
                <span className="text-[11px] text-[#6C6459]">Progetto</span>
                <select
                  value={activeProject?.id || ""}
                  onChange={(e) => setActiveProjectId(e.target.value)}
                  className="rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-xs"
                >
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.title}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {activeProject ? (
              <SceneEditor
                projectId={activeProject.id}
                projectTitle={activeProject.title}
                onPreview={() => setNav("preview")}
              />
            ) : (
              <p className="rounded-2xl border border-[#EDE6D9] bg-white p-6 text-sm text-[#6C6459]">
                Apri un progetto dalla Home o da Progetti per lavorare sulle scene.
              </p>
            )}
          </div>
        )}

        {nav === "providers" && (
          <div className="space-y-5">
            <div className="flex flex-wrap gap-2">
              {(
                [
                  ["engines", "Motori locali"],
                  ["ai", "Motore AI"],
                  ["models", "Modelli & benchmark"],
                  ["budget", "Provider & budget"],
                ] as [ProviderTab, string][]
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setProviderTab(id)}
                  className={`rounded-xl px-3.5 py-2 text-xs font-medium transition ${
                    providerTab === id
                      ? "bg-[#1D1B18] text-[#FAF7F2]"
                      : "border border-[#DDD4C4] bg-white text-[#4A443C] hover:border-[#9A8052]"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            {providerTab === "engines" && (
              <div className="space-y-4">
                <EnginesInventory />
                <EngineConnect variant="full" />
              </div>
            )}
            {providerTab === "ai" && <AIEngineView />}
            {providerTab === "models" && <CreativeEngineModels />}
            {providerTab === "budget" && (
              <ProvidersView
                providers={providers}
                budget={budget}
                voices={voices}
                onRefresh={() => loadSettingsData(true)}
              />
            )}
          </div>
        )}

        {nav === "brand" && <BrandProfileView />}

        {nav === "export" && (
          <ExportView
            project={activeProject}
            onRefresh={async () => {
              const list = await koraApi.listProjects();
              setProjects(list);
            }}
          />
        )}
      </main>
    </div>
  );
}
