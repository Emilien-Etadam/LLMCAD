#!/bin/bash
# @file start.sh
# @brief Launch CadQuery (Python) and frontend/API (Node) servers in bare-metal mode

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

cleanup() {
  echo ""
  echo "[start.sh] Stopping servers..."
  if [ -n "${CQ_PID:-}" ] && kill -0 "$CQ_PID" 2>/dev/null; then
    kill "$CQ_PID" 2>/dev/null || true
  fi
  if [ -n "${NODE_PID:-}" ] && kill -0 "$NODE_PID" 2>/dev/null; then
    kill "$NODE_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

echo "[start.sh] Starting CadQuery server (Python) on ${CADQUERY_HOST:-127.0.0.1}:${CADQUERY_PORT:-5002}..."
(
  cd "$ROOT_DIR/cadquery"
  # shellcheck disable=SC1091
  . venv/bin/activate
  exec python server.py
) &
CQ_PID=$!

echo "[start.sh] Starting Node server on ${NODE_HOST:-0.0.0.0}:${NODE_PORT:-49157}..."
(
  cd "$ROOT_DIR/node"
  exec node server.js
) &
NODE_PID=$!

echo "[start.sh] CadQuery PID=$CQ_PID  Node PID=$NODE_PID"
echo "[start.sh] Logs: $ROOT_DIR/logs/"
echo "[start.sh] Open http://${NODE_HOST:-0.0.0.0}:${NODE_PORT:-49157} in your browser"

wait -n "$CQ_PID" "$NODE_PID" 2>/dev/null || wait
cleanup
