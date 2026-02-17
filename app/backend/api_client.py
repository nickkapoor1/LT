"""
Lifetime Fitness API client — handles authentication, schedule browsing,
and registration via direct HTTP calls (no browser automation needed).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

import requests

from app.config.settings import (
    AUTH_URL,
    EVENTS_URL,
    EVENT_DETAIL_URL,
    EVENT_REGISTRATION_URL,
    MAX_RETRY_ATTEMPTS,
    PROFILE_URL,
    REG_COMPLETE_URL,
    REG_CREATE_URL,
    REG_GET_URL,
    RESERVATIONS_URL,
    RETRY_DELAY_SEC,
    fetch_api_keys,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AuthSession:
    """Stores authentication state for one Lifetime account."""
    email: str
    token: str = ""
    sso_id: str = ""
    party_id: str = ""
    member_id: int = 0
    member_name: str = ""
    authenticated: bool = False
    last_auth: float = 0.0  # timestamp

    def is_expired(self, max_age_sec: int = 3600) -> bool:
        if not self.authenticated:
            return True
        return (time.time() - self.last_auth) > max_age_sec


@dataclass
class Event:
    """A single pickleball event from the schedule API."""
    event_id: str = ""
    title: str = ""
    club: str = ""
    club_id: int = 0
    room: str = ""
    location: str = ""
    description: str = ""
    start: str = ""            # ISO datetime
    end: str = ""              # ISO datetime
    is_registrable: bool = False
    is_paid: bool = False
    upc: str = ""
    image_url: str = ""
    # Registration info (populated by get_event_registration)
    has_spots: bool | None = None
    remaining_spots: int | None = None
    has_waitlist: bool | None = None
    total_waitlisted: int = 0
    register_cta_text: str = ""
    register_disabled: bool = False
    registered_members: list[dict] = field(default_factory=list)
    unregistered_members: list[dict] = field(default_factory=list)
    # Snipe timing (from tooSoonRule)
    registration_opens_at: str = ""   # human-readable
    too_soon_minutes: int = 0

    def display_time(self) -> str:
        """Format start/end for display."""
        try:
            s = datetime.fromisoformat(self.start)
            e = datetime.fromisoformat(self.end)
            return f"{s.strftime('%a %b %d, %I:%M %p')} – {e.strftime('%I:%M %p')}"
        except Exception:
            return f"{self.start} – {self.end}"

    def display_date(self) -> str:
        try:
            return datetime.fromisoformat(self.start).strftime("%Y-%m-%d")
        except Exception:
            return self.start[:10]


@dataclass
class RegistrationResult:
    success: bool
    waitlisted: bool = False
    message: str = ""
    reg_id: str = ""


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class LifetimeAPI:
    """Stateless-ish API client. Each account gets its own AuthSession."""

    def __init__(self) -> None:
        self._sessions: dict[str, AuthSession] = {}  # email → AuthSession
        self._apim_key: str = ""

    def _ensure_keys(self) -> str:
        """Fetch and cache the APIM subscription key."""
        if not self._apim_key:
            self._apim_key, _ = fetch_api_keys()
        return self._apim_key

    def _headers(self, session: AuthSession | None = None) -> dict:
        """Build common request headers."""
        key = self._ensure_keys()
        h = {
            "ocp-apim-subscription-key": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        if session and session.token:
            h["Authorization"] = f"Bearer {session.token}"
            h["x-ltf-ssoid"] = session.sso_id
        return h

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def login(self, email: str, password: str) -> AuthSession:
        """
        Authenticate with Lifetime and store the session.

        Returns the AuthSession (check .authenticated for success).
        """
        session = AuthSession(email=email)
        key = self._ensure_keys()

        try:
            resp = requests.post(
                AUTH_URL,
                json={"username": email, "password": password},
                headers={
                    "ocp-apim-subscription-key": key,
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            data = resp.json()

            if data.get("message") != "Success":
                session.authenticated = False
                log.warning("[%s] Login failed: %s", email, data.get("message", resp.text))
                return session

            session.token = data["token"]
            session.sso_id = data["ssoId"]
            session.party_id = data.get("partyId", "")
            session.authenticated = True
            session.last_auth = time.time()

            # Fetch profile to get member IDs
            self._fetch_profile(session)

            self._sessions[email] = session
            log.info("[%s] Login successful (member_id=%s).", email, session.member_id)
            return session

        except Exception as exc:
            log.error("[%s] Login error: %s", email, exc)
            return session

    def ensure_authenticated(self, email: str, password: str) -> AuthSession:
        """Return a valid session, re-authenticating if expired."""
        session = self._sessions.get(email)
        if session and not session.is_expired():
            return session
        return self.login(email, password)

    def get_session(self, email: str) -> AuthSession | None:
        return self._sessions.get(email)

    def _fetch_profile(self, session: AuthSession) -> None:
        """Fetch member details from the profile endpoint."""
        try:
            resp = requests.get(
                PROFILE_URL,
                headers=self._headers(session),
                timeout=15,
            )
            data = resp.json()
            details = data.get("memberDetails", data)
            session.member_id = int(details.get("memberId", 0))
            session.member_name = details.get("firstName", "")
        except Exception as exc:
            log.warning("[%s] Failed to fetch profile: %s", session.email, exc)

    # ------------------------------------------------------------------
    # Schedule browsing
    # ------------------------------------------------------------------

    def get_events(
        self,
        session: AuthSession,
        club_name: str,
        start_date: str,
        end_date: str,
        tags: str = "department:Pickleball",
        page_size: int = 200,
    ) -> list[Event]:
        """
        Fetch events (classes) for a club within a date range.

        Parameters
        ----------
        club_name : str
            Club display name exactly as the API expects, e.g. "PENN 1".
        start_date / end_date : str
            Dates in MM/DD/YYYY format.
        tags : str
            Filter tag, e.g. "department:Pickleball".
        """
        params = {
            "locations": club_name,
            "start": start_date,
            "end": end_date,
            "status": "confirmed",
            "tags": tags,
            "pageSize": page_size,
        }
        try:
            resp = requests.get(
                EVENTS_URL,
                params=params,
                headers=self._headers(session),
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.error("Failed to fetch events: %s", exc)
            return []

        events: list[Event] = []
        for r in data.get("results", []):
            ev = Event(
                event_id=r.get("id", ""),
                title=r.get("title", ""),
                club=r.get("club", ""),
                club_id=r.get("clubId", 0),
                room=r.get("room", ""),
                location=r.get("location", ""),
                description=r.get("description", ""),
                start=r.get("start", ""),
                end=r.get("end", ""),
                is_registrable=r.get("isRegistrable", False),
                is_paid=r.get("isPaidClass", False),
                upc=r.get("upc", ""),
                image_url=r.get("imageUrl", ""),
            )
            events.append(ev)

        log.info("Fetched %d pickleball events for %s.", len(events), club_name)
        return events

    def get_event_registration(self, session: AuthSession, event_id: str) -> Event | None:
        """
        Get registration details for a specific event (spots, waitlist, members).
        Returns an Event populated with registration fields, or None on error.
        """
        url = EVENT_REGISTRATION_URL.format(event_id=event_id)
        try:
            resp = requests.get(url, headers=self._headers(session), timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.error("Failed to get registration info for %s: %s", event_id, exc)
            return None

        ev = Event(event_id=event_id)
        ev.has_spots = data.get("hasSpots", False)
        ev.remaining_spots = data.get("remainingSpots", 0)
        ev.has_waitlist = data.get("hasWaitlist", False)
        ev.total_waitlisted = data.get("totalWaitlisted", 0)
        ev.registered_members = data.get("registeredMembers", [])
        ev.unregistered_members = data.get("unregisteredMembers", [])

        cta = data.get("registerCta", {})
        ev.register_cta_text = cta.get("text", "")
        ev.register_disabled = cta.get("disabled", False)

        rules = data.get("rules", {})
        too_soon = rules.get("tooSoonRule", {})
        ev.registration_opens_at = too_soon.get("errorMessage", "")
        ev.too_soon_minutes = too_soon.get("minutesFromStart", 0)

        return ev

    def get_my_reservations(
        self,
        session: AuthSession,
        start_date: str,
        end_date: str,
        member_ids: str = "",
    ) -> list[dict]:
        """Fetch existing reservations for the account's members."""
        params: dict = {
            "start": start_date,
            "end": end_date,
            "groupCamps": "true",
            "pageSize": "200",
        }
        if member_ids:
            params["memberIds"] = member_ids
        try:
            resp = requests.get(
                RESERVATIONS_URL,
                params=params,
                headers=self._headers(session),
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json().get("results", [])
        except Exception as exc:
            log.error("Failed to fetch reservations: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        session: AuthSession,
        event_id: str,
        member_ids: list[int],
    ) -> RegistrationResult:
        """
        Register member(s) for an event.

        Flow:
        1. POST /sys/registrations/V3/ux/event  →  creates registration
        2. PUT  .../complete  →  finalizes it

        Returns RegistrationResult.
        """
        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            log.info(
                "[%s] Registration attempt %d/%d for event %s, members %s",
                session.email, attempt, MAX_RETRY_ATTEMPTS, event_id, member_ids,
            )
            try:
                result = self._try_register(session, event_id, member_ids)
                if result.success or result.waitlisted:
                    return result
                # If it's a non-retryable error, stop
                if "not eligible" in result.message.lower() or "age" in result.message.lower():
                    return result
            except Exception as exc:
                log.error("[%s] Attempt %d error: %s", session.email, attempt, exc)
                if attempt == MAX_RETRY_ATTEMPTS:
                    return RegistrationResult(success=False, message=f"Error: {exc}")

            time.sleep(RETRY_DELAY_SEC * attempt)

        return RegistrationResult(success=False, message="Max retries exhausted.")

    def _try_register(
        self,
        session: AuthSession,
        event_id: str,
        member_ids: list[int],
    ) -> RegistrationResult:
        """Single registration attempt."""
        headers = self._headers(session)

        # Step 1: Create registration
        body = {"eventId": event_id, "memberId": member_ids}
        resp = requests.post(REG_CREATE_URL, json=body, headers=headers, timeout=15)

        if resp.status_code == 401:
            return RegistrationResult(success=False, message="Auth expired — will re-login.")

        data = resp.json() if resp.text else {}

        # Check for validation errors
        validation = data.get("validation", {})
        if validation:
            notification = validation.get("notification", "")
            if "already registered" in notification.lower():
                return RegistrationResult(success=True, message="Already registered.")
            if validation.get("isFatal"):
                return RegistrationResult(success=False, message=notification or "Registration blocked.")
            # "tooSoon" means registration hasn't opened yet
            if "registration will be open" in notification.lower():
                return RegistrationResult(success=False, message=notification)

        reg_id = data.get("id", "")
        if not reg_id:
            return RegistrationResult(
                success=False,
                message=f"No registration ID returned. Response: {resp.text[:300]}",
            )

        # Step 2: Complete registration
        complete_url = REG_COMPLETE_URL.format(reg_id=reg_id)
        resp2 = requests.put(complete_url, json={}, headers=headers, timeout=15)

        if resp2.ok:
            log.info("[%s] Registration completed! reg_id=%s", session.email, reg_id)
            return RegistrationResult(success=True, reg_id=reg_id, message="Registered successfully!")

        # Check if we're waitlisted
        data2 = resp2.json() if resp2.text else {}
        msg = data2.get("message", resp2.text[:200])
        if "waitlist" in msg.lower():
            return RegistrationResult(success=False, waitlisted=True, reg_id=reg_id, message=msg)

        return RegistrationResult(success=False, reg_id=reg_id, message=f"Complete failed: {msg}")
