"""
Lifetime Pickleball Auto-Registration — Streamlit UI

Run with:
    streamlit run main.py
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so "app.*" imports work when
# Streamlit is launched from the project directory.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backend.crypto import add_account, load_accounts, remove_account, save_accounts
from app.backend.monitor import Monitor
from app.backend.scheduler import Scheduler
from app.backend.scraper import Session
from app.config.settings import (
    CLUB_SLUGS,
    DEFAULT_POLL_INTERVAL_SEC,
    MAX_POLL_INTERVAL_SEC,
    MIN_POLL_INTERVAL_SEC,
    SESSION_TYPES,
)

# ---------------------------------------------------------------------------
# Logging setup (once)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Streamlit page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Pickleball Auto-Register",
    page_icon="🏓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Persistent objects stored in Streamlit session state so they survive reruns.
# ---------------------------------------------------------------------------
if "monitor" not in st.session_state:
    st.session_state.monitor = Monitor()

if "scheduler" not in st.session_state:
    st.session_state.scheduler = Scheduler(st.session_state.monitor)

if "scraped_sessions" not in st.session_state:
    st.session_state.scraped_sessions: list[dict] = []

monitor: Monitor = st.session_state.monitor
scheduler: Scheduler = st.session_state.scheduler


# ===================================================================
# SIDEBAR — Navigation
# ===================================================================
st.sidebar.title("Pickleball Auto-Register")
page = st.sidebar.radio(
    "Navigate",
    ["Dashboard", "Accounts", "Schedule", "Settings"],
    index=0,
)

# Engine controls in sidebar
st.sidebar.markdown("---")
st.sidebar.subheader("Engine Controls")

if scheduler.running:
    st.sidebar.success("Engine is RUNNING")
    if st.sidebar.button("Stop Engine", type="primary", use_container_width=True):
        scheduler.stop()
        st.rerun()
else:
    st.sidebar.warning("Engine is STOPPED")
    if st.sidebar.button("Start Engine", type="primary", use_container_width=True):
        accounts = load_accounts()
        if not accounts:
            st.sidebar.error("Add at least one account first.")
        elif not monitor.pending():
            st.sidebar.error("Select at least one session to watch first.")
        else:
            scheduler.start()
            st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption(
    "Polling every **{}s** · {} account(s) · {} watched session(s)".format(
        scheduler.poll_interval,
        len(load_accounts()),
        len(monitor.all_watched()),
    )
)


# ===================================================================
# PAGE: Dashboard
# ===================================================================
def page_dashboard() -> None:
    st.header("Dashboard")

    accounts = load_accounts()
    watched = monitor.all_watched()

    # --- Metrics row ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Accounts", len(accounts))
    c2.metric("Watched Sessions", len(watched))
    c3.metric("Registered", sum(1 for w in watched if w.registration_status == "registered"))
    c4.metric("Engine", "Running" if scheduler.running else "Stopped")

    # --- Watched sessions table ---
    st.subheader("Watched Sessions")
    if not watched:
        st.info("No sessions being watched yet. Go to **Schedule** to add some.")
    else:
        for ws in watched:
            status_icon = {
                "pending": "🔵",
                "registered": "✅",
                "waitlisted": "🟡",
                "failed": "🔴",
            }.get(ws.registration_status, "⚪")

            with st.expander(
                f"{status_icon} {ws.session.name} — {ws.session.date} {ws.session.time}  |  {ws.account_email}",
                expanded=False,
            ):
                col1, col2 = st.columns(2)
                col1.write(f"**Account:** {ws.account_email}")
                col1.write(f"**Status:** {ws.registration_status}")
                col2.write(f"**Last checked:** {ws.last_checked or 'never'}")
                col2.write(f"**Availability:** {ws.last_status or 'unknown'}")

                bcol1, bcol2 = st.columns(2)
                if ws.registration_status in ("failed", "waitlisted"):
                    if bcol1.button("Retry", key=f"retry_{ws.key}"):
                        monitor.reset_status(ws)
                        st.rerun()
                if bcol2.button("Remove", key=f"remove_{ws.key}"):
                    monitor.unwatch(ws.session, ws.account_email)
                    st.rerun()

    # --- Activity log ---
    st.subheader("Activity Log")
    log_entries = scheduler.get_recent_log(30)
    if not log_entries:
        st.caption("No activity yet. Start the engine to begin monitoring.")
    else:
        for entry in log_entries:
            color = {
                "success": "green",
                "error": "red",
                "warning": "orange",
            }.get(entry.level, "gray")
            st.markdown(
                f"<span style='color:{color}'>[{entry.timestamp}]</span> "
                f"**{entry.account}** — {entry.message}",
                unsafe_allow_html=True,
            )

    # Auto-refresh while engine is running
    if scheduler.running:
        st.caption("Auto-refreshing every 10 seconds …")
        import time
        time.sleep(10)
        st.rerun()


# ===================================================================
# PAGE: Accounts
# ===================================================================
def page_accounts() -> None:
    st.header("Manage Accounts")

    accounts = load_accounts()

    # --- Existing accounts ---
    if accounts:
        st.subheader(f"Stored Accounts ({len(accounts)})")
        for i, acct in enumerate(accounts):
            with st.expander(f"{'✅' if acct.get('enabled', True) else '⏸️'}  {acct['email']}  —  {acct.get('club_name', '')}"):
                st.write(f"**Club:** {acct.get('club_name', 'N/A')} (`{acct.get('club_slug', '')}`)")
                st.write(f"**Session types:** {', '.join(acct.get('session_types', [])) or 'All pickleball'}")
                st.write(f"**Enabled:** {acct.get('enabled', True)}")

                col1, col2, col3 = st.columns(3)
                # Toggle enable/disable
                if acct.get("enabled", True):
                    if col1.button("Disable", key=f"disable_{i}"):
                        acct["enabled"] = False
                        save_accounts(accounts)
                        st.rerun()
                else:
                    if col1.button("Enable", key=f"enable_{i}"):
                        acct["enabled"] = True
                        save_accounts(accounts)
                        st.rerun()

                if col2.button("Remove", key=f"rm_{i}"):
                    remove_account(acct["email"])
                    monitor.unwatch_all(acct["email"])
                    st.rerun()
    else:
        st.info("No accounts yet. Add one below.")

    # --- Add new account ---
    st.subheader("Add New Account")
    with st.form("add_account_form", clear_on_submit=True):
        email = st.text_input("Lifetime Email")
        password = st.text_input("Password", type="password")

        club_name = st.selectbox("Club Location", list(CLUB_SLUGS.keys()))
        custom_slug = ""
        if club_name == "Custom (enter slug below)":
            custom_slug = st.text_input(
                "Custom club slug",
                help="The URL slug for your club — e.g. 'johns-creek' from my.lifetime.life/clubs/johns-creek/classes.html",
            )

        session_types = st.multiselect(
            "Session types to monitor",
            SESSION_TYPES,
            default=SESSION_TYPES[:2],
            help="Leave empty to match all pickleball sessions.",
        )

        submitted = st.form_submit_button("Add Account")
        if submitted:
            if not email or not password:
                st.error("Email and password are required.")
            else:
                slug = custom_slug if club_name == "Custom (enter slug below)" else CLUB_SLUGS[club_name]
                if not slug:
                    st.error("Please enter a club slug.")
                else:
                    add_account(email, password, slug, club_name, session_types)
                    st.success(f"Account **{email}** saved.")
                    st.rerun()


# ===================================================================
# PAGE: Schedule
# ===================================================================
def page_schedule() -> None:
    st.header("Pickleball Schedule")

    accounts = load_accounts()
    if not accounts:
        st.warning("Add at least one account in the **Accounts** tab first.")
        return

    # Controls
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        acct_emails = [a["email"] for a in accounts if a.get("enabled", True)]
        if not acct_emails:
            st.warning("No enabled accounts.")
            return
        selected_email = st.selectbox("Use account", acct_emails)

    selected_acct = next(a for a in accounts if a["email"] == selected_email)

    with col2:
        target_date = st.date_input("Date", value=datetime.now().date())

    with col3:
        scrape_btn = st.button("Refresh Schedule", use_container_width=True)

    # Scrape on button press
    if scrape_btn:
        with st.spinner("Logging in and scraping schedule …"):
            try:
                from app.backend.login import LoginManager
                from app.backend.scraper import scrape_schedule

                lm = LoginManager()
                lm.start()
                ok = lm.login(selected_acct["email"], selected_acct["password"])
                if not ok:
                    st.error("Login failed — check credentials.")
                    lm.stop()
                    return

                page = lm.page_for(selected_acct["email"])
                sessions = scrape_schedule(
                    page,
                    selected_acct["club_slug"],
                    target_date=target_date.isoformat(),
                    session_types=selected_acct.get("session_types") or None,
                )
                lm.stop()

                st.session_state.scraped_sessions = [s.to_dict() for s in sessions]
                st.session_state.scrape_account = selected_email
                st.session_state.scrape_date = target_date.isoformat()
                st.success(f"Found {len(sessions)} pickleball session(s).")
            except Exception as exc:
                st.error(f"Scrape error: {exc}")

    # Display scraped sessions
    sessions_data: list[dict] = st.session_state.get("scraped_sessions", [])
    if sessions_data:
        st.subheader(
            f"Sessions for {st.session_state.get('scrape_date', '')} "
            f"(via {st.session_state.get('scrape_account', '')})"
        )

        for i, sd in enumerate(sessions_data):
            avail = sd.get("availability", "Unknown")
            icon = "🟢" if "open" in avail.lower() or "spot" in avail.lower() else "🔴"

            with st.container():
                c1, c2, c3 = st.columns([4, 2, 2])
                c1.markdown(f"**{icon} {sd['name']}**")
                c1.caption(f"{sd['date']}  ·  {sd['time']}")
                c2.write(f"Availability: **{avail}**")

                # Let the user pick which accounts to watch this session for
                watch_accounts = c3.multiselect(
                    "Watch for",
                    acct_emails,
                    key=f"watch_accts_{i}",
                    label_visibility="collapsed",
                    placeholder="Select accounts …",
                )
                if c3.button("Add to watchlist", key=f"watch_{i}"):
                    session_obj = Session(**sd)
                    for em in watch_accounts:
                        monitor.watch(session_obj, em, auto_register=True)
                    if watch_accounts:
                        st.success(f"Watching **{sd['name']}** for {len(watch_accounts)} account(s).")
                    else:
                        st.warning("Select at least one account.")

                st.divider()
    else:
        st.caption("Click **Refresh Schedule** to load sessions.")


# ===================================================================
# PAGE: Settings
# ===================================================================
def page_settings() -> None:
    st.header("Settings")

    st.subheader("Polling Interval")
    new_interval = st.slider(
        "Seconds between availability checks",
        min_value=MIN_POLL_INTERVAL_SEC,
        max_value=MAX_POLL_INTERVAL_SEC,
        value=scheduler.poll_interval,
        step=5,
    )
    if new_interval != scheduler.poll_interval:
        scheduler.poll_interval = new_interval
        st.success(f"Polling interval set to **{new_interval}s**.")

    st.subheader("Browser Mode")
    headless = st.toggle("Run browser in headless mode (invisible)", value=True)
    st.caption(
        "Turn this OFF to watch the browser perform actions — useful for debugging. "
        "Requires restarting the engine to take effect."
    )
    # We store this but it only takes effect on next engine start
    from app.config import settings as cfg
    cfg.HEADLESS = headless

    st.subheader("Watched Sessions")
    watched = monitor.all_watched()
    if watched:
        if st.button("Clear ALL watched sessions", type="secondary"):
            monitor.unwatch_all()
            st.success("All watches cleared.")
            st.rerun()
    else:
        st.caption("No sessions being watched.")

    st.subheader("About")
    st.markdown(
        """
        **Pickleball Auto-Register** automates session booking on the
        Lifetime Fitness member portal using Playwright browser automation.

        - Credentials are encrypted at rest (AES-128 via Fernet).
        - The automation runs in a background thread so this UI stays responsive.
        - If Lifetime changes their website, update the selectors in
          `app/config/selectors.py`.
        """
    )


# ===================================================================
# Router
# ===================================================================
if page == "Dashboard":
    page_dashboard()
elif page == "Accounts":
    page_accounts()
elif page == "Schedule":
    page_schedule()
elif page == "Settings":
    page_settings()
