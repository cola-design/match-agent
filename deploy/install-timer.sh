#!/bin/sh
# Run after a successful initial full apply and verification, as the server admin.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$(id -u)" = 0 ] || { echo 'Run this installer with sudo.' >&2; exit 1; }
case "$ROOT" in *[!A-Za-z0-9_./-]*) echo 'Use an absolute installation path without spaces or special characters.' >&2; exit 1 ;; esac
DOCKER=$(command -v docker)
[ -n "$DOCKER" ] || { echo 'Docker is required.' >&2; exit 1; }
# Do not enable automatic writes before the operator has completed and verified
# the first manual apply. This only reads the persisted local report.
"$DOCKER" compose --project-directory "$ROOT/product_sync" --env-file "$ROOT/product_sync/.env" -f "$ROOT/product_sync/compose.yaml" run --rm --entrypoint python sync -c '
import json,sys
from pathlib import Path
p=Path("/state/last_success.json")
r=json.loads(p.read_text()) if p.exists() else {}
ok=r.get("result")=="success" and r.get("phase")=="complete" and r.get("applied_operations")==r.get("verified_operations") and "verified_operations" in r
if not ok: print("First complete and verify a manual full apply; timer was not installed.",file=sys.stderr)
sys.exit(0 if ok else 1)
'
cat > /etc/systemd/system/match-agent-product-sync.service <<EOF
[Unit]
Description=Reconcile product specifications into MongoDB
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT/product_sync
ExecStart=$DOCKER compose run --rm --name match-agent-product-sync-daily sync --apply
ExecStopPost=-$DOCKER stop --time 30 match-agent-product-sync-daily
TimeoutStartSec=4h
UMask=0077
EOF
install -m 0644 "$ROOT/product_sync/deploy/match-agent-product-sync.timer" /etc/systemd/system/match-agent-product-sync.timer
systemctl daemon-reload
systemctl enable --now match-agent-product-sync.timer
systemctl list-timers match-agent-product-sync.timer
