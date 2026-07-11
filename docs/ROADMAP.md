# AAItrade — Master Improvement Roadmap

Focus: **live trading**. Goal: the smartest, most aware, most predictive autonomous
trading machine that a solo operator can run safely — while keeping token spend flat.

This document has two parts: (A) what was already fixed, so you know the new baseline;
(B) the phased plan for everything that comes next, ordered by what makes live trading
safer and smarter fastest.

---

## Part A — Fixed (July 2026 overhaul)

| # | Problem | Fix |
|---|---------|-----|
| 1 | **Kite API key + secret hardcoded** in `server.py`, `refresh_token.py` — pushed to GitHub | Moved to `.env` via new `kite_auth.py`. **ACTION: regenerate the API secret on the Zerodha dev console — the old one is public git history.** |
| 2 | **Phantom fills**: live orders unverified after 5s were recorded as "bought" in DB (the "it thought it bought the stocks" incident) | `_verify_order_fill`: poll to terminal state (~20s), cancel stuck orders, re-check, record partial fills correctly. DB is only updated on confirmed fills. |
| 3 | **EOD processing never ran**: after the 14:00 slot the loop slept to next morning, skipping the 15:30 EOD window entirely — no daily summaries, no EOD stop-loss checks, day counter frozen | Event-based scheduler: next wake = min(next slot, EOD 15:30, tomorrow 8:55). EOD runs any time after 15:30, once/day. |
| 4 | **Pre-market macro news never fetched**: loop woke at 8:55, next check ran at 9:30 — outside the hardcoded 9:00–9:05 window ("it forgot to look at global market news") | `_pre_market_tasks()` runs once/day on any wake after 8:30: token health check, macro news, FII/DII prefetch, portfolio sync. |
| 5 | **Token lifecycle broken**: Telegram `/token` didn't persist to `.env` (lost on restart); recovered sessions used the stale frozen `APIKeys`; price monitors kept the dead client | One shared path (`kite_auth.apply_kite_token`) used by dashboard + Telegram; keys rebuilt from env after update; price monitors refreshed; **pre-market token health check pings Telegram before open if the token is dead**. |
| 6 | **Cross-session data corruption risk**: tool modules hold `_session_id` in module globals; with several sessions in server threads, session A's tools could write into session B | Global `_CYCLE_LOCK` serializes decision cycles; every cycle re-points tool context to its own session first. |
| 7 | **`--recover` silently converted LIVE sessions to PAPER** (hardcoded default config) | Recovery reads execution mode, trading mode, capital, watchlist from the DB. |
| 8 | **Timezone bug**: naive IST timestamps parsed as server-local time (UTC on the VPS) shifted slot logic by 5.5h — double-run/skipped cycles | Parse naive DB timestamps as IST explicitly. |
| 9 | **Stop-losses unwatched between cycles**: a position could crash 10% intraday and nothing noticed for up to 90 min | Price monitor now auto-watches every position's stop/target and wakes Claude for an ad-hoc decision cycle on breach (once/day per level). |
| 10 | **Zero-cost P&L fantasy**: no STT/DP/stamp charges anywhere — a ₹4,000 position costs ~0.6% round trip, which erases the 0.5–1% profit target | Full Zerodha CNC charge model in the executor (both modes); `charges` column on trades; net P&L; cost-floor guidance in the system prompt (prefer fewer/larger positions). |
| 11 | **Token waste**: every tool round re-processed the whole conversation at full input price; `get_indicators` capped at 5 symbols while the prompt demands batches of 10–15 (3× the tool rounds) | Incremental message caching (moving cache breakpoint); indicators/prices batch 15 symbols with parallel fetch + 45-min history cache; per-cycle token usage logged. |
| 12 | **Weekends were dead time** ("can it look at sat-sun news and predict Monday?") | New `research.py`: Sat/Sun/holiday evenings gather global context + Tavily research, Claude writes a **Next-Session Outlook** (expected open, causal chains, sector impact map, events ahead, risk flags) — injected into every briefing the next trading day. |
| 13 | **Stale, US-heavy news**: NewsAPI free tier delays articles ~24h | Tavily (real-time, India-focused) is now the primary macro news source; NewsAPI is fallback. FII/DII flows + outlook added to every briefing. |
| 14 | Misc: 15:15 no-trade rule unenforced; summarizer mangling tool output at 800 chars; holiday calendar silent for 2027+; dashboard stop taking hours to register during long sleeps | All fixed (hard block after 15:15, threshold 3500, loud missing-year warning, interruptible 60s-chunk sleeps). |

**Immediate manual actions for you:**
1. **Regenerate the Kite API secret** (Zerodha dev console) — the old one is in public git history. Update `.env` local + server.
2. Consider rewriting git history (or rotating everything) since the secret was pushed.
3. The dashboard API has **no authentication** and `CORS *` behind your tunnel — anyone with the URL can start live sessions and push tokens. See Phase 1.
4. 11 stale tests were already failing before this overhaul (prompt-text and memory-limit assertions that drifted) — worth a cleanup pass.

---

## Part B — The Plan

### Phase 1 — Live-trading hardening (do first, ~1–2 weeks)

1.1 **Dashboard auth** (small): bearer-token middleware on FastAPI (`API_AUTH_TOKEN` in
`.env`, checked on every `/control` route at minimum). Without it, the tunnel URL is a
remote control for your real money.

1.2 **One-live-session-per-Kite-account guard** (small): refuse to start a second live
session against the same account. Today two live sessions double-count the same Zerodha
holdings in `portfolio_sync` and collide on sells (the code only logs a warning).

1.3 **Order-state journal** (medium): persist every placed order (`orders` table:
order_id, state, placed/filled/cancelled timestamps, fill price, qty). On crash/restart,
reconcile pending orders against Kite before doing anything else. Today a crash between
`place_order` and DB write loses the order entirely.

1.4 **GTT (Good-Till-Triggered) stop-losses on the exchange** (medium-high value):
place a Kite GTT sell at the stop price the moment a BUY fills. Then the stop executes
even if your server, tunnel, or token is dead. The price monitor becomes the *second*
layer, not the only one. This is the single biggest live-safety upgrade available.

1.5 **Margin/funds check before live buys** (small): call `kite.margins()` pre-order —
internal `current_capital` and the real account can drift; a rejected order wastes a
cycle, an unexpected fill from stale accounting is worse.

1.6 **Kite WebSocket ticker** (medium): replace 30s REST polling in the price monitor
with `KiteTicker` streaming quotes. Faster alert reaction (seconds, not half-minutes),
fewer API calls, real-time stop-loss protection.

1.7 **Daily automated token flow** (small): morning Telegram message at 8:30 with the
login URL; you tap, log in, paste the request token back via `/token`. The pre-market
health check (already built) closes the loop by verifying before 9:15.

### Phase 2 — Awareness & prediction engine (~2–4 weeks)

2.1 **Event calendar table** (`events` in SQLite): RBI MPC dates, Fed FOMC, CPI/GDP
releases, earnings dates for held + watchlist stocks, expiry days, budget dates.
Populated weekly by the research cycle via Tavily. Briefing shows "TODAY'S EVENTS" and
"THIS WEEK". Rules like "no new entries 24h before an earnings report of that stock"
become enforceable in the executor, not just prompted.

2.2 **Reaction-chain knowledge base**: the system prompt hardcodes 5 causal chains
(oil→inflation→FII…). Move these to a DB table Claude can *append to* when it observes
a new chain play out ("US chip sanctions → Indian IT down 2 days later"). Inject the
top-N relevant chains per briefing based on today's headlines. The machine literally
learns geopolitics→market mappings over time.

2.3 **Market regime flag** (computed, not prompted): pre-market task computes
RISK_ON / NEUTRAL / RISK_OFF from VIX level+slope, FII 5-day flow, Nifty vs MA20/50,
global overnight moves. One line in the briefing. Executor can size down automatically
in RISK_OFF (e.g. max_per_trade × 0.6).

2.4 **Intraday news watcher thread** (like the price monitor, but for headlines):
every 15 min, scan headlines for held + watchlist symbols; if a material keyword hits
(fraud, raid, downgrade, order win, guidance), wake Claude with an ad-hoc news cycle
exactly like a price alert. This is what closes the "markets react in minutes, cycles
run every 90" gap.

2.5 **Weekend research v2**: current version produces one outlook blob. Upgrade to
structured output (JSON: bias, confidence, sector map, event list) so Monday's cycles
can *check* predictions against reality, and a "prediction scorecard" accumulates —
did weekend calls come true? Feed the scorecard back into the research prompt so it
calibrates itself.

2.6 **Pre-open auction read** (9:00–9:15): NSE publishes pre-open indicative prices.
Fetch them in the pre-market task; the outlook gets a reality check before 9:15, and
gap-up/gap-down handling becomes explicit.

### Phase 3 — Intelligence upgrades (~1 month, parallel with Phase 2)

3.1 **Deterministic level-analysis tool** — the highest-value intelligence upgrade.
The prompt currently makes Claude *count candle touches by eyeballing raw OHLCV JSON*
("it looked at things like a graph"). Build `analyze_levels(symbol, entry, target)`
in Python: visit-frequency counts for entry/target, 14-day band position, oscillation
score (direction changes / straight-line detection), nearest 3+-visit levels above and
below. Claude gets the *answers* to its six checks in one tool call — cheaper, faster,
and immune to arithmetic slips. The prompt shrinks accordingly.

3.2 **Hierarchical model strategy** (smart *and* cheap):
- 9:30 planning cycle + weekend research → **Sonnet** (deep reasoning where it pays)
- 11:00/12:30/14:00 execution cycles → **Haiku** (mechanical: check plan, check levels, act)
- News summarization stays Haiku.
Config already supports per-session models; add per-cycle-type model selection in
`SessionConfig`. Estimated net cost: roughly flat vs today, materially smarter mornings.

3.3 **Post-trade attribution loop**: on every closed trade, a small Haiku call tags it:
thesis-correct/wrong, exit-quality, holding-period vs plan, cost drag. Weekly rollup
("your oversold-bounce trades in RISK_OFF regimes lose 70% of the time") injected into
the system prompt as a LEARNED LESSONS block. This is how the machine gets *better*
instead of just older.

3.4 **Structured session memory**: replace the free-text blob with fields (plan,
watch-levels, open questions, predictions made). Predictions become checkable (2.5).

3.5 **Candlestick rendering for vision** (experiment): render the 30-day chart to a
PNG and pass it as an image block — Claude reads shape (flags, support tests) far
better from a picture than from 30 rows of JSON. Measure token cost vs. accuracy win.

### Phase 4 — Continuous operation (~3–6 weeks out)

4.1 **Event-driven core**: today the day is 4 fixed slots + alerts. Move to a scheduler
where *everything* is an event source — price alerts, position stops, news hits, index
moves >1%, volume spikes >3× — each waking a scoped mini-cycle with a purpose-built
briefing ("you were woken because X; decide about X only"). Cheaper than more full
cycles, and the machine becomes genuinely continuous and active.

4.2 **A 15:00 positioning slot**: one more scheduled decision point for "hold overnight
or trim before close" on days with elevated risk (events tomorrow, regime RISK_OFF).

4.3 **Watchlist auto-refresh**: weekly job proposes candidates from NSE top
gainers/losers, 52-week-low lists, and sector rotation scans; Claude accepts/rejects
with reasons during the weekend research cycle.

### Phase 5 — Evaluation & safety net (ongoing)

5.1 **Backtest harness**: replay historical OHLCV through the *same* executor +
risk rules with a scripted "oracle Claude" (rule-based stand-in) to validate strategy
math (cost floor, band strategy win rate) without API spend. Live-mode changes should
require a green backtest.

5.2 **Shadow mode**: run a paper session with identical config alongside the live one;
divergence between them flags execution problems (slippage, fills) instantly.

5.3 **Risk analytics on the dashboard**: per-trade cost drag, win rate by setup type,
regime-conditional performance, max adverse excursion per position, token cost per
cycle/day (now logged and ready to chart).

5.4 **Kill-switch tiers**: today it's all-or-nothing halt. Add: block-new-buys (keep
managing exits), reduce-size mode, and full halt — each triggerable from Telegram.

### Token budget strategy (applies throughout)

- Incremental caching (done) is the big one; verify `cache_read` dominates in the new
  per-cycle usage logs.
- Keep briefings computed-not-narrated: numbers and flags, no prose (regime flag,
  level analysis, event list are all *cheaper* than making Claude derive them).
- Batch tools aggressively (done for indicators/prices; apply to news next).
- Sonnet only where reasoning depth pays (9:30 + weekend); Haiku everywhere else.
- Budget guard: if a cycle exceeds N tokens (config), log + Telegram warn; the
  monthly-limit halt already exists.

### Suggested order of attack

| Priority | Item | Why first |
|----------|------|-----------|
| 1 | Rotate Kite secret + dashboard auth (1.1) | Real money is exposed today |
| 2 | GTT exchange-side stops (1.4) | Survives every failure mode you've had |
| 3 | Order journal + margin check (1.3, 1.5) | Kills the remaining phantom-trade paths |
| 4 | Level-analysis tool (3.1) | Biggest smartness-per-token win |
| 5 | Event calendar + regime flag (2.1, 2.3) | Makes it "aware" cheaply |
| 6 | News watcher thread (2.4) | Reacts in minutes like you asked |
| 7 | Hierarchical models (3.2) | Smarter mornings, flat cost |
| 8 | Weekend research v2 + scorecard (2.5) | Turns predictions into a learning loop |
| 9 | Attribution loop (3.3) | Compounds intelligence over months |
| 10 | Backtests + shadow mode (5.1, 5.2) | Confidence before scaling capital |
