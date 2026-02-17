"""
Registration engine — attempts to book a pickleball session for an account.

Handles:
- Clicking the Register / Book button
- Confirming the registration modal
- Detecting success or failure
- Falling back to Waitlist when the session is full
- Retry with exponential backoff
"""

from __future__ import annotations

import logging
import time

from playwright.sync_api import Page

from app.config import selectors as sel
from app.config import settings

log = logging.getLogger(__name__)


class RegistrationResult:
    """Outcome of a registration attempt."""

    def __init__(
        self,
        success: bool,
        waitlisted: bool = False,
        message: str = "",
        attempts: int = 1,
    ):
        self.success = success
        self.waitlisted = waitlisted
        self.message = message
        self.attempts = attempts

    def __repr__(self) -> str:
        status = "OK" if self.success else ("WAITLIST" if self.waitlisted else "FAIL")
        return f"RegistrationResult({status}, {self.message!r}, attempts={self.attempts})"


def register_for_session(
    page: Page,
    session_url: str,
    email: str,
    max_retries: int = settings.MAX_RETRY_ATTEMPTS,
) -> RegistrationResult:
    """
    Attempt to register the currently-logged-in account for a session.

    Parameters
    ----------
    page : Page
        Authenticated Playwright page for this account.
    session_url : str
        URL of the session detail / schedule page containing the Register button.
    email : str
        Account email (used only for logging).
    max_retries : int
        Number of retry attempts on transient failures.

    Returns
    -------
    RegistrationResult
    """
    for attempt in range(1, max_retries + 1):
        log.info("[%s] Registration attempt %d/%d for %s", email, attempt, max_retries, session_url)
        try:
            result = _try_register(page, session_url, email)
            result.attempts = attempt
            if result.success or result.waitlisted:
                return result
            # If it's a definitive "full" with no waitlist, no point retrying
            if "full" in result.message.lower() and "waitlist" not in result.message.lower():
                return result
        except Exception as exc:
            log.error("[%s] Attempt %d error: %s", email, attempt, exc)
            if attempt == max_retries:
                return RegistrationResult(
                    success=False,
                    message=f"Error after {max_retries} attempts: {exc}",
                    attempts=attempt,
                )

        # Exponential backoff before retry
        delay = settings.RETRY_DELAY_SEC * (2 ** (attempt - 1))
        log.info("[%s] Waiting %ds before retry …", email, delay)
        time.sleep(delay)

    return RegistrationResult(success=False, message="Max retries exhausted.", attempts=max_retries)


# ------------------------------------------------------------------
# Internal registration flow
# ------------------------------------------------------------------

def _try_register(page: Page, session_url: str, email: str) -> RegistrationResult:
    """Single attempt to register."""

    # Navigate to the session page
    page.goto(session_url, wait_until="domcontentloaded")

    # Dismiss cookie banners
    try:
        page.click(sel.COOKIE_ACCEPT_BUTTON, timeout=2000)
    except Exception:
        pass

    # Look for a Register / Book button
    register_btn = page.query_selector(sel.REGISTER_BUTTON)

    if register_btn:
        log.info("[%s] Found Register button — clicking …", email)
        register_btn.click()

        # Handle confirmation modal if one appears
        try:
            page.wait_for_selector(sel.CONFIRM_REGISTER_BUTTON, timeout=5000)
            page.click(sel.CONFIRM_REGISTER_BUTTON)
            log.info("[%s] Clicked confirmation button.", email)
        except Exception:
            # No confirmation modal — that's fine, some sessions register immediately
            pass

        # Wait a moment for the result
        page.wait_for_load_state("domcontentloaded")

        # Check for success
        if _check_success(page):
            log.info("[%s] Registration successful!", email)
            return RegistrationResult(success=True, message="Registered successfully.")

        # Check if we ended up on a waitlist
        if _check_waitlisted(page):
            log.info("[%s] Added to waitlist.", email)
            return RegistrationResult(success=False, waitlisted=True, message="Added to waitlist.")

        return RegistrationResult(success=False, message="Register clicked but no success confirmation found.")

    # No Register button — try Waitlist
    waitlist_btn = page.query_selector(sel.WAITLIST_BUTTON)
    if waitlist_btn:
        log.info("[%s] Session full — joining waitlist …", email)
        waitlist_btn.click()

        try:
            page.wait_for_selector(sel.CONFIRM_REGISTER_BUTTON, timeout=5000)
            page.click(sel.CONFIRM_REGISTER_BUTTON)
        except Exception:
            pass

        page.wait_for_load_state("domcontentloaded")
        return RegistrationResult(success=False, waitlisted=True, message="Joined waitlist (session was full).")

    # Neither button found
    page_text = (page.inner_text("body") or "")[:500]
    log.warning("[%s] No Register or Waitlist button found. Page text: %s", email, page_text)
    return RegistrationResult(success=False, message="No registration button found on page.")


def _check_success(page: Page) -> bool:
    """Look for a success indicator on the page."""
    try:
        el = page.query_selector(sel.REGISTRATION_SUCCESS)
        return el is not None
    except Exception:
        return False


def _check_waitlisted(page: Page) -> bool:
    """Check if the page indicates a waitlist placement."""
    try:
        text = (page.inner_text("body") or "").lower()
        return "waitlist" in text and ("added" in text or "joined" in text)
    except Exception:
        return False
