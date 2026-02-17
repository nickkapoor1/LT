"""
Notification system — sends alerts when registration events occur.

Supports:
- Email (SMTP) notifications
- Desktop notifications (cross-platform via subprocess)
- Log-only (always active)

Configuration is loaded from the encrypted notification settings file.
"""

from __future__ import annotations

import json
import logging
import smtplib
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from app.config.settings import DATA_DIR

log = logging.getLogger(__name__)

NOTIFY_SETTINGS_FILE = DATA_DIR / "notify_settings.json"


@dataclass
class NotifySettings:
    """Notification preferences."""
    # Email
    email_enabled: bool = False
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    notify_to_email: str = ""  # where to send alerts

    # Desktop
    desktop_enabled: bool = True

    # What to notify about
    on_registration: bool = True
    on_waitlist: bool = True
    on_failure: bool = True
    on_spots_open: bool = True


def load_notify_settings() -> NotifySettings:
    """Load notification settings from disk."""
    if not NOTIFY_SETTINGS_FILE.exists():
        return NotifySettings()
    try:
        data = json.loads(NOTIFY_SETTINGS_FILE.read_text())
        return NotifySettings(**{k: v for k, v in data.items() if hasattr(NotifySettings, k)})
    except Exception:
        return NotifySettings()


def save_notify_settings(settings: NotifySettings) -> None:
    """Persist notification settings."""
    data = {
        "email_enabled": settings.email_enabled,
        "smtp_server": settings.smtp_server,
        "smtp_port": settings.smtp_port,
        "smtp_user": settings.smtp_user,
        "smtp_password": settings.smtp_password,
        "notify_to_email": settings.notify_to_email,
        "desktop_enabled": settings.desktop_enabled,
        "on_registration": settings.on_registration,
        "on_waitlist": settings.on_waitlist,
        "on_failure": settings.on_failure,
        "on_spots_open": settings.on_spots_open,
    }
    NOTIFY_SETTINGS_FILE.write_text(json.dumps(data, indent=2))


class Notifier:
    """Sends notifications for registration events. All sends are non-blocking."""

    def __init__(self) -> None:
        self.settings = load_notify_settings()

    def reload(self) -> None:
        self.settings = load_notify_settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_registered(self, account: str, event_title: str, event_time: str) -> None:
        if not self.settings.on_registration:
            return
        subject = f"Registered: {event_title}"
        body = (
            f"Successfully registered for {event_title}!\n\n"
            f"Account: {account}\n"
            f"Time: {event_time}\n"
        )
        self._send(subject, body)

    def notify_waitlisted(self, account: str, event_title: str, event_time: str) -> None:
        if not self.settings.on_waitlist:
            return
        subject = f"Waitlisted: {event_title}"
        body = (
            f"Added to waitlist for {event_title}.\n\n"
            f"Account: {account}\n"
            f"Time: {event_time}\n"
        )
        self._send(subject, body)

    def notify_failed(self, account: str, event_title: str, reason: str) -> None:
        if not self.settings.on_failure:
            return
        subject = f"Registration Failed: {event_title}"
        body = (
            f"Registration failed for {event_title}.\n\n"
            f"Account: {account}\n"
            f"Reason: {reason}\n"
        )
        self._send(subject, body)

    def notify_spots_open(self, account: str, event_title: str, spots: int) -> None:
        if not self.settings.on_spots_open:
            return
        subject = f"Spots Open: {event_title}"
        body = (
            f"{spots} spot(s) available for {event_title}!\n\n"
            f"Account: {account}\n"
        )
        self._send(subject, body)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send(self, subject: str, body: str) -> None:
        """Send notifications on a background thread."""
        threading.Thread(
            target=self._do_send, args=(subject, body), daemon=True
        ).start()

    def _do_send(self, subject: str, body: str) -> None:
        if self.settings.desktop_enabled:
            self._desktop_notify(subject, body)
        if self.settings.email_enabled:
            self._email_notify(subject, body)

    def _desktop_notify(self, title: str, message: str) -> None:
        """Cross-platform desktop notification."""
        try:
            if sys.platform == "darwin":
                subprocess.run(
                    ["osascript", "-e",
                     f'display notification "{message[:200]}" with title "{title}"'],
                    timeout=5, capture_output=True,
                )
            elif sys.platform == "linux":
                subprocess.run(
                    ["notify-send", title, message[:200]],
                    timeout=5, capture_output=True,
                )
            elif sys.platform == "win32":
                # PowerShell toast notification
                ps_cmd = (
                    f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                    f"ContentType = WindowsRuntime] > $null; "
                    f"$xml = [Windows.UI.Notifications.ToastNotificationManager]"
                    f"::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                    f"$text = $xml.GetElementsByTagName('text'); "
                    f"$text[0].AppendChild($xml.CreateTextNode('{title}')) > $null; "
                    f"$text[1].AppendChild($xml.CreateTextNode('{message[:200]}')) > $null; "
                    f"$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
                    f"[Windows.UI.Notifications.ToastNotificationManager]"
                    f"::CreateToastNotifier('Pickleball Sniper').Show($toast)"
                )
                subprocess.run(
                    ["powershell", "-Command", ps_cmd],
                    timeout=10, capture_output=True,
                )
        except Exception as exc:
            log.debug("Desktop notification failed: %s", exc)

    def _email_notify(self, subject: str, body: str) -> None:
        """Send email via SMTP."""
        s = self.settings
        if not all([s.smtp_server, s.smtp_user, s.smtp_password, s.notify_to_email]):
            log.debug("Email notification skipped — incomplete SMTP config.")
            return
        try:
            msg = MIMEMultipart()
            msg["From"] = s.smtp_user
            msg["To"] = s.notify_to_email
            msg["Subject"] = f"[Pickleball Sniper] {subject}"
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(s.smtp_server, s.smtp_port, timeout=15) as server:
                server.starttls()
                server.login(s.smtp_user, s.smtp_password)
                server.send_message(msg)

            log.info("Email notification sent: %s", subject)
        except Exception as exc:
            log.warning("Email notification failed: %s", exc)
