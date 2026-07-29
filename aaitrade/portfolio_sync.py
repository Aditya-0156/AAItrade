"""Portfolio reconciliation — compares AAItrade's DB with Zerodha holdings.

CRITICAL OWNERSHIP RULE
───────────────────────
The AAItrade DB is the ONLY source of truth for what this system owns.
The Zerodha account also holds the user's PERSONAL trades (e.g. 254 HDFCBANK
bought by hand). Those must never be touched, sold, counted as P&L, or
adopted into the system's portfolio.

Therefore this module is REPORT-ONLY:
- It NEVER inserts a Kite holding into the portfolio table (that would steal
  the user's personal shares — HDFCBANK is on the seed watchlist, so the old
  "adopt watchlist symbols" behaviour would have done exactly that).
- It NEVER deletes a DB position because Kite doesn't show it.
- It NEVER edits avg_price or quantity.
It only warns when Kite holds FEWER shares than the system thinks it owns —
the one case that actually breaks the system's ability to sell.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)


def sync_portfolio_with_kite(session_id: int, kite) -> dict:
    """Reconcile (read-only) the session's portfolio against Zerodha holdings.

    Returns a report. Makes NO writes to the portfolio table.
    """
    session = db.query_one(
        "SELECT id, execution_mode FROM sessions WHERE id = ?",
        (session_id,),
    )
    if not session:
        return {"error": "Session not found"}
    if session["execution_mode"] != "live":
        return {"status": "skipped", "reason": "Paper mode — no reconciliation needed"}

    warnings: list[dict] = []
    external: list[dict] = []

    try:
        kite_holdings = kite.holdings()
        # Total broker quantity = settled + T1 (bought today, not yet settled)
        kite_qty: dict[str, int] = {}
        for h in kite_holdings:
            total = (h.get("quantity") or 0) + (h.get("t1_quantity") or 0)
            if total > 0:
                kite_qty[h["tradingsymbol"]] = total

        db_positions = db.query(
            "SELECT id, symbol, quantity, avg_price FROM portfolio WHERE session_id = ?",
            (session_id,),
        )
        db_map = {p["symbol"]: p for p in db_positions}

        # 1. Every system position must be BACKED by at least that many shares
        #    in the broker account. Fewer = the system cannot sell what it thinks
        #    it owns (manual sale, partial fill, or a mis-recorded trade).
        for symbol, pos in db_map.items():
            broker = kite_qty.get(symbol, 0)
            if broker < pos["quantity"]:
                warnings.append({
                    "symbol": symbol,
                    "type": "under_backed",
                    "db_qty": pos["quantity"],
                    "broker_qty": broker,
                })
                logger.error(
                    f"RECONCILE: {symbol} — system DB says {pos['quantity']} shares but "
                    f"broker shows only {broker}. NOT auto-corrected. Investigate before "
                    f"the system tries to sell."
                )

        # 2. Everything the broker holds beyond what the system bought is the
        #    USER'S OWN. Log it as external so it's visible, never adopt it.
        for symbol, broker in kite_qty.items():
            owned = db_map[symbol]["quantity"] if symbol in db_map else 0
            if broker > owned:
                external.append({
                    "symbol": symbol,
                    "external_qty": broker - owned,
                    "system_qty": owned,
                })

        if external:
            logger.info(
                "RECONCILE: external (user-owned) holdings ignored by the system: "
                + ", ".join(f"{e['symbol']} x{e['external_qty']}" for e in external)
            )

        # NOTE: current_capital is NEVER synced with Kite margins — the Zerodha
        # account holds the user's own money and manual positions. The system's
        # capital is tracked internally from its own trades only.

    except Exception as e:
        logger.error(f"Portfolio reconciliation failed: {e}", exc_info=True)
        return {"error": str(e)}

    status = "ok" if not warnings else "warnings"
    logger.info(
        f"Portfolio reconciliation: {len(warnings)} warning(s), "
        f"{len(external)} external holding(s) ignored"
    )
    return {
        "status": status,
        "warnings": warnings,
        "external_holdings": external,
        "read_only": True,
        "timestamp": datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S"),
    }


def external_holdings_note(session_id: int, kite) -> str:
    """One-line briefing note listing user-owned shares the system must ignore."""
    try:
        report = sync_portfolio_with_kite(session_id, kite)
        ext = report.get("external_holdings") or []
        if not ext:
            return ""
        items = ", ".join(f"{e['symbol']} x{e['external_qty']}" for e in ext)
        return (
            f"\n\n🔒 NOT YOURS (the user's own holdings in the same Zerodha account): {items}. "
            f"These are invisible to your portfolio and P&L. NEVER sell them — only sell "
            f"positions that appear in get_portfolio()."
        )
    except Exception:
        return ""
