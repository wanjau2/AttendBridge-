"""
lateness_tracker.py — Tracks late arrivals per employee on a rolling monthly basis.
Occurrences are stored in a local JSON file so they survive server restarts.
"""

import json
import logging
import os
from datetime import datetime, date

log = logging.getLogger(__name__)

# ── Disciplinary thresholds ────────────────────────────────────────────────────
# Maps occurrence number → (action label, is_formal)
DISCIPLINARY_ACTIONS = {
    1: ("System Record / Informal Caution",   False),
    2: ("System Record / Informal Caution",   False),
    3: ("Formal Verbal Counseling",           True),
    4: ("First Written Warning",              True),
    5: ("Final Written Warning",              True),
}
DISCIPLINARY_DEFAULT = ("Show-Cause Letter and Disciplinary Hearing", True)


def get_action(occurrence: int) -> tuple:
    """Return (action_label, is_formal) for the given monthly occurrence count."""
    return DISCIPLINARY_ACTIONS.get(occurrence, DISCIPLINARY_DEFAULT)


class LatenessTracker:
    """
    Persists monthly lateness occurrence counts to a JSON file.

    Storage format:
    {
      "<employee_id>": {
        "2026-04": {
          "count": 3,
          "occurrences": [
            {"date": "2026-04-01", "minutes_late": 12, "action": "...", "notified": true},
            ...
          ]
        }
      }
    }
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self._data = {}
        self._load()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self.filepath):
            with open(self.filepath) as f:
                self._data = json.load(f)
            log.info(f"[LATENESS] Loaded occurrence store from {self.filepath}")
        else:
            self._data = {}

    def _save(self):
        with open(self.filepath, "w") as f:
            json.dump(self._data, f, indent=2)

    # ── Core methods ───────────────────────────────────────────────────────────

    def record(self, employee_id: int, punch_time: datetime, minutes_late: int) -> dict:
        """
        Record a late arrival for the employee.
        Returns a dict with occurrence count, action label, and is_formal flag.
        """
        eid   = str(employee_id)
        month = punch_time.strftime("%Y-%m")   # e.g. "2026-04"
        today = punch_time.strftime("%Y-%m-%d")

        # Ensure nested structure exists
        self._data.setdefault(eid, {})
        self._data[eid].setdefault(month, {"count": 0, "occurrences": []})

        self._data[eid][month]["count"] += 1
        count = self._data[eid][month]["count"]
        action, is_formal = get_action(count)

        self._data[eid][month]["occurrences"].append({
            "date":         today,
            "minutes_late": minutes_late,
            "action":       action,
            "is_formal":    is_formal,
            "notified":     False,
        })

        self._save()
        log.info(
            f"[LATENESS] employee_id={employee_id} | {today} | "
            f"{minutes_late} min late | occurrence #{count} | action: {action}"
        )

        return {
            "occurrence":  count,
            "month":       month,
            "minutes_late": minutes_late,
            "action":      action,
            "is_formal":   is_formal,
        }

    def mark_notified(self, employee_id: int, month: str):
        """Mark the latest occurrence for this employee/month as notified."""
        eid = str(employee_id)
        try:
            occs = self._data[eid][month]["occurrences"]
            if occs:
                occs[-1]["notified"] = True
                self._save()
        except KeyError:
            pass

    def get_monthly_count(self, employee_id: int, month: str = None) -> int:
        """Return the occurrence count for the given month (default: current month)."""
        month = month or datetime.now().strftime("%Y-%m")
        eid   = str(employee_id)
        return self._data.get(eid, {}).get(month, {}).get("count", 0)

    def get_history(self, employee_id: int) -> dict:
        """Return full history for an employee."""
        return self._data.get(str(employee_id), {})
