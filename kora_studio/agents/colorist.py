"""Colorist agent — per-scene pre-analysis, normalization, and KORA Mediterranean grading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from kora_studio.media_tools import run_ffmpeg


class Colorist:
    """Analyzes each clip/image before grading to ensure coherent KORA Mediterranean tones."""

    def analyze_frame(self, image_path: Path) -> dict[str, float]:
        with Image.open(image_path) as im:
            rgb = ImageOps.exif_transpose(im).convert("RGB").resize((128, 128))
            arr = np.asarray(rgb, dtype=np.float32)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        luma = 0.299 * r + 0.587 * g + 0.114 * b
        mean_luma = float(np.mean(luma))
        std_luma = float(np.std(luma))
        warmth = float(np.mean(r - b))
        maxc = np.max(arr, axis=2)
        minc = np.min(arr, axis=2)
        sat = float(np.mean(np.where(maxc > 0, (maxc - minc) / (maxc + 1e-5), 0.0)))
        blue_dom = float(np.mean((b > r + 15) & (b >= g)))
        return {
            "mean_luma": round(mean_luma, 2),
            "contrast_std": round(std_luma, 2),
            "warmth": round(warmth, 2),
            "saturation": round(sat, 3),
            "blue_ratio": round(blue_dom, 3),
        }

    def analyze_video_sample(self, video_path: Path, tmp_frame: Path) -> dict[str, float]:
        tmp_frame.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(
            ["-y", "-loglevel", "error", "-ss", "0.4", "-i", str(video_path), "-frames:v", "1", str(tmp_frame)],
            timeout=30,
        )
        if tmp_frame.is_file():
            return self.analyze_frame(tmp_frame)
        return {"mean_luma": 128.0, "contrast_std": 48.0, "warmth": 8.0, "saturation": 0.25, "blue_ratio": 0.1}

    def compute_adaptive_grade(self, metrics: dict[str, float], target_warmth: float = 12.0) -> dict[str, Any]:
        """Compute scene-normalized, non-uniform KORA Mediterranean color parameters.

        Target:
        - Natural whites, credible skin tones
        - Sea not artificially saturated
        - Warm realistic wood, soft shadows, moderate cinematic contrast
        """
        mean_luma = metrics.get("mean_luma", 128.0)
        contrast_std = metrics.get("contrast_std", 48.0)
        warmth = metrics.get("warmth", 10.0)
        sat = metrics.get("saturation", 0.25)
        blue_ratio = metrics.get("blue_ratio", 0.05)

        # 1. Exposure normalization toward balanced 126 luma
        brightness = max(-0.08, min(0.08, (126.0 - mean_luma) / 520.0))
        # Moderate cinematic contrast without crushing soft shadows
        contrast = max(0.96, min(1.08, 1.02 + (48.0 - contrast_std) * 0.0012))
        gamma = max(0.94, min(1.06, 1.0 - brightness * 0.3))

        # 2. Restrained Mediterranean saturation (protect sea from neon saturation)
        if blue_ratio > 0.22:
            saturation = max(0.92, min(1.03, 0.98 - (sat - 0.28) * 0.25))
        else:
            saturation = max(0.95, min(1.08, 1.03 - (sat - 0.28) * 0.20))

        # 3. Gentle warm sand / golden light balance only if cool cast detected
        warm_delta = max(-0.04, min(0.05, (target_warmth - warmth) / 450.0))
        rs = round(warm_delta * 0.6, 4)
        bs = round(-warm_delta * 0.6, 4)
        rm = round(warm_delta * 0.8, 4)
        bm = round(-warm_delta * 0.5, 4)
        rh = round(warm_delta * 0.5, 4)
        bh = round(-warm_delta * 0.4, 4)

        filter_str = (
            f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}:gamma={gamma:.4f}:saturation={saturation:.4f},"
            f"colorbalance=rs={rs}:bs={bs}:rm={rm}:bm={bm}:rh={rh}:bh={bh}"
        )
        return {
            "metrics": metrics,
            "filter": filter_str,
            "adjustments": {
                "brightness": round(brightness, 4),
                "contrast": round(contrast, 4),
                "gamma": round(gamma, 4),
                "saturation": round(saturation, 4),
                "warm_shift": round(warm_delta, 4),
            },
        }
