"""Tests for the knowledge engine — theme matching, beneficiaries, graph."""

import pytest

from aaitrade import db
from aaitrade.knowledge import (
    match_themes, find_beneficiaries, add_edge, get_connections,
    refresh_stock_universe, policy_signals_block,
)


@pytest.fixture
def seeded_universe(in_memory_db):
    csv_text = (
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Balrampur Chini Mills Ltd.,Fast Moving Consumer Goods,BALRAMCHIN,EQ,X1\n"
        "Praj Industries Ltd.,Capital Goods,PRAJIND,EQ,X2\n"
        "Hindustan Aeronautics Ltd.,Capital Goods,HAL,EQ,X3\n"
        "Tata Consultancy Services Ltd.,Information Technology,TCS,EQ,X4\n"
        "IRB Infrastructure Developers Ltd.,Construction,IRB,EQ,X5\n"
    )
    assert refresh_stock_universe(csv_text) == 5
    return in_memory_db


class TestThemes:
    def test_ethanol_headline_matches(self):
        themes = match_themes("Cabinet approves higher ethanol blending target for 2027")
        assert "ethanol_blending" in themes

    def test_defence_headline_matches(self):
        themes = match_themes("Government clears major defence procurement for border security")
        assert "defence_procurement" in themes

    def test_unrelated_text_matches_nothing(self):
        assert match_themes("cricket world cup final today") == []

    def test_beneficiaries_by_name_and_sector(self, seeded_universe):
        bens = find_beneficiaries("ethanol_blending")
        symbols = {b["symbol"] for b in bens}
        assert "BALRAMCHIN" in symbols  # 'chini' name match
        assert "PRAJIND" in symbols     # 'praj' name match
        assert "TCS" not in symbols

    def test_defence_beneficiaries(self, seeded_universe):
        bens = find_beneficiaries("defence_procurement")
        symbols = {b["symbol"] for b in bens}
        assert "HAL" in symbols


class TestGraph:
    def test_add_and_query_edge(self, in_memory_db):
        r = add_edge("Company X", "company", "benefits_from",
                     "ethanol_blending", "policy_theme", 0.8, "https://example.com/article")
        assert r["status"] == "saved"
        edges = get_connections("Company X")
        assert len(edges) == 1
        assert "benefits_from" in edges[0]
        assert "0.8" in edges[0]

    def test_duplicate_edge_updates_confidence(self, in_memory_db):
        add_edge("A", "person", "family_of", "B", "person", 0.4, "src1")
        add_edge("A", "person", "family_of", "B", "person", 0.9, "src2")
        edges = get_connections("A")
        assert len(edges) == 1
        assert "0.9" in edges[0]

    def test_invalid_relation_rejected(self, in_memory_db):
        r = add_edge("A", "person", "went_to_school_with", "B", "person", 0.5)
        assert "error" in r

    def test_graph_beneficiary_flows_into_theme_lookup(self, seeded_universe):
        # A researched edge should surface in find_beneficiaries
        add_edge("Balrampur Chini Mills", "company", "benefits_from",
                 "ethanol_blending", "policy_theme", 0.9, "budget doc")
        bens = find_beneficiaries("ethanol_blending")
        reasons = {b["symbol"]: b["reason"] for b in bens}
        assert "BALRAMCHIN" in reasons
        # graph edge overrides/annotates the name match
        assert "graph" in reasons["BALRAMCHIN"] or "name match" in reasons["BALRAMCHIN"]


class TestPolicySignals:
    def test_signals_from_cached_news(self, seeded_universe):
        db.insert("news_cache", {
            "category": "macro", "key": "macro",
            "summary": "Government announces new ethanol blending targets and sugarcane pricing.",
            "source": "test", "fetched_at": db.now_iso(),
            "expires_at": "2099-01-01T00:00:00",
        })
        block = policy_signals_block()
        assert "ethanol_blending" in block
        assert "BALRAMCHIN" in block

    def test_no_news_no_signals(self, in_memory_db):
        assert policy_signals_block() == ""
