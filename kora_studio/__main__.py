"""CLI entrypoint for KORA STUDIO (`python -m kora_studio ...`)."""

from __future__ import annotations

import argparse
import json
import sys

import uvicorn

from kora_studio.agents.director import KoraDirector
from kora_studio.backup.backup_manager import BackupManager
from kora_studio.media_tools import check_remotion_runtime, find_ffmpeg, find_ffprobe


def main() -> int:
    parser = argparse.ArgumentParser(prog="kora_studio", description="KORA STUDIO CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve_p = sub.add_parser("serve", help="Avvia il server web KORA STUDIO")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8000)

    sub.add_parser("doctor", help="Diagnostica ambiente, FFmpeg, FFprobe, Remotion e provider")
    sub.add_parser("seed", help="Inizializza Brand Brain, Media Vault e Sound Library")
    sub.add_parser("backup", help="Crea backup completo DB + progetti + originali")

    e2e_p = sub.add_parser("e2e", help="Esegue una produzione end-to-end reale da tema libero")
    e2e_p.add_argument("--theme", default="Il piacere di stare insieme davanti al mare.")
    e2e_p.add_argument("--voice", default="brando")
    e2e_p.add_argument("--duration", type=float, default=20.0)

    args = parser.parse_args()

    if args.cmd == "serve":
        uvicorn.run("kora_studio.api.app:app", host=args.host, port=args.port, reload=False)
        return 0

    director = KoraDirector()
    if args.cmd == "seed":
        res = director.vault.seed_kora_authentic_assets()
        pal = director.brand_brain.refresh_palette_from_vault()
        print(json.dumps({"vault_seed": res, "brand_version": pal["version"]}, indent=2))
        return 0

    if args.cmd == "doctor":
        bm = BackupManager(director.db)
        out = {
            "ffmpeg": find_ffmpeg(),
            "ffprobe": find_ffprobe(),
            "remotion": check_remotion_runtime(),
            "integrity": bm.check_integrity(),
            "providers": [
                {"id": p["id"], "status": p["operational_status"], "quota": p["quota_status"]}
                for p in director.providers.list_providers()
            ],
            "budget": director.providers.cost_guard.get_budget_settings(),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "backup":
        bm = BackupManager(director.db)
        print(json.dumps(bm.create_backup(), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "e2e":
        director.vault.seed_kora_authentic_assets()
        concepts = director.creative.ideate_from_theme(args.theme, duration_sec=args.duration)
        c = concepts[0]
        proj = director.create_project(
            title=c.title,
            theme=args.theme,
            preset=c.preset,
            voice_id=args.voice,
            target_duration_sec=args.duration,
            concept=c,
        )
        res = director.run_production_pipeline(proj["id"])
        print(json.dumps({"project_status": res["project"]["status"], "output": res["project"]["output_video_path"]}, indent=2))
        return 0 if res["project"]["status"] == "Completato" else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
