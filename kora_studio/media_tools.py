"""FFmpeg / FFprobe / Remotion runtime discovery and media probing utilities."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from kora_studio.config import REPO_ROOT


def find_ffmpeg() -> Optional[str]:
    """Locate ffmpeg executable reliably across virtualenv, PATH, and imageio-ffmpeg."""
    candidates = [
        shutil.which("ffmpeg"),
        str(Path(sys.executable).parent / "ffmpeg"),
        str(REPO_ROOT / ".venv" / "bin" / "ffmpeg"),
        str(REPO_ROOT / ".venv" / "Scripts" / "ffmpeg.exe"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return exe
    except Exception:
        pass
    return None


def find_ffprobe() -> Optional[str]:
    """Locate ffprobe executable reliably across virtualenv, PATH, and .runtime."""
    candidates = [
        shutil.which("ffprobe"),
        str(Path(sys.executable).parent / "ffprobe"),
        str(REPO_ROOT / ".venv" / "bin" / "ffprobe"),
        str(REPO_ROOT / ".venv" / "Scripts" / "ffprobe.exe"),
        str(
            REPO_ROOT
            / ".runtime"
            / "ffprobe"
            / "node_modules"
            / "ffprobe-static"
            / "bin"
            / "linux"
            / "x64"
            / "ffprobe"
        ),
        str(
            REPO_ROOT
            / ".runtime"
            / "ffprobe"
            / "node_modules"
            / "ffprobe-static"
            / "bin"
            / "darwin"
            / "arm64"
            / "ffprobe"
        ),
        str(
            REPO_ROOT
            / ".runtime"
            / "ffprobe"
            / "node_modules"
            / "ffprobe-static"
            / "bin"
            / "darwin"
            / "x64"
            / "ffprobe"
        ),
        str(
            REPO_ROOT
            / ".runtime"
            / "ffprobe"
            / "node_modules"
            / "ffprobe-static"
            / "bin"
            / "win32"
            / "x64"
            / "ffprobe.exe"
        ),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


def check_remotion_runtime() -> dict[str, Any]:
    """Check whether Remotion and headless Chromium are usable on this host."""
    composer_dir = REPO_ROOT / "remotion-composer"
    node_bin = shutil.which("node")
    remotion_bin = composer_dir / "node_modules" / ".bin" / "remotion"
    chrome_bin = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
    )
    available = bool(node_bin and remotion_bin.is_file() and chrome_bin)
    reasons = []
    if not node_bin:
        reasons.append("Node.js non trovato")
    if not remotion_bin.is_file():
        reasons.append("Dipendenze remotion-composer non installate")
    if not chrome_bin:
        reasons.append("Browser Chromium headless non presente su questo ambiente")
    return {
        "available": available,
        "node": node_bin,
        "remotion_bin": str(remotion_bin) if remotion_bin.is_file() else None,
        "chromium": chrome_bin,
        "reasons": reasons,
    }


def probe_media(path: Path) -> dict[str, Any]:
    """Inspect video/audio file metadata using ffprobe (or PyAV fallback)."""
    ffprobe = find_ffprobe()
    if ffprobe:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise ValueError(f"File non decodificabile con ffprobe: {proc.stderr.strip()}")
        data = json.loads(proc.stdout)
        streams = data.get("streams", [])
        fmt = data.get("format", {})
        if not streams:
            raise ValueError("Nessuno stream multimediale trovato nel file")
        return {"streams": streams, "format": fmt}

    # Fallback to PyAV if ffprobe binary is missing
    try:
        import av

        with av.open(str(path)) as container:
            streams_out = []
            for s in container.streams:
                entry: dict[str, Any] = {
                    "codec_type": s.type,
                    "codec_name": s.codec_context.name if s.codec_context else "",
                }
                if s.type == "video":
                    entry["width"] = s.width
                    entry["height"] = s.height
                    entry["r_frame_rate"] = str(s.average_rate or s.guessed_rate or "25/1")
                elif s.type == "audio":
                    entry["sample_rate"] = str(s.sample_rate or 48000)
                    entry["channels"] = s.channels or 2
                streams_out.append(entry)
            dur = float(container.duration / 1_000_000) if container.duration else 0.0
            return {
                "streams": streams_out,
                "format": {"duration": str(dur), "size": str(path.stat().st_size)},
            }
    except Exception as exc:
        raise ValueError(f"Impossibile analizzare il file multimediale: {exc}") from exc


def parse_fps(rate_str: str) -> float:
    if not rate_str or rate_str == "0/0":
        return 0.0
    if "/" in rate_str:
        num, den = rate_str.split("/", 1)
        d = float(den)
        return float(num) / d if d else 0.0
    return float(rate_str)


def run_ffmpeg(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg non disponibile nel sistema.")
    env = os.environ.copy()
    bin_dir = str(Path(ffmpeg).parent)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run(
        [ffmpeg, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Errore FFmpeg ({proc.returncode}): {proc.stderr[-1200:]}")
    return proc
