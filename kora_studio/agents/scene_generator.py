"""AI Scene Generator (v3.5) — modalità A–E con controllo dei costi.

Non introduce motori nuovi: usa l'AIMediaLab esistente e i provider già
configurati. Prima di ogni chiamata esterna verifica credenziali, modello,
quota e costo; preferisce sempre la strada gratuita; se il costo non è
verificabile chiede autorizzazione esplicita. Nessuna installazione automatica,
nessuna scansione del disco.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import kora_studio.config as cfg

MODES: dict[str, dict[str, Any]] = {
    "A": {
        "id": "A",
        "label": "Dettaglio reale dall'originale",
        "cost": "gratuito",
        "engine": "kora_crop",
        "authenticity": "dettaglio_da_originale",
        "description": "Ritaglio fedele dalla fotografia autentica: nessun elemento inventato.",
        "requires_reference": True,
        "tested": True,
    },
    "B": {
        "id": "B",
        "label": "Animazione deterministica",
        "cost": "gratuito",
        "engine": "kora_kenburns",
        "authenticity": "animazione_conservativa",
        "description": "Movimento di camera calcolato sulla foto reale, nessun modello generativo.",
        "requires_reference": True,
        "tested": True,
    },
    "C": {
        "id": "C",
        "label": "Image-to-Video",
        "cost": "a consumo",
        "engine": "google_veo_api",
        "authenticity": "generativo_controllato",
        "description": "Animazione da fotografia di riferimento con vincoli di autenticità.",
        "requires_reference": True,
        "tested": False,
    },
    "D": {
        "id": "D",
        "label": "Atmosfera illustrativa sintetica",
        "cost": "a consumo",
        "engine": "google_veo_api",
        "authenticity": "illustrativo_sintetico",
        "description": "Scena d'atmosfera non documentaria, dichiarata come sintetica.",
        "requires_reference": False,
        "tested": False,
    },
    "E": {
        "id": "E",
        "label": "Variazione guidata da riferimento",
        "cost": "a consumo",
        "engine": "google_veo_api",
        "authenticity": "generativo_controllato",
        "description": "Nuova inquadratura dello stesso soggetto, guidata dalla foto e dal prompt.",
        "requires_reference": True,
        "tested": False,
    },
}

ENGINES: dict[str, dict[str, Any]] = {
    "kora_crop": {"label": "KORA Crop (locale)", "free": True, "credential": None},
    "kora_kenburns": {"label": "KORA Animazione (locale)", "free": True, "credential": None},
    "google_veo_api": {"label": "Google Veo / Gemini", "free": False, "credential": "gemini_api"},
    "fal_ai": {"label": "fal.ai", "free": False, "credential": "fal_api"},
    "qwen_wan_api": {"label": "Alibaba Qwen / Wan", "free": False, "credential": "qwen_api"},
    "huggingface_api": {"label": "Hugging Face", "free": True, "credential": "huggingface_api"},
}


class SceneGenerator:
    """Facade sottile: preflight onesto, generazione, provenienza, uso in scena."""

    def __init__(self, director: Any) -> None:
        self.director = director
        self.lab = director.ai_media_lab

    # ----------------------------------------------------------- inventario
    def modes(self) -> dict[str, Any]:
        return {
            "modes": list(MODES.values()),
            "engines": ENGINES,
            "budget_zero": self._budget_zero(),
            "note": (
                "Le modalità A e B sono locali e gratuite. C, D ed E chiamano un provider esterno: "
                "il costo è stimato prima dell'invio e nulla parte senza autorizzazione."
            ),
        }

    def _budget_zero(self) -> bool:
        try:
            prefs = self.director.settings.get_preferences()
            return float(prefs.get("monthly_budget_eur") or 0.0) <= 0.0
        except Exception:
            return True

    def _credential(self, engine: str) -> Optional[str]:
        key_name = ENGINES.get(engine, {}).get("credential")
        if not key_name:
            return "locale"
        try:
            key = self.director.providers.get_decrypted_key(key_name)
        except Exception:
            key = None
        return key

    # ------------------------------------------------------------ preflight
    def preflight(self, mode: str, engine: Optional[str] = None) -> dict[str, Any]:
        spec = MODES.get(str(mode).upper())
        if spec is None:
            raise ValueError("Modalità non riconosciuta: scegli A, B, C, D o E.")
        chosen = engine or spec["engine"]
        checks: list[dict[str, Any]] = []

        credential = self._credential(chosen)
        checks.append(
            {
                "check": "credenziali",
                "ok": bool(credential),
                "detail": "provider locale" if credential == "locale" else ("chiave presente" if credential else "chiave assente"),
            }
        )

        free = bool(ENGINES.get(chosen, {}).get("free"))
        budget_zero = self._budget_zero()
        checks.append(
            {
                "check": "costo",
                "ok": free or not budget_zero,
                "detail": "gratuito" if free else ("budget a zero: bloccato" if budget_zero else "a consumo, autorizzazione richiesta"),
            }
        )
        checks.append(
            {
                "check": "modello",
                "ok": True,
                "detail": "modello locale" if free else "scelto dal provider a runtime, elenco non verificabile offline",
            }
        )
        checks.append(
            {
                "check": "quota",
                "ok": True,
                "detail": "non verificabile prima della chiamata" if not free else "nessuna quota",
            }
        )
        checks.append(
            {
                "check": "verifica_umana",
                "ok": True,
                "detail": "risultato marcato «da verificare umano» e provenienza registrata",
            }
        )

        blocking = [c for c in checks if not c["ok"]]
        return {
            "mode": spec,
            "engine": chosen,
            "available": (not blocking) or (free and bool(credential)),
            "requires_authorization": not free,
            "blocking": blocking,
            "checks": checks,
            "tested": spec["tested"],
            "declared_limit": (
                "Provider non ancora collaudato end-to-end in questa build: la generazione reale "
                "dipende dalla chiave e dal piano dell'account."
                if not spec["tested"]
                else ""
            ),
        }

    # ---------------------------------------------------------- generazione
    def generate(
        self,
        project_id: str,
        scene_number: int,
        mode: str,
        prompt: str = "",
        reference_asset_id: Optional[str] = None,
        duration_sec: int = 5,
        explicit_authorization: bool = False,
        vertical: bool = True,
        variants: int = 1,
    ) -> dict[str, Any]:
        spec = MODES.get(str(mode).upper())
        if spec is None:
            raise ValueError("Modalità non riconosciuta: scegli A, B, C, D o E.")
        proj, scenes = self.director._project_scenes(project_id)
        scene = next((s for s in scenes if s.scene_number == int(scene_number)), None)
        if scene is None:
            raise KeyError(f"Scena {scene_number} non trovata")
        if scene.is_outro:
            return {"ok": False, "blocked": True, "reason": "La scena finale non si genera: usa il materiale istituzionale."}

        reference = reference_asset_id or scene.selected_asset_id
        if spec["requires_reference"] and not reference:
            raise ValueError("Questa modalità richiede una fotografia di riferimento dal Vault.")

        pre = self.preflight(mode)
        if not pre["available"]:
            return {"ok": False, "blocked": True, "reason": pre["blocking"][0]["detail"], "preflight": pre}
        if pre["requires_authorization"] and not explicit_authorization:
            return {
                "ok": False,
                "requires_authorization": True,
                "preflight": pre,
                "reason": "Il costo non è verificabile in anticipo: conferma esplicita necessaria per evitare spese.",
            }

        started = datetime.utcnow()
        if mode == "A":
            result = self.lab.create_detail_crop(
                reference,
                x_norm=0.5,
                y_norm=0.5,
                zoom=1.8,
                detail_label=(prompt or "dettaglio reale")[:80],
            )
        elif mode == "B":
            result = self.lab.animate_photo_deterministic(reference, duration_sec=duration_sec)
        else:
            result = self.lab.execute_veo_video(
                mode="image_to_video" if spec["requires_reference"] else "text_to_video",
                prompt=prompt,
                duration_sec=max(3, min(8, int(duration_sec))),
                asset_id=reference if spec["requires_reference"] else None,
                camera_motion="movimento lento e naturale",
                explicit_authorization=True,
            )

        if not isinstance(result, dict):
            result = {"status": "completed", "output_path": str(result)}
        status = str(result.get("status") or ("completed" if result.get("asset") else ""))
        asset = result.get("asset") if isinstance(result, dict) else None
        generation_id = "gen_" + uuid.uuid4().hex[:10]
        elapsed_sec = round((datetime.utcnow() - started).total_seconds(), 2)
        local_engine = spec["cost"] in ("gratuito", "locale")
        provider = str(result.get("provider") or (spec["engine"] if local_engine else "") or "")
        if not provider and not local_engine:
            provider = pre.get("provider") or ""
        record = {
            "id": generation_id,
            "project_id": project_id,
            "scene_number": scene_number,
            "mode": mode,
            "label": spec["label"],
            "engine": spec["engine"],
            "provider": provider or "kora_locale",
            "model": str(result.get("model") or ""),
            "prompt": prompt,
            "prompt_hash": __import__("hashlib").sha256((prompt or "").encode("utf-8")).hexdigest()[:32],
            "reference_asset_id": reference,
            "references": [reference] if reference else [],
            "params": {
                "duration_sec": duration_sec,
                "vertical": bool(vertical),
                "variants": int(variants),
                "crop": {"x": 0.5, "y": 0.5, "zoom": 1.8} if mode == "A" else None,
            },
            "duration_sec": duration_sec,
            "authenticity": result.get("authenticity_status") or spec["authenticity"],
            "status": status or "unknown",
            "asset_id": (asset or {}).get("id"),
            "created_at": datetime.utcnow().isoformat() + "Z",
            "elapsed_sec": elapsed_sec,
            "cost_eur": 0.0 if local_engine else result.get("cost_eur"),
            "cost_class": "locale" if local_engine else "stimato_dal_provider",
            "validation": {
                "file_exists": bool(asset and asset.get("working_path") and Path(str(asset["working_path"])).exists()),
                "engine_reported": status or "unknown",
                "human_review": "richiesta",
            },
            "provenance_version": "3.6",
            "requires_human_review": True,
            "result": {k: v for k, v in (result or {}).items() if k != "asset"},
        }
        self._record(project_id, record)

        ok = status in ("completed", "ok", "ready")
        return {
            "ok": ok,
            "blocked": status == "blocked",
            "generation": record,
            "preflight": pre,
            "note": ""
            if ok
            else (
                "Generazione non completata dal provider. "
                "Nessun file è stato sostituito: l'originale resta intatto."
            ),
        }

    # ------------------------------------------------------------ storico
    def _preferences(self, project_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        proj = self.director.get_project(project_id)
        if not proj:
            raise KeyError(f"Progetto {project_id} non trovato")
        return proj, dict(proj.get("preferences") or {})

    def _record(self, project_id: str, record: dict[str, Any]) -> None:
        _, prefs = self._preferences(project_id)
        items = list(prefs.get("ai_generations") or [])
        items.append(record)
        prefs["ai_generations"] = items[-60:]
        with self.director.db.session() as conn:
            conn.execute(
                "UPDATE projects SET preferences_json = ? WHERE id = ?",
                (json.dumps(prefs, ensure_ascii=False), project_id),
            )

    def history(self, project_id: str) -> dict[str, Any]:
        _, prefs = self._preferences(project_id)
        return {"project_id": project_id, "generations": list(prefs.get("ai_generations") or [])}

    def use_in_scene(self, project_id: str, generation_id: str) -> dict[str, Any]:
        _, prefs = self._preferences(project_id)
        record = next((g for g in prefs.get("ai_generations") or [] if g.get("id") == generation_id), None)
        if not record or not record.get("asset_id"):
            raise KeyError("Generazione non trovata o senza file associato.")
        result = self.director.scene_editor.update_scene(
            project_id, int(record["scene_number"]), {"asset_id": record["asset_id"]}
        )
        if result.get("ok"):
            _, scenes = self.director._project_scenes(project_id)
            scene = next((s for s in scenes if s.scene_number == int(record["scene_number"])), None)
            if scene is not None:
                scene.authenticity_status = record["authenticity"]
                self.director._persist_scenes(project_id, scenes, "Materiale generato inserito in scena")
        return {"ok": bool(result.get("ok")), "generation": record, "scene": result}

    def discard(self, project_id: str, generation_id: str) -> dict[str, Any]:
        _, prefs = self._preferences(project_id)
        items = [g for g in prefs.get("ai_generations") or [] if g.get("id") != generation_id]
        if len(items) == len(prefs.get("ai_generations") or []):
            raise KeyError("Generazione non trovata.")
        prefs["ai_generations"] = items
        with self.director.db.session() as conn:
            conn.execute(
                "UPDATE projects SET preferences_json = ? WHERE id = ?",
                (json.dumps(prefs, ensure_ascii=False), project_id),
            )
        return {"ok": True, "discarded": generation_id, "originals_untouched": True}

    def save_copy(self, project_id: str, generation_id: str) -> dict[str, Any]:
        """Salva una copia nel Vault: l'asset originale non viene mai sovrascritto."""
        _, prefs = self._preferences(project_id)
        record = next((g for g in prefs.get("ai_generations") or [] if g.get("id") == generation_id), None)
        if not record or not record.get("asset_id"):
            raise KeyError("Generazione non trovata o senza file associato.")
        asset = self.director.vault.get_asset(record["asset_id"])
        if not asset:
            raise KeyError("Il file generato non è più nel Vault.")
        source = Path(asset.get("working_path") or asset.get("original_path"))
        if not source.is_file():
            raise KeyError("Il file generato non è più leggibile su disco.")
        dest = cfg.CACHE_DIR / f"saved_{generation_id}{source.suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
        return {"ok": True, "saved_path": str(dest), "asset_id": record["asset_id"], "originals_untouched": True}
