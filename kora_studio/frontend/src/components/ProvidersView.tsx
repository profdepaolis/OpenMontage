"use client";

import React, { useState } from "react";
import { KeyRound, RefreshCw, Shield, Wallet } from "lucide-react";
import type { BudgetSettings, ProviderRecord } from "@/lib/types";
import { koraApi } from "@/lib/api";

interface ProvidersViewProps {
  providers: ProviderRecord[];
  budget: BudgetSettings | null;
  onRefresh: () => void;
}

export function ProvidersView({ providers, budget, onRefresh }: ProvidersViewProps) {
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [paidInput, setPaidInput] = useState<string>(
    budget ? String(budget.paid_budget_eur) : "0"
  );
  const [capInput, setCapInput] = useState<string>(
    budget ? String(budget.monthly_cap_eur) : "20"
  );
  const [msg, setMsg] = useState<string | null>(null);

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
    setMsg(null);
    try {
      await koraApi.setProviderKey(providerId, val);
      setKeyInputs((prev) => ({ ...prev, [providerId]: "" }));
      setMsg(`Chiave cifrata aggiornata e testata per ${providerId}.`);
      onRefresh();
    } catch (err) {
      setMsg((err as Error).message);
    }
  };

  const handleCheck = async (providerId: string) => {
    await koraApi.checkProvider(providerId);
    onRefresh();
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="font-editorial text-2xl text-[#1D1B18]">AI Provider Manager & Crediti</h2>
        <p className="text-xs text-[#6C6459]">
          Architettura modulare multi-provider. Budget predefinito 0 € per generazioni a pagamento; chiavi cifrate localmente fuori dal codice.
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
          <div className="mt-4 flex items-center justify-between">
            <span className="text-xs text-[#6C6459]">
              Nessun servizio a pagamento viene attivato senza autorizzazione esplicita.
            </span>
            <button
              type="submit"
              className="rounded-xl bg-[#1D1B18] px-4 py-2 text-xs font-medium text-white hover:bg-[#224C66]"
            >
              Salva Budget
            </button>
          </div>
        </form>
      )}

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
                <div className="mt-0.5 text-[11px] text-[#6C6459]">{p.endpoint}</div>
              </div>
              <span
                className={`rounded-full px-2.5 py-0.5 text-[10px] font-medium ${
                  p.operational_status === "operativo"
                    ? "bg-emerald-50 text-emerald-800"
                    : p.operational_status === "errore"
                      ? "bg-red-50 text-red-700"
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
            </div>
            <p className="text-[11px] text-[#6C6459]">{p.limits_doc}</p>

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
                  onClick={() => handleSaveKey(p.id)}
                  className="flex items-center justify-center gap-1 rounded-lg bg-[#1D1B18] px-3 py-1.5 text-xs text-white"
                >
                  <Shield className="h-3 w-3" /> Salva
                </button>
                <button
                  type="button"
                  onClick={() => handleCheck(p.id)}
                  className="flex items-center justify-center gap-1 rounded-lg border border-[#DDD4C4] px-3 py-1.5 text-xs"
                >
                  <RefreshCw className="h-3 w-3" /> Verifica
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
