"""
Application-wide settings, paths, and defaults.
"""

import re
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

ACCOUNTS_FILE = DATA_DIR / "accounts.enc"    # Encrypted account store
KEY_FILE = DATA_DIR / ".key"                 # Fernet encryption key
LOG_FILE = DATA_DIR / "activity.log"

# ---------------------------------------------------------------------------
# Lifetime Fitness API endpoints  (reverse-engineered from their website JS)
# ---------------------------------------------------------------------------
API_BASE = "https://api.lifetimefitness.com"

# Authentication
AUTH_URL = f"{API_BASE}/auth/v2/login"
PROFILE_URL = f"{API_BASE}/user-profile/profile"

# Schedule / Events
EVENTS_URL = f"{API_BASE}/ux/web-schedules/v2/events"
EVENT_DETAIL_URL = f"{API_BASE}/ux/web-schedules/v2/events/{{event_id}}"
EVENT_REGISTRATION_URL = f"{API_BASE}/ux/web-schedules/v2/events/{{event_id}}/registration"

# My Reservations (existing bookings)
RESERVATIONS_URL = f"{API_BASE}/ux/web-schedules/v3/reservations"

# Registration actions
REG_CREATE_URL = f"{API_BASE}/sys/registrations/V3/ux/event"
REG_GET_URL = f"{API_BASE}/sys/registrations/V3/ux/event/{{reg_id}}"
REG_COMPLETE_URL = f"{API_BASE}/sys/registrations/V3/ux/event/{{reg_id}}/complete"
REG_EXTEND_URL = f"{API_BASE}/sys/registrations/V3/ux/event/{{reg_id}}/extend"
REG_CANCEL_URL = f"{API_BASE}/sys/registrations/V3/ux/event/{{reg_id}}/cancel"
REG_ATTENDEE_URL = f"{API_BASE}/sys/registrations/V3/ux/event/{{reg_id}}/attendees/{{attendee_id}}"

# Page for dynamic API key scraping
LT_HOMEPAGE = "https://my.lifetime.life/"

# ---------------------------------------------------------------------------
# API keys — fetched dynamically from the Lifetime website.
# The website embeds these in a JS config object on every page.
# We cache them so we don't re-fetch on every call.
# ---------------------------------------------------------------------------
_cached_apim_key: str | None = None
_cached_myaccount_key: str | None = None


def fetch_api_keys(force: bool = False) -> tuple[str, str]:
    """
    Scrape the two API keys from the Lifetime homepage JS config.

    Returns (apim_key, myaccount_key).
    """
    global _cached_apim_key, _cached_myaccount_key
    if not force and _cached_apim_key and _cached_myaccount_key:
        return _cached_apim_key, _cached_myaccount_key

    resp = requests.get(LT_HOMEPAGE, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    html = resp.text

    m1 = re.search(r'"apimKey"\s*:\s*"([a-f0-9]{32})"', html)
    m2 = re.search(r'"ltMyAccountApiKey"\s*:\s*"([A-Za-z0-9]{32})"', html)
    if not m1 or not m2:
        raise RuntimeError("Failed to extract API keys from Lifetime homepage. Site may have changed.")

    _cached_apim_key = m1.group(1)
    _cached_myaccount_key = m2.group(1)
    return _cached_apim_key, _cached_myaccount_key


# ---------------------------------------------------------------------------
# Club locations — the API uses the display name (e.g. "PENN 1"), not a slug.
# Add your clubs here for the dropdown.
# ---------------------------------------------------------------------------
CLUBS: dict[str, int] = {
    # Format: "Display Name": clubId
    # -- New York --
    "PENN 1": 351,
    "Sky (Manhattan)": 250,
    "23rd Street": 364,
    "Atlantic Avenue": 327,
    "Battery Park": 331,
    "Brooklyn Tower": 380,
    "Bryant Park": 367,
    "Dumbo": 363,
    "Fifth Avenue": 389,
    "Midtown": 320,
    "NoHo": 345,
    "One Wall Street": 362,
    "Westchester": 241,
    # -- New Jersey --
    "Bergen County": 350,
    "Montvale": 373,
    "Berkeley Heights": 316,
    "Bridgewater": 329,
    "Florham Park": 310,
    "Garwood": 317,
    "Princeton": 352,
    # -- Atlanta --
    "Alpharetta": 114,
    "Buckhead": 119,
    "Johns Creek": 184,
    "Peachtree Corners": 120,
    "Sugarloaf": 156,
    "Woodstock": 137,
    # -- Texas (Dallas/Ft Worth) --
    "Flower Mound": 141,
    "Frisco": 158,
    "Plano": 104,
    "Allen": 191,
    "Las Colinas": 103,
    "North Dallas": 185,
    "Southlake": 189,
    # -- Texas (Houston) --
    "Houston Galleria": 163,
    "Houston Memorial": 197,
    "Cinco Ranch": 171,
    "Sugar Land": 172,
    "The Woodlands": 170,
    # -- Arizona --
    "Scottsdale": 143,
    "Gilbert": 147,
    "Paradise Valley": 165,
    "Tempe": 176,
    "North Scottsdale": 203,
    # -- Minnesota --
    "Edina": 8,
    "Plymouth": 7,
    "St. Louis Park": 3,
    "Bloomington South": 4,
    "Eagan": 5,
    "Lakeville": 10,
    "Maple Grove": 14,
    "Woodbury": 11,
    "Eden Prairie": 6,
    "Chanhassen": 9,
    "Fridley": 13,
    "Rochester": 18,
    "Target Center": 371,
    # -- Colorado --
    "Centennial": 136,
    "Flatirons (Broomfield)": 166,
    "Highlands Ranch": 134,
    "Lone Tree": 135,
    "Parker": 177,
    # -- Chicago --
    "Lincoln Park": 298,
    "Schaumburg": 159,
    "Warrenville": 160,
    "Algonquin": 186,
    # -- Michigan --
    "Troy": 199,
    "Rochester Hills": 200,
    "Novi": 202,
    "Shelby Township": 198,
    # -- Virginia --
    "Centreville": 221,
    "Gainesville": 266,
    "Reston": 374,
    # -- Utah --
    "Cottonwood Heights": 153,
    "Draper": 152,
    "Jordan Landing": 154,
}

# ---------------------------------------------------------------------------
# Polling / automation defaults
# ---------------------------------------------------------------------------
DEFAULT_POLL_INTERVAL_SEC = 5         # seconds between checks during snipe window
MIN_POLL_INTERVAL_SEC = 1
MAX_POLL_INTERVAL_SEC = 300
MAX_RETRY_ATTEMPTS = 5                # retries per registration attempt
RETRY_DELAY_SEC = 0.5                 # very fast retries for sniping

# ---------------------------------------------------------------------------
# Snipe timing
# ---------------------------------------------------------------------------
# How many seconds BEFORE a registration window opens to start rapid polling.
SNIPE_LEAD_TIME_SEC = 30
