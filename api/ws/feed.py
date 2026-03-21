import asyncio
import json
import logging
from datetime import datetime, timezone
from fastapi import WebSocket, WebSocketDisconnect
import aiosqlite
from api.database import DB_PATH

logger = logging.getLogger(__name__)

POLL_INTERVAL = 15  # seconds


async def websocket_feed(websocket: WebSocket):
    """
    WebSocket endpoint that polls the DB every 15s and pushes new
    decisions and tool_calls to the client.
    """
    await websocket.accept()
    logger.info("WebSocket client connected")

    last_decision_id: int = 0
    last_tool_call_id: int = 0

    # Initialise the high-water marks from current DB state so we
    # don't flood the client with historical rows on connect.
    try:
        async with aiosqlite.connect(f"file:{DB_PATH}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT COALESCE(MAX(id), 0) FROM decisions") as cur:
                row = await cur.fetchone()
                last_decision_id = row[0] if row else 0
            async with db.execute("SELECT COALESCE(MAX(id), 0) FROM tool_calls") as cur:
                row = await cur.fetchone()
                last_tool_call_id = row[0] if row else 0
    except Exception as exc:
        logger.warning("Could not initialise WS high-water marks: %s", exc)

    try:
        while True:
            await asyncio.sleep(POLL_INTERVAL)

            events: list[dict] = []

            try:
                async with aiosqlite.connect(f"file:{DB_PATH}?mode=ro", uri=True) as db:
                    db.row_factory = aiosqlite.Row

                    # New decisions
                    async with db.execute(
                        """
                        SELECT d.id, d.session_id, s.name as session_name,
                               d.cycle_number, d.action, d.symbol,
                               CAST(d.quantity AS REAL) as quantity,
                               d.reason, d.confidence, d.flags, d.decided_at
                        FROM decisions d
                        LEFT JOIN sessions s ON s.id = d.session_id
                        WHERE d.id > ?
                        ORDER BY d.id ASC
                        LIMIT 50
                        """,
                        (last_decision_id,),
                    ) as cur:
                        rows = await cur.fetchall()
                        for row in rows:
                            d = dict(row)
                            d["type"] = "decision"
                            events.append(d)
                            last_decision_id = max(last_decision_id, d["id"])

                    # New tool calls
                    async with db.execute(
                        """
                        SELECT tc.id, tc.session_id, s.name as session_name,
                               tc.cycle_number, tc.tool_name, tc.parameters,
                               tc.result_summary, tc.called_at
                        FROM tool_calls tc
                        LEFT JOIN sessions s ON s.id = tc.session_id
                        WHERE tc.id > ?
                        ORDER BY tc.id ASC
                        LIMIT 50
                        """,
                        (last_tool_call_id,),
                    ) as cur:
                        rows = await cur.fetchall()
                        for row in rows:
                            d = dict(row)
                            d["type"] = "tool_call"
                            events.append(d)
                            last_tool_call_id = max(last_tool_call_id, d["id"])

            except Exception as exc:
                logger.warning("WS DB poll error: %s", exc)

            if events:
                payload = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "events": events,
                }
                await websocket.send_text(json.dumps(payload))
            else:
                # Send a heartbeat so the client knows the connection is alive
                await websocket.send_text(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "events": []}))

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
