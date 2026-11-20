"""Cinematic Story Director — real shot planning, not a cyclic sequence.

The director reasons about the actual material: it receives the scene draft (with
its durations and spoken phrases), the real inventory and the vision notes of each
asset, and returns a shot plan — purpose, framing, camera move, transition, SFX
and rationale — that the Creative Director applies to the storyboard.

When a configured engine is available the plan comes from the model and is
validated field by field (an invented asset id or an unknown camera move is
rejected). When no engine can be used, the deterministic planner builds the same
structure from narrative proportion (not ``idx % 6``), asset features and the
scene function, so the fallback is honest and still non-repetitive.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from kora_studio.ai_engine.base import EngineError
from kora_studio.db.models import (
    AuthenticityStatus,
    CameraMotion,
    EditingPreset,
    SceneTransition,
    StoryboardScene,
)

#: narrative functions in order — the arc is distributed proportionally over the
#: real number of scenes, never cycled with a modulo
ARC_PHASES = (
    ("hook", "Apertura: aggancia in 2 secondi senza testo aggressivo"),
    ("context", "Contesto: dove siamo, luce e architettura reale"),
    ("detail", "Dettaglio tattile: materia, texture, gesto"),
    ("experience", "Esperienza: ospitalità e convivialità KORA"),
    ("emotion", "Emozione: respiro, mare, silenzio"),
    ("payoff", "Chiusura: payoff esatto 'The Art of Being'"),
)

FRAMING_BY_PHASE = {
    "hook": "campo_ampio",
    "context": "campo_ampio",
    "detail": "dettaglio_ravvicinato",
    "experience": "piano_medio",
    "emotion": "campo_ampio",
    "payoff": "piano_medio",
}

MOTION_BY_FRAMING = {
    "campo_ampio": (CameraMotion.SLOW_PUSH_IN.value, CameraMotion.SLOW_PULL_OUT.value, CameraMotion.PAN_RIGHT.value),
    "piano_medio": (CameraMotion.MICRO_PARALLAX.value, CameraMotion.PAN_LEFT.value, CameraMotion.TILT_UP.value),
    "dettaglio_ravvicinato": (
        CameraMotion.MICRO_PARALLAX.value,
        CameraMotion.STATIC_BREATHE.value,
        CameraMotion.SLOW_PUSH_IN.value,
    ),
}

SFX_BY_CATEGORY = {
    "mare": "onde",
    "spiaggia": "onde",
    "colazione": "tazzine",
    "aperitivo": "bicchieri",
    "pergolato": "vento",
    "arredi": "vento",
    "ombrelloni": "vento",
    "cucina": "bicchieri",
    "ospitalità": "ambiente_spiaggia",
}

VALID_FRAMINGS = {"campo_ampio", "piano_medio", "dettaglio_ravvicinato", "outro_logo"}
VALID_MOTIONS = {m.value for m in CameraMotion}
VALID_TRANSITIONS = {t.value for t in SceneTransition}


def phases_for(count: int) -> list[tuple[str, str]]:
    """Distribute the narrative arc over ``count`` scenes proportionally."""
    if count <= 0:
        return []
    if count == 1:
        return [ARC_PHASES[0]]
    out: list[tuple[str, str]] = []
    for index in range(count):
        # map scene index onto the arc: first scene = hook, last = payoff
        position = index / (count - 1)
        slot = min(len(ARC_PHASES) - 1, int(round(position * (len(ARC_PHASES) - 1))))
        out.append(ARC_PHASES[slot])
    return out


class CinematicStoryDirector:
    """Plans shots with the configured engine, with a deterministic fallback."""

    def __init__(self, vault: Any, engine: Any = None) -> None:
        self.vault = vault
        self.engine = engine

    def attach_engine_manager(self, engine: Any) -> None:
        self.engine = engine

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _inventory(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for asset in assets:
            multimodal = (asset.get("analysis") or {}).get("multimodal") or {}
            rows.append(
                {
                    "id": asset["id"],
                    "category": asset.get("category"),
                    "media_type": asset.get("media_type"),
                    "lighting": multimodal.get("lighting"),
                    "framing": multimodal.get("framing"),
                    "environment": multimodal.get("environment"),
                    "materials": (multimodal.get("materials") or [])[:4],
                    "vision_notes": (multimodal.get("vision_review") or {}).get("description"),
                }
            )
        return rows[:30]

    def _deterministic_plan(
        self,
        scenes: list[StoryboardScene],
        assets_by_id: dict[str, dict[str, Any]],
        preset: str,
    ) -> list[dict[str, Any]]:
        visual = [s for s in scenes if not s.is_outro]
        phases = phases_for(len(visual))
        plan: list[dict[str, Any]] = []
        previous_env: Optional[str] = None
        for idx, scene in enumerate(visual):
            phase, purpose = phases[idx]
            asset = assets_by_id.get(scene.selected_asset_id or "") or {}
            multimodal = (asset.get("analysis") or {}).get("multimodal") or {}
            framing = FRAMING_BY_PHASE.get(phase, "piano_medio")
            # a real asset that is already a close-up stays a close-up
            if multimodal.get("framing") == "dettaglio_ravvicinato" and framing == "campo_ampio":
                framing = "piano_medio"
            motions = MOTION_BY_FRAMING.get(framing, MOTION_BY_FRAMING["piano_medio"])
            motion = motions[idx % len(motions)]
            environment = multimodal.get("environment") or ""
            if idx == 0:
                transition = SceneTransition.WARM_FADE.value
            elif previous_env and environment and environment != previous_env:
                transition = SceneTransition.CUT_ON_MOTION.value
            elif phase == "emotion":
                transition = SceneTransition.CROSS_DISSOLVE.value
            else:
                transition = SceneTransition.CROSS_DISSOLVE.value
            previous_env = environment or previous_env
            category = (asset.get("category") or "").lower()
            sfx = next((v for k, v in SFX_BY_CATEGORY.items() if k in category), "ambiente_spiaggia")
            plan.append(
                {
                    "scene_number": scene.scene_number,
                    "purpose": purpose,
                    "phase": phase,
                    "asset_id": scene.selected_asset_id,
                    "framing": framing,
                    "camera": motion,
                    "transition": transition,
                    "sfx": sfx,
                    "music": "musica_cinematic" if "cinematic" in preset.lower() else "musica_ambient",
                    "rationale": (
                        f"{phase}: {asset.get('filename', 'materiale reale')} "
                        f"({multimodal.get('lighting', 'luce naturale')}) sostiene «{purpose.lower()}»."
                    ),
                    "source": "deterministic",
                }
            )
        return plan

    # ------------------------------------------------------------------ engine
    def _engine_plan(
        self,
        scenes: list[StoryboardScene],
        assets: list[dict[str, Any]],
        preset: str,
        production_id: Optional[str],
    ) -> Optional[list[dict[str, Any]]]:
        if self.engine is None:
            return None
        visual = [s for s in scenes if not s.is_outro]
        if not visual:
            return None
        payload = {
            "preset": preset,
            "scene_count": len(visual),
            "scenes": [
                {
                    "scene_number": s.scene_number,
                    "frames": s.duration_frames,
                    "seconds": s.duration_sec,
                    "spoken": s.spoken_text,
                    "current_asset": s.selected_asset_id,
                }
                for s in visual
            ],
            "inventory": self._inventory(assets),
            "allowed": {
                "framing": sorted(VALID_FRAMINGS - {"outro_logo"}),
                "camera": sorted(VALID_MOTIONS),
                "transition": sorted(VALID_TRANSITIONS),
                "sfx": sorted(set(SFX_BY_CATEGORY.values())),
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "Sei il Cinematic Story Director di KORA Beach Club (Policoro). Devi pianificare "
                    "le inquadrature usando SOLO gli asset reali dell'inventario (usa i loro id). "
                    "Mai sequenze cicliche: ogni scena ha una funzione narrativa distinta (hook, contesto, "
                    "dettaglio, esperienza, emozione, payoff) e il ritmo deve crescere e respirare. "
                    "Rispondi SOLO con JSON: {\"shots\":[{\"scene_number\":int,\"purpose\":str,\"asset_id\":str,"
                    "\"framing\":str,\"camera\":str,\"transition\":str,\"sfx\":str,\"music\":str,\"rationale\":str}]}"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            result = self.engine.call(
                "cinematic_story_director",
                messages,
                json_mode=True,
                timeout=90,
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
        shots = data.get("shots") if isinstance(data, dict) else None
        if not isinstance(shots, list) or not shots:
            return None
        valid_ids = {a["id"] for a in assets}
        allowed_sfx = set(SFX_BY_CATEGORY.values()) | {"ambiente_spiaggia", "transizioni", "nessuno"}
        by_number = {s.scene_number: s for s in visual}
        plan: list[dict[str, Any]] = []
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            number = shot.get("scene_number")
            scene = by_number.get(number) if isinstance(number, int) else None
            if scene is None:
                continue
            asset_id = shot.get("asset_id") if shot.get("asset_id") in valid_ids else scene.selected_asset_id
            framing = shot.get("framing") if shot.get("framing") in VALID_FRAMINGS else "piano_medio"
            camera = shot.get("camera") if shot.get("camera") in VALID_MOTIONS else CameraMotion.SLOW_PUSH_IN.value
            transition = (
                shot.get("transition")
                if shot.get("transition") in VALID_TRANSITIONS
                else SceneTransition.CROSS_DISSOLVE.value
            )
            plan.append(
                {
                    "scene_number": scene.scene_number,
                    "purpose": str(shot.get("purpose") or "")[:160],
                    "phase": "engine",
                    "asset_id": asset_id,
                    "framing": framing,
                    "camera": camera,
                    "transition": transition,
                    "sfx": (
                        str(shot.get("sfx"))[:40] if shot.get("sfx") in allowed_sfx else "ambiente_spiaggia"
                    ),
                    "music": str(shot.get("music") or "")[:40] or None,
                    "rationale": str(shot.get("rationale") or "")[:300],
                    "source": f"engine:{result.provider}/{result.model}",
                }
            )
        return plan or None

    # ------------------------------------------------------------------ public
    def plan(
        self,
        scenes: list[StoryboardScene],
        assets: list[dict[str, Any]],
        preset: str = EditingPreset.CINEMATIC_IDENTITY.value,
        production_id: Optional[str] = None,
        use_engine: bool = True,
    ) -> list[dict[str, Any]]:
        by_id = {a["id"]: a for a in assets}
        engine_plan = self._engine_plan(scenes, assets, preset, production_id) if use_engine else None
        if engine_plan:
            return engine_plan
        return self._deterministic_plan(scenes, by_id, preset)

    def apply_plan(
        self,
        scenes: list[StoryboardScene],
        plan: list[dict[str, Any]],
        assets_by_id: dict[str, dict[str, Any]],
    ) -> list[StoryboardScene]:
        """Apply a validated plan to the storyboard (frames are never touched here)."""
        by_number = {p["scene_number"]: p for p in plan}
        for scene in scenes:
            shot = by_number.get(scene.scene_number)
            if not shot:
                continue
            asset_id = shot.get("asset_id")
            if asset_id and asset_id in assets_by_id:
                scene.selected_asset_id = asset_id
                asset = assets_by_id[asset_id]
                if asset.get("media_type") == "video":
                    scene.asset_mode = "video_clip"
                elif scene.shot_framing == "outro_logo":
                    scene.asset_mode = scene.asset_mode
                else:
                    scene.asset_mode = "animated_photo"
                scene.authenticity_status = asset.get("authenticity_status") or scene.authenticity_status
            scene.shot_framing = shot.get("framing") or scene.shot_framing
            scene.camera_motion = shot.get("camera") or scene.camera_motion
            scene.transition = (
                SceneTransition.FADE_BLACK.value
                if scene.is_outro
                else (shot.get("transition") or scene.transition)
            )
            if shot.get("sfx"):
                scene.sfx_category = shot["sfx"]
            if shot.get("music"):
                scene.music_cue = shot["music"]
            if shot.get("rationale"):
                scene.visual_rationale = shot["rationale"]
        # keep the outro untouched and always fading to black
        for scene in scenes:
            if scene.is_outro:
                scene.transition = SceneTransition.FADE_BLACK.value
                scene.camera_motion = CameraMotion.STATIC_BREATHE.value
                scene.authenticity_status = AuthenticityStatus.AUTENTICO.value
        return scenes
