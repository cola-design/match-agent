#!/bin/sh
# Run after a successful initial full apply and verification, as the server admin.
set -eu
PERIOD=monthly
MONTH_DAY=1
START_HOUR=2
END_HOUR=6
while [ "$#" -gt 0 ]; do
    case "$1" in
        --period) PERIOD=${2:?Missing period}; shift 2 ;;
        --month-day) MONTH_DAY=${2:?Missing month day}; shift 2 ;;
        --start-hour) START_HOUR=${2:?Missing start hour}; shift 2 ;;
        --end-hour) END_HOUR=${2:?Missing end hour}; shift 2 ;;
        *) echo 'Options: --period monthly|15days --month-day 1 --start-hour 2 --end-hour 6' >&2; exit 2 ;;
    esac
done
START_HOUR=$(expr "$START_HOUR" + 0) || [ "$START_HOUR" = 0 ]
END_HOUR=$(expr "$END_HOUR" + 0) || [ "$END_HOUR" = 0 ]
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$(id -u)" = 0 ] || { echo 'Run this installer with sudo.' >&2; exit 1; }
case "$ROOT" in *[!A-Za-z0-9_./-]*) echo 'Use an absolute installation path without spaces or special characters.' >&2; exit 1 ;; esac
DOCKER=$(command -v docker)
[ -n "$DOCKER" ] || { echo 'Docker is required.' >&2; exit 1; }
if systemctl is-active --quiet match-agent-product-sync.service; then
    echo 'Wait for the running sync service to finish before changing the schedule.' >&2; exit 1
fi
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
# Stop the previous timer before replacing its policy. A running manual job is
# protected by the same state-volume lock used by schedule configuration.
if systemctl cat match-agent-product-sync.timer >/dev/null 2>&1; then
    systemctl disable --now match-agent-product-sync.timer
fi
"$DOCKER" compose --project-directory "$ROOT/product_sync" --env-file "$ROOT/product_sync/.env" -f "$ROOT/product_sync/compose.yaml" run --rm --entrypoint python sync -m product_sync.scheduled --configure --period "$PERIOD" --month-day "$MONTH_DAY" --start-hour "$START_HOUR" --end-hour "$END_HOUR"
WINDOW_SECONDS=$(( ((END_HOUR - START_HOUR + 24) % 24) * 3600 ))
cat > /etc/systemd/system/match-agent-product-sync.service <<EOF
[Unit]
Description=Reconcile product specifications into MongoDB
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT/product_sync
ExecStart=$DOCKER compose run --rm --name match-agent-product-sync-scheduled --entrypoint python sync -m product_sync.scheduled
ExecStopPost=-/bin/sh -c '$DOCKER inspect match-agent-product-sync-scheduled >/dev/null 2>&1 && $DOCKER stop --time 30 match-agent-product-sync-scheduled || true'
TimeoutStartSec=$WINDOW_SECONDS
UMask=0077
EOF
cat > /etc/systemd/system/match-agent-product-sync.timer <<EOF
[Unit]
Description=Nightly local schedule check; product source sync uses configured interval

[Timer]
OnCalendar=*-*-* $START_HOUR:00:00 Asia/Shanghai
Persistent=false
AccuracySec=1s
RandomizedDelaySec=0
Unit=match-agent-product-sync.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now match-agent-product-sync.timer
systemctl list-timers match-agent-product-sync.timer
