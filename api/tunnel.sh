#!/usr/bin/env bash
# SSH tunnel script for AAItrade dashboard
# Usage: ./api/tunnel.sh [user@host]
#
# Forwards localhost:8000 -> remote server port 8000
# Default host can be overridden by passing user@host as first argument.

set -euo pipefail

REMOTE_HOST="${1:-}"

if [[ -z "$REMOTE_HOST" ]]; then
  echo "Usage: $0 user@hostname_or_ip"
  echo ""
  echo "Example: $0 ubuntu@192.168.1.100"
  exit 1
fi

echo "Opening SSH tunnel: localhost:8000 -> $REMOTE_HOST:8000"
echo "Press Ctrl+C to close."
echo ""

ssh -N -L 8000:localhost:8000 "$REMOTE_HOST"
