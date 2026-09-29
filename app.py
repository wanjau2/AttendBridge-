"""
MB360 → Odoo Online Middleware
Implements the ZKTeco iClock/ADMS push protocol.
The MB360 pushes attendance logs here; this server writes them to Odoo via XML-RPC.

ZMM501-NF28VF-Ver1.0.8 (the device at Chambers) puts the verify method in
field 2, not a reliable in/out flag:

    0 = password, 1 = fingerprint, 15 = face, 255 = automatic state

Only 0 and 1 are treated as explicit check-in / check-out. Every other value
is decided from the employee's open attendance in Odoo: no open row means
check-in, an open row at least MIN_HOURS_BEFORE_CHECKOUT later means check-out.
"""

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from flask import Flask, request, Response
from config import Config
from odoo_client import OdooClient
from employee_map import EmployeeMap
from lateness_tracker import LatenessTracker
from notifier import send_lateness_email

# EAT is UTC+3. Odoo Online always stores datetimes in UTC.
DEVICE_TZ = timezone(timedelta(hours=Config.DEVICE_TIMEZONE_OFFSET))

# Values of ATTLOG field 2 that this firmware uses as an explicit direction.
# Anything else is a verify method or an automatic state and must not be dropped.
EXPLICIT_DIRECTIONS = (0, 1)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("middleware.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── App setup ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
odoo = OdooClient(Config.ODOO_URL, Config.ODOO_DB, Config.ODOO_USER, Config.ODOO_PASSWORD, Config.ODOO_COMPANY_ID)
emap = EmployeeMap(Config.EMPLOYEE_MAP_FILE)
tracker = LatenessTracker(Config.LATENESS_STORE_FILE)

# Tracks last-seen state of the MB360 device
_device_state = {}


def _touch_device(sn: str):
    """Record that the terminal just contacted us."""
    _device_state.update({
        "sn":           sn,
        "ip":           request.remote_addr,
        "last_seen":    datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "last_seen_ts": time.time(),
    })


# ── ADMS endpoints ─────────────────────────────────────────────────────────────

@app.route("/iclock/cdata", methods=["GET"])
def device_init():
    """
    Called by the MB360 on startup / reconnect.
    We return the device configuration (polling interval, flags, etc.).

    ATTLOGStamp stays 9999 on purpose. The terminal is already pushing live
    punches; a stamp of 0 would make it replay its entire on-device history.
    """
    sn = request.args.get("SN", "UNKNOWN")
    _touch_device(sn)
    log.info(f"[INIT] Device connected: SN={sn} IP={request.remote_addr}")

    # iClock configuration response — device will use these settings.
    # ZKTeco firmware expects CRLF line endings.
    body = (
        f"GET OPTION FROM: {sn}\r\n"
        "ATTLOGStamp=9999\r\n"
        "OPERLOGStamp=9999\r\n"
        "ATTPHOTOStamp=9999\r\n"
        "ErrorDelay=30\r\n"
        f"Delay={Config.PUSH_INTERVAL_SECONDS}\r\n"
        "TransTimes=00:00;23:59\r\n"
        "TransInterval=1\r\n"
        "TransFlag=TransData AttLog\r\n"
        f"TimeZone={Config.DEVICE_TIMEZONE_OFFSET}\r\n"
        "Realtime=1\r\n"
        "Encrypt=0\r\n"
    )
    return Response(body, mimetype="text/plain")


@app.route("/iclock/cdata", methods=["POST"])
def receive_attendance():
    """
    The MB360 POSTs attendance records here.
    Query param: table=ATTLOG
    Body (one line per punch, tab-separated):
        <PIN> <DateTime> <Verified> <Status> <WorkCode> <Reserved>
    """
    sn = request.args.get("SN", "UNKNOWN")
    table = request.args.get("table", "")

    if table != "ATTLOG":
        log.info(f"[SKIP] Ignoring table={table} from SN={sn}")
        return Response("OK: 0", mimetype="text/plain")

    raw = request.data.decode("utf-8", errors="replace").strip()
    if not raw:
        return Response("OK: 0", mimetype="text/plain")

    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    log.info(f"[ATTLOG] SN={sn} | {len(lines)} record(s) received")

    accepted = 0
    retry = False
    for line in lines:
        try:
            if _process_punch(line, sn):
                accepted += 1
            else:
                # Unmapped PIN. Refuse the ACK so the terminal keeps the punch
                # and sends it again after the map is fixed.
                retry = True
        except Exception as e:
            log.error(f"[ERROR] Failed to process line '{line}': {e}")
            retry = True

    if retry:
        # A body that does not start with OK tells the terminal to keep the
        # batch and post it again. "OK: 0" would delete the punch.
        return Response(f"ERROR: {accepted}", mimetype="text/plain")

    return Response(f"OK: {accepted}", mimetype="text/plain")


@app.route("/iclock/getrequest", methods=["GET"])
def heartbeat():
    """Periodic keepalive from the device. Respond with OK."""
    sn = request.args.get("SN", "UNKNOWN")
    _touch_device(sn)
    log.debug(f"[HEARTBEAT] SN={sn}")
    return Response("OK", mimetype="text/plain")


@app.route("/iclock/ping", methods=["GET"])
def ping():
    """Secondary keepalive. This firmware calls it about every four minutes."""
    sn = request.args.get("SN", "UNKNOWN")
    _touch_device(sn)
    return Response("OK", mimetype="text/plain")


@app.route("/iclock/devicecmd", methods=["POST"])
def device_cmd_ack():
    """Device acknowledges a server command. Not used here but must return OK."""
    return Response("OK", mimetype="text/plain")


# ── Health check ───────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    """Live health check — tests Odoo connection and reports device last-seen time."""
    result = {
        "middleware": "ok",
        "odoo_url":   Config.ODOO_URL,
        "odoo":       "unknown",
        "odoo_uid":   None,
        "device_sn":  _device_state.get("sn",       "not_connected"),
        "device_ip":  _device_state.get("ip",        "unknown"),
        "device_last_seen": _device_state.get("last_seen", "never"),
        "device_status": "unknown",
        "employees_mapped": len(emap._map),
    }

    # Test Odoo connection live
    try:
        odoo._connect()
        result["odoo"]     = "ok"
        result["odoo_uid"] = odoo._uid
    except Exception as e:
        result["odoo"]  = "error"
        result["odoo_error"] = str(e)

    # Device status — consider offline if no heartbeat in last 60 seconds
    last_seen = _device_state.get("last_seen_ts")
    if last_seen:
        seconds_ago = time.time() - last_seen
        result["device_status"]       = "online" if seconds_ago < 60 else "offline"
        result["device_seconds_ago"]  = round(seconds_ago)
    else:
        result["device_status"] = "never_connected"

    overall = "ok" if result["odoo"] == "ok" and result["device_status"] == "online" else "degraded"
    result["status"] = overall

    return result, 200


# ── Core punch logic ───────────────────────────────────────────────────────────

def _direction_for(pin: str, field2: int) -> int:
    """
    Return 0 (check-in path) or 1 (check-out path) for one ATTLOG field 2.

    0 and 1 keep the original meaning. Face punches arrive as 15 and were
    previously discarded at DEBUG level after the terminal had already been
    told the upload succeeded.
    """
    if field2 in EXPLICIT_DIRECTIONS:
        return field2

    log.info(
        "[AUTO] PIN=%s field2=%s — deciding in/out from open attendance",
        pin, field2,
    )
    return 0


def _process_punch(line: str, sn: str) -> bool:
    """
    Parse one ATTLOG line and write the appropriate record to Odoo.

    Returns True when the terminal may drop the line (it was stored, or it
    was an intentional duplicate / re-tap). Returns False when the line must
    be retried, which today means the PIN is not in the employee map.
    """
    log.info("[RAW] SN=%s %s", sn, line)

    parts = line.split("\t")
    if len(parts) < 4:
        raise ValueError(f"Too few fields: {line!r}")

    pin    = parts[0].strip()
    dt_str = parts[1].strip()
    field2 = int(parts[2].strip())
    direction = _direction_for(pin, field2)

    # Parse timestamp — device sends local EAT, convert to UTC for Odoo
    punch_time_local = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=DEVICE_TZ)
    punch_time = punch_time_local.astimezone(timezone.utc).replace(tzinfo=None)

    # Look up the Odoo employee_id for this device PIN
    employee_id = emap.get(pin)
    if not employee_id:
        log.warning(f"[MAP] No Odoo employee mapped for PIN={pin}. Holding for retry.")
        return False

    log.info(
        "[PARSE] PIN=%s field2=%s direction=%s time=%s UTC",
        pin, field2, direction, punch_time,
    )

    if direction == 0:
        _apply_check_in(pin, employee_id, punch_time)
    else:
        _apply_check_out(pin, employee_id, punch_time)
    return True


def _apply_check_in(pin: str, employee_id: int, punch_time: datetime):
    """
    Open a check-in, or turn this punch into a check-out when an open
    attendance from earlier today is already past the minimum hours.
    """
    open_rec = odoo.get_open_attendance(employee_id)
    if open_rec:
        check_in_utc  = datetime.strptime(open_rec["check_in"], "%Y-%m-%d %H:%M:%S")
        seconds_since = (punch_time - check_in_utc).total_seconds()
        hours_since   = seconds_since / 3600.0

        # Duplicate (rapid re-tap)
        if seconds_since < 60:
            log.warning(f"[DUP] Duplicate check-in PIN={pin} ({seconds_since:.0f}s after last) — ignored")
            return

        # 4h-rule: any same-day punch ≥ MIN_HOURS_BEFORE_CHECKOUT after an open
        # check-in is treated as a check-out, regardless of what the device's
        # direction field says. Evening exits on this firmware are not marked
        # as check-out.
        same_day = check_in_utc.date() == punch_time.date()
        if same_day and hours_since >= Config.MIN_HOURS_BEFORE_CHECKOUT:
            odoo.check_out(employee_id, punch_time)
            log.info(
                f"[OUT] employee_id={employee_id} PIN={pin} at {punch_time} UTC "
                f"(auto-detected: {hours_since:.1f}h after check-in)"
            )
            return

        # Different calendar day — yesterday's record was never closed.
        # Close it at WORK_END_TIME + grace of the original date, then create
        # today's check-in below.
        if not same_day:
            h, m = map(int, Config.WORK_END_TIME.split(":"))
            ci_local = check_in_utc.replace(tzinfo=timezone.utc).astimezone(DEVICE_TZ)
            close_local = ci_local.replace(
                hour=h, minute=m, second=0, microsecond=0
            ) + timedelta(minutes=Config.AUTO_CHECKOUT_GRACE_MINUTES)
            close_utc = close_local.astimezone(timezone.utc).replace(tzinfo=None)
            log.warning(
                f"[STALE] PIN={pin} unclosed record from {open_rec['check_in']} UTC "
                f"— closing at {close_utc} UTC"
            )
            odoo.check_out(employee_id, close_utc)
        else:
            # Same day, <4h since check-in, not a duplicate — likely an
            # accidental re-tap on the check-in side. Ignore.
            log.warning(
                f"[SKIP] PIN={pin} re-punch {hours_since:.1f}h after check-in "
                f"(< {Config.MIN_HOURS_BEFORE_CHECKOUT}h) — ignored"
            )
            return

    odoo.check_in(employee_id, punch_time)
    log.info(f"[IN]  employee_id={employee_id} PIN={pin} at {punch_time} UTC")
    _check_lateness(employee_id, pin, punch_time)


def _apply_check_out(pin: str, employee_id: int, punch_time: datetime):
    """Close the open attendance. An explicit check-out with nothing open is ignored."""
    open_rec = odoo.get_open_attendance(employee_id)
    if not open_rec:
        log.warning(f"[SKIP] Check-out for PIN={pin} but no open record in Odoo — ignored")
        return

    check_in_utc  = datetime.strptime(open_rec["check_in"], "%Y-%m-%d %H:%M:%S")
    seconds_since = (punch_time - check_in_utc).total_seconds()

    if seconds_since < 60:
        log.warning(f"[DUP] Duplicate check-out PIN={pin} ({seconds_since:.0f}s after check-in) — ignored")
        return

    odoo.check_out(employee_id, punch_time)
    log.info(f"[OUT] employee_id={employee_id} PIN={pin} at {punch_time} UTC")


# ── Lateness detection ─────────────────────────────────────────────────────────

def _check_lateness(employee_id: int, pin: str, punch_time: datetime):
    """
    Compare punch_time (UTC) against the configured work start time.
    If late, record the occurrence, notify the employee, and log to Odoo.
    Skips weekends and non-working days per WORK_DAYS config.
    """
    # Convert punch UTC back to local time for comparison
    local_punch = punch_time.replace(tzinfo=timezone.utc).astimezone(
        timezone(timedelta(hours=Config.DEVICE_TIMEZONE_OFFSET))
    )

    # Skip non-working days
    if local_punch.weekday() not in Config.WORK_DAYS:
        log.debug(f"[LATENESS] Skipping non-working day for PIN={pin}")
        return

    # Parse work start time
    h, m = map(int, Config.WORK_START_TIME.split(":"))
    work_start = local_punch.replace(hour=h, minute=m, second=0, microsecond=0)
    deadline   = work_start + timedelta(minutes=Config.LATE_GRACE_MINUTES)

    if local_punch <= deadline:
        return  # On time

    minutes_late = int((local_punch - deadline).total_seconds() / 60)
    log.info(f"[LATENESS] PIN={pin} is {minutes_late} min late")

    # Record occurrence and get disciplinary action
    result = tracker.record(employee_id, local_punch, minutes_late)

    # Fetch employee details from Odoo once
    employee_name  = odoo.get_employee_name(employee_id) or f"PIN {pin}"
    employee_email = odoo.get_employee_email(employee_id)

    # Log note on Odoo employee record (always)
    odoo.log_lateness_note(
        employee_id, local_punch,
        minutes_late, result["occurrence"],
        result["month"], result["action"], result["is_formal"]
    )

    # Create Odoo Activity for HR on formal actions (occurrence 3+)
    if result["is_formal"]:
        odoo.create_disciplinary_activity(
            employee_id, local_punch,
            result["occurrence"], result["month"],
            result["action"]
        )
        log.info(
            f"[LATENESS] Disciplinary activity created for {employee_name} "
            f"| occurrence #{result['occurrence']} | {result['action']}"
        )

    # Send email notification to employee
    if employee_email:
        sent = send_lateness_email(
            to_email=employee_email,
            employee_name=employee_name,
            punch_time=local_punch,
            minutes_late=minutes_late,
            occurrence=result["occurrence"],
            month=result["month"],
            action=result["action"],
            is_formal=result["is_formal"],
        )
        if sent:
            tracker.mark_notified(employee_id, result["month"])
    else:
        log.warning(f"[LATENESS] No work email for {employee_name} — skipping email")


# ── Auto-checkout sweep ────────────────────────────────────────────────────────

_sweep_state = {"last_run_date": None}


def _auto_checkout_sweep():
    """
    Close every open attendance whose employee does NOT have an approved
    overtime request for today. Recorded check_out = WORK_END_TIME + grace
    (local), converted to UTC.
    """
    h, m = map(int, Config.WORK_END_TIME.split(":"))
    now_local = datetime.now(DEVICE_TZ)
    cutoff_local = now_local.replace(
        hour=h, minute=m, second=0, microsecond=0
    ) + timedelta(minutes=Config.AUTO_CHECKOUT_GRACE_MINUTES)
    cutoff_utc = cutoff_local.astimezone(timezone.utc).replace(tzinfo=None)

    try:
        open_recs = odoo.get_all_open_attendances()
    except Exception as e:
        log.error(f"[AUTO-OUT] Could not fetch open attendances: {e}")
        return

    log.info(f"[AUTO-OUT] Sweep starting — {len(open_recs)} open record(s)")
    closed = 0
    skipped_ot = 0
    for rec in open_recs:
        emp_id = rec["employee_id"]
        try:
            if odoo.has_approved_overtime(emp_id, now_local.date()):
                skipped_ot += 1
                log.info(f"[AUTO-OUT] employee_id={emp_id} has approved overtime — skip")
                continue
            odoo.close_attendance(rec["id"], cutoff_utc)
            closed += 1
            log.info(f"[AUTO-OUT] employee_id={emp_id} auto-closed at {cutoff_utc} UTC")
        except Exception as e:
            log.error(f"[AUTO-OUT] Failed to close attendance id={rec['id']}: {e}")

    log.info(f"[AUTO-OUT] Sweep done — closed={closed} skipped_overtime={skipped_ot}")


def _sweep_loop():
    """
    Daemon loop: every 60s check whether we've crossed WORK_END_TIME + grace
    on a working day and haven't yet run today. If so, run the sweep once.
    """
    h, m = map(int, Config.WORK_END_TIME.split(":"))
    while True:
        try:
            now_local = datetime.now(DEVICE_TZ)
            today = now_local.date()
            trigger = now_local.replace(
                hour=h, minute=m, second=0, microsecond=0
            ) + timedelta(minutes=Config.AUTO_CHECKOUT_GRACE_MINUTES)

            is_workday = now_local.weekday() in Config.WORK_DAYS
            already_ran = _sweep_state["last_run_date"] == today

            if is_workday and not already_ran and now_local >= trigger:
                _sweep_state["last_run_date"] = today
                _auto_checkout_sweep()
        except Exception as e:
            log.error(f"[AUTO-OUT] Sweep loop error: {e}")
        time.sleep(60)


@app.route("/admin/auto-checkout", methods=["POST"])
def admin_auto_checkout():
    """Manual trigger for the auto-checkout sweep (testing / one-off use)."""
    _auto_checkout_sweep()
    return Response("OK", mimetype="text/plain")


# ── Boot ───────────────────────────────────────────────────────────────────────
# Runs on module import (gunicorn loads `app:app`) AND when executed directly.
# Gunicorn must be configured with --workers 1 to avoid running the sweep N times.

log.info("Starting MB360 → Odoo middleware")
log.info(f"  Odoo: {Config.ODOO_URL}  DB: {Config.ODOO_DB}")
log.info("  ATTLOG: field2 0/1 are explicit in/out; face and other codes use open attendance")

if Config.AUTO_CHECKOUT_ENABLED:
    log.info(
        f"  Auto-checkout: WORK_END={Config.WORK_END_TIME} "
        f"+ {Config.AUTO_CHECKOUT_GRACE_MINUTES}min grace"
    )
    threading.Thread(target=_sweep_loop, name="auto-checkout", daemon=True).start()


if __name__ == "__main__":
    # Local dev: Flask's built-in server. Production uses gunicorn (see restart.sh).
    log.info(f"  Listening on 0.0.0.0:{Config.LISTEN_PORT} (Flask dev server)")
    app.run(host="0.0.0.0", port=Config.LISTEN_PORT, debug=False)
