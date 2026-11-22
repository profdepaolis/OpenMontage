"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Cpu,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import { koraApi } from "@/lib/api";

type Engine = {
  id: string;
  label: string;
  kind: string;
  installed: boolean;
  configured: boolean;
  available: boolean;
  test_state: "superato" | "fallito" | "non eseguito";
  test_detail: string;
  missing: string[];
  requirements: string;
  details: Record<string, unknown>;
};

type Summary = {
  total: number;
  installed: number;
  available: number;
  with_problems: string[];
  render_ready: boolean;
};

const KIND_LABEL: Record<string, string> = {
  render: "Rendering",
  composition: "Composizione",
  asr: "Trascrizione",
  tts: "Voce (TTS)",
};

function StateBadge({ engine }: { engine: Engine }) {
  if (engine.available) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-800">
        <CheckCircle2 className="h-3 w-3" /> Disponibile
      </span>
    );
  }
  if (engine.installed) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-800">
        <AlertTriangle className="h-3 w-3" /> Installato, non utilizzabile
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-[#EFE8DB] px-2 py-0.5 text-[10px] font-medium text-[#6C6459]">
      <CircleSlash className="h-3 w-3" /> Non installato
    </span>
  );
}

/**
 * Inventario reale dei motori installati sulla macchina.
 * Un nome nel codice non è una disponibilità: qui contano solo le verifiche locali.
 */
export function EnginesInventory({ compact = false }: { compact?: boolean }) {
  const [engines, setEngines] = useState<Engine[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [testing, setTesting] = useState<string>("");
  const [results, setResults] = useState<Record<string, { ok: boolean; detail: string; ms: number }>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    try {
      const res = await koraApi.mediaEngines(refresh);
      setEngines(res.engines || []);
      setSummary(res.summary || null);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const runTest = async (id: string) => {
    setTesting(id);
    try {
      const res = await koraApi.mediaEngineTest(id);
      setResults((prev) => ({ ...prev, [id]: { ok: res.ok, detail: res.detail, ms: res.latency_ms } }));
    } catch (err) {
      setResults((prev) => ({
        ...prev,
        [id]: { ok: false, detail: `Test non eseguito: ${(err as Error).message}`, ms: 0 },
      }));
    } finally {
      setTesting("");
    }
  };

  const shown = compact ? engines.filter((e) => e.available || e.installed) : engines;

  return (
    <section className="rounded-2xl border border-[#EDE6D9] bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-[#224C66]" />
            <h3 className="text-sm font-semibold text-[#1D1B18]">Motori sulla macchina</h3>
          </div>
          <p className="mt-0.5 text-[11px] text-[#6C6459]">
            Verifica locale, motore per motore. I test sono facoltativi e non installano nulla.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load(true)}
          disabled={loading || refreshing}
          className="inline-flex items-center gap-1.5 rounded-lg border border-[#DDD4C4] px-2.5 py-1 text-[11px] text-[#4A443C] disabled:opacity-50"
        >
          <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} /> Riverifica
        </button>
      </div>

      {summary && (
        <div className="mt-3 flex flex-wrap gap-1.5 text-[10px]">
          <span className="rounded-full bg-[#EFE8DB] px-2 py-0.5 text-[#4A443C]">
            {summary.total} motori censiti
          </span>
          <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-800">
            {summary.available} disponibili
          </span>
          <span className="rounded-full bg-[#FAF7F2] px-2 py-0.5 text-[#6C6459]">
            {summary.installed} installati
          </span>
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 ${
              summary.render_ready ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-800"
            }`}
          >
            <ShieldCheck className="h-3 w-3" />
            {summary.render_ready ? "Render FFmpeg pronto" : "Render non pronto"}
          </span>
        </div>
      )}

      {error && (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-900">
          Inventario non leggibile: {error}
        </p>
      )}

      {loading ? (
        <div className="flex items-center gap-2 py-6 text-[11px] text-[#6C6459]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Verifico i motori…
        </div>
      ) : (
        <div className="mt-3 space-y-1.5">
          {shown.map((engine) => {
            const tested = results[engine.id];
            return (
              <div
                key={engine.id}
                className="rounded-xl border border-[#EDE6D9] bg-[#FAF7F2]/60 px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium text-[#1D1B18]">{engine.label}</span>
                  <span className="text-[10px] uppercase tracking-wider text-[#9A8052]">
                    {KIND_LABEL[engine.kind] || engine.kind}
                  </span>
                  <StateBadge engine={engine} />
                  <span className="ml-auto text-[10px] text-[#6C6459]">
                    {engine.test_state === "superato"
                      ? `test superato · ${engine.test_detail}`
                      : engine.test_state === "fallito"
                        ? `test fallito · ${engine.test_detail}`
                        : "test non eseguito"}
                  </span>
                  {engine.id !== "ffprobe" && (
                    <button
                      type="button"
                      onClick={() => void runTest(engine.id)}
                      disabled={testing === engine.id}
                      className="inline-flex items-center gap-1 rounded-lg border border-[#DDD4C4] bg-white px-2 py-0.5 text-[10px] text-[#4A443C] disabled:opacity-50"
                    >
                      {testing === engine.id ? (
                        <Loader2 className="h-2.5 w-2.5 animate-spin" />
                      ) : (
                        <Terminal className="h-2.5 w-2.5" />
                      )}
                      Verifica ora
                    </button>
                  )}
                </div>
                <p className="mt-0.5 text-[10px] leading-snug text-[#6C6459]">
                  {engine.available
                    ? engine.test_detail || "Pronto all'uso."
                    : engine.missing.length
                      ? `Manca: ${engine.missing.join(", ")}`
                      : engine.test_detail || "Non disponibile su questa macchina."}
                  {engine.requirements ? ` · Requisiti: ${engine.requirements}` : ""}
                </p>
                {tested && (
                  <p
                    className={`mt-1 rounded-lg px-2 py-1 text-[10px] ${
                      tested.ok ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-800"
                    }`}
                  >
                    {tested.ok ? "Test superato" : "Test fallito"} ({tested.ms} ms): {tested.detail}
                  </p>
                )}
              </div>
            );
          })}
          {!shown.length && (
            <p className="py-4 text-center text-[11px] text-[#6C6459]">Nessun motore installato.</p>
          )}
        </div>
      )}
      <p className="mt-2 text-[10px] text-[#9A8052]">
        HyperFrames interno (preset locali) e servizio HyperFrames esterno sono voci distinte: i preset locali non
        dimostrano un servizio collegato. Remotion risulta disponibile solo se il runtime Composer è davvero eseguibile.
      </p>
    </section>
  );
}

export default EnginesInventory;
