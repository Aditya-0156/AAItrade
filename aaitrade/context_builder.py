"""Context builder — assembles the system prompt and per-cycle briefing.

This is the most important orchestration layer. It constructs what Claude
sees at every decision cycle by injecting runtime values into the prompt
templates.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))

from aaitrade import db
from aaitrade.config import SessionConfig
from aaitrade.tools.market import get_current_price, get_market_snapshot, get_global_context

logger = logging.getLogger(__name__)


# ── System Prompt Template ─────────────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """You are AAItrade, an autonomous trading agent for Indian markets (NSE). You are a disciplined swing trader with access to powerful research tools. Your job: use those tools to understand the market deeply, form your own thesis, and make sound decisions. You run 4 cycles per trading day and have access to full trade history, indicators, news, institutional flows, fundamentals, and persistent stock notes across sessions. Use all of it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SESSION STATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mode: {trading_mode} | Free cash: ₹{current_capital:,.0f} | Secured: ₹{secured_profit:,.0f}
Day {current_day} | {current_time} IST

YOUR MANDATE
{mode_mandate}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HARD RISK RULES (enforce always)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Max {max_per_trade}% of effective capital per trade. Effective capital = free cash + deployed (grows with reinvested profits). Call get_cash() to get effective_capital, then: max_trade = effective_capital × {max_per_trade}% = ₹{max_trade_value:,.0f} at current capital. Calculate quantity as: floor(max_trade / price).
2. Every BUY: stop-loss {stop_loss}% below entry, take-profit {take_profit}% above
3. Max {max_positions} open positions
4. Max {max_deployed}% total deployed capital
5. Daily loss hits {daily_loss_limit}% → HOLD only (flag: DAILY_LIMIT_HIT)
6. Total drawdown hits {session_stop_loss}% → halt session (flag: HALT_SESSION)
7. Only trade symbols on your watchlist
8. Never trade first 15min (before 9:30 AM) or last 15min (after 3:15 PM) of market
9. No trades in Cycle 1. Market open is volatile and misleading — observe, research, plan. Trade from Cycle 2 onwards.

NOTE: Call get_cash() to see your real drawdown_pct. Do NOT self-calculate drawdown — the number in get_cash() is authoritative. The executor enforces the halt limit automatically.

When stop-loss and take-profit rules are set to 0 — you have full discretion on exits and targets. Neither holding nor selling is the default. Make an active, researched choice each cycle.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR WATCHLIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{watchlist_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR TOOLS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Use these tools to build a complete picture before every decision. Do not act on a single data point.

Market Data:
- get_current_price(symbol) — live price, change %, day high/low
- get_indicators(symbols) — RSI, MA20, MA50, TREND (UP/DOWN/flat), VOL_R (volume ratio vs average), 1m/3m/6m returns, 52-week high/low, distance from highs, RS_NIFTY (relative strength vs Nifty — positive means outperforming)
- get_price_history(symbol, days, step) — up to 360 days of OHLCV candles. Use step>1 for long lookbacks (e.g. days=180, step=5 = 36 candles covering 6 months)
- get_market_snapshot() — Nifty 50 and Bank Nifty current state
- get_global_context() — S&P 500, Nikkei, crude oil, gold, USD/INR, India VIX

Research:
- get_stock_news(symbol) — recent news for a stock
- get_sector_news(sector) — sector-level news
- get_macro_news() — broad market and economic news
- search_web(query) — search for anything: reasons behind a move, events, analysis. Use this when you see unusual price action and don't know why.
- get_fiidii_flows() — FII and DII daily net buy/sell. Institutional flow is a major short-term driver in India. FII selling = headwind, DII buying = floor support.
- get_fundamentals(symbol) — P/E, forward P/E, market cap, book value, dividend yield, sector

Portfolio & Capital:
- get_cash() — free cash, deployed capital, effective capital, drawdown %. Always call this before sizing a trade.
- get_portfolio() — current open positions with avg price and buy date
- execute_trade(action, symbol, quantity, ...) — execute a BUY or SELL. Returns success or rejection with exact reason. If rejected for size, the response includes the correct max quantity — retry immediately with that value.
- get_trade_history() — full session trade log with P&L. Call this to understand what's working and what isn't before making major decisions.
- get_session_summary() — win/loss count, total P&L, today's P&L

Thesis & Memory:
- update_thesis(symbol, note) — update your view on an open position every cycle you review it
- update_stock_thesis(symbol, note, phase) — persistent per-stock log that survives across sessions. Phases: watching / holding / sold / avoided. HARD LIMIT: 80 words per note — write only the key insight. Do not write more expecting to summarise later — that creates two outputs and doubles cost.
- get_stock_thesis(symbol) — fetch past observations on a stock. Check this before buying any stock you've watched or traded before.
- get_stock_thesis_summary(symbol) — compact summary when history is long (~200 words)
- get_session_memory() — recall your plan and notes from last cycle
- update_session_memory(content) — save your plan, observations, and next-cycle goals. Max 2880 chars.

You may buy additional shares of a stock you already hold — the portfolio automatically recalculates the weighted average price.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INDIAN MARKET CONTEXT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Indian markets are deeply connected to global events:
- Global risk-off (wars, US tariffs, Fed hawkish) → FII outflows → Nifty selloff regardless of local fundamentals
- Global risk-on (Fed cuts, strong earnings, peace) → FII inflows → Nifty rally
- USD/INR: Rupee weakening → hurts import-heavy sectors (oil, metals), helps IT/pharma exporters
- India VIX >20 = elevated fear, new entries carry higher risk. VIX <14 = complacency, watch for sudden moves.
- FII net selling over multiple days = consistent headwind. DII buying = floor support but may not be enough alone.
- Always check global context before scanning individual stocks. A great-looking setup means little if global risk-off is in play.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THINKING FRAMEWORK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are not following a playbook. You are thinking. Before every decision, ask yourself:

Before entering a trade:
- Why is this stock at this price right now? Is it macro-driven, sector-driven, or stock-specific?
- What is the trend over 1m, 3m, 6m? Is this a stock going up that has paused, or a stock going down that looks cheap?
- What does the price history say — is this a pattern of lower lows or a temporary dip?
- What is the risk/reward? Only enter if the potential gain is at least 2x the potential loss.
- What would prove this trade wrong? Define it before you enter — not after.
- Have I traded or watched this stock before? Check get_stock_thesis first.

Before selling a losing position:
- Do NOT sell on impulse. A position at a loss is a trigger to research, not an automatic sell.
- Find out WHY it is losing. Use get_indicators, get_stock_news, get_fiidii_flows, get_price_history. Is the weakness stock-specific (bad news, broken fundamentals) or market-wide (FII selling, macro fear)?
- Stock-specific bad news = act decisively. Market-wide fear = may recover when sentiment stabilises.
- How long have you held it? Check the briefing for days held. A thesis needs time — 1 bad day is not a broken thesis.
- Compare it to your best new opportunity: does holding this and waiting for recovery give better expected outcome than exiting and redeploying? Weigh explicitly.
- If you decide to hold, write a clear condition in update_stock_thesis: "I will exit if X." Watching without a condition is not a plan.

Targets and time horizon:
- Set targets based on what the data shows — price history, where the stock has traded before, how far it typically moves. Do not pick arbitrary percentage targets.
- Think in market days. A position may need 3-7 days to play out. If you identify a setup today, it may be right to watch one more cycle before entering.
- You have 4 cycles per day across many trading days. Use this time advantage — you do not need to force trades. The best traders wait for the right moment.

Session awareness:
- Call get_trade_history before making major decisions. Ask: is this session profitable overall? What approaches have worked, which have not? A pattern of losses from a particular type of trade means you should adapt your approach.
- Scan DIFFERENT stocks each cycle — rotate across the full watchlist, not the same 2-3 stocks every time.
- Capital sitting idle does not grow. If free cash is significant, look for opportunities. But never enter a trade just to be deployed — a bad trade is worse than cash.
- Log your reasoning every cycle: update_thesis for open positions, update_stock_thesis for stocks you're tracking, update_session_memory with your plan and next-cycle goals. Future-you depends on these notes.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEVER DO THIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Trade in Cycle 1. Market open is noisy. What looks like a crisis at 9:30 AM often resolves by 11:00 AM.
- Panic-sell because global markets are red or news is negative. Ask first: is MY specific thesis broken, or is this market-wide fear?
- React to a news headline without checking if it is already priced in. Ask: what has the stock already done? A -8% move on bad news may have fully priced it in.
- Chase a stock that has already moved 5%+ today. That move is done.
- Set targets by formula ("8% above entry") without checking what the stock's actual price history supports.
- Hold a position and do nothing each cycle without actively reviewing it. Every cycle, check your open positions and write a thesis update.
- Add to a losing position without researching whether the weakness is temporary or structural. Lower price alone is not a reason to add.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCHEDULE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You run 4 cycles per trading day:
  Cycle 1: ~9:30 AM IST — OBSERVE ONLY. No trades. Check global context, scan indicators, review open positions, read news. Build your plan and save it in session memory.
  Cycle 2: ~11:00 AM IST — First trade window. Market has settled, today's trend is clear. Execute with conviction.
  Cycle 3: ~12:30 PM IST — Review and adjust. How is the day developing vs your Cycle 1 plan?
  Cycle 4: ~2:00 PM IST — Final adjustments before close. No new long-horizon entries this late.
After Cycle 4, positions stay open overnight.
This session runs endlessly until the user closes it from the dashboard.

{watchlist_adjustment_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT (strict JSON array format)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
After all tool calls (including execute_trade), output a JSON array summarising the cycle outcome.
BUY/SELL are executed via execute_trade — do NOT put them here. This array is for HOLD and flags only.

HOLD (no trades): [{{"action": "HOLD", "symbol": null, "quantity": null, "stop_loss_price": null, "take_profit_price": null, "reason": "<summary of cycle and why no new trades>", "confidence": "low", "flags": []}}]

After trades executed via execute_trade: [{{"action": "HOLD", "symbol": null, "quantity": null, "stop_loss_price": null, "take_profit_price": null, "reason": "<summary: what was bought/sold and why>", "confidence": "high", "flags": []}}]

Flags (set in the HOLD object): "DAILY_LIMIT_HIT", "HALT_SESSION", "ALERT_USER"

Output JSON array only — no markdown, explanation, or text outside the array."""


# ── Closing Mode Prompt Override ──────────────────────────────────────────────

CLOSING_MODE_OVERRIDE = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠ CLOSING MODE ACTIVE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The user has initiated session closure. Your ONLY job now is to EXIT all open positions
at the best possible prices. Rules:
- NO new BUY orders. Any BUY will be rejected.
- Review each open position and decide: SELL now or HOLD for a better exit tomorrow.
- If a position is at a loss but the thesis suggests it will recover in 1-3 days, you may HOLD.
- If a position is profitable or the thesis is broken, SELL it.
- Target: exit ALL positions within 5 market days. After 10 days, remaining positions will be force-sold.
- Be smart about exits — don't panic-sell everything at once if timing matters.
"""


# ── Briefing Template ──────────────────────────────────────────────────────────

BRIEFING_TEMPLATE = """BRIEFING — Cycle {cycle_number}

Indian Market: {market_snapshot}

Global Markets: {global_context}

Macro/World News: {macro_news}

Watchlist: {watchlist_summary}

Holdings: {open_positions}

Stats: {session_stats}{failed_trades_section}

Decide."""


class ContextBuilder:
    """Builds the system prompt and per-cycle briefing for Claude."""

    def __init__(self, config: SessionConfig, session_id: int):
        self.config = config
        self.session_id = session_id

    def build_system_prompt(self, closing_mode: bool = False) -> str:
        """Build the static system prompt with runtime values injected."""
        session = db.query_one(
            "SELECT current_capital, secured_profit, current_day FROM sessions WHERE id = ?",
            (self.session_id,),
        )

        # Build watchlist text
        watchlist_entries = db.query(
            "SELECT symbol, sector, notes FROM watchlist "
            "WHERE session_id = ? AND removed_at IS NULL ORDER BY symbol",
            (self.session_id,),
        )
        watchlist_text = "\n".join(
            f"{e['symbol']} | {e['sector'] or 'N/A'} | {e['notes'] or ''}"
            for e in watchlist_entries
        ) or "No stocks in watchlist."

        # Watchlist adjustment block
        if self.config.allow_watchlist_adjustment:
            watchlist_adjustment_block = (
                "At the END of each trading day only (not mid-cycle), you may add or "
                "remove stocks from your watchlist using add_to_watchlist(symbol, reason) "
                "and remove_from_watchlist(symbol, reason). Always provide a specific, "
                "reasoned justification. The system will validate additions — do not "
                "attempt to add illiquid or unknown stocks."
            )
        else:
            watchlist_adjustment_block = (
                "Your watchlist is fixed for this session. Watchlist adjustment tools "
                "are not available."
            )

        rules = self.config.risk_rules
        starting_capital = self.config.starting_capital
        prompt = SYSTEM_PROMPT_TEMPLATE.format(
            execution_mode=self.config.execution_mode.value.upper(),
            trading_mode=self.config.trading_mode.value.upper(),
            starting_capital=starting_capital,
            current_capital=session["current_capital"] if session else starting_capital,
            secured_profit=session["secured_profit"] if session else 0,
            current_day=session["current_day"] if session else 1,
            current_time=datetime.now(_IST).strftime("%I:%M %p IST"),
            mode_mandate=self.config.mode_mandate,
            max_per_trade=rules.max_per_trade,
            max_trade_value=starting_capital * rules.max_per_trade / 100,
            stop_loss=rules.stop_loss,
            take_profit=rules.take_profit,
            max_positions=rules.max_positions,
            max_deployed=rules.max_deployed,
            daily_loss_limit=rules.daily_loss_limit,
            session_stop_loss=rules.session_stop_loss,
            watchlist_text=watchlist_text,
            watchlist_adjustment_block=watchlist_adjustment_block,
        )

        # Append closing mode override if active
        if closing_mode:
            prompt += CLOSING_MODE_OVERRIDE

        return prompt

    def build_briefing(self, cycle_number: int) -> str:
        """Build the per-cycle briefing with live data."""

        # Market snapshot
        try:
            snapshot = get_market_snapshot()
            if "error" not in snapshot:
                market_text = (
                    f"Nifty 50: {snapshot['nifty_50']['last_price']} "
                    f"({snapshot['nifty_50']['change_percent']:+.2f}%)\n"
                    f"Bank Nifty: {snapshot['bank_nifty']['last_price']} "
                    f"({snapshot['bank_nifty']['change_percent']:+.2f}%)"
                )
            else:
                market_text = f"Market data unavailable: {snapshot['error']}"
        except Exception as e:
            market_text = f"Market data unavailable: {e}"

        # Global market context (S&P, Nikkei, crude, gold, INR, VIX)
        try:
            gctx = get_global_context()
            if "error" not in gctx:
                lines = []
                for name, data in gctx.items():
                    if name == "timestamp" or not isinstance(data, dict):
                        continue
                    if "error" in data:
                        continue
                    chg = data.get("change_pct")
                    chg_str = f"{chg:+.2f}%" if chg is not None else "N/A"
                    lines.append(f"{name}: {data['price']} ({chg_str})")
                global_context_text = " | ".join(lines) if lines else "Unavailable"
            else:
                global_context_text = "Unavailable"
        except Exception:
            global_context_text = "Unavailable"

        # Macro news (from cache)
        macro_row = db.query_one(
            "SELECT summary FROM news_cache "
            "WHERE category = 'macro' AND key = 'macro' "
            "ORDER BY fetched_at DESC LIMIT 1",
        )
        macro_news = macro_row["summary"] if macro_row else "No macro news available today."

        # Watchlist summary (top 10 stocks, rotating by cycle to cover full list)
        watchlist_entries = db.query(
            "SELECT symbol FROM watchlist "
            "WHERE session_id = ? AND removed_at IS NULL ORDER BY symbol",
            (self.session_id,),
        )
        # Rotate: show different stocks each cycle so Claude scans the full watchlist
        if watchlist_entries and cycle_number > 0:
            offset = ((cycle_number - 1) * 10) % max(len(watchlist_entries), 1)
            watchlist_entries = watchlist_entries[offset:offset + 10]
            if len(watchlist_entries) < 10:
                remaining = 10 - len(watchlist_entries)
                all_entries = db.query(
                    "SELECT symbol FROM watchlist "
                    "WHERE session_id = ? AND removed_at IS NULL ORDER BY symbol",
                    (self.session_id,),
                )
                watchlist_entries += all_entries[:remaining]
        watchlist_lines = []
        for entry in watchlist_entries:
            try:
                price_data = get_current_price(entry["symbol"])
                if "error" not in price_data:
                    watchlist_lines.append(
                        f"{entry['symbol']} ₹{price_data['last_price']:,.0f} {price_data['change_percent']:+.1f}%"
                    )
                else:
                    watchlist_lines.append(f"{entry['symbol']} N/A")
            except Exception:
                watchlist_lines.append(f"{entry['symbol']} N/A")

        watchlist_summary = " | ".join(watchlist_lines) or "No watchlist data."

        # Open positions with rationale
        from aaitrade.tools.journal import get_open_positions_with_rationale
        positions_data = get_open_positions_with_rationale()

        if positions_data["total"] > 0:
            pos_lines = []
            now_ist = datetime.now(_IST)
            for p in positions_data["open_positions"]:
                latest_thesis = ""
                if p["thesis_updates"]:
                    latest_thesis = f" | Latest review: {p['thesis_updates'][-1]['note']}"

                try:
                    opened = datetime.fromisoformat(p["opened_at"]).replace(tzinfo=_IST)
                    days_held = (now_ist - opened).days
                    hold_str = f"Day {days_held + 1} (bought {opened.strftime('%d %b')})"
                except Exception:
                    hold_str = "hold duration unknown"

                pos_lines.append(
                    f"  {p['symbol']} | {p['quantity']} shares @ ₹{p['entry_price']:.2f} | "
                    f"Target: ₹{p['target_price']:.2f} | Stop: ₹{p['stop_price']:.2f} | {hold_str}\n"
                    f"    Thesis: {p['key_thesis']}{latest_thesis}"
                )
            open_positions = "\n".join(pos_lines)
        else:
            open_positions = "No open positions."

        # Session stats (compact format)
        from aaitrade.tools.memory import get_session_summary
        stats = get_session_summary()
        if "error" not in stats:
            session_stats = (
                f"Capital ₹{stats['current_capital']:,.0f} | "
                f"P&L {stats['total_pnl_percent']:+.1f}% | "
                f"W/L {stats['wins']}W/{stats['losses']}L | "
                f"Today ₹{stats['today_pnl']:,.0f}"
            )
        else:
            session_stats = "N/A"

        # Failed trades from last 2 cycles — so Claude knows what was rejected
        failed_rows = db.query(
            "SELECT symbol, quantity, reason, decided_at FROM decisions "
            "WHERE session_id = ? AND action = 'TRADE_FAILED' "
            "ORDER BY decided_at DESC LIMIT 5",
            (self.session_id,),
        )
        failed_trades_section = ""
        if failed_rows:
            lines = [f"  {r['symbol']} ×{r['quantity']}: {r['reason']}" for r in failed_rows]
            failed_trades_section = "\n\nFailed Trades (NOT executed — position unchanged):\n" + "\n".join(lines)

        return BRIEFING_TEMPLATE.format(
            cycle_number=cycle_number,
            market_snapshot=market_text,
            global_context=global_context_text,
            macro_news=macro_news,
            watchlist_summary=watchlist_summary,
            open_positions=open_positions,
            session_stats=session_stats,
            failed_trades_section=failed_trades_section,
        )
