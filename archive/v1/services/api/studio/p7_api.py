"""Session/origin policy is inherited from the owning local FastAPI app."""
from pydantic import BaseModel, ConfigDict, Field
from .p7_service import AcceptanceService
from .local_picker import pick_folder, pick_project_output
from .domain import DomainError
from pathlib import Path


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RunIn(Strict):
    episode_id: str
    evidence_kind: str


class RecordIn(Strict):
    expected_version: int = Field(ge=0)
    kind: str
    payload: dict


class SettingsIn(Strict):
    expected_revision: int
    backup_parent: str
    restore_parent: str


class RecoveryIn(Strict):
    kind: str
    backup_id: str | None = None
    ue_closed: bool = False


class VersionIn(Strict):
    expected_version: int


class BaselineIn(Strict):
    expected_version: int
    limits: dict
    currency: str
    evidence_path: str
    confirmed: bool


def install(app, service: AcceptanceService):
    @app.get('/projects/{project_id}/acceptance/baseline')
    def baseline(project_id: str):
        return service.baseline(project_id)

    @app.post('/projects/{project_id}/acceptance/baseline')
    def save_baseline(project_id: str, body: BaselineIn):
        return service.save_baseline(project_id, **body.model_dump())

    @app.get('/acceptance/catalog')
    def catalog():
        return service.catalog()

    @app.get('/projects/{project_id}/acceptance/runs')
    def runs(project_id: str):
        return service.runs(project_id)

    @app.post('/projects/{project_id}/acceptance/runs')
    def create_run(project_id: str, body: RunIn):
        return service.create_run(project_id, **body.model_dump())

    @app.get('/projects/{project_id}/acceptance/gate')
    def gate(project_id: str):
        return service.gate(project_id)

    @app.post('/acceptance/runs/{run_id}/records')
    def record(run_id: str, body: RecordIn):
        return service.record(run_id, **body.model_dump())

    @app.post('/acceptance/runs/{run_id}/export')
    def export(run_id: str, body: VersionIn):
        return service.export_evidence(run_id, body.expected_version)

    @app.post('/projects/{project_id}/acceptance/browse')
    def browse_evidence(project_id: str):
        path = pick_project_output(Path(service.studio.project(project_id)['root']), 'evidence_file')
        if not path:
            raise DomainError('Đã hủy chọn evidence.', 409)
        return {'relative_path': path}

    @app.get('/settings/recovery')
    def settings():
        return service.settings()

    @app.post('/settings/recovery')
    def save_settings(body: SettingsIn):
        return service.save_settings(**body.model_dump())

    @app.post('/settings/recovery/browse')
    def browse():
        path = pick_folder()
        if not path:
            raise DomainError('Đã hủy chọn thư mục.', 409)
        return {'path': path}

    @app.get('/recovery/jobs')
    def recovery_jobs():
        return service.recovery_jobs()

    @app.post('/recovery/jobs')
    def enqueue(body: RecoveryIn):
        return service.enqueue_recovery(**body.model_dump())

    @app.post('/jobs/{job_id}/cancel')
    def cancel(job_id: str):
        return service.studio.cancel(job_id)
