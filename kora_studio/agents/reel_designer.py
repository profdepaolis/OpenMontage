"""KORA STUDIO Essential — Progetta Reel in UNA sola richiesta creativa.

Dopo la scelta del tema, il motore creativo attivo restituisce in un unico JSON:

* concept (titolo, hook, messaggio, arco narrativo, voiceover definitivo, mood);
* storyboard scena per scena (obiettivo visivo, asset, inquadratura, movimento, transizione);
* sound design, copertina e caption Instagram.

La matematica dei frame resta deterministica (25 fps, durata esatta): l'AI decide
*cosa* si vede, KORA garantisce *quando* e *per quanto*.

Se il motore non è disponibile o risponde fuori schema, si usa il progetto
deterministico KORA (0 €) dichiarando il motivo: nessuna catena di provider.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from kora_studio.ai_engine.base import EngineCancelled, EngineError
from kora_studio.ai_engine.models import (
    BILLING_LOCAL,
    EXEC_LOCAL,
)
from kora_studio.db.models import (
    CameraMotion,
    CreativeConcept,
    CreativeRequestPayload,
    SceneTransition,
    StoryboardScene,
    ThemeProposalLite,
)

AGENT = "creative_director"
DEFAULT_FPS = 25
SCHEMA_VERSION = "reel-design-v1"
MAX_INVENTORY = 20

PHILOSOPHY = (
    "KORA STUDIO — The Art of Being (KORA Beach Club, Policoro). Luxury mediterraneo: "
    "calma, luce naturale, ospitalità autentica, dettagli reali. Niente slogan, sconti o prezzi."
)

FRAMINGS = {
    "campo_ampio",
    "piano_medio",
    "piano_americano",
    "dettaglio_ravvicinato",
    "dettaglio_tattile",
    "controcampo",
}


@dataclass
class ReelDesign:
    concept: CreativeConcept
    scenes: list[StoryboardScene]
    caption: str
    cover: dict[str, str]
    sound_design: dict[str, str]
    generation_engine: str
    model: str = ""
    fallback_reason: Optional[str] = None
    timings_ms: dict[str, Any] = field(default_factory=dict)
    calls: int = 0
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept.model_dump(),
            "scenes": [s.model_dump() for s in self.scenes],
            "caption": self.caption,
            "cover": self.cover,
            "sound_design": self.sound_design,
            "generation_engine": self.generation_engine,
            "model": self.model,
            "fallback_reason": self.fallback_reason,
            "timings_ms": self.timings_ms,
            "calls": self.calls,
            "schema_version": self.schema_version,
        }


class ReelDesigner:
    """Una sola chiamata creativa per progetto; schema validato; frame esatti."""

    def __init__(self, director: Any) -> None:
        self.director = director
        self.creative = director.creative
        self.db = director.db

    # ------------------------------------------------------------------ public
    def design(
        self,
        req: Optional[CreativeRequestPayload] = None,
        proposal: Optional[ThemeProposalLite] = None,
        *,
        deadline_sec: float = 45.0,
    ) -> ReelDesign:
        req = req or CreativeRequestPayload()
        if proposal is not None:
            # Durata e voce esplicite dell'utente vincono sempre sul consiglio dell'AI.
            suggested_duration = float(proposal.recommended_duration_sec or req.duration_sec)
            chosen_duration = req.duration_sec if req.duration_locked else suggested_duration
            chosen_voice = req.voice_id if req.voice_locked else (proposal.recommended_voice_id or req.voice_id)
            req = req.model_copy(
                update={
                    "theme": proposal.title or req.theme,
                    "duration_sec": round(float(chosen_duration), 3),
                    "voice_id": chosen_voice,
                    "objective": req.objective or proposal.recommended_objective,
                }
            )
        started = time.perf_counter()
        timings: dict[str, Any] = {}
        manager = getattr(self.director, "model_manager", None)
        if manager is not None:
            try:
                configured = float(manager.timeout_for("design"))
                deadline_sec = max(deadline_sec, configured)
            except Exception:
                pass
        inventory = self._inventory(req)
        ctx = self._context(req, proposal, inventory)
        timings["context_building"] = round((time.perf_counter() - started) * 1000, 1)

        engine = getattr(self.director, "ai_engine", None)
        entry = None
        if engine is not None:
            try:
                # 3.6.1 — stesso modello mostrato nella Home (Model Manager)
                entry = engine.creative_entry(AGENT, purpose="design")
            except Exception:
                entry = None
            if entry is None:
                # difensivo: un adapter registrato non deve mai diventare «nessun motore»
                try:
                    entry = engine.fast_entry(AGENT)
                except Exception:
                    entry = None
        if entry is None:
            reason = (
                "Nessun motore creativo attivo/autenticato: progetto deterministico KORA (0 €)."
                if engine is not None
                else "AI Engine non collegato: progetto deterministico KORA (0 €)."
            )
            return self._deterministic(req, inventory, reason, timings, started, proposal=proposal)

        provider = str(entry.get("provider") or "")
        model = str(entry.get("model") or "")
        # «opencode/opencode/hy4-flash-free» → «opencode/hy4-flash-free»
        display_model = model.split("/", 1)[1] if provider and model.startswith(provider + "/") else model
        call_started = time.perf_counter()
        try:
            result = engine.call(
                AGENT,
                self._messages(ctx),
                json_mode=True,
                timeout=max(5.0, deadline_sec),
                deadline=time.time() + max(5.0, deadline_sec),
                attempt_timeout=max(5.0, deadline_sec),
                max_candidates=1,
                brand_context=False,
                provider_override=provider,
                model_override=model,
                production_id=f"reel-design-{uuid.uuid4().hex[:8]}",
                request_label="project-reel",
            )
        except EngineCancelled:
            raise
        except EngineError as exc:
            timings["inference"] = round((time.perf_counter() - call_started) * 1000, 1)
            return self._deterministic(
                req, inventory, f"Motore AI non disponibile ({exc})", timings, started, proposal=proposal
            )
        timings.update(result.timings or {})
        timings["inference"] = round((time.perf_counter() - call_started) * 1000, 1)
        timings["model_latency"] = result.latency_ms

        parse_started = time.perf_counter()
        try:
            payload = self._extract_json(result.text or "")
            timings["json_parsing"] = round((time.perf_counter() - parse_started) * 1000, 1)
        except Exception as exc:
            timings["json_parsing"] = round((time.perf_counter() - parse_started) * 1000, 1)
            timings["json_error"] = f"{exc.__class__.__name__}: {exc}"[:300]
            # un solo ritentativo di riparazione: costa una chiamata, evita un fallback inutile
            repaired = self._repair_json_attempt(
                engine, entry, ctx, result.text or "", deadline_sec=deadline_sec, timings=timings
            )
            if repaired is None:
                return self._deterministic(
                    req,
                    inventory,
                    "Il motore AI ha risposto in un formato non leggibile: progetto deterministico KORA.",
                    timings,
                    started,
                    calls=2,
                    proposal=proposal,
                )
            payload = repaired
            timings["json_repaired"] = True

        validate_started = time.perf_counter()
        try:
            design = self._build_from_payload(
                payload, req, inventory, engine_label=f"ai_engine:{provider}/{display_model}",
                model=display_model, timings=timings, started=started, calls=1,
            )
            timings["validation"] = round((time.perf_counter() - validate_started) * 1000, 1)
            design.timings_ms = {
                **timings,
                "total": round((time.perf_counter() - started) * 1000, 1),
            }
            return design
        except ValueError as exc:
            timings["validation_error"] = str(exc)[:300]
            return self._deterministic(
                req, inventory, f"Schema non valido: {exc}", timings, started, calls=1, proposal=proposal
            )

    # ---------------------------------------------------------------- context
    def _visual_assets(self) -> list[dict[str, Any]]:
        try:
            assets = self.director.vault.list_assets()
        except Exception:
            return []
        return [
            a for a in assets
            if a.get("media_type") in ("image", "video") and a.get("category") != "Logo"
        ]

    def _inventory(self, req: CreativeRequestPayload) -> list[dict[str, Any]]:
        assets = self._visual_assets()
        allowed = {str(a.get("id")) for a in assets}
        if req.asset_ids:
            selected = [a for a in assets if str(a.get("id")) in {str(i) for i in req.asset_ids}]
            return selected or assets
        return assets

    def _context(
        self,
        req: CreativeRequestPayload,
        proposal: Optional[ThemeProposalLite],
        inventory: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            season = self.creative._season_context()
        except Exception:
            season = ""
        try:
            history = [str(h.get("title")) for h in self.creative.anti_rep.get_history(limit=8)]
        except Exception:
            history = []
        profile_version = None
        try:
            profile_version = (self.director.brand_brain.get_current() or {}).get("version")
        except Exception:
            pass
        sample = [
            {
                "id": a.get("id"),
                "categoria": a.get("category"),
                "descrizione": (a.get("analysis") or {}).get("description")
                or (a.get("analysis") or {}).get("caption")
                or a.get("filename"),
            }
            for a in inventory[:MAX_INVENTORY]
        ]
        return {
            "profilo_kora": {"versione": profile_version, "sintesi": PHILOSOPHY},
            "stagionalita": season,
            "temi_gia_usati": history,
            "richiesta": {
                "tema": req.theme.strip(),
                "obiettivo": req.objective.strip(),
                "durata_sec": req.duration_sec,
                "voce": req.voice_id,
                "istruzioni": req.custom_instructions.strip(),
            },
            "proposta_scelta": (
                {
                    "titolo": proposal.title,
                    "hook": proposal.hook,
                    "messaggio": proposal.central_message,
                    "direzione": proposal.narrative_direction,
                }
                if proposal is not None
                else None
            ),
            "scene_target": max(3, min(8, int(round(max(4.0, req.duration_sec - 3.0) / 3.2)))),
            "materiale": {
                "totale_asset_disponibili": len(inventory),
                "campione": sample,
                "inquadrature_ammesse": sorted(FRAMINGS),
                "movimenti_ammessi": [m.value for m in CameraMotion],
                "transizioni_ammesse": [t.value for t in SceneTransition],
            },
        }

    def _messages(self, ctx: dict[str, Any]) -> list[dict[str, Any]]:
        system = (
            "Sei il Direttore Creativo di KORA STUDIO (" + PHILOSOPHY + "). "
            "Nessun tool, nessun file, nessuna analisi immagini. Rispondi SOLO con JSON valido in italiano, "
            "compatto su una riga, senza blocchi di codice ``` e senza testo prima o dopo. "
            "Schema esatto: {\"concept\": {\"title\", \"hook\", \"central_message\", \"narrative_arc\", "
            "\"voiceover_script\", \"visual_mood\", \"objective\", \"primary_emotion\", \"ending\"}, "
            "\"scenes\": [{\"visual_goal\", \"asset_id\", \"framing\", \"camera_motion\", \"transition\", "
            "\"subtitle_text\"}], \"sound_design\": {\"music_mood\", \"texture\", \"mix_note\"}, "
            "\"cover\": {\"title\", \"subtitle\"}, \"caption\": \"testo Instagram\"}. "
            "Il voiceover deve essere definitivo, coerente col tema scelto, massimo 45 parole, "
            "e terminare con la payoff «The Art of Being». "
            "Scegli SOLO asset_id presenti nel campione. Non inventare luoghi o servizi. "
            f"Genera esattamente {ctx['scene_target']} scene visive (l'outro KORA è automatico), "
            "senza indicare durate: la matematica dei frame è deterministica."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)},
        ]

    # ----------------------------------------------------------------- parser
    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        from kora_studio.ai_engine.json_extract import parse_json_object

        return parse_json_object(raw, required_keys={"concept"})

    def _repair_json_attempt(
        self,
        engine: Any,
        entry: dict[str, Any],
        ctx: dict[str, Any],
        raw: str,
        *,
        deadline_sec: float,
        timings: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Seconda chiamata breve: stessa richiesta + risposta precedente da correggere."""
        from kora_studio.ai_engine.base import EngineCancelled, EngineError

        remaining = min(max(8.0, deadline_sec), 30.0)
        messages = self._messages(ctx) + [
            {"role": "assistant", "content": (raw or "")[:4000]},
            {
                "role": "user",
                "content": (
                    "La risposta precedente non è JSON valido o è incompleta. "
                    "Riscrivila per intero come UN SOLO oggetto JSON compatto, senza blocchi di codice, "
                    "senza testo prima o dopo, con tutte le chiavi dello schema."
                ),
            },
        ]
        started = time.perf_counter()
        try:
            result = engine.call(
                AGENT,
                messages,
                json_mode=True,
                timeout=remaining,
                deadline=time.time() + remaining,
                attempt_timeout=remaining,
                max_candidates=1,
                brand_context=False,
                provider_override=str(entry.get("provider") or ""),
                model_override=str(entry.get("model") or ""),
                production_id=f"reel-repair-{uuid.uuid4().hex[:8]}",
                request_label="project-reel-repair",
            )
        except EngineCancelled:
            raise
        except EngineError as exc:
            timings["json_repair_error"] = f"{exc.__class__.__name__}: {exc}"[:200]
            return None
        timings["json_repair_ms"] = round((time.perf_counter() - started) * 1000, 1)
        try:
            return self._extract_json(result.text or "")
        except Exception as exc:
            timings["json_repair_error"] = f"{exc.__class__.__name__}: {exc}"[:200]
            return None

    def _build_from_payload(
        self,
        payload: dict[str, Any],
        req: CreativeRequestPayload,
        inventory: list[dict[str, Any]],
        *,
        engine_label: str,
        model: str,
        timings: dict[str, Any],
        started: float,
        calls: int,
    ) -> ReelDesign:
        concept_raw = payload.get("concept") or {}
        if not isinstance(concept_raw, dict):
            raise ValueError("concept mancante")
        title = str(concept_raw.get("title") or "").strip()
        voiceover = str(concept_raw.get("voiceover_script") or "").strip()
        if not title or not voiceover:
            raise ValueError("title/voiceover_script mancanti")
        words = voiceover.split()
        if len(words) > 70:
            voiceover = " ".join(words[:70]).rstrip(",;:") + "."
        if "art of being" not in voiceover.lower():
            voiceover = voiceover.rstrip(".") + ". KORA. The Art of Being."

        valid_ids = {str(a.get("id")) for a in inventory}
        primary = next(
            (str(a.get("id")) for a in inventory if a.get("category") != "Logo"), None
        )
        concept = CreativeConcept(
            id=f"conc_{uuid.uuid4().hex[:8]}",
            title=title[:90],
            central_idea=str(concept_raw.get("central_message") or title)[:300],
            message=str(concept_raw.get("central_message") or "")[:300],
            primary_emotion=str(concept_raw.get("primary_emotion") or "")[:80] or "serenità",
            hook=str(concept_raw.get("hook") or "")[:200],
            narrative_arc=str(concept_raw.get("narrative_arc") or "")[:600],
            ending=str(concept_raw.get("ending") or "The Art of Being")[:200],
            voiceover_script=voiceover[:1200],
            visual_mood=str(concept_raw.get("visual_mood") or "")[:200],
            moodboard_tags=[],
            required_materials=["fotografie autentiche KORA"],
            available_asset_ids=[str(a.get("id")) for a in inventory][:12] or ([primary] if primary else []),
            missing_materials=[],
            objective=str(concept_raw.get("objective") or req.objective)[:200],
            recommended_voice_id=req.voice_id,
            duration_sec=float(req.duration_sec),
            feasibility_score=0.9,
            originality_score=0.9,
            preset="KORA Cinematic Identity",
            generation_engine=engine_label,
            creative_request=req.model_dump(),
        )
        scenes = self._overlay_scenes(payload.get("scenes"), concept, valid_ids, req)
        self._validate_scenes(scenes, req)
        sound = payload.get("sound_design") or {}
        cover = payload.get("cover") or {}
        caption = str(payload.get("caption") or "").strip()
        sound_design = {
            "music_mood": str(sound.get("music_mood") or "")[:120] or "mediterraneo calmo",
            "texture": str(sound.get("texture") or "")[:120] or "mare, brezza, passi sulla sabbia",
            "mix_note": str(sound.get("mix_note") or "")[:200] or "voce in primo piano, ambiente -18 dB",
        }
        cover_payload = {
            "title": str(cover.get("title") or concept.title)[:70],
            "subtitle": str(cover.get("subtitle") or "The Art of Being")[:70],
        }
        caption = self._clean_caption(caption, concept)
        return ReelDesign(
            concept=concept,
            scenes=scenes,
            caption=caption[:900],
            cover=cover_payload,
            sound_design=sound_design,
            generation_engine=engine_label,
            model=model,
            fallback_reason=None,
            timings_ms={
                **timings,
                "total": round((time.perf_counter() - started) * 1000, 1),
            },
            calls=calls,
        )

    def _overlay_scenes(
        self,
        raw_scenes: Any,
        concept: CreativeConcept,
        valid_ids: set[str],
        req: CreativeRequestPayload,
    ) -> list[StoryboardScene]:
        base = self.creative.build_storyboard(
            concept,
            voice_id=req.voice_id,
            fps=DEFAULT_FPS,
            authorized_asset_ids=req.asset_ids or None,
            annotate_vault=False,  # una sola richiesta creativa: niente analisi multimedia qui
            use_story_director=False,  # la regia è già decisa dall'AI in questa unica risposta
        )
        ai_scenes = raw_scenes if isinstance(raw_scenes, list) else []
        framings = FRAMINGS
        motions = {m.value for m in CameraMotion}
        transitions = {t.value for t in SceneTransition}
        out: list[StoryboardScene] = []
        for idx, scene in enumerate(base):
            if scene.is_outro:
                out.append(scene)
                continue
            choice = ai_scenes[idx % len(ai_scenes)] if ai_scenes else {}
            if not isinstance(choice, dict):
                choice = {}
            data = scene.model_dump()
            goal = str(choice.get("visual_goal") or "").strip()
            if goal:
                data["narrative_goal"] = goal[:160]
            asset_id = str(choice.get("asset_id") or "")
            if asset_id in valid_ids and asset_id != data.get("selected_asset_id"):
                data["selected_asset_id"] = asset_id
                data["visual_rationale"] = "scelta dal Direttore Creativo KORA (asset autorizzato)"
            framing = str(choice.get("framing") or "")
            if framing in framings:
                data["shot_framing"] = framing
            motion = str(choice.get("camera_motion") or "")
            if motion in motions:
                data["camera_motion"] = motion
            transition = str(choice.get("transition") or "")
            if transition in transitions:
                data["transition"] = transition
            subtitle = str(choice.get("subtitle_text") or "").strip()
            if subtitle:
                data["subtitle_text"] = subtitle[:180]
            out.append(StoryboardScene(**data))
        return out

    @staticmethod
    def _validate_scenes(scenes: list[StoryboardScene], req: CreativeRequestPayload) -> None:
        if len(scenes) < 3:
            raise ValueError("storyboard con meno di 3 scene")
        expected = int(round(req.duration_sec * DEFAULT_FPS))
        total = sum(s.duration_frames for s in scenes)
        if total != expected:
            raise ValueError(f"frame totali {total} ≠ attesi {expected}")
        if not any(s.is_outro for s in scenes):
            raise ValueError("outro KORA mancante")

    # ------------------------------------------------------------ deterministic
    # ----------------------------------------------------- direzione per tema
    THEME_DIRECTIONS: dict[str, dict[str, Any]] = {
        "identita": {
            "keywords": (
                "identità", "identita", "brand", "marca", "logo", "atmosfera",
                "architettura", "design", "eleganza", "mood",
            ),
            "suffix": "Luce del Mattino",
            "variants": [
                (
                    "C'è un'ora, davanti al mare, in cui tutto rallenta.",
                    [
                        "La luce accarezza il pergolato e la sabbia.",
                        "Materiali semplici, proporzioni giuste, niente di superfluo.",
                        "L'identità KORA si riconosce da un dettaglio.",
                    ],
                ),
                (
                    "Non serve alzare la voce per farsi riconoscere.",
                    [
                        "Un tavolo in legno, un'ombra misurata, lo stesso bianco di sempre.",
                        "Il mare sullo sfondo non è una scenografia: è il motivo.",
                        "L'eleganza qui è una questione di distanze, non di decori.",
                    ],
                ),
                (
                    "Il lusso, qui, si misura in centimetri di ombra.",
                    [
                        "Il pergolato dosa la luce invece di nasconderla.",
                        "Ogni seduta guarda l'orizzonte da un'angolazione scelta.",
                        "KORA non decora il mare: gli costruisce intorno il silenzio giusto.",
                    ],
                ),
            ],
            "preset": "KORA Cinematic Identity",
            "order": ["Pergolato", "Arredi", "Dettagli", "Mare", "Colazione", "Tramonto", "Aperitivo"],
        },
        "ospitalita": {
            "keywords": (
                "esperienza", "ospitalità", "ospitalita", "convivial", "tavola", "tavolo",
                "aperitivo", "colazione", "persone", "servizio", "ristorante", "food",
            ),
            "suffix": "Ospitalità Misurata",
            "variants": [
                (
                    "Un tavolo apparecchiato, a pochi passi dall'acqua.",
                    [
                        "Il caffè del mattino arriva prima delle parole.",
                        "Un calice che aspetta il tramonto sotto il pergolato.",
                        "Stare insieme, con la cura di sempre.",
                    ],
                ),
                (
                    "Il posto migliore è quello dove arriva l'ombra alle tre.",
                    [
                        "Le sedute sono distanti quanto serve per parlare senza alzare il tono.",
                        "Il bicchiere è già lucido prima che tu ti sieda.",
                        "Accogliere bene è una questione di dettagli che non si vedono.",
                    ],
                ),
                (
                    "L'aperitivo comincia quando il telefono resta in tasca.",
                    [
                        "Le voci si abbassano, il ghiaccio nel bicchiere si scioglie piano.",
                        "La luce scende di un grado e il mare cambia colore una volta sola.",
                        "Nessuno guarda l'ora: è questo il servizio.",
                    ],
                ),
            ],
            "preset": "KORA Experience",
            "order": ["Colazione", "Aperitivo", "Arredi", "Pergolato", "Tramonto", "Mare", "Dettagli"],
        },
        "stagione": {
            "keywords": (
                "stagion", "autunn", "estat", "primaver", "invern", "tramonto",
                "alba", "luce", "tempo", "sole", "settembre", "ottobre",
            ),
            "suffix": "Ritmo di Stagione",
            "variants": [
                (
                    "La stagione cambia, il mare resta.",
                    [
                        "La luce si abbassa sull'orizzonte e il colore si scalda.",
                        "Il pergolato si svuota, l'aria diventa più fresca.",
                        "Ogni periodo dell'anno ha il suo ritmo, al KORA.",
                    ],
                ),
                (
                    "Quando il litorale si fa silenzioso, il mare parla più chiaro.",
                    [
                        "La spiaggia respira fuori dalla fretta di agosto.",
                        "Il legno del tavolo è più freddo, la luce arriva di taglio.",
                        "C'è un modo di stare al mare che non ha bisogno dell'estate.",
                    ],
                ),
                (
                    "Ci sono giorni in cui il colore giusto dura venti minuti.",
                    [
                        "Il sole tocca l'orizzonte e tutto prende la tonalità che serviva.",
                        "Poi la luce se ne va, senza chiedere il permesso a nessuno.",
                        "Chi era seduto qui l'ha vista: gli altri l'hanno persa.",
                    ],
                ),
            ],
            "preset": "KORA Editorial",
            "order": ["Tramonto", "Mare", "Pergolato", "Dettagli", "Colazione", "Arredi", "Aperitivo"],
        },
    }

    def _theme_direction(self, req: CreativeRequestPayload) -> dict[str, Any]:
        """Direzione deterministica coerente col tema: nessun reel identico per temi diversi."""
        text = " ".join(
            [req.theme or "", req.objective or "", req.custom_instructions or ""]
        ).lower()
        best_key, best_score = "identita", 0
        for key, spec in self.THEME_DIRECTIONS.items():
            score = sum(1 for kw in spec["keywords"] if kw in text)
            if score > best_score:
                best_key, best_score = key, score
        direction = dict(self.THEME_DIRECTIONS[best_key])
        direction["key"] = best_key
        variants = direction.get("variants") or []
        if variants:
            recent: list[str] = []
            try:
                recent = [str(x) for x in (self.director.db.get_preference("reel_script_variants", []) or [])]
            except Exception:
                recent = []
            seed = int(
                hashlib.md5(f"{best_key}|{text}|{len(recent)}".encode("utf-8")).hexdigest(),
                16,
            )
            index = seed % len(variants)
            for candidate in range(len(variants)):
                probe = (index + candidate) % len(variants)
                if f"{best_key}:{probe}" not in recent:
                    index = probe
                    break
            hook, lines = variants[index]
            direction["hook"] = hook
            direction["lines"] = list(lines)
            direction["variant"] = f"{best_key}:{index}"
        return direction

    def _order_inventory(
        self, inventory: list[dict[str, Any]], order: list[str], slots: int
    ) -> list[str]:
        """Ordina gli asset autentici secondo l'arco narrativo del tema (categorie reali)."""
        ranked: list[dict[str, Any]] = []
        for category in order:
            ranked.extend(a for a in inventory if str(a.get("category")) == category)
        ranked.extend(a for a in inventory if a not in ranked)
        seen: set[str] = set()
        ids: list[str] = []
        for asset in ranked:
            aid = str(asset.get("id"))
            if aid and aid not in seen:
                seen.add(aid)
                ids.append(aid)
        return ids[: max(3, slots)]

    def _apply_theme_direction(
        self, concept: CreativeConcept, req: CreativeRequestPayload, inventory: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Sostituisce la narrazione generica con una direzione derivata dal tema reale."""
        direction = self._theme_direction(req)
        slots = min(
            12,
            max(5, int(round(float(req.duration_sec or 20.0) / (2.8 if direction["key"] != "stagione" else 2.4)))),
        )
        concept.available_asset_ids = self._order_inventory(inventory, direction["order"], slots)
        clean_theme = (req.theme or "").strip().rstrip(".")
        if clean_theme:
            concept.title = f"{clean_theme[:34]} — {direction['suffix']}"[:90]
        concept.hook = direction["hook"]
        body = direction["lines"]
        concept.message = " ".join(body)
        concept.central_idea = (
            f"{req.theme.strip() or 'KORA'} — {direction['suffix']}: "
            f"{body[0].rstrip('.')}"
        )[:300]
        concept.narrative_arc = " → ".join([direction["hook"], *body, "KORA. The Art of Being."])
        concept.voiceover_script = " ".join([direction["hook"], *body, "KORA. The Art of Being."])
        concept.preset = direction["preset"]
        concept.duration_sec = float(req.duration_sec)  # durata vincolante (25 fps)
        return direction

    def _deterministic(
        self,
        req: CreativeRequestPayload,
        inventory: list[dict[str, Any]],
        reason: str,
        timings: dict[str, Any],
        started: float,
        *,
        calls: int = 0,
        proposal: Optional[ThemeProposalLite] = None,
    ) -> ReelDesign:
        try:
            concept = self.creative._build_concept_for_theme(
                req.theme or req.objective or "KORA · The Art of Being",
                angle_idx=0,
                duration_sec=req.duration_sec,
                preferred_asset_ids=req.asset_ids or None,
            )
        except Exception:
            concept = CreativeConcept(
                id=f"conc_{uuid.uuid4().hex[:8]}",
                title=req.theme or "KORA · The Art of Being",
                central_idea="Il tempo che si ferma al KORA",
                message="Luce mediterranea e ospitalità autentica",
                primary_emotion="serenità",
                hook="C'è un'ora, davanti al mare, in cui tutto rallenta.",
                narrative_arc="Apertura ampia, dettagli reali, chiusura sul mare.",
                ending="The Art of Being",
                voiceover_script=(
                    "C'è un'ora, davanti al mare, in cui tutto rallenta. "
                    "La luce accarezza il pergolato, il caffè ha il sapore del mattino. "
                    "KORA. The Art of Being."
                ),
                visual_mood="luce naturale, calma mediterranea",
                required_materials=["fotografie autentiche KORA"],
                available_asset_ids=[str(a.get("id")) for a in inventory][:8],
                missing_materials=[],
                objective=req.objective,
                recommended_voice_id=req.voice_id,
                duration_sec=req.duration_sec,
                generation_engine="deterministic_offline_fallback",
                creative_request=req.model_dump(),
            )
        # Direzione derivata dal tema reale (nessun reel identico per temi diversi)
        direction = self._apply_theme_direction(concept, req, inventory)
        try:
            key = str(direction.get("variant") or "")
            if key:
                recent = [str(x) for x in (self.director.db.get_preference("reel_script_variants", []) or [])]
                self.director.db.set_preference("reel_script_variants", ([key] + recent)[:12])
        except Exception:
            pass
        body_lines = list(direction["lines"])
        opening = direction["hook"]
        if proposal is not None:
            # il tema scelto dall'utente resta il titolo del progetto
            concept.title = (proposal.title or concept.title)[:90]
            if proposal.hook:
                opening = proposal.hook[:200]
            if proposal.recommended_objective:
                concept.objective = proposal.recommended_objective
            if proposal.asset_ids:
                authorised = {str(a.get("id")) for a in inventory}
                keep = [str(i) for i in proposal.asset_ids if str(i) in authorised][:3]
                if keep:
                    concept.available_asset_ids = keep
        concept.hook = opening
        concept.narrative_arc = " → ".join([opening, *body_lines, "KORA. The Art of Being."])
        concept.voiceover_script = " ".join([opening, *body_lines, "KORA. The Art of Being."])
        concept.recommended_voice_id = req.voice_id
        concept.duration_sec = float(req.duration_sec)
        concept.objective = concept.objective or req.objective
        concept.generation_engine = "deterministic_offline_fallback"
        scenes = self.creative.build_storyboard(
            concept,
            voice_id=req.voice_id,
            fps=DEFAULT_FPS,
            authorized_asset_ids=req.asset_ids or None,
            annotate_vault=False,
            use_story_director=False,
        )
        self._validate_scenes(scenes, req)
        caption = self._clean_caption("", concept)
        return ReelDesign(
            concept=concept,
            scenes=scenes,
            caption=caption[:900],
            cover={"title": concept.title[:70], "subtitle": "The Art of Being"},
            sound_design={
                "music_mood": "mediterraneo calmo",
                "texture": "mare, brezza, passi sulla sabbia",
                "mix_note": "voce in primo piano, ambiente -18 dB",
            },
            generation_engine="deterministic_offline_fallback",
            model="kora-deterministic",
            fallback_reason=reason[:500],
            timings_ms={
                **timings,
                "total": round((time.perf_counter() - started) * 1000, 1),
            },
            calls=calls,
        )

    @staticmethod
    def _clean_caption(caption: str, concept: CreativeConcept) -> str:
        """Caption KORA senza ripetizioni: hook e messaggio identici non si duplicano."""
        body = (caption or "").strip()
        if not body:
            parts: list[str] = []
            for text in (concept.hook, concept.message):
                text = (text or "").strip()
                if not text or any(text == p or text in p for p in parts):
                    continue
                # se il nuovo testo contiene il precedente, tengo solo il più completo
                parts = [p for p in parts if p not in text]
                parts.append(text)
            body = " ".join(parts).strip()
        footer = "KORA Beach Club · Policoro · The Art of Being\n#KORA #TheArtOfBeing #Policoro"
        if "KORA Beach Club" not in body:
            body = f"{body}\n\n{footer}".strip()
        return body[:900]

    @staticmethod
    def log_deterministic(director: Any, timings: dict[str, Any], reason: str) -> None:
        """Il progetto deterministico compare nel Monitor AI come chiamata a 0 €."""
        engine = getattr(director, "ai_engine", None)
        log = getattr(engine, "_log", None) if engine is not None else None
        if log is None:
            return
        try:
            import datetime as _dt

            log(
                None,
                AGENT,
                {"provider": "deterministic", "model": "kora-deterministic", "billing": BILLING_LOCAL, "execution": EXEC_LOCAL},
                _dt.datetime.now(_dt.timezone.utc).isoformat(),
                int(time.time() * 1000) - int(timings.get("total") or 0),
                outcome="ok",
                attempts=0,
                fallback_used=True,
                fallback_from="ai_engine",
                cost=0.0,
                cost_basis="local_compute",
                timings=timings,
                request_label=f"project-reel:deterministic:{reason[:70]}",
            )
        except Exception:
            pass
