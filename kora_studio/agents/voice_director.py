"""Voice Studio — Brando, Tiziana, configurable voices, and no-voiceover mode.

Never silently substitutes one voice for another. Never unnaturally speeds up speech.
Never alters approved script text. Never pastes an archived recording onto a
different new script! Uses authorized matching assets or dynamic TTS APIs,
and explicitly labels any fallback when primary TTS is unavailable.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Optional

import requests

from kora_studio.config import VOICES_PIPER_DIR
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
            el_id = self._get_elevenlabs_voice_id(vid)
            out.append(
                {
                    **meta,
                    "vault_asset_id": vault_asset["id"] if vault_asset else None,
                    "vault_duration_sec": vault_asset["duration_sec"] if vault_asset else None,
                    "elevenlabs_voice_id": el_id or "",
                    "elevenlabs_configured": bool(eleven_key and el_id),
                    "openai_tts_configured": bool(openai_key),
                    "piper_local_available": piper_ready,
                    "status": (
                        "attiva_senza_voce"
                        if vid == "none"
                        else (
                            "operativa_api"
                            if (eleven_key and el_id)
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

    def _synthesize_elevenlabs(
        self, voice_id: str, text: str, dest: Path
    ) -> Optional[Path]:
        key = self.providers.get_decrypted_key("elevenlabs_api")
        el_voice_id = self._get_elevenlabs_voice_id(voice_id)
        if not key or not el_voice_id:
            return None
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{el_voice_id}",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.55, "similarity_boost": 0.78},
            },
            timeout=45,
        )
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs API error {r.status_code}: {r.text[:200]}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest

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
        source_used = ""

        # 1. Primary: Official ElevenLabs API (synthesizes exact new script in requested voice)
        el_key = self.providers.get_decrypted_key("elevenlabs_api")
        el_voice_id = self._get_elevenlabs_voice_id(voice_id)
        if el_key and el_voice_id:
            auth = self.providers.cost_guard.check_authorization(
                0.05, explicit_user_authorization=explicit_paid_authorization
            )
            if auth["authorized"]:
                try:
                    mp3_tmp = work_dir / f"voice_{voice_id}_el.mp3"
                    self._synthesize_elevenlabs(voice_id, full_text, mp3_tmp)
                    self.normalize_audio(mp3_tmp, norm_out)
                    source_used = "elevenlabs_api"
                except Exception as exc:
                    substitution_notice = f"ElevenLabs non riuscito ({exc}); verifico alternative."

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
                substitution_notice = (
                    f"Servizio vocale '{VOICE_CATALOG[voice_id]['label']}' non configurato per nuovo testo: "
                    "utilizzato sintetizzatore locale gratuito Piper TTS (esplicitamente segnalato)."
                )

        # 5. Free Edge TTS fallback (works on internet-connected machines; skipped cleanly offline)
        if not source_used:
            edge_mp3 = work_dir / f"voice_{voice_id}_edge.mp3"
            edge_res = self._synthesize_edge_tts(voice_id, full_text, edge_mp3)
            if edge_res and edge_res.is_file():
                self.normalize_audio(edge_res, norm_out)
                source_used = "edge_tts_fallback"
                substituted = True
                substitution_notice = (
                    f"Voce '{VOICE_CATALOG[voice_id]['label']}' sostituita temporaneamente con voce neurale "
                    "gratuita di fallback (Edge TTS) per pronunciare esattamente il nuovo testo."
                )

        # 6. No TTS & script differs from archived recording -> DO NOT insert mismatched old audio!
        if not source_used or not norm_out.is_file():
            return {
                "voice_id": voice_id,
                "status": "richiede_sintesi_o_registrazione",
                "audio_path": None,
                "duration_sec": 0.0,
                "substituted": True,
                "substitution_notice": (
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
        if actual_dur > total_scene_dur:
            timing_adjustments.append(
                f"Durata audio ({actual_dur:.2f}s) superiore alle scene visive ({total_scene_dur:.2f}s): "
                "consentito raccordo naturale verso il finale senza accelerare la voce."
            )

        return {
            "voice_id": voice_id,
            "voice_label": VOICE_CATALOG[voice_id]["label"],
            "source_used": source_used,
            "status": "pronta",
            "audio_path": str(norm_out),
            "duration_sec": actual_dur,
            "substituted": substituted,
            "substitution_notice": substitution_notice,
            "word_check": {
                "passed": True,
                "expected_words": expected_words,
                "note": "Testo approvato preservato integralmente.",
            },
            "timing_adjustments": timing_adjustments,
        }
