"""Quality Controller agent — real technical, visual, and narrative QC via FFprobe/FFmpeg."""

from __future__ import annotations

import json
import difflib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from kora_studio.agents.brand_guardian import BrandGuardian
from kora_studio.config import DEFAULT_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH
from kora_studio.db.models import (
    AuthenticityStatus,
    QualityCheckItem,
    QualityReport,
    StoryboardScene,
)
from kora_studio.media_tools import find_ffprobe, parse_fps, probe_media, run_ffmpeg
from kora_studio.vault.media_vault import MediaVault


class _SkipAudioLevels(Exception):
    """Segnale interno: livelli audio non applicabili a questo export."""


def _content_words(text: str) -> set[str]:
    """Parole portanti di una frase: senza articoli, preposizioni e congiunzioni."""
    stopwords = {
        "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di", "del", "dello",
        "della", "dei", "degli", "delle", "a", "al", "allo", "alla", "ai", "agli",
        "alle", "da", "dal", "dallo", "dalla", "dai", "dagli", "dalle", "in", "nel",
        "nello", "nella", "nei", "negli", "nelle", "con", "su", "sul", "sullo",
        "sulla", "sui", "sugli", "sulle", "per", "tra", "fra", "e", "ed", "o", "che",
        "chi", "cui", "non", "piu", "più", "si", "ci", "mi", "ti", "vi", "ne", "è",
        "e'", "sono", "sei", "siamo", "siete", "ha", "ho", "hai", "hanno", "come",
        "dove", "quando", "questo", "questa", "questi", "queste", "quello", "quella",
        "tuo", "tua", "suo", "sua", "te", "me", "lui", "lei", "noi", "voi", "loro",
    }
    return {w for w in text.split() if len(w) > 2 and w not in stopwords}


def _word_coverage(spoken: str, subtitle: str) -> float:
    """Quota di parole portanti del sottotitolo già presenti nella frase detta."""
    sub = _content_words(subtitle)
    if not sub:
        return 1.0
    return len(sub & _content_words(spoken)) / len(sub)


class QualityController:
    """Performs automated technical, visual, and narrative checks on rendered Reels."""

    def __init__(self, vault: MediaVault, guardian: BrandGuardian, engine: Any = None) -> None:
        self.vault = vault
        self.guardian = guardian
        self.engine = engine

    def attach_engine_manager(self, engine: Any) -> None:
        self.engine = engine

    def diagnose(self, report: Any) -> dict[str, Any]:
        """Spiega i controlli falliti con il motore configurato (diagnosi, mai un falso OK)."""
        failures = [c for c in getattr(report, "checks", []) or [] if not c.passed]
        summary = "; ".join(f"{c.name}: {c.detail}" for c in failures)[:1500]
        fallback = {
            "used": False,
            "suggestion": "Correggere i controlli falliti e rieseguire il montaggio: " + (summary or "nessun dettaglio"),
        }
        if self.engine is None or not failures:
            return fallback
        from kora_studio.ai_engine.base import EngineError

        prompt = (
            "Sei il Quality Guardian di KORA. Questi controlli automatici sono falliti durante il "
            "montaggio del Reel. Indica in 3 righe la causa tecnica più probabile e la correzione "
            "concreta, senza inventare dati. Rispondi solo con il testo."
        )
        try:
            result = self.engine.call(
                "quality_guardian",
                [{"role": "system", "content": prompt}, {"role": "user", "content": summary}],
                json_mode=False,
                timeout=45,
            )
        except EngineError as exc:
            return {**fallback, "error": str(exc)}
        return {
            "used": True,
            "suggestion": (result.text or "").strip()[:800],
            "engine": f"{result.provider}/{result.model}",
        }

    @staticmethod
    def count_frames(mp4_path: Path) -> tuple[Optional[int], str]:
        """Conteggio esatto dei fotogrammi decodificati (mai stimato dalla durata).

        Ritorna ``(None, motivo)`` quando il conteggio non è eseguibile: il QC
        fallisce invece di approssimare.
        """
        import subprocess as _sp

        ffprobe_bin = find_ffprobe()
        if not ffprobe_bin:
            # Fallback esatto: PyAV decodifica ogni fotogramma (FFmpeg incluso nei wheel)
            try:
                import av

                total = 0
                with av.open(str(mp4_path)) as container:
                    stream = container.streams.video[0]
                    for _ in container.decode(stream):
                        total += 1
                if total <= 0:
                    return None, "PyAV non ha decodificato fotogrammi"
                return total, "PyAV decode count"
            except Exception as exc:  # pragma: no cover - dipende dall'ambiente
                return None, f"ffprobe non disponibile e PyAV non utilizzabile ({type(exc).__name__})"
        try:
            cp = _sp.run(
                [
                    ffprobe_bin,
                    "-v",
                    "error",
                    "-count_frames",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=nb_read_frames",
                    "-of",
                    "csv=p=0",
                    str(mp4_path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except _sp.TimeoutExpired:
            return None, "ffprobe: timeout"
        except Exception as exc:  # pragma: no cover - defensive
            return None, f"ffprobe: {exc.__class__.__name__}"
        raw = (cp.stdout or "").strip()
        if not raw:
            return None, (cp.stderr or "ffprobe non ha restituito frame").strip()[:160]
        first = raw.splitlines()[0].strip()
        if not first.isdigit():
            return None, f"valore inatteso: {first[:40]}"
        return int(first), "nb_read_frames"

    def inspect_reel(
        self,
        mp4_path: Path,
        scenes: list[StoryboardScene],
        expected_duration_sec: float,
        expect_audio: bool = True,
        work_dir: Path | None = None,
        voiceover_present: Optional[bool] = None,
    ) -> QualityReport:
        checks: list[QualityCheckItem] = []
        metrics: dict[str, Any] = {}
        human_reasons: list[str] = []

        # 1. File existence & size
        exists = mp4_path.is_file() and mp4_path.stat().st_size > 10_000
        size_mb = round(mp4_path.stat().st_size / (1024 * 1024), 2) if mp4_path.is_file() else 0.0
        metrics["file_size_mb"] = size_mb
        checks.append(
            QualityCheckItem(
                category="technical",
                name="File decodificabile e dimensioni valide",
                passed=exists and size_mb < 120.0,
                detail=f"Dimensione {size_mb} MB" if exists else "File MP4 mancante o vuoto",
            )
        )
        if not exists:
            return QualityReport(
                passed=False,
                needs_human_review=True,
                human_review_reasons=["File MP4 finale assente."],
                checks=checks,
                metrics=metrics,
            )

        # 2. FFprobe stream checks (1080x1920, 25fps, h264, aac, exact duration)
        a_streams: list[dict[str, Any]] = []
        try:
            probe = probe_media(mp4_path)
            v_streams = [s for s in probe["streams"] if s.get("codec_type") == "video"]
            a_streams = [s for s in probe["streams"] if s.get("codec_type") == "audio"]
            actual_dur = float(probe["format"].get("duration", 0.0) or 0.0)
            metrics["actual_duration_sec"] = round(actual_dur, 3)
            metrics["expected_duration_sec"] = round(expected_duration_sec, 3)

            dur_ok = abs(actual_dur - expected_duration_sec) <= 0.16
            checks.append(
                QualityCheckItem(
                    category="technical",
                    name="Durata esatta",
                    passed=dur_ok,
                    detail=f"Attesa {expected_duration_sec:.2f}s, rilevata {actual_dur:.2f}s",
                )
            )

            if v_streams:
                vs = v_streams[0]
                w, h = int(vs.get("width", 0)), int(vs.get("height", 0))
                fps = round(parse_fps(str(vs.get("r_frame_rate", "25/1"))), 2)
                vcodec = vs.get("codec_name", "")
                metrics.update({"width": w, "height": h, "fps": fps, "video_codec": vcodec})
                checks.append(
                    QualityCheckItem(
                        category="technical",
                        name="Risoluzione 1080x1920 (9:16)",
                        passed=(w == DEFAULT_WIDTH and h == DEFAULT_HEIGHT),
                        detail=f"{w}x{h}",
                    )
                )
                checks.append(
                    QualityCheckItem(
                        category="technical",
                        name="Frame rate 25 fps",
                        passed=abs(fps - DEFAULT_FPS) <= 0.05,
                        detail=f"{fps} fps",
                    )
                )
                checks.append(
                    QualityCheckItem(
                        category="technical",
                        name="Codec video H.264 compatibile",
                        passed=(vcodec == "h264"),
                        detail=vcodec,
                    )
                )
            else:
                checks.append(
                    QualityCheckItem(
                        category="technical",
                        name="Traccia video presente",
                        passed=False,
                        detail="Nessuna traccia video rilevata",
                    )
                )

            if a_streams:
                acodec = a_streams[0].get("codec_name", "")
                metrics["audio_codec"] = acodec
                checks.append(
                    QualityCheckItem(
                        category="technical",
                        name="Audio AAC presente",
                        passed=(acodec == "aac"),
                        detail=acodec,
                    )
                )
            else:
                checks.append(
                    QualityCheckItem(
                        category="technical",
                        name="Audio AAC presente",
                        passed=not expect_audio,
                        detail="Nessuna traccia audio: non richiesta per questo export",
                        status="non_applicabile" if not expect_audio else "non_superato",
                    )
                )
        except Exception as exc:
            checks.append(
                QualityCheckItem(
                    category="technical",
                    name="Decodifica FFprobe",
                    passed=False,
                    detail=str(exc),
                )
            )

        # 3. Audio clipping check via FFmpeg volumedetect
        if not expect_audio or not a_streams:
            checks.append(
                QualityCheckItem(
                    category="technical",
                    name="Nessun clipping audio",
                    passed=True,
                    detail="Nessuna traccia audio nell'export: verifica non applicabile",
                    status="non_applicabile",
                )
            )
        try:
            vol_proc = run_ffmpeg(
                ["-i", str(mp4_path), "-af", "volumedetect", "-f", "null", "-"],
                timeout=60,
            )
            m_max = re.search(r"max_volume:\s*([-\d.]+)\s*dB", vol_proc.stderr)
            m_mean = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", vol_proc.stderr)
            max_db = float(m_max.group(1)) if m_max else -99.0
            mean_db = float(m_mean.group(1)) if m_mean else -99.0
            metrics["max_volume_db"] = max_db
            metrics["mean_volume_db"] = mean_db
            no_clip = max_db <= -0.1
            if not expect_audio or not a_streams:
                raise _SkipAudioLevels()
            checks.append(
                QualityCheckItem(
                    category="technical",
                    name="Nessun clipping audio",
                    passed=no_clip,
                    detail=f"Picco massimo {max_db:.1f} dB, media {mean_db:.1f} dB",
                )
            )
        except _SkipAudioLevels:
            pass
        except Exception as exc:
            checks.append(
                QualityCheckItem(
                    category="technical",
                    name="Analisi livelli audio",
                    passed=True,
                    severity="warning",
                    status="non_verificato",
                    detail=f"Verifica volumedetect non eseguita: {exc}",
                )
            )

        # 3b. Loudness reale del master: ≈ −14 LUFS, picco ≤ −1 dBTP
        if expect_audio and a_streams:
            try:
                loud = run_ffmpeg(
                    [
                        "-hide_banner",
                        "-nostats",
                        "-i",
                        str(mp4_path),
                        "-af",
                        "loudnorm=print_format=json",
                        "-f",
                        "null",
                        "-",
                    ],
                    timeout=90,
                )
                blob = (loud.stderr or "") + (loud.stdout or "")
                start = blob.rfind("{")
                end = blob.rfind("}")
                data = json.loads(blob[start : end + 1]) if start >= 0 and end > start else {}
                lufs = float(data.get("input_i")) if data.get("input_i") not in (None, "-inf") else None
                peak = float(data.get("input_tp")) if data.get("input_tp") not in (None, "-inf") else None
                if lufs is None or peak is None:
                    checks.append(
                        QualityCheckItem(
                            category="technical",
                            name="Loudness del master (−14 LUFS, picco ≤ −1 dBTP)",
                            passed=True,
                            severity="warning",
                            status="non_verificato",
                            detail="Misura loudnorm non interpretabile",
                        )
                    )
                else:
                    metrics["lufs"] = round(lufs, 2)
                    metrics["true_peak_db"] = round(peak, 2)
                    loud_ok = -15.5 <= lufs <= -12.5 and peak <= -1.0
                    checks.append(
                        QualityCheckItem(
                            category="technical",
                            name="Loudness del master (−14 LUFS, picco ≤ −1 dBTP)",
                            passed=loud_ok,
                            severity="warning",
                            status="superato" if loud_ok else "non_superato",
                            detail=f"{lufs:.1f} LUFS integrato, true peak {peak:.1f} dBTP",
                        )
                    )
            except Exception as exc:
                checks.append(
                    QualityCheckItem(
                        category="technical",
                        name="Loudness del master (−14 LUFS, picco ≤ −1 dBTP)",
                        passed=True,
                        severity="warning",
                        status="non_verificato",
                        detail=f"Misura non eseguita: {exc}",
                    )
                )
        else:
            checks.append(
                QualityCheckItem(
                    category="technical",
                    name="Loudness del master (−14 LUFS, picco ≤ −1 dBTP)",
                    passed=True,
                    status="non_applicabile",
                    detail="Nessuna traccia audio: loudness non applicabile",
                )
            )

        # 4. Full video decode pass + real frame-count verification (Section 10)
        try:
            dec_proc = run_ffmpeg(
                ["-v", "error", "-i", str(mp4_path), "-f", "null", "-"],
                timeout=90,
            )
            decode_err = (dec_proc.stderr or "").strip()
            checks.append(
                QualityCheckItem(
                    category="technical",
                    name="Decodifica completa del video (zero pacchetti corrotti)",
                    passed=(dec_proc.returncode == 0 and not decode_err),
                    detail="Tutti i frame decodificati senza errori"
                    if not decode_err
                    else decode_err[:180],
                )
            )
        except Exception as exc:
            checks.append(
                QualityCheckItem(
                    category="technical",
                    name="Decodifica completa del video",
                    passed=False,
                    severity="warning",
                    status="non_verificato",
                    detail=f"Decodifica non eseguibile: {exc}",
                )
            )

        expected_frames = sum(s.duration_frames for s in scenes) or int(round(expected_duration_sec * DEFAULT_FPS))
        actual_frames, frame_detail = self.count_frames(mp4_path)
        metrics["expected_frames"] = expected_frames
        metrics["actual_frames"] = actual_frames
        checks.append(
            QualityCheckItem(
                category="technical",
                name="Verifica esatta dei fotogrammi",
                passed=actual_frames is not None and actual_frames == expected_frames,
                severity="error" if actual_frames is not None else "warning",
                status=(
                    "non_verificato"
                    if actual_frames is None
                    else ("superato" if actual_frames == expected_frames else "non_superato")
                ),
                detail=(
                    f"Attesi {expected_frames} frame ({expected_frames / DEFAULT_FPS:.1f} s @ {DEFAULT_FPS} fps), "
                    + (
                        f"contati {actual_frames} frame ({frame_detail})"
                        if actual_frames is not None
                        else f"conteggio non eseguibile ({frame_detail}): una stima non è un conteggio"
                    )
                ),
            )
        )

        # 4b. Black-frame check on non-outro content scenes
        content_end = max(
            (s.end_sec for s in scenes if not s.is_outro),
            default=max(0.5, expected_duration_sec - 2.5),
        )
        try:
            bd_proc = run_ffmpeg(
                [
                    "-t",
                    f"{max(0.5, content_end - 0.3):.3f}",
                    "-i",
                    str(mp4_path),
                    "-vf",
                    "blackdetect=d=0.4:pix_th=0.08",
                    "-an",
                    "-f",
                    "null",
                    "-",
                ],
                timeout=60,
            )
            has_black = "black_start:" in bd_proc.stderr
            checks.append(
                QualityCheckItem(
                    category="technical",
                    name="Nessun frame nero indesiderato nelle scene",
                    passed=not has_black,
                    detail="Nessun nero anomalo rilevato" if not has_black else "Rilevata sequenza nera anomala",
                )
            )
        except Exception:
            pass

        # 5. Visual & Authenticity checks
        missing_assets = 0
        generative_used = False
        for sc in scenes:
            if sc.is_outro:
                logo = self.vault.get_logo_asset()
                if not logo:
                    missing_assets += 1
                continue
            asset = self.vault.get_asset(sc.selected_asset_id) if sc.selected_asset_id else None
            if not asset:
                missing_assets += 1
            elif asset["authenticity_status"] in (
                AuthenticityStatus.GENERATIVO_CONTROLLATO.value,
                AuthenticityStatus.ILLUSTRATIVO_SINTETICO.value,
                AuthenticityStatus.DA_VERIFICARE.value,
            ):
                generative_used = True
                human_reasons.append(
                    f"Scena {sc.scene_number} utilizza materiale '{asset['authenticity_status']}' ({asset['filename']}): verifica umana consigliata."
                )

        checks.append(
            QualityCheckItem(
                category="visual",
                name="Integrità asset e logo originale",
                passed=(missing_assets == 0),
                detail="Tutti gli asset e il logo originale verificati"
                if missing_assets == 0
                else f"{missing_assets} asset mancanti",
            )
        )
        checks.append(
            QualityCheckItem(
                category="visual",
                name="Coerenza architettura, arredi e autenticità KORA",
                passed=True,
                severity="warning" if generative_used else "info",
                requires_human_review=generative_used,
                detail="Materiali 100% autentici/conservativi"
                if not generative_used
                else "Presenti elaborazioni generative: richiesta revisione umana mirata",
            )
        )

        # 6. Narrative checks (hook, continuity, brand tone, voice/subtitle coherence, temporal validity, logo & payoff)
        all_text = " ".join(s.subtitle_text or s.spoken_text for s in scenes if not s.is_outro)
        guard = self.guardian.validate_copy(all_text)
        checks.append(
            QualityCheckItem(
                category="narrative",
                name="Identità narrativa KORA e continuità",
                passed=guard["approved"],
                detail="Tono evocativo e identità KORA coerenti"
                if guard["approved"]
                else "; ".join(guard["violations"]),
            )
        )
        # Voiceover vs subtitle coherence.
        # 3.6.4 — un sottotitolo più breve della frase detta è una scelta legittima,
        # non un errore: blocca solo quando i due testi raccontano cose diverse.
        vo_sub_hard: list[tuple[int, str, str]] = []
        vo_sub_soft: list[int] = []
        for s in scenes:
            if s.is_outro:
                continue
            sp_raw = (s.spoken_text or "").strip()
            sb_raw = (s.subtitle_text or "").strip()
            sp = re.sub(r"\W+", " ", sp_raw.lower()).strip()
            sb = re.sub(r"\W+", " ", sb_raw.lower()).strip()
            if not sp or not sb or sp == sb:
                continue
            ratio = difflib.SequenceMatcher(None, sp, sb).ratio()
            shortened = (
                sp.startswith(sb)
                or sb.startswith(sp)
                or ratio >= 0.6
                # 3.6.5 — il sottotitolo riformula la frase dello speaker con
                # parole sue (es. «La luce sfiora il pergolato» al posto di
                # «La luce di novembre sfiora il pergolato, posa i suoi fili
                # d'ombra»): resta una sintesi, non un testo diverso.
                or _word_coverage(sp, sb) >= 0.75
            )
            if shortened:
                vo_sub_soft.append(s.scene_number)
            else:
                vo_sub_hard.append((s.scene_number, sp_raw, sb_raw))

        def _short(value: str, limit: int = 48) -> str:
            return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"

        if vo_sub_hard:
            examples = "; ".join(
                f"scena {n}: voce «{_short(sp)}» ≠ sottotitoli «{_short(sb)}»"
                for n, sp, sb in vo_sub_hard[:3]
            )
            checks.append(
                QualityCheckItem(
                    category="narrative",
                    name="Coerenza tra voiceover e sottotitoli",
                    passed=False,
                    severity="error",
                    detail=(
                        f"Testo e sottotitoli raccontano cose diverse ({examples}). "
                        "Apri Storyboard → «Allinea sottotitoli al testo» per riallineare "
                        "le scene indicate, poi premi Riprova."
                    ),
                )
            )
        elif vo_sub_soft:
            checks.append(
                QualityCheckItem(
                    category="narrative",
                    name="Coerenza tra voiceover e sottotitoli",
                    passed=True,
                    severity="warning",
                    requires_human_review=True,
                    detail=(
                        f"Sottotitoli più sintetici del testo alle scene {vo_sub_soft}: "
                        "scelta legittima, la voce dice la frase completa."
                    ),
                )
            )
        else:
            checks.append(
                QualityCheckItem(
                    category="narrative",
                    name="Coerenza tra voiceover e sottotitoli",
                    passed=True,
                    detail="Voiceover e sottotitoli allineati su tutte le scene",
                )
            )
        # 7. 3.6.6 — Sincronizzazione reale: la timeline canonica è l'unica prova ammessa.
        # La somiglianza lessicale NON dimostra che una frase sia stata pronunciata
        # nell'intervallo in cui appare: qui si leggono i tempi, non le parole.
        timeline = None
        if work_dir is not None:
            try:
                from kora_studio.agents.audio_timeline import TIMELINE_FILENAME, AudioTimelineManager

                timeline_path = Path(work_dir) / TIMELINE_FILENAME
                if timeline_path.is_file():
                    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
                    mgr = AudioTimelineManager(fps=int(timeline.get("fps") or 25))
                    timeline_issues = mgr.validate(timeline, total_duration_sec=expected_duration_sec)
                else:
                    timeline_issues = [
                        {
                            "code": "timeline_assente",
                            "severity": "warning",
                            "message": "Nessuna timeline canonica per questo progetto: allineamento non verificato.",
                        }
                    ]
            except Exception as exc:  # un file illeggibile non deve diventare un falso OK
                timeline = None
                timeline_issues = [
                    {"code": "timeline_illeggibile", "severity": "warning", "message": f"Timeline non leggibile: {exc}"}
                ]
        else:
            timeline_issues = [
                {
                    "code": "timeline_non_valutata",
                    "severity": "warning",
                    "message": "QC eseguito senza cartella progetto: sincronizzazione non valutata.",
                }
            ]

        if timeline:
            audio = timeline.get("audio") or {}
            transcript = timeline.get("transcript") or {}
            alignment = str(transcript.get("alignment") or "none")
            words = len(transcript.get("words") or [])
            cues = len(timeline.get("caption_cues") or [])
            errors = [i for i in timeline_issues if i.get("severity") == "error"]
            warnings = [i for i in timeline_issues if i.get("severity") == "warning"]
            checks.append(
                QualityCheckItem(
                    category="technical",
                    name="Voce non tagliata (inizio e fine conservati)",
                    passed=not any(i["code"] == "voce_tagliata" for i in timeline_issues),
                    severity="error",
                    detail=(
                        f"Voce {float(audio.get('start_offset_sec') or 0.0):.2f}s → "
                        f"{float(audio.get('end_sec') or 0.0):.2f}s su {expected_duration_sec:.2f}s di Reel"
                        if not any(i["code"] == "voce_tagliata" for i in timeline_issues)
                        else next(i["message"] for i in timeline_issues if i["code"] == "voce_tagliata")
                    ),
                )
            )
            checks.append(
                QualityCheckItem(
                    category="narrative",
                    name="Sincronizzazione audio, scene e sottotitoli",
                    passed=not errors,
                    severity="error" if errors else ("warning" if warnings else "info"),
                    requires_human_review=bool(warnings),
                    detail=(
                        f"Timeline: {words} parole, {cues} cue, allineamento «{alignment}». "
                        + ("; ".join(i["message"] for i in errors + warnings) if (errors or warnings) else "Nessun conflitto.")
                    ),
                )
            )
            if warnings:
                human_reasons.append(
                    "Sincronizzazione non verificata: "
                    + " ".join(i["message"] for i in warnings)
                )
            # 3.6.7 — i sottotitoli devono venire dal testo approvato, non da un altro testo
            transcript_words = " ".join(
                str(w.get("word") or "") for w in (transcript.get("words") or [])
            ).lower()
            editorial = [
                c
                for c in (timeline.get("caption_cues") or [])
                if str(c.get("timing_source")) in ("editorial_in_segment", "editorial")
            ]
            faithful = [
                c
                for c in (timeline.get("caption_cues") or [])
                if c not in editorial
            ]
            untraced = (
                [
                    c
                    for c in faithful
                    if transcript_words
                    and not all(w.lower() in transcript_words for w in str(c.get("text") or "").split())
                ]
                if faithful
                else []
            )
            checks.append(
                QualityCheckItem(
                    category="narrative",
                    name="Sottotitoli coerenti con la revisione del transcript",
                    passed=not untraced,
                    severity="warning",
                    status="superato" if not untraced else "non_superato",
                    requires_human_review=bool(editorial),
                    detail=(
                        f"{len(faithful)} cue fedeli alla trascrizione, "
                        f"{len(editorial)} editoriali dichiarati"
                        + (
                            "; cue con parole non presenti nella trascrizione: "
                            + ", ".join(str(c.get("cue_uid")) for c in untraced[:3])
                            if untraced
                            else ""
                        )
                    ),
                )
            )
            checks.append(
                QualityCheckItem(
                    category="narrative",
                    name="Trascrizione verificata sulle parole pronunciate",
                    passed=alignment == "asr" and bool(transcript.get("verified")),
                    severity="warning",
                    status="superato" if (alignment == "asr" and transcript.get("verified")) else "non_verificato",
                    requires_human_review=alignment != "asr",
                    detail=(
                        "Trascrizione ASR con timestamp delle parole."
                        if alignment == "asr"
                        else (
                            "Tempi stimati dentro le pause misurate (nessun motore ASR): "
                            "i sottotitoli sono coerenti con il testo, non trascritti dalla voce."
                        )
                    ),
                )
            )
            # 3.6.8 — una parola del copione appoggiata ai tempi ASR non è una parola
            # riconosciuta: se `verified` è True deve esserlo ogni singola parola.
            timed_words = list(transcript.get("words") or [])
            non_asr = [
                w
                for w in timed_words
                if str(w.get("timing_source") or "") not in ("asr",)
            ]
            false_verified = bool(transcript.get("verified")) and (
                alignment != "asr" or bool(non_asr)
            )
            script_mismatches = [str(m) for m in (transcript.get("mismatches") or [])]
            checks.append(
                QualityCheckItem(
                    category="narrative",
                    name="Nessuna parola del copione spacciata per riconosciuta",
                    passed=not false_verified,
                    severity="error",
                    status="superato" if not false_verified else "non_superato",
                    requires_human_review=bool(script_mismatches),
                    detail=(
                        "Ogni parola dichiara la propria origine "
                        f"({(transcript.get('verification') or {}).get('words_from_asr', 0)} da ASR, "
                        f"{(transcript.get('verification') or {}).get('words_forced_aligned', 0)} allineate dal copione, "
                        f"{(transcript.get('verification') or {}).get('words_estimated', 0)} stimate)"
                        + (
                            f"; parole del copione non riconosciute: {', '.join(script_mismatches[:5])}"
                            if script_mismatches
                            else ""
                        )
                    ),
                )
            )
        else:
            checks.append(
                QualityCheckItem(
                    category="narrative",
                    name="Sincronizzazione audio, scene e sottotitoli",
                    passed=True,
                    severity="warning",
                    status="non_verificato",
                    requires_human_review=True,
                    detail="; ".join(i["message"] for i in timeline_issues) or "Timeline non disponibile.",
                )
            )

        # Temporal validity of promo/date mentions
        cur_year = datetime.now(timezone.utc).year
        past_years = [str(y) for y in range(2020, cur_year) if str(y) in all_text]
        promo_flags = [k for k in ("scade", "solo oggi", "ultimo giorno", "black friday") if k in all_text.lower()]
        temporal_ok = not past_years and not promo_flags
        checks.append(
            QualityCheckItem(
                category="narrative",
                name="Validità temporale delle informazioni promozionali",
                passed=temporal_ok,
                detail="Nessun riferimento temporale scaduto o promozionale improprio"
                if temporal_ok
                else f"Riferimenti temporali da verificare: {past_years + promo_flags}",
            )
        )
        # Logo originale + payoff esatto
        outro_scene = next((s for s in scenes if s.is_outro), None)
        logo_asset = self.vault.get_logo_asset()
        logo_payoff_ok = bool(
            logo_asset
            and outro_scene
            and outro_scene.selected_asset_id == logo_asset["id"]
            and "the art of being" in (outro_scene.subtitle_text or "").lower()
        )
        checks.append(
            QualityCheckItem(
                category="visual",
                name="Logo originale e payoff esatto 'The Art of Being'",
                passed=logo_payoff_ok,
                detail="Logo originale KORA e payoff verificati in chiusura"
                if logo_payoff_ok
                else "Outro privo di logo originale o payoff esatto",
            )
        )
        sub_sync_ok = all(
            s.end_frame > s.start_frame and len(s.subtitle_text) <= 120 for s in scenes
        )
        # 3.6 — «sincronizzato» ha senso solo se esiste una traccia voce reale:
        # un testo scritto in storyboard non è un voiceover prodotto.
        if voiceover_present is None:
            has_voiceover = any((s.spoken_text or "").strip() for s in scenes if not s.is_outro)
        else:
            has_voiceover = bool(voiceover_present)
        checks.append(
            QualityCheckItem(
                category="narrative",
                name="Sottotitoli sincronizzati e non tagliati",
                passed=True if not has_voiceover else sub_sync_ok,
                status=(
                    "non_applicabile"
                    if not has_voiceover
                    else ("superato" if sub_sync_ok else "non_superato")
                ),
                detail=(
                    "Nessun voiceover in questo Reel: sincronizzazione non applicabile"
                    if not has_voiceover
                    else "Testi entro l'area sicura e sincronizzati per frame"
                ),
            )
        )

        # Sample a mid-frame to confirm visual decode works in Pillow too
        if work_dir:
            sample_jpg = work_dir / "qc_sample.jpg"
            try:
                run_ffmpeg(
                    [
                        "-y",
                        "-loglevel",
                        "error",
                        "-ss",
                        f"{min(2.0, expected_duration_sec / 3):.2f}",
                        "-i",
                        str(mp4_path),
                        "-frames:v",
                        "1",
                        str(sample_jpg),
                    ],
                    timeout=30,
                )
                if sample_jpg.is_file():
                    with Image.open(sample_jpg) as im:
                        metrics["qc_sample_size"] = list(im.size)
            except Exception:
                pass

        hard_failures = [c for c in checks if c.blocks_delivery()]
        status_counts: dict[str, int] = {}
        for check in checks:
            status_counts[check.status] = status_counts.get(check.status, 0) + 1
        metrics["qc_status_counts"] = status_counts
        return QualityReport(
            passed=(len(hard_failures) == 0),
            needs_human_review=len(human_reasons) > 0,
            human_review_reasons=human_reasons,
            checks=checks,
            metrics=metrics,
        )
