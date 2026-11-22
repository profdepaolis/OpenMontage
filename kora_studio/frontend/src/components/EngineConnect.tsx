"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ExternalLink, KeyRound, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { koraApi as api } from "@/lib/api";

type ConnectOption = {
  id: string;
  label: string;
  cost: string;
  how: string;
  ready: boolean;
  state?: string;
  detail?: string;
  link?: string;
};

type Props = {
  /** "compact" = striscia nella Home, "full" = pannello in Impostazioni */
  variant?: "compact" | "full";
  onConnected?: () => void;
};

/**
 * Collegamento del motore intelligente senza terminale.
 * Gemini AI Studio: si incolla la chiave, KORA la verifica, sceglie Flash,
 * dichiara la quota gratuita (0 €) e lo rende l'unico motore attivo.
 */
export function EngineConnect({ variant = "compact", onConnected }: Props) {
  const [options, setOptions] = useState<ConnectOption[]>([]);
  const [active, setActive] = useState<string>("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState<"" | "load" | "connect">("");
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");

  const load = useCallback(async () => {
    setBusy("load");
    try {
      const data = await api.engineConnectOptions();
      setOptions(data.options || []);
      setActive(data.essential?.active_provider || "");
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Impossibile leggere lo stato dei motori");
    } finally {
      setBusy("");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const connect = useCallback(async () => {
    const key = apiKey.trim();
    if (!key) {
      setError("Incolla la API key di Google AI Studio.");
      return;
    }
    setBusy("connect");
    setError("");
    setOk("");
    try {
      const res = await api.engineConnectGemini(key);
      setOk(
        `Gemini collegato${res.model ? ` (${res.model})` : ""} · motore attivo · costo NON verificabile dalle API: dipende dal tuo progetto Google. KORA blocca comunque le chiamate fuori budget.`
      );
      setApiKey("");
      setActive("gemini_api");
      await load();
      onConnected?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Collegamento non riuscito");
    } finally {
      setBusy("");
    }
  }, [apiKey, load, onConnected]);

  const gemini = options.find((o) => o.id === "gemini_api");
  const others = options.filter((o) => o.id !== "gemini_api");
  const activeLabel = options.find((o) => o.id === active)?.label || active || "nessuno";

  return (
    <section className="rounded-2xl border border-[#EDE6D9] bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-[#1D1B18]">
          <Sparkles className="h-4 w-4 text-[#9A8052]" /> Motore intelligente
          <span className="rounded-full bg-[#224C66]/10 px-2 py-0.5 text-[10px] font-medium text-[#224C66]">
            {gemini?.ready ? "collegato" : "da collegare"}
          </span>
        </h3>
        <button
          type="button"
          onClick={() => void load()}
          disabled={busy !== ""}
          className="inline-flex items-center gap-1.5 rounded-lg border border-[#DDD4C4] px-2 py-1 text-[11px] text-[#4A443C] disabled:opacity-50"
        >
          {busy === "load" ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
          Aggiorna stato
        </button>
      </div>

      <p className="mt-1 text-[11px] text-[#6C6459]">
        Motore attivo: <strong>{activeLabel}</strong>. Senza un motore collegato KORA scrive con la direzione
        creativa locale dichiarata. Con Gemini collegato hook e voice-over arrivano dal modello. La chiave
        viene verificata <strong>prima</strong> di essere salvata.
      </p>

      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="flex-1 text-[11px] text-[#4A443C]">
          API key Google AI Studio
          <input
            type="password"
            value={apiKey}
            autoComplete="off"
            placeholder="AIza… (incolla qui)"
            onChange={(event) => setApiKey(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void connect();
            }}
            className="mt-1 block w-full rounded-xl border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-2 text-xs"
          />
        </label>
        <button
          type="button"
          onClick={() => void connect()}
          disabled={busy === "connect"}
          className="inline-flex items-center justify-center gap-2 rounded-xl bg-[#224C66] px-3.5 py-2 text-xs font-medium text-white disabled:opacity-50"
        >
          {busy === "connect" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />}
          Collega e attiva
        </button>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-[#6C6459]">
        <a
          href={gemini?.link || "https://aistudio.google.com/apikey"}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-[#224C66] underline decoration-dotted"
        >
          Apri AI Studio e crea la chiave <ExternalLink className="h-3 w-3" />
        </a>
        <span>costo: non verificabile via API — nessuna garanzia automatica di gratuità</span>
      </div>

      {error ? (
        <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-[#B4472F]/10 px-2.5 py-1.5 text-[11px] text-[#B4472F]">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> {error}
        </p>
      ) : null}
      {ok ? (
        <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-[#2F6B4F]/10 px-2.5 py-1.5 text-[11px] text-[#2F6B4F]">
          <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0" /> {ok}
        </p>
      ) : null}

      {variant === "full" ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {others.map((opt) => (
            <div
              key={opt.id}
              className="rounded-xl border border-[#EDE6D9] bg-[#FAF7F2] p-2.5 text-[11px] text-[#4A443C]"
            >
              <div className="flex items-center justify-between gap-2">
                <strong className="text-[#1D1B18]">{opt.label}</strong>
                <span className={opt.ready ? "text-[#2F6B4F]" : "text-[#9A8052]"}>
                  {opt.ready ? "pronto" : "non collegato"}
                </span>
              </div>
              <p className="mt-1 text-[#6C6459]">{opt.how}</p>
              <p className="mt-1 text-[#9A8052]">{opt.cost}</p>
              {opt.link ? (
                <a
                  href={opt.link}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-flex items-center gap-1 text-[#224C66] underline decoration-dotted"
                >
                  Istruzioni <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export default EngineConnect;
