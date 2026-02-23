#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PORT="${DASHBOARD_PORT:-8502}"
exec streamlit run polyedge/dashboard.py --server.port "$PORT"
