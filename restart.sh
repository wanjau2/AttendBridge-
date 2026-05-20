#!/bin/sh
# restart.sh — pull latest from GitHub, kill all running middleware instances,
# and relaunch app.py in the background.
#
# Usage (on the Synology NAS):
#     cd /volume1/web/mb360_middleware
#     sh restart.sh
#
# Logs go to middleware.log in this directory.

set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_FILE="app.py"
LOG_FILE="$APP_DIR/middleware.log"
PID_FILE="$APP_DIR/middleware.pid"

cd "$APP_DIR"

echo "[restart] $(date '+%Y-%m-%d %H:%M:%S') — restarting middleware in $APP_DIR"

# ── 1. Pull latest from GitHub ─────────────────────────────────────────────────
echo "[restart] git pull"
git pull --ff-only

# ── 2. Stop every running instance ─────────────────────────────────────────────
# Match any python process running this directory's app.py.
PIDS=$(pgrep -f "python.* $APP_DIR/$APP_FILE" 2>/dev/null || true)
# Also include PIDs that match the short form `python app.py` from this cwd
PIDS="$PIDS $(pgrep -f "python.* $APP_FILE\$" 2>/dev/null || true)"
# Plus anything in our PID file
if [ -f "$PID_FILE" ]; then
    PIDS="$PIDS $(cat "$PID_FILE")"
fi
# De-dup and trim
PIDS=$(echo "$PIDS" | tr ' ' '\n' | sort -u | grep -E '^[0-9]+$' || true)

if [ -n "$PIDS" ]; then
    echo "[restart] stopping PIDs: $(echo "$PIDS" | tr '\n' ' ')"
    # Graceful first
    echo "$PIDS" | xargs -r kill 2>/dev/null || true
    sleep 3
    # Force-kill anything that survived
    STILL=$(echo "$PIDS" | while read pid; do kill -0 "$pid" 2>/dev/null && echo "$pid"; done)
    if [ -n "$STILL" ]; then
        echo "[restart] force-killing: $(echo "$STILL" | tr '\n' ' ')"
        echo "$STILL" | xargs -r kill -9 2>/dev/null || true
    fi
else
    echo "[restart] no running instance found"
fi

rm -f "$PID_FILE"

# ── 3. Relaunch in background ──────────────────────────────────────────────────
echo "[restart] launching $APP_FILE"
nohup python3 "$APP_DIR/$APP_FILE" >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

# Give it a moment to fail-fast on a startup error
sleep 2
if kill -0 "$NEW_PID" 2>/dev/null; then
    echo "[restart] started PID=$NEW_PID — tailing $LOG_FILE for live logs"
    exit 0
else
    echo "[restart] ERROR: process exited immediately. Last 30 log lines:"
    tail -n 30 "$LOG_FILE"
    exit 1
fi
