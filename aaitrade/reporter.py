"""Reporter — end-of-day summaries and session reports.

Generates daily performance summaries using Claude and stores them.
Also produces final session reports.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aaitrade import db
from aaitrade.config import SessionConfig

logger = logging.getLogger(__name__)


class Reporter:
    """Generates trading reports and summaries."""

    def __init__(self, config: SessionConfig, session_id: int, claude_client=None):
        self.config = config
        self.session_id = session_id
        self.claude = claude_client

    def generate_daily_summary(self):
        """Generate and store end-of-day summary."""
        session = db.query_one(
            "SELECT * FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if not session:
            return

        today = db.now_iso()[:10]
        day_number = session["current_day"]

        # Today's trades
        trades = db.query(
            "SELECT * FROM trades WHERE session_id = ? AND executed_at LIKE ? ORDER BY executed_at",
            (self.session_id, f"{today}%"),
        )

        sells = [t for t in trades if t["action"] == "SELL"]
        buys = [t for t in trades if t["action"] == "BUY"]
        wins = sum(1 for t in sells if t["pnl"] and t["pnl"] > 0)
        losses = sum(1 for t in sells if t["pnl"] and t["pnl"] < 0)
        total_pnl = sum(t["pnl"] for t in sells if t["pnl"]) if sells else 0

        # Build summary text
        summary_lines = [
            f"Day {day_number} Summary — {today}",
            f"{'=' * 40}",
            f"Mode: {session['trading_mode'].upper()}",
            f"",
            f"Trades today: {len(trades)} ({len(buys)} buys, {len(sells)} sells)",
            f"Wins: {wins} | Losses: {losses}",
            f"Day P&L: ₹{total_pnl:,.2f}",
            f"",
            f"Capital: ₹{session['current_capital']:,.2f}",
            f"Secured profit: ₹{session['secured_profit']:,.2f}",
            f"Total P&L: ₹{session['current_capital'] + session['secured_profit'] - session['starting_capital']:,.2f}",
        ]

        if trades:
            summary_lines.append(f"\nTrades:")
            for t in trades:
                pnl_str = f" | P&L: ₹{t['pnl']:,.2f}" if t["pnl"] else ""
                summary_lines.append(
                    f"  {t['action']} {t['symbol']} x{t['quantity']} @ ₹{t['price']:,.2f}{pnl_str}"
                )
                if t["reason"]:
                    summary_lines.append(f"    Reason: {t['reason']}")

        summary_text = "\n".join(summary_lines)

        # Store daily summary — upsert so double-calling on the same day is safe
        db.upsert("daily_summary", {
            "session_id": self.session_id,
            "day_number": day_number,
            "date": today,
            "starting_capital": session["starting_capital"],
            "ending_capital": session["current_capital"],
            "secured_profit": session["secured_profit"],
            "trades_made": len(trades),
            "wins": wins,
            "losses": losses,
            "total_pnl": round(total_pnl, 2),
            "summary_text": summary_text,
        }, conflict_columns=["session_id", "day_number"])

        logger.info(f"\n{summary_text}")
        return summary_text

    def generate_session_report(self):
        """Generate final session report with full trade history and statistics."""
        session = db.query_one(
            "SELECT * FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if not session:
            return

        # All trades (buys + sells)
        all_trades = db.query(
            "SELECT * FROM trades WHERE session_id = ? ORDER BY executed_at",
            (self.session_id,),
        )
        all_sells = [t for t in all_trades if t["action"] == "SELL"]

        total_trades = len(all_sells)
        wins = sum(1 for t in all_sells if t["pnl"] and t["pnl"] > 0)
        losses = sum(1 for t in all_sells if t["pnl"] and t["pnl"] < 0)
        total_pnl = session["current_capital"] + session["secured_profit"] - session["starting_capital"]
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0

        # Profit factor
        gross_profit = sum(t["pnl"] for t in all_sells if t["pnl"] and t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in all_sells if t["pnl"] and t["pnl"] < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Best and worst trades
        if all_sells:
            best = max(all_sells, key=lambda t: t["pnl"] or 0)
            worst = min(all_sells, key=lambda t: t["pnl"] or 0)
            avg_win = gross_profit / wins if wins > 0 else 0
            avg_loss = gross_loss / losses if losses > 0 else 0
        else:
            best = worst = None
            avg_win = avg_loss = 0

        # Daily summaries
        daily = db.query(
            "SELECT * FROM daily_summary WHERE session_id = ? ORDER BY day_number",
            (self.session_id,),
        )

        # Closed journal entries for rationale
        journal_entries = db.query(
            "SELECT * FROM trade_journal WHERE session_id = ? ORDER BY opened_at",
            (self.session_id,),
        )

        report = [
            "=" * 60,
            "AAItrade SESSION REPORT",
            "=" * 60,
            f"Session:  {session['name'] or self.session_id}",
            f"Mode:     {session['execution_mode'].upper()} | {session['trading_mode'].upper()}",
            f"Duration: Day 1 → Day {session['current_day'] - 1} of {session['total_days']}",
            f"Started:  {session['started_at']}",
            f"Ended:    {session['ended_at'] or 'ongoing'}",
            f"Status:   {session['status'].upper()}",
            "",
            "CAPITAL SUMMARY",
            "-" * 40,
            f"Starting capital:  ₹{session['starting_capital']:>10,.2f}",
            f"Final capital:     ₹{session['current_capital']:>10,.2f}",
            f"Secured profit:    ₹{session['secured_profit']:>10,.2f}",
            f"Total value:       ₹{session['current_capital'] + session['secured_profit']:>10,.2f}",
            f"Net P&L:           ₹{total_pnl:>+10,.2f}  ({total_pnl / session['starting_capital'] * 100:+.2f}%)",
            "",
            "TRADE STATISTICS",
            "-" * 40,
            f"Closed trades:  {total_trades}",
            f"Wins:           {wins}  |  Losses: {losses}",
            f"Win rate:       {win_rate:.1f}%",
            f"Avg win:        ₹{avg_win:,.2f}",
            f"Avg loss:       ₹{avg_loss:,.2f}",
            f"Profit factor:  {profit_factor:.2f}x  (gross profit / gross loss)",
            f"Gross profit:   ₹{gross_profit:,.2f}",
            f"Gross loss:     ₹{gross_loss:,.2f}",
        ]

        if best:
            report += [
                "",
                f"Best trade:   {best['symbol']} | P&L ₹{best['pnl']:,.2f} | {best['executed_at'][:10]}",
                f"Worst trade:  {worst['symbol']} | P&L ₹{worst['pnl']:,.2f} | {worst['executed_at'][:10]}",
            ]

        # Daily breakdown
        if daily:
            report += ["", "DAILY BREAKDOWN", "-" * 40]
            for d in daily:
                pnl_sign = "+" if d["total_pnl"] >= 0 else ""
                report.append(
                    f"Day {d['day_number']:>2} ({d['date']}): "
                    f"P&L {pnl_sign}₹{d['total_pnl']:,.2f} | "
                    f"{d['trades_made']} trades | "
                    f"{d['wins']}W / {d['losses']}L | "
                    f"Capital ₹{d['ending_capital']:,.2f}"
                )

        # Full trade history
        if all_trades:
            report += ["", "FULL TRADE HISTORY", "-" * 40]
            for t in all_trades:
                pnl_str = f" | P&L ₹{t['pnl']:+,.2f}" if t["pnl"] is not None else ""
                report.append(
                    f"{t['executed_at'][:16]}  {t['action']:<4} {t['symbol']:<12} "
                    f"x{t['quantity']:<4} @ ₹{t['price']:>8,.2f}{pnl_str}"
                )
                if t["reason"]:
                    report.append(f"  Reason: {t['reason'][:80]}")

        # Trade journal — rationale and outcomes
        if journal_entries:
            report += ["", "TRADE JOURNAL (RATIONALE & THESIS)", "-" * 40]
            for j in journal_entries:
                outcome = "✓ WIN" if j["pnl"] and j["pnl"] > 0 else ("✗ LOSS" if j["pnl"] and j["pnl"] < 0 else "OPEN")
                report.append(
                    f"\n[{outcome}] {j['symbol']} | Entry ₹{j['entry_price']:,.2f} → "
                    f"Exit ₹{j['exit_price']:,.2f}" if j.get('exit_price') else
                    f"\n[{outcome}] {j['symbol']} | Entry ₹{j['entry_price']:,.2f}"
                )
                report.append(f"  Thesis: {j['key_thesis']}")
                if j.get("exit_reason"):
                    report.append(f"  Exit reason: {j['exit_reason'][:80]}")
                if j["pnl"] is not None:
                    report.append(f"  P&L: ₹{j['pnl']:+,.2f}")

        report.append("\n" + "=" * 60)

        report_text = "\n".join(report)
        logger.info(f"\n{report_text}")
        return report_text
