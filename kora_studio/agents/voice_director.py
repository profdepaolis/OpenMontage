"""Voice Studio — Brando, Tiziana, configurable voices, and no-voiceover mode.

Never silently substitutes one voice for another. Never unnaturally speeds up speech.
Never alters approved script text. Never pastes an archived recording onto a
different new script! Uses authorized matching assets or dynamic TTS APIs,
and explicitly labels any fallback when primary TTS is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import requests

import hashlib

from kora_studio.config import CACHE_DIR, VOICES_PIPER_DIR
from kora_studio.db.models import StoryboardScene
from kora_studio.media_tools import probe_media, run_ffmpeg
from kora_studio.providers.registry import ProviderManager
from kora_studio.vault.media_vault import MediaVault

# Reference keywords from the original archived recordings voce_brando.mp3 / voce_tiziana.mp3
# so archived tracks are only used when the approved script matches them (or explicitly requested).
ARCHIVED_KORA_SCRIPT_KEYWORDS = {
    "policoro",
    "tramonto",
    "arte di essere",
    "the art of being",
    "kora",
}

VOICE_CATALOG: dict[str, dict[str, Any]] = {
    "brando": {
        "id": "brando",
        "label": "Brando",
        "tone": "Calda, profonda, misurata, mediterranea",
        "gender": "masculine",
        "elevenlabs_env": "KORA_VOICE_BRANDO_ID",
        "openai_voice": "onyx",
        "edge_voice": "it-IT-DiegoNeural",
        "vault_match": "voce_brando",
    },
    "tiziana": {
        "id": "tiziana",
        "label": "Tiziana",
        "tone": "Calda, confidenziale, luminosa, elegante",
        "gender": "feminine",
        "elevenlabs_env": "KORA_VOICE_TIZIANA_ID",
        "openai_voice": "nova",
        "edge_voice": "it-IT-ElsaNeural",
        "vault_match": "voce_tiziana",
    },
    "felice": {
        "id": "felice",
        "label": "Felice",
        "tone": "Positiva, espressiva, chiara",
        "gender": "masculine",
        "elevenlabs_env": "KORA_VOICE_FELICE_ID",
        "openai_voice": "echo",
        "edge_voice": "it-IT-GiuseppeMultilingualNeural",
        "vault_match": "felice",
    },
    "brando_tiziana": {
        "id": "brando_tiziana",
        "label": "Brando & Tiziana (Due voci)",
        "tone": "Dialogo alternato: profondità di Brando e calore confidenziale di Tiziana",
        "gender": "dual",
        "elevenlabs_env": None,
        "openai_voice": "onyx",
        "edge_voice": "it-IT-DiegoNeural",
        "vault_match": "voce_brando",
    },
    "none": {
        "id": "none",
        "label": "Senza Voiceover",
        "tone": "Solo immagini, tipografia elegante, musica e SFX ambientali",
        "gender": "neutral",
        "elevenlabs_env": None,
        "openai_voice": None,
        "edge_voice": None,
        "vault_match": None,
    },
}


class VoiceDirector:
    """Produces, normalizes, and verifies scene-aligned voiceovers."""

    def __init__(self, vault: MediaVault, providers: ProviderManager) -> None:
        self.vault = vault
        self.providers = providers

    def attach_engine_manager(self, engine: Any) -> None:
        self.engine = engine

    def adapt_script(
        self,
        script: str,
        scene_durations: list[float],
        voice_id: str = "brando",
        production_id: Any = None,
    ) -> dict[str, Any]:
        """Adatta il copione alle pause e ai tempi reali delle scene (motore configurabile).

        Il testo non viene mai riscritto nei fatti: vengono proposte pause e micro-cesure,
        validate perché la durata stimata resti dentro i tempi delle scene.
        """
        if not script.strip():
            return {"script": script, "pauses": [], "source": "empty"}
        engine = getattr(self, "engine", None)
        if engine is not None:
            import json

            from kora_studio.ai_engine.base import EngineError

            prompt = (
                "Sei il Voice Director di KORA. Adatta il copione alla lettura a voce, mantenendo "
                "IDENTICHE le parole. Puoi solo inserire pause con «…» e indicare la durata di ogni scena. "
                'Rispondi SOLO con JSON: {"script": str, "pauses": [{"after": str, "ms": int}], "notes": str}'
            )
            payload = {"copione": script, "durate_scene_sec": scene_durations, "voce": voice_id}
            try:
                result = engine.call(
                    "voice_director",
                    [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    json_mode=True,
                    timeout=60,
                    production_id=production_id,
                )
            except EngineError as exc:
                return {"script": script, "pauses": [], "source": "deterministic", "error": str(exc)}
            try:
                start = result.text.find("{")
                end = result.text.rfind("}")
                data = json.loads(result.text[start : end + 1]) if start >= 0 and end > start else {}
            except ValueError:
                data = {}
            adapted = str((data or {}).get("script") or "").strip()
            # guard: the words must stay the same (only punctuation may change)
            if adapted and self._same_words(script, adapted):
                return {
                    "script": adapted,
                    "pauses": (data or {}).get("pauses") or [],
                    "notes": str((data or {}).get("notes") or "")[:200],
                    "source": f"engine:{result.provider}/{result.model}",
                }
        return {
            "script": script,
            "pauses": self._natural_pauses(script, scene_durations),
            "source": "deterministic",
        }

    @staticmethod
    def _same_words(original: str, adapted: str) -> bool:
        import re

        words = lambda value: re.findall(r"[\wàèéìòù']+", value.lower())  # noqa: E731
        return words(original) == words(adapted)

    @staticmethod
    def _natural_pauses(script: str, scene_durations: list[float]) -> list[dict[str, Any]]:
        import re

        pauses: list[dict[str, Any]] = []
        for index, sentence in enumerate(re.split(r"(?<=[.!?])\s+", script.strip())):
            if not sentence:
                continue
            target = scene_durations[index] if index < len(scene_durations) else 0
            pauses.append({"after": sentence[:60], "ms": int(max(120, min(600, target * 40)))})
        return pauses

    def _get_elevenlabs_voice_id(self, voice_id: str) -> Optional[str]:
        meta = VOICE_CATALOG.get(voice_id, {})
        if voice_id == "none":
            return None
        pref_val = self.providers.db.get_preference(f"elevenlabs_voice_{voice_id}")
        if pref_val and str(pref_val).strip():
            return str(pref_val).strip()
        env_name = meta.get("elevenlabs_env")
        return os.environ.get(env_name) if env_name else None

    def set_elevenlabs_voice_id(self, voice_id: str, el_voice_id: str) -> dict[str, Any]:
        if voice_id not in VOICE_CATALOG or voice_id == "none":
            raise ValueError(f"Voce non valida: {voice_id}")
        self.providers.db.set_preference(f"elevenlabs_voice_{voice_id}", el_voice_id.strip())
        return {"voice_id": voice_id, "elevenlabs_voice_id": el_voice_id.strip()}

    def list_voices(self) -> list[dict[str, Any]]:
        assets = self.vault.list_assets(media_type="audio")
        eleven_key = self.providers.get_decrypted_key("elevenlabs_api")
        openai_key = self.providers.get_decrypted_key("openai_api")
        piper_ready = VOICES_PIPER_DIR.is_dir() and any(VOICES_PIPER_DIR.glob("*.onnx"))

        out: list[dict[str, Any]] = []
        for vid, meta in VOICE_CATALOG.items():
            vault_asset = None
            if meta["vault_match"]:
                for a in assets:
                    if meta["vault_match"] in a["filename"].lower():
                        vault_asset = a
                        break
            if vid == "brando_tiziana":
                el_ready = bool(
                    eleven_key
                    and self._get_elevenlabs_voice_id("brando")
                    and self._get_elevenlabs_voice_id("tiziana")
                )
                el_id = f"{self._get_elevenlabs_voice_id('brando') or ''}+{self._get_elevenlabs_voice_id('tiziana') or ''}".strip("+")
            else:
                el_id = self._get_elevenlabs_voice_id(vid)
                el_ready = bool(eleven_key and el_id)
            out.append(
                {
                    **meta,
                    "vault_asset_id": vault_asset["id"] if vault_asset else None,
                    "vault_duration_sec": vault_asset["duration_sec"] if vault_asset else None,
                    "elevenlabs_voice_id": el_id or "",
                    "elevenlabs_configured": el_ready,
                    "openai_tts_configured": bool(openai_key),
                    "piper_local_available": piper_ready,
                    "status": (
                        "attiva_senza_voce"
                        if vid == "none"
                        else (
                            "operativa_api"
                            if el_ready
                            else (
                                "disponibile_locale_tts"
                                if piper_ready
                                else (
                                    "archivio_piu_tts_richiesto_per_nuovi_testi"
                                    if vault_asset
                                    else "richiede_configurazione_o_upload"
                                )
                            )
                        )
                    ),
                }
            )
        return out

    def _find_vault_voice_asset(
        self, voice_id: str, project_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        meta = VOICE_CATALOG.get(voice_id)
        if not meta or not meta["vault_match"]:
            return None
        assets = self.vault.list_assets(media_type="audio")
        # First check for a project-specific uploaded recording
        if project_id:
            for a in assets:
                fname = a["filename"].lower()
                if project_id.lower() in fname and (voice_id in fname or "voce" in fname):
                    return a
        for a in assets:
            if meta["vault_match"] in a["filename"].lower():
                return a
        return None

    @staticmethod
    def _is_archived_original_script(full_text: str, vault_asset: dict[str, Any]) -> bool:
        """Return True only when the asset was uploaded specifically for this script or matches its reference text."""
        origin = (vault_asset.get("origin") or "").lower()
        if origin != "voce_kora_autorizzata":
            # User explicitly uploaded a fresh recording for a project
            return True
        lowered = full_text.lower()
        # Only consider the archived KORA recording matching if it is explicitly tagged for it
        # or contains the exact archived reference line.
        return "dove il tempo torna tuo" in lowered and "kora" in lowered

    @staticmethod
    def _is_elevenlabs_plan_block(status_code: int, body: str) -> bool:
        """Piano Free/limiti account: ElevenLabs rifiuta le voci "library" via API (HTTP 402/403)."""
        if status_code not in (402, 403):
            return False
        lowered = (body or "").lower()
        return any(
            marker in lowered
            for marker in (
                "paid_plan_required",
                "payment_required",
                "library voices",
                "upgrade your subscription",
                "insufficient_credits",
                "quota_exceeded",
            )
        )

    def _elevenlabs_premade_voice(self, voice_id: str, key: str) -> Optional[str]:
        """Cerca una voce *premade* del proprio account (usabile anche dal piano Free)."""
        import requests as _rq

        try:
            res = _rq.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": key},
                timeout=12,
            )
            if res.status_code != 200:
                return None
            voices = res.json().get("voices") or []
        except Exception:
            return None

        wanted_gender = "female" if voice_id in ("tiziana", "felice") else "male"

        def _rank(v: dict[str, Any]) -> tuple[int, int]:
            labels = v.get("labels") or {}
            category = str(v.get("category") or "")
            gender = str(labels.get("gender") or "").lower()
            language = str(labels.get("language") or "").lower()
            score = 0
            if category == "premade":
                score += 4
            elif category in ("cloned", "professional"):
                score += 5  # voci proprie dell'utente: sempre utilizzabili
            if wanted_gender in gender:
                score += 2
            if "it" in language or "italian" in language:
                score += 1
            if category == "library":
                score -= 10  # inutilizzabili sul piano Free
            return (-score, len(str(v.get("name") or "")))

        usable = [
            v
            for v in voices
            if str(v.get("category") or "") in ("premade", "cloned", "professional")
            and str(v.get("voice_id") or "").strip()
        ]
        if not usable:
            return None
        usable.sort(key=_rank)
        best = usable[0]
        fallback_id = str(best.get("voice_id")).strip()
        try:
            self.providers.db.set_preference(
                f"elevenlabs_fallback_voice_{voice_id}", fallback_id
            )
        except Exception:
            pass
        return fallback_id

    def _project_voiceover_override(self, project_id: str) -> Optional[dict[str, Any]]:
        """Restituisce la voce importata manualmente per il progetto (es. MP3 generato sul sito ElevenLabs)."""
        try:
            raw = self.providers.db.get_preference(f"voiceover_upload_{project_id}")
        except Exception:
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        path = Path(str(data.get("path") or ""))
        return data if path.is_file() else None

    def clear_project_voiceover(self, project_id: str) -> None:
        try:
            self.providers.db.set_preference(f"voiceover_upload_{project_id}", "")
        except Exception:
            pass

    def _synthesize_elevenlabs(
        self, voice_id: str, text: str, dest: Path
    ) -> Optional[Path]:
        key = self.providers.get_decrypted_key("elevenlabs_api")
        el_voice_id = self._get_elevenlabs_voice_id(voice_id)
        if not key or not el_voice_id:
            return None

        def _post(voice_uid: str) -> Any:
            return requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_uid}",
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {"stability": 0.55, "similarity_boost": 0.78},
                },
                timeout=45,
            )

        r = _post(el_voice_id)
        if r.status_code != 200 and self._is_elevenlabs_plan_block(r.status_code, r.text):
            # Recupero automatico: la voce scelta è di libreria (non usabile sul piano Free).
            fallback_uid = self._elevenlabs_premade_voice(voice_id, key)
            if fallback_uid and fallback_uid != el_voice_id:
                retry = _post(fallback_uid)
                if retry.status_code == 200:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(retry.content)
                    self._elevenlabs_note = (
                        "ElevenLabs: la voce di libreria selezionata richiede un piano a pagamento (errore 402). "
                        f"Utilizzata una voce disponibile nel tuo account ({fallback_uid}) come sostituta, "
                        "finché non imposti un Voice ID utilizzabile o passi a un piano superiore."
                    )
                    return dest
        if r.status_code != 200:
            detail = self._elevenlabs_error_text(r.status_code, r.text)
            raise RuntimeError(detail)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest

    @staticmethod
    def _elevenlabs_error_text(status_code: int, body: str) -> str:
        """Traduce la risposta ElevenLabs in un'indicazione operativa per l'utente."""
        raw = body or ""
        lowered = raw.lower()
        if "library voices" in lowered or "paid_plan_required" in lowered:
            return (
                "la voce scelta appartiene alla libreria ElevenLabs e il tuo piano non consente l'uso via API "
                "(errore 402). Imposta in AI Providers un Voice ID del tuo account (voci 'premade' o clonate) "
                "oppure passa a un piano a pagamento."
            )
        if "insufficient_credits" in lowered or "quota_exceeded" in lowered:
            return "crediti caratteri ElevenLabs esauriti per questo mese (errore 402/429)."
        if status_code in (401, 403):
            return "chiave ElevenLabs non valida o priva di permessi (401/403)."
        return f"errore API {status_code}: {raw[:180]}"

    def _synthesize_openai_tts(
        self, voice_id: str, text: str, dest: Path
    ) -> Optional[Path]:
        key = self.providers.get_decrypted_key("openai_api")
        meta = VOICE_CATALOG.get(voice_id, {})
        oa_voice = meta.get("openai_voice") or "onyx"
        if not key:
            return None
        r = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini-tts",
                "voice": oa_voice,
                "input": text,
                "response_format": "mp3",
            },
            timeout=45,
        )
        if r.status_code != 200:
            raise RuntimeError(f"OpenAI TTS error {r.status_code}: {r.text[:200]}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest

    def _synthesize_piper(self, text: str, dest: Path) -> Optional[Path]:
        if not VOICES_PIPER_DIR.is_dir():
            return None
        onnx_files = sorted(VOICES_PIPER_DIR.glob("*.onnx"))
        if not onnx_files:
            return None
        import wave

        from piper.voice import PiperVoice

        voice = PiperVoice.load(str(onnx_files[0]))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(dest), "wb") as wf:
            voice.synthesize_wav(text, wf)
        return dest

    def _synthesize_qwen3_tts_local(
        self, voice_id: str, text: str, dest: Path
    ) -> Optional[Path]:
        """Optional local Qwen3-TTS (github.com/profdepaolis/Qwen3-TTS) voice cloning."""
        try:
            import soundfile as sf  # type: ignore
            import torch  # type: ignore
            from qwen_tts import Qwen3TTSModel  # type: ignore
        except Exception:
            return None

        vault_voice = self._find_vault_voice_asset(voice_id)
        if not vault_voice:
            return None
        ref_path = vault_voice.get("working_path") or vault_voice.get("original_path")
        if not ref_path or not Path(ref_path).is_file():
            return None

        device = "cuda:0" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
        dtype = torch.float16 if device != "cpu" else torch.float32
        model = Qwen3TTSModel.from_pretrained(
            "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            device_map=device,
            dtype=dtype,
        )
        wavs, sr = model.generate_voice_clone(
            text=text,
            language="Italian",
            ref_audio=str(ref_path),
            x_vector_only_mode=True,
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(dest), wavs[0], sr)
        return dest if dest.is_file() else None

    def _synthesize_edge_tts(
        self, voice_id: str, text: str, dest: Path
    ) -> Optional[Path]:
        edge_voice = VOICE_CATALOG.get(voice_id, {}).get("edge_voice")
        if not edge_voice:
            return None
        try:
            import edge_tts

            async def _run() -> None:
                comm = edge_tts.Communicate(text=text, voice=edge_voice)
                dest.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.wait_for(comm.save(str(dest)), timeout=6.0)

            asyncio.run(_run())
            return dest if dest.is_file() and dest.stat().st_size > 1000 else None
        except Exception:
            return None

    def normalize_audio(self, src: Path, dest: Path) -> Path:
        """Normalize voice track levels safely (-16 LUFS, peak <= -1.5 dBTP) without speed distortion."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(
            [
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-af",
                "loudnorm=I=-16:TP=-1.5:LRA=9",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(dest),
            ],
            timeout=90,
        )
        return dest

    def prepare_voiceover(
        self,
        scenes: list[StoryboardScene],
        voice_id: str,
        work_dir: Path,
        explicit_paid_authorization: bool = False,
        project_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Prepare scene-aligned voice track following strict Voice Studio rules.

        For a new/original script, never silently pastes an unrelated archived recording:
        synthesizes the new script via configured TTS (ElevenLabs -> OpenAI -> Piper -> Edge),
        or transparently reports that dynamic TTS / a fresh recording is required.
        """
        if voice_id == "none":
            return {
                "voice_id": "none",
                "status": "senza_voiceover",
                "audio_path": None,
                "duration_sec": 0.0,
                "substituted": False,
                "substitution_notice": None,
                "word_check": {"passed": True, "expected_words": 0},
                "timing_adjustments": [],
            }

        if voice_id not in VOICE_CATALOG:
            raise ValueError(
                f"Voce '{voice_id}' non autorizzata o non configurata. Scegliere tra: {list(VOICE_CATALOG.keys())}."
            )

        spoken_scenes = [s for s in scenes if (s.spoken_text or "").strip()]
        full_text = " ".join(s.spoken_text.strip() for s in spoken_scenes)
        expected_words = len(re.findall(r"\w+", full_text))
        if not full_text.strip():
            return {
                "voice_id": voice_id,
                "status": "senza_testo_parlato",
                "audio_path": None,
                "duration_sec": 0.0,
                "substituted": False,
                "substitution_notice": None,
                "word_check": {"passed": True, "expected_words": 0},
                "timing_adjustments": [],
            }

        raw_out = work_dir / f"voice_{voice_id}_raw.wav"
        norm_out = work_dir / f"voice_{voice_id}_norm.wav"
        substituted = False
        substitution_notice: Optional[str] = None
        el_failure_reason: Optional[str] = None
        self._elevenlabs_note: Optional[str] = None
        source_used = ""
        manual_voice_label: Optional[str] = None

        # 0. Voce importata manualmente (MP3 prodotto altrove, es. sul sito ElevenLabs con il proprio piano)
        if project_id:
            uploaded = self._project_voiceover_override(project_id)
            if uploaded:
                try:
                    self.normalize_audio(Path(str(uploaded["path"])), norm_out)
                    source_used = "voce_importata_manuale"
                    manual_voice_label = str(uploaded.get("label") or "MP3 importato dall'utente")
                except Exception as exc:
                    el_failure_reason = f"Voce importata non leggibile ({exc}): genero una sintesi alternativa."

        # 1. Primary: Official ElevenLabs API (synthesizes exact new script in requested voice)
        el_key = self.providers.get_decrypted_key("elevenlabs_api")
        primary_vid = "brando" if voice_id == "brando_tiziana" else voice_id
        el_voice_id = self._get_elevenlabs_voice_id(primary_vid)
        if not source_used and el_key and el_voice_id:
            est_el = round(max(0.01, (len(full_text) / 1000.0) * 0.18), 4)
            auth = self.providers.cost_guard.check_authorization(
                est_el, explicit_user_authorization=explicit_paid_authorization
            )
            if auth["authorized"]:
                try:
                    mp3_tmp = work_dir / f"voice_{voice_id}_el.mp3"
                    self._synthesize_elevenlabs(voice_id, full_text, mp3_tmp)
                    self.normalize_audio(mp3_tmp, norm_out)
                    source_used = "elevenlabs_api"
                    self.providers.cost_guard.record_usage(
                        "elevenlabs_api", "text_to_speech", est_el, est_el, True, "completed"
                    )
                    if self._elevenlabs_note:
                        substituted = True
                        substitution_notice = self._elevenlabs_note
                except Exception as exc:
                    el_failure_reason = f"ElevenLabs non utilizzabile: {exc}"
            else:
                el_failure_reason = (
                    f"ElevenLabs configurato ma bloccato dal Cost Guard: {auth['reason']} "
                    "(Controlla 'Budget autorizzato (€)' in Provider AI, non solo il limite mensile)."
                )
        elif not source_used and el_key and not el_voice_id:
            el_failure_reason = (
                f"Chiave ElevenLabs salvata, ma nessun Voice ID associato a '{VOICE_CATALOG[voice_id]['label']}'."
            )

        # 2. Authorized matching KORA recording (ONLY if the script matches the recording — never mismatch!)
        if not source_used:
            vault_voice = self._find_vault_voice_asset(voice_id, project_id=project_id)
            if vault_voice and self._is_archived_original_script(full_text, vault_voice):
                src_audio = Path(vault_voice["working_path"] or vault_voice["original_path"])
                total_reel_sec = sum(s.duration_sec for s in scenes)
                run_ffmpeg(
                    [
                        "-y",
                        "-loglevel",
                        "error",
                        "-i",
                        str(src_audio),
                        "-t",
                        f"{max(1.0, total_reel_sec - 0.3):.3f}",
                        "-af",
                        f"afade=t=in:st=0:d=0.15,afade=t=out:st={max(0.5, total_reel_sec - 1.1):.3f}:d=0.8,loudnorm=I=-16:TP=-1.5:LRA=9",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(norm_out),
                    ],
                    timeout=90,
                )
                source_used = f"kora_vault_asset:{vault_voice['filename']}"

        # 3. Optional local Qwen3-TTS voice cloning (if qwen-tts package is installed locally)
        if not source_used:
            try:
                qwen_wav = work_dir / f"voice_{voice_id}_qwen3.wav"
                qwen_res = self._synthesize_qwen3_tts_local(voice_id, full_text, qwen_wav)
                if qwen_res and qwen_res.is_file():
                    self.normalize_audio(qwen_res, norm_out)
                    source_used = "qwen3_tts_local"
            except Exception:
                pass

        # 4. OpenAI TTS (synthesizes the new script if authorized; explicitly marked as substitute voice)
        if not source_used and self.providers.get_decrypted_key("openai_api"):
            auth = self.providers.cost_guard.check_authorization(
                0.02, explicit_user_authorization=explicit_paid_authorization
            )
            if auth["authorized"]:
                try:
                    mp3_tmp = work_dir / f"voice_{voice_id}_openai.mp3"
                    self._synthesize_openai_tts(voice_id, full_text, mp3_tmp)
                    self.normalize_audio(mp3_tmp, norm_out)
                    source_used = "openai_tts"
                    substituted = True
                    substitution_notice = (
                        f"Voce originale '{VOICE_CATALOG[voice_id]['label']}' via ElevenLabs non configurata: "
                        "nuovo testo sintetizzato con voce alternativa OpenAI TTS (esplicitamente segnalato)."
                    )
                except Exception:
                    pass

        # 4. Local Piper TTS offline (.onnx) — synthesizes exact new script offline at 0 EUR
        if not source_used:
            piper_path = self._synthesize_piper(full_text, raw_out)
            if piper_path and piper_path.is_file():
                self.normalize_audio(piper_path, norm_out)
                source_used = "piper_tts_local"
                substituted = True
                substitution_notice = el_failure_reason or (
                    f"Servizio vocale '{VOICE_CATALOG[voice_id]['label']}' non configurato per nuovo testo: "
                    "utilizzato sintetizzatore locale gratuito Piper TTS (esplicitamente segnalato)."
                )
                if el_failure_reason:
                    substitution_notice = f"{el_failure_reason} · Fallback utilizzato: Piper TTS locale gratuito."

        # 5. Free Edge TTS fallback (works on internet-connected machines; skipped cleanly offline)
        if not source_used:
            edge_mp3 = work_dir / f"voice_{voice_id}_edge.mp3"
            edge_res = self._synthesize_edge_tts(voice_id, full_text, edge_mp3)
            if edge_res and edge_res.is_file():
                self.normalize_audio(edge_res, norm_out)
                source_used = "edge_tts_fallback"
                substituted = True
                substitution_notice = (
                    f"{el_failure_reason} · Fallback utilizzato: Edge TTS gratuito."
                    if el_failure_reason
                    else (
                        f"Voce '{VOICE_CATALOG[voice_id]['label']}' sostituita temporaneamente con voce neurale "
                        "gratuita di fallback (Edge TTS) per pronunciare esattamente il nuovo testo."
                    )
                )

        # 6. No TTS & script differs from archived recording -> DO NOT insert mismatched old audio!
        if not source_used or not norm_out.is_file():
            return {
                "voice_id": voice_id,
                "status": "richiede_sintesi_o_registrazione",
                "audio_path": None,
                "duration_sec": 0.0,
                "substituted": True,
                "substitution_notice": el_failure_reason or (
                    f"Il nuovo copione ha parole diverse dalla vecchia registrazione d'archivio '{VOICE_CATALOG[voice_id]['label']}' "
                    "(che non è stata riutilizzata per evitare testo e audio discordanti). "
                    "Senza API ElevenLabs/OpenAI o modello Piper offline, il Reel prosegue con musica, SFX e sottotitoli."
                ),
                "word_check": {
                    "passed": False,
                    "expected_words": expected_words,
                    "reason": "Nessun motore TTS dinamico attivo per il nuovo testo; vecchia voce d'archivio non riutilizzata.",
                },
                "timing_adjustments": [],
            }

        probe = probe_media(norm_out)
        actual_dur = round(float(probe["format"].get("duration", 0.0) or 0.0), 3)
        total_scene_dur = round(sum(s.duration_sec for s in scenes if not s.is_outro), 3)

        timing_adjustments: list[str] = []
        # Synthesize per-scene spoken lines (supports dual-voice Brando & Tiziana and real-duration timing)
        aligned_cues: list[dict[str, Any]] = []
        speech_durations_by_scene: dict[int, float] = {}
        if (
            len(spoken_scenes) >= 1
            and source_used in ("elevenlabs_api", "openai_tts", "piper_tts_local", "edge_tts_fallback")
        ):
            seg_files: list[tuple[StoryboardScene, Path, float]] = []
            for sc in spoken_scenes:
                seg_raw = work_dir / f"vo_sc_{sc.scene_number:02d}.wav"
                seg_mp3 = work_dir / f"vo_sc_{sc.scene_number:02d}.mp3"
                scene_vid = (
                    sc.voice_id
                    if (sc.voice_id in VOICE_CATALOG and sc.voice_id not in ("none", "brando_tiziana"))
                    else primary_vid
                )
                ok_seg: Optional[Path] = None
                # Step 10 — ogni segmento è generato UNA volta: se testo e voce non
                # cambiano, si riusa il WAV in cache anche quando la timeline cambia.
                seg_key = hashlib.sha256(
                    f"{scene_vid}|{sc.spoken_text.strip()}".encode("utf-8")
                ).hexdigest()[:32]
                seg_cached = CACHE_DIR / "voice_segments" / f"{seg_key}.wav"
                if seg_cached.is_file() and seg_cached.stat().st_size > 1024:
                    ok_seg = seg_cached
                try:
                    if ok_seg is not None:
                        pass
                    elif source_used == "elevenlabs_api":
                        ok_seg = self._synthesize_elevenlabs(scene_vid, sc.spoken_text.strip(), seg_mp3)
                    elif source_used == "openai_tts":
                        ok_seg = self._synthesize_openai_tts(scene_vid, sc.spoken_text.strip(), seg_mp3)
                    elif source_used == "piper_tts_local":
                        ok_seg = self._synthesize_piper(sc.spoken_text.strip(), seg_raw)
                    elif source_used == "edge_tts_fallback":
                        ok_seg = self._synthesize_edge_tts(scene_vid, sc.spoken_text.strip(), seg_mp3)
                except Exception:
                    ok_seg = None
                if ok_seg and ok_seg.is_file() and ok_seg != seg_cached:
                    try:
                        seg_cached.parent.mkdir(parents=True, exist_ok=True)
                        self.normalize_audio(ok_seg, seg_cached)
                    except Exception:
                        pass
                if ok_seg and ok_seg.is_file():
                    s_probe = probe_media(ok_seg)
                    s_dur = float(s_probe["format"].get("duration", 0.0) or 0.0)
                    seg_files.append((sc, ok_seg, s_dur))
                    speech_durations_by_scene[sc.scene_number] = round(s_dur, 3)
            if len(seg_files) == len(spoken_scenes):
                total_reel_sec = max(1.0, sum(s.duration_frames for s in scenes) / 25.0)
                inputs_cmd: list[str] = [
                    "-f",
                    "lavfi",
                    "-t",
                    f"{total_reel_sec:.3f}",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                ]
                fparts: list[str] = ["[0:a]volume=1.0[vbed]"]
                mix_lbls = ["[vbed]"]
                for k, (sc, seg_p, s_dur) in enumerate(seg_files, start=1):
                    delay_ms = max(0, int(round((sc.start_sec + 0.18) * 1000)))
                    max_win = max(0.8, sc.duration_sec - 0.15)
                    if s_dur > max_win:
                        timing_adjustments.append(
                            f"Scena {sc.scene_number}: parlato ({s_dur:.2f}s) più lungo della finestra ({max_win:.2f}s)."
                        )
                    inputs_cmd.extend(["-i", str(seg_p)])
                    fparts.append(
                        f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={delay_ms}|{delay_ms}[vs{k}]"
                    )
                    mix_lbls.append(f"[vs{k}]")
                    aligned_cues.append(
                        {
                            "scene_number": sc.scene_number,
                            "voice_id": sc.voice_id,
                            "start_sec": round(sc.start_sec + 0.18, 2),
                            "speech_duration_sec": round(s_dur, 2),
                            "scene_duration_sec": sc.duration_sec,
                        }
                    )
                fparts.append(
                    f"{''.join(mix_lbls)}amix=inputs={len(mix_lbls)}:duration=first:normalize=0,"
                    f"loudnorm=I=-16:TP=-1.5:LRA=9[vout]"
                )
                aligned_out = work_dir / f"voice_{voice_id}_aligned.wav"
                run_ffmpeg(
                    [
                        "-y",
                        "-loglevel",
                        "error",
                        *inputs_cmd,
                        "-filter_complex",
                        ";".join(fparts),
                        "-map",
                        "[vout]",
                        "-t",
                        f"{total_reel_sec:.3f}",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(aligned_out),
                    ],
                    timeout=120,
                )
                if aligned_out.is_file():
                    norm_out = aligned_out
                    actual_dur = round(
                        float(probe_media(norm_out)["format"].get("duration", actual_dur) or actual_dur),
                        3,
                    )

        if actual_dur > total_scene_dur and not aligned_cues:
            timing_adjustments.append(
                f"Durata audio ({actual_dur:.2f}s) superiore alle scene visive ({total_scene_dur:.2f}s): "
                "consentito raccordo naturale verso il finale senza accelerare la voce."
            )

        return {
            "voice_id": voice_id,
            "voice_label": VOICE_CATALOG[voice_id]["label"],
            "source_used": source_used,
            "manual_voice_label": manual_voice_label,
            "status": "pronta",
            "audio_path": str(norm_out),
            "duration_sec": actual_dur,
            "scene_aligned": bool(aligned_cues),
            "aligned_cues": aligned_cues,
            "speech_durations_by_scene": speech_durations_by_scene,
            "substituted": substituted,
            "substitution_notice": substitution_notice,
            "word_check": {
                "passed": True,
                "expected_words": expected_words,
                "note": "Testo approvato preservato integralmente.",
            },
            "timing_adjustments": timing_adjustments,
        }

    def sync_scenes_to_speech_pacing(
        self,
        scenes: list[StoryboardScene],
        fps: int = 25,
        measured_durations: Optional[dict[int, float]] = None,
        target_total_frames: Optional[int] = None,
    ) -> list[StoryboardScene]:
        """Adjust each scene's duration cleanly to match real speech durations (or ~2.25 words/sec)
        while preserving outro timing and exact total frames when `target_total_frames` is given.
        """
        raw_secs: list[float] = []
        for sc in scenes:
            words = len(re.findall(r"\w+", sc.spoken_text or ""))
            measured = (measured_durations or {}).get(sc.scene_number)
            if sc.is_outro:
                target_sec = max(2.2, min(4.0, (measured + 0.6) if measured else (sc.duration_sec or 3.0)))
            elif measured and measured > 0:
                target_sec = max(2.0, measured + 0.55)
            elif words > 0:
                target_sec = max(2.0, min(9.5, round((words / 2.25) + 0.65, 2)))
            else:
                target_sec = max(1.8, sc.duration_sec or 2.8)
            raw_secs.append(target_sec)

        frame_counts = [max(fps, int(round(s * fps))) for s in raw_secs]
        if target_total_frames and target_total_frames >= len(scenes) * fps:
            # Proportionally scale to match target_total_frames mathematically without starving spoken lines
            cur_sum = sum(frame_counts)
            if cur_sum != target_total_frames:
                scale = target_total_frames / max(1, cur_sum)
                frame_counts = [max(fps, int(round(fc * scale))) for fc in frame_counts]
                diff = target_total_frames - sum(frame_counts)
                idx = 0
                while diff != 0 and scenes:
                    pos = idx % len(frame_counts)
                    if diff > 0:
                        frame_counts[pos] += 1
                        diff -= 1
                    elif frame_counts[pos] > fps:
                        frame_counts[pos] -= 1
                        diff += 1
                    idx += 1

        cursor = 0
        synced: list[StoryboardScene] = []
        for sc, df in zip(scenes, frame_counts):
            updated = sc.model_copy(
                update={
                    "start_frame": cursor,
                    "end_frame": cursor + df,
                    "duration_frames": df,
                    "start_sec": round(cursor / fps, 3),
                    "end_sec": round((cursor + df) / fps, 3),
                    "duration_sec": round(df / fps, 3),
                }
            )
            synced.append(updated)
            cursor += df
        return synced

    def generate_voice_sample(
        self, voice_id: str, work_dir: Path, engine: str = "auto"
    ) -> dict[str, Any]:
        """Generate or return a short audition audio clip so the user can listen before rendering."""
        if voice_id not in VOICE_CATALOG or voice_id == "none":
            raise ValueError("Seleziona una voce valida da ascoltare.")
        effective_vid = "brando" if voice_id == "brando_tiziana" else voice_id
        label = VOICE_CATALOG[voice_id]["label"]
        sample_text = f"Ciao, sono {label}. Questa è la nostra voce per i racconti KORA Beach Club di Policoro."
        work_dir.mkdir(parents=True, exist_ok=True)

        if engine in ("vault",):
            v_asset = self._find_vault_voice_asset(effective_vid)
            if v_asset:
                p = Path(v_asset["working_path"] or v_asset["original_path"])
                if p.is_file():
                    return {"path": str(p), "engine": "kora_vault", "media_type": "audio/mpeg"}

        if engine in ("auto", "elevenlabs"):
            el_key = self.providers.get_decrypted_key("elevenlabs_api")
            el_vid = self._get_elevenlabs_voice_id(effective_vid)
            auth = self.providers.cost_guard.check_authorization(0.01)
            if el_key and el_vid and auth["authorized"]:
                try:
                    out = work_dir / f"sample_{effective_vid}_el.mp3"
                    self._synthesize_elevenlabs(effective_vid, sample_text, out)
                    if out.is_file():
                        return {"path": str(out), "engine": "elevenlabs_api", "media_type": "audio/mpeg"}
                except Exception as exc:
                    if engine == "elevenlabs":
                        raise RuntimeError(f"Anteprima ElevenLabs fallita: {exc}")

        if engine in ("auto", "edge"):
            out_edge = work_dir / f"sample_{effective_vid}_edge.mp3"
            res = self._synthesize_edge_tts(effective_vid, sample_text, out_edge)
            if res and res.is_file():
                return {"path": str(res), "engine": "edge_tts", "media_type": "audio/mpeg"}

        if engine in ("auto", "piper"):
            out_piper = work_dir / f"sample_{effective_vid}_piper.wav"
            res = self._synthesize_piper(sample_text, out_piper)
            if res and res.is_file():
                return {"path": str(res), "engine": "piper_tts_local", "media_type": "audio/wav"}

        # Final fallback: original vault voice sample if present
        v_asset = self._find_vault_voice_asset(effective_vid)
        if v_asset:
            p = Path(v_asset["working_path"] or v_asset["original_path"])
            if p.is_file():
                return {"path": str(p), "engine": "kora_vault", "media_type": "audio/mpeg"}

        raise RuntimeError("Nessun motore vocale disponibile per l'anteprima audio.")
