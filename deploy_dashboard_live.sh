#!/usr/bin/env bash
set -euo pipefail

REMOTE="${1:-root@178.62.251.178}"
APP_DIR="${2:-/opt/PolyEdge}"
LOCAL_FILE="polyedge/dashboard.py"

if [[ ! -f "$LOCAL_FILE" ]]; then
  echo "[deploy] missing local file: $LOCAL_FILE" >&2
  exit 1
fi

echo "[deploy] copying $LOCAL_FILE -> $REMOTE:/tmp/dashboard.py"
scp -o StrictHostKeyChecking=no "$LOCAL_FILE" "$REMOTE:/tmp/dashboard.py"

echo "[deploy] restarting dashboard on $REMOTE ($APP_DIR)"
ssh -o StrictHostKeyChecking=no "$REMOTE" "set -euo pipefail; \
  APP_DIR='$APP_DIR'; \
  mkdir -p \"\$APP_DIR/polyedge\" \"\$APP_DIR/logs\"; \
  install -m 644 /tmp/dashboard.py \"\$APP_DIR/polyedge/dashboard.py\"; \
  rm -f /tmp/dashboard.py; \
  if [ ! -f \"\$APP_DIR/run_dashboard.sh\" ]; then echo '[deploy] missing run_dashboard.sh' >&2; exit 1; fi; \
  pkill -f 'streamlit run polyedge/dashboard.py' || true; \
  cd \"\$APP_DIR\"; \
  nohup bash run_dashboard.sh > logs/dashboard.out 2>&1 & \
  sleep 4; \
  echo '[verify] marker:'; \
  grep -n 'Exchange Fills (Session)' \"\$APP_DIR/polyedge/dashboard.py\" | head -n 1 || echo 'MISSING_MARKER'; \
  echo '[verify] process:'; \
  pgrep -af 'streamlit run polyedge/dashboard.py' | head -n 1 || echo 'NO_PROCESS'; \
  echo '[verify] tail:'; \
  tail -n 8 \"\$APP_DIR/logs/dashboard.out\" || true"

echo "[deploy] done"
