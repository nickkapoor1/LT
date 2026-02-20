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
from app.backend.rules import WatchRule, load_rules, save_rules, add_rule, remove_rule
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
        active_rules = [r for r in load_rules() if r.enabled]
        if not accounts:
            st.sidebar.error("Add at least one account first.")
        elif not pending and not active_rules:
            st.sidebar.error("Add sessions to your watchlist or create an auto-watch rule first.")
        else:
            scheduler.start()
            st.rerun()

_active_rules = sum(1 for r in load_rules() if r.enabled)
st.sidebar.caption(
    "Polling: **{}s** | {} account(s) | {} watched | {} rule(s)".format(
        scheduler.poll_interval, len(load_accounts()), len(monitor.all_watched()), _active_rules,
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
                from datetime import datetime as _dt, timezone as _tz
                now = _dt.now(tz=ws.registration_opens_at_dt.tzinfo or _tz.utc)
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

    # -- My Registrations (pull from Lifetime API) --
    st.subheader("My Registrations")
    accounts = load_accounts()
    if not accounts:
        st.info("Add an account in **Accounts** to see your registrations.")
    else:
        # Date range: today → 14 days out
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        _today = _dt.now().date()
        _start = _today.strftime("%m/%d/%Y")
        _end = (_today + _td(days=14)).strftime("%m/%d/%Y")

        if st.button("Load My Registrations", key="load_registrations"):
            st.session_state["show_registrations"] = True

        if st.session_state.get("show_registrations"):
            all_reservations: list[tuple[dict, dict]] = []  # (account, reservation)
            for acct in accounts:
                if not acct.get("enabled", True):
                    continue
                sess = api.ensure_authenticated(acct["email"], acct["password"])
                if not sess or not sess.authenticated:
                    st.warning(f"Could not log in to {acct['email']}")
                    continue
                member_ids_str = ",".join(str(mid) for mid in sess.get_all_member_ids())
                reservations = api.get_my_reservations(sess, _start, _end, member_ids_str)
                for r in reservations:
                    all_reservations.append((acct, r))

            if not all_reservations:
                st.info("No upcoming registrations found in the next 14 days.")
            else:
                # Sort by start time (handle nested event structure)
                def _sort_start(item):
                    r = item[1]
                    return r.get("start") or r.get("event", {}).get("start", "") or r.get("startDate", "") or ""
                all_reservations.sort(key=_sort_start)
                st.caption(f"**{len(all_reservations)}** upcoming registration(s)")

                for acct, res in all_reservations:
                    # The API may nest event details under an "event" sub-object
                    ev = res.get("event", {}) if isinstance(res.get("event"), dict) else {}
                    r_title = res.get("title") or ev.get("title") or res.get("eventName") or ev.get("name") or "Unknown Event"
                    r_start = res.get("start") or ev.get("start") or res.get("startDate") or ""
                    r_end = res.get("end") or ev.get("end") or res.get("endDate") or ""
                    r_location = res.get("location") or res.get("club") or ev.get("location") or ev.get("club") or ""
                    r_event_id = res.get("eventId") or ev.get("id") or res.get("id") or ""
                    r_reg_id = res.get("regId") or res.get("registrationId") or res.get("reservationId") or ""
                    r_status = res.get("status") or res.get("registrationStatus") or ""
                    r_members = res.get("attendees") or res.get("members") or res.get("registrants") or ev.get("attendees") or []

                    # Format time
                    try:
                        s_dt = _dt.fromisoformat(r_start)
                        e_dt = _dt.fromisoformat(r_end)
                        time_str = f"{s_dt.strftime('%a %b %d, %I:%M %p')} – {e_dt.strftime('%I:%M %p')}"
                    except Exception:
                        time_str = f"{r_start} – {r_end}"

                    # Waitlist detection
                    is_waitlisted = any(
                        kw in (r_status or "").lower()
                        for kw in ["waitlist", "wait"]
                    )
                    icon = "🟡" if is_waitlisted else "✅"

                    rc1, rc2 = st.columns([6, 2])
                    rc1.markdown(f"{icon} **{r_title}**")
                    rc1.caption(f"{time_str} | {r_location}")
                    if r_members:
                        member_names = [m.get("name", m.get("firstName", "")) for m in r_members]
                        rc1.caption(f"Members: {', '.join(n for n in member_names if n)}")
                    if is_waitlisted:
                        rc1.caption("Status: **Waitlisted**")

                    # Cancel button
                    cancel_key = f"cancel_{r_reg_id or r_event_id}_{acct['email']}"
                    if r_reg_id:
                        if rc2.button("Cancel", key=cancel_key):
                            sess = api.ensure_authenticated(acct["email"], acct["password"])
                            if sess and sess.authenticated:
                                result = api.cancel_registration(sess, r_reg_id)
                                if result.success:
                                    st.success(f"Cancelled **{r_title}**")
                                    st.rerun()
                                else:
                                    st.error(f"Cancel failed: {result.message}")
                            else:
                                st.error("Could not authenticate to cancel.")

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

    # -- Helpers --
    import re as _re
    from collections import defaultdict as _defaultdict

    def _extract_level(title: str) -> str:
        m = _re.search(r'(\d\.\d\s*[-–]\s*\d\.\d\+?)', title)
        if m:
            return m.group(1).replace(" ", "")
        m = _re.search(r'(\d\.\d\+?)', title)
        if m:
            return m.group(1)
        return ""

    DRILL_KEYWORDS = ["drill", "clinic", "lesson", "training", "skills"]

    def _session_type(title: str) -> str:
        t = title.lower()
        if any(kw in t for kw in DRILL_KEYWORDS):
            return "Drill / Clinic"
        if "round robin" in t:
            return "Round Robin"
        if "league" in t:
            return "League"
        if "tournament" in t or "tourney" in t:
            return "Tournament"
        if "open play" in t or "open rec" in t:
            return "Open Play"
        return "Other"

    TYPE_COLORS = {
        "Drill / Clinic": "#e67e22",
        "Open Play": "#27ae60",
        "Round Robin": "#2980b9",
        "League": "#8e44ad",
        "Tournament": "#c0392b",
        "Other": "#7f8c8d",
    }

    # -- Filters --
    all_levels = sorted({_extract_level(ev.title) for ev in events} - {""})
    all_types = sorted({_session_type(ev.title) for ev in events})

    fc1, fc2 = st.columns(2)
    with fc1:
        selected_levels = st.multiselect(
            "Filter by level", options=all_levels, default=[],
            help="Leave empty to show all levels.",
        )
    with fc2:
        selected_types = st.multiselect(
            "Filter by session type", options=all_types, default=[],
            help="Leave empty to show all types.",
        )

    filtered_events = events
    if selected_levels:
        filtered_events = [ev for ev in filtered_events if _extract_level(ev.title) in selected_levels]
    if selected_types:
        filtered_events = [ev for ev in filtered_events if _session_type(ev.title) in selected_types]

    if len(filtered_events) != len(events):
        st.caption(f"Showing **{len(filtered_events)}** of {len(events)} events")

    st.divider()

    # ----------------------------------------------------------------
    # Calendar grid view: hours (Y) x days (X)
    # ----------------------------------------------------------------

    # Group events by date
    events_by_date: dict[str, list[Event]] = _defaultdict(list)
    for ev in filtered_events:
        try:
            date_key = datetime.fromisoformat(ev.start).strftime("%Y-%m-%d")
        except Exception:
            date_key = "unknown"
        events_by_date[date_key].append(ev)

    sorted_dates = sorted(events_by_date.keys())
    if not sorted_dates:
        st.info("No events match your filters.")
        return

    # Find the hour range across all events
    min_hour, max_hour = 23, 0
    for ev in filtered_events:
        try:
            h = datetime.fromisoformat(ev.start).hour
            min_hour = min(min_hour, h)
            max_hour = max(max_hour, h)
        except Exception:
            pass
    # Pad by 1 hour each direction for breathing room
    min_hour = max(0, min_hour - 1)
    max_hour = min(23, max_hour + 1)

    # Build events lookup: (date, hour) -> list of events
    grid: dict[tuple[str, int], list[Event]] = _defaultdict(list)
    for ev in filtered_events:
        try:
            dt = datetime.fromisoformat(ev.start)
            grid[(dt.strftime("%Y-%m-%d"), dt.hour)].append(ev)
        except Exception:
            pass

    # Render the calendar grid as HTML
    # Column headers = days, row headers = hours
    n_days = len(sorted_dates)

    # Build date labels
    date_labels = {}
    for d in sorted_dates:
        try:
            dt = datetime.fromisoformat(d)
            date_labels[d] = f"{dt.strftime('%a')}<br><b>{dt.strftime('%b %d')}</b>"
        except Exception:
            date_labels[d] = d

    # CSS + HTML for the calendar
    col_width = max(140, 700 // n_days)

    css = f"""
    <style>
    .cal-grid {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    .cal-grid th {{ background: #1a1a2e; color: #eee; padding: 8px 4px; text-align: center;
                    font-size: 13px; border: 1px solid #333; width: {col_width}px; }}
    .cal-grid th.hour-col {{ width: 60px; min-width: 60px; }}
    .cal-grid td {{ border: 1px solid #2a2a3e; vertical-align: top; padding: 2px; height: 30px; }}
    .cal-grid td.hour-label {{ background: #1a1a2e; color: #aaa; text-align: right; padding: 4px 8px;
                               font-size: 12px; font-weight: 600; white-space: nowrap; }}
    .cal-block {{ border-radius: 4px; padding: 3px 6px; margin: 1px 0; font-size: 11px;
                  color: #fff; cursor: default; line-height: 1.3; overflow: hidden; }}
    .cal-block .cal-time {{ font-weight: 700; }}
    .cal-block .cal-title {{ display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .cal-block .cal-level {{ opacity: 0.85; font-size: 10px; }}
    .cal-block .cal-club {{ opacity: 0.7; font-size: 10px; display: block; white-space: nowrap;
                            overflow: hidden; text-overflow: ellipsis; }}
    </style>
    """

    rows_html = ""
    for hour in range(min_hour, max_hour + 1):
        hour_label = datetime(2000, 1, 1, hour).strftime("%I %p").lstrip("0")
        cells = f'<td class="hour-label">{hour_label}</td>'
        for d in sorted_dates:
            cell_events = grid.get((d, hour), [])
            blocks = ""
            for ev in cell_events:
                stype = _session_type(ev.title)
                bg = TYPE_COLORS.get(stype, "#555")
                level = _extract_level(ev.title)
                level_html = f' <span class="cal-level">{level}</span>' if level else ""
                try:
                    t = datetime.fromisoformat(ev.start).strftime("%I:%M").lstrip("0")
                except Exception:
                    t = ""
                club_html = f'<span class="cal-club">{ev.club or ev.location}</span>' if (ev.club or ev.location) else ""
                blocks += (
                    f'<div class="cal-block" style="background:{bg}" title="{ev.title}">'
                    f'<span class="cal-time">{t}</span>{level_html} '
                    f'<span class="cal-title">{ev.title}</span>'
                    f'{club_html}</div>'
                )
            cells += f"<td>{blocks}</td>"
        rows_html += f"<tr>{cells}</tr>"

    header_cells = '<th class="hour-col"></th>' + "".join(
        f"<th>{date_labels[d]}</th>" for d in sorted_dates
    )
    table_html = f'{css}<table class="cal-grid"><thead><tr>{header_cells}</tr></thead><tbody>{rows_html}</tbody></table>'

    st.markdown(table_html, unsafe_allow_html=True)

    # Legend
    legend_items = " &nbsp; ".join(
        f'<span style="display:inline-block;width:12px;height:12px;background:{c};'
        f'border-radius:2px;vertical-align:middle"></span> {t}'
        for t, c in TYPE_COLORS.items() if t in all_types
    )
    if legend_items:
        st.markdown(f"<div style='font-size:12px;color:#aaa;margin-top:4px'>{legend_items}</div>", unsafe_allow_html=True)

    st.caption(f"{len(filtered_events)} session(s) across {len(sorted_dates)} day(s)")

    # ----------------------------------------------------------------
    # Session detail + actions (below the calendar)
    # ----------------------------------------------------------------
    st.divider()
    st.subheader("Session Actions")

    # Build a flat list for the selectbox
    event_options: dict[str, Event] = {}
    for ev in filtered_events:
        try:
            dt = datetime.fromisoformat(ev.start)
            time_str = dt.strftime("%a %b %d %I:%M %p").lstrip("0")
        except Exception:
            time_str = ev.start
        club_tag = f" @ {ev.club or ev.location}" if (ev.club or ev.location) else ""
        label = f"{time_str} — {ev.title}{club_tag}"
        event_options[label] = ev

    selected_label = st.selectbox("Select a session", options=list(event_options.keys()))
    if selected_label:
        ev = event_options[selected_label]
        st.markdown(f"**{ev.title}**")
        st.caption(f"{ev.display_time()} | {ev.location or ev.club or ''}")

        btn1, btn2, btn3, btn4 = st.columns(4)

        if btn1.button("Check Availability", key=f"check_{ev.event_id}", use_container_width=True):
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

                if reg.too_soon_minutes and ev.start:
                    try:
                        event_start_dt = datetime.fromisoformat(ev.start)
                        opens_at_dt = event_start_dt - timedelta(minutes=reg.too_soon_minutes)
                        from datetime import timezone as _tz2
                        now = datetime.now(tz=opens_at_dt.tzinfo or _tz2.utc)
                        time_until = (opens_at_dt - now).total_seconds()
                        if time_until > 0:
                            countdown = scheduler._format_countdown(time_until)
                            st.info(
                                f"Registration opens **{opens_at_dt.strftime('%a %b %d, %I:%M %p')}** "
                                f"(in {countdown})"
                            )
                    except Exception:
                        pass

                if reg.register_cta_text:
                    st.caption(f"CTA: {reg.register_cta_text} {'(disabled)' if reg.register_disabled else ''}")

                for m in reg.unregistered_members:
                    st.caption(f"Eligible: {m.get('name')} (ID {m.get('id')})")
                for m in reg.registered_members:
                    st.caption(f"Already registered: {m.get('name')}")

                if reg.unregistered_members or reg.registered_members:
                    api.discover_members(session, ev.event_id)
            else:
                st.error("Could not fetch availability.")

        if btn2.button("Add to Watchlist", key=f"watch_{ev.event_id}", use_container_width=True):
            if not selected_member_ids:
                st.error("Select at least one member above.")
            else:
                monitor.watch(ev, selected_email, [int(mid) for mid in selected_member_ids])
                names = selected_member_labels if selected_member_labels else selected_member_ids
                st.success(f"Watching **{ev.title}** for {names}")

        if btn3.button("Register NOW", key=f"reg_{ev.event_id}", use_container_width=True):
            if not selected_member_ids:
                st.error("Select at least one member above.")
            else:
                with st.spinner("Registering …"):
                    try:
                        if not session or not session.authenticated or session.is_expired():
                            session = api.ensure_authenticated(acct["email"], acct["password"])
                        if not session or not session.authenticated:
                            st.error("Login failed — check credentials in Accounts tab.")
                        else:
                            member_ids_int = [int(mid) for mid in selected_member_ids]
                            result = api.register(session, ev.event_id, member_ids_int)
                            if result.success:
                                verification = api.verify_registration(session, ev.event_id, member_ids_int)
                                if verification == "waitlisted":
                                    st.warning("WAITLISTED — session is full.")
                                else:
                                    st.success(f"CONFIRMED! {result.message}")
                            elif result.waitlisted:
                                st.warning(f"WAITLISTED — {result.message}")
                            else:
                                st.error(f"Failed: {result.message}")
                    except Exception as exc:
                        st.error(f"Registration error: {exc}")

        if btn4.button("Auto-Register (Snipe)", key=f"snipe_{ev.event_id}", use_container_width=True):
            if not selected_member_ids:
                st.error("Select at least one member above.")
            else:
                member_ids_int = [int(mid) for mid in selected_member_ids]
                ws = monitor.watch(ev, selected_email, member_ids_int)

                reg = api.get_event_registration(session, ev.event_id)
                if reg and reg.too_soon_minutes and ev.start:
                    try:
                        from datetime import datetime as _dt, timezone as _tz
                        event_start = _dt.fromisoformat(ev.start)
                        opens_at = event_start - timedelta(minutes=reg.too_soon_minutes)
                        ws.registration_opens_at_dt = opens_at
                        ws.registration_opens_at_display = opens_at.strftime("%a %b %d, %I:%M %p")
                        now = _dt.now(tz=opens_at.tzinfo or _tz.utc)
                        delta = (opens_at - now).total_seconds()
                        if delta > 0:
                            countdown = scheduler._format_countdown(delta)
                            st.success(
                                f"SNIPE QUEUED — auto-register at **{ws.registration_opens_at_display}** "
                                f"(in {countdown}). Start the engine to activate."
                            )
                        else:
                            st.success("Queued — registration is already open! Start the engine.")
                    except Exception:
                        st.success("Queued. Start the engine to activate.")
                elif reg and not reg.register_disabled:
                    st.success("Registration is OPEN — start the engine to register immediately!")
                else:
                    st.success("Queued. Start the engine to activate.")


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

    # -- Auto-Watch Rules (Presets) --
    st.subheader("Auto-Watch Rules")
    st.caption(
        "Define rules to automatically watch matching events. "
        "The engine scans for new events every 5 minutes and queues matches for snipe registration."
    )

    rules = load_rules()

    # Show existing rules
    if rules:
        for idx, rule in enumerate(rules):
            with st.expander(
                f"{'ON' if rule.enabled else 'OFF'} — {rule.name}",
                expanded=False,
            ):
                st.markdown(f"**Account:** {rule.account_email}")
                st.markdown(f"**Clubs:** {', '.join(rule.clubs) if rule.clubs else 'Any'}")
                st.markdown(f"**Session types:** {', '.join(rule.session_types) if rule.session_types else 'Any'}")
                st.markdown(f"**Min level:** {rule.min_level if rule.min_level > 0 else 'Any'}")
                st.markdown(f"**Time window:** {rule.time_earliest} – {rule.time_latest}")
                if rule.title_contains:
                    st.markdown(f"**Title keywords:** {', '.join(rule.title_contains)}")
                if rule.days_of_week:
                    day_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
                    st.markdown(f"**Days:** {', '.join(day_names[d] for d in rule.days_of_week if d in day_names)}")
                st.markdown(f"**Members:** {rule.member_ids if rule.member_ids else 'All on account'}")

                rc1, rc2 = st.columns(2)
                with rc1:
                    new_enabled = st.checkbox("Enabled", value=rule.enabled, key=f"rule_en_{idx}")
                    if new_enabled != rule.enabled:
                        rules[idx].enabled = new_enabled
                        save_rules(rules)
                        st.rerun()
                with rc2:
                    if st.button("Delete", key=f"rule_del_{idx}"):
                        remove_rule(idx)
                        st.rerun()
    else:
        st.info("No rules yet. Add one below.")

    # Add new rule form
    with st.expander("Add New Rule", expanded=not rules):
        accounts = load_accounts()
        enabled_accounts = [a for a in accounts if a.get("enabled", True)]

        if not enabled_accounts:
            st.warning("Add an account first.")
        else:
            r_name = st.text_input("Rule name", placeholder="e.g. BYO Partner Drill 8pm Penn")

            r_email = st.selectbox("Account", [a["email"] for a in enabled_accounts], key="rule_acct")

            r_clubs = st.multiselect(
                "Clubs",
                options=list(CLUBS.keys()),
                default=[],
                help="Which clubs to search. Leave empty for all clubs in the account.",
                key="rule_clubs",
            )

            SESSION_TYPE_OPTIONS = ["Drill / Clinic", "Open Play", "Round Robin", "League", "Tournament", "Other"]
            r_types = st.multiselect(
                "Session types",
                options=SESSION_TYPE_OPTIONS,
                default=[],
                help="Leave empty for all types.",
                key="rule_types",
            )

            r_min_level = st.number_input(
                "Minimum skill level (0 = any)",
                min_value=0.0, max_value=7.0, value=0.0, step=0.5,
                key="rule_level",
            )

            tc1, tc2 = st.columns(2)
            with tc1:
                r_earliest = st.time_input("Earliest start time", value=datetime.strptime("00:00", "%H:%M").time(), key="rule_t1")
            with tc2:
                r_latest = st.time_input("Latest start time", value=datetime.strptime("23:59", "%H:%M").time(), key="rule_t2")

            r_keywords_str = st.text_input(
                "Title keywords (comma-separated, optional)",
                placeholder="e.g. bring your own partner, BYO",
                key="rule_kw",
            )
            r_keywords = [kw.strip() for kw in r_keywords_str.split(",") if kw.strip()] if r_keywords_str else []

            DAY_OPTIONS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
            r_days_labels = st.multiselect(
                "Days of week (leave empty for all)",
                options=list(DAY_OPTIONS.keys()),
                default=[],
                key="rule_days",
            )
            r_days = [DAY_OPTIONS[d] for d in r_days_labels]

            # Get member IDs for selected account
            r_member_ids: list[int] = []
            rule_sess = api.get_session(r_email)
            if rule_sess and rule_sess.members:
                member_opts = {f"{m.display_name()} (ID {m.member_id})": m.member_id for m in rule_sess.members}
                r_member_labels = st.multiselect(
                    "Members to register (leave empty for all)",
                    options=list(member_opts.keys()),
                    default=[],
                    key="rule_members",
                )
                r_member_ids = [member_opts[lbl] for lbl in r_member_labels]
            else:
                st.caption("Log in first (Load Schedule) to see members, or leave empty for all.")

            if st.button("Save Rule", type="primary", key="save_rule"):
                if not r_name:
                    st.error("Give the rule a name.")
                elif not r_clubs:
                    st.error("Select at least one club.")
                else:
                    new_rule = WatchRule(
                        name=r_name,
                        account_email=r_email,
                        member_ids=r_member_ids,
                        clubs=r_clubs,
                        session_types=r_types,
                        min_level=r_min_level,
                        time_earliest=r_earliest.strftime("%H:%M"),
                        time_latest=r_latest.strftime("%H:%M"),
                        title_contains=r_keywords,
                        days_of_week=r_days,
                        enabled=True,
                    )
                    add_rule(new_rule)
                    st.success(f"Rule **{r_name}** saved!")
                    st.rerun()

    st.divider()

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
        2. **Set up auto-watch rules** (above) for sessions you always want.
        3. Or **manually add events** from the Schedule tab.
        4. **Start the engine** — it polls the API every few seconds.
        5. The engine **auto-scans** for events matching your rules every 5 minutes.
        6. The moment registration opens, it fires the register call instantly.
        7. You'll get a **notification** (desktop/email) when it succeeds!

        **Auto-watch rules** let you define presets like "all 5.0+ drills at Penn & Sky"
        — the engine finds matching events and queues them automatically.

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
