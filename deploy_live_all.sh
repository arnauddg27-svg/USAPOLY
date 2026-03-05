#!/usr/bin/env bash
set -euo pipefail

REMOTE="${1:-root@178.62.251.178}"
APP_DIR="${2:-/opt/PolyEdge}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

FILES=(
  "docker-compose.yml"
  "run_dashboard.sh"
  "polyedge/dashboard.py"
  "polyedge/main.py"
  "polyedge/config.py"
  "polyedge/execution/sizing.py"
  "polyedge/risk/limits.py"
  "polyedge/models.py"
  "polyedge/execution/executor.py"
  "polyedge/paths.py"
)

for file in "${FILES[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "[deploy] missing local file: $file" >&2
    exit 1
  fi
done

echo "[deploy] syncing runtime files to $REMOTE:$APP_DIR"
rsync -az --relative "${FILES[@]}" "$REMOTE:$APP_DIR/"

echo "[deploy] restarting bot + dashboard on $REMOTE"
ssh -o StrictHostKeyChecking=no "$REMOTE" APP_DIR="$APP_DIR" 'bash -se' <<'EOF'
set -euo pipefail
cd "$APP_DIR"

docker compose up -d --build bot dashboard

echo "[verify] services:"
docker compose ps bot dashboard || true

echo "[verify] bot logs:"
docker compose logs --tail=20 bot || true

echo "[verify] dashboard logs:"
docker compose logs --tail=30 dashboard || true

echo "[verify] dashboard health endpoint:"
curl -fsS http://127.0.0.1:8502/_stcore/health || true
echo
EOF

echo "[deploy] complete"
