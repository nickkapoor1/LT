"""
Session monitor — tracks which sessions each account is watching
and detects when availability changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from app.backend.scraper import Session

log = logging.getLogger(__name__)


@dataclass
class WatchedSession:
    """A session that an account is monitoring for availability."""
    session: Session
    account_email: str
    auto_register: bool = True
    # Tracking fields
    last_checked: str = ""
    last_status: str = ""
    registration_status: str = "pending"  # pending | registered | waitlisted | failed

    @property
    def key(self) -> str:
        return f"{self.account_email}|{self.session.name}|{self.session.date}|{self.session.time}"


class Monitor:
    """
    Manages the set of watched sessions and determines which ones need
    registration attempts on each polling cycle.
    """

    def __init__(self) -> None:
        # key → WatchedSession
        self._watched: dict[str, WatchedSession] = {}

    # ------------------------------------------------------------------
    # Add / remove watches
    # ------------------------------------------------------------------

    def watch(self, session: Session, account_email: str, auto_register: bool = True) -> WatchedSession:
        """Add a session to the watch list for an account."""
        ws = WatchedSession(
            session=session,
            account_email=account_email,
            auto_register=auto_register,
        )
        self._watched[ws.key] = ws
        log.info("Watching: %s for %s", session.name, account_email)
        return ws

    def unwatch(self, session: Session, account_email: str) -> None:
        key = f"{account_email}|{session.name}|{session.date}|{session.time}"
        self._watched.pop(key, None)
        log.info("Unwatched: %s for %s", session.name, account_email)

    def unwatch_all(self, account_email: str | None = None) -> None:
        """Remove all watches, optionally filtered to one account."""
        if account_email:
            self._watched = {k: v for k, v in self._watched.items() if v.account_email != account_email}
        else:
            self._watched.clear()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def all_watched(self) -> list[WatchedSession]:
        return list(self._watched.values())

    def watched_for(self, account_email: str) -> list[WatchedSession]:
        return [ws for ws in self._watched.values() if ws.account_email == account_email]

    def pending(self) -> list[WatchedSession]:
        """Return watched sessions that still need registration."""
        return [
            ws for ws in self._watched.values()
            if ws.registration_status == "pending" and ws.auto_register
        ]

    # ------------------------------------------------------------------
    # Status updates (called by the scheduler after each cycle)
    # ------------------------------------------------------------------

    def mark_checked(self, ws: WatchedSession, availability: str) -> None:
        ws.last_checked = datetime.now().isoformat(timespec="seconds")
        ws.last_status = availability

    def mark_registered(self, ws: WatchedSession) -> None:
        ws.registration_status = "registered"
        log.info("Registered: %s for %s", ws.session.name, ws.account_email)

    def mark_waitlisted(self, ws: WatchedSession) -> None:
        ws.registration_status = "waitlisted"

    def mark_failed(self, ws: WatchedSession, reason: str = "") -> None:
        ws.registration_status = "failed"
        log.warning("Failed: %s for %s — %s", ws.session.name, ws.account_email, reason)

    def reset_status(self, ws: WatchedSession) -> None:
        """Reset a failed/waitlisted session back to pending for retry."""
        ws.registration_status = "pending"
