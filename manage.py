"""
manage.py — CLI helpers for setup and maintenance.

Usage:
    python manage.py onboard               # Add one new employee in a single step
    python manage.py list-employees        # Print all Odoo employees with their IDs
    python manage.py audit-map             # Check every mapped PIN has a valid email
    python manage.py test-connection       # Verify Odoo credentials work
    python manage.py generate-map          # Generate a starter employee_map_setup.json
    python manage.py build-map             # Build employee_map.json from the setup file
"""

import json
import sys
from config import Config
from odoo_client import OdooClient


def cmd_test_connection():
    odoo = OdooClient(Config.ODOO_URL, Config.ODOO_DB, Config.ODOO_USER, Config.ODOO_PASSWORD, Config.ODOO_COMPANY_ID)
    odoo._connect()
    print(f"✓ Connected to {Config.ODOO_URL} as uid={odoo._uid}")


def cmd_list_employees():
    odoo = OdooClient(Config.ODOO_URL, Config.ODOO_DB, Config.ODOO_USER, Config.ODOO_PASSWORD, Config.ODOO_COMPANY_ID)
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
    odoo = OdooClient(Config.ODOO_URL, Config.ODOO_DB, Config.ODOO_USER, Config.ODOO_PASSWORD, Config.ODOO_COMPANY_ID)
    employees = odoo._call(
        "hr.employee", "search_read",
        [[["active", "=", True]]],
        {"fields": ["id", "name"], "order": "name asc", "limit": 0}
    )

    # Preserve any PINs already filled in (from employee_map.json) so that
    # re-running this command to pick up new hires doesn't wipe existing work.
    known_pin = {v: k for k, v in _load_map().items()}  # odoo_id → pin

    setup = {
        "_instructions": (
            "Fill in the device_pin for each employee. "
            "Find PINs on the MB360: Menu → User Mgmt → All Users. "
            "Then run: python manage.py build-map  "
            "(Tip: 'python manage.py onboard' does this in one step.)"
        ),
        "employees": [
            {"odoo_id": e["id"], "name": e["name"],
             "device_pin": known_pin.get(e["id"])}
            for e in employees
        ]
    }

    with open("employee_map_setup.json", "w") as f:
        json.dump(setup, f, indent=2)

    print(f"✓ Wrote employee_map_setup.json ({len(employees)} employees)")
    print("  Fill in 'device_pin' for each person, then run: python manage.py build-map")


def _load_map():
    """Return the current PIN→odoo_id map as a dict (empty if no file)."""
    try:
        with open(Config.EMPLOYEE_MAP_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    return {str(k): int(v) for k, v in data.items() if not str(k).startswith("_")}


def _save_map(mapping):
    """Write the PIN→odoo_id map to disk (sorted by PIN for readability)."""
    ordered = {k: mapping[k] for k in sorted(mapping, key=lambda x: int(x))}
    with open(Config.EMPLOYEE_MAP_FILE, "w") as f:
        json.dump(ordered, f, indent=2)


def cmd_onboard():
    """
    Onboard one new employee in a single step — no JSON editing, no restart.

        python manage.py onboard                     # interactive picker
        python manage.py onboard --pin 14 --id 33    # pin → odoo employee id
        python manage.py onboard --pin 14 --name Jane

    Validates the employee exists, is active, and has a work_email, then
    merges the new PIN into employee_map.json (existing entries are kept).
    The running server picks up the change automatically.
    """
    args = sys.argv[2:]

    def _flag(name):
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                return args[i + 1]
        return None

    pin       = _flag("--pin")
    want_id   = _flag("--id")
    want_name = _flag("--name")

    odoo = OdooClient(Config.ODOO_URL, Config.ODOO_DB, Config.ODOO_USER,
                      Config.ODOO_PASSWORD, Config.ODOO_COMPANY_ID)
    employees = odoo._call(
        "hr.employee", "search_read",
        [[["active", "=", True]]],
        {"fields": ["id", "name", "work_email"], "order": "name asc", "limit": 0},
    )
    by_id = {e["id"]: e for e in employees}

    mapping = _load_map()
    mapped_ids = {int(v) for v in mapping.values()}

    # ── 1. Pick the employee ──────────────────────────────────────────────────
    emp = None
    if want_id:
        emp = by_id.get(int(want_id))
        if not emp:
            print(f"✗ No active Odoo employee with id={want_id}.")
            sys.exit(1)
    elif want_name:
        matches = [e for e in employees if want_name.lower() in e["name"].lower()]
        if not matches:
            print(f"✗ No active employee matching {want_name!r}.")
            sys.exit(1)
        if len(matches) > 1:
            print(f"Multiple employees match {want_name!r}:")
            for e in matches:
                print(f"   id={e['id']:<4} {e['name']}")
            print("Re-run with --id <id> to disambiguate.")
            sys.exit(1)
        emp = matches[0]
    else:
        # Interactive: list employees that aren't mapped yet
        unmapped = [e for e in employees if e["id"] not in mapped_ids]
        if not unmapped:
            print("All active employees are already mapped. Nothing to onboard.")
            return
        print("\nEmployees not yet mapped to a device PIN:")
        print(f"{'#':>3}  {'ID':>5}  {'Name':<32}  {'Work Email'}")
        print("-" * 70)
        for idx, e in enumerate(unmapped, 1):
            print(f"{idx:>3}  {e['id']:>5}  {e['name'][:32]:<32}  {e.get('work_email') or '<missing>'}")
        choice = input("\nPick employee number (or blank to cancel): ").strip()
        if not choice:
            print("Cancelled.")
            return
        try:
            emp = unmapped[int(choice) - 1]
        except (ValueError, IndexError):
            print("✗ Invalid choice.")
            sys.exit(1)

    # ── 2. Get the PIN ────────────────────────────────────────────────────────
    if not pin:
        pin = input(f"Device PIN for {emp['name']}: ").strip()
    if not pin or not pin.isdigit():
        print(f"✗ PIN must be a number (got {pin!r}).")
        sys.exit(1)
    pin = str(int(pin))  # normalise (strip leading zeros)

    # ── 3. Validate before writing ────────────────────────────────────────────
    if pin in mapping and mapping[pin] != emp["id"]:
        other = by_id.get(mapping[pin], {}).get("name", f"id={mapping[pin]}")
        ans = input(f"⚠ PIN {pin} is already mapped to {other}. Reassign to "
                    f"{emp['name']}? [y/N]: ").strip().lower()
        if ans != "y":
            print("Cancelled.")
            return

    existing_pin = next((p for p, i in mapping.items() if i == emp["id"]), None)
    if existing_pin and existing_pin != pin:
        ans = input(f"⚠ {emp['name']} is already mapped to PIN {existing_pin}. "
                    f"Move to PIN {pin}? [y/N]: ").strip().lower()
        if ans != "y":
            print("Cancelled.")
            return
        del mapping[existing_pin]

    if not emp.get("work_email"):
        print(f"⚠ {emp['name']} has no work_email in Odoo — lateness emails "
              "will have nowhere to go until you add one\n"
              "  (Odoo → Employees → Work Information → Work Email).")

    # ── 4. Write & confirm ────────────────────────────────────────────────────
    mapping[pin] = emp["id"]
    _save_map(mapping)
    print(f"\n✓ Onboarded {emp['name']}: PIN {pin} → Odoo id {emp['id']}")
    print(f"  employee_map.json now has {len(mapping)} mappings.")
    print("  The running server will pick this up automatically (hot-reload).")


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


def cmd_audit_map():
    """
    For every PIN in employee_map.json, fetch the Odoo employee's current
    name and work_email. Flags missing emails and unknown employee IDs.
    Run this any time after onboarding to make sure lateness notifications
    will reach a real inbox.
    """
    from employee_map import EmployeeMap
    emap = EmployeeMap(Config.EMPLOYEE_MAP_FILE)
    if not emap._map:
        print(f"No mappings in {Config.EMPLOYEE_MAP_FILE}. Run generate-map / build-map first.")
        sys.exit(1)

    odoo = OdooClient(Config.ODOO_URL, Config.ODOO_DB, Config.ODOO_USER,
                      Config.ODOO_PASSWORD, Config.ODOO_COMPANY_ID)

    # Batch read in one XML-RPC call
    ids = list({int(v) for v in emap._map.values()})
    records = odoo._call(
        "hr.employee", "read",
        [ids],
        {"fields": ["id", "name", "work_email", "active"]},
    )
    by_id = {r["id"]: r for r in records}

    rows = []
    missing_email = 0
    missing_record = 0
    inactive = 0
    for pin, emp_id in sorted(emap._map.items(), key=lambda kv: int(kv[0])):
        rec = by_id.get(int(emp_id))
        if not rec:
            rows.append((pin, emp_id, "<NOT FOUND IN ODOO>", "—", False))
            missing_record += 1
            continue
        name  = rec.get("name") or "<no name>"
        email = rec.get("work_email") or ""
        active = bool(rec.get("active"))
        if not email:
            missing_email += 1
        if not active:
            inactive += 1
        rows.append((pin, emp_id, name, email or "<missing>", active))

    # Print table
    print(f"\n{'PIN':>4}  {'ID':>5}  {'A':<1}  {'Name':<32}  {'Work Email'}")
    print("-" * 90)
    for pin, emp_id, name, email, active in rows:
        flag = " " if active else "✗"
        print(f"{pin:>4}  {emp_id:>5}  {flag:<1}  {name[:32]:<32}  {email}")
    print("-" * 90)
    print(f"Total mapped: {len(rows)}")
    print(f"  Missing work_email:   {missing_email}")
    print(f"  Inactive in Odoo:     {inactive}")
    print(f"  Not found in Odoo:    {missing_record}")
    if missing_email or missing_record or inactive:
        print("\nFix in Odoo: Employees → [person] → Work Information → Work Email.")
        sys.exit(2)


COMMANDS = {
    "test-connection": cmd_test_connection,
    "list-employees": cmd_list_employees,
    "generate-map": cmd_generate_map,
    "build-map": cmd_build_map,
    "audit-map": cmd_audit_map,
    "onboard": cmd_onboard,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python manage.py <command>")
        print("Commands:", ", ".join(COMMANDS))
        sys.exit(1)

    COMMANDS[sys.argv[1]]()
