"""Authorized Sound & Music Library for KORA STUDIO (CC0 & licensed assets)."""

from __future__ import annotations

import json
import shutil
import wave
from pathlib import Path
from typing import Any, Optional

import numpy as np

from kora_studio.config import ASSETS_KORA_DIR, SOUND_LIBRARY_DIR, ensure_directories
from kora_studio.db.database import Database, utcnow_iso
from kora_studio.media_tools import probe_media

SOUND_CATEGORIES = [
    "onde",
    "vento",
    "ambiente_spiaggia",
    "tazzine",
    "bicchieri",
    "passi",
    "tessuti",
    "movimenti_camera",
    "transizioni",
    "atmosfere",
    "musica_ambient",
    "musica_cinematic",
]


def _write_wav(path: Path, samples: np.ndarray, sr: int = 48000) -> float:
    """Write normalized stereo float32 [-1, 1] array to 16-bit PCM WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if samples.ndim == 1:
        samples = np.stack([samples, samples], axis=1)
    peak = float(np.max(np.abs(samples))) if samples.size else 1.0
    if peak > 0.92:
        samples = samples * (0.85 / peak)
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return float(len(samples) / sr)


def _synth_coastal_sfx(category: str, dest: Path, sr: int = 48000) -> float:
    """Synthesize clean, subtle CC0-local acoustic/coastal foley WAV assets."""
    rng = np.random.default_rng(abs(hash(category)) % (2**31))

    if category == "onde":
        dur = 6.0
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        noise = rng.normal(0, 1, len(t))
        # Simple 1-pole lowpass to sound like soft surf
        filtered = np.zeros_like(noise)
        alpha = 0.045
        for i in range(1, len(noise)):
            filtered[i] = filtered[i - 1] + alpha * (noise[i] - filtered[i - 1])
        swell = 0.25 + 0.55 * (0.5 - 0.5 * np.cos(2 * np.pi * t / 3.0))
        sig = filtered * swell * 0.45
        return _write_wav(dest, sig, sr)

    if category == "vento":
        dur = 5.0
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        noise = rng.normal(0, 1, len(t))
        filtered = np.zeros_like(noise)
        for i in range(1, len(noise)):
            filtered[i] = filtered[i - 1] + 0.028 * (noise[i] - filtered[i - 1])
        gust = 0.3 + 0.5 * np.sin(2 * np.pi * t / 5.0) ** 2
        return _write_wav(dest, filtered * gust * 0.32, sr)

    if category == "ambiente_spiaggia":
        dur = 6.0
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        noise = rng.normal(0, 1, len(t))
        surf = np.zeros_like(noise)
        for i in range(1, len(noise)):
            surf[i] = surf[i - 1] + 0.038 * (noise[i] - surf[i - 1])
        env = 0.3 + 0.3 * np.sin(2 * np.pi * t / 4.2) ** 2
        return _write_wav(dest, surf * env * 0.3, sr)

    if category == "tazzine":
        dur = 1.6
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        sig = np.zeros_like(t)
        # Subtle ceramic porcelain clink around 0.35s
        mask = t >= 0.35
        dt = t[mask] - 0.35
        clink = (
            0.5 * np.sin(2 * np.pi * 2420 * dt) * np.exp(-dt * 28.0)
            + 0.35 * np.sin(2 * np.pi * 3690 * dt) * np.exp(-dt * 36.0)
            + 0.2 * np.sin(2 * np.pi * 5100 * dt) * np.exp(-dt * 45.0)
        )
        sig[mask] = clink * 0.38
        return _write_wav(dest, sig, sr)

    if category == "bicchieri":
        dur = 1.8
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        sig = np.zeros_like(t)
        for offset, freq in [(0.3, 3180), (0.36, 4250)]:
            m = t >= offset
            dt = t[m] - offset
            sig[m] += (
                0.45 * np.sin(2 * np.pi * freq * dt) * np.exp(-dt * 22.0)
                + 0.25 * np.sin(2 * np.pi * (freq * 1.41) * dt) * np.exp(-dt * 30.0)
            )
        return _write_wav(dest, sig * 0.35, sr)

    if category == "passi":
        dur = 2.4
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        sig = np.zeros_like(t)
        for step_t in (0.35, 1.05, 1.75):
            m = (t >= step_t) & (t < step_t + 0.18)
            dt = t[m] - step_t
            burst = rng.normal(0, 1, len(dt)) * np.exp(-dt * 24.0)
            thud = np.sin(2 * np.pi * 110 * dt) * np.exp(-dt * 22.0)
            sig[m] = (burst * 0.25 + thud * 0.35) * 0.45
        return _write_wav(dest, sig, sr)

    if category == "tessuti":
        dur = 1.8
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        noise = rng.normal(0, 1, len(t))
        bp = np.diff(noise, prepend=0.0) * 0.5
        env = np.exp(-((t - 0.7) ** 2) / 0.08)
        return _write_wav(dest, bp * env * 0.22, sr)

    if category in ("movimenti_camera", "transizioni"):
        dur = 1.4
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        noise = rng.normal(0, 1, len(t))
        lp = np.zeros_like(noise)
        for i in range(1, len(noise)):
            lp[i] = lp[i - 1] + 0.06 * (noise[i] - lp[i - 1])
        env = np.sin(np.pi * t / dur) ** 2
        return _write_wav(dest, lp * env * 0.24, sr)

    if category == "atmosfere":
        dur = 8.0
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        pad = (
            0.35 * np.sin(2 * np.pi * 146.83 * t)
            + 0.25 * np.sin(2 * np.pi * 220.0 * t)
            + 0.20 * np.sin(2 * np.pi * 293.66 * t)
        )
        env = np.minimum(1.0, t / 1.5) * np.minimum(1.0, (dur - t) / 1.5)
        return _write_wav(dest, pad * env * 0.22, sr)

    if category == "musica_ambient":
        dur = 22.0
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        # Warm Dmaj9 Mediterranean ambient chord progression
        chords = [
            (146.83, 220.00, 277.18, 329.63),
            (130.81, 196.00, 246.94, 293.66),
            (110.00, 164.81, 220.00, 277.18),
            (146.83, 220.00, 293.66, 369.99),
        ]
        sig = np.zeros_like(t)
        seg = dur / len(chords)
        for idx, freqs in enumerate(chords):
            start = idx * seg
            end = (idx + 1) * seg
            m = (t >= start) & (t < end)
            lt = t[m] - start
            w = np.sin(np.pi * lt / seg) ** 0.7
            chord_sig = sum(np.sin(2 * np.pi * f * t[m]) for f in freqs) / len(freqs)
            sig[m] = chord_sig * w
        fade = np.minimum(1.0, t / 1.5) * np.minimum(1.0, (dur - t) / 2.0)
        return _write_wav(dest, sig * fade * 0.25, sr)

    # musica_cinematic fallback
    dur = 22.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    sig = (
        0.4 * np.sin(2 * np.pi * 130.81 * t)
        + 0.3 * np.sin(2 * np.pi * 196.00 * t)
        + 0.2 * np.sin(2 * np.pi * 246.94 * t)
    )
    fade = np.minimum(1.0, t / 1.5) * np.minimum(1.0, (dur - t) / 2.0)
    return _write_wav(dest, sig * fade * 0.25, sr)


class SoundLibrary:
    """Manages categorized, license-verified SFX and music assets."""

    def __init__(self, db: Database) -> None:
        self.db = db
        ensure_directories()
        self.ensure_seeded()

    def ensure_seeded(self) -> None:
        with self.db.session() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM sound_library").fetchone()["c"]
        if count > 0:
            return

        # 1. Verified CC0 music track from KORA archive (Next to You - Joth)
        cc0_music = ASSETS_KORA_DIR / "music" / "next_to_you_joth_cc0.mp3"
        if cc0_music.is_file():
            dest = SOUND_LIBRARY_DIR / "next_to_you_joth_cc0.mp3"
            shutil.copy2(cc0_music, dest)
            try:
                probe = probe_media(dest)
                dur = float(probe["format"].get("duration", 45.0) or 45.0)
            except Exception:
                dur = 45.0
            self.register_sound(
                sound_id="mus_next_to_you_cc0",
                name="Next to You — Joth (CC0)",
                category="musica_cinematic",
                file_path=dest,
                duration_sec=dur,
                license="CC0 1.0 Public Domain",
                source="opengameart.org/content/next-to-you",
                tags=["cinematic", "pianoforte", "delicato", "mediterraneo", "emozionante"],
                mood="caldo e contemplativo",
            )

        # 2. Clean CC0-local acoustic/coastal foley & ambient music for every category
        presets = [
            ("sfx_onde_ionio", "Onde morbide sullo Ionio", "onde", ["mare", "onde", "spiaggia", "acqua"], "rilassante"),
            ("sfx_vento_pergolato", "Brezza leggera sul pergolato", "vento", ["vento", "brezza", "pergolato", "pomeriggio"], "ariosa"),
            ("sfx_ambiente_spiaggia", "Ambiente spiaggia mattino", "ambiente_spiaggia", ["spiaggia", "mattino", "mare", "quiete"], "naturale"),
            ("sfx_tazzina_porcellana", "Tazzina colazione al tavolo", "tazzine", ["colazione", "caffè", "tazzina", "gesto"], "intimo"),
            ("sfx_bicchiere_aperitivo", "Calice aperitivo al tramonto", "bicchieri", ["aperitivo", "bicchiere", "spritz", "tramonto"], "conviviale"),
            ("sfx_passi_sabbia", "Passi leggeri verso il mare", "passi", ["passi", "sabbia", "legno", "arrivo"], "discreto"),
            ("sfx_tessuti_parasole", "Lino e frange nella brezza", "tessuti", ["tessuti", "ombrelloni", "tende", "parasole"], "delicato"),
            ("sfx_camera_push", "Movimento camera morbido", "movimenti_camera", ["camera", "push", "pan"], "pulito"),
            ("sfx_transizione_aria", "Transizione d'aria morbida", "transizioni", ["transizione", "dissolvenza", "raccordo"], "fluido"),
            ("sfx_atmosfera_sera", "Atmosfera serale mediterranea", "atmosfere", ["tramonto", "sera", "luce dorata"], "avvolgente"),
            ("mus_mediterranean_drift", "Luce sul Mare — Ambient (CC0 Local)", "musica_ambient", ["ambient", "calmo", "mattino", "colazione"], "sereno"),
        ]
        for sid, name, cat, tags, mood in presets:
            dest = SOUND_LIBRARY_DIR / f"{sid}.wav"
            dur = _synth_coastal_sfx(cat, dest)
            self.register_sound(
                sound_id=sid,
                name=name,
                category=cat,
                file_path=dest,
                duration_sec=dur,
                license="CC0 1.0 (KORA Local Foley Synth)",
                source="kora_sound_library_synth",
                tags=tags,
                mood=mood,
            )

    def register_sound(
        self,
        sound_id: str,
        name: str,
        category: str,
        file_path: Path,
        duration_sec: float,
        license: str,
        source: str,
        tags: list[str],
        mood: str = "",
        bpm: Optional[int] = None,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sound_library
                (id, name, category, file_path, duration_sec, license, source, tags_json, bpm, mood, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sound_id,
                    name,
                    category,
                    str(file_path),
                    round(duration_sec, 3),
                    license,
                    source,
                    json.dumps(tags, ensure_ascii=False),
                    bpm,
                    mood,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM sound_library WHERE id = ?", (sound_id,)).fetchone()
            return self._row_to_dict(row)

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "category": row["category"],
            "file_path": row["file_path"],
            "duration_sec": row["duration_sec"],
            "license": row["license"],
            "source": row["source"],
            "tags": json.loads(row["tags_json"]),
            "bpm": row["bpm"],
            "mood": row["mood"],
            "created_at": row["created_at"],
        }

    def list_sounds(self, category: Optional[str] = None) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM sound_library WHERE category = ? ORDER BY name", (category,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sound_library ORDER BY category, name"
                ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_sound(self, sound_id: str) -> Optional[dict[str, Any]]:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM sound_library WHERE id = ?", (sound_id,)).fetchone()
            return self._row_to_dict(row) if row else None
