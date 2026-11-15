#!/bin/bash
set -euo pipefail
ROOT=/home/nifeel/tech-pulse
BACKEND="$ROOT/backend"
PID_FILE="$BACKEND/data/uvicorn.pid"
LOG_FILE="$BACKEND/data/uvicorn.log"
PORT=8088

mkdir -p "$BACKEND/data"
ACCESS_ENV="$BACKEND/data/access.env"
if [[ -f "$ACCESS_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ACCESS_ENV"
  set +a
fi
cd "$BACKEND"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "already running pid=$(cat "$PID_FILE")"
  exit 0
fi

if ss -lnt | grep -q ":${PORT} "; then
  echo "port ${PORT} already in use"
  exit 1
fi

nohup "$BACKEND/.venv/bin/uvicorn" app.main:app --host 0.0.0.0 --port "$PORT" --app-dir . \
  >>"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"
echo "started pid=$(cat "$PID_FILE") http://10.0.0.202:${PORT}/"
