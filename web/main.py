import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .routes_api import router as api_router
from .routes_ws import router as ws_router
from .stats_poller import StatsPoller

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    poller = StatsPoller(interval_sec=settings.poll_interval_sec, mock=settings.mock_stats)
    await poller.start()
    app.state.poller = poller
    try:
        yield
    finally:
        await poller.stop()


app = FastAPI(title="Lockdown Admin", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(ws_router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/about")
def about() -> FileResponse:
    return FileResponse(STATIC_DIR / "about.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
