"""
odoo_client.py — Thin wrapper around Odoo's XML-RPC external API.
Handles authentication and hr.attendance record management.
"""

import logging
import xmlrpc.client
from datetime import datetime

log = logging.getLogger(__name__)

# Odoo stores datetimes in UTC, naive format
ODOO_DT_FORMAT = "%Y-%m-%d %H:%M:%S"


class OdooClient:

    def __init__(self, url: str, db: str, username: str, password: str, company_id: int = 0):
        self.url        = url.rstrip("/")
        self.db         = db
        self.username   = username
        self.password   = password
        self.company_id = company_id   # 0 = use user's default company
        self._uid       = None
        self._models    = None
        self._common    = None

    # ── Auth ───────────────────────────────────────────────────────────────────

    def _connect(self):
        """Authenticate and cache the XML-RPC proxies."""
        if self._uid:
            return

        log.info(f"[ODOO] Authenticating as {self.username} on {self.url}")
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        uid = common.authenticate(self.db, self.username, self.password, {})

        if not uid:
            raise ConnectionError(
                f"Odoo authentication failed for user '{self.username}'. "
                "Check credentials and that the user exists in the database."
            )

        self._uid = uid
        self._common = common
        self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
        log.info(f"[ODOO] Authenticated. uid={uid}")

    def _call(self, model: str, method: str, args: list, kwargs: dict = None):
        """Execute an XML-RPC call, reconnecting once on failure."""
        self._connect()

        # Build context — inject company if configured
        kw = dict(kwargs or {})
        if self.company_id:
            ctx = dict(kw.get("context", {}))
            ctx["allowed_company_ids"] = [self.company_id]
            ctx["force_company"]       = self.company_id
            kw["context"] = ctx

        try:
            return self._models.execute_kw(
                self.db, self._uid, self.password,
                model, method, args, kw
            )
        except xmlrpc.client.Fault as e:
            log.error(f"[ODOO] XML-RPC fault: {e.faultString}")
            raise
        except Exception as e:
            self._uid = None
            log.warning(f"[ODOO] Connection error, will retry next call: {e}")
            raise

    # ── hr.attendance helpers ──────────────────────────────────────────────────

    def check_in(self, employee_id: int, punch_time: datetime) -> int:
        """Create a new open attendance record (check_in set, check_out empty)."""
        vals = {
            "employee_id": employee_id,
            "check_in": punch_time.strftime(ODOO_DT_FORMAT),
        }
        record_id = self._call("hr.attendance", "create", [vals])
        log.debug(f"[ODOO] Created attendance id={record_id} for employee {employee_id}")
        return record_id

    def check_out(self, employee_id: int, punch_time: datetime) -> bool:
        """
        Find the latest open attendance record (no check_out) for this employee
        and set its check_out time.
        Returns True if a record was updated, False if none found.
        """
        open_records = self._call(
            "hr.attendance", "search_read",
            [[
                ["employee_id", "=", employee_id],
                ["check_out", "=", False],
            ]],
            {
                "fields": ["id", "check_in"],
                "order": "check_in desc",
                "limit": 1,
            }
        )

        if not open_records:
            log.warning(
                f"[ODOO] No open attendance record found for employee {employee_id}. "
                "Creating a check-out-only record is not possible in Odoo. "
                "Consider reviewing the device status mapping."
            )
            return False

        record_id = open_records[0]["id"]
        self._call(
            "hr.attendance", "write",
            [[record_id], {"check_out": punch_time.strftime(ODOO_DT_FORMAT)}]
        )
        log.debug(f"[ODOO] Closed attendance id={record_id} for employee {employee_id}")
        return True

    def has_open_attendance(self, employee_id: int) -> bool:
        """Return True if the employee has an open (no check_out) attendance record."""
        count = self._call(
            "hr.attendance", "search_count",
            [[
                ["employee_id", "=", employee_id],
                ["check_out", "=", False],
            ]]
        )
        return count > 0

    # ── Utility ────────────────────────────────────────────────────────────────

    def is_duplicate(self, employee_id: int, punch_time: datetime) -> bool:
        """
        Return True if an attendance record already exists for this employee
        within ±60 seconds of punch_time. Prevents duplicates on device retry.
        """
        from datetime import timedelta
        window = timedelta(seconds=60)
        t_from = (punch_time - window).strftime(ODOO_DT_FORMAT)
        t_to   = (punch_time + window).strftime(ODOO_DT_FORMAT)

        count = self._call(
            "hr.attendance", "search_count",
            [[
                ["employee_id", "=", employee_id],
                ["check_in", ">=", t_from],
                ["check_in", "<=", t_to],
            ]]
        )
        return count > 0

    def get_employees(self):
        """Fetch all active employees. Returns list of {id, name} dicts."""
        return self._call(
            "hr.employee", "search_read",
            [[["active", "=", True]]],
            {"fields": ["id", "name"], "order": "name asc", "limit": 0}
        )

    def get_employee_email(self, employee_id: int) -> str:
        """Return the work email of an employee, or empty string if not set."""
        try:
            result = self._call(
                "hr.employee", "read",
                [[employee_id]],
                {"fields": ["work_email", "name"]}
            )
            if result:
                return result[0].get("work_email") or ""
        except Exception as e:
            log.warning(f"[ODOO] Could not fetch employee email: {e}")
        return ""

    def get_employee_name(self, employee_id: int) -> str:
        """Return the name of an employee."""
        try:
            result = self._call(
                "hr.employee", "read",
                [[employee_id]],
                {"fields": ["name"]}
            )
            if result:
                return result[0].get("name") or f"Employee {employee_id}"
        except Exception as e:
            log.warning(f"[ODOO] Could not fetch employee name: {e}")
        return f"Employee {employee_id}"

    def log_lateness_note(self, employee_id: int, punch_time,
                          minutes_late: int, occurrence: int,
                          month: str, action: str, is_formal: bool) -> bool:
        """
        Post a chatter note on the employee record in Odoo.
        Visible in Employees app → employee profile → chatter.
        """
        from datetime import datetime as dt
        month_label = dt.strptime(month, "%Y-%m").strftime("%B %Y")
        date_label  = punch_time.strftime("%d %b %Y %I:%M %p")
        icon = "🔴" if is_formal else "🟡"
        body = (
            f"{icon} <strong>Late Arrival — Occurrence #{occurrence} in {month_label}</strong><br/>"
            f"<b>Check-in:</b> {date_label}<br/>"
            f"<b>Minutes Late:</b> {minutes_late}<br/>"
            f"<b>Action:</b> {action}<br/>"
            f"<i>Logged automatically by the MB360 Attendance Middleware.</i>"
        )
        try:
            self._call(
                "hr.employee", "message_post",
                [employee_id],
                {
                    "body":          body,
                    "message_type":  "comment",
                    "subtype_xmlid": "mail.mt_note",
                }
            )
            log.info(f"[ODOO] Lateness note posted for employee {employee_id}")
            return True
        except Exception as e:
            log.warning(f"[ODOO] Could not post lateness note: {e}")
            return False

    def create_disciplinary_activity(self, employee_id: int, punch_time,
                                     occurrence: int, month: str,
                                     action: str) -> bool:
        """
        Create a mail.activity on the employee record assigned to HR.
        Appears as a to-do task on the HR dashboard in Odoo Online.
        Only called for formal actions (occurrence >= 3).
        """
        from datetime import datetime as dt, timedelta
        month_label = dt.strptime(month, "%Y-%m").strftime("%B %Y")
        date_label  = punch_time.strftime("%d %b %Y")
        due_date    = (punch_time + timedelta(days=3)).strftime("%Y-%m-%d")

        note = (
            f"<p><strong>Late Arrival — Occurrence #{occurrence} ({month_label})</strong></p>"
            f"<p>Employee recorded late on {date_label}. "
            f"Required action: <strong>{action}</strong></p>"
            f"<p>Please schedule the appropriate HR intervention and update "
            f"the employee record accordingly.</p>"
        )

        try:
            # Find the To-Do activity type
            activity_types = self._call(
                "mail.activity.type", "search_read",
                [[["name", "ilike", "todo"]]],
                {"fields": ["id", "name"], "limit": 1}
            )
            if not activity_types:
                activity_types = self._call(
                    "mail.activity.type", "search_read",
                    [[]],
                    {"fields": ["id", "name"], "limit": 1}
                )
            activity_type_id = activity_types[0]["id"] if activity_types else 4

            self._call(
                "mail.activity", "create",
                [{
                    "res_model":        "hr.employee",
                    "res_id":           employee_id,
                    "activity_type_id": activity_type_id,
                    "summary":          f"Disciplinary Action Required — {action}",
                    "note":             note,
                    "date_deadline":    due_date,
                }]
            )
            log.info(
                f"[ODOO] Disciplinary activity created for employee {employee_id} "
                f"| occurrence #{occurrence} | action: {action}"
            )
            return True
        except Exception as e:
            log.warning(f"[ODOO] Could not create disciplinary activity: {e}")
            return False

    def get_all_open_attendances(self) -> list:
        """
        Return all open attendance records (no check_out) across employees.
        Each dict: {id, employee_id, check_in}.
        """
        recs = self._call(
            "hr.attendance", "search_read",
            [[["check_out", "=", False]]],
            {"fields": ["id", "employee_id", "check_in"], "order": "check_in asc", "limit": 0}
        )
        out = []
        for r in recs:
            emp = r.get("employee_id")
            # Odoo returns [id, name] for m2o fields
            emp_id = emp[0] if isinstance(emp, (list, tuple)) and emp else emp
            out.append({
                "id":          r["id"],
                "employee_id": emp_id,
                "check_in":    str(r["check_in"])[:19] if r.get("check_in") else None,
            })
        return out

    def close_attendance(self, attendance_id: int, check_out_time: datetime) -> bool:
        """Set check_out on a specific attendance record by ID."""
        self._call(
            "hr.attendance", "write",
            [[attendance_id], {"check_out": check_out_time.strftime(ODOO_DT_FORMAT)}]
        )
        log.debug(f"[ODOO] Closed attendance id={attendance_id} @ {check_out_time} UTC")
        return True

    def has_approved_overtime(self, employee_id: int, on_date) -> bool:
        """
        Return True if the employee has an approved approval.request in the
        'Overtime' category that covers `on_date` (a datetime.date).

        Matches request_status='approved' and category name ILIKE the configured
        overtime category. Date match tries date_start/date_end first, then date.
        """
        from datetime import datetime as dt, time as _time

        # Resolve employee → user (approval.request uses request_owner_id = res.users)
        try:
            emp = self._call(
                "hr.employee", "read",
                [[employee_id]],
                {"fields": ["user_id"]}
            )
        except Exception as e:
            log.warning(f"[ODOO] Could not read user_id for employee {employee_id}: {e}")
            return False

        if not emp:
            return False
        user_field = emp[0].get("user_id")
        user_id = user_field[0] if isinstance(user_field, (list, tuple)) and user_field else None
        if not user_id:
            return False

        # Import here to avoid circular at module load
        from config import Config
        day_start = dt.combine(on_date, _time.min).strftime(ODOO_DT_FORMAT)
        day_end   = dt.combine(on_date, _time.max).strftime(ODOO_DT_FORMAT)
        day_str   = on_date.strftime("%Y-%m-%d")

        # Try (date_start <= end-of-day) AND (date_end >= start-of-day)
        try:
            count = self._call(
                "approval.request", "search_count",
                [[
                    ["request_owner_id", "=", user_id],
                    ["request_status",   "=", "approved"],
                    ["category_id.name", "ilike", Config.OVERTIME_APPROVAL_CATEGORY],
                    "|",
                        "&", ["date_start", "<=", day_end], ["date_end", ">=", day_start],
                        ["date", "=", day_str],
                ]]
            )
            return count > 0
        except Exception as e:
            log.warning(f"[ODOO] Overtime lookup failed (employee {employee_id}): {e}")
            return False

    def get_open_attendance(self, employee_id: int):
        """
        Return the latest open attendance record (no check_out) for this employee,
        or None if no open record exists.
        Returns dict with keys: id, check_in (UTC string "%Y-%m-%d %H:%M:%S")
        """
        records = self._call(
            "hr.attendance", "search_read",
            [[
                ["employee_id", "=", employee_id],
                ["check_out",   "=", False],
            ]],
            {
                "fields": ["id", "check_in"],
                "order":  "check_in desc",
                "limit":  1,
            }
        )
        if not records:
            return None
        rec = records[0]
        # Normalise check_in — Odoo returns a datetime string or False
        if rec.get("check_in"):
            # Odoo may return "2026-04-28 05:30:00" (UTC naive string)
            rec["check_in"] = str(rec["check_in"])[:19]
        return rec
