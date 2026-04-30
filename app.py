"""
MB360 → Odoo Online Middleware
Implements the ZKTeco iClock/ADMS push protocol.
The MB360 pushes attendance logs here; this server writes them to Odoo via XML-RPC.
"""

import logging
from datetime import datetime, timezone, timedelta
from flask import Flask, request, Response
from config import Config
from odoo_client import OdooClient
from employee_map import EmployeeMap
from lateness_tracker import LatenessTracker
from notifier import send_lateness_email

# EAT is UTC+3. Odoo Online always stores datetimes in UTC.
DEVICE_TZ = timezone(timedelta(hours=Config.DEVICE_TIMEZONE_OFFSET))

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
odoo = OdooClient(Config.ODOO_URL, Config.ODOO_DB, Config.ODOO_USER, Config.ODOO_PASSWORD)
emap = EmployeeMap(Config.EMPLOYEE_MAP_FILE)
tracker = LatenessTracker(Config.LATENESS_STORE_FILE)

# Tracks last-seen state of the MB360 device
_device_state = {}


# ── ADMS endpoints ─────────────────────────────────────────────────────────────

@app.route("/iclock/cdata", methods=["GET"])
def device_init():
    """
    Called by the MB360 on startup / reconnect.
    We return the device configuration (polling interval, flags, etc.).
    """
    import time
    sn = request.args.get("SN", "UNKNOWN")
    _device_state.update({
        "sn":          sn,
        "ip":          request.remote_addr,
        "last_seen":   datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "last_seen_ts": time.time(),
    })
    log.info(f"[INIT] Device connected: SN={sn} IP={request.remote_addr}")

    # iClock configuration response — device will use these settings
    body = (
        f"GET OPTION FROM: {sn}\n"
        "ATTLOGStamp=9999\n"
        "OPERLOGStamp=9999\n"
        "ATTPHOTOStamp=9999\n"
        "ErrorDelay=30\n"
        f"Delay={Config.PUSH_INTERVAL_SECONDS}\n"
        "TransTimes=00:00;23:59\n"
        "TransInterval=1\n"
        "TransFlag=TransData AttLog\n"
        f"TimeZone={Config.DEVICE_TIMEZONE_OFFSET}\n"
        "Realtime=1\n"
        "Encrypt=0\n"
    )
    return Response(body, mimetype="text/plain")


@app.route("/iclock/cdata", methods=["POST"])
def receive_attendance():
    """
    The MB360 POSTs attendance records here.
    Query param: table=ATTLOG
    Body (one line per punch):
        <PIN> \\t <DateTime> \\t <Verified> \\t <Status> \\t <WorkCode> \\t <Reserved>
    Status: 0=check-in, 1=check-out, 4=break-out, 5=break-in, 255=auto
    """
    sn = request.args.get("SN", "UNKNOWN")
    table = request.args.get("table", "")

    if table != "ATTLOG":
        log.debug(f"[SKIP] Ignoring table={table} from SN={sn}")
        return Response("OK: 0", mimetype="text/plain")

    raw = request.data.decode("utf-8", errors="replace").strip()
    if not raw:
        return Response("OK: 0", mimetype="text/plain")

    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    log.info(f"[ATTLOG] SN={sn} | {len(lines)} record(s) received")

    processed = 0
    for line in lines:
        try:
            _process_punch(line, sn)
            processed += 1
        except Exception as e:
            log.error(f"[ERROR] Failed to process line '{line}': {e}")

    return Response(f"OK: {processed}", mimetype="text/plain")


@app.route("/iclock/getrequest", methods=["GET"])
def heartbeat():
    """Periodic keepalive from the device. Respond with OK."""
    import time
    sn = request.args.get("SN", "UNKNOWN")
    _device_state.update({
        "sn":           sn,
        "ip":           request.remote_addr,
        "last_seen":    datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "last_seen_ts": time.time(),
    })
    log.debug(f"[HEARTBEAT] SN={sn}")
    return Response("OK", mimetype="text/plain")


@app.route("/iclock/devicecmd", methods=["POST"])
def device_cmd_ack():
    """Device acknowledges a server command. Not used here but must return OK."""
    return Response("OK", mimetype="text/plain")


# ── Health check ───────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    """Live health check — tests Odoo connection and reports device last-seen time."""
    import time

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

def _process_punch(line: str, sn: str):
    """
    Parse one ATTLOG line and write the appropriate record to Odoo.

    ZMM501-NF28VF firmware ATTLOG format (tab-separated):
        PIN  DateTime            Verified  Status  WorkCode ...
        9    2026-04-30 08:56:42  1         1       0  ...
        9    2026-04-30 08:57:56  0         1       0  ...

    IMPORTANT — firmware quirk on ZMM501-NF28VF Ver1.0.8:
        field[3] Status  → always 1, IGNORE
        field[2] Verified → encodes punch direction on this firmware:
                             0 = Check-In
                             1 = Check-Out
    """
    parts = line.split("\t")
    if len(parts) < 4:
        raise ValueError(f"Too few fields: {line!r}")

    pin    = parts[0].strip()
    dt_str = parts[1].strip()

    # ZMM501 firmware: direction is in field[2], not field[3]
    direction = int(parts[2].strip())   # 0 = Check-In, 1 = Check-Out

    # Parse timestamp — device sends local EAT, convert to UTC for Odoo
    punch_time_local = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=DEVICE_TZ)
    punch_time = punch_time_local.astimezone(timezone.utc).replace(tzinfo=None)

    # Look up the Odoo employee_id for this device PIN
    employee_id = emap.get(pin)
    if not employee_id:
        log.warning(f"[MAP] No Odoo employee mapped for PIN={pin}. Skipping.")
        return

    log.debug(f"[PARSE] PIN={pin} direction={direction} time={punch_time} (UTC)")

    if direction == 0:
        # ── Check-In ───────────────────────────────────────────────────────────
        # Guard: if there's already an open record within 60 seconds → duplicate
        open_rec = odoo.get_open_attendance(employee_id)
        if open_rec:
            check_in_utc = datetime.strptime(open_rec["check_in"], "%Y-%m-%d %H:%M:%S")
            seconds_since = (punch_time - check_in_utc).total_seconds()
            if seconds_since < 60:
                log.warning(f"[DUP] Duplicate check-in PIN={pin} ({seconds_since:.0f}s after last) — ignored")
                return
            # Open record exists but this is a new check-in (e.g. forgot to check out yesterday)
            # Close the stale record at midnight before creating the new check-in
            log.warning(
                f"[STALE] PIN={pin} has unclosed record from {open_rec['check_in']} UTC — closing it"
            )
            odoo.check_out(employee_id, check_in_utc.replace(hour=23, minute=59, second=59))

        odoo.check_in(employee_id, punch_time)
        log.info(f"[IN]  employee_id={employee_id} PIN={pin} at {punch_time} UTC")
        _check_lateness(employee_id, pin, punch_time)

    elif direction == 1:
        # ── Check-Out ──────────────────────────────────────────────────────────
        # Guard: if no open record, nothing to close
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

    else:
        log.debug(f"[SKIP] Unknown direction={direction} for PIN={pin}")


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


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Starting MB360 → Odoo middleware")
    log.info(f"  Odoo: {Config.ODOO_URL}  DB: {Config.ODOO_DB}")
    log.info(f"  Listening on 0.0.0.0:{Config.LISTEN_PORT}")
    app.run(host="0.0.0.0", port=Config.LISTEN_PORT, debug=False)
