#!/bin/bash
# @file start.sh
# @brief Launch CadQuery (Python) and frontend/API (Node) servers in bare-metal mode.
#
# Phase 4.5 — process supervision:
#   The CadQuery Flask process is now wrapped in a supervisor loop that
#   (a) restarts it whenever it exits and
#   (b) probes GET /health every 30 seconds and force-restarts it if the
#       endpoint stops responding (e.g. Python deadlock, OOM-killed, etc.).
#   Subprocess crashes inside the worker no longer take down Flask, but the
#   supervisor stays in place as a safety net for the rare case where the
#   Flask process itself dies.

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env"
  set +a
fi

if ! command -v node >/dev/null 2>&1; then
  if [ -s "$HOME/.nvm/nvm.sh" ]; then
    export NVM_DIR="$HOME/.nvm"
    # shellcheck disable=SC1091
    . "$NVM_DIR/nvm.sh"
  fi
fi

if [ ! -d "$ROOT_DIR/cadquery/venv" ]; then
  echo "[start.sh] Python venv missing. Create it with:"
  echo "  cd cadquery && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

if [ ! -d "$ROOT_DIR/node/node_modules" ]; then
  echo "[start.sh] node_modules missing. Run: cd node && npm install"
  exit 1
fi

mkdir -p "$ROOT_DIR/logs"

CADQUERY_HOST_VAL="${CADQUERY_HOST:-127.0.0.1}"
CADQUERY_PORT_VAL="${CADQUERY_PORT:-5002}"
HEALTH_URL="http://${CADQUERY_HOST_VAL}:${CADQUERY_PORT_VAL}/health"
HEALTH_INTERVAL_SEC="${CADQUERY_HEALTH_INTERVAL:-30}"
HEALTH_TIMEOUT_SEC="${CADQUERY_HEALTH_TIMEOUT:-5}"

cleanup() {
  echo ""
  echo "[start.sh] Stopping servers..."
  if [ -n "${SUPERVISOR_PID:-}" ] && kill -0 "$SUPERVISOR_PID" 2>/dev/null; then
    kill "$SUPERVISOR_PID" 2>/dev/null || true
  fi
  # Also kill any python server.py the supervisor may have spawned.
  pkill -f "python server.py" 2>/dev/null || true
  if [ -n "${NODE_PID:-}" ] && kill -0 "$NODE_PID" 2>/dev/null; then
    kill "$NODE_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

echo "[start.sh] Starting CadQuery supervisor on ${CADQUERY_HOST_VAL}:${CADQUERY_PORT_VAL}..."
(
  while true; do
    (
      cd "$ROOT_DIR/cadquery"
      # shellcheck disable=SC1091
      . venv/bin/activate
      exec python server.py
    ) &
    cq_pid=$!
    echo "[supervisor] CadQuery PID=$cq_pid"

    # Health-check loop. Sleeps HEALTH_INTERVAL_SEC between probes; if /health
    # stops returning 2xx we kill the process and let the outer while restart
    # it. If it dies on its own (segfault, OOM, manual kill -9) we exit the
    # inner loop because `kill -0` fails.
    while kill -0 "$cq_pid" 2>/dev/null; do
      sleep "$HEALTH_INTERVAL_SEC"
      if ! kill -0 "$cq_pid" 2>/dev/null; then
        break
      fi
      if ! curl -sf -m "$HEALTH_TIMEOUT_SEC" "$HEALTH_URL" >/dev/null 2>&1; then
        echo "[supervisor] /health probe failed, killing PID=$cq_pid"
        kill -9 "$cq_pid" 2>/dev/null || true
        break
      fi
    done

    wait "$cq_pid" 2>/dev/null || true
    echo "[supervisor] CadQuery exited; restarting in 1s..."
    sleep 1
  done
) &
SUPERVISOR_PID=$!

echo "[start.sh] Starting Node server on ${NODE_HOST:-0.0.0.0}:${NODE_PORT:-49157}..."
(
  cd "$ROOT_DIR/node"
  exec node server.js
) &
NODE_PID=$!

echo "[start.sh] Supervisor PID=$SUPERVISOR_PID  Node PID=$NODE_PID"
echo "[start.sh] Logs: $ROOT_DIR/logs/"
echo "[start.sh] Health probe every ${HEALTH_INTERVAL_SEC}s -> $HEALTH_URL"
echo "[start.sh] Open http://${NODE_HOST:-0.0.0.0}:${NODE_PORT:-49157} in your browser"

# Wait on Node primarily (the supervisor is meant to be immortal). If Node
# exits we tear everything down via `cleanup`.
wait "$NODE_PID" 2>/dev/null || true
cleanup
