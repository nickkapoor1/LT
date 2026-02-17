"""
Quick one-off registration for a specific event.
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
print()

# Fetch schedule for PENN 1 covering Wednesday Feb 18
start = "02/18/2026"
end = "02/18/2026"

print("Fetching PENN 1 pickleball schedule for Wed Feb 18…")
events = api.get_events(session, "PENN 1", start, end)

if not events:
    print("No pickleball events found for that date.")
    sys.exit(1)

# Find the 4.75 session at 2 PM
target_event = None
for ev in events:
    is_475 = "4.75" in ev.title.lower() or "4.75" in ev.description.lower()
    try:
        ev_start = datetime.fromisoformat(ev.start)
        is_2pm = ev_start.hour == 14
    except Exception:
        is_2pm = False

    print(f"  Found: {ev.title} | {ev.display_time()} | ID: {ev.event_id}")
    if is_475 and is_2pm:
        target_event = ev
        print(f"  ^^^ THIS IS THE TARGET")

if not target_event:
    print()
    print("Could not find a 4.75 session at 2 PM. Listing all events above.")
    print("Please check the titles and times and let me know which one to register for.")
    sys.exit(1)

print()
print(f"Target event: {target_event.title}")
print(f"  Time: {target_event.display_time()}")
print(f"  Event ID: {target_event.event_id}")
print()

# Get registration details to find YOUR member ID (not Christina)
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

    # Find YOUR member ID - the one that is NOT Christina
    your_member_id = None
    for m in reg.unregistered_members:
        name = m.get("name", "").lower()
        if "christina" not in name:
            your_member_id = m.get("id")
            print(f"Registering: {m.get('name')} (ID {your_member_id})")
            break

    if your_member_id is None:
        # Fallback to account's main member ID
        your_member_id = session.member_id
        print(f"Using account member ID: {your_member_id}")

    # Attempt registration
    print()
    print("=" * 60)
    print("ATTEMPTING REGISTRATION…")
    print("=" * 60)
    result = api.register(session, target_event.event_id, [int(your_member_id)])
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
