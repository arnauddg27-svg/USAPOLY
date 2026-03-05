#!/usr/bin/env bash
set -euo pipefail

REMOTE="${1:-root@178.62.251.178}"
APP_DIR="${2:-/opt/PolyEdge}"
LOCAL_DASHBOARD_FILE="polyedge/dashboard.py"
LOCAL_COMPOSE_FILE="docker-compose.yml"

if [[ ! -f "$LOCAL_DASHBOARD_FILE" ]]; then
  echo "[deploy] missing local file: $LOCAL_DASHBOARD_FILE" >&2
  exit 1
fi
if [[ ! -f "$LOCAL_COMPOSE_FILE" ]]; then
  echo "[deploy] missing local file: $LOCAL_COMPOSE_FILE" >&2
  exit 1
fi

echo "[deploy] copying dashboard and compose files to $REMOTE:/tmp"
scp -o StrictHostKeyChecking=no "$LOCAL_DASHBOARD_FILE" "$REMOTE:/tmp/polyedge_dashboard.py"
scp -o StrictHostKeyChecking=no "$LOCAL_COMPOSE_FILE" "$REMOTE:/tmp/polyedge_docker_compose.yml"

echo "[deploy] restarting dashboard on $REMOTE ($APP_DIR)"
ssh -o StrictHostKeyChecking=no "$REMOTE" APP_DIR="$APP_DIR" 'bash -se' <<'EOF'
set -euo pipefail

mkdir -p "$APP_DIR/polyedge" "$APP_DIR/logs"
install -m 644 /tmp/polyedge_dashboard.py "$APP_DIR/polyedge/dashboard.py"
install -m 644 /tmp/polyedge_docker_compose.yml "$APP_DIR/docker-compose.yml"
rm -f /tmp/polyedge_dashboard.py /tmp/polyedge_docker_compose.yml
cd "$APP_DIR"

echo "[verify] marker:"
grep -n 'Exchange Fills (Session)' "$APP_DIR/polyedge/dashboard.py" | head -n 1 || echo 'MISSING_MARKER'

if command -v docker >/dev/null 2>&1 \
  && docker compose config >/dev/null 2>&1 \
  && docker compose config --services | grep -qx dashboard; then
  echo "[deploy] using docker compose dashboard service"
  docker compose up -d --build dashboard
  echo "[verify] compose:"
  docker compose ps dashboard || true
  echo "[verify] dashboard logs:"
  docker compose logs --tail=12 dashboard || true
else
  echo "[deploy] docker compose dashboard service unavailable; fallback to host python3 -m streamlit"
  pkill -f '[s]treamlit run polyedge/dashboard.py' || true
  nohup python3 -m streamlit run polyedge/dashboard.py \
    --server.port "${DASHBOARD_PORT:-8502}" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --server.enableWebsocketCompression false \
    --browser.gatherUsageStats false \
    > logs/dashboard.out 2>&1 &
  sleep 4
  echo "[verify] process:"
  pgrep -af '[s]treamlit run polyedge/dashboard.py' | head -n 1 || echo 'NO_PROCESS'
  echo "[verify] tail:"
  tail -n 8 "$APP_DIR/logs/dashboard.out" || true
fi
EOF

echo "[deploy] done"
