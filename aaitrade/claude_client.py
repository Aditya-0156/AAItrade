"""Claude client — sends prompts with tool-use to the Anthropic API.

Handles the full tool-use loop: send message → Claude calls tools →
execute tools → return results → Claude makes final decision.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime

import anthropic

from aaitrade import db
from aaitrade.tools import call_tool, get_tools_for_api

logger = logging.getLogger(__name__)


class ClaudeClient:
    """Manages communication with Claude for trading decisions."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-haiku-4-5-20251001",  # Default to Haiku for cost (~73% cheaper)
        max_tool_rounds: int = 8,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model  # Can be overridden per-session via config
        self.max_tool_rounds = max_tool_rounds

    def make_decision(
        self,
        system_prompt: str,
        briefing: str,
        session_id: int,
        cycle_number: int,
    ) -> dict:
        """Run a full decision cycle with tool-use.

        Returns the parsed JSON decision from Claude.
        Uses prompt caching for system prompt to reduce costs ~65%.
        """
        tools = get_tools_for_api()

        messages = [{"role": "user", "content": briefing}]

        # Tool-use loop: Claude may call tools multiple times before deciding
        for round_num in range(self.max_tool_rounds):
            # Retry on rate limit with exponential backoff
            for attempt in range(4):
                try:
                    response = self.client.messages.create(
                        model=self.model,
                        max_tokens=4096,
                        system=[
                            {
                                "type": "text",
                                "text": system_prompt,
                                "cache_control": {"type": "ephemeral"}
                            }
                        ],
                        tools=tools,
                        messages=messages,
                    )
                    break
                except anthropic.RateLimitError:
                    wait = 15 * (2 ** attempt)  # 15s, 30s, 60s, 120s
                    logger.warning(f"Rate limit hit — waiting {wait}s before retry {attempt + 1}/4")
                    time.sleep(wait)
            else:
                logger.error("Rate limit retries exhausted — returning HOLD")
                return {"action": "HOLD", "symbol": None, "quantity": None,
                        "stop_loss_price": None, "take_profit_price": None,
                        "reason": "Rate limit — too many concurrent sessions", "confidence": "low", "flags": []}

            # Check if Claude wants to use tools
            if response.stop_reason == "tool_use":
                # Process all tool calls in this response
                tool_results = []
                assistant_content = response.content

                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        tool_use_id = block.id

                        logger.info(
                            f"Cycle {cycle_number} | Tool call: {tool_name}({tool_input})"
                        )

                        # Execute the tool
                        result = call_tool(tool_name, tool_input)

                        # Log tool call to DB
                        db.insert("tool_calls", {
                            "session_id": session_id,
                            "cycle_number": cycle_number,
                            "tool_name": tool_name,
                            "parameters": json.dumps(tool_input),
                            "result_summary": json.dumps(result)[:500],
                            "called_at": db.now_iso(),
                        })

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": json.dumps(result),
                        })

                # Add assistant response and tool results to conversation
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_results})

            elif response.stop_reason == "end_turn":
                # Claude is done — extract the final text
                decision_text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        decision_text += block.text

                # Parse JSON decision
                decision = self._parse_decision(decision_text)

                # Log the decision
                db.insert("decisions", {
                    "session_id": session_id,
                    "cycle_number": cycle_number,
                    "action": decision.get("action", "PARSE_ERROR"),
                    "symbol": decision.get("symbol"),
                    "quantity": decision.get("quantity"),
                    "reason": decision.get("reason", ""),
                    "confidence": decision.get("confidence", ""),
                    "flags": json.dumps(decision.get("flags", [])),
                    "raw_json": decision_text,
                    "decided_at": db.now_iso(),
                })

                return decision

            else:
                logger.warning(f"Unexpected stop_reason: {response.stop_reason}")
                break

        # If we exhaust tool rounds, return a HOLD
        logger.warning(f"Cycle {cycle_number}: exhausted {self.max_tool_rounds} tool rounds")
        return {
            "action": "HOLD",
            "symbol": None,
            "quantity": None,
            "stop_loss_price": None,
            "take_profit_price": None,
            "reason": "Exhausted tool call budget without reaching a decision.",
            "confidence": "low",
            "flags": [],
        }

    def _parse_decision(self, text: str) -> dict:
        """Parse Claude's JSON output into a decision dict."""
        text = text.strip()

        # Try to extract JSON from the text
        # Claude should output only JSON, but handle edge cases
        try:
            # Direct parse
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON in the text (between { and })
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

        # Parse failure — return error HOLD
        logger.error(f"Failed to parse Claude output as JSON: {text[:200]}")
        return {
            "action": "HOLD",
            "symbol": None,
            "quantity": None,
            "stop_loss_price": None,
            "take_profit_price": None,
            "reason": f"PARSE ERROR: Could not parse Claude output as JSON.",
            "confidence": "low",
            "flags": ["ALERT_USER"],
        }

    def generate_eod_summary(
        self,
        system_prompt: str,
        summary_prompt: str,
    ) -> str:
        """Generate end-of-day summary (no tools needed)."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": summary_prompt}],
        )

        for block in response.content:
            if hasattr(block, "text"):
                return block.text

        return "End-of-day summary generation failed."
