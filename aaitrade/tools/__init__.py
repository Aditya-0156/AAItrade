"""Tool registry for AAItrade.

Plug-and-play pattern: each tool module registers functions via the
@register_tool decorator. The Claude client reads from this registry
to build the tools list for the Anthropic API.

To add a new tool:
1. Create a function in an existing or new tools/*.py file
2. Decorate it with @register_tool(name, description, parameters)
3. Import the module in _TOOL_MODULES below
4. Done — Claude sees it on the next run
"""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """A registered tool that Claude can call."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for parameters
    handler: Callable[..., Any]
    enabled: bool = True


# Global registry — all tools register here
_REGISTRY: dict[str, ToolDefinition] = {}


def register_tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
):
    """Decorator to register a function as a Claude-callable tool."""

    def decorator(func: Callable) -> Callable:
        _REGISTRY[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            handler=func,
        )
        return func

    return decorator


def get_all_tools() -> dict[str, ToolDefinition]:
    """Return all registered tools."""
    return dict(_REGISTRY)


def get_enabled_tools() -> dict[str, ToolDefinition]:
    """Return only enabled tools."""
    return {k: v for k, v in _REGISTRY.items() if v.enabled}


def disable_tool(name: str):
    """Disable a tool by name (it won't appear in Claude's tool list)."""
    if name in _REGISTRY:
        _REGISTRY[name].enabled = False


def enable_tool(name: str):
    """Enable a tool by name."""
    if name in _REGISTRY:
        _REGISTRY[name].enabled = True


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Execute a tool by name with the given arguments."""
    if name not in _REGISTRY:
        return {"error": f"Unknown tool: {name}"}

    tool = _REGISTRY[name]
    if not tool.enabled:
        return {"error": f"Tool '{name}' is currently disabled"}

    try:
        result = tool.handler(**arguments)
        return result
    except Exception as e:
        logger.error(f"Tool '{name}' failed: {e}", exc_info=True)
        return {"error": f"Tool '{name}' failed: {str(e)}"}


def get_tools_for_api() -> list[dict]:
    """Return tool definitions formatted for the Anthropic API."""
    tools = []
    for tool in get_enabled_tools().values():
        tools.append({
            "name": tool.name,
            "description": tool.description,
            "input_schema": {
                "type": "object",
                "properties": tool.parameters.get("properties", {}),
                "required": tool.parameters.get("required", []),
            },
        })
    return tools


# ── Auto-import tool modules to trigger registration ───────────────────────────

_TOOL_MODULES = [
    "aaitrade.tools.market",
    "aaitrade.tools.news",
    "aaitrade.tools.search",
    "aaitrade.tools.portfolio_tools",
    "aaitrade.tools.memory",
    "aaitrade.tools.journal",
    "aaitrade.tools.watchlist_tools",
    "aaitrade.tools.session_memory",
]


def load_all_tools():
    """Import all tool modules so their @register_tool decorators fire."""
    for module_name in _TOOL_MODULES:
        try:
            importlib.import_module(module_name)
            logger.debug(f"Loaded tool module: {module_name}")
        except ImportError as e:
            logger.warning(f"Could not load tool module {module_name}: {e}")
