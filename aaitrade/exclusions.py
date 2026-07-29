"""Symbol exclusions — total separation between the user's own trading and the system's.

WHY SYMBOL-LEVEL, NOT JUST QUANTITY-LEVEL
─────────────────────────────────────────
Clamping sell quantity stops the system selling more than it owns, but it does
NOT stop the two books from tangling. Zerodha holds one pooled position per
symbol and disposes FIFO: if the user owns 254 HDFCBANK bought in March and the
system buys 10 more today, a system sell of 10 disposes the OLDEST shares —
the user's — moving their cost basis, holding period, and tax lots.

So the rule is absolute: **the system never trades a symbol the user holds.**

The exclusion list is built from the broker account itself, so it needs no
manual upkeep:
- At session start, every symbol already in the Zerodha account is the user's.
- Every pre-market, any symbol that appears in the account which the system
  did NOT buy is added (the user bought something new).
- A symbol the system itself owns is never excluded by that rule.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)


def add_exclusion(session_id: int, symbol: str, reason: str, qty: int = 0) -> bool:
    """Exclude a symbol. Returns True if newly added."""
    existing = db.query_one(
        "SELECT id FROM excluded_symbols WHERE session_id = ? AND symbol = ?",
        (session_id, symbol),
    )
    if existing:
        db.update("excluded_symbols", existing["id"], {
            "external_qty": qty, "reason": reason, "updated_at": db.now_iso(),
        })
        return False
    db.insert("excluded_symbols", {
        "session_id": session_id,
        "symbol": symbol,
        "external_qty": qty,
        "reason": reason,
        "created_at": db.now_iso(),
        "updated_at": db.now_iso(),
    })
    logger.warning(f"EXCLUDED {symbol} ({qty} shares) — {reason}. System will not trade it.")
    return True


def remove_exclusion(session_id: int, symbol: str) -> bool:
    """Allow the system to trade a symbol again (user sold their holding)."""
    row = db.query_one(
        "SELECT id FROM excluded_symbols WHERE session_id = ? AND symbol = ?",
        (session_id, symbol),
    )
    if not row:
        return False
    with db.get_connection() as conn:
        conn.execute("DELETE FROM excluded_symbols WHERE id = ?", (row["id"],))
    logger.info(f"Exclusion lifted for {symbol} — system may trade it again")
    return True


def is_excluded(session_id: int, symbol: str) -> bool:
    return db.query_one(
        "SELECT 1 FROM excluded_symbols WHERE session_id = ? AND symbol = ?",
        (session_id, symbol),
    ) is not None


def get_exclusions(session_id: int) -> list[dict]:
    return db.query(
        "SELECT symbol, external_qty, reason, updated_at FROM excluded_symbols "
        "WHERE session_id = ? ORDER BY symbol",
        (session_id,),
    )


def excluded_symbol_set(session_id: int) -> set[str]:
    return {r["symbol"] for r in get_exclusions(session_id)}


def refresh_from_broker(session_id: int, kite, initial: bool = False) -> dict:
    """Sync exclusions with the broker account.

    Any symbol held at the broker in a quantity the system did not buy belongs
    to the user → exclude it. Symbols the user has fully sold are released.
    Returns {"added": [...], "released": [...], "excluded": [...]}.
    """
    added, released = [], []
    try:
        holdings = kite.holdings()
        broker_qty: dict[str, int] = {}
        for h in holdings:
            total = (h.get("quantity") or 0) + (h.get("t1_quantity") or 0)
            if total > 0:
                broker_qty[h["tradingsymbol"]] = total

        system_qty = {
            p["symbol"]: p["quantity"]
            for p in db.query(
                "SELECT symbol, quantity FROM portfolio WHERE session_id = ?", (session_id,)
            )
        }

        for symbol, broker in broker_qty.items():
            owned = system_qty.get(symbol, 0)
            external = broker - owned
            if external <= 0:
                continue  # fully accounted for by the system's own buys
            reason = (
                "Held in the Zerodha account at session start — user's own position"
                if initial else
                "Appeared in the Zerodha account without a system buy — user bought it"
            )
            if add_exclusion(session_id, symbol, reason, external):
                added.append(symbol)

        # Release symbols the user no longer holds (and the system doesn't own)
        for row in get_exclusions(session_id):
            sym = row["symbol"]
            if broker_qty.get(sym, 0) <= system_qty.get(sym, 0):
                if remove_exclusion(session_id, sym):
                    released.append(sym)

    except Exception as e:
        logger.error(f"Exclusion refresh failed: {e}", exc_info=True)
        return {"error": str(e), "added": added, "released": released}

    return {
        "added": added,
        "released": released,
        "excluded": [r["symbol"] for r in get_exclusions(session_id)],
    }


def exclusions_prompt_block(session_id: int) -> str:
    """Briefing-ready block naming the untouchable symbols."""
    rows = get_exclusions(session_id)
    if not rows:
        return ""
    items = ", ".join(f"{r['symbol']} ({r['external_qty']} shares)" for r in rows)
    return (
        f"\n\n🔒 OFF-LIMITS SYMBOLS — the user trades these themselves in the same account: {items}. "
        f"You may NOT buy or sell them, at any size, for any reason — the broker pools shares per "
        f"symbol and would dispose of the user's shares first. They are not on your watchlist and "
        f"any trade in them will be rejected. Ignore them completely and trade other stocks."
    )
