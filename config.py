"""
config.py — Loads configuration from .env file in the same directory.
Edit .env with your actual values. Do not hardcode credentials here.
"""

import os

# ── Load .env file manually (no extra dependencies needed) ────────────────────
_ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")

if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE) as f:
        for line in f:
            line = line.strip()
            # Skip blank lines and comments
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                # Only set if not already in environment
                os.environ.setdefault(key.strip(), value.strip())


class Config:

    # ── Odoo connection ────────────────────────────────────────────────────────
    ODOO_URL      = os.getenv("ODOO_URL",      "http://192.168.0.43:8069")
    ODOO_DB       = os.getenv("ODOO_DB",       "postgres")
    ODOO_USER     = os.getenv("ODOO_USER",     "admin")
    ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "")

    # ── Middleware server ──────────────────────────────────────────────────────
    LISTEN_PORT            = int(os.getenv("LISTEN_PORT",            "8008"))
    PUSH_INTERVAL_SECONDS  = int(os.getenv("PUSH_INTERVAL_SECONDS",  "10"))

    # ── Timezone ───────────────────────────────────────────────────────────────
    DEVICE_TIMEZONE_OFFSET = int(os.getenv("DEVICE_TIMEZONE_OFFSET", "3"))

    # ── Employee map ───────────────────────────────────────────────────────────
    EMPLOYEE_MAP_FILE = os.getenv(
        "EMPLOYEE_MAP_FILE",
        os.path.join(os.path.dirname(__file__), "employee_map.json")
    )

    # ── Lateness tracking ──────────────────────────────────────────────────────
    # Work start time in HH:MM (24hr), e.g. "08:00"
    WORK_START_TIME = os.getenv("WORK_START_TIME", "08:00")

    # Minutes after WORK_START_TIME before a punch is considered late
    LATE_GRACE_MINUTES = int(os.getenv("LATE_GRACE_MINUTES", "5"))

    # File to persist monthly occurrence counts
    LATENESS_STORE_FILE = os.getenv(
        "LATENESS_STORE_FILE",
        os.path.join(os.path.dirname(__file__), "lateness_store.json")
    )

    # Days of the week that count as working days (0=Monday ... 6=Sunday)
    # Default: Monday-Friday
    WORK_DAYS = [int(d) for d in os.getenv("WORK_DAYS", "0,1,2,3,4").split(",")]

    # ── Email / SMTP ───────────────────────────────────────────────────────────
    # Leave SMTP_HOST blank to disable email notifications
    SMTP_HOST     = os.getenv("SMTP_HOST",     "")
    SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USE_TLS  = os.getenv("SMTP_USE_TLS",  "true").lower() == "true"
    SMTP_USER     = os.getenv("SMTP_USER",     "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM     = os.getenv("SMTP_FROM",     "attendance@chambersfederation.com")

    # HR email — copied on all formal disciplinary notifications
    HR_EMAIL = os.getenv("HR_EMAIL", "")

    # Work end time in HH:MM (24hr)
    WORK_END_TIME = os.getenv("WORK_END_TIME", "17:00")

    # Minimum hours worked before a second punch counts as check-out.
    # Under this threshold → ignored as accidental double punch.
    # e.g. 4.0 → second punch within 4hrs ignored; after 4hrs = check-out
    MIN_HOURS_BEFORE_CHECKOUT = float(os.getenv("MIN_HOURS_BEFORE_CHECKOUT", "4.0"))

    # If True, a second punch any time after 12:00 (noon) is treated as check-out
    # regardless of MIN_HOURS_BEFORE_CHECKOUT
    CHECKOUT_AFTER_NOON = os.getenv("CHECKOUT_AFTER_NOON", "true").lower() == "true"
