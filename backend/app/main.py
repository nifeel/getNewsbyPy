import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import purge_copied_fj_articles, router
from app.charts_db import init_charts_db
from app.config import BACKEND_DIR
from app.db import SessionLocal, init_db
from app.fj_settings import refresh_fj_cache
from app.ingest.events import sync_cn_events
from app.ingest.scheduler import start_scheduler, stop_scheduler
from app.news_ws import router as news_ws_router
from app.news_ws import start_news_watch, stop_news_watch
from app.proxy_settings import refresh_proxy_cache
from app.settings_api import router as settings_router
from app.access import access_key_middleware, auth_required
from app.llm_settings import refresh_llm_cache
from app.translate_settings import refresh_translate_cache

FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    try:
        init_charts_db()
    except Exception:
        logger.exception("init charts database failed")
    session = SessionLocal()
    try:
        refresh_proxy_cache(session)
        refresh_fj_cache(session)
        refresh_translate_cache(session)
        refresh_llm_cache(session)
        purge_copied_fj_articles(session)
        sync_cn_events(session)
    finally:
        session.close()
    start_scheduler()
    await start_news_watch()
    yield
    await stop_news_watch()
    stop_scheduler()


app = FastAPI(
    title="Tech Pulse",
    lifespan=lifespan,
    docs_url=None if auth_required() else "/docs",
    redoc_url=None if auth_required() else "/redoc",
)
app.include_router(router)
app.include_router(news_ws_router, prefix="/api")
app.include_router(settings_router)
app.middleware("http")(access_key_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://10.0.0.202:8088",
        "http://47.116.57.201",
        "http://47.116.57.201:80",
        "http://47.116.57.201:8088",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _mount_frontend(dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():
        return
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    async def frontend_index() -> FileResponse:
        return FileResponse(index)

    @app.get("/{full_path:path}")
    async def frontend_spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/") or full_path in {"docs", "redoc", "openapi.json"}:
            raise HTTPException(status_code=404)
        target = dist / full_path
        if target.is_file():
            return FileResponse(target)
        return FileResponse(index)


_mount_frontend(FRONTEND_DIST)
