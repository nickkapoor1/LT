"""
Playwright-based login manager for Lifetime Fitness accounts.

Each account gets its own persistent browser context so cookies survive
between polling cycles.  If cookies expire the login is retried.
"""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright, Playwright

from app.config import selectors as sel
from app.config import settings

log = logging.getLogger(__name__)


class LoginManager:
    """Manages Playwright browser instances and login state for all accounts."""

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        # email → BrowserContext
        self._contexts: dict[str, BrowserContext] = {}
        # email → logged-in Page (reusable tab)
        self._pages: dict[str, Page] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the shared Playwright browser."""
        if self._browser:
            return
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=settings.HEADLESS,
            slow_mo=settings.SLOW_MO_MS,
        )
        log.info("Playwright browser launched (headless=%s)", settings.HEADLESS)

    def stop(self) -> None:
        """Close all contexts and the browser."""
        for ctx in self._contexts.values():
            try:
                ctx.close()
            except Exception:
                pass
        self._contexts.clear()
        self._pages.clear()
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._pw:
            self._pw.stop()
            self._pw = None
        log.info("Playwright browser stopped.")

    # ------------------------------------------------------------------
    # Context / page helpers
    # ------------------------------------------------------------------

    def _context_for(self, email: str) -> BrowserContext:
        """Return (or create) a persistent-ish context for the given account."""
        if email in self._contexts:
            return self._contexts[email]
        if not self._browser:
            self.start()

        storage_dir = settings.SESSION_DIR / email.replace("@", "_at_")
        storage_dir.mkdir(exist_ok=True)
        storage_file = storage_dir / "state.json"

        ctx: BrowserContext
        if storage_file.exists():
            ctx = self._browser.new_context(
                storage_state=str(storage_file),
            )
        else:
            ctx = self._browser.new_context()

        ctx.set_default_timeout(settings.BROWSER_TIMEOUT_MS)
        self._contexts[email] = ctx
        return ctx

    def page_for(self, email: str) -> Page:
        """Return a reusable page for the given account."""
        if email in self._pages and not self._pages[email].is_closed():
            return self._pages[email]
        ctx = self._context_for(email)
        page = ctx.new_page()
        self._pages[email] = page
        return page

    def _save_storage(self, email: str) -> None:
        """Persist cookies / local-storage so next launch is faster."""
        ctx = self._contexts.get(email)
        if not ctx:
            return
        storage_dir = settings.SESSION_DIR / email.replace("@", "_at_")
        storage_dir.mkdir(exist_ok=True)
        ctx.storage_state(path=str(storage_dir / "state.json"))

    # ------------------------------------------------------------------
    # Login flow
    # ------------------------------------------------------------------

    def login(self, email: str, password: str) -> bool:
        """
        Log in to Lifetime Fitness for the given account.

        Returns True on success, False on failure.
        """
        page = self.page_for(email)
        try:
            log.info("[%s] Navigating to login page …", email)
            page.goto(settings.LOGIN_URL, wait_until="domcontentloaded")

            # Dismiss cookie banners if present
            try:
                page.click(sel.COOKIE_ACCEPT_BUTTON, timeout=3000)
            except Exception:
                pass

            # Check if we're already logged in (saved cookies still valid)
            if self._is_logged_in(page):
                log.info("[%s] Already logged in (cookies valid).", email)
                return True

            # Fill credentials
            page.fill(sel.LOGIN_EMAIL_INPUT, email)
            page.fill(sel.LOGIN_PASSWORD_INPUT, password)
            page.click(sel.LOGIN_SUBMIT_BUTTON)

            # Wait for navigation after submit
            page.wait_for_load_state("domcontentloaded")

            # Check result
            if self._is_logged_in(page):
                log.info("[%s] Login successful.", email)
                self._save_storage(email)
                return True

            # Check for explicit error
            try:
                err = page.query_selector(sel.LOGIN_ERROR_INDICATOR)
                if err:
                    log.warning("[%s] Login failed: %s", email, err.inner_text())
            except Exception:
                pass

            log.warning("[%s] Login failed (no success indicator found).", email)
            return False

        except Exception as exc:
            log.error("[%s] Login error: %s", email, exc)
            return False

    def ensure_logged_in(self, email: str, password: str) -> bool:
        """Verify the session is still alive; re-login if needed."""
        page = self.page_for(email)
        try:
            # Quick check — navigate to a protected page
            page.goto(settings.BASE_URL, wait_until="domcontentloaded")
            if self._is_logged_in(page):
                return True
        except Exception:
            pass
        log.info("[%s] Session expired, re-logging in …", email)
        return self.login(email, password)

    def logout(self, email: str) -> None:
        """Close context and discard session for an account."""
        page = self._pages.pop(email, None)
        if page and not page.is_closed():
            page.close()
        ctx = self._contexts.pop(email, None)
        if ctx:
            ctx.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _is_logged_in(page: Page) -> bool:
        """Return True if the page shows a logged-in indicator."""
        try:
            indicator = page.query_selector(sel.LOGIN_SUCCESS_INDICATOR)
            return indicator is not None
        except Exception:
            return False
