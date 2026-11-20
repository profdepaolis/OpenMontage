"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Cpu,
  Eye,
  KeyRound,
  Loader2,
  RefreshCw,
  Shield,
  Sparkles,
  Terminal,
  Wallet,
  Wifi,
  WifiOff,
} from "lucide-react";
import type {
  AIEngineAgent,
  AIEngineModel,
  AIEngineProvider,
  AIEngineStatus,
  AIEngineStrategy,
} from "@/lib/types";
import { koraApi } from "@/lib/api";

const STRATEGY_LABELS: Record<AIEngineStrategy, string> = {
  auto: "Automatica (abbonamenti e quote gratuite, poi costo minimo)",
  max_quality: "Massima qualità",
  min_cost: "Minimo costo aggiuntivo",
  local_only: "Solo locale (nessun servizio esterno)",
};

const STATE_STYLE: Record<string, { label: string; className: string }> = {
  operativo: { label: "Operativo", className: "bg-emerald-100 text-emerald-800" },
  autenticato: { label: "Autenticato", className: "bg-sky-100 text-sky-800" },
  installato: { label: "Installato", className: "bg-amber-100 text-amber-800" },
  indisponibile: { label: "Indisponibile", className: "bg-stone-200 text-stone-600" },
  errore: { label: "Errore", className: "bg-rose-100 text-rose-800" },
};

export function AIEngineView() {
  const [status, setStatus] = useState<AIEngineStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [models, setModels] = useState<Record<string, AIEngineModel[]>>({});
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [baseUrlInput, setBaseUrlInput] = useState("");
  const [strategy, setStrategy] = useState<AIEngineStrategy>("auto");
  const [primaryProvider, setPrimaryProvider] = useState("auto");
  const [budget, setBudget] = useState("0");
  const [allowPaid, setAllowPaid] = useState(false);

  const applyStatus = useCallback((next: AIEngineStatus) => {
    setStatus(next);
    setStrategy(next.settings.strategy);
    setPrimaryProvider(next.settings.primary_provider);
    setBudget(String(next.settings.max_budget_eur));
    setAllowPaid(next.settings.allow_paid_calls);
    setBaseUrlInput(next.settings.providers?.custom_openai?.base_url || "");
  }, []);

  const load = useCallback(
    async (refresh = false) => {
      setLoading(true);
      setError(null);
      try {
        applyStatus(await koraApi.engineStatus(refresh));
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [applyStatus]
  );

  useEffect(() => {
    load(false);
  }, [load]);

  const loadModels = async (providerId: string) => {
    try {
      const list = await koraApi.engineModels(providerId);
      setModels((prev) => ({ ...prev, [providerId]: list }));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const saveSettings = async (patch: Parameters<typeof koraApi.engineUpdateSettings>[0], note: string) => {
    setBusy("settings");
    setMsg(null);
    setError(null);
    try {
      const next = await koraApi.engineUpdateSettings(patch);
      applyStatus(next.status);
      setMsg(note);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const verify = async (providerId: string) => {
    setBusy(`probe:${providerId}`);
    setMsg(null);
    try {
      const probe = await koraApi.engineVerify(providerId);
      setStatus((prev) =>
        prev
          ? { ...prev, providers: prev.providers.map((p) => (p.provider === providerId ? probe : p)) }
          : prev
      );
      setMsg(`${probe.label}: stato «${STATE_STYLE[probe.state]?.label || probe.state}». ${probe.detail || ""}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const runTest = async (providerId: string, multimodal: boolean) => {
    setBusy(`test:${providerId}:${multimodal ? "mm" : "text"}`);
    setMsg(null);
    setError(null);
    try {
      const res = await koraApi.engineTest({ provider: providerId, multimodal });
      if (res.ok) {
        setMsg(
          `${res.provider} · ${res.model || "modello auto"} · ${res.latency_ms ?? 0} ms · ${
            res.multimodal ? "test multimodale (immagine reale del Vault)" : "test testuale"
          } → “${res.preview || ""}”`
        );
      } else {
        setError(`${res.provider}: ${res.detail || "test non riuscito"}`);
      }
      await load(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const saveKey = async (providerId: string) => {
    const value = (keyInputs[providerId] || "").trim();
    if (!value) return;
    setBusy(`key:${providerId}`);
    setMsg(null);
    try {
      const probe = await koraApi.engineSetKey(providerId, value);
      setKeyInputs((prev) => ({ ...prev, [providerId]: "" }));
      setMsg(
        `Chiave salvata cifrata nel backend per ${probe.label}. Stato: «${
          STATE_STYLE[probe.state]?.label || probe.state
        }». ${probe.detail || ""}`
      );
      await load(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const updateAgent = async (agent: AIEngineAgent, patch: Partial<AIEngineAgent>) => {
    const merged = { ...agent, ...patch };
    setBusy(`agent:${agent.id}`);
    setMsg(null);
    try {
      const res = await koraApi.engineSetAgent(agent.id, {
        mode: merged.mode,
        provider: merged.mode === "provider" ? merged.provider || undefined : undefined,
        model: merged.mode === "provider" ? merged.model || undefined : undefined,
      });
      setStatus((prev) =>
        prev
          ? {
              ...prev,
              agents: prev.agents.map((a) => (a.id === agent.id ? res.agent : a)),
            }
          : prev
      );
      setMsg(`Agente «${agent.label}» aggiornato senza riavviare KORA.`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  if (loading && !status) {
    return (
      <div className="flex items-center gap-2 text-sm text-[#6C6459]">
        <Loader2 className="h-4 w-4 animate-spin" /> Rilevamento dei motori AI in corso…
      </div>
    );
  }

  if (!status) {
    return (
      <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
        Impossibile leggere lo stato dei motori AI: {error}
      </div>
    );
  }

  const configuredProviders = status.providers;
  const primaryOptions = [
    { id: "auto", label: "Automatico (segue la strategia scelta)" },
    ...configuredProviders.map((p) => ({
      id: p.provider,
      label: `${p.label} — ${STATE_STYLE[p.state]?.label || p.state}`,
    })),
  ];

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-[#1D1B18]">AI Engine Manager</h2>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-[#6C6459]">
            Scegli quale motore usa KORA (OpenCode, Codex, Ollama e API compatibili). Lo stato è
            sempre verificato realmente: nessun provider viene mostrato come attivo solo perché
            appare nell&apos;elenco.
          </p>
        </div>
        <button
          type="button"
          onClick={() => load(true)}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-3 py-2 text-xs font-medium text-[#1D1B18] hover:bg-[#EFE8DB] disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} /> Rileva di nuovo
        </button>
      </header>

      {msg && (
        <div className="flex items-start gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> <span>{msg}</span>
        </div>
      )}
      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> <span>{error}</span>
        </div>
      )}

      {/* Router principale */}
      <section className="rounded-2xl border border-[#EDE6D9] bg-white p-4">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-[#1D1B18]">
          <Sparkles className="h-4 w-4 text-[#9A8052]" /> Motore principale e strategia
        </h3>
        <div className="mt-3 grid gap-4 lg:grid-cols-2">
          <label className="block text-xs text-[#6C6459]">
            Provider principale
            <select
              value={primaryProvider}
              onChange={(e) => setPrimaryProvider(e.target.value)}
              className="mt-1 w-full rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-3 py-2 text-xs text-[#1D1B18]"
            >
              {primaryOptions.map((opt) => (
                <option key={opt.id} value={opt.id}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs text-[#6C6459]">
            Strategia di routing
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value as AIEngineStrategy)}
              className="mt-1 w-full rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-3 py-2 text-xs text-[#1D1B18]"
            >
              {status.strategies.map((s) => (
                <option key={s} value={s}>
                  {STRATEGY_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs text-[#6C6459]">
            Budget massimo mensile (€) — tetto rigido 20 €
            <input
              type="number"
              min={0}
              max={20}
              step="0.5"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              className="mt-1 w-full rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-3 py-2 text-xs text-[#1D1B18]"
            />
          </label>
          <label className="flex items-start gap-2 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] p-3 text-xs text-[#4A443C]">
            <input
              type="checkbox"
              checked={allowPaid}
              onChange={(e) => setAllowPaid(e.target.checked)}
              className="mt-0.5 h-4 w-4"
            />
            <span>
              Autorizzo chiamate a pagamento entro il budget indicato.
              <span className="mt-1 block text-[11px] text-[#9A8052]">
                Con 0 € KORA usa solo abbonamenti, quote gratuite e motori locali. Saldo credito:{" "}
                <strong>Non verificabile</strong> (nessun dato inventato).
              </span>
            </span>
          </label>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy === "settings"}
            onClick={() =>
              saveSettings(
                {
                  primary_provider: primaryProvider,
                  strategy,
                  max_budget_eur: Number(budget || 0),
                  allow_paid_calls: allowPaid,
                },
                "Impostazioni del motore salvate. Nessun riavvio necessario."
              )
            }
            className="inline-flex items-center gap-2 rounded-xl bg-[#1D1B18] px-3.5 py-2 text-xs font-medium text-[#FAF7F2] disabled:opacity-50"
          >
            {busy === "settings" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            Salva motore principale
          </button>
          <span className="text-[11px] text-[#9A8052]">
            Le modifiche valgono subito, anche a metà lavorazione.
          </span>
        </div>
      </section>

      {/* Provider */}
      <section className="space-y-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-[#1D1B18]">
          <Cpu className="h-4 w-4 text-[#9A8052]" /> Motori disponibili
        </h3>
        <div className="grid gap-3 xl:grid-cols-2">
          {configuredProviders.map((p) => {
            const badge = STATE_STYLE[p.state] || STATE_STYLE.indisponibile;
            const isCustom = p.provider === "custom_openai";
            const needsKey = p.requires_key && p.kind === "http";
            return (
              <article key={p.provider} className="rounded-2xl border border-[#EDE6D9] bg-white p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-2">
                      {p.kind === "cli" ? (
                        <Terminal className="h-4 w-4 text-[#4A443C]" />
                      ) : p.local ? (
                        <WifiOff className="h-4 w-4 text-[#4A443C]" />
                      ) : (
                        <Wifi className="h-4 w-4 text-[#4A443C]" />
                      )}
                      <span className="text-sm font-semibold text-[#1D1B18]">{p.label}</span>
                    </div>
                    <div className="mt-1 text-[11px] text-[#6C6459]">
                      {p.kind === "cli" ? "CLI locale" : p.local ? "Endpoint locale" : "API remota"}
                      {p.subscription ? " · usa un abbonamento già attivo" : ""}
                      {p.version ? ` · v${p.version}` : ""}
                      {p.models_count ? ` · ${p.models_count} modelli rilevati` : ""}
                    </div>
                  </div>
                  <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${badge.className}`}>
                    {badge.label}
                  </span>
                </div>

                <p className="mt-2 text-[11px] leading-relaxed text-[#6C6459]">
                  {p.state === "indisponibile" && !p.installed ? <CircleSlash className="mr-1 inline h-3 w-3" /> : null}
                  {p.detail || p.auth_detail || "Stato non ancora verificato."}
                  {p.account ? ` · account: ${p.account}` : ""}
                </p>
                <div className="mt-1 flex flex-wrap gap-2 text-[10px] text-[#9A8052]">
                  <span>Installato: {p.installed ? "sì" : "no"}</span>
                  <span>Autenticato: {p.authenticated ? "sì" : "no"}</span>
                  <span>Operativo verificato: {p.verified_operational ? "sì" : "no"}</span>
                  <span>Visione: {p.supports_vision ? "sì" : "no"}</span>
                </div>

                {isCustom && (
                  <div className="mt-3 flex gap-2">
                    <input
                      value={baseUrlInput}
                      onChange={(e) => setBaseUrlInput(e.target.value)}
                      placeholder="http://127.0.0.1:1234/v1"
                      className="flex-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-3 py-2 text-[11px]"
                    />
                    <button
                      type="button"
                      onClick={() =>
                        saveSettings(
                          { providers: { custom_openai: { base_url: baseUrlInput.trim() } } },
                          "Indirizzo dell'endpoint personalizzato salvato."
                        )
                      }
                      className="rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-3 py-2 text-[11px] font-medium text-[#1D1B18]"
                    >
                      Salva indirizzo
                    </button>
                  </div>
                )}

                {needsKey && (
                  <div className="mt-3 flex gap-2">
                    <input
                      type="password"
                      value={keyInputs[p.provider] || ""}
                      onChange={(e) => setKeyInputs((prev) => ({ ...prev, [p.provider]: e.target.value }))}
                      placeholder="Incolla qui la API key (resta solo nel backend)"
                      className="flex-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-3 py-2 text-[11px]"
                    />
                    <button
                      type="button"
                      onClick={() => saveKey(p.provider)}
                      disabled={busy === `key:${p.provider}`}
                      className="inline-flex items-center gap-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-3 py-2 text-[11px] font-medium text-[#1D1B18] disabled:opacity-50"
                    >
                      <KeyRound className="h-3 w-3" /> Salva
                    </button>
                  </div>
                )}

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => verify(p.provider)}
                    disabled={busy === `probe:${p.provider}`}
                    className="inline-flex items-center gap-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-2.5 py-1.5 text-[11px] font-medium text-[#1D1B18] disabled:opacity-50"
                  >
                    {busy === `probe:${p.provider}` ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <RefreshCw className="h-3 w-3" />
                    )}
                    Verifica
                  </button>
                  <button
                    type="button"
                    onClick={() => runTest(p.provider, false)}
                    disabled={busy === `test:${p.provider}:text`}
                    className="inline-flex items-center gap-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-2.5 py-1.5 text-[11px] font-medium text-[#1D1B18] disabled:opacity-50"
                  >
                    Test testo
                  </button>
                  <button
                    type="button"
                    onClick={() => runTest(p.provider, true)}
                    disabled={busy === `test:${p.provider}:mm` || !p.supports_vision}
                    title={p.supports_vision ? "" : "Nessun modello con visione rilevato su questo motore"}
                    className="inline-flex items-center gap-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-2.5 py-1.5 text-[11px] font-medium text-[#1D1B18] disabled:opacity-40"
                  >
                    <Eye className="h-3 w-3" /> Test multimodale
                  </button>
                  <button
                    type="button"
                    onClick={() => loadModels(p.provider)}
                    className="rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-2.5 py-1.5 text-[11px] font-medium text-[#1D1B18]"
                  >
                    Elenca modelli reali
                  </button>
                </div>

                {(models[p.provider]?.length ?? 0) > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {models[p.provider].map((m) => (
                      <button
                        key={m.id}
                        type="button"
                        onClick={() =>
                          saveSettings(
                            { providers: { [p.provider]: { model: m.id } } },
                            `Modello predefinito per ${p.label}: ${m.id}`
                          )
                        }
                        className="rounded-full border border-[#E2D9C8] bg-[#FAF7F2] px-2 py-1 text-[10px] text-[#4A443C] hover:bg-[#EFE8DB]"
                        title={`${m.vision ? "visione" : "solo testo"} · ${
                          m.free || m.subscription ? "incluso/0 €" : `~${m.cost_per_1k_eur} €/1k token`
                        }`}
                      >
                        {m.label}
                        {m.vision ? " · 👁" : ""}
                        {m.free || m.subscription ? " · 0 €" : ""}
                      </button>
                    ))}
                  </div>
                )}
              </article>
            );
          })}
        </div>
        <p className="flex items-center gap-1.5 text-[11px] text-[#9A8052]">
          <Wallet className="h-3.5 w-3.5" />
          Ollama è opzionale: {status.ollama_installed ? "rilevato su questo computer" : "non installato"} — KORA
          funziona comunque con gli altri motori.
        </p>
      </section>

      {/* Agenti */}
      <section className="rounded-2xl border border-[#EDE6D9] bg-white p-4">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-[#1D1B18]">
          <Shield className="h-4 w-4 text-[#9A8052]" /> Modello per agente
        </h3>
        <p className="mt-1 text-xs text-[#6C6459]">
          Ogni agente può ereditare il motore principale oppure usarne uno specifico. Gli agenti con
          👁 richiedono un modello con visione: KORA non li indirizza mai su modelli solo testo.
        </p>
        <div className="mt-3 space-y-2">
          {status.agents.map((agent) => {
            const providerConf = agent.mode === "provider" ? agent.provider || "" : "";
            const agentModels = models[providerConf] || [];
            return (
              <div
                key={agent.id}
                className="grid items-center gap-2 rounded-xl border border-[#F2EBDF] bg-[#FAF7F2] p-2.5 lg:grid-cols-[1.2fr_0.8fr_0.8fr_auto]"
              >
                <div className="text-xs text-[#1D1B18]">
                  <span className="font-medium">
                    {agent.needs_vision ? "👁 " : ""}
                    {agent.label}
                  </span>
                  <span className="block text-[10px] text-[#6C6459]">{agent.description}</span>
                </div>
                <select
                  value={agent.mode}
                  onChange={(e) =>
                    updateAgent(agent, {
                      mode: e.target.value as AIEngineAgent["mode"],
                      provider: e.target.value === "inherit" ? null : agent.provider || status.providers[0]?.provider,
                    })
                  }
                  className="rounded-lg border border-[#E2D9C8] bg-white px-2 py-1.5 text-[11px]"
                >
                  <option value="inherit">Eredita il principale</option>
                  <option value="provider">Provider specifico</option>
                </select>
                <select
                  value={providerConf}
                  disabled={agent.mode !== "provider"}
                  onChange={(e) => updateAgent(agent, { mode: "provider", provider: e.target.value, model: null })}
                  className="rounded-lg border border-[#E2D9C8] bg-white px-2 py-1.5 text-[11px] disabled:opacity-40"
                >
                  <option value="">—</option>
                  {status.providers
                    .filter((p) => (agent.needs_vision ? p.supports_vision : true))
                    .map((p) => (
                      <option key={p.provider} value={p.provider}>
                        {p.label}
                      </option>
                    ))}
                </select>
                <div className="flex items-center gap-1">
                  <input
                    list={`models-${agent.id}`}
                    value={agent.model || ""}
                    disabled={agent.mode !== "provider"}
                    onChange={(e) => updateAgent(agent, { mode: "provider", provider: providerConf, model: e.target.value })}
                    onFocus={() => providerConf && !models[providerConf] && loadModels(providerConf)}
                    placeholder="modello (auto)"
                    className="w-full rounded-lg border border-[#E2D9C8] bg-white px-2 py-1.5 text-[11px] disabled:opacity-40"
                  />
                  <datalist id={`models-${agent.id}`}>
                    {agentModels
                      .filter((m) => (agent.needs_vision ? m.vision : true))
                      .map((m) => (
                        <option key={m.id} value={m.id} />
                      ))}
                  </datalist>
                  {busy === `agent:${agent.id}` && <Loader2 className="h-3 w-3 animate-spin text-[#9A8052]" />}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="rounded-2xl border border-[#EDE6D9] bg-[#FAF7F2] p-4 text-[11px] leading-relaxed text-[#6C6459]">
        <strong className="text-[#1D1B18]">Sicurezza.</strong> Le chiavi restano cifrate nel backend e non
        vengono mai mostrate qui. KORA non legge né copia i token di OpenCode/Codex: chiede al CLI
        ufficiale di eseguire il lavoro, con permessi minimi e senza shell. Prima di inviare materiale
        riservato a un provider esterno viene richiesta un&apos;autorizzazione esplicita.
      </section>
    </div>
  );
}
