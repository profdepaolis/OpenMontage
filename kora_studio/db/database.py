"""SQLite database engine with versioned migrations for KORA STUDIO."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from kora_studio.config import DB_PATH, ensure_directories

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "initial_schema",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS brand_profiles (
            version INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_name TEXT NOT NULL,
            location TEXT NOT NULL,
            payoff TEXT NOT NULL,
            profile_json TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS media_assets (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE,
            media_type TEXT NOT NULL,
            extension TEXT NOT NULL,
            origin TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            duration_sec REAL,
            orientation TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            fps REAL,
            category TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            technical_quality REAL NOT NULL,
            authenticity_status TEXT NOT NULL,
            usage_rights TEXT NOT NULL,
            original_path TEXT NOT NULL,
            working_path TEXT,
            thumbnail_path TEXT,
            analysis_json TEXT NOT NULL,
            semantic_text TEXT NOT NULL,
            projects_used_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_media_category ON media_assets(category);
        CREATE INDEX IF NOT EXISTS idx_media_type ON media_assets(media_type);

        CREATE TABLE IF NOT EXISTS sound_library (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            file_path TEXT NOT NULL,
            duration_sec REAL NOT NULL,
            license TEXT NOT NULL,
            source TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            bpm INTEGER,
            mood TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            theme TEXT NOT NULL,
            mode TEXT NOT NULL,
            preset TEXT NOT NULL,
            production_mode TEXT NOT NULL DEFAULT 'AUTO',
            voice_id TEXT NOT NULL DEFAULT 'brando',
            fps INTEGER NOT NULL DEFAULT 25,
            target_duration_sec REAL NOT NULL DEFAULT 20.0,
            total_frames INTEGER NOT NULL DEFAULT 500,
            status TEXT NOT NULL,
            current_version INTEGER NOT NULL DEFAULT 1,
            concept_json TEXT NOT NULL DEFAULT '{}',
            storyboard_json TEXT NOT NULL DEFAULT '[]',
            edit_plan_json TEXT NOT NULL DEFAULT '{}',
            audio_plan_json TEXT NOT NULL DEFAULT '{}',
            qc_report_json TEXT NOT NULL DEFAULT '{}',
            export_bundle_json TEXT NOT NULL DEFAULT '{}',
            preferences_json TEXT NOT NULL DEFAULT '{}',
            output_video_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS project_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            summary TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_proj_ver ON project_versions(project_id, version);

        CREATE TABLE IF NOT EXISTS reel_history (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            theme TEXT NOT NULL,
            hook TEXT NOT NULL,
            ending TEXT NOT NULL,
            phrases_json TEXT NOT NULL,
            asset_ids_json TEXT NOT NULL,
            music_id TEXT,
            preset TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS providers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            capabilities_json TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            auth_type TEXT NOT NULL,
            models_json TEXT NOT NULL,
            declared_costs_json TEXT NOT NULL,
            quota_status TEXT NOT NULL DEFAULT 'Non verificabile',
            limits_doc TEXT NOT NULL DEFAULT '',
            last_check TEXT,
            operational_status TEXT NOT NULL DEFAULT 'non_configurato',
            avg_latency_ms REAL,
            recent_errors_json TEXT NOT NULL DEFAULT '[]',
            encrypted_secret TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            is_free_local INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS budget_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            paid_budget_eur REAL NOT NULL DEFAULT 0.0,
            monthly_cap_eur REAL NOT NULL DEFAULT 20.0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS provider_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id TEXT NOT NULL,
            project_id TEXT,
            capability TEXT NOT NULL,
            estimated_eur REAL NOT NULL DEFAULT 0.0,
            actual_eur REAL NOT NULL DEFAULT 0.0,
            authorized INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            job_type TEXT NOT NULL,
            state TEXT NOT NULL,
            current_step TEXT NOT NULL,
            completed_steps_json TEXT NOT NULL DEFAULT '[]',
            total_steps INTEGER NOT NULL,
            error_message TEXT,
            intervention_reason TEXT,
            checkpoint_json TEXT NOT NULL DEFAULT '{}',
            cancelled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);

        CREATE TABLE IF NOT EXISTS job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            level TEXT NOT NULL,
            agent TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS user_preferences (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thread-safe SQLite wrapper with schema migration tracking."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else DB_PATH
        ensure_directories()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> list[int]:
        applied: list[int] = []
        with self.session() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            existing = {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for version, name, sql in MIGRATIONS:
                if version not in existing:
                    conn.executescript(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                        (version, name, utcnow_iso()),
                    )
                    applied.append(version)
            # Ensure default budget row (0 EUR paid default, 20 EUR monthly cap)
            row = conn.execute("SELECT id FROM budget_settings WHERE id = 1").fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO budget_settings (id, paid_budget_eur, monthly_cap_eur, updated_at) VALUES (1, 0.0, 20.0, ?)",
                    (utcnow_iso(),),
                )
        return applied

    def get_schema_version(self) -> int:
        with self.session() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            return int(row["v"] or 0) if row else 0

    def set_preference(self, key: str, value: Any) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), utcnow_iso()),
            )

    def get_preference(self, key: str, default: Any = None) -> Any:
        with self.session() as conn:
            row = conn.execute(
                "SELECT value_json FROM user_preferences WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                return default
            return json.loads(row["value_json"])

    def get_all_preferences(self) -> dict[str, Any]:
        with self.session() as conn:
            rows = conn.execute("SELECT key, value_json FROM user_preferences").fetchall()
            return {r["key"]: json.loads(r["value_json"]) for r in rows}
