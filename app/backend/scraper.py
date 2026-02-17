"""
Scrape pickleball session data from the Lifetime Fitness schedule page.

The scraper navigates to a club's class schedule, filters for pickleball,
and extracts structured session data for display and monitoring.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

from playwright.sync_api import Page

from app.config import selectors as sel
from app.config import settings

log = logging.getLogger(__name__)


@dataclass
class Session:
    """A single pickleball session parsed from the schedule page."""
    name: str = ""
    date: str = ""           # e.g. "2026-02-20"
    time: str = ""           # e.g. "6:00 PM – 8:00 PM"
    instructor: str = ""
    availability: str = ""   # e.g. "Open", "3 spots", "Full", "Waitlist"
    url: str = ""            # direct link to the session detail / registration
    registered: bool = False
    raw_card_text: str = ""  # full text of the card for debugging

    def is_open(self) -> bool:
        """Return True if the session appears to have availability."""
        lower = self.availability.lower()
        if any(w in lower for w in ("full", "closed", "unavailable", "0 spot")):
            return False
        if any(w in lower for w in ("open", "spot", "available", "register", "book")):
            return True
        # If we can't tell, assume open so the registrar will attempt it
        return True

    def to_dict(self) -> dict:
        return asdict(self)


def scrape_schedule(
    page: Page,
    club_slug: str,
    target_date: str | None = None,
    session_types: list[str] | None = None,
) -> list[Session]:
    """
    Scrape sessions from a club's schedule page for a given date.

    Parameters
    ----------
    page : Page
        An already-authenticated Playwright page.
    club_slug : str
        The URL slug for the club (e.g. "johns-creek").
    target_date : str | None
        ISO date string ("YYYY-MM-DD"). Defaults to today.
    session_types : list[str] | None
        Only return sessions whose name matches one of these strings
        (case-insensitive substring match).  None = return all pickleball.

    Returns
    -------
    list[Session]
    """
    if session_types is None:
        session_types = settings.SESSION_TYPES

    url = settings.SCHEDULE_URL_TEMPLATE.format(club_slug=club_slug)
    if target_date:
        url += f"?date={target_date}"

    log.info("Scraping schedule: %s", url)
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as exc:
        log.error("Failed to load schedule page: %s", exc)
        return []

    # Dismiss cookie banners
    try:
        page.click(sel.COOKIE_ACCEPT_BUTTON, timeout=2000)
    except Exception:
        pass

    # Wait for session cards to appear
    try:
        page.wait_for_selector(sel.SESSION_CARD, timeout=10_000)
    except Exception:
        log.warning("No session cards found on page — schedule may be empty or selectors are stale.")
        return []

    cards = page.query_selector_all(sel.SESSION_CARD)
    log.info("Found %d total session cards on page.", len(cards))

    sessions: list[Session] = []
    for card in cards:
        try:
            s = _parse_card(card, page.url)
        except Exception as exc:
            log.debug("Error parsing card: %s", exc)
            continue

        # Filter to pickleball sessions
        if not _matches_type(s.name, session_types):
            continue

        sessions.append(s)

    log.info("Extracted %d pickleball sessions.", len(sessions))
    return sessions


def scrape_week(
    page: Page,
    club_slug: str,
    weeks: int = 2,
    session_types: list[str] | None = None,
) -> list[Session]:
    """Scrape sessions for the next *weeks* weeks."""
    all_sessions: list[Session] = []
    today = datetime.now().date()
    for day_offset in range(weeks * 7):
        target = today + timedelta(days=day_offset)
        day_sessions = scrape_schedule(
            page, club_slug, target.isoformat(), session_types,
        )
        all_sessions.extend(day_sessions)
    return _deduplicate(all_sessions)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _parse_card(card, page_url: str) -> Session:
    """Extract session data from a single card element."""
    s = Session()
    s.raw_card_text = (card.inner_text() or "").strip()

    s.name = _text(card, sel.SESSION_NAME)
    s.date = _text(card, sel.SESSION_DATE)
    s.time = _text(card, sel.SESSION_TIME)
    s.instructor = _text(card, sel.SESSION_INSTRUCTOR)
    s.availability = _text(card, sel.SESSION_AVAILABILITY) or "Unknown"

    # Try to extract a direct link to the session
    link = card.query_selector("a[href]")
    if link:
        href = link.get_attribute("href") or ""
        if href.startswith("/"):
            s.url = settings.BASE_URL + href
        elif href.startswith("http"):
            s.url = href
    if not s.url:
        s.url = page_url

    return s


def _text(card, selector: str) -> str:
    """Safely extract inner text from a sub-element of a card."""
    el = card.query_selector(selector)
    if el:
        return (el.inner_text() or "").strip()
    return ""


def _matches_type(name: str, types: list[str]) -> bool:
    """Check if a session name matches any of the target types."""
    name_lower = name.lower()
    return any(t.lower() in name_lower for t in types)


def _deduplicate(sessions: list[Session]) -> list[Session]:
    """Remove duplicate sessions based on name + date + time."""
    seen: set[str] = set()
    unique: list[Session] = []
    for s in sessions:
        key = f"{s.name}|{s.date}|{s.time}"
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique
