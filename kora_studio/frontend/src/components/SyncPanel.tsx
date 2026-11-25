"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, ShieldCheck, Wand2 } from "lucide-react";
import { koraApi, type SyncIssue, type TimelineRecord } from "@/lib/api";

type Props = { projectId: string };

const ALIGNMENT_LABEL: Record<string, string> = {
  asr: "Trascritta dalla voce (verificata)",
  forced_aligned: "Copione allineato ai tempi della voce",
  estimated: "Tempi stimati nelle pause misurate",
  manual: "Testo corretto a mano",
  none: "Nessuna trascrizione",
};

export default function SyncPanel({ projectId }: Props) {
  const [timeline, setTimeline] = useState<TimelineRecord | null>(null);
  const [issues, setIssues] = useState<SyncIssue[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "info" | "err"; text: string } | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<"asr" | "script" | "estimated">("asr");
  const [script, setScript] = useState("");
  const [captionSource, setCaptionSource] = useState<"transcript" | "editorial">("transcript");
  const [asr, setAsr] = useState<{ available: boolean; message: string } | null>(null);
  const [mismatches, setMismatches] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      const data = await koraApi.getTimeline(projectId);
      setTimeline(data.timeline);
      setIssues(data.issues || []);
      setDrafts({});
      if (!data.timeline) setMsg({ kind: "info", text: data.message || "Timeline non ancora costruita." });
    } catch (err) {
      setMsg({ kind: "err", text: err instanceof Error ? err.message : "Timeline non leggibile." });
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    koraApi
      .asrStatus()
      .then((s) => setAsr({ available: s.available, message: s.message }))
      .catch(() => setAsr(null));
  }, [load]);

  const rebuild = async () => {
    setBusy("rebuild");
    setMsg(null);
    try {
      const res = await koraApi.rebuildTimeline(projectId, {
        mode,
        // dichiarazione esplicita: la trascrizione si chiede, non si presume
        use_asr: mode === "asr",
        script_text: mode === "script" ? script.trim() : undefined,
        caption_source: captionSource,
      });
      setTimeline(res.timeline);
      setIssues(res.issues || []);
      setMismatches(res.mismatches || []);
      setMsg({
        kind: res.issues.some((i) => i.severity === "error") ? "err" : "ok",
        text: res.asr_note || "Timeline ricalcolata.",
      });
    } catch (err) {
      setMsg({ kind: "err", text: err instanceof Error ? err.message : "Ricalcolo non riuscito." });
    } finally {
      setBusy(null);
    }
  };

  const verify = async () => {
    setBusy("verify");
    setMsg(null);
    try {
      const res = await koraApi.verifySync(projectId);
      setIssues(res.issues || []);
      setMsg({
        kind: res.ok ? "ok" : "err",
        text: res.ok
          ? res.verified
            ? "Sincronizzazione verificata sulla trascrizione della voce."
            : "Nessun conflitto. Sincronizzazione dichiarata, non verificata dalla voce."
          : "Conflitti da risolvere prima di esportare.",
      });
    } catch (err) {
      setMsg({ kind: "err", text: err instanceof Error ? err.message : "Verifica non riuscita." });
    } finally {
      setBusy(null);
    }
  };

  const saveSegment = async (segmentUid: string) => {
    const text = drafts[segmentUid];
    if (text === undefined) return;
    setBusy(segmentUid);
    setMsg(null);
    try {
      const res = await koraApi.patchTranscript(projectId, [{ segment_uid: segmentUid, text }]);
      setIssues(res.issues || []);
      setTimeline((prev) =>
        prev
          ? { ...prev, transcript: res.transcript, caption_cues: res.caption_cues }
          : prev
      );
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[segmentUid];
        return next;
      });
      setMsg({ kind: "ok", text: "Testo aggiornato: l'audio non è stato toccato." });
    } catch (err) {
      setMsg({ kind: "err", text: err instanceof Error ? err.message : "Correzione non salvata." });
    } finally {
      setBusy(null);
    }
  };

  const alignment = timeline?.transcript.alignment || "none";
  const errors = issues.filter((i) => i.severity === "error");
  const warnings = issues.filter((i) => i.severity !== "error");

  return (
    <section className="rounded-2xl border border-[#EDE6D9] bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-[#2A2A28]">
            <ShieldCheck className="h-4 w-4" /> Trascrizione &amp; Sincronizzazione
          </h3>
          <p className="mt-1 text-[11px] text-[#6C6459]">
            Una sola timeline per voce, scene e sottotitoli: i tempi si calcolano qui, non in ogni stadio.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={rebuild}
            disabled={busy !== null}
            className="inline-flex items-center gap-1.5 rounded-full border border-[#DDD4C4] px-3 py-1.5 text-[11px] font-medium text-[#2A2A28] hover:bg-[#F6F1E7] disabled:opacity-50"
          >
            {busy === "rebuild" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}
            Ricalcola allineamento
          </button>
          <button
            type="button"
            onClick={verify}
            disabled={busy !== null}
            className="inline-flex items-center gap-1.5 rounded-full bg-[#1F2A24] px-3 py-1.5 text-[11px] font-medium text-white hover:bg-[#2A382F] disabled:opacity-50"
          >
            {busy === "verify" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            Verifica sincronizzazione
          </button>
        </div>
      </div>

      <div className="mt-4 grid gap-3 rounded-xl border border-[#EDE6D9] bg-[#FCFAF6] p-3 sm:grid-cols-2">
        <label className="text-[11px] text-[#6C6459]">
          <span className="mb-1 block uppercase tracking-wide text-[10px] text-[#8C8273]">Come ricavare i tempi</span>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as "asr" | "script" | "estimated")}
            className="w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-[11px] text-[#2A2A28]"
          >
            <option value="asr">Trascrivi automaticamente l&apos;audio (ASR)</option>
            <option value="script">Allinea un copione già disponibile (trascrive e confronta)</option>
            <option value="estimated">Tempi stimati dentro le pause</option>
          </select>
        </label>
        <label className="text-[11px] text-[#6C6459]">
          <span className="mb-1 block uppercase tracking-wide text-[10px] text-[#8C8273]">Testo dei sottotitoli</span>
          <select
            value={captionSource}
            onChange={(e) => setCaptionSource(e.target.value as "transcript" | "editorial")}
            className="w-full rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-[11px] text-[#2A2A28]"
          >
            <option value="transcript">Fedeli alla trascrizione approvata</option>
            <option value="editorial">Editoriali sintetici (non letterali)</option>
          </select>
        </label>
        {mode === "script" && (
          <label className="sm:col-span-2 text-[11px] text-[#6C6459]">
            <span className="mb-1 block uppercase tracking-wide text-[10px] text-[#8C8273]">Copione pronunciato nel file</span>
            <textarea
              value={script}
              onChange={(e) => setScript(e.target.value)}
              rows={3}
              placeholder="Incolla qui il testo esatto della registrazione…"
              className="w-full resize-y rounded-lg border border-[#DDD4C4] bg-white px-2.5 py-1.5 text-[11px] text-[#2A2A28]"
            />
          </label>
        )}
        {asr && !asr.available && mode === "asr" && (
          <p className="sm:col-span-2 rounded-lg bg-[#FBEDED] px-3 py-2 text-[11px] text-[#8A2B2B]">
            Nessun motore ASR installato: usa «Allinea un copione» o «Tempi stimati», oppure installa il
            motore con <code className="font-mono">./installa_asr.sh</code>.
          </p>
        )}
      </div>

      {msg && (
        <p
          className={`mt-3 rounded-lg px-3 py-2 text-[11px] ${
            msg.kind === "ok"
              ? "bg-[#EEF5EE] text-[#2D5232]"
              : msg.kind === "err"
                ? "bg-[#FBEDED] text-[#8A2B2B]"
                : "bg-[#F6F1E7] text-[#6C6459]"
          }`}
        >
          {msg.text}
        </p>
      )}

      {mismatches.length > 0 && (
        <div className="mt-3 rounded-lg bg-[#FBF4E7] px-3 py-2 text-[11px] text-[#7A5A20]">
          <p className="font-medium">
            Parole del copione non riconosciute nella registrazione ({mismatches.length})
          </p>
          <p className="mt-1 break-words">
            {mismatches.slice(0, 24).join(" · ")}
            {mismatches.length > 24 ? " …" : ""}
          </p>
          <p className="mt-1 text-[10px] text-[#8C8273]">
            I loro tempi sono stimati dentro il parlato, non riconosciuti: correggi il copione o il testo.
          </p>
        </div>
      )}

      {timeline ? (
        <>
          <div className="mt-4 grid gap-2 sm:grid-cols-4">
            {[
              ["Allineamento", ALIGNMENT_LABEL[alignment] || alignment],
              ["Parole", String(timeline.transcript.words.length)],
              ["Sottotitoli", String(timeline.caption_cues.length)],
              [
                "Voce",
                `${timeline.audio.start_offset_sec.toFixed(2)}s → ${timeline.audio.end_sec.toFixed(2)}s`,
              ],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl border border-[#EDE6D9] bg-[#FCFAF6] px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-[#8C8273]">{label}</div>
                <div className="text-[12px] font-medium text-[#2A2A28]">{value}</div>
              </div>
            ))}
          </div>

          {asr && !asr.available && (
            <p className="mt-3 flex items-start gap-2 rounded-lg bg-[#F6F1E7] px-3 py-2 text-[11px] text-[#6C6459]">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {asr.message}
            </p>
          )}

          {(errors.length > 0 || warnings.length > 0) && (
            <ul className="mt-3 space-y-1.5">
              {[...errors, ...warnings].map((issue, index) => (
                <li
                  key={`${issue.code}-${index}`}
                  className={`flex items-start gap-2 rounded-lg px-3 py-2 text-[11px] ${
                    issue.severity === "error" ? "bg-[#FBEDED] text-[#8A2B2B]" : "bg-[#F6F1E7] text-[#6C6459]"
                  }`}
                >
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{issue.message}</span>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-4 max-h-72 overflow-auto rounded-xl border border-[#EDE6D9]">
            <table className="w-full text-left text-[11px]">
              <thead className="sticky top-0 bg-[#FCFAF6] text-[10px] uppercase tracking-wide text-[#8C8273]">
                <tr>
                  <th className="px-3 py-2">Tempo</th>
                  <th className="px-3 py-2">Scena</th>
                  <th className="px-3 py-2">Frase</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {timeline.transcript.segments.map((seg) => {
                  const draft = drafts[seg.segment_uid];
                  return (
                    <tr key={seg.segment_uid} className="border-t border-[#F1EBE0] align-top">
                      <td className="whitespace-nowrap px-3 py-2 text-[#6C6459]">
                        {seg.start_sec.toFixed(2)}–{seg.end_sec.toFixed(2)}s
                      </td>
                      <td className="px-3 py-2 text-[#6C6459]">{seg.scene_number ?? "—"}</td>
                      <td className="px-3 py-2">
                        <textarea
                          value={draft ?? seg.text}
                          onChange={(e) =>
                            setDrafts((prev) => ({ ...prev, [seg.segment_uid]: e.target.value }))
                          }
                          rows={2}
                          className="w-full resize-y rounded-md border border-[#EDE6D9] bg-white px-2 py-1 text-[11px] text-[#2A2A28] focus:border-[#B9A98C] focus:outline-none"
                        />
                      </td>
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => saveSegment(seg.segment_uid)}
                          disabled={draft === undefined || busy !== null}
                          className="inline-flex items-center gap-1 rounded-full border border-[#DDD4C4] px-2.5 py-1 text-[10px] text-[#2A2A28] hover:bg-[#F6F1E7] disabled:opacity-40"
                        >
                          {busy === seg.segment_uid ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <CheckCircle2 className="h-3 w-3" />
                          )}
                          Salva
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[10px] text-[#8C8273]">
            Correggere il testo non tocca l&apos;audio: cambiano solo parole, sottotitoli e cue.
          </p>
        </>
      ) : (
        <p className="mt-4 rounded-xl bg-[#FCFAF6] px-3 py-3 text-[11px] text-[#6C6459]">
          Nessuna timeline: importa o genera il voiceover, poi premi «Ricalcola allineamento».
        </p>
      )}
    </section>
  );
}
