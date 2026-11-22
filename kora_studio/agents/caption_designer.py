"""Caption Designer (v3.5) — sottotitoli e testi grafici.

Tre elementi distinti: sottotitoli del voiceover (derivati dalla fonte narrativa
ufficiale), titoli grafici interni al video (non pronunciati), caption Instagram
(fuori dal video, nessun rendering).

Nessun runtime nuovo: si riusano `hyperframes_captions.py` e il compositing
FFmpeg già presenti. I preset sono leggeri e mappano sugli stili esistenti.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from typing import Any, Optional

PRESETS: dict[str, dict[str, Any]] = {
    "kora_editorial": {
        "label": "KORA Editorial",
        "style": "serif_ivory",
        "description": "Playfair avorio, scrim morbido, tono editoriale.",
        "overrides": {"font_size": 44, "scrim": True, "position": "bottom"},
    },
    "cinematic_luxury": {
        "label": "Cinematic Luxury",
        "style": "hyperframes_glass",
        "description": "Card vetro satinato, bordo sabbia, tipografia alta.",
        "overrides": {"font_size": 42, "scrim": True, "position": "bottom"},
    },
    "minimal": {
        "label": "Minimal",
        "style": "serif_minimal",
        "description": "Solo testo, ombra discreta, nessun box.",
        "overrides": {"font_size": 40, "scrim": False, "position": "bottom"},
    },
    "luxury_serif": {
        "label": "Luxury Serif",
        "style": "serif_ivory",
        "description": "Serif ampio, elegante, interlinea ariosa.",
        "overrides": {"font_size": 48, "line_height": 1.3, "scrim": True},
    },
    "kinetic_typography": {
        "label": "Kinetic Typography",
        "style": "hyperframes_word_highlight",
        "description": "Evidenziazione progressiva parola per parola.",
        "overrides": {"font_size": 42, "scrim": False, "highlight": True},
    },
    "elegant_dissolve": {
        "label": "Dissolvenza elegante",
        "style": "hyperframes_kinetic",
        "description": "Ingresso/uscita in dissolvenza con accento oro.",
        "overrides": {"font_size": 44, "fade_in_sec": 0.3},
    },
    "none": {
        "label": "Nessun sottotitolo",
        "style": "none",
        "description": "Il Reel resta senza sottotitoli.",
        "overrides": {},
    },
}

STYLE_IDS = set(PRESETS)
SAFE_TOP = 0.10
SAFE_BOTTOM = 0.82
FONT_CHOICES = {"kora_serif": "Playfair Display", "kora_sans": "Space Grotesk"}
POSITIONS = {"top", "center", "bottom"}
ALIGNMENTS = {"left", "center", "right"}

DEFAULT_OVERRIDES: dict[str, Any] = {
    "font_size": 44,
    "color": "#F7F3EA",
    "position": "bottom",
    "align": "center",
    "line_height": 1.25,
    "max_lines": 3,
    "max_chars": 26,
    "scrim": True,
    "shadow": True,
    "fade_in_sec": 0.0,
    "fade_out_sec": 0.0,
    "highlight": False,
    "font": "kora_serif",
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def engine_style_for(preset: Optional[str]) -> Optional[str]:
    """Preset utente → stile reale del renderer (es. cinematic_luxury → hyperframes_glass)."""
    if not preset:
        return preset
    return PRESETS.get(preset, {}).get("style", preset)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_overrides(raw: Optional[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    clean = dict(DEFAULT_OVERRIDES)
    warnings: list[str] = []
    if not raw:
        return clean, warnings

    def _num(key: str, low: float, high: float) -> None:
        if key in raw and raw.get(key) is not None:
            try:
                clean[key] = round(_clamp(float(raw[key]), low, high), 3)
            except (TypeError, ValueError):
                warnings.append(f"{key} non numerico: ignorato")

    _num("font_size", 24, 72)
    _num("line_height", 1.0, 1.6)
    _num("fade_in_sec", 0.0, 1.0)
    _num("fade_out_sec", 0.0, 1.0)
    for key, low, high in (("max_lines", 1, 3), ("max_chars", 12, 34)):
        if key in raw and raw.get(key) is not None:
            try:
                clean[key] = int(_clamp(int(raw[key]), low, high))
            except (TypeError, ValueError):
                warnings.append(f"{key} non numerico: ignorato")
    if raw.get("color"):
        color = str(raw["color"]).strip()
        if _HEX.match(color):
            clean["color"] = color.upper()
        else:
            warnings.append("colore non valido (serve #RRGGBB): mantenuto l'avorio KORA")
    for key, allowed in (("position", POSITIONS), ("align", ALIGNMENTS), ("font", set(FONT_CHOICES))):
        if raw.get(key):
            value = str(raw[key]).strip().lower()
            if value in allowed:
                clean[key] = value
            else:
                warnings.append(f"{key} non riconosciuto: ignorato")
    for key in ("scrim", "shadow", "highlight"):
        if key in raw and raw[key] is not None:
            clean[key] = bool(raw[key])
    return clean, warnings


class CaptionDesigner:
    """Applica preset e override a tutto il Reel o a una singola scena."""

    def __init__(self, director: Any) -> None:
        self.director = director

    #: esposto anche sull'istanza per chi valida gli override da fuori
    normalize_overrides = staticmethod(normalize_overrides)

    def presets(self) -> dict[str, Any]:
        from kora_studio.agents.hyperframes_captions import CAPTION_STYLES

        items = []
        for pid, preset in PRESETS.items():
            engine_style = CAPTION_STYLES.get(preset["style"], {})
            items.append(
                {
                    "id": pid,
                    "label": preset["label"],
                    "description": preset["description"],
                    "engine_style": preset["style"],
                    "engine": engine_style.get("engine", "kora_native"),
                    "overrides": preset["overrides"],
                }
            )
        return {
            "presets": items,
            "defaults": copy.deepcopy(DEFAULT_OVERRIDES),
            "safe_area": {"top": SAFE_TOP, "bottom": SAFE_BOTTOM},
            "fonts": FONT_CHOICES,
            "positions": sorted(POSITIONS),
            "alignments": sorted(ALIGNMENTS),
        }

    def current(self, project_id: str) -> dict[str, Any]:
        proj = self.director.get_project(project_id)
        if not proj:
            raise KeyError(f"Progetto {project_id} non trovato")
        prefs = proj.get("preferences") or {}
        preset = prefs.get("caption_style") or "kora_editorial"
        if isinstance(preset, dict):
            preset = preset.get("preset") or "kora_editorial"
        return {
            "project_id": project_id,
            "preset": preset,
            "overrides": prefs.get("caption_overrides") or {},
            "updated_at": prefs.get("caption_updated_at"),
            "engine_style": engine_style_for(preset),
        }

    def _write_preferences(self, project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        proj = self.director.get_project(project_id)
        if not proj:
            raise KeyError(f"Progetto {project_id} non trovato")
        prefs = dict(proj.get("preferences") or {})
        prefs.update(patch)
        with self.director.db.session() as conn:
            conn.execute(
                "UPDATE projects SET preferences_json = ? WHERE id = ?",
                (json.dumps(prefs, ensure_ascii=False), project_id),
            )
        return prefs

    def apply(
        self,
        project_id: str,
        preset: str,
        overrides: Optional[dict[str, Any]] = None,
        scene_number: Optional[int] = None,
    ) -> dict[str, Any]:
        """Applica lo stile al Reel o a una scena. Non tocca l'audio."""
        if preset not in PRESETS:
            raise ValueError("Preset non riconosciuto. Disponibili: " + ", ".join(sorted(PRESETS)))
        clean, warnings = normalize_overrides(overrides)
        merged = {**DEFAULT_OVERRIDES, **PRESETS[preset]["overrides"], **clean}

        if scene_number is None:
            self._write_preferences(
                project_id,
                {
                    "caption_style": preset,
                    "caption_overrides": merged,
                    "caption_updated_at": datetime.utcnow().isoformat() + "Z",
                },
            )
            self.director.invalidate_preview(project_id)
            return {
                "ok": True,
                "scope": "reel",
                "preset": preset,
                "overrides": merged,
                "warnings": warnings,
                "audio_untouched": True,
                "note": "Sottotitoli aggiornati: la voce non viene rigenerata.",
            }

        _, scenes = self.director._project_scenes(project_id)
        scene = next((s for s in scenes if s.scene_number == scene_number), None)
        if scene is None:
            raise KeyError(f"Scena {scene_number} non trovata")
        if scene.is_outro:
            return {
                "ok": False,
                "blocked": True,
                "reason": "La scena finale (logo e payoff) non ha sottotitoli modificabili.",
            }
        if (scene.locks or {}).get("caption"):
            return {
                "ok": False,
                "blocked": True,
                "reason": f"Caption bloccata sulla scena {scene_number}: sbloccala prima di modificarla.",
            }
        scene.caption_style = preset
        scene.caption_overrides = merged
        scene.preview_status = "pending"
        self.director._persist_scenes(project_id, scenes, f"Stile caption scena {scene_number}: {preset}")
        self.director.invalidate_preview(project_id)
        return {
            "ok": True,
            "scope": "scene",
            "scene_number": scene_number,
            "preset": preset,
            "overrides": merged,
            "warnings": warnings,
            "audio_untouched": True,
        }

    def set_title_text(self, project_id: str, scene_number: int, text: str) -> dict[str, Any]:
        """Testo grafico interno (non pronunciato), separato dai sottotitoli."""
        _, scenes = self.director._project_scenes(project_id)
        scene = next((s for s in scenes if s.scene_number == scene_number), None)
        if scene is None:
            raise KeyError(f"Scena {scene_number} non trovata")
        if scene.is_outro:
            return {"ok": False, "blocked": True, "reason": "L'outro usa il payoff ufficiale, non un titolo."}
        clean = " ".join(str(text or "").split())[:80]
        if clean:
            guard = self.director.guardian.validate_copy(clean)
            if not guard.get("approved"):
                raise ValueError(
                    "Titolo non conforme al brand KORA: "
                    + "; ".join(guard.get("violations") or ["violazione non specificata"])
                )
        scene.title_text = clean or None
        scene.preview_status = "pending"
        self.director._persist_scenes(project_id, scenes, f"Titolo grafico scena {scene_number}")
        self.director.invalidate_preview(project_id)
        return {"ok": True, "scene_number": scene_number, "title_text": scene.title_text, "audio_untouched": True}
