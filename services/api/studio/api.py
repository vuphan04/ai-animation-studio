from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .domain import DomainError
from .service import Studio
from .p3_service import PerformanceService
from .p4_service import CacheRenderService
from .p5_service import PlanningService
from .ai_settings import AISettings
from .p6_video_service import VideoService
from .p6_sound_service import SoundDeliveryService
from .media_settings import MediaSettings
from .p7_service import AcceptanceService
from .p7_api import install as install_acceptance_api


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectIn(Strict):
    name: str


class EpisodeIn(Strict):
    name: str
    ordinal: int


class AssetIn(Strict):
    registry_id: str
    kind: str
    metadata: dict = Field(default_factory=dict)
    relative_path: str | None = None
    checksum: str | None = None


class MotionQueryIn(Strict):
    skeleton_id: str
    tags: list[str]
    paired: bool = False


class ShotIn(Strict):
    episode_id: str
    ordinal: int
    manifest: dict


class RevisionIn(Strict):
    expected_revision: int
    manifest: dict


class ApprovalIn(Strict):
    exact_revision: int
    expected_current_revision: int
    route: str


class StateIn(Strict):
    expected_state_revision: int
    mode: str
    lock_state: str
    adopt_fingerprint: str | None = None


class ObserveIn(Strict):
    expected_state_revision: int
    fingerprint: str


class SubsystemApprovalIn(Strict):
    exact_shot_revision: int
    expected_state_revision: int


class BuildIn(Strict):
    revision: int
    subsystem: str
    profile_version: str = "P1_FAKE_V1"
    depends_on: list[str] = Field(default_factory=list)
    resource: str = "CPU"


class UnrealEpisodeIn(Strict):
    shot_codes: list[str] | None = None


class UnrealReconcileIn(Strict):
    worker_stopped: bool = False


class UnrealSettingsIn(Strict):
    editor_path: str
    project_path: str


class SaveUnrealSettingsIn(UnrealSettingsIn):
    expected_revision: int


class BrowseUnrealIn(Strict):
    kind: str


class SaveAISettingsIn(Strict):
    provider_kind: str
    model: str
    max_output_tokens: int
    expected_revision: int


class VoiceJobIn(Strict):
    performance_id: str
    client_request_id: str


class VoiceReviewIn(Strict):
    expected_version: int
    decision: str


class VoiceLockIn(Strict):
    expected_version: int
    expected_selection_revision: int | None = None


class TimingCreateIn(Strict):
    expected_shot_revision: int
    expected_input_hash: str


class TimingApproveIn(Strict):
    expected_version: int


class CacheSourceIn(Strict):
    subsystem: str
    expected_shot_revision: int
    source: dict


class CacheFileIn(CacheSourceIn):
    range_in: int
    range_out_exclusive: int
    relative_path: str
    expected_sha256: str


class CacheReviewIn(Strict):
    expected_version: int


class RenderInventoryIn(Strict):
    kind: str
    expected_shot_revision: int
    relative_folder: str
    color_profile: str


class BrowseOutputIn(Strict):
    kind: str


class ScreenplayIn(Strict):
    text: str
    language: str
    target_language: str
    estimated_seconds: int


class PlannerRunIn(Strict):
    screenplay_id: str
    client_request_id: str
    fps: dict
    grant_id: str | None = None
    base_draft_id: str | None = None


class PlanningGrantIn(Strict):
    provider_kind: str
    max_cost_microusd: int
    expires_at: str


class PlanningApplyIn(Strict):
    shot_code: str
    expected_target_revision: int
    preview_token: str


class VideoGrantIn(Strict):
    currency: str
    max_micro: int
    expires_at: str


class VideoReferencesIn(Strict):
    expected_shot_revision: int
    references: dict


class VideoRunIn(Strict):
    stage: str
    grant_id: str
    client_request_id: str


class VideoReviewIn(Strict):
    expected_version: int
    decision: str
    checks: dict


class VideoSelectIn(Strict):
    expected_version: int


class PictureLockIn(Strict):
    expected_shot_revision: int
    expected_take_id: str
    edit_in: int
    edit_out_exclusive: int


class FoleyRunIn(Strict):
    client_request_id: str


class StemIn(Strict):
    picture_lock_id: str
    role: str
    relative_path: str
    sha256: str
    offset_frame: int


class StemReviewIn(Strict):
    expected_version: int


class MediaRouteIn(Strict):
    expected_shot_revision: int
    route: str
    fallback_take_id: str | None = None


class UEVideoIn(Strict):
    expected_shot_revision: int
    relative_path: str
    sha256: str
    duration_frames: int
    fps: dict


class AssetReferenceIn(Strict):
    asset_version_id: str
    relative_path: str
    sha256: str


class SilentDialogueIn(Strict):
    picture_lock_id: str


class DeliveryProfileIn(Strict):
    expected_revision: int
    width: int
    height: int
    color_profile: str
    audio_sample_rate: int
    timecode_start: str


class MediaSettingsIn(Strict):
    expected_revision: int
    platform: str
    model_id: str


def create_app(data_root: Path | None = None, *, testing: bool = False, worker: bool = True) -> FastAPI:
    build_id = json.loads((Path(__file__).resolve().parents[3] / "resources" / "app-build.json").read_text(encoding="utf-8"))["build_id"]
    root = data_root or Path(os.environ.get("STUDIO_DATA_ROOT", Path(__file__).resolve().parents[3] / "data"))
    studio = Studio(Path(root))
    performance = PerformanceService(studio)
    cache_render = CacheRenderService(studio)
    planning = PlanningService(studio)
    ai_settings = AISettings(studio)
    video = VideoService(studio, cache_render=cache_render)
    sound = SoundDeliveryService(studio, video, cache_render)
    acceptance = AcceptanceService(studio)
    sessions: set[str] = set()
    stop = threading.Event()

    def work_loop():
        while not stop.is_set():
            try:
                studio.process_next()
            except Exception:
                # Job errors are stored by the service. This guard keeps the worker alive.
                pass
            stop.wait(0.25)

    def unreal_loop():
        while not stop.is_set():
            try:
                studio.process_next_unreal_episode()
            except Exception:
                pass
            stop.wait(0.5)

    def voice_loop():
        while not stop.is_set():
            try:
                performance.process_next()
            except Exception:
                pass
            stop.wait(0.5)

    def planning_loop():
        while not stop.is_set():
            try:
                planning.process_next()
            except Exception:
                pass
            stop.wait(0.5)

    def video_loop():
        while not stop.is_set():
            try:
                video.process_next()
                video.poll_next()
                sound.process_next()
                sound.process_delivery()
            except Exception:
                pass
            stop.wait(0.5)

    def recovery_loop():
        while not stop.is_set():
            try:
                acceptance.process_recovery()
            except Exception:
                pass
            stop.wait(0.5)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        threads = [threading.Thread(target=work_loop, name="studio-local-worker", daemon=True)] if worker else []
        if worker:
            threads.append(threading.Thread(target=unreal_loop, name="studio-unreal-worker", daemon=True))
            threads.append(threading.Thread(target=voice_loop, name="studio-voice-worker", daemon=True))
            threads.append(threading.Thread(target=planning_loop, name="studio-planning-worker", daemon=True))
            threads.append(threading.Thread(target=video_loop, name="studio-media-worker", daemon=True))
            threads.append(threading.Thread(target=recovery_loop, name='studio-recovery-worker', daemon=True))
        for thread in threads:
            thread.start()
        yield
        stop.set()
        for thread in threads:
            thread.join(timeout=2)

    app = FastAPI(title="AI Animation Studio", version="0.2.0", lifespan=lifespan)
    app.state.studio = studio
    app.state.performance = performance
    app.state.cache_render = cache_render
    app.state.planning = planning
    app.state.video = video
    app.state.sound = sound
    app.state.acceptance = acceptance
    install_acceptance_api(app, acceptance)

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, exc: DomainError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(_request: Request, exc: sqlite3.IntegrityError):
        return JSONResponse({"detail": "database uniqueness or relationship conflict"}, status_code=409)

    @app.middleware("http")
    async def local_guard(request: Request, call_next):
        host = request.url.hostname
        client = request.client.host if request.client else None
        origin = request.headers.get("origin")
        allowed_origins = {"http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000", "http://localhost:8000"}
        if host not in ({"127.0.0.1", "localhost", "testserver"} if testing else {"127.0.0.1", "localhost"}) or client not in ({"127.0.0.1", "::1", "localhost", "testclient"} if testing else {"127.0.0.1", "::1", "localhost"}):
            return JSONResponse({"detail": "loopback only"}, status_code=403)
        if origin and origin not in allowed_origins:
            return JSONResponse({"detail": "origin denied"}, status_code=403)
        if request.method == "OPTIONS":
            response = JSONResponse({}, status_code=204)
        route_path = request.url.path
        root_path = request.scope.get("root_path", "")
        if root_path and route_path.startswith(root_path):
            route_path = route_path[len(root_path):] or "/"
        if request.method != "OPTIONS" and route_path not in ("/health", "/session", "/openapi.json", "/docs", "/redoc"):
            token = request.cookies.get("studio_session")
            if not token or token not in sessions:
                return JSONResponse({"detail": "local session required"}, status_code=401)
            if request.method not in ("GET", "HEAD") and request.headers.get("x-session-token") != token:
                return JSONResponse({"detail": "session token required for mutation"}, status_code=403)
            if request.method not in ('GET', 'HEAD'):
                with studio.db.connect() as conn:
                    snapshot_active = conn.execute("SELECT 1 FROM recovery_jobs WHERE kind='BACKUP' AND state='RUNNING' LIMIT 1").fetchone()
                if snapshot_active:
                    return JSONResponse({'detail': 'Đang sao lưu snapshot; chờ hoàn tất rồi thực hiện thay đổi.'}, status_code=409)
            response = await call_next(request)
        elif request.method != "OPTIONS":
            response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type,X-Session-Token"
            response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "adapter_kind": studio.adapter.kind,
                "unreal_configured": studio.unreal_settings()["configured"], "build_id": build_id}

    @app.get("/session")
    def session():
        token = secrets.token_urlsafe(32)
        sessions.add(token)
        response = JSONResponse({"session_token": token, "adapter_kind": studio.adapter.kind,
                                 "unreal_configured": studio.unreal_settings()["configured"], "build_id": build_id})
        response.set_cookie("studio_session", token, httponly=True, secure=False, samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/settings/unreal")
    def unreal_settings():
        return studio.unreal_settings()

    @app.get("/settings/ai")
    def get_ai_settings():
        return ai_settings.get()

    @app.post("/settings/ai")
    def save_ai_settings(body: SaveAISettingsIn):
        return ai_settings.save(body.provider_kind, body.model, body.max_output_tokens, body.expected_revision)

    @app.post("/settings/ai/key/prompt")
    def prompt_ai_key():
        try:
            return ai_settings.prompt_key()
        except RuntimeError as exc:
            raise DomainError(str(exc), 409) from exc

    @app.post("/settings/ai/key/delete")
    def delete_ai_key():
        return ai_settings.delete_key()

    @app.post("/settings/ai/check")
    def check_ai_connection():
        return planning.openai_provider.check_connection(ai_settings.get()["model"])

    @app.post("/settings/unreal/validate")
    def validate_unreal_settings(body: UnrealSettingsIn):
        return studio.validate_unreal_settings(body.editor_path, body.project_path)

    @app.post("/settings/unreal")
    def save_unreal_settings(body: SaveUnrealSettingsIn):
        return studio.save_unreal_settings(body.editor_path, body.project_path, body.expected_revision)

    @app.post("/settings/unreal/browse")
    def browse_unreal_file(body: BrowseUnrealIn):
        from .local_picker import pick_file
        current = studio.unreal_settings()
        initial_path = current["editor_path"] if body.kind == "editor" else current["project_path"]
        return {"path": pick_file(body.kind, initial_path)}

    @app.get("/projects")
    def projects():
        return studio.projects()

    @app.post("/projects")
    def create_project(body: ProjectIn):
        return studio.create_project(body.name)

    @app.get("/projects/{project_id}")
    def project(project_id: str):
        return studio.project(project_id)

    @app.get("/projects/{project_id}/episodes")
    def episodes(project_id: str):
        return studio.episodes(project_id)

    @app.post("/projects/{project_id}/episodes")
    def create_episode(project_id: str, body: EpisodeIn):
        return studio.create_episode(project_id, body.name, body.ordinal)

    @app.get("/projects/{project_id}/registry")
    def registry(project_id: str):
        return studio.registry(project_id)

    @app.post("/projects/{project_id}/registry")
    def add_asset(project_id: str, body: AssetIn):
        return studio.add_asset(project_id, body.registry_id, body.kind, body.metadata, body.relative_path, body.checksum)

    @app.post("/projects/{project_id}/motion/search")
    def search_motion(project_id: str, body: MotionQueryIn):
        return studio.search_motion(project_id, body.skeleton_id, body.tags, body.paired)

    @app.get("/projects/{project_id}/shots")
    def shots(project_id: str):
        return studio.shots(project_id)

    @app.post("/projects/{project_id}/shots")
    def create_shot(project_id: str, body: ShotIn):
        return studio.create_shot(project_id, body.episode_id, body.ordinal, body.manifest)

    @app.get("/shots/{shot_id}")
    def shot(shot_id: str):
        return studio.shot(shot_id)

    @app.post("/shots/{shot_id}/revisions/preview")
    def preview_revision(shot_id: str, body: RevisionIn):
        return studio.preview_revision(shot_id, body.expected_revision, body.manifest)

    @app.post("/shots/{shot_id}/revisions")
    def revise_shot(shot_id: str, body: RevisionIn):
        return studio.revise_shot(shot_id, body.expected_revision, body.manifest)

    @app.post("/shots/{shot_id}/approve")
    def approve(shot_id: str, body: ApprovalIn):
        return studio.approve(shot_id, body.exact_revision, body.expected_current_revision, body.route)

    @app.post("/shots/{shot_id}/subsystems/{subsystem}")
    def set_state(shot_id: str, subsystem: str, body: StateIn):
        return studio.set_state(shot_id, subsystem, body.expected_state_revision, body.mode, body.lock_state, body.adopt_fingerprint)

    @app.post("/shots/{shot_id}/subsystems/{subsystem}/observe")
    def observe_external(shot_id: str, subsystem: str, body: ObserveIn):
        return studio.observe_external(shot_id, subsystem, body.expected_state_revision, body.fingerprint)

    @app.post("/shots/{shot_id}/subsystems/{subsystem}/approve")
    def approve_subsystem(shot_id: str, subsystem: str, body: SubsystemApprovalIn):
        return studio.approve_subsystem(shot_id, subsystem, body.exact_shot_revision, body.expected_state_revision)

    @app.post("/shots/{shot_id}/preflight")
    def preflight(shot_id: str, body: BuildIn):
        return studio.preflight(shot_id, body.revision, body.subsystem, body.depends_on)

    @app.post("/shots/{shot_id}/build")
    def build(shot_id: str, body: BuildIn):
        return studio.enqueue(shot_id, body.revision, body.subsystem, body.profile_version, body.depends_on, body.resource)

    @app.get("/projects/{project_id}/jobs")
    def jobs(project_id: str):
        return studio.jobs(project_id)

    @app.post("/projects/{project_id}/episodes/{episode_id}/unreal/preview")
    def unreal_preview(project_id: str, episode_id: str, body: UnrealEpisodeIn):
        return studio.preview_unreal_episode(project_id, episode_id, body.shot_codes)

    @app.post("/projects/{project_id}/episodes/{episode_id}/unreal/preflight")
    def unreal_preflight(project_id: str, episode_id: str, body: UnrealEpisodeIn):
        return studio.preflight_unreal_episode(project_id, episode_id, body.shot_codes)

    @app.post("/projects/{project_id}/episodes/{episode_id}/unreal/build")
    def unreal_build(project_id: str, episode_id: str, body: UnrealEpisodeIn):
        return studio.enqueue_unreal_episode(project_id, episode_id, body.shot_codes)

    @app.get("/projects/{project_id}/unreal/jobs")
    def unreal_jobs(project_id: str):
        return studio.unreal_jobs(project_id)

    @app.post("/shots/{shot_id}/voice/jobs")
    def enqueue_voice(shot_id: str, body: VoiceJobIn):
        return performance.enqueue_voice(shot_id, body.performance_id, body.client_request_id)

    @app.get("/projects/{project_id}/voice/jobs")
    def voice_jobs(project_id: str):
        return performance.jobs(project_id)

    @app.get("/voice/jobs/{job_id}")
    def voice_job(job_id: str):
        return performance.job(job_id)

    @app.post("/voice/jobs/{job_id}/reconcile")
    def reconcile_voice(job_id: str):
        return performance.reconcile(job_id)

    @app.post("/voice/jobs/{job_id}/retry")
    def retry_voice(job_id: str):
        return performance.retry(job_id)

    @app.get("/shots/{shot_id}/voice/takes")
    def voice_takes(shot_id: str):
        return performance.takes(shot_id)

    @app.get("/voice/takes/{take_id}/wav")
    def voice_wav(take_id: str):
        return FileResponse(performance.take_wav(take_id), media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.post("/voice/takes/{take_id}/review")
    def review_voice(take_id: str, body: VoiceReviewIn):
        return performance.review_take(take_id, body.expected_version, body.decision)

    @app.post("/voice/takes/{take_id}/lock")
    def lock_voice(take_id: str, body: VoiceLockIn):
        return performance.lock_take(take_id, body.expected_version, body.expected_selection_revision)

    @app.get("/voice/takes/{take_id}/lock/preview")
    def preview_voice_lock(take_id: str):
        return performance.preview_lock(take_id)

    @app.get("/shots/{shot_id}/timing/preview")
    def preview_timing(shot_id: str):
        return performance.preview_timing(shot_id)

    @app.post("/shots/{shot_id}/timing")
    def create_timing(shot_id: str, body: TimingCreateIn):
        return performance.create_timing(shot_id, body.expected_shot_revision, body.expected_input_hash)

    @app.get("/shots/{shot_id}/timing")
    def timings(shot_id: str):
        return performance.timings(shot_id)

    @app.post("/timing/{timing_id}/approve")
    def approve_timing(timing_id: str, body: TimingApproveIn):
        return performance.approve_timing(timing_id, body.expected_version)

    @app.get("/shots/{shot_id}/facial/plan")
    def facial_plan(shot_id: str):
        return performance.facial_plan(shot_id)

    @app.post("/shots/{shot_id}/facial/preflight")
    def facial_preflight(shot_id: str):
        return performance.facial_preflight(shot_id)

    @app.get("/shots/{shot_id}/camera/plan")
    def camera_plan(shot_id: str):
        return performance.camera_plan(shot_id)

    @app.post("/shots/{shot_id}/camera/preflight")
    def camera_preflight(shot_id: str):
        return performance.camera_preflight(shot_id)

    @app.get("/shots/{shot_id}/simulation")
    def simulation_status(shot_id: str):
        return cache_render.cache_status(shot_id)

    @app.get("/shots/{shot_id}/simulation/hints")
    def simulation_hints(shot_id: str):
        return cache_render.source_hints(shot_id)

    @app.post("/projects/{project_id}/outputs/browse")
    def browse_project_output(project_id: str, body: BrowseOutputIn):
        from .local_picker import pick_project_output
        from .p4_domain import file_sha256
        root = Path(studio.project(project_id)["root"]).resolve()
        relative = pick_project_output(root, body.kind)
        return {"relative_path": relative, "sha256": file_sha256(root / relative) if relative and body.kind != "render_folder" else None}

    @app.post("/shots/{shot_id}/simulation/source")
    def simulation_source(shot_id: str, body: CacheSourceIn):
        return cache_render.configure_source(shot_id, body.subsystem, body.expected_shot_revision, body.source)

    @app.post("/shots/{shot_id}/simulation/source/preview")
    def simulation_source_preview(shot_id: str, body: CacheSourceIn):
        return cache_render.preview_source(shot_id, body.subsystem, body.expected_shot_revision, body.source)

    @app.post("/shots/{shot_id}/simulation/cache")
    def simulation_cache(shot_id: str, body: CacheFileIn):
        return cache_render.register_file(shot_id, body.subsystem, body.expected_shot_revision,
                                          body.source, body.range_in, body.range_out_exclusive,
                                          body.relative_path, body.expected_sha256)

    @app.get("/shots/{shot_id}/simulation/caches")
    def simulation_caches(shot_id: str):
        return cache_render.caches(shot_id)

    @app.post("/simulation/caches/{cache_id}/review")
    def simulation_review(cache_id: str, body: CacheReviewIn):
        return cache_render.review_cache(cache_id, body.expected_version)

    @app.get("/shots/{shot_id}/render/final/preflight")
    def final_render_preflight(shot_id: str):
        return cache_render.final_preflight(shot_id)

    @app.post("/shots/{shot_id}/render/final")
    def final_render_request(shot_id: str):
        return cache_render.request_final(shot_id)

    @app.post("/shots/{shot_id}/render/inventory")
    def render_inventory(shot_id: str, body: RenderInventoryIn):
        return cache_render.inventory_render(shot_id, body.kind, body.expected_shot_revision,
                                             body.relative_folder, body.color_profile)

    @app.get("/shots/{shot_id}/render/inventories")
    def render_inventories(shot_id: str):
        return cache_render.render_inventories(shot_id)

    @app.get("/shots/{shot_id}/picture-lock/preflight")
    def picture_lock_preflight(shot_id: str):
        return cache_render.picture_lock_preflight(shot_id)

    @app.get("/resources/gpu-heavy")
    def gpu_heavy_status():
        return {"lease": cache_render.heavy_lease()}

    @app.get("/media/video/capability")
    def video_capability():
        return video.capability()

    @app.get("/settings/media")
    def media_settings():
        return MediaSettings(studio).get()

    @app.post("/settings/media")
    def save_media_settings(body: MediaSettingsIn):
        return MediaSettings(studio).save(body.expected_revision, body.platform, body.model_id)

    @app.post("/settings/media/key/prompt")
    def prompt_media_key():
        return MediaSettings(studio).key()

    @app.post("/settings/media/key/delete")
    def delete_media_key():
        return MediaSettings(studio).key(delete=True)

    @app.post("/shots/{shot_id}/media/route")
    def media_route(shot_id: str, body: MediaRouteIn):
        return video.approve_route(shot_id, body.expected_shot_revision, body.route, body.fallback_take_id)

    @app.get("/shots/{shot_id}/media/route")
    def media_routes(shot_id: str):
        return video.route_decisions(shot_id)

    @app.post("/shots/{shot_id}/media/ue-video")
    def import_ue_video(shot_id: str, body: UEVideoIn):
        return video.import_ue_video(shot_id, body.expected_shot_revision, body.relative_path, body.sha256, body.duration_frames, body.fps)

    @app.post("/projects/{project_id}/media/asset-references")
    def register_asset_reference(project_id: str, body: AssetReferenceIn):
        return video.register_asset_reference(project_id, body.asset_version_id, body.relative_path, body.sha256)

    @app.get("/projects/{project_id}/media/asset-references")
    def asset_references(project_id: str):
        return video.asset_references(project_id)

    @app.post("/projects/{project_id}/media/video/grants")
    def create_video_grant(project_id: str, body: VideoGrantIn):
        return video.create_grant(project_id, body.currency, body.max_micro, body.expires_at)

    @app.get("/projects/{project_id}/media/video/grants")
    def video_grants(project_id: str):
        return video.grants(project_id)

    @app.post("/shots/{shot_id}/media/references")
    def create_video_references(shot_id: str, body: VideoReferencesIn):
        return video.create_reference_package(shot_id, body.expected_shot_revision, body.references)

    @app.get("/shots/{shot_id}/media/references")
    def video_references(shot_id: str):
        return video.references(shot_id)

    @app.get("/shots/{shot_id}/media/video/preflight")
    def video_preflight(shot_id: str, stage: str = "DRAFT", grant_id: str | None = None):
        return video.preflight(shot_id, stage, grant_id)

    @app.post("/shots/{shot_id}/media/video/jobs")
    def enqueue_video(shot_id: str, body: VideoRunIn):
        return video.enqueue(shot_id, body.stage, body.client_request_id, body.grant_id)

    @app.get("/projects/{project_id}/media/video/jobs")
    def video_jobs(project_id: str):
        return video.jobs(project_id)

    @app.get("/media/video/jobs/{job_id}")
    def video_job(job_id: str):
        return video.job(job_id)

    @app.post("/media/video/jobs/{job_id}/reconcile")
    def reconcile_video(job_id: str):
        return video.reconcile(job_id)

    @app.get("/shots/{shot_id}/media/video/takes")
    def video_takes(shot_id: str):
        return video.takes(shot_id)

    @app.get("/media/video/takes/{take_id}/file")
    def video_file(take_id: str):
        with studio.db.connect() as conn:
            row = conn.execute("SELECT * FROM video_takes WHERE id=?", (take_id,)).fetchone()
            if not row:
                raise DomainError("Không tìm thấy video take.", 404)
        _, path = video._checked_file(row["project_id"], row["relative_path"], row["sha256"])
        return FileResponse(path, media_type="video/mp4" if path.suffix.lower() == ".mp4" else "video/quicktime", headers={"Cache-Control": "no-store"})

    @app.post("/media/video/takes/{take_id}/review")
    def review_video_take(take_id: str, body: VideoReviewIn):
        return video.review_take(take_id, body.expected_version, body.decision, body.checks)

    @app.post("/media/video/takes/{take_id}/select")
    def select_video_take(take_id: str, body: VideoSelectIn):
        return video.select_take(take_id, body.expected_version)

    @app.get("/shots/{shot_id}/media/picture/preflight")
    def media_picture_preflight(shot_id: str):
        return video.picture_preflight(shot_id)

    @app.post("/shots/{shot_id}/media/picture/lock")
    def media_picture_lock(shot_id: str, body: PictureLockIn):
        return video.lock_picture(shot_id, body.expected_shot_revision, body.expected_take_id,
                                  body.edit_in, body.edit_out_exclusive)

    @app.get("/shots/{shot_id}/media/picture/locks")
    def media_picture_locks(shot_id: str):
        return video.picture_locks(shot_id)

    @app.get("/shots/{shot_id}/media/foley/preflight")
    def foley_preflight(shot_id: str):
        return sound.foley_preflight(shot_id)

    @app.post("/shots/{shot_id}/media/foley/jobs")
    def enqueue_foley(shot_id: str, body: FoleyRunIn):
        return sound.enqueue_foley(shot_id, body.client_request_id)

    @app.get("/projects/{project_id}/media/foley/jobs")
    def foley_jobs(project_id: str):
        return sound.foley_jobs(project_id)

    @app.post("/media/foley/jobs/{job_id}/reconcile")
    def reconcile_foley(job_id: str):
        return sound.reconcile_foley(job_id)

    @app.post("/shots/{shot_id}/media/dialogue/silence")
    def silent_dialogue(shot_id: str, body: SilentDialogueIn):
        return sound.create_silent_dialogue(shot_id, body.picture_lock_id)

    @app.get("/episodes/{episode_id}/media/delivery/profile")
    def delivery_profile(episode_id: str):
        return sound.delivery_profile(episode_id)

    @app.post("/episodes/{episode_id}/media/delivery/profile")
    def save_delivery_profile(episode_id: str, body: DeliveryProfileIn):
        return sound.save_delivery_profile(episode_id, body.expected_revision, body.width, body.height, body.color_profile, body.audio_sample_rate, body.timecode_start)

    @app.post("/shots/{shot_id}/media/stems")
    def register_stem(shot_id: str, body: StemIn):
        return sound.register_stem(shot_id, body.picture_lock_id, body.role, body.relative_path,
                                   body.sha256, body.offset_frame)

    @app.get("/shots/{shot_id}/media/stems")
    def sound_stems(shot_id: str):
        return sound.stems(shot_id)

    @app.get("/media/stems/{stem_id}/file")
    def stem_file(stem_id: str):
        with studio.db.connect() as conn:
            row = conn.execute("SELECT * FROM sound_stems WHERE id=?", (stem_id,)).fetchone()
            if not row:
                raise DomainError("Không tìm thấy stem.", 404)
        _, path = video._checked_file(row["project_id"], row["relative_path"], row["sha256"])
        return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.post("/media/stems/{stem_id}/review")
    def review_stem(stem_id: str, body: StemReviewIn):
        return sound.review_stem(stem_id, body.expected_version)

    @app.get("/episodes/{episode_id}/media/delivery/preflight")
    def delivery_preflight(episode_id: str):
        return sound.delivery_preflight(episode_id)

    @app.post("/episodes/{episode_id}/media/delivery/export")
    def export_delivery(episode_id: str):
        return sound.export_delivery(episode_id)

    @app.get("/episodes/{episode_id}/media/delivery/packages")
    def delivery_packages(episode_id: str):
        return sound.packages(episode_id)

    @app.post("/media/delivery/packages/{package_id}/reconcile")
    def reconcile_delivery(package_id: str):
        return sound.reconcile_delivery(package_id)

    @app.post("/projects/{project_id}/episodes/{episode_id}/screenplays")
    def ingest_screenplay(project_id: str, episode_id: str, body: ScreenplayIn):
        return planning.ingest(project_id, episode_id, body.text, body.language, body.target_language, body.estimated_seconds)

    @app.get("/episodes/{episode_id}/screenplays")
    def screenplays(episode_id: str):
        return planning.screenplays(episode_id)

    @app.post("/screenplays/{screenplay_id}/planning/jobs")
    def enqueue_planning(screenplay_id: str, body: PlannerRunIn):
        if body.screenplay_id != screenplay_id:
            raise DomainError("Screenplay ID trong URL và request không khớp.")
        return planning.enqueue(screenplay_id, body.client_request_id, fps=body.fps,
                                grant_id=body.grant_id, base_draft_id=body.base_draft_id)

    @app.get("/projects/{project_id}/planning/jobs")
    def planning_jobs(project_id: str):
        return planning.jobs(project_id)

    @app.get("/planning/jobs/{job_id}")
    def planning_job(job_id: str):
        return planning.job(job_id)

    @app.get("/episodes/{episode_id}/planning/drafts")
    def planning_drafts(episode_id: str):
        return planning.drafts(episode_id)

    @app.get("/planning/drafts/{draft_id}")
    def planning_draft(draft_id: str):
        return planning.draft(draft_id)

    @app.get("/planning/drafts/{draft_id}/shots/{shot_code}/preview")
    def planning_apply_preview(draft_id: str, shot_code: str):
        return planning.preview_apply(draft_id, shot_code)

    @app.post("/planning/drafts/{draft_id}/shots/{shot_code}/apply")
    def planning_apply(draft_id: str, shot_code: str, body: PlanningApplyIn):
        if body.shot_code != shot_code:
            raise DomainError("Shot code trong URL và request không khớp.")
        return planning.apply(draft_id, shot_code, body.expected_target_revision, body.preview_token)

    @app.post("/projects/{project_id}/planning/grants")
    def planning_grant(project_id: str, body: PlanningGrantIn):
        if body.provider_kind != "OPENAI":
            raise DomainError("Giao diện hiện chỉ cấp ngân sách cho OpenAI.")
        return planning.create_grant(project_id, body.provider_kind, body.max_cost_microusd, body.expires_at)

    @app.get("/projects/{project_id}/planning/grants")
    def planning_grants(project_id: str):
        return planning.grants(project_id)

    @app.get("/unreal/jobs/{job_id}")
    def unreal_job(job_id: str):
        return studio.unreal_job(job_id)

    @app.post("/unreal/jobs/{job_id}/reconcile")
    def unreal_reconcile(job_id: str, body: UnrealReconcileIn):
        return studio.reconcile_unreal_episode(job_id, body.worker_stopped)

    @app.post("/unreal/jobs/{job_id}/retry")
    def unreal_retry(job_id: str):
        return studio.retry_unreal_episode(job_id)

    @app.get("/jobs/{job_id}")
    def job(job_id: str):
        return studio.job(job_id)

    @app.post("/jobs/{job_id}/reconcile")
    def reconcile(job_id: str):
        return studio.reconcile(job_id)

    @app.post("/jobs/{job_id}/retry")
    def retry(job_id: str):
        return studio.retry(job_id)

    @app.get("/projects/{project_id}/artifacts")
    def artifacts(project_id: str):
        studio.project(project_id)
        with studio.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM artifact_versions WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    @app.get("/events")
    def events(after: int = 0, project_id: str | None = None):
        return studio.events(after, project_id)

    @app.get("/events/stream")
    async def event_stream(request: Request, after: int = 0, project_id: str | None = None):
        async def generate():
            import json

            cursor = after
            while not await request.is_disconnected():
                for event in studio.events(cursor, project_id):
                    cursor = event["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    return app


app = create_app()
