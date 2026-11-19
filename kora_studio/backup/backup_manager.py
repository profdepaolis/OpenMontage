"""Backup, full archive export, restore, integrity check, and safe cache cleanup."""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from kora_studio.config import (
    BACKUPS_DIR,
    CACHE_DIR,
    DATA_DIR,
    DB_PATH,
    EXPORTS_DIR,
    PROJECTS_DIR,
    VAULT_ORIGINALS_DIR,
    ensure_directories,
)
from kora_studio.db.database import Database, utcnow_iso


class BackupManager:
    """Protects database, projects, config, and original assets across restarts/updates."""

    def __init__(self, db: Database) -> None:
        self.db = db
        ensure_directories()

    def check_integrity(self) -> dict[str, Any]:
        with self.db.session() as conn:
            res = conn.execute("PRAGMA integrity_check").fetchone()[0]
            assets = conn.execute("SELECT id, original_path FROM media_assets").fetchall()
        missing_originals = [
            {"id": a["id"], "path": a["original_path"]}
            for a in assets
            if not Path(a["original_path"]).is_file()
        ]
        return {
            "sqlite_integrity": res,
            "total_vault_assets": len(assets),
            "missing_originals": missing_originals,
            "healthy": (res == "ok" and len(missing_originals) == 0),
        }

    def create_backup(self, include_originals: bool = True) -> dict[str, Any]:
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = utcnow_iso().replace(":", "-").replace(".", "-")[:19]
        zip_path = BACKUPS_DIR / f"kora_studio_backup_{stamp}.zip"

        # Safe online SQLite backup copy first
        tmp_db = BACKUPS_DIR / f"tmp_db_{stamp}.db"
        src_conn = sqlite3.connect(str(self.db.db_path))
        dst_conn = sqlite3.connect(str(tmp_db))
        src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()

        manifest = {
            "created_at": utcnow_iso(),
            "include_originals": include_originals,
            "db_schema_version": self.db.get_schema_version(),
        }

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db, arcname="kora_studio.db")
            zf.writestr("backup_manifest.json", json.dumps(manifest, indent=2))
            for folder_name, folder in [
                ("projects", PROJECTS_DIR),
                ("exports", EXPORTS_DIR),
                ("originals", VAULT_ORIGINALS_DIR if include_originals else None),
            ]:
                if folder and folder.is_dir():
                    for p in folder.rglob("*"):
                        if p.is_file():
                            zf.write(p, arcname=f"{folder_name}/{p.relative_to(folder)}")
        tmp_db.unlink(missing_ok=True)
        return {
            "backup_path": str(zip_path),
            "size_bytes": zip_path.stat().st_size,
            "manifest": manifest,
        }

    def restore_backup(self, backup_zip: Path, target_data_dir: Path | None = None) -> dict[str, Any]:
        if not backup_zip.is_file():
            raise FileNotFoundError(f"Archivio backup non trovato: {backup_zip}")
        dest_root = Path(target_data_dir) if target_data_dir else DATA_DIR
        dest_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(backup_zip, "r") as zf:
            for member in zf.namelist():
                if member.startswith("/") or ".." in Path(member).parts:
                    raise ValueError(f"Percorso non sicuro nel backup: {member}")
            zf.extractall(dest_root)
        return {"restored_to": str(dest_root), "backup_file": str(backup_zip)}

    def clean_cache(self) -> dict[str, Any]:
        """Clear temporary cache files without EVER touching originals or vault assets."""
        removed_files = 0
        freed_bytes = 0
        if CACHE_DIR.is_dir():
            for p in list(CACHE_DIR.rglob("*")):
                if p.is_file():
                    freed_bytes += p.stat().st_size
                    p.unlink()
                    removed_files += 1
        return {
            "removed_files": removed_files,
            "freed_bytes": freed_bytes,
            "originals_untouched": True,
        }
