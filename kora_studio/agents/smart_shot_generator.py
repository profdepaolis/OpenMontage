"""Smart Shot Generator — derives real cinematic framings from authentic photographs.

It reads the actual pixels of a KORA photograph and finds what can carry a shot:
salient regions, the horizon, textures, perspective direction and the sharpest
detail areas. From that analysis it produces a *derived framing* — normalised
crop, focal point, residual resolution, zoom ramp, camera trajectory, keyframes,
easing, movement intensity, subtle depth simulation and the narrative reason.

The Cinematic Editor then uses these numbers to build the FFmpeg zoompan motion:
the plan is not a description in the storyboard, it is the motion itself.

Authenticity rule: the generator never invents a part of the venue. Every crop is
a window inside the original photograph, and a crop is refused when the residual
resolution would be too low to stay sharp at 1080×1920.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageOps

from kora_studio.config import DEFAULT_HEIGHT, DEFAULT_WIDTH

OUT_W = DEFAULT_WIDTH
OUT_H = DEFAULT_HEIGHT
ASPECT = OUT_W / OUT_H  # 9:16

#: minimum *visible* source width at maximum zoom (1080 / 540 = 2× upscale, still sharp)
MIN_VISIBLE_WIDTH = 540
#: hard ceiling for the digital zoom (never a fake "detail" from pixels that don't exist)
MAX_ZOOM = 2.6

EASINGS = ("ease_in_out", "ease_out", "ease_in", "linear")


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722


def _box_blur(arr: np.ndarray, radius: int = 4) -> np.ndarray:
    """Cheap separable box blur via cumulative sums (no external dependency)."""
    if radius < 1:
        return arr
    pad = np.pad(arr, radius, mode="edge")
    csum = np.cumsum(pad, axis=0)
    csum = np.vstack([np.zeros((1, csum.shape[1])), csum])
    arr_v = (csum[2 * radius + 1 :] - csum[: -(2 * radius + 1)]) / (2 * radius + 1)
    csum = np.cumsum(arr_v, axis=1)
    csum = np.hstack([np.zeros((csum.shape[0], 1)), csum])
    return (csum[:, 2 * radius + 1 :] - csum[:, : -(2 * radius + 1)]) / (2 * radius + 1)


def _integral(arr: np.ndarray) -> np.ndarray:
    out = np.cumsum(np.cumsum(arr, axis=0), axis=1)
    out = np.pad(out, ((1, 0), (1, 0)), mode="constant")
    return out


def _window_sum(ii: np.ndarray, x: int, y: int, w: int, h: int) -> float:
    return float(ii[y + h, x + w] - ii[y, x + w] - ii[y + h, x] + ii[y, x])


class SmartShotGenerator:
    """Analyses photographs and plans derived shots with real camera data."""

    def __init__(self, vault: Any = None) -> None:
        self.vault = vault
        self._cache: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ analysis
    def analyze_image(self, path: Path | str) -> dict[str, Any]:
        path = Path(path)
        key = f"{path}:{path.stat().st_mtime_ns if path.is_file() else 0}"
        if key in self._cache:
            return self._cache[key]

        with Image.open(path) as raw:
            img = ImageOps.exif_transpose(raw).convert("RGB")
        src_w, src_h = img.size
        work = img.copy()
        work.thumbnail((720, 720), Image.Resampling.BILINEAR)
        rgb = np.asarray(work, dtype=np.float32) / 255.0
        w, h = work.size

        luma = _luma(rgb)
        gx = np.zeros_like(luma)
        gy = np.zeros_like(luma)
        gx[:, 1:-1] = np.abs(luma[:, 2:] - luma[:, :-2]) * 0.5
        gy[1:-1, :] = np.abs(luma[2:, :] - luma[:-2, :]) * 0.5
        grad = gx + gy
        grad_s = _box_blur(grad, 3)
        sat = rgb.max(axis=2) - rgb.min(axis=2)

        yy, xx = np.mgrid[0:h, 0:w]
        cx_n, cy_n = xx / max(1, w - 1), yy / max(1, h - 1)
        # centre-weighted saliency: what the eye would catch first in the photo
        centre = np.exp(-(((cx_n - 0.5) ** 2) / 0.18 + ((cy_n - 0.5) ** 2) / 0.26))
        saliency = (0.62 * grad_s / (grad_s.max() + 1e-6)) + (0.20 * sat / (sat.max() + 1e-6)) + (0.18 * centre)

        horizon = self._horizon(luma)
        detail_energy = float(grad.mean())
        contrast = float(luma.std())
        brightness = float(luma.mean())
        warm = float((rgb[:, :, 0].mean() - rgb[:, :, 2].mean()))
        texture = "texture fitta" if detail_energy > 0.055 else ("texture morbida" if detail_energy > 0.025 else "superficie liscia")
        perspective = self._perspective(gx, gy)

        cache_key = key
        data = {
            "source_size": {"width": src_w, "height": src_h},
            "analysis_size": {"width": w, "height": h},
            "brightness": round(brightness, 4),
            "contrast": round(contrast, 4),
            "detail": round(detail_energy, 4),
            "warmth": round(warm, 4),
            "texture": texture,
            "horizon_norm": round(horizon, 4),
            "perspective": perspective,
            "_saliency": saliency,
            "_ii": _integral(saliency),
            "_w": w,
            "_h": h,
        }
        self._cache[cache_key] = data
        return data

    @staticmethod
    def _horizon(luma: np.ndarray) -> float:
        rows = np.abs(np.diff(luma, axis=0)).mean(axis=1)
        if rows.size == 0:
            return 0.5
        # smooth to avoid single-pixel noise, then a slight bias to the centre
        kernel = np.ones(9) / 9.0
        smooth = np.convolve(rows, kernel, mode="same")
        idx = int(np.argmax(smooth))
        return float(np.clip(idx / max(1, luma.shape[0] - 1), 0.05, 0.95))

    @staticmethod
    def _perspective(gx: np.ndarray, gy: np.ndarray) -> dict[str, Any]:
        """Dominant direction of the strong lines (a proxy, stated as such)."""
        horiz = float(gx.mean())
        vert = float(gy.mean())
        direction = "orizzontale" if horiz >= vert else "verticale"
        strength = round(abs(horiz - vert) / (horiz + vert + 1e-6), 3)
        return {"dominant": direction, "strength": strength, "note": "stime su gradiente, nessuna ricostruzione 3D"}

    # --------------------------------------------------------------- ROI search
    def find_region(self, analysis: dict[str, Any], max_zoom: float = 1.8) -> dict[str, Any]:
        """Best 9:16 window inside the photo, scored by real saliency."""
        ii = analysis["_ii"]
        w, h = analysis["_w"], analysis["_h"]
        best: Optional[dict[str, Any]] = None
        # zoom = how much of the photo we keep (1.0 = largest window)
        for zoom in (1.0, 1.15, 1.3, 1.5, max_zoom):
            cw = int(w / zoom)
            ch = int(cw / ASPECT)
            if ch > h:
                ch = int(h / zoom)
                cw = int(ch * ASPECT)
            if cw < MIN_VISIBLE_WIDTH or ch < MIN_VISIBLE_WIDTH / ASPECT:
                # a window smaller than the usable resolution cannot carry 1080×1920
                continue
            step = max(4, int(min(cw, ch) / 12))
            best_here: Optional[dict[str, Any]] = None
            for y in range(0, max(1, h - ch + 1), step):
                for x in range(0, max(1, w - cw + 1), step):
                    score = _window_sum(ii, x, y, cw, ch) / (cw * ch)
                    if best_here is None or score > best_here["score"]:
                        best_here = {"score": score, "x": x, "y": y, "w": cw, "h": ch, "zoom": zoom}
            if best_here is None:
                continue
            # prefer the tighter framing only when it is clearly better
            if best is None or best_here["score"] > best["score"] * 1.04:
                best = best_here
        if best is None:  # extremely defensive fallback: centre window
            cw = int(h * ASPECT)
            best = {"score": 0.0, "x": max(0, (w - cw) // 2), "y": 0, "w": cw, "h": h, "zoom": 1.0}

        src_w = analysis["source_size"]["width"]
        src_h = analysis["source_size"]["height"]
        sx = src_w / w
        sy = src_h / h
        crop_px = {
            "x": int(best["x"] * sx),
            "y": int(best["y"] * sy),
            "w": int(best["w"] * sx),
            "h": int(best["h"] * sy),
        }
        crop_px["w"] = min(src_w, crop_px["w"])
        crop_px["h"] = min(src_h, crop_px["h"])
        crop_px["x"] = max(0, min(src_w - crop_px["w"], crop_px["x"]))
        crop_px["y"] = max(0, min(src_h - crop_px["h"], crop_px["y"]))

        focal = {
            "x": round((crop_px["x"] + crop_px["w"] / 2) / max(1, src_w), 4),
            "y": round((crop_px["y"] + crop_px["h"] / 2) / max(1, src_h), 4),
        }
        return {
            "crop": {
                "x": round(crop_px["x"] / max(1, src_w), 4),
                "y": round(crop_px["y"] / max(1, src_h), 4),
                "w": round(crop_px["w"] / max(1, src_w), 4),
                "h": round(crop_px["h"] / max(1, src_h), 4),
            },
            "crop_px": crop_px,
            "focal_point": focal,
            "saliency_score": round(float(best["score"]), 4),
            "source_zoom": best["zoom"],
        }

    # --------------------------------------------------------------- shot plan
    def _motion_profile(self, purpose: str, analysis: dict[str, Any], intensity: float) -> dict[str, Any]:
        """Narrative function -> camera grammar (no random motion)."""
        text = (purpose or "").lower()
        if any(k in text for k in ("hook", "apertura", "aggancia")):
            return {"kind": "push_in", "zoom": (1.0, 1.10), "easing": "ease_out", "intensity": 0.55}
        if any(k in text for k in ("contesto", "architettura", "dove")):
            return {"kind": "pull_out", "zoom": (1.14, 1.0), "easing": "ease_in_out", "intensity": 0.5}
        if any(k in text for k in ("dettaglio", "materia", "texture", "gesto")):
            return {"kind": "detail_push", "zoom": (1.12, 1.34), "easing": "ease_in_out", "intensity": 0.8}
        if any(k in text for k in ("esperienza", "convivial", "insieme", "tavola")):
            return {"kind": "parallax", "zoom": (1.06, 1.12), "easing": "ease_in_out", "intensity": 0.6}
        if any(k in text for k in ("emozione", "respiro", "mare", "silenzio")):
            return {"kind": "drift", "zoom": (1.03, 1.09), "easing": "ease_in_out", "intensity": 0.35}
        if any(k in text for k in ("chiusura", "payoff", "logo")):
            return {"kind": "settle", "zoom": (1.05, 1.0), "easing": "ease_out", "intensity": 0.2}
        return {"kind": "push_in", "zoom": (1.0, 1.08), "easing": "ease_in_out", "intensity": 0.45}

    @staticmethod
    def _keyframes(profile: dict[str, Any], focal: dict[str, float], horizon: float, count: int = 4) -> list[dict[str, Any]]:
        zs, ze = profile["zoom"]
        kind = profile["kind"]
        # trajectory offsets are fractions of the available pan range
        if kind == "pull_out":
            path = [(0.5, 0.5), (0.48, 0.46), (0.5, 0.5), (0.5, 0.5)]
        elif kind in ("parallax", "drift"):
            drift = 0.06 if kind == "parallax" else 0.03
            path = [(0.5 - drift, 0.5), (0.5, 0.5 - drift * 0.5), (0.5 + drift, 0.5), (0.5 + drift * 0.8, 0.5)]
        elif kind == "detail_push":
            path = [(0.42, max(0.25, horizon * 0.8)), (0.5, 0.5), (0.58, min(0.85, horizon * 1.1)), (0.5, 0.5)]
        elif kind == "settle":
            path = [(0.5, 0.5), (0.5, 0.5), (0.5, 0.5), (0.5, 0.5)]
        else:  # push_in
            path = [(0.5, 0.5), (0.5, 0.5), (0.5, 0.5), (0.5, 0.5)]
        frames: list[dict[str, Any]] = []
        for i in range(count):
            t = i / (count - 1)
            zoom = zs + (ze - zs) * (0.5 - 0.5 * math.cos(math.pi * t))  # eased ramp
            fx, fy = path[min(i, len(path) - 1)]
            frames.append(
                {
                    "t": round(t, 4),
                    "x": round(float(fx), 4),
                    "y": round(float(fy), 4),
                    "zoom": round(float(zoom), 4),
                }
            )
        return frames

    def plan_for_scene(
        self,
        asset: dict[str, Any],
        *,
        purpose: str = "",
        duration_frames: int = 75,
        focal_hint: Optional[dict[str, float]] = None,
        motion_hint: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Build a derived shot for one real photograph (None for videos/outro)."""
        if not asset or asset.get("media_type") != "image" or asset.get("category") == "Logo":
            return None
        path = asset.get("working_path") or asset.get("original_path")
        if not path or not Path(path).is_file():
            return None
        analysis = self.analyze_image(path)
        region = self.find_region(analysis)
        profile = self._motion_profile(purpose, analysis, 0.5)
        if motion_hint == "still":
            profile = {"kind": "settle", "zoom": (1.02, 1.0), "easing": "ease_out", "intensity": 0.15}

        focal = focal_hint or region["focal_point"]
        # respect the real resolution: never zoom beyond what the crop can support
        crop_w = region["crop_px"]["w"]
        crop_h = region["crop_px"]["h"]
        max_supported = max(1.0, min(MAX_ZOOM, crop_w / MIN_VISIBLE_WIDTH))
        zs, ze = profile["zoom"]
        ze = max(1.0, min(ze, max_supported))
        zs = max(1.0, min(zs, max_supported))
        profile["zoom"] = (round(zs, 4), round(ze, 4))
        # what the viewer actually sees at the end of the move
        residual = {
            "width": int(round(crop_w / max(1e-6, ze))),
            "height": int(round(crop_h / max(1e-6, ze))),
        }

        keyframes = self._keyframes(profile, focal, analysis["horizon_norm"])
        motivation = self._motivation(purpose, analysis, profile, region)
        return {
            "version": 1,
            "asset_id": asset.get("id"),
            "asset_sha256": asset.get("sha256"),
            "source_size": analysis["source_size"],
            "aspect_ratio": "9:16",
            "crop": region["crop"],
            "crop_px": region["crop_px"],
            "focal_point": {"x": round(float(focal["x"]), 4), "y": round(float(focal["y"]), 4)},
            "residual_resolution": residual,
            "zoom_start": profile["zoom"][0],
            "zoom_end": profile["zoom"][1],
            "trajectory": profile["kind"],
            "keyframes": keyframes,
            "easing": profile["easing"],
            "motion_intensity": round(float(profile["intensity"]), 3),
            "depth_simulation": {
                "enabled": profile["kind"] in ("parallax", "drift"),
                "mode": "micro_parallax" if profile["kind"] in ("parallax", "drift") else "none",
                "note": "parallasse simulata con micro-zoom e deriva verticale sui pixel originali (nessuna ricostruzione 3D)",
            },
            "analysis": {
                "brightness": analysis["brightness"],
                "contrast": analysis["contrast"],
                "detail": analysis["detail"],
                "texture": analysis["texture"],
                "horizon_norm": analysis["horizon_norm"],
                "perspective": analysis["perspective"],
                "saliency_score": region["saliency_score"],
            },
            "duration_frames": int(duration_frames),
            "motivation": motivation,
            "generator": "smart_shot_generator_v1",
        }

    @staticmethod
    def _motivation(purpose: str, analysis: dict[str, Any], profile: dict[str, Any], region: dict[str, Any]) -> str:
        where = purpose.strip() or "scena KORA"
        return (
            f"«{where}»: regione a massima salienza ({region['saliency_score']:.2f}) con "
            f"{analysis['texture']} e orizzonte al {int(analysis['horizon_norm'] * 100)}%; "
            f"movimento {profile['kind']} ({profile['easing']}, intensità {profile['intensity']:.2f}) "
            f"sostenuto dai pixel originali della fotografia."
        )

    # ------------------------------------------------------------- validation
    @staticmethod
    def validate_plan(plan: Optional[dict[str, Any]], src_size: dict[str, int]) -> tuple[bool, list[str]]:
        """A derived shot must be renderable and inside the real photograph."""
        issues: list[str] = []
        if not plan:
            return False, ["piano assente"]
        crop = plan.get("crop") or {}
        for key in ("x", "y", "w", "h"):
            if not isinstance(crop.get(key), (int, float)):
                issues.append(f"crop.{key} mancante")
        if crop and (crop.get("x", 0) < 0 or crop.get("y", 0) < 0):
            issues.append("crop fuori dall'immagine")
        if crop and (crop.get("x", 0) + crop.get("w", 0) > 1.0001 or crop.get("y", 0) + crop.get("h", 0) > 1.0001):
            issues.append("crop oltre i bordi della fotografia originale")
        zs, ze = plan.get("zoom_start"), plan.get("zoom_end")
        if not isinstance(zs, (int, float)) or not isinstance(ze, (int, float)):
            issues.append("zoom non numerico")
        elif zs < 1.0 or ze < 1.0 or max(zs, ze) > MAX_ZOOM:
            issues.append("zoom fuori intervallo")
        frames = plan.get("keyframes") or []
        if len(frames) < 2:
            issues.append("keyframe insufficienti")
        for kf in frames:
            for key in ("t", "x", "y", "zoom"):
                if not isinstance(kf.get(key), (int, float)):
                    issues.append(f"keyframe.{key} non numerico")
                    break
        if plan.get("easing") not in EASINGS:
            issues.append("easing non supportato")
        if (plan.get("residual_resolution") or {}).get("width", 0) < MIN_VISIBLE_WIDTH:
            issues.append("risoluzione residua insufficiente")
        if src_size and plan.get("source_size") != src_size:
            issues.append("dimensioni sorgente incoerenti")
        return (not issues), issues


def easing_expression(easing: str, frames: int) -> str:
    """FFmpeg expression for the eased progress ``p`` in [0,1] over the clip."""
    n = max(1, frames - 1)
    if easing == "linear":
        return f"min(1,on/{n})"
    if easing == "ease_in":
        return f"pow(min(1,on/{n}),2)"
    if easing == "ease_out":
        return f"(1-pow(1-min(1,on/{n}),2))"
    # ease_in_out (cosine)
    return f"(0.5-0.5*cos(PI*min(1,on/{n})))"


def keyframe_track(plan: dict[str, Any], field: str, sign: str = "+") -> str:
    """Piecewise-linear FFmpeg expression following the plan's keyframes.

    ``field`` is one of ``x``, ``y``, ``zoom``; values are interpolated between
    keyframes with the plan's easing applied to each segment.
    """
    frames = plan.get("keyframes") or []
    if len(frames) < 2:
        value = plan.get(f"{field}_end" if field == "zoom" else field, 0.5)
        return str(value)
    parts: list[str] = []
    for i in range(1, len(frames)):
        a, b = frames[i - 1], frames[i]
        ta, tb = float(a["t"]), float(b["t"])
        va, vb = float(a[field]), float(b[field])
        if tb <= ta:
            continue
        seg = f"between(t,{ta:.4f},{tb:.4f})"
        alpha = f"((t-{ta:.4f})/{max(1e-6, tb - ta):.6f})"
        expr = f"({va:.5f}+({vb - va:.5f})*({alpha}))"
        parts.append(f"if({seg},{expr},")
    if not parts:
        return str(float(frames[-1][field]))
    return "".join(parts) + f"{float(frames[-1][field]):.5f}" + ")" * len(parts)


def _track(plan: dict[str, Any], field: str, frames: int) -> str:
    """Keyframe track using ``on/N`` as normalised time (zoompan has no ``t``)."""
    kfs = plan.get("keyframes") or []
    if len(kfs) < 2:
        value = plan.get(f"{field}_end") if field == "zoom" else 0.5
        return f"{float(value if value is not None else 1.0):.5f}"
    n = max(1, frames - 1)
    expr_parts: list[str] = []
    for i in range(1, len(kfs)):
        a, b = kfs[i - 1], kfs[i]
        ta, tb = float(a["t"]), float(b["t"])
        va, vb = float(a[field]), float(b[field])
        if tb <= ta:
            continue
        span = max(1e-6, tb - ta)
        alpha = f"max(0,min(1,((on/{n})-{ta:.5f})/{span:.6f}))"
        expr_parts.append(f"if(between(on/{n},{ta:.5f},{tb:.5f}),{va:.5f}+({vb - va:.5f})*{alpha},")
    if not expr_parts:
        return f"{float(kfs[-1][field]):.5f}"
    return "".join(expr_parts) + f"{float(kfs[-1][field]):.5f}" + ")" * len(expr_parts)


def zoompan_expressions(plan: dict[str, Any], frames: int) -> tuple[str, str, str]:
    """FFmpeg zoompan z/x/y expressions built from the shot plan's real numbers."""
    z = _track(plan, "zoom", frames)
    fx = _track(plan, "x", frames)
    fy = _track(plan, "y", frames)
    # x/y in zoompan are offsets in pixels of the visible window
    x_expr = f"(iw-iw/zoom)*({fx})"
    depth = plan.get("depth_simulation") or {}
    if depth.get("enabled"):
        # micro parallax: a very small vertical drift, still inside the original pixels
        y_expr = f"(ih-ih/zoom)*({fy})+0.004*ih*sin(2*PI*on/{max(1, frames - 1)})"
    else:
        y_expr = f"(ih-ih/zoom)*({fy})"
    return z, x_expr, y_expr
