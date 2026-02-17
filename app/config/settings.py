"""
Application-wide settings, paths, and defaults.

Edit the values here to match your Lifetime Fitness club and preferences.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

ACCOUNTS_FILE = DATA_DIR / "accounts.enc"    # Encrypted account store
KEY_FILE = DATA_DIR / ".key"                 # Fernet encryption key
LOG_FILE = DATA_DIR / "activity.log"
SESSION_DIR = DATA_DIR / "sessions"          # Playwright persistent contexts
SESSION_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Lifetime Fitness base URLs
# ---------------------------------------------------------------------------
BASE_URL = "https://my.lifetime.life"
LOGIN_URL = f"{BASE_URL}/login"

# Club schedule URL template — replace {club_slug} at runtime.
# Example slug: "johns-creek"  →  .../clubs/johns-creek/classes.html
SCHEDULE_URL_TEMPLATE = f"{BASE_URL}/clubs/{{club_slug}}/classes.html"

# ---------------------------------------------------------------------------
# Common club slugs  (add yours here so the dropdown is pre-populated)
# ---------------------------------------------------------------------------
CLUB_SLUGS: dict[str, str] = {
    "Alpharetta": "alpharetta",
    "Johns Creek": "johns-creek",
    "Buckhead": "buckhead",
    "Centennial": "centennial",
    "Duluth": "duluth",
    "Peachtree Corners": "peachtree-corners",
    "Sugarloaf": "sugarloaf",
    "Woodstock": "woodstock",
    "Brookhaven": "brookhaven",
    "Flower Mound": "flower-mound",
    "Frisco": "frisco",
    "Plano": "plano",
    "Scottsdale": "scottsdale",
    "Gilbert": "gilbert",
    "Tempe": "tempe",
    "Edina": "edina",
    "Plymouth": "plymouth",
    "St. Louis Park": "st-louis-park",
    "Bloomington South": "bloomington-south",
    "Fridley": "fridley",
    "Custom (enter slug below)": "",
}

# ---------------------------------------------------------------------------
# Session types to look for when scraping (case-insensitive match)
# ---------------------------------------------------------------------------
SESSION_TYPES: list[str] = [
    "Pickleball Open Play",
    "Pickleball Court Reservation",
    "Pickleball Clinic",
    "Pickleball League",
    "Pickleball Skills & Drills",
    "Pickleball",
]

# ---------------------------------------------------------------------------
# Polling / automation defaults
# ---------------------------------------------------------------------------
DEFAULT_POLL_INTERVAL_SEC = 30        # seconds between availability checks
MIN_POLL_INTERVAL_SEC = 10
MAX_POLL_INTERVAL_SEC = 300
MAX_RETRY_ATTEMPTS = 3                # retries per registration attempt
RETRY_DELAY_SEC = 2                   # base delay between retries (exponential)

# ---------------------------------------------------------------------------
# Playwright settings
# ---------------------------------------------------------------------------
HEADLESS = True                       # Set False to watch the browser work
BROWSER_TIMEOUT_MS = 30_000           # 30 s page-load / action timeout
SLOW_MO_MS = 0                        # ms delay between Playwright actions (debug aid)
