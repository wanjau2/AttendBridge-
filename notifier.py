"""
notifier.py — Sends lateness notification emails to employees and HR.
Uses Python's built-in smtplib — no extra dependencies.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from config import Config

log = logging.getLogger(__name__)


def _build_email(
    to_email: str,
    employee_name: str,
    punch_time: datetime,
    minutes_late: int,
    occurrence: int,
    month: str,
    action: str,
    is_formal: bool,
    cc_hr: bool = False,
) -> MIMEMultipart:
    """Build the lateness notification email."""

    month_label = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    date_label  = punch_time.strftime("%A, %d %B %Y")
    time_label  = punch_time.strftime("%I:%M %p")

    subject = (
        f"[{'FORMAL ACTION' if is_formal else 'Attendance Notice'}] "
        f"Late Arrival – {employee_name} – {date_label}"
    )

    # ── HTML body ──────────────────────────────────────────────────────────────
    border_color = "#B45309" if is_formal else "#1A6B6B"
    badge_color  = "#DC2626" if is_formal else "#2D9292"

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; background: #F9FAFB; margin: 0; padding: 20px;">
  <div style="max-width: 600px; margin: 0 auto; background: white; border-radius: 8px;
              border-left: 6px solid {border_color}; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">

    <!-- Header -->
    <div style="background: {border_color}; padding: 24px 32px; border-radius: 2px 2px 0 0;">
      <h1 style="color: white; margin: 0; font-size: 20px;">
        {'⚠️ Formal Disciplinary Action' if is_formal else '🕐 Attendance Notice'}
      </h1>
      <p style="color: rgba(255,255,255,0.85); margin: 4px 0 0 0; font-size: 14px;">
        Chambers Federation — Time &amp; Attendance System
      </p>
    </div>

    <!-- Body -->
    <div style="padding: 32px;">
      <p style="color: #374151; font-size: 15px;">Dear <strong>{employee_name}</strong>,</p>

      <p style="color: #374151; font-size: 15px;">
        This is an automated notification from the Chambers Federation Time &amp; Attendance System
        regarding your arrival time on <strong>{date_label}</strong>.
      </p>

      <!-- Details box -->
      <div style="background: #F3F4F6; border-radius: 6px; padding: 20px; margin: 24px 0;">
        <table style="width: 100%; border-collapse: collapse;">
          <tr>
            <td style="padding: 8px 0; color: #6B7280; font-size: 14px; width: 45%;">Date</td>
            <td style="padding: 8px 0; color: #111827; font-weight: bold; font-size: 14px;">{date_label}</td>
          </tr>
          <tr>
            <td style="padding: 8px 0; color: #6B7280; font-size: 14px;">Check-in Time</td>
            <td style="padding: 8px 0; color: #111827; font-weight: bold; font-size: 14px;">{time_label}</td>
          </tr>
          <tr>
            <td style="padding: 8px 0; color: #6B7280; font-size: 14px;">Expected Start</td>
            <td style="padding: 8px 0; color: #111827; font-weight: bold; font-size: 14px;">{Config.WORK_START_TIME} (grace: {Config.LATE_GRACE_MINUTES} min)</td>
          </tr>
          <tr>
            <td style="padding: 8px 0; color: #6B7280; font-size: 14px;">Minutes Late</td>
            <td style="padding: 8px 0; color: #DC2626; font-weight: bold; font-size: 14px;">{minutes_late} minutes</td>
          </tr>
          <tr>
            <td style="padding: 8px 0; color: #6B7280; font-size: 14px;">Occurrence This Month</td>
            <td style="padding: 8px 0; font-size: 14px;">
              <span style="background: {badge_color}; color: white; padding: 2px 10px;
                           border-radius: 12px; font-weight: bold;">
                #{occurrence} in {month_label}
              </span>
            </td>
          </tr>
        </table>
      </div>

      <!-- Action notice -->
      <div style="border: 2px solid {border_color}; border-radius: 6px; padding: 16px; margin: 24px 0;
                  background: {'#FFF7ED' if is_formal else '#F0F9F9'};">
        <p style="margin: 0; font-size: 14px; color: #374151;">
          <strong>Action Taken:</strong> {action}
        </p>
        {'<p style="margin: 8px 0 0 0; font-size: 13px; color: #B45309;"><strong>Note:</strong> This constitutes a formal disciplinary action. A copy has been sent to HR and logged on your employee record.</p>' if is_formal else ''}
      </div>

      <!-- Policy reminder -->
      <div style="background: #F9FAFB; border-radius: 6px; padding: 16px; margin: 24px 0;">
        <p style="margin: 0 0 8px 0; font-size: 13px; color: #6B7280; font-weight: bold;">
          MONTHLY DISCIPLINARY THRESHOLDS
        </p>
        <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
          {''.join([
            f'<tr style="background: {"#FEF3C7" if i+1 == occurrence else "transparent"};">'
            f'<td style="padding: 4px 8px; color: #374151;">Occurrence {i+1}{"+" if i==5 else ""}</td>'
            f'<td style="padding: 4px 8px; color: #374151;">{label}</td></tr>'
            for i, label in enumerate([
                "1st &amp; 2nd — System Record / Informal Caution",
                "3rd — Formal Verbal Counseling",
                "4th — First Written Warning",
                "5th — Final Written Warning",
                "6th or More — Show-Cause Letter and Disciplinary Hearing",
            ])
          ])}
        </table>
      </div>

      <p style="color: #374151; font-size: 14px;">
        If you believe this notification has been sent in error, please contact HR immediately.
      </p>

      <p style="color: #374151; font-size: 14px; margin-top: 32px;">
        Regards,<br>
        <strong>Chambers Federation</strong><br>
        <span style="color: #6B7280;">Time &amp; Attendance System (automated)</span>
      </p>
    </div>

    <!-- Footer -->
    <div style="background: #F3F4F6; padding: 16px 32px; border-radius: 0 0 8px 8px;
                text-align: center;">
      <p style="margin: 0; font-size: 12px; color: #9CA3AF;">
        This is an automated message. Do not reply to this email.<br>
        Chambers Federation — HR &amp; Compliance System
      </p>
    </div>
  </div>
</body>
</html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = Config.SMTP_FROM
    msg["To"]      = to_email
    if cc_hr and Config.HR_EMAIL and Config.HR_EMAIL != to_email:
        msg["Cc"] = Config.HR_EMAIL

    msg.attach(MIMEText(html, "html"))
    return msg


def send_lateness_email(
    to_email: str,
    employee_name: str,
    punch_time: datetime,
    minutes_late: int,
    occurrence: int,
    month: str,
    action: str,
    is_formal: bool,
) -> bool:
    """
    Send lateness notification email.
    Returns True on success, False on failure.
    CC HR automatically for formal actions.
    """
    if not Config.SMTP_HOST:
        log.warning("[EMAIL] SMTP not configured — skipping email notification")
        return False

    try:
        msg = _build_email(
            to_email, employee_name, punch_time,
            minutes_late, occurrence, month, action, is_formal,
            cc_hr=is_formal,
        )

        recipients = [to_email]
        if is_formal and Config.HR_EMAIL and Config.HR_EMAIL != to_email:
            recipients.append(Config.HR_EMAIL)

        # Port 465 = implicit TLS (SMTPS). Port 587/25 = plain or STARTTLS.
        if Config.SMTP_PORT == 465:
            smtp_cls = smtplib.SMTP_SSL
            use_starttls = False
        else:
            smtp_cls = smtplib.SMTP
            use_starttls = Config.SMTP_USE_TLS

        with smtp_cls(Config.SMTP_HOST, Config.SMTP_PORT, timeout=30) as server:
            server.ehlo()
            if use_starttls:
                server.starttls()
                server.ehlo()
            if Config.SMTP_USER:
                server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            server.sendmail(Config.SMTP_FROM, recipients, msg.as_string())

        log.info(f"[EMAIL] Sent lateness notice to {to_email} (formal={is_formal})")
        return True

    except Exception as e:
        log.error(f"[EMAIL] Failed to send to {to_email}: {e}")
        return False
