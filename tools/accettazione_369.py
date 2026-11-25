"""Collaudo 3.6.9 — un Reel con sottotitoli editoriali dichiarati deve esportare.

Riproduce il caso reale segnalato dall'utente («L'aperitivo senza fretta»): voce che
legge una frase completa, sottotitoli brevi e creativi. Lo script **esegue davvero**
due produzioni complete:

1. con la dichiarazione predefinita (`transcript`) → la produzione si ferma e il
   messaggio deve indicare la causa reale e i due rimedi;
2. dopo la dichiarazione «editoriali» → la produzione completa, il QC deve registrare
   l'avviso (non un errore) e i sottotitoli dell'utente devono restare intatti.

Nessun risultato è simulato: job, MP4 e report QC sono letti dai file prodotti.

Uso:
    KORA_DATA_DIR=/tmp/acc369/data .venv/bin/python tools/accettazione_369.py --out /tmp/acc369
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

SCRIPT = (
    "Nel tardo pomeriggio d'autunno, la luce si abbassa e le ombre si allungano "
    "sul pergolato. Il bicchiere resta sul tavolo, il tempo rallenta."
)
CAPTIONS = {
    0: "Il pomeriggio rallenta.",
    1: "Ombre lunghe.",
    2: "Un bicchiere, il silenzio.",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/acc369")
    args = parser.parse_args()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = Path(os.environ.get("KORA_DATA_DIR") or (out_dir / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["KORA_DATA_DIR"] = str(data_dir)

    from kora_studio.agents.director import KoraDirector
    from kora_studio.config import DATA_DIR, EXPORTS_DIR
    from kora_studio.db.database import Database
    from kora_studio.db.models import CreativeRequestPayload
    from kora_studio.media_tools import find_ffprobe

    db = Database(DATA_DIR / "kora_studio.db")
    director = KoraDirector(db=db)

    photos = sorted((REPO / "assets" / "kora" / "photos").glob("*.png"))
    for photo in photos:
        director.vault.ingest_file(
            photo, origin="asset_kora", authenticity_status="autentico",
            usage_rights="proprietario_kora", category="Mare",
        )
    logo = REPO / "assets" / "kora" / "logo" / "LOGO KORA.PNG"
    if logo.is_file():
        director.vault.ingest_file(
            logo, origin="asset_kora", authenticity_status="autentico",
            usage_rights="proprietario_kora", category="Logo",
        )
    asset_ids = [
        a["id"] for a in director.vault.list_assets()
        if a.get("media_type") == "image" and a.get("category") != "Logo"
    ]
    logo_asset = next(
        (a for a in director.vault.list_assets() if a.get("category") == "Logo"), None
    )

    design = director.design_reel(
        CreativeRequestPayload(
            theme="L'aperitivo senza fretta", duration_sec=18.0, voice_id="brando",
            asset_ids=asset_ids, media_selection="vault_only",
        )
    )
    project = director.create_project_from_design(
        design, theme="L'aperitivo senza fretta", caption_style="hyperframes_word_highlight",
    )
    project_id = project["id"]

    mp3 = REPO / "assets" / "kora" / "voices" / "voce_brando.mp3"
    # La voce è quella reale dell'utente (MP3 già pronto): in questa macchina il motore
    # ASR non c'è, quindi la timeline dichiara la stima — è la stessa condizione del
    # progetto segnalato («estimated», nessun segmento trascritto).
    director.apply_uploaded_voiceover(project_id, mp3, "voce_brando.mp3", script_text=SCRIPT, use_asr=True)
    rebuild = director.rebuild_audio_timeline(project_id, mode="asr")
    print("timeline:", rebuild["alignment"], "|", rebuild["asr_note"], flush=True)
    caption_source_iniziale = director.caption_source_for(project_id)
    print("caption_source iniziale:", caption_source_iniziale, flush=True)

    def read_scenes() -> list[dict]:
        return list(director.get_project(project_id)["storyboard"])

    def write_scenes(storyboard: list[dict]) -> None:
        with director.db.session() as conn:
            conn.execute(
                "UPDATE projects SET storyboard_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(storyboard, ensure_ascii=False), "2026-11-25T00:00:00+00:00", project_id),
            )

    # Chiusura brand (logo + payoff) e sottotitoli creativi, come li scriverebbe l'utente.
    storyboard = read_scenes()
    content = [s for s in storyboard if not s.get("is_outro")]
    for index, scene in enumerate(content):
        if index in CAPTIONS:
            scene["subtitle_text"] = CAPTIONS[index]
    outro = next((s for s in storyboard if s.get("is_outro")), None)
    if outro is not None and logo_asset:
        outro["selected_asset_id"] = logo_asset["id"]
        outro["subtitle_text"] = "The Art of Being"
        locks = dict(outro.get("locks") or {})
        locks["caption"] = True
        outro["locks"] = locks
    write_scenes(storyboard)
    creative = {i: s.get("subtitle_text") for i, s in enumerate(content) if i in CAPTIONS}
    print("sottotitoli creativi impostati:", creative, flush=True)

    def produce(label: str) -> dict:
        job = director.create_job(project_id, "full_production")
        result = director.run_production_pipeline(project_id=project_id, job_id=job["id"])
        job_state = (result.get("job") or {}).get("state")
        print(f"{label} · esito:", job_state, flush=True)
        if (result.get("job") or {}).get("error_message"):
            print(f"{label} · messaggio:", result["job"]["error_message"], flush=True)
        return result

    # ---------------------------------------------------------------- 1. severo
    strict = produce("1 · Transcript (predefinito)")
    _before = {i: sc.get("subtitle_text") for i, sc in enumerate(
        [s for s in read_scenes() if not s.get("is_outro")]
    ) if i in CAPTIONS}
    if _before != creative:
        print("ATTENZIONE: i sottotitoli creativi sono stati riscritti prima del QC:", _before, flush=True)
    strict_msg = (strict.get("job") or {}).get("error_message") or ""

    # ------------------------------------------------- 2. dichiarazione editoriale
    declared = director.rebuild_audio_timeline(project_id, mode="script", script_text=SCRIPT, caption_source="editorial")
    after = {i: s.get("subtitle_text") for i, s in enumerate(
        [s for s in read_scenes() if not s.get("is_outro")]
    ) if i in CAPTIONS}
    captions_kept = after == creative

    editorial = produce("2 · Editoriali dichiarati")
    job2 = editorial.get("job") or {}

    export_dir = EXPORTS_DIR / project_id
    final_mp4 = export_dir / f"{project_id}_reel_final.mp4"
    qc_file = export_dir / f"{project_id}_report_tecnico.json"
    qc_report = json.loads(qc_file.read_text(encoding="utf-8")).get("qc") if qc_file.is_file() else {}
    vc = next(
        (c for c in (qc_report.get("checks") or []) if c.get("name", "").startswith("Coerenza tra voiceover")),
        {},
    )

    probe = {}
    if final_mp4.is_file():
        proc = subprocess.run(
            [find_ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(final_mp4)],
            capture_output=True, text=True, check=True,
        )
        probe = json.loads(proc.stdout)

    summary = {
        "project_id": project_id,
        "mp3": str(mp3),
        "copione": SCRIPT,
        "sottotitoli_creativi": creative,
        "timeline": {
            "alignment": rebuild["alignment"],
            "verified": rebuild["verified"],
            "verification": rebuild.get("verification"),
            "caption_source_predefinito": caption_source_iniziale,
        },
        "produzione_severa": {
            "job_state": (strict.get("job") or {}).get("state"),
            "messaggio": strict_msg,
            "elenca_due_rimedi": "Due rimedi" in strict_msg,
            "separa_avvisi": "Avvisi (non bloccanti)" in strict_msg,
        },
        "dichiarazione_editoriale": {
            "caption_source": director.caption_source_for(project_id),
            "sottotitoli_intatti_dopo_ricalcolo": captions_kept,
            "sottotitoli_prima_della_dichiarazione": _before,
        },
        "produzione_editoriale": {
            "job_state": job2.get("state"),
            "error_message": job2.get("error_message"),
            "mp4": str(final_mp4),
            "mp4_esiste": final_mp4.is_file(),
            "mp4_bytes": final_mp4.stat().st_size if final_mp4.is_file() else 0,
            "durata_sec": float(((probe.get("format") or {}).get("duration") or 0) or 0),
            "video": next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {}),
            "audio": next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), {}),
        },
        "qc": {
            "passed": qc_report.get("passed"),
            "controllo_coerenza": {
                "passed": vc.get("passed"),
                "severity": vc.get("severity"),
                "requires_human_review": vc.get("requires_human_review"),
                "detail": vc.get("detail"),
            },
            "revisione_umana": qc_report.get("needs_human_review"),
            "motivi": qc_report.get("human_review_reasons"),
        },
    }

    (out_dir / "report_3.6.9.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    if final_mp4.is_file():
        shutil.copy2(final_mp4, out_dir / final_mp4.name)

    ok = (
        summary["produzione_severa"]["job_state"] == "Errore"
        and summary["produzione_severa"]["elenca_due_rimedi"]
        and summary["dichiarazione_editoriale"]["sottotitoli_intatti_dopo_ricalcolo"]
        and summary["produzione_editoriale"]["job_state"] == "Completato"
        and summary["produzione_editoriale"]["mp4_esiste"]
        and summary["qc"]["controllo_coerenza"]["passed"] is True
        and summary["qc"]["controllo_coerenza"]["severity"] == "warning"
    )
    print("\nesito complessivo:", "OK" if ok else "KO", flush=True)
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
