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
from aaitrade.claude_client import sampling_kwargs

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
        target_model = model or claude_client.model
        response = claude_client.client.messages.create(
            model=target_model,
            max_tokens=2048,
            **sampling_kwargs(target_model, 0.3),
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

    # Phase B/C: expand the connection graph from this week's research material
    try:
        _expand_connection_graph(claude_client, intel, model=model)
    except Exception as e:
        logger.warning(f"Connection-graph expansion failed: {e}")

    return outlook


# ── Connection-graph expansion (Phase B + C-lite) ─────────────────────────

_EXTRACTION_SYSTEM = (
    "You extract factual, sourced connections from research material for a "
    "public-information knowledge graph about Indian markets and policy. "
    "PUBLIC information only. Only extract what the material actually states — "
    "no speculation. Be honest with confidence: 0.9 = stated in a filing or "
    "major outlet, 0.5 = single news mention, 0.3 = weak inference."
)

_EXTRACTION_PROMPT = """From the research material below, extract connections worth remembering for trading Indian equities.

Valid relations: family_of, promoter_of, director_of, owns_stake, ministry_of, pushes_policy, benefits_from, operates_in, supplies_to, linked_to
Valid entity types: person, company, ministry, policy_theme, sector
Known policy themes (use these names when they fit): {themes}

── MATERIAL ──
{material}

Output ONLY a JSON array (may be empty) of objects:
[{{"subject": "...", "subject_type": "...", "relation": "...", "object": "...", "object_type": "...", "confidence": 0.6, "source": "short citation"}}]

Extract at most 12 high-value connections. Skip anything already obvious from a company's name or sector."""


def _expand_connection_graph(claude_client, intel: dict, model: str | None = None):
    """One weekly Claude call: turn research material into graph edges.

    Also runs Phase C-lite: promoter/ownership mini-research on two rotating
    top-scanner names, so the ownership map deepens a little every week.
    """
    material_parts = [intel.get("macro_news", ""), intel.get("search_results", "")]

    # Phase C-lite: promoter research for 2 rotating top scanner picks
    try:
        from aaitrade.tools.search import search_web, _tavily_client
        if _tavily_client:
            top = db.query(
                "SELECT symbol FROM scan_results WHERE scan_date = "
                "(SELECT MAX(scan_date) FROM scan_results) ORDER BY rank LIMIT 10",
            )
            if top:
                week = datetime.now(_IST).isocalendar()[1]
                picks = [top[week % len(top)]["symbol"],
                         top[(week + 5) % len(top)]["symbol"]]
                for sym in dict.fromkeys(picks):
                    r = search_web(f"{sym} NSE company promoter family owners political connections")
                    ans = r.get("answer") or ""
                    if ans:
                        material_parts.append(f"[promoter research: {sym}]\n{ans}")
    except Exception as e:
        logger.warning(f"Promoter mini-research failed: {e}")

    material = "\n\n".join(p for p in material_parts if p)[:12000]
    if len(material) < 200:
        return

    from aaitrade.knowledge import POLICY_THEMES, add_edge
    prompt = _EXTRACTION_PROMPT.format(
        themes=", ".join(POLICY_THEMES.keys()), material=material,
    )

    target_model = model or claude_client.model
    response = claude_client.client.messages.create(
        model=target_model,
        max_tokens=1500,
        **sampling_kwargs(target_model, 0.1),
        system=_EXTRACTION_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in response.content if hasattr(b, "text")).strip()

    import json as _json
    start, end = text.find("["), text.rfind("]") + 1
    if start < 0 or end <= start:
        return
    try:
        triples = _json.loads(text[start:end])
    except Exception:
        logger.warning("Graph extraction: could not parse JSON output")
        return

    saved = 0
    for t in triples[:12]:
        try:
            result = add_edge(
                t["subject"], t["subject_type"], t["relation"],
                t["object"], t["object_type"],
                float(t.get("confidence", 0.4)), t.get("source", ""),
            )
            if result.get("status") == "saved":
                saved += 1
        except Exception:
            continue
    logger.info(f"Connection graph: +{saved} edges from weekly research")
