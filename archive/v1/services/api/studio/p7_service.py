"""Acceptance evidence and offline recovery. Never launches UE or paid requests."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sqlite3
from pathlib import Path
from fractions import Fraction

from .domain import DomainError, canonical, digest, safe_relative
from .db import ClosingConnection
from .p4_domain import file_sha256
from .service import Studio, contains_secret_key, now, require, uid

REPO = Path(__file__).resolve().parents[3]
ACTION_RUBRIC = ["walk", "walk_turn", "walk_stop", "run", "run_stop", "sit", "stand_up", "reach",
                 "pick_up", "put_down", "point", "wave", "nod", "shake_head", "listen", "talk",
                 "angry_talk", "surprised_react", "paired_handoff", "paired_contact"]
CAMERA_RUBRIC = ["static_dialogue", "follow_walking", "two_shot", "wide", "medium", "close_up",
                 "over_shoulder", "pan", "dolly", "orbit"]
FAULTS = ["backend_restart", "ue_after_write_before_ack", "disk_full", "cloud_timeout",
          "output_corruption", "duplicate_event", "cancel", "restore_backup"]
ACTIVE = ('QUEUED', 'RUNNING', 'RECONCILING', 'PENDING', 'BUILDING', 'CANCEL_REQUESTED')
METRICS = {'assemble_seconds', 'render_seconds', 'generate_seconds', 'vram_peak_mib', 'ram_peak_mib', 'manual_actions', 'cloud_actual'}


def clean_payload(value):
    if contains_secret_key(value):
        raise DomainError("Evidence không được chứa trường khóa/bí mật.")
    # Refuse recognizable credentials even in unstructured notes. No raw echo in errors.
    if re.search(r"(?i)(sk-[\w-]{12,}|bearer\s+\S+|(?:api[_ -]?key|password|secret)\s*[:=]\s*\S+)", canonical(value)):
        raise DomainError("Evidence có dữ liệu giống bí mật; hãy loại bỏ trước khi lưu.")
    return value


def source_build():
    return digest({p.relative_to(REPO).as_posix(): file_sha256(p)
                   for folder in ('services/api', 'apps/web/src', 'schemas')
                   for p in sorted((REPO / folder).rglob('*'))
                   if p.is_file() and p.suffix in ('.py', '.sql', '.tsx', '.ts', '.css', '.json')})


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
                raise DomainError("Sao lưu/evidence không chấp nhận symlink hoặc junction.", 409)
        for name in files:
            path = Path(current) / name
            result.append({'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size, 'sha256': file_sha256(path)})
    return sorted(result, key=lambda item: item['path'])


class AcceptanceService:
    def __init__(self, studio: Studio):
        self.studio = studio
        with studio.db.transaction() as conn:
            for row in conn.execute('SELECT id,request_json FROM recovery_jobs WHERE operation_id IS NULL').fetchall():
                conn.execute('UPDATE recovery_jobs SET operation_id=?,input_hash=? WHERE id=?', (row['id'], digest(json.loads(row['request_json'])), row['id']))
            conn.execute("UPDATE recovery_jobs SET state='RECONCILING',error='Khởi động lại; bản sao có thể chưa đầy đủ, không tự chạy lại.' WHERE state='RUNNING'")
            conn.execute("UPDATE recovery_attempts SET state='RECONCILING',ended_at=? WHERE state='RUNNING'", (now(),))

    def catalog(self):
        # Runtime labels must be available in source distributions without design documents.
        cases = json.loads((Path(__file__).parent / 'resources' / 'acceptance-catalog.json').read_text(encoding='utf-8'))
        return {'cases': cases, 'actions': ACTION_RUBRIC, 'cameras': CAMERA_RUBRIC, 'faults': FAULTS,
                'rubric_version': 'p7.rubric.1', 'required_action_pass': 17, 'required_camera_pass': 8}

    def _snapshot(self, project_id, episode_id):
        self.studio.project(project_id)
        with self.studio.db.connect() as conn:
            episode = require(conn.execute("SELECT * FROM episodes WHERE id=? AND project_id=?", (episode_id, project_id)).fetchone(), 'episode')
            shots = [dict(r) for r in conn.execute("SELECT s.id,s.code,s.current_revision,r.manifest_json,r.input_hash FROM shots s JOIN shot_revisions r ON r.shot_id=s.id AND r.revision=s.current_revision WHERE s.episode_id=? ORDER BY s.ordinal", (episode_id,))]
            approvals = [dict(r) for r in conn.execute("SELECT scope,scope_id,exact_version,actor FROM approvals WHERE project_id=?", (project_id,))]
            dependencies = self._dependencies(conn, episode_id)
            receipts = [dict(r) for r in conn.execute("SELECT r.* FROM receipts r WHERE operation_id IN (SELECT operation_id FROM jobs WHERE project_id=? UNION SELECT operation_id FROM unreal_episode_jobs WHERE project_id=? UNION SELECT operation_id FROM voice_jobs WHERE project_id=? UNION SELECT operation_id FROM video_jobs WHERE project_id=? UNION SELECT operation_id FROM foley_jobs WHERE project_id=?)", (project_id,) * 5)]
            renders = [dict(r) for r in conn.execute("SELECT * FROM render_inventories WHERE project_id=?", (project_id,))]
            for shot in shots:
                shot['manifest'] = json.loads(shot.pop('manifest_json'))
        profile = json.loads((REPO / 'docs/capability-profile.json').read_text(encoding='utf-8'))
        return clean_payload({'episode': episode, 'shots': shots, 'approvals': approvals, 'receipts': receipts,
                              'renders': renders, 'dependencies': dependencies, 'build_sha256': source_build(), 'profile': profile,
                              'profile_sha256': digest(profile), 'rubric': self.catalog()['rubric_version'],
                              'baseline': self.baseline(project_id)})

    @staticmethod
    def _dependencies(conn, episode_id):
        """Everything a run's review depends on beyond the shot revision number."""
        shots = conn.execute("SELECT s.id,s.project_id,s.current_revision,r.input_hash,r.resolved_registry_json FROM shots s JOIN shot_revisions r ON r.shot_id=s.id AND r.revision=s.current_revision WHERE s.episode_id=? ORDER BY s.ordinal", (episode_id,)).fetchall()
        result = []
        for shot in shots:
            latest = {r['registry_id']: r['id'] for r in conn.execute("SELECT a.registry_id,a.id FROM asset_versions a JOIN (SELECT registry_id,MAX(version) version FROM asset_versions WHERE project_id=? GROUP BY registry_id) m ON a.registry_id=m.registry_id AND a.version=m.version WHERE a.project_id=?", (shot['project_id'], shot['project_id']))}
            pins = json.loads(shot['resolved_registry_json'])
            result.append({
                'shot_id': shot['id'], 'revision': shot['current_revision'], 'input_hash': shot['input_hash'],
                'registry': {registry_id: latest.get(registry_id) for registry_id in sorted(pins)},
                'registry_matches_pins': all(latest.get(registry_id) == pin['asset_version_id'] for registry_id, pin in pins.items()),
                'artifacts': [r[0] for r in conn.execute("SELECT id FROM artifact_versions WHERE shot_id=? AND validity='CURRENT' ORDER BY id", (shot['id'],))],
                'voice': [r[0] for r in conn.execute("SELECT take_id FROM voice_selections WHERE shot_id=? ORDER BY take_id", (shot['id'],))],
                'timing': [r[0] for r in conn.execute("SELECT id FROM timing_revisions WHERE shot_id=? AND validity='CURRENT' ORDER BY id", (shot['id'],))]})
        return result

    def baseline(self, project_id):
        self.studio.project(project_id)
        with self.studio.db.connect() as conn:
            row = conn.execute('SELECT * FROM acceptance_baselines WHERE project_id=? ORDER BY version DESC LIMIT 1', (project_id,)).fetchone()
        return {**dict(row), 'payload': json.loads(row['payload_json'])} if row else None

    def save_baseline(self, project_id, expected_version, limits, currency, evidence_path, confirmed):
        if not confirmed or set(limits) != METRICS or currency not in ('USD', 'CNY'):
            raise DomainError('Chốt đủ ngưỡng theo evidence P0 trước khi tạo run thật.')
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in limits.values()):
            raise DomainError('Ngưỡng phải hữu hạn và không âm.')
        evidence = self._files(project_id, [evidence_path])[0]
        payload = {'limits': limits, 'currency': currency, 'evidence': evidence,
                   'actor': 'LOCAL_USER', 'provenance': 'P0_OPERATOR_ATTESTED_UNVERIFIED'}
        with self.studio.db.transaction() as conn:
            version = conn.execute('SELECT COALESCE(MAX(version),0) FROM acceptance_baselines WHERE project_id=?', (project_id,)).fetchone()[0]
            if expected_version != version:
                raise DomainError('Baseline đổi ở phiên khác; tải lại.', 409)
            conn.execute('INSERT INTO acceptance_baselines VALUES(?,?,?,?,?)', (uid(), project_id, version + 1, canonical(payload), now()))
        return self.baseline(project_id)

    def create_run(self, project_id, episode_id, evidence_kind):
        if evidence_kind not in ('LOCAL_CONTRACT', 'REAL_INTEGRATION'):
            raise DomainError("Loại evidence không hợp lệ.")
        snapshot = self._snapshot(project_id, episode_id)
        if evidence_kind == 'REAL_INTEGRATION' and not snapshot['baseline']:
            raise DomainError('Chốt baseline P0 trong app trước khi tạo hồ sơ run thật.', 409)
        item = {'id': uid(), 'project_id': project_id, 'episode_id': episode_id, 'evidence_kind': evidence_kind,
                'snapshot_json': canonical(snapshot), 'snapshot_hash': digest(snapshot), 'created_at': now()}
        with self.studio.db.transaction() as conn:
            conn.execute("INSERT INTO acceptance_runs VALUES(:id,:project_id,:episode_id,:evidence_kind,:snapshot_json,:snapshot_hash,:created_at)", item)
            self.studio.audit(conn, project_id, 'acceptance.run.created', item['id'], {'scope': evidence_kind})
        return self.run(item['id'])

    def runs(self, project_id):
        self.studio.project(project_id)
        with self.studio.db.connect() as conn:
            return [self.run(row[0]) for row in conn.execute("SELECT id FROM acceptance_runs WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()]

    def run(self, run_id):
        with self.studio.db.connect() as conn:
            item = require(conn.execute("SELECT * FROM acceptance_runs WHERE id=?", (run_id,)).fetchone(), 'run')
            item['snapshot'] = json.loads(item.pop('snapshot_json'))
            item['records'] = [{**dict(r), 'payload': json.loads(r['payload_json'])} for r in conn.execute("SELECT id,version,kind,payload_json,created_at FROM acceptance_records WHERE run_id=? ORDER BY version", (run_id,))]
        item['version'] = len(item['records'])
        with self.studio.db.connect() as conn:
            current = {r['id']: r['current_revision'] for r in conn.execute('SELECT id,current_revision FROM shots WHERE episode_id=?', (item['episode_id'],))}
            dependencies = self._dependencies(conn, item['episode_id'])
        pinned = item['snapshot'].get('dependencies')
        # Runs snapshotted before dependency pins existed still go STALE when the registry moved past a shot's pins.
        fresh = dependencies == pinned if pinned is not None else all(d['registry_matches_pins'] for d in dependencies)
        item['validity'] = 'CURRENT' if fresh and current == {s['id']: s['current_revision'] for s in item['snapshot']['shots']} else 'STALE'
        shots = item['snapshot']['shots']
        ordered = [s['manifest'] for s in shots]
        contiguous = all(a['timeline']['out_exclusive'] == b['timeline']['in'] for a, b in zip(ordered, ordered[1:]))
        duration = sum((Fraction(m['timeline']['out_exclusive'] - m['timeline']['in']) * m['fps']['den'] / m['fps']['num'] for m in ordered), Fraction(0))
        item['d1_metadata_matches'] = len(shots) == 6 and contiguous and duration == 48 and ordered[0]['timeline']['in'] == 0
        item['execution_status'] = 'NOT_VERIFIED'
        return item

    def _files(self, project_id, paths):
        root = Path(self.studio.project(project_id)['root'])
        if not paths or len(paths) > 1000:
            raise DomainError("Chọn ít nhất một file evidence, tối đa 1000 file.")
        result = []
        for value in paths:
            relative = safe_relative(root, value)
            file = root / relative
            if not file.is_file() or file.is_symlink() or any(p.is_junction() or p.is_symlink() for p in file.parents if p.is_relative_to(root)):
                raise DomainError("File evidence thiếu hoặc là liên kết.", 409)
            # Reports must be scrubbed; inspect text before copying into an evidence package.
            if file.suffix.lower() in ('.txt', '.json', '.md', '.log'):
                clean_payload(file.read_text(encoding='utf-8'))
            result.append({'path': relative, 'sha256': file_sha256(file), 'bytes': file.stat().st_size})
        return result

    def record(self, run_id, expected_version, kind, payload):
        run = self.run(run_id)
        clean_payload(payload)
        if kind == 'CASE':
            if payload.get('tc') not in {c['id'] for c in self.catalog()['cases']} or payload.get('tier') not in ('UNIT_CONTRACT', 'INTEGRATION', 'UAT') or payload.get('status') not in ('PASS', 'FAIL', 'BLOCKED'):
                raise DomainError("TC, loại evidence hoặc kết quả không hợp lệ; mandatory case không được N/A.")
            if payload['status'] == 'PASS' and not payload.get('paths'):
                raise DomainError("PASS cần file evidence, không chỉ ghi chú.")
            payload = {**payload, 'files': self._files(run['project_id'], payload['paths']) if payload.get('paths') else [],
                       'provenance': 'OPERATOR_REPORTED_UNVERIFIED'}
            if run['evidence_kind'] == 'LOCAL_CONTRACT' and payload['tier'] != 'UNIT_CONTRACT':
                raise DomainError("Run local không được nhận evidence integration/UAT.")
        elif kind == 'MEASUREMENTS':
            required = {'cache_mode', 'assemble_seconds', 'render_seconds', 'generate_seconds', 'vram_peak_mib', 'ram_peak_mib', 'manual_actions', 'cloud_actual', 'currency', 'baseline_path'}
            if set(payload) != required or payload['cache_mode'] not in ('COLD', 'WARM') or payload['currency'] not in ('USD', 'CNY'):
                raise DomainError("Thiếu/sai trường đo hiệu năng hoặc baseline P0.")
            for key in required - {'cache_mode', 'currency', 'baseline_path'}:
                if type(payload[key]) not in (float, int) or not math.isfinite(payload[key]) or payload[key] < 0:
                    raise DomainError("Số đo phải hữu hạn và không âm.")
            if type(payload['manual_actions']) is not int:
                raise DomainError('Số thao tác tay phải là số nguyên.')
            baseline_file = self._files(run['project_id'], [payload['baseline_path']])[0]
            baseline = run['snapshot']['baseline']
            if run['evidence_kind'] == 'REAL_INTEGRATION' and (not baseline or baseline_file != baseline['payload']['evidence'] or payload['currency'] != baseline['payload']['currency']):
                raise DomainError('Số đo phải dùng baseline/currency đã pin trước run.', 409)
            exceeded = [key for key in METRICS if baseline and payload[key] > baseline['payload']['limits'][key]]
            payload = {**payload, 'baseline_file': baseline_file, 'exceeded_limits': exceeded,
                       'baseline_comparison': ('FAIL' if exceeded else 'WITHIN_DECLARED_LIMITS') if baseline else 'BLOCKED_NO_PINNED_BASELINE',
                       'provenance': 'OPERATOR_MEASURED_NOT_AUTOMATIC_TELEMETRY'}
        elif kind == 'HUMAN_REVIEW':
            if run['validity'] != 'CURRENT':
                raise DomainError('Input run đã STALE; tạo run mới để review.', 409)
            if set(payload) != {'decision', 'actions', 'cameras', 'wrong_actor_count', 'manual_overwrite_count', 'notes'} or payload['decision'] not in ('APPROVE', 'REJECT'):
                raise DomainError("Review cần decision, rubric và số lỗi actor/overwrite.")
            for name, ids in [('actions', ACTION_RUBRIC), ('cameras', CAMERA_RUBRIC)]:
                if not isinstance(payload[name], dict) or set(payload[name]) != set(ids) or any(type(v) is not bool for v in payload[name].values()):
                    raise DomainError("Phải chấm đủ 20 action và 10 camera, không suy rộng một mẫu.")
            if any(type(payload[k]) is not int or payload[k] < 0 for k in ('wrong_actor_count', 'manual_overwrite_count')):
                raise DomainError("Số lỗi phải là số nguyên không âm.")
            if payload['decision'] == 'APPROVE' and (sum(payload['actions'].values()) < 17 or sum(payload['cameras'].values()) < 8 or payload['wrong_actor_count'] or payload['manual_overwrite_count']):
                raise DomainError('Chưa đạt ≥17/20 action, ≥8/10 camera hoặc còn sai actor/overwrite; không duyệt hồ sơ.', 409)
            payload = {**payload, 'actor': 'LOCAL_USER', 'exact_record_version': expected_version}
        else:
            raise DomainError("Loại bản ghi không hợp lệ.")
        with self.studio.db.transaction() as conn:
            version = conn.execute("SELECT COUNT(*) FROM acceptance_records WHERE run_id=?", (run_id,)).fetchone()[0]
            if version != expected_version:
                raise DomainError("Run đã thay đổi; tải lại trước khi ghi evidence/review.", 409)
            conn.execute("INSERT INTO acceptance_records VALUES(?,?,?,?,?,?)", (uid(), run_id, version + 1, kind, canonical(payload), now()))
        return self.run(run_id)

    def gate(self, project_id):
        runs = self.runs(project_id)
        blockers = ['P0 chưa có baseline nghiệm thu được kiểm chứng; P2–P6 chưa đóng integration gates.',
                    'Evidence nhập tay là báo cáo của người vận hành, chưa thay cho kiểm tra receipt UE/provider và UAT.',
                    'Chưa thực thi ba pilot D1 thật và restore/rerender trên máy UE.']
        real = [r for r in runs if r['evidence_kind'] == 'REAL_INTEGRATION']
        if len(real) < 3:
            blockers.append(f'Mới có {len(real)}/3 hồ sơ run môi trường thật (hồ sơ không xác nhận đã chạy).')
        cases = []
        for case in self.catalog()['cases']:
            records = [record['payload'] for run in runs for record in run['records'] if record['kind'] == 'CASE' and record['payload']['tc'] == case['id']]
            cases.append({**case, 'reported': records, 'acceptance_status': 'BLOCKED'})
        return {'status': 'BLOCKED', 'long_pilot_allowed': False, 'blockers': blockers, 'cases': cases,
                'open_defects': ['P6-PRODUCTION-VIDEO', 'P3-FACIAL-APPLY', 'P4-MRQ-CACHE', 'P7-REAL-PILOT', 'P7-RESTORE-RERENDER']}

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

    def recovery_jobs(self):
        with self.studio.db.connect() as conn:
            jobs = []
            for row in conn.execute('SELECT * FROM recovery_jobs ORDER BY created_at DESC').fetchall():
                job = {**dict(row), 'result': json.loads(row['result_json']) if row['result_json'] else None}
                job['attempts'] = [dict(a) for a in conn.execute('SELECT * FROM recovery_attempts WHERE job_id=? ORDER BY attempt', (row['id'],))]
                job['dependencies'] = [r[0] for r in conn.execute('SELECT depends_on_id FROM recovery_dependencies WHERE job_id=?', (row['id'],))]
                receipt = conn.execute('SELECT * FROM receipts WHERE operation_id=?', (row['operation_id'],)).fetchone()
                job['receipt'] = dict(receipt) if receipt else None
                jobs.append(job)
            return jobs

    def enqueue_recovery(self, kind, backup_id=None, ue_closed=False):
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
        return next(j for j in self.recovery_jobs() if j['id'] == job_id)

    def _quiescent(self, conn):
        for table in ('jobs', 'unreal_episode_jobs', 'voice_jobs', 'planning_jobs', 'video_jobs', 'foley_jobs', 'delivery_packages', 'series_extraction_jobs', 'ue_asset_scans', 'style_capture_jobs', 'breakdown_jobs', 'dialogue_jobs', 'episode_ue_jobs', 'ai_video_jobs', 'ue_proxy_jobs', 'rough_cut_jobs', 'final_jobs', 'episode_foley_jobs', 'episode_sound_jobs', 'episode_delivery_jobs'):
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
            target.mkdir(exist_ok=False)
            with self.studio.db.connect() as source, sqlite3.connect(target / 'studio.sqlite3', factory=ClosingConnection) as destination:
                source.backup(destination)
            projects = [dict(r) for r in locked.execute("SELECT * FROM projects")]
            for project in projects:
                self._copy_tree(Path(project['root']), target / 'projects' / project['id'])
            ue = locked.execute("SELECT project_path FROM unreal_settings WHERE id=1").fetchone()
            ue_path = Path(ue[0]) if ue and ue[0] else None
            ue_included = bool(ue_path and ue_path.is_file())
            if ue_included:
                ue_root = ue_path.resolve().parent
                if target.is_relative_to(ue_root) or ue_root.is_relative_to(target) or self.studio.db.data_root.is_relative_to(ue_root):
                    raise DomainError("Thư mục backup không được nằm trong project UE.")
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
            before = {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in ('approvals', 'subsystem_states', 'shot_revisions')}
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
                raise DomainError("DB restore integrity/approval/lock/revision không đạt.", 409)
            # Project-scoped persisted file references must resolve after relocation.
            if missing_links(conn, lambda project_id: target / 'projects' / project_id):
                raise DomainError("Restore thiếu liên kết media/cache đã đăng ký.", 409)
        result = {'root': str(target), 'integrity': integrity, 'foreign_key_errors': len(fk), 'versions_locks_approvals_preserved': unchanged,
                  'project_relative_links_verified': True, 'ue_resolve_links': 'UNVERIFIED',
                  'rerender': 'BLOCKED_REQUIRES_REAL_UE', 'activation': 'OFFLINE_RESTORED_COPY', 'scope': manifest['scope']}
        (target / 'restore-report.json').write_text(canonical(result), encoding='utf-8')
        return result

    def _validate_parent(self, path):
        data = self.studio.db.data_root
        if not path.is_dir() or path == Path(path.anchor) or path.resolve().is_relative_to(data) or data.is_relative_to(path.resolve()) or any(p.is_junction() or p.is_symlink() for p in [path, *path.parents]):
            raise DomainError('Thư mục đích thay đổi/không an toàn; lưu cấu hình lại.', 409)

    def process_recovery(self):
        with self.studio.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM recovery_jobs WHERE state='RUNNING'").fetchone():
                return None
            conn.execute("UPDATE recovery_jobs SET state='BLOCKED',error='Dependency failed/requires reconcile' WHERE state='QUEUED' AND EXISTS(SELECT 1 FROM recovery_dependencies d JOIN recovery_jobs p ON p.id=d.depends_on_id WHERE d.job_id=recovery_jobs.id AND p.state IN ('BLOCKED','PARTIAL','RECONCILING'))")
            row = conn.execute("SELECT * FROM recovery_jobs WHERE state='QUEUED' AND NOT EXISTS(SELECT 1 FROM recovery_dependencies d JOIN recovery_jobs p ON p.id=d.depends_on_id WHERE d.job_id=recovery_jobs.id AND p.state!='COMPLETED') ORDER BY created_at LIMIT 1").fetchone()
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
            if job['kind'] == 'BACKUP':
                result = self._backup(job)
            elif job['kind'] == 'RESTORE':
                result = self._restore(job)
            else:
                request = json.loads(job['request_json'])
                if self.run(request['run_id'])['version'] != request['version']:
                    raise DomainError('Run thay đổi sau enqueue; xuất đúng phiên bản mới.', 409)
                result = self._export_evidence(request['run_id'])
            state, error = 'COMPLETED', None
        except Exception as exc:
            result = None
            state = 'BLOCKED' if isinstance(exc, DomainError) else 'PARTIAL'
            error = str(exc) if isinstance(exc, DomainError) else f'{type(exc).__name__}: I/O lỗi; giữ bản partial, không tự retry.'
        with self.studio.db.transaction() as conn:
            receipt_id = None
            if state == 'COMPLETED':
                receipt_id = uid()
                output = {'schema': 'p7.maintenance.result.1', 'operation_id': job['operation_id'], 'input_hash': job['input_hash'],
                          'kind': job['kind'], 'provenance': 'LOCAL_FILESYSTEM_VERIFIED_NOT_UE_INTEGRATION', 'result': result}
                conn.execute("INSERT INTO receipts VALUES(?,?,?,'COMPLETED',?,?)", (receipt_id, job['operation_id'], job['input_hash'], canonical(output), now()))
            conn.execute('UPDATE recovery_attempts SET state=?,receipt_id=?,ended_at=? WHERE id=?', (state, receipt_id, now(), attempt_id))
            conn.execute("UPDATE recovery_jobs SET state=?,error=?,result_json=?,updated_at=? WHERE id=?", (state, error, canonical(result) if result else None, now(), job['id']))
            self.studio.audit(conn, None, 'recovery.' + state.lower(), job['id'], {'receipt_id': receipt_id, 'kind': job['kind']})
        return next(j for j in self.recovery_jobs() if j['id'] == job['id'])

    def export_evidence(self, run_id, expected_version):
        run = self.run(run_id)
        if run['version'] != expected_version:
            raise DomainError('Run đã thay đổi; tải lại.', 409)
        job_id = uid()
        with self.studio.db.transaction() as conn:
            request = {'run_id': run_id, 'version': expected_version}
            conn.execute("INSERT INTO recovery_jobs(id,kind,state,request_json,created_at,updated_at,operation_id,input_hash) VALUES(?,'EVIDENCE','QUEUED',?,?,?,?,?)", (job_id, canonical(request), now(), now(), job_id, digest(request)))
        return next(j for j in self.recovery_jobs() if j['id'] == job_id)

    def _export_evidence(self, run_id):
        run = self.run(run_id)
        root = Path(self.studio.project(run['project_id'])['root'])
        target = root / 'evidence' / f"{run_id}-v{run['version']}"
        files = {f['path']: f for r in run['records'] for f in r['payload'].get('files', [])}
        for record in run['records']:
            f = record['payload'].get('baseline_file')
            if f:
                files[f['path']] = f
        for f in files.values():
            if file_sha256(root / safe_relative(root, f['path'])) != f['sha256']:
                raise DomainError("File evidence đổi sau khi đăng ký.", 409)
        target.mkdir(parents=True, exist_ok=False)
        for f in files.values():
            dest = target / 'samples' / f['path']
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / f['path'], dest)
            if file_sha256(dest) != f['sha256']:
                raise DomainError("Copy evidence sai checksum.", 409)
        (target / 'run.json').write_text(canonical(clean_payload(run)), encoding='utf-8')
        (target / 'gate.json').write_text(canonical(self.gate(run['project_id'])), encoding='utf-8')
        (target / 'inventory.json').write_text(canonical(inventory(target)), encoding='utf-8')
        return {'relative_path': target.relative_to(root).as_posix(), 'scope': run['evidence_kind'], 'version': run['version']}
