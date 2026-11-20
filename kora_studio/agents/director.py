"""KORA Director — central orchestrator of all 9 agents and observable Job Runner."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from kora_studio.agents.ai_media_lab import AIMediaLab, MediaCurator
from kora_studio.agents.brand_guardian import BrandGuardian
from kora_studio.agents.cinematic_editor import CinematicEditor
from kora_studio.agents.colorist import Colorist
from kora_studio.agents.creative_director import CreativeDirector
from kora_studio.agents.export_manager import ExportManager
from kora_studio.agents.quality_controller import QualityController
from kora_studio.agents.revision_engine import RevisionEngine
from kora_studio.agents.sound_designer import SoundDesigner
from kora_studio.agents.voice_director import VoiceDirector
from kora_studio.brand.brand_brain import BrandBrain
from kora_studio.config import DEFAULT_FPS, PROJECTS_DIR, ensure_directories
from kora_studio.db.database import Database, utcnow_iso
from kora_studio.db.models import (
    CreativeConcept,
    EditingPreset,
    JobState,
    ProductionMode,
    StoryboardScene,
)
from kora_studio.providers.registry import ProviderManager
from kora_studio.vault.media_vault import MediaVault
from kora_studio.vault.sound_library import SoundLibrary


class KoraDirector:
    """Central coordinator synchronizing the 9 internal agents over persisted project state."""

    def __init__(self, db: Optional[Database] = None) -> None:
        ensure_directories()
        self.db = db or Database()
        self.brand_brain = BrandBrain(self.db)
        self.guardian = BrandGuardian(self.brand_brain)
        self.vault = MediaVault(self.db)
        self.sound_library = SoundLibrary(self.db)
        self.providers = ProviderManager(self.db)

        self.creative = CreativeDirector(
            self.db, self.brand_brain, self.vault, providers=self.providers
        )
        self.curator = MediaCurator(self.vault)
        self.media_lab = AIMediaLab(self.vault, self.brand_brain, self.providers)
        self.voice_director = VoiceDirector(self.vault, self.providers)
        self.colorist = Colorist()
        self.sound_designer = SoundDesigner(self.sound_library)
        self.editor = CinematicEditor(self.vault, self.media_lab, self.colorist)
        self.qc = QualityController(self.vault, self.guardian)
        self.export_manager = ExportManager(
            self.vault, self.brand_brain, self.editor, self.sound_designer, self.qc
        )
        self.revision_engine = RevisionEngine(self.vault, self.sound_library)
        self._lock = threading.Lock()

    # ---- Project Persistence & Versioning --------------------------------

    def create_project(
        self,
        title: str,
        theme: str,
        mode: str = "theme",
        preset: str = EditingPreset.CINEMATIC_IDENTITY.value,
        production_mode: str = ProductionMode.AUTO.value,
        voice_id: str = "brando",
        target_duration_sec: float = 20.0,
        concept: Optional[CreativeConcept] = None,
        preferences: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        project_id = f"prj_{uuid.uuid4().hex[:10]}"
        fps = DEFAULT_FPS
        total_frames = int(round(target_duration_sec * fps))
        now = utcnow_iso()

        storyboard: list[dict[str, Any]] = []
        if concept is not None:
            scenes = self.creative.build_storyboard(concept, voice_id=voice_id, fps=fps)
            storyboard = [s.model_dump() for s in scenes]

        prefs_payload = dict(preferences or {})
        if concept is not None and concept.creative_request:
            prefs_payload.setdefault("creative_request", concept.creative_request)

        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    id, title, theme, mode, preset, production_mode, voice_id,
                    fps, target_duration_sec, total_frames, status, current_version,
                    concept_json, storyboard_json, edit_plan_json, audio_plan_json,
                    qc_report_json, export_bundle_json, preferences_json, output_video_path,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, '{}', '{}', '{}', '{}', ?, NULL, ?, ?)
                """,
                (
                    project_id,
                    title,
                    theme,
                    mode,
                    preset,
                    production_mode,
                    voice_id,
                    fps,
                    target_duration_sec,
                    total_frames,
                    JobState.IN_PREPARAZIONE.value,
                    json.dumps(concept.model_dump() if concept else {}, ensure_ascii=False),
                    json.dumps(storyboard, ensure_ascii=False),
                    json.dumps(prefs_payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        # Remember user voice & preset preference automatically
        self.db.set_preference("preferred_voice_id", voice_id)
        self.db.set_preference("preferred_preset", preset)
        self.db.set_preference("preferred_production_mode", production_mode)

        self.save_project_snapshot(project_id, "Creazione progetto iniziale", "KoraDirector")
        return self.get_project(project_id)  # type: ignore

    def get_project(self, project_id: str) -> Optional[dict[str, Any]]:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            return self._project_row_to_dict(row) if row else None

    def list_projects(self) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
            return [self._project_row_to_dict(r) for r in rows]

    def _project_row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "theme": row["theme"],
            "mode": row["mode"],
            "preset": row["preset"],
            "production_mode": row["production_mode"],
            "voice_id": row["voice_id"],
            "fps": row["fps"],
            "target_duration_sec": row["target_duration_sec"],
            "total_frames": row["total_frames"],
            "status": row["status"],
            "current_version": row["current_version"],
            "concept": json.loads(row["concept_json"]),
            "storyboard": json.loads(row["storyboard_json"]),
            "edit_plan": json.loads(row["edit_plan_json"]),
            "audio_plan": json.loads(row["audio_plan_json"]),
            "qc_report": json.loads(row["qc_report_json"]),
            "export_bundle": json.loads(row["export_bundle_json"]),
            "preferences": json.loads(row["preferences_json"]),
            "output_video_path": row["output_video_path"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_project_snapshot(
        self, project_id: str, summary: str, agent_name: str
    ) -> int:
        proj = self.get_project(project_id)
        if not proj:
            raise KeyError(f"Progetto {project_id} non trovato")
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM project_versions WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            next_ver = int(row["v"] or 0) + 1
            conn.execute(
                """
                INSERT INTO project_versions (project_id, version, summary, agent_name, snapshot_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    next_ver,
                    summary,
                    agent_name,
                    json.dumps(proj, ensure_ascii=False),
                    utcnow_iso(),
                ),
            )
            conn.execute(
                "UPDATE projects SET current_version = ?, updated_at = ? WHERE id = ?",
                (next_ver, utcnow_iso(), project_id),
            )
            return next_ver

    def get_project_versions(self, project_id: str) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT id, project_id, version, summary, agent_name, created_at FROM project_versions WHERE project_id = ? ORDER BY version DESC",
                (project_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def restore_project_version(self, project_id: str, target_version: int) -> dict[str, Any]:
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT snapshot_json FROM project_versions WHERE project_id = ? AND version = ?",
                (project_id, target_version),
            ).fetchone()
            if not row:
                raise KeyError(f"Versione {target_version} non trovata per {project_id}")
            snap = json.loads(row["snapshot_json"])
            conn.execute(
                """
                UPDATE projects SET
                    title = ?, theme = ?, preset = ?, production_mode = ?, voice_id = ?,
                    storyboard_json = ?, edit_plan_json = ?, audio_plan_json = ?,
                    qc_report_json = ?, export_bundle_json = ?, output_video_path = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    snap["title"],
                    snap["theme"],
                    snap["preset"],
                    snap["production_mode"],
                    snap["voice_id"],
                    json.dumps(snap["storyboard"], ensure_ascii=False),
                    json.dumps(snap["edit_plan"], ensure_ascii=False),
                    json.dumps(snap["audio_plan"], ensure_ascii=False),
                    json.dumps(snap["qc_report"], ensure_ascii=False),
                    json.dumps(snap["export_bundle"], ensure_ascii=False),
                    snap.get("output_video_path"),
                    utcnow_iso(),
                    project_id,
                ),
            )
        self.save_project_snapshot(
            project_id, f"Ripristino alla versione {target_version}", "KoraDirector"
        )
        return self.get_project(project_id)  # type: ignore

    # ---- Observable Job Runner -------------------------------------------

    def has_active_job(self, project_id: str) -> bool:
        terminal = (
            JobState.COMPLETATO.value,
            JobState.ERRORE.value,
            JobState.RICHIEDE_INTERVENTO.value,
        )
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE project_id = ? AND cancelled = 0 AND state NOT IN (?, ?, ?) LIMIT 1",
                (project_id, *terminal),
            ).fetchone()
            return row is not None

    def create_job(self, project_id: str, job_type: str = "full_production") -> dict[str, Any]:
        with self._lock:
            if self.has_active_job(project_id):
                raise RuntimeError(f"Un job di produzione è già attivo per il progetto {project_id}.")
            job_id = f"job_{uuid.uuid4().hex[:10]}"
            now = utcnow_iso()
            with self.db.session() as conn:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        id, project_id, job_type, state, current_step, completed_steps_json,
                        total_steps, checkpoint_json, cancelled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'Inizializzazione', '[]', 9, '{}', 0, ?, ?)
                    """,
                    (job_id, project_id, job_type, JobState.IN_PREPARAZIONE.value, now, now),
                )
        self.log_job(job_id, "INFO", "KoraDirector", f"Job '{job_type}' avviato per progetto {project_id}.")
        return self.get_job(job_id)  # type: ignore

    def log_job(self, job_id: str, level: str, agent: str, message: str) -> None:
        with self.db.session() as conn:
            conn.execute(
                "INSERT INTO job_logs (job_id, level, agent, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, level, agent, message, utcnow_iso()),
            )

    def _update_job(
        self,
        job_id: str,
        state: JobState,
        step: str,
        completed_steps: list[str],
        checkpoint: Optional[dict[str, Any]] = None,
        error_message: Optional[str] = None,
        intervention_reason: Optional[str] = None,
    ) -> None:
        with self.db.session() as conn:
            conn.execute(
                """
                UPDATE jobs SET
                    state = ?, current_step = ?, completed_steps_json = ?,
                    checkpoint_json = COALESCE(?, checkpoint_json),
                    error_message = ?, intervention_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    state.value,
                    step,
                    json.dumps(completed_steps, ensure_ascii=False),
                    json.dumps(checkpoint, ensure_ascii=False) if checkpoint is not None else None,
                    error_message,
                    intervention_reason,
                    utcnow_iso(),
                    job_id,
                ),
            )

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self.db.session() as conn:
            conn.execute(
                "UPDATE jobs SET cancelled = 1, state = ?, current_step = 'Annullato dall\\'utente', updated_at = ? WHERE id = ?",
                (JobState.ERRORE.value, utcnow_iso(), job_id),
            )
        self.log_job(job_id, "WARN", "KoraDirector", "Job annullato su richiesta dell'utente.")
        return self.get_job(job_id)  # type: ignore

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return None
            logs = conn.execute(
                "SELECT level, agent, message, created_at FROM job_logs WHERE job_id = ? ORDER BY id ASC",
                (job_id,),
            ).fetchall()
            return {
                "id": row["id"],
                "project_id": row["project_id"],
                "job_type": row["job_type"],
                "state": row["state"],
                "current_step": row["current_step"],
                "completed_steps": json.loads(row["completed_steps_json"]),
                "total_steps": row["total_steps"],
                "error_message": row["error_message"],
                "intervention_reason": row["intervention_reason"],
                "checkpoint": json.loads(row["checkpoint_json"]),
                "cancelled": bool(row["cancelled"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "logs": [dict(l) for l in logs],
            }

    def list_jobs(self, project_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT id FROM jobs WHERE project_id = ? ORDER BY created_at DESC",
                    (project_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT 30").fetchall()
        return [self.get_job(r["id"]) for r in rows if self.get_job(r["id"])]  # type: ignore

    # ---- Full Autonomous Production Pipeline -----------------------------

    def run_production_pipeline(
        self,
        project_id: str,
        job_id: Optional[str] = None,
        include_subtitles: bool = True,
        include_no_subtitles_variant: bool = False,
        include_no_music_variant: bool = False,
        explicit_paid_authorization: bool = False,
    ) -> dict[str, Any]:
        """Execute observable 9-agent pipeline from project state to verified exported MP4."""
        proj = self.get_project(project_id)
        if not proj:
            raise KeyError(f"Progetto {project_id} non trovato")
        if not job_id:
            job = self.create_job(project_id, "full_production")
            job_id = job["id"]

        project_dir = PROJECTS_DIR / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        completed: list[str] = []

        def check_cancelled() -> None:
            j = self.get_job(job_id)  # type: ignore
            if j and j["cancelled"]:
                raise InterruptedError("Job annullato dall'utente")

        try:
            # 1. Analisi materiali + Brand Guardian check
            self._update_job(job_id, JobState.ANALISI_MATERIALI, "Verifica Brand Brain e Logo", completed)
            logo = self.vault.get_logo_asset()
            logo_check = self.guardian.validate_logo_asset(logo["original_path"] if logo else None)
            if not logo_check["approved"]:
                self._update_job(
                    job_id,
                    JobState.RICHIEDE_INTERVENTO,
                    "Logo originale mancante",
                    completed,
                    intervention_reason=logo_check["error"],
                )
                self.log_job(job_id, "WARN", "BrandGuardian", logo_check["error"])
                return {"project": self.get_project(project_id), "job": self.get_job(job_id)}
            completed.append("Analisi materiali")
            self.log_job(job_id, "INFO", "BrandGuardian", "Logo originale e identità KORA verificati.")
            check_cancelled()

            # 2. Concept & Storyboard
            self._update_job(job_id, JobState.CONCEPT, "Consolidamento Concept", completed)
            concept_dict = proj.get("concept") or {}
            if not concept_dict:
                concepts = self.creative.ideate_from_theme(
                    proj["theme"], duration_sec=proj["target_duration_sec"]
                )
                concept = concepts[0]
                concept_dict = concept.model_dump()
            else:
                concept = CreativeConcept(**concept_dict)
            completed.append("Concept")
            self.log_job(job_id, "INFO", "CreativeDirector", f"Concept attivo: {concept.title}")
            check_cancelled()

            self._update_job(job_id, JobState.STORYBOARD, "Costruzione Storyboard 25fps", completed)
            raw_sb = proj.get("storyboard") or []
            if raw_sb:
                scenes = [StoryboardScene(**s) for s in raw_sb]
            else:
                scenes = self.creative.build_storyboard(
                    concept, voice_id=proj["voice_id"], fps=proj["fps"]
                )
            completed.append("Storyboard")
            self.log_job(
                job_id,
                "INFO",
                "CreativeDirector",
                f"Storyboard pronto: {len(scenes)} scene, {sum(s.duration_frames for s in scenes)} frame esatti ({proj['fps']} fps).",
            )
            check_cancelled()

            # 3. Media Curation & Asset preparation
            self._update_job(job_id, JobState.GENERAZIONE_ASSET, "Selezione e preparazione asset", completed)
            for sc in scenes:
                curated = self.curator.curate_for_scene(sc, theme=proj["theme"])
                if not curated["asset"]:
                    reason = f"Asset mancante per la scena {sc.scene_number}. Caricare almeno una foto o ripresa nel Media Vault."
                    self._update_job(
                        job_id,
                        JobState.RICHIEDE_INTERVENTO,
                        "Asset mancante",
                        completed,
                        intervention_reason=reason,
                    )
                    self.log_job(job_id, "WARN", "MediaCurator", reason)
                    return {"project": self.get_project(project_id), "job": self.get_job(job_id)}
                sc.selected_asset_id = curated["asset"]["id"]
                sc.production_status = "asset_pronto"
            completed.append("Generazione asset")
            self.log_job(job_id, "INFO", "MediaCurator", "Asset autentici assegnati a tutte le scene.")
            check_cancelled()

            # 4. Voiceover FIRST -> measure real speech durations -> lock exact timeline before Sound/Edit
            self._update_job(job_id, JobState.VOICEOVER, f"Preparazione voce ({proj['voice_id']})", completed)
            voice_res = self.voice_director.prepare_voiceover(
                scenes=scenes,
                voice_id=proj["voice_id"],
                work_dir=project_dir,
                explicit_paid_authorization=explicit_paid_authorization,
                project_id=project_id,
            )
            speech_durs = voice_res.get("speech_durations_by_scene") or {}
            if speech_durs and proj.get("production_mode", "AUTO") == ProductionMode.AUTO.value:
                target_frames = int(round(proj["target_duration_sec"] * proj["fps"]))
                synced_scenes = self.voice_director.sync_scenes_to_speech_pacing(
                    scenes,
                    fps=proj["fps"],
                    measured_durations=speech_durs,
                    target_total_frames=target_frames,
                )
                if [s.duration_frames for s in synced_scenes] != [s.duration_frames for s in scenes]:
                    scenes = synced_scenes
                    voice_res = self.voice_director.prepare_voiceover(
                        scenes=scenes,
                        voice_id=proj["voice_id"],
                        work_dir=project_dir,
                        explicit_paid_authorization=explicit_paid_authorization,
                        project_id=project_id,
                    )
                    self.log_job(
                        job_id,
                        "INFO",
                        "VoiceDirector",
                        f"Timeline riallineata alla durata vocale reale ({sum(s.duration_frames for s in scenes)} frame esatti).",
                    )
            if voice_res.get("substitution_notice"):
                self.log_job(job_id, "WARN", "VoiceDirector", voice_res["substitution_notice"])
            else:
                self.log_job(job_id, "INFO", "VoiceDirector", f"Voiceover pronto ({voice_res['status']}).")
            completed.append("Voiceover")
            check_cancelled()

            # 5. Sound Design & Mix
            self._update_job(job_id, JobState.SOUND_DESIGN, "Sound design e mix audio", completed)
            existing_ap = proj.get("audio_plan") or {}
            pref_music_id = (existing_ap.get("music") or {}).get("id")
            audio_plan = self.sound_designer.plan_audio(
                scenes=scenes,
                preset=proj["preset"],
                preferred_music_id=pref_music_id,
                include_music=existing_ap.get("include_music", True),
                music_start_sec=float(existing_ap.get("music_start_sec") or 0.0),
                music_volume=existing_ap.get("music_volume"),
                voice_volume=float(existing_ap.get("voice_volume") or 1.05),
            )
            total_sec = sum(s.duration_frames for s in scenes) / proj["fps"]
            mix_wav = project_dir / "audio_mix.wav"
            voice_path = Path(voice_res["audio_path"]) if voice_res.get("audio_path") else None
            self.sound_designer.render_mix(
                total_duration_sec=total_sec,
                audio_plan=audio_plan,
                voice_audio_path=voice_path,
                output_wav=mix_wav,
            )
            completed.append("Sound design")
            self.log_job(job_id, "INFO", "SoundDesigner", "Mix audio (voce + musica CC0 + SFX) normalizzato senza clipping.")
            check_cancelled()

            # 6. Montaggio & Rendering
            self._update_job(job_id, JobState.MONTAGGIO, "Color grading e composizione scene", completed)
            completed.append("Montaggio")
            self._update_job(job_id, JobState.RENDERING, "Rendering MP4 1080x1920 25fps", completed)
            rendered_mp4 = project_dir / f"{project_id}_preview.mp4"
            prefs = proj.get("preferences") or {}
            caption_style = prefs.get("caption_style")
            hyperframes_cfg = prefs.get("hyperframes")
            edit_res = self.editor.render_reel(
                project_id=project_id,
                title=proj["title"],
                scenes=scenes,
                preset=proj["preset"],
                project_dir=project_dir,
                audio_mix_path=mix_wav,
                output_mp4=rendered_mp4,
                include_subtitles=include_subtitles,
                fps=proj["fps"],
                caption_style=caption_style,
                hyperframes=hyperframes_cfg,
            )
            completed.append("Rendering")
            self.log_job(job_id, "INFO", "CinematicEditor", f"Render completato: {rendered_mp4.name}")
            check_cancelled()

            # 7. Controllo qualità & Export Bundle
            self._update_job(job_id, JobState.CONTROLLO_QUALITA, "Verifica tecnica, visiva e narrativa", completed)
            proj_updated = {
                **proj,
                "concept": concept_dict,
                "storyboard": [s.model_dump() for s in scenes],
                "edit_plan": {**edit_res, "voice": voice_res},
                "audio_plan": audio_plan,
            }
            bundle = self.export_manager.export_bundle(
                project=proj_updated,
                scenes=scenes,
                project_dir=project_dir,
                main_mp4=rendered_mp4,
                voice_audio_path=voice_path,
                audio_plan=audio_plan,
                include_no_subtitles_variant=include_no_subtitles_variant,
                include_no_music_variant=include_no_music_variant,
                fps=proj["fps"],
            )
            completed.append("Controllo qualità")

            # Persist final state & usage tracking
            used_assets = [s.selected_asset_id for s in scenes if s.selected_asset_id]
            self.vault.mark_project_usage(used_assets, project_id)
            self.creative.anti_rep.record_reel(
                project_id=project_id,
                title=proj["title"],
                theme=proj["theme"],
                hook=concept.hook,
                ending=concept.ending,
                phrases=[s.subtitle_text for s in scenes if s.subtitle_text],
                asset_ids=used_assets,
                music_id=(audio_plan.get("music") or {}).get("id"),
                preset=proj["preset"],
            )

            with self.db.session() as conn:
                conn.execute(
                    """
                    UPDATE projects SET
                        status = ?, concept_json = ?, storyboard_json = ?,
                        edit_plan_json = ?, audio_plan_json = ?, qc_report_json = ?,
                        export_bundle_json = ?, output_video_path = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        JobState.COMPLETATO.value,
                        json.dumps(concept_dict, ensure_ascii=False),
                        json.dumps([s.model_dump() for s in scenes], ensure_ascii=False),
                        json.dumps(proj_updated["edit_plan"], ensure_ascii=False),
                        json.dumps(audio_plan, ensure_ascii=False),
                        json.dumps(bundle["qc"], ensure_ascii=False),
                        json.dumps(bundle, ensure_ascii=False),
                        bundle["reel_mp4"],
                        utcnow_iso(),
                        project_id,
                    ),
                )
            self.save_project_snapshot(project_id, "Produzione completata e verificata QC", "ExportManager")
            self._update_job(
                job_id,
                JobState.COMPLETATO,
                "Completato",
                completed,
                checkpoint={"reel_mp4": bundle["reel_mp4"]},
            )
            self.log_job(job_id, "INFO", "ExportManager", f"Esportazione verificata pronta: {bundle['reel_mp4']}")
            return {"project": self.get_project(project_id), "job": self.get_job(job_id)}

        except InterruptedError as exc:
            self.log_job(job_id, "WARN", "KoraDirector", str(exc))
            return {"project": self.get_project(project_id), "job": self.get_job(job_id)}
        except Exception as exc:
            self._update_job(
                job_id,
                JobState.ERRORE,
                "Errore di produzione",
                completed,
                error_message=str(exc),
            )
            with self.db.session() as conn:
                conn.execute(
                    "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                    (JobState.ERRORE.value, utcnow_iso(), project_id),
                )
            self.log_job(job_id, "ERROR", "KoraDirector", f"Errore: {exc}")
            return {"project": self.get_project(project_id), "job": self.get_job(job_id)}

    def apply_nl_revision_and_rerender(
        self,
        project_id: str,
        instruction: str,
        explicit_paid_authorization: bool = False,
    ) -> dict[str, Any]:
        """Apply natural-language revision to targeted scenes only and selectively re-render."""
        proj = self.get_project(project_id)
        if not proj:
            raise KeyError(f"Progetto {project_id} non trovato")
        scenes = [StoryboardScene(**s) for s in proj["storyboard"]]
        audio_plan = proj.get("audio_plan") or {}
        new_scenes, new_audio, proposal = self.revision_engine.apply_revision(
            instruction, scenes, audio_plan
        )

        with self.db.session() as conn:
            conn.execute(
                """
                UPDATE projects SET storyboard_json = ?, audio_plan_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps([s.model_dump() for s in new_scenes], ensure_ascii=False),
                    json.dumps(new_audio, ensure_ascii=False),
                    utcnow_iso(),
                    project_id,
                ),
            )
        self.save_project_snapshot(
            project_id,
            f"Revisione selettiva: {instruction}",
            "RevisionEngine",
        )
        job = self.create_job(project_id, "selective_revision")
        self.log_job(
            job["id"],
            "INFO",
            "RevisionEngine",
            f"Modifica applicata solo alle scene {proposal.target_scenes} / global={proposal.global_changes}. Scene preservate: {proposal.preserved_scenes}",
        )
        result = self.run_production_pipeline(
            project_id=project_id,
            job_id=job["id"],
            explicit_paid_authorization=explicit_paid_authorization,
        )
        result["revision_proposal"] = proposal.model_dump()
        return result

    def sync_project_audio_and_caption(
        self,
        project_id: str,
        caption_style: Optional[str] = None,
        hyperframes: Optional[dict[str, Any]] = None,
        music_id: Optional[str] = None,
        include_music: Optional[bool] = None,
        music_start_sec: Optional[float] = None,
        music_volume: Optional[float] = None,
        voice_volume: Optional[float] = None,
        auto_sync_scenes_to_voice: bool = False,
    ) -> dict[str, Any]:
        """Update caption style & Hyperframes options, music sync parameters, and optionally align scene durations to speech."""
        proj = self.get_project(project_id)
        if not proj:
            raise KeyError(f"Progetto {project_id} non trovato")

        prefs = dict(proj.get("preferences") or {})
        existing_hf = dict(prefs.get("hyperframes") or {})
        if hyperframes is not None:
            existing_hf.update(hyperframes)
            prefs["hyperframes"] = existing_hf
        if caption_style is not None:
            prefs["caption_style"] = caption_style
            existing_hf["caption_style"] = caption_style
            prefs["hyperframes"] = existing_hf
        elif existing_hf.get("caption_style"):
            prefs["caption_style"] = existing_hf["caption_style"]

        scenes = [StoryboardScene(**s) for s in (proj.get("storyboard") or [])]
        if auto_sync_scenes_to_voice and scenes:
            scenes = self.voice_director.sync_scenes_to_speech_pacing(scenes, fps=proj["fps"])

        existing_ap = proj.get("audio_plan") or {}
        resolved_music_id = music_id if music_id is not None else (existing_ap.get("music") or {}).get("id")
        new_ap = self.sound_designer.plan_audio(
            scenes=scenes,
            preset=proj["preset"],
            preferred_music_id=resolved_music_id,
            include_music=include_music if include_music is not None else existing_ap.get("include_music", True),
            music_start_sec=music_start_sec if music_start_sec is not None else float(existing_ap.get("music_start_sec") or 0.0),
            music_volume=music_volume if music_volume is not None else existing_ap.get("music_volume"),
            voice_volume=voice_volume if voice_volume is not None else float(existing_ap.get("voice_volume") or 1.05),
        )
        total_frames = sum(s.duration_frames for s in scenes)
        target_dur = round(total_frames / max(1, proj["fps"]), 2)

        with self.db.session() as conn:
            conn.execute(
                """
                UPDATE projects
                SET storyboard_json = ?, audio_plan_json = ?, preferences_json = ?,
                    total_frames = ?, target_duration_sec = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps([s.model_dump() for s in scenes], ensure_ascii=False),
                    json.dumps(new_ap, ensure_ascii=False),
                    json.dumps(prefs, ensure_ascii=False),
                    total_frames,
                    target_dur,
                    utcnow_iso(),
                    project_id,
                ),
            )
        self.save_project_snapshot(
            project_id,
            "Aggiornamento sincronizzazione audio/musica/caption",
            "KoraDirector",
        )
        return self.get_project(project_id)  # type: ignore
