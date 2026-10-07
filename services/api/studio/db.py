from __future__ import annotations

import sqlite3
from contextlib import contextmanager
import json
from pathlib import Path


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


# Migrations that change what existing projects mean: when the query finds such projects, the database is copied
# to data/archive/ as it was, before the migration runs.
SNAPSHOT_BEFORE = {"029_single_workflow": "SELECT 1 FROM projects WHERE workflow_version='V1' LIMIT 1"}


class Database:
    def __init__(self, data_root: Path):
        self.data_root = data_root.resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.path = self.data_root / "studio.sqlite3"
        self._migrate()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _migrate(self) -> None:
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY)")
            for path in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")):
                if db.execute("SELECT 1 FROM schema_migrations WHERE version=?", (path.stem,)).fetchone():
                    continue
                if path.stem in SNAPSHOT_BEFORE and db.execute(SNAPSHOT_BEFORE[path.stem]).fetchone():
                    self._snapshot(db, path.stem)
                db.executescript(path.read_text(encoding="utf-8"))
                db.execute("INSERT INTO schema_migrations(version) VALUES(?)", (path.stem,))
            if not db.execute("SELECT 1 FROM schema_migrations WHERE version='002_registry_pins'").fetchone():
                state_columns = {row[1] for row in db.execute("PRAGMA table_info(subsystem_states)")}
                revision_columns = {row[1] for row in db.execute("PRAGMA table_info(shot_revisions)")}
                if "observed_fingerprint" not in state_columns:
                    db.execute("ALTER TABLE subsystem_states ADD COLUMN observed_fingerprint TEXT")
                if "resolved_registry_json" not in revision_columns:
                    db.execute("ALTER TABLE shot_revisions ADD COLUMN resolved_registry_json TEXT NOT NULL DEFAULT '{}'")
                db.execute("INSERT INTO schema_migrations(version) VALUES('002_registry_pins')")
            if not db.execute("SELECT 1 FROM schema_migrations WHERE version='003_relationships'").fetchone():
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS revision_assets(shot_revision_id TEXT NOT NULL REFERENCES shot_revisions(id), asset_version_id TEXT NOT NULL REFERENCES asset_versions(id), registry_id TEXT NOT NULL, PRIMARY KEY(shot_revision_id,asset_version_id));
                    CREATE TABLE IF NOT EXISTS job_artifacts(job_id TEXT NOT NULL REFERENCES jobs(id), artifact_id TEXT NOT NULL REFERENCES artifact_versions(id), role TEXT NOT NULL, PRIMARY KEY(job_id,artifact_id));
                """)
                for revision in db.execute("SELECT id,resolved_registry_json FROM shot_revisions").fetchall():
                    for registry_id, pinned in json.loads(revision["resolved_registry_json"]).items():
                        db.execute("INSERT OR IGNORE INTO revision_assets(shot_revision_id,asset_version_id,registry_id) SELECT ?,id,? FROM asset_versions WHERE id=?", (revision["id"], registry_id, pinned["asset_version_id"]))
                db.execute("INSERT INTO schema_migrations(version) VALUES('003_relationships')")
            if not db.execute("SELECT 1 FROM schema_migrations WHERE version='004_legacy_job_links'").fetchone():
                db.execute("""
                    INSERT OR IGNORE INTO job_artifacts(job_id,artifact_id,role)
                    SELECT j.id,a.id,'output' FROM jobs j JOIN artifact_versions a
                    ON a.project_id=j.project_id AND a.shot_id=j.shot_id AND a.role=j.subsystem AND a.input_hash=j.input_hash
                    WHERE j.state='COMPLETED' AND a.provenance=j.adapter_kind
                    AND (SELECT COUNT(*) FROM jobs x WHERE x.project_id=j.project_id AND x.shot_id=j.shot_id AND x.subsystem=j.subsystem AND x.input_hash=j.input_hash AND x.state='COMPLETED')=1
                    AND (SELECT COUNT(*) FROM artifact_versions x WHERE x.project_id=a.project_id AND x.shot_id=a.shot_id AND x.role=a.role AND x.input_hash=a.input_hash)=1
                """)
                db.execute("INSERT INTO schema_migrations(version) VALUES('004_legacy_job_links')")

    def _snapshot(self, db: sqlite3.Connection, migration: str) -> None:
        target = self.data_root / "archive" / f"studio-before-{migration}.sqlite3"
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target, factory=ClosingConnection) as copy:
            db.backup(copy)

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def row_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
