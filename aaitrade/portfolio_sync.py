"""Portfolio sync — reconciles AAItrade DB with real Zerodha holdings.

For live trading, this runs once daily (pre-market) to ensure the DB
portfolio table matches actual Kite holdings. Catches discrepancies
from partial fills, manual trades, or system errors.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)


def sync_portfolio_with_kite(session_id: int, kite) -> dict:
    """Sync a session's portfolio with real Zerodha holdings.

    Compares DB portfolio table with Kite's holdings API.
    Returns a report of discrepancies found and corrections made.

    Only runs for LIVE mode sessions.
    """
    session = db.query_one(
        "SELECT id, execution_mode FROM sessions WHERE id = ?",
        (session_id,),
    )
    if not session:
        return {"error": "Session not found"}
    if session["execution_mode"] != "live":
        return {"status": "skipped", "reason": "Paper mode — no sync needed"}

    discrepancies = []

    try:
        # Get real holdings from Kite
        kite_holdings = kite.holdings()
        kite_positions = {h["tradingsymbol"]: h for h in kite_holdings if h.get("quantity", 0) > 0}

        # Get DB portfolio
        db_positions = db.query(
            "SELECT id, symbol, quantity, avg_price FROM portfolio WHERE session_id = ?",
            (session_id,),
        )
        db_map = {p["symbol"]: p for p in db_positions}

        # Check each DB position against Kite
        for symbol, db_pos in db_map.items():
            if symbol in kite_positions:
                kite_pos = kite_positions[symbol]
                kite_qty = kite_pos.get("quantity", 0)
                kite_avg = kite_pos.get("average_price", 0)

                if kite_qty != db_pos["quantity"]:
                    discrepancies.append({
                        "symbol": symbol,
                        "type": "quantity_mismatch",
                        "db_qty": db_pos["quantity"],
                        "kite_qty": kite_qty,
                        "action": "updated_db",
                    })
                    db.update("portfolio", db_pos["id"], {"quantity": kite_qty})
                    logger.warning(
                        f"SYNC: {symbol} quantity mismatch — "
                        f"DB: {db_pos['quantity']}, Kite: {kite_qty}. Updated DB."
                    )

                if abs(kite_avg - db_pos["avg_price"]) > 0.5:
                    discrepancies.append({
                        "symbol": symbol,
                        "type": "price_mismatch",
                        "db_avg": db_pos["avg_price"],
                        "kite_avg": kite_avg,
                        "action": "updated_db",
                    })
                    db.update("portfolio", db_pos["id"], {"avg_price": round(kite_avg, 2)})
                    logger.warning(
                        f"SYNC: {symbol} avg price mismatch — "
                        f"DB: ₹{db_pos['avg_price']}, Kite: ₹{kite_avg}. Updated DB."
                    )
            else:
                # Position in DB but not in Kite — was sold outside the system
                discrepancies.append({
                    "symbol": symbol,
                    "type": "missing_in_kite",
                    "db_qty": db_pos["quantity"],
                    "action": "removed_from_db",
                })
                with db.get_connection() as conn:
                    conn.execute("DELETE FROM portfolio WHERE id = ?", (db_pos["id"],))
                logger.warning(
                    f"SYNC: {symbol} in DB but not in Kite — removed from DB."
                )

        # Check for Kite positions not in DB (manual buys)
        watchlist_symbols = {
            w["symbol"] for w in db.query(
                "SELECT symbol FROM watchlist WHERE session_id = ? AND removed_at IS NULL",
                (session_id,),
            )
        }
        for symbol, kite_pos in kite_positions.items():
            if symbol not in db_map and symbol in watchlist_symbols:
                discrepancies.append({
                    "symbol": symbol,
                    "type": "missing_in_db",
                    "kite_qty": kite_pos["quantity"],
                    "action": "added_to_db",
                })
                db.insert("portfolio", {
                    "session_id": session_id,
                    "symbol": symbol,
                    "quantity": kite_pos["quantity"],
                    "avg_price": round(kite_pos.get("average_price", 0), 2),
                    "stop_loss_price": None,
                    "take_profit_price": None,
                    "opened_at": db.now_iso(),
                })
                logger.warning(
                    f"SYNC: {symbol} in Kite but not in DB — added to DB. "
                    f"(qty={kite_pos['quantity']}, avg=₹{kite_pos.get('average_price', 0):.2f})"
                )

        # NOTE: Do NOT sync current_capital with Kite margins!
        # Reason: Zerodha account may have money unrelated to this session.
        # Current capital is tracked internally by the system (gains/losses from trades).
        # We only sync positions (holdings), not cash balance.

    except Exception as e:
        logger.error(f"Portfolio sync failed: {e}", exc_info=True)
        return {"error": str(e)}

    status = "synced" if not discrepancies else "corrected"
    logger.info(f"Portfolio sync complete: {len(discrepancies)} discrepancy(ies) found")
    return {
        "status": status,
        "discrepancies": discrepancies,
        "timestamp": datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S"),
    }
