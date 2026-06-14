#!/usr/bin/env bash
# Keeps the SSH tunnel (local 8800 -> Nebius VM 8000) alive.
# Re-opens it automatically whenever it drops (e.g. after the Mac sleeps).
#
#   nohup ./deploy/tunnel_keeper.sh > /tmp/tunnel_keeper.log 2>&1 &
#
set -u

KEY="${KEY:-$HOME/.ssh/nebius_cosmos}"
REMOTE="${REMOTE:-cosmos@195.242.31.145}"
LOCAL_PORT="${LOCAL_PORT:-8800}"
REMOTE_PORT="${REMOTE_PORT:-8000}"
# vLLM-Omni has no /health; /v1/models is the cheap liveness probe.
HEALTH_PATH="${HEALTH_PATH:-/v1/models}"

# Require two consecutive failures before tearing down, so a slow health check
# during a big clip transfer doesn't kill the in-flight tunnel.
fails=0
while true; do
  code=$(curl -s -m 12 -o /dev/null -w "%{http_code}" "http://127.0.0.1:${LOCAL_PORT}${HEALTH_PATH}" 2>/dev/null || echo 000)
  if [ "$code" = "200" ]; then
    fails=0
  else
    fails=$((fails + 1))
    echo "$(date '+%H:%M:%S') health=$code (fail ${fails}/2)"
    if [ "$fails" -ge 2 ]; then
      echo "$(date '+%H:%M:%S') tunnel down — reconnecting…"
      pkill -f "${LOCAL_PORT}:localhost:${REMOTE_PORT} ${REMOTE}" 2>/dev/null
      sleep 1
      ssh -i "$KEY" -f -N \
        -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=20 -o ServerAliveCountMax=3 \
        -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new \
        -L "${LOCAL_PORT}:localhost:${REMOTE_PORT}" "$REMOTE" \
        && echo "$(date '+%H:%M:%S') reconnected" \
        || echo "$(date '+%H:%M:%S') reconnect failed (VM down?)"
      fails=0
    fi
  fi
  sleep 15
done
