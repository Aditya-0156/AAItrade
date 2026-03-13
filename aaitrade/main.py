"""CLI entry point for AAItrade.

Usage:
    aaitrade --capital 10000 --mode balanced --days 14 --watchlist config/watchlist_seed.yaml
    aaitrade --capital 10000 --mode aggressive --days 7 --execution paper
    aaitrade --multi config/multi_session.yaml
    aaitrade --recover
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from aaitrade.config import (
    APIKeys,
    ExecutionMode,
    SessionConfig,
    TradingMode,
)
from aaitrade.session_manager import SessionManager


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    # Create logs directory
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    # File handler
    from datetime import datetime
    log_file = log_dir / f"aaitrade_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )

    # Quiet down noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="aaitrade",
        description="AAItrade — Autonomous AI Trading System",
    )

    # Single session args
    parser.add_argument(
        "--capital",
        type=float,
        help="Starting capital in INR (e.g. 10000)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["safe", "balanced", "aggressive"],
        default="balanced",
        help="Trading mode (default: balanced)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Session duration in trading days (default: 14)",
    )
    parser.add_argument(
        "--execution",
        type=str,
        choices=["paper", "live"],
        default="paper",
        help="Execution mode (default: paper)",
    )
    parser.add_argument(
        "--watchlist",
        type=str,
        default="config/watchlist_seed.yaml",
        help="Path to watchlist YAML file",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Decision interval in minutes (default: 60)",
    )
    parser.add_argument(
        "--no-watchlist-adjust",
        action="store_true",
        help="Disable watchlist adjustment by Claude",
    )
    parser.add_argument(
        "--session-name",
        type=str,
        default=None,
        help="Human-readable name for this session",
    )

    # Multi-session mode
    parser.add_argument(
        "--multi",
        type=str,
        default=None,
        metavar="CONFIG",
        help="Path to multi-session YAML config (runs multiple sessions in parallel)",
    )

    # Crash recovery
    parser.add_argument(
        "--recover",
        action="store_true",
        help="Recover and resume active sessions from database",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug logging",
    )

    return parser.parse_args()


def main():
    args = parse_args()
    setup_logging(verbose=args.verbose)

    logger = logging.getLogger("aaitrade")

    # Load API keys
    keys = APIKeys.from_env()
    if not keys.anthropic:
        logger.error("ANTHROPIC_API_KEY not set. Add it to your .env file.")
        sys.exit(1)

    # Initialize Telegram bot (if configured)
    from aaitrade.telegram_bot import init_telegram
    bot = init_telegram()
    if bot:
        logger.info("Telegram bot initialized")

    # ── Multi-session mode ──
    if args.multi:
        multi_path = Path(args.multi)
        if not multi_path.exists():
            logger.error(f"Multi-session config not found: {multi_path}")
            sys.exit(1)

        from aaitrade.multi_session import MultiSessionRunner
        runner = MultiSessionRunner(keys)
        runner.start_from_config(multi_path)
        runner.wait()
        return

    # ── Crash recovery mode ──
    if args.recover:
        from aaitrade.multi_session import MultiSessionRunner
        runner = MultiSessionRunner(keys)
        runner.recover_active_sessions()
        if runner.get_active_sessions():
            logger.info(f"Recovered {len(runner.get_active_sessions())} sessions")
            runner.wait()
        else:
            logger.info("No sessions to recover.")
        return

    # ── Single session mode ──
    if not args.capital:
        logger.error("--capital is required for single-session mode. Use --multi or --recover for other modes.")
        sys.exit(1)

    # Validate watchlist path
    watchlist_path = Path(args.watchlist)
    if not watchlist_path.exists():
        logger.error(f"Watchlist file not found: {watchlist_path}")
        sys.exit(1)

    # Build session config
    config = SessionConfig(
        execution_mode=ExecutionMode(args.execution),
        trading_mode=TradingMode(args.mode),
        starting_capital=args.capital,
        total_days=args.days,
        watchlist_path=watchlist_path,
        allow_watchlist_adjustment=not args.no_watchlist_adjust,
        decision_interval_minutes=args.interval,
    )

    # Warn if running live
    if config.execution_mode == ExecutionMode.LIVE:
        logger.warning("=" * 60)
        logger.warning("⚠  LIVE MODE — REAL MONEY WILL BE USED")
        logger.warning(f"   Capital: ₹{config.starting_capital:,.2f}")
        logger.warning(f"   Mode: {config.trading_mode.value}")
        logger.warning("=" * 60)
        confirm = input("Type 'CONFIRM' to proceed with live trading: ")
        if confirm != "CONFIRM":
            logger.info("Live trading cancelled.")
            sys.exit(0)

    # Start session
    session = SessionManager(config, keys)
    session.start()
    session.run()


if __name__ == "__main__":
    main()
