"""Knowledge engine — policy→sector→stock mapping and the connection graph.

The edge humans miss isn't secret information; it's public information nobody
cross-references. This module makes those cross-references mechanical:

Phase A — POLICY THEMES: a curated map of Indian policy themes → keywords →
industries/company-name patterns. When policy news mentions "ethanol blending",
the system instantly lists every NSE-500 company that benefits — across the
whole market, not just the watchlist. Deterministic, zero tokens.

Phase B — CONNECTION GRAPH: entities (people, companies, ministries, themes)
and relations (promoter_of, family_of, pushes_policy, benefits_from...)
accumulated by weekend research with source citations and confidence scores.
Compounds weekly: in months, the system knows who owns what and who pushes
which policy — assembled entirely from public information.

Phase C (lite) — targeted promoter/shareholding research feeds the same graph.
Structured exchange-filing ingestion is the roadmap successor.

LEGAL LINE: public data only. Reading news, filings, and public records faster
than humans is research. Non-public information is insider trading — never.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)


# ── Phase A: curated Indian policy themes ─────────────────────────────────
# keywords: matched (case-insensitive) against news/headlines to detect a theme
# industries: substring match on stock_universe.industry
# name_keywords: substring match on company names (catches "Balrampur Chini"
#                for sugar/ethanol where the industry column is too coarse)

POLICY_THEMES: dict[str, dict] = {
    "ethanol_blending": {
        "keywords": ["ethanol", "e20", "blending", "sugarcane", "flex fuel", "biofuel"],
        "industries": [],
        "name_keywords": ["sugar", "chini", "distiller", "renuka", "bajaj hindusthan", "praj", "triveni"],
        "note": "Ethanol blending push → sugar mills/distillery/equipment plays",
    },
    "defence_procurement": {
        "keywords": ["defence", "defense", "drdo", "military", "border", "make in india defence",
                     "defence exports", "missile", "artillery", "conflict", "war"],
        "industries": ["Capital Goods"],
        "name_keywords": ["hindustan aeronautics", "bharat electronics", "bharat dynamics",
                          "mazagon", "cochin shipyard", "garden reach", "bel ", "data patterns",
                          "paras defence", "astra microwave", "zen technologies", "solar industries"],
        "note": "Defence budget/procurement/conflict → defence PSUs and suppliers",
    },
    "railways_capex": {
        "keywords": ["railway", "vande bharat", "rail capex", "dedicated freight", "metro project"],
        "industries": [],
        "name_keywords": ["ircon", "rvnl", "rail vikas", "railtel", "irfc", "rites",
                          "titagarh", "texmaco", "jupiter wagons", "brls", "concor", "container corp"],
        "note": "Railway budget/orders → rail PSUs, wagon makers, financiers",
    },
    "solar_renewables": {
        "keywords": ["solar", "renewable", "green energy", "pli solar", "rooftop", "green hydrogen"],
        "industries": ["Power"],
        "name_keywords": ["suzlon", "inox wind", "waaree", "premier energies", "acme solar",
                          "adani green", "tata power", "nhpc", "sjvn", "iex", "borosil renew"],
        "note": "Renewable policy/PLI/green hydrogen → gencos, wind/solar manufacturers",
    },
    "ev_battery": {
        "keywords": ["electric vehicle", " ev ", "fame scheme", "battery", "lithium", "charging infrastructure"],
        "industries": ["Automobile and Auto Components"],
        "name_keywords": ["ola electric", "exide", "amara raja", "tata motors", "m&m",
                          "mahindra", "tvs motor", "bajaj auto", "sona blw", "uno minda"],
        "note": "EV subsidies/FAME → OEMs with EV lines, battery makers, component suppliers",
    },
    "semiconductors_pli": {
        "keywords": ["semiconductor", "chip fab", "pli scheme", "electronics manufacturing"],
        "industries": ["Consumer Durables", "Capital Goods"],
        "name_keywords": ["dixon", "amber", "kaynes", "syrma", "cg power", "tejas networks"],
        "note": "Electronics/semiconductor PLI → EMS players and fab-adjacent names",
    },
    "roads_infra": {
        "keywords": ["highway", "nhai", "road construction", "infrastructure spending", "bharatmala"],
        "industries": ["Construction"],
        "name_keywords": ["larsen", "irb", "knr", "pnc infratech", "hg infra", "gr infra",
                          "ncc", "ultratech", "ambuja", "shree cement", "dalmia"],
        "note": "Road/infra budget → EPC contractors and cement",
    },
    "psu_banks": {
        "keywords": ["psu bank", "recapitalisation", "public sector bank", "npa", "bank privatisation"],
        "industries": ["Financial Services"],
        "name_keywords": ["state bank", "bank of baroda", "punjab national", "canara bank",
                          "union bank", "indian bank", "bank of india", "uco bank"],
        "note": "PSU bank policy/recap/privatisation → PSU bank basket moves together",
    },
    "oil_price": {
        "keywords": ["crude", "opec", "oil price", "brent", "windfall tax", "petrol diesel price"],
        "industries": ["Oil Gas & Consumable Fuels"],
        "name_keywords": ["ongc", "oil india", "indian oil", "bpcl", "hpcl", "reliance",
                          "petronet", "gail", "gujarat gas", "igl", "mgl"],
        "note": "Crude spikes hurt OMCs/airlines/paints, help upstream (ONGC/OIL); falls reverse it",
    },
    "pharma_regulation": {
        "keywords": ["usfda", "fda approval", "drug pricing", "pharma pli", "api manufacturing"],
        "industries": ["Healthcare"],
        "name_keywords": ["sun pharma", "cipla", "dr. reddy", "lupin", "aurobindo",
                          "zydus", "divi", "laurus", "gland", "biocon"],
        "note": "FDA actions/pricing policy → stock-specific pharma moves; PLI → API makers",
    },
    "it_us_policy": {
        "keywords": ["h-1b", "h1b", "visa", "us tech spending", "outsourcing", "us recession"],
        "industries": ["Information Technology"],
        "name_keywords": ["tcs", "infosys", "wipro", "hcl", "tech mahindra", "ltimindtree",
                          "persistent", "coforge", "mphasis"],
        "note": "US visa/spending policy → whole IT pack, midcap IT amplified",
    },
    "telecom_tariff": {
        "keywords": ["tariff hike", "spectrum", "5g", "agr dues", "telecom policy"],
        "industries": ["Telecommunication"],
        "name_keywords": ["bharti airtel", "vodafone idea", "indus towers", "tejas networks", "hfcl"],
        "note": "Tariff/spectrum policy → telcos and tower/equipment suppliers",
    },
    "fertilizer_subsidy": {
        "keywords": ["fertiliser subsidy", "fertilizer subsidy", "urea", "dap", "msp", "farm income"],
        "industries": ["Chemicals"],
        "name_keywords": ["chambal", "coromandel", "gnfc", "gsfc", "rcf", "fact",
                          "national fertilizer", "paradeep"],
        "note": "Subsidy/MSP announcements → fertilizer and agrochem names",
    },
    "rate_sensitive": {
        "keywords": ["rbi rate", "repo rate", "rate cut", "rate hike", "monetary policy", "cash reserve ratio"],
        "industries": ["Realty", "Financial Services"],
        "name_keywords": ["dlf", "godrej properties", "oberoi realty", "prestige", "lodha",
                          "brigade", "bajaj finance", "chola", "shriram finance", "lic housing"],
        "note": "Rate decisions → NBFCs, housing finance, realty most sensitive",
    },
    "power_transmission": {
        "keywords": ["power demand", "transmission", "smart meter", "discom", "electricity amendment"],
        "industries": ["Power"],
        "name_keywords": ["ntpc", "power grid", "tata power", "torrent power", "jsw energy",
                          "adani power", "genus power", "hpl electric", "transformers"],
        "note": "Power reform/demand surge → gencos, transmission, smart-meter plays",
    },
    "china_metals": {
        "keywords": ["china stimulus", "steel import", "anti-dumping", "import duty steel", "commodity rally"],
        "industries": ["Metals & Mining"],
        "name_keywords": ["tata steel", "jsw steel", "jindal steel", "sail", "nmdc",
                          "hindalco", "vedanta", "nalco", "hind zinc"],
        "note": "China stimulus/duties → the whole metals pack; duties help domestic steel",
    },
}


# ── Universe loading ──────────────────────────────────────────────────────


def refresh_stock_universe(csv_text: str) -> int:
    """Parse the NSE-500 constituents CSV into stock_universe. Returns count."""
    count = 0
    now = db.now_iso()
    for line in csv_text.splitlines()[1:]:
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) < 3 or not parts[2]:
            continue
        company, industry, symbol = parts[0], parts[1], parts[2]
        try:
            db.upsert("stock_universe", {
                "symbol": symbol, "company_name": company,
                "industry": industry, "updated_at": now,
            }, conflict_columns=["symbol"])
            count += 1
        except Exception:
            pass
    logger.info(f"Stock universe refreshed: {count} companies with sectors")
    return count


# ── Phase A: theme matching + beneficiary lookup ──────────────────────────


def match_themes(text: str) -> list[str]:
    """Return theme names whose keywords appear in the text."""
    low = f" {text.lower()} "
    return [name for name, t in POLICY_THEMES.items()
            if any(k in low for k in t["keywords"])]


def find_beneficiaries(theme: str, limit: int = 12) -> list[dict]:
    """Listed NSE-500 companies that benefit from a policy theme, with reasons.

    Sources: industry match, company-name match, and graph edges
    (benefits_from → theme entity).
    """
    t = POLICY_THEMES.get(theme)
    if not t:
        return []
    results: dict[str, str] = {}  # symbol -> reason

    rows = db.query("SELECT symbol, company_name, industry FROM stock_universe")
    for r in rows:
        name_low = (r["company_name"] or "").lower()
        ind = r["industry"] or ""
        if any(nk in name_low for nk in t["name_keywords"]):
            results.setdefault(r["symbol"], f"name match ({r['company_name']})")
        elif any(i in ind for i in t["industries"]):
            results.setdefault(r["symbol"], f"sector match ({ind})")

    # Graph edges: X benefits_from theme (accumulated by research)
    try:
        theme_ent = db.query_one(
            "SELECT id FROM entities WHERE name = ? AND etype = 'policy_theme'", (theme,)
        )
        if theme_ent:
            edge_rows = db.query(
                "SELECT e.confidence, e.source, s.name FROM edges e "
                "JOIN entities s ON s.id = e.src_id "
                "WHERE e.dst_id = ? AND e.relation = 'benefits_from' AND e.confidence >= 0.4",
                (theme_ent["id"],),
            )
            for er in edge_rows:
                sym = _company_to_symbol(er["name"])
                if sym:
                    results[sym] = f"graph: benefits_from ({er['confidence']:.1f} conf, {er['source'] or 'unsourced'})"
    except Exception:
        pass

    out = [{"symbol": s, "reason": reason} for s, reason in results.items()]
    return out[:limit]


def _company_to_symbol(name: str) -> str | None:
    """Best-effort map of a company name (or symbol) to an NSE symbol."""
    row = db.query_one(
        "SELECT symbol FROM stock_universe WHERE symbol = ? "
        "OR company_name LIKE ? LIMIT 1",
        (name.upper(), f"%{name}%"),
    )
    return row["symbol"] if row else None


def policy_signals_block(max_themes: int = 2) -> str:
    """Briefing-ready section: themes detected in today's cached news, with
    beneficiary shortlists. Deterministic — zero tokens spent detecting."""
    texts = []
    for cat, key in [("macro", "macro"), ("outlook", "next_session")]:
        row = db.query_one(
            "SELECT summary FROM news_cache WHERE category = ? AND key = ? "
            "ORDER BY fetched_at DESC LIMIT 1", (cat, key),
        )
        if row:
            texts.append(row["summary"])
    if not texts:
        return ""

    themes = match_themes(" ".join(texts))
    if not themes:
        return ""

    lines = []
    for theme in themes[:max_themes]:
        bens = find_beneficiaries(theme, limit=6)
        if not bens:
            continue
        symbols = ", ".join(f"{b['symbol']}" for b in bens)
        lines.append(f"  {theme} ({POLICY_THEMES[theme]['note']}): {symbols}")
    if not lines:
        return ""
    return (
        "\n\n⚡ POLICY SIGNALS (themes detected in today's news → NSE-500 beneficiaries; "
        "verify with find_policy_beneficiaries + analyze_levels):\n" + "\n".join(lines)
    )


# ── Phase B: connection graph ─────────────────────────────────────────────

VALID_RELATIONS = {
    "family_of", "promoter_of", "director_of", "owns_stake", "ministry_of",
    "pushes_policy", "benefits_from", "operates_in", "supplies_to", "linked_to",
}


def get_or_create_entity(name: str, etype: str) -> int:
    name = name.strip()
    row = db.query_one(
        "SELECT id FROM entities WHERE name = ? AND etype = ?", (name, etype)
    )
    if row:
        return row["id"]
    return db.insert("entities", {
        "name": name, "etype": etype, "created_at": db.now_iso(),
    })


def add_edge(src_name: str, src_type: str, relation: str, dst_name: str,
             dst_type: str, confidence: float = 0.5, source: str = "") -> dict:
    relation = relation.strip().lower()
    if relation not in VALID_RELATIONS:
        return {"error": f"Unknown relation '{relation}'. Valid: {sorted(VALID_RELATIONS)}"}
    confidence = max(0.0, min(1.0, confidence))
    src_id = get_or_create_entity(src_name, src_type)
    dst_id = get_or_create_entity(dst_name, dst_type)
    try:
        db.insert("edges", {
            "src_id": src_id, "relation": relation, "dst_id": dst_id,
            "confidence": confidence, "source": source[:300],
            "observed_at": db.now_iso(), "created_at": db.now_iso(),
        })
    except Exception:
        # Duplicate edge — refresh confidence/source instead
        row = db.query_one(
            "SELECT id, confidence FROM edges WHERE src_id = ? AND relation = ? AND dst_id = ?",
            (src_id, relation, dst_id),
        )
        if row:
            db.update("edges", row["id"], {
                "confidence": max(row["confidence"], confidence),
                "source": source[:300] or None,
                "observed_at": db.now_iso(),
            })
    return {"status": "saved", "edge": f"{src_name} —{relation}→ {dst_name} ({confidence:.1f})"}


def get_connections(name: str, max_edges: int = 25) -> list[str]:
    """All edges touching entities whose name matches (case-insensitive)."""
    rows = db.query(
        "SELECT s.name as src, s.etype as st, e.relation, d.name as dst, d.etype as dt, "
        "e.confidence, e.source "
        "FROM edges e JOIN entities s ON s.id = e.src_id JOIN entities d ON d.id = e.dst_id "
        "WHERE s.name LIKE ? OR d.name LIKE ? "
        "ORDER BY e.confidence DESC LIMIT ?",
        (f"%{name}%", f"%{name}%", max_edges),
    )
    return [
        f"{r['src']} ({r['st']}) —{r['relation']}→ {r['dst']} ({r['dt']}) "
        f"[conf {r['confidence']:.1f}{'; ' + r['source'][:80] if r['source'] else ''}]"
        for r in rows
    ]


def graph_stats() -> str:
    e = db.query_one("SELECT COUNT(*) as n FROM entities")
    g = db.query_one("SELECT COUNT(*) as n FROM edges")
    return f"{e['n'] if e else 0} entities, {g['n'] if g else 0} edges"
