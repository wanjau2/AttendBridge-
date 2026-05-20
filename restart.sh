#!/bin/sh
# restart.sh — pull latest from GitHub, kill all running middleware instances,
# and relaunch under gunicorn in the background.
#
# Usage (on the Synology NAS):
#     cd /volume1/web/mb360_middleware
#     sh restart.sh
#
# Logs:
#   middleware.log        — application logs (from Python logging)
#   gunicorn-error.log    — gunicorn worker startup / crash output
#   access.log            — HTTP access log
#
# Single worker is REQUIRED: the auto-checkout thread and in-process device
# state would otherwise run/duplicate N times.

set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_MODULE="app:app"
PORT="${LISTEN_PORT:-8008}"

LOG_FILE="$APP_DIR/middleware.log"
ERR_LOG="$APP_DIR/gunicorn-error.log"
ACCESS_LOG="$APP_DIR/access.log"
PID_FILE="$APP_DIR/middleware.pid"

cd "$APP_DIR"

echo "[restart] $(date '+%Y-%m-%d %H:%M:%S') — restarting middleware in $APP_DIR"

# ── 1. Pull latest from GitHub ─────────────────────────────────────────────────
echo "[restart] git pull"
git pull --ff-only

# ── 2. Stop every running instance ─────────────────────────────────────────────
# Match gunicorn serving our app and any straight `python app.py` leftovers.
PIDS=$(pgrep -f "gunicorn.* $APP_MODULE" 2>/dev/null || true)
PIDS="$PIDS $(pgrep -f "python.* $APP_DIR/app.py" 2>/dev/null || true)"
PIDS="$PIDS $(pgrep -f "python.* app.py\$" 2>/dev/null || true)"
if [ -f "$PID_FILE" ]; then
    PIDS="$PIDS $(cat "$PID_FILE")"
fi
PIDS=$(echo "$PIDS" | tr ' ' '\n' | sort -u | grep -E '^[0-9]+$' || true)

if [ -n "$PIDS" ]; then
    echo "[restart] stopping PIDs: $(echo "$PIDS" | tr '\n' ' ')"
    echo "$PIDS" | xargs -r kill 2>/dev/null || true
    sleep 3
    STILL=$(echo "$PIDS" | while read pid; do kill -0 "$pid" 2>/dev/null && echo "$pid"; done)
    if [ -n "$STILL" ]; then
        echo "[restart] force-killing: $(echo "$STILL" | tr '\n' ' ')"
        echo "$STILL" | xargs -r kill -9 2>/dev/null || true
    fi
else
    echo "[restart] no running instance found"
fi

# Make sure the port is actually free before we bind
if command -v fuser >/dev/null 2>&1; then
    fuser -k "${PORT}/tcp" 2>/dev/null || true
fi

rm -f "$PID_FILE"

# ── 3. Relaunch under gunicorn ─────────────────────────────────────────────────
# Find a usable gunicorn binary.
GUNICORN=""
for candidate in gunicorn /usr/local/bin/gunicorn /volume1/@appstore/py3k/usr/local/bin/gunicorn; do
    if command -v "$candidate" >/dev/null 2>&1; then
        GUNICORN="$candidate"
        break
    fi
done
if [ -z "$GUNICORN" ]; then
    # Fall back to `python3 -m gunicorn` if the script isn't on PATH
    if python3 -c "import gunicorn" 2>/dev/null; then
        GUNICORN="python3 -m gunicorn"
    else
        echo "[restart] ERROR: gunicorn not installed. Run: pip3 install gunicorn"
        exit 1
    fi
fi

echo "[restart] launching gunicorn on 0.0.0.0:$PORT (1 worker)"
# stdout/stderr → /dev/null because gunicorn already writes its own logs and
# the app uses a FileHandler — duplicating stdout into middleware.log would
# produce double lines.
nohup $GUNICORN \
    --workers 1 \
    --threads 4 \
    --bind "0.0.0.0:$PORT" \
    --timeout 60 \
    --pid "$PID_FILE" \
    --access-logfile "$ACCESS_LOG" \
    --error-logfile  "$ERR_LOG" \
    "$APP_MODULE" \
    >/dev/null 2>&1 &

# Give gunicorn a moment to bind or fail
sleep 3

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "[restart] started — PID $(cat "$PID_FILE")"
    echo "[restart] logs: $LOG_FILE  /  $ERR_LOG  /  $ACCESS_LOG"
    exit 0
else
    echo "[restart] ERROR: gunicorn failed to start. Last 30 lines of $ERR_LOG:"
    [ -f "$ERR_LOG" ] && tail -n 30 "$ERR_LOG"
    exit 1
fi
