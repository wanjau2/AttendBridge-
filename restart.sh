#!/bin/sh
# restart.sh — pull latest from GitHub, kill all running middleware instances,
# and relaunch under gunicorn in the background.
#
# Usage (on the Synology NAS):
#     cd /volume1/web/mb360_middleware
#     sh restart.sh
#
# Logs:
#   middleware.log        — application logs (Python logging FileHandler)
#   gunicorn-error.log    — gunicorn worker startup / crash output
#   access.log            — HTTP access log
#
# Single worker is REQUIRED: the auto-checkout thread and in-process device
# state would otherwise run/duplicate N times.
#
# NOTE: deliberately NOT using `set -e` — busybox sh aborts on non-zero exit
# from command substitution / pipelines (e.g. `kill -0` on a dead PID), which
# is exactly what we need to tolerate when sweeping stale PIDs.

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_MODULE="app:app"
PORT="${LISTEN_PORT:-8008}"

LOG_FILE="$APP_DIR/middleware.log"
ERR_LOG="$APP_DIR/gunicorn-error.log"
ACCESS_LOG="$APP_DIR/access.log"
PID_FILE="$APP_DIR/middleware.pid"

cd "$APP_DIR" || exit 1

log() { echo "[restart] $*"; }

log "$(date '+%Y-%m-%d %H:%M:%S') — restarting middleware in $APP_DIR"

# ── 1. Pull latest from GitHub ─────────────────────────────────────────────────
log "git pull"
git pull --ff-only || { log "WARN: git pull failed — continuing with current code"; }

# ── 2. Stop every running instance ─────────────────────────────────────────────
log "scanning for running instances"

# Collect candidate PIDs from multiple sources
PIDS=""
PIDS="$PIDS $(pgrep -f "gunicorn.* $APP_MODULE" 2>/dev/null)"
PIDS="$PIDS $(pgrep -f "gunicorn:" 2>/dev/null)"
PIDS="$PIDS $(pgrep -f "python.* $APP_DIR/app.py" 2>/dev/null)"
PIDS="$PIDS $(pgrep -f "python.* app.py" 2>/dev/null)"
if [ -f "$PID_FILE" ]; then
    PIDS="$PIDS $(cat "$PID_FILE" 2>/dev/null)"
fi

# Filter to numeric, unique, and currently-alive PIDs only
ALIVE=""
for pid in $PIDS; do
    case "$pid" in
        ''|*[!0-9]*) continue ;;            # skip non-numeric
    esac
    if kill -0 "$pid" 2>/dev/null; then
        # de-dup
        case " $ALIVE " in
            *" $pid "*) ;;
            *) ALIVE="$ALIVE $pid" ;;
        esac
    fi
done

if [ -n "$ALIVE" ]; then
    log "stopping PIDs:$ALIVE"
    for pid in $ALIVE; do
        kill "$pid" 2>/dev/null
    done
    sleep 3

    # Re-check; force-kill survivors
    STILL=""
    for pid in $ALIVE; do
        if kill -0 "$pid" 2>/dev/null; then
            STILL="$STILL $pid"
        fi
    done
    if [ -n "$STILL" ]; then
        log "force-killing:$STILL"
        for pid in $STILL; do
            kill -9 "$pid" 2>/dev/null
        done
        sleep 1
    fi
else
    log "no running instance found"
fi

# Free the port if anything still holds it
if command -v fuser >/dev/null 2>&1; then
    fuser -k "${PORT}/tcp" 2>/dev/null
fi

rm -f "$PID_FILE"

# ── 3. Ensure the virtualenv exists and has our deps ───────────────────────────
# The middleware runs from its own venv so that Synology/DSM updates (which
# periodically wipe /usr/lib/python3.8/site-packages) can't take attendance
# down. If the venv is missing or broken, rebuild it from requirements.txt.
VENV="$APP_DIR/venv"
if [ ! -x "$VENV/bin/gunicorn" ] || ! "$VENV/bin/python" -c "import flask" 2>/dev/null; then
    log "venv missing or incomplete — (re)building $VENV"
    python3 -m venv "$VENV" 2>/dev/null || python3 -m venv --without-pip "$VENV"
    "$VENV/bin/python" -m ensurepip --upgrade 2>/dev/null
    "$VENV/bin/python" -m pip install --upgrade pip >/dev/null 2>&1
    if [ -f "$APP_DIR/requirements.txt" ]; then
        "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" || {
            log "ERROR: failed to install requirements into venv"; exit 1; }
    else
        "$VENV/bin/pip" install flask gunicorn || {
            log "ERROR: failed to install flask/gunicorn into venv"; exit 1; }
    fi
fi

# ── 4. Locate gunicorn (prefer the venv) ───────────────────────────────────────
GUNICORN=""
for candidate in "$VENV/bin/gunicorn" gunicorn /usr/local/bin/gunicorn /volume1/@appstore/py3k/usr/local/bin/gunicorn; do
    if command -v "$candidate" >/dev/null 2>&1; then
        GUNICORN="$candidate"
        break
    fi
done
if [ -z "$GUNICORN" ]; then
    if "$VENV/bin/python" -c "import gunicorn" 2>/dev/null; then
        GUNICORN="$VENV/bin/python -m gunicorn"
    else
        log "ERROR: gunicorn not installed. Run: $VENV/bin/pip install gunicorn"
        exit 1
    fi
fi
log "gunicorn: $GUNICORN"

# ── 5. Launch ──────────────────────────────────────────────────────────────────
log "launching on 0.0.0.0:$PORT (1 worker, 4 threads)"
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

# Give gunicorn time to bind or fail
sleep 3

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    log "started — master PID $(cat "$PID_FILE")"
    log "logs: $LOG_FILE  /  $ERR_LOG  /  $ACCESS_LOG"
    exit 0
else
    log "ERROR: gunicorn failed to start"
    if [ -f "$ERR_LOG" ]; then
        log "last 30 lines of $ERR_LOG:"
        tail -n 30 "$ERR_LOG"
    fi
    exit 1
fi
