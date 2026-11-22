#!/usr/bin/env python3
"""Impacchetta KORA STUDIO LIGHT 3.6.1 in una cartella pronta al doppio clic.

Regole:
- l'interfaccia compilata (frontend/out) viaggia dentro: nessuna build sul Mac dell'utente;
- niente node_modules, cache, __pycache__, .venv, database locali;
- prima di creare lo ZIP si verifica che OGNI file atteso esista davvero.
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET_NAME = "Kora Studio Light"

EXCLUDE_DIRS = {
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".venv",
    "kora_data",
    "out/cache",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log", ".DS_Store"}
EXCLUDE_NAMES = {".DS_Store"}

TOP_FILES = [
    "setup.sh",
    "verifica.sh",
    "start.sh",
    "ripristina.sh",
    "diagnostica_ai.sh",
    "Installa KORA.command",
    "Avvia KORA.command",
    "LEGGIMI.txt",
    "INSTALLAZIONE.md",
    "KORA_3.6_REPORT.md",
    "requirements.txt",
]

REQUIRED = [
    "kora_studio/__main__.py",
    "kora_studio/api/app.py",
    "kora_studio/agents/caption_designer.py",
    "kora_studio/agents/script_director.py",
    "kora_studio/agents/art_director.py",
    "kora_studio/agents/scene_generator.py",
    "kora_studio/ai_engine/model_manager.py",
    "kora_studio/frontend/out/index.html",
    "kora_studio/frontend/out/404.html",
    "kora_studio/requirements_kora.txt",
    "assets/kora/PlayfairDisplay.ttf",
    "assets/kora/SpaceGrotesk.ttf",
]


def ignore(dirpath: str, names: list[str]) -> set[str]:
    skipped: set[str] = set()
    for name in names:
        full = Path(dirpath) / name
        if name in EXCLUDE_DIRS or name in EXCLUDE_NAMES:
            skipped.add(name)
        elif full.suffix in EXCLUDE_SUFFIXES:
            skipped.add(name)
    return skipped


def main() -> int:
    dest_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/kora_pkg")
    dest = dest_root / TARGET_NAME
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    missing_src = [p for p in REQUIRED if not (ROOT / p).exists()]
    if missing_src:
        print("Sorgenti mancanti:\n  " + "\n  ".join(missing_src))
        return 1

    shutil.copytree(ROOT / "kora_studio", dest / "kora_studio", ignore=ignore)
    shutil.copytree(ROOT / "assets" / "kora", dest / "assets" / "kora", ignore=ignore)

    for name in TOP_FILES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, dest / name)

    docs_src = ROOT / "docs"
    if docs_src.is_dir():
        shutil.copytree(docs_src, dest / "docs", ignore=ignore)

    demo_src = ROOT / "demo"
    if demo_src.is_dir():
        shutil.copytree(demo_src, dest / "demo", ignore=ignore)

    # permessi eseguibili
    for name in (
        "setup.sh",
        "verifica.sh",
        "start.sh",
        "ripristina.sh",
        "diagnostica_ai.sh",
        "Installa KORA.command",
        "Avvia KORA.command",
    ):
        path = dest / name
        if path.exists():
            path.chmod(0o755)

    # controllo duro: ogni file richiesto deve essere nella cartella finale
    missing_dest = [p for p in REQUIRED if not (dest / p).exists()]
    if missing_dest:
        print("File mancanti nel pacchetto:\n  " + "\n  ".join(missing_dest))
        return 1

    files = [p for p in dest.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"cartella: {dest}")
    print(f"file: {len(files)} · dimensione: {total / 1_000_000:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
