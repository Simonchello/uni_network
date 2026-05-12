#!/usr/bin/env bash
# rsync web/ to VPS and restart the service.
# Usage: bash deploy/sync.sh [vps-user@vps-host]
set -euo pipefail

VPS="${1:-root@<YOUR_VPS_IP>}"
REMOTE_DIR="/opt/lockdown-web"

cd "$(dirname "$0")/.."

echo "→ syncing web/ to ${VPS}:${REMOTE_DIR}"
rsync -av --delete \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '.env' \
    web/ "${VPS}:${REMOTE_DIR}/web/"

echo "→ restarting web-admin.service"
ssh "${VPS}" "systemctl restart web-admin && systemctl is-active web-admin"

echo "✓ done"
