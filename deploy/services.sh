#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
api() { docker compose --project-directory "$ROOT/mongodb_api" --env-file "$ROOT/mongodb_api/.env" -f "$ROOT/mongodb_api/compose.yaml" "$@"; }
sync() { docker compose --project-directory "$ROOT/product_sync" --env-file "$ROOT/product_sync/.env" -f "$ROOT/product_sync/compose.yaml" "$@"; }
case "${1:-help}" in
  build) api build; sync build ;;
  start-api) api config --quiet; api up -d --build ;;
  check-sync) sync run --rm sync --check ;;
  preview) sync run --rm sync ;;
  apply) sync run --rm sync --apply ;;
  reuse)
    [ "$#" -eq 2 ] || { echo 'Usage: services.sh reuse RUN_ID' >&2; exit 2; }
    sync run --rm sync --reuse-snapshot "$2" ;;
  report) sync run --rm --entrypoint cat sync /state/latest.json ;;
  check-report) sync run --rm --entrypoint cat sync /state/latest_check.json ;;
  schedule-status) sync run --rm --entrypoint python sync -m product_sync.scheduled --status ;;
  last-success) sync run --rm --entrypoint cat sync /state/last_success.json ;;
  status)
    api ps
    sync run --rm --entrypoint python sync -m product_sync.sync --status ;;
  api-status)
    api exec -T match-agent-mongo-api python -c 'import os,json,urllib.request; r=urllib.request.Request("http://127.0.0.1:8000/v1/status",headers={"X-API-Key":os.environ["MONGODB_API_KEY"]}); print(json.dumps(json.load(urllib.request.urlopen(r,timeout=12)),ensure_ascii=False,indent=2))' ;;
  api-logs) api logs --tail=100 match-agent-mongo-api ;;
  *) echo 'Commands: build | start-api | check-sync | preview | apply | reuse RUN_ID | report | check-report | last-success | status | schedule-status | api-status | api-logs' ;;
esac
