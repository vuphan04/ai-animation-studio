from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import create_app


def create_standalone_app(
    data_root: Path | None = None,
    dist_dir: Path | None = None,
    *,
    testing: bool = False,
    worker: bool = True,
) -> FastAPI:
    """Serve the built UI and the local API from one loopback process."""
    dist = dist_dir or Path(__file__).resolve().parents[3] / "apps" / "web" / "dist"
    if not (dist / "index.html").is_file():
        raise FileNotFoundError(f"UI build missing: {dist / 'index.html'}")
    api = create_app(data_root, testing=testing, worker=worker)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with api.router.lifespan_context(api):
            yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def ui_cache_policy(request, call_next):
        response = await call_next(request)
        if request.url.path in ("/", "/index.html"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/api", api)
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app
