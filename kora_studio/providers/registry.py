"""AI Provider Manager — modular registry, diagnostics, secret storage, and strategy selection."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import requests

from kora_studio.config import SecretVault
from kora_studio.db.database import Database, utcnow_iso
from kora_studio.media_tools import check_remotion_runtime, find_ffmpeg, find_ffprobe
from kora_studio.providers.cost_guard import CostGuard

DEFAULT_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "local_ffmpeg_pillow",
        "name": "Motore Locale FFmpeg + Pillow + NumPy",
        "capabilities": [
            "video_editing",
            "image_editing",
            "image_animation",
            "color_grading",
            "visual_analysis",
            "audio_mixing",
        ],
        "endpoint": "local://ffmpeg-9",
        "auth_type": "nessuna (locale)",
        "models": ["ffmpeg-libx264", "lanczos-kenburns", "pil-color-analyzer"],
        "declared_costs": {"per_operation_eur": 0.0, "note": "Gratuito su hardware locale"},
        "quota_status": "Illimitato (locale)",
        "limits_doc": "Dipende da CPU locale; nessuna chiamata di rete.",
        "is_free_local": True,
        "env_var": None,
    },
    {
        "id": "piper_tts_local",
        "name": "Piper TTS Locale (Offline)",
        "capabilities": ["text_to_speech"],
        "endpoint": "local://piper-tts",
        "auth_type": "nessuna (modello .onnx locale)",
        "models": ["it_IT-paola-medium", "it_IT-riccardo-x_low"],
        "declared_costs": {"per_1k_chars_eur": 0.0, "note": "Gratuito offline se modello .onnx presente"},
        "quota_status": "Illimitato (locale)",
        "limits_doc": "Richiede file .onnx italiano in kora_data/voices/piper/.",
        "is_free_local": True,
        "env_var": None,
    },
    {
        "id": "remotion_local",
        "name": "Remotion Composer Locale",
        "capabilities": ["video_editing", "subtitles"],
        "endpoint": "local://remotion-composer",
        "auth_type": "nessuna (locale)",
        "models": ["remotion-4.0"],
        "declared_costs": {"per_operation_eur": 0.0, "note": "Open source locale; richiede Chromium headless"},
        "quota_status": "Illimitato (locale)",
        "limits_doc": "Richiede Node.js + Chromium headless installato.",
        "is_free_local": True,
        "env_var": None,
    },
    {
        "id": "gemini_api",
        "name": "Google Gemini API (Ufficiale)",
        "capabilities": ["llm", "visual_analysis", "image_generation", "image_editing"],
        "endpoint": "https://generativelanguage.googleapis.com/v1beta",
        "auth_type": "API Key (GEMINI_API_KEY)",
        "models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-image"],
        "declared_costs": {"llm_per_call_eur": 0.01, "image_per_gen_eur": 0.04, "free_tier_note": "Quota gratuita soggetta a limiti Google AI Studio"},
        "quota_status": "Non verificabile",
        "limits_doc": "Solo API ufficiale; l'abbonamento web Gemini Advanced non include API gratuite.",
        "is_free_local": False,
        "env_var": "GEMINI_API_KEY",
    },
    {
        "id": "google_veo_api",
        "name": "Google Veo / Flow API (Ufficiale)",
        "capabilities": ["image_to_video", "text_to_video"],
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/veo-3.0-generate-preview",
        "auth_type": "API Key / Vertex AI OAuth",
        "models": ["veo-3.0-generate-preview", "veo-2.0-generate-001"],
        "declared_costs": {"per_second_eur": 0.35},
        "quota_status": "Non verificabile",
        "limits_doc": "Generazione video a pagamento; richiede autorizzazione esplicita.",
        "is_free_local": False,
        "env_var": "GEMINI_API_KEY",
    },
    {
        "id": "elevenlabs_api",
        "name": "ElevenLabs Voice API (Ufficiale)",
        "capabilities": ["text_to_speech"],
        "endpoint": "https://api.elevenlabs.io/v1",
        "auth_type": "API Key (ELEVENLABS_API_KEY)",
        "models": ["eleven_multilingual_v2", "eleven_turbo_v2_5"],
        "declared_costs": {"per_1k_chars_eur": 0.18},
        "quota_status": "Non verificabile",
        "limits_doc": "Crediti residui verificabili via /v1/user/subscription solo con chiave configurata.",
        "is_free_local": False,
        "env_var": "ELEVENLABS_API_KEY",
    },
    {
        "id": "openai_api",
        "name": "OpenAI API (Ufficiale)",
        "capabilities": ["llm", "text_to_speech", "transcription", "visual_analysis", "image_generation"],
        "endpoint": "https://api.openai.com/v1",
        "auth_type": "Bearer API Key (OPENAI_API_KEY)",
        "models": ["gpt-4.1-mini", "gpt-4o-mini-tts", "whisper-1", "gpt-image-1"],
        "declared_costs": {"llm_per_call_eur": 0.01, "tts_per_1k_chars_eur": 0.015, "image_per_gen_eur": 0.04},
        "quota_status": "Non verificabile",
        "limits_doc": "L'abbonamento ChatGPT Plus web non include crediti API; richiede credito prepagato su platform.openai.com.",
        "is_free_local": False,
        "env_var": "OPENAI_API_KEY",
    },
    {
        "id": "leonardo_ai",
        "name": "Leonardo AI API (Ufficiale)",
        "capabilities": ["image_generation", "image_editing", "image_to_video"],
        "endpoint": "https://cloud.leonardo.ai/api/rest/v1",
        "auth_type": "Bearer API Key (LEONARDO_API_KEY)",
        "models": ["leonardo-phoenix", "motion-2.0"],
        "declared_costs": {"image_per_gen_eur": 0.03, "video_per_gen_eur": 0.25},
        "quota_status": "Non verificabile",
        "limits_doc": "Token residui interrogabili via /me solo con chiave API valida.",
        "is_free_local": False,
        "env_var": "LEONARDO_API_KEY",
    },
    {
        "id": "huggingface_api",
        "name": "Hugging Face Inference API (Ufficiale)",
        "capabilities": ["image_generation", "llm", "transcription"],
        "endpoint": "https://api-inference.huggingface.co",
        "auth_type": "Bearer Token (HF_TOKEN)",
        "models": ["FLUX.1-schnell", "whisper-large-v3"],
        "declared_costs": {"free_tier_eur": 0.0, "pro_rate_note": "Quota gratuita limitata su Inference API"},
        "quota_status": "Non verificabile",
        "limits_doc": "Rispetta i rate limit ufficiali; nessun aggiramento.",
        "is_free_local": False,
        "env_var": "HF_TOKEN",
    },
]


class ProviderManager:
    """Central registry, health tester, secret manager, and strategy selector."""

    def __init__(self, db: Database, vault: Optional[SecretVault] = None) -> None:
        self.db = db
        self.vault = vault or SecretVault()
        self.cost_guard = CostGuard(db)
        self.ensure_seeded()

    def ensure_seeded(self) -> None:
        now = utcnow_iso()
        with self.db.session() as conn:
            for p in DEFAULT_PROVIDERS:
                row = conn.execute("SELECT id FROM providers WHERE id = ?", (p["id"],)).fetchone()
                if not row:
                    conn.execute(
                        """
                        INSERT INTO providers (
                            id, name, capabilities_json, endpoint, auth_type, models_json,
                            declared_costs_json, quota_status, limits_doc, last_check,
                            operational_status, avg_latency_ms, recent_errors_json,
                            encrypted_secret, enabled, is_free_local, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'non_verificato', NULL, '[]', '', 1, ?, ?)
                        """,
                        (
                            p["id"],
                            p["name"],
                            json.dumps(p["capabilities"]),
                            p["endpoint"],
                            p["auth_type"],
                            json.dumps(p["models"]),
                            json.dumps(p["declared_costs"], ensure_ascii=False),
                            p["quota_status"],
                            p["limits_doc"],
                            1 if p["is_free_local"] else 0,
                            now,
                        ),
                    )
        self.refresh_local_statuses()

    def _get_env_var_name(self, provider_id: str) -> Optional[str]:
        for p in DEFAULT_PROVIDERS:
            if p["id"] == provider_id:
                return p.get("env_var")
        return None

    def set_api_key(self, provider_id: str, api_key: str) -> dict[str, Any]:
        encrypted = self.vault.encrypt(api_key.strip()) if api_key.strip() else ""
        with self.db.session() as conn:
            row = conn.execute("SELECT id FROM providers WHERE id = ?", (provider_id,)).fetchone()
            if not row:
                raise KeyError(f"Provider sconosciuto: {provider_id}")
            conn.execute(
                "UPDATE providers SET encrypted_secret = ?, updated_at = ? WHERE id = ?",
                (encrypted, utcnow_iso(), provider_id),
            )
        return self.check_provider(provider_id)

    def get_decrypted_key(self, provider_id: str) -> Optional[str]:
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT encrypted_secret FROM providers WHERE id = ?", (provider_id,)
            ).fetchone()
            if row and row["encrypted_secret"]:
                try:
                    val = self.vault.decrypt(row["encrypted_secret"])
                    if val:
                        return val
                except Exception:
                    return None
        env_var = self._get_env_var_name(provider_id)
        if env_var and os.environ.get(env_var):
            return os.environ.get(env_var)
        if provider_id == "google_veo_api":
            return self.get_decrypted_key("gemini_api")
        return None

    def refresh_local_statuses(self) -> None:
        now = utcnow_iso()
        with self.db.session() as conn:
            # 1. local_ffmpeg_pillow
            ff = find_ffmpeg()
            fp = find_ffprobe()
            ff_ok = bool(ff and fp)
            conn.execute(
                "UPDATE providers SET operational_status = ?, last_check = ?, avg_latency_ms = ? WHERE id = 'local_ffmpeg_pillow'",
                ("operativo" if ff_ok else "non_disponibile", now, 12.0 if ff_ok else None),
            )
            # 2. piper_tts_local
            from kora_studio.config import VOICES_PIPER_DIR

            has_onnx = VOICES_PIPER_DIR.is_dir() and any(VOICES_PIPER_DIR.glob("*.onnx"))
            try:
                import piper  # noqa: F401

                piper_pkg = True
            except Exception:
                piper_pkg = False
            piper_status = "operativo" if (piper_pkg and has_onnx) else (
                "modello_mancante" if piper_pkg else "non_disponibile"
            )
            conn.execute(
                "UPDATE providers SET operational_status = ?, last_check = ? WHERE id = 'piper_tts_local'",
                (piper_status, now),
            )
            # 3. remotion_local
            rem = check_remotion_runtime()
            conn.execute(
                "UPDATE providers SET operational_status = ?, last_check = ? WHERE id = 'remotion_local'",
                ("operativo" if rem["available"] else "non_disponibile", now),
            )
            # 4. External APIs: check key presence without inventing credits
            for p in DEFAULT_PROVIDERS:
                if p["is_free_local"]:
                    continue
                key = self.get_decrypted_key(p["id"])
                status = "configurato" if key else "non_configurato"
                conn.execute(
                    """
                    UPDATE providers
                    SET operational_status = CASE WHEN operational_status IN ('operativo','errore') AND ? != '' THEN operational_status ELSE ? END,
                        quota_status = CASE WHEN ? = '' THEN 'Non verificabile' ELSE quota_status END,
                        last_check = COALESCE(last_check, ?)
                    WHERE id = ?
                    """,
                    (key or "", status, key or "", now, p["id"]),
                )

    def check_provider(self, provider_id: str) -> dict[str, Any]:
        """Run a real operational/health probe for the given provider."""
        self.refresh_local_statuses()
        p = self.get_provider(provider_id)
        if not p:
            raise KeyError(f"Provider non trovato: {provider_id}")
        if p["is_free_local"]:
            return p

        key = self.get_decrypted_key(provider_id)
        now = utcnow_iso()
        if not key:
            with self.db.session() as conn:
                conn.execute(
                    "UPDATE providers SET operational_status = 'non_configurato', quota_status = 'Non verificabile', last_check = ? WHERE id = ?",
                    (now, provider_id),
                )
            return self.get_provider(provider_id)  # type: ignore

        t0 = time.monotonic()
        errors: list[str] = []
        status = "operativo"
        quota = "Non verificabile"
        try:
            if provider_id == "elevenlabs_api":
                r = requests.get(
                    "https://api.elevenlabs.io/v1/user/subscription",
                    headers={"xi-api-key": key},
                    timeout=8,
                )
                if r.status_code == 200:
                    d = r.json()
                    rem = int(d.get("character_limit", 0)) - int(d.get("character_count", 0))
                    quota = f"{max(0, rem)} caratteri residui"
                elif r.status_code in (401, 403):
                    status = "errore"
                    errors.append("API key ElevenLabs non valida (401/403)")
                else:
                    status = "errore"
                    errors.append(f"HTTP {r.status_code}")
            elif provider_id == "leonardo_ai":
                r = requests.get(
                    "https://cloud.leonardo.ai/api/rest/v1/me",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=8,
                )
                if r.status_code == 200:
                    details = (r.json().get("user_details") or [{}])[0]
                    tok = details.get("apiPaidTokens") or details.get("subscriptionTokens")
                    quota = f"{tok} token" if tok is not None else "Non verificabile"
                else:
                    status = "errore"
                    errors.append(f"HTTP {r.status_code}")
            elif provider_id == "openai_api":
                r = requests.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=8,
                )
                if r.status_code != 200:
                    status = "errore"
                    errors.append(f"HTTP {r.status_code}")
            elif provider_id in ("gemini_api", "google_veo_api"):
                r = requests.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    headers={"x-goog-api-key": key},
                    timeout=8,
                )
                if r.status_code in (401, 403):
                    status = "errore"
                    errors.append("API key Google Gemini non valida o senza permessi (401/403)")
                elif r.status_code != 200:
                    status = "errore"
                    errors.append(f"HTTP {r.status_code}")
            elif provider_id == "huggingface_api":
                r = requests.get(
                    "https://huggingface.co/api/whoami-v2",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=8,
                )
                if r.status_code in (401, 403):
                    status = "errore"
                    errors.append("Token Hugging Face non valido (401/403)")
                elif r.status_code != 200:
                    status = "errore"
                    errors.append(f"HTTP {r.status_code}")
        except Exception as exc:
            status = "errore"
            raw_err = str(exc)
            if key:
                raw_err = raw_err.replace(key, "***")
            if any(
                k in raw_err
                for k in (
                    "SSLZeroReturnError",
                    "SSLError",
                    "ConnectionError",
                    "NameResolutionError",
                    "Max retries exceeded",
                    "ConnectTimeout",
                )
            ):
                errors.append(
                    "Connessione esterna bloccata da rete/firewall o sandbox (chiave salvata e cifrata; verifica in locale sul tuo PC)."
                )
            else:
                errors.append(raw_err[:180])

        latency = round((time.monotonic() - t0) * 1000, 1)
        with self.db.session() as conn:
            conn.execute(
                """
                UPDATE providers
                SET operational_status = ?, quota_status = ?, last_check = ?,
                    avg_latency_ms = ?, recent_errors_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, quota, now, latency, json.dumps(errors, ensure_ascii=False), now, provider_id),
            )
        return self.get_provider(provider_id)  # type: ignore

    def setup_remotion(self) -> dict[str, Any]:
        """Attempt to install/verify Remotion dependencies and headless Chromium."""
        import shutil
        import subprocess
        from kora_studio.config import REPO_ROOT

        composer_dir = REPO_ROOT / "remotion-composer"
        logs: list[str] = []
        rem = check_remotion_runtime()

        npm_bin = shutil.which("npm")
        if not rem["remotion_bin"] and npm_bin and composer_dir.is_dir():
            proc = subprocess.run(
                [npm_bin, "install", "--no-audit", "--no-fund"],
                cwd=composer_dir,
                capture_output=True,
                text=True,
                timeout=180,
            )
            logs.append(f"npm install -> exit {proc.returncode}")
            if proc.returncode != 0:
                logs.append((proc.stderr or proc.stdout)[-240:])

        rem = check_remotion_runtime()
        if rem["remotion_bin"] and not rem["chromium"]:
            proc = subprocess.run(
                [rem["remotion_bin"], "browser", "ensure"],
                cwd=composer_dir,
                capture_output=True,
                text=True,
                timeout=180,
            )
            logs.append(f"remotion browser ensure -> exit {proc.returncode}")
            if proc.returncode != 0:
                logs.append(
                    "Impossibile scaricare Chrome Headless Shell in questo ambiente (rete esterna bloccata o firewall). "
                    "In locale sul tuo PC esegui: cd remotion-composer && npx remotion browser ensure (oppure installa Google Chrome)."
                )

        self.refresh_local_statuses()
        final_rem = check_remotion_runtime()
        prov = self.get_provider("remotion_local")
        return {"remotion": final_rem, "provider": prov, "logs": logs}

    def install_piper_italian_voice(self) -> dict[str, Any]:
        """Download free offline Italian Piper TTS voice model (it_IT-paola-medium) into kora_data/voices/piper/."""
        from kora_studio.config import VOICES_PIPER_DIR

        VOICES_PIPER_DIR.mkdir(parents=True, exist_ok=True)
        base_url = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/it/it_IT/paola/medium"
        files = {
            "it_IT-paola-medium.onnx": f"{base_url}/it_IT-paola-medium.onnx",
            "it_IT-paola-medium.onnx.json": f"{base_url}/it_IT-paola-medium.onnx.json",
        }
        downloaded: list[str] = []
        for fname, url in files.items():
            dest = VOICES_PIPER_DIR / fname
            if dest.is_file() and dest.stat().st_size > 100:
                downloaded.append(fname)
                continue
            r = requests.get(url, timeout=120, stream=True)
            if r.status_code != 200:
                raise RuntimeError(f"Download fallito ({r.status_code}) per {fname}")
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)
            downloaded.append(fname)

        self.refresh_local_statuses()
        return {
            "ok": True,
            "files": downloaded,
            "directory": str(VOICES_PIPER_DIR),
            "provider": self.get_provider("piper_tts_local"),
        }

    def fetch_elevenlabs_voices(self) -> list[dict[str, Any]]:
        """Fetch real voice list from user's configured ElevenLabs account."""
        key = self.get_decrypted_key("elevenlabs_api")
        if not key:
            raise ValueError("Inserisci e salva prima la tua API key ElevenLabs.")
        r = requests.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": key},
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Errore ElevenLabs ({r.status_code}): impossibile leggere le voci.")
        data = r.json()
        return [
            {
                "voice_id": v.get("voice_id", ""),
                "name": v.get("name", ""),
                "category": v.get("category", ""),
            }
            for v in data.get("voices", [])
            if v.get("voice_id")
        ]

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        has_key = False
        masked = ""
        if not row["is_free_local"]:
            key = self.get_decrypted_key(row["id"])
            if key:
                has_key = True
                masked = self.vault.mask(key)
        return {
            "id": row["id"],
            "name": row["name"],
            "capabilities": json.loads(row["capabilities_json"]),
            "endpoint": row["endpoint"],
            "auth_type": row["auth_type"],
            "models": json.loads(row["models_json"]),
            "declared_costs": json.loads(row["declared_costs_json"]),
            "quota_status": row["quota_status"],
            "limits_doc": row["limits_doc"],
            "last_check": row["last_check"],
            "operational_status": row["operational_status"],
            "avg_latency_ms": row["avg_latency_ms"],
            "recent_errors": json.loads(row["recent_errors_json"]),
            "has_api_key": has_key,
            "masked_api_key": masked,
            "enabled": bool(row["enabled"]),
            "is_free_local": bool(row["is_free_local"]),
            "updated_at": row["updated_at"],
        }

    def list_providers(self) -> list[dict[str, Any]]:
        self.refresh_local_statuses()
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM providers ORDER BY is_free_local DESC, name ASC"
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_provider(self, provider_id: str) -> Optional[dict[str, Any]]:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM providers WHERE id = ?", (provider_id,)).fetchone()
            return self._row_to_dict(row) if row else None

    def select_provider_for(
        self,
        capability: str,
        estimated_eur: float = 0.0,
        explicit_user_authorization: bool = False,
    ) -> dict[str, Any]:
        """Follow Section 8 automatic strategy:
        1. Prefer free local solution first.
        2. Then free-tier configured providers.
        3. Then cheapest authorized paid provider (only if budget & authorization allow).
        """
        providers = [
            p
            for p in self.list_providers()
            if p["enabled"] and capability in p["capabilities"]
        ]
        # 1. Local free operational providers
        for p in providers:
            if p["is_free_local"] and p["operational_status"] == "operativo":
                return {"selected": p, "cost_eur": 0.0, "authorized": True, "strategy": "local_free"}

        # 2. Configured external providers
        configured = [
            p
            for p in providers
            if not p["is_free_local"] and p["operational_status"] in ("operativo", "configurato")
        ]
        for p in configured:
            auth = self.cost_guard.check_authorization(estimated_eur, explicit_user_authorization)
            if auth["authorized"]:
                return {
                    "selected": p,
                    "cost_eur": estimated_eur,
                    "authorized": True,
                    "strategy": "external_authorized",
                }
            return {
                "selected": None,
                "candidate": p,
                "cost_eur": estimated_eur,
                "authorized": False,
                "reason": auth["reason"],
                "strategy": "blocked_by_budget",
            }

        return {
            "selected": None,
            "cost_eur": 0.0,
            "authorized": False,
            "reason": f"Nessun provider operativo disponibile per '{capability}'.",
            "strategy": "unavailable",
        }
