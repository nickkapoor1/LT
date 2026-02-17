"""
Direct runner — fetches schedule and shows upcoming pickleball events,
then displays registration status for each.

Usage:
    python run_now.py                           # uses first stored account
    python run_now.py --email user@example.com  # uses specific stored account
    python run_now.py --days 7                  # look ahead N days (default 14)
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.backend.api_client import LifetimeAPI
from app.backend.crypto import load_accounts


def get_credentials(args) -> tuple[str, str, list[str]]:
    """Resolve credentials from CLI args or encrypted store. Returns (email, password, clubs)."""
    accounts = load_accounts()

    if args.email and args.password:
        clubs = [args.club] if args.club else ["PENN 1", "Sky (Manhattan)"]
        return args.email, args.password, clubs

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

    clubs = [args.club] if args.club else [acct.get("club_name", "PENN 1")]
    return acct["email"], acct["password"], clubs


def main():
    parser = argparse.ArgumentParser(description="View upcoming pickleball schedule")
    parser.add_argument("--email", help="Lifetime account email (uses stored account if omitted)")
    parser.add_argument("--password", help="Lifetime account password")
    parser.add_argument("--club", default="", help="Club name (default: account's club)")
    parser.add_argument("--days", type=int, default=14, help="Days to look ahead (default 14)")
    args = parser.parse_args()

    email, password, clubs = get_credentials(args)

    api = LifetimeAPI()

    # Login
    print("=" * 60)
    print("Logging in …")
    session = api.login(email, password)
    if not session.authenticated:
        print("LOGIN FAILED")
        sys.exit(1)

    print(f"Logged in as: {session.member_name} (member ID {session.member_id})")
    print(f"SSO ID: {session.sso_id}")
    print()

    # Fetch schedule
    today = datetime.now()
    start = today.strftime("%m/%d/%Y")
    end = (today + timedelta(days=args.days)).strftime("%m/%d/%Y")

    for club_name in clubs:
        print("=" * 60)
        print(f"PICKLEBALL SCHEDULE — {club_name}")
        print(f"Date range: {start} to {end}")
        print("=" * 60)

        events = api.get_events(session, club_name, start, end)

        if not events:
            print("  No pickleball events found.\n")
            continue

        for i, ev in enumerate(events, 1):
            print(f"\n  [{i}] {ev.title}")
            print(f"      {ev.display_time()}")
            print(f"      {ev.location}")
            print(f"      Event ID: {ev.event_id}")

            # Check registration
            reg = api.get_event_registration(session, ev.event_id)
            if reg:
                if reg.has_spots:
                    print(f"      STATUS: {reg.remaining_spots} spots OPEN")
                elif reg.has_waitlist:
                    print(f"      STATUS: FULL (waitlist: {reg.total_waitlisted})")
                else:
                    print(f"      STATUS: Closed")

                if reg.registration_opens_at:
                    print(f"      OPENS: {reg.registration_opens_at}")

                if reg.register_disabled:
                    print(f"      CTA: {reg.register_cta_text} (disabled)")
                else:
                    print(f"      CTA: {reg.register_cta_text}")

                for m in reg.registered_members:
                    print(f"      REGISTERED: {m.get('name')} (ID {m.get('id')})")
                for m in reg.unregistered_members:
                    print(f"      ELIGIBLE:   {m.get('name')} (ID {m.get('id')})")

        print()

    print("=" * 60)
    print("Done! To auto-register, run: streamlit run main.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
