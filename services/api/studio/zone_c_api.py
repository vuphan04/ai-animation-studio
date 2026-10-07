"""HTTP surface for workflow V4.5 zone C after finals: Foley and sound (12/C9), timeline package and QC (13), C10.

Session policy comes from the owning app.
"""
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from .delivery_service import DeliveryService
from .domain import DomainError
from .foley_runner import FIELDS
from .progress import WorkflowProgress
from .sound_service import SoundService


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class FoleyToolIn(Strict):
    expected_revision: int
    python_path: str
    script_path: str
    model_path: str
    config_path: str


class FoleyBrowseIn(Strict):
    field: str
    current: str = ''


class FoleyIn(Strict):
    shot_id: str
    prompt: str


class WorkerStoppedIn(Strict):
    worker_stopped: bool = False


class ClipBrowseIn(Strict):
    kind: str
    shot_id: str


class RevisionIn(Strict):
    expected_revision: int


class ClipIn(RevisionIn):
    shot_id: str
    offset_frames: int
    length_frames: int | None = None
    gain_db: float
    fade_in_frames: int
    fade_out_frames: int
    enabled: bool


class ReviewIn(Strict):
    expected_hash: str
    checks: dict


class DeliveryIn(Strict):
    loudness_lufs: int


def install(app, sound: SoundService, delivery: DeliveryService, progress: WorkflowProgress):
    @app.get('/projects/{project_id}/progress')
    def workflow_progress(project_id: str, episode_id: str | None = None):
        return progress.state(project_id, episode_id or None)

    @app.get('/settings/foley-tool')
    def foley_tool():
        return sound.tool.get()

    @app.post('/settings/foley-tool')
    def save_foley_tool(body: FoleyToolIn):
        return sound.tool.save(body.expected_revision, body.model_dump(exclude={'expected_revision'}))

    @app.post('/settings/foley-tool/browse')
    def browse_foley_tool(body: FoleyBrowseIn):
        from .local_picker import pick_file, pick_folder
        if body.field not in FIELDS:
            raise DomainError('Mục cần chọn không hợp lệ.')
        if body.field == 'model_path':
            return {'path': pick_folder('Chọn thư mục chứa model HunyuanVideo-Foley đã tải về')}
        return {'path': pick_file({'python_path': 'python', 'script_path': 'foley_script', 'config_path': 'foley_config'}[body.field], body.current)}

    @app.get('/episodes/{episode_id}/sound')
    def sound_state(episode_id: str):
        return sound.state(episode_id)

    @app.post('/episodes/{episode_id}/sound/foley')
    def sound_foley(episode_id: str, body: FoleyIn):
        return sound.enqueue_foley(episode_id, body.shot_id, body.prompt)

    @app.post('/sound/foley-jobs/{job_id}/release')
    def sound_foley_release(job_id: str, body: WorkerStoppedIn):
        return sound.release_foley(job_id, body.worker_stopped)

    @app.post('/episodes/{episode_id}/sound/clips/browse')
    def sound_clip_browse(episode_id: str, body: ClipBrowseIn):
        from .local_picker import pick_file
        path = pick_file('audio')
        if not path:
            raise DomainError('Đã hủy chọn file.', 409)
        return sound.import_clip(episode_id, body.kind, body.shot_id, path)

    @app.post('/sound/clips/{clip_id}')
    def sound_clip_update(clip_id: str, body: ClipIn):
        return sound.update_clip(clip_id, **body.model_dump())

    @app.post('/sound/clips/{clip_id}/keep')
    def sound_clip_keep(clip_id: str, body: RevisionIn):
        return sound.keep_foley(clip_id, body.expected_revision)

    @app.post('/sound/clips/{clip_id}/delete')
    def sound_clip_delete(clip_id: str, body: RevisionIn):
        return sound.delete_clip(clip_id, body.expected_revision)

    @app.get('/sound/clips/{clip_id}/file')
    def sound_clip_file(clip_id: str):
        return FileResponse(sound.clip_file(clip_id), headers={'Cache-Control': 'no-store'})

    @app.post('/episodes/{episode_id}/sound/build')
    def sound_build(episode_id: str):
        return sound.enqueue_build(episode_id)

    @app.get('/sound/builds/{build_id}/files/{role}')
    def sound_build_file(build_id: str, role: str):
        return FileResponse(sound.build_file(build_id, role), media_type='video/mp4' if role == 'REVIEW' else 'audio/wav', headers={'Cache-Control': 'no-store'})

    @app.post('/episodes/{episode_id}/c9/approve')
    def approve_c9(episode_id: str, body: ReviewIn):
        return sound.approve_c9(episode_id, body.expected_hash, body.checks)

    @app.get('/episodes/{episode_id}/delivery')
    def delivery_state(episode_id: str):
        return delivery.state(episode_id)

    @app.post('/episodes/{episode_id}/delivery/build')
    def delivery_build(episode_id: str, body: DeliveryIn):
        return delivery.enqueue(episode_id, body.loudness_lufs)

    @app.get('/delivery/{delivery_id}/preview')
    def delivery_preview(delivery_id: str):
        return FileResponse(delivery.preview_file(delivery_id), media_type='video/mp4', headers={'Cache-Control': 'no-store'})

    @app.post('/episodes/{episode_id}/delivery/open-folder')
    def delivery_open(episode_id: str):
        return delivery.open_folder(episode_id)

    @app.post('/episodes/{episode_id}/delivery/export/browse')
    def delivery_export(episode_id: str):
        from .local_picker import pick_file
        path = pick_file('video')
        if not path:
            raise DomainError('Đã hủy chọn file.', 409)
        return delivery.register_export(episode_id, path)

    @app.post('/episodes/{episode_id}/c10/approve')
    def approve_c10(episode_id: str, body: ReviewIn):
        return delivery.approve_c10(episode_id, body.expected_hash, body.checks)
