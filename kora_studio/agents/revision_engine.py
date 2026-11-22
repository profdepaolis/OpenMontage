"""Natural Language Revision Engine — targeted scene diffs, selective re-render, and snapshots."""

from __future__ import annotations

import copy
import re
from typing import Any

from kora_studio.db.models import CameraMotion, RevisionProposal, StoryboardScene
from kora_studio.vault.media_vault import MediaVault
from kora_studio.vault.sound_library import SoundLibrary

ORDINALS = {
    "prima": 1, "1": 1, "1a": 1, "first": 1,
    "seconda": 2, "2": 2, "2a": 2, "second": 2,
    "terza": 3, "3": 3, "3a": 3, "third": 3,
    "quarta": 4, "4": 4, "4a": 4, "fourth": 4,
    "quinta": 5, "5": 5, "5a": 5, "fifth": 5,
}


class RevisionEngine:
    """Parses Italian/English natural-language feedback into minimal, reversible scene diffs."""

    def __init__(self, vault: MediaVault, sound_library: SoundLibrary) -> None:
        self.vault = vault
        self.sound_library = sound_library

    def _detect_scene_numbers(self, text: str, scenes: list[StoryboardScene]) -> list[int]:
        lower = text.lower()
        found: list[int] = []
        if any(k in lower for k in ("scena finale", "finale", "outro", "ultima scena", "last scene")):
            found.append(scenes[-1].scene_number)
        for token, num in ORDINALS.items():
            if re.search(rf"\b{re.escape(token)}\b", lower):
                if any(s.scene_number == num for s in scenes):
                    found.append(num)
        m = re.search(r"scena\s*(\d+)", lower)
        if m:
            num = int(m.group(1))
            if any(s.scene_number == num for s in scenes):
                found.append(num)
        return sorted(set(found))

    def preview_revision(
        self,
        instruction: str,
        scenes: list[StoryboardScene],
        current_audio_plan: dict[str, Any],
    ) -> RevisionProposal:
        lower = (instruction or "").lower().strip()
        targets = self._detect_scene_numbers(lower, scenes)
        global_changes: list[str] = []
        what_changes: list[str] = []

        if "cambia soltanto la musica" in lower or ("musica" in lower and not targets):
            global_changes.append("music")
            what_changes.append(
                "Sostituzione della sola traccia musicale mantenendo intatti storyboard, clip video e voce."
            )
            preserved = [s.scene_number for s in scenes]
            return RevisionProposal(
                instruction=instruction,
                target_scenes=[],
                global_changes=global_changes,
                what_will_change=what_changes,
                preserved_scenes=preserved,
                selective_render_possible=True,
            )

        if "meno parole" in lower or "più breve" in lower or "riduci il testo" in lower:
            non_outro = [s.scene_number for s in scenes if not s.is_outro]
            targets = targets or non_outro
            global_changes.append("shorten_copy")
            what_changes.append(
                f"Semplificazione ed essenzializzazione del testo parlato/sottotitoli sulle scene {targets}."
            )

        if "mare" in lower and ("prima" in lower or "subito" in lower or "inizio" in lower):
            targets = sorted(set(targets + [1]))
            global_changes.append("move_sea_earlier")
            what_changes.append(
                "Anticipo dell'inquadratura sul mare nella prima parte del Reel senza alterare la durata totale."
            )

        if any(k in lower for k in ("emozionante", "intensa", "intima", "calda", "lenta")):
            if not targets:
                targets = [min(3, len(scenes))]
            what_changes.append(
                f"Scena {targets}: movimento camera più avvolgente (slow push-in) e resa emotiva mirata."
            )

        if any(k in lower for k in ("non mi piace", "cambia foto", "cambia fotografia", "altra foto", "sostituisci")):
            if not targets:
                targets = [1]
            what_changes.append(
                f"Scena {targets}: sostituzione della fotografia/clip con un'alternativa autentica dal Media Vault."
            )

        if not what_changes:
            if not targets:
                targets = [scenes[-1].scene_number if "finale" in lower else 1]
            what_changes.append(
                f"Aggiornamento mirato scena {targets} secondo indicazione: '{instruction}'."
            )

        preserved = [s.scene_number for s in scenes if s.scene_number not in targets]
        return RevisionProposal(
            instruction=instruction,
            target_scenes=targets,
            global_changes=global_changes,
            what_will_change=what_changes,
            preserved_scenes=preserved,
            selective_render_possible=True,
        )

    def apply_revision(
        self,
        instruction: str,
        scenes: list[StoryboardScene],
        audio_plan: dict[str, Any],
    ) -> tuple[list[StoryboardScene], dict[str, Any], RevisionProposal]:
        proposal = self.preview_revision(instruction, scenes, audio_plan)
        updated_scenes = [copy.deepcopy(s) for s in scenes]
        updated_audio = copy.deepcopy(audio_plan)
        lower = instruction.lower()

        # 1. Music-only change
        if "music" in proposal.global_changes:
            tracks = [
                s
                for s in self.sound_library.list_sounds()
                if s["category"] in ("musica_cinematic", "musica_ambient")
            ]
            cur_id = (updated_audio.get("music") or {}).get("id")
            if tracks:
                next_track = next((t for t in tracks if t["id"] != cur_id), tracks[0])
                updated_audio["music"] = next_track
            return updated_scenes, updated_audio, proposal

        # 2. Move sea earlier (swap scene 1 asset with sea asset while preserving exact frame timing)
        if "move_sea_earlier" in proposal.global_changes:
            sea_idx = next(
                (
                    i
                    for i, s in enumerate(updated_scenes)
                    if not s.is_outro and (s.sfx_category == "onde" or "mare" in s.narrative_goal.lower())
                ),
                None,
            )
            if sea_idx is not None and sea_idx > 0:
                s0, s_sea = updated_scenes[0], updated_scenes[sea_idx]
                s0.selected_asset_id, s_sea.selected_asset_id = s_sea.selected_asset_id, s0.selected_asset_id
                s0.asset_mode, s_sea.asset_mode = s_sea.asset_mode, s0.asset_mode
                s0.sfx_category, s_sea.sfx_category = s_sea.sfx_category, s0.sfx_category
                s0.narrative_goal = "Apertura sul mare"
            else:
                sea_matches = self.vault.semantic_search("mare policoro orizzonte", limit=2)
                if sea_matches and updated_scenes:
                    updated_scenes[0].selected_asset_id = sea_matches[0]["id"]
                    updated_scenes[0].asset_mode = (
                        "video_clip" if sea_matches[0]["media_type"] == "video" else "animated_photo"
                    )
                    updated_scenes[0].sfx_category = "onde"
                    updated_scenes[0].narrative_goal = "Apertura sul mare"

        # 3. Shorten copy ("Usa meno parole")
        if "shorten_copy" in proposal.global_changes:
            for sc in updated_scenes:
                if sc.scene_number in proposal.target_scenes and not sc.is_outro:
                    words = sc.subtitle_text.split()
                    if len(words) > 5:
                        short = " ".join(words[:5]).rstrip(",;") + "."
                        sc.subtitle_text = short
                        if sc.spoken_text:
                            sc.spoken_text = short

        # 4. Per-scene targeted edits
        for sc in updated_scenes:
            if sc.scene_number not in proposal.target_scenes:
                continue
            if any(k in lower for k in ("emozionante", "intensa", "intima", "calda")):
                if not sc.is_outro:
                    sc.camera_motion = CameraMotion.SLOW_PUSH_IN.value
                    sc.narrative_goal = f"{sc.narrative_goal} (enfasi emotiva)"
                else:
                    sc.subtitle_text = "The Art of Being · Policoro"
            if any(k in lower for k in ("non mi piace", "cambia foto", "cambia fotografia", "altra foto", "sostituisci")):
                if not sc.is_outro:
                    candidates = [
                        a
                        for a in self.vault.list_assets()
                        if a["media_type"] in ("image", "video")
                        and a["category"] != "Logo"
                        and a["id"] != sc.selected_asset_id
                    ]
                    if sc.alternative_asset_id and sc.alternative_asset_id != sc.selected_asset_id:
                        sc.selected_asset_id, sc.alternative_asset_id = (
                            sc.alternative_asset_id,
                            sc.selected_asset_id,
                        )
                    elif candidates:
                        sc.alternative_asset_id = sc.selected_asset_id
                        sc.selected_asset_id = candidates[0]["id"]
                        sc.asset_mode = (
                            "video_clip" if candidates[0]["media_type"] == "video" else "animated_photo"
                        )

        return updated_scenes, updated_audio, proposal


# ---------------------------------------------------------------------------
# v2.8 — conversational revision of a SINGLE scene (approved scenes untouched)
# ---------------------------------------------------------------------------

SCENE_EDITABLE_FIELDS = (
    "framing",
    "camera",
    "motion_intensity",
    "subtitle_text",
    "spoken_text",
    "sfx",
    "transition",
    "zoom_hint",
    "focus_hint",
)

_FRAMING_WORDS = {
    "dettaglio": "dettaglio_ravvicinato",
    "close": "dettaglio_ravvicinato",
    "vicino": "dettaglio_ravvicinato",
    "ampio": "campo_ampio",
    "wide": "campo_ampio",
    "panoramica": "campo_ampio",
    "medio": "piano_medio",
}


class SingleSceneReviser:
    """Turns a conversational note into a validated change set for one scene."""

    def __init__(self, engine: Any = None) -> None:
        self.engine = engine

    def attach_engine_manager(self, engine: Any) -> None:
        self.engine = engine

    def _deterministic(self, instruction: str, scene: StoryboardScene) -> dict[str, Any]:
        lower = (instruction or "").lower()
        changes: dict[str, Any] = {}
        for word, framing in _FRAMING_WORDS.items():
            if word in lower:
                changes["framing"] = framing
                break
        if any(k in lower for k in ("più lento", "lento", "calmo", "lenta")):
            changes["motion_intensity"] = 0.3
        if any(k in lower for k in ("più veloce", "dinamico", "energico", "veloce")):
            changes["motion_intensity"] = 0.9
        if any(k in lower for k in ("fermo", "statico", "immobile")):
            changes["camera"] = CameraMotion.STATIC_BREATHE.value
        if "indietro" in lower or "allontana" in lower:
            changes["zoom_hint"] = "out"
        if "avanti" in lower or "avvicina" in lower:
            changes["zoom_hint"] = "in"
        quoted = re.findall(r"[\"«“](.+?)[\"»”]", instruction or "")
        if quoted:
            changes["subtitle_text"] = quoted[0].strip()
            changes["spoken_text"] = quoted[0].strip()
        if ("onde" in lower or "mare" in lower) and any(k in lower for k in ("suono", "sound", "sfx", "audio")):
            changes["sfx"] = "onde"
        if "dissolvenza" in lower:
            changes["transition"] = "cross_dissolve"
        return changes

    def _engine_changes(self, instruction: str, scene: StoryboardScene) -> Optional[dict[str, Any]]:
        if self.engine is None:
            return None
        import json as _json

        from kora_studio.ai_engine.base import EngineError

        payload = {
            "instruction": instruction,
            "scene": {
                "scene_number": scene.scene_number,
                "framing": scene.shot_framing,
                "camera": scene.camera_motion,
                "subtitle": scene.subtitle_text,
                "sfx": scene.sfx_category,
                "transition": scene.transition,
                "duration_frames": scene.duration_frames,
            },
            "allowed_fields": list(SCENE_EDITABLE_FIELDS),
            "allowed_framing": ["campo_ampio", "piano_medio", "dettaglio_ravvicinato"],
            "allowed_camera": [m.value for m in CameraMotion],
            "allowed_transition": ["cross_dissolve", "warm_fade", "cut_on_motion", "fade_black"],
        }
        try:
            result = self.engine.call(
                "cinematic_story_director",
                [
                    {
                        "role": "system",
                        "content": (
                            "Sei il regista di KORA. Devi modificare UNA sola scena secondo l'istruzione, "
                            "senza toccare le altre. Rispondi solo JSON: {\"changes\":{...}} usando esclusivamente "
                            "i campi consentiti."
                        ),
                    },
                    {"role": "user", "content": _json.dumps(payload, ensure_ascii=False)},
                ],
                json_mode=True,
                timeout=60,
            )
        except EngineError:
            return None
        try:
            raw = result.text
            start, end = raw.find("{"), raw.rfind("}")
            data = _json.loads(raw[start : end + 1]) if start >= 0 and end > start else {}
        except ValueError:
            return None
        changes = data.get("changes") if isinstance(data, dict) else None
        return changes if isinstance(changes, dict) else None

    def propose(self, instruction: str, scene: StoryboardScene) -> dict[str, Any]:
        engine_changes = self._engine_changes(instruction, scene)
        source = "engine"
        changes = engine_changes or self._deterministic(instruction, scene)
        if engine_changes is None:
            source = "deterministic"
        # validate: only allowed fields, only renderable values
        clean: dict[str, Any] = {}
        for key, value in (changes or {}).items():
            if key not in SCENE_EDITABLE_FIELDS:
                continue
            if key == "framing" and value not in ("campo_ampio", "piano_medio", "dettaglio_ravvicinato"):
                continue
            if key == "camera" and value not in {m.value for m in CameraMotion}:
                continue
            if key == "transition" and value not in ("cross_dissolve", "warm_fade", "cut_on_motion", "fade_black"):
                continue
            if key == "motion_intensity":
                try:
                    value = max(0.05, min(1.0, float(value)))
                except (TypeError, ValueError):
                    continue
            if key in ("subtitle_text", "spoken_text"):
                value = str(value)[:180]
            if key == "zoom_hint" and value not in ("in", "out"):
                continue
            if key == "focus_hint" and not isinstance(value, dict):
                continue
            clean[key] = value
        return {
            "instruction": instruction,
            "scene_number": scene.scene_number,
            "changes": clean,
            "source": source,
            "applies_to": [scene.scene_number],
            "preserved_scenes": [],
            "note": (
                "Modifica conversazionale limitata a questa scena: nessun'altra scena viene rigenerata."
                if clean
                else "Nessuna modifica riconosciuta: riformula indicando inquadratura, movimento o testo."
            ),
        }
