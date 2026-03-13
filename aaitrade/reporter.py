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

        # Store daily summary
        db.insert("daily_summary", {
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
        })

        logger.info(f"\n{summary_text}")
        return summary_text

    def generate_session_report(self):
        """Generate final session report."""
        session = db.query_one(
            "SELECT * FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if not session:
            return

        # All trades
        all_sells = db.query(
            "SELECT * FROM trades WHERE session_id = ? AND action = 'SELL'",
            (self.session_id,),
        )

        total_trades = len(all_sells)
        wins = sum(1 for t in all_sells if t["pnl"] and t["pnl"] > 0)
        losses = sum(1 for t in all_sells if t["pnl"] and t["pnl"] < 0)
        total_pnl = session["current_capital"] + session["secured_profit"] - session["starting_capital"]

        # Best and worst trades
        if all_sells:
            best = max(all_sells, key=lambda t: t["pnl"] or 0)
            worst = min(all_sells, key=lambda t: t["pnl"] or 0)
        else:
            best = worst = None

        # Daily summaries
        daily = db.query(
            "SELECT * FROM daily_summary WHERE session_id = ? ORDER BY day_number",
            (self.session_id,),
        )

        report = [
            "=" * 60,
            "AAItrade SESSION REPORT",
            "=" * 60,
            f"Session ID: {self.session_id}",
            f"Mode: {session['execution_mode'].upper()} + {session['trading_mode'].upper()}",
            f"Duration: {session['current_day'] - 1} days",
            f"Started: {session['started_at']}",
            f"Ended: {session['ended_at'] or 'N/A'}",
            f"Status: {session['status']}",
            "",
            "PERFORMANCE",
            "-" * 40,
            f"Starting capital: ₹{session['starting_capital']:,.2f}",
            f"Final capital: ₹{session['current_capital']:,.2f}",
            f"Secured profit: ₹{session['secured_profit']:,.2f}",
            f"Total P&L: ₹{total_pnl:,.2f} ({total_pnl / session['starting_capital'] * 100:+.2f}%)",
            "",
            f"Total closed trades: {total_trades}",
            f"Wins: {wins} | Losses: {losses}",
            f"Win rate: {wins / total_trades * 100:.1f}%" if total_trades > 0 else "Win rate: N/A",
        ]

        if best:
            report.append(f"\nBest trade: {best['symbol']} | P&L: ₹{best['pnl']:,.2f}")
        if worst:
            report.append(f"Worst trade: {worst['symbol']} | P&L: ₹{worst['pnl']:,.2f}")

        if daily:
            report.append(f"\nDAILY BREAKDOWN")
            report.append("-" * 40)
            for d in daily:
                report.append(
                    f"Day {d['day_number']} ({d['date']}): "
                    f"P&L ₹{d['total_pnl']:,.2f} | "
                    f"{d['trades_made']} trades | "
                    f"{d['wins']}W/{d['losses']}L"
                )

        report.append("=" * 60)

        report_text = "\n".join(report)
        logger.info(f"\n{report_text}")
        return report_text
