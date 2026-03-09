"""
app/main.py
───────────
FastAPI application entry point.
- Registers all routers
- Configures CORS (permissive for local dev)
- Provides /health endpoint
- Configures structured logging
"""
import logging
import sys

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
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
    version="0.1.0",
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
    return {"status": "ok", "version": "0.1.0", "env": settings.app_env}


# ── Startup / shutdown events ─────────────────────────────────────────────────
@app.on_event("startup")
async def on_startup():
    logger.info("ADRC API starting up...")
    logger.info(
        f"Environment: {settings.app_env} | Log level: {settings.log_level}"
    )


@app.on_event("shutdown")
async def on_shutdown():
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
