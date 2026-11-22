"use client";

import React, { useEffect, useState } from "react";
import { RefreshCw, RotateCcw, ShieldCheck } from "lucide-react";
import { koraApi } from "@/lib/api";

export function BrandProfileView() {
  const [brand, setBrand] = useState<Record<string, unknown> | null>(null);
  const [history, setHistory] = useState<Array<Record<string, unknown>>>([]);
  const [philosophy, setPhilosophy] = useState("");
  const [note, setNote] = useState("");

  const load = async () => {
    const [b, h] = await Promise.all([koraApi.getBrand(), koraApi.brandHistory()]);
    setBrand(b);
    setHistory(h);
    setPhilosophy(String(b.luxury_philosophy || ""));
  };

  useEffect(() => {
    load();
  }, []);

  if (!brand) return null;

  const palette = (brand.palette || {}) as {
    status?: string;
    derived_from?: string[];
    colors?: Record<string, { label: string; hex: string; source: string }>;
  };
  const values = (brand.values || []) as string[];
  const logo = (brand.logo || {}) as { original_filename?: string; payoff_exact?: string };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    await koraApi.updateBrand(
      { ...brand, luxury_philosophy: philosophy },
      note || "Aggiornamento filosofia KORA"
    );
    setNote("");
    await load();
  };

  const handleRefreshPalette = async () => {
    await koraApi.refreshPalette();
    await load();
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="font-editorial text-2xl text-[#1D1B18]">KORA Brand Brain</h2>
          <p className="text-xs text-[#6C6459]">
            Versione {String(brand.version)} · {String(brand.location)} · Payoff:{" "}
            <strong className="text-[#1D1B18]">{String(brand.payoff)}</strong>
          </p>
        </div>
        <button
          type="button"
          onClick={handleRefreshPalette}
          className="flex items-center gap-1.5 rounded-xl border border-[#DDD4C4] bg-white px-4 py-2 text-xs font-medium hover:border-[#9A8052]"
        >
          <RefreshCw className="h-3.5 w-3.5" /> Ricava palette da materiali autentici
        </button>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Values & Philosophy */}
        <form onSubmit={handleSave} className="rounded-2xl border border-[#DDD4C4] bg-white p-6 space-y-4">
          <h3 className="text-sm font-medium text-[#1D1B18]">Valori creativi KORA</h3>
          <div className="flex flex-wrap gap-2">
            {values.map((v) => (
              <span
                key={v}
                className="rounded-full border border-[#DDD4C4] bg-[#FAF7F2] px-3 py-1 text-xs text-[#1D1B18]"
              >
                {v}
              </span>
            ))}
          </div>
          <label className="block text-xs font-medium text-[#6C6459]">
            Filosofia narrativa (modificabile e versionata)
            <textarea
              rows={3}
              value={philosophy}
              onChange={(e) => setPhilosophy(e.target.value)}
              className="mt-1 w-full rounded-xl border border-[#DDD4C4] p-3 text-sm text-[#1D1B18]"
            />
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Nota di versione (opzionale)"
              className="flex-1 rounded-xl border border-[#DDD4C4] px-3 py-2 text-xs"
            />
            <button
              type="submit"
              className="rounded-xl bg-[#1D1B18] px-4 py-2 text-xs font-medium text-white"
            >
              Salva versione
            </button>
          </div>
        </form>

        {/* Derived Palette & Logo Integrity */}
        <div className="rounded-2xl border border-[#DDD4C4] bg-white p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium text-[#1D1B18]">
              Palette ricavata dai materiali autentici
            </h3>
            <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-[10px] text-emerald-800">
              {palette.status}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {Object.entries(palette.colors || {}).map(([k, c]) => (
              <div key={k} className="rounded-xl border border-[#EDE6D9] p-2.5 text-xs">
                <div
                  className="h-10 w-full rounded-lg border border-black/10"
                  style={{ backgroundColor: c.hex }}
                />
                <div className="mt-2 font-medium text-[#1D1B18]">{c.label}</div>
                <div className="font-mono text-[10px] text-[#6C6459]">{c.hex}</div>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-2 rounded-xl bg-[#FAF7F2] p-3 text-xs text-[#4A443C]">
            <ShieldCheck className="h-4 w-4 shrink-0 text-emerald-700" />
            <span>
              Logo vincolato all&apos;asset originale:{" "}
              <strong>{logo.original_filename || "Da caricare nel Media Vault"}</strong> · Payoff:{" "}
              <strong>{logo.payoff_exact}</strong>
            </span>
          </div>
        </div>
      </div>

      {/* Version History */}
      <div className="rounded-2xl border border-[#DDD4C4] bg-white p-5">
        <h3 className="text-sm font-medium text-[#1D1B18]">Versioni Brand Brain</h3>
        <div className="mt-3 space-y-2">
          {history.map((h) => (
            <div
              key={String(h.version)}
              className="flex items-center justify-between rounded-xl bg-[#FAF7F2] px-4 py-2 text-xs"
            >
              <div>
                <strong>v{String(h.version)}</strong> · {String(h.note || "Aggiornamento")} (
                {String(h.updated_by)})
              </div>
              <button
                type="button"
                onClick={async () => {
                  await koraApi.rollbackBrand(Number(h.version));
                  await load();
                }}
                className="flex items-center gap-1 rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1"
              >
                <RotateCcw className="h-3 w-3" /> Ripristina
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
