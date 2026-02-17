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
class MemberInfo:
    """A member on a Lifetime account (primary or family)."""
    member_id: int = 0
    name: str = ""
    first_name: str = ""
    last_name: str = ""
    relationship: str = ""  # e.g. "primary", "spouse", "child"

    def display_name(self) -> str:
        return self.name or f"{self.first_name} {self.last_name}".strip() or f"Member {self.member_id}"


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
    # All members on the account (primary + family)
    members: list[MemberInfo] = field(default_factory=list)

    def is_expired(self, max_age_sec: int = 3600) -> bool:
        if not self.authenticated:
            return True
        return (time.time() - self.last_auth) > max_age_sec

    def get_all_member_ids(self) -> list[int]:
        """Return all member IDs on this account."""
        if self.members:
            return [m.member_id for m in self.members]
        return [self.member_id] if self.member_id else []


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
        # Persistent member cache — survives session re-authentication
        self._member_cache: dict[str, list[MemberInfo]] = {}  # email → members

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

            # Fetch profile to get member IDs (primary + family)
            self._fetch_profile(session)

            self._sessions[email] = session
            member_names = [m.display_name() for m in session.members] if session.members else [session.member_name]
            log.info("[%s] Login successful. Members: %s", email, member_names)
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
        """Fetch member details from the profile endpoint, including family members."""
        try:
            resp = requests.get(
                PROFILE_URL,
                headers=self._headers(session),
                timeout=15,
            )
            data = resp.json()
            log.debug("[%s] Profile response keys: %s", session.email, list(data.keys()))

            details = data.get("memberDetails", data)
            session.member_id = int(details.get("memberId", 0))
            session.member_name = details.get("firstName", "")

            # Build the members list
            session.members = []

            # Primary member
            primary = MemberInfo(
                member_id=session.member_id,
                first_name=details.get("firstName", ""),
                last_name=details.get("lastName", ""),
                name=f"{details.get('firstName', '')} {details.get('lastName', '')}".strip(),
                relationship="primary",
            )
            session.members.append(primary)

            # Check for family / additional members in various response formats
            family_members = (
                data.get("familyMembers", [])
                or data.get("additionalMembers", [])
                or data.get("members", [])
                or details.get("familyMembers", [])
                or details.get("additionalMembers", [])
            )
            for fm in family_members:
                mid = int(fm.get("memberId", fm.get("id", 0)))
                if mid and mid != session.member_id:
                    member = MemberInfo(
                        member_id=mid,
                        first_name=fm.get("firstName", ""),
                        last_name=fm.get("lastName", ""),
                        name=f"{fm.get('firstName', '')} {fm.get('lastName', '')}".strip(),
                        relationship=fm.get("relationship", fm.get("type", "")),
                    )
                    session.members.append(member)

            log.info(
                "[%s] Found %d member(s) from profile: %s",
                session.email, len(session.members),
                [(m.display_name(), m.member_id) for m in session.members],
            )

            # If profile only returned the primary member, restore cached family members
            cached = self._member_cache.get(session.email)
            if cached and len(cached) > len(session.members):
                log.info(
                    "[%s] Restoring %d cached member(s) (profile only returned %d)",
                    session.email, len(cached), len(session.members),
                )
                session.members = cached

        except Exception as exc:
            log.warning("[%s] Failed to fetch profile: %s", session.email, exc)
            # Still try to restore cached members
            cached = self._member_cache.get(session.email)
            if cached:
                session.members = cached

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

            # Handle auth expiry
            if resp.status_code == 401:
                log.warning("Auth expired while checking availability for %s", event_id)
                return None

            resp.raise_for_status()
            data = resp.json()
            log.debug("Registration data for %s: %s", event_id, list(data.keys()))
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

        log.debug(
            "Event %s: spots=%s remaining=%s waitlist=%s registered=%s unregistered=%s cta='%s' disabled=%s",
            event_id, ev.has_spots, ev.remaining_spots, ev.has_waitlist,
            [m.get("name") for m in ev.registered_members],
            [m.get("name") for m in ev.unregistered_members],
            ev.register_cta_text, ev.register_disabled,
        )

        return ev

    def discover_members(self, session: AuthSession, event_id: str) -> list[MemberInfo]:
        """
        Discover all members on the account by checking an event's registration.

        The event registration endpoint returns registeredMembers and
        unregisteredMembers — this is the only reliable way to see ALL
        members (primary + family) on the account.
        """
        reg = self.get_event_registration(session, event_id)
        if not reg:
            return session.members  # fall back to what we have

        all_api_members: list[dict] = []
        all_api_members.extend(reg.registered_members or [])
        all_api_members.extend(reg.unregistered_members or [])

        if not all_api_members:
            return session.members

        # Build MemberInfo list from the API response
        discovered: list[MemberInfo] = []
        seen_ids: set[int] = set()
        for m in all_api_members:
            mid = int(m.get("id", 0))
            if not mid or mid in seen_ids:
                continue
            seen_ids.add(mid)
            name = m.get("name", "")
            discovered.append(MemberInfo(
                member_id=mid,
                name=name,
                first_name=name.split()[0] if name else "",
                last_name=" ".join(name.split()[1:]) if name and len(name.split()) > 1 else "",
                relationship="primary" if mid == session.member_id else "family",
            ))

        if discovered:
            session.members = discovered
            # Persist to cache so members survive re-authentication
            self._member_cache[session.email] = discovered
            log.info(
                "[%s] Discovered %d member(s) from event registration: %s",
                session.email, len(discovered),
                [(m.display_name(), m.member_id) for m in discovered],
            )

        return session.members

    def verify_registration(
        self,
        session: AuthSession,
        event_id: str,
        member_ids: list[int],
    ) -> str:
        """
        After a registration attempt, verify the actual outcome by checking
        the event registration endpoint.

        Returns: "registered", "waitlisted", or "unknown"
        """
        try:
            # Brief pause to let the server update its state after registration
            time.sleep(1)

            reg = self.get_event_registration(session, event_id)
            if not reg:
                return "unknown"

            registered_ids = {int(m.get("id", 0)) for m in reg.registered_members}
            all_registered = all(mid in registered_ids for mid in member_ids)

            log.debug(
                "Verification: member_ids=%s registered_ids=%s all_registered=%s "
                "has_spots=%s cta='%s' totalWaitlisted=%s",
                member_ids, registered_ids, all_registered,
                reg.has_spots, reg.register_cta_text, reg.total_waitlisted,
            )

            if all_registered:
                return "registered"

            # Only trust explicit waitlist CTA actions (e.g. "Leave Waitlist")
            cta_lower = reg.register_cta_text.lower()
            if any(kw in cta_lower for kw in ["leave wait", "on waitlist", "waitlisted"]):
                return "waitlisted"

            # Only conclude waitlisted if the member is actually on the waitlist
            # (totalWaitlisted > 0 and member NOT in registered list).
            # Previously this also checked `has_spots`, which caused false
            # positives when we grabbed the last spot.
            if reg.total_waitlisted > 0 and not all_registered:
                return "waitlisted"

            return "unknown"
        except Exception as exc:
            log.debug("Verification check failed: %s", exc)
            return "unknown"

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
        log.info(
            "[%s] Starting registration for event %s, members %s",
            session.email, event_id, member_ids,
        )

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
                msg_lower = result.message.lower()
                if any(kw in msg_lower for kw in ["not eligible", "age", "already registered"]):
                    return result
                # Auth expired — try re-login
                if "auth expired" in msg_lower or "401" in msg_lower:
                    log.info("[%s] Re-authenticating …", session.email)
                    self._apim_key = ""  # force key refresh too
                    continue
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
        # The API expects "memberId" (singular) with a list of member IDs
        body = {"eventId": event_id, "memberId": member_ids}
        log.debug("[%s] POST %s body=%s", session.email, REG_CREATE_URL, body)
        resp = requests.post(REG_CREATE_URL, json=body, headers=headers, timeout=15)

        log.debug("[%s] Create response: %d %s", session.email, resp.status_code, resp.text[:500])

        if resp.status_code == 401:
            return RegistrationResult(success=False, message="Auth expired — will re-login.")

        data = resp.json() if resp.text else {}

        # Check for validation errors
        validation = data.get("validation", {})
        if validation:
            notification = validation.get("notification", "")
            log.info("[%s] Validation: %s", session.email, validation)
            if "already registered" in notification.lower():
                return RegistrationResult(success=True, message="Already registered.")
            if validation.get("isFatal"):
                return RegistrationResult(success=False, message=notification or "Registration blocked.")
            # "tooSoon" means registration hasn't opened yet
            if "registration will be open" in notification.lower():
                return RegistrationResult(success=False, message=notification)

        # Try multiple response field names for the registration ID
        reg_id = (
            data.get("regId", "")
            or data.get("id", "")
            or data.get("registrationId", "")
            or data.get("registration", {}).get("id", "")
        )
        if not reg_id:
            # If data is a list (batch registration), get the first one
            if isinstance(data, list) and len(data) > 0:
                reg_id = data[0].get("regId", "") or data[0].get("id", "")

        if not reg_id:
            return RegistrationResult(
                success=False,
                message=f"No registration ID returned. Status: {resp.status_code}. Response: {resp.text[:500]}",
            )

        # Step 2: Complete registration
        complete_url = REG_COMPLETE_URL.format(reg_id=reg_id)
        log.debug("[%s] PUT %s", session.email, complete_url)
        resp2 = requests.put(complete_url, json={}, headers=headers, timeout=15)

        log.debug("[%s] Complete response: %d %s", session.email, resp2.status_code, resp2.text[:500])

        # Parse the completion response regardless of status code
        data2 = {}
        try:
            data2 = resp2.json() if resp2.text else {}
        except Exception:
            pass
        msg2 = data2.get("message", "") or data2.get("error", "") or ""
        full_resp_text = resp2.text[:500] if resp2.text else ""

        # Check for waitlist indicators in human-readable message fields only.
        # IMPORTANT: Do NOT scan the raw JSON response text — field names like
        # "hasWaitlist" or "totalWaitlisted" cause false positives.
        waitlist_keywords = ["waitlist", "wait list", "waiting list", "added to wait", "join wait"]
        waitlist_check_texts = [
            msg2.lower(),
            str(data2.get("notification", "")).lower(),
            str(data2.get("status", "")).lower(),
        ]
        # Also check step 1 message/notification fields
        step1_notification = str(validation.get("notification", "")).lower()
        step1_message = str(data.get("message", "")).lower() if isinstance(data, dict) else ""
        waitlist_check_texts.extend([step1_notification, step1_message])

        is_waitlisted = any(
            kw in text for text in waitlist_check_texts for kw in waitlist_keywords
        )

        if resp2.ok:
            if is_waitlisted:
                log.info("[%s] Registration completed but WAITLISTED. reg_id=%s", session.email, reg_id)
                return RegistrationResult(
                    success=False, waitlisted=True, reg_id=reg_id,
                    message=msg2 or "Added to waitlist (session was full).",
                )
            log.info("[%s] Registration completed! reg_id=%s", session.email, reg_id)
            return RegistrationResult(success=True, reg_id=reg_id, message="Registered successfully!")

        # Non-OK response
        if is_waitlisted:
            return RegistrationResult(success=False, waitlisted=True, reg_id=reg_id, message=msg2 or full_resp_text[:200])

        return RegistrationResult(success=False, reg_id=reg_id, message=f"Complete failed ({resp2.status_code}): {msg2 or full_resp_text[:200]}")

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel_registration(
        self,
        session: AuthSession,
        reg_id: str,
    ) -> RegistrationResult:
        """
        Cancel an existing registration.

        Uses PUT /sys/registrations/V3/ux/event/{reg_id}/cancel
        """
        url = REG_CANCEL_URL.format(reg_id=reg_id)
        log.info("[%s] Cancelling registration %s", session.email, reg_id)
        try:
            resp = requests.put(url, json={}, headers=self._headers(session), timeout=15)
            log.debug("[%s] Cancel response: %d %s", session.email, resp.status_code, resp.text[:500])

            if resp.status_code == 401:
                return RegistrationResult(success=False, message="Auth expired.")

            if resp.ok:
                log.info("[%s] Cancelled registration %s", session.email, reg_id)
                return RegistrationResult(success=True, reg_id=reg_id, message="Registration cancelled.")

            data = {}
            try:
                data = resp.json() if resp.text else {}
            except Exception:
                pass
            msg = data.get("message", "") or data.get("error", "") or resp.text[:200]
            return RegistrationResult(success=False, reg_id=reg_id, message=f"Cancel failed ({resp.status_code}): {msg}")

        except Exception as exc:
            log.error("[%s] Cancel error for %s: %s", session.email, reg_id, exc)
            return RegistrationResult(success=False, message=f"Error: {exc}")
