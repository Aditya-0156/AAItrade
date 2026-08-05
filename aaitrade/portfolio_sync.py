"""Portfolio reconciliation — compares AAItrade's DB with Zerodha holdings.

CRITICAL OWNERSHIP RULE
───────────────────────
The AAItrade DB is the ONLY source of truth for what this system owns.
The Zerodha account also holds the user's PERSONAL trades (e.g. 254 HDFCBANK
bought by hand). Those must never be touched, sold, counted as P&L, or
adopted into the system's portfolio.

Therefore this module never changes WHAT or HOW MUCH we own:
- It NEVER inserts a Kite holding into the portfolio table (that would steal
  the user's personal shares — HDFCBANK is on the seed watchlist, so the old
  "adopt watchlist symbols" behaviour would have done exactly that).
- It NEVER deletes a DB position because Kite doesn't show it.
- It NEVER edits quantity.
It warns when Kite holds FEWER shares than the system thinks it owns — the one
case that breaks our ability to sell.

The single exception is COST BASIS: when the broker holds exactly the quantity
we claim (proving no personal lot is blended in), the broker's average price is
authoritative and we adopt it. Quantities are never touched; only the price we
believe we paid, which the broker knows better than we do.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)


def sync_portfolio_with_kite(session_id: int, kite) -> dict:
    """Reconcile the session's portfolio against Zerodha holdings.

    Quantities are never modified. The only write is a cost-basis correction
    when the broker holds exactly the quantity we claim — see module docstring.
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
    corrections: list[dict] = []

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

        # 1b. COST-BASIS DRIFT. The broker is the authority on what a lot
        #     actually cost. Drift appears when a sell is netted against an
        #     unsettled (T+1) buy — Zerodha treats it as an intraday round
        #     trip and the original delivery lot survives untouched, while
        #     our books have moved on to the re-entry price. That leaves the
        #     system managing a position against a false basis: wrong P&L,
        #     wrong distance to target.
        #
        #     Correcting is only safe when the broker quantity EXACTLY matches
        #     ours — that proves there is no personal lot blended into the
        #     broker's average. When the user also owns the symbol, the two
        #     cost bases cannot be disentangled, so we only warn.
        for symbol, pos in db_map.items():
            kite_pos = next(
                (h for h in kite_holdings if h["tradingsymbol"] == symbol), None
            )
            if not kite_pos:
                continue
            broker_avg = kite_pos.get("average_price") or 0
            broker_total = (kite_pos.get("quantity") or 0) + (kite_pos.get("t1_quantity") or 0)
            if broker_avg <= 0 or pos["avg_price"] <= 0:
                continue
            drift_pct = abs(broker_avg - pos["avg_price"]) / pos["avg_price"] * 100
            if drift_pct < 0.5:
                continue

            if broker_total == pos["quantity"]:
                db.update("portfolio", pos["id"], {"avg_price": round(broker_avg, 2)})
                # The journal carries the SAME basis and is what the model reads
                # back when it reasons about a position. Correcting only the
                # portfolio leaves the two disagreeing, and the model trusts the
                # journal — so the false entry price survives the fix.
                journal = db.query_one(
                    "SELECT id FROM trade_journal WHERE session_id = ? AND symbol = ? "
                    "AND status = 'open' ORDER BY id DESC",
                    (session_id, symbol),
                )
                if journal:
                    db.update("trade_journal", journal["id"],
                              {"entry_price": round(broker_avg, 2)})
                corrections.append({
                    "symbol": symbol, "type": "cost_basis_corrected",
                    "was": pos["avg_price"], "now": round(broker_avg, 2),
                    "drift_pct": round(drift_pct, 2),
                    "journal_updated": bool(journal),
                })
                _note_correction_in_memory(
                    session_id, symbol, pos["avg_price"], round(broker_avg, 2)
                )
                logger.warning(
                    f"RECONCILE: {symbol} cost basis corrected {pos['avg_price']} -> "
                    f"{broker_avg} ({drift_pct:.1f}% drift). Broker is authoritative for a "
                    f"lot we exclusively own."
                )
            else:
                warnings.append({
                    "symbol": symbol, "type": "cost_basis_drift_unresolvable",
                    "db_avg": pos["avg_price"], "broker_avg": round(broker_avg, 2),
                    "drift_pct": round(drift_pct, 2),
                })
                logger.error(
                    f"RECONCILE: {symbol} cost basis differs ({pos['avg_price']} vs broker "
                    f"{broker_avg}) but the broker also holds the user's shares — cannot "
                    f"separate the lots. NOT corrected; investigate manually."
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
        f"{len(corrections)} cost-basis correction(s), "
        f"{len(external)} external holding(s) ignored"
    )
    return {
        "status": status,
        "warnings": warnings,
        "corrections": corrections,
        "external_holdings": external,
        "quantities_read_only": True,
        "timestamp": datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _note_correction_in_memory(session_id: int, symbol: str, was: float, now: float) -> None:
    """Append a correction line to the session's self-maintained memory.

    The memory is free text the model rewrites each cycle, and it copies
    position prices into it. A basis fixed in the tables therefore keeps
    circulating in the narrative — the live memory still read
    "CDSL: 37@1348.70" after the tables said 1330.30. Writing the correction
    where the model will read it is what actually stops the stale number.
    """
    try:
        row = db.query_one(
            "SELECT id, content FROM session_memory WHERE session_id = ?", (session_id,)
        )
        if not row:
            return
        note = (
            f"\n\n[SYSTEM CORRECTION {datetime.now(_IST).strftime('%Y-%m-%d %H:%M')}] "
            f"{symbol} entry price corrected ₹{was} → ₹{now} (broker is authoritative). "
            f"Any earlier note in this memory quoting ₹{was} for {symbol} is wrong — "
            f"use ₹{now} and recompute P&L and distance-to-target from it."
        )
        db.update("session_memory", row["id"], {
            "content": (row["content"] or "") + note,
            "updated_at": db.now_iso(),
        })
    except Exception as e:
        logger.warning(f"Could not note {symbol} correction in session memory: {e}")


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
