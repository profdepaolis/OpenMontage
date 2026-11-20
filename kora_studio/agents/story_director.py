"""Cinematic Story Director — real shot planning over the authorised inventory.

The director reasons about the actual material: it receives the scene draft (with
its durations and spoken phrases), the real inventory that the user authorised,
and the vision notes of each asset, and returns a shot plan — purpose, framing,
camera move, transition, SFX and rationale — that the Creative Director applies.

Plan contract (v2.8):
* the plan must be *complete*: every visual scene has exactly one shot;
* every referenced asset must exist, be authorised and be a real visual;
* framings/motions/transitions must be renderable by the deterministic editor;
* the arc must breathe: no modulo sequences, no identical consecutive shots,
  real shot diversification and continuity;
* voiceover and subtitles must stay coherent with the final storyboard.

If the model plan fails validation, the director sends **one** structured repair
request listing only the problems. If a scene is still missing afterwards, the
deterministic planner fills *only those scenes* and declares it in ``source`` —
never silently rewriting scenes that were already approved.
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
AUTHORIZED_AUTHENTICITY = {
    AuthenticityStatus.AUTENTICO.value,
    AuthenticityStatus.DERIVATO_CONSERVATIVO.value,
    "originale_reale",
}


def phases_for(count: int) -> list[tuple[str, str]]:
    """Distribute the narrative arc over ``count`` scenes proportionally."""
    if count <= 0:
        return []
    if count == 1:
        return [ARC_PHASES[0]]
    out: list[tuple[str, str]] = []
    for index in range(count):
        position = index / (count - 1)
        slot = min(len(ARC_PHASES) - 1, int(round(position * (len(ARC_PHASES) - 1))))
        out.append(ARC_PHASES[slot])
    return out


class CinematicStoryDirector:
    """Plans shots with the configured engine, with a validated deterministic fallback."""

    def __init__(self, vault: Any, engine: Any = None) -> None:
        self.vault = vault
        self.engine = engine
        self.last_validation: dict[str, Any] = {}
        self.last_repair_request: Optional[dict[str, Any]] = None

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
        return rows[:60]

    def _deterministic_plan(
        self,
        scenes: list[StoryboardScene],
        assets_by_id: dict[str, dict[str, Any]],
        preset: str,
        only_scene_numbers: Optional[set[int]] = None,
    ) -> list[dict[str, Any]]:
        visual = [s for s in scenes if not s.is_outro]
        phases = phases_for(len(visual))
        plan: list[dict[str, Any]] = []
        previous_env: Optional[str] = None
        for idx, scene in enumerate(visual):
            if only_scene_numbers is not None and scene.scene_number not in only_scene_numbers:
                continue
            phase, purpose = phases[idx]
            asset = assets_by_id.get(scene.selected_asset_id or "") or {}
            multimodal = (asset.get("analysis") or {}).get("multimodal") or {}
            framing = FRAMING_BY_PHASE.get(phase, "piano_medio")
            if multimodal.get("framing") == "dettaglio_ravvicinato" and framing == "campo_ampio":
                framing = "piano_medio"
            motions = MOTION_BY_FRAMING.get(framing, MOTION_BY_FRAMING["piano_medio"])
            motion = motions[idx % len(motions)]
            environment = multimodal.get("environment") or ""
            if idx == 0:
                transition = SceneTransition.WARM_FADE.value
            elif previous_env and environment and environment != previous_env:
                transition = SceneTransition.CUT_ON_MOTION.value
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
    def _call_engine(
        self,
        scenes: list[StoryboardScene],
        assets: list[dict[str, Any]],
        preset: str,
        production_id: Optional[str],
        repair: Optional[dict[str, Any]] = None,
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
                "sfx": sorted(set(SFX_BY_CATEGORY.values()) | {"ambiente_spiaggia", "transizioni", "nessuno"}),
            },
        }
        if repair:
            payload["repair"] = repair
        system = (
            "Sei il Cinematic Story Director di KORA Beach Club (Policoro). Devi pianificare "
            "le inquadrature usando SOLO gli asset reali dell'inventario (usa i loro id). "
            "Mai sequenze cicliche: ogni scena ha una funzione narrativa distinta (hook, contesto, "
            "dettaglio, esperienza, emozione, payoff) e il ritmo deve crescere e respirare. "
            "Il piano deve coprire TUTTE le scene elencate, una sola volta ciascuna. "
            "Rispondi SOLO con JSON: {\"shots\":[{\"scene_number\":int,\"purpose\":str,\"asset_id\":str,"
            "\"framing\":str,\"camera\":str,\"transition\":str,\"sfx\":str,\"music\":str,\"rationale\":str}]}"
        )
        if repair:
            system += (
                " Questa è una richiesta di correzione: ricevi in 'repair' i problemi rilevati e le "
                "scene mancanti. Correggi esattamente quei punti e restituisci il piano completo."
            )
        messages = [
            {"role": "system", "content": system},
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

    # ------------------------------------------------------------- validation
    def validate_plan(
        self,
        scenes: list[StoryboardScene],
        plan: list[dict[str, Any]],
        assets_by_id: dict[str, dict[str, Any]],
        authorized_ids: Optional[set[str]] = None,
        fps: int = 25,
    ) -> dict[str, Any]:
        """Full contract validation of a direction plan (7 checks)."""
        visual = [s for s in scenes if not s.is_outro]
        expected = {s.scene_number for s in visual}
        problems: list[dict[str, Any]] = []
        by_number: dict[int, dict[str, Any]] = {}
        duplicates: list[int] = []
        for shot in plan:
            number = shot.get("scene_number")
            if number in by_number:
                duplicates.append(int(number))
            by_number[number] = shot

        missing = sorted(expected - set(by_number))
        extra = sorted(set(by_number) - expected)
        for number in missing:
            problems.append({"code": "scena_mancante", "scene_number": number})
        for number in duplicates:
            problems.append({"code": "scena_duplicata", "scene_number": number})
        for number in extra:
            problems.append({"code": "scena_sconosciuta", "scene_number": number})

        # 1. valid + authorised inventory
        for number in sorted(expected):
            shot = by_number.get(number)
            if not shot:
                continue
            asset_id = shot.get("asset_id")
            asset = assets_by_id.get(asset_id or "")
            if not asset:
                problems.append({"code": "asset_inesistente", "scene_number": number, "asset_id": asset_id})
                continue
            if authorized_ids is not None and asset_id not in authorized_ids:
                problems.append({"code": "asset_non_autorizzato", "scene_number": number, "asset_id": asset_id})
            if asset.get("category") == "Logo":
                problems.append({"code": "logo_fuori_outro", "scene_number": number})
            if asset.get("media_type") not in ("image", "video"):
                problems.append({"code": "asset_non_visuale", "scene_number": number})
            # 2. authenticity
            status = (asset.get("authenticity_status") or "").lower()
            if status and status not in AUTHORIZED_AUTHENTICITY and asset.get("generated"):
                problems.append({"code": "asset_non_autentico", "scene_number": number})
            # 3. renderer compatibility
            if shot.get("framing") not in VALID_FRAMINGS:
                problems.append({"code": "framing_non_valido", "scene_number": number})
            if shot.get("camera") not in VALID_MOTIONS:
                problems.append({"code": "camera_non_valida", "scene_number": number})
            if shot.get("transition") not in VALID_TRANSITIONS:
                problems.append({"code": "transizione_non_valida", "scene_number": number})
            scene = next(s for s in visual if s.scene_number == number)
            if scene.duration_frames <= 0:
                problems.append({"code": "durata_non_valida", "scene_number": number})
            # 4. voiceover coherence
            if scene.spoken_text and not scene.subtitle_text:
                problems.append({"code": "sottotitolo_mancante", "scene_number": number})
            if shot.get("framing") == "dettaglio_ravvicinato" and asset.get("media_type") == "video":
                problems.append({"code": "dettaglio_su_video", "scene_number": number})

        # 5. shot diversification (not a modulo chain)
        framings = [by_number[n].get("framing") for n in sorted(expected) if n in by_number]
        cameras = [by_number[n].get("camera") for n in sorted(expected) if n in by_number]
        distinct_framings = len(set(framings))
        needed = min(3, len(framings))
        if len(framings) >= 3 and distinct_framings < needed:
            problems.append({"code": "inquadrature_poco_diversificate", "distinct": distinct_framings})
        if len(framings) >= 4 and len(set(cameras)) < 2:
            problems.append({"code": "movimenti_poco_diversificati"})

        # 6. continuity: identical consecutive asset + framing/motion pairs
        ordered = [by_number[n] for n in sorted(expected) if n in by_number]
        repeats = 0
        for prev, cur in zip(ordered, ordered[1:]):
            if prev.get("asset_id") and prev.get("asset_id") == cur.get("asset_id"):
                repeats += 1
                problems.append(
                    {"code": "asset_consecutivo", "scene_number": cur.get("scene_number")}
                )
            if prev.get("framing") == cur.get("framing") and prev.get("camera") == cur.get("camera"):
                problems.append({"code": "inquadratura_ripetuta", "scene_number": cur.get("scene_number")})
        # with fewer assets than scenes, repeats are unavoidable: declare, don't fail
        if repeats and len(assets_by_id) <= len(visual):
            problems = [p for p in problems if p["code"] != "asset_consecutivo"]
            problems.append({"code": "riuso_asset_dichiarato", "scenes": repeats})

        # 7. frame math of the whole storyboard
        total = sum(s.duration_frames for s in scenes)
        if fps > 0 and total <= 0:
            problems.append({"code": "frame_totali_nulli"})

        blocking = [
            p for p in problems if p["code"] not in ("riuso_asset_dichiarato",)
        ]
        result = {
            "valid": not blocking,
            "complete": not missing and not duplicates and not extra,
            "missing_scenes": missing,
            "problems": problems,
            "blocking": blocking,
            "shot_count": len(plan),
            "expected_scenes": len(expected),
            "distinct_framings": distinct_framings,
        }
        self.last_validation = result
        return result

    # ------------------------------------------------------------------ public
    def plan(
        self,
        scenes: list[StoryboardScene],
        assets: list[dict[str, Any]],
        preset: str = EditingPreset.CINEMATIC_IDENTITY.value,
        production_id: Optional[str] = None,
        use_engine: bool = True,
        authorized_ids: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Produce a complete, validated plan (engine → one repair → limited fallback)."""
        by_id = {a["id"]: a for a in assets}
        auth_set = set(authorized_ids) if authorized_ids is not None else None
        engine_plan = self._call_engine(scenes, assets, preset, production_id) if use_engine else None
        validation: dict[str, Any]
        if engine_plan:
            validation = self.validate_plan(scenes, engine_plan, by_id, auth_set)
            if not validation["valid"]:
                # one structured repair request, then fall back only for the gaps
                repair = {
                    "problems": validation["blocking"][:20],
                    "missing_scenes": validation["missing_scenes"],
                    "instruction": "restituisci il piano completo corretto, una scena per numero",
                }
                self.last_repair_request = repair
                repaired = self._call_engine(scenes, assets, preset, production_id, repair=repair)
                if repaired:
                    revalidation = self.validate_plan(scenes, repaired, by_id, auth_set)
                    if revalidation["valid"]:
                        self.last_validation = revalidation
                        return repaired
                    engine_plan, validation = repaired, revalidation
        else:
            validation = self.validate_plan(scenes, [], by_id, auth_set)

        missing = set(validation.get("missing_scenes") or [])
        if engine_plan:
            # keep valid shots, drop only the ones that block the renderer
            blocked_numbers = {
                p["scene_number"]
                for p in validation["blocking"]
                if "scene_number" in p and p["code"] in ("framing_non_valido", "camera_non_valida", "transizione_non_valida")
            }
            kept = [s for s in engine_plan if s.get("scene_number") not in blocked_numbers]
            missing |= blocked_numbers
        else:
            kept = []
        if missing:
            fallback = self._deterministic_plan(scenes, by_id, preset, only_scene_numbers=missing)
            if engine_plan:
                for shot in fallback:
                    shot["source"] = f"fallback:missing_scene_{shot['scene_number']}"
            merged = kept + fallback
        else:
            merged = kept if engine_plan else self._deterministic_plan(scenes, by_id, preset)
        # one shot per scene, ordered
        deduped: dict[int, dict[str, Any]] = {}
        for shot in merged:
            deduped.setdefault(int(shot["scene_number"]), shot)
        merged = [deduped[k] for k in sorted(deduped)]
        final = self.validate_plan(scenes, merged, by_id, auth_set)
        self.last_validation = final
        return merged

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
            if shot.get("purpose") and not scene.is_outro:
                scene.narrative_goal = shot["purpose"]
        # keep the outro untouched and always fading to black
        for scene in scenes:
            if scene.is_outro:
                scene.transition = SceneTransition.FADE_BLACK.value
                scene.camera_motion = CameraMotion.STATIC_BREATHE.value
                scene.authenticity_status = AuthenticityStatus.AUTENTICO.value
        return scenes
