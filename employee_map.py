"""
employee_map.py — Maps ZKTeco device PINs to Odoo employee IDs.
The mapping is stored in a plain JSON file (employee_map.json) so you can
update it without touching any code.
"""

import json
import logging
import os

log = logging.getLogger(__name__)


class EmployeeMap:

    def __init__(self, filepath: str):
        self.filepath = filepath
        self._map = {}
        self._mtime = None
        self.load()

    def load(self):
        """Load (or reload) the mapping from disk."""
        if not os.path.exists(self.filepath):
            log.warning(
                f"[MAP] {self.filepath} not found. "
                "Run 'python manage.py list-employees' to generate it, "
                "then fill in the PIN column."
            )
            self._map = {}
            self._mtime = None
            return

        with open(self.filepath, "r") as f:
            data = json.load(f)

        # Normalise keys to strings; skip comment/metadata keys
        self._map = {
            str(k): int(v)
            for k, v in data.items()
            if not str(k).startswith("_")
        }
        self._mtime = os.path.getmtime(self.filepath)
        log.info(f"[MAP] Loaded {len(self._map)} employee mapping(s) from {self.filepath}")

    def _maybe_reload(self):
        """Reload automatically if the file changed on disk since last load.

        Lets `manage.py onboard` add a new employee while the server is
        running — no restart needed. Punches are infrequent, so the extra
        stat() per lookup is negligible.
        """
        try:
            current = os.path.getmtime(self.filepath)
        except OSError:
            return
        if current != self._mtime:
            log.info("[MAP] Map file changed on disk — hot-reloading.")
            self.load()

    def get(self, pin):
        """Return the Odoo employee_id for a given device PIN, or None."""
        self._maybe_reload()
        return self._map.get(str(pin))

    def reload(self):
        """Hot-reload the mapping without restarting the server."""
        self.load()
