#!/usr/bin/env bash
# Always-on local videographer: runs the marketing loop 24/7 until the
# north-star goals are met, restarting itself on any crash or GPU outage.
#
# The loop is resume-safe — all state (dossier, goals, versions, feedback,
# lessons) lives on disk, so a restart simply picks up where it left off.
#
#   nohup ./deploy/run_local.sh > /tmp/cosmosclaw_loop.log 2>&1 &
#
# Or supervise it with launchd (macOS) / systemd (Linux) — see DEPLOY.md.
set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

PY="${PY:-$ROOT/.venv/bin/python}"
PROJECTS="${PROJECTS:-la-house-1,hacker-house}"
BACKEND="${LIVEHERE_BACKEND:-cosmos}"
# Effectively uncapped — the real stop condition is --until-goals.
MAX_VIDEOS="${MAX_VIDEOS:-1000000}"
# Aggressive pacing: keep shipping with almost no idle time between cuts.
PACE_SECONDS="${PACE_SECONDS:-0}"
RESTART_SECONDS="${RESTART_SECONDS:-30}"

LOG_DIR="${LOG_DIR:-$ROOT/outputs/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/loop.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [run_local] $*" | tee -a "$LOG"; }

log "starting — projects=$PROJECTS backend=$BACKEND pace=${PACE_SECONDS}s"

while true; do
  "$PY" scripts/marketing_loop.py \
    --projects "$PROJECTS" \
    --backend "$BACKEND" \
    --max-videos "$MAX_VIDEOS" \
    --until-goals \
    --sleep "$PACE_SECONDS" \
    >> "$LOG" 2>&1
  code=$?
  if [ "$code" -eq 0 ]; then
    log "loop returned cleanly (goals met or cap hit) — re-checking in ${RESTART_SECONDS}s"
  else
    log "loop exited with code $code — restarting in ${RESTART_SECONDS}s"
  fi
  sleep "$RESTART_SECONDS"
done
