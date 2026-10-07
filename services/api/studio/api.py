"""The local HTTP API: one FastAPI app on loopback, its services and the background workers that run their queues."""

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
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from .ai_settings import AISettings
from .ai_video_service import AiVideoService
from .animation_service import AnimationService
from .breakdown_service import BreakdownService
from .claude_code import ClaudeCodeSettings
from .budget import BudgetService
from .delivery_service import DeliveryService
from .dialogue_service import DialogueService
from .domain import DomainError
from .episode_api import install as install_episode_api
from .final_service import FinalService
from .i18n import EnglishResponses
from .openai_client import check_connection
from .progress import WorkflowProgress
from .proxy_service import UeProxyService
from .recovery_service import RecoveryService
from .rough_cut_service import RoughCutService
from .route_service import RouteService
from .route_voice_api import install as install_route_voice_api
from .series_api import install as install_series_api
from .series_service import SeriesService
from .service import Studio
from .sound_service import SoundService
from .style_service import StyleService
from .timing_service import TimingService
from .tool_settings import ToolSettings
from .ue_branch_service import UeBranchService
from .voice_settings import VoiceSettings
from .zone_b_api import install as install_zone_b_api
from .zone_c_api import install as install_zone_c_api

APP_ID = "ai-animation-studio"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectIn(Strict):
    name: str


class EpisodeIn(Strict):
    name: str
    ordinal: int


class UnrealSettingsIn(Strict):
    editor_path: str
    project_path: str


class SaveUnrealSettingsIn(UnrealSettingsIn):
    expected_revision: int


class BrowseUnrealIn(Strict):
    kind: str


class SaveClaudeCodeIn(Strict):
    expected_revision: int
    executable_path: str
    model: str


class SaveAISettingsIn(Strict):
    provider_kind: str
    model: str
    max_output_tokens: int
    expected_revision: int


class RecoverySettingsIn(Strict):
    expected_revision: int
    backup_parent: str
    restore_parent: str


class RecoveryIn(Strict):
    kind: str
    backup_id: str | None = None
    ue_closed: bool = False


def create_app(data_root: Path | None = None, *, testing: bool = False, worker: bool = True) -> FastAPI:
    build_id = json.loads((Path(__file__).resolve().parents[3] / "resources" / "app-build.json").read_text(encoding="utf-8"))["build_id"]
    root = data_root or Path(os.environ.get("STUDIO_DATA_ROOT", Path(__file__).resolve().parents[3] / "data"))
    studio = Studio(Path(root))
    claude_code = ClaudeCodeSettings(studio)
    ai_settings = AISettings(studio, claude_code)
    recovery = RecoveryService(studio)
    series = SeriesService(studio, claude_code=claude_code)
    style = StyleService(studio, series)
    breakdown = BreakdownService(studio, series, style, claude_code=claude_code)
    routes = RouteService(studio, breakdown)
    dialogue = DialogueService(studio, breakdown, routes)
    timing = TimingService(studio, breakdown, routes, dialogue)
    animation = AnimationService(studio, breakdown, routes, timing)
    ue_branch = UeBranchService(studio, breakdown, animation)
    ai_video = AiVideoService(studio, breakdown, animation, ue_branch)
    ue_proxy = UeProxyService(studio, breakdown, ue_branch, ai_video)
    rough_cut = RoughCutService(studio, breakdown, animation, ue_proxy, ai_video, timing)
    finals = FinalService(studio, breakdown, ue_proxy, ai_video, rough_cut)
    episode_sound = SoundService(studio, breakdown, rough_cut, timing)
    delivery = DeliveryService(studio, breakdown, finals, episode_sound)
    progress = WorkflowProgress(studio, series, style, breakdown, timing, finals, episode_sound, delivery)
    sessions: set[str] = set()
    stop = threading.Event()

    def loop(pause: float, *steps):
        """One background worker: run each step in turn, forever. A step stores its own errors; the guard keeps the worker alive."""
        def run():
            while not stop.is_set():
                try:
                    for step in steps:
                        step()
                except Exception:
                    pass
                stop.wait(pause)
        return run

    # Work that needs the GPU shares one worker and one lease, so Unreal, renders and Foley never overlap.
    workers = {
        "studio-unreal-worker": loop(0.5, series.process_next_scan, style.process_next_capture, ue_branch.process_next, ue_proxy.process_next,
                                     finals.process_next_ue, episode_sound.process_next_foley),
        "studio-voice-worker": loop(0.5, dialogue.process_next),
        "studio-planning-worker": loop(0.5, series.process_next_extraction, breakdown.process_next),
        "studio-media-worker": loop(0.5, ai_video.process_next, ai_video.poll_next, finals.process_next_ai, finals.poll_next),
        # FFmpeg encodes can take minutes; they get their own thread so provider polling is not held up.
        "studio-encode-worker": loop(0.5, rough_cut.process_next, episode_sound.process_next_build, delivery.process_next),
        "studio-recovery-worker": loop(0.5, recovery.process_next),
    }

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        threads = [threading.Thread(target=target, name=name, daemon=True) for name, target in workers.items()] if worker else []
        for thread in threads:
            thread.start()
        yield
        stop.set()
        for thread in threads:
            thread.join(timeout=2)

    app = FastAPI(title="AI Animation Studio", version="0.3.0", lifespan=lifespan)
    for name, service in (("studio", studio), ("recovery", recovery), ("series", series), ("style", style), ("breakdown", breakdown), ("routes", routes),
                          ("dialogue", dialogue), ("timing", timing), ("animation", animation), ("ue_branch", ue_branch), ("ai_video", ai_video),
                          ("ue_proxy", ue_proxy), ("rough_cut", rough_cut), ("finals", finals), ("episode_sound", episode_sound), ("delivery", delivery),
                          ("progress", progress), ("claude_code", claude_code)):
        setattr(app.state, name, service)
    install_series_api(app, studio, series, BudgetService(studio), ToolSettings(studio))
    install_episode_api(app, VoiceSettings(studio), style, breakdown)
    install_route_voice_api(app, routes, dialogue, timing)
    install_zone_b_api(app, ai_video.settings, animation, ue_branch, ai_video, ue_proxy, rough_cut, finals)
    install_zone_c_api(app, episode_sound, delivery, progress)

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
            if request.method not in ("GET", "HEAD"):
                with studio.db.connect() as conn:
                    snapshot_active = conn.execute("SELECT 1 FROM recovery_jobs WHERE kind='BACKUP' AND state='RUNNING' LIMIT 1").fetchone()
                if snapshot_active:
                    return JSONResponse({"detail": "Đang sao lưu snapshot; chờ hoàn tất rồi thực hiện thay đổi."}, status_code=409)
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
        # `app` is how the launcher tells this server from anything else listening on the port.
        return {"status": "ok", "app": APP_ID, "unreal_configured": studio.unreal_settings()["configured"], "build_id": build_id}

    @app.get("/session")
    def session():
        token = secrets.token_urlsafe(32)
        sessions.add(token)
        response = JSONResponse({"session_token": token, "app": APP_ID, "unreal_configured": studio.unreal_settings()["configured"], "build_id": build_id})
        response.set_cookie("studio_session", token, httponly=True, secure=False, samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/settings/unreal")
    def unreal_settings():
        return studio.unreal_settings()

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

    @app.get("/settings/claude-code")
    def get_claude_code():
        return claude_code.status()

    @app.post("/settings/claude-code")
    def save_claude_code(body: SaveClaudeCodeIn):
        return claude_code.save(body.expected_revision, body.executable_path, body.model)

    @app.post("/settings/claude-code/browse")
    def browse_claude_code():
        from .local_picker import pick_file
        return {"path": pick_file("claude", claude_code.executable())}

    @app.post("/settings/claude-code/test")
    def test_claude_code():
        return claude_code.test()

    @app.post("/settings/ai/check")
    def check_ai_connection():
        return check_connection(ai_settings.get()["model"])

    @app.get("/settings/recovery")
    def recovery_settings():
        return recovery.settings()

    @app.post("/settings/recovery")
    def save_recovery_settings(body: RecoverySettingsIn):
        return recovery.save_settings(**body.model_dump())

    @app.post("/settings/recovery/browse")
    def browse_recovery_folder():
        from .local_picker import pick_folder
        path = pick_folder()
        if not path:
            raise DomainError("Đã hủy chọn thư mục.", 409)
        return {"path": path}

    @app.get("/recovery/jobs")
    def recovery_jobs():
        return recovery.jobs()

    @app.post("/recovery/jobs")
    def enqueue_recovery(body: RecoveryIn):
        return recovery.enqueue(**body.model_dump())

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

    @app.get("/events")
    def events(after: int = 0, project_id: str | None = None):
        return studio.events(after, project_id)

    @app.get("/events/stream")
    async def event_stream(request: Request, after: int = 0, project_id: str | None = None, from_latest: bool = False):
        async def generate():
            # A screen that only wants to know when something changes starts at the newest event instead of replaying the history.
            cursor = studio.last_event_id(project_id) if from_latest and not after else after
            while not await request.is_disconnected():
                for event in studio.events(cursor, project_id):
                    cursor = event["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    # Added last so it wraps everything above, including the answers of the guard.
    app.add_middleware(EnglishResponses)
    return app


def __getattr__(name: str):
    # `studio.api:app` is built on first use. Importing this module must not open the operator's data folder:
    # tests and tools import it, and an import-time app would migrate and "recover" the real database.
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
