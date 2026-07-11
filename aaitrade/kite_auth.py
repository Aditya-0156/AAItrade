"""Kite token lifecycle — validate, exchange, persist, and propagate tokens.

One shared code path for "a new token arrived", used by:
- the dashboard API (server.update_kite_token)
- the Telegram /token command
- the pre-market token health check

Credentials come from the environment (KITE_API_KEY / KITE_API_SECRET) —
never hardcode them; this repo is public.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _api_key() -> str:
    key = os.environ.get("KITE_API_KEY", "")
    if not key:
        raise RuntimeError("KITE_API_KEY not set in environment/.env")
    return key


def _api_secret() -> str:
    secret = os.environ.get("KITE_API_SECRET", "")
    if not secret:
        raise RuntimeError("KITE_API_SECRET not set in environment/.env")
    return secret


def resolve_access_token(token: str) -> str:
    """Turn user input into a working access_token.

    Both access_tokens and request_tokens are ~32 chars, so probe:
    try it as an access_token first; if Kite rejects it, exchange it
    as a request_token (valid ~2 minutes after login).
    """
    from kiteconnect import KiteConnect

    probe = KiteConnect(api_key=_api_key())
    probe.set_access_token(token)
    try:
        probe.profile()
        logger.info("Token validated as access_token directly")
        return token
    except Exception:
        pass

    data = probe.generate_session(token, api_secret=_api_secret())
    logger.info("Converted request_token to access_token")
    return data["access_token"]


def persist_token(token: str):
    """Write KITE_ACCESS_TOKEN to os.environ and the .env file."""
    os.environ["KITE_ACCESS_TOKEN"] = token
    try:
        env_content = _ENV_PATH.read_text() if _ENV_PATH.exists() else ""
        lines = env_content.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("KITE_ACCESS_TOKEN="):
                lines[i] = f"KITE_ACCESS_TOKEN={token}"
                break
        else:
            lines.append(f"KITE_ACCESS_TOKEN={token}")
        _ENV_PATH.write_text("\n".join(lines))
        logger.info("Token persisted to .env file")
    except Exception as e:
        logger.error(f"Failed to persist token to .env: {e}")


def build_client(token: str):
    """Create and validate a KiteConnect client for the given access token."""
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=_api_key(), timeout=15)
    kite.set_access_token(token)
    profile = kite.profile()
    logger.info(f"Kite token validated — logged in as {profile['user_name']}")
    return kite


def propagate_client(kite):
    """Push a fresh Kite client into every module that holds one."""
    from aaitrade.tools.market import set_kite_client as set_market_kite
    from aaitrade.tools.watchlist_tools import set_kite_client as set_watchlist_kite
    from aaitrade.executor import set_kite_client as set_executor_kite

    set_market_kite(kite)      # also rebuilds the instrument cache
    set_watchlist_kite(kite)
    set_executor_kite(kite)


def apply_kite_token(token: str) -> dict:
    """Full flow: resolve → persist → build client → propagate.

    Returns {"status": "ok"|"error", "message": str}.
    Price monitors of running sessions are NOT updated here — the server
    layer does that, since only it knows the running managers.
    """
    try:
        access_token = resolve_access_token(token)
    except Exception as e:
        return {
            "status": "error",
            "message": (
                f"Failed to validate/exchange token: {e}. "
                "Request tokens expire ~2 minutes after login — get a fresh one."
            ),
        }

    persist_token(access_token)

    try:
        kite = build_client(access_token)
        propagate_client(kite)
    except Exception as e:
        return {"status": "error", "message": f"Token invalid or Kite API error: {e}"}

    return {"status": "ok", "message": "Token updated, applied live, and persisted to .env"}


def check_token_health() -> tuple[bool, str]:
    """Check whether the current Kite session is alive. Returns (ok, message)."""
    from aaitrade.tools.market import _kite, _kite_lock

    if _kite is None:
        return False, "Kite client not initialized (no token loaded)"
    try:
        with _kite_lock:
            profile = _kite.profile()
        return True, f"Token valid — {profile.get('user_name', 'unknown user')}"
    except Exception as e:
        return False, f"Kite token dead: {e}"
