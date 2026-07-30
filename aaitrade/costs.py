"""Expense engine — tracks every rupee the system spends.

Three cost streams, all reported in INR:

1. TRADING CHARGES — STT, stamp duty, exchange + SEBI fees, GST, and the flat
   ₹15.34 DP charge per sell. Computed per trade by executor.transaction_costs
   and stored on the trades table.
2. CLAUDE API — token usage per call, priced from the table below.
3. SUBSCRIPTIONS — Zerodha Kite Connect ₹500/month, recorded once per month.

Net profit = realised P&L (already net of trading charges) − API − subscriptions.
That is the number that matters: what actually lands in the account.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)

# USD → INR. Override with AAITRADE_USD_INR in .env if the rate moves a lot.
DEFAULT_USD_INR = 88.0

# Claude API list pricing, USD per million tokens (input, output).
# Cache reads bill at ~0.1x input; 5-minute cache writes at ~1.25x input.
# Sonnet 5 has introductory pricing ($2/$10) through 2026-08-31 — list rates
# are used here so the tracker never UNDER-states what a month can cost.
_MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
}
_FALLBACK_PRICING = (3.00, 15.00)  # unknown model — assume Sonnet-tier

CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25

ZERODHA_MONTHLY_INR = 500.0


def _usd_inr() -> float:
    import os
    try:
        return float(os.environ.get("AAITRADE_USD_INR", DEFAULT_USD_INR))
    except ValueError:
        return DEFAULT_USD_INR


def compute_call_cost_inr(model: str, input_tokens: int, output_tokens: int,
                          cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Cost of one Claude API call in INR."""
    in_usd, out_usd = _MODEL_PRICING_USD_PER_MTOK.get(model, _FALLBACK_PRICING)
    usd = (
        input_tokens / 1e6 * in_usd
        + output_tokens / 1e6 * out_usd
        + cache_read_tokens / 1e6 * in_usd * CACHE_READ_MULTIPLIER
        + cache_write_tokens / 1e6 * in_usd * CACHE_WRITE_MULTIPLIER
    )
    return round(usd * _usd_inr(), 4)


def record_api_usage(session_id: int | None, cycle_number: int | None, model: str,
                     usage: dict, purpose: str = "cycle") -> float:
    """Persist one call's token usage + cost. Returns the cost in INR."""
    try:
        cost = compute_call_cost_inr(
            model,
            usage.get("input", 0), usage.get("output", 0),
            usage.get("cache_read", 0), usage.get("cache_write", 0),
        )
        db.insert("api_usage", {
            "session_id": session_id,
            "cycle_number": cycle_number,
            "model": model,
            "purpose": purpose,
            "input_tokens": usage.get("input", 0),
            "output_tokens": usage.get("output", 0),
            "cache_read_tokens": usage.get("cache_read", 0),
            "cache_write_tokens": usage.get("cache_write", 0),
            "cost_inr": cost,
            "created_at": db.now_iso(),
        })
        return cost
    except Exception as e:
        logger.warning(f"record_api_usage failed: {e}")
        return 0.0


def ensure_monthly_subscription(session_id: int | None = None,
                                amount: float = ZERODHA_MONTHLY_INR,
                                label: str = "Zerodha Kite Connect API") -> None:
    """Record the monthly broker API fee once per calendar month (idempotent)."""
    period = datetime.now(_IST).strftime("%Y-%m")
    try:
        existing = db.query_one(
            "SELECT id FROM expenses WHERE category = 'subscription' AND label = ? AND period = ?",
            (label, period),
        )
        if existing:
            return
        db.insert("expenses", {
            "session_id": session_id,
            "category": "subscription",
            "label": label,
            "amount_inr": amount,
            "period": period,
            "created_at": db.now_iso(),
        })
        logger.info(f"Recorded monthly subscription expense: {label} ₹{amount} ({period})")
    except Exception as e:
        logger.warning(f"ensure_monthly_subscription failed: {e}")


def expense_summary(session_id: int | None = None) -> dict:
    """Full expense + net-profit breakdown.

    session_id scopes trading charges and API cost to one session;
    subscriptions are always account-wide (they aren't per-session costs).
    """
    where_trades = "WHERE session_id = ?" if session_id else ""
    params: tuple = (session_id,) if session_id else ()

    # 1. Trading charges (STT, stamp, exchange, GST, DP) — from the trades table
    row = db.query_one(
        f"SELECT COALESCE(SUM(charges), 0) AS total, COUNT(*) AS n FROM trades {where_trades}",
        params,
    )
    trading_charges = round(row["total"] if row else 0, 2)
    trade_count = row["n"] if row else 0

    side_row = db.query_one(
        f"SELECT COALESCE(SUM(CASE WHEN action='BUY' THEN charges ELSE 0 END), 0) AS buy_c, "
        f"COALESCE(SUM(CASE WHEN action='SELL' THEN charges ELSE 0 END), 0) AS sell_c "
        f"FROM trades {where_trades}",
        params,
    )
    buy_charges = round(side_row["buy_c"] if side_row else 0, 2)
    sell_charges = round(side_row["sell_c"] if side_row else 0, 2)

    # 2. Claude API cost
    api_row = db.query_one(
        f"SELECT COALESCE(SUM(cost_inr), 0) AS total, "
        f"COALESCE(SUM(input_tokens), 0) AS inp, COALESCE(SUM(output_tokens), 0) AS outp, "
        f"COALESCE(SUM(cache_read_tokens), 0) AS cr, COUNT(*) AS calls "
        f"FROM api_usage {'WHERE session_id = ?' if session_id else ''}",
        params,
    )
    api_cost = round(api_row["total"] if api_row else 0, 2)

    # 3. Subscriptions (account-wide)
    sub_row = db.query_one(
        "SELECT COALESCE(SUM(amount_inr), 0) AS total FROM expenses WHERE category = 'subscription'"
    )
    subscriptions = round(sub_row["total"] if sub_row else 0, 2)

    other_row = db.query_one(
        "SELECT COALESCE(SUM(amount_inr), 0) AS total FROM expenses WHERE category != 'subscription'"
    )
    other = round(other_row["total"] if other_row else 0, 2)

    # 4. Realised trading profit, computed from CAPITAL MOVEMENT rather than
    #    the pnl column.
    #
    #    The trades.pnl figure only nets out the SELL-side charges. Buy-side
    #    charges (STT, stamp, GST on entry) are debited from cash at purchase
    #    and never appear in pnl — so summing pnl overstates the real profit
    #    by exactly the buy-side charges.
    #
    #    (free cash + deployed at cost + secured) − starting capital is net of
    #    every trading charge by construction, because both sides were taken
    #    out of those balances. Open positions are counted at cost, so this is
    #    realised profit only — unrealised gains are correctly excluded.
    sess_rows = db.query(
        "SELECT id, starting_capital, current_capital, secured_profit FROM sessions"
        + (" WHERE id = ?" if session_id else ""),
        params,
    )
    realised_pnl = 0.0
    for srow in sess_rows:
        dep = db.query_one(
            "SELECT COALESCE(SUM(quantity * avg_price), 0) AS d FROM portfolio WHERE session_id = ?",
            (srow["id"],),
        )
        deployed_at_cost = dep["d"] if dep else 0
        total_value = srow["current_capital"] + deployed_at_cost + srow["secured_profit"]
        realised_pnl += total_value - srow["starting_capital"]
    realised_pnl = round(realised_pnl, 2)

    total_expenses = round(trading_charges + api_cost + subscriptions + other, 2)
    # realised_pnl is already net of ALL trading charges (both sides) — so the
    # only costs left to deduct are the ones outside the broker account.
    net_profit = round(realised_pnl - api_cost - subscriptions - other, 2)

    # Today's API burn, for a running-rate view
    today = datetime.now(_IST).strftime("%Y-%m-%d")
    today_row = db.query_one(
        "SELECT COALESCE(SUM(cost_inr), 0) AS total FROM api_usage WHERE created_at LIKE ?",
        (f"{today}%",),
    )

    return {
        "trading_charges": trading_charges,
        "buy_charges": buy_charges,
        "sell_charges": sell_charges,
        "trade_count": trade_count,
        "api_cost": api_cost,
        "api_calls": api_row["calls"] if api_row else 0,
        "api_tokens": {
            "input": api_row["inp"] if api_row else 0,
            "output": api_row["outp"] if api_row else 0,
            "cache_read": api_row["cr"] if api_row else 0,
        },
        "api_cost_today": round(today_row["total"] if today_row else 0, 2),
        "subscriptions": subscriptions,
        "other": other,
        "total_expenses": total_expenses,
        "realised_pnl_net_of_charges": realised_pnl,
        "net_profit_after_all_expenses": net_profit,
        "usd_inr_rate": _usd_inr(),
    }


def expense_breakdown_lines(session_id: int | None = None) -> list[str]:
    """Compact lines for the Telegram daily summary / briefing."""
    s = expense_summary(session_id)
    return [
        f"Realised profit (net of ALL trading charges): ₹{s['realised_pnl_net_of_charges']:,.2f}",
        f"Trading charges paid: ₹{s['trading_charges']:,.2f} over {s['trade_count']} trades "
        f"(buy ₹{s['buy_charges']:,.2f} + sell ₹{s['sell_charges']:,.2f}) — already deducted above",
        f"Claude API: ₹{s['api_cost']:,.2f} ({s['api_calls']} calls, ₹{s['api_cost_today']:,.2f} today)",
        f"Subscriptions: ₹{s['subscriptions']:,.2f}",
        f"NET after everything: ₹{s['net_profit_after_all_expenses']:,.2f}",
    ]
