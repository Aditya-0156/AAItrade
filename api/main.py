import logging
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from api.routers import sessions, trades, portfolio, decisions, tool_calls, journal, summary
from api.ws.feed import websocket_feed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AAItrade Dashboard API",
    description="Read-only API for the AAItrade autonomous trading system",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# Routers
app.include_router(sessions.router)
app.include_router(trades.router)
app.include_router(portfolio.router)
app.include_router(decisions.router)
app.include_router(tool_calls.router)
app.include_router(journal.router)
app.include_router(summary.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws/feed")
async def ws_feed(websocket: WebSocket):
    await websocket_feed(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
