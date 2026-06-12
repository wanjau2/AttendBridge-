# AttendBridge

**ZKTeco  → Odoo Attendance Middleware**

AttendBridge is a lightweight Python/Flask server that bridges ZKTeco biometric attendance devices to [Odoo](https://www.odoo.com/) using the native iClock/ADMS push protocol. The device sends punches to AttendBridge; AttendBridge writes them to Odoo's `hr.attendance` module via XML-RPC — with duplicate detection, timezone conversion, stale-record handling, and an optional lateness-notification system built in.

---

## Features

- **Native iClock/ADMS protocol** — no SDK, no vendor software required
- **Odoo Online and self-hosted** — works with any Odoo 16/17/18 instance
- **ZMM501-NF28VF firmware quirk** handled automatically (Verified field encodes direction)
- **Timezone-aware** — device sends local time; middleware converts to UTC for Odoo
- **Duplicate punch guard** — ignores re-sends within 60 seconds
- **Stale record recovery** — auto-closes forgotten check-ins from previous day
- **Lateness tracking** — monthly occurrence counter with configurable disciplinary escalation
- **Email notifications** — HTML emails to employees; CC HR on formal actions
- **Odoo chatter notes** — every late arrival logged on the employee record
- **Odoo activities** — HR to-do tasks created for formal disciplinary actions (occurrence ≥ 3)
- **Web admin UI** — live dashboard, employee map editor, lateness history, log viewer
- **Zero extra dependencies** — only `flask` required; uses Python stdlib for everything else

---

## Supported Devices

Tested with:
- **ZKTeco MB360** (primary target)

Should work with any ZKTeco device running firmware that supports the iClock/ADMS push protocol, including most MB, F, and G series devices.

---

## Architecture

```
┌─────────────┐   iClock/ADMS push   ┌──────────────────┐   XML-RPC   ┌───────────────┐
│  ZKTeco     │ ──────────────────►  │  AttendBridge    │ ──────────► │  Odoo         │
│             │                      │  (Flask, port    │             │  hr.attendance│
│  device     │ ◄──────────────────  │   8008)          │             │               │
└─────────────┘   config response    └──────────────────┘             └───────────────┘
                                              │
                                              │ SMTP
                                              ▼
                                     Employee / HR email
```

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/your-org/attendbridge.git
cd attendbridge
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
nano .env   # fill in Odoo URL, DB, credentials, SMTP, timezone
```

### 3. Set up the employee map

The employee map links each device PIN (the number enrolled on the finger scanner) to an Odoo `hr.employee` ID.

```bash
# List all active employees in Odoo
python manage.py list-employees

# Generate a setup helper file
python manage.py generate-map
# → creates employee_map_setup.json

# Edit employee_map_setup.json and fill in device_pin for each person
# (find PINs on the device: Menu → User Mgmt → All Users)

# Build the final map
python manage.py build-map
# → creates employee_map.json
```

### 4. Test the Odoo connection

```bash
python manage.py test-connection
# ✓ Connected to https://yourdb.odoo.com as uid=2
```

### 5. Run

```bash
python app.py
```

### 6. Point the device at AttendBridge

On the MB360: **Comm** → **Cloud Server Settings**

| Setting | Value |
|---|---|
| Server Address | IP or hostname of the machine running AttendBridge |
| Server Port | `8008` (or your `LISTEN_PORT`) |
| HTTPS | Off (unless you've set up TLS termination) |

The device will connect within a few seconds. Check the log or `/health` endpoint to confirm.

---

## Configuration Reference

All configuration is via environment variables, loaded from `.env` on startup.

| Variable | Default | Description |
|---|---|---|
| `ORG_NAME` | `Your Organisation` | Shown in email footers |
| `ODOO_URL` | — | Full URL of your Odoo instance |
| `ODOO_DB` | — | Database name |
| `ODOO_USER` | — | Odoo login (email) |
| `ODOO_PASSWORD` | — | Password or API key |
| `LISTEN_PORT` | `8008` | Port the middleware listens on |
| `PUSH_INTERVAL_SECONDS` | `10` | How often the device pushes (seconds) |
| `DEVICE_TIMEZONE_OFFSET` | `0` | UTC offset of the device clock |
| `EMPLOYEE_MAP_FILE` | `./employee_map.json` | Path to PIN→employee_id map |
| `WORK_START_TIME` | `08:00` | Work start time (HH:MM, 24hr) |
| `LATE_GRACE_MINUTES` | `5` | Grace period before marking late |
| `WORK_END_TIME` | `17:00` | Work end time (for display) |
| `LATENESS_STORE_FILE` | `./lateness_store.json` | Lateness occurrence persistence |
| `WORK_DAYS` | `0,1,2,3,4` | Working days (0=Mon … 6=Sun) |
| `MIN_HOURS_BEFORE_CHECKOUT` | `4.0` | Min hours before 2nd punch = checkout |
| `CHECKOUT_AFTER_NOON` | `true` | Any punch after noon = checkout |
| `SMTP_HOST` | _(blank = disabled)_ | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USE_TLS` | `true` | Use STARTTLS |
| `SMTP_USER` | — | SMTP login |
| `SMTP_PASSWORD` | — | SMTP password |
| `SMTP_FROM` | — | From address |
| `HR_EMAIL` | — | CC'd on formal disciplinary emails |
| `MANAGER_EMAIL` | — | CC'd from the 4th monthly lateness occurrence onward |
| `MANAGER_CC_OCCURRENCE` | `4` | Occurrence at which the manager starts being CC'd |
| `ADMIN_TOKEN` | _(blank = no auth)_ | Token for admin UI/API |

---

## Lateness & Disciplinary Escalation

AttendBridge tracks late arrivals per employee on a rolling monthly basis and applies escalating disciplinary actions:

| Monthly Occurrence | Action | Formal? |
|---|---|---|
| 1st | System Record / Informal Caution | No |
| 2nd | System Record / Informal Caution | No |
| 3rd | Formal Verbal Counseling | **Yes** |
| 4th | First Written Warning | **Yes** |
| 5th | Final Written Warning | **Yes** |
| 6th+ | Show-Cause Letter and Disciplinary Hearing | **Yes** |

"Formal" actions trigger an Odoo activity assigned to HR and CC HR on the email notification. Occurrence counts reset each calendar month.

---

## Admin UI

Visit `http://your-server:8008/admin` in a browser.

The admin UI provides:
- **Status dashboard** — device online/offline, Odoo connection, employees mapped
- **Employee map** — view current PIN→ID mappings, reload from disk
- **Lateness history** — per-employee monthly occurrence data
- **Live log viewer** — last 200 lines of the middleware log

If `ADMIN_TOKEN` is set, pass it as the `X-Admin-Token` header or `?token=` query parameter on API calls, or log in via the UI prompt.

---

## Deployment

### Systemd (Linux)

```ini
# /etc/systemd/system/attendbridge.service
[Unit]
Description=AttendBridge Attendance Middleware
After=network.target

[Service]
Type=simple
User=attendbridge
WorkingDirectory=/opt/attendbridge
ExecStart=/opt/attendbridge/venv/bin/python app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now attendbridge
```

### Synology NAS (Task Scheduler)

Use the Synology Task Scheduler to run `python app.py` at boot with the working directory set to your package folder. Use an absolute path for `EMPLOYEE_MAP_FILE` and `LATENESS_STORE_FILE` in `.env`.

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8008
CMD ["python", "app.py"]
```

```bash
docker build -t attendbridge .
docker run -d -p 8008:8008 --env-file .env -v $(pwd)/data:/app attendbridge
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/iclock/cdata` | Device init (ADMS protocol) |
| `POST` | `/iclock/cdata` | Attendance push (ADMS protocol) |
| `GET` | `/iclock/getrequest` | Device heartbeat |
| `POST` | `/iclock/devicecmd` | Device command ACK |
| `GET` | `/health` | Health check (JSON) |
| `GET` | `/admin` | Admin UI |
| `GET` | `/admin/api/status` | Status JSON |
| `GET` | `/admin/api/employees` | Employee map |
| `POST` | `/admin/api/employees/reload` | Reload map from disk |
| `GET` | `/admin/api/lateness` | Lateness store |
| `GET` | `/admin/api/logs?n=200` | Last N log lines |

---

## Firmware Notes

### ZMM501-NF28VF (Ver 1.0.8)

This firmware has a non-standard ATTLOG field mapping:

- `field[2]` (Verified) encodes punch **direction**: `0` = Check-In, `1` = Check-Out
- `field[3]` (Status) is always `1` and should be **ignored**

AttendBridge handles this automatically. Standard ZKTeco firmware (Status field for direction) is not currently supported but is a straightforward change — PRs welcome.

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss the approach.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push and open a pull request

---

## License

[Apache License 2.0](LICENSE)
