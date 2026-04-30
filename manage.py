"""
manage.py — CLI helpers for setup and maintenance.

Usage:
    python manage.py list-employees        # Print all Odoo employees with their IDs
    python manage.py test-connection       # Verify Odoo credentials work
    python manage.py reload-map            # Reload employee_map.json (if server running)
    python manage.py generate-map          # Generate a starter employee_map.json
"""

import json
import sys
from config import Config
from odoo_client import OdooClient


def cmd_test_connection():
    odoo = OdooClient(Config.ODOO_URL, Config.ODOO_DB, Config.ODOO_USER, Config.ODOO_PASSWORD)
    odoo._connect()
    print(f"✓ Connected to {Config.ODOO_URL} as uid={odoo._uid}")


def cmd_list_employees():
    odoo = OdooClient(Config.ODOO_URL, Config.ODOO_DB, Config.ODOO_USER, Config.ODOO_PASSWORD)
    employees = odoo._call(
        "hr.employee", "search_read",
        [[["active", "=", True]]],
        {"fields": ["id", "name", "barcode"], "order": "name asc", "limit": 0}
    )
    print(f"\n{'ID':>6}  {'Name':<40}  {'Barcode'}")
    print("-" * 60)
    for e in employees:
        print(f"{e['id']:>6}  {e['name']:<40}  {e.get('barcode') or '-'}")
    print(f"\nTotal: {len(employees)} employees")


def cmd_generate_map():
    """
    Generate a starter employee_map.json with all employees.
    The 'pin' value is set to null — fill these in from your MB360 device
    (Menu → User Mgmt → View enrolled users to see each person's PIN).

    Format written:
        { "<device_pin>": <odoo_employee_id>, ... }

    This command writes a helper file 'employee_map_setup.json' with names
    so you can identify who is who, then you manually create employee_map.json.
    """
    odoo = OdooClient(Config.ODOO_URL, Config.ODOO_DB, Config.ODOO_USER, Config.ODOO_PASSWORD)
    employees = odoo._call(
        "hr.employee", "search_read",
        [[["active", "=", True]]],
        {"fields": ["id", "name"], "order": "name asc", "limit": 0}
    )

    setup = {
        "_instructions": (
            "Fill in the device_pin for each employee. "
            "Find PINs on the MB360: Menu → User Mgmt → All Users. "
            "Then run: python manage.py build-map"
        ),
        "employees": [
            {"odoo_id": e["id"], "name": e["name"], "device_pin": None}
            for e in employees
        ]
    }

    with open("employee_map_setup.json", "w") as f:
        json.dump(setup, f, indent=2)

    print(f"✓ Wrote employee_map_setup.json ({len(employees)} employees)")
    print("  Fill in 'device_pin' for each person, then run: python manage.py build-map")


def cmd_build_map():
    """Convert filled-in employee_map_setup.json → employee_map.json"""
    try:
        with open("employee_map_setup.json") as f:
            setup = json.load(f)
    except FileNotFoundError:
        print("✗ employee_map_setup.json not found. Run: python manage.py generate-map")
        sys.exit(1)

    result = {}
    skipped = []
    for entry in setup.get("employees", []):
        pin = entry.get("device_pin")
        oid = entry.get("odoo_id")
        name = entry.get("name", "?")
        if pin is None:
            skipped.append(name)
            continue
        result[str(pin)] = oid

    with open(Config.EMPLOYEE_MAP_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"✓ Wrote {Config.EMPLOYEE_MAP_FILE} ({len(result)} mappings)")
    if skipped:
        print(f"  Skipped {len(skipped)} employees with no PIN: {', '.join(skipped)}")


COMMANDS = {
    "test-connection": cmd_test_connection,
    "list-employees": cmd_list_employees,
    "generate-map": cmd_generate_map,
    "build-map": cmd_build_map,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python manage.py <command>")
        print("Commands:", ", ".join(COMMANDS))
        sys.exit(1)

    COMMANDS[sys.argv[1]]()
