#!/bin/bash
set -euo pipefail
PID_FILE=/home/nifeel/tech-pulse/backend/data/uvicorn.pid
if [[ -f "$PID_FILE" ]]; then
  pid=$(cat "$PID_FILE")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "stopped $pid"
  fi
  rm -f "$PID_FILE"
else
  echo "not running"
fi
