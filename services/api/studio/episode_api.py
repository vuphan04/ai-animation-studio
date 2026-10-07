"""HTTP surface for the Style bible (S4/CS2), voice settings and episode zone A (01–04, C1, C2).

Session policy comes from the owning app.
"""
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from .breakdown_service import BreakdownService
from .domain import DomainError
from .style_service import StyleService
from .voice_settings import VoiceSettings


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class VoiceSettingsIn(Strict):
    expected_revision: int
    provider: str
    model_id: str
    output_format: str = 'pcm_24000'


class StyleIn(Strict):
    expected_revision: int
    style_phrases: list[str]
    banned_words: list[str]
    turnaround_map_path: str | None = None


class ProfileIn(Strict):
    expected_revision: int
    prompt_description: str
    voice_mode: str
    voice_id: str | None = None


class RoleIn(Strict):
    role: str


class DecisionIn(Strict):
    decision: str


class CaptureIn(Strict):
    entity_ids: list[str] | None = None


class WorkerStoppedIn(Strict):
    worker_stopped: bool = False


class DraftHashIn(Strict):
    expected_draft_hash: str


class ScreenplayRefIn(Strict):
    screenplay_id: str
    client_request_id: str | None = None


class SceneSaveIn(Strict):
    expected_content_hash: str
    scene: dict


class SceneAddIn(Strict):
    expected_content_hash: str | None = None
    after_scene_key: str | None = None
    heading: str
    environment_name: str


class ContentHashIn(Strict):
    expected_content_hash: str


class SceneMoveIn(ContentHashIn):
    direction: str


class SceneApproveIn(Strict):
    expected_scene_hash: str


class LibraryAddIn(Strict):
    kind: str
    name: str


class AliasIn(Strict):
    name: str
    entity_id: str


class ExpectedHashIn(Strict):
    expected_hash: str


def install(app, voice: VoiceSettings, style: StyleService, breakdown: BreakdownService):
    @app.get('/settings/voice')
    def voice_settings():
        return voice.get()

    @app.post('/settings/voice')
    def save_voice_settings(body: VoiceSettingsIn):
        return voice.save(body.expected_revision, body.provider, body.model_id, body.output_format)

    @app.post('/settings/voice/key/prompt')
    def prompt_voice_key():
        return voice.key()

    @app.post('/settings/voice/key/delete')
    def delete_voice_key():
        return voice.key(delete=True)

    @app.post('/settings/voice/catalog/refresh')
    def refresh_voice_catalog():
        return voice.refresh_catalog()

    @app.get('/settings/voice/voices')
    def voice_list():
        return voice.voices()

    @app.get('/projects/{project_id}/style')
    def style_state(project_id: str):
        return style.state(project_id)

    @app.post('/projects/{project_id}/style')
    def save_style(project_id: str, body: StyleIn):
        return style.save_style(project_id, **body.model_dump())

    @app.post('/series/entities/{entity_id}/style-profile')
    def save_profile(entity_id: str, body: ProfileIn):
        return style.save_profile(entity_id, **body.model_dump())

    @app.post('/series/entities/{entity_id}/references/browse')
    def browse_reference(entity_id: str, body: RoleIn):
        from .local_picker import pick_file
        path = pick_file('image')
        if not path:
            raise DomainError('Đã hủy chọn ảnh.', 409)
        return style.import_reference(entity_id, body.role, path)

    @app.get('/series/references/{reference_id}/file')
    def reference_file(reference_id: str):
        path = style.reference_file(reference_id)
        return FileResponse(path, media_type='image/png' if path.suffix == '.png' else 'image/jpeg', headers={'Cache-Control': 'no-store'})

    @app.post('/series/references/{reference_id}/review')
    def review_reference(reference_id: str, body: DecisionIn):
        return style.review_reference(reference_id, body.decision)

    @app.post('/series/references/{reference_id}/remove')
    def remove_reference(reference_id: str):
        return style.remove_reference(reference_id)

    @app.post('/projects/{project_id}/style/capture')
    def enqueue_capture(project_id: str, body: CaptureIn):
        return style.enqueue_capture(project_id, body.entity_ids)

    @app.post('/style/capture/{job_id}/release')
    def release_capture(job_id: str, body: WorkerStoppedIn):
        return style.release_interrupted_capture(job_id, body.worker_stopped)

    @app.post('/projects/{project_id}/style/cs2/approve')
    def approve_cs2(project_id: str, body: DraftHashIn):
        return style.approve_cs2(project_id, body.expected_draft_hash)

    @app.get('/episodes/{episode_id}/open')
    def open_episode(episode_id: str):
        return breakdown.open_status(episode_id)

    @app.post('/episodes/{episode_id}/breakdown/preview')
    def breakdown_preview(episode_id: str, body: ScreenplayRefIn):
        return breakdown.preview_job(episode_id, body.screenplay_id)

    @app.post('/episodes/{episode_id}/breakdown/jobs')
    def breakdown_enqueue(episode_id: str, body: ScreenplayRefIn):
        if not body.client_request_id:
            raise DomainError('Thiếu client_request_id.')
        return breakdown.enqueue(episode_id, body.screenplay_id, body.client_request_id)

    @app.get('/episodes/{episode_id}/breakdown/jobs')
    def breakdown_jobs(episode_id: str):
        return breakdown.jobs(episode_id)

    @app.get('/episodes/{episode_id}/breakdown')
    def breakdown_current(episode_id: str):
        return breakdown.current(episode_id)

    @app.post('/episodes/{episode_id}/breakdown/scenes/save')
    def save_scene(episode_id: str, body: SceneSaveIn):
        return breakdown.save_scene(episode_id, body.expected_content_hash, body.scene)

    @app.post('/episodes/{episode_id}/breakdown/scenes/add')
    def add_scene(episode_id: str, body: SceneAddIn):
        return breakdown.add_scene(episode_id, **body.model_dump())

    @app.post('/episodes/{episode_id}/breakdown/scenes/{scene_key}/delete')
    def delete_scene(episode_id: str, scene_key: str, body: ContentHashIn):
        return breakdown.delete_scene(episode_id, body.expected_content_hash, scene_key)

    @app.post('/episodes/{episode_id}/breakdown/scenes/{scene_key}/move')
    def move_scene(episode_id: str, scene_key: str, body: SceneMoveIn):
        return breakdown.move_scene(episode_id, body.expected_content_hash, scene_key, body.direction)

    @app.post('/episodes/{episode_id}/breakdown/scenes/{scene_key}/approve')
    def approve_scene(episode_id: str, scene_key: str, body: SceneApproveIn):
        return breakdown.approve_scene(episode_id, scene_key, body.expected_scene_hash)

    @app.post('/episodes/{episode_id}/breakdown/c1/approve')
    def approve_c1(episode_id: str, body: ContentHashIn):
        return breakdown.approve_c1(episode_id, body.expected_content_hash)

    @app.get('/episodes/{episode_id}/compare')
    def compare(episode_id: str):
        return breakdown.compare(episode_id)

    @app.post('/episodes/{episode_id}/compare/add')
    def compare_add(episode_id: str, body: LibraryAddIn):
        return breakdown.add_to_library(episode_id, body.kind, body.name)

    @app.post('/episodes/{episode_id}/compare/alias')
    def compare_alias(episode_id: str, body: AliasIn):
        return breakdown.link_alias(episode_id, body.name, body.entity_id)

    @app.post('/episodes/{episode_id}/compare/export')
    def compare_export(episode_id: str):
        return breakdown.export_missing(episode_id)

    @app.post('/episodes/{episode_id}/c2/approve')
    def approve_c2(episode_id: str, body: ExpectedHashIn):
        return breakdown.approve_c2(episode_id, body.expected_hash)
