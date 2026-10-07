"""HTTP surface for workflow V4.5 foundations and the series library. Session policy comes from the owning app."""
from pydantic import BaseModel, ConfigDict, Field

from . import gpu_queue, workflow
from .budget import BudgetService
from .domain import DomainError, UE_TARGET_VERSION
from .series_service import SeriesService
from .tool_settings import ToolSettings, probe_ffmpeg


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BudgetPolicyIn(Strict):
    expected_revision: int
    monthly_cap_micro_usd: int
    monthly_cap_micro_cny: int
    warn_percent: int
    paid_paused: bool


class LedgerResolveIn(Strict):
    action: str
    confirmed: bool = False


class ToolPathIn(Strict):
    ffmpeg_path: str


class SaveToolPathIn(ToolPathIn):
    expected_revision: int


class StandardsIn(Strict):
    expected_revision: int
    fps_num: int
    fps_den: int
    width: int
    height: int
    color_space: str
    lut_name: str
    default_video_model: str


class SeriesScreenplayIn(Strict):
    episode_id: str
    text: str
    language: str
    estimated_seconds: int


class ExtractionIn(Strict):
    screenplay_id: str
    client_request_id: str | None = None


class EntityIn(Strict):
    kind: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ''


class EntityUpdateIn(Strict):
    expected_revision: int
    name: str
    aliases: list[str]
    description: str


class EntityMergeIn(Strict):
    source_revision: int
    target_id: str
    target_revision: int


class RevisionIn(Strict):
    expected_revision: int


class WorkerStoppedIn(Strict):
    worker_stopped: bool = False


class MappingIn(Strict):
    expected_revision: int
    status: str
    asset_path: str | None = None
    body_mesh_path: str | None = None
    face_mesh_path: str | None = None
    note: str = ''


class FoldersIn(Strict):
    expected_revision: int
    animation_folders: list[str]
    vfx_folders: list[str]
    retargeters: list[dict]


class ApproveIn(Strict):
    expected_draft_hash: str


def install(app, studio, series: SeriesService, budget: BudgetService, tools: ToolSettings):
    @app.get('/workflow/definition')
    def workflow_definition():
        return {'checkpoints': workflow.CHECKPOINTS, 'target_engine': UE_TARGET_VERSION}

    @app.get('/projects/{project_id}/workflow')
    def workflow_status(project_id: str):
        return workflow.status(studio, project_id)

    @app.get('/episodes/{episode_id}/screenplays')
    def screenplays(episode_id: str):
        return series.screenplays(episode_id)

    @app.get('/projects/{project_id}/budget')
    def budget_summary(project_id: str):
        return budget.summary(project_id)

    @app.post('/projects/{project_id}/budget/policy')
    def save_budget_policy(project_id: str, body: BudgetPolicyIn):
        return budget.save_policy(project_id, **body.model_dump())

    @app.post('/budget/entries/{entry_id}/resolve')
    def resolve_budget_entry(entry_id: str, body: LedgerResolveIn):
        return budget.resolve(entry_id, body.action, body.confirmed)

    @app.get('/settings/tools')
    def tool_settings():
        return tools.get()

    @app.post('/settings/tools/validate')
    def validate_tools(body: ToolPathIn):
        return probe_ffmpeg(body.ffmpeg_path)

    @app.post('/settings/tools')
    def save_tools(body: SaveToolPathIn):
        return tools.save(body.expected_revision, body.ffmpeg_path)

    @app.post('/settings/tools/browse')
    def browse_tools():
        from .local_picker import pick_file
        return {'path': pick_file('ffmpeg', tools.get()['ffmpeg_path'])}

    @app.get('/resources/gpu-queue')
    def gpu_queue_status():
        with studio.db.connect() as conn:
            return gpu_queue.waiting(conn)

    @app.get('/projects/{project_id}/series/standards')
    def standards(project_id: str):
        return series.standards(project_id)

    @app.post('/projects/{project_id}/series/standards')
    def save_standards(project_id: str, body: StandardsIn):
        return series.save_standards(project_id, **body.model_dump())

    @app.post('/projects/{project_id}/series/screenplay')
    def series_screenplay(project_id: str, body: SeriesScreenplayIn):
        return series.ingest_screenplay(project_id, **body.model_dump())

    @app.post('/projects/{project_id}/series/extraction/preview')
    def extraction_preview(project_id: str, body: ExtractionIn):
        return series.preview_extraction(project_id, body.screenplay_id)

    @app.post('/projects/{project_id}/series/extraction/jobs')
    def extraction_enqueue(project_id: str, body: ExtractionIn):
        if not body.client_request_id:
            raise DomainError('Thiếu client_request_id.')
        return series.enqueue_extraction(project_id, body.screenplay_id, body.client_request_id)

    @app.get('/projects/{project_id}/series/extraction/jobs')
    def extraction_jobs(project_id: str):
        return series.extraction_jobs(project_id)

    @app.get('/projects/{project_id}/series/entities')
    def entities(project_id: str):
        return series.entities(project_id)

    @app.post('/projects/{project_id}/series/entities')
    def add_entity(project_id: str, body: EntityIn):
        return series.add_entity(project_id, **body.model_dump())

    @app.post('/series/entities/{entity_id}')
    def update_entity(entity_id: str, body: EntityUpdateIn):
        return series.update_entity(entity_id, **body.model_dump())

    @app.post('/series/entities/{entity_id}/merge')
    def merge_entity(entity_id: str, body: EntityMergeIn):
        return series.merge_entities(entity_id, body.source_revision, body.target_id, body.target_revision)

    @app.post('/series/entities/{entity_id}/remove')
    def remove_entity(entity_id: str, body: RevisionIn):
        return series.remove_entity(entity_id, body.expected_revision)

    @app.post('/series/entities/{entity_id}/mapping')
    def set_mapping(entity_id: str, body: MappingIn):
        return series.set_mapping(entity_id, **body.model_dump())

    @app.get('/projects/{project_id}/series/scans')
    def scans(project_id: str):
        return series.scans(project_id)

    @app.post('/projects/{project_id}/series/scans')
    def enqueue_scan(project_id: str):
        return series.enqueue_scan(project_id)

    @app.post('/series/scans/{scan_id}/release')
    def release_scan(scan_id: str, body: WorkerStoppedIn):
        return series.release_interrupted_scan(scan_id, body.worker_stopped)

    @app.get('/projects/{project_id}/series/scan/assets')
    def scan_assets(project_id: str, category: str | None = None, q: str = '', folder: str = '', limit: int = 100, offset: int = 0):
        return series.scan_assets(project_id, category, q, folder, limit, offset)

    @app.get('/projects/{project_id}/series/scan/folders')
    def scan_folders(project_id: str, category: str):
        return series.scan_folders(project_id, category)

    @app.get('/projects/{project_id}/series/mapping')
    def mapping(project_id: str):
        return series.mapping(project_id)

    @app.post('/projects/{project_id}/series/folders')
    def save_folders(project_id: str, body: FoldersIn):
        return series.save_folders(project_id, **body.model_dump())

    @app.post('/projects/{project_id}/series/cs1/approve')
    def approve_cs1(project_id: str, body: ApproveIn):
        return series.approve_cs1(project_id, body.expected_draft_hash)

    @app.get('/projects/{project_id}/series/library')
    def library(project_id: str):
        return series.library_versions(project_id)