"use client";

import React, { useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Download,
  KeyRound,
  Mic,
  RefreshCw,
  Shield,
  Terminal,
  Trash2,
  Volume2,
  Wallet,
} from "lucide-react";
import type { BudgetSettings, ProviderRecord, VoiceOption } from "@/lib/types";
import { koraApi } from "@/lib/api";

interface ProvidersViewProps {
  providers: ProviderRecord[];
  budget: BudgetSettings | null;
  voices: VoiceOption[];
  onRefresh: () => void;
}

export function ProvidersView({ providers, budget, voices, onRefresh }: ProvidersViewProps) {
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [voiceIds, setVoiceIds] = useState<Record<string, string>>({});
  const [accountVoices, setAccountVoices] = useState<
    Array<{
      voice_id: string;
      name: string;
      category: string;
      preview_url?: string;
      labels?: Record<string, string>;
      usable_on_free_plan?: boolean;
      library_voice?: boolean;
    }>
  >([]);
  const [audioPreviewUrl, setAudioPreviewUrl] = useState<string | null>(null);
  const [audioPreviewLabel, setAudioPreviewLabel] = useState<string | null>(null);
  const [paidInput, setPaidInput] = useState<string>(
    budget ? String(budget.paid_budget_eur) : "0"
  );
  const [capInput, setCapInput] = useState<string>(
    budget ? String(budget.monthly_cap_eur) : "20"
  );
  const [remotionInfo, setRemotionInfo] = useState<{
    available: boolean;
    node?: string | null;
    remotion_bin?: string | null;
    chromium?: string | null;
    reasons: string[];
    install_command?: string;
  } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (budget) {
      setPaidInput(String(budget.paid_budget_eur));
      setCapInput(String(budget.monthly_cap_eur));
    }
  }, [budget]);

  useEffect(() => {
    const initial: Record<string, string> = {};
    voices.forEach((v) => {
      if (v.id !== "none") initial[v.id] = v.elevenlabs_voice_id || "";
    });
    setVoiceIds(initial);
  }, [voices]);

  useEffect(() => {
    koraApi
      .health()
      .then((h) => {
        if (h.remotion) {
          setRemotionInfo(h.remotion as typeof remotionInfo);
        }
      })
      .catch(() => {});
  }, [providers]);

  const handleSaveBudget = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    try {
      await koraApi.updateBudget(Number(paidInput), Number(capInput));
      setMsg("Limiti di budget salvati.");
      onRefresh();
    } catch (err) {
      setMsg((err as Error).message);
    }
  };

  const handleSaveKey = async (providerId: string) => {
    const val = keyInputs[providerId] ?? "";
    if (!val.trim()) return;
    setBusyId(providerId);
    setMsg(null);
    try {
      const updated = await koraApi.setProviderKey(providerId, val);
      setKeyInputs((prev) => ({ ...prev, [providerId]: "" }));
      if (updated.operational_status === "operativo") {
        setMsg(`Chiave salvata, cifrata e verificata con successo per ${updated.name}.`);
      } else {
        setMsg(
          `Chiave salvata e cifrata per ${updated.name}. Esito test: ${
            updated.recent_errors?.[0] || updated.operational_status
          }`
        );
      }
      onRefresh();
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const handleClearKey = async (providerId: string) => {
    setBusyId(providerId);
    setMsg(null);
    try {
      await koraApi.setProviderKey(providerId, "");
      setMsg(`Chiave rimossa per ${providerId}.`);
      onRefresh();
    } finally {
      setBusyId(null);
    }
  };

  const handleCheck = async (providerId: string) => {
    setBusyId(providerId);
    try {
      await koraApi.checkProvider(providerId);
      onRefresh();
    } finally {
      setBusyId(null);
    }
  };

  const handleSetupRemotion = async () => {
    setBusyId("remotion_local");
    setMsg(null);
    try {
      const res = await koraApi.setupRemotion();
      setRemotionInfo(res.remotion);
      setMsg(
        res.remotion.available
          ? "Remotion Composer Locale attivo e pronto!"
          : `Remotion preparato (${res.logs.join(" · ")}). ${res.remotion.reasons.join(" · ")}`
      );
      onRefresh();
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const handleInstallPiper = async () => {
    setBusyId("piper_tts_local");
    setMsg(null);
    try {
      const res = await koraApi.installPiperModel();
      setMsg(
        `Modello vocale italiano Piper scaricato (${res.files.join(", ")}). Piper TTS Locale ora è operativo!`
      );
      onRefresh();
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const handleFetchElevenLabsVoices = async () => {
    setBusyId("fetch_el_voices");
    setMsg(null);
    try {
      const list = await koraApi.fetchElevenLabsVoices();
      setAccountVoices(list);
      // Auto-match voices named Brando / Tiziana / Felice if present
      const updates: Record<string, string> = { ...voiceIds };
      for (const v of list) {
        const low = v.name.toLowerCase();
        if (low.includes("brando") && !updates["brando"]) updates["brando"] = v.voice_id;
        if (low.includes("tiziana") && !updates["tiziana"]) updates["tiziana"] = v.voice_id;
        if (low.includes("felice") && !updates["felice"]) updates["felice"] = v.voice_id;
      }
      setVoiceIds(updates);
      setMsg(
        `Caricate ${list.length} voci dal tuo account ElevenLabs. Selezionale qui sotto e clicca "Salva ID".`
      );
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const handleSaveVoiceId = async (voiceId: string) => {
    setBusyId(`voice_${voiceId}`);
    try {
      await koraApi.setVoiceElevenLabsId(voiceId, voiceIds[voiceId] || "");
      setMsg(`Voice ID ElevenLabs salvato per ${voiceId}.`);
      onRefresh();
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="font-editorial text-2xl text-[#1D1B18]">AI Provider Manager & Crediti</h2>
        <p className="text-xs text-[#6C6459]">
          Architettura modulare multi-provider. Budget predefinito 0 € per generazioni a pagamento; chiavi cifrate localmente in <code>.kora_master.key</code>.
        </p>
      </div>

      {/* Budget Panel */}
      {budget && (
        <form
          onSubmit={handleSaveBudget}
          className="rounded-2xl border border-[#DDD4C4] bg-white p-6 shadow-sm"
        >
          <div className="flex items-center gap-2 text-sm font-medium text-[#1D1B18]">
            <Wallet className="h-4 w-4 text-[#9A8052]" />
            Gestione Crediti & Budget Mensile
          </div>
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-4">
            <div className="rounded-xl bg-[#FAF7F2] p-3">
              <div className="text-[11px] text-[#6C6459]">Spesa effettiva mese</div>
              <div className="mt-1 text-lg font-semibold text-[#1D1B18]">
                {budget.actual_month_eur.toFixed(2)} €
              </div>
            </div>
            <div className="rounded-xl bg-[#FAF7F2] p-3">
              <div className="text-[11px] text-[#6C6459]">Consumo stimato</div>
              <div className="mt-1 text-lg font-semibold text-[#1D1B18]">
                {budget.estimated_month_eur.toFixed(2)} €
              </div>
            </div>
            <label className="rounded-xl bg-[#FAF7F2] p-3 text-[11px] text-[#6C6459]">
              Budget autorizzato (€)
              <input
                type="number"
                step="0.5"
                min="0"
                max="20"
                value={paidInput}
                onChange={(e) => setPaidInput(e.target.value)}
                className="mt-1 w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1 text-sm font-semibold text-[#1D1B18]"
              />
            </label>
            <label className="rounded-xl bg-[#FAF7F2] p-3 text-[11px] text-[#6C6459]">
              Limite mensile (max 20 €)
              <input
                type="number"
                step="0.5"
                min="0"
                max="20"
                value={capInput}
                onChange={(e) => setCapInput(e.target.value)}
                className="mt-1 w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1 text-sm font-semibold text-[#1D1B18]"
              />
            </label>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs text-[#6C6459]">
              Per usare ElevenLabs o Google Veo nei Reel, imposta il Budget autorizzato &gt; 0 € (es. 5 €) e salva.
            </span>
            <div className="flex flex-wrap items-center gap-2">
              {budget.paid_budget_eur <= 0 && (
                <button
                  type="button"
                  onClick={async () => {
                    const target = Math.max(5, Number(capInput) || 5);
                    setPaidInput(String(target));
                    setCapInput(String(Math.max(target, Number(capInput) || target)));
                    await koraApi.updateBudget(target, Math.max(target, Number(capInput) || target));
                    setMsg(`Budget autorizzato impostato a ${target.toFixed(2)} € (ElevenLabs sbloccato!).`);
                    onRefresh();
                  }}
                  className="rounded-xl bg-[#9A8052] px-3.5 py-2 text-xs font-medium text-white hover:bg-[#7f673f]"
                >
                  Autorizza {Math.max(5, Number(capInput) || 5)} € subito
                </button>
              )}
              <button
                type="submit"
                className="rounded-xl bg-[#1D1B18] px-4 py-2 text-xs font-medium text-white hover:bg-[#224C66]"
              >
                Salva Budget
              </button>
            </div>
          </div>
          {budget.paid_budget_eur <= 0 && (
            <div className="mt-3 rounded-xl border border-amber-300 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-900">
              <strong>Nota importante:</strong> il <em>Budget autorizzato</em> è a <strong>0.00 €</strong> (anche se il limite mensile è {budget.monthly_cap_eur.toFixed(0)} €). Finché resta a 0 €, ElevenLabs viene bloccato dal Cost Guard e KORA usa Piper TTS locale gratuito.
            </div>
          )}
        </form>
      )}

      {/* Remotion Composer Diagnostics + ElevenLabs Voice Mapping */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-[#DDD4C4] bg-white p-5 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-medium text-[#1D1B18]">
              <Cpu className="h-4 w-4 text-[#224C66]" />
              Attivazione Remotion Composer Locale
            </div>
            <button
              type="button"
              onClick={handleSetupRemotion}
              disabled={busyId === "remotion_local"}
              className="rounded-lg bg-[#224C66] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#1D1B18] disabled:opacity-50"
            >
              {busyId === "remotion_local" ? "Installazione..." : "Attiva / Verifica"}
            </button>
          </div>
          <p className="text-xs text-[#6C6459]">
            KORA STUDIO genera sempre le composizioni <code>remotion_composition.json</code> e usa FFmpeg come motore veloce garantito; quando Chrome/Chromium è presente attiva anche Remotion.
          </p>
          {remotionInfo && (
            <div className="space-y-1.5 rounded-xl bg-[#FAF7F2] p-3 text-xs">
              <div className="flex items-center gap-2">
                {remotionInfo.node ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-700" />
                ) : (
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                )}
                <span>Node.js: {remotionInfo.node ? "presente" : "mancante"}</span>
              </div>
              <div className="flex items-center gap-2">
                {remotionInfo.remotion_bin ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-700" />
                ) : (
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                )}
                <span>Pacchetti Remotion: {remotionInfo.remotion_bin ? "installati" : "da installare"}</span>
              </div>
              <div className="flex items-center gap-2">
                {remotionInfo.chromium ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-700" />
                ) : (
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                )}
                <span>
                  Chrome / Chromium: {remotionInfo.chromium ? remotionInfo.chromium : "non trovato in questo ambiente"}
                </span>
              </div>
              {!remotionInfo.available && (
                <div className="mt-2 flex items-center gap-2 rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-2 font-mono text-[11px] text-[#1D1B18]">
                  <Terminal className="h-3.5 w-3.5 shrink-0 text-[#9A8052]" />
                  <span className="truncate">
                    {remotionInfo.install_command || "cd remotion-composer && npm install && npx remotion browser ensure"}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="rounded-2xl border border-[#DDD4C4] bg-white p-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-sm font-medium text-[#1D1B18]">
              <Mic className="h-4 w-4 text-[#9A8052]" />
              Mappatura Voci ElevenLabs (Brando · Tiziana · Felice)
            </div>
            <button
              type="button"
              onClick={handleFetchElevenLabsVoices}
              disabled={busyId === "fetch_el_voices"}
              className="rounded-lg bg-[#1D1B18] px-3 py-1.5 text-[11px] font-medium text-white hover:bg-[#224C66] disabled:opacity-50"
            >
              {busyId === "fetch_el_voices" ? "Caricamento..." : "Carica voci da ElevenLabs"}
            </button>
          </div>
          <p className="text-xs text-[#6C6459]">
            Collega i Voice ID del tuo account ElevenLabs (e autorizza un budget &gt; 0 €) affinché Brando e Tiziana leggano ogni nuovo copione.
          </p>
          <div className="space-y-2">
            {voices
              .filter((v) => v.id !== "none")
              .map((v) => (
                <div key={v.id} className="flex flex-wrap items-center gap-2">
                  <span className="w-16 text-xs font-medium text-[#1D1B18]">{v.label}</span>
                  {accountVoices.length > 0 ? (
                    <select
                      value={voiceIds[v.id] || ""}
                      onChange={(e) =>
                        setVoiceIds((prev) => ({ ...prev, [v.id]: e.target.value }))
                      }
                      className="flex-1 min-w-[160px] rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-2.5 py-1.5 text-xs"
                    >
                      <option value="">-- Seleziona voce ElevenLabs --</option>
                      {accountVoices.map((av) => (
                        <option key={av.voice_id} value={av.voice_id}>
                          {av.name} ({av.category || "custom"})
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      value={voiceIds[v.id] || ""}
                      onChange={(e) =>
                        setVoiceIds((prev) => ({ ...prev, [v.id]: e.target.value }))
                      }
                      placeholder={`Voice ID ElevenLabs per ${v.label}`}
                      className="flex-1 min-w-[160px] rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-1.5 text-xs"
                    />
                  )}
                  <button
                    type="button"
                    title="Ascolta anteprima voce"
                    onClick={() => {
                      const sel = accountVoices.find((av) => av.voice_id === voiceIds[v.id]);
                      if (sel?.preview_url) {
                        setAudioPreviewUrl(sel.preview_url);
                        setAudioPreviewLabel(`ElevenLabs · ${sel.name}`);
                      } else {
                        setAudioPreviewUrl(`/api/voices/${v.id}/sample?engine=auto&t=${Date.now()}`);
                        setAudioPreviewLabel(`Anteprima live · ${v.label}`);
                      }
                    }}
                    className="inline-flex items-center gap-1 rounded-lg border border-[#DDD4C4] bg-[#FAF7F2] px-2.5 py-1.5 text-xs text-[#1D1B18] hover:border-[#9A8052]"
                  >
                    <Volume2 className="h-3.5 w-3.5 text-[#9A8052]" />
                    Ascolta
                  </button>
                  <button
                    type="button"
                    onClick={() => handleSaveVoiceId(v.id)}
                    disabled={busyId === `voice_${v.id}`}
                    className="rounded-lg border border-[#DDD4C4] bg-white px-3 py-1.5 text-xs hover:border-[#9A8052]"
                  >
                    Salva ID
                  </button>
                </div>
              ))}
          </div>
          {audioPreviewUrl && (
            <div className="rounded-xl border border-[#DDD4C4] bg-[#FAF7F2] p-2.5">
              <div className="mb-1 text-[11px] font-medium text-[#1D1B18]">
                In ascolto: {audioPreviewLabel}
              </div>
              <audio key={audioPreviewUrl} src={audioPreviewUrl} controls autoPlay className="h-8 w-full" />
            </div>
          )}
          {accountVoices.length > 0 && (
            <div className="max-h-48 overflow-y-auto rounded-xl border border-[#EDE6D9] bg-[#FAF7F2] p-2.5 space-y-1.5">
              <div className="text-[11px] font-medium text-[#6C6459]">
                Catalogo voci ElevenLabs ({accountVoices.length}) — ascolta e assegna:
              </div>
              <p className="text-[10px] text-[#6C6459]">
                KORA accetta solo voci «ok anche Free» (premade, clonate o professional) quando l&apos;account è
                Free; le voci di libreria rispondono 402 e vengono sostituite automaticamente.
              </p>
              {accountVoices.map((av) => (
                <div
                  key={av.voice_id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-white px-2.5 py-1.5 text-[11px]"
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-medium text-[#1D1B18]">{av.name}</span>{" "}
                    <span className="text-[#6C6459]">({av.category || "voice"})</span>
                    {av.library_voice && (
                      <span
                        className="rounded-full bg-amber-50 px-1.5 py-0.5 text-[9px] font-medium text-amber-800"
                        title="Le voci della libreria comunitaria non sono utilizzabili via API con il piano Free (HTTP 402)."
                      >
                        libreria · serve piano a pagamento
                      </span>
                    )}
                    {av.usable_on_free_plan && (
                      <span className="rounded-full bg-emerald-50 px-1.5 py-0.5 text-[9px] font-medium text-emerald-800">
                        ok anche Free
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5">
                    {av.preview_url && (
                      <button
                        type="button"
                        onClick={() => {
                          setAudioPreviewUrl(av.preview_url || null);
                          setAudioPreviewLabel(`ElevenLabs · ${av.name}`);
                        }}
                        className="inline-flex items-center gap-1 rounded border border-[#DDD4C4] px-2 py-0.5 text-[10px] hover:border-[#9A8052]"
                      >
                        <Volume2 className="h-3 w-3 text-[#9A8052]" /> Ascolta
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={async () => {
                        setVoiceIds((prev) => ({ ...prev, brando: av.voice_id }));
                        await koraApi.setVoiceElevenLabsId("brando", av.voice_id);
                        setMsg(`Voce '${av.name}' assegnata a Brando!`);
                        onRefresh();
                      }}
                      className="rounded bg-[#1D1B18] px-2 py-0.5 text-[10px] text-white hover:bg-[#224C66]"
                    >
                      Usa su Brando
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {msg && (
        <div className="rounded-xl border border-[#DDD4C4] bg-[#EFE8DB] px-4 py-2.5 text-xs text-[#1D1B18]">
          {msg}
        </div>
      )}

      {/* Providers List */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {providers.map((p) => (
          <div key={p.id} className="rounded-2xl border border-[#DDD4C4] bg-white p-5 space-y-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold text-[#1D1B18]">{p.name}</h3>
                <div className="mt-0.5 text-[11px] text-[#6C6459] break-all">{p.endpoint}</div>
              </div>
              <span
                className={`shrink-0 rounded-full px-2.5 py-0.5 text-[10px] font-medium ${
                  p.operational_status === "operativo"
                    ? "bg-emerald-50 text-emerald-800"
                    : p.operational_status === "errore"
                      ? "bg-red-50 text-red-700"
                      : p.operational_status === "limitato"
                        ? "bg-amber-50 text-amber-800"
                        : p.operational_status === "configurato"
                          ? "bg-sky-50 text-sky-800"
                          : "bg-[#EFE8DB] text-[#6C6459]"
                }`}
              >
                {p.operational_status}
              </span>
            </div>

            <div className="flex flex-wrap gap-1">
              {p.capabilities.map((cap) => (
                <span key={cap} className="rounded bg-[#FAF7F2] px-2 py-0.5 text-[10px] text-[#4A443C]">
                  {cap}
                </span>
              ))}
            </div>

            <div className="grid grid-cols-2 gap-2 rounded-xl bg-[#FAF7F2] p-3 text-[11px]">
              <div>
                <span className="text-[#6C6459]">Crediti residui: </span>
                <span className="font-medium text-[#1D1B18]">{p.quota_status}</span>
              </div>
              <div>
                <span className="text-[#6C6459]">Autenticazione: </span>
                <span className="font-medium text-[#1D1B18]">{p.auth_type}</span>
              </div>
              {p.avg_latency_ms !== null && p.avg_latency_ms !== undefined && (
                <div className="col-span-2 text-[10px] text-[#6C6459]">
                  Latenza ultima verifica: {p.avg_latency_ms} ms
                </div>
              )}
            </div>

            {p.recent_errors && p.recent_errors.length > 0 && (
              <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50/80 p-2.5 text-[11px] text-red-900">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600" />
                <span>{p.recent_errors[0]}</span>
              </div>
            )}

            <p className="text-[11px] text-[#6C6459]">{p.limits_doc}</p>

            {p.id === "piper_tts_local" && p.operational_status !== "operativo" && (
              <button
                type="button"
                disabled={busyId === "piper_tts_local"}
                onClick={handleInstallPiper}
                className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-[#224C66] px-3 py-2 text-xs font-medium text-white hover:bg-[#1D1B18] disabled:opacity-50"
              >
                <Download className="h-3.5 w-3.5" />
                {busyId === "piper_tts_local"
                  ? "Download voce italiana in corso..."
                  : "Scarica voce italiana Piper offline (0 €)"}
              </button>
            )}

            {!p.is_free_local && (
              <div className="flex flex-col gap-2 sm:flex-row">
                <div className="relative flex-1">
                  <KeyRound className="absolute left-2.5 top-2 h-3.5 w-3.5 text-[#9A8052]" />
                  <input
                    type="password"
                    value={keyInputs[p.id] || ""}
                    onChange={(e) =>
                      setKeyInputs((prev) => ({ ...prev, [p.id]: e.target.value }))
                    }
                    placeholder={
                      p.has_api_key ? `Salvata (${p.masked_api_key})` : "Inserisci API key ufficiale"
                    }
                    className="w-full rounded-lg border border-[#DDD4C4] pl-8 pr-3 py-1.5 text-xs"
                  />
                </div>
                <button
                  type="button"
                  disabled={busyId === p.id}
                  onClick={() => handleSaveKey(p.id)}
                  className="flex items-center justify-center gap-1 rounded-lg bg-[#1D1B18] px-3 py-1.5 text-xs text-white disabled:opacity-50"
                >
                  <Shield className="h-3 w-3" /> Salva
                </button>
                <button
                  type="button"
                  disabled={busyId === p.id}
                  onClick={() => handleCheck(p.id)}
                  className="flex items-center justify-center gap-1 rounded-lg border border-[#DDD4C4] px-3 py-1.5 text-xs disabled:opacity-50"
                >
                  <RefreshCw className="h-3 w-3" /> Verifica
                </button>
                {p.has_api_key && (
                  <button
                    type="button"
                    disabled={busyId === p.id}
                    onClick={() => handleClearKey(p.id)}
                    title="Rimuovi chiave salvata"
                    className="flex items-center justify-center rounded-lg border border-red-200 px-2.5 py-1.5 text-xs text-red-700 hover:bg-red-50"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
