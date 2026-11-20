"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Cpu,
  Eye,
  KeyRound,
  ListChecks,
  Loader2,
  RefreshCw,
  Shield,
  Sparkles,
  Star,
  Terminal,
  Wallet,
  Wifi,
  WifiOff,
} from "lucide-react";
import type {
  AIActivityCall,
  AIActivitySummary,
  AIEngineAgent,
  AIEngineModel,
  AIEngineProvider,
  AIEngineStatus,
  AIEngineStrategy,
  AIEngineTestResult,
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
  const [allowUnknown, setAllowUnknown] = useState(false);
  const [fallback, setFallback] = useState<"off" | "free_only" | "authorized">("free_only");
  const [activity, setActivity] = useState<AIActivityCall[]>([]);
  const [activitySummary, setActivitySummary] = useState<AIActivitySummary | null>(null);
  const [lastTest, setLastTest] = useState<Record<string, AIEngineTestResult>>({});
  const [planInputs, setPlanInputs] = useState<Record<string, string>>({});

  const applyStatus = useCallback((next: AIEngineStatus) => {
    setStatus(next);
    setStrategy(next.settings.strategy);
    setPrimaryProvider(next.settings.primary_provider);
    setBudget(String(next.settings.max_budget_eur));
    setAllowPaid(next.settings.allow_paid_calls);
    setAllowUnknown(next.settings.allow_unknown_costs);
    setFallback(next.settings.fallback || "free_only");
    setPlanInputs((prev) => ({ ...(next.settings.declared_subscriptions || {}), ...prev }));
    setBaseUrlInput(next.settings.providers?.custom_openai?.base_url || "");
  }, []);

  const loadActivity = useCallback(async (limit = 60) => {
    try {
      const res = await koraApi.engineActivity(limit);
      setActivity(res.calls || []);
      setActivitySummary(res.summary || null);
    } catch {
      // il registro non blocca la schermata dei motori
    }
  }, []);

  const load = useCallback(
    async (refresh = false) => {
      setLoading(true);
      setError(null);
      try {
        applyStatus(await koraApi.engineStatus(refresh));
        await loadActivity();
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

  const verify = async (providerId: string, live = false) => {
    setBusy(`probe:${providerId}`);
    setMsg(null);
    setError(null);
    try {
      const probe = await koraApi.engineVerify(providerId, live);
      setStatus((prev) =>
        prev
          ? { ...prev, providers: prev.providers.map((p) => (p.provider === providerId ? probe : p)) }
          : prev
      );
      if (live && probe.live_test) {
        const test = probe.live_test;
        setLastTest((prev) => ({ ...prev, [providerId]: test }));
        if (test.ok) {
          setMsg(
            `${probe.label}: generazione reale riuscita in ${test.latency_ms ?? 0} ms con ${
              test.model || "modello auto"
            } → “${(test.preview || "").slice(0, 160)}”`
          );
        } else {
          setError(`${probe.label}: test live non riuscito — ${test.detail || ""}`);
        }
        return;
      }
      setMsg(`${probe.label}: stato «${STATE_STYLE[probe.state]?.label || probe.state}». ${probe.detail || ""}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const runTest = async (providerId: string, kind: "text" | "vault" | "probe", model?: string) => {
    const busyKey = `test:${providerId}:${kind}`;
    setBusy(busyKey);
    setMsg(null);
    setError(null);
    try {
      const res: AIEngineTestResult =
        kind === "probe"
          ? await koraApi.engineVisionTest(providerId, model)
          : await koraApi.engineTest({
              provider: providerId,
              model,
              multimodal: kind === "vault",
              prompt:
                kind === "vault"
                  ? "Descrivi in italiano, in una frase, ciò che vedi nella fotografia allegata (materiali, luce, ambiente)."
                  : "Rispondi solo con: KORA OK",
            });
      setLastTest((prev) => ({ ...prev, [providerId]: res }));
      if (res.ok) {
        const visionLine =
          res.vision_verified === undefined || res.vision_verified === null
            ? ""
            : res.vision_verified
              ? " · contenuto noto riconosciuto ✅"
              : " · contenuto noto NON riconosciuto: visione non verificata";
        setMsg(
          `${res.provider} · ${res.model || "modello auto"} · ${res.latency_ms ?? 0} ms · ${
            kind === "probe"
              ? "prova di visione su immagine a contenuto noto"
              : kind === "vault"
                ? "test su fotografia reale del Vault"
                : "test testuale"
          }${visionLine} → “${(res.preview || "").slice(0, 180)}”`
        );
      } else {
        setError(`${res.provider}: ${res.detail || "test non riuscito"}`);
      }
      if (res.provider_status) {
        const probe = res.provider_status;
        setStatus((prev) =>
          prev ? { ...prev, providers: prev.providers.map((p) => (p.provider === providerId ? probe : p)) } : prev
        );
      }
      await load(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const setPrimary = async (providerId: string) => {
    setBusy(`primary:${providerId}`);
    setMsg(null);
    try {
      const next = await koraApi.engineSetPrimary(providerId);
      applyStatus(next.status);
      setMsg(`Motore principale impostato su ${providerId}. Attivo subito, senza riavvio.`);
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
            Fallback autorizzato
            <select
              value={fallback}
              onChange={(e) => setFallback(e.target.value as "off" | "free_only" | "authorized")}
              className="mt-1 w-full rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-3 py-2 text-xs text-[#1D1B18]"
            >
              <option value="off">Nessun fallback (solo il motore scelto)</option>
              <option value="free_only">Fallback solo su motori a costo zero</option>
              <option value="authorized">Fallback anche su motori autorizzati a pagamento</option>
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
          <label className="flex items-start gap-2 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] p-3 text-xs text-[#4A443C]">
            <input
              type="checkbox"
              checked={allowUnknown}
              onChange={(e) => setAllowUnknown(e.target.checked)}
              className="mt-0.5 h-4 w-4"
            />
            <span>
              Autorizzo anche i motori con costo non verificabile.
              <span className="mt-1 block text-[11px] text-[#9A8052]">
                Disattivato (consigliato): con costi sconosciuti le chiamate vengono bloccate, anche a
                budget disponibile.
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
                  fallback,
                  max_budget_eur: Number(budget || 0),
                  allow_paid_calls: allowPaid,
                  allow_unknown_costs: allowUnknown,
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
                  <span>Visione verificata: {p.supports_vision ? "sì" : "no"}</span>
                  <span>Allegati immagini: {p.attachments ? p.attachment_mode || "sì" : "no"}</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-2 text-[10px] text-[#6C6459]">
                  <span>
                    Fatturazione: {(p.billing_kinds || []).length ? (p.billing_kinds as string[]).join(" · ") : "sconosciuta"}
                  </span>
                  <span>Costo noto: {p.models_count - (p.unknown_cost_models ?? 0)}/{p.models_count}</span>
                  <span>Visione documentata: {p.models_count - (p.unknown_vision_models ?? 0)}/{p.models_count}</span>
                  <span>
                    Ultimo test:{" "}
                    {p.last_test_at
                      ? `${new Date(p.last_test_at).toLocaleString("it-IT")}${
                          p.last_test_ok === false ? " (fallito)" : " (riuscito)"
                        }`
                      : "mai eseguito"}
                  </span>
                  <span>Tempo di risposta: {p.last_latency_ms ? `${p.last_latency_ms} ms` : "non misurato"}</span>
                </div>
                {p.models_count > 0 && (p.unknown_cost_models ?? 0) > 0 && (
                  <p className="mt-1 rounded-lg bg-amber-50 px-2 py-1 text-[10px] text-amber-900">
                    {(p.unknown_cost_models ?? 0)} modelli con costo non verificabile: con budget 0 € o
                    autorizzazione assente non vengono usati.
                  </p>
                )}

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

                {(p.provider === "opencode" || p.provider === "codex") && (
                  <div className="mt-3 flex gap-2">
                    <input
                      value={planInputs[p.provider] || ""}
                      onChange={(e) => setPlanInputs((prev) => ({ ...prev, [p.provider]: e.target.value }))}
                      placeholder="Piano dichiarato (es. ChatGPT Plus) — usato solo se la CLI non lo espone"
                      className="flex-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-3 py-2 text-[11px]"
                    />
                    <button
                      type="button"
                      onClick={() =>
                        saveSettings(
                          { declared_subscriptions: { [p.provider]: (planInputs[p.provider] || "").trim() } },
                          "Piano dichiarato registrato per i motori CLI."
                        )
                      }
                      className="rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-3 py-2 text-[11px] font-medium text-[#1D1B18]"
                    >
                      Salva piano
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
                    Rileva
                  </button>
                  <button
                    type="button"
                    onClick={() => verify(p.provider, true)}
                    disabled={busy === `probe:${p.provider}` || !p.authenticated}
                    title={p.authenticated ? "Esegue una generazione reale" : "Autentica prima il motore"}
                    className="inline-flex items-center gap-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-2.5 py-1.5 text-[11px] font-medium text-[#1D1B18] disabled:opacity-40"
                  >
                    Test connessione
                  </button>
                  <button
                    type="button"
                    onClick={() => runTest(p.provider, "text")}
                    disabled={busy === `test:${p.provider}:text` || !p.authenticated}
                    className="inline-flex items-center gap-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-2.5 py-1.5 text-[11px] font-medium text-[#1D1B18] disabled:opacity-40"
                  >
                    Test testo
                  </button>
                  <button
                    type="button"
                    onClick={() => runTest(p.provider, "vault")}
                    disabled={busy === `test:${p.provider}:vault` || !p.attachments}
                    title={p.attachments ? "Analizza una fotografia reale del Vault" : "Il motore non trasferisce immagini"}
                    className="inline-flex items-center gap-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-2.5 py-1.5 text-[11px] font-medium text-[#1D1B18] disabled:opacity-40"
                  >
                    <Eye className="h-3 w-3" /> Test foto
                  </button>
                  <button
                    type="button"
                    onClick={() => runTest(p.provider, "probe")}
                    disabled={busy === `test:${p.provider}:probe` || !p.attachments}
                    title="Immagine con contenuto noto: verifica reale della capacità visiva"
                    className="inline-flex items-center gap-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-2.5 py-1.5 text-[11px] font-medium text-[#1D1B18] disabled:opacity-40"
                  >
                    Prova visione
                  </button>
                  <button
                    type="button"
                    onClick={() => loadModels(p.provider)}
                    className="rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-2.5 py-1.5 text-[11px] font-medium text-[#1D1B18]"
                  >
                    Aggiorna modelli
                  </button>
                  <button
                    type="button"
                    onClick={() => setPrimary(p.provider)}
                    disabled={busy === `primary:${p.provider}`}
                    className="inline-flex items-center gap-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-2.5 py-1.5 text-[11px] font-medium text-[#1D1B18] disabled:opacity-50"
                  >
                    <Star className="h-3 w-3" /> Imposta primario
                  </button>
                </div>

                {lastTest[p.provider] && (
                  <p className="mt-2 rounded-lg bg-[#FAF7F2] px-2 py-1 text-[10px] text-[#4A443C]">
                    Ultima prova in questa sessione: {lastTest[p.provider].ok ? "riuscita" : "non riuscita"}
                    {lastTest[p.provider].model ? ` · ${lastTest[p.provider].model}` : ""}
                    {lastTest[p.provider].latency_ms ? ` · ${lastTest[p.provider].latency_ms} ms` : ""}
                    {lastTest[p.provider].vision_verified === true ? " · visione verificata" : ""}
                    {lastTest[p.provider].vision_verified === false ? " · visione non verificata" : ""}
                  </p>
                )}

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
                        title={[
                          m.vision_verified
                            ? "visione VERIFICATA con immagine a contenuto noto"
                            : m.vision_documented ?? m.vision
                              ? "visione documentata, non ancora verificata"
                              : "capacità visiva non documentata",
                          `esecuzione: ${m.execution}`,
                          `fatturazione: ${m.billing}`,
                          m.cost_known ? `costo ${m.cost_per_1k_eur} €/1k token` : "costo non verificabile",
                          m.zero_cost ? "a costo zero" : "possibile costo",
                        ].join(" · ")}
                      >
                        {m.label}
                        {m.vision_verified ? " · 👁✓" : (m.vision_documented ?? m.vision) ? " · 👁?" : ""}
                        {m.zero_cost ? " · 0 €" : m.cost_known ? "" : " · costo ?"}
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

      {/* AI Activity Monitor */}
      <section className="rounded-2xl border border-[#EDE6D9] bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-[#1D1B18]">
            <ListChecks className="h-4 w-4 text-[#9A8052]" /> Registro delle chiamate AI
          </h3>
          <button
            type="button"
            onClick={() => loadActivity(120)}
            className="inline-flex items-center gap-1 rounded-xl border border-[#E2D9C8] bg-[#FAF7F2] px-2.5 py-1.5 text-[11px] font-medium text-[#1D1B18]"
          >
            <RefreshCw className="h-3 w-3" /> Aggiorna registro
          </button>
        </div>
        <p className="mt-1 text-[11px] text-[#6C6459]">
          Una riga per chiamata: produzione, agente, provider e modello reali, durata, esito, tentativi,
          fallback e costo (0 €, stimato o sconosciuto). Nessun prompt e nessuna credenziale.
        </p>
        {activitySummary && (
          <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-[#4A443C]">
            <span>Chiamate: {activitySummary.calls}</span>
            <span>Riuscite: {activitySummary.ok}</span>
            <span>Errori: {activitySummary.errors}</span>
            <span>Fallback: {activitySummary.fallbacks}</span>
            <span>Costo reale: {(activitySummary.actual_cost_eur ?? activitySummary.known_cost_eur).toFixed(3)} €</span>
            <span>Costo stimato: {(activitySummary.estimated_cost_eur ?? 0).toFixed(3)} €</span>
            <span>Costo sconosciuto: {activitySummary.unknown_cost_calls} chiamate</span>
          </div>
        )}
        {activity.length === 0 ? (
          <p className="mt-3 rounded-xl border border-dashed border-[#E2D9C8] bg-[#FAF7F2] p-3 text-[11px] text-[#6C6459]">
            Nessuna chiamata registrata in questa installazione.
          </p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-[11px]">
              <thead className="text-[10px] uppercase tracking-wide text-[#9A8052]">
                <tr>
                  <th className="pb-1 pr-2">Ora</th>
                  <th className="pb-1 pr-2">Produzione</th>
                  <th className="pb-1 pr-2">Agente</th>
                  <th className="pb-1 pr-2">Provider / modello</th>
                  <th className="pb-1 pr-2">Auth</th>
                  <th className="pb-1 pr-2">Allegati</th>
                  <th className="pb-1 pr-2">Durata</th>
                  <th className="pb-1 pr-2">Stato</th>
                  <th className="pb-1 pr-2">Fallback</th>
                  <th className="pb-1 pr-2">Tariffa /1k</th>
                  <th className="pb-1 pr-2">Stimato</th>
                  <th className="pb-1 pr-2">Reale</th>
                </tr>
              </thead>
              <tbody className="text-[#4A443C]">
                {activity.map((row) => (
                  <tr key={row.id} className="border-t border-[#F2EBDF]">
                    <td className="py-1 pr-2">{new Date(row.started_at).toLocaleString("it-IT")}</td>
                    <td className="py-1 pr-2 font-mono text-[10px]">{row.production_id || "—"}</td>
                    <td className="py-1 pr-2">{row.agent}</td>
                    <td className="py-1 pr-2">
                      {row.provider}
                      <span className="text-[#9A8052]">/{row.model}</span>
                    </td>
                    <td className="py-1 pr-2">{row.auth_type || "—"}</td>
                    <td className="py-1 pr-2">
                      {row.vision_image ? "immagine" : ""}
                      {row.attachments ? ` (${row.attachments})` : row.vision_image ? "" : "—"}
                    </td>
                    <td className="py-1 pr-2">{row.duration_ms} ms</td>
                    <td className="py-1 pr-2">
                      {row.request_status === "completed" || row.outcome === "ok"
                        ? "✅ riuscita"
                        : row.cancelled
                        ? "⏹ annullata"
                        : `⚠ ${row.outcome}`}
                      {row.attempts > 1 ? ` · ${row.attempts} tent.` : ""}
                    </td>
                    <td className="py-1 pr-2">{row.fallback_used ? `sì (da ${row.fallback_from || "?"})` : "no"}</td>
                    <td className="py-1 pr-2">
                      {row.unit_price_per_1k_eur !== null && row.unit_price_per_1k_eur !== undefined
                        ? `${row.unit_price_per_1k_eur.toFixed(4)} €`
                        : "n/d"}
                    </td>
                    <td className="py-1 pr-2">
                      {row.estimated_cost_eur !== null && row.estimated_cost_eur !== undefined
                        ? `${row.estimated_cost_eur.toFixed(4)} €`
                        : "—"}
                    </td>
                    <td className="py-1 pr-2">
                      {row.cost_known && (row.actual_cost_eur ?? row.cost_eur) !== null &&
                      (row.actual_cost_eur ?? row.cost_eur) !== undefined
                        ? `${Number(row.actual_cost_eur ?? row.cost_eur).toFixed(4)} €`
                        : "sconosciuto"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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
