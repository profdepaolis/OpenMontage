"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Home,
  FolderKanban,
  Images,
  Clapperboard,
  Activity,
  PlaySquare,
  Cpu,
  Palette,
  Download,
} from "lucide-react";
import type {
  BudgetSettings,
  CreativeConcept,
  HyperframesConfig,
  JobRecord,
  MediaAsset,
  NavSection,
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
import { ProvidersView } from "@/components/ProvidersView";
import { BrandProfileView } from "@/components/BrandProfileView";
import { ExportView } from "@/components/ExportView";

const NAV_ITEMS: Array<{ id: NavSection; label: string; icon: React.ComponentType<{ className?: string }> }> = [
  { id: "home", label: "Home", icon: Home },
  { id: "projects", label: "Progetti", icon: FolderKanban },
  { id: "vault", label: "Media Vault", icon: Images },
  { id: "creative", label: "Creative Studio", icon: Clapperboard },
  { id: "production", label: "Production", icon: Activity },
  { id: "preview", label: "Preview", icon: PlaySquare },
  { id: "providers", label: "AI Providers", icon: Cpu },
  { id: "brand", label: "Brand Profile", icon: Palette },
  { id: "export", label: "Export", icon: Download },
];

export default function KoraStudioPage() {
  const [section, setSection] = useState<NavSection>("home");
  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [voices, setVoices] = useState<VoiceOption[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [providers, setProviders] = useState<ProviderRecord[]>([]);
  const [budget, setBudget] = useState<BudgetSettings | null>(null);

  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const refreshAll = useCallback(async () => {
    const [vAssets, pList, vList, jList, provList, bData] = await Promise.all([
      koraApi.listVault(),
      koraApi.listProjects(),
      koraApi.listVoices(),
      koraApi.listJobs(),
      koraApi.listProviders(),
      koraApi.getBudget(),
    ]);
    setAssets(vAssets);
    setProjects(pList);
    setVoices(vList);
    setJobs(jList);
    setProviders(provList);
    setBudget(bData);
  }, []);

  useEffect(() => {
    refreshAll().catch(() => {});
  }, [refreshAll]);

  // Poll active running job automatically until completion/error
  const activeJob = jobs.find((j) => j.id === activeJobId) || null;
  useEffect(() => {
    if (!activeJob) return;
    const isTerminal =
      activeJob.state === "Completato" ||
      activeJob.state === "Errore" ||
      activeJob.state === "Richiede intervento";
    if (isTerminal) return;

    const timer = setInterval(() => {
      refreshAll().catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [activeJob, refreshAll]);

  const activeProject = projects.find((p) => p.id === activeProjectId) || projects[0] || null;

  const handleStartProduction = async (
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
    const res = await koraApi.createProject({
      title: concept.title,
      theme,
      mode,
      preset: concept.preset,
      production_mode: effectiveMode,
      voice_id: voiceId,
      target_duration_sec: concept.duration_sec,
      concept,
      caption_style: captionStyle || hyperframes?.caption_style,
      hyperframes,
      creative_request: concept.creative_request,
      asset_ids: assetIds && assetIds.length > 0 ? assetIds : undefined,
      destination_dir: destinationDir || undefined,
      auto_start: !customizeFirst && prodMode === "AUTO",
    });
    setActiveProjectId(res.project.id);
    if (res.job) {
      setActiveJobId(res.job.id);
      setSection("production");
    } else {
      setSection("creative");
    }
    await refreshAll();
  };

  const handleProduceExisting = async (projectId: string) => {
    const res = await koraApi.produceProject(projectId);
    setActiveProjectId(projectId);
    setActiveJobId(res.job.id);
    setSection("production");
    await refreshAll();
  };

  return (
    <div className="min-h-screen flex flex-col lg:flex-row bg-[#FAF7F2]">
      {/* Sidebar Desktop + Header Mobile */}
      <aside className="border-b border-[#DDD4C4] bg-white/90 px-4 py-4 lg:w-64 lg:shrink-0 lg:border-b-0 lg:border-r lg:py-8 lg:px-5">
        <div className="flex items-center justify-between lg:block">
          <div>
            <div className="font-editorial text-xl tracking-wide text-[#1D1B18]">KORA STUDIO</div>
            <div className="text-[10px] uppercase tracking-[0.2em] text-[#9A8052]">
              The Art of Being · Policoro
            </div>
          </div>
          {activeJob &&
            activeJob.state !== "Completato" &&
            activeJob.state !== "Errore" && (
              <span className="lg:mt-3 inline-flex items-center gap-1.5 rounded-full bg-[#224C66]/10 px-2.5 py-1 text-[11px] font-medium text-[#224C66]">
                <span className="h-2 w-2 rounded-full bg-[#224C66] animate-pulse" />
                {activeJob.state}
              </span>
            )}
        </div>

        <nav className="mt-4 flex gap-1.5 overflow-x-auto pb-1 lg:mt-8 lg:flex-col lg:overflow-visible">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = section === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setSection(item.id)}
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

        {activeProject && (
          <div className="mt-8 hidden rounded-2xl border border-[#EDE6D9] bg-[#FAF7F2] p-3.5 lg:block">
            <div className="text-[10px] uppercase tracking-wider text-[#9A8052]">Progetto attivo</div>
            <div className="mt-1 truncate text-xs font-medium text-[#1D1B18]">
              {activeProject.title}
            </div>
            <div className="mt-0.5 text-[11px] text-[#6C6459]">
              {activeProject.status} · v{activeProject.current_version}
            </div>
          </div>
        )}

        {budget && (
          <div className="mt-3 hidden rounded-2xl border border-[#EDE6D9] bg-white p-3.5 text-[11px] text-[#6C6459] lg:block">
            <div className="flex items-center justify-between">
              <span>Motore locale</span>
              <span className="font-medium text-emerald-700">Operativo</span>
            </div>
            <div className="mt-1 flex items-center justify-between">
              <span>Spesa mese</span>
              <span className="font-medium text-[#1D1B18]">
                {budget.actual_month_eur.toFixed(2)} / {budget.monthly_cap_eur.toFixed(0)} €
              </span>
            </div>
            <div className="mt-1 flex items-center justify-between">
              <span>Budget pagam.</span>
              <span
                className={`font-medium ${
                  budget.paid_budget_eur > 0 ? "text-emerald-700" : "text-amber-700"
                }`}
              >
                {budget.paid_budget_eur.toFixed(2)} €
              </span>
            </div>
          </div>
        )}
      </aside>

      {/* Main Content */}
      <main className="flex-1 p-5 md:p-8 lg:p-10 overflow-y-auto">
        {section === "home" && (
          <HomeHub
            voices={voices}
            recentProjects={projects}
            onConceptsReady={() => {}}
            onStartProduction={handleStartProduction}
            onOpenProject={(id) => {
              setActiveProjectId(id);
              const pr = projects.find((x) => x.id === id);
              setSection(pr?.output_video_path ? "preview" : "creative");
            }}
            onVaultUpdated={() => refreshAll()}
          />
        )}

        {section === "projects" && (
          <ProjectsView
            projects={projects}
            activeProjectId={activeProject?.id || null}
            onSelectProject={(id, tab) => {
              setActiveProjectId(id);
              if (tab) setSection(tab);
            }}
            onRefresh={() => refreshAll()}
          />
        )}

        {section === "vault" && (
          <MediaVaultView assets={assets} onRefresh={() => refreshAll()} />
        )}

        {section === "creative" && (
          <CreativeStudioView
            project={activeProject}
            assets={assets}
            voices={voices}
            onUpdated={() => refreshAll()}
            onProduce={handleProduceExisting}
          />
        )}

        {section === "production" && (
          <ProductionView
            jobs={jobs}
            activeJob={activeJob}
            onRefresh={() => refreshAll()}
            onOpenPreview={(pid) => {
              setActiveProjectId(pid);
              setSection("preview");
            }}
          />
        )}

        {section === "preview" && (
          <PreviewView
            project={activeProject}
            onRevisionStarted={(jobId) => {
              setActiveJobId(jobId);
              setSection("production");
              refreshAll();
            }}
            onRestored={() => refreshAll()}
            onGoExport={() => setSection("export")}
          />
        )}

        {section === "providers" && (
          <ProvidersView
            providers={providers}
            budget={budget}
            voices={voices}
            onRefresh={() => refreshAll()}
          />
        )}

        {section === "brand" && <BrandProfileView />}

        {section === "export" && (
          <ExportView project={activeProject} onRefresh={() => refreshAll()} />
        )}
      </main>
    </div>
  );
}
