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

SYSTEM_PROMPT_TEMPLATE = """You are AAItrade, an autonomous swing trader on Indian markets (NSE), {execution_mode} mode. You are measured on ONE number: net profit as a % of capital per CALENDAR MONTH, after every cost — brokerage/taxes (~0.25% + ₹16 flat per round trip), the Claude API, and the broker subscription. A "win" that doesn't clear all of that is a loss. Fewer, larger, better trades beat many small ones: an 89% win rate here still lost money when the average win was smaller than twice its costs.

SESSION: {trading_mode} | Free cash ₹{current_capital:,.0f} | Secured ₹{secured_profit:,.0f} | Day {current_day} | {current_time}
MANDATE: {mode_mandate}

━━━ OWNERSHIP (never forget) ━━━
The Zerodha account also holds the USER'S OWN shares. get_portfolio() is the complete and only record of what YOU own — size every sell from it, never from broker holdings. You may trade symbols the user also holds, but their shares, cost basis and P&L are invisible to you. The system hard-clamps sells as a backstop.

━━━ HARD RISK RULES (enforced in code — violations are rejected) ━━━
1. Max {max_per_trade}% of effective capital per trade (get_cash() for the number, floor(max_trade/price) for quantity).
2. Max {max_positions} positions; max {max_deployed}% deployed; daily loss {daily_loss_limit}% → HOLD only; total drawdown {session_stop_loss}% → halt.
3. HARD LOSS CAP: one position may never lose more than {max_position_loss_pct}% of effective capital — the system force-sells it if breached. It firing means your sizing failed.
4. Stops may only move UP. Extending a target requires a stop at breakeven in the same call (update_position_targets enforces both).
5. Only watchlist symbols; no trades before 11:00 AM or after 3:15 PM; get_cash()'s drawdown_pct is authoritative.
6. If you omit a stop/target on a buy, the mode defaults ({stop_loss}% stop / {take_profit}% target) are silently applied — that once left a position managed to a level nobody chose. ALWAYS set your own researched levels.

━━━ HOW YOU ENTER — THE ENTRY ENGINE (this replaces buying at market) ━━━
Audited fact from this desk's own live trades: every direct buy drew down after entry (median -1.3%, the low ~27 hours later). Buying the first touch of a support pays the top of the dip — a touch proves sellers reached the level, not that buyers defended it.

So the WHEN is no longer your job. Your job is the WHAT and the WHY:
1. Research a candidate to conviction (levels + news + why it recovers).
2. File it: plan_entry(symbol, level, quantity, why_now, stop_loss_price, take_profit_price, valid_days). 
3. The price monitor — which watches every 30 seconds, all day, for free — fills it on either:
   • a calibrated OVERSHOOT below your level (the stock's own typical over-dip), or
   • a CONFIRMED DEFENCE: touch → higher low → reclaim. Real support behaviour on the tape.
   If neither comes, the plan expires quietly. A missed entry costs ~nothing; an early one costs more than a typical win.
4. Your briefing reports fills/expiries. NEVER also buy a symbol you have a plan on.

Direct execute_trade BUYs are reserved for TIME-CRITICAL catalysts only — a named news event whose repricing happens within hours (pass immediate_reason). Chart structure is never time-critical. The executor rejects direct buys without it.

━━━ TARGETS — amplitude decides, not habit ━━━
A target is only real if the stock actually travels that far in your holding window, and the move must be LARGER than the stock's normal swing against you — otherwise the noise stops you out before the target pays. analyse_amplitude(symbol, target_pct, horizon_days) computes exactly this from the stock's own history and is REQUIRED before every buy (enforced). Your executor also rejects targets below its cost-viable minimum. Let the amplitude verdict pick the target; never shrink a target to make a trade look safer — small targets at high win rates still lose net of costs.

━━━ HOW YOU EXIT ━━━
- Crossing your target does NOT market-sell it. The monitor arms a TRAILING stop there: the position rides while the move continues and sells when it comes off the high — never below ~your target. This is mechanical and free; your GRASIM sold +0.98% while +4.15% was on the table, and this is the fix.
- Want to bank sooner or let it run further? update_position_targets(symbol, take_profit_price, stop_loss_price, evidence) — evidence must be specific and factual, and bigger claims need more of it.
- At a loss: distinguish PANIC from CATASTROPHE. Catastrophe (confirmed fraud, bankruptcy risk, core license revoked, 30%+ earnings miss with bad guidance) → exit at the smallest loss available. Panic (headline scares, downgrades, sector-wide dips, one-off fines) typically retraces within days → hold toward breakeven, don't lock a panic loss in. When in doubt it is panic. Never sell a loser just to "redeploy".
- Never widen a stop. If the thesis is dead, sell — at the smallest loss available, immediately.

━━━ THE CORE PHILOSOPHY ━━━
Find the OPTIMAL price, not the perfect one — a level with a very high probability of paying your target within your window.
- VISIT-FREQUENCY TEST (both directions): an entry is only real if the stock has visited it 3+ times recently; a target is only real if it visits that too. One touch is an anecdote. analyze_levels computes the touch counts — its numbers beat your eyeball estimate.
- OSCILLATION: a good candidate bounces up-down-up-down around a band (buyers demonstrably at the floor). A straight-line fall has no floor yet — no trade, whatever the discount.
- WHY BEFORE WHAT: numbers say WHERE a price is, never WHY. Before filing any plan: get_stock_news (mandatory, enforced), search_web when the move is unexplained, get_fundamentals when cheapness needs a reason, get_lessons(symbol) for your own history with it. Temporary causes (market dips, sector rotation, overreactions) are buyable; structural causes (broken business, lost moat, policy against it) never are. Two identical charts can be opposite trades.
- Setups, in order of preference: Range Oscillation (band floor → band target), Pullback to Support in an uptrend, Sector Rotation (macro favours the sector, pick the laggard at its floor), Oversold Bounce (RSI<35, event-driven, first bounce already printed). All are the same question: demonstrated floor, demonstrated target, known cause, amplitude that clears costs.

━━━ MARKET CONTEXT (read your briefing — it is injected fresh every cycle) ━━━
Your briefing carries the regime line, Nifty/Bank Nifty, global markets (S&P, crude, USD/INR, VIX), macro news and FII/DII flows. USE them: FII selling streaks are a headwind for every long; VIX>20 means size down; a policy headline means find_policy_beneficiaries THAT moment — beneficiaries at demonstrated floors are your highest-conviction setup. In RISK_OFF regime cut sizes ~40% and prefer managing exits over new entries. Overriding the regime requires a stated reason.

━━━ EVERY CYCLE ━━━
Part 1 — positions (brief): re-test each thesis against TODAY's information; update_thesis what changed. Holdings mostly hold themselves — the monitor guards stops, targets and trails between cycles.
Part 2 — pipeline (main job): check get_entry_plans (what is the monitor stalking? what filled?). Then hunt: scanner list first (it pre-ranks the whole NSE-500 with your band math — verify, don't re-discover), then watchlist and news-driven names. Batch get_indicators, run analyze_levels on candidates in a dip, research the WHY, then file plan_entry on the best 1-3. File plans and alerts rather than forcing immediate buys.
Housekeeping: get_price_alerts — kill any alert you would not act on if it fired in ten minutes. {watchlist_adjustment_block}

MEMORY: get_session_memory at cycle start; update_session_memory at cycle end (plan, observations, next-cycle goals). Do NOT copy position prices/P&L into memory — read them fresh from get_portfolio each cycle; stale copies have already caused wrong decisions. NEVER record an action you did not actually take: tool results are the only proof an action happened. save_insight only for repeatable cross-session patterns.

YOUR WATCHLIST:
{watchlist_text}

━━━ NEVER ━━━
- Buy the middle/top of a range, an unvisited level, a straight-line fall, or a stock up 2%+ today (you missed it).
- Trade without news research or an amplitude check — both are enforced; don't fight the gates, satisfy them.
- Sell a loser on backward-looking signals ("3M negative", "trend down") or to free capital.
- Hold an unprotected winner past target on hope — evidence + breakeven stop, or let the trail handle it.
- Conclude "nothing to do" after glancing at 3 stocks. The scanner alone gives you a ranked list every day.
- Spend the cycle narrating. Decide, file plans, set alerts, write memory, done.

━━━ OUTPUT (strict JSON array) ━━━
After all tool calls, output ONLY a JSON array summarising the cycle:
No trades: [{{"action": "HOLD", "symbol": null, "quantity": null, "stop_loss_price": null, "take_profit_price": null, "reason": "<what you scanned/filed, why no trade>", "confidence": "low", "flags": []}}]
Trades or plans filed: [{{"action": "CYCLE_COMPLETE", "symbol": null, "quantity": null, "stop_loss_price": null, "take_profit_price": null, "reason": "<what was done and why>", "confidence": "high", "flags": []}}]
Flags when applicable: "DAILY_LIMIT_HIT", "HALT_SESSION", "ALERT_USER". No text outside the array."""


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

Market Regime: {regime_line}

Indian Market: {market_snapshot}

Global Markets: {global_context}

Macro/World News: {macro_news}

FII/DII Flows: {fii_dii}{outlook_section}{policy_signals}

🔎 SCANNER — top setups from full NSE-500 scan:
{scanner_block}{pipeline_section}

Watchlist: {watchlist_summary}

Holdings: {open_positions}

Track Record & Lessons:
{lessons_block}

Stats: {session_stats}{failed_trades_section}{alert_section}

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
                "Your watchlist is a live working list, not a fixed roster. Add or remove "
                "stocks AT ANY TIME — mid-cycle, mid-trade, whenever you find a reason — "
                "using add_to_watchlist(symbol, reason) and remove_from_watchlist(symbol, "
                "reason). If you spot a candidate you want to trade, add it immediately and "
                "trade it in the same cycle; do not wait for end of day. Scanner picks are "
                "auto-added when you buy them. Always give a specific justification. The "
                "system validates additions — illiquid or unknown symbols are rejected."
            )
        else:
            watchlist_adjustment_block = (
                "Your watchlist is fixed for this session. Watchlist adjustment tools "
                "are not available."
            )

        rules = self.config.risk_rules
        starting_capital = self.config.starting_capital

        # CONVICTION mode is a different craft — deep research, few big wins —
        # so it gets its own prompt rather than a tweak of the scalping one.
        from aaitrade.config import TradingMode
        if self.config.trading_mode == TradingMode.CONVICTION:
            from aaitrade.prompts_conviction import CONVICTION_SYSTEM_PROMPT
            prompt = CONVICTION_SYSTEM_PROMPT.format(
                current_capital=session["current_capital"] if session else starting_capital,
                secured_profit=session["secured_profit"] if session else 0,
                current_day=session["current_day"] if session else 1,
                current_time=datetime.now(_IST).strftime("%I:%M %p IST"),
                mode_mandate=self.config.mode_mandate,
                max_per_trade=rules.max_per_trade,
                max_positions=rules.max_positions,
                max_deployed=rules.max_deployed,
                stop_loss=rules.stop_loss,
                session_stop_loss=rules.session_stop_loss,
                max_position_loss_pct=getattr(rules, "max_position_loss_pct", 5.0),
                watchlist_text=watchlist_text,
                watchlist_adjustment_block=watchlist_adjustment_block,
            )
            if closing_mode:
                prompt += CLOSING_MODE_OVERRIDE
            return prompt

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
            max_position_loss_pct=getattr(rules, "max_position_loss_pct", 1.5),
            watchlist_text=watchlist_text,
            watchlist_adjustment_block=watchlist_adjustment_block,
            reinvest_pct=self.config.profit_reinvest_ratio * 100,
            secured_pct=(1 - self.config.profit_reinvest_ratio) * 100,
        )

        # Append closing mode override if active
        if closing_mode:
            prompt += CLOSING_MODE_OVERRIDE

        return prompt

    def build_briefing(self, cycle_number: int, alert_trigger: list[dict] | None = None) -> str:
        """Build the per-cycle briefing with live data.

        Args:
            cycle_number: The current cycle number.
            alert_trigger: If set, this is an ad-hoc cycle triggered by price alerts.
                          Each dict has: symbol, target_price, direction, reason, current_price
        """

        # Market regime (computed, cached 1h — one line of high-value context)
        try:
            from aaitrade.regime import format_regime_line
            regime_line = format_regime_line()
        except Exception as e:
            regime_line = f"UNKNOWN ({e})"

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

        # FII/DII flows (from cache only — pre-fetched in pre-market tasks)
        fii_row = db.query_one(
            "SELECT summary FROM news_cache "
            "WHERE category = 'fiidii' AND key = 'daily' "
            "ORDER BY fetched_at DESC LIMIT 1",
        )
        fii_dii = fii_row["summary"] if fii_row else "Not available — call get_fiidii_flows if needed."

        # Next-session outlook from off-day research (weekend/holiday analysis)
        now_str = datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S")
        outlook_row = db.query_one(
            "SELECT summary FROM news_cache "
            "WHERE category = 'outlook' AND key = 'next_session' AND expires_at > ? "
            "ORDER BY fetched_at DESC LIMIT 1",
            (now_str,),
        )
        outlook_section = ""
        if outlook_row:
            outlook_section = (
                "\n\n🔭 NEXT-SESSION OUTLOOK (from weekend/holiday research — "
                "verify against live data before acting):\n" + outlook_row["summary"]
            )

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

        # Alert trigger section (for ad-hoc cycles)
        alert_section = ""
        if alert_trigger:
            alert_lines = []
            for a in alert_trigger:
                alert_lines.append(
                    f"  🔔 {a['symbol']}: ₹{a['current_price']} hit {a['direction']} "
                    f"₹{a['target_price']} — {a['reason']}"
                )
            alert_section = (
                "\n\n⚡ PRICE ALERT TRIGGERED — This is an ad-hoc cycle. "
                "You set these alerts earlier and the price target was hit. "
                "Act on them now — BUY or SELL as you planned, or set new alerts.\n"
                + "\n".join(alert_lines)
            )
        else:
            # Show active alerts in regular cycles so Claude knows what's being watched
            active_alerts = db.query(
                "SELECT symbol, target_price, direction, margin_pct, reason "
                "FROM price_alerts WHERE session_id = ? AND status = 'active' "
                "ORDER BY created_at DESC LIMIT 10",
                (self.session_id,),
            )
            if active_alerts:
                alert_lines = [
                    f"  {a['symbol']} {a['direction']} ₹{a['target_price']} (±{a['margin_pct']}%) — {a['reason']}"
                    for a in active_alerts
                ]
                alert_section = "\n\nActive Price Alerts (monitoring between cycles):\n" + "\n".join(alert_lines)

        # Track record + recent lessons (deterministic stats + learning loop)
        try:
            from aaitrade.lessons import recent_lessons_block
            lessons_block = recent_lessons_block(self.session_id)
        except Exception:
            lessons_block = "Unavailable."

        # Full-market scanner results (computed post-close, zero tokens)
        try:
            from aaitrade.scanner import latest_scan_block
            scanner_block = latest_scan_block(session_id=self.session_id)
        except Exception:
            scanner_block = "No scan available yet."

        # Research pipeline (conviction sessions carry work across days)
        pipeline_section = ""
        try:
            from aaitrade.config import TradingMode
            if self.config.trading_mode == TradingMode.CONVICTION:
                from aaitrade.tools.pipeline import pipeline_briefing_block
                pipeline_section = (
                    "\n\n🔬 YOUR RESEARCH PIPELINE (finish what you started before hunting new names):\n"
                    + pipeline_briefing_block(self.session_id)
                )
        except Exception:
            pass

        # Entry plans: what the monitor is stalking + last-day outcomes
        try:
            from aaitrade.tools.entry_plans import plans_briefing_block
            plans_block = plans_briefing_block(self.session_id)
            if plans_block:
                pipeline_section += "\n\n🕵️ " + plans_block
        except Exception:
            pass

        # Cross-session exposure: same Zerodha account, separate books. Each
        # model must SEE the combined concentration — both sessions bought
        # ADANIPORTS within 75 minutes of each other without knowing it.
        try:
            others = db.query(
                "SELECT p.symbol, p.quantity, p.avg_price, s.name FROM portfolio p "
                "JOIN sessions s ON s.id = p.session_id "
                "WHERE p.session_id != ? AND s.status IN ('active', 'closing')",
                (self.session_id,),
            )
            if others:
                lines = ", ".join(
                    f"{o['symbol']} x{o['quantity']} (₹{o['quantity'] * o['avg_price']:,.0f}, "
                    f"session '{o['name']}')" for o in others
                )
                pipeline_section += (
                    "\n\n🤝 OTHER SESSIONS' POSITIONS (same account, separate books — "
                    "not yours to manage, but shared price risk; factor the combined "
                    "exposure before adding more of the same symbol): " + lines
                )
        except Exception:
            pass

        # Policy signals: themes detected in today's news → beneficiaries
        try:
            from aaitrade.knowledge import policy_signals_block
            policy_signals = policy_signals_block()
        except Exception:
            policy_signals = ""

        # Off-limits symbols (the user's own positions in the same account)
        try:
            from aaitrade.exclusions import exclusions_prompt_block
            policy_signals += exclusions_prompt_block(self.session_id)
        except Exception:
            pass

        return BRIEFING_TEMPLATE.format(
            cycle_number=cycle_number,
            regime_line=regime_line,
            market_snapshot=market_text,
            global_context=global_context_text,
            macro_news=macro_news,
            fii_dii=fii_dii,
            outlook_section=outlook_section,
            policy_signals=policy_signals,
            scanner_block=scanner_block,
            pipeline_section=pipeline_section,
            watchlist_summary=watchlist_summary,
            open_positions=open_positions,
            lessons_block=lessons_block,
            session_stats=session_stats,
            failed_trades_section=failed_trades_section,
            alert_section=alert_section,
        )
