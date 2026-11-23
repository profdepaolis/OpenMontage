"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clapperboard,
  CircleDot,
  Film,
  ImagePlus,
  Loader2,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { koraApi, type SceneRow, type VaultOption } from "@/lib/api";
import { CreativeDirectionPanel } from "./CreativeDirectionPanel";
import { SceneMaterialsPanel } from "./SceneMaterialsPanel";

interface Props {
  projectId: string;
  projectTitle?: string;
  onPreview?: () => void;
}

type Progress = {
  percent: number;
  done: number;
  total: number;
  current: string;
  stages: Array<{ id: string; label: string; done: boolean; detail: string }>;
};

type Readiness = {
  ready: boolean;
  render_ready?: boolean;
  scene_count: number;
  total_frames?: number;
  target_frames?: number;
  audio_state?: string;
  preview_note?: string;
  checks: Array<{ id: string; label: string; ok: boolean; detail: string; blocking: boolean }>;
};

/**
 * Produzione delle scene: guarda a che punto è il reel, aggiunge/toglie scene,
 * cambia il materiale, riscrive il testo (anche con l'AI collegata) e rigenera l'anteprima.
 */
export function SceneEditor({ projectId, projectTitle, onPreview }: Props) {
  const [scenes, setScenes] = useState<SceneRow[]>([]);
  const [assets, setAssets] = useState<VaultOption[]>([]);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string>("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "info"; text: string } | null>(null);
  const [drafts, setDrafts] = useState<Record<number, { text: string; assetId: string; duration: string }>>({});
  const [instruction, setInstruction] = useState<Record<number, string>>({});
  const [newScene, setNewScene] = useState({ assetId: "", duration: "4", text: "" });
  const [showAdd, setShowAdd] = useState(false);
  const [proposal, setProposal] = useState<{
    scene: number;
    text: string;
    source: string;
    note: string;
  } | null>(null);

  const hydrateDrafts = useCallback((rows: SceneRow[]) => {
    const next: Record<number, { text: string; assetId: string; duration: string }> = {};
    for (const row of rows) {
      next[row.scene_number] = {
        text: row.spoken_text,
        assetId: row.asset_id || "",
        duration: row.duration_sec.toFixed(1),
      };
    }
    setDrafts(next);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [data, prog, ready] = await Promise.all([
        koraApi.listScenes(projectId),
        koraApi.projectProgress(projectId),
        koraApi.projectReadiness(projectId),
      ]);
      setScenes(data.scenes);
      setAssets(data.available_assets);
      hydrateDrafts(data.scenes);
      setProgress(prog);
      setReadiness(ready);
      setMsg(null);
    } catch (err) {
      setMsg({ kind: "err", text: (err as Error).message });
    } finally {
      setLoading(false);
    }
  }, [projectId, hydrateDrafts]);

  useEffect(() => {
    void load();
  }, [load]);

  const saveScene = async (scene: SceneRow) => {
    const draft = drafts[scene.scene_number];
    if (!draft) return;
    setBusy(`save-${scene.scene_number}`);
    try {
      const res = await koraApi.updateScene(projectId, scene.scene_number, {
        spoken_text: draft.text,
        asset_id: draft.assetId || undefined,
        duration_sec: Number(draft.duration) || undefined,
      });
      setScenes(res.scenes);
      hydrateDrafts(res.scenes);
      setMsg({ kind: "ok", text: `Scena ${scene.scene_number} salvata. L'anteprima precedente è da rigenerare.` });
      const ready = await koraApi.projectReadiness(projectId);
      setReadiness(ready);
      const prog = await koraApi.projectProgress(projectId);
      setProgress(prog);
    } catch (err) {
      setMsg({ kind: "err", text: (err as Error).message });
    } finally {
      setBusy("");
    }
  };

  const generateText = async (scene: SceneRow) => {
    setBusy(`text-${scene.scene_number}`);
    try {
      const res = await koraApi.sceneText(projectId, scene.scene_number, instruction[scene.scene_number] || "");
      if (!res.ok) {
        setMsg({ kind: "err", text: res.note });
        setProposal(null);
        return;
      }
      // proposta: NON viene applicata finché non premi «Applica»
      setProposal({ scene: scene.scene_number, text: res.text, source: res.source, note: res.note });
      setMsg({ kind: "info", text: res.note });
    } catch (err) {
      setMsg({ kind: "err", text: (err as Error).message });
    } finally {
      setBusy("");
    }
  };

  const applyProposal = async () => {
    if (!proposal) return;
    setBusy(`apply-${proposal.scene}`);
    try {
      const res = await koraApi.applySceneText(projectId, proposal.scene, proposal.text);
      setScenes((rows) =>
        rows.map((row) => (row.scene_number === proposal.scene ? { ...row, spoken_text: res.text } : row))
      );
      setDrafts((d) => ({
        ...d,
        [proposal.scene]: {
          ...(d[proposal.scene] || { text: "", assetId: "", duration: "" }),
          text: res.text,
        },
      }));
      setMsg({ kind: "ok", text: `Testo applicato alla scena ${proposal.scene}. Audio e sottotitoli andranno rigenerati.` });
      setProposal(null);
      const [ready, prog] = await Promise.all([
        koraApi.projectReadiness(projectId),
        koraApi.projectProgress(projectId),
      ]);
      setReadiness(ready);
      setProgress(prog);
    } catch (err) {
      setMsg({ kind: "err", text: (err as Error).message });
    } finally {
      setBusy("");
    }
  };

  const removeScene = async (scene: SceneRow) => {
    if (!window.confirm(`Eliminare la scena ${scene.scene_number}? Il reel verrà ricalcolato.`)) return;
    setBusy(`del-${scene.scene_number}`);
    try {
      const res = await koraApi.deleteScene(projectId, scene.scene_number);
      setScenes(res.scenes);
      hydrateDrafts(res.scenes);
      setMsg({ kind: "ok", text: `Scena ${scene.scene_number} rimossa: restano ${res.scenes.length} scene.` });
      const prog = await koraApi.projectProgress(projectId);
      setProgress(prog);
    } catch (err) {
      setMsg({ kind: "err", text: (err as Error).message });
    } finally {
      setBusy("");
    }
  };

  const addScene = async () => {
    if (!newScene.assetId) {
      setMsg({ kind: "err", text: "Scegli un materiale per la nuova scena." });
      return;
    }
    setBusy("add");
    try {
      const res = await koraApi.addScene(projectId, {
        asset_id: newScene.assetId,
        duration_sec: Number(newScene.duration) || 4,
        spoken_text: newScene.text,
      });
      setScenes(res.scenes);
      hydrateDrafts(res.scenes);
      setNewScene({ assetId: "", duration: "4", text: "" });
      setShowAdd(false);
      setMsg({ kind: "ok", text: `Scena aggiunta (${Math.round(Number(res.added.duration_sec))} s): totale ${res.scenes.length} scene.` });
      const prog = await koraApi.projectProgress(projectId);
      setProgress(prog);
    } catch (err) {
      setMsg({ kind: "err", text: (err as Error).message });
    } finally {
      setBusy("");
    }
  };

  const missing = (readiness?.checks || []).filter((c) => !c.ok);

  return (
    <div className="space-y-4">
      <section className="rounded-2xl border border-[#EDE6D9] bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-[#1D1B18]">
            <Clapperboard className="h-4 w-4 text-[#9A8052]" /> Produzione scene
            {projectTitle ? <span className="text-[11px] font-normal text-[#6C6459]">· {projectTitle}</span> : null}
          </h2>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-[#DDD4C4] px-2 py-1 text-[11px] text-[#4A443C] disabled:opacity-50"
            >
              {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />} Aggiorna
            </button>
            <button
              type="button"
              disabled={busy === "captions"}
              onClick={async () => {
                setBusy("captions");
                try {
                  const res = await koraApi.alignCaptions(projectId);
                  if (res.scenes) {
                    setScenes(res.scenes);
                    hydrateDrafts(res.scenes);
                  }
                  setMsg({
                    kind: res.updated_scenes.length ? "ok" : "info",
                    text: res.updated_scenes.length
                      ? `Sottotitoli riallineati alle scene ${res.updated_scenes.join(", ")}. La voce non cambia: rigenera l'anteprima per vederli.`
                      : "Sottotitoli già allineati al testo: nessuna modifica.",
                  });
                } catch (err) {
                  setMsg({ kind: "err", text: `Allineamento non riuscito: ${(err as Error).message}` });
                } finally {
                  setBusy("");
                }
              }}
              title="Riporta i sottotitoli alla frase esatta del voiceover"
              className="inline-flex items-center gap-1.5 rounded-lg border border-[#DDD4C4] px-2 py-1 text-[11px] text-[#4A443C] disabled:opacity-50"
            >
              {busy === "captions" ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <RefreshCw className="h-3 w-3" />
              )}
              Allinea sottotitoli
            </button>
            {onPreview ? (
              <button
                type="button"
                onClick={onPreview}
                className="inline-flex items-center gap-1.5 rounded-lg bg-[#1D1B18] px-2.5 py-1 text-[11px] text-[#FAF7F2]"
              >
                <Film className="h-3 w-3" /> Vai all'anteprima
              </button>
            ) : null}
          </div>
        </div>

        {/* avanzamento reale */}
        <div className="mt-3">
          <div className="flex items-center justify-between text-[11px] text-[#6C6459]">
            <span>
              Avanzamento: <strong className="text-[#1D1B18]">{progress?.percent ?? 0}%</strong>{" "}
              {progress ? `· fase attuale: ${progress.current}` : null}
            </span>
            <span>
              {progress?.done ?? 0}/{progress?.total ?? 0} fasi completate
            </span>
          </div>
          <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-[#EFE9DE]">
            <div
              className="h-full rounded-full bg-[#224C66] transition-all"
              style={{ width: `${progress?.percent ?? 0}%` }}
            />
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {(progress?.stages || []).map((stage) => (
              <span
                key={stage.id}
                title={stage.detail}
                className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] ${
                  stage.done
                    ? "bg-[#2F6B4F]/10 text-[#2F6B4F]"
                    : "border border-[#E2D9C8] bg-[#FAF7F2] text-[#6C6459]"
                }`}
              >
                {stage.done ? <CheckCircle2 className="h-3 w-3" /> : <CircleDot className="h-3 w-3" />}
                {stage.label}
              </span>
            ))}
          </div>
        </div>
      </section>

      <CreativeDirectionPanel projectId={projectId} onChanged={() => void load()} />

      {missing.length ? (
        <section className="rounded-2xl border border-[#E9D6BF] bg-[#FBF4E9] p-3">
          <p className="flex items-center gap-1.5 text-[11px] font-medium text-[#8A5A22]">
            <AlertTriangle className="h-3.5 w-3.5" /> Prima di generare l'anteprima:
          </p>
          <ul className="mt-1 space-y-1 text-[11px] text-[#7A5A34]">
            {missing.map((check) => (
              <li key={check.id}>
                <strong>{check.label}</strong> — {check.detail}
                {check.blocking ? " (bloccante)" : ""}
              </li>
            ))}
          </ul>
        </section>
      ) : readiness ? (
        <p className="flex flex-col gap-0.5 rounded-2xl border border-[#CFE3D6] bg-[#F1F8F3] px-3 py-2 text-[11px] text-[#2F6B4F]">
          <span className="flex items-center gap-1.5">
            <CheckCircle2 className="h-3.5 w-3.5" /> Anteprima: pronta.
            {readiness.preview_note ? <span className="text-[#2F6B4F]/80"> {readiness.preview_note}</span> : null}
          </span>
          <span className="text-[#2F6B4F]/80">
            Render finale: {readiness.render_ready ? "pronto" : "mancano ancora controlli bloccanti"} ·{" "}
            {readiness.total_frames ?? "?"}/{readiness.target_frames ?? "?"} frame
          </span>
        </p>
      ) : null}

      {msg ? (
        <p
          className={`rounded-2xl px-3 py-2 text-[11px] ${
            msg.kind === "err"
              ? "bg-[#B4472F]/10 text-[#B4472F]"
              : msg.kind === "ok"
                ? "bg-[#2F6B4F]/10 text-[#2F6B4F]"
                : "bg-[#224C66]/10 text-[#224C66]"
          }`}
        >
          {msg.text}
        </p>
      ) : null}

      {proposal ? (
        <section className="rounded-2xl border border-[#CFD9E4] bg-[#F3F7FA] p-3">
          <p className="text-[11px] font-medium text-[#224C66]">
            Proposta per la scena {proposal.scene} · fonte: {proposal.source}
          </p>
          <p className="mt-1 text-xs text-[#1D1B18]">«{proposal.text}»</p>
          <p className="mt-1 text-[10px] text-[#6C6459]">{proposal.note}</p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => void applyProposal()}
              disabled={busy === `apply-${proposal.scene}`}
              className="rounded-lg bg-[#224C66] px-2.5 py-1 text-[10px] font-medium text-white disabled:opacity-50"
            >
              {busy === `apply-${proposal.scene}` ? "Applico…" : "Applica"}
            </button>
            <button
              type="button"
              onClick={() => setProposal(null)}
              className="rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1 text-[10px] text-[#4A443C]"
            >
              Annulla
            </button>
          </div>
        </section>
      ) : null}

      <section className="space-y-2">
        {scenes.map((scene) => {
          const draft = drafts[scene.scene_number] || {
            text: scene.spoken_text,
            assetId: scene.asset_id || "",
            duration: scene.duration_sec.toFixed(1),
          };
          const asset = assets.find((a) => a.id === draft.assetId);
          return (
            <div key={scene.scene_number} className="rounded-2xl border border-[#EDE6D9] bg-white p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-[11px] text-[#6C6459]">
                  <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-[#1D1B18] text-[10px] font-medium text-[#FAF7F2]">
                    {scene.scene_number}
                  </span>
                  {scene.is_outro ? <span className="text-[#9A8052]">scena finale (logo)</span> : null}
                  <span>
                    {scene.start_sec.toFixed(1)}–{scene.end_sec.toFixed(1)} s
                  </span>
                  {asset ? <span className="text-[#9A8052]">· {asset.label}</span> : null}
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => void generateText(scene)}
                    disabled={busy === `text-${scene.scene_number}`}
                    className="inline-flex items-center gap-1 rounded-lg border border-[#DDD4C4] px-2 py-1 text-[10px] text-[#4A443C] disabled:opacity-50"
                  >
                    {busy === `text-${scene.scene_number}` ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Sparkles className="h-3 w-3" />
                    )}
                    Genera testo
                  </button>
                  <button
                    type="button"
                    onClick={() => void saveScene(scene)}
                    disabled={busy === `save-${scene.scene_number}`}
                    className="rounded-lg bg-[#224C66] px-2 py-1 text-[10px] font-medium text-white disabled:opacity-50"
                  >
                    {busy === `save-${scene.scene_number}` ? "Salvo…" : "Salva"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void removeScene(scene)}
                    disabled={busy === `del-${scene.scene_number}`}
                    className="inline-flex items-center gap-1 rounded-lg border border-[#E4C9BE] px-2 py-1 text-[10px] text-[#B4472F] disabled:opacity-50"
                  >
                    <Trash2 className="h-3 w-3" /> Togli
                  </button>
                </div>
              </div>

              <textarea
                value={draft.text}
                onChange={(event) =>
                  setDrafts((d) => ({ ...d, [scene.scene_number]: { ...draft, text: event.target.value } }))
                }
                rows={2}
                placeholder="Testo voice-over di questa scena"
                className="mt-2 w-full rounded-xl border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-2 text-xs leading-relaxed"
              />

              <div className="mt-2 flex flex-wrap items-end gap-2">
                <label className="min-w-[180px] flex-1 text-[10px] text-[#6C6459]">
                  Materiale
                  <select
                    value={draft.assetId}
                    onChange={(event) =>
                      setDrafts((d) => ({ ...d, [scene.scene_number]: { ...draft, assetId: event.target.value } }))
                    }
                    className="mt-1 block w-full rounded-lg border border-[#DDD4C4] bg-white px-2 py-1.5 text-[11px]"
                  >
                    <option value="">— nessuno —</option>
                    {assets.map((opt) => (
                      <option key={opt.id} value={opt.id}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="w-24 text-[10px] text-[#6C6459]">
                  Durata (s)
                  <input
                    type="number"
                    min={1}
                    max={15}
                    step={0.5}
                    value={draft.duration}
                    onChange={(event) =>
                      setDrafts((d) => ({ ...d, [scene.scene_number]: { ...draft, duration: event.target.value } }))
                    }
                    className="mt-1 block w-full rounded-lg border border-[#DDD4C4] bg-white px-2 py-1.5 text-[11px]"
                  />
                </label>
                <label className="min-w-[200px] flex-[2] text-[10px] text-[#6C6459]">
                  Indicazione per l'AI (facoltativa)
                  <input
                    value={instruction[scene.scene_number] || ""}
                    onChange={(event) =>
                      setInstruction((i) => ({ ...i, [scene.scene_number]: event.target.value }))
                    }
                    placeholder="es. più concreto, meno poetico"
                    className="mt-1 block w-full rounded-lg border border-[#DDD4C4] bg-white px-2 py-1.5 text-[11px]"
                  />
                </label>
              </div>

              <SceneMaterialsPanel
                projectId={projectId}
                scene={scene}
                onScenes={(rows) => {
                  setScenes(rows);
                  hydrateDrafts(rows);
                }}
                onNotify={(kind, text) => setMsg({ kind, text })}
              />
            </div>
          );
        })}
      </section>

      <section className="rounded-2xl border border-dashed border-[#DDD4C4] bg-[#FAF7F2] p-3">
        {!showAdd ? (
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-[#224C66]"
          >
            <Plus className="h-3.5 w-3.5" /> Aggiungi una scena
          </button>
        ) : (
          <div className="space-y-2">
            <p className="flex items-center gap-1.5 text-[11px] font-medium text-[#1D1B18]">
              <ImagePlus className="h-3.5 w-3.5 text-[#9A8052]" /> Nuova scena (va prima della scena finale)
            </p>
            <div className="flex flex-wrap items-end gap-2">
              <label className="min-w-[200px] flex-1 text-[10px] text-[#6C6459]">
                Materiale dal vault
                <select
                  value={newScene.assetId}
                  onChange={(event) => setNewScene((s) => ({ ...s, assetId: event.target.value }))}
                  className="mt-1 block w-full rounded-lg border border-[#DDD4C4] bg-white px-2 py-1.5 text-[11px]"
                >
                  <option value="">— scegli —</option>
                  {assets.map((opt) => (
                    <option key={opt.id} value={opt.id}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="w-24 text-[10px] text-[#6C6459]">
                Durata (s)
                <input
                  type="number"
                  min={1}
                  max={15}
                  step={0.5}
                  value={newScene.duration}
                  onChange={(event) => setNewScene((s) => ({ ...s, duration: event.target.value }))}
                  className="mt-1 block w-full rounded-lg border border-[#DDD4C4] bg-white px-2 py-1.5 text-[11px]"
                />
              </label>
              <label className="min-w-[220px] flex-[2] text-[10px] text-[#6C6459]">
                Testo (facoltativo, puoi generarlo dopo)
                <input
                  value={newScene.text}
                  onChange={(event) => setNewScene((s) => ({ ...s, text: event.target.value }))}
                  className="mt-1 block w-full rounded-lg border border-[#DDD4C4] bg-white px-2 py-1.5 text-[11px]"
                />
              </label>
              <button
                type="button"
                onClick={() => void addScene()}
                disabled={busy === "add"}
                className="inline-flex items-center gap-1.5 rounded-xl bg-[#1D1B18] px-3 py-1.5 text-[11px] font-medium text-[#FAF7F2] disabled:opacity-50"
              >
                {busy === "add" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />} Aggiungi
              </button>
              <button
                type="button"
                onClick={() => setShowAdd(false)}
                className="rounded-xl border border-[#DDD4C4] px-3 py-1.5 text-[11px] text-[#4A443C]"
              >
                Annulla
              </button>
            </div>
            <p className="text-[10px] text-[#9A8052]">
              Per foto o video nuovi: caricali prima in <strong>Archivio</strong>, poi sceglili qui.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

export default SceneEditor;
