"""Lessons — the learning loop. Every closed trade and every checked prediction
leaves a durable record that future cycles read.

What lives in the DB and why it's effective:
- `lessons` rows are SHORT (1-3 sentences), factual, and cross-session — the
  highest-value context per token in the whole system. A raw trade log tells
  Claude what happened; a lesson tells it what to do differently.
- Deterministic performance stats (win rate, avg win/loss, cost drag, hold
  days) are computed from the trades table on demand — never stored, never
  stale, ~1 line of briefing.
- Prediction records score the weekend outlook against what actually happened,
  so the research prompt can calibrate itself ("your last 5 open-bias calls:
  3 hits").

Categories: trade_outcome | prediction | insight (Claude-written via tool).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)


def record_trade_lesson(session_id: int, symbol: str, position: dict,
                        exit_price: float, net_pnl: float, exit_reason: str):
    """Write a deterministic post-trade record when a position fully closes."""
    try:
        journal = db.query_one(
            "SELECT key_thesis, target_price, stop_price, opened_at FROM trade_journal "
            "WHERE session_id = ? AND symbol = ? ORDER BY id DESC LIMIT 1",
            (session_id, symbol),
        )
        invested = position["avg_price"] * position.get("quantity", 1)
        pct = net_pnl / invested * 100 if invested else 0

        held_days = "?"
        if journal and journal.get("opened_at"):
            try:
                opened = datetime.fromisoformat(journal["opened_at"]).replace(tzinfo=_IST)
                held_days = (datetime.now(_IST) - opened).days
            except Exception:
                pass

        outcome = "WIN" if net_pnl > 0 else "LOSS"
        hit = ""
        if journal:
            if journal.get("target_price") and exit_price >= journal["target_price"] * 0.997:
                hit = " Hit target."
            elif journal.get("stop_price") and exit_price <= journal["stop_price"] * 1.003:
                hit = " Hit stop."

        thesis = (journal.get("key_thesis", "") if journal else "")[:120]
        lesson = (
            f"{outcome} {symbol}: net ₹{net_pnl:.0f} ({pct:+.2f}%) after {held_days} days.{hit} "
            f"Thesis: {thesis}. Exit: {exit_reason[:120]}"
        )
        db.insert("lessons", {
            "session_id": session_id,
            "symbol": symbol,
            "category": "trade_outcome",
            "lesson": lesson,
            "pnl": round(net_pnl, 2),
            "created_at": db.now_iso(),
        })
    except Exception as e:
        logger.warning(f"record_trade_lesson failed for {symbol}: {e}")


def record_prediction_result(session_id: int | None, predicted_bias: str,
                             actual_change_pct: float):
    """Score a weekend-outlook open-bias prediction against reality."""
    actual = ("GAP_UP" if actual_change_pct > 0.25
              else "GAP_DOWN" if actual_change_pct < -0.25 else "FLAT")
    hit = predicted_bias == actual
    lesson = (
        f"Outlook predicted {predicted_bias}, actual Nifty {actual_change_pct:+.2f}% ({actual}) "
        f"— {'HIT' if hit else 'MISS'}"
    )
    db.insert("lessons", {
        "session_id": session_id,
        "symbol": None,
        "category": "prediction",
        "lesson": lesson,
        "pnl": None,
        "created_at": db.now_iso(),
    })
    logger.info(f"Prediction scored: {lesson}")


def performance_stats(session_id: int) -> str:
    """Deterministic one-line performance summary from the trades table."""
    try:
        rows = db.query(
            "SELECT pnl, charges FROM trades WHERE session_id = ? AND action = 'SELL' AND pnl IS NOT NULL",
            (session_id,),
        )
        if not rows:
            return "No closed trades yet."
        wins = [r["pnl"] for r in rows if r["pnl"] > 0]
        losses = [r["pnl"] for r in rows if r["pnl"] <= 0]
        total_charges = sum(r["charges"] or 0 for r in db.query(
            "SELECT charges FROM trades WHERE session_id = ?", (session_id,),
        ))
        n = len(rows)
        win_rate = len(wins) / n * 100
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        return (
            f"{n} closed | win rate {win_rate:.0f}% | avg win ₹{avg_win:.0f} / avg loss ₹{avg_loss:.0f} | "
            f"total charges paid ₹{total_charges:.0f}"
        )
    except Exception as e:
        logger.warning(f"performance_stats failed: {e}")
        return "Stats unavailable."


def recent_lessons_block(session_id: int, limit: int = 5) -> str:
    """Briefing-ready block: performance stats + the most recent lessons.

    Trade lessons are session-scoped (this capital pot); insight lessons are
    global (knowledge transfers across sessions).
    """
    lines = [f"Performance: {performance_stats(session_id)}"]
    try:
        from aaitrade.costs import expense_summary
        e = expense_summary(session_id)
        lines.append(
            f"Running costs: API ₹{e['api_cost']:,.0f} + subscriptions ₹{e['subscriptions']:,.0f} "
            f"→ NET after everything: ₹{e['net_profit_after_all_expenses']:,.0f} "
            f"(this is the number that matters)"
        )
    except Exception:
        pass
    try:
        rows = db.query(
            "SELECT lesson FROM lessons "
            "WHERE (session_id = ? OR category = 'insight') "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
        for r in rows:
            lines.append(f"• {r['lesson']}")
    except Exception:
        pass
    return "\n".join(lines)


def prediction_calibration(limit: int = 5) -> str:
    """For the research prompt: how accurate were recent outlook calls?"""
    try:
        rows = db.query(
            "SELECT lesson FROM lessons WHERE category = 'prediction' ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        if not rows:
            return "No prior predictions scored yet."
        hits = sum(1 for r in rows if "HIT" in r["lesson"])
        detail = "; ".join(r["lesson"] for r in rows[:3])
        return f"Your last {len(rows)} open-bias calls: {hits} hit(s). Recent: {detail}"
    except Exception:
        return "Calibration unavailable."
