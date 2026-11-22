"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Gauge,
  RefreshCw,
  Search,
  Star,
  Timer,
  XCircle,
} from "lucide-react";
import { koraApi } from "@/lib/api";
import type { ModelManagerConfig, ModelTestRecord } from "@/lib/api";

type Role = "everyday" | "premium" | "fallback";
type TestKind = "quick" | "creative";

const ROLE_LABEL: Record<Role, string> = {
  everyday: "Principale (Everyday)",
  premium: "Cinematic Premium",
  fallback: "Alternativo",
};

const MODE_LABEL: Record<string, string> = {
  ask: "Chiedi prima di cambiare motore",
  fallback: "Passa da solo al modello alternativo",
  local: "Usa il fallback locale",
  stop: "Fermati e mostrami l'errore",
};

function stateBadge(record?: ModelTestRecord | null) {
  if (!record) {
    return (
      <span className="rounded-full border border-[#DDD4C4] px-2 py-0.5 text-[10px] text-[#8A8276]">
        non testato
      </span>
    );
  }
  if (record.ok) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-[#E8F0E5] px-2 py-0.5 text-[10px] text-[#3F5D38]">
        <CheckCircle2 size={11} /> verificato · {Math.round(record.latency_ms || 0)} ms
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-[#FBEAE7] px-2 py-0.5 text-[10px] text-[#8C3B2C]">
      <XCircle size={11} /> {record.error_kind || "non superato"}
    </span>
  );
}

export function CreativeEngineModels() {
  const [config, setConfig] = useState<ModelManagerConfig | null>(null);
  const [models, setModels] = useState<Array<{ id: string; provider: string; last_test: ModelTestRecord | null }>>([]);
  const [providers, setProviders] = useState<string[]>([]);
  const [meta, setMeta] = useState<{ total: number; cached: boolean; error: string; fetched_at_iso: string | null; cli_version: string | null }>({
    total: 0,
    cached: false,
    error: "",
    fetched_at_iso: null,
    cli_version: null,
  });
  const [query, setQuery] = useState("");
  const [providerFilter, setProviderFilter] = useState("");
  const [busy, setBusy] = useState<string>("");
  const [message, setMessage] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const [benchmark, setBenchmark] = useState<{ kind: string; at: string; results: ModelTestRecord[] } | null>(null);
  const [draftTimeouts, setDraftTimeouts] = useState<Record<string, string>>({});

  const load = useCallback(async (q = "", prov = "") => {
    try {
      const [cfg, found] = await Promise.all([koraApi.modelConfig(), koraApi.searchModels(q, prov)]);
      setConfig(cfg);
      setDraftTimeouts({
        ideation: String(cfg.timeouts.ideation),
        design: String(cfg.timeouts.design),
        revision: String(cfg.timeouts.revision),
        premium: String(cfg.timeouts.premium),
      });
      setModels(found.models);
      setProviders(found.providers);
      setMeta({
        total: found.total,
        cached: Boolean(found.cached),
        error: found.error,
        fetched_at_iso: found.fetched_at_iso,
        cli_version: found.cli_version,
      });
    } catch (err) {
      setMeta((m) => ({ ...m, error: String(err) }));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = async () => {
    setBusy("refresh");
    setMessage(null);
    try {
      await koraApi.refreshModels();
      await load(query, providerFilter);
      setMessage({ tone: "ok", text: "Elenco modelli aggiornato dal CLI." });
    } catch (err) {
      setMessage({ tone: "warn", text: String(err) });
    } finally {
      setBusy("");
    }
  };

  const saveConfig = async (patch: Record<string, unknown>, note: string) => {
    setBusy("config");
    try {
      const next = await koraApi.updateModelConfig(patch);
      setConfig(next);
      setMessage({ tone: "ok", text: note });
    } catch (err) {
      setMessage({ tone: "warn", text: String(err) });
    } finally {
      setBusy("");
    }
  };

  const chooseModel = async (role: Role, model: string) => {
    await saveConfig({ roles: { [role]: model } }, `${ROLE_LABEL[role]} impostato su ${model}.`);
  };

  const testOne = async (model: string, kind: TestKind) => {
    setBusy(`${kind}:${model}`);
    setMessage(null);
    try {
      const record = await koraApi.testModel(model, kind);
      await load(query, providerFilter);
      setMessage({
        tone: record.ok ? "ok" : "warn",
        text: record.ok
          ? `${model}: risposta reale in ${Math.round(record.latency_ms || 0)} ms (${kind === "quick" ? "test rapido" : "test creativo"}).`
          : `${model}: ${record.error || record.notes || "non superato"}`,
      });
    } catch (err) {
      setMessage({ tone: "warn", text: String(err) });
    } finally {
      setBusy("");
    }
  };

  const runBenchmark = async (kind: TestKind) => {
    setBusy(`bench:${kind}`);
    setMessage(null);
    try {
      const result = await koraApi.runBenchmark(kind);
      setBenchmark({ kind: result.kind, at: result.at, results: result.results });
      setMessage({
        tone: result.passed === result.results.length ? "ok" : "warn",
        text: `Benchmark ${kind}: ${result.passed}/${result.results.length} modelli superano il test.`,
      });
      await load(query, providerFilter);
    } catch (err) {
      setMessage({ tone: "warn", text: String(err) });
    } finally {
      setBusy("");
    }
  };

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return models.filter(
      (m) => (!q || m.id.toLowerCase().includes(q)) && (!providerFilter || m.provider === providerFilter)
    );
  }, [models, query, providerFilter]);

  const selectedIds = config ? Object.values(config.roles) : [];

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-[#DDD4C4] bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-[#2C2822]">
              <Cpu size={16} /> Motore creativo · modelli OpenCode
            </div>
            <p className="mt-1 max-w-xl text-xs leading-relaxed text-[#6B6358]">
              La presenza nell&apos;elenco non dimostra che un modello risponda. Un modello è «verificato»
              solo dopo un test reale, con data e latenza misurate.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={refresh}
              disabled={busy === "refresh"}
              className="inline-flex items-center gap-1.5 rounded-xl border border-[#DDD4C4] px-3 py-2 text-xs text-[#4A443C] hover:border-[#9A8052] disabled:opacity-50"
            >
              <RefreshCw size={13} className={busy === "refresh" ? "animate-spin" : ""} />
              Aggiorna elenco
            </button>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-[#6B6358]">
          <span>CLI: {meta.cli_version || "versione non rilevata"}</span>
          <span>Modelli: {meta.total}</span>
          <span>{meta.cached ? "elenco in cache" : "elenco appena letto"}</span>
          {meta.fetched_at_iso && <span>letto il {new Date(meta.fetched_at_iso).toLocaleString("it-IT")}</span>}
        </div>
        {(meta.error || (!meta.total && !busy)) && (
          <div className="mt-3 flex items-start gap-2 rounded-xl bg-[#FBF3E8] px-3 py-2 text-[11px] text-[#7A5A2A]">
            <AlertTriangle size={13} className="mt-0.5" />
            <span>
              {meta.error || "Nessun modello disponibile: OpenCode non è installato o non è autenticato."}
            </span>
          </div>
        )}
        {message && (
          <div
            className={`mt-3 rounded-xl px-3 py-2 text-[11px] ${
              message.tone === "ok" ? "bg-[#E8F0E5] text-[#3F5D38]" : "bg-[#FBEAE7] text-[#8C3B2C]"
            }`}
          >
            {message.text}
          </div>
        )}
      </div>

      {config && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-2xl border border-[#DDD4C4] bg-white p-4">
            <div className="text-sm font-semibold text-[#2C2822]">Ruoli dei modelli</div>
            <p className="mt-1 text-[11px] text-[#6B6358]">
              Un modello per richiesta. La modalità Everyday/Premium decide quale usare.
            </p>
            <div className="mt-3 space-y-3">
              {(Object.keys(ROLE_LABEL) as Role[]).map((role) => (
                <label key={role} className="block text-[11px] text-[#4A443C]">
                  <span className="font-medium">{ROLE_LABEL[role]}</span>
                  <select
                    value={config.roles[role]}
                    onChange={(event) => void chooseModel(role, event.target.value)}
                    disabled={busy === "config"}
                    className="mt-1 w-full rounded-xl border border-[#DDD4C4] bg-[#FDFBF7] px-3 py-2 text-xs text-[#2C2822]"
                  >
                    {!selectedIds.includes(config.roles[role]) && (
                      <option value={config.roles[role]}>{config.roles[role]} · non presente nell&apos;elenco</option>
                    )}
                    {filtered.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.id}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              {(["everyday", "premium"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => void saveConfig({ mode }, `Modalità ${mode === "premium" ? "Cinematic Premium" : "Everyday"} attiva.`)}
                  className={`rounded-xl px-3 py-2 text-[11px] ${
                    config.mode === mode
                      ? "bg-[#1D1B18] text-[#FAF7F2]"
                      : "border border-[#DDD4C4] text-[#4A443C] hover:border-[#9A8052]"
                  }`}
                >
                  {mode === "premium" ? "Cinematic Premium" : "Everyday"}
                </button>
              ))}
            </div>

            <label className="mt-4 block text-[11px] text-[#4A443C]">
              <span className="font-medium">Se il modello non risponde</span>
              <select
                value={config.policy}
                onChange={(event) => void saveConfig({ policy: event.target.value }, "Politica di fallback aggiornata.")}
                className="mt-1 w-full rounded-xl border border-[#DDD4C4] bg-[#FDFBF7] px-3 py-2 text-xs"
              >
                {Object.entries(config.policies).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-[10px] text-[#8A8276]">
                Il fallback non viene mai presentato come risposta dell&apos;IA: la UI lo dichiara.
              </span>
            </label>
          </div>

          <div className="rounded-2xl border border-[#DDD4C4] bg-white p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-[#2C2822]">
              <Timer size={15} /> Tempi massimi (modificabili)
            </div>
            <p className="mt-1 text-[11px] text-[#6B6358]">
              Il timeout copre anche preparazione e avvio del CLI, non solo l&apos;inferenza.
            </p>
            <div className="mt-3 grid grid-cols-2 gap-3">
              {(
                [
                  ["ideation", "4 proposte"],
                  ["design", "Reel completo"],
                  ["revision", "Revisione mirata"],
                  ["premium", "Modello Premium"],
                ] as Array<[string, string]>
              ).map(([key, label]) => (
                <label key={key} className="text-[11px] text-[#4A443C]">
                  <span className="font-medium">{label}</span>
                  <div className="mt-1 flex items-center gap-1">
                    <input
                      type="number"
                      min={10}
                      max={600}
                      value={draftTimeouts[key] ?? ""}
                      onChange={(event) => setDraftTimeouts((d) => ({ ...d, [key]: event.target.value }))}
                      className="w-full rounded-xl border border-[#DDD4C4] bg-[#FDFBF7] px-3 py-2 text-xs"
                    />
                    <span className="text-[10px] text-[#8A8276]">s</span>
                  </div>
                </label>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-2">
              <button
                type="button"
                disabled={busy === "config"}
                onClick={() =>
                  void saveConfig(
                    { timeouts: Object.fromEntries(Object.entries(draftTimeouts).map(([k, v]) => [k, Number(v)])) },
                    "Tempi massimi salvati."
                  )
                }
                className="rounded-xl bg-[#1D1B18] px-3 py-2 text-[11px] text-[#FAF7F2] disabled:opacity-50"
              >
                Salva tempi
              </button>
              <button
                type="button"
                onClick={() =>
                  setDraftTimeouts(
                    Object.fromEntries(
                      Object.entries(config.defaults.timeouts).map(([k, v]) => [k, String(v)])
                    )
                  )
                }
                className="rounded-xl border border-[#DDD4C4] px-3 py-2 text-[11px] text-[#4A443C]"
              >
                Ripristina consigliati
              </button>
            </div>

            <div className="mt-5 rounded-xl bg-[#F7F3EC] p-3">
              <div className="flex items-center gap-2 text-[11px] font-medium text-[#4A443C]">
                <Gauge size={13} /> Benchmark (solo su richiesta)
              </div>
              <p className="mt-1 text-[10px] text-[#8A8276]">
                Rapido: «Rispondi soltanto con OK». Creativo: 4 temi KORA in JSON. Nessuna esecuzione periodica.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void runBenchmark("quick")}
                  disabled={Boolean(busy)}
                  className="rounded-xl border border-[#DDD4C4] bg-white px-3 py-1.5 text-[11px] text-[#4A443C] disabled:opacity-50"
                >
                  {busy === "bench:quick" ? "Test in corso…" : "Benchmark rapido"}
                </button>
                <button
                  type="button"
                  onClick={() => void runBenchmark("creative")}
                  disabled={Boolean(busy)}
                  className="rounded-xl border border-[#DDD4C4] bg-white px-3 py-1.5 text-[11px] text-[#4A443C] disabled:opacity-50"
                >
                  {busy === "bench:creative" ? "Test in corso…" : "Benchmark creativo"}
                </button>
              </div>
              {benchmark && (
                <ul className="mt-2 space-y-1 text-[10px] text-[#4A443C]">
                  {benchmark.results.map((r) => (
                    <li key={`${benchmark.at}-${r.model}-${r.kind}`} className="flex items-center justify-between gap-2">
                      <span className="truncate">{r.model}</span>
                      <span>{stateBadge(r)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="rounded-2xl border border-[#DDD4C4] bg-white p-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-1 items-center gap-2 rounded-xl border border-[#DDD4C4] bg-[#FDFBF7] px-3 py-2">
            <Search size={13} className="text-[#8A8276]" />
            <input
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                void load(event.target.value, providerFilter);
              }}
              placeholder="Cerca un modello (es. flash, mimo, nemotron)"
              className="w-full bg-transparent text-xs outline-none"
            />
          </div>
          <select
            value={providerFilter}
            onChange={(event) => {
              setProviderFilter(event.target.value);
              void load(query, event.target.value);
            }}
            className="rounded-xl border border-[#DDD4C4] bg-[#FDFBF7] px-3 py-2 text-xs"
          >
            <option value="">Tutti i provider</option>
            {providers.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>

        <div className="mt-3 divide-y divide-[#EFE8DB]">
          {filtered.map((m) => (
            <div key={m.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-xs text-[#2C2822]">
                  {selectedIds.includes(m.id) && <Star size={11} className="text-[#9A8052]" />}
                  <span className="truncate">{m.id}</span>
                </div>
                <div className="mt-0.5">{stateBadge(m.last_test)}</div>
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => void testOne(m.id, "quick")}
                  disabled={Boolean(busy)}
                  className="rounded-lg border border-[#DDD4C4] px-2 py-1 text-[10px] text-[#4A443C] disabled:opacity-50"
                >
                  {busy === `quick:${m.id}` ? "…" : "Test rapido"}
                </button>
                <button
                  type="button"
                  onClick={() => void testOne(m.id, "creative")}
                  disabled={Boolean(busy)}
                  className="rounded-lg border border-[#DDD4C4] px-2 py-1 text-[10px] text-[#4A443C] disabled:opacity-50"
                >
                  {busy === `creative:${m.id}` ? "…" : "Test creativo"}
                </button>
              </div>
            </div>
          ))}
          {!filtered.length && (
            <p className="py-4 text-center text-[11px] text-[#8A8276]">
              Nessun modello da mostrare. Aggiorna l&apos;elenco o togli i filtri.
            </p>
          )}
        </div>

        <p className="mt-3 text-[10px] leading-relaxed text-[#8A8276]">
          Il suffisso «-free» non è una verifica di gratuità: il costo resta «non verificabile» finché il
          provider non lo dichiara. Il badge verde significa risposta reale e latenza misurata, non «gratis».
        </p>
      </div>
    </div>
  );
}
