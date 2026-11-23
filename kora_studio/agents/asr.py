"""ASR adapter (3.6.6) — trascrizione e timestamp, solo con motori realmente presenti.

Nessun modello viene scaricato automaticamente: se ``faster-whisper`` o
``whisper`` non sono installati, KORA lo dichiara e chiede il testo all'utente.
Non esiste in questo modulo un percorso che "inventa" una trascrizione.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LEVELS = {
    "rapido": {"beam_size": 1, "vad_filter": True, "label": "Rapido"},
    "bilanciato": {"beam_size": 5, "vad_filter": True, "label": "Bilanciato"},
    "accurato": {"beam_size": 8, "vad_filter": True, "label": "Accurato"},
}


class AsrUnavailable(RuntimeError):
    """Nessun motore di trascrizione utilizzabile in questa installazione."""


@dataclass
class AsrWord:
    word: str
    start_sec: float
    end_sec: float
    confidence: Optional[float] = None


@dataclass
class AsrSegment:
    text: str
    start_sec: float
    end_sec: float
    words: list[AsrWord] = field(default_factory=list)


@dataclass
class AsrResult:
    text: str
    segments: list[AsrSegment]
    language: str
    engine: str
    model: str
    duration_sec: float
    words_have_timestamps: bool
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "engine": self.engine,
            "model": self.model,
            "duration_sec": self.duration_sec,
            "words_have_timestamps": self.words_have_timestamps,
            "created_at": self.created_at or datetime.now(timezone.utc).isoformat(),
            "segments": [
                {
                    "text": s.text,
                    "start_sec": round(s.start_sec, 3),
                    "end_sec": round(s.end_sec, 3),
                    "words": [
                        {
                            "word": w.word,
                            "start_sec": round(w.start_sec, 3),
                            "end_sec": round(w.end_sec, 3),
                            "confidence": w.confidence,
                        }
                        for w in s.words
                    ],
                }
                for s in self.segments
            ],
        }


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def available_engines() -> list[str]:
    """Motori ASR realmente importabili in questo ambiente (ordine di preferenza)."""
    found: list[str] = []
    if _has("faster_whisper"):
        found.append("faster_whisper")
    if _has("whisper"):
        found.append("whisper")
    return found


def status() -> dict[str, Any]:
    engines = available_engines()
    return {
        "available": bool(engines),
        "engines": engines,
        "preferred": engines[0] if engines else None,
        "levels": [{"id": k, "label": v["label"]} for k, v in LEVELS.items()],
        "message": (
            f"Motore locale pronto: {engines[0]}"
            if engines
            else "Nessun motore ASR installato (faster-whisper o whisper). "
            "KORA non inventa la trascrizione: incolla il copione o correggi il testo a mano."
        ),
        "install_hint": "pip install faster-whisper  # oppure: pip install openai-whisper",
    }


def transcribe(
    audio_path: Path,
    *,
    language: str = "it",
    level: str = "bilanciato",
    model_name: Optional[str] = None,
    duration_sec: Optional[float] = None,
) -> AsrResult:
    """Trascrive ``audio_path`` con il primo motore disponibile.

    Solleva :class:`AsrUnavailable` se non c'è alcun motore: il chiamante deve
    gestire il caso, mai simulare il risultato.
    """
    engines = available_engines()
    if not engines:
        raise AsrUnavailable(
            "Nessun motore di trascrizione installato: impossibile ricavare le "
            "parole pronunciate. Inserisci il copione o correggi il testo a mano."
        )
    cfg = LEVELS.get(level, LEVELS["bilanciato"])
    if engines[0] == "faster_whisper":
        return _transcribe_faster_whisper(
            audio_path, language=language, cfg=cfg, model_name=model_name, duration_sec=duration_sec
        )
    return _transcribe_whisper(
        audio_path, language=language, model_name=model_name or "base", duration_sec=duration_sec
    )


def _transcribe_faster_whisper(
    audio_path: Path,
    *,
    language: str,
    cfg: dict[str, Any],
    model_name: Optional[str],
    duration_sec: Optional[float],
) -> AsrResult:
    from faster_whisper import WhisperModel  # type: ignore

    name = model_name or "small"
    model = WhisperModel(name, device="cpu", compute_type="int8")
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=int(cfg["beam_size"]),
        vad_filter=bool(cfg["vad_filter"]),
        word_timestamps=True,
    )
    segments: list[AsrSegment] = []
    has_words = False
    for seg in segments_iter:
        words = [
            AsrWord(
                word=w.word.strip(),
                start_sec=float(w.start),
                end_sec=float(w.end),
                confidence=getattr(w, "probability", None),
            )
            for w in (getattr(seg, "words", None) or [])
            if (w.word or "").strip()
        ]
        has_words = has_words or bool(words)
        segments.append(
            AsrSegment(
                text=(seg.text or "").strip(),
                start_sec=float(seg.start),
                end_sec=float(seg.end),
                words=words,
            )
        )
    full = " ".join(s.text for s in segments).strip()
    dur = float(duration_sec or getattr(info, "duration", 0.0) or 0.0)
    return AsrResult(
        text=full,
        segments=segments,
        language=getattr(info, "language", language) or language,
        engine="faster_whisper",
        model=name,
        duration_sec=dur,
        words_have_timestamps=has_words,
    )


def _transcribe_whisper(
    audio_path: Path,
    *,
    language: str,
    model_name: str,
    duration_sec: Optional[float],
) -> AsrResult:
    import whisper  # type: ignore

    model = whisper.load_model(model_name)
    data = model.transcribe(str(audio_path), language=language, word_timestamps=True)
    segments: list[AsrSegment] = []
    has_words = False
    for seg in data.get("segments") or []:
        words = [
            AsrWord(
                word=str(w.get("word", "")).strip(),
                start_sec=float(w.get("start", 0.0)),
                end_sec=float(w.get("end", 0.0)),
                confidence=w.get("probability"),
            )
            for w in (seg.get("words") or [])
            if str(w.get("word", "")).strip()
        ]
        has_words = has_words or bool(words)
        segments.append(
            AsrSegment(
                text=str(seg.get("text", "")).strip(),
                start_sec=float(seg.get("start", 0.0)),
                end_sec=float(seg.get("end", 0.0)),
                words=words,
            )
        )
    return AsrResult(
        text=str(data.get("text", "")).strip(),
        segments=segments,
        language=str(data.get("language", language)),
        engine="whisper",
        model=model_name,
        duration_sec=float(duration_sec or 0.0),
        words_have_timestamps=has_words,
    )
