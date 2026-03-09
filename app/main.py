"""
app/main.py
───────────
FastAPI application entry point.
- Registers all routers
- Configures CORS (permissive for local dev)
- Provides /health endpoint
- Configures structured logging
- Runs background pollers for USGS earthquakes + IMD/weather warnings
"""
import asyncio
import logging
import sys

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.routers import crises, ingest, nodes, orchestration
from app.services.notifier import notifier

# ── Logging setup ─────────────────────────────────────────────────────────────
settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.DEBUG),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="ADRC API",
    description=(
        "**AI Disaster Response Coordinator** — Backend API\n\n"
        "Manages the Trusted Node verification pipeline, Active Crises, "
        "and bridges the AutoGen multi-agent orchestration layer."
    ),
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS (open for local dev; tighten for production) ─────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # React dev server on localhost:3000
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(ingest.router)
app.include_router(crises.router)
app.include_router(nodes.router)
app.include_router(orchestration.router)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"], summary="Health Check")
async def health():
    return {"status": "ok", "version": "0.2.0", "env": settings.app_env}


# ── Background pollers ────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 300  # 5 minutes

async def _disaster_feed_poller():
    """Background loop that polls USGS + IMD/weather feeds every 5 minutes."""
    from app.services.earthquake_watcher import poll_usgs_earthquakes
    from app.services.weather_watcher import poll_imd_warnings

    # Wait 10 seconds after startup before first poll
    await asyncio.sleep(10)
    logger.info("🛰️ Background disaster feed poller started (every 5 min)")

    while True:
        try:
            async with AsyncSessionLocal() as db:
                await poll_usgs_earthquakes(db)
                await poll_imd_warnings(db)
                await db.commit()
        except Exception as e:
            logger.error(f"Disaster feed poller error: {e}")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ── Startup / shutdown events ─────────────────────────────────────────────────
_poller_task = None

@app.on_event("startup")
async def on_startup():
    global _poller_task
    logger.info("ADRC API starting up...")
    logger.info(
        f"Environment: {settings.app_env} | Log level: {settings.log_level}"
    )
    # Start background disaster feed poller
    _poller_task = asyncio.create_task(_disaster_feed_poller())


@app.on_event("shutdown")
async def on_shutdown():
    global _poller_task
    if _poller_task:
        _poller_task.cancel()
    logger.info("ADRC API shutting down.")


# ── WebSockets ────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Accepts WebSocket connections from the React Dashboard.
    Pushes live updates when an SMS arrives, a crisis changes state, etc.
    """
    await notifier.connect(websocket)
    try:
        while True:
            # We don't really expect incoming WS messages from the dashboard,
            # but we need to keep the connection open and listen for disconnects
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        notifier.disconnect(websocket)

