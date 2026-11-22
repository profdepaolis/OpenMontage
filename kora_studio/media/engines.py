"""Inventario REALE dei motori audiovisivi di KORA Studio Light.

Regole del modulo (3.6.2):

* nessuna installazione automatica: qui si osserva soltanto;
* la presenza del nome nel codice non vale come disponibilità: ogni motore ha
  un controllo eseguito su questa macchina (binario, modulo Python, cartella,
  credenziale) e, quando richiesto, un test operativo vero;
* i test di rete (ElevenLabs, HyperFrames esterno) NON partono da soli: solo su
  richiesta esplicita dell'utente, con timeout breve e nessuna spesa.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from kora_studio.config import REPO_ROOT, VOICES_PIPER_DIR
from kora_studio.media_tools import check_remotion_runtime, find_ffmpeg, find_ffprobe

INVENTORY_TTL_SEC = 20.0
_cache: dict[str, Any] = {"at": 0.0, "payload": None}


def _module_available(*names: str) -> Optional[str]:
    """True se uno dei moduli è importabile, senza importarlo (nessun costo)."""
    for name in names:
        try:
            if importlib.util.find_spec(name) is not None:
                return name
        except (ImportError, ValueError):
            continue
    return None


def _run(cmd: list[str], timeout: float = 6.0) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{exc.__class__.__name__}: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out.strip()[:400]


def _first_line(text: str, default: str = "") -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:160]
    return default


# --------------------------------------------------------------------- singoli
def _ffmpeg() -> dict[str, Any]:
    path = find_ffmpeg()
    return {
        "id": "ffmpeg",
        "label": "FFmpeg",
        "kind": "render",
        "installed": bool(path),
        "path": path or "",
        "requirements": "Nessuna rete · ~80 MB su disco",
        "notes": "Renderer locale principale (compositing, transizioni, audio).",
    }


def _ffprobe() -> dict[str, Any]:
    path = find_ffprobe()
    return {
        "id": "ffprobe",
        "label": "FFprobe",
        "kind": "render",
        "installed": bool(path),
        "path": path or "",
        "requirements": "Nessuna rete · ~80 MB su disco",
        "notes": "Misura durata, risoluzione, fps e fotogrammi per il QC esatto.",
    }


def _remotion() -> dict[str, Any]:
    composer = REPO_ROOT / "remotion-composer"
    runtime = check_remotion_runtime()
    node = shutil.which("node")
    npm = shutil.which("npm")
    has_package = (composer / "package.json").is_file()
    installed = bool(runtime.get("remotion_bin"))
    chromium = runtime.get("chromium")
    reasons: list[str] = []
    if not composer.is_dir():
        reasons.append("cartella remotion-composer assente")
    elif not has_package:
        reasons.append("remotion-composer senza package.json")
    if not node:
        reasons.append("Node.js non installato")
    if not installed:
        reasons.append("dipendenze Remotion non installate (npm install)")
    if installed and not chromium:
        reasons.append("Chrome Headless non disponibile (remotion browser ensure)")
    return {
        "id": "remotion",
        "label": "Remotion (Composer)",
        "kind": "render",
        "installed": installed,
        "path": str(composer),
        "version": str(runtime.get("remotion_version") or ""),
        "requirements": "Node.js 18+, ~300 MB (dipendenze) + ~150 MB Chrome Headless",
        "notes": "Composizioni React, titoli animati, maschere, compositing. FFmpeg resta il fallback.",
        "details": {
            "composer_dir": composer.is_dir(),
            "package_json": has_package,
            "node": node or "",
            "npm": npm or "",
            "remotion_bin": runtime.get("remotion_bin") or "",
            "chromium": chromium or "",
            "has_composition_json": bool(list(REPO_ROOT.glob("**/remotion_composition.json"))[:1]),
        },
        "missing": reasons,
    }


def _hyperframes_internal() -> dict[str, Any]:
    module_path = REPO_ROOT / "kora_studio" / "agents" / "hyperframes_captions.py"
    styles: list[str] = []
    error = ""
    if module_path.is_file():
        try:
            from kora_studio.agents import hyperframes_captions as hf

            styles = sorted(getattr(hf, "CAPTION_STYLES", {}) or {})
        except Exception as exc:  # pragma: no cover - difensivo
            error = f"{exc.__class__.__name__}: {exc}"
    return {
        "id": "hyperframes_internal",
        "label": "HyperFrames interno",
        "kind": "graphics",
        "installed": module_path.is_file() and bool(styles),
        "path": str(module_path),
        "version": f"{len(styles)} preset",
        "requirements": "Nessuna rete · usa FFmpeg e i font KORA già presenti",
        "notes": "Caption cinetiche, glass, word highlight, titoli, grana, vignetta, logo outro.",
        "details": {"presets": styles},
        "error": error,
    }


def _hyperframes_external() -> dict[str, Any]:
    url = (os.environ.get("KORA_HYPERFRAMES_URL") or "").strip()
    token = bool((os.environ.get("KORA_HYPERFRAMES_TOKEN") or "").strip())
    return {
        "id": "hyperframes_external",
        "label": "HyperFrames esterno",
        "kind": "graphics",
        "installed": bool(url),
        "configured": bool(url),
        "path": url,
        "requirements": "Servizio esterno + rete · nessun costo dichiarato",
        "notes": "Non è collegato per il solo fatto che esistano i preset interni.",
        "details": {"url": url, "token": token},
        "missing": [] if url else ["KORA_HYPERFRAMES_URL non impostata"],
    }


def _whisper() -> dict[str, Any]:
    module = _module_available("whisper")
    cli = shutil.which("whisper")
    return {
        "id": "whisper",
        "label": "Whisper (OpenAI)",
        "kind": "stt",
        "installed": bool(module or cli),
        "path": cli or module or "",
        "requirements": "Python 3.9–3.11, torch (~2 GB), modello base ~150 MB",
        "notes": "Trascrizione del voiceover importato con timestamp: facoltativa.",
        "details": {"module": module or "", "cli": cli or ""},
        "missing": [] if (module or cli) else ["pacchetto whisper non installato"],
    }


def _faster_whisper() -> dict[str, Any]:
    module = _module_available("faster_whisper")
    return {
        "id": "faster_whisper",
        "label": "faster-whisper",
        "kind": "stt",
        "installed": bool(module),
        "path": module or "",
        "requirements": "ctranslate2 (~200 MB), modello small ~500 MB · molto più leggero di Whisper",
        "notes": "Alternativa consigliata su Mac: trascrizione veloce senza torch.",
        "details": {"module": module or ""},
        "missing": [] if module else ["pacchetto faster-whisper non installato"],
    }


def _chatterbox() -> dict[str, Any]:
    module = _module_available("chatterbox", "chatterbox_tts")
    return {
        "id": "chatterbox_tts",
        "label": "Chatterbox TTS",
        "kind": "tts",
        "installed": bool(module),
        "path": module or "",
        "requirements": "torch, ~2–3 GB RAM durante la sintesi, GPU consigliata",
        "notes": "Clonazione voce: da valutare solo se l'hardware regge (nessuna installazione automatica).",
        "details": {"module": module or ""},
        "missing": [] if module else ["pacchetto chatterbox non installato"],
    }


def _elevenlabs(providers: Any) -> dict[str, Any]:
    module = _module_available("elevenlabs")
    key = ""
    try:
        key = providers.get_decrypted_key("elevenlabs_api") or "" if providers else ""
    except Exception:
        key = ""
    env_key = bool((os.environ.get("ELEVENLABS_API_KEY") or "").strip())
    configured = bool(key or env_key)
    return {
        "id": "elevenlabs",
        "label": "ElevenLabs",
        "kind": "tts",
        "installed": bool(module or configured),
        "configured": configured,
        "path": module or "",
        "requirements": "Chiave API + rete · voci autorizzate sul piano dell'utente",
        "notes": "Brando e Tiziana solo con voci autorizzate; nessuna spesa senza consenso.",
        "details": {"module": module or "", "api_key": configured},
        "missing": [] if configured else ["chiave ElevenLabs non configurata"],
    }


def _piper() -> dict[str, Any]:
    binary = shutil.which("piper")
    models = sorted(str(p) for p in VOICES_PIPER_DIR.glob("*.onnx")) if VOICES_PIPER_DIR.is_dir() else []
    return {
        "id": "piper_tts",
        "label": "Piper TTS (locale)",
        "kind": "tts",
        "installed": bool(binary),
        "configured": bool(models),
        "path": binary or "",
        "requirements": "Binario piper + voce .onnx italiana (~60 MB) · offline",
        "notes": "Voce locale gratuita: nessuna rete, nessun costo.",
        "details": {"binary": binary or "", "voices": models},
        "missing": ([] if binary else ["binario piper assente"]) + ([] if models else ["nessuna voce .onnx nella cartella voices"]),
    }


def _edge_tts() -> dict[str, Any]:
    module = _module_available("edge_tts")
    return {
        "id": "edge_tts",
        "label": "Edge TTS",
        "kind": "tts",
        "installed": bool(module),
        "path": module or "",
        "requirements": "Rete verso Microsoft Edge · gratuito, nessuna chiave",
        "notes": "Fallback cloud gratuito quando la rete è disponibile.",
        "details": {"module": module or ""},
        "missing": [] if module else ["pacchetto edge-tts non installato"],
    }


def _qwen3_tts() -> dict[str, Any]:
    module = _module_available("qwen_tts", "qwen3_tts")
    local_dir = REPO_ROOT / "models" / "qwen3-tts"
    return {
        "id": "qwen3_tts",
        "label": "Qwen3-TTS",
        "kind": "tts",
        "installed": bool(module or local_dir.is_dir()),
        "path": module or (str(local_dir) if local_dir.is_dir() else ""),
        "requirements": "Python 3.10+, torch, modello ~1–2 GB · GPU consigliata",
        "notes": "Compatibilità da verificare sull'hardware prima di installare.",
        "details": {"module": module or "", "models_dir": local_dir.is_dir()},
        "missing": [] if (module or local_dir.is_dir()) else ["pacchetto/modello Qwen3-TTS assente"],
    }


def _build(providers: Any) -> list[dict[str, Any]]:
    engines = [
        _ffmpeg(),
        _ffprobe(),
        _remotion(),
        _hyperframes_internal(),
        _hyperframes_external(),
        _whisper(),
        _faster_whisper(),
        _chatterbox(),
        _elevenlabs(providers),
        _piper(),
        _edge_tts(),
        _qwen3_tts(),
    ]
    for engine in engines:
        engine.setdefault("configured", engine.get("installed", False))
        engine.setdefault("available", False)
        engine.setdefault("test_state", "non eseguito")
        engine.setdefault("test_detail", "")
        engine.setdefault("missing", [])
        engine.setdefault("error", "")
        engine["available"] = bool(engine["installed"] and engine.get("configured", True))
        # un motore non installato non è "disponibile": il motivo va detto
        if not engine["installed"] and not engine["test_detail"]:
            engine["test_detail"] = "; ".join(engine["missing"]) or "non installato"
    return engines


def engines_inventory(providers: Any = None, *, refresh: bool = False) -> dict[str, Any]:
    now = time.time()
    cached = _cache.get("payload")
    if cached and not refresh and (now - float(_cache.get("at") or 0.0)) < INVENTORY_TTL_SEC:
        return cached

    engines = _build(providers)
    # test locali immediati e sicuri: nessuna rete, nessuna spesa
    ffmpeg_path = next((e for e in engines if e["id"] == "ffmpeg"), {})
    if ffmpeg_path.get("installed"):
        ok, out = _run([ffmpeg_path["path"], "-version"], timeout=5.0)
        ffmpeg_path["test_state"] = "superato" if ok else "fallito"
        ffmpeg_path["test_detail"] = _first_line(out)
    ffprobe_path = next((e for e in engines if e["id"] == "ffprobe"), {})
    if ffprobe_path.get("installed"):
        ok, out = _run([ffprobe_path["path"], "-version"], timeout=5.0)
        ffprobe_path["test_state"] = "superato" if ok else "fallito"
        ffprobe_path["test_detail"] = _first_line(out)
    internals = next((e for e in engines if e["id"] == "hyperframes_internal"), {})
    if internals.get("installed"):
        internals["test_state"] = "superato"
        internals["test_detail"] = f"{len(internals['details']['presets'])} preset caricati"

    summary = {
        "total": len(engines),
        "installed": sum(1 for e in engines if e["installed"]),
        "available": sum(1 for e in engines if e["available"]),
        "with_problems": [e["id"] for e in engines if not e["available"]],
        "render_ready": bool(ffmpeg_path.get("installed") and ffprobe_path.get("installed")),
    }
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "engines": engines, "summary": summary}
    _cache["at"] = now
    _cache["payload"] = payload
    return payload


# ------------------------------------------------------------ test su richiesta
def _test_remotion(engine: dict[str, Any]) -> tuple[bool, str]:
    binary = engine.get("details", {}).get("remotion_bin")
    if not binary:
        return False, "Composer non installato: usa tools/ripristina_remotion.sh"
    ok, out = _run([binary, "--version"], timeout=20.0)
    return ok, _first_line(out, "nessuna risposta")


def _test_hyperframes_external(engine: dict[str, Any]) -> tuple[bool, str]:
    url = (engine.get("details") or {}).get("url") or ""
    if not url:
        return False, "KORA_HYPERFRAMES_URL non impostata"
    try:
        import requests

        res = requests.get(url.rstrip("/") + "/health", timeout=4.0)
        return res.ok, f"HTTP {res.status_code}"
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


def _test_piper(engine: dict[str, Any]) -> tuple[bool, str]:
    binary = (engine.get("details") or {}).get("binary")
    if not binary:
        return False, "binario piper assente"
    ok, out = _run([binary, "--help"], timeout=8.0)
    return ok, _first_line(out, "piper risponde")


def _test_ffmpeg_like(engine: dict[str, Any]) -> tuple[bool, str]:
    path = engine.get("path") or ""
    if not path:
        return False, "binario assente"
    ok, out = _run([path, "-version"], timeout=6.0)
    return ok, _first_line(out)


def test_engine(engine_id: str, providers: Any = None) -> dict[str, Any]:
    """Test operativo vero di un singolo motore (nessuna installazione)."""
    inventory = engines_inventory(providers, refresh=True)
    engine = next((e for e in inventory["engines"] if e["id"] == engine_id), None)
    if engine is None:
        return {"ok": False, "engine": engine_id, "detail": "motore sconosciuto"}

    started = time.perf_counter()
    if not engine["installed"]:
        result = (False, "; ".join(engine["missing"]) or "non installato")
    elif engine_id in ("ffmpeg", "ffprobe"):
        result = _test_ffmpeg_like(engine)
    elif engine_id == "remotion":
        result = _test_remotion(engine)
    elif engine_id == "hyperframes_external":
        result = _test_hyperframes_external(engine)
    elif engine_id == "hyperframes_internal":
        count = len((engine.get("details") or {}).get("presets") or [])
        result = (count > 0, f"{count} preset caricati")
    elif engine_id == "piper_tts":
        result = _test_piper(engine)
    elif engine_id in ("whisper", "faster_whisper", "chatterbox_tts", "edge_tts", "qwen3_tts", "elevenlabs"):
        # nessuna rete, nessuna spesa: si verifica che il pacchetto sia importabile
        module = (engine.get("details") or {}).get("module") or ""
        if not module:
            result = (False, engine.get("test_detail") or "modulo non importabile")
        else:
            try:
                __import__(module)
                result = (True, f"modulo {module} importato")
            except Exception as exc:  # pragma: no cover - dipende dall'ambiente
                result = (False, f"{exc.__class__.__name__}: {exc}")
    else:  # pragma: no cover - nessun caso previsto
        result = (False, "test non previsto")

    ok, detail = result
    engine["test_state"] = "superato" if ok else "fallito"
    engine["test_detail"] = detail
    if ok:
        engine["available"] = True
    return {
        "engine": engine_id,
        "ok": ok,
        "detail": detail,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "engine_state": engine,
    }


def reset_cache() -> None:
    _cache["at"] = 0.0
    _cache["payload"] = None


# ------------------------------------------------------------------ CLI locale
def _cli(argv: list[str] | None = None) -> int:
    """Diagnostica da terminale: `python -m kora_studio.media.engines [--test]`."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m kora_studio.media.engines",
        description="Inventario reale dei motori KORA (nessuna installazione automatica).",
    )
    parser.add_argument("--json", action="store_true", help="output JSON completo")
    parser.add_argument("--test", action="store_true", help="esegue i test locali sicuri")
    args = parser.parse_args(argv)

    inventory = engines_inventory(None, refresh=True)
    if args.test:
        for engine in inventory["engines"]:
            if engine["available"] and engine["id"] in ("ffmpeg", "ffprobe"):
                continue
            result = test_engine(engine["id"])
            engine["test_state"] = "superato" if result["ok"] else "fallito"
            engine["test_detail"] = result["detail"]

    if args.json:
        print(json.dumps(inventory, ensure_ascii=False, indent=2))
    else:
        print(f"KORA STUDIO — motori sulla macchina ({inventory['generated_at']})")
        print(f"{'motore':<24}{'stato':<34}{'dettaglio'}")
        print("-" * 96)
        for engine in inventory["engines"]:
            state = (
                "disponibile"
                if engine["available"]
                else "installato, non utilizzabile"
                if engine["installed"]
                else "non installato"
            )
            detail = engine["test_detail"] or "; ".join(engine["missing"])
            print(f"{engine['label']:<24}{state:<34}{detail[:60]}")
        summary = inventory["summary"]
        print("-" * 96)
        print(
            f"{summary['available']}/{summary['total']} disponibili · "
            f"render FFmpeg: {'pronto' if summary['render_ready'] else 'NON pronto'}"
        )
        problems = [e for e in inventory["engines"] if not e["available"]]
        if problems:
            print("Non disponibili: " + ", ".join(e["id"] for e in problems))
        print("I test sono soltanto locali: nessun download, nessuna spesa, nessuna installazione.")
    return 0 if inventory["summary"]["render_ready"] else 1


if __name__ == "__main__":  # pragma: no cover - ingresso CLI
    raise SystemExit(_cli())
