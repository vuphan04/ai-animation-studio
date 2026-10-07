"""HTTP surface for workflow V4.5 zone B: animation assignment, UE branch builds and C5, AI drafts and C6.

Session policy comes from the owning app.
"""
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from .ai_video_service import AiVideoService
from .animation_service import AnimationService
from .proxy_service import UeProxyService
from .rough_cut_service import RoughCutService
from .domain import DomainError
from .final_service import FinalService
from .ue_branch_service import UeBranchService
from .video_model_settings import VideoModelSettings


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class VideoModelIn(Strict):
    expected_revision: int
    platform: str
    model_id: str


class RevisionIn(Strict):
    expected_revision: int


class AssignIn(RevisionIn):
    clips: list[dict]
    upper_body_path: str | None = None


class KimodoPromptIn(RevisionIn):
    prompt: str


class KimodoVariantsIn(RevisionIn):
    asset_paths: list[str]


class KimodoApproveIn(RevisionIn):
    asset_path: str
    tags: list[str]


class ShotsIn(Strict):
    shot_ids: list[str] | None = None


class WorkerStoppedIn(Strict):
    worker_stopped: bool = False


class C5In(Strict):
    expected_hash: str
    checks: dict
    notes: dict = {}


class AiInputIn(RevisionIn):
    prompt_override: str | None = None
    reference_keys: list[str] | None = None


class DraftIn(Strict):
    client_request_id: str


class TakeReviewIn(Strict):
    decision: str
    checks: dict = {}


class LockDraftIn(Strict):
    take_id: str


class CacheIn(Strict):
    expected_build_hash: str
    declarations: dict


class ExpectedHashIn(Strict):
    expected_hash: str


class SceneScopeIn(Strict):
    scene_key: str | None = None


class SceneLockIn(Strict):
    expected_hash: str
    checks: dict


class FinalBatchIn(Strict):
    shot_ids: list[str] | None = None
    client_request_id: str


def install(app, models: VideoModelSettings, animation: AnimationService, ue: UeBranchService, ai: AiVideoService,
            proxy: UeProxyService, rough: RoughCutService, final: FinalService):
    @app.get('/settings/video-models')
    def video_models():
        return models.get()

    @app.post('/settings/video-models/{model}')
    def save_video_model(model: str, body: VideoModelIn):
        return models.save(model, body.expected_revision, body.platform, body.model_id)

    @app.post('/settings/video-models/{model}/key/prompt')
    def prompt_video_key(model: str):
        return models.key(model)

    @app.post('/settings/video-models/{model}/key/delete')
    def delete_video_key(model: str):
        return models.key(model, delete=True)

    @app.get('/episodes/{episode_id}/animation')
    def animation_state(episode_id: str):
        return animation.state(episode_id)

    @app.post('/episodes/{episode_id}/animation/propose')
    def animation_propose(episode_id: str):
        return animation.propose(episode_id)

    @app.post('/episodes/{episode_id}/animation/approve-confident')
    def animation_approve_confident(episode_id: str):
        return animation.approve_confident(episode_id)

    @app.post('/episodes/{episode_id}/animation/kimodo/export')
    def kimodo_export(episode_id: str):
        return animation.export_requests(episode_id)

    @app.post('/episodes/{episode_id}/animation/slots/{slot_id}/assign')
    def animation_assign(episode_id: str, slot_id: str, body: AssignIn):
        return animation.assign(episode_id, slot_id, body.expected_revision, body.clips, body.upper_body_path)

    @app.post('/episodes/{episode_id}/animation/slots/{slot_id}/approve')
    def animation_approve(episode_id: str, slot_id: str, body: RevisionIn):
        return animation.approve(episode_id, slot_id, body.expected_revision)

    @app.post('/episodes/{episode_id}/animation/slots/{slot_id}/kimodo')
    def animation_to_kimodo(episode_id: str, slot_id: str, body: RevisionIn):
        return animation.to_kimodo(episode_id, slot_id, body.expected_revision)

    @app.post('/kimodo/requests/{request_id}/prompt')
    def kimodo_prompt(request_id: str, body: KimodoPromptIn):
        return animation.save_request(request_id, body.expected_revision, body.prompt)

    @app.post('/kimodo/requests/{request_id}/variants')
    def kimodo_variants(request_id: str, body: KimodoVariantsIn):
        return animation.attach_variants(request_id, body.expected_revision, body.asset_paths)

    @app.post('/kimodo/requests/{request_id}/approve')
    def kimodo_approve(request_id: str, body: KimodoApproveIn):
        return animation.approve_variant(request_id, body.expected_revision, body.asset_path, body.tags)

    @app.get('/episodes/{episode_id}/ue-branch')
    def ue_state(episode_id: str):
        return ue.state(episode_id)

    @app.post('/episodes/{episode_id}/ue-branch/assemble')
    def ue_assemble(episode_id: str, body: ShotsIn):
        return ue.enqueue_assemble(episode_id, body.shot_ids)

    @app.post('/episodes/{episode_id}/ue-branch/animate')
    def ue_animate(episode_id: str, body: ShotsIn):
        return ue.enqueue_animate(episode_id, body.shot_ids)

    @app.post('/ue-branch/jobs/{job_id}/release')
    def ue_release(job_id: str, body: WorkerStoppedIn):
        return ue.release_interrupted(job_id, body.worker_stopped)

    @app.post('/episodes/{episode_id}/ue-branch/shots/{shot_id}/c5')
    def ue_c5(episode_id: str, shot_id: str, body: C5In):
        return ue.approve_c5(episode_id, shot_id, body.expected_hash, body.checks, body.notes)

    @app.post('/episodes/{episode_id}/ue-branch/shots/{shot_id}/c5/unlock')
    def ue_c5_unlock(episode_id: str, shot_id: str):
        return ue.unlock_c5(episode_id, shot_id)

    @app.get('/episodes/{episode_id}/ai-video')
    def ai_state(episode_id: str):
        return ai.state(episode_id)

    @app.post('/episodes/{episode_id}/ai-video/shots/{shot_id}/input')
    def ai_input(episode_id: str, shot_id: str, body: AiInputIn):
        return ai.save_input(episode_id, shot_id, body.expected_revision, body.prompt_override, body.reference_keys)

    @app.post('/episodes/{episode_id}/ai-video/shots/{shot_id}/control/browse')
    def ai_control(episode_id: str, shot_id: str):
        from .local_picker import pick_file
        path = pick_file('control')
        if not path:
            raise DomainError('Đã hủy chọn file.', 409)
        return ai.register_control(episode_id, shot_id, path)

    @app.post('/episodes/{episode_id}/ai-video/shots/{shot_id}/draft')
    def ai_draft(episode_id: str, shot_id: str, body: DraftIn):
        return ai.enqueue_draft(episode_id, shot_id, body.client_request_id)

    @app.post('/episodes/{episode_id}/ai-video/shots/{shot_id}/c6')
    def ai_lock(episode_id: str, shot_id: str, body: LockDraftIn):
        return ai.lock_draft(episode_id, shot_id, body.take_id)

    @app.post('/episodes/{episode_id}/ai-video/shots/{shot_id}/c6/unlock')
    def ai_unlock(episode_id: str, shot_id: str):
        return ai.unlock_draft(episode_id, shot_id)

    @app.post('/ai-video/takes/{take_id}/review')
    def ai_review(take_id: str, body: TakeReviewIn):
        return ai.review_take(take_id, body.decision, body.checks)

    @app.get('/ai-video/takes/{take_id}/file')
    def ai_take_file(take_id: str):
        return FileResponse(ai.take_file(take_id), media_type='video/mp4', headers={'Cache-Control': 'no-store'})

    @app.get('/ai-video/controls/{control_id}/file')
    def ai_control_file(control_id: str):
        path = ai.control_file(control_id)
        kind = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.mov': 'video/quicktime'}.get(path.suffix.lower(), 'video/mp4')
        return FileResponse(path, media_type=kind, headers={'Cache-Control': 'no-store'})

    @app.post('/ai-video/jobs/{job_id}/stop-waiting')
    def ai_stop_waiting(job_id: str):
        return ai.stop_waiting(job_id)

    @app.get('/episodes/{episode_id}/proxy')
    def proxy_state(episode_id: str):
        return proxy.state(episode_id)

    @app.post('/episodes/{episode_id}/proxy/shots/{shot_id}/cache')
    def proxy_cache(episode_id: str, shot_id: str, body: CacheIn):
        return proxy.declare_cache(episode_id, shot_id, body.expected_build_hash, body.declarations)

    @app.post('/episodes/{episode_id}/proxy/render')
    def proxy_render(episode_id: str, body: ShotsIn):
        return proxy.enqueue_render(episode_id, body.shot_ids)

    @app.post('/proxy/jobs/{job_id}/release')
    def proxy_release(job_id: str, body: WorkerStoppedIn):
        return proxy.release_interrupted(job_id, body.worker_stopped)

    @app.post('/episodes/{episode_id}/proxy/shots/{shot_id}/import/browse')
    def proxy_import(episode_id: str, shot_id: str):
        from .local_picker import pick_file
        path = pick_file('video')
        if not path:
            raise DomainError('Đã hủy chọn file.', 409)
        return proxy.import_proxy(episode_id, shot_id, path)

    @app.get('/proxy/files/{proxy_id}')
    def proxy_file(proxy_id: str):
        return FileResponse(proxy.proxy_file(proxy_id), media_type='video/mp4', headers={'Cache-Control': 'no-store'})

    @app.post('/episodes/{episode_id}/proxy/shots/{shot_id}/c6')
    def proxy_c6(episode_id: str, shot_id: str, body: ExpectedHashIn):
        return proxy.approve_c6(episode_id, shot_id, body.expected_hash)

    @app.post('/episodes/{episode_id}/proxy/shots/{shot_id}/c6/unlock')
    def proxy_c6_unlock(episode_id: str, shot_id: str):
        return proxy.unlock_c6(episode_id, shot_id)

    @app.get('/episodes/{episode_id}/rough-cut')
    def rough_state(episode_id: str):
        return rough.state(episode_id)

    @app.post('/episodes/{episode_id}/rough-cut/build')
    def rough_build(episode_id: str, body: SceneScopeIn):
        return rough.enqueue(episode_id, body.scene_key)

    @app.get('/rough-cut/files/{rough_cut_id}')
    def rough_file(rough_cut_id: str):
        return FileResponse(rough.rough_cut_file(rough_cut_id), media_type='video/mp4', headers={'Cache-Control': 'no-store'})

    @app.post('/episodes/{episode_id}/rough-cut/scenes/{scene_key}/lock')
    def rough_lock(episode_id: str, scene_key: str, body: SceneLockIn):
        return rough.lock_scene(episode_id, scene_key, body.expected_hash, body.checks)

    @app.post('/episodes/{episode_id}/rough-cut/scenes/{scene_key}/unlock')
    def rough_unlock(episode_id: str, scene_key: str):
        return rough.unlock_scene(episode_id, scene_key)

    @app.post('/episodes/{episode_id}/c7/approve')
    def approve_c7(episode_id: str, body: ExpectedHashIn):
        return rough.approve_c7(episode_id, body.expected_hash)

    @app.get('/episodes/{episode_id}/rough-cut/segments/{shot_id}')
    def rough_segment(episode_id: str, shot_id: str):
        return FileResponse(rough.segment_file(episode_id, shot_id), media_type='video/mp4', headers={'Cache-Control': 'no-store'})

    @app.get('/episodes/{episode_id}/finals')
    def final_state(episode_id: str):
        return final.state(episode_id)

    @app.post('/episodes/{episode_id}/finals/ai')
    def final_ai(episode_id: str, body: FinalBatchIn):
        return final.enqueue_ai(episode_id, body.shot_ids, body.client_request_id)

    @app.post('/episodes/{episode_id}/finals/ue')
    def final_ue(episode_id: str, body: ShotsIn):
        return final.enqueue_ue(episode_id, body.shot_ids)

    @app.post('/finals/jobs/{job_id}/stop-waiting')
    def final_stop_waiting(job_id: str):
        return final.stop_waiting(job_id)

    @app.post('/finals/jobs/{job_id}/release')
    def final_release(job_id: str, body: WorkerStoppedIn):
        return final.release_interrupted(job_id, body.worker_stopped)

    @app.post('/episodes/{episode_id}/finals/shots/{shot_id}/import/browse')
    def final_import(episode_id: str, shot_id: str):
        from .local_picker import pick_file
        path = pick_file('video')
        if not path:
            raise DomainError('Đã hủy chọn file.', 409)
        return final.import_final(episode_id, shot_id, path)

    @app.post('/episodes/{episode_id}/finals/shots/{shot_id}/conform')
    def final_reconform(episode_id: str, shot_id: str):
        return final.reconform(episode_id, shot_id)

    @app.get('/finals/review/{conform_id}')
    def final_review(conform_id: str):
        return FileResponse(final.review_file(conform_id), media_type='video/mp4', headers={'Cache-Control': 'no-store'})

    @app.post('/episodes/{episode_id}/finals/shots/{shot_id}/c8')
    def final_c8(episode_id: str, shot_id: str, body: SceneLockIn):
        return final.approve_c8(episode_id, shot_id, body.expected_hash, body.checks)

    @app.post('/episodes/{episode_id}/finals/shots/{shot_id}/c8/revoke')
    def final_c8_revoke(episode_id: str, shot_id: str):
        return final.revoke_c8(episode_id, shot_id)
