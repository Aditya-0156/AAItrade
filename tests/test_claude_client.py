"""Tests for claude_client.py — the Anthropic API tool-use loop.

All Anthropic API calls are mocked. Tests cover: JSON parsing robustness,
tool-use loop iteration, rate limit handling, monthly limit handling,
tool round exhaustion, and decision DB logging.
"""

import json
from unittest.mock import MagicMock, call, patch

import pytest

from aaitrade.claude_client import ClaudeClient
import aaitrade.db as db


@pytest.fixture
def client(in_memory_db):
    return ClaudeClient(api_key="test-key", model="claude-haiku-4-5-20251001", max_tool_rounds=4)


def make_text_response(text):
    """Simulate Claude returning a final text response."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def make_tool_use_response(tool_name, tool_input, tool_id="tool_1"):
    """Simulate Claude calling a tool."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = tool_id
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response


# ── JSON parsing ───────────────────────────────────────────────────────────────

class TestParseDecision:
    def test_parse_array_json(self, client):
        text = '[{"action": "BUY", "symbol": "RELIANCE", "quantity": 5, "stop_loss_price": 2700.0, "take_profit_price": 2940.0, "reason": "test", "confidence": "high", "flags": []}]'
        result = client._parse_decision(text)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["action"] == "BUY"

    def test_parse_single_object_wraps_in_list(self, client):
        text = '{"action": "HOLD", "symbol": null, "quantity": null, "stop_loss_price": null, "take_profit_price": null, "reason": "waiting", "confidence": "low", "flags": []}'
        result = client._parse_decision(text)
        assert isinstance(result, list)
        assert result[0]["action"] == "HOLD"

    def test_parse_multiple_decisions(self, client):
        text = '[{"action": "BUY", "symbol": "RELIANCE", "quantity": 2, "stop_loss_price": 2700.0, "take_profit_price": 2940.0, "reason": "r1", "confidence": "high", "flags": []}, {"action": "SELL", "symbol": "TCS", "quantity": 3, "stop_loss_price": null, "take_profit_price": null, "reason": "r2", "confidence": "high", "flags": []}]'
        result = client._parse_decision(text)
        assert len(result) == 2
        assert result[0]["action"] == "BUY"
        assert result[1]["action"] == "SELL"

    def test_parse_json_embedded_in_text(self, client):
        text = 'Here is my decision:\n[{"action": "HOLD", "symbol": null, "quantity": null, "stop_loss_price": null, "take_profit_price": null, "reason": "no setup", "confidence": "low", "flags": []}]\nThat is all.'
        result = client._parse_decision(text)
        assert isinstance(result, list)
        assert result[0]["action"] == "HOLD"

    def test_parse_failure_returns_hold_with_alert(self, client):
        result = client._parse_decision("This is not JSON at all")
        assert isinstance(result, list)
        assert result[0]["action"] == "HOLD"
        assert "ALERT_USER" in result[0]["flags"]

    def test_parse_empty_string_returns_hold(self, client):
        result = client._parse_decision("")
        assert result[0]["action"] == "HOLD"

    def test_parse_markdown_fenced_json(self, client):
        text = '```json\n[{"action": "BUY", "symbol": "INFY", "quantity": 3, "stop_loss_price": 1400.0, "take_profit_price": 1600.0, "reason": "r", "confidence": "medium", "flags": []}]\n```'
        result = client._parse_decision(text)
        assert result[0]["action"] == "BUY"


# ── Tool-use loop ──────────────────────────────────────────────────────────────

class TestToolUseLoop:
    def test_direct_end_turn_returns_decisions(self, client, in_memory_db, session_with_watchlist):
        decision_json = '[{"action": "HOLD", "symbol": null, "quantity": null, "stop_loss_price": null, "take_profit_price": null, "reason": "quiet market", "confidence": "low", "flags": []}]'
        mock_response = make_text_response(decision_json)

        with patch.object(client.client.messages, "create", return_value=mock_response):
            result = client.make_decision("sys", "briefing", session_with_watchlist, 1)

        assert isinstance(result, list)
        assert result[0]["action"] == "HOLD"

    def test_tool_use_then_end_turn(self, client, in_memory_db, session_with_watchlist):
        """Claude calls one tool then makes a decision."""
        from aaitrade.tools import load_all_tools
        load_all_tools()

        tool_response = make_tool_use_response(
            "get_session_memory", {}, "tool_1"
        )
        final_json = '[{"action": "HOLD", "symbol": null, "quantity": null, "stop_loss_price": null, "take_profit_price": null, "reason": "done", "confidence": "low", "flags": []}]'
        final_response = make_text_response(final_json)

        with patch.object(client.client.messages, "create", side_effect=[tool_response, final_response]), \
             patch("aaitrade.tools.session_memory._session_id", session_with_watchlist):
            result = client.make_decision("sys", "briefing", session_with_watchlist, 1)

        assert result[0]["action"] == "HOLD"

    def test_exhausted_tool_rounds_returns_hold(self, client, in_memory_db, session_with_watchlist):
        """When max_tool_rounds exceeded, returns HOLD."""
        from aaitrade.tools import load_all_tools
        load_all_tools()

        # Always return tool_use (never end_turn)
        tool_response = make_tool_use_response("get_session_memory", {}, "t1")

        with patch.object(client.client.messages, "create", return_value=tool_response), \
             patch("aaitrade.tools.session_memory._session_id", session_with_watchlist):
            result = client.make_decision("sys", "briefing", session_with_watchlist, 1)

        assert result[0]["action"] == "HOLD"
        assert "Exhausted" in result[0]["reason"]

    def test_decisions_logged_to_db(self, client, in_memory_db, session_with_watchlist):
        decision_json = '[{"action": "BUY", "symbol": "RELIANCE", "quantity": 2, "stop_loss_price": 2700.0, "take_profit_price": 2940.0, "reason": "good setup", "confidence": "high", "flags": []}]'
        mock_response = make_text_response(decision_json)

        with patch.object(client.client.messages, "create", return_value=mock_response):
            client.make_decision("sys", "briefing", session_with_watchlist, 3)

        decisions = db.query(
            "SELECT * FROM decisions WHERE session_id = ?", (session_with_watchlist,)
        )
        assert len(decisions) == 1
        assert decisions[0]["action"] == "BUY"
        assert decisions[0]["cycle_number"] == 3

    def test_multiple_decisions_each_logged(self, client, in_memory_db, session_with_watchlist):
        decision_json = '[{"action": "BUY", "symbol": "RELIANCE", "quantity": 2, "stop_loss_price": 2700.0, "take_profit_price": 2940.0, "reason": "r1", "confidence": "high", "flags": []}, {"action": "SELL", "symbol": "TCS", "quantity": 5, "stop_loss_price": null, "take_profit_price": null, "reason": "r2", "confidence": "high", "flags": []}]'
        mock_response = make_text_response(decision_json)

        with patch.object(client.client.messages, "create", return_value=mock_response):
            client.make_decision("sys", "briefing", session_with_watchlist, 1)

        decisions = db.query(
            "SELECT action FROM decisions WHERE session_id = ?", (session_with_watchlist,)
        )
        actions = [d["action"] for d in decisions]
        assert "BUY" in actions
        assert "SELL" in actions


# ── Error handling ─────────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_rate_limit_retries_then_hold(self, client, in_memory_db, session_with_watchlist):
        import anthropic
        with patch.object(
            client.client.messages, "create",
            side_effect=anthropic.RateLimitError.__new__(anthropic.RateLimitError)
        ), patch("time.sleep"):  # Don't actually sleep
            result = client.make_decision("sys", "briefing", session_with_watchlist, 1)

        assert result[0]["action"] == "HOLD"
        assert "Rate limit" in result[0]["reason"]

    def test_monthly_api_limit_returns_halt_session(self, client, in_memory_db, session_with_watchlist):
        import anthropic

        # BadRequestError needs response, body, and request args — use a real-ish subclass
        class FakeSpendLimitError(anthropic.BadRequestError):
            def __init__(self):
                pass
            def __str__(self):
                return "reached your specified API usage limits"

        with patch.object(client.client.messages, "create", side_effect=FakeSpendLimitError()):
            result = client.make_decision("sys", "briefing", session_with_watchlist, 1)

        assert result[0]["action"] == "HOLD"
        assert "HALT_SESSION" in result[0]["flags"]

    def test_unexpected_stop_reason_returns_hold(self, client, in_memory_db, session_with_watchlist):
        mock_response = MagicMock()
        mock_response.stop_reason = "max_tokens"
        mock_response.content = []

        with patch.object(client.client.messages, "create", return_value=mock_response):
            result = client.make_decision("sys", "briefing", session_with_watchlist, 1)

        assert result[0]["action"] == "HOLD"

    def test_tool_call_logged_to_db(self, client, in_memory_db, session_with_watchlist):
        from aaitrade.tools import load_all_tools
        load_all_tools()

        tool_response = make_tool_use_response("get_session_memory", {}, "t1")
        final_json = '[{"action": "HOLD", "symbol": null, "quantity": null, "stop_loss_price": null, "take_profit_price": null, "reason": "done", "confidence": "low", "flags": []}]'
        final_response = make_text_response(final_json)

        with patch.object(client.client.messages, "create", side_effect=[tool_response, final_response]), \
             patch("aaitrade.tools.session_memory._session_id", session_with_watchlist):
            client.make_decision("sys", "briefing", session_with_watchlist, 2)

        tool_calls = db.query(
            "SELECT * FROM tool_calls WHERE session_id = ?", (session_with_watchlist,)
        )
        assert len(tool_calls) >= 1
        assert tool_calls[0]["tool_name"] == "get_session_memory"
