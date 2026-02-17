"""
Background scheduler — runs the polling loop in a separate thread so the
Streamlit UI stays responsive.

The scheduler:
1. Iterates over all pending watched sessions.
2. For each account, ensures the session is still logged in.
3. Re-scrapes availability for each watched session.
4. If a session is open, hands it to the registrar.
5. Sleeps for the configured polling interval, then repeats.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from app.backend.crypto import load_accounts
from app.backend.login import LoginManager
from app.backend.monitor import Monitor, WatchedSession
from app.backend.registrar import register_for_session
from app.backend.scraper import scrape_schedule
from app.config import settings

log = logging.getLogger(__name__)


@dataclass
class LogEntry:
    """A single line in the activity log."""
    timestamp: str
    account: str
    message: str
    level: str = "info"  # info | warning | error | success


class Scheduler:
    """
    Manages the background automation loop.

    Attributes
    ----------
    monitor : Monitor
        Shared monitor that tracks watched sessions.
    poll_interval : int
        Seconds between polling cycles.
    running : bool
        Whether the loop is active.
    activity_log : list[LogEntry]
        Recent activity entries (displayed in the Streamlit dashboard).
    """

    def __init__(self, monitor: Monitor) -> None:
        self.monitor = monitor
        self.poll_interval: int = settings.DEFAULT_POLL_INTERVAL_SEC
        self.running: bool = False

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._login_mgr = LoginManager()
        self._lock = threading.Lock()

        # Bounded in-memory log (most recent first)
        self.activity_log: list[LogEntry] = []
        self._max_log = 500

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the polling loop in a background thread."""
        if self.running:
            return
        self._stop_event.clear()
        self._login_mgr.start()
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()
        self._log_activity("system", "Scheduler started.", level="info")

    def stop(self) -> None:
        """Signal the loop to stop and wait for it to finish."""
        if not self.running:
            return
        self._stop_event.set()
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=15)
        self._login_mgr.stop()
        self._log_activity("system", "Scheduler stopped.", level="info")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        log.info("Scheduler loop started (interval=%ds).", self.poll_interval)
        while not self._stop_event.is_set():
            try:
                self._poll_cycle()
            except Exception as exc:
                log.error("Scheduler cycle error: %s", exc)
                self._log_activity("system", f"Cycle error: {exc}", level="error")

            # Sleep in small increments so we can stop promptly
            for _ in range(self.poll_interval):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

        log.info("Scheduler loop exited.")

    def _poll_cycle(self) -> None:
        """One full polling cycle across all pending watched sessions."""
        pending = self.monitor.pending()
        if not pending:
            return

        # Group by account
        accounts_map: dict[str, list[WatchedSession]] = {}
        for ws in pending:
            accounts_map.setdefault(ws.account_email, []).append(ws)

        # Load credentials
        all_accounts = {a["email"].lower(): a for a in load_accounts()}

        for email, watched_list in accounts_map.items():
            acct = all_accounts.get(email.lower())
            if not acct:
                self._log_activity(email, "Account not found in credential store — skipping.", level="error")
                continue
            if not acct.get("enabled", True):
                continue

            # Ensure logged in
            if not self._login_mgr.ensure_logged_in(acct["email"], acct["password"]):
                self._log_activity(email, "Login failed — will retry next cycle.", level="error")
                continue

            page = self._login_mgr.page_for(email)

            for ws in watched_list:
                if self._stop_event.is_set():
                    return

                self._log_activity(email, f"Checking: {ws.session.name} on {ws.session.date} …")

                # Re-scrape just this date to get fresh availability
                fresh = scrape_schedule(
                    page,
                    acct["club_slug"],
                    target_date=ws.session.date,
                    session_types=[ws.session.name],
                )

                # Find the matching session in fresh data
                match = None
                for s in fresh:
                    if s.name == ws.session.name and s.time == ws.session.time:
                        match = s
                        break

                if match is None:
                    self.monitor.mark_checked(ws, "not found")
                    self._log_activity(email, f"Session not found on page — may have been removed.", level="warning")
                    continue

                self.monitor.mark_checked(ws, match.availability)

                if not match.is_open():
                    self._log_activity(email, f"Not open yet ({match.availability}). Will retry.")
                    continue

                # Attempt registration
                self._log_activity(email, f"Session OPEN — attempting registration …", level="info")
                result = register_for_session(page, match.url or ws.session.url, email)

                if result.success:
                    self.monitor.mark_registered(ws)
                    self._log_activity(email, f"Registered for {ws.session.name}!", level="success")
                elif result.waitlisted:
                    self.monitor.mark_waitlisted(ws)
                    self._log_activity(email, f"Waitlisted for {ws.session.name}.", level="warning")
                else:
                    self.monitor.mark_failed(ws, result.message)
                    self._log_activity(email, f"Registration failed: {result.message}", level="error")

    # ------------------------------------------------------------------
    # Activity log
    # ------------------------------------------------------------------

    def _log_activity(self, account: str, message: str, level: str = "info") -> None:
        entry = LogEntry(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            account=account,
            message=message,
            level=level,
        )
        with self._lock:
            self.activity_log.insert(0, entry)
            if len(self.activity_log) > self._max_log:
                self.activity_log = self.activity_log[: self._max_log]

        # Also write to file
        try:
            with open(settings.LOG_FILE, "a") as f:
                f.write(f"[{entry.timestamp}] [{entry.level.upper()}] [{account}] {message}\n")
        except Exception:
            pass

    def get_recent_log(self, limit: int = 50) -> list[LogEntry]:
        with self._lock:
            return list(self.activity_log[:limit])
