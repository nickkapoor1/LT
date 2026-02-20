"""
Auto-watch rules — persistent presets that automatically queue matching events.

Each rule describes a pattern (clubs, session type, level, time window, keywords).
The scheduler periodically scans for new events matching active rules and
auto-watches them for snipe registration.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from app.config.settings import DATA_DIR

log = logging.getLogger(__name__)

RULES_FILE = DATA_DIR / "rules.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class WatchRule:
    """A persistent auto-watch rule (preset)."""
    name: str                                 # Human-readable label
    account_email: str                        # Which account to register with
    member_ids: list[int]                     # Which members to register
    clubs: list[str]                          # Club display names to search
    session_types: list[str] = field(default_factory=list)  # e.g. ["Drill / Clinic", "Open Play"]
    min_level: float = 0.0                    # Minimum skill level (e.g. 5.0)
    time_earliest: str = "00:00"              # Earliest event start (HH:MM, 24h)
    time_latest: str = "23:59"                # Latest event start (HH:MM, 24h)
    title_contains: list[str] = field(default_factory=list)  # Title must contain ANY of these
    days_of_week: list[int] = field(default_factory=list)    # 0=Mon..6=Sun; empty=all
    enabled: bool = True

    def matches(self, title: str, club: str, start_iso: str) -> bool:
        """Return True if an event matches this rule."""
        t_lower = title.lower()

        # Club check
        if self.clubs and club not in self.clubs:
            return False

        # Session type check
        if self.session_types:
            etype = _session_type(title)
            if etype not in self.session_types:
                return False

        # Level check
        if self.min_level > 0:
            level = _extract_max_level(title)
            if level < self.min_level:
                return False

        # Title keyword check (any keyword must appear)
        if self.title_contains:
            if not any(kw.lower() in t_lower for kw in self.title_contains):
                return False

        # Time-of-day check
        try:
            dt = datetime.fromisoformat(start_iso)
            event_time = dt.strftime("%H:%M")
            if event_time < self.time_earliest or event_time > self.time_latest:
                return False
            # Day-of-week check
            if self.days_of_week and dt.weekday() not in self.days_of_week:
                return False
        except Exception:
            pass  # If we can't parse time, don't filter on it

        return True


# ---------------------------------------------------------------------------
# Helpers (shared with main.py)
# ---------------------------------------------------------------------------

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


def _extract_max_level(title: str) -> float:
    """Extract the highest skill level mentioned in the title (e.g. '3.5-4.0+' → 4.0)."""
    # Range like "3.5-4.0+"
    m = re.search(r'(\d\.\d)\s*[-–]\s*(\d\.\d)\+?', title)
    if m:
        return max(float(m.group(1)), float(m.group(2)))
    # Single like "5.0+"
    m = re.search(r'(\d\.\d)\+?', title)
    if m:
        return float(m.group(1))
    return 0.0


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_rules() -> list[WatchRule]:
    """Load rules from disk."""
    if not RULES_FILE.exists():
        return []
    try:
        data = json.loads(RULES_FILE.read_text())
        rules = []
        for d in data:
            try:
                rules.append(WatchRule(**d))
            except Exception as exc:
                log.warning("Skipping malformed rule: %s", exc)
        return rules
    except Exception as exc:
        log.error("Failed to load rules: %s", exc)
        return []


def save_rules(rules: list[WatchRule]) -> None:
    """Persist rules to disk."""
    data = [asdict(r) for r in rules]
    RULES_FILE.write_text(json.dumps(data, indent=2))
    log.info("Saved %d rule(s) to %s", len(rules), RULES_FILE)


def add_rule(rule: WatchRule) -> list[WatchRule]:
    """Add a rule and persist. Returns updated list."""
    rules = load_rules()
    rules.append(rule)
    save_rules(rules)
    return rules


def remove_rule(index: int) -> list[WatchRule]:
    """Remove rule by index and persist. Returns updated list."""
    rules = load_rules()
    if 0 <= index < len(rules):
        rules.pop(index)
        save_rules(rules)
    return rules
