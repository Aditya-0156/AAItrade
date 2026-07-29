"""Configuration for AAItrade sessions, risk rules, and trading modes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()


# ── Enums ──────────────────────────────────────────────────────────────────────


class ExecutionMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class TradingMode(str, Enum):
    SAFE = "safe"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


# ── Risk Rules ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskRules:
    """Risk parameters that vary by trading mode.

    All percentages are expressed as floats (e.g. 7.0 means 7%).
    """

    max_per_trade: float          # max % of capital on a single trade
    stop_loss: float              # exit if position drops by this %
    take_profit: float            # take profit at this % gain
    max_positions: int            # max simultaneous open positions
    max_deployed: float           # max % of capital deployed at once
    daily_loss_limit: float       # halt trading if day loss hits this %

    # Session-level (universal — same for all modes)
    session_stop_loss: float = 40.0       # halt session at 40% drawdown

    # Hard cap: one position may never lose more than this % of effective
    # capital. Enforced by the price monitor with a FORCED exit — this is the
    # rule that stops one runaway loser from eating a month of 1% wins.
    max_position_loss_pct: float = 1.5


RISK_PROFILES: dict[TradingMode, RiskRules] = {
    TradingMode.SAFE: RiskRules(
        max_per_trade=15.0,  # ₹3,000 of ₹20,000
        stop_loss=2.0,
        take_profit=4.0,
        max_positions=4,
        max_deployed=90.0,
        daily_loss_limit=3.0,
    ),
    TradingMode.BALANCED: RiskRules(
        max_per_trade=20.0,  # ₹4,000 of ₹20,000
        stop_loss=3.0,
        take_profit=5.0,
        max_positions=5,
        max_deployed=90.0,
        daily_loss_limit=5.0,
    ),
    TradingMode.AGGRESSIVE: RiskRules(
        max_per_trade=25.0,  # ₹5,000 of ₹20,000
        stop_loss=5.0,
        take_profit=8.0,
        max_positions=6,
        max_deployed=90.0,
        daily_loss_limit=8.0,
    ),
}


# ── Mode Mandates (for system prompt injection) ───────────────────────────────


MODE_MANDATES: dict[TradingMode, str] = {
    TradingMode.SAFE: (
        "Preserve capital above all else. Your primary objective is to avoid "
        "significant losses while generating modest, consistent gains. Take profits "
        "early and move them to the secured pot. Only enter trades with high "
        "conviction and clear setups. When in doubt, HOLD. Never chase momentum."
    ),
    TradingMode.BALANCED: (
        "Balance capital growth with protection. Reinvest 50% of realised profits, "
        "secure 50%. Enter trades with moderate-to-high conviction. Be selective "
        "— quality over quantity. Review open positions critically each cycle."
    ),
    TradingMode.AGGRESSIVE: (
        "Maximise total return by compounding profits back into new positions. "
        "Accept wider price swings in pursuit of larger gains. Be bold but not "
        "reckless — every trade must still have a clear thesis and respect all "
        "hard risk rules. Aggressive means high-conviction, not impulsive."
    ),
}


# ── Profit Handling ────────────────────────────────────────────────────────────


PROFIT_REINVEST_RATIO: dict[TradingMode, float] = {
    TradingMode.SAFE: 0.0,       # 0% reinvested, 100% secured
    TradingMode.BALANCED: 0.5,   # 50/50 split
    TradingMode.AGGRESSIVE: 1.0, # 100% reinvested
}


# ── Watchlist Entry ────────────────────────────────────────────────────────────


@dataclass
class WatchlistEntry:
    symbol: str
    company: str
    sector: str
    notes: str = ""


def load_watchlist(path: str | Path) -> list[WatchlistEntry]:
    """Load watchlist from a YAML config file."""
    path = Path(path)
    with open(path) as f:
        data = yaml.safe_load(f)

    entries = []
    for item in data.get("watchlist", []):
        entries.append(
            WatchlistEntry(
                symbol=item["symbol"],
                company=item.get("company", ""),
                sector=item.get("sector", ""),
                notes=item.get("notes", ""),
            )
        )
    return entries


# ── Session Config ─────────────────────────────────────────────────────────────


@dataclass
class SessionConfig:
    """Complete configuration for a trading session."""

    execution_mode: ExecutionMode
    trading_mode: TradingMode
    starting_capital: float
    total_days: int
    watchlist_path: str | Path
    allow_watchlist_adjustment: bool = True
    decision_interval_minutes: int = 90  # 4 cycles/day: ~9:30, ~11:00, ~12:30, ~14:00
    max_tool_calls_per_cycle: int = 30
    max_web_searches_per_cycle: int = 5   # No hard limit in prompt — Claude decides
    model: str = "claude-haiku-4-5-20251001"  # Execution cycles: Haiku (cheap, mechanical work)
    # Planning model: used for the 9:30 AM observe/plan cycle and weekend
    # research — the two places where reasoning depth actually pays. One
    # Sonnet planning cycle/day costs roughly as much as the other three
    # Haiku cycles combined; set equal to `model` to disable tiering.
    planning_model: str = "claude-sonnet-5"
    profit_reinvest_ratio: float = 0.5  # 0.0=secure all profit, 1.0=reinvest all profit
    # Symbol separation from the user's own manual trading.
    # False (default): the system MAY trade the same symbols the user holds.
    #   Its books stay fully separate — it only ever sells what it itself
    #   bought, never adopts the user's shares, never counts them in P&L.
    #   Caveat the user has accepted: the broker pools shares per symbol and
    #   disposes FIFO, so a system sell in a shared symbol is booked against
    #   the user's older tax lots (share count and money are unaffected).
    # True: the system refuses to trade any symbol the user holds.
    exclude_user_symbols: bool = False

    # Apply realistic Zerodha CNC charges (STT, stamp, DP, slippage) to paper
    # trades. Without this, paper P&L overstates reality — the 0.5-1% profit
    # targets get materially eaten by ~0.25-0.5% round-trip costs on small trades.
    charges_enabled: bool = True

    # Derived
    risk_rules: RiskRules = field(init=False)
    mode_mandate: str = field(init=False)

    def __post_init__(self):
        self.risk_rules = RISK_PROFILES[self.trading_mode]
        self.mode_mandate = MODE_MANDATES[self.trading_mode]


# ── API Keys ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class APIKeys:
    """API keys loaded from environment."""

    anthropic: str
    kite_api_key: str
    kite_api_secret: str
    kite_access_token: str
    newsapi: str
    tavily: str
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def from_env(cls) -> APIKeys:
        return cls(
            anthropic=os.environ.get("ANTHROPIC_API_KEY", ""),
            kite_api_key=os.environ.get("KITE_API_KEY", ""),
            kite_api_secret=os.environ.get("KITE_API_SECRET", ""),
            kite_access_token=os.environ.get("KITE_ACCESS_TOKEN", ""),
            newsapi=os.environ.get("NEWSAPI_KEY", ""),
            tavily=os.environ.get("TAVILY_API_KEY", ""),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        )
