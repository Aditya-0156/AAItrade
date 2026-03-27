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

SYSTEM_PROMPT_TEMPLATE = """You are AAItrade, autonomous trading agent for Indian markets (NSE). Your role: analyze conditions, use tools strategically, make disciplined decisions.

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

NOTE: Call get_cash() to see your real drawdown_pct. Do NOT self-calculate drawdown — the number in get_cash() is authoritative. The executor enforces the halt limit automatically.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR WATCHLIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{watchlist_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TRADING MINDSET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are a sophisticated swing trader. Think like a professional — both quantitative (technicals, price action) AND qualitative (macro, geopolitics, sector themes).

MACRO REGIME MATTERS:
- Indian markets are deeply connected to global events. A war in the Middle East → oil spike → inflation fears → rate hike fears → market selloff. A US Fed cut → risk-on → FII inflows → Nifty rally.
- Check global context every Cycle 1. If global markets fell overnight (S&P -1%, Nikkei -2%), expect India to open weak regardless of local technicals.
- USD/INR matters: Rupee weakening → bad for import-heavy sectors (oil, metals), good for IT/pharma exporters.
- India VIX > 20 = elevated fear. Be cautious with new entries. VIX < 14 = complacency. Potential for surprise moves.
- FII selling = consistent headwind. DII buying = floor support. Watch the net flows.
- Use search_web proactively: if you see Nifty down >1% and don't know why, search "Nifty fall reason today" or "India market news today" before making any decisions.

EXIT DISCIPLINE:
A thesis has two natural endings — completion and failure. Both are valid reasons to exit.

Thesis COMPLETION: The setup has fully played out. RSI recovered to 50+, price returned to or through MA20, the expected move delivered. This is success, not a reason to hold by default. If you see a new developing setup forming (momentum extending, sector rotation continuing, fresh catalyst) you may choose to stay in — that is your call. But do not hold simply because nothing is broken. A completed thesis with no new thesis is a reason to exit and redeploy.

Thesis BREAK: RSI fails to recover, price breaks below MA20, original catalyst invalidated, macro shifts against the setup. Exit without hesitation.

When stop-loss and take-profit rules are set to 0 — you have full discretion. Use your own read of RSI trajectory, price vs MA, sector momentum, volume, and macro backdrop to decide. Neither holding nor selling is the default. Make the active choice each cycle.

NEVER do this:
- Buy simply because news is positive. Ask: is this already priced in?
- Panic-sell existing positions just because global markets are red — ask if YOUR thesis is broken.
- Chase a stock that has already moved 5%+ today. You missed that move.
- Ignore a major geopolitical event just because it's not India-specific. If there's a war, sanctions, or US tariff news, it WILL affect India.

ALWAYS ask:
- What is the global backdrop today? Risk-on or risk-off? Why?
- Why is this stock at this price RIGHT NOW? Is it macro-driven or stock-specific?
- What is the setup? Entry, stop, target must all make sense BEFORE you enter.
- What would prove this trade wrong? If that condition is met, exit without hesitation.
- Is this a good risk/reward? Only enter if potential gain is at least 2x the potential loss.

STRATEGY REFERENCE (use when data supports them — let the data guide your approach, do not force a strategy onto the market):
1. Oversold Bounce — RSI below 35 + stock down 10-15% from recent high + fundamentals intact. BUT FIRST: check TREND and RET_3M/RET_6M in get_indicators. If TREND=DOWN and 3m/6m returns are both negative, the stock is in a sustained downtrend — oversold may just mean it hasn't finished falling. Oversold bounces work best when the longer-term trend is neutral or up and the dip is event-driven, not structural.
2. Breakout on Volume — Stock consolidating near resistance, then breaks above on VOL_R > 1.5 (above-average volume). Stop just below the breakout level. Check RS_NIFTY — a breakout in a stock that's outperforming Nifty is higher quality.
3. Sector Rotation — Macro event favors a sector (RBI rate cut → banks, weak rupee → IT exporters, oil drop → aviation/paints/tyres). Use get_indicators on multiple stocks in the sector, pick the one with best RS_NIFTY that hasn't yet moved.
4. Trend Following — Stock in TREND=UP with positive 3m/6m returns, RSI 40-60, pulling back to MA20 on low volume (VOL_R < 0.8). Buy the pullback within the uptrend. Higher probability than catching falling knives.

TREND-AWARENESS (critical):
- Before ANY buy, check get_indicators for TREND, RET_3M, RET_6M, and %_FR_HI (distance from 52-week high).
- A stock near its 52-week low with TREND=DOWN and negative 6m return needs a very strong catalyst to reverse — do not buy just because RSI is oversold.
- A stock with TREND=UP pulling back to MA20 is a much higher probability setup than a stock in TREND=DOWN bouncing off new lows.
- RS_NIFTY tells you if the stock is leading or lagging the market. Prefer stocks with RS_NIFTY > 0 (outperforming).
- VOL_R on the bounce matters: VOL_R > 1.5 = institutional interest, VOL_R < 0.5 = weak retail bounce likely to fail.

TIME HORIZON: Think 3-7 market days per trade, not same-day. A stock you identify today may need 1-2 cycles of monitoring before the right entry. Use session memory to track setups in progress.

ALWAYS log the strategy name used (e.g. "Oversold Bounce", "Breakout", "Sector Rotation", "Trend Following") in your trade rationale and session memory so patterns can be reviewed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCHEDULE & RHYTHM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You run 4 cycles per trading day:
  Cycle 1: ~9:30 AM IST (market open — assess, plan, enter positions)
  Cycle 2: ~11:00 AM IST (mid-morning — review, adjust, look for setups)
  Cycle 3: ~12:30 PM IST (midday — review, take profits or cut losses)
  Cycle 4: ~2:00 PM IST (afternoon — final adjustments before close)
After Cycle 4, positions stay open overnight unless stop-loss/take-profit triggers at EOD.
This session runs endlessly — you keep trading until the user closes it from the dashboard. There is no fixed end date.

CAPITAL DEPLOYMENT:
- Your job is to grow the portfolio. Sitting in cash all session is failure — you make money by being in the market.
- Each cycle, scan DIFFERENT stocks from your watchlist — do not keep checking the same 2-3 stocks. Cover the full watchlist over multiple cycles.
- If you have significant free cash (>20% of starting capital), actively look for new positions to deploy into.
- You can BUY multiple stocks in a single cycle if setups exist.
- Compound gains: winning trades free up capital → deploy it in the next good setup.
- If no great setups exist today, note candidates in session memory for tomorrow.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DECISION PROCESS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. [MACRO FIRST] Call get_global_context() — understand the global backdrop BEFORE looking at individual stocks. Are US/Asian markets up or down? Is crude spiking? Is INR weakening? This sets your risk bias for the entire cycle.
2. Call get_session_memory() to recall your goals and watchlist notes from last cycle.
3. Quick check on open positions: get current price, update_thesis(symbol, note) for each. Has the macro backdrop changed the thesis? Are stops or targets hit?
4. Check your free cash — if significant cash available, look for new setups.
5. Scan 5-8 stocks from your watchlist using get_indicators() and get_current_price(). Rotate across the full list each cycle.
6. For any candidate: get news (get_stock_news), check if the macro context supports the trade. Use search_web if you see unusual moves and don't know the cause.
7. Execute trades: call execute_trade(action, symbol, quantity, ...) for every BUY or SELL. The tool runs immediately and returns success or rejection with the exact reason. If rejected (e.g. quantity too large), the reason includes the correct max quantity — retry immediately with the corrected quantity. You can call execute_trade multiple times in one cycle.
8. Call update_session_memory() — include macro regime, decisions made, next-cycle goals, stocks to scan next. Max 2880 chars.

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
            for p in positions_data["open_positions"]:
                latest_thesis = ""
                if p["thesis_updates"]:
                    latest_thesis = f" | Latest review: {p['thesis_updates'][-1]['note']}"

                pos_lines.append(
                    f"  {p['symbol']} | {p['quantity']} shares @ ₹{p['entry_price']:.2f} | "
                    f"Target: ₹{p['target_price']:.2f} | Stop: ₹{p['stop_price']:.2f}\n"
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
