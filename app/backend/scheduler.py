"""
Background scheduler — runs the polling loop in a separate thread.

For each pending watched event:
1. Checks registration availability via the API.
2. If spots are open (or waitlist available), fires the registration call.
3. Logs results to the activity feed.
4. Sends notifications on registration/waitlist/failure.

Improvements over baseline:
- Adaptive polling: speeds up automatically near registration open time.
- Concurrent account processing via thread pool.
- Exponential backoff on transient errors (avoids hammering the API).
- Notification integration (email + desktop).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.backend.api_client import LifetimeAPI
from app.backend.crypto import load_accounts
from app.backend.monitor import Monitor, WatchedEvent
from app.backend.notifier import Notifier
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

        # Adaptive polling
        self.adaptive_polling: bool = True
        self.snipe_interval: int = 1  # poll every 1s when near open time
        self.normal_interval: int = settings.DEFAULT_POLL_INTERVAL_SEC

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._api = LifetimeAPI()
        self._notifier = Notifier()
        self._lock = threading.Lock()

        self.activity_log: list[LogEntry] = []
        self._max_log = 500

        # Track consecutive errors per account for backoff
        self._error_counts: dict[str, int] = {}
        self._max_workers = 4  # max concurrent account threads

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._error_counts.clear()
        self._notifier.reload()
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()
        self._log("system", "Engine started (polling every {}s, adaptive={}).".format(
            self.poll_interval, self.adaptive_polling))

    def stop(self) -> None:
        if not self.running:
            return
        self._stop_event.set()
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        self._log("system", "Engine stopped.")

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

            # Adaptive interval: check if any watched event opens soon
            interval = self._compute_interval()

            # Sleep in 1-second ticks so we can stop promptly
            for _ in range(interval):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def _compute_interval(self) -> int:
        """Dynamically adjust polling interval based on how close we are to a registration window."""
        if not self.adaptive_polling:
            return self.poll_interval

        pending = self.monitor.pending()
        if not pending:
            return self.poll_interval

        now = datetime.now()
        min_minutes_away = float("inf")

        for ws in pending:
            if ws.event.too_soon_minutes > 0:
                try:
                    event_start = datetime.fromisoformat(ws.event.start)
                    opens_at = event_start - timedelta(minutes=ws.event.too_soon_minutes)
                    minutes_away = (opens_at - now).total_seconds() / 60
                    if minutes_away < min_minutes_away:
                        min_minutes_away = minutes_away
                except Exception:
                    pass

        # Scale interval based on proximity
        if min_minutes_away <= 1:
            # Within 1 minute: maximum speed
            return self.snipe_interval
        elif min_minutes_away <= 5:
            # Within 5 minutes: fast polling
            return max(self.snipe_interval, 2)
        elif min_minutes_away <= settings.SNIPE_LEAD_TIME_SEC / 60:
            # Within lead time: moderately fast
            return min(5, self.poll_interval)
        else:
            return self.poll_interval

    def _poll_cycle(self) -> None:
        pending = self.monitor.pending()
        if not pending:
            return

        # Group by account
        by_account: dict[str, list[WatchedEvent]] = {}
        for ws in pending:
            by_account.setdefault(ws.account_email, []).append(ws)

        all_accounts = {a["email"].lower(): a for a in load_accounts()}

        # Process accounts concurrently for speed
        num_accounts = len(by_account)
        if num_accounts <= 1:
            # Single account — no need for thread pool overhead
            for email, watched_list in by_account.items():
                if self._stop_event.is_set():
                    return
                self._process_account(email, watched_list, all_accounts)
        else:
            workers = min(num_accounts, self._max_workers)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._process_account, email, watched_list, all_accounts): email
                    for email, watched_list in by_account.items()
                }
                for future in as_completed(futures):
                    if self._stop_event.is_set():
                        return
                    try:
                        future.result()
                    except Exception as exc:
                        email = futures[future]
                        log.error("[%s] Account processing error: %s", email, exc)
                        self._log(email, f"Processing error: {exc}", "error")

    def _process_account(
        self, email: str, watched_list: list[WatchedEvent], all_accounts: dict
    ) -> None:
        acct = all_accounts.get(email.lower())
        if not acct:
            self._log(email, "Account not found — skipping.", "error")
            return

        # Exponential backoff on consecutive errors
        error_count = self._error_counts.get(email, 0)
        if error_count >= 3:
            backoff_sec = min(2 ** error_count, 60)
            self._log(email, f"Backing off {backoff_sec}s after {error_count} errors.", "warning")
            time.sleep(backoff_sec)

        session = self._api.ensure_authenticated(acct["email"], acct["password"])
        if not session.authenticated:
            self._error_counts[email] = error_count + 1
            self._log(email, "Login failed — will retry next cycle.", "error")
            return

        # Clear error count on successful auth
        self._error_counts[email] = 0

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

        # Update snipe timing info from API response
        if reg_info.too_soon_minutes and not ws.event.too_soon_minutes:
            ws.event.too_soon_minutes = reg_info.too_soon_minutes
        if reg_info.registration_opens_at and not ws.event.registration_opens_at:
            ws.event.registration_opens_at = reg_info.registration_opens_at

        # Check if member is already registered
        registered_ids = {m.get("id") for m in reg_info.registered_members}
        target_ids = ws.member_ids
        already_registered = all(mid in registered_ids for mid in target_ids)
        if already_registered:
            self.monitor.mark_registered(ws)
            self._log(email, f"Already registered for {ws.event.title}!", "success")
            self._notifier.notify_registered(email, ws.event.title, ws.event.display_time())
            return

        # Notify if spots just opened
        if reg_info.has_spots and reg_info.remaining_spots and reg_info.remaining_spots > 0:
            self._notifier.notify_spots_open(
                email, ws.event.title, reg_info.remaining_spots
            )

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
            self._notifier.notify_registered(email, ws.event.title, ws.event.display_time())
        elif result.waitlisted:
            self.monitor.mark_waitlisted(ws)
            self._log(email, f"Waitlisted for {ws.event.title}.", "warning")
            self._notifier.notify_waitlisted(email, ws.event.title, ws.event.display_time())
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
