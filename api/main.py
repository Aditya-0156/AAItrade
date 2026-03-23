import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.routers import sessions, trades, portfolio, decisions, tool_calls, journal, summary
from api.routers import control
from api.ws.feed import websocket_feed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_DIST = Path(__file__).resolve().parent.parent / "dashboard" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize trading server on startup, recover active sessions."""
    from aaitrade.server import get_server
    server = get_server()
    server.initialize()
    server.recover_all_active()
    logger.info("Trading server ready — active sessions recovered")
    yield
    logger.info("Shutting down trading server")


app = FastAPI(
    title="AAItrade Dashboard API",
    description="Command center API for the AAItrade autonomous trading system",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Read-only routers
app.include_router(sessions.router)
app.include_router(trades.router)
app.include_router(portfolio.router)
app.include_router(decisions.router)
app.include_router(tool_calls.router)
app.include_router(journal.router)
app.include_router(summary.router)

# Control router (write operations — start/stop/pause/resume/token)
app.include_router(control.router)


@app.get("/api/health")
async def health():
    from aaitrade.server import get_server
    server = get_server()
    return {
        "status": "ok",
        "running_sessions": server.get_running_sessions(),
    }


@app.websocket("/ws/feed")
async def ws_feed(websocket: WebSocket):
    await websocket_feed(websocket)


# Serve built dashboard as static files (if dist/ exists)
# Falls back gracefully if not built yet — API still works.
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str = ""):
        # API and WS routes are matched first — this only catches frontend routes
        index = _DIST / "index.html"
        return FileResponse(str(index))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
