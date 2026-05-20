"""
test_lateness_email.py — End-to-end check that lateness emails actually leave the box.

Three modes, pick one:

  --smtp-only <to_email>
        Lowest-level test. Builds a sample lateness email and pushes it through
        smtplib using the .env SMTP settings. Doesn't touch Odoo. Confirms the
        SMTP host, port, TLS, and credentials are correct.

  --employee <PIN> [--minutes-late N] [--occurrence N]
        Mid-level test. Looks up the employee by device PIN in employee_map.json,
        reads their work_email from Odoo, and sends a sample lateness email.
        Confirms the Odoo lookup AND email path work together.

  --simulate-punch <PIN> [--minutes-late N]
        Full end-to-end. Calls the real _check_lateness() with a synthetic late
        punch. Writes to lateness_store.json, posts an Odoo chatter note, sends
        the email — exactly as if the device pushed a late check-in just now.
        WARNING: this mutates state (lateness occurrence counter, Odoo records).

Examples:
    python3 test_lateness_email.py --smtp-only eugene@example.com
    python3 test_lateness_email.py --employee 9
    python3 test_lateness_email.py --employee 9 --minutes-late 45 --occurrence 4
    python3 test_lateness_email.py --simulate-punch 9 --minutes-late 30
"""

import argparse
import logging
import sys
from datetime import datetime, timezone, timedelta

from config import Config
from notifier import send_lateness_email

# Same log format as the live app so output is easy to read
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("test_lateness")


def _action_for(occurrence: int) -> tuple[str, bool]:
    """Mirror the disciplinary ladder used by lateness_tracker."""
    if occurrence <= 2:
        return ("System Record / Informal Caution", False)
    if occurrence == 3:
        return ("Formal Verbal Counseling", True)
    if occurrence == 4:
        return ("First Written Warning", True)
    if occurrence == 5:
        return ("Final Written Warning", True)
    return ("Show-Cause Letter and Disciplinary Hearing", True)


def _build_dummy_punch(minutes_late: int) -> datetime:
    """Return a local datetime that is `minutes_late` after WORK_START_TIME today."""
    tz = timezone(timedelta(hours=Config.DEVICE_TIMEZONE_OFFSET))
    h, m = map(int, Config.WORK_START_TIME.split(":"))
    return datetime.now(tz).replace(
        hour=h, minute=m, second=0, microsecond=0
    ) + timedelta(minutes=Config.LATE_GRACE_MINUTES + minutes_late)


# ── Mode 1: pure SMTP test ─────────────────────────────────────────────────────

def mode_smtp_only(to_email: str, minutes_late: int, occurrence: int):
    log.info(f"SMTP_HOST={Config.SMTP_HOST!r} SMTP_PORT={Config.SMTP_PORT} "
             f"TLS={Config.SMTP_USE_TLS} USER={Config.SMTP_USER!r} FROM={Config.SMTP_FROM!r}")
    if not Config.SMTP_HOST:
        log.error("SMTP_HOST is blank in .env — fix that first, no email can be sent.")
        sys.exit(2)

    action, is_formal = _action_for(occurrence)
    punch = _build_dummy_punch(minutes_late)
    month = punch.strftime("%Y-%m")

    log.info(f"Sending sample lateness email to {to_email} "
             f"(minutes_late={minutes_late}, occurrence={occurrence}, formal={is_formal})")

    ok = send_lateness_email(
        to_email=to_email,
        employee_name="TEST USER",
        punch_time=punch,
        minutes_late=minutes_late,
        occurrence=occurrence,
        month=month,
        action=action,
        is_formal=is_formal,
    )
    if ok:
        log.info("✓ smtplib reports send succeeded — check the inbox.")
    else:
        log.error("✗ Send failed — see error above.")
        sys.exit(1)


# ── Mode 2: PIN → Odoo email lookup → send ─────────────────────────────────────

def mode_employee(pin: str, minutes_late: int, occurrence: int):
    from employee_map import EmployeeMap
    from odoo_client import OdooClient

    emap = EmployeeMap(Config.EMPLOYEE_MAP_FILE)
    employee_id = emap.get(pin)
    if not employee_id:
        log.error(f"PIN {pin!r} is not in {Config.EMPLOYEE_MAP_FILE}")
        sys.exit(2)

    odoo = OdooClient(Config.ODOO_URL, Config.ODOO_DB, Config.ODOO_USER,
                      Config.ODOO_PASSWORD, Config.ODOO_COMPANY_ID)
    name  = odoo.get_employee_name(employee_id)
    email = odoo.get_employee_email(employee_id)
    log.info(f"Resolved PIN {pin} → employee_id={employee_id} name={name!r} email={email!r}")
    if not email:
        log.error("Employee has no work_email set in Odoo. Set it and retry.")
        sys.exit(2)

    action, is_formal = _action_for(occurrence)
    punch = _build_dummy_punch(minutes_late)
    month = punch.strftime("%Y-%m")

    ok = send_lateness_email(
        to_email=email,
        employee_name=name,
        punch_time=punch,
        minutes_late=minutes_late,
        occurrence=occurrence,
        month=month,
        action=action,
        is_formal=is_formal,
    )
    if ok:
        log.info(f"✓ Email accepted by SMTP server. Recipient: {email}"
                 + (f" (+ HR cc: {Config.HR_EMAIL})" if is_formal and Config.HR_EMAIL else ""))
    else:
        log.error("✗ Send failed.")
        sys.exit(1)


# ── Mode 3: simulate a real punch through the whole pipeline ───────────────────

def mode_simulate_punch(pin: str, minutes_late: int):
    """
    Runs the SAME code path as a real late check-in: writes to lateness_store,
    posts an Odoo chatter note, creates a disciplinary activity if formal,
    and sends the email.
    """
    from employee_map import EmployeeMap
    from odoo_client import OdooClient
    from lateness_tracker import LatenessTracker
    # Lazy-import app so its module-level boot logging shows up in our output
    import app  # noqa: F401

    emap = EmployeeMap(Config.EMPLOYEE_MAP_FILE)
    employee_id = emap.get(pin)
    if not employee_id:
        log.error(f"PIN {pin!r} not in employee map.")
        sys.exit(2)

    # Build a fake late punch (local time)
    local_punch = _build_dummy_punch(minutes_late)
    # Convert to UTC the way _process_punch does, then back — _check_lateness expects UTC
    punch_utc = local_punch.astimezone(timezone.utc).replace(tzinfo=None)

    log.warning("This mode MUTATES state: lateness counter, Odoo chatter, Odoo activity, real email.")
    log.info(f"Simulating: PIN={pin} employee_id={employee_id} "
             f"local_punch={local_punch.isoformat()} punch_utc={punch_utc.isoformat()}")

    # Call the real lateness handler exactly the way app.py does
    app._check_lateness(employee_id, pin, punch_utc)
    log.info("Done. Inspect middleware.log and the employee's Odoo chatter to verify.")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Test lateness email delivery.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--smtp-only", metavar="EMAIL",
                   help="Send a sample email directly to EMAIL via SMTP. Skips Odoo.")
    g.add_argument("--employee", metavar="PIN",
                   help="Look up employee by PIN in Odoo and send a sample email to their work_email.")
    g.add_argument("--simulate-punch", metavar="PIN",
                   help="Run the full lateness pipeline as if the device sent a late check-in now.")

    p.add_argument("--minutes-late", type=int, default=15,
                   help="How many minutes past the grace deadline (default: 15).")
    p.add_argument("--occurrence", type=int, default=1,
                   help="Which occurrence # to render in the email (default: 1). "
                        "Use 3+ to test the formal/HR-cc variant. Ignored by --simulate-punch.")

    args = p.parse_args()

    if args.smtp_only:
        mode_smtp_only(args.smtp_only, args.minutes_late, args.occurrence)
    elif args.employee:
        mode_employee(args.employee, args.minutes_late, args.occurrence)
    elif args.simulate_punch:
        mode_simulate_punch(args.simulate_punch, args.minutes_late)


if __name__ == "__main__":
    main()
