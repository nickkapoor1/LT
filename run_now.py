"""
Direct runner — fetches schedule and shows upcoming pickleball events
for PENN 1 and Sky (Manhattan), then lets you add them to auto-registration.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.backend.api_client import LifetimeAPI

api = LifetimeAPI()

# Login
print("=" * 60)
print("Logging in …")
session = api.login("nickkapoor26@gmail.com", "Abigail911")
if not session.authenticated:
    print("LOGIN FAILED")
    sys.exit(1)

print(f"Logged in as: {session.member_name} (member ID {session.member_id})")
print(f"SSO ID: {session.sso_id}")
print()

# Fetch schedule for both clubs
today = datetime.now()
start = today.strftime("%m/%d/%Y")
end = (today + timedelta(days=14)).strftime("%m/%d/%Y")

for club_name in ["PENN 1", "Sky (Manhattan)"]:
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
