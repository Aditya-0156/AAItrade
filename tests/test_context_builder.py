"""Tests for context_builder.py — prompt assembly."""

from unittest.mock import patch

import pytest

from aaitrade.context_builder import ContextBuilder
from tests.conftest import make_price


class TestSystemPrompt:
    def test_system_prompt_contains_mode(self, in_memory_db, balanced_config, session_with_watchlist):
        ctx = ContextBuilder(balanced_config, session_with_watchlist)
        prompt = ctx.build_system_prompt()
        assert "BALANCED" in prompt

    def test_system_prompt_contains_risk_rules(self, in_memory_db, balanced_config, session_with_watchlist):
        ctx = ContextBuilder(balanced_config, session_with_watchlist)
        prompt = ctx.build_system_prompt()
        # Balanced: max_per_trade=10, stop_loss=3, take_profit=5
        assert "10" in prompt
        assert "3" in prompt

    def test_system_prompt_contains_watchlist_stocks(self, in_memory_db, balanced_config, session_with_watchlist):
        ctx = ContextBuilder(balanced_config, session_with_watchlist)
        prompt = ctx.build_system_prompt()
        assert "RELIANCE" in prompt
        assert "TCS" in prompt

    def test_system_prompt_contains_trading_mindset(self, in_memory_db, balanced_config, session_with_watchlist):
        ctx = ContextBuilder(balanced_config, session_with_watchlist)
        prompt = ctx.build_system_prompt()
        assert "TRADING MINDSET" in prompt
        assert "news reactor" in prompt.lower() or "sophisticated" in prompt.lower()

    def test_system_prompt_contains_strategies(self, in_memory_db, balanced_config, session_with_watchlist):
        ctx = ContextBuilder(balanced_config, session_with_watchlist)
        prompt = ctx.build_system_prompt()
        assert "Oversold Bounce" in prompt
        assert "Breakout" in prompt
        assert "Sector Rotation" in prompt

    def test_system_prompt_watchlist_adjust_block_enabled(self, in_memory_db, balanced_config, session_with_watchlist):
        ctx = ContextBuilder(balanced_config, session_with_watchlist)
        prompt = ctx.build_system_prompt()
        assert "add_to_watchlist" in prompt or "add or remove" in prompt.lower()

    def test_system_prompt_watchlist_adjust_block_disabled(self, in_memory_db, safe_config, session_with_watchlist):
        """Safe config with no watchlist adjustment."""
        from aaitrade.config import SessionConfig, ExecutionMode, TradingMode
        config = SessionConfig(
            execution_mode=ExecutionMode.PAPER,
            trading_mode=TradingMode.SAFE,
            starting_capital=10_000,
            total_days=10,
            watchlist_path="config/watchlist_seed.yaml",
            allow_watchlist_adjustment=False,
        )
        ctx = ContextBuilder(config, session_with_watchlist)
        prompt = ctx.build_system_prompt()
        assert "fixed" in prompt.lower() or "not available" in prompt.lower()

    def test_system_prompt_contains_json_array_output_format(self, in_memory_db, balanced_config, session_with_watchlist):
        ctx = ContextBuilder(balanced_config, session_with_watchlist)
        prompt = ctx.build_system_prompt()
        assert "JSON array" in prompt or "json array" in prompt.lower()
        assert "[{" in prompt  # Shows array example

    def test_system_prompt_mentions_session_memory_tools(self, in_memory_db, balanced_config, session_with_watchlist):
        ctx = ContextBuilder(balanced_config, session_with_watchlist)
        prompt = ctx.build_system_prompt()
        assert "get_session_memory" in prompt
        assert "update_session_memory" in prompt


class TestBriefing:
    def test_briefing_contains_cycle_number(self, in_memory_db, balanced_config, session_with_watchlist):
        ctx = ContextBuilder(balanced_config, session_with_watchlist)
        with patch("aaitrade.context_builder.get_market_snapshot", return_value={"error": "no kite"}), \
             patch("aaitrade.context_builder.get_current_price", return_value={"error": "no kite"}), \
             patch("aaitrade.tools.memory.get_session_summary", return_value={"error": "no data"}), \
             patch("aaitrade.tools.journal.get_open_positions_with_rationale", return_value={"total": 0, "open_positions": []}):
            briefing = ctx.build_briefing(7)
        assert "7" in briefing

    def test_briefing_shows_no_positions_when_empty(self, in_memory_db, balanced_config, session_with_watchlist):
        ctx = ContextBuilder(balanced_config, session_with_watchlist)
        with patch("aaitrade.context_builder.get_market_snapshot", return_value={"error": "no kite"}), \
             patch("aaitrade.context_builder.get_current_price", return_value={"error": "no kite"}), \
             patch("aaitrade.tools.memory.get_session_summary", return_value={"error": "no data"}), \
             patch("aaitrade.tools.journal.get_open_positions_with_rationale", return_value={"total": 0, "open_positions": []}):
            briefing = ctx.build_briefing(1)
        assert "No open positions" in briefing

    def test_briefing_shows_open_positions(self, in_memory_db, balanced_config, session_with_watchlist):
        import aaitrade.db as db
        from aaitrade.tools import journal, portfolio_tools
        journal.set_session_id(session_with_watchlist)
        portfolio_tools.set_session_id(session_with_watchlist)
        db.insert("trade_journal", {
            "session_id": session_with_watchlist,
            "symbol": "RELIANCE", "entry_price": 2800, "reason": "r",
            "news_cited": "[]", "key_thesis": "Oversold Bounce",
            "target_price": 2940, "stop_price": 2716,
            "status": "open", "opened_at": db.now_iso(),
        })
        db.insert("portfolio", {
            "session_id": session_with_watchlist,
            "symbol": "RELIANCE", "quantity": 5, "avg_price": 2800,
            "stop_loss_price": 2716, "take_profit_price": 2940,
            "opened_at": db.now_iso(),
        })
        ctx = ContextBuilder(balanced_config, session_with_watchlist)
        with patch("aaitrade.context_builder.get_market_snapshot", return_value={"error": "no kite"}), \
             patch("aaitrade.context_builder.get_current_price", return_value={"error": "no kite"}), \
             patch("aaitrade.tools.memory.get_session_summary", return_value={"error": "no data"}):
            briefing = ctx.build_briefing(2)
        assert "RELIANCE" in briefing
        assert "Oversold Bounce" in briefing
