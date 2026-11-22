"""Regia conversazionale (v3.5).

Un solo campo: «Come vuoi modificare il Reel?». La frase viene tradotta in
modifiche strutturate, mostrate all'utente *prima* di applicarle. Non avvia mai
la produzione completa: tocca solo la scena o la proprietà indicata.

Interpretazione: prima il motore AI (se collegato e disponibile), poi un parser
italiano deterministico. Le proposte sono sempre dichiarate nella loro fonte.
"""

from __future__ import annotations

import json
import re
from typing import Any

from kora_studio.agents.caption_designer import PRESETS
from kora_studio.ai_engine.json_extract import parse_json_object

EASING_WORDS = {
    "lineare": "linear",
    "linear": "linear",
    "lenta": "ease_in_out",
    "lento": "ease_in_out",
    "morbida": "ease_in_out",
    "morbido": "ease_in_out",
    "decelera": "ease_out",
    "frena": "ease_out",
    "accelera": "ease_in",
}

FRAMING_WORDS = {
    "primissimo piano": "primissimo_piano",
    "primo piano": "primo_piano",
    "piano medio": "piano_medio",
    "campo lungo": "campo_lungo",
    "dettaglio": "dettaglio",
    "larga": "campo_lungo",
    "larghe": "campo_lungo",
}

MOTION_WORDS = {
    "ferma": "none",
    "statica": "none",
    "statico": "none",
    "lentissimo": "slow_zoom",
    "lenta": "slow_zoom",
    "lento": "slow_zoom",
    "panoramica": "pan_left",
    "pan": "pan_left",
    "carrello": "dolly_in",
    "avvicina": "slow_push_in",
    "avvicinamento": "slow_push_in",
    "allontana": "pull_back",
    "drone": "drone_reveal",
}

PRESET_WORDS = {
    "editorial": "kora_editorial",
    "editoriale": "kora_editorial",
    "luxury": "cinematic_luxury",
    "minimal": "minimal",
    "serif": "luxury_serif",
    "kinetic": "kinetic_typography",
    "cinetica": "kinetic_typography",
    "dissolvenza": "elegant_dissolve",
    "nessun sottotitolo": "none",
}

_NUM = r"(\d+(?:[.,]\d+)?)"


def _sec(value: str) -> float:
    return float(value.replace(",", "."))


class ArtDirector:
    """Traduce una frase in un elenco di modifiche verificabili."""

    def __init__(self, director: Any) -> None:
        self.director = director

    # ------------------------------------------------------------- contesto
    def _snapshot(self, project_id: str) -> tuple[dict[str, Any], list[Any]]:
        return self.director._project_scenes(project_id)

    def _scene(self, scenes: list[Any], number: int) -> Any:
        return next((s for s in scenes if s.scene_number == int(number)), None)

    # ------------------------------------------------------------ proposte
    def propose(self, project_id: str, instruction: str) -> dict[str, Any]:
        text = " ".join(str(instruction or "").split())
        if not text:
            raise ValueError("Scrivi che cosa vuoi cambiare nel Reel.")

        proj, scenes = self._snapshot(project_id)
        changes: list[dict[str, Any]] = []
        source = "parser_locale"

        ai_changes = self._ai_changes(project_id, proj, scenes, text)
        if ai_changes is not None:
            changes, source = ai_changes, "motore_ai"
        else:
            changes = self._parse(proj, scenes, text)

        notes = []
        if not changes:
            notes.append(
                "Non ho trovato una modifica applicabile in questa frase. "
                "Prova a indicare la scena e il dettaglio, per esempio «scena 3 più lenta»."
            )

        blocked = [c for c in changes if c.get("blocked")]
        return {
            "ok": bool(changes) and not blocked,
            "instruction": text,
            "source": source,
            "changes": changes,
            "blocked": [c for c in blocked],
            "notes": notes,
            "applied": False,
            "starts_production": False,
            "scene_count": len(scenes),
            "duration_frames": sum(s.duration_frames for s in scenes),
        }

    def apply(self, project_id: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
        """Applica le singole modifiche confermate, una per una."""
        _, scenes = self._snapshot(project_id)
        applied: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for change in changes:
            kind = change.get("kind")
            number = change.get("scene_number")
            try:
                if kind == "scene_patch":
                    result = self.director.scene_editor.update_scene(
                        project_id, int(number), change.get("patch") or {}
                    )
                elif kind == "caption_preset":
                    result = self.director.caption_designer.apply(
                        project_id,
                        str(change.get("preset")),
                        change.get("overrides") or {},
                        scene_number=number,
                    )
                elif kind == "lock":
                    result = self.director.scene_editor.set_locks(
                        project_id, int(number), change.get("locks") or {}
                    )
                elif kind == "asset_swap":
                    result = self.director.scene_editor.update_scene(
                        project_id, int(number), {"asset_id": change.get("asset_id")}
                    )
                else:
                    raise ValueError(f"Modifica «{kind}» non gestita")
            except Exception as exc:  # pragma: no cover - difensivo
                failed.append({"change": change, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if result.get("blocked"):
                failed.append({"change": change, "error": result.get("reason") or "bloccata"})
            else:
                applied.append(change)

        return {
            "ok": bool(applied) and not failed,
            "applied": applied,
            "failed": failed,
            "starts_production": False,
            "audio_invalidated": any(
                c.get("kind") == "scene_patch" and {"spoken_text", "duration_sec"} & set((c.get("patch") or {}))
                for c in applied
            ),
        }

    # --------------------------------------------------------------- AI
    def _ai_changes(
        self, project_id: str, proj: dict[str, Any], scenes: list[Any], text: str
    ) -> list[dict[str, Any]] | None:
        engine = getattr(self.director, "ai_engine", None)
        if engine is None:
            return None
        payload = {
            "richiesta": text,
            "scene": [
                {
                    "n": s.scene_number,
                    "testo": s.spoken_text,
                    "durata": round(s.duration_sec, 2),
                    "inquadratura": s.shot_framing,
                    "movimento": s.camera_motion,
                    "outro": s.is_outro,
                }
                for s in scenes
            ],
        }
        system = (
            "Sei il regista di KORA. Traduci la richiesta dell'utente in modifiche minime e concrete. "
            "Campi ammessi: kind=scene_patch con patch {spoken_text|duration_sec|shot_framing|motion_intensity"
            "|motion_easing|camera_motion}; kind=caption_preset con preset; kind=lock con locks. "
            "L'outro (outro=true) non si modifica mai. Non inventare scene. "
            'Rispondi solo con JSON: {"changes":[{"kind":"scene_patch","scene_number":2,'
            '"patch":{"motion_intensity":0.8},"why":"..."}]}'
        )
        try:
            result = engine.call(
                "director",
                [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                json_mode=True,
                timeout=45.0,
                request_label="art-direction",
            )
            data = parse_json_object(getattr(result, "text", "") or "", required_keys={"changes"})
        except Exception:
            return None
        rows = data.get("changes") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return None
        clean: list[dict[str, Any]] = []
        for row in rows[:8]:
            if not isinstance(row, dict) or row.get("kind") not in ("scene_patch", "caption_preset", "lock"):
                continue
            number = row.get("scene_number")
            if number is not None:
                scene = self._scene(scenes, int(number))
                if scene is None or scene.is_outro:
                    clean.append(
                        {
                            "kind": "blocked",
                            "blocked": True,
                            "scene_number": number,
                            "reason": "L'outro o una scena inesistente non si modifica.",
                        }
                    )
                    continue
            clean.append({**row, "source": "motore_ai"})
        return clean or None

    # ------------------------------------------------------------ parser
    def _targets(self, scenes: list[Any], text: str) -> list[Any]:
        low = text.lower()
        numbers = [int(n) for n in re.findall(r"scena\s+(\d+)", low)]
        if numbers:
            picked = [self._scene(scenes, n) for n in numbers]
            return [s for s in picked if s is not None and not s.is_outro]
        if re.search(r"\b(ultim[ao]|outro|chiusura|logo)\b", low):
            return []
        if re.search(r"\b(tutte|tutto il reel|intero reel|ogni scena)\b", low):
            return [s for s in scenes if not s.is_outro]
        return [s for s in scenes if not s.is_outro][:1]

    def _parse(self, proj: dict[str, Any], scenes: list[Any], text: str) -> list[dict[str, Any]]:
        low = text.lower()
        changes: list[dict[str, Any]] = []
        targets = self._targets(scenes, low)

        if targets and re.search(r"\b(più lenta|piu lenta|rallenta|più veloce|piu veloce|accelera)\b", low):
            faster = bool(re.search(r"\b(più veloce|piu veloce|accelera)\b", low))
            for scene in targets:
                value = max(0.5, scene.motion_speed - 0.2) if faster else min(2.0, scene.motion_speed + 0.2)
                changes.append(
                    {
                        "kind": "scene_patch",
                        "scene_number": scene.scene_number,
                        "patch": {"motion_speed": round(value, 2)},
                        "why": "Velocità del movimento aggiornata",
                    }
                )

        if targets:
            for word, framing in FRAMING_WORDS.items():
                if word in low:
                    for scene in targets:
                        changes.append(
                            {
                                "kind": "scene_patch",
                                "scene_number": scene.scene_number,
                                "patch": {"shot_framing": framing},
                                "why": f"Inquadratura → {framing}",
                            }
                        )
                    break

        if targets:
            for word, motion in MOTION_WORDS.items():
                if re.search(rf"\b{re.escape(word)}\b", low):
                    for scene in targets:
                        changes.append(
                            {
                                "kind": "scene_patch",
                                "scene_number": scene.scene_number,
                                "patch": {"camera_motion": motion},
                                "why": f"Movimento → {motion}",
                            }
                        )
                    break

        if targets:
            for word, easing in EASING_WORDS.items():
                if re.search(rf"\b{re.escape(word)}\b", low):
                    for scene in targets:
                        changes.append(
                            {
                                "kind": "scene_patch",
                                "scene_number": scene.scene_number,
                                "patch": {"motion_easing": easing},
                                "why": f"Curva → {easing}",
                            }
                        )
                    break

        match = re.search(rf"scena\s+(\d+)\D+?{_NUM}\s*(?:secondi|sec|s\b)", low)
        if not match:
            match = re.search(rf"{_NUM}\s*(?:secondi|sec)\b", low)
        if match and targets:
            if len(match.groups()) == 2:
                number, value = int(match.group(1)), _sec(match.group(2))
                scene = self._scene(scenes, number)
                targeted = [scene] if scene is not None and not scene.is_outro else []
            else:
                value = _sec(match.group(1))
                targeted = targets
            for scene in targeted:
                changes.append(
                    {
                        "kind": "scene_patch",
                        "scene_number": scene.scene_number,
                        "patch": {"duration_sec": round(value, 2)},
                        "why": f"Durata scena → {value}s",
                    }
                )

        match = re.search(r"intensit[aà]\s*(?:a|al|del|della)?\s*" + _NUM, low)
        if not match:
            match = re.search(r"(?:movimento|moto)\s*(?:più|piu)?\s*(?:forte|intenso|delicato|leggero)", low)
        if not match:
            match = re.search(r"\b(più|piu)\s+(?:intens[ao]|marcat[ao]|decis[ao]|delicat[ao]|morbido)\b", low)
        if match and targets:
            if match.groups() and match.group(1) and match.group(1)[0].isdigit():
                value = min(2.0, max(0.2, _sec(match.group(1))))
            elif match.group(1) and match.group(1).startswith("pi"):
                value = 0.6 if re.search(r"delicat|morbido", low) else 1.6
            else:
                value = 1.6 if re.search(r"forte|intenso", low) else 0.6
            for scene in targets:
                changes.append(
                    {
                        "kind": "scene_patch",
                        "scene_number": scene.scene_number,
                        "patch": {"motion_intensity": round(value, 2)},
                        "why": f"Intensità → {value}",
                    }
                )

        for word, preset in PRESET_WORDS.items():
            if word in low:
                changes.append(
                    {
                        "kind": "caption_preset",
                        "scene_number": None,
                        "preset": preset,
                        "overrides": {},
                        "why": f"Stile sottotitoli → {PRESETS[preset]['label']}",
                    }
                )
                break

        if targets and re.search(r"\b(blocca|bloccare|blocca la scena|non toccare)\b", low):
            for scene in targets:
                changes.append(
                    {
                        "kind": "lock",
                        "scene_number": scene.scene_number,
                        "locks": {"scene": True},
                        "why": "Scena bloccata",
                    }
                )

        if targets and re.search(r"\b(più buio|piu buio|scura|scuro|calda|caldo|luminosa)\b", low):
            changes.append(
                {
                    "kind": "blocked",
                    "blocked": True,
                    "scene_number": None,
                    "reason": "La correzione colore richiede il pannello Visual Director (crop e fuoco), non ancora collegato a questa frase.",
                }
            )

        return changes
