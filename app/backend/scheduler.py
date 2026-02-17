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

        # Snipe timers — one thread per event that fires at exact open time
        self._snipe_threads: dict[str, threading.Thread] = {}
        self._snipe_fired: set[str] = set()  # event keys already sniped

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
        # Clean up snipe threads
        for t in self._snipe_threads.values():
            if t.is_alive():
                t.join(timeout=3)
        self._snipe_threads.clear()
        self._snipe_fired.clear()
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
        min_seconds_away = float("inf")

        for ws in pending:
            # Use the stored precise open time if available
            if ws.registration_opens_at_dt:
                seconds_away = (ws.registration_opens_at_dt - now).total_seconds()
                if seconds_away < min_seconds_away:
                    min_seconds_away = seconds_away
            elif ws.event.too_soon_minutes > 0:
                try:
                    event_start = datetime.fromisoformat(ws.event.start)
                    opens_at = event_start - timedelta(minutes=ws.event.too_soon_minutes)
                    seconds_away = (opens_at - now).total_seconds()
                    if seconds_away < min_seconds_away:
                        min_seconds_away = seconds_away
                except Exception:
                    pass

        min_minutes_away = min_seconds_away / 60

        # Scale interval based on proximity
        if min_seconds_away <= 10:
            # Within 10 seconds: maximum speed (snipe threads handle the exact moment)
            return self.snipe_interval
        elif min_minutes_away <= 1:
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

        # Compute and store precise registration open datetime
        if reg_info.too_soon_minutes and not ws.registration_opens_at_dt:
            try:
                event_start = datetime.fromisoformat(ws.event.start)
                opens_at_dt = event_start - timedelta(minutes=reg_info.too_soon_minutes)
                ws.registration_opens_at_dt = opens_at_dt
                ws.registration_opens_at_display = opens_at_dt.strftime("%a %b %d, %I:%M %p")
                self._log(
                    email,
                    f"{ws.event.title}: Registration opens {ws.registration_opens_at_display} "
                    f"({reg_info.too_soon_minutes} min before start)",
                    "info",
                )
            except Exception as exc:
                log.debug("Failed to parse open time for %s: %s", ws.event.title, exc)

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

        # If registration is disabled (too soon), schedule a precise snipe
        if reg_info.register_disabled:
            if ws.registration_opens_at_dt:
                now = datetime.now()
                seconds_until = (ws.registration_opens_at_dt - now).total_seconds()
                if seconds_until > 0:
                    self._log(
                        email,
                        f"{ws.event.title}: Opens in {self._format_countdown(seconds_until)} "
                        f"({ws.registration_opens_at_display})",
                    )
                    # Launch a precision snipe thread if not already running
                    self._schedule_snipe(ws, acct, seconds_until)
                else:
                    # Open time has passed but API still says disabled — retry immediately
                    self._log(email, f"{ws.event.title}: Open time passed, retrying …")
            elif reg_info.registration_opens_at:
                self._log(email, f"{ws.event.title}: {reg_info.registration_opens_at}")
            else:
                self._log(email, f"{ws.event.title}: Registration not yet open.")
            return

        # Attempt registration
        self._log(email, f"Attempting registration for {ws.event.title} …")
        result = self._api.register(session, ws.event.event_id, target_ids)

        if result.success:
            # Verify the outcome — API might say "success" but actually waitlisted
            verification = self._api.verify_registration(session, ws.event.event_id, target_ids)
            if verification == "waitlisted":
                self.monitor.mark_waitlisted(ws)
                self._log(email, f"API said success but verification shows WAITLISTED for {ws.event.title}.", "warning")
                self._notifier.notify_waitlisted(email, ws.event.title, ws.event.display_time())
            else:
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
    # Precision snipe — fires registration at exact open moment
    # ------------------------------------------------------------------

    def _schedule_snipe(self, ws: WatchedEvent, acct: dict, seconds_until: float) -> None:
        """
        Launch a background thread that sleeps until the exact registration
        open time, then fires rapid registration attempts.
        """
        snipe_key = ws.key
        if snipe_key in self._snipe_fired or snipe_key in self._snipe_threads:
            return  # already scheduled or already fired

        # Only schedule if open time is within 2 hours (avoid sleeping forever)
        if seconds_until > 7200:
            return

        self._log(
            ws.account_email,
            f"SNIPE SCHEDULED: {ws.event.title} — firing in {self._format_countdown(seconds_until)}",
            "info",
        )

        thread = threading.Thread(
            target=self._snipe_worker,
            args=(ws, acct, seconds_until),
            daemon=True,
            name=f"snipe-{ws.event.event_id[:8]}",
        )
        self._snipe_threads[snipe_key] = thread
        thread.start()

    def _snipe_worker(self, ws: WatchedEvent, acct: dict, seconds_until: float) -> None:
        """
        Worker thread: sleep until just BEFORE open time, then fire rapid
        registration attempts.  We start 1.5 seconds early to account for
        clock skew between our machine and Lifetime's servers.
        """
        email = ws.account_email
        snipe_key = ws.key

        # How far ahead of the published open time to start firing (seconds)
        PRE_FIRE_SEC = 1.5
        MAX_ATTEMPTS = 10
        ATTEMPT_DELAY = 0.2  # 200ms between rapid attempts

        try:
            # Re-authenticate early so we have a fresh token ready to go
            self._log(email, f"Pre-authenticating for snipe on {ws.event.title} …")
            session = self._api.ensure_authenticated(acct["email"], acct["password"])
            if not session.authenticated:
                self._log(email, f"Snipe pre-auth failed for {ws.event.title}", "error")
                return

            # Sleep until PRE_FIRE_SEC + 3s before open time (coarse sleep)
            coarse_wait = max(0, seconds_until - PRE_FIRE_SEC - 3)
            if coarse_wait > 0:
                for _ in range(int(coarse_wait)):
                    if self._stop_event.is_set() or ws.registration_status != "pending":
                        return
                    time.sleep(1)

            # Refresh auth right before we fire (token could be 2+ hours old)
            session = self._api.ensure_authenticated(acct["email"], acct["password"])
            if not session.authenticated:
                self._log(email, f"Snipe auth refresh failed for {ws.event.title}", "error")
                return

            # Precision wait: busy-wait until PRE_FIRE_SEC before open time
            if ws.registration_opens_at_dt:
                fire_at = ws.registration_opens_at_dt - timedelta(seconds=PRE_FIRE_SEC)
                while datetime.now() < fire_at:
                    if self._stop_event.is_set() or ws.registration_status != "pending":
                        return
                    time.sleep(0.05)  # 50ms precision

            # GO! Start firing BEFORE the exact open time
            self._log(email, f"SNIPE FIRING for {ws.event.title}! ({MAX_ATTEMPTS} rapid attempts)", "info")
            self._snipe_fired.add(snipe_key)

            for attempt in range(1, MAX_ATTEMPTS + 1):
                if self._stop_event.is_set() or ws.registration_status != "pending":
                    return

                self._log(
                    email,
                    f"Snipe attempt {attempt}/{MAX_ATTEMPTS} for {ws.event.title} …",
                )
                result = self._api.register(session, ws.event.event_id, ws.member_ids)

                if result.success:
                    # Verify the outcome to catch silent waitlist placement
                    verification = self._api.verify_registration(
                        session, ws.event.event_id, ws.member_ids
                    )
                    if verification == "waitlisted":
                        self.monitor.mark_waitlisted(ws)
                        self._log(email, f"Snipe: API said success but verification shows WAITLISTED for {ws.event.title}.", "warning")
                        self._notifier.notify_waitlisted(email, ws.event.title, ws.event.display_time())
                    else:
                        self.monitor.mark_registered(ws)
                        self._log(email, f"SNIPE SUCCESS! Registered for {ws.event.title}!", "success")
                        self._notifier.notify_registered(email, ws.event.title, ws.event.display_time())
                    return
                elif result.waitlisted:
                    self.monitor.mark_waitlisted(ws)
                    self._log(email, f"Snipe: Waitlisted for {ws.event.title}.", "warning")
                    self._notifier.notify_waitlisted(email, ws.event.title, ws.event.display_time())
                    return
                else:
                    # "registration will be open" means we're still too early — keep trying
                    if "registration will be open" in result.message.lower() or "too soon" in result.message.lower():
                        self._log(email, f"Snipe attempt {attempt}: Not open yet, retrying …")
                    else:
                        self._log(
                            email,
                            f"Snipe attempt {attempt} failed: {result.message}",
                            "warning",
                        )
                    time.sleep(ATTEMPT_DELAY)

            self._log(email, f"Snipe exhausted {MAX_ATTEMPTS} attempts for {ws.event.title}. Normal polling will continue.", "warning")

        except Exception as exc:
            log.error("[%s] Snipe worker error: %s", email, exc)
            self._log(email, f"Snipe error: {exc}", "error")
        finally:
            self._snipe_threads.pop(snipe_key, None)

    @staticmethod
    def _format_countdown(seconds: float) -> str:
        """Format seconds into a readable countdown string."""
        if seconds < 0:
            return "NOW"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"

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
