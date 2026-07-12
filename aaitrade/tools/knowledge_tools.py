"""Knowledge tools — policy-beneficiary lookup and the connection graph.

These expose the knowledge engine to Claude: when policy news breaks, find
every listed beneficiary instantly; record sourced connections discovered
during research; recall what the graph already knows before trading.
"""

from __future__ import annotations

import logging

from aaitrade.tools import register_tool

logger = logging.getLogger(__name__)


@register_tool(
    name="find_policy_beneficiaries",
    description=(
        "Map a policy headline or theme to the NSE-500 companies that benefit. "
        "Pass any policy-related text ('cabinet raises ethanol blending target', "
        "'defence budget increased', 'RBI cuts repo rate') — it detects the "
        "policy theme(s) and returns listed beneficiaries across the WHOLE "
        "market with the reason (sector match, name match, or a researched "
        "connection-graph edge with source). Use this the moment policy/news "
        "breaks — the beneficiaries a human would take hours to list, you get "
        "instantly. Then run analyze_levels on the interesting ones."
    ),
    parameters={
        "properties": {
            "text": {
                "type": "string",
                "description": "Policy headline, news snippet, or theme description",
            },
        },
        "required": ["text"],
    },
)
def find_policy_beneficiaries(text: str) -> dict:
    from aaitrade.knowledge import match_themes, find_beneficiaries, POLICY_THEMES

    themes = match_themes(text)
    if not themes:
        return {
            "themes_detected": [],
            "message": (
                "No known policy theme matched. If this IS a real policy story, "
                "reason about affected sectors yourself and consider save_connection "
                "to teach the graph for next time."
            ),
        }
    out = {}
    for theme in themes[:3]:
        out[theme] = {
            "logic": POLICY_THEMES[theme]["note"],
            "beneficiaries": find_beneficiaries(theme),
        }
    return {"themes_detected": themes, "results": out}


@register_tool(
    name="save_connection",
    description=(
        "Record a factual, SOURCED connection in the knowledge graph — this is "
        "how the system learns who owns what and who pushes which policy. "
        "Examples: ('Person X', 'person', 'family_of', 'Minister Y', 'person'), "
        "('Company Z', 'company', 'benefits_from', 'ethanol_blending', "
        "'policy_theme'), ('Company Z', 'company', 'promoter_of', ...). "
        "Relations: family_of, promoter_of, director_of, owns_stake, "
        "ministry_of, pushes_policy, benefits_from, operates_in, supplies_to, "
        "linked_to. ALWAYS include the source (URL or citation) and an honest "
        "confidence (0.9 = stated in a filing/major outlet; 0.5 = single news "
        "mention; 0.3 = inference). PUBLIC information only."
    ),
    parameters={
        "properties": {
            "subject": {"type": "string", "description": "Entity name (person/company/ministry)"},
            "subject_type": {"type": "string", "enum": ["person", "company", "ministry", "policy_theme", "sector"]},
            "relation": {"type": "string", "description": "One of the valid relations"},
            "object": {"type": "string", "description": "Target entity name"},
            "object_type": {"type": "string", "enum": ["person", "company", "ministry", "policy_theme", "sector"]},
            "confidence": {"type": "number", "description": "0.0-1.0 honesty about certainty"},
            "source": {"type": "string", "description": "URL or citation for this fact"},
        },
        "required": ["subject", "subject_type", "relation", "object", "object_type", "confidence"],
    },
)
def save_connection(subject: str, subject_type: str, relation: str, object: str,
                    object_type: str, confidence: float, source: str = "") -> dict:
    from aaitrade.knowledge import add_edge
    return add_edge(subject, subject_type, relation, object, object_type, confidence, source)


@register_tool(
    name="find_connections",
    description=(
        "Query the connection graph for everything known about an entity — "
        "a company, person, ministry, or policy theme. Returns edges with "
        "confidence and sources. Call before trading a politically-sensitive "
        "name, or when news mentions a person/company and you want to know "
        "who they're linked to."
    ),
    parameters={
        "properties": {
            "name": {"type": "string", "description": "Entity name or partial name"},
        },
        "required": ["name"],
    },
)
def find_connections(name: str) -> dict:
    from aaitrade.knowledge import get_connections, graph_stats
    edges = get_connections(name)
    return {
        "entity": name,
        "connections": edges if edges else ["Nothing recorded yet about this entity."],
        "graph_size": graph_stats(),
    }
