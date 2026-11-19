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
