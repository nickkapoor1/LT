"""Quick CLI snipe — bypass Streamlit, hit the API directly."""
import sys, time, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("snipe")

from datetime import datetime, timedelta
from app.backend.api_client import LifetimeAPI

EMAIL = "nickkapoor"
PASSWORD = "Lifetime123"
CLUB = "PENN 1"
TARGET_DAY = "Monday"
TARGET_HOUR_START = 8   # 8 AM
TARGET_HOUR_END = 8     # 8 AM hour
TARGET_KEYWORDS = ["drill", "bring your own partner"]

RAPID_ATTEMPTS = 15
ATTEMPT_DELAY = 0.2  # 200ms between attempts

api = LifetimeAPI()

# --- Step 1: Login ---
log.info("Logging in as %s ...", EMAIL)
session = api.login(EMAIL, PASSWORD)
if not session.authenticated:
    log.error("LOGIN FAILED. Check credentials.")
    sys.exit(1)
log.info("Logged in! Member: %s (ID %d)", session.member_name, session.member_id)
member_ids = session.get_all_member_ids()
log.info("Members: %s", [(m.display_name(), m.member_id) for m in session.members])

# --- Step 2: Find the target event ---
# Search next 7 days
today = datetime.now()
start_str = today.strftime("%m/%d/%Y")
end_str = (today + timedelta(days=7)).strftime("%m/%d/%Y")

log.info("Fetching events at %s from %s to %s ...", CLUB, start_str, end_str)
events = api.get_events(session, CLUB, start_str, end_str)
log.info("Found %d total events.", len(events))

# Filter for the target
candidates = []
for ev in events:
    try:
        dt = datetime.fromisoformat(ev.start)
    except Exception:
        continue
    if dt.strftime("%A") != TARGET_DAY:
        continue
    if not (TARGET_HOUR_START <= dt.hour <= TARGET_HOUR_END):
        continue
    title_lower = ev.title.lower()
    if not any(kw in title_lower for kw in TARGET_KEYWORDS):
        continue
    candidates.append(ev)
    log.info("  MATCH: %s | %s | %s", ev.title, ev.display_time(), ev.club or ev.location)

if not candidates:
    log.error("No matching Monday 8-9:30 drill at Penn found!")
    log.info("All events:")
    for ev in events:
        try:
            dt = datetime.fromisoformat(ev.start)
            log.info("  %s %s | %s | %s", dt.strftime("%A"), dt.strftime("%I:%M %p"), ev.title, ev.club)
        except Exception:
            log.info("  ?? | %s | %s", ev.title, ev.club)
    sys.exit(1)

target = candidates[0]
log.info("TARGET: %s | %s", target.title, target.display_time())

# --- Step 3: Check registration status ---
reg = api.get_event_registration(session, target.event_id)
if reg:
    log.info("Spots: %s (remaining: %s) | Waitlist: %s | CTA: '%s' disabled=%s",
             reg.has_spots, reg.remaining_spots, reg.has_waitlist,
             reg.register_cta_text, reg.register_disabled)
    if reg.registration_opens_at:
        log.info("Opens at: %s", reg.registration_opens_at)

# Discover members
api.discover_members(session, target.event_id)
member_ids = session.get_all_member_ids()
log.info("Registering members: %s", member_ids)

# --- Step 4: SNIPE! Rapid registration attempts ---
log.info("=" * 60)
log.info("SNIPE MODE — firing %d rapid attempts (%.0fms apart)", RAPID_ATTEMPTS, ATTEMPT_DELAY * 1000)
log.info("=" * 60)

for attempt in range(1, RAPID_ATTEMPTS + 1):
    log.info("Attempt %d/%d ...", attempt, RAPID_ATTEMPTS)

    # Re-auth if needed
    if session.is_expired():
        log.info("Re-authenticating ...")
        session = api.ensure_authenticated(EMAIL, PASSWORD)

    result = api.register(session, target.event_id, member_ids)

    if result.success:
        log.info("SUCCESS! Registered for %s! reg_id=%s", target.title, result.reg_id)
        # Verify
        verification = api.verify_registration(session, target.event_id, member_ids)
        log.info("Verification: %s", verification)
        if verification == "waitlisted":
            log.warning("Verification says WAITLISTED — but check the app to confirm.")
        else:
            log.info("CONFIRMED REGISTERED!")
        sys.exit(0)
    elif result.waitlisted:
        log.warning("WAITLISTED: %s", result.message)
        sys.exit(0)
    else:
        log.info("  Attempt %d result: %s", attempt, result.message)
        if "registration will be open" in result.message.lower() or "too soon" in result.message.lower():
            log.info("  Not open yet — retrying ...")
        time.sleep(ATTEMPT_DELAY)

log.warning("Exhausted %d attempts. Registration may not be open yet.", RAPID_ATTEMPTS)
log.info("Waiting 5 seconds and trying one more burst...")
time.sleep(5)

# Second burst
session = api.ensure_authenticated(EMAIL, PASSWORD)
for attempt in range(1, 11):
    log.info("Burst 2 — Attempt %d/10 ...", attempt)
    result = api.register(session, target.event_id, member_ids)
    if result.success:
        log.info("SUCCESS! %s", result.message)
        sys.exit(0)
    elif result.waitlisted:
        log.warning("WAITLISTED: %s", result.message)
        sys.exit(0)
    else:
        log.info("  %s", result.message)
    time.sleep(ATTEMPT_DELAY)

log.error("All attempts exhausted. Check manually.")
