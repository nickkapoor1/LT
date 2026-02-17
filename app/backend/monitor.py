"""
Session monitor — tracks which events each account is watching
and their registration status.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from app.backend.api_client import Event

log = logging.getLogger(__name__)


@dataclass
class WatchedEvent:
    """An event that an account is monitoring for registration."""
    event: Event
    account_email: str
    member_ids: list[int]           # Which members to register
    auto_register: bool = True
    # Tracking
    last_checked: str = ""
    last_status: str = ""
    registration_status: str = "pending"  # pending | registered | waitlisted | failed

    @property
    def key(self) -> str:
        return f"{self.account_email}|{self.event.event_id}"


class Monitor:
    """Manages the set of watched events."""

    def __init__(self) -> None:
        self._watched: dict[str, WatchedEvent] = {}

    def watch(
        self,
        event: Event,
        account_email: str,
        member_ids: list[int],
        auto_register: bool = True,
    ) -> WatchedEvent:
        ws = WatchedEvent(
            event=event,
            account_email=account_email,
            member_ids=member_ids,
            auto_register=auto_register,
        )
        self._watched[ws.key] = ws
        log.info("Watching: %s for %s (members: %s)", event.title, account_email, member_ids)
        return ws

    def unwatch(self, event_id: str, account_email: str) -> None:
        key = f"{account_email}|{event_id}"
        self._watched.pop(key, None)

    def unwatch_all(self, account_email: str | None = None) -> None:
        if account_email:
            self._watched = {k: v for k, v in self._watched.items() if v.account_email != account_email}
        else:
            self._watched.clear()

    def all_watched(self) -> list[WatchedEvent]:
        return list(self._watched.values())

    def pending(self) -> list[WatchedEvent]:
        return [
            ws for ws in self._watched.values()
            if ws.registration_status == "pending" and ws.auto_register
        ]

    def mark_checked(self, ws: WatchedEvent, status: str) -> None:
        ws.last_checked = datetime.now().strftime("%H:%M:%S")
        ws.last_status = status

    def mark_registered(self, ws: WatchedEvent) -> None:
        ws.registration_status = "registered"

    def mark_waitlisted(self, ws: WatchedEvent) -> None:
        ws.registration_status = "waitlisted"

    def mark_failed(self, ws: WatchedEvent, reason: str = "") -> None:
        ws.registration_status = "failed"

    def reset_status(self, ws: WatchedEvent) -> None:
        ws.registration_status = "pending"
