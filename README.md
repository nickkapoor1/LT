# Pickleball Auto-Register for Lifetime Fitness

A browser-based tool that automatically registers you (and multiple accounts) for pickleball sessions on the Lifetime Fitness member portal the moment they become available.

Built with **Streamlit** (browser UI) and **Playwright** (browser automation).

---

## Quick Start

### 1. Install Python

You need Python 3.10 or newer. Download from [python.org](https://www.python.org/downloads/) if you don't have it.

To check your version:

```bash
python --version
```

### 2. Install Dependencies

Open a terminal in this project folder and run:

```bash
pip install -r requirements.txt
```

### 3. Install the Playwright Browser

Playwright needs to download a Chromium browser the first time:

```bash
playwright install chromium
```

### 4. Launch the App

```bash
streamlit run main.py
```

This opens the dashboard in your default web browser at `http://localhost:8501`.

---

## How to Use

### Add Accounts

1. Click **Accounts** in the sidebar.
2. Enter your Lifetime Fitness email, password, club, and session types.
3. Click **Add Account**. Credentials are encrypted and stored locally.
4. Repeat for additional accounts.

### View the Schedule

1. Click **Schedule** in the sidebar.
2. Pick an account and a date, then click **Refresh Schedule**.
3. The app logs into Lifetime and scrapes available pickleball sessions.

### Watch Sessions

1. On the Schedule page, find a session you want.
2. Select which accounts should watch it.
3. Click **Add to watchlist**.

### Start the Engine

1. Click **Start Engine** in the sidebar.
2. The automation polls the Lifetime website at your configured interval.
3. When a watched session becomes available, it automatically registers.
4. Watch progress in real time on the **Dashboard**.

### Stop the Engine

Click **Stop Engine** in the sidebar at any time.

---

## Configuration

### Polling Interval

Go to **Settings** to adjust how often the engine checks for availability (default: 30 seconds).

### Headless Mode

By default the browser runs invisibly. Turn off headless mode in **Settings** to watch the browser work (useful for debugging).

### Club Locations

Common Lifetime clubs are pre-loaded in the dropdown. If yours isn't listed, select "Custom" and enter the URL slug for your club.

To find your club slug, go to your club's class schedule on `my.lifetime.life` and look at the URL:

```
https://my.lifetime.life/clubs/johns-creek/classes.html
                              ^^^^^^^^^^^^
                              this is the slug
```

---

## Updating Selectors

If Lifetime redesigns their website, the automation may stop working. To fix it:

1. Open `app/config/selectors.py`.
2. Each selector is labeled with the page and element it targets.
3. Open the Lifetime website in Chrome, right-click the element, choose **Inspect**.
4. Copy a stable CSS selector (prefer `id`, `data-*`, or `aria-label` attributes).
5. Update the constant in `selectors.py` and restart the app.

---

## Project Structure

```
main.py                  ← Streamlit UI (run this)
app/
  config/
    settings.py          ← URLs, paths, defaults, club slugs
    selectors.py         ← CSS selectors for Lifetime website
  backend/
    crypto.py            ← Encrypted credential storage
    login.py             ← Playwright login manager
    scraper.py           ← Schedule scraping
    registrar.py         ← Registration engine with retries
    monitor.py           ← Session watch list tracking
    scheduler.py         ← Background polling loop
data/                    ← Created at runtime (credentials, cookies, logs)
requirements.txt
README.md
```

---

## Security Notes

- Passwords are encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256).
- The encryption key is stored in `data/.key` — do not share this file.
- Session cookies are stored in `data/sessions/` — do not share this folder.
- **Never commit the `data/` folder to git.**

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `playwright install chromium` fails | Make sure you have a working internet connection and sufficient disk space. |
| Login fails | Double-check your email and password. Try logging in manually on `my.lifetime.life` first. |
| No sessions found | The selectors may be outdated. See "Updating Selectors" above. |
| Engine stops unexpectedly | Check `data/activity.log` for errors. Common cause: session timeout (the engine will auto-retry). |
| GUI is slow / unresponsive | Increase the polling interval in Settings. Each poll cycle launches a browser, so shorter intervals use more CPU. |
