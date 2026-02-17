"""
Background scheduler — runs the polling loop in a separate thread.

For each pending watched event:
1. Checks registration availability via the API.
2. If spots are open (or waitlist available), fires the registration call.
3. Logs results to the activity feed.

Designed for "sniping" — when registration opens at a precise time, the
loop polls rapidly and registers the instant availability appears.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from app.backend.api_client import LifetimeAPI
from app.backend.crypto import load_accounts
from app.backend.monitor import Monitor, WatchedEvent
from app.config import settings

log = logging.getLogger(__name__)


@dataclass
class LogEntry:
    timestamp: str
    account: str
    message: str
    level: str = "info"


class Scheduler:
    def __init__(self, monitor: Monitor) -> None:
        self.monitor = monitor
        self.poll_interval: int = settings.DEFAULT_POLL_INTERVAL_SEC
        self.running: bool = False

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._api = LifetimeAPI()
        self._lock = threading.Lock()

        self.activity_log: list[LogEntry] = []
        self._max_log = 500

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()
        self._log("system", "Scheduler started (polling every {}s).".format(self.poll_interval))

    def stop(self) -> None:
        if not self.running:
            return
        self._stop_event.set()
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        self._log("system", "Scheduler stopped.")

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_cycle()
            except Exception as exc:
                log.error("Scheduler cycle error: %s", exc)
                self._log("system", f"Cycle error: {exc}", "error")

            # Sleep in 1-second ticks so we can stop promptly
            for _ in range(self.poll_interval):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def _poll_cycle(self) -> None:
        pending = self.monitor.pending()
        if not pending:
            return

        # Group by account
        by_account: dict[str, list[WatchedEvent]] = {}
        for ws in pending:
            by_account.setdefault(ws.account_email, []).append(ws)

        all_accounts = {a["email"].lower(): a for a in load_accounts()}

        for email, watched_list in by_account.items():
            acct = all_accounts.get(email.lower())
            if not acct:
                self._log(email, "Account not found — skipping.", "error")
                continue

            session = self._api.ensure_authenticated(acct["email"], acct["password"])
            if not session.authenticated:
                self._log(email, "Login failed — will retry next cycle.", "error")
                continue

            for ws in watched_list:
                if self._stop_event.is_set():
                    return
                self._process_event(session, ws, acct)

    def _process_event(self, session, ws: WatchedEvent, acct: dict) -> None:
        email = ws.account_email

        # Check registration status for this event
        reg_info = self._api.get_event_registration(session, ws.event.event_id)
        if reg_info is None:
            self.monitor.mark_checked(ws, "API error")
            self._log(email, f"Failed to check {ws.event.title}", "error")
            return

        # Build a status string
        if reg_info.has_spots:
            status = f"{reg_info.remaining_spots} spots open"
        elif reg_info.has_waitlist:
            status = f"Full (waitlist: {reg_info.total_waitlisted})"
        else:
            status = "Closed"

        self.monitor.mark_checked(ws, status)

        # Check if member is already registered
        registered_ids = {m.get("id") for m in reg_info.registered_members}
        target_ids = ws.member_ids
        already_registered = all(mid in registered_ids for mid in target_ids)
        if already_registered:
            self.monitor.mark_registered(ws)
            self._log(email, f"Already registered for {ws.event.title}!", "success")
            return

        # If registration is disabled (too soon), just log and wait
        if reg_info.register_disabled:
            if reg_info.registration_opens_at:
                self._log(email, f"{ws.event.title}: {reg_info.registration_opens_at}")
            else:
                self._log(email, f"{ws.event.title}: Registration not yet open.")
            return

        # Attempt registration
        self._log(email, f"Attempting registration for {ws.event.title} …")
        result = self._api.register(session, ws.event.event_id, target_ids)

        if result.success:
            self.monitor.mark_registered(ws)
            self._log(email, f"REGISTERED for {ws.event.title}!", "success")
        elif result.waitlisted:
            self.monitor.mark_waitlisted(ws)
            self._log(email, f"Waitlisted for {ws.event.title}.", "warning")
        else:
            # Don't mark as permanently failed — keep retrying
            self._log(email, f"Registration attempt failed: {result.message}", "warning")

    # ------------------------------------------------------------------
    # Activity log
    # ------------------------------------------------------------------

    def _log(self, account: str, message: str, level: str = "info") -> None:
        entry = LogEntry(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            account=account,
            message=message,
            level=level,
        )
        with self._lock:
            self.activity_log.insert(0, entry)
            if len(self.activity_log) > self._max_log:
                self.activity_log = self.activity_log[:self._max_log]
        try:
            with open(settings.LOG_FILE, "a") as f:
                f.write(f"[{entry.timestamp}] [{level.upper()}] [{account}] {message}\n")
        except Exception:
            pass

    def get_recent_log(self, limit: int = 50) -> list[LogEntry]:
        with self._lock:
            return list(self.activity_log[:limit])
