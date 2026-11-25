"""AudioTimelineManager (3.6.6) — una sola timeline canonica per voce, scene e sottotitoli.

Il difetto della 3.6.5: ogni stadio ricostruiva i propri tempi. Le scene
venivano divise in proporzione alla durata dell'MP3, i sottotitoli nascevano dal
testo vecchio dello storyboard, le caption dividevano la scena in parti uguali.
Qui i tempi si costruiscono **una volta sola** e tutti leggono lo stesso file
``project_timeline.json``.

Onestà sui dati: senza un motore ASR le parole non possono essere "riconosciute".
In quel caso la timeline è marcata ``alignment="estimated"``: le parole sono
distribuite **dentro le regioni di parlato realmente misurate** (non su tutta la
scena), e i sottotitoli sono dichiarati non verificati.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from kora_studio.media_tools import find_ffmpeg, probe_media

TIMELINE_FILENAME = "project_timeline.json"
TRANSCRIPT_FILENAME = "transcript.json"
SCHEMA_VERSION = 1

SENTENCE_SPLIT = re.compile(r"(?<=[\.\!\?…])\s+")
WORD_SPLIT = re.compile(r"\s+")


class VoiceTruncationError(RuntimeError):
    """La voce finirebbe oltre la fine del Reel: la prima o l'ultima parola andrebbe persa."""


@dataclass
class SpeechRegion:
    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


def _round(value: float) -> float:
    return round(float(value), 3)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def media_duration(path: Path) -> float:
    try:
        probe = probe_media(path)
        return float(probe["format"].get("duration", 0.0) or 0.0)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# Regioni di parlato (energia reale, nessun modello)
# --------------------------------------------------------------------------- #
def speech_regions(
    audio_path: Path,
    *,
    min_silence_sec: float = 0.35,
    threshold_db: int = -35,
    duration_sec: Optional[float] = None,
) -> list[SpeechRegion]:
    """Regioni in cui c'è voce, misurate con ``silencedetect`` di FFmpeg.

    È una misura di energia, non un riconoscimento: dice *quando* si parla,
    non *cosa* si dice.
    """
    total = float(duration_sec if duration_sec is not None else media_duration(audio_path))
    ffmpeg = find_ffmpeg()
    if not ffmpeg or total <= 0:
        return [SpeechRegion(0.0, max(0.0, total))]
    cmd = [
        ffmpeg, "-hide_banner", "-nostats", "-i", str(audio_path),
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence_sec}",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        log = (proc.stderr or "") + (proc.stdout or "")
    except Exception:
        return [SpeechRegion(0.0, total)]

    silences: list[tuple[float, float]] = []
    start: Optional[float] = None
    for line in log.splitlines():
        m_start = re.search(r"silence_start:\s*(-?[0-9.]+)", line)
        if m_start:
            start = max(0.0, float(m_start.group(1)))
            continue
        m_end = re.search(r"silence_end:\s*(-?[0-9.]+)", line)
        if m_end and start is not None:
            silences.append((start, max(start, float(m_end.group(1)))))
            start = None
    if start is not None:
        silences.append((start, total))

    regions: list[SpeechRegion] = []
    cursor = 0.0
    for s_start, s_end in silences:
        if s_start > cursor + 0.05:
            regions.append(SpeechRegion(cursor, min(s_start, total)))
        cursor = max(cursor, s_end)
    if cursor < total - 0.05:
        regions.append(SpeechRegion(cursor, total))
    if not regions:
        return [SpeechRegion(0.0, total)]
    return regions


# --------------------------------------------------------------------------- #
# Parole e segmenti
# --------------------------------------------------------------------------- #
def frames(sec: float, fps: int) -> int:
    return int(round(float(sec) * max(1, int(fps))))


def _word_slots(words: list[str], regions: list[SpeechRegion], total: float) -> list[tuple[str, float, float]]:
    """Distribuisce le parole solo dentro le regioni di parlato, in proporzione ai caratteri."""
    tokens = [w for w in words if w]
    if not tokens:
        return []
    if not regions:
        regions = [SpeechRegion(0.0, total)]
    speech_total = sum(r.duration_sec for r in regions) or total or 1.0
    weights = [max(1, len(w)) for w in tokens]
    weight_total = sum(weights)
    out: list[tuple[str, float, float]] = []
    consumed = 0.0
    for word, weight in zip(tokens, weights):
        start_off = speech_total * (consumed / weight_total)
        consumed += weight
        end_off = speech_total * (consumed / weight_total)
        # mappa lo scostamento "tempo di parlato" su tempo assoluto saltando i silenzi
        s_abs = _offset_to_abs(start_off, regions)
        e_abs = _offset_to_abs(end_off, regions)
        # Una parola non attraversa un silenzio: la sua fine resta nella regione
        # in cui comincia, altrimenti il centro cadrebbe nel vuoto.
        owner = _region_of(s_abs, regions)
        if owner is not None and e_abs > owner.end_sec:
            e_abs = owner.end_sec
        if e_abs <= s_abs:
            e_abs = min(total or s_abs + 0.08, s_abs + 0.08)
        out.append((word, _round(s_abs), _round(e_abs)))
    return out


def _offset_to_abs(offset: float, regions: list[SpeechRegion]) -> float:
    remaining = max(0.0, offset)
    for region in regions:
        if remaining <= region.duration_sec:
            return region.start_sec + remaining
        remaining -= region.duration_sec
    return regions[-1].end_sec if regions else remaining


def _region_of(instant: float, regions: list[SpeechRegion]) -> Optional[SpeechRegion]:
    for region in regions:
        if region.start_sec - 1e-6 <= instant <= region.end_sec + 1e-6:
            return region
    return None



def _shift_words(words: list[dict[str, Any]], offset_sec: float, fps: int) -> list[dict[str, Any]]:
    """Applica lo stesso offset dell'audio a ogni parola: nessuna anticipazione."""
    if not offset_sec:
        return words
    shifted: list[dict[str, Any]] = []
    for word in words:
        start = float(word["start_sec"]) + offset_sec
        end = float(word["end_sec"]) + offset_sec
        shifted.append(
            {
                **word,
                "start_sec": _round(start),
                "end_sec": _round(end),
                "start_frame": frames(start, fps),
                "end_frame": max(frames(start, fps) + 1, frames(end, fps)),
            }
        )
    return shifted


def estimate_words(text: str, regions: list[SpeechRegion], fps: int, total: float) -> list[dict[str, Any]]:
    slots = _word_slots(WORD_SPLIT.split(text.strip()), regions, total)
    return [
        {
            "word": word,
            "start_sec": start,
            "end_sec": end,
            "start_frame": frames(start, fps),
            "end_frame": max(frames(start, fps) + 1, frames(end, fps)),
            "confidence": None,
            "speaker_id": None,
            "timing_source": "estimated",
        }
        for word, start, end in slots
    ]


def words_from_asr(asr: dict[str, Any], fps: int) -> list[dict[str, Any]]:
    """Parole lette da un risultato ASR **conservandone la provenienza**.

    3.6.8 — se la parola arriva da un allineamento forzato del copione
    (``timing_source="forced_aligned"``) non viene promossa a parola
    riconosciuta: la fonte dichiarata dal chiamante resta quella.
    """
    words: list[dict[str, Any]] = []
    for seg in asr.get("segments") or []:
        for word in seg.get("words") or []:
            start = float(word.get("start_sec") or seg.get("start_sec") or 0.0)
            end = float(word.get("end_sec") or start + 0.08)
            source = str(word.get("timing_source") or "asr")
            words.append(
                {
                    "word": str(word.get("word", "")).strip(),
                    "start_sec": _round(start),
                    "end_sec": _round(max(end, start + 0.02)),
                    "start_frame": frames(start, fps),
                    "end_frame": max(frames(start, fps) + 1, frames(max(end, start + 0.02), fps)),
                    "confidence": word.get("confidence"),
                    "speaker_id": word.get("speaker_id"),
                    "timing_source": source,
                    "recognized": source == "asr",
                }
            )
    return [w for w in words if w["word"]]


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENTENCE_SPLIT.split(text.strip()) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def build_segments(
    words: list[dict[str, Any]],
    *,
    fps: int,
    max_words: int = 16,
    pause_sec: float = 0.45,
) -> list[dict[str, Any]]:
    """Raggruppa le parole in frasi usando le pause reali e la punteggiatura."""
    segments: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        text = " ".join(w["word"] for w in current).strip()
        start = current[0]["start_sec"]
        end = current[-1]["end_sec"]
        segments.append(
            {
                "segment_uid": f"seg_{len(segments) + 1:03d}",
                "text": text,
                "start_sec": start,
                "end_sec": end,
                "start_frame": frames(start, fps),
                "end_frame": max(frames(start, fps) + 1, frames(end, fps)),
                "word_ids": [str(w.get("word_id")) for w in current if w.get("word_id")],
                "scene_uid": None,
                "review_status": "pending",
            }
        )
        current.clear()

    for index, word in enumerate(words):
        current.append(word)
        nxt = words[index + 1] if index + 1 < len(words) else None
        ends_sentence = bool(re.search(r"[\.\!\?…]$", word["word"]))
        long_pause = bool(nxt and float(nxt["start_sec"]) - float(word["end_sec"]) >= pause_sec)
        if ends_sentence or long_pause or len(current) >= max_words or nxt is None:
            flush()
    return segments


def build_cues(
    words: list[dict[str, Any]],
    *,
    fps: int,
    max_words: int = 7,
    max_duration_sec: float = 2.8,
    pause_sec: float = 0.35,
) -> list[dict[str, Any]]:
    """Cue dei sottotitoli derivati dai tempi reali delle parole (o stimati, dichiarati)."""
    cues: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []

    def flush() -> None:
        if not group:
            return
        start = float(group[0]["start_sec"])
        end = float(group[-1]["end_sec"])
        cues.append(
            {
                "cue_uid": f"cue_{len(cues) + 1:03d}",
                "text": " ".join(w["word"] for w in group).strip(),
                "start_sec": _round(start),
                "end_sec": _round(end),
                "start_frame": frames(start, fps),
                "end_frame": max(frames(start, fps) + 1, frames(end, fps)),
                "timing_source": group[0].get("timing_source", "estimated"),
                "words": [
                    {"word": w["word"], "start_sec": w["start_sec"], "end_sec": w["end_sec"]}
                    for w in group
                ],
            }
        )
        group.clear()

    for index, word in enumerate(words):
        if not group:
            group.append(word)
        else:
            candidate_end = float(word["end_sec"])
            group_start = float(group[0]["start_sec"])
            too_long = candidate_end - group_start > max_duration_sec or len(group) >= max_words
            gap = float(word["start_sec"]) - float(group[-1]["end_sec"])
            if too_long or gap >= pause_sec:
                flush()
            group.append(word)
        if re.search(r"[\.\!\?…]$", word["word"]):
            flush()
    flush()
    return cues




def build_cues_per_scene(
    words: list[dict[str, Any]],
    scene_windows: list[dict[str, Any]],
    *,
    fps: int,
    max_words: int = 7,
    max_duration_sec: float = 2.8,
) -> list[dict[str, Any]]:
    """Cue costruiti **dentro** ogni scena dai tempi delle sue parole.

    Così una frase lunga che attraversa un taglio non viene schiacciata in una
    scena sola: le parole vanno dove vengono pronunciate e ogni cue resta nella
    propria finestra, senza accorciare il testo.
    """
    if not scene_windows:
        return build_cues(words, fps=fps, max_words=max_words, max_duration_sec=max_duration_sec)
    ordered = sorted(scene_windows, key=lambda w: float(w["start_sec"]))
    buckets: dict[str, list[dict[str, Any]]] = {str(w.get("scene_uid")): [] for w in ordered}
    for word in words:
        mid = (float(word["start_sec"]) + float(word["end_sec"])) / 2
        owner = next(
            (w for w in ordered if float(w["start_sec"]) <= mid < float(w["end_sec"])), None
        )
        if owner is None:
            owner = ordered[-1] if mid >= float(ordered[-1]["end_sec"]) else ordered[0]
        buckets[str(owner.get("scene_uid"))].append(word)

    cues: list[dict[str, Any]] = []
    for window in ordered:
        subset = buckets.get(str(window.get("scene_uid"))) or []
        if not subset:
            continue
        scene_cues = build_cues(
            subset, fps=fps, max_words=max_words, max_duration_sec=max_duration_sec, pause_sec=0.3
        )
        for cue in scene_cues:
            start = max(float(cue["start_sec"]), float(window["start_sec"]))
            end = min(float(cue["end_sec"]), float(window["end_sec"]))
            if end - start < 0.08:
                # una parola a cavallo del taglio: la si tiene vicino al suo tempo,
                # non si sposta all'inizio della scena
                start = max(float(window["start_sec"]), min(float(cue["start_sec"]), float(window["end_sec"]) - 0.12))
                end = min(float(window["end_sec"]), start + 0.5)
            cue["start_sec"] = _round(start)
            cue["end_sec"] = _round(max(end, start + 0.12))
            cue["start_frame"] = frames(cue["start_sec"], fps)
            cue["end_frame"] = max(frames(cue["start_sec"], fps) + 1, frames(cue["end_sec"], fps))
            cue["scene_uid"] = window.get("scene_uid")
            cue["scene_number"] = window.get("scene_number")
            cues.append(cue)
    cues.sort(key=lambda c: float(c["start_sec"]))
    for index, cue in enumerate(cues, start=1):
        cue["cue_uid"] = f"cue_{index:03d}"
    return cues



def resolve_cue_overlaps(cues: list[dict[str, Any]], fps: int) -> list[dict[str, Any]]:
    """Due sottotitoli non possono stare sullo schermo nello stesso istante."""
    ordered = sorted(cues, key=lambda c: float(c["start_sec"]))
    out: list[dict[str, Any]] = []
    for cue in ordered:
        if out and float(cue["start_sec"]) < float(out[-1]["end_sec"]) - 1e-6:
            prev = out[-1]
            if str(prev.get("scene_uid")) != str(cue.get("scene_uid")):
                # scene diverse ma sovrapposte: si taglia sull'inizio della nuova
                prev["end_sec"] = _round(min(float(prev["end_sec"]), float(cue["start_sec"])))
                prev["end_frame"] = max(int(prev["start_frame"]) + 1, frames(prev["end_sec"], fps))
                out.append(cue)
                continue
            gap = float(cue["start_sec"]) - float(prev["start_sec"])
            if gap >= 0.24:
                prev["end_sec"] = _round(float(cue["start_sec"]))
                prev["end_frame"] = max(int(prev["start_frame"]) + 1, frames(prev["end_sec"], fps))
                out.append(cue)
            else:  # troppo vicini: si unisce il testo
                prev["text"] = f"{prev['text']} {cue['text']}".strip()
                prev["end_sec"] = _round(max(float(prev["end_sec"]), float(cue["end_sec"])))
                prev["end_frame"] = max(int(prev["start_frame"]) + 1, frames(prev["end_sec"], fps))
            continue
        out.append(cue)
    for index, cue in enumerate(out, start=1):
        cue["cue_uid"] = f"cue_{index:03d}"
    return out


def clip_cues_to_scenes(
    cues: list[dict[str, Any]],
    scene_windows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ritaglia ogni cue dentro la scena a cui appartiene.

    Il parlato non conosce i tagli immagine: una frase può attraversare il
    confine tra due scene. Il sottotitolo però è un overlay di scena, quindi
    deve stare dentro la sua finestra, altrimenti comparirebbe sulla scena
    sbagliata (o fuori da ogni scena).
    """
    if not scene_windows:
        return cues
    out: list[dict[str, Any]] = []
    for cue in cues:
        win = next((w for w in scene_windows if w.get("scene_uid") == cue.get("scene_uid")), None)
        if win is None:
            out.append(cue)
            continue
        start = max(float(cue["start_sec"]), float(win["start_sec"]))
        end = min(float(cue["end_sec"]), float(win["end_sec"]))
        if end - start < 0.12:
            start = max(float(win["start_sec"]), min(float(cue["start_sec"]), float(win["end_sec"]) - 0.3))
            end = min(float(win["end_sec"]), start + max(0.5, min(1.2, float(win["end_sec"]) - start)))
        if end - start < 0.12:
            out.append(cue)
            continue
        clipped = (
            abs(start - float(cue["start_sec"])) > 1e-6 or abs(end - float(cue["end_sec"])) > 1e-6
        )
        out.append(
            {
                **cue,
                "start_sec": _round(start),
                "end_sec": _round(end),
                "start_frame": int(cue.get("start_frame") or 0)
                if not clipped
                else frames(start, int(cue.get("fps") or 25)),
                "end_frame": int(cue.get("end_frame") or 0)
                if not clipped
                else frames(end, int(cue.get("fps") or 25)),
                "clipped_to_scene": clipped,
            }
        )
    for index, cue in enumerate(out, start=1):
        cue["cue_uid"] = f"cue_{index:03d}"
    return out


def assign_scenes(
    segments: list[dict[str, Any]],
    scene_windows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Associa ogni frase alla scena che contiene il suo punto centrale."""
    for seg in segments:
        mid = (float(seg["start_sec"]) + float(seg["end_sec"])) / 2
        chosen = None
        for win in scene_windows:
            if float(win["start_sec"]) <= mid < float(win["end_sec"]):
                chosen = win
                break
        if chosen is None and scene_windows:
            chosen = scene_windows[-1] if mid >= float(scene_windows[-1]["end_sec"]) else scene_windows[0]
        seg["scene_uid"] = chosen.get("scene_uid") if chosen else None
        seg["scene_number"] = chosen.get("scene_number") if chosen else None
    return segments




def plan_scene_boundaries(
    regions: list[SpeechRegion],
    scene_count: int,
    duration_sec: float,
    fps: int,
) -> list[int]:
    """Confini di scena (in frame) posati sulle pause realmente misurate.

    Il parlato comanda i tagli: se ci sono abbastanza pause, ogni confine cade
    nel punto medio di un silenzio; quando le pause sono poche, le rimanenti
    nascono dividendo gli intervalli più lunghi. La copertura resta continua.
    """
    total_frames = max(1, frames(duration_sec, fps))
    needed = max(0, int(scene_count) - 1)
    if needed == 0:
        return []
    gaps = [
        (regions[i].end_sec, regions[i + 1].start_sec)
        for i in range(len(regions) - 1)
        if regions[i + 1].start_sec - regions[i].end_sec >= 0.15
    ]
    candidates = sorted((a + b) / 2 for a, b in gaps if 0.05 < (a + b) / 2 < duration_sec - 0.05)
    if len(candidates) > needed:
        step = len(candidates) / needed
        candidates = [candidates[min(len(candidates) - 1, int(i * step))] for i in range(needed)]
    boundaries = list(dict.fromkeys(candidates))
    while len(boundaries) < needed:
        edges = [0.0, *boundaries, duration_sec]
        widest_index = max(
            range(len(edges) - 1), key=lambda i: edges[i + 1] - edges[i]
        )
        boundaries.append((edges[widest_index] + edges[widest_index + 1]) / 2)
        boundaries.sort()
    result: list[int] = []
    for value in sorted(boundaries):
        frame = min(total_frames - 1, max(1, frames(value, fps)))
        if result and frame <= result[-1]:
            frame = result[-1] + 1
        if frame >= total_frames:
            continue
        result.append(frame)
    # invariante: servono esattamente `needed` confini, strettamente crescenti
    while len(result) < needed:
        edges = [0, *result, total_frames]
        widest = max(range(len(edges) - 1), key=lambda i: edges[i + 1] - edges[i])
        candidate = (edges[widest] + edges[widest + 1]) // 2
        if candidate <= edges[widest] or candidate >= edges[widest + 1]:
            break
        result.append(candidate)
        result.sort()
    return result[:needed]



def align_script_to_asr(
    script_text: str,
    asr_words: list[dict[str, Any]],
    *,
    fps: int,
) -> dict[str, Any]:
    """Forced alignment leggero: il copione noto viene mappato sui tempi ASR.

    Non è un allineatore acustico (tipo MFA): usa la sequenza delle parole
    riconosciute per dare a ogni parola del copione l'intervallo in cui è stata
    pronunciata. Le parole del copione che l'ASR non ha sentito restano senza
    tempo e vengono dichiarate come discordanze, mai inventate.
    """
    script_tokens = [w for w in WORD_SPLIT.split((script_text or "").strip()) if w]
    if not script_tokens or not asr_words:
        return {"words": [], "mismatches": [], "matched": 0}
    spoken = [str(w.get("word", "")).strip() for w in asr_words]
    matcher = difflib.SequenceMatcher(
        None, [_norm_token(t) for t in script_tokens], [_norm_token(t) for t in spoken]
    )
    out: list[dict[str, Any]] = []
    mismatches: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                src = asr_words[j1 + offset]
                out.append(
                    {
                        "word": script_tokens[i1 + offset],
                        "start_sec": float(src["start_sec"]),
                        "end_sec": float(src["end_sec"]),
                        "start_frame": int(src.get("start_frame") or frames(src["start_sec"], fps)),
                        "end_frame": int(src.get("end_frame") or frames(src["end_sec"], fps)),
                        "confidence": src.get("confidence"),
                        "speaker_id": src.get("speaker_id"),
                        "timing_source": "forced_aligned",
                    }
                )
        elif tag == "replace":
            span = asr_words[j1:j2] or [asr_words[min(len(asr_words) - 1, j1)]]
            start = float(span[0]["start_sec"])
            end = float(span[-1]["end_sec"])
            step = max(0.02, (end - start) / max(1, i2 - i1))
            for offset, token in enumerate(script_tokens[i1:i2]):
                w_start = start + offset * step
                out.append(
                    {
                        "word": token,
                        "start_sec": _round(w_start),
                        "end_sec": _round(min(end, w_start + step)),
                        "start_frame": frames(w_start, fps),
                        "end_frame": max(frames(w_start, fps) + 1, frames(min(end, w_start + step), fps)),
                        "confidence": None,
                        "speaker_id": None,
                        "timing_source": "forced_aligned",
                    }
                )
                if _norm_token(token) not in {_norm_token(x) for x in spoken[j1:j2]}:
                    mismatches.append(token)
        elif tag == "delete":
            for token in script_tokens[i1:i2]:
                mismatches.append(token)
    return {"words": out, "mismatches": mismatches, "matched": len(out)}


def _norm_token(token: str) -> str:
    return re.sub(r"[^\wàèéìòù']+", "", str(token).lower())


def caption_timings_from_transcript(
    timeline: dict[str, Any],
    scenes: list[Any],
) -> dict[int, list[dict[str, Any]]]:
    """Tempi parola per parola presi **dai segmenti della trascrizione**, non dall'indice.

    3.6.8 — una scena può contenere **più frasi**: si raccolgono tutti i segmenti
    associati alla scena (in ordine temporale) e ogni parola conserva il tempo del
    proprio segmento. Una frase che attraversa due scene viene divisa per parola
    sulla scena in cui viene pronunciata. Le scene senza parlato non producono
    sottotitoli e non inventano tempi.

    Se il testo del sottotitolo coincide con i segmenti (modalità «fedele alla
    trascrizione») la corrispondenza è 1:1 e verificabile (``mapping="exact"``).
    Se diverge, il sottotitolo è editoriale: resta ancorato ai tempi reali delle
    parole che copre, ma viene dichiarato ``editorial_in_segment``.
    """
    transcript = timeline.get("transcript") or {}
    segments = transcript.get("segments") or []
    words = transcript.get("words") or []
    by_id = {str(w.get("word_id")): w for w in words if w.get("word_id")}
    windows: list[tuple[int, float, float]] = []
    for sc in scenes:
        try:
            windows.append(
                (
                    int(getattr(sc, "scene_number", 0) or 0),
                    float(getattr(sc, "start_sec", 0.0) or 0.0),
                    float(getattr(sc, "end_sec", 0.0) or 0.0),
                )
            )
        except (TypeError, ValueError):
            continue

    def owner_of(mid: float) -> Optional[int]:
        for number, start_sec, end_sec in windows:
            if end_sec > start_sec and start_sec - 0.02 <= mid < end_sec + 0.02:
                return number
        return None

    def words_of(seg: dict[str, Any]) -> list[dict[str, Any]]:
        picked = [by_id[str(wid)] for wid in (seg.get("word_ids") or []) if str(wid) in by_id]
        if not picked:
            seg_start = float(seg.get("start_sec") or 0.0)
            seg_end = float(seg.get("end_sec") or 0.0)
            picked = [
                w
                for w in words
                if seg_start <= (float(w["start_sec"]) + float(w["end_sec"])) / 2 <= seg_end
            ]
        return sorted(picked, key=lambda w: float(w.get("start_sec") or 0.0))

    out: dict[int, list[dict[str, Any]]] = {}
    for scene in scenes:
        number = int(getattr(scene, "scene_number", 0) or 0)
        caption = (getattr(scene, "subtitle_text", "") or "").strip()
        if not caption:
            continue
        own = sorted(
            [s for s in segments if int(s.get("scene_number") or 0) == number],
            key=lambda s: float(s.get("start_sec") or 0.0),
        )
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for seg in own:
            for word in words_of(seg):
                key = str(word.get("word_id") or f"{word.get('word')}@{word.get('start_sec')}")
                if key in seen:
                    continue
                mid = (float(word["start_sec"]) + float(word["end_sec"])) / 2
                place = owner_of(mid)
                if place is not None and place != number:
                    # frase distribuita su più scene: questa scena prende solo le sue parole
                    continue
                seen.add(key)
                selected.append(word)
        if not selected and not own:
            # frase distribuita su più scene: la scena prende le parole che cadono
            # nella propria finestra, anche se il segmento è assegnato altrove
            for word in sorted(words, key=lambda w: float(w.get("start_sec") or 0.0)):
                key = str(word.get("word_id") or f"{word.get('word')}@{word.get('start_sec')}")
                if key in seen:
                    continue
                mid = (float(word["start_sec"]) + float(word["end_sec"])) / 2
                if owner_of(mid) != number:
                    continue
                seen.add(key)
                selected.append(word)
        if not selected:
            continue
        combined = _norm_text(
            " ".join(str(s.get("text") or "") for s in own)
            or " ".join(str(w.get("word") or "") for w in selected)
        )
        caption_norm = _norm_text(caption)
        tokens = caption.split()
        exact = bool(combined) and combined == caption_norm and len(selected) == len(tokens)
        editorial = caption_norm != combined
        planned: list[dict[str, Any]] = []
        for index in range(len(tokens)):
            if exact:
                word = selected[index]
                source = str(word.get("timing_source") or "estimated")
                mapping = "exact"
            else:
                span = len(selected)
                mapped = (
                    index
                    if span == len(tokens)
                    else int(round(index * (span - 1) / max(1, len(tokens) - 1)))
                )
                word = selected[max(0, min(span - 1, mapped))]
                if editorial:
                    source = "editorial_in_segment"
                    mapping = "editorial"
                else:
                    source = str(word.get("timing_source") or "estimated")
                    mapping = "proportional"
            planned.append(
                {
                    "start_sec": float(word["start_sec"]),
                    "end_sec": float(word["end_sec"]),
                    "word_index": index,
                    "timing_source": source,
                    "mapping": mapping,
                }
            )
        out[number] = planned
    return out


def _norm_text(text: str) -> str:
    return " ".join(_norm_token(t) for t in WORD_SPLIT.split(text.strip()) if t)


def rebuild_words_from_segments(
    timeline: dict[str, Any],
    *,
    fps: Optional[int] = None,
    segments: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Ricalcola parole e cue dopo una correzione manuale del testo, senza toccare l'audio.

    I tempi dei segmenti restano quelli misurati: cambia solo la distribuzione
    delle parole dentro ciascun segmento. La trascrizione viene marcata
    ``alignment="manual"``: corretta da una persona, non certificata da un ASR.
    """
    rate = int(fps or timeline.get("fps") or 25)
    segs = [dict(s) for s in (segments or (timeline.get("transcript") or {}).get("segments") or [])]
    segs.sort(key=lambda s: float(s.get("start_sec") or 0.0))
    words: list[dict[str, Any]] = []
    for seg in segs:
        tokens = str(seg.get("text") or "").split()
        if not tokens:
            continue
        start = float(seg.get("start_sec") or 0.0)
        end = max(start + 0.05, float(seg.get("end_sec") or start + 0.05))
        step = (end - start) / len(tokens)
        seg_word_ids: list[str] = []
        for index, token in enumerate(tokens):
            w_start = start + index * step
            w_end = start + (index + 1) * step
            word_id = f"w_{len(words) + 1:04d}"
            seg_word_ids.append(word_id)
            words.append(
                {
                    "word_id": word_id,
                    "word": token,
                    "start_sec": _round(w_start),
                    "end_sec": _round(w_end),
                    "start_frame": frames(w_start, rate),
                    "end_frame": max(frames(w_start, rate) + 1, frames(w_end, rate)),
                    "confidence": None,
                    "speaker_id": seg.get("speaker_id"),
                    "timing_source": "manual",
                }
            )
        seg["word_ids"] = seg_word_ids
        seg.setdefault("review_status", "pending")
    transcript = timeline.setdefault("transcript", {})
    transcript["segments"] = segs
    transcript["words"] = words
    transcript["text"] = " ".join(str(s.get("text") or "") for s in segs).strip()
    transcript["alignment"] = "manual"
    transcript["verified"] = False
    timeline["caption_cues"] = build_cues(words, fps=rate)
    windows = timeline.get("scenes") or []
    if windows:
        assign_scenes(segs, windows)
        for cue in timeline["caption_cues"]:
            mid = (float(cue["start_sec"]) + float(cue["end_sec"])) / 2
            owner = next(
                (w for w in windows if float(w["start_sec"]) <= mid < float(w["end_sec"])), None
            )
            cue["scene_uid"] = owner.get("scene_uid") if owner else None
            cue["scene_number"] = owner.get("scene_number") if owner else None
            cue["fps"] = rate
        if windows:
            timeline["caption_cues"] = build_cues_per_scene(words, windows, fps=rate)
        timeline["caption_cues"] = clip_cues_to_scenes(timeline["caption_cues"], windows)
        timeline["caption_cues"] = resolve_cue_overlaps(timeline["caption_cues"], rate)
        for cue in timeline["caption_cues"]:
            cue.pop("fps", None)
    timeline["generated_at"] = datetime.now(timezone.utc).isoformat()
    return timeline


# --------------------------------------------------------------------------- #
# Manager
# --------------------------------------------------------------------------- #
class AudioTimelineManager:
    """Costruisce, valida e persiste la timeline canonica del progetto."""

    def __init__(self, fps: int = 25, project_dir: Optional[Path] = None) -> None:
        self.fps = max(1, int(fps))
        self.project_dir = project_dir

    # -- costruzione ------------------------------------------------------- #
    def build(
        self,
        *,
        audio_path: Path,
        text: Optional[str] = None,
        source: str = "upload",
        is_scene_aligned: bool = False,
        start_offset_sec: Optional[float] = None,
        end_padding_sec: float = 0.0,
        scenes: Optional[list[Any]] = None,
        asr_result: Optional[dict[str, Any]] = None,
        language: str = "it",
        audio_duration_sec: Optional[float] = None,
    ) -> dict[str, Any]:
        duration = float(audio_duration_sec or media_duration(audio_path))
        regions = speech_regions(audio_path, duration_sec=duration)
        speech_span = sum(r.duration_sec for r in regions) or duration

        offset = float(
            start_offset_sec
            if start_offset_sec is not None
            else (0.0 if is_scene_aligned else 0.0)
        )
        effective = round(max(0.0, duration - float(end_padding_sec or 0.0)), 3)

        # 3.6.8 — tre stati separati: allineamento dei tempi, corrispondenza del
        # testo, grado di verifica. Una parola del copione appoggiata ai tempi ASR
        # non è una parola riconosciuta dal parlato.
        mismatches: list[str] = []
        text_status = "none"
        if asr_result and (asr_result.get("segments") or asr_result.get("text")):
            words = words_from_asr(asr_result, self.fps)
            sources = {str(w.get("timing_source") or "asr") for w in words}
            if sources == {"asr"}:
                alignment = "asr"
            elif "forced_aligned" in sources:
                alignment = "forced_aligned"
            elif sources == {"manual"}:
                alignment = "manual"
            else:
                alignment = "estimated"
            engine = asr_result.get("engine")
            model = asr_result.get("model")
            transcript_text = asr_result.get("text") or " ".join(w["word"] for w in words)
            mismatches = [str(m) for m in (asr_result.get("mismatches") or []) if str(m).strip()]
            if alignment == "asr":
                text_status = "recognized"
            elif alignment == "forced_aligned":
                text_status = "script_with_mismatches" if mismatches else "script_matched"
            elif alignment == "manual":
                text_status = "manual"
            else:
                text_status = "unknown"
            # verified solo se OGNI parola viene dal riconoscimento del parlato
            verified = (
                alignment == "asr"
                and bool(asr_result.get("words_have_timestamps"))
                and bool(words)
                and all(str(w.get("timing_source")) == "asr" for w in words)
            )
        elif text and text.strip():
            words = estimate_words(text, regions, self.fps, duration)
            alignment = "estimated"
            engine = None
            model = None
            transcript_text = text.strip()
            text_status = "from_script"
            verified = False
        else:
            words = estimate_words("", regions, self.fps, duration)
            alignment = "none"
            engine = None
            model = None
            transcript_text = ""
            text_status = "none"
            verified = False

        # 3.6.7 — l'offset dell'audio vale per tutto: parole, frasi, cue, caption.
        words = _shift_words(words, offset, self.fps)
        for index, word in enumerate(words, start=1):
            word["word_id"] = f"w_{index:04d}"

        segments = build_segments(words, fps=self.fps)
        cues = build_cues(words, fps=self.fps)

        scene_windows: list[dict[str, Any]] = []
        if scenes:
            for sc in scenes:
                scene_windows.append(
                    {
                        "scene_uid": getattr(sc, "scene_uid", None) or f"scene_{getattr(sc, 'scene_number', 0):02d}",
                        "scene_number": getattr(sc, "scene_number", None),
                        "start_sec": _round(getattr(sc, "start_sec", 0.0)),
                        "end_sec": _round(getattr(sc, "end_sec", 0.0)),
                        "start_frame": int(getattr(sc, "start_frame", frames(getattr(sc, "start_sec", 0.0), self.fps))),
                        "end_frame": int(
                            getattr(sc, "end_frame", frames(getattr(sc, "end_sec", 0.0), self.fps))
                        ),
                        "asset_id": getattr(sc, "selected_asset_id", None),
                        "duration_frames": int(getattr(sc, "duration_frames", 0)),
                    }
                )
            segments = assign_scenes(segments, scene_windows)
            for cue in cues:
                mid = (float(cue["start_sec"]) + float(cue["end_sec"])) / 2
                owner = None
                for win in scene_windows:
                    if float(win["start_sec"]) <= mid < float(win["end_sec"]):
                        owner = win
                        break
                cue["scene_uid"] = owner.get("scene_uid") if owner else None
                cue["scene_number"] = owner.get("scene_number") if owner else None
                cue["fps"] = self.fps
            cues = build_cues_per_scene(words, scene_windows, fps=self.fps)
            cues = clip_cues_to_scenes(cues, scene_windows)
            cues = resolve_cue_overlaps(cues, self.fps)
            for cue in cues:
                cue.pop("fps", None)

        total_frames = frames(duration, self.fps)
        timeline = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "fps": self.fps,
            "duration_sec": _round(duration),
            "total_frames": total_frames,
            "audio": {
                "path": str(audio_path),
                "sha256": sha256_of(audio_path) if Path(audio_path).is_file() else None,
                "source": source,
                "duration_sec": _round(duration),
                "is_scene_aligned": bool(is_scene_aligned),
                "start_offset_sec": _round(offset),
                "end_padding_sec": _round(float(end_padding_sec or 0.0)),
                "effective_duration_sec": _round(effective),
                "end_sec": _round(offset + effective),
                "speech_regions": [
                    {
                        "start_sec": _round(min(duration, r.start_sec + offset)),
                        "end_sec": _round(min(duration, r.end_sec + offset)),
                    }
                    for r in regions
                ],
                "speech_span_sec": _round(speech_span),
            },
            "transcript": {
                "alignment": alignment,
                "verified": verified,
                "verification": {
                    "timing_status": alignment,
                    "text_status": text_status,
                    "verified": verified,
                    "words_from_asr": sum(1 for w in words if str(w.get("timing_source")) == "asr"),
                    "words_forced_aligned": sum(
                        1 for w in words if str(w.get("timing_source")) == "forced_aligned"
                    ),
                    "words_estimated": sum(1 for w in words if str(w.get("timing_source")) == "estimated"),
                },
                "mismatches": mismatches,
                "engine": engine,
                "model": model,
                "language": language,
                "text": transcript_text,
                "words": words,
                "segments": segments,
            },
            "scenes": scene_windows,
            "caption_cues": cues,
        }
        return timeline

    # -- validazione -------------------------------------------------------- #
    def validate(
        self,
        timeline: dict[str, Any],
        *,
        total_duration_sec: Optional[float] = None,
        scene_windows: Optional[list[dict[str, Any]]] = None,
        tolerance_sec: float = 0.05,
    ) -> list[dict[str, Any]]:
        """Conflitti reali tra audio, scene e sottotitoli. Vuoto = nessun conflitto."""
        issues: list[dict[str, Any]] = []
        audio = timeline.get("audio") or {}
        duration = float(
            total_duration_sec
            if total_duration_sec is not None
            else timeline.get("duration_sec") or 0.0
        )
        end_sec = float(audio.get("end_sec") or 0.0)
        if duration and end_sec > duration + 0.02:
            issues.append(
                {
                    "code": "voce_tagliata",
                    "severity": "error",
                    "message": (
                        f"La voce finisce a {end_sec:.2f}s ma il Reel dura {duration:.2f}s: "
                        f"l'ultima parola andrebbe persa. Allunga il Reel, accorcia il copione "
                        f"o riduci le pause eliminabili."
                    ),
                }
            )
        start_offset = float(audio.get("start_offset_sec") or 0.0)
        if start_offset < 0:
            issues.append(
                {"code": "offset_negativo", "severity": "error", "message": "Offset voce negativo."}
            )

        for cue in timeline.get("caption_cues") or []:
            if scene_windows:
                owner = next(
                    (w for w in scene_windows if w.get("scene_uid") == cue.get("scene_uid")), None
                )
            else:
                owner = next(
                    (w for w in timeline.get("scenes") or [] if w.get("scene_uid") == cue.get("scene_uid")),
                    None,
                )
            if owner is None:
                continue
            if float(cue["start_sec"]) < float(owner["start_sec"]) - 0.25 or float(
                cue["end_sec"]
            ) > float(owner["end_sec"]) + 0.25:
                issues.append(
                    {
                        "code": "cue_fuori_scena",
                        "severity": "error",
                        "message": (
                            f"Sottotitolo «{cue['text'][:40]}» ({cue['start_sec']:.2f}s–"
                            f"{cue['end_sec']:.2f}s) fuori dalla scena {owner.get('scene_number')} "
                            f"({owner['start_sec']:.2f}s–{owner['end_sec']:.2f}s)."
                        ),
                    }
                )

        transcript = timeline.get("transcript") or {}
        if transcript.get("alignment") in ("estimated", "manual"):
            issues.append(
                {
                    "code": "trascrizione_stimata",
                    "severity": "warning",
                    "message": (
                        "Tempi stimati dentro le pause misurate (nessun motore ASR o testo "
                        "corretto a mano): la sincronizzazione è dichiarata, non verificata."
                    ),
                }
            )
        elif transcript.get("alignment") == "none" and audio.get("source") in ("upload", "tts"):
            issues.append(
                {
                    "code": "trascrizione_assente",
                    "severity": "warning",
                    "message": "Nessuna trascrizione disponibile: incolla il copione o correggi il testo.",
                }
            )
        return issues

    # -- persistenza -------------------------------------------------------- #
    def timeline_path(self, project_dir: Optional[Path] = None) -> Optional[Path]:
        base = project_dir or self.project_dir
        return (Path(base) / TIMELINE_FILENAME) if base else None

    def save(self, timeline: dict[str, Any], project_dir: Optional[Path] = None) -> Optional[Path]:
        path = self.timeline_path(project_dir)
        if path is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
        (path.parent / TRANSCRIPT_FILENAME).write_text(
            json.dumps(timeline.get("transcript") or {}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    def load(self, project_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
        path = self.timeline_path(project_dir)
        if path and path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                return None
        return None


# --------------------------------------------------------------------------- #
# Esportazione sottotitoli dagli stessi cue
# --------------------------------------------------------------------------- #
def _srt_time(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_time(sec: float) -> str:
    return _srt_time(sec).replace(",", ".")


def _ass_time(sec: float) -> str:
    cs = int(round(max(0.0, sec) * 100))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6_000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def cues_to_srt(cues: Iterable[dict[str, Any]]) -> str:
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{_srt_time(cue['start_sec'])} --> {_srt_time(cue['end_sec'])}\n{cue['text']}\n"
        )
    return "\n".join(blocks)


def cues_to_vtt(cues: Iterable[dict[str, Any]]) -> str:
    lines = ["WEBVTT", ""]
    for cue in cues:
        lines.append(f"{_vtt_time(cue['start_sec'])} --> {_vtt_time(cue['end_sec'])}")
        lines.append(str(cue["text"]))
        lines.append("")
    return "\n".join(lines)


def cues_to_ass(cues: Iterable[dict[str, Any]], *, font: str = "Playfair Display", font_size: int = 54) -> str:
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic,"
        " Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
        " Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: KORA,{font},{font_size},&H00F5F0E6,&H00202020,&H64000000,0,0,0,0,100,100,1,0,1,2,1,2,80,80,220,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for cue in cues:
        text = str(cue["text"]).replace("\n", " ")
        lines.append(
            f"Dialogue: 0,{_ass_time(cue['start_sec'])},{_ass_time(cue['end_sec'])},KORA,,0,0,0,,{text}"
        )
    return header + "\n".join(lines) + "\n"
