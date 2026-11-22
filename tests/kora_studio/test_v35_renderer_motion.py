"""v3.5 — i parametri di regia cambiano davvero il renderer.

Non è una verifica di gusto: due clip generate dalla stessa fotografia con
parametri diversi devono produrre pixel diversi, altrimenti i controlli della
Shot Director sarebbero decorativi.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from kora_studio.agents.ai_media_lab import AIMediaLab, motion_expressions


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_expressions_change_with_direction_parameters():
    base = motion_expressions("slow_push_in", 50, intensity=1.0, easing="linear", speed=1.0)
    stronger = motion_expressions("slow_push_in", 50, intensity=1.8, easing="linear", speed=1.0)
    eased = motion_expressions("slow_push_in", 50, intensity=1.0, easing="ease_out", speed=1.0)
    faster = motion_expressions("slow_push_in", 50, intensity=1.0, easing="linear", speed=1.6)
    thirds = motion_expressions("slow_push_in", 50, intensity=1.0, easing="linear", composition="regola_terzi")
    centre = motion_expressions("slow_push_in", 50, intensity=1.0, easing="linear", composition="centro")

    assert base[0] != stronger[0], "intensità ignorata"
    assert base[1] == thirds[1] != centre[1], "composizione ignorata"
    assert eased[0] != base[0] or eased[1] != base[1], "curva ignorata"
    assert "1.600" in faster[0], "velocità ignorata"


def test_rendered_clip_changes_with_intensity(tmp_path: Path):
    lab = AIMediaLab.__new__(AIMediaLab)  # nessuna dipendenza dal vault per il solo animatore
    photo = tmp_path / "foto.png"
    Image.new("RGB", (720, 1280), (60, 90, 120)).save(photo)

    soft = lab.animate_photo_deterministic(
        photo,
        tmp_path / "soft.mp4",
        duration_frames=25,
        motion="slow_push_in",
        intensity=0.4,
        easing="ease_in_out",
    )
    strong = lab.animate_photo_deterministic(
        photo,
        tmp_path / "strong.mp4",
        duration_frames=25,
        motion="slow_push_in",
        intensity=1.9,
        easing="ease_out",
    )

    assert soft.is_file() and strong.is_file()
    assert soft.stat().st_size > 1000 and strong.stat().st_size > 1000
    assert _sha(soft) != _sha(strong), "stessi pixel: i controlli di regia non arrivano al renderer"
