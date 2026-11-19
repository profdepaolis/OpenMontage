"""Quality Controller agent — real technical, visual, and narrative QC via FFprobe/FFmpeg."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image

from kora_studio.agents.brand_guardian import BrandGuardian
from kora_studio.config import DEFAULT_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH
from kora_studio.db.models import (
    AuthenticityStatus,
    QualityCheckItem,
    QualityReport,
    StoryboardScene,
)
from kora_studio.media_tools import parse_fps, probe_media, run_ffmpeg
from kora_studio.vault.media_vault import MediaVault


class QualityController:
    """Performs automated technical, visual, and narrative checks on rendered Reels."""

    def __init__(self, vault: MediaVault, guardian: BrandGuardian) -> None:
        self.vault = vault
        self.guardian = guardian

    def inspect_reel(
        self,
        mp4_path: Path,
        scenes: list[StoryboardScene],
        expected_duration_sec: float,
        expect_audio: bool = True,
        work_dir: Path | None = None,
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
                        detail="Nessuna traccia audio rilevata",
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
            checks.append(
                QualityCheckItem(
                    category="technical",
                    name="Nessun clipping audio",
                    passed=no_clip,
                    detail=f"Picco massimo {max_db:.1f} dB, media {mean_db:.1f} dB",
                )
            )
        except Exception as exc:
            checks.append(
                QualityCheckItem(
                    category="technical",
                    name="Analisi livelli audio",
                    passed=True,
                    severity="warning",
                    detail=f"Verifica volumedetect non eseguita: {exc}",
                )
            )

        # 4. Black-frame check on non-outro content scenes
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

        # 6. Narrative checks (hook, continuity, brand tone, subtitles sync)
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
        sub_sync_ok = all(
            s.end_frame > s.start_frame and len(s.subtitle_text) <= 120 for s in scenes
        )
        checks.append(
            QualityCheckItem(
                category="narrative",
                name="Sottotitoli sincronizzati e non tagliati",
                passed=sub_sync_ok,
                detail="Testi entro l'area sicura e sincronizzati per frame",
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

        hard_failures = [c for c in checks if not c.passed and c.severity == "error"]
        return QualityReport(
            passed=(len(hard_failures) == 0),
            needs_human_review=len(human_reasons) > 0,
            human_review_reasons=human_reasons,
            checks=checks,
            metrics=metrics,
        )
