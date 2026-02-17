"""
CSS / XPath selectors for the Lifetime Fitness member portal.

=======================================================================
HOW TO UPDATE SELECTORS
=======================================================================
Lifetime Fitness may redesign their site at any time.  When they do,
the automation will break.  To fix it:

1. Open the Lifetime website in Chrome / Edge.
2. Right-click the element that changed → "Inspect".
3. Find a stable CSS selector or XPath for the new element.
   - Prefer selectors based on IDs, data-* attributes, or aria-labels
     because they change less often than class names.
   - Avoid deeply nested positional selectors (div > div > div:nth-child(3))
     because those are fragile.
4. Update the corresponding constant below.
5. Restart the app.

Each selector group below is labeled with the page / action it belongs to.
=======================================================================
"""

# ---------------------------------------------------------------------------
# LOGIN PAGE  —  https://my.lifetime.life/login
# ---------------------------------------------------------------------------
# Email / username input field
LOGIN_EMAIL_INPUT = 'input[name="username"], input[type="email"], #username'

# Password input field
LOGIN_PASSWORD_INPUT = 'input[name="password"], input[type="password"], #password'

# Submit / sign-in button
LOGIN_SUBMIT_BUTTON = 'button[type="submit"], button:has-text("Sign In"), button:has-text("Log In")'

# Element that proves we're logged in (visible after successful login).
# Typically a profile icon, account name, or dashboard element.
LOGIN_SUCCESS_INDICATOR = '[data-cy="user-menu"], .user-profile, .account-nav, a[href*="account"]'

# Error banner shown on failed login
LOGIN_ERROR_INDICATOR = '.error-message, .alert-danger, [role="alert"]:has-text("incorrect")'

# ---------------------------------------------------------------------------
# SCHEDULE / CLASSES PAGE
# ---------------------------------------------------------------------------
# Container that holds the list of classes/sessions
SCHEDULE_CONTAINER = '.classes-list, .schedule-container, .class-schedule, [data-cy="class-list"]'

# Individual session card / row inside the container
SESSION_CARD = '.class-card, .class-item, .schedule-item, [data-cy="class-card"]'

# Within a session card — sub-selectors (applied relative to the card element)
SESSION_NAME = '.class-name, .class-title, h3, h4, [data-cy="class-name"]'
SESSION_DATE = '.class-date, .date, time, [data-cy="class-date"]'
SESSION_TIME = '.class-time, .time, [data-cy="class-time"]'
SESSION_INSTRUCTOR = '.instructor, .trainer, [data-cy="instructor"]'
SESSION_AVAILABILITY = '.availability, .spots, .spots-left, [data-cy="availability"]'

# ---------------------------------------------------------------------------
# REGISTRATION BUTTONS
# ---------------------------------------------------------------------------
# "Register" / "Book" / "Reserve" button on a session card
REGISTER_BUTTON = (
    'button:has-text("Register"), '
    'button:has-text("Book"), '
    'button:has-text("Reserve"), '
    'a:has-text("Register"), '
    'a:has-text("Book"), '
    '[data-cy="register-button"]'
)

# Confirmation button on the registration overlay / modal
CONFIRM_REGISTER_BUTTON = (
    'button:has-text("Confirm"), '
    'button:has-text("Complete"), '
    'button:has-text("Yes"), '
    '[data-cy="confirm-button"]'
)

# Element visible after a successful registration
REGISTRATION_SUCCESS = (
    '.success-message, '
    '.confirmation, '
    ':has-text("You\'re registered"), '
    ':has-text("Successfully registered"), '
    '[data-cy="registration-success"]'
)

# "Waitlist" button (used as fallback when session is full)
WAITLIST_BUTTON = (
    'button:has-text("Waitlist"), '
    'button:has-text("Join Waitlist"), '
    'a:has-text("Waitlist"), '
    '[data-cy="waitlist-button"]'
)

# ---------------------------------------------------------------------------
# NAVIGATION HELPERS
# ---------------------------------------------------------------------------
# Date picker / calendar navigation on the schedule page
DATE_NEXT_BUTTON = 'button:has-text("Next"), .next-day, [aria-label="Next"]'
DATE_PREV_BUTTON = 'button:has-text("Prev"), .prev-day, [aria-label="Previous"]'
DATE_PICKER_INPUT = 'input[type="date"], .date-picker, [data-cy="date-picker"]'

# Filter / search bar on the schedule page (to narrow to pickleball)
CLASS_FILTER_INPUT = 'input[placeholder*="Search"], input[placeholder*="Filter"], .class-filter'
CLASS_FILTER_OPTION = '.filter-option, .dropdown-item'

# ---------------------------------------------------------------------------
# COOKIE / CONSENT BANNERS  (dismiss automatically)
# ---------------------------------------------------------------------------
COOKIE_ACCEPT_BUTTON = (
    'button:has-text("Accept"), '
    'button:has-text("Got it"), '
    'button:has-text("OK"), '
    '#onetrust-accept-btn-handler'
)
