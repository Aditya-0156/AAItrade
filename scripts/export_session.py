#!/usr/bin/env python3
"""Export a full human-readable timeline of an AAItrade session.

Reads directly from the SQLite DB — no aaitrade package imports needed.
Use this after a paper run to review every decision, tool call, trade,
and capital change in chronological order.

Usage:
    python scripts/export_session.py --last
    python scripts/export_session.py --session-id 3
    python scripts/export_session.py --last --output session_3.txt
    python scripts/export_session.py --list
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "aaitrade.db"


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        print("Run the app first to create the database.", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def list_sessions(conn: sqlite3.Connection):
    rows = conn.execute(
        "SELECT id, name, execution_mode, trading_mode, starting_capital, "
        "current_capital, secured_profit, status, started_at, ended_at, "
        "current_day, total_days FROM sessions ORDER BY id DESC"
    ).fetchall()
    if not rows:
        print("No sessions found.")
        return
    print(f"{'ID':<5} {'Name':<20} {'Mode':<20} {'Status':<12} {'Started':<20} {'P&L':>12}")
    print("-" * 95)
    for r in rows:
        net_pnl = r["current_capital"] + r["secured_profit"] - r["starting_capital"]
        name = (r["name"] or "(unnamed)")[:20]
        mode = f"{r['execution_mode']}/{r['trading_mode']}"
        started = (r["started_at"] or "")[:16]
        print(
            f"{r['id']:<5} {name:<20} {mode:<20} {r['status']:<12} "
            f"{started:<20} {net_pnl:>+12,.2f}"
        )


def get_last_session_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def export_session(session_id: int, output_file: str | None = None):
    conn = connect()

    session = conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        print(f"ERROR: session {session_id} not found", file=sys.stderr)
        sys.exit(1)
    session = dict(session)

    lines: list[str] = []

    def emit(line: str = ""):
        lines.append(line)

    # ── Header ────────────────────────────────────────────────────────────────
    net_pnl = session["current_capital"] + session["secured_profit"] - session["starting_capital"]
    net_pct = net_pnl / session["starting_capital"] * 100

    emit("=" * 72)
    emit("  AAITRADE SESSION EXPORT")
    emit("=" * 72)
    emit(f"  Session ID   : {session['id']}")
    emit(f"  Name         : {session['name'] or '(unnamed)'}")
    emit(f"  Mode         : {session['execution_mode'].upper()} | {session['trading_mode'].upper()}")
    emit(f"  Status       : {session['status'].upper()}")
    emit(f"  Duration     : Day 1 → Day {session['current_day'] - 1} of {session['total_days']}")
    emit(f"  Started      : {session['started_at']}")
    emit(f"  Ended        : {session['ended_at'] or 'still running'}")
    emit()
    emit(f"  Starting cap : ₹{session['starting_capital']:>12,.2f}")
    emit(f"  Free cash    : ₹{session['current_capital']:>12,.2f}")
    emit(f"  Secured      : ₹{session['secured_profit']:>12,.2f}")
    emit(f"  Total value  : ₹{session['current_capital'] + session['secured_profit']:>12,.2f}")
    emit(f"  Net P&L      : ₹{net_pnl:>+12,.2f}  ({net_pct:+.2f}%)")
    emit("=" * 72)

    # ── Build unified event list ───────────────────────────────────────────────
    events: list[tuple[str, str, dict]] = []

    for row in conn.execute(
        "SELECT * FROM decisions WHERE session_id = ? ORDER BY decided_at",
        (session_id,),
    ):
        events.append((row["decided_at"] or "", "DECISION", dict(row)))

    for row in conn.execute(
        "SELECT * FROM tool_calls WHERE session_id = ? ORDER BY called_at",
        (session_id,),
    ):
        events.append((row["called_at"] or "", "TOOL", dict(row)))

    for row in conn.execute(
        "SELECT * FROM trades WHERE session_id = ? ORDER BY executed_at",
        (session_id,),
    ):
        events.append((row["executed_at"] or "", "TRADE", dict(row)))

    for row in conn.execute(
        "SELECT * FROM trade_journal WHERE session_id = ? ORDER BY opened_at",
        (session_id,),
    ):
        events.append((row["opened_at"] or "", "JOURNAL_OPEN", dict(row)))

    events.sort(key=lambda x: x[0])

    # ── Chronological timeline ─────────────────────────────────────────────────
    emit()
    emit("CHRONOLOGICAL TIMELINE")
    emit("─" * 72)

    current_date: str | None = None
    current_cycle: int | None = None

    for ts, event_type, data in events:
        date_part = ts[:10] if ts else "unknown"
        time_part = ts[11:16] if len(ts) >= 16 else "??:??"

        if date_part != current_date:
            current_date = date_part
            emit()
            emit("┌" + "─" * 70 + "┐")
            emit(f"│  DATE: {date_part:<62}│")
            emit("└" + "─" * 70 + "┘")

        if event_type == "DECISION":
            cycle = data.get("cycle_number")
            if cycle != current_cycle:
                current_cycle = cycle
                emit()
                emit(f"  ▶ Cycle {cycle}")

            action = data.get("action", "?")
            symbol = (data.get("symbol") or "").ljust(14)
            confidence = data.get("confidence") or ""
            reason = (data.get("reason") or "")[:100]
            flags = data.get("flags") or "[]"
            emit(f"  {time_part}  DECISION  {action:<5} {symbol} [{confidence}]")
            if reason:
                emit(f"             Reason: {reason}")
            if flags and flags not in ("[]", "", "null"):
                emit(f"             Flags:  {flags}")

        elif event_type == "TOOL":
            tool_name = data.get("tool_name", "?")
            params = (data.get("parameters") or "")[:80]
            result = (data.get("result_summary") or "")[:100]
            emit(f"  {time_part}  TOOL      {tool_name}")
            if params and params not in ("{}", "null", ""):
                emit(f"             Params: {params}")
            if result:
                emit(f"             Result: {result}")

        elif event_type == "TRADE":
            action = data.get("action", "?")
            symbol = data.get("symbol", "?")
            qty = data.get("quantity", 0)
            price = data.get("price", 0)
            pnl = data.get("pnl")
            pnl_str = f"  P&L ₹{pnl:+,.2f}" if pnl is not None else ""
            reason = (data.get("reason") or "")[:80]
            emit(f"  {time_part}  TRADE     {action:<4} {symbol:<14} x{qty:<5} @ ₹{price:>9,.2f}{pnl_str}")
            if reason:
                emit(f"             Reason: {reason}")
            if action == "BUY":
                emit(f"             → ₹{price * qty:,.2f} deployed from free cash")
            elif pnl is not None:
                outcome = "PROFIT" if pnl >= 0 else "LOSS"
                emit(f"             → {outcome}: ₹{abs(pnl):,.2f} {'secured/reinvested' if pnl >= 0 else 'lost'}")

        elif event_type == "JOURNAL_OPEN":
            symbol = data.get("symbol", "?")
            entry_price = data.get("entry_price", 0)
            thesis = (data.get("key_thesis") or "")[:100]
            status = data.get("status", "?")
            exit_price = data.get("exit_price")
            pnl = data.get("pnl")
            outcome_str = ""
            if pnl is not None:
                outcome_str = f"  → {'WIN' if pnl >= 0 else 'LOSS'} ₹{pnl:+,.2f}"
            emit(f"  {time_part}  JOURNAL   {symbol} entered @ ₹{entry_price:.2f}  [{status.upper()}]{outcome_str}")
            if thesis:
                emit(f"             Thesis: {thesis}")
            if exit_price:
                emit(f"             Exit @ ₹{exit_price:.2f}  Reason: {(data.get('exit_reason') or '')[:60]}")

    # ── Daily summaries ────────────────────────────────────────────────────────
    daily_rows = conn.execute(
        "SELECT * FROM daily_summary WHERE session_id = ? ORDER BY day_number",
        (session_id,),
    ).fetchall()

    if daily_rows:
        emit()
        emit()
        emit("DAILY PERFORMANCE")
        emit("─" * 72)
        emit(f"  {'Day':<5} {'Date':<12} {'Trades':<8} {'W/L':<8} {'Day P&L':>12} {'Capital':>14}")
        emit("  " + "─" * 60)
        for d in daily_rows:
            d = dict(d)
            pnl_sign = "+" if d["total_pnl"] >= 0 else ""
            emit(
                f"  {d['day_number']:<5} {d['date']:<12} {d['trades_made']:<8} "
                f"{d['wins']}W/{d['losses']}L   "
                f"{pnl_sign}₹{d['total_pnl']:>10,.2f}  ₹{d['ending_capital']:>12,.2f}"
            )

    # ── Trade statistics ───────────────────────────────────────────────────────
    all_trades = conn.execute(
        "SELECT * FROM trades WHERE session_id = ? ORDER BY executed_at",
        (session_id,),
    ).fetchall()
    sells = [dict(r) for r in all_trades if r["action"] == "SELL"]

    if sells:
        wins_list = [t for t in sells if (t["pnl"] or 0) > 0]
        losses_list = [t for t in sells if (t["pnl"] or 0) < 0]
        gross_profit = sum(t["pnl"] for t in wins_list)
        gross_loss = abs(sum(t["pnl"] for t in losses_list))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        win_rate = len(wins_list) / len(sells) * 100 if sells else 0

        emit()
        emit()
        emit("TRADE STATISTICS")
        emit("─" * 72)
        emit(f"  Closed trades : {len(sells)}")
        emit(f"  Win rate      : {win_rate:.1f}%  ({len(wins_list)}W / {len(losses_list)}L)")
        emit(f"  Avg win       : ₹{gross_profit / len(wins_list):,.2f}" if wins_list else "  Avg win       : —")
        emit(f"  Avg loss      : ₹{gross_loss / len(losses_list):,.2f}" if losses_list else "  Avg loss      : —")
        emit(f"  Profit factor : {profit_factor:.2f}x")
        emit(f"  Gross profit  : ₹{gross_profit:,.2f}")
        emit(f"  Gross loss    : ₹{gross_loss:,.2f}")

        if wins_list:
            best = max(sells, key=lambda t: t["pnl"] or 0)
            worst = min(sells, key=lambda t: t["pnl"] or 0)
            emit(f"  Best trade    : {best['symbol']} ₹{best['pnl']:+,.2f} on {best['executed_at'][:10]}")
            emit(f"  Worst trade   : {worst['symbol']} ₹{worst['pnl']:+,.2f} on {worst['executed_at'][:10]}")

    # ── Watchlist changes ──────────────────────────────────────────────────────
    watchlist_rows = conn.execute(
        "SELECT * FROM watchlist WHERE session_id = ? ORDER BY added_at",
        (session_id,),
    ).fetchall()

    if watchlist_rows:
        emit()
        emit()
        emit("WATCHLIST CHANGES")
        emit("─" * 72)
        for w in watchlist_rows:
            w = dict(w)
            added = (w["added_at"] or "")[:16]
            removed = (w["removed_at"] or "")[:16]
            tag = "[REMOVED]" if w["removed_at"] else "[ACTIVE] "
            emit(f"  {tag} {w['symbol']:<14} added {added}" + (f"  removed {removed}" if removed else ""))
            if w.get("add_reason") and w["add_reason"] != "Seed watchlist":
                emit(f"             Add reason: {w['add_reason'][:80]}")
            if w.get("remove_reason"):
                emit(f"             Remove reason: {w['remove_reason'][:80]}")

    # ── Session memory ─────────────────────────────────────────────────────────
    mem = conn.execute(
        "SELECT content, updated_at, cycle_number FROM session_memory WHERE session_id = ?",
        (session_id,),
    ).fetchone()

    if mem:
        emit()
        emit()
        emit("FINAL SESSION MEMORY (Claude's last notes)")
        emit("─" * 72)
        emit(f"  Last updated: {mem['updated_at'][:16]}  (after cycle {mem['cycle_number']})")
        emit()
        for line in (mem["content"] or "").splitlines():
            emit(f"  {line}")

    emit()
    emit("=" * 72)
    emit("  END OF EXPORT")
    emit("=" * 72)

    output = "\n".join(lines)

    if output_file:
        Path(output_file).write_text(output, encoding="utf-8")
        print(f"Exported session {session_id} → {output_file}")
    else:
        print(output)

    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Export a full AAItrade session timeline for post-run analysis."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session-id", type=int, help="Session ID to export")
    group.add_argument("--last", action="store_true", help="Export the most recent session")
    group.add_argument("--list", action="store_true", help="List all sessions")
    parser.add_argument("--output", "-o", metavar="FILE", help="Write to file instead of stdout")
    args = parser.parse_args()

    conn = connect()

    if args.list:
        list_sessions(conn)
        conn.close()
        return

    if args.last:
        session_id = get_last_session_id(conn)
        conn.close()
        if not session_id:
            print("ERROR: No sessions in database.", file=sys.stderr)
            sys.exit(1)
    else:
        conn.close()
        session_id = args.session_id

    export_session(session_id, output_file=args.output)


if __name__ == "__main__":
    main()
