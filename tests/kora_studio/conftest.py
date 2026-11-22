"""Isolated workspace fixtures for KORA STUDIO tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_BIN = REPO_ROOT / ".venv" / "bin"
if VENV_BIN.is_dir():
    os.environ["PATH"] = f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect kora_studio runtime directories to an isolated temp workspace."""
    data_dir = tmp_path / "kora_data"
    vault_dir = data_dir / "vault"
    originals_dir = vault_dir / "originals"
    working_dir = vault_dir / "working"
    thumbs_dir = vault_dir / "thumbs"
    sound_dir = data_dir / "sound_library"
    projects_dir = data_dir / "projects"
    exports_dir = data_dir / "exports"
    backups_dir = data_dir / "backups"
    cache_dir = data_dir / "cache"
    voices_dir = data_dir / "voices" / "piper"
    db_path = data_dir / "kora_studio.db"
    master_key_path = tmp_path / ".kora_master.key"

    for d in (
        data_dir,
        vault_dir,
        originals_dir,
        working_dir,
        thumbs_dir,
        sound_dir,
        projects_dir,
        exports_dir,
        backups_dir,
        cache_dir,
        voices_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)

    import kora_studio.config as cfg
    import kora_studio.db.database as db_mod
    import kora_studio.vault.media_vault as mv
    import kora_studio.vault.sound_library as sl
    import kora_studio.agents.director as dir_mod
    import kora_studio.agents.export_manager as exp_mod
    import kora_studio.agents.cinematic_editor as ed_mod
    import kora_studio.agents.voice_director as vd_mod
    import kora_studio.agents.ai_media_lab as ml_mod
    import kora_studio.backup.backup_manager as bm_mod
    import kora_studio.api.app as app_mod

    monkeypatch.setattr(cfg, "DATA_DIR", data_dir)
    monkeypatch.setattr(cfg, "VAULT_DIR", vault_dir)
    monkeypatch.setattr(cfg, "VAULT_ORIGINALS_DIR", originals_dir)
    monkeypatch.setattr(cfg, "VAULT_WORKING_DIR", working_dir)
    monkeypatch.setattr(cfg, "VAULT_THUMBS_DIR", thumbs_dir)
    monkeypatch.setattr(cfg, "SOUND_LIBRARY_DIR", sound_dir)
    monkeypatch.setattr(cfg, "PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(cfg, "EXPORTS_DIR", exports_dir)
    monkeypatch.setattr(cfg, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(cfg, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(cfg, "VOICES_PIPER_DIR", voices_dir)
    monkeypatch.setattr(cfg, "DB_PATH", db_path)
    monkeypatch.setattr(cfg, "MASTER_KEY_PATH", master_key_path)

    # Patch module-level imported path constants where referenced directly
    for mod, attrs in [
        (db_mod, {"DB_PATH": db_path}),
        (mv, {"VAULT_ORIGINALS_DIR": originals_dir, "VAULT_WORKING_DIR": working_dir, "VAULT_THUMBS_DIR": thumbs_dir}),
        (sl, {"SOUND_LIBRARY_DIR": sound_dir}),
        (dir_mod, {"PROJECTS_DIR": projects_dir}),
        (exp_mod, {"EXPORTS_DIR": exports_dir}),
        (ed_mod, {"CACHE_DIR": cache_dir}),
        (vd_mod, {"VOICES_PIPER_DIR": voices_dir, "CACHE_DIR": cache_dir}),
        (ml_mod, {"CACHE_DIR": cache_dir}),
        (bm_mod, {"DATA_DIR": data_dir, "BACKUPS_DIR": backups_dir, "CACHE_DIR": cache_dir, "PROJECTS_DIR": projects_dir, "EXPORTS_DIR": exports_dir, "VAULT_ORIGINALS_DIR": originals_dir}),
        (app_mod, {"BACKUPS_DIR": backups_dir, "PROJECTS_DIR": projects_dir}),
    ]:
        for attr, val in attrs.items():
            if hasattr(mod, attr):
                monkeypatch.setattr(mod, attr, val)

    return {
        "tmp_path": tmp_path,
        "data_dir": data_dir,
        "db_path": db_path,
        "master_key_path": master_key_path,
    }
