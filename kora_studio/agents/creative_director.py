"""Creative Director agent — Sorprendimi, Free Theme ideation, Anti-Repetition, and 25fps Storyboard."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from kora_studio.agents.brand_guardian import BrandGuardian
from kora_studio.brand.brand_brain import BrandBrain
from kora_studio.config import DEFAULT_FPS
from kora_studio.db.database import Database, utcnow_iso
from kora_studio.db.models import (
    CameraMotion,
    CreativeConcept,
    EditingPreset,
    SceneTransition,
    StoryboardScene,
)
from kora_studio.vault.media_vault import MediaVault

# Curated diverse Mediterranean storytelling angles so concepts never repeat mechanically
STORY_ANGLES = [
    {
        "angle": "luce_del_mattino",
        "emotion": "Quietudine luminosa",
        "preset": EditingPreset.CINEMATIC_IDENTITY.value,
        "visual_mood": "Avorio mattutino, ombre lunghe sotto il pergolato, orizzonte limpido.",
        "queries": ["mattino luce", "colazione tazzina", "pergolato ombre", "mare orizzonte"],
        "hook_tpl": "C'è un'ora, davanti al mare, in cui tutto rallenta.",
        "arc_tpl": [
            "La prima luce accarezza il pergolato e la sabbia.",
            "Un caffè preparato con calma, senza guardare l'orologio.",
            "Il tempo ritrova il suo respiro naturale.",
        ],
        "ending": "KORA. The Art of Being.",
    },
    {
        "angle": "convivialita_condivisa",
        "emotion": "Calore autentico",
        "preset": EditingPreset.EXPERIENCE.value,
        "visual_mood": "Legno caldo, gesti spontanei, sorrisi discreti al tavolo vista mare.",
        "queries": ["tavolo persone aperitivo", "colazione persone", "dettaglio bicchiere", "mare policoro"],
        "hook_tpl": "I luoghi restano per come ti fanno stare insieme.",
        "arc_tpl": [
            "Un tavolo già pronto, dove la conversazione nasce naturale.",
            "Un calice al tramonto, un gesto attento che non ha bisogno di parole.",
            "Condividere la semplicità diventa il vero lusso.",
        ],
        "ending": "KORA. Il piacere di esserci.",
    },
    {
        "angle": "poetica_dei_dettagli",
        "emotion": "Contemplazione essenziale",
        "preset": EditingPreset.EDITORIAL.value,
        "visual_mood": "Texture tattili: intrecci di paglia, legno, vetro e luce dorata.",
        "queries": ["ombre morbide", "dettaglio bicchiere", "aperitivo tramonto parasole", "mare policoro"],
        "hook_tpl": "Poche cose, scelte con misura.",
        "arc_tpl": [
            "La luce che attraversa l'intreccio e disegna il legno.",
            "L'attesa del tramonto sotto il parasole, davanti all'orizzonte.",
            "L'eleganza discreta di chi sa fermarsi.",
        ],
        "ending": "KORA · Policoro.",
    },
    {
        "angle": "stagione_fuori_tempo",
        "emotion": "Libertà serena",
        "preset": EditingPreset.SEASONAL.value,
        "visual_mood": "Toni neutri e blu profondo del mare d'autunno e inverno.",
        "queries": ["mare policoro", "pomeriggio pergolato", "tavolo seduta", "aperitivo tramonto"],
        "hook_tpl": "Quando il litorale si fa silenzioso, il mare parla più chiaro.",
        "arc_tpl": [
            "La spiaggia respira fuori dalla fretta estiva.",
            "Un rifugio caldo tra legno naturale e luce bassa.",
            "Un modo autentico di vivere il mare tutto l'anno.",
        ],
        "ending": "KORA. The Art of Being.",
    },
]


def _word_set(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zàèéìòù0-9]+", (text or "").lower()) if len(w) > 2}


class AntiRepetitionEngine:
    """Tracks past Reels and scores/filters new concepts to prevent repetition."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def get_history(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM reel_history ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "project_id": r["project_id"],
                    "title": r["title"],
                    "theme": r["theme"],
                    "hook": r["hook"],
                    "ending": r["ending"],
                    "phrases": json.loads(r["phrases_json"]),
                    "asset_ids": json.loads(r["asset_ids_json"]),
                    "music_id": r["music_id"],
                    "preset": r["preset"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    def record_reel(
        self,
        project_id: str,
        title: str,
        theme: str,
        hook: str,
        ending: str,
        phrases: list[str],
        asset_ids: list[str],
        music_id: Optional[str],
        preset: str,
    ) -> None:
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO reel_history
                (id, project_id, title, theme, hook, ending, phrases_json, asset_ids_json, music_id, preset, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"hist_{uuid.uuid4().hex[:10]}",
                    project_id,
                    title,
                    theme,
                    hook,
                    ending,
                    json.dumps(phrases, ensure_ascii=False),
                    json.dumps(asset_ids),
                    music_id,
                    preset,
                    utcnow_iso(),
                ),
            )

    def evaluate_originality(self, title: str, hook: str, phrases: list[str]) -> tuple[float, list[str]]:
        history = self.get_history()
        if not history:
            return 1.0, []

        cand_words = _word_set(f"{title} {hook} {' '.join(phrases)}")
        max_sim = 0.0
        notes: list[str] = []
        for h in history:
            if h["title"].strip().lower() == title.strip().lower():
                notes.append(f"Titolo già utilizzato nel progetto '{h['title']}'.")
                max_sim = max(max_sim, 0.9)
            if h["hook"].strip().lower() == hook.strip().lower():
                notes.append("Hook iniziale identico a un Reel precedente: variare l'apertura.")
                max_sim = max(max_sim, 0.8)
            hist_words = _word_set(f"{h['title']} {h['hook']} {' '.join(h['phrases'])}")
            if cand_words and hist_words:
                sim = len(cand_words & hist_words) / len(cand_words | hist_words)
                max_sim = max(max_sim, sim)

        originality = round(max(0.1, 1.0 - max_sim * 0.85), 2)
        if originality >= 0.75 and not notes:
            notes.append("Approccio narrativo distinto rispetto ai Reel in archivio.")
        return originality, notes


class CreativeDirector:
    """Generates 3 distinct KORA-aligned concepts and exact 25fps storyboards."""

    def __init__(
        self,
        db: Database,
        brand_brain: BrandBrain,
        vault: MediaVault,
    ) -> None:
        self.db = db
        self.brand_brain = brand_brain
        self.guardian = BrandGuardian(brand_brain)
        self.vault = vault
        self.anti_rep = AntiRepetitionEngine(db)

    def _season_context(self) -> str:
        month = datetime.now(timezone.utc).month
        if month in (11, 12, 1, 2):
            return "Autunno/Inverno sul mare — luce radente, quiete, accoglienza intima"
        if month in (3, 4, 5):
            return "Primavera mediterranea — aria limpida, prime colazioni all'aperto"
        if month in (6, 7, 8):
            return "Estate al KORA — luce dorata, mare vivo, tramonti sotto il parasole"
        return "Fine estate sul litorale — ritmo disteso, aperitivi al tramonto"

    def _extract_theme_keywords(self, theme: str) -> dict[str, Any]:
        lower = (theme or "").lower()
        minimal_words = any(
            k in lower for k in ("poche parole", "essenziale", "breve", "silenzio", "minimal")
        )
        emotional = any(
            k in lower for k in ("emozionante", "emozione", "sentire", "cuore", "intimo")
        )
        sunset = any(k in lower for k in ("tramonto", "aperitivo", "sera", "calice", "spritz"))
        morning = any(k in lower for k in ("mattino", "colazione", "caffè", "alba"))
        together = any(k in lower for k in ("insieme", "condividere", "ospiti", "amici", "tavolo"))
        sea = any(k in lower for k in ("mare", "onde", "spiaggia", "orizzonte", "policoro"))
        return {
            "minimal_words": minimal_words,
            "emotional": emotional,
            "sunset": sunset,
            "morning": morning,
            "together": together,
            "sea": sea,
        }

    def _build_concept_for_theme(
        self,
        theme: str,
        angle_idx: int,
        duration_sec: float = 20.0,
        preferred_asset_ids: Optional[list[str]] = None,
    ) -> CreativeConcept:
        cues = self._extract_theme_keywords(theme)
        angle = STORY_ANGLES[angle_idx % len(STORY_ANGLES)]
        profile = self.brand_brain.get_current()

        # Select matching authentic assets from Vault (never invent missing media)
        candidate_assets: list[dict[str, Any]] = []
        if preferred_asset_ids:
            for aid in preferred_asset_ids:
                a = self.vault.get_asset(aid)
                if a and a["media_type"] in ("image", "video") and a["category"] != "Logo":
                    candidate_assets.append(a)

        search_queries = list(angle["queries"])
        if theme.strip():
            search_queries.insert(0, theme.strip())

        seen_ids = {a["id"] for a in candidate_assets}
        for q in search_queries:
            for a in self.vault.semantic_search(q, limit=4):
                if a["id"] not in seen_ids and a["media_type"] in ("image", "video"):
                    candidate_assets.append(a)
                    seen_ids.add(a["id"])

        target_slots = max(
            len(preferred_asset_ids or []),
            min(14, max(5, int(round(duration_sec / 2.8)))),
        )
        # Fallback: include any authentic images/videos in vault so we fill target_slots
        if len(candidate_assets) < target_slots:
            for a in self.vault.list_assets():
                if (
                    a["id"] not in seen_ids
                    and a["media_type"] in ("image", "video")
                    and a["category"] != "Logo"
                ):
                    candidate_assets.append(a)
                    seen_ids.add(a["id"])

        available_ids = [a["id"] for a in candidate_assets[:target_slots]]
        missing: list[str] = []
        if len(available_ids) == 0:
            missing.append("Almeno una fotografia o clip autentica del KORA")

        logo_asset = self.vault.get_logo_asset()
        if not logo_asset:
            missing.append("Logo originale KORA (LOGO KORA.PNG)")

        # Compose evocative, restrained KORA narration
        if cues["minimal_words"]:
            if angle_idx == 0:
                title = "Dove il mare respira"
                hook = "Il mare davanti."
                phrases = [
                    "Il mare davanti.",
                    "Un tavolo preparato con calma.",
                    "Stare insieme, senza fretta.",
                    "KORA. The Art of Being.",
                ]
            elif angle_idx == 1:
                title = "Luce e silenzio"
                hook = "Solo l'essenziale."
                phrases = [
                    "Solo l'essenziale.",
                    "Luce morbida sul legno.",
                    "Il piacere di ritrovarsi.",
                    "KORA. The Art of Being.",
                ]
            else:
                title = "Al proprio ritmo"
                hook = "Fermarsi un istante."
                phrases = [
                    "Fermarsi un istante.",
                    "Sentire l'aria del mare.",
                    "Nulla da aggiungere.",
                    "KORA. The Art of Being.",
                ]
            preset = EditingPreset.EDITORIAL.value
        elif cues["together"] and cues["sea"]:
            if angle_idx == 0:
                title = "Davanti allo stesso orizzonte"
                hook = "Il piacere di stare insieme comincia quando nessuno guarda l'ora."
                phrases = [
                    "Il piacere di stare insieme comincia quando nessuno guarda l'ora.",
                    "Una tavola accogliente, la brezza leggera sotto il pergolato.",
                    "E il mare davanti, che rende semplice ogni momento.",
                    "KORA. The Art of Being.",
                ]
                preset = EditingPreset.CINEMATIC_IDENTITY.value
            elif angle_idx == 1:
                title = "Il tempo condiviso"
                hook = "C'è un tavolo che ti aspetta a pochi passi dall'acqua."
                phrases = [
                    "C'è un tavolo che ti aspetta a pochi passi dall'acqua.",
                    "Un gesto attento, un calice al tramonto, una conversazione che resta.",
                    "Vivere il mare insieme, con naturalezza.",
                    "KORA. The Art of Being.",
                ]
                preset = EditingPreset.EXPERIENCE.value
            else:
                title = "Senza fretta sul mare"
                hook = "Non serve molto per sentirsi al proprio posto."
                phrases = [
                    "Non serve molto per sentirsi al proprio posto.",
                    "Luce calda, profumo di caffè, voci vicine.",
                    "Tutto il resto può aspettare.",
                    "KORA. The Art of Being.",
                ]
                preset = EditingPreset.EDITORIAL.value
        else:
            clean_theme = theme.strip().rstrip(".") or "L'arte di vivere il KORA"
            if angle_idx == 0:
                title = f"{clean_theme[:34]} — Luce del Mattino"
                hook = angle["hook_tpl"]
                phrases = [hook, angle["arc_tpl"][0], angle["arc_tpl"][1], "KORA. The Art of Being."]
                preset = angle["preset"]
            elif angle_idx == 1:
                title = f"{clean_theme[:34]} — Incontro al Tramonto"
                hook = "Ogni dettaglio al KORA racconta un'ospitalità misurata."
                phrases = [
                    hook,
                    f"{clean_theme}, davanti alla luce dorata del litorale.",
                    "Un momento semplice, curato con attenzione.",
                    "KORA. The Art of Being.",
                ]
                preset = EditingPreset.EXPERIENCE.value
            else:
                title = f"{clean_theme[:34]} — Essenza Mediterranea"
                hook = "Ritrovare il proprio tempo, tra sabbia e pergolato."
                phrases = [
                    hook,
                    "Materiali naturali, gesti reali e la voce calma del mare.",
                    "L'eleganza discreta del presente.",
                    "KORA. The Art of Being.",
                ]
                preset = EditingPreset.EDITORIAL.value

        vo_script = " ".join(phrases)
        guard = self.guardian.validate_copy(vo_script)
        if not guard["approved"]:
            # Fallback to safe brand-approved lines
            vo_script = "C'è una luce calma sul mare di Policoro. Tempo autentico da condividere. KORA. The Art of Being."
            phrases = [
                "C'è una luce calma sul mare di Policoro.",
                "Tempo autentico da condividere.",
                "KORA. The Art of Being.",
            ]

        originality, anti_notes = self.anti_rep.evaluate_originality(title, hook, phrases)
        feasibility = round(min(1.0, len(available_ids) / 3.0) * (1.0 if logo_asset else 0.75), 2)

        return CreativeConcept(
            id=f"cnc_{uuid.uuid4().hex[:10]}",
            title=title,
            central_idea=f"Racconto visivo ispirato a: {theme.strip() or profile['payoff']} ({self._season_context()})",
            message=" ".join(phrases[:-1]),
            primary_emotion=angle["emotion"],
            hook=hook,
            narrative_arc=" → ".join(phrases),
            ending=phrases[-1],
            voiceover_script=vo_script,
            visual_mood=angle["visual_mood"],
            required_materials=[
                "Ripresa o foto reale del mare/orizzonte",
                "Ambiente pergolato o tavolo preparato",
                "Dettaglio autentico (colazione o aperitivo)",
                "Logo originale KORA",
            ],
            available_asset_ids=available_ids,
            missing_materials=missing,
            duration_sec=duration_sec,
            feasibility_score=feasibility,
            originality_score=originality,
            preset=preset,
            anti_repetition_notes=anti_notes,
        )

    def surprise_me(self, duration_sec: float = 20.0) -> list[CreativeConcept]:
        """Modalità Sorprendimi: proposes 3 original, vault-backed concepts avoiding past repetitions."""
        history = self.anti_rep.get_history()
        used_presets = {h["preset"] for h in history[:3]}
        themes = [
            "Luce morbida del mattino tra pergolato e mare a Policoro",
            "Il piacere di condividere un aperitivo al tramonto davanti al mare",
            "La quiete autentica del mare fuori stagione: poche parole, dettagli reali",
            "Ospitalità semplice e curata: dal caffè del mattino all'orizzonte",
        ]
        concepts: list[CreativeConcept] = []
        for idx in range(4):
            c = self._build_concept_for_theme(themes[idx], angle_idx=idx, duration_sec=duration_sec)
            # Prioritize concepts whose preset/hook hasn't just been used
            penalty = 0.15 if c.preset in used_presets else 0.0
            c.originality_score = round(max(0.1, c.originality_score - penalty), 2)
            concepts.append(c)
        concepts.sort(key=lambda x: (x.originality_score, x.feasibility_score), reverse=True)
        return concepts[:3]

    def ideate_from_theme(
        self,
        theme: str,
        duration_sec: float = 20.0,
        preferred_asset_ids: Optional[list[str]] = None,
    ) -> list[CreativeConcept]:
        """Modalità Tema libero / Materiali caricati: returns 3 distinct concepts."""
        if not theme or not theme.strip():
            return self.surprise_me(duration_sec=duration_sec)
        concepts = [
            self._build_concept_for_theme(
                theme,
                angle_idx=i,
                duration_sec=duration_sec,
                preferred_asset_ids=preferred_asset_ids,
            )
            for i in range(3)
        ]
        return concepts

    def build_storyboard(
        self,
        concept: CreativeConcept,
        voice_id: str = "brando",
        fps: int = DEFAULT_FPS,
    ) -> list[StoryboardScene]:
        """Build a frame-exact storyboard summing precisely to round(duration_sec * fps) frames."""
        total_frames = int(round(concept.duration_sec * fps))
        outro_frames = min(int(round(3.0 * fps)), max(fps * 2, total_frames // 6))
        content_frames = total_frames - outro_frames

        # Split spoken script into phrases (excluding final payoff which belongs to outro)
        raw_phrases = [
            p.strip()
            for p in re.split(r"(?<=[.!?])\s+", concept.voiceover_script)
            if p.strip()
        ]
        if raw_phrases and "the art of being" in raw_phrases[-1].lower():
            outro_phrase = raw_phrases.pop()
        else:
            outro_phrase = "KORA. The Art of Being."
        if raw_phrases and raw_phrases[-1].strip().rstrip(".").upper() == "KORA":
            outro_phrase = f"{raw_phrases.pop()} {outro_phrase}"

        # Gather visual assets from concept
        assets: list[dict[str, Any]] = []
        for aid in concept.available_asset_ids:
            a = self.vault.get_asset(aid)
            if a and a["category"] != "Logo" and a["media_type"] in ("image", "video"):
                assets.append(a)
        if not assets:
            assets = [
                a
                for a in self.vault.list_assets()
                if a["category"] != "Logo" and a["media_type"] in ("image", "video")
            ]

        # Auto-scale scene count by duration (~3.2s per visual scene) and provided media assets
        content_sec = max(4.0, content_frames / fps)
        max_feasible_by_time = max(3, int(content_sec // 1.8))
        desired_by_duration = max(3, min(14, int(round(content_sec / 3.2))))
        desired_by_assets = min(14, len(assets)) if len(assets) > 0 else 4
        num_visual_scenes = max(
            3,
            min(max_feasible_by_time, max(desired_by_duration, desired_by_assets, len(raw_phrases))),
        )
        if len(raw_phrases) == 0:
            raw_phrases = [""] * num_visual_scenes
        elif len(raw_phrases) < num_visual_scenes:
            # Spread phrases across scenes so breathing shots sit naturally between spoken lines
            spread = [""] * num_visual_scenes
            for p_idx, phrase in enumerate(raw_phrases):
                slot = min(
                    num_visual_scenes - 1,
                    int(round(p_idx * (num_visual_scenes - 1) / max(1, len(raw_phrases) - 1)))
                    if len(raw_phrases) > 1
                    else 0,
                )
                spread[slot] = phrase
            raw_phrases = spread
        elif len(raw_phrases) > num_visual_scenes:
            # Merge trailing phrases cleanly
            merged = raw_phrases[: num_visual_scenes - 1]
            merged.append(" ".join(raw_phrases[num_visual_scenes - 1 :]))
            raw_phrases = merged

        # Distribute content_frames deterministically across visual scenes
        base = content_frames // num_visual_scenes
        rem = content_frames % num_visual_scenes
        durations = [base + (1 if i < rem else 0) for i in range(num_visual_scenes)]

        motions_by_preset = {
            EditingPreset.CINEMATIC_IDENTITY.value: [
                CameraMotion.SLOW_PUSH_IN.value,
                CameraMotion.PAN_RIGHT.value,
                CameraMotion.SLOW_PULL_OUT.value,
                CameraMotion.MICRO_PARALLAX.value,
            ],
            EditingPreset.EXPERIENCE.value: [
                CameraMotion.SLOW_PUSH_IN.value,
                CameraMotion.PAN_LEFT.value,
                CameraMotion.TILT_UP.value,
                CameraMotion.SLOW_PULL_OUT.value,
            ],
            EditingPreset.EDITORIAL.value: [
                CameraMotion.STATIC_BREATHE.value,
                CameraMotion.SLOW_PUSH_IN.value,
                CameraMotion.PAN_RIGHT.value,
                CameraMotion.SLOW_PULL_OUT.value,
            ],
        }
        motions = motions_by_preset.get(
            concept.preset, motions_by_preset[EditingPreset.CINEMATIC_IDENTITY.value]
        )

        logo = self.vault.get_logo_asset()
        scenes: list[StoryboardScene] = []
        cursor = 0
        goals = ["Hook atmosferico", "Dettaglio e luce", "Esperienza autentica", "Respiro sul mare", "Chiusura emotiva"]

        for idx in range(num_visual_scenes):
            dur_f = durations[idx]
            asset = assets[idx % len(assets)] if assets else None
            alt_asset = assets[(idx + 1) % len(assets)] if len(assets) > 1 else None
            spoken = raw_phrases[idx] if voice_id != "none" else ""
            sub = raw_phrases[idx]

            # Infer authentic SFX matching only what's in the scene
            sfx_cat = "ambiente_spiaggia"
            if asset:
                cat_l = asset["category"].lower()
                name_l = asset["filename"].lower()
                if "mare" in cat_l or "mare" in name_l:
                    sfx_cat = "onde"
                elif "colazione" in cat_l or "tazzina" in name_l:
                    sfx_cat = "tazzine"
                elif "aperitivo" in cat_l or "bicchiere" in name_l:
                    sfx_cat = "bicchieri"
                elif "pergolato" in cat_l or "ombre" in name_l:
                    sfx_cat = "vento"

            scenes.append(
                StoryboardScene(
                    scene_number=idx + 1,
                    start_frame=cursor,
                    end_frame=cursor + dur_f,
                    duration_frames=dur_f,
                    start_sec=round(cursor / fps, 3),
                    end_sec=round((cursor + dur_f) / fps, 3),
                    duration_sec=round(dur_f / fps, 3),
                    narrative_goal=goals[idx % len(goals)],
                    spoken_text=spoken,
                    voice_id=voice_id,
                    selected_asset_id=asset["id"] if asset else None,
                    alternative_asset_id=alt_asset["id"] if alt_asset else None,
                    asset_mode="video_clip" if (asset and asset["media_type"] == "video") else "animated_photo",
                    camera_motion=motions[idx % len(motions)],
                    transition=SceneTransition.CROSS_DISSOLVE.value
                    if idx < num_visual_scenes - 1
                    else SceneTransition.FADE_BLACK.value,
                    sfx_category=sfx_cat,
                    music_cue="musica_cinematic" if "cinematic" in concept.preset.lower() else "musica_ambient",
                    subtitle_text=sub,
                    production_status="pianificata",
                    approval_status="approvata",
                    is_outro=False,
                )
            )
            cursor += dur_f

        # Final Logo Outro (exact remaining frames so total == total_frames)
        scenes.append(
            StoryboardScene(
                scene_number=num_visual_scenes + 1,
                start_frame=cursor,
                end_frame=total_frames,
                duration_frames=total_frames - cursor,
                start_sec=round(cursor / fps, 3),
                end_sec=round(total_frames / fps, 3),
                duration_sec=round((total_frames - cursor) / fps, 3),
                narrative_goal="Logo Outro KORA — The Art of Being",
                spoken_text=outro_phrase if voice_id != "none" else "",
                voice_id=voice_id,
                selected_asset_id=logo["id"] if logo else None,
                asset_mode="logo_outro",
                camera_motion=CameraMotion.STATIC_BREATHE.value,
                transition=SceneTransition.FADE_BLACK.value,
                sfx_category="transizioni",
                music_cue="fade_out",
                subtitle_text="The Art of Being",
                production_status="pianificata",
                approval_status="approvata",
                is_outro=True,
            )
        )
        return scenes
