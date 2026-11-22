"""Editor delle scene di un progetto KORA (v3.2).

Permette di intervenire sulla produzione dopo la scelta del tema:
aggiungere/togliere scene, cambiare il materiale, riscrivere il testo
(anche con il motore AI, se collegato) e seguire l'avanzamento reale.

Tutto lavora sullo storyboard persistito del progetto e rinumera le scene
in modo che frame e durate restino coerenti.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

import kora_studio.config as cfg
from kora_studio.config import DEFAULT_FPS
from kora_studio.db.models import CameraMotion, SceneTransition, StoryboardScene


def _frames_from_sec(sec: float, fps: int = DEFAULT_FPS) -> int:
    return max(1, int(round(max(0.6, sec) * fps)))


def _protect_outro(scenes: list[StoryboardScene]) -> list[StoryboardScene]:
    """Una sola chiusura istituzionale, sempre ultima.

    L'ordine della lista è la verità: non si riordina per numero di scena,
    altrimenti una scena aggiunta prima del logo finirebbe dopo di esso.
    """
    outros = [s for s in scenes if s.is_outro]
    if not outros:
        return scenes
    keeper = outros[0]
    if len(outros) > 1:  # deduplica eventuali chiusure doppie
        for extra in outros[1:]:
            if extra in scenes:
                scenes.remove(extra)
    if scenes[-1] is not keeper:
        scenes.remove(keeper)
        scenes.append(keeper)
    return scenes


def renumber(scenes: list[StoryboardScene], fps: int = DEFAULT_FPS) -> list[StoryboardScene]:
    """Rinumera le scene mantenendo l'ordine effettivo e l'outro in fondo."""
    ordered = _protect_outro(list(scenes))
    cursor = 0
    for index, scene in enumerate(ordered, start=1):
        scene.scene_number = index
        frames = max(1, int(scene.duration_frames or _frames_from_sec(scene.duration_sec, fps)))
        scene.start_frame = cursor
        scene.end_frame = cursor + frames
        scene.duration_frames = frames
        scene.start_sec = round(cursor / fps, 3)
        scene.end_sec = round((cursor + frames) / fps, 3)
        scene.duration_sec = round(frames / fps, 3)
        cursor += frames
    return ordered


def ensure_scene_uids(scenes: list[StoryboardScene]) -> list[StoryboardScene]:
    """Identità stabile per ogni scena: sopravvive a rinumerazioni e riordini."""
    import uuid

    for scene in scenes:
        if not getattr(scene, "scene_uid", None):
            scene.scene_uid = "sc_" + uuid.uuid4().hex[:10]
    return scenes


def _renumber_safe(scenes: list[StoryboardScene], fps: int = DEFAULT_FPS) -> list[StoryboardScene]:
    """Rinumera garantendo uid e chiusura protetta."""
    return renumber(ensure_scene_uids(scenes), fps)


LOCK_KEYS = ("scene", "visual", "voice", "duration", "direction", "caption")

MOTION_EASINGS = ("linear", "ease_in", "ease_out", "ease_in_out")
COMPOSITIONS = ("regola_terzi", "centro", "orizzonte_basso", "orizzonte_alto", "simmetria")
FRAMINGS = ("primissimo_piano", "primo_piano", "piano_medio", "campo_lungo", "dettaglio")
MIN_MOTION_INTENSITY = 0.2
MAX_MOTION_INTENSITY = 2.0


def _scene_summary(scene: StoryboardScene) -> dict[str, Any]:
    return {
        "scene_number": scene.scene_number,
        "start_sec": scene.start_sec,
        "end_sec": scene.end_sec,
        "duration_sec": scene.duration_sec,
        "duration_frames": scene.duration_frames,
        "spoken_text": scene.spoken_text,
        "subtitle_text": scene.subtitle_text,
        "voice_id": scene.voice_id,
        "asset_id": scene.selected_asset_id,
        "alternative_asset_id": scene.alternative_asset_id,
        "visual_rationale": scene.visual_rationale,
        "narrative_goal": scene.narrative_goal,
        "shot_framing": scene.shot_framing,
        "camera_motion": scene.camera_motion,
        "transition": scene.transition,
        "is_outro": scene.is_outro,
        "preview_status": scene.preview_status,
        "approval_status": scene.approval_status,
        "generative_prompt": scene.generative_prompt,
        # v3.5
        "scene_uid": scene.scene_uid,
        "locks": dict(scene.locks or {}),
        "is_locked": bool((scene.locks or {}).get("scene")),
        "motion_intensity": scene.motion_intensity,
        "motion_easing": scene.motion_easing,
        "motion_speed": getattr(scene, "motion_speed", 1.0),
        "composition": getattr(scene, "composition", "regola_terzi"),
        "crop_focus": scene.crop_focus,
        "caption_style": scene.caption_style,
        "caption_overrides": dict(scene.caption_overrides or {}),
        "title_text": scene.title_text,
        "asset_history": list(getattr(scene, "asset_history", []) or []),
        "authenticity_status": scene.authenticity_status,
        "shot_plan": scene.shot_plan,
        "shot_source": getattr(scene, "shot_source", "smart_shot"),
    }


class SceneEditor:
    """Operazioni sulle scene con persistenza e snapshot di progetto."""

    def __init__(self, director: Any) -> None:
        self.director = director
        self.vault = director.vault

    # ---------------------------------------------------------------- lettura
    def list_scenes(self, project_id: str) -> dict[str, Any]:
        proj, scenes = self.director._project_scenes(project_id)
        project_dir = cfg.PROJECTS_DIR / project_id
        available: list[dict[str, Any]] = []
        for asset in self.vault.list_assets():
            if asset.get("status") not in (None, "disponibile", "ready"):
                continue
            path = Path(asset.get("working_path") or asset.get("original_path") or "")
            available.append(
                {
                    "id": asset["id"],
                    "label": asset.get("filename") or path.name,
                    "media_type": asset.get("media_type"),
                    "category": asset.get("category"),
                    "duration_sec": asset.get("duration_sec"),
                    "path_ok": path.is_file(),
                }
            )
        ensure_scene_uids(scenes)
        thumbs: dict[str, str] = {}
        for scene in scenes:
            if scene.selected_asset_id and scene.selected_asset_id not in thumbs:
                thumbs[scene.selected_asset_id] = f"/api/vault/{scene.selected_asset_id}/thumb"
        return {
            "project_id": project_id,
            "title": proj.get("title"),
            "preset": proj.get("preset"),
            "fps": proj.get("fps") or DEFAULT_FPS,
            "total_sec": round(sum(s.duration_sec for s in scenes), 3),
            "total_frames": sum(s.duration_frames for s in scenes),
            "thumbnails": thumbs,
            "scenes": [_scene_summary(s) for s in scenes],
            "available_assets": available,
            "has_voice_mix": (project_dir / "audio_mix.wav").is_file(),
        }

    # --------------------------------------------------------------- modifica
    def update_scene(self, project_id: str, number: int, patch: dict[str, Any]) -> dict[str, Any]:
        _, scenes = self.director._project_scenes(project_id)
        scene = next((s for s in scenes if s.scene_number == number), None)
        if scene is None:
            raise KeyError(f"Scena {number} non trovata")

        locks = dict(scene.locks or {})
        ensure_scene_uids(scenes)

        # Blocchi: un blocco scena ferma tutto, gli altri fermi la sola componente.
        protected_fields = []
        if locks.get("scene"):
            protected_fields.append("scena bloccata")
        if locks.get("voice") and {"spoken_text", "subtitle_text"} & set(patch):
            protected_fields.append("voiceover bloccato")
        if locks.get("duration") and patch.get("duration_sec") is not None:
            protected_fields.append("durata bloccata")
        if locks.get("direction") and {"shot_framing", "camera_motion", "motion_intensity", "motion_easing", "motion_speed", "composition", "transition", "zoom_level"} & set(patch):
            protected_fields.append("regia bloccata")
        if locks.get("caption") and {"caption_style", "caption_overrides", "title_text", "subtitle_text"} & set(patch):
            protected_fields.append("caption bloccata")
        if locks.get("visual") and {"asset_id", "crop_focus"} & set(patch):
            protected_fields.append("visual bloccato")
        if protected_fields:
            return {
                "ok": False,
                "blocked": True,
                "reason": "Modifica non applicata: " + ", ".join(protected_fields) + ".",
                "locks": locks,
                "scenes": [_scene_summary(s) for s in scenes],
            }

        # L'outro è identità: non cambia materiale né diventa scena ordinaria.
        if scene.is_outro and ("asset_id" in patch and patch.get("asset_id")):
            return {
                "ok": False,
                "blocked": True,
                "reason": "La scena finale (logo e payoff) mantiene il materiale istituzionale.",
                "scenes": [_scene_summary(s) for s in scenes],
            }

        audio_touch = False
        if "spoken_text" in patch and patch["spoken_text"] is not None:
            text = str(patch["spoken_text"]).strip()
            if text:
                guard = self.director.guardian.validate_copy(text)
                if not guard.get("approved"):
                    raise ValueError(
                        "Testo non conforme al brand KORA: "
                        + "; ".join(guard.get("violations") or ["violazione non specificata"])
                    )
            scene.spoken_text = text
            # single source of truth: sottotitoli derivati dal testo ufficiale
            if not str(patch.get("subtitle_text") or "").strip():
                scene.subtitle_text = text
            audio_touch = True
        if "subtitle_text" in patch and patch["subtitle_text"] is not None:
            scene.subtitle_text = str(patch["subtitle_text"]).strip()
        if "asset_id" in patch and patch["asset_id"]:
            asset = self.vault.get_asset(str(patch["asset_id"]))
            if not asset:
                raise ValueError(f"Asset {patch['asset_id']} non presente nel Media Vault")
            if scene.selected_asset_id and scene.selected_asset_id != asset["id"]:
                history = list(getattr(scene, "asset_history", []) or [])
                history.append(scene.selected_asset_id)
                scene.asset_history = history[-10:]
            scene.selected_asset_id = asset["id"]
            if not scene.is_outro:
                scene.is_outro = False
        if "duration_sec" in patch and patch["duration_sec"] is not None:
            scene.duration_frames = _frames_from_sec(float(patch["duration_sec"]))
            audio_touch = True
        if "crop_focus" in patch:
            focus = patch.get("crop_focus")
            if focus is None:
                scene.crop_focus = None
            else:
                scene.crop_focus = {
                    "x": min(1.0, max(0.0, float(focus.get("x", 0.5)))),
                    "y": min(1.0, max(0.0, float(focus.get("y", 0.5)))),
                    "zoom": min(2.5, max(1.0, float(focus.get("zoom", 1.0)))),
                }
        if patch.get("camera_motion"):
            scene.camera_motion = str(patch["camera_motion"])
        if patch.get("transition"):
            scene.transition = str(patch["transition"])
        if patch.get("narrative_goal"):
            scene.narrative_goal = str(patch["narrative_goal"])[:200]
        if patch.get("shot_framing"):
            framing = str(patch["shot_framing"]).strip().lower()
            if framing not in FRAMINGS:
                raise ValueError("Inquadratura non riconosciuta: " + ", ".join(FRAMINGS))
            scene.shot_framing = framing
        if patch.get("motion_easing"):
            easing = str(patch["motion_easing"]).strip().lower()
            if easing not in MOTION_EASINGS:
                raise ValueError("Curva di movimento non riconosciuta: " + ", ".join(MOTION_EASINGS))
            scene.motion_easing = easing
        if patch.get("motion_intensity") is not None:
            value = float(patch["motion_intensity"])
            scene.motion_intensity = round(min(MAX_MOTION_INTENSITY, max(MIN_MOTION_INTENSITY, value)), 2)
        if patch.get("motion_speed") is not None:
            value = float(patch["motion_speed"])
            scene.motion_speed = round(min(2.0, max(0.5, value)), 2)
        if patch.get("composition"):
            comp = str(patch["composition"]).strip().lower()
            if comp not in COMPOSITIONS:
                raise ValueError("Composizione non riconosciuta: " + ", ".join(COMPOSITIONS))
            scene.composition = comp
        if "caption_style" in patch:
            scene.caption_style = patch["caption_style"] or None
        if "caption_overrides" in patch and patch["caption_overrides"] is not None:
            scene.caption_overrides = dict(patch["caption_overrides"])
        scene.preview_status = "pending"
        scene.approval_status = "modificata"
        order = _renumber_safe(scenes)
        self.director._persist_scenes(project_id, order, f"Scena {number} modificata a mano")
        self.director.invalidate_preview(project_id)
        if audio_touch:
            self.director.invalidate_audio(project_id, reason=f"scena {number}: testo o durata cambiati")
        return {"ok": True, "audio_invalidated": audio_touch, "scenes": [_scene_summary(s) for s in order]}

    def add_scene(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        _, scenes = self.director._project_scenes(project_id)
        asset_id = str(payload.get("asset_id") or "").strip()
        if not asset_id:
            raise ValueError("Serve un materiale (foto o video) per la nuova scena")
        asset = self.vault.get_asset(asset_id)
        if not asset:
            raise ValueError("Asset non presente nel Media Vault")
        duration = float(payload.get("duration_sec") or 4.0)
        if not (1.0 <= duration <= 15.0):
            raise ValueError("La durata di una scena deve stare tra 1 e 15 secondi")

        new_scene = StoryboardScene(
            scene_number=len(scenes) + 1,
            start_frame=0,
            end_frame=_frames_from_sec(duration),
            duration_frames=_frames_from_sec(duration),
            start_sec=0.0,
            end_sec=duration,
            duration_sec=duration,
            narrative_goal=str(payload.get("narrative_goal") or "Scena aggiunta a mano")[:200],
            spoken_text=str(payload.get("spoken_text") or "").strip(),
            subtitle_text=str(payload.get("subtitle_text") or payload.get("spoken_text") or "").strip(),
            voice_id=str(payload.get("voice_id") or (scenes[0].voice_id if scenes else "brando")),
            selected_asset_id=asset["id"],
            visual_rationale="Scena inserita manualmente dall'utente",
            shot_framing=str(payload.get("shot_framing") or "piano_medio"),
            camera_motion=str(payload.get("camera_motion") or CameraMotion.SLOW_PUSH_IN.value),
            transition=str(payload.get("transition") or SceneTransition.CROSS_DISSOLVE.value),
            preview_status="pending",
            approval_status="modificata",
            scene_uid="sc_" + __import__("uuid").uuid4().hex[:10],
            motion_intensity=float(payload.get("motion_intensity") or 1.0),
            motion_easing=str(payload.get("motion_easing") or "ease_in_out"),
            composition=str(payload.get("composition") or "regola_terzi"),
        )

        # inserimento: di default in fondo, prima dell'outro se esiste
        insert_after = payload.get("insert_after")
        if insert_after is not None:
            position = next(
                (i for i, s in enumerate(scenes) if s.scene_number == int(insert_after)), len(scenes) - 1
            )
            scenes.insert(position + 1, new_scene)
        else:
            outro_index = next((i for i, s in enumerate(scenes) if s.is_outro), None)
            if outro_index is None:
                scenes.append(new_scene)
            else:
                scenes.insert(outro_index, new_scene)

        order = _renumber_safe(scenes)
        self.director._persist_scenes(project_id, order, "Scena aggiunta a mano")
        self.director.invalidate_preview(project_id)
        self.director.invalidate_audio(project_id, reason="nuova scena: timeline cambiata")
        added = next(s for s in order if s.selected_asset_id == asset["id"] and s.narrative_goal == new_scene.narrative_goal)
        return {
            "ok": True,
            "added": _scene_summary(added),
            "scenes": [_scene_summary(s) for s in order],
        }

    def remove_scene(self, project_id: str, number: int) -> dict[str, Any]:
        _, scenes = self.director._project_scenes(project_id)
        target = next((s for s in scenes if s.scene_number == number), None)
        if target is not None and target.is_outro:
            # l'identità della chiusura non si elimina con un clic distratto
            order = _renumber_safe(scenes)
            return {
                "ok": False,
                "blocked": True,
                "removed": None,
                "reason": "La scena finale (logo KORA e payoff) non si elimina: è l'identità del Reel.",
                "scenes": [_scene_summary(s) for s in order],
            }
        remaining = [s for s in scenes if s.scene_number != number]
        if len(remaining) == len(scenes):
            raise KeyError(f"Scena {number} non trovata")
        if not remaining:
            raise ValueError("Il reel deve mantenere almeno una scena")
        order = _renumber_safe(remaining)
        self.director._persist_scenes(project_id, order, f"Scena {number} rimossa")
        self.director.invalidate_preview(project_id)
        self.director.invalidate_audio(project_id, reason="scena rimossa: timeline cambiata")
        return {"ok": True, "removed": number, "scenes": [_scene_summary(s) for s in order]}

    # ------------------------------------------------- strutture e blocchi
    def duplicate_scene(self, project_id: str, number: int) -> dict[str, Any]:
        """Duplica una scena subito dopo di sé; l'outro non si duplica."""
        _, scenes = self.director._project_scenes(project_id)
        target = next((s for s in scenes if s.scene_number == number), None)
        if target is None:
            raise KeyError(f"Scena {number} non trovata")
        if target.is_outro:
            return {
                "ok": False,
                "blocked": True,
                "reason": "La chiusura istituzionale non si duplica: il payoff resta unico.",
                "scenes": [_scene_summary(s) for s in scenes],
            }
        clone = target.model_copy(deep=True)
        clone.scene_uid = "sc_" + __import__("uuid").uuid4().hex[:10]
        clone.locks = {}
        clone.approval_status = "modificata"
        clone.preview_status = "pending"
        clone.asset_history = []
        position = next(i for i, s in enumerate(scenes) if s.scene_number == number)
        scenes.insert(position + 1, clone)
        order = _renumber_safe(scenes)
        self.director._persist_scenes(project_id, order, f"Scena {number} duplicata")
        self.director.invalidate_preview(project_id)
        self.director.invalidate_audio(project_id, reason="scena duplicata: timeline cambiata")
        return {
            "ok": True,
            "duplicated_from": number,
            "added": _scene_summary(clone),
            "scenes": [_scene_summary(s) for s in order],
        }

    def move_scene(self, project_id: str, number: int, direction: str) -> dict[str, Any]:
        """Sposta una scena di una posizione. L'outro resta in fondo."""
        _, scenes = self.director._project_scenes(project_id)
        target = next((s for s in scenes if s.scene_number == number), None)
        if target is None:
            raise KeyError(f"Scena {number} non trovata")
        if target.is_outro:
            return {"ok": False, "blocked": True, "reason": "La chiusura resta in fondo al Reel."}
        index = scenes.index(target)
        step = -1 if str(direction).lower() in ("up", "su", "prev", "-1") else 1
        dest = index + step
        if dest < 0 or dest >= len(scenes) or scenes[dest].is_outro:
            return {"ok": False, "blocked": True, "reason": "Spostamento non consentito oltre il bordo o l'outro."}
        scenes[index], scenes[dest] = scenes[dest], scenes[index]
        order = _renumber_safe(scenes)
        self.director._persist_scenes(project_id, order, f"Scena {number} spostata")
        self.director.invalidate_preview(project_id)
        self.director.invalidate_audio(project_id, reason="ordine delle scene cambiato")
        return {"ok": True, "scenes": [_scene_summary(s) for s in order]}

    def reorder(self, project_id: str, ordered_numbers: list[int]) -> dict[str, Any]:
        """Riordino esplicito dalla timeline, per identità stabile (uid)."""
        _, scenes = self.director._project_scenes(project_id)
        by_number = {s.scene_number: s for s in scenes}
        wanted = [by_number.get(int(n)) for n in ordered_numbers]
        if any(item is None for item in wanted) or len(wanted) != len(scenes):
            raise ValueError("Riordino non valido: servono tutte le scene, una volta ciascuna.")
        order = _renumber_safe([s for s in wanted if s is not None])
        self.director._persist_scenes(project_id, order, "Riordino timeline")
        self.director.invalidate_preview(project_id)
        self.director.invalidate_audio(project_id, reason="ordine delle scene cambiato")
        return {"ok": True, "scenes": [_scene_summary(s) for s in order]}

    def set_locks(self, project_id: str, number: int, locks: dict[str, bool]) -> dict[str, Any]:
        """Imposta i blocchi per componente. L'outro resta comunque protetto."""
        _, scenes = self.director._project_scenes(project_id)
        scene = next((s for s in scenes if s.scene_number == number), None)
        if scene is None:
            raise KeyError(f"Scena {number} non trovata")
        clean: dict[str, bool] = {}
        for key in LOCK_KEYS:
            if key in locks:
                if bool(locks[key]):
                    clean[key] = True
        if scene.is_outro and clean.get("scene") is False:
            clean.pop("scene", None)
        scene.locks = clean
        if clean:
            scene.approval_status = "bloccata"
        order = _renumber_safe(scenes)
        self.director._persist_scenes(project_id, order, f"Blocchi scena {number} aggiornati")
        return {"ok": True, "locks": clean, "scenes": [_scene_summary(s) for s in order]}

    def restore_asset(self, project_id: str, number: int) -> dict[str, Any]:
        """Ripristina il materiale precedente della scena, se registrato."""
        _, scenes = self.director._project_scenes(project_id)
        scene = next((s for s in scenes if s.scene_number == number), None)
        if scene is None:
            raise KeyError(f"Scena {number} non trovata")
        history = list(getattr(scene, "asset_history", []) or [])
        if not history:
            return {"ok": False, "blocked": True, "reason": "Nessun materiale precedente registrato."}
        previous = history.pop()
        if not self.vault.get_asset(previous):
            return {"ok": False, "blocked": True, "reason": "Il materiale precedente non è più nel Vault."}
        if scene.selected_asset_id:
            history.append(scene.selected_asset_id)
        scene.selected_asset_id = previous
        scene.asset_history = history[-10:]
        scene.preview_status = "pending"
        scene.approval_status = "modificata"
        order = _renumber_safe(scenes)
        self.director._persist_scenes(project_id, order, f"Materiale scena {number} ripristinato")
        self.director.invalidate_preview(project_id)
        return {"ok": True, "asset_id": previous, "audio_untouched": True, "scenes": [_scene_summary(s) for s in order]}

    # ------------------------------------------------------------ testo AI
    def suggest_text(self, project_id: str, number: int, instruction: str = "") -> dict[str, Any]:
        proj, scenes = self.director._project_scenes(project_id)
        scene = next((s for s in scenes if s.scene_number == number), None)
        if scene is None:
            raise KeyError(f"Scena {number} non trovata")

        asset = self.vault.get_asset(scene.selected_asset_id) if scene.selected_asset_id else None
        context = {
            "titolo": proj.get("title"),
            "tema": proj.get("theme") or proj.get("title"),
            "obiettivo": proj.get("objective") or "",
            "preset": proj.get("preset"),
            "posizione": number,
            "totale_scene": len(scenes),
            "materiale": (asset or {}).get("category") or (asset or {}).get("filename") or "materiale KORA",
            "direzione": "prima scena: hook breve e concreto"
            if number == 1
            else ("scena finale: chiusura con firma KORA" if number == len(scenes) else "scena centrale: dettaglio sensoriale"),
            "prima": scenes[number - 2].spoken_text if number > 1 else "",
            "dopo": scenes[number].spoken_text if number < len(scenes) else "",
            "istruzione": instruction.strip(),
            "testo_attuale": scene.spoken_text,
        }

        text, source, note = self._compose_text(context)
        if not text:
            raise ValueError("Nessun testo generato: riprova o scrivilo a mano")

        guard = self.director.guardian.validate_copy(text)
        if not guard.get("approved"):
            # nessun testo applicato: si restituisce solo una proposta alternativa dichiarata
            return {
                "ok": False,
                "applied": False,
                "scene_number": number,
                "text": text,
                "previous_text": scene.spoken_text,
                "source": "engine_scartato_dal_guardian",
                "violations": guard.get("violations") or [],
                "note": "Proposta NON applicata: il Brand Guardian l'ha scartata. "
                + "; ".join(guard.get("violations") or []),
            }

        # Proposta, non applicazione: l'utente decide con «Applica».
        return {
            "ok": True,
            "applied": False,
            "scene_number": number,
            "text": text,
            "previous_text": scene.spoken_text,
            "source": source,
            "note": note,
        }

    def apply_text(self, project_id: str, number: int, text: str, source: str = "manuale") -> dict[str, Any]:
        """Applica una proposta di testo (o un testo scritto a mano) alla scena."""
        _, scenes = self.director._project_scenes(project_id)
        scene = next((s for s in scenes if s.scene_number == number), None)
        if scene is None:
            raise KeyError(f"Scena {number} non trovata")
        clean = (text or "").strip()
        if not clean:
            raise ValueError("Testo vuoto: nulla da applicare")
        guard = self.director.guardian.validate_copy(clean)
        if not guard.get("approved"):
            raise ValueError(
                "Testo non conforme al brand KORA: "
                + "; ".join(guard.get("violations") or ["violazione non specificata"])
            )
        previous = scene.spoken_text
        scene.spoken_text = clean
        scene.subtitle_text = clean
        scene.preview_status = "pending"
        scene.approval_status = "modificata"
        self.director._persist_scenes(project_id, scenes, f"Testo scena {number} applicato ({source})")
        self.director.invalidate_preview(project_id)
        self.director.invalidate_audio(project_id, reason=f"testo scena {number} cambiato")
        return {"ok": True, "applied": True, "scene_number": number, "text": clean, "previous_text": previous}

    def _compose_text(self, ctx: dict[str, Any]) -> tuple[str, str, str]:
        engine = getattr(self.director, "ai_engine", None)
        if engine is not None:
            try:
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "Sei il copywriter di KORA, ospitalità mediterranea a Policoro. "
                            "Scrivi UNA sola riga di voice-over in italiano, 8-16 parole, concreta e sensoriale, "
                            "senza aggettivi pubblicitari, senza prezzi, senza emoji, senza virgolette. "
                            "Deve suonare come una frase detta, non come uno slogan. "
                            'Rispondi solo con JSON: {"line": "..."}'
                        ),
                    },
                    {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)},
                ]
                result = engine.call(
                    "copywriter",
                    messages,
                    json_mode=True,
                    timeout=45.0,
                    request_label="scene-text",
                )
                candidate = self._parse_line(result.text or "")
                if 4 <= len(candidate.split()) <= 24:
                    return candidate, f"engine:{getattr(result, 'provider', 'ai')}", "Testo generato dal motore AI attivo."
            except Exception as exc:  # motore assente/limite: si continua in locale, dichiarandolo
                return (
                    self._fallback_line(ctx),
                    "deterministic_offline_fallback",
                    f"Motore AI non disponibile ({type(exc).__name__}): proposta locale.",
                )
        return (
            self._fallback_line(ctx),
            "deterministic_offline_fallback",
            "Nessun motore AI collegato: proposta locale basata sul materiale della scena.",
        )

    @staticmethod
    def _parse_line(raw: str) -> str:
        """Parsing JSON strutturato; la regex resta solo come ultima risorsa."""
        text = (raw or "").strip()
        if not text:
            return ""
        for start, end in ((text.find("{"), text.rfind("}")),):
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                except Exception:
                    data = None
                if isinstance(data, dict):
                    line = data.get("line") or data.get("text") or data.get("voiceover")
                    if isinstance(line, str) and line.strip():
                        return re.sub(r"\s+", " ", line.strip().strip('"'))
                break
        # il modello ha risposto in prosa: si scarta, meglio del testo sbagliato
        if text.startswith("{") or '"line"' in text:
            return ""
        return re.sub(r"\s+", " ", text.strip().strip('"'))

    def _fallback_line(self, ctx: dict[str, Any]) -> str:
        """Riga locale deterministica ma variabile: dipende da scena, materiale e tema."""
        material = str(ctx.get("materiale") or "materiale").lower()
        seed = int(
            hashlib.md5(
                f"{ctx.get('titolo')}|{ctx.get('posizione')}|{material}|{ctx.get('testo_attuale')}".encode("utf-8")
            ).hexdigest(),
            16,
        )
        pool: list[str] = []
        if ctx.get("posizione") == 1:
            pool = [
                "La luce arriva prima delle parole, e non serve aggiungerne.",
                "Il primo suono della giornata è il mare che si muove.",
                "C'è un'ora in cui il posto è solo tuo: è questa.",
                "Nessuno ha fretta, e si sente dal rumore dei passi.",
            ]
        elif ctx.get("posizione") == ctx.get("totale_scene"):
            pool = [
                "Poi basta: resta la sensazione di essere stati bene.",
                "Il resto lo fa il tempo che ci metti a tornare.",
                "KORA. The Art of Being.",
                "Qui non si trattiene niente, nemmeno la luce.",
            ]
        elif "caffè" in material or "colazione" in material:
            pool = [
                "La tazzina è ancora calda quando arriva il sole sulla tovaglia.",
                "Il caffè si prende senza guardare l'ora.",
                "Zucchero, silenzio, e il rumore del cucchiaino.",
            ]
        elif "aperitivo" in material or "bicchiere" in material:
            pool = [
                "Il ghiaccio scende piano, la conversazione pure.",
                "Un calice che aspetta il tramonto senza fretta.",
                "Le voci si abbassano quando la luce si scalda.",
            ]
        elif "pergolato" in material or "ombre" in material:
            pool = [
                "L'ombra si sposta di un centimetro e cambia tutto.",
                "Sotto il pergolato l'aria resta ferma e la luce si fa trama.",
                "Il riparo migliore è quello che non si nota.",
            ]
        elif "mare" in material or "spiaggia" in material:
            pool = [
                "L'acqua arriva con lo stesso rumore, mai nello stesso modo.",
                "La sabbia bagnata riflette il cielo per qualche secondo.",
                "Chi esce dall'acqua porta il sale addosso fino a sera.",
            ]
        else:
            pool = [
                "Il legno del tavolo conserva l'ombra di mezzogiorno.",
                "Ogni dettaglio è stato scelto per invecchiare bene.",
                "Il vetro è pulito da prima che tu arrivassi.",
                "Una cura che si vede solo da vicino.",
            ]
        line = pool[seed % len(pool)]
        instruction = str(ctx.get("istruzione") or "").strip()
        if instruction:
            line = f"{line}"
        return line

    # ------------------------------------------------------------- avanzamento
    def progress(self, project_id: str) -> dict[str, Any]:
        proj, scenes = self.director._project_scenes(project_id)
        project_dir = cfg.PROJECTS_DIR / project_id
        audio_mix = project_dir / "audio_mix.wav"
        preview_manifest = project_dir / "preview" / "preview_manifest.json"
        preview_video = None
        if preview_manifest.is_file():
            try:
                preview_video = json.loads(preview_manifest.read_text(encoding="utf-8")).get("preview_mp4")
            except Exception:
                preview_video = None

        stages = [
            {
                "id": "concept",
                "label": "Concept e tema",
                "done": bool(proj.get("title")),
                "detail": proj.get("title") or "—",
            },
            {
                "id": "storyboard",
                "label": "Storyboard",
                "done": len(scenes) > 0,
                "detail": f"{len(scenes)} scene · {round(sum(s.duration_sec for s in scenes), 1)} s",
            },
            {
                "id": "assets",
                "label": "Materiali assegnati",
                "done": all(
                    s.is_outro or bool(self.vault.get_asset(s.selected_asset_id or "")) for s in scenes
                ),
                "detail": f"{sum(1 for s in scenes if s.selected_asset_id)}/{len(scenes)} scene con materiale",
            },
            {
                "id": "voiceover",
                "label": "Voce fuori campo",
                "done": audio_mix.is_file() and audio_mix.stat().st_size > 1000,
                "detail": "mix audio pronto" if audio_mix.is_file() else "da generare o caricare",
            },
            {
                "id": "preview",
                "label": "Anteprima cinematografica",
                "done": bool(preview_video) and Path(str(preview_video)).is_file(),
                "detail": "anteprima disponibile" if preview_video else "da generare",
            },
            {
                "id": "render",
                "label": "Reel finale 1080×1920",
                "done": bool(proj.get("output_video_path"))
                and Path(str(proj.get("output_video_path"))).is_file(),
                "detail": proj.get("output_video_path") or "non ancora renderizzato",
            },
        ]
        done = sum(1 for stage in stages if stage["done"])
        return {
            "project_id": project_id,
            "percent": int(round(done / len(stages) * 100)),
            "done": done,
            "total": len(stages),
            "stages": stages,
            "current": next((s["label"] for s in stages if not s["done"]), "completato"),
        }

    # ------------------------------------------------------------ prontezza
    def readiness(self, project_id: str) -> dict[str, Any]:
        """Cosa serve davvero, distinguendo anteprima visiva e render finale."""
        from kora_studio.media_tools import find_ffmpeg, find_ffprobe

        proj, scenes = self.director._project_scenes(project_id)
        fps = int(proj.get("fps") or 25)
        checks: list[dict[str, Any]] = []

        ffmpeg_path = find_ffmpeg()
        checks.append(
            {
                "id": "ffmpeg",
                "label": "FFmpeg",
                "ok": bool(ffmpeg_path),
                "detail": ffmpeg_path
                or "non trovato: installa ffmpeg (brew install ffmpeg) e riavvia KORA",
                "blocking": True,
                "scope": "preview",
            }
        )
        ffprobe_path = find_ffprobe()
        checks.append(
            {
                "id": "ffprobe",
                "label": "FFprobe",
                "ok": bool(ffprobe_path),
                "detail": ffprobe_path or "non trovato: serve per verificare il file prodotto",
                "blocking": False,
                "scope": "render",
            }
        )

        missing_assets = [
            s.scene_number for s in scenes if not s.is_outro and not self.vault.get_asset(s.selected_asset_id or "")
        ]
        checks.append(
            {
                "id": "assets",
                "label": "Materiale delle scene",
                "ok": not missing_assets,
                "detail": "tutte le scene hanno un asset"
                if not missing_assets
                else f"scene senza asset valido: {', '.join(map(str, missing_assets))}",
                "blocking": True,
                "scope": "preview",
            }
        )

        outros = [s for s in scenes if s.is_outro]
        outro_ok = len(outros) == 1 and scenes[-1].is_outro
        checks.append(
            {
                "id": "outro",
                "label": "Chiusura KORA (logo + payoff)",
                "ok": outro_ok,
                "detail": "outro unico e in fondo alla timeline"
                if outro_ok
                else f"outro trovati: {len(outros)}; ultimo in timeline: {bool(scenes and scenes[-1].is_outro)}",
                "blocking": True,
                "scope": "preview",
            }
        )
        if outro_ok:
            logo = self.vault.get_logo_asset()
            checks.append(
                {
                    "id": "logo",
                    "label": "Logo originale KORA",
                    "ok": bool(logo),
                    "detail": "logo presente" if logo else "logo originale mancante nel Media Vault",
                    "blocking": True,
                    "scope": "preview",
                }
            )

        total_frames = sum(sc.duration_frames for sc in scenes)
        target_frames = int(round(float(proj.get("target_duration_sec") or 0) * fps))
        duration_ok = target_frames == 0 or total_frames == target_frames
        checks.append(
            {
                "id": "duration",
                "label": "Durata vincolante",
                "ok": duration_ok,
                "detail": f"{total_frames} frame ({round(total_frames / fps, 1)} s) su {target_frames} richiesti"
                if target_frames
                else f"{total_frames} frame",
                "blocking": bool(target_frames) and not duration_ok,
                "scope": "render",
            }
        )

        def _norm(value: str) -> str:
            return re.sub(r"[^a-z0-9 ]+", "", (value or "").lower().replace("'", " ")).strip()

        mismatched = [
            sc.scene_number
            for sc in scenes
            if not sc.is_outro
            and sc.spoken_text.strip()
            and _norm(sc.spoken_text) != _norm(sc.subtitle_text)
        ]
        checks.append(
            {
                "id": "subtitle_source",
                "label": "Sottotitoli coerenti con la voce",
                "ok": not mismatched,
                "detail": "sottotitoli derivati dallo stesso testo"
                if not mismatched
                else f"scene con testo e sottotitoli diversi: {', '.join(map(str, mismatched))}",
                "blocking": False,
                "scope": "render",
            }
        )

        # verifiche narrative eseguibili PRIMA del rendering
        violations: list[str] = []
        for scene in scenes:
            if scene.is_outro:
                continue
            for field, value in (("testo", scene.spoken_text), ("sottotitoli", scene.subtitle_text)):
                guard = self.director.guardian.validate_copy(value or "")
                if not guard.get("approved"):
                    for item in guard.get("violations") or []:
                        violations.append(f"scena {scene.scene_number} ({field}): {item}")
        checks.append(
            {
                "id": "brand_copy",
                "label": "Testi conformi al brand",
                "ok": not violations,
                "detail": "nessuna violazione" if not violations else " · ".join(violations[:4]),
                "blocking": True,
                "scope": "render",
            }
        )

        audio = self.director.audio_state(project_id)
        checks.append(
            {
                "id": "audio",
                "label": "Voce e mix audio",
                "ok": audio["valid"],
                "detail": f"{audio['state']}: {audio['detail']}",
                "blocking": False,  # l'anteprima muta è ammessa, ma dichiarata
                "scope": "render",
            }
        )

        voice_ids = {sc.voice_id for sc in scenes if not sc.is_outro and sc.spoken_text.strip()}
        checks.append(
            {
                "id": "voice",
                "label": "Voce configurata",
                "ok": bool(voice_ids),
                "detail": ", ".join(sorted(voice_ids)) if voice_ids else "nessuna voce assegnata alle scene",
                "blocking": False,
                "scope": "render",
            }
        )

        blocking_preview = [c for c in checks if c["blocking"] and c["scope"] == "preview" and not c["ok"]]
        blocking_render = [c for c in checks if c["blocking"] and not c["ok"]]
        return {
            "project_id": project_id,
            "ready": not blocking_preview,
            "render_ready": not blocking_render,
            "checks": checks,
            "scene_count": len(scenes),
            "total_frames": total_frames,
            "target_frames": target_frames,
            "fps": fps,
            "audio_state": audio["state"],
            "preview_note": (
                "anteprima visiva possibile, ma muta: l'audio non è aggiornato"
                if audio["state"] != "aggiornato"
                else "anteprima completa (video + voce)"
            ),
        }
