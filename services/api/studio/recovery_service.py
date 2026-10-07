"""Offline backup of the whole Studio, and restore into a separate copy. Never launches Unreal or a paid request.

A backup holds the database, every project folder and, when one is configured, the Unreal project. A restore
writes a new folder beside the running data; the app keeps running on its own data and the restored copy
is an archive to inspect, not something the app switches to.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

from .db import ClosingConnection
from .domain import DomainError, canonical, digest, file_sha256, safe_relative
from .service import Studio, now, require, uid

ACTIVE = ('QUEUED', 'RUNNING', 'RECONCILING', 'PENDING')
# Every queue of the app; a snapshot is taken only while all of them are idle.
JOB_TABLES = ('series_extraction_jobs', 'ue_asset_scans', 'style_capture_jobs', 'breakdown_jobs', 'dialogue_jobs', 'episode_ue_jobs', 'ai_video_jobs',
              'ue_proxy_jobs', 'rough_cut_jobs', 'final_jobs', 'episode_foley_jobs', 'episode_sound_jobs', 'episode_delivery_jobs')
# Re-rooting a restored copy must leave what the operator approved and what was spent exactly as it was.
PRESERVED = ('workflow_checkpoints', 'cost_ledger')


def missing_links(conn, root_of) -> list[dict]:
    """Project-scoped file references that do not resolve under `root_of(project_id)`.

    Shared by backup and restore so a backup cannot complete with a set that restore would reject.
    """
    missing = []
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        for column in ('relative_path', 'relative_folder'):
            if column in columns and 'project_id' in columns:
                for project_id, value in conn.execute(f'SELECT project_id,"{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'):
                    root = root_of(project_id)
                    if not (root / safe_relative(root, value)).exists():
                        missing.append({'table': table, 'path': value})
    return missing


def inventory(root: Path):
    result = []
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(current) / name
            if path.is_symlink() or path.is_junction():
                raise DomainError("Sao lưu không chấp nhận symlink hoặc junction.", 409)
        for name in files:
            path = Path(current) / name
            result.append({'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size, 'sha256': file_sha256(path)})
    return sorted(result, key=lambda item: item['path'])


class RecoveryService:
    def __init__(self, studio: Studio):
        self.studio = studio
        with studio.db.transaction() as conn:
            conn.execute("UPDATE recovery_jobs SET state='RECONCILING',error='Khởi động lại; bản sao có thể chưa đầy đủ, không tự chạy lại.' WHERE state='RUNNING'")
            conn.execute("UPDATE recovery_attempts SET state='RECONCILING',ended_at=? WHERE state='RUNNING'", (now(),))

    def settings(self):
        with self.studio.db.connect() as conn:
            row = conn.execute("SELECT * FROM recovery_settings WHERE id=1").fetchone()
        return dict(row) if row else {'revision': 0, 'backup_parent': '', 'restore_parent': ''}

    def save_settings(self, expected_revision, backup_parent, restore_parent):
        paths = [Path(value).expanduser().resolve() for value in (backup_parent, restore_parent)]
        data = self.studio.db.data_root
        for path in paths:
            if not path.is_dir() or path == Path(path.anchor) or path.is_relative_to(data) or data.is_relative_to(path) or any(p.is_junction() or p.is_symlink() for p in [path, *path.parents]):
                raise DomainError("Chọn thư mục tồn tại, ngoài data root và không qua liên kết.")
        with self.studio.db.transaction() as conn:
            row = conn.execute("SELECT revision FROM recovery_settings WHERE id=1").fetchone()
            if (row[0] if row else 0) != expected_revision:
                raise DomainError("Cấu hình đã thay đổi; tải lại.", 409)
            conn.execute("INSERT OR REPLACE INTO recovery_settings VALUES(1,?,?,?)", (expected_revision + 1, *(str(p) for p in paths)))
        return self.settings()

    def jobs(self):
        with self.studio.db.connect() as conn:
            return [{**dict(row), 'result': json.loads(row['result_json']) if row['result_json'] else None}
                    for row in conn.execute("SELECT * FROM recovery_jobs WHERE kind IN ('BACKUP','RESTORE') ORDER BY created_at DESC")]

    def enqueue(self, kind, backup_id=None, ue_closed=False):
        settings = self.settings()
        if not settings['revision'] or kind not in ('BACKUP', 'RESTORE'):
            raise DomainError("Lưu thư mục sao lưu/phục hồi trước.")
        if not ue_closed:
            raise DomainError("Đóng UE và các ứng dụng đang sửa media; xác nhận trong giao diện.", 409)
        if kind == 'RESTORE':
            with self.studio.db.connect() as conn:
                source = require(conn.execute("SELECT * FROM recovery_jobs WHERE id=? AND kind='BACKUP' AND state='COMPLETED'", (backup_id,)).fetchone(), 'backup')
            bundle = json.loads(source['result_json'])['bundle']
        else:
            bundle = None
        job_id = uid()
        request = {'settings': settings, 'bundle': bundle, 'ue_closed_operator_confirmed': True}
        with self.studio.db.transaction() as conn:
            conn.execute("INSERT INTO recovery_jobs(id,kind,state,request_json,created_at,updated_at,operation_id,input_hash) VALUES(?,?,'QUEUED',?,?,?,?,?)", (job_id, kind, canonical(request), now(), now(), job_id, digest(request)))
            self.studio.audit(conn, None, 'recovery.queued', job_id, {'kind': kind, 'operation_id': job_id})
        return next(j for j in self.jobs() if j['id'] == job_id)

    def _quiescent(self, conn):
        for table in JOB_TABLES:
            if conn.execute(f"SELECT 1 FROM {table} WHERE state IN ({','.join('?' for _ in ACTIVE)}) LIMIT 1", ACTIVE).fetchone():
                raise DomainError("Còn công việc chờ/chạy/chưa đối soát; hoàn tất trước khi snapshot.", 409)
        if conn.execute("SELECT 1 FROM gpu_heavy_leases LIMIT 1").fetchone():
            raise DomainError("GPU lease chưa đối soát; chưa thể sao lưu.", 409)

    def _copy_tree(self, source, target):
        before = inventory(source)
        target.mkdir(parents=True, exist_ok=False)
        for item in before:
            dest = target / item['path']
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / item['path'], dest)
        if inventory(source) != before or inventory(target) != before:
            raise DomainError("Nguồn thay đổi hoặc bản sao sai checksum; giữ bản partial để kiểm tra.", 409)

    def _backup(self, job):
        settings = json.loads(job['request_json'])['settings']
        self._validate_parent(Path(settings['backup_parent']))
        target = Path(settings['backup_parent']) / ('studio-' + job['id'])
        # A reserved writer lock blocks enqueue/mutations while filesystem and DB are captured.
        with self.studio.db.transaction() as locked:
            self._quiescent(locked)
            roots = {r['id']: Path(r['root']) for r in locked.execute("SELECT id,root FROM projects")}
            missing = missing_links(locked, lambda project_id: roots[project_id])
            if missing:
                listed = '; '.join(f"{m['table']}: {m['path']}" for m in missing[:8])
                raise DomainError(f"Thiếu {len(missing)} file media/cache đã đăng ký, chưa sao lưu: {listed}" + ('…' if len(missing) > 8 else ''), 409)
            ue = locked.execute("SELECT project_path FROM unreal_settings WHERE id=1").fetchone()
            ue_path = Path(ue[0]) if ue and ue[0] else None
            ue_included = bool(ue_path and ue_path.is_file())
            ue_root = ue_path.resolve().parent if ue_included else None
            # Checked before anything is copied, so a refused backup leaves no folder behind.
            if ue_root and self.studio.db.data_root.is_relative_to(ue_root):
                raise DomainError("Thư mục dữ liệu của app đang nằm bên trong thư mục project Unreal. Đặt app và project Unreal ở hai thư mục tách nhau rồi sao lưu lại.")
            if ue_root and (target.is_relative_to(ue_root) or ue_root.is_relative_to(target)):
                raise DomainError("Thư mục backup không được nằm trong project UE.")
            target.mkdir(exist_ok=False)
            with self.studio.db.connect() as source, sqlite3.connect(target / 'studio.sqlite3', factory=ClosingConnection) as destination:
                source.backup(destination)
            projects = [dict(r) for r in locked.execute("SELECT * FROM projects")]
            for project in projects:
                self._copy_tree(Path(project['root']), target / 'projects' / project['id'])
            if ue_root:
                self._copy_tree(ue_root, target / 'unreal')
            files = inventory(target)
            manifest = {'schema': 'p7.backup.1', 'id': job['id'], 'created_at': now(), 'projects': projects,
                        'ue_project_name': ue_path.name if ue_included else None, 'ue_project_included': ue_included,
                        'scope': 'DB_ALL_PROJECT_FILES_AND_UE' if ue_included else 'LOCAL_ONLY_NO_UE',
                        'vault_included': False, 'files': files, 'inventory_hash': digest(files)}
            (target / 'backup.json').write_text(canonical(manifest), encoding='utf-8')
        return {'bundle': str(target), 'manifest_sha256': file_sha256(target / 'backup.json'),
                'scope': manifest['scope'], 'file_count': len(files), 'integration': 'UNVERIFIED'}

    def _restore(self, job):
        request = json.loads(job['request_json'])
        self._validate_parent(Path(request['settings']['restore_parent']))
        bundle = Path(request['bundle']).resolve()
        # Pin backup descriptor against accidental edits before accepting any path from it.
        with self.studio.db.connect() as conn:
            backups = [json.loads(r[0]) for r in conn.execute("SELECT result_json FROM recovery_jobs WHERE kind='BACKUP' AND state='COMPLETED'")]
        pin = next((b for b in backups if Path(b['bundle']).resolve() == bundle), None)
        if not pin or file_sha256(bundle / 'backup.json') != pin['manifest_sha256']:
            raise DomainError("Backup manifest bị thay đổi.", 409)
        manifest = json.loads((bundle / 'backup.json').read_text(encoding='utf-8'))
        if [f for f in inventory(bundle) if f['path'] != 'backup.json'] != manifest['files']:
            raise DomainError('Backup inventory/checksum không khớp hoặc có file lạ.', 409)
        for item in manifest['files']:
            path = bundle / safe_relative(bundle, item['path'])
            if not path.is_file() or file_sha256(path) != item['sha256']:
                raise DomainError("Backup thiếu file hoặc checksum hỏng.", 409)
        if digest(manifest['files']) != manifest['inventory_hash']:
            raise DomainError("Backup inventory không khớp.", 409)
        target = Path(request['settings']['restore_parent']) / ('restored-' + job['id'])
        target.mkdir(exist_ok=False)
        for project in manifest['projects']:
            (target / 'projects' / project['id']).mkdir(parents=True, exist_ok=True)
        for item in manifest['files']:
            dest = target / item['path']
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundle / item['path'], dest)
            if file_sha256(dest) != item['sha256']:
                raise DomainError("Restore sai checksum; giữ bản partial.", 409)
        data = target / 'studio.sqlite3'
        with sqlite3.connect(data, factory=ClosingConnection) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            before = {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in PRESERVED if table in tables}
            for project in manifest['projects']:
                conn.execute("UPDATE projects SET root=? WHERE id=?", (str(target / 'projects' / project['id']), project['id']))
            if manifest['ue_project_name']:
                conn.execute("UPDATE unreal_settings SET project_path=?,editor_path='' WHERE id=1", (str(target / 'unreal' / manifest['ue_project_name']),))
            # A restored copy is quarantined; old provider tasks/operation paths must never replay.
            conn.execute("UPDATE recovery_jobs SET state='RESTORED_ARCHIVE' WHERE state IN ('QUEUED','RUNNING','RECONCILING')")
            conn.execute("DELETE FROM recovery_settings")
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            unchanged = all(conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() == rows for table, rows in before.items())
            if integrity != 'ok' or fk or not unchanged:
                raise DomainError("Bản phục hồi không qua kiểm tra toàn vẹn DB, checkpoint hoặc sổ chi phí.", 409)
            # Project-scoped persisted file references must resolve after relocation.
            if missing_links(conn, lambda project_id: target / 'projects' / project_id):
                raise DomainError("Restore thiếu liên kết media/cache đã đăng ký.", 409)
        result = {'root': str(target), 'integrity': integrity, 'foreign_key_errors': len(fk), 'checkpoints_and_ledger_preserved': unchanged,
                  'project_relative_links_verified': True, 'ue_resolve_links': 'UNVERIFIED', 'activation': 'OFFLINE_RESTORED_COPY', 'scope': manifest['scope']}
        (target / 'restore-report.json').write_text(canonical(result), encoding='utf-8')
        return result

    def _validate_parent(self, path):
        data = self.studio.db.data_root
        if not path.is_dir() or path == Path(path.anchor) or path.resolve().is_relative_to(data) or data.is_relative_to(path.resolve()) or any(p.is_junction() or p.is_symlink() for p in [path, *path.parents]):
            raise DomainError('Thư mục đích thay đổi/không an toàn; lưu cấu hình lại.', 409)

    def process_next(self):
        with self.studio.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM recovery_jobs WHERE state='RUNNING'").fetchone():
                return None
            row = conn.execute("SELECT * FROM recovery_jobs WHERE state='QUEUED' AND kind IN ('BACKUP','RESTORE') ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            conn.execute("UPDATE recovery_jobs SET state='RUNNING',attempt=attempt+1,updated_at=? WHERE id=?", (now(), job['id']))
            attempt_id = uid()
            conn.execute("INSERT INTO recovery_attempts VALUES(?,?,?,'RUNNING',NULL,?,NULL)", (attempt_id, job['id'], job['attempt'] + 1, now()))
            self.studio.audit(conn, None, 'recovery.running', job['id'], {'attempt': job['attempt'] + 1})
        try:
            if digest(json.loads(job['request_json'])) != job['input_hash']:
                raise DomainError('Recovery input hash không khớp; không ghi output.', 409)
            result = self._backup(job) if job['kind'] == 'BACKUP' else self._restore(job)
            state, error = 'COMPLETED', None
        except Exception as exc:
            result = None
            state = 'BLOCKED' if isinstance(exc, DomainError) else 'PARTIAL'
            error = str(exc) if isinstance(exc, DomainError) else f'{type(exc).__name__}: I/O lỗi; giữ bản partial, không tự retry.'
        with self.studio.db.transaction() as conn:
            conn.execute('UPDATE recovery_attempts SET state=?,ended_at=? WHERE id=?', (state, now(), attempt_id))
            conn.execute("UPDATE recovery_jobs SET state=?,error=?,result_json=?,updated_at=? WHERE id=?", (state, error, canonical(result) if result else None, now(), job['id']))
            self.studio.audit(conn, None, 'recovery.' + state.lower(), job['id'], {'kind': job['kind']})
        return next(j for j in self.jobs() if j['id'] == job['id'])
