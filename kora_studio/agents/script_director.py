"""Script Director (v3.5) — rigenerazione del copione a quattro livelli.

1. singola frase  2. singola scena  3. gruppo di scene  4. intero copione
Più hook, apertura, chiusura, caption Instagram e call to action indipendenti.

Regole: una sola chiamata AI strutturata per gruppi/intero copione; nessuna
proposta viene applicata senza «Applica»; le scene bloccate non si toccano;
durata, fotografie e regia restano intatte.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from kora_studio.ai_engine.json_extract import JsonExtractionError, parse_json_object
from kora_studio.db.models import StoryboardScene

SCOPES = ("sentence", "scene", "group", "full", "hook", "opening", "closing", "caption", "cta")

SCOPE_LABELS = {
    "sentence": "Singola frase",
    "scene": "Singola scena",
    "group": "Gruppo di scene",
    "full": "Intero copione",
    "hook": "Hook",
    "opening": "Apertura narrativa",
    "closing": "Chiusura narrativa",
    "caption": "Caption Instagram",
    "cta": "Call to action",
}

MAX_SCENE_WORDS = 22


def _clean_line(text: Any) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip().strip('"').strip("«»")
    return value[:220]


class ScriptDirector:
    """Proposte di testo con confronto prima/dopo e applicazione esplicita."""

    def __init__(self, director: Any) -> None:
        self.director = director

    def _context(self, proj: dict[str, Any], scenes: list[StoryboardScene]) -> dict[str, Any]:
        return {
            "titolo": proj.get("title"),
            "tema": proj.get("theme") or proj.get("title"),
            "obiettivo": (proj.get("concept") or {}).get("objective") or "",
            "messaggio": (proj.get("concept") or {}).get("message") or "",
            "voce": proj.get("voice_id"),
            "durata_sec": round(sum(s.duration_sec for s in scenes), 2),
            "fps": proj.get("fps") or 25,
            "scene": [
                {
                    "n": s.scene_number,
                    "uid": s.scene_uid,
                    "testo": s.spoken_text,
                    "durata": round(s.duration_sec, 2),
                    "materiale": (self.director.vault.get_asset(s.selected_asset_id) or {}).get("category")
                    if s.selected_asset_id
                    else None,
                }
                for s in scenes
            ],
        }

    def _voice_locked(self, scene: StoryboardScene) -> bool:
        locks = scene.locks or {}
        return bool(locks.get("voice")) or bool(locks.get("scene"))

    def _targets(
        self, scenes: list[StoryboardScene], scope: str, scene_numbers: list[int]
    ) -> tuple[list[StoryboardScene], list[int]]:
        editable = [s for s in scenes if not s.is_outro]
        if scope in ("sentence", "scene"):
            if not scene_numbers:
                raise ValueError("Indica il numero della scena da riscrivere.")
            picked = [s for s in editable if s.scene_number in scene_numbers[:1]]
        elif scope == "group":
            if not scene_numbers:
                raise ValueError("Indica almeno una scena per la rigenerazione di gruppo.")
            picked = [s for s in editable if s.scene_number in scene_numbers]
        elif scope == "full":
            picked = editable
        elif scope == "hook":
            picked = editable[:1]
        elif scope == "opening":
            picked = editable[:2]
        elif scope == "closing":
            picked = editable[-1:] if editable else []
        else:
            picked = []
        locked = [s.scene_number for s in picked if self._voice_locked(s)]
        picked = [s for s in picked if not self._voice_locked(s)]
        return picked, locked

    def propose(
        self,
        project_id: str,
        scope: str,
        scene_numbers: Optional[list[int]] = None,
        instruction: str = "",
    ) -> dict[str, Any]:
        if scope not in SCOPES:
            raise ValueError(f"Livello non riconosciuto. Disponibili: {', '.join(SCOPES)}")
        proj, scenes = self.director._project_scenes(project_id)
        numbers = [int(n) for n in (scene_numbers or [])]
        targets, locked = self._targets(scenes, scope, numbers)

        if scope in ("caption", "cta"):
            return self._propose_project_copy(project_id, proj, scope, instruction)

        if not targets:
            return {
                "ok": False,
                "applied": False,
                "blocked": True,
                "scope": scope,
                "locked_scenes": locked,
                "reason": "Nessuna scena modificabile: le scene coinvolte sono bloccate o l'outro è protetto.",
            }

        context = self._context(proj, scenes)
        context["istruzione"] = instruction.strip()
        context["bersagli"] = [s.scene_number for s in targets]
        lines: dict[int, str] = {}
        source = "deterministic_offline_fallback"
        note = ""
        engine_error = ""

        engine = getattr(self.director, "ai_engine", None)
        if engine is not None:
            try:
                lines = self._engine_lines(engine, scope, targets, context, instruction)
                if lines:
                    source = "engine:ai"
            except Exception as exc:
                engine_error = f"{type(exc).__name__}"

        if not lines:
            for scene in targets:
                lines[scene.scene_number] = self._fallback_for(scene, proj, scenes, instruction)
            note = (
                f"Motore AI non utilizzabile ({engine_error or 'non collegato'}): proposta locale dichiarata."
                if engine is not None
                else "Nessun motore AI collegato: proposta locale."
            )

        proposals: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for scene in targets:
            after = _clean_line(lines.get(scene.scene_number, ""))
            if not after:
                continue
            guard = self.director.guardian.validate_copy(after)
            if not guard.get("approved"):
                rejected.append(
                    {
                        "scene_number": scene.scene_number,
                        "text": after,
                        "violations": guard.get("violations") or [],
                    }
                )
                continue
            proposals.append(
                {
                    "scene_number": scene.scene_number,
                    "scene_uid": scene.scene_uid,
                    "before": scene.spoken_text,
                    "after": after,
                    "source": source,
                    "changed": after.strip() != (scene.spoken_text or "").strip(),
                }
            )

        return {
            "ok": bool(proposals),
            "applied": False,
            "scope": scope,
            "scope_label": SCOPE_LABELS[scope],
            "instruction": instruction.strip(),
            "proposals": proposals,
            "rejected": rejected,
            "locked_scenes": locked,
            "source": source,
            "calls": 1 if source.startswith("engine") else 0,
            "note": note or ("Proposta pronta: nulla è stato applicato finché non premi «Applica»."),
            "duration_frames": sum(s.duration_frames for s in scenes),
            "visuals_untouched": True,
            "direction_untouched": True,
        }

    def _engine_lines(
        self,
        engine: Any,
        scope: str,
        targets: list[StoryboardScene],
        context: dict[str, Any],
        instruction: str,
    ) -> dict[int, str]:
        system = (
            "Sei il copywriter di KORA, ospitalità mediterranea a Policoro (Italia). "
            "Filosofia: lusso discreto, autenticità, niente aggettivi pubblicitari, niente prezzi, niente emoji. "
            "Scrivi voice-over in italiano, una riga per scena, 8-16 parole, concreta e sensoriale, "
            "come una frase detta e non uno slogan. L'ultima riga del Reel è il payoff ufficiale e non si tocca. "
            "Rispetta il tema, la durata e il numero di scene indicati. "
            'Rispondi SOLO con JSON compatto su una riga: {"lines":[{"n":1,"text":"..."}]}'
        )
        user = json.dumps(context, ensure_ascii=False)
        if instruction.strip():
            user += f"\n\nIstruzione dell'utente da rispettare: {instruction.strip()}"
        result = engine.call(
            "copywriter",
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            json_mode=True,
            timeout=60.0,
            request_label=f"script-{scope}",
        )
        data = parse_json_object(getattr(result, "text", "") or "", required_keys={"lines"})
        rows = data.get("lines") if isinstance(data, dict) else None
        if isinstance(rows, dict):
            rows = [{"n": k, "text": v} for k, v in rows.items()]
        if not isinstance(rows, list):
            raise JsonExtractionError("campo lines assente o non lista")
        allowed = {s.scene_number for s in targets}
        parsed: dict[int, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                number = int(row.get("n") or row.get("scene_number") or 0)
            except (TypeError, ValueError):
                continue
            text = _clean_line(row.get("text") or row.get("line") or "")
            if number in allowed and text and len(text.split()) <= MAX_SCENE_WORDS:
                parsed[number] = text
        if not parsed:
            raise JsonExtractionError("nessuna riga valida nel JSON del motore")
        return parsed

    def _fallback_for(
        self,
        scene: StoryboardScene,
        proj: dict[str, Any],
        scenes: list[StoryboardScene],
        instruction: str,
    ) -> str:
        asset = self.director.vault.get_asset(scene.selected_asset_id) if scene.selected_asset_id else None
        ctx = {
            "titolo": proj.get("title"),
            "tema": proj.get("theme") or proj.get("title"),
            "posizione": scene.scene_number,
            "totale_scene": len(scenes),
            "materiale": (asset or {}).get("category") or (asset or {}).get("filename") or "materiale KORA",
            "istruzione": instruction,
            "testo_attuale": scene.spoken_text,
        }
        return self.director.scene_editor._fallback_line(ctx)

    def _propose_project_copy(
        self, project_id: str, proj: dict[str, Any], scope: str, instruction: str
    ) -> dict[str, Any]:
        concept = proj.get("concept") or {}
        prefs = proj.get("preferences") or {}
        before = (
            prefs.get("instagram_caption") if scope == "caption" else prefs.get("call_to_action")
        ) or (concept.get("caption") if scope == "caption" else concept.get("cta")) or ""

        proposal = ""
        source = "deterministic_offline_fallback"
        engine = getattr(self.director, "ai_engine", None)
        if engine is not None:
            try:
                system = (
                    "Sei il copywriter social di KORA (ospitalità mediterranea, Policoro). "
                    + (
                        "Scrivi una caption Instagram di 1-2 righe, concreta, senza emoji pubblicitarie, "
                        "massimo 220 caratteri, coerente con il tema."
                        if scope == "caption"
                        else "Scrivi una call to action breve (massimo 60 caratteri), discreta, senza urgenza finta."
                    )
                    + ' Rispondi solo con JSON: {"text":"..."}'
                )
                user = json.dumps(
                    {
                        "tema": proj.get("theme"),
                        "titolo": proj.get("title"),
                        "messaggio": concept.get("message"),
                        "testo_attuale": before,
                        "istruzione": instruction,
                    },
                    ensure_ascii=False,
                )
                result = engine.call(
                    "copywriter",
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    json_mode=True,
                    timeout=45.0,
                    request_label=f"script-{scope}",
                )
                data = parse_json_object(getattr(result, "text", "") or "", required_keys={"text"})
                proposal = _clean_line(data.get("text"))
                source = "engine:ai"
            except Exception:
                proposal = ""

        if not proposal:
            if scope == "caption":
                theme = str(proj.get("theme") or proj.get("title") or "KORA")
                proposal = f"{theme}. Ospitalità mediterranea a Policoro, senza fretta."
            else:
                proposal = "Scrivici quando vuoi tornare."

        guard = self.director.guardian.validate_copy(proposal)
        if not guard.get("approved"):
            return {
                "ok": False,
                "applied": False,
                "blocked": True,
                "scope": scope,
                "reason": "Proposta scartata dal Brand Guardian: "
                + "; ".join(guard.get("violations") or []),
                "proposals": [],
            }
        return {
            "ok": True,
            "applied": False,
            "scope": scope,
            "scope_label": SCOPE_LABELS[scope],
            "proposals": [
                {
                    "scene_number": None,
                    "scene_uid": None,
                    "before": before,
                    "after": proposal,
                    "source": source,
                    "changed": proposal.strip() != str(before).strip(),
                }
            ],
            "locked_scenes": [],
            "source": source,
            "note": "Proposta pronta: nessuna caption è stata salvata finché non premi «Applica».",
            "requires_render": False,
        }

    def apply(self, project_id: str, proposals: list[dict[str, Any]], scope: str = "group") -> dict[str, Any]:
        """Applica solo ciò che l'utente ha confermato. Audio invalidato, visual no."""
        proj, scenes = self.director._project_scenes(project_id)
        by_number = {s.scene_number: s for s in scenes}
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for item in proposals:
            number = item.get("scene_number")
            text = _clean_line(item.get("after") or item.get("text") or "")
            if not text:
                continue
            if number is None:
                key = "instagram_caption" if scope == "caption" else "call_to_action"
                prefs = dict(proj.get("preferences") or {})
                prefs[key] = text
                with self.director.db.session() as conn:
                    conn.execute(
                        "UPDATE projects SET preferences_json = ? WHERE id = ?",
                        (json.dumps(prefs, ensure_ascii=False), project_id),
                    )
                applied.append({"scene_number": None, "field": key, "text": text, "requires_render": False})
                continue
            scene = by_number.get(int(number))
            if scene is None or scene.is_outro:
                skipped.append({"scene_number": number, "reason": "scena assente o outro protetto"})
                continue
            if self._voice_locked(scene):
                skipped.append({"scene_number": number, "reason": "voiceover bloccato su questa scena"})
                continue
            guard = self.director.guardian.validate_copy(text)
            if not guard.get("approved"):
                skipped.append(
                    {
                        "scene_number": number,
                        "reason": "testo non conforme: " + "; ".join(guard.get("violations") or []),
                    }
                )
                continue
            before = scene.spoken_text
            scene.spoken_text = text
            scene.subtitle_text = text
            scene.preview_status = "pending"
            scene.approval_status = "modificata"
            applied.append({"scene_number": number, "before": before, "text": text, "requires_render": True})

        if not applied:
            return {"ok": False, "applied": False, "reason": "Nessuna proposta applicabile.", "skipped": skipped}

        text_changed = any(a.get("requires_render") for a in applied)
        if text_changed:
            from kora_studio.agents.scene_editor import _renumber_safe

            order = _renumber_safe(scenes)
            self.director._persist_scenes(project_id, order, f"Copione aggiornato ({SCOPE_LABELS.get(scope, scope)})")
            self.director.invalidate_preview(project_id)
            self.director.invalidate_audio(project_id, reason="copione modificato")
        return {
            "ok": True,
            "applied": True,
            "scope": scope,
            "applied_items": applied,
            "skipped": skipped,
            "audio_invalidated": text_changed,
            "visuals_untouched": True,
            "duration_frames": sum(s.duration_frames for s in scenes),
        }
