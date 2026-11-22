"""Sound Designer agent — scene-matched SFX, licensed CC0 music, ducking, and peak-safe mix."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from kora_studio.db.models import StoryboardScene
from kora_studio.media_tools import run_ffmpeg
from kora_studio.vault.sound_library import SoundLibrary


class SoundDesigner:
    """Selects coherent, licensed SFX and music and mixes them safely under voiceover."""

    def __init__(self, sound_library: SoundLibrary, engine: Any = None) -> None:
        self.sound_library = sound_library
        self.engine = engine

    def attach_engine_manager(self, engine: Any) -> None:
        self.engine = engine

    def _engine_cues(
        self,
        scenes: list[StoryboardScene],
        by_cat: dict[str, list[dict[str, Any]]],
        production_id: Any = None,
    ) -> Optional[dict[str, Any]]:
        """Il Sound Director configurabile sceglie musica e cue dentro il catalogo REALE."""
        if self.engine is None:
            return None
        import json

        from kora_studio.ai_engine.base import EngineError

        music = [s for s in self.sound_library.list_sounds() if s["category"].startswith("musica")]
        prompt = (
            "Sei il Sound Designer di KORA Beach Club. Scegli una traccia dal catalogo reale e, per ogni "
            "scena, una categoria di suono presente nel catalogo. Nessun suono inventato. "
            'Rispondi SOLO con JSON: {"music_id": str, "cues": [{"scene_number": int, "category": str, "why": str}]}'
        )
        payload = {
            "scene": [
                {"n": sc.scene_number, "goal": sc.narrative_goal, "asset": sc.selected_asset_id, "sec": sc.duration_sec}
                for sc in scenes
            ],
            "music_catalog": [{"id": m["id"], "name": m["name"]} for m in music],
            "sfx_categories": sorted(by_cat.keys()),
        }
        try:
            result = self.engine.call(
                "sound_designer",
                [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                json_mode=True,
                timeout=45,
                production_id=production_id,
            )
        except EngineError:
            return None
        try:
            start = result.text.find("{")
            end = result.text.rfind("}")
            data = json.loads(result.text[start : end + 1]) if start >= 0 and end > start else {}
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        valid_music = {m["id"] for m in music}
        cues: dict[int, str] = {}
        for cue in data.get("cues") or []:
            if not isinstance(cue, dict):
                continue
            number = cue.get("scene_number")
            category = cue.get("category")
            if isinstance(number, int) and category in by_cat:
                cues[number] = category
        music_id = data.get("music_id") if data.get("music_id") in valid_music else None
        return {"music_id": music_id, "cues": cues, "engine": f"{result.provider}/{result.model}"}

    def select_music(self, preset: str, preferred_music_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        if preferred_music_id:
            found = self.sound_library.get_sound(preferred_music_id)
            if found:
                return found
        all_sounds = self.sound_library.list_sounds()
        if "editorial" in preset.lower() or "seasonal" in preset.lower():
            for s in all_sounds:
                if s["category"] == "musica_ambient":
                    return s
        for s in all_sounds:
            if s["category"] == "musica_cinematic":
                return s
        for s in all_sounds:
            if s["category"] == "musica_ambient":
                return s
        return None

    def plan_audio(
        self,
        scenes: list[StoryboardScene],
        preset: str,
        preferred_music_id: Optional[str] = None,
        include_music: bool = True,
        music_start_sec: float = 0.0,
        music_volume: Optional[float] = None,
        voice_volume: float = 1.05,
        production_id: Any = None,
    ) -> dict[str, Any]:
        all_sounds = self.sound_library.list_sounds()
        by_cat: dict[str, list[dict[str, Any]]] = {}
        for s in all_sounds:
            by_cat.setdefault(s["category"], []).append(s)

        music = self.select_music(preset, preferred_music_id) if include_music else None
        cues: Optional[dict[str, Any]] = None
        if include_music and not preferred_music_id:
            cues = self._engine_cues(scenes, by_cat, production_id=production_id)
            if cues and cues.get("music_id"):
                music = self.sound_library.get_sound(cues["music_id"]) or music
        sfx_cues: list[dict[str, Any]] = []
        for scene in scenes:
            cat = (cues or {}).get("cues", {}).get(scene.scene_number, scene.sfx_category)
            if not cat or cat in ("nessuno", "none") or cat not in by_cat:
                continue
            sfx = by_cat[cat][0]
            # Keep SFX subtle and physically justified by scene content
            gain = 0.22 if not scene.is_outro else 0.16
            if cat in ("tazzine", "bicchieri"):
                gain = 0.28
            sfx_cues.append(
                {
                    "scene_number": scene.scene_number,
                    "sound_id": sfx["id"],
                    "sound_name": sfx["name"],
                    "category": cat,
                    "file_path": sfx["file_path"],
                    "license": sfx["license"],
                    "start_sec": round(scene.start_sec + 0.15, 3),
                    "duration_sec": min(sfx["duration_sec"], max(0.8, scene.duration_sec - 0.2)),
                    "gain": gain,
                }
            )

        return {
            "music": music,
            "cue_source": (cues or {}).get("engine", "deterministic") if include_music else "disabled",
            "include_music": include_music,
            "music_start_sec": max(0.0, float(music_start_sec)),
            "music_volume": music_volume,
            "voice_volume": float(voice_volume),
            "sfx_cues": sfx_cues,
        }

    def render_mix(
        self,
        total_duration_sec: float,
        audio_plan: dict[str, Any],
        voice_audio_path: Optional[Path],
        output_wav: Path,
    ) -> Path:
        """Mix voiceover + music (ducked, with custom offset) + scene SFX with fade-in/out and anti-clipping limiter."""
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        has_voice = bool(voice_audio_path and voice_audio_path.is_file())
        music = audio_plan.get("music") if audio_plan.get("include_music", True) else None
        has_music = bool(music and Path(music["file_path"]).is_file())
        sfx_cues = [c for c in audio_plan.get("sfx_cues", []) if Path(c["file_path"]).is_file()][:16]

        # Build deterministic FFmpeg filter_complex starting from silent stereo bed
        inputs: list[str] = [
            "-f",
            "lavfi",
            "-t",
            f"{total_duration_sec:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]
        filter_parts: list[str] = ["[0:a]volume=1.0[base]"]
        mix_labels = ["[base]"]
        idx = 1

        if has_voice and voice_audio_path:
            inputs.extend(["-i", str(voice_audio_path)])
            vo_vol = float(audio_plan.get("voice_volume") or 1.05)
            # Notice: per-scene aligned voiceover already includes scene offsets; unaligned starts at 0.25s
            is_aligned = "aligned" in voice_audio_path.name
            vo_filter = (
                f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vo_vol:.3f}[vo]"
                if is_aligned
                else f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,adelay=250|250,volume={vo_vol:.3f}[vo]"
            )
            filter_parts.append(vo_filter)
            mix_labels.append("[vo]")
            idx += 1

        if has_music and music:
            music_start = max(0.0, float(audio_plan.get("music_start_sec") or 0.0))
            if music_start > 0.0:
                inputs.extend(["-stream_loop", "-1", "-ss", f"{music_start:.3f}", "-i", str(music["file_path"])])
            else:
                inputs.extend(["-stream_loop", "-1", "-i", str(music["file_path"])])
            default_mvol = 0.17 if has_voice else 0.42
            raw_mvol = audio_plan.get("music_volume")
            music_vol = float(raw_mvol) if raw_mvol is not None else default_mvol
            fade_out_start = max(0.5, total_duration_sec - 1.8)
            filter_parts.append(
                f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"atrim=0:{total_duration_sec:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d=1.0,afade=t=out:st={fade_out_start:.3f}:d=1.8,"
                f"volume={music_vol:.3f}[mus]"
            )
            mix_labels.append("[mus]")
            idx += 1

        for k, cue in enumerate(sfx_cues):
            inputs.extend(["-i", str(cue["file_path"])])
            delay_ms = max(0, int(round(cue["start_sec"] * 1000)))
            cue_dur = max(0.4, float(cue["duration_sec"]))
            fade_st = max(0.1, cue_dur - 0.35)
            gain = float(cue.get("gain", 0.22)) * (0.65 if has_voice else 1.0)
            filter_parts.append(
                f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"atrim=0:{cue_dur:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d=0.15,afade=t=out:st={fade_st:.3f}:d=0.35,"
                f"volume={gain:.3f},adelay={delay_ms}|{delay_ms}[sfx{k}]"
            )
            mix_labels.append(f"[sfx{k}]")
            idx += 1

        n_mix = len(mix_labels)
        filter_parts.append(
            f"{''.join(mix_labels)}amix=inputs={n_mix}:duration=first:normalize=0,"
            f"alimiter=limit=0.891:attack=5:release=60[mixout]"
        )

        run_ffmpeg(
            [
                "-y",
                "-loglevel",
                "error",
                *inputs,
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[mixout]",
                "-t",
                f"{total_duration_sec:.3f}",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(output_wav),
            ],
            timeout=120,
        )
        return output_wav
