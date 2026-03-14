"""Tests for the tool registry (tools/__init__.py)."""

import pytest
from aaitrade.tools import (
    _REGISTRY,
    call_tool,
    disable_tool,
    enable_tool,
    get_enabled_tools,
    get_tools_for_api,
    load_all_tools,
    register_tool,
)


class TestRegistration:
    def test_register_tool_adds_to_registry(self):
        @register_tool(
            name="_test_dummy_tool",
            description="A test tool",
            parameters={"properties": {"x": {"type": "integer"}}, "required": ["x"]},
        )
        def dummy(x: int):
            return x * 2

        assert "_test_dummy_tool" in _REGISTRY
        # Cleanup
        del _REGISTRY["_test_dummy_tool"]

    def test_load_all_tools_registers_expected_tools(self):
        load_all_tools()
        expected = {
            "get_current_price", "get_price_history", "get_indicators", "get_market_snapshot",
            "get_stock_news", "get_sector_news", "get_macro_news",
            "search_web",
            "get_portfolio", "get_cash",
            "get_trade_history", "get_session_summary",
            "write_trade_rationale", "get_open_positions_with_rationale",
            "update_thesis", "get_closed_trade_history",
            "get_watchlist",
            "get_session_memory", "update_session_memory",
        }
        registered = set(_REGISTRY.keys())
        assert expected.issubset(registered)


class TestEnableDisable:
    def setup_method(self):
        """Register a fresh test tool for isolation."""
        @register_tool(
            name="_test_toggle_tool",
            description="Toggle test",
            parameters={"properties": {}, "required": []},
        )
        def toggle():
            return "ok"

    def teardown_method(self):
        _REGISTRY.pop("_test_toggle_tool", None)

    def test_tool_enabled_by_default(self):
        assert _REGISTRY["_test_toggle_tool"].enabled is True

    def test_disable_tool_removes_from_enabled(self):
        disable_tool("_test_toggle_tool")
        assert "_test_toggle_tool" not in get_enabled_tools()

    def test_enable_tool_restores_it(self):
        disable_tool("_test_toggle_tool")
        enable_tool("_test_toggle_tool")
        assert "_test_toggle_tool" in get_enabled_tools()

    def test_disabled_tool_call_returns_error(self):
        disable_tool("_test_toggle_tool")
        result = call_tool("_test_toggle_tool", {})
        assert "error" in result
        enable_tool("_test_toggle_tool")

    def test_unknown_tool_call_returns_error(self):
        result = call_tool("nonexistent_tool_xyz", {})
        assert "error" in result


class TestCallTool:
    def setup_method(self):
        @register_tool(
            name="_test_echo_tool",
            description="Echo",
            parameters={"properties": {"msg": {"type": "string"}}, "required": ["msg"]},
        )
        def echo(msg: str):
            return {"echoed": msg}

    def teardown_method(self):
        _REGISTRY.pop("_test_echo_tool", None)

    def test_call_tool_executes_handler(self):
        result = call_tool("_test_echo_tool", {"msg": "hello"})
        assert result == {"echoed": "hello"}

    def test_call_tool_catches_exceptions(self):
        @register_tool(
            name="_test_fail_tool",
            description="Fails",
            parameters={"properties": {}, "required": []},
        )
        def fail():
            raise ValueError("intentional failure")

        result = call_tool("_test_fail_tool", {})
        assert "error" in result
        assert "intentional failure" in result["error"]
        del _REGISTRY["_test_fail_tool"]


class TestGetToolsForApi:
    def test_returns_correct_anthropic_schema(self):
        load_all_tools()
        tools = get_tools_for_api()
        assert isinstance(tools, list)
        assert len(tools) > 0
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert "type" in tool["input_schema"]
            assert "properties" in tool["input_schema"]
            assert "required" in tool["input_schema"]

    def test_disabled_tool_not_in_api_list(self):
        load_all_tools()
        disable_tool("search_web")
        tools = get_tools_for_api()
        names = [t["name"] for t in tools]
        assert "search_web" not in names
        enable_tool("search_web")
