"""Cinematic Editor — deterministic 25fps 9:16 FFmpeg & Remotion composition engine."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

from kora_studio.agents.ai_media_lab import AIMediaLab
from kora_studio.agents.colorist import Colorist
from kora_studio.agents.hyperframes_captions import (
    CAPTION_STYLES,
    build_caption_segments_for_scene,
    build_hyperframes_vf_filters,
    export_hyperframes_workspace,
    normalize_hyperframes_config,
    render_title_card_png,
)
from kora_studio.config import ASSETS_KORA_DIR, DEFAULT_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH
from kora_studio.db.models import EditingPreset, StoryboardScene
from kora_studio.media_tools import check_remotion_runtime, run_ffmpeg
from kora_studio.vault.media_vault import MediaVault

# --------------------------------------------------------------- 3.6.2 velocità
# Il render deve adattarsi alla macchina, non a un numero fisso: su un Mac Intel
# 718 frame a 1080x1920 con otto sottotitoli non stanno in 240 secondi.
X264_PRESET = os.environ.get("KORA_X264_PRESET", "veryfast")


def render_timeout_seconds(
    frames: int, *, per_frame: float = 1.6, minimum: float = 600.0
) -> int:
    """Tempo massimo concesso al montaggio, proporzionale al lavoro richiesto.

    `KORA_RENDER_TIMEOUT_SEC` (secondi) ha la precedenza: serve a chi ha una
    macchina molto lenta o vuole un limite più stretto.
    """
    override = os.environ.get("KORA_RENDER_TIMEOUT_SEC")
    if override:
        try:
            return max(60, int(float(override)))
        except (TypeError, ValueError):
            pass
    return int(max(minimum, round(max(1, frames) * per_frame)))


PRESET_DEFINITIONS: dict[str, dict[str, Any]] = {
    EditingPreset.CINEMATIC_IDENTITY.value: {
        "name": EditingPreset.CINEMATIC_IDENTITY.value,
        "description": "Ritmo elegante, movimenti lenti, racconto emozionale.",
        "transition_frames": 10,
        "default_warmth": 13.0,
        "subtitle_style": "serif_ivory",
    },
    EditingPreset.EXPERIENCE.value: {
        "name": EditingPreset.EXPERIENCE.value,
        "description": "Ritmo leggermente più dinamico, gesti e interazioni reali.",
        "transition_frames": 8,
        "default_warmth": 11.0,
        "subtitle_style": "serif_ivory",
    },
    EditingPreset.SEASONAL.value: {
        "name": EditingPreset.SEASONAL.value,
        "description": "Comunicazione informativa integrata con immagini cinematografiche.",
        "transition_frames": 8,
        "default_warmth": 10.0,
        "subtitle_style": "clean_sans",
    },
    EditingPreset.EDITORIAL.value: {
        "name": EditingPreset.EDITORIAL.value,
        "description": "Narrazione visiva con testo minimo e attenzione alla composizione.",
        "transition_frames": 12,
        "default_warmth": 12.0,
        "subtitle_style": "serif_minimal",
    },
    EditingPreset.CUSTOM.value: {
        "name": EditingPreset.CUSTOM.value,
        "description": "Preset personalizzato da istruzione creativa dell'utente.",
        "transition_frames": 10,
        "default_warmth": 12.0,
        "subtitle_style": "serif_ivory",
    },
}


def _load_font(size: int, italic: bool = False, sans: bool = False) -> ImageFont.FreeTypeFont:
    candidates = []
    if sans:
        candidates.append(ASSETS_KORA_DIR / "SpaceGrotesk.ttf")
        candidates.append(Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    elif italic:
        candidates.append(ASSETS_KORA_DIR / "PlayfairDisplay-Italic.ttf")
        candidates.append(ASSETS_KORA_DIR / "PlayfairDisplay.ttf")
        candidates.append(Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"))
    else:
        candidates.append(ASSETS_KORA_DIR / "PlayfairDisplay.ttf")
        candidates.append(Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"))

    for c in candidates:
        if c.is_file():
            try:
                return ImageFont.truetype(str(c), size=size)
            except Exception:
                continue
    return ImageFont.load_default()  # type: ignore


class CinematicEditor:
    """Deterministic frame-exact Reel builder with scene cache for selective re-rendering."""

    def __init__(
        self,
        vault: MediaVault,
        media_lab: AIMediaLab,
        colorist: Optional[Colorist] = None,
    ) -> None:
        self.vault = vault
        self.media_lab = media_lab
        self.colorist = colorist or Colorist()

    def _scene_cache_key(self, scene: StoryboardScene, asset_sha: str, warmth: float) -> str:
        payload = json.dumps(
            {
                "asset_id": scene.selected_asset_id,
                "asset_sha": asset_sha,
                "duration_frames": scene.duration_frames,
                "camera_motion": scene.camera_motion,
                "motion_intensity": getattr(scene, "motion_intensity", 1.0),
                "motion_easing": getattr(scene, "motion_easing", "ease_in_out"),
                "motion_speed": getattr(scene, "motion_speed", 1.0),
                "composition": getattr(scene, "composition", "regola_terzi"),
                "caption_style": scene.caption_style,
                "caption_overrides": hashlib.sha256(
                    json.dumps(scene.caption_overrides or {}, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()[:8],
                "title_text": scene.title_text,
                "crop_focus": scene.crop_focus,
                "shot_plan": hashlib.sha256(
                    json.dumps(scene.shot_plan or {}, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()[:12],
                "is_outro": scene.is_outro,
                "warmth": round(warmth, 1),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def render_logo_outro_frame(
        self,
        logo_path: Path,
        out_png: Path,
        payoff: str = "The Art of Being",
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
    ) -> Path:
        """Reusable Logo Outro component: authentic LOGO KORA.PNG on deep dark canvas + payoff."""
        if not logo_path.is_file():
            raise ValueError(
                "Logo originale KORA mancante. Caricare LOGO KORA.PNG prima di generare l'outro."
            )
        canvas = Image.new("RGB", (width, height), (11, 12, 11))  # #0B0C0B
        with Image.open(logo_path) as raw_logo:
            logo = ImageOps.exif_transpose(raw_logo).convert("RGBA")
            # Preserve exact aspect ratio, never stretch or distort
            target_w = int(width * 0.56)
            ratio = target_w / max(1, logo.width)
            target_h = max(1, int(logo.height * ratio))
            logo_resized = logo.resize((target_w, target_h), Image.Resampling.LANCZOS)

            # Defringe light halo if original logo has light-matted or opaque white background
            import numpy as np

            rgba_arr = np.asarray(logo_resized, dtype=np.float32).copy()
            alpha_min = float(rgba_arr[:, :, 3].min())
            if alpha_min >= 250.0:
                # White background PNG: derive clean alpha from distance to white
                whiteness = np.min(rgba_arr[:, :, :3], axis=2)
                new_alpha = np.clip((242.0 - whiteness) / 70.0, 0.0, 1.0) * 255.0
                rgba_arr[:, :, 3] = new_alpha
            # Color-defringe semi-transparent edge pixels toward KORA gold (149, 122, 72)
            edge_mask = (rgba_arr[:, :, 3] > 5) & (rgba_arr[:, :, 3] < 245) & (rgba_arr[:, :, :3].mean(axis=2) > 155)
            if np.any(edge_mask):
                rgba_arr[edge_mask, 0] = 149.0
                rgba_arr[edge_mask, 1] = 122.0
                rgba_arr[edge_mask, 2] = 72.0
            logo_resized = Image.fromarray(np.clip(rgba_arr, 0, 255).astype(np.uint8), mode="RGBA")

            lx = (width - target_w) // 2
            ly = int(height * 0.43) - target_h // 2
            canvas.paste(logo_resized, (lx, ly), logo_resized)

        draw = ImageDraw.Draw(canvas)
        font = _load_font(44, italic=True)
        bbox = draw.textbbox((0, 0), payoff, font=font)
        tw = bbox[2] - bbox[0]
        tx = (width - tw) // 2
        ty = int(height * 0.56)
        draw.text((tx, ty), payoff, fill=(245, 240, 230), font=font)  # Warm Ivory #F5F0E6

        # Subtle location footer inside safe zone
        sub_font = _load_font(22, sans=True)
        loc_text = "POLICORO · MEDITERRANEAN SEA"
        lb = draw.textbbox((0, 0), loc_text, font=sub_font)
        draw.text(
            ((width - (lb[2] - lb[0])) // 2, int(height * 0.62)),
            loc_text,
            fill=(178, 164, 138),
            font=sub_font,
        )

        out_png.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_png, "PNG")
        return out_png

    def _render_subtitle_overlay_png(
        self,
        text: str,
        out_png: Path,
        style: str = "serif_ivory",
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        title: str = "",
    ) -> Path:
        """Render safe-area 9:16 transparent subtitle overlay with adaptive soft scrim."""
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        title = (title or "").strip()
        if not text.strip() and not title:
            overlay.save(out_png, "PNG")
            return out_png

        if title:
            tdraw = ImageDraw.Draw(overlay)
            tfont = _load_font(max(24, int(width * 0.052)))
            tspaced = " ".join(title.upper()[:40])
            tbb = tdraw.textbbox((0, 0), tspaced, font=tfont)
            tw = tbb[2] - tbb[0]
            tx = (width - tw) // 2
            ty = int(height * 0.60)
            tdraw.text((tx + 1, ty + 2), tspaced, fill=(0, 0, 0, 150), font=tfont)
            tdraw.text((tx, ty), tspaced, fill=(226, 208, 168, 255), font=tfont)

        if not text.strip():
            out_png.parent.mkdir(parents=True, exist_ok=True)
            overlay.save(out_png, "PNG")
            return out_png

        draw = ImageDraw.Draw(overlay)
        is_sans = style in ("clean_sans", "serif_minimal")
        font = _load_font(44 if not is_sans else 40, sans=is_sans)
        wrapped = textwrap.fill(text.strip(), width=28)
        lines = wrapped.splitlines()[:3]

        line_heights: list[int] = []
        line_widths: list[int] = []
        for line in lines:
            bb = draw.textbbox((0, 0), line, font=font)
            line_widths.append(bb[2] - bb[0])
            line_heights.append((bb[3] - bb[1]) + 14)

        total_h = sum(line_heights)
        max_w = max(line_widths) if line_widths else 0
        # Safe area: y ~ 74% (above IG caption & CTA buttons, clear of right icons)
        pad_x, pad_y = 34, 20
        box_w = min(width - 140, max_w + pad_x * 2)
        box_h = total_h + pad_y * 2
        box_x = (width - box_w) // 2
        box_y = int(height * 0.74) - box_h // 2

        draw.rounded_rectangle(
            [box_x, box_y, box_x + box_w, box_y + box_h],
            radius=18,
            fill=(15, 16, 15, 148),
        )

        cur_y = box_y + pad_y
        for idx, line in enumerate(lines):
            lw = line_widths[idx]
            lx = (width - lw) // 2
            # Subtle shadow + crisp warm ivory text
            draw.text((lx + 1, cur_y + 2), line, fill=(0, 0, 0, 170), font=font)
            draw.text((lx, cur_y), line, fill=(247, 243, 234, 255), font=font)
            cur_y += line_heights[idx]

        out_png.parent.mkdir(parents=True, exist_ok=True)
        overlay.save(out_png, "PNG")
        return out_png

    def _render_scene_clip(
        self,
        scene: StoryboardScene,
        preset_cfg: dict[str, Any],
        scenes_dir: Path,
        fps: int = DEFAULT_FPS,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        crf: int = 18,
    ) -> tuple[Path, dict[str, Any]]:
        """Render or reuse cached clip for a single scene (enables fast selective re-rendering)."""
        scenes_dir.mkdir(parents=True, exist_ok=True)
        asset = self.vault.get_asset(scene.selected_asset_id) if scene.selected_asset_id else None
        if scene.is_outro:
            logo = self.vault.get_logo_asset()
            if not logo:
                raise ValueError(
                    "Impossibile generare la scena finale: logo originale KORA non presente nel Media Vault."
                )
            asset = logo

        if not asset:
            raise ValueError(
                f"Scena {scene.scene_number} priva di asset multimediale valido."
            )

        cache_key = self._scene_cache_key(
            scene, asset["sha256"], preset_cfg.get("default_warmth", 12.0)
        )
        if (width, height) != (DEFAULT_WIDTH, DEFAULT_HEIGHT):
            cache_key = cache_key + f"_{width}x{height}"
        cached_mp4 = scenes_dir / f"scene_{scene.scene_number:02d}_{cache_key}.mp4"
        if cached_mp4.is_file() and cached_mp4.stat().st_size > 1000:
            return cached_mp4, {"cache_hit": True, "scene_number": scene.scene_number}

        dur_sec = scene.duration_frames / fps
        # una scena corta su un Mac lento può superare i 2 minuti: il limite cresce
        # con la durata della scena invece di essere un numero fisso
        scene_timeout = max(180, int(round(scene.duration_frames * 4)))
        src_path = Path(asset["working_path"] or asset["original_path"])

        if scene.is_outro:
            outro_png = scenes_dir / f"outro_{cache_key}.png"
            self.render_logo_outro_frame(src_path, outro_png)
            fade_in = min(0.7, dur_sec * 0.3)
            fade_out_st = max(0.2, dur_sec - 0.5)
            run_ffmpeg(
                [
                    "-y",
                    "-loglevel",
                    "error",
                    "-loop",
                    "1",
                    "-i",
                    str(outro_png),
                    "-vf",
                    f"fps={fps},scale={width}:{height},fade=t=in:st=0:d={fade_in:.2f},fade=t=out:st={fade_out_st:.2f}:d=0.5,format=yuv420p",
                    "-t",
                    f"{dur_sec:.4f}",
                    "-r",
                    str(fps),
                    "-c:v",
                    "libx264",
                    "-preset",
                    X264_PRESET,
                    "-crf",
                    str(crf),
                    "-pix_fmt",
                    "yuv420p",
                    str(cached_mp4),
                ],
                timeout=scene_timeout,
            )
            return cached_mp4, {"cache_hit": False, "scene_number": scene.scene_number, "type": "outro"}

        if asset["media_type"] == "video":
            sample_frame = scenes_dir / f"sample_{cache_key}.png"
            metrics = self.colorist.analyze_video_sample(src_path, sample_frame)
            grade = self.colorist.compute_adaptive_grade(
                metrics, target_warmth=preset_cfg.get("default_warmth", 12.0)
            )
            # Conform fps to 25, smart 9:16 crop, adaptive grade, exact duration
            vf = (
                f"fps={fps},"
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                f"{grade['filter']},setsar=1,format=yuv420p"
            )
            run_ffmpeg(
                [
                    "-y",
                    "-loglevel",
                    "error",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(src_path),
                    "-an",
                    "-vf",
                    vf,
                    "-t",
                    f"{dur_sec:.4f}",
                    "-r",
                    str(fps),
                    "-c:v",
                    "libx264",
                    "-preset",
                    X264_PRESET,
                    "-crf",
                    str(crf),
                    "-pix_fmt",
                    "yuv420p",
                    str(cached_mp4),
                ],
                timeout=scene_timeout,
            )
            return cached_mp4, {
            "cache_hit": False,
            "scene_number": scene.scene_number,
            "grade": grade["adjustments"],
            "shot_source": scene.shot_source,
            "shot_plan": {
                "trajectory": (scene.shot_plan or {}).get("trajectory"),
                "zoom_start": (scene.shot_plan or {}).get("zoom_start"),
                "zoom_end": (scene.shot_plan or {}).get("zoom_end"),
                "easing": (scene.shot_plan or {}).get("easing"),
                "crop": (scene.shot_plan or {}).get("crop"),
                "applied": bool(scene.shot_plan),
            },
        }

        # Photo -> optional crop focus -> color grade -> deterministic camera motion
        metrics = self.colorist.analyze_frame(src_path)
        grade = self.colorist.compute_adaptive_grade(
            metrics, target_warmth=preset_cfg.get("default_warmth", 12.0)
        )
        motion_raw = scenes_dir / f"raw_motion_{cache_key}.mp4"
        self.media_lab.animate_photo_deterministic(
            asset_path=src_path,
            output_path=motion_raw,
            duration_frames=scene.duration_frames,
            motion=scene.camera_motion,
            fps=fps,
            width=width,
            height=height,
            shot_plan=scene.shot_plan,
            intensity=getattr(scene, "motion_intensity", 1.0) or 1.0,
            easing=getattr(scene, "motion_easing", "ease_in_out") or "ease_in_out",
            speed=getattr(scene, "motion_speed", 1.0) or 1.0,
            composition=getattr(scene, "composition", "regola_terzi") or "regola_terzi",
        )
        run_ffmpeg(
            [
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(motion_raw),
                "-vf",
                f"{grade['filter']},format=yuv420p",
                "-r",
                str(fps),
                "-c:v",
                "libx264",
                "-preset",
                X264_PRESET,
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                str(cached_mp4),
            ],
            timeout=scene_timeout,
        )
        return cached_mp4, {"cache_hit": False, "scene_number": scene.scene_number, "grade": grade["adjustments"]}

    def render_reel(
        self,
        project_id: str,
        title: str,
        scenes: list[StoryboardScene],
        preset: str,
        project_dir: Path,
        audio_mix_path: Optional[Path],
        output_mp4: Path,
        include_subtitles: bool = True,
        fps: int = DEFAULT_FPS,
        caption_style: Optional[str] = None,
        hyperframes: Optional[dict[str, Any]] = None,
        caption_overrides: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Assemble full 1080x1920 25fps H.264/AAC Rec.709 MP4 with transitions & captions (KORA / Hyperframes)."""
        if not scenes:
            raise ValueError("Impossibile renderizzare: storyboard vuoto.")
        total_frames = sum(s.duration_frames for s in scenes)
        expected_duration = total_frames / fps

        preset_cfg = PRESET_DEFINITIONS.get(preset, PRESET_DEFINITIONS[EditingPreset.CINEMATIC_IDENTITY.value])
        hf_cfg = normalize_hyperframes_config(
            {
                **(hyperframes or {}),
                **({"caption_style": caption_style} if caption_style else {}),
            }
        )
        active_caption_style = (
            hf_cfg["caption_style"]
            if (caption_style or (hyperframes and hyperframes.get("caption_style")))
            else preset_cfg.get("subtitle_style", "serif_ivory")
        )
        hf_cfg["caption_style"] = active_caption_style
        scenes_dir = project_dir / "scene_cache"
        sub_dir = project_dir / "subtitles"
        scenes_dir.mkdir(parents=True, exist_ok=True)
        sub_dir.mkdir(parents=True, exist_ok=True)

        clip_paths: list[Path] = []
        render_stats: list[dict[str, Any]] = []
        for sc in scenes:
            clip_p, stat = self._render_scene_clip(sc, preset_cfg, scenes_dir, fps=fps)
            clip_paths.append(clip_p)
            render_stats.append(stat)

        # Concatenate cached scene clips cleanly via filter_complex (zero PTS drift) + overlay subtitles + mux audio
        inputs: list[str] = []
        for p in clip_paths:
            inputs.extend(["-i", str(p)])

        n_clips = len(clip_paths)
        concat_inputs = "".join(f"[{i}:v]" for i in range(n_clips))
        filter_parts: list[str] = [f"{concat_inputs}concat=n={n_clips}:v=1:a=0,fps={fps}[vcat]"]

        next_idx = n_clips
        audio_input_idx: Optional[int] = None
        if audio_mix_path and audio_mix_path.is_file():
            inputs.extend(["-i", str(audio_mix_path)])
            audio_input_idx = next_idx
            next_idx += 1
        else:
            inputs.extend(
                [
                    "-f",
                    "lavfi",
                    "-t",
                    f"{expected_duration:.4f}",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                ]
            )
            audio_input_idx = next_idx
            next_idx += 1

        cur_v = "[vcat]"
        hf_filters = build_hyperframes_vf_filters(hf_cfg)
        if hf_filters:
            filter_parts.append(f"{cur_v}{','.join(hf_filters)}[vhf]")
            cur_v = "[vhf]"

        hyperframes_ws: Optional[dict[str, str]] = None
        if hf_cfg.get("title_card_intro", "none") != "none" and scenes and not scenes[0].is_outro:
            tc_png = sub_dir / "title_card_intro.png"
            render_title_card_png(title, tc_png, style=hf_cfg["title_card_intro"])
            tc_en = max(1.5, min(2.9, scenes[0].end_sec - 0.15))
            inputs.extend(
                ["-framerate", str(fps), "-loop", "1", "-t", f"{tc_en - 0.20:.4f}", "-i", str(tc_png)]
            )
            filter_parts.append(f"[{next_idx}:v]setpts=PTS+0.25/TB,format=rgba[vtcsrc]")
            filter_parts.append(f"{cur_v}[vtcsrc]overlay=0:0:eof_action=pass:repeatlast=0[vtc]")
            cur_v = "[vtc]"
            next_idx += 1

        if include_subtitles:
            if active_caption_style.startswith("hyperframes") or hf_cfg.get("title_card_intro") != "none":
                hyperframes_ws = export_hyperframes_workspace(
                    scenes=scenes,
                    caption_style=active_caption_style,
                    title=title,
                    out_dir=project_dir / "hyperframes_captions",
                    fps=fps,
                )
            seg_counter = 0
            for sc in scenes:
                from kora_studio.agents.caption_designer import engine_style_for

                scene_style = engine_style_for(sc.caption_style) or active_caption_style
                if scene_style == "none":
                    continue
                segs = build_caption_segments_for_scene(
                    scene=sc,
                    caption_style=scene_style,
                    sub_dir=sub_dir,
                    overrides={**(caption_overrides or {}), **(sc.caption_overrides or {})} or None,
                )
                for seg in segs:
                    st = float(seg["start_sec"])
                    en = float(seg["end_sec"])
                    seg_dur = max(0.12, en - st)
                    # Si decodifica SOLO la durata del sottotitolo e lo si sposta nel
                    # tempo: prima ogni PNG veniva espanso su tutto il reel (28 s x 8
                    # sottotitoli) e su un Mac non bastavano 4 minuti.
                    inputs.extend(
                        ["-framerate", str(fps), "-loop", "1", "-t", f"{seg_dur:.4f}", "-i", str(seg["png"])]
                    )
                    out_lbl = f"[vsub_{seg_counter}]"
                    src_lbl = f"[vsubsrc_{seg_counter}]"
                    filter_parts.append(
                        f"[{next_idx}:v]setpts=PTS+{st:.4f}/TB,format=rgba{src_lbl}"
                    )
                    filter_parts.append(
                        f"{cur_v}{src_lbl}overlay=0:0:eof_action=pass:repeatlast=0{out_lbl}"
                    )
                    cur_v = out_lbl
                    next_idx += 1
                    seg_counter += 1

        filter_parts.append(f"{cur_v}format=yuv420p[vfinal]")
        output_mp4.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "-y",
            "-loglevel",
            "error",
            *inputs,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vfinal]",
            "-map",
            f"{audio_input_idx}:a",
            # master finale: ≈ −14 LUFS, picco ≤ −1 dBTP (misurato poi dal QC)
            "-af",
            "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-t",
            f"{expected_duration:.4f}",
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-preset",
            X264_PRESET,
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-movflags",
            "+faststart",
            str(output_mp4),
        ]
        render_started = time.perf_counter()
        montaggio_timeout = render_timeout_seconds(total_frames or expected_duration * fps)
        try:
            run_ffmpeg(cmd, timeout=montaggio_timeout)
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                f"Montaggio troppo lento su questa macchina: superato il limite di "
                f"{montaggio_timeout} s per {int(total_frames or 0)} frame. "
                "Le scene sono già in cache: premi «Riprova» per completare solo il montaggio. "
                "Puoi anche alzare il limite con KORA_RENDER_TIMEOUT_SEC=1800 e riavviare KORA."
            ) from exc
        render_seconds = round(time.perf_counter() - render_started, 2)

        # Emit Remotion-compatible spec & TSX descriptor for interoperability
        remotion_spec_path = project_dir / "remotion_composition.json"
        remotion_spec = {
            "compositionId": f"KoraReel_{project_id}",
            "title": title,
            "fps": fps,
            "width": DEFAULT_WIDTH,
            "height": DEFAULT_HEIGHT,
            "durationInFrames": total_frames,
            "preset": preset,
            "includeSubtitles": include_subtitles,
            "remotionRuntime": check_remotion_runtime(),
            "scenes": [s.model_dump() for s in scenes],
        }
        remotion_spec_path.write_text(
            json.dumps(remotion_spec, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        return {
            "render_engine": "ffmpeg_deterministic_25fps",
            "render_seconds": render_seconds,
            "render_timeout_sec": montaggio_timeout,
            "x264_preset": X264_PRESET,
            "output_mp4": str(output_mp4),
            "total_frames": total_frames,
            "expected_duration_sec": round(expected_duration, 3),
            "scene_render_stats": render_stats,
            "remotion_spec_path": str(remotion_spec_path),
            "caption_style": active_caption_style,
            "caption_engine": CAPTION_STYLES.get(active_caption_style, {}).get("engine", "kora_native"),
            "hyperframes_config": hf_cfg,
            "hyperframes_workspace": hyperframes_ws,
        }


# ---------------------------------------------------------------------------
# Cinematic Preview (v2.8) — low-res, cached, scene-by-scene review before render
# ---------------------------------------------------------------------------

PREVIEW_WIDTH = 540
PREVIEW_HEIGHT = 960
PREVIEW_FPS = 25
PREVIEW_CRF = 30


def _preview_scene_hash(scene: StoryboardScene) -> str:
    payload = json.dumps(
        {
            "asset": scene.selected_asset_id,
            "frames": scene.duration_frames,
            "framing": scene.shot_framing,
            "motion": scene.camera_motion,
            "transition": scene.transition,
            "subtitle": scene.subtitle_text,
            "shot_plan": scene.shot_plan or {},
            "intensity": getattr(scene, "motion_intensity", 1.0),
            "easing": getattr(scene, "motion_easing", "ease_in_out"),
            "speed": getattr(scene, "motion_speed", 1.0),
            "composition": getattr(scene, "composition", "regola_terzi"),
            "caption_style": scene.caption_style,
            "caption_overrides": scene.caption_overrides or {},
            "title_text": scene.title_text,
            "is_outro": scene.is_outro,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class CinematicPreviewRenderer:
    """Builds a low-resolution review reel + one card per scene, with caching."""

    def __init__(self, editor: CinematicEditor, vault: MediaVault) -> None:
        self.editor = editor
        self.vault = vault

    def _thumb(self, src: Path, out_png: Path, width: int = 270, height: int = 480) -> Path:
        from PIL import ImageOps as _IO

        out_png.parent.mkdir(parents=True, exist_ok=True)
        source = src
        extracted: Optional[Path] = None
        try:
            Image.open(src).close()
        except Exception:
            # 3.6 — una clip video nel Vault non è un'immagine: si estrae il primo frame
            extracted = out_png.with_name(f"{out_png.stem}_frame.png")
            frame = self._first_frame(src, extracted)
            if not frame:
                raise
            source = frame
        with Image.open(source) as raw:
            img = _IO.exif_transpose(raw).convert("RGB")
            img = ImageOps.fit(img, (width, height), method=Image.Resampling.LANCZOS)
            img.save(out_png, "PNG")
        if extracted is not None:
            extracted.unlink(missing_ok=True)
        return out_png

    def _first_frame(self, clip: Path, out_png: Path) -> Optional[Path]:
        try:
            run_ffmpeg(
                ["-y", "-loglevel", "error", "-i", str(clip), "-frames:v", "1", str(out_png)],
                timeout=60,
            )
            return out_png if out_png.is_file() else None
        except Exception:
            return None

    def render(
        self,
        project_id: str,
        title: str,
        scenes: list[StoryboardScene],
        preset: str,
        project_dir: Path,
        audio_mix_path: Optional[Path] = None,
        fps: int = PREVIEW_FPS,
        force: bool = False,
    ) -> dict[str, Any]:
        if not scenes:
            raise ValueError("Nessuna scena da mostrare in anteprima.")
        preview_dir = project_dir / "preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = preview_dir / "preview_manifest.json"
        storyboard_hash = hashlib.sha256(
            json.dumps(
                [
                    {
                        "n": sc.scene_number,
                        "asset": sc.selected_asset_id,
                        "frames": sc.duration_frames,
                        "framing": sc.shot_framing,
                        "motion": sc.camera_motion,
                        "transition": sc.transition,
                        "subtitle": sc.subtitle_text,
                        "spoken": sc.spoken_text,
                        "plan": sc.shot_plan or {},
                        "intensity": getattr(sc, "motion_intensity", 1.0),
                        "easing": getattr(sc, "motion_easing", "ease_in_out"),
                        "speed": getattr(sc, "motion_speed", 1.0),
                        "composition": getattr(sc, "composition", "regola_terzi"),
                        "caption": sc.caption_style,
                        "caption_overrides": sc.caption_overrides or {},
                        "title": sc.title_text,
                        "outro": sc.is_outro,
                    }
                    for sc in scenes
                ],
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:16]
        audio_sig = ""
        if audio_mix_path and Path(audio_mix_path).is_file():
            st = Path(audio_mix_path).stat()
            audio_sig = f"{st.st_size}:{int(st.st_mtime)}"

        if manifest_path.is_file() and not force:
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    existing.get("storyboard_hash") == storyboard_hash
                    and existing.get("audio_signature") == audio_sig
                    and Path(existing.get("preview_mp4") or "").is_file()
                ):
                    existing["cache_hit"] = True
                    return existing
            except Exception:
                pass

        preset_cfg = PRESET_DEFINITIONS.get(preset, PRESET_DEFINITIONS[EditingPreset.CINEMATIC_IDENTITY.value])
        clips_dir = preview_dir / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        thumbs_dir = preview_dir / "thumbs"
        subs_dir = preview_dir / "subtitles"
        subs_dir.mkdir(parents=True, exist_ok=True)

        clips: list[Path] = []
        cards: list[dict[str, Any]] = []
        cache_hits = 0
        for scene in scenes:
            scene_hash = _preview_scene_hash(scene)
            clip, stat = self.editor._render_scene_clip(
                scene,
                preset_cfg,
                clips_dir,
                fps=fps,
                width=PREVIEW_WIDTH,
                height=PREVIEW_HEIGHT,
                crf=PREVIEW_CRF,
            )
            if stat.get("cache_hit"):
                cache_hits += 1
            clips.append(clip)

            asset = self.vault.get_asset(scene.selected_asset_id) if scene.selected_asset_id else None
            if scene.is_outro:
                asset = self.vault.get_logo_asset() or asset
            src_path = None
            if asset:
                candidate = Path(asset.get("working_path") or asset.get("original_path") or "")
                if candidate.is_file():
                    src_path = candidate
            thumb_src = (
                self._thumb(src_path, thumbs_dir / f"scene_{scene.scene_number:02d}_source.png")
                if src_path
                else None
            )
            thumb_derived = self._first_frame(clips[-1], thumbs_dir / f"scene_{scene.scene_number:02d}_derived.png")
            plan = scene.shot_plan or {}
            cards.append(
                {
                    "scene_number": scene.scene_number,
                    "is_outro": scene.is_outro,
                    "duration_frames": scene.duration_frames,
                    "duration_sec": round(scene.duration_frames / fps, 3),
                    "start_sec": round(scene.start_sec, 3),
                    "original_photo": str(src_path) if src_path else None,
                    "derived_shot": {
                        "framing": scene.shot_framing,
                        "trajectory": plan.get("trajectory"),
                        "crop": plan.get("crop"),
                        "zoom_start": plan.get("zoom_start"),
                        "zoom_end": plan.get("zoom_end"),
                        "easing": plan.get("easing"),
                        "motion_intensity": plan.get("motion_intensity"),
                        "residual_resolution": plan.get("residual_resolution"),
                        "source": scene.shot_source,
                        "motivation": plan.get("motivation"),
                    },
                    "camera_move": scene.camera_motion,
                    "voiceover": {
                        "text": scene.spoken_text,
                        "voice_id": scene.voice_id,
                        "subtitles": scene.subtitle_text,
                    },
                    "subtitle_text": scene.subtitle_text,
                    "transition": scene.transition,
                    "sound_design": {
                        "sfx": scene.sfx_category,
                        "music_cue": scene.music_cue,
                    },
                    "approval_status": scene.approval_status,
                    "preview_status": scene.preview_status,
                    "thumbnail_source": str(thumb_src) if thumb_src else None,
                    "thumbnail_derived": str(thumb_derived) if thumb_derived else None,
                    "preview_clip": str(clips[-1]),
                }
            )

        # subtitle overlay per scene (simplified preview captions)
        inputs: list[str] = []
        for clip in clips:
            inputs.extend(["-i", str(clip)])
        total_frames = sum(s.duration_frames for s in scenes)
        total_sec = total_frames / fps
        filter_parts = [
            "".join(f"[{i}:v]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0,fps={fps}[vcat]"
        ]
        cur = "[vcat]"
        next_idx = len(clips)
        for scene in scenes:
            if scene.is_outro:
                continue
            if not scene.subtitle_text.strip() and not (scene.title_text or "").strip():
                continue
            from kora_studio.agents.caption_designer import engine_style_for

            preset_style = engine_style_for(scene.caption_style) or "serif_ivory"
            if preset_style == "none":
                continue
            png = subs_dir / f"sub_{scene.scene_number:02d}_{_preview_scene_hash(scene)}.png"
            self.editor._render_subtitle_overlay_png(
                scene.subtitle_text,
                png,
                style=("clean_sans" if preset_style in ("serif_minimal",) else "serif_ivory"),
                width=PREVIEW_WIDTH,
                height=PREVIEW_HEIGHT,
                title=scene.title_text or "",
            )
            seg_dur = max(0.12, float(scene.end_sec) - float(scene.start_sec))
            inputs.extend(
                ["-framerate", str(fps), "-loop", "1", "-t", f"{seg_dur:.4f}", "-i", str(png)]
            )
            out = f"[vsub_{next_idx}]"
            src = f"[vsubsrc_{next_idx}]"
            filter_parts.append(
                f"[{next_idx}:v]setpts=PTS+{float(scene.start_sec):.4f}/TB,format=rgba{src}"
            )
            filter_parts.append(f"{cur}{src}overlay=0:0:eof_action=pass:repeatlast=0{out}")
            cur = out
            next_idx += 1
        filter_parts.append(f"{cur}format=yuv420p[vfinal]")

        if audio_mix_path and Path(audio_mix_path).is_file():
            inputs.extend(["-i", str(audio_mix_path)])
        else:
            inputs.extend(["-f", "lavfi", "-t", f"{total_sec:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
        audio_idx = next_idx

        preview_mp4 = preview_dir / f"{project_id}_cinematic_preview.mp4"
        run_ffmpeg(
            [
                "-y",
                "-loglevel",
                "error",
                *inputs,
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[vfinal]",
                "-map",
                f"{audio_idx}:a",
                "-t",
                f"{total_sec:.3f}",
                "-r",
                str(fps),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                str(PREVIEW_CRF),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
                str(preview_mp4),
            ],
            timeout=render_timeout_seconds(total_frames, per_frame=1.2, minimum=300.0),
        )
        manifest = {
            "project_id": project_id,
            "title": title,
            "storyboard_hash": storyboard_hash,
            "audio_signature": audio_sig,
            "audio_included": bool(audio_mix_path and Path(audio_mix_path).is_file()),
            "audio_note": (
                "voce e sound design inclusi"
                if audio_mix_path and Path(audio_mix_path).is_file()
                else "audio non ancora generato: l'anteprima è muta finché non viene prodotto il mix"
            ),
            "resolution": f"{PREVIEW_WIDTH}x{PREVIEW_HEIGHT}",
            "fps": fps,
            "total_frames": total_frames,
            "duration_sec": round(total_sec, 3),
            "preview_mp4": str(preview_mp4),
            "cards": cards,
            "scene_clips_cached": cache_hits,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cache_hit": False,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return manifest
