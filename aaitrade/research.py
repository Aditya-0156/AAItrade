"""Off-day research — weekend/holiday deep scan producing a next-session outlook.

Markets close, the world doesn't. On Saturdays, Sundays, and holidays this
module gathers global news, geopolitics, policy moves, and market wraps, then
asks Claude to predict how the NEXT Indian trading session will react:
which chains fire (oil spike → inflation → FII outflow), which sectors move,
what to watch at the open.

The result is cached in news_cache (category='outlook') and injected into
every briefing of the following trading day by the context builder.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade import db
from aaitrade.holidays import next_trading_day

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)

_RESEARCH_QUERIES = [
    "major geopolitical events this weekend affecting global financial markets",
    "India stock market next week outlook RBI government policy announcements",
    "US markets Friday close summary — what it means for Asian markets Monday",
    "FII DII flows Indian equities latest trend and outlook",
]

_OUTLOOK_SYSTEM = (
    "You are the weekend research analyst for AAItrade, an autonomous swing-trading "
    "system on the Indian NSE. Your job: digest weekend/overnight information and "
    "predict how the NEXT Indian trading session will react. Indian markets react "
    "fast to global events — trace the causal chains explicitly "
    "(e.g. 'oil +4% on Hormuz tensions → OMCs and airlines gap down, upstream ONGC/OIL "
    "gap up → broader inflation fear → FII selling pressure on Nifty'). "
    "Be concrete and falsifiable, not vague. If information is thin, say so."
)

_OUTLOOK_PROMPT_TEMPLATE = """Today is {today} (market closed). Next trading session: {next_day}.

RAW INTELLIGENCE GATHERED:

── Global market levels (latest available) ──
{global_context}

── Macro headlines ──
{macro_news}

── Web research ──
{search_results}

── Your prediction track record ──
{calibration}

Produce a NEXT-SESSION OUTLOOK (max 400 words) with exactly these sections:
1. WEEKEND DEVELOPMENTS — the 3-5 things that actually matter for Indian markets.
2. EXPECTED OPEN — directional bias for Nifty at {next_day} open (gap up / flat / gap down) with the causal chain behind it.
3. SECTOR & STOCK IMPACT MAP — which NSE sectors benefit / get hurt and why. Name specific large-cap symbols where confident.
4. EVENTS AHEAD — scheduled events next week (central bank meetings, data releases, earnings, policy deadlines) that could move markets.
5. RISK FLAGS — what would invalidate this outlook; what to check at 9:15 AM.

Calibrate against your track record above — if you have been over-calling direction, lean FLAT unless the evidence is strong.

Write it as plain text (no markdown headers). This goes directly into the trading agent's morning briefing.
Then end with EXACTLY one machine-readable line (this is scored against the actual open):
BIAS: GAP_UP or FLAT or GAP_DOWN"""


def _gather_intelligence() -> dict:
    """Collect global context, macro news, and web research. All best-effort."""
    intel = {"global_context": "Unavailable", "macro_news": "Unavailable", "search_results": "Unavailable"}

    try:
        from aaitrade.tools.market import get_global_context
        gctx = get_global_context()
        if "error" not in gctx:
            lines = []
            for name, data in gctx.items():
                if not isinstance(data, dict) or "error" in data:
                    continue
                chg = data.get("change_pct")
                chg_str = f"{chg:+.2f}%" if chg is not None else "n/a"
                lines.append(f"{name}: {data['price']} ({chg_str})")
            intel["global_context"] = " | ".join(lines) or "Unavailable"
    except Exception as e:
        logger.warning(f"Research: global context failed: {e}")

    try:
        from aaitrade.tools.news import get_macro_news
        macro = get_macro_news()
        intel["macro_news"] = macro.get("summary", "Unavailable")
    except Exception as e:
        logger.warning(f"Research: macro news failed: {e}")

    try:
        from aaitrade.tools.search import search_web, _tavily_client
        if _tavily_client:
            chunks = []
            for q in _RESEARCH_QUERIES:
                result = search_web(q)
                answer = result.get("answer") or ""
                if not answer and result.get("sources"):
                    answer = "; ".join(s["snippet"] for s in result["sources"][:2])
                if answer:
                    chunks.append(f"[{q}]\n{answer}")
            intel["search_results"] = "\n\n".join(chunks) or "No search results"
    except Exception as e:
        logger.warning(f"Research: web search failed: {e}")

    return intel


def run_offday_research(claude_client, session_id: int, model: str | None = None) -> str | None:
    """Run the research cycle and cache the outlook for the next trading day.

    Returns the outlook text, or None on failure. `model` overrides the
    client's default (planning model — research is where reasoning pays).
    """
    now = datetime.now(_IST)
    next_day = next_trading_day(now.date())
    intel = _gather_intelligence()

    from aaitrade.lessons import prediction_calibration
    prompt = _OUTLOOK_PROMPT_TEMPLATE.format(
        today=now.strftime("%A %d %B %Y"),
        next_day=next_day.strftime("%A %d %B"),
        global_context=intel["global_context"],
        macro_news=intel["macro_news"],
        search_results=intel["search_results"],
        calibration=prediction_calibration(),
    )

    try:
        response = claude_client.client.messages.create(
            model=model or claude_client.model,
            max_tokens=2048,
            temperature=0.3,
            system=_OUTLOOK_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        outlook = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
    except Exception as e:
        logger.error(f"Research: Claude outlook generation failed: {e}")
        return None

    if not outlook:
        return None

    # Extract the machine-readable open-bias line for later scoring
    bias = "FLAT"
    for line in reversed(outlook.splitlines()):
        if line.strip().upper().startswith("BIAS:"):
            candidate = line.split(":", 1)[1].strip().upper().replace(" ", "_")
            if candidate in ("GAP_UP", "FLAT", "GAP_DOWN"):
                bias = candidate
            break

    # Valid until the next trading day's close — every briefing that day sees it
    expires = datetime.combine(next_day, datetime.min.time()).replace(
        hour=15, minute=30, tzinfo=_IST
    )
    db.insert("news_cache", {
        "category": "outlook",
        "key": "next_session",
        "summary": outlook,
        "source": "offday_research",
        "fetched_at": db.now_iso(),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    db.insert("news_cache", {
        "category": "outlook",
        "key": "bias",
        "summary": bias,
        "source": "offday_research",
        "fetched_at": db.now_iso(),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S"),
    })

    logger.info(f"Off-day research saved: outlook for {next_day} (bias {bias}, {len(outlook)} chars)")
    return outlook
