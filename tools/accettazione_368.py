"""Collaudo audiovisivo 3.6.8 — due percorsi con MP3 italiani reali.

Percorso A: MP3 + copione  → `mode="script"` (con ASR trascrive e allinea; qui
            l'ASR non è installato, quindi KORA deve DICHIARARE la stima).
Percorso B: MP3 senza copione → `mode="asr"` (senza motore KORA non inventa testo).

Lo script produce, per ogni percorso: MP4, timeline JSON, transcript JSON,
SRT, VTT e un riepilogo leggibile. Non simula la trascrizione: se il motore ASR
manca, il report lo dice e i tempi restano `estimated` / `none`.

Uso:
    KORA_DATA_DIR=/tmp/acc368/data .venv/bin/python tools/accettazione_368.py --out /tmp/acc368
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _prepare_env(out_dir: Path) -> None:
    data_dir = Path(os.environ.get("KORA_DATA_DIR") or (out_dir / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["KORA_DATA_DIR"] = str(data_dir)


SCRIPT_A = (
    "La luce accarezza il pergolato e la sabbia. Materiali semplici, proporzioni giuste, "
    "niente di superfluo. L'identità KORA si riconosce da un dettaglio."
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/acc368")
    args = parser.parse_args()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    _prepare_env(out_dir)

    from kora_studio.agents import asr as asr_mod
    from kora_studio.agents.audio_timeline import AudioTimelineManager
    from kora_studio.agents.director import KoraDirector
    from kora_studio.db.database import Database
    from kora_studio.db.models import CreativeRequestPayload
    from kora_studio.media_tools import find_ffprobe
    from kora_studio.config import DATA_DIR

    status = asr_mod.status()
    print("ASR:", status["available"], status["engines"], flush=True)

    db = Database(DATA_DIR / "kora_studio.db")
    director = KoraDirector(db=db)

    photos = sorted((REPO / "assets" / "kora" / "photos").glob("*.png"))
    for photo in photos:
        director.vault.ingest_file(
            photo,
            origin="asset_kora",
            authenticity_status="autentico",
            usage_rights="proprietario_kora",
            category="Mare",
        )
    logo = REPO / "assets" / "kora" / "logo" / "LOGO KORA.PNG"
    if logo.is_file():
        director.vault.ingest_file(
            logo,
            origin="asset_kora",
            authenticity_status="autentico",
            usage_rights="proprietario_kora",
            category="Logo",
        )
    asset_ids = [
        a["id"]
        for a in director.vault.list_assets()
        if a.get("media_type") == "image" and a.get("category") != "Logo"
    ]
    print("asset nel vault:", len(asset_ids), flush=True)

    def new_project(voice_id: str, seconds: float, title_theme: str) -> str:
        design = director.design_reel(
            CreativeRequestPayload(
                theme=title_theme,
                duration_sec=seconds,
                voice_id=voice_id,
                asset_ids=asset_ids,
                media_selection="vault_only",
            )
        )
        project = director.create_project_from_design(
            design,
            theme=title_theme,
            caption_style="hyperframes_word_highlight",
        )
        return project["id"]

    logo_asset = next(
        (a for a in director.vault.list_assets() if a.get("category") == "Logo"), None
    )

    def configure_outro(project_id: str) -> None:
        """Chiusura brand: logo originale + payoff esatto (come fa la UI)."""
        import json as _json

        if not logo_asset:
            return
        project = director.get_project(project_id)
        storyboard = project["storyboard"]
        outro = next((sc for sc in storyboard if sc.get("is_outro")), None)
        if outro is None:
            return
        outro["selected_asset_id"] = logo_asset["id"]
        outro["subtitle_text"] = "The Art of Being"
        outro["spoken_text"] = outro.get("spoken_text") or ""
        # chiusura approvata: il blocco impedisce al transcript di riscriverla
        locks = dict(outro.get("locks") or {})
        locks["caption"] = True
        outro["locks"] = locks
        with director.db.session() as conn:
            conn.execute(
                "UPDATE projects SET storyboard_json = ?, updated_at = ? WHERE id = ?",
                (_json.dumps(storyboard, ensure_ascii=False), "2026-11-24T00:00:00+00:00", project_id),
            )

    def produce(project_id: str, include_subtitles: bool = True) -> dict:
        job = director.create_job(project_id, "full_production")
        return director.run_production_pipeline(
            project_id=project_id,
            job_id=job["id"],
            include_subtitles=include_subtitles,
        )

    def probe(path: Path) -> dict:
        ffprobe = find_ffprobe()
        proc = subprocess.run(
            [
                ffprobe, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        return json.loads(proc.stdout)

    report: dict = {"asr": status, "percorsi": []}

    # ------------------------------------------------------------------ A
    print("\n=== PERCORSO A: MP3 + copione ===", flush=True)
    pid_a = new_project("brando", 24.0, "La luce radente")
    mp3_a = REPO / "assets" / "kora" / "voices" / "voce_brando.mp3"
    director.apply_uploaded_voiceover(
        pid_a, mp3_a, "voce_brando.mp3", script_text=SCRIPT_A, use_asr=True
    )
    rebuild_a = director.rebuild_audio_timeline(pid_a, mode="script", script_text=SCRIPT_A)
    print("A · alignment =", rebuild_a["alignment"], "| nota:", rebuild_a["asr_note"], flush=True)
    # la chiusura brand va impostata dopo il riallineamento (l'upload riscrive lo storyboard)
    configure_outro(pid_a)
    result_a = produce(pid_a)
    print("A · esito produzione:", (result_a.get("job") or {}).get("state"), flush=True)
    report["percorsi"].append(
        {
            "percorso": "A · MP3 + copione",
            "project_id": pid_a,
            "mp3": str(mp3_a),
            "copione": SCRIPT_A,
            "mode": "script",
            "alignment_after_rebuild": rebuild_a["alignment"],
            "verified": rebuild_a["verified"],
            "verification": rebuild_a.get("verification"),
            "mismatches": rebuild_a.get("mismatches"),
            "asr_note": rebuild_a["asr_note"],
            "production": {
                "job_state": ((result_a.get("job") or {}).get("state")),
                "project_keys": sorted(k for k in result_a.keys()),
            },
        }
    )

    # ------------------------------------------------------------------ B
    print("\n=== PERCORSO B: MP3 senza copione ===", flush=True)
    pid_b = new_project("tiziana", 24.0, "La bellezza non fa rumore")
    mp3_b = REPO / "assets" / "kora" / "voices" / "voce_tiziana.mp3"
    director.apply_uploaded_voiceover(pid_b, mp3_b, "voce_tiziana.mp3", use_asr=True)
    rebuild_b = director.rebuild_audio_timeline(pid_b, mode="asr")
    print("B · alignment =", rebuild_b["alignment"], "| nota:", rebuild_b["asr_note"], flush=True)
    configure_outro(pid_b)
    result_b = produce(pid_b, include_subtitles=False)
    print("B · esito produzione:", (result_b.get("job") or {}).get("state"), flush=True)
    report["percorsi"].append(
        {
            "percorso": "B · MP3 senza copione",
            "project_id": pid_b,
            "mp3": str(mp3_b),
            "mode": "asr",
            "alignment_after_rebuild": rebuild_b["alignment"],
            "verified": rebuild_b["verified"],
            "trigger_asr": rebuild_b.get("asr_note"),
            "production": {
                "job_state": ((result_b.get("job") or {}).get("state")),
                "project_keys": sorted(k for k in result_b.keys()),
            },
        }
    )

    # ------------------------------------------------------------- verifiche
    (out_dir / "reels").mkdir(exist_ok=True)
    checks: list[dict] = []
    for entry in report["percorsi"]:
        pid = entry["project_id"]
        project_dir = Path("kora_data") / "projects" / pid
        timeline = AudioTimelineManager(fps=25, project_dir=director.project_dir_for(pid)).load()
        export_root = DATA_DIR / "exports" / pid
        mp4s = sorted(export_root.rglob("*.mp4")) if export_root.is_dir() else []
        if mp4s:
            final = max(mp4s, key=lambda p: p.stat().st_size)
            shutil.copy2(final, out_dir / "reels" / f"{pid}_{final.name}")
            info = probe(final)
            streams = {s["codec_type"]: s for s in info.get("streams", [])}
            entry["mp4"] = str(out_dir / "reels" / f"{pid}_{final.name}")
            entry["mp4_bytes"] = final.stat().st_size
            entry["mp4_duration_sec"] = float(info["format"].get("duration") or 0.0)
            video = streams.get("video") or {}
            fps_raw = str(video.get("r_frame_rate") or "25/1")
            try:
                num, den = (int(x) for x in fps_raw.split("/"))
                fps_val = num / den if den else 25.0
            except Exception:
                fps_val = 25.0
            entry["fps"] = round(fps_val, 3)
            entry["video_frames"] = int(video.get("nb_frames") or 0) or int(
                round(entry["mp4_duration_sec"] * fps_val)
            )
            entry["has_audio_stream"] = "audio" in streams
            entry["resolution"] = (
                f"{(streams.get('video') or {}).get('width')}x{(streams.get('video') or {}).get('height')}"
            )
            if timeline:
                entry["timeline"] = {
                    "alignment": timeline["transcript"]["alignment"],
                    "verified": timeline["transcript"]["verified"],
                    "words": len(timeline["transcript"]["words"]),
                    "segments": len(timeline["transcript"]["segments"]),
                    "cues": len(timeline["caption_cues"]),
                    "audio_end_sec": timeline["audio"]["end_sec"],
                    "total_frames": timeline["total_frames"],
                }
                # ogni cue deve stare dentro l'audio
                over = [
                    c for c in timeline["caption_cues"]
                    if float(c["end_sec"]) > float(timeline["audio"]["end_sec"]) + 0.05
                ]
                entry["cues_oltre_la_voce"] = len(over)
                checks.append(
                    {
                        "percorso": entry["percorso"],
                        "audio_completo": entry["mp4_duration_sec"] >= float(
                            (timeline["audio"]["end_sec"] or 0)
                        ) - 0.2,
                        "cue_coerenti": not over,
                    }
                )
            # frame di controllo
            frames_dir = out_dir / "frames"
            frames_dir.mkdir(exist_ok=True)
            for t in (1.0, 6.0, 12.0, 18.0):
                shot = frames_dir / f"{pid}_{int(t):02d}s.jpg"
                subprocess.run(
                    [
                        str((REPO / ".venv" / "bin" / "ffmpeg")), "-y", "-loglevel", "error",
                        "-ss", str(t), "-i", str(final), "-frames:v", "1", str(shot),
                    ],
                    check=False,
                )
        # coerenza SRT <-> timeline (i sottotitoli esportati sono quelli renderizzati)
        srt_files = sorted(export_root.rglob("*sottotitoli.srt")) if export_root.is_dir() else []
        if srt_files and timeline:
            raw = srt_files[0].read_text(encoding="utf-8")
            blocks = [b for b in raw.strip().split("\n\n") if "-->" in b]
            entry["srt_cues"] = len(blocks)
            entry["srt_coerente_con_timeline"] = len(blocks) == len(timeline["caption_cues"])
            entry["srt_prime_righe"] = [
                b.splitlines()[2] for b in blocks[:3] if len(b.splitlines()) > 2
            ]
        # artefatti di sincronizzazione
        arts = {
            "srt": sorted(export_root.rglob("*sottotitoli.srt")) if export_root.is_dir() else [],
            "vtt": sorted(export_root.rglob("*sottotitoli.vtt")) if export_root.is_dir() else [],
            "transcript": sorted(export_root.rglob("*transcript.json")) if export_root.is_dir() else [],
            "timeline": sorted(export_root.rglob("*timeline.json")) if export_root.is_dir() else [],
        }
        entry["artefatti"] = {k: [str(p) for p in v] for k, v in arts.items()}
        # copia degli artefatti accanto ai reel, per la consegna
        bundle = out_dir / "artefatti" / pid
        for key, paths in arts.items():
            for path in paths:
                bundle.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, bundle / path.name)
        del project_dir

    report["verifiche"] = checks
    (out_dir / "report_accettazione_3.6.8.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("\nReport:", out_dir / "report_accettazione_3.6.8.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
