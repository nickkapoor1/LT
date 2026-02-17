"""
Quick one-off registration for a specific event.

Usage:
    python register_now.py                           # uses first stored account
    python register_now.py --email user@example.com  # uses specific stored account
    python register_now.py --email user@example.com --password secret  # manual login
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.backend.api_client import LifetimeAPI
from app.backend.crypto import load_accounts


def get_credentials(args) -> tuple[str, str, str]:
    """Resolve credentials from CLI args or encrypted store. Returns (email, password, club)."""
    if args.email and args.password:
        return args.email, args.password, args.club or "PENN 1"

    accounts = load_accounts()
    if not accounts:
        print("ERROR: No stored accounts found. Add one via 'streamlit run main.py' first,")
        print("       or pass --email and --password on the command line.")
        sys.exit(1)

    if args.email:
        acct = next((a for a in accounts if a["email"].lower() == args.email.lower()), None)
        if not acct:
            print(f"ERROR: Account '{args.email}' not found in stored accounts.")
            print(f"Available: {', '.join(a['email'] for a in accounts)}")
            sys.exit(1)
    else:
        acct = accounts[0]
        print(f"Using first stored account: {acct['email']}")

    return acct["email"], acct["password"], args.club or acct.get("club_name", "PENN 1")


def main():
    parser = argparse.ArgumentParser(description="Quick one-off pickleball registration")
    parser.add_argument("--email", help="Lifetime account email (uses stored account if omitted)")
    parser.add_argument("--password", help="Lifetime account password")
    parser.add_argument("--club", default="", help="Club name (default: account's club or PENN 1)")
    parser.add_argument("--date", default="", help="Date to search (MM/DD/YYYY, default: today)")
    parser.add_argument("--level", default="", help="Filter by level (e.g. '4.75', '3.5')")
    parser.add_argument("--hour", type=int, default=-1, help="Filter by start hour (24h, e.g. 14 for 2 PM)")
    args = parser.parse_args()

    email, password, club = get_credentials(args)

    api = LifetimeAPI()

    # Login
    print("=" * 60)
    print("Logging in …")
    session = api.login(email, password)
    if not session.authenticated:
        print("LOGIN FAILED")
        sys.exit(1)

    print(f"Logged in as: {session.member_name} (member ID {session.member_id})")
    print()

    # Determine date
    if args.date:
        search_date = args.date
    else:
        search_date = datetime.now().strftime("%m/%d/%Y")

    print(f"Fetching {club} pickleball schedule for {search_date}…")
    events = api.get_events(session, club, search_date, search_date)

    if not events:
        print("No pickleball events found for that date.")
        sys.exit(1)

    # Filter by level and/or hour
    target_event = None
    for ev in events:
        matches_level = True
        matches_hour = True

        if args.level:
            matches_level = args.level in ev.title.lower() or args.level in ev.description.lower()

        if args.hour >= 0:
            try:
                ev_start = datetime.fromisoformat(ev.start)
                matches_hour = ev_start.hour == args.hour
            except Exception:
                matches_hour = False

        print(f"  Found: {ev.title} | {ev.display_time()} | ID: {ev.event_id}")
        if matches_level and matches_hour:
            target_event = ev
            print(f"  ^^^ MATCH")

    if not target_event:
        print()
        if args.level or args.hour >= 0:
            print("No event matched your filters. Listing all events above.")
        else:
            # Use the last event listed as the default
            target_event = events[-1]
            print(f"Using last event: {target_event.title}")

    if not target_event:
        sys.exit(1)

    print()
    print(f"Target event: {target_event.title}")
    print(f"  Time: {target_event.display_time()}")
    print(f"  Event ID: {target_event.event_id}")
    print()

    # Get registration details
    reg = api.get_event_registration(session, target_event.event_id)
    if reg:
        print(f"  Spots open: {reg.has_spots} (remaining: {reg.remaining_spots})")
        print(f"  Waitlist: {reg.has_waitlist} (total waitlisted: {reg.total_waitlisted})")
        print(f"  CTA: {reg.register_cta_text} (disabled: {reg.register_disabled})")
        if reg.registration_opens_at:
            print(f"  Opens: {reg.registration_opens_at}")
        for m in reg.registered_members:
            print(f"  Already registered: {m.get('name')} (ID {m.get('id')})")
        for m in reg.unregistered_members:
            print(f"  Eligible: {m.get('name')} (ID {m.get('id')})")
        print()

        # Find eligible member IDs
        member_ids = [m.get("id") for m in reg.unregistered_members]
        if not member_ids:
            member_ids = [session.member_id]
            print(f"Using account member ID: {session.member_id}")
        else:
            print(f"Registering members: {[m.get('name') for m in reg.unregistered_members]}")

        # Attempt registration
        print()
        print("=" * 60)
        print("ATTEMPTING REGISTRATION…")
        print("=" * 60)
        result = api.register(session, target_event.event_id, [int(mid) for mid in member_ids])
        print()
        if result.success:
            print(f"SUCCESS! {result.message}")
        elif result.waitlisted:
            print(f"WAITLISTED: {result.message}")
        else:
            print(f"FAILED: {result.message}")
    else:
        print("Could not fetch registration details for this event.")
        sys.exit(1)


if __name__ == "__main__":
    main()
