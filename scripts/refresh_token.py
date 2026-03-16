"""Daily Kite token refresh — run this every morning before 9:15 AM IST.

Usage:
    .venv/bin/python scripts/refresh_token.py <request_token>

Steps:
    1. Exchanges request_token for access_token via Kite API
    2. SSHs into the server and updates KITE_ACCESS_TOKEN in .env
    3. Signals the running app to reload the token (no restart needed)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY    = "9dz93b78apapfn1l"
API_SECRET = "071tnt5srh72p63b96mh8s8btw9gogyk"  # regenerate on Zerodha dev console

SERVER_USER    = "ubuntu"
SERVER_HOST    = "68.233.98.35"
SERVER_SSH_KEY = str(Path.home() / "Downloads/ssh-key-2026-03-13.key")
SERVER_ENV     = "~/AAItrade/.env"
# ─────────────────────────────────────────────────────────────────────────────


def exchange_token(request_token: str) -> str:
    """Exchange request_token for access_token via Kite API."""
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        print("Installing kiteconnect...")
        subprocess.run([sys.executable, "-m", "pip", "install", "kiteconnect", "-q"], check=True)
        from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=API_KEY)
    data = kite.generate_session(request_token, api_secret=API_SECRET)
    return data["access_token"]


def validate_token(access_token: str) -> str:
    """Validate the token works by fetching the user profile. Returns user name."""
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(access_token)
    profile = kite.profile()
    return profile["user_name"]


def update_server_env(access_token: str):
    """SSH into server and update all KITE_ keys in .env"""
    # Update all 3 Kite keys — API key and secret are constant but ensure they're set
    sed_cmds = " && ".join([
        f"sed -i 's/^KITE_API_KEY=.*/KITE_API_KEY={API_KEY}/' {SERVER_ENV}",
        f"sed -i 's/^KITE_API_SECRET=.*/KITE_API_SECRET={API_SECRET}/' {SERVER_ENV}",
        f"sed -i 's/^KITE_ACCESS_TOKEN=.*/KITE_ACCESS_TOKEN={access_token}/' {SERVER_ENV}",
    ])

    ssh = [
        "ssh",
        "-i", SERVER_SSH_KEY,
        "-o", "StrictHostKeyChecking=no",
        f"{SERVER_USER}@{SERVER_HOST}",
        sed_cmds,
    ]

    result = subprocess.run(ssh, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"SSH error: {result.stderr}")
        sys.exit(1)


def main():
    if len(sys.argv) != 2:
        print("Usage: .venv/bin/python scripts/refresh_token.py <request_token>")
        print()
        print("Get request_token by visiting:")
        print(f"  https://kite.trade/connect/login?api_key={API_KEY}&v=3")
        sys.exit(1)

    request_token = sys.argv[1].strip()

    print("Exchanging request_token for access_token...")
    try:
        access_token = exchange_token(request_token)
    except Exception as e:
        print(f"FAILED: Could not get access token: {e}")
        print("The request_token may have expired — get a fresh one from the browser.")
        sys.exit(1)

    print("Validating token with Kite API...")
    try:
        user_name = validate_token(access_token)
        print(f"Token valid — logged in as: {user_name}")
    except Exception as e:
        print(f"FAILED: Token validation failed: {e}")
        print("Server NOT updated. Get a fresh request_token and try again.")
        sys.exit(1)

    # Update local .env first
    local_env = Path(__file__).resolve().parent.parent / ".env"
    if local_env.exists():
        content = local_env.read_text()
        import re
        content = re.sub(r"^KITE_ACCESS_TOKEN=.*$", f"KITE_ACCESS_TOKEN={access_token}", content, flags=re.MULTILINE)
        local_env.write_text(content)
        print(f"Local .env updated with new token.")

    print("Updating server .env...")
    update_server_env(access_token)

    print("Done. Kite token verified and updated on server — trading continues without restart.")


if __name__ == "__main__":
    main()
