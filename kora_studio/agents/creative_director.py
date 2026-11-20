"""Creative Director agent — LLM Provider Manager + Offline Fallback, 4-theme ideation, Anti-Repetition, and Storyboard 2.0."""

from __future__ import annotations

import json
import random
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from kora_studio.agents.brand_guardian import BrandGuardian
from kora_studio.brand.brand_brain import BrandBrain
from kora_studio.config import DEFAULT_FPS
from kora_studio.db.database import Database, utcnow_iso
from kora_studio.db.models import (
    AuthenticityStatus,
    CameraMotion,
    CreativeConcept,
    CreativeProposalsEnvelope,
    CreativeRequestPayload,
    EditingPreset,
    SceneTransition,
    StoryboardScene,
)
from kora_studio.ai_engine.context import BrandContextBuilder
from kora_studio.providers.registry import ProviderManager
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
    """Generates KORA-aligned concepts via LLM Provider Manager (with explicit deterministic offline fallback) and Storyboard 2.0."""

    def __init__(
        self,
        db: Database,
        brand_brain: BrandBrain,
        vault: MediaVault,
        providers: Optional[ProviderManager] = None,
    ) -> None:
        self.db = db
        self.brand_brain = brand_brain
        self.guardian = BrandGuardian(brand_brain)
        self.vault = vault
        self.providers = providers
        self.anti_rep = AntiRepetitionEngine(db)
        self.ai_engine: Any = None
        self.brand_context: Any = None
        self.story_director: Any = None
        self.visual_intelligence: Any = None
        self.last_storyboard_plan: list[dict[str, Any]] = []

    def attach_engine_manager(self, engine_manager: Any) -> None:
        """Collega l'AI Engine Manager (provider/agenti configurabili senza riavvio)."""
        from kora_studio.agents.story_director import CinematicStoryDirector
        from kora_studio.agents.visual_intelligence import VisualIntelligence

        self.ai_engine = engine_manager
        self.brand_context = BrandContextBuilder(
            self.brand_brain,
            history_provider=lambda limit=8: self.anti_rep.get_history(limit),
            season_provider=self._season_context,
        )
        self.story_director = CinematicStoryDirector(self.vault, engine_manager)
        self.visual_intelligence = VisualIntelligence(self.vault, engine_manager, db=self.db)
        if hasattr(engine_manager, "register_vision_listener"):
            engine_manager.register_vision_listener(self._on_vision_verified)
        self.guardian.attach_engine_manager(engine_manager)

    def _on_vision_verified(self, provider_id: str = "", model: str = "") -> dict[str, Any]:
        """Auto-upgrade fallback analyses when a Vision model becomes verified."""
        result = self.visual_intelligence.upgrade_fallback_analyses()
        return {"trigger": "vision_verified", "provider": provider_id, "model": model, **result}

    def _call_engine_manager(
        self, req: CreativeRequestPayload, count: int, valid_asset_ids: set[str]
    ) -> tuple[Optional[list[CreativeConcept]], Optional[str]]:
        """Prima scelta: AI Engine Manager con routing per agente. None se non disponibile/autorizzato."""
        engine = getattr(self, "ai_engine", None)
        if engine is None:
            return None, None
        try:
            from kora_studio.ai_engine.base import EngineError
        except Exception:  # pragma: no cover
            return None, None
        context = self._build_llm_context(req)
        builder = getattr(self, "brand_context", None)
        system_prompt = (
            "Sei il Direttore Creativo di KORA Beach Club (Policoro). Rispetta l'identità luxury "
            "mediterranea, calma ed evocativa. Niente slogan aggressivi, sconti o prezzi. Usa SOLO gli ID "
            "asset reali dell'inventario. Rispondi SOLO con JSON valido: "
            '{"concepts": [{"title","central_idea","message","primary_emotion","hook","narrative_arc",'
            '"ending","voiceover_script","visual_mood","moodboard_tags","required_materials",'
            '"available_asset_ids","missing_materials","objective","recommended_voice_id","duration_sec",'
            '"feasibility_score","originality_score","preset"}]}'
        )
        if builder is not None:
            try:
                system_prompt = (
                    builder.to_prompt_block(builder.build("creative_director")) + "\n\n" + system_prompt
                )
            except Exception:
                pass
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {"richiesta": context, "numero_proposte": count}, ensure_ascii=False
                ),
            },
        ]
        try:
            result = engine.call("creative_director", messages, json_mode=True, timeout=120)
        except EngineError as exc:
            return None, f"AI Engine: {exc}"
        except Exception as exc:  # pragma: no cover
            return None, f"AI Engine: errore inatteso ({exc.__class__.__name__})"
        parsed = self._parse_and_validate_llm_concepts(
            result.text, req, valid_asset_ids, result.provider
        )
        if not parsed:
            return None, f"AI Engine ({result.provider}/{result.model}): risposta non interpretabile"
        return parsed, None

    def _build_llm_context(self, req: CreativeRequestPayload) -> dict[str, Any]:
        """Assemble full KORA context: versioned brand profile, history, season, semantic vault, user request."""
        profile = self.brand_brain.get_current()
        if req.brand_profile_version:
            for h in self.brand_brain.get_history():
                if int(h.get("version", 0)) == int(req.brand_profile_version):
                    profile = h
                    break
        history = self.anti_rep.get_history(limit=15)
        if req.previous_production_ids:
            history = [h for h in history if h["project_id"] in req.previous_production_ids] or history

        visual_assets = [
            a
            for a in self.vault.list_assets()
            if a["media_type"] in ("image", "video") and a["category"] != "Logo"
        ]
        inventory = [
            {
                "id": a["id"],
                "filename": a["filename"],
                "category": a["category"],
                "media_type": a["media_type"],
                "tags": a["tags"][:8],
                "quality": a["technical_quality"],
                "authenticity": a["authenticity_status"],
                "multimodal": a.get("analysis", {}).get("multimodal", {}),
            }
            for a in visual_assets[:24]
        ]
        return {
            "brand_profile": {
                "version": profile.get("version"),
                "brand_name": profile.get("brand_name"),
                "location": profile.get("location"),
                "payoff": profile.get("payoff"),
                "philosophy": profile.get("philosophy", ""),
                "vision": profile.get("vision", ""),
                "mission": profile.get("mission", ""),
                "tone_of_voice": profile.get("tone_of_voice", []),
                "values": profile.get("values", []),
                "forbidden_claims": profile.get("forbidden_claims", []),
            },
            "season": self._season_context(),
            "past_productions": [
                {"title": h["title"], "hook": h["hook"], "preset": h["preset"], "theme": h["theme"]}
                for h in history[:8]
            ],
            "vault_inventory": inventory,
            "request": req.model_dump(),
        }

    def _extract_json_block(self, raw_text: str) -> dict[str, Any]:
        cleaned = raw_text.strip()
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(1)
        else:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                cleaned = cleaned[start : end + 1]
        return json.loads(cleaned)

    def _call_llm_proposals(
        self, req: CreativeRequestPayload, count: int = 3
    ) -> tuple[Optional[list[CreativeConcept]], str, Optional[str]]:
        """Try configured LLM providers (Ollama local first, then authorized cloud APIs) and validate via Pydantic."""
        if not self.providers:
            return None, "deterministic_offline_fallback", "ProviderManager non collegato."

        ctx = self._build_llm_context(req)
        system_prompt = (
            "Sei il Direttore Creativo di KORA Beach Club (Policoro). Rispetta rigorosamente l'identità luxury "
            "mediterranea, calma, evocativa ('The Art of Being'). Niente slogan aggressivi, niente sconti o prezzi. "
            "Usa SOLO gli ID asset reali presenti in vault_inventory. Restituisci SOLO JSON valido conforme allo schema "
            "{\"concepts\": [{\"id\", \"title\", \"central_idea\", \"message\", \"primary_emotion\", \"hook\", "
            "\"narrative_arc\", \"ending\", \"voiceover_script\", \"visual_mood\", \"moodboard_tags\", "
            "\"required_materials\", \"available_asset_ids\", \"missing_materials\", \"objective\", "
            "\"recommended_voice_id\", \"duration_sec\", \"feasibility_score\", \"originality_score\", \"preset\"}]}. "
            f"Genera esattamente {count} proposte originali e diverse tra loro e dalle past_productions."
        )
        user_prompt = json.dumps(ctx, ensure_ascii=False)

        errors: list[str] = []
        valid_ids = {a["id"] for a in ctx["vault_inventory"]}

        # 0. AI Engine Manager (provider/agenti configurabili, nessun riavvio richiesto)
        engine_concepts, engine_error = self._call_engine_manager(req, count, valid_ids)
        if engine_concepts:
            return engine_concepts, engine_concepts[0].generation_engine or "ai_engine", None
        if engine_error:
            errors.append(engine_error)

        reason = (
            self._fallback_reason(errors)
            if errors
            else "Nessun motore AI configurato o autorizzato: utilizzato il motore deterministico offline KORA (0 €)."
        )
        return None, "deterministic_offline_fallback", reason

    @staticmethod
    def _fallback_reason(errors: list[str]) -> str:
        """Messaggio leggibile: motore locale attivo + dettaglio compatto per provider."""
        head = "Motore deterministico locale KORA attivo (0 €): nessun motore AI ha risposto."
        return f"{head} Dettagli → " + " | ".join(errors)

    def _parse_and_validate_llm_concepts(
        self,
        raw_txt: str,
        req: CreativeRequestPayload,
        valid_asset_ids: set[str],
        engine_name: str,
    ) -> Optional[list[CreativeConcept]]:
        data = self._extract_json_block(raw_txt)
        items = data.get("concepts") if isinstance(data, dict) else data
        if not isinstance(items, list) or not items:
            return None
        out: list[CreativeConcept] = []
        for idx, raw in enumerate(items):
            if not isinstance(raw, dict):
                continue
            cleaned_ids = [aid for aid in (raw.get("available_asset_ids") or []) if aid in valid_asset_ids]
            if not cleaned_ids:
                cleaned_ids = list(valid_asset_ids)[:6]
            raw.setdefault("id", f"cnc_{uuid.uuid4().hex[:10]}")
            raw["available_asset_ids"] = cleaned_ids
            raw["duration_sec"] = req.duration_sec
            raw["generation_engine"] = engine_name
            raw["fallback_reason"] = None
            raw["creative_request"] = req.model_dump()
            raw.setdefault("objective", req.objective or "Valorizzare l'identità KORA")
            raw.setdefault("recommended_voice_id", req.voice_id or "brando")
            # Un motore esterno può omettere campi: completiamo con valori sicuri
            # invece di scartare l'intera proposta (nessun dato inventato, solo placeholder neutri).
            for field_name, field in CreativeConcept.model_fields.items():
                if field_name in raw and raw[field_name] is not None:
                    continue
                annotation = str(field.annotation)
                if "list" in annotation:
                    raw[field_name] = []
                elif "bool" in annotation:
                    raw[field_name] = False
                elif "int" in annotation and "float" not in annotation:
                    raw[field_name] = 0
                elif "float" in annotation or "int" in annotation:
                    raw[field_name] = 0.75
                else:
                    raw[field_name] = ""
            concept = CreativeConcept(**raw)
            if not self.guardian.validate_copy(concept.voiceover_script)["approved"]:
                continue
            orig, notes = self.anti_rep.evaluate_originality(
                concept.title, concept.hook, [concept.message]
            )
            concept.originality_score = orig
            concept.anti_repetition_notes = notes
            out.append(concept)
        return out or None

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
            moodboard_tags=[
                angle["angle"].replace("_", " "),
                preset.replace("KORA ", ""),
                *(a["category"].lower() for a in candidate_assets[:4]),
            ],
            required_materials=[
                "Ripresa o foto reale del mare/orizzonte",
                "Ambiente pergolato o tavolo preparato",
                "Dettaglio autentico (colazione o aperitivo)",
                "Logo originale KORA",
            ],
            available_asset_ids=available_ids,
            missing_materials=missing,
            objective=f"Valorizzare l'identità KORA ({angle['emotion'].lower()})",
            recommended_voice_id="tiziana" if angle_idx % 2 == 1 else "brando",
            duration_sec=duration_sec,
            feasibility_score=feasibility,
            originality_score=originality,
            preset=preset,
            generation_engine="deterministic_offline_fallback",
            fallback_reason=None,
            anti_repetition_notes=anti_notes,
        )

    def suggest_four_themes(
        self, req: Optional[CreativeRequestPayload] = None
    ) -> CreativeProposalsEnvelope:
        """Generate 4 original, non-fixed narrative proposals using LLM Provider Manager or declared offline fallback."""
        req = req or CreativeRequestPayload()
        llm_concepts, engine, fallback_reason = self._call_llm_proposals(req, count=4)
        if llm_concepts and len(llm_concepts) >= 4:
            return CreativeProposalsEnvelope(
                concepts=llm_concepts[:4],
                generation_engine=engine,
                fallback_reason=None,
            )

        # Declared deterministic offline fallback: dynamically synthesize 4 distinct narratives from live Vault + season + history
        history = self.anti_rep.get_history(limit=20)
        used_titles = {h["title"].strip().lower() for h in history}
        used_presets = {h["preset"] for h in history[:3]}
        vault_visuals = [
            a
            for a in self.vault.list_assets()
            if a["media_type"] in ("image", "video") and a["category"] != "Logo"
        ]
        # Build dynamic theme seeds from actual media categories & user context (never a fixed 4-item ceiling)
        cat_pool = sorted({a["category"] for a in vault_visuals}) or ["Mare", "Pergolato", "Colazione", "Aperitivo"]
        season = self._season_context()
        dynamic_prompts = [
            f"{cat_pool[0 % len(cat_pool)]} e luce naturale: {req.objective or 'respiro autentico al KORA'} ({season})",
            f"Ospitalità condivisa tra {cat_pool[1 % len(cat_pool)]} e {cat_pool[2 % len(cat_pool)]} a Policoro",
            f"Dettagli tattili e silenzio sul mare: focus {cat_pool[3 % len(cat_pool)]} con poche parole",
            f"{(req.theme.strip() + ' — ') if req.theme.strip() else ''}Dialogo a due voci tra mattina e tramonto vista Ionio",
        ]
        voice_recs = ["brando", "tiziana", "brando_tiziana", "none"]
        objectives = [
            req.objective or "Identità di marca & atmosfera mediterranea",
            req.objective or "Esperienza conviviale & ospitalità vista mare",
            req.objective or "Estetica editoriale & cura dei dettagli reali",
            req.objective or "Racconto stagionale a due voci Brando & Tiziana",
        ]

        concepts: list[CreativeConcept] = []
        for idx in range(4):
            c = self._build_concept_for_theme(
                dynamic_prompts[idx],
                angle_idx=idx,
                duration_sec=req.duration_sec,
                preferred_asset_ids=req.asset_ids or None,
            )
            if c.title.lower() in used_titles:
                c.title = f"{c.title} · Ed. {len(history) + idx + 1}"
            penalty = 0.12 if c.preset in used_presets else 0.0
            c.originality_score = round(max(0.15, c.originality_score - penalty), 2)
            c.objective = objectives[idx]
            c.recommended_voice_id = req.voice_id if req.voice_id and idx == 0 else voice_recs[idx]
            c.generation_engine = "deterministic_offline_fallback"
            c.fallback_reason = fallback_reason
            c.creative_request = req.model_dump()
            concepts.append(c)
        return CreativeProposalsEnvelope(
            concepts=concepts,
            generation_engine="deterministic_offline_fallback",
            fallback_reason=fallback_reason,
        )

    def ideate_from_request(self, req: CreativeRequestPayload) -> CreativeProposalsEnvelope:
        """Ideate 3 concepts for a structured CreativeRequestPayload using LLM or declared fallback."""
        if not req.theme.strip():
            env = self.suggest_four_themes(req)
            return CreativeProposalsEnvelope(
                concepts=env.concepts[:3],
                generation_engine=env.generation_engine,
                fallback_reason=env.fallback_reason,
            )
        llm_concepts, engine, fallback_reason = self._call_llm_proposals(req, count=3)
        if llm_concepts:
            return CreativeProposalsEnvelope(
                concepts=llm_concepts[:3],
                generation_engine=engine,
                fallback_reason=None,
            )
        concepts = []
        for i in range(3):
            c = self._build_concept_for_theme(
                req.theme,
                angle_idx=i,
                duration_sec=req.duration_sec,
                preferred_asset_ids=req.asset_ids or None,
            )
            c.objective = req.objective or c.objective
            c.recommended_voice_id = req.voice_id or c.recommended_voice_id
            c.generation_engine = "deterministic_offline_fallback"
            c.fallback_reason = fallback_reason
            c.creative_request = req.model_dump()
            concepts.append(c)
        return CreativeProposalsEnvelope(
            concepts=concepts,
            generation_engine="deterministic_offline_fallback",
            fallback_reason=fallback_reason,
        )

    def surprise_me(self, duration_sec: float = 20.0) -> list[CreativeConcept]:
        """Modalità Sorprendimi: proposes 3 original, vault-backed concepts avoiding past repetitions."""
        env = self.suggest_four_themes(CreativeRequestPayload(duration_sec=duration_sec))
        return sorted(
            env.concepts,
            key=lambda x: (x.originality_score, x.feasibility_score),
            reverse=True,
        )[:3]

    def ideate_from_theme(
        self,
        theme: str,
        duration_sec: float = 20.0,
        preferred_asset_ids: Optional[list[str]] = None,
    ) -> list[CreativeConcept]:
        """Modalità Tema libero / Materiali caricati: returns 3 distinct concepts."""
        env = self.ideate_from_request(
            CreativeRequestPayload(
                theme=theme,
                duration_sec=duration_sec,
                asset_ids=preferred_asset_ids or [],
            )
        )
        return env.concepts

    def build_storyboard(
        self,
        concept: CreativeConcept,
        voice_id: str = "brando",
        fps: int = DEFAULT_FPS,
        authorized_asset_ids: Optional[list[str]] = None,
    ) -> list[StoryboardScene]:
        """Storyboard Engine 2.0: narrative-driven asset planning & exact 25fps frame math.

        ``authorized_asset_ids`` is a *constraint on the inventory*, never a forced
        sequence: the Story Director is free to choose the best authorised photo for
        each scene, and the outro logo stays independent from this selection.
        """
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

        # Gather visual assets from concept + full vault pool
        preferred_assets: list[dict[str, Any]] = []
        for aid in concept.available_asset_ids:
            a = self.vault.get_asset(aid)
            if a and a["category"] != "Logo" and a["media_type"] in ("image", "video"):
                preferred_assets.append(a)
        all_visuals = [
            a
            for a in self.vault.list_assets()
            if a["category"] != "Logo" and a["media_type"] in ("image", "video")
        ]
        if authorized_asset_ids is not None:
            auth = {str(a) for a in authorized_asset_ids if str(a).strip()}
            # the authorised inventory *is* the inventory; nothing outside it may appear
            assets_pool = [
                a
                for a in (preferred_assets + all_visuals)
                if a.get("id") in auth and a["category"] != "Logo"
            ]
            preferred_assets = [a for a in preferred_assets if a["id"] in auth]
        else:
            assets_pool = preferred_assets + [
                a for a in all_visuals if a["id"] not in {p["id"] for p in preferred_assets}
            ]

        # Auto-scale scene count by duration (~3.2s per visual scene) and provided media assets
        content_sec = max(4.0, content_frames / fps)
        max_feasible_by_time = max(3, int(content_sec // 1.8))
        desired_by_duration = max(3, min(14, int(round(content_sec / 3.2))))
        desired_by_assets = min(14, len(preferred_assets)) if preferred_assets else 4
        num_visual_scenes = max(
            3,
            min(max_feasible_by_time, max(desired_by_duration, desired_by_assets, len(raw_phrases))),
        )
        if len(raw_phrases) == 0:
            raw_phrases = [""] * num_visual_scenes
        elif len(raw_phrases) < num_visual_scenes:
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
            merged = raw_phrases[: num_visual_scenes - 1]
            merged.append(" ".join(raw_phrases[num_visual_scenes - 1 :]))
            raw_phrases = merged

        # Narrative phase pacing: weight spoken scenes slightly longer than silent breath shots
        weights = [1.25 if raw_phrases[i].strip() else 0.85 for i in range(num_visual_scenes)]
        w_sum = sum(weights) or 1.0
        durations = [max(fps, int(round(content_frames * (w / w_sum)))) for w in weights]
        diff = content_frames - sum(durations)
        d_idx = 0
        while diff != 0 and num_visual_scenes > 0:
            pos = d_idx % num_visual_scenes
            if diff > 0:
                durations[pos] += 1
                diff -= 1
            elif durations[pos] > fps:
                durations[pos] -= 1
                diff += 1
            d_idx += 1

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

        # Visual Intelligence reale: rivede il materiale e arricchisce l'analisi
        # (solo con un modello visivo VERIFICATO; altrimenti analisi deterministica dichiarata).
        visual = getattr(self, "visual_intelligence", None)
        if visual is not None and getattr(self, "ai_engine", None) is not None:
            try:
                updated = visual.annotate_vault(production_id=getattr(concept, "id", None), limit=6)
                if updated:
                    all_visuals = [
                        a
                        for a in self.vault.list_assets()
                        if a["category"] != "Logo" and a["media_type"] in ("image", "video")
                    ]
            except Exception:
                pass

        logo = self.vault.get_logo_asset()
        scenes: list[StoryboardScene] = []
        cursor = 0
        goals = [
            "Hook atmosferico (apertura)",
            "Ambiente & architettura naturale",
            "Dettaglio tattile reale",
            "Esperienza conviviale KORA",
            "Respiro visivo sul mare",
            "Chiusura emotiva",
        ]
        used_asset_ids: list[str] = []

        for idx in range(num_visual_scenes):
            dur_f = durations[idx]
            goal = goals[idx % len(goals)]
            spoken = raw_phrases[idx] if voice_id != "none" else ""
            sub = raw_phrases[idx]
            phase_query = f"{goal} {sub} {concept.visual_mood}".strip()

            # Narrative semantic selection *inside the authorised inventory only*
            pool_ids = {a["id"] for a in assets_pool}
            sem_matches = [
                m for m in self.vault.semantic_search(phase_query, limit=16) if m["id"] in pool_ids
            ][:8]
            candidates = sem_matches + [
                a for a in assets_pool if a["id"] not in {m["id"] for m in sem_matches}
            ]
            last_id = used_asset_ids[-1] if used_asset_ids else None
            unused = [a for a in candidates if a["id"] not in used_asset_ids]
            non_repeat = [a for a in candidates if a["id"] != last_id]
            asset = (unused[0] if unused else (non_repeat[0] if non_repeat else (candidates[0] if candidates else None)))
            alt_pool = [a for a in candidates if asset and a["id"] != asset["id"]]
            alt_asset = alt_pool[0] if alt_pool else None
            if asset:
                used_asset_ids.append(asset["id"])

            mm = (asset or {}).get("analysis", {}).get("multimodal", {})
            framing = mm.get("framing") or (
                "campo_ampio" if idx == 0 else ("dettaglio_ravvicinato" if "Dettaglio" in goal else "piano_medio")
            )
            rationale = (
                f"Scelto '{asset['filename']}' ({asset['category']}, {mm.get('lighting', 'luce naturale')}) "
                f"per sostenere '{goal}' senza alterare l'ambiente reale."
                if asset
                else "Nessun asset reale disponibile: richiesto caricamento o ritaglio conservativo."
            )

            # Assign speaker in dual-voice mode (Brando & Tiziana alternating per spoken line)
            scene_voice = voice_id
            if voice_id == "brando_tiziana":
                scene_voice = "brando" if idx % 2 == 0 else "tiziana"

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

            gen_prompt = None
            if not asset:
                gen_prompt = (
                    f"Generazione vincolata KORA Beach Club Policoro per '{goal}': {concept.visual_mood}. "
                    "Non alterare architettura o arredi reali."
                )

            scenes.append(
                StoryboardScene(
                    scene_number=idx + 1,
                    start_frame=cursor,
                    end_frame=cursor + dur_f,
                    duration_frames=dur_f,
                    start_sec=round(cursor / fps, 3),
                    end_sec=round((cursor + dur_f) / fps, 3),
                    duration_sec=round(dur_f / fps, 3),
                    narrative_goal=goal,
                    spoken_text=spoken,
                    voice_id=scene_voice,
                    selected_asset_id=asset["id"] if asset else None,
                    alternative_asset_id=alt_asset["id"] if alt_asset else None,
                    visual_rationale=rationale,
                    shot_framing=framing,
                    asset_mode="video_clip" if (asset and asset["media_type"] == "video") else "animated_photo",
                    camera_motion=motions[idx % len(motions)],
                    transition=SceneTransition.CROSS_DISSOLVE.value
                    if idx < num_visual_scenes - 1
                    else SceneTransition.FADE_BLACK.value,
                    sfx_category=sfx_cat,
                    music_cue="musica_cinematic" if "cinematic" in concept.preset.lower() else "musica_ambient",
                    subtitle_text=sub,
                    generative_prompt=gen_prompt,
                    authenticity_status=(
                        asset["authenticity_status"] if asset else AuthenticityStatus.DA_VERIFICARE.value
                    ),
                    production_status="pianificata",
                    approval_status="approvata",
                    is_outro=False,
                )
            )
            cursor += dur_f

        # Final Logo Outro (exact remaining frames so total == total_frames)
        outro_voice = "tiziana" if voice_id == "brando_tiziana" else voice_id
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
                voice_id=outro_voice,
                selected_asset_id=logo["id"] if logo else None,
                visual_rationale="Chiusura istituzionale con logo originale e payoff esatto 'The Art of Being'.",
                shot_framing="outro_logo",
                asset_mode="logo_outro",
                camera_motion=CameraMotion.STATIC_BREATHE.value,
                transition=SceneTransition.FADE_BLACK.value,
                sfx_category="transizioni",
                music_cue="fade_out",
                subtitle_text="The Art of Being",
                authenticity_status=AuthenticityStatus.AUTENTICO.value,
                production_status="pianificata",
                approval_status="approvata",
                is_outro=True,
            )
        )
        # Cinematic Story Director: piano delle inquadrature (motore configurabile,
        # fallback deterministico proporzionale — mai sequenze cicliche).
        story_director = getattr(self, "story_director", None)
        if story_director is not None:
            # Only the authorised inventory reaches the director (outro logo excluded).
            assets_now = [a for a in assets_pool if a["category"] != "Logo"]
            by_id = {a["id"]: a for a in assets_now}
            plan = story_director.plan(
                scenes,
                assets_now,
                preset=concept.preset,
                production_id=getattr(concept, "id", None),
                authorized_ids=authorized_asset_ids,
            )
            if plan:
                story_director.apply_plan(scenes, plan, by_id)
                self.last_storyboard_plan = plan
                self.last_plan_validation = getattr(story_director, "last_validation", {})
        self.apply_shot_plans(scenes)
        return scenes

    # ------------------------------------------------------- derived framing
    def apply_shot_plans(
        self, scenes: list[StoryboardScene], only_scene_numbers: Optional[set[int]] = None
    ) -> list[StoryboardScene]:
        """Attach a real Smart Shot plan (crop, keyframes, zoom ramp) to each photo scene."""
        generator = getattr(self, "shot_generator", None)
        if generator is None:
            from kora_studio.agents.smart_shot_generator import SmartShotGenerator

            generator = SmartShotGenerator(self.vault)
            self.shot_generator = generator
        for scene in scenes:
            if scene.is_outro:
                scene.shot_source = "logo_component"
                continue
            if only_scene_numbers is not None and scene.scene_number not in only_scene_numbers:
                continue
            asset = self.vault.get_asset(scene.selected_asset_id) if scene.selected_asset_id else None
            if not asset:
                scene.shot_plan = None
                scene.shot_source = "fallback:asset_mancante"
                continue
            if asset.get("media_type") == "video":
                # video clips keep their own real motion: no derived still framing
                scene.shot_plan = None
                scene.shot_source = "video_clip"
                continue
            plan = generator.plan_for_scene(
                asset,
                purpose=scene.narrative_goal,
                duration_frames=scene.duration_frames,
                motion_hint="still" if scene.camera_motion == CameraMotion.STATIC_BREATHE.value else None,
            )
            if plan:
                ok, issues = generator.validate_plan(plan, plan.get("source_size") or {})
                if not ok:
                    scene.shot_plan = None
                    scene.shot_source = "fallback:no_plan"
                    scene.visual_rationale = (
                        f"{scene.visual_rationale} Piano inquadratura derivato non valido: {'; '.join(issues)}. "
                        "Usato movimento deterministico conservativo."
                    )
                    continue
                scene.shot_plan = plan
                scene.shot_source = "smart_shot"
                scene.crop_focus = {
                    "x": plan["focal_point"]["x"],
                    "y": plan["focal_point"]["y"],
                    "zoom": plan["zoom_end"],
                }
                # the plan's grammar wins over the generic motion name
                scene.camera_motion = {
                    "push_in": CameraMotion.SLOW_PUSH_IN.value,
                    "pull_out": CameraMotion.SLOW_PULL_OUT.value,
                    "detail_push": CameraMotion.SLOW_PUSH_IN.value,
                    "parallax": CameraMotion.MICRO_PARALLAX.value,
                    "drift": CameraMotion.PAN_RIGHT.value,
                    "settle": CameraMotion.STATIC_BREATHE.value,
                }.get(plan["trajectory"], scene.camera_motion)
                scene.visual_rationale = f"{scene.visual_rationale} {plan['motivation']}".strip()
        return scenes

    def enforce_authorized_inventory(
        self,
        scenes: list[StoryboardScene],
        authorized_asset_ids: Optional[list[str]],
    ) -> list[StoryboardScene]:
        """Manual selection constrains the inventory — it never forces a per-index sequence.

        Scenes whose asset is outside the authorised set are re-cast with the best
        authorised alternative (semantic match); approved scenes stay untouched.
        """
        if not authorized_asset_ids:
            return scenes
        auth = {str(a) for a in authorized_asset_ids if str(a).strip()}
        pool = [
            a
            for a in self.vault.list_assets()
            if a["id"] in auth and a["category"] != "Logo" and a["media_type"] in ("image", "video")
        ]
        if not pool:
            return scenes
        by_id = {a["id"]: a for a in pool}
        used: set[str] = set()
        for scene in [s for s in scenes if not s.is_outro]:
            recast = scene.selected_asset_id not in auth
            if scene.selected_asset_id:
                used.add(scene.selected_asset_id)
            if recast:
                query = f"{scene.narrative_goal} {scene.spoken_text}".strip()
                matches = [m["id"] for m in self.vault.semantic_search(query, limit=12) if m["id"] in by_id]
                choice = next((mid for mid in matches if mid not in used), matches[0] if matches else None)
                choice = choice or next((a["id"] for a in pool if a["id"] not in used), pool[0]["id"])
                scene.selected_asset_id = choice
                scene.alternative_asset_id = None
                scene.visual_rationale = (
                    f"Ricollocata su '{by_id[choice]['filename']}' (inventario autorizzato) "
                    f"per sostenere «{scene.narrative_goal}»."
                )
                scene.authenticity_status = by_id[choice].get("authenticity_status") or scene.authenticity_status
                scene.asset_mode = "video_clip" if by_id[choice]["media_type"] == "video" else "animated_photo"
                scene.approval_status = "da_riverificare"
            used.add(choice if recast else scene.selected_asset_id)
        self.apply_shot_plans(scenes)
        return scenes
