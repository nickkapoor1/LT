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

from app.backend.api_client import LifetimeAPI, Event, MemberInfo
from app.backend.crypto import add_account, load_accounts, remove_account, save_accounts
from app.backend.monitor import Monitor
from app.backend.notifier import load_notify_settings, save_notify_settings
from app.backend.scheduler import Scheduler
from app.config.settings import CLUBS, CLUB_REGIONS, DEFAULT_POLL_INTERVAL_SEC, MIN_POLL_INTERVAL_SEC, MAX_POLL_INTERVAL_SEC

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
import subprocess
def _get_git_info() -> str:
    try:
        sha = subprocess.check_output(["git", "log", "-1", "--format=%h"], cwd=str(PROJECT_ROOT), stderr=subprocess.DEVNULL).decode().strip()
        date = subprocess.check_output(["git", "log", "-1", "--format=%ci"], cwd=str(PROJECT_ROOT), stderr=subprocess.DEVNULL).decode().strip()[:16]
        return f"v1.0 | {date} ({sha})"
    except Exception:
        return "v1.0"

st.sidebar.title("Pickleball Sniper")
st.sidebar.caption(_get_git_info())
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

            # Show registration open time if known
            if ws.registration_opens_at_dt:
                from datetime import datetime as _dt
                now = _dt.now()
                seconds_until = (ws.registration_opens_at_dt - now).total_seconds()
                if seconds_until > 0:
                    countdown = scheduler._format_countdown(seconds_until)
                    col1.markdown(f"⏰ **Registration opens:** {ws.registration_opens_at_display} (in {countdown})")
                    # Show if snipe is scheduled
                    if ws.key in scheduler._snipe_threads:
                        col1.markdown("🎯 **Snipe thread active** — will fire at exact open time")
                else:
                    col1.markdown(f"⏰ Registration opened at {ws.registration_opens_at_display}")

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
                    with st.spinner("Logging in and discovering members …"):
                        test_session = api.login(acct["email"], acct["password"])
                        if test_session.authenticated:
                            st.success(f"Login OK! Primary: {test_session.member_name} (ID {test_session.member_id})")

                            # Discover all members via an event check
                            club = acct.get("club_name", "PENN 1")
                            today = datetime.now().strftime("%m/%d/%Y")
                            week = (datetime.now() + timedelta(days=7)).strftime("%m/%d/%Y")
                            test_events = api.get_events(test_session, club, today, week)
                            if test_events:
                                api.discover_members(test_session, test_events[0].event_id)

                            if test_session.members:
                                st.info(f"**{len(test_session.members)} member(s) on account:**")
                                for m in test_session.members:
                                    st.write(f"  - {m.display_name()} (ID {m.member_id})")
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

    # -- Account selection --
    selected_email = st.selectbox("Account", [a["email"] for a in enabled])
    acct = next(a for a in enabled if a["email"] == selected_email)

    # -- Multi-club selection with region presets --
    st.subheader("Clubs")

    # Region quick-select buttons
    region_cols = st.columns(len(CLUB_REGIONS))
    preset_clubs: list[str] = []
    for idx, (region, clubs_in_region) in enumerate(CLUB_REGIONS.items()):
        with region_cols[idx]:
            if st.button(region, key=f"region_{region}", use_container_width=True):
                st.session_state["selected_clubs"] = clubs_in_region

    # Initialize selected clubs from session state or account default
    default_clubs = st.session_state.get("selected_clubs", [acct.get("club_name", "PENN 1")])

    selected_clubs = st.multiselect(
        "Select clubs to search",
        options=list(CLUBS.keys()),
        default=[c for c in default_clubs if c in CLUBS],
        help="Pick one or more clubs. Use the region buttons above for quick presets.",
    )
    st.session_state["selected_clubs"] = selected_clubs

    # -- Date range --
    dc1, dc2, dc3 = st.columns([2, 2, 1])
    with dc1:
        start = st.date_input("From", value=datetime.now().date())
    with dc2:
        end = st.date_input("To", value=datetime.now().date() + timedelta(days=7))
    with dc3:
        fetch = st.button("Load Schedule", use_container_width=True)

    # Ensure we have a session for this account
    session = api.get_session(selected_email)

    if fetch:
        if not selected_clubs:
            st.error("Select at least one club.")
            return

        with st.spinner(f"Fetching schedule from {len(selected_clubs)} club(s) …"):
            session = api.ensure_authenticated(acct["email"], acct["password"])
            if not session.authenticated:
                st.error("Login failed.")
                return

            all_events: list[Event] = []
            start_str = start.strftime("%m/%d/%Y")
            end_str = end.strftime("%m/%d/%Y")

            # Fetch from each selected club
            progress = st.progress(0, text="Loading …")
            for club_idx, club_name in enumerate(selected_clubs):
                progress.progress(
                    (club_idx + 1) / len(selected_clubs),
                    text=f"Fetching {club_name} …",
                )
                club_events = api.get_events(session, club_name, start_str, end_str)
                all_events.extend(club_events)
            progress.empty()

            # Sort by start time
            all_events.sort(key=lambda e: e.start)

            # Discover all members on the account using the first event
            if all_events:
                api.discover_members(session, all_events[0].event_id)

            st.session_state.events = all_events
            st.session_state.schedule_email = selected_email
            club_summary = ", ".join(selected_clubs) if len(selected_clubs) <= 3 else f"{len(selected_clubs)} clubs"
            st.success(f"Found **{len(all_events)}** pickleball events across {club_summary}.")
            if session.members:
                names = [f"{m.display_name()} (ID {m.member_id})" for m in session.members]
                st.info(f"Members on account: {', '.join(names)}")

    events: list[Event] = st.session_state.get("events", [])
    if not events:
        st.caption("Click **Load Schedule** to fetch events.")
        return

    # Re-authenticate if session was lost (Streamlit rerun)
    if not session or not session.authenticated:
        session = api.ensure_authenticated(acct["email"], acct["password"])

    # Auto-discover members if we only have the primary (family members missing)
    if session and session.authenticated and len(session.members) <= 1 and events:
        api.discover_members(session, events[0].event_id)

    # -- Account Members Section --
    if session and session.members and len(session.members) > 1:
        st.subheader("Account Members")
        member_cols = st.columns(len(session.members))
        for idx, m in enumerate(session.members):
            with member_cols[idx]:
                st.markdown(f"**{m.display_name()}**")
                st.caption(f"ID: {m.member_id} | {m.relationship or 'member'}")
    elif session and session.member_id:
        st.subheader("Account Members")
        st.write(f"**{session.member_name}** (ID {session.member_id})")
        if events:
            st.caption("Only primary member found. Discovering family members …")
            api.discover_members(session, events[0].event_id)
            if len(session.members) > 1:
                st.rerun()

    # -- Member Selection --
    st.subheader(f"Events ({len(events)})")

    # Build member options from session.members (populated by discover_members)
    member_options: dict[str, int] = {}
    if session and session.members:
        for m in session.members:
            member_options[f"{m.display_name()} (ID {m.member_id})"] = m.member_id
    elif session and session.member_id:
        member_options[f"{session.member_name} (ID {session.member_id})"] = session.member_id

    selected_member_labels: list[str] = []
    if member_options:
        selected_member_labels = st.multiselect(
            "Register these members",
            options=list(member_options.keys()),
            default=list(member_options.keys()),
            help="Choose which member(s) to register. Select one or both.",
        )
        selected_member_ids = [member_options[label] for label in selected_member_labels]
    else:
        selected_member_ids = []
        st.info("Log in to see available members. Click **Load Schedule** first.")

    st.divider()

    for i, ev in enumerate(events):
        col1, col2 = st.columns([5, 3])
        # Show club name for multi-club searches
        club_tag = f" — *{ev.club or ev.location}*" if ev.club or ev.location else ""
        col1.markdown(f"**{ev.title}**{club_tag}")
        col1.caption(f"{ev.display_time()}")
        if ev.location:
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

                    # Show computed exact open time
                    if reg.too_soon_minutes and ev.start:
                        try:
                            event_start_dt = datetime.fromisoformat(ev.start)
                            opens_at_dt = event_start_dt - timedelta(minutes=reg.too_soon_minutes)
                            now = datetime.now()
                            time_until = opens_at_dt - now
                            if time_until.total_seconds() > 0:
                                countdown = scheduler._format_countdown(time_until.total_seconds())
                                st.info(
                                    f"Registration opens **{opens_at_dt.strftime('%a %b %d, %I:%M %p')}** "
                                    f"(in {countdown}). Add to watchlist — the engine will auto-register at the exact moment."
                                )
                        except Exception:
                            pass

                    if reg.register_cta_text:
                        st.caption(f"CTA: {reg.register_cta_text} {'(disabled)' if reg.register_disabled else ''}")

                    # Show member eligibility
                    for m in reg.unregistered_members:
                        st.caption(f"Eligible: {m.get('name')} (ID {m.get('id')})")
                    for m in reg.registered_members:
                        st.caption(f"Already registered: {m.get('name')}")

                    # Also update member list if we see new members
                    if reg.unregistered_members or reg.registered_members:
                        api.discover_members(session, ev.event_id)
                else:
                    st.error("Could not fetch availability. Try reloading the schedule.")

            if st.button("Add to Watchlist", key=f"watch_{i}"):
                if not selected_member_ids:
                    st.error("Select at least one member above.")
                else:
                    monitor.watch(ev, selected_email, [int(mid) for mid in selected_member_ids])
                    names = selected_member_labels if selected_member_labels else selected_member_ids
                    st.success(f"Watching **{ev.title}** for {names}")

            if st.button("Register NOW", key=f"reg_{i}"):
                if not selected_member_ids:
                    st.error("Select at least one member above.")
                else:
                    with st.spinner("Registering …"):
                        try:
                            # Re-authenticate if session is stale
                            if not session or not session.authenticated or session.is_expired():
                                session = api.ensure_authenticated(acct["email"], acct["password"])
                            if not session or not session.authenticated:
                                st.error("Login failed — check your credentials in the Accounts tab.")
                            else:
                                member_ids_int = [int(mid) for mid in selected_member_ids]
                                member_names = [l.split(" (ID")[0] for l in selected_member_labels] if selected_member_labels else member_ids_int
                                st.caption(f"Registering {member_names} …")
                                result = api.register(session, ev.event_id, member_ids_int)
                                if result.success:
                                    # Verify: is it actually registered or secretly waitlisted?
                                    verification = api.verify_registration(session, ev.event_id, member_ids_int)
                                    if verification == "waitlisted":
                                        st.warning(f"WAITLISTED — session is full. You're on the waitlist, not confirmed.")
                                    else:
                                        st.success(f"CONFIRMED — You are registered! {result.message}")
                                elif result.waitlisted:
                                    st.warning(f"WAITLISTED — session is full. {result.message}")
                                else:
                                    st.error(f"Failed: {result.message}")
                        except Exception as exc:
                            st.error(f"Registration error: {exc}")

            # Auto-Register (Snipe) — adds to watchlist with snipe scheduling
            if st.button("Auto-Register (Snipe)", key=f"snipe_{i}"):
                if not selected_member_ids:
                    st.error("Select at least one member above.")
                else:
                    member_ids_int = [int(mid) for mid in selected_member_ids]
                    ws = monitor.watch(ev, selected_email, member_ids_int)

                    # Pre-check: get registration timing info
                    reg = api.get_event_registration(session, ev.event_id)
                    if reg and reg.too_soon_minutes and ev.start:
                        try:
                            from datetime import datetime as _dt
                            event_start = _dt.fromisoformat(ev.start)
                            opens_at = event_start - timedelta(minutes=reg.too_soon_minutes)
                            ws.registration_opens_at_dt = opens_at
                            ws.registration_opens_at_display = opens_at.strftime("%a %b %d, %I:%M %p")
                            now = _dt.now()
                            delta = (opens_at - now).total_seconds()
                            if delta > 0:
                                countdown = scheduler._format_countdown(delta)
                                st.success(
                                    f"SNIPE QUEUED — Will auto-register at **{ws.registration_opens_at_display}** "
                                    f"(in {countdown}). Start the engine to activate."
                                )
                            else:
                                st.success("Added to auto-register queue. Start the engine — registration is already open!")
                        except Exception:
                            st.success("Added to auto-register queue. Start the engine to activate.")
                    elif reg and not reg.register_disabled:
                        st.success("Added to auto-register queue. Registration is OPEN — start the engine to register immediately!")
                    else:
                        st.success("Added to auto-register queue. Start the engine to activate.")

        st.divider()


# ===================================================================
# SETTINGS
# ===================================================================
def page_settings():
    st.header("Settings")

    # -- Polling --
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

    # -- Adaptive Polling --
    st.subheader("Adaptive Polling")
    adaptive = st.checkbox(
        "Auto-speed-up near registration open time",
        value=scheduler.adaptive_polling,
    )
    if adaptive != scheduler.adaptive_polling:
        scheduler.adaptive_polling = adaptive
        st.success(f"Adaptive polling: **{'ON' if adaptive else 'OFF'}**")

    st.caption(
        "When enabled, the engine automatically polls faster as registration "
        "windows approach — 1s when <1 min away, 2s when <5 min away."
    )

    # -- Notifications --
    st.subheader("Notifications")
    ns = load_notify_settings()

    col1, col2 = st.columns(2)
    with col1:
        ns.desktop_enabled = st.checkbox("Desktop notifications", value=ns.desktop_enabled)
    with col2:
        ns.email_enabled = st.checkbox("Email notifications", value=ns.email_enabled)

    if ns.email_enabled:
        st.markdown("**Email (SMTP) Settings**")
        ec1, ec2 = st.columns(2)
        with ec1:
            ns.smtp_server = st.text_input("SMTP server", value=ns.smtp_server)
            ns.smtp_port = st.number_input("SMTP port", value=ns.smtp_port, min_value=1, max_value=65535)
            ns.smtp_user = st.text_input("SMTP username / email", value=ns.smtp_user)
        with ec2:
            ns.smtp_password = st.text_input("SMTP password (app password)", value=ns.smtp_password, type="password")
            ns.notify_to_email = st.text_input("Send alerts to", value=ns.notify_to_email)

    st.markdown("**Notify me when:**")
    nc1, nc2, nc3, nc4 = st.columns(4)
    with nc1:
        ns.on_registration = st.checkbox("Registration succeeds", value=ns.on_registration)
    with nc2:
        ns.on_waitlist = st.checkbox("Added to waitlist", value=ns.on_waitlist)
    with nc3:
        ns.on_failure = st.checkbox("Registration fails", value=ns.on_failure)
    with nc4:
        ns.on_spots_open = st.checkbox("Spots open up", value=ns.on_spots_open)

    if st.button("Save Notification Settings"):
        save_notify_settings(ns)
        st.success("Notification settings saved!")

    # -- Watchlist --
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

    # -- How it works --
    st.subheader("How It Works")
    st.markdown(
        """
        1. **Add your account** in the Accounts tab.
        2. **Load the schedule** to see upcoming pickleball events.
        3. **Add events to your watchlist** — pick the ones you want.
        4. **Start the engine** — it polls the API every few seconds.
        5. The moment registration opens, it fires the register call instantly.
        6. You'll get a **notification** (desktop/email) when it succeeds!

        **Adaptive polling** automatically speeds up as registration windows
        approach — no need to manually adjust the interval.

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
