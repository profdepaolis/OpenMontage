"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Clapperboard,
  Gem,
  Loader2,
  Lock,
  MessageSquare,
  Sparkles,
  Type,
  Unlock,
  Wand2,
  X,
} from "lucide-react";
import {
  koraApi,
  type CaptionPreset,
  type DirectionChange,
  type GenerationRecord,
  type SceneRow,
  type ScriptProposal,
} from "@/lib/api";

interface Props {
  projectId: string;
  onChanged?: () => void;
}

type Msg = { kind: "ok" | "err" | "info"; text: string } | null;

const FRAMINGS = ["primissimo_piano", "primo_piano", "piano_medio", "campo_lungo", "dettaglio"];
const MOTIONS = [
  "slow_push_in",
  "slow_pull_out",
  "pan_left",
  "pan_right",
  "tilt_up",
  "tilt_down",
  "micro_parallax",
  "static_breathe",
];
const EASINGS = ["linear", "ease_in", "ease_out", "ease_in_out"];
const COMPOSITIONS = ["regola_terzi", "centro", "orizzonte_basso", "orizzonte_alto", "simmetria"];
const LOCK_LABELS: Record<string, string> = {
  scene: "Scena",
  visual: "Visual",
  voice: "Voce",
  duration: "Durata",
  direction: "Regia",
  caption: "Caption",
};
const SCOPES = [
  { id: "scene", label: "Una scena" },
  { id: "group", label: "Più scene" },
  { id: "full", label: "Tutto il copione" },
  { id: "hook", label: "Solo hook" },
  { id: "closing", label: "Solo chiusura" },
  { id: "caption", label: "Caption Instagram" },
  { id: "cta", label: "Call to action" },
];

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-[#8C8377]">
      {label}
      {children}
    </label>
  );
}

const selectClass =
  "rounded-lg border border-[#E2D9C8] bg-white px-2 py-1 text-[11px] text-[#1D1B18] focus:outline-none focus:ring-1 focus:ring-[#224C66]";

/**
 * Regia artistica (v3.5): un solo campo per chiedere le modifiche, i pannelli
 * per copione, sottotitoli e regia di scena, più il generatore di scene AI con
 * controllo dei costi. Tutto opzionale: la produzione automatica resta il percorso principale.
 */
export function CreativeDirectionPanel({ projectId, onChanged }: Props) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"direzione" | "copione" | "caption" | "regia">("direzione");
  const [scenes, setScenes] = useState<SceneRow[]>([]);
  const [presets, setPresets] = useState<CaptionPreset[]>([]);
  const [captionState, setCaptionState] = useState<{ preset: string; overrides: Record<string, unknown> }>({
    preset: "kora_editorial",
    overrides: {},
  });
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<Msg>(null);

  // direzione conversazionale
  const [instruction, setInstruction] = useState("");
  const [changes, setChanges] = useState<DirectionChange[] | null>(null);
  const [changesMeta, setChangesMeta] = useState<{ source: string; notes: string[] } | null>(null);

  // copione
  const [scope, setScope] = useState("scene");
  const [scriptInstruction, setScriptInstruction] = useState("");
  const [scriptSceneNumbers, setScriptSceneNumbers] = useState<number[]>([]);
  const [scriptProposals, setScriptProposals] = useState<ScriptProposal[] | null>(null);
  const [scriptMeta, setScriptMeta] = useState<{ source: string; note: string; locked: number[] } | null>(null);

  // caption
  const [captionScene, setCaptionScene] = useState<number | "reel">("reel");
  const [fontSize, setFontSize] = useState(44);
  const [color, setColor] = useState("#F7F3EA");
  const [position, setPosition] = useState("bottom");
  const [align, setAlign] = useState("center");
  const [titleDraft, setTitleDraft] = useState<Record<number, string>>({});

  // generatore
  const [modes, setModes] = useState<Array<{ id: string; label: string; cost: string; description: string; requires_reference: boolean; tested: boolean }>>([]);
  const [genMode, setGenMode] = useState("A");
  const [genScene, setGenScene] = useState(1);
  const [genPrompt, setGenPrompt] = useState("");
  const [genDuration, setGenDuration] = useState(5);
  const [preflight, setPreflight] = useState<{ available: boolean; requires_authorization: boolean; tested: boolean; declared_limit: string; checks: Array<{ check: string; ok: boolean; detail: string }> } | null>(null);
  const [history, setHistory] = useState<GenerationRecord[]>([]);

  const load = useCallback(async () => {
    try {
      const data = await koraApi.listScenes(projectId);
      setScenes(data.scenes);
      const cap = await koraApi.captionPanel(projectId);
      setPresets(cap.presets);
      setCaptionState({ preset: cap.preset, overrides: (cap.overrides || {}) as Record<string, unknown> });
    } catch (err) {
      setMsg({ kind: "err", text: (err as Error).message });
    }
  }, [projectId]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  useEffect(() => {
    if (!open || tab !== "regia") return;
    void koraApi
      .generatorModes()
      .then((data) => setModes(data.modes))
      .catch(() => setModes([]));
    void koraApi
      .generatorHistory(projectId)
      .then((data) => setHistory(data.generations))
      .catch(() => setHistory([]));
  }, [open, tab, projectId]);

  const editableScenes = useMemo(() => scenes.filter((s) => !s.is_outro), [scenes]);

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    setMsg(null);
    try {
      await fn();
    } catch (err) {
      setMsg({ kind: "err", text: (err as Error).message });
    } finally {
      setBusy("");
    }
  };

  const proposeDirection = () =>
    run("dir-propose", async () => {
      const res = await koraApi.directionPropose(projectId, instruction);
      setChanges(res.changes);
      setChangesMeta({ source: res.source, notes: res.notes });
      if (!res.changes.length) setMsg({ kind: "info", text: res.notes[0] || "Nessuna modifica trovata." });
    });

  const applyDirection = () =>
    run("dir-apply", async () => {
      const res = await koraApi.directionApply(projectId, changes || []);
      if (res.failed.length) {
        setMsg({ kind: "err", text: res.failed.map((f) => f.error).join(" · ") });
      } else {
        setMsg({
          kind: "ok",
          text: res.audio_invalidated
            ? "Modifiche applicate. Il voiceover va rigenerato: l'anteprima precedente non è più valida."
            : "Modifiche applicate. Nessuna rigenerazione audio necessaria.",
        });
      }
      setChanges(null);
      await load();
      onChanged?.();
    });

  const proposeScript = () =>
    run("script-propose", async () => {
      const res = await koraApi.scriptPropose(projectId, {
        scope,
        scene_numbers: scriptSceneNumbers,
        instruction: scriptInstruction,
      });
      setScriptProposals(res.proposals || []);
      setScriptMeta({
        source: res.source,
        note: res.note || res.reason || "",
        locked: res.locked_scenes || [],
      });
    });

  const applyScript = () =>
    run("script-apply", async () => {
      const chosen = (scriptProposals || []).filter((p) => p.changed);
      if (!chosen.length) {
        setMsg({ kind: "info", text: "Nessuna proposta cambiata da applicare." });
        return;
      }
      const res = await koraApi.scriptApply(projectId, { scope, proposals: chosen });
      setMsg({
        kind: res.ok ? "ok" : "err",
        text: res.reason
          ? res.reason
          : res.audio_invalidated
            ? "Copione applicato. Il voiceover verrà rigenerato alla prossima anteprima."
            : "Copione applicato senza toccare l'audio.",
      });
      setScriptProposals(null);
      await load();
      onChanged?.();
    });

  const applyCaption = (target: number | "reel") =>
    run("caption-apply", async () => {
      const res = await koraApi.captionApply(projectId, {
        preset: captionState.preset,
        overrides: { ...captionState.overrides, font_size: fontSize, color, position, align },
        scene_number: target === "reel" ? undefined : target,
      });
      setMsg({
        kind: res.blocked ? "err" : "ok",
        text: res.reason || res.note || "Sottotitoli aggiornati: la voce non viene rigenerata.",
      });
      onChanged?.();
    });

  const patchScene = (number: number, patch: Record<string, unknown>) =>
    run(`patch-${number}`, async () => {
      const res = await koraApi.updateScene(projectId, number, patch);
      setScenes(res.scenes);
      onChanged?.();
    });

  const toggleLock = (scene: SceneRow, key: string) =>
    run(`lock-${scene.scene_number}-${key}`, async () => {
      const next = { ...(scene.locks || {}), [key]: !(scene.locks || {})[key] };
      const res = await koraApi.sceneLocks(projectId, scene.scene_number, next);
      setScenes(res.scenes);
      onChanged?.();
    });

  const runPreflight = () =>
    run("preflight", async () => {
      const res = await koraApi.generatorPreflight({ mode: genMode });
      setPreflight(res);
    });

  const generate = (authorized = false) =>
    run("generate", async () => {
      const res = await koraApi.generatorGenerate(projectId, {
        mode: genMode,
        scene_number: genScene,
        prompt: genPrompt,
        duration_sec: genDuration,
        explicit_authorization: authorized,
      });
      if (res.requires_authorization) {
        setMsg({ kind: "info", text: res.reason || "Serve un'autorizzazione esplicita." });
        return;
      }
      if (res.generation?.status === "blocked") {
        setMsg({ kind: "err", text: res.reason || "Richiesta bloccata dal Brand Guardian: nessun elemento inventato." });
      } else {
        setMsg({ kind: res.ok ? "ok" : "err", text: res.note || "Generazione registrata: l'originale non è stato toccato." });
      }
      const hist = await koraApi.generatorHistory(projectId);
      setHistory(hist.generations);
    });

  return (
    <section className="rounded-2xl border border-[#E2D9C8] bg-[#FCFAF6]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left"
      >
        <span className="flex items-center gap-2 text-[12px] font-semibold text-[#1D1B18]">
          <Gem className="h-4 w-4 text-[#9A8052]" />
          Regia artistica
          <span className="rounded-full bg-[#EFE9DE] px-1.5 py-0.5 text-[9px] font-normal text-[#6C6459]">
            opzionale
          </span>
        </span>
        <ChevronDown className={`h-4 w-4 text-[#8C8377] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open ? (
        <div className="border-t border-[#E2D9C8] px-3 pb-3 pt-2">
          <div className="flex flex-wrap gap-1.5">
            {(
              [
                ["direzione", "Come vuoi cambiarlo"],
                ["copione", "Copione"],
                ["caption", "Sottotitoli"],
                ["regia", "Regia e scene AI"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setTab(id)}
                className={`rounded-full px-2.5 py-1 text-[10px] ${
                  tab === id
                    ? "bg-[#224C66] text-white"
                    : "border border-[#E2D9C8] bg-white text-[#6C6459] hover:border-[#224C66]"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {msg ? (
            <p
              className={`mt-2 flex items-start gap-1.5 rounded-lg px-2 py-1.5 text-[11px] ${
                msg.kind === "ok"
                  ? "bg-[#2F6B4F]/10 text-[#2F6B4F]"
                  : msg.kind === "err"
                    ? "bg-[#8A3A2A]/10 text-[#8A3A2A]"
                    : "bg-[#EFE9DE] text-[#6C6459]"
              }`}
            >
              {msg.kind === "ok" ? <Check className="mt-0.5 h-3 w-3 shrink-0" /> : <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />}
              <span className="whitespace-pre-wrap">{msg.text}</span>
            </p>
          ) : null}

          {tab === "direzione" ? (
            <div className="mt-2 space-y-2">
              <textarea
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder="Es. «la scena 3 più lenta», «tutte le scene con inquadratura più larga», «sottotitoli minimal»"
                rows={2}
                className="w-full rounded-xl border border-[#E2D9C8] bg-white px-2.5 py-2 text-[12px] text-[#1D1B18] focus:outline-none focus:ring-1 focus:ring-[#224C66]"
              />
              <div className="flex flex-wrap gap-1.5">
                <button
                  type="button"
                  disabled={!instruction.trim() || busy === "dir-propose"}
                  onClick={proposeDirection}
                  className="inline-flex items-center gap-1.5 rounded-full bg-[#224C66] px-3 py-1.5 text-[11px] font-medium text-white disabled:opacity-40"
                >
                  {busy === "dir-propose" ? <Loader2 className="h-3 w-3 animate-spin" /> : <MessageSquare className="h-3 w-3" />}
                  Prepara le modifiche
                </button>
                <span className="self-center text-[10px] text-[#8C8377]">
                  nulla viene applicato senza conferma · non avvia la produzione
                </span>
              </div>

              {changes && changes.length ? (
                <div className="rounded-xl border border-[#E2D9C8] bg-white p-2">
                  <p className="text-[10px] uppercase tracking-wide text-[#8C8377]">
                    Modifiche proposte {changesMeta ? `· fonte: ${changesMeta.source}` : ""}
                  </p>
                  <ul className="mt-1 space-y-1">
                    {changes.map((change, idx) => (
                      <li key={idx} className="flex items-start gap-1.5 text-[11px] text-[#1D1B18]">
                        <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-[#9A8052]" />
                        <span>
                          {change.scene_number ? <strong>Scena {change.scene_number} · </strong> : null}
                          {change.why || change.kind}
                          {change.patch ? (
                            <span className="text-[#6C6459]"> ({Object.keys(change.patch).join(", ")})</span>
                          ) : null}
                          {change.blocked ? <span className="text-[#8A3A2A]"> — bloccata: {change.reason}</span> : null}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-2 flex gap-1.5">
                    <button
                      type="button"
                      onClick={applyDirection}
                      disabled={busy === "dir-apply"}
                      className="inline-flex items-center gap-1 rounded-full bg-[#2F6B4F] px-3 py-1 text-[11px] font-medium text-white disabled:opacity-40"
                    >
                      {busy === "dir-apply" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                      Applica
                    </button>
                    <button
                      type="button"
                      onClick={() => setChanges(null)}
                      className="inline-flex items-center gap-1 rounded-full border border-[#E2D9C8] px-3 py-1 text-[11px] text-[#6C6459]"
                    >
                      <X className="h-3 w-3" /> Annulla
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}

          {tab === "copione" ? (
            <div className="mt-2 space-y-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <Field label="Livello">
                  <select className={selectClass} value={scope} onChange={(e) => setScope(e.target.value)}>
                    {SCOPES.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Scene">
                  <div className="flex max-w-[240px] flex-wrap gap-1">
                    {editableScenes.map((scene) => {
                      const on = scriptSceneNumbers.includes(scene.scene_number);
                      return (
                        <button
                          key={scene.scene_number}
                          type="button"
                          onClick={() =>
                            setScriptSceneNumbers((prev) =>
                              on ? prev.filter((n) => n !== scene.scene_number) : [...prev, scene.scene_number]
                            )
                          }
                          className={`h-6 w-6 rounded-md text-[10px] ${
                            on ? "bg-[#224C66] text-white" : "border border-[#E2D9C8] bg-white text-[#6C6459]"
                          }`}
                        >
                          {scene.scene_number}
                        </button>
                      );
                    })}
                  </div>
                </Field>
              </div>
              <textarea
                value={scriptInstruction}
                onChange={(e) => setScriptInstruction(e.target.value)}
                rows={2}
                placeholder="Istruzione libera: tono, cosa mettere in evidenza, cosa togliere…"
                className="w-full rounded-xl border border-[#E2D9C8] bg-white px-2.5 py-2 text-[12px] focus:outline-none focus:ring-1 focus:ring-[#224C66]"
              />
              <button
                type="button"
                onClick={proposeScript}
                disabled={busy === "script-propose"}
                className="inline-flex items-center gap-1.5 rounded-full bg-[#224C66] px-3 py-1.5 text-[11px] font-medium text-white disabled:opacity-40"
              >
                {busy === "script-propose" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Wand2 className="h-3 w-3" />}
                Proponi il nuovo testo
              </button>

              {scriptProposals ? (
                <div className="space-y-1.5 rounded-xl border border-[#E2D9C8] bg-white p-2">
                  <p className="text-[10px] uppercase tracking-wide text-[#8C8377]">
                    Prima → dopo {scriptMeta ? `· fonte: ${scriptMeta.source}` : ""}
                    {scriptMeta?.locked.length ? ` · scene bloccate: ${scriptMeta.locked.join(", ")}` : ""}
                  </p>
                  {scriptProposals.length ? (
                    scriptProposals.map((p) => (
                      <div key={`${p.scene_number}`} className="rounded-lg bg-[#FAF7F2] p-1.5 text-[11px]">
                        <p className="text-[10px] text-[#8C8377]">
                          {p.scene_number ? `Scena ${p.scene_number}` : "Fuori dal video"}
                        </p>
                        <p className="text-[#8C8377] line-through">{p.before || "—"}</p>
                        <p className="text-[#1D1B18]">{p.after}</p>
                      </div>
                    ))
                  ) : (
                    <p className="text-[11px] text-[#6C6459]">{scriptMeta?.note || "Nessuna proposta."}</p>
                  )}
                  {scriptProposals.length ? (
                    <button
                      type="button"
                      onClick={applyScript}
                      disabled={busy === "script-apply"}
                      className="inline-flex items-center gap-1 rounded-full bg-[#2F6B4F] px-3 py-1 text-[11px] font-medium text-white disabled:opacity-40"
                    >
                      {busy === "script-apply" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                      Applica
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}

          {tab === "caption" ? (
            <div className="mt-2 space-y-2">
              <div className="grid grid-cols-2 gap-1.5">
                <Field label="Preset">
                  <select
                    className={selectClass}
                    value={captionState.preset}
                    onChange={(e) => setCaptionState((prev) => ({ ...prev, preset: e.target.value }))}
                  >
                    {presets.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Applica a">
                  <select
                    className={selectClass}
                    value={String(captionScene)}
                    onChange={(e) => setCaptionScene(e.target.value === "reel" ? "reel" : Number(e.target.value))}
                  >
                    <option value="reel">Tutto il Reel</option>
                    {editableScenes.map((s) => (
                      <option key={s.scene_number} value={s.scene_number}>
                        Scena {s.scene_number}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Dimensione">
                  <input
                    type="range"
                    min={24}
                    max={72}
                    value={fontSize}
                    onChange={(e) => setFontSize(Number(e.target.value))}
                  />
                </Field>
                <Field label="Colore">
                  <input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="h-7 w-full rounded" />
                </Field>
                <Field label="Posizione">
                  <select className={selectClass} value={position} onChange={(e) => setPosition(e.target.value)}>
                    <option value="bottom">Basso (safe area IG)</option>
                    <option value="center">Centro</option>
                    <option value="top">Alto</option>
                  </select>
                </Field>
                <Field label="Allineamento">
                  <select className={selectClass} value={align} onChange={(e) => setAlign(e.target.value)}>
                    <option value="center">Centrato</option>
                    <option value="left">Sinistra</option>
                    <option value="right">Destra</option>
                  </select>
                </Field>
              </div>
              <p className="text-[10px] text-[#8C8377]">
                I sottotitoli cambiano senza rigenerare la voce. Testi fuori dall'area sicura Instagram vengono riportati
                dentro.
              </p>
              <button
                type="button"
                onClick={() => applyCaption(captionScene)}
                disabled={busy === "caption-apply"}
                className="inline-flex items-center gap-1.5 rounded-full bg-[#224C66] px-3 py-1.5 text-[11px] font-medium text-white disabled:opacity-40"
              >
                {busy === "caption-apply" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Type className="h-3 w-3" />}
                Applica sottotitoli
              </button>

              <div className="rounded-xl border border-[#E2D9C8] bg-white p-2">
                <p className="text-[10px] uppercase tracking-wide text-[#8C8377]">Titolo grafico interno (non pronunciato)</p>
                <div className="mt-1 flex flex-wrap items-end gap-1.5">
                  <Field label="Scena">
                    <select
                      className={selectClass}
                      value={String(genScene)}
                      onChange={(e) => setGenScene(Number(e.target.value))}
                    >
                      {editableScenes.map((s) => (
                        <option key={s.scene_number} value={s.scene_number}>
                          {s.scene_number}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <input
                    value={titleDraft[genScene] ?? ""}
                    onChange={(e) => setTitleDraft((prev) => ({ ...prev, [genScene]: e.target.value }))}
                    placeholder="Es. Mattino sul mare"
                    className="min-w-[180px] flex-1 rounded-lg border border-[#E2D9C8] px-2 py-1 text-[11px]"
                  />
                  <button
                    type="button"
                    onClick={() =>
                      run("title", async () => {
                        const res = await koraApi.sceneTitle(projectId, genScene, titleDraft[genScene] ?? "");
                        setMsg({ kind: res.blocked ? "err" : "ok", text: res.reason || "Titolo aggiornato (nessuna rigenerazione audio)." });
                      })
                    }
                    className="rounded-full bg-[#9A8052] px-3 py-1 text-[11px] font-medium text-white"
                  >
                    Salva titolo
                  </button>
                </div>
              </div>
            </div>
          ) : null}

          {tab === "regia" ? (
            <div className="mt-2 space-y-2">
              <div className="space-y-1.5">
                {editableScenes.map((scene) => (
                  <div key={scene.scene_number} className="rounded-xl border border-[#E2D9C8] bg-white p-2">
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-semibold text-[#1D1B18]">
                        Scena {scene.scene_number} · {scene.duration_sec.toFixed(2)} s
                        {scene.asset_history && scene.asset_history.length ? (
                          <span className="ml-1 text-[9px] text-[#8C8377]">({scene.asset_history.length} precedenti)</span>
                        ) : null}
                      </span>
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => run(`up-${scene.scene_number}`, async () => setScenes((await koraApi.sceneMove(projectId, scene.scene_number, "up")).scenes))}
                          className="rounded border border-[#E2D9C8] px-1 text-[10px] text-[#6C6459]"
                          title="Sposta su"
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          onClick={() => run(`down-${scene.scene_number}`, async () => setScenes((await koraApi.sceneMove(projectId, scene.scene_number, "down")).scenes))}
                          className="rounded border border-[#E2D9C8] px-1 text-[10px] text-[#6C6459]"
                          title="Sposta giù"
                        >
                          ↓
                        </button>
                        <button
                          type="button"
                          onClick={() => run(`dup-${scene.scene_number}`, async () => setScenes((await koraApi.sceneDuplicate(projectId, scene.scene_number)).scenes))}
                          className="rounded border border-[#E2D9C8] px-1 text-[10px] text-[#6C6459]"
                          title="Duplica"
                        >
                          ⧉
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            run(`preview-${scene.scene_number}`, async () => {
                              const info = await koraApi.scenePreview(projectId, scene.scene_number);
                              window.open(info.url, "_blank", "noopener");
                              setMsg({
                                kind: "info",
                                text: `Anteprima scena ${scene.scene_number} · ${info.resolution} · revisione ${info.revision}${info.cache_hit ? " · dalla cache" : ""}`,
                              });
                            })
                          }
                          className="rounded border border-[#E2D9C8] px-1 text-[10px] text-[#6C6459]"
                          title="Anteprima di questa scena (bassa risoluzione)"
                        >
                          ▶
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            run(`restore-${scene.scene_number}`, async () => {
                              const res = await koraApi.sceneRestoreAsset(projectId, scene.scene_number);
                              setMsg({ kind: res.ok ? "ok" : "err", text: res.reason || "Materiale precedente ripristinato." });
                              await load();
                            })
                          }
                          className="rounded border border-[#E2D9C8] px-1 text-[10px] text-[#6C6459]"
                          title="Ripristina materiale precedente"
                        >
                          ↺
                        </button>
                      </div>
                    </div>

                    <div className="mt-1.5 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
                      <Field label="Inquadratura">
                        <select
                          className={selectClass}
                          value={scene.shot_framing || "piano_medio"}
                          onChange={(e) => patchScene(scene.scene_number, { shot_framing: e.target.value })}
                        >
                          {FRAMINGS.map((f) => (
                            <option key={f} value={f}>
                              {f.replace(/_/g, " ")}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Movimento">
                        <select
                          className={selectClass}
                          value={scene.camera_motion}
                          onChange={(e) => patchScene(scene.scene_number, { camera_motion: e.target.value })}
                        >
                          {MOTIONS.map((m) => (
                            <option key={m} value={m}>
                              {m.replace(/_/g, " ")}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Curva">
                        <select
                          className={selectClass}
                          value={scene.motion_easing || "ease_in_out"}
                          onChange={(e) => patchScene(scene.scene_number, { motion_easing: e.target.value })}
                        >
                          {EASINGS.map((e2) => (
                            <option key={e2} value={e2}>
                              {e2}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Composizione">
                        <select
                          className={selectClass}
                          value={scene.composition || "regola_terzi"}
                          onChange={(e) => patchScene(scene.scene_number, { composition: e.target.value })}
                        >
                          {COMPOSITIONS.map((c) => (
                            <option key={c} value={c}>
                              {c.replace(/_/g, " ")}
                            </option>
                          ))}
                        </select>
                      </Field>
                    </div>

                    <div className="mt-1.5 flex flex-wrap items-center gap-2">
                      <label className="flex items-center gap-1 text-[10px] text-[#6C6459]">
                        Intensità
                        <input
                          type="range"
                          min={0.2}
                          max={2}
                          step={0.05}
                          value={scene.motion_intensity ?? 1}
                          onChange={(e) => patchScene(scene.scene_number, { motion_intensity: Number(e.target.value) })}
                        />
                        <span className="w-8 tabular-nums">{(scene.motion_intensity ?? 1).toFixed(2)}</span>
                      </label>
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(LOCK_LABELS).map(([key, label]) => {
                          const on = Boolean((scene.locks || {})[key]);
                          return (
                            <button
                              key={key}
                              type="button"
                              onClick={() => toggleLock(scene, key)}
                              className={`inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] ${
                                on ? "bg-[#8A5A22]/15 text-[#8A5A22]" : "border border-[#E2D9C8] text-[#8C8377]"
                              }`}
                              title={on ? `Sblocca ${label}` : `Blocca ${label}`}
                            >
                              {on ? <Lock className="h-2.5 w-2.5" /> : <Unlock className="h-2.5 w-2.5" />}
                              {label}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              <div className="rounded-xl border border-[#E2D9C8] bg-white p-2">
                <p className="flex items-center gap-1.5 text-[11px] font-semibold text-[#1D1B18]">
                  <Clapperboard className="h-3.5 w-3.5 text-[#9A8052]" /> Generatore di scene AI
                </p>
                <p className="mt-0.5 text-[10px] text-[#8C8377]">
                  Nessun originale viene sovrascritto. Ogni risultato è marcato «da verificare umano» con la sua
                  provenienza.
                </p>
                <div className="mt-1.5 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
                  <Field label="Modalità">
                    <select className={selectClass} value={genMode} onChange={(e) => { setGenMode(e.target.value); setPreflight(null); }}>
                      {modes.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.id} · {m.label}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Scena">
                    <select className={selectClass} value={String(genScene)} onChange={(e) => setGenScene(Number(e.target.value))}>
                      {editableScenes.map((s) => (
                        <option key={s.scene_number} value={s.scene_number}>
                          {s.scene_number}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Durata (s)">
                    <input
                      type="number"
                      min={3}
                      max={8}
                      value={genDuration}
                      onChange={(e) => setGenDuration(Number(e.target.value))}
                      className={selectClass}
                    />
                  </Field>
                  <Field label="Costo">
                    <span className="pt-1 text-[11px] text-[#6C6459]">
                      {modes.find((m) => m.id === genMode)?.cost || "—"}
                    </span>
                  </Field>
                </div>
                <textarea
                  value={genPrompt}
                  onChange={(e) => setGenPrompt(e.target.value)}
                  rows={2}
                  placeholder="Descrizione della scena o del dettaglio da ottenere, con i vincoli di autenticità KORA"
                  className="mt-1.5 w-full rounded-lg border border-[#E2D9C8] px-2 py-1.5 text-[11px]"
                />
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  <button
                    type="button"
                    onClick={runPreflight}
                    disabled={busy === "preflight"}
                    className="inline-flex items-center gap-1 rounded-full border border-[#224C66] px-3 py-1 text-[11px] text-[#224C66] disabled:opacity-40"
                  >
                    {busy === "preflight" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Sparkles className="h-3 w-3" />}
                    Verifica disponibilità e costo
                  </button>
                  <button
                    type="button"
                    onClick={() => generate(false)}
                    disabled={busy === "generate"}
                    className="inline-flex items-center gap-1 rounded-full bg-[#224C66] px-3 py-1 text-[11px] font-medium text-white disabled:opacity-40"
                  >
                    {busy === "generate" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Wand2 className="h-3 w-3" />}
                    Genera
                  </button>
                  {preflight?.requires_authorization ? (
                    <button
                      type="button"
                      onClick={() => generate(true)}
                      className="rounded-full bg-[#8A5A22] px-3 py-1 text-[11px] font-medium text-white"
                    >
                      Autorizzo la spesa
                    </button>
                  ) : null}
                </div>
                {preflight ? (
                  <ul className="mt-1.5 space-y-0.5">
                    {preflight.checks.map((c) => (
                      <li key={c.check} className="text-[10px] text-[#6C6459]">
                        <strong className={c.ok ? "text-[#2F6B4F]" : "text-[#8A3A2A]"}>
                          {c.ok ? "ok" : "no"}
                        </strong>{" "}
                        · {c.check}: {c.detail}
                      </li>
                    ))}
                    {preflight.declared_limit ? (
                      <li className="text-[10px] text-[#8A5A22]">{preflight.declared_limit}</li>
                    ) : null}
                  </ul>
                ) : null}

                {history.length ? (
                  <div className="mt-2 space-y-1">
                    <p className="text-[10px] uppercase tracking-wide text-[#8C8377]">Generazioni di questo progetto</p>
                    {history
                      .slice()
                      .reverse()
                      .slice(0, 4)
                      .map((g) => (
                        <div key={g.id} className="flex flex-wrap items-center gap-1 rounded-lg bg-[#FAF7F2] px-2 py-1 text-[10px]">
                          <span className="font-medium text-[#1D1B18]">
                            {g.mode} · scena {g.scene_number}
                          </span>
                          <span className="text-[#8C8377]">
                            {g.engine} · {g.authenticity} · {g.status}
                          </span>
                          <span className="ml-auto flex gap-1">
                            <button
                              type="button"
                              onClick={() =>
                                run(`use-${g.id}`, async () => {
                                  await koraApi.generatorUse(projectId, g.id);
                                  setMsg({ kind: "ok", text: "Materiale inserito nella scena. Ripristinabile con ↺." });
                                  await load();
                                  onChanged?.();
                                })
                              }
                              className="rounded bg-[#2F6B4F]/10 px-1.5 py-0.5 text-[#2F6B4F]"
                            >
                              usa
                            </button>
                            <button
                              type="button"
                              onClick={() =>
                                run(`save-${g.id}`, async () => {
                                  const res = await koraApi.generatorSave(projectId, g.id);
                                  setMsg({ kind: "ok", text: `Copia salvata in ${res.saved_path}` });
                                })
                              }
                              className="rounded bg-[#EFE9DE] px-1.5 py-0.5 text-[#6C6459]"
                            >
                              salva
                            </button>
                            <button
                              type="button"
                              onClick={() =>
                                run(`discard-${g.id}`, async () => {
                                  await koraApi.generatorDiscard(projectId, g.id);
                                  setHistory((prev) => prev.filter((x) => x.id !== g.id));
                                })
                              }
                              className="rounded bg-[#8A3A2A]/10 px-1.5 py-0.5 text-[#8A3A2A]"
                            >
                              scarta
                            </button>
                          </span>
                        </div>
                      ))}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
