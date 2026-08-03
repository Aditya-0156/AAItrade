"""Candidate pipeline — research that spans days instead of collapsing into one cycle.

The conviction session is meant to study a name for several days before
committing. That only works if partial research survives between cycles, so
each candidate carries a stage, a running set of findings, and the numbers
that will define the trade when it is finally taken.

Stages: watching → researching → ready → entered / rejected
"""

from __future__ import annotations

import logging

from aaitrade.tools import register_tool
from aaitrade import db

logger = logging.getLogger(__name__)

_STAGES = ("watching", "researching", "ready", "entered", "rejected")

_session_id: int | None = None


def set_session_id(session_id: int):
    global _session_id
    _session_id = session_id


@register_tool(
    name="update_candidate",
    description=(
        "Record or advance a research candidate. This is your memory across "
        "days — use it every time you learn something about a name, even if "
        "you are nowhere near buying.\n\n"
        "Stages: 'watching' (caught your eye), 'researching' (actively working "
        "it), 'ready' (research done, waiting on price/timing), 'entered' "
        "(bought), 'rejected' (you did the work and said no — record WHY so "
        "you don't research it again next week).\n\n"
        "`findings` is APPENDED with a timestamp each call, so a picture builds "
        "over several days. Set target_pct / stop_pct / horizon_days once the "
        "amplitude analysis gives you real numbers, and conviction (1-10) as "
        "your honest confidence."
    ),
    parameters={
        "properties": {
            "symbol": {"type": "string", "description": "NSE symbol"},
            "stage": {"type": "string", "enum": list(_STAGES)},
            "findings": {
                "type": "string",
                "description": "What you learned this cycle — appended to the running notes",
            },
            "thesis": {
                "type": "string",
                "description": "The one-line case: why it's cheap and what re-rates it",
            },
            "target_pct": {"type": "number", "description": "Intended gain %"},
            "stop_pct": {"type": "number", "description": "Stop distance % (from analyse_amplitude)"},
            "horizon_days": {"type": "integer", "description": "Trading days you expect to hold"},
            "conviction": {"type": "integer", "description": "Honest confidence, 1-10"},
        },
        "required": ["symbol", "stage", "findings"],
    },
)
def update_candidate(symbol: str, stage: str, findings: str, thesis: str = "",
                     target_pct: float | None = None, stop_pct: float | None = None,
                     horizon_days: int | None = None,
                     conviction: int | None = None) -> dict:
    if _session_id is None:
        return {"status": "error", "reason": "Session not initialised"}
    symbol = symbol.upper().strip()
    stage = stage.lower().strip()
    if stage not in _STAGES:
        return {"status": "rejected", "reason": f"stage must be one of {list(_STAGES)}"}

    stamp = db.now_iso()[:16].replace("T", " ")
    entry = f"[{stamp}] {findings.strip()}"

    existing = db.query_one(
        "SELECT id, findings, stage FROM candidates WHERE session_id = ? AND symbol = ?",
        (_session_id, symbol),
    )
    updates = {"stage": stage, "updated_at": db.now_iso()}
    if thesis:
        updates["thesis"] = thesis
    for k, v in (("target_pct", target_pct), ("stop_pct", stop_pct),
                 ("horizon_days", horizon_days), ("conviction", conviction)):
        if v is not None:
            updates[k] = v

    if existing:
        prior = existing["findings"] or ""
        # Keep the notes bounded — recent research matters most
        combined = (prior + "\n" + entry).strip()
        if len(combined) > 4000:
            combined = combined[-4000:]
        updates["findings"] = combined
        db.update("candidates", existing["id"], updates)
        moved = existing["stage"] != stage
        return {
            "status": "updated",
            "symbol": symbol,
            "stage": stage,
            "stage_changed": moved,
            "days_in_pipeline": _days_tracked(existing["id"]),
            "message": f"{symbol}: {existing['stage']} → {stage}" if moved else f"{symbol} notes added ({stage})",
        }

    db.insert("candidates", {
        "session_id": _session_id, "symbol": symbol, "stage": stage,
        "thesis": thesis or None, "target_pct": target_pct, "stop_pct": stop_pct,
        "horizon_days": horizon_days, "conviction": conviction,
        "findings": entry, "created_at": db.now_iso(), "updated_at": db.now_iso(),
    })
    return {"status": "created", "symbol": symbol, "stage": stage,
            "message": f"{symbol} added to pipeline at stage '{stage}'"}


def _days_tracked(cand_id: int) -> int:
    row = db.query_one("SELECT created_at FROM candidates WHERE id = ?", (cand_id,))
    if not row:
        return 0
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    try:
        created = datetime.fromisoformat(row["created_at"]).replace(tzinfo=ist)
        return (datetime.now(ist) - created).days
    except Exception:
        return 0


@register_tool(
    name="get_pipeline",
    description=(
        "Review your research pipeline — everything you are watching, "
        "researching, or have declared ready, with the running notes and how "
        "many days each has been tracked. Call this at the START of every "
        "cycle: finishing research already underway beats starting new "
        "research from scratch."
    ),
    parameters={
        "properties": {
            "stage": {
                "type": "string",
                "description": "Filter to one stage (optional). Omit for the live pipeline.",
            },
        },
        "required": [],
    },
)
def get_pipeline(stage: str | None = None) -> dict:
    if _session_id is None:
        return {"status": "error", "reason": "Session not initialised"}
    if stage:
        rows = db.query(
            "SELECT * FROM candidates WHERE session_id = ? AND stage = ? ORDER BY updated_at DESC",
            (_session_id, stage.lower()),
        )
    else:
        # Live work only — entered/rejected are history, not the working set
        rows = db.query(
            "SELECT * FROM candidates WHERE session_id = ? "
            "AND stage IN ('watching','researching','ready') ORDER BY "
            "CASE stage WHEN 'ready' THEN 1 WHEN 'researching' THEN 2 ELSE 3 END, "
            "COALESCE(conviction, 0) DESC",
            (_session_id,),
        )

    out = []
    for r in rows:
        out.append({
            "symbol": r["symbol"], "stage": r["stage"],
            "conviction": r["conviction"], "thesis": r["thesis"],
            "target_pct": r["target_pct"], "stop_pct": r["stop_pct"],
            "horizon_days": r["horizon_days"],
            "days_tracked": _days_tracked(r["id"]),
            "findings": r["findings"],
        })
    counts = db.query(
        "SELECT stage, COUNT(*) AS n FROM candidates WHERE session_id = ? GROUP BY stage",
        (_session_id,),
    )
    return {
        "candidates": out,
        "count": len(out),
        "stage_counts": {c["stage"]: c["n"] for c in counts},
        "message": "Nothing in the pipeline — start by screening the scanner list."
                   if not out else f"{len(out)} candidate(s) in progress.",
    }


def pipeline_briefing_block(session_id: int, limit: int = 8) -> str:
    """Compact pipeline summary for the cycle briefing."""
    rows = db.query(
        "SELECT symbol, stage, conviction, target_pct, horizon_days, thesis, created_at "
        "FROM candidates WHERE session_id = ? AND stage IN ('watching','researching','ready') "
        "ORDER BY CASE stage WHEN 'ready' THEN 1 WHEN 'researching' THEN 2 ELSE 3 END, "
        "COALESCE(conviction,0) DESC LIMIT ?",
        (session_id, limit),
    )
    if not rows:
        return "Pipeline empty — nothing under research yet."
    lines = []
    for r in rows:
        tgt = f"target {r['target_pct']}%" if r["target_pct"] else "no target yet"
        conv = f"conviction {r['conviction']}/10" if r["conviction"] else "unrated"
        thesis = (r["thesis"] or "")[:70]
        lines.append(f"  {r['symbol']:12} [{r['stage']}] {conv}, {tgt} — {thesis}")
    return "\n".join(lines)
