"""Export Manager agent — final MP4, cover, IG caption, variants, editable project, report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

from kora_studio.agents.cinematic_editor import CinematicEditor, _load_font
from kora_studio.agents.quality_controller import QualityController
from kora_studio.agents.sound_designer import SoundDesigner
from kora_studio.brand.brand_brain import BrandBrain
from kora_studio.config import DEFAULT_FPS, DEFAULT_HEIGHT, DEFAULT_WIDTH, EXPORTS_DIR
from kora_studio.db.models import StoryboardScene
from kora_studio.media_tools import run_ffmpeg
from kora_studio.vault.media_vault import MediaVault


def _qc_block_message(report: Any, diagnosis: Optional[dict[str, Any]] = None) -> str:
    """Messaggio di blocco leggibile, senza ripetere due volte lo stesso testo."""
    detail = "; ".join(
        f"{c.name}: {c.detail}" for c in getattr(report, "checks", []) or [] if not c.passed
    )
    hint = ((diagnosis or {}).get("suggestion") or "").strip()
    # La diagnosi di ripiego ripete i nostri stessi dettagli: inutile e confusa.
    if hint and hint.startswith("Correggere i controlli falliti"):
        hint = ""
    if hint and hint not in detail:
        detail = f"{detail} Diagnosi: {hint}"
    return "Esportazione bloccata dal Controllo Qualità: " + detail


class ExportManager:
    """Produces the complete verified export bundle required by Section 19."""

    def __init__(
        self,
        vault: MediaVault,
        brand_brain: BrandBrain,
        editor: CinematicEditor,
        sound_designer: SoundDesigner,
        qc: QualityController,
        copywriter: Any = None,
    ) -> None:
        self.vault = vault
        self.brand_brain = brand_brain
        self.editor = editor
        self.sound_designer = sound_designer
        self.qc = qc
        self.copywriter = copywriter

    def attach_engine_manager(self, engine: Any) -> None:
        from kora_studio.agents.copywriter import Copywriter

        self.copywriter = Copywriter(engine, guardian=None)

    def generate_vertical_cover(
        self,
        scenes: list[StoryboardScene],
        title: str,
        output_png: Path,
    ) -> Path:
        """Create a 1080x1920 vertical Instagram cover from the strongest authentic scene."""
        target_asset = None
        for sc in scenes:
            if sc.is_outro or not sc.selected_asset_id:
                continue
            a = self.vault.get_asset(sc.selected_asset_id)
            if a and a["media_type"] == "image":
                target_asset = a
                break
        if not target_asset:
            for sc in scenes:
                if sc.is_outro or not sc.selected_asset_id:
                    continue
                a = self.vault.get_asset(sc.selected_asset_id)
                if a:
                    target_asset = a
                    break

        output_png.parent.mkdir(parents=True, exist_ok=True)
        if target_asset and target_asset["media_type"] == "image":
            src = Path(target_asset["working_path"] or target_asset["original_path"])
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im = ImageOps.fit(im, (DEFAULT_WIDTH, DEFAULT_HEIGHT), method=Image.Resampling.LANCZOS)
        elif target_asset and target_asset["media_type"] == "video":
            frame_tmp = output_png.with_name("cover_frame.png")
            run_ffmpeg(
                [
                    "-y",
                    "-loglevel",
                    "error",
                    "-ss",
                    "1.0",
                    "-i",
                    str(target_asset["original_path"]),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={DEFAULT_WIDTH}:{DEFAULT_HEIGHT}:force_original_aspect_ratio=increase,crop={DEFAULT_WIDTH}:{DEFAULT_HEIGHT}",
                    str(frame_tmp),
                ],
                timeout=30,
            )
            with Image.open(frame_tmp) as im:
                im = im.convert("RGB").copy()
        else:
            im = Image.new("RGB", (DEFAULT_WIDTH, DEFAULT_HEIGHT), (35, 78, 104))

        # Subtle bottom gradient scrim + KORA editorial cover typography
        overlay = Image.new("RGBA", (DEFAULT_WIDTH, DEFAULT_HEIGHT), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        for y in range(int(DEFAULT_HEIGHT * 0.55), DEFAULT_HEIGHT):
            alpha = int(165 * ((y - DEFAULT_HEIGHT * 0.55) / (DEFAULT_HEIGHT * 0.45)))
            odraw.line([(0, y), (DEFAULT_WIDTH, y)], fill=(12, 13, 12, alpha))

        composited = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(composited)
        title_font = _load_font(56)
        sub_font = _load_font(28, italic=True)
        import textwrap

        lines = textwrap.wrap(title, width=22)[:2]
        y_cursor = int(DEFAULT_HEIGHT * (0.71 if len(lines) > 1 else 0.75))
        for line in lines:
            draw.text((88, y_cursor), line, fill=(247, 243, 234), font=title_font)
            y_cursor += 68
        draw.text(
            (88, y_cursor + 10),
            "KORA Beach Club · Policoro · The Art of Being",
            fill=(218, 206, 184),
            font=sub_font,
        )
        composited.save(output_png, "PNG")
        return output_png

    def generate_instagram_caption(
        self,
        title: str,
        message: str,
        theme: str,
        production_id: Optional[str] = None,
    ) -> dict[str, Any]:
        profile = self.brand_brain.get_current()
        contact = profile.get("contact", {})
        loc = contact.get("address", "Lungomare destro · Policoro (MT)")
        wa = contact.get("whatsapp", "352 0379963")
        caption = (
            f"{title}.\n\n"
            f"{message}\n\n"
            "KORA · The Art of Being\n\n"
            f"📍 {loc}\n"
            f"📲 Info e WhatsApp {wa}"
        )
        template = {
            "caption": caption,
            "hashtags": [
                "#KoraBeachClub",
                "#TheArtOfBeing",
                "#Policoro",
                "#OspitalitàMediterranea",
                "#Basilicata",
                "#MareIonio",
                "#TempoRitrovato",
            ],
        }
        if self.copywriter is not None:
            written = self.copywriter.write(
                title=title,
                message=message,
                theme=theme,
                contact=contact,
                production_id=production_id,
                fallback=template,
            )
            return written
        return {
            "caption": template["caption"],
            "hashtags": template["hashtags"],
            "full_post_text": f"{caption}\n\n{' '.join(template['hashtags'])}",
            "source": "deterministic_template",
        }

    def export_bundle(
        self,
        project: dict[str, Any],
        scenes: list[StoryboardScene],
        project_dir: Path,
        main_mp4: Path,
        voice_audio_path: Optional[Path],
        audio_plan: dict[str, Any],
        include_no_subtitles_variant: bool = False,
        include_no_music_variant: bool = False,
        fps: int = DEFAULT_FPS,
    ) -> dict[str, Any]:
        project_id = project["id"]
        export_dir = EXPORTS_DIR / project_id
        export_dir.mkdir(parents=True, exist_ok=True)
        expected_duration = sum(s.duration_frames for s in scenes) / fps

        # 1. Verify main MP4 passes QC before exporting
        voice_source = str((audio_plan or {}).get("source_used") or "").strip().lower()
        voiceover_present = bool(
            voice_audio_path
            and Path(voice_audio_path).is_file()
            and voice_source
            not in ("", "nessuna", "none", "senza_voce", "voce_assente")
        )
        qc_report = self.qc.inspect_reel(
            mp4_path=main_mp4,
            scenes=scenes,
            expected_duration_sec=expected_duration,
            expect_audio=True,
            work_dir=project_dir,
            voiceover_present=voiceover_present,
        )
        if not qc_report.passed:
            diagnosis = None
            try:
                diagnosis = self.qc.diagnose(qc_report)
            except Exception:
                diagnosis = None
            raise RuntimeError(_qc_block_message(qc_report, diagnosis))

        final_mp4 = export_dir / f"{project_id}_reel_final.mp4"
        if main_mp4.resolve() != final_mp4.resolve():
            import shutil

            shutil.copy2(main_mp4, final_mp4)

        # 2. Cover PNG
        cover_png = export_dir / f"{project_id}_copertina.png"
        self.generate_vertical_cover(scenes, project["title"], cover_png)

        # 3. Instagram caption + hashtags
        concept = project.get("concept", {})
        ig = self.generate_instagram_caption(
            project["title"],
            concept.get("message", project["theme"]),
            project["theme"],
            production_id=project_id,
        )
        caption_txt = export_dir / f"{project_id}_caption_instagram.txt"
        caption_txt.write_text(ig["full_post_text"], encoding="utf-8")

        # 4. Optional clean variants (no subtitles / no music)
        no_sub_mp4: Optional[Path] = None
        if include_no_subtitles_variant:
            no_sub_mp4 = export_dir / f"{project_id}_senza_sottotitoli.mp4"
            mix_wav = project_dir / "audio_mix.wav"
            self.editor.render_reel(
                project_id=project_id,
                title=project["title"],
                scenes=scenes,
                preset=project["preset"],
                project_dir=project_dir,
                audio_mix_path=mix_wav if mix_wav.is_file() else None,
                output_mp4=no_sub_mp4,
                include_subtitles=False,
                fps=fps,
            )

        no_music_mp4: Optional[Path] = None
        if include_no_music_variant:
            no_music_mp4 = export_dir / f"{project_id}_senza_musica.mp4"
            no_mus_plan = {**audio_plan, "include_music": False, "music": None}
            no_mus_wav = project_dir / "audio_mix_nomusic.wav"
            self.sound_designer.render_mix(
                total_duration_sec=expected_duration,
                audio_plan=no_mus_plan,
                voice_audio_path=voice_audio_path,
                output_wav=no_mus_wav,
            )
            self.editor.render_reel(
                project_id=project_id,
                title=project["title"],
                scenes=scenes,
                preset=project["preset"],
                project_dir=project_dir,
                audio_mix_path=no_mus_wav,
                output_mp4=no_music_mp4,
                include_subtitles=True,
                fps=fps,
            )

        # 5. Editable project JSON
        editable_json = export_dir / f"{project_id}_progetto_modificabile.json"
        editable_json.write_text(
            json.dumps(
                {
                    "project": project,
                    "scenes": [s.model_dump() for s in scenes],
                    "audio_plan": audio_plan,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # 6. Technical report JSON & Markdown
        report_data = {
            "project_id": project_id,
            "title": project["title"],
            "preset": project["preset"],
            "spec": {
                "format": "MP4",
                "video_codec": "H.264 (libx264)",
                "resolution": f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}",
                "aspect_ratio": "9:16",
                "fps": fps,
                "audio_codec": "AAC 48kHz Stereo",
                "color_space": "Rec.709 (bt709)",
                "total_frames": sum(s.duration_frames for s in scenes),
                "duration_sec": round(expected_duration, 3),
            },
            "qc": qc_report.model_dump(),
            "files": {
                "reel_mp4": str(final_mp4),
                "cover_png": str(cover_png),
                "caption_txt": str(caption_txt),
                "no_subtitles_mp4": str(no_sub_mp4) if no_sub_mp4 else None,
                "no_music_mp4": str(no_music_mp4) if no_music_mp4 else None,
                "editable_project_json": str(editable_json),
            },
        }
        report_json = export_dir / f"{project_id}_report_tecnico.json"
        report_json.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")

        report_md = export_dir / f"{project_id}_report_tecnico.md"
        status_tag = {
            "superato": "OK",
            "non_superato": "FAIL",
            "non_applicabile": "N/A",
            "non_verificato": "N/V",
        }
        checks_md = "\n".join(
            f"- [{status_tag.get(c.status, '?')}] **{c.name}** ({c.category}) · {c.status_label}: {c.detail}"
            for c in qc_report.checks
        )
        counts = qc_report.metrics.get("qc_status_counts") or {}
        report_md.write_text(
            f"# Report Tecnico — {project['title']}\n\n"
            f"- Formato: MP4 H.264 / AAC · 1080×1920 (9:16) · {fps} fps · Rec.709\n"
            f"- Durata: {expected_duration:.2f} s ({sum(s.duration_frames for s in scenes)} frame)\n"
            f"- Esito QC: {'SUPERATO' if qc_report.passed else 'NON SUPERATO'}\n"
            f"- Stati: {counts.get('superato', 0)} superati · "
            f"{counts.get('non_superato', 0)} non superati · "
            f"{counts.get('non_applicabile', 0)} non applicabili · "
            f"{counts.get('non_verificato', 0)} non verificati\n\n"
            f"## Verifiche\n{checks_md}\n",
            encoding="utf-8",
        )

        return {
            "reel_mp4": str(final_mp4),
            "cover_png": str(cover_png),
            "caption": ig["caption"],
            "hashtags": ig["hashtags"],
            "caption_txt": str(caption_txt),
            "no_subtitles_mp4": str(no_sub_mp4) if no_sub_mp4 else None,
            "no_music_mp4": str(no_music_mp4) if no_music_mp4 else None,
            "editable_project_json": str(editable_json),
            "report_json": str(report_json),
            "report_md": str(report_md),
            "qc": qc_report.model_dump(),
        }
