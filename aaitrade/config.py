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
    human_alert_threshold: float = 25.0   # alert if single trade > 25% capital


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
    max_tool_calls_per_cycle: int = 40
    max_web_searches_per_cycle: int = 5   # No hard limit in prompt — Claude decides
    model: str = "claude-haiku-4-5-20251001"  # Default Haiku for cost (~73% cheaper); override per-session if needed

    # Derived
    risk_rules: RiskRules = field(init=False)
    mode_mandate: str = field(init=False)
    profit_reinvest_ratio: float = field(init=False)

    def __post_init__(self):
        self.risk_rules = RISK_PROFILES[self.trading_mode]
        self.mode_mandate = MODE_MANDATES[self.trading_mode]
        self.profit_reinvest_ratio = PROFIT_REINVEST_RATIO[self.trading_mode]


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
