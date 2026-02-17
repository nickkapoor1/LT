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

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backend.api_client import LifetimeAPI, Event
from app.backend.crypto import add_account, load_accounts, remove_account, save_accounts
from app.backend.monitor import Monitor
from app.backend.scheduler import Scheduler
from app.config.settings import CLUBS, DEFAULT_POLL_INTERVAL_SEC, MIN_POLL_INTERVAL_SEC, MAX_POLL_INTERVAL_SEC

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Pickleball Sniper", page_icon="🏓", layout="wide")

# ---------------------------------------------------------------------------
# Persistent state
# ---------------------------------------------------------------------------
if "monitor" not in st.session_state:
    st.session_state.monitor = Monitor()
if "scheduler" not in st.session_state:
    st.session_state.scheduler = Scheduler(st.session_state.monitor)
if "api" not in st.session_state:
    st.session_state.api = LifetimeAPI()
if "events" not in st.session_state:
    st.session_state.events: list[Event] = []

monitor: Monitor = st.session_state.monitor
scheduler: Scheduler = st.session_state.scheduler
api: LifetimeAPI = st.session_state.api

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("Pickleball Sniper")
page = st.sidebar.radio("Navigate", ["Dashboard", "Accounts", "Schedule", "Settings"])

st.sidebar.markdown("---")
if scheduler.running:
    st.sidebar.success("Engine RUNNING")
    if st.sidebar.button("Stop Engine", type="primary", use_container_width=True):
        scheduler.stop()
        st.rerun()
else:
    st.sidebar.warning("Engine STOPPED")
    if st.sidebar.button("Start Engine", type="primary", use_container_width=True):
        accounts = load_accounts()
        pending = monitor.pending()
        if not accounts:
            st.sidebar.error("Add at least one account first.")
        elif not pending:
            st.sidebar.error("Add sessions to your watchlist first.")
        else:
            scheduler.start()
            st.rerun()

st.sidebar.caption(
    "Polling: **{}s** | {} account(s) | {} watched".format(
        scheduler.poll_interval, len(load_accounts()), len(monitor.all_watched()),
    )
)


# ===================================================================
# DASHBOARD
# ===================================================================
def page_dashboard():
    st.header("Dashboard")

    watched = monitor.all_watched()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Accounts", len(load_accounts()))
    c2.metric("Watching", len(watched))
    c3.metric("Registered", sum(1 for w in watched if w.registration_status == "registered"))
    c4.metric("Engine", "Running" if scheduler.running else "Stopped")

    # Watched events
    st.subheader("Watched Events")
    if not watched:
        st.info("Go to **Schedule** to find events and add them to your watchlist.")
    else:
        for ws in watched:
            icon = {"pending": "🔵", "registered": "✅", "waitlisted": "🟡", "failed": "🔴"}.get(
                ws.registration_status, "⚪"
            )
            col1, col2, col3 = st.columns([5, 3, 2])
            col1.markdown(f"{icon} **{ws.event.title}**")
            col1.caption(f"{ws.event.display_time()} | {ws.event.location}")
            col2.write(f"Account: {ws.account_email}")
            col2.write(f"Status: **{ws.registration_status}** | Last: {ws.last_status or '—'} ({ws.last_checked or 'never'})")
            col2.write(f"Members: {ws.member_ids}")

            btn_col1, btn_col2 = col3.columns(2)
            if ws.registration_status in ("failed", "waitlisted"):
                if btn_col1.button("Retry", key=f"retry_{ws.key}"):
                    monitor.reset_status(ws)
                    st.rerun()
            if btn_col2.button("Remove", key=f"rm_{ws.key}"):
                monitor.unwatch(ws.event.event_id, ws.account_email)
                st.rerun()
            st.divider()

    # Activity log
    st.subheader("Activity Log")
    log_entries = scheduler.get_recent_log(40)
    if not log_entries:
        st.caption("No activity yet.")
    else:
        for entry in log_entries:
            color = {"success": "green", "error": "red", "warning": "orange"}.get(entry.level, "gray")
            st.markdown(
                f"<span style='color:{color};font-family:monospace'>[{entry.timestamp}]</span> "
                f"**{entry.account}** — {entry.message}",
                unsafe_allow_html=True,
            )

    if scheduler.running:
        st.caption("Auto-refreshing …")
        import time; time.sleep(5)
        st.rerun()


# ===================================================================
# ACCOUNTS
# ===================================================================
def page_accounts():
    st.header("Manage Accounts")

    accounts = load_accounts()
    if accounts:
        st.subheader(f"Stored Accounts ({len(accounts)})")
        for i, acct in enumerate(accounts):
            enabled = acct.get("enabled", True)
            with st.expander(f"{'✅' if enabled else '⏸️'}  {acct['email']}  —  {acct.get('club_name', '')}"):
                st.write(f"**Club:** {acct.get('club_name', 'N/A')} (ID {acct.get('club_id', '?')})")
                st.write(f"**Enabled:** {enabled}")

                # Test login
                if st.button("Test Login", key=f"test_{i}"):
                    with st.spinner("Logging in …"):
                        session = api.login(acct["email"], acct["password"])
                        if session.authenticated:
                            st.success(f"Login OK! Member: {session.member_name} (ID {session.member_id})")
                        else:
                            st.error("Login failed — check credentials.")

                c1, c2, c3 = st.columns(3)
                if enabled:
                    if c1.button("Disable", key=f"dis_{i}"):
                        acct["enabled"] = False
                        save_accounts(accounts)
                        st.rerun()
                else:
                    if c1.button("Enable", key=f"en_{i}"):
                        acct["enabled"] = True
                        save_accounts(accounts)
                        st.rerun()
                if c2.button("Remove", key=f"del_{i}"):
                    remove_account(acct["email"])
                    st.rerun()
    else:
        st.info("No accounts yet.")

    # Add account form
    st.subheader("Add New Account")
    with st.form("add_account", clear_on_submit=True):
        email = st.text_input("Lifetime Email")
        password = st.text_input("Password", type="password")
        club_name = st.selectbox("Club", list(CLUBS.keys()), index=0)
        if st.form_submit_button("Add Account"):
            if not email or not password:
                st.error("Email and password required.")
            else:
                club_id = CLUBS[club_name]
                add_account(email, password, str(club_id), club_name, [])
                st.success(f"**{email}** added for **{club_name}**.")
                st.rerun()


# ===================================================================
# SCHEDULE
# ===================================================================
def page_schedule():
    st.header("Pickleball Schedule")

    accounts = load_accounts()
    if not accounts:
        st.warning("Add an account first.")
        return

    enabled = [a for a in accounts if a.get("enabled", True)]
    if not enabled:
        st.warning("No enabled accounts.")
        return

    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
    with col1:
        selected_email = st.selectbox("Account", [a["email"] for a in enabled])
    acct = next(a for a in enabled if a["email"] == selected_email)

    with col2:
        start = st.date_input("From", value=datetime.now().date())
    with col3:
        end = st.date_input("To", value=datetime.now().date() + timedelta(days=7))
    with col4:
        fetch = st.button("Load Schedule", use_container_width=True)

    if fetch:
        with st.spinner("Fetching schedule from Lifetime API …"):
            session = api.ensure_authenticated(acct["email"], acct["password"])
            if not session.authenticated:
                st.error("Login failed.")
                return
            events = api.get_events(
                session,
                acct["club_name"],
                start.strftime("%m/%d/%Y"),
                end.strftime("%m/%d/%Y"),
            )
            st.session_state.events = events
            st.session_state.schedule_email = selected_email
            st.success(f"Found **{len(events)}** pickleball events.")

    events: list[Event] = st.session_state.get("events", [])
    if not events:
        st.caption("Click **Load Schedule** to fetch events.")
        return

    st.subheader(f"Events ({len(events)})")

    # Get member IDs for registration selection
    session = api.get_session(selected_email)
    all_member_ids: list[int] = []
    if session and session.member_id:
        all_member_ids = [session.member_id]

    for i, ev in enumerate(events):
        col1, col2 = st.columns([5, 3])
        col1.markdown(f"**{ev.title}**")
        col1.caption(f"{ev.display_time()}")
        col1.caption(f"{ev.location}")

        with col2:
            # Check registration status
            if st.button("Check Availability", key=f"check_{i}"):
                reg = api.get_event_registration(session, ev.event_id)
                if reg:
                    if reg.has_spots:
                        st.success(f"{reg.remaining_spots} spot(s) available!")
                    elif reg.has_waitlist:
                        st.warning(f"Full — waitlist ({reg.total_waitlisted} waiting)")
                    else:
                        st.error("Closed")

                    if reg.registration_opens_at:
                        st.info(reg.registration_opens_at)

                    # Show member eligibility
                    for m in reg.unregistered_members:
                        st.caption(f"Eligible: {m.get('name')} (ID {m.get('id')})")
                    for m in reg.registered_members:
                        st.caption(f"Already registered: {m.get('name')}")

            if st.button("Add to Watchlist", key=f"watch_{i}"):
                # Determine members to register
                if session:
                    reg = api.get_event_registration(session, ev.event_id)
                    if reg and reg.unregistered_members:
                        member_ids = [m["id"] for m in reg.unregistered_members]
                    else:
                        member_ids = [session.member_id] if session.member_id else []
                else:
                    member_ids = []

                if member_ids:
                    monitor.watch(ev, selected_email, member_ids)
                    st.success(f"Watching **{ev.title}** for member(s) {member_ids}")
                else:
                    st.error("No eligible members found.")

            if st.button("Register NOW", key=f"reg_{i}"):
                if not session:
                    st.error("Not logged in.")
                else:
                    reg = api.get_event_registration(session, ev.event_id)
                    if reg and reg.unregistered_members:
                        member_ids = [m["id"] for m in reg.unregistered_members]
                    else:
                        member_ids = [session.member_id]

                    with st.spinner("Registering …"):
                        result = api.register(session, ev.event_id, member_ids)
                        if result.success:
                            st.success(f"Registered! {result.message}")
                        elif result.waitlisted:
                            st.warning(f"Waitlisted: {result.message}")
                        else:
                            st.error(f"Failed: {result.message}")

        st.divider()


# ===================================================================
# SETTINGS
# ===================================================================
def page_settings():
    st.header("Settings")

    st.subheader("Polling Interval")
    new_interval = st.slider(
        "Seconds between checks",
        min_value=MIN_POLL_INTERVAL_SEC,
        max_value=MAX_POLL_INTERVAL_SEC,
        value=scheduler.poll_interval,
        step=1,
    )
    if new_interval != scheduler.poll_interval:
        scheduler.poll_interval = new_interval
        st.success(f"Polling interval: **{new_interval}s**")

    st.caption(
        "For sniping (registering exactly when slots open), use 1-5 seconds. "
        "For passive monitoring, use 30-60 seconds."
    )

    st.subheader("Watchlist")
    watched = monitor.all_watched()
    if watched:
        st.write(f"{len(watched)} event(s) being watched.")
        if st.button("Clear ALL watched events"):
            monitor.unwatch_all()
            st.success("Cleared.")
            st.rerun()
    else:
        st.caption("No events being watched.")

    st.subheader("How It Works")
    st.markdown(
        """
        1. **Add your account** in the Accounts tab.
        2. **Load the schedule** to see upcoming pickleball events.
        3. **Add events to your watchlist** — pick the ones you want.
        4. **Start the engine** — it polls the API every few seconds.
        5. The moment registration opens, it fires the register call instantly.

        This uses Lifetime's internal API directly — no browser automation.
        Registration calls complete in milliseconds, not seconds.
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
